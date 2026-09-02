"""러너의 **재시도 재판정**을 잰다(pytmux-430).

# 무엇이 잘못돼 있었나

`tests/run.py` 의 재시도 주석은 오래 이렇게 적고 있었다 — *"일시적 플레이크는 재시도로
통과하고, **진짜 실패는 모든 시도에서 실패해 그대로 잡힌다**"*. **뒷문장이 참이 아니었다.**
이 스위트는 전 모듈이 **한 프로세스**에서 돌아 시도들이 서로 독립이 아니다: 앞 시도가
프로세스 전역(캐시·레지스트리·모듈 전역)을 데워 놓으면 다음 시도의 조건이 달라지고,
**결정론적 실패가 초록으로 덮인다.**

실측(2026-09-01 · pytmux-382 의 회귀 오라클을 짓다가): 상한의 **26배**로 두 번 넘어진
시험이 시도 3 에서 통과해 `0 failed` 로 회계됐다. `textual.strip.Strip.blank` 이
`lru_cache` 라 **클래스에 붙어 프로세스 수명을 살고**, 앞 두 시도가 그것을 포화시켰기
때문이다 — 같은 코드 · 같은 시험 · 다른 답.

# 무엇으로 고쳤나(사용자 결정 2026-09-02)

재시도를 없애지 **않았다** — 부하 스톨 복구용 재시도는 값이 있고 그 값은 실측으로 서
있다. 대신 **재시도로만 통과한 건(FLAKY)은 깨끗한 새 프로세스에서 한 번 더 재서**
판정한다. 거기서도 실패하면 그것은 플레이크가 아니라 실패다. 비용은 flaky 경로에만 든다.

# 이 파일이 재는 것

⛔ 이 부류의 증상은 **초록불**이라 아무도 안 들여다본다 — 그래서 함수를 겨누지 않고
**러너를 실제로 돌려** 화면을 읽는다(CLAUDE.md §호출부까지 단언 — 이 저장소의 상습
실패). 표본은 `test_runner_retry_control.py` 이고, 그것을 켜서 서브프로세스로 돌리는
자가 여기다.

| 러너의 규칙 | 대조군의 결말 |
|---|---|
| 재시도만(종전 · `PYTMUX_TEST_ADJUDICATE=off`) | `PASS (FLAKY)` · `0 failed` — **덮인다** |
| 재시도 + 재판정(지금) | `FAIL` · `1 failed` — 잡힌다 |

그 **뒤집힘**이 곧 「덮였다」의 증거다 — 아래 두 시험이 한 쌍인 이유가 그것이다.
"""
import os
import subprocess
import sys

import run

HERE = os.path.dirname(os.path.abspath(__file__))

# 재판정이 뒤집었을 때 러너가 화면에 남기는 말(run.py 의 그 자리와 한 벌이다).
_FLIPPED = "재판정: 새 프로세스에서 **실패**"


def _run_control(**extra):
    """대조군 모듈 하나만 **진짜 러너로** 돌리고 그 화면을 돌려준다.

    ⛔ `PYTMUX_TEST_REPORT=off` 는 필수다 — 안 끄면 이 서브프로세스가 `reports/testrun.jsonl`
       에 「1 failed」 런을 한 줄 더 쌓고, 그것이 트래커로 흘러 **없는 결함**이 된다.
    """
    env = dict(os.environ, PYTMUX_TEST_RETRY_CONTROL="1", PYTMUX_TEST_REPORT="off",
               **extra)
    env.pop("NO_COLOR", None)          # Textual 무채색 필터(CLAUDE.md §테스트)
    # 부모가 재판정 자식으로 돌고 있으면 그 값이 새어 아래 시험이 통째로 무의미해진다.
    env.pop("PYTMUX_TEST_ADJUDICATING", None)
    env.pop("PYTMUX_TEST_RETRIES", None)
    env.update(extra)
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "run.py"), "test_runner_retry_control"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=300)
    return proc.stdout + proc.stderr


