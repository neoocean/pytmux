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
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"판 {len(screens)} · 인자 폼 {len(options)} → {args.out}")


if __name__ == "__main__":
    main()
