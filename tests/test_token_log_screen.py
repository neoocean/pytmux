"""TokenLogScreen(claude-code 플러그인) 팝업 UI 회귀 — 노트북 탭 연결선 + 표 행
하이라이트 키 이동(사용자 요청 2026-06-18).

claude-code 는 하이픈 패키지라 importlib 로 모듈을 가져온다(다른 플러그인 계약 테스트와
동일 — test_plugin_usage_view 패턴). 헤드리스 Textual run_test 로 실제 화면을 띄워
위젯 region/렌더 줄을 검사한다."""
import importlib

import harness  # noqa: F401  (sys.path 주입)

screens = importlib.import_module("pytmuxlib.plugins.claude-code.screens")


def _hour_records():
    """여러 날(기본 day 버킷)에 흩어진 레코드 — 기간(time) 뷰가 ≥6 행을 만든다."""
    base = 1_700_000_000.0          # 고정 ts
    recs = []
    for d in range(6):
        recs.append({"ts": base + d * 86400, "account": "me@x.org",
                     "tokens": 1000 * (d + 1)})
    return recs


async def test_tab_connector_bridges_active_view_tab():
    """노트북 연결선(#tkconn)이 활성 메인 뷰 탭 아래 구간만 ▀(파란 다리)로 그리고
    나머지는 ─ 로 채운다 — 활성 탭이 본문으로 열리는 메인 탭바 모양(사용자 요청)."""
    from harness import make_app, server_only, teardown

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(_hour_records()))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            assert scr.__class__.__name__ == "TokenLogScreen"

            conn = scr.query_one("#tkconn")
            # 렌더된 한 줄을 텍스트로 — ▀ 다리와 ─ 규칙이 함께 있어야 한다.
            strip = conn.render_line(0)
            text = strip.text
            assert "▀" in text, "활성 탭 아래 ▀ 다리가 있어야(노트북 연결)"
            assert "─" in text, "활성 탭 밖은 ─ 규칙이어야"

            # ▀ 구간이 활성 탭(기본=기간/tab_period) Label 의 가로 region 과 겹친다.
            lbl = scr._active_main_tab_widget()
            assert lbl is not None and lbl.id == "tab_period"
            bx0 = lbl.region.x - conn.region.x
            bx1 = bx0 + lbl.region.width
            bridge = [i for i, ch in enumerate(text) if ch == "▀"]
            assert bridge, "다리 셀이 있어야"
            assert min(bridge) >= max(0, bx0) and max(bridge) < bx1, \
                "▀ 다리는 활성 탭 가로 구간 안에 있어야"

            # 다른 탭으로 전환하면(한도 'l') 다리가 그 탭 아래로 옮겨간다.
            await pilot.press("l")
            await pilot.pause(0.2)
            lbl2 = scr._active_main_tab_widget()
            assert lbl2 is not None and lbl2.id == "tab_limit"
            text2 = conn.render_line(0).text
            b2 = [i for i, ch in enumerate(text2) if ch == "▀"]
            b0 = lbl2.region.x - conn.region.x
            assert b2 and min(b2) >= max(0, b0), "다리가 한도 탭 아래로 이동"
    finally:
        await teardown(srv, task, sock)


async def test_table_row_highlight_home_end_pageup_pagedown():
    """표의 하이라이트 행 커서를 Home/End/PgUp/PgDn 으로 옮긴다(사용자 요청 —
    기본 DataTable 바인딩이 이 키들로 행을 안 옮겨준다는 보고)."""
    from textual.widgets import DataTable
    from harness import make_app, server_only, teardown

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(_hour_records()))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            table = scr.query_one(DataTable)
            n = table.row_count
            assert n >= 3, f"여러 시간 버킷 행이 있어야(got {n})"
            table.focus()
            await pilot.pause(0.05)

            # End → 마지막 행, Home → 첫 행
            await pilot.press("end")
            await pilot.pause(0.05)
            assert table.cursor_coordinate.row == n - 1, "End=마지막 행"
            await pilot.press("home")
            await pilot.pause(0.05)
            assert table.cursor_coordinate.row == 0, "Home=첫 행"

            # PgDn 은 아래로, PgUp 은 위로 행 커서를 옮긴다(최소 1행 이상).
            await pilot.press("pagedown")
            await pilot.pause(0.05)
            after_pgdn = table.cursor_coordinate.row
            assert after_pgdn > 0, "PgDn=행 커서 아래로"
            await pilot.press("pageup")
            await pilot.pause(0.05)
            assert table.cursor_coordinate.row < after_pgdn, "PgUp=행 커서 위로"
    finally:
        await teardown(srv, task, sock)
