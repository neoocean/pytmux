"""서버 프레임은 믿을 수 없다 — 파이썬 클라 보안 검수 2026-09-04 후속(pytmux-34).

문서 `pytmux/client-reports-2026-09-04-python-tui-client-security-review` 의 발견을 막은
자리를 하나씩 잰다. 서버는 로컬 데몬뿐 아니라 **페더레이션 상류**(다른 머신)의 글을
릴레이하므로, 클라는 받은 글·기하·형식을 그대로 쓰지 않는다.

되돌리면 실패해야 하는 오라클:
  · S1 줄 필터를 안 달면(**호출부**) → test_the_control_filter_is_installed_first
  · 필터가 ESC·C1 을 그대로 두면 → test_a_hostile_row_reaches_the_terminal_without_escapes
  · 피커 셋이 마크업으로 읽으면 → test_pickers_show_server_text_literally
  · layout 기하를 안 다듬으면 → test_a_hostile_layout_is_trimmed_before_compositing
  · 탭바가 index 타입을 안 보면 → test_a_tab_without_an_integer_index_is_not_drawn
  · 모르는 키의 기본 글을 str.format 하면 → test_a_server_format_string_cannot_reach_attributes
  · Tier C 크기 상한이 없으면 → test_a_huge_text_panel_is_capped
"""
import harness  # noqa: F401  (러너 위생 · 경로 설정)
from harness import wait_mounted, wait_until

from pytmuxlib import i18n
from pytmuxlib.clientutil import neutralize_controls

_HOSTILE = "A\x1b]52;c;cHduZWQ=\x07B\x9b2JC\x1b[?1049h"


def test_neutralize_keeps_the_width_and_drops_every_control():
    out = neutralize_controls(_HOSTILE)
    assert len(out) == len(_HOSTILE)           # 같은 칸 수(격자가 안 밀린다)
    assert not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in out), repr(out)
    assert out.startswith("A␛]52;c;"), out  # ESC → ␛ (눈에 보이는 기호)


async def test_the_control_filter_is_installed_first():
    """★ 호출부 — 함수만 있고 앱에 안 달면 공허하다. 맨 앞이어야 다른 필터가 원본을 안 본다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        assert type(app._filters[0]).__name__ == "StripControlChars", app._filters

    await _with_app(body)


async def test_a_hostile_row_reaches_the_terminal_without_escapes():
    """패널 행에 실린 ESC·C1 이 렌더된 줄 → 앱의 줄 필터를 지나면 남지 않는다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        pane = app.layout.get("active")
        rect = [p for p in app.layout["panes"] if p["id"] == pane][0]
        app.pane_content[pane] = ([[[_HOSTILE, {}]]], None)
        app._composite()
        y = rect["y"]
        strip = app.view.render_line(y)
        segs = list(strip)
        assert any("\x1b" in s.text for s in segs), "장면이 위험 글을 안 실었다(대조군)"
        from textual.color import Color
        for f in app.get_line_filters():
            segs = f.apply(segs, Color(0, 0, 0))
        text = "".join(s.text for s in segs)
        assert "\x1b" not in text and "\x9b" not in text and "\x07" not in text, repr(text)

    await _with_app(body)


async def test_pickers_show_server_text_literally():
    """`[/x]` 는 MarkupError 로 compose 를 죽이고 `[@click=…]` 은 동작 링크가 된다(S2)."""
    from test_client import _with_app
    from textual.widgets import Label
    from pytmuxlib.clientscreens import (ChooseBufferScreen, ChooseLayoutScreen,
                                         MergeRemoteTabScreen)

    async def body(app, pilot, srv):
        cases = [
            (ChooseBufferScreen([{"i": 0, "preview": "[/x] ls"}]), "[/x] ls"),
            (ChooseLayoutScreen(["[@click=app.quit]work"]), "[@click=app.quit]work"),
            (MergeRemoteTabScreen([{"i": 0, "name": "[b]bold[/b]"}]), "[b]bold[/b]"),
        ]
        for screen, raw in cases:
            app.push_screen(screen)
            name = type(screen).__name__
            await wait_mounted(pilot, name)
            assert type(app.screen).__name__ == name, f"{name} 가 안 떴다(compose 가 죽었다)"
            shown = " ".join(str(lb.render()) for lb in app.screen.query(Label))
            assert raw in shown, (name, shown)
            app.pop_screen()
            await wait_until(pilot, lambda: type(app.screen).__name__ != name)

    await _with_app(body)


