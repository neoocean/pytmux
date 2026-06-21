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

import os
import time as _time

from pytmuxlib import i18n

# §6 ⑤ claude.* 카탈로그는 clientstatus 가 import 시점에 등록한다. 58659(헤더 제거)가
# 사용처 import 를 전부 함수 안으로 지연화하면서 plugins.load() 만으로는 등록이 안 되는
# 순서 의존이 생겼다(test_i18n 단독 실행 실패·전체 스위트에선 test_client 가 가림) —
# 모듈은 가볍다(textual 미사용, 자기 docstring) → 로드 시점 명시 import 로 보장한다.
from . import clientstatus  # noqa: F401  (i18n claude.* 등록 부수효과)

# ---- 명령 메타데이터(코어 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐짐) ----
COMMANDS = [
    ("claude-rules", "Claude 시작 규칙 편집(저장 시 새 세션/clear 후 프롬프트에 "
                     "자동 주입)", "Claude"),
    ("token-saver", "토큰 절감 설정 팝업 — 각 자동 개입 토글·잔량 임계·실측 게이트"
                    "(별칭 claude-settings, token-settings)", "Claude"),
    ("auto-resume", "토큰 리밋 자동 재개 [on|off]", "Claude"),
    ("auto-resume-message", "자동 재개 메시지 설정", "Claude"),
    ("token-log", "토큰 사용량 팝업 — 기간(시/일/주/월)·계정·세션 뷰 + 실측 한도·5h창"
                  "(별칭 token-usage, 상태줄 사용량 클릭)", "Claude"),
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
                      "/compact on/off (auto-hardstop on|off|toggle, 기본 off)",
                      "Claude"),
    ("auto-retry", "전송 에러(API error·rate limit) 시 1분 뒤 '계속' 자동 주입 on/off "
                   "(auto-retry on|off|toggle, 기본 on)", "Claude"),
    ("auto-token-on-exit", "Claude 세션 종료 시 토큰 사용량 화면(한도/usage) 자동 표시 "
                           "on/off (auto-token-on-exit on|off|toggle, 기본 on)",
                           "Claude"),
    ("claude-auto-mode", "Claude idle 시 권한모드를 자동으로 오토모드로 전환 on/off "
                         "(claude-auto-mode on|off|toggle)", "Claude"),
    ("auto-launch", "새 Claude 세션 시작 시 /rc(원격 제어)+권한모드 auto 1회 자동 적용 "
                    "on/off (auto-launch on|off|toggle, 기본 on)", "Claude"),
    ("model-hint", "Opus 로 반복 작업+컨텍스트 여유 시 가벼운 모델 '고려' 힌트 표시 "
                   "(알림만·자동 전환 없음) on/off (model-hint on|off|toggle, 기본 off)",
                   "Claude"),
    ("token-debug", "토큰 회계 진단 로그(<sock>.tokendbg.jsonl) on/off — §10-D 과소집계 "
                    "원인 판정용 진단(평시 OFF) (token-debug on|off|toggle, 기본 off)",
                    "Claude"),
]
NOARG = {
    "claude-rules", "token-saver",
    "token-usage", "token-log",
    "claude-usage", "usage", "usage-panel", "usage-limits", "limits",
}
# 옵션(선택지) 스키마 — 팔레트에서 on/off/토글을 키보드로 고른다(코어 COMMAND_OPTIONS 병합).
_ONOFF = [("토글", ""), ("켜기", "on"), ("끄기", "off")]
COMMAND_OPTIONS = {
    "auto-resume": [{"key": "state", "label": "자동재개", "choices": _ONOFF}],
    "prompt-clear": [{"key": "state", "label": "클리어모드", "choices": _ONOFF}],
    "auto-doc-clear": [{"key": "state", "label": "자동클리어", "choices": _ONOFF}],
    "auto-hardstop": [{"key": "state", "label": "하드스톱복구", "choices": _ONOFF}],
    "auto-retry": [{"key": "state", "label": "자동재시도", "choices": _ONOFF}],
    "claude-auto-mode": [{"key": "state", "label": "오토모드", "choices": _ONOFF}],
    "auto-launch": [{"key": "state", "label": "자동셋업", "choices": _ONOFF}],
    "model-hint": [{"key": "state", "label": "모델힌트", "choices": _ONOFF}],
    "token-debug": [{"key": "state", "label": "토큰진단로그", "choices": _ONOFF}],
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
        "cmd.token-log": "Token usage popup — period (h/d/w/m)·session views + measured limits·5h window (alias token-usage, click status usage)",
        "cmd.claude-usage": "Shadow /usage query — refresh real session/weekly limits via a hidden session (alias usage)",
        "cmd.usage-panel": "Claude token usage-limit popup — /usage (session 5h·week all·week Sonnet) bar graph (alias usage-limits·limits)",
        "cmd.token-account": "Manually set the active pane's Claude account (token-account <name>, empty=auto)",
        "cmd.prompt-clear": "Toggle per-prompt clear mode (document + /clear each completion) [on|off]",
        "cmd.prompt-clear-message": "Change the per-prompt-clear documentation directive",
        "cmd.prompt-clear-queue": "Queue commands for per-prompt clear (empty=list, -c=clear)",
        "cmd.model": "Model·context change popup (also opens via the status model badge, injects /model; alias model-config, claude-model)",
        "cmd.auto-doc-clear": "Auto document + /clear when Claude idle 30s on/off (auto-doc-clear on|off|toggle)",
        "cmd.auto-compact": "Auto /compact when Claude idle 30s on/off (auto-compact on|off|toggle)",
        "cmd.auto-hardstop": "Auto /compact immediately on context hardstop ('Context limit reached') on/off (auto-hardstop on|off|toggle, default off)",
        "cmd.auto-retry": "Auto-inject a 'continue' message 1 min after a transmission error (API error·rate limit) on/off (auto-retry on|off|toggle, default on)",
        "cmd.auto-token-on-exit": "Auto-open token usage screen (Limit/usage) when Claude session ends on/off (auto-token-on-exit on|off|toggle, default on)",
        "cmd.claude-auto-mode": "Auto-switch permission mode to auto when Claude idle on/off (claude-auto-mode on|off|toggle)",
        "cmd.auto-launch": "On new Claude session apply /rc (remote control)+permission auto once on/off (auto-launch on|off|toggle, default on)",
        "cmd.model-hint": "Show a 'consider a lighter model' hint when Opus repeats work with context headroom (alert only, never auto-switches) on/off (model-hint on|off|toggle, default off)",
        "cmd.token-debug": "Token-accounting diagnostic log (<sock>.tokendbg.jsonl) on/off — §10-D undercount root-cause diagnostic (off normally) (token-debug on|off|toggle, default off)",
    },
})

