#!/usr/bin/env python3
"""마우스 패스스루 인코딩 픽스처 — 두 클라가 앱에게 **같은 바이트**를 주는가.

# 왜 이 대조가 필요한가

패스스루는 클라가 앱에게 직접 말을 거는 유일한 자리다. 인코딩이 한 바이트라도 어긋나면
증상은 "그 앱만 마우스가 이상하다"로 나타나고, 사용자도 우리도 **앱을 의심하게 된다**.
게다가 틀린 바이트는 조용히 사라지지 않는다 — 추적을 안 켠 앱은 그걸 **글자로 찍는다**
(Windows 에서 실제로 겪은 모양: 프롬프트에 SGR 시퀀스가 박힌다. HANDOFF §10-H).

파이썬 클라는 이 인코딩을 몇 년째 실사용으로 다듬어 왔다(레거시 X10 의 223 캡, 뗌은
버튼 3, 드래그의 32 오프셋). 러스트 쪽에 손으로 옮겨 적으면 그 이력이 통째로 빠진다.

# 어떻게 뽑는가

`clientwidgets.MultiplexerView._encode_mouse` 는 `self` 를 **한 번도 안 쓴다** — 순수
함수인데 메서드 자리에 있을 뿐이다. 그래서 언바운드로 부른다(위젯도 앱도 안 띄운다).

    python3 scripts/gen_mouse_fixture.py [--pytmux ..]
"""

import argparse
import base64
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures",
                   "mouse.json")

# 패널 하나. 좌표 변환(캔버스 → 패널 1-based)이 시험 대상이라 원점을 0 이 아닌 곳에 둔다.
PANE = {"x": 5, "y": 3, "w": 20, "h": 10}

# (이름, x, y, kind, button). 범위 밖·경계·큰 좌표를 함께 넣는다 — 경계에서 갈리는
# 규칙(1-based 변환, 223 캡)이 실제로 이 표에서 드러난다.
CASES = [
    ("press_left_topleft",      5,   3, "press",     1),
    ("press_left_inside",       8,   6, "press",     1),
    ("press_middle",            8,   6, "press",     2),
    ("press_right",             8,   6, "press",     3),
    ("release_left",            8,   6, "release",   1),
    ("release_right",           8,   6, "release",   3),
    ("drag_left",               9,   7, "drag",      1),
    ("drag_right",              9,   7, "drag",      3),
    ("wheel_up",                8,   6, "wheelup",   0),
    ("wheel_down",              8,   6, "wheeldown", 0),
    ("bottom_right_corner",    24,  12, "press",     1),
    # 밖 — 빈 결과라야 한다. 클램프해서 억지로 넣으면 앱은 **누른 적 없는 자리**를 받는다.
    ("left_of_pane",            4,   6, "press",     1),
    ("above_pane",              8,   2, "press",     1),
    ("right_of_pane",          25,   6, "press",     1),
    ("below_pane",              8,  13, "press",     1),
]

# 넓은 패널에서만 드러나는 레거시 캡(223). SGR 은 상한이 없어 같은 좌표가 다르게 나간다.
WIDE_PANE = {"x": 0, "y": 0, "w": 400, "h": 400}
WIDE_CASES = [
    ("wide_within_cap",       200, 200, "press", 1),
    ("wide_beyond_cap",       300, 300, "press", 1),
]


def main():
    # Windows 콘솔의 기본 코드페이지(한국어 = cp949)는 이 스크립트의 한글 출력 일부를
    # 인코딩하지 못해 **파일을 다 쓴 뒤 print 에서** 죽는다. 그러면 종료코드가 1 이라
    # 사람은 생성이 실패한 줄 안다(실제로는 성공했다). 출력만 UTF-8 로 돌린다.
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
    from pytmuxlib import clientwidgets

    encode = clientwidgets.MultiplexerView._encode_mouse

    rows = []
    for pane, cases in ((PANE, CASES), (WIDE_PANE, WIDE_CASES)):
        for sgr in (False, True):
            for name, x, y, kind, button in cases:
                p = dict(pane, mouse=2, mouse_sgr=sgr)
                data = encode(None, p, x, y, kind, button)
                rows.append({
                    "name": f"{name}_{'sgr' if sgr else 'x10'}",
                    "pane": [pane["x"], pane["y"], pane["w"], pane["h"]],
                    "sgr": sgr,
                    "x": x, "y": y, "kind": kind, "button": button,
                    # base64: 이 바이트열은 UTF-8 이 아니다(레거시 X10 은 임의의
                    # 0x20~0xff 를 낸다). JSON 문자열로 그냥 담으면 인코딩에서 깨진다.
                    "bytes_b64": base64.b64encode(data).decode("ascii"),
                })

    if not any(r["bytes_b64"] for r in rows):
        sys.exit("전부 빈 결과다 — 뽑는 방법이 틀렸다")

    payload = {
        "_comment": "python3 scripts/gen_mouse_fixture.py 로 생성. 출처 = "
                    "pytmuxlib/clientwidgets.py 의 MultiplexerView._encode_mouse. "
                    "bytes_b64 는 base64(레거시 X10 이 UTF-8 이 아닌 바이트를 낸다).",
        "cases": rows,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")

    empty = sum(1 for r in rows if not r["bytes_b64"])
    print(f"{args.out} — 경우 {len(rows)}개 (빈 결과 {empty}개 = 패널 밖)")


if __name__ == "__main__":
    main()
