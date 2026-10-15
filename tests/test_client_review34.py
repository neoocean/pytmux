"""pytmux-34 전수 검수 — 파이썬 Textual 클라 지적 17건의 회귀 오라클.

검수 원문은 스크래치의 `review34_client.md`(저장소 밖). 각 시험은 그 고침을 되돌리면
실패하도록 짰고, 가능한 곳은 **호출부까지** 잰다(값 만드는 헬퍼만 재면 「호출 제거」
뮤테이션에 공허 통과한다 — 이 저장소 실측 2회).

되돌리면 실패해야 하는 오라클:
  · #1  make_style 캐시 상한            → test_make_style_cache_is_bounded
  · #2  layout.popup 기하 무검증         → test_a_hostile_popup_is_trimmed · test_the_popup_dispatch_uses_the_trimmer
  · #3  화면 세그먼트·커서·제목줄 무검증  → test_hostile_screen_frames_do_not_stop_the_composite
                                          · test_hostile_titlebars_and_titles_are_trimmed
                                          · test_a_failing_pending_composite_is_contained
  · #4  notice.detail 형식 문자열        → test_notice_detail_unknown_key_is_not_a_format_string
  · #5  묻지 않은 selection 이 클립보드로 → test_an_unsolicited_selection_is_dropped
                                          · test_copy_range_reply_echoes_the_request_token
  · #6  재접속 hello 에 플러그인 caps 누락 → test_reconnect_hello_carries_plugin_caps_and_closes_old_writer
  · #7  확인 팝업·검색 머리말 마크업      → test_confirm_and_search_headline_are_literal
  · #8  run-shell/if-shell 루프 정지      → test_run_shell_does_not_block_the_loop
  · #9  plugin_screen 무한 push           → test_a_plugin_screen_flood_keeps_one_panel
  · #10 _prompt_buf 정리 누락             → test_prompt_buf_is_pruned_with_the_layout
  · #11 screen-delta 행 무한 증가         → test_screen_delta_rows_are_capped
  · #12 set-titles 죽은 옵션              → test_set_titles_writes_a_sanitized_osc2
  · #13 display-panes 타이머 겹침          → test_display_panes_timer_is_restarted
  · #14 재접속 때 옛 writer 미종료         → (#6 시험이 같이 잰다)
  · #15 하드코딩 한국어                    → test_english_locale_has_no_hangul_in_these_paths
  · #16 rss 가 최고 수위                   → test_client_diag.test_rss_is_the_current_value_on_windows_and_linux
  · #17 .lang 비-ASCII 기동 크래시         → test_a_non_ascii_lang_file_is_ignored
"""
import ast
import asyncio
import os
import re
import tempfile
import time

import harness  # noqa: F401  (러너 위생 · 경로 설정)
from harness import patched, wait_mounted, wait_until

from pytmuxlib import clientcmd, clientconn, clientutil, i18n

_HANGUL = re.compile(r"[가-힣]")


def _active_rect(app):
    pane = app.layout.get("active")
    return pane, [p for p in app.layout["panes"] if p["id"] == pane][0]


def _row_text(app, y):
    return "".join(c[0] for c in app.view._cells[y])


# ── #1 ────────────────────────────────────────────────────────────────────────
def test_make_style_cache_is_bounded():
    clientutil._style_cache.clear()
    for i in range(30000):
        clientutil.make_style({"f": "#%06x" % i, "b": "#%06x" % (i * 7 % 0xFFFFFF)})
    assert len(clientutil._style_cache) <= clientutil._STYLE_CACHE_MAX, \
        len(clientutil._style_cache)
    # 적중은 지킨다 — 같은 dict 는 같은 Style 객체다.
    d = {"f": "red", "bo": True}
    assert clientutil.make_style(d) is clientutil.make_style(dict(d))
    # 모양 방어(#3): 해시 불가 값·dict 아닌 값은 기본 스타일로 접는다(예외 없음).
    assert clientutil.make_style({"f": ["x"]}) is clientutil.DEFAULT_STYLE
    assert clientutil.make_style(["f", "x"]) is clientutil.DEFAULT_STYLE