# §6 ⑤ 플러그인 토스트/InfoScreen 표면 문자열(i18n 전수조사 2026-06-19 — en 로케일
# 한글 누출 수정). 명령 핸들러의 display_message/InfoScreen 에 직접 박혀 있던 한글을
# 이리로 모아 ko/en 대칭화한다. 키 네임스페이스 "ccmsg.*".
i18n.register({
    "ko": {
        "ccmsg.model_apply": "/model {arg} 적용 요청",
        "ccmsg.perm_switching": "권한모드 → {target} 전환 중…",
        "ccmsg.usage_no_data": "/usage 한도 데이터 없음 — Claude 패널에서 /usage 를 먼저 실행",
        "ccmsg.usage_title": "Claude 사용 한도 (/usage)",
        "ccmsg.no_warn": "표시할 Claude 경고가 없습니다(이미 해소됨).",
        "ccmsg.rc_title": "원격 제어(Remote Control)",
        "ccmsg.rc_body":
            "이 패널의 Claude Code 가 데스크탑 앱 '원격 제어'로 연결돼 있습니다.\n"
            "(패널 화면의 'Remote Control active' 표시)\n"
            "\n"
            "• 원격 제어는 Claude Code CLI 의 '/rc' 명령으로 켜고 끕니다.\n"
            "  → 이 화면에서 [r] 키로 바로 토글합니다(해당 패널에 /rc 주입).\n"
            "• 원격 제어로 입력된 프롬프트도 상단 프롬프트 헤더에 반영됩니다.\n"
            "\n"
            "[r] 원격 제어 토글(/rc)   ·   닫기: Esc 또는 바깥 클릭.",
        "ccmsg.usage_querying": "사용량 조회 중… (숨은 /usage, ~수초)",
        "ccmsg.pc_queue_title": "프롬프트 클리어 큐",
        "ccmsg.pc_queue_empty": "(큐 비어 있음)",
        "ccmsg.pc_cleared": "큐 비움",
        "ccmsg.rules_saved": "시작 규칙 저장됨",
        "ccmsg.rules_cleared": "시작 규칙 비움",
    },
    "en": {
        "ccmsg.model_apply": "Requested /model {arg}",
        "ccmsg.perm_switching": "Switching permission mode → {target}…",
        "ccmsg.usage_no_data":
            "No /usage limit data — run /usage in a Claude panel first",
        "ccmsg.usage_title": "Claude usage limit (/usage)",
        "ccmsg.no_warn": "No Claude warning to show (already cleared).",
        "ccmsg.rc_title": "Remote Control",
        "ccmsg.rc_body":
            "This panel's Claude Code is connected to the desktop app's "
            "'Remote Control'.\n"
            "(the panel shows 'Remote Control active')\n"
            "\n"
            "• Remote control is toggled with the Claude Code CLI '/rc' command.\n"
            "  → Press [r] here to toggle it directly (injects /rc into the panel).\n"
            "• Prompts entered via remote control also appear in the top prompt "
            "header.\n"
            "\n"
            "[r] Toggle remote control (/rc)   ·   close: Esc or click outside.",
        "ccmsg.usage_querying": "Querying usage… (hidden /usage, ~a few sec)",
        "ccmsg.pc_queue_title": "Prompt-clear queue",
        "ccmsg.pc_queue_empty": "(queue empty)",
        "ccmsg.pc_cleared": "Queue cleared",
        "ccmsg.rules_saved": "Start rules saved",
        "ccmsg.rules_cleared": "Start rules cleared",
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
        "오토모드": "Auto mode", "자동셋업": "Auto setup",
        "자동재시도": "Auto-retry", "모델힌트": "Model hint",
        "토큰진단로그": "Token diag log",
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
    ("auto_token_on_exit", "세션 종료 시 토큰 사용량 화면 자동 표시", "toggle"),
    ("claude_auto_mode", "권한모드 자동 오토", "toggle"),
    ("prompt_clear", "프롬프트 단위 클리어(완료마다 doc+/clear)", "toggle"),
    ("long_turn", "장기 턴 경고(초)", "cycle"),
    ("repeat_alert", "반복 루프 경고(회)", "cycle"),
    ("model_hint", "모델 과선택 힌트(Opus 반복+여유 시 가벼운 모델 제안)", "toggle"),
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
        "auto_token_on_exit": st.auto_token_on_exit,
        "claude_auto_mode": st.claude_auto_mode,
        "prompt_clear": st.prompt_clear,
        "model_hint": st.claude_model_hint,
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
    elif key == "auto_token_on_exit":
        app.send_cmd("set_auto_token_on_exit", value=None)
        st.auto_token_on_exit = not st.auto_token_on_exit
    elif key == "claude_auto_mode":
        app.send_cmd("set_claude_auto_mode", value=None)
        st.claude_auto_mode = not st.claude_auto_mode
    elif key == "prompt_clear":
        app.send_cmd("set_prompt_clear", value=None)
        st.prompt_clear = not st.prompt_clear
    elif key == "model_hint":
        app.send_cmd("set_claude_model_hint", value=None)
        st.claude_model_hint = not st.claude_model_hint
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
    """status 의 패널별 Claude 항목(헤더/권한모드 표시용). (serverio 코어에서 이리로
    이전.)"""
    return {"id": p.id, "claude": p._claude, "prompt": p.last_prompt,
            "perm_mode": p._perm_mode, "bypass_ok": p._bypass_seen}


# Claude Code 피드백 권유 문구(시작 팁 "Tip: Use /feedback …" + 세션 종료 평가 배너
# "How is Claude doing this session?")를 화면에서 가리는 기능은 별도 플러그인
# claude-disable-feedback 로 분리했다(2026-06-20). server_filter_rows 훅은 레지스트리가
# 모든 활성 플러그인에 체인하므로, 그 플러그인이 자기 server_filter_rows 에서 가린다.


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
    app.display_message(i18n.t("ccmsg.model_apply", arg=arg))


def _interrupt_pane(app, pane_id):
    """busy footer 의 'esc to interrupt' 클릭 → 그 패널에 ESC(\\x1b)를 주입한다.
    실행 중인 Claude 작업을 중단(키보드 ESC 와 동일). 활성 패널을 바꾸지 않도록
    send_input_pane 으로 클릭한 패널에 직접 보낸다(비활성 Claude 패널도 가능)."""
    app.send_input_pane(pane_id, b"\x1b")


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
            app.display_message(i18n.t("ccmsg.perm_switching", target=target))
    app.push_screen(PermModeScreen(current, anchor_y=anchor_y,
                                   anchor_x=anchor_x,
                                   bypass_available=bypass_ok), _chosen)


def _open_token_log(app, initial_mode=None):
    """토큰 사용량 영속 로그 집계 팝업(#7). 서버에 최근 로그를 요청하고, 응답
    (t==token_log)이 오면 handle_message 가 TokenLogScreen 으로 시간/일/주/월×계정
    집계를 띄운다. 상태바 Σ 클릭의 진입점이기도 하다.

    initial_mode="limit" 이면 한도(/usage) 탭이 활성인 채로 연다 — usage-view 팝업이
    별도 화면 대신 이 통합 팝업의 한도 탭을 열게 한다(통합, 사용자 결정 2026-06-17).
    initial_mode="hour" 면 기간 뷰의 시간(hour) 버킷으로 연다 — 상태줄 "N%/5h used"
    세그먼트 클릭이 시각별 5h% 막대 뷰를 바로 보이게 한다(사용자 요청 2026-06-18)."""
    app._want_token_log = True
    app._token_log_initial = initial_mode
    app.send_cmd("request_token_log", limit=5000)


def _on_token_log_msg(app, msg):
    """서버 token_log 회신 → TokenLogScreen 팝업(open_token_log 가 요청했을 때만)."""
    if not getattr(app, "_want_token_log", False):
        return
    app._want_token_log = False
    initial_mode = getattr(app, "_token_log_initial", None)
    app._token_log_initial = None
    from .screens import TokenLogScreen
    app.push_screen(TokenLogScreen(
        msg.get("records") or [],
        usage=getattr(app.status, "usage_limits", None),
        total_all=msg.get("total_all"),
        daily=msg.get("daily"),
        reconcile=msg.get("reconcile"),
        daily_pct=msg.get("daily_pct"),
        hourly_pct=msg.get("hourly_pct"),
        hourly_week_pct=msg.get("hourly_week_pct"),
        active_session=msg.get("active_session"),
        initial_mode=initial_mode))


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
        app.display_message(i18n.t("ccmsg.usage_no_data"))
        return
    app.push_screen(InfoScreen(lines, title=i18n.t("ccmsg.usage_title")))


def _open_warn_info(app):
    """하단 상태줄 Claude 경고 배지(⚠) 클릭 → 통합 토큰 팝업의 '경고' 탭을 연다
    (2026-06-17 통합: 별도 InfoScreen 대신 token-log 의 경고 탭이 상황·할일 안내를
    그린다 — 경고 종류 판별/문구는 TokenLogScreen._warn_info_text 가 소유). 경고가 이미
    사라졌으면 팝업을 열지 않고 가볍게 알린다."""
    warn = getattr(app.status, "claude_warn", None)
    if not warn:
        app.display_message(i18n.t("ccmsg.no_warn"))
        return
    fn = getattr(app, "open_token_log", None)
    if fn is not None:
        fn("warn")
    else:                       # claude-code 부재 폴백(개념상 항상 있음)
        app.display_message(warn)


# ---- Claude 헤더/상태 (클라) — Phase 2c 에서 코어 client.py 에서 이리로 이전 ----
def _update_claude(app, panes_claude):
    """status 의 패널별 Claude 항목으로 app.pane_claude 를 갱신(헤더용)."""
    app.pane_claude = {e["id"]: e for e in panes_claude}


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
    lines = i18n.t("ccmsg.rc_body").split("\n")
    app.push_screen(InfoScreen(
        lines, title=i18n.t("ccmsg.rc_title"),
        hide_key="r", max_width=92,   # 넓은 터미널에선 본문이 안 잘리게 확장(요청)
        # app._toggle_remote_control(attach_client 설치)을 거쳐 테스트 monkeypatch 도 존중.
        hide_cb=lambda: app._toggle_remote_control(pane_id)))


# (토큰 사용량 트리 팝업(_open_claude_usage_tree/_open_usage_tree/_usage_tree_lines,
#  통합 상태 팝업의 '토큰 사용량' 탭 포함)은 2026-06-12 token-log 로 통합·제거 —
#  같은 데이터(세션별 Σ)는 TokenLogScreen 세션 뷰([p])가 영속 이력으로 보여 주고,
#  라이브 ctx% 는 상태줄·프롬프트 헤더가 이미 보인다. `token-usage` 명령은 token-log
#  별칭으로 남는다(handle_command). 통합 상태 팝업은 REC·서버 두 탭으로 줄었다.)


class _ClaudeCodePlugin:
    name = "claude-code"
    description = "Claude Code 연동 — 헤더·상태줄 토큰·시작 규칙·자동개입"
    category = "Claude"
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
                  ("usage_gate_week_pct", 0, int),
                  # 전송 에러(API error/rate limit) 자동 재시도(요청 2026-06-12): 에러로
                  # 멈추면 1분 뒤 "계속" 주입. 기본 ON.
                  ("claude_auto_retry", True, bool),
                  # M14c 모델 과선택 힌트(T3/S4): Opus 반복+여유 시 가벼운 모델 "고려"
                  # 헤더 힌트(알림만, 자동 전환 없음). opt-in 이라 기본 OFF.
                  ("claude_model_hint", False, bool),
                  # §10-D 토큰 회계 진단 로그(<sock>.tokendbg.jsonl). 기본 OFF — 켜면
                  # 매 토큰 step 을 jsonl 로 남긴다(평시 성능/디스크 무영향). 종전 env
                  # PYTMUX_TOKEN_DEBUG 를 대체(런타임 `token-debug on/off` 토글). opts.json
                  # 미존재 시 그 env 를 기동 기본값으로 폴백 — 아래 server_opts_init.
                  ("token_debug", False, bool),
                  # §10-F: Claude 세션 종료 시 토큰 사용량 화면(한도/usage 탭) 자동
                  # 표시(요청 2026-06-18). 기본 ON.
                  ("auto_token_on_exit", True, bool))

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
        # §10-D 마이그레이션: token_debug 가 opts.json 에 아직 없으면(구 env 사용자·신규
        # 설치) 종전 env PYTMUX_TOKEN_DEBUG 를 기동 기본값으로 폴백한다. 위 루프가 token_debug
        # 를 default(False)로 깔아 두므로, 양쪽 키가 모두 없을 때만 env 로 덮어쓴다. 한 번
        # 런타임 토글이 _save_opts 로 영속되면 그 값이 권위(다음 기동부터 env 무시).
        if "token_debug" not in po and "token_debug" not in opts:
            server.token_debug = bool(os.environ.get("PYTMUX_TOKEN_DEBUG"))

    def server_opts_serialize(self, server):
        """server 속성 → opts.json plugin_opts 네임스페이스(코어 _save_opts 의
        해당 블록을 이전). 코어는 이 dict 를 plugin_opts 밑에 불투명하게 저장한다."""
        out = {key: getattr(server, key, default)
               for key, default, _cast in self._OPTS_KEYS}
        return out

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
        # M18-B: 5시간 한도 근접도 %(분모 미상이면 None). 단, 활성 모델이 Sonnet 이면
        # 5h 세션%(Anthropic 이 모델 통합값으로만 줘 모델별 측정 불가) 대신 주간
        # Sonnet only % 를 보낸다(2026-06-16 사용자 결정: sonnet 일 땐 측정 가능한
        # Sonnet 사용 비율만 표시, 5h 는 숨김). 둘은 상호배타(한쪽만 채움) — 클라가
        # 받은 쪽으로 라벨('%/5h' vs '%/주(Sonnet)')한다.
        _is_sonnet = bool(ap and ap._claude
                          and (ap._claude_model or "").startswith("sonnet"))
        if _is_sonnet:
            msg["tok5h_pct"] = None
            msg["week_sonnet_pct"] = server._week_sonnet_pct(ap)
        else:
            msg["tok5h_pct"] = server._tok5h_pct(ap, tok_total)
            msg["week_sonnet_pct"] = None
        # M17(T7): 장기턴/반복루프 경고(없으면 None). 종류(kind)·반복수(n)도 함께
        # 보내 클라가 로케일별로 배지/안내를 렌더한다(i18n 전수조사 2026-06-19 — 한글
        # 부분문자열 판별 대체). claude_warn 문자열은 호환/장기턴 배지(언어중립)용 유지.
        msg["claude_warn"] = ap._claude_warn if ap else None
        msg["claude_warn_kind"] = getattr(ap, "_claude_warn_kind", None) if ap else None
        msg["claude_warn_n"] = getattr(ap, "_claude_warn_n", None) if ap else None
        # M14c 모델 과선택 힌트 배지(알림만, 없으면 None — claude_warn 과 같은 송출 방식).
        msg["claude_model_tip"] = ap._model_tip if ap else None
        # M19: 그림자 /usage 세션·주간 한도(없으면 None).
        msg["usage_limits"] = server._usage
        # S6 T3: 실측 경과(초) — 클라가 stale 표기("N분 전 실측")에 쓴다. 시계 동기
        # 가정 없이 서버가 경과로 환산해 보낸다. 실측 없으면 None.
        uts = getattr(server, "_usage_ts", None)
        msg["usage_age_sec"] = (max(0, int(_time.time() - uts))
                                if uts is not None else None)
        # (usage_shown_seq = 인패널 /usage 자동 팝업 신호는 2026-06-17 제거 — §3.9)
        # M14c: 활성 패널 모델 배지(없으면 None) + 계정 식별자.
        msg["claude_model"] = (ap._claude_model
                               if ap and ap._claude else None)
        msg["claude_account"] = ap._claude_account if ap else None
        # footer 전체 표시용 비별칭 계정(폭 충분 시 전체, 아니면 클라가 별칭으로 폴백).
        # 로그·이벤트는 위 별칭(claude_account)만 쓴다 — 이 키는 클라 표시 전용.
        msg["claude_account_full"] = (
            getattr(ap, "_claude_account_full", None) if ap else None)
        msg["autoresume"] = bool(ap.autoresume) if ap else False
        msg["prompt_clear"] = bool(ap.prompt_clear_mode) if ap else False
        # 프롬프트 단위 클리어 큐(#4): 활성 패널에 쌓인 명령들(표시·목록용).
        msg["prompt_clear_queue"] = (list(ap.prompt_clear_queue) if ap else [])
        msg["auto_doc_clear"] = server.auto_doc_clear
        msg["auto_compact"] = server.auto_compact
        msg["auto_hardstop"] = server.auto_hardstop
        msg["auto_token_on_exit"] = server.auto_token_on_exit
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
                # M14c 모델 과선택 힌트 토글(설정 팝업 표시용 — 정적 옵션, full 시만).
                "claude_model_hint": server.claude_model_hint,
                # S6 T4 실측 한도 게이트 임계(설정 팝업 표시용)
                "usage_gate_session_pct": server.usage_gate_session_pct,
                "usage_gate_week_pct": server.usage_gate_week_pct,
                # §10-D 토큰 회계 진단 로그 토글(현재값 표시용 — 정적 옵션, full 시만).
                "token_debug": server.token_debug,
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
        # 사용자가 직접 대응(예: 손수 "계속" 입력) 중이면 무장된 자동 재시도도 거둔다 —
        # 안 거두면 잔상 에러 줄 때문에 발화직전 재확인을 통과해 "계속" 이 중복 주입된다(#9 H2).
        server._cancel_retry(pane)
        pane._retry_attempts = 0

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
        if action == "set_auto_token_on_exit":
            server.set_auto_token_on_exit(msg.get("value"))
            return "send_full"
        if action == "set_claude_auto_retry":
            server.set_claude_auto_retry(msg.get("value"))
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
        if action == "set_claude_model_hint":         # M14c 모델 과선택 힌트(알림만)
            server.set_claude_model_hint(msg.get("value"))
            return "broadcast"
        if action == "set_token_debug":               # §10-D 토큰 회계 진단 로그 토글
            server.set_token_debug(msg.get("value"))
            return "broadcast"                         # status 로 새 값 회신(:설정 표시)
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
        app.pane_claude = {}            # id -> {"claude","prompt",…}
        app._perm_zone = {}             # id -> (x0,x1,y) 권한모드 footer 클릭존
        app._remote_zone = {}           # id -> (x0,x1,y) 원격제어 표시 클릭존
        app._interrupt_zone = {}        # id -> (x0,x1,y) busy footer 'esc to interrupt' 클릭존
        # 헤더 상태/클릭존 글루(코어/clientwidgets 가 getattr 로 호출 — 없으면 no-op).
        app._update_claude = lambda pc: _update_claude(app, pc)
        app.interrupt_pane = lambda pid: _interrupt_pane(app, pid)
        app._toggle_remote_control = lambda pid: _toggle_remote_control(app, pid)
        app.open_remote_control = lambda pid: _open_remote_control(app, pid)
        from .clientrender import footer_zone_at
        app._footer_zone_at = lambda x, y: footer_zone_at(app, x, y)
        # Claude 팝업(Phase 2a) — 코어에서 이리로 이전. 인스턴스 메서드로 설치한다
        # (PytmuxApp 은 build_client_app 팩토리 안 지역 클래스라 동적 베이스 믹스인을
        # 못 써 ncd/_saver_* 와 같은 클로저 설치 패턴을 쓴다). 코어 클릭/ESC/자동팝업
        # 콜러는 getattr 가드로 호출하므로, 디렉토리 삭제 시 이 속성들이 없어 no-op.
        app.open_model_config = lambda: _open_model_config(app)
        app._apply_model_config = lambda res: _apply_model_config(app, res)
        app.open_perm_mode = lambda pane_id: _open_perm_mode(app, pane_id)
        app.open_usage_panel = lambda: _open_usage_panel(app)
        app.open_token_log = lambda initial=None: _open_token_log(app, initial)
        app.open_claude_warn_info = lambda: _open_warn_info(app)
        # (open_claude_usage_tree/_open_usage_tree 설치는 token-usage→token-log 통합
        #  (2026-06-12)으로 제거 — 상태줄 사용량 클릭·esc 포커스 Enter 는 이미
        #  open_token_log 를 부른다.)

    def pane_closing(self, server, pane):
        """패널 종료 직전(코어 servertree → pane_closing 훅) — 닫히는 Claude 패널의 확정
        토큰을 같은 계정 생존 패널로 이관한다(#20, S5 토큰 모듈화 T4). 동적 합성된 믹스인
        메서드로 위임 — 코어 servertree 는 토큰 누계를 모른다."""
        server._carry_tokens_on_close(pane)
        # 닫히는 패널에 무장된 자동재개/재시도 타이머를 거둬 닫힌 Pane 참조가 최대
        # 백오프 간격(최대 5분) 동안 살아있지 않게 한다(#9 M1 — _fire_* 의 pty 가드가
        # 오발화는 막지만 참조 누수는 남는다).
        server._cancel_resume(pane)
        server._cancel_retry(pane)

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
            # usagelog 로 시간/일/월·세션 집계해 팝업에 표시(라운드트립 없이 버킷/차원
            # 전환). Phase B: 버킷 전환용 N 건과 별개로, 정확한 **전체 이력 합**
            # (total_all)을 서버가 SQL 로 함께 보내, 이력이 N 을 넘어도 lifetime Σ 가
            # 과소표시되지 않게 한다. (계정별 합은 머신-로컬 표시로 전환돼 제거 — 2026-06-19.)
            from . import usagedb   # S5 T5: 플러그인 소속(물리 이전)
            conn = server._tokens_db_conn()
            recs = (usagedb.query_records(conn, limit=int(msg.get("limit", 5000)))
                    if conn is not None else [])
            total_all = usagedb.total_all(conn) if conn is not None else 0
            # 버킷(일/주/월) 전체 이력 집계용 일자별 합성 레코드(cap 무관). 클라가
            # usagelog.agg_view 에 먹여 옛 버킷이 안 잘리게 재구성한다(Phase B 완성).
            daily = usagedb.daily_breakdown(conn) if conn is not None else []
            # S6 T2: 대사(reconcile) 구간 — 실측 스냅샷 Δpct vs 스크랩 Σ. 진단
            # 전용 데이터라 표시는 TokenLogScreen [대사] 뷰만 소비한다.
            recon = usagedb.reconcile(conn) if conn is not None else []
            # §10-D: 세션 5h 한도 최대%(권위 /usage). 스크랩 Σ 가 5h 소비를 과소반영
            # 하므로 사용량 뷰가 '얼마나 썼나'를 이 값으로 보인다. daily=일자별(레거시
            # 조인용 유지), hourly=시각별(5h 비율은 시간 단위 뷰에 둔다 — 사용자 결정
            # 2026-06-17). day 뷰는 더는 5h% 열을 안 보이고 hour 뷰가 hourly_pct 로 보인다.
            daily_pct = usagedb.daily_limit_pct(conn) if conn is not None else {}
            hourly_pct = usagedb.hourly_limit_pct(conn) if conn is not None else {}
            # 1w%(주간 전체모델 한도) 시각별 — 5h% 옆 열(사용자 요청 2026-06-17).
            hourly_week = usagedb.hourly_week_pct(conn) if conn is not None else {}
            # 요청 2026-06-21: 현재 활성 패널의 claude 세션 id 를 함께 보내, [세션] 뷰가
            # 지금 보고 있는 세션 행을 하이라이트하게 한다. 0(=세션 없음)/예외는 None.
            active_sid = None
            try:
                win = sess.active_window
                ap = win.active_pane if win else None
                active_sid = getattr(ap, "_claude_session_id", None) or None
            except Exception:
                active_sid = None
            return {"t": "token_log", "records": recs,
                    "total_all": total_all,
                    "daily": daily, "reconcile": recon,
                    "daily_pct": daily_pct, "hourly_pct": hourly_pct,
                    "hourly_week_pct": hourly_week,
                    "active_session": active_sid}
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
        claude_rules 동기화, 패널별 Claude 상태(pane_claude) 갱신.

        (인패널 /usage 자동 팝업은 2026-06-17 제거 — §3.9. 사용자가 Claude 패널에서
        /usage 를 직접 띄워 보고 있는데 같은 내용을 전용 모달로 덮는 게 불필요·방해라서.
        수동 usage-panel/limits 명령과 그림자 /usage 질의·실측 캡처는 그대로 유지.)"""
        if "claude_rules" in msg:
            app._claude_rules = msg.get("claude_rules", "")
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

    def client_statusbar(self, app, status, segs, w, w0=None):
        """하단 상태줄 좌측에 Claude 세그먼트(모델·컨텍스트·토큰Σ·예산·카운트다운·경고)를
        그리고 클릭존을 채운다(코어 StatusBar._render_main 의 Claude 블록 이전). w0=들어오는
        누적 셀폭(P6) → render_segs 가 ux0/left 재합산을 생략하고 새 누적 폭을 반환한다."""
        from .clientstatus import render_segs
        return render_segs(status, segs, w, w0)

    # (client_status_tabs 훅 — 통합 상태 팝업의 '토큰 사용량' 탭 — 은 token-log 통합
    #  (2026-06-12)으로 제거. 통합 상태 팝업은 REC·서버 두 탭, 토큰은 token-log 팝업.)

    def handle_command(self, app, c, args):
        # 팝업(명령 전용 + 클릭/ESC 겸용) — 클릭/렌더 경로의 open_* 는 아직 코어에 있어
        # (Phase 2 이전 예정) 여기선 그 메서드를 호출한다. 디렉토리를 지우면 명령 경로는
        # 사라지지만 클릭 경로는 Phase 2 까지 코어에 남는다(단계적 추출).
        if c in ("claude-rules", "rules", "startup-rules"):
            self._open_rules(app)
        elif c in ("token-saver", "claude-settings", "token-settings"):
            self._open_saver(app)
        elif c in ("token-log", "tokens-log", "token-usage-log",
                   "token-usage", "tokens"):
            # token-usage 는 token-log 로 통합(2026-06-12) — 별칭으로만 남는다.
            app.open_token_log()
        elif c in ("usage-panel", "usage-limits", "limits"):
            app.open_usage_panel()
        elif c in ("model", "model-config", "claude-model"):
            app.open_model_config()
        # 토글/주입 명령(서버로 전송, on/off 없으면 서버가 토글)
        elif c in ("auto-resume", "autoresume"):
            app.send_cmd("set_autoresume", value=_onoff(args))
        elif c in ("auto-resume-message", "autoresume-message"):
            app.send_cmd("set_autoresume", msg=" ".join(args))
        elif c in ("claude-usage", "usage", "refresh-usage"):
            # M19 그림자 /usage 질의: 서버가 숨은 claude 를 띄워 실 세션/주간 한도를
            # 긁어온다(사용자 화면 무간섭, ~수초). 회신은 status 로 반영.
            app.send_cmd("refresh_usage")
            app.display_message(i18n.t("ccmsg.usage_querying"), 4.0)
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
        elif c in ("auto-token-on-exit", "auto-token", "token-on-exit"):
            app.send_cmd("set_auto_token_on_exit", value=_onoff(args))
        elif c in ("auto-retry", "retry"):
            app.send_cmd("set_claude_auto_retry", value=_onoff(args))
        elif c in ("claude-auto-mode", "auto-mode"):
            app.send_cmd("set_claude_auto_mode", value=_onoff(args))
        elif c in ("model-hint", "model-advice"):
            app.send_cmd("set_claude_model_hint", value=_onoff(args))
        elif c in ("token-debug", "token-dbg"):
            # §10-D 토큰 회계 진단 로그 토글(서버 opts.json 영속, 즉시 발효). 진단용이라
            # 평시엔 거의 안 만지므로 결과를 짧게 알린다(다른 토글은 설정 팝업이 상태를
            # 보여 주지만 이건 팝업이 없다). 무인자 토글은 결과값을 동기적으로 모르므로
            # 의도(켜기/끄기/토글)만 알린다 — 권위 현재값은 status 로 따라온다.
            _v = _onoff(args)
            app.send_cmd("set_token_debug", value=_v)
            app.display_message(
                "토큰 진단 로그 켜짐" if _v is True else
                "토큰 진단 로그 꺼짐" if _v is False else "토큰 진단 로그 토글")
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
                [i18n.t("ccmsg.pc_queue_empty")]
            app.push_screen(InfoScreen(lines, title=i18n.t("ccmsg.pc_queue_title")))
        elif args[0].lower() in ("-c", "clear", "--clear"):
            app.send_cmd("pc_queue_clear")
            app.display_message(i18n.t("ccmsg.pc_cleared"))
        else:
            app.send_cmd("pc_queue_add", cmd=" ".join(args).strip())

    def _open_rules(self, app):
        # #27: Claude 시작 규칙 편집 팝업. 저장하면 서버 opts.json 에 영속하고, 새 Claude
        # 세션 또는 /clear 직후 첫 idle 에 프롬프트로 자동 주입한다.
        from .screens import RulesEditScreen

        def _saved(text):
            if text is not None:
                app.send_cmd("set_claude_rules", text=text)
                app.display_message(i18n.t("ccmsg.rules_saved") if text.strip()
                                    else i18n.t("ccmsg.rules_cleared"))
        app.push_screen(RulesEditScreen(getattr(app, "_claude_rules", "")), _saved)

    def _open_saver(self, app):
        from .screens import ClaudeSaverScreen
        app.push_screen(ClaudeSaverScreen())


PLUGIN = _ClaudeCodePlugin()
