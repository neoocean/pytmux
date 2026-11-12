#!/usr/bin/env python3
"""상태줄 메시지 **등급표** 답지 생성기(pytmux-641 ②).

정본 `pytmuxlib/clientnotices.py` 의 `_SPEC`·`SEVERITIES` 를 **직접 import** 해 등급마다
「기호 · 테마 색 이름 · 자동으로 사라지는 시간 · 손으로 닫는 등급인가」를 뜬다. 정본
`display_message` 가 등급 기본값으로 시한과 닫기를 정하고(`default_secs`·`default_dismissable`),
그 «닫는 등급» 이 esc 하단 동선의 `msg` 갈래를 세운다(`_status_buttons`).

GUI 의 `proto::session::Severity` 가 같은 표를 내는지 `crates/proto/tests/notice_levels_conformance.rs`
가 잰다.

사용: python3 scripts/gen_notice_levels.py [--out 경로]
출력: crates/proto/tests/fixtures/notice_levels.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYTMUX = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PYTMUX)

from pytmuxlib import clientnotices as cn  # noqa: E402


def build():
    rows = []
    for sev in cn.SEVERITIES:
        rows.append({
            "name": sev,
            "symbol": cn.symbol(sev),
            "theme": cn.theme_name(sev),
            "auto_close_ms": int(round(cn.default_secs(sev) * 1000)),
            "dismissable": bool(cn.default_dismissable(sev)),
        })
    if len(rows) < 4:
        sys.exit(f"등급이 {len(rows)} 개뿐이다 — 정본 표를 못 읽었다")
    return {
        "_comment": "python3 scripts/gen_notice_levels.py 로 생성. 출처 = "
                    "pytmuxlib/clientnotices.py _SPEC (pytmux-641 ②).",
        "default": cn.DEFAULT_SEVERITY,
        "levels": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        HERE, "..", "crates", "proto", "tests", "fixtures", "notice_levels.json"))
    args = ap.parse_args()
    data = build()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"{args.out}: 등급 {len(data['levels'])}")


if __name__ == "__main__":
    main()
