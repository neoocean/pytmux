#!/usr/bin/env python3
"""터치 스크롤바 픽스처 — 정본의 스크롤바 **산수**를 그대로 뽑는다.

# 왜 손으로 안 옮기나

`clientrender.scrollbar_*` 셋은 짧지만 **반올림이 세 군데**(썸 길이·썸 위치·점프 델타)
있고 경계 조건이 넷이다(높이 3 미만 · 스크롤백 없음 · 맨 위 · 맨 아래). 손으로 옮기면
"거의 같은" 바가 나오고, 그 차이는 **한 칸씩 어긋난 썸**으로만 보인다 — 사람이 두 화면을
나란히 놓고 봐야 알아채는 부류다. 이 저장소가 이미 그 부류로 두 번 물렸다(모양 대조가
아니라 **값 대조**가 답이다).

정본을 직접 import 해서 뽑으므로, 정본이 산수를 바꾸면 픽스처가 바뀌고 적합성 테스트가
운다 — 그것이 이 파일의 값이다.

# 무엇을 뽑나

| 키 | 뜻 |
|---|---|
| `consts` | 글자 넷(▲▼│█)과 최소 높이 — 우리 표가 같은 글자를 써야 한다 |
| `bars` | `(h, top, scroll)` → 그 열의 글자 문자열(빈 문자열 = 미표시) |
| `hits` | `(h, iy)` → `null` 또는 `["up"|"down"|"jump", frac]` |
| `jumps` | `(h, top, scroll, frac)` → 델타 |

경우는 **경계를 노려** 고른다(높이 2·3 · 스크롤백 0 · 맨 위 · 맨 아래 · 트랙 한 칸).
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join("crates", "proto", "tests", "fixtures", "scrollbar.json")

# (h, top, scroll) — 높이·절대 첫 행·올라간 행수.
BAR_CASES = [
    (2, 0, 0),        # 최소 높이 미만 → 미표시
    (3, 0, 0),        # 트랙 한 칸
    (5, 0, 0),        # 스크롤백 없음 → 썸이 트랙 전체
    (5, 0, 3),
    (10, 0, 0),
    (10, 20, 0),      # 맨 아래(라이브)
    (10, 0, 20),      # 맨 위
    (10, 10, 10),
    (10, 100, 5),
    (24, 500, 250),
    (24, 3, 1),
    (40, 1000, 0),
]

# (h, iy)
HIT_CASES = [
    (2, 0), (2, 1),
    (3, 0), (3, 1), (3, 2), (3, 3), (3, -1),
    (5, 0), (5, 1), (5, 2), (5, 3), (5, 4),
    (10, 0), (10, 1), (10, 5), (10, 8), (10, 9),
    (24, 12),
]

# (h, top, scroll, frac)
JUMP_CASES = [
    (10, 0, 0, 0.0), (10, 0, 0, 1.0),
    (10, 100, 0, 0.0), (10, 100, 0, 0.5), (10, 100, 0, 1.0),
    (10, 50, 50, 0.0), (10, 50, 50, 0.5), (10, 50, 50, 1.0),
    (24, 7, 3, 0.25), (24, 7, 3, 0.75),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(HERE, ".."))
    ap.add_argument("--out", default=os.path.join(HERE, OUT))
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib import clientrender as cr

    data = {
        "consts": {
            "up": cr.SCROLLBAR_UP,
            "down": cr.SCROLLBAR_DOWN,
            "track": cr.SCROLLBAR_TRACK,
            "thumb": cr.SCROLLBAR_THUMB,
            "min_h": cr.SCROLLBAR_MIN_H,
        },
        "bars": [
            {"h": h, "top": top, "scroll": scroll,
             "bar": "".join(cr.scrollbar_chars(h, top, scroll))}
            for (h, top, scroll) in BAR_CASES
        ],
        "hits": [
            {"h": h, "iy": iy, "hit": _hit(cr.scrollbar_hit(h, iy))}
            for (h, iy) in HIT_CASES
        ],
        "jumps": [
            {"h": h, "top": top, "scroll": scroll, "frac": frac,
             "delta": cr.scrollbar_jump_delta(h, top, scroll, frac)}
            for (h, top, scroll, frac) in JUMP_CASES
        ],
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"{args.out} — 바 {len(data['bars'])} · 탭 {len(data['hits'])} "
          f"· 점프 {len(data['jumps'])}")


def _hit(hit):
    """`(kind, frac)` 튜플을 JSON 으로. 미표시/범위 밖은 `null`."""
    if hit is None:
        return None
    kind, frac = hit
    return {"kind": kind, "frac": frac}


if __name__ == "__main__":
    main()
