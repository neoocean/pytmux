"""블록 고르기(pytmux-469 · 449 ⑴) — 정본 클라가 블록을 **고르고 강조하고 복사한다**.

# 이 시험이 무엇을 붙드나

이 표면은 GUI 에 먼저 섰고([[pytmux-18]]), 갈림 대장이 그 줄을 **할 일**로 들고 있었다.
[[pytmux-185]] 가 GUI 의 최소 요건으로 못박은 셋이 여기서는 반대 방향으로 걸린다 —
정본이 GUI 와 **같게 굴어야** 한다: 키 반응 · 취소 조건 · 포커스 이동.

⛔ **값을 만드는 함수만 재면 안 된다.** 그 값을 화면에 붙이는 호출을 지워도 통과하는
시험을 이 저장소가 두 번 겪었다(공허 통과). 그래서 아래는 `_composite` 를 실제로 돌려
**셀이 반전됐는지**를 보고, 복사는 **서버로 나간 명령**을 본다.
"""
import harness
from harness import make_app, server_only, teardown, wait_until
from pytmuxlib.plugins.blocks.segment import row_span


async def _with_app(coro, size=(60, 20)):
    srv, task, sock = await server_only()
    app = make_app(sock)
    try:
        async with app.run_test(size=size) as pilot:
            # 첫 레이아웃이 올 때까지 — 고정 대기는 느린 러너에서 플레이크다(대기 규약).
            assert await wait_until(pilot, lambda: app.layout.get("panes")), \
                "첫 레이아웃이 안 왔다"
            await coro(app, pilot, srv)
    finally:
        await teardown(srv, task, sock)


def _seed(app, pane=None, blocks=None, top=0, scr=0):
    """그 패널에 블록 목록을 심고 좌표를 세운다. 심은 패널 id 를 돌려준다."""
    pane = pane if pane is not None else app.layout.get("active")
    app._dispatch({"t": "blocks", "pane": pane,
                   "blocks": blocks if blocks is not None else
                   [{"cmd": "ls", "state": "done", "exit": 0, "start": 0, "end": 3},
                    {"cmd": "make", "state": "running", "start": 4}]})
    app.pane_top[pane] = top
    app.pane_scroll[pane] = scr
    return pane


async def test_the_client_asks_the_server_for_blocks():
    """⛔ **여기가 이 슬라이스의 첫 걸음**이다. 정본은 이제껏 `blocks` 능력을 안
    광고했고, 그래서 서버는 프레임을 **한 바이트도** 안 보냈다 — 나머지가 다 있어도
    이 한 줄이 없으면 화면에 아무 일도 안 일어난다.

    ★ 그 능력은 **플러그인이 싣는다**(`client_caps`). 코어 목록에 박으면 `blocks/` 를
    지웠을 때 그리는 코드만 사라지고 대역폭은 남는다."""
    from pytmuxlib import plugins, protocol
    caps = plugins.get().client_caps()
    assert "blocks" in caps, f"플러그인이 blocks 를 안 광고한다: {caps}"
    assert "blocks" not in protocol.CLIENT_CAPS, (
        "코어 목록에 박으면 blocks/ 를 지워도 프레임이 계속 온다 — 플러그인이 실을 것")


