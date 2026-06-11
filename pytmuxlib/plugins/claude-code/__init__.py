"""claude-code 플러그인 — Claude Code 관련 기능 모음.

이 디렉토리를 통째로 지우면 Claude Code 관련 명령/팝업이 명령 검색·자동완성·디스패치
어디에도 나타나지 않고 조용히 비활성화된다(코어는 plugins 레지스트리로만 호출).

**단계적 추출(staged)**: Claude Code 기능은 매우 방대하고 30Hz 스캔 루프·렌더·토큰
회계·영속에 깊게 얽혀 있어 여러 단계로 나눠 옮긴다. 이 모듈은 그 첫 단계로 **명령 전용
팝업**(시작 규칙 편집 `claude-rules`, 토큰 절감 설정 `token-saver`)과 그 메타데이터·
디스패치를 담는다. 나머지(헤더 렌더·상태줄 토큰·서버 스캔 등)는 후속 단계에서 이리로
옮긴다.

무게: 이 모듈은 textual 을 import 하지 않는다(서버 프로세스도 plugins.load() 로 읽음).
화면(screens.py)은 실제로 열 때 지연 import 한다."""
from __future__ import annotations

import time as _time

from pytmuxlib import i18n

# ---- 명령 메타데이터(코어 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐짐) ----
COMMANDS = [
    ("claude-rules", "Claude 시작 규칙 편집(저장 시 새 세션/clear 후 프롬프트에 "
                     "자동 주입)", "Claude"),
    ("token-saver", "토큰 절감 설정 팝업 — 각 자동 개입 토글·잔량 임계·실측 게이트"
                    "(별칭 claude-settings, token-settings)", "Claude"),
    ("auto-resume", "토큰 리밋 자동 재개 [on|off]", "Claude"),
    ("auto-resume-message", "자동 재개 메시지 설정", "Claude"),
    ("claude-header", "Claude 프롬프트 헤더 표시 on/off (claude-header on|off|toggle)",
                      "Claude"),
    ("prompt-history", "Claude 프롬프트 히스토리 팝업(헤더 클릭으로도 열림)", "Claude"),
    ("token-usage", "Claude 실행 중 탭/패널 + 토큰 사용량 트리(상태줄 사용량 클릭)",
                    "Claude"),
    ("token-log", "토큰 사용량 영속 로그 집계 팝업(시간/일/월 × 계정 — h/d/m·a 전환)",
                  "Claude"),
    ("claude-usage", "그림자 /usage 질의 — 숨은 세션으로 실 세션/주간 한도 갱신"
                     "(별칭 usage)", "Claude"),
    ("usage-panel", "Claude 토큰 사용 한도 팝업 — /usage(세션 5h·주 전체·주 Sonnet) "
                    "막대 그래프(별칭 usage-limits·limits)", "Claude"),
    ("token-account", "활성 패널 Claude 계정 수동 지정 (token-account <이름>, "
                      "빈값=자동)", "Claude"),
    ("prompt-clear", "프롬프트 단위 클리어 모드 토글(완료마다 문서화+/clear) [on|off]",
                     "Claude"),
    ("prompt-clear-message", "프롬프트 단위 클리어의 문서화 지시문 변경", "Claude"),
    ("prompt-clear-queue", "프롬프트 단위 클리어 큐에 명령 쌓기(빈값=목록, -c=비움)",
                           "Claude"),
    ("model", "모델·컨텍스트 변경 팝업(상태줄 모델 배지 클릭으로도 열림, /model 주입; "
              "별칭 model-config, claude-model)", "Claude"),
    ("auto-doc-clear", "Claude idle 30초 지속 시 자동 문서화+/clear on/off "
                       "(auto-doc-clear on|off|toggle)", "Claude"),
    ("auto-compact", "Claude idle 30초 지속 시 자동 /compact on/off "
                     "(auto-compact on|off|toggle)", "Claude"),
    ("auto-hardstop", "컨텍스트 하드스톱('Context limit reached') 시 즉시 자동 "
                      "/compact on/off (auto-hardstop on|off|toggle, 기본 on)",
                      "Claude"),
    ("claude-auto-mode", "Claude idle 시 권한모드를 자동으로 오토모드로 전환 on/off "
                         "(claude-auto-mode on|off|toggle)", "Claude"),
    ("auto-launch", "새 Claude 세션 시작 시 /rc(원격 제어)+권한모드 auto 1회 자동 적용 "
                    "on/off (auto-launch on|off|toggle, 기본 on)", "Claude"),
]
NOARG = {
    "claude-rules", "token-saver",
    "prompt-history", "token-usage", "token-log",
    "claude-usage", "usage", "usage-panel", "usage-limits", "limits",
}
# 옵션(선택지) 스키마 — 팔레트에서 on/off/토글을 키보드로 고른다(코어 COMMAND_OPTIONS 병합).
_ONOFF = [("토글", ""), ("켜기", "on"), ("끄기", "off")]
COMMAND_OPTIONS = {
    "auto-resume": [{"key": "state", "label": "자동재개", "choices": _ONOFF}],
    "prompt-clear": [{"key": "state", "label": "클리어모드", "choices": _ONOFF}],
    "auto-doc-clear": [{"key": "state", "label": "자동클리어", "choices": _ONOFF}],
    "auto-hardstop": [{"key": "state", "label": "하드스톱복구", "choices": _ONOFF}],
    "claude-auto-mode": [{"key": "state", "label": "오토모드", "choices": _ONOFF}],
    "auto-launch": [{"key": "state", "label": "자동셋업", "choices": _ONOFF}],
    "claude-header": [{"key": "state", "label": "헤더", "choices": _ONOFF}],
}

# §6 ⑤ 플러그인 명령 i18n: '?' 목록·힌트는 코어 CommandListScreen/_cmd_desc 가
# t("cmd.<name>", default=원본) 으로 번역한다. ko 는 위 COMMANDS 에서 자동 시드(원본=ko),
# en 만 보강. 플러그인 명령이라 코어 cmd.* 키와 이름이 겹치지 않는다(delete-to-disable:
# 디렉토리를 지우면 이 등록도 사라짐).
i18n.register({
    "ko": {f"cmd.{n}": d for n, d, *_ in COMMANDS},
    "en": {
        "cmd.claude-rules": "Edit Claude start rules (auto-injected into the prompt after a new session/clear)",
        "cmd.token-saver": "Token-saving settings popup — toggle each auto-intervention·remaining threshold·measured gate (alias claude-settings, token-settings)",
        "cmd.auto-resume": "Auto-resume on token limit [on|off]",
        "cmd.auto-resume-message": "Set the auto-resume message",
        "cmd.claude-header": "Show Claude prompt header on/off (claude-header on|off|toggle)",
        "cmd.prompt-history": "Claude prompt history popup (also opens by clicking the header)",
        "cmd.token-usage": "Tab/pane tree of running Claude + token usage (click status usage)",
        "cmd.token-log": "Persistent token-usage log aggregation popup (hour/day/month × account — h/d/m·a)",
        "cmd.claude-usage": "Shadow /usage query — refresh real session/weekly limits via a hidden session (alias usage)",
        "cmd.usage-panel": "Claude token usage-limit popup — /usage (session 5h·week all·week Sonnet) bar graph (alias usage-limits·limits)",
        "cmd.token-account": "Manually set the active pane's Claude account (token-account <name>, empty=auto)",
        "cmd.prompt-clear": "Toggle per-prompt clear mode (document + /clear each completion) [on|off]",
        "cmd.prompt-clear-message": "Change the per-prompt-clear documentation directive",
        "cmd.prompt-clear-queue": "Queue commands for per-prompt clear (empty=list, -c=clear)",
        "cmd.model": "Model·context change popup (also opens via the status model badge, injects /model; alias model-config, claude-model)",
        "cmd.auto-doc-clear": "Auto document + /clear when Claude idle 30s on/off (auto-doc-clear on|off|toggle)",
        "cmd.auto-compact": "Auto /compact when Claude idle 30s on/off (auto-compact on|off|toggle)",
        "cmd.auto-hardstop": "Auto /compact immediately on context hardstop ('Context limit reached') on/off (auto-hardstop on|off|toggle, default on)",
        "cmd.claude-auto-mode": "Auto-switch permission mode to auto when Claude idle on/off (claude-auto-mode on|off|toggle)",
        "cmd.auto-launch": "On new Claude session apply /rc (remote control)+permission auto once on/off (auto-launch on|off|toggle, default on)",
    },
})

