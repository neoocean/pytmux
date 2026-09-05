"""스크롤 힌트를 「늘 붙는 것 / 스크롤될 때만」으로 가른다(pytmux-478 ⑵).

# 무엇이 결함이었나

글 판의 꼬리줄이 내용이 **다 들어가는데도** `↑↓ 스크롤` 이라고 말했다 — 할 수 없는
조작을 광고하는 것이다. 그 문구는 서버가 짓는 스펙의 `hint` 한 문자열이었고, 서버는
그 판이 누구 화면에서 몇 줄로 그려지는지 모른다.

⇒ 서버는 두 토막을 **따로 싣기만** 하고, 붙일지는 **제 뷰포트를 아는 클라**가 정한다.

⛔ **한쪽 클라에서만 떼지 않는다** — GUI 에서만 지우면 정본과 다른 말을 하게 되고,
그것이 [[pytmux-185]] 가 결함으로 세는 갈림이다. 그래서 갈라 두는 자리는 **스펙**이고,
그 값이 실제로 나오는지를 아래가 전수로 잰다.
"""
import harness
from harness import make_app, server_only, teardown, wait_until, wait_mounted
from pytmuxlib import i18n


async def _with_app(coro, size=(60, 20)):
    srv, task, sock = await server_only()
    app = make_app(sock)
    try:
        async with app.run_test(size=size) as pilot:
            assert await wait_until(pilot, lambda: app.layout.get("panes")), \
                "첫 레이아웃이 안 왔다"
            await coro(app, pilot, srv)
    finally:
        await teardown(srv, task, sock)


def _spec(text, hint="Esc 닫기", scroll_hint="↑↓ 스크롤"):
    return {"t": "plugin_screen", "id": "t478", "kind": "text",
            "title": "판", "hint": hint, "scroll_hint": scroll_hint,
            "text": text, "note": "", "rows": [], "keys": {}}


def _tail(screen):
    """판이 실제로 그리는 **마지막 글줄**. 위젯 트리에서 읽는다(값이 아니라 화면)."""
    from textual.widgets import Label, ListView
    lv = screen.query_one(ListView)
    for item in reversed(list(lv.children)):
        text = str(item.query_one(Label).content).strip()
        if text:
            return text
    return ""


async def test_a_panel_that_fits_does_not_advertise_scrolling():
    """⛔ 제보의 화면이다. 네 줄짜리 글이 20행 판에 다 들어가는데 꼬리줄이 「↑↓ 스크롤」
    이라고 말하면, 사용자는 눌러 보고 **아무 일도 안 일어나는 것**을 본다."""
    async def body(app, pilot, srv):
        app._dispatch(_spec("".join(f"줄{n}\n" for n in range(1, 5))))
        screen = await wait_mounted(pilot, child="ListView")
        tail = _tail(screen)
        assert tail == "Esc 닫기", f"다 들어가는 판이 스크롤을 광고한다: {tail!r}"
    await _with_app(body)


async def test_a_panel_that_overflows_does_advertise_scrolling():
    """반대쪽. 안 붙이기만 하면 **넘치는 판에서도 안 뜨는** 반쪽 수정이 되고, 그건
    「스크롤되는데 그 말을 안 하는」 새 결함이다(부정 단언만 있는 오라클의 함정)."""
    async def body(app, pilot, srv):
        app._dispatch(_spec("".join(f"줄{n}\n" for n in range(1, 200))))
        screen = await wait_mounted(pilot, child="ListView")
        assert await wait_until(pilot, lambda: "↑↓ 스크롤" in _tail(screen)), \
            f"넘치는 판이 스크롤을 안 알린다: {_tail(screen)!r}"
        # 늘 붙는 토막이 사라지면 안 된다 — 가른 것은 **스크롤 토막 하나**다.
        assert "Esc 닫기" in _tail(screen), _tail(screen)
    await _with_app(body)


async def test_the_scroll_piece_goes_last_so_the_rest_never_moves():
    """⚠ **뒤에 붙인다.** 가운데에 끼우면 토막이 나타나고 사라질 때마다 `Esc 닫기` 가
    좌우로 움직인다 — 같은 판을 두 번 열었을 뿐인데 꼬리줄이 딴 데 있는 것으로 보인다."""
    async def body(app, pilot, srv):
        app._dispatch(_spec("".join(f"줄{n}\n" for n in range(1, 200)),
                            hint="r 토글 · Esc 닫기"))
        screen = await wait_mounted(pilot, child="ListView")
        assert await wait_until(pilot, lambda: "↑↓ 스크롤" in _tail(screen)), _tail(screen)
        assert _tail(screen).startswith("r 토글 · Esc 닫기"), \
            f"늘 붙는 토막이 앞자리를 안 지켰다: {_tail(screen)!r}"
    await _with_app(body)


async def test_a_spec_without_the_field_behaves_exactly_as_before():
    """점진 채택 — 칸을 모르는 판은 종전대로 힌트를 통째로 늘 붙인다. 스펙을 내는
    플러그인 전부를 한 CL 에 고치지 않아도 되게 하는 자리다."""
    async def body(app, pilot, srv):
        msg = _spec("한 줄\n", hint="↑↓ 스크롤 · Esc 닫기")
        del msg["scroll_hint"]
        app._dispatch(msg)
        screen = await wait_mounted(pilot, child="ListView")
        assert _tail(screen) == "↑↓ 스크롤 · Esc 닫기", _tail(screen)
    await _with_app(body)


async def test_the_three_text_panels_actually_split_their_hint():
    """⛔ **호출부까지 잰다.** `_spec` 에 칸을 더하고 아무도 안 쓰면 화면은 그대로다 —
    이 저장소가 두 번 겪은 공허 통과다. 그래서 세 판이 **실제로 내는 값**을 본다.

    셋이 이 이슈가 말한 「진짜 글 판」 전수다(pytmux-478 ⑴ 이 전수로 세어 남긴 목록):
    `claude-remote-control` · `mdir` 뷰 · `p4changes` 상세.
    """
    from pytmuxlib.plugins.mdir import PLUGIN as mdir  # noqa: F401  (로드 확인)
    import importlib
    spec = importlib.import_module("pytmuxlib.plugins.claude-code.screenspec")
    rc = spec._rc_spec(None, None, 7)
    assert rc["kind"] == "text"
    assert rc["scroll_hint"] == i18n.t("pscreen.rc_scroll_hint") != ""
    assert "스크롤" not in rc["hint"] and "scroll" not in rc["hint"].lower(), (
        f"늘 붙는 토막에 스크롤이 남았다: {rc['hint']!r}")


async def test_the_always_on_half_never_promises_scrolling():
    """가른 뜻이 지켜지는지를 **문구로** 못박는다. 늘 붙는 토막에 「스크롤」이 남아 있으면
    가른 보람이 없다 — 다 들어가는 판에서 그 낱말이 계속 뜬다.

    ⚠ 셋만 본다(이 CL 이 옮긴 것). 나머지 판은 아직 통째로 붙이는 쪽이라 여기 안 든다 —
    점진 채택이고, 그 사실이 위 `..._behaves_exactly_as_before` 가 지키는 것이다."""
    bad = []
    for key in ("pscreen.rc_hint", "mdir.view_hint", "p4cl.detail_back"):
        for loc in ("ko", "en"):
            i18n.set_locale(loc)
            text = i18n.t(key)
            if "스크롤" in text or "scroll" in text.lower():
                bad.append(f"{key}[{loc}] = {text!r}")
    i18n.set_locale("ko")
    assert not bad, "늘 붙는 토막이 아직 스크롤을 광고한다:\n  " + "\n  ".join(bad)
