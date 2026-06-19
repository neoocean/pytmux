"""atheris 커버리지 가이드 퍼저(Tier 2) — docs/internal/SECURITY_REVIEW.md §9.

`test_fuzz_parsers.py`(결정론적 baseline)와 **같은 불변식 타깃**(`tests/fuzz_targets.py`)을
공유하되, atheris(libFuzzer)가 커버리지로 입력을 진화시켜 더 깊은 경로를 판다.

설치·실행(로컬/야간 CI):
    pip install atheris
    python tests/fuzz/gen_corpus.py                 # 시드 코퍼스 생성
    FUZZ_TARGET=vtparse python tests/fuzz/fuzzer.py -max_total_time=120 \
        tests/fuzz/corpus/vtparse
타깃(FUZZ_TARGET): protocol | ptyhost | clamp | vtparse(기본).

이 파일은 `test_` 가 아니라 run.py 스위트에 수집되지 않는다(atheris 미설치 환경 무영향).
크래시가 나오면 atheris 가 `crash-<hash>` 파일을 떨군다 → tests/fuzz/crashes/ 로 옮겨
test_fuzz_parsers 의 known-crashers 회귀에 추가한다.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, os.pardir))                      # tests/
sys.path.insert(0, os.path.join(_HERE, os.pardir, os.pardir))          # 프로젝트 루트(pytmuxlib)

import atheris  # noqa: E402

with atheris.instrument_imports():
    import fuzz_targets as ft  # noqa: E402

_TARGET = os.environ.get("FUZZ_TARGET", "vtparse")
_fn = ft.TARGETS[_TARGET]


def TestOneInput(data: bytes) -> None:
    # 타깃이 던지는 모든 예외(파서 계약 위반·불변식 AssertionError)를 그대로 전파하면
    # atheris 가 크래시로 기록한다 — 그게 곧 보안 결함 신호다.
    _fn(data)


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
