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


async def test_remote_screen_delta_without_baseline_requests_redraw():
    """§1.7 페더레이션 회복: baseline(직전 full) 없이 screen-delta 만 오면 바뀐 행을
    둘 기준 캐시가 없어 행이 유실되던 것을, 드롭 대신 redraw 를 1회 요청해 full 을
    끌어오게 한다(원격 패널이면 request_redraw 가 업스트림으로 릴레이됨). full 수신
    시 디바운스 해제. baseline 이 있으면 종전대로 델타가 적용된다(옛 갈래 보존)."""
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append(action)
        pid = 9999
        app.pane_content.pop(pid, None)
        # (1) 새 갈래: baseline 없는 델타 → redraw 요청, 캐시 미생성(유실 대신 회복)
        app._dispatch({"t": "screen-delta", "pane": pid,
                       "rows": [[2, [("hello", {})]]], "cursor": None})
        assert sent == ["request_redraw"], "baseline 없으면 redraw 요청"
        assert pid not in app.pane_content, "기준 없는 델타는 캐시에 안 남김"
        assert pid in app._delta_no_base
        # (2) 디바운스: full 오기 전 같은 패널 델타는 재요청 안 함
        app._dispatch({"t": "screen-delta", "pane": pid,
                       "rows": [[3, [("world", {})]]], "cursor": None})
        assert sent == ["request_redraw"], "full 전 중복 요청 디바운스"
        # (3) full(screen) 수신 → baseline 회복, 디바운스 해제
        base = [[("", {})] for _ in range(5)]
        app._dispatch({"t": "screen", "pane": pid, "rows": base, "cursor": None})
        assert pid not in app._delta_no_base, "full 수신 시 디바운스 해제"
        assert app.pane_content[pid][0] == base
        # (4) 옛 갈래 보존: baseline 있으면 델타가 정상 적용·추가 요청 없음
        app._dispatch({"t": "screen-delta", "pane": pid,
                       "rows": [[1, [("X", {})]]], "cursor": None})
        assert app.pane_content[pid][0][1] == [("X", {})], "기준 위 델타 적용"
        assert sent == ["request_redraw"], "정상 적용 시 추가 요청 없음"
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


async def test_stale_dismiss_after_popup_closed_is_noop_not_crash():
    """팝업(InfoScreen)이 이미 닫힌 뒤, 큐에 남아 있던 백드롭/우클릭 Click 이 그
    화면의 on_click 을 늦게 발화해 dismiss(None)→pop_screen 을 한 번 더 부르면
    종전엔 Textual ScreenStackError 로 클라 전체가 크래시했다(restart-check 팝업
    우클릭 재현). pop_screen 가드가 기본 화면만 남은 상태의 pop 을 no-op 으로
    삼켜 크래시 대신 안전하게 무시하는지 확인한다."""
    from pytmuxlib.clientscreens import InfoScreen

    async def body(app, pilot, srv):
        app._show_restart_check_popup({
            "reexec_supported": True, "has_sessions": True,
            "serialize_ok": True, "panes": 1, "panes_with_fd": 1,
            "running_version": "p4:1", "disk_version": "p4:1"})
        await pilot.pause(0.1)
        scr = app.screen
        assert isinstance(scr, InfoScreen)
        # 정상 닫기 → 기본 화면으로 복귀.
        scr.dismiss(None)
        await pilot.pause(0.1)
        assert not isinstance(app.screen, InfoScreen)
        assert len(app.screen_stack) == 1
        # stale 중복 dismiss(이미 팝된 화면의 on_click 늦은 발화 모사) → 크래시 금지.
        # 실제 크래시 경로는 on_click→Screen.dismiss→app.pop_screen 이었다. dismiss 는
        # pop_screen() 반환값에 set_pre_await_callback 을 곧장 호출하므로, 이미 팝된
        # 화면에서 다시 dismiss(None) 해도 ScreenStackError(스택 부족) 도, None 반환
        # 으로 인한 AttributeError 도 없이 조용히 무시돼야 한다.
        scr.dismiss(None)
        await pilot.pause(0.05)
        assert len(app.screen_stack) == 1
        # 길목(pop_screen) 직접 호출도 ScreenStackError 없이 awaitable 을 돌려준다.
        assert app.pop_screen() is not None
        assert len(app.screen_stack) == 1
    await _with_app(body)


async def test_version_command_opens_popup():
    """version 명령이 서버에 요청을 보내고(_want_version), version 회신을 받으면
    클라/서버 버전·업타임 팝업(InfoScreen)을 띄운다. 버전은 `p4:` 접두사 없이 CL 번호만,
    pid 줄엔 폴백 설명 텍스트가 없고, 팝업은 중앙 정렬·업타임은 매 초 증가한다."""
    from pytmuxlib.clientscreens import InfoScreen
    from textual.widgets import Label, ListView

    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append(action)
        app._run_command("version")
        assert app._want_version is True
        assert "request_version" in sent
        # 서버 회신 모사 → 팝업
        app._show_version_popup({"version": "p4:99999", "uptime": 3661, "pid": 42})
        await pilot.pause(0.1)
        scr = app.screen
        assert isinstance(scr, InfoScreen)
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "99999" in joined, joined          # 서버 CL 번호
        assert "p4:" not in joined, joined         # 접두사 제거
        assert "폴백" not in joined and "동기화된" not in joined, joined
        assert "서버 pid 42" in joined, joined
        assert scr._center is True
        # 업타임은 tick_cb 로 매 초 갱신된다 — 강제 tick 이 줄 수를 보존하며 라벨을
        # in-place 갱신(에러 없이 동작)하는지 확인. tick_cb 는 호출마다 현재 시각으로
        # 재계산하므로 줄 형식이 유지된다.
        n0 = len(list(scr.query_one(ListView).children))
        scr._tick()
        await pilot.pause(0.05)
        n1 = len(list(scr.query_one(ListView).children))
        assert n0 == n1, (n0, n1)
        joined2 = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "서버 pid 42" in joined2 and "p4:" not in joined2, joined2
    await _with_app(body)


async def test_list_keys_shows_mouse_gestures():
    """list-keys 팝업이 사용자 바인딩뿐 아니라 구현된 1급 마우스 제스처(헤더 드래그
    pick-up→swap·탭 드래그 분할·Shift+드래그 선택 등)를 노출한다(§2.2 발견성 — 과거엔
    제스처가 명령이 아니라 ?목록·메뉴 어디에도 안 떠 사장됐다). 키워드는 ko/en 양쪽에
    공통이라 로케일과 무관하게 단언한다."""
    from pytmuxlib.clientscreens import InfoScreen
    from textual.widgets import Label

    async def body(app, pilot, srv):
        app._run_command("list-keys")
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert isinstance(scr, InfoScreen)
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "swap" in joined, joined            # 헤더 드래그 pick-up→swap
        assert "Shift" in joined, joined           # Shift+드래그 텍스트 선택
        assert ("드래그" in joined or "drag" in joined.lower()), joined
    await _with_app(body)


async def test_net_degraded_hysteresis():
    """degraded 히스테리시스(#5.8): 임계 초과 RTT 가 net_bad_n 회 연속이면 degraded
    ON, 임계 이하가 net_good_n 회 연속이면 OFF. 한두 표본 깜빡임엔 안 뒤집힌다."""
    async def body(app, pilot, srv):
        app._net_local = False                # 원격 경로(로컬은 degraded 억제)
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
        app._net_local = False                # 원격 경로(로컬은 자동 재접속 억제)
        app.net_auto_reconnect = True
        app._force_reconnecting = False
        app._net_degraded = False
        app._net_bad = app._net_good = 0
        for _ in range(app.net_recover_n):
            app._net_sample(app.net_rtt_threshold + 1.0)
        assert calls == ["auto"], calls
        assert app._net_bad == 0   # 다음 회복까지 카운터 리셋(간격 두기)
    await _with_app(body)


async def test_net_local_suppresses_degraded():
    """§10-F: 로컬(AF_UNIX·루프백 TCP) 연결은 네트워크 개념이 없으므로 임계 초과
    RTT 가 아무리 연속돼도 degraded(빨강 외곽선)로 가지 않고 자동 재접속도 안 한다.
    RTT 표본·연속 카운터는 그대로 갱신돼 진단 로그는 살아 있다(_net_last_rtt)."""
    async def body(app, pilot, srv):
        calls = []
        app.reconnect_now = lambda why="auto": calls.append(why)
        app._net_local = True                 # 로컬 연결
        app.net_auto_reconnect = True
        app._force_reconnecting = False
        app._net_degraded = False
        app._net_bad = app._net_good = 0
        thr = app.net_rtt_threshold
        # 표시 임계·회복 임계를 한참 넘는 느린 표본을 길게 줘도…
        for _ in range(app.net_recover_n + app.net_bad_n + 2):
            app._net_sample(thr + 1.0)
        assert app._net_degraded is False, "로컬은 degraded 로 안 감"
        assert calls == [], "로컬은 자동 재접속 안 함"
        assert app._net_last_rtt == thr + 1.0, "RTT 표본은 계속 기록(진단용)"
        assert app._net_bad >= app.net_bad_n, "연속 카운터도 갱신(로그에 보임)"
    await _with_app(body)


async def test_net_debug_log_env_gated():
    """§10-F 진단: PYTMUX_NET_DEBUG off 면 RTT 로그를 안 남기고, on 이면 표본마다
    한 줄(rtt_ms/thr_ms/bad/good/degraded/local)을 `<state>.netdbg.jsonl` 에 append."""
    import os
    import json
    from pytmuxlib import ipc

    async def body(app, pilot, srv):
        path = ipc.state_base(app.sock_path) + ".netdbg.jsonl"
        try:
            os.remove(path)
        except OSError:
            pass
        old = os.environ.get("PYTMUX_NET_DEBUG")
        try:
            # off: 로그 파일 생성 안 됨
            os.environ.pop("PYTMUX_NET_DEBUG", None)
            app._net_sample(0.01)
            assert not os.path.exists(path), "off 면 로그 없음"
            # on: 표본마다 한 줄
            os.environ["PYTMUX_NET_DEBUG"] = "1"
            app._net_local = True
            app._net_sample(app.net_rtt_threshold + 1.0)
            with open(path, encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            assert len(lines) == 1, lines
            rec = json.loads(lines[0])
            assert rec["local"] is True and rec["degraded"] is False
            assert rec["rtt_ms"] > rec["thr_ms"]
        finally:
            if old is None:
                os.environ.pop("PYTMUX_NET_DEBUG", None)
            else:
                os.environ["PYTMUX_NET_DEBUG"] = old
            try:
                os.remove(path)
            except OSError:
                pass
    await _with_app(body)


async def test_remote_tab_pink_styles_in_tabbar():
    """§1.7-a: remote-attach 병합 탭(remote=True)은 탭바에서 분홍으로 구분된다 —
    활성=분홍 배경, 비활성=분홍 글자. 로컬 탭은 종전 그대로(활성=primary 파랑)."""
    async def body(app, pilot, srv):
        from rich.style import Style
        from pytmuxlib.clientutil import REMOTE_PINK
        pink = Style(color=REMOTE_PINK).color
        pink_bg = Style(bgcolor=REMOTE_PINK).bgcolor
        # 비활성 원격 탭 → 분홍 글자
        app.tabbar.tabs = [
            {"index": 0, "name": "local", "active": True},
            {"index": 1, "name": "⇄h:win", "active": False, "remote": True}]
        segs = list(app.tabbar.render_line(0))
        seg_r = next(s for s in segs if "⇄h:win" in s.text)
        assert seg_r.style.color == pink, seg_r.style
        seg_l = next(s for s in segs if "local" in s.text)
        assert seg_l.style.color != pink, seg_l.style
        # 활성 원격 탭 → 분홍 배경(로컬 활성 primary 파랑 자리)
        app.tabbar.tabs = [
            {"index": 0, "name": "local", "active": False},
            {"index": 1, "name": "⇄h:win", "active": True, "remote": True}]
        app.tabbar._entries_sig = None     # 기하 캐시 무효화(탭 교체)
        segs = list(app.tabbar.render_line(0))
        seg_r = next(s for s in segs if "⇄h:win" in s.text)
        assert seg_r.style.bgcolor == pink_bg, seg_r.style
    await _with_app(body)


async def test_remote_tab_visible_after_pinned_local_tab():
    """회귀: 고정(핀) 로컬 탭 뒤에 덧붙은 비고정 원격 탭이 탭바에서 누락되면 안 된다.
    서버는 로컬 탭을 [비고정][고정]으로 정규화하지만 원격 탭은 그 뒤에 append 되어,
    옛 'first_pin 이후는 전부 고정' 가정 하에선 비고정 원격 탭이 가운데/우측 두 루프
    사이 사각지대에 빠져 그려지지 않았다(번호 키 이동은 index 기반이라 됐음)."""
    async def body(app, pilot, srv):
        # [로컬-비고정, 로컬-고정, 원격-비고정] — 스크린샷 그대로의 순서
        app.tabbar.set_tabs([
            {"index": 0, "name": "win", "active": False},
            {"index": 1, "name": "pytmux", "active": True, "pinned": True},
            {"index": 2, "name": "⇄h:win", "active": False, "remote": True}], 1)
        await pilot.pause(0.05)
        rendered = {p for kind, p, _ in app.tabbar._entries() if kind == "tab"}
        assert rendered == {0, 1, 2}, f"원격 탭(index 2) 누락: {rendered}"
        segs = list(app.tabbar.render_line(0))
        assert any("⇄h:win" in s.text for s in segs), "원격 탭이 렌더링 안 됨"
    await _with_app(body, cfg={"tab_bar_always": True})


async def test_remote_unpinned_tab_after_pinned_remote_tab_visible():
    """회귀(사용자 2026-06-23): 로컬 비고정 2개 + **원격 고정** 1개 + 그 뒤 **원격 비고정**
    1개 일 때, 고정 원격 탭 뒤의 비고정 원격 탭이 탭바에서 누락되면 안 된다(esc+숫자로만
    접근되고 마우스로 못 가던 문제). 비고정/고정을 위치 목록으로 분리하는 가운데 루프가
    고정 탭을 건너뛰고 비고정 전부(여기선 index 3 포함)를 그려야 한다."""
    async def body(app, pilot, srv):
        # [로컬-비고정, 로컬-비고정, 원격-고정, 원격-비고정] — 사용자 보고 그대로의 순서
        app.tabbar.set_tabs([
            {"index": 0, "name": "win", "active": False},
            {"index": 1, "name": "win", "active": False},
            {"index": 2, "name": "⇄host:pytmux", "active": True,
             "remote": True, "pinned": True},
            {"index": 3, "name": "⇄host:cmd", "active": False, "remote": True}], 2)
        await pilot.pause(0.05)
        rendered = {p for kind, p, _ in app.tabbar._entries() if kind == "tab"}
        assert rendered == {0, 1, 2, 3}, f"비고정 원격 탭(index 3) 누락: {rendered}"
        segs = list(app.tabbar.render_line(0))
        assert any("⇄host:cmd" in s.text for s in segs), "비고정 원격 탭 미렌더"
    await _with_app(body, cfg={"tab_bar_always": True})


async def test_remote_view_outline_pink():
    """§1.7-a: 활성 탭이 원격(remote=True)이면 패널 외곽선이 분홍(활성=REMOTE_PINK·
    비활성=REMOTE_PINK_DIM)으로, 로컬 탭으로 돌아오면 종전 색(활성=primary)으로
    그려진다. degraded(빨강)는 분홍보다 우선."""
    async def body(app, pilot, srv):
        from rich.style import Style
        from pytmuxlib.clientutil import REMOTE_PINK, REMOTE_PINK_DIM
        pink = Style(color=REMOTE_PINK).color
        pink_dim = Style(color=REMOTE_PINK_DIM).color
        app.layout = {"panes": [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 5,
                                 "box": [0, 0, 10, 5]},
                                {"id": 2, "x": 10, "y": 0, "w": 10, "h": 5,
                                 "box": [10, 0, 10, 5]}],
                      "active": 1, "cols": 20, "rows": 5, "dividers": []}
        app.pane_content = {1: ([[("x" * 10, {})] for _ in range(5)], None),
                            2: ([[("y" * 10, {})] for _ in range(5)], None)}
        app._net_degraded = False
        # 원격 탭 보기 → 활성 박스 분홍·비활성 박스 어두운 분홍
        app.status.windows = [{"index": 0, "name": "local", "active": False},
                              {"index": 1, "name": "⇄h:w", "active": True,
                               "remote": True}]
        app._composite()
        cells = app.view._cells
        assert cells[4][0][1].color == pink, cells[4][0][1]       # 활성(1) 모서리
        assert cells[4][19][1].color == pink_dim, cells[4][19][1]  # 비활성(2)
        # 로컬 탭 복귀 → 종전 색(분홍 아님)
        app.status.windows = [{"index": 0, "name": "local", "active": True},
                              {"index": 1, "name": "⇄h:w", "active": False,
                               "remote": True}]
        app._composite()
        cells = app.view._cells
        assert cells[4][0][1].color != pink, cells[4][0][1]
        # degraded 는 분홍보다 우선(빨강 경고 유지)
        app.status.windows = [{"index": 0, "name": "local", "active": False},
                              {"index": 1, "name": "⇄h:w", "active": True,
                               "remote": True}]
        app._net_degraded = True
        app._composite()
        assert app.view._cells[4][0][1].color != pink
    await _with_app(body)


async def test_remote_view_letterbox_fills_smaller_session():
    """§1.7-a 레터박싱: 원격 탭을 보는데 공유(업스트림) 세션이 내 뷰보다 작으면
    (업스트림 코-클라가 미러링 최소크기로 핀) 레이아웃 격자를 내 콘텐츠 영역 전체로
    넓히고 남는 L자 여백을 무광(panel 색) 배경으로 채운다 — 아래·우측 빈 띠가
    터미널 기본 배경으로 남아 "렌더 깨짐"처럼 보이던 것을 의도된 레터박스로 바꾼다.
    로컬 탭으로 돌아오면 발동하지 않아(격자=레이아웃 크기) 종전 동작 불변."""
    async def body(app, pilot, srv):
        from rich.style import Style
        from pytmuxlib.clientutil import theme_color
        panel = Style(bgcolor=theme_color(app, "panel")).bgcolor
        # 업스트림 세션 20×5(테두리 박스 포함), 내 뷰 100×28(=30-탭바1-상태1).
        app.layout = {"panes": [{"id": 1, "x": 1, "y": 1, "w": 18, "h": 3,
                                 "box": [0, 0, 20, 5]}],
                      "active": 1, "cols": 20, "rows": 5, "dividers": []}
        app.pane_content = {1: ([[("x" * 18, {})] for _ in range(3)], None)}
        # 원격 탭 활성 → 레터박스 발동
        app.status.windows = [{"index": 0, "name": "local", "active": False},
                              {"index": 1, "name": "⇄h:w", "active": True,
                               "remote": True}]
        app._composite()
        cells = app.view._cells
        vw, vh = app._content_size()
        # ① 격자가 내 콘텐츠 영역 전체로 확장(아래 빈 띠 제거)
        assert len(cells) == vh, (len(cells), vh)
        assert len(cells[0]) == vw, (len(cells[0]), vw)
        # ② 하단 띠(y>=5)와 우측 띠(x>=20, y<5)가 무광 panel 배경
        assert cells[vh - 1][0][1].bgcolor == panel, cells[vh - 1][0][1]
        assert cells[2][vw - 1][1].bgcolor == panel, cells[2][vw - 1][1]
        # ③ 라이브 영역(업스트림 내용)은 보존 — 무광 아님
        assert cells[2][2][0] == "x", cells[2][2]
        assert cells[2][2][1].bgcolor != panel, cells[2][2][1]
        # ④ 로컬 탭 복귀 → 레터박스 미발동(격자=레이아웃 5행)
        app.status.windows = [{"index": 0, "name": "local", "active": True},
                              {"index": 1, "name": "⇄h:w", "active": False,
                               "remote": True}]
        app._composite()
        assert len(app.view._cells) == 5, len(app.view._cells)
    await _with_app(body)


