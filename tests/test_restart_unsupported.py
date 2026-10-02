"""재시작을 못 하는 서버는 그렇다고 말한다 · pty-host 에 못 붙은 까닭이 남는다 (pytmux-514).

Windows 서버가 기동 때 pty-host 에 못 붙으면 in-process 로 폴백하고, 그 순간부터
`:restart-server` 는 구조적으로 아무 일도 안 한다. 종전에는 ⓐ 그 실패의 사유가 어디에도
없었고(`[ptyhost_connect]` + `NoneType: None`) ⓑ 클라는 「그래도 할까」를 물어 예를 누르면
아무 일도 없이 끝났다 — 사용자는 재시작했다고 믿는데 3일 된 서버가 옛 코드를 쥐고 있었다.

되돌리면 실패해야 하는 오라클:
  · `_try_connect` 가 사유를 안 남기면 → test_a_missing_endpoint_leaves_a_reason
  · 서버가 그 사유를 로그에 안 실으면(**호출부**) → test_the_server_logs_why_it_fell_back
  · 드라이런 회신에 `host_fail` 이 없으면 → test_the_dry_run_carries_the_reason_on_windows
  · 정본이 다시 「그래도 할까」를 물으면(**호출부**) → test_the_client_says_why_instead_of_asking
"""
import asyncio

import harness
from harness import server_only, teardown

from pytmuxlib import ipc, ptyhostmgr, pty_backend


async def test_a_missing_endpoint_leaves_a_reason(tmp_path=None):
    sock = "/nonexistent/pytmux-514/default.sock"
    ptyhostmgr._LAST_FAILURE.pop(sock, None)
    client = await ptyhostmgr._try_connect(asyncio.get_running_loop(), sock, 0.1)
    assert client is None
    why = ptyhostmgr.last_failure(sock)
    assert why and "endpoint" in why, why


async def test_the_server_logs_why_it_fell_back():
    """★ 호출부 — 기동 경로가 그 사유를 `ptyhost_connect` 진단 줄에 싣는다(트레이스백 아님)."""
    async def fake_connect(loop, sock_path):
        ptyhostmgr._note_failure(sock_path, "connect to tcp:127.0.0.1:1 timed out after 1s")
        return None

    with harness.patched(ptyhostmgr, host_enabled=lambda: True,
                         ensure_connected=fake_connect):
        srv, task, sock = await server_only()
    try:
        with open(ipc.state_base(sock) + ".error.log", encoding="utf-8") as f:
            text = f.read()
        assert "[ptyhost_connect]" in text, text
        assert "timed out after 1s" in text, text
        assert "Traceback" not in text and "NoneType: None" not in text, text
    finally:
        await teardown(srv, task, sock)


async def test_the_dry_run_carries_the_reason_on_windows():
    srv, task, sock = await server_only()
    try:
        ptyhostmgr._note_failure(srv.sock_path, "spawning pty-host failed: OSError: x")
        with harness.patched(pty_backend, IS_WINDOWS=True):
            chk = srv.restart_check()
        assert chk["reexec_supported"] is False
        assert chk["host_fail"] == "spawning pty-host failed: OSError: x", chk
        # POSIX(그리고 host 모드)에서는 비어 있다 — 재기동이 되는 서버에 사유를 안 단다.
        chk2 = srv.restart_check()
        assert chk2["host_fail"] == "", chk2
    finally:
        ptyhostmgr._LAST_FAILURE.pop(srv.sock_path, None)
        await teardown(srv, task, sock)


async def test_the_client_says_why_instead_of_asking():
    """★ 호출부 — 재기동을 못 하는 서버면 확인 창도 restart_server 도 없다. 까닭이 뜬다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        sent, shown = [], []
        app.send_cmd = lambda action, **kw: sent.append(action)
        real = app.display_message

        def spy(text, *a, **kw):
            shown.append((text, kw.get("severity")))
            return real(text, *a, **kw)
        app.display_message = spy
        app._run_command("restart-server")
        assert sent == ["request_restart_check"]
        depth = len(app.screen_stack)
        app._dispatch({"t": "restart_check", "reexec_supported": False,
                       "server_os": "windows", "host_fail": "connect: TimeoutError",
                       "has_sessions": True, "serialize_ok": True,
                       "panes": 1, "panes_with_fd": 1})
        assert sent == ["request_restart_check"], f"재기동을 못 하는 서버에 보냈다: {sent}"
        assert len(app.screen_stack) == depth, "확인 창을 띄웠다 — 예를 눌러도 아무 일도 없다"
        assert any("connect: TimeoutError" in t and sev == "error" for t, sev in shown), shown

    await _with_app(body)