# §6 ⑤ 플러그인 옵션 피커 라벨(키=원문 한국어, 코어 옵션 피커와 동일 방식). 선택지
# (토글/켜기/끄기)는 코어 _ONOFF 와 같은 원문이라 코어 카탈로그가 이미 번역한다.
i18n.register({
    "ko": {lab: lab for lab in
           (s["label"] for specs in COMMAND_OPTIONS.values() for s in specs)},
    "en": {
        "자동재개": "Auto-resume", "클리어모드": "Clear mode",
        "자동클리어": "Auto-clear", "하드스톱복구": "Hardstop recovery",
        "오토모드": "Auto mode", "자동셋업": "Auto setup", "헤더": "Header",
    },
})


def _onoff(args):
    """on/off 인자 → True/False, 없으면 None(서버가 토글). 기존 코어 디스패치와 동일."""
    if "on" in args:
        return True
    if "off" in args:
        return False
    return None

# 토큰 절감 설정 팝업(ClaudeSaverScreen)의 행/순환 프리셋. clientutil 에서 이리로 이전.
SAVER_ROWS = [
    ("autoresume", "토큰리밋 자동재개", "toggle"),
    ("usage_gate_session", "실측 세션한도 게이트(자동재개 보류 %)", "cycle"),
    ("usage_gate_week", "실측 주간한도 게이트(%)", "cycle"),
    ("budget_plan", "실측 한도 압박(게이트의 80%) 시 plan 모드 유도", "toggle"),
    ("ctx_autoclear", "컨텍스트 잔량 부족 시 자동 정리", "toggle"),
    ("ctx_action", "  └ 정리 방식", "cycle"),
    ("ctx_threshold", "  └ 잔량 임계", "cycle"),
    ("ctx_min_interval", "  └ 정리 빈도 상한", "cycle"),
    ("auto_doc_clear", "idle 지속 시 자동 문서화+/clear", "toggle"),
    ("auto_compact", "idle 지속 시 자동 /compact", "toggle"),
    ("auto_hardstop", "컨텍스트 하드스톱 시 즉시 자동 /compact", "toggle"),
    ("claude_auto_mode", "권한모드 자동 오토", "toggle"),
    ("prompt_clear", "프롬프트 단위 클리어(완료마다 doc+/clear)", "toggle"),
    ("long_turn", "장기 턴 경고(초)", "cycle"),
    ("repeat_alert", "반복 루프 경고(회)", "cycle"),
]
# cycle 행의 프리셋 값(Enter 마다 다음으로 순환). 0=끔.
SAVER_CYCLES = {
    # S6 T4 실측 게이트 임계(%): 0=끔. 세션 기본 95 — Enter 마다 다음으로 순환.
    "usage_gate_session": [0, 80, 90, 95, 98],
    "usage_gate_week": [0, 90, 95, 98],
    "ctx_action": ["compact", "doc-clear"],
    "ctx_threshold": [10, 15, 20, 25, 30],
    "ctx_min_interval": [0, 60, 120, 300, 600],
    "long_turn": [0, 300, 600, 900, 1800],
    "repeat_alert": [0, 2, 3, 5, 10],
}


def _cycle_next(key, cur):
    vals = SAVER_CYCLES[key]
    try:
        i = vals.index(cur)
    except ValueError:
        i = -1
    return vals[(i + 1) % len(vals)]


def saver_display(app, key):
    """설정 팝업의 한 행이 보일 현재값 문자열(토글 ●/○ 또는 cycle 값)."""
    st = app.status
    bools = {
        "autoresume": st.autoresume,
        "budget_plan": st.claude_budget_plan,
        "ctx_autoclear": st.claude_ctx_autoclear,
        "auto_doc_clear": st.auto_doc_clear,
        "auto_compact": st.auto_compact,
        "auto_hardstop": st.auto_hardstop,
        "claude_auto_mode": st.claude_auto_mode,
        "prompt_clear": st.prompt_clear,
    }
    if key in bools:
        return "●" if bools[key] else "○"
    if key == "ctx_action":
        return "/compact" if st.claude_ctx_action == "compact" else "doc+/clear"
    if key == "ctx_threshold":
        return f"잔량<{st.claude_ctx_threshold}%"
    if key == "ctx_min_interval":
        iv = int(st.claude_ctx_min_interval)
        return "상한 없음" if iv <= 0 else f"{iv}초마다 최대 1회"
    if key == "usage_gate_session":
        v = int(st.usage_gate_session_pct)
        return "끔" if v <= 0 else f"실측 ≥{v}%"
    if key == "usage_gate_week":
        v = int(st.usage_gate_week_pct)
        return "끔" if v <= 0 else f"실측 ≥{v}%"
    if key == "long_turn":
        v = int(st.claude_long_turn_sec)
        return "끔" if v <= 0 else f"{v}초 이상"
    if key == "repeat_alert":
        v = int(st.claude_repeat_alert)
        return "끔" if v <= 0 else f"{v}회 이상"
    return ""


def saver_action(app, key):
    """설정 팝업에서 한 행을 Enter — 동작을 서버로 보내고 status 를 낙관적으로 즉시
    반영한다(서버 broadcast 가 권위값으로 확정). 토글은 set_* 를 인자 없이(서버가
    반전), cycle 은 다음 프리셋 값을 보낸다."""
    st = app.status
    if key == "autoresume":
        app.send_cmd("set_autoresume")
        st.autoresume = not st.autoresume
    elif key == "budget_plan":
        app.send_cmd("set_claude_budget_plan")
        st.claude_budget_plan = not st.claude_budget_plan
    elif key == "ctx_autoclear":
        app.send_cmd("set_claude_ctx_autoclear")
        st.claude_ctx_autoclear = not st.claude_ctx_autoclear
    elif key == "auto_doc_clear":
        app.send_cmd("set_auto_doc_clear", value=None)
        st.auto_doc_clear = not st.auto_doc_clear
    elif key == "auto_compact":
        app.send_cmd("set_auto_compact", value=None)
        st.auto_compact = not st.auto_compact
    elif key == "auto_hardstop":
        app.send_cmd("set_auto_hardstop", value=None)
        st.auto_hardstop = not st.auto_hardstop
    elif key == "claude_auto_mode":
        app.send_cmd("set_claude_auto_mode", value=None)
        st.claude_auto_mode = not st.claude_auto_mode
    elif key == "prompt_clear":
        app.send_cmd("set_prompt_clear", value=None)
        st.prompt_clear = not st.prompt_clear
    elif key == "ctx_action":
        nxt = _cycle_next("ctx_action", st.claude_ctx_action)
        app.send_cmd("set_claude_ctx_action", value=nxt)
        st.claude_ctx_action = nxt
    elif key == "ctx_threshold":
        nxt = _cycle_next("ctx_threshold", st.claude_ctx_threshold)
        app.send_cmd("set_claude_ctx_threshold", value=nxt)
        st.claude_ctx_threshold = nxt
    elif key == "ctx_min_interval":
        nxt = _cycle_next("ctx_min_interval", int(st.claude_ctx_min_interval))
        app.send_cmd("set_claude_ctx_min_interval", value=nxt)
        st.claude_ctx_min_interval = nxt
    elif key == "usage_gate_session":
        nxt = _cycle_next("usage_gate_session", int(st.usage_gate_session_pct))
        app.send_cmd("set_usage_gate", session=nxt)
        st.usage_gate_session_pct = nxt
    elif key == "usage_gate_week":
        nxt = _cycle_next("usage_gate_week", int(st.usage_gate_week_pct))
        app.send_cmd("set_usage_gate", week=nxt)
        st.usage_gate_week_pct = nxt
    elif key == "long_turn":
        nxt = _cycle_next("long_turn", int(st.claude_long_turn_sec))
        app.send_cmd("set_claude_turn_warn", long_sec=nxt)
        st.claude_long_turn_sec = nxt
    elif key == "repeat_alert":
        nxt = _cycle_next("repeat_alert", int(st.claude_repeat_alert))
        app.send_cmd("set_claude_turn_warn", repeat=nxt)
        st.claude_repeat_alert = nxt


