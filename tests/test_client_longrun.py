"""pytmux-382 — 「수 일 띄워 두면 느려진다」를 **횟수**로 재는 회귀 오라클.

# 왜 이 파일이 있나

제보는 *"tui를 장시간(수 일) 띄워놓으면 응답이 점점 더 느려집니다"* 였다. 그 잣대로는
아무도 회귀를 못 잡는다 — 사람이 며칠을 기다릴 수 없고, 기다린다 해도 그때의 값을 옛
값과 견줄 자리가 없다.

2026-08-25 계측(문서 `pytmux/pytmux-382-measured-2026-08-25`)이 그 잣대를 바꿨다:
사용자가 하루에 수백 번 하는 일(**모달 판을 열고 닫기**)을 횟수로 압축하면 같은 곡선이
분 단위에 나온다. 그리고 느려지는 자리는 **그리는 시간이 아니라 GC 정지 시간**이고, 그
비용은 **산 객체의 개수**에 붙는다(파이썬 세대 GC 는 산 것을 전부 훑는다). 클라는 단일
스레드 asyncio 루프라 그 정지가 곧 「키가 안 먹는 시간」이다.

⇒ 그래서 여기서 재는 것은 시간이 아니라 **객체 수의 «모양»** 이다.

# 총량이 아니라 «준비운동 뒤의 기울기» 를 잰다

판을 처음 여는 회차는 **반드시** 많이 늘어난다 — Textual 이 그 화면의 스타일·레이아웃·
`Strip` 캐시를 그때 짓는다. 그것은 누수가 아니라 준비운동이고, 상자마다·판마다 크기가
다르다. 그래서 **총량**에 상한을 두면(「객체 N개를 넘지 마라」) 상자를 옮기는 순간 거짓이
된다. 재는 것은 준비운동이 끝난 **뒤**의 사이클당 증가다.

⛔ 그 증가를 «준비운동 창 대비 비»로 재려던 것이 처음 설계였고, **그것이 틀렸다** —
아래 `MAX_GROWTH_PER_CYCLE` 주석에 그 실패가 적혀 있다. 요지는 이 스위트가 전 모듈을
한 프로세스에서 돌리므로 **분모(준비운동)가 앞서 돈 시험에 이미 먹혔을 수 있다**는 것이다.
절대 기울기는 그 사정을 안 탄다.

⚠ **시간을 단언하지 않는다.** `gc.collect()` 밀리초는 이 저장소의 러너가 부하를 탈 때
몇 배로 흔들린다 — 실측으로 같은 판의 같은 지점이 혼자 돌 때 47.7 ms, 다른 계측과 함께
돌 때 62.6 ms 였다(2026-09-01). 시간은 객체 수의 종속 변수라는 것이 위 계측의 결론이므로,
재는 것은 **개수** 하나면 족하다.
"""
import gc

from harness import make_app, server_only, teardown, wait_mounted

# 준비운동 회수 / 측정 회수. 실측(macOS · Python 3.13 · Textual 8.2.5)으로 팔레트의
# 준비운동은 **25회 안쪽에 끝난다**(52k → 126k 뒤 400회까지 평탄).
WARMUP, WINDOW = 30, 25

# 준비운동 뒤 **사이클당** 늘어도 되는 객체 수의 상한.
#
#   실측 — 포화 구간 0.2/회(150회에 +167) · 선형 구간 약 1,080/회(25회에 +27,000).
#   그 둘은 **1000배** 떨어져 있어 상한을 아무 데나 놔도 갈린다. 40 은 잡음의 200배이고
#   결함의 1/27 이다.
#
# ⛔ **비율(준비운동 창 대비)로 재지 않는다.** 그렇게 지었더니 시험이 «프로세스 안에서
#    두 번 돌면 다르게» 굴었다 — `Strip.blank` 은 `lru_cache` 라 **클래스에 붙어 프로세스
#    수명을 산다**. 두 번째 회차는 준비운동이 이미 끝나 있어 분모가 0 에 가까워지고,
#    실제로 러너의 재시도가 **결정론적 실패를 초록으로 덮었다**(시도1·2 실패 → 시도3 통과).
#    이 스위트는 전 모듈이 한 프로세스라 그 함정이 상시다.
MAX_GROWTH_PER_CYCLE = 40


async def _live_objects():
    gc.collect()
    return len(gc.get_objects())


async def _cycle(pilot):
    """사용자가 하루에 수백 번 하는 일 — 팔레트를 열고 닫는다."""
    await pilot.press("escape", "question_mark")
    opened = await wait_mounted(pilot, "CommandListScreen")
    # ⛔ 「열렸다」를 못박는다 — `wait_mounted` 는 시한을 넘겨도 예외를 안 던지므로
    #    (그 규약은 harness 의 다른 wait_* 와 같다) 판이 안 열린 채로 수십 번 도는
    #    계측이 만들어질 수 있다. 실제로 이 시험을 지을 때 그 함정을 한 번 밟았다.
    name = opened.__class__.__name__
    assert name == "CommandListScreen", f"팔레트가 안 열렸다 — 맨 위가 {name} 다"
    await pilot.press("escape")
    await pilot.pause()


