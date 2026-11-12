#!/usr/bin/env python3
"""정본 설정 판의 **숫자 한 걸음**(`SettingsScreen._cycle`)을 픽스처로 뽑는다 (pytmux-622).

# 왜 필요한가

GUI 는 숫자 줄이 끝에서 **반대쪽 끝으로 감겼고** 정본은 **끝에서 멈춘다**
(`max(lo, min(hi, cur + step*dir))`). 그 갈림은 코드를 읽어야 보였다 — 이 픽스처는
정본의 `_cycle` 을 **직접 불러** 그 답을 적어 두고, Rust 시험이 같은 시작값에서
`setting_pick_dir` 이 같은 값을 내는지 맞댄다. 재구현이 아니라 호출이라 정본이 규칙을
바꾸면(예: 감기로 정하면) 이 픽스처가 따라 바뀌고 GUI 시험이 운다.

    python3 scripts/gen_setting_cycle.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "setting_cycle.json")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()
    root = os.path.abspath(args.pytmux)
    sys.path.insert(0, root)
    from pytmuxlib import clientutil as cu
    from pytmuxlib.clientscreens import SettingsScreen

    class _App:
        def __init__(self):
            self.applied = []

        def apply_setting(self, desc, val):
            self.applied.append(val)

    cases = []
    for desc in cu.SETTINGS:
        if desc.get("type") not in ("ratio", "int"):
            continue
        lo, hi, step = desc["lo"], desc["hi"], desc["step"]
        # 시작값은 그 줄이 실제로 가질 수 있는 값이라야 한다(정수 줄에 10.5 를 주면 GUI 는
        # 그 값을 담을 칸이 없다 — 재는 것이 규칙이 아니라 표현이 된다).
        mid = (lo + hi) // 2 if desc["type"] == "int" else round((lo + hi) / 2, 2)
        starts = [None, lo, hi, lo + step, hi - step, mid]
        for start in starts:
            for direction in (1, -1):
                scr = SettingsScreen.__new__(SettingsScreen)
                app = _App()
                scr._flat = [(desc, True)]
                scr._vals = {desc["key"]: start}
                scr._refresh_row = lambda idx: None
                # `self.app` 은 Textual 의 속성이라 인스턴스에 못 꽂는다 — 클래스 하나를 덧씌운다.
                sub = type("S", (SettingsScreen,), {"app": app})
                scr.__class__ = sub
                scr._cycle(0, direction)
                if len(app.applied) != 1:
                    sys.exit(f"{desc['key']}: _cycle 이 apply_setting 을 {len(app.applied)}번 불렀다")
                cases.append({
                    "key": desc["key"], "type": desc["type"],
                    "lo": lo, "hi": hi, "step": step,
                    "start": start, "forward": direction > 0,
                    "got": float(app.applied[0]),
                })
    if len(cases) < 12:
        sys.exit("숫자 설정이 거의 안 잡혔다 — 정본 SETTINGS 의 type 이름이 바뀌었을 것이다")
    payload = {
        "_comment": "python3 scripts/gen_setting_cycle.py 로 생성. 출처 = 정본 "
                    "pytmuxlib/clientscreens.py SettingsScreen._cycle 을 직접 부른 값.",
        "cases": cases,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{len(cases)} 걸음 → {args.out}")


if __name__ == "__main__":
    main()