def _pane_claude_entry(p, full):
    """status 의 패널별 Claude 항목. history 는 드물게 바뀌는데 매 status(토큰 변동으로
    자주 발화)마다 30개 프롬프트를 재직렬화·전송하면 ssh 트래픽이 커진다(§4.5). 그래서
    **변할 때만** 싣는다: `full`(신규 attach·구조 변경 resync)이면 항상 실어 새 클라가
    비어 보이지 않게 하고, 주기 flush(full=False)는 직전 전송분(_hist_sent)과 다를 때만
    싣고 갱신한다. full 은 _hist_sent 를 건드리지 않는다 — 주기 스트림(모든 클라 공유)의
    추적을 오염시키면 다른 클라가 델타를 놓친다. (serverio 코어에서 이리로 이전.)"""
    e = {"id": p.id, "claude": p._claude, "prompt": p.last_prompt,
         "perm_mode": p._perm_mode, "bypass_ok": p._bypass_seen}
    h = p.prompt_history[-30:]
    if full or h != p._hist_sent:
        e["history"] = h
        if not full:
            p._hist_sent = h
    return e


# ---- Claude 팝업(클라) — Phase 2a 에서 코어 client.py 에서 이리로 이전 ----
# textual 화면은 실제로 열 때 지연 import(플러그인 __init__ 은 서버도 읽어 가벼워야 함).
def _open_model_config(app):
    """Claude 모델·컨텍스트 변경 팝업. 상태줄 모델 배지 클릭 / `model` 명령 / esc 모드
    상태바 포커스로 연다. 고른 값은 활성 패널에 '/model <이름>'(+컨텍스트)으로 주입한다."""
    from .screens import ModelCtxScreen
    cur = getattr(app.status, "claude_model", None)
    app.push_screen(ModelCtxScreen(cur), app._apply_model_config)


def _apply_model_config(app, res):
    if not res:
        return
    model, ctx = res
    arg = model if ctx in (None, "default") else f"{model} {ctx}"
    # '/model <이름>' + Enter 주입(사용자 확인). 짧은 슬래시 명령이라 한 번에.
    app.send_input(("/model " + arg + "\r").encode("utf-8"))
    app.display_message(f"/model {arg} 적용 요청")


def _open_perm_mode(app, pane_id):
    """Claude 권한모드 선택 팝업(하단 footer 클릭, §10 item 2). 현재 모드(서버 status
    perm_mode)를 표시하고, 고른 목표를 서버로 보내면 서버가 shift+tab 폐루프로 그
    모드까지 순환 주입한다."""
    from .screens import PermModeScreen
    info = app.pane_claude.get(pane_id) or {}
    current = info.get("perm_mode")
    # bypass 가 가용(시작 시 위험 플래그 활성)할 때만 팝업에 노출. 현재 모드가 이미
    # bypass 면 당연히 가용이므로 함께 친다(서버 관측이 한 프레임 늦어도 안전).
    bypass_ok = bool(info.get("bypass_ok")) or current == "bypass"
    zone = app._perm_zone.get(pane_id)
    anchor_y = zone[2] if zone else None   # 클릭한 footer 행 → 팝업 세로 위치 기준
    anchor_x = zone[0] if zone else None   # footer 시작 x → 팝업 좌측 정렬 기준(#2)
    # 클릭한 그 줄(auto mode on 등)은 팝업 dim 에서 제외해 밝게 둔다(#29).
    app._undim_rows = {anchor_y} if anchor_y is not None else set()

    def _chosen(target):
        app._undim_rows = set()            # 닫히면 dim 제외 해제
        if target:
            app.send_cmd("set_claude_perm_mode", id=pane_id, target=target)
            app.display_message(f"권한모드 → {target} 전환 중…")
    app.push_screen(PermModeScreen(current, anchor_y=anchor_y,
                                   anchor_x=anchor_x,
                                   bypass_available=bypass_ok), _chosen)


def _open_token_log(app):
    """토큰 사용량 영속 로그 집계 팝업(#7). 서버에 최근 로그를 요청하고, 응답
    (t==token_log)이 오면 handle_message 가 TokenLogScreen 으로 시간/일/주/월×계정
    집계를 띄운다. 상태바 Σ 클릭의 진입점이기도 하다."""
    app._want_token_log = True
    app.send_cmd("request_token_log", limit=5000)


def _on_token_log_msg(app, msg):
    """서버 token_log 회신 → TokenLogScreen 팝업(open_token_log 가 요청했을 때만)."""
    if not getattr(app, "_want_token_log", False):
        return
    app._want_token_log = False
    from .screens import TokenLogScreen
    app.push_screen(TokenLogScreen(
        msg.get("records") or [],
        usage=getattr(app.status, "usage_limits", None),
        total_all=msg.get("total_all"),
        accounts_total=msg.get("accounts_total"),
        reconcile=msg.get("reconcile")))


def _open_prompt_history(app, pane_id=None):
    """Claude 프롬프트 히스토리 팝업(시간순). 헤더 클릭/명령으로 연다(#7). [h] 로 이
    패널 헤더 숨김/표시 토글(#6 ②). InfoScreen(코어 공유)에 2칼럼(번호/본문)으로 싣고,
    pane_claude/_claude_hidden_panes/toggle_header_hidden(코어 렌더 상태)을 읽고 쓴다."""
    from pytmuxlib.clientscreens import InfoScreen
    pid = pane_id if pane_id is not None else app.layout.get("active")
    info = app.pane_claude.get(pid) or {}
    hist = info.get("history") or ([info["prompt"]] if info.get("prompt") else [])
    # 2칼럼 행: (번호, 본문) — 본문이 여러 줄이어도 번호 칼럼 아래로 새지 않고 본문
    # 칼럼 안에 머문다(사용자 요청). 번호는 우측정렬 폭 보존.
    rows = [(f"{i + 1}.", h) for i, h in enumerate(hist)]
    hidden = pid in app._claude_hidden_panes
    # 맨 나중에 입력한(최신) 프롬프트를 열 때 강조(#17). hist 는 시간순이라 마지막을 선택.
    latest = (len(hist) - 1) if hist else None
    # §10-A #8: 마지막 항목과 [h] footer 사이에 구분선(nav 에서 건너뜀).
    rows.append("─" * 24)
    rows.append("  [h] 이 헤더 " + ("다시 표시" if hidden else "숨기기"))
    app.push_screen(InfoScreen(
        [], title="프롬프트 히스토리(시간순)",
        hide_key="h", hide_cb=lambda: app.toggle_header_hidden(pid),
        initial_index=latest, max_width=110,   # 좌우로 넓게(#17)
        col_rows=rows))                        # 번호/본문 2칼럼 표시


