#!/usr/bin/env python3
"""와이어 적합성 코퍼스 생성기 — Rust 클라이언트의 교차구현 오라클 입력.

# 왜 이게 성립하는가

pytmux 서버는 클라에게 **이미 렌더된 행**을 보낸다(`screen` 메시지의 `rows`) — 행마다
`[텍스트, 스타일]` 런의 목록이다. 클라는 그 런들을 셀 격자에 앉혀 화면을 만든다.

pytmux 에는 이미 그 합성 결과를 SHA-256 으로 동결한 골든이 있다
(`tests/fixtures/replay_golden.json`, p4 66957). 그 골든이 해싱하는
`replay.render_pane_lines()` 의 입력이 바로 `pane.render(True)` — **와이어의 `rows` 그
자체**다. 함수 docstring 도 "클라이언트와 동일한 방식으로 합성"이라고 못박고 있다.

따라서 **같은 `rows` 를 Rust 가 합성해 같은 해시를 내면, 격자 해석이 파이썬 클라와
같다는 뜻**이다. 새 오라클을 만들 필요 없이 이미 신뢰받는 골든을 재사용한다.

# 무엇을 만드는가

`crates/proto/tests/fixtures/wire_rows.json`:

    {
      "<이름>@<cols>x<rows>": {
        "cols": 80,
        "rows": [[["텍스트","스타일"], ...], ...],   // 와이어 그대로
        "sha256": "...")                             // replay_golden.json 과 같은 값
      }
    }

# 다시 만들어야 할 때

pytmux 쪽 합성기가 의도적으로 바뀌어 `replay_golden.json` 을 재생성했을 때. 그 다음
이걸 다시 돌리고 Rust 테스트를 확인한다. 두 골든이 어긋나면 **어느 쪽이 바뀐 것인지**
부터 본다 — 이 스크립트는 파이썬 골든과 값이 다르면 즉시 실패한다.

    python3 scripts/gen_wire_fixture.py [--pytmux ..]
"""

import argparse
import hashlib
import json
import os
import sys


def build_corpus(pytmux_root):
    """(이름, 원시바이트) 목록. pytmux 골든 테스트의 코퍼스와 **정확히 같아야** 한다."""
    # 골든 테스트(tests/test_replay_golden.py)의 _SYNTH 와 동일. 값이 갈리면 표본
    # 집합이 어긋나 아래 대조에서 잡힌다.
    synth = {
        "wide_at_right_edge": "가나다라마바사아자차카타파하" * 8,
        "wide_split_by_cr": "한글ABC\r漢字\r\n두번째 줄 가나다\r\n",
        "scroll_up_su": "".join(f"line{i}\r\n" for i in range(40)) + "\x1b[5S" + "after",
        "alt_screen": "main\r\n\x1b[?1049halt screen 내용\r\n\x1b[?1049lback",
        "combining_zero_width": "é̀x 조합문자 ​ zw\r\n",
        "emoji_and_box": "⏺ ✻ ⚠️ 🚀 │├─┤ 완료\r\n",
        "cursor_addressing": "\x1b[2J\x1b[H\x1b[5;10HX\x1b[A\x1b[2DY\x1b[1;1Htop",
        "long_wrap": ("가" * 60) + ("A" * 60) + "\r\n",
    }
    items = [(f"synth_{k}", v.encode()) for k, v in sorted(synth.items())]

    fixtures = os.path.join(pytmux_root, "tests", "fixtures", "claude")
    for name in sorted(os.listdir(fixtures)):
        if name.endswith(".txt"):
            with open(os.path.join(fixtures, name), "rb") as fp:
                items.append((f"fixture_{name}", fp.read()))
    return items


GEOMETRIES = ((80, 24), (40, 10), (120, 30))


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
    ap.add_argument("--pytmux", default=os.path.join(".."),
                    help="pytmux 저장소 경로(기본: ../pytmux)")
    ap.add_argument("--out", default=os.path.join(
        "crates", "proto", "tests", "fixtures", "wire_rows.json"))
    args = ap.parse_args()

    pytmux_root = os.path.abspath(args.pytmux)
    sys.path.insert(0, pytmux_root)
    from pytmuxlib import cellwidth                      # noqa: E402
    from pytmuxlib.model import Pane                     # noqa: E402
    from pytmuxlib.replay import render_pane_lines       # noqa: E402

    # 골든은 기본(narrow) 모드 기준이다. wide 모드면 폭 계산이 달라져 해시가 흔들린다.
    if cellwidth.ambiguous_wide():
        cellwidth.set_ambiguous_wide(False)

    golden_path = os.path.join(pytmux_root, "tests", "fixtures", "replay_golden.json")
    with open(golden_path, encoding="utf-8") as fp:
        golden = json.load(fp)

    out = {}
    for name, data in build_corpus(pytmux_root):
        for cols, rows in GEOMETRIES:
            key = f"{name}@{cols}x{rows}"
            pane = Pane(-1, -1, cols, rows)   # PTY 불필요(피드만 한다)
            pane.feed(data)
            # 와이어에 실려 가는 것과 **같은 호출**. 여기서 갈라지면 오라클이 무의미하다.
            wire_rows, _cursor = pane.render(True)
            digest = hashlib.sha256(
                "\n".join(render_pane_lines(pane)).encode()).hexdigest()

            expected = golden.get(key)
            if expected is None:
                sys.exit(f"골든에 없는 표본: {key} — 코퍼스가 어긋났다")
            if expected != digest:
                sys.exit(
                    f"골든 불일치: {key}\n  골든 {expected}\n  계산 {digest}\n"
                    "  → 코퍼스나 모호폭 설정이 골든 테스트와 다르다")
            out[key] = {"cols": cols, "rows": wire_rows, "sha256": digest}

    missing = set(golden) - set(out)
    if missing:
        sys.exit(f"골든에는 있는데 못 만든 표본: {sorted(missing)[:5]}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fp.write("\n")
    size = os.path.getsize(args.out)
    print(f"{len(out)} 표본 → {args.out} ({size / 1024:.0f} KB)")
    print("모든 표본의 해시가 pytmux 골든과 일치한다.")


if __name__ == "__main__":
    main()
