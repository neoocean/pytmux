#!/usr/bin/env python3
"""정본 플러그인 관리 판의 **이름 검색**(`PluginManagerScreen._matches`)을 픽스처로 뽑는다
(pytmux-591).

정본은 글자를 치면 이름(구분자 무시)·설명으로 줄을 거른다. GUI 판에는 그 검색이 없었다.
이 생성기는 `_matches` 를 **직접 불러** 고정 목록 × 검색어마다 걸린 이름을 적고, Rust
시험(`plugin_filter_conformance.rs`)이 `base::screens::plugin_filter` 로 같은 답이 나오는지
맞댄다. 목록은 실제 플러그인 개요(`plugin_overview`)에 고정 줄 몇을 더한 것이다 — 실제
목록만 쓰면 구분자·설명 일치 같은 갈래가 우연히 안 걸릴 수 있다.

    python3 scripts/gen_plugin_filter.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "plugin_filter.json")

FIXED = [
    ("p4-show-submitted-changelists", "p4 제출된 변경 목록", "기타", True),
    ("claude_name_sync", "Claude 세션 이름을 디렉터리별로 맞춘다", "Claude", True),
    ("mdir", "Mdir III 풍 파일 관리자", "탐색", False),
    ("clock", "Big digital clock overlay", "표시", True),
]

QUERIES = ["", "p4", "P4", "p4 show", "p4_show", "name-sync", "name sync", "ㅔ4",
           "ㅡㅇㅑㄱ", "파일", "overlay", "OVERLAY", "  clock  ", "zz-none", "-", "c"]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()
    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib import plugins as py_plugins
    from pytmuxlib.clientscreens import PluginManagerScreen

    registry = py_plugins.load()
    items = [tuple(x) for x in registry.plugin_overview()] + FIXED
    if len(items) < len(FIXED) + 3:
        sys.exit("실제 플러그인 개요가 거의 비었다 — plugins.load() 가 안 돌았다")
    cases = []
    for q in QUERIES:
        scr = PluginManagerScreen.__new__(PluginManagerScreen)
        scr._query = q
        # 번호로 적는다 — 같은 이름이 둘일 수 있다(실제 목록 + 고정 줄).
        hit = {id(x) for x in scr._matches(items)}
        cases.append({"query": q, "rows": [i for i, it in enumerate(items) if id(it) in hit]})
    if not any(c["rows"] and c["query"] for c in cases):
        sys.exit("어느 검색어도 걸리지 않았다 — 고장이다")
    payload = {
        "_comment": "python3 scripts/gen_plugin_filter.py 로 생성. 출처 = 정본 "
                    "PluginManagerScreen._matches 를 직접 부른 값.",
        "items": [{"name": n, "desc": d or ""} for (n, d, _c, _e) in items],
        "cases": cases,
    }
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{len(items)} 줄 × {len(cases)} 검색어 → {args.out}")


if __name__ == "__main__":
    main()
