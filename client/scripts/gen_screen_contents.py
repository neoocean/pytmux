#!/usr/bin/env python3
"""정본 판의 **제목·꼬리줄**과 인자 폼 판의 **줄**을 픽스처로 뽑는다 (pytmux-571).

# 왜 필요한가

판은 이름이 있지만 판 «안» 은 이름이 없다. 키 반응(`screen_key_conformance`)·자리(`screen_anchor_conformance`)
·어휘(`screen_row_conformance`)는 판마다 재는 자가 섰는데 **제목과 꼬리줄(키 안내)** 을 정본과 맞대는 자는 0 이었다
(`screens_tests` 는 «비어 있지 않다» 만 본다). 안내가 틀리면 도움말이 없느니만 못하다(`Screen::hint` 머리말).

# 무엇을 뽑나 (손으로 적은 기대값 0)

- `screens` — `pytmuxlib/clientscreens.py` 의 판 클래스마다 몸통을 **AST 로** 걸어 `i18n.t("<열쇠>")` 중
  제목(`…title`)·꼬리줄(`…_sub`·`…hint`) 열쇠를 모으고, 그 글을 **로케일 둘**(ko·en)로 푼다.
  `{n}`·`{cmd}` 같은 자리표는 글 그대로 둔다(시험이 와일드카드로 읽는다). 판이 고정 글 대신 부르는 쪽에서
  제목을 받으면(`InfoScreen(title=…)`) 그 판은 `null` 이다 — 시험이 «잴 고정 글 없음» 으로 센다.
- `options` — 정본 `clientutil.COMMAND_OPTIONS`(인자 폼 판 `CommandOptionsScreen` 의 줄)를 명령마다
  (칸 이름, [(낱말, 값)…]) 차례 그대로. GUI `base::COMMAND_OPTIONS` 가 같은 줄을 같은 차례로 갖는지 잰다.

    python3 scripts/gen_screen_contents.py [--pytmux ..]
"""

import argparse
import ast
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "screen_contents.json")


def keys_by_class(src):
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        found = []
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "t"
                    and getattr(sub.func.value, "id", None) == "i18n" and sub.args
                    and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str)):
                found.append(sub.args[0].value)
        out[node.name] = found
    return out


def pick(keys, kind):
    """판의 제목·꼬리줄 열쇠 하나 — `screen.`/`plugins.`/`compose.` 판 열쇠를 먼저, 꼬리는 `_esc` 변형을 뺀다."""
    if kind == "title":
        cands = [k for k in keys if k.endswith("title")]
    else:
        cands = [k for k in keys if (k.endswith("_sub") or k.endswith("hint")) and not k.endswith("_esc")]
    cands.sort(key=lambda k: (not k.startswith(("screen.", "plugins.", "compose.")), keys.index(k)))
    return cands[0] if cands else None


# ── 판 «안» 의 줄(pytmux-647) — 정본 판을 **고정 자료**로 지어 줄을 뽑는다 ─────────────────────
#
# 자료는 픽스처에 같이 싣는다 — Rust 시험이 **같은 자료**를 `SessionState` 에 먹인다(손으로 적은 기대값 0).
INPUTS = {
    "buffers": [{"i": 0, "preview": "첫 버퍼"}, {"i": 1, "preview": "[x] two"}],
    "layouts": ["work", "play"],
    # 같은 호스트(office)의 다른 원격 탭만 후보다 — 활성 탭 · 로컬 · 다른 호스트는 빠져야 한다.
    "merge_tabs": [
        {"index": 0, "name": "⇄office:a", "active": True, "remote": True},
        {"index": 1, "name": "⇄office:b", "remote": True},
        {"index": 2, "name": "local"},
        {"index": 3, "name": "⇄home:c", "remote": True},
        {"index": 4, "name": "⇄office:d", "remote": True},
    ],
    "shell": ["a\nb\n", "a\r\nb", "x\ry", "\n".join(f"l{i}" for i in range(45)), "   "],
    "search": {"t": "search_results", "query": "err", "panes": 2, "items": [
        {"win": 0, "tab": "sh", "title": "zsh", "line": 12, "text": "error here"},
        {"win": 1, "gwin": 3, "tab": "⇄h:x", "title": "t", "line": 5, "text": "remote err", "remote": True},
        {"win": 2, "tab": "py", "title": "", "text": "no line"},
    ]},
    "blocks": [{"cmd": f"cmd{i}", "cwd": "/w" if i % 2 else "", "state": "done", "exit": 0 if i else 1}
               for i in range(7)],
    "binds": {"x": "split-window -h"},
}


