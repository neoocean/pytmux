"""Tier 3 라이브 레드팀 코어 회귀 — docs/internal/SECURITY_REVIEW.md §9.3.

`scripts/redteam.py` 의 배터리·자원 표본 헬퍼를 **하네스 서버**에 붙여 가볍게 검증한다
(풀 spawn/attach CLI 는 로컬/Windows 수동 — 헤드리스 macOS CI wedge 회피). 단언:
  · 적대 배터리의 무인가/손상 프레임은 전부 거절(auth_failed)·드롭되고 **무인가 수용 0**,
  · 플러드 뒤에도 서버 생존 + **fd 누수 없음**,
  · 자원 표본 헬퍼가 정수를 돌려준다.
"""
import asyncio
import gc
import os
import sys
import tempfile
import time

import harness  # noqa: F401 (sys.path 주입)
from harness import server_only, teardown
from pytmuxlib import ipc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "scripts"))
import redteam  # noqa: E402


async def test_redteam_battery_rejects_unauth_and_keeps_alive():
    srv, task, ep = await server_only()
    try:
        counts = await redteam.run_battery(ep, rounds=15)
        # 무인가 hello/wrong-token 은 거절, 손상 프레임은 드롭, 수용은 0.
        assert counts["sent"] == 15 * 6, counts
        assert counts["accepted_unexpected"] == 0, counts
        assert counts["rejected"] >= 15 * 2, counts        # unauth_hello + wrong_token
        assert counts["dropped"] >= 15 * 3, counts         # oversized/non_json/non_dict/truncated
        assert counts["rejected"] + counts["dropped"] == counts["sent"], counts
        tok = ipc.read_token(ep)
        assert await redteam.authed_list_alive(ep, tok)
    finally:
        await teardown(srv, task, ep)


async def test_redteam_no_fd_leak_under_flood():
    srv, task, ep = await server_only()
    try:
        gc.collect()
        fd0 = redteam.count_fds()
        await redteam.run_battery(ep, rounds=40)
        gc.collect()
        fd1 = redteam.count_fds()
        if fd0 >= 0:                                        # /proc·/dev/fd 가용 플랫폼
            assert fd1 <= fd0 + 8, (fd0, fd1)              # 연결당 fd 미반환이면 누적
    finally:
        await teardown(srv, task, ep)


async def test_redteam_concurrent_flood_survives_no_unauth():
    # 동시 폭주(웨이브당 width 연결을 진짜 동시에) 뒤에도 서버 생존·무인가 수용 0·
    # fd 회수. 순차 배터리가 못 보는 동시 연결 처리(accept 루프·연결당 태스크)를 친다.
    srv, task, ep = await server_only()
    try:
        gc.collect()
        fd0 = redteam.count_fds()
        counts = await redteam.run_concurrent_flood(ep, waves=4, width=12)
        assert counts["sent"] == 4 * 12, counts
        assert counts["accepted_unexpected"] == 0, counts
        assert counts["peak_inflight"] == 12, counts        # 진짜 동시 폭주
        # 거절/드롭/연결오류로만 귀결(수용 없음). 동시성에서도 합이 보존돼야.
        assert (counts["rejected"] + counts["dropped"]
                + counts["errors"]) == counts["sent"], counts
        gc.collect()
        fd1 = redteam.count_fds()
        if fd0 >= 0:                                         # /proc·/dev/fd 가용 플랫폼
            assert fd1 <= fd0 + 8, (fd0, fd1)               # 동시 연결 후 fd 회수
        tok = ipc.read_token(ep)
        assert await redteam.authed_list_alive(ep, tok)     # 폭주 뒤 생존
    finally:
        await teardown(srv, task, ep)


async def test_redteam_slowloris_keeps_server_responsive():
    # half-open 연결을 잡아둔 채(불완전 프레임, 서버 read 매달림) 정상 클라 list 가
    # 굶지 않아야 한다(연결당 독립 태스크 = HOL 차단 없음). 잡아둔 뒤 서버 생존.
    srv, task, ep = await server_only()
    try:
        tok = ipc.read_token(ep)
        gc.collect()
        fd0 = redteam.count_fds()
        rep = await redteam.run_slowloris(ep, tok, n_conns=20, hold_sec=0.3,
                                          probes=5)
        assert rep["held"] >= 15, rep                       # 대부분 잡힘
        assert rep["probe_ok"] > 0, rep                     # 정상 클라 안 굶김
        assert rep["probe_fail"] == 0, rep
        assert rep["alive_after"] is True, rep              # 잡아둔 뒤 생존
        gc.collect()
        fd1 = redteam.count_fds()
        if fd0 >= 0:                                         # half-open 닫힌 뒤 fd 회수
            assert fd1 <= fd0 + 8, (fd0, fd1)
    finally:
        await teardown(srv, task, ep)


