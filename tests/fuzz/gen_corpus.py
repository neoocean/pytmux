"""atheris 시드 코퍼스 생성 — `fuzz_targets.seed_corpus()` 를 타깃별 디렉터리에 떨군다.

atheris 는 코퍼스 디렉터리의 시드로 커버리지 탐색을 시작한다. 정상 프레임·경계·알려진
악성(N1 거대 OSC·R2 과다 파라미터 CSI 등)을 시드로 줘 빠르게 의미 있는 경로에 닿게 한다.

    python tests/fuzz/gen_corpus.py            # tests/fuzz/corpus/<target>/ 채움
"""
import hashlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, os.pardir))                       # tests/
sys.path.insert(0, os.path.join(_HERE, os.pardir, os.pardir))           # 프로젝트 루트(pytmuxlib)

import fuzz_targets as ft  # noqa: E402

# 타깃별로 같은 시드를 쓰되(파서가 달라도 바이트는 공유), 디렉터리는 분리해 atheris 가
# 타깃별 코퍼스를 독립 진화시키게 한다.
TARGETS = list(ft.TARGETS)
EXTRA_CRASHERS = [
    b"\x1b[38;2;H", b"\x1b[1;2A", b"\x1b[5;10;99H", b"\x1b[1;2;3r",
    b"\x1b[?1;2;3;4h",
]


def main() -> int:
    base = os.path.join(os.path.dirname(__file__), "corpus")
    seeds = ft.seed_corpus() + EXTRA_CRASHERS
    total = 0
    for target in TARGETS:
        d = os.path.join(base, target)
        os.makedirs(d, exist_ok=True)
        for blob in seeds:
            name = hashlib.sha1(blob).hexdigest()[:16]
            with open(os.path.join(d, name), "wb") as f:
                f.write(blob)
            total += 1
    print(f"wrote {total} seed files across {len(TARGETS)} targets under {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