async def test_esc_b_enters_the_mode_and_the_status_bar_says_so():
    """입구와 **취소 조건**. GUI 의 `esc b` 와 같은 자리이고, 나가는 키는 셋 다 같은
    뜻이다(스크롤 모드의 `q`·`Esc`·`Enter` 와 같은 배정).

    ⛔ 배지까지 본다 — 모드가 서 있는데 화면이 아무 말도 안 하면 사용자는 자기가 어느
    모드에 있는지 알 길이 없다(pytmux-467 이 `[prefix]` 에서 세운 근거)."""
    async def body(app, pilot, srv):
        _seed(app)
        await pilot.press("escape")
        await pilot.press("b")
        assert app.mode == "block", "esc b 가 블록 모드를 안 세웠다"
        assert app._block_pick is not None
        segs = []
        app.plugins.client_statusbar_badges(app, app.status, segs, 60, 0)
        assert any("[block]" in s.text for s in segs), f"배지가 없다: {segs}"
        # 나가는 키 셋. ⚠ 재진입은 **팔레트 쪽 입구**로 한다 — `esc b` 를 다시 쓰면
        #    방금 나가며 누른 ESC 와 붙어 ESC 오토리핏 디바운스(`_ESC_DEBOUNCE`)에
        #    먹히고, 그건 제품의 의도된 동작이라 시험이 피해 가야 한다. 두 입구가 같은
        #    모드를 세운다는 것도 여기서 함께 재진다.
        for leave in ("escape", "enter", "q"):
            if app.mode != "block":
                app._run_command("select-blocks")
            assert app.mode == "block", f"{leave} 회차: 재진입 실패"
            await pilot.press(leave)
            assert app.mode == "normal", f"{leave} 로 안 나갔다"
            assert app._block_pick is None, f"{leave} 로 나갔는데 고른 자리가 남았다"
    await _with_app(body)


async def test_the_first_pick_is_the_last_block_and_arrows_walk_it():
    """첫 선택이 **마지막 블록**인 이유: 방금 친 명령의 출력을 집으려는 것이 이 기능의
    첫 쓰임이고 그것이 목록의 끝이다. `↓` 가 더 최근 — 화면에서 아래로 가는 것과 같은
    방향이라야 손이 안 어긋난다(GUI `block_key` 와 같은 배정).

    ⛔ 끝에서 더 가려 해도 **감기지 않는다**. 감기면 `↑` 을 길게 눌렀을 때 선택이
    목록 끝으로 튀어, 복사한 것이 보고 있던 것과 달라진다."""
    async def body(app, pilot, srv):
        pane = _seed(app)
        await pilot.press("escape")
        await pilot.press("b")
        assert app._block_pick == (pane, 1), "첫 선택은 마지막 블록"
        await pilot.press("up")
        assert app._block_pick == (pane, 0)
        await pilot.press("up")
        assert app._block_pick == (pane, 0), "맨 위에서 더 위로 안 감긴다"
        await pilot.press("down")
        assert app._block_pick == (pane, 1)
        await pilot.press("down")
        assert app._block_pick == (pane, 1), "맨 아래에서 더 아래로 안 감긴다"
    await _with_app(body)


async def test_keys_that_are_not_ours_do_not_reach_the_shell():
    """⛔ **모드 안의 글자를 패널로 흘리지 않는다.** 흘리면 블록을 고르는 동안 친 글자가
    셸에 찍힌다 — 정본의 esc·스크롤 모드와 같은 규율이고 GUI 도 같다."""
    async def body(app, pilot, srv):
        _seed(app)
        sent = []
        app.send_input = lambda data: sent.append(data)
        await pilot.press("escape")
        await pilot.press("b")
        for key in ("x", "z", "left", "right", "tab"):
            await pilot.press(key)
        assert sent == [], f"블록 모드에서 패널로 샜다: {sent}"
        assert app.mode == "block", "우리 것 아닌 키가 모드를 풀어선 안 된다"
    await _with_app(body)


