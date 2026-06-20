"""Tier 3 라이브 레드팀 코어 회귀 — docs/internal/SECURITY_REVIEW.md §9.3.

`scripts/redteam.py` 의 배터리·자원 표본 헬퍼를 **하네스 서버**에 붙여 가볍게 검증한다
(풀 spawn/attach CLI 는 로컬/Windows 수동 — 헤드리스 macOS CI wedge 회피). 단언:
  · 적대 배터리의 무인가/손상 프레임은 전부 거절(auth_failed)·드롭되고 **무인가 수용 0**,
  · 플러드 뒤에도 서버 생존 + **fd 누수 없음**,
  · 자원 표본 헬퍼가 정수를 돌려준다.
"""
import gc
import os
import sys

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
