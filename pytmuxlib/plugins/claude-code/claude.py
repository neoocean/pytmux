"""Claude Code 연동 — 화면 텍스트 휴리스틱(상태/사용량/리밋 파서).

이 모듈은 Claude Code 특화 로직을 코어(멀티플렉서)와 분리해 모은다(docs/internal/HANDOFF.md
§11). 여기 있는 함수들은 **순수 함수**(패널 화면 텍스트 → 상태/사용량/지연)라
서버/클라이언트 어디서도 부담 없이 부를 수 있다. Claude Code 버전이 표시 문구를
바꾸면 가장 먼저 손볼 곳이다(정규식 보강). protocol.py 는 하위호환을 위해 이
세 함수를 re-export 한다.
"""
from __future__ import annotations

import datetime as _dt
import os
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
# claude_state 에서 busy 를 먼저 판정한다.
# §3.4: `↑/↓ N tokens` 단독은 busy 신호에서 **제외** — 응답이 끝난 transcript 에
# 토큰 델타 잔재("↓ 1.9k tokens")가 남아 idle 화면을 busy 로 오인했었다(완료알림·
# auto-doc 지연). busy 는 스피너에 앵커된 신호("… (Ns"·글리프+동명사…·still
# thinking)만 보고, 토큰 화살표는 토큰 누계(claude_usage/_TOK_RE) 전용으로 둔다.
_BUSY_SPINNER_RE = re.compile(
    r"…\s*\((?:\d+\s*m\s*)?\d+\s*s"      # "… (20s" / "… (2m 17s"
    r"|[✽✢✳✶✷✻*·]\s+\w+…"                # 스피너 글리프 + "동명사…"(시간 표시 전)
    r"|still\s+thinking"                  # 명시적 진행 표기
)


# ---- §3.2: 사용량 리밋(차단) 정밀 판정 ----
# 예전엔 `"limit" in low AND any(reset/again/…)` 로 화면 아무 곳의 두 키워드 공존만
# 봐서 오탐이 많았다: ① 사용률 경고("used 93% of your session limit · resets …", 차단
# 아님)를 차단으로 오인 ② 사용자가 입력창에 친 'rate limit'·산문 ③ Claude 가 **우리
# 소스/diff**(테스트 코드에 "usage limit reached" 리터럴이 있음 — 실측 캡처로 확인)를
# 띄우면 그 텍스트가 차단으로 오판. 정밀화: **사용자 입력(>)·소스/diff 줄을 제외한**
# Claude 출력에서, **차단 동사**(reached/exceeded/hit · "limit will reset")를 동반한
# 연속 구(phrase)만 차단으로 본다. "used N% … limit"(사용률 경고)는 차단 동사가 없어
# 자연히 빠진다.
_USER_LINE_RE = re.compile(r"^\s*(?:[│|]\s*)?>")          # 사용자 입력/제출 턴
_CODE_LINE_RE = re.compile(r"^\s*(?:\d+\s*[-+|]|[-+]\s)")  # 행번호+diff·diff 접두
# 슬래시 명령 메뉴/도움말 행("  /usage-credits   Configure … when you hit a limit").
# Claude Code 가 `/` 입력 시 띄우는 명령 목록의 도움말 텍스트가 'hit a limit' 같은
# 차단-동사 구를 품어 idle 화면을 차단(limit)으로 오인시키는 실측 오탐 사례(#9 F1,
# 캡처 corpus 의 limit 오판 4/4 가 이 줄). 줄 첫 토큰이 `/명령` 인 행은 UI 크롬이므로
# 리밋/에러 판정 본문에서 제외(차단 배너·전송 에러는 절대 `/` 로 시작하지 않는다).
_SLASH_MENU_RE = re.compile(r"^\s*(?:[│|]\s*)?/[\w-]+(?:\s|$)")
_LIMIT_BLOCKED_RE = re.compile(
    r"\blimit\s+(?:has\s+been\s+)?(?:reached|exceeded)\b"        # "usage limit reached"
    r"|\b(?:reached|hit|exceeded)\s+(?:your|the|a|my)?\s*"
    r"(?:[\w%]+\s+){0,3}limit\b"                                 # "reached your usage limit"
    r"|\blimit\s+will\s+reset\b",                               # "your limit will reset at 5pm"
    re.I)


def _claude_body(text: str) -> str:
    """사용자 입력(>)·소스/diff 줄·슬래시 메뉴 행을 제외한 Claude **출력**만 한
    문자열로(리밋/리셋 판정용). 리밋 배너·전송 에러는 항상 Claude 출력에 뜨고 사용자
    입력·코드 표시·슬래시 명령 도움말엔 안 뜨므로, 그 영역을 떼어 오탐(사용자 타이핑·
    소스 리터럴·`/usage-credits` 도움말 'hit a limit')을 막는다."""
    keep = [ln for ln in (text or "").splitlines()
            if not _USER_LINE_RE.match(ln) and not _CODE_LINE_RE.match(ln)
            and not _SLASH_MENU_RE.match(ln)]
    return "\n".join(keep)


def claude_limit(text: str) -> bool:
    """화면이 **사용량 리밋으로 차단된** 상태면 True(정밀). 사용자 입력·소스/diff 줄을
    제외한 Claude 출력에서 차단 배너 문구(reached/exceeded/hit · "limit will reset")만
    본다 — 사용률 경고("used N% of your limit")·산문 속 'rate limit' 언급·소스 표시를
    리밋으로 오판하지 않는다. claude_state·parse_reset_delay 가 공유하는 단일 신호."""
    # 컨텍스트 하드스톱("Context limit reached · /compact or /clear to continue")은
    # 'limit reached' 로 _LIMIT_BLOCKED_RE 에 걸리지만 사용량/rate 리밋이 아니라
    # *대화 컨텍스트* 가 꽉 찬 별도 신호다(#9 F2). claude_context_hardstop 이 즉시
    # /compact 로 처리하므로, 여기서 단락해 usage-limit 으로 오인(=오지 않을 reset
    # 시각을 기다리는 autoresume 무장)하지 않는다.
    if claude_context_hardstop(text):
        return False
    return bool(_LIMIT_BLOCKED_RE.search(_claude_body(text)))