async def test_set_ambiguous_width_runtime_toggle():
    """:set ambiguous-width narrow|wide|auto 런타임 전환(모바일에서 단말·앱 모호폭
    셈법이 다를 때 사용자가 직접 맞춘다). _apply_ambiguous_wide 가 클라 폭 모델
    (cellwidth)을 바꾸고 서버에 set_ambig 를 통지해 서버 pyte 격자도 맞춘 뒤 화면을
    다시 그린다. 같은 프로세스라 cellwidth 전역을 공유하므로 모드값·멱등·크래시
    부재로 검증한다(set_ambig 왕복+_send_full 재수신이 예외 없이 처리)."""
    async def body(app, pilot, srv):
        from pytmuxlib import cellwidth
        try:
            cellwidth.set_ambiguous_wide(False)
            # narrow→wide 전환
            app._apply_ambiguous_wide(True)
            assert cellwidth.ambiguous_wide() is True
            await pilot.pause(0.2)              # 서버 set_ambig 처리 + _send_full 왕복
            assert app.view._cells, "전환 후에도 화면 합성 유지"
            # wide→narrow 복귀(겹침 완화 경로)
            app._apply_ambiguous_wide(False)
            assert cellwidth.ambiguous_wide() is False
            await pilot.pause(0.2)
            # 같은 모드 재적용은 no-op(크래시·중복 통지 없음)
            app._apply_ambiguous_wide(False)
            assert cellwidth.ambiguous_wide() is False
        finally:
            cellwidth.set_ambiguous_wide(False)
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


async def test_tab_index_command_negative_and_out_of_range():
    """select/move/swap-tab 의 인덱스가 양수면 1-based, 음수면 끝에서(-1=마지막).
    범위를 벗어난 인덱스는 조용히 무시하지 않고 상태줄에 알린다(§2.8 — 과거엔
    음수·범위밖이 _first_int 에서 None 으로 떨어져 move-tab -2 등이 먹통이었다)."""
    async def body(app, pilot, srv):
        app.status.windows = [{"index": i, "name": f"w{i}", "active": (i == 0)}
                              for i in range(4)]            # 탭 4개(0..3)
        app.tabbar.set_tabs(app.status.windows, 0)
        sent = []
        msgs = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app.display_message = lambda m, *a, **k: msgs.append(m)
        app._run_command("select-tab 2")     # 양수 1-based → 0-based 1
        app._run_command("move-tab -1")       # 음수 끝에서 → 마지막(3)
        app._run_command("swap-tab -t -2")    # -t 음수 → 뒤에서 둘째(2)
        app._run_command("select-tab -t 4")   # 1-based 4 → 마지막(3)
        assert ("select_window", {"index": 1}) in sent, sent
        assert ("move_window", {"index": 3}) in sent, sent
        assert ("swap_window", {"index": 2}) in sent, sent
        assert ("select_window", {"index": 3}) in sent, sent
        # 범위밖: 인덱스가 주어졌으나 무효 → send 안 하고 상태줄 알림
        sent.clear(); msgs.clear()
        app._run_command("move-tab -99")      # 끝에서 99번째 → 음수 → 범위밖
        app._run_command("select-tab 9")      # 1-based 9 → 탭 4개 초과
        app._run_command("move-tab 0")        # 0 은 무효
        assert not sent, sent                 # 어떤 윈도우 명령도 안 보냄
        assert len(msgs) == 3, msgs           # 셋 다 알림
        # 인덱스 자체가 없으면 조용히 무동작(알림도 없음)
        sent.clear(); msgs.clear()
        app._run_command("move-tab")
        assert not sent and not msgs, (sent, msgs)
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


async def test_signed_int_helpers():
    """_signed_int·_first_signed_int 는 음수 부호를 값으로 본다(§2.8 탭 인덱스의
    '끝에서 N번째'용). _first_int 와 달리 음수를 건너뛰지 않는다."""
    async def body(app, pilot, srv):
        from pytmuxlib.clientutil import _signed_int, _first_signed_int
        assert _signed_int("3") == 3
        assert _signed_int("-2") == -2
        assert _signed_int("-t") is None and _signed_int("foo") is None
        assert _signed_int(None) is None and _signed_int("") is None
        assert _first_signed_int(["-t", "2"]) == 2     # -t 건너뛰고 2
        assert _first_signed_int(["-2"]) == -2          # 음수도 값
        assert _first_signed_int(["move", "-1"]) == -1
        assert _first_signed_int(["foo", "bar"]) is None
        # 견고성: 비ASCII 유니코드 "숫자"(²·③ — isdigit()=True 지만 int() 가 깨짐)는
        # 정수로 보지 않고 None(크래시 대신 무동작). _first_int 도 같은 가드.
        from pytmuxlib.clientutil import _first_int
        assert _signed_int("²") is None and _signed_int("-③") is None
        assert _first_int(["③"]) is None and _first_signed_int(["²"]) is None
        assert _first_int(["²", "5"]) == 5              # 비ASCII 건너뛰고 5
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


async def test_command_prompt_toggle_left_right_selects():
    # 토글 인자(on/off)는 좌우 방향키로도 선택(요청). 화면 on_key 가 App 바인딩 검사보다
    # 먼저 도므로 event.stop() 이 Input 의 cursor_left/right 를 막아, 커서가 안 움직이고
    # 선택지만 순환한다. ↑↓ 와 함께 ←→ 가 토글 주 경로다.
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        inp = scr.query_one(Input)
        inp.value = "monitor-activity"
        inp.cursor_position = len(inp.value)
        scr._refresh_cands(); scr._refresh_hint()
        assert scr._choices, "토글 선택지"
        cur0 = inp.cursor_position
        base = scr._choice_sel
        await pilot.press("right")
        assert scr._choice_sel == (base + 1) % len(scr._choices), "→ 다음 선택지"
        assert inp.cursor_position == cur0, "→ 가 커서를 옮기지 않음(선택지 이동만)"
        await pilot.press("left")
        assert scr._choice_sel == base, "← 이전 선택지"
        assert inp.cursor_position == cur0, "← 가 커서를 옮기지 않음"
    await _with_app(body)


async def test_command_prompt_toggle_cursor_on_current_state():
    # 토글 선택지 팝업은 첫 항목이 아니라 **현재 설정값**에 커서를 올린다(요청). auto-retry
    # 의 현재 상태(status.claude_auto_retry)에 따라 초기 _choice_sel 이 '켜기'(on)/'끄기'
    # (off)를 가리킨다 — command_option_current(claude-code) 경로.
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        inp = scr.query_one(Input)

        def idx_for_value(v):
            return next(i for i, (_d, val) in enumerate(scr._choices) if val == v)

        # 현재 ON → 커서가 'on' 선택지(첫 항목 '토글'이 아님)
        app.status.claude_auto_retry = True
        inp.value = "auto-retry"
        scr._refresh_cands(); scr._refresh_hint()
        assert scr._choices, "토글 선택지"
        assert scr._choice_sel == idx_for_value("on"), (scr._choice_sel, scr._choices)
        assert scr._choice_sel != 0, "첫 항목(토글)이 아니라 현재값에 커서"

        # 현재 OFF → 커서가 'off' 선택지
        app.status.claude_auto_retry = False
        inp.value = "auto-retry"
        scr._refresh_cands(); scr._refresh_hint()
        assert scr._choice_sel == idx_for_value("off"), (scr._choice_sel, scr._choices)
    await _with_app(body)


async def test_command_prompt_arg_history_recommend_and_complete():
    # remote-attach 등 자유 텍스트 인자 명령은 이전 입력 인자를 기억해 ① 추천(후보 목록)·
    # ② 자동완성(ghost) 한다(요청). 같은 버킷(remote-*)은 이력을 공유한다.
    async def body(app, pilot, srv):
        app._arghist = {}
        app._record_arg("remote-attach office1")
        app._record_arg("remote-attach lab@host2")
        # 버킷 공유: remote-new-tab 도 같은 호스트 이력을 본다.
        assert app._arghist_list("remote-new-tab") == ["lab@host2", "office1"]
        # 중복은 최근으로 끌어올림(앞으로).
        app._record_arg("remote-attach office1")
        assert app._arghist_list("remote-attach")[0] == "office1"

        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        inp = scr.query_one(Input)

        # ① 인자 자리(명령 + 공백)에서 모든 최근 인자를 추천(arg 모드).
        inp.value = "remote-attach "
        inp.cursor_position = len(inp.value)
        scr._refresh_cands(); scr._refresh_hint()
        assert scr._arg_mode and scr._cand_shown
        assert [n for n, _ in scr._cand] == ["office1", "lab@host2"]

        # ② 부분 인자는 prefix 로 거른다.
        inp.value = "remote-attach off"
        inp.cursor_position = len(inp.value)
        scr._refresh_cands()
        assert [n for n, _ in scr._cand] == ["office1"]

        # ③ Tab(=_accept_cand) 은 'cmd arg' 로 채운다(뒤 공백 없음).
        scr._sel = 0
        scr._accept_cand()
        assert inp.value == "remote-attach office1"

        # ④ 인자 미입력 + 추천 강조 → Enter 가 그 추천으로 실행.
        inp.value = "remote-attach "
        inp.cursor_position = len(inp.value)
        scr._refresh_cands()
        captured = []
        scr.dismiss = lambda v=None: captured.append(v)
        scr._sel = 1
        scr.on_input_submitted(type("E", (), {"value": inp.value})())
        assert captured == ["remote-attach lab@host2"], captured

        # ⑤ ghost 자동완성: 이력 줄이 suggester 풀에 들어가 prefix 로 제안된다.
        sugg = app._arghist_completions()
        assert "remote-attach office1" in sugg
        s = await scr._suggester.get_suggestion("remote-attach o")
        assert s == "remote-attach office1", s
    await _with_app(body)


async def test_command_prompt_executes_exact_name_with_substring_sibling():
    # 회귀: 명령 이름이 다른 명령의 부분문자열이면(예: 'help' ⊂ 'mouse-help') 후보가
    # 영영 안 사라져(_refresh_cands 의 'len==1 동일' 가드 미발동), Enter 가 매번 후보만
    # 다시 채우고 실행으로 못 넘어가던 버그(:help 무반응). 입력이 강조 후보와 정확히
    # 일치하면 Enter 가 바로 실행(dismiss)해야 한다.
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "PromptScreen"
        inp = scr.query_one(Input)
        inp.value = "help"
        scr._refresh_cands()
        names = [n for n, *_ in scr._cand]
        # 버그 재현 전제: 'help' 와 'mouse-help' 가 둘 다 후보라 목록이 안 사라진다.
        assert "help" in names and "mouse-help" in names, names
        assert scr._cand_shown, "후보가 떠 있는 상태"
        assert scr._cand[scr._sel][0] == "help", names   # 강조=정확 일치 'help'
        captured = []
        scr.dismiss = lambda v=None: captured.append(v)
        scr.on_input_submitted(type("E", (), {"value": inp.value})())
        assert captured == ["help"], (captured, names)    # 무한 채움이 아니라 실행
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
        # §10: 박스 높이를 최대 카테고리 항목 수(≤_CMDS_MAX_ROWS) 기준으로 고정 →
        # ←→ 전환 시 박스 높이 불변(출렁임 방지). ListView 는 1fr 로 박스를 채워
        # 가시 영역과 스크롤 뷰포트를 일치시킨다(낮은 터미널서 커서가 화면 밖으로
        # 빠지던 버그 수정 — 고정 행수 강제 폐기).
        assert str(scr.query_one("#cmds").styles.height) == "1fr", \
            scr.query_one("#cmds").styles.height
        box_h = box.region.height
        # 박스는 화면 안에 들어맞아야 한다(아래쪽 여백 포함, 잘림 없음).
        assert box.region.bottom <= app.size.height, (box.region, app.size)
        # 박스 높이 상한 = _CMDS_MAX_ROWS 목록행 + 외곽선/머리/검색창 오버헤드
        assert box_h <= scr._CMDS_MAX_ROWS + scr._BOX_OVERHEAD, box_h
        # ←→ 카테고리 전환에도 박스 높이는 불변
        await pilot.press("right")
        await pilot.pause(0.1)
        assert box.region.height == box_h, (box.region.height, box_h)
        await pilot.press("left")
        await pilot.pause(0.1)
        # §10 #1: Claude 관련 명령이 독립 "Claude" 카테고리 탭으로 분리됨(일반
        # 모니터링은 "모니터" 로 분리 — 이전엔 "모니터/Claude" 혼합 카테고리였다).
        catmap = dict(scr._all_cats)
        assert "Claude" in catmap and "모니터" in catmap, list(catmap)
        claude_names = [n for n, _ in catmap["Claude"]]
        for nm in ("claude-auto-mode", "auto-retry", "auto-resume",
                   "token-log", "prompt-clear"):
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


async def test_command_candidate_word_prefix_ranks_first():
    """관련도 정렬(요청): 'esc' 는 단어 접두 일치(send-escape 의 'escape')를 중간
    부분일치(coalesce-repaints 의 'coalesce')보다 위에 둔다 — send-escape 가 맨 위."""
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        for ch in "esc":
            await pilot.press(ch)
        await pilot.pause(0.1)
        names = [n for n, _ in scr._cand]
        assert "send-escape" in names and "coalesce-repaints" in names, names
        assert names[0] == "send-escape", f"send-escape 가 맨 위여야: {names}"
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


async def test_esc_mode_ignores_windows_modifier_artifact():
    # Windows 콘솔은 Shift/Ctrl/Alt 단독 키다운에도 KEY_EVENT(UnicodeChar 0)를 주고
    # Textual 이 이를 ctrl+@(character "\x00")로 만든다. `:`·`?` 처럼 Shift 가 필요한
    # esc 모드 명령에서 이 아티팩트가 진짜 글자보다 먼저 도착해 catch-all else 가
    # esc 모드를 풀어버리던 버그(요청). 수정자 단독 이벤트는 모드를 유지해야 한다.
    async def body(app, pilot, srv):
        # 아티팩트를 진짜 버그 경로(on_key 디스패치)로 주입 — on_key 가 mode=="esc"
        # 분기로 _handle_esc_mode 에 넘기고, 거기서 모드가 풀리지 않아야 한다.
        await pilot.press("escape")
        assert app.mode == "esc"
        app.on_key(Key(key="ctrl+@", character="\x00"))   # Shift 키다운 아티팩트
        assert app.mode == "esc", "수정자 단독 아티팩트는 esc 모드를 풀지 않는다"
        await pilot.press("colon")                        # 뒤이어 진짜 ':'
        assert app.mode == "normal"
        assert app.screen_stack[-1].__class__.__name__ == "PromptScreen"
    await _with_app(body)


async def test_esc_mode_question_after_modifier_artifact_opens_help():
    # ? 도 Shift+/ 라 같은 아티팩트가 선행한다 — esc 모드가 풀리지 않고 help 가 떠야.
    async def body(app, pilot, srv):
        await pilot.press("escape")
        assert app.mode == "esc"
        app.on_key(Key(key="ctrl+@", character="\x00"))   # Shift 키다운 아티팩트
        assert app.mode == "esc"
        await pilot.press("question_mark")                # 뒤이어 진짜 '?'
        assert app.mode == "normal"
        assert app.screen_stack[-1].__class__.__name__ == "CommandListScreen"
    await _with_app(body)


async def test_prefix_ignores_windows_modifier_artifact():
    # 같은 아티팩트가 prefix 를 소비해 `prefix %`(Shift 조합) 등이 셸로 새던 문제.
    async def body(app, pilot, srv):
        splits = []
        app.send_cmd = lambda c, **k: splits.append((c, k))
        await pilot.press("ctrl+b")
        assert app.mode == "prefix"
        app.on_key(Key(key="ctrl+@", character="\x00"))   # Shift 키다운 아티팩트
        assert app.mode == "prefix", "수정자 단독 아티팩트는 prefix 를 소비하지 않는다"
        app.on_key(Key(key="percent_sign", character="%"))  # 뒤이어 진짜 '%'
        assert app.mode == "normal"
        assert ("split", {"orient": "lr"}) in splits
    await _with_app(body)


async def test_scroll_mode_ignores_windows_modifier_artifact():
    # 스크롤(copy)모드의 Shift 키(`G`=맨끝·`N`=역방향·`/`=검색)마다 Windows 의
    # 수정자 단독 아티팩트(ctrl+@, character "\x00")가 선행한다. 아티팩트는 무시되어
    # 모드를 유지하고 스크롤도 일으키지 않으며, 뒤이은 진짜 Shift 키는 동작해야 한다.
    async def body(app, pilot, srv):
        calls = []
        app.send_scroll = lambda aid, **k: calls.append(k)
        await pilot.press("ctrl+b")
        await pilot.press("left_square_bracket")          # prefix [ → 스크롤 모드
        assert app.mode == "scroll"
        app.on_key(Key(key="ctrl+@", character="\x00"))   # Shift 키다운 아티팩트
        assert app.mode == "scroll", "아티팩트는 스크롤 모드를 풀지 않는다"
        assert calls == [], "아티팩트는 스크롤을 일으키지 않는다"
        app.on_key(Key(key="G", character="G"))           # 뒤이어 진짜 Shift+g(맨끝)
        assert {"bottom": True} in calls
    await _with_app(body)


async def test_esc_mode_entry_via_backtick_and_double_tap_literal():
    # ` 도 ESC 처럼 esc(명령) 모드에 진입한다(요청 2026-06-12). esc 모드에서 ` 를 한 번
    # 더 누르면(double-tap) 패널에 리터럴 backtick 을 전달하고 모드를 빠진다(tmux prefix
    # 관례) — ` 진입키 때문에 패널에 백틱을 못 넣는 일이 없게 한다.
    async def body(app, pilot, srv):
        sent = []
        app.send_input = lambda d: sent.append(d)
        await pilot.press("grave_accent")            # ` → esc 모드 진입
        assert app.mode == "esc" and app.status.cmd_mode is True
        assert sent == [], "진입 시엔 패널로 아무것도 보내지 않는다"
        await pilot.press("grave_accent")            # ` 한 번 더 → 리터럴 ` + 종료
        assert app.mode == "normal", "double-tap 은 모드를 빠진다"
        assert sent == [b"`"], sent
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


async def test_esc_e_sends_escape_to_pane():
    # ESC+e = 활성 패널에 ESC(\x1b) 전달 후 모드 종료 — Shift+ESC 로 ESC 를 못 보내는
    # 터미널(WT 등)용 2단 키보드 동선(요청 2026-06-18). 대조: 단독 ESC 두 번은 여전히
    # 패널로 ESC 를 보내지 않는다(모드만 종료, 56632 불변).
    async def body(app, pilot, srv):
        sent = []
        app.send_input = lambda d: sent.append(d)
        app._last_esc_ts = 0.0
        await pilot.press("escape")
        assert app.mode == "esc"
        await pilot.press("e")
        assert sent == [b"\x1b"], sent
        assert app.mode == "normal", "esc e 후 모드 종료"
        # 대조: ESC 두 번은 패널로 ESC 안 보냄(모드만 진입→종료)
        sent.clear()
        app._last_esc_ts = 0.0
        await pilot.press("escape")
        assert app.mode == "esc"
        app._last_esc_ts = 0.0
        await pilot.press("escape")
        assert app.mode == "normal" and sent == [], \
            "단독 ESC 두 번은 전달 없음(Shift+ESC/esc e/send-escape 만 전달)"
    await _with_app(body)


