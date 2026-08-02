"""IME 배지의 **자리 규칙 + 런 생성기** — UI 무의존(rich/textual 을 안 읽는다).

# 왜 여기인가

시계(08-02e)·달력(08-02b)·한도(08-02f)가 낸 길이다. 다만 이 배지는 부류가 하나 더
있다 — **입력기 상태는 OS 가 클라 창에만 알려 준다**(설계 §1.2 의 ⑤ · Tier D). 서버는
"지금 한글 모드인가"를 스스로 알 수 없다.

그래서 갈래를 나눈다:

- **사실**(한/영)은 클라만 안다 → 클라가 `client_fact` 로 올려 보낸다.
- **그릴지·어디에·무슨 색으로**는 플러그인이 정한다 → 이 모듈 한 벌.

설계 §4.4 가 적은 그대로다: *"클라가 사실만 서버에 보고하고, 배지를 그릴지 말지·
어떻게는 플러그인이 Tier B 로 정한다. 그러면 로직은 여전히 한 벌이다."*

# 자리 규칙이 왜 이렇게 생겼나

배지는 **커서가 있는 줄**의 활성 패널 오른쪽 끝에 붙는다(2026-06-11 우상단 고정 →
커서 줄 · 2026-06-16 화면 우측 → **활성 패널** 우측). 근거는 두 가지다: 조합(preedit)이
보이는 커서 줄과 같은 높이라 시선을 안 옮겨도 한/영이 보이고, 좌우 분할에서 활성
패널이 화면 왼쪽 절반일 때 배지가 비활성 패널 위에 뜨는 혼동이 없다.

⚠ **2026-08-02i 이전에는 네이티브 클라가 이 규칙을 안 따랐다** — 손으로 옮긴 판이
**활성 패널 우상단 고정**이었고(`proto::session::draw_ime_badge`), 그 자리 주석은
"정본과 같은 자리"라고 적고 있었지만 커서가 첫 줄에 있을 때만 같았다. 두 벌은 갈린다 —
이 모듈이 그 두 벌을 없앤다(자리 통일은 **사용자 결정**: 정본 규칙으로).
"""
from __future__ import annotations

from pytmuxlib.cellwidth import char_cells

# 배지 두 상태의 의미 색. 한글은 강조(success), 영문은 기본 강조색(primary).
# 글자색은 리터럴 `black` 이다 — 강조색 바탕 위에서 읽히기만 하면 되는 자리라
# 테마가 아니라 모양의 일부다(달력 '오늘'과 같은 판단).
_THEME = {"한": "success", "EN": "primary"}

# 탭 닫기 `[x]` 와 같은 행일 때 오른쪽으로 비켜 줄 칸 수([x] 가 배지 뒤에 그려진다).
RESERVE_FOR_TAB_CLOSE = 4


def text_width(s: str) -> int:
    """표시 폭(한글 등 와이드=2칸)."""
    return sum(char_cells(c) for c in s)


def badge_row(cursor_xy, last_row, pane_box):
    """배지를 그릴 **행**을 고른다 — 세 갈래(정본 `client_render` 의 규칙 그대로).

    1. 커서가 보이면 **그 행**.
    2. 안 보이지만 같은 활성 패널의 **직전 커서 행**을 알면 그 행 — Claude 가 '생각 중'
       커서를 숨기면 배지가 화면 맨 위로 튀던 것을 막는다(요청 2026-06-21).
    3. 둘 다 없으면 활성 패널의 **마지막 내용 행**(프롬프트가 있는 쪽), 그마저 없으면 0.

    `pane_box` = `(x, y, w, h)` 또는 None.
    """
    if cursor_xy is not None:
        return cursor_xy[1]
    if last_row is not None:
        return last_row
    if pane_box:
        return pane_box[1] + pane_box[3] - 1
    return 0


def badge_span(label, right_edge, reserve_right):
    """배지가 차지할 `(x0, x_end_exclusive)` — 폭이 모자라면 None.

    `right_edge` 는 **exclusive** 한 오른쪽 경계(활성 패널의 우측)다."""
    text = "[" + label + "]"
    x_end = right_edge - reserve_right
    x0 = x_end - text_width(text)
    if x0 < 0:
        return None
    return (x0, x_end)


def ime_cells(label, row, right_edge, reserve_right=0):
    """`(runs, span)` — 배지 런 하나와 그 칸 범위(없으면 `([], None)`).

    자리는 **창 절대 좌표**다. 색은 `theme` 에 **의미 이름**만 싣고 각 클라가 자기
    테마에서 푼다(서버가 hex 를 실으면 서버가 UI 를 알게 된다 — 설계 §10 위험표).
    """
    if not label or row < 0:
        return [], None
    span = badge_span(label, right_edge, reserve_right)
    if span is None:
        return [], None
    run = {"x": span[0], "y": row, "text": "[" + label + "]",
           "style": {"f": "black", "bo": 1},
           "theme": {"b": _THEME.get(label, "primary")}}
    return [run], span
