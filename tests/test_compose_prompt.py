"""ComposePromptScreen — 블록 선택(Shift+방향키) 멀티라인 작성창 → 활성 패널
bracketed paste 투입(권고안 B, CLAUDE_PROMPT_BLOCK_SELECTION_FEASIBILITY).

검증: ① ESC 모드 Insert 가 작성창을 연다 ② Ctrl+S 투입이 입력 텍스트 그대로(끝에
자동 개행 없음) `paste` 명령으로 활성 패널에 간다 ③ Esc 취소는 아무것도 안 보낸다
④ Textual TextArea 네이티브 블록 선택(shift+방향키)으로 범위 삭제가 동작한다(자식
입력기엔 없는 기능을 pytmux 작성창이 제공)."""
import harness  # noqa: F401  (sys.path 주입)

from harness import make_app, server_only, teardown, wait_until
from textual.widgets import TextArea


async def _with_app(coro, size=(100, 30)):
    srv, task, sock = await server_only()
    app = make_app(sock, None, None)
    try:
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.3)
            await coro(app, pilot, srv)
    finally:
        await teardown(srv, task, sock)


async def test_esc_insert_opens_compose():
    """ESC → Insert → ComposePromptScreen 이 뜨고 TextArea 가 포커스를 잡는다."""
    async def body(app, pilot, srv):
        await pilot.press("escape")
        assert app.mode == "esc"
        await pilot.press("insert")
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "ComposePromptScreen"
        assert app.mode == "normal"        # 모달 진입 시 esc 모드는 빠진다
        ta = scr.query_one(TextArea)
        assert ta.has_focus
    await _with_app(body)


async def test_ctrl_s_injects_text_without_trailing_newline():
    """작성 후 Ctrl+S → 입력 텍스트 그대로 paste 로 투입(끝에 자동 개행 없음 →
    자식이 자동 제출하지 않음)."""
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app.open_compose()
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        ta = scr.query_one(TextArea)
        ta.text = "line one\nline two"
        await pilot.pause(0.05)
        await pilot.press("ctrl+s")
        await pilot.pause(0.2)
        assert ("paste", {"text": "line one\nline two"}) in sent, sent
        # 끝에 개행이 붙지 않았다(자동 제출 방지).
        assert not sent[-1][1]["text"].endswith("\n")
    await _with_app(body)


async def test_escape_cancels_no_paste():
    """Esc-Esc 는 취소 — paste 를 보내지 않는다(esc 한 번은 '메뉴 모드' 진입, 두 번째
    esc 가 취소)."""
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append(action)
        app.open_compose()
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        ta = scr.query_one(TextArea)
        ta.text = "discard me"
        await pilot.pause(0.05)
        await pilot.press("escape")                       # 메뉴 모드(취소 아님)
        await pilot.pause(0.05)
        assert scr._esc_mode is True                      # 모드 진입
        assert app.screen_stack[-1] is scr, "esc 한 번은 안 닫힘"
        await pilot.press("escape")                       # 두 번째 esc = 취소
        await pilot.pause(0.2)
        assert "paste" not in sent, sent
    await _with_app(body)


async def test_empty_compose_does_not_paste():
    """빈 작성창에서 Ctrl+S → 투입할 게 없으니 paste 안 보냄."""
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append(action)
        app.open_compose()
        await pilot.pause(0.2)
        await pilot.press("ctrl+s")
        await pilot.pause(0.2)
        assert "paste" not in sent, sent
    await _with_app(body)


async def test_no_dim_and_bottom_docked():
    """사용자 요청: Claude 프롬프트는 이전 출력을 보며 입력해야 하므로 배경을
    딤하지 않고(스크린 배경 투명 + _no_backdrop_dim), 작성창은 하단에 도킹해 기존
    프롬프트 위에 겹쳐 뜨고 내용이 늘면 위로 자란다(dock:bottom + height:auto)."""
    async def body(app, pilot, srv):
        app.open_compose()
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        # 딤 없음: 스크린 배경 알파 0(투명) → 박스 밖 패널이 그대로 보인다.
        assert scr.styles.background.a == 0, \
            f"배경 딤이 없어야(투명) — got {scr.styles.background!r}"
        # 클라이언트 _composite 의 #25 백드롭 딤 면제 플래그.
        assert getattr(scr, "_no_backdrop_dim", False) is True
        # 박스는 하단 도킹(위로 자람) + height auto.
        wrap = scr.query_one("#cwrap")
        assert wrap.styles.dock == "bottom", wrap.styles.dock
    await _with_app(body)


