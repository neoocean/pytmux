#!/usr/bin/env python3
"""프롬프트 바(고정 · 미리보기) 규칙 답지 생성기(pytmux-587).

정본 `plugins/claude-prompt-history/render.py` 의 `draw_preview`·`draw_pinned` 를 **직접
호출**한다 — 가짜 앱(레이아웃 · `ph_panes` · 스크롤 · 대상 패널)과 셀 격자를 주고, 정본이
격자에 남긴 글과 바 표(`_ph_pinned`)를 답으로 뜬다. 비키는 조건 다섯과 접기·자르기·`…`·`▾`
경계가 손으로 옮기면 조용히 어긋나는 자리라 답지는 정본이 쓴다.

함께 싣는 것: 코어 패널 대상 명령 표(`clientutil.PANE_SCOPED_CMDS`) — `:` 미리보기가 서는 조건.

GUI 의 `proto::prompt_bar` 가 같은 답을 내는지 `proto/tests/prompt_bar_conformance.rs` 가 잰다.

사용: python3 scripts/gen_prompt_bar.py [--out 경로]
출력: crates/proto/tests/fixtures/prompt_bar.json
"""
import argparse
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYTMUX = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PYTMUX)

from rich.style import Style  # noqa: E402

from pytmuxlib.clientutil import PANE_SCOPED_CMDS  # noqa: E402

render = importlib.import_module("pytmuxlib.plugins.claude-prompt-history.render")


class _App:
    def __init__(self, case):
        self.layout = {"panes": case["panes"], "active": case["panes"][0]["id"]}
        self.ph_panes = {int(k): {"id": int(k), "h": v} for k, v in case["hist"].items()}
        self.ph_max_lines = case.get("max_lines", 3)
        self._cmd_target_pane = case.get("target")
        self.pane_scroll = {int(k): v for k, v in case.get("scroll", {}).items()}
        self._theme = None


def _grid(case):
    W, H = case["W"], case["H"]
    st = Style()
    cells = [[(" ", st) for _ in range(W)] for _ in range(H)]
    for y, row in enumerate(case.get("grid", [])):
        for x, ch in enumerate(row[:W]):
            cells[y][x] = (ch, st)
    return cells


def _text(cells, y, x0, x1):
    return "".join(cells[y][x][0] for x in range(x0, x1)
                   if cells[y][x][0] != "").rstrip()


def P(pid, x, y, w, h):
    return {"id": pid, "x": x, "y": y, "w": w, "h": h}


