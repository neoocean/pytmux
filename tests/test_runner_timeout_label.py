"""러너가 **누가 시간을 쟀나**를 가려 적는지 잰다(pytmux-452).

# 무엇이 잘못돼 있었나

러너의 `TIMEOUT` 은 「러너가 자기 상한(`TEST_TIMEOUT`)에서 끊었다」는 뜻이어야 하는데,
**시험이 스스로 낸 `TimeoutError`** 도 같은 자리에 걸렸다. `asyncio.wait_for` 가 자기
마감과 안쪽이 던진 것을 **같은 예외**로 내기 때문이다(3.11+ 에서 `asyncio.TimeoutError`
는 내장 `TimeoutError` 의 별칭이다).

그 결과 셋이 한꺼번에 거짓이 됐다 — ⑴ 지속시간(4초를 「90.0s 초과」로) ⑵ 재시도 예산
(일반 재시도가 아니라 **타임아웃** 재시도를 쓴다) ⑶ ☠ **트레이스백이 덮인다**
(`last_tb` 가 `"TIMEOUT after …"` 한 줄로 갈린다).

⑶ 이 가장 비쌌다: `test_remote.test_remote_no_mixing_guards` 하나로 실패 이슈가 셋
(`pytmux-165`·`pytmux-220`·`pytmux-348`) 섰고 **전부 `invalid` 로 닫혔다** — 줄이
「데드락 의심」이라고 적으니 데드락을 찾았고, 없으니 무효가 됐다. 진짜 원인
([[pytmux-453]])은 그 줄에 안 적혀 있었다.

# 여기서 재는 것

마감이 **실제로 지났는지**만이 둘을 가른다(`asyncio.timeout().expired()`). 그래서 두 표본을
**한 쌍으로** 둔다 — 하나는 스스로 던지고 하나는 진짜로 매달린다. 둘이 **다르게** 적혀야
이 오라클이 통과한다: 한쪽만 재면 「전부 hang 으로 적기」도 「전부 실패로 적기」도 지나간다.
"""
import asyncio

import run


# ⛔ 표본의 「멎음」은 **고정 sleep 이 아니라 영영 안 오는 이벤트**로 만든다
#    (`tests/test_wait_convention.py` 의 래칫 · 신규 모듈 상한은 0). 뜻도 이쪽이 낫다 —
#    재려는 것은 「N초 뒤」가 아니라 「끝나지 않음」이다.
async def _forever():
    await asyncio.Event().wait()               # 아무도 set 하지 않는다


async def _raises_its_own_timeout():
    """시험이 **스스로** TimeoutError 를 낸다 — 러너 상한과 무관하다."""
    await asyncio.wait_for(_forever(), 0.05)    # ← 여기서 TimeoutError


async def _really_hangs():
    """러너 상한에 실제로 걸린다."""
    await _forever()


async def _run(fn, timeout):
    """러너의 그 경로를 그대로 지난다 — `_run_with_timeout` 이 판정하는 자리다."""
    old = run.TEST_TIMEOUT
    run.TEST_TIMEOUT = timeout
    try:
        await run._run_with_timeout(fn)
        return None
    except BaseException as e:                  # noqa: BLE001 — 종류가 곧 답이다
        return e
    finally:
        run.TEST_TIMEOUT = old


async def test_a_timeout_the_test_raised_is_not_called_a_hang():
    """★ [[pytmux-452]] 의 관문 — 스스로 낸 것은 `SuiteTimeout` 이 **아니다**.

    이것이 `SuiteTimeout` 이면 루프가 `hung=True` 로 세고 트레이스백을 덮는다.
    """
    e = await _run(_raises_its_own_timeout, 30)
    assert e is not None, "표본이 안 넘어졌다 — 오라클이 공허하다"
    assert not isinstance(e, run.SuiteTimeout), (
        f"시험이 스스로 낸 TimeoutError 를 러너 상한으로 적었다: {e!r}")
    assert isinstance(e, TimeoutError), f"예상 밖의 예외: {e!r}"


async def test_the_runners_own_deadline_still_counts_as_a_hang():
    """⛔ 대조군 — 진짜 상한은 종전 그대로 `SuiteTimeout` 이다.

    이 짝이 없으면 「전부 평범한 실패로 적기」로도 위 시험이 통과한다. 그러면 부하 스톨
    재시도(`TEST_TIMEOUT_RETRIES`)가 조용히 죽는다.
    """
    e = await _run(_really_hangs, 0.2)
    assert isinstance(e, run.SuiteTimeout), (
        f"러너 자기 상한이 hang 으로 안 적혔다: {e!r}")


async def test_the_traceback_of_a_self_raised_timeout_survives():
    """☠ 가장 비쌌던 것 — 진짜 원인의 스택이 남는다.

    종전에는 `last_tb` 가 `"TIMEOUT after 90.0s"` 한 줄로 갈려 **어느 줄에서 무엇을
    기다리다 넘어졌는지가 사라졌다**. 그것이 이슈 셋을 `invalid` 로 만든 그 자리다.
    """
    import traceback
    e = await _run(_raises_its_own_timeout, 30)
    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    assert "_raises_its_own_timeout" in tb, (
        f"진짜 원인의 스택이 트레이스백에 없다:\n{tb}")
