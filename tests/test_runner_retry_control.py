"""pytmux-430 의 **대조군** — 결정론적으로 넘어지되 프로세스 전역을 데우는 시험.

# 왜 이 파일이 따로 있나

[[pytmux-430]] 의 관문은 *"결정론적으로 실패하되 프로세스 전역을 데우는 시험 하나를
두고, 그것이 `0 failed` 로 끝나지 **않는지**를 잰다"* 이다. 그 잣대는 **러너를 실제로
돌려야** 잴 수 있다 — 재시도는 `tests/run.py` 의 루프 안에서만 일어난다.

그래서 여기 있는 것은 「제품의 시험」이 아니라 **러너에게 먹일 표본**이다. 평소에는
`skip` 이고, `PYTMUX_TEST_RETRY_CONTROL=1` 일 때만 실제로 넘어진다. 그것을 켜서
서브프로세스로 돌리는 자는 `tests/test_runner_retry.py` 다.

⛔ **조용한 `return` 이 아니라 `skip("사유")` 다**(CLAUDE.md §명시 SKIP) — 안 도는 것이
   요약에 사유와 함께 남아야 커버리지 갭이 안 숨는다.

# 이 표본이 «대조군» 인 이유

첫 시도만 넘어지고 그다음부터는 통과한다 — **프로세스 전역**(`_WARMED`)이 시도 사이에
이어지기 때문이다. 이것이 실제로 물렸던 모양 그대로다(2026-09-01 · pytmux-382 의 회귀
오라클 · `textual.strip.Strip.blank` 의 `lru_cache` 가 클래스에 붙어 프로세스 수명을
살았다). 그래서 이 표본은 **규칙이 바뀌면 답이 뒤집힌다**:

| 러너의 규칙 | 이 표본의 결말 |
|---|---|
| 모든 실패를 재시도(종전) | `PASS (FLAKY)` · `0 failed` — **덮인다** |
| `AssertionError` 는 재시도 안 함(지금) | `FAIL` · `1 failed` — 잡힌다 |
"""
import os

from run import skip

# ★ 프로세스 전역 — 시도들이 서로 **독립이 아니라는 것**이 이 대조군의 전부다.
_WARMED: list[int] = []


async def test_a_deterministic_failure_that_warms_a_process_global():
    if os.environ.get("PYTMUX_TEST_RETRY_CONTROL") != "1":
        skip("pytmux-430 러너 대조군 — test_runner_retry.py 가 켜서 돌린다")
    first = not _WARMED
    _WARMED.append(len(_WARMED))
    assert not first, (
        "pytmux-430 대조군: 첫 시도는 반드시 넘어진다. 이 줄이 «재시도로 통과»가 되면 "
        "러너가 결정론적 실패를 초록으로 덮고 있는 것이다")
