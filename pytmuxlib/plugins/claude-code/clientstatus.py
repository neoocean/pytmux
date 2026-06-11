"""claude-code 하단 상태줄(StatusBar) 세그먼트/흡수(Phase 2c).

코어 clientwidgets.StatusBar 의 `update_status` Claude 흡수 블록과 `_render_main` 의
Claude 세그먼트 렌더(모델 배지·컨텍스트·토큰Σ·예산경고·카운트다운·폭주경고)를 이리로
이전했다. 코어는 `plugins.client_statusbar_update`·`plugins.client_statusbar` 훅으로만
닿고, status 위젯의 claude_* 속성(코어 __init__ 의 안전한 기본값)을 읽고 쓴다 →
디렉토리를 지우면 흡수·렌더가 사라져 상태줄 Claude 세그먼트가 전혀 안 나타난다.

무게: rich.Segment/Style 과 clientutil 헬퍼만 쓴다(textual 클래스 import 안 함)."""
from __future__ import annotations


def init_defaults(status):
    """하단 상태줄 위젯에 Claude 상태 속성을 안전한 기본값으로 설치한다(코어
    StatusBar.__init__ 에서 빼낸 필드들). client_statusbar_init 훅이 위젯 생성 직후
    호출 → absorb/render_segs 가 의존하는 속성이 항상 존재한다. 디렉토리를 지우면 이
    설치가 사라지지만 흡수/렌더 훅도 함께 사라지고 코어 _render_main 은 이 속성을 읽지
    않아 안전하다(delete-to-disable)."""
    status.claude_active = False   # 활성 패널이 Claude 패널인가(좌하단 토큰 표기 게이트)
    status.claude_usage = None     # 활성 Claude 패널의 토큰/컨텍스트(best-effort)
    status.claude_tokens = 0       # 활성 계정 누적 토큰(§10 계정별 합계, 지속표시)
    status.claude_account = None   # 누적 토큰의 귀속 계정(표시에 곁들임)
    status.tok5h_pct = None        # M18-B: 5시간 한도 근접도 %(분모 미상이면 None)
    status.claude_warn = None      # M17: 장기턴/반복루프 경고(grade0, 없으면 None)
    status.claude_model = None     # M14c: 활성 Claude 모델 배지(opus-4.8 등)
    status.usage_limits = None     # M19: 그림자 /usage 세션·주간 한도 dict
    status.usage_age_sec = None    # S6 T3: 실측 경과(초) — stale 표기용
    # 토큰 절감 설정(설정 팝업 토글 현재값 + 예산 경고).
    status.auto_doc_clear = False
    status.auto_compact = False
    status.auto_hardstop = True    # 서버 기본 ON(하드스톱 자동복구) 과 일치
    status.claude_auto_mode = False
    status.claude_ctx_autoclear = False
    status.claude_ctx_threshold = 15
    status.claude_ctx_action = "compact"
    status.claude_ctx_min_interval = 120
    status.claude_long_turn_sec = 600  # M17: 장기 턴 경고 임계(초, 0=끔)
    status.claude_repeat_alert = 3     # M17: 반복 루프 경고 임계(회, 0=끔)
    status.claude_budget_plan = False
    # S6 T4 실측 한도 게이트 임계(%) — 서버 기본(세션 95 ON·주간 끔)과 일치.
    status.usage_gate_session_pct = 95
    status.usage_gate_week_pct = 0
    # 한도 경고 레벨(0/80/100) — §7-4 이후 실측 게이트 기반(키 이름은 유지).
    status.budget_level = 0
    status.claude_pending = None   # 무장된 자동 액션 {kind, eta초}(M14 카운트다운)