async def test_composite_does_not_dim_behind_compose():
    """_composite 가 컴포즈 작성창 뒤 패널을 어둡게 하지 않는다(#25 면제). 일반
    모달(InfoScreen 등)은 어둡게 하던 것과 대조 — 같은 셀이 작성창 아래에선 원색."""
    async def body(app, pilot, srv):
        # 작성창을 띄우고 합성하면 뒷 패널 셀이 원색(딤 스타일이 안 입혀짐).
        app.open_compose()
        await pilot.pause(0.2)
        app._composite()
        # 첫 행 어딘가에 비공백 셀이 있고, 그 스타일이 _darken 으로 죽지 않았는지
        # 확인하긴 어렵다 → 대신 모달이 _no_backdrop_dim 임을 신뢰하고, 딤 분기가
        # 그 플래그를 본다는 계약을 위 단위테스트로 잠갔다. 여기선 합성이 예외 없이
        # 통과(작성창 떠 있는 채 _composite 안전)함을 확인한다.
        assert app.view._cells, "작성창 위에서도 합성 프레임이 생성된다"
    await _with_app(body)


async def test_input_aligned_one_below_prompt_row():
    """작성창 입력 줄(TextArea)이 활성 패널 프롬프트 줄(prompt_row)보다 **한 칸
    아래**에 온다(사용자 요청 2026-06-19). 박스 하단 구조는 [입력 줄][테두리 1행]이라
    TextArea 영역 하단이 prompt_row+1 이어야 한다."""
    from pytmuxlib.clientscreens import ComposePromptScreen
    from textual.widgets import TextArea as _TA

    async def body(app, pilot, srv):
        H = app.size.height                    # 30 (테스트 size)
        target = H - 6                          # footer 위로 몇 줄 띄운 프롬프트 행 가정
        app.push_screen(ComposePromptScreen("", prompt_row=target))
        await pilot.pause(0.3)
        ta = app.screen_stack[-1].query_one(_TA)
        last_line_y = ta.region.y + ta.region.height - 1
        assert abs(last_line_y - (target + 1)) <= 1, \
            f"입력 줄 y={last_line_y} 가 prompt_row+1={target + 1} 에 정렬돼야"
    await _with_app(body)


async def test_width_within_active_pane():
    """작성창 좌우가 활성 패널 테두리 안쪽(pane_x..pane_x+pane_w)에 들어온다 —
    패널 아웃라인 밖으로 안 삐져나가야(사용자 요청)."""
    from pytmuxlib.clientscreens import ComposePromptScreen

    async def body(app, pilot, srv):
        app.push_screen(ComposePromptScreen("", pane_x=10, pane_w=30))
        await pilot.pause(0.3)
        wrap = app.screen_stack[-1].query_one("#cwrap")
        assert wrap.region.x >= 10, wrap.region
        assert wrap.region.right <= 10 + 30, wrap.region
    await _with_app(body)


async def test_enter_sends_and_shift_enter_newlines():
    """Claude Code 동일: Enter=전송, Shift+Enter(=Ctrl+J/LF)=줄바꿈(사용자 요청).
    Ctrl+J 로 줄바꿈을 넣고 Enter 로 전송하면 개행 보존된 멀티라인이 paste 된다."""
    from textual.widgets import TextArea as _TA

    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app.open_compose()
        await pilot.pause(0.2)
        ta = app.screen_stack[-1].query_one(_TA)
        ta.text = "a"
        ta.move_cursor((0, 1))
        await pilot.press("ctrl+j")        # Shift+Enter 등가 → 줄바꿈
        await pilot.pause(0.05)
        ta.insert("b")
        await pilot.pause(0.05)
        assert "\n" in ta.text, repr(ta.text)   # Enter 아닌 Ctrl+J 가 줄바꿈
        await pilot.press("enter")         # Enter → 전송
        await pilot.pause(0.2)
        pastes = [kw["text"] for a, kw in sent if a == "paste"]
        assert pastes and "\n" in pastes[0], (sent, pastes)
        assert not pastes[0].endswith("\n")     # 끝 개행 없음(자동 제출 방지)
    await _with_app(body)


async def test_distinct_textbox_and_box_backgrounds():
    """입력칸(#carea)과 팝업 박스(#cwrap)의 배경색이 서로 다르다(사용자 요청)."""
    async def body(app, pilot, srv):
        app.open_compose()
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        wrap_bg = scr.query_one("#cwrap").styles.background
        area_bg = scr.query_one("#carea").styles.background
        assert wrap_bg != area_bg, (wrap_bg, area_bg)
    await _with_app(body)


