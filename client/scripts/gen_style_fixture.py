#!/usr/bin/env python3
"""스타일 적합성 코퍼스 생성기 — 서버가 실제로 내보내는 스타일 값의 전수 표.

# 왜 필요한가

P2 의 적합성 오라클(`wire_rows.json`)은 **텍스트 배치만** 고정한다. 코퍼스로 쓴 클로드
화면 덤프에 SGR 색 시퀀스가 하나도 없어서, 스타일 경로는 전혀 검증되지 않았다. 색을
실제로 칠하기 시작하는 P3 에서 그 공백을 메운다.

# 무엇이 위험한가

스타일 값 이름을 **추측하면 틀린다**. 실측 예: SGR 93(밝은 노랑)은 `bright_brown` 으로
나온다 — pyte 의 색 이름 체계가 ANSI 이름과 다르기 때문이다. 클라가 `bright_yellow` 를
기대하면 그 색이 조용히 기본색으로 떨어진다(예외도, 로그도 없이).

그래서 **서버를 권위로 삼아** SGR 코드 → 스타일 객체 대응표를 통째로 뽑아 둔다. 클라의
파서는 이 표의 모든 값을 알아야 한다.

# 만드는 것

`crates/proto/tests/fixtures/styles.json`:

    {
      "sgr": [{"seq": "31", "style": {"f": "red"}}, ...],
      "colors": ["red", "bright_brown", ...],   // 서버가 낼 수 있는 색 이름 전부
      "attrs":  ["bo", "it", "un", "rv", "st"]  // 속성 키 전부
    }

    python3 scripts/gen_style_fixture.py [--pytmux ..]
"""

import argparse
import json
import os
import sys


def sgr_cases():
    """(라벨, SGR 파라미터) — 서버가 낼 수 있는 스타일을 고루 훑는다."""
    cases = []
    # 표준 8색 전경/배경, 밝은 8색 전경/배경.
    for code in list(range(30, 38)) + list(range(40, 48)):
        cases.append((str(code), str(code)))
    for code in list(range(90, 98)) + list(range(100, 108)):
        cases.append((str(code), str(code)))
    # 256색 팔레트 — 경계와 대표값만(전수는 표를 부풀리기만 한다).
    for n in (0, 1, 7, 8, 15, 16, 100, 231, 232, 255):
        cases.append((f"38;5;{n}", f"38;5;{n}"))
        cases.append((f"48;5;{n}", f"48;5;{n}"))
    # 트루컬러.
    for rgb in ("0;0;0", "255;255;255", "18;52;86"):
        cases.append((f"38;2;{rgb}", f"38;2;{rgb}"))
        cases.append((f"48;2;{rgb}", f"48;2;{rgb}"))
    # 속성들과 조합.
    for code in ("1", "3", "4", "7", "9"):
        cases.append((code, code))
    cases.append(("1;4;31", "1;4;31"))
    cases.append(("7;44;93", "7;44;93"))
    cases.append(("1;3;4;7;9;35;46", "1;3;4;7;9;35;46"))
    return cases


def _utf8_stdout():
    """Windows 콘솔의 기본 코드페이지(한국어=cp949)에서 한글 출력이 죽지 않게.

    이 스크립트들은 마지막에 결과 요약을 한글로 찍는다. cp949 콘솔에서는 그 print 가
    UnicodeEncodeError 로 죽는데, **파일은 이미 다 쓴 뒤**라 종료코드 1 만 보고
    "생성 실패"로 오인하게 된다(2026-07-28 실측: 생성기 6개 전부 그랬다).
    출력 스트림만 UTF-8 로 돌린다 — 생성 결과에는 영향이 없다.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pytmux", default=os.path.join(".."))
    ap.add_argument("--out", default=os.path.join(
        "crates", "proto", "tests", "fixtures", "styles.json"))
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib.model import Pane  # noqa: E402

    entries = []
    colors, attrs = set(), set()
    for label, params in sgr_cases():
        pane = Pane(-1, -1, 20, 2)
        pane.feed(f"\x1b[{params}mX".encode())
        rows, _ = pane.render(True)
        # 첫 글자의 스타일이 이 SGR 의 결과다.
        style = {}
        for text, st in rows[0]:
            if text.startswith("X"):
                style = st
                break
        entries.append({"seq": label, "style": style})
        for key, value in style.items():
            if key in ("f", "b"):
                colors.add(value)
            else:
                attrs.add(key)

    out = {
        "sgr": entries,
        "colors": sorted(colors),
        "attrs": sorted(attrs),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, sort_keys=True)
        fp.write("\n")
    print(f"{len(entries)} SGR 표본 → {args.out}")
    print(f"  색 이름 {len(colors)}종: {sorted(colors)[:8]}...")
    print(f"  속성 키 {len(attrs)}종: {sorted(attrs)}")


if __name__ == "__main__":
    main()
