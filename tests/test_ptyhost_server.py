"""서버 host 모드 배선 테스트(옵션 C P4) — spawn_pane/respawn_pane 이 host 경유.

host 를 인프로세스로 띄우고 server._pty_host 에 연결된 PtyHostClient 를 주입해, 서버의
패널 생성이 host 가 소유한 원격 PTY(_RemotePtyProcess)를 통해 동작함을 검증한다(detached
서브프로세스 기동은 ptyhostmgr 가 담당·office 라이브 검증). POSIX 전 기능 / Windows 스킵.

추가로 ptyhostmgr 게이팅(env 토글)과 reconnect(이미 뜬 host 재연결)를 검증."""
import asyncio
import os
import shutil
import tempfile

import harness
from harness import server_only, teardown
from pytmuxlib import ptyhost, ptyhostmgr, pty_backend, serverio
from pytmuxlib.ptyhostclient import PtyHostClient


async def _inproc_host():
    d = tempfile.mkdtemp(prefix="pytmux-srvhost-")
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


async def _pane_has(pane, needle: str, timeout=4.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if needle in harness.pane_text(pane):
            return True
        await asyncio.sleep(0.03)
    return needle in harness.pane_text(pane)


async def test_host_enabled_env_toggle():
    # run.py 는 async 'test_' 함수만 실행하므로 async 로 둔다(await 불필요).
    old = os.environ.get("PYTMUX_PTY_HOST")
    try:
        os.environ["PYTMUX_PTY_HOST"] = "1"
        assert ptyhostmgr.host_enabled() is True
        os.environ["PYTMUX_PTY_HOST"] = "0"
        assert ptyhostmgr.host_enabled() is False
        os.environ.pop("PYTMUX_PTY_HOST", None)
        assert ptyhostmgr.host_enabled() is pty_backend.IS_WINDOWS
    finally:
        if old is None:
            os.environ.pop("PYTMUX_PTY_HOST", None)
        else:
            os.environ["PYTMUX_PTY_HOST"] = old


async def test_server_spawn_pane_via_host():
    if pty_backend.IS_WINDOWS:
        return
    host, htask, endpoint, d = await _inproc_host()
    srv, task, sock = await server_only()
    client = PtyHostClient(srv.loop)
    try:
        await client.connect(endpoint)
        srv._pty_host = client                  # host 모드 주입
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        # host 모드 패널이어야 한다(host_pane_id 설정, 원격 프록시).
        assert pane.host_pane_id is not None, "host 모드 패널 아님"
        assert type(pane.pty).__name__ == "_RemotePtyProcess"
        # 실제 셸이 host 에서 떠 출력이 서버 화면(pyte)까지 흐른다.
        pane.pty.write(b"echo HOST_ROUTED_77\n")
        assert await _pane_has(pane, "HOST_ROUTED_77"), harness.pane_text(pane)
        # host 가 실제로 패널을 소유한다.
        assert pane.host_pane_id in host.panes
    finally:
        await teardown(srv, task, sock)
        await _stop_host(host, htask, d, client)


async def test_server_respawn_pane_via_host():
    if pty_backend.IS_WINDOWS:
        return
    host, htask, endpoint, d = await _inproc_host()
    srv, task, sock = await server_only()
    client = PtyHostClient(srv.loop)
    try:
        await client.connect(endpoint)
        srv._pty_host = client
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        first_id = pane.host_pane_id
        srv.respawn_pane(sess)                  # 같은 슬롯에 새 host 패널
        assert pane.host_pane_id is not None and pane.host_pane_id != first_id
        pane.pty.write(b"echo RESPAWNED_HOST\n")
        assert await _pane_has(pane, "RESPAWNED_HOST"), harness.pane_text(pane)
        # 옛 패널 id 는 host 에서 정리됐어야 한다(close).
        await asyncio.sleep(0.1)
        assert first_id not in host.panes, "옛 host 패널 미정리"
    finally:
        await teardown(srv, task, sock)
        await _stop_host(host, htask, d, client)


async def test_ptyhostmgr_reconnects_existing_host():
    """이미 떠 있는 host 에 ensure_connected 가 새 서브프로세스 없이 재연결한다
    (서버 재시작 경로의 토대)."""
    if pty_backend.IS_WINDOWS:
        return
    host, htask, endpoint, d = await _inproc_host()
    try:
        # ptyhostmgr 는 sock_path 기반으로 endpoint 를 파생하므로, 그 경로에 맞춰
        # 미리 띄운 host 의 endpoint 를 쓰도록 sock_path 를 역산해 맞춘다.
        # 여기선 _connect_endpoint 가 unix 경로(존재 시)를 그대로 쓰는 점을 이용:
        # 임시 sock_path 의 state_base + ".ptyhost.sock" == endpoint 가 되도록 구성.
        loop = asyncio.get_event_loop()
        # endpoint 가 곧 listen_endpoint 가 되도록 sock_path 를 맞춘다.
        base = endpoint[:-len(".ptyhost.sock")]
        sock_path = base                      # state_base(sock_path)=sock_path(unix)
        client = await ptyhostmgr.ensure_connected(loop, sock_path)
        assert client is not None, "기존 host 재연결 실패"
        await client.close()
    finally:
        await _stop_host(host, htask, d)


async def test_reconnect_storm_guard():
    """host 재연결 폭주 가드: 두 서버가 같은 host 를 다툴 때 생기는 무한 즉시 재연결을
    막는다. 직전 연결이 곧바로 끊기는 급속 churn 이 누적되면(burst), MAX_BURST 초과 시
    ① 클라 없는 stale 중복 서버는 스스로 종료(승자에게 host 양보), ② 클라 붙은 서버는
    종료하지 않고 재시도를 이어간다(burst 는 CAP 에 고정).

    (host/PTY 를 실제로 띄우지 않고 ensure_connected 를 모킹해 가드 로직만 검증하므로
    OS 무관 — 다른 ptyhost 테스트와 달리 Windows 에서도 돈다.)"""
    srv, task, sock = await server_only()
    base = serverio.HOST_RECONNECT_BACKOFF_BASE
    cap = serverio.HOST_RECONNECT_BACKOFF_CAP
    real_ec = ptyhostmgr.ensure_connected
    real_shutdown = srv.shutdown
    shutdown_calls = []

    async def fake_ec(loop, sock_path):
        return None     # 재연결 실패(=즉시 끊김 등가): _host_last_connect_ts 갱신 안 됨

    try:
        serverio.HOST_RECONNECT_BACKOFF_BASE = 0.0   # 테스트가 즉시 끝나게 백오프 0
        serverio.HOST_RECONNECT_BACKOFF_CAP = 0.0
        ptyhostmgr.ensure_connected = fake_ec
        srv.shutdown = lambda: shutdown_calls.append(True)
        mb = serverio.HOST_RECONNECT_MAX_BURST

        # ① 클라 없는 stale 서버: MAX_BURST 까지는 burst 누적·종료 없음.
        srv.clients.clear()
        srv._host_reconnect_burst = 0
        srv._host_last_connect_ts = srv.loop.time()   # 방금 붙음 → 급속 churn 조건
        for _ in range(mb):
            await srv._reconnect_host()
        assert srv._host_reconnect_burst == mb, srv._host_reconnect_burst
        assert not shutdown_calls, "MAX_BURST 이내에 조기 종료"
        # MAX_BURST 초과 → 클라 없으니 스스로 종료 예약(call_soon → 한 틱 양보).
        await srv._reconnect_host()
        await asyncio.sleep(0)
        assert shutdown_calls, "stale 클라없는 서버가 폭주에도 종료 안 함"

        # ② 클라 붙은 서버: 폭주에도 종료하지 않고 burst 를 CAP 에 고정한 채 재시도.
        shutdown_calls.clear()
        dummy_client = object()
        srv.clients.append(dummy_client)
        srv._host_reconnect_burst = mb + 5            # 이미 폭주 상태
        srv._host_last_connect_ts = srv.loop.time()
        await srv._reconnect_host()
        await asyncio.sleep(0)
        assert not shutdown_calls, "클라 붙은 서버가 폭주에 종료됨"
        assert srv._host_reconnect_burst == mb, "burst 가 CAP 에 고정 안 됨"
        srv.clients.remove(dummy_client)

        # ③ 안정 연결(STABLE 경과)이면 burst 가 리셋된다.
        srv._host_reconnect_burst = mb
        srv._host_last_connect_ts = (
            srv.loop.time() - serverio.HOST_RECONNECT_STABLE_SEC - 1.0)
        await srv._reconnect_host()
        assert srv._host_reconnect_burst == 0, "안정 연결 후 burst 미리셋"
    finally:
        serverio.HOST_RECONNECT_BACKOFF_BASE = base
        serverio.HOST_RECONNECT_BACKOFF_CAP = cap
        ptyhostmgr.ensure_connected = real_ec
        srv.shutdown = real_shutdown
        await teardown(srv, task, sock)
