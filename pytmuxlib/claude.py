"""Claude Code 연동 — 화면 텍스트 휴리스틱(상태/사용량/리밋 파서).

이 모듈은 Claude Code 특화 로직을 코어(멀티플렉서)와 분리해 모은다(docs/HANDOFF.md
§11). 여기 있는 함수들은 **순수 함수**(패널 화면 텍스트 → 상태/사용량/지연)라
서버/클라이언트 어디서도 부담 없이 부를 수 있다. Claude Code 버전이 표시 문구를
바꾸면 가장 먼저 손볼 곳이다(정규식 보강). protocol.py 는 하위호환을 위해 이
세 함수를 re-export 한다.
"""
from __future__ import annotations

import datetime as _dt
import re


# ---- 토큰 리밋 자동 재개: 리밋 해제 시각 파서 ----
_RESET_RE12 = re.compile(r'(\d{1,2})(?::(\d{2}))?\s*([ap]m)', re.I)
_RESET_RE24 = re.compile(r'\b([01]?\d|2[0-3]):([0-5]\d)\b')

# 처리중(busy) 스피너: 현행 Claude Code 는 footer 에 "esc to interrupt" 대신
# "<글리프> <동명사>… (12s · ↑ 1.9k tokens · still thinking)" 형식의 애니메이션
# 줄을 그린다. 동명사(Crunching/Flowing/Baking/…)·글리프(✽✢✳✶✷✻ + 폰트에 따라
# `*`/`·`로 렌더되기도 함)는 매 프레임 바뀌므로 안정적인 시그널만 잡는다.
# 시간 숫자+s 를 요구해 "… +38 lines (ctrl+o)" 같은 도구 출력 오탐을 피한다.
# idle footer("shift+tab to cycle")가 busy 중에도 같이 보이므로 — busy 시그널은
# 가급적 여러 형태로 잡아두고 claude_state 에서 busy 를 먼저 판정한다.
_BUSY_SPINNER_RE = re.compile(
    r"…\s*\((?:\d+\s*m\s*)?\d+\s*s"      # "… (20s" / "… (2m 17s"
    r"|[✽✢✳✶✷✻*·]\s+\w+…"                # 스피너 글리프 + "동명사…"(시간 표시 전)
    r"|[↑↓]\s*[\d.,]+\s*[kKmM]?\s*tokens"  # "↑ 419 tokens" / "↓ 1.9k tokens"
    r"|still\s+thinking"                  # 명시적 진행 표기
)


def claude_state(text: str):
    """패널의 최근 화면 텍스트로 Claude Code CLI 상태를 추정한다.

    반환: "limit"(사용량 리밋으로 멈춤) / "busy"(프롬프트 처리중) /
    "idle"(입력 대기) / None(Claude Code 아님). 화면 특징 문자열로 휴리스틱 판별.

    현행 Claude Code(2026 기준)는 busy 시 작업 스피너("✽ Crunching… (38s …)"),
    idle 시 권한 모드 footer("⏵⏵ auto mode on (shift+tab to cycle)")를 그린다.
    모드 footer 는 busy 중에도 같이 보이므로 반드시 busy 를 먼저 판정한다.
    """
    low = text.lower()
    # 사용량 리밋 안내(자동재개 파서와 동일 신호)
    if "limit" in low and any(k in low for k in
                              ("reset", "again", "resume", "retry", "upgrade")):
        return "limit"
    # 처리중: 현행 작업 스피너 또는 레거시 "esc to interrupt"
    if (_BUSY_SPINNER_RE.search(text)
            or "esc to interrupt" in low or "interrupt)" in low):
        return "busy"
    # 입력 대기: 권한 모드 footer(shift+tab 순환) 또는 도움말/단축키 신호
    if ("shift+tab to" in low or "mode on (shift" in low
            or "? for shortcuts" in low or "for shortcuts" in low
            or "/help for help" in low or "bypass permissions" in low):
        return "idle"
    return None


