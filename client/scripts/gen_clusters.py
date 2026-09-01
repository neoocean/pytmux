#!/usr/bin/env python3
"""문자소 군집이 **격자에 어떻게 앉나**를 정본에서 뽑는다(pytmux-407 ⓐ).

# 무엇을 고정하나

사람이 고른 규약(2026-09-01)은 **군집의 폭 = 밑글자의 폭**이다(tmux 3.4·현대 단말과
같다). 그 규약을 지키는 판정(`cellwidth.joins_previous`)은 **네 자리**가 읽는다 —
서버 격자(`nativescreen`) · 파이썬 클라 합성 · 러스트 `proto::compose` · GUI 조각 나누기.
어느 하나가 갈리면 그 줄이 **통째로 밀린다**(폭 판정이 갈릴 때와 같은 증상이고, 그래서
같은 방식으로 잰다 — `gen_wire_fixture.py` 의 형제다).

여기서 뽑는 것은 **정본 서버 격자가 내는 칸들**이다: 진짜 화면 모델에 글을 찍고
(`NativeScreen.draw`) 칸마다의 글을 그대로 적는다. 연속 칸은 빈 문자열이다.

⛔ 판정 함수를 여기서 다시 적지 않는다 — 격자를 **돌려서** 뽑는다. 규칙을 베껴 적으면
픽스처가 「우리가 믿는 규칙」이 되고, 그건 아무것도 안 재는 것과 같다.

    python3 scripts/gen_clusters.py [--pytmux ..] [--print]
"""

import argparse
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_PYTMUX = HERE.parent
DEFAULT_OUT = HERE / "crates" / "proto" / "tests" / "fixtures" / "clusters.json"

#: 표본 — 세 갈래(ZWJ · 피부톤 · 지역 지시자)와 **대조군**을 함께 든다.
#: ⛔ 대조군이 없으면 「무엇이든 이어 붙이는」 판도 통과한다.
SAMPLES = [
    ("zwj_family", "|👨‍👩‍👧|"),
    ("zwj_tech", "|🧑‍💻|"),
    ("zwj_rainbow", "|🏳️‍🌈|"),
    ("skin_tone", "|👍🏿|"),
    ("skin_tone_hand", "|✋🏽|"),
    ("flag", "|🇰🇷|"),
    ("two_flags", "|🇰🇷🇯🇵|"),
    ("keycap", "|1️⃣|"),
    ("vs16", "|⚠️|"),
    ("heart", "|❤️|"),
    # ── 대조군 ─────────────────────────────────────────────────────────────
    ("plain_ascii", "|abc|"),
    ("wide_hangul", "|한글|"),
    ("two_emoji_no_join", "|👍👎|"),
    ("combining", "|é|"),          # 조합형 — NFC 로 한 칸
    ("zwj_devanagari", "|क‍ष|"),   # ZWJ 지만 그림 글자가 아니다 — 이으면 안 된다
]


def collect(pytmux_root: pathlib.Path) -> dict:
    sys.path.insert(0, os.fspath(pytmux_root))
    from pytmuxlib.nativescreen import NativeScreen  # noqa: E402

    out = []
    for name, text in SAMPLES:
        screen = NativeScreen(24, 3)
        screen.draw(text)
        line = screen.buffer[0]
        cells = [line[x].data for x in range(24)]
        # 뒤쪽 빈 칸은 잘라 낸다(모두 공백이라 잴 것이 없다).
        while cells and cells[-1] == " ":
            cells.pop()
        if not cells:
            raise SystemExit(f"{name}: 격자가 비었다 — 표본이 안 그려졌다")
        out.append({"name": name, "text": text, "cells": cells})
    if len(out) < len(SAMPLES):
        raise SystemExit("표본이 줄었다")
    return {"cols": 24, "samples": out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pytmux", type=pathlib.Path, default=DEFAULT_PYTMUX)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--print", action="store_true", help="파일 대신 표준출력으로")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    data = collect(args.pytmux)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.print:
        sys.stdout.write(text)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"{os.fspath(args.out)}: 표본 {len(data['samples'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
