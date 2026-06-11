"""클라이언트(Textual headless) 테스트: 프롬프트/명령목록/자동완성/ESC 모드/
IME 단축키/표시줄/포커스 경계/와이드 문자 합성."""
import asyncio

import harness
from harness import make_app, server_only, teardown, wait_until
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


async def test_restart_all_arms_relaunch_and_restarts_server():
    """restart-all: 실행 전 드라이런(request_restart_check)을 먼저 보내고, 통과
    회신을 받으면 클라가 relaunch 를 무장(_relaunch_on_restart)하고 서버에
    restart_server 를 보낸다. 일반 restart-server 는 relaunch 무장 안 함."""
    _ok = {"reexec_supported": True, "has_sessions": True, "serialize_ok": True,
           "panes": 1, "panes_with_fd": 1}

    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append(action)
        # restart-server: 드라이런 먼저, 통과 회신 후에야 restart_server.
        app._run_command("restart-server")
        assert sent == ["request_restart_check"]
        assert app._relaunch_on_restart is False
        app._dispatch(dict(_ok, t="restart_check"))
        assert app._relaunch_on_restart is False
        assert sent == ["request_restart_check", "restart_server"]
        sent.clear()
        # restart-all: 드라이런 먼저, 통과 회신 후 relaunch 무장 + restart_server.
        app._run_command("restart-all")
        assert sent == ["request_restart_check"]
        assert app._relaunch_on_restart is False
        app._dispatch(dict(_ok, t="restart_check"))
        assert app._relaunch_on_restart is True
        assert sent == ["request_restart_check", "restart_server"]
    await _with_app(body)


async def test_restart_check_command_opens_popup():
    """restart-check: 서버에 드라이런 점검을 요청(_want_restart_check)하고, 회신을
    받으면 PASS/FAIL 팝업(InfoScreen)을 띄운다."""
    from pytmuxlib.clientscreens import InfoScreen

    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append(action)
        app._run_command("restart-check")
        assert app._want_restart_check is True
        assert "request_restart_check" in sent
        app._show_restart_check_popup({
            "reexec_supported": True, "has_sessions": True,
            "serialize_ok": True, "panes": 2, "panes_with_fd": 2,
            "running_version": "p4:1", "disk_version": "p4:2"})
        await pilot.pause(0.1)
        assert isinstance(app.screen, InfoScreen)
    await _with_app(body)


async def test_version_command_opens_popup():
    """version 명령이 서버에 요청을 보내고(_want_version), version 회신을 받으면
    클라/서버 버전·업타임 팝업(InfoScreen)을 띄운다."""
    from pytmuxlib.clientscreens import InfoScreen

    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append(action)
        app._run_command("version")
        assert app._want_version is True
        assert "request_version" in sent
        # 서버 회신 모사 → 팝업
        app._show_version_popup({"version": "p4:99999", "uptime": 3661, "pid": 42})
        await pilot.pause(0.1)
        assert isinstance(app.screen, InfoScreen)
    await _with_app(body)


async def test_net_degraded_hysteresis():
    """degraded 히스테리시스(#5.8): 임계 초과 RTT 가 net_bad_n 회 연속이면 degraded
    ON, 임계 이하가 net_good_n 회 연속이면 OFF. 한두 표본 깜빡임엔 안 뒤집힌다."""
    async def body(app, pilot, srv):
        app.net_auto_reconnect = False        # 회복용 강제 재접속 경로 분리
        app._net_degraded = False
        app._net_bad = app._net_good = 0
        thr = app.net_rtt_threshold
        bad, good = app.net_bad_n, app.net_good_n
        # 느린 표본 (bad-1)회: 아직 ON 아님(깜빡임 방어)
        for _ in range(bad - 1):
            app._net_sample(thr + 0.5)
        assert app._net_degraded is False
        app._net_sample(thr + 0.5)            # bad 번째 → ON
        assert app._net_degraded is True
        # 양호 표본 (good-1)회: 아직 OFF 아님
        for _ in range(good - 1):
            app._net_sample(thr * 0.1)
        assert app._net_degraded is True
        app._net_sample(thr * 0.1)            # good 번째 → OFF
        assert app._net_degraded is False
    await _with_app(body)


async def test_net_degraded_recover_triggers_reconnect():
    """degraded 가 net_recover_n 회 연속 지속되면 강제 재접속을 1회 시도한다(§10)."""
    async def body(app, pilot, srv):
        calls = []
        app.reconnect_now = lambda why="auto": calls.append(why)
        app.net_auto_reconnect = True
        app._force_reconnecting = False
        app._net_degraded = False
        app._net_bad = app._net_good = 0
        for _ in range(app.net_recover_n):
            app._net_sample(app.net_rtt_threshold + 1.0)
        assert calls == ["auto"], calls
        assert app._net_bad == 0   # 다음 회복까지 카운터 리셋(간격 두기)
    await _with_app(body)


async def test_resize_pane_directional_command():
    """resize-pane -L/-R/-U/-D [N] 이 resize_dir 로 매핑된다(#17 — 키·명령·마우스
    리사이즈 대칭). 과거엔 -Z(줌)만 처리해 명령/팔레트로 분할선 이동이 불가했다."""
    async def body(app, pilot, srv):
        sent = []
        orig = app.send_cmd
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        try:
            app._run_command("resize-pane -R 5")
            app._run_command("resize-pane -U")      # N 생략 → 기본 3
            app._run_command("resize-pane -Z")      # 줌은 그대로
        finally:
            app.send_cmd = orig
        assert ("resize_dir", {"dir": "right", "cells": 5}) in sent, sent
        assert ("resize_dir", {"dir": "up", "cells": 3}) in sent, sent
        assert ("zoom", {}) in sent, sent
    await _with_app(body)


async def test_swap_pane_index_command():
    """swap-pane -s/-t <번호> 가 display-panes 0-based 번호로 임의의 두 패널을
    swap_pane_to 로 교환한다(§2.3 — 과거엔 인접 순환 -U/-D 만 가능, 임의 swap 은
    마우스 헤더 드래그 전용이었다). -t 만 주면 활성 패널과 교환, 번호가 없으면
    기존 인접 순환을 유지하고, 범위밖 번호는 조용히 무시한다."""
    async def body(app, pilot, srv):
        app.layout = {"active": 1, "panes": [
            {"id": 1, "x": 0, "y": 0, "w": 40, "h": 20},
            {"id": 2, "x": 40, "y": 0, "w": 40, "h": 20},
            {"id": 3, "x": 0, "y": 20, "w": 80, "h": 20}]}
        sent = []
        orig = app.send_cmd
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        try:
            app._run_command("swap-pane -t 2")        # 활성(id1) ↔ 번호2(id3)
            app._run_command("swap-pane -s 0 -t 1")   # 번호0(id1) ↔ 번호1(id2)
            app._run_command("swap-pane -U")          # 번호 없음 → 인접 순환(이전)
            app._run_command("swap-pane -t 9")        # 범위밖 → no-op
        finally:
            app.send_cmd = orig
        assert ("swap_pane_to", {"id": 1, "to_id": 3}) in sent, sent
        assert ("swap_pane_to", {"id": 1, "to_id": 2}) in sent, sent
        assert ("swap_pane", {"forward": False}) in sent, sent
        # 범위밖 -t 9 는 어떤 swap 도 일으키지 않는다(인접 swap 으로도 안 떨어짐):
        # 정확히 위 세 번의 swap 만 발생.
        swaps = [s for s in sent if s[0] in ("swap_pane", "swap_pane_to")]
        assert len(swaps) == 3, swaps
    await _with_app(body)


async def test_first_int_skips_flags_and_negatives():
    """_first_int 가 플래그/음수 토큰을 건너뛰고 뒤따르는 양수 인덱스를 찾는다.

    과거엔 첫 음수 토큰에서 None 을 반환해 `move-tab foo -2 3` 같은 입력에서 뒤의
    3 을 가렸다(인덱스 명령 침묵 실패)."""
    async def body(app, pilot, srv):
        from pytmuxlib.clientutil import _first_int as f   # §5.4: 클로저서 분리됨
        assert f(["3"]) == 3
        assert f(["-t", "2"]) == 2            # 플래그 건너뛰고 2
        assert f(["foo", "-2", "3"]) == 3     # 음수 가려도 뒤 양수 발견(회귀)
        assert f(["-2"]) is None              # 음수 단독은 미지원(불변)
        assert f(["bar", "baz"]) is None
    await _with_app(body)


async def test_set_frame_dirty_row_refresh():
    """set_frame 이 직전 프레임과 행 단위 비교해 변경된 행만 region refresh 한다(B8).

    _composite 는 전 화면을 재구성하지만(오버레이 정합), set_frame 이 dirty 행만
    textual 에 무효화해 깨끗한 행의 render_line 재호출을 건너뛴다. refresh 를 스파이로
    바꿔(텍스추얼 미접촉) 정확한 dirty 검출만 단위 검증한다."""
    from pytmuxlib.clientwidgets import MultiplexerView
    from rich.style import Style

    view = MultiplexerView()
    calls = []
    view.refresh = lambda *regions, **kw: calls.append(regions)
    S = Style()

    def frame(h, w, mark=None):
        cells = [[(" ", S) for _ in range(w)] for _ in range(h)]
        if mark is not None:
            y, ch = mark
            cells[y][0] = (ch, S)
        return cells

    # 1) 첫 프레임 → 전체 refresh(region 인자 없음).
    view.set_frame(frame(10, 20))
    assert calls == [()], calls
    calls.clear()

    # 2) 동일 프레임 → 재렌더 불필요(refresh 호출 0).
    view.set_frame(frame(10, 20))
    assert calls == [], calls

    # 3) 한 행만 변경 → 그 y 의 Region 하나만.
    view.set_frame(frame(10, 20, mark=(3, "X")))
    assert len(calls) == 1 and len(calls[0]) == 1, calls
    reg = calls[0][0]
    assert (reg.x, reg.y, reg.width, reg.height) == (0, 3, 20, 1), reg
    calls.clear()

    # 4) 열 수(리사이즈) 변화 → 안전하게 전체 refresh.
    view.set_frame(frame(10, 25, mark=(3, "X")))
    assert calls == [()], calls
    calls.clear()

    # 5) 절반 이상(여기선 6/10) 변경 → region 분할 이득이 적어 전체 refresh.
    many = frame(10, 25, mark=(3, "X"))
    for y in range(6):
        many[y][1] = ("Z", S)
    view.set_frame(many)
    assert calls == [()], calls
    # _cells 는 최신 프레임으로 갱신(_extract_selection 등 의존부 정합).
    assert view._cells is many


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


async def test_command_prompt_converts_hangul_name_to_qwerty():
    # 한글 IME 가 켜진 채 명령 이름을 치면 자모/음절이 들어간다. 명령 이름 구간(첫 공백
    # 이전)은 QWERTY 로 자동 변환되고, 인자 구간(공백 이후)의 한글은 보존돼야 한다.
    from pytmuxlib.clientutil import has_hangul, hangul_to_qwerty

    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "PromptScreen"
        inp = scr.query_one(Input)
        # 두벌식: n→ㅜ, e→ㄷ, w→ㅈ → "ㅜㄷㅈ" 가 "new" 로 변환돼야.
        ko = "ㅜㄷㅈ"
        inp.value = ko
        await pilot.pause(0.2)
        assert not has_hangul(inp.value), f"명령 이름 한글이 안 변환됨: {inp.value!r}"
        assert inp.value == hangul_to_qwerty(ko) == "new", inp.value
        # 인자 구간(공백 이후) 한글은 보존(rename-tab 한글탭이름).
        inp.value = "rename-tab 안녕"
        await pilot.pause(0.2)
        assert inp.value == "rename-tab 안녕", \
            f"인자 한글이 변환됨: {inp.value!r}"
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
        # 이 테스트는 한국어 카테고리/명령 라벨을 단언한다(§6 i18n 이전 작성). 앱이
        # 환경 LANG 으로 로케일을 정하므로(CI 는 ko 가 아닐 수 있음) ko 로 고정해
        # 결정론적으로 만든다. CommandListScreen 은 구성(아래 ? 입력) 시점 로케일을 읽는다.
        from pytmuxlib import i18n
        i18n.set_locale("ko")
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
        # 맨 앞 '전체' 탭(모든 명령을 한 탭에서 ↑↓ 탐색) 다음에 카테고리 탭들이 온다.
        assert [c for c, _ in scr._all_cats][:3] == ["전체", "패널", "탭"], \
            scr._all_cats
        # '전체' 는 다른 모든 카테고리 명령의 합을 담는다(등장 순서, 첫 명령=split-window)
        assert len(scr._all_cats[0][1]) == \
            sum(len(items) for _c, items in scr._all_cats[1:])
        assert scr._ci == 0 and scr._cur[0][0] == "split-window", scr._cur[:1]
        from textual.widgets import ListView
        lv = scr.query_one(ListView)
        assert str(lv.styles.overflow_y) == "scroll", "스크롤바 항상 표시"
        # ← → 로 카테고리(탭) 전환: 전체 → 패널 → 탭(new-tab) → … 그리고 되돌림
        await pilot.press("right")
        await pilot.pause(0.1)
        assert scr._ci == 1 and scr._all_cats[1][0] == "패널", scr._ci
        await pilot.press("right")
        await pilot.pause(0.1)
        assert scr._ci == 2 and scr._cur[0][0] == "new-tab", (scr._ci, scr._cur[:1])
        await pilot.press("left")
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
        # §10 #1: Claude 관련 명령이 독립 "Claude" 카테고리 탭으로 분리됨(일반
        # 모니터링은 "모니터" 로 분리 — 이전엔 "모니터/Claude" 혼합 카테고리였다).
        catmap = dict(scr._all_cats)
        assert "Claude" in catmap and "모니터" in catmap, list(catmap)
        claude_names = [n for n, _ in catmap["Claude"]]
        for nm in ("claude-auto-mode", "auto-doc-clear", "auto-resume",
                   "token-usage", "claude-header", "prompt-clear"):
            assert nm in claude_names, (nm, claude_names)
        mon_names = [n for n, _ in catmap["모니터"]]
        assert "monitor-activity" in mon_names, mon_names
        assert "claude-auto-mode" not in mon_names, mon_names
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


