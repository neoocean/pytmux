"""claude-code 하단 상태줄(StatusBar) 세그먼트/흡수(Phase 2c).

코어 clientwidgets.StatusBar 의 `update_status` Claude 흡수 블록과 `_render_main` 의
Claude 세그먼트 렌더(모델 배지·컨텍스트·토큰Σ·예산경고·카운트다운·폭주경고)를 이리로
이전했다. 코어는 `plugins.client_statusbar_update`·`plugins.client_statusbar` 훅으로만
닿고, status 위젯의 claude_* 속성(코어 __init__ 의 안전한 기본값)을 읽고 쓴다 →
디렉토리를 지우면 흡수·렌더가 사라져 상태줄 Claude 세그먼트가 전혀 안 나타난다.

무게: rich.Segment/Style 과 clientutil 헬퍼만 쓴다(textual 클래스 import 안 함)."""
from __future__ import annotations

from pytmuxlib import i18n

# §6 ⑤ 플러그인 i18n: claude-code 가 자기 표면 문자열을 코어 레지스트리에 등록한다
# (delete-to-disable — 플러그인을 지우면 이 모듈이 import 안 돼 키도 사라진다). 키는
# "claude.*" 네임스페이스. 모듈 import 시점(플러그인 활성)에 1회 병합한다.
i18n.register({
    "ko": {
        "claude.limit_reached": " ⚠ 한도 도달 ",
        "claude.limit_near": " ⚠ 한도 근접 ",
        "claude.auto_resume": "자동재개",
        "claude.auto_cleanup": "자동정리",
        "claude.countdown": " ⏳ {label} {eta}s(입력=취소) ",
        "claude.limit_used": "{pct}%/5h 사용",
        "claude.limit_week_sonnet": "{pct}%/주(Sonnet)",
        "claude.limit_unknown": "?%/5h 사용",
        # M17 경고 배지(상태줄 ⚠) — 서버가 보낸 kind 로 클라가 로케일별 렌더. 장기 턴은
        # 언어중립('⚠ M:SS')이라 서버 문자열을 그대로 쓴다(배지 키 없음).
        "claude.warn_repeat_badge": "⚠ 동일 결과 {n}회 반복 — 루프 의심",
        "claude.warn_fmt_badge": "⚠ Claude 포맷 미인식 — 추적 중단(버전 업데이트?)",
    },
    "en": {
        "claude.limit_reached": " ⚠ Limit reached ",
        "claude.limit_near": " ⚠ Limit near ",
        "claude.auto_resume": "auto-resume",
        "claude.auto_cleanup": "auto-cleanup",
        "claude.countdown": " ⏳ {label} {eta}s (input=cancel) ",
        "claude.limit_used": "{pct}%/5h used",
        "claude.limit_week_sonnet": "{pct}%/wk(Sonnet)",
        "claude.limit_unknown": "?%/5h used",
        "claude.warn_repeat_badge": "⚠ Same output repeated {n}× — loop suspected",
        "claude.warn_fmt_badge":
            "⚠ Claude format unrecognized — tracking paused (version update?)",
    },
})


