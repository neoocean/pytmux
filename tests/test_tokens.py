"""토큰 누적(pytmuxlib/tokens.py) 단위 테스트: running 파서·응답별 peak 합산·표기."""
from pytmuxlib import tokens


async def test_parse_running_tokens():
    assert tokens.parse_running_tokens("✽ Crunching… (12s · ↓ 1.9k tokens)") == 1900
    assert tokens.parse_running_tokens("↑ 419 tokens") == 419
    assert tokens.parse_running_tokens("(2m · ↓ 2M tokens)") == 2_000_000
    # 화살표 없는 누계 언급은 running(스트리밍)이 아니므로 제외
    assert tokens.parse_running_tokens("used 45k tokens") is None
    assert tokens.parse_running_tokens("a normal line") is None


async def test_step_commits_peak_on_response_end():
    st = tokens.new_state()
    # 응답1: 0.5k → 1.9k 스트리밍, 그 후 idle(busy 종료)
    assert tokens.step(st, 500, True) == 0
    assert tokens.step(st, 1900, True) == 0
    assert tokens.step(st, None, False) == 1900   # 응답 종료 → peak 확정
    assert st["total"] == 1900
    # 응답2: 2.5k 까지 → idle
    tokens.step(st, 300, True)
    tokens.step(st, 2500, True)
    assert tokens.step(st, None, False) == 2500
    assert st["total"] == 4400


async def test_step_handles_back_to_back_without_idle_gap():
    # idle 갭 없이 running 이 급감하면 새 응답 시작 — 직전 peak 를 확정한다.
    st = tokens.new_state()
    tokens.step(st, 1000, True)
    tokens.step(st, 2000, True)
    assert tokens.step(st, 100, True) == 2000    # 급감 → 이전 peak 확정
    tokens.step(st, 800, True)
    assert tokens.step(st, None, False) == 800
    assert st["total"] == 2800


async def test_reset_and_fmt():
    st = tokens.new_state()
    tokens.step(st, 1000, True)
    tokens.step(st, None, False)
    assert st["total"] == 1000
    tokens.reset(st)
    assert st == {"peak": 0, "total": 0}
    assert tokens.fmt(1_234_567) == "1.2M"
    assert tokens.fmt(45_200) == "45.2k"
    assert tokens.fmt(1_000) == "1k"
    assert tokens.fmt(1_000_000) == "1M"
    assert tokens.fmt(800) == "800"
