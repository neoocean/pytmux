"""claude-resume 플러그인 — 이 머신의 Claude Code 세션 목록을 보여주고, 하나를 골라
Enter 하면 새 탭을 열어 그 세션을 리줌한다(사용자 요청 2026-06-18, HANDOFF §10-F).

구성(다른 플러그인과 동일한 계약):
  - `__init__.py` : 코어와의 계약(명령 메타·디스패치·메시지/요청 핸들러). 가벼움.
  - `sessions.py` : 세션 열거(순수 로직 — textual 무관, 서버에서 import).
  - `screen.py`   : 리줌 피커 모달(textual). 클라에서 실제로 열 때 지연 import.

흐름: 클라 `claude-resume` 명령 → 서버에 `claude_list_sessions` 요청 → 서버가
`sessions.list_sessions()`(이 머신 ~/.claude/projects 전체)로 회신 → 클라가 피커를 연다.
행 선택+Enter → 서버에 `claude_resume_session`(session_id·cwd) → 서버가 그 cwd 로 새 탭을
열고(`new_window`) 새 패널 셸에 `claude --resume <id>` 를 주입(Enter 포함)한 뒤 방송한다.
세션 파일이 서버 측에 있으므로 열거·리줌을 **서버에서** 수행한다(remote-attach 안전 +
새 탭 패널 id race 회피).

delete-to-disable: 이 디렉토리를 지우면 `claude-resume` 명령·서버 회신이 모두 사라지고
코어는 그대로 동작한다. 무게: 이 모듈은 textual 을 최상단 import 하지 않는다(서버도 읽음)."""
from __future__ import annotations

import re

from pytmuxlib import i18n

# 명령 메타데이터 — 코어가 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐 쓴다.
COMMANDS = [
    ("claude-resume", "이 머신의 Claude Code 세션 목록 — ↑↓ 탐색·Enter 새 탭에서 리줌 "
                      "(별칭 claude-sessions·cr)", "Claude"),
]
NOARG = {"claude-resume", "claude-sessions", "cr"}
_ALIASES = ("claude-resume", "claude-sessions", "cr")

# 세션 id 위생 — 셸로 주입하므로 영숫자/.-_ 만 허용(uuid 형식). 그 외면 리줌 거부.
_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# 피커에 보일 최대 세션 수(아주 오래된 세션까지 무한정 나열하지 않게). 최신순 상위 N.
_LIST_LIMIT = 300


def resume_command(session_id, enter: str = "\r"):
    """새 탭 셸에 주입할 리줌 명령 문자열. session_id 가 위생 통과 못 하면 None
    (셸 인젝션 방지). 셸 무관하게 `claude --resume <id>` + Enter."""
    sid = (session_id or "").strip()
    if not _ID_OK.match(sid):
        return None
    return f"claude --resume {sid}{enter}"


# cmd.<name> 번역 — ko 는 COMMANDS 에서 자동 시드(원본=ko), en 보강 + 피커 화면 문자열.
i18n.register({
    "ko": dict(
        [(f"cmd.{n}", d) for n, d, *_ in COMMANDS]
        + [("cresume.title", "Claude 세션 리줌"),
           ("cresume.none", "(이 머신에 리줌할 세션이 없습니다)"),
           ("cresume.hint", "↑↓ 이동 · Enter 새 탭에서 리줌 · Esc 닫기"),
           ("cresume.opening", "새 탭에서 세션 리줌: {title}")]),
    "en": {
        "cmd.claude-resume": "List this machine's Claude Code sessions — ↑↓ browse · "
                             "Enter resume in a new tab (alias claude-sessions·cr)",
        "cresume.title": "Resume Claude session",
        "cresume.none": "(no resumable sessions on this machine)",
        "cresume.hint": "↑↓ move · Enter resume in new tab · Esc close",
        "cresume.opening": "Resuming in new tab: {title}",
    },
})


class _ClaudeResumePlugin:
    name = "claude-resume"
    description = "Claude Code 세션 리줌 피커(목록→새 탭에서 리줌)"
    category = "Claude"
    commands = COMMANDS
    noarg = NOARG
    completions = []            # 명령 이름은 레지스트리가 자동 추가
    command_options = {}

    # ---- 클라이언트 측 ----
    def attach_client(self, app):
        """피커를 여는 진입점 — 명령/메뉴가 호출한다. 서버에 목록을 요청하고, 응답
        (t==claude_sessions)이 오면 handle_message 가 화면을 연다."""
        def request_claude_sessions():
            app._want_claude_sessions = True
            app.send_cmd("claude_list_sessions")
        app.request_claude_sessions = request_claude_sessions

    def handle_command(self, app, c, args):
        if c in _ALIASES:
            req = getattr(app, "request_claude_sessions", None)
            if req is not None:
                req()
            return True
        return False

    def handle_message(self, app, msg):
        if msg.get("t") == "claude_sessions":
            if not getattr(app, "_want_claude_sessions", False):
                return True                 # 요청 안 했는데 온 응답은 무시(방어)
            app._want_claude_sessions = False
            from .screen import ClaudeResumeScreen
            app.push_screen(ClaudeResumeScreen(msg.get("sessions") or []))
            return True
        return False

    # ---- 서버 측 ----
    def handle_server_request(self, server, sess, action, msg):
        if action == "claude_list_sessions":
            from . import sessions
            return {"t": "claude_sessions",
                    "sessions": sessions.list_sessions(limit=_LIST_LIMIT)}
        if action == "claude_resume_session":
            cmd = resume_command(msg.get("session_id"))
            if cmd is None:
                return None                 # 위생 실패 — 아무것도 안 함
            # 세션의 원래 디렉토리에서 새 탭을 연다(사용자 결정: cd 후 리줌).
            server.new_window(sess, path=msg.get("cwd"))
            win = sess.active_window
            pane = win.active_pane if win else None
            try:
                if pane is not None and pane.pty is not None:
                    pane.pty.write(cmd.encode("utf-8"))
            except OSError:
                pass
            # 새 탭이 보이도록 세션 전 클라에 전체 동기화 방송.
            server._broadcast_session(sess)
            return None
        return None


PLUGIN = _ClaudeResumePlugin()