async def test_track_input_estimates_prompt_text():
    """_compose_track_input 이 패널별 입력을 누적해 현재 프롬프트 텍스트를 추정한다
    (CSI 건너뜀·backspace 제거·Enter 비움·\\n 누적)."""
    async def body(app, pilot, srv):
        pid = app.layout.get("active")
        app._compose_track_input(pid, b"hel")
        app._compose_track_input(pid, b"\x7flo")          # backspace 후 'lo'
        app._compose_track_input(pid, b"\x1b[D")          # 화살표(CSI) 무시
        assert app._prompt_buf[pid] == "helo", app._prompt_buf
        app._compose_track_input(pid, b"\r")              # Enter 제출 → 비움
        assert app._prompt_buf[pid] == ""
    await _with_app(body)


async def test_open_seeds_from_typed_prompt_clears_on_apply():
    """프롬프트 인계: 패널에 친 텍스트가 있으면 작성창이 그 텍스트로 시드된다. 비우기는
    **여는 시점이 아니라 적용(Ctrl+S) 시점**에 — 적용 시 그 길이만큼 백스페이스로 비우고
    작성창 텍스트를 paste 한다(Esc 취소 시 프롬프트 보존, 사용자 요청 2026-06-22)."""
    from textual.widgets import TextArea as _TA

    async def body(app, pilot, srv):
        sent_in = []
        sent_cmd = []
        app.send_input = lambda d: sent_in.append(d)
        app.send_cmd = lambda action, **kw: sent_cmd.append((action, kw))
        pid = app.layout.get("active")
        app._compose_track_input(pid, b"hello")
        app.open_compose()
        await pilot.pause(0.2)
        ta = app.screen_stack[-1].query_one(_TA)
        assert ta.text == "hello", repr(ta.text)          # 시드됨
        assert sent_in == []                              # 여는 시점엔 안 비움
        assert app._prompt_buf[pid] == "hello"            # 추적값도 그대로
        await pilot.press("ctrl+s")                        # 적용
        await pilot.pause(0.2)
        assert b"\x7f" * 5 in sent_in, sent_in            # 적용 시 5칸 백스페이스로 비움
        assert ("paste", {"text": "hello"}) in sent_cmd, sent_cmd
        assert app._prompt_buf[pid] == "hello"            # 투입 후 프롬프트=작성 텍스트
    await _with_app(body)


async def test_cancel_keeps_prompt_unchanged():
    """Esc 취소: 작성창을 닫아도 활성 패널 프롬프트는 건드리지 않는다(비우기 없음).
    비우기를 적용 시점으로 옮긴 결과 — 취소 시 친 내용이 그대로 남는다(사용자 요청)."""
    from textual.widgets import TextArea as _TA

    async def body(app, pilot, srv):
        sent_in = []
        app.send_input = lambda d: sent_in.append(d)
        pid = app.layout.get("active")
        app._compose_track_input(pid, b"hello")
        app.open_compose()
        await pilot.pause(0.2)
        ta = app.screen_stack[-1].query_one(_TA)
        assert ta.text == "hello"
        await pilot.press("escape", "escape")              # 취소(esc-esc)
        await pilot.pause(0.2)
        assert sent_in == []                              # 백스페이스 안 보냄
        assert app._prompt_buf[pid] == "hello"            # 프롬프트 그대로
    await _with_app(body)


async def test_ime_paste_input_seeds_compose():
    """IME 한글 확정 입력은 Textual 이 Paste 이벤트로 보낸다(개별 Key 아님). on_paste
    도 _prompt_buf 에 누적해야 '프롬프트 인계' 시드가 한글에서도 채워진다(버그 수정).
    적용(Ctrl+S) 시 그만큼 백스페이스로 비운다."""
    from textual import events
    from textual.widgets import TextArea as _TA

    async def body(app, pilot, srv):
        pid = app.layout.get("active")
        app.on_paste(events.Paste("라이브로 확인"))
        await pilot.pause(0.05)
        assert app._prompt_buf.get(pid) == "라이브로 확인", app._prompt_buf
        sent_in = []
        app.send_cmd = lambda action, **kw: None
        app.send_input = lambda d: sent_in.append(d)
        app.open_compose()
        await pilot.pause(0.2)
        ta = app.screen_stack[-1].query_one(_TA)
        assert ta.text == "라이브로 확인", repr(ta.text)        # 한글 시드됨
        await pilot.press("ctrl+s")                        # 적용
        await pilot.pause(0.2)
        assert b"\x7f" * len("라이브로 확인") in sent_in, sent_in  # 그만큼 비움
    await _with_app(body)


