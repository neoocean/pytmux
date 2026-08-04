#!/usr/bin/env python3
"""**경로 범위** 픽스처 — 정본이 패널 글에서 무엇을 경로로 보는가(§10-21ⓧ2).

# 왜 이 표가 필요한가

제보가 "정본에도 같이"라고 명시한 항목이라, 같은 줄에서 **두 클라가 같은 자리를 짚어야**
한다. 그런데 그 판정은 휴리스틱이다 — 구분자가 있나, 마지막 조각에 확장자가 있나, 감싼
괄호를 어디까지 떼나. 그런 규칙은 옮겨 적으면 반드시 한 칸씩 어긋나고, **어긋난 것은 나란히
놓아야만 보인다**(한쪽에서만 밑줄이 한 글자 길거나 짧다).

그래서 정본 함수를 **직접 불러** 답을 뽑고, Rust 쪽이 같은 답을 내는지 기계로 잰다.

# 왜 링크(URL)는 안 뽑나

링크는 **GUI 전용**이다(§10-21ⓥ2 — 정본에 없다. 제보 자신이 "이것은 패리티가 아니라
신규"라고 적었다). 없는 것을 대조하면 픽스처가 거짓말을 하게 되므로, 두 클라가 **둘 다
가진 것**만 싣는다.

    python3 scripts/gen_spans_fixture.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join("crates", "proto", "tests", "fixtures", "spans.json")

# 재는 줄들. **경계 사례가 요점**이다 — 평범한 줄만 넣으면 아무것도 안 재는 표가 된다.
LINES = [
    "Update(server/test/shot-guide-badges.mjs)",
    "2026/08/02 에 고쳤다",
    "a/b 를 보라",
    "readme.md 하나",
    "client/crates/base/src/spans.rs 를 열었다",
    r"열었다 client\crates\gui\src\main.rs 를",
    "끝에 붙은 것 docs/x.md, 그리고 docs/y.md.",
    "\"quoted/path.txt\" 와 'other/p.rs'",
    "https://x.dev/a/b.html",
    "한글과 docs/한글/파일.md 섞임",
    "여러 개 a/b.rs 와 c/d.rs 둘",
    "",
]


def _utf8_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(HERE, ".."))
    ap.add_argument("--out", default=os.path.join(HERE, OUT))
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib.clientutil import find_paths

    cases = []
    for line in LINES:
        cases.append({
            "line": line,
            # `[시작, 끝)` 은 **글자 인덱스**다(바이트가 아니다 — 한글이 섞이면 갈린다).
            "paths": [{"start": s, "end": e, "text": t} for s, e, t in find_paths(line)],
        })
    found = sum(len(c["paths"]) for c in cases)
    if not found:
        sys.exit("경로를 하나도 못 찾았다 — 빈 결과는 통과가 아니라 고장이다")

    payload = {
        "_comment": "python3 scripts/gen_spans_fixture.py 로 생성. 출처 = "
                    "pytmuxlib/clientutil.find_paths. 손으로 고치지 말 것.",
        "cases": cases,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"{args.out} — 줄 {len(cases)}개 · 경로 {found}개")


if __name__ == "__main__":
    main()