async def test_settings_key_tab_lists_esc_prefix_and_user_bindings():
    # §설정 팝업 '키' 탭(요청 2026-06-18): ESC 모드·prefix 모드 내장 키 + 사용자 바인딩을
    # 읽기 전용으로 나열. prefix 서브헤더에 현재 prefix 키 표기. 모든 행 렌더 무오류.
    from pytmuxlib.clientscreens import SettingsScreen
    s = SettingsScreen(prefix_key="ctrl+a",
                       user_bindings={"r": "redraw"},
                       root_bindings={"f5": "refresh"})
    assert "키" in s._cats, s._cats
    kr = [d for d, _ in s._flat if d["type"] == "keyref"]
    assert any(d.get("kid") == "e_e" for d in kr), "ESC 모드 e 행 없음"
    assert any(d.get("kid") == "p_pct" for d in kr), "prefix % 행 없음"
    assert any(d.get("k") == "r" and d.get("d") == "redraw" for d in kr), \
        "사용자 prefix 바인딩 행 없음"
    assert any(d.get("k") == "f5" and d.get("d") == "refresh" for d in kr), \
        "사용자 root 바인딩 행 없음"
    subs = [d.get("sub", "") for d in kr if "sub" in d]
    assert any("Ctrl-a" in sx for sx in subs), subs
    for i in range(len(s._flat)):
        assert isinstance(s._row_text(i), str)


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


async def test_token_usage_alias_opens_token_log():
    # token-usage 는 token-log 로 통합(2026-06-12) — 명령은 별칭으로만 남아 같은
    # 영속 토큰 팝업을 연다(트리 팝업·통합 상태 팝업의 토큰 탭은 제거됨).
    async def body(app, pilot, srv):
        called = []
        app.open_token_log = lambda: called.append(True)
        app._run_command("token-usage")
        app._run_command("tokens")
        app._run_command("token-log")
        assert called == [True, True, True], called
        # 트리 팝업 클로저는 더 이상 설치되지 않는다.
        assert getattr(app, "_open_usage_tree", None) is None
        assert getattr(app, "open_claude_usage_tree", None) is None
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
    # #7(2026-06-12 재설계): token_log 응답 → TokenLogScreen. 표는 한 번에 한
    # 차원만 — 기간 뷰(기본)는 시간 버킷 행만. [m] 월 버킷 전환이 라운드트립 없이
    # 동작. (계정 차원은 제거 — 토큰 사용량은 머신-로컬 기준, 2026-06-19.)
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
        # 기간 뷰(기본)엔 계정 행이 없다 — 계정 차원 자체가 제거됐다.
        assert scr._view == "time"
        assert "team@y.org" not in joined and "me@x.org" not in joined, joined
        # 계정 탭은 없다(제거).
        assert not scr.query("#tab_acct")
        # 닫기 버튼 [x] 가 글자까지 보여야 함(markup=False — 예전엔 마크업으로
        # 해석돼 배경색만 남고 X 가 사라졌다).
        close = scr.query_one("#tklogclose", Label)
        assert "[x]" in close.render().plain, \
            f"닫기 버튼에 [x] 글자가 보여야 함: {close.render().plain!r}"
        # 정렬 토글([o])은 제거됐다(2026-06-22, 기간 뷰는 항상 시간순 계층 트리).
        # 옛 입도 서브탭/정렬 단축키는 계층 트리로 대체돼 h/d/w/m/o 는 예약 no-op.
        for k in ("h", "d", "w", "m", "o"):       # 예약 no-op: 닫히지 않는다
            await pilot.press(k)
            await pilot.pause(0.02)
            assert app.screen_stack[-1] is scr, f"{k} 키는 닫지 않음"
        # M19: /usage 결과를 status 훅으로 밀어넣으면 상단 1줄 요약(5h 7%)이 보이고,
        # [한도] 뷰로 들어가면 막대 상세가 표 자리에 보인다(2026-06-14 한도 전용 뷰).
        scr.update_usage({"session": {"pct": 7, "reset": "5am"}})
        await pilot.pause(0.1)
        joined3 = _tok_text(scr)
        assert "5h 7%" in joined3, joined3            # 상단 1줄 한도 요약
        assert "세션 5h" not in joined3, joined3        # 막대 상세는 기본 화면엔 없음
        await pilot.press("l")                          # [한도] 뷰 진입
        await pilot.pause(0.1)
        assert scr._limit_mode and app.screen_stack[-1] is scr
        joined3b = _tok_text(scr)
        assert "세션 5h" in joined3b and "7%" in joined3b, joined3b  # /usage 막대
        await pilot.press("l")                          # 집계로 복귀
        await pilot.pause(0.1)
        assert not scr._limit_mode
        # 그 외 키는 닫는다
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is not scr, "Esc 로 닫힘"
    await _with_app(body)


async def test_token_log_recon_view_toggle():
    """[r]/[대사] 가 집계 ↔ 대사 **시간축 그래프** 뷰를 토글한다(요청 2026-06-20 —
    표 대신 그래프). 대사 뷰는 그래프 위젯(_ReconChart)을 보이고 표를 숨기며, 상단엔
    시간 범위·최신 5h%·구간 수를, 다시 [r] 로 집계 표로 돌아온다(닫히지 않음)."""
    async def body(app, pilot, srv):
        from textual.widgets import DataTable
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
        assert scr._recon_mode
        # 대사 모드: 그래프 보이고 표 숨김.
        chart = scr.query_one("#tkchart")
        assert chart.display and not scr.query_one(DataTable).display
        joined = _tok_text(scr)
        assert "사용률 추이" in joined, joined          # 그래프 제목
        assert "최신 2%" in joined and "구간 2개" in joined, joined
        # 막대 글리프가 실제로 렌더된다.
        rendered = "\n".join(chart.render_line(y).text
                             for y in range(chart.size.height))
        assert any(b in rendered for b in "▁▂▃▄▅▆▇█"), rendered
        await pilot.press("r")                     # 집계 표로 복귀
        await pilot.pause(0.1)
        assert not scr._recon_mode
        assert scr.query_one(DataTable).display and not chart.display
        joined2 = _tok_text(scr)
        assert "Δ+4" not in joined2 and "Σ1.5k" in joined2, joined2
    await _with_app(body)


async def test_recon_legend_shows_all_models_including_unknown():
    """사용자 요청 2026-06-22: [대사] 그래프 범례가 opus 뿐 아니라 **등장한 모든 모델**
    (그리고 모델 미귀속 구간은 '?')을 함께 보인다 — 종전엔 모델 분해된 티어만 떠
    미귀속(회색) 막대가 정체 불명이었다."""
    async def body(app, pilot, srv):
        base = 1_700_000_000.0
        recs = [{"ts": base + 200, "tab": 0, "pane": 1, "session": 1,
                 "account": "me@x.org", "tokens": 1500}]
        recon = [
            # opus 귀속 구간
            {"t0": base, "t1": base + 3600, "account": "me@x.org",
             "pct0": 5, "pct1": 9, "dpct": 4, "tokens": 1500,
             "models": {"opus": 1500}, "reset": False},
            # 모델 미귀속(막대는 그려짐, models 비어 있음) → 범례 '?'
            {"t0": base + 3600, "t1": base + 7200, "account": "me@x.org",
             "pct0": 9, "pct1": 12, "dpct": 3, "tokens": 0,
             "models": {}, "reset": False},
        ]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs, "reconcile": recon})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        await pilot.press("r")
        await pilot.pause(0.1)
        top = str(scr._tktop_text)        # _recon_top_line 결과(범례 포함)
        assert "Opus" in top and "?" in top, top
    await _with_app(body)


async def test_recon_scroll_hint_conditional_on_max_off():
    """항목5(2026-06-22): [대사] 그래프 footer 의 ←→ 스크롤 안내는 **스크롤 여지가
    있을 때만**(구간 수 > 한 화면 capacity → max_off>0) 보이고, 전 구간이 다 보이면
    'all spans shown'(전 구간 표시)로 바뀐다 — 종전엔 reconcile 캡(20)이 늘 capacity
    보다 작아 max_off==0 인데도 동작 않는 스크롤을 약속하던 거짓 안내. max_off 는
    위젯이 실제 폭으로 렌더(_build)될 때만 정해지므로(헤드리스 폭=0), 안내 결정
    함수(_set_recon_hint)와 빌드 훅(_on_built) 배선을 직접 검증한다."""
    async def body(app, pilot, srv):
        base = 1_700_000_000.0
        recs = [{"ts": base + 200, "tab": 0, "pane": 1, "session": 1,
                 "account": "me@x.org", "tokens": 1500}]
        ivs = [{"t0": base + i * 3600, "t1": base + (i + 1) * 3600,
                "account": "me@x.org", "pct0": i % 50, "pct1": (i % 50) + 1,
                "dpct": 1, "tokens": 100, "reset": False} for i in range(200)]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs, "reconcile": ivs})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        await pilot.press("r")
        await pilot.pause(0.1)
        assert scr._recon_mode
        # ① 안내 결정: max_off>0 → ←→ 스크롤, ==0 → 전 구간 표시.
        scr._set_recon_hint(7)
        assert "스크롤" in str(scr.query_one("#tkhint").render())
        scr._set_recon_hint(0)
        h = str(scr.query_one("#tkhint").render())
        assert "전 구간 표시" in h and "스크롤" not in h, h
        # ② 차트가 실제 폭(여기선 강제 plot_dims)으로 빌드되면 _on_built 훅이 max_off
        #    를 hint 로 민다 — 200 구간 > capacity(폭10→5) 라 스크롤 여지가 생긴다.
        chart = scr.query_one("#tkchart")
        got = []
        chart._on_built = lambda mo: got.append(mo)
        chart._last_built_off = None
        chart._plot_dims = lambda: (10, 5)
        chart._build()
        assert got and got[-1] > 0, got
    await _with_app(body)


async def test_token_log_limit_view_toggle():
    """2026-06-14(사용자 요청): 상단 빽빽한 한도 블록(막대·창Σ·계정·신선도 ~7줄)을
    [한도] 전용 서브뷰로 옮겨 작은 화면을 정리. 기본 화면 상단엔 1줄 요약(5h%·주%)만,
    [l]/[한도] 탭으로 상세를 표 자리에 펼치고 다시 [l] 로 집계 복귀. recon 과 배타하며
    기간 키로 빠져나온다."""
    async def body(app, pilot, srv):
        recs = [{"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
                 "account": "me@x.org", "tokens": 1500}]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "TokenLogScreen"
        scr.update_usage({
            "session": {"pct": 17, "reset": "5:39pm (Asia/Seoul)"},
            "week_all": {"pct": 14, "reset": "Jun 20 (Asia/Seoul)"}})
        await pilot.pause(0.1)
        # 기본(집계) 화면: 상단 1줄 요약만 — 막대 라벨/리셋 상세는 숨김.
        base = _tok_text(scr)
        assert "5h 17%" in base and "주 14%" in base, base
        assert "세션 5h" not in base, base       # 막대 상세 라벨은 기본 화면에 없음
        assert "5:39pm" not in base, base          # 리셋 상세도 숨김
        # [l] 한도 뷰: 막대 상세가 표 자리에 보인다.
        await pilot.press("l")
        await pilot.pause(0.1)
        assert scr._limit_mode and app.screen_stack[-1] is scr
        lim = _tok_text(scr)
        assert "세션 5h" in lim and "17%" in lim, lim
        assert "주 전체" in lim and "14%" in lim, lim
        assert any(b in lim for b in "▏▎▍▌▋▊▉█"), lim    # 막대 글리프
        assert "5:39pm" in lim, lim                       # 리셋 상세
        # 마우스 탭 클릭으로도 토글 → 집계 복귀.
        await pilot.click("#tab_limit")
        await pilot.pause(0.1)
        assert not scr._limit_mode
        # recon 과 배타: 한도 켠 뒤 r → recon 으로 넘어가며 한도 꺼짐.
        await pilot.press("l")
        await pilot.pause(0.1)
        await pilot.press("r")
        await pilot.pause(0.1)
        assert scr._recon_mode and not scr._limit_mode
        # 다시 l → 한도 켜지고 recon 꺼짐(상호 배타).
        await pilot.press("l")
        await pilot.pause(0.1)
        assert scr._limit_mode and not scr._recon_mode
        # 한도 뷰에서 기간 탭 클릭 → 기간 뷰로 빠져나온다(어느 모드에서도 먹게).
        await pilot.click("#tab_period")
        await pilot.pause(0.1)
        assert not scr._limit_mode and scr._view == "time"
        await pilot.press("escape")
        await pilot.pause(0.1)
    await _with_app(body)


async def test_token_log_tab_subrow_and_limit_return():
    """§7.1·§7.2: 상위 뷰 탭 1줄 + 활성 탭 보조옵션 하위줄. 보조옵션 줄은 기간/세션
    뷰에서만 보이고(입도 그룹은 기간 뷰만), 한도/대사/경고 뷰에선 숨는다. 한도
    뷰에서 기간 입도가 강조되지 않으며(§7.1), 기간 탭 클릭으로 한도에서 복귀된다."""
    from textual.widgets import Label
    async def body(app, pilot, srv):
        recs = [{"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
                 "account": "me@x.org", "tokens": 1500}]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "TokenLogScreen"
        # 기간(time) 뷰: 기간 탭 활성. 입도 서브탭/정렬 보조옵션 줄은 계층 트리·정렬
        # 제거로 함께 없앴다(2026-06-22) — #tksub 위젯 자체가 없다.
        assert scr._active_tab() == "time"
        assert not scr.query("#tksub"), "보조옵션 줄(#tksub)은 제거돼야"
        assert scr.query_one("#tab_period", Label).has_class("tkbtab-active")
        # 세션 뷰 전환.
        await pilot.press("p")
        await pilot.pause(0.1)
        assert scr._active_tab() == "session"
        # 한도 뷰: 한도 탭만 활성, 기간 탭은 강조 안 됨(§7.1).
        await pilot.press("l")
        await pilot.pause(0.1)
        assert scr._active_tab() == "limit"
        assert scr.query_one("#tab_limit", Label).has_class("tkbtab-active")
        assert not scr.query_one("#tab_period", Label).has_class("tkbtab-active")
        # 한도에서 기간 탭 클릭 → 기간 뷰 복귀(§7.1 핵심: 복귀 동선 명확).
        await pilot.click("#tab_period")
        await pilot.pause(0.1)
        assert not scr._limit_mode and scr._active_tab() == "time"
        await pilot.press("escape")
        await pilot.pause(0.1)
    await _with_app(body)


async def test_token_log_opens_time_view_from_5h_segment():
    """상태줄 "N%/5h used" 세그먼트 클릭 경로(open_token_log("hour"))는 팝업을 기간
    (계층 타임라인) 뷰로 연다 — 오늘 행이 시각까지 펼쳐져 5h% 막대가 바로 보인다(옛
    hour 버킷 대체, 2026-06-21). 모드 없이 여는 일반 경로도 같은 기간 뷰."""
    async def body(app, pilot, srv):
        recs = [{"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
                 "account": "me@x.org", "tokens": 1500}]
        # 5h% 세그먼트 클릭 = open_token_log("hour") → _token_log_initial 세팅.
        app._want_token_log = True
        app._token_log_initial = "hour"
        app._dispatch({"t": "token_log", "records": recs,
                       "hourly_pct": {"2023-11-14 22:00": 8}})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "TokenLogScreen"
        assert scr._active_tab() == "time", scr._active_tab()
        await pilot.press("escape")
        await pilot.pause(0.1)
        # 대조: 모드 없이 여는 일반 경로도 같은 기간(시간순 트리) 뷰.
        app._want_token_log = True
        app._token_log_initial = None
        app._dispatch({"t": "token_log", "records": recs})
        await pilot.pause(0.1)
        scr2 = app.screen_stack[-1]
        assert scr2._active_tab() == "time", scr2._active_tab()
        await pilot.press("escape")
        await pilot.pause(0.1)
    await _with_app(body)


async def test_warn_tab_tree_active_expanded_past_collapsed():
    """항목2(2026-06-22): [경고] 탭이 트리 — 현재 활성 경고(라이브 status)는 맨 위
    노드로 펼쳐져 상황·할일 본문이 바로 보이고, 과거 경고 이력(서버 warn_history)은
    아래에 접혀 나열된다. 과거 노드를 Enter 로 펼치면 그 경고의 본문이 보인다."""
    async def body(app, pilot, srv):
        app.status.claude_warn = "⚠ 5:00"
        app.status.claude_warn_kind = "long_turn"
        app.status.claude_warn_n = None
        recs = [{"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
                 "account": "a", "tokens": 1}]
        wh = [{"ts": 1_700_000_500.0, "kind": "repeat", "n": 3, "badge": "x"},
              {"ts": 1_700_000_400.0, "kind": "fmt_unknown", "n": None,
               "badge": "y"}]
        app._want_token_log = True
        app._token_log_initial = "warn"
        app._dispatch({"t": "token_log", "records": recs, "warn_history": wh})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr._active_tab() == "warn", scr._active_tab()
        joined = _tok_text(scr)
        # 활성(장기 턴) 경고는 기본 펼침 → 본문이 보인다.
        assert "임계 시간을 넘겨" in joined, joined
        # 과거 경고 섹션 라벨은 보이고, 과거 repeat 의 본문은 접혀 숨김.
        assert "이전 경고" in joined, joined
        assert "같은 출력이 여러 번 반복" not in joined, "과거 repeat 본문은 접힘이어야"
        # repeat 헤더 행으로 커서 이동 후 Enter → 펼쳐져 본문이 보인다.
        from textual.widgets import DataTable
        table = scr.query_one(DataTable)
        target = None
        for r in range(table.row_count):
            cell = " ".join(str(c) for c in table.get_row_at(r))
            if "동일 결과 3회" in cell:
                target = r
                break
        assert target is not None, "repeat 헤더 행을 찾지 못함"
        table.move_cursor(row=target)
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert "같은 출력이 여러 번 반복" in _tok_text(scr), "Enter 로 과거 경고 펼침"
    await _with_app(body)


async def test_statusbar_emoji_deemojied_under_modal_backdrop():
    """반투명 모달(팝업)이 떠 본문을 어둡게 깔면 상태표시줄의 컬러 이모지(경고 ⚠)도
    함께 딤돼 보여야 한다 — Textual backdrop 은 셀 스타일색만 블렌딩하고 컬러 이모지
    글리프는 안 어두워지므로, 상태표시줄이 스스로 ⚠ 를 폭1 placeholder(·)로 바꾼다
    (#25). 팝업을 닫으면 원본 ⚠ 로 복원되고, 폭(셀 길이)은 두 경우 모두 같다."""
    from pytmuxlib.clientscreens import InfoScreen

    async def body(app, pilot, srv):
        app.status.claude_warn = "⚠ 5:00"
        app.status.claude_warn_kind = "long_turn"
        app.status.claude_warn_n = None
        app.status.refresh()
        await pilot.pause(0.05)
        # 모달 없음: ⚠ 가 상태표시줄에 그대로 그려진다.
        bottom = app.status.size.height - 1
        plain = app.status.render_line(bottom)
        assert "⚠" in plain.text, plain.text
        # 반투명 backdrop 팝업(InfoScreen)을 띄우면 ⚠ → · 로 치환(딤 일관).
        app.push_screen(InfoScreen(["body line"], title="t"))
        await pilot.pause(0.05)
        dimmed = app.status.render_line(bottom)
        assert "⚠" not in dimmed.text, dimmed.text
        assert "·" in dimmed.text, dimmed.text
        # 폭(셀 길이) 보존 — 클릭존·우측정렬이 흔들리지 않는다.
        assert dimmed.cell_length == plain.cell_length
        # 팝업 닫으면 원본 ⚠ 복원.
        await pilot.press("escape")
        await pilot.pause(0.05)
        restored = app.status.render_line(bottom)
        assert "⚠" in restored.text, restored.text
    await _with_app(body)


