#!/usr/bin/env python3
"""입력 소스 ID → 한/영 판정 답지 생성기(pytmux-590).

정본 `plugins/ime-indicator/oskbd.py` 의 `is_korean`·`parse_fcitx5`·`parse_ibus` 를 **직접
호출**한다. GUI 는 같은 판정을 `base::ime_source` 로 하고(OS 에 묻는 것만 gui `ime.rs`),
둘이 같은 답을 내는지 `proto/tests/ime_source_conformance.rs` 가 잰다.

사용: python3 scripts/gen_ime_source.py [--out 경로]
출력: crates/proto/tests/fixtures/ime_source.json
"""
import argparse
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYTMUX = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PYTMUX)

oskbd = importlib.import_module("pytmuxlib.plugins.ime-indicator.oskbd")

SOURCE_IDS = [
    "com.apple.keylayout.ABC",
    "com.apple.keylayout.US",
    "com.apple.inputmethod.Korean.2SetKorean",
    "com.apple.inputmethod.Korean.390Sebulshik",
    "com.apple.inputmethod.Kotoeri.RomajiTyping.Japanese",
    "org.youknowone.inputmethod.Gureum.han2",
    "org.youknowone.inputmethod.Gureum.han3final",
    "org.youknowone.inputmethod.Gureum.qwerty",
    "windows.ime.hangul",
    "windows.ime.latin",
    "fcitx5:hangul",
    "fcitx5:keyboard-us",
    "ibus:hangul",
    "ibus:xkb:us::eng",
    "HANGUL",
    "",
]

FCITX5_OUTPUTS = ["hangul\n", "keyboard-us\n", "", "\n  \n", "  hangul  \nextra\n"]
IBUS_OUTPUTS = ["hangul\n", "xkb:us::eng\n", "", "\n"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        HERE, "..", "crates", "proto", "tests", "fixtures", "ime_source.json"))
    args = ap.parse_args(argv)
    out = {
        "is_korean": [{"id": s, "korean": oskbd.is_korean(s)} for s in SOURCE_IDS]
        + [{"id": None, "korean": oskbd.is_korean(None)}],
        "fcitx5": [{"out": o, "id": oskbd.parse_fcitx5(o)} for o in FCITX5_OUTPUTS],
        "ibus": [{"out": o, "id": oskbd.parse_ibus(o)} for o in IBUS_OUTPUTS],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
