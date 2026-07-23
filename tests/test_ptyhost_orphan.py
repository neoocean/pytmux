"""pty-host 고아 회수 테스트(PTYHOST_ORPHAN_2026-07-24 R1~R5).

종전 host 는 종료 트리거가 서버의 `shutdown` op **하나뿐**이라, 그 프레임이 못 가는 모든
경로(서버 SIGKILL·fatal·폴백 모드 종료·중복 host 경합)에서 자식 셸까지 안은 채 영구
잔존했다. 기존 host 테스트는 **정상 종료 축만** 덮고 있어 이 결함을 못 잡았다 — 이
파일이 그 빈 축(비정상 종료)을 채운다.

  R1 워치독 : 소유자 사망+grace → self-shutdown / 소유자 생존이면 절대 안 죽음
  R2 동기회수: 폴백 서버 종료 시 idle host 만 회수(사용 중 host 는 보존)
  R3 중복방지: spawn 직렬화 락(획득/해제/stale 스틸)
  R5 재바인딩: pane id 보정(옛 셸 흡수 차단) + 미상 패널 prune 게이트

POSIX 검증 / Windows 스킵(실 ConPTY·detached 경로는 office 라이브 검증)."""
import asyncio
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile

from harness import server_only, teardown
from run import skip
from pytmuxlib import ptyhost, ptyhostmgr, pty_backend
from pytmuxlib.ptyhostclient import PtyHostClient
from pytmuxlib import ptyhostproto as proto


async def _inproc_host(grace=None, *, derived=False):
    """인프로세스 host 를 띄운다. grace 를 주면 그 값으로 워치독을 켠다(초, 문자열).

    derived=True 면 **ptyhostmgr 가 sock_path 에서 파생하는 그 경로**로 listen 한다 —
    동기 회수 경로(shutdown_host_sync)는 sock_path 로부터 endpoint 를 역산하므로."""
    d = tempfile.mkdtemp(prefix="pytmux-orphan-")
    endpoint = (ptyhostmgr.listen_endpoint(os.path.join(d, "t.sock"))
                if derived else os.path.join(d, "host.sock"))
    old = os.environ.get("PYTMUX_PTYHOST_GRACE")
    if grace is not None:
        os.environ["PYTMUX_PTYHOST_GRACE"] = grace
    else:                       # 기본 60s 워치독이 테스트 중 발화하지 않게 명시 OFF
        os.environ["PYTMUX_PTYHOST_GRACE"] = "0"
    try:
        host = ptyhost.PtyHost()
        htask = asyncio.ensure_future(host.serve(endpoint))
        for _ in range(200):
            if os.path.exists(endpoint):
                break
            await asyncio.sleep(0.01)
    finally:
        if old is None:
            os.environ.pop("PYTMUX_PTYHOST_GRACE", None)
        else:
            os.environ["PYTMUX_PTYHOST_GRACE"] = old
    return host, htask, endpoint, d


async def _stop_host(host, htask, d, *clients):
    for c in clients:
        with contextlib.suppress(Exception):
            await c.close()
    if host._server is not None:
        host._server.close()
    if not htask.done():
        htask.cancel()
    try:
        await htask
    except (asyncio.CancelledError, Exception):
        pass
    for pid in list(host.panes):
        host._close_pane(pid)
    shutil.rmtree(d, ignore_errors=True)


async def _await(pred, timeout=5.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.03)
    return pred()


