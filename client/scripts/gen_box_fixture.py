#!/usr/bin/env python3
"""경계 문자 합성표 픽스처 생성기 — 두 클라가 같은 격자 모양을 그리게 묶는다.

# 왜 이게 필요한가

패널이 맞닿으면 두 테두리가 같은 칸을 두 번 그린다. 그 칸을 `│` 로 두면 격자가 끊겨
보이고, `┬`·`┴`·`┼` 로 **합쳐야** 한 장처럼 보인다. 파이썬 클라는 이 합성을 문자↔변
비트 표(`clientutil._BOX_BITS`)로 한다.

Rust 클라도 같은 표를 들고 있는데(`canvas::BOX_BITS`) **값으로 공유되는 표**라 한쪽만
바뀌면 조용히 갈린다 — 같은 배치에서 두 클라가 다른 모양을 그리게 된다. 그래서 파이썬
쪽 표를 뽑아 두고 Rust 테스트가 자기 표와 대조한다.

표를 뽑는 데 서버도 앱도 필요 없다 — 모듈 상수 하나다. 그래서 이 스크립트는 빠르고,
실패하면 원인이 표 하나로 좁혀진다.

# 다시 만들어야 할 때

파이썬 클라가 경계 문자를 늘렸을 때(예: 굵은 선·이중선 지원). 그 다음 이걸 돌리고
`cargo test -p proto` 를 확인한다.

    python3 scripts/gen_box_fixture.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures",
                   "box_chars.json")


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
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."),
                    help="pytmux 저장소 경로")
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()

    root = os.path.abspath(args.pytmux)
    if not os.path.isdir(root):
        sys.exit(f"pytmux 저장소를 못 찾았다: {root}")
    sys.path.insert(0, root)
    # clientutil 은 Rich·Textual 을 import 한다(상수만 필요해도 모듈은 통째로 읽힌다).
    from pytmuxlib import clientutil

    bits = clientutil._BOX_BITS
    # 비트는 U=8·D=4·L=2·R=1. 값이 겹치면 역표(_BOX_REV)가 문자를 잃으므로 여기서 막는다.
    if len(set(bits.values())) != len(bits):
        sys.exit(f"파이썬 표에 중복 비트가 있다: {bits!r}")
    payload = {
        "_comment": "python3 scripts/gen_box_fixture.py 로 생성. "
                    "출처 = pytmuxlib/clientutil.py::_BOX_BITS (U=8,D=4,L=2,R=1)",
        "bits": {ch: v for ch, v in sorted(bits.items(), key=lambda kv: kv[1])},
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{args.out} — 경계 문자 {len(bits)}개")


if __name__ == "__main__":
    main()
