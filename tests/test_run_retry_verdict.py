"""러너의 «재시도 재판정» 대조군 — pytmux-430 의 관문.

## 무엇을 재나

`tests/run.py` 의 재시도는 **한 프로세스**에서 돈다. 그래서 시도들이 서로 독립이 아니고,
앞 시도가 프로세스 전역(캐시·레지스트리·모듈 전역)을 데우면 **결정론적 실패가 초록으로
덮인다**(실측 2026-09-01: 상한의 26배로 두 번 넘어진 시험이 `0 failed` 로 회계됐다).

이 편은 그 이슈가 못박은 관문을 그대로 세운다 — **결정론적으로 실패하되 프로세스 전역을
데우는 시험**을 하나 만들어 러너에 먹이고, 그것이 `0 failed` 로 끝나지 **않는지**를 잰다.

## 왜 대조군이 둘인가 (공허 통과 방지)

⑴ 재판정 **켠** 채로 → `0 passed, 1 failed` 여야 한다(고침이 문다).
⑵ `PYTMUX_TEST_ADJUDICATE=off` 로 → `1 passed, 0 failed` 여야 한다.
   ⑵ 가 곧 **이 결함이 실재했다는 증거**다. ⑴ 만 있으면 「원래 그랬는지」를 모른다.

## ⚠ 탐침 파일 규약 (test_run_report 와 같다)

탐침은 `discover` 가 보는 **tests/ 안**에 있어야 한다. 그래서 병렬 세션의 전체 스위트가
그 찰나에 이 파일을 집어도 무해해야 한다 — 그래서 탐침은 `PYTMUX_RETRY_CONTROL=1` 일
때만 실패한다. 끝나면 `finally` 로 지운다.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNPY = os.path.join(HERE, "run.py")

# 탐침: 시도 1·2 는 실패하고 시도 3 에서 통과한다 — «데워져야만» 통과하는 모양이다.
# 새 프로세스에서는 _WARM 이 다시 비므로 **언제나 첫 시도에서 실패**한다.
_PROBE = '''import os

_ON = os.environ.get("PYTMUX_RETRY_CONTROL") == "1"
_WARM = []


async def test_only_passes_once_the_process_is_warm():
    _WARM.append(1)
    if _ON and len(_WARM) < 3:
        assert False, "아직 안 데워졌다 (시도 %d)" % len(_WARM)
'''


def _run_probe(name, **extra_env):
    env = dict(os.environ, PYTMUX_RETRY_CONTROL="1", PYTMUX_TEST_REPORT="off")
    env.pop("PYTMUX_TEST_ADJUDICATING", None)   # 부모가 재판정 중이어도 대조군은 돈다
    env.update(extra_env)
    r = subprocess.run([sys.executable, RUNPY, name], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       env=env, timeout=300)
    return r.stdout + r.stderr


def _counts(out):
    m = re.search(r"(\d+) passed, (\d+) failed", out)
    assert m, "요약줄이 없다(절단):\n" + out[-2000:]
    return int(m.group(1)), int(m.group(2))


def _cleanup(name, path):
    for p in (path, path + "c"):
        try:
            os.remove(p)
        except OSError:
            pass
    cache = os.path.join(HERE, "__pycache__")
    if os.path.isdir(cache):
        for f in os.listdir(cache):
            if f.startswith(name):
                try:
                    os.remove(os.path.join(cache, f))
                except OSError:
                    pass


async def test_retry_no_longer_masks_a_deterministic_failure():
    """⑴ 재판정이 켜져 있으면 «데워야 통과하는» 시험은 실패로 잡힌다.

    ⑵ 같은 탐침을 재판정 끄고 돌리면 `1 passed, 0 failed` — 종전 행동이 그대로
    재현되므로, 이 시험은 「원래도 잡혔을 것」을 잡는 공허 통과가 아니다.
    """
    name = "test_retry_probe_%d" % os.getpid()
    path = os.path.join(HERE, name + ".py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_PROBE)
    try:
        # ⑵ 종전 행동(재판정 끔) — 결정론적 실패가 초록으로 덮인다.
        before = _run_probe(name, PYTMUX_TEST_ADJUDICATE="off")
        assert _counts(before) == (1, 0), (
            "대조군이 성립하지 않는다 — 재판정을 꺼도 이미 실패로 잡힌다면 이 편은 "
            "아무것도 안 재는 것이다:\n" + before[-2000:])
        assert "FLAKY" in before, before[-2000:]

        # ⑴ 지금 행동(재판정 켬) — 새 프로세스가 판정을 뒤집는다.
        after = _run_probe(name)
        assert _counts(after) == (0, 1), (
            "재시도가 아직도 결정론적 실패를 덮는다:\n" + after[-2000:])
        assert "재판정" in after, after[-2000:]
    finally:
        _cleanup(name, path)


async def test_module_dot_test_selector_runs_exactly_one():
    """재판정이 딛고 선 «모듈.시험» 선택자 — 그 한 건만 돈다.

    이것이 없으면 재판정은 모듈 전체를 다시 돌려야 하고(비싸다), 있으면서 안 거르면
    엉뚱한 시험의 결과로 판정하게 된다. 그래서 **수**를 단언한다.
    """
    name = "test_retry_sel_%d" % os.getpid()
    path = os.path.join(HERE, name + ".py")
    with open(path, "w", encoding="utf-8") as f:
        f.write("async def test_a():\n    pass\n\n\n"
                "async def test_b():\n    pass\n")
    try:
        whole = _run_probe(name)
        assert _counts(whole) == (2, 0), whole[-1500:]
        one = _run_probe(name + ".test_b")
        assert _counts(one) == (1, 0), one[-1500:]
        assert name + ".test_b" in one and name + ".test_a" not in one, one[-1500:]
    finally:
        _cleanup(name, path)
