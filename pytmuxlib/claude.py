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
# 주의(ReDoS): 선행 `\(?\s*` 를 두면 거대 공백 화면(와이드·대부분 빈 줄)에서 `\s*` 가
# 매 위치마다 백트래킹해 O(n²)로 폭주한다(200x50 빈화면서 ~420ms 관측). 매칭은 항상
# 숫자에서 시작하게 두어(`\d+` 가 공백 위치에서 즉시 실패) 선형 스캔이 되게 한다.
# "(1M context)" 의 여는 괄호는 search 가 알아서 건너뛰므로 굳이 패턴에 안 넣는다.
_CTX_BADGE_RE = re.compile(r"(\d+\s*[kKmM])\s*context\b", re.I)
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
            # M18-A: 사용%+윈도우를 'ctx N% / 1M' 슬래시 포맷으로(배지 있을 때).
            return f"ctx {m.group(1)}% / {badge}" if badge else f"ctx {m.group(1)}%"
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


# ---- M17(T7): 반복 실패 루프(S8) 감지용 순수 헬퍼 ----
def screen_tail_key(text, n=12):
    """화면 꼬리 n줄(빈 줄 제거·우측 공백 제거)을 합쳐 완료 비교용 안정 키를 만든다.
    busy→idle 완료마다 이 키를 직전과 비교해 동일 출력 반복(루프 의심)을 센다."""
    lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def track_repeat(prev_tail, repeat_n, new_tail):
    """완료 꼬리 비교 → (갱신된 tail, repeat_n). 직전과 같으면 +1, 다르면 0 으로 리셋.
    빈 키(new_tail 없음)는 비교를 건드리지 않는다(상태 유지)."""
    if not new_tail:
        return prev_tail, repeat_n
    if new_tail == prev_tail:
        return prev_tail, repeat_n + 1
    return new_tail, 0


# M14c: 모델 배지 파서. 실 캡처 'Opus 4.8 (1M context)' · 'claude-opus-4-8' 둘 다 잡는다.
# 계열 뒤 버전은 점(4.8) 또는 하이픈(4-8) 표기 모두 허용하고 점으로 정규화한다.
_MODEL_RE = re.compile(r"\b(Opus|Sonnet|Haiku)\b[\s-]*([0-9]+(?:[.\-][0-9]+)*)?", re.I)


def claude_model(text):
    """Claude Code 화면 배지에서 모델 계열(+버전)을 best-effort 추출.

    'Opus 4.8 (1M context)' → 'opus-4.8', 'claude-sonnet-4-6' → 'sonnet-4.6',
    계열만 보이면 'opus'. 못 찾으면 None. 모델 과선택 힌트(T3/S4)·표시용 — 현행
    Claude UI 포맷 의존(§5.7)이라 실 골든 픽스처(badge_1m.txt)로 회귀 고정한다."""
    m = _MODEL_RE.search(text)
    if not m:
        return None
    fam = m.group(1).lower()
    ver = m.group(2)
    return f"{fam}-{ver.replace('-', '.')}" if ver else fam


_WINDOW_RE = re.compile(r"(\d+)\s*([kKmM])")


def ctx_window_tokens(s):
    """배지 문자열('1M'/'200K'/'1M ctx'/'ctx 23% / 1M')에서 컨텍스트 윈도우 토큰 수
    (int)를 뽑는다. 못 찾으면 None. M18-A 의 세션 사용% 근사(분모)용."""
    if not s:
        return None
    m = _WINDOW_RE.search(s)
    if not m:
        return None
    return int(m.group(1)) * (1_000_000 if m.group(2) in "mM" else 1_000)


def claude_context_pct(text: str):
    """Claude Code 화면에서 **컨텍스트 잔량(headroom) %**를 best-effort 추출(int|None).

    `claude_usage` 가 표시용 문자열("ctx 23%")을 내는 것과 달리, 이 함수는 **자동
    정리 트리거(M11)에 쓸 숫자**를 낸다. 같은 `_CTX_PCT_RES` 정규식을 재사용하므로
    표시값과 일관된다.

    **의미 규약**: 반환값은 "남은 여유 %"로 해석한다 — **작을수록 컨텍스트가 꽉 참**.
    Claude Code 의 세 표기를 모두 이 의미로 본다:
      · "context left/remaining N%"  → N = 남은 여유(작을수록 참)
      · "N% until auto-compact"      → N = 압축까지 남은 %(작을수록 곧 압축=참)
      · "auto-compact … N%"          → 위와 동일 계열로 취급
    따라서 호출부(M11)는 **값 < 임계** 일 때 정리를 발화한다. 못 찾으면 None 을 내고,
    호출부는 None 을 0%로 오해하지 말고 발화를 보류해야 한다(§5.5 미동작 편향).

    **주의(휴리스틱)**: "사용 %"(used)와 "잔량 %"(left)가 화면에서 반대 의미인데,
    현행 정규식은 left/remaining/until-compact 계열만 잡는다. Claude 가 "82% used"
    같은 표기를 쓰면 의미가 뒤집히므로 골든 픽스처(tests/fixtures/claude)로 실제
    표기를 회귀 고정한다(docs/TOKEN_SAVING_SCENARIO.md §7)."""
    for rx in _CTX_PCT_RES:
        m = rx.search(text)
        if m:
            try:
                v = int(m.group(1))
            except (TypeError, ValueError):
                return None
            return v if 0 <= v <= 100 else None
    return None


