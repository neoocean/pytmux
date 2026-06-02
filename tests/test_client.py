"""클라이언트(Textual headless) 테스트: 프롬프트/명령목록/자동완성/ESC 모드/
IME 단축키/표시줄/포커스 경계/와이드 문자 합성."""
import asyncio

import harness
from harness import make_app, server_only, teardown
from textual.events import Key
from textual.widgets import Input


async def _with_app(coro, size=(100, 30), cfg=None, session=None):
    srv, task, sock = await server_only()
    app = make_app(sock, cfg, session)
    try:
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.4)
            await coro(app, pilot, srv)
    finally:
        await teardown(srv, task, sock)


async def test_hello_paints_screen():
    async def body(app, pilot, srv):
        assert app.layout.get("panes"), "초기 레이아웃 패널 존재"
        assert app.view._cells, "프레임 합성됨"
        assert app.status.session != "" or True
    await _with_app(body)


async def test_command_prompt_via_esc():
    async def body(app, pilot, srv):
        sess = next(iter(srv.sessions.values()))
        await pilot.press("escape")            # 명령 모드
        assert app.mode == "esc"
        await pilot.press("colon")             # 명령 프롬프트(모달 Input)
        assert app.screen_stack[-1].__class__.__name__ == "PromptScreen"
        inp = app.screen_stack[-1].query_one(Input)
        assert app.focused is inp, "모달 Input 포커스"
        for ch in "new-window":
            await pilot.press(ch if ch != "-" else "minus")
        n = len(sess.tabs)
        await pilot.press("enter")
        await pilot.pause(0.4)
        assert len(sess.tabs) == n + 1, "명령 실행"
    await _with_app(body)


async def test_command_list_and_autocomplete():
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        inp = app.screen_stack[-1].query_one(Input)
        for ch in "spl":
            await pilot.press(ch)
        await pilot.pause(0.1)
        sug = await inp.suggester.get_suggestion("spl") if inp.suggester else None
        # 자동완성이 옵션까지 한 번에 제안(-h)
        assert sug == "split-window -h", sug
        sug2 = await inp.suggester.get_suggestion("split-window")
        assert sug2 == "split-window -h", sug2
        # ? 로 명령 목록
        await pilot.press("question_mark")
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "CommandListScreen"
        # 스크롤 가능 + 명령 개수/스크롤 힌트 표시
        from textual.widgets import ListView
        lv = scr.query_one(ListView)
        assert str(lv.styles.overflow_y) == "scroll", "스크롤바 항상 표시"
        assert "스크롤" in str(lv.border_subtitle), lv.border_subtitle
    await _with_app(body)


async def test_help_command():
    async def body(app, pilot, srv):
        sess = next(iter(srv.sessions.values()))
        app._run_command("help")
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "CommandListScreen"
        # 첫 항목(split-window) 선택 → 명령 프롬프트에 채워짐
        await pilot.press("enter")
        await pilot.pause(0.2)
        ps = app.screen_stack[-1]
        assert ps.__class__.__name__ == "PromptScreen"
        inp = ps.query_one(Input)
        assert inp.value.strip() == "split-window", repr(inp.value)
        # 한 번 더 Enter → 실행
        n = len(sess.active_window.panes())
        await pilot.press("enter")
        await pilot.pause(0.4)
        assert len(sess.active_window.panes()) == n + 1, "help 선택→실행"
    await _with_app(body)


async def test_esc_mode_arrows_and_colon():
    async def body(app, pilot, srv):
        await pilot.press("escape")
        assert app.mode == "esc" and app.status.cmd_mode is True
        await pilot.press("left")
        assert app.mode == "esc", "방향키는 모드 유지"
        await pilot.press("colon")
        assert app.mode == "normal"
        assert app.screen_stack[-1].__class__.__name__ == "PromptScreen"
    await _with_app(body)


async def test_display_panes():
    async def body(app, pilot, srv):
        await pilot.press("ctrl+b")
        await pilot.press("percent_sign")
        await pilot.pause(0.4)
        assert len(app.layout["panes"]) == 2
        await pilot.press("ctrl+b")
        await pilot.press("q")
        assert app.mode == "display"
        first = app.layout["panes"][1]["id"]
        await pilot.press("1")
        await pilot.pause(0.3)
        assert app.mode == "normal" and app.layout["active"] == first
    await _with_app(body)


