#!/usr/bin/env python3
"""정본 `:` 줄의 **인자 자리 후보**(`PromptScreen._arg_candidates`)를 픽스처로 뽑는다
(pytmux-621).

표 둘(`COMMAND_ARGHIST`·`SET_OPTION_CHOICES`)을 그대로 싣고, 고정 이력 위에서 줄마다
`_arg_candidates` 를 **직접 불러** 「인자 자리인가 · 후보 · 줄 재조립에 쓸 명령 토큰」을 적는다.
Rust 시험(`prompt_args_conformance.rs`)이 `base::promptargs` 가 같은 답을 내는지 맞댄다.

    python3 scripts/gen_prompt_args.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "prompt_args.json")

HIST = {
    "remote-host": ["office1", "Office2", "build@ci", "NATGAMES\\user@host"],
    "layout-name": ["work", "home"],
    "run-shell": ["make test"],
}

LINES = ["", "remote-attach", "remote-attach ", "remote-attach o", "remote-attach O",
         "Remote_Attach ", "remote attach ", "remote-attach office1", "remote-attach zz",
         "  layout-load ", "layout-load h", "run-shell ", "send-keys ", "rename-tab ",
         "set ", "set ambiguous-width", "set ambiguous-width ", "set ambiguous-width n",
         "set ambiguous-width narrow", "set-option mouse ", "set mouse_drag_copy s",
         "set status-left ", "set ambiguous-width narrow x", "kill-pane"]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()
    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib import clientutil as cu
    from pytmuxlib import clientcmd
    from pytmuxlib.clientscreens import PromptScreen

    mixin = next(c for c in vars(clientcmd).values()
                 if isinstance(c, type) and hasattr(c, "_arghist_list") and hasattr(c, "_arghist_canon"))

    class _App:
        _arghist = HIST
        _arghist_canon = staticmethod(mixin._arghist_canon)
        _arghist_list = mixin._arghist_list

    class _Lbl:
        display = False

    sub = type("P", (PromptScreen,), {"app": _App()})
    cases = []
    for raw in LINES:
        scr = PromptScreen.__new__(sub)
        scr._purpose = "command"
        scr._cand = []
        scr._sel = 0
        scr._cand_shown = False
        scr._arg_mode = False
        scr._arg_cmd = None
        scr._render_cands = lambda: None
        is_arg = bool(scr._arg_candidates(raw, _Lbl()))
        cases.append({
            "raw": raw,
            "arg_spot": is_arg,
            "cmd": scr._arg_cmd if is_arg else None,
            "items": [c[0] for c in scr._cand] if is_arg else [],
            "kinds": [("choice" if d == cu.i18n.t("screen.arg_choice") else "recent")
                      for _n, d in scr._cand] if is_arg else [],
        })
    if not any(c["items"] for c in cases):
        sys.exit("어느 줄에도 후보가 안 떴다 — 뽑는 방법이 틀렸다")
    payload = {
        "_comment": "python3 scripts/gen_prompt_args.py 로 생성. 출처 = 정본 "
                    "PromptScreen._arg_candidates 를 직접 부른 값 + clientutil 의 두 표.",
        "command_arghist": cu.COMMAND_ARGHIST,
        "set_option_choices": {k: list(v) for k, v in cu.SET_OPTION_CHOICES.items()},
        "history": HIST,
        "cases": cases,
    }
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{len(cases)} 줄 → {args.out}")


if __name__ == "__main__":
    main()
