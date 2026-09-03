"""클라이언트 순수 유틸리티 — 화면 폭·토큰 포맷·셸 argv·키 정규화·상수.

client.py 의 거대 클로저(build_client_app)에서 분리한, 클로저 상태(config/sock_path)
를 캡처하지 않는 순수 함수·상수 모음(§10 LLM 친화 리팩토링). client.py 가 이름을
그대로 import 해 쓰므로 동작은 불변이다."""
from __future__ import annotations

import os
import re
import sys
from functools import lru_cache

from rich.style import Style
from rich.terminal_theme import DEFAULT_TERMINAL_THEME, TerminalTheme

from . import i18n, proc
from .keymap import DRAG_COPY_VALUES
from .cellwidth import attaches, char_advance, char_cells

def _shell_argv(cmd: str) -> list:
    """run-shell/if-shell/display-popup 의 셸 명령 argv. OS 별 셸로 분기.

    POSIX: /bin/sh -c <cmd>,  Windows: cmd /c <cmd> (COMSPEC 우선).
    셸 분기 로직은 server(pipe-pane)와 공유하기 위해 proc.shell_argv 에 둔다.
    """
    return proc.shell_argv(cmd)


# ── §2.13 OS 네이티브 선택 박스드로잉 정화 ────────────────────────────────────
# 터미널 에뮬레이터의 네이티브 마우스 드래그 선택으로 pytmux 화면을 복사하면, 패널
# 테두리(박스드로잉 U+2500–U+257F)가 각 줄 끝(우측 테두리 │)·앞(좌측 테두리)에 딸려
# 들어오고, 가로 구분선(├──┤)만으로 된 줄도 섞여 코드 붙여넣기가 깨진다. pytmux 자체
# copy-mode·Shift+드래그 선택(§2.4)은 활성 패널 가로 범위로 클램프해 이미 깨끗하지만,
# OS 네이티브 선택은 **복사 시점에 가로챌 수 없다** — pytmux 가 손댈 수 있는 유일한 지점은
# 그 텍스트가 `paste-clipboard`(Ctrl+V)로 다시 들어올 때다. 거기서 테두리를 타깃 제거한다.
# 줄 **끝/앞**에 붙은 테두리 런(과 인접 공백)만 떼므로 줄 내부의 박스드로잉(markdown
# 표·neofetch 아트)은 보존해 오탐을 최소화한다(타깃형 — 사용자 선택).
#
# **왜 정규식이 아닌가**(검수 2026-07-30, 성능 결함 수정): 종전 끝쪽 규칙
# `[ \t]*[─-╿]+[ \t]*\r?$` 는 줄 길이의 **제곱**이었다 — `re.sub` 은 모든 시작 위치에서
# 선행 `[ \t]*` 로 공백 런을 끝까지 훑고 실패한다. 실측(이 머신): 공백 한 줄 2만 자
# =1.3초 · 5만 자=8.1초 · **10만 자=32초**. 붙여넣기(`paste-clipboard`, 기본 on)는 이걸
# **이벤트 루프에서** 돌리므로(클립보드 읽기만 to_thread) 오른쪽을 공백으로 채운 넓은
# 텍스트(스프레드시트·`column`·wide 터미널 복사) 한 번에 클라가 통째로 멎었다. 마우스
# 복사(copy-unwrap)도 같은 헬퍼를 쓴다. 아래 세 헬퍼는 **같은 판정을 선형**으로 한다
# (동형은 test_clientutil 의 차분 오라클이 옛 정규식과 대조해 고정).
_BOX_LO, _BOX_HI = "─", "╿"      # ─ … ╿ (박스드로잉 블록)
_PAD = " \t"


def _is_box(ch: str) -> bool:
    return _BOX_LO <= ch <= _BOX_HI


def _cut_trail_box(s: str) -> str:
    """줄 끝의 `공백* 박스런 공백* \\r?` 를 뗀다(박스런이 없으면 무변경)."""
    i = len(s)
    if i and s[i - 1] == "\r":
        i -= 1
    j = i
    while j and s[j - 1] in _PAD:
        j -= 1
    k = j
    while k and _is_box(s[k - 1]):
        k -= 1
    if k == j:
        return s                    # 박스런 없음 → 끝의 공백·\r 까지 그대로 둔다
    while k and s[k - 1] in _PAD:
        k -= 1
    return s[:k]


def _cut_lead_box_pad(s: str) -> str:
    """줄 앞의 `박스런 공백*` 를 뗀다(붙여넣기 규칙 — 안쪽 패딩까지 먹는다)."""
    i = 0
    n = len(s)
    while i < n and _is_box(s[i]):
        i += 1
    if not i:
        return s
    while i < n and s[i] in _PAD:
        i += 1
    return s[i:]


def _cut_lead_box(s: str) -> str:
    """줄 앞의 `공백* 박스런` 을 뗀다(복사 규칙 — 안쪽 패딩 한 칸은 **남긴다**:
    그 칸이 copy-unwrap 의 매달림 들여쓰기 신호다)."""
    i = 0
    n = len(s)
    while i < n and s[i] in _PAD:
        i += 1
    j = i
    while j < n and _is_box(s[j]):
        j += 1
    return s if j == i else s[j:]


def strip_box_drawing(text: str) -> str:
    """OS 네이티브 선택으로 복사된 텍스트에서 패널 테두리 오염을 제거한다(§2.13 문제 1).

    각 줄에서 앞·뒤에 붙은 박스드로잉 런(과 인접 공백·trailing \\r)을 떼고, 그 결과 비게 된
    테두리/구분선 전용 줄은 버린다. 줄 내부의 박스드로잉과 **원래** 빈 줄(문단 구분)은
    보존한다. 박스드로잉이 전혀 없으면 무변경(no-op)이라 일반 붙여넣기에 안전하다.

    한계(§2.13 문제 2 — 비활성 패널 텍스트 혼입): 출처 패널의 가로 범위를 알아야 잘라낼 수
    있어 출처 정보가 없는 붙여넣기 경로에선 불가 — pytmux 자체 선택(§2.4)이 출처에서 처리한다.
    """
    out = []
    for line in text.split("\n"):
        s = _cut_trail_box(_cut_lead_box_pad(line))
        if not s and line.strip():
            continue          # 테두리/구분선만이던 줄 → 제거(원래 빈 줄은 유지)
        out.append(s)
    return "\n".join(out)


# ── 마우스 복사 '앱-접힘' 펴기(copy-unwrap) ───────────────────────────────────
# 제보 2026-07-30: Claude Code 화면의 `! …` 셸 명령을 마우스로 긁어 복사하면, **앱이
# 스스로 접은** 자리마다 개행과 이어지는 줄의 매달림 들여쓰기가 함께 딸려와 셸/입력창에
# 그대로 붙여넣을 수 없었다(사용자는 별도 에디터에서 손으로 지우고 있었다).
#
# 왜 휴리스틱인가: 터미널 오토랩(DECAWM)으로 접힌 줄은 pytmux 가 `wrapped` 태그로
# **정확히** 알아 이미 한 줄로 잇는다(model.extract_range·_extract_selection). 하지만
# Claude Code 처럼 앱이 폭 미만에서 스스로 접어 **하드 개행**으로 내보낸 줄은 그 신호가
# 원천 부재다(ConPTY prewrap 과 같은 한계) → 남은 단서는 텍스트 모양뿐이다.
#
# 오탐(진짜 줄바꿈을 이어붙임)을 줄이는 게이트:
#   ① 이어지는 줄이 **들여쓰여** 있어야 한다(매달림 들여쓰기). 로그·`ls` 출력처럼 열 0
#      에서 시작하는 긴 줄들은 절대 이어붙지 않는다.
#   ② 앞 줄 끝에 이어지는 줄의 **첫 낱말이 들어갈 자리가 없었어야** 한다 — 들어갈 수
#      있었다면 앱은 접을 이유가 없었다(= 의도된 줄바꿈). 접힘 폭은 블록에서 관측된
#      최대 채움(fill)으로 추정한다.
#   ③ 블록이 폭 근처까지 차 있어야 하고(짧은 목록·YAML 배제), 앞 줄이 코드/셸의
#      **의도된 줄 끝 문자**로 끝나지 않아야 한다(`:` `;` `{` `}` `\`).
# 그래도 앱-접힘과 하드 개행이 원리상 구분 불가인 경우가 남는다 → `set copy-unwrap off`.
_UNWRAP_HANG_MAX = 12       # 매달림 들여쓰기 상한(더 깊으면 코드 블록으로 본다)
_UNWRAP_MIN_FILL = 24       # 이보다 좁은 블록은 접힐 일이 없다
_UNWRAP_TAIL_STOP = ":;{}\\"   # 이 문자로 끝난 줄은 '의도된 줄 끝'
# 접힘 폭 추정의 여유 칸. 앱은 보통 오른쪽에 1~2칸을 남기고 접는데(입력박스 패딩),
# 관측 최대 채움(fill)은 그만큼 작게 잡힌다 — 그러면 이어지는 줄의 첫 낱말이 `|`·`&`
# 처럼 1글자일 때 "들어갈 자리가 있었다"로 오판해 접힘을 놓친다(실측).
_UNWRAP_SLACK = 2
# 복사 쪽 앞 테두리 제거는 붙여넣기 쪽(_cut_lead_box_pad)과 둘이 다르다: **안쪽 패딩 한
# 칸을 남긴다**(그 칸이 매달림 들여쓰기 신호 ①다) — `_cut_lead_box` 가 그 규칙이다.
# 패널 왼쪽에 여백을 두고 박스를 그리는 앱을 위해 테두리 앞 공백도 함께 먹는다.


def _vis_width(s: str) -> int:
    """문자열의 표시 칸 수(한글·CJK 2칸). 접힘 판정은 칸 기준이라 len() 은 못 쓴다."""
    return sum(char_cells(ch) for ch in s)


