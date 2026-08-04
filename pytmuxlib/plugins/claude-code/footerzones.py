"""Claude 패널 **안**의 클릭존 규칙 — 한 벌(pytmux-2 · pytmux-23).

# 왜 이 파일인가

이 규칙은 오래 정본 클라 안에만 있었다(`clientrender._scan_all_footer_zones`). 그래서
GUI 에는 권한모드 footer 를 눌러도 아무 일이 없었다 — 규칙이 파이썬/Textual 쪽에 살아
소켓을 못 건너기 때문이다(루트 CLAUDE.md 「플러그인」의 그 문제의 실물).

고치는 길은 pytmux-20 이 상태줄 표식에서 낸 것과 같다: **규칙은 한 벌로 두고 서버가
자료로 낸다.** 그래서 이 모듈은 **UI 무의존**이다 — `textual` 도 `rich` 도 안 읽고
(`clientutil` 대신 `cellwidth` 를 직접 읽는 이유다) 서버 프로세스가 그대로 import 한다.

- 정본 클라 — 매 `_composite` 마다 `clientrender` 가 부른다(종전 그대로 · 지연 0).
- GUI — 서버 `plugin_triggers` 가 같은 함수를 불러 `zones` 로 실어 보낸다.

여기서 두 벌이 되면 그 순간 두 클라의 누르는 자리가 갈린다. **규칙을 고칠 일이 있으면
이 파일만 고친다.**

# 못 찾으면 존을 안 만든다

문구는 전부 **Claude 가 그리는 글**이라 버전이 바뀌면 사라지거나 달라진다. 그때
"대충 이 줄 어딘가" 로 넓히면 엉뚱한 자리를 누르게 되고, 그건 아무 일도 안 나는
것보다 나쁘다 — 사용자가 트랜스크립트를 누르려다 팝업을 연다. 그래서 **문구를 못
찾으면 조용히 존을 안 만든다.**
"""
from __future__ import annotations

import re

from pytmuxlib.cellwidth import char_cells

# 권한모드 footer 의 **모드 표시 부분**("⏵⏵ auto mode on" 등)만 클릭존으로 잡는
# 패턴 — 예전엔 footer 줄 전체가 존이라 힌트 문구("(shift+tab to cycle)"·"← for
# agents" 등)를 눌러도 팝업이 떠 오클릭이 잦았다(사용자 07-15). 글리프(⏵⏵/⏸)가
# 문구 앞에 붙어 있으면 존에 포함한다. 모드 문구 집합은 claude.py:claude_perm_mode
# 와 동기("is on" 접미 변형 포함 — 2026-06-25).
_PERM_LABEL_RE = re.compile(
    r"(?:(?:⏵⏵|⏸)\s*)?"
    r"(?:auto-accept\s+edits|accept\s+edits|auto\s+mode|plan\s+mode|"
    r"bypass\s+permissions)"
    r"(?:\s+(?:is\s+)?on\b)?", re.I)

# 컨텍스트 footer 의 **토큰 수치**만 덮는 패턴(pytmux-23) — 제보 스크린샷의
# `new task? /clear to save 386.8k tokens` 에서 `386.8k tokens` 부분이다.
#
# ⚠ 이 줄은 다른 둘과 달리 **트랜스크립트 본문과 헷갈릴 수 있다**: 대화 중에
# "1000 tokens" 같은 글이 얼마든 지나간다. 그래서 수치 패턴만으로는 안 잡고
# **같은 줄에 `/clear` 가 있을 때만** 존으로 만든다(footer 힌트의 서명). 이 짝이
# 깨지는 Claude 판이 오면 존이 사라질 뿐 오탐은 안 난다 — 그 방향이 맞다.
_TOKENS_RE = re.compile(r"\d[\d.,]*\s*[kKmM]?\s+tokens\b", re.I)
_TOKENS_SIG = "/clear"


def _span(text, start, end, px, pw):
    """줄 안 `[start,end)` 글자 범위 → 창 절대 x 범위 `(x0, x1)`(와이드 인지).

    문자열 인덱스와 칸은 다르다 — 한글·이모지가 앞에 있으면 어긋난다. 세 존이
    전부 같은 셈을 하므로 한 자리에 둔다."""
    x0 = px + sum(char_cells(c) for c in text[:start])
    x1 = min(px + pw, x0 + sum(char_cells(c) for c in text[start:end]))
    return x0, x1


