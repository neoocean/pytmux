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
| `overrides` | **호출이 CSS 를 뒤집는 자리** — `클래스` → {`title` → 앵커} |
| `docks` | 그 화면 안에서 바닥에 고정되는 컨테이너 id(있으면) |
| `prompt_order` | `PromptScreen` 안 **요소 차례**(후보가 위인지 입력이 아래인지) |

`overrides` 가 왜 필요한가(§10-21ⓐ3): 정본의 범용 `InfoScreen` 은 CSS 로 `center top`
인데, **호출이 `center=True` 로 그것을 뒤집는 자리**가 있다(`version` 판 — 다섯 줄뿐이라
위에 붙이면 화면이 비어 보인다). 클래스 CSS 만 읽던 이 생성기는 그 예외를 통째로 못 봤고,
그래서 우리 버전 판이 정본과 **다른 자리에 서는 것을 아무도 안 쟀다**. 자리가 CSS 한
낱말이라 안 잰다는 것이 이 픽스처의 존재 이유였는데, 정작 그 낱말을 덮어쓰는 인자를
빠뜨리고 있었다.

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

    overrides = _anchor_overrides(os.path.abspath(args.pytmux))
    replaced = _code_placed(src)
    payload = {
        "_comment": "python3 scripts/gen_screen_anchor_fixture.py 로 생성. 출처 = "
                    "pytmuxlib/clientscreens.py 의 CSS(align/dock)와 "
                    "PromptScreen.compose 의 요소 차례, 그리고 CSS 를 뒤집는 "
                    "호출 인자(InfoScreen center=). 손으로 고치지 말 것.",
        "anchors": dict(sorted(anchors.items())),
        "overrides": {k: dict(sorted(v.items())) for k, v in sorted(overrides.items())},
        "docks": dict(sorted(docks.items())),
        # ★ CSS 의 `align` **위에서 코드가 자리를 다시 잡는** 판(아래 `_code_placed`).
        "code_placed": dict(sorted(replaced.items())),
        "prompt_order": _prompt_order(src),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    by = {}
    for v in anchors.values():
        by[v] = by.get(v, 0) + 1
    n_over = sum(len(v) for v in overrides.values())
    print(f"{args.out} — 화면 {len(anchors)}개 "
          f"({' · '.join(f'{k} {n}' for k, n in sorted(by.items()))}) "
          f"· 덮어쓰기 {n_over} · dock {len(docks)} "
          f"· 프롬프트 차례 {payload['prompt_order']}")


# ── CSS 위에서 «코드가» 자리를 다시 잡는 판 ─────────────────────────────────
#
# ⛔ **`align` 만 읽으면 정본을 잘못 읽는다**(pytmux-370). `ComposePromptScreen` 의 CSS 는
#    `align: center bottom` 인데, 그 화면은 `on_mount` 에서 **활성 패널의 안쪽 x·폭과
#    커서(프롬프트) 행**으로 margin 을 다시 계산해 판을 그 줄에 세운다. 즉 CSS 의 `bottom`
#    은 「바닥에 붙인다」가 아니라 「바닥에서 위로 띄우는 기준」이고, 실제 자리는 **커서
#    줄**이다.
#
#    이 한 낱말을 놓친 대가가 그대로 제보로 왔다: GUI 가 `bottom` 을 곧이곧대로 읽어 창
#    맨 아래 전폭 판으로 그렸고, 사용자는 *"gui는 완전히 별도 위치에 팝업이 나타난다"* 고
#    적었다. 픽스처가 CSS 만 뽑는 한 그 갈림은 **게이트를 그대로 통과한다**.
#
# 그래서 「코드가 다시 잡는다」를 **실측으로** 뽑는다 — 그 클래스 본문에서 자리를 정하는
# 재료(패널 x/폭 · 프롬프트 행)를 실제로 쓰는지 본다. 손으로 적은 목록이 아니라서, 정본이
# 그 계산을 걷어내면 이 표에서 저절로 사라지고 대조가 따라 움직인다.
_PLACE_MARKS = ("_prompt_row", "_pane_x", "_pane_w")


def _code_placed(src):
    """`클래스 → 자리를 다시 잡을 때 쓰는 재료들`. 안 쓰면 그 클래스는 표에 없다."""
    out = {}
    parts = re.split(r"^class\s+(\w+)\(", src, flags=re.M)
    for name, body in zip(parts[1::2], parts[2::2]):
        # `styles.margin` 을 직접 주는 자리가 있어야 «다시 잡는» 것이다 — 재료만 들고
        # 아무것도 안 하는 클래스를 세면 표가 거짓말이 된다.
        if not re.search(r"styles\.margin\s*=", body):
            continue
        used = sorted(m for m in _PLACE_MARKS if m in body)
        if used:
            out[name] = used
    return out


# ── 호출이 CSS 를 뒤집는 자리 ──────────────────────────────────────────────
#
# 범용 `InfoScreen` 은 CSS 로 `center top` 이지만 호출이 `center=True` 로 뒤집을 수
# 있다. 그 인자를 안 뽑으면 정본이 가운데 띄우는 판을 우리는 위에 세우게 된다(§10-21ⓐ3).

def _client_sources(root):
    """정본 클라 코드 전부 — `InfoScreen` 을 띄우는 자리는 여러 모듈에 흩어져 있다
    (`clientconn.py`·`clientcmd.py`·플러그인). 한 파일만 보면 조용히 빠뜨린다."""
    base = os.path.join(root, "pytmuxlib")
    for dirpath, _dirs, files in os.walk(base):
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _balanced(src, open_paren):
    """`src[open_paren]` 의 `(` 에 대응하는 `)` 까지의 **안쪽 텍스트**.

    괄호를 세는 이유: 호출이 여러 줄에 걸치고 안에 또 호출이 있다
    (`InfoScreen(make_lines(), title="version", center=True, ...)`). 정규식 하나로는
    그 짝을 못 맞춘다."""
    depth, i = 0, open_paren
    while i < len(src):
        ch = src[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return src[open_paren + 1:i]
        elif ch in "\"'":                   # 문자열 안의 괄호는 안 센다
            quote = src[i:i + 3] if src[i:i + 3] in ('"""', "'''") else ch
            end = src.find(quote, i + len(quote))
            if end < 0:
                return None
            i = end + len(quote) - 1
        i += 1
    return None


def _title_of(args):
    """호출의 `title=` 인자를 **정본이 그 판을 부르는 이름**으로 정규화.

    문자열 리터럴이면 그 값, `i18n.t("k")` 면 키 `k`, 그 밖이면 표현식 원문.
    인자가 없으면 `InfoScreen` 의 기본값(`info`)이다."""
    m = re.search(r"\btitle\s*=\s*", args)
    if not m:
        return "info"
    rest = args[m.end():]
    depth, cut = 0, len(rest)
    for i, ch in enumerate(rest):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            cut = i
            break
    expr = rest[:cut].strip()
    lit = re.fullmatch(r"""['"](.*)['"]""", expr, re.S)
    if lit:
        return lit.group(1)
    key = re.fullmatch(r"""i18n\.t\(\s*['"]([^'"]+)['"].*\)""", expr, re.S)
    if key:
        return key.group(1)
    return " ".join(expr.split())


def _anchor_overrides(root):
    m_all = {}
    for path in _client_sources(root):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for call in re.finditer(r"\bInfoScreen\(", src):
            args = _balanced(src, call.end() - 1)
            if args is None or not re.search(r"\bcenter\s*=\s*True\b", args):
                continue
            m_all.setdefault("InfoScreen", {})[_title_of(args)] = "middle"
    if not m_all:
        sys.exit("호출이 CSS 를 뒤집는 자리를 하나도 못 찾았다 — 정본이 그 예외를 "
                 "없앴거나(그러면 Rust 쪽 canon_variant 도 지울 것) 뽑는 방법이 틀렸다")
    return m_all


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