# ---- 계정 식별(토큰 로깅 계정별 구분, docs/HANDOFF.md §10 #7) ----
# Claude Code 의 /status·로그인 배너·푸터에 보이는 이메일/플랜으로 계정을 추정한다.
# 화면 텍스트만 보므로 휴리스틱이고, 못 찾으면 None(서버가 "unknown" 으로 적는다).
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
# RFC 2606/6761 예약·플레이스홀더 도메인 — 실제 로그인 계정일 수 없다. Claude 가
# 처리 중인 transcript/문서 본문의 예시 이메일(user@example.com 등)을 계정으로
# 오검출하던 사용자 보고를 차단한다(상태줄에 @us…@example.com 으로 튐 —
# docs/TOKEN_SAVING_SCENARIO.md §5.7 휴리스틱 포맷 오탐의 실제 사례).
_RESERVED_EMAIL_DOMAINS = frozenset({
    "example.com", "example.net", "example.org", "example.edu",
})
_RESERVED_EMAIL_TLDS = frozenset({"example", "invalid", "localhost", "test"})
# 계정 맥락 키워드 — 이메일이 이 단어와 같은 줄에 있으면 본문 예시가 아니라 로그인
# 계정으로 본다(/status·로그인 배너·푸터). 맥락 줄이 없으면 첫 비예약 이메일로 폴백.
_ACCT_CONTEXT_RE = re.compile(
    r"\b(?:account|login|logged\s*in|signed\s*in|e-?mail|auth)\b", re.I)


def _is_reserved_email_domain(domain: str) -> bool:
    """예약/플레이스홀더 도메인(절대 실계정 아님)이면 True."""
    d = domain.lower()
    return d in _RESERVED_EMAIL_DOMAINS or d.rsplit(".", 1)[-1] in _RESERVED_EMAIL_TLDS


# 구분자는 콜론(`org: Foo`) 또는 **공백으로 둘러싼** 대시(`account - Foo`)만 인정한다.
# 앞에 단어문자/슬래시/대시가 붙은 경우는 제외해 `/team-onboarding …` 같은 슬래시
# 명령·하이픈 단어를 계정명으로 오검출하지 않는다(상태줄 @계정에 튐, 사용자 보고).
_ORG_RE = re.compile(r"(?<![\w/\-])(?:organization|org|team|workspace|account)\s*"
                     r"(?::|\s[-–]\s)\s*([A-Za-z0-9 ._\-]{2,40})", re.I)
_PLAN_RE = re.compile(r"\b(Pro|Max|Team|Enterprise|Free)\b\s*"
                      r"(?:plan|subscription|tier)", re.I)


# Claude Code 세션 종료 시 뜨는 피드백 프롬프트("How is Claude doing this session?
# 1:Bad 2:Fine 3:Good 0:Dismiss"). 자동으로 0(Dismiss) 을 눌러 치우기 위한 감지(#26).
_FEEDBACK_RE = re.compile(r"How is Claude doing this session", re.I)


def claude_feedback_prompt(text: str) -> bool:
    """화면에 Claude 세션 피드백 프롬프트가 떠 있으면 True(자동 Dismiss 대상)."""
    return bool(_FEEDBACK_RE.search(text))


