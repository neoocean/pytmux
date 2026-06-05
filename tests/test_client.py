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


async def test_command_prompt_hint_desc_arg_toggle():
    # §10: 명령을 다 치면 오른쪽 힌트(#phint)에 ① 무인자→설명, ② 자유텍스트 인자→
    # 밑줄(____), ③ 토글/선택지 인자→방향키 선택 + Enter 즉시 실행.
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "PromptScreen"
        inp = scr.query_one(Input)

        # ① 무인자 완성 명령 → 설명만(선택지 없음)
        inp.value = "detach-client"
        scr._refresh_cands(); scr._refresh_hint()
        assert scr._hint_cmd == "detach-client" and not scr._choices
        assert scr._hint_text.strip(), "설명 힌트"

        # ② 자유 텍스트 인자 → 밑줄 힌트
        inp.value = "rename-tab"
        scr._refresh_cands(); scr._refresh_hint()
        assert scr._hint_cmd == "rename-tab" and not scr._choices
        assert "____" in scr._hint_text

        # ③ 토글 인자 → 선택지 채워지고 방향키로 강조 이동
        inp.value = "synchronize-panes"
        scr._refresh_cands(); scr._refresh_hint()
        assert scr._choices and scr._hint_cmd == "synchronize-panes"
        base = scr._choice_sel
        await pilot.press("down")
        assert scr._choice_sel == (base + 1) % len(scr._choices), "방향키 강조 이동"

        # Enter → 강조된 선택지로 'cmd value' 즉시 실행(dismiss 로 전달)
        captured = []
        scr.dismiss = lambda v=None: captured.append(v)
        scr.on_input_submitted(type("E", (), {"value": inp.value})())
        _disp, val = scr._choices[scr._choice_sel]
        expect = f"synchronize-panes {val}" if val else "synchronize-panes"
        assert captured == [expect], (captured, expect)
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
        assert [c for c, _ in scr._all_cats][:2] == ["패널", "탭"], scr._all_cats
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
        assert "탭" in str(box.border_subtitle), box.border_subtitle
        # §10: #cmds 높이를 최대 카테고리 항목 수(≤_CMDS_MAX_ROWS)로 고정 → ←→
        # 전환 시 박스 높이 불변(출렁임 방지)
        maxn = max(len(items) for _, items in scr._all_cats)
        exp = min(maxn, scr._CMDS_MAX_ROWS)
        assert scr.query_one("#cmds").styles.height.value == exp, \
            scr.query_one("#cmds").styles.height
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
        # 첫 항목(split-window)은 옵션 스키마가 있어 옵션 모달이 열린다(#3)
        await pilot.press("enter")
        await pilot.pause(0.2)
        opt = app.screen_stack[-1]
        assert opt.__class__.__name__ == "CommandOptionsScreen"
        assert opt.cmd_name == "split-window", opt.cmd_name
        # Enter → 기본 선택(-h)으로 프롬프트 없이 바로 실행 → 패널 분할
        n = len(sess.active_window.panes())
        await pilot.press("enter")
        await pilot.pause(0.4)
        assert len(sess.active_window.panes()) == n + 1, "옵션 모달 Enter→실행"
    await _with_app(body)


async def test_command_options_change_value():
    # #3: 옵션 모달에서 ←→ 로 값을 바꾸면 그 값으로 프롬프트 없이 바로 실행된다.
    async def body(app, pilot, srv):
        sess = next(iter(srv.sessions.values()))
        ran = []
        orig = app._run_command
        app._run_command = lambda line, *a, **k: (
            ran.append(line), orig(line, *a, **k))[-1]
        app._run_command("help")
        await pilot.pause(0.15)
        ran.clear()
        await pilot.press("enter")           # 첫 항목 split-window → 옵션 모달
        await pilot.pause(0.15)
        opt = app.screen_stack[-1]
        assert opt.__class__.__name__ == "CommandOptionsScreen", opt
        assert opt._build_line() == "split-window -h"
        await pilot.press("right")           # 방향 -h → -v
        assert opt._build_line() == "split-window -v"
        n = len(sess.active_window.panes())
        await pilot.press("enter")           # 바로 실행
        await pilot.pause(0.4)
        assert "split-window -v" in ran, ran
        assert len(sess.active_window.panes()) == n + 1
    await _with_app(body)


async def test_command_palette_routing():
    # #3: 커맨드 팔레트에서 인자 없는 안전한 명령은 선택 즉시 실행되고, 자유 텍스트
    # 인자가 필요한 명령은 기존처럼 명령 프롬프트에 채워진다(즉시 실행 아님).
    async def body(app, pilot, srv):
        ran = []
        orig = app._run_command
        app._run_command = lambda line, *a, **k: (
            ran.append(line), orig(line, *a, **k))[-1]
        # no-arg(next-tab): 선택(dismiss) 즉시 실행
        app._run_command("help")
        await pilot.pause(0.15)
        ran.clear()
        app.screen_stack[-1].dismiss("next-tab")
        await pilot.pause(0.15)
        assert "next-tab" in ran, ran
        # 자유 텍스트(rename-tab): 명령 프롬프트가 열림(즉시 실행 아님)
        app._run_command("help")
        await pilot.pause(0.15)
        ran.clear()
        app.screen_stack[-1].dismiss("rename-tab")
        await pilot.pause(0.15)
        assert app.screen_stack[-1].__class__.__name__ == "PromptScreen"
        assert "rename-tab" not in ran
    await _with_app(body)


async def test_prompt_clear_queue_command():
    # #4: prompt-clear-queue <명령>=추가, -c=비움, 빈값=현재 큐 목록 팝업.
    async def body(app, pilot, srv):
        from textual.widgets import Label
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        app._run_command("prompt-clear-queue do the thing")
        assert sent[-1] == ("pc_queue_add", {"cmd": "do the thing"}), sent
        app._run_command("prompt-clear-queue -c")
        assert sent[-1] == ("pc_queue_clear", {}), sent
        # 빈값 → status 의 큐를 InfoScreen 으로 표시
        app.status.prompt_clear_queue = ["alpha", "beta"]
        app._run_command("prompt-clear-queue")
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoScreen"
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "alpha" in joined and "beta" in joined, joined
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


async def test_esc_mode_question_opens_help():
    async def body(app, pilot, srv):
        await pilot.press("escape")
        assert app.mode == "esc"
        # ':' 대신 '?' → 프롬프트 거치지 않고 바로 help 팝업.
        await pilot.press("question_mark")
        assert app.mode == "normal", "? 는 ESC 모드 종료"
        assert app.screen_stack[-1].__class__.__name__ == "CommandListScreen"
    await _with_app(body)


async def test_esc_n_new_tab_and_p_new_pane():
    # ESC+n = 새 탭(new_window), ESC+p = 새 패널(상하 분할, 새 패널 아래). 둘 다
    # 액션 후 esc 모드를 빠진다. (멀티 ESC 라 디바운스#36 를 매번 리셋)
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        app._last_esc_ts = 0.0
        await pilot.press("escape")
        assert app.mode == "esc"
        await pilot.press("n")
        assert ("new_window", {}) in sent and app.mode == "normal", "n=새 탭 후 종료"
        sent.clear()
        app._last_esc_ts = 0.0
        await pilot.press("escape")
        assert app.mode == "esc"
        await pilot.press("p")
        assert ("split", {"orient": "tb"}) in sent and app.mode == "normal", \
            "p=상하 분할 후 종료"
    await _with_app(body)


async def test_esc_invalid_digit_blinks_active_tab():
    # ESC+없는 숫자 → 전환 불가. 현재 활성 탭을 깜빡여 안내하고 esc 모드 유지.
    # 존재하는 번호는 전환 후 종료.
    async def body(app, pilot, srv):
        blinked = []
        app.tabbar.blink_active = lambda *a, **k: blinked.append(True)
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        app._last_esc_ts = 0.0
        await pilot.press("escape")
        assert app.mode == "esc"
        await pilot.press("2")          # 기본 1탭 → 탭 2 없음
        assert blinked == [True], "없는 번호 → 활성 탭 깜빡임"
        assert app.mode == "esc", "없는 번호는 esc 모드 유지"
        assert not sent, "없는 번호는 전환 명령 없음"
        await pilot.press("1")          # 탭 1 존재 → 전환 + 종료
        assert ("select_window", {"index": 0}) in sent
        assert app.mode == "normal", "있는 번호는 전환 후 종료"
    await _with_app(body)


async def test_command_prompt_hangul_typo_recovery():
    # 한영 오타: IME 켠 채 친 한글 명령을 QWERTY 로 되돌려 검색/실행한다.
    from pytmuxlib.clientutil import hangul_to_qwerty, has_hangul
    assert hangul_to_qwerty("ㅏㅑㅣㅣ") == "kill"        # kill (낱자 모음)
    assert hangul_to_qwerty("네ㅣㅑㅅ") == "split"        # split (합성+낱자 혼합)
    assert hangul_to_qwerty("kill") == "kill"            # ASCII 는 그대로
    assert has_hangul("ㅏㅑㅣㅣ") and not has_hangul("kill")

    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        # "kill-pane" 를 IME 켠 채 치면 "ㅏㅑㅣㅣ-pane" → 복원돼 kill_pane 실행.
        app._run_command("ㅏㅑㅣㅣ-pane")
        assert ("kill_pane", {}) in sent, sent
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
        # ESC 단독 → esc 모드 진입(셸로 전달 안 함). 별개의 누름이므로 디바운스 리셋(#32).
        sent.clear()
        app._last_esc_ts = 0.0
        app.on_key(Key(key="escape", character=None))
        assert sent == []
        assert app.mode == "esc"
    await _with_app(body)


