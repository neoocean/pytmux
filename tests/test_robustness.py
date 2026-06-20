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
