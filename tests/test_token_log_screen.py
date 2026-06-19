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


def _multiday_hour_records():
    """3시간 간격으로 30시간에 걸친 레코드 — 시(hour) 버킷이 ≥2 달력일에 걸친
    여러 시각 행을 만든다(날짜 그룹핑 검증용). 로컬 타임존 무관하게 30h 폭이면
    날짜 경계를 반드시 넘는다."""
    import time as _t
    base = 1_700_000_000.0
    recs = [{"ts": base + h * 3600, "account": "me@x.org", "tokens": 5000}
            for h in range(0, 30, 3)]
    # 같은 레코드들의 로컬시각 'YYYY-MM-DD HH:00' 키로 5h%/1w% 도 채워 열 공존 검증.
    hourly = {}
    for r in recs:
        hk = _t.strftime("%Y-%m-%d %H:00", _t.localtime(r["ts"]))
        hourly[hk] = (hourly.get(hk, 0) + 7)
    dates = sorted({k[:10] for k in hourly})
    return recs, hourly, dates


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


async def test_hour_view_groups_rows_under_date_headers():
    """시(hour) 뷰(시간순)는 같은 날짜의 시각 행을 **날짜 헤더 아래로 묶는다**
    (요청 2026-06-19): 달력일마다 헤더 행 1개 + 그 아래 시각 행들은 날짜를 떼고
    'HHh' 만 들여쓴다."""
    import re
    from textual.widgets import DataTable
    from harness import make_app, server_only, teardown

    recs, hourly, dates = _multiday_hour_records()
    assert len(dates) >= 2, "픽스처가 ≥2 달력일에 걸쳐야"
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(
                recs, hourly_pct=hourly, hourly_week_pct=hourly))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            await pilot.press("h")          # 시(hour) 버킷으로 전환
            await pilot.pause(0.2)
            assert scr._bucket == "hour"
            table = scr.query_one(DataTable)
            n = table.row_count
            labels = [str(table.get_row_at(i)[0]) for i in range(n)]

            # 날짜 헤더 행: 'MM-DD (요일)' 패턴, 들여쓰기 없음. 달력일 수만큼 있어야.
            hdr_rows = [s for s in labels if re.match(r"^\d\d-\d\d \(", s)]
            assert len(hdr_rows) == len(dates), \
                f"날짜 헤더 {len(dates)}개 기대, got {hdr_rows}"

            # 시각 행: 두 칸 들여쓴 'HHh'(또는 ko 'HH시') — 날짜 접두 없음.
            data_rows = [s for s in labels if s.startswith("  ")]
            assert data_rows, "들여쓴 시각 행이 있어야"
            for s in data_rows:
                assert re.match(r"^  \d\d?.+$", s), f"시각 행 형식: {s!r}"
                assert not re.search(r"\d\d-\d\d", s), \
                    f"시각 행 라벨에 날짜가 남으면 안 됨: {s!r}"

            # 헤더 + 시각 행 합 = 전체 행. 헤더는 데이터 행보다 적어야(묶음이므로).
            assert len(hdr_rows) + len(data_rows) == n
            assert len(hdr_rows) < len(data_rows)
    finally:
        await teardown(srv, task, sock)


async def test_hour_view_token_order_stays_flat():
    """토큰순 정렬에서는 날짜가 섞이므로 묶지 않고 평평한 'MM-DD HHh' 라벨을 유지한다."""
    import re
    from textual.widgets import DataTable
    from harness import make_app, server_only, teardown

    recs, hourly, _dates = _multiday_hour_records()
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(recs, hourly_pct=hourly))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            await pilot.press("h")          # 시 버킷
            await pilot.press("o")          # 토큰순 정렬 토글
            await pilot.pause(0.2)
            assert scr._bucket == "hour" and scr._order == "tokens"
            table = scr.query_one(DataTable)
            labels = [str(table.get_row_at(i)[0])
                      for i in range(table.row_count)]
            # 날짜 헤더 행이 없어야 하고, 각 행은 'MM-DD HHh' 평평한 라벨.
            assert not [s for s in labels if re.match(r"^\d\d-\d\d \(", s)], \
                "토큰순엔 날짜 헤더가 없어야"
            assert any(re.search(r"\d\d-\d\d ", s) for s in labels), \
                "평평한 라벨에 날짜 접두가 남아야"
    finally:
        await teardown(srv, task, sock)


async def test_hour_view_footer_shows_boundary_remaining():
    """시(hour) 뷰 footer 가 5h/1주 경계까지 남은 시간을 한 줄로 보이고(요청
    2026-06-19), 다른 버킷(일)에선 footer 를 숨긴다. 시각 전용 리셋 표기는
    parse_reset_ts 가 항상 미래로 롤오버하므로(오늘 지났으면 내일) 결정론적이다."""
    import datetime as _dt
    from textual.widgets import Static, DataTable  # noqa: F401
    from harness import make_app, server_only, teardown

    recs, hourly, _dates = _multiday_hour_records()
    # 주간 경계는 now+2일로 명시 날짜를 만들어 '미래'를 보장(과거면 stale 로 생략됨).
    wk = _dt.datetime.now() + _dt.timedelta(days=2)
    mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec"][wk.month - 1]
    usage = {"session": {"pct": 20, "reset": "11:59pm (Asia/Seoul)"},
             "week_all": {"pct": 14,
                          "reset": f"{mon} {wk.day} at 3am (Asia/Seoul)"}}
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(
                recs, usage=usage, hourly_pct=hourly))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            await pilot.press("h")          # 시(hour) 버킷
            await pilot.pause(0.2)
            foot = scr.query_one("#tkfoot", Static)
            assert foot.display, "hour 뷰에선 footer 가 보여야"
            line = scr._boundary_left_line()
            assert "5h" in line, f"5h 경계 잔여시간이 있어야: {line!r}"
            # 두 축(5h·주간)이 ' · ' 로 이어진다.
            assert " · " in line, f"5h·주간 두 축이 이어져야: {line!r}"

            # 일(day) 버킷으로 바꾸면 footer 를 숨긴다.
            await pilot.press("d")
            await pilot.pause(0.2)
            assert scr._bucket == "day"
            assert not scr.query_one("#tkfoot", Static).display, \
                "일 뷰에선 footer 가 숨겨져야"
    finally:
        await teardown(srv, task, sock)


async def test_hour_view_footer_hidden_without_usage():
    """실측 /usage 가 없으면(리셋 표기 없음) hour 뷰라도 footer 를 숨긴다(지어내지 않음)."""
    from textual.widgets import Static
    from harness import make_app, server_only, teardown

    recs, hourly, _dates = _multiday_hour_records()
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(recs, hourly_pct=hourly))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            await pilot.press("h")
            await pilot.pause(0.2)
            assert scr._boundary_left_line() == ""
            assert not scr.query_one("#tkfoot", Static).display
    finally:
        await teardown(srv, task, sock)
