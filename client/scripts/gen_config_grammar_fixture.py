#!/usr/bin/env python3
"""설정 파일 **읽기** 문법 픽스처 — 같은 설정 파일을 두 클라가 같은 뜻으로 읽나(pytmux-567).

# 왜 이 자가 필요한가

패리티 래칫은 `clientutil.SETTINGS`(설정 판에 뜨는 줄)와 팔레트 이름을 센다. 설정 파일은
사람이 손으로 쓰는 자리라 판에 안 뜨는 문법이 더 많다 — `alias` 줄 · `bind -n` ·
`hook`/`set-hook` · 판에 없는 `set` 옵션(`set status 2` · `status-format` · `mouse-debug`).
그 어느 것도 자가 안 셌다. 이 생성기가 그 모집단을 **정본에서** 뽑는다.

# 무엇을 뽑나

- ⓐ `directives` — 정본 파서(`pytmuxlib/keymap.py::load_config`)가 **가르는 지시어 전수**
  (`parts[0] == "…"` · `parts[0] in (…)` 를 AST 로 긁는다) + `bind` 의 깃발(`parts[1] == "-n"`).
- ⓑ `load_options` — 같은 함수가 `set <옵션>` 으로 받는 **옵션 이름 전수**(별칭 철자 포함).
- ⓒ `apply_options` — 런타임 `set` 이 닿는 `pytmuxlib/client.py` 의 `apply_option`
  (중첩 함수라 AST 로만 닿는다)이 받는 옵션 이름 전수(별칭 포함).
- ⓓ `cases` — 대표 줄마다 **정본이 실제로 읽은 결과**. 그 줄 하나만 든 임시 파일을
  `load_config(path)` 로 **그대로 불러** 빈 파일의 결과와 갈린 칸만 싣는다(`read`).
  런타임에만 받는 옵션(ⓒ − ⓑ)은 파일 파서가 안 읽으므로 `read` 대신 `runtime: true` 를 싣는다
  — GUI 의 런타임 `set` 은 설정 파일에 쓰고 `Config::parse` 로 다시 읽기 때문에, 그 줄을
  `Config::parse` 가 읽나가 곧 GUI 가 그 옵션을 받나다.

⚠ 손으로 적는 것은 **입력**(옵션마다 먹일 값 · `SAMPLES`)뿐이다. 기대값은 전부 정본이 낸다.
AST 가 새 옵션을 찾았는데 `SAMPLES` 에 값이 없으면 **실패로 떨어진다** — 조용히 모집단 밖에
남는 길을 막는다.

    python scripts/gen_config_grammar_fixture.py [--pytmux ..] [--out …]
"""

import argparse
import ast
import json
import os
import sys
import tempfile

# 옵션 하나에 먹일 값 — **기본값이 아닌 것**(두 클라 모두에서). 기본값을 먹이면 「안 읽었다」와
# 「읽었다」가 같은 결과라 자가 아무것도 못 잰다. 열쇠는 하이픈 철자이고 `_` 별칭은 같은 값을 쓴다.
SAMPLES = {
    "prefix": "C-a",
    "mouse": "off",
    "mouse-drag-copy": "shift",
    "set-clipboard": "off",
    "mouse-drag-threshold": "7",
    "copy-unwrap": "off",
    "status-bg": "red",
    "status-fg": "blue",
    "mode-keys": "emacs",
    "status-left": "#S left side",
    "status-right": "#H right side",
    "status-position": "top",
    "remote-title": "host",
    "status-interval": "5",
    "tab-bar": "auto",
    "tabbar": "auto",
    "set-titles": "on",
    "set-titles-string": "#S:#W title",
    "default-path": "home",
    "claude-command": "claude --verbose",
    "ambiguous-width": "wide",
    "lang": "en",
    "language": "en",
    "inactive-dim": "off",
    "inactive-dim-ratio": "0.5",
    "strip-box-drawing": "off",
    "alt-scroll": "off",
    "net-rtt-threshold": "2.5",
    # 런타임에만 받는 것(파일 파서는 안 읽는다).
    "status": "2",
    "status-format": "1 #S extra",
    "mouse-debug": "on",
    "mouse-log": "on",
}

