"""§10-3⑤ — **서버가 예외를 로그로만 삼키는 것**을 전 스위트가 잡게 만든 가드의 자기검증.

서버는 데몬이라 stderr 가 /dev/null 이고 예외를 `_log_error` 로 `<state_base>.error.log`
에만 남긴다(의도된 설계 — 한 클라의 실패가 서버를 죽이지 않게). 그 대가로 **테스트가
초록불인데 서버가 매 프레임 터지는** 상태가 성립하고, 실제로 그런 결함(§9.1 화면델타
베이스라인 레이스)을 사람이 로그를 읽어야 발견했다. `harness.teardown` 이 이제 매
테스트 끝에 error.log 를 검사한다.

가드가 **가드 노릇을 하는지**(=조용한 실패를 정말 잡는지, 그리고 정상 테스트를 오탐하지
않는지)를 여기서 못박는다. 되돌리면 실패해야 하는 오라클:
  · 가드를 teardown 에서 빼면 → test_guard_catches_swallowed_exception 실패
  · 진단 로그(예외 없는 _log_error)를 예외로 세면 → test_diagnostic_log_is_not_an_error 실패
  · allow_errors 를 전면 허용으로 바꾸면 → test_allow_is_narrow 실패
"""
import os

import harness
from harness import running_server, server_only, teardown
from pytmuxlib import ipc


async def test_guard_catches_swallowed_exception():
    """서버가 예외를 삼켜 로그만 남기면 그 테스트는 **실패해야 한다**."""
    srv, task, sock = await server_only()
    try:
        try:
            raise RuntimeError("가짜 결함")
        except RuntimeError:
            srv._log_error("fake_defect")     # 실제 코드가 하는 그대로
        try:
            await teardown(srv, task, sock)
        except AssertionError as e:
            assert "조용한 실패" in str(e) and "fake_defect" in str(e)
        else:
            raise AssertionError("가드가 삼킨 예외를 놓쳤다")
    finally:
        harness.cleanup(srv, sock)
        task.cancel()


async def test_clean_run_does_not_trip_guard():
    """정상 테스트는 그대로 통과한다(오탐 0) — 오탐이 나면 전 스위트가 빨개진다."""
    async with running_server() as (srv, task, sock):
        assert srv is not None


async def test_diagnostic_log_is_not_an_error():
    """`_log_error(where, detail)` 는 예외 없이도 쓰인다(claude_format_unrecognized 가
    미인식 화면 tail 을 남기는 용도). 그때 트레이스백 자리는 `NoneType: None` 이라
    **예외가 아니다** — 이걸 세면 정상 진단이 스위트를 빨갛게 만든다."""
    async with running_server() as (srv, task, sock):
        srv._log_error("diag_only", "footer tail: ...")
        blocks = harness.server_error_blocks(sock)
        assert blocks == [], "진단 로그를 예외로 셌다: %r" % (blocks,)


async def test_allow_is_narrow():
    """allow_errors 는 **좁아야** 한다 — 허용 라벨과 무관한 새 예외는 여전히 잡힌다.

    실제로 밟은 함정: 처음엔 '블록 본문에 이 문자열이 있으면 허용' 이었는데
    `expected_thing` 허용이 `unexpected_thing` 까지 삼켰다(부분문자열). 지금은
    **where 라벨의 접두**로 매칭한다 — 이 테스트가 그 규칙을 못박는다."""
    srv, task, sock = await server_only()
    try:
        for where in ("expected_thing", "unexpected_thing"):
            try:
                raise RuntimeError("x")
            except RuntimeError:
                srv._log_error(where)
        try:
            await teardown(srv, task, sock, allow_errors=("expected_thing",))
        except AssertionError as e:
            assert "unexpected_thing" in str(e)
        else:
            raise AssertionError("허용 밖 예외를 놓쳤다")
    finally:
        harness.cleanup(srv, sock)
        task.cancel()


async def test_client_crash_log_is_also_watched():
    """클라 미처리 예외는 `<base>.client.crash.log` 로 간다 — 같은 가드가 본다."""
    srv, task, sock = await server_only()
    try:
        path = ipc.state_base(sock) + ".client.crash.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n==== 2026-07-25 00:00:00 [client] ====\n"
                    "Traceback (most recent call last):\n"
                    "  File \"x\", line 1\nRuntimeError: 클라 크래시\n")
        assert any("클라 크래시" in b for b in harness.server_error_blocks(sock))
    finally:
        try:
            os.unlink(ipc.state_base(sock) + ".client.crash.log")
        except OSError:
            pass
        harness.cleanup(srv, sock)
        task.cancel()
