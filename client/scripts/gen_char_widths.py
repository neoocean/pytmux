#!/usr/bin/env python3
"""정본이 **두 칸이라 부르는 글자**를 구간으로 뽑는다(pytmux-407 ⓐ 에서 드러난 자리).

# 왜 필요한가

`proto::compose::char_cells` 는 *"파이썬 `cellwidth.char_cells` 와 글자 하나까지 같아야
한다"* 는 계약을 진다(그 모듈 머리말). 갈리면 **그 줄이 통째로 밀린다.** 그런데 종전에
그 계약을 재던 것은 `conformance.rs` 의 표본 60개뿐이었고, 표본에 없는 글자는 **아무도
안 쟀다** — 실측(2026-09-01)으로 지역 지시자(`🇰`)에서 갈려 있었다: 파이썬 `wcwidth` 는
2, 러스트 `unicode-width` 는 1이라 국기가 든 줄이 두 칸씩 어긋난다.

그래서 표본이 아니라 **구간 전수**로 잰다. 사전은 두 언어가 서로 다른 것을 쓰므로
(`wcwidth` ↔ `unicode-width`) 판이 오를 때마다 갈릴 수 있고, 그 갈림은 조용하다.

# 무엇을 훑나

기호·이모지가 사는 자리다(전부 훑으면 픽스처가 무거워지고 CJK 본진은 이미 표본이 있다):

  · `U+2000–U+33FF` — 기호·화살표·도형·딩뱃·CJK 기호
  · `U+1F000–U+1FAFF` — 이모지 본진(지역 지시자·피부톤 수정자 포함)
  · `U+FE00–U+FE0F` · `U+0300–U+036F` — 변이 선택자·결합 표시(폭 0 대조군)

⛔ 모호폭(EAW='A')은 **좁은 모드**로 뽑는다(`wide`).

★ **wide 모드도 따로 뽑는다**(`wide_ambig` · pytmux-578). 「설정이 켜면 양쪽 다 같은 규칙으로
넓힌다」는 종전 문장은 **재지 않은 가정**이었고 틀렸다 — GUI 는 선·블록(U+2500–259F)까지
넓혔고, 러스트 `width_cjk` 는 라틴-1 모호폭 247자를 한 칸으로 줬다. wide 모드는 유니코드
전체(`WIDE_SWEEP`)를 훑는다. 뽑은 뒤 정본 전역을 **좁은 모드로 되돌린다**.
`--rust` 는 GUI 가 싣는 정본 표(`proto/src/ambig_table.rs`)를 표준출력으로 찍는다.

    python3 scripts/gen_char_widths.py [--pytmux ..] [--print | --rust]
"""

import argparse
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_PYTMUX = HERE.parent
DEFAULT_OUT = HERE / "crates" / "proto" / "tests" / "fixtures" / "char_widths.json"

SWEEP = [(0x0300, 0x036F), (0x2000, 0x33FF), (0xFE00, 0xFE0F), (0x1F000, 0x1FAFF)]
#: wide 모드는 **유니코드 전체**를 훑는다 — 모호폭은 라틴-1·그리스·키릴·사용자 영역(PUA)까지
#: 흩어져 있고(실측 176구간), 러스트 `unicode-width` 의 `width_cjk` 는 그중 247자를 한 칸으로
#: 준다(0.2.1 실측). 그래서 GUI 는 정본 표를 그대로 싣는다 — `--rust` 가 그 표를 찍는다.
WIDE_SWEEP = [(0x0000, 0xD7FF), (0xE000, 0x10FFFF)]


def _ambiguous_widened(cellwidth) -> list[list[int]]:
    """정본 wide 모드가 **한 칸 → 두 칸**으로 올리는 글자(= `char_cells` 의 모호폭 갈래)."""
    from wcwidth import wcwidth
    out: list[list[int]] = []
    for a, b in WIDE_SWEEP:
        for o in range(a, b + 1):
            ch = chr(o)
            if not cellwidth.is_ambiguous(ch) or wcwidth(ch) == 2:
                continue
            if out and out[-1][1] == o - 1:
                out[-1][1] = o
            else:
                out.append([o, o])
    return out


def rust_table(cellwidth) -> str:
    rows = _ambiguous_widened(cellwidth)
    body = "\n".join(f"    (0x{a:04X}, 0x{b:04X})," for a, b in rows)
    return (
        "//! 모호폭(EAW 'A') 가운데 정본 wide 모드가 **두 칸으로 올리는** 글자 — 정본 표 그대로\n"
        "//! (pytmux-578). ⛔ 손으로 고치지 않는다: `python3 scripts/gen_char_widths.py --rust`\n"
        "//! 가 찍은 것이고, 정본과 같은지는 `tests/char_width_conformance.rs` 가 유니코드 전체로 잰다.\n"
        "//! 정본 규칙: `unicodedata.east_asian_width == 'A'` 이고 선·블록(U+2500–259F)이 아니며\n"
        "//! `wcwidth` 가 2 가 아닌 글자(`cellwidth.char_cells` 의 wide 갈래).\n\n"
        f"pub(crate) static AMBIGUOUS_WIDENED: &[(u32, u32)] = &[\n{body}\n];\n"
    )


def _two_cell_ranges(cellwidth, sweep) -> list[list[int]]:
    out: list[list[int]] = []
    for a, b in sweep:
        for o in range(a, b + 1):
            if cellwidth.char_cells(chr(o)) != 2:
                continue
            if out and out[-1][1] == o - 1:
                out[-1][1] = o
            else:
                out.append([o, o])
    return out


def collect(pytmux_root: pathlib.Path) -> dict:
    sys.path.insert(0, os.fspath(pytmux_root))
    from pytmuxlib import cellwidth  # noqa: E402

    if cellwidth.ambiguous_wide():
        raise SystemExit("모호폭 wide 모드에서 뽑으면 안 된다 — 좁은 모드가 기준이다")
    wide = _two_cell_ranges(cellwidth, SWEEP)
    if len(wide) < 50:
        raise SystemExit(f"뽑힌 구간이 너무 적다({len(wide)}) — 훑기가 헛돌았다")
    cellwidth.set_ambiguous_wide(True)
    try:
        wide_ambig = _two_cell_ranges(cellwidth, WIDE_SWEEP)
    finally:
        cellwidth.set_ambiguous_wide(False)
    if len(wide_ambig) <= len(wide):
        raise SystemExit("wide 모드가 넓힌 글자가 없다 — 전역 전환이 안 먹었다")
    return {"sweep": [list(r) for r in SWEEP], "wide": wide,
            "wide_sweep": [list(r) for r in WIDE_SWEEP], "wide_ambig": wide_ambig,
            "widened": _ambiguous_widened(cellwidth)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pytmux", type=pathlib.Path, default=DEFAULT_PYTMUX)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--print", action="store_true", help="파일 대신 표준출력으로")
    ap.add_argument("--rust", action="store_true",
                    help="GUI 가 싣는 모호폭 표(proto/src/ambig_table.rs)를 표준출력으로")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.rust:
        sys.path.insert(0, os.fspath(args.pytmux))
        from pytmuxlib import cellwidth  # noqa: E402
        sys.stdout.write(rust_table(cellwidth))
        return 0

    data = collect(args.pytmux)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.print:
        sys.stdout.write(text)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"{os.fspath(args.out)}: 넓은 구간 {len(data['wide'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
