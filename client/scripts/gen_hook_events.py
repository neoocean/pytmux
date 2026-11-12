#!/usr/bin/env python3
"""이벤트 훅 사건 전수 + `claude-limit` 전이 답지 생성기(pytmux-588).

정본이 발화하는 훅 사건은 두 갈래다:

- **코어** — `client.py` 의 `_fire_hook("…")` 호출부(문자열 리터럴). AST 로 전수를 뜬다 —
  손으로 적으면 새 사건이 생겨도 GUI 표가 모른다.
- **플러그인발** — `claude-code` 의 `claude.py:saver_hook_events(prev, msg)` 가 status
  전이에서 `(event, env)` 를 낸다. **그 함수를 직접 불러** 케이스열의 답을 뜬다.

GUI 의 `base::hooks::HookEvent::ALL`·`HookWatcher::saw_claude` 가 같은 답을 내는지
`proto/tests/hook_events_conformance.rs` 가 잰다.

사용: python3 scripts/gen_hook_events.py [--out 경로]
출력: crates/proto/tests/fixtures/hook_events.json
"""
import argparse
import ast
import glob
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYTMUX = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PYTMUX)

claude = importlib.import_module("pytmuxlib.plugins.claude-code.claude")


def core_events():
    """`pytmuxlib/` 전체에서 `_fire_hook(<리터럴>)` 의 리터럴을 모은다(코어 사건)."""
    names = set()
    for path in sorted(glob.glob(os.path.join(PYTMUX, "pytmuxlib", "**", "*.py"),
                                 recursive=True)):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "_fire_hook"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                names.add(node.args[0].value)
    return sorted(names)


def status(active, panes, account=None):
    msg = {"active_pane": active,
           "panes_claude": [{"id": pid, "claude": st} for pid, st in panes]}
    if account is not None:
        msg["claude_account"] = account
    return msg


# 이름 → status 열. 한 케이스 안에서 prev 를 이어 쓴다(정본 `app._saver_prev`).
SEQUENCES = {
    "first_status_already_limit_fires": [status(1, [(1, "limit")], "work")],
    "holding_limit_does_not_refire": [status(1, [(1, "limit")], "a"),
                                      status(1, [(1, "limit")], "a")],
    "fall_and_rise_fires_again": [status(1, [(1, "limit")], "a"),
                                  status(1, [(1, "idle")], "a"),
                                  status(1, [(1, "limit")], "b")],
    "busy_then_limit": [status(1, [(1, "busy")]), status(1, [(1, "limit")])],
    "other_pane_limit_is_ignored": [status(1, [(1, "idle"), (2, "limit")])],
    "active_switches_to_a_limited_pane": [status(1, [(1, "idle"), (2, "limit")]),
                                          status(2, [(1, "idle"), (2, "limit")])],
    "not_claude_pane": [status(1, [(1, None)])],
    "no_account_is_empty_string": [status(1, [(1, "limit")])],
    "missing_panes_resets_the_edge": [status(1, [(1, "limit")], "a"),
                                      {"active_pane": 1},
                                      status(1, [(1, "limit")], "a")],
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        HERE, "..", "crates", "proto", "tests", "fixtures", "hook_events.json"))
    args = ap.parse_args(argv)
    cases = {}
    plugin_events = set()
    for name, seq in SEQUENCES.items():
        prev = {}
        steps = []
        for msg in seq:
            fired = claude.saver_hook_events(prev, msg)
            for ev, _env in fired:
                plugin_events.add(ev)
            steps.append({"status": msg,
                          "fires": [{"event": ev, "env": env} for ev, env in fired]})
        cases[name] = steps
    out = {"core": core_events(), "plugin": sorted(plugin_events), "cases": cases}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print(f"wrote {args.out} ({len(cases)} cases)")


if __name__ == "__main__":
    main()
