#!/usr/bin/env python3
"""시계 오버레이 폰트 픽스처 — 글리프 한 칸이라도 어긋나면 **눈에 바로 보인다**.

# 왜 표로 묶는가

`clock` 플러그인은 3×5 블록 폰트로 숫자를 그린다(`clientutil._CLOCK_FONT` 와 넓은
화면용 `_CLOCK_FONT_BIG`). 손으로 옮겨 적으면 `▀`/`▄`/`█` 이 한 칸만 어긋나도 숫자가
다르게 보이고, 그건 "우리 시계는 파이썬 것과 다르게 생겼다"로 끝난다 — 두 클라를 오가는
사람에게는 그 자체가 결함이다.

폰트 고르는 기준(`clock_font_for`)도 같이 뽑는다. 그 함수가 **언제 큰 폰트로 넘어가는지**가
갈리면 같은 크기 패널에서 두 클라가 다른 크기의 시계를 그린다.

    python scripts/gen_clock_font_fixture.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

# 폰트 선택이 갈리는 자리를 노린다 — 경계 바로 아래/위.
# "HH:MM:SS" 8글자 기준 큰 폰트 폭은 8*6 + 7*1 = 55, 높이는 5다.
PICKS = [
    (80, 24), (60, 20),
    (55, 5), (54, 5), (55, 4),          # ★ 경계 — 한 칸 차이로 폰트가 갈린다
    (30, 10), (23, 10), (24, 3), (23, 3), (10, 3), (8, 2), (3, 1),
]
TEXT_LEN = 8  # "HH:MM:SS"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(".."))
    ap.add_argument("--out", default=os.path.join(
        "crates", "proto", "tests", "fixtures", "clock_font.json"))
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib import clientutil  # noqa: E402

    small = clientutil._CLOCK_FONT
    big = clientutil._CLOCK_FONT_BIG
    if set(small) != set(big):
        sys.exit(f"두 폰트의 글자 집합이 다르다: {sorted(set(small) ^ set(big))}")

    picks = []
    for w, h in PICKS:
        font, rows, cols, width = clientutil.clock_font_for(w, h, TEXT_LEN)
        picks.append({
            "avail_w": w, "avail_h": h, "chars": TEXT_LEN,
            "big": font is big, "rows": rows, "cols": cols, "width": width,
        })

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "source": "pytmuxlib/clientutil.py (_CLOCK_FONT · _CLOCK_FONT_BIG · "
                      "clock_font_for)",
            "small": {"rows": clientutil._CLOCK_FONT_ROWS,
                      "cols": clientutil._CLOCK_FONT_COLS,
                      "glyphs": {k: small[k] for k in sorted(small)}},
            "big": {"rows": clientutil._CLOCK_FONT_BIG_ROWS,
                    "cols": clientutil._CLOCK_FONT_BIG_COLS,
                    "glyphs": {k: big[k] for k in sorted(big)}},
            # 글자 사이 간격(칸). clock_font_for 의 기본값이자 render 의 호출값.
            "gap": 1,
            "picks": picks,
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"글자 {len(small)}개 · 고르기 {len(picks)}칸 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