def _open_usage_panel(app):
    """Claude `/usage` 한도(세션 5h·주 전체·주 Sonnet)를 깨끗한 전용 화면(InfoScreen,
    막대+%+리셋)으로 연다. 인패널 /usage 자동 팝업과 수동 명령(`usage-panel`)이 공유.
    한도 데이터가 없으면 안내만 표시한다. (usage_bar_lines·InfoScreen 은 코어 공유 —
    코어 TokenLogScreen 도 쓰므로 코어에 남겨 두고 여기서 가져온다.)"""
    from pytmuxlib.clientscreens import InfoScreen, usage_bar_lines
    u = getattr(app.status, "usage_limits", None)
    w = app.size.width if app.size else 80
    lines = usage_bar_lines(u, w,
                            age_sec=getattr(app.status, "usage_age_sec", None))
    if not lines:
        app.display_message(
            "/usage 한도 데이터 없음 — Claude 패널에서 /usage 를 먼저 실행")
        return
    app.push_screen(InfoScreen(lines, title="Claude 사용 한도 (/usage)"))


# ---- Claude 헤더/상태 (클라) — Phase 2c 에서 코어 client.py 에서 이리로 이전 ----
def _update_claude(app, panes_claude):
    """status 의 패널별 Claude 항목으로 app.pane_claude 를 갱신(헤더용). history 가
    빠진 항목(§4.5: 서버가 변할 때만 실음)은 직전 history 를 유지한다 — 매 status
    마다 30개 프롬프트를 재전송하지 않게."""
    prev_all = getattr(app, "pane_claude", {})
    new = {}
    for e in panes_claude:
        pid = e["id"]
        if "history" not in e:
            prev = prev_all.get(pid)
            if prev is not None and "history" in prev:
                e = {**e, "history": prev["history"]}
        new[pid] = e
    app.pane_claude = new


def _set_claude_header(app, on):
    """claude-header on|off — 프롬프트 헤더 표시 토글(전역). 낙관적으로 즉시 반영하고
    서버에 보내 opts.json 에 영속(#6 ③) — 서버가 status 로 회신."""
    app.claude_header_on = on
    app.send_cmd("set_claude_header", value=bool(on))
    app._composite()


def _toggle_header_hidden(app, pane_id):
    """특정 패널의 헤더만 숨기거나 다시 보이게(#6 ② — 히스토리 팝업서 토글). 패널별·
    세션 한정(전역 claude-header off 와 별개)."""
    hidden = app._claude_hidden_panes
    if pane_id in hidden:
        hidden.discard(pane_id)
    else:
        hidden.add(pane_id)
    app._composite()


def _toggle_remote_control(app, pane_id):
    """원격 제어 토글: 해당 Claude 패널에 `/rc` 슬래시 명령+Enter 를 주입한다. Claude
    Code CLI 가 `/rc` 로 원격 제어를 켜고 끈다 — 사용자가 직접 친 것과 동일한 입력
    경로(서버가 그 패널 PTY 에 그대로 쓴다)."""
    import asyncio
    import base64
    from pytmuxlib.protocol import write_msg
    if app.writer and pane_id is not None:
        asyncio.create_task(write_msg(app.writer, {
            "t": "input", "pane": pane_id,
            "data": base64.b64encode(b"/rc\r").decode("ascii")}))


def _open_remote_control(app, pane_id):
    """Claude 원격제어('Remote Control active') 정보+토글 팝업(§10 item 3). 원격 제어는
    Claude Code CLI 의 `/rc` 슬래시 명령으로 켜고 끌 수 있으므로, 이 팝업에서 [r] 로
    바로 토글한다(해당 패널에 `/rc` 주입)."""
    from pytmuxlib.clientscreens import InfoScreen
    lines = [
        "이 패널의 Claude Code 가 데스크탑 앱 '원격 제어'로 연결돼 있습니다.",
        "(패널 화면의 'Remote Control active' 표시)",
        "",
        "• 원격 제어는 Claude Code CLI 의 '/rc' 명령으로 켜고 끕니다.",
        "  → 이 화면에서 [r] 키로 바로 토글합니다(해당 패널에 /rc 주입).",
        "• 원격 제어로 입력된 프롬프트도 상단 프롬프트 헤더에 반영됩니다.",
        "",
        "[r] 원격 제어 토글(/rc)   ·   닫기: Esc 또는 바깥 클릭.",
    ]
    app.push_screen(InfoScreen(
        lines, title="원격 제어(Remote Control)",
        hide_key="r", max_width=92,   # 넓은 터미널에선 본문이 안 잘리게 확장(요청)
        # app._toggle_remote_control(attach_client 설치)을 거쳐 테스트 monkeypatch 도 존중.
        hide_cb=lambda: app._toggle_remote_control(pane_id)))


# ---- 토큰 사용량 트리 팝업(클라) — Phase 2c 에서 코어 client.py 에서 이리로 이전 ----
def _open_claude_usage_tree(app):
    """토큰 사용량 클릭/`token-usage` 명령 → 통합 상태 팝업의 '토큰 사용량' 탭(#10).
    탭 팝업 자체(REC/토큰/서버)는 코어 show_status_tabs 가 연다(REC/서버는 코어)."""
    app.show_status_tabs(initial=1)   # 1 = 토큰 탭(오른쪽)


def _open_usage_tree(app, tree):
    """purpose=="usage" 트리 회신 → 세션별 토큰 사용량 전용 InfoScreen 팝업."""
    from pytmuxlib.clientscreens import InfoScreen
    app.push_screen(InfoScreen(_usage_tree_lines(app, tree),
                               title="Claude 토큰 사용량(세션별)"))


def _usage_tree_lines(app, tree):
    """트리 응답 → 사용량 표시 줄. ctx(컨텍스트 %)와 함께 실제 세션 누계 토큰(Σ)을
    탭 합계·패널별로 보인다(#18). 맨 아래에는 가로 구분선과 **모든 세션 토큰 합계**
    한 줄을 덧붙인다(§10-A #6). 코어 통합 상태탭(_open_status_tabs)의 토큰 탭도 이
    함수를 getattr 로 불러 쓴다(없으면 안내문)."""
    from pytmuxlib.clientutil import _fmt_tokens
    lines = []
    grand = 0
    for s in tree.get("sessions", []):
        for w in s.get("windows", []):
            cps = [p for p in (w.get("panes") or [])
                   if isinstance(p, dict) and p.get("claude")]
            if not cps:
                continue
            wtok = sum((p.get("tokens") or 0) for p in cps)  # 탭 합계
            grand += wtok
            # ~Σ(S6 T3): 패널별 누계는 스크랩 추정(활동량) — 실측 한도가 아니다.
            lines.append(f"[{w['index'] + 1}] {w['name']}  —  ~Σ {_fmt_tokens(wtok)}")
            for p in cps:
                a = p.get("cmd") or "claude"
                usage = p.get("usage") or "-"
                state = p.get("claude")
                tok = _fmt_tokens(p.get("tokens") or 0)
                lines.append(f"    pane {p['id']} · {a} · {state} · "
                             f"{usage} · ~Σ {tok}")
    if not lines:
        return ["(실행 중인 Claude 패널 없음)"]
    # 하단 가로 구분선 + 전 세션 토큰 합계(§10-A #6).
    lines.append("─" * 36)
    lines.append(f"전체 세션 합계(추정)  —  ~Σ {_fmt_tokens(grand)}")
    return lines