def _dead_pid() -> int:
    """확실히 죽은(그리고 거둬진) pid 를 만든다 — 워치독의 '소유자 사망' 입력."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# ── R1 워치독 ────────────────────────────────────────────────────────────────
async def test_watchdog_reaps_host_when_owner_dead():
    """소유 서버가 죽고 grace 가 지나면 host 가 패널까지 닫고 스스로 내려간다.

    이게 제보(“pytmux 종료 후에도 ptyhost 잔존”)의 정면 수정이다. 종전엔 이 상황에서
    host 가 `_stop` 을 영원히 기다리며 자식 셸을 붙들고 있었다."""
    if pty_backend.IS_WINDOWS:
        skip("host 워치독 실 검증은 POSIX 인프로세스 경로에서 — Windows 는 라이브 검증")
        return
    host, htask, endpoint, d = await _inproc_host(grace="0.4")
    client = PtyHostClient()
    try:
        await client.connect(endpoint)
        client.spawn(1, ["/bin/cat"], 80, 24)
        assert await _await(lambda: 1 in host.panes), "패널 생성 실패"
        # 소유자를 '죽은 pid' 로 통지한 뒤 연결을 끊는다(= 서버 크래시 시나리오).
        client._send(proto.encode_json({"op": "owner", "pid": _dead_pid()}))
        await asyncio.sleep(0.1)
        await client.close()
        # grace(0.4s) 후 워치독이 발화해 serve 가 끝나야 한다.
        await asyncio.wait_for(htask, 6.0)
        assert not host.panes, "워치독 종료인데 패널(자식 셸) 잔존 — 고아"
    finally:
        await _stop_host(host, htask, d, client)


async def test_watchdog_spares_host_when_owner_alive():
    """소유자가 살아 있으면(재연결 백오프 중인 서버 등) 절대 자살하지 않는다.
    이 가드가 없으면 워치독이 살아있는 세션을 파괴한다."""
    if pty_backend.IS_WINDOWS:
        skip("host 워치독 실 검증은 POSIX 인프로세스 경로에서")
        return
    host, htask, endpoint, d = await _inproc_host(grace="0.4")
    client = PtyHostClient()
    try:
        await client.connect(endpoint)          # owner = 이 테스트 프로세스(생존)
        client.spawn(1, ["/bin/cat"], 80, 24)
        assert await _await(lambda: 1 in host.panes), "패널 생성 실패"
        await client.close()                    # 연결만 끊김(= 서버 재시작 갭)
        await asyncio.sleep(1.5)                # grace 의 3배 이상 기다린다
        assert not htask.done(), "소유자 생존인데 host 가 자살했다(세션 파괴)"
        assert 1 in host.panes, "소유자 생존인데 패널이 닫혔다"
    finally:
        await _stop_host(host, htask, d, client)


async def test_watchdog_reaps_never_claimed_host():
    """아무 서버도 붙지 않은 host(서버가 폴백으로 돌아선 P3)도 grace 후 회수된다."""
    if pty_backend.IS_WINDOWS:
        skip("host 워치독 실 검증은 POSIX 인프로세스 경로에서")
        return
    host, htask, endpoint, d = await _inproc_host(grace="0.4")
    try:
        await asyncio.wait_for(htask, 6.0)
    except asyncio.TimeoutError:
        raise AssertionError("소유자 없는 host 가 grace 후에도 살아 있다")
    finally:
        await _stop_host(host, htask, d)


async def test_grace_zero_disables_watchdog():
    """탈출구: `PYTMUX_PTYHOST_GRACE=0` 이면 워치독이 아예 안 돈다(종전 거동)."""
    if pty_backend.IS_WINDOWS:
        skip("host 워치독 실 검증은 POSIX 인프로세스 경로에서")
        return
    host, htask, endpoint, d = await _inproc_host(grace="0")
    try:
        await asyncio.sleep(0.6)
        assert not htask.done(), "grace=0 인데 워치독이 host 를 내렸다"
    finally:
        await _stop_host(host, htask, d)


# ── R5 재바인딩 게이트 ───────────────────────────────────────────────────────
async def test_list_reply_reports_prev_owner_liveness():
    """list_reply 의 prev_owner_alive: 첫 연결=None(직전 소유자 없음), 재연결=직전
    소유자 생존 여부. 새 서버의 prune 안전 게이트가 이 값 하나에 달려 있다."""
    if pty_backend.IS_WINDOWS:
        skip("POSIX 인프로세스 host 경로")
        return
    host, htask, endpoint, d = await _inproc_host()
    a = PtyHostClient()
    b = PtyHostClient()
    try:
        await a.connect(endpoint)
        await a.list_panes()
        assert a.prev_owner_alive is None, "첫 연결인데 직전 소유자가 보고됐다"
        # 소유자를 '다른, 살아있는 pid'(러너 부모)로 바꾼다 — 같은 프로세스에서 두
        # 클라이언트를 띄우면 pid 가 같아 소유자 교체가 일어나지 않기 때문.
        a._send(proto.encode_json({"op": "owner", "pid": os.getppid()}))
        await asyncio.sleep(0.05)
        # 그 상태에서 b 가 붙으면 prev_owner = 살아있는 러너 부모.
        await b.connect(endpoint)
        await b.list_panes()
        assert b.prev_owner_alive is True, "생존 소유자를 죽은 것으로 보고"
        # 직전 소유자를 죽은 pid 로 바꾼 뒤 다시 붙으면 False 여야 한다.
        b._send(proto.encode_json({"op": "owner", "pid": _dead_pid()}))
        await asyncio.sleep(0.05)
        c = PtyHostClient()
        await c.connect(endpoint)
        await c.list_panes()
        assert c.prev_owner_alive is False, "죽은 소유자를 생존으로 보고(prune 불가)"
        await c.close()
    finally:
        await _stop_host(host, htask, d, a, b)


async def test_reattach_without_resume_does_not_adopt_dead_shell():
    """크래시(resume 파일 없음) 후 새 서버가 host 에 붙었을 때, 새 패널이 **옛 셸을
    흡수하지 않는다**. 종전엔 _pane_seq 가 0 에서 다시 1을 발급 → host 의 멱등 _spawn
    이 무시 → 새 탭이 죽은 세션의 셸에 연결됐다(실측)."""
    if pty_backend.IS_WINDOWS:
        skip("POSIX 인프로세스 host 경로")
        return
    host, htask, endpoint, d = await _inproc_host()
    srv, task, sock = await server_only()
    old = PtyHostClient()
    new = PtyHostClient()
    try:
        # 옛 서버가 남긴 패널(id 1) — 죽은 세션의 셸.
        await old.connect(endpoint)
        old.spawn(1, ["/bin/cat"], 80, 24)
        assert await _await(lambda: 1 in host.panes), "선행 패널 생성 실패"
        old_pty = host.panes[1].pty
        await old.close()
        # 새 서버가 붙어 host 패널 목록을 반영(serve() 의 재연결 경로와 동형).
        await new.connect(endpoint)
        srv._pty_host = new
        srv._adopt_host_panes(await new.list_panes())
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        assert pane.host_pane_id != 1, (
            f"새 패널이 옛 host 패널 id 를 재사용했다(id={pane.host_pane_id}) "
            "— 죽은 세션의 셸을 흡수한다")
        assert await _await(lambda: pane.host_pane_id in host.panes), \
            "새 패널이 host 에 생성되지 않았다"
        assert host.panes[pane.host_pane_id].pty is not old_pty, \
            "새 패널이 옛 셸의 PTY 를 그대로 물려받았다"
    finally:
        await teardown(srv, task, sock)
        await _stop_host(host, htask, d, old, new)


async def test_prune_unknown_host_panes_gated_by_prev_owner():
    """미상 패널 prune 은 **직전 소유자가 죽었을 때만** 돈다. 살아있는 다른 서버와
    host 를 공유하는 경쟁 상황에서 남의 셸을 죽이면 안 된다."""
    if pty_backend.IS_WINDOWS:
        skip("POSIX 인프로세스 host 경로")
        return
    host, htask, endpoint, d = await _inproc_host()
    srv, task, sock = await server_only()
    client = PtyHostClient()
    try:
        await client.connect(endpoint)
        client.spawn(1, ["/bin/cat"], 80, 24)
        client.spawn(2, ["/bin/cat"], 80, 24)
        assert await _await(lambda: {1, 2} <= set(host.panes)), "선행 패널 생성 실패"
        srv._pty_host = client
        srv._host_resume_alive = {1, 2}
        # ① 게이트 닫힘(직전 소유자 생존/미상) → 아무것도 안 닫는다.
        srv._host_prune_ok = False
        srv._prune_unknown_host_panes()
        await asyncio.sleep(0.2)
        assert {1, 2} <= set(host.panes), "게이트가 닫혔는데 남의 패널을 닫았다"
        # ② 게이트 열림(직전 소유자 사망) → 서버가 모르는 패널을 회수한다.
        srv._host_prune_ok = True
        srv._prune_unknown_host_panes()
        assert await _await(lambda: not ({1, 2} & set(host.panes))), \
            "고아 패널이 회수되지 않았다"
    finally:
        await teardown(srv, task, sock)
        await _stop_host(host, htask, d, client)


# ── R2 동기 회수 ─────────────────────────────────────────────────────────────
async def test_shutdown_host_sync_idle_only():
    """폴백 서버 종료 경로: **패널 0개인 host 만** 내린다(사용 중 host 는 보존).
    이벤트 루프 없이 도는 blocking 경로라 별도 스레드에서 호출한다."""
    if pty_backend.IS_WINDOWS:
        skip("POSIX 인프로세스 host 경로(동기 회수는 Windows 도 같은 코드)")
        return
    host, htask, endpoint, d = await _inproc_host(derived=True)
    sock_path = os.path.join(d, "t.sock")     # listen_endpoint(sock_path) == endpoint
    client = PtyHostClient()
    try:
        await client.connect(endpoint)
        client.spawn(1, ["/bin/cat"], 80, 24)
        assert await _await(lambda: 1 in host.panes), "패널 생성 실패"
        sent = await asyncio.to_thread(
            ptyhostmgr.shutdown_host_sync, sock_path, idle_only=True)
        await asyncio.sleep(0.2)
        assert sent is False, "사용 중(패널 보유) host 에 shutdown 을 보냈다"
        assert not htask.done(), "사용 중 host 가 내려갔다"
        # 패널을 비우면 같은 호출이 이제 host 를 내린다.
        client.close_pane(1)
        assert await _await(lambda: not host.panes), "패널 정리 실패"
        sent = await asyncio.to_thread(
            ptyhostmgr.shutdown_host_sync, sock_path, idle_only=True)
        assert sent is True, "idle host 회수 실패"
        await asyncio.wait_for(htask, 5.0)
    finally:
        await _stop_host(host, htask, d, client)


async def test_sync_probe_does_not_evict_live_server():
    """회수 판정용 단발 연결(probe)은 **현재 서버 연결을 대체하지 않는다**. host 는
    '새 연결이 옛 연결을 대체'하는 모델이라, 이 가드가 없으면 조회 한 번이 살아있는
    서버를 host 에서 떼어내 재연결 churn(과 그 사이 출력 버퍼링)을 만든다."""
    if pty_backend.IS_WINDOWS:
        skip("POSIX 인프로세스 host 경로")
        return
    host, htask, endpoint, d = await _inproc_host(derived=True)
    sock_path = os.path.join(d, "t.sock")
    client = PtyHostClient()
    try:
        await client.connect(endpoint)
        client.spawn(1, ["/bin/cat"], 80, 24)
        assert await _await(lambda: 1 in host.panes), "패널 생성 실패"
        live = host._writer
        await asyncio.to_thread(
            ptyhostmgr.shutdown_host_sync, sock_path, idle_only=True)
        await asyncio.sleep(0.2)
        assert host._writer is live, "probe 가 살아있는 서버 연결을 대체했다"
        # 원래 연결이 그대로 살아 있어 제어가 계속 먹는다.
        client.close_pane(1)
        assert await _await(lambda: not host.panes), \
            "probe 이후 원래 서버 연결이 죽었다(제어 미도달)"
    finally:
        await _stop_host(host, htask, d, client)


async def test_shutdown_host_sync_forced_kills_busy_host():
    """`pytmux kill-server` 경로(idle_only=False): 사용자의 명시 종료이므로 패널이
    있어도 내린다(그 셸들은 소유 서버가 없는 고아다)."""
    if pty_backend.IS_WINDOWS:
        skip("POSIX 인프로세스 host 경로")
        return
    host, htask, endpoint, d = await _inproc_host(derived=True)
    sock_path = os.path.join(d, "t.sock")
    client = PtyHostClient()
    try:
        await client.connect(endpoint)
        client.spawn(1, ["/bin/cat"], 80, 24)
        assert await _await(lambda: 1 in host.panes), "패널 생성 실패"
        await client.close()
        sent = await asyncio.to_thread(
            ptyhostmgr.shutdown_host_sync, sock_path)
        assert sent is True, "고아 host 강제 회수 실패"
        await asyncio.wait_for(htask, 5.0)
        assert not host.panes, "강제 회수인데 패널 잔존"
    finally:
        await _stop_host(host, htask, d, client)


# ── R3 중복 host 방지 ────────────────────────────────────────────────────────
async def test_spawn_lock_serializes_and_expires():
    """spawn 직렬화 락: 두 번째 획득은 실패(=중복 host 안 띄움), 해제 후 재획득 가능,
    stale(TTL 초과) 락은 스틸한다(락 소유자가 크래시해도 영구 잠김 없음)."""
    d = tempfile.mkdtemp(prefix="pytmux-spawnlock-")
    sock_path = os.path.join(d, "t.sock")
    try:
        assert ptyhostmgr._acquire_spawn_lock(sock_path) is True, "첫 락 획득 실패"
        assert ptyhostmgr._acquire_spawn_lock(sock_path) is False, \
            "락이 걸렸는데 두 번째 spawn 이 허용됐다(중복 host)"
        ptyhostmgr._release_spawn_lock(sock_path)
        assert ptyhostmgr._acquire_spawn_lock(sock_path) is True, "해제 후 재획득 실패"
        # TTL 을 넘긴 락은 스틸된다(크래시 잔재 자가 복구).
        path = ptyhostmgr._spawn_lockfile(sock_path)
        with open(path, "w", encoding="ascii") as f:
            f.write("999999")            # 남의(죽은) 락으로 위장
        old = os.stat(path)              # 쓰기가 mtime 을 갱신하므로 **쓴 뒤에** 늙힌다
        os.utime(path, (old.st_atime - 3600, old.st_mtime - 3600))
        assert ptyhostmgr._acquire_spawn_lock(sock_path) is True, \
            "stale 락이 영구 잠김 상태로 남았다"
        ptyhostmgr._release_spawn_lock(sock_path)
        assert not os.path.exists(path), "해제 후에도 락 파일 잔존"
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_departing_server_spares_successor_socket():
    """물러나는 서버는 **자기 소켓일 때만** AF_UNIX 소켓 파일을 지운다.

    종전엔 무조건 unlink 라 ① 경쟁에서 물러나는 서버가 승자의 소켓을 지우고(§3 E3-3)
    ② host 모드 restart-all 에서 옛 서버가 **후속 서버의** 소켓을 지워 `pytmux ls` 가
    "실행 중인 서버 없음"이 됐다(실측). 두 경로 모두 이 헬퍼 하나를 쓴다."""
    if pty_backend.IS_WINDOWS:
        skip("AF_UNIX 소켓 파일 경로(Windows 는 TCP+포트파일)")
        return
    srv, task, sock = await server_only()
    try:
        assert os.path.exists(sock), "테스트 전제: 소켓 파일 존재"
        # 후속 서버가 같은 이름을 자기 소켓으로 교체한 상황을 모사(inode 가 바뀐다).
        os.unlink(sock)
        with open(sock, "w", encoding="ascii") as f:
            f.write("")                       # 남의 것(다른 inode)
        assert srv._unlink_sock_if_mine() is False, \
            "남의(후속 서버) 소켓을 지웠다 — 살아있는 서버가 도달 불가가 된다"
        assert os.path.exists(sock), "남의 소켓 파일이 사라졌다"
        # 내 inode 로 맞춰 두면 정상 종료 경로에서 제대로 정리된다(청소 회귀 방지).
        srv._sock_ino = os.stat(sock).st_ino
        assert srv._unlink_sock_if_mine() is True, "내 소켓을 못 지웠다"
        assert not os.path.exists(sock), "내 소켓 파일이 남았다"
    finally:
        await teardown(srv, task, sock)


async def test_host_running_uses_pid_liveness():
    """host_running 은 파일 존재가 아니라 **pid 생존**으로 판정한다(죽은 host 의 잔재
    pidfile 을 '살아있다'고 오판하면 prespawn 을 건너뛰어 host 모드가 조용히 죽는다)."""
    d = tempfile.mkdtemp(prefix="pytmux-hostpid-")
    sock_path = os.path.join(d, "t.sock")
    try:
        with open(ptyhostmgr.host_pidfile(sock_path), "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
        assert ptyhostmgr.host_running(sock_path) is True, "생존 host 를 못 알아봤다"
        with open(ptyhostmgr.host_pidfile(sock_path), "w", encoding="ascii") as f:
            f.write(str(_dead_pid()))
        assert ptyhostmgr.host_running(sock_path) is False, \
            "죽은 host 의 pidfile 을 생존으로 판정(중복/누락 spawn 의 원인)"
    finally:
        shutil.rmtree(d, ignore_errors=True)