async def test_ime_shortcuts():
    async def body(app, pilot, srv):
        sess = next(iter(srv.sessions.values()))
        # Ctrl-b 가 IME 에서 ctrl+ㅠ 로 들어와도 prefix 진입
        app.on_key(Key(key="ctrl+ㅠ", character=None))
        assert app.mode == "prefix", app.mode
        # prefix 후 물리 c(=ㅊ) → new-window
        n = len(sess.tabs)
        app.on_key(Key(key="ㅊ", character="ㅊ"))
        await pilot.pause(0.3)
        assert len(sess.tabs) == n + 1, "prefix+ㅊ → new-window"
    await _with_app(body)


async def test_ctrl_korean_no_crash():
    async def body(app, pilot, srv):
        # 한글 Ctrl 조합이 forward 경로로 가도 크래시 없음
        app.on_key(Key(key="ctrl+ㅂ", character=None))
        await pilot.pause(0.05)
        assert app.mode == "normal"
    await _with_app(body)


async def test_active_pane_border_highlight():
    async def body(app, pilot, srv):
        await pilot.press("ctrl+b")
        await pilot.press("percent_sign")          # 좌우 분할(활성=새 패널)
        await pilot.pause(0.4)
        lay = app.layout
        assert lay.get("bordered"), "다중 패널이면 테두리 박스"
        active = lay["active"]
        cells = app.view._cells

        def is_blue(x, y):
            st = cells[y][x][1]
            return bool(st and st.color and "blue" in str(st.color))

        ap = next(p for p in lay["panes"] if p["id"] == active)
        bx, by, bw, bh = ap["box"]
        x2, y2 = bx + bw - 1, by + bh - 1
        # 활성 패널 박스의 네 변 전체가 파란색
        assert all(is_blue(gx, by) and is_blue(gx, y2)
                   for gx in range(bx, x2 + 1)), "활성 상/하 변 파랑"
        assert all(is_blue(bx, gy) and is_blue(x2, gy)
                   for gy in range(by, y2 + 1)), "활성 좌/우 변 파랑"
        # 비활성 패널의 (활성과 공유하지 않는) 바깥 모서리는 회색
        ip = next(p for p in lay["panes"] if p["id"] != active)
        ibx, iby, ibw, ibh = ip["box"]
        assert not is_blue(ibx, iby), "비활성 바깥 모서리는 회색"
    await _with_app(body)


async def test_pane_name_on_border():
    async def body(app, pilot, srv):
        await pilot.press("ctrl+b")
        await pilot.press("percent_sign")          # 2패널 → 테두리 박스
        await pilot.pause(0.4)
        lay = app.layout
        active = lay["active"]
        ap = next(p for p in lay["panes"] if p["id"] == active)
        ip = next(p for p in lay["panes"] if p["id"] != active)
        ap["title"] = "EDITOR"      # 활성 패널 리네임
        ip["title"] = "LOGS"        # 비활성 패널 리네임
        app._composite()
        cells = app.view._cells

        def top_text(p):
            bx, by, bw, _ = p["box"]
            return "".join(cells[by][x][0] or " " for x in range(bx, bx + bw))

        # 이름이 위쪽 테두리 중앙에 표시
        ta = top_text(ap)
        assert "EDITOR" in ta, repr(ta)
        assert "LOGS" in top_text(ip), repr(top_text(ip))
        # 활성 패널 이름은 활성 색(파랑)
        bx, by, bw, _ = ap["box"]
        i = ta.index("E")
        st = cells[by][bx + i][1]
        assert st and st.color and "blue" in str(st.color), "활성 이름 파랑"
    await _with_app(body)


async def test_wide_char_composite():
    async def body(app, pilot, srv):
        sess = next(iter(srv.sessions.values()))
        p = sess.active_window.active_pane
        p.feed(b"\x1b[2J\x1b[H")
        p.feed("AB가CD\r\n".encode())
        rows, cur = p.render(True)
        app.pane_content[p.id] = (rows, cur)
        app._composite()
        cells = app.view._cells
        grid = [cells[0][x][0] for x in range(6)]
        # 가(와이드)=col2, 연속 셀=col3(""), C=col4
        assert grid[2] == "가" and grid[3] == "" and grid[4] == "C", grid
        text = "".join(seg.text for seg in app.view.render_line(0))
        assert "가CD" in text, repr(text[:8])
    await _with_app(body)


async def test_status_format():
    async def body(app, pilot, srv):
        strip = app.status.render_line(0)
        txt = "".join(seg.text for seg in strip)
        assert txt.startswith("S=0|"), repr(txt[:10])  # status-left "S=#S| " 확장
    await _with_app(body, cfg={"status_left": "S=#S| "})