async def test_an_empty_pane_says_why_instead_of_entering():
    """빈 목록에서 모드에 들여보내면 배지만 켜진 채 키가 통째로 죽는다 — 사용자에게는
    "고장났다"로 보이고 진짜 원인(셸 통합)은 화면 어디에도 안 적혀 있다.

    ⚠ **그 한 줄이 패널마다 달라야 한다**(pytmux-21). Claude 패널에서 "셸 통합을 켜라"고
    말하면 **고칠 수 없는 것을 고치라는 안내**가 된다 — Claude 는 OSC 를 안 보낸다."""
    async def body(app, pilot, srv):
        seen = []
        app.display_message = lambda text, **kw: seen.append(text)
        await pilot.press("escape")
        await pilot.press("b")
        assert app.mode == "normal", "빈 패널에서 모드에 들어갔다"
        assert seen and "OSC 133" in seen[-1], f"셸 문구가 아니다: {seen}"
        app.is_claude_pane = lambda pid: True
        await pilot.press("escape")
        await pilot.press("b")
        assert app.mode == "normal"
        assert "OSC 133" not in seen[-1] and seen[-1] != seen[0], (
            f"Claude 패널인데 셸 통합 안내가 나왔다: {seen[-1]}")
    await _with_app(body)


async def test_the_picked_block_is_actually_inverted_on_screen():
    """⛔ **호출부까지 잰다.** `row_span` 만 재면 강조를 붙이는 훅을 지워도 통과한다 —
    이 저장소가 두 번 겪은 공허 통과다. 그래서 `_composite` 를 돌려 **셀**을 본다."""
    async def body(app, pilot, srv):
        pane = _seed(app)
        rect = [p for p in app.layout["panes"] if p["id"] == pane][0]
        app._composite()
        before = app.view._cells[rect["y"]][rect["x"]][1]
        await pilot.press("escape")
        await pilot.press("b")
        await pilot.press("up")            # 0..3 행 블록(뷰포트 안)
        app._composite()
        after = app.view._cells[rect["y"]][rect["x"]][1]
        assert bool(after.reverse) and not bool(before.reverse), (
            f"고른 블록이 화면에서 안 밝다: {before!r} → {after!r}")
        # 블록 밖(4행 이후)은 그대로다 — 안 그러면 패널 전체가 밝아진 것이다.
        outside = app.view._cells[rect["y"] + 5][rect["x"]][1]
        assert not bool(outside.reverse), "블록 밖까지 밝다"
    await _with_app(body)


async def test_the_highlight_follows_the_scroll_and_is_clipped_to_the_pane():
    """블록은 스크롤백 좌표라 화면보다 길 수 있다(수백 줄짜리 빌드 로그가 흔하다).
    안 자르면 강조가 패널 밖으로 새어 이웃 패널·크롬 위에 그려진다."""
    async def body(app, pilot, srv):
        pane = _seed(app, blocks=[{"cmd": "build", "state": "done",
                                   "start": 0, "end": 10000}], top=5000)
        rect = [p for p in app.layout["panes"] if p["id"] == pane][0]
        await pilot.press("escape")
        await pilot.press("b")
        app._composite()
        H = len(app.view._cells)
        for y in range(H):
            inside = rect["y"] <= y < rect["y"] + rect["h"]
            got = bool(app.view._cells[y][rect["x"]][1].reverse)
            assert got == inside, f"{y}행: 패널 안={inside} 인데 반전={got}"
        # 통째로 화면 위로 지나간 블록은 아무것도 안 그린다(선택이 풀린 것은 아니다).
        app.pane_top[pane] = 99999
        app._composite()
        assert not any(bool(app.view._cells[y][rect["x"]][1].reverse)
                       for y in range(H)), \
            "화면 밖 블록을 그렸다"
        assert app._block_pick is not None, "화면 밖이라고 선택이 풀리면 안 된다"
    await _with_app(body)


