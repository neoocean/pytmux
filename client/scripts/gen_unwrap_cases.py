#!/usr/bin/env python3
"""정본의 `unwrap_copy_text` 를 **직접 불러** 입출력 짝을 픽스처로 뽑는다.

# 왜 생성기인가

이 함수는 규칙이 여섯이다(테두리 떼기 · 구분선 경계 · 최소 채움 · 매달림 들여쓰기 상한 ·
의도된 줄 끝 · 여유 칸). 손으로 기대값을 적으면 **우리가 이해한 규칙**을 재게 되고, 그건
정본이 아니다. 여기서는 정본 함수를 그대로 호출해 답을 받는다 — 우리 포팅이 틀리면
그 자리에서 갈린다.

# 무엇을 담나

`cases[]`: `{name, text, width, first_col, want}`. 규칙마다 그것이 **발동하는 경우와
발동하지 않는 경우**를 짝으로 둔다 — 한쪽만 있으면 "아무것도 안 하는" 구현이 통과한다.

    python3 scripts/gen_unwrap_cases.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "unwrap_copy.json")

# (이름, 텍스트, 폭, 시작열). 텍스트는 실제 패널에서 긁힐 법한 모양으로 짠다.
CASES = [
    ("한 줄은 손대지 않는다", "그냥 한 줄이다", 80, 0),
    ("폭을 모르면 그대로", "접힌 것처럼\n  보이는 두 줄", 0, 0),
    # 접힘: 첫 줄이 폭 가까이 차고 다음 줄이 매달림 들여쓰기로 이어진다.
    ("앱이 접은 두 줄을 잇는다",
     "이 줄은 패널 폭 가까이까지 채워져 있어서 다음 줄로 넘어간다 그리고\n"
     "  이어지는 낱말들이 여기 온다",
     60, 0),
    # 같은 모양인데 **들여쓰기가 없다** → 접힘이 아니다.
    ("들여쓰기가 없으면 안 잇는다",
     "이 줄은 패널 폭 가까이까지 채워져 있어서 다음 줄로 넘어간다 그리고\n"
     "이어지는 낱말들이 여기 온다",
     60, 0),
    # 들여쓰기가 상한(12)을 넘으면 코드 블록으로 본다.
    ("깊은 들여쓰기는 코드로 본다",
     "이 줄은 패널 폭 가까이까지 채워져 있어서 다음 줄로 넘어간다 그리고\n"
     "                여기는 코드 블록이다",
     60, 0),
    # 의도된 줄 끝(`:` 등)이면 안 잇는다.
    ("의도된 줄 끝은 안 잇는다",
     "이 줄은 패널 폭 가까이까지 채워져 있고 콜론으로 끝난다 그러니까:\n"
     "  다음 줄은 새 줄이다",
     60, 0),
    # 폭 근처까지 안 간 블록은 접힘이 아니다.
    ("짧은 블록은 접힘이 아니다", "짧다\n  이것도", 80, 0),
    # 테두리가 붙어 온 경우 — 앞뒤 박스런을 떼고 판정한다.
    ("테두리를 떼고 판정한다",
     "│ 이 줄은 패널 폭 가까이까지 채워져 있어서 다음 줄로 넘어간다 그리고 │\n"
     "│   이어지는 낱말들이 여기 온다                                      │",
     60, 0),
    # 구분선만 있던 줄은 버리되 **경계**로 남는다(위아래가 붙으면 안 된다).
    ("구분선은 경계로 남는다",
     "│ 이 줄은 패널 폭 가까이까지 채워져 있어서 다음 줄로 넘어간다 그리고 │\n"
     "└────────────────────────────────────────────────────────────────────┘\n"
     "  다음 블록의 첫 줄이다",
     60, 0),
    # 첫 줄이 드래그 시작 열에서 잘려 나온 경우 — first_col 을 되돌려 줘야 한다.
    ("시작 열을 되돌려 준다",
     "채워져 있어서 다음 줄로 넘어간다 그리고\n"
     "  이어지는 낱말들이 여기 온다",
     60, 26),
    ("시작 열을 안 주면 판정이 어긋난다",
     "채워져 있어서 다음 줄로 넘어간다 그리고\n"
     "  이어지는 낱말들이 여기 온다",
     60, 0),
    # ★ 여유 칸(SLACK=2)이 **혼자 결정하는** 자리. 앞 줄이 관측 최대 채움보다 두 칸
    # 짧고 이어지는 줄의 첫 낱말이 한 글자면, 여유가 없으면 "들어갈 자리가 있었다"로
    # 오판해 접힘을 놓친다(정본 주석의 실측 그대로).
    ("여유 칸이 결정한다",
     "a" * 62 + "\n" + "b" * 60 + "\n" + "  | 이어지는 낱말",
     60, 0),
    # ★ 시작 열(first_col)이 **혼자 결정하는** 자리. 그 값을 안 되돌리면 관측 최대
    # 채움이 작게 잡혀, 접힘이 아닌 줄까지 이어붙는다.
    ("시작 열이 결정한다",
     "a" * 40 + "\n" + "b" * 60 + "\n" + "  | 이어지는 낱말",
     60, 25),
    ("시작 열을 안 주면 이어붙는다",
     "a" * 40 + "\n" + "b" * 60 + "\n" + "  | 이어지는 낱말",
     60, 0),
    # CJK 는 두 칸이라 len() 으로 재면 어긋난다.
    ("한글은 두 칸으로 센다",
     "한글로만 이루어진 줄인데 폭을 거의 다 채운다 그리고 여기서\n"
     "  이어진다",
     60, 0),
]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()

    root = os.path.abspath(args.pytmux)
    if not os.path.isdir(root):
        sys.exit(f"pytmux 저장소를 못 찾았다: {root}")
    sys.path.insert(0, root)
    from pytmuxlib import clientutil as cu

    cases = []
    for name, text, width, first_col in CASES:
        cases.append({
            "name": name,
            "text": text,
            "width": width,
            "first_col": first_col,
            "want": cu.unwrap_copy_text(text, width, first_col),
        })

    # ★ 아무것도 안 하는 구현이 통과하면 안 된다 — **바뀌는 경우가 실제로 있는지**
    #   여기서 확인한다(생성기가 우는 자리를 두는 이 저장소의 규율).
    changed = sum(1 for c in cases if c["want"] != c["text"])
    if changed < 3:
        sys.exit(f"바뀌는 경우가 {changed}개뿐이다 — 케이스가 규칙을 안 건드린다")

    payload = {
        "_comment": "python3 scripts/gen_unwrap_cases.py 로 생성. 출처 = "
                    "pytmuxlib/clientutil.py::unwrap_copy_text 를 직접 호출한 결과. "
                    "기대값을 손으로 적지 않는 이유는 그러면 '우리가 이해한 규칙'을 "
                    "재게 되기 때문이다.",
        "hang_max": cu._UNWRAP_HANG_MAX,
        "min_fill": cu._UNWRAP_MIN_FILL,
        "slack": cu._UNWRAP_SLACK,
        "tail_stop": cu._UNWRAP_TAIL_STOP,
        "cases": cases,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{args.out} — 케이스 {len(cases)} (바뀌는 것 {changed})")


if __name__ == "__main__":
    main()