async def test_tabbar_emoji_deemojied_under_modal_backdrop():
    """상태표시줄과 동일(#25): 탭 이름에 컬러 이모지가 있으면, 반투명 모달 backdrop
    딤 중에는 탭바도 _composite 그리드 밖 위젯이라 그 이모지가 안 어두워진다 → 탭바가
    스스로 폭 보존 placeholder(·)로 치환한다. 팝업을 닫으면 원본으로 복원된다."""
    from pytmuxlib.clientscreens import InfoScreen

    async def body(app, pilot, srv):
        app.tabbar.set_tabs([
            {"index": 0, "name": "⚠hot", "active": True},
            {"index": 1, "name": "calm", "active": False}], 0)
        await pilot.pause(0.05)
        plain = app.tabbar.render_line(0)
        assert "⚠hot" in plain.text, plain.text
        # 반투명 backdrop 팝업 → 탭명 이모지 ⚠ → · (폭 보존).
        app.push_screen(InfoScreen(["body line"], title="t"))
        await pilot.pause(0.05)
        dimmed = app.tabbar.render_line(0)
        assert "⚠" not in dimmed.text, dimmed.text
        assert "·hot" in dimmed.text, dimmed.text
        assert dimmed.cell_length == plain.cell_length
        # 팝업 닫으면 원본 탭명 복원.
        await pilot.press("escape")
        await pilot.pause(0.05)
        restored = app.tabbar.render_line(0)
        assert "⚠hot" in restored.text, restored.text
    await _with_app(body, cfg={"tab_bar_always": True})


async def test_token_log_tree_has_5h_and_1w_columns_no_ratio():
    """사용자 요청(2026-06-17): 시각 행에 5h% 옆 1w%(주간 한도) 열을 두고 기존
    비율(ratio/막대) 열은 없다. 계층 트리(항상 시간순)는 hourly 데이터가 있으면
    5h%/1w% 열을 둔다(정렬 토큰순 옵션은 제거됨, 2026-06-22)."""
    from textual.widgets import DataTable
    async def body(app, pilot, srv):
        recs = [{"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
                 "account": "me@x.org", "tokens": 1000}]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs,
                       "hourly_pct": {"2023-11-14 22:00": 13},
                       "hourly_week_pct": {"2023-11-14 22:00": 42}})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "TokenLogScreen"
        table = scr.query_one(DataTable)
        labels = [str(c.label) for c in table.columns.values()]
        assert any("5h%" in lb for lb in labels), labels
        assert any("1w%" in lb for lb in labels), labels       # 신규 열
        assert not any(("비율" in lb or "Ratio" in lb) for lb in labels), labels
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
        app._dispatch({"t": "token_log", "records": recs, "total_all": 9500})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        joined = _tok_text(scr)
        assert "Σ9.5k" in joined, joined          # lifetime 합(전체 이력)
        assert "표시 3.5k" in joined, joined        # 표시 레코드 합 병기
    await _with_app(body)


async def test_token_log_window_estimate_line():
    """실측 리셋 표기로 현재 5h 창을 역산(리셋-5h)해 그 창의 스크랩 추정 Σ 와
    리셋까지 남은 시간을 한 줄로 보인다(요청: 5시간 내 사용량·리셋 시점을 알기
    쉽게). 창 밖(5h 이전) 레코드는 합산되지 않아야 한다."""
    async def body(app, pilot, srv):
        import time as _t
        now = _t.time()
        # 리셋 = 지금+30분 — 로컬 12시간제 표기로 구성("H:MMam/pm (Asia/Seoul)").
        tm = _t.localtime(now + 1800)
        h12 = (tm.tm_hour + 11) % 12 + 1
        ap = "am" if tm.tm_hour < 12 else "pm"
        reset = f"{h12}:{tm.tm_min:02d}{ap} (Asia/Seoul)"
        recs = [   # 창 안(1시간 전) 1k + 창 밖(6시간 전) 500
            {"ts": now - 3600, "tab": 0, "pane": 1, "session": 1,
             "account": "me@x.org", "tokens": 1000},
            {"ts": now - 6 * 3600, "tab": 0, "pane": 1, "session": 1,
             "account": "me@x.org", "tokens": 500},
        ]
        app.status.usage_limits = {"session": {"pct": 7, "reset": reset}}
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        await pilot.press("l")                       # 창 추정 Σ 는 [한도] 뷰로 이동
        await pilot.pause(0.1)
        joined = _tok_text(scr)
        assert "이번 5h창 ~Σ1k" in joined, joined   # 창 밖 500 은 제외돼야
        assert "리셋" in joined and "후" in joined, joined
        await pilot.press("escape")
        await pilot.pause(0.1)
    await _with_app(body)


async def test_token_log_day_bucket_full_history_not_capped():
    """계층 트리는 서버 daily(전체 이력 일자 GROUP BY) 로 집계해 레코드 cap 에 옛 날짜가
    잘리지 않는다. records(서버가 보내는 cap 된 최근 N 건)엔 최근 하루만, daily 엔 12일치
    전체 이력을 넣으면 트리 합산이 12일 전부(12k)를 반영해야 한다(수정 전엔 records 만
    써서 최근 하루만 보였다). 트리 합은 날짜-무관이라 _build_tree_rows 의 total 로 검증
    (렌더 텍스트는 오늘 날짜에 따라 펼침이 달라 fragile — 정렬 토큰순 평탄 목록 제거)."""
    async def body(app, pilot, srv):
        ACCT = "me@x.org"
        # daily: 2026-06-01..06-12 전체 이력(각 1000) — 합 12000.
        daily = [{"day": f"2026-06-{d:02d}", "account": ACCT, "session": 1,
                  "tab": 0, "pane": 1, "tokens": 1000} for d in range(1, 13)]
        # records: 최근 하루치 1건만 — cap 으로 옛 날짜가 안 온 상황 시뮬.
        recs = [{"ts": 1_780_000_000.0, "tab": 0, "pane": 1, "session": 1,
                 "account": ACCT, "tokens": 1000}]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs, "total_all": 12000,
                       "daily": daily})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "TokenLogScreen", scr
        # 트리 집계 합(중복 없는 일자 전체 합)이 daily 전체 이력 12k 여야 한다 — 단일
        # cap 레코드(1k)가 아니라 daily 가 트리를 구동함을 날짜-무관하게 입증.
        _nodes, total = scr._build_tree_rows()
        assert total == 12000, f"트리 합이 전체 이력(12k)이어야: {total}"
        # Σ 는 전체 이력 합(12k); 표시 합도 12k 라 '(표시 …)' 병기 없음.
        joined = _tok_text(scr)
        assert "Σ12k" in joined, joined
        assert "표시" not in joined, f"전체 이력엔 과소표시 병기가 없어야: {joined}"
    await _with_app(body)


async def test_token_log_panel_subtab_groups_by_session():
    """[세션] 서브탭/[p]: 기간 뷰 ↔ 세션 뷰 토글. 세션 뷰는 (재사용되는) 패널 id 가
    아니라 세션 id 로 묶어 보인다(설계 §8 — 세션 기준)."""
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
        assert scr._view == "time", "기본은 기간 뷰"
        # 키 [p] 로 세션 뷰 토글
        await pilot.press("p")
        await pilot.pause(0.1)
        assert scr._view == "session" and app.screen_stack[-1] is scr
        joined = _tok_text(scr)
        assert "세션 1" in joined and "세션 2" in joined, joined
        # 세션 라벨에 대표 탭:패널이 곁들여진다(식별성, 사용자 결정)
        assert "탭" in joined and ":p" in joined, joined
        assert "세션별" in joined, joined
        # 세션1=2000(1500+500), 세션2=2000 — 합 4000 유지
        assert "Σ4k" in joined, joined
        # 마우스로 [세션] 탭 클릭 → 기간 뷰로 되돌림(토글)
        await pilot.click("#tab_panel")
        await pilot.pause(0.1)
        assert scr._view == "time" and app.screen_stack[-1] is scr
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
        await pilot.press("l")        # 한도 막대 상세는 [한도] 뷰(2026-06-14)
        await pilot.pause(0.1)
        joined = _tok_text(scr)
        # 세 한도 라벨 + 각 % 가 보인다 — % 는 '사용' 라벨로 방향을 명시한다
        # (footer 의 5h 표기도 같은 사용률로 통일 — 2026-06-12).
        assert "세션 5h" in joined and "54% 사용" in joined, joined
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
        import datetime as _dt
        from textual.widgets import DataTable
        # 계층 트리는 오늘 행을 시각까지 펼친다 — 두 값을 오늘의 서로 다른 시각에 둬
        # 별개 시각 행(M 급·k 급)으로 보이게 한다(정렬 비교용).
        noon = _dt.datetime.now().replace(hour=12, minute=0, second=0,
                                          microsecond=0).timestamp()
        recs = [
            {"ts": noon, "tab": 0, "pane": 1, "session": 1,
             "account": "me@x.org", "tokens": 7_800_000},   # M 급
            {"ts": noon - 3600, "tab": 0, "pane": 1, "session": 1,
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


async def test_f10_opens_full_menu():
    # F10 = 전체 메뉴(컨텍스트 메뉴 최상위) 직진 진입(F10_MENU_SCENARIO.md).
    # 종전 진입로(prefix Enter·우클릭)에 더해 normal-mode 단일키로 연다.
    async def body(app, pilot, srv):
        assert app.mode == "normal"
        await pilot.press("f10")
        await pilot.pause(0.1)
        menu = app.screen_stack[-1]
        assert menu.__class__.__name__ == "MenuScreen", "F10 → 전체 메뉴"
        assert menu._entries is None, "최상위(MENU_TOPLEVEL) 메뉴"
        # 모든 액션 그룹이 도달 가능한지: 최상위 토큰에 패널/레이아웃/탭 그룹 진입점
        toks = menu._toplevel_entries()
        assert {"group:pane", "group:layout", "group:tab"} <= set(toks)
    await _with_app(body)


async def test_f10_toggles_menu_closed():
    # 메뉴가 떠 있을 때 F10 을 다시 누르면 닫힌다(메뉴바 토글: 연 키=닫는 키).
    async def body(app, pilot, srv):
        await pilot.press("f10")
        await pilot.pause(0.1)
        menu = app.screen_stack[-1]
        assert menu.__class__.__name__ == "MenuScreen"
        # 모달이 떠 있으므로 키는 MenuScreen.on_key 가 받아 닫는다.
        menu.on_key(Key(key="f10", character=None))
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is not menu, "F10 재입력 → 메뉴 닫힘"
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
        # 비토글 항목 선택 → 메뉴 닫힘(§8.1: search 는 최상위 직접 항목)
        menu.on_list_view_selected(_Sel("m_search"))
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is not menu, "비토글 선택 → 닫힘"
    await _with_app(body)


async def test_context_menu_click_outside_closes():
    """우클릭 컨텍스트 메뉴(MenuScreen)는 박스(#menu) 바깥(백드롭) 클릭 시 dismiss(None)
    로 닫힌다(PluginManagerScreen·InfoScreen 과 동일한 inside-box 판정). 박스 안(항목)
    클릭은 닫지 않아 on_list_view_selected 토글/선택이 그대로 동작한다."""
    async def body(app, pilot, srv):
        app.open_menu(app.layout.get("active"))
        await pilot.pause(0.1)
        menu = app.screen_stack[-1]
        assert menu.__class__.__name__ == "MenuScreen"

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

        # 박스 안 클릭(항목 → ListView#menu → screen) → 닫히지 않음
        ev_in = _Ev(_W("m_search", parent=_W("menu", parent=_W("screen"))))
        menu.on_click(ev_in)
        await pilot.pause(0.05)
        assert app.screen_stack[-1] is menu, "박스 안 클릭은 닫지 않는다"
        # 박스 바깥(백드롭) 클릭 → 닫힘
        ev_out = _Ev(_W("backdrop", parent=None))
        menu.on_click(ev_out)
        await pilot.pause(0.05)
        assert app.screen_stack[-1] is not menu, "바깥 클릭은 메뉴를 닫는다"
        assert ev_out.stopped
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


async def test_context_menu_plugin_items_join_and_mouse_help():
    """§2.7+§2.2: ① 플러그인 메뉴 항목(clock/calendar — key=그 플러그인 명령 이름)이
    컨텍스트 메뉴에 병합되고, 코어에 없는 key 는 _run_menu_action 이 _run_command 로
    폴백 디스패치한다. ② 신규 코어 항목 join_pane(명령 프롬프트 프리필)·mouse_help
    (list-keys "키 · 마우스" 팝업 재사용)와 :mouse-help 별칭 배선."""
    async def body(app, pilot, srv):
        from pytmuxlib.clientscreens import InfoScreen
        from textual.widgets import ListItem
        # :mouse-help 별칭 → list-keys 와 같은 "키 · 마우스" InfoScreen.
        app._run_command("mouse-help")
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert isinstance(scr, InfoScreen), "mouse-help → 키·마우스 팝업"
        app.pop_screen()
        # 메뉴에 플러그인 항목 + 신규 코어 항목이 보인다.
        app.open_menu(app.layout.get("active"))
        await pilot.pause(0.1)
        menu = app.screen_stack[-1]
        assert menu.__class__.__name__ == "MenuScreen"
        # §8.1: 최상위엔 그룹 진입점 + 직접 항목(mouse_help)만. 평면 항목은 그룹 하위로.
        top_ids = {it.id for it in menu.query(ListItem)}
        assert "m_mouse_help" in top_ids
        assert {"g_pane", "g_layout", "g_tab", "g_plugin"} <= top_ids, top_ids
        # 플러그인 항목은 "플러그인" 그룹, join_pane 은 "패널" 그룹 하위에서 도달.
        assert {"clock-mode", "calendar-mode"} <= set(menu._group_items("plugin"))
        assert "join_pane" in menu._group_items("pane")
        await pilot.press("escape")
        await pilot.pause(0.05)
        # 폴백 디스패치: 미지 키(플러그인 명령 이름) → _run_command(key).
        ran = []
        app._run_command = lambda line: ran.append(line)
        app._run_menu_action("clock-mode")
        assert ran == ["clock-mode"], ran
        # mouse_help 메뉴 → mouse-help 명령 경로.
        ran.clear()
        app._run_menu_action("mouse_help")
        assert ran == ["mouse-help"], ran
        # join_pane → 명령 프롬프트 "join-pane " 프리필(rename_pane 패턴).
        opened = []
        app.open_prompt = lambda purpose, ph="", **k: opened.append((purpose, k))
        app._run_menu_action("join_pane")
        assert ("command", {"initial": "join-pane "}) in opened, opened
    await _with_app(body)


def _sel_event(iid):
    """ListView.Selected 모사 — on_list_view_selected(event) 직접 호출용."""
    return type("E", (), {"item": type("I", (), {"id": iid})()})()


async def test_context_menu_grouped_submenu_and_reachability():
    """§8.1: 컨텍스트 메뉴가 그룹(서브메뉴)+구분선 구조다. ① 최상위는 그룹 진입점·직접
    항목·구분선만 ② 구분선은 비선택(disabled) ③ 모든 평면 액션이 그룹∪최상위 직접항목으로
    빠짐없이·중복없이 도달 ④ 그룹 펼침 → 자식 MenuScreen, 자식 leaf 선택은 부모로 버블해
    _run_menu_action 까지 디스패치되고 전체 메뉴가 닫힌다."""
    async def body(app, pilot, srv):
        from textual.widgets import ListItem
        from pytmuxlib.clientutil import MENU_ITEMS, MENU_GROUPS, MENU_TOPLEVEL
        app.open_menu(app.layout.get("active"))
        await pilot.pause(0.1)
        menu = app.screen_stack[-1]
        assert menu.__class__.__name__ == "MenuScreen"
        items = list(menu.query(ListItem))
        ids = [it.id for it in items]
        assert {"g_pane", "g_layout", "g_tab"} <= set(ids), ids
        seps = [it for it in items if (it.id or "").startswith("sep_")]
        assert seps and all(it.disabled for it in seps), "구분선은 비선택"
        # 도달성: 그룹∪최상위 직접항목 == MENU_ITEMS 키 전체, 중복 없음.
        grouped = set().union(*[set(v) for v in MENU_GROUPS.values()])
        direct = {t for t in MENU_TOPLEVEL
                  if not t.startswith("group:") and t != "--"}
        assert grouped | direct == {k for k, _ in MENU_ITEMS}, "전 액션 도달"
        assert not (grouped & direct), "그룹/직접 중복 없음"
        # 그룹 펼침 → 자식 MenuScreen(헤더 있음), leaf 선택 → 부모로 버블 디스패치.
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        menu._open_group("pane")
        await pilot.pause(0.1)
        child = app.screen_stack[-1]
        assert child is not menu and child.__class__.__name__ == "MenuScreen"
        assert child._title, "서브메뉴 헤더(그룹 라벨)"
        child.on_list_view_selected(_sel_event("m_split_lr"))
        await pilot.pause(0.1)
        assert ("split", {"orient": "lr"}) in sent, sent
        assert all(s.__class__.__name__ != "MenuScreen"
                   for s in app.screen_stack), "leaf 디스패치 후 전체 닫힘"
    await _with_app(body)


async def test_context_menu_submenu_cascades_to_right_of_parent():
    """§8.1: 그룹 선택 시 자식 서브메뉴는 중앙이 아니라 부모 메뉴 우측에 인접해
    캐스케이드로 펼쳐진다. 최상위 메뉴는 우측에 서브메뉴 한 폭을 비워두도록 좌측
    바이어스되어(화면 폭≥메뉴×2면) 자식이 **부모와 겹치지 않고** 우측에 들어간다."""
    async def body(app, pilot, srv):
        w = 40
        app.open_menu(app.layout.get("active"))
        await pilot.pause(0.1)
        top = app.screen_stack[-1]
        reg = top.query_one("#menu").region
        # 좌측 바이어스: 100폭에선 부모 우측+메뉴폭 ≤ 화면폭 (우측에 자식 자리 확보).
        assert reg.right + w <= app.size.width, (reg.right, app.size.width)
        item = top.query_one("#g_pane")          # 실제 그룹 행(앵커 row y)
        top._open_group("pane", item)
        await pilot.pause(0.1)
        child = app.screen_stack[-1]
        assert child is not top and child._anchor is not None, "앵커 캐스케이드"
        off = child.query_one("#menu").styles.offset
        # 부모 우측 edge 에 인접, 부모와 안 겹침.
        assert int(off.x.value) == reg.right, (off.x.value, reg.right)
        assert int(off.x.value) >= reg.right, "자식이 부모 우측(무겹침)"
        assert int(off.y.value) >= 0             # 화면 안에 배치
    await _with_app(body)


async def test_context_menu_submenu_hover_switches_group():
    """§8.1: 한 서브메뉴가 열린 상태에서 마우스를 부모의 **다른** 그룹 항목 위로
    호버하면 현재 서브메뉴를 닫고 그 그룹 서브메뉴를 연다(요청). 같은 그룹 위면 유지."""
    async def body(app, pilot, srv):
        app.open_menu(app.layout.get("active"))
        await pilot.pause(0.1)
        top = app.screen_stack[-1]
        top._open_group("pane", top.query_one("#g_pane"))
        await pilot.pause(0.1)
        child = app.screen_stack[-1]
        assert child is not top and child._group == "pane", child._group
        # 'layout' 그룹 항목 영역 중심으로 호버 → on_mouse_move 가 부모로 위임 → 전환.
        lr = top.query_one("#g_layout").region

        class _MM:
            screen_x = lr.x + lr.width // 2
            screen_y = lr.y + lr.height // 2
        child.on_mouse_move(_MM())
        await pilot.pause(0.15)
        new_child = app.screen_stack[-1]
        assert new_child is not child, "다른 그룹 호버 → 서브메뉴 교체"
        assert new_child._group == "layout", new_child._group
        assert new_child._prev_menu is top, "새 서브메뉴의 부모는 최상위"
        # 같은(layout) 그룹 위 재호버는 유지 — 전환/재오픈 안 함.
        lr2 = top.query_one("#g_layout").region

        class _MM2:
            screen_x = lr2.x + lr2.width // 2
            screen_y = lr2.y + lr2.height // 2
        new_child.on_mouse_move(_MM2())
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is new_child, "같은 그룹 호버는 서브메뉴 유지"
    await _with_app(body)


async def test_context_menu_toplevel_left_biased_keeps_center_when_wide():
    """§8.1: 최상위 메뉴 좌측 바이어스는 '필요한 만큼만' — 화면이 넉넉하면(≥메뉴×2 보다
    충분히 큼) 중앙 유지(offset.x dx==0), 좁으면 우측에 한 폭 비우게 왼쪽으로 민다."""
    async def body_wide(app, pilot, srv):
        app.open_menu(app.layout.get("active"))
        await pilot.pause(0.1)
        top = app.screen_stack[-1]
        off = top.query_one("#menu").styles.offset
        assert int(off.x.value) == 0, ("넓으면 중앙(dx=0)", off.x.value)
    await _with_app(body_wide, size=(160, 40))

    async def body_narrow(app, pilot, srv):
        app.open_menu(app.layout.get("active"))
        await pilot.pause(0.1)
        top = app.screen_stack[-1]
        off = top.query_one("#menu").styles.offset
        assert int(off.x.value) < 0, ("좁으면 왼쪽으로", off.x.value)
        reg = top.query_one("#menu").region
        assert reg.right + 40 <= 100, (reg.right,)   # 우측 한 폭 확보
    await _with_app(body_narrow, size=(100, 30))


async def test_context_menu_submenu_esc_returns_to_parent_and_toggle_stays():
    """§8.1: 서브메뉴에서 토글(zoom)은 메뉴를 안 닫고, Esc 는 부모 메뉴로만 복귀한다
    (전체 닫힘 아님)."""
    async def body(app, pilot, srv):
        app.open_menu(app.layout.get("active"))
        await pilot.pause(0.1)
        top = app.screen_stack[-1]
        top._open_group("pane")
        await pilot.pause(0.1)
        child = app.screen_stack[-1]
        assert child is not top
        app.send_cmd = lambda *a, **k: None
        # zoom 토글 → 자식 유지(안 닫힘).
        child.on_list_view_selected(_sel_event("m_zoom"))
        assert app.screen_stack[-1] is child, "토글은 서브메뉴 유지"
        # Esc → 부모(top)로 복귀, top 은 아직 열림.
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert app.screen_stack[-1] is top, "Esc=부모 복귀(전체 닫힘 아님)"
        # _menu_screen 은 부모로 복원돼 status 갱신이 최상위를 가리킨다.
        assert app._menu_screen is top
    await _with_app(body)


async def test_context_menu_dims_other_panes():
    # 컨텍스트 메뉴가 열려 있는 동안 대상 패널 외 나머지 패널은 흐리게(dim) 그려
    # 어느 패널 대상인지 배경으로 구분한다(#18).
    async def body(app, pilot, srv):
        app.inactive_dim = False   # §2.9 비활성 dim 격리(이 테스트는 메뉴 dim 만 검증)
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


async def test_inactive_pane_dimmed_unless_toggled_or_single():
    """§2.9(요청): 한 탭에 패널이 둘 이상이면 비활성 패널을 활성 대비 한 톤 옅게(dim)
    그려 외곽선 없이도 활성 패널을 구분한다. 활성 패널은 원색, 토글 off·단일 패널이면
    dim 없음."""
    async def body(app, pilot, srv):
        # 테두리(행 0/마지막·좌우 끝)는 활성/비활성 box 색이 원래 다르므로, 내부 콘텐츠
        # 셀(행 2)을 본다(context-menu dim 테스트와 동일 관례).
        app.inactive_dim = True
        app.inactive_dim_ratio = 0.30
        app.layout = {"panes": [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 5,
                                 "box": [0, 0, 10, 5]},
                                {"id": 2, "x": 10, "y": 0, "w": 10, "h": 5,
                                 "box": [10, 0, 10, 5]}],
                      "active": 1, "cols": 20, "rows": 5, "dividers": []}
        app.pane_content = {1: ([[("a" * 10, {})] for _ in range(5)], None),
                            2: ([[("b" * 10, {})] for _ in range(5)], None)}
        app._composite()
        cells = app.view._cells
        s_active = cells[2][5][1]       # 패널 1(활성) 내부 콘텐츠
        s_inactive = cells[2][15][1]    # 패널 2(비활성) 내부 콘텐츠
        # 활성=원색(기본 None), 비활성=흐린 실색 폴백 → 색이 달라야 한다
        assert s_inactive.color is not None, "비활성은 흐린 실색 적용"
        assert s_inactive.color != s_active.color, (s_active.color, s_inactive.color)
        # 토글 off → 비활성도 원색 복귀(활성과 동일)
        app.inactive_dim = False
        app._composite()
        assert app.view._cells[2][15][1].color == app.view._cells[2][5][1].color, \
            "off 면 dim 없음"
        # 단일 패널 → inactive_dim on 이어도 dim 안 함(구분 대상 없음)
        app.inactive_dim = True
        app.layout = {"panes": [{"id": 1, "x": 0, "y": 0, "w": 20, "h": 5,
                                 "box": [0, 0, 20, 5]}],
                      "active": 1, "cols": 20, "rows": 5, "dividers": []}
        app.pane_content = {1: ([[("a" * 20, {})] for _ in range(5)], None)}
        app._composite()
        assert app.view._cells[2][5][1].color == s_active.color, "단일 패널은 dim 없음"
    await _with_app(body)


async def test_inactive_pane_emoji_dimmed_to_placeholder():
    """§2.10: 비활성(딤) 패널의 컬러 이모지는 터미널이 셀 전경색을 무시해 안 어두워지므로,
    폭 보존 중간점(·)으로 치환한다(폭2 이모지→··). 활성 패널은 원본 이모지 유지, 토글
    off 면 원복."""
    async def body(app, pilot, srv):
        app.inactive_dim = True
        app.inactive_dim_ratio = 0.30
        app.layout = {"panes": [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 5,
                                 "box": [0, 0, 10, 5]},
                                {"id": 2, "x": 10, "y": 0, "w": 10, "h": 5,
                                 "box": [10, 0, 10, 5]}],
                      "active": 1, "cols": 20, "rows": 5, "dividers": []}
        # 각 패널 내부 행에 폭2 이모지 🔥(앞 2글자는 테두리 칸에 묻히는 더미). 패널
        # 테두리(좌단=x0/x10)가 콘텐츠 첫 칸을 덮으므로 이모지는 안쪽 칸에 둔다.
        app.pane_content = {1: ([[("ab\U0001F525cd", {})] for _ in range(5)], None),
                            2: ([[("ab\U0001F525cd", {})] for _ in range(5)], None)}
        app._composite()
        cells = app.view._cells
        # 활성(패널1) 이모지(x=2) = 원본 유지(치환 안 함)
        assert cells[2][2][0] == "\U0001F525", repr(cells[2][2][0])
        # 비활성(패널2) 이모지(x=12,13) = · 치환, 폭2라 두 칸 모두 ··
        assert cells[2][12][0] == "·" and cells[2][13][0] == "·", \
            (cells[2][12][0], cells[2][13][0])
        # 토글 off → 비활성도 원본 이모지 복귀(재합성)
        app.inactive_dim = False
        app._composite()
        assert app.view._cells[2][12][0] == "\U0001F525"
    await _with_app(body)