async def test_copy_goes_through_the_same_path_as_a_drag_copy():
    """**드래그 복사와 같은 길**이라야 한다 — 같은 `copy_range` 를 보내고 회신이 오면
    접힘 되돌리기·클립보드·"N자 복사됨" 한 줄까지 그 경로가 그대로 한다. 여기서 따로
    클립보드를 건드리면 두 복사가 서로 다른 규칙(`copy-unwrap`)을 타기 시작한다."""
    async def body(app, pilot, srv):
        pane = _seed(app)
        rect = [p for p in app.layout["panes"] if p["id"] == pane][0]
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        await pilot.press("escape")
        await pilot.press("b")
        await pilot.press("up")
        await pilot.press("ctrl+c")
        assert sent, "복사가 서버에 아무것도 안 청했다"
        action, kw = sent[-1]
        assert action == "copy_range", action
        # 끝은 와이어 `end`(3) **한 줄 앞**이다 — 그 줄은 다음 프롬프트 줄이라 딸려 오면
        # 안 된다(`segment.row_span` · 네이티브 클라와 같은 답).
        assert (kw["pane"], kw["y0"], kw["y1"]) == (pane, 0, 2), kw
        # 블록은 **줄 단위**라 열은 패널 폭 끝까지다.
        assert (kw["x0"], kw["x1"]) == (0, rect["w"] - 1), kw
        assert app._copy_unwrap_geom == (rect["w"], 0), "접힘 되돌리기 기하가 없다"
    await _with_app(body)


async def test_the_pick_is_dropped_when_the_list_shrinks_under_it():
    """목록은 상한(500)에서 잘리고 스크롤백 회전으로도 줄어든다. **읽을 때마다** 접지
    않으면 `↑↓` 가 없는 자리를 가리키고 `Ctrl+C` 가 엉뚱한 글을 담는다."""
    async def body(app, pilot, srv):
        pane = _seed(app)
        await pilot.press("escape")
        await pilot.press("b")
        assert app._block_pick == (pane, 1)
        app._dispatch({"t": "blocks", "pane": pane,
                       "blocks": [{"cmd": "ls", "state": "done", "start": 0, "end": 3}]})
        assert app._block_pick == (pane, 0), "줄어든 목록으로 안 접혔다"
        app._dispatch({"t": "blocks", "pane": pane, "blocks": []})
        assert app._block_pick is None and app.mode == "normal", \
            "목록이 비었는데 모드가 남았다"
    await _with_app(body)


async def test_moving_the_focus_to_another_pane_ends_the_selection():
    """☠ 검수 2026-09-05 T-1 — **다른 패널을 고르면 블록 모드가 끝난다.**

    블록 목록은 패널마다 따로다. 모드를 붙잡고 있으면 `↑`/`↓` 는 **안 보이는 패널**의
    자리를 옮기고 `Ctrl+C` 는 그 패널의 글을 담는데, 화면에는 아무 반응이 없다 —
    `client_render` 가 활성 아닌 패널은 «그리기만» 건너뛰기 때문이다(배지는 남는다).

    GUI 는 이 계약을 이미 갖고 있다(`session_view::drop_block_pick_unless_selecting`
    · `moving_the_focus_to_another_pane_ends_the_selection`). [[pytmux-185]] 는
    정본이 **같게 굴 것**을 요구하므로 이 갈림은 결함이다."""
    async def body(app, pilot, srv):
        pane = _seed(app)
        await pilot.press("escape")
        await pilot.press("b")
        assert app._block_pick == (pane, 1) and app.mode == "block"
        sent = []
        app.send_command = lambda *a, **kw: sent.append((a, kw))
        # 서버가 «다른 패널이 활성이다» 를 알려 온다(클릭·분할 뒤 포커스 이동).
        other = pane + 1
        app._dispatch({"t": "layout", "active": other, "cols": 60, "rows": 20,
                       "dividers": [],
                       "panes": [
                           {"id": pane, "x": 0, "y": 0, "w": 30, "h": 20,
                            "title": "sh", "active": False},
                           {"id": other, "x": 30, "y": 0, "w": 30, "h": 20,
                            "title": "sh", "active": True}]})
        assert app.mode == "normal", "포커스가 옮겨갔는데 [block] 배지가 남았다"
        assert app._block_pick is None, "안 보는 패널의 선택이 남았다"
        # 그 뒤의 `↑`·`Ctrl+C` 는 블록의 것이 아니다 — 옛 패널의 글을 복사하지 않는다.
        await pilot.press("up")
        await pilot.press("ctrl+c")
        assert not any("copy" in str(a).lower() for a, _ in sent), \
            f"옛 패널의 블록을 복사했다: {sent}"
    await _with_app(body)


