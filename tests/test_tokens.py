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


async def test_step_ignores_idle_residue_no_duplicate_commits():
    """잔상 가드(2026-06-11 [대사] 관찰 버그): 응답 종료 후에도 `↑/↓ N tokens`
    텍스트가 화면에 남으면(완료 라인·스크롤 잔재) 비-busy 프레임마다 같은 peak 가
    재확정돼 하루 레코드의 83% 가 중복(한 응답 최대 117회)이었다. 확정 값 이하의
    비-busy running 은 잔상으로 무시하고, busy 재진입이 가드를 푼다."""
    st = tokens.new_state()
    tokens.step(st, 500, True)
    tokens.step(st, 43_100, True)
    assert tokens.step(st, 43_100, False) == 43_100   # 응답 종료 — 1회 확정
    # 잔상: 같은 값이 비-busy 로 계속 보여도 재확정 없음(예전엔 매 프레임 43,100).
    for _ in range(5):
        assert tokens.step(st, 43_100, False) == 0
    assert st["total"] == 43_100 and st["peak"] == 0
    # 잔상보다 작은 값(스크롤로 일부만 보임)도 무시.
    assert tokens.step(st, 19_300, False) == 0
    # 새 응답: busy 진입이 가드를 풀어 작은 running 부터 정상 누적.
    assert tokens.step(st, 400, True) == 0
    tokens.step(st, 2_000, True)
    assert tokens.step(st, None, False) == 2_000
    assert st["total"] == 45_100
    # busy 미감지인데 mark 를 넘게 커지는 드문 경우는 통과(언더카운트 방지 failsafe).
    st2 = tokens.new_state()
    tokens.step(st2, 1_000, True)
    assert tokens.step(st2, 1_000, False) == 1_000
    assert tokens.step(st2, 3_000, False) == 3_000    # mark(1k) 초과 → 새 활동으로 인정
    assert st2["total"] == 4_000


async def test_reset_and_fmt():
    st = tokens.new_state()
    tokens.step(st, 1000, True)
    tokens.step(st, None, False)
    assert st["total"] == 1000
    tokens.reset(st)
    assert st == {"peak": 0, "total": 0, "idle_mark": None}
    assert tokens.fmt(1_234_567) == "1.2M"
    assert tokens.fmt(45_200) == "45.2k"
    assert tokens.fmt(1_000) == "1k"
    assert tokens.fmt(1_000_000) == "1M"
    assert tokens.fmt(800) == "800"