def claude_account(text: str):
    """Claude Code 화면 텍스트에서 계정 식별 문자열을 best-effort 추출.

    개인/팀(조직) 계정을 토큰 로그에서 구분하기 위함(요금·한도 별개). 우선순위:
    ① 이메일(별칭화 — 원문 미노출) → ② 조직/팀명 → ③ 플랜명. 못 찾으면 None.

    **민감정보 보호**: 이메일은 원문 대신 `로컬앞2글자…@도메인` 별칭으로 돌려준다
    (개인 vs 조직 도메인 구분은 되되 전체 주소는 로그에 남기지 않음).

    **오탐 방지**: 화면 전체에서 첫 이메일을 무조건 잡지 않는다 — 예약 도메인
    (example.* 등)은 건너뛰고, 계정 맥락 줄(account/login/email…)의 이메일을 본문
    예시 이메일보다 우선한다. 맥락 줄이 없으면 첫 비예약 이메일로 폴백."""
    fallback = None
    for line in text.splitlines():
        anchored = _ACCT_CONTEXT_RE.search(line) is not None
        for m in _EMAIL_RE.finditer(line):
            local, domain = m.group(1), m.group(2)
            if _is_reserved_email_domain(domain):
                continue
            alias = (local[:2] + "…") if len(local) > 2 else local
            acct = f"{alias}@{domain}"
            if anchored:                # 계정 맥락 줄의 이메일 — 최우선 채택
                return acct
            if fallback is None:        # 맥락 없는 첫 비예약 이메일 — 폴백 후보
                fallback = acct
    if fallback is not None:
        return fallback
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
    # 명시적 권한모드 글리프/문구부터 판정한다(글리프·모드명은 footer 줄 앞쪽이라
    # 좁은 폭(모바일)에서 뒤가 잘려도 살아남는다).
    if "bypass permissions" in low:
        return "bypass"
    if ("⏵⏵" in text or "auto-accept" in low or "auto mode" in low
            or "accept edits on" in low):
        return "auto"
    if "plan mode" in low or "⏸" in text:
        return "plan"
    # 글리프가 없으면 default(일반) 모드 후보. 실제 Claude default 모드는 권한 글리프
    # 없이 입력 힌트("? for shortcuts" / "/help for help")만 그린다(claude_state 의
    # idle 신호와 동일). 이전엔 default 신호로 "shift+tab to cycle" 만 봤는데 — 실제
    # default footer 엔 그 문구가 없어서 — None 을 반환했고, 그 결과 default→auto
    # 자동전환(_maybe_auto_mode)이 시작조차 못 했다(폭 무관한 근본 버그였지만, 좁은
    # 폭 모바일에서 auto/plan 글리프 footer 마저 안 보일 때 특히 두드러졌다). idle
    # 입력 힌트가 보이면 default 로 판정해 자동전환 폐루프가 시작되게 한다.
    if ("shift+tab to" in low or "mode on (shift" in low
            or "? for shortcuts" in low or "for shortcuts" in low
            or "/help for help" in low):
        return "default"
    return None


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


# ---- M16: PTY 밖 에스컬레이션 훅 — status 전이 → (event, env) (§8) ----
# 클라(PytmuxApp)가 status 마다 호출한다. 이미 송신되는 신호(budget_level·
# claude_pending·활성패널 limit)의 **상승 에지에서만** 1회 발화하도록 (event, env)
# 목록을 계산하고 prev(가변 dict)를 갱신한다. PytmuxApp 은 import 불가(함수 내부
# 정의)라 여기 모듈 함수로 빼서 단위 테스트가 가능하게 한다(alert-bell 전이 패턴과 동형).
def saver_hook_events(prev: dict, msg: dict) -> list:
    """status 전이에서 발화할 [(event, env_dict), …] 을 계산하고 prev 를 갱신한다.

    prev 키: budget_level(int)·pending_kind(str|None)·limit(bool). 상승 에지(미만→이상,
    None→값, False→True)에서만 이벤트를 낸다 — 같은 화면을 여러 프레임 봐도 중복
    발화하지 않는다(§5.6). env 는 사용자 훅 셸 명령이 참조할 PYTMUX_* 컨텍스트.
    계정은 별칭(claude_account)만 — 원문 이메일은 싣지 않는다."""
    events = []
    acct = msg.get("claude_account") or ""
    blvl = int(msg.get("budget_level") or 0)
    pblvl = int(prev.get("budget_level", 0))
    if blvl >= 80 > pblvl:
        events.append(("claude-budget-warn", {
            "PYTMUX_HOOK_EVENT": "claude-budget-warn",
            "PYTMUX_BUDGET_LEVEL": blvl, "PYTMUX_ACCOUNT": acct}))
    if blvl >= 100 > pblvl:
        events.append(("claude-budget-over", {
            "PYTMUX_HOOK_EVENT": "claude-budget-over",
            "PYTMUX_BUDGET_LEVEL": blvl, "PYTMUX_ACCOUNT": acct}))
    prev["budget_level"] = blvl

    pend = msg.get("claude_pending")
    kind = pend.get("kind") if isinstance(pend, dict) else None
    if kind and not prev.get("pending_kind"):
        events.append(("claude-auto-armed", {
            "PYTMUX_HOOK_EVENT": "claude-auto-armed",
            "PYTMUX_PENDING_KIND": kind,
            "PYTMUX_PENDING_ETA": (pend.get("eta") if isinstance(pend, dict)
                                   else "") or "",
            "PYTMUX_ACCOUNT": acct}))
    prev["pending_kind"] = kind

    apid = msg.get("active_pane")
    astate = None
    for e in msg.get("panes_claude", []):
        if e.get("id") == apid:
            astate = e.get("claude")
            break
    is_limit = (astate == "limit")
    if is_limit and not prev.get("limit"):
        events.append(("claude-limit", {
            "PYTMUX_HOOK_EVENT": "claude-limit", "PYTMUX_ACCOUNT": acct}))
    prev["limit"] = is_limit
    return events