def init_defaults(status):
    """하단 상태줄 위젯에 Claude 상태 속성을 안전한 기본값으로 설치한다(코어
    StatusBar.__init__ 에서 빼낸 필드들). client_statusbar_init 훅이 위젯 생성 직후
    호출 → absorb/render_segs 가 의존하는 속성이 항상 존재한다. 디렉토리를 지우면 이
    설치가 사라지지만 흡수/렌더 훅도 함께 사라지고 코어 _render_main 은 이 속성을 읽지
    않아 안전하다(delete-to-disable)."""
    status.claude_active = False   # 활성 패널이 Claude 패널인가(좌하단 토큰 표기 게이트)
    status.claude_usage = None     # 활성 Claude 패널의 토큰/컨텍스트(best-effort)
    status._last_active_pane = None  # 직전 status 의 활성 패널 id — 바뀌면 per-pane
                                     # 지속표시(ctx)를 새 패널 값으로 교체한다(아래 absorb)
    status.claude_tokens = 0       # 활성 계정 누적 토큰(§10 계정별 합계, 지속표시)
    status.claude_account = None   # 패널 스크랩 계정(서버 호환 — 더는 표시 안 함)
    status.claude_account_full = None  # 비별칭 전체 계정(서버 호환 — 더는 표시 안 함)
    status.tok5h_pct = None        # M18-B: 5시간 한도 근접도 %(분모 미상이면 None)
    status.week_sonnet_pct = None  # 활성 모델=Sonnet 일 때 주간(Sonnet only) % — 5h
                                   # 통합값(모델별 측정 불가) 대신 표시(2026-06-16 요청)
    status.claude_warn = None      # M17: 장기턴/반복루프 경고(grade0, 없으면 None)
    status.claude_warn_kind = None # M17 경고 종류(None|long_turn|repeat|fmt_unknown)
    status.claude_warn_n = None    # 반복 종류일 때 반복 횟수(로케일별 배지 렌더용)
    status.claude_model = None     # M14c: 활성 Claude 모델 배지(opus-4.8 등)
    status.claude_model_tip = None # M14c: 모델 과선택 힌트 배지(알림만, 없으면 None)
    status.claude_model_hint = False  # M14c 힌트 토글(설정 팝업 표시용·기본 OFF)
    status.usage_limits = None     # M19: 그림자 /usage 세션·주간 한도 dict
    status.usage_age_sec = None    # S6 T3: 실측 경과(초) — stale 표기용
    # 토큰 절감 설정(설정 팝업 토글 현재값 + 예산 경고).
    status.auto_doc_clear = False
    status.auto_compact = False
    status.auto_hardstop = False   # 서버 기본 OFF(2026-06-18 사용자 요청) 과 일치
    status.auto_token_on_exit = True  # §10-F 세션 종료 시 토큰 화면 자동 표시(서버 기본 ON)
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
    # 활성 패널 전환 감지: status 는 매번 활성 패널 id(active_pane)를 싣는다(serverio
    # _status_msg). 바뀌었으면 **per-pane 지속표시(ctx)를 새 패널 값으로 교체**한다 —
    # 빈 값이라도. 안 그러면 새로 포커스한 Claude 패널의 ctx 가 아직 안 잡힌 동안(스캔
    # 전·컨텍스트 배지 미표시) 이전 패널의 'ctx:~N%/…' 가 그대로 남아, 서로 다른 Claude
    # 세션 패널을 옮겨 다녀도 같은 컨텍스트가 붙어 보였다(사용자 보고 2026-06-17).
    pane_changed = ("active_pane" in msg
                    and msg.get("active_pane") != status._last_active_pane)
    if "active_pane" in msg:
        status._last_active_pane = msg.get("active_pane")
    # §10 지속표시: usage 가 비어 와도 마지막 비-빈 값을 유지한다 — 단 **같은 패널일
    # 때만**. 패널이 바뀌면 위 사유로 새 패널 값(None 이면 None)으로 교체해 stale 를 끊는다.
    cu = msg.get("claude_usage")
    if cu or pane_changed:
        status.claude_usage = cu
    ct = msg.get("claude_tokens", 0)
    if ct:
        status.claude_tokens = ct
    ca = msg.get("claude_account")
    if ca:
        status.claude_account = ca
    # 전체 계정(footer 폭 충분 시 표시). 별칭과 함께 와도 빈 값이면 직전값 유지.
    caf = msg.get("claude_account_full")
    if caf:
        status.claude_account_full = caf
    # M18-B: 5시간 한도 근접도 %(분모 미상이면 None — 표시 생략). 100 으로 클램프해
    # 과거 "999%/5h" 버그를 막는다(실측 세션% 경로는 0~100 이라 영향 없음).
    t5 = msg.get("tok5h_pct")
    status.tok5h_pct = min(100, t5) if isinstance(t5, int) else t5
    ws = msg.get("week_sonnet_pct")
    status.week_sonnet_pct = min(100, ws) if isinstance(ws, int) else ws
    status.claude_warn = msg.get("claude_warn")   # M17 grade0 경고(권위값)
    status.claude_warn_kind = msg.get("claude_warn_kind")  # 종류(로케일별 렌더)
    status.claude_warn_n = msg.get("claude_warn_n")        # 반복 횟수(반복 종류)
    status.claude_model_tip = msg.get("claude_model_tip")  # M14c 힌트(권위값, 매 status)
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
    status.auto_token_on_exit = msg.get("auto_token_on_exit",
                                        status.auto_token_on_exit)
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
    status.claude_model_hint = msg.get(            # M14c 힌트 토글(full 시만 도달)
        "claude_model_hint", status.claude_model_hint)
    status.usage_gate_session_pct = msg.get(            # S6 T4 실측 게이트 임계
        "usage_gate_session_pct", status.usage_gate_session_pct)
    status.usage_gate_week_pct = msg.get(
        "usage_gate_week_pct", status.usage_gate_week_pct)
    status.budget_level = msg.get("budget_level", 0)
    # M14 카운트다운: 서버가 매 status 에 항상 키를 실어 보낸다(없으면 None).
    status.claude_pending = msg.get("claude_pending")


