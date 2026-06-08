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
            # M18-A: 사용%+윈도우를 'ctx:N%/1M' 콤팩트 포맷(공백 없이, 사용자 요청).
            return f"ctx:{m.group(1)}%/{badge}" if badge else f"ctx:{m.group(1)}%"
    for m in _TOK_RE.finditer(text):
        # 화살표 델타(↑/↓ … tokens)와 겹치면 건너뜀
        prefix = text[max(0, m.start() - 4):m.start()]
        if "↑" in prefix or "↓" in prefix:
            continue
        g = m.group(1).replace(" ", "")
        # 단위(k/M/B) 없는 작은 수("1 token" 등)는 컨텍스트 지표가 아니라 대화/도구
        # 출력에 섞인 노이즈다 — 상태줄에 "1 tok" 같은 오표시가 떴다(요청). 컨텍스트
        # 토큰 수는 항상 천 단위 이상(k/M)이므로 단위가 붙은 값만 채택한다.
        if g[-1:].lower() not in ("k", "m", "b"):
            continue
        return _join(f"{g} tok")
    # 토큰/잔량은 못 찾았지만 확장 컨텍스트 모델 배지만 보일 때
    if badge:
        return f"{badge} ctx"
    return None


# ---- M19: /usage 패널 파서(그림자 질의 결과 스크랩) ----
# 두 레이아웃 모두 처리한다(실측):
#  · 넓은 raw: 'Current session' / '█ 2% used' / 'Resets 2pm (Asia/Seoul)' (줄 분리)
#  · 좁은 렌더: 'Current session · Resets 5am (Asia/Seoul)' + 'N% used' (일부 합쳐짐)
# 'Current <항목>' 헤더로 블록을 시작하고, 블록 안에서 'N% used'·'Resets …' 를 줍는다.
_USAGE_HDR_RE = re.compile(r"Current\s+(session|week\s*\([^)]*\))", re.I)
_USAGE_PCT_RE = re.compile(r"(\d{1,3})%\s*used", re.I)
_USAGE_RESET_RE = re.compile(r"Resets\s+(.+?)\s*$", re.I)


def parse_usage(text):
    """Claude `/usage` TUI 패널 텍스트에서 세션(5시간)·주간 한도 %·리셋 표기를 뽑는다.

    반환 예: {"session": {"pct":2,"reset":"2pm (Asia/Seoul)"},
              "week_all": {"pct":14,"reset":"Jun 13 at 3am (Asia/Seoul)"},
              "week_sonnet": {"pct":0,"reset":"..."}}. 패널 없으면 None.
    세션 % 가 직접 나오므로 §9.3 의 5h 분모 추정이 불필요해진다(M19)."""
    blocks, cur = [], None
    for raw in (text or "").splitlines():
        ln = raw.strip()
        mh = _USAGE_HDR_RE.search(ln)
        if mh:
            cur = {"label": mh.group(1).lower(), "pct": None, "reset": None}
            mr = _USAGE_RESET_RE.search(ln)      # 같은 줄에 Resets 가 붙은 좁은 레이아웃
            if mr:
                cur["reset"] = mr.group(1).strip()
            blocks.append(cur)
            continue
        if cur is None:
            continue
        mp = _USAGE_PCT_RE.search(ln)
        if mp and cur["pct"] is None:
            cur["pct"] = int(mp.group(1))
        mr = _USAGE_RESET_RE.search(ln)
        if mr and cur["reset"] is None:
            cur["reset"] = mr.group(1).strip()
    out = {}
    for b in blocks:
        if b["pct"] is None:
            continue
        lab = b["label"]
        key = ("session" if lab.startswith("session")
               else "week_all" if "all models" in lab
               else "week_sonnet" if "sonnet" in lab else "week")
        out[key] = {"pct": b["pct"], "reset": b["reset"]}
    return out or None


