#!/usr/bin/env python3
"""정본 **타이핑 찾기**의 뛰기 사례를 뽑는다(pytmux-611·612).

정본 mdir·ncd 의 빨리찾기는 `pytmuxlib/typeahead.py:find_jump` 한 벌이고, GUI 클라가 같은
규칙을 `base::screens::typeahead_jump` 로 한 벌 더 든다. 이 생성기는 정본 함수를 **직접
불러** 사례마다 답을 적고, `crates/proto/tests/typeahead_conformance.rs` 가 같은 사례를 우리
함수에 넣어 맞댄다 — 한쪽만 고치면 그 자가 운다.

    python3 scripts/gen_typeahead_fixture.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "typeahead.json")

#: 이름표 — 빈 이름은 건너뛸 줄(mdir 의 `..`·드라이브)이다.
_NAMES = ["", "alpha", "Beta", "xalphab", "gamma", "가나다", "README.md", "", "beta2"]
_QUERIES = ["a", "al", "lph", "b", "BE", "beta2", "가", "나", "md", "zz", "", "x", "e"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(os.path.dirname(__file__), "..", ".."))
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.pytmux))
    from pytmuxlib.typeahead import find_jump

    cases = []
    for start in range(len(_NAMES)):
        for q in _QUERIES:
            cases.append({"start": start, "query": q,
                          "answer": find_jump(_NAMES, start, q)})
    # 끝을 넘긴 시작(커서가 `End` 의 큰 값에 서 있을 때)도 한 사례.
    cases.append({"start": 10 ** 6, "query": "al", "answer": find_jump(_NAMES, 10 ** 6, "al")})
    if not any(c["answer"] is not None for c in cases):
        sys.exit("사례가 하나도 안 맞았다 — 공허한 픽스처")
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"names": _NAMES, "cases": cases}, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{a.out}: 사례 {len(cases)}")


if __name__ == "__main__":
    main()