# 지시어 줄 — (열쇠, 줄, 왜 이 칸이 있나). 열쇠로 찾는다(줄 번호로 찾지 않는다).
DIRECTIVE_CASES = [
    ("bind", "bind r source-file", "prefix 뒤 키 — 팔레트 이름 그대로인 명령"),
    ("bind ctrl", "bind C-x split-window -h", "tmux 표기 키(`C-x`)를 정규화해 읽나"),
    ("bind args", "bind y select-tab 3", "인자 있는 명령 — 정본은 아무 명령 줄이나 돌린다"),
    ("bind -n", "bind -n F5 next-tab", "prefix 없이 바로(root table)"),
    ("bind -n args", "bind -n M-z resize-pane -Z", "root table + 플래그를 품은 이름"),
    ("bind -n Enter", "bind -n Enter next-tab", "이름 키 — 대문자 철자(pytmux-600)"),
    ("bind -n PageUp", "bind -n PageUp next-tab", "이름 키 — 낙타 철자(pytmux-600)"),
    ("bind -n PPage", "bind -n PPage next-tab", "이름 키 — tmux 별칭 철자(pytmux-600)"),
    ("bind -n Home", "bind -n Home next-tab", "이름 키(pytmux-600)"),
    ("bind C-Left", "bind C-Left next-tab", "수정키 + 이름 키(pytmux-600)"),
    ("alias", "alias sp split-window -h", "사용자 명령 별칭"),
    ("hook", "hook after-new-window display-message made", "`hook` 철자"),
    ("set-hook", "set-hook client-attached display-message hi", "`set-hook` 철자"),
]


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _strs(comp):
    if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
        return [comp.value]
    if isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
        return [e.value for e in comp.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def _compared(fn, match_left):
    """`fn` 안의 `<왼쪽> == "…"` · `<왼쪽> in (…)` 에서 문자열을 긁는다."""
    found = set()
    for sub in ast.walk(fn):
        if not isinstance(sub, ast.Compare) or not match_left(sub.left):
            continue
        for op, comp in zip(sub.ops, sub.comparators):
            if isinstance(op, (ast.Eq, ast.In)):
                found.update(_strs(comp))
    return sorted(found)


def _is_parts(index):
    def match(node):
        return (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name) and node.value.id == "parts"
                and isinstance(node.slice, ast.Constant) and node.slice.value == index)
    return match


def _is_name(name):
    return lambda node: isinstance(node, ast.Name) and node.id == name


