#!/usr/bin/env python3
"""정본 플러그인 **클라 훅 × 플러그인** 전수를 픽스처로 뽑는다 (pytmux-570).

# 왜 필요한가

정본 클라의 플러그인 훅은 파이썬 객체를 주고받아 소켓을 못 건넌다. GUI 에는 ⓐ 선언형 다리
(`plugin_surface`·`plugin_cells`·`plugin_badges`·`plugin_screen` …)로 건너온 것과 ⓑ 손으로 옮긴 것만 있다.
`plugin_screen_conformance` 같은 자는 «건너온 것이 맞나» 를 잰다 — **«안 건너온 것이 있나»** 를 재는 자가
없었고, 그래서 플러그인이 훅을 하나 더 쓰기 시작해도 아무도 울지 않았다.

# 어떻게 뽑나 (손으로 적은 이름 0)

- 훅 이름: `pytmuxlib/plugins/__init__.py` 의 `Registry` 를 **AST 로** 읽어 메서드마다 그 안의
  `getattr(p, "<이름>")`·`hasattr(p, "<이름>")` 문자열을 모은다(그 문자열이 플러그인 쪽 훅 이름이다).
- 클라 훅 판정: 정본 클라 모듈(`pytmuxlib/client*.py`)이 `.plugins.<메서드>` 로 부르는 메서드 ∪ 첫 인자가
  `app` 인 메서드(`client_overlay_covers` 처럼 렌더 모듈에서 부르는 것). 나머지는 `server_hooks` 로 따로 싣는다.
- 칸: `_discover()` 의 PLUGIN 마다 그 훅이 **있나** — 호출 가능하면 등록, 자료(`commands` 등)는 비어 있지 않을
  때만, `settings` 는 불러서 비어 있지 않을 때만.
- 다리 이름: 서버 `serverio._plugin_surface` 가 싣는 칸(`plugin_surface.<칸>`) ∪ `Registry` 의 `plugin_*` 메서드
  (서버가 클라에 나르는 선언형 훅). GUI 대장의 `Declared(<다리>)` 가 이 집합 안이어야 한다.

    python3 scripts/gen_plugin_hooks.py [--pytmux ..]
"""

import argparse
import ast
import glob
import json
import os
import re
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "plugin_hooks.json")

# 레지스트리 메서드인데 훅이 아닌 것(관리용) — 이름 그대로 뺀다.
_NOT_HOOKS = {"__init__", "set_disabled", "default_disabled", "plugin_overview", "_blocks_sources", "_reopen"}


def registry_methods(src):
    tree = ast.parse(src)
    reg = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Registry")
    out = {}
    for fn in reg.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name in _NOT_HOOKS:
            continue
        names = set()
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("getattr", "hasattr")
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)):
                names.add(node.args[1].value)
        names.discard("name")
        args = [a.arg for a in fn.args.args]
        if names:
            out[fn.name] = {"attrs": sorted(names), "app_arg": len(args) > 1 and args[1] == "app"}
    return out


def surface_keys(serverio_src):
    tree = ast.parse(serverio_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_plugin_surface":
            for ret in ast.walk(node):
                if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Dict):
                    return sorted(k.value for k in ret.value.keys if isinstance(k, ast.Constant))
    sys.exit("serverio._plugin_surface 의 반환 dict 를 못 찾았다 — 정본 모양이 바뀌었다")


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
    lib = os.path.join(root, "pytmuxlib")

    methods = registry_methods(open(os.path.join(lib, "plugins", "__init__.py"), encoding="utf-8").read())
    called = set()
    for path in glob.glob(os.path.join(lib, "client*.py")):
        called |= set(re.findall(r"\.plugins\.(\w+)", open(path, encoding="utf-8").read()))
    client = {m: v for m, v in methods.items() if m in called or v["app_arg"]}
    server = sorted(m for m in methods if m not in client)

    from pytmuxlib import plugins as P
    found = P._discover()
    names = sorted(getattr(p, "name") for p in found)
    if len(found) < 10:
        sys.exit(f"플러그인이 {len(found)} 개만 잡혔다 — import 가 깨졌을 것이다")

    def registers(plugin, attr):
        value = getattr(plugin, attr, None)
        if value is None:
            return False
        if attr == "settings" and callable(value):
            descs, cats = value()
            return bool(descs) or bool(cats)
        return callable(value) or bool(value)

    cells = []
    hooks = []
    for method in sorted(client):
        for attr in client[method]["attrs"]:
            hooks.append(attr)
            for plugin in sorted(found, key=lambda p: p.name):
                if registers(plugin, attr):
                    cells.append({"hook": attr, "plugin": plugin.name})
    hooks = sorted(set(hooks))
    cells = sorted({(c["hook"], c["plugin"]) for c in cells})

    bridges = ["plugin_surface." + k for k in surface_keys(
        open(os.path.join(lib, "serverio.py"), encoding="utf-8").read())]
    bridges += sorted(m for m in methods if m.startswith("plugin_"))

    payload = {
        "_comment": "python3 scripts/gen_plugin_hooks.py 로 생성. 출처 = pytmuxlib/plugins/__init__.py "
                    "Registry(AST) · pytmuxlib/client*.py 의 호출 · plugins._discover() · serverio._plugin_surface.",
        "plugins": names,
        "client_hooks": hooks,
        "server_hooks": server,
        "cells": [{"hook": h, "plugin": p} for h, p in cells],
        "bridges": bridges,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"플러그인 {len(names)} · 클라 훅 {len(hooks)} · 칸 {len(cells)} → {args.out}")


if __name__ == "__main__":
    main()
