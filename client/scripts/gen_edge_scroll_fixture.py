#!/usr/bin/env python3
"""선택 끌기 가장자리 자동 스크롤 픽스처 — 두 클라가 **같은 델타**로 구르는가(pytmux-579).

# 왜 이 대조가 필요한가

정본은 끌어 선택하다 포인터가 패널 위/아래 가장자리에 닿으면 타이머로 계속 굴러
한 화면을 넘는 선택을 만든다. 판정(경계 행 포함)과 속도(밖으로 갈수록 빨라져 최대 3행)가
어긋나면 «GUI 는 끝 줄에서 안 구른다» · «너무 빨라 읽을 수 없다» 로만 보인다.

# 어떻게 뽑는가

`clientwidgets.MultiplexerView._edge_scroll_delta` 는 `self` 에서 `_sel_rect` 와
`_AUTOSCROLL_MAX` 만 읽는다. 그 둘만 든 가짜 self 로 언바운드 호출한다(위젯도 앱도 안 띄운다).
간격 `_AUTOSCROLL_SEC` 도 함께 싣는다 — 소비자는 `base::edge_scroll::TICK` 과 맞댄다.

    python3 scripts/gen_edge_scroll_fixture.py [--pytmux ..] [--out …]
"""

import argparse
import json
import os
import sys
import types

OUT = os.path.join("crates", "proto", "tests", "fixtures", "edge_scroll.json")

# (pane_y, pane_h) — 맨 위 패널 · 가운데 패널 · 한 줄 패널.
PANES = [(0, 10), (5, 8), (3, 1)]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()

    root = os.path.abspath(args.pytmux)
    if not os.path.isdir(root):
        sys.exit(f"pytmux 저장소를 못 찾았다: {root}")
    sys.path.insert(0, root)
    from pytmuxlib import clientwidgets

    view = clientwidgets.MultiplexerView
    fn = view._edge_scroll_delta
    rows = []
    for py, ph in PANES:
        fake = types.SimpleNamespace(_sel_rect=(0, py, 40, ph),
                                     _AUTOSCROLL_MAX=view._AUTOSCROLL_MAX)
        for y in range(py - 8, py + ph + 8):
            rows.append({"pane_y": py, "pane_h": ph, "y": y, "delta": fn(fake, y)})
    if not any(r["delta"] for r in rows) or not any(r["delta"] == 0 for r in rows):
        sys.exit("결과가 한쪽으로만 쏠렸다 — 뽑는 방법이 틀렸다")

    payload = {
        "_comment": "python3 scripts/gen_edge_scroll_fixture.py 로 생성. 출처 = "
                    "pytmuxlib/clientwidgets.py 의 MultiplexerView._edge_scroll_delta · "
                    "_AUTOSCROLL_SEC · _AUTOSCROLL_MAX.",
        "tick_ms": round(view._AUTOSCROLL_SEC * 1000),
        "max": view._AUTOSCROLL_MAX,
        "cases": rows,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{args.out} — 경우 {len(rows)}개")


if __name__ == "__main__":
    main()