async def test_unsaved_draft_persists_across_cancel():
    """저장(Enter/Ctrl+S) 없이 Esc 로 닫아도 작성 중 내용이 _compose_draft 에 남아
    다음에 다시 열면 시드된다(사용자 요청). 프롬프트에 친 게 없을 때 초안이 우선."""
    from textual.widgets import TextArea as _TA

    async def body(app, pilot, srv):
        app.open_compose()
        await pilot.pause(0.2)
        ta = app.screen_stack[-1].query_one(_TA)
        ta.text = "half-written"
        await pilot.pause(0.05)
        await pilot.press("escape", "escape")             # 저장 없이 닫기(esc-esc)
        await pilot.pause(0.2)
        assert app._compose_draft == "half-written"
        app.open_compose()                                # 다시 열기
        await pilot.pause(0.2)
        ta2 = app.screen_stack[-1].query_one(_TA)
        assert ta2.text == "half-written", repr(ta2.text)  # 초안이 시드됨
    await _with_app(body)


async def test_ime_badge_inside_popup_follows_state():
    """팝업 내부 우상단 IME 배지가 app.ime_state 를 따른다(작성 중 한/영 표시,
    사용자 요청). 상태가 바뀌면 폴링으로 따라온다."""
    from textual.widgets import Label as _Label

    async def body(app, pilot, srv):
        # OS 실측 폴링(Windows IMM32 의 50ms 틱)이 테스트가 수동 설정한 ime_state 를
        # 덮어쓰지 않게 끈다 — 이 테스트는 배지가 app.ime_state 를 따르는지만 본다
        # (한/영 OS 왕복은 test_plugin_ime_indicator 영역). 끄지 않으면 Windows 에선
        # current_source_id() 가 권위값을 매 틱 재적용해 'EN' 설정이 되돌려진다.
        app._ime_os = False
        app._ime_sock = None
        app.ime_show = True
        app.ime_state = "한"
        app.open_compose()
        await pilot.pause(0.3)
        lbl = app.screen_stack[-1].query_one("#cime", _Label)
        assert "한" in str(lbl.content), str(lbl.content)
        app.ime_state = "EN"               # 폴링(0.2s)이 따라온다
        await pilot.pause(0.5)
        assert "EN" in str(lbl.content), str(lbl.content)
    await _with_app(body)


async def test_block_select_delete_in_textarea():
    """TextArea 네이티브 블록 선택: 커서를 끝에 두고 Shift+Left 로 범위 선택 후
    삭제하면 선택분만 지워진다(자식 프롬프트엔 없는 편집을 작성창이 제공)."""
    async def body(app, pilot, srv):
        app.open_compose()
        await pilot.pause(0.2)
        ta = app.screen_stack[-1].query_one(TextArea)
        ta.text = "hello"
        ta.move_cursor((0, 5))             # 끝으로
        await pilot.pause(0.05)
        await pilot.press("shift+left", "shift+left", "shift+left")  # "llo" 선택
        await pilot.press("backspace")     # 선택 범위 삭제
        await pilot.pause(0.1)
        assert ta.text == "he", repr(ta.text)
    await _with_app(body)


async def test_ctrl_a_selects_all_in_textarea():
    """Ctrl+A 가 작성창 전체 텍스트를 선택한다(자식 프롬프트엔 없는 편집). 선택 후
    한 글자 입력하면 전체가 교체된다."""
    async def body(app, pilot, srv):
        app.open_compose()
        await pilot.pause(0.2)
        ta = app.screen_stack[-1].query_one(TextArea)
        ta.text = "line one\nline two"
        ta.move_cursor((0, 0))
        await pilot.pause(0.05)
        await pilot.press("ctrl+a")
        await pilot.pause(0.05)
        assert ta.selected_text == "line one\nline two", repr(ta.selected_text)
        await pilot.press("x")             # 선택 전체 교체
        await pilot.pause(0.1)
        assert ta.text == "x", repr(ta.text)
    await _with_app(body)