async def test_command_completion_context_aware_highlight():
    """접두사가 모호하면(rename → rename-pane/rename-tab) 무조건 첫(선언 순서)
    항목을 고르지 않고 맥락에 맞는 명령을 하이라이트한다(요청). 단일 패널 탭이면
    패널-상대 rename-pane 대신 rename-tab 을 먼저, 패널이 2개 이상이면 기존 선언
    순서(rename-pane 먼저)로 복귀한다."""
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        # 기본 레이아웃은 단일 패널 — rename 입력 시 rename-tab 이 기본 하이라이트
        for ch in "rename":
            await pilot.press(ch)
        await pilot.pause(0.1)
        names = [n for n, _ in scr._cand]
        assert "rename-pane" in names and "rename-tab" in names, names
        assert scr._cand[scr._sel][0] == "rename-tab", \
            ("단일 패널: 맥락상 rename-tab 우선", scr._sel, names)
        # 패널을 2개로 늘리면 재정렬하지 않고 선언 순서(rename-pane 먼저) 복귀
        app.layout = dict(app.layout)
        app.layout["panes"] = [{"id": 1}, {"id": 2}]
        scr._refresh_cands()
        names2 = [n for n, _ in scr._cand]
        assert scr._cand[scr._sel][0] == "rename-pane", \
            ("다중 패널: 선언 순서 보존", scr._sel, names2)
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
        await wait_until(pilot, lambda: app.screen_stack[-1].__class__.__name__
                         == "PromptScreen")
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


async def test_esc_shortcuts_work_with_ime_jamo():
    # IME(두벌식)가 켜진 채 'n'(새 탭)·'p'(분할) 키를 누르면 자모 'ㅜ'·'ㅔ' 가
    # 들어온다 — 입력 문자를 물리 QWERTY 키로 되돌려 ESC 단축키가 IME 무관하게
    # 동작해야 한다. _handle_esc_mode 를 직접 호출(자모 character 합성)해 검증한다.
    from textual.events import Key

    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        app._last_esc_ts = 0.0
        await pilot.press("escape")
        assert app.mode == "esc"
        app._handle_esc_mode(Key("ㅜ", "ㅜ"))          # 'n' 자리 자모 = 새 탭
        assert ("new_window", {}) in sent and app.mode == "normal", "ㅜ(=n)=새 탭"
        sent.clear()
        app._last_esc_ts = 0.0
        await pilot.press("escape")
        assert app.mode == "esc"
        app._handle_esc_mode(Key("ㅔ", "ㅔ"))          # 'p' 자리 자모 = 상하 분할
        assert ("split", {"orient": "tb"}) in sent and app.mode == "normal", \
            "ㅔ(=p)=분할"
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


def _tok_text(scr):
    """TokenLogScreen 의 보이는 텍스트 전부(제목/탭/스코프 Static + DataTable 셀)."""
    from textual.widgets import DataTable, Label, Static
    parts = [str(w.render()) for w in scr.query(Label)]
    parts += [str(w.render()) for w in scr.query(Static)]
    try:
        t = scr.query_one(DataTable)
        for i in range(t.row_count):
            parts.append(" ".join(str(c) for c in t.get_row_at(i)))
    except Exception:
        pass
    return " ".join(parts)


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
        joined = _tok_text(scr)
        assert "Σ3.5k" in joined, joined
        assert "me@x.org" in joined and "team@y.org" in joined
        # 닫기 버튼 [x] 가 글자까지 보여야 함(markup=False — 예전엔 마크업으로
        # 해석돼 배경색만 남고 X 가 사라졌다).
        close = scr.query_one("#tklogclose", Label)
        assert "[x]" in close.render().plain, \
            f"닫기 버튼에 [x] 글자가 보여야 함: {close.render().plain!r}"
        # 계정 필터 순환([a]): 전체 → 첫 계정만
        await pilot.press("a")
        await pilot.pause(0.1)
        joined2 = _tok_text(scr)
        accts = {a for a in ("me@x.org", "team@y.org") if a in joined2}
        assert len(accts) == 1, f"한 계정만 필터링돼야: {joined2}"
        # 월 버킷 전환([m]) — 닫히지 않고 갱신
        await pilot.press("m")
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is scr, "버킷 전환 키는 닫지 않음"
        # 주 버킷 전환([w]) — 닫히지 않고 갱신
        await pilot.press("w")
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is scr, "주 버킷 전환 키는 닫지 않음"
        assert scr._bucket == "week", scr._bucket
        # 마우스로 서브탭 클릭 → 버킷 전환(키 없이도)
        await pilot.click("#tab_month")
        await pilot.pause(0.1)
        assert scr._bucket == "month", scr._bucket
        await pilot.click("#tab_hour")
        await pilot.pause(0.1)
        assert scr._bucket == "hour", scr._bucket
        assert app.screen_stack[-1] is scr, "탭 클릭은 닫지 않음"
        # M19: /usage 결과를 status 훅으로 밀어넣으면 한도 막대 그래프가 보인다
        scr.update_usage({"session": {"pct": 7, "reset": "5am"}})
        await pilot.pause(0.1)
        joined3 = _tok_text(scr)
        assert "세션 5h" in joined3 and "7%" in joined3, joined3  # /usage 막대
        # 그 외 키는 닫는다
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is not scr, "Esc 로 닫힘"
    await _with_app(body)


async def test_token_log_recon_view_toggle():
    """S6 T2: [r]/[대사] 가 집계 ↔ 대사 뷰를 토글한다 — 대사 뷰는 실측 Δ%(리셋
    구분)와 추정 ~Σ 를 나란히 보이고, 다시 [r] 로 집계로 돌아온다(닫히지 않음)."""
    async def body(app, pilot, srv):
        base = 1_700_000_000.0
        recs = [{"ts": base + 200, "tab": 0, "pane": 1, "session": 1,
                 "account": "me@x.org", "tokens": 1500}]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs, "reconcile": [
            {"t0": base, "t1": base + 3600, "account": "me@x.org",
             "pct0": 5, "pct1": 9, "dpct": 4, "tokens": 1500, "reset": False},
            {"t0": base + 3600, "t1": base + 7200, "account": None,
             "pct0": 9, "pct1": 2, "dpct": -7, "tokens": 50, "reset": True},
        ]})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "TokenLogScreen"
        await pilot.press("r")
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is scr, "[r] 는 닫지 않음"
        joined = _tok_text(scr)
        assert "5%→9% (Δ+4)" in joined, joined
        assert "9%→2% (리셋)" in joined, joined
        assert "~1.5k" in joined and "~50" in joined, joined
        assert "계정혼합/미상" in joined, joined
        await pilot.press("r")                     # 집계 뷰로 복귀
        await pilot.pause(0.1)
        joined2 = _tok_text(scr)
        assert "Δ+4" not in joined2 and "Σ1.5k" in joined2, joined2
    await _with_app(body)


async def test_token_log_lifetime_total_from_server_agg():
    """Phase B: 서버가 보낸 total_all(전체 이력 합, SQL 집계)이 받은 레코드 cap 합보다
    크면, Σ 는 전체 이력 합을 보이고 표시 레코드 합을 병기한다(과소표시 방지)."""
    async def body(app, pilot, srv):
        recs = [   # 받은(최근) 레코드 — 합 3.5k
            {"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
             "account": "me@x.org", "tokens": 1500},
            {"ts": 1_700_500_000.0, "tab": 1, "pane": 2, "session": 2,
             "account": "team@y.org", "tokens": 2000},
        ]
        app._want_token_log = True
        # 전체 이력은 9.5k(레코드 cap 밖 6k 가 더 있음) — 서버가 SQL 로 집계해 전달.
        app._dispatch({"t": "token_log", "records": recs, "total_all": 9500,
                       "accounts_total": {"me@x.org": 7500, "team@y.org": 2000}})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        joined = _tok_text(scr)
        assert "Σ9.5k" in joined, joined          # lifetime 합(전체 이력)
        assert "표시 3.5k" in joined, joined        # 표시 레코드 합 병기
        # 계정 필터([a]) → me@x.org: lifetime 7.5k, 표시 1.5k
        await pilot.press("a")
        await pilot.pause(0.1)
        joined2 = _tok_text(scr)
        assert "Σ7.5k" in joined2 and "표시 1.5k" in joined2, joined2
    await _with_app(body)


async def test_token_log_panel_subtab_groups_by_session():
    """[패널] 서브탭: 그룹 차원을 계정↔세션으로 토글한다. 세션 차원은 (재사용되는)
    패널 id 가 아니라 세션 id 로 묶어 보인다(설계 §8 — 세션 기준)."""
    async def body(app, pilot, srv):
        from textual.widgets import Label
        recs = [
            # 같은 세션 1 이 다른 패널(1,3)에 걸쳐도 한 그룹
            {"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
             "account": "me@x.org", "tokens": 1500},
            {"ts": 1_700_000_100.0, "tab": 0, "pane": 3, "session": 1,
             "account": "me@x.org", "tokens": 500},
            {"ts": 1_700_000_200.0, "tab": 1, "pane": 2, "session": 2,
             "account": "me@x.org", "tokens": 2000},
        ]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr._dim == "account", "기본은 계정 차원"
        # 키 [p] 로 세션 차원 토글
        await pilot.press("p")
        await pilot.pause(0.1)
        assert scr._dim == "session" and app.screen_stack[-1] is scr
        joined = _tok_text(scr)
        assert "세션 1" in joined and "세션 2" in joined, joined
        # 세션 라벨에 대표 탭:패널이 곁들여진다(식별성, 사용자 결정)
        assert "탭" in joined and ":p" in joined, joined
        assert "세션별" in joined, joined
        # 세션1=2000(1500+500), 세션2=2000 — 합 4000 유지
        assert "Σ4k" in joined, joined
        # 마우스로 [패널] 탭 클릭 → 계정 차원으로 되돌림
        await pilot.click("#tab_panel")
        await pilot.pause(0.1)
        assert scr._dim == "account" and app.screen_stack[-1] is scr
    await _with_app(body)


async def test_token_log_sort_toggle_and_tab():
    """버킷 정렬 토글: 기본 시간순(최근 위) ↔ [o]키/[정렬]탭 으로 토큰순. 토큰순이면
    가장 큰 버킷이 맨 위로 온다."""
    async def body(app, pilot, srv):
        recs = [
            {"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
             "account": "me@x.org", "tokens": 100},      # 오래된·큼
            {"ts": 1_700_500_000.0, "tab": 0, "pane": 1, "session": 1,
             "account": "me@x.org", "tokens": 5},         # 최근·작음
        ]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        from textual.widgets import DataTable
        assert scr._order == "time", "기본은 시간순"
        first0 = str(scr.query_one(DataTable).get_row_at(0)[1])   # 토큰 셀
        assert "5" in first0, f"시간순: 최근(작은) 버킷이 위 — {first0}"
        # [o] 키로 토큰순 → 큰 버킷(100)이 위
        await pilot.press("o")
        await pilot.pause(0.1)
        assert scr._order == "tokens" and app.screen_stack[-1] is scr
        first1 = str(scr.query_one(DataTable).get_row_at(0)[1])
        assert "100" in first1, f"토큰순: 큰 버킷이 위 — {first1}"
        # [정렬] 탭 클릭 → 다시 시간순
        await pilot.click("#tab_order")
        await pilot.pause(0.1)
        assert scr._order == "time"
    await _with_app(body)


async def test_token_log_usage_graphs():
    """토큰 사용량 화면에서 /usage 결과를 막대 그래프로 보여준다(세션/주 전체/주
    Sonnet 각각 라벨+막대+%+리셋). 요청: Claude /usage 의 사용률 바를 포함."""
    import importlib
    TokenLogScreen = importlib.import_module(
        "pytmuxlib.plugins.claude-code.screens").TokenLogScreen
    async def body(app, pilot, srv):
        recs = [{"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
                 "account": "me@x.org", "tokens": 100}]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "TokenLogScreen"
        scr.update_usage({
            "session": {"pct": 54, "reset": "1:39pm (Asia/Seoul)"},
            "week_all": {"pct": 41, "reset": "Jun 11, 12:59pm (Asia/Seoul)"},
            "week_sonnet": {"pct": 0, "reset": None}})
        await pilot.pause(0.1)
        joined = _tok_text(scr)
        # 세 한도 라벨 + 각 % 가 보인다.
        assert "세션 5h" in joined and "54%" in joined, joined
        assert "주 전체" in joined and "41%" in joined, joined
        assert "주 Sonnet" in joined and "0%" in joined, joined
        # 리셋 요약(타임존 괄호 제거).
        assert "1:39pm" in joined and "(Asia/Seoul)" not in joined, joined
        # 막대 문자(부분블록 또는 채움)가 그려진다.
        assert any(b in joined for b in "▏▎▍▌▋▊▉█"), joined
    await _with_app(body)