_CTX_PCT_RES = [
    re.compile(r"context\s+(?:low|left|remaining)[^0-9%]*?(\d{1,3})\s*%", re.I),
    re.compile(r"(\d{1,3})\s*%\s*(?:context|remaining|"
               r"until\s+auto[- ]?compact)", re.I),
    re.compile(r"auto[- ]?compact[^0-9%]*?(\d{1,3})\s*%", re.I),
]
# 확장 컨텍스트 모델 배지: "claude-opus-4-8 (1M context)" / "1M context window" 등.
# 컨텍스트 잔량%·토큰과 별개로 "이 패널은 1M(또는 200k) 컨텍스트 모델"임을 알린다.
_CTX_BADGE_RE = re.compile(r"\(?\s*(\d+\s*[kKmM])\s*context\b", re.I)
_TOK_RE = re.compile(r"([\d][\d.,]*\s?[kKmM]?)\s*tokens?\b", re.I)


def claude_usage(text: str):
    """Claude Code 화면 텍스트에서 컨텍스트 사용률/토큰 수를 best-effort 추출.

    Claude Code 가 항상 고정 위치에 토큰/컨텍스트를 출력하진 않으므로 휴리스틱이다.
    'ctx NN%' 또는 'NNk tok' 같은 짧은 문자열을 반환(못 찾으면 None).

    우선순위: ① 컨텍스트 잔량%(가장 의미있음) → ② 화살표 없는 토큰 누계.
    확장 컨텍스트 모델 배지(예: "1M")가 보이면 뒤에 덧붙인다.

    **스트리밍 델타 제외**: busy footer 의 "↑/↓ N tokens" 는 한 프레임 분의 송수신
    델타라 누적 컨텍스트가 아니므로 사용량으로 보고하지 않는다(이건 busy 신호로만
    쓰임 — _BUSY_SPINNER_RE). 화살표가 바로 앞에 붙은 토큰 언급은 건너뛴다.
    """
    badge = None
    mb = _CTX_BADGE_RE.search(text)
    if mb:
        badge = mb.group(1).replace(" ", "").upper()   # "1m" → "1M"

    def _join(s):
        return f"{s} {badge}" if badge else s

    for rx in _CTX_PCT_RES:
        m = rx.search(text)
        if m:
            return _join(f"ctx {m.group(1)}%")
    for m in _TOK_RE.finditer(text):
        # 화살표 델타(↑/↓ … tokens)와 겹치면 건너뜀
        prefix = text[max(0, m.start() - 4):m.start()]
        if "↑" in prefix or "↓" in prefix:
            continue
        return _join(f"{m.group(1).replace(' ', '')} tok")
    # 토큰/잔량은 못 찾았지만 확장 컨텍스트 모델 배지만 보일 때
    if badge:
        return f"{badge} ctx"
    return None


# ---- 계정 식별(토큰 로깅 계정별 구분, docs/HANDOFF.md §10 #7) ----
# Claude Code 의 /status·로그인 배너·푸터에 보이는 이메일/플랜으로 계정을 추정한다.
# 화면 텍스트만 보므로 휴리스틱이고, 못 찾으면 None(서버가 "unknown" 으로 적는다).
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
_ORG_RE = re.compile(r"\b(?:organization|org|team|workspace|account)\s*[:\-]\s*"
                     r"([A-Za-z0-9 ._\-]{2,40})", re.I)
_PLAN_RE = re.compile(r"\b(Pro|Max|Team|Enterprise|Free)\b\s*"
                      r"(?:plan|subscription|tier)", re.I)


def claude_account(text: str):
    """Claude Code 화면 텍스트에서 계정 식별 문자열을 best-effort 추출.

    개인/팀(조직) 계정을 토큰 로그에서 구분하기 위함(요금·한도 별개). 우선순위:
    ① 이메일(별칭화 — 원문 미노출) → ② 조직/팀명 → ③ 플랜명. 못 찾으면 None.

    **민감정보 보호**: 이메일은 원문 대신 `로컬앞2글자…@도메인` 별칭으로 돌려준다
    (개인 vs 조직 도메인 구분은 되되 전체 주소는 로그에 남기지 않음)."""
    m = _EMAIL_RE.search(text)
    if m:
        local, domain = m.group(1), m.group(2)
        alias = (local[:2] + "…") if len(local) > 2 else local
        return f"{alias}@{domain}"
    m = _ORG_RE.search(text)
    if m:
        return m.group(1).strip()
    m = _PLAN_RE.search(text)
    if m:
        return m.group(1).lower()
    return None