# footer 인라인 한도 안내: "You've used 93% of your session limit · resets 1:40pm
# (Asia/Seoul) · /usage-credits to request more". /usage 패널을 안 열어도 이 한 줄로
# 세션/주간 실측 %를 잡아 상태줄 5h% 가 추정치 대신 실측을 따르게 한다(요청).
_INLINE_LIMIT_RE = re.compile(
    r"used\s+(\d{1,3})%\s+of\s+your\s+(session|week(?:ly)?)\s+limit", re.I)
_INLINE_RESET_RE = re.compile(r"resets?\s+(.+?)(?:\s*·|\s*$)", re.I)


def parse_inline_limit(text):
    """Claude footer 인라인 한도 문구에서 한도 %·리셋을 뽑는다(parse_usage 패널과 같은
    형식의 dict). 세션→'session', 주간→'week_all' 키. 없으면 None."""
    out = {}
    for ln in (text or "").splitlines():
        m = _INLINE_LIMIT_RE.search(ln)
        if not m:
            continue
        kind = ("session" if m.group(2).lower().startswith("session")
                else "week_all")
        mr = _INLINE_RESET_RE.search(ln)
        out[kind] = {"pct": int(m.group(1)),
                     "reset": mr.group(1).strip() if mr else None}
    return out or None


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
# Claude Code UI 가 직접 그린 계정 신호(<email>'s Organization · 계정 라벨)에서만
# 계정을 추정한다. 화면 본문(코드·git URL·예시)의 임의 이메일은 계정이 아니므로
# 잡지 않고, 신뢰 신호가 없으면 None(서버가 "unknown" 으로 묶는다 — 사용자 지시).
# RFC 2606/6761 예약·플레이스홀더 도메인 — 실제 로그인 계정일 수 없다. Claude 가
# 처리 중인 transcript/문서 본문의 예시 이메일(user@example.com 등)을 계정으로
# 오검출하던 사용자 보고를 차단한다(상태줄에 @us…@example.com 으로 튐 —
# docs/TOKEN_SAVING_SCENARIO.md §5.7 휴리스틱 포맷 오탐의 실제 사례).
_RESERVED_EMAIL_DOMAINS = frozenset({
    "example.com", "example.net", "example.org", "example.edu",
})
_RESERVED_EMAIL_TLDS = frozenset({"example", "invalid", "localhost", "test"})
# ① 가장 신뢰할 수 있는 신호: Claude Code 계정/조직 표시 "<email>'s Organization"
# (계정 패널·릴리스노트 푸터에 실측). 화면 본문(코드·diff·git URL·예시)에 흩어진
# 임의 이메일이 아니라 Claude UI 가 직접 그린 계정이므로 이걸 최우선·사실상 유일
# 출처로 본다. 아포스트로피는 곧은(') / 둥근(') 둘 다 허용.
_ACCT_ORG_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})['’]s\s+Organization",
    re.I)
# ② 계정 라벨 **바로 뒤**의 이메일(Login:/Account:/Email: <addr> 류 /status 출력).
# 키워드와 이메일이 인접해야만 매치 → transcript 산문("…email someone at x@y.com")이나
# 본문 예시는 안 잡힌다. git SSH URL(git@host:path)은 매치 뒤 ':' 검사로 따로 배제.
_ACCT_LABEL_EMAIL_RE = re.compile(
    r"(?:account|logged\s+in(?:\s+as)?|signed\s+in(?:\s+as)?|login|e-?mail)"
    r"\s*[:·\-]?\s+([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})", re.I)


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


# /clear·세션 시작 직후의 환영(splash) 배너 = 컨텍스트가 비워진 신호. 버전 줄
# "Claude Code v2.1.168" 은 빈/정리된 화면에만 뜨고 대화 중엔 안 보인다. 사용자가
# 직접 /clear 하면 pytmux 자동화 경로(_pc_advance)를 안 타 토큰 누계가 안 끊기므로,
# 이 배너로 수동 /clear(빈 컨텍스트)를 감지해 누계를 새 세션으로 끊는다(상태줄 ctx 근사%).
_WELCOME_RE = re.compile(r"Claude Code v\d+\.\d", re.I)