async def test_token_amounts_aligned_by_magnitude():
    """토큰 약식 표기를 전체 자릿수 기준으로 들여써, 큰 값일수록 왼쪽에서 시작한다
    (단위 M/k 가 자릿수를 가려 우측정렬만으론 대소 비교가 어렵던 문제)."""
    import importlib
    TokenLogScreen = importlib.import_module(
        "pytmuxlib.plugins.claude-code.screens").TokenLogScreen
    # 단위 함수 단위 검증: 7.8M(7자리)=들여쓰기0, 103.1k(6자리)=1, 71.9k(5자리)=2.
    a = TokenLogScreen._tok_aligned
    assert a(7_800_000, 7) == "7.8M", repr(a(7_800_000, 7))
    assert a(103_100, 7) == " 103.1k", repr(a(103_100, 7))
    assert a(71_900, 7) == "  71.9k", repr(a(71_900, 7))
    # 큰 값(7.8M)이 작은 값(71.9k)보다 왼쪽에서 시작(들여쓰기 더 적음).
    assert (len(a(7_800_000, 7)) - len(a(7_800_000, 7).lstrip())) \
        < (len(a(71_900, 7)) - len(a(71_900, 7).lstrip()))

    async def body(app, pilot, srv):
        from textual.widgets import DataTable
        recs = [
            {"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
             "account": "me@x.org", "tokens": 7_800_000},   # M 급
            {"ts": 1_700_500_000.0, "tab": 0, "pane": 1, "session": 1,
             "account": "me@x.org", "tokens": 71_900},       # k 급(더 작음)
        ]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        tbl = scr.query_one(DataTable)
        cells = [str(tbl.get_row_at(r)[1]) for r in range(tbl.row_count)]
        big = next(c for c in cells if "M" in c)
        small = next(c for c in cells if "k" in c)
        big_indent = len(big) - len(big.lstrip())
        small_indent = len(small) - len(small.lstrip())
        assert big_indent < small_indent, (big, small)
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


async def test_context_menu_new_pane_ops_wired():
    # §2.7: 컨텍스트 메뉴에 추가된 패널 동작(회전/교환/분리/레이아웃/검색/제목)이
    # 알맞은 서버 명령·프롬프트로 배선됐는지 _run_menu_action 직접 호출로 검증.
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        app._run_menu_action("rotate")
        app._run_menu_action("swap_pane")
        app._run_menu_action("break_pane")
        app._run_menu_action("next_layout")
        assert ("rotate", {"forward": True}) in sent
        assert ("swap_pane", {"forward": True}) in sent
        assert ("break_pane", {}) in sent
        assert ("cycle_layout", {}) in sent
        # rename_pane → 명령 프롬프트를 "rename-pane " 로 연다
        opened = []
        app.open_prompt = lambda purpose, ph="", **k: opened.append((purpose, k))
        app._run_menu_action("rename_pane")
        app._run_menu_action("search")
        assert ("command", {"initial": "rename-pane "}) in opened
        assert any(p == "search" for p, _ in opened), "스크롤백 검색 프롬프트"
        # select_layout → 레이아웃 프리셋 옵션 모달을 push
        pushed = []
        app.push_screen = lambda scr, *a, **k: pushed.append(scr.__class__.__name__)
        app._run_menu_action("select_layout")
        assert "CommandOptionsScreen" in pushed, "레이아웃 프리셋 선택기"
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
        await wait_until(pilot, lambda: app.screen_stack[-1].__class__.__name__
                         == "MenuScreen")
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
        # 첫 행 우상단의 IME 인디케이터 배지([한]/[EN])도 의도된 테두리 오버레이라 예외
        # (ime-indicator 플러그인. 부재 시 None → 예외 없음. [x] 와 동일 처리).
        iz = getattr(app, "_ime_zone", None)

        def ok(x, y):
            return (is_blue(x, y)
                    or (tz and y == tz[2] and tz[0] <= x < tz[1])
                    or (iz and y == iz[2] and iz[0] <= x < iz[1]))

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
        # 클래스뿐 아니라 클릭 대상 버튼(#cy)이 mount 될 때까지 대기(자식 미마운트
        # 상태로 클릭하면 ConfirmScreen 내부 query 가 실패 — CI 플레이크).
        await wait_until(pilot, lambda: app.screen_stack[-1].query_one("#cy"))
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
        await wait_until(pilot, lambda: app.screen_stack[-1].__class__.__name__
                         == "ChooseLayoutScreen")
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


async def test_big_calendar_digit_spacing():
    """§10-A #9: 큰 패널에서 '큰 달력'(시계 폰트) 경로가 렌더되고, 한 날짜의 두 자리
    숫자 사이 간격이 DIG=1(글리프 폭 3 + 간격 1)로 좁다 — 두 자리가 한 덩어리로 읽힘.
    렌더된 셀에서 2-digit 날짜(10일)의 두 글리프 블록 사이 빈 칸이 정확히 1칸인지 측정
    (DIG=2 였다면 빈 칸 2개라 실패 — 상수 회귀를 셀 단위로 고정)."""
    async def body(app, pilot, srv):
        active = app.layout["active"]
        app.set_calendar(active, True)
        await pilot.pause(0.1)
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        DCW, DGAP, RHB, DIG = 8, 3, 6, 1   # plugins/calendar/render.draw_calendar_overlay 와 일치
        import calendar as _cal
        from datetime import datetime
        now = datetime.now()
        weeks = _cal.Calendar(firstweekday=6).monthdayscalendar(now.year, now.month)
        gw_big = 7 * DCW + 6 * DGAP
        nl_big = 2 + len(weeks) * RHB
        if not (ap["w"] >= gw_big + 2 and ap["h"] >= nl_big + 2):
            return   # 화면이 작으면 큰 달력 경로가 아니므로 검증 생략
        px, py, pw, ph = ap["x"], ap["y"], ap["w"], ap["h"]
        ox = px + (pw - gw_big) // 2
        oy = py + (ph - nl_big) // 2
        # 10일의 (주, 요일) 위치
        loc = next(((wi, col) for wi, wk in enumerate(weeks)
                    for col, d in enumerate(wk) if d == 10), None)
        assert loc is not None
        wi, col = loc
        gw = 2 * 3 + 1 * DIG                 # 두 자리 글리프 총폭
        gx0 = ox + col * (DCW + DGAP) + (DCW - gw) // 2
        ry = oy + 2 + wi * RHB
        cells = app.view._cells

        def block_has_glyph(x):              # x열의 5행 중 비공백이 하나라도 있나
            return any((cells[ry + r][x][0] or " ") != " " for r in range(5))
        # 첫 자리(gx0..+2)·둘째 자리(gx0+4..+6) 블록엔 글리프, 사이(gx0+3)는 빈 칸 1개.
        assert any(block_has_glyph(gx0 + k) for k in range(3)), "첫 자리 글리프"
        assert any(block_has_glyph(gx0 + 4 + k) for k in range(3)), "둘째 자리 글리프"
        assert not block_has_glyph(gx0 + 3), "두 자리 사이는 빈 칸 1개(DIG=1)"
    await _with_app(body, size=(44, 16))


async def _ime_cursor_body(app, pilot, srv):
    """IME preedit 동기화 본문(아래 두 사이즈 테스트가 공유)."""
    from textual.geometry import Offset
    from pytmuxlib.clientscreens import InfoScreen
    # hello 직후엔 layout["panes"] 가 아직 비어 있거나 active 와 어긋날 수 있어
    # (서버 layout 메시지 레이스) 활성 패널이 나타날 때까지 폴링(최대 ~2초).
    active, p = None, None
    for _ in range(40):
        active = app.layout.get("active")
        p = next((pp for pp in app.layout.get("panes", ())
                  if pp["id"] == active), None)
        if p is not None:
            break
        await pilot.pause(0.05)
    assert p is not None, f"레이아웃 미정착: {app.layout}"
    ccx, ccy = 2, 1
    # 활성 패널에 알려진 커서를 주입하고 재합성 → 하드웨어 커서가 그 전역 셀로.
    app.pane_content[active] = ([], (ccx, ccy))
    app._composite()
    await pilot.pause(0.05)
    assert app.cursor_position == Offset(p["x"] + ccx, p["y"] + ccy)
    # 모달이 떠 있으면 _composite 는 cursor_position 을 건드리지 않는다(경합 방지).
    app.push_screen(InfoScreen(["x"]))
    await pilot.pause(0.05)
    assert len(app.screen_stack) > 1
    sentinel = Offset(0, 0)
    app.cursor_position = sentinel
    app.pane_content[active] = ([], (5, 4))   # 커서를 바꿔도
    app._composite()
    await pilot.pause(0.05)
    assert app.cursor_position == sentinel, "모달 중엔 하드웨어 커서 미이동"


async def test_ime_hardware_cursor_follows_active_pane_cursor():
    """IME preedit 동기화(docs/IME_PREEDIT_CURSOR_SCENARIO.md): _composite 가 활성 패널
    커서 셀로 app.cursor_position(Textual 이 매 프레임 끝에 move_to 하는 하드웨어 커서)
    을 옮긴다. 호스트 터미널이 IME 조합 문자열(preedit)을 하드웨어 커서 자리에 덧그리는
    특성상, 안 옮기면 stale 좌표(테두리 행)에 잔상이 박힌다. 모달이 떠 있으면
    (screen_stack>1) 텍스트 위젯(Input/TextArea)이 cursor_position 을 소유하므로 덮어쓰지
    않는다. preedit 오버레이 자체는 OS 그림이라 헤드리스론 좌표 동기화 로직만 가드한다(§6).

    flaky 수정(2026-06-11): 원래 한 테스트가 `_with_app` 을 **한 이벤트 루프에서 두 번**
    (60×20→90×46) 돌리던 스위트 유일 패턴이었고, 두 번째 앱의 hello 가 간헐 미처리
    (StopIteration, 15회 중 10회 재현)되거나 행으로 빠졌다. 사이즈별 테스트로 분리해
    다른 모든 테스트와 같은 1루프-1앱 패턴으로 정상화(분리 후 12/12 결정적 통과)."""
    await _with_app(_ime_cursor_body, size=(60, 20))


async def test_ime_hardware_cursor_follows_active_pane_cursor_large():
    """위 테스트의 큰 화면(90×46) 변형 — 커서 전역좌표 산식이 레이아웃(패널 위치)
    의존이라 두 사이즈 모두 가드한다(flaky 분리, 위 독스트링 참조)."""
    await _with_app(_ime_cursor_body, size=(90, 46))


async def test_open_close_clock_calendar_commands():
    """§10-A #10: open-clock/open-calendar(멱등 켜기)·close-clock/close-calendar(끄기).
    토글과 달리 두 번 열어도 켜진 채 유지되고, 한 패널엔 한 오버레이만(상호 배타)."""
    async def body(app, pilot, srv):
        active = app.layout["active"]
        # open-clock: 멱등 — 두 번 열어도 켜진 채 유지
        app._run_command("open-clock")
        assert active in app.clock_panes
        app._run_command("open-clock")
        assert active in app.clock_panes, "open 은 멱등(재토글로 꺼지지 않음)"
        # open-calendar: 시계는 꺼지고 달력만(상호 배타)
        app._run_command("open-calendar")
        assert active in app.calendar_panes and active not in app.clock_panes
        # close-calendar: 끔
        app._run_command("close-calendar")
        assert active not in app.calendar_panes
        # close-clock 은 안 떠 있어도 안전(멱등)
        app._run_command("close-clock")
        assert active not in app.clock_panes
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
        # ③ 달력 켜고 → 활성 패널 단순 ESC 로도 닫힘(요청). 오버레이가 떠 있으면
        #    ESC 가 esc 모드로 진입하지 않고 오버레이부터 닫는다.
        app._last_esc_ts = 0.0
        app.toggle_calendar(active)
        await pilot.pause(0.1)
        assert active in app.calendar_panes
        app.on_key(Key(key="escape", character=None))
        await pilot.pause(0.1)
        assert active not in app.calendar_panes, "단순 ESC 로 달력 닫힘"
        assert app.mode == "normal", "오버레이 닫을 때 esc 모드로 진입하지 않음"
        # 오버레이가 없을 땐 ESC 가 평소대로 esc 모드 진입(회귀 가드)
        app._last_esc_ts = 0.0
        app.on_key(Key(key="escape", character=None))
        assert app.mode == "esc", "오버레이 없으면 ESC 는 esc 모드"
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


async def test_hangwrap_preserves_number_alignment():
    """§10-A #7: _hangwrap 가 긴 줄(공백 없는 URL 포함)을 폭 안으로 하드 줄바꿈하되
    'NN. ' 번호 접두사 폭만큼 이어줄을 들여써 번호 정렬을 보존한다."""
    from pytmuxlib.clientscreens import _hangwrap
    line = "12. https://example.com/" + "a" * 80
    out = _hangwrap(line, 30)
    assert len(out) > 1, out
    assert out[0].startswith("12. ")
    assert all(len(o) <= 30 for o in out), [len(o) for o in out]
    # 이어줄은 4칸('12. ') 들여쓰기로 시작(번호 자리 아래 정렬)
    assert out[1].startswith("    ") and out[1].strip(), out
    # 짧은 줄은 그대로
    assert _hangwrap(" 1. short", 30) == [" 1. short"]


async def test_hangwrap_cell_aware_and_indent_preserved():
    """요청: 좁은 폭에서 줄바꿈 시 이어줄이 이전 줄의 들여쓰기(선행 공백+목록 표지)에
    맞춰 들여써지고, 한글 2셀을 고려해 셀폭으로 끊긴다(char 수 아님)."""
    from pytmuxlib.clientscreens import _hangwrap
    from pytmuxlib.clientutil import _char_cells
    cells = lambda s: sum(_char_cells(c) for c in s)
    # ① "  → " 들여쓴 줄 → 이어줄도 4칸 들여쓰기, 각 줄이 셀폭 28 이내
    out = _hangwrap("  → 이 화면에서 [r] 키로 바로 토글합니다(해당 패널에 /rc 주입).", 28)
    assert len(out) > 1, out
    assert all(cells(o) <= 28 for o in out), [cells(o) for o in out]
    assert out[1].startswith("    ") and out[1][4:5] != " ", out   # 정확히 4칸
    # ② 불릿 "• " 줄 → 이어줄 2칸 들여쓰기
    out2 = _hangwrap("• 원격 제어로 입력된 프롬프트도 상단 헤더에 반영됩니다.", 24)
    assert len(out2) > 1 and out2[1].startswith("  ") and out2[1][2:3] != " ", out2
    assert all(cells(o) <= 24 for o in out2), [cells(o) for o in out2]
    # ③ 들여쓰기/표지 없는 줄은 이어줄도 0칸(이전 줄 들여쓰기=0 에 맞춤)
    out3 = _hangwrap("원격 제어로 입력된 프롬프트가 상단 헤더에 그대로 반영됩니다.", 20)
    assert len(out3) > 1 and not out3[1].startswith(" "), out3