async def test_settings_screen_applies_persists_and_links():
    """통합 설정 화면(:settings): config-scoped 설정을 ←→ 로 바꾸면 즉시 적용되고
    config 파일에 영속, 링크 행은 dismiss 로 전용 화면 명령을 돌려준다."""
    import os
    import tempfile
    from pytmuxlib import i18n
    from pytmuxlib.clientscreens import SettingsScreen

    async def body(app, pilot, srv):
        p = os.path.join(tempfile.mkdtemp(), "config")
        app._config_path = p
        # 서버 status 가 권위 옵션을 채워 '미상' 대신 실제 값이 보인다(미상 제거).
        assert app.server_opts.get("vt_parser") in ("pyte", "native"), app.server_opts
        assert app.setting_current("coalesce-repaints") in ("on", "off")
        assert app.setting_current("vt-parser") == app.server_opts["vt_parser"]
        app._run_command("settings")
        await pilot.pause(0.2)
        scr = app.screen
        assert isinstance(scr, SettingsScreen), type(scr)
        # bool/enum 행은 선택지를 펼쳐 현재값을 강조(세그먼트) — '미상' 아님.
        vidx = next(i for i, (d, _f) in enumerate(scr._flat)
                    if d["key"] == "vt-parser")
        seg = scr._val_display(scr._flat[vidx][0])
        assert "native" in seg or "pyte" in seg
        assert i18n.t("setting.unknown") not in seg, seg

        # ratio 행: ←→ 로 +0.02 → app 상태 갱신 + config 기록.
        ridx = next(i for i, (d, _f) in enumerate(scr._flat)
                    if d["key"] == "inactive-dim-ratio")
        before = app.inactive_dim_ratio
        scr._cycle(ridx, 1)
        await pilot.pause(0.05)
        assert abs(app.inactive_dim_ratio - round(before + 0.02, 2)) < 1e-9, \
            app.inactive_dim_ratio
        assert "set inactive-dim-ratio" in open(p, encoding="utf-8").read()

        # bool 행: inactive-dim 토글 → 상태 반전 + config 기록.
        bidx = next(i for i, (d, _f) in enumerate(scr._flat)
                    if d["key"] == "inactive-dim")
        was = app.inactive_dim
        scr._cycle(bidx, 1)
        await pilot.pause(0.05)
        assert app.inactive_dim != was
        assert "set inactive-dim " in open(p, encoding="utf-8").read()

        # 세로 카테고리 탭: 탭으로 점프하면 전체 목록이 그 카테고리 첫 행으로 이동하고
        # 활성 탭이 따라온다(전체 목록 유지·필터링 아님).
        ci = scr._cats.index("동작")
        scr._jump_to_cat(ci)
        await pilot.pause(0.05)
        assert scr.query_one("#sets").index == scr._cat_first["동작"]
        assert scr._active_cat() == "동작"

        # 링크 행: _activate → dismiss(대상 명령).
        lidx = next(i for i, (d, _f) in enumerate(scr._flat)
                    if d["key"] == "token-saver")
        captured = {}
        scr.dismiss = lambda v=None: captured.setdefault("v", v)
        scr._activate(lidx)
        assert captured["v"] == "token-saver", captured
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


async def test_mouse_debug_logs_passthrough_with_believed_mode():
    """진단(HANDOFF §10-H): send_mouse 가 마우스 시퀀스를 PTY 로 흘릴 때, 클라가
    그 패널을 어떤 마우스 모드(mouse/sgr)로 **믿고** 보냈는지 + 실제 바이트를 함께
    남긴다. restart-all 후 SGR 모션이 프롬프트에 텍스트로 박히는 버그에서, 여기 찍힌
    'mouse=3 sgr=True' 는 '클라는 추적 ON 으로 믿었다'는 결정 신호다(앱은 실제 OFF)."""
    import os
    import tempfile

    async def body(app, pilot, srv):
        path = os.path.join(tempfile.gettempdir(), "pytmux_mousepass_test.log")
        if os.path.exists(path):
            os.remove(path)
        app._mouse_log_path = path
        pid = app.layout["panes"][0]["id"]
        app.layout["panes"][0]["mouse"] = 3
        app.layout["panes"][0]["mouse_sgr"] = True
        seq = b"\x1b[<35;43;38M"   # any-motion(35) SGR 모션 — 버그의 그 바이트
        # off 면 패스스루 자체는 되지만 진단 로그는 없음
        app.mouse_debug = False
        app.send_mouse(pid, seq)
        await pilot.pause(0.05)
        assert not os.path.exists(path), "mouse-debug off 면 패스스루 로그 없음"
        # on 이면 믿은 모드 + 바이트가 남는다
        app.mouse_debug = True
        app.send_mouse(pid, seq)
        await pilot.pause(0.05)
        with open(path) as f:
            log = f.read()
        assert "pass" in log, log
        assert "mouse=3" in log and "sgr=True" in log, log
        assert "1b" in log.lower() or "\\x1b" in log, log  # 바이트 repr 포함
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


async def test_close_remote_tab_routes_to_detach():
    """원격 탭(remote-attach 병합)을 닫기[x]/esc x 로 닫으면 kill_window(서버가
    §1.7-c 로 거부 → '원격 탭에서는 사용할 수 없는 명령입니다' notice) 대신 그
    링크를 분리하는 remote_detach 로 라우팅한다(사용자 보고 2026-06-20). 확인
    팝업도 '탭 닫기'가 아니라 '원격 탭 분리'."""
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        # 활성 탭이 원격(⇄office1:win) → 닫기 = remote_detach(host=office1)
        app.status.windows = [
            {"index": 0, "name": "local", "active": False},
            {"index": 1, "name": "⇄office1:win", "active": True,
             "remote": True}]
        assert app._active_remote_host() == "office1"
        app.confirm_kill_tab()
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "ConfirmScreen"
        assert "분리" in scr._message, scr._message
        await wait_until(pilot, lambda: app.screen_stack[-1].query_one("#cy"))
        await pilot.click("#cy")
        await pilot.pause(0.3)
        assert ("remote_detach", {"host": "office1"}) in sent, sent
        assert not any(a == "kill_window" for a, _ in sent), sent
        # 로컬 탭이 활성이면 종전대로 kill_window 경로(원격 분리 아님)
        sent.clear()
        app.status.windows = [
            {"index": 0, "name": "local", "active": True},
            {"index": 1, "name": "⇄office1:win", "active": False,
             "remote": True}]
        assert app._active_remote_host() is None
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


async def test_tabbar_pinned_render_right_with_separator():
    """항목7: 고정(pinned) 탭은 구분자 '‖' 오른쪽 구역에 핀 글리프('*') 프리픽스로
    그려진다. 고정 탭이 없으면 구분자·핀 글리프 없이 종전과 동일."""
    async def body(app, pilot, srv):
        app.tabbar.tabs = [
            {"index": 0, "name": "build", "active": True},
            {"index": 1, "name": "logs", "active": False},
            {"index": 2, "name": "p4v", "active": False, "pinned": True}]
        app.tabbar._entries_sig = None
        line = "".join(s.text for s in app.tabbar.render_line(0))
        assert "‖" in line, line                      # 구분자
        assert "*" in line, line                       # 핀 글리프
        assert line.index("‖") < line.index("p4v"), line   # 고정은 구분자 오른쪽
        assert line.index("logs") < line.index("p4v"), line  # 비고정 왼쪽
        # 무핀: 구분자·핀 글리프 없음.
        app.tabbar.tabs = [
            {"index": 0, "name": "build", "active": True},
            {"index": 1, "name": "logs", "active": False}]
        app.tabbar._entries_sig = None
        line2 = "".join(s.text for s in app.tabbar.render_line(0))
        assert "‖" not in line2 and "*" not in line2, line2
    await _with_app(body)


async def test_confirm_kill_pinned_prompts():
    """항목7: 활성 탭이 고정이면 닫기 확인 문구가 '고정 탭 닫기'로 바뀐다(실수 닫기
    방지). 일반 탭은 종전 흐름."""
    async def body(app, pilot, srv):
        cap = {}
        app.confirm_popup = (lambda msg, action=None, title=None, **kw:
                             cap.update(msg=msg, title=title))
        app._active_window_name = lambda: "p4v"
        # 활성 고정 탭(2개 — last 아님, 원격 아님).
        app.tabbar.tabs = [
            {"index": 0, "name": "a", "active": False},
            {"index": 1, "name": "p4v", "active": True, "pinned": True}]
        app.confirm_kill_tab()
        assert "p4v" in cap.get("msg", ""), cap
        assert "고정" in cap.get("title", ""), cap
        # 일반(비고정) 활성 탭은 종전 '탭 닫기' 문구.
        cap.clear()
        app.tabbar.tabs = [
            {"index": 0, "name": "a", "active": True},
            {"index": 1, "name": "b", "active": False}]
        app.confirm_kill_tab()
        assert "고정" not in cap.get("title", ""), cap
    await _with_app(body)


async def test_pin_toggle_sends_active_merged_index():
    """§12 ①: pin-toggle 은 활성 탭의 병합 index 를 명시해 보낸다(원격 탭도 더 이상
    거부 안 함 — 서버가 index 로 로컬/원격 per-link 를 가른다)."""
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        # 원격 탭이 활성 → 그 병합 index(1) 를 보낸다.
        app.status.windows = [
            {"index": 0, "name": "local", "active": False},
            {"index": 1, "name": "⇄hostA:sh", "active": True, "remote": True}]
        app._run_command("pin-toggle")
        assert ("set_pinned", {"index": 1}) in sent, sent
        # 로컬 탭이 활성 → 그 로컬 index(0).
        sent.clear()
        app.status.windows = [
            {"index": 0, "name": "local", "active": True},
            {"index": 1, "name": "⇄hostA:sh", "active": False, "remote": True}]
        app._run_command("pin-toggle")
        assert ("set_pinned", {"index": 0}) in sent, sent
    await _with_app(body)


async def test_choose_tree_marks_pinned():
    """§12 ⑤: 트리(개요) 뷰에서 고정 탭은 핀 글리프('*')로 표식된다."""
    async def body(app, pilot, srv):
        from textual.widgets import Label
        tree = {"sessions": [{"name": "s", "windows": [
            {"index": 0, "name": "win", "active": True, "panes": []},
            {"index": 1, "name": "p4v", "active": False, "pinned": True,
             "panes": []}]}]}
        app._open_choose_tree(tree)
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "* 1:p4v" in joined, joined      # 고정 탭에 핀 글리프
        assert "* 0:win" not in joined, joined   # 비고정 탭엔 없음
    await _with_app(body)


async def test_pin_toggle_keybinding_prefix_and_esc():
    """§12 ③: prefix P · ESC P 가 활성 탭 고정(핀) 토글 명령을 낸다(소문자 p 선점
    회피 — 대문자 P)."""
    from textual.events import Key

    async def body(app, pilot, srv):
        ran = []
        app._run_command = lambda c, *a, **k: ran.append(c)
        app.mode = "normal"
        app._handle_prefix(Key("P", "P"))
        assert "pin-toggle" in ran, ("prefix P", ran)
        ran.clear()
        app._last_esc_ts = 0.0
        await pilot.press("escape")
        assert app.mode == "esc"
        app._handle_esc_mode(Key("P", "P"))
        assert "pin-toggle" in ran and app.mode == "normal", ("esc P", ran)
    await _with_app(body)