def test_a_hostile_layout_is_trimmed_before_compositing():
    from pytmuxlib.client import _sane_layout
    msg = {"t": "layout", "cols": 100000, "rows": "x", "active": 1,
           "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 20, "box": [0, 0, "a", 1]},
                     {"id": 2, "x": "x", "y": 0, "w": 10, "h": 10},
                     {"id": 3, "x": 0, "y": 0, "w": 10**9, "h": 10},
                     "not-a-pane"],
           "dividers": [{"x": 0, "y": 0, "w": 1, "h": None}]}
    out = _sane_layout(msg)
    assert "cols" not in out and "rows" not in out, out
    assert [p["id"] for p in out["panes"]] == [1], out["panes"]
    assert "box" not in out["panes"][0]
    assert out["dividers"] == []
    # 원본은 안 건드린다(사본이다).
    assert msg["cols"] == 100000 and len(msg["panes"]) == 4


async def test_the_layout_dispatch_uses_the_trimmer():
    """★ 호출부 — 다듬는 함수만 있고 디스패치가 안 부르면 공허하다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        good = dict(app.layout)
        # ⛔ 합성은 막아 둔다 — 다듬기가 빠진 회귀에서 이 시험이 정말로 10^12 칸을 지어
        #    상자를 멈춰 세웠다(뮤테이션 확인 때 실측). 재는 것은 「디스패치가 다듬은 값을
        #    담는가」 하나다. 합성의 안전은 그 값 위에서 성립한다.
        app._request_composite = lambda: None
        app._dispatch({**good, "cols": 10**6, "rows": 10**6})
        assert app.layout.get("cols") is None and app.layout.get("rows") is None

    await _with_app(body)


async def test_a_tab_without_an_integer_index_is_not_drawn():
    from test_client import _with_app

    async def body(app, pilot, srv):
        app.tabbar.tabs = [{"index": "x", "name": "bad"},
                           {"index": 0, "name": ["not", "str"], "active": True}]
        labels = app.tabbar._labels()             # 터지면 안 된다
        assert len(labels) == 1, labels

    await _with_app(body)


async def test_a_server_format_string_cannot_reach_attributes():
    """카탈로그에 없는 키면 서버의 기본 글이 형식 문자열이 된다(S4) — `{이름}` 만 채운다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        out = app._notice_text({"key": "zz.not.in.catalog",
                                "text": "x {why.__class__} {why:>10000000} {why}",
                                "kw": {"why": "W"}})
        assert len(out) < 3000, len(out)
        assert "{why.__class__}" in out and out.endswith(" W"), out
        # 카탈로그 키는 종전대로 우리 글로 채운다(대조군).
        assert i18n.has("msg.paste_image_path")
        ok = app._notice_text({"key": "msg.paste_image_path", "kw": {"path": "/tmp/p.png"}})
        assert "/tmp/p.png" in ok, ok

    await _with_app(body)


async def test_a_huge_text_panel_is_capped():
    from test_client import _with_app

    async def body(app, pilot, srv):
        app._open_plugin_text({"kind": "text", "title": "t",
                               "text": "\n".join("line %d" % i for i in range(60000))})
        await wait_mounted(pilot, "InfoScreen")
        lines = app.screen._lines
        assert len(lines) <= 5001, len(lines)
        assert lines[-1] == "…"

    await _with_app(body)