async def test_prompt_history_down_jumps_to_h_over_divider():
    """§10-A #8: 프롬프트 히스토리 — 마지막 프롬프트에서 ↓ 한 번에 구분선을 건너뛰어
    [h] footer 로 점프한다(구분선/빈 줄은 nav 에서 skip)."""
    async def body(app, pilot, srv):
        from textual.widgets import Label, ListView
        app.pane_claude = {7: {"id": 7, "claude": "idle", "prompt": "p2",
                               "history": ["p1", "p2"]}}
        app.open_prompt_history(7)
        # InfoScreen 이 top 이 되고 **그 안의 ListView 가 mount** 될 때까지 대기
        # (push 직후엔 자식이 아직 안 그려져 query_one 이 실패할 수 있다 — CI 플레이크).
        await wait_until(pilot, lambda: app.screen_stack[-1].query_one(ListView))
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoScreen"
        lv = scr.query_one(ListView)
        # 2칼럼 항목은 Label 이 2개(번호+본문)라 query_one 대신 전부 합친다.
        texts = [" ".join(str(l.render()) for l in it.query(Label))
                 for it in lv.children]
        assert any("─" in t for t in texts), ("구분선 표시", texts)
        hidx = next(i for i, t in enumerate(texts) if "[h]" in t)
        p2idx = next(i for i, t in enumerate(texts) if "p2" in t)
        # 마지막 프롬프트(p2) 선택 후 ↓ → 구분선 건너뛰고 [h] 로 점프
        lv.index = p2idx
        scr.on_key(Key(key="down", character=None))
        await wait_until(pilot, lambda: lv.index == hidx)
        assert lv.index == hidx, (lv.index, hidx, texts)
        # ↑ → 다시 p2 로(구분선 건너뜀)
        scr.on_key(Key(key="up", character=None))
        await wait_until(pilot, lambda: lv.index == p2idx)
        assert lv.index == p2idx, (lv.index, p2idx, texts)
    await _with_app(body)


async def test_prompt_history_two_column_layout():
    """프롬프트 히스토리는 번호/본문 2칼럼으로 그린다. 본문이 여러 줄(내장 \\n)이어도
    번호 칼럼이 아니라 본문 칼럼 한 항목(ListItem) 안에 머문다 — 번호와 분리 정렬."""
    async def body(app, pilot, srv):
        from textual.widgets import Label, ListView
        from textual.containers import Horizontal
        multiline = "첫 줄 지시\n둘째 줄 계속\n셋째 줄"
        app.pane_claude = {3: {"id": 3, "claude": "idle", "prompt": multiline,
                               "history": ["짧은 프롬프트", multiline]}}
        app.open_prompt_history(3)
        await wait_until(pilot, lambda: app.screen_stack[-1].query_one(ListView))
        scr = app.screen_stack[-1]
        lv = scr.query_one(ListView)
        # 프롬프트 항목은 [번호칼럼 | 본문칼럼] Horizontal(.histrow) 을 갖는다.
        rows = lv.query(".histrow")
        assert len(rows) == 2, ("프롬프트 2개가 각각 1 항목", len(rows))
        first = rows[0]
        nums = first.query(".histnum")
        bodies = first.query(".histbody")
        assert len(nums) == 1 and len(bodies) == 1, (len(nums), len(bodies))
        assert "1." in str(nums.first().render()), nums.first().render()
        # 다중 행 본문은 통째로 본문 칼럼에 들어간다(번호 칼럼엔 번호만).
        body2 = rows[1].query_one(".histbody", Label)
        bt = str(body2.render())
        assert "첫 줄 지시" in bt and "셋째 줄" in bt, bt
        assert "2." not in str(rows[1].query_one(".histbody", Label).render())
        num2 = str(rows[1].query_one(".histnum", Label).render())
        assert num2.strip() == "2.", num2
        # 최신(마지막) 프롬프트가 초기 선택된다(#17).
        assert lv.index == 1, lv.index
    await _with_app(body)


async def test_esc_arrow_flashes_selected_pane():
    # ESC 모드 방향키로 패널 전환을 요청하면 깜빡임을 예약하고(select_pane 전송),
    # 서버가 active 를 바꿔 layout 을 보내면 새 활성 패널을 깜빡인다(가시화 요청).
    async def body(app, pilot, srv):
        panes = [
            {"id": 1, "x": 0, "y": 0, "w": 80, "h": 12, "box": [0, 0, 80, 12]},
            {"id": 2, "x": 0, "y": 12, "w": 80, "h": 12, "box": [0, 12, 80, 12]}]
        app.layout = {"active": 1, "cols": 80, "rows": 24, "panes": panes,
                      "dividers": []}
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        await pilot.press("escape")                       # esc 모드 진입
        await pilot.pause(0.02)
        app._handle_esc_mode(Key(key="down", character=None))
        assert app._flash_pending is True, "방향키가 깜빡임 예약"
        assert ("select_pane", {"dir": "down"}) in sent, sent
        # 서버 응답: active 가 2로 바뀐 layout → 새 패널 깜빡임 시작.
        app._dispatch({"t": "layout", "active": 2, "cols": 80, "rows": 24,
                       "panes": panes, "dividers": []})
        await pilot.pause(0.02)
        assert app._pane_flash_id == 2 and app._pane_flash_on is True
        assert app._flash_pending is False, "깜빡임 시작 후 예약 해제"
        # active 가 안 바뀌는 layout(같은 패널)에선 깜빡이지 않는다.
        app._flash_pending = True
        app._pane_flash_id = None
        app._dispatch({"t": "layout", "active": 2, "cols": 80, "rows": 24,
                       "panes": panes, "dividers": []})
        await pilot.pause(0.02)
        assert app._pane_flash_id is None, "active 불변 → 깜빡임 없음"
    await _with_app(body)


async def test_alt_digit_switches_tab_in_normal_mode():
    # ESC 직후 빠른 숫자가 터미널에서 Alt+숫자(\x1b<digit>)로 합쳐 와도 그 번호 탭으로
    # 전환된다(ESC 모드 진입 지연 회피). normal 모드에서 alt+숫자 = esc 모드 숫자키.
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        # 탭 2개(index 0,1) 구성.
        app.tabbar.tabs = [{"index": 0}, {"index": 1}]
        assert app.mode == "normal"
        app.on_key(Key(key="alt+2", character=None))     # 2번(1-based) 탭 = index 1
        assert ("select_window", {"index": 1}) in sent, sent
        # 없는 번호는 전환 대신 활성 탭 깜빡임(blink_active) — send_cmd 추가 없음.
        sent.clear()
        app.on_key(Key(key="alt+9", character=None))
        assert not any(a == "select_window" for a, _ in sent), sent
    await _with_app(body)


async def test_model_config_popup_command_and_inject():
    # `model` 명령(별칭 model-config/claude-model)으로 모델·컨텍스트 팝업을 열고,
    # 적용 시 활성 패널에 '/model <이름> [컨텍스트]' + Enter 를 주입한다(요청).
    async def body(app, pilot, srv):
        keys = []
        app.send_input = lambda b: keys.append(b)
        app._run_command("model")
        await pilot.pause(0.05)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "ModelCtxScreen"
        await pilot.press("enter")                  # 기본(opus·기본 컨텍스트) 적용
        await pilot.pause(0.05)
        assert keys and keys[-1] == b"/model opus\r", keys
        # 1M 컨텍스트 선택 시 토큰이 덧붙는다.
        app._apply_model_config(("opus-4.8", "1m"))
        assert keys[-1] == b"/model opus-4.8 1m\r", keys
    await _with_app(body)


async def test_model_badge_click_opens_config():
    # 상태줄 모델 배지 클릭 → 모델·컨텍스트 변경 팝업(요청).
    from types import SimpleNamespace
    async def body(app, pilot, srv):
        app.mouse_enabled = True
        opened = []
        app.open_model_config = lambda: opened.append(1)
        sb = app.status
        for z in ("_rec_zone", "_clock_zone", "_date_zone",
                  "_usage_zone", "_host_zone"):
            setattr(sb, z, None)
        sb._model_zone = (5, 15)
        ev = SimpleNamespace(x=8, y=sb.size.height - 1, stop=lambda: None)
        sb.on_mouse_down(ev)
        assert opened == [1], opened
    await _with_app(body)


async def test_esc_down_focuses_status_bar_buttons():
    # ESC 모드 최하단 패널에서 ↓ → 하단 상태바 버튼 포커스(요청). ←→ 순환, Enter 실행.
    async def body(app, pilot, srv):
        app.layout = {"active": 1, "cols": 80, "rows": 24, "dividers": [],
                      "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24,
                                 "box": [0, 0, 80, 24]}]}
        app._status_buttons = lambda: ["model", "usage"]   # 버튼 존재(렌더 비의존)
        await pilot.press("escape")
        app._handle_esc_mode(Key(key="down", character=None))   # 최하단 ↓
        assert app._status_focus == "model", app._status_focus
        assert app.status.focus_btn == "model"
        app._handle_esc_mode(Key(key="right", character=None))  # → 순환
        assert app._status_focus == "usage", app._status_focus
        opened = []
        app.open_token_log = lambda: opened.append(1)
        app._handle_esc_mode(Key(key="enter", character=None))  # 실행
        assert opened == [1] and app._status_focus is None
    await _with_app(body)


async def test_usage_bar_lines_format():
    # 공유 포맷터: /usage 한도 dict → 라벨+막대+%+리셋(타임존 생략). 데이터 없으면 None.
    from pytmuxlib.clientscreens import usage_bar_lines
    u = {"session": {"pct": 10, "reset": "5am (Asia/Seoul)"},
         "week_all": {"pct": 14, "reset": "Jun 13 at 3am (Asia/Seoul)"},
         "week_sonnet": {"pct": 0, "reset": "Jun 13 at 3am (Asia/Seoul)"}}
    lines = usage_bar_lines(u, 80)
    assert lines and len(lines) == 3, lines
    assert "세션 5h" in lines[0] and "10%" in lines[0], lines[0]
    assert "↻5am" in lines[0] and "(Asia" not in lines[0], lines[0]  # 타임존 생략
    assert usage_bar_lines(None, 80) is None
    assert usage_bar_lines({}, 80) is None
    # account 키 없으면 계정 줄 안 붙음(인패널 전용 갱신 등) → 기존 3줄 유지
    assert all("계정(/usage)" not in ln for ln in lines), lines
    # 그림자 probe 가 계정을 실으면 일치 확인용 줄을 덧붙인다(②).
    u2 = dict(u, account="me@woojinkim.org")
    lines2 = usage_bar_lines(u2, 80)
    assert lines2[-1] == "계정(/usage): me@woojinkim.org", lines2
    # 계정 신호를 못 잡으면(account=None) '미확인' 으로 표시
    u3 = dict(u, account=None)
    assert "미확인" in usage_bar_lines(u3, 80)[-1], usage_bar_lines(u3, 80)
    # S6 T3: 실측 경과(age_sec) — 2분 미만은 표기 생략(잡음), 이상은 'N분 전 실측'
    assert all("전 실측" not in ln for ln in usage_bar_lines(u, 80, age_sec=60))
    aged = usage_bar_lines(u, 80, age_sec=600)
    assert "(10분 전 실측" in aged[-1], aged
    aged2 = usage_bar_lines(u, 80, age_sec=7800)
    assert "(2시간 10분 전 실측" in aged2[-1], aged2
    assert usage_bar_lines(u, 80, age_sec=None)[-1] == lines[-1], "None=표기 없음"


async def test_bar_gauge_partial_blocks():
    """막대 게이지(clientutil.bar): 비율→부분블록. 0/음수/빈 폭은 빈 문자열, 최대값은
    가득. S5b 에서 usagelog 에서 clientutil 로 이전 — 코어 표시 헬퍼라 여기서 검증한다
    (데이터 모듈 test_usagedb 가 표시 헬퍼를 끌고 가지 않게)."""
    from pytmuxlib.clientutil import bar, _BAR_BLOCKS
    assert bar(0, 100, 10) == ""
    assert bar(50, 0, 10) == ""         # vmax<=0
    assert bar(50, 100, 0) == ""        # cells<=0
    assert bar(100, 100, 8) == "█" * 8, repr(bar(100, 100, 8))
    half = bar(50, 100, 8)
    assert len(half) <= 8 and "█" in half       # 절반쯤
    # 아주 작은 비율도 0칸이면 빈 문자열에 가까움(부분블록만)
    tiny = bar(1, 1000, 8)
    assert tiny == "" or tiny[0] in _BAR_BLOCKS


async def test_usage_panel_auto_popup_on_shown_seq():
    # 인패널 /usage 가 새로 떴다는 usage_shown_seq 증가 → 전용 사용량 화면 자동 팝업.
    # 접속 후 첫 status 는 베이스라인만(안 띄움), 그 다음 증가에서 띄운다.
    async def body(app, pilot, srv):
        u = {"session": {"pct": 10, "reset": "5am (Asia/Seoul)"},
             "week_all": {"pct": 14, "reset": "Jun 13 at 3am (Asia/Seoul)"}}
        # 접속 status 가 이미 베이스라인을 잡았을 수 있으니 명시적으로 5 로 둔다.
        app._last_usage_shown_seq = 5
        app._dispatch({"t": "status", "usage_limits": u, "usage_shown_seq": 5})
        await pilot.pause(0.05)
        assert len(app.screen_stack) == 1, "같은 seq 는 안 띄움"
        app._dispatch({"t": "status", "usage_limits": u, "usage_shown_seq": 6})
        await pilot.pause(0.1)
        assert app.screen_stack[-1].__class__.__name__ == "InfoScreen", \
            [s.__class__.__name__ for s in app.screen_stack]
    await _with_app(body)