# ── #2 ────────────────────────────────────────────────────────────────────────
def test_a_hostile_popup_is_trimmed():
    from pytmuxlib.client import _sane_layout
    good = {"id": 9, "x": 2, "y": 1, "w": 20, "h": 8, "cx": 3, "cy": 2,
            "cw": 18, "ch": 6, "title": "pop"}
    base = {"t": "layout", "panes": [], "dividers": []}
    assert _sane_layout({**base, "popup": good})["popup"] == good
    for bad in ({**good, "y": -10**9, "h": 10**9 + 5},
                {**good, "w": 10**12},
                {**good, "x": "a"},
                {**good, "id": None},
                {k: v for k, v in good.items() if k != "cw"},
                "not-a-dict", 1):
        assert "popup" not in _sane_layout({**base, "popup": bad}), bad
    # 제목만 틀리면 제목만 뺀다.
    out = _sane_layout({**base, "popup": {**good, "title": ["x"]}})
    assert out["popup"]["id"] == 9 and "title" not in out["popup"]


async def test_the_popup_dispatch_uses_the_trimmer():
    """★ 호출부 — 디스패치가 다듬은 popup 을 담고, 합성이 예외 없이 돈다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        app._request_composite = lambda: None
        good = dict(app.layout)
        app._dispatch({**good, "popup": {"id": 1, "x": "a", "y": 0, "w": 5, "h": 5,
                                         "cx": 1, "cy": 1, "cw": 3, "ch": 3}})
        assert "popup" not in app.layout, app.layout.get("popup")
        app._composite()                       # 되돌리면 TypeError
        # 상한 안의 큰 음수 기하도 화면 안으로 잘라 곧 끝난다.
        app._dispatch({**good, "popup": {"id": 1, "x": -4000, "y": -4000, "w": 4000,
                                         "h": 4000, "cx": 0, "cy": 0, "cw": 1, "ch": 1}})
        t0 = time.perf_counter()
        app._composite()
        assert time.perf_counter() - t0 < 2.0

    await _with_app(body)


# ── #3 ────────────────────────────────────────────────────────────────────────
async def test_hostile_screen_frames_do_not_stop_the_composite():
    from test_client import _with_app

    async def body(app, pilot, srv):
        app._request_composite = lambda: None
        pane, rect = _active_rect(app)
        frames = [
            [[["AAA", {"f": ["unhashable"]}]], [["BBB", {}]]],           # 해시 불가 스타일
            [[123], [["BBB", {}]]],                                      # 세그먼트가 int
            [[["AAA", "style-not-dict"], [5, {}], "seg"], [["BBB", {}]]],  # 글이 int·문자열 세그
            [[["AAA", {}, "extra"]], [["BBB", {}]]],                     # 3-튜플
            "rows-not-a-list",
        ]
        for rows in frames:
            for cursor in ("ab", [1], [1, "x"], None, [0, 0]):
                app._dispatch({"t": "screen", "pane": pane, "rows": rows,
                               "cursor": cursor})
                app._composite()                # 되돌리면 언팩/해시/타입 오류
        # 나머지는 그대로 그려진다(정상 행은 살아남는다).
        app._dispatch({"t": "screen", "pane": pane,
                       "rows": [[["AAA", {"f": ["x"]}]], [123, ["BBB", {}]]],
                       "cursor": "zz"})
        app._composite()
        assert "AAA" in _row_text(app, rect["y"]), _row_text(app, rect["y"])
        assert "BBB" in _row_text(app, rect["y"] + 1)
        rows, cursor = app.pane_content[pane]
        assert cursor is None
        assert rows[1] == [["BBB", {}]], rows

    await _with_app(body)


async def test_hostile_titlebars_and_titles_are_trimmed():
    from test_client import _with_app

    async def body(app, pilot, srv):
        app._request_composite = lambda: None
        good = dict(app.layout)
        panes = [dict(p, title=["not", "str"]) for p in good["panes"]]
        app._dispatch({**good, "panes": panes,
                       "titlebars": [{"x": 0, "y": 0, "w": 5},                 # 제목 없음
                                     {"x": 0, "y": "a", "w": 5, "title": "t"},
                                     {"x": 0, "y": 0, "w": 10**9, "title": "t"},
                                     {"x": 0, "y": 0, "w": 6, "title": "ok"}]})
        assert app.layout["titlebars"] == [{"x": 0, "y": 0, "w": 6, "title": "ok"}]
        assert all("title" not in p for p in app.layout["panes"])
        app._composite()                       # 되돌리면 KeyError/AttributeError

    await _with_app(body)


async def test_a_failing_pending_composite_is_contained():
    """마지막 방어선 — 가드 밖 콜백에서 난 예외는 세고 삼킨다(화면이 영영 멎지 않게)."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        real = app._composite

        def boom():
            raise TypeError("hostile")
        app._composite = boom
        try:
            before = getattr(app, "_composite_errors", 0)
            app._do_pending_composite()        # 되돌리면 여기서 TypeError
            assert app._composite_errors == before + 1
            assert app._composite_pending is False
        finally:
            app._composite = real

    await _with_app(body)


