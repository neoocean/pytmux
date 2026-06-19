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


async def test_redteam_resource_samplers_return_ints():
    assert isinstance(redteam.count_fds(), int)
    me = os.getpid()
    assert isinstance(redteam.pid_fds(me), int)
    assert isinstance(redteam.pid_rss_kb(me), int)
    # 이 플랫폼(개발 박스 macOS=/dev/fd, Linux=/proc)에선 self fd 표본이 양수.
    assert redteam.count_fds() > 0