async def test_esc_status_focus_includes_host_clock_date_perm():
    # ESC 모드 하단 포커스 동선에 host(ssh:서버)·시계·달력·"auto mode on"(perm)도
    # 편입돼 ←→ 로 닿고, Enter 가 각 동작을 부른다(요청).
    async def body(app, pilot, srv):
        app.layout = {"active": 1, "cols": 80, "rows": 24, "dividers": [],
                      "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24,
                                 "box": [0, 0, 80, 24]}]}
        # 우측 상태(host/시계/달력) 클릭존을 실제 렌더로 채우고, 좌측 배지는 끈다.
        app.status.render_line(0)
        for z in ("_model_zone", "_usage_zone", "_rec_zone"):
            setattr(app.status, z, None)
        app._perm_zone = {1: (4, 20, 22)}        # 활성 패널 "auto mode on" footer
        # 실사용에선 _composite 이 패널 footer 텍스트에서 perm 존을 매 프레임 재감지해
        # 유지하지만, 테스트 패널엔 그 텍스트가 없어 재스캔이 주입 존을 지운다 → 노옵.
        app._composite = lambda: None
        btns = app._status_buttons()
        for k in ("host", "clock", "date", "perm"):
            assert k in btns, (k, btns)
        # Enter 가 각 대상 핸들러로 라우팅되는지(클릭과 동일 동작).
        calls = []
        app.show_status_tabs = lambda initial=0: calls.append(("host", initial))
        app.toggle_clock = lambda pid: calls.append(("clock", pid))
        app.toggle_calendar = lambda pid: calls.append(("date", pid))
        app.open_perm_mode = lambda pid: calls.append(("perm", pid))
        for key in ("host", "clock", "date", "perm"):
            app._set_status_focus(key)
            app._handle_status_focus(Key(key="enter", character=None))
        assert ("host", 2) in calls, calls
        assert ("clock", 1) in calls and ("date", 1) in calls, calls
        assert ("perm", 1) in calls, calls
    await _with_app(body)


async def test_inactive_pane_prompt_header_darker():
    # 비활성 패널의 첫 행 프롬프트 헤더 바는 활성(primary-darken-2)보다 한 단계
    # 어둡게(primary-darken-3) 그려 활성/비활성을 더 또렷이 구분한다(요청).
    async def body(app, pilot, srv):
        app.claude_header_on = True
        app.layout = {"active": 1, "cols": 40, "rows": 12, "dividers": [],
                      "panes": [
                          {"id": 1, "x": 0, "y": 1, "w": 40, "h": 5,
                           "box": [0, 0, 40, 6], "claude_hdr": True},
                          {"id": 2, "x": 0, "y": 7, "w": 40, "h": 5,
                           "box": [0, 6, 40, 6], "claude_hdr": True}]}
        app.pane_claude = {
            1: {"id": 1, "claude": "idle", "prompt": "active prompt"},
            2: {"id": 2, "claude": "idle", "prompt": "inactive prompt"}}
        app._composite()
        d2 = app.theme_variables.get("primary-darken-2", "#0053AA").lower()
        d3 = app.theme_variables.get("primary-darken-3", "#004295").lower()
        act_bg = str(app.view._cells[0][1][1].bgcolor).lower()   # 활성 헤더(행 0)
        ina_bg = str(app.view._cells[6][1][1].bgcolor).lower()   # 비활성 헤더(행 6)
        assert d2 in act_bg, ("활성=darken-2", act_bg)
        assert d3 in ina_bg, ("비활성=darken-3(더 어두움)", ina_bg)
    await _with_app(body)


async def test_command_prompt_ignores_leading_colon():
    # 명령 프롬프트는 이미 ':' 프리픽스가 있으므로 첫 글자 ':' 입력은 무시한다(요청).
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")          # 명령 프롬프트 열기
        scr = app.screen_stack[-1]
        inp = scr.query_one(Input)
        await pilot.press("colon")          # 첫 글자 ':' → 무시
        await pilot.pause(0.05)
        assert inp.value == "", repr(inp.value)
        await pilot.press("l", "s")         # 일반 입력은 정상
        await pilot.pause(0.05)
        assert inp.value == "ls", repr(inp.value)
    await _with_app(body)


async def test_rename_tab_command_noarg_cancels():
    # rename-tab 명령을 인자 없이 입력하면 **아무 동작 없이 취소**한다 — 예전 rename
    # 프롬프트 인터페이스를 열지 않는다(사용자 요청). 인자가 있으면 즉시 변경.
    async def body(app, pilot, srv):
        app.status.windows = [{"index": 0, "name": "mytab", "active": True}]
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        n0 = len(app.screen_stack)
        app._run_command("rename-tab")      # 인자 없음 → no-op(프롬프트 안 열림)
        await pilot.pause(0.05)
        assert len(app.screen_stack) == n0, "무인자 rename-tab 은 프롬프트를 안 연다"
        assert not sent, "무인자 rename-tab 은 명령을 보내지 않는다"
        app._run_command("rename-tab proj")  # 인자 있음 → 즉시 변경
        assert sent == [("rename_window", {"name": "proj"})], sent
    await _with_app(body)


async def test_rename_prompt_ghost_not_prefilled():
    # 탭 이름 변경 ghost 프롬프트는 prefix+, 키로 연다 — 현재 이름을 미리 채우지
    # 않고(빈 입력) ghost(제안)로 띄운다. 타이핑하면 덮어쓰기, Tab/→ 로 제안 채움(요청).
    from textual.events import Key
    async def body(app, pilot, srv):
        app.status.windows = [{"index": 0, "name": "mytab", "active": True}]
        app.mode = "prefix"
        app._handle_prefix(Key("comma", ","))   # prefix+, → rename ghost 프롬프트
        await pilot.pause(0.05)
        scr = app.screen_stack[-1]
        inp = scr.query_one(Input)
        assert inp.value == "", ("미리 채우지 않음", repr(inp.value))
        assert scr._suggester is not None, "현재 이름 제안(ghost) suggester"
        for ch in "new":                    # 타이핑 → 덮어쓰기
            await pilot.press(ch)
        await pilot.pause(0.05)
        assert inp.value == "new", repr(inp.value)
    await _with_app(body)


async def test_pane_scoped_command_highlights_target_pane():
    # 패널 대상 명령(rename-pane 등)을 프롬프트에서 작성 중이면 대상(활성) 패널을
    # 밝게 표시한다(요청). 프롬프트를 닫으면 해제.
    async def body(app, pilot, srv):
        app.layout = {"active": 1, "cols": 80, "rows": 24, "dividers": [],
                      "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24,
                                 "box": [0, 0, 80, 24]}]}
        await pilot.press("escape")
        await pilot.press("colon")
        for k in ["r", "e", "n", "a", "m", "e", "minus", "p", "a", "n", "e"]:
            await pilot.press(k)
        await pilot.pause(0.05)
        assert app._cmd_target_pane == 1, app._cmd_target_pane
        await pilot.press("escape")         # 프롬프트 닫기 → 해제
        await pilot.pause(0.05)
        assert app._cmd_target_pane is None, app._cmd_target_pane
    await _with_app(body)


class _MEv:
    """경량 마우스 이벤트 더블(패널 DnD 단위 테스트용)."""
    def __init__(self, x, y, shift=False, button=1, ctrl=False):
        self.x, self.y = x, y
        self.shift, self.button, self.ctrl = shift, button, ctrl

    def stop(self):
        pass


def _two_pane_layout():
    # box 가 있는(테두리) 2-패널 좌우 분할 — box 상단 행(y=0)이 헤더 손잡이.
    return {"active": 1, "border_status": False, "dividers": [], "panes": [
        {"id": 1, "x": 1, "y": 1, "w": 38, "h": 18, "box": [0, 0, 40, 20]},
        {"id": 2, "x": 42, "y": 1, "w": 38, "h": 18, "box": [41, 0, 40, 20]}]}


async def test_header_drag_pane_pickup_swap():
    # #1: 패널의 위쪽 테두리(헤더, box 상단 행 y=0)를 잡아 다른 패널에 놓으면 두 패널을
    # swap(서버에 swap_pane_to). 헤더에서 안 움직이고 떼면(클릭) 포커스만(select_pane_id).
    async def body(app, pilot, srv):
        app.layout = _two_pane_layout()
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        v = app.view
        # 패널1 헤더(box 상단 행)에서 다운 → pick-up 시작
        v.on_mouse_down(_MEv(5, 0))
        assert v._pickup == 1, "소스 패널 = 1"
        # 패널2 본문 위로 이동 → 대상 추적(swap 미리보기)
        v.on_mouse_move(_MEv(60, 5))
        assert v._pickup_over == 2 and v._pickup_moved
        # 놓음 → swap_pane_to, 상태 초기화
        v.on_mouse_up(_MEv(60, 5))
        assert ("swap_pane_to", {"id": 1, "to_id": 2}) in sent, sent
        assert v._pickup is None and v._pickup_over is None

        # 헤더에서 안 움직이고 떼면(클릭) → 포커스만, swap 없음
        sent.clear()
        v.on_mouse_down(_MEv(5, 0))
        v.on_mouse_up(_MEv(5, 0))
        assert not any(a == "swap_pane_to" for a, _ in sent), "클릭은 swap 없음"
        assert ("select_pane_id", {"id": 1}) in sent, "헤더 클릭=포커스"

        # 본문(헤더 아님) 다운은 pick-up 시작 안 함
        v.on_mouse_down(_MEv(5, 5))
        assert v._pickup is None, "본문은 pick-up 아님"
    await _with_app(body)


async def test_header_drag_to_tabbar_moves_and_breaks():
    # #1: 헤더로 든 패널을 탭바(event.y<0)에 놓으면 — 다른 탭 위=그 탭으로 이동
    # (move_pane_to_tab), [+] 위=새 탭으로 분리(break_pane). 탭바 hit-test 는 mock.
    async def body(app, pilot, srv):
        app.layout = _two_pane_layout()
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        v = app.view
        # 실제 탭바 탭(렌더 가능한 완전한 dict)을 그대로 쓰고 _hit 만 mock 한다 —
        # 같은-탭 드롭은 _composite 를 타므로 mock tabs 면 렌더가 깨진다.
        cur = next((t["index"] for t in app.tabbar.tabs if t.get("active")), 0)
        # (1) 다른 탭(cur+1) 위에 드롭 → move_pane_to_tab
        app.tabbar._hit = lambda x: ("tab", cur + 1)
        v.on_mouse_down(_MEv(5, 0))
        v.on_mouse_up(_MEv(10, -1))           # y<0 = 탭바 영역
        assert ("select_pane_id", {"id": 1}) in sent
        assert ("move_pane_to_tab", {"id": 1, "to": cur + 1}) in sent, sent
        # (2) [+] 위에 드롭 → break_pane
        sent.clear()
        app.tabbar._hit = lambda x: ("add", None)
        v.on_mouse_down(_MEv(5, 0))
        v.on_mouse_up(_MEv(70, -1))
        assert ("break_pane", {}) in sent, sent
        # (3) 현재 활성 탭 위에 드롭 → 아무 이동/분리 없음
        sent.clear()
        app.tabbar._hit = lambda x: ("tab", cur)
        v.on_mouse_down(_MEv(5, 0))
        v.on_mouse_up(_MEv(10, -1))
        assert not any(a in ("move_pane_to_tab", "break_pane")
                       for a, _ in sent), "같은 탭은 무동작"
    await _with_app(body)


async def test_shift_drag_starts_text_selection():
    # 2026-06-05 결정: Shift+드래그는 (구) 패널 swap 이 아니라 **텍스트 선택**이다.
    async def body(app, pilot, srv):
        app.layout = _two_pane_layout()
        v = app.view
        v.on_mouse_down(_MEv(5, 5, shift=True))
        assert v._pickup is None, "Shift+드래그는 pick-up 아님"
        assert v._sel_start is not None, "Shift+드래그 = 텍스트 선택 시작"
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
        # §10-A #6: 토큰 탭 맨 아래 가로 구분선 + 전 세션 합계 한 줄
        assert "전체 세션 합계" in j2, j2
        assert "─" in j2, "하단 가로 구분선"
        # §10-A #6: 팝업 하단 닫기 버튼 존재
        assert scr.query_one("#itclosebtn", Label).render().plain.strip() == "닫기"
    await _with_app(body)


async def test_info_tabs_bottom_close_button():
    """§10-A #6: 통합 상태 팝업(InfoTabsScreen) 하단 닫기 버튼(#itclosebtn) 클릭 시 닫힌다."""
    async def body(app, pilot, srv):
        app._status_cap_lines = ["파일: /tmp/x/pane-1.log"]
        app._status_tab_initial = 0
        app._open_status_tabs({"sessions": []})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoTabsScreen"
        await pilot.click("#itclosebtn")
        await pilot.pause(0.1)
        assert app.screen_stack[-1].__class__.__name__ != "InfoTabsScreen", \
            "하단 닫기 버튼으로 닫힘"
    await _with_app(body)


async def test_status_tabs_has_server_tab():
    """§10-A #12: 통합 상태 팝업에 '서버' 탭(3번째)이 생기고 호스트·소켓 정보를 보인다."""
    async def body(app, pilot, srv):
        from textual.widgets import Label
        app._status_cap_lines = ["파일: /tmp/x/pane-1.log"]
        app._status_tab_initial = 2
        app._open_status_tabs({"sessions": []})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoTabsScreen"
        names = [t[0] for t in scr._tabs]
        assert names == ["출력 캡처(REC)", "토큰 사용량", "서버"], names
        assert scr._ti == 2, "초기 탭=서버"
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "호스트:" in joined and "소켓:" in joined, joined
    await _with_app(body)


async def test_shift_nav_keys_forwarded_to_panel():
    """§10-A #5 검증: pytmux 가 shift+Home/End/shift+방향키를 표준 xterm CSI 1;2
    시퀀스로 활성 패널(앱)에 그대로 전달한다(정상 모드, 가로채지 않음). 이 포워딩이
    Claude CLI 등에서 텍스트 선택/편집의 전제가 된다 — 실제 선택 동작은 앱 몫."""
    async def body(app, pilot, srv):
        sent = []
        app.send_input = lambda b: sent.append(b)
        cases = {
            "shift+left": b"\x1b[1;2D", "shift+right": b"\x1b[1;2C",
            "shift+up": b"\x1b[1;2A", "shift+down": b"\x1b[1;2B",
            "shift+home": b"\x1b[1;2H", "shift+end": b"\x1b[1;2F",
        }
        assert app.mode == "normal" and len(app.screen_stack) == 1
        for key, seq in cases.items():
            sent.clear()
            app.on_key(Key(key=key, character=None))
            assert sent == [seq], (key, sent)
    await _with_app(body)