def claude_welcome(text: str) -> bool:
    """Claude Code 환영/시작 배너(버전 splash)가 보이면 True. /clear 직후·세션 시작
    화면에 뜬다 = 컨텍스트(transcript)가 비워진 신호."""
    return bool(_WELCOME_RE.search(text or ""))


def claude_remote_active(text: str) -> bool:
    """Claude Code 패널이 데스크탑 앱 '원격 제어'에 연결돼 있는지(화면의 'Remote
    Control active' 표시) 판정. 시작 시 자동 /rc 주입(auto-launch)이 **이미 켜진**
    원격제어를 도로 끄지 않도록 idempotent 가드로 쓴다(재시작 resume 후 첫 스캔이
    None→Claude 로 보여 새 세션으로 오인하는 경우 등). 클라 클릭존 판정과 동일 문구."""
    return "remote control" in (text or "").lower()


def claude_remote_blocked(text: str) -> bool:
    """원격 제어가 조직 정책으로 비활성화됐다는 메시지("Remote Control is disabled by
    your organization's policy")가 화면에 보이면 True. 이게 한 번 뜨면 이 세션에서는
    /rc 자동 주입을 영구 중단해야 한다(요청) — 매 새 세션마다 /rc 를 재시도해 같은
    거부 메시지를 반복 띄우는 것을 막는다. 조직 정책이라 폭넓게(소유격 유무 무관) 잡는다."""
    return "disabled by your organization" in (text or "").lower()


def _alias_email(local: str, domain: str) -> str:
    """이메일을 `로컬앞2글자…@도메인` 별칭으로(원문 미노출). 로컬이 2글자 이하면 그대로."""
    alias = (local[:2] + "…") if len(local) > 2 else local
    return f"{alias}@{domain}"


def claude_account(text: str):
    """Claude Code 화면 텍스트에서 **신뢰할 수 있는** 계정 식별자만 추출(없으면 None).

    개인/팀 계정을 토큰 로그에서 구분하기 위함(요금·한도 별개). 화면 본문에는 코드·
    diff·git URL·예시 등 **계정과 무관한 이메일**이 흔하므로(예: git SSH URL
    `git@github.com:user/repo` → 과거 `gi…@github.com` 오검출), 임의 이메일을 계정으로
    잡지 않는다. **정확히 못 잡으면 None 을 돌려 서버가 "unknown" 으로 묶게 한다**
    (사용자 지시 2026-06-07 — 잘못된 계정 표시보다 Unknown 이 옳다).

    우선순위:
      ① Claude UI 의 `<email>'s Organization` 표시(가장 신뢰).
      ② 계정 라벨 바로 뒤 이메일(Login:/Account:/Email: <addr>). git SSH URL 제외.
      ③ 조직/팀명 라벨(`organization: Foo`)·플랜명(라벨 기반, 약한 신호).
    어디서도 못 찾으면 None. 이메일은 별칭화(_alias_email)해 원문을 로그에 안 남긴다."""
    # ① Claude 계정/조직 표시 — 사실상 유일하게 믿을 수 있는 출처.
    m = _ACCT_ORG_RE.search(text)
    if m and not _is_reserved_email_domain(m.group(2)):
        return _alias_email(m.group(1), m.group(2))
    # ② 계정 라벨 바로 뒤 이메일(예약 도메인·git SSH URL 배제).
    for m in _ACCT_LABEL_EMAIL_RE.finditer(text):
        local, domain = m.group(1), m.group(2)
        if _is_reserved_email_domain(domain):
            continue
        if text[m.end():m.end() + 1] == ":":   # git@host:path 등 SSH URL → 배제
            continue
        return _alias_email(local, domain)
    # ③ 라벨 기반 조직/팀명·플랜(약한 신호 — 이메일을 못 찾을 때만).
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