# ── #4 ────────────────────────────────────────────────────────────────────────
async def test_notice_detail_unknown_key_is_not_a_format_string():
    from test_client import _with_app

    async def body(app, pilot, srv):
        shown = []
        app.display_message = lambda text, **kw: shown.append((text, kw))
        t0 = time.perf_counter()
        app._dispatch({"t": "notice", "key": "rnotice.zz_unknown",
                       "text": "실패: {why}", "secs": 1e9,
                       "detail": {"key": "zz.not.in.catalog",
                                  "text": "{a:>20000000} {a.__class__} {a}",
                                  "kw": {"a": "W"}}})
        assert time.perf_counter() - t0 < 1.0
        assert shown, "notice 가 안 떴다"
        text, kw = shown[-1]
        assert len(text) < 3000, len(text)
        assert "{a:>20000000}" in text and "{a.__class__}" in text, text
        assert text.endswith(" W"), text
        assert kw["secs"] == 60.0, kw          # 유지 시간 상한
        # 카탈로그 키는 종전대로 치환한다(대조군).
        assert i18n.has("msg.paste_image_path")
        ok = app._notice_text({"key": "msg.copied_chars", "kw": {"n": 1},
                               "detail": {"key": "msg.paste_image_path",
                                          "kw": {"path": "/tmp/p.png"}}})
        assert "1" in ok
        why = app._notice_text({"key": "zz", "text": "{why}",
                                "detail": {"key": "msg.paste_image_path",
                                           "kw": {"path": "/tmp/p.png"}}})
        assert "/tmp/p.png" in why, why
        # detail·kw 가 dict 가 아니어도 죽지 않는다.
        app._notice_text({"key": "zz", "text": "x", "kw": [1], "detail": "str"})
        app._dispatch({"t": "notice", "text": "x", "secs": "nan?"})
        app._dispatch({"t": "notice", "text": "x", "secs": float("nan")})
        assert shown[-1][1]["secs"] is None   # None = 등급 기본값(display_message 가 채운다)

    await _with_app(body)