def _parse(root, *rel):
    path = os.path.join(root, *rel)
    with open(path, encoding="utf-8") as fp:
        return path, ast.parse(fp.read(), filename=path)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(".."))
    ap.add_argument("--out", default=os.path.join(
        "crates", "proto", "tests", "fixtures", "config_grammar.json"))
    args = ap.parse_args()
    root = os.path.abspath(args.pytmux)

    path, tree = _parse(root, "pytmuxlib", "keymap.py")
    load = _func(tree, "load_config")
    if load is None:
        sys.exit(f"{path} 에 load_config 가 없다 — 뽑는 방법이 틀렸다")
    directives = _compared(load, _is_parts(0))
    bind_flags = [f for f in _compared(load, _is_parts(1)) if f.startswith("-")]
    load_options = _compared(load, _is_name("opt"))
    cpath, ctree = _parse(root, "pytmuxlib", "client.py")
    apply = _func(ctree, "apply_option")
    if apply is None:
        sys.exit(f"{cpath} 에 apply_option 이 없다 — 뽑는 방법이 틀렸다")
    apply_options = _compared(apply, _is_name("name"))
    # ⓕ `source-file` 이 다시 읽는 칸 — `client.py::reload_config`(중첩 함수)가 `cfg` 에서 꺼내는
    #    열쇠 전수(`cfg["k"]` · `cfg.get("k", …)`). pytmux-574 의 «다시 읽으면 바뀌나» 모집단이다.
    reload = _func(ctree, "reload_config")
    if reload is None:
        sys.exit(f"{cpath} 에 reload_config 가 없다 — 뽑는 방법이 틀렸다")
    reload_keys = set()
    for sub in ast.walk(reload):
        if (isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name)
                and sub.value.id == "cfg" and isinstance(sub.slice, ast.Constant)):
            reload_keys.add(sub.slice.value)
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "get" and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "cfg" and sub.args
                and isinstance(sub.args[0], ast.Constant)):
            reload_keys.add(sub.args[0].value)
        if (isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Constant)
                and any(isinstance(c, ast.Name) and c.id == "cfg" for c in sub.comparators)):
            reload_keys.add(sub.left.value)
    reload_keys = sorted(reload_keys)
    if not reload_keys:
        sys.exit("reload_config 가 읽는 칸을 하나도 못 찾았다 — 뽑는 방법이 틀렸다")
    for label, got in (("지시어", directives), ("set 옵션", load_options),
                       ("apply_option 옵션", apply_options)):
        if not got:
            # 빈 집합을 조용히 돌려주면 그 위의 자가 아무것도 안 재면서 초록이 된다.
            sys.exit(f"{label} 를 하나도 못 찾았다 — 뽑는 방법이 틀렸다")

    def sample(opt):
        v = SAMPLES.get(opt) or SAMPLES.get(opt.replace("_", "-"))
        if v is None:
            sys.exit(f"옵션 {opt!r} 에 먹일 값이 SAMPLES 에 없다 — 기본값이 아닌 값을 더할 것")
        return v

    # 정본을 **그대로 부른다**. ⛔ 사용자의 진짜 설정 파일을 안 읽게 격리한다 —
    # `load_config` 는 명시 경로가 있어도 `_migrate_config_to_home()` 을 부르고, 그 함수는
    # `PYTMUX_HOME/config` 가 없으면 흩어진 설정 파일을 **복사해 온다**.
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        open(os.path.join(home, "config"), "w", encoding="utf-8").close()
        os.environ["PYTMUX_HOME"] = home
        os.environ.pop("PYTMUX_CONFIG", None)
        os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "xdg")
        sys.path.insert(0, root)
        from pytmuxlib.keymap import load_config  # noqa: E402

        def read(line):
            p = os.path.join(tmp, "case.conf")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
            return load_config(p)

        base = read("# 빈 파일")
        if load_config(os.path.join(tmp, "case.conf")) != base:
            sys.exit("load_config 가 같은 파일을 두 번 다르게 읽었다")

        def delta(line):
            got = read(line)
            return {k: _jsonable(v) for k, v in sorted(got.items()) if base.get(k) != v}

        # ⓔ 이름 키 — 정본 `_tmux_key_to_textual` 이 접는 철자 전수(pytmux-600). 토큰은 정본 표
        #    `_NAMED_KEYS` 의 열쇠마다 낙타·대문자 철자 + F-키 + 수정키 조합이다. 기대값은 정본이 낸다.
        from pytmuxlib import keymap as _km  # noqa: E402
        tokens = []
        for k in sorted(_km._NAMED_KEYS):
            tokens += [k.capitalize(), k.upper()]
        tokens += ["F1", "F5", "f12", "C-Left", "M-PageUp", "C-M-Home", "C-F5", "S-Tab",
                   "C-S-Tab", "M-Enter", "C-x", "M-q", "q", "G"]
        named_keys = []
        for tok in dict.fromkeys(tokens):
            named_keys.append({"token": tok, "canon": _km._tmux_key_to_textual(tok)})
        named_table = sorted(_km._NAMED_KEYS)

        cases = []
        seen_directives = set()
        for key, line, why in DIRECTIVE_CASES:
            seen_directives.add(line.split()[0])
            cases.append({"key": key, "line": line, "why": why, "read": delta(line)})
        for opt in load_options:
            seen_directives.add("set")
            line = f"set {opt} {sample(opt)}"
            cases.append({"key": f"set {opt}", "line": line,
                          "why": "정본 파일 파서가 받는 옵션", "read": delta(line)})
        for opt in sorted(set(apply_options) - set(load_options)):
            line = f"set {opt} {sample(opt)}"
            got = delta(line)
            if got:
                sys.exit(f"{opt!r} 는 apply_option 에만 있다고 봤는데 파일 파서가 읽었다: {got}")
            cases.append({"key": f"set {opt}", "line": line, "runtime": True,
                          "why": "런타임 `set` 만 받는 옵션(정본 `apply_option`)"})
    missing = sorted(set(directives) - seen_directives)
    if missing:
        sys.exit(f"지시어 {missing} 에 대표 줄이 없다 — DIRECTIVE_CASES 에 더할 것")
    for case in cases:
        if not case.get("runtime") and not case["read"]:
            sys.exit(f"{case['line']!r} 를 정본이 아무것도 안 읽었다 — 기본값을 먹였나")

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "_comment": "python3 scripts/gen_config_grammar_fixture.py 로 생성. "
                        "출처 = pytmuxlib/keymap.py::load_config(호출) + "
                        "pytmuxlib/client.py::apply_option(AST). pytmux-567.",
            "directives": directives,
            "bind_flags": bind_flags,
            "load_options": load_options,
            "apply_options": apply_options,
            "cases": cases,
            "named_key_table": named_table,
            "reload_keys": reload_keys,
            "named_keys": named_keys,
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"지시어 {len(directives)} · set 옵션 {len(load_options)} · "
          f"apply_option {len(apply_options)} · 칸 {len(cases)} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