async def test_a_hostile_blocks_frame_cannot_crash_the_client():
    """블록의 값은 애초에 **패널 안 아무 프로그램**이 보낸 OSC 이고, 원격 링크 너머의
    서버는 이 버전이 아닐 수 있다. 여기서 접지 않으면 `range()` 가 TypeError 로 클라를
    죽인다."""
    async def body(app, pilot, srv):
        pane = app.layout.get("active")
        for bad in ("nope", 3, [{"start": "x"}], [{"start": -5}], [None, 7],
                    [{"start": 1, "end": "y"}]):
            app._dispatch({"t": "blocks", "pane": pane, "blocks": bad})
            app._composite()
        assert app.mode == "normal"
    await _with_app(body)


async def test_the_row_span_matches_what_the_native_client_computes():
    """⛔ **강조를 그리는 자리와 복사할 범위를 정하는 자리가 각자 세면** 화면에 밝은
    것과 클립보드에 담기는 것이 조용히 어긋난다. 그래서 판정은 한 함수이고, 그 함수는
    네이티브 클라의 `proto::blocks::row_span` 과 **같은 답**을 내야 한다.

    아래 넷은 그쪽 시험이 든 값 그대로다(`crates/proto/src/blocks.rs` 의 `#[test]`).

    ⛔ 첫 값은 종전에 `(10, 15)` 였다 — 와이어 `end` 가 **다음 프롬프트 줄**이라는 사실을
    정본만 빠뜨려 한 줄이 딸려 왔고, 그쪽 시험(`the_end_row_is_the_next_prompt_so_the_block_
    stops_one_line_earlier`)은 처음부터 `(10, 14)` 였다. 「같은 값 그대로」라고 적힌 채 둘이
    달랐던 것이고, 스티키 바 픽스처(pytmux-520)가 그것을 잡았다."""
    assert row_span([{"start": 10, "end": 15}], 0, 999) == (10, 14)
    # `D`(끝)만 오고 `A`가 아직 안 온 블록은 **다음 블록의 시작 한 줄 앞**까지다.
    assert row_span([{"start": 0}, {"start": 7}], 0, 999) == (0, 6)
    # 마지막 블록은 지금도 자라는 중이라 물어볼 데가 없다 — 지금까지 찬 데까지.
    assert row_span([{"start": 0}, {"start": 7}], 1, 20) == (7, 20)
    # 한 줄짜리 블록에서 **범위가 뒤집히면** 엉뚱한 데가 복사된다.
    assert row_span([{"start": 5, "end": 5}], 0, 999) == (5, 5)


async def test_deleting_the_plugin_leaves_no_stranded_mode():
    """delete-to-disable 이 「조용히 사라진다」가 아니라 「조용히 망가진다」가 되는
    자리다 — 플러그인 없이 그 모드가 서 있으면 클라가 **키를 하나도 안 먹는 상태로
    갇힌다**. 코어가 그 복구를 든다(아무도 안 받으면 모드를 푼다)."""
    async def body(app, pilot, srv):
        _seed(app)
        await pilot.press("escape")
        await pilot.press("b")
        assert app.mode == "block"
        sent = []
        app.send_input = lambda data: sent.append(data)
        with harness.patched(app.plugins, client_mode_key=lambda a, e: False):
            await pilot.press("x")
        assert app.mode == "normal", "아무도 안 받았는데 모드가 남았다"
        assert sent == [], "모드를 푸는 그 키까지 셸에 찍혔다"
    await _with_app(body)