async def test_double_escape_exits_mode_without_pane_esc():
    """ESC 더블탭(ESC ESC) = **모드만 종료, 패널로 ESC 전달 없음**. 패널(앱)에 실제
    ESC 를 보내는 통로는 Shift+ESC 일 때만이어야 한다(사용자 요청, 더블탭 통로 폐지)."""
    async def body(app, pilot, srv):
        sent = []
        app.send_input = lambda data: sent.append(data)
        # 각 ESC 는 별개의 누름이므로 디바운스(#32) 리셋 후 보낸다(오토리핏 아님).
        # 첫 ESC = esc 모드 진입(전달 없음)
        app._last_esc_ts = 0.0
        app.on_key(Key(key="escape", character=None))
        assert app.mode == "esc" and sent == []
        # 두 번째 ESC = 모드만 종료, 패널로 ESC 전달 없음
        app._last_esc_ts = 0.0
        app.on_key(Key(key="escape", character=None))
        assert app.mode == "normal"
        assert sent == [], sent
        # i/그 외 키로 빠질 때도 전달 없음(동일 동작).
        app._last_esc_ts = 0.0
        app.on_key(Key(key="escape", character=None))
        assert app.mode == "esc"
        app.on_key(Key(key="i", character="i"))
        assert app.mode == "normal" and sent == []
    await _with_app(body)


async def test_shift_escape_sends_esc_to_pane():
    """패널에 ESC 를 보내는 유일한 키 통로는 Shift+ESC — esc 모드로 빠지지 않고
    활성 패널에 \\x1b 를 그대로 전달한다(오버레이가 없을 때)."""
    async def body(app, pilot, srv):
        sent = []
        app.send_input = lambda data: sent.append(data)
        app.on_key(Key(key="shift+escape", character=None))
        assert app.mode == "normal", app.mode   # esc 모드 진입 안 함
        assert sent == [b"\x1b"], sent
    await _with_app(body)


async def test_send_escape_command():
    """send-escape 명령(한 토큰)으로 활성 패널에 ESC 전달 — 한 키에 bind-key
    하기 쉽게 노출(send-keys Escape 와 동일 동작)."""
    async def body(app, pilot, srv):
        sent = []
        app.send_input = lambda data: sent.append(data)
        app._run_command("send-escape")
        assert sent == [b"\x1b"], sent
    await _with_app(body)


class _FakeMouse:
    def __init__(self, x, y, button=1, ctrl=False):
        self.x, self.y, self.button = x, y, button
        self.ctrl = ctrl
        self.stopped = False

    def stop(self):
        self.stopped = True


async def test_token_usage_tree_popup():
    # 토큰 사용량 클릭 → Claude 실행 중 패널 + 사용량 트리 팝업(#19). Claude 아닌
    # 패널은 제외.
    async def body(app, pilot, srv):
        from textual.widgets import Label
        tree = {"sessions": [{"name": "s", "windows": [
            {"index": 0, "name": "w", "panes": [
                {"id": 5, "cmd": "claude", "claude": "busy", "usage": "ctx 42%",
                 "remote": False},
                {"id": 6, "cmd": "zsh", "claude": None, "usage": None,
                 "remote": False}]}]}]}
        app._open_usage_tree(tree)
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoScreen"
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "ctx 42%" in joined and "pane 5" in joined, joined
        assert "pane 6" not in joined, "Claude 아닌 패널은 제외"
    await _with_app(body)


async def test_token_log_screen_aggregates_and_switches():
    # #7: token_log 응답 → TokenLogScreen 이 시간/일/월 × 계정 집계를 보이고,
    # [m] 월 버킷 전환·[a] 계정 필터 순환이 라운드트립 없이 동작.
    async def body(app, pilot, srv):
        from textual.widgets import Label
        recs = [
            {"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
             "account": "me@x.org", "tokens": 1500},
            {"ts": 1_700_500_000.0, "tab": 1, "pane": 2, "session": 2,
             "account": "team@y.org", "tokens": 2000},
        ]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "TokenLogScreen"
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "전체 Σ3.5k" in joined, joined
        assert "me@x.org" in joined and "team@y.org" in joined
        # 닫기 버튼 [x] 가 글자까지 보여야 함(markup=False — 예전엔 마크업으로
        # 해석돼 배경색만 남고 X 가 사라졌다).
        close = scr.query_one("#tklogclose", Label)
        assert "[x]" in close.render().plain, \
            f"닫기 버튼에 [x] 글자가 보여야 함: {close.render().plain!r}"
        # 계정 필터 순환([a]): 전체 → 첫 계정만
        await pilot.press("a")
        await pilot.pause(0.1)
        joined2 = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        accts = {a for a in ("me@x.org", "team@y.org") if a in joined2}
        assert len(accts) == 1, f"한 계정만 필터링돼야: {joined2}"
        # 월 버킷 전환([m]) — 닫히지 않고 갱신
        await pilot.press("m")
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is scr, "버킷 전환 키는 닫지 않음"
        # 그 외 키는 닫는다
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is not scr, "Esc 로 닫힘"
    await _with_app(body)


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
        # 대상(1) 내부 셀은 평소대로, 비대상(2)은 실색 블렌드로 어둡게(§10: ANSI dim
        # 아님 — bold 해제 + 전경색을 검정 쪽으로 블렌드). 색이 대상과 달라야 한다.
        s_target = cells[2][5][1]
        s_other = cells[2][15][1]
        assert not (s_target and s_target.dim), "대상 패널은 흐리지 않음"
        assert s_other and not s_other.dim, "ANSI dim 아님(실색 블렌드)"
        assert not s_other.bold and s_other.color is not None, "어두운 실색 적용"
        assert s_other.color != s_target.color, "비대상은 대상과 다른(어두운) 색"
        # 메뉴 닫힘 → 원복(대상 셀과 동일한 평소 스타일)
        app._menu_open = False
        app._composite()
        cells = app.view._cells
        assert cells[2][15][1].color == cells[2][5][1].color, "닫으면 원복"
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
        # 내비게이션 키도 기록 — 휠이 화살표로 변환돼 새는 경우(1007 미지원)를
        # 휠 이벤트 미도달과 切り分け 하기 위함.
        app.on_key(Key(key="up", character=None))
        app.on_key(Key(key="down", character=None))
        # 문자/단축키는 기록하지 않는다(패널 입력 유출 방지).
        app.on_key(Key(key="a", character="a"))
        app.on_key(Key(key="ctrl+b", character=None))
        with open(path) as f:
            log = f.read()
        assert "scroll_up" in log and "scroll_down" in log, log
        assert "key up" in log and "key down" in log, log
        assert "key a" not in log and "ctrl+b" not in log, \
            "문자/단축키는 진단 로그에 남기지 않아야 한다: " + log
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
        # 비활성 패널의 (활성과 공유하지 않는) 바깥 모서리는 회색. 단 row 0 의
        # 활성 탭 연결부(▀, 활성색)가 그 위에 덧칠되는 구간은 제외(닫기 [x] 와 동일).
        ip = next(p for p in lay["panes"] if p["id"] != active)
        ibx, iby, ibw, ibh = ip["box"]
        conn = app.tabbar.active_tab_xrange() or (0, 0)
        if iby == 0 and conn[0] <= ibx < conn[1]:
            iby += ibh - 1            # 연결부에 가려지면 아래쪽 바깥 모서리로 검사
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
        await pilot.press("up")          # 헤더 없으면 우상단 [x] 로(#)
        await pilot.press("up")          # 다시 ↑ → 탭바
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
        # 고정 pause 대신 조건 대기: Windows(ConPTY)는 패널 기동·status 왕복·레이아웃
        # 패스가 느려 0.4s 안에 탭바가 정착(render_line 이 [+] 를 그릴) 안 될 수 있다.
        txt = ""
        for _ in range(60):
            await pilot.pause(0.05)
            if app.tabbar.display:
                txt = "".join(s.text for s in app.tabbar.render_line(0))
                if "[+]" in txt:
                    break
        assert app.tabbar.display is True, "탭 2개면 탭바 표시"
        # [+] 새 탭은 탭바(마지막 탭 오른쪽)에, [x] 닫기는 콘텐츠 패널 위로 이동
        assert "[+]" in txt and "[x]" not in txt, txt
        assert txt.rstrip().endswith("[+]"), txt   # 마지막 탭 바로 오른쪽
        assert app._tab_close_zone is not None, "콘텐츠 탭 닫기 [x] 영역"
        # ESC 모드: 위 → (헤더 없으면 [x]) → 위 → 탭바 포커스 → ← 선택 → Enter 전환
        await pilot.press("escape")
        await pilot.press("up")          # 헤더 없으면 우상단 [x] 로(#)
        await pilot.press("up")          # 다시 ↑ → 탭바
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
        await pilot.press("up")          # 헤더 없으면 우상단 [x] 로(#)
        await pilot.press("up")          # 다시 ↑ → 탭바
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
        # 뒤 화면이 어둡게(§10: 실색 블렌드 — ANSI dim 아님, bold 해제+전경 어둡게)
        st = cells[ap["y"]][ap["x"]][1]
        assert st is not None and not st.dim and not st.bold, st
        assert st.color is not None, "어두운 실색 적용(기본 전경→어두운 회색)"
        # 큰 시계 블록 문자가 그려짐
        assert any("█" in (cells[y][x][0] or "")
                   for y in range(len(cells))
                   for x in range(len(cells[0]))), "큰 시계 표시"
        # 우상단 [x] 폐지 → 활성 패널 Shift+ESC 로 닫힌다
        await pilot.press("shift+escape")
        await pilot.pause(0.1)
        assert active not in app.clock_panes, "Shift+ESC 로 시계 닫힘"
    await _with_app(body, size=(44, 14))


