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
            # ★ **executor 로** 나간다. 이 훑기는 `~/.claude/projects` 의 jsonl 을 전부
            #   읽어 이 머신에서 실측 **5.5초**다(세션 300개) — 루프에서 하면 그 동안
            #   모든 패널의 출력이 멎는다(단일 스레드 asyncio). 종전에는 그대로 루프에서
            #   했고, 증상이 "리줌 목록을 열면 pytmux 가 잠깐 먹통"이라 원인이 이 훅으로
            #   안 보였다(2026-08-02 화면 스펙을 붙이며 시간을 재다 드러났다).
            return self._list_sessions()
        if action == "claude_resume_session":
            self._resume(server, sess, msg.get("session_id"), msg.get("cwd"))
            return None
        return None

    def _list_sessions(self):
        import asyncio
        from . import sessions
        return asyncio.get_event_loop().run_in_executor(
            None, lambda: {"t": "claude_sessions",
                           "sessions": sessions.list_sessions(limit=_LIST_LIMIT)})

    def _resume(self, server, sess, session_id, cwd):
        """그 세션의 원래 디렉토리에서 새 탭을 열고 리줌 명령을 주입한다. 했으면 True.

        **정본과 네이티브 클라가 같이 부른다** — 한쪽만 고치면 "GUI 에서만 cd 가 안
        된다" 같은 것이 생긴다(사용자 결정: cd 후 리줌)."""
        cmd = resume_command(session_id)
        if cmd is None:
            return False                    # 위생 실패 — 아무것도 안 함
        server.new_window(sess, path=cwd)
        win = sess.active_window
        pane = win.active_pane if win else None
        try:
            if pane is not None and pane.pty is not None:
                pane.pty.write(cmd.encode("utf-8"))
        except OSError:
            pass
        # 새 탭이 보이도록 세션 전 클라에 전체 동기화 방송.
        server._broadcast_session(sess)
        return True

    # ---- 서버 측: 화면 스펙(Tier C) ----
    #
    # 정본은 위 `handle_message` 에서 자기 Textual 피커를 띄운다. 네이티브 클라는
    # 파이썬을 못 읽으므로 **무엇을 그릴지**를 스펙으로 준다 — 목록의 자료도 리줌하는
    # 손도 위와 **같은 함수**라, 두 클라에서 다른 세션이 보이거나 다른 디렉토리에서
    # 열릴 자리가 없다.
    def plugin_screen(self, server, sess, req):
        do = req.get("do")
        if do == "open":
            if req.get("name") not in _ALIASES:
                return None                 # 내 이름이 아니다
            return self._open_spec(req.get("state") or {})
        if req.get("id") != "claude-resume":
            return None
        if do == "resume":
            sid = str(req.get("input") or "")
            # cwd 는 **목록을 만들 때 적어 둔 것**을 쓴다(그 클라의 화면 상태 —
            # 설계 Tier C · P5). 여기서 다시 훑으면 수백 개 jsonl 을 두 번 읽는다.
            cwd = (((req.get("state") or {}).get("claude-resume") or {})
                   .get("cwds") or {}).get(sid)
            self._resume(server, sess, sid, cwd)
            return {"t": "plugin_screen_close", "id": "claude-resume"}
        if do == "close":
            return {"t": "plugin_screen_close", "id": "claude-resume"}
        return None

    async def _open_spec(self, state):
        """세션 목록 스펙. 훑기는 **executor 로** 나간다 — `~/.claude/projects` 전체를
        읽는 일이라 루프에서 하면 그동안 모든 패널이 멎는다."""
        import time
        found = (await self._list_sessions())["sessions"]
        # 고른 줄로 리줌할 때 쓸 cwd 를 적어 둔다(위 `resume` 주석).
        state.setdefault("claude-resume", {})["cwds"] = {
            s["id"]: s.get("cwd") for s in found}

        def when(mtime):
            try:
                return time.strftime("%m-%d %H:%M", time.localtime(mtime or 0))
            except (ValueError, OSError, TypeError):
                return ""

        return {
            "t": "plugin_screen", "id": "claude-resume", "kind": "list",
            "title": "Claude 세션 리줌",
            "hint": "(↑↓ 이동 · Enter 새 탭에서 리줌 · Esc 닫기)",
            # `key` 는 그 줄의 **뜻**(세션 id)이다 — 자리로 가리키면 목록이 바뀔 때
            # 엉뚱한 세션이 열린다.
            "rows": [{"key": s["id"], "label": s.get("title") or s["id"],
                      "cols": [s.get("project") or "", when(s.get("mtime"))]}
                     for s in found],
            "selected": 0,
            "keys": {"enter": "resume"},
            "note": "" if found else "이 머신에 리줌할 세션이 없습니다",
        }


PLUGIN = _ClaudeResumePlugin()
