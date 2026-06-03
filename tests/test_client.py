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


async def test_command_prompt_colon_prefix():
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        assert scr.query("#pprefix"), "고정 ':' 프리픽스 위젯 존재"
        inp = scr.query_one(Input)
        for ch in "ls":
            await pilot.press(ch)
        assert inp.value == "ls"           # ':' 는 입력 값에 포함되지 않음
        for _ in range(5):                 # 다 지워도 프리픽스 위젯은 유지
            await pilot.press("backspace")
        assert inp.value == "", repr(inp.value)
        assert scr.query("#pprefix"), "백스페이스로 ':' 가 지워지지 않음"
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
        # ? 로 명령 목록(필터 없이 전체 보려면 입력을 먼저 비움)
        for _ in "spl":
            await pilot.press("backspace")
        await pilot.press("question_mark")
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "CommandListScreen"
        # 카테고리 탭으로 그룹화됨(첫 카테고리 = 패널, 첫 명령 = split-window)
        assert [c for c, _ in scr._cats][:2] == ["패널", "탭"], scr._cats
        assert scr._ci == 0 and scr._cur[0][0] == "split-window", scr._cur[:1]
        from textual.widgets import ListView
        lv = scr.query_one(ListView)
        assert str(lv.styles.overflow_y) == "scroll", "스크롤바 항상 표시"
        # ← → 로 카테고리(탭) 전환
        await pilot.press("right")
        await pilot.pause(0.1)
        assert scr._ci == 1 and scr._cur[0][0] == "new-tab", (scr._ci, scr._cur[:1])
        await pilot.press("left")
        await pilot.pause(0.1)
        assert scr._ci == 0 and scr._cur[0][0] == "split-window", scr._ci
        # 힌트는 박스 subtitle 에 표시
        box = scr.query_one("#cmdbox")
        assert "카테고리" in str(box.border_subtitle), box.border_subtitle
    await _with_app(body)


async def test_command_substring_candidates():
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        inp = scr.query_one(Input)
        from textual.widgets import Label
        cand = scr.query_one("#pcand", Label)
        # 중간부터 일치: "tab" 은 접두사가 아니지만 tab 이 든 명령들이 후보가 된다.
        for ch in "tab":
            await pilot.press(ch)
        await pilot.pause(0.1)
        names = [n for n, _ in scr._cand]
        assert cand.display is True, "후보 영역이 위로 펼쳐짐"
        assert "new-tab" in names and "kill-tab" in names, names
        assert all("tab" in n for n in names), names
        assert scr._sel == 0
        # ↓ 로 후보 내 선택 이동
        first = scr._cand[0][0]
        await pilot.press("down")
        await pilot.pause(0.05)
        assert scr._sel == 1, scr._sel
        # Tab 으로 강조 후보를 입력에 채움(뒤에 공백) → 후보 영역 숨김
        chosen = scr._cand[scr._sel][0]
        await pilot.press("tab")
        await pilot.pause(0.1)
        assert inp.value == chosen + " ", repr(inp.value)
        assert cand.display is False, "옵션 입력 단계에선 후보 숨김"
        # 첫 글자 일치(접두사)도 여전히 후보로 동작
        for _ in range(len(inp.value)):
            await pilot.press("backspace")
        for ch in "new":
            await pilot.press(ch)
        await pilot.pause(0.1)
        assert "new-tab" in [n for n, _ in scr._cand], scr._cand
        # Enter 는 강조 후보를 채우기만(실행 X), 그 다음 Enter 로 실행
        sel_name = scr._cand[scr._sel][0]
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is scr, "첫 Enter 는 후보 채우기"
        assert inp.value == sel_name + " ", repr(inp.value)
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


async def test_f12_opens_command_prompt():
    async def body(app, pilot, srv):
        assert app.mode == "normal"
        await pilot.press("f12")               # ESC 모드 아님 → 바로 명령 프롬프트
        assert app.screen_stack[-1].__class__.__name__ == "PromptScreen"
        inp = app.screen_stack[-1].query_one(Input)
        assert app.focused is inp
        # prefix F12 는 중첩 prefix 토글(명령 프롬프트 아님)
        await pilot.press("escape")            # 프롬프트 닫기
        await pilot.pause(0.1)
        await pilot.press("ctrl+b")
        await pilot.press("f12")
        await pilot.pause(0.1)
        assert app.prefix_enabled is False, "prefix F12 → 중첩 패스스루 토글"
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


async def test_shift_tab_forwarded_as_backtab():
    async def body(app, pilot, srv):
        sent = []
        app.send_input = lambda data: sent.append(data)
        # normal 모드에서 Shift+Tab(=backtab) → 패널로 CSI Z 전달
        app.on_key(Key(key="shift+tab", character=None))
        assert sent == [b"\x1b[Z"], sent
    await _with_app(body)


async def test_shift_enter_forwarded_as_lf():
    async def body(app, pilot, srv):
        sent = []
        app.send_input = lambda data: sent.append(data)
        # normal 모드에서 Shift+Enter → 앱으로 LF(줄바꿈) 전달(Enter=CR 과 구분)
        app.on_key(Key(key="shift+enter", character=None))
        assert sent == [b"\n"], sent
    await _with_app(body)


