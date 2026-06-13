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


# ── §3.2/§3.7: 실 캡처에서 뽑은 회귀 픽스처 ───────────────────────────────────
# 그간 step() 검증은 **합성** 시퀀스뿐이라(docs IMPROVEMENT §3.2/§3.7 "실 캡처 fixture
# 확보 시 교체"), 실제 Claude busy footer 진행에 대한 회귀 가드가 없었다. 아래 시퀀스는
# 실 캡처(captures/woojinkim/pane-1.log)를 **충실히** 재생해 뽑은 (running, busy) 프레임이다.
#
# 충실 재생의 핵심(비자명 — 2026-06-13 조사): Claude 의 토큰 footer 는 **동기화 출력**
# (DECSET 2026: `ESC[?2026h … ESC[?2026l`) 한 블록 안에서 ↑/↓ 토큰을 함께 갱신한다.
# 캡처를 고정 바이트 청크로 잘라 **동기 블록 중간**에 화면을 읽으면 ↓만 쓰이고 ↑ 가 아직
# 안 쓰인 부분 프레임을 잡아 running 이 "394↔1394" 식으로 **유령 진동**하고, 그러면
# step() 의 급감 규칙이 한 응답을 여러 번 false-split 한다. 서버 flush 는 완성 프레임을
# 보므로 그런 일이 없다 → 픽스처도 **동기-출력 종료(`ESC[?2026l`) 경계**에서만 샘플해
# 만든 것이다(서버가 보는 원자 갱신과 동치). 충실 샘플링에선 running 이 매끈히 단조
# 증가하고 급감 규칙은 합리적으로 동작한다.
#
# 한계(정직성): 캡처에는 Claude 의 **응답별 실제 청구 토큰(정답 경계)** 신호가 없어,
# "응답 4개로 나눈 게 옳은지"는 자동 채점 불가다. 그래서 이 픽스처는 **불변식**(총합 =
# 확정합, 중복확정 0, 단조 누적)과 **현재 거동 고정**(회귀 감지)만 단언한다 — 경계
# 휴리스틱의 '정확도 증명'은 ground-truth 부재로 범위 밖. (이 누계는 S6 이후 /usage
# 실측이 권위값이 된 보조 ~Σ 추정치라 정밀화 가치도 낮다.)
_PANE1_REAL_FRAMES = [
    (None, False), (None, True), (13, True), (25, True), (38, True), (50, True),
    (63, True), (75, True), (82, True), (89, True), (95, True), (105, True),
    (None, True), (524, True), (525, True), (529, True), (1250, True),
    (1971, True), (1250, True), (1971, True), (1442, True), (2442, True),
    (3442, True), (2442, True), (1442, True), (2642, True), (3121, True),
    (4321, True), (1200, True), (2400, True), (1300, True), (2600, True),
    (4100, True), (2600, True), (4100, True), (2600, True), (4100, True),
    (5900, True), (6000, True), (6100, True), (2000, True), (None, False),
]


async def test_step_real_capture_pane1_regression():
    """실 캡처 충실 재생 시퀀스(pane-1.log)에 대한 step() 회귀 + 불변식 고정."""
    st = tokens.new_state()
    commits = []
    for running, busy in _PANE1_REAL_FRAMES:
        c = tokens.step(st, running, busy)
        if c:
            commits.append(c)
    fin = tokens.step(st, None, False)
    if fin:
        commits.append(fin)
    # ① 현재 거동 고정(이 시퀀스의 응답 분할 — 변경 시 실 데이터 영향이 드러난다).
    assert commits == [3442, 4321, 6100, 2000], commits
    # ② 불변식: 총합 = 확정합(중복확정 0 — 잔상 가드가 부풀림 차단), 각 확정 양수.
    assert st["total"] == sum(commits), (st["total"], commits)
    assert all(c > 0 for c in commits)
    assert st["peak"] == 0   # 종료 후 미확정 잔여 없음


async def test_step_invariants_no_inflation_on_residue_frames():
    """실 캡처 꼬리에서 흔한 '확정 후 같은/작은 running 잔상 프레임'이 누계를
    부풀리지 않는다(불변식). idle_mark 가드의 실 데이터 회귀 가드."""
    st = tokens.new_state()
    # 한 응답(busy) → 종료 → 잔상 프레임 다수(완료 라인이 화면에 남음).
    for r in (500, 1500, 6100):
        tokens.step(st, r, True)
    assert tokens.step(st, 6100, False) == 6100      # 확정 1회
    before = st["total"]
    for r in (6100, 6100, 4100, 6100, None, 6100):   # 잔상/스크롤 잔재 — 재확정 없어야
        tokens.step(st, r, False)
    assert st["total"] == before, "잔상 프레임이 누계를 부풀리면 안 됨"


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
