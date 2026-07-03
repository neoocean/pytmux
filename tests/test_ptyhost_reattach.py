"""host 모드 재시작 reattach 테스트(옵션 C P5a) — 서버 재시작을 가로질러 세션 유지.

핵심 시나리오: 서버 A 가 host 에 패널을 띄우고(살아있는 셸) resume 상태를 직렬화 →
서버 B(새 서버=재시작 후 이미지)가 **같은 host 에 재연결**해 resume 를 복원하면, host 가
살려 둔 같은 원격 PTY 에 host_pane_id 로 재바인딩되어 셸이 계속 살아 동작한다. 이게
Windows 세션유지 재시작의 본질(POSIX execv 대신 host 상주 + 재연결). POSIX 검증 / Win 스킵."""
import asyncio
import os
import shutil
import tempfile

import harness
from harness import server_only, teardown
from pytmuxlib import ptyhost, pty_backend
from pytmuxlib.ptyhostclient import PtyHostClient


async def _inproc_host():
    d = tempfile.mkdtemp(prefix="pytmux-reattach-")
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


async def test_host_resume_reattach_keeps_session():
    if pty_backend.IS_WINDOWS:
        return
    host, htask, endpoint, d = await _inproc_host()
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    clientA = PtyHostClient(srvA.loop)
    clientB = PtyHostClient(srvB.loop)
    state_path = tempfile.mktemp(suffix=".resume.json")
    try:
        # ── 서버 A: host 모드로 패널 생성(장수명 cat 셸), resume 상태 직렬화 ──
        await clientA.connect(endpoint)
        srvA._pty_host = clientA
        sessA = srvA.ensure_default_session(80, 24)
        paneA = sessA.active_window.active_pane
        hpid = paneA.host_pane_id
        assert hpid is not None
        paneA.pty.write(b"echo BEFORE_RESTART_55\n")
        assert await _pane_has(paneA, "BEFORE_RESTART_55"), harness.pane_text(paneA)
        assert srvA.save_resume_state(state_path), "resume 직렬화 실패"
        # 서버 A 종료(셸은 host 가 계속 소유 — 끊겨도 죽지 않음).
        await clientA.close()
        srvA.sessions.clear()

        # ── 서버 B(=재시작 후 새 이미지): 같은 host 재연결 + resume 복원 ──
        await clientB.connect(endpoint)
        srvB._pty_host = clientB
        panes = await clientB.list_panes()
        srvB._host_resume_alive = {p["pane"] for p in panes if p["alive"]}
        assert hpid in srvB._host_resume_alive, "host 가 패널을 못 살림"
        # reattach 후엔 'spawned' 프레임이 다시 오지 않으므로 list_reply 가 실제 셸
        # pid 를 재구성해야 한다 — 안 그러면 pane.pty.pid=-1 이라 _pane_cwd(ncd·
        # default-path=current)가 재시작 후 기존 패널에서 cwd 를 못 찾는다.
        shell_pid = clientB.pid(hpid)
        assert shell_pid > 0, f"reattach 후 셸 pid 미복구: {shell_pid}"
        ok = srvB.restore_resume_state(state_path)
        assert ok, "resume 복원 실패"
        # 복원된 패널이 같은 host_pane_id 로 재바인딩됐는지.
        paneB = next(iter(srvB.sessions.values())).active_window.active_pane
        assert paneB.host_pane_id == hpid, (paneB.host_pane_id, hpid)
        assert type(paneB.pty).__name__ == "_RemotePtyProcess"
        # 재바인딩된 프록시가 재구성된 실제 셸 pid 를 돌려주고, host 모드(child_pid=-1)
        # 에서도 그 pid 로 셸 cwd 를 구할 수 있다(POSIX /proc).
        assert paneB.pty.pid == shell_pid
        assert srvB._pane_cwd(paneB) is not None
        # ★살아 있는 같은 셸이 계속 동작 — 재시작 후 새 입력이 먹는다.
        paneB.pty.write(b"echo AFTER_RESTART_66\n")
        assert await _pane_has(paneB, "AFTER_RESTART_66"), harness.pane_text(paneB)
        # _pane_seq 가 reattach id 위로 보정돼 새 spawn 이 충돌하지 않는다.
        assert srvB._pane_seq >= hpid
        new_pane = srvB.spawn_pane(80, 24)
        assert new_pane.host_pane_id != hpid
    finally:
        try:
            os.unlink(state_path)
        except OSError:
            pass
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)
        await _stop_host(host, htask, d, clientA, clientB)


async def test_host_resume_skips_dead_pane():
    """갭 중 죽은(host 가 안 들고 있는) 패널은 복원에서 건너뛴다(_host_resume_alive 부재)."""
    if pty_backend.IS_WINDOWS:
        return
    host, htask, endpoint, d = await _inproc_host()
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    clientA = PtyHostClient(srvA.loop)
    clientB = PtyHostClient(srvB.loop)
    state_path = tempfile.mktemp(suffix=".resume.json")
    try:
        await clientA.connect(endpoint)
        srvA._pty_host = clientA
        sessA = srvA.ensure_default_session(80, 24)
        paneA = sessA.active_window.active_pane
        assert srvA.save_resume_state(state_path)
        await clientA.close()
        srvA.sessions.clear()

        await clientB.connect(endpoint)
        srvB._pty_host = clientB
        srvB._host_resume_alive = set()        # host 가 아무 패널도 안 살림(전부 죽음)
        srvB.restore_resume_state(state_path)
        # 생존 패널이 없으니 세션 복원이 비어야 한다(죽은 노드 스킵 → 빈 탭 제외).
        assert not srvB.sessions, "죽은 패널이 복원됨"
    finally:
        try:
            os.unlink(state_path)
        except OSError:
            pass
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)
        await _stop_host(host, htask, d, clientA, clientB)
