#!/usr/bin/env python3
"""세션 뷰 **키 차례** 를 정본에서 뽑는다 — 평소 모드의 크롬 키 · 바인딩의 자리 · 시간 규칙 · 정규화 표.

# 왜 필요한가 (pytmux-606 · 607 · 610 · 583)

`gen_client_surface_fixture.py` 의 `esc_key_modes`·`prefix_key_modes` 는 **모드 안**의
키를 잰다. 그 바깥 — 평소 모드에서 정본 `clientio._InputMixin.on_key` 가 패널로 안
보내고 **제 것으로 쓰는** 키(백틱 · F10 · F12 · Alt+숫자 …) — 는 어느 자도 안 쟀고,
GUI 는 그 키를 전부 패널로 흘리고 있었다(pytmux-606). 같은 자리에서 사용자 바인딩이
**언제** 보이는지(607), 패널 번호가 **몇 초** 떠 있는지(610), 모드 안에서 한글 자모·
맥 Option 숫자열이 **어떻게** 풀리는지(583)도 손으로 적은 값뿐이었다.

그래서 이 넷을 **정본 소스**에서 뽑는다. 정본이 키를 하나 더 잡으면 이 픽스처가 늘고,
`proto/tests/mode_transition_conformance.rs` 가 GUI 에 같은 키가 걸릴 때까지 운다.

# 무엇을 뽑나

| 키 | 출처 | 뜻 |
|---|---|---|
| `normal_key_modes` | `on_key` 의 `# normal` 뒤 `if` 사슬 | 평소 모드에서 그 키를 누르면 **무엇을 하나**(`does`) |
| `normal_alt_digit` | 같은 자리의 `_alt_digit` 식 | `alt+<숫자>` 가 탭 전환인가 |
| `root_bindings_after` | 같은 자리에서 `self.root_bindings` 앞에 놓인 키 | `bind -n` 보다 **먼저** 보는 내장 키 |
| `prefix_order` | `_handle_prefix` 의 문장 차례 | prefix 두 번 · 사용자 표 · 내장(F12) 의 차례 |
| `display_secs` | `_enter_display` 의 `set_timer` | `prefix q` 번호가 저절로 닫히는 초 |
| `esc_debounce_secs` | `client.py` 의 `self._ESC_DEBOUNCE = …` | ESC 오토리핏 창(초) |
| `mode_normalize` | 각 모드 핸들러가 부르는 정규화 | 모드마다 자모·Option 글자를 푸나 |
| `jamo` · `mac_opt_number_row` | `clientutil._JAMO` · `MAC_OPT_NUMBER_ROW` | 정규화 표 그대로 |

    python3 scripts/gen_session_keys_fixture.py [--pytmux ..] [--out …]
"""

import argparse
import ast
import json
import os
import re
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "session_keys.json")


def _utf8_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _parse(root, *rel):
    path = os.path.join(root, *rel)
    with open(path, encoding="utf-8") as fp:
        src = fp.read()
    return src, ast.parse(src, filename=path)


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    sys.exit(f"{name} 가 없다 — 뽑는 방법이 틀렸다")


def _key_literals(test):
    """`event.key == "x"` · `event.key in (…)` · `event.character == "x"` 의 문자열들."""
    out = []
    for node in ast.walk(test):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Attribute) and left.attr in ("key", "character")):
            continue
        for op, comp in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant):
                if isinstance(comp.value, str):
                    out.append((left.attr, comp.value))
            elif isinstance(op, ast.In) and isinstance(comp, (ast.Tuple, ast.List)):
                for elt in comp.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        out.append((left.attr, elt.value))
    return out


def _calls(stmts):
    names = []
    for node in ast.walk(ast.Module(body=list(stmts), type_ignores=[])):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.append(node.func.attr)
    return names


def _does(test, body):
    """이 갈래가 **무엇을 하나** — 몸이 부르는 것을 한 낱말로 접는다.

    낱말은 GUI 쪽 대조 표(`mode_transition_conformance.rs` `NORMAL_DOES`)가 읽는다.
    모르는 몸이면 생성기가 **죽는다** — 새 갈래를 조용히 `?` 로 적으면 그 키는 아무도 안 잰다.
    """
    calls = _calls(body)
    sets_esc = any(
        isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
        and n.value.value == "esc"
        and any(isinstance(t, ast.Attribute) and t.attr == "mode" for t in n.targets)
        for n in ast.walk(ast.Module(body=list(body), type_ignores=[]))
    )
    if sets_esc:
        return "esc"
    if "open_menu" in calls:
        return "menu"
    if "open_prompt" in calls:
        return "command_prompt"
    if "paste_os_clipboard" in calls:
        return "paste"
    # `event.key == "shift+escape" and self._close_active_overlay()` — 닫을 것이 없으면
    # 아래로 흘러 패널에 ESC 가 간다.
    if "_close_active_overlay" in _calls([ast.Expr(test)]):
        return "close_overlay_else_pane"
    sys.exit(f"on_key 평소 갈래의 몸을 못 읽었다(부르는 것: {calls}) — 대조 낱말을 더할 것")