# ---- 화면에서 사용자 프롬프트 추출(데스크탑 앱 원격제어 등 입력 경로 미경유, §10) ----
# Claude Code transcript 는 사용자 턴을 "> 내용" 으로 그린다(바닥 입력박스도 "> ").
# 제출된 프롬프트(transcript)와 라이브 입력박스를 구분하려고 하단 몇 줄은 건너뛴다.
_PROMPT_LINE_RE = re.compile(r"^\s*(?:[│|]\s*)?>\s+(\S.*?)\s*$")
_PROMPT_TAIL_SKIP = 3   # 하단 N줄(입력박스+footer) 제외


def claude_prompt(text: str):
    """Claude Code 화면 transcript 에서 최신 사용자 프롬프트 줄을 best-effort 추출.

    사용자 턴은 보통 "> 내용"(테두리 안이면 "│ > 내용")으로 렌더된다. 라이브 입력
    박스의 "> 타이핑중" 을 제출된 프롬프트로 오인하지 않도록 뒤쪽 빈 줄을 떼고 하단
    _PROMPT_TAIL_SKIP 줄은 건너뛴 뒤, 그 위에서 가장 최근 매치를 고른다. 못 찾으면
    None. **Claude UI 포맷 의존이라 best-effort** — 오검출 시 헤더가 잠깐 어긋날 뿐
    이고, 서버는 입력 경로로 안 잡힌(히스토리에 없는) 경우에만 이 값을 쓴다."""
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    cutoff = len(lines) - _PROMPT_TAIL_SKIP
    found = None
    for i, line in enumerate(lines):
        if i >= cutoff:
            break
        m = _PROMPT_LINE_RE.match(line)
        if m:
            cand = m.group(1).strip()
            if len(cand) >= 2:   # 한 글자 등 잡음 제외
                found = cand
    return found


def claude_perm_mode(text: str):
    """Claude Code idle 권한모드 footer 에서 현재 권한모드를 best-effort 추정.

    반환:
      "auto"    — 자동 수락(⏵⏵ / "auto-accept edits on" / "auto mode on")
      "bypass"  — 권한 우회("bypass permissions"). 명시적·위험 모드라 건드리지 않음.
      "plan"    — 플랜 모드("plan mode on")
      "default" — 일반 모드(footer 는 보이나 위 어느 것도 아님)
      None      — 권한모드 footer 신호가 안 보임(판정 불가)

    Claude Code 버전이 footer 문구를 바꾸면 가장 먼저 손볼 곳이다(claude_state 와
    같은 footer 를 본다)."""
    low = text.lower()
    footer = ("shift+tab to" in low or "mode on (shift" in low)
    has_signal = (footer or "⏵⏵" in text or "auto-accept" in low
                  or "auto mode" in low or "plan mode" in low
                  or "bypass permissions" in low)
    if not has_signal:
        return None
    if "bypass permissions" in low:
        return "bypass"
    if "⏵⏵" in text or "auto-accept" in low or "auto mode" in low:
        return "auto"
    if "plan mode" in low:
        return "plan"
    return "default"


def parse_reset_delay(text: str, now: "_dt.datetime | None" = None):
    """Claude Code 등의 사용량 리밋 안내 문구에서 해제 시각을 찾아
    지금부터 그때까지의 지연(초)을 반환. 못 찾으면 None."""
    low = text.lower()
    if "limit" not in low:
        return None
    if not any(k in low for k in ("reset", "again", "resume", "retry")):
        return None
    now = now or _dt.datetime.now()
    m = _RESET_RE12.search(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ap = m.group(3).lower()
        if ap == "pm" and hour != 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
    else:
        m = _RESET_RE24.search(text)
        if not m:
            return None
        hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += _dt.timedelta(days=1)
    delay = (target - now).total_seconds()
    return delay if 0 < delay <= 26 * 3600 else None