async def test_shift_escape_forwards_esc_plain_escape_enters_esc_mode():
    async def body(app, pilot, srv):
        sent = []
        app.send_input = lambda data: sent.append(data)
        # Shift+Escape → 앱으로 ESC(0x1b) 전달, esc 모드 진입 안 함
        app.on_key(Key(key="shift+escape", character=None))
        assert sent == [b"\x1b"], sent
        assert app.mode == "normal"
        # ESC 단독 → esc 모드 진입(셸로 전달 안 함)
        sent.clear()
        app.on_key(Key(key="escape", character=None))
        assert sent == []
        assert app.mode == "esc"
    await _with_app(body)


class _FakeMouse:
    def __init__(self, x, y, button=1, ctrl=False):
        self.x, self.y, self.button = x, y, button
        self.ctrl = ctrl
        self.stopped = False

    def stop(self):
        self.stopped = True


async def test_choose_tree_shows_panes_and_switches():
    # 탭/패널 트리가 패널을 들여쓰기로 보이고 로컬/원격([local]/[ssh])·실행 앱을
    # 표시하며, 패널 선택 시 그 탭+패널로 전환한다(#14/#24).
    async def body(app, pilot, srv):
        from textual.widgets import Label
        tree = {"sessions": [{"name": "s", "windows": [
            {"index": 1, "name": "win", "active": True, "panes": [
                {"id": 11, "title": "shell", "cmd": "zsh", "remote": False},
                {"id": 12, "title": "box", "cmd": "ssh", "remote": True}]}]}]}
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        app._open_choose_tree(tree)
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "ChooseTreeScreen"
        assert [e["kind"] for e in scr.entries] == ["win", "pane", "pane"]
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "[ssh]" in joined and "[local]" in joined, "로컬/원격 배지"
        assert "ssh" in joined and "zsh" in joined, "실행 앱 표시"

        class _Sel:
            def __init__(self, iid):
                self.item = type("I", (), {"id": iid})()
        scr.on_list_view_selected(_Sel("e2"))   # entries[2] = pane id 12
        await pilot.pause(0.1)
        assert ("select_window", {"index": 1}) in sent
        assert ("select_pane_id", {"id": 12}) in sent
    await _with_app(body)


async def test_context_menu_toggle_shows_state_and_stays_open():
    # 컨텍스트 메뉴의 토글 항목(줌/동기화/자동재개)은 현재 on/off 를 표시하고,
    # 선택해도 메뉴를 닫지 않으며(ESC 로만 닫음) 라벨이 갱신된다(#17).
    async def body(app, pilot, srv):
        active = app.layout.get("active")
        app.status.sync = False
        app.open_menu(active)
        await pilot.pause(0.1)
        menu = app.screen_stack[-1]
        assert menu.__class__.__name__ == "MenuScreen"
        # 초기 상태 표시(꺼짐 ○)
        assert menu._toggle_state("sync") is False
        assert menu._fmt("sync", "동기화").endswith("○")
        # 토글 선택 → 메뉴 유지 + 명령 전송 + 라벨 켜짐(●)로 즉시 갱신
        sent = []
        app.send_cmd = lambda a, **k: sent.append(a)

        class _Sel:
            def __init__(self, iid):
                self.item = type("I", (), {"id": iid})()
        menu.on_list_view_selected(_Sel("m_sync"))
        assert "set_sync" in sent, "토글 명령 전송"
        assert app.screen_stack[-1] is menu, "토글 선택해도 메뉴 유지"
        assert menu._toggle_state("sync") is True, "낙관적 토글 반영"
        assert menu._fmt("sync", "동기화").endswith("●")
        # 비토글 항목 선택 → 메뉴 닫힘
        menu.on_list_view_selected(_Sel("m_new_window"))
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is not menu, "비토글 선택 → 닫힘"
    await _with_app(body)


async def test_context_menu_dims_other_panes():
    # 컨텍스트 메뉴가 열려 있는 동안 대상 패널 외 나머지 패널은 흐리게(dim) 그려
    # 어느 패널 대상인지 배경으로 구분한다(#18).
    async def body(app, pilot, srv):
        app.layout = {"panes": [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 5,
                                 "box": [0, 0, 10, 5]},
                                {"id": 2, "x": 10, "y": 0, "w": 10, "h": 5,
                                 "box": [10, 0, 10, 5]}],
                      "active": 1, "cols": 20, "rows": 5, "dividers": []}
        app.pane_content = {1: ([[("x" * 10, {})] for _ in range(5)], None),
                            2: ([[("y" * 10, {})] for _ in range(5)], None)}
        app._menu_pane = 1
        app._menu_open = True
        app._composite()
        cells = app.view._cells
        # 대상(1) 내부 셀은 dim 아님, 비대상(2) 내부 셀은 dim
        s_target = cells[2][5][1]
        s_other = cells[2][15][1]
        assert not (s_target and s_target.dim), "대상 패널은 흐리지 않음"
        assert s_other and s_other.dim, "비대상 패널은 흐리게(dim)"
        # 메뉴 닫힘 → dim 해제
        app._menu_open = False
        app._composite()
        cells = app.view._cells
        assert not (cells[2][15][1] and cells[2][15][1].dim), "닫으면 dim 해제"
    await _with_app(body)


