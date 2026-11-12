#!/usr/bin/env python3
"""정본 **트리 판**과 **탭 스위처**의 줄을 픽스처로 뽑는다 (pytmux-593 · pytmux-620).

재구현하지 않고 정본을 **직접 돌린다**:

- 트리: `ChooseTreeScreen(tree).compose()` — Textual 위젯(`Label`·`ListItem`·`ListView`)을
  기록 스텁으로 갈아 끼우고 나온 줄 글과 `entries`(고르면 어디로 가나)를 적는다.
- 스위처: `PytmuxApp.open_tab_switcher` → `_fill_tab_switcher_panes` →
  `TabSwitcherScreen.add_panes` 를 가짜 `self` 로 차례로 부른다. 세 함수가 나눠 갖는 규칙
  (시각 차례 · 내 세션만 · 원격 탭은 status 요약 · 패널 줄 서식)이 전부 그 안에 있다.

Rust 시험(`tree_row_conformance.rs`·`tab_switcher_conformance.rs`)이 같은 입력을
`SessionState` 에 먹여 `tree_rows`·`switcher_rows` 가 같은 줄을 내는지 맞댄다.

    python3 scripts/gen_tree_switcher.py [--pytmux ..]
"""

import argparse
import asyncio
import json
import os
import sys
from types import SimpleNamespace

OUT = os.path.join("crates", "proto", "tests", "fixtures", "tree_switcher.json")

# 트리 입력 — 세션 둘(같은 index 창) · 패널 하나/둘 · cmd 빈 · 제목 shell/앱과 같음 · 원격 · 핀.
TREE = {"t": "tree", "sessions": [
    # ⚠ 남의 세션을 **먼저** 둔다 — 세션 거르기를 빼면 첫 일치가 남의 창이 되어 시험이 운다.
    {"name": "other", "windows": [
        {"index": 1, "name": "남의 창", "panes": [
            {"id": 90, "cmd": "python3", "title": ""},
            {"id": 91, "cmd": "node", "title": ""}]},
    ]},
    {"name": "main", "windows": [
        {"index": 0, "name": "쉘", "active": True,
         "panes": [{"id": 1, "cmd": "bash", "title": "shell"}]},
        {"index": 1, "name": "일", "pinned": True, "panes": [
            {"id": 2, "cmd": "vim", "title": "vim"},
            {"id": 3, "cmd": "ssh", "title": "office", "remote": True},
            {"id": 4, "cmd": "", "title": ""}]},
        {"index": 2, "name": "로그", "panes": [
            {"id": 5, "cmd": "tail", "title": " 서버 로그 "},
            {"id": 6, "cmd": "htop", "title": "htop"}]},
    ]}
]}

# 탭바(status windows) — 고정 탭이 비고정 사이에 끼어 있고, 원격 탭(비고정·index 큼)이 뒤에.
TABS = [
    {"index": 0, "name": "쉘", "active": True},
    {"index": 1, "name": "일", "pinned": True},
    {"index": 2, "name": "로그"},
    {"index": 3, "name": "⇄office:빌드", "remote": True, "panes": [
        {"id": 7, "cmd": "make", "title": "make", "remote": False},
        {"id": 8, "cmd": "ssh", "title": "db", "remote": True}]},
    {"index": 4, "name": "⇄office:한칸", "remote": True, "panes": None},
]


class _Rec:
    """`Label`·`ListItem`·`ListView` 대신 서는 기록 스텁."""

    def __init__(self, *children, **kw):
        self.children = list(children)
        self.kw = kw


def tree_rows(cs):
    real = (cs.Label, cs.ListItem, cs.ListView)
    cs.Label, cs.ListItem, cs.ListView = _Rec, _Rec, _Rec
    try:
        scr = cs.ChooseTreeScreen(TREE)
        lv = list(scr.compose())[0]
    finally:
        cs.Label, cs.ListItem, cs.ListView = real
    rows = []
    for item, entry in zip(lv.children, scr.entries):
        label = item.children[0].children[0]
        rows.append({"label": label, "kind": entry["kind"], "window": entry["index"],
                     "pane": entry.get("pid")})
    if len(rows) != len(scr.entries):
        sys.exit("트리 줄과 entries 수가 다르다 — 정본 compose 모양이 바뀌었다")
    return rows


def switcher_rows(client_mod, cs):
    pushed = {}
    later = []
    fake = SimpleNamespace(
        tabbar=SimpleNamespace(tabs=[dict(t) for t in TABS], blink_active=lambda: None),
        push_screen=lambda scr, handle=None: pushed.setdefault("scr", scr),
        request_tree=lambda purpose=None: None,
        status=SimpleNamespace(session="main"),
        session_name="main",
        call_later=lambda fn, *a: later.append(a),
    )
    App = client_mod._ChooseScreensMixin
    App.open_tab_switcher(fake)
    entries = pushed["scr"]._entries
    initial = pushed["scr"]._initial
    fake._tabsw_screen = SimpleNamespace(is_attached=True, add_panes=None)
    App._fill_tab_switcher_panes(fake, TREE)
    (arg,) = later[0]

    class _LV:
        index = 0

        async def clear(self):
            pass

        async def extend(self, _rows):
            pass

    stub = SimpleNamespace(is_attached=True, _entries=entries, _rows=lambda: [])
    stub.query_one = lambda _cls: _LV()
    asyncio.run(cs.TabSwitcherScreen.add_panes(stub, arg))
    out = []
    for e in stub._entries:
        out.append({"label": e["label"], "kind": e["kind"], "window": e["index"],
                    "pane": e.get("pid"), "active": bool(e.get("active"))})
    return out, initial


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()
    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib import client as client_mod
    from pytmuxlib import clientscreens as cs

    tree = tree_rows(cs)
    switcher, initial = switcher_rows(client_mod, cs)
    if not any(r["kind"] == "pane" for r in switcher):
        sys.exit("스위처에 패널 줄이 하나도 안 붙었다 — 뽑는 방법이 틀렸다")
    payload = {
        "_comment": "python3 scripts/gen_tree_switcher.py 로 생성. 출처 = 정본 "
                    "ChooseTreeScreen.compose · open_tab_switcher · _fill_tab_switcher_panes · "
                    "TabSwitcherScreen.add_panes 를 직접 돌린 값.",
        "tree_input": TREE,
        "tabs_input": TABS,
        "session": "main",
        "tree_rows": tree,
        "switcher_rows": switcher,
        "switcher_initial": initial,
    }
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"트리 {len(tree)} 줄 · 스위처 {len(switcher)} 줄 → {args.out}")


if __name__ == "__main__":
    main()