LONG = "please refactor the session view so that the sticky bar folds"
CASES = {
    "one_row": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)], "hist": {"1": ["fix the bug"]}},
    "older_history_uses_the_last": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                                    "hist": {"1": ["first", "second"]}},
    "folds_into_two_rows_with_ellipsis": {"W": 20, "H": 6, "panes": [P(1, 0, 0, 20, 6)],
                                          "hist": {"1": [LONG]}},
    "exact_fit_two_rows": {"W": 14, "H": 6, "panes": [P(1, 0, 0, 14, 6)],
                           "hist": {"1": ["abcdefghij" "klmnopqrst"]}},
    "multiline_capped_with_ellipsis": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                                       "hist": {"1": ["line one\nline two\nline three"]}},
    "two_lines_exact": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                        "hist": {"1": ["line one\nline two"]}},
    "wide_chars_fold": {"W": 16, "H": 6, "panes": [P(1, 0, 0, 16, 6)],
                        "hist": {"1": ["한글 프롬프트를 길게 적어 봅니다 끝"]}},
    "blank_lines_skipped": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                            "hist": {"1": ["\n\n  first  \n\n second"]}},
    "control_chars_fold_to_space": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                                    "hist": {"1": ["a\tb\x1bc\x7fd"]}},
    "clear_hides_the_bar": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                            "hist": {"1": ["old prompt", "/clear"]}},
    "reset_with_args_hides": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                              "hist": {"1": ["old", "  /Reset now"]}},
    "first_row_already_a_prompt": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                                   "grid": ["  ❯ something else"],
                                   "hist": {"1": ["fix the bug"]}},
    "first_row_old_sticky_mark": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                                  "grid": ["› x"], "hist": {"1": ["fix the bug"]}},
    "prompt_visible_in_pane": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                               "grid": ["", "", "", "> fix the bug"],
                               "hist": {"1": ["fix the bug please"]}},
    "other_prompt_visible_does_not_hide": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                                           "grid": ["", "", "> something else"],
                                           "hist": {"1": ["fix the bug"]}},
    "bare_mark_row_does_not_hide": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                                    "grid": ["", ">"], "hist": {"1": ["fix"]}},
    "scrolled_pane_yields": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                             "hist": {"1": ["fix the bug"]}, "scroll": {"1": 3}},
    "narrow_pane_has_none": {"W": 7, "H": 6, "panes": [P(1, 0, 0, 7, 6)],
                             "hist": {"1": ["fix"]}},
    "short_pane_has_none": {"W": 30, "H": 3, "panes": [P(1, 0, 0, 30, 3)],
                            "hist": {"1": ["fix"]}},
    "four_rows_pane": {"W": 30, "H": 4, "panes": [P(1, 0, 0, 30, 4)],
                       "hist": {"1": ["a\nb\nc"]}},
    "empty_last_prompt": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)], "hist": {"1": [""]}},
    "pane_without_history": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)], "hist": {}},
    "target_gets_preview_not_pinned": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                                       "hist": {"1": ["fix the bug"]}, "target": 1},
    "preview_truncates_lines_with_arrow": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                                           "hist": {"1": ["one\ntwo\nthree\nfour\nfive"]},
                                           "target": 1},
    "preview_max_one_line": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                             "hist": {"1": ["one\ntwo"]}, "target": 1, "max_lines": 1},
    "preview_short_pane": {"W": 30, "H": 2, "panes": [P(1, 0, 0, 30, 2)],
                           "hist": {"1": ["one\ntwo\nthree"]}, "target": 1},
    "preview_cuts_width_not_folds": {"W": 12, "H": 6, "panes": [P(1, 0, 0, 12, 6)],
                                     "hist": {"1": [LONG]}, "target": 1},
    "preview_wide_chars": {"W": 11, "H": 6, "panes": [P(1, 0, 0, 11, 6)],
                           "hist": {"1": ["한글한글한글\n둘째"]}, "target": 1, "max_lines": 1},
    "preview_scrolled_still_shows": {"W": 30, "H": 6, "panes": [P(1, 0, 0, 30, 6)],
                                     "hist": {"1": ["fix"]}, "target": 1, "scroll": {"1": 4}},
    "preview_too_narrow": {"W": 5, "H": 6, "panes": [P(1, 0, 0, 5, 6)],
                           "hist": {"1": ["fix"]}, "target": 1},
    "two_panes_target_and_pinned": {"W": 61, "H": 6,
                                    "panes": [P(1, 0, 0, 30, 6), P(2, 31, 0, 30, 6)],
                                    "hist": {"1": ["left prompt"], "2": ["right prompt"]},
                                    "target": 1},
    "offset_pane": {"W": 40, "H": 10, "panes": [P(3, 5, 2, 30, 6)],
                    "grid": ["", "", "     hello"], "hist": {"3": ["fix"]}},
}


def run(case):
    app = _App(case)
    cells = _grid(case)
    W, H = case["W"], case["H"]
    render.draw_preview(app, cells, W, H)
    preview_rows = sorted(getattr(app, "_ph_undim_rows", set()) or ())
    render.draw_pinned(app, cells, W, H)
    preview = None
    if preview_rows:
        pid = case["target"]
        pane = next(p for p in case["panes"] if p["id"] == pid)
        x0 = pane["x"] + 1
        x1 = min(pane["x"] + pane["w"] - 1, W)
        preview = {"pane": pid, "rows": [_text(cells, y, x0, x1) for y in preview_rows]}
    pinned = {}
    for pid, (y0, y1, x0, x1) in sorted(getattr(app, "_ph_pinned", {}).items()):
        pane = next(p for p in case["panes"] if p["id"] == pid)
        pinned[str(pid)] = {"rect": [y0, y1, x0, x1],
                            "rows": [_text(cells, y, pane["x"] + 1, x1 - 1)
                                     for y in range(y0, y1)]}
    return {"preview": preview, "pinned": pinned}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        HERE, "..", "crates", "proto", "tests", "fixtures", "prompt_bar.json"))
    args = ap.parse_args(argv)
    out = {"core_pane_scoped": sorted(PANE_SCOPED_CMDS),
           "pinned_max_rows": render.PINNED_MAX_ROWS, "cases": {}}
    for name, case in CASES.items():
        out["cases"][name] = {"input": case, "expect": run(case)}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print(f"wrote {args.out} ({len(CASES)} cases)")


if __name__ == "__main__":
    main()
