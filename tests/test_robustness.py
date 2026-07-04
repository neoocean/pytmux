"""견고성 공백 테스트(#5.8): 손상 상태파일 복원, 미지 cmd 액션 무해성.

프로토콜 프레이밍 견고성(거대 length·비-JSON·잘린 프레임·미지 타입)은
test_protocol.py, degraded 히스테리시스는 test_client.py 가 다룬다."""
import json
import os

import harness  # noqa: F401  (경로 설정)
from harness import server_only, teardown
from pytmuxlib.model import ClientConn


async def test_restore_resume_corrupt_returns_false():
    """re-exec 복원이 손상/잘림/버전불일치 상태파일에 예외 없이 False 를 돌려준다
    (브릭 방지 — 복원 실패 시 새 부트로 자연 폴백)."""
    srv, task, sock = await server_only()
    try:
        p = srv.resume_state_path

        def _restore(content: str | bytes):
            mode = "wb" if isinstance(content, bytes) else "w"
            with open(p, mode) as f:
                f.write(content)
            return srv.restore_resume_state(p)

        assert _restore("{ this is not json ") is False       # 깨진 JSON
        assert _restore(b"\xff\xfe\x00garbage") is False       # 비-UTF8 쓰레기
        assert _restore('{"version": 1, "sessions": [') is False  # 잘린 JSON
        assert _restore(json.dumps({"version": 2,
                                    "sessions": []})) is False  # 버전 불일치
        assert _restore(json.dumps({"sessions": []})) is False  # version 키 없음
        # 없는 파일도 False(예외 아님)
        os.unlink(p)
        assert srv.restore_resume_state(p) is False
    finally:
        try:
            os.unlink(srv.resume_state_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_restore_resume_typeerror_node_skipped():
    """M4: 상태 노드의 타입 오류(예: cols 가 문자열)가 복원 전체를 크래시→세션 전손
    시키지 않고, 해당 탭만 스킵하고 예외 없이 False(유효 탭 없음)를 돌려준다."""
    srv, task, sock = await server_only()
    try:
        bad = {"version": 1, "sessions": [{
            "name": "s", "active_index": 0, "tabs": [{
                "index": 0, "name": "t", "window": {"root": {
                    "pane": {"cols": "x", "rows": 24,    # cols 문자열 → TypeError
                             "child_pid": 5, "master_fd": 3}}}}]}]}
        with open(srv.resume_state_path, "w") as f:
            json.dump(bad, f)
        assert srv.restore_resume_state(srv.resume_state_path) is False
    finally:
        try:
            os.unlink(srv.resume_state_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_close_resume_subtree_releases_panes():
    """M4: 부분 복원 정리 — 서브트리의 모든 패널 pty 가 stop_reader+close 된다(형제
    노드 빌드 실패 시 이미 채택된 master fd/리더 누수 방지)."""
    from pytmuxlib.model import Pane, Split
    srv, task, sock = await server_only()
    try:
        class _FakePty:
            def __init__(self):
                self.closed = self.stopped = False

            def stop_reader(self):
                self.stopped = True

            def close(self):
                self.closed = True

        p1 = Pane(1, -1, 80, 24)
        p2 = Pane(2, -1, 80, 24)
        p1.pty, p2.pty = _FakePty(), _FakePty()
        srv._close_resume_subtree(Split("lr", p1, Split("tb", p2, p1, 0.5), 0.5))
        assert p1.pty.closed and p1.pty.stopped, "패널1 정리"
        assert p2.pty.closed and p2.pty.stopped, "패널2 정리"
    finally:
        await teardown(srv, task, sock)


class _FakeWriter:
    def write(self, *_a):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


async def test_send_to_serializes_drain_on_write_lock():
    """_send_to 는 c.write_lock 을 잡아, 같은 writer 에 두 코루틴의 drain 이 **겹치지 않게**
    직렬화한다(§5 [H] — 동시 drain 은 CPython 에서 AssertionError/-O 영구 hang). lock 이
    없으면 두 drain 이 동시에 진입한다."""
    import asyncio
    srv, task, sock = await server_only()
    try:
        order = []
        ev = asyncio.Event()

        class _BlockWriter:
            def write(self, *_a):
                pass

            async def drain(self):
                order.append("enter")
                if len(order) == 1:      # 첫 drain 은 멈춰 겹칠 기회를 준다
                    await ev.wait()
                order.append("exit")

            def close(self):
                pass

        c = ClientConn(_BlockWriter())
        t1 = asyncio.create_task(srv._send_to(c, {"t": "a"}))
        await asyncio.sleep(0)           # t1 이 lock 잡고 첫 drain 진입
        t2 = asyncio.create_task(srv._send_to(c, {"t": "b"}))
        await asyncio.sleep(0)
        # 직렬화면 t2 는 lock 대기 → 아직 drain 진입 못 함(enter 1개)
        assert order == ["enter"], order
        ev.set()
        await asyncio.gather(t1, t2)
        assert order == ["enter", "exit", "enter", "exit"], order
    finally:
        await teardown(srv, task, sock)


async def test_flush_acquire_timeout_drops_wedged_client():
    """flush 가 write_lock 을 오래 쥔 먹통 클라(무제한 drain 중인 _send_full 등)에서
    acquire 자체를 타임아웃해 그 클라를 떨군다 — flush 루프 전체가 프리즈(최대
    CLIENT_IDLE_TIMEOUT)하지 않게(§5 [M])."""
    import asyncio
    from pytmuxlib import serverio
    srv, task, sock = await server_only()
    saved = serverio._CLIENT_WRITE_TIMEOUT
    serverio._CLIENT_WRITE_TIMEOUT = 0.05
    try:
        c = ClientConn(_FakeWriter())
        srv.clients.append(c)
        await c.write_lock.acquire()     # 다른 코루틴이 lock 을 쥔 상태 모사
        try:
            await srv._flush_to_client(c, [b"\x00\x00\x00\x02{}"])
        finally:
            c.write_lock.release()
        assert c not in srv.clients, "acquire 타임아웃 시 먹통 클라를 떨궈야 함"
    finally:
        serverio._CLIENT_WRITE_TIMEOUT = saved
        await teardown(srv, task, sock)


async def test_flush_loop_restarts_after_unexpected_exception():
    """_on_flush_done 는 flush(렌더) 루프 태스크가 예외로 죽으면 재기동한다 — 무감시
    태스크가 조용히 멈춰 화면이 영구 정지하는 최악의 안전망(§5 [H]). 취소/정상종료는
    재기동하지 않는다."""
    import asyncio
    srv, task, sock = await server_only()
    try:
        srv.running = True
        srv.loop = asyncio.get_running_loop()
        calls = []

        async def _fake_flush():        # 재기동 감지용(즉시 종료)
            calls.append(1)

        srv._flush_loop = _fake_flush

        # ① 예외로 죽은 태스크 → 재기동(_fake_flush 1회)
        async def _boom():
            raise RuntimeError("flush boom")
        dead = asyncio.ensure_future(_boom())
        try:
            await dead
        except RuntimeError:
            pass
        srv._on_flush_done(dead)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(calls) == 1, "예외 사망 시 재기동"

        # ② 취소된 태스크 → 재기동 안 함
        cancelled = asyncio.ensure_future(asyncio.sleep(3600))
        await asyncio.sleep(0)
        cancelled.cancel()
        try:
            await cancelled
        except asyncio.CancelledError:
            pass
        srv._on_flush_done(cancelled)
        await asyncio.sleep(0)
        assert len(calls) == 1, "취소는 재기동 안 함"

        # ③ running=False(셧다운) → 재기동 안 함
        srv.running = False
        another = asyncio.ensure_future(_boom())
        try:
            await another
        except RuntimeError:
            pass
        srv._on_flush_done(another)
        await asyncio.sleep(0)
        assert len(calls) == 1, "셧다운 중엔 재기동 안 함"
    finally:
        await teardown(srv, task, sock)


async def test_unknown_cmd_action_is_noop():
    """미지 cmd 액션은 _handle_cmd 의 else 로 떨어져 무해 no-op(세션 보존, 예외 없음).
    한 클라의 엉뚱한 메시지가 서버/세션을 깨지 않는다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        client = ClientConn(_FakeWriter())
        client.session = sess
        client.cols, client.rows = 80, 24
        n_tabs_before = len(sess.tabs)
        # 미지 액션 — 예외 없이 조용히 반환
        await srv._handle_cmd(client, {"t": "cmd", "action": "totally-bogus"})
        await srv._handle_cmd(client, {"t": "cmd"})            # action 키 없음
        assert len(sess.tabs) == n_tabs_before                # 세션 불변
        # 정상 액션은 여전히 동작(서버가 멀쩡)
        await srv._handle_cmd(client, {"t": "cmd", "action": "new_window"})
        assert len(sess.tabs) == n_tabs_before + 1
    finally:
        await teardown(srv, task, sock)