# ── 스티키 바(pytmux-520 · 사용자 제보 2026-09-26) ──────────────────────────────
#
# *"화면을 위쪽으로 스크롤해도 이전 프롬프트가 나타나지 않습니다. 위로 한 번 스크롤하면
# 이전 프롬프트가 보이고 클릭하면 그 프롬프트 위치까지 스크롤 되어야 합니다."*
# 정본에도 없던 표면이라 **정본에 먼저**(pytmux-185 · INV1) — GUI 는 같은 CL 에서
# 같은 규칙(`proto::blocks::sticky_at`)으로 선다.

from pytmuxlib.plugins.blocks.segment import sticky_at  # noqa: E402

_TWO = [{"cmd": "ls", "state": "done", "exit": 0, "start": 0, "end": 3},
        {"cmd": "make", "state": "running", "start": 4}]


async def test_sticky_at_names_the_block_the_viewport_starts_inside():
    """규칙 셋 — 라이브면 없다 · 블록 **안**이면 그 블록 · 첫 줄이 곧 시작 줄이면 없다.
    아래 값은 GUI 쪽 픽스처(`blocks_sticky.json`)와 같은 사례다."""
    assert sticky_at(_TWO, 10, 0, 30) is None, "라이브에서는 바가 없다"
    assert sticky_at(_TWO, 10, 20, 30) == 1, "둘째 블록의 답 가운데가 첫 줄"
    assert sticky_at(_TWO, 4, 26, 30) is None, "첫 줄이 곧 프롬프트 줄이면 두 번 안 보인다"
    assert sticky_at(_TWO, 1, 29, 30) == 0, "첫 블록 안"
    assert sticky_at(_TWO, 2, 28, 30) == 0, "블록의 마지막 줄(end 한 줄 앞)도 안이다"
    assert sticky_at(_TWO, 3, 27, 30) is None, "어느 블록에도 안 속하는 줄(합성 자료의 틈)"
    assert sticky_at([], 10, 5, 30) is None
    assert sticky_at(_TWO, "x", 5, 30) is None, "신뢰 못 할 값에 죽지 않는다"


async def test_a_scrolled_pane_shows_the_prompt_it_is_inside_as_a_sticky_bar():
    """⛔ 호출부까지 — `_composite` 를 돌려 **셀**을 본다. 라이브로 돌아오면 사라진다."""
    async def body(app, pilot, srv):
        pane = _seed(app, blocks=_TWO, top=10, scr=20)
        rect = [p for p in app.layout["panes"] if p["id"] == pane][0]
        app._composite()
        row = app.view._cells[rect["y"]][rect["x"]:rect["x"] + rect["w"]]
        text = "".join(ch for ch, _ in row)
        assert "make" in text and "▲" in text, f"스티키 바가 없다: {text!r}"
        assert all(st.bold for _ch, st in row if st is not None), "띠가 한 줄을 통째로 안 칠했다"
        # 그 아래 줄은 그대로다 — 바는 한 줄이다.
        below = "".join(ch for ch, _ in app.view._cells[rect["y"] + 1][rect["x"]:rect["x"] + 6])
        assert "make" not in below
        # 라이브로 돌아오면 사라진다 — 바가 라이브 글을 가리면 안 된다.
        app.pane_scroll[pane] = 0
        app._composite()
        text2 = "".join(ch for ch, _ in app.view._cells[rect["y"]][rect["x"]:rect["x"] + rect["w"]])
        assert "make" not in text2, "라이브인데 바가 남았다"
        # 첫 줄이 곧 프롬프트 줄이면 안 그린다.
        app.pane_scroll[pane] = 26
        app.pane_top[pane] = 4
        app._composite()
        text3 = "".join(ch for ch, _ in app.view._cells[rect["y"]][rect["x"]:rect["x"] + rect["w"]])
        assert "▲" not in text3, "프롬프트 줄이 보이는데 바까지 그렸다"
    await _with_app(body)


