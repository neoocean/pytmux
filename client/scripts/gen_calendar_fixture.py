#!/usr/bin/env python3
"""달력 오버레이 픽스처 — 그려진 **화면 자체**를 뜬다.

# 왜 글리프 표가 아니라 화면인가

달력은 시계와 달리 폰트만의 문제가 아니다. 칸 폭(`colw`)·주 간격(`rowh`)이 패널 크기에
따라 늘어나고, 어느 크기에서 큰 달력 → 보통 격자 → 단순 날짜로 떨어지는지가 규칙이다.
글리프만 맞춰 놓고 그 규칙을 손으로 옮기면, 특정 창 크기에서만 두 클라가 다르게 보인다 —
사람이 그 크기를 우연히 만나기 전까지 아무도 모른다.

그래서 파이썬 `draw_calendar_overlay` 를 **직접 불러** 여러 패널 크기에서 나온 셀 그리드를
글자만 떠 온다. Rust 쪽은 같은 입력에 같은 그림이 나오는지만 보면 된다.

기준 시각은 고정한다(2026-07-29 수요일) — 안 그러면 픽스처가 뽑은 날에 묶인다.

    python scripts/gen_calendar_fixture.py [--pytmux ..]
"""

import argparse
import datetime
import json
import os
import sys

# 세 단(큰 달력 · 보통 격자 · 단순 날짜)과 그 경계를 노린다.
SIZES = [
    (200, 60),   # 큰 달력(큰 폰트)
    (120, 40),   # 큰 달력(반칸 폰트)
    (80, 24),    # 보통 격자
    (60, 20),
    (40, 12),
    (30, 10),
    (26, 9),
    (20, 6),     # 단순 날짜로 떨어지는 언저리
    (12, 3),
    (8, 2),
]
# 기준 시각 — 요일 배치와 '오늘' 강조가 이 날짜에 걸린다.
NOW = datetime.datetime(2026, 7, 29, 11, 57, 48)
# 이번 달·지난달·다음 달. 오프셋이 0이 아니면 '오늘' 강조가 없어야 한다.
OFFSETS = [0, -1, 1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(".."))
    ap.add_argument("--out", default=os.path.join(
        "crates", "proto", "tests", "fixtures", "calendar.json"))
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.pytmux))
    from rich.style import Style  # noqa: E402
    from pytmuxlib.plugins.calendar.render import draw_calendar_overlay  # noqa: E402

    # 스타일은 **자리만** 채운다 — 픽스처가 뜨는 것은 글자다(색은 테마가 정하고,
    # 우리 쪽 팔레트와 일대일로 맞출 수 없다). 색까지 뜨면 표가 테마에 묶인다.
    styles = {k: Style() for k in
              ("day", "title", "today", "big_today", "border")}

    cases = []
    for w, h in SIZES:
        for off in OFFSETS:
            cells = [[(" ", Style()) for _ in range(w)] for _ in range(h)]
            panes = [{"id": 1, "x": 0, "y": 0, "w": w, "h": h}]
            zones = {}
            draw_calendar_overlay(cells, panes, {1}, w, h, styles, now=NOW,
                                  offsets={1: off}, nav_zones=zones)
            lines = ["".join(c for c, _ in row).rstrip() for row in cells]
            cases.append({
                "w": w, "h": h, "offset": off,
                "lines": lines,
                # ‹/› 클릭존 — 이게 없으면 제목의 화살표가 거짓말이 된다.
                "nav_zones": [list(z) for z in zones.get(1, [])],
            })

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "source": "pytmuxlib/plugins/calendar/render.py::draw_calendar_overlay",
            "now": NOW.strftime("%Y-%m-%dT%H:%M:%S"),
            "note": "색은 안 뜬다(테마가 정한다) — 글자와 자리만.",
            "cases": cases,
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{len(cases)}칸 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
