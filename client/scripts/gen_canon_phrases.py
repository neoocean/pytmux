#!/usr/bin/env python3
"""정본 카탈로그의 **글 전수**(ko/en)를 픽스처로 뽑는다 (pytmux-595 · 말투).

GUI 가 스스로 쓴 한국어 문구는 두 규칙을 따른다(사람 결정 2026-11-12):

1. 정본에 **같은 뜻의 글**이 있으면 그 글을 **글자까지 그대로** 쓴다(영어 짝도 정본 en).
2. 정본에 없는 GUI 전용 글은 **합니다체**다.

둘을 Rust 가 재려면 정본 글이 손에 있어야 한다 — 그래서 정본 카탈로그(`pytmuxlib/i18n.py` +
플러그인 `register`)를 **플러그인까지 로드한 상태**로 통째로 싣는다(`gen_server_strings.py` 와
같은 로드). `proto/tests/canon_phrase_conformance.rs` 가 읽는다:

- 확인 판 물음 · 빈 판 글 · 재시작 점검 머리줄 등 «정본 글 그대로» 자리가 정본 ko/en 과 같은가.
- GUI 카탈로그(`base/src/i18n/en_*.rs`)의 한국어 열쇠 가운데 해라체로 끝나는 것은 **정본 글일 때만**.

    python3 scripts/gen_canon_phrases.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "canon_phrases.json")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()
    os.environ.pop("NO_COLOR", None)
    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib import i18n, plugins
    # 코어 밖에서 자기 글을 `register` 하는 정본 모듈 — `debug-stats` 판(`clientdiag`)의 `diag.*`.
    from pytmuxlib import clientdiag  # noqa: F401

    plugins.load()
    ko = dict(i18n._CATALOG.get("ko", {}))
    en = dict(i18n._CATALOG.get("en", {}))
    if len(ko) < 300 or len(en) < 300:
        sys.exit(f"카탈로그가 너무 작다(ko {len(ko)} · en {len(en)}) — 플러그인 로드가 안 됐다")
    payload = {
        "_comment": "python3 scripts/gen_canon_phrases.py 로 생성. 출처 = 정본 pytmuxlib/i18n.py "
                    "카탈로그(플러그인 register 포함)의 ko/en 전수.",
        "ko": ko,
        "en": en,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=1, sort_keys=True)
        fp.write("\n")
    print(f"ko {len(ko)} · en {len(en)} → {args.out}")


if __name__ == "__main__":
    main()
