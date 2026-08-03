#!/usr/bin/env python3
"""정본이 **패널로 보내는 키 바이트 표**를 뽑아 픽스처로 남긴다(§10-21ⓩ2).

# 왜 이 자가 필요한가

제보: `Ctrl`+`End` 로 맨 아래로 못 간다. 원인은 한 키가 아니라 **부류**였다 — 수정자 붙은
커서 키가 우리 표에서 통째로 빠져 있었고, 두 클라가 **패널로 다른 바이트**를 보내고 있었다.
그리고 아무도 그것을 안 재고 있었다: 패리티 래칫은 표면(명령·설정·화면)을 세지 **패널로
나가는 바이트**를 안 센다.

그 축의 자가 이 파일이다. 정본 `pytmuxlib/clientutil.SPECIAL` 을 그대로 떠서 Rust
`base::keys::encode` 와 대조한다(`crates/proto/tests/key_bytes_conformance.rs`).

출력: `crates/proto/tests/fixtures/key_bytes.json` — `{이름: [바이트…]}`.
바이트를 숫자 배열로 적는 이유: 이스케이프 표기는 두 언어에서 서로 다르게 읽힌다(`\\x1b`
가 문자열 안에서 어떻게 살아남는가는 언어마다 다르다). 숫자는 갈릴 자리가 없다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_PYTMUX = HERE.parent.parent
DEFAULT_OUT = HERE.parent / "crates" / "proto" / "tests" / "fixtures" / "key_bytes.json"


def collect(pytmux_root: pathlib.Path) -> dict:
    sys.path.insert(0, str(pytmux_root))
    from pytmuxlib.clientutil import SPECIAL

    return {name: list(value) for name, value in sorted(SPECIAL.items())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pytmux", type=pathlib.Path, default=DEFAULT_PYTMUX)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--print", action="store_true")
    args = ap.parse_args()

    data = collect(args.pytmux)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.print:
        sys.stdout.write(text)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"{args.out}: 키 {len(data)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
