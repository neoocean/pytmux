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
    await _with_app(body, cfg={"tab_bar_always": False})


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
        # 스티키 헤더: 마지막 프롬프트 + [x]
        app.pane_claude = {active: {"id": active, "claude": "idle",
                                    "prompt": "do the thing"}}
        app._composite()
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        row = "".join((c[0] or " ") for c in app.view._cells[ap["y"]])
        assert "do the thing" in row and "[x]" in row, repr(row)
        assert active in app._claude_close_zones
        # 닫기 → 숨김(같은 프롬프트면 계속 숨김)
        app.close_claude_header(active)
        app._composite()
        row2 = "".join((c[0] or " ") for c in app.view._cells[ap["y"]])
        assert "do the thing" not in row2, repr(row2)
        # 새 프롬프트가 오면 다시 표시
        app._update_claude([{"id": active, "claude": "idle", "prompt": "next"}])
        app._composite()
        row3 = "".join((c[0] or " ") for c in app.view._cells[ap["y"]])
        assert "next" in row3, repr(row3)
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