class _Rec:
    """Textual 위젯 대신 서는 기록 스텁 — `with` 컨테이너도 받는다."""

    kind = "?"

    def __init__(self, *children, **kw):
        self.children = list(children)
        self.kw = kw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _texts(node, out):
    if isinstance(node, _Rec):
        if node.kind == "Label" and node.children:
            first = node.children[0]
            out.append(getattr(first, "plain", None) or str(first))
        for child in node.children:
            _texts(child, out)


def _rows_of(cs, screen):
    """판 하나의 `compose()` 를 스텁 위에서 돌려 Label 글을 차례대로."""
    names = ("Label", "ListItem", "ListView", "Static", "Vertical", "Horizontal", "_CycleListView")
    real = {n: getattr(cs, n) for n in names if hasattr(cs, n)}
    for n in real:
        setattr(cs, n, type(n, (_Rec,), {"kind": n}))
    try:
        out = []
        for node in screen.compose():
            _texts(node, out)
        return out
    finally:
        for n, v in real.items():
            setattr(cs, n, v)


def _in_locale(i18n, loc, fn):
    i18n.set_locale(loc)
    try:
        return fn()
    finally:
        i18n.set_locale("ko")


def canon_rows(scratch):
    import asyncio
    from pytmuxlib import clientscreens as cs, clientcmd, keymap
    from pytmuxlib.client import build_client_app
    from pytmuxlib.plugins.blocks import clientside as blocks_side

    cfg = os.path.join(scratch, "config")
    from pytmuxlib import i18n
    loc = i18n.get_locale()
    app = build_client_app(os.path.join(scratch, "x.sock"), keymap.load_config(cfg))
    if loc:
        i18n.set_locale(loc)   # 앱을 지으면 환경 로케일로 돌아간다 — 부른 쪽이 정한 것으로 되돌린다
    pushed = []
    app.push_screen = lambda scr, *a, **k: pushed.append(scr)
    app.send_cmd = lambda *a, **k: None
    app.display_message = lambda *a, **k: None
    out = {"inputs": INPUTS}

    out["buffers"] = {"items": _rows_of(cs, cs.ChooseBufferScreen(INPUTS["buffers"])),
                      "empty": len(_rows_of(cs, cs.ChooseBufferScreen([])))}
    out["saved_layouts"] = {"items": _rows_of(cs, cs.ChooseLayoutScreen(INPUTS["layouts"], "T")),
                            "empty": len(_rows_of(cs, cs.ChooseLayoutScreen([], "T")))}

    app.tabbar.tabs = [dict(t) for t in INPUTS["merge_tabs"]]
    app.status.windows = [dict(t) for t in INPUTS["merge_tabs"]]   # 활성 원격 탭의 호스트는 status 가 정한다
    pushed.clear()
    app.merge_remote_tab_picker()
    out["merge_remote"] = _rows_of(cs, pushed[-1]) if pushed else None

    shell = []
    real_capture = clientcmd._shell_capture
    try:
        for text in INPUTS["shell"]:
            clientcmd._shell_capture = lambda cmd, env, _t=text: (_t, 0)
            app.run_worker = lambda coro, **kw: asyncio.run(coro)
            pushed.clear()
            app._run_shell("x")
            shell.append(list(pushed[-1]._lines) if pushed else None)
    finally:
        clientcmd._shell_capture = real_capture
    out["shell"] = shell

    pushed.clear()
    app.bindings = {k: v for k, v in INPUTS["binds"].items()}
    app.root_bindings = {}
    app._run_command("list-keys")
    out["keys"] = list(pushed[-1]._lines) if pushed else None
    # 절의 차례(pytmux-656) — `list-keys` 판의 머리줄과 설정 판 «키» 탭의 소제목을 정본 열쇠로 이름 짓는다.
    heads = {i18n.t("keys.mouse_header", default="마우스 제스처"): "mouse",
             i18n.t("keys.user_header", default="사용자 키 바인딩"): "user"}
    out["keys_sections"] = [heads[ln] for ln in (out["keys"] or []) if ln in heads]
    scr = cs.SettingsScreen.__new__(cs.SettingsScreen)
    scr._cats, scr._cat_first, scr._flat = [], {}, []
    scr._prefix_key = "ctrl+b"
    scr._user_bindings = dict(INPUTS["binds"])
    scr._root_bindings = {}
    scr._build_keys_category()
    subs = [d["sub"] for d, _ in scr._flat if d.get("type") == "keyref" and "sub" in d]
    named = []
    for sub in subs:
        if sub == i18n.t("klist.sub_esc"):
            named.append("esc")
        elif sub == i18n.t("klist.sub_user"):
            named.append("user")
        elif sub == i18n.t("klist.sub_user_root"):
            named.append("user")          # GUI 는 root 바인딩을 사용자 절 안에 싣는다
        else:
            named.append("prefix")        # `klist.sub_prefix` 는 prefix 키 이름을 품는다
    out["settings_key_sections"] = named

    pushed.clear()
    app._open_status_tabs({"t": "tree", "sessions": []})
    out["info_tab_names"] = [name for name, _ in pushed[-1]._tabs] if pushed else None

    scr = cs.SearchResultsScreen(INPUTS["search"])
    out["search"] = {
        "cells": [[scr._tab_of(it), str(it.get("title") or ""), scr._line_of(it), str(it.get("text", ""))]
                  for it in INPUTS["search"]["items"]],
        "empty": len(_rows_of(cs, cs.SearchResultsScreen({"t": "search_results", "items": []}))),
    }

    # 버전 판 — 업타임은 시각이라 글이 아니라 **줄의 모양**(몇 줄 · 빈 줄 자리 · 머리줄)을 적는다.
    pushed.clear()
    app._show_version_popup({"t": "version", "version": "p4:123", "uptime": 5, "pid": 42})
    lines = list(pushed[-1]._lines) if pushed else []
    out["version"] = {"count": len(lines), "blank": [i for i, ln in enumerate(lines) if not ln],
                      "head": lines[0] if lines else None,
                      "has": ["123", "42"]}

    blocks_side.handle_message(app, {"t": "blocks", "pane": 1, "blocks": INPUTS["blocks"]})
    out["summary"] = blocks_side.summary_lines(app, 1)
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import tempfile
    scratch = tempfile.mkdtemp(prefix="pytmux-screen-contents-")
    open(os.path.join(scratch, "config"), "w").close()
    # ⛔ 이 상자의 진짜 상태·설정을 안 읽는다 — import 전에 세운다.
    os.environ["PYTMUX_HOME"] = scratch
    os.environ["PYTMUX_CONFIG"] = os.path.join(scratch, "config")
    os.environ.pop("NO_COLOR", None)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()
    root = os.path.abspath(args.pytmux)
    sys.path.insert(0, root)
    from pytmuxlib import i18n, clientutil as cu

    src = open(os.path.join(root, "pytmuxlib", "clientscreens.py"), encoding="utf-8").read()
    by_class = keys_by_class(src)

    def both(key):
        if key is None:
            return None
        out = {}
        for loc in ("ko", "en"):
            i18n.set_locale(loc)
            out[loc] = i18n.t(key)
        i18n.set_locale("ko")
        return out

    screens = {}
    for name, keys in sorted(by_class.items()):
        if not name.endswith("Screen") or name.startswith("_"):
            continue
        tk, hk = pick(keys, "title"), pick(keys, "hint")
        screens[name] = {"title_key": tk, "title": both(tk), "hint_key": hk, "hint": both(hk)}
    if len(screens) < 15:
        sys.exit(f"정본 판이 {len(screens)} 개만 잡혔다 — clientscreens 모양이 바뀌었다")

    options = {}
    for cmd, specs in cu.COMMAND_OPTIONS.items():
        options[cmd] = [{"label": s["label"], "choices": [[lab, val] for lab, val in s["choices"]]}
                        for s in specs]

    payload = {
        "_comment": "python3 scripts/gen_screen_contents.py 로 생성. 출처 = pytmuxlib/clientscreens.py 판 클래스의 "
                    "i18n 열쇠(AST) · i18n.t 로 푼 ko/en · clientutil.COMMAND_OPTIONS.",
        "screens": screens,
        "options": options,
        "rows": {loc: _in_locale(i18n, loc, lambda: canon_rows(scratch)) for loc in ("ko", "en")},
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"판 {len(screens)} · 인자 폼 {len(options)} → {args.out}")


if __name__ == "__main__":
    main()
