"""host 모드 restart_server 오케스트레이션 테스트(옵션 C P5b) — Windows 세션유지 재시작.

restart_server 가 host 모드면 execv 대신 후속 서버 프로세스를 띄우고(이 서버는 패널을
terminate 하지 않고 종료) host(세션)를 살린다. detached 기동/loop.stop 은 패치해 단위
검증한다(실제 프로세스 핸드오프는 office Windows 라이브 = P7). POSIX / Win 스킵."""
import asyncio
import json
import os
import shutil
import tempfile

from harness import server_only, teardown
from pytmuxlib import ptyhost, proc as procmod, pty_backend
from pytmuxlib.ptyhostclient import PtyHostClient


async def _inproc_host():
    d = tempfile.mkdtemp(prefix="pytmux-restart-")
    endpoint = os.path.join(d, "host.sock")
    host = ptyhost.PtyHost()
    htask = asyncio.ensure_future(host.serve(endpoint))
    for _ in range(100):
        if os.path.exists(endpoint):
            break
        await asyncio.sleep(0.01)
    return host, htask, endpoint, d


async def _stop_host(host, htask, d, *clients):
    for c in clients:
        await c.close()
    if host._server is not None:
        host._server.close()
    htask.cancel()
    try:
        await htask
    except (asyncio.CancelledError, Exception):
        pass
    for pid in list(host.panes):
        host._close_pane(pid)
    shutil.rmtree(d, ignore_errors=True)


def _walk_host_ids(payload):
    ids = []

    def rec(node):
        if node.get("type") == "split":
            rec(node["a"])
            rec(node["b"])
        else:
            hp = node.get("pane", {}).get("host_pane_id")
            if hp is not None:
                ids.append(hp)
    for s in payload.get("sessions", []):
        for t in s.get("tabs", []):
            rec(t["window"]["root"])
    return ids


async def test_restart_server_host_preserves_session():
    if pty_backend.IS_WINDOWS:
        return
    host, htask, endpoint, d = await _inproc_host()
    srv, task, sock = await server_only()
    client = PtyHostClient(srv.loop)
    captured = []
    orig_spawn = procmod.spawn_detached
    orig_stop = srv.loop.stop
    stop_called = []
    try:
        await client.connect(endpoint)
        srv._pty_host = client
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        hpid = pane.host_pane_id
        assert hpid is not None
        # 후속 서버 detached 기동·loop.stop 을 패치(실제 핸드오프/루프정지 차단).
        # loop.stop 은 반드시 finally 에서 복구한다 — 안 하면 runner 가 루프를 못 닫아 hang.
        procmod.spawn_detached = lambda argv, **kw: captured.append(list(argv))
        srv.loop.stop = lambda: stop_called.append(1)        # type: ignore

        ok = srv.restart_server()
        assert ok, "host 모드 restart_server 가 False"
        # resume 가 host_pane_id 와 함께 직렬화됐는지.
        data = json.load(open(srv.resume_state_path, encoding="utf-8"))
        assert hpid in _walk_host_ids(data), "resume 에 host_pane_id 없음"
        # call_later(0.2, _host_restart_exit) 가 발화하도록 대기.
        await asyncio.sleep(0.4)
        # 후속 서버가 --resume 로 detached 기동됐는지.
        assert captured, "후속 서버 미기동"
        assert "--resume" in captured[0], captured[0]
        # 종료가 발생했고(loop.stop 패치 호출).
        assert stop_called, "_host_restart_exit 미발화"
        # ★핵심: 패널이 terminate 되지 않아 host 가 여전히 소유·자식 생존.
        assert hpid in host.panes and host.panes[hpid].alive, "재시작이 셸을 죽임"
    finally:
        procmod.spawn_detached = orig_spawn
        srv.loop.stop = orig_stop                            # type: ignore
        await teardown(srv, task, sock)
        await _stop_host(host, htask, d, client)


async def test_restart_check_host_mode_supported():
    if pty_backend.IS_WINDOWS:
        return
    host, htask, endpoint, d = await _inproc_host()
    srv, task, sock = await server_only()
    client = PtyHostClient(srv.loop)
    try:
        await client.connect(endpoint)
        srv._pty_host = client
        sess = srv.ensure_default_session(80, 24)
        chk = srv.restart_check()
        assert chk["host_mode"] is True
        assert chk["reexec_supported"] is True, "host 모드 재시작 미지원 보고"
        # host 패널(master_fd=-1)도 복원 가능으로 카운트.
        assert chk["panes"] >= 1 and chk["panes_with_fd"] == chk["panes"]
        assert chk["serialize_ok"] is True
    finally:
        await teardown(srv, task, sock)
        await _stop_host(host, htask, d, client)