# ── #5 ────────────────────────────────────────────────────────────────────────
async def test_an_unsolicited_selection_is_dropped():
    from test_client import _with_app

    async def body(app, pilot, srv):
        copied = []
        app.copy_selection_text = lambda text, *a: copied.append(text)
        app._selection_wait_until = 0.0
        app._dispatch({"t": "selection", "text": "someone else's secret"})
        assert copied == [], "묻지 않은 회신이 클립보드로 갔다"
        # 물으면 한 번만 받는다 — send_cmd 가 표시를 세운다(플러그인 길).
        sent = []
        real_write = app.writer
        app.writer = object()                  # send_cmd 가 쓰려 들지 않게 막는다
        try:
            with patched(asyncio, create_task=lambda coro: (coro.close(), sent.append(1))):
                app.send_cmd("copy_range", pane=1, y0=0, x0=0, y1=0, x1=1)
        finally:
            app.writer = real_write
        assert sent, "send_cmd 가 아무것도 안 보냈다"
        app._dispatch({"t": "selection", "text": "mine"})
        app._dispatch({"t": "selection", "text": "again"})
        assert copied == ["mine"], copied
        # 마우스 드래그의 정문(clientwidgets)도 표시를 세운다.
        app._expect_selection()
        app._dispatch({"t": "selection", "text": "drag"})
        assert copied == ["mine", "drag"], copied

    await _with_app(body)


async def test_copy_range_reply_echoes_the_request_token():
    """서버 짝 — 릴레이된 copy_range 의 회신은 요청 클라 토큰을 돌려준다(라우팅 근거)."""
    from harness import server_only, teardown
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        pane.feed(b"row0\r\nrow1\r\n")
        sent = []

        class _C:
            session = sess
            remote_view = None
            id = 1

        async def _cap(client, obj):
            sent.append(obj)
        with patched(srv, _send_to=_cap):
            await srv._cmd_copy_range(_C(), sess, {"pane": pane.id, "y0": 0, "x0": 0,
                                                  "y1": 1, "x1": 79,
                                                  "_req_token": 4242})
            await srv._cmd_copy_range(_C(), sess, {"pane": pane.id, "y0": 0, "x0": 0,
                                                  "y1": 1, "x1": 79})
        assert sent[0]["t"] == "selection" and sent[0]["_req_token"] == 4242, sent[0]
        assert "_req_token" not in sent[1], sent[1]
    finally:
        await teardown(srv, task, sock)


# ── #6 · #14 ─────────────────────────────────────────────────────────────────
class _FakeWriter:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


async def test_reconnect_hello_carries_plugin_caps_and_closes_old_writer():
    from test_client import _with_app
    from pytmuxlib.protocol import CLIENT_CAPS

    async def body(app, pilot, srv):
        plugin_caps = set(app.plugins.client_caps())
        assert plugin_caps - set(CLIENT_CAPS), \
            "플러그인 caps 가 없어 이 시험이 공허하다(blocks 플러그인이 있어야 한다)"
        hellos = []
        new_w = _FakeWriter()

        async def _open(_path):
            return object(), new_w

        async def _write(_w, msg):
            hellos.append(msg)
        real_r, real_w = app.reader, app.writer
        old = _FakeWriter()
        app.writer = old
        try:
            with patched(clientconn.ipc, open_connection=_open), \
                    patched(clientconn, write_msg=_write):
                ok = await app._connect_and_hello(1)
        finally:
            app.reader, app.writer = real_r, real_w
        assert ok
        assert old.closed, "재접속이 옛 writer 를 안 닫았다(#14)"
        assert hellos and hellos[0]["t"] == "hello"
        assert set(hellos[0]["caps"]) == set(CLIENT_CAPS) | plugin_caps, hellos[0]["caps"]
        # 최초 hello 와 같은 조립이다(한 벌).
        assert hellos[0]["caps"] == app._hello_msg()["caps"]

    await _with_app(body)


# ── #7 ────────────────────────────────────────────────────────────────────────
async def test_confirm_and_search_headline_are_literal():
    from test_client import _with_app
    from textual.widgets import Label
    from pytmuxlib.clientscreens import ConfirmScreen, SearchResultsScreen

    async def body(app, pilot, srv):
        raw = "notes [/x] [/] [red]y[@click=app.quit]z"
        app.push_screen(ConfirmScreen(raw))
        scr = await wait_mounted(pilot, ConfirmScreen)
        msg = scr.query_one("#confirmmsg", Label)
        assert raw in str(msg.render()), str(msg.render())
        app.pop_screen()
        await wait_until(pilot, lambda: not isinstance(app.screen, ConfirmScreen))
        app.push_screen(SearchResultsScreen({"query": "[/INST]", "items": []}))
        scr = await wait_mounted(pilot, SearchResultsScreen)
        assert isinstance(app.screen, SearchResultsScreen), "compose 가 죽었다"
        box = scr.query_one("#sresbox")
        assert "[/INST]" in box._border_title.plain, box._border_title
        app.pop_screen()

    await _with_app(body)