def _line_indent(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def _is_app_wrap(prev: str, cur: str, fill: int, prev_cells: int) -> bool:
    """`cur` 가 `prev` 의 이어지는 줄(앱이 접은 자리)인지 — 위 ①②③ 게이트.
    `prev_cells` = `prev` 가 실제로 차지한 칸 수(첫 줄은 시작 열이 더해져 있다)."""
    if not prev.strip() or not cur.strip():
        return False
    if not 0 < _line_indent(cur) <= _UNWRAP_HANG_MAX:
        return False                                  # ① 매달림 들여쓰기 없음
    if prev[-1] in _UNWRAP_TAIL_STOP:
        return False                                  # ③ 의도된 줄 끝
    word = cur.strip().split(" ", 1)[0]
    return prev_cells + 1 + _vis_width(word) + _UNWRAP_SLACK > fill         # ②


def unwrap_copy_text(text: str, width: int, first_col: int = 0) -> str:
    """마우스 선택 텍스트에서 **앱이 접은** 줄바꿈과 그에 딸린 들여쓰기·테두리를 없앤다.

    `width` = 선택이 일어난 패널의 내용 폭(칸). 한 줄 선택은 사용자가 고른 그대로가
    정답이라 손대지 않고, 폭을 모르면(0/None) 판정 근거가 없어 그대로 돌려준다.
    이어붙일 때는 낱말 사이에 공백 한 칸을 넣는다(앱의 접힘은 낱말 경계에서 일어난다).

    `first_col` = 첫 줄이 시작하는 패널 내 열(드래그 시작 칸). 첫 줄만 그 칸에서
    잘려 나와 나머지 줄보다 짧으므로, 칸 계산에 이 값을 되돌려 줘야 접힘 폭 추정이
    어긋나지 않는다(매달림 들여쓰기가 깊은 표시에서 실제로 판정을 놓쳤다).
    """
    if not text or "\n" not in text or not width or width <= 0:
        return text
    lines, barrier = [], set()
    for raw in text.split("\n"):
        s = _cut_trail_box(_cut_lead_box(raw)).rstrip()
        if not s and raw.strip():
            # 테두리/구분선만이던 줄(입력박스 위아래 가로줄) → 버리되 그 자리를
            # **경계**로 남긴다. 안 그러면 구분선을 지운 뒤 위아래 남남인 줄(윗
            # 대화 끝줄과 입력줄)이 맞붙어 이어붙을 수 있다.
            barrier.add(len(lines))
            continue
        lines.append(s)
    if len(lines) < 2:
        return "\n".join(lines)
    # 칸 폭: 첫 줄만 시작 열을 더해 다른 줄과 같은 원점(패널 열 0)으로 맞춘다.
    pad = max(0, int(first_col or 0))
    cells = [_vis_width(l) + (pad if i == 0 else 0) for i, l in enumerate(lines)]
    fill = max(cells)
    if fill < max(_UNWRAP_MIN_FILL, width // 2):
        return "\n".join(lines)      # 폭 근처까지 안 간 블록 = 접힘이 아니다
    out = [lines[0]]
    for i in range(1, len(lines)):
        if i not in barrier and _is_app_wrap(lines[i - 1], lines[i],
                                             fill, cells[i - 1]):
            out[-1] = out[-1] + " " + lines[i].strip()
        else:
            out.append(lines[i])
    return "\n".join(out)


# 셀 폭은 cellwidth.char_cells 한 곳이 권위다(모호폭 wide 모드를 일관 반영). 종전
# 로컬 `_char_cells` 는 그 별칭 — 임포트 경로(`from .clientutil import _char_cells`)와
# lru 메모이즈(C1 PERFORMANCE_REVIEW 2026-06-07: 합성 셀 루프·TabBar·상태줄에서 문자
# 1개당 호출, ASCII 절대다수라 적중률≈100%)를 그대로 유지한다.
_char_cells = char_cells
# 칸을 **나눌 때** 쓰는 자(폭 0 글자는 0). 같은 이유로 여기 별칭을 둔다.
_char_advance = char_advance
# 「이 글자가 앞 칸에 얹히나」 — 폭 0 이거나 **군집이 이어질 때**(pytmux-407 ⓐ).
_attaches = attaches


# 이모지(컬러 픽토그래프) 코드포인트 대략 범위. 팝업이 떠 본문을 어둡게 칠할 때,
# 이모지는 터미널이 셀 스타일을 무시하고 컬러로 그려 안 어두워지므로 placeholder 로
# 치환한다(#25). CJK/한글·기하도형(○◐ 등)·박스문자는 스타일대로 어두워지므로 제외.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF), (0x1FC00, 0x1FFFD),
    (0x2600, 0x27BF),   # 기타 기호·딩뱃(✽ ✓ ☀ 등 컬러로 그려질 수 있음)
    (0x2B00, 0x2BFF), (0x2300, 0x23FF),  # ⌛⏳⏰ 등
    (0x2190, 0x21FF),  # 화살표 일부(컬러 이모지 변형)
    (0xFE00, 0xFE0F),  # variation selectors
)


# C3(PERFORMANCE_REVIEW 2026-06-07): 합성(_composite) 핫패스가 셀/프레임마다 새로
# 만들던 **불변** Style·dict 를 모듈 상수로 호이스트한다. 시각 결과는 동일하고 객체
# 할당만 제거한다(대형 선택·풀리페인트서 셀당 누적되던 비용).
_REVERSE_STYLE = Style(reverse=True)            # 커서·copy-mode 선택 반전
_TB_ACTIVE_STYLE = Style(color="black", bgcolor="cyan")    # pane-border-status 활성
_TB_INACTIVE_STYLE = Style(color="black", bgcolor="white")  # 비활성
_TB_BORDER_STYLE = Style(color="grey50")        # 제목줄 채움선(라벨 뒤 ─)
# 박스 문자 ↔ 변 비트(U=8,D=4,L=2,R=1): 겹치는 경계를 합쳐 ┬┴├┤┼ 로 연결.
_BOX_BITS = {"─": 0b0011, "│": 0b1100, "┌": 0b0101, "┐": 0b0110,
             "└": 0b1001, "┘": 0b1010, "├": 0b1101, "┤": 0b1110,
             "┬": 0b0111, "┴": 0b1011, "┼": 0b1111}
_BOX_REV = {v: k for k, v in _BOX_BITS.items()}


@lru_cache(maxsize=512)
def _with_reverse(st: Style) -> Style:
    """st 에 반전(reverse)을 더한 Style. 대형 copy-mode 선택은 같은 바탕 스타일 셀이
    많아 적중률이 높다 — 셀마다 `st + Style(reverse=True)` 를 새로 만들지 않는다.
    Style 은 불변·hashable 이라 캐시 키로 안전하다(C3)."""
    return st + _REVERSE_STYLE


def _is_emoji(ch: str) -> bool:
    """ch 가 컬러 이모지로 렌더될 가능성이 높은 문자인지(어둡게 안 되는 대상, #25)."""
    if not ch:
        return False
    o = ord(ch[0])
    return any(a <= o <= b for a, b in _EMOJI_RANGES)


def _dim_cell(ch, st):
    """이미 합성된 그리드의 한 셀을 어둡게(딤). 컬러 이모지는 터미널이 셀 스타일을
    무시하고 자체 색 글리프로 그려 안 어두워지므로 폭1 placeholder(·)로 치환한다(#25)
    — 모달 배경 딤(client._composite)과 동일한 단일 · 치환 방식. clock/calendar
    오버레이 딤이 이 헬퍼를 공유해, 시계/달력 모드 배경의 이모지(예 ✅)가 함께
    어두워지지 않고 밝게 남던 버그를 막는다. (폭2 이모지는 첫 셀=글리프, 다음 셀은
    연속칸이라 글리프 셀만 치환해도 폭이 보존된다.) 오버레이를 끄면 재합성이 원본에서
    다시 그려 자동 원복된다."""
    if ch and _is_emoji(ch):
        ch = "·"
    return (ch, _darken_style(st))


def _deemoji_text(s: str) -> str:
    """문자열 안 컬러 이모지 글리프를 같은 폭의 placeholder(·)로 치환한다(#25).

    _composite 그리드 밖의 위젯(상태표시줄·탭바)은 반투명 모달 backdrop 으로 딤될
    때 Textual 이 셀 스타일색만 블렌딩하는데, 컬러 이모지(⚠ 등)는 터미널이 셀
    스타일을 무시하고 자체 색 글리프로 그려 어두워지지 않고 밝게 남는다. 본문 grid
    의 _dim_cell 과 동일하게 글리프를 ·(폭 보존: _char_cells 칸)로 바꿔 surrounding
    딤 텍스트와 일관되게 만든다. 모달을 닫으면 위젯이 원본 문자열로 다시 렌더된다."""
    if not s or not any(_is_emoji(c) for c in s):
        return s
    return "".join("·" * _char_cells(c) if _is_emoji(c) else c for c in s)


def _fmt_tokens(total: int) -> str:
    """누적 토큰 수를 짧게 표기. 1234567→"1.2M", 45200→"45.2k", 800→"800".
    (서버측 tokens.fmt 과 동일 규칙 — 클라이언트 단독 표시용 경량 복제.)"""
    if total >= 1_000_000:
        return f"{total / 1_000_000:.1f}M".replace(".0M", "M")
    if total >= 1_000:
        return f"{total / 1_000:.1f}k".replace(".0k", "k")
    return str(total)


# 막대 게이지용 부분블록(1/8 단위) — 우측 끝 잔량을 부드럽게 표현.
# 막대 프리미티브(`bar`)와 그 여덟 단 블록 표는 **UI 무의존 모듈**로 옮겼다
# (`usagebar.py` — 2026-08-02f). 이 모듈은 최상단에서 `rich.style` 을 읽으므로 서버가
# 같은 막대를 못 그린다. 종전 이름으로 읽는 자리를 위해 아래에서 다시 내보낸다
# (`bar_floating*` 은 여기 남는다 — 표시 전용이고 서버가 안 쓴다).
from .usagebar import _BAR_BLOCKS, bar  # noqa: F401,E402  (재수출 — 종전 이름 보존)


def bar_floating_segments(start: float, end: float, vmax: int, cells: int):
    """bar_floating 의 떠 있는 막대를 (선행칸수 lead, 채움문자열 fill) 로 분해한다 —
    호출부가 선행 [0, start) 을 본 막대보다 **연한 색**으로 칠할 수 있게(요청
    2026-06-17). lead 는 통째로 칠하거나 비우는 시작 칸 수(start 를 칸 경계로 내림),
    fill 은 [start, end] 를 bar() 처럼 1/8 칸 정밀도로 채운 블록 문자열. 빈 폭/vmax<=0
    은 (0, "")."""
    if cells <= 0 or vmax <= 0:
        return 0, ""
    start = max(0.0, min(float(vmax), float(start)))
    end = max(start, min(float(vmax), float(end)))
    s_cell = int(start / vmax * cells)            # 시작 칸(내림)
    fill_eighths = int(round(end / vmax * cells * 8)) - s_cell * 8
    if fill_eighths <= 0:
        return s_cell, ""
    full, rem = divmod(fill_eighths, 8)
    avail = cells - s_cell
    full = min(full, avail)
    seg = "█" * full + (_BAR_BLOCKS[rem] if rem and full < avail else "")
    return s_cell, seg


def bar_floating(start: float, end: float, vmax: int, cells: int) -> str:
    """[start, end] 구간만 채운 '떠 있는' 막대 — 계단식 누적 표현용(bar() 와 같은
    스케일 규약, 0..vmax → cells 칸). 앞쪽 [0, start) 는 공백, [start, end] 는 채움,
    (end, vmax] 는 잘림. 시각별 5h% 막대를 이전 시각이 끝난 위치에서 시작해 누적
    계단으로 그릴 때 쓴다(요청 2026-06-17). 선행을 연한 색으로 칠하려는 호출부는
    bar_floating_segments 로 lead/fill 을 따로 받아 색을 입힌다.

    start 는 칸 경계로 **내림**한다 — 좌측을 부분만 채우는 블록 문자가 없어 시작 칸은
    통째로 비우거나 통째로 시작한다. end 는 bar() 처럼 1/8 칸 정밀도로 채운다. 빈
    구간(end<=start, 또는 둘 다 0)은 선행 공백만(막대 없음) — 숫자(%)가 값을 전한다."""
    lead, fill = bar_floating_segments(start, end, vmax, cells)
    return " " * lead + fill


def format_option_row(spec, sel_idx):
    """옵션 피커 한 행 표시문: '라벨:  ◀ ▶  현재선택지'. 라벨·선택지를 로케일 번역(t,
    미등록은 원문 폴백)하고 선택지가 2개 이상이면 ←→ 화살표를 붙인다. 코어
    CommandOptionsScreen·플러그인 ModelCtxScreen 의 옵션 행 렌더 공유(중복 제거)."""
    choices = spec["choices"]
    cur = i18n.t(choices[sel_idx][0])
    arrows = "◀ ▶" if len(choices) > 1 else "    "
    return f"{i18n.t(spec['label'])}:  {arrows}  {cur}"


# 상태줄 오른쪽 strftime 코드 분류 — 시각(시계) vs 날짜(달력) 클릭 존 분리용.
_TIME_STRFTIME = set("HIMSpRTrXkl")
_DATE_STRFTIME = set("YymdbBaAjeDFuwUWxgGCV")


# 블록 폰트 자산은 **UI 무의존 모듈**로 옮겼다(`blockfont.py` — 설계 Tier B · P3).
# 이 모듈은 최상단에서 `rich.style` 을 읽으므로 서버가 같은 글리프를 못 쓴다.
# 종전 이름으로 읽는 자리(clock/calendar 의 render·테스트)를 위해 여기서 다시 내보낸다.
from .blockfont import (  # noqa: F401,E402  (재수출 — 종전 이름 보존)
    _CLOCK_FONT, _CLOCK_FONT_BIG, _CLOCK_FONT_BIG_COLS, _CLOCK_FONT_BIG_ROWS,
    _CLOCK_FONT_COLS, _CLOCK_FONT_ROWS, clock_font_for,
)


# 한글 두벌식 자모 → QWERTY 영문 키. IME 가 켜져 있어도 단축키가 동작하도록
# 키 매칭 시 자모를 물리 키(영문)로 되돌린다.
_JAMO = {
    "ㅂ": "q", "ㅈ": "w", "ㄷ": "e", "ㄱ": "r", "ㅅ": "t", "ㅛ": "y", "ㅕ": "u",
    "ㅑ": "i", "ㅐ": "o", "ㅔ": "p", "ㅁ": "a", "ㄴ": "s", "ㅇ": "d", "ㄹ": "f",
    "ㅎ": "g", "ㅗ": "h", "ㅓ": "j", "ㅏ": "k", "ㅣ": "l", "ㅋ": "z", "ㅌ": "x",
    "ㅊ": "c", "ㅍ": "v", "ㅠ": "b", "ㅜ": "n", "ㅡ": "m",
    # 시프트(쌍자음/이중모음) → 대문자 영문
    "ㅃ": "Q", "ㅉ": "W", "ㄸ": "E", "ㄲ": "R", "ㅆ": "T", "ㅒ": "O", "ㅖ": "P",
}


# macOS Option(⌥) + 숫자열 → 그 키의 **레이아웃 글자**. 되돌림표(글자 → 물리 키).
#
# 왜 필요한가: pytmux 의 탭 전환은 `ESC` 다음 숫자다. ESC 직후 숫자를 아주 빨리 치면
# 터미널이 둘을 한 시퀀스(`\x1b<숫자>`)로 합쳐 보내는데, Option 을 **메타**로 쓰는
# 터미널에선 그게 `alt+<숫자>` 로 도착해 clientio 가 탭 전환으로 받아 준다. 그런데
# macOS 기본 설정(Option = 일반 키)에서는 같은 손동작이 ESC 접두가 아니라 **이 글자들**
# (⌥1=¡, ⌥2=™ …)로 도착해, 탭이 안 바뀌고 패널에 엉뚱한 글자가 찍혔다(제보 2026-07-28).
# 글자를 물리 키로 되돌려 `alt+<숫자>` 와 같은 경로로 보낸다 — 자모 되돌림(_JAMO)과 같은
# 발상이다. 숫자열 12키만 다룬다(탭 번호와 그 양 끝 -/=). 이 글자를 정말 입력해야 하면
# `:` 명령 프롬프트·작성창(별도 스크린 — 이 정규화를 안 탄다)을 쓴다.
MAC_OPT_NUMBER_ROW = {
    "¡": "1", "™": "2", "£": "3", "¢": "4", "∞": "5", "§": "6",
    "¶": "7", "•": "8", "ª": "9", "º": "0", "–": "-", "≠": "=",
}


def _normalize_key(k: str) -> str:
    """Textual 키 문자열에서 한글 자모를 QWERTY 영문 키로 정규화."""
    if not k:
        return k
    for pfx in ("ctrl+", "shift+", "alt+", "meta+"):
        if k.startswith(pfx):
            base = k[len(pfx):]
            return pfx + _JAMO.get(base, base)
    return _JAMO.get(k, k)


# 한영 오타 복원(명령 프롬프트): IME 가 켜진 채 영문 명령을 치면 두벌식 자모가
# 음절로 합성돼 들어온다(예: "kill"→"ㅏㅑㅣㅣ", "split"→"�holl"… 식). 합성 음절을
# 초/중/종성으로 분해하고 복합 자모(겹받침·이중모음)를 낱자로 풀어 _JAMO 로 QWERTY
# 영문으로 되돌린다. ASCII 등 비-한글은 그대로 둔다.
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"            # 초성 19
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"          # 중성 21
_JONG = "_ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"  # 종성 28(0=없음)
# 복합 자모 → 두벌식상 그것을 만드는 낱자 시퀀스(각 낱자는 _JAMO 로 영문 변환).
_COMPOUND_JAMO = {
    "ㅘ": "ㅗㅏ", "ㅙ": "ㅗㅐ", "ㅚ": "ㅗㅣ", "ㅝ": "ㅜㅓ", "ㅞ": "ㅜㅔ",
    "ㅟ": "ㅜㅣ", "ㅢ": "ㅡㅣ",
    "ㄳ": "ㄱㅅ", "ㄵ": "ㄴㅈ", "ㄶ": "ㄴㅎ", "ㄺ": "ㄹㄱ", "ㄻ": "ㄹㅁ",
    "ㄼ": "ㄹㅂ", "ㄽ": "ㄹㅅ", "ㄾ": "ㄹㅌ", "ㄿ": "ㄹㅍ", "ㅀ": "ㄹㅎ",
    "ㅄ": "ㅂㅅ",
}


def _jamo_to_q(j: str) -> str:
    if j in _COMPOUND_JAMO:
        return "".join(_JAMO.get(x, x) for x in _COMPOUND_JAMO[j])
    return _JAMO.get(j, j)


def has_hangul(s: str) -> bool:
    """문자열에 한글(자모/호환자모/완성형 음절)이 하나라도 있으면 True."""
    return any(0x1100 <= ord(c) <= 0x11FF or 0x3130 <= ord(c) <= 0x318F
               or 0xAC00 <= ord(c) <= 0xD7A3 for c in s)


def norm_sep(s: str) -> str:
    """명령 검색 매칭에서 공백·언더바·하이픈을 모두 하이픈으로 통일한다.

    사용자가 단어 구분자를 무엇으로 치든(스페이스/언더바/하이픈) 같은 명령에
    매칭되게 한다 — 예: "rename ", "rename_", "rename-" 가 모두 "rename-tab" 에
    매칭. 검색어와 후보(명령 이름)에 똑같이 적용해 비교하면 구분자가 무시된다."""
    return s.replace(" ", "-").replace("_", "-")


def hangul_to_qwerty(text: str) -> str:
    """한글(두벌식 IME 로 잘못 입력된 영문)을 QWERTY 영문으로 되돌린다.

    완성형 음절은 초/중/종성으로 분해, 낱자/복합자모는 _JAMO 로 변환. 비-한글은
    그대로. 예: "ㅏㅑㅣㅣ"→"kill", "ㄴ푤ㅑㅅ"류 합성도 분해해 복원."""
    out = []
    for ch in text:
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:                 # 완성형 음절 → 분해
            s = o - 0xAC00
            cho, jung, jong = s // 588, (s // 28) % 21, s % 28
            out.append(_jamo_to_q(_CHO[cho]))
            out.append(_jamo_to_q(_JUNG[jung]))
            if jong:
                out.append(_jamo_to_q(_JONG[jong]))
        elif ch in _JAMO or ch in _COMPOUND_JAMO:  # 낱자/복합자모(미합성)
            out.append(_jamo_to_q(ch))
        else:
            out.append(ch)
    return "".join(out)


# mouse-debug 진단에서 기록할 '내비게이션 키' 안전 목록. 원격 휠이 alt-scroll
# (DECSET 1007) 변환으로 ↑/↓ 화살표로 새는지(=휠 이벤트는 안 오고 화살표만 옴)를
# 가려내기 위함. **여기 없는 키(특히 문자)는 절대 로그에 남기지 않는다** — 패널에
# 친 텍스트/비밀번호가 진단 로그로 유출되지 않게 하기 위한 화이트리스트.
_KEY_DIAG = frozenset({
    "up", "down", "left", "right", "pageup", "pagedown", "home", "end",
})


# ---- build_client_app 클로저에서 분리한 순수 헬퍼/상수(§10) ----
# 스타일 해석·키 바이트 변환·명령/메뉴 테이블 등 config/sock_path 를 캡처하지
# 않는 부분. client.py 클로저가 이 이름들을 그대로 import 해 참조한다.

DEFAULT_STYLE = Style()
_style_cache: dict = {}

# §1.7-a 원격(remote-attach) 탭/외곽선 분홍 — 로컬(파랑 primary)과 한눈에 구분.
# 활성 탭 배경·활성 패널 외곽선용 본색과, 비활성 외곽선용 어두운 변형.
REMOTE_PINK = "#ff5fd7"        # hot pink (256색 #206 근사)
REMOTE_PINK_DIM = "#af5f87"    # 비활성 외곽선(어두운 분홍)

# §10-21ⓧ2 패널 글에서 **경로처럼 보이는 범위**를 찾는다.
#
# 넓히면 아프다: 산문 속 `a/b` 나 날짜 `2026/08/02` 도 경로처럼 보인다. 그래서 두 조건을
# **둘 다** 요구한다 — ⑴ 구분자(`/`·`\`)가 있고 ⑵ 마지막 조각에 확장자가 있다.
#
# ★ 이 규칙은 **네이티브 클라와 한 벌**이라야 한다(`client/crates/base/src/spans.rs`).
# 두 클라가 각자 판정하면 같은 줄에서 서로 다른 자리를 짚고, 그 어긋남은 나란히 놓아야만
# 보인다. 픽스처(`client/scripts/gen_spans_fixture.py`)가 이 함수를 직접 불러 대조한다.
_PATH_WRAP = set("()[]{}\"'`<>,")
_PATH_TRAIL = set(".,;:!?)]}>\"'`")


def _looks_like_path(word: str) -> bool:
    if len(word) < 3 or "://" in word:
        return False                       # 링크는 링크의 것이다
    if "/" not in word and "\\" not in word:
        return False                       # 낱말 하나는 경로로 안 본다
    last = word.replace("\\", "/").rsplit("/", 1)[-1]
    dot = last.rfind(".")
    return 0 < dot < len(last) - 1


def find_paths(line: str) -> list:
    """줄에서 경로 범위들을 `[(start, end, text)]` 로(글자 인덱스, `[start, end)`)."""
    out, i, n = [], 0, len(line)
    while i < n:
        if line[i].isspace() or line[i] in _PATH_WRAP:
            i += 1
            continue
        end = i
        while end < n and not line[end].isspace() and line[end] not in _PATH_WRAP:
            end += 1
        stop = end
        while stop > i and line[stop - 1] in _PATH_TRAIL:
            stop -= 1
        word = line[i:stop]
        if _looks_like_path(word):
            out.append((i, stop, word))
        i = max(end, i + 1)
    return out


def path_at(line: str, index: int):
    """그 자리(글자 인덱스)에 걸친 경로 범위. 없으면 `None`."""
    for start, end, text in find_paths(line):
        if start <= index < end:
            return (start, end, text)
    return None


# §10-21ⓓ2 원격 탭 제목을 **표시할 때만** 접는 형식. 값(이름 자체)은 안 바꾼다.
REMOTE_TITLE_CHOICES = ("full", "host", "name")


def remote_title_display(name: str, remote: bool, mode: str) -> str:
    """원격 탭 이름 `⇄호스트:이름` 을 표시 형식에 맞게 접는다(§10-21ⓓ2).

    # ⚠ 이름 자체는 못 바꾼다

    그 문자열을 짓는 자리는 서버 한 곳이고(`serverremote.py`), 그것이 **`remote-detach`
    의 인자**이자 여러 소비자가 "`⇄` 와 첫 `:` 사이가 호스트"로 읽는 계약이다. 그래서
    접는 것은 **그리는 순간뿐**이고, 값으로 쓰는 자리는 원래 이름을 그대로 쓴다.

    # 왜 접나

    원격은 **이미 색으로 구분된다**(§1.7-a 분홍). 그 위에 아이콘·호스트까지 늘 붙어
    있으면 탭바에서 정작 탭 **이름**이 밀려난다 — 제보가 그 말이다.

    | 형식 | 보이는 것 |
    |---|---|
    | `full` | `⇄host:name`(기본 — 종전 그대로) |
    | `host` | `host:name`(아이콘만 뺀다 — 색이 이미 원격을 말한다) |
    | `name` | `name` |

    로컬 탭이면 무엇을 골랐든 그대로다. 판정은 **`remote` 플래그**로 한다 — 사용자가 탭
    이름을 `⇄…` 로 지어도 속지 않으려고 서버가 그 플래그를 따로 보낸다.
    모양이 예상과 다르면 통째로 이름으로 쓴다(파싱 실패가 탭을 지우면 안 된다)."""
    if not remote or mode == "full":
        return name
    rest = name[1:] if name.startswith("⇄") else name
    if mode == "host":
        return rest
    if mode == "name":
        head, sep, tail = rest.partition(":")
        return tail if sep else rest
    return name


# ── 앱이 낸 **ANSI 색**(SGR 30–37·90–97)을 hex 로 옮길 때 쓰는 팔레트 (pytmux-205) ──
#
# 화면에 나가는 것은 결국 truecolor 라 「빨강」을 어떤 hex 로 적을지 누군가는 정해야 한다
# (Textual 의 `ANSIToTruecolor` 필터가 그 자리다 · `App.ansi_theme_*`). 그 기본값이
# **Rich MONOKAI** 인데, 그 테마의 ANSI 표는 보통 터미널과 많이 다르다 — 실측(2026-08-22):
# 빨강(1·9) `#f4005f` · 밝은검정(8) `#625e4c`. 그래서 패널 앱이 `ESC[31m` 을 내면 화면에는
# **자홍**이, `ESC[90m` 을 내면 **올리브**가 찍혔다(제보 pytmux-205 의 그 두 색이 정확히 이것).
#
# 그래서 표준 팔레트(xterm/VGA 기본 16색)로 바꾼다. ⛔ 새 표를 손으로 적지 않는다 —
# Rich 의 `DEFAULT_TERMINAL_THEME` 이 이미 그 값이고, **이 저장소의 다른 자리도 이미 그것을
# 쓰고 있다**: `_darken_style`·`_dim_inactive_style` 의 `Color.get_truecolor()` 는 테마를
# 안 주므로 Rich 기본값 = 이 표를 쓴다. 종전에는 그래서 같은 ANSI 색이 **그리는 자리(MONOKAI)와
# 어둡게 하는 자리(표준)에서 서로 다른 색**이었다 — 이 상수가 그 둘을 한 표로 모은다.
#
# ⚠ 기본 전경/배경(ColorType.DEFAULT)만은 Rich 기본값(검정 글자·흰 배경)을 안 쓴다 —
# 그것을 쓰면 「기본색」이 어두운 화면에서 검정으로 찍힌다. 이 앱의 테마 폴백을 그대로 쓴다.
# ★ 더 옳은 것은 **호스트 터미널에 실제 팔레트를 물어보는 것**(OSC 4)이고 그 기계는 이미
#   있다(`launcher` 의 XTVERSION in-band 프로브) — 별건이라 여기서 안 한다.
# ⚠ `Palette` 는 슬라이스를 안 받는다(`__getitem__` 이 번호 하나만 받는다) — 번호로 집는다.
_ANSI_STD = [tuple(DEFAULT_TERMINAL_THEME.ansi_colors[i]) for i in range(16)]
ANSI_PALETTE_THEME = TerminalTheme(
    (0x12, 0x12, 0x12),               # 배경 = _THEME_FALLBACK["background"]
    (0xE0, 0xE0, 0xE0),               # 전경 = _THEME_FALLBACK["foreground"]
    _ANSI_STD[:8],                    # 0–7  표준
    _ANSI_STD[8:],                    # 8–15 밝은 쪽
)


# p4v-tui 와 동일한 textual-dark 테마 색을 따른다(없으면 폴백).
_THEME_FALLBACK = {
    "primary": "#0178D4", "secondary": "#004578", "accent": "#FEA62B",
    "background": "#121212", "surface": "#1E1E1E", "panel": "#242F38",
    "foreground": "#E0E0E0", "success": "#4EBF71", "warning": "#FEA62B",
    "error": "#B93C5B",
    "primary-darken-2": "#0053AA",   # Claude 헤더 배경(진한 파랑)
    "primary-darken-3": "#004295",   # 명령 프롬프트 명령어 배경(옅은 진파랑)
}

# P5: (테마이름, 변수명) → 해석색 캐시. theme_color 는 _composite·상태바·clientstatus
# 가 프레임/세그먼트마다 호출하는데, 매번 try/except + 이중 dict.get 였다. 활성 테마가
# 같은 동안 결과는 불변이므로 메모한다. 테마 전환 시 app.theme(이름)이 바뀌어 키가
# 갈리니 자연 무효화된다(이 앱은 테마를 바꾸지 않지만 정확성 위해 키에 포함).
_THEME_COLOR_CACHE: dict = {}


def theme_color(widget, name: str) -> str:
    """현재 Textual 테마에서 색을 해석(없으면 textual-dark 폴백). (P5) 테마이름+변수명으로 메모."""
    try:
        app = widget.app
        key = (getattr(app, "theme", ""), name)
        hit = _THEME_COLOR_CACHE.get(key)
        if hit is not None:
            return hit
        v = app.theme_variables.get(name)
        result = v if v else _THEME_FALLBACK.get(name, "white")
        _THEME_COLOR_CACHE[key] = result
        return result
    except Exception:
        return _THEME_FALLBACK.get(name, "white")

@lru_cache(maxsize=8192)
def _darken_style(st: Style, ratio: float = 0.55) -> Style:
    """ANSI dim 대신 실제 전경/배경 색을 검정 쪽으로 ratio 만큼 블렌드한 어두운
    색을 계산해 적용하고 bold 를 해제한다(§10). ANSI dim 은 터미널 의존적이라
    bold 글자(많은 터미널이 bold 를 밝게 렌더 → dim 상쇄)·명시적 밝은색 글자를
    충분히 어둡게 못 하는데, 실색 블렌드는 터미널 무관하게 균일하게 어둡다.
    전경색이 기본(None)이면 밝은 기본 전경이 안 묻히게 어두운 회색으로 둔다.

    §10-A #4: 팝업 배경 dim 은 전 화면 셀(수천 개)에 이 함수를 부르는데, 대부분
    셀의 스타일이 같으므로 (st, ratio) 키로 lru_cache 한다 — 같은 스타일은 한 번만
    블렌드 계산하고 재사용해 dim 적용을 같은 프레임에 끝낼 만큼 가볍게 만든다
    (Style 은 해시 가능·불변, 순수 함수라 캐시 안전)."""
    from rich.color import Color

    def _dark(col, fallback):
        if col is None:
            return fallback
        try:
            t = col.get_truecolor()
        except Exception:
            return fallback
        return Color.from_rgb(t.red * (1 - ratio),
                              t.green * (1 - ratio),
                              t.blue * (1 - ratio))

    return Style(color=_dark(st.color, Color.parse("grey23")),
                 bgcolor=_dark(st.bgcolor, None), bold=False)


@lru_cache(maxsize=8192)
def _dim_inactive_style(st: Style, ratio: float = 0.18) -> Style:
    """비활성 패널 셀을 활성 대비 '약간 옅게'(§2.9, 요청): 전경/배경 실색을 검정 쪽으로
    ratio(기본 0.18)만큼 살짝 블렌드해 패널 전체가 한 톤 가라앉아 보이게 한다 — 외곽선
    없이도 활성 패널이 도드라진다. ANSI dim 은 터미널 의존(bold 글자 상쇄 등)이라
    _darken_style 처럼 실색 블렌드를 쓰되, 그건 팝업용 강한 dim(검정 0.55 + 기본전경
    grey23 고정)이라 여기선 더 약하게 + 기본 전경은 중간 회색(grey46)으로 둬 평문도
    과하지 않게 옅어진다(전경 없는 셀이 새카매지지 않게). bold/italic 등 다른 속성은
    `+` 오버레이로 보존하고 색만 덮는다. 전 화면 셀(수천)에 부르므로 (st, ratio) lru_cache."""
    from rich.color import Color

    def _dim(col, fb):
        if col is None:
            return fb
        try:
            t = col.get_truecolor()
        except Exception:
            return col
        return Color.from_rgb(t.red * (1 - ratio),
                              t.green * (1 - ratio),
                              t.blue * (1 - ratio))

    new_bg = _dim(st.bgcolor, None) if st.bgcolor is not None else None
    return st + Style(color=_dim(st.color, Color.parse("grey46")), bgcolor=new_bg)


# pyte 계보 색 이름 중 **Rich 가 모르는 것** → Rich 이름. 이게 없으면 해당 색이 그냥
# 사라진다(아래 _rich_color 주석 참조).
#
# - `bright_brown`: SGR 93/103(밝은 노랑). 표준 노랑은 `yellow` 로 오는데 밝은 쪽만
#   `brown` 계열 이름이라 체계가 어긋나 있다.
# - `bfightmagenta`: SGR 105(밝은 마젠타 배경). **원 pyte 의 오타를 vtconst 가 의도적으로
#   보존**한 값이다(vtconst.py — "렌더 바이트 동일성"). 서버가 실제로 이 값을 보내므로
#   여기서 받아 줘야 한다. 서버 쪽 이름을 '고치면' 그쪽 계약이 깨지므로 표시 계층인
#   여기서 번역한다.
_COLOR_ALIASES = {
    "bright_brown": "bright_yellow",
    "bfightmagenta": "bright_magenta",
}


@lru_cache(maxsize=512)
def _rich_color(name: str | None) -> str | None:
    """서버가 준 색 이름을 Rich 가 이해하는 이름으로. 못 알아들으면 None(그 색만 포기).

    색을 **하나씩** 검증하는 이유: 종전엔 `Style(...)` 전체를 try 로 감싸고 실패하면
    `Style(reverse, bold)` 로 떨어뜨렸다. 그래서 색 이름 하나를 모르면 **기울임·밑줄·
    취소선까지 함께 사라졌다**. 실제로 SGR 93/103/105 에서 그 일이 일어나고 있었다
    (밝은 노랑은 CLI 도구가 흔히 쓴다). 이제 못 알아듣는 색만 버리고 나머지는 남는다.
    """
    if not name:
        return None
    name = _COLOR_ALIASES.get(name, name)
    try:
        from rich.color import Color
        Color.parse(name)
    except Exception:
        return None
    return name


def harden_no_color_filters(app) -> int:
    """`NO_COLOR` 가 켜진 상자에서 Textual 이 렌더 중 죽는 것을 막는다(§10-14).

    Textual(8.2.x ~ upstream main, 확인 2026-07-28)의 `filter.monochrome_style` 은
    `style.color` 를 **가드 없이** 읽는데 Rich `Segment.style` 은 `Optional[Style]` 이라
    `None` 이 정상값이다 → `Monochrome.apply` 가 `AttributeError: 'NoneType' object has
    no attribute 'color'` 로 터진다. 앱 **초기화** 경로라 테스트 아티팩트가 아니고,
    그 변수를 켜 둔 사용자의 클라는 뜨다가 죽는다(실측 `alienware`).

    **`NO_COLOR` 는 그대로 존중한다** — 색은 계속 빠지고, `None` 세그먼트만 통과시킨다
    (사용자 결정 2026-07-28: 그 변수를 무시하는 선택은 안 섞는다). upstream 이 고치면
    이 함수는 무해한 no-op 이 된다.

    반환 = 교체한 필터 수(그 변수가 없으면 0). 멱등 — 이미 교체된 것은 다시 안 감싼다.
    """
    try:
        from rich.segment import Segment
        from textual.filter import Monochrome, monochrome_style
    except Exception:
        return 0                      # Textual 이 이 필터를 없앴다면 할 일이 없다

    class _SafeMonochrome(Monochrome):
        """`None` 스타일 세그먼트를 그대로 통과시키는 것 말고는 상류와 같다."""

        def apply(self, segments, background):
            return [Segment(text,
                            monochrome_style(style) if style is not None else None,
                            None)
                    for text, style, _ in segments]

    filters = getattr(app, "_filters", None)
    if not isinstance(filters, list):
        return 0                      # 상류가 필터 목록 구조를 바꿨다 — 조용히 포기
    n = 0
    for i, f in enumerate(filters):
        if type(f) is Monochrome:     # 정확히 상류 클래스일 때만(멱등)
            filters[i] = _SafeMonochrome()
            n += 1
    return n


def make_style(d: dict) -> Style:
    if not d:
        return DEFAULT_STYLE
    key = tuple(sorted(d.items()))
    st = _style_cache.get(key)
    if st is None:
        attrs = dict(bold=bool(d.get("bo")), italic=bool(d.get("it")),
                     underline=bool(d.get("un")), reverse=bool(d.get("rv")),
                     strike=bool(d.get("st")))
        try:
            st = Style(color=_rich_color(d.get("f")),
                       bgcolor=_rich_color(d.get("b")), **attrs)
        except Exception:
            # 색은 이미 걸러 냈으므로 여기 오는 건 예상 밖이다. 그래도 속성은 지킨다.
            st = Style(**attrs)
        _style_cache[key] = st
    return st

SPECIAL = {
    "enter": b"\r", "tab": b"\t", "backspace": b"\x7f", "escape": b"\x1b",
    "space": b" ", "up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C",
    "left": b"\x1b[D", "home": b"\x1b[H", "end": b"\x1b[F",
    "pageup": b"\x1b[5~", "pagedown": b"\x1b[6~", "delete": b"\x1b[3~",
    "insert": b"\x1b[2~", "f1": b"\x1bOP", "f2": b"\x1bOQ", "f3": b"\x1bOR",
    "f4": b"\x1bOS", "f5": b"\x1b[15~", "f6": b"\x1b[17~", "f7": b"\x1b[18~",
    "f8": b"\x1b[19~", "f9": b"\x1b[20~", "f10": b"\x1b[21~",
    "f11": b"\x1b[23~", "f12": b"\x1b[24~",
    "shift+tab": b"\x1b[Z",   # backtab(CSI Z) — Claude 권한 모드 순환 등
    "shift+enter": b"\n",     # LF — Claude 등 입력 줄바꿈(Enter=CR 제출과 구분)
    "shift+escape": b"\x1b",  # 앱으로 ESC 전달(ESC 단독은 esc 모드 진입)
    # 수정자 포함 커서 키(표준 xterm CSI 1;mod 시퀀스)를 패널 앱에 그대로 전달한다.
    # 예전엔 매핑이 없어 b"" 로 버려졌다. Shift+Home/End 로 줄 선택 → Del(\x1b[3~)로
    # 삭제 같은 동작이 가능해진다(지원 여부는 패널 앱에 달림 — Claude Code 등). #14
    "shift+home": b"\x1b[1;2H", "shift+end": b"\x1b[1;2F",
    "shift+left": b"\x1b[1;2D", "shift+right": b"\x1b[1;2C",
    "shift+up": b"\x1b[1;2A", "shift+down": b"\x1b[1;2B",
    "ctrl+home": b"\x1b[1;5H", "ctrl+end": b"\x1b[1;5F",
}

def key_to_bytes(event: events.Key) -> bytes:
    k = event.key
    if k in SPECIAL:
        return SPECIAL[k]
    if k.startswith("ctrl+"):
        c = k[5:]
        # ASCII 영문 ctrl 조합만 제어코드로(한글 등 비ASCII는 무시)
        if len(c) == 1 and "a" <= c.lower() <= "z":
            return bytes([ord(c.lower()) - 96])
        if event.character and event.character.isascii():
            return event.character.encode("utf-8", "replace")
        return b""
    if event.character is not None and event.character != "":
        return event.character.encode("utf-8", "replace")
    return b""

# ── 명령 인자 파싱 / 재시작 드라이런 평가 (§5.4: build_client_app 클로저에서 분리한
#    app-상태 비의존 순수 함수). client.py 가 이름 그대로 import 해 쓴다 — 동작 불변.
def _opt_value(args, flag):
    """args 에서 flag 바로 뒤 토큰을 돌려준다(없으면 None). 예: ["-t","3"] → "3"."""
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def _first_int(args):
    """args 에서 첫 양의 정수 토큰을 int 로 돌려준다(없으면 None).

    플래그(-t)·음수 토큰은 건너뛰고 계속 스캔한다 — 과거엔 첫 음수에서 None 을
    반환해 뒤따르는 양수 인덱스를 가려 move-tab 등이 조용히 무시됐다(§2.8/#34).
    카운트·셀 수처럼 음수가 무의미한 인자용. 인덱스(음수=끝에서)는 _first_signed_int."""
    for a in args:
        if a.startswith("-"):
            continue
        if a.isascii() and a.isdigit():   # 비ASCII 유니코드 숫자(²·③)는 int() 가 깨짐
            return int(a)
    return None


def _signed_int(tok):
    """정수 토큰(앞 '-' 부호 허용)을 int 로. 정수가 아니면(또는 None) None.
    '3'→3, '-2'→-2, '-t'·'foo'·None→None."""
    if not tok:
        return None
    body = tok[1:] if tok[0] == "-" else tok
    # body.isdigit() 는 비ASCII 유니코드 숫자(²·③ 등)에도 True 지만 int() 는 그중 일부에
    # ValueError → ASCII 숫자만 정수로 본다(그런 토큰이 인자로 오면 None=무동작).
    return int(tok) if (body.isascii() and body.isdigit()) else None


def _first_signed_int(args):
    """args 에서 첫 정수 토큰(음수 포함)을 int 로 돌려준다(없으면 None).

    _first_int 와 달리 음수도 값으로 본다 — 탭 인덱스 명령이 '끝에서 N번째'
    (-1=마지막)를 받게 하려는 것(§2.8). '-t 2' 의 -t 는 정수가 아니라 건너뛰고 2 를
    잡고, 'move-tab -2' 의 -2 는 값으로 잡는다."""
    for a in args:
        v = _signed_int(a)
        if v is not None:
            return v
    return None


def _client_relaunch_ok() -> bool:
    """restart-all 의 클라 relaunch(os.execv)가 인자를 해석할 수 있는지 — run_client
    의 재실행 로직과 동일 판정(.py 진입점이거나 실행 가능 파일)."""
    a0 = sys.argv[0]
    return bool(a0) and (a0.endswith(".py") or os.access(a0, os.X_OK))


def _restart_server_relaunch_label(server_os) -> str:
    """첫 점검 줄의 라벨 — **그 서버의 OS 가 실제로 재는 것**을 적는다(§10-21ⓔ3).

    제보: Windows 이진에서도 `✗ 서버 re-exec 지원(POSIX·이벤트루프)` 이 떠, **못 하는
    것이 정상인 조건**을 실패로 보여 "재시작 불가"로 읽혔다. 실제로는 조건 자체가 이미
    OS 별로 갈려 있다 — POSIX 는 `os.execv` 로 자기를 덮어쓰고, Windows 는 그 시스템콜이
    없어 **pty-host 인수인계**(옵션 C)로 같은 일을 한다(`Server.restart_check` 의
    `reexec_supported` 가 그 둘을 이미 OR 로 잰다). 갈려 있던 것은 **라벨뿐**이었다.

    ⚠ 클라의 OS 로 판단하면 안 된다 — 서버는 원격일 수 있다(페더레이션·ssh). 그래서
    서버가 `server_os` 를 적어 보내고, 모르면(옛 서버) 종전 문구로 남는다."""
    if server_os == "windows":
        return "서버 재기동 지원(pty-host 인수인계)"
    return "서버 re-exec 지원(POSIX·이벤트루프)"


def _restart_check_eval(m, cli_ok, kind="all"):
    """서버 restart_check 결과(m) + 클라 측 점검(cli_ok)을 (safe, checks) 로 평가.

    kind="server" 는 클라를 relaunch 하지 않으므로 relaunch 점검을 제외한다.
    checks 는 (통과여부, 라벨) 리스트(팝업 표시용), safe 는 전체 AND.

    첫 줄의 **라벨만** 서버 OS 를 따른다(§10-21ⓔ3) — 재는 값도 순서도 그대로다."""
    panes, with_fd = m.get("panes", 0), m.get("panes_with_fd", 0)
    fd_ok = (panes == with_fd and panes > 0)
    checks = [
        (m.get("reexec_supported"),
         _restart_server_relaunch_label(m.get("server_os"))),
        (m.get("has_sessions"), "복원할 세션 존재"),
        (m.get("serialize_ok"), "상태 직렬화 round-trip"),
        (fd_ok, f"패널 master fd 보유 ({with_fd}/{panes})"),
    ]
    if kind == "all":
        checks.append((cli_ok, "클라이언트 relaunch 인자 해석"))
    return all(ok for ok, _ in checks), checks


MENU_ITEMS = [
    ("split_lr", "패널 분할 │ (좌우)"),
    ("split_tb", "패널 분할 ─ (상하)"),
    ("zoom", "패널 줌 토글 ⛶"),
    ("rotate", "패널 회전 ↻"),
    ("swap_pane", "패널 교환 (다음 패널과)"),
    ("break_pane", "패널 → 새 탭으로 분리"),
    ("join_pane", "패널 → 다른 탭에 합치기 (join-pane <탭>)"),
    ("merge_remote_tab", "원격 탭 → 현재 탭에 pane 으로 머지 (같은 서버)"),
    ("rename_pane", "패널 제목 변경"),
    ("select_layout", "레이아웃 프리셋…"),
    ("next_layout", "다음 레이아웃 프리셋"),
    ("search", "스크롤백 검색"),
    ("search_all", "모든 탭·패널 검색"),
    ("kill_pane", "패널 삭제 ✕"),
    ("sync", "입력 동기화 토글"),
    ("autoresume", "토큰리밋 자동재개 토글"),
    ("prompt_clear", "프롬프트 단위 클리어 토글"),
    ("new_window", "새 탭"),
    ("new_claude_window", "새 탭에서 Claude Code 실행"),
    ("rename_window", "탭 이름 변경"),
    ("kill_window", "탭 삭제"),
    ("toggle_pin", "탭 고정 토글 (오른쪽 구역으로)"),
    ("choose_tree", "탭 선택기(트리)"),
    ("next_window", "다음 탭"),
    ("prev_window", "이전 탭"),
    ("layout_save", "레이아웃 저장(현재 탭)"),
    ("layout_load_over", "레이아웃 불러오기(현재 탭 덮어쓰기)"),
    ("layout_load_new", "레이아웃 불러오기(새 탭)"),
    ("mouse_help", "마우스 제스처 도움말"),
    ("command", "명령 입력"),
    ("settings", "⚙ 설정…"),
    ("detach", "detach (앱 종료, 셸 유지)"),
    ("kill_server", "서버 종료 (모든 탭/셸 종료)"),
]
# 토글 메뉴 항목(현재 on/off 표시·선택해도 메뉴 안 닫음). 상태는 status 에서 읽음.
MENU_TOGGLES = {"zoom", "sync", "autoresume", "prompt_clear", "toggle_pin"}

# §8.1 컨텍스트 메뉴 그룹(서브메뉴)화(요청 2026-06-18): 평면 29항목이 세로로 너무 길어,
# 묶을 수 있는 항목을 그룹으로 접고 자주/세션 항목만 최상위에 둔다. MENU_ITEMS(평면)는
# 라벨·토글 멤버십·i18n 기본값의 권위로 그대로 두고, 표시 구조만 아래 트리로 정의한다.
# 그룹 항목 키는 MENU_ITEMS 키의 부분집합 — 모든 액션은 여전히 서브메뉴 경유로 도달
# 가능하고 디스패치(_run_menu_action)는 leaf 키 그대로다.
MENU_GROUPS = {
    "pane": ["split_lr", "split_tb", "zoom", "rotate", "swap_pane",
             "break_pane", "join_pane", "merge_remote_tab", "rename_pane",
             "kill_pane"],
    "layout": ["select_layout", "next_layout", "layout_save",
               "layout_load_over", "layout_load_new"],
    "tab": ["new_window", "new_claude_window", "rename_window", "kill_window",
            "toggle_pin", "choose_tree", "next_window", "prev_window"],
}
# 최상위 표시 순서. "group:<g>"=서브메뉴 진입점, "--"=비선택 구분선, 그 외=직접 액션.
# 자주 쓰는 단독 항목·토글·세션 동작만 최상위에 두어 짧게 — 파괴적 동작
# (detach/kill_server)은 구분선 뒤로 격리한다. 플러그인 항목이 있으면 런타임에
# "group:plugin" 을 group:tab 뒤에 끼운다(MenuScreen._toplevel_entries).
MENU_TOPLEVEL = [
    "group:pane", "group:layout", "group:tab",
    "--",
    "search", "search_all", "command", "settings", "mouse_help",
    "sync", "autoresume", "prompt_clear",
    "--",
    "detach", "kill_server",
]
# 그룹 라벨(서브메뉴 진입점·헤더)의 ko 기본값. en 은 아래 카탈로그(menu.group.*)에서
# 등록하고, 코드는 i18n.t(f"menu.group.{g}", default=MENU_GROUP_LABELS[g]) 로 읽는다.
MENU_GROUP_LABELS = {
    "pane": "패널", "layout": "레이아웃", "tab": "탭", "plugin": "플러그인",
}

# 마우스 제스처 도움말은 list-keys 팝업("키 · 마우스" — §2.2 발견성, i18n keys.*)이
# 권위 — `mouse-help`/`mouse` 명령과 컨텍스트 메뉴 "마우스 제스처 도움말"은 그
# 별칭/진입점이다(제스처 목록을 두 벌 두지 않는다).

# 토큰 절감 설정 팝업(`token-saver`)의 행/순환 프리셋(SAVER_ROWS/SAVER_CYCLES)과
# 시작 규칙 편집(`claude-rules`)은 claude-code 플러그인(pytmuxlib/plugins/claude-code)
# 으로 이전했다 — 디렉토리를 지우면 두 명령·팝업이 조용히 사라진다.

# 명령 프롬프트(:)에서 쓸 수 있는 명령 목록 (이름, 설명) — ? 목록·자동완성용
# (이름, 설명, 카테고리). 카테고리는 ?/help 목록의 탭 그룹으로 쓰인다.
# 새 명령을 추가할 땐 카테고리도 함께 지정할 것(없으면 "기타"로 묶임).
COMMANDS = [
    ("split-window", "패널 분할 (-h 좌우 │ · -v/기본 상하 ─)", "패널"),
    ("kill-pane", "현재 패널 삭제", "패널"),
    ("resize-pane", "패널 크기 (-Z 줌 · -L/-R/-U/-D 분할선 이동)", "패널"),
    ("select-pane", "패널 이동 (-L/-R/-U/-D) 또는 제목 (-T)", "패널"),
    ("rename-pane", "패널 제목 변경", "패널"),
    ("swap-pane", "패널 위치 교환 (-U/-D 인접 · -s/-t 번호 임의)", "패널"),
    ("rotate-window", "윈도우 내 패널 회전", "패널"),
    ("break-pane", "패널을 새 탭으로 분리", "패널"),
    ("join-pane", "다른 탭의 패널을 현재로 합치기 (-h)", "패널"),
    ("respawn-pane", "패널 셸 재시작", "패널"),
    ("select-layout", "레이아웃 프리셋 (even-h/v, main-h/v, tiled)", "패널"),
    ("next-layout", "다음 레이아웃 프리셋", "패널"),
    ("synchronize-panes", "입력 동기화 토글 [on|off]", "패널"),
    ("new-tab", "새 탭 (새 윈도우 1개 생성, = new-window)", "탭"),
    ("new-claude-tab", "새 탭에서 Claude Code 실행 (현재 디렉토리 · esc c · 실행할 명령은 set claude-command)", "탭"),
    ("kill-tab", "탭 삭제 (= kill-window)", "탭"),
    ("next-tab", "다음 탭", "탭"),
    ("previous-tab", "이전 탭", "탭"),
    ("last-tab", "직전 탭", "탭"),
    ("select-tab", "탭 선택 (-t N)", "탭"),
    ("move-tab", "탭 이동 (-t N)", "탭"),
    ("move-tab-left", "현재 탭을 왼쪽으로", "탭"),
    ("move-tab-right", "현재 탭을 오른쪽으로", "탭"),
    ("move-tab-first", "현재 탭을 맨 앞으로", "탭"),
    ("move-tab-last", "현재 탭을 맨 뒤로", "탭"),
    ("pin-tab", "탭 고정(오른쪽 구역으로·실수 닫기 방지)", "탭"),
    ("unpin-tab", "탭 고정 해제", "탭"),
    ("pin-toggle", "탭 고정 토글 (별칭 pin)", "탭"),
    ("swap-tab", "탭 교환 (-t N)", "탭"),
    ("rename-tab", "탭 이름 변경", "탭"),
    ("automatic-rename", "탭 자동 이름 [on|off]", "탭"),
    ("choose-tree", "탭/패널 트리(전환·종료, 실행 앱·로컬/원격 표시. d/x=종료)", "탭"),
    # ncd 는 플러그인(pytmuxlib/plugins/ncd)이 등록한다 — 디렉토리를 지우면 명령
    # 검색·자동완성에서 조용히 사라진다(코어 목록엔 없음).
    ("capture-pane", "패널 내용을 버퍼로 캡처 (-S 전체)", "복사/버퍼"),
    ("pipe-pane", "패널 출력을 외부 명령으로 파이프", "복사/버퍼"),
    ("clear-history", "스크롤백 비우기", "복사/버퍼"),
    ("send-keys", "패널에 키 주입 (예: Enter, C-c)", "복사/버퍼"),
    ("send-escape", "활성 패널에 ESC 전달 (Shift+ESC 안 먹는 터미널용 — bind-key 로 전용 키에 바인딩)", "복사/버퍼"),
    ("redraw", "화면 전체 강제 재그리기 (깨진/잔상 화면 회복; 별칭 refresh·refresh-client, prefix r)", "설정/기타"),
    # paste-clipboard 를 paste-buffer 앞에 둔다(2026-06-16 요청): 'paste' 접두는 둘 다
    # 전체 접두 일치(_relevance_rank tier 1)라 동률 → 안정 정렬이 선언 순서를 보존하므로
    # 여기 순서가 곧 기본 하이라이트다. OS 클립보드 붙여넣기가 더 흔한 의도라 먼저 선택.
    ("paste-clipboard", "OS 클립보드 붙여넣기", "복사/버퍼"),
    ("paste-buffer", "페이스트 버퍼 붙여넣기 (N)", "복사/버퍼"),
    ("choose-buffer", "페이스트 버퍼 선택기", "복사/버퍼"),
    ("save-layout", "전체 레이아웃 저장(서버 영속)", "레이아웃"),
    ("restore-layout", "전체 레이아웃 복원(서버 영속)", "레이아웃"),
    ("layout-save", "현재 탭 레이아웃 저장 (이름)", "레이아웃"),
    ("layout-load", "레이아웃 불러오기 → 현재 탭 덮어쓰기 (이름)", "레이아웃"),
    ("layout-load-new", "레이아웃 불러오기 → 새 탭 (이름)", "레이아웃"),
    ("monitor-activity", "활동 모니터링 [on|off]", "모니터"),
    ("monitor-bell", "벨 모니터링 [on|off]", "모니터"),
    # capture-output 은 plugins/rec 로 이전(레지스트리 commands 로 병합).
    ("settings", "통합 설정 화면(모든 설정을 한 곳에서)", "설정/기타"),
    ("inactive-dim", "비활성 패널 흐리게 토글 [on|off]", "설정/기타"),
    ("inactive-dim-ratio", "비활성 패널 흐리게 세기 [0~0.8]", "설정/기타"),
    ("strip-box-drawing",
     "붙여넣기 시 패널 테두리(박스드로잉) 제거 토글 [on|off]", "설정/기타"),
    ("set", "옵션 설정 (prefix/mouse/status-*/mode-keys 등)", "설정/기타"),
    ("plugins", "플러그인 관리 — 설치된 플러그인 켜고/끄기(별칭 plugin-manager)",
                "설정/기타"),
    ("show-options", "현재 옵션 보기", "설정/기타"),
    ("set-hook", "이벤트 훅 설정 (<event> <cmd>)", "설정/기타"),
    ("show-hooks", "훅 목록 보기", "설정/기타"),
    ("source-file", "설정 파일 다시 불러오기", "설정/기타"),
    ("display-message", "상태줄에 메시지 표시", "설정/기타"),
    ("display-popup", "명령 실행 결과를 팝업으로", "설정/기타"),
    # clock-mode/open-clock/close-clock 은 clock 플러그인(pytmuxlib/plugins/clock),
    # calendar-mode/open-calendar/close-calendar 는 calendar 플러그인이 등록한다.
    ("single-border", "패널이 하나뿐일 때 테두리 표시 on/off (single-border on|off|toggle)", "설정/기타"),
    ("coalesce-repaints", "대량 출력 시 alt-screen 풀스크린 리페인트 합치기 on/off — ssh 반응성(coalesce-repaints on|off|toggle)", "설정/기타"),
    ("nest-auto-attach", "원격에서 pytmux 실행 시 거부 대신 자동 remote-attach 승격 on/off (nest-auto-attach on|off|toggle)", "설정/기타"),
    ("exit-empty", "세션이 0개여도 서버 종료 on/off — tmux exit-empty 동형, off=원격 federation 링크 보존용 상주(exit-empty on|off|toggle)", "설정/기타"),
    ("win-mouse-motion", "Windows 마우스 모션(any-motion) 패스스루 on/off — 기본 on(끄면 hover 계열이 안 산다 · pytmux-423) (win-mouse-motion on|off|toggle)", "설정/기타"),
    ("vt-parser", "VT 파서 백엔드 선택 pyte|native (재시작 시 발효 · vt-parser pyte|native)", "설정/기타"),
    ("window-size", "다중 클라 미러링 시 공유 크기 규칙 smallest|latest|largest — latest=마지막 조작 창 크기(window-size smallest|latest|largest)", "설정/기타"),
    # Claude Code 명령(auto-resume·token-log·
    # claude-usage·usage-panel·token-account·prompt-clear*·model·auto-doc-clear·
    # auto-compact·claude-auto-mode·auto-launch 등)은 claude-code 플러그인이 등록한다
    # (pytmuxlib/plugins/claude-code — 디렉토리 삭제 시 명령 검색·자동완성·디스패치에서 사라짐).
    ("version", "클라/서버 버전(p4 CL)·업타임 팝업(별칭 about)", "설정/기타"),
    ("debug-stats", "클라 런타임 계측 팝업 — 산 객체·GC 세대·판 깊이·Timer 수"
     "(-c 면 전체 수거까지 재고 그만큼 멎는다). pytmux-382 「수 일 두면 느려진다」"
     "를 그 자리에서 재는 자", "설정/기타"),
    ("lang", "UI 언어 전환 (lang ko|en) — 한국어/영어", "설정/기타"),
    ("run-shell", "셸 명령 실행", "설정/기타"),
    ("if-shell", "조건부 셸 실행", "설정/기타"),
    ("bind-key", "prefix 후 키에 명령 바인딩 (bind-key <key> <command> · -n 은 "
                 "prefix 없이 root)", "설정/기타"),
    ("unbind-key", "키 바인딩 해제 (unbind-key <key> | -n <key> | -a)", "설정/기타"),
    ("list-keys", "현재 키 바인딩 목록 팝업", "설정/기타"),
    ("mouse-help", "마우스 제스처 도움말 팝업(헤더 드래그 swap·탭 드래그·Shift+선택 등, "
                   "별칭 mouse)", "설정/기타"),
    # help/commands/list-commands 도 자동완성 후보에 잡히게 등록(§10 #8). 입력하면
    # 전체 명령 목록(CommandListScreen)을 연다 — '?' 입력도 같은 목록을 즉시 연다.
    ("help", "전체 명령 목록 보기('?' 도 동일, 카테고리 탭)", "설정/기타"),
    ("commands", "전체 명령 목록 보기(help 별칭)", "설정/기타"),
    ("list-commands", "전체 명령 목록 보기(help 별칭)", "설정/기타"),
    ("detach-client", "detach (앱 종료, 셸 유지)", "설정/기타"),
    ("kill-server", "서버와 모든 탭/셸 종료", "설정/기타"),
    ("restart-server", "작업 보존 재시작 — 셸/PTY 를 살린 채 서버 코드만 교체(재접속). 실행 전 드라이런 자동 점검, FAIL 시 재확인", "설정/기타"),
    ("restart-all", "전체 재시작 — 서버 세션유지 재시작 + 클라 재기동(별칭 full-restart). 서버·클라 코드 모두 갱신. 실행 전 드라이런 자동 점검, FAIL 시 재확인", "설정/기타"),
    ("restart-check", "독립 드라이런 — 실행 없이 안전성(re-exec·직렬화·fd·relaunch) 점검 팝업(별칭 restart-dry-run)", "설정/기타"),
    ("reconnect", "IPC 강제 재접속 — degraded(빨간 외곽선) 고착 회복(서버 보존)", "설정/기타"),
    ("remote-attach", "원격 pytmux 서버의 탭을 이 pytmux 에 어태치 (remote-attach <host>) — §1.7 페더레이션", "설정/기타"),
    ("remote-new-tab", "원격 pytmux 에 새 터미널을 만들어 새 탭으로 붙이기 (remote-new-tab <host>; 미어태치면 먼저 어태치)", "설정/기타"),
    ("remote-detach", "원격 어태치 해제 (remote-detach [host], 생략=전부)", "설정/기타"),
    ("merge-remote-tab", "같은 원격 서버의 다른 원격 탭을 현재 탭에 pane 으로 머지 "
                         "(피커, 별칭 merge-remote) — §1.7 페더레이션", "설정/기타"),
]

# 명령 프롬프트 자동완성 후보. 자주 쓰는 옵션 템플릿을 앞에 두어, 명령을 다 치면
# ghost 로 옵션(-h 등)까지 함께 제안된다(→ 로 수락). 뒤에 전체 명령 이름을 붙여
# 옵션이 없는 명령도 보완.
# `set <옵션>` ghost 자동완성용 옵션 이름 — client.apply_option 의 케이스와 일치시킨다
# (새 set 옵션 추가 시 여기도 한 줄). `set ` 까지 친 뒤 옵션 이름이 자동완성되게
# COMPLETIONS 에 "set <name>" 으로 병합한다(사용자 요청 2026-06-25).
_SET_OPTION_NAMES = (
    "prefix", "mouse", "mouse-drag-copy", "mouse-drag-threshold",
    "copy-unwrap", "mouse-debug", "alt-scroll", "set-clipboard",
    "ambiguous-width",
    "status", "status-bg", "status-fg", "status-left", "status-right",
    "status-format", "status-position", "status-interval", "mode-keys",
    "set-titles", "set-titles-string", "tab-bar", "default-path",
    "remote-title", "claude-command",
)

# `set <옵션> <값>` 의 선택지(enum/bool) — 값 자동완성(ghost)·후보 추천(↑↓)용.
# 자유 텍스트 옵션(status-bg/-fg/-left/prefix 등)은 정해진 값이 없어 제외(이력 추천만).
SET_OPTION_CHOICES = {
    "ambiguous-width": ("auto", "narrow", "wide"),
    "alt-scroll": ("on", "off"),
    "mouse": ("on", "off"),
    "mouse-drag-copy": DRAG_COPY_VALUES,
    "mouse-drag-threshold": ("1", "2", "3", "5", "8"),
    "copy-unwrap": ("on", "off"),
    "set-clipboard": ("on", "off"),
    "mouse-debug": ("on", "off"),
    "mode-keys": ("vi", "emacs"),
    "tab-bar": ("always", "auto"),
    "status-position": ("bottom", "top"),
    "remote-title": REMOTE_TITLE_CHOICES,
    "status": ("on", "off"),
    "set-titles": ("on", "off"),
}

COMPLETIONS = [
    "split-window -h", "split-window -v",
    "resize-pane -Z",
    "resize-pane -L", "resize-pane -R", "resize-pane -U", "resize-pane -D",
    "select-pane -L", "select-pane -R", "select-pane -U", "select-pane -D",
    "swap-pane -U", "swap-pane -D", "swap-pane -s", "swap-pane -t",
    "join-pane -h",
    "select-layout tiled", "select-layout even-horizontal",
    "select-layout even-vertical", "select-layout main-vertical",
    "select-layout main-horizontal",
    "capture-pane -S",
    "monitor-activity on", "monitor-bell on", "automatic-rename on",
    "detach-client", "kill-server",
] + ["set " + o for o in _SET_OPTION_NAMES] \
  + ["set %s %s" % (k, c) for k, cs in SET_OPTION_CHOICES.items() for c in cs] \
  + [c[0] for c in COMMANDS]

# 커맨드 팔레트(#3)에서 명령을 고르면 옵션(선택지)을 모달에서 정한 뒤 프롬프트를
# 거치지 않고 바로 실행한다. 각 항목은 {"key","label","choices":[(보임,값),...]}
# — 선택지(choice)뿐이라 모달이 키보드만으로 동작한다(자유 텍스트 인자가 필요한
# 명령은 schema 없이 기존처럼 프롬프트에 채운다). 값이 빈 문자열이면 토큰 생략.
_ONOFF = [("토글", ""), ("켜기", "on"), ("끄기", "off")]
COMMAND_OPTIONS = {
    "split-window": [{"key": "orient", "label": "방향", "choices": [
        ("좌우 분할 │ (-h)", "-h"), ("상하 분할 ─ (-v)", "-v")]}],
    "select-pane": [{"key": "dir", "label": "이동", "choices": [
        ("◀ 왼쪽", "-L"), ("▶ 오른쪽", "-R"),
        ("▲ 위", "-U"), ("▼ 아래", "-D")]}],
    "resize-pane": [{"key": "dir", "label": "동작", "choices": [
        ("줌 토글 ⛶", "-Z"), ("◀ 왼쪽", "-L"), ("▶ 오른쪽", "-R"),
        ("▲ 위", "-U"), ("▼ 아래", "-D")]}],
    "select-layout": [{"key": "preset", "label": "프리셋", "choices": [
        ("바둑판 tiled", "tiled"),
        ("가로 균등 even-horizontal", "even-horizontal"),
        ("세로 균등 even-vertical", "even-vertical"),
        ("메인 세로 main-vertical", "main-vertical"),
        ("메인 가로 main-horizontal", "main-horizontal")]}],
    "capture-pane": [{"key": "scope", "label": "범위", "choices": [
        ("보이는 영역", ""), ("스크롤백 전체 -S", "-S")]}],
    "synchronize-panes": [{"key": "state", "label": "동기화", "choices": _ONOFF}],
    "monitor-activity": [{"key": "state", "label": "활동", "choices": _ONOFF}],
    "monitor-bell": [{"key": "state", "label": "벨", "choices": _ONOFF}],
    # capture-output 옵션 스키마는 plugins/rec 가 command_options 로 기여.
    "inactive-dim": [{"key": "state", "label": "비활성흐리게", "choices": _ONOFF}],
    "strip-box-drawing": [{"key": "state", "label": "테두리제거", "choices": _ONOFF}],
    "inactive-dim-ratio": [{"key": "ratio", "label": "흐리게세기", "choices": [
        ("아주 옅게 0.10", "0.10"), ("옅게 0.18", "0.18"),
        ("보통 0.30", "0.30"), ("진하게 0.45", "0.45")]}],
    "automatic-rename": [{"key": "state", "label": "자동이름", "choices": _ONOFF}],
    "single-border": [{"key": "state", "label": "단일테두리", "choices": _ONOFF}],
    "coalesce-repaints": [{"key": "state", "label": "리페인트합치기", "choices": _ONOFF}],
    "nest-auto-attach": [{"key": "state", "label": "중첩자동승격", "choices": _ONOFF}],
    "exit-empty": [{"key": "state", "label": "세션0개시종료", "choices": _ONOFF}],
    "win-mouse-motion": [{"key": "state", "label": "윈도우모션", "choices": _ONOFF}],
    "vt-parser": [{"key": "backend", "label": "VT파서",
                   "choices": [("pyte", "pyte"), ("native", "native")]}],
    "window-size": [{"key": "mode", "label": "공유크기",
                     "choices": [("smallest", "smallest"), ("latest", "latest"),
                                 ("largest", "largest")]}],
    "lang": [{"key": "lang", "label": "언어",
              "choices": [("한국어", "ko"), ("English", "en")]}],
    # auto-resume·prompt-clear·auto-doc-clear·claude-auto-mode·auto-launch
    # 의 옵션 스키마는 claude-code 플러그인이 등록한다(command_options).
}

# ── 통합 설정 레지스트리(:settings 화면) ──────────────────────────────────────
# pytmux 설정이 config 파일·런타임 명령·서버 opts·플러그인 화면에 흩어져 있던 것을
# 한 화면에서 보고 바꾸기 위한 선언형 목록. SettingsScreen 은 이 리스트만 순회하므로
# **설정 추가 = 여기 한 줄**. 런타임 적용/영속은 client.apply_setting 이 backend·cmd 로
# 라우팅한다(흩어진 적용 로직을 재구현하지 않고 기존 명령 경로를 그대로 호출).
#   key      : 정규(하이픈) 옵션명 — 라벨 i18n(setting.<key>)·config 기록 키
#   cat      : 카테고리(setcat.<cat> 로 번역) — 화면 탭 그룹
#   type     : bool | enum | ratio | int | str | link
#   cmd      : 런타임 적용 명령 접두 — apply_setting 이 f"{cmd} {value}" 를 _run_command
#   backend  : config(=config 파일 기록) | server(=서버 opts.json, 명령이 이미 영속)
#              | lang(=.lang, 명령이 영속) | link(=대상 화면 열기, 값 없음)
#   choices  : enum 의 저장값 목록 / lo,hi,step : ratio·int 증감 / link : 열 명령
#   restart  : True 면 "재시작 시 발효" 표시(vt-parser)
SETTINGS = [
    # 표시
    {"key": "inactive-dim", "cat": "표시", "type": "bool",
     "cmd": "inactive-dim", "backend": "config"},
    {"key": "inactive-dim-ratio", "cat": "표시", "type": "ratio",
     "lo": 0.0, "hi": 0.8, "step": 0.02,
     "cmd": "inactive-dim-ratio", "backend": "config"},
    {"key": "tab-bar", "cat": "표시", "type": "enum",
     "choices": ["always", "auto"], "cmd": "set tab-bar", "backend": "config"},
    {"key": "status-position", "cat": "표시", "type": "enum",
     "choices": ["bottom", "top"], "cmd": "set status-position",
     "backend": "config"},
    {"key": "single-border", "cat": "표시", "type": "bool",
     "cmd": "single-border", "backend": "server"},
    {"key": "pane-border-status", "cat": "표시", "type": "bool",
     "cmd": "pane-border-status", "backend": "server"},
    {"key": "remote-title", "cat": "표시", "type": "enum",
     "choices": list(REMOTE_TITLE_CHOICES), "cmd": "set remote-title",
     "backend": "config"},
    {"key": "language", "cat": "표시", "type": "enum",
     "choices": ["ko", "en"], "cmd": "lang", "backend": "lang"},
    # 입력/키
    {"key": "mouse", "cat": "입력", "type": "bool",
     "cmd": "set mouse", "backend": "config"},
    # 값이 셋이라 bool 이 아니다(on·off·shift — 뜻은 keymap.drag_copy_policy 한 곳).
    {"key": "mouse-drag-copy", "cat": "입력", "type": "enum",
     "choices": list(DRAG_COPY_VALUES),
     "cmd": "set mouse-drag-copy", "backend": "config"},
    {"key": "mouse-drag-threshold", "cat": "입력", "type": "int",
     "lo": 1, "hi": 20, "step": 1, "cmd": "set mouse-drag-threshold",
     "backend": "config"},
    {"key": "copy-unwrap", "cat": "입력", "type": "bool",
     "cmd": "set copy-unwrap", "backend": "config"},
    {"key": "set-clipboard", "cat": "입력", "type": "bool",
     "cmd": "set set-clipboard", "backend": "config"},
    {"key": "mode-keys", "cat": "입력", "type": "enum",
     "choices": ["vi", "emacs"], "cmd": "set mode-keys", "backend": "config"},
    {"key": "alt-scroll", "cat": "입력", "type": "bool",
     "cmd": "set alt-scroll", "backend": "config"},
    {"key": "ambiguous-width", "cat": "입력", "type": "enum",
     "choices": ["auto", "narrow", "wide"],
     "cmd": "set ambiguous-width", "backend": "config"},
    {"key": "prefix", "cat": "입력", "type": "str",
     "cmd": "set prefix", "backend": "config"},
    {"key": "strip-box-drawing", "cat": "입력", "type": "bool",
     "cmd": "strip-box-drawing", "backend": "config"},
    # 동작
    {"key": "default-path", "cat": "동작", "type": "str",
     "cmd": "set default-path", "backend": "config"},
    {"key": "claude-command", "cat": "동작", "type": "str",
     "cmd": "set claude-command", "backend": "config"},
    {"key": "set-titles", "cat": "동작", "type": "bool",
     "cmd": "set set-titles", "backend": "config"},
    {"key": "status-interval", "cat": "동작", "type": "int",
     "lo": 1, "hi": 60, "step": 1, "cmd": "set status-interval",
     "backend": "config"},
    {"key": "automatic-rename", "cat": "동작", "type": "bool",
     "cmd": "automatic-rename", "backend": "server"},
    {"key": "monitor-activity", "cat": "동작", "type": "bool",
     "cmd": "monitor-activity", "backend": "server"},
    {"key": "monitor-bell", "cat": "동작", "type": "bool",
     "cmd": "monitor-bell", "backend": "server"},
    {"key": "synchronize-panes", "cat": "동작", "type": "bool",
     "cmd": "synchronize-panes", "backend": "server"},
    {"key": "coalesce-repaints", "cat": "동작", "type": "bool",
     "cmd": "coalesce-repaints", "backend": "server"},
    {"key": "nest-auto-attach", "cat": "동작", "type": "bool",
     "cmd": "nest-auto-attach", "backend": "server"},
    {"key": "exit-empty", "cat": "동작", "type": "bool",
     "cmd": "exit-empty", "backend": "server"},
    {"key": "win-mouse-motion", "cat": "동작", "type": "bool",
     "cmd": "win-mouse-motion", "backend": "server"},
    {"key": "vt-parser", "cat": "동작", "type": "enum",
     "choices": ["pyte", "native"], "cmd": "vt-parser", "backend": "server",
     "restart": True},
    {"key": "window-size", "cat": "동작", "type": "enum",
     "choices": ["smallest", "latest", "largest"], "cmd": "window-size",
     "backend": "server"},
    # 상태줄
    {"key": "status-left", "cat": "상태줄", "type": "str",
     "cmd": "set status-left", "backend": "config"},
    {"key": "status-right", "cat": "상태줄", "type": "str",
     "cmd": "set status-right", "backend": "config"},
    {"key": "status-bg", "cat": "상태줄", "type": "str",
     "cmd": "set status-bg", "backend": "config"},
    {"key": "status-fg", "cat": "상태줄", "type": "str",
     "cmd": "set status-fg", "backend": "config"},
    # (Claude 전용 화면 링크 token-saver/model/claude-rules/token-log 와 'Claude'
    #  카테고리는 claude-code 플러그인이 settings() 훅으로 기여한다 — SettingsScreen 이
    #  코어 SETTINGS/SETTINGS_CATS 와 병합. delete-to-disable: 부재 시 통째로 사라진다.)
    # 고급/플러그인(링크)
    {"key": "plugins", "cat": "고급", "type": "link", "link": "plugins"},
    {"key": "list-keys", "cat": "고급", "type": "link", "link": "list-keys"},
]

# 화면 탭 순서(등장 카테고리). setcat.<name> 로 번역. 플러그인 기여 카테고리(예:
# 'Claude')는 SettingsScreen 이 이 뒤(‘키’ 앞)에 덧붙인다.
SETTINGS_CATS = ["표시", "입력", "동작", "상태줄", "고급", "키"]

# ── 키 바인딩 레퍼런스(설정 팝업 '키' 탭 — 읽기 전용 표시) ──────────────────────
# ESC 모드(_handle_esc_mode)·prefix 모드(_handle_prefix)의 내장 키를 한 곳에 보여 준다.
# **데이터-주도가 아니라 수동 미러**다(핸들러는 if/elif 체인) — client.py 의 두 핸들러를
# 고치면 여기도 같이 갱신할 것. (id, 키 표기, ko, en). 키 표기는 언어중립(번역 안 함).
ESC_MODE_KEYS = [
    ("e_arrows", "↑ ↓ ← →", "패널 이동", "Move pane"),
    ("e_num", "1–9", "번호로 탭 전환", "Switch to tab by number"),
    ("e_tab", "Tab", "탭 스위처(Tab 다음 · Shift+Tab 이전 · Enter 전환)",
     "Tab switcher (Tab next · Shift+Tab prev · Enter switch)"),
    ("e_n", "n", "새 탭", "New tab"),
    ("e_c", "c", "새 탭에서 Claude Code 실행(현재 디렉토리 · set claude-command)",
     "New tab running Claude Code (current dir · set claude-command)"),
    ("e_p", "p", "상하 분할", "Split top/bottom"),
    ("e_P", "P", "탭 고정(핀) 토글", "Toggle tab pin"),
    ("e_e", "e", "활성 패널에 ESC 전달", "Send ESC to active pane"),
    ("e_f", "f", "모든 탭·패널 검색(결과 목록에서 그 자리로)",
     "Search all tabs/panes (jump from the result list)"),
    ("e_jump", "Ctrl+↑ / Ctrl+↓", "이전 / 다음 입력 프롬프트로 점프(Claude 패널·스크롤 모드로)",
     "Jump to prev/next input prompt (Claude pane · enters scroll mode)"),
    ("e_ins", "Insert / Shift+Delete", "작성창(블록 선택 편집→투입)",
     "Compose box (block-select → inject)"),
    ("e_sesc", "Shift+ESC", "활성 패널에 ESC 전달", "Send ESC to active pane"),
    ("e_bt", "`", "리터럴 백틱 전달", "Send literal backtick"),
    ("e_colon", ":", "명령 프롬프트", "Command prompt"),
    ("e_help", "?", "도움말", "Help"),
    ("e_esc", "ESC", "모드 종료(앱에 ESC 전달 없음)", "Exit mode (no ESC sent to app)"),
    ("e_up", "↑ (최상단에서)", "닫기 [x] → 탭바 포커스", "Focus close [x] → tab bar"),
    ("e_tb", "탭바 포커스 후", "←→ 선택·Enter 전환·+/a 새 탭·x/d 닫기·Shift+←→ 이동",
     "←→ select · Enter switch · +/a new · x/d close · Shift+←→ move"),
    ("e_down", "↓ (최하단에서)",
     "상태줄 배지 포커스 — ←→ 배지 순환·Enter 실행(알림 이력·모델·시계 등)",
     "Focus status-bar badges — ←→ cycle · Enter run (notice history, model, clock…)"),
]
PREFIX_KEYS = [
    ("p_pct", "%", "좌우 분할", "Split left/right"),
    ("p_dq", "\"", "상하 분할", "Split top/bottom"),
    ("p_x", "x", "패널 닫기", "Close pane"),
    ("p_z", "z", "줌 토글", "Toggle zoom"),
    ("p_o", "o", "다음 패널", "Next pane"),
    ("p_semi", ";", "직전 패널", "Last pane"),
    ("p_q", "q", "패널 번호 표시", "Show pane numbers"),
    ("p_space", "Space", "레이아웃 순환", "Cycle layout"),
    ("p_co", "Ctrl+o", "패널 회전", "Rotate panes"),
    ("p_swap", "{  }", "패널 swap(이전/다음)", "Swap pane (prev/next)"),
    ("p_bang", "!", "패널을 새 탭으로", "Break pane to new tab"),
    ("p_arrows", "← ↑ ↓ →", "패널 이동", "Move pane"),
    ("p_hjkl", "H J K L", "패널 크기 조절", "Resize pane"),
    ("p_c", "c", "새 탭", "New tab"),
    ("p_comma", ",", "탭 이름변경", "Rename tab"),
    ("p_amp", "&", "탭 닫기", "Close tab"),
    ("p_P", "P", "탭 고정(핀) 토글", "Toggle tab pin"),
    ("p_T", "T", "패널 제목 변경", "Rename pane"),
    ("p_t", "t", "시계 토글", "Toggle clock"),
    ("p_R", "R", "자동재개 토글", "Toggle autoresume"),
    ("p_r", "r", "화면 재그리기(redraw)", "Redraw screen"),
    ("p_colon", ":", "명령 프롬프트", "Command prompt"),
    ("p_np", "n / p", "다음 / 이전 탭", "Next / prev tab"),
    ("p_l", "l", "직전 탭", "Last tab"),
    ("p_w", "w", "트리(개요)", "Tree overview"),
    ("p_dot", ".", "탭 이동", "Move tab"),
    ("p_num", "0–9", "번호로 탭", "Tab by number"),
    ("p_d", "d", "detach", "Detach"),
    ("p_lb", "[", "스크롤 모드", "Scroll mode"),
    ("p_rb", "]", "붙여넣기 버퍼", "Paste buffer"),
    ("p_eq", "=", "버퍼 선택", "Choose buffer"),
    ("p_enter", "Enter", "메뉴", "Menu"),
]

i18n.register({
    "ko": dict([(f"klist.{i}", ko) for i, _k, ko, _en in ESC_MODE_KEYS + PREFIX_KEYS]
               + [("kkey.e_up", "↑ (최상단에서)"), ("kkey.e_tb", "탭바 포커스 후"),
                  ("kkey.e_down", "↓ (최하단에서)")]
               + [("klist.sub_esc", "ESC 모드 (ESC 한 번 후)"),
                  ("klist.sub_prefix", "prefix 후 ({p})"),
                  ("klist.sub_user", "사용자 바인딩 (config)"),
                  ("klist.sub_user_root", "사용자 바인딩 (prefix 없이, bind -n)"),
                  ("klist.none", "(없음)"), ("setcat.키", "키")]),
    "en": dict([(f"klist.{i}", en) for i, _k, _ko, en in ESC_MODE_KEYS + PREFIX_KEYS]
               + [("kkey.e_up", "↑ (at top)"), ("kkey.e_tb", "After tab-bar focus"),
                  ("kkey.e_down", "↓ (at bottom)")]
               + [("klist.sub_esc", "ESC mode (after one ESC)"),
                  ("klist.sub_prefix", "After prefix ({p})"),
                  ("klist.sub_user", "User bindings (config)"),
                  ("klist.sub_user_root", "User bindings (no prefix, bind -n)"),
                  ("klist.none", "(none)"), ("setcat.키", "Keys")]),
})

# §6 ⑤ 옵션 피커 i18n: COMMAND_OPTIONS 의 라벨·선택지 표시문(방향·줌 토글 ⛶·켜기 등)을
# 로케일 전환 가능하게. 키=원문 한국어(gettext 식 — 피커 렌더는 t(원문) 로 단순 조회).
# ko 는 specs 에서 자동 수집(키=값=원문, DRY), en 만 보강. 미등록 문자열은 원문 폴백.
def _collect_opt_strings(*option_dicts):
    out = set()
    for od in option_dicts:
        for specs in od.values():
            for spec in specs:
                out.add(spec["label"])
                for disp, _v in spec["choices"]:
                    out.add(disp)
    return out


i18n.register({
    "ko": {s: s for s in _collect_opt_strings(COMMAND_OPTIONS)},
    "en": {
        # 라벨
        "방향": "Direction", "이동": "Move", "동작": "Action", "프리셋": "Preset",
        "범위": "Scope", "동기화": "Sync", "활동": "Activity", "벨": "Bell",
        "자동이름": "Auto-rename", "단일테두리": "Single border",
        "비활성흐리게": "Inactive dim", "흐리게세기": "Dim strength",
        "테두리제거": "Strip borders",
        "아주 옅게 0.10": "Very light 0.10", "옅게 0.18": "Light 0.18",
        "보통 0.30": "Medium 0.30", "진하게 0.45": "Strong 0.45",
        "리페인트합치기": "Coalesce repaints", "언어": "Language",
        "중첩자동승격": "Nested auto-attach", "세션0개시종료": "Exit on empty",
        "VT파서": "VT parser",
        "윈도우모션": "Windows mouse motion", "공유크기": "Shared size",
        # 선택지
        # VT 파서 백엔드·window-size 모드 이름은 로케일 무관(고유명사) — 양쪽 그대로.
        "pyte": "pyte", "native": "native",
        "smallest": "smallest", "latest": "latest", "largest": "largest",
        "좌우 분할 │ (-h)": "Split L/R │ (-h)", "상하 분할 ─ (-v)": "Split T/B ─ (-v)",
        "◀ 왼쪽": "◀ Left", "▶ 오른쪽": "▶ Right", "▲ 위": "▲ Up", "▼ 아래": "▼ Down",
        "줌 토글 ⛶": "Zoom toggle ⛶",
        "바둑판 tiled": "Tiled", "가로 균등 even-horizontal": "Even horizontal",
        "세로 균등 even-vertical": "Even vertical",
        "메인 세로 main-vertical": "Main vertical",
        "메인 가로 main-horizontal": "Main horizontal",
        "보이는 영역": "Visible area", "스크롤백 전체 -S": "Full scrollback -S",
        "토글": "Toggle", "켜기": "On", "끄기": "Off",
        # 언어 이름은 로케일 무관 양쪽 그대로(자기 언어로 표기).
        "한국어": "한국어", "English": "English",
    },
})
# 인자 없이 바로 실행해도 되는(파괴적이지 않은) 명령 — 선택 즉시 실행한다(#3).
# kill-*/detach/respawn 등 파괴적 명령은 의도 확인을 위해 기존처럼 프롬프트에 채운다.
COMMAND_NOARG = {
    "next-tab", "previous-tab", "last-tab",
    "move-tab-left", "move-tab-right", "move-tab-first", "move-tab-last",
    "next-layout", "rotate-window", "new-tab", "choose-tree",
    # ncd/nc 는 플러그인(pytmuxlib/plugins/ncd)이 무인자 명령으로 등록한다.
    "choose-buffer", "paste-clipboard", "save-layout", "restore-layout",
    "show-options", "show-hooks", "source-file",
    # clock-mode/open-clock/close-clock 은 clock 플러그인, calendar-mode/calendar/
    # cal/open-calendar/open-cal/close-calendar/close-cal 은 calendar 플러그인이
    # 무인자 명령으로 등록한다.
    "list-keys", "send-escape", "version",
    "mouse-help", "mouse",
    "merge-remote-tab", "merge-remote",
    "restart-check",
    # Claude Code 무인자 명령(token-log(별칭 token-usage)·claude-usage·usage·
    # usage-panel·usage-limits·limits·claude-rules·token-saver)은 claude-code 플러그인이 등록.
}
# 자유 텍스트 인자를 받는 명령 — 명령 프롬프트에서 명령을 다 치면 인자 자리에 밑줄
# (____)을 그려 "여기에 인자를 입력" 임을 알린다(사용자 요청). 선택지형(COMMAND_OPTIONS)
# 은 밑줄 대신 방향키로 고르는 토글 UI 를 쓰므로 여기 넣지 않는다. 무인자/파괴적
# 명령(detach-client·kill-* 등)도 제외해 잘못된 밑줄을 막는다.
COMMAND_FREETEXT = {
    "rename-pane", "rename-tab", "send-keys", "pipe-pane", "paste-buffer",
    "layout-save", "layout-load", "layout-load-new", "auto-resume-message",
    "set", "set-hook", "display-message", "display-popup", "run-shell",
    "if-shell", "bind-key", "unbind-key", "claude-token-account",
    "prompt-clear-message", "prompt-clear-queue", "select-tab", "move-tab",
    "swap-tab", "resize-pane", "swap-pane", "capture-pane", "join-pane",
    # 원격 페더레이션: 호스트(NATGAMES\user@host 등)를 직접 친다 → 밑줄 힌트 + 이력.
    "remote-attach", "remote-new-tab", "remote-new-window", "remote-detach",
}

# 인자를 직접 입력하는 명령 중, 이전에 입력한 인자를 기억해 두었다가 다음에 추천·
# 자동완성하는 것들(사용자 요청). command → "이력 버킷" 매핑 — 같은 버킷을 공유하는
# 명령끼리는 인자 이력을 공유한다(예: remote-attach 로 붙인 호스트를 remote-new-tab·
# remote-detach 가 그대로 추천받는다). 이력은 클라이언트가 서버별 상태파일
# (<state>.arghist.json)에 영속한다. 버킷 키는 임의 문자열(파일 키로만 쓰임).
COMMAND_ARGHIST = {
    "remote-attach": "remote-host",
    "remote-new-tab": "remote-host",
    "remote-new-window": "remote-host",
    "remote-detach": "remote-host",
    "layout-save": "layout-name",
    "layout-load": "layout-name",
    "layout-load-new": "layout-name",
    "run-shell": "run-shell",
    "send-keys": "send-keys",
}

# 활성 패널에 적용되는 명령들. 명령 프롬프트에서 이 명령을 작성 중이면 대상(활성)
# 패널 테두리를 밝게 표시해 어느 패널에 적용될지 보이게 한다(요청). 탭/서버 범위
# 명령(rename-tab·new-window 등)은 제외 — 특정 패널 대상이 아니다.
PANE_SCOPED_CMDS = {
    "rename-pane", "resize-pane", "select-pane", "swap-pane", "break-pane",
    "join-pane", "respawn-pane", "kill-pane", "capture-pane", "pipe-pane",
    "clear-history", "send-keys", "send-escape", "paste-buffer",
    "paste-clipboard", "split-window",
    # clock-mode/calendar-mode 등 오버레이 명령은 clock·calendar 플러그인이
    # pane_scoped 로 등록한다(레지스트리 plugins.pane_scoped 로 합쳐짐).
}

# ─────────────────────────────────────────────────────────────────────────────
# §6 ③ i18n: 명령 설명·카테고리·컨텍스트 메뉴 라벨의 ko/en 카탈로그.
# ko 는 위 데이터(COMMANDS/MENU_ITEMS)에서 자동 시드해 중복·드리프트를 없애고(원본=ko),
# en 만 아래에 보강한다. 소비부(clientscreens CommandListScreen·_cmd_desc·컨텍스트 메뉴)는
# t("cmd.<name>"/"cat.<범주>"/"menu.<key>", default=원본) 로 표시 시점에 번역한다.
# (COMMAND_OPTIONS 선택 피커 라벨은 후속 — 주 발견 표면인 ?목록·힌트·메뉴를 우선.)
i18n.register({
    "ko": {
        **{f"cmd.{n}": d for n, d, *_ in COMMANDS},
        **{f"cat.{c}": c for *_rest, c in COMMANDS},
        "cat.전체": "전체", "cat.기타": "기타",
        **{f"menu.{k}": v for k, v in MENU_ITEMS},
        **{f"menu.group.{g}": v for g, v in MENU_GROUP_LABELS.items()},  # §8.1 그룹 라벨
    },
    "en": {
        # 카테고리(?목록 탭)
        "cat.패널": "Pane", "cat.탭": "Tab", "cat.복사/버퍼": "Copy/Buffer",
        "cat.레이아웃": "Layout", "cat.모니터": "Monitor",
        "cat.설정/기타": "Settings/Misc", "cat.전체": "All", "cat.기타": "Misc",
        # 명령 설명(?목록·힌트)
        "cmd.split-window": "Split pane (-h side-by-side │ · -v/default stacked ─)",
        "cmd.kill-pane": "Delete current pane",
        "cmd.resize-pane": "Resize pane (-Z zoom · -L/-R/-U/-D move divider)",
        "cmd.select-pane": "Move to pane (-L/-R/-U/-D) or set title (-T)",
        "cmd.rename-pane": "Rename pane title",
        "cmd.swap-pane": "Swap pane position (-U/-D adjacent · -s/-t by number)",
        "cmd.rotate-window": "Rotate panes in window",
        "cmd.break-pane": "Break pane into a new tab",
        "cmd.join-pane": "Join a pane from another tab (-h)",
        "cmd.respawn-pane": "Restart pane shell",
        "cmd.select-layout": "Layout preset (even-h/v, main-h/v, tiled)",
        "cmd.next-layout": "Next layout preset",
        "cmd.synchronize-panes": "Toggle input sync [on|off]",
        "cmd.new-tab": "New tab (creates one new window, = new-window)",
        "cmd.new-claude-tab": "New tab running Claude Code (current dir · esc c · command from set claude-command)",
        "cmd.kill-tab": "Delete tab (= kill-window)",
        "cmd.next-tab": "Next tab",
        "cmd.previous-tab": "Previous tab",
        "cmd.last-tab": "Last (previous) tab",
        "cmd.select-tab": "Select tab (-t N)",
        "cmd.move-tab": "Move tab (-t N)",
        "cmd.move-tab-left": "Move current tab left",
        "cmd.move-tab-right": "Move current tab right",
        "cmd.move-tab-first": "Move current tab to front",
        "cmd.move-tab-last": "Move current tab to end",
        "cmd.pin-tab": "Pin tab (move to right zone · prevent accidental close)",
        "cmd.unpin-tab": "Unpin tab",
        "cmd.pin-toggle": "Toggle tab pin (alias pin)",
        "cmd.swap-tab": "Swap tabs (-t N)",
        "cmd.rename-tab": "Rename tab",
        "cmd.automatic-rename": "Auto-rename tab [on|off]",
        "cmd.choose-tree": "Tab/pane tree (switch·kill, shows app·local/remote. d/x=kill)",
        "cmd.capture-pane": "Capture pane content to buffer (-S all)",
        "cmd.pipe-pane": "Pipe pane output to external command",
        "cmd.clear-history": "Clear scrollback",
        "cmd.send-keys": "Inject keys to pane (e.g. Enter, C-c)",
        "cmd.send-escape": "Send ESC to active pane (for terminals where Shift+ESC fails — bind to a key with bind-key)",
        "cmd.redraw": "Force full screen redraw (recover broken/stale screen; aliases refresh·refresh-client, prefix r)",
        "cmd.paste-buffer": "Paste from paste buffer (N)",
        "cmd.choose-buffer": "Paste buffer picker",
        "cmd.paste-clipboard": "Paste from OS clipboard",
        "cmd.save-layout": "Save full layout (server-persisted)",
        "cmd.restore-layout": "Restore full layout (server-persisted)",
        "cmd.layout-save": "Save current tab layout (name)",
        "cmd.layout-load": "Load layout → overwrite current tab (name)",
        "cmd.layout-load-new": "Load layout → new tab (name)",
        "cmd.monitor-activity": "Activity monitoring [on|off]",
        "cmd.monitor-bell": "Bell monitoring [on|off]",
        "cmd.settings": "Unified settings screen (all settings in one place)",
        "cmd.inactive-dim": "Toggle dimming of inactive panes [on|off]",
        "cmd.inactive-dim-ratio": "Dimming strength of inactive panes [0~0.8]",
        "cmd.strip-box-drawing":
            "Toggle stripping pane borders (box-drawing) on paste [on|off]",
        "cmd.set": "Set option (prefix/mouse/status-*/mode-keys etc.)",
        "cmd.plugins": "Manage plugins — enable/disable installed plugins "
                       "(alias plugin-manager)",
        "cmd.show-options": "Show current options",
        "cmd.set-hook": "Set event hook (<event> <cmd>)",
        "cmd.show-hooks": "Show hook list",
        "cmd.source-file": "Reload config file",
        "cmd.display-message": "Show message in status bar",
        "cmd.display-popup": "Show command output in a popup",
        "cmd.single-border": "Show border when only one pane on/off (single-border on|off|toggle)",
        "cmd.debug-stats": "Client runtime diagnostics popup — live objects · GC "
                           "generations · screen depth · timer count (-c also runs a "
                           "full collection and pauses that long). The measuring stick "
                           "for pytmux-382 \u300cslows down after days\u300d",
        "cmd.coalesce-repaints": "Coalesce alt-screen full repaints on heavy output on/off — ssh responsiveness (coalesce-repaints on|off|toggle)",
        "cmd.nest-auto-attach": "Auto-promote remote pytmux run to remote-attach instead of rejecting on/off (nest-auto-attach on|off|toggle)",
        "cmd.exit-empty": "Exit server when session count hits 0 on/off — tmux exit-empty equivalent, off=stay resident for remote federation links (exit-empty on|off|toggle)",
        "cmd.win-mouse-motion": "Windows mouse motion (any-motion) passthrough on/off — default on (off kills hover-driven UI · pytmux-423) (win-mouse-motion on|off|toggle)",
        "cmd.vt-parser": "Select VT parser backend pyte|native (takes effect on restart · vt-parser pyte|native)",
        "cmd.window-size": "Shared grid sizing rule for multi-client mirroring smallest|latest|largest — latest=size of last-operated client (window-size smallest|latest|largest)",
        "cmd.version": "Client/server version (p4 CL)·uptime popup (alias about)",
        "cmd.lang": "Switch UI language (lang ko|en) — Korean/English",
        "cmd.run-shell": "Run shell command",
        "cmd.if-shell": "Conditional shell run",
        "cmd.bind-key": "Bind command to key after prefix (bind-key <key> "
                        "<command> · -n = root, no prefix)",
        "cmd.unbind-key": "Unbind key (unbind-key <key> | -n <key> | -a)",
        "cmd.list-keys": "Popup current key bindings",
        "cmd.mouse-help": "Mouse gesture help popup (header-drag swap·tab drag·"
                          "Shift+select etc., alias mouse)",
        "cmd.help": "Show full command list ('?' too, category tabs)",
        "cmd.commands": "Show full command list (alias of help)",
        "cmd.list-commands": "Show full command list (alias of help)",
        "cmd.detach-client": "detach (quit app, keep shells)",
        "cmd.kill-server": "Kill server and all tabs/shells",
        "cmd.remote-attach": "Attach a remote pytmux server's tabs into this one (remote-attach <host>) — federation",
        "cmd.remote-new-tab": "Spawn a new terminal on a remote pytmux and attach it as a new tab (remote-new-tab <host>; attaches first if needed)",
        "cmd.remote-detach": "Detach remote attach (remote-detach [host], omit=all)",
        "cmd.merge-remote-tab": "Merge another remote tab from the same server into "
                                "the current tab as a pane (picker, alias merge-remote) "
                                "— federation",
        "cmd.restart-server": "Work-preserving restart — swap server code keeping shells/PTY (reconnect). Auto dry-run first, re-confirm on FAIL",
        "cmd.restart-all": "Full restart — server session-preserving restart + client relaunch (alias full-restart). Updates both server·client code. Auto dry-run first, re-confirm on FAIL",
        "cmd.restart-check": "Standalone dry-run — check safety (re-exec·serialize·fd·relaunch) without running, popup (alias restart-dry-run)",
        "cmd.reconnect": "Force IPC reconnect — recover stuck degraded (red border) state (server preserved)",
        # 컨텍스트 메뉴(우클릭)
        "menu.split_lr": "Split pane │ (left/right)",
        "menu.split_tb": "Split pane ─ (top/bottom)",
        "menu.zoom": "Toggle pane zoom ⛶",
        "menu.rotate": "Rotate panes ↻",
        "menu.swap_pane": "Swap pane (with next)",
        "menu.break_pane": "Pane → break to new tab",
        "menu.rename_pane": "Rename pane title",
        "menu.select_layout": "Layout preset…",
        "menu.next_layout": "Next layout preset",
        "menu.search": "Search scrollback",
        "menu.search_all": "Search all tabs/panes",
        "menu.kill_pane": "Delete pane ✕",
        "menu.sync": "Toggle input sync",
        "menu.autoresume": "Toggle token-limit auto-resume",
        "menu.prompt_clear": "Toggle per-prompt clear",
        "menu.new_window": "New tab",
        "menu.new_claude_window": "New tab running Claude Code",
        "menu.rename_window": "Rename tab",
        "menu.kill_window": "Delete tab",
        "menu.toggle_pin": "Toggle tab pin (to right zone)",
        "menu.choose_tree": "Tab picker (tree)",
        "menu.next_window": "Next tab",
        "menu.prev_window": "Previous tab",
        "menu.layout_save": "Save layout (current tab)",
        "menu.layout_load_over": "Load layout (overwrite current tab)",
        "menu.layout_load_new": "Load layout (new tab)",
        "menu.join_pane": "Pane → join another tab (join-pane <tab>)",
        "menu.merge_remote_tab": "Remote tab → merge into current tab as a pane "
                                 "(same server)",
        "menu.mouse_help": "Mouse gesture help",
        "menu.command": "Enter command",
        "menu.settings": "⚙ Settings…",
        "menu.detach": "detach (quit app, keep shell)",
        "menu.kill_server": "Kill server (all tabs/shells)",
        # §8.1 그룹(서브메뉴) 라벨
        "menu.group.pane": "Pane",
        "menu.group.layout": "Layout",
        "menu.group.tab": "Tab",
        "menu.group.plugin": "Plugins",
    },
})