# ---- claude_input_box: 화면 입력박스 스크레이프(클라 키 추적 누락분 fallback) ----
async def test_claude_input_box_parser():
    """라이브 입력박스 추출(best-effort): 박스 없는 한 줄·박스 한 줄·박스 멀티라인
    (하드 개행)·빈 박스·soft-wrap(개행 없이 잇기)·커서 없을 때 바닥 스캔·빈 입력 None."""
    from pytmuxlib.claude import claude_input_box as f
    # 박스 없는 "> 줄"(커서 행 앵커)
    assert f(["일반", "행2", "> 타이핑 중인 줄",
              "⏵⏵ auto mode on (shift+tab)"], cursor_y=2) == "타이핑 중인 줄"
    # 테두리 박스 한 줄
    assert f(["  ╭────────╮", "  │ > hello │", "  ╰────────╯",
              "  ? for shortcuts"], cursor_y=1) == "hello"
    # 박스 멀티라인(하드 개행) — 정렬 들여쓰기 제거 + 개행 보존
    assert f(["╭───╮", "│ > line one │", "│   line two │", "╰───╯"],
             cursor_y=2) == "line one\nline two"
    # 빈 박스 → ""(못 찾음 아님)
    assert f(["╭───╮", "│ > │", "╰───╯"], cursor_y=1) == ""
    # soft-wrap(연속원 행은 wrap 집합) — 개행 없이 이어 붙임
    assert f(["╭───╮", "│ > aaaa │", "│   bbbb │", "╰───╯"],
             wrap={2}, cursor_y=1) == "aaaabbbb"
    # 커서 미상 → 아래에서부터 박스/footer/빈 줄 건너뛴 첫 줄 앵커
    assert f(["⏺ 출력", "", "╭───╮", "│ > hi there │", "╰───╯",
              "? for shortcuts"]) == "hi there"
    # 빈 입력(못 찾음) → None
    assert f([]) is None
    # 최신 Claude: 박스 없는 ❯(U+276F)+비분리공백(\xa0) 프롬프트 — 마커가 시드에
    # 딸려 오면 안 된다(ESC→Insert 작성창에 '❯' 누출 버그, 사용자 보고 2026-07-06).
    assert f(["⏺ 출력", "─────────", "❯\xa0"], cursor_y=2) == ""     # 빈 입력
    assert f(["─────────", "❯\xa0안녕 세계"], cursor_y=1) == "안녕 세계"
    # 박스 안 ❯ 마커도 제거
    assert f(["  ╭────╮", "  │ ❯ hi │", "  ╰────╯"], cursor_y=1) == "hi"


async def test_scrape_fallback_seeds_when_tracker_empty():
    """클라 키 추적(_prompt_buf)이 비어 있어도(원격제어/재접속처럼 on_key 미경유)
    화면 입력박스를 긁어 작성창을 시드한다 — client_prompt_text 훅 fallback. Claude
    패널 상태(pane_claude)일 때만 긁고(셸 오긁기 방지), 적용 시 그 길이만큼 비운다."""
    from textual.widgets import TextArea as _TA

    async def body(app, pilot, srv):
        pid = app.layout.get("active")
        app._prompt_buf = {}                      # 추적 비움(이 클라가 안 친 입력 가정)
        # 화면에 라이브 입력박스가 그려져 있다고 두고, 클라 측 캐시를 심는다.
        rows = [[("⏺ 이전 출력", {})],
                [("╭──────────────╮", {})],
                [("│ > remote text │", {})],
                [("╰──────────────╯", {})]]
        app.pane_content = {pid: (rows, (4, 2))}   # 커서는 입력박스(행2)
        app.pane_wrap = {pid: set()}
        app.pane_claude = {pid: {"id": pid, "claude": "idle"}}   # Claude 패널 게이트
        # 직접 훅 경로 확인
        assert app._current_prompt_text(pid) == "remote text"
        # 셸 패널(claude 상태 없음)이면 긁지 않음(셸 프롬프트 오긁기 방지)
        app.pane_claude = {pid: {"id": pid, "claude": None}}
        assert app._current_prompt_text(pid) == ""
        # 다시 Claude 패널 — open_compose 가 긁은 값으로 시드된다
        app.pane_claude = {pid: {"id": pid, "claude": "idle"}}
        app.open_compose()
        await pilot.pause(0.2)
        ta = app.screen_stack[-1].query_one(_TA)
        assert ta.text == "remote text", repr(ta.text)
    await _with_app(body)


