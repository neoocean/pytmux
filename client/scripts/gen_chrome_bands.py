#!/usr/bin/env python3
"""앱 화면의 **띠 배치** 답지 생성기(pytmux-637).

정본 `pytmuxlib.clientutil.chrome_bands` 를 **직접 호출**한다 — 정본 클라가 쌓는 차례
(`PytmuxApp._apply_status_position`)와 서버에 보고하는 행 수(`_content_size`)가 이 함수
한 벌이고, 그 함수가 실제 위젯 자리와 같다는 것은 정본 시험
`tests/test_status_position_layout.py` 가 헤드리스로 잰다.

조합: 탭바 보임/숨김 × 상태줄 bottom/top × 줄 수 0~5 (높이 24 칸).

GUI 의 `base::chrome::bands` 가 같은 차례·같은 「상태줄 몫은 격자 밖」을 내는지
`crates/base/src/chrome_tests.rs` 가 잰다.

사용: python3 scripts/gen_chrome_bands.py [--out 경로]
출력: crates/proto/tests/fixtures/chrome_bands.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYTMUX = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PYTMUX)

from pytmuxlib.clientutil import chrome_bands  # noqa: E402

HEIGHT = 24


def build():
    rows = []
    for tabbar in (False, True):
        for position in ("bottom", "top"):
            for lines in range(0, 6):
                b = chrome_bands(HEIGHT, tabbar, position, lines)
                rows.append({
                    "tabbar": tabbar, "position": position, "lines": lines,
                    "order": b["order"],
                    "bands": {k: list(b[k]) for k in ("tabbar", "status", "view") if b[k]},
                    "report_rows": b["report_rows"],
                })
    if not rows:
        sys.exit("조합이 0개다 — 생성 방법이 틀렸다")
    return {
        "_comment": "python3 scripts/gen_chrome_bands.py 로 생성. 출처 = "
                    "pytmuxlib/clientutil.py chrome_bands (pytmux-637).",
        "height": HEIGHT,
        "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        HERE, "..", "crates", "proto", "tests", "fixtures", "chrome_bands.json"))
    args = ap.parse_args()
    data = build()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"{args.out}: {len(data['rows'])} 조합")


if __name__ == "__main__":
    main()