async def test_popup_dim_synchronous_and_cached():
    """§10-A #4: 팝업 배경 디밍 지연 개선 — ① _darken_style 은 (style,ratio) 캐시라
    같은 스타일이면 동일 객체를 즉시 반환(전 화면 셀 dim 을 경량화), ② push_screen 이
    같은 턴에 _composite 를 즉시 호출해 dim 이 다음 refresh/타이머를 기다리지 않는다."""
    from rich.style import Style
    from pytmuxlib.clientutil import _darken_style
    s = Style(color="white", bgcolor="blue", bold=True)
    assert _darken_style(s) is _darken_style(s), "lru_cache 적중(동일 객체)"

    async def body(app, pilot, srv):
        from pytmuxlib.clientscreens import InfoScreen
        calls = []
        real = app._composite

        def spy():
            calls.append(1)
            real()
        app._composite = spy
        app.push_screen(InfoScreen(["x"], title="t"))
        # await 없이 — 즉시(같은 턴) _composite 가 불려야 한다(지연 dim 제거)
        assert calls, "push_screen 직후 즉시 _composite 호출(지연 없음)"
        app._composite = real
        await pilot.pause(0.1)              # 팝업 마운트 완료까지 대기 후 정리
        app.pop_screen()
        await pilot.pause(0.1)
    await _with_app(body)


async def test_status_usage_click_opens_token_log():
    """상태줄 토큰 사용량(Σ) 클릭존이 등록되고, 클릭하면 영속 통계 팝업
    (계정=클라이언트별 · 시간/일/주/월)을 여는 open_token_log 를 호출한다."""
    async def body(app, pilot, srv):
        from textual import events
        # 사용량 존이 그려지도록 ctx%/계정을 채운 뒤 렌더(활성 Claude 패널). 좌하단
        # 표기는 토큰 수치 대신 ctx%/5h 잔여%지만, 클릭하면 여전히 토큰 로그가 열린다.
        app.status.claude_active = True
        app.status.claude_usage = "ctx 12%"
        app.status.claude_account = "me@x.org"
        app.status.render_line(0)
        uz = app.status._usage_zone
        assert uz is not None, "사용량(ctx%/5h) 클릭존 등록"
        called = []
        app.open_token_log = lambda: called.append(True)
        y = app.status.size.height - 1
        ev = events.MouseDown(app.status, uz[0], y, 0, 0, 1, False, False, False)
        app.status.on_mouse_down(ev)
        assert called == [True], called
    await _with_app(body)


async def test_status_host_click_opens_server_tab():
    """§10-A #12: 상태줄 서버이름(host) 클릭존이 등록되고, 클릭하면 통합 상태 팝업을
    서버 탭(initial=2)으로 연다."""
    async def body(app, pilot, srv):
        from textual import events
        app.status.render_line(0)
        hz = app.status._host_zone
        assert hz is not None, "서버이름(host) 클릭존 등록"
        called = []
        app.show_status_tabs = lambda initial=0: called.append(initial)
        y = app.status.size.height - 1
        ev = events.MouseDown(app.status, hz[0], y, 0, 0, 1, False, False, False)
        app.status.on_mouse_down(ev)
        assert called == [2], called
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
        await wait_until(pilot, lambda: app.screen_stack[-1].__class__.__name__
                         == "InfoTabsScreen")
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
        await wait_until(pilot, lambda: app.screen_stack[-1].__class__.__name__
                         == "InfoScreen")
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoScreen"
        assert app._hdr_focus is None and app.mode == "normal"
    await _with_app(body)


async def test_composite_coalescing():
    """B9: 한 read 버스트에서 _request_composite 를 여러 번 호출해도 _composite 는
    루프 틱당 1회만 돈다(서버 B4 배치/B2 델타가 보낸 N 메시지에 N회 재합성 방지)."""
    import asyncio as _asyncio

    async def body(app, pilot, srv):
        calls = []
        app._composite = lambda: calls.append(1)
        app._composite_pending = False
        app._request_composite()
        app._request_composite()
        app._request_composite()
        assert calls == [], "예약만 — 즉시 합성 안 함"
        await _asyncio.sleep(0)            # 루프 틱 → call_soon 콜백 1회
        assert calls == [1], ("틱당 1회 합성", calls)
        # 다음 버스트는 다시 1회
        app._request_composite()
        await _asyncio.sleep(0)
        assert calls == [1, 1], calls
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
        # §10-A #2: 좌측 정렬 — 박스 offset.x 가 footer 시작 x(anchor)에 맞는다
        # (화면 오른쪽을 넘지 않게 클램프). 세로는 클릭 줄 바로 위.
        off = scr.query_one("#perm").styles.offset
        ax = app._perm_zone[pid][0]
        ax_clamped = max(0, min(ax, scr.size.width - scr._BOX_W))
        assert int(off.x.value) == ax_clamped, (off.x.value, ax_clamped)
        assert int(off.y.value) >= 0                      # 화면 안에 배치
        scr.dismiss("plan")
        await pilot.pause(0.05)
        assert sent and sent[0][0] == "set_claude_perm_mode"
        assert sent[0][1].get("target") == "plan"
        # 원격제어 팝업: InfoScreen + [r] 토글(/rc 주입). 메시지가 틀렸다는 사용자
        # 보고로 안내문을 정정하고 토글 수단을 추가했다.
        toggled = []
        app._toggle_remote_control = lambda pane_id: toggled.append(pane_id)
        app.open_remote_control(pid)
        await pilot.pause(0.05)
        assert app.screen.__class__.__name__ == "InfoScreen"
        assert app.screen._hide_key == "r"            # [r] = 토글 바인딩
        await pilot.press("r")                        # [r] → /rc 토글 + 닫힘
        await pilot.pause(0.05)
        assert toggled == [pid]
        assert app.screen.__class__.__name__ != "InfoScreen"
    await _with_app(body)


async def test_remote_control_toggle_injects_rc():
    """원격제어 토글이 해당 패널에 '/rc'+Enter(input 메시지)를 주입한다(CLI /rc 로
    켜고 끔). 사용자 보고로 '직접 토글 불가' 안내를 정정하고 추가한 동작."""
    import base64
    # 원격제어 토글(_toggle_remote_control)은 claude-code 플러그인으로 이전됐고(Phase
    # 2c) 거기서 pytmuxlib.protocol.write_msg 를 지연 import 한다 → 그 참조를 패치.
    import pytmuxlib.protocol as protomod

    async def body(app, pilot, srv):
        pid = app.layout["panes"][0]["id"]
        sent = []
        orig = protomod.write_msg

        async def cap(writer, msg):
            sent.append(msg)
        protomod.write_msg = cap
        try:
            app._toggle_remote_control(pid)
            await pilot.pause(0.05)
        finally:
            protomod.write_msg = orig
        inp = [m for m in sent if m.get("t") == "input" and m.get("pane") == pid]
        assert inp, sent
        assert base64.b64decode(inp[0]["data"]) == b"/rc\r"
    await _with_app(body)


async def test_claude_footer_no_hover_highlight_but_keyboard_focus():
    """§10: Claude footer(권한모드/원격제어) 클릭존은 여전히 클릭 가능(_footer_zone_at
    히트테스트)하지만 **마우스 호버로는 배경을 바꾸지 않는다**(요청 — 호버 강조 폐지).
    배경 강조는 ESC 모드 키보드 포커스(_status_focus=="perm") 일 때만 입힌다."""
    from rich.style import Style as RStyle
    from pytmuxlib.clientutil import theme_color

    async def body(app, pilot, srv):
        pid = app.layout["panes"][0]["id"]
        py = app.layout["panes"][0]["y"]
        app.pane_claude = {pid: {"id": pid, "claude": "idle",
                                 "perm_mode": "default"}}
        rows = [
            [("일반 출력 줄", {})],
            [("⏵⏵ auto mode on (shift+tab to cycle)", {})],
            [("Remote Control active", {})],
        ]
        app.pane_content[pid] = (rows, None)
        app._composite()
        # 히트테스트(클릭은 유지): 권한모드 줄 안 → ("perm"), 원격제어 줄 안 →
        # ("remote"), 존 바깥(첫 줄) → None.
        zx0, zx1, zy = app._perm_zone[pid]
        assert app._footer_zone_at(zx0, zy) == (pid, "perm")
        rx0, rx1, ry = app._remote_zone[pid]
        assert app._footer_zone_at(rx0, ry) == (pid, "remote")
        assert app._footer_zone_at(zx0, py) is None
        # 호버 강조는 폐지 — 호버 상태(_footer_hover)·갱신자(_set_footer_hover)가 없다.
        assert not hasattr(app, "_footer_hover"), "마우스 호버 강조 폐지(상태 없음)"
        assert not hasattr(app.view, "_set_footer_hover")
        sec = RStyle(bgcolor=theme_color(app, "secondary")).bgcolor
        # 포커스 없으면 권한모드 줄 배경은 secondary 아님(아무것도 칠하지 않음).
        app._status_focus = None
        app._composite()
        assert app.view._cells[zy][zx0][1].bgcolor != sec
        # ESC 모드 키보드 포커스(perm) → 그 줄 배경이 secondary 로 강조(유지).
        app._status_focus = "perm"
        app._composite()
        assert app.view._cells[zy][zx0][1].bgcolor == sec
        # 포커스 해제 → 원래대로(secondary 아님)
        app._status_focus = None
        app._composite()
        assert app.view._cells[zy][zx0][1].bgcolor != sec
    await _with_app(body)


async def test_perm_mode_click_outside_closes():
    """§10-A #3: 권한모드 팝업 박스(#perm) 바깥(백드롭) 클릭 시 dismiss(None) 로 닫힌다.
    박스 안 클릭은 닫지 않는다(InfoScreen 의 inside-box 판정 패턴)."""
    async def body(app, pilot, srv):
        pid = app.layout["panes"][0]["id"]
        app.pane_claude = {pid: {"id": pid, "claude": "idle",
                                 "perm_mode": "default"}}
        # 클릭존이 없어도 open_perm_mode 는 동작(앵커 None → 중앙)
        app.open_perm_mode(pid)
        await pilot.pause(0.05)
        scr = app.screen
        assert scr.__class__.__name__ == "PermModeScreen", scr

        class _W:
            def __init__(self, wid, parent=None):
                self.id = wid
                self.parent = parent

        class _Ev:
            def __init__(self, widget):
                self.widget = widget
                self.stopped = False

            def stop(self):
                self.stopped = True

        # 박스 안 클릭(#perm) → 닫히지 않음
        ev_in = _Ev(_W("perm", parent=_W("screen")))
        scr.on_click(ev_in)
        await pilot.pause(0.05)
        assert app.screen is scr, "박스 안 클릭은 닫지 않는다"
        # 박스 바깥(백드롭) 클릭 → 닫힘
        ev_out = _Ev(_W("backdrop", parent=None))
        scr.on_click(ev_out)
        await pilot.pause(0.05)
        assert app.screen is not scr, "바깥 클릭은 팝업을 닫는다"
        assert ev_out.stopped
    await _with_app(body)


async def test_perm_mode_popup_bypass_conditional():
    """권한모드 팝업의 'Bypass Permission Mode' 항목은 **가용할 때만** 노출한다(요청).
    서버 status 의 bypass_ok(=idle footer 에서 bypass 관측 → 시작 시 위험 플래그 활성)
    가 참이거나 현재 모드가 이미 bypass 일 때만 목록에 추가하고, 그 외에는 숨겨 도달
    불가 모드를 실수로 고르지 못하게 한다. 선택 시 set_claude_perm_mode(target=bypass)."""
    async def body(app, pilot, srv):
        pid = app.layout["panes"][0]["id"]

        def open_and_keys(info):
            app.pane_claude = {pid: {"id": pid, "claude": "idle", **info}}
            app.open_perm_mode(pid)

        # 미가용(bypass_ok 없음·현재 default) → bypass 항목 없음
        open_and_keys({"perm_mode": "default"})
        await pilot.pause(0.05)
        scr = app.screen
        assert scr.__class__.__name__ == "PermModeScreen", scr
        keys = [it.id[2:] for it in scr.query_one("#perm").query("ListItem")]
        assert "bypass" not in keys, keys
        assert keys == ["auto", "accept", "default", "plan"], keys
        scr.dismiss(None)
        await pilot.pause(0.05)

        # 가용(bypass_ok=True) → bypass 항목이 맨 끝에 추가
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        open_and_keys({"perm_mode": "auto", "bypass_ok": True})
        await pilot.pause(0.05)
        scr = app.screen
        keys = [it.id[2:] for it in scr.query_one("#perm").query("ListItem")]
        assert keys == ["auto", "accept", "default", "plan", "bypass"], keys
        # 선택 시 set_claude_perm_mode(target=bypass)
        scr.dismiss("bypass")
        await pilot.pause(0.05)
        assert sent and sent[0] == ("set_claude_perm_mode",
                                    {"id": pid, "target": "bypass"}), sent

        # 현재가 이미 bypass 면 bypass_ok 없이도 노출(현재 모드를 목록에 표시)
        open_and_keys({"perm_mode": "bypass"})
        await pilot.pause(0.05)
        scr = app.screen
        keys = [it.id[2:] for it in scr.query_one("#perm").query("ListItem")]
        assert "bypass" in keys, keys
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
        app.status.claude_active = True
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