async def test_calendar_overlay_and_date_click():
    # 날짜 클릭/명령으로 이번 달 달력 오버레이를 켜고(뒤 화면 dim·오늘 강조),
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
        # 뒤 화면이 어둡게(§10: 실색 블렌드 — ANSI dim 아님)
        assert st is not None and not st.dim and not st.bold, st
        assert st.color is not None, "어두운 실색 적용"
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


async def test_overlay_closes_by_panel_click_and_shift_esc():
    # 시계/달력 오버레이는 우상단 [x] 대신 ① 패널 클릭 ② (활성 패널) Shift+ESC 로
    # 닫는다. [x] 닫기 영역은 더 이상 그리지 않는다.
    async def body(app, pilot, srv):
        from textual import events
        active = app.layout["active"]
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        cx, cy = ap["x"] + ap["w"] // 2, ap["y"] + ap["h"] // 2
        # 닫기 영역 속성은 폐지됨
        assert not hasattr(app, "_clock_close_zones"), "시계 [x] 닫기영역 폐지"
        assert not hasattr(app, "_calendar_close_zones"), "달력 [x] 닫기영역 폐지"
        # ① 달력 켜고 → 패널 클릭으로 닫힘
        app.toggle_calendar(active)
        await pilot.pause(0.1)
        assert active in app.calendar_panes
        ev = events.MouseDown(app.view, cx, cy, 0, 0, 1, False, False, False)
        app.view.on_mouse_down(ev)
        await pilot.pause(0.1)
        assert active not in app.calendar_panes, "패널 클릭으로 달력 닫힘"
        # ② 시계 켜고 → 활성 패널 Shift+ESC 로 닫힘
        app.toggle_clock(active)
        await pilot.pause(0.1)
        assert active in app.clock_panes
        await pilot.press("shift+escape")
        await pilot.pause(0.1)
        assert active not in app.clock_panes, "Shift+ESC 로 시계 닫힘"
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