# Claude Code idle footer/입력 힌트 앵커 — claude_state 가 "idle"(입력 대기)로
# 인정하는 신호 모음. footer 문구는 버전마다 바뀐다(실측 변천: "(shift+tab to
# cycle)" 접미 제거 → "auto mode on · 2 shells · ↵ for agents · ↓ to manage",
# "accept edits on" → "accept edits is on" 등). 그래서 접미·구두점에 휘둘리는 정확
# 문구 대신 **변동 적은 핵심 어구**로 잡아 footer 가 바뀌어도 살아남게 한다:
#   · 권한모드 이름(공식 4모드 auto/plan mode·accept edits·bypass): on/is on/toggled
#     등 **접미 무관** 부분일치 — footer 가 어떻게 조립되든 모드 이름은 그대로다.
#   · 입력 힌트("for shortcuts/help", "shift+tab"): 모드 표시 없는 입력 박스도 idle.
# 새 footer 변형이 나오면 **여기 한 줄만** 추가하면 되고 claude_state 로직은 불변이다
# (요청: 추후 유사 변화에 최소 변경 대응). 프롬프트 프리픽스 ⏵⏵(전 모드 공통)는
# 원문 대소문자를 보존하므로 claude_state 에서 따로 본다.
_IDLE_ANCHORS = (
    "auto mode", "plan mode", "accept edits", "bypass permissions",
    "shift+tab", "for shortcuts", "for help",
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
    # 사용량 리밋(차단) — 정밀 판정(§3.2). 자동재개 파서와 동일 신호(claude_limit).
    if claude_limit(text):
        return "limit"
    # 처리중: 현행 작업 스피너 또는 레거시 "esc to interrupt"
    if (_BUSY_SPINNER_RE.search(text)
            or "esc to interrupt" in low or "interrupt)" in low):
        return "busy"
    # 입력 대기: 권한 모드 footer(_IDLE_ANCHORS) 또는 프롬프트 프리픽스 ⏵⏵.
    # footer 문구는 버전마다 바뀌므로(예전 사례: "(shift+tab to cycle)" 접미 제거로
    # idle 화면이 None 처리돼 '포맷 미인식 — 추적 중단'이 정상 세션에 오발화, 사용자
    # 보고 2026-06-22; 후속: "accept edits on"→"accept edits is on") 접미·구두점에
    # 둔감한 모드 이름·힌트 앵커(_IDLE_ANCHORS)로 잡는다. ⏵⏵(U+23F5×2 — auto/plan/
    # accept/bypass 전 모드 공통, 신·구 포맷 불변)는 대소문자 보존이라 따로 본다.
    if "⏵⏵" in text or any(a in low for a in _IDLE_ANCHORS):
        return "idle"
    return None


def claude_context_hardstop(text: str) -> bool:
    """**컨텍스트 윈도우 하드스톱** 화면인지 — 컨텍스트가 가득 차 Claude Code 가
    더 진행하지 못하고 "Context limit reached · /compact or /clear to continue"
    류 안내로 멈춘 상태. 이때는 /compact(또는 /clear)만이 진행을 잇는 유일한 수단.

    claude_state 의 "limit"(=사용량/rate 리밋, reset·resume 대기)과 **다른 신호**다:
    이건 사용량이 아니라 *대화 컨텍스트*가 꽉 찬 것이고, 시간이 지난다고 풀리지
    않으며 사용자(또는 자동화)가 즉시 /compact·/clear 해야만 풀린다. 그래서 별도
    파서로 둬 서버가 즉시(idle 지연 없이) 자동 /compact 를 주입할 수 있게 한다.

    오탐 방지: 화면에 `/compact` 또는 `/clear` 리터럴이 실제로 떠 있을 때만 True
    (하드스톱 안내는 항상 "· /compact or /clear to continue" 를 동반한다). 셸 출력에
    우연히 'context limit' 문구만 있어도 슬래시 명령 없이는 발화하지 않는다."""
    low = text.lower()
    if "/compact" not in low and "/clear" not in low:
        return False
    return ("context limit reached" in low
            or ("context" in low and "limit" in low and "to continue" in low))


# ---- 전송 에러(API error / rate limit / overloaded) 자동 재시도(요청) ----
# Claude Code 가 서버측 전송 에러(API Error·429 rate limit·Overloaded)로 응답을 못 마치고
# 멈춘 상태. 사용량 5h 리밋(claude_limit)과는 **다른 신호** — 그건 reset 시각까지 기다려야
# 풀리고 autoresume 가 그 시각으로 다루지만, 전송 에러는 보통 잠깐 뒤 재시도하면 풀린다.
# 그래서 별도 파서로 두고, 서버가 **고정 1분 뒤 "계속" 을 주입**해 이어가게 한다(요청).
# §3.7 코퍼스 감사(2026-06-16, captures/playground.local 137 Claude 프레임): 맨단어
# "rate limit"/"overloaded" 매칭이 **idle 화면의 산문/소스**에서 오탐했다 — 실측 FP:
# Claude 가 pytmux i18n 카탈로그를 편집/표시한 idle 프레임의 명령 설명 문자열
# "…(auto-retry on error·rate limit) on/off…" 가 전송에러로 오판돼, idle+에러 게이트를
# 통과하면 working 아닌 Claude 에 "계속" 을 잘못 주입할 위험(servermixin _fire_retry).
# 실측된 **진짜** 전송에러는 4/4 전부 `⏺ API Error: …` 배너 형식이었다(connect 실패·
# "Server is temporarily limiting … · Rate limited"·Overloaded 모두 배너 동반). 그래서
# 맨단어 영어를 떼고 **배너 앵커**(`api error` 뒤 `:` 또는 `(` 동반 — 실 배너는 항상
# "API Error:" / "API Error (코드)") + 산문에 못 나오는 JSON 에러타입(rate_limit_error/
# overloaded_error)만 본다. 콜론/괄호 요구는 tool-use 설명("Grep captures for api error /
# rate limit") 같은 산문 'api error' 까지 떨군다(이건 busy 가드로도 무해했지만 정밀화).
# 트레이드오프: 배너 없는 맨 "Rate limited"/"Overloaded" 단독 줄은 더는 안 잡지만, 실
# Claude UI 는 항상 배너를 동반하므로 코퍼스 기준 false-negative 0. (test_claude 회귀 고정.)
#
# 추가(2026-06-21): 네트워크/전송 실패의 또 다른 실측 배너
#   "No response from API   · Retrying in 2m 12s · check your network"
# 도 잡는다(captures/playground.local 의 .claude 프레임 7건 — 위 `API Error:` 와 다른
# 형식이라 기존 앵커로는 누락됐다). Claude Code 자체도 카운트다운으로 재시도하지만 종종
# 그 상태로 멈춰 있어, 사용자 요청대로 1분 뒤 "계속" 을 주입해 이어가게 한다. 산문 오탐
# 방지를 위해 **맨 "No response from API" 만으로는 안 잡고** 같은 줄의 동반 문구
# (`Retrying …` / `check your network`)를 함께 요구한다 — 실 배너는 항상 한 줄에
# "No response from API … · Retrying in <시간> · check your network" 형태(끝의 "network"
# 만 줄바꿈되므로 `retry`/`check your network` 중 앞쪽 `retry` 가 동반 앵커로 신뢰적).
# `[^\n]{0,60}` 는 음의 클래스 + 상한이라 선형(ReDoS 안전).
_API_ERROR_RE = re.compile(
    r"\bapi\s+error\b[ \t]*[:(]"                # "API Error:" / "API Error ("(실 배너 형식)
    r"|\brate[\s_\-]*limit_error\b"             # rate_limit_error(JSON 에러타입 — 산문 불가)
    r"|\boverloaded_error\b"                    # overloaded_error(529 JSON 타입)
    r"|\bno\s+response\s+from\s+api\b[^\n]{0,60}"   # "No response from API … "
    r"(?:retr(?:y|ies|ying)|check\s+your\s+network)",  # 동반 앵커(재시도/네트워크 점검)
    re.I)


def claude_api_error(text: str) -> bool:
    """화면이 **전송 에러(API error·rate limit·overloaded·네트워크 무응답)** 로 멈춘 상태면 True.

    claude_limit(사용량 5h 리밋)·claude_context_hardstop(컨텍스트 꽉 참)과 다른 신호다.
    사용자 입력(>)·소스/diff 줄을 제외한 Claude 출력에서만 본다(claude_limit 과 동일
    _claude_body 가드 — 사용자가 친 'rate limit'·테스트/소스 리터럴 오탐 방지). 호출부는
    사용량 리밋(claude_limit) 이 아닐 때만 이 신호로 1분 뒤 "계속" 재시도를 건다."""
    return bool(_API_ERROR_RE.search(_claude_body(text)))


# **사용량(used)%를 직접** 캡처하는 패턴 — Claude Code 신형 푸터 "98% context used"
# (숫자가 클수록 컨텍스트가 꽉 참 = 잔량 계열과 의미가 반대). 아래 잔량 패턴보다
# **먼저** 본다: "98% context used" 는 잔량 패턴 `(\d)%\s*context` 로도 걸리는데, 그럼
# 98 을 잔량으로 오해해 100-98=2% 로 뒤집혀 표시된다(원격 탭 Claude 가 98% 인데 pytmux
# 상태줄엔 ctx 2% 로 뜨던 실 버그). "used" 를 명시적으로 앵커해 사용%로 바로 채택한다.
_CTX_USED_RES = [
    re.compile(r"(\d{1,3})\s*%\s*context\s+used", re.I),
]
# **잔량(headroom)%**를 캡처하는 패턴 — 작을수록 컨텍스트가 꽉 참. (신형 "context used"
# 와 충돌하지 않도록 bare "N% context" 는 여기서 빼고 remaining/auto-compact 만 남긴다.)
_CTX_PCT_RES = [
    re.compile(r"context\s+(?:low|left|remaining)[^0-9%]*?(\d{1,3})\s*%", re.I),
    re.compile(r"(\d{1,3})\s*%\s*(?:remaining|"
               r"until\s+auto[- ]?compact)", re.I),
    re.compile(r"auto[- ]?compact[^0-9%]*?(\d{1,3})\s*%", re.I),
]
# 확장 컨텍스트 모델 배지: "claude-opus-4-8 (1M context)" / "1M context window" 등.
# 컨텍스트 잔량%·토큰과 별개로 "이 패널은 1M(또는 200k) 컨텍스트 모델"임을 알린다.
# 주의(ReDoS): 선행 `\(?\s*` 를 두면 거대 공백 화면(와이드·대부분 빈 줄)에서 `\s*` 가
# 매 위치마다 백트래킹해 O(n²)로 폭주한다(200x50 빈화면서 ~420ms 관측). 매칭은 항상
# 숫자에서 시작하게 두어(`\d+` 가 공백 위치에서 즉시 실패) 선형 스캔이 되게 한다.
# "(1M context)" 의 여는 괄호는 search 가 알아서 건너뛰므로 굳이 패턴에 안 넣는다.
# 추가(§5.9, 2026-06-13): 숫자 런 자체는 위 '공백서 즉시 실패' 가 안 통한다 — 거대
# 숫자열("9"×4만)에선 `\d+`/`[\d.,]*` 가 끝까지 먹고 뒤 리터럴(context/tokens) 실패 시
# 한 자씩 백트래킹하며 시작점마다 반복해 O(n²)로 폭주한다(_CTX_BADGE_RE 적대입력 ~22초
# 실측). 실제 배지/토큰 수치는 길어야 십수 자리이므로 런 길이를 **상한**으로 묶어
# (`{1,9}`/`{0,19}`) 선형으로 만든다(실 매칭 보존 — 1M·200k·"1,234,567 tokens" 등).
_CTX_BADGE_RE = re.compile(r"(\d{1,9}\s*[kKmM])\s*context\b", re.I)
_TOK_RE = re.compile(r"([\d][\d.,]{0,19}\s?[kKmM]?)\s*tokens?\b", re.I)


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

    for rx in _CTX_USED_RES:
        m = rx.search(text)
        if m:
            # _CTX_USED_RES 는 **사용량%**를 그대로 캡처한다("98% context used" → 98).
            # 잔량 패턴과 달리 뒤집지 않는다 — Claude Code 표시와 동일한 방향.
            used = max(0, min(100, int(m.group(1))))
            return f"ctx:{used}%/{badge}" if badge else f"ctx:{used}%"
    for rx in _CTX_PCT_RES:
        m = rx.search(text)
        if m:
            # _CTX_PCT_RES 는 **잔량(headroom)%**를 캡처한다(작을수록 컨텍스트가 꽉 참).
            # 표시는 **사용량%**로 뒤집어 보인다(2026-06-16 요청: 잔여 7% → 사용 93%) —
            # /usage 의 'N% used'·5h 사용률과 같은 '사용량' 방향으로 통일. 자동화용
            # claude_context_pct 는 잔량 의미를 그대로 유지한다(임계 비교가 잔량 기준).
            used = max(0, min(100, 100 - int(m.group(1))))
            # M18-A: 사용%+윈도우를 'ctx:N%/1M' 콤팩트 포맷(공백 없이, 사용자 요청).
            return f"ctx:{used}%/{badge}" if badge else f"ctx:{used}%"
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


# ---- §3.7: Claude 포맷 미인식 가시화(silent failure) ----
def fmt_unknown_update(prev_first, recognized, fg_claude, now, unknown_after):
    """Claude 가 **실행 중인데**(포그라운드 명령에 'claude') 화면 파서가 상태를 못
    읽는 상태가 지속되는지 판정하는 순수 상태전이. 반환 `(first_mono, unknown_bool)`.

    토큰 추적·자동화는 모두 `claude_state`/`claude_usage` 등 현행 화면 포맷 가정에
    강결합돼 있어(§3.7), Claude Code 가 표시 문구를 바꾸면 상태머신이 **조용히 멈춘다**.
    이를 가시화하려면 파서와 **무관한** ground-truth 가 필요한데, 그것이 포그라운드
    프로세스의 명령행에 'claude' 가 있는지(fg_claude)다.

    규약:
      · recognized=True(파서가 상태 인식) 또는 fg_claude=False(Claude 아님)
        → 의심 해제(None, False).
      · recognized=False + fg_claude=True → 의심 누적: 처음 본 시각(first)을 기록하고,
        now-first 가 unknown_after 이상 지속되면 unknown=True.
    호출부(서버)는 fg 검사(ps)가 비싸므로 이 함수를 **throttle 간격**으로만 부른다."""
    if recognized or not fg_claude:
        return None, False
    first = prev_first if prev_first is not None else now
    return first, (now - first >= unknown_after)


# M14c: 모델 배지 파서. 실 캡처 'Opus 4.8 (1M context)' · 'claude-opus-4-8' 둘 다 잡는다.
# 계열 뒤 버전은 점(4.8) 또는 하이픈(4-8) 표기 모두 허용하고 점으로 정규화한다.
# 4-B: 모델 패밀리 화이트리스트를 외부화한다 — 신규 패밀리(Anthropic 이 새 계열을
# 내면)를 **코드 수정 없이** 환경변수 PYTMUX_CLAUDE_MODEL_FAMILIES(쉼표구분)로 보탤
# 수 있다. Fable 은 현행 계열(Fable 5 = claude-fable-5)이라 기본 화이트리스트에
# 포함한다(요청 2026-06-21 — 토큰 모델별 색 분해가 Fable 도 기본 인식하도록).
_DEFAULT_MODEL_FAMILIES = ("Opus", "Sonnet", "Haiku", "Fable")


def _model_families():
    fams = list(_DEFAULT_MODEL_FAMILIES)
    extra = (os.environ.get("PYTMUX_CLAUDE_MODEL_FAMILIES") or "").strip()
    if extra:
        fams += [f.strip() for f in extra.split(",") if f.strip()]
    return fams


def _build_model_re():
    # 버전 토큰은 실 모델 버전 꼴('4.8'·'4-6'·'5'·'4.10')만 받는다 — 각 숫자 성분은
    # 1~2자리, 점/하이픈으로 이음. **임의 길이 숫자(`[0-9]+`)를 받던 것**이 스크롤백/
    # 대화에 섞인 무관한 수를 버전으로 오인하던 원인('Sonnet 255' → 'sonnet-255',
    # 'claude-sonnet-4-5-20250929' → 'sonnet-4.5.20250929' 같은 오표기, 2026-06-22).
    # 끝의 `(?![0-9])` 는 3자리+(255)나 날짜접미(20250929)가 앞부분만 잘려 'sonnet-2'
    # 식으로 매치되지 않도록, 버전을 통째로 포기(→ 계열만 또는 None)하게 한다.
    alt = "|".join(re.escape(f) for f in _model_families())
    return re.compile(
        r"\b(" + alt + r")\b[\s-]*([0-9]{1,2}(?:[.\-][0-9]{1,2})*)?(?![0-9])",
        re.I)


_MODEL_RE = _build_model_re()

# /usage 한도 카테고리 라벨('Current week (Sonnet only)', '(Opus only)' 등)은
# 활성 모델이 **아니다** — 계열명(+버전) 바로 뒤가 'only'면 사용량 분류로 보고 모델
# 매치에서 제외한다(2026-06-22, 오귀속 방지). '(all models)'는 계열명이 없어 애초에
# 안 잡힌다. `_MODEL_RE` 의 `[\s-]*` 가 버전 없는 경우 사이 공백을 이미 먹으므로
# 선행 공백은 0개일 수도 있다 → `\s{0,4}`(상한 — `\s*` 는 §5.9 ReDoS 가드 위반).
_MODEL_CATEGORY_AFTER_RE = re.compile(r"\s{0,4}only\b", re.I)


def claude_model(text):
    """Claude Code 화면 배지에서 모델 계열(+버전)을 best-effort 추출.

    'Opus 4.8 (1M context)' → 'opus-4.8', 'claude-sonnet-4-6' → 'sonnet-4.6',
    계열만 보이면 'opus'. 못 찾으면 None. 모델 과선택 힌트(T3/S4)·표시용 — 현행
    Claude UI 포맷 의존(§5.7)이라 실 골든 픽스처(badge_1m.txt)로 회귀 고정한다.

    배지는 Claude Code UI 하단에 있다(fixture 참고). 대화 내용에 모델명이 언급되면
    첫 번째 매치가 대화 텍스트를 잡을 수 있으므로 **마지막** 매치를 배지로 본다.

    단, /usage 패널의 한도 카테고리('… (Sonnet only)')는 활성 모델이 아니므로
    제외한다 — 사용자가 /usage 를 열고 있을 때 모델이 'sonnet'으로 오귀속되던 것을
    막는다(2026-06-22). 카테고리뿐이면(다른 배지 없음) None(→ 프로브 폴백으로 위임)."""
    matches = [m for m in _MODEL_RE.finditer(text)
               if not _MODEL_CATEGORY_AFTER_RE.match(text, m.end())]
    if not matches:
        return None
    m = matches[-1]
    fam = m.group(1).lower()
    ver = m.group(2)
    return f"{fam}-{ver.replace('-', '.')}" if ver else fam


# 실 Claude Code 푸터 배지의 서명: 모델명 **직후**에 '(… context)' 또는 '/model' 힌트가
# 붙는다(fixture badge_1m.txt: 'Opus 4.8 (1M context) · /model to change'). 이 서명 뒤
# 24자 이내 상한(§5.9 ReDoS: 무제한 `[\s\S]*` 금지)으로 배지를 식별한다. context 뒤의
# 닫는 괄호 `)` 를 요구해 'large context window' 같은 본문 언급은 배제한다.
_MODEL_BADGE_ANCHOR_RE = re.compile(r"[\s\S]{0,24}?(?:context\)|/model)", re.I)

# 화면이 **현재 모델**을 밝히는 자리는 푸터 배지만이 아니다(실캡처 코퍼스 확인
# 2026-07-16). 배지는 상시 표시가 아니라(idle 푸터엔 'auto mode on …'뿐) 배지만 보면
# 사용자가 화면에서 **읽고 있는 모델**이 상태줄에 안 뜬다 — 아래 서명들도 함께 인정해
# 지나가는 즉시 반영한다(사용자 보고 2026-07-16: 화면은 'currently Opus 4.8' 인데
# 상태줄은 프로브 폴백값 'sonnet-5'). 넷 다 모델명 **앞**에 붙어서, 뒤 서명
# (_MODEL_BADGE_ANCHOR_RE)과는 별도 규칙으로 검사한다:
#   · '/' 슬래시 메뉴의 /model 설명 — 'Set the AI model for Claude Code (currently Opus 4.8)'
#   · /model 선택기 안내 — 'Currently using claude-fable-5 for this session only.'
#     ('claude-' 접두 ID 꼴 → `(?:claude-)?`. 뒤에 'for this session only' 가 붙지만
#      _MODEL_CATEGORY_AFTER_RE 는 모델명 **직후** only 만 보므로 카테고리 오인 없음.)
#   · /model 선택 확인 — 'Set model to Opus 4.8 and saved as your default…'
#   · 인패널 /status 패널 — 'Model:            Opus 4.8'(라벨 뒤 정렬 공백)
# 선택기가 나열하는 **후보** 모델 행('2. Opus  Opus 4.8 with 1M context · …')엔 이
# 서명이 없어 활성 모델로 오인되지 않는다(닫는 괄호 없는 'context' 라 뒤 서명도 불발).
# §5.9 ReDoS: 가변 반복은 전부 상한(`{0,20}`, `\s*` 금지) + 검사 창 32자 슬라이스.
#
# `model:` 오검출 방어(2026-07-16 검수 COR-1/M-2): 이 짧은 앵커는 좌측 경계가 없으면
# 패널 본문의 파일/툴 출력에도 걸린다 — ① `ANTHROPIC_MODEL:`·`_model:` **내부**(앞이
# 단어문자) ② YAML frontmatter `model: sonnet`(에이전트 정의 .md, 공백 1개). 걸리면
# 라이브 서명으로 오인돼 상태줄 배지 **및 토큰 DB** 가 실행 모델과 다른 값으로 튀고,
# p4 64941 로 프로브가 강한 값을 못 덮어 자가치유도 약하다(2026-07-04 본문 오검출 재발).
# 그래서 `model:` 만 ⓐ 앞이 단어문자면 배제(`(?<![\w])` → 접두 붙은 env 키 제외)
# ⓑ 뒤 공백 ≥2 요구(인패널 /status 는 'Model:' 뒤 정렬 padding, YAML 은 정확히 1칸).
# 프로즈 앵커('(currently'/'currently using'/'set model to')는 문구가 충분히 특이해
# 종전대로 유지한다.
_MODEL_BEFORE_RE = re.compile(
    r"(?:"
    r"(?:\(currently|currently using|set model to)[ \t]{0,20}"
    r"|(?<![\w])model:[ \t]{2,20}"
    r")(?:claude-)?$", re.I)
_MODEL_BEFORE_WINDOW = 32

# 환영 배너 — Claude 를 띄우면 **매번 맨 위에** 뜨는, 화면이 스스로 밝히는 실행 모델:
#     Claude Code v2.1.211
#     Fable 5 · Claude Max        ← 모델명 **뒤** ' · Claude <플랜>'
#     ~/work/project
# 서명을 요구하기 시작하면서(위 두 규칙) 이 자리가 인정 대상에서 빠져, **새 Claude
# 패널은 /usage 프로브가 돌기 전까지 상태줄 모델 배지가 비었다**(라이브 스크린샷
# 재촬영에서 발견 2026-07-16 — 이전 컷은 'opus-4.8 · ?%/5h', 재촬영은 '?%/5h').
# 배너야말로 '화면이 권위' 원칙에 가장 잘 맞는 자리(프로브는 별도 세션이라 --model
# 로 띄운 패널에선 틀린다)이므로 서명으로 인정한다.
#   · 구분자는 ` · `(U+00B7) — 실 캡처 코퍼스 891건 전수 확인(2026-07-16).
#   · 뒤에 **플랜명**(Max/Pro/…)을 요구해 본문의 모델명 언급과 구분한다.
#   · 앞 공백 상한을 넉넉히(20) 둔 건 배너가 커서이동(`ESC[23G`)으로 그려질 때
#     화면상 열 padding 이 공백으로 남기 때문(코퍼스에 실존).
# §5.9 ReDoS: 가변 반복은 전부 상한(`\s*` 금지).
_MODEL_BANNER_AFTER_RE = re.compile(
    r"[ \t]{0,20}·[ \t]{0,4}Claude[ \t]{1,4}"
    r"(?:Max|Pro|Team|Enterprise|Free)\b", re.I)


def claude_model_badge(text):
    """`claude_model` 과 같되, **화면이 현재 모델을 밝히는 서명**이 붙은 매치만
    인정한다 — **라이브 화면 스크랩 전용**. 서명은 두 갈래다:
      · 모델명 **뒤** — 실 푸터 배지('(… context)'/'/model', _MODEL_BADGE_ANCHOR_RE)
        · 환영 배너 'Fable 5 · Claude Max'(_MODEL_BANNER_AFTER_RE) — 띄울 때마다
          맨 위에 뜨는 자리라, 이게 빠지면 새 패널은 프로브 전까지 배지가 빈다.
      · 모델명 **앞** — /model 메뉴 '(currently X)'·선택기 'Currently using X'·
        선택 확인 'Set model to X'·인패널 /status 'Model: X'(_MODEL_BEFORE_RE)
    푸터 배지는 상시 표시가 아니라(idle 푸터엔 없음) 앞 서명들이 실제로는 사용자가
    모델을 확인하는 주 경로다 — 지나가는 즉시 잡아야 상태줄이 화면과 일치한다.

    화면 스크랩은 전체 화면(screen_text)을 먹으므로, 대화/문서 본문이 모델명을 언급하면
    (예: 이 저장소 온보딩·환경 텍스트의 "Fable 5: 'claude-fable-5'" 같은 모델 ID 설명)
    `claude_model` 의 '마지막 매치=배지' 가정이 깨져 상태줄 모델 배지가 **실행 중 모델과
    다른 모델로 튄다**(사용자 보고 2026-07-04: 팝업/프로브는 opus 인데 상태줄은 'fable-5').
    실 배지에만 있는 서명을 요구해 본문 언급(서명 없음)을 배제한다. 배지가 화면에 없으면
    None → 호출부(servermixin)는 /usage 프로브가 잡은 실 모델로 폴백한다(팝업과 동일 출처).

    카테고리('… (Sonnet only)') 제외는 `claude_model` 과 동일. 서명 있는 매치 중
    **마지막**(화면 하단 = 가장 최근 표시)을 쓴다."""
    hit = None
    for m in _MODEL_RE.finditer(text):
        if _MODEL_CATEGORY_AFTER_RE.match(text, m.end()):
            continue
        if (_MODEL_BADGE_ANCHOR_RE.match(text, m.end())
                or _MODEL_BANNER_AFTER_RE.match(text, m.end())
                or _MODEL_BEFORE_RE.search(
                    text[max(0, m.start() - _MODEL_BEFORE_WINDOW):m.start()])):
            hit = m
    if hit is None:
        return None
    fam = hit.group(1).lower()
    ver = hit.group(2)
    return f"{fam}-{ver.replace('-', '.')}" if ver else fam


# §5.9 ReDoS: `\d+` 무제한은 거대 숫자열에서 뒤 `[kKmM]` 실패 시 O(n²) 백트래킹 →
# 윈도우 수치는 십수 자리면 충분하므로 상한(`{1,9}`)으로 묶어 선형화(실 매칭 보존).
_WINDOW_RE = re.compile(r"(\d{1,9})\s*([kKmM])")


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

    `claude_usage` 가 표시용 문자열을 내는 것과 달리, 이 함수는 **자동 정리 트리거
    (M11)에 쓸 숫자**를 낸다. 같은 `_CTX_PCT_RES` 정규식(잔량 캡처)을 재사용하되,
    **이 함수는 잔량을 그대로** 돌려준다(임계 비교가 잔량 기준). 표시(`claude_usage`)
    는 2026-06-16부터 `100-잔량`=**사용량**으로 뒤집어 보이므로(요청), 표시 숫자와
    이 반환값은 더는 같지 않다(예: 표시 'ctx:93%' ↔ 이 함수 7).

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
    표기를 회귀 고정한다(docs/internal/TOKEN_SAVING_SCENARIO.md §7)."""
    for rx in _CTX_USED_RES:
        m = rx.search(text)
        if m:
            # "98% context used" 는 **사용량**이므로 잔량으로 뒤집어 반환(100-used).
            try:
                v = int(m.group(1))
            except (TypeError, ValueError):
                return None
            return (100 - v) if 0 <= v <= 100 else None
    for rx in _CTX_PCT_RES:
        m = rx.search(text)
        if m:
            try:
                v = int(m.group(1))
            except (TypeError, ValueError):
                return None
            return v if 0 <= v <= 100 else None
    return None


# ---- 계정 식별(토큰 로깅 계정별 구분, docs/internal/HANDOFF.md §10 #7) ----
# Claude Code UI 가 직접 그린 계정 신호(<email>'s Organization · 계정 라벨)에서만
# 계정을 추정한다. 화면 본문(코드·git URL·예시)의 임의 이메일은 계정이 아니므로
# 잡지 않고, 신뢰 신호가 없으면 None(서버가 "unknown" 으로 묶는다 — 사용자 지시).
# RFC 2606/6761 예약·플레이스홀더 도메인 — 실제 로그인 계정일 수 없다. Claude 가
# 처리 중인 transcript/문서 본문의 예시 이메일(user@example.com 등)을 계정으로
# 오검출하던 사용자 보고를 차단한다(상태줄에 @us…@example.com 으로 튐 —
# docs/internal/TOKEN_SAVING_SCENARIO.md §5.7 휴리스틱 포맷 오탐의 실제 사례).
_RESERVED_EMAIL_DOMAINS = frozenset({
    "example.com", "example.net", "example.org", "example.edu",
})
_RESERVED_EMAIL_TLDS = frozenset({"example", "invalid", "localhost", "test"})
# ① 가장 신뢰할 수 있는 신호: Claude Code 계정/조직 표시 "<email>'s Organization"
# (계정 패널·릴리스노트 푸터에 실측). 화면 본문(코드·diff·git URL·예시)에 흩어진
# 임의 이메일이 아니라 Claude UI 가 직접 그린 계정이므로 이걸 최우선·사실상 유일
# 출처로 본다. 아포스트로피는 곧은(') / 둥근(') 둘 다 허용.
# §5.9 ReDoS: 무제한 `+` 로컬/도메인은 거대 문자열("a"×4만)에서 뒤 `@`/구조 실패 시
# 시작점마다 한 자씩 백트래킹해 O(n²)로 폭주(_ACCT_ORG_RE 적대입력 ~14초 실측). 실
# 이메일은 RFC 상 로컬≤64·도메인≤255·TLD 짧으므로 길이 상한으로 묶어 선형화한다
# (실 매칭 보존). 상한은 위치당 비용을 상수로 제한 → 전체 O(n).
_ACCT_ORG_RE = re.compile(
    r"([A-Za-z0-9._%+\-]{1,64})@([A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24})"
    r"['’]s\s+Organization", re.I)
# ② 계정 라벨 **바로 뒤**의 이메일(Login:/Account:/Email: <addr> 류 /status 출력).
# 키워드와 이메일이 인접해야만 매치 → transcript 산문("…email someone at x@y.com")이나
# 본문 예시는 안 잡힌다. git SSH URL(git@host:path)은 매치 뒤 ':' 검사로 따로 배제.
_ACCT_LABEL_EMAIL_RE = re.compile(
    r"(?:account|logged\s+in(?:\s+as)?|signed\s+in(?:\s+as)?|login|e-?mail)"
    r"\s*[:·\-]?\s+([A-Za-z0-9._%+\-]{1,64})@([A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24})",
    re.I)  # §5.9 ReDoS: 이메일 부분 길이 상한(위 _ACCT_ORG_RE 와 동일 사유)


def _is_reserved_email_domain(domain: str) -> bool:
    """예약/플레이스홀더 도메인(절대 실계정 아님)이면 True."""
    d = domain.lower()
    return d in _RESERVED_EMAIL_DOMAINS or d.rsplit(".", 1)[-1] in _RESERVED_EMAIL_TLDS


# (조직/팀명 라벨 `_ORG_RE`·플랜명 `_PLAN_RE` 약신호는 2026-06-12 제거: Claude 가
# 산문/도구 출력에 띄운 임의 구절을 계정으로 오검출해 — 실측 사례 'Account: Running 1
# shell command' 류가 토큰 DB 에 "Running 1 shell command" 계정으로 적재 — 존재하지
# 않는 계정이 토큰을 쓴 것처럼 보였다. 계정 신호는 이메일 기반 ①②만 신뢰한다.
# 정확히 못 잡으면 None → unknown(2026-06-07 사용자 지시)이 옳다.)


# Claude Code 세션 종료 시 뜨는 피드백 프롬프트("How is Claude doing this session?
# 1:Bad 2:Fine 3:Good 0:Dismiss"). 자동으로 Esc 를 눌러 치우기 위한 감지(#26).
# 주의: 표시는 "0:Dismiss" 지만 이 배너는 컴포저 위 비모달이라 '0' 을 쏘면 닫히지
# 않고 컴포저에 찍힌다 — 실제 Dismiss 는 Esc/Space/Enter. serverclaude 참조.
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


def fmt_long_turn_badge(elapsed_sec) -> str:
    """장기 턴 경고 배지 문자열. 기본 '⚠ M:SS'(경과 분:초)지만 **1시간 이상이면
    '⚠ H:MM'(시:분)** 으로 바꾼다 — 1시간을 넘으면 분이 60+ 로 커져 읽기 어렵다는
    사용자 요청(2026-06-17). 경계(3600s)에서 60:00→1:00 으로 단위가 매끄럽게 넘어가
    M:SS 의 분이 60 이상으로 가지 않는다(표기 중첩 없음)."""
    el = max(0, int(elapsed_sec))
    if el >= 3600:
        return "⚠ %d:%02d" % (el // 3600, (el % 3600) // 60)
    return "⚠ %d:%02d" % (el // 60, el % 60)


def claude_remote_active(text: str) -> bool:
    """Claude Code 패널이 데스크탑 앱 '원격 제어'에 연결돼 있는지(화면의 'Remote
    Control active' 표시) 판정. 시작 시 자동 /rc 주입(auto-launch)이 **이미 켜진**
    원격제어를 도로 끄지 않도록 idempotent 가드로 쓴다(재시작 resume 후 첫 스캔이
    None→Claude 로 보여 새 세션으로 오인하는 경우 등). 클라 클릭존 판정과 동일 문구."""
    return "remote control" in (text or "").lower()


# Claude Code `/rc`(원격 제어) 실행 시 뜨는 **원격 제어 관리 메뉴**. 예전 CLI 의 /rc 는
# 메뉴 없이 원격을 토글했지만, 현재 CLI 는 세션을 모바일 앱/claude.ai 에 노출한 뒤
# "Disconnect this session · Show QR code(Scan with your phone) · Continue (Enter to
# select · Esc to continue)" **비모달 메뉴**를 띄워 응답 대기로 진행을 막는다. auto-launch
# (set_claude_auto_launch, 기본 ON)가 새 세션마다 /rc 를 1회 주입하므로 이 메뉴가 매번
# 떠 자동화가 멈췄다(사용자 보고 2026-06-18: 폰에서 pytmux 로 접속 중 Continue 메뉴가
# 진행을 가로막음). 메뉴 안내대로 **Esc=Continue**(원격은 켜진 채 메뉴만 닫힘)를 자동
# 주입해 치우기 위한 감지 — 피드백 프롬프트 자동 Dismiss(#26)와 같은 Esc 경로를 탄다.
# 'Disconnect this session' + QR/scan 안내가 **함께** 보일 때만 잡아, 산문에 'Disconnect'
# 한 단어가 우연히 섞인 경우의 오검출을 막는다.
_REMOTE_MENU_RE = re.compile(r"Disconnect this session", re.I)
_REMOTE_MENU_QR_RE = re.compile(r"Show QR code|Scan with your phone", re.I)


def claude_remote_menu(text: str) -> bool:
    """Claude `/rc` 원격 제어 관리 메뉴(Continue/Disconnect/QR)가 떠 진행을 막고 있으면
    True(자동 Esc Dismiss 대상). 'Disconnect this session' 과 QR/scan 안내가 함께 보일
    때만 — 단어 하나가 산문에 우연히 섞인 경우의 오검출을 피한다."""
    t = text or ""
    return bool(_REMOTE_MENU_RE.search(t) and _REMOTE_MENU_QR_RE.search(t))


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


def _resolve_account(text: str):
    """화면 텍스트에서 신뢰할 수 있는 계정을 (전체, 별칭) 튜플로 추출(없으면 None).

    이메일이면 전체=`local@domain`, 별칭=`local앞2글자…@domain`(원문 미노출). 조직/팀명·
    플랜처럼 이메일이 아닌 식별자는 전체=별칭(가릴 게 없음). claude_account(별칭, 로그·
    이벤트용)와 claude_account_full(전체, footer 표시용)이 같은 판정을 공유하게 한다."""
    # ① Claude 계정/조직 표시 — 사실상 유일하게 믿을 수 있는 출처.
    m = _ACCT_ORG_RE.search(text)
    if m and not _is_reserved_email_domain(m.group(2)):
        local, domain = m.group(1), m.group(2)
        return (f"{local}@{domain}", _alias_email(local, domain))
    # ② 계정 라벨 바로 뒤 이메일(예약 도메인·git SSH URL 배제).
    for m in _ACCT_LABEL_EMAIL_RE.finditer(text):
        local, domain = m.group(1), m.group(2)
        if _is_reserved_email_domain(domain):
            continue
        if text[m.end():m.end() + 1] == ":":   # git@host:path 등 SSH URL → 배제
            continue
        return (f"{local}@{domain}", _alias_email(local, domain))
    # (③ 조직/팀명·플랜 약신호는 제거 — 위 _ORG_RE/_PLAN_RE 제거 주석 참조.)
    return None


def claude_account(text: str):
    """Claude Code 화면 텍스트에서 **신뢰할 수 있는** 계정 식별자(별칭)만 추출(없으면 None).

    개인/팀 계정을 토큰 로그에서 구분하기 위함(요금·한도 별개). 화면 본문에는 코드·
    diff·git URL·예시 등 **계정과 무관한 이메일**이 흔하므로(예: git SSH URL
    `git@github.com:user/repo` → 과거 `gi…@github.com` 오검출), 임의 이메일을 계정으로
    잡지 않는다. **정확히 못 잡으면 None 을 돌려 서버가 "unknown" 으로 묶게 한다**
    (사용자 지시 2026-06-07 — 잘못된 계정 표시보다 Unknown 이 옳다).

    우선순위(이메일 신호만 — 비이메일 약신호 ③은 2026-06-12 오검출로 제거):
      ① Claude UI 의 `<email>'s Organization` 표시(가장 신뢰).
      ② 계정 라벨 바로 뒤 이메일(Login:/Account:/Email: <addr>). git SSH URL 제외.
    어디서도 못 찾으면 None. 이메일은 별칭화(_alias_email)해 원문을 로그·이벤트에 안
    남긴다(footer 표시용 전체 이메일은 claude_account_full 참조)."""
    r = _resolve_account(text)
    return r[1] if r else None


def claude_account_full(text: str):
    """claude_account 과 동일 판정의 **별칭 미적용(전체)** 계정 — footer 표시 전용.

    별칭(claude_account)은 디스크 토큰 로그·훅 이벤트 env 에 원문 이메일을 남기지 않으려는
    프라이버시 장치다. 반면 하단 상태줄은 사용자 본인의 휘발성 화면이라, 폭이 충분하면
    전체 계정명을 보이고 싶다는 요청(2026-06-12)이 있었다. 이 값은 상태 메시지로만 클라에
    전달돼 footer 가 폭에 맞춰 전체/별칭을 고르는 데 쓰인다(로그·이벤트는 별칭 그대로)."""
    r = _resolve_account(text)
    return r[0] if r else None


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


# ---- 라이브 입력박스(지금 타이핑 중인 프롬프트) 추출 — claude_prompt 의 역(逆) ----
# claude_prompt 은 **제출된** transcript 줄을 뽑고 하단 라이브 입력박스를 건너뛴다.
# 여기서는 반대로 화면 맨 아래 **입력박스에 현재 들어 있는 텍스트**를 긁는다. 작성창
# open_compose 의 '프롬프트 인계' 가 클라 키 추적(_prompt_buf)이 빈 경우 — 원격제어
# (/rc)·재접속처럼 클라 on_key 를 안 거친 입력 — 시드/비우기 길이로 쓰는 fallback.
# **Claude UI 포맷 의존 best-effort**: 박스 구조가 불분명하면 None 을 돌려 호출부가
# 안전히 기존 동작(추적치/초안)으로 떨어지게 한다. 단일 줄은 견고, 멀티라인은 근사치.
_BOX_TOP = "╭┌"          # 박스 위 모서리(좌)
_BOX_BOTTOM = "╰└"       # 박스 아래 모서리(좌)
_BOX_SIDE = "│|"         # 박스 세로 테두리(유니코드/ASCII 폴백)
_PROMPT_MARK = ("❯", ">")   # 입력 프롬프트 마커: 최신 Claude=❯(U+276F), 구=">"

# **현행 Claude UI 는 모서리 박스를 안 그린다** — 입력 구획을 위아래 **가로줄**로만
# 나눈다(fixture idle.txt·badge_1m.txt 가 이미 이 모양):
#     ────────────────────────────────
#     ❯ 첫 줄
#       둘째 줄
#     ────────────────────────────────
# 모서리(╭╰)도 세로 테두리(│)도 없어서, 모서리만 찾던 종전 탐색은 top/bottom 을 못
# 잡고 `rows=[cursor_y]`(박스 없음 폴백)로 떨어졌다 → ESC→Insert 작성창에 **커서가
# 있던 줄만** 인계됨(사용자 보고 2026-07-16). p4 64741 의 '화면 긁기 우선' 은 맞는
# 수정이었지만, 정작 파서가 현행 박스 모양에 눈이 멀어 한 줄만 긁고 있었다 — 회귀
# 테스트가 **구 UI(모서리 박스)** 로만 고정돼 있어 초록불이었다(픽스처는 현행인데).
_BOX_RULE_CHARS = "─━═-"    # 가로줄 구획(유니코드 실선/굵은선/겹선 + ASCII 폴백)
_BOX_RULE_MIN = 3           # 이보다 짧으면 구획선으로 안 봄('--' 같은 본문 배제)


def _is_box_rule(line: str) -> bool:
    """모서리·세로 테두리 없이 **가로줄만**으로 입력 구획을 그리는 현행 Claude UI 의
    구획선인지. 대화 본문의 '---'(마크다운 구분선) 같은 것도 참이 될 수 있지만,
    탐색은 커서에서 **가장 가까운** 줄만 취하므로(커서=입력 구획 안) 먼 오검출은
    닿지 않는다."""
    s = (line or "").strip()
    return len(s) >= _BOX_RULE_MIN and all(c in _BOX_RULE_CHARS for c in s)


def _box_inner(line: str) -> str:
    """박스 한 줄에서 좌우 세로 테두리(│)와 바깥 패딩 한 칸을 떼고 안쪽 내용을
    돌려준다. 테두리가 없으면(박스 없는 입력 줄) 줄 전체를 우측 공백만 떼고 돌려준다."""
    l = -1
    for i, ch in enumerate(line):
        if ch in _BOX_SIDE:
            l = i
            break
    if l == -1:
        return line.rstrip()
    r = len(line)
    for i in range(len(line) - 1, l, -1):
        if line[i] in _BOX_SIDE:
            r = i
            break
    inner = line[l + 1:r]
    if inner[:1] == " ":      # 박스 안쪽 패딩 한 칸 제거
        inner = inner[1:]
    return inner.rstrip()


# 트랜스크립트에 **제출된** 사용자 프롬프트 줄: 열 0 의 마커(`> `/`❯ `)+내용. 라이브
# 입력박스 안의 `> ` 는 박스 테두리(`│`)가 앞서므로 열 0 앵커가 그것을 자연히 배제한다
# (박스 없는 구버전 입력줄은 맨 아래 하나만 걸리는데, 그건 '지금 프롬프트'라 무해).
# 멀티라인 프롬프트는 첫 줄에만 마커가 있고 이어지는 줄은 들여쓰기라 프롬프트당 1개.
_PROMPT_MARK_LINE_RE = re.compile(r"^[>❯][ \xa0]+\S")


def claude_prompt_marks(texts) -> list:
    """스크롤백+화면 행 텍스트에서 **제출된 사용자 프롬프트** 줄의 인덱스를 위→아래
    순서로 돌려준다(없으면 []). `esc ctrl+↑/↓` 프롬프트 점프의 목표 지점 — 한 프롬프트
    (=한 턴의 시작)당 하나다. 순수 함수(패널/서버 비의존)라 단위 테스트가 쉽다."""
    return [i for i, ln in enumerate(texts or ())
            if _PROMPT_MARK_LINE_RE.match(ln or "")]


# 큐된 메시지 대기 중 라이브 입력칸에 뜨는 플레이스홀더(실제 입력 아님). 이 문구를
# 작성창 시드로 인계하면 안 된다(사용자 보고 2026-07-18: 팝업에 이 안내문이 들어옴).
_INPUT_PLACEHOLDER_RE = re.compile(r"^Press up to edit queued messages", re.I)


def _prompt_block_rows(lines, anchor):
    """박스 구획선(가로줄/모서리)이 없을 때 — 현행 Claude 는 **busy·큐 대기 중** 입력
    구획선을 안 그린다 — 커서(anchor)를 품은 **논리 프롬프트 블록**의 행 인덱스를
    돌려준다(없으면 None → 입력줄 아님, 추적치 폴백).

    블록 = 프롬프트 마커(❯/>) 줄 하나 + 그 아래로 이어지는 **들여쓴 연속 줄**(다음
    마커·빈 줄·구획선·footer 힌트 전까지). 종전엔 이 자리에서 `[anchor]` 한 줄만 긁어,
    여러 줄 프롬프트에서 커서 줄만 오고 이웃 줄이 빠졌다(사용자 보고: 커서 윗줄만 옴).
    커서가 busy 스피너처럼 마커 없는 줄에 얹히면 None → 그 텍스트를 안 긁는다."""
    n = len(lines)
    start = None
    for i in range(anchor, -1, -1):
        s = lines[i].strip()
        if not s:
            break                    # 위쪽 빈 줄 = 블록 경계
        if lines[i].lstrip("\xa0 ")[:1] in _PROMPT_MARK:
            start = i
            break
        if _is_box_rule(lines[i]) or _FOOTER_HINT_RE.search(s):
            break                    # 구획선/footer 를 먼저 만남 = 입력 블록 밖
    if start is None:
        return None
    rows = [start]
    for i in range(start + 1, n):
        s = lines[i].strip()
        if not s or _is_box_rule(lines[i]) or _FOOTER_HINT_RE.search(s):
            break
        if lines[i].lstrip("\xa0 ")[:1] in _PROMPT_MARK:
            break                    # 다음 프롬프트 블록 시작
        rows.append(i)
    return rows


def claude_input_box(lines, wrap=(), cursor_y=None):
    """패널 화면 행 문자열 목록에서 라이브 입력박스의 현재 텍스트를 추출(best-effort).

    lines: 패널 콘텐츠 행 문자열(위→아래). wrap: soft-wrap 연속원 행 인덱스 집합 —
    그 행은 윗행과 **개행 없이** 이어 붙인다(자동 줄바꿈이라 한 논리 줄). cursor_y:
    하드웨어 커서 행(있으면 그 행이 입력박스 안이므로 앵커로 쓴다). 반환: 입력 텍스트
    (빈 박스면 ""), 입력박스를 못 찾으면 None."""
    n = len(lines)
    if not n:
        return None
    wrap = set(wrap or ())
    # 앵커 행: 커서 행 우선. 없으면 아래에서부터 박스 테두리/footer/빈 줄을 건너뛴 첫 줄.
    if cursor_y is None or not (0 <= cursor_y < n):
        cursor_y = None
        for i in range(n - 1, -1, -1):
            s = lines[i].strip()
            if not s:
                continue
            if s[0] in _BOX_TOP or s[0] in _BOX_BOTTOM or _is_box_rule(s):
                continue
            if _FOOTER_HINT_RE.search(s):
                continue
            cursor_y = i
            break
        if cursor_y is None:
            return None
    # 앵커를 감싸는 박스 테두리 탐색(위로 top·아래로 bottom). 다른 박스 경계를 먼저
    # 만나면 앵커가 박스 밖(박스 없는 입력)인 것으로 본다.
    # 구획선(현행 UI)은 위아래가 같은 모양이라 **탐색 방향**이 top/bottom 을 정한다:
    # 커서 위의 첫 구획선=top, 아래의 첫 구획선=bottom(커서는 입력 구획 안).
    # 주의: `c` 는 반드시 **비었는지 먼저** 본다 — `"" in _BOX_TOP` 은 파이썬에서
    # True(빈 문자열은 모든 문자열의 부분문자열)라, 빈 줄이 모서리로 오인된다. 구 UI
    # 에선 박스 안 빈 줄에도 세로 테두리(│)가 있어 c 가 빌 일이 없어 안 드러났지만,
    # 테두리 없는 현행 UI 에선 멀티라인 프롬프트 **중간의 빈 줄**이 곧장 이 함정을
    # 밟아 거짓 top 이 잡혔다(→ 커서 줄만 인계, 2026-07-16).
    top = bottom = None
    for i in range(cursor_y, -1, -1):
        if i != cursor_y and _is_box_rule(lines[i]):
            top = i
            break
        c = lines[i].lstrip()[:1]
        if c and c in _BOX_TOP:
            top = i
            break
        if c and c in _BOX_BOTTOM and i != cursor_y:
            break
    for i in range(cursor_y, n):
        if i != cursor_y and _is_box_rule(lines[i]):
            bottom = i
            break
        c = lines[i].lstrip()[:1]
        if c and c in _BOX_BOTTOM:
            bottom = i
            break
        if c and c in _BOX_TOP and i != cursor_y:
            break
    if top is not None and bottom is not None and top < bottom:
        rows = list(range(top + 1, bottom))
    else:
        # 박스(가로줄/모서리)를 못 찾음 — busy·큐 대기 중 현행 Claude 는 입력 구획선을
        # 안 그린다. 그땐 프롬프트 마커(❯/>)로 **논리 프롬프트 블록**을 찾아 전체를
        # 인계한다(커서 줄 + 이웃 줄). 마커를 못 찾으면(입력줄 아님) None → 추적치 폴백.
        rows = _prompt_block_rows(lines, cursor_y)
        if rows is None:
            return None
    if not rows:
        return ""
    parts = []
    indent = 2          # 연속 줄에서 떼어낼 정렬 들여쓰기 폭(첫 줄에서 학습)
    for k, ri in enumerate(rows):
        inner = _box_inner(lines[ri])
        if k == 0:
            t = inner.lstrip("\xa0 ")   # 앞 공백/비분리공백 제거(마커 앞 패딩)
            if t[:1] in _PROMPT_MARK:   # 프롬프트 마커("> "/"❯ ") 제거
                # 최신 Claude 는 U+276F(❯)+비분리공백(\xa0), 구버전은 ">"+공백.
                # 마커를 안 떼면 ESC→Insert 작성창 시드에 마커가 딸려 온다(사용자 보고).
                t = t[1:]
                if t[:1] in ("\xa0", " "):
                    t = t[1:]
            # 연속 줄은 첫 줄의 **텍스트 시작 열**에 맞춰 들여써진다 → 첫 줄이 마커·
            # 패딩으로 소비한 폭이 곧 그 정렬 폭이다. 종전엔 2칸으로 못박아, 세로
            # 테두리가 없어 바깥 패딩이 inner 에 남는 현행 UI 에서 한 칸이 덜 떼여
            # 둘째 줄부터 공백이 붙어 나왔다(2026-07-16).
            indent = len(inner) - len(t)
            parts.append(t.rstrip())
            continue
        # 연속 줄: 첫 줄 아래 정렬용 들여쓰기(indent 칸까지)를 떼고 잇는다. 그 이상의
        # 들여쓰기는 사용자가 친 것이므로 보존한다(붙여넣은 코드 블록 등). soft-wrap
        # (wrap 집합)은 개행 없이, 하드 개행(Shift+Enter)은 "\n" 으로 잇는다.
        b = inner
        j = 0
        while j < indent and j < len(b) and b[j] == " ":
            j += 1
        b = b[j:].rstrip()
        parts.append(b if ri in wrap else "\n" + b)
    text = "".join(parts)
    # 큐 대기 플레이스홀더("Press up to edit queued messages")는 실제 입력이 아니므로
    # 작성창에 시드하지 않는다 — 라이브 입력칸은 실제로 비어 있다(빈 시드 = "").
    if _INPUT_PLACEHOLDER_RE.match(text.strip()):
        return ""
    return text


# ---- 자동 /compact 억제: Claude 가 사용자에게 질문/선택을 요청 중인지(요청) ----
# 화면이 질문으로 끝나면(=사용자 답을 기다리는 중) 자동 /compact 를 넣지 않는다 —
# 답하기 전에 압축하면 진행 중 상호작용을 끊거나(선택지 사라짐) 무의미하다. 두 신호:
#   ① 대화형 선택 박스(❯ 선택자 + "1." 류 번호 옵션) — 권한 확인·옵션 선택 등
#   ② 입력박스("> ")·footer 힌트를 제외한 마지막 본문 줄이 물음표로 끝남
_CHOICE_RE = re.compile(r"❯\s*\d+[.)]\s")
_FOOTER_HINT_RE = re.compile(
    r"for shortcuts|/help|shift\s*\+\s*tab|esc to|ctrl\s*\+", re.I)


def claude_awaiting_answer(text: str) -> bool:
    """Claude 화면이 사용자 답변을 기다리는 질문/선택으로 끝나면 True(best-effort).
    자동 /compact 가 질문 직후 끼어드는 것을 막는 가드(요청). 보수적으로 잡는다 —
    오탐이어도 자동 /compact 한 번을 건너뛸 뿐이다."""
    text = text or ""
    # ① 대화형 선택 박스 — ❯ + 번호 옵션. 가장 확실한 '답 대기' 신호.
    if _CHOICE_RE.search(text):
        return True
    # ② 입력박스·footer 힌트·글리프·빈 줄을 아래에서부터 건너뛴 첫 본문 줄을 본다.
    for line in reversed(text.splitlines()):
        s = line.strip().strip("│|").strip()
        if not s or s.startswith(">"):            # 빈 줄 / 라이브 입력박스
            continue
        if _FOOTER_HINT_RE.search(s):             # footer 힌트 줄
            continue
        if not any(c.isalnum() for c in s):       # 글리프/구분선만
            continue
        return s.endswith("?") or s.endswith("？")   # ASCII/전각 물음표
    return False


def claude_perm_mode(text: str):
    """Claude Code idle 권한모드 footer 에서 현재 권한모드를 best-effort 추정.

    반환:
      "auto"    — 진짜 auto 모드("auto mode on"): 모든 동작을 분류기 안전검사 후
                  자동 수락. acceptEdits 와 **다른** 모드다(Claude Code v2.1.x).
      "accept"  — acceptEdits("accept edits on" / 구버전 "auto-accept edits on"):
                  파일 편집·기본 FS 명령만 자동 수락(다른 Bash·네트워크는 확인).
      "bypass"  — 권한 우회("bypass permissions"). 명시적·위험 모드라 건드리지 않음.
      "plan"    — 플랜 모드("plan mode on")
      "default" — 일반 모드(footer 는 보이나 위 어느 것도 아님)
      None      — 권한모드 footer 신호가 안 보임(판정 불가)

    주의: acceptEdits 와 auto 는 **둘 다 ⏵⏵ 글리프**를 쓴다(공식 permission-modes
    문서). 그래서 글리프가 아니라 **문구**("auto mode" vs "accept edits")로만 가른다 —
    예전엔 ⏵⏵·"accept edits on" 을 모두 auto 로 봐, 새 세션이 acceptEdits 에서 멈춰
    진짜 auto 까지 못 가던 버그가 있었다(사용자 보고). 버전이 footer 문구를 바꾸면
    가장 먼저 손볼 곳이다(claude_state 와 같은 footer 를 본다)."""
    low = text.lower()
    # 명시적 권한모드 문구부터 판정한다(모드명은 footer 줄 앞쪽이라 좁은 폭(모바일)
    # 에서 뒤가 잘려도 살아남는다). ⏵⏵ 글리프는 auto·accept 공용이라 단독 신호로 안 쓴다.
    if "bypass permissions" in low:
        return "bypass"
    if "auto mode" in low:                 # 진짜 auto(모든 동작 자동, 분류기 검사)
        return "auto"
    if "accept edits" in low or "auto-accept" in low:   # acceptEdits(편집만)
        return "accept"
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
    지금부터 그때까지의 지연(초)을 반환. 못 찾으면 None.

    §3.2: **차단 상태일 때만**(claude_limit) 시각을 찾고, 시각도 사용자 입력·소스/diff
    를 제외한 Claude 출력(_claude_body)에서만 본다 — 화면 아무 곳의 우연한 시각 숫자를
    리셋 시각으로 오인하던 위험을 줄인다(자동재개가 엉뚱한 delay 로 트리거되는 것 방지)."""
    if not claude_limit(text):
        return None
    text = _claude_body(text)
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


# ---- /usage 리셋 표기 → 절대 시각(epoch) 파서 ----
# parse_reset_delay(위)는 "리밋 차단 화면"에서 지연(초)을 구하는 자동재개 전용이고,
# 이건 /usage 패널·footer 인라인이 주는 **리셋 표기 문자열**("6:59pm (Asia/Seoul)" ·
# "Jun 13 at 3am (Asia/Seoul)")을 epoch 로 바꾼다 — 토큰 팝업이 ① 리셋까지 남은
# 시간 표시 ② 현재 5h/주간 창 구간 역산(창 시작 = 리셋 - 5h/7일)에 쓴다.
_RESET_MD_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})"
    r"(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?)?", re.I)
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def parse_reset_ts(reset, now: "_dt.datetime | None" = None):
    """`/usage` 리셋 표기 문자열을 epoch 초(float)로 변환한다(못 읽으면 None).

    실측 표기 형태(parse_usage 가 긁는 그대로): 세션 "6:59pm (Asia/Seoul)" ·
    "7pm (Asia/Seoul)", 주간 "Jun 13 at 3am (Asia/Seoul)", 드물게 24시간 "14:30".
    표기 타임존은 이 머신 로컬과 같다고 가정한다 — 실측은 같은 머신에서 띄운
    claude(그림자 프로브/인패널)의 표기라 로컬 타임존으로 그려진다.

    해석 규약: 월·일이 있으면 그 달력일(200일 넘게 지난 월일이면 내년 — 12월 말
    실측의 'Jan 2' 연도 롤오버). **약간 지난** 월일은 그대로 과거를 돌려줘 호출부가
    stale 실측을 판단하게 한다(내년으로 잘못 점프하지 않음). 시각만 있으면 지금
    이후 가장 가까운 그 시각(지났으면 다음날 — parse_reset_delay 와 동일 규약)."""
    text = reset or ""
    now = now or _dt.datetime.now()
    m = _RESET_MD_RE.search(text)
    if m and m.group(2):
        month = _MONTHS[m.group(1).lower()[:3]]
        day = int(m.group(2))
        hour = minute = 0
        if m.group(3):
            hour = int(m.group(3))
            minute = int(m.group(4) or 0)
            ap = (m.group(5) or "").lower()
            if ap == "pm" and hour != 12:
                hour += 12
            if ap == "am" and hour == 12:
                hour = 0
        if hour > 23 or minute > 59:
            return None
        try:
            target = now.replace(month=month, day=day, hour=hour,
                                 minute=minute, second=0, microsecond=0)
        except ValueError:
            return None                      # 잘못된 월일(예: Feb 30)
        if (now - target).days > 200:        # 한참 지난 월일 → 내년(연도 롤오버)
            try:
                target = target.replace(year=now.year + 1)
            except ValueError:
                return None                  # 내년에 없는 날(2/29)
        return target.timestamp()
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
        target += _dt.timedelta(days=1)      # 지난 시각 → 다음날 그 시각
    return target.timestamp()


# ---- M16: PTY 밖 에스컬레이션 훅 — status 전이 → (event, env) (§8) ----
# 클라(PytmuxApp)가 status 마다 호출한다. 이미 송신되는 신호(claude_pending·
# 활성패널 limit)의 **상승 에지에서만** 1회 발화하도록 (event, env)
# 목록을 계산하고 prev(가변 dict)를 갱신한다. PytmuxApp 은 import 불가(함수 내부
# 정의)라 여기 모듈 함수로 빼서 단위 테스트가 가능하게 한다(alert-bell 전이 패턴과 동형).
def saver_hook_events(prev: dict, msg: dict) -> list:
    """status 전이에서 발화할 [(event, env_dict), …] 을 계산하고 prev 를 갱신한다.

    prev 키: pending_kind(str|None)·limit(bool). 상승 에지(None→값, False→True)에서만
    이벤트를 낸다 — 같은 화면을 여러 프레임 봐도 중복 발화하지 않는다(§5.6). env 는
    사용자 훅 셸 명령이 참조할 PYTMUX_* 컨텍스트. 계정은 별칭(claude_account)만 —
    원문 이메일은 싣지 않는다."""
    events = []
    acct = msg.get("claude_account") or ""

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