async def test_the_runner_fails_the_control_instead_of_retrying_it_green():
    """★ [[pytmux-430]] 의 관문 그 자체 — 대조군이 `0 failed` 로 끝나지 **않는다**.

    대조군은 첫 시도만 넘어지고 그다음부터 통과한다(프로세스 전역이 시도 사이에
    이어진다) — 종전 규칙에서는 정확히 `PASS (FLAKY)` + `0 failed` 였다.
    """
    out = _run_control()
    assert "1 failed" in out, f"대조군이 실패로 안 잡혔다 — 재시도가 덮고 있다:\n{out}"
    # ★ **어떻게** 잡혔는지까지 본다 — 대조군이 그냥 늘 실패하게 되어도 위 줄은
    #   초록이라, 재판정이 실제로 돌아 뒤집었다는 것을 여기서 못박는다.
    assert _FLIPPED in out, f"재판정이 안 돌았거나 뒤집지 않았다:\n{out}"
    assert "PASS  test_runner_retry_control" not in out, (
        f"대조군이 «재시도 후 통과» 로 회계됐다:\n{out}")


async def test_the_control_really_would_have_been_masked():
    """이 오라클이 **공허하지 않다**는 것을 대조군 자신으로 증명한다.

    ⛔ 위 시험만 두면 「대조군이 그냥 늘 실패하는 시험」이어도 초록이다. 그러면 재시도가
    무엇을 덮었는지는 하나도 안 잰 것이 된다. 탈출구(`PYTMUX_TEST_ADJUDICATE=off`)로
    **종전 규칙을 되살려** 돌리면 같은 대조군이 초록이 돼야 한다 — 그 뒤집힘이 곧
    「덮였다」의 증거다.
    """
    out = _run_control(PYTMUX_TEST_ADJUDICATE="off")
    assert "0 failed" in out and "FLAKY" in out, (
        "종전 규칙을 되살렸는데도 대조군이 초록이 아니다 — 대조군이 프로세스 전역을 "
        f"안 데우고 있거나 탈출구가 안 먹는다(그러면 위 시험은 공허하다):\n{out}")


async def test_the_adjudication_child_does_not_adjudicate_again():
    """재귀 금지 — 재판정 자식(`PYTMUX_TEST_ADJUDICATING=1`)은 또 재판정하지 않는다.

    이 가드가 없으면 flaky 한 건이 프로세스를 무한히 낳는다. 자식은 재시도도 꺼서
    (`PYTMUX_TEST_RETRIES=0`) 「또 데우지 않는다」 — 그래서 자식의 답은 첫 시도의 답이다.
    """
    out = _run_control(PYTMUX_TEST_ADJUDICATING="1", PYTMUX_TEST_RETRIES="0")
    assert "1 failed" in out, f"재판정 자식이 대조군을 통과시켰다:\n{out}"
    assert _FLIPPED not in out, f"재판정 자식이 또 재판정했다(무한 재귀):\n{out}"


async def test_a_plain_failure_costs_no_adjudication():
    """비용은 **flaky 경로에만** 든다 — 모든 시도에서 넘어진 건은 그냥 실패다.

    재시도를 끄면 대조군은 첫 시도에서 확정 실패한다. 그 자리에서 프로세스를 하나 더
    낳으면 붉은 스위트마다 재판정 비용이 곱해진다.
    """
    out = _run_control(PYTMUX_TEST_RETRIES="0")
    assert "1 failed" in out, f"대조군이 실패로 안 잡혔다:\n{out}"
    assert "재판정" not in out, f"재시도 없이 넘어진 건에까지 재판정이 붙었다:\n{out}"


async def test_the_load_stall_retry_survived_the_fix():
    """행(타임아웃) 재시도를 같이 죽이지 않았다 — 그것이 이 고침의 전제다.

    이슈 §고칠 방향의 1안(단언은 재시도 안 함)을 안 골랐다는 뜻이기도 하다. 이 상수가
    0 이 되면 무거운 2-서버 E2E 의 부하 스톨이 다시 스위트를 상시 붉게 만든다.
    """
    assert run.TEST_RETRIES >= 1, "일반 재시도가 꺼졌다 — 재판정의 전제가 사라진다"
    assert run.TEST_TIMEOUT_RETRIES >= 1, "부하 스톨 복구용 타임아웃 재시도가 꺼졌다"