def absorb(status, msg):
    """status 메시지의 Claude 필드를 status 위젯에 in-place 흡수(코어 update_status 의
    Claude 블록 이전). 지속표시(usage/tokens/account/model)는 빈 값이 와도 마지막
    비-빈 값을 유지하고, 정적 옵션은 키 부재 시 직전값 보존(C4)."""
    # 활성 패널이 Claude 패널인지(권위값, 매 status). 값은 아래처럼 지속표시하되, 렌더는
    # 이 플래그로 게이트해 Claude 가 아닌 탭/패널에선 좌하단 토큰 표기를 숨긴다.
    status.claude_active = msg.get("claude_active", False)
    # §10 지속표시: usage/tokens/account 가 비어 와도 마지막 비-빈 값을 유지한다.
    cu = msg.get("claude_usage")
    if cu:
        status.claude_usage = cu
    ct = msg.get("claude_tokens", 0)
    if ct:
        status.claude_tokens = ct
    ca = msg.get("claude_account")
    if ca:
        status.claude_account = ca
    # M18-B: 5시간 한도 근접도 %(분모 미상이면 None — 표시 생략). 100 으로 클램프해
    # 과거 "999%/5h" 버그를 막는다(실측 세션% 경로는 0~100 이라 영향 없음).
    t5 = msg.get("tok5h_pct")
    status.tok5h_pct = min(100, t5) if isinstance(t5, int) else t5
    status.claude_warn = msg.get("claude_warn")   # M17 grade0 경고(권위값)
    cm = msg.get("claude_model")                  # M14c 모델 배지(지속표시)
    if cm:
        status.claude_model = cm
    if "usage_limits" in msg:                     # M19 그림자 /usage 결과(권위값)
        status.usage_limits = msg.get("usage_limits")
    if "usage_age_sec" in msg:                    # S6 T3: 실측 경과(stale 표기)
        status.usage_age_sec = msg.get("usage_age_sec")
    # 토큰 절감 설정(설정 팝업이 현재값으로 토글을 그리는 데 씀). 항상 권위값 반영.
    status.auto_doc_clear = msg.get("auto_doc_clear", status.auto_doc_clear)
    status.auto_compact = msg.get("auto_compact", status.auto_compact)
    status.auto_hardstop = msg.get("auto_hardstop", status.auto_hardstop)
    status.claude_auto_mode = msg.get("claude_auto_mode", status.claude_auto_mode)
    status.claude_ctx_autoclear = msg.get(
        "claude_ctx_autoclear", status.claude_ctx_autoclear)
    status.claude_ctx_threshold = msg.get(
        "claude_ctx_threshold", status.claude_ctx_threshold)
    status.claude_ctx_action = msg.get(
        "claude_ctx_action", status.claude_ctx_action)
    status.claude_ctx_min_interval = msg.get(
        "claude_ctx_min_interval", status.claude_ctx_min_interval)
    status.claude_long_turn_sec = msg.get(
        "claude_long_turn_sec", status.claude_long_turn_sec)
    status.claude_repeat_alert = msg.get(
        "claude_repeat_alert", status.claude_repeat_alert)
    status.claude_budget_plan = msg.get(
        "claude_budget_plan", status.claude_budget_plan)
    status.usage_gate_session_pct = msg.get(            # S6 T4 실측 게이트 임계
        "usage_gate_session_pct", status.usage_gate_session_pct)
    status.usage_gate_week_pct = msg.get(
        "usage_gate_week_pct", status.usage_gate_week_pct)
    status.budget_level = msg.get("budget_level", 0)
    # M14 카운트다운: 서버가 매 status 에 항상 키를 실어 보낸다(없으면 None).
    status.claude_pending = msg.get("claude_pending")


