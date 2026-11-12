#!/usr/bin/env python3
"""`:` 줄의 **선택지 모드** 답지 생성기(pytmux-634).

정본 `PromptScreen`(`pytmuxlib/clientscreens.py`)은 친 줄이 **선택지 인자 명령의 이름 그대로**
(인자 없음)이고 그 이름 말고 부분일치하는 명령이 없으면(`_refresh_cands` 가 후보를 숨긴다)
인자 자리에 선택지를 띄운다(`_refresh_hint`). ←→/↑↓ 가 돌고 Enter 가 `이름 값`(값이 비면
`이름`)을 실행한다(`_run_choice`).

여기서는 정본 표(`COMMAND_OPTIONS` + 플러그인 `command_options`)와 명령 풀(`COMMANDS` +
플러그인 `commands`)을 **직접 import** 해 명령마다 «선택지 모드에 들어가나 · 선택지 · 고른
자리마다 실행 줄»을 뽑는다. 게이트 산수(부분일치 풀)는 정본 `_refresh_cands` 의 그것이고,
그 답이 실제 정본 프롬프트와 같은지는 정본 시험
`tests/test_prompt_choice_mode.py` 가 헤드리스 프롬프트에 이 답지를 그대로 쳐서 잰다.

GUI 의 `base::screens` 팔레트가 같은 답을 내는지 `crates/base/src/screens_tests.rs`·
`crates/proto/tests/prompt_choices_conformance.rs` 가 잰다.

사용: python3 scripts/gen_prompt_choices.py [--out 경로]
출력: crates/proto/tests/fixtures/prompt_choices.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYTMUX = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PYTMUX)


def _pairs(choices):
    return [[str(c[0]), str(c[1])] if isinstance(c, (list, tuple)) else [str(c), str(c)]
            for c in choices]


def build():
    from pytmuxlib import plugins as py_plugins
    from pytmuxlib.clientutil import COMMAND_OPTIONS, COMMANDS, norm_sep
    reg = py_plugins.load()
    pool = [n for (n, *_rest) in COMMANDS] + [n for (n, *_rest) in reg.commands]
    opts = dict(COMMAND_OPTIONS)
    plugin_names = set((reg.command_options or {}).keys())
    opts.update(reg.command_options or {})
    rows = []
    for name in sorted(opts):
        specs = opts[name]
        if not specs:
            continue
        qs = norm_sep(name.lower())
        matches = [n for n in pool if qs in norm_sep(n.lower())]
        # 정본 `_refresh_cands`: 맞는 것이 정확히 하나이고 그게 입력과 같으면 후보를 숨긴다.
        hidden = len(matches) == 1 and norm_sep(matches[0].lower()) == qs
        # 완성 명령이어야 한다(`_cmd_desc` — 풀에 있는 이름).
        known = name in pool
        choices = _pairs(specs[0].get("choices") or ())
        rows.append({
            "name": name,
            "plugin": name in plugin_names,
            "choice_mode": bool(hidden and known and choices),
            "choices": choices,
            "lines": [f"{name} {v}" if v else name for _d, v in choices],
        })
    if len(rows) < 20:
        sys.exit(f"선택지 명령이 {len(rows)} 개뿐이다 — 정본 표를 못 읽었다")
    return {
        "_comment": "python3 scripts/gen_prompt_choices.py 로 생성. 출처 = "
                    "clientutil.COMMAND_OPTIONS·COMMANDS + plugins command_options·commands "
                    "(정본 PromptScreen._refresh_hint 의 선택지 모드 · pytmux-634).",
        "rows": rows,
        # 플러그인 명령 풀 — GUI 는 이것을 서버 표면(`plugin_surface.commands`)으로 받는다.
        "plugin_commands": sorted(n for (n, *_rest) in reg.commands),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        HERE, "..", "crates", "proto", "tests", "fixtures", "prompt_choices.json"))
    args = ap.parse_args()
    data = build()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    n = sum(1 for r in data["rows"] if r["choice_mode"])
    print(f"{args.out}: 선택지 명령 {len(data['rows'])} · 선택지 모드 {n}")


if __name__ == "__main__":
    main()
