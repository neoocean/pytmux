"""claude-prompt-history 플러그인 — Claude 패널에 입력한 프롬프트 히스토리.

기존(코어+claude-code 에 통합돼 있던) 프롬프트 히스토리 기능을 **독립 플러그인**으로
재구성한 것. 동작:
  * Claude CLI 실행 후 입력한 프롬프트를 **패널마다** 시간순 저장(서버 server_input 훅).
  * `:` 명령 프롬프트에서 `prompt-history`(pane_scoped) 를 작성하는 동안, 대상(활성)
    패널 외곽선 안쪽 위에서부터 **직전 프롬프트를 미리보기 패널**로 표시(client_render).
    프롬프트가 멀티라인이면 1행부터 시작해 여러 줄로 늘리되 **최대 N행**(설정 가능).
  * 미리보기 패널이 뜬 상태에서 Enter(=명령 실행) → **프롬프트 팝업**으로 전환. 팝업에서
    ↑↓ 로 이전 프롬프트를 살펴보고, Enter 로 그 프롬프트가 입력된 스크롤백 위치로
    화면을 올린 뒤 팝업을 닫는다(서버가 스크롤백 텍스트 검색으로 점프). Esc 로도 닫는다.
  * 미리보기 행수 N 은 명령(`prompt-history-lines N`)과 팝업(+/−)에서 설정·영속(서버 opts).

설계 결정(사용자 확정 2026-06-12):
  - 기존 claude-code 의 always-on 단일행 헤더를 **대체**한다(Phase 2 에서 그 헤더 제거).
  - 미리보기 패널은 **명령 프롬프트 작성 중에만** transient 하게 뜬다(서버 행 예약 없이
    client_render 오버레이). 평소엔 안 보인다.
  - 점프는 **플러그인 내부 스크롤백 텍스트 검색**으로 — 코어 model.py(앵커/hist_total)
    무수정. deque 회전으로 사라진 프롬프트는 점프 불가(graceful).

delete-to-disable: 이 디렉토리를 지우면 prompt-history 명령·미리보기·팝업이 조용히
사라지고, 히스토리 추적도 멈춘다(코어/claude-code 무에러). Claude 존재 판정은
claude-code 가 세우는 `pane._claude` 를 getattr 로 **약하게** 읽어, claude-code 가 없으면
아무 프롬프트도 기록하지 않는다(하드 참조 금지).

무게: 이 __init__ 은 textual 을 최상단에서 import 하지 않는다(서버도 plugins.load 로
읽는다). 화면(screen)·렌더(render)·서버 로직(server)은 실제로 쓸 때 지연 import 한다."""
from __future__ import annotations

from pytmuxlib import i18n

COMMANDS = [
    ("prompt-history", "Claude 프롬프트 히스토리 팝업(`:`서 작성 중엔 직전 프롬프트 "
                       "미리보기; 별칭 prompts·ph)", "Claude"),
    ("prompt-history-lines", "프롬프트 미리보기 패널 최대 행수 설정 "
                             "(prompt-history-lines <1-3>; 별칭 ph-lines)", "Claude"),
]
NOARG = {"prompt-history", "prompts", "ph"}
PANE_SCOPED = {"prompt-history", "prompts", "ph"}   # 작성 중 대상 패널에 미리보기

_OPEN_ALIASES = ("prompt-history", "prompts", "ph")
_LINES_ALIASES = ("prompt-history-lines", "ph-lines")
_DEFAULT_LINES = 3
_MIN_LINES, _MAX_LINES = 1, 3

# 미리보기 행수 picker(옵션 모달) — ←→ 로 1~3 선택.
COMMAND_OPTIONS = {
    "prompt-history-lines": [{"key": "n", "label": "행수",
                              "choices": ["1", "2", "3"]}],
    "ph-lines": [{"key": "n", "label": "행수", "choices": ["1", "2", "3"]}],
}

i18n.register({
    "ko": {
        "cmd.prompt-history": "Claude 프롬프트 히스토리 팝업(작성 중 미리보기; 별칭 prompts·ph)",
        "cmd.prompt-history-lines": "프롬프트 미리보기 최대 행수 (prompt-history-lines <1-3>)",
        "ph.popup_title": "프롬프트 히스토리",
        "ph.popup_sub": "↑↓ 이동 · Enter 그 위치로 점프 · +/− 미리보기 {n}행 · Esc 닫기",
        "ph.empty": "(저장된 프롬프트가 없습니다 — Claude 에 프롬프트를 입력해 보세요)",
        "ph.multiline_mark": " ⏎",
        "ph.jump_fail": "그 프롬프트가 스크롤백에 없습니다(회전/재시작으로 사라짐)",
        "ph.lines_set": "프롬프트 미리보기: {n}행",
    },
    "en": {
        "cmd.prompt-history": "Claude prompt-history popup (preview while typing; alias prompts·ph)",
        "cmd.prompt-history-lines": "Max prompt-preview rows (prompt-history-lines <1-3>)",
        "ph.popup_title": "Prompt history",
        "ph.popup_sub": "↑↓ move · Enter jump to position · +/− preview {n} rows · Esc close",
        "ph.empty": "(no saved prompts — type a prompt into Claude first)",
        "ph.multiline_mark": " ⏎",
        "ph.jump_fail": "That prompt is no longer in scrollback (rotated out / restarted)",
        "ph.lines_set": "Prompt preview: {n} rows",
    },
})