class _ClaudeCodePlugin:
    name = "claude-code"
    commands = COMMANDS
    noarg = NOARG
    completions = []
    command_options = COMMAND_OPTIONS

    def server_mixin(self):
        """서버측 Claude 로직 믹스인 클래스(server.Server 의 동적 베이스로 합성된다).
        지연 import — 클라이언트도 plugins.load() 를 부르지만 servermixin 은 서버측
        코드(model/tokens/claude)를 끌어오므로 실제 서버가 요청할 때만 읽는다. 이
        디렉토리를 지우면 server_mixins() 가 비어 서버측 Claude 로직이 사라진다."""
        from .servermixin import ServerClaudeMixin
        return ServerClaudeMixin

    def server_init(self, server):
        """Server.__init__ 1회 훅 — 토큰 DB 연결 런타임 상태를 설치한다
        (S5 토큰 모듈화 T2). 코어 server.__init__ 에서 빼낸 _tokens_db 를
        동적 합성된 믹스인 메서드로 설치한다. 디렉토리 삭제 시 이 훅이
        사라져 코어 server 엔 토큰 상태가 안 생긴다(delete-to-disable). 형제 런타임 훅
        (server_input→_track_prompt 등)과 같은 불변식에 기댄다 — self.plugins 에 claude 가
        있으면 Server 에 ServerClaudeMixin 도 합성돼 있다(프로덕션·정상 테스트 순서)."""
        server._init_token_state()

    # ---- 토큰 설정 소유(S5 토큰 모듈화 T3) — 코어 server.py __init__·serverpersist
    # _save_opts 에서 이전. 코어는 키의 의미를 모르고, opts.json 의 plugin_opts
    # 네임스페이스를 불투명하게 저장만 한다. 디렉토리 삭제 시 이 훅들이 사라져 코어 server
    # 엔 이 속성들이 안 생기고 opts.json plugin_opts 가 비어 설정이 통째로 사라진다.
    # §7-4(2026-06-11): 절대 예산 token_budget_*(day/session/5h/account/resume_gate)
    # deprecate — 목록에서 제거. 구 opts.json 에 남은 키는 로드 시 무시되고 다음
    # _save_opts 에서 자연 소멸한다(마이그레이션 shim — S5 T3 선례와 같은 방식).
    _OPTS_KEYS = (
                  # S6 T4 실측 한도 게이트(%): 세션 기본 95(ON)·주간 기본 0(끔)
                  # — 2026-06-10 사용자 결정. 0=그 축 끔.
                  ("usage_gate_session_pct", 95, int),
                  ("usage_gate_week_pct", 0, int))

    def server_opts_init(self, server, opts):
        """opts.json → server 속성 설치(코어 __init__ 의 _opts.get 들을 이전).
        **마이그레이션 shim**: plugin_opts 네임스페이스를 우선 읽되, 없으면 구 top-level
        키(이 CL 이전·타 머신 opts.json)로 폴백한다 → 업그레이드 무중단. 한 번 _save_opts
        가 돌면 top-level 키는 사라지고 plugin_opts 만 남는다(코어가 더는 top-level 로
        안 씀)."""
        po = opts.get("plugin_opts")
        po = po if isinstance(po, dict) else {}
        for key, default, cast in self._OPTS_KEYS:
            raw = po[key] if key in po else opts.get(key, default)  # nested 우선, 구 키 폴백
            try:
                setattr(server, key, cast(raw))
            except (TypeError, ValueError):
                setattr(server, key, cast(default))

    def server_opts_serialize(self, server):
        """server 속성 → opts.json plugin_opts 네임스페이스(코어 _save_opts 의
        해당 블록을 이전). 코어는 이 dict 를 plugin_opts 밑에 불투명하게 저장한다."""
        return {key: getattr(server, key, default)
                for key, default, _cast in self._OPTS_KEYS}

    # ---- 서버 런타임 훅(코어 serverio/server 가 레지스트리로만 호출) ----
    # 코어는 Claude 서버 로직을 이름으로 부르지 않고 이 훅들로만 닿는다. 각 훅은 동적
    # 합성된 ServerClaudeMixin 메서드(server.<method>)로 위임한다. 디렉토리를 지우면
    # 레지스트리 훅이 no-op 가 되고 코어는 그대로 동작한다(delete-to-disable).
    def server_scan(self, server, sess, win):
        """30Hz flush 스캔 — 상태/사용량/자동개입 갱신. 변화 있으면 True."""
        return server._scan_claude(sess, win)

    def server_status(self, server, sess, win, msg, full):
        """status 메시지에 Claude 필드를 in-place 로 채운다(serverio._status_msg 에서
        이전). 코어가 만든 windows[] 항목에 탭 집계(claude)를 덧붙이고, 패널별 항목·
        토큰·사용량·예산·팝업 시퀀스와 full-only 정적 옵션 12개를 추가한다. 키/값은
        이전 코어 _status_msg 와 동일 — 서버 테스트가 그대로 검증한다."""
        ap = win.active_pane if win else None
        # C5: 계정 합계는 한 번만 계산해 claude_tokens·tok5h_pct 에 재사용.
        tok_total = server._account_token_total(ap)
        # 코어 windows[] 항목에 탭별 Claude 집계를 덧붙인다(순서=sess.tabs 와 일치).
        for wd, t in zip(msg.get("windows", ()), sess.tabs):
            wd["claude"] = server._tab_claude(t)
        # 활성 윈도우 패널별 Claude 상태/마지막 프롬프트(헤더용). history 는 변할 때만(§4.5).
        msg["panes_claude"] = [_pane_claude_entry(p, full)
                               for p in (win.panes() if win else ())]
        # 활성 패널이 Claude 패널인가(권위값) — 클라가 좌하단 토큰/사용량 표기를 게이트.
        msg["claude_active"] = bool(ap and ap._claude)
        # Claude 면 토큰/컨텍스트 사용량(best-effort; M18-A 근사 사용% 포함).
        msg["claude_usage"] = (server._usage_text(ap)
                               if ap and ap._claude else None)
        # 활성 패널 계정 기준 누적 토큰 합계(§10).
        msg["claude_tokens"] = tok_total
        # M18-B: 5시간 한도 근접도 %(분모 미상이면 None).
        msg["tok5h_pct"] = server._tok5h_pct(ap, tok_total)
        # M17(T7): 장기턴/반복루프 경고(없으면 None).
        msg["claude_warn"] = ap._claude_warn if ap else None
        # M19: 그림자 /usage 세션·주간 한도(없으면 None) + 자동 팝업 one-shot 시퀀스.
        msg["usage_limits"] = server._usage
        # S6 T3: 실측 경과(초) — 클라가 stale 표기("N분 전 실측")에 쓴다. 시계 동기
        # 가정 없이 서버가 경과로 환산해 보낸다. 실측 없으면 None.
        uts = getattr(server, "_usage_ts", None)
        msg["usage_age_sec"] = (max(0, int(_time.time() - uts))
                                if uts is not None else None)
        msg["usage_shown_seq"] = server._usage_shown_seq
        # M14c: 활성 패널 모델 배지(없으면 None) + 계정 식별자.
        msg["claude_model"] = (ap._claude_model
                               if ap and ap._claude else None)
        msg["claude_account"] = ap._claude_account if ap else None
        msg["autoresume"] = bool(ap.autoresume) if ap else False
        msg["prompt_clear"] = bool(ap.prompt_clear_mode) if ap else False
        # 프롬프트 단위 클리어 큐(#4): 활성 패널에 쌓인 명령들(표시·목록용).
        msg["prompt_clear_queue"] = (list(ap.prompt_clear_queue) if ap else [])
        # 낙관적 토글(클라 즉시 반영) + 주기 status 권위값으로 화해 — 매 status 에 싣는다.
        msg["claude_header"] = server.claude_header
        msg["auto_doc_clear"] = server.auto_doc_clear
        msg["auto_compact"] = server.auto_compact
        msg["auto_hardstop"] = server.auto_hardstop
        msg["claude_auto_mode"] = server.claude_auto_mode
        # §7-4: 절대 예산 deprecate — 경고 레벨은 실측 게이트(0/80/100)만. 와이어
        # 키 이름(budget_level)은 유지(클라 ⚠ 배지·전이 팝업이 그대로 소비).
        msg["budget_level"] = server._usage_gate_level(ap)
        # M14 무장된 자동 액션 카운트다운(없으면 None): {kind, eta(초)}.
        msg["claude_pending"] = server._pending_action(ap)
        # C4: 토글로만 바뀌는 정적 옵션은 full(신규 attach·_broadcast_session)일 때만
        # 싣는다 — set_* 핸들러가 _broadcast_session(full=True)으로 회신하므로 변경·
        # 접속 시 항상 도달하고, 주기(full=False) status 에선 빠져도 클라가 직전 값 유지.
        if full:
            msg.update({
                "claude_rules": server.claude_rules,   # #27 시작 규칙(에디터 초기값)
                "claude_ctx_autoclear": server.claude_ctx_autoclear,
                "claude_ctx_threshold": server.claude_ctx_threshold,
                "claude_ctx_min_interval": server.claude_ctx_min_interval,
                "claude_ctx_action": server.claude_ctx_action,
                "claude_long_turn_sec": server.claude_long_turn_sec,
                "claude_repeat_alert": server.claude_repeat_alert,
                "claude_budget_plan": server.claude_budget_plan,
                # S6 T4 실측 한도 게이트 임계(설정 팝업 표시용)
                "usage_gate_session_pct": server.usage_gate_session_pct,
                "usage_gate_week_pct": server.usage_gate_week_pct,
            })

    def server_pane_overview(self, server, pane, info):
        """트리/개요 패널 정보에 Claude 상태/사용량/세션 누계 토큰(#18)을 덧붙인다."""
        info["claude"] = pane._claude
        info["usage"] = pane._claude_usage
        info["tokens"] = pane._session_tokens

    def server_input(self, server, pane, data):
        """사용자 입력 1건의 Claude 부수효과: 프롬프트 추적(헤더용) + 자동 doc→/clear·
        자동 /compact·자동재개 예약 해제(사용자가 키를 쳤다 = 작업 이어받음)."""
        server._track_prompt(pane, data)
        server._adc_disarm(pane)
        server._acpt_disarm(pane)
        pane._acpt_fired = False  # 활동 재개 → 다음 idle 에 자동 /compact 재무장 허용
        pane._hardstop_fired = False  # 사용자가 직접 대응 중 → 하드스톱 자동복구 재무장
        server._cancel_resume(pane)

    def server_paste(self, server, pane, data):
        """붙여넣기(모바일 받아쓰기·자동완성 포함)도 프롬프트 추적에 반영(헤더용)."""
        server._track_prompt(pane, data)

    def server_pending(self, server, pane):
        """무장된 자동 액션 카운트다운({kind, eta}) 또는 None."""
        return server._pending_action(pane)

    async def server_usage_refresh(self, server):
        """그림자 /usage 자동 갱신 1회 — Claude 패널이 있고 질의 중이 아닐 때만."""
        if not server._usage_busy and server._any_claude_pane():
            try:
                await server.refresh_usage()
            except Exception:
                pass

    def server_command(self, server, client, sess, action, msg):
        """Claude 명령 액션을 처리하고 코어가 따를 후속 지시를 반환한다(없으면 None).
        serverio._handle_cmd 의 Claude elif 분기에서 이전. 반환값 의미:
        'handled'=추가 회신 없음, 'send_full'=요청 클라에 _send_full,
        'broadcast'=_broadcast_session(sess) 후 _send_full(원래 동작과 동일)."""
        import asyncio
        if action == "set_claude_perm_mode":
            # footer 클릭 팝업: 활성/지정 패널 권한모드 목표 설정.
            server.set_claude_perm_mode(sess, str(msg.get("target", "")),
                                        pane_id=msg.get("id"))
            return "handled"
        if action == "set_autoresume":
            server.set_autoresume(sess, value=msg.get("value"),
                                  msg=msg.get("msg"))
            return "send_full"
        if action == "set_auto_doc_clear":
            server.set_auto_doc_clear(msg.get("value"))
            return "send_full"
        if action == "set_auto_compact":
            server.set_auto_compact(msg.get("value"))
            return "send_full"
        if action == "set_auto_hardstop":
            server.set_auto_hardstop(msg.get("value"))
            return "send_full"
        if action == "set_claude_auto_mode":
            server.set_claude_auto_mode(msg.get("value"))
            return "send_full"
        if action == "set_claude_ctx_autoclear":      # M11 잔량 자동 정리 토글
            server.set_claude_ctx_autoclear(msg.get("value"))
            return "broadcast"
        if action == "set_claude_ctx_action":         # M11 정리 방식(compact/doc-clear)
            server.set_claude_ctx_action(str(msg.get("value", "")))
            return "broadcast"
        if action == "set_claude_ctx_threshold":      # M11 잔량 임계(%)
            server.set_claude_ctx_threshold(msg.get("value"))
            return "broadcast"
        if action == "set_claude_ctx_min_interval":   # M14 정리 빈도 상한(초)
            server.set_claude_ctx_min_interval(msg.get("value"))
            return "broadcast"
        if action == "set_claude_turn_warn":          # M17 장기턴/반복 임계
            server.set_claude_turn_warn(long_sec=msg.get("long_sec"),
                                        repeat=msg.get("repeat"))
            return "broadcast"
        if action == "refresh_usage":                 # M19 그림자 /usage 질의
            asyncio.create_task(server.refresh_usage())
            return "send_full"
        if action == "set_usage_gate":                # S6 T4 실측 한도 게이트 임계
            server.set_usage_gate(session=msg.get("session"),
                                  week=msg.get("week"))
            return "broadcast"
        if action == "set_claude_budget_plan":        # M13 예산 압박 plan 유도
            server.set_claude_budget_plan(msg.get("value"))
            return "broadcast"
        if action == "set_claude_rules":              # #27 시작 규칙 저장(영속)
            server.set_claude_rules(msg.get("text", ""))
            return "broadcast"                        # status 로 새 규칙 회신
        if action == "set_prompt_clear":
            server.set_prompt_clear(sess, msg.get("value"))
            return "send_full"
        if action == "set_prompt_clear_message":
            server.set_prompt_clear_message(str(msg.get("msg", "")))
            return "handled"
        if action == "pc_queue_add":
            server.pc_queue_add(sess, str(msg.get("cmd", "")))
            return "send_full"
        if action == "pc_queue_clear":
            server.pc_queue_clear(sess)
            return "send_full"
        return None

    def attach_client(self, app):
        # ClaudeSaverScreen 이 self.app._saver_display/_saver_action 를 부르므로 설치.
        app._saver_display = lambda key: saver_display(app, key)
        app._saver_action = lambda key: saver_action(app, key)
        # M16 토큰 절감 에스컬레이션 훅의 직전 전이 상태(상승 에지 1회 발화용, §8) —
        # 코어 client.__init__ 에서 이리로 이전(S5a). client_status 훅이 status 전이를
        # 보고 _fire_hook 을 발화하므로, 그 상태도 플러그인이 소유한다. 디렉토리 삭제 시
        # 이 속성이 없어 코어는 절감 훅을 전혀 발화하지 않는다(delete-to-disable).
        app._saver_prev = {"budget_level": 0, "pending_kind": None, "limit": False}
        # ---- Claude 헤더/상태 렌더 상태(Phase 2c) — 코어 __init__ 에서 이리로 이전 ----
        # 코어는 이 속성들을 직접 만들지 않고, 헤더 렌더(client_render 훅)·ESC nav·
        # 클릭 핸들러에서 getattr(app, ..., 기본값)으로만 읽는다 → 디렉토리 삭제 시
        # 속성이 없어 Claude 헤더/클릭존이 전혀 나타나지 않는다(delete-to-disable).
        app.pane_claude = {}            # id -> {"claude","prompt","history"}
        app.claude_header_on = True     # 프롬프트 헤더 표시(claude-header on|off)
        app._claude_hidden_panes = set()  # 헤더를 숨긴 패널 id(#6 ② 팝업서 토글)
        app._claude_header_zones = {}   # id -> (x0,x1,y) 헤더 클릭존(히스토리 팝업)
        app._perm_zone = {}             # id -> (x0,x1,y) 권한모드 footer 클릭존
        app._remote_zone = {}           # id -> (x0,x1,y) 원격제어 표시 클릭존
        app._last_usage_shown_seq = None  # /usage 자동 팝업 one-shot 시퀀스 베이스라인
        # 헤더 상태/클릭존 글루(코어/clientwidgets 가 getattr 로 호출 — 없으면 no-op).
        app._update_claude = lambda pc: _update_claude(app, pc)
        app.set_claude_header = lambda on: _set_claude_header(app, on)
        app.toggle_header_hidden = lambda pid: _toggle_header_hidden(app, pid)
        app._toggle_remote_control = lambda pid: _toggle_remote_control(app, pid)
        app.open_remote_control = lambda pid: _open_remote_control(app, pid)
        from .clientrender import claude_header_panes, footer_zone_at
        app._footer_zone_at = lambda x, y: footer_zone_at(app, x, y)
        app._claude_header_panes = lambda: claude_header_panes(app)
        # Claude 팝업(Phase 2a) — 코어에서 이리로 이전. 인스턴스 메서드로 설치한다
        # (PytmuxApp 은 build_client_app 팩토리 안 지역 클래스라 동적 베이스 믹스인을
        # 못 써 ncd/_saver_* 와 같은 클로저 설치 패턴을 쓴다). 코어 클릭/ESC/자동팝업
        # 콜러는 getattr 가드로 호출하므로, 디렉토리 삭제 시 이 속성들이 없어 no-op.
        app.open_model_config = lambda: _open_model_config(app)
        app._apply_model_config = lambda res: _apply_model_config(app, res)
        app.open_perm_mode = lambda pane_id: _open_perm_mode(app, pane_id)
        app.open_usage_panel = lambda: _open_usage_panel(app)
        app.open_prompt_history = lambda pane_id=None: _open_prompt_history(app, pane_id)
        app.open_token_log = lambda: _open_token_log(app)
        # 토큰 사용량 팝업(Phase 2c) — 코어 tree 디스패치(purpose=="usage")가 getattr 로
        # _open_usage_tree 를 부른다(없으면 no-op). 통합 상태 팝업의 '토큰 사용량' 탭은
        # 코어가 client_status_tabs 훅으로 받아 끼운다(_usage_tree_lines 직접 노출 불요).
        app.open_claude_usage_tree = lambda: _open_claude_usage_tree(app)
        app._open_usage_tree = lambda tree: _open_usage_tree(app, tree)

    def pane_closing(self, server, pane):
        """패널 종료 직전(코어 servertree → pane_closing 훅) — 닫히는 Claude 패널의 확정
        토큰을 같은 계정 생존 패널로 이관한다(#20, S5 토큰 모듈화 T4). 동적 합성된 믹스인
        메서드로 위임 — 코어 servertree 는 토큰 누계를 모른다."""
        server._carry_tokens_on_close(pane)

    # ---- Pane Claude 상태 소유(S4) — panestate 모듈에 위임 ----
    def pane_init(self, pane):
        from .panestate import init_pane
        init_pane(pane)

    def pane_reset(self, pane):
        from .panestate import reset_pane
        reset_pane(pane)

    def pane_serialize(self, pane):
        from .panestate import serialize
        return serialize(pane)

    def pane_restore(self, pane, data):
        from .panestate import restore
        restore(pane, data)

    def handle_message(self, app, msg):
        # 서버 token_log 회신 → TokenLogScreen 팝업(코어 _dispatch 의 else 에서 위임).
        if msg.get("t") == "token_log":
            _on_token_log_msg(app, msg)
            return True
        return False

    def handle_server_request(self, server, sess, action, msg):
        """코어 serverio 가 알 수 없는 action 을 넘기면(레지스트리 handle_server_request)
        토큰 영속 로그 조회를 처리해 회신 dict 를 돌려준다 — 코어가 그대로 클라로 보낸다.
        S5(토큰 모듈화 T1)에서 serverio 의 `request_token_log` elif 분기를 이리로 이전:
        코어 serverio 가 더는 usagedb 를 import 하지 않게 한다(탈토큰). 디렉토리를 지우면
        이 훅이 사라져 토큰 로그 요청이 무응답(클라는 빈 팝업) — 코어는 무에러."""
        if action == "request_token_log":
            # 영속 토큰 레코드(최근 N 건)를 SQLite 에서 읽어 클라이언트로. 클라가
            # usagelog 로 시간/일/월 × 계정/세션 집계해 팝업에 표시(라운드트립 없이
            # 버킷/차원 전환). Phase B: 버킷 전환용 N 건과 별개로, 정확한 **전체
            # 이력 합**(total_all)·계정별 합(accounts_total)을 서버가 SQL GROUP BY 로
            # 함께 보내, 이력이 N 을 넘어도 lifetime Σ 가 과소표시되지 않게 한다.
            from . import usagedb   # S5 T5: 플러그인 소속(물리 이전)
            conn = server._tokens_db_conn()
            recs = (usagedb.query_records(conn, limit=int(msg.get("limit", 5000)))
                    if conn is not None else [])
            total_all = usagedb.total_all(conn) if conn is not None else 0
            accts = usagedb.totals_by_account(conn) if conn is not None else {}
            # S6 T2: 대사(reconcile) 구간 — 실측 스냅샷 Δpct vs 스크랩 Σ. 진단
            # 전용 데이터라 표시는 TokenLogScreen [대사] 뷰만 소비한다.
            recon = usagedb.reconcile(conn) if conn is not None else []
            return {"t": "token_log", "records": recs,
                    "total_all": total_all, "accounts_total": accts,
                    "reconcile": recon}
        return None

    # ---- 클라이언트 콘텐츠-레이어 렌더/상태 훅(Phase 2c) ----
    def client_render(self, app, cells, W, H):
        """코어 _composite 가 콘텐츠를 그린 뒤 호출 — Claude 프롬프트 헤더를 그리고
        footer 클릭존(권한모드/원격제어)을 스캔해 app zone dict 를 채운다. 디렉토리를
        지우면 이 훅이 사라져 헤더·클릭존이 전혀 나타나지 않는다(delete-to-disable)."""
        from .clientrender import render
        render(app, cells, W, H)

    def client_status(self, app, msg):
        """서버 status 의 Claude 필드를 클라가 흡수한다(코어 _dispatch status 에서 위임).
        claude_header/claude_rules 동기화, 패널별 Claude 상태(pane_claude) 갱신, 인패널
        /usage 자동 팝업 시퀀스를 처리한다."""
        if "claude_header" in msg:
            app.claude_header_on = bool(msg["claude_header"])
        if "claude_rules" in msg:
            app._claude_rules = msg.get("claude_rules", "")
        # /usage 자동 팝업(요청): 인패널 /usage 패널이 새로 떴다는 seq 가 늘면 깨끗한
        # 전용 사용량 화면을 자동으로 띄운다. 접속 후 첫 status 는 베이스라인만 잡고
        # 띄우지 않는다(과거 seq 로 엉뚱하게 안 뜨게).
        seq = msg.get("usage_shown_seq", 0)
        if app._last_usage_shown_seq is None:
            app._last_usage_shown_seq = seq
        elif seq > app._last_usage_shown_seq:
            app._last_usage_shown_seq = seq
            # 다른 모달이 떠 있으면 건너뛴다(가림·중복 방지). 팝업은 open_usage_panel.
            if len(app.screen_stack) <= 1:
                fn = getattr(app, "open_usage_panel", None)
                fn and fn()
        _update_claude(app, msg.get("panes_claude", []))
        # M16: 절감 신호 전이 → PTY 밖 에스컬레이션 훅(자리 비움 대응, §8). 코어
        # client._dispatch 에서 이리로 이전(S5a) — saver_hook_events 는 플러그인 소유
        # claude.py 의 함수라, 코어가 더는 claude 를 import 하지 않게 된다. _fire_hook 은
        # 코어의 범용 셸-훅 디스패처(after-new-window 등과 공유)라 그대로 호출한다.
        from .claude import saver_hook_events
        for ev, env in saver_hook_events(app._saver_prev, msg):
            app._fire_hook(ev, env=env)

    def client_statusbar_init(self, app, status):
        """하단 상태줄 위젯 생성 직후 — Claude 상태 속성을 안전한 기본값으로 설치한다
        (코어 StatusBar.__init__ 에서 빼낸 claude_*/usage_gate_*/auto_* 필드).
        흡수(absorb)·렌더(render_segs)가 이 속성들을 읽고 쓴다."""
        from .clientstatus import init_defaults
        init_defaults(status)

    def client_statusbar_update(self, app, status, msg):
        """하단 상태줄 위젯에 status 메시지의 Claude 필드를 흡수(코어 StatusBar.
        update_status 의 Claude 블록 이전)."""
        from .clientstatus import absorb
        absorb(status, msg)

    def client_statusbar(self, app, status, segs, w):
        """하단 상태줄 좌측에 Claude 세그먼트(모델·컨텍스트·토큰Σ·예산·카운트다운·경고)를
        그리고 클릭존을 채운다(코어 StatusBar._render_main 의 Claude 블록 이전)."""
        from .clientstatus import render_segs
        render_segs(status, segs, w)

    def client_status_tabs(self, app, tree):
        """통합 상태 팝업(코어 _open_status_tabs)의 '토큰 사용량' 탭을 기여한다 — 코어가
        REC 탭과 서버 탭 사이에 끼운다(REC(0)·토큰(1)·서버(2)). 디렉토리를 지우면 이 훅이
        사라져 토큰 탭이 통째로 빠지고 REC·서버만 남는다(delete-to-disable)."""
        return [("토큰 사용량", _usage_tree_lines(app, tree))]

    def handle_command(self, app, c, args):
        # 팝업(명령 전용 + 클릭/ESC 겸용) — 클릭/렌더 경로의 open_* 는 아직 코어에 있어
        # (Phase 2 이전 예정) 여기선 그 메서드를 호출한다. 디렉토리를 지우면 명령 경로는
        # 사라지지만 클릭 경로는 Phase 2 까지 코어에 남는다(단계적 추출).
        if c in ("claude-rules", "rules", "startup-rules"):
            self._open_rules(app)
        elif c in ("token-saver", "claude-settings", "token-settings"):
            self._open_saver(app)
        elif c in ("prompt-history", "prompts"):
            app.open_prompt_history(app.layout.get("active"))
        elif c in ("token-usage", "tokens"):
            app.open_claude_usage_tree()
        elif c in ("token-log", "tokens-log", "token-usage-log"):
            app.open_token_log()
        elif c in ("usage-panel", "usage-limits", "limits"):
            app.open_usage_panel()
        elif c in ("model", "model-config", "claude-model"):
            app.open_model_config()
        elif c == "claude-header":
            # claude-header [on|off|toggle] — 프롬프트 헤더 표시 제어(기본 toggle).
            arg = args[0].lower() if args else "toggle"
            app.set_claude_header(arg == "on" if arg in ("on", "off")
                                  else not app.claude_header_on)
        # 토글/주입 명령(서버로 전송, on/off 없으면 서버가 토글)
        elif c in ("auto-resume", "autoresume"):
            app.send_cmd("set_autoresume", value=_onoff(args))
        elif c in ("auto-resume-message", "autoresume-message"):
            app.send_cmd("set_autoresume", msg=" ".join(args))
        elif c in ("claude-usage", "usage", "refresh-usage"):
            # M19 그림자 /usage 질의: 서버가 숨은 claude 를 띄워 실 세션/주간 한도를
            # 긁어온다(사용자 화면 무간섭, ~수초). 회신은 status 로 반영.
            app.send_cmd("refresh_usage")
            app.display_message("사용량 조회 중… (숨은 /usage, ~수초)", 4.0)
        elif c in ("token-account", "tokens-account"):
            app.send_cmd("set_claude_account", name=" ".join(args).strip())
        elif c in ("prompt-clear", "prompt-clear-mode"):
            app.send_cmd("set_prompt_clear", value=_onoff(args))
        elif c in ("auto-doc-clear", "auto-doc"):
            app.send_cmd("set_auto_doc_clear", value=_onoff(args))
        elif c in ("auto-compact", "auto-cmp"):
            app.send_cmd("set_auto_compact", value=_onoff(args))
        elif c in ("auto-hardstop", "auto-hard", "hardstop"):
            app.send_cmd("set_auto_hardstop", value=_onoff(args))
        elif c in ("claude-auto-mode", "auto-mode"):
            app.send_cmd("set_claude_auto_mode", value=_onoff(args))
        elif c == "prompt-clear-message":
            app.send_cmd("set_prompt_clear_message", msg=" ".join(args).strip())
        elif c in ("prompt-clear-queue", "pc-queue"):
            self._pc_queue(app, args)
        else:
            return False
        return True

    def _pc_queue(self, app, args):
        # prompt-clear-queue [<명령> | -c|clear] — 빈값=현재 큐 목록 팝업(#4), -c/clear=
        # 큐 비움, 그 외=명령을 큐에 추가(모드 자동 on, doc+/clear 사이클마다 하나씩).
        if not args:
            from pytmuxlib.clientscreens import InfoScreen
            q = app.status.prompt_clear_queue
            lines = [f"{i + 1}. {cmd}" for i, cmd in enumerate(q)] or \
                ["(큐 비어 있음)"]
            app.push_screen(InfoScreen(lines, title="프롬프트 클리어 큐"))
        elif args[0].lower() in ("-c", "clear", "--clear"):
            app.send_cmd("pc_queue_clear")
            app.display_message("큐 비움")
        else:
            app.send_cmd("pc_queue_add", cmd=" ".join(args).strip())

    def _open_rules(self, app):
        # #27: Claude 시작 규칙 편집 팝업. 저장하면 서버 opts.json 에 영속하고, 새 Claude
        # 세션 또는 /clear 직후 첫 idle 에 프롬프트로 자동 주입한다.
        from .screens import RulesEditScreen

        def _saved(text):
            if text is not None:
                app.send_cmd("set_claude_rules", text=text)
                app.display_message("시작 규칙 저장됨" if text.strip()
                                    else "시작 규칙 비움")
        app.push_screen(RulesEditScreen(getattr(app, "_claude_rules", "")), _saved)

    def _open_saver(self, app):
        from .screens import ClaudeSaverScreen
        app.push_screen(ClaudeSaverScreen())


PLUGIN = _ClaudeCodePlugin()