def normal_keys(tree):
    fn = _func(tree, "on_key")
    body = fn.body
    # `if self.mode not in (...)` (플러그인 모드) 다음부터가 평소 모드다.
    start = None
    for i, st in enumerate(body):
        if isinstance(st, ast.If) and "not in" in ast.unparse(st.test) and "mode" in ast.unparse(st.test):
            start = i + 1
    if start is None:
        sys.exit("on_key 에서 평소 모드의 시작을 못 찾았다")
    found = {}
    order = []
    alt_digit = False
    root_after = None
    for st in body[start:]:
        if isinstance(st, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_alt_digit" for t in st.targets
        ):
            src = ast.unparse(st.value)
            alt_digit = "startswith('alt+')" in src and "isdigit()" in src
            continue
        if not isinstance(st, ast.If):
            continue
        test_src = ast.unparse(st.test)
        if "root_bindings" in test_src:
            root_after = list(order)
            continue
        if "_alt_digit" in test_src:
            if not alt_digit:
                sys.exit("_alt_digit 식을 못 읽었다")
            found["alt+<digit>"] = "select_tab"
            order.append("alt+<digit>")
            continue
        lits = _key_literals(st.test)
        if not lits:
            continue
        does = _does(st.test, st.body)
        for attr, value in lits:
            # `event.character == "`"` 는 `grave_accent` 와 같은 갈래의 별칭이다.
            name = value if attr == "key" else f"char:{value}"
            found.setdefault(name, does)
            if name not in order:
                order.append(name)
    if root_after is None:
        sys.exit("on_key 에서 root_bindings 자리를 못 찾았다")
    if len(found) < 5:
        sys.exit(f"평소 모드 키가 너무 적다({found}) — 뽑기가 깨졌다")
    return dict(sorted(found.items())), alt_digit, root_after


def prefix_order(tree):
    """`_handle_prefix` 의 차례 — prefix 두 번 · 사용자 표 · 내장 F12."""
    fn = _func(tree, "_handle_prefix")
    marks = []
    for st in fn.body:
        if not isinstance(st, ast.If):
            continue
        src = ast.unparse(st.test)
        if "prefix_key" in src:
            marks.append("prefix_twice")
        elif "self.bindings" in src:
            marks.append("user_bindings")
        elif "'f12'" in src:
            marks.append("f12_toggle")
            if "prefix_enabled" not in ast.unparse(ast.Module(body=st.body, type_ignores=[])):
                sys.exit("prefix F12 갈래가 prefix_enabled 를 안 뒤집는다 — 뜻이 바뀌었다")
    if marks[:3] != ["prefix_twice", "user_bindings", "f12_toggle"]:
        sys.exit(f"_handle_prefix 차례를 못 읽었다: {marks}")
    return marks


def display_secs(tree):
    fn = _func(tree, "_enter_display")
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_timer" and node.args
                and isinstance(node.args[0], ast.Constant)):
            return float(node.args[0].value)
    sys.exit("_enter_display 의 타이머를 못 찾았다")


def esc_debounce(src):
    m = re.search(r"self\._ESC_DEBOUNCE\s*=\s*([0-9.]+)", src)
    if not m:
        sys.exit("client.py 에서 _ESC_DEBOUNCE 를 못 찾았다")
    return float(m.group(1))


def mode_normalize(tree):
    """모드 핸들러마다 자모(`jamo`)·Option 글자(`mac_opt`)를 푸나."""
    out = {}
    for mode, func in (("esc", "_handle_esc_mode"), ("prefix", "_handle_prefix"),
                       ("scroll", "_handle_scroll_key")):
        src = ast.unparse(_func(tree, func))
        out[mode] = {
            "jamo": any(s in src for s in ("hangul_to_qwerty", "_JAMO", "_normalize_key")),
            "mac_opt": "MAC_OPT_NUMBER_ROW" in src,
        }
    return out


def main():
    _utf8_stdout()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()
    root = os.path.abspath(args.pytmux)
    sys.path.insert(0, root)
    from pytmuxlib import clientutil as cu

    _io_src, io_tree = _parse(root, "pytmuxlib", "clientio.py")
    client_src, _ = _parse(root, "pytmuxlib", "client.py")
    keys, alt_digit, root_after = normal_keys(io_tree)
    payload = {
        "_comment": "python3 scripts/gen_session_keys_fixture.py 로 생성. 출처 = "
                    "pytmuxlib/clientio.py(on_key·_handle_prefix·_enter_display·모드 핸들러) "
                    "+ client.py(_ESC_DEBOUNCE) + clientutil.py(_JAMO·MAC_OPT_NUMBER_ROW).",
        "normal_key_modes": keys,
        "normal_alt_digit": alt_digit,
        "root_bindings_after": root_after,
        "prefix_order": prefix_order(io_tree),
        "display_secs": display_secs(io_tree),
        "esc_debounce_secs": esc_debounce(client_src),
        "mode_normalize": mode_normalize(io_tree),
        "jamo": dict(sorted(cu._JAMO.items())),
        "mac_opt_number_row": dict(sorted(cu.MAC_OPT_NUMBER_ROW.items())),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{args.out} — 평소 키 {len(keys)} · 바인딩 앞 {len(root_after)} · "
          f"자모 {len(payload['jamo'])} · Option {len(payload['mac_opt_number_row'])}")


if __name__ == "__main__":
    main()