async def test_redteam_authed_fuzz_survives():
    # 인증 통과 후(post-auth) 악성 프레임(control/resize/input/scroll/cmd/unknown)에도
    # 서버 프로세스가 살아 있어야 한다(디스패치 가드 + R3 handle_control 비-str 가드).
    srv, task, ep = await server_only()
    try:
        tok = ipc.read_token(ep)
        rep = await redteam.run_authed_fuzz(ep, tok)
        assert rep["sent"] >= 10, rep                       # top 5 + loop 7
        assert rep["alive_after"] is True, rep              # 어떤 인증 악성에도 생존
    finally:
        await teardown(srv, task, ep)


async def test_redteam_resource_samplers_return_ints():
    assert isinstance(redteam.count_fds(), int)
    me = os.getpid()
    assert isinstance(redteam.pid_fds(me), int)
    assert isinstance(redteam.pid_rss_kb(me), int)
    # 자원 표본은 모든 지원 플랫폼에서 양수다 — Linux=/proc, macOS/BSD=/dev/fd,
    # Windows=프로세스 핸들 수(count_fds/pid_fds)·tasklist 작업셋(pid_rss_kb, §10 W3).
    # 열거 불가 환경만 -1.
    fds = redteam.count_fds()
    assert fds > 0 or fds == -1
    if ipc.IS_WINDOWS:                                  # §10 W3: -1 폴백이 아니라 실측
        assert redteam.count_fds() > 0
        assert redteam._win_handle_count(me) > 0
        assert redteam.pid_rss_kb(me) > 0


async def test_pid_listening_on_returns_int():
    # §10 W5: 디스커버리 헬퍼는 항상 int 를 돌려준다(못 찾으면 -1, 예외 누출 없음).
    p = redteam._pid_listening_on("127.0.0.1", 1)     # 1 번 포트는 거의 항상 비어있음
    assert isinstance(p, int)
    assert p == -1


async def test_redteam_attach_resource_verdict():
    # §10 W5: attach verdict 가 자원 표본(fd/핸들 증가)을 실제로 반영한다 — 종전엔
    # 표본을 떠도 verdict 가 무시했다. 하네스 서버(=이 프로세스)를 self PID 로 표본해
    # 비파괴 배터리 뒤 핸들 누수 0·무인가 수용 0·생존을 단언.
    srv, task, ep = await server_only()
    try:
        rep = await redteam.redteam_attach(ep, os.getpid(), rounds=12,
                                           destructive=False)
        assert rep["server_alive"] is True, rep
        assert rep["battery"]["accepted_unexpected"] == 0, rep
        assert rep["verdict_ok"] is True, rep
        if ipc.IS_WINDOWS:                             # 표본이 실측되는 플랫폼
            assert rep["resource_growth"] is not None, rep
            assert "fd_growth" in rep["resource_growth"], rep
    finally:
        await teardown(srv, task, ep)


async def test_win_external_pid_handle_sample():
    # §10 W5: 외부-PID 핸들/RSS 표본이 *다른 프로세스*에 대해 실측된다(--spawn 은 self).
    # 짧은 자식(sleeper)을 띄워 OpenProcess 핸들 수·tasklist RSS 가 양수인지 본다.
    if not ipc.IS_WINDOWS:
        return
    import subprocess
    import sys
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert redteam._win_handle_count(child.pid) > 0, "외부 PID 핸들 표본 실패"
        assert redteam._win_rss_kb(child.pid) > 0, "외부 PID RSS 표본 실패"
        assert redteam.pid_fds(child.pid) > 0, "pid_fds(외부) 실패"
    finally:
        child.terminate()
        child.wait(timeout=10)