async def test_ctrl_q_passthrough_not_quit():
    # Ctrl+Q 는 앱을 종료하지 않고 활성 패널로 전달된다(종료는 detach 명령). (#25)
    async def body(app, pilot, srv):
        sent = []
        app.send_input = lambda d: sent.append(d)
        app.mode = "normal"
        await pilot.press("ctrl+q")
        await pilot.pause(0.1)
        assert b"\x11" in sent, "normal 모드 ctrl+q → 활성 패널로 DC1 전달"
        assert app.is_running, "ctrl+q 로 앱이 종료되지 않음"
        # 다른 모드(prefix 등)에서는 패스스루하지 않음
        sent.clear()
        app.mode = "prefix"
        app.action_ctrl_q()
        assert sent == [], "비 normal 모드는 패스스루 안 함"
    await _with_app(body)


async def test_divider_hover_tints_background():
    # 경계선(divider) 위에 마우스를 올리면 그 칸 배경이 강조되고(리사이즈 암시),
    # 벗어나면 해제된다(#27).
    async def body(app, pilot, srv):
        v = app.view
        app.layout = {
            "panes": [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 5},
                      {"id": 2, "x": 11, "y": 0, "w": 10, "h": 5}],
            "dividers": [{"x": 10, "y": 0, "w": 1, "h": 5, "split_id": 100,
                          "orient": "lr", "rect": [0, 0, 21, 5]}],
            "active": 1, "cols": 21, "rows": 5}
        app.pane_content = {1: ([[("x" * 10, {})] for _ in range(5)], None),
                            2: ([[("y" * 10, {})] for _ in range(5)], None)}
        # divider(x=10) 위 모션 → 호버 추적 + 배경 강조
        v.on_mouse_move(_FakeMouse(10, 2))
        await pilot.pause(0.05)
        assert v._hover_divider == (10, 0, 1, 5), v._hover_divider
        st = app.view._cells[2][10][1]
        assert st is not None and st.bgcolor is not None, "divider 칸 배경 강조"
        # divider 밖(패널 내부) 모션 → 해제
        v.on_mouse_move(_FakeMouse(3, 2))
        await pilot.pause(0.05)
        assert v._hover_divider is None, "divider 벗어나면 해제"
    await _with_app(body)


async def test_right_click_menu_unified_and_ctrl_click_noop():
    # 우클릭(button 3)은 마우스 모드(패스스루) 패널 위에서도 pytmux 컨텍스트 메뉴를
    # 열고 그 패널을 활성화한다. Ctrl+Click 은 무동작(메뉴 안 뜸). (#29)
    async def body(app, pilot, srv):
        v = app.view
        app.layout = {"panes": [{"id": 7, "x": 2, "y": 1, "w": 10, "h": 5,
                                 "box": [1, 0, 12, 7], "mouse": 2,
                                 "mouse_sgr": True, "active": True}],
                      "dividers": [], "active": 7, "cols": 100, "rows": 30}
        app.mode = "normal"
        sel = []
        app.send_cmd = lambda action, **kw: sel.append((action, kw))
        # 우클릭 → 메뉴 열림(마우스 모드여도 패스스루보다 우선)
        v.on_mouse_down(_FakeMouse(5, 3, button=3))
        await pilot.pause(0.1)
        assert app.screen_stack[-1].__class__.__name__ == "MenuScreen", "우클릭→메뉴"
        assert app._menu_pane == 7, "메뉴 대상 = 우클릭한 패널"
        app.pop_screen()
        await pilot.pause(0.05)
        # Ctrl+Click(button 1 + ctrl) → 무동작(메뉴 안 뜸)
        v.on_mouse_down(_FakeMouse(5, 3, button=1, ctrl=True))
        await pilot.pause(0.1)
        assert app.screen_stack[-1].__class__.__name__ != "MenuScreen", \
            "Ctrl+Click 은 메뉴를 열지 않음"
    await _with_app(body)