# ── #8 ────────────────────────────────────────────────────────────────────────
async def test_run_shell_does_not_block_the_loop():
    from test_client import _with_app
    from pytmuxlib.clientscreens import InfoScreen

    async def body(app, pilot, srv):
        seen_env = []

        def slow(cmd, env=None):
            seen_env.append(dict(env or {}))
            time.sleep(1.0)
            return "hello from shell\n", 0
        with patched(clientcmd, _shell_capture=slow):
            t0 = time.perf_counter()
            # 훅 환경(PYTMUX_*)은 부를 때 떠 둔다 — 스레드가 늦게 읽어도 남아 있어야 한다.
            app.hooks["zz-test-hook"] = "run-shell -b 'echo hi'"
            app._fire_hook("zz-test-hook", env={"PYTMUX_ZZ": "1"})
            assert time.perf_counter() - t0 < 0.5, "run-shell 이 루프를 막았다"
            assert "PYTMUX_ZZ" not in os.environ
            await wait_mounted(pilot, InfoScreen, timeout=6.0)
            assert isinstance(app.screen, InfoScreen), "결과 판이 안 떴다"
            assert seen_env and seen_env[0].get("PYTMUX_ZZ") == "1", seen_env
            app.pop_screen()
            # if-shell 도 워커에서 — then 은 결과가 온 뒤 메인 루프에서 돈다.
            ran = []
            real_run = app._run_command

            def spy(line, _depth=0):
                if line == "zz-then":
                    ran.append(line)
                    return None
                return real_run(line, _depth)
            app._run_command = spy
            try:
                t0 = time.perf_counter()
                app._if_shell("true", "zz-then")
                assert time.perf_counter() - t0 < 0.5, "if-shell 이 루프를 막았다"
                assert await wait_until(pilot, lambda: ran == ["zz-then"], timeout=6.0)
            finally:
                app._run_command = real_run
        app.hooks.pop("zz-test-hook", None)

    await _with_app(body)


# ── #9 ────────────────────────────────────────────────────────────────────────
async def test_a_plugin_screen_flood_keeps_one_panel():
    from test_client import _with_app
    from pytmuxlib.clientscreens import InfoScreen

    async def body(app, pilot, srv):
        for i in range(100):
            app._dispatch({"t": "plugin_screen", "id": "zz", "kind": "text",
                           "title": "t%d" % i, "text": "line %d" % i})
        await wait_until(pilot, lambda: isinstance(app.screen, InfoScreen))
        await pilot.pause(0.2)
        n = sum(1 for s in app.screen_stack if getattr(s, "_from_plugin_screen", False))
        assert n == 1 and len(app.screen_stack) <= 2, (n, len(app.screen_stack))
        assert app.screen._title == "t99", app.screen._title   # 마지막 판이 보인다

    await _with_app(body)


# ── #10 · #11 ────────────────────────────────────────────────────────────────
async def test_prompt_buf_is_pruned_with_the_layout():
    from test_client import _with_app

    async def body(app, pilot, srv):
        app._request_composite = lambda: None
        pane, _ = _active_rect(app)
        app._prompt_buf = {pane: "abc", 987654: "z" * 4000}
        app._dispatch(dict(app.layout))
        assert 987654 not in app._prompt_buf, list(app._prompt_buf)
        assert app._prompt_buf.get(pane) == "abc"

    await _with_app(body)