async def test_drag_cross_zone_toggles_pin():
    """§12 ②: 끌어온 탭을 반대 구역(고정↔비고정)의 탭 위에 놓으면 그 탭의 고정 상태가
    토글된다(같은 구역 재정렬은 종전대로 move_tab)."""
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda a, **k: sent.append((a, k))
        app._drag_split = None
        app.tabbar.tabs = [
            {"index": 0, "name": "build", "active": True},
            {"index": 1, "name": "logs", "active": False},
            {"index": 2, "name": "p4v", "active": False, "pinned": True}]
        app.tabbar._entries_sig = None
        app.tabbar.render_line(0)               # _zones 채우기

        def tab_x(idx):
            z = next(z for z in app.tabbar._zones
                     if z[2] == "tab" and z[3] == idx)
            return (z[0] + z[1]) // 2
        # 비고정 탭0 을 고정 탭2 위로 드롭 → 탭0 고정 토글(=True)
        app.tabbar._drag = 0
        app.tabbar.on_mouse_up(_FakeMouse(tab_x(2), 0, button=1))
        assert ("set_pinned", {"index": 0, "value": True}) in sent, sent
        assert not any(a == "move_tab" for a, _ in sent), sent
        # 같은(비고정) 구역 재정렬은 종전대로 move_tab.
        sent.clear()
        app.tabbar._drag = 0
        app.tabbar.on_mouse_up(_FakeMouse(tab_x(1), 0, button=1))
        assert ("move_tab", {"index": 0, "to": 1}) in sent, sent
    await _with_app(body)


async def test_cmd_mode_badge_no_hangul_leak_in_en():
    """en 로케일에서 명령 모드(esc :) 상태줄 CMD 배지가 한글로 새지 않는다.

    과거 clientwidgets.StatusBar.render_line 의 cmd_mode 배지가 한글 리터럴
    하드코딩이라 카탈로그만 보는 test_en_catalog_has_no_hangul_leak 가 못 잡았다.
    렌더 출력(Segment 텍스트)에 한글 코드포인트가 없음을 직접 단언해 소스
    하드코딩 누출까지 회귀로 잡는다(선례 _warn_badge 렌더 단언)."""
    import re
    hangul = re.compile(r"[가-힣]")

    async def body(app, pilot, srv):
        app.status.cmd_mode = True
        app.status.refresh()
        await pilot.pause(0.1)
        stxt = "".join(s.text for s in app.status.render_line(0))
        assert "CMD(" in stxt, f"CMD 배지가 렌더돼야: {stxt!r}"
        assert not hangul.search(stxt), f"en 모드 상태줄 한글 누출: {stxt!r}"
    await _with_app(body, cfg={"lang": "en"})


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


async def test_calendar_month_navigation():
    """달력이 활성 패널에 떠 있을 때 ←/→ 로 이전·다음 달, ↑/↓ 로 연 이동, Home 으로
    이번 달 복귀. 닫으면 오프셋 상태도 사라진다. 키는 패널로 새지 않고 소비된다."""
    async def body(app, pilot, srv):
        active = app.layout["active"]
        app.set_calendar(active, True)
        await pilot.pause(0.1)
        assert app.calendar_offset[active] == 0, "달력은 이번 달(0)에서 시작"
        await pilot.press("right")
        await pilot.pause(0.05)
        assert app.calendar_offset[active] == 1, "→ 다음 달"
        await pilot.press("left", "left")
        await pilot.pause(0.05)
        assert app.calendar_offset[active] == -1, "← 이전 달"
        await pilot.press("down")              # 다음 해(+12)
        await pilot.pause(0.05)
        assert app.calendar_offset[active] == 11
        await pilot.press("up", "up")          # 이전 해 두 번(-24)
        await pilot.pause(0.05)
        assert app.calendar_offset[active] == -13
        await pilot.press("home")              # 이번 달로 복귀
        await pilot.pause(0.05)
        assert app.calendar_offset[active] == 0
        # 화면에 옮긴 달 제목이 반영되는지(다음 달)
        await pilot.press("right")
        await pilot.pause(0.05)
        from datetime import datetime
        now = datetime.now()
        m0 = now.year * 12 + (now.month - 1) + 1   # 이번 달 +1 (render 와 동일 계산)
        yr, mo = m0 // 12, m0 % 12 + 1
        flat = "".join(app.view._cells[y][x][0] or ""
                       for y in range(len(app.view._cells))
                       for x in range(len(app.view._cells[0])))
        assert f"{yr}-{mo:02d}" in flat, "다음 달 제목 렌더"
        # 달력을 닫으면 오프셋 항목도 사라진다
        app.set_calendar(active, False)
        await pilot.pause(0.05)
        assert active not in app.calendar_offset
    await _with_app(body, size=(44, 16))