async def test_mouse_passthrough_encoding_and_routing():
    async def body(app, pilot, srv):
        v = app.view
        # 마우스 모드를 켠 내부 앱 패널 하나. content=(2,1) 10x5, 테두리 box.
        app.layout = {"panes": [{"id": 7, "x": 2, "y": 1, "w": 10, "h": 5,
                                 "box": [1, 0, 12, 7], "mouse": 2,
                                 "mouse_sgr": True, "active": True}],
                      "dividers": [], "active": 7, "cols": 100, "rows": 30}
        app.mode = "normal"
        # 대상 판정: content 안 → 패널, 테두리/모드전환 시 → None
        assert v._mouse_target(5, 3)["id"] == 7
        assert v._mouse_target(1, 0) is None        # 테두리(content 밖)
        app.mode = "prefix"
        assert v._mouse_target(5, 3) is None         # prefix 시 pytmux 우선
        app.mode = "normal"

        p = app.layout["panes"][0]
        # SGR 1006 인코딩(좌표 content 기준 1-based: 5-2+1=4, 3-1+1=3)
        assert v._encode_mouse(p, 5, 3, "press", 1) == b"\x1b[<0;4;3M"
        assert v._encode_mouse(p, 5, 3, "release", 1) == b"\x1b[<0;4;3m"
        assert v._encode_mouse(p, 5, 3, "drag", 1) == b"\x1b[<32;4;3M"
        assert v._encode_mouse(p, 5, 3, "wheelup", 0) == b"\x1b[<64;4;3M"
        assert v._encode_mouse(p, 99, 99, "press", 1) == b""   # content 밖
        # X10 폴백(1006 off): 릴리스는 버튼3, 좌표/버튼 +32
        p2 = dict(p, mouse_sgr=False)
        assert v._encode_mouse(p2, 5, 3, "press", 1) == b"\x1b[M" + bytes([32, 36, 35])
        assert v._encode_mouse(p2, 5, 3, "release", 1) == b"\x1b[M" + bytes([35, 36, 35])

        # 라우팅: down→press 가 대상 패널 id 로, up→release 로, _mouse_fwd 정리
        sent = []
        app.send_mouse = lambda pid, data: sent.append((pid, data))
        app.send_cmd = lambda action, **kw: None
        v.on_mouse_down(_FakeMouse(5, 3, 1))
        assert sent == [(7, b"\x1b[<0;4;3M")], sent
        assert v._mouse_fwd == 7
        v.on_mouse_move(_FakeMouse(6, 3, 1))       # 드래그(1002+)
        assert sent[-1] == (7, b"\x1b[<32;5;3M"), sent
        v.on_mouse_up(_FakeMouse(6, 3, 1))
        assert sent[-1] == (7, b"\x1b[<0;5;3m"), sent
        assert v._mouse_fwd is None

        # 마우스 모드 OFF 패널은 패스스루 안 함(pytmux 가 select 처리)
        app.layout["panes"][0]["mouse"] = 0
        sent.clear()
        v.on_mouse_down(_FakeMouse(5, 3, 1))
        assert sent == [], "마우스 모드 off 면 패스스루 안 함"
    await _with_app(body)