async def test_clicking_the_sticky_bar_scrolls_to_that_prompt():
    """바를 누르면 그 프롬프트 줄이 첫 줄에 오게 굴린다 — Δ = top − 시작 행(과거 +).
    바 밖은 안 먹는다. 코어의 캔버스 클릭이 그 훅을 **실제로 부르는지**까지 본다."""
    async def body(app, pilot, srv):
        pane = _seed(app, blocks=_TWO, top=10, scr=20)
        rect = [p for p in app.layout["panes"] if p["id"] == pane][0]
        app._composite()
        sent = []
        app.send_scroll = lambda pid, **kw: sent.append((pid, kw))
        assert app.plugins.client_click(app, rect["x"] + 1, rect["y"], 1), "바 위 클릭을 안 먹었다"
        assert sent == [(pane, {"delta": 10 - 4})], sent
        assert not app.plugins.client_click(app, rect["x"] + 1, rect["y"] + 1, 1), "바 밖을 먹었다"
        assert not app.plugins.client_click(app, rect["x"] + 1, rect["y"], 3), "오른쪽 버튼을 먹었다"
        # 코어 캔버스가 그 훅을 부른다 — 훅만 재면 배선을 지워도 통과한다(공허 통과).
        sent.clear()
        from textual import events
        ev = events.MouseDown(app.view, x=rect["x"] + 1, y=rect["y"], delta_x=0,
                              delta_y=0, button=1, shift=False, meta=False, ctrl=False)
        app.view.on_mouse_down(ev)
        assert sent == [(pane, {"delta": 6})], f"캔버스 클릭이 훅을 안 거쳤다: {sent}"
    await _with_app(body)


# ── 요약 판(pytmux-538 · 449 ⑴ 의 «판은 뒤 CL») ──────────────────────────────────

async def test_summary_opens_a_read_panel_listing_the_blocks_with_the_same_badges():
    """`summary` 는 활성 패널의 블록을 **읽는 판**(InfoScreen)으로 띄운다 — 네이티브 클라의
    `Screen::Summary` 와 같은 손(아무 키나 닫기 · ↑↓ 굴리기)이고 표식도 같은 표다
    (`proto::blocks::badge` — ok · err · ?? · ··· · … · ❯)."""
    from pytmuxlib.plugins.blocks.clientside import _badge, summary_lines
    assert _badge({"state": "done", "exit": 0}) == "ok"
    assert _badge({"state": "done", "exit": 2}) == "err"
    assert _badge({"state": "done"}) == "??"
    assert _badge({"state": "running"}) == "···"
    assert _badge({"state": "prompt"}) == "…"
    assert _badge({"state": "turn"}) == "❯"

    async def body(app, pilot, srv):
        pane = _seed(app, blocks=[
            {"cmd": "ls", "state": "done", "exit": 0, "start": 0, "end": 3, "cwd": "/tmp"},
            {"cmd": "make", "state": "running", "start": 4}])
        lines = summary_lines(app, pane)
        assert lines[0].endswith("2개") or "2" in lines[0], lines[0]
        assert lines[1].startswith("ok ") and "ls" in lines[1] and "/tmp" in lines[1], lines
        assert lines[2].startswith("···") and "make" in lines[2], lines
        assert app._run_command("summary") is not False
        await wait_until(pilot, lambda: len(app.screen_stack) > 1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoScreen", scr
        assert scr._lines == lines
        # 읽는 판 — 아무 키나 닫는다(정본 InfoScreen · 네이티브 `Screen::Summary` 와 같다).
        await pilot.press("f5")
        await wait_until(pilot, lambda: len(app.screen_stack) == 1)
        # 블록이 없으면 판 대신 한 줄.
        app._dispatch({"t": "blocks", "pane": pane, "blocks": []})
        shown = []
        app.display_message = lambda msg, **kw: shown.append(msg)
        app._run_command("summary")
        assert shown and len(app.screen_stack) == 1, shown
    await _with_app(body)