async def test_calendar_nav_click_zones():
    """달력 제목 ‹/› 화살표를 마우스로 클릭해 이전·다음 달로 넘긴다. 화살표 클릭은
    '패널 클릭=닫기' 보다 먼저 가로채지므로 달력이 닫히지 않고, 본문(화살표 밖)을
    클릭하면 기존처럼 닫힌다."""
    async def body(app, pilot, srv):
        active = app.layout["active"]
        app.set_calendar(active, True)
        await pilot.pause(0.1)
        assert app.calendar_offset[active] == 0
        zones = app._calendar_nav_zones.get(active)
        assert zones and len(zones) == 2, "‹/› 클릭존 기록"
        # › (delta +1) 클릭 → 다음 달, 달력은 유지
        x0, x1, y, _ = next(z for z in zones if z[3] == 1)
        app.view.on_mouse_down(_FakeMouse((x0 + x1) // 2, y, button=1))
        await pilot.pause(0.05)
        assert app.calendar_offset[active] == 1, "› 클릭 → 다음 달"
        assert active in app.calendar_panes, "화살표 클릭으로 닫히지 않음"
        # ‹ (delta -1) 클릭 → 이전 달(렌더 후 갱신된 zone 을 다시 읽는다)
        x0, x1, y, _ = next(z for z in app._calendar_nav_zones[active]
                            if z[3] == -1)
        app.view.on_mouse_down(_FakeMouse((x0 + x1) // 2, y, button=1))
        await pilot.pause(0.05)
        assert app.calendar_offset[active] == 0, "‹ 클릭 → 이전 달"
        # 달력 본문(화살표 아닌 곳, 좌하단) 클릭은 여전히 닫는다
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        app.view.on_mouse_down(
            _FakeMouse(ap["x"] + 1, ap["y"] + ap["h"] - 1, button=1))
        await pilot.pause(0.05)
        assert active not in app.calendar_panes, "본문 클릭 → 닫힘"
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
    """IME preedit 동기화(docs/internal/IME_PREEDIT_CURSOR_SCENARIO.md): _composite 가 활성 패널
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


async def test_root_bindings_dispatch_and_runtime():
    """§2.5 root table: `bind -n` 키는 prefix 없이 노멀 모드에서 바로 명령을 실행하고
    그 키는 패널로 전달되지 않는다. 런타임 bind-key -n / unbind-key -n / -a 정리."""
    async def body(app, pilot, srv):
        app.root_bindings = {}
        app._run_command("bind-key -n f5 new-window")
        assert app.root_bindings["f5"] == "new-window"
        ran, sent = [], []
        orig = app._run_command
        app._run_command = lambda line: ran.append(line)
        app.send_input = lambda data: sent.append(data)
        await pilot.press("f5")                  # root 바인딩 → 명령 실행
        assert ran == ["new-window"], ran
        assert sent == [], "root 바인딩 키는 패널로 전달 안 함"
        await pilot.press("q")                   # 비바인딩 키 → 평소대로 패스스루
        assert ran == ["new-window"] and sent, (ran, sent)
        app._run_command = orig
        # unbind-key -n 으로 root 만 해제, -a 는 양 테이블 모두 비움.
        app._run_command("unbind-key -n f5")
        assert "f5" not in app.root_bindings
        app._run_command("bind-key -n f6 next-tab")
        app._run_command("bind-key z kill-pane")
        app._run_command("unbind-key -a")
        assert app.root_bindings == {} and app.bindings == {}
    await _with_app(body)


async def test_infoscreen_arrows_navigate_not_close():
    # #7: InfoScreen 에서 방향키는 팝업을 닫지 않고 항목을 내비게이션한다
    # (이전엔 아무 키나 닫혀 방향키도 즉시 닫혔다). 긴 줄은 잘리지 않고 여러
    # 줄로 줄바꿈되며, 방향키 외 키는 기존대로 닫는다.
    async def body(app, pilot, srv):
        from textual.widgets import Label, ListView
        from pytmuxlib.clientscreens import InfoScreen
        long_line = "이것은 " + "아주 " * 40 + "긴 줄입니다"
        app.push_screen(InfoScreen(["p1", long_line, "p3"]))
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
        # 긴 줄이 잘리지 않고 보존(줄바꿈 표시)
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "긴 줄입니다" in joined, joined
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


async def test_infoscreen_down_jumps_over_divider():
    """§10-A #8: InfoScreen — 마지막 항목에서 ↓ 한 번에 구분선을 건너뛰어 footer 로
    점프한다(구분선/빈 줄은 nav 에서 skip)."""
    async def body(app, pilot, srv):
        from textual.widgets import Label, ListView
        from pytmuxlib.clientscreens import InfoScreen
        app.push_screen(InfoScreen(["p1", "p2", "─" * 24, "  [r] footer"]))
        # InfoScreen 이 top 이 되고 **그 안의 ListView 가 mount** 될 때까지 대기
        # (push 직후엔 자식이 아직 안 그려져 query_one 이 실패할 수 있다 — CI 플레이크).
        await wait_until(pilot, lambda: app.screen_stack[-1].query_one(ListView))
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoScreen"
        lv = scr.query_one(ListView)
        texts = [" ".join(str(l.render()) for l in it.query(Label))
                 for it in lv.children]
        assert any("─" in t for t in texts), ("구분선 표시", texts)
        ridx = next(i for i, t in enumerate(texts) if "[r]" in t)
        p2idx = next(i for i, t in enumerate(texts) if "p2" in t)
        # 마지막 항목(p2) 선택 후 ↓ → 구분선 건너뛰고 footer 로 점프
        lv.index = p2idx
        scr.on_key(Key(key="down", character=None))
        await wait_until(pilot, lambda: lv.index == ridx)
        assert lv.index == ridx, (lv.index, ridx, texts)
        # ↑ → 다시 p2 로(구분선 건너뜀)
        scr.on_key(Key(key="up", character=None))
        await wait_until(pilot, lambda: lv.index == p2idx)
        assert lv.index == p2idx, (lv.index, p2idx, texts)
    await _with_app(body)


async def test_remote_attach_preserves_backslash_host_and_notice():
    """§1.7: ① `:remote-attach NATGAMES\\user@host` 의 host 는 shlex 토큰이 아니라
    원시 잔여 문자열 — 도메인 계정 백슬래시가 보존된다(사용자 보고: 백슬래시가
    삼켜져 엉뚱한 host 로 ssh). ② 서버 notice 메시지는 상태줄에 표시된다."""
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("remote-attach NATGAMES\\woojinkim@office1")
        assert sent == [("remote_attach",
                         {"host": "NATGAMES\\woojinkim@office1"})], sent
        # notice → display_message(상태줄)
        shown = []
        app.display_message = lambda m, *a, **k: shown.append(m)
        app._dispatch({"t": "notice", "text": "remote-attach x 실패 — 이유"})
        assert shown == ["remote-attach x 실패 — 이유"], shown
        # 인자 없으면 사용법 안내(전송 없음)
        sent.clear()
        app._run_command("remote-attach")
        assert sent == [], sent
    await _with_app(body)


async def test_server_notice_translates_to_locale():
    """서버발 원격 notice 는 로케일을 모르는 서버가 key(rnotice.*)+kw(+실패 원인
    detail)만 싣고, 클라가 자기 로케일로 번역한다(사용자 보고 2026-06-20: lang en
    인데 '원격 탭 병합됨'·'핸드셰이크 실패'가 한국어로 남던 누출). text 는 구서버/
    플러그인용 한국어 폴백 — key 가 있으면 무시되고 번역이 우선한다."""
    from pytmuxlib import i18n
    async def body(app, pilot, srv):
        shown = []
        app.display_message = lambda m, *a, **k: shown.append(m)
        # 서버가 만드는 것과 동형의 notice(병합 성공 + 핸드셰이크 실패 detail).
        merged = {"t": "notice", "key": "rnotice.attach_merged",
                  "kw": {"target": "office1"},
                  "text": "remote-attach office1: 원격 탭 병합됨"}
        fail = {"t": "notice", "key": "rnotice.attach_fail",
                "kw": {"target": "office1", "why": "(ko)"},
                "detail": {"key": "rerr.handshake_perm", "text": "(ko)",
                           "kw": {"detail": "Permission denied (publickey)"}},
                "text": "remote-attach office1 실패 — (ko)"}
        try:
            i18n.set_locale("en")
            app._dispatch(dict(merged)); app._dispatch(dict(fail))
            assert shown[-2] == "remote-attach office1: remote tab merged", shown
            assert shown[-1].startswith(
                "remote-attach office1 failed — stdio-proxy handshake failed: "
                "Permission denied (publickey) — no key configured"), shown[-1]
            i18n.set_locale("ko")
            app._dispatch(dict(merged))
            assert shown[-1] == "remote-attach office1: 원격 탭 병합됨", shown[-1]
            # key 없는 옛 notice 는 text 그대로(하위호환).
            app._dispatch({"t": "notice", "text": "plain"})
            assert shown[-1] == "plain", shown[-1]
        finally:
            i18n.set_locale("ko")
    await _with_app(body)


async def test_dismissable_notice_click_and_enter_close():
    """핸드셰이크 실패처럼 secs/dismissable 를 실은 notice 는 ① 유지 시간을 따르고
    수동 닫기 가능 플래그가 서고, ② 상태줄 클릭/터치로 즉시 닫히며, ③ ESC 모드
    하단 포커스 'msg' 위에서 Enter 로도 닫힌다(사용자 보고 2026-06-16)."""
    async def body(app, pilot, srv):
        secs_seen = []
        orig = app.set_timer
        app.set_timer = lambda s, cb: secs_seen.append(s) or orig(s, cb)
        app._dispatch({"t": "notice", "text": "remote-attach x 실패 — 이유",
                       "secs": 3.0, "dismissable": True})
        assert app.status.message == "remote-attach x 실패 — 이유"
        assert app._msg_dismissable is True
        assert 3.0 in secs_seen, secs_seen
        # ② 상태줄 맨 아래 줄 클릭 → 즉시 닫힘
        from types import SimpleNamespace
        sb = app.status
        app.mouse_enabled = True
        ev = SimpleNamespace(x=3, y=sb.size.height - 1, stop=lambda: None)
        sb.on_mouse_down(ev)
        assert sb.message is None and app._msg_dismissable is False
        # ③ ESC 모드: 메시지가 떠 있으면 포커스 대상은 'msg' 하나, Enter 로 닫힘
        app._dispatch({"t": "notice", "text": "또 실패",
                       "secs": 3.0, "dismissable": True})
        assert app._status_buttons() == ["msg"], app._status_buttons()
        app._set_status_focus("msg")
        assert sb.focus_btn == "msg"
        app._handle_status_focus(Key(key="enter", character=None))
        assert sb.message is None and sb.focus_btn is None
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
    # `model` 명령(별칭 model-config/claude-model)이 **토큰 사용량 팝업의 [한도] 탭**
    # (모델/컨텍스트 섹션)을 연다(2026-06-22 — 독립 모달 ModelCtxScreen 대신 통합).
    # 적용 시 활성 패널에 '/model <이름> [컨텍스트]' + Enter 를 주입한다.
    async def body(app, pilot, srv):
        keys, cmds = [], []
        app.send_input = lambda b: keys.append(b)
        # send_cmd 를 스텁해 서버 왕복(token_log 회신→화면 push)을 막고, 라우팅
        # 의도(limit 모드 토큰 로그 요청)만 확인한다(레이스 없이).
        app.send_cmd = lambda c, **kw: cmds.append(c)
        app._run_command("model")
        assert getattr(app, "_token_log_initial", None) == "limit", \
            "model 명령은 한도 탭(limit)으로 토큰 팝업을 연다"
        assert getattr(app, "_want_token_log", False)
        assert "request_token_log" in cmds, cmds
        # 적용 경로(한도 탭 Enter → _mc_apply → _apply_model_config)는 그대로 주입.
        app._apply_model_config(("opus", "default"))
        assert keys and keys[-1] == b"/model opus\r", keys
        app._apply_model_config(("opus-4.8", "1m"))     # 1M 컨텍스트는 토큰이 덧붙음
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
        app.open_token_log = lambda initial=None: opened.append(initial)
        app._handle_esc_mode(Key(key="enter", character=None))  # 실행
        # 마우스 클릭과 동일하게 시간(hour) 뷰로 연다(사용자 요청 2026-06-18).
        assert opened == ["hour"] and app._status_focus is None
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


async def test_bar_floating_staircase_segment():
    """떠 있는 막대(clientutil.bar_floating): [start,end] 구간만 채워 계단식 누적을
    표현. 앞쪽은 공백(시작 칸 내림), 끝은 1/8 칸 정밀도. 빈 폭/vmax 0 은 빈 문자열."""
    from pytmuxlib.clientutil import bar_floating
    assert bar_floating(0, 50, 100, 0) == ""        # cells<=0
    assert bar_floating(0, 50, 0, 8) == ""          # vmax<=0
    # start=0 이면 bar() 와 동치(처음부터 채움)
    assert bar_floating(0, 100, 100, 8) == "█" * 8
    # 50→100: 앞 4칸 공백 + 뒤 4칸 채움(떠 있는 막대)
    assert bar_floating(50, 100, 100, 8) == "    " + "█" * 4
    # 25→50: 시작칸 2(내림) 공백 + 2칸 채움
    assert bar_floating(25, 50, 100, 8) == "  " + "██"
    # 증분 없음(end<=start)은 선행 공백만(막대 없음 — 숫자가 값을 전함)
    assert bar_floating(50, 50, 100, 8) == "    "
    # 선행 공백 + 채움 칸수 합은 폭을 넘지 않는다
    s = bar_floating(60, 95, 100, 8)
    assert len(s) <= 8 and s.startswith(" ") and "█" in s


async def test_bar_floating_segments_lead_and_fill():
    """bar_floating_segments: 떠 있는 막대를 (선행칸수 lead, 채움문자열 fill) 로 분해 —
    호출부가 선행 [0,start) 을 연한 색으로 칠하려고. bar_floating 과 같은 칸 규약."""
    from pytmuxlib.clientutil import bar_floating, bar_floating_segments
    assert bar_floating_segments(0, 50, 100, 0) == (0, "")     # cells<=0
    assert bar_floating_segments(0, 50, 0, 8) == (0, "")       # vmax<=0
    # start=0 → 선행 없음, 처음부터 채움
    assert bar_floating_segments(0, 100, 100, 8) == (0, "█" * 8)
    # 50→100: 선행 4칸 + 채움 4칸
    assert bar_floating_segments(50, 100, 100, 8) == (4, "█" * 4)
    # 증분 없음(end<=start): 선행칸만, 채움 없음
    assert bar_floating_segments(50, 50, 100, 8) == (4, "")
    # bar_floating 은 lead 를 공백으로 합쳐 종전 동작 유지(회귀 가드)
    for start, end in ((0, 100), (50, 100), (25, 50), (50, 50), (60, 95)):
        lead, fill = bar_floating_segments(start, end, 100, 8)
        assert " " * lead + fill == bar_floating(start, end, 100, 8)


async def test_hourly_spans_staircase_and_reset():
    """TokenLogScreen._hourly_spans: 시각별 누적 5h%({hour:max})를 계단식 구간
    {hour:(start,end)} 로. 같은 5h 창은 직전 누적%에서 이어 시작, 창 리셋(누적% 하락
    또는 ≥5h 공백)이면 start=0 으로 되돌린다(요청 2026-06-17)."""
    import importlib
    TokenLogScreen = importlib.import_module(
        "pytmuxlib.plugins.claude-code.screens").TokenLogScreen
    f = TokenLogScreen._hourly_spans
    data = {
        "2026-06-17 09:00": 10,   # 첫 표본 → 0 부터
        "2026-06-17 10:00": 25,   # 1h 뒤·증가 → 이어서
        "2026-06-17 11:00": 40,   # 1h 뒤·증가 → 이어서
        "2026-06-17 16:00": 15,   # 5h 공백 → 리셋(0 부터)
        "2026-06-17 17:00": 30,   # 1h 뒤·증가 → 이어서
    }
    spans = f(data)
    assert spans["2026-06-17 09:00"] == (0, 10)
    assert spans["2026-06-17 10:00"] == (10, 25)
    assert spans["2026-06-17 11:00"] == (25, 40)
    assert spans["2026-06-17 16:00"] == (0, 15)    # ≥5h 공백 리셋
    assert spans["2026-06-17 17:00"] == (15, 30)
    # 공백 없이 누적%가 하락해도(=창 리셋) 0 부터 다시
    drop = f({"2026-06-17 12:00": 40, "2026-06-17 13:00": 5})
    assert drop["2026-06-17 13:00"] == (0, 5)
    assert f({}) == {}


async def test_inpane_usage_no_auto_popup_but_manual_still_opens():
    # §3.9(2026-06-17): 인패널 /usage 자동 팝업 제거. usage_shown_seq 가 증가하는
    # status 가 와도 전용 화면이 **자동으로 뜨지 않는다**. 단, 수동 usage-panel
    # 명령 경로(open_usage_panel)는 그대로 동작한다.
    async def body(app, pilot, srv):
        u = {"session": {"pct": 10, "reset": "5am (Asia/Seoul)"},
             "week_all": {"pct": 14, "reset": "Jun 13 at 3am (Asia/Seoul)"}}
        # 자동 팝업 시퀀스 필드/경로는 제거됐다.
        assert not hasattr(app, "_last_usage_shown_seq")
        app._dispatch({"t": "status", "usage_limits": u, "usage_shown_seq": 6})
        await pilot.pause(0.1)
        assert len(app.screen_stack) == 1, "자동 팝업이 뜨면 안 됨(§3.9 제거)"
        # 수동 명령 경로는 유지 — open_usage_panel 이 InfoScreen 을 연다.
        app.open_usage_panel()
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


async def test_redraw_command_and_prefix_r_emit_request_redraw():
    # §2.12: redraw/refresh/refresh-client 명령과 prefix r 가 모두 request_redraw 를
    # 서버에 보낸다(화면 전체 강제 재그리기).
    from textual.events import Key
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append(action)
        for cmd in ("redraw", "refresh", "refresh-client"):
            app._run_command(cmd)
        assert sent == ["request_redraw", "request_redraw", "request_redraw"], sent
        sent.clear()
        app.mode = "prefix"
        app._handle_prefix(Key("r", "r"))     # prefix r → redraw
        assert sent == ["request_redraw"], sent
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
    # #10: REC 버튼이 통합 팝업(InfoTabsScreen)을 연다. token-usage→token-log 통합
    # (2026-06-12)으로 토큰 탭은 빠지고 REC(0)·서버(1) 두 탭 — ←→ 로 서버 탭 이동.
    async def body(app, pilot, srv):
        from textual.widgets import Label
        app.status.capture = True
        app.status.render_line(0)
        assert app.status._rec_zone is not None, "REC 클릭존 등록"
        # REC 버튼 배선: status_tabs 트리 요청 + 캡처 탭(0=왼쪽) + 캡처 줄 준비
        app.show_capture_info("/tmp/x.sock.capture/pane-1.log", 2048)
        assert app._tree_purpose == "status_tabs" and app._status_tab_initial == 0
        app._want_tree = False     # 서버의 실제 트리 응답이 또 팝업을 띄우지 않게(결정성)
        app._open_status_tabs({"sessions": []})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoTabsScreen"
        names = [t[0] for t in scr._tabs]
        assert names == ["출력 캡처(REC)", "서버"], \
            f"토큰 탭은 token-log 로 통합돼 빠져야: {names}"
        # 초기 탭 = 캡처(0=왼쪽): 캡처 정보 보임
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "pane-1.log" in joined and "2,048" in joined, joined
        # → 로 서버 탭(1=오른쪽) → 호스트/소켓 정보 보임.
        # (←→ 순환에 닫기[x] 가 포함되므로 탭0→탭1 은 'right' 다.)
        await pilot.press("right")
        await pilot.pause(0.1)
        j2 = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "호스트:" in j2 and "소켓:" in j2, j2
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


async def test_info_tabs_notebook_connector():
    """탭 줄과 본문 사이 노트북 연결선(_ItTabConnector): accent ─ 가로선을 긋되 활성
    탭 아래만 ▀(상단 반블록)로 덮어 활성 탭이 본문으로 이어지게 한다(요청 2026-06-20).
    탭을 바꾸면 ▀ 다리가 새 활성 탭 아래로 옮겨간다."""
    async def body(app, pilot, srv):
        app._status_cap_lines = ["파일: /tmp/x/pane-1.log"]
        app._status_tab_initial = 0
        app._open_status_tabs({"sessions": []})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoTabsScreen"
        conn = scr.query_one("#itconn")

        def bridge_range():
            # 연결선 한 줄을 그려 ▀(다리) 구간의 [시작, 끝) x 를 구한다.
            strip = conn.render_line(0)
            x = lo = hi = 0
            for seg in strip:
                for ch in seg.text:
                    if ch == "▀":
                        if lo == hi:
                            lo = x
                        hi = x + 1
                    x += 1
            return lo, hi

        lo0, hi0 = bridge_range()
        assert hi0 > lo0, "활성 탭(0) 아래 ▀ 다리가 그려져야"
        # → 로 서버 탭(1)으로 이동 → 다리가 오른쪽으로 옮겨간다.
        await pilot.press("right")
        await pilot.pause(0.1)
        lo1, hi1 = bridge_range()
        assert hi1 > lo1, "활성 탭(1) 아래에도 ▀ 다리"
        assert lo1 > lo0, f"다리가 두 번째 탭 아래(오른쪽)로 이동해야: {lo0}->{lo1}"
    await _with_app(body)


async def test_status_tabs_has_server_tab():
    """§10-A #12: 통합 상태 팝업에 '서버' 탭(3번째)이 생기고 호스트·소켓 정보를 보인다."""
    async def body(app, pilot, srv):
        from textual.widgets import Label
        app._status_cap_lines = ["파일: /tmp/x/pane-1.log"]
        app._status_tab_initial = 2          # host 클릭 의도(서버 탭)
        app._open_status_tabs({"sessions": []})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoTabsScreen"
        names = [t[0] for t in scr._tabs]
        assert names == ["출력 캡처(REC)", "서버"], names
        assert scr._ti == 1, "initial=2 가 마지막 탭=서버(인덱스 1)로 클램프"
        joined = " ".join(str(lbl.render()) for lbl in scr.query(Label))
        assert "호스트:" in joined and "소켓:" in joined, joined
    await _with_app(body)


async def test_server_tab_rtt_graph_lines():
    """서버 정보 탭이 최근 60분 RTT 표본이 있으면 세로 막대 그래프 줄을 포함한다.
    표본이 없으면 그래프 줄이 안 뜨고(이전 거동 보존), 있으면 제목·축·통계줄이 나온다."""
    async def body(app, pilot, srv):
        import time as _t
        # 표본이 없으면 그래프 줄 없음(회귀: 기존 서버 탭 거동 보존)
        assert app._rtt_graph_lines() is None
        # 60분 창에 걸친 합성 표본을 직접 주입(핑 대기 없이 결정적으로)
        now = _t.monotonic()
        app._net_rtt_hist = [(now - i * 30.0, 0.001 + (0.5 if i == 3 else 0))
                             for i in range(120)]   # 매 30초, 1개 스파이크
        g = app._rtt_graph_lines()
        assert g and any("RTT" in ln for ln in g), g
        assert any("┴" in ln for ln in g), "x축 줄"           # 축
        assert any("peak" in ln for ln in g), "통계줄"
        # 서버 정보 줄에 그래프가 합쳐진다
        lines = app._server_info_lines()
        assert any("┴" in ln for ln in lines), lines
    await _with_app(body)


async def test_rtt_graph_autoscales_to_peak_low_rtt_visible():
    """세로 스케일이 임계(400ms) 고정이 아니라 관측 peak 에 자동으로 맞춰져, ~1ms 같은
    낮은 RTT 도 막대로 보인다(요청 ②). 임계가 peak 위(정상)면 기준선은 생략한다."""
    async def body(app, pilot, srv):
        import time as _t
        now = _t.monotonic()
        app.net_rtt_threshold = 0.4                  # 400ms 임계(정상상태 — peak 보다 큼)
        # 모두 ~1ms, peak 14ms — 임계 고정이면 전부 0 높이로 뭉개졌을 데이터.
        app._net_rtt_hist = [(now - i * 5.0, 0.001 + (0.013 if i == 2 else 0))
                             for i in range(400)]
        g = app._rtt_graph_lines()
        # 세로 막대 글리프가 실제로 그려져야 한다(0 으로 뭉개지지 않음).
        assert any(any(ch in ln for ch in "▁▂▃▄▅▆▇█") for ln in g), g
        # 상단 축 라벨 = peak(≈14ms), 임계(400)가 아니다.
        axis_top = [ln for ln in g if "┤" in ln][0]
        assert "14" in axis_top and "400" not in axis_top, axis_top
        # 임계가 스케일 위라 점선(┄) 기준선은 안 그려진다.
        assert not any("┄" in ln for ln in g), g
    await _with_app(body)


async def test_rtt_graph_threshold_line_when_in_range():
    """임계가 peak 이하(저하 영역)면 그 높이에 점선(┄) 기준선과 라벨을 따로 그린다(요청 ②)."""
    async def body(app, pilot, srv):
        import time as _t
        now = _t.monotonic()
        app.net_rtt_threshold = 0.2                  # 200ms 임계
        app._net_rtt_hist = [(now - i * 5.0, 0.05 + (0.45 if i == 2 else 0))
                             for i in range(400)]     # peak 500ms > 임계 → 기준선 안에 듦
        g = app._rtt_graph_lines()
        thr_lines = [ln for ln in g if "┄" in ln]
        assert thr_lines, ("임계 기준선(점선)", g)
        assert any("200" in ln for ln in thr_lines), ("임계 라벨", thr_lines)
    await _with_app(body)


async def test_rtt_graph_marks_no_data_gaps():
    """측정이 없던 칸(클라 미가동/끊김)은 공백이 아니라 '·' 마커로 그려, '측정 없음' 을
    '0 에 가까운 측정' 과 구분하고 범례를 덧붙인다(요청 ①)."""
    async def body(app, pilot, srv):
        import time as _t
        now = _t.monotonic()
        # 최근 ~10분만 표본, 그 앞 50분은 빈 구간(클라가 안 떠 있던 때).
        app._net_rtt_hist = [(now - i * 2.0, 0.002) for i in range(300)]
        g = app._rtt_graph_lines()
        assert any("·" in ln for ln in g), ("측정 없음 마커", g)
        assert any("측정 없음" in ln or "no data" in ln for ln in g), ("범례", g)
        # 표본이 전 구간을 꽉 채우면 마커/범례는 안 뜬다(공백 칸 없음).
        app._net_rtt_hist = [(now - i * 5.0, 0.002) for i in range(800)]
        g2 = app._rtt_graph_lines()
        assert not any("측정 없음" in ln or "no data" in ln for ln in g2), g2
    await _with_app(body)


async def test_rtt_hist_persists_across_restart():
    """60분 RTT 이력이 디스크에 영속돼 (서버/클라) 재시작 후 그래프로 복원된다(요청).
    창 밖(60분 초과) 표본은 버리고, 복원 표본은 현재 시각 기준 창 안에 들어온다.
    데이터가 없던 구간(서버 꺼짐)은 표본이 없어 그래프에서 공백으로 남는다(건너뜀)."""
    async def body(app, pilot, srv):
        import time as _t
        now = _t.monotonic()
        # 창 안 2개(50분·10분 전) + 창 밖 1개(70분 전 — 복원 시 버려져야 함)
        app._net_rtt_hist = [(now - 50 * 60, 0.01), (now - 10 * 60, 0.02),
                             (now - 70 * 60, 0.5)]
        app._save_rtt_hist()
        # 같은 sock 으로 새 앱 생성 = 재시작 시뮬레이션 → __init__ 에서 _load_rtt_hist.
        app2 = make_app(app.sock_path)
        hist = app2._net_rtt_hist
        assert len(hist) == 2, hist                    # 창 밖 표본 제외
        assert sorted(r for _, r in hist) == [0.01, 0.02]
        now2 = _t.monotonic()
        for ts, _ in hist:                             # 복원 표본은 창 안(age≥0)
            assert 0 <= now2 - ts <= app2._RTT_WINDOW
        # 복원된 이력으로 그래프가 실제로 그려진다(재시작 후 유지).
        assert app2._rtt_graph_lines() is not None
    await _with_app(body)


async def test_rtt_hist_persist_off_skips_file(tmp_path=None):
    """net_rtt_persist=False 면 이력 파일을 쓰지도 읽지도 않는다(롤백 스위치)."""
    async def body(app, pilot, srv):
        import os as _os
        import time as _t
        now = _t.monotonic()
        app._net_rtt_hist = [(now - 60, 0.01)]
        app._save_rtt_hist()
        path = app._rtt_hist_path()
        assert path and _os.path.exists(path)
        # off 로 만든 새 앱은 파일을 무시하고 빈 이력으로 시작한다.
        app2 = make_app(app.sock_path, {"net_rtt_persist": False})
        assert app2._rtt_hist_path() is None
        assert app2._net_rtt_hist == []
    await _with_app(body)


async def test_rtt_graph_width_fits_narrow_popup():
    """좁은 팝업에서 RTT 그래프 줄이 박스 안쪽 폭을 넘지 않아 접히지 않는다(요청).
    그래프 가로 칸은 화면(=박스 92%·최대 100) 폭에서 테두리·축 프리픽스를 빼 맞춘다."""
    async def body(app, pilot, srv):
        import time as _t
        from pytmuxlib.clientutil import _char_cells   # 표시 폭(CJK 2칸)
        now = _t.monotonic()
        app._net_rtt_hist = [(now - i * 20.0, 0.002) for i in range(150)]
        inner = min(100, int(app.size.width * 0.92)) - 4   # 박스 안쪽(테두리·패딩 제외)
        # 막대/축 줄(┤┴ 포함)만 검사 — 통계 문장줄은 자연 텍스트라 접혀도 무방하다.
        bars = [ln for ln in app._rtt_graph_lines() if "┤" in ln or "┴" in ln]
        assert bars, "그래프 막대/축 줄"
        for ln in bars:
            w = sum(_char_cells(c) for c in ln)
            assert w <= inner, (w, inner, ln)
    await _with_app(body, size=(40, 30))


async def test_info_tabs_notebook_shape_and_constant_height():
    """플랫 탭(외곽선 없음) 모양 + 탭 전환 시 팝업 높이 불변(요청).
    - 활성 탭 라벨은 외곽선(╭╮│) 없이 이름만 배경 반전으로 강조된다.
    - 본문(ListView) 항목 수가 탭을 오가도 동일(짧은 탭은 빈 줄로 패딩)."""
    async def body(app, pilot, srv):
        from textual.widgets import Label, ListView
        # 길이 차가 큰 두 탭(짧은 REC vs 긴 서버 + RTT 그래프)
        import time as _t
        now = _t.monotonic()
        app._net_rtt_hist = [(now - i * 20.0, 0.002) for i in range(150)]
        app._status_cap_lines = ["파일: /tmp/x/pane-1.log"]
        app._status_tab_initial = 0
        app._open_status_tabs({"sessions": []})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoTabsScreen"
        # 활성 탭(0)의 라벨에 외곽선 박스 문자(╭╮│─)가 없어야 한다(플랫 탭).
        t0 = str(scr.query_one("#ittab_0", Label).render())
        assert not any(ch in t0 for ch in "╭╮╯╰│─"), t0
        lv = scr.query_one(ListView)
        n_rec = len(lv.children)
        await pilot.press("right")        # 서버 탭으로 전환
        await pilot.pause(0.1)
        n_srv = len(lv.children)
        assert n_rec == n_srv, ("탭 전환 시 본문 행 수 불변", n_rec, n_srv)
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
        app.open_token_log = lambda initial=None: called.append(initial)
        y = app.status.size.height - 1
        ev = events.MouseDown(app.status, uz[0], y, 0, 0, 1, False, False, False)
        app.status.on_mouse_down(ev)
        # "N%/5h used" 세그먼트라 시간(hour) 뷰로 연다(사용자 요청 2026-06-18).
        assert called == ["hour"], called
    await _with_app(body)


async def test_status_warn_badge_click_opens_info():
    """상태줄 Claude 경고 배지(claude_warn)가 클릭존(_warn_zone)으로 등록되고, 클릭하면
    상황·할일 팝업(open_claude_warn_info)을 연다(요청)."""
    async def body(app, pilot, srv):
        from textual import events
        app.status.claude_warn = "⚠ Claude 포맷 미인식 — 추적 중단(버전 업데이트?)"
        app.status.render_line(0)
        wz = app.status._warn_zone
        assert wz is not None, "경고 배지 클릭존 등록"
        called = []
        app.open_claude_warn_info = lambda: called.append(True)
        y = app.status.size.height - 1
        cx = (wz[0] + wz[1]) // 2
        ev = events.MouseDown(app.status, cx, y, 0, 0, 1, False, False, False)
        app.status.on_mouse_down(ev)
        assert called == [True], called
    await _with_app(body)


async def test_status_warn_badge_emoji_gets_visible_space():
    """§10-E #3(2026-06-17): ⚠(2칸 렌더·wcwidth 1)와 숫자가 붙어 "⚠️10:25" 로 보이던 것을,
    상태줄 **표시에서만** ⚠ 뒤 공백을 한 칸 더 넣어 "⚠  10:25"(화면상 "⚠️ 10:25")로 띄운다.
    저장값(claude_warn)은 원문("⚠ 10:25") 그대로(파서·info 팝업·테스트 불변)."""
    async def body(app, pilot, srv):
        app.status.claude_warn = "⚠ 10:25"
        line = "".join(s.text for s in app.status.render_line(0))
        assert "⚠  10:25" in line, repr(line)        # 표시엔 공백 2칸
        assert app.status.claude_warn == "⚠ 10:25"   # 저장값은 원문(공백 1칸)
        assert app.status._warn_zone is not None
    await _with_app(body)


async def test_open_warn_info_popup_content():
    """open_claude_warn_info: 상태줄 ⚠ 경고 배지 클릭 → 통합 토큰 팝업(TokenLogScreen)의
    '경고' 탭을 열어 경고 종류별 상황·할일을 보여준다(2026-06-17 통합 — 옛 별도 InfoScreen
    대체). 포맷 미인식이면 '추적 중단'·'claude.py' 안내가, 경고 부재면 팝업 없이 안내만."""
    async def body(app, pilot, srv):
        app.status.claude_warn = "⚠ Claude 포맷 미인식 — 추적 중단(버전 업데이트?)"
        # open_token_log("warn") 은 서버 라운드트립이므로 회신을 모사해 푸시를 끌어온다
        # (다른 token-log 테스트와 동일 패턴 — _want_token_log 는 open_token_log 가 켠다).
        app.open_claude_warn_info()
        app._dispatch({"t": "token_log", "records": []})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "TokenLogScreen", scr.__class__.__name__
        assert scr._warn_mode, "⚠ 배지 클릭은 경고 탭으로 열려야"
        blob = _tok_text(scr)
        assert "상황" in blob and "할일" in blob, blob
        assert "claude.py" in blob, blob
        await pilot.press("escape")
        await pilot.pause(0.05)
        # 경고가 없으면 팝업 대신 안내 메시지(스택 불변)
        app.status.claude_warn = None
        n = len(app.screen_stack)
        app.open_claude_warn_info()
        await pilot.pause(0.05)
        assert len(app.screen_stack) == n, "경고 없으면 팝업 안 띄움"
    await _with_app(body)


async def test_status_ar_badge_click_opens_autoresume_info():
    """상태줄 AR(자동재개) 배지가 autoresume 켜졌을 때 클릭존(_ar_zone)으로 등록되고,
    클릭하면 자동 재개 켜고 끄기 팝업(open_autoresume_info)을 연다(요청)."""
    async def body(app, pilot, srv):
        from textual import events
        app.status.autoresume = True
        app.status.render_line(0)
        az = app.status._ar_zone
        assert az is not None, "AR 배지 클릭존 등록"
        called = []
        app.open_autoresume_info = lambda: called.append(True)
        y = app.status.size.height - 1
        cx = (az[0] + az[1]) // 2
        ev = events.MouseDown(app.status, cx, y, 0, 0, 1, False, False, False)
        app.status.on_mouse_down(ev)
        assert called == [True], called
    await _with_app(body)


async def test_open_autoresume_info_popup_toggles():
    """open_autoresume_info: 현재 상태를 보여 주는 InfoScreen 을 띄우고, [a] 키로
    set_autoresume 를 보내 토글한다(원격제어 팝업과 같은 hide_key 패턴)."""
    async def body(app, pilot, srv):
        app.status.autoresume = True
        sent = []
        app.send_cmd = lambda c, **kw: sent.append(c)
        app.open_autoresume_info()
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "InfoScreen", scr.__class__.__name__
        assert any("AR" in ln for ln in scr._lines), scr._lines
        await pilot.press("a")               # 토글 키 → set_autoresume + 닫힘
        await pilot.pause(0.05)
        assert "set_autoresume" in sent, sent
        assert app.screen_stack[-1] is not scr, "[a] 후 팝업 닫힘"
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


async def test_claude_icon_and_close_button():
    async def body(app, pilot, srv):
        active = app.layout["active"]
        # 탭 아이콘: busy → ◐
        app.tabbar.tabs = [{"index": 0, "name": "win",
                            "active": True, "claude": "busy"}]
        assert "◐" in "".join(app.tabbar._labels())
        # 닫기 [x] 는 활성 패널 **상단 테두리 행** 우측(2026-06-13 한 칸 위로 —
        # 콘텐츠 비가림·IME 배지와 비중첩. 테두리 없으면 콘텐츠 첫 행 폴백).
        ap = next(p for p in app.layout["panes"] if p["id"] == active)
        app._composite()
        tcz = app._tab_close_zone
        want_y = ap["box"][1] if ap.get("box") else ap["y"]
        assert tcz == (ap["x"] + ap["w"] - 3, ap["x"] + ap["w"], want_y), tcz
        assert ap.get("box") is None or want_y == ap["y"] - 1, (want_y, ap)
        xs = "".join(app.view._cells[tcz[2]][x][0] for x in range(tcz[0], tcz[1]))
        assert xs == "[x]", repr(xs)
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
        app._net_local = False                # 원격 경로(로컬은 degraded 억제)
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
        app._net_local = False                # 원격 경로(로컬은 자동 재접속 억제)
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


async def test_claude_interrupt_zone_sends_esc():
    """busy footer 의 'esc to interrupt' 문구를 좁은 클릭존(_interrupt_zone)으로 잡고,
    그 영역 클릭(interrupt_pane)이 해당 패널에 ESC(\\x1b) input 을 보낸다. 줄 전체를
    덮는 perm 존 안의 부분영역이라, 클릭 핸들러는 interrupt 를 perm 보다 먼저 가로챈다."""
    import base64
    import pytmuxlib.client as clientmod

    async def body(app, pilot, srv):
        pid = app.layout["panes"][0]["id"]
        py = app.layout["panes"][0]["y"]
        app.pane_claude = {pid: {"id": pid, "claude": "busy",
                                 "perm_mode": "default"}}
        rows = [
            [("일반 출력 줄", {})],
            [("⏵⏵ auto mode on (shift+tab to cycle) · esc to interrupt", {})],
        ]
        app.pane_content[pid] = (rows, None)
        app._composite()
        # 같은 줄(py+1)에 perm 존과 interrupt 존이 둘 다 등록된다.
        assert pid in app._perm_zone, app._perm_zone
        assert pid in app._interrupt_zone, app._interrupt_zone
        izx0, izx1, izy = app._interrupt_zone[pid]
        pzx0, pzx1, pzy = app._perm_zone[pid]
        assert izy == py + 1 == pzy
        # interrupt 존은 perm 존(줄 전체) 안의 진부분집합 — 'esc to interrupt'만 덮는다.
        assert pzx0 <= izx0 < izx1 <= pzx1
        assert izx0 > pzx0, "interrupt 존은 줄 앞쪽 'auto mode on' 보다 오른쪽"
        # 그 영역 클릭 → interrupt_pane → 해당 패널에 ESC input 전송.
        sent = []
        orig = clientmod.write_msg

        async def cap(writer, msg):
            sent.append(msg)
        clientmod.write_msg = cap
        try:
            app.interrupt_pane(pid)
            await pilot.pause(0.05)
        finally:
            clientmod.write_msg = orig
        inp = [m for m in sent if m.get("t") == "input" and m.get("pane") == pid]
        assert inp, sent
        assert base64.b64decode(inp[0]["data"]) == b"\x1b"
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


async def test_token_log_click_outside_closes():
    """토큰 사용량 팝업(TokenLogScreen) 박스(#tklogbox) 바깥(백드롭) 클릭/터치 시
    dismiss(None) 로 닫힌다. 박스 안(표 셀 등) 클릭은 닫지 않는다(InfoScreen·권한모드
    팝업과 동일한 inside-box 판정)."""
    async def body(app, pilot, srv):
        recs = [{"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
                 "account": "me@x.org", "tokens": 1500}]
        app._want_token_log = True
        app._dispatch({"t": "token_log", "records": recs})
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "TokenLogScreen", scr

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

        # 박스 안 클릭(표 셀 → … → #tklogbox) → 닫히지 않음
        ev_in = _Ev(_W("tktable", parent=_W("tklogbox", parent=_W("screen"))))
        scr.on_click(ev_in)
        await pilot.pause(0.05)
        assert app.screen_stack[-1] is scr, "박스 안 클릭은 닫지 않는다"
        # 박스 바깥(백드롭) 클릭 → 닫힘
        ev_out = _Ev(_W("backdrop", parent=None))
        scr.on_click(ev_out)
        await pilot.pause(0.05)
        assert app.screen_stack[-1] is not scr, "바깥 클릭은 팝업을 닫는다"
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
    # 컨텍스트 비율% ②5h 리밋 사용률%(2026-06-12 잔여→사용 통일). 토큰 수치(~Σ)는 직접 표시하지
    # 않는다(기록은 서버측 _log_tokens 가 유지). claude_tokens 는 받아도 표시 안 함.
    async def body(app, pilot, srv):
        app.status.claude_active = True
        app.status.claude_usage = "ctx 42%"
        app.status.claude_tokens = 45200          # 받지만 표시 안 함
        txt = "".join(s.text for s in app.status.render_line(0))
        assert "ctx 42%" in txt, repr(txt)
        # 토큰 수치/누계 기호는 표시되지 않는다
        assert "Σ" not in txt and "45,200" not in txt, repr(txt)
        # 계정 라벨은 더는 붙지 않는다(머신-로컬 표시, 2026-06-19). 5h 실측이 없으므로
        # (tok5h_pct=None) §10-F 'Unknown' 배지가 5h 자리에 들어간다(ctx 42% · ?%/5h 사용).
        app.status.claude_account = "alice"
        txt_a = "".join(s.text for s in app.status.render_line(0))
        assert "ctx 42% · ?%/5h 사용" in txt_a and "alice" not in txt_a, repr(txt_a)
        # 5h 리밋 **사용률**(실측 37% 사용 — 2026-06-12 사용자 결정: 팝업 막대
        # "N% 사용"·Claude /usage "N% used" 와 같은 방향·같은 숫자로 전 표면 통일).
        # 계정 라벨은 붙지 않는다(머신-로컬).
        app.status.usage_limits = {"session": {"pct": 37}, "account": "alice"}
        app.status.tok5h_pct = 37
        txt_m = "".join(s.text for s in app.status.render_line(0))
        assert "37%/5h 사용" in txt_m and "alice" not in txt_m, repr(txt_m)
        assert txt_m.index("ctx 42%") < txt_m.index("37%/5h"), repr(txt_m)
        app.status.usage_limits = None
        # claude_usage 가 토큰 폴백('Xk tok')이면 표시하지 않는다(토큰 수치 비표시 원칙)
        app.status.claude_usage = "12k tok"
        app.status.tok5h_pct = None
        app.status.claude_account = None
        txt3 = "".join(s.text for s in app.status.render_line(0))
        assert "tok" not in txt3 and "12k" not in txt3, repr(txt3)
    await _with_app(body)


async def test_status_no_account_label_in_footer():
    """머신-로컬 표시(2026-06-19): footer 에는 계정 이름을 더는 붙이지 않는다 —
    패널 스크랩 계정·/usage 실측 계정이 채워져 있어도 % 옆에 계정 라벨이 없다."""
    async def body(app, pilot, srv):
        app.status.claude_active = True
        app.status.claude_usage = "ctx 42%"
        app.status.tok5h_pct = 2
        app.status.claude_account = "wo…@nexongames.co.kr"
        app.status.claude_account_full = "woojinkim@nexongames.co.kr"
        app.status.usage_limits = {"session": {"pct": 2}, "account": "me@woojinkim.org"}
        txt = "".join(s.text for s in app.status.render_line(0))
        assert "2%/5h 사용" in txt, repr(txt)
        assert "nexongames" not in txt and "woojinkim.org" not in txt, repr(txt)
    await _with_app(body, size=(200, 30))


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
        # 다시 Claude 패널이 활성화되면 보존된 값이 그대로 표시된다. 5h 실측이
        # 없으므로(tok5h_pct=None) §10-F 'Unknown' 배지가 끼어든다(ctx 42% · ?%/5h 사용).
        # 계정 라벨은 더는 붙지 않는다(머신-로컬, 2026-06-19).
        app.status.claude_active = True
        txt2 = "".join(s.text for s in app.status.render_line(0))
        assert "ctx 42%" in txt2 and "?%/5h 사용" in txt2 and "alice" not in txt2, \
            repr(txt2)
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
        # week_sonnet_pct(모델=Sonnet 경로)도 같은 0~100 클램프.
        app.status.update_status({"week_sonnet_pct": 999})
        assert app.status.week_sonnet_pct == 100
        app.status.update_status({"week_sonnet_pct": 12})
        assert app.status.week_sonnet_pct == 12
        app.status.update_status({"week_sonnet_pct": None})
        assert app.status.week_sonnet_pct is None
    await _with_app(body)


async def test_status_week_sonnet_display():
    """모델=Sonnet 일 때 서버가 5h(통합) 대신 주간 Sonnet% 만 보내면(tok5h_pct=None,
    week_sonnet_pct=값) 상태줄에 'N%/주(Sonnet)' 가 보이고 '/5h' 는 안 보인다(요청
    2026-06-16). 계정 라벨은 붙지 않는다(머신-로컬 표시, 2026-06-19)."""
    async def body(app, pilot, srv):
        app.status.claude_active = True
        app.status.claude_usage = "ctx 42%"
        app.status.usage_limits = {"week_sonnet": {"pct": 12}, "account": "alice"}
        app.status.tok5h_pct = None
        app.status.week_sonnet_pct = 12
        txt = "".join(s.text for s in app.status.render_line(0))
        assert "12%/주(Sonnet)" in txt, repr(txt)
        assert "/5h" not in txt, repr(txt)
        assert "alice" not in txt, repr(txt)      # 계정 라벨 없음
    await _with_app(body, size=(120, 30))


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


async def test_prompt_candidates_paste_prefers_clipboard():
    # 'paste' 입력 → paste-clipboard 가 첫(기본 하이라이트) 후보(2026-06-16 요청).
    # paste-buffer/paste-clipboard 둘 다 전체 접두 일치(동률)라 선언 순서가 기본
    # 선택을 정한다 — paste-clipboard 를 앞에 둬 OS 클립보드 붙여넣기가 먼저 잡힌다.
    async def body(app, pilot, srv):
        await pilot.press("escape")
        await pilot.press("colon")
        scr = app.screen_stack[-1]
        inp = scr.query_one(Input)
        inp.value = "paste"
        scr._refresh_cands()
        await pilot.pause(0.05)
        names = [n for n, _ in scr._cand]
        assert names and names[0] == "paste-clipboard", names
        assert "paste-buffer" in names, names
        assert scr._sel == 0, scr._sel   # 첫 후보가 선택(하이라이트)
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


async def test_esc_nav_reaches_close_button():
    # ESC 모드 ↑(최상단 패널) → 우상단 닫기 [x] 포커스, Enter → 탭 닫기 확인(#31).
    from textual.events import Key
    async def body(app, pilot, srv):
        await pilot.press("escape")
        app.on_key(Key(key="up", character=None))          # 최상단 패널 ↑ → [x]
        assert app._close_focus is True
        killed = []
        app.confirm_kill_tab = lambda: killed.append(1)
        app.on_key(Key(key="enter", character=None))       # [x] Enter → 탭 닫기
        assert killed == [1] and app._close_focus is False
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


async def test_paste_clipboard_image_remote_tab_scp():
    """원격 탭에서 이미지 붙여넣기 — scp_to_remote 로 원격 /tmp/ 에 복사 후 원격 경로 paste.
    실패 시엔 로컬 경로로 폴백."""
    from pytmuxlib import clientclip
    _orig = (clientclip.paste, clientclip.has_image,
             clientclip.save_image, clientclip.scp_to_remote)

    async def body(app, pilot, srv):
        sent = []
        scp_calls = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        # 원격 탭 흉내: _active_remote_host() 가 "myhost" 를 반환하도록
        app._active_remote_host = lambda: "myhost"
        clientclip.paste = lambda: ""
        clientclip.has_image = lambda: True
        clientclip.save_image = lambda: "/tmp/pytmux-clip-x.png"

        # ① scp 성공 → 원격 경로 paste
        def fake_scp_ok(host, local, remote):
            scp_calls.append((host, local, remote))
            return True
        clientclip.scp_to_remote = fake_scp_ok
        app.paste_os_clipboard()
        await wait_until(pilot, lambda: sent and sent[-1][0] == "paste")
        assert sent[-1] == ("paste", {"text": "/tmp/pytmux-clip-x.png"}), sent
        assert scp_calls[-1] == ("myhost", "/tmp/pytmux-clip-x.png",
                                 "/tmp/pytmux-clip-x.png"), scp_calls

        # ② scp 실패 → 로컬 경로로 폴백
        sent.clear(); scp_calls.clear()
        def fake_scp_fail(host, local, remote):
            scp_calls.append((host, local, remote))
            return False
        clientclip.scp_to_remote = fake_scp_fail
        app.paste_os_clipboard()
        await wait_until(pilot, lambda: sent and sent[-1][0] == "paste")
        assert sent[-1] == ("paste", {"text": "/tmp/pytmux-clip-x.png"}), sent

    try:
        await _with_app(body)
    finally:
        (clientclip.paste, clientclip.has_image,
         clientclip.save_image, clientclip.scp_to_remote) = _orig


async def test_clientclip_save_image_macos_osascript_without_pngpaste():
    """맥에서 이미지 붙여넣기가 무동작이던 버그 수정(요청 2026-06-12): pngpaste(서드파티,
    기본 미설치)가 없으면 osascript 로 클립보드 PNG(«class PNGf»)를 직접 파일로 저장한다.
    which/subprocess/IS_WINDOWS 를 모킹해 ① pngpaste 부재 시 osascript 분기 선택 ② PNGf
    코어션 명령 구성 ③ 성공 경로(경로 반환)를 단언한다(실 클립보드 불필요·포터블)."""
    import os
    import subprocess as _sp
    from pytmuxlib import clientclip
    _which = clientclip.shutil.which
    _run = clientclip.subprocess.run
    _isw = clientclip.proc.IS_WINDOWS
    calls = []

    def fake_which(tool):
        return "/usr/bin/osascript" if tool == "osascript" else None  # pngpaste 없음

    def fake_run(args, **kw):
        calls.append(list(args))
        if args and args[0] == "osascript":
            # 첫 -e 라인('set p to POSIX file "<path>"')에서 경로를 떼어 비어있지 않은
            # 파일을 써, save_image 의 getsize>0 검사를 통과시킨다(osascript 성공 모사).
            for a in args:
                if a.startswith('set p to POSIX file "'):
                    pth = a[len('set p to POSIX file "'):-1]
                    with open(pth, "wb") as f:
                        f.write(b"\x89PNG\r\n\x1a\n")
            return _sp.CompletedProcess(args, 0, b"", b"")
        return _sp.CompletedProcess(args, 1, b"", b"")

    try:
        clientclip.proc.IS_WINDOWS = False
        clientclip.shutil.which = fake_which
        clientclip.subprocess.run = fake_run
        path = clientclip.save_image()
        assert path and path.endswith(".png"), path
        osa = [c for c in calls if c and c[0] == "osascript"]
        assert osa, ("osascript 분기 선택", calls)
        joined = " ".join(osa[0])
        assert "«class PNGf»" in joined, joined
        assert "open for access" in joined and "write d to f" in joined, joined
    finally:
        clientclip.shutil.which = _which
        clientclip.subprocess.run = _run
        clientclip.proc.IS_WINDOWS = _isw
        try:
            if path:
                os.remove(path)
        except (OSError, NameError):
            pass


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
        st.update_status({"t": "status", "claude_long_turn_sec": 900,
                          "claude_repeat_alert": 5})
        assert st.claude_long_turn_sec == 900
        assert st.claude_repeat_alert == 5
        # 정적 옵션 키가 모두 빠진 주기 status → 직전 값 그대로 유지
        st.update_status({"t": "status"})
        assert st.claude_long_turn_sec == 900, "장기턴 임계 유실"
        assert st.claude_repeat_alert == 5, "반복 임계 유실"
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