def _trailing_cells(status, _char_cells) -> int:
    """render_segs 이후 코어 _render_main 이 우측에 덧붙일 폭(NEST + 윈도우목록 +
    host/시각/날짜) 추정. 계정명을 **전체로 보일 폭이 되는지** 판단에만 쓴다 —
    clientwidgets._render_main 의 해당 구간(NEST·windows·right_parts)과 같은 계산이다
    (drift 나도 표시 결정만 보수적으로 틀어질 뿐 동작엔 영향 없음). 계정이 이메일이라
    전체/별칭 갈림이 있을 때만 호출돼 비용도 미미하다."""
    char = lambda s: sum(_char_cells(c) for c in s)  # noqa: E731
    n = 0
    if getattr(status, "prefix_off", False):
        n += char("NEST ")
    if not getattr(status, "hide_tabs", False):
        for win in (getattr(status, "windows", None) or []):
            flag = "!" if win.get("bell") else ("#" if win.get("activity") else "")
            n += char("%d:%s%s " % (win["index"] + 1, win["name"], flag))
    try:
        for kind, text in status._expand_parts(status.right_fmt):
            if kind == "host" and getattr(status, "_is_remote", False):
                text = "ssh:" + text
            n += char(text)
    except Exception:
        pass
    return n


def render_segs(status, segs, w, w0=None):
    """하단 상태줄 좌측에 Claude 세그먼트를 append 하고 클릭존을 status 에 채운다(코어
    _render_main 의 Claude 블록 이전). segs 는 REC 까지 누적된 상태로 들어오고, 여기서
    이어 그린 뒤 코어가 NEST/윈도우 목록을 계속 붙인다. 클릭존은 누적 폭으로 계산한다.

    w0(P6) = 들어오는 segs 의 누적 셀폭. 코어가 넘겨주면 ux0/left 를 segs 전수합산으로
    다시 구하지 않는다(없으면 None → 하위호환 전수합산). 자기 세그먼트를 그린 뒤의 **새
    누적 셀폭**을 반환해 코어가 재순회 없이 이어 쓰게 한다."""
    from rich.segment import Segment
    from rich.style import Style
    from pytmuxlib.clientutil import _char_cells, theme_color
    tc = lambda n: theme_color(status, n)  # noqa: E731
    _cw = lambda t: sum(_char_cells(c) for c in t)  # noqa: E731
    # w0 미지정(직접 호출)이면 기존처럼 전수합산으로 폭을 구한다(하위호환).
    acc = w0 if w0 is not None else sum(_cw(s.text) for s in segs)
    # 활성 Claude 패널: 모델(M14c) + 컨텍스트 사용량(best-effort) + 세션 누적(#3, Σ).
    uparts = []
    if status.claude_active:
        # 모델 배지는 좁은 폭에선 생략(자리 절약). claude_usage 가 있을 때만.
        if status.claude_model and status.claude_usage and w >= 60:
            uparts.append(status.claude_model)
        # 좌하단 표기(사용자 요청 2026-06-11): 하이라이트 패널의 계정 기준으로 ①현재
        # 패널 세션의 컨텍스트 비율% ②5시간 리밋까지 남은 비율%만 보인다. **토큰 수치는
        # 직접 표시하지 않는다**(누계 ~Σ 제거). 기록(계정·시간·토큰 단위)은 서버측
        # _log_tokens 가 그대로 유지 — 이건 표시만 바꾼 것이고 클릭하면 토큰 로그가 열린다.
        usage_parts = []
        # ① 컨텍스트 비율%: claude_usage 가 'ctx…' 일 때만(토큰 폴백 'Xk tok' 은
        #    토큰 수치라 표시 안 함). best-effort 라 없으면 생략.
        cu = status.claude_usage
        if isinstance(cu, str) and cu.startswith("ctx"):
            usage_parts.append(cu)
        # ② 5시간 리밋 **사용률**%(실측만 — 지어내지 않음; 분모 근사 폐기로 이 값은
        #    항상 /usage 실측). 2026-06-12 사용자 결정: 잔여("N%/5h 남음")가 아니라
        #    사용률로 — 토큰 팝업/usage-panel 막대("N% 사용")·Claude /usage 원문
        #    ("N% used")과 같은 방향·같은 숫자가 모든 표면에 보이게 통일한다(잔여
        #    표기와 섞이면 같은 실측이 다른 값처럼 읽혔다 — 사용자 보고 2회).
        #    활성 모델이 Sonnet 이면 서버가 5h(통합) 대신 주간 Sonnet only % 를
        #    보낸다(tok5h_pct=None, week_sonnet_pct=값) — Anthropic 이 5h 를 모델
        #    통합으로만 줘 모델별 측정이 불가하기 때문(2026-06-16 사용자 결정).
        #    둘은 상호배타(서버가 한쪽만 채움).
        if status.tok5h_pct is not None:
            usage_parts.append(
                i18n.t("claude.limit_used",
                       pct=max(0, min(100, int(status.tok5h_pct)))))
        elif status.week_sonnet_pct is not None:
            usage_parts.append(
                i18n.t("claude.limit_week_sonnet",
                       pct=max(0, min(100, int(status.week_sonnet_pct)))))
        else:
            # 실측(/usage 5h·주간 Sonnet) 미도착 — Claude 를 막 시작해 아직 그림자
            # /usage 가 없으면 위 두 분기가 비어 사용량 배지가 **아예 안 보였다**.
            # 활성 Claude 패널이면 값 미상이라도 즉시 'Unknown' 배지(`?%/5h used`)를
            # 띄우고, 실측이 도착하면 위 분기로 그 자리에서 숫자 갱신된다(요청
            # 2026-06-18). 이 분기는 `if status.claude_active:` 안이라 비-Claude
            # 패널엔 안 뜬다. 계정 라벨은 아래 claude_account 분기가 붙인다(미상이면
            # 생략) — 실측 계정(usage_limits)은 아직 없으니 is_usage_last 는 False.
            usage_parts.append(i18n.t("claude.limit_unknown"))
        # 계정 라벨은 더는 붙이지 않는다 — Claude 토큰 사용량은 계정과 무관하게
        # **현재 로컬 머신** 기준으로 표시한다(계정별 집계/표기 제거, 2026-06-19 결정).
        uparts.extend(usage_parts)
    if uparts:
        sec = Style(color="white", bgcolor=tc("secondary"), bold=True)
        hi = Style(color="black", bgcolor=tc("warning"), bold=True)
        fb = status.focus_btn
        ux0 = acc   # P6: 들어오는 누적폭(=이 시점 segs 전수합산) 재사용
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
        acc = x   # P6: uparts 블록이 append 한 폭만큼 누적 전진(x 가 정확히 추적)
    # 실측 한도 경고(알림만 — 동작 변경 없음, §7-4 이후 게이트 임계 기반).
    # 임계 도달=빨강 ⚠, 임계의 80% 도달=노랑 ⚠.
    if status.budget_level >= 80:
        over = status.budget_level >= 100
        # 텍스트 색은 배경 대비로: 노랑(근접) 배경엔 흰 글자가 묻혀 안 보이므로
        # 검은 글자(아래 ⏳ 카운트다운 배지와 동일 패턴), 빨강(도달) 배경엔 흰 글자.
        _bt = (i18n.t("claude.limit_reached") if over
               else i18n.t("claude.limit_near"))
        # ⚠(U+26A0) 표시 보정(M17 경고 배지와 동일): 터미널선 ⚠ 가 2칸 컬러 이모지라
        # wcwidth=1 인데도 바로 뒤 단일 공백이 둘째 칸에 흡수돼 "⚠Limit near" 처럼
        # 붙어 보였다(사용자 보고 2026-06-19). **표시용으로만** ⚠ 뒤 공백을 한 칸 더
        # 넣어 띄운다 — i18n 카탈로그는 자연스러운 한 칸 유지, 클릭존 폭은 표시 문자열
        # 기준 _cw 로 일관(근접 노랑·도달 빨강 둘 다 적용).
        _bt = _bt.replace("⚠ ", "⚠  ", 1)
        segs.append(Segment(_bt,
                            Style(color=("white" if over else "black"),
                                  bgcolor=("red" if over else "yellow"),
                                  bold=True)))
        # 한도 경고 배지 클릭존(요청): 클릭/터치 시 사용량+리셋 카운트다운 팝업
        # (usage-view, claude-token-usage-view 플러그인)을 연다. 토큰Σ 클릭(_usage_zone)
        # 이 여는 영속 통계 로그와 달리, 이쪽은 "지금 한도까지 얼마/리셋까지 몇 초" 뷰다.
        status._limit_zone = (acc, acc + _cw(_bt))
        acc += _cw(_bt)
    # M14 카운트다운 배지: 무장된 자동 액션의 종류 + 남은 초(비가역 동작 발견성).
    if isinstance(status.claude_pending, dict):
        kind = status.claude_pending.get("kind")
        eta = status.claude_pending.get("eta", 0)
        label = (i18n.t("claude.auto_resume") if kind == "resume"
                 else i18n.t("claude.auto_cleanup"))
        _ct = i18n.t("claude.countdown", label=label, eta=eta)
        segs.append(Segment(_ct,
                            Style(color="black", bgcolor=tc("warning"),
                                  bold=True)))
        acc += _cw(_ct)
    # M17(T7): 장기턴/반복루프 경고 배지(grade0 — 알림만, 개입 없음). 아이콘은 warn
    # 문자열이 직접 포함한다(장기턴 ⚠ 분:초 / 그 외 ⚠ …).
    # ⚠(U+26A0)는 wcwidth=1 이지만 터미널에선 컬러 이모지(2칸)로 그려진다 — 그래서 ⚠
    # 바로 뒤 한 칸은 이모지의 둘째 칸에 흡수돼, "⚠ 10:25" 가 화면엔 "⚠️10:25" 처럼 붙어
    # 보였다(사용자 보고 2026-06-17; 2026-06-12 의 단일 공백은 글자 겹침만 막고 가시
    # 간격은 못 줬다). **표시용으로만** ⚠ 뒤에 공백을 한 칸 더 넣어 "⚠️ 10:25" 로 띄운다
    # — 저장값(claude_warn)·경고 info 팝업·파서는 원문("⚠ ...")을 그대로 쓴다(테스트/
    # 종류 판정 불변). 클릭존 폭은 표시 문자열 기준 _cw 로 맞춘다.
    if status.claude_warn:
        # 종류(kind)별 로케일 배지: 반복/포맷-미인식은 i18n(ko/en), 장기 턴은 언어중립
        # ('⚠ M:SS') 서버 문자열 그대로. kind 미상(구버전 서버)이면 서버 문자열 폴백
        # (한글일 수 있으나 호환 유지) — i18n 전수조사 2026-06-19.
        _wkind = status.claude_warn_kind
        if _wkind == "repeat":
            _disp = i18n.t("claude.warn_repeat_badge", n=status.claude_warn_n or 0)
        elif _wkind == "fmt_unknown":
            _disp = i18n.t("claude.warn_fmt_badge")
        else:
            _disp = status.claude_warn
        if _disp.startswith("⚠ "):
            _disp = "⚠  " + _disp[2:]
        _wt = f" {_disp} "
        segs.append(Segment(_wt,
                            Style(color="white", bgcolor=tc("error"),
                                  bold=True)))
        # 경고 배지 클릭존(요청): 클릭/터치 시 상황 설명 + 할일 팝업을 연다
        # (포맷 미인식 / 장기 턴 / 반복 루프 종류별 안내).
        status._warn_zone = (acc, acc + _cw(_wt))
        acc += _cw(_wt)
    # M14c 모델 과선택 힌트 배지(알림만 — 개입 없음). 경고(error 빨강)와 구분되게
    # secondary 배경의 소프트 톤으로 그린다. 힌트 문자열은 💡(2칸) 뒤 공백을 직접
    # 포함해 다음 글자 겹침이 없다(claude_warn 의 이모지 패턴과 동일).
    if status.claude_model_tip:
        _tt = f" {status.claude_model_tip} "
        segs.append(Segment(_tt,
                            Style(color="white", bgcolor=tc("secondary"),
                                  bold=True)))
        acc += _cw(_tt)
    return acc   # P6: 새 누적 셀폭(코어가 NEST/윈도우 이어붙일 기준)