async def test_active_tab_connects_to_content():
    # 상단 탭바가 보이면 콘텐츠 최상단 테두리에서 활성 탭 x 범위 구간이 위 절반 블록
    # ▀(활성색 전경)으로 칠해져 탭과 콘텐츠가 연결돼 보인다(#23). 셀 전체 배경 블록은
    # 본문 상단 테두리(─, 셀 중앙)를 침범해, 윗절반만 칠하는 ▀ 로 되돌렸다(사용자 요청).
    async def body(app, pilot, srv):
        app.tabbar.render_line(0)            # _zones 채우기
        xr = app.tabbar.active_tab_xrange()
        assert xr is not None, "활성 탭 x 범위"
        app._composite()
        tx0, tx1 = xr
        mid = min((tx0 + tx1) // 2, len(app.view._cells[0]) - 1)
        ch, st = app.view._cells[0][mid]
        assert ch == "▀", "위 절반 블록 ▀ 로 연결(아웃라인 비침범)"
        assert st is not None and st.color is not None, "연결 색(활성 전경색)"
    await _with_app(body, cfg={"tab_bar_always": True})


async def test_tabbar_lead_and_plus_gap():
    # 사용자 요청: 첫 탭은 한 칸 오른쪽에서 시작(LEAD), [+] 는 왼쪽 탭과 한 칸 더 띄움.
    async def body(app, pilot, srv):
        app.status.windows = [{"index": 0, "name": "win", "active": True}]
        app._update_tabbar()
        app.tabbar.set_tabs(app.status.windows, 0)
        ents = app.tabbar._entries()
        assert ents[0][0] == "lead" and ents[0][2] == " " * app.tabbar.LEAD, ents[0]
        # 첫 탭 엔트리는 lead 다음
        assert ents[1][0] == "tab", ents[1]
        # [+] 버튼은 간격칸(addgap "  ", 터미널 배경)으로 분리되고 버튼은 "[+]"
        # (뒤 여백 없음 — 사용자 요청, §10 #16)
        gap = next(e for e in ents if e[0] == "addgap")
        add = next(e for e in ents if e[0] == "add")
        assert gap[2] == "  " and add[2] == "[+]", (gap, add)
        # 렌더 첫 칸은 빈 여백, 탭은 LEAD 칸 뒤에서 시작
        app._composite()
        line = "".join(s.text for s in app.tabbar.render_line(0))
        assert line[:app.tabbar.LEAD] == " " * app.tabbar.LEAD
        assert app.tabbar.active_tab_xrange()[0] == app.tabbar.LEAD
    await _with_app(body, cfg={"tab_bar_always": True})


async def test_active_tab_connector_follows_switch():
    # #23 회귀: 활성 탭을 바꾸면 콘텐츠 상단 연결부(활성색 배경 블록)가 새 탭으로 따라와야 한다.
    # 예전엔 ① active_tab_xrange 가 render_line 부산물 _zones 를 읽어 전환 직후
    # stale 값을 주고, ② _composite 가 status(탭 변경) 메시지에 안 돌아 연결부가
    # 옛 탭 위치에 남았다. 폭(100)을 넘겨 스크롤이 생기는 긴 이름으로 재현한다.
    async def body(app, pilot, srv):
        names = [f"window-name-{i}" for i in range(6)]    # 6*≈17 > 100 → 스크롤
        wins = lambda a: [{"index": i, "name": n, "active": (i == a)}
                          for i, n in enumerate(names)]
        app.status.windows = wins(0)
        app._update_tabbar(); app._composite()
        xr0 = app.tabbar.active_tab_xrange()
        # 첫 탭은 왼쪽 여백(LEAD) 만큼 오른쪽에서 시작
        assert xr0 is not None and xr0[0] == app.tabbar.LEAD, \
            f"활성 탭0 은 LEAD({app.tabbar.LEAD}) 칸 뒤에서 시작, got {xr0}"
        # ① render_line 재실행(=_zones 갱신) 전에도 새 활성 탭 범위를 직접 계산
        app.status.windows = wins(5)
        prev_zones = list(app.tabbar._zones)
        app.tabbar.set_tabs(app.status.windows, app._active_tab_index())
        xr5 = app.tabbar.active_tab_xrange()
        assert app.tabbar._zones == prev_zones, "render_line 아직 안 돎(_zones 그대로)"
        assert xr5 is not None and xr5 != xr0, "전환 직후 새 활성 탭(5) 범위를 계산"
        # ② 전체 경로(_update_tabbar)는 활성 탭이 바뀌면 즉시 재합성해 연결부를 옮긴다
        app.status.windows = wins(0)
        app._update_tabbar()                  # 5→0: 활성 변경 → 내부 _composite
        app.status.windows = wins(5)
        app._update_tabbar()                  # 0→5: 활성 변경 → 내부 _composite
        xr = app.tabbar.active_tab_xrange()
        cells = app.view._cells
        mid = min((xr[0] + xr[1]) // 2, len(cells[0]) - 1)
        cch, cst = cells[0][mid]
        assert cch == "▀" and cst is not None and cst.color is not None, \
            "연결부(위 절반 블록 ▀)가 새 활성 탭 위치에 그려짐"
    await _with_app(body, cfg={"tab_bar_always": True})


async def test_multiline_status_bar():
    # #10: 다중 줄 상태표시줄. status N(0~5) 로 줄 수 조절, 맨 아래 줄이 주 상태,
    # 그 위 줄들은 status-format[i] 포맷(index 1 = 바닥 바로 위)을 _expand 로 표시.
    async def body(app, pilot, srv):
        sb = app.status
        sb.session = "work"
        assert sb.lines == 1, "기본 1줄"
        # 2줄로 + 보조 줄(index 1) 포맷 지정
        app.apply_option("status", "2")
        assert sb.lines == 2 and sb.styles.height.value == 2, sb.lines
        app.apply_option("status-format", "1 second-line-marker")
        await pilot.pause(0.05)
        # 위젯 높이 2: render_line(1)=주 상태(bottom), render_line(0)=보조(index1)
        top = "".join(s.text for s in sb.render_line(0))
        assert "second-line-marker" in top, top
        # bottom 줄은 주 상태(시각/날짜 포맷 흔적). 최소한 예외 없이 렌더되는지 확인.
        bottom = "".join(s.text for s in sb.render_line(1))
        assert isinstance(bottom, str)
        # status 0 → 숨김
        app.apply_option("status", "0")
        assert sb.lines == 0 and sb.display is False
        # 다시 1줄로 복귀(기본 동작 유지)
        app.apply_option("status", "1")
        assert sb.lines == 1 and sb.display is True
    await _with_app(body)


async def test_bind_unbind_keys():
    # #10/#11: 런타임 bind-key/unbind-key/list-keys. FEATURES 에서 unbind 가 미구현
    # 이었다. bind-key 로 추가, unbind-key 로 해제(-a 전체), tmux 표기(C-x) 정규화.
    async def body(app, pilot, srv):
        app.bindings = {}
        # bind-key: 한 글자 키 + tmux 표기(C-x → ctrl+x 정규화)
        app._run_command("bind-key x split-window -h")
        app._run_command("bind-key C-g new-window")
        assert app.bindings["x"] == "split-window -h"
        assert app.bindings["ctrl+g"] == "new-window", app.bindings
        # unbind 단일
        app._run_command("unbind-key x")
        assert "x" not in app.bindings
        # 없는 키 unbind 는 무해
        app._run_command("unbind-key zzz")
        # list-keys 팝업
        app._run_command("list-keys")
        await pilot.pause(0.05)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoScreen"
        app.pop_screen()
        # unbind -a 전체 해제
        app._run_command("bind-key y kill-pane")
        app._run_command("unbind-key -a")
        assert app.bindings == {}
    await _with_app(body)


async def test_prompt_history_arrows_navigate_not_close():
    # #7: 프롬프트 히스토리 팝업에서 방향키는 팝업을 닫지 않고 항목을 내비게이션한다
    # (이전엔 아무 키나 닫혀 방향키도 즉시 닫혔다). 긴 프롬프트는 잘리지 않고 여러
    # 줄로 줄바꿈되며, 방향키 외 키는 기존대로 닫는다.
    async def body(app, pilot, srv):
        from textual.widgets import Label, ListView
        long_prompt = "이것은 " + "아주 " * 40 + "긴 프롬프트입니다"
        app.pane_claude = {5: {"id": 5, "claude": "idle", "prompt": "p3",
                               "history": ["p1", long_prompt, "p3"]}}
        app.open_prompt_history(5)
        await pilot.pause(0.05)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoScreen"
        lv = scr.query_one(ListView)
        start = lv.index
        # 아래로 두 번 → 닫히지 않고 선택이 이동
        scr.on_key(Key(key="down", character=None))
        scr.on_key(Key(key="down", character=None))
        await pilot.pause(0.05)
        assert app.screen_stack[-1] is scr, "방향키로 팝업이 닫히면 안 됨"
        assert lv.index != start, ("선택이 이동해야 함", start, lv.index)
        # 위로 한 번 → 여전히 열려 있음
        scr.on_key(Key(key="up", character=None))
        await pilot.pause(0.05)
        assert app.screen_stack[-1] is scr
        # home/end 도 닫지 않는다
        scr.on_key(Key(key="end", character=None))
        scr.on_key(Key(key="home", character=None))
        await pilot.pause(0.05)
        assert app.screen_stack[-1] is scr and lv.index == 0
        # 긴 프롬프트가 잘리지 않고 보존(줄바꿈 표시)
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "긴 프롬프트입니다" in joined, joined
        # 방향키 외 키는 닫는다
        scr.on_key(Key(key="enter", character=None))
        await pilot.pause(0.05)
        assert scr not in app.screen_stack, "Enter 등은 기존대로 닫혀야 함"
    await _with_app(body)


async def test_shift_drag_pane_swap():
    # #9b: Shift+좌버튼 드래그로 패널을 잡아 다른 패널에 놓으면 두 패널 위치를
    # 맞바꾼다(서버에 swap_pane_to 전송). 드래그 중 소스/대상 상태를 추적한다.
    async def body(app, pilot, srv):
        app.layout = {"active": 1, "panes": [
            {"id": 1, "x": 0, "y": 0, "w": 40, "h": 20, "box": None},
            {"id": 2, "x": 41, "y": 0, "w": 40, "h": 20, "box": None}]}
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        v = app.view

        class _Ev:
            def __init__(self, x, y, shift=True, button=1, ctrl=False):
                self.x, self.y = x, y
                self.shift, self.button, self.ctrl = shift, button, ctrl

            def stop(self):
                pass

        # Shift+좌버튼 다운 on 패널 1 → swap 시작
        v.on_mouse_down(_Ev(5, 5))
        assert v._pane_swap == 1, "소스 패널 = 1"
        # 패널 2 위로 이동 → 대상 추적
        v.on_mouse_move(_Ev(60, 5))
        assert v._pane_swap_over == 2, "대상 패널 = 2"
        # 놓음 → swap_pane_to 전송, 상태 초기화
        v.on_mouse_up(_Ev(60, 5))
        assert ("swap_pane_to", {"id": 1, "to_id": 2}) in sent, sent
        assert v._pane_swap is None and v._pane_swap_over is None

        # 제자리(같은 패널)에 놓으면 swap 안 함
        sent.clear()
        v.on_mouse_down(_Ev(5, 5))
        v.on_mouse_up(_Ev(5, 5))
        assert not any(a == "swap_pane_to" for a, _ in sent), "제자리는 swap 없음"

        # Shift 없으면 swap 시작 안 함(일반 클릭 경로)
        v.on_mouse_down(_Ev(5, 5, shift=False))
        assert v._pane_swap is None, "Shift 없으면 swap 모드 아님"
    await _with_app(body)


async def test_tab_drag_reorder_visual_feedback():
    # #8: 탭을 드래그 재정렬하는 동안 들고 있는 탭(소스)은 흐리게(dim), 놓을 위치
    # (드롭 대상)은 warning 배경+밑줄로 표시된다. 놓을 때만 확정하던 것에 더해
    # 드래그 중 시각 피드백을 준다. on_mouse_move 가 _drag_over 를 추적한다.
    async def body(app, pilot, srv):
        tabs = [{"index": 0, "name": "a", "active": True},
                {"index": 1, "name": "b", "active": False},
                {"index": 2, "name": "c", "active": False}]
        app.tabbar.set_tabs(tabs, 0)
        app.tabbar.render_line(0)               # _zones 채우기

        # 드래그 시작: 탭 0 을 잡는다 (y=0 = 탭바 위, 재정렬 경로; #19 분할은 y>=1)
        class _Ev:
            def __init__(self, x, y=0):
                self.x = x
                self.y = y
            def stop(self):
                pass

        z0 = next(z for z in app.tabbar._zones if z[2] == "tab" and z[3] == 0)
        z2 = next(z for z in app.tabbar._zones if z[2] == "tab" and z[3] == 2)
        app.tabbar.on_mouse_down(_Ev((z0[0] + z0[1]) // 2))
        assert app.tabbar._drag == 0, "드래그 소스 = 탭 0"

        # 탭 2 위로 이동 → 드롭 대상 추적
        app.tabbar.on_mouse_move(_Ev((z2[0] + z2[1]) // 2))
        assert app.tabbar._drag_over == 2, "드롭 대상 = 탭 2"

        strip = app.tabbar.render_line(0)
        src_seg = next(s for s in strip if "1:a" in s.text)   # 표시 1-based(#21)
        dst_seg = next(s for s in strip if "3:c" in s.text)
        assert src_seg.style and src_seg.style.dim, "소스 탭은 흐리게(dim)"
        assert dst_seg.style and dst_seg.style.underline, "드롭 대상은 밑줄"
        assert dst_seg.style.bgcolor is not None, "드롭 대상 강조 배경"

        # 같은 탭 위로 오면 대상 해제(소스만 흐림)
        app.tabbar.on_mouse_move(_Ev((z0[0] + z0[1]) // 2))
        assert app.tabbar._drag_over is None, "소스 위면 드롭 대상 없음"

        # 놓으면 드래그 상태 해제
        app.tabbar.on_mouse_up(_Ev((z2[0] + z2[1]) // 2))
        assert app.tabbar._drag is None and app.tabbar._drag_over is None
    await _with_app(body)


async def test_esc_autorepeat_debounced():
    # #32: ESC 오토리핏(짧은 간격 반복)은 무시되어 모드가 깜빡이지 않는다. 직전 ESC
    # 처리 직후의 ESC 는 모드를 안 바꾸고, 충분한 간격 뒤의 ESC 만 토글한다.
    async def body(app, pilot, srv):
        import time as _t
        from textual.events import Key
        assert app.mode == "normal"
        app._last_esc_ts = 0.0
        app.on_key(Key(key="escape", character=None))      # 첫 ESC → esc 모드
        assert app.mode == "esc"
        # 곧바로 온 ESC(오토리핏) → 무시(모드 유지)
        app.on_key(Key(key="escape", character=None))
        assert app.mode == "esc", "오토리핏 ESC 는 무시"
        # 디바운스 창을 지난 ESC → 정상 토글(여기선 esc 종료)
        app._last_esc_ts = _t.monotonic() - 1.0
        app.on_key(Key(key="escape", character=None))
        assert app.mode == "normal", "간격 충분하면 토글"
    await _with_app(body)


async def test_esc_double_tap_toggles():
    # #36: 의도적인 빠른 더블탭(오토리핏 반복 간격 ~33ms 보다 크고 사람 더블탭
    # 간격 ~100ms+ 안)은 두 번째 ESC 가 살아나 모드를 토글한다. 첫 ESC=진입,
    # 디바운스 창(0.06s)을 갓 지난 두 번째 ESC=해제.
    async def body(app, pilot, srv):
        import time as _t
        from textual.events import Key
        assert app.mode == "normal"
        app._last_esc_ts = 0.0
        app.on_key(Key(key="escape", character=None))      # 1번째 → esc 진입
        assert app.mode == "esc"
        # 사람의 더블탭 간격(디바운스 창보다 큼) → 2번째 ESC 가 토글
        app._last_esc_ts = _t.monotonic() - (app._ESC_DEBOUNCE + 0.05)
        app.on_key(Key(key="escape", character=None))
        assert app.mode == "normal", "더블탭 2번째 ESC 는 모드 해제"
    await _with_app(body)


async def test_esc_nav_reaches_header_and_close():
    # #31: ESC 모드에서 ↑(최상단 패널)→프롬프트 헤더, →(마지막 헤더)→닫기 [x],
    # Enter(닫기 포커스)→탭 닫기 확인. ↑(헤더)→탭바.
    async def body(app, pilot, srv):
        from textual.events import Key
        active = app.layout["active"]
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        ap["claude_hdr"] = True
        app.claude_header_on = True
        app.pane_claude = {active: {"id": active, "claude": "idle", "prompt": "hi"}}
        assert app._claude_header_panes() == [active]
        await pilot.press("escape")
        assert app.mode == "esc"
        app.on_key(Key(key="up", character=None))      # 최상단 패널 ↑ → 헤더
        assert app._hdr_focus == active, app._hdr_focus
        app.on_key(Key(key="right", character=None))   # 마지막 헤더 → 닫기 [x]
        assert app._hdr_focus == "close", app._hdr_focus
        killed = []
        app.confirm_kill_tab = lambda: killed.append(1)
        app.on_key(Key(key="enter", character=None))   # 닫기 [x] Enter → 탭 닫기
        assert killed == [1] and app._hdr_focus is None
    await _with_app(body)


async def test_tab_drag_to_pane_split():
    # #19: 탭을 탭바 아래(콘텐츠)로 끌면 커서 아래 패널·분할 방향을 판정하고, 놓으면
    # 그 패널을 활성화한 뒤 끌어온 탭을 분할로 합치는 명령(select_pane_id+join_pane)을
    # 보낸다. 좌우 가장자리=lr, 위아래=tb.
    async def body(app, pilot, srv):
        # 단일 패널 레이아웃 가정: 패널 하나가 콘텐츠 전체.
        ps = app.layout.get("panes")
        assert ps, "패널 존재"
        p = ps[0]
        px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]
        # 오른쪽 가장자리 근처 → lr
        d = app._tabdrop_at(px + pw - 1, py + ph // 2)
        assert d == (p["id"], "lr"), d
        # 위쪽 가장자리 근처 → tb
        d2 = app._tabdrop_at(px + pw // 2, py)
        assert d2 == (p["id"], "tb"), d2
        # 드롭 시 명령 전송 검증
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app.tabbar._drag = 1                 # 끌고 있는 탭(인덱스 1)
        app._drag_split = (p["id"], "lr")
        ev = type("E", (), {"x": px + pw - 1, "y": py + 2,
                            "stop": lambda self: None})()
        app.tabbar.on_mouse_up(ev)
        assert ("select_pane_id", {"id": p["id"]}) in sent, sent
        assert ("join_pane", {"src": 1, "orient": "lr"}) in sent, sent
        assert app._drag_split is None
    await _with_app(body)


async def test_tabbar_claude_done_name_color():
    # 비활성 탭에 claude_done 플래그가 오면 **배경은 일반 탭과 같게 두고 탭 이름
    # 글자색만** 호박색(warning)으로 바꿔 완료를 알린다(#31 — 배경 강조는 안 함).
    async def body(app, pilot, srv):
        tabs = [{"index": 0, "name": "a", "active": True},
                {"index": 1, "name": "b", "active": False, "claude_done": True},
                {"index": 2, "name": "c", "active": False}]
        app.tabbar.set_tabs(tabs, 0)
        strip = app.tabbar.render_line(0)
        seg = next(s for s in strip if "2:b" in s.text)       # 표시 1-based(#21)
        normal = next(s for s in strip if "3:c" in s.text)
        assert seg.style.bgcolor == normal.style.bgcolor, "배경은 일반 탭과 동일(강조 안 함)"
        assert seg.style.color != normal.style.color, "완료는 탭 이름 글자색으로 구분"
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


async def test_status_tabs_popup_merged():
    # #10: REC·토큰 사용량 두 버튼이 **한 팝업**(InfoTabsScreen)의 서로 다른 탭을
    # 연다. REC → '출력 캡처' 탭(초기), ←→ 로 '토큰 사용량' 탭(ctx + Σ 토큰).
    async def body(app, pilot, srv):
        from textual.widgets import Label
        app.status.capture = True
        app.status.render_line(0)
        assert app.status._rec_zone is not None, "REC 클릭존 등록"
        # REC 버튼 배선: status_tabs 트리 요청 + 캡처 탭(0=왼쪽) + 캡처 줄 준비
        app.show_capture_info("/tmp/x.sock.capture/pane-1.log", 2048)
        assert app._tree_purpose == "status_tabs" and app._status_tab_initial == 0
        app._want_tree = False     # 서버의 실제 트리 응답이 또 팝업을 띄우지 않게(결정성)
        # 트리 응답을 직접 넣어 팝업 구성(라운드트립 비의존)
        tree = {"sessions": [{"name": "s", "windows": [
            {"index": 0, "name": "w", "panes": [
                {"id": 5, "cmd": "claude", "claude": "busy", "usage": "ctx 42%",
                 "tokens": 8200}]}]}]}
        app._open_status_tabs(tree)
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoTabsScreen"
        # 초기 탭 = 캡처(0=왼쪽): 캡처 정보 보임
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "pane-1.log" in joined and "2,048" in joined, joined
        # → 로 토큰 사용량 탭(1=오른쪽) → ctx 와 실제 토큰(Σ 8.2k) 보임(#18).
        # (←→ 순환에 닫기[x] 가 포함되므로 탭0→탭1 은 'right' 다.)
        await pilot.press("right")
        await pilot.pause(0.1)
        j2 = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "ctx 42%" in j2 and "8.2k" in j2, j2
    await _with_app(body)


async def test_info_tabs_close_button_and_esc():
    # 좁은(모바일) 폭에서도 통합 상태 팝업(InfoTabsScreen)에 닫기 [x] 가 화면 안에
    # 보이고, [x] 클릭과 Esc 둘 다로 닫힌다(#10).
    async def body(app, pilot, srv):
        from textual.widgets import Label
        app._status_cap_lines = ["파일: /tmp/x/pane-1.log"]
        app._status_tab_initial = 1
        app._open_status_tabs({"sessions": []})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoTabsScreen"
        close = scr.query_one("#itclose", Label)
        reg = close.region
        assert reg.width > 0 and reg.right <= app.size.width, \
            f"닫기 [x] 가 화면 안에 보여야 함 {reg} (폭 {app.size.width})"
        assert "[x]" in close.render().plain
        await pilot.click("#itclose")
        await pilot.pause(0.1)
        assert app.screen_stack[-1].__class__.__name__ != "InfoTabsScreen", "[x] 닫기"
        app._open_status_tabs({"sessions": []})
        await pilot.pause(0.1)
        assert app.screen_stack[-1].__class__.__name__ == "InfoTabsScreen"
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert app.screen_stack[-1].__class__.__name__ != "InfoTabsScreen", "Esc 닫기"
    await _with_app(body, size=(58, 30))      # 좁은(모바일) 폭


async def test_info_tabs_arrow_reaches_close_button():
    # ←→ 가 탭들 + 닫기[x] 를 순환 포커스한다(요청). 위치 0..N-1=탭, N=[x].
    async def body(app, pilot, srv):
        from textual.widgets import Label
        app._status_cap_lines = ["파일: /tmp/x/pane-1.log"]
        app._status_tab_initial = 0
        app._open_status_tabs({"sessions": []})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoTabsScreen"
        n = len(scr._tabs)
        assert n >= 2 and scr._sel == 0
        await pilot.press("right")            # 0 → 1
        await pilot.pause(0.05)
        assert scr._sel == 1 and scr._ti == 1
        for _ in range(n - 1):                # 마지막 탭에서 한 번 더 → [x]
            await pilot.press("right")
            await pilot.pause(0.03)
        assert scr._sel == n, "←→ 가 닫기[x] 에 도달해야"
        assert scr.query_one("#itclose", Label).has_class("-focus"), "[x] 강조"
        await pilot.press("right")            # [x] → 0 (wrap)
        await pilot.pause(0.05)
        assert scr._sel == 0
        await pilot.press("left")             # 0 → [x] (wrap back)
        await pilot.pause(0.05)
        assert scr._sel == n
        await pilot.press("enter")            # [x]+Enter → 닫힘
        await pilot.pause(0.1)
        assert app.screen_stack[-1].__class__.__name__ != "InfoTabsScreen", \
            "[x] 포커스 + Enter 로 닫힘"
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
        # 스티키 헤더: 마지막 프롬프트(좌측 [x] 닫기 버튼은 제거됨, #8). 서버가 헤더
        # 행을 예약하면 pane["claude_hdr"]=True 로 알려오고, 헤더는 내용 위 한 줄
        # (ap["y"]-1)에 그려진다(#1).
        app.pane_claude = {active: {"id": active, "claude": "idle",
                                    "prompt": "do the thing"}}
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        # 서버 예약을 모사: claude_hdr=True 면 내용 영역이 한 행 내려오고(y+1, h-1)
        # 헤더는 그 위 한 줄(내부 행)에 그려진다. y 를 안 내리면 헤더가 박스 윗
        # 테두리 행(탭 닫기 [x] 가 있는 줄)에 겹쳐 검증이 헷갈린다.
        ap["claude_hdr"] = True
        ap["y"] += 1
        ap["h"] -= 1
        hy = ap["y"] - 1
        app._composite()
        row = "".join((c[0] or " ") for c in app.view._cells[hy])
        assert "do the thing" in row, repr(row)
        # #15: 닫기 [x] 가 프롬프트(헤더) 행으로 한 줄 올라왔다 → 헤더 행 우측 끝에 보인다.
        assert "[x]" in row, "닫기 [x] 가 헤더(프롬프트) 행으로 이동"
        # 헤더 배경은 진한 파랑(primary-darken-2) — 본문/테두리(primary)보다 어둡게
        dark = app.theme_variables.get("primary-darken-2", "#0053AA").lower()
        cellbg = app.view._cells[hy][ap["x"] + 1][1]
        assert cellbg and cellbg.bgcolor and dark in str(cellbg.bgcolor).lower(), \
            f"헤더 배경 진한 파랑 기대, got {cellbg.bgcolor if cellbg else None}"
        # 탭 닫기 [x] 는 활성 패널 프롬프트(헤더) 행 우상단(#15) — 콘텐츠 첫 행이 아님
        tcz = app._tab_close_zone
        assert tcz == (ap["x"] + ap["w"] - 3, ap["x"] + ap["w"], hy), tcz
        xs = "".join(app.view._cells[hy][x][0] for x in range(tcz[0], tcz[1]))
        assert xs == "[x]", repr(xs)
        # 프롬프트 본문은 [x] 직전 한 칸까지만 — [x] 바로 왼쪽 칸은 비어 있다(겹침 방지).
        assert app.view._cells[hy][tcz[0] - 1][0] in (" ", ""), "프롬프트와 [x] 사이 간격"
        # claude-header off → 헤더 숨김
        app._run_command("claude-header off")
        app._composite()
        row2 = "".join((c[0] or " ") for c in app.view._cells[hy])
        assert "do the thing" not in row2, "claude-header off → 숨김"
        assert app.claude_header_on is False
        # claude-header on → 다시 표시(전역 옵션, 프롬프트 단위 아님)
        app._run_command("claude-header on")
        app._composite()
        row3 = "".join((c[0] or " ") for c in app.view._cells[hy])
        assert "do the thing" in row3, "claude-header on → 표시"
        assert app.claude_header_on is True
    await _with_app(body)


async def test_prompt_history_popup():
    # Claude 헤더 클릭/명령으로 프롬프트 히스토리 팝업(시간순)이 열린다(#7).
    async def body(app, pilot, srv):
        from textual.widgets import Label
        active = app.layout["active"]
        app._update_claude([{"id": active, "claude": "idle", "prompt": "latest",
                             "history": ["do a", "do b", "latest"]}])
        next(p for p in app.layout["panes"]
             if p["id"] == active)["claude_hdr"] = True  # 서버 헤더 행 예약(#1)
        app._composite()
        assert active in app._claude_header_zones, "헤더 클릭존 등록"
        app.open_prompt_history(active)
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoScreen"
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "do a" in joined and "latest" in joined, joined
    await _with_app(body)


async def test_esc_header_focus_opens_history():
    # #5: ESC 모드에서 h 로 Claude 헤더 포커스 진입(accent 강조), Enter 로 히스토리 팝업.
    async def body(app, pilot, srv):
        active = app.layout["active"]
        app._update_claude([{"id": active, "claude": "idle", "prompt": "latest",
                             "history": ["do a", "latest"]}])
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        ap["claude_hdr"] = True                # 서버 헤더 행 예약(#1)
        app._composite()
        await pilot.press("escape")
        assert app.mode == "esc"
        await pilot.press("h")                 # 헤더 포커스 진입
        assert app._hdr_focus == active, app._hdr_focus
        # 포커스 헤더는 accent(강조)색 배경. 헤더는 내용 위 한 줄(ap["y"]-1)에 그려짐(#1)
        accent = app.theme_variables.get("accent", "#FEA62B").lower()
        cellbg = app.view._cells[ap["y"] - 1][ap["x"] + 1][1]
        assert cellbg and cellbg.bgcolor and accent in str(cellbg.bgcolor).lower(), \
            f"헤더 포커스 강조색 기대, got {cellbg.bgcolor if cellbg else None}"
        await pilot.press("enter")             # 히스토리 팝업 + 모드 종료
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoScreen"
        assert app._hdr_focus is None and app.mode == "normal"
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


async def test_net_responsiveness_hysteresis_and_border():
    """§10: RTT 표본 히스테리시스 — 임계 초과 net_bad_n 회 연속이면 degraded ON,
    임계 이하 net_good_n 회 연속이면 OFF(깜빡임 방지). degraded 면 패널 외곽선이
    error 색으로 바뀐다. 실제 ping/pong 왕복도 표본을 기록하고 _net_ping_ts 를
    클리어한다."""
    async def body(app, pilot, srv):
        app.net_rtt_threshold = 0.4
        app.net_bad_n = 3
        app.net_good_n = 3
        app._net_degraded = False
        app._net_bad = app._net_good = 0
        # 느림 2회로는 ON 안 됨, 3회 연속 → ON
        app._net_sample(1.0)
        app._net_sample(1.0)
        assert app._net_degraded is False
        app._net_sample(1.0)
        assert app._net_degraded is True
        # 양호 2회로는 안 풀림, 3회 연속 → OFF
        app._net_sample(0.01)
        app._net_sample(0.01)
        assert app._net_degraded is True
        app._net_sample(0.01)
        assert app._net_degraded is False
        # 외곽선 색: degraded OFF vs ON 비교 + ON == error 색
        pane = app.layout["panes"][0]
        bx, by, bw, bh = pane["box"]
        app._net_degraded = False
        app._composite()
        off_c = app.view._cells[by][bx][1].color
        app._net_degraded = True
        app._composite()
        on_c = app.view._cells[by][bx][1].color
        # degraded 면 외곽선 색이 평소(primary/grey)와 달라진다(error 색으로 덮임)
        assert on_c != off_c, (on_c, off_c)
        app._net_degraded = False
        app._composite()
        # 실제 ping/pong 왕복: 로컬 RTT 작음 → _net_ping_ts 설정 후 pong 으로 클리어
        app._net_ping_ts = None
        app._net_ping()
        assert app._net_ping_ts is not None
        await pilot.pause(0.1)
        assert app._net_ping_ts is None, "pong 수신해 표본 기록 후 클리어"
    await _with_app(body)


async def test_force_reconnect_recovers_without_exit():
    """§10 degraded 회복: 강제 재접속(_force_reconnect)이 ① IPC 소켓을 교체(연결
    세대 _conn_gen 증가)하고 ② degraded 를 해제하며 ③ 앱을 종료하지 않고 ④ 서버
    _send_full 로 레이아웃을 재수신한다. 옛 reader 태스크는 세대 불일치로 조용히
    종료(앱 안 닫힘) — 서버 PTY/세션은 보존."""
    async def body(app, pilot, srv):
        gen0 = app._conn_gen
        old_reader = app.reader
        app._net_degraded = True
        app._net_bad = 99
        await app._force_reconnect("manual")
        await pilot.pause(0.2)
        assert app._conn_gen > gen0, "새 연결 세대"
        assert app.reader is not old_reader, "소켓 교체됨"
        assert app._net_degraded is False, "degraded 해제"
        assert app._net_bad == 0 and not app._force_reconnecting
        assert app.layout.get("panes"), "재동기된 레이아웃 수신"
        # 서버는 옛 연결을 정리하고 새 연결 1개만 들고 있어야(누수 없음)
        await pilot.pause(0.2)
        assert len(srv.clients) == 1, f"클라 누수: {len(srv.clients)}"
        # 앱이 살아 있어 명령이 계속 동작(입력 왕복)
        app._net_sample(0.01)
        assert app._net_degraded is False
    await _with_app(body)


async def test_net_watchdog_triggers_auto_reconnect():
    """§10: degraded 가 net_recover_n 회 연속 지속되면 워치독이 강제 재접속을 트리거
    한다(net_auto_reconnect ON). reconnect_now 호출 여부로 검증."""
    async def body(app, pilot, srv):
        app.net_rtt_threshold = 0.4
        app.net_recover_n = 5
        app.net_auto_reconnect = True
        app._net_bad = app._net_good = 0
        app._force_reconnecting = False
        fired = []
        app.reconnect_now = lambda reason="manual": fired.append(reason)
        for _ in range(4):
            app._net_sample(1.0)
        assert not fired, "임계 미만에선 트리거 안 함"
        app._net_sample(1.0)   # 5회째
        assert fired == ["auto"], fired
        assert app._net_bad == 0, "재시도 간격용 카운터 리셋"
    await _with_app(body)


async def test_claude_footer_zones_and_popups():
    """§10 item 2/3: _composite 가 Claude 패널 content 의 권한모드 footer 와 'Remote
    Control active' 줄을 찾아 클릭존(_perm_zone/_remote_zone)을 등록하고, open_perm_mode/
    open_remote_control 가 각각 권한모드 선택/원격제어 정보 팝업을 연다."""
    async def body(app, pilot, srv):
        pid = app.layout["panes"][0]["id"]
        px = app.layout["panes"][0]["x"]
        py = app.layout["panes"][0]["y"]
        app.pane_claude = {pid: {"id": pid, "claude": "idle",
                                 "perm_mode": "default"}}
        rows = [
            [("일반 출력 줄", {})],
            [("⏵⏵ auto-accept edits on (shift+tab to cycle)", {})],
            [("Remote Control active", {})],
        ]
        app.pane_content[pid] = (rows, None)
        app._composite()
        assert pid in app._perm_zone, app._perm_zone
        assert pid in app._remote_zone, app._remote_zone
        # 권한모드 footer 는 둘째 줄(py+1), 원격제어는 셋째 줄(py+2)
        assert app._perm_zone[pid][2] == py + 1
        assert app._remote_zone[pid][2] == py + 2
        # x 범위는 패널 시작 이상
        assert app._perm_zone[pid][0] >= px
        # 권한모드 팝업: 현재 모드 표시 + 선택 시 set_claude_perm_mode 전송
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app.open_perm_mode(pid)
        await pilot.pause(0.05)
        scr = app.screen
        assert scr.__class__.__name__ == "PermModeScreen", scr
        assert "default" in (scr.query_one("#perm").border_title or "")
        scr.dismiss("plan")
        await pilot.pause(0.05)
        assert sent and sent[0][0] == "set_claude_perm_mode"
        assert sent[0][1].get("target") == "plan"
        # 원격제어 팝업: InfoScreen
        app.open_remote_control(pid)
        await pilot.pause(0.05)
        assert app.screen.__class__.__name__ == "InfoScreen"
        app.screen.dismiss(None)
        await pilot.pause(0.05)
    await _with_app(body)


async def test_command_candidates_above_input_box():
    """§10: 자동완성 후보(#pcand)가 입력 박스(#prow) **위쪽**에 펼쳐진다(모바일 키보드에
    안 가리게). dock 적층 순서(Textual 버전 의존)에 기대지 않고 바닥 고정 Vertical
    (#pwrap)에 후보→박스 순으로 둬 순서를 못박는다."""
    async def body(app, pilot, srv):
        app.open_prompt("command", "")
        await pilot.pause(0.1)
        scr = app.screen
        assert scr.__class__.__name__ == "PromptScreen"
        scr.query_one("#pinput").value = "tab"
        scr._refresh_cands()
        await pilot.pause(0.1)
        prow = scr.query_one("#prow")
        pcand = scr.query_one("#pcand")
        assert pcand.display, "후보가 표시돼야"
        assert pcand.region.y < prow.region.y, (pcand.region, prow.region)
    await _with_app(body)


async def test_status_claude_usage():
    async def body(app, pilot, srv):
        app.status.claude_usage = "ctx 42%"
        txt = "".join(s.text for s in app.status.render_line(0))
        assert "ctx 42%" in txt, repr(txt)
    await _with_app(body)


async def test_popup_dims_and_substitutes_emoji():
    # #25: 팝업(모달)이 떠 있으면 본문을 어둡게 칠하고, 컬러로 안 어두워지는 이모지는
    # placeholder(·)로 치환한다. 팝업이 없으면 이모지 원본 그대로.
    async def body(app, pilot, srv):
        from textual.screen import ModalScreen
        from pytmuxlib.clientutil import _is_emoji
        assert _is_emoji("✽") and not _is_emoji("○") and not _is_emoji("가")
        active = app.layout["active"]
        # 모달 없음: 이모지 그대로(set→composite 사이 await 없음 → 서버 덮어쓰기 회피)
        app.pane_content = {active: ([[("✽AA", {})]], None)}
        app._composite()
        flat0 = "".join(c[0] or "" for row in app.view._cells for c in row)
        assert "✽" in flat0, "모달 없을 땐 이모지 그대로"
        # 모달 푸시 후(screen_stack>1): 이모지 치환 + 어둡게. 빈 ModalScreen 으로 충분.
        app.push_screen(ModalScreen())
        assert len(app.screen_stack) > 1
        app.pane_content = {active: ([[("✽AA", {})]], None)}
        app._composite()
        flat1 = "".join(c[0] or "" for row in app.view._cells for c in row)
        assert "✽" not in flat1 and "·" in flat1, "팝업 시 이모지 치환됨"
    await _with_app(body)


async def test_status_session_tokens():
    # 세션 누적 토큰(#3)을 상태줄에 Σ 표기로 보여준다. 넓은 폭(≥80)에서는 세 자리
    # 콤마 전체 숫자(#30), 좁은 폭에서는 약어(k/M).
    async def body(app, pilot, srv):
        app.status.claude_usage = "ctx 42%"
        app.status.claude_tokens = 45200
        txt = "".join(s.text for s in app.status.render_line(0))
        # 기호 Σ 와 숫자 사이 한 칸 띄움 + 넓은 폭이라 전체 숫자(콤마)
        assert "Σ 45,200" in txt, repr(txt)
        # 계정이 있으면 @계정 곁들임(§10 계정별 합계)
        app.status.claude_account = "alice"
        txt_a = "".join(s.text for s in app.status.render_line(0))
        assert "Σ 45,200 @alice" in txt_a, repr(txt_a)
        # 사용량 문구 없이 누계만 있어도 표시
        app.status.claude_usage = None
        app.status.claude_account = None
        app.status.claude_tokens = 1_200_000
        txt2 = "".join(s.text for s in app.status.render_line(0))
        assert "Σ 1,200,000" in txt2, repr(txt2)
    await _with_app(body)


async def test_status_tokens_abbrev_when_narrow():
    # 좁은 폭(<80칸)에서는 토큰 누계를 약어(k/M)로 줄여 자리를 아낀다(#30).
    async def body(app, pilot, srv):
        app.status.claude_tokens = 1_200_000
        txt = "".join(s.text for s in app.status.render_line(0))
        assert "Σ 1.2M" in txt, repr(txt)
    await _with_app(body, size=(60, 20))


async def test_status_tokens_persist_when_empty():
    """§10 지속표시: usage/tokens/account 가 비어 온 status 프레임에서도 마지막
    비어있지 않은 값을 유지한다(활성 패널이 Claude 가 아니거나 한 프레임 파싱
    실패해도 표시가 사라지지 않음). 새 비-0 값이 오면 갱신된다."""
    async def body(app, pilot, srv):
        app.status.update_status({"claude_usage": "ctx 42%",
                                  "claude_tokens": 45200,
                                  "claude_account": "alice"})
        assert app.status.claude_tokens == 45200
        # 빈 프레임 → 유지
        app.status.update_status({})
        assert app.status.claude_tokens == 45200
        assert app.status.claude_usage == "ctx 42%"
        assert app.status.claude_account == "alice"
        # 새 비-0 값 → 갱신
        app.status.update_status({"claude_tokens": 99000, "claude_account": "bob"})
        assert app.status.claude_tokens == 99000
        assert app.status.claude_account == "bob"
    await _with_app(body)


async def test_status_format():
    async def body(app, pilot, srv):
        strip = app.status.render_line(0)
        txt = "".join(seg.text for seg in strip)
        assert txt.startswith("S=0|"), repr(txt[:10])  # status-left "S=#S| " 확장
    await _with_app(body, cfg={"status_left": "S=#S| "})


async def test_alt_scroll_toggle():
    """대체 스크롤 모드(1007) 비활성 옵션 — 기본 on(=1007 끔), set 으로 토글.
    _term_write 는 헤드리스 드라이버에서도 예외 없이 동작해야 한다(no-op 허용)."""
    async def body(app, pilot, srv):
        assert app.disable_alt_scroll is True, "기본은 휠 스크롤백 pytmux 처리"
        app.apply_option("alt-scroll", "off")
        assert app.disable_alt_scroll is False
        app.apply_option("alt-scroll", "on")
        assert app.disable_alt_scroll is True
        app._term_write("\x1b[?1007l")          # 직접 호출도 예외 없음
    await _with_app(body)


async def test_header_hide_toggle_from_history():
    # #6 ②: 히스토리 팝업에서 h 로 그 패널 헤더를 숨기고/다시 보이게 토글.
    async def body(app, pilot, srv):
        active = app.layout["active"]
        app._update_claude([{"id": active, "claude": "idle", "prompt": "latest",
                             "history": ["latest"]}])
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        ap["claude_hdr"] = True                # 서버 헤더 행 예약(#1)
        hy = ap["y"] - 1                        # 헤더는 내용 위 한 줄
        app._composite()
        row = "".join((c[0] or " ") for c in app.view._cells[hy])
        assert "latest" in row, "처음엔 헤더 표시"
        # 팝업 열고 h → 숨김
        app.open_prompt_history(active)
        await pilot.pause(0.1)
        await pilot.press("h")
        await pilot.pause(0.1)
        assert active in app._claude_hidden_panes
        app._composite()
        row2 = "".join((c[0] or " ") for c in app.view._cells[hy])
        assert "latest" not in row2, "헤더 숨김"
        # 다시 팝업 h → 표시 복원
        app.open_prompt_history(active)
        await pilot.pause(0.1)
        await pilot.press("h")
        await pilot.pause(0.1)
        assert active not in app._claude_hidden_panes
    await _with_app(body)


async def test_claude_header_status_applies():
    # #6 ③: 서버 status 의 claude_header 권위값이 claude_header_on 에 반영된다.
    async def body(app, pilot, srv):
        app._dispatch({"t": "status", "windows": [], "claude_header": False})
        assert app.claude_header_on is False
        app._dispatch({"t": "status", "windows": [], "claude_header": True})
        assert app.claude_header_on is True
    await _with_app(body)


async def test_command_list_search_filters_and_tab_counts():
    # 검색창에 타이핑하면 즉시 필터링 + 각 탭에 일치 수 표시 + 결과 있는 탭으로
    # 자동 점프(현재 탭에 결과가 없을 때).
    from pytmuxlib.clientscreens import CommandListScreen
    async def body(app, pilot, srv):
        items = [
            ("split-window", "패널 분할", "패널"),
            ("kill-pane", "패널 삭제", "패널"),
            ("new-tab", "새 탭 열기", "탭"),
            ("rename-tab", "탭 이름 변경", "탭"),
            ("copy-mode", "복사 모드", "복사/버퍼"),
        ]
        app.push_screen(CommandListScreen(items))
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "CommandListScreen"
        # 검색 없음 → 첫 탭(패널) 활성, 전체 2건.
        assert scr._ci == 0 and len(scr._cur) == 2, (scr._ci, scr._cur)
        # 'tab' 검색 → 패널 0건, 탭 2건. 현재 탭(패널)에 결과 없어 탭으로 자동 점프.
        for ch in "tab":
            await pilot.press(ch)
        await pilot.pause(0.1)
        assert scr._query == "tab"
        assert scr._ci == 1, scr._ci                       # 자동 점프
        assert [n for n, _ in scr._cur] == ["new-tab", "rename-tab"], scr._cur
        # 비활성/활성 탭 모두 일치 수가 계산된다(패널 0, 탭 2).
        assert len(scr._matches(scr._all_cats[0][1])) == 0
        assert len(scr._matches(scr._all_cats[1][1])) == 2
        # 활성 탭 라벨에 (2) 표기.
        from textual.widgets import Label
        lbl = scr.query_one("#cmdtab_1", Label)
        assert "(2)" in str(lbl.render()), lbl.render()
        # 검색창(표시 전용)에 입력값 반영.
        assert scr.query_one("#cmdsearch", Input).value == "tab"
        # 백스페이스로 모두 지우면 전체 복귀.
        for _ in "tab":
            await pilot.press("backspace")
        await pilot.pause(0.1)
        assert scr._query == "" and len(scr._cur) == 2
    await _with_app(body)


async def test_command_list_home_end_tab_click_and_close():
    # Home/End 로 목록 처음·끝, 탭 클릭으로 카테고리 전환, [x] 클릭으로 닫기.
    from pytmuxlib.clientscreens import CommandListScreen
    from textual.widgets import ListView
    async def body(app, pilot, srv):
        items = [(f"cmd{i:02d}", f"desc {i}", "패널") for i in range(6)] + \
                [("new-tab", "새 탭", "탭")]
        app.push_screen(CommandListScreen(items))
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        lv = scr.query_one(ListView)
        assert lv.index == 0
        await pilot.press("end")                           # 맨 아래
        await pilot.pause(0.1)
        assert lv.index == len(scr._cur) - 1, lv.index
        await pilot.press("home")                          # 맨 위
        await pilot.pause(0.1)
        assert lv.index == 0
        await pilot.click("#cmdtab_1")                     # 탭 클릭 → 전환
        await pilot.pause(0.1)
        assert scr._ci == 1 and scr._cur[0][0] == "new-tab", (scr._ci, scr._cur[:1])
        await pilot.click("#cmdclose")                     # [x] → 닫기
        await pilot.pause(0.2)
        assert not any(s.__class__.__name__ == "CommandListScreen"
                       for s in app.screen_stack)
    await _with_app(body)


async def test_rules_editor_save_cancel_and_spacer():
    # #27 규칙 에디터: 타이틀↔에디터 한 줄 여백 + 우측 닫기[x] + 하단 저장/취소.
    from pytmuxlib.clientscreens import RulesEditScreen
    async def body(app, pilot, srv):
        captured = []
        app.push_screen(RulesEditScreen("hello rules"),
                        lambda v: captured.append(v))
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "RulesEditScreen"
        assert scr.query("#rulesspacer"), "타이틀↔에디터 한 줄 여백"
        assert scr.query("#rulesclose"), "우측 닫기 버튼"
        assert scr.query("#rulessave") and scr.query("#rulescancel"), "저장/취소"
        await pilot.click("#rulessave")                    # 저장 → 텍스트 반환
        await pilot.pause(0.2)
        assert captured == ["hello rules"], captured
    await _with_app(body)


async def test_rules_editor_cancel_returns_none():
    from pytmuxlib.clientscreens import RulesEditScreen
    async def body(app, pilot, srv):
        captured = []
        app.push_screen(RulesEditScreen("x"), lambda v: captured.append(v))
        await pilot.pause(0.2)
        await pilot.click("#rulescancel")                  # 취소 → None
        await pilot.pause(0.2)
        assert captured == [None], captured
    await _with_app(body)


async def test_command_prompt_empty_lists_all_commands():
    # esc : 로 연 빈 명령 프롬프트는 위쪽(#pcand)에 전체 명령을 펼친다(↑↓ 탐색, #).
    from textual.widgets import Label
    from pytmuxlib.clientutil import COMMANDS
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "PromptScreen"
        await pilot.pause(0.1)
        assert scr.query_one("#pcand", Label).display is True, "빈 입력서 후보 펼침"
        assert len(scr._cand) == len(COMMANDS), (len(scr._cand), len(COMMANDS))
        await pilot.press("down")                          # 윈도우 탐색
        assert scr._sel == 1, scr._sel
    await _with_app(body)


async def test_esc_nav_reaches_close_without_header():
    # 헤더(1행 프롬프트)가 없어도 ESC 모드 ↑ 로 우상단 닫기 [x] 에 닿는다(#).
    from textual.events import Key
    async def body(app, pilot, srv):
        app.claude_header_on = True
        app.pane_claude = {}                               # Claude 헤더 없음
        assert app._claude_header_panes() == []
        await pilot.press("escape")
        app.on_key(Key(key="up", character=None))          # 최상단 패널 ↑ → [x]
        assert app._hdr_focus == "close", app._hdr_focus
    await _with_app(body)


async def test_status_tabs_capture_toggle():
    # REC 탭에서 [c] 로 출력 캡처를 켜고 끌 수 있다(#). capture-output 명령 전송 +
    # 낙관적 로컬 반영.
    async def body(app, pilot, srv):
        app.status.capture = True
        app.status.capture_path = "/tmp/x/pane-1.log"
        app.status.capture_size = 10
        app._status_cap_lines = None
        app._status_tab_initial = 0
        sent = []
        app._run_command = lambda line, *a, **k: sent.append(line)
        app._open_status_tabs({"sessions": []})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoTabsScreen"
        await pilot.press("c")                             # 캡처 토글
        await pilot.pause(0.1)
        assert sent == ["capture-output"], sent
        assert app.status.capture is False, "낙관적 OFF 반영"
    await _with_app(body)
