#!/usr/bin/env python3
"""정본 `debug-stats` 판의 **서버 절반**(`clientdiag.render_server`)을 픽스처로 뽑는다
(pytmux-627 ②).

판의 두 절반 중 **서버 절반**은 두 클라가 같은 회신(`debug_stats`)을 받아 같은 줄을 지어야
한다 — 정본 주석이 *"두 벌이 같은 줄 순서·같은 라벨을 낸다"* 고 적지만 그것을 맞대는 시험이
없었다. 이 생성기는 고정 회신 몇 장을 ko·en 로 `render_server` 에 **직접 넣어** 줄을 적고,
Rust 시험(`debug_stats_conformance.rs`)이 `proto::diag::ServerStats::lines` 와 줄 단위로 맞댄다.
「서버가 아직 답하지 않았다」 줄(`diag.waiting`)도 같이 싣는다.

클라 절반은 대상 밖이다 — 각자 **제 프로세스**(파이썬 힙 · GUI 프레임)를 재는 판이라 값도
항목도 같을 수 없다(interaction.rs 의 그 판 note).

    python3 scripts/gen_debug_stats_fixture.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "debug_stats.json")

NOW = 1_700_000_100.0
STATS = [
    {"pid": 4242, "python": "3.13.1", "uptime_s": 93784.0, "rss": 157286400, "fds": 41,
     "threads": 5, "tasks": 17, "clients": 2, "client_idle_s": [0.4, None], "sessions": 1,
     "windows": 3, "panes": 5, "scrollback_rows": 120000, "remote_links": 1,
     "remote_reconnecting": 0, "objects": 912345,
     "gc": [{"gen": 0, "collections": 1200, "collected": 34000, "uncollectable": 0},
            {"gen": 2, "collections": 3, "collected": 1500, "uncollectable": 2}],
     "top": [["dict", 120000], ["Strip", 9000]],
     "usage_probe": {"boot": 3.2, "panel": 1.0, "total": 4.25, "ok": True, "at": NOW - 125},
     "error_log_bytes": 2048},
    {"pid": 7, "python": "", "uptime_s": None, "rss": None, "fds": None, "threads": None,
     "tasks": None, "clients": 0, "client_idle_s": [], "sessions": 0, "windows": 0,
     "panes": 0, "scrollback_rows": None, "remote_links": 0, "remote_reconnecting": 0,
     "usage_probe": None, "error_log_bytes": None},
    {"pid": 1, "python": "3.11.9", "uptime_s": 59.0, "rss": 1048576, "fds": 9, "threads": 1,
     "tasks": 3, "clients": 1, "client_idle_s": [12.0], "sessions": 2, "windows": 2,
     "panes": 2, "scrollback_rows": 0, "remote_links": 2, "remote_reconnecting": 1,
     "usage_probe": {"boot": None, "panel": None, "total": None, "ok": False, "at": None},
     "error_log_bytes": 0},
]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()
    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib import clientdiag, i18n

    cases = []
    waiting = {}
    for loc in ("ko", "en"):
        i18n.set_locale(loc)
        waiting[loc] = f"― {i18n.t('diag.server')} ― {i18n.t('diag.waiting')}"
        for stats in STATS:
            cases.append({"locale": loc, "stats": stats,
                          "lines": clientdiag.render_server(stats, now=NOW)})
    i18n.set_locale("ko")
    payload = {
        "_comment": "python3 scripts/gen_debug_stats_fixture.py 로 생성. 출처 = 정본 "
                    "clientdiag.render_server 를 직접 부른 줄.",
        "now": NOW,
        "waiting": waiting,
        "cases": cases,
    }
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{len(cases)} 장 → {args.out}")


if __name__ == "__main__":
    main()