def scan_pane(lines, px, py, pw, ph) -> dict:
    """패널 하나의 화면 줄에서 footer 클릭존을 찾는다.

    `lines` 는 위에서부터의 줄 문자열이고 `px,py,pw,ph` 는 그 패널의 **내용 영역**
    (테두리 안 · 창 절대 좌표)이다. 정본은 자기 `pane_content` 행을, 서버는
    `screen_text(pane.screen)` 를 넘긴다 — 둘 다 와이드 문자를 한 번만 담은 같은
    모양이라(연속 셀은 빈 문자열) 같은 셈이 선다.

    돌려주는 것은 **찾은 것만** 담은 `{종류: (x0, x1, y)}` 다. 종류는
    `perm`·`remote`·`interrupt`·`tokens`.
    """
    found = {}
    for ry, text in enumerate(lines):
        if ry >= ph:
            break
        gy = py + ry
        low = text.lower()
        stripped = text.strip()
        if not stripped:
            continue
        # 줄의 실제 글자 범위(앞뒤 공백 제외)를 클릭존 x 범위로 — 와이드 인지.
        lead = len(text) - len(text.lstrip())
        x0, x1 = _span(text, lead, len(text.rstrip()), px, pw)
        # 권한모드 footer(claude.py:claude_perm_mode 와 같은 신호). 존은 줄
        # 전체가 아니라 **모드 표시 문구만** 덮는다("⏵⏵ auto mode on" 까지 —
        # 뒤의 "(shift+tab to cycle)" 힌트는 클릭존 밖, 사용자 07-15). 좁은 폭
        # 잘림 등으로 모드 문구가 안 보이면 예전대로 줄 전체 — 팝업 진입로는
        # 남긴다.
        if ("shift+tab to" in low or "mode on (shift" in low
                or "⏵⏵" in text or "auto-accept" in low):
            m = _PERM_LABEL_RE.search(text)
            if m:
                found["perm"] = (*_span(text, m.start(), m.end(), px, pw), gy)
            else:
                found["perm"] = (x0, x1, gy)
        if "remote control" in low:
            found["remote"] = (x0, x1, gy)
        # busy footer 의 'esc to interrupt' 만 덮는 좁은 클릭존 — perm 존(모드
        # 표시 문구)과 같은 줄의 다른 구간이라 겹치지 않지만, 클릭 핸들러는
        # 여전히 interrupt 를 perm 보다 먼저 검사한다(폭 잘림 fallback 으로 perm
        # 이 줄 전체일 땐 진부분집합이 되므로 순서가 유효하다).
        # 문구 시작('esc')부터 끝('interrupt')까지를 와이드 인지해 x 범위로 잡는다.
        imark = low.find("esc to interrupt")
        if imark >= 0:
            iend = imark + len("esc to interrupt")
            found["interrupt"] = (*_span(text, imark, iend, px, pw), gy)
        # 컨텍스트 footer 의 토큰 수치(pytmux-23) — `/clear` 서명이 같은 줄에
        # 있을 때만. 위 셋과 달리 본문과 헷갈릴 수 있는 문구라 짝을 요구한다.
        if _TOKENS_SIG in low:
            m = _TOKENS_RE.search(text)
            if m:
                found["tokens"] = (*_span(text, m.start(), m.end(), px, pw), gy)
    return found


#: 자리가 **겹칠 때 먼저 집는 순서**. 규칙이라 여기 산다 — 두 소비자가 각자 순서를
#: 적으면 같은 자리를 눌러도 클라마다 다른 일이 난다.
#:
#: `interrupt` 가 `perm` 보다 앞인 이유: 둘은 같은 줄의 다른 구간이라 보통 안 겹치지만,
#: 폭이 잘려 모드 문구를 못 찾으면 `perm` 이 **줄 전체**로 넓어진다(위 fallback). 그때
#: `interrupt` 는 그 줄의 진부분집합이라, 뒤에 두면 영영 못 눌린다.
PRIORITY = ("interrupt", "perm", "remote", "tokens")

#: 존 종류 → 그 존이 여는 Tier C 화면 이름(클라가 `plugin_open` 으로 되돌려 보낸다).
#:
#: `remote` 도 여기 있다 — 정본에서 그 자리는 곧바로 토글이 아니라 **판을 연다**
#: (`_open_remote_control` = 원격 제어가 무엇인지 적은 InfoScreen + `[r]` 토글). 판을
#: 건너뛰고 바로 `/rc` 를 치면 그건 정본과 다른 제품이다.
OPENS = {
    "perm": "claude-perm-mode",
    "remote": "claude-remote-control",
    "tokens": "claude-token-log",
}

#: 존 종류 → 누르면 **그 패널에 치는 글자**(화면이 아니라 동작인 자리).
#:
#: `interrupt` 만 여기 있다. 정본도 이 자리는 팝업이 아니라 `send_input_pane(pid, ESC)`
#: 한 줄이고(`_interrupt_pane`), 그러니 이 자리의 뜻은 *"그 패널에 이것을 친다"* 가
#: 전부다 — 자리와 함께 **칠 것까지** 실어 보내면 두 클라가 같은 것을 친다.
#:
#: ⚠ 클라는 여전히 뜻을 모른다(설계 §4.4). `\x1b` 가 "지금 하는 일을 멈춰라"라는 것은
#: Claude Code 가 정하는 일이고, 클라는 서버가 정한 바이트를 그 패널로 넘길 뿐이다 —
#: 사람이 그 자리를 눌러 ESC 를 친 것과 같은 경로다.
SENDS = {
    "interrupt": "\x1b",
}