async def test_reopening_a_modal_screen_saturates_instead_of_accumulating():
    """★ 판을 여닫는 것이 **준비운동 뒤에는 안 자란다**(pytmux-382).

    자라면 GC 가 훑을 것이 늘고, 그 비용이 단일 스레드 루프의 **정지**로 나온다 —
    그것이 제보가 말한 「점점 느려진다」의 기제다.

    ⚠ 이 시험이 **못 재는 것** — 초록을 넓게 읽지 말 것:

    - **다른 판은 곡선이 다르다.** 같은 잣대로 잰 실측(2026-09-01 · macOS · Python 3.13 ·
      Textual 8.2.5)에서 `PromptScreen`(`esc :`)은 **200사이클까지** 자란 뒤에야 눕는다
      (52k → 193k). 팔레트는 **25사이클**에 눕는다(52k → 126k). 그래서 여기 창(25×2)은
      팔레트에만 맞는 크기이고, 이 시험의 초록은 「모든 판이 안 자란다」가 **아니다**.
      판마다 창을 새로 재야 하고, 그 값이 비싸서(400사이클 ≈ 10분) 스위트에는 팔레트
      하나만 두었다.
    - **서버 쪽 누적**(제보의 ①은 GUI 를 같이 붙여 갈라야 한다).
    """
    srv, task, sock = await server_only()
    app = make_app(sock)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            for _ in range(WARMUP):
                await _cycle(pilot)
            warm = await _live_objects()
            for _ in range(WINDOW):
                await _cycle(pilot)
            after = await _live_objects()
    finally:
        await teardown(srv, task, sock)

    per_cycle = (after - warm) / WINDOW
    assert per_cycle <= MAX_GROWTH_PER_CYCLE, (
        f"준비운동 {WARMUP}회 뒤에도 판을 여닫을수록 산 객체가 는다 — 포화가 아니라 "
        f"누적이다. 다음 {WINDOW}회에 +{after - warm}개 = 사이클당 {per_cycle:.0f}개 "
        f"(상한 {MAX_GROWTH_PER_CYCLE}). 파이썬 GC 는 산 객체를 전부 훑으므로 이 증가는 "
        f"곧 단일 스레드 루프의 «정지»가 되고, 며칠이면 사람 눈에 보인다 "
        f"(pytmux-382 · 문서 pytmux/pytmux-382-measured-2026-08-25)")


async def test_the_strip_cache_count_is_a_multiple_of_the_strip_count():
    """`FIFOCache` 가 «따로» 새는 것이 아니라 `Strip` 하나에 **일곱**씩 달려 나온다.

    2026-08-25 계측은 자라는 것의 이름을 `FIFOCache` +546/사이클 · `Strip` +78/사이클로
    적고 *"FIFOCache 수는 Strip 수의 종속변수다"* 라고만 판정했다. 그 배수가 몇이고
    어디서 정해지는지는 안 적혀 있었는데, 그것이 조사의 방향을 가른다 —
    **캐시를 줄이는 길은 없고 Strip 을 줄이는 길만 있다**는 뜻이기 때문이다.

    배수는 `textual.strip.Strip.__init__` 이 **무조건** 짓는 캐시의 수다(비어 있어도
    짓는다 — 그래서 계측이 *"33,632개 중 29,551개가 len()==0"* 을 봤다). 그 수가 상류
    판올림으로 바뀌면 위 문서의 산수(546/78 = 7)가 조용히 틀리므로 여기서 센다.
    """
    import textual.strip
    from textual.cache import FIFOCache

    # ⛔ **이름으로 세지 않는다.** `__slots__` 에는 `_render_cache` 도 있는데 그것은
    #    캐시가 아니라 `str | None` 이다(이 시험을 처음 지을 때 그 이름에 속아 8을 셌다).
    #    빈 캐시가 GC 를 무겁게 한다는 것이 이 부류의 기제이므로, 세야 하는 것은
    #    **정말로 지어진 FIFOCache 객체**다.
    strip = textual.strip.Strip([])
    eager = [n for n in textual.strip.Strip.__slots__
             if isinstance(getattr(strip, n, None), FIFOCache)]
    assert len(eager) == 7, (
        f"`Strip()` 하나가 짓는 FIFOCache 가 일곱이 아니라 {len(eager)}개다({eager}) — "
        f"pytmux-382 계측의 «FIFOCache = 7 × Strip» 산수가 낡았다. 줄었다면(게을러졌다면) "
        f"이 부류의 비용 자체가 사라진 것이니, 그 문서의 「자라는 것의 이름」 표를 다시 읽을 것")
