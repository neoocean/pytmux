"""pytmux-34 서버 검수(2026-10-15)가 찾은 견고성 결함의 회귀 가드.

되돌리면 실패해야 하는 오라클:
  · #5 `_send_full` 의 시한을 빼면 → test_a_stuck_client_cannot_hold_a_full_resync_forever
  · #7 pty-host 프레임의 RecursionError·비-dict 가드를 빼면 → test_ptyhost_frames_reject_*
  · #8 handle_client 의 비-dict 가드를 빼면 → test_a_non_dict_frame_does_not_drop_the_client
  · #4 pending flush 루프에 await 을 되살리면 → test_ptyhost_pending_flush_does_not_yield
  · #6 원격 링크가 stderr 를 비우지 않으면 → test_remote_link_keeps_draining_stderr
"""
import asyncio
import ast
import inspect
import json
import struct
import textwrap

import harness
from harness import server_only, teardown

from pytmuxlib import ptyhost, ptyhostproto, serverio, serverremote
from pytmuxlib.model import ClientConn


class _NeverDrains:
    def __init__(self):
        self.closed = False

    def write(self, data):
        pass

    async def drain(self):
        await asyncio.Event().wait()

    def close(self):
        self.closed = True

    def is_closing(self):
        return self.closed

    def get_extra_info(self, *_a, **_k):
        return None


async def test_a_stuck_client_cannot_hold_a_full_resync_forever():
    srv, task, sock = await server_only()
    try:
        c = ClientConn(_NeverDrains())
        srv.clients.append(c)

        async def stuck(_client):
            await asyncio.Event().wait()
        with harness.patched(serverio, _CLIENT_WRITE_TIMEOUT=0.05):
            srv._send_full_inner = stuck
            await asyncio.wait_for(srv._send_full(c), 2.0)
        assert c not in srv.clients, "full 재동기에 묶인 클라를 안 떼어 냈다"
    finally:
        await teardown(srv, task, sock,
                       allow_errors=("slow client dropped",))


def _frame(payload: bytes) -> bytes:
    body = ptyhostproto.TYPE_JSON + payload
    return struct.pack(">I", len(body)) + body


async def _read(raw: bytes):
    r = asyncio.StreamReader()
    r.feed_data(raw)
    r.feed_eof()
    return await ptyhostproto.read_frame(r)


async def test_ptyhost_frames_reject_deep_nesting():
    assert await _read(_frame(b"[" * 20000)) is None


async def test_ptyhost_frames_reject_non_dict_json():
    assert await _read(_frame(b"[]")) is None
    assert await _read(_frame(b"7")) is None
    ok = await _read(_frame(json.dumps({"op": "probe"}).encode()))
    assert ok == ("json", {"op": "probe"}), ok


def _func_src(fn):
    return textwrap.dedent(inspect.getsource(fn))


def test_a_non_dict_frame_does_not_drop_the_client():
    """호출부 — 메시지 루프가 dispatch 전에 비-dict 를 거른다(except 안의 msg.get 이 다시
    터져 연결이 끊기던 것)."""
    src = _func_src(serverio.ServerIOMixin.handle_client)
    i = src.index("msg = await read_msg(reader)")
    head = src[i:i + 600]
    assert "isinstance(msg, dict)" in head, "비-dict 프레임 가드가 메시지 루프에 없다"


def test_ptyhost_pending_flush_does_not_yield():
    """재연결의 pending flush 루프 안에 await 이 없어야 한다 — 그 틈에 다른 패널의 새
    출력이 옛 pending 보다 먼저 나갔다(#4)."""
    src = _func_src(ptyhost.PtyHost._handle_conn)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.iter, ast.Call)
                and getattr(node.iter.func, "attr", "") == "values"
                and "pending" in ast.unparse(node)):
            awaits = [n for n in ast.walk(node) if isinstance(n, ast.Await)]
            assert not awaits, "pending flush 루프 안에서 await 한다: %s" % ast.unparse(awaits[0])
            return
    raise AssertionError("pending flush 루프를 못 찾았다(구조가 바뀌었으면 이 시험을 고칠 것)")


async def test_a_split_pane_is_spawned_at_its_final_size():
    """분할로 생긴 패널의 PTY 는 **처음부터 최종 크기**로 뜬다(pytmux-418).

    종전엔 3×3 으로 띄우고 곧 재배치했다 — 셸이 그 사이 찍은 배너·프롬프트가 ConPTY 의
    3칸 버퍼에서 접혔다 reflow 돼 사라지거나 안 접힌 채 겹쳐 쓰였다. 되돌리면(임시 크기로
    spawn) 이 시험이 운다. 좌우·상하 둘 다, 테두리 상태줄이 있을 때도 잰다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        spawned = []
        real = srv.spawn_pane

        def spy(cols, rows, *a, **k):
            spawned.append((cols, rows))
            return real(cols, rows, *a, **k)
        srv.spawn_pane = spy
        for orient in ("lr", "tb", "lr"):
            srv.split_pane(sess, orient)
            new = sess.active_window.active_pane
            srv._layout_msg(sess)                   # 실제 배치 → 패널 크기 확정
            assert spawned[-1] == (new.cols, new.rows), (
                "분할 패널을 %r 로 띄웠는데 배치는 %r — 띄우자마자 리사이즈된다"
                % (spawned[-1], (new.cols, new.rows)))
        sess.active_window.border_status = True
        srv.split_pane(sess, "tb")
        new = sess.active_window.active_pane
        srv._layout_msg(sess)
        assert spawned[-1] == (new.cols, new.rows), (spawned[-1], (new.cols, new.rows))
    finally:
        await teardown(srv, task, sock)


def test_remote_link_keeps_draining_stderr():
    """원격 링크 ssh 의 stderr 를 핸드셰이크 뒤에도 비운다(#6) — 호출부까지."""
    src = inspect.getsource(serverremote)
    assert "_drain_stderr" in src and "remote_stderr_drain" in src