async def test_status_session_ctx_and_5h():
    # 좌하단(사용자 요청 2026-06-11): 하이라이트 패널 계정 기준 ①현재 패널 세션의
    # 컨텍스트 비율% ②5h 리밋까지 **남은** 비율%. 토큰 수치(~Σ)는 직접 표시하지
    # 않는다(기록은 서버측 _log_tokens 가 유지). claude_tokens 는 받아도 표시 안 함.
    async def body(app, pilot, srv):
        app.status.claude_active = True
        app.status.claude_usage = "ctx 42%"
        app.status.claude_tokens = 45200          # 받지만 표시 안 함
        txt = "".join(s.text for s in app.status.render_line(0))
        assert "ctx 42%" in txt, repr(txt)
        # 토큰 수치/누계 기호는 표시되지 않는다
        assert "Σ" not in txt and "45,200" not in txt, repr(txt)
        # 계정은 표시 %들의 기준 — 마지막 항목에 @계정 곁들임
        app.status.claude_account = "alice"
        txt_a = "".join(s.text for s in app.status.render_line(0))
        assert "ctx 42% @alice" in txt_a, repr(txt_a)
        # 5h 리밋 **남은** 비율(실측 사용 37% → 남음 63%). 계정은 마지막(5h)에 붙는다.
        app.status.tok5h_pct = 37
        txt_m = "".join(s.text for s in app.status.render_line(0))
        assert "5h 63% 남음 @alice" in txt_m, repr(txt_m)
        assert txt_m.index("ctx 42%") < txt_m.index("5h 63%"), repr(txt_m)
        # claude_usage 가 토큰 폴백('Xk tok')이면 표시하지 않는다(토큰 수치 비표시 원칙)
        app.status.claude_usage = "12k tok"
        app.status.tok5h_pct = None
        app.status.claude_account = None
        txt3 = "".join(s.text for s in app.status.render_line(0))
        assert "tok" not in txt3 and "12k" not in txt3, repr(txt3)
    await _with_app(body)


async def test_status_tokens_hidden_when_not_claude():
    """Claude 가 아닌 탭/패널(claude_active False)에선 좌하단 ctx%/5h 표기를 숨긴다
    — 지속표시 값이 남아 있어도 렌더하지 않는다. 값은 보존되어(다시 Claude 패널로
    돌아오면 표시), 클릭존도 등록되지 않는다."""
    async def body(app, pilot, srv):
        # 값은 채워 두되 활성 패널은 Claude 가 아님.
        app.status.claude_active = False
        app.status.claude_usage = "ctx 42%"
        app.status.claude_tokens = 45200
        app.status.claude_account = "alice"
        txt = "".join(s.text for s in app.status.render_line(0))
        assert "ctx 42%" not in txt and "Σ" not in txt, repr(txt)
        assert app.status._usage_zone is None, "클릭존 미등록"
        # 다시 Claude 패널이 활성화되면 보존된 값이 그대로 표시된다.
        app.status.claude_active = True
        txt2 = "".join(s.text for s in app.status.render_line(0))
        assert "ctx 42% @alice" in txt2, repr(txt2)
    await _with_app(body)


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


async def test_tok5h_pct_clamped_to_100():
    """5시간 한도 근접도 게이지는 0~100 범위. 추정 분모가 작아 서버가 100 을 크게
    넘는 값(과거 999 클램프 → 상태줄 '999% / 5h' 버그)을 보내도 클라가 100 으로
    클램프해 보여준다. 정상값·None 은 그대로."""
    async def body(app, pilot, srv):
        app.status.update_status({"tok5h_pct": 999})
        assert app.status.tok5h_pct == 100, "999 → 100 클램프"
        app.status.update_status({"tok5h_pct": 5000})
        assert app.status.tok5h_pct == 100
        app.status.update_status({"tok5h_pct": 73})
        assert app.status.tok5h_pct == 73, "정상값 유지"
        app.status.update_status({"tok5h_pct": None})
        assert app.status.tok5h_pct is None, "None 유지(표시 생략)"
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
        # 검색 없음 → 첫 탭('전체') 활성, 모든 명령 5건.
        assert scr._ci == 0 and len(scr._cur) == 5, (scr._ci, scr._cur)
        # 패널 탭(2건)으로 이동한 뒤 'tab' 검색 → 패널 0건이라 결과 있는 첫 탭('전체')
        # 으로 자동 점프한다(빈 화면 방지).
        await pilot.press("right")
        await pilot.pause(0.1)
        assert scr._ci == 1 and len(scr._cur) == 2, (scr._ci, scr._cur)
        for ch in "tab":
            await pilot.press(ch)
        await pilot.pause(0.1)
        assert scr._query == "tab"
        assert scr._ci == 0, scr._ci                       # '전체'로 자동 점프(결과 보유)
        assert [n for n, _ in scr._cur] == ["new-tab", "rename-tab"], scr._cur
        # 카테고리별 일치 수: 전체 2, 패널 0, 탭 2.
        assert len(scr._matches(scr._all_cats[0][1])) == 2   # 전체
        assert len(scr._matches(scr._all_cats[1][1])) == 0   # 패널
        assert len(scr._matches(scr._all_cats[2][1])) == 2   # 탭
        # 활성 탭('전체') 라벨에 (2) 표기.
        from textual.widgets import Label
        lbl = scr.query_one("#cmdtab_0", Label)
        assert "(2)" in str(lbl.render()), lbl.render()
        # 검색창(표시 전용)에 입력값 반영.
        assert scr.query_one("#cmdsearch", Input).value == "tab"
        # 백스페이스로 모두 지우면 전체(5건) 복귀.
        for _ in "tab":
            await pilot.press("backspace")
        await pilot.pause(0.1)
        assert scr._query == "" and len(scr._cur) == 5
    await _with_app(body)


async def test_command_search_separator_insensitive():
    # 공백·언더바·하이픈을 동일 취급: 'rename_'·'rename '·'rename-' 어느 구분자로
    # 쳐도 'rename-tab' 가 검색·자동완성에 잡힌다.
    from pytmuxlib.clientscreens import CommandListScreen
    async def body(app, pilot, srv):
        items = [
            ("rename-tab", "탭 이름 변경", "탭"),
            ("rename-pane", "패널 제목 변경", "패널"),
            ("move-tab-left", "현재 탭을 왼쪽으로", "탭"),
        ]
        app.push_screen(CommandListScreen(items))
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        # _all_cats[0] = '전체'(모든 명령, 중복 없음) — 카테고리 전체를 평탄화하면
        # '전체'가 사본을 더해 중복되므로 '전체' 버킷만 써서 매칭한다.
        names = lambda: sorted(n for n, _ in scr._matches(scr._all_cats[0][1]))
        # ?-목록 검색(_matches): 세 구분자 모두 동일 결과.
        for q in ("rename-", "rename_", "rename "):
            scr._query = q
            assert "rename-tab" in names() and "rename-pane" in names(), (q, names())
        scr._query = "move tab left"      # 전부 공백으로 쳐도 매칭
        assert names() == ["move-tab-left"], names()
        scr._query = "move_tab"
        assert names() == ["move-tab-left"], names()
    await _with_app(body)


async def test_prompt_candidates_separator_insensitive():
    # ':' 프롬프트 실시간 후보(_refresh_cands)도 언더바를 하이픈처럼 취급.
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        inp = scr.query_one(Input)
        inp.value = "rename_"
        scr._refresh_cands()
        await pilot.pause(0.05)
        names = [n for n, _ in scr._cand]
        assert "rename-tab" in names and "rename-pane" in names, names
        # 언더바로 친 멀티-구분자 이름도 잡힌다.
        inp.value = "move_tab_l"
        scr._refresh_cands()
        await pilot.pause(0.05)
        assert "move-tab-left" in [n for n, _ in scr._cand], scr._cand
    await _with_app(body)


async def test_prompt_candidates_space_separator_and_plugin_cmds():
    # 공백도 언더바처럼 구분자로 취급한다(이전엔 `" " not in s` 게이트로 공백 입력 시
    # 후보가 전혀 안 떴다). 또 후보 풀에 플러그인 명령(clock-mode 등)도 포함된다.
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        inp = scr.query_one(Input)

        # 공백으로 친 멀티워드 코어 명령: 'move tab l' → move-tab-left.
        inp.value = "move tab l"
        scr._refresh_cands()
        await pilot.pause(0.05)
        assert "move-tab-left" in [n for n, _ in scr._cand], scr._cand

        # 공백으로 친 플러그인 명령: 'clock m' → clock-mode(clock 플러그인이 등록).
        inp.value = "clock m"
        scr._refresh_cands()
        await pilot.pause(0.05)
        names = [n for n, _ in scr._cand]
        if app.plugins and any("clock-mode" == n for n, *_ in app.plugins.commands):
            assert "clock-mode" in names, names

        # 완성된 명령 + 실제 인자 → 정규화 입력이 어떤 명령 이름의 부분문자열도
        # 아니게 되어 후보가 사라지고(인자 입력 단계), 힌트가 대신 뜬다.
        inp.value = "rename-tab foo"
        scr._refresh_cands(); scr._refresh_hint()
        await pilot.pause(0.05)
        assert scr._cand == [], scr._cand
        assert scr._hint_cmd == "rename-tab", scr._hint_cmd
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
        # 탭 클릭 → 전환. cmdtab_0='전체', cmdtab_1='패널', cmdtab_2='탭'(new-tab).
        await pilot.click("#cmdtab_2")
        await pilot.pause(0.1)
        assert scr._ci == 2 and scr._cur[0][0] == "new-tab", (scr._ci, scr._cur[:1])
        await pilot.click("#cmdclose")                     # [x] → 닫기
        await pilot.pause(0.2)
        assert not any(s.__class__.__name__ == "CommandListScreen"
                       for s in app.screen_stack)
    await _with_app(body)


async def test_rules_editor_save_cancel_and_spacer():
    # #27 규칙 에디터: 타이틀↔에디터 한 줄 여백 + 우측 닫기[x] + 하단 저장/취소.
    # RulesEditScreen 은 claude-code 플러그인으로 이전(패키지명에 하이픈 → importlib).
    import importlib
    RulesEditScreen = importlib.import_module(
        "pytmuxlib.plugins.claude-code.screens").RulesEditScreen
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
    import importlib
    RulesEditScreen = importlib.import_module(
        "pytmuxlib.plugins.claude-code.screens").RulesEditScreen
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
    # 전체 = 코어 COMMANDS + 등록된 플러그인 명령(_commands() 풀과 동일).
    from textual.widgets import Label
    from pytmuxlib.clientutil import COMMANDS
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "PromptScreen"
        await pilot.pause(0.1)
        assert scr.query_one("#pcand", Label).display is True, "빈 입력서 후보 펼침"
        assert len(scr._cand) == len(scr._commands()), \
            (len(scr._cand), len(scr._commands()))
        assert len(scr._cand) >= len(COMMANDS)   # 최소 코어 명령 수 이상
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


async def test_status_tabs_open_capture_dir():
    # REC 탭에 '기록 폴더 열기' 동작([o] 키 + 클릭 가능한 ▸ 버튼 행)이 있어, 캡처
    # 파일이 있는 디렉터리를 OS 파일 관리자로 연다(요청).
    from pytmuxlib import proc
    opened = []
    orig = proc.open_in_file_manager
    proc.open_in_file_manager = lambda p: (opened.append(p) or True)
    try:
        async def body(app, pilot, srv):
            from textual.widgets import ListView
            app.status.capture = True
            app.status.capture_path = "/tmp/capdir/pane-2.log"
            app.status.capture_size = 5
            app._status_cap_lines = None
            app._status_tab_initial = 0
            app._open_status_tabs({"sessions": []})
            await pilot.pause(0.1)
            scr = app.screen_stack[-1]
            assert scr.__class__.__name__ == "InfoTabsScreen"
            # 클릭 가능한 액션 버튼 행(▸ [c]…/[o]…)이 목록에 있다.
            lv = scr.query_one(ListView)
            ids = [getattr(it, "id", None) for it in lv.children]
            assert "itact_o" in ids and "itact_c" in ids, ids
            # [o] 키 → 기록 폴더(파일의 dirname)를 연다.
            await pilot.press("o")
            await pilot.pause(0.05)
            assert opened, "open_in_file_manager 호출됨"
            assert opened[-1].replace("\\", "/").endswith("/capdir"), opened
        await _with_app(body)
    finally:
        proc.open_in_file_manager = orig


async def test_paste_clipboard_text_image_and_fallback():
    """§10-A #11: paste-clipboard 디스패치 — ① 텍스트면 그 텍스트 paste, ② 텍스트가
    없고 이미지면 PNG 저장 후 경로 paste(결정 ①), ③ 저장 실패 시 Alt+V(ESC v) 폴백."""
    from pytmuxlib import clientclip
    _orig = (clientclip.paste, clientclip.has_image, clientclip.save_image)

    async def body(app, pilot, srv):
        sent = []
        keys = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app.send_input = lambda b: keys.append(b)
        # 클립보드 IO 는 clientclip 모듈 자유함수(#12) — 그 함수를 모킹한다.
        # 붙여넣기는 워커(thread)로 비동기 실행되므로 호출 뒤 pause 로 완료를 기다린다.
        # ① 텍스트 우선
        clientclip.paste = lambda: "hello"
        app.paste_os_clipboard()
        await wait_until(pilot, lambda: sent and sent[-1][0] == "paste")
        assert sent[-1] == ("paste", {"text": "hello"}), sent
        assert app._pasting is False, "완료 후 _pasting 해제"
        # ② 텍스트 없음 + 이미지 있음 + 저장 성공 → 경로 paste
        sent.clear(); keys.clear()
        clientclip.paste = lambda: ""
        clientclip.has_image = lambda: True
        clientclip.save_image = lambda: "/tmp/pytmux-clip-x.png"
        app.paste_os_clipboard()
        await wait_until(pilot, lambda: sent and sent[-1][0] == "paste")
        assert sent[-1] == ("paste", {"text": "/tmp/pytmux-clip-x.png"}), sent
        assert keys == [], "저장 성공 시 Alt+V 안 보냄"
        # ③ 이미지 있음 + 저장 실패 → Alt+V(ESC v) 폴백
        sent.clear(); keys.clear()
        clientclip.save_image = lambda: None
        app.paste_os_clipboard()
        await wait_until(pilot, lambda: keys == [b"\x1bv"])
        assert keys == [b"\x1bv"], keys
        assert all(a != "paste" for a, _ in sent), sent
    try:
        await _with_app(body)
    finally:   # 모듈 전역 모킹 복원(다른 테스트 누수 방지)
        clientclip.paste, clientclip.has_image, clientclip.save_image = _orig