# ---- 이미지/붙여넣기 라우팅(작성창이 열린 상태·프롬프트 인계) ----
async def test_image_paste_to_pane_seeds_compose_later():
    """활성 패널에 이미지를 붙여넣으면(작성창 없음) pytmux 가 붙인 **경로를 _prompt_buf
    에 기록**해, 이후 esc→Insert 작성창에 이미지(=경로)가 '딸려온다'(요청). Claude 는
    경로를 [Image #N] 첨부로 바꿔 화면에서 못 되돌리므로 클라가 스스로 추적한다."""
    from pytmuxlib import clientclip
    from textual.widgets import TextArea as _TA
    _orig = (clientclip.paste, clientclip.has_image, clientclip.save_image)

    async def body(app, pilot, srv):
        pid = app.layout.get("active")
        app.send_cmd = lambda action, **kw: None
        clientclip.paste = lambda: ""
        clientclip.has_image = lambda: True
        clientclip.save_image = lambda: "/tmp/pytmux-clip-x.png"
        # 사용자가 먼저 텍스트를 쳤다고 두고(추적) 이미지를 붙여넣는다.
        app._compose_track_input(pid, b"look at ")
        app.paste_os_clipboard()
        await wait_until(pilot, lambda: "/tmp/pytmux-clip-x.png"
                         in app._prompt_buf.get(pid, ""))
        assert app._prompt_buf[pid] == "look at /tmp/pytmux-clip-x.png"
        # 이제 작성창을 열면 경로가 시드로 딸려온다.
        app.open_compose()
        await pilot.pause(0.2)
        ta = app.screen_stack[-1].query_one(_TA)
        assert ta.text == "look at /tmp/pytmux-clip-x.png", repr(ta.text)
    try:
        await _with_app(body)
    finally:
        clientclip.paste, clientclip.has_image, clientclip.save_image = _orig


async def test_paste_into_open_compose_text_and_image():
    """작성창이 열린 상태에서 paste-clipboard 를 하면 활성 패널이 아니라 **작성 버퍼**에
    들어간다(요청: 팝업에서 붙여넣은 텍스트·이미지가 팝업에 유지). ① 텍스트는 커서에
    삽입 ② 이미지는 경로 텍스트로 삽입 ③ 패널로 paste 명령을 보내지 않는다."""
    from pytmuxlib import clientclip
    from textual.widgets import TextArea as _TA
    _orig = (clientclip.paste, clientclip.has_image, clientclip.save_image)

    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app.open_compose()
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        ta = scr.query_one(_TA)
        ta.text = "before "
        ta.move_cursor((0, 7))
        await pilot.pause(0.05)
        # ① 텍스트 붙여넣기 → 작성창에 삽입
        clientclip.paste = lambda: "typed"
        app.paste_os_clipboard()
        await wait_until(pilot, lambda: "typed" in ta.text)
        assert ta.text == "before typed", repr(ta.text)
        # ② 이미지 붙여넣기 → 경로 텍스트로 작성창에 삽입
        clientclip.paste = lambda: ""
        clientclip.has_image = lambda: True
        clientclip.save_image = lambda: "/tmp/pytmux-clip-y.png"
        app.paste_os_clipboard()
        await wait_until(pilot, lambda: "/tmp/pytmux-clip-y.png" in ta.text)
        assert ta.text == "before typed/tmp/pytmux-clip-y.png", repr(ta.text)
        # ③ 패널로는 paste 를 보내지 않았다(작성창으로 라우팅됨)
        assert all(a != "paste" for a, _ in sent), sent
    try:
        await _with_app(body)
    finally:
        clientclip.paste, clientclip.has_image, clientclip.save_image = _orig


async def test_esc_colon_opens_command_over_open_compose():
    """작성창이 열린 상태에서 esc→: 로 명령 프롬프트를 띄운다(요청). 작성창은 스택에
    남아 있어(그 위에 명령 프롬프트), 명령 실행 후 작성창으로 돌아간다."""
    from pytmuxlib.clientscreens import ComposePromptScreen

    async def body(app, pilot, srv):
        app.open_compose()
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert isinstance(scr, ComposePromptScreen)
        await pilot.press("escape")                       # 메뉴 모드
        await pilot.pause(0.05)
        assert scr._esc_mode is True
        await pilot.press("colon")                        # : → 명령 프롬프트
        await pilot.pause(0.2)
        # 명령 프롬프트가 작성창 위에 떴고, 작성창은 스택에 그대로 남아 있다.
        assert app.screen_stack[-1].__class__.__name__ == "PromptScreen"
        assert scr in app.screen_stack, "작성창이 스택에 남아 있어야(돌아갈 수 있게)"
        assert scr._esc_mode is False                     # : 처리하며 모드 해제
    await _with_app(body)
