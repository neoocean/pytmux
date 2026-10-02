"""스크롤백이 빈 패널에서 프롬프트 점프는 «못 뛴다»고 말한다 (pytmux-544).

Claude Code 의 fullscreen 렌더러(대체 화면) 패널은 pytmux 스크롤백에 아무것도 안 남긴다
(서버는 그 패널의 `top`·`scr` 를 0 으로 보낸다). 종전에는 그 패널에서 ⑴ `esc ctrl+↑/↓` 가
스크롤 모드로 들어가 키를 가두고 아무 일도 안 했고 ⑵ prompt-history 판의 Enter 점프가
조용히 실패했다(`ph.jump_fail` 은 등록만 되고 쓰이지 않았다).

되돌리면 실패해야 하는 오라클:
  · 정본 키 갈래를 지우면(**호출부**) → test_esc_ctrl_up_on_an_empty_scrollback_says_why
  · 스크롤백이 있는 패널까지 막으면 → test_esc_ctrl_up_with_scrollback_still_jumps (대조군)
  · ph 실패를 다시 조용히 두면 → test_a_failed_prompt_history_jump_is_told_to_the_client
"""
import harness  # noqa: F401  (러너 위생 · 경로 설정)


async def test_esc_ctrl_up_on_an_empty_scrollback_says_why():
    async def body(app, pilot, srv):
        sent, shown = [], []
        app.send_cmd = lambda action, **kw: sent.append(action)
        real = app.display_message

        def spy(text, *a, **kw):
            shown.append(text)
            return real(text, *a, **kw)
        app.display_message = spy
        aid = app.layout.get("active")
        app.pane_top[aid] = 0
        app.pane_scroll[aid] = 0
        await pilot.press("escape", "ctrl+up")
        assert app.mode != "scroll", "뛸 곳이 없는데 스크롤 모드에 가뒀다"
        assert "jump_prompt" not in sent, sent
        assert any("스크롤백" in t for t in shown), shown

    await harness_app(body)


async def test_esc_ctrl_up_with_scrollback_still_jumps():
    """대조군 — 스크롤백이 있으면 종전대로 스크롤 모드 + jump_prompt."""
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append(action)
        aid = app.layout.get("active")
        app.pane_top[aid] = 40
        app.pane_scroll[aid] = 0
        await pilot.press("escape", "ctrl+up")
        assert app.mode == "scroll"
        assert "jump_prompt" in sent, sent

    await harness_app(body)


async def harness_app(body):
    from test_client import _with_app
    await _with_app(body)


async def test_a_failed_prompt_history_jump_is_told_to_the_client():
    """서버 쪽 — 못 뛰면 요청한 클라에게 알림이 간다. 대체 화면이면 그 까닭이다."""
    import importlib
    from harness import server_only, teardown
    from test_plugin_prompt_history import _claude_pane
    ph = importlib.import_module("pytmuxlib.plugins.claude-prompt-history")
    srv, task, sock = await server_only()
    try:
        pane = _claude_pane()
        pane.feed(b"nothing to see here\r\n")
        pane._ph_history = ["hello prompt that is nowhere"]

        class Sess:
            class active_window:
                active_pane = pane
        sess = Sess()
        sent = []

        async def fake_send(client, msg):
            sent.append(msg)
        srv._send_to = fake_send
        # 정본 경로(server_command) — 실패는 «handled» 이고 알림이 따로 간다.
        r = ph.PLUGIN.server_command(srv, object(), sess, "ph_scroll_to", {"index": 0})
        assert r == "handled"
        await harness.wait_for(lambda: sent)
        assert sent[-1].get("key") == "ph.jump_fail", sent
        # GUI 경로(Tier C do=jump) — 닫기와 알림을 함께 돌려준다. 대체 화면이면 그 까닭.
        pane.alt_active = True
        resp = ph.PLUGIN.plugin_screen(srv, sess, {"id": "prompt-history", "do": "jump",
                                                   "input": "0", "state": {}})
        assert isinstance(resp, list), resp
        assert resp[0]["t"] == "plugin_screen_close"
        assert resp[1].get("key") == "ph.jump_alt", resp
    finally:
        await teardown(srv, task, sock)