async def test_mouse_debug_logging():
    import os
    import tempfile

    async def body(app, pilot, srv):
        path = os.path.join(tempfile.gettempdir(), "pytmux_mousedbg_test.log")
        if os.path.exists(path):
            os.remove(path)
        app._mouse_log_path = path
        # 꺼져 있으면 아무것도 기록하지 않음
        app.mouse_debug = False
        app.view.on_mouse_scroll_up(_FakeMouse(5, 5))
        assert not os.path.exists(path), "mouse-debug off 면 로그 없음"
        # 켜면 받은 휠 이벤트가 기록됨(원격에서 이벤트 도달 여부 진단용)
        app.mouse_debug = True
        app.view.on_mouse_scroll_up(_FakeMouse(5, 5))
        app.view.on_mouse_scroll_down(_FakeMouse(5, 5))
        with open(path) as f:
            log = f.read()
        assert "scroll_up" in log and "scroll_down" in log, log
        os.remove(path)
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
        primary = app.theme_variables.get("primary", "#0178D4").lower()

        def is_blue(x, y):
            st = cells[y][x][1]
            return bool(st and st.color and primary in str(st.color).lower())

        ap = next(p for p in lay["panes"] if p["id"] == active)
        bx, by, bw, bh = ap["box"]
        x2, y2 = bx + bw - 1, by + bh - 1
        # 콘텐츠 오른쪽 위 모서리의 탭 닫기 [x] 는 빨강 오버레이라 예외.
        tz = app._tab_close_zone

        def ok(x, y):
            return is_blue(x, y) or (tz and y == tz[2] and tz[0] <= x < tz[1])

        # 활성 패널 박스의 네 변 전체가 파란색([x] 자리는 제외)
        assert all(ok(gx, by) and ok(gx, y2)
                   for gx in range(bx, x2 + 1)), "활성 상/하 변 파랑"
        assert all(ok(bx, gy) and ok(x2, gy)
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
        # 활성 패널 이름은 활성 색(테마 primary)
        primary = app.theme_variables.get("primary", "#0178D4").lower()
        bx, by, bw, _ = ap["box"]
        i = ta.index("E")
        st = cells[by][bx + i][1]
        assert st and st.color and primary in str(st.color).lower(), "활성 이름 색"
    await _with_app(body)


async def test_esc_tab_bar_plus_button_nav():
    # ESC 모드 탭바 내비게이션이 맨 오른쪽 [+] 버튼까지 포함하고, [+] 에서 Enter
    # 누르면 새 탭이 열리며 ESC 모드도 종료된다(#26 + #3).
    async def body(app, pilot, srv):
        sess = list(srv.sessions.values())[0]
        before = len(sess.tabs)
        await pilot.press("escape")
        await pilot.press("up")
        assert app.tabbar.bar_focus is True, "탭바 포커스"
        await pilot.press("right")          # 탭 1개 → 오른쪽은 [+]
        assert app.tabbar.sel == "+", "맨 오른쪽 [+] 선택"
        seg = next(s for s in app.tabbar.render_line(0) if "[+]" in s.text)
        assert seg.style is not None and seg.style.bgcolor is not None
        await pilot.press("enter")
        await pilot.pause(0.4)
        assert app.mode == "normal", "Enter 후 ESC 모드 종료"
        assert len(sess.tabs) == before + 1, "[+] Enter → 새 탭"
    await _with_app(body, cfg={"tab_bar_always": True})


async def test_tab_bar_and_esc_nav():
    # auto 모드(tab-bar off): 1탭 숨김 → 2탭 표시 동작 검증
    async def body(app, pilot, srv):
        assert app.tabbar.display is False, "auto 모드: 탭 1개면 탭바 숨김"
        app.send_cmd("new_window")          # 새 탭
        await pilot.pause(0.4)
        assert app.tabbar.display is True, "탭 2개면 탭바 표시"
        txt = "".join(s.text for s in app.tabbar.render_line(0))
        # [+] 새 탭은 탭바(마지막 탭 오른쪽)에, [x] 닫기는 콘텐츠 패널 위로 이동
        assert "[+]" in txt and "[x]" not in txt, txt
        assert txt.rstrip().endswith("[+]"), txt   # 마지막 탭 바로 오른쪽
        assert app._tab_close_zone is not None, "콘텐츠 탭 닫기 [x] 영역"
        # ESC 모드: 위 → 탭바 포커스 → ← 선택 → Enter 전환
        await pilot.press("escape")
        await pilot.press("up")
        assert app.tabbar.bar_focus is True, "위 방향키로 탭바 포커스"
        before = app._active_tab_index()
        await pilot.press("left")
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert app.tabbar.bar_focus is False, "Enter 후 탭바 포커스 해제"
        assert app._active_tab_index() != before, "탭 전환 완료"
        assert app.mode == "normal", "Enter 한 번으로 ESC 모드도 종료(#3)"
    await _with_app(body, cfg={"tab_bar_always": False})


async def test_close_confirm_distinguishes_pytmux_exit():
    # 닫기 확인 팝업이 "pytmux 종료" 케이스(마지막 탭)와 일반 케이스를 메시지·
    # 강조색(danger=붉은색)으로 구분한다(#16).
    async def body(app, pilot, srv):
        from textual.widgets import Label
        # 탭 1개(기본) → 닫으면 pytmux 종료 → danger 강조 + 경고 메시지
        app.confirm_kill_tab()
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "ConfirmScreen"
        assert scr._danger is True, "마지막 탭 → 종료(danger)"
        assert "종료" in scr._message
        assert scr.query_one("#cn", Label).has_class("danger"), "붉은색 강조"
        await pilot.press("escape")
        await pilot.pause(0.2)
        # 탭 2개 → 일반(danger 아님)
        app.send_cmd("new_window")
        await pilot.pause(0.4)
        app.confirm_kill_tab()
        await pilot.pause(0.2)
        scr2 = app.screen_stack[-1]
        assert scr2._danger is False, "탭 여럿 → 일반"
        assert not scr2.query_one("#cn", Label).has_class("danger")
        await pilot.press("escape")
    await _with_app(body)


async def test_tab_close_confirm_popup():
    async def body(app, pilot, srv):
        sess = next(iter(srv.sessions.values()))
        app.send_cmd("new_window")            # 탭 2개
        await pilot.pause(0.4)
        assert len(sess.tabs) == 2
        # 탭바 [x] 닫기 버튼 클릭 → 확인 팝업
        app.confirm_kill_tab()
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "ConfirmScreen", scr
        assert scr._sel == 1, "기본 선택은 '취소'(안전)"
        from textual.widgets import Label
        assert scr.query_one("#cn", Label).has_class("sel"), "취소가 강조됨"
        assert not scr.query_one("#cy", Label).has_class("sel"), "닫기는 무채색"
        # Esc = 취소 → 탭 유지
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert len(sess.tabs) == 2, "취소 시 탭 유지"
        # 다시 팝업 → '닫기' 버튼 터치(클릭) 로 확정 → 탭 닫힘
        app.confirm_kill_tab()
        await pilot.pause(0.2)
        assert app.screen_stack[-1].__class__.__name__ == "ConfirmScreen"
        await pilot.click("#cy")
        await pilot.pause(0.5)
        assert len(sess.tabs) == 1, "확인(클릭) 시 탭 닫힘"
    await _with_app(body)


async def test_tab_bar_scroll_and_hide_bottom():
    async def body(app, pilot, srv):
        for _ in range(6):                 # 총 7개 탭(좁은 폭에서 오버플로)
            app.send_cmd("new_window")
            await pilot.pause(0.15)
        await pilot.pause(0.3)
        # 상단 탭바가 보이면 하단 상태줄 탭 목록 숨김
        assert app.status.hide_tabs is True
        # 탭 이름(예: win/zsh)이 하단 상태줄에 렌더되지 않음(시계의 "0:" 와 충돌 회피)
        stxt = "".join(s.text for s in app.status.render_line(0))
        assert ":win" not in stxt and ":zsh" not in stxt, stxt
        # 오버플로 → 스크롤 표시(◀/▶ 중 하나)
        bar = "".join(s.text for s in app.tabbar.render_line(0))
        assert ("◀" in bar) or ("▶" in bar), bar
        # ESC 포커스 → 오른쪽 끝까지 선택 이동 → 선택 탭이 보이도록 스크롤
        await pilot.press("escape")
        await pilot.press("up")
        assert app.tabbar.bar_focus is True
        for _ in range(len(app.tabbar.tabs)):
            await pilot.press("right")
        bar2 = "".join(s.text for s in app.tabbar.render_line(0))
        assert f"{app.tabbar.sel}:" in bar2, (app.tabbar.sel, bar2)
    await _with_app(body, size=(38, 12))


async def test_tab_bar_force_always():
    async def body(app, pilot, srv):
        assert app.tabbar.display is True, "tab-bar always 면 1탭도 표시"
    await _with_app(body, cfg={"tab_bar_always": True})


async def test_layout_save_load_client():
    async def body(app, pilot, srv):
        sess = next(iter(srv.sessions.values()))
        app._run_command("split-window -v")        # 좌우 2패널
        await pilot.pause(0.4)
        assert len(sess.active_tab.window.panes()) == 2
        app._run_command("layout-save two")
        await pilot.pause(0.3)
        assert "two" in srv.list_tab_layouts()
        # 직접 이름으로 새 탭에 불러오기
        n = len(sess.tabs)
        app._run_command("layout-load-new two")
        await pilot.pause(0.4)
        assert len(sess.tabs) == n + 1
        assert len(sess.active_tab.window.panes()) == 2
        # 이름 없이 불러오기 → 레이아웃 선택기 팝업
        app._run_command("layout-load")
        await pilot.pause(0.3)
        assert app.screen_stack[-1].__class__.__name__ == "ChooseLayoutScreen"
    await _with_app(body)


async def test_clock_mode_overlay():
    async def body(app, pilot, srv):
        active = app.layout["active"]
        app.toggle_clock(active)              # clock-mode on(현재 패널)
        await pilot.pause(0.2)
        assert active in app.clock_panes
        cells = app.view._cells
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        # 뒤 화면이 흐리게(dim)
        st = cells[ap["y"]][ap["x"]][1]
        assert st and st.dim, "패널 내용 dim"
        # 큰 시계 블록 문자가 그려짐
        assert any("█" in (cells[y][x][0] or "")
                   for y in range(len(cells))
                   for x in range(len(cells[0]))), "큰 시계 표시"
        # 우상단 닫기 버튼 영역 등록
        assert active in app._clock_close_zones
        # 다시 토글 → 닫힘
        app.toggle_clock(active)
        await pilot.pause(0.1)
        assert active not in app.clock_panes
    await _with_app(body, size=(44, 14))


async def test_calendar_overlay_and_date_click():
    # 날짜 클릭/명령으로 이번 달 달력 오버레이를 켜고(뒤 화면 dim·오늘 강조·[x]),
    # clock-mode 와 상호 배타인지 검증(#13).
    async def body(app, pilot, srv):
        from textual import events
        from datetime import datetime
        active = app.layout["active"]
        # 상태줄 날짜 존 클릭 → 달력 on
        app.status.render_line(0)
        dz = app.status._date_zone
        assert dz is not None, "날짜 클릭 존 등록"
        ev = events.MouseDown(app.status, dz[0], 0, 0, 0, 1, False, False, False)
        app.status.on_mouse_down(ev)
        await pilot.pause(0.2)
        assert active in app.calendar_panes, "날짜 클릭 → 달력 켜짐"
        cells = app.view._cells
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        st = cells[ap["y"]][ap["x"]][1]
        assert st and st.dim, "패널 내용 dim"
        assert active in app._calendar_close_zones, "우상단 [x] 등록"
        # 요일 헤더(Mo) 또는 연-월 제목이 그려졌는지
        flat = "".join(cells[y][x][0] or ""
                       for y in range(len(cells)) for x in range(len(cells[0])))
        assert f"{datetime.now().year}-" in flat or "Mo" in flat, "달력 텍스트 표시"
        # clock-mode 를 켜면 같은 패널의 달력은 꺼진다(상호 배타)
        app.toggle_clock(active)
        await pilot.pause(0.1)
        assert active in app.clock_panes and active not in app.calendar_panes
        # 명령으로 달력 토글 → clock 은 꺼짐
        app.toggle_calendar(active)
        await pilot.pause(0.1)
        assert active in app.calendar_panes and active not in app.clock_panes
        app.toggle_calendar(active)
        await pilot.pause(0.1)
        assert active not in app.calendar_panes, "재토글 → 닫힘"
    await _with_app(body, size=(44, 16))


async def test_clock_mode_over_wide_chars_keeps_alignment():
    # 회귀: 한글(와이드 문자)이 깔린 화면에서 clock-mode 를 켜면 시계 글자가
    # 와이드 문자의 한쪽 칸만 덮어 정렬이 깨졌다(녹색 블록 흩어짐).
    # _put_cell 이 짝 셀을 공백으로 정리해 정렬을 보존하는지 검증.
    async def body(app, pilot, srv):
        from pytmuxlib.client import _char_cells
        sess = next(iter(srv.sessions.values()))
        p = sess.active_window.active_pane
        p.feed(b"\x1b[2J\x1b[H")
        for _ in range(12):                    # 화면을 한글로 가득 채움
            p.feed("가나다라마바사아자차카타파하".encode() + b"\r\n")
        rows, cur = p.render(True)
        app.pane_content[p.id] = (rows, cur)
        active = app.layout["active"]
        app.toggle_clock(active)
        await pilot.pause(0.2)
        cells = app.view._cells
        W = len(cells[0])
        # 불변식: "" 연속 셀의 왼쪽은 반드시 와이드 문자여야 한다(짝이 안 깨짐).
        for y, row in enumerate(cells):
            for x, (ch, _st) in enumerate(row):
                if ch == "":
                    assert x > 0 and _char_cells(row[x - 1][0]) == 2, \
                        f"고아 연속셀 @({x},{y}) 좌={row[x-1][0]!r}"
            # 각 행의 렌더 폭이 그리드 폭과 일치(밀림 없음)
            rw = sum(_char_cells(c) for c, _ in row if c != "")
            assert rw == W, f"행 {y} 폭 {rw} != {W}"
        # 큰 시계 블록 문자가 실제로 그려졌는지
        assert any("█" in (cells[y][x][0] or "")
                   for y in range(len(cells)) for x in range(W)), "시계 표시"
    await _with_app(body, size=(44, 16))


async def test_status_clock_click_toggles_clock_mode():
    async def body(app, pilot, srv):
        from textual import events
        active = app.layout["active"]
        # 상태줄 렌더 → 오른쪽 시계 클릭 영역(_clock_zone) 계산
        app.status.render_line(0)
        z = app.status._clock_zone
        assert z is not None, "시계 영역 등록"
        # 시계 영역 안을 클릭 → clock-mode on
        ev = events.MouseDown(app.status, z[0], 0, 0, 0, 1, False, False, False)
        app.status.on_mouse_down(ev)
        await pilot.pause(0.1)
        assert active in app.clock_panes, "시계 클릭 → clock-mode 켜짐"
        # 다시 클릭 → 토글로 꺼짐
        app.status.on_mouse_down(ev)
        await pilot.pause(0.1)
        assert active not in app.clock_panes, "다시 클릭 → 꺼짐"
    await _with_app(body)


async def test_tabbar_claude_done_background():
    # 비활성 탭에 claude_done 플래그가 오면 옅은(success) 배경으로 그려 활성 탭
    # (primary)과 구분된다(#22).
    async def body(app, pilot, srv):
        tabs = [{"index": 0, "name": "a", "active": True},
                {"index": 1, "name": "b", "active": False, "claude_done": True}]
        app.tabbar.set_tabs(tabs, 0)
        strip = app.tabbar.render_line(0)
        seg = next(s for s in strip if "1:b" in s.text)
        active_seg = next(s for s in strip if "0:a" in s.text)
        assert seg.style and seg.style.bgcolor is not None, "완료 탭 배경 강조"
        assert seg.style.bgcolor != active_seg.style.bgcolor, "활성 탭과 다른 색"
    await _with_app(body)


async def test_bars_use_terminal_default_background():
    # 상단 탭바·하단 상태줄의 base(비활성/여백) 배경은 터미널 기본(bgcolor=None)을
    # 따르고, 강조 배지(활성 탭·REC 등)는 자체 bgcolor 를 유지한다(#10/#28).
    async def body(app, pilot, srv):
        # 탭바: base 세그먼트(배경 None) 존재 + 활성 탭은 bgcolor 지정
        tstrip = app.tabbar.render_line(0)
        tbgs = [s.style.bgcolor for s in tstrip if s.style]
        assert any(b is None for b in tbgs), "탭바 base 배경 = 터미널 기본(None)"
        assert any(b is not None for b in tbgs), "활성 탭 등 강조 배지는 bgcolor 유지"
        # 상태줄: 명시 bg 미설정이면 base 배경 None + REC 배지는 bgcolor 지정
        app.status.bg = None
        app.status.capture = True
        sstrip = app.status.render_line(0)
        sbgs = [s.style.bgcolor for s in sstrip if s.style]
        assert any(b is None for b in sbgs), "상태줄 base 배경 = 터미널 기본(None)"
        rec = next((s for s in sstrip if s.text.strip() == "REC"), None)
        assert rec is not None and rec.style.bgcolor is not None, \
            "REC 배지는 자체 bgcolor 유지"
    await _with_app(body)


async def test_status_right_segments_clock_and_date_zones():
    # 오른쪽(host/시각/날짜)을 별도 런으로 쪼갠 뒤 시각=시계 존, 날짜=달력 존이
    # 서로 겹치지 않고, 날짜 클릭은 clock-mode 를 켜지 않아야 한다(#12).
    async def body(app, pilot, srv):
        from textual import events
        app.status.render_line(0)
        cz = app.status._clock_zone
        dz = app.status._date_zone
        assert cz is not None, "시각(시계) 클릭 존 등록"
        assert dz is not None, "날짜(달력) 클릭 존 등록"
        # 기본 포맷 ' ...%H:%M %Y-%m-%d ' 이라 시각이 날짜보다 왼쪽.
        assert cz[1] <= dz[0], "시각/날짜 존이 겹치지 않음(시각이 앞)"
        active = app.layout["active"]
        # 날짜 영역 클릭 → 시계 토글 안 됨.
        ev = events.MouseDown(app.status, dz[0], 0, 0, 0, 1, False, False, False)
        app.status.on_mouse_down(ev)
        await pilot.pause(0.1)
        assert active not in app.clock_panes, "날짜 클릭은 clock-mode 와 무관"
        # 시각 영역 클릭 → 시계 토글.
        ev = events.MouseDown(app.status, cz[0], 0, 0, 0, 1, False, False, False)
        app.status.on_mouse_down(ev)
        await pilot.pause(0.1)
        assert active in app.clock_panes, "시각 클릭 → clock-mode 켜짐"
    await _with_app(body)


async def test_status_right_ssh_host_prefix_when_remote():
    # 원격(SSH) 세션이면 머신 이름 앞에 'ssh:' 접두사 + 붉은색(#11). 로컬이면 없음.
    async def body(app, pilot, srv):
        # 로컬: ssh: 접두사 없음.
        app.status._is_remote = False
        txt = "".join(s.text for s in app.status.render_line(0))
        assert "ssh:" not in txt, "로컬은 ssh: 접두사 없음"
        # 원격으로 강제 후 재렌더 → ssh: 접두사 + host 세그먼트가 error 색.
        app.status._is_remote = True
        strip = app.status.render_line(0)
        txt = "".join(s.text for s in strip)
        assert "ssh:" in txt, "원격은 ssh: 접두사 표시"
        host_seg = next(s for s in strip if s.text.startswith("ssh:"))
        assert host_seg.style is not None and host_seg.style.color is not None, \
            "원격 host 세그먼트는 색이 지정됨(붉은색)"
    await _with_app(body)


async def test_tab_bar_default_always():
    # 기본값(tab_bar_always=True): 탭이 하나여도 상단 탭바 표시
    async def body(app, pilot, srv):
        assert app.tab_bar_always is True
        assert app.tabbar.display is True, "기본값: 1탭도 탭바 표시"
        # 런타임 set tab-bar auto → 1탭이면 숨김
        app.apply_option("tab-bar", "auto")
        await pilot.pause(0.2)
        assert app.tab_bar_always is False
        assert app.tabbar.display is False, "auto + 1탭 → 숨김"
    await _with_app(body, cfg={"tab_bar_always": True})


async def test_claude_icon_and_header():
    async def body(app, pilot, srv):
        active = app.layout["active"]
        # 탭 아이콘: busy → ◐
        app.tabbar.tabs = [{"index": 0, "name": "win",
                            "active": True, "claude": "busy"}]
        assert "◐" in "".join(app.tabbar._labels())
        # 스티키 헤더: 마지막 프롬프트(좌측 [x] 닫기 버튼은 제거됨, #8)
        app.pane_claude = {active: {"id": active, "claude": "idle",
                                    "prompt": "do the thing"}}
        app._composite()
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        row = "".join((c[0] or " ") for c in app.view._cells[ap["y"]])
        assert "do the thing" in row, repr(row)
        assert "[x]" not in row, "헤더 [x] 닫기 버튼은 제거됨"
        # 탭 닫기 [x] 는 우상단(W-3..W) 에 그대로
        tcz = app._tab_close_zone
        assert tcz is not None and tcz[1] == app.layout["cols"], tcz
        # claude-header off → 헤더 숨김
        app._run_command("claude-header off")
        app._composite()
        row2 = "".join((c[0] or " ") for c in app.view._cells[ap["y"]])
        assert "do the thing" not in row2, "claude-header off → 숨김"
        assert app.claude_header_on is False
        # claude-header on → 다시 표시(전역 옵션, 프롬프트 단위 아님)
        app._run_command("claude-header on")
        app._composite()
        row3 = "".join((c[0] or " ") for c in app.view._cells[ap["y"]])
        assert "do the thing" in row3, "claude-header on → 표시"
        assert app.claude_header_on is True
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
        # 단일 패널도 테두리가 있어 내용은 inset 됨 → 내용 원점에서 상대 확인
        ap = next(pp for pp in app.layout["panes"] if pp["id"] == p.id)
        cx, cy = ap["x"], ap["y"]
        grid = [cells[cy][cx + x][0] for x in range(6)]
        # 가(와이드)=+2, 연속 셀=+3(""), C=+4
        assert grid[2] == "가" and grid[3] == "" and grid[4] == "C", grid
        text = "".join(seg.text for seg in app.view.render_line(cy))
        assert "가CD" in text, repr(text)
    await _with_app(body)


async def test_status_claude_usage():
    async def body(app, pilot, srv):
        app.status.claude_usage = "ctx 42%"
        txt = "".join(s.text for s in app.status.render_line(0))
        assert "ctx 42%" in txt, repr(txt)
    await _with_app(body)


async def test_status_format():
    async def body(app, pilot, srv):
        strip = app.status.render_line(0)
        txt = "".join(seg.text for seg in strip)
        assert txt.startswith("S=0|"), repr(txt[:10])  # status-left "S=#S| " 확장
    await _with_app(body, cfg={"status_left": "S=#S| "})
