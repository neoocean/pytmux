"""폴링·스톨 워치독 헬퍼 자체의 오라클(harness `wait_for`/`wait_until`(+settled)).

이 헬퍼들은 **전 스위트의 대기 규약**이라 조용히 어긋나면 플레이크가 늘거나 스톨
진단이 사라진다. 종전 커버리지는 `test_clientutil.test_wait_until_settled_stall_vs_
progress_vs_met`(pilot 판 settled 3거동) **하나뿐**이었고, `wait_until` 기본 판·새로
생긴 pilot 없는 쌍(`wait_for`/`wait_for_settled`)·불변 카운터 리셋은 비어 있었다.
네 헬퍼가 `_poll` 단일 구현을 공유하게 됐으므로(pilot 판 = `pilot.pause`, 서버 판 =
`asyncio.sleep`) 여기서 코어 계약을 못박는다:

  ① 조건이 참이면 즉시 성공 · ② 시간 초과면 실패 · ③ cond 예외는 삼킨다(폴링 중
  아직 준비 안 된 객체 접근이 흔하다) · ④ **정착-오답 스톨**은 timeout 을 다 쓰지 않고
  진단과 함께 조기 반환 · ⑤ 상태가 계속 변하면(진행 중) timeout 까지 인내.

④⑤ 를 가르는 게 스톨 워치독의 전부다 — 둘을 뒤집으면 "느린 CI 에서 조기 실패"(⑤ 붕괴)
또는 "멈췄는데 끝까지 기다림"(④ 붕괴)이 된다.
"""
import asyncio
import time

import harness


class _FakePilot:
    """Textual 없이 pilot 판을 검증한다(pause = 이벤트 루프 양보)."""

    def __init__(self):
        self.pauses = 0

    async def pause(self, step=0.0):
        self.pauses += 1
        await asyncio.sleep(step)


async def test_wait_for_true_immediately_does_not_sleep():
    t0 = time.monotonic()
    assert await harness.wait_for(lambda: True, timeout=5.0) is True
    assert time.monotonic() - t0 < 0.2, "이미 참인 조건에 대기 비용을 물면 안 된다"


async def test_wait_for_becomes_true_later():
    flag = []
    loop = asyncio.get_event_loop()
    loop.call_later(0.15, lambda: flag.append(1))
    assert await harness.wait_for(lambda: bool(flag), timeout=3.0) is True


async def test_wait_for_times_out_false():
    t0 = time.monotonic()
    assert await harness.wait_for(lambda: False, timeout=0.3, step=0.02) is False
    assert time.monotonic() - t0 >= 0.3, "timeout 전에 포기하면 느린 러너에서 플레이크"


async def test_cond_exceptions_are_swallowed_until_ready():
    """폴링 중 아직 없는 속성을 만지는 건 정상 — 예외로 죽으면 규약이 못 쓰인다."""
    box = {}

    def cond():
        return box["k"] > 0          # 처음엔 KeyError

    asyncio.get_event_loop().call_later(0.1, lambda: box.__setitem__("k", 1))
    assert await harness.wait_for(cond, timeout=3.0) is True


async def test_settled_reports_stall_early_with_diagnosis():
    """스냅샷이 연속 불변인데 조건 미충족 = 수렴-오답 → **timeout 전에** 진단 반환."""
    t0 = time.monotonic()
    ok, diag = await harness.wait_for_settled(
        lambda: False, lambda: "화면 그대로", timeout=5.0, step=0.01, settle=4)
    elapsed = time.monotonic() - t0
    assert ok is False
    assert diag and "화면 그대로" in diag, diag
    assert elapsed < 1.0, f"스톨인데 timeout(5s)까지 기다렸다: {elapsed:.2f}s"


async def test_settled_is_patient_while_state_keeps_changing():
    """진행 중(스냅샷 변화)이면 인내해야 한다 — 안 그러면 느린 CI 를 스톨로 오진한다."""
    n = [0]

    def snapshot():
        n[0] += 1
        return n[0]              # 매 폴에서 달라진다 = 계속 진행 중

    t0 = time.monotonic()
    ok, diag = await harness.wait_for_settled(
        lambda: False, snapshot, timeout=0.4, step=0.01, settle=3)
    elapsed = time.monotonic() - t0
    assert ok is False and elapsed >= 0.4, f"조기 포기: {elapsed:.2f}s"


async def test_settled_counter_resets_on_every_change():
    """불변 카운터는 **변할 때마다 0 으로 리셋**돼야 한다.

    실제 렌더는 '두 폴 그대로 → 한 프레임 갱신'을 반복한다(디바운스·부분 갱신). 리셋이
    없으면 이 정상 진행이 settle 회를 누적해 **스톨로 오진**된다 — 위 '계속 변함' 테스트는
    매 폴 달라져서 이 결함을 못 잡았다(실측: 리셋 제거 뮤테이션 무증상).
    """
    n, calls = [0], [0]

    def snapshot():
        calls[0] += 1
        if calls[0] % 3 == 0:        # 두 번 그대로, 세 번째에 진행
            n[0] += 1
        return n[0]

    t0 = time.monotonic()
    ok, diag = await harness.wait_for_settled(
        lambda: False, snapshot, timeout=0.5, step=0.01, settle=3)
    elapsed = time.monotonic() - t0
    assert ok is False
    assert elapsed >= 0.5, f"진행 중인데 스톨로 오진했다: {elapsed:.2f}s ({diag})"


async def test_settled_success_returns_no_diagnosis():
    ok, diag = await harness.wait_for_settled(lambda: True, lambda: 0, timeout=1.0)
    assert (ok, diag) == (True, None)


async def test_pilot_variants_share_the_same_core():
    """pilot 판은 `pilot.pause` 로 프레임을 돌린다(렌더 진행) — 그 배선을 확인한다."""
    p = _FakePilot()
    assert await harness.wait_until(p, lambda: False, timeout=0.15, step=0.01) is False
    assert p.pauses > 0, "pilot.pause 로 폴링하지 않으면 Textual 렌더가 진행되지 않는다"
    p2 = _FakePilot()
    ok, diag = await harness.wait_until_settled(
        p2, lambda: False, lambda: "고정", timeout=5.0, step=0.01, settle=3)
    assert (ok, "고정" in (diag or "")) == (False, True), (ok, diag)
    assert p2.pauses >= 3