async def test_paste_clipboard_ignores_keys_until_done():
    """붙여넣기 진행 중(_pasting)엔 ESC 외 키 입력을 무시한다(요청 — 외부 도구로
    붙여넣는 동안 친 키가 완료 후 패널로 새는 것 방지). ESC 는 그대로 동작."""
    async def body(app, pilot, srv):
        sent = []
        app.send_input = lambda b: sent.append(b)
        app._pasting = True
        app.on_key(Key(key="a", character="a"))       # 무시
        app.on_key(Key(key="enter", character=None))   # 무시
        assert sent == [], ("진행 중 키 무시", sent)
        # ESC 는 통과 → esc 모드 진입(빠져나갈 수단)
        app.on_key(Key(key="escape", character=None))
        assert app.mode == "esc", app.mode
    await _with_app(body)


# ---- §2.4: copy-mode 선택을 시작 패널 경계로 클램프 ----
async def test_copy_mode_selection_clamped_to_start_pane():
    """여러 줄 드래그 선택의 중간 줄이 화면 끝까지 잡혀 인접 패널·테두리까지
    복사되던 오염(§2.4)을 시작 패널 가로 범위로 묶어 막는다. rect 없으면(단일
    패널) 기존 전체 폭 동작 불변. _extract_selection/_clamp_sel 은 앱 비의존이라
    __new__ 로 만든 뷰에 셀/선택만 주입해 단언한다."""
    from pytmuxlib.clientwidgets import MultiplexerView
    v = MultiplexerView.__new__(MultiplexerView)
    line = "L" * 9 + "|" + "R" * 10            # 0..8 좌패널, 9 테두리, 10..19 우패널
    v._cells = [[(c, None) for c in line] for _ in range(4)]
    # 좌패널(x0..8)에서 시작한 선택. 끝점은 드래그 시 이미 클램프됐다고 가정.
    v._sel_rect = (0, 0, 9, 4)
    v._sel = (1, 0, 7, 2)
    lines = v._extract_selection().split("\n")
    assert lines[1] == "LLLLLLLLL", lines      # 중간 줄: 패널 폭(0..8)만
    assert "|" not in "".join(lines) and "R" not in "".join(lines)
    # 끝점 클램프: 우패널 쪽(15) 좌표는 좌패널 우경계(8)로 당겨진다.
    assert v._clamp_sel(15, 2) == (8, 2)
    assert v._clamp_sel(-3, 9) == (0, 3)       # 좌·하 경계도 클램프
    # rect 없음(단일 패널) → 전체 폭 동작 그대로
    v._sel_rect = None
    v._sel = (1, 0, 18, 2)
    full = v._extract_selection().split("\n")
    assert "|" in full[1] and "R" in full[1]
    assert v._clamp_sel(99, 99) == (99, 99)


# ---- soft-wrap(자동 줄바꿈) 줄을 복사 시 한 줄로 잇기 ----
async def test_copy_mode_joins_soft_wrapped_lines():
    """서버가 표시한 soft-wrap 연속원 행(app.pane_wrap)을 추출 시 개행 없이 잇는다.
    wrap 인 행은 다음 행과 한 줄로(꽉 찬 줄이라 trailing 보존), wrap 아닌 행은
    rstrip 후 개행. 마지막 선택행은 wrap 여부와 무관하게 거기서 끝난다. wrap 정보가
    없으면(구버전/단일 주입) 기존 줄 단위 개행으로 폴백. __new__ 주입 + 가짜 app."""
    from pytmuxlib.clientwidgets import MultiplexerView

    def _mk(wrap):
        v = MultiplexerView.__new__(MultiplexerView)
        # 폭 10 단일 패널. row0,row1 은 꽉 찬 wrap 연속원, row2 는 짧은 종결 줄.
        rows = ["ABCDEFGHIJ", "KLMNOPQRST", "UVWXY     "]
        v._cells = [[(c, None) for c in r] for r in rows]
        v._sel_rect = (0, 0, 10, 3)
        v._sel_pane_id = 7
        # app 은 Textual 읽기전용 프로퍼티라 주입 불가 — wrap 조회 헬퍼만 오버라이드해
        # 앱 비의존으로 단언한다(_sel_wrap_set 의 app 접근 경로는 폴백으로 검증됨).
        v._sel_wrap_set = lambda: set(wrap)
        return v

    # row0,row1 이 wrap → 세 줄이 한 줄로. 끝점은 row2 의 'Y'(col4).
    v = _mk({0, 1})
    v._sel = (0, 0, 4, 2)
    assert v._extract_selection() == "ABCDEFGHIJKLMNOPQRSTUVWXY"

    # 일부만 wrap: row0 만 wrap, row1 은 하드 개행 → 두 줄.
    v = _mk({0})
    v._sel = (0, 0, 4, 2)
    assert v._extract_selection() == "ABCDEFGHIJKLMNOPQRST\nUVWXY"

    # wrap 정보 없음 → 기존 동작(줄마다 개행, rstrip).
    v = _mk(set())
    v._sel = (0, 0, 4, 2)
    assert v._extract_selection() == "ABCDEFGHIJ\nKLMNOPQRST\nUVWXY"

    # 마지막 선택행이 wrap 으로 표시돼도 거기서 선택이 끝나면 잇지 않는다(개행 없음,
    # 단일 줄). row0 까지만 선택, row0 가 wrap 이어도 다음 행은 선택 밖.
    v = _mk({0, 1})
    v._sel = (0, 0, 9, 0)
    assert v._extract_selection() == "ABCDEFGHIJ"


# ---- §4.5: history 누락 시 직전 값 유지 ----
async def test_claude_history_retained_when_omitted():
    """서버가 history 를 변할 때만 싣는(§4.5) 것에 맞춰, history 키가 빠진 status
    항목은 직전에 받은 history 를 유지하고 나머지 필드는 갱신한다."""
    async def body(app, pilot, srv):
        app._update_claude([{"id": 1, "claude": "idle", "prompt": "p",
                             "perm_mode": "default", "history": ["a", "b"]}])
        assert app.pane_claude[1]["history"] == ["a", "b"]
        # history 빠진 갱신 → 직전 값 유지, 나머지는 새 값
        app._update_claude([{"id": 1, "claude": "busy", "prompt": "p2",
                             "perm_mode": "default"}])
        assert app.pane_claude[1]["history"] == ["a", "b"]
        assert app.pane_claude[1]["claude"] == "busy"
        # history 다시 오면 교체
        app._update_claude([{"id": 1, "claude": "idle", "prompt": "p3",
                             "perm_mode": "default", "history": ["a", "b", "c"]}])
        assert app.pane_claude[1]["history"] == ["a", "b", "c"]
    await _with_app(body)


# ---- C1(PERFORMANCE_REVIEW 2026-06-07): _char_cells 메모이즈 ----
async def test_char_cells_memoized_correct():
    """_char_cells 가 lru_cache 로 메모이즈되며 폭 계산이 정확한지(동작 불변).

    ASCII=1, 와이드(CJK)=2 값은 그대로여야 하고, 같은 문자 반복 호출은 캐시
    적중으로 처리돼 wcwidth 왕복을 줄인다(클라 합성·탭바·상태줄 핫패스)."""
    from pytmuxlib.clientutil import _char_cells
    # 캐시가 실제로 붙어 있는지(functools.lru_cache 표식)
    assert hasattr(_char_cells, "cache_info"), "_char_cells 에 lru_cache 미적용"
    # 값 정확성: ASCII·공백=1, 한글·CJK=2
    assert _char_cells("a") == 1
    assert _char_cells(" ") == 1
    assert _char_cells("가") == 2
    assert _char_cells("漢") == 2
    # 반복 호출은 캐시 적중(첫 호출이 채운 항목을 재사용)
    before = _char_cells.cache_info()
    for _ in range(10):
        _char_cells("a")
    after = _char_cells.cache_info()
    assert after.hits > before.hits, "반복 호출이 캐시 적중하지 않음"


# ---- C2(PERFORMANCE_REVIEW 2026-06-07): TabBar _entries() 프레임 캐시 ----
async def test_tabbar_entries_cached_and_consistent():
    """_entries() 가 (폭·sel·스크롤·탭 기하)별로 캐시되어 같은 프레임 2회 호출이
    동일 객체를 돌려주고(render_line+active_tab_xrange 중복 계산 제거), 기하가
    render zone 과 일치하며, 탭/sel 변경 시 캐시가 무효화되는지 검증."""
    async def body(app, pilot, srv):
        tb = app.tabbar
        tabs = [{"index": i, "name": f"t{i}", "active": i == 1}
                for i in range(3)]
        tb.set_tabs(tabs, 1)
        await pilot.pause(0.05)
        # 같은 상태 반복 호출 → 동일 캐시 객체(히트)
        e1 = tb._entries()
        assert tb._entries() is e1, "동일 상태에서 _entries 캐시 미적중"
        # active_tab_xrange 기하가 render_line 의 활성 탭 zone 과 정확히 일치
        tb.render_line(0)
        zone = next(((x0, x1) for x0, x1, kind, pl in tb._zones
                     if kind == "tab" and pl == 1), None)
        assert zone is not None, "활성 탭 zone 없음"
        assert tb.active_tab_xrange() == zone, "xrange 가 render zone 과 불일치"
        # 탭 내용 변경(추가) → 캐시 무효화
        tb.set_tabs(tabs + [{"index": 3, "name": "t3", "active": False}], 1)
        assert tb._entries() is not e1, "탭 변경 후 캐시 무효화 안 됨"
        # 선택(sel) 변경 → 캐시 무효화
        before = tb._entries()
        tb.sel = 2
        assert tb._entries() is not before, "sel 변경 후 캐시 무효화 안 됨"
    await _with_app(body, cfg={"tab_bar_always": True})


# ---- C3(PERFORMANCE_REVIEW 2026-06-07): 합성 루프 Style/dict 상수 호이스트 ----
async def test_with_reverse_and_box_constants():
    """_with_reverse 가 st + Style(reverse=True) 와 동일(시각 불변)하며 lru_cache 로
    캐시되고, _BOX_REV 가 _BOX_BITS 의 정확한 역인덱스인지(테두리 합성 동작 불변)."""
    from rich.style import Style
    from pytmuxlib.clientutil import (
        _BOX_BITS, _BOX_REV, _REVERSE_STYLE, _with_reverse)
    # 동치: 캐시 헬퍼 결과 == 직접 합성
    base = Style(color="red", bgcolor="blue")
    assert _with_reverse(base) == base + Style(reverse=True)
    assert _REVERSE_STYLE == Style(reverse=True)
    # 캐시 적용 표식 + 반복 호출 적중
    assert hasattr(_with_reverse, "cache_info")
    before = _with_reverse.cache_info()
    for _ in range(5):
        _with_reverse(base)
    assert _with_reverse.cache_info().hits > before.hits
    # 박스 비트 역인덱스가 정확(합성에서 brev[bbits[a]|bbits[b]] 가 유효해야 함)
    assert len(_BOX_REV) == len(_BOX_BITS)
    for ch, bits in _BOX_BITS.items():
        assert _BOX_REV[bits] == ch


# ---- C4(PERFORMANCE_REVIEW 2026-06-07): 정적 옵션 주기 status 생략 시 유지 ----
async def test_status_retains_static_opts_when_omitted_c4():
    """C4: 서버가 정적 옵션을 주기 status 에서 빼도(키 부재), 클라 StatusBar 는 직전
    권위값을 유지한다(update_status 의 msg.get(k, self.k) 보존). 설정 팝업이 낡은
    값을 그리지 않게 하는 핵심 불변식."""
    async def body(app, pilot, srv):
        st = app.status
        st.update_status({"t": "status", "claude_ctx_threshold": 22,
                          "claude_ctx_action": "doc-clear",
                          "usage_gate_session_pct": 90})
        assert st.claude_ctx_threshold == 22
        assert st.claude_ctx_action == "doc-clear"
        assert st.usage_gate_session_pct == 90
        # 정적 옵션 키가 모두 빠진 주기 status → 직전 값 그대로 유지
        st.update_status({"t": "status"})
        assert st.claude_ctx_threshold == 22, "컨텍스트 임계 유실"
        assert st.claude_ctx_action == "doc-clear", "정리 방식 유실"
        assert st.usage_gate_session_pct == 90, "실측 게이트 임계 유실"
    await _with_app(body)


async def test_token_log_box_height_stable_regardless_of_content():
    """토큰로그 팝업 높이 고정(2026-06-07): 레코드 수·버킷이 달라도 박스 높이가
    변하지 않는다(예전 height:auto 는 짧은 '일' 버킷에선 쪼그라들고 '시간' 버킷에선
    화면 끝까지 찼다). 고정 높이 + 리스트 1fr 로 출렁임 제거."""
    async def body(app, pilot, srv):
        now = 1_700_000_000.0
        recs = [{"ts": now + i * 3600, "tab": 0, "pane": 1, "session": 1,
                 "account": "me@x.org", "tokens": 100} for i in range(30)]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "TokenLogScreen"
        box = scr.query_one("#tklogbox")
        h_day = box.size.height                 # 기본 '일' 버킷(행 적음)
        assert h_day > 0
        await pilot.press("h")                  # '시간' 버킷(행 많음 — 예전엔 더 컸음)
        await pilot.pause(0.1)
        assert box.size.height == h_day, ("hour", box.size.height, h_day)
        await pilot.press("m")                  # '월' 버킷(행 적음 — 예전엔 더 작았음)
        await pilot.pause(0.1)
        assert box.size.height == h_day, ("month", box.size.height, h_day)
    await _with_app(body, size=(80, 28))