def render_segs(status, segs, w):
    """하단 상태줄 좌측에 Claude 세그먼트를 append 하고 클릭존을 status 에 채운다(코어
    _render_main 의 Claude 블록 이전). segs 는 REC 까지 누적된 상태로 들어오고, 여기서
    이어 그린 뒤 코어가 NEST/윈도우 목록을 계속 붙인다. 클릭존은 누적 폭으로 계산한다."""
    from rich.segment import Segment
    from rich.style import Style
    from pytmuxlib.clientutil import _char_cells, _fmt_tokens, theme_color
    tc = lambda n: theme_color(status, n)  # noqa: E731
    # 활성 Claude 패널: 모델(M14c) + 컨텍스트 사용량(best-effort) + 세션 누적(#3, Σ).
    uparts = []
    if status.claude_active:
        # 모델 배지는 좁은 폭에선 생략(자리 절약). claude_usage 가 있을 때만.
        if status.claude_model and status.claude_usage and w >= 60:
            uparts.append(status.claude_model)
        if status.claude_usage:
            uparts.append(status.claude_usage)
        # S6 T3 표시 1차화: 실측 세션(5h) % 가 **주 표시** — 추정 누계(~Σ)보다 앞.
        # 실측 없으면 생략(지어내지 않음 — 분모 근사 폐기로 이 값은 항상 /usage 실측).
        if status.tok5h_pct is not None:
            uparts.append(f"{status.tok5h_pct}%/5h")
        if status.claude_tokens:
            # 기호와 숫자 사이 한 칸 띄움(§10). 계정이 있으면 @계정 곁들임. 폭이
            # 넉넉하면(≥80칸) 약어(6.3M) 대신 세 자리 콤마 전체 숫자로(#30).
            # ~ 접두사(S6 T3): 스크랩 누계는 패널별 활동량 **추정**이지 과금 실측이
            # 아니다 — 실측(5h%)과 한 줄에 섞이므로 라벨로 구분한다(원칙 2).
            num = (f"{status.claude_tokens:,}" if w >= 80
                   else _fmt_tokens(status.claude_tokens))
            tk = "~Σ " + num
            if status.claude_account:
                tk += " @" + status.claude_account
            uparts.append(tk)
    if uparts:
        sec = Style(color="white", bgcolor=tc("secondary"), bold=True)
        hi = Style(color="black", bgcolor=tc("warning"), bold=True)
        fb = status.focus_btn
        ux0 = sum(sum(_char_cells(c) for c in s.text) for s in segs)
        # 모델 배지(첫 upart)와 나머지(사용량·Σ)를 **분리 세그먼트**로 그려, 각각
        # 클릭존·esc 포커스 강조가 가능하게 한다(요청). 모델 클릭=모델 팝업,
        # 나머지 클릭=토큰 로그.
        has_model = uparts[0] == status.claude_model
        rest = uparts[1:] if has_model else uparts
        segs.append(Segment(" ", sec))
        x = ux0 + 1
        if has_model:
            mw = sum(_char_cells(c) for c in uparts[0])
            status._model_zone = (x, x + mw)
            segs.append(Segment(uparts[0], hi if fb == "model" else sec))
            x += mw
            if rest:
                segs.append(Segment(" · ", sec))
                x += 3
        if rest:
            rtext = " · ".join(rest)
            rw = sum(_char_cells(c) for c in rtext)
            segs.append(Segment(rtext, hi if fb == "usage" else sec))
            x += rw
        segs.append(Segment(" ", sec))
        x += 1
        status._usage_zone = (ux0, x)
    # 실측 한도 경고(알림만 — 동작 변경 없음, §7-4 이후 게이트 임계 기반).
    # 임계 도달=빨강 ⚠, 임계의 80% 도달=노랑 ⚠.
    if status.budget_level >= 80:
        over = status.budget_level >= 100
        # ⚠ 뒤에 공백: 이모지(⚠ U+26A0)가 터미널에선 2칸으로 그려지는데 wcwidth 는
        # 1칸이라, 바로 뒤 글자가 둘째 칸에 겹쳐 그려졌다(요청) → 공백으로 흡수.
        segs.append(Segment(" ⚠ 한도 " + ("도달 " if over else "근접 "),
                            Style(color="white",
                                  bgcolor=("red" if over else "yellow"),
                                  bold=True)))
    # M14 카운트다운 배지: 무장된 자동 액션의 종류 + 남은 초(비가역 동작 발견성).
    if isinstance(status.claude_pending, dict):
        kind = status.claude_pending.get("kind")
        eta = status.claude_pending.get("eta", 0)
        label = "자동재개" if kind == "resume" else "자동정리"
        segs.append(Segment(f" ⏳ {label} {eta}s(입력=취소) ",  # ⏳ 뒤 공백(겹침 방지)
                            Style(color="black", bgcolor=tc("warning"),
                                  bold=True)))
    # M17(T7): 장기턴/반복루프('폭주 가능') 경고 배지(grade0 — 알림만, 개입 없음).
    if status.claude_warn:
        segs.append(Segment(f" ⚠ {status.claude_warn} ",  # ⚠ 뒤 공백(글자 겹침 방지)
                            Style(color="white", bgcolor=tc("error"),
                                  bold=True)))