async def test_screen_delta_rows_are_capped():
    from test_client import _with_app
    from pytmuxlib.client import _LAYOUT_MAX_DIM

    async def body(app, pilot, srv):
        app._request_composite = lambda: None
        pane, _ = _active_rect(app)
        app.pane_content[pane] = ([], None)
        app._dispatch({"t": "screen-delta", "pane": pane,
                       "rows": [[i, [["x", {}]]] for i in range(10000)]
                       + [["bad", []], "junk", [1]], "cursor": None})
        rows, _ = app.pane_content[pane]
        assert len(rows) <= _LAYOUT_MAX_DIM, len(rows)

    await _with_app(body)


# ── #12 ───────────────────────────────────────────────────────────────────────
async def test_set_titles_writes_a_sanitized_osc2():
    from test_client import _with_app

    async def body(app, pilot, srv):
        wrote = []
        app._term_write = lambda seq: wrote.append(seq)
        app.set_titles = True
        app.title_fmt = "zz-\x1b]52;c;x\x07-#S-\x9b2J"
        # ★ 호출부 — 서버의 정기 status 로 써져야 한다(헬퍼만 부르는 시험은 공허하다).
        assert await wait_until(pilot, lambda: any(s.startswith("\x1b]2;zz-") for s in wrote),
                                timeout=6.0), wrote
        seq = [s for s in wrote if s.startswith("\x1b]2;zz-")][0]
        body_ = seq[len("\x1b]2;"):-1]
        assert seq.endswith("\x07")
        assert not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in body_), repr(seq)
        # 같은 제목은 다시 안 쓴다(status 는 초마다 온다).
        n = len(wrote)
        app._apply_term_title(app._last_term_title)
        assert len(wrote) == n
        app.set_titles = False

    await _with_app(body)


# ── #13 ───────────────────────────────────────────────────────────────────────
async def test_display_panes_timer_is_restarted():
    from test_client import _with_app

    async def body(app, pilot, srv):
        app._enter_display()
        app._exit_display()
        await pilot.pause(1.0)
        app._enter_display()
        await pilot.pause(0.7)                 # 앞 타이머(1.5초)가 살아 있었다면 닫혔다
        assert app.mode == "display", app.mode
        app._exit_display()

    await _with_app(body)


# ── #15 ───────────────────────────────────────────────────────────────────────
async def test_english_locale_has_no_hangul_in_these_paths():
    from test_client import _with_app

    async def body(app, pilot, srv):
        prev = i18n.get_locale()
        i18n.set_locale("en")
        try:
            m = {"reexec_supported": True, "has_sessions": True, "serialize_ok": True,
                 "panes": 2, "panes_with_fd": 1}
            for os_ in ("windows", "posix", None):
                _safe, checks = clientutil._restart_check_eval(
                    dict(m, server_os=os_), True)
                for _ok, lbl in checks:
                    assert not _HANGUL.search(lbl), lbl
            assert "1/2" in checks[3][1], checks[3]
            for k in ("msg.reconnecting", "msg.reconnecting_auto", "msg.full_restart"):
                assert i18n.has(k) and not _HANGUL.search(i18n.t(k)), k
            shown = []
            app.display_message = lambda text, **kw: shown.append(text)
            app._term_write = lambda seq: None
            app.apply_option("alt-scroll", "on")
            app.apply_option("alt-scroll", "off")
            assert len(shown) == 2 and not any(_HANGUL.search(s) for s in shown), shown
        finally:
            i18n.set_locale(prev)
        # clientconn 의 알림 호출부에 한국어 리터럴이 다시 들어오면 운다.
        src = open(clientconn.__file__, encoding="utf-8").read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "display_message":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        assert not _HANGUL.search(sub.value), (node.lineno, sub.value)

    await _with_app(body)


# ── #17 ───────────────────────────────────────────────────────────────────────
def test_a_non_ascii_lang_file_is_ignored():
    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "s.sock")
        with open(sock + ".lang", "w", encoding="utf-8") as f:
            f.write("한")
        assert i18n.load_persisted(sock) is None
        with open(sock + ".lang", "w", encoding="ascii") as f:
            f.write("en")
        assert i18n.load_persisted(sock) == "en"      # 대조군