async def test_attach_selftest_socket_path_within_af_unix_limit():
    """redteam --attach-selftest 는 격리 자식 서버를 PYTMUX_HOME 아래 AF_UNIX 소켓으로
    띄운다. macOS sun_path 한계(~104바이트)를 넘으면 자식이 bind 실패(rc=1)로 조기
    종료해 selftest 가 pytmux 결함이 아닌 harness 경로버그로 죽었다(코드검수 2026-07-10).
    수정: 짧은 base(/tmp)+짧은 prefix. 실제 소켓 경로가 한계 안인지 회귀로 못박는다.
    (POSIX 전용 — Windows 는 TCP 라 경로길이 무관.)"""
    if os.name == "nt":
        return
    import tempfile
    short_base = "/tmp" if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK) else None
    home = tempfile.mkdtemp(prefix="rt-st-", dir=short_base)
    try:
        # ipc.state_dir/state_base 가 PYTMUX_HOME 아래 소켓 경로를 어떻게 만드는지 그대로
        # 재현: <home>/state/default.sock (server_only 과 동일 레이아웃).
        sock = os.path.join(home, "state", "default.sock")
        assert len(sock) < 104, f"AF_UNIX 경로 초과({len(sock)}): {sock}"
    finally:
        import shutil
        shutil.rmtree(home, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 루프-생존 배터리 (blocking-on-loop 을 코드리뷰 산물이 아니라 런타임 단언으로)
# ─────────────────────────────────────────────────────────────────────────────
# 이 저장소는 blocking-on-loop 을 **네 번** 겪었다: claude-name-sync(S-3, 2026-07-10)·
# autorename _fg_command(P8)·mdir(LIV, 2026-07-16)·그리고 2026-07-17 검수에서 코어
# `_pane_cwd`(split/새탭마다 lsof 321ms) + ncd 재귀검색(1.44s×키스트로크, **릴레이라
# 상류까지 얼림**) + p4 describe(20s). 매번 **사람이 코드를 읽어야** 발견됐고, 고칠 땐
# 그 자리만 고쳐 형제가 남았다 — serverio 가 awaitable 을 await 하는 탈출구는 2026-07-16
# 부터 있었는데 mdir 만 채택했다. 아래 배터리는 그 패턴을 **실행으로** 잡는다: 훅의
# 실작업을 느리게 스텁하고 구동하면서 이벤트 루프가 멎는지 본다. 오프로드돼 있으면
# 작업이 400ms 여도 루프 gap 은 ~5ms, 인라인이면 gap≈400ms — 60배 차라 임계값이 둔감하다.

_SLOW = 0.4          # 스텁 작업 시간
_GAP_MAX = 0.15      # 허용 루프 정지(오프로드면 ~0.005s, 인라인이면 ~0.4s)


class _FakeSrvCwd:
    """훅이 cwd 추정에 쓰는 최소 서버(세션 읽기 부분만)."""
    def _resolve_start_cwd(self, sess, path):
        return tempfile.gettempdir()


async def test_loop_alive_ncd_find_offloaded():
    """[LOOP-1 회귀] ncd 재귀검색은 이벤트 루프를 막지 않는다.

    회귀 전: 훅이 dict 를 곧바로 반환해 BFS(최대 20000 디렉토리, 실측 1.44s/회)가
    루프에서 돌았다. 스피드서치는 **키스트로크마다** 요청하므로 'documents' 한 번
    타이핑에 누적 ~11초 전면 정지. 게다가 request_nc_find 는 `_REMOTE_RELAY_ACTIONS`
    라 하류 사용자의 타이핑이 **상류 서버 전체**를 얼렸다(신뢰경계를 넘는 DoS)."""
    import pytmuxlib.plugins.ncd.server as ncds
    from pytmuxlib.plugins.ncd import PLUGIN

    orig = ncds.nc_find_msg
    ncds.nc_find_msg = lambda *a, **k: (time.sleep(_SLOW),
                                        {"t": "nc_found", "target": None,
                                         "chain": []})[1]
    try:
        gap = await harness.max_loop_gap(
            lambda: PLUGIN.handle_server_request(
                _FakeSrvCwd(), None, "request_nc_find",
                {"query": "x", "root": tempfile.gettempdir()}))
        assert gap < _GAP_MAX, (
            f"ncd find 가 이벤트 루프를 {gap*1000:.0f}ms 막았다 — 훅이 executor 로 "
            f"오프로드하지 않고 루프에서 실행(LOOP-1 회귀)")
    finally:
        ncds.nc_find_msg = orig


async def test_loop_alive_ncd_list_offloaded():
    """[LOOP-1 회귀] ncd 목록(초기 진입·노드 펼치기)도 루프를 막지 않는다."""
    import pytmuxlib.plugins.ncd.server as ncds
    from pytmuxlib.plugins.ncd import PLUGIN

    orig = ncds.nc_list_fs
    ncds.nc_list_fs = lambda *a, **k: (time.sleep(_SLOW),
                                       {"t": "nc_list", "root": "/", "path": None,
                                        "chain": []})[1]
    try:
        for msg in ({}, {"path": tempfile.gettempdir()}):
            gap = await harness.max_loop_gap(
                lambda m=msg: PLUGIN.handle_server_request(
                    _FakeSrvCwd(), None, "request_nc_list", m))
            assert gap < _GAP_MAX, (
                f"ncd list{msg} 가 루프를 {gap*1000:.0f}ms 막았다(LOOP-1 회귀)")
    finally:
        ncds.nc_list_fs = orig


async def test_loop_alive_p4_hooks_offloaded():
    """[LOOP-2 회귀] p4 서브프로세스는 루프를 막지 않는다.

    회귀 전: describe 타임아웃이 20초라 느린/불통 P4PORT 하나면 **공격자 없이도**
    서버가 최대 20초 정지했다(list 는 8초×2)."""
    import importlib
    p4mod = importlib.import_module(
        "pytmuxlib.plugins.p4-show-submitted-changelists")
    p4srv = importlib.import_module(
        "pytmuxlib.plugins.p4-show-submitted-changelists.server")

    o_list, o_desc = p4srv.list_changes_msg, p4srv.describe_msg
    p4srv.list_changes_msg = lambda *a, **k: (time.sleep(_SLOW),
                                              {"t": "p4_changes", "rows": [],
                                               "err": None, "info": {}})[1]
    p4srv.describe_msg = lambda *a, **k: (time.sleep(_SLOW),
                                          {"t": "p4_describe", "change": "1",
                                           "text": "", "err": None})[1]
    try:
        for action, msg in (("request_p4_changes", {"count": 3}),
                            ("request_p4_describe", {"change": "1"})):
            gap = await harness.max_loop_gap(
                lambda a=action, m=msg: p4mod.PLUGIN.handle_server_request(
                    _FakeSrvCwd(), None, a, m))
            assert gap < _GAP_MAX, (
                f"p4 {action} 가 루프를 {gap*1000:.0f}ms 막았다(LOOP-2 회귀)")
    finally:
        p4srv.list_changes_msg, p4srv.describe_msg = o_list, o_desc


async def test_loop_alive_mdir_offloaded():
    """[LIV-1/2 회귀, 2026-07-16] mdir fs I/O 는 루프를 막지 않는다(이미 고쳐진 것을
    배터리에 편입해 되돌아가지 못하게 못박는다)."""
    import pytmuxlib.plugins.mdir.server as mds
    from pytmuxlib.plugins.mdir import PLUGIN

    orig = mds.mdir_list_fs
    mds.mdir_list_fs = lambda *a, **k: (time.sleep(_SLOW),
                                        {"t": "mdir_list", "path": "/",
                                         "entries": []})[1]
    try:
        gap = await harness.max_loop_gap(
            lambda: PLUGIN.handle_server_request(
                _FakeSrvCwd(), None, "request_mdir_list",
                {"path": tempfile.gettempdir()}))
        assert gap < _GAP_MAX, f"mdir list 가 루프를 {gap*1000:.0f}ms 막았다(LIV 회귀)"
    finally:
        mds.mdir_list_fs = orig


async def test_loop_alive_pane_cwd_core_paths():
    """[blocking-on-loop 4회차 회귀] 코어의 cwd 추정(split/새탭/popup/respawn 이 쓰는
    `_pane_cwd`)이 루프를 막지 않는다.

    회귀 전: macOS 폴백이 `lsof` 서브프로세스(실측 중앙값 **321ms**, 상한 2s)였고
    호출부가 전부 sync `def` 라 await 오프로드가 구조적으로 불가했다 → 분할/새 탭
    한 번마다 전 클라 동결. 수정=플랫폼 빠른 경로(libproc/proc/PEB, ~1µs).

    같은 헬퍼를 빌려 쓰는 플러그인(name-sync)엔 S-3 회귀가 있었는데 코어엔 없었다 —
    그 비대칭이 이 결함을 반년 살려뒀다."""
    from pytmuxlib.model import Pane
    srv, task, ep = await server_only()
    try:
        class _Pty:
            pid = os.getpid()
        pane = Pane(-1, -1, 80, 24)
        pane.pty = _Pty()

        async def _drive():
            # 새 탭/분할 20번이 부르는 만큼 그대로 부른다(sync 경로라 루프 위에서 돈다).
            for _ in range(20):
                srv._pane_cwd(pane)

        gap = await harness.max_loop_gap(_drive)
        # 20회 합계가 임계 안. 회귀(lsof)면 20×321ms=6.4초.
        assert gap < _GAP_MAX, (
            f"_pane_cwd 20회가 루프를 {gap*1000:.0f}ms 막았다 — 서브프로세스 폴백 "
            f"회귀(blocking-on-loop 4회차)")
    finally:
        await teardown(srv, task, ep)