def _clamp_lines(n) -> int:
    try:
        return max(_MIN_LINES, min(_MAX_LINES, int(n)))
    except (TypeError, ValueError):
        return _DEFAULT_LINES


class _PromptHistoryPlugin:
    name = "claude-prompt-history"
    commands = COMMANDS
    noarg = NOARG
    completions = []
    command_options = COMMAND_OPTIONS
    pane_scoped = PANE_SCOPED

    # ---- Pane 상태(서버·재시작 직렬화) ----
    def pane_init(self, pane):
        pane._ph_history = []       # 시간순 제출 프롬프트(멀티라인 보존, 한 항목=한 제출)
        pane._ph_inbuf = ""         # 입력 누적(claude-code _inbuf 와 별개 — 충돌 방지)
        pane._ph_sent = None        # status 디바운스(직전 전송 tail 슬라이스)

    def pane_reset(self, pane):
        pane._ph_inbuf = ""         # respawn: 입력 버퍼만 비움(히스토리는 보존 가치 적어 유지)

    def pane_serialize(self, pane) -> dict:
        return {"_ph_history": list(getattr(pane, "_ph_history", []))[-100:]}

    def pane_restore(self, pane, data):
        if "_ph_history" in (data or {}):
            pane._ph_history = list(data["_ph_history"])

    # ---- 서버 옵션(미리보기 행수 영속) ----
    def server_opts_init(self, server, opts):
        server._ph_max_lines = _clamp_lines(opts.get("ph_max_lines", _DEFAULT_LINES))

    def server_opts_serialize(self, server) -> dict:
        return {"ph_max_lines": getattr(server, "_ph_max_lines", _DEFAULT_LINES)}

    # ---- 서버 런타임 ----
    def server_input(self, server, pane, data):
        from .server import track_input
        track_input(pane, data)

    def server_paste(self, server, pane, data):
        from .server import track_input
        track_input(pane, data)

    def server_status(self, server, sess, win, msg, full):
        from .server import status_fields
        status_fields(server, win, msg, full)

    def server_command(self, server, client, sess, action, msg):
        if action == "set_ph_max_lines":
            server._ph_max_lines = _clamp_lines(msg.get("n", _DEFAULT_LINES))
            server._save_opts()
            return "broadcast"          # 모든 클라에 새 행수 status 반영
        if action == "ph_scroll_to":
            from .server import scroll_to_prompt
            ok = scroll_to_prompt(server, sess, int(msg.get("index", 0)))
            # 점프 성공 시 스크롤 변경을 모든 클라에 반영(pane.scroll 은 공유 서버 상태).
            # 실패(스크롤백서 못 찾음)면 무동작.
            return "broadcast" if ok else "handled"
        return None

    # ---- 클라이언트 ----
    def attach_client(self, app):
        app.ph_panes = {}                       # id -> {"id","h":[프롬프트…]}
        app.ph_max_lines = _DEFAULT_LINES

        def open_prompt_history(pane_id=None):
            from .screen import PromptHistoryScreen
            pid = pane_id if pane_id is not None else app.layout.get("active")
            entry = app.ph_panes.get(pid) or {}
            hist = list(entry.get("h") or [])
            app.push_screen(PromptHistoryScreen(pid, hist, app.ph_max_lines))
        app.open_prompt_history = open_prompt_history

    def handle_command(self, app, c, args):
        if c in _OPEN_ALIASES:
            app.open_prompt_history(app.layout.get("active"))
            return True
        if c in _LINES_ALIASES:
            n = next((int(a) for a in args if str(a).lstrip("-").isdigit()), None)
            if n is not None:
                app.send_cmd("set_ph_max_lines", n=_clamp_lines(n))
            return True
        return False

    def client_status(self, app, msg):
        if "ph_max_lines" in msg:
            app.ph_max_lines = _clamp_lines(msg["ph_max_lines"])
        if "ph_panes" not in msg:
            return
        prev = getattr(app, "ph_panes", {})
        new = {}
        for e in msg["ph_panes"]:
            pid = e["id"]
            if "h" not in e:                    # 디바운스로 빠진 항목은 직전 유지
                p = prev.get(pid)
                if p is not None and "h" in p:
                    e = {**e, "h": p["h"]}
            new[pid] = e
        app.ph_panes = new

    def client_render(self, app, cells, W, H):
        from .render import draw_preview
        draw_preview(app, cells, W, H)


PLUGIN = _PromptHistoryPlugin()
