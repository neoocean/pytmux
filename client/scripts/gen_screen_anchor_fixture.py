#!/usr/bin/env python3
"""화면 **앵커** 픽스처 — 팝업이 화면 어디에 서는가를 정본에서 뽑는다.

# 왜 이 표가 필요한가

정본은 화면마다 **어디에 설지를 정해 뒀다**(`clientscreens.py` 의 CSS `align`):

| 앵커 | 화면 | 왜 |
|---|---|---|
| `center bottom` | `PromptScreen` · `CommandListScreen` · `ComposePromptScreen` | **치던 흐름의 연장**이다. 손과 눈이 방금 `:` 를 친 화면 아래에 있는데 판이 가운데나 위에 뜨면 시선이 한 번 튄다 |
| `center top` | `InfoScreen` | 긴 글을 **읽는** 판이라 위에서 시작해야 첫 줄이 늘 같은 자리다 |
| `center middle` | 나머지 전부 | **고르러 여는** 판. 목록이 짧으면 판도 작고, 가운데가 눈의 기본 자리다 |

이 배치는 취향이 아니라 **두 달을 쓰며 굳은 것**이고(사용자 지시 2026-08-01), 그래서
정본이 정본이다. 그런데 우리 두 뷰는 각자 다른 기본값을 갖고 있었다 — Rust TUI 는 전부
위에서 시작하고, GUI 는 전부 가운데였다. 셋이 제각각인데 **아무도 안 쟀다**.

# 무엇을 뽑나

| 키 | 뜻 |
|---|---|
| `anchors` | 화면 클래스 → `top`\\|`middle`\\|`bottom`(세로 축만. 가로는 정본이 전부 `center`) |
| `docks` | 그 화면 안에서 바닥에 고정되는 컨테이너 id(있으면) |
| `prompt_order` | `PromptScreen` 안 **요소 차례**(후보가 위인지 입력이 아래인지) |

`prompt_order` 를 따로 뽑는 이유: 이 차례는 정본이 **사용자 요청으로 뒤집은** 것이다
(§10 — 모바일에서 후보가 입력 박스 아래로 가 키보드에 가렸다). 앵커만 맞추고 차례가
뒤집혀 있으면 같은 자리에 다른 화면이 서는 셈이다.

# 왜 CSS 를 정규식으로 읽나

`clientscreens.py` 는 Textual 을 요구해 import 할 수 없다(`gen_client_surface_fixture.py`
의 `_screens()` 와 같은 사정). CSS 는 클래스 본문의 문자열 리터럴이라 정규식으로 정확히
잡히고, **잡힌 것이 0이면 실패로 떨어뜨린다** — 빈 결과를 통과로 두면 안 우는 게이트가
된다(`check_licenses.sh` 가 한 번 밟은 자리).
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join("crates", "proto", "tests", "fixtures",
                   "screen_anchors.json")

# 정본 CSS 의 세로 낱말 → 우리 이름. 가로(`center`)는 정본이 전부 같아 안 싣는다.
VERTICAL = {"top": "top", "middle": "middle", "bottom": "bottom"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(HERE, ".."))
    ap.add_argument("--out", default=os.path.join(HERE, OUT))
    args = ap.parse_args()

    path = os.path.join(os.path.abspath(args.pytmux), "pytmuxlib", "clientscreens.py")
    if not os.path.isfile(path):
        sys.exit(f"정본을 못 찾았다: {path}")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()

    # 클래스 단위로 자른다(다음 `class` 선언 전까지가 그 클래스의 CSS 범위다).
    parts = re.split(r"^class\s+(\w+)\(", src, flags=re.M)
    anchors, docks = {}, {}
    for name, body in zip(parts[1::2], parts[2::2]):
        # 밑줄로 시작하는 것은 내부 위젯(`_CommandWordHighlighter` 등)이라 화면이 아니다.
        # 그래도 `_SettingInputScreen` 처럼 화면인 것이 있어, 이름이 아니라 **align 유무**로
        # 가른다 — 화면은 반드시 자기 정렬을 적는다.
        m = re.search(rf"\b{name}\s*\{{[^}}]*?align:\s*(\w+)\s+(\w+)", body)
        if not m:
            continue
        horizontal, vertical = m.group(1), m.group(2)
        if horizontal != "center":
            sys.exit(f"{name}: 가로 정렬이 center 가 아니다({horizontal}) — 표를 늘려야 한다")
        if vertical not in VERTICAL:
            sys.exit(f"{name}: 모르는 세로 정렬 {vertical}")
        anchors[name] = VERTICAL[vertical]
        found = re.findall(r"#(\w+)\s*\{[^}]*?dock:\s*(\w+)", body)
        if found:
            docks[name] = {i: d for i, d in found}

    if not anchors:
        sys.exit(f"{path} 에서 화면 정렬을 하나도 못 찾았다 — 뽑는 방법이 틀렸다")

    payload = {
        "_comment": "python3 scripts/gen_screen_anchor_fixture.py 로 생성. 출처 = "
                    "pytmuxlib/clientscreens.py 의 CSS(align/dock)와 "
                    "PromptScreen.compose 의 요소 차례. 손으로 고치지 말 것.",
        "anchors": dict(sorted(anchors.items())),
        "docks": dict(sorted(docks.items())),
        "prompt_order": _prompt_order(src),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    by = {}
    for v in anchors.values():
        by[v] = by.get(v, 0) + 1
    print(f"{args.out} — 화면 {len(anchors)}개 "
          f"({' · '.join(f'{k} {n}' for k, n in sorted(by.items()))}) "
          f"· dock {len(docks)} · 프롬프트 차례 {payload['prompt_order']}")


def _prompt_order(src):
    """`PromptScreen.compose` 가 **바닥 고정 묶음 안에** 두는 요소 차례.

    정본 주석이 이 차례의 이유를 못박는다: "후보 영역(#pcand)을 입력 박스(#prow)
    **위쪽**에 확실히 두려고 둘을 바닥 고정 Vertical(#pwrap)로 묶고 후보를 먼저 둔다".
    그래서 여기서 뽑는 것은 `#pwrap` 안에서 id 가 나타나는 **차례**다.
    """
    m = re.search(r"class\s+PromptScreen\(.*?(?=^class\s)", src, re.S | re.M)
    if not m:
        sys.exit("PromptScreen 을 못 찾았다")
    body = m.group(0)
    wrap = body.find('id="pwrap"')
    if wrap < 0:
        sys.exit("PromptScreen 에 #pwrap 바닥 묶음이 없다 — 차례를 못 잰다")
    order = re.findall(r'id="(\w+)"', body[wrap:])
    # `#pwrap` 자신은 빼고, 같은 id 가 두 번 나오면 첫 등장만 센다.
    seen, out = set(), []
    for i in order:
        if i == "pwrap" or i in seen:
            continue
        seen.add(i)
        out.append(i)
    if "pcand" not in out or "prow" not in out:
        sys.exit(f"프롬프트 차례에 후보/입력이 없다: {out}")
    return out


if __name__ == "__main__":
    main()
