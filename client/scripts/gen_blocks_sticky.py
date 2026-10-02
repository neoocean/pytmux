#!/usr/bin/env python3
"""스티키 바 규칙 픽스처 생성기(pytmux-520).

정본 `plugins/blocks/segment.sticky_at` 을 **직접 호출**해 (블록 목록 · 첫 줄 · 스크롤 ·
라이브 하단) → 답(블록 번호 또는 없음)의 짝을 뽑는다. 규칙은 짧지만 경계가 셋이다
(라이브 · 시작 줄 자체 · 마지막 줄) — 손으로 옮겨 적으면 그 경계가 조용히 어긋난다.
GUI 의 `proto::blocks::sticky_at` 이 같은 답을 내는지 `blocks_sticky_conformance.rs` 가 잰다.

사용: python3 scripts/gen_blocks_sticky.py [--out 경로]
출력: crates/proto/tests/fixtures/blocks_sticky.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYTMUX = os.path.join(HERE, "..", "..")
sys.path.insert(0, PYTMUX)

from pytmuxlib.plugins.blocks.segment import sticky_at  # noqa: E402

TWO = [{"cmd": "ls", "state": "done", "exit": 0, "start": 0, "end": 3},
       {"cmd": "make", "state": "running", "start": 4}]
THREE = [{"cmd": "a", "state": "done", "start": 100, "end": 110},
         {"cmd": "b", "state": "done", "start": 110},        # 끝 없음 → 다음 시작 한 줄 앞
         {"cmd": "c", "state": "running", "start": 150}]      # 마지막 → 라이브 하단까지

CASES = {
    "live_has_no_bar": (TWO, 10, 0, 30),
    "inside_second_block": (TWO, 10, 20, 30),
    "first_row_is_the_prompt_itself": (TWO, 4, 26, 30),
    "inside_first_block": (TWO, 1, 29, 30),
    "last_row_of_a_block_is_inside": (TWO, 2, 28, 30),      # end(3) 한 줄 앞이 마지막 줄
    "row_between_blocks_belongs_to_none": (TWO, 3, 27, 30),  # 합성 자료의 틈
    "empty_list": ([], 10, 5, 30),
    "open_ended_middle_block": (THREE, 120, 40, 200),
    "boundary_between_blocks": (THREE, 149, 11, 200),
    "growing_last_block": (THREE, 180, 20, 200),
    "above_every_block": (THREE, 50, 150, 200),
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        HERE, "..", "crates", "proto", "tests", "fixtures", "blocks_sticky.json"))
    args = ap.parse_args(argv)
    cases = {}
    for name, (wire, top, scroll, bottom) in CASES.items():
        cases[name] = {"blocks": wire, "top": top, "scroll": scroll,
                       "live_bottom": bottom,
                       "expect": sticky_at(wire, top, scroll, bottom)}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"cases": cases}, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"wrote {args.out} ({len(cases)} cases)")


if __name__ == "__main__":
    main()
