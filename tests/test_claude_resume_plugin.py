"""claude-resume 플러그인 배선 회귀 — 마일스톤 2.

서버측(handle_server_request)은 페이크 서버로 단위 검증(실 pty/claude 안 띄움).
클라측(명령/메시지/피커 화면)은 헤드리스 Textual 앱으로 검증."""
import importlib

import harness  # noqa: F401  (sys.path 주입)

cr = importlib.import_module("pytmuxlib.plugins.claude-resume")
sess_mod = importlib.import_module("pytmuxlib.plugins.claude-resume.sessions")

_UUID = "3c7eadab-718e-4202-ae0d-e98519c729db"


# ---- 서버측: handle_server_request ----

class _FakePty:
    def __init__(self):
        self.written = b""

    def write(self, b):
        self.written += b


class _FakePane:
    def __init__(self):
        self.pty = _FakePty()


class _FakeWin:
    def __init__(self, pane):
        self.active_pane = pane


class _FakeSess:
    active_window = None


class _FakeServer:
    def __init__(self):
        self.pane = _FakePane()
        self.win = _FakeWin(self.pane)
        self.new_window_args = None
        self.broadcasted = False

    def new_window(self, sess, path=None):
        self.new_window_args = (sess, path)
        sess.active_window = self.win        # 새 탭의 활성 패널이 생긴 것을 흉내

    def _broadcast_session(self, sess):
        self.broadcasted = True


def test_list_sessions_request_returns_sessions(monkeypatch):
    fake = [{"id": _UUID, "cwd": "D:\\x", "title": "t", "mtime": 1.0,
             "project": "office/x"}]
    monkeypatch.setattr(sess_mod, "list_sessions", lambda limit=None: fake)
    resp = cr.PLUGIN.handle_server_request(object(), _FakeSess(),
                                           "claude_list_sessions", {})
    assert resp == {"t": "claude_sessions", "sessions": fake}


def test_resume_request_opens_tab_in_cwd_and_injects():
    srv = _FakeServer()
    sess = _FakeSess()
    out = cr.PLUGIN.handle_server_request(
        srv, sess, "claude_resume_session",
        {"session_id": _UUID, "cwd": "D:\\p4\\office\\rx"})
    assert out is None                                   # 회신 없음(방송으로 갱신)
    assert srv.new_window_args == (sess, "D:\\p4\\office\\rx")   # 세션 cwd 로 새 탭
    assert srv.pane.pty.written == f"claude --resume {_UUID}\r".encode()
    assert srv.broadcasted


def test_resume_request_rejects_bad_session_id():
    srv = _FakeServer()
    out = cr.PLUGIN.handle_server_request(
        srv, _FakeSess(), "claude_resume_session",
        {"session_id": "bad; rm -rf /", "cwd": None})
    assert out is None
    assert srv.new_window_args is None                   # 새 탭 안 열림
    assert srv.pane.pty.written == b""                   # 주입 없음
    assert not srv.broadcasted


def test_unknown_action_returns_none():
    assert cr.PLUGIN.handle_server_request(object(), _FakeSess(),
                                           "something_else", {}) is None


# ---- 클라측: 명령/메시지/화면 ----

async def test_command_requests_list_and_message_opens_picker():
    from harness import make_app, server_only, teardown
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            sent = []
            app.send_cmd = lambda c, **kw: sent.append((c, kw))
            # 명령 → 서버에 목록 요청
            assert cr.PLUGIN.handle_command(app, "cr", []) is True
            assert ("claude_list_sessions", {}) in sent
            assert getattr(app, "_want_claude_sessions", False) is True
            # 응답 메시지 → 피커 화면 push
            ok = cr.PLUGIN.handle_message(app, {"t": "claude_sessions", "sessions": [
                {"id": _UUID, "cwd": "D:\\x", "title": "제목", "mtime": 1.0,
                 "project": "office/x"}]})
            assert ok is True
            await pilot.pause(0.2)
            assert app.screen_stack[-1].__class__.__name__ == "ClaudeResumeScreen"
            assert getattr(app, "_want_claude_sessions", True) is False
    finally:
        await teardown(srv, task, sock)


async def test_picker_enter_resumes_selected_session():
    from harness import make_app, server_only, teardown
    screen_mod = importlib.import_module("pytmuxlib.plugins.claude-resume.screen")
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            sent = []
            app.send_cmd = lambda c, **kw: sent.append((c, kw))
            app.display_message = lambda *a, **k: None
            sessions = [
                {"id": _UUID, "cwd": "D:\\p4\\office\\rx", "title": "리줌할 세션",
                 "mtime": 1.0, "project": "office/rx"},
                {"id": "11111111-2222-3333-4444-555555555555", "cwd": "D:\\y",
                 "title": "다른 세션", "mtime": 2.0, "project": "x/y"},
            ]
            app.push_screen(screen_mod.ClaudeResumeScreen(sessions))
            await pilot.pause(0.2)
            scr = app.screen_stack[-1]
            scr._resume(0)                       # 첫 행 리줌
            await pilot.pause(0.1)
            assert ("claude_resume_session",
                    {"session_id": _UUID, "cwd": "D:\\p4\\office\\rx"}) in sent
            # 리줌 후 화면 닫힘
            assert not any(s.__class__.__name__ == "ClaudeResumeScreen"
                           for s in app.screen_stack)
    finally:
        await teardown(srv, task, sock)


async def test_picker_empty_list_shows_none_and_esc_closes():
    from harness import make_app, server_only, teardown
    screen_mod = importlib.import_module("pytmuxlib.plugins.claude-resume.screen")
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screen_mod.ClaudeResumeScreen([]))
            await pilot.pause(0.2)
            scr = app.screen_stack[-1]
            assert scr.query_one("#crnone")          # 빈 안내 표시
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not any(s.__class__.__name__ == "ClaudeResumeScreen"
                           for s in app.screen_stack)
    finally:
        await teardown(srv, task, sock)
