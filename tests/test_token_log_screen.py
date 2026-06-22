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


def _recent_tree_records():
    """오늘(같은 날 5개 시각)·이전 달(40일 전)에 걸친 레코드 — 계층 타임라인 트리가
    오늘 행을 시각까지 기본 펼치고, 이전 달은 월 행으로 접는다. ts 는 **오늘 정오 기준
    상대값**이라 새벽/자정 경계로 시각이 어제로 새지 않아 결정론적이다."""
    import time as _t
    import datetime as _dt
    noon = _dt.datetime.now().replace(hour=12, minute=0, second=0,
                                      microsecond=0)
    today_ts = noon.timestamp()
    recs, hourly = [], {}
    for h in range(0, 5):                       # 오늘 12·11·10·09·08시 (모두 같은 날)
        ts = today_ts - h * 3600
        recs.append({"ts": ts, "account": "me@x.org", "tokens": 5000})
        hk = _t.strftime("%Y-%m-%d %H:00", _t.localtime(ts))
        hourly[hk] = hourly.get(hk, 0) + 7
    # 40일 전 = 반드시 이전 달·이전 ISO주 → 기본 접힌 월 행.
    recs.append({"ts": today_ts - 40 * 86400, "account": "me@x.org",
                 "tokens": 9000})
    return recs, hourly


async def test_table_row_highlight_home_end_pageup_pagedown():
    """표의 하이라이트 행 커서를 Home/End/PgUp/PgDn 으로 옮긴다(사용자 요청 —
    기본 DataTable 바인딩이 이 키들로 행을 안 옮겨준다는 보고)."""
    from textual.widgets import DataTable
    from harness import make_app, server_only, teardown

    recs, hourly = _recent_tree_records()
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(recs, hourly_pct=hourly))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            table = scr.query_one(DataTable)
            n = table.row_count
            assert n >= 3, f"여러 트리 행이 있어야(got {n})"
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


async def test_tree_today_expands_to_hours():
    """계층 타임라인(2026-06-21): 오늘 행은 기본 펼침(▼)으로 그 아래 시각 행들을
    들여쓰기로 보이고, 이전 달은 접힌 월 행(▶)으로 보인다(§3 기본 펼침 깊이)."""
    import re
    from textual.widgets import DataTable
    from harness import make_app, server_only, teardown

    recs, hourly = _recent_tree_records()
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(
                recs, hourly_pct=hourly, hourly_week_pct=hourly))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            assert scr._view == "time"
            table = scr.query_one(DataTable)
            labels = [str(table.get_row_at(i)[0])
                      for i in range(table.row_count)]

            # 오늘 행: ▼(펼침)로 시작하는 일 행이 있어야.
            assert any(s.startswith("▼ ") for s in labels), \
                f"오늘 행이 ▼로 펼쳐져야: {labels}"
            # 이전 달: ▶(접힘)로 시작하는 'YYYY-MM' 월 행.
            assert any(s.startswith("▶ ") and re.search(r"\d{4}-\d\d", s)
                       for s in labels), f"이전 달이 ▶ 월 행이어야: {labels}"
            # 시각 행: 들여쓰고 'HH시'/'HHh'만(날짜 없음). 트리 노드로 확인.
            hour_nodes = [n for n in scr._tree_nodes if n["kind"] == "hour"]
            assert len(hour_nodes) >= 2, "오늘 시각 행이 여럿 펼쳐져야"
            for n in hour_nodes:
                assert n["level"] >= 1 and re.match(r"^\d\d(시|h)$", n["label"]), \
                    f"시각 라벨: {n['label']!r}"
            # 시각 행은 leaf — 펼침 불가.
            assert all(not n["expandable"] for n in hour_nodes)
    finally:
        await teardown(srv, task, sock)


async def test_tree_collapse_and_expand_today_row():
    """오늘 행에서 ← 접기 → 시각 행 사라지고 ▶ 로, → 펼치기 → 시각 행 복귀(SC-2/3)."""
    from textual.widgets import DataTable
    from harness import make_app, server_only, teardown

    recs, hourly = _recent_tree_records()
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(recs, hourly_pct=hourly))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            table = scr.query_one(DataTable)
            table.focus()
            await pilot.pause(0.05)

            def hour_count():
                return sum(1 for n in scr._tree_nodes if n["kind"] == "hour")

            assert hour_count() >= 2, "처음엔 오늘 시각 행이 펼쳐져 있어야"
            # 커서를 오늘(첫) 행에 두고 ← 로 접는다.
            await pilot.press("home")
            await pilot.pause(0.05)
            await pilot.press("left")
            await pilot.pause(0.1)
            assert hour_count() == 0, "← 로 오늘 행 접으면 시각 행이 사라져야"
            assert str(table.get_row_at(0)[0]).startswith("▶ "), "접힌 오늘 행=▶"
            # → 로 다시 펼친다.
            await pilot.press("right")
            await pilot.pause(0.1)
            assert hour_count() >= 2, "→ 로 다시 펼치면 시각 행 복귀"
            assert str(table.get_row_at(0)[0]).startswith("▼ "), "펼친 오늘 행=▼"
            # Enter 로도 토글(닫히지 않고). 두 번이면 접었다 펼침.
            await pilot.press("home")
            await pilot.pause(0.05)
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.screen_stack[-1] is scr, "Enter 는 팝업을 닫지 않음"
            assert hour_count() == 0, "Enter 로 접힘"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert hour_count() >= 2, "Enter 로 다시 펼침"
    finally:
        await teardown(srv, task, sock)


async def test_tree_hour_rows_have_5h_1w_columns_with_reset_left():
    """시각 행이 있는 트리는 5h%/1w% 칼럼 제목에 리셋 잔여시간을 inline 으로 붙인다
    (예 '5h% (in 87m)'·'1w% (in 6d)')."""
    import datetime as _dt
    from textual.widgets import DataTable
    from textual.css.query import NoMatches
    from harness import make_app, server_only, teardown

    recs, hourly = _recent_tree_records()
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
                recs, usage=usage, hourly_pct=hourly, hourly_week_pct=hourly))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            table = scr.query_one(DataTable)
            heads = [str(c.label) for c in table.columns.values()]
            joined = " | ".join(heads)
            assert any(h.startswith("5h%") and "(" in h for h in heads), joined
            assert any(h.startswith("1w%") and "(" in h for h in heads), joined
            # 옛 footer 위젯(#tkfoot)은 제거됐다.
            try:
                scr.query_one("#tkfoot")
                assert False, "#tkfoot 위젯은 제거돼야"
            except NoMatches:
                pass
    finally:
        await teardown(srv, task, sock)


def _recon_intervals(n=30):
    """대사 구간 n개(시간순) — 5h% 가 톱니로 오르며 중간에 리셋 1회."""
    base = 1_700_000_000.0
    ivs = []
    for i in range(n):
        p0 = (i * 7) % 100
        p1 = min(100, p0 + 6)
        ivs.append({"t0": base + i * 3600, "t1": base + (i + 1) * 3600,
                    "account": "me@x.org", "pct0": p0, "pct1": p1,
                    "dpct": p1 - p0, "tokens": 1000 + i,
                    "reset": (i == 10)})
    return ivs


async def test_recon_tab_shows_time_axis_chart_and_scrolls():
    """[대사] 탭(r)이 표 대신 시간축 세로 막대 그래프를 보이고(요청 2026-06-20),
    좌우 키로 더 이전 구간을 스크롤한다. 진입 시 표는 숨고 그래프가 본문을 차지."""
    from textual.widgets import DataTable
    from harness import make_app, server_only, teardown

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            # 폭에 다 안 들어갈 만큼(>capacity) 구간을 줘 스크롤을 실제로 검증.
            app.push_screen(screens.TokenLogScreen(
                _hour_records(), reconcile=_recon_intervals(120)))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]

            # 대사 모드 진입(r) → 그래프 보이고 표 숨김.
            await pilot.press("r")
            await pilot.pause(0.2)
            assert scr._recon_mode
            chart = scr.query_one("#tkchart", screens._ReconChart)
            table = scr.query_one(DataTable)
            assert chart.display and not table.display, \
                "대사 모드는 그래프를 보이고 표를 숨겨야"

            # 막대 글리프가 실제로 렌더된다(세로 블록 문자 중 하나라도).
            rendered = "\n".join(chart.render_line(y).text
                                 for y in range(chart.size.height))
            assert any(b in rendered for b in "▁▂▃▄▅▆▇█"), \
                f"세로 막대 글리프가 있어야:\n{rendered}"
            # x축선·시각 라벨도(└ 모서리, ':' 또는 '-' 가 포함된 시각 라벨).
            assert "└" in rendered and "─" in rendered, "x축선이 있어야"

            # 좌우 스크롤: 처음엔 최신이 보이고(왼쪽 키=더 옛 구간으로).
            chart.x_off = 0
            chart.refresh()
            await pilot.pause(0.05)
            scr._build_off0 = chart._build()["i0"]
            await pilot.press("left")       # 더 이전(옛) 구간으로
            await pilot.pause(0.05)
            assert chart.x_off >= 1, "왼쪽 키로 옛 구간 스크롤"
            after = chart._build()["i0"]
            assert after < scr._build_off0, "보이는 첫 구간이 더 옛으로 이동"

            # Home=가장 옛(첫 구간 0 포함), End=최신으로 복귀.
            await pilot.press("home")
            await pilot.pause(0.05)
            assert chart._build()["i0"] == 0, "Home=가장 옛 구간"
            await pilot.press("end")
            await pilot.pause(0.05)
            assert chart.x_off == 0, "End=최신(오른쪽 끝)"

            # r 다시 누르면 표로 복귀(그래프 숨김).
            await pilot.press("r")
            await pilot.pause(0.2)
            assert not scr._recon_mode
            assert not scr.query_one("#tkchart", screens._ReconChart).display
            assert scr.query_one(DataTable).display
    finally:
        await teardown(srv, task, sock)


def _session_records():
    """세션 3개(27·6·99)의 토큰 레코드 — [세션] 뷰가 세션별 합 행을 만든다."""
    base = 1_700_000_000.0
    recs = []
    for sid, tok in [(27, 5000), (6, 3000), (99, 1000)]:
        recs.append({"ts": base, "tab": 0, "pane": 1, "session": sid,
                     "account": "me@x.org", "tokens": tok})
    return recs


async def test_session_view_highlights_active_session():
    """요청 2026-06-21: [세션] 뷰에서 현재 활성 세션(active_session) 행은 라벨이
    orange1 굵게로 강조되고, 막대도 단색 오렌지로 그려진다 — 비활성 행은 평문/모델색."""
    from textual.widgets import DataTable
    from textual.coordinate import Coordinate
    from rich.text import Text
    from harness import make_app, server_only, teardown

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(
                _session_records(), active_session=27))
            await pilot.pause(0.2)
            scr = app.screen_stack[-1]
            # 세션 뷰로 전환(클릭 핸들러와 같은 경로).
            scr._exit_body_modes()
            scr._view = "session"
            await scr._refresh()
            await pilot.pause(0.1)
            table = scr.query_one(DataTable)
            active_lbl = active_bar = None
            other_lbl_plain = False
            for r in range(table.row_count):
                lbl = table.get_cell_at(Coordinate(r, 0))
                plain = lbl.plain if isinstance(lbl, Text) else str(lbl)
                if "세션 27" in plain:
                    active_lbl = lbl
                    # 막대 열(라벨0·타임스탬프1·토큰2·막대3) — 활성 세션은 최대라 꽉 참.
                    active_bar = table.get_cell_at(Coordinate(r, 3))
                elif "세션 6" in plain:
                    other_lbl_plain = not isinstance(lbl, Text) or \
                        "orange" not in str(lbl.style)
            assert isinstance(active_lbl, Text) and "orange1" in str(active_lbl.style), \
                f"활성 세션 라벨이 orange1 강조여야: {active_lbl!r}"
            assert isinstance(active_bar, Text) and "orange1" in str(active_bar.style) \
                and any(b in active_bar.plain for b in "▁▂▃▄▅▆▇█"), \
                f"활성 세션 막대가 단색 오렌지여야: {active_bar!r}"
            assert other_lbl_plain, "비활성 세션 라벨은 강조하지 않음"
    finally:
        await teardown(srv, task, sock)


async def test_hour_view_header_plain_without_usage():
    """실측 /usage 가 없으면(리셋 표기 없음) hour 뷰 5h% 칼럼 제목은 잔여시간 없이
    맨 '5h%'(지어내지 않음). _reset_left 가 None 이라 inline 잔여시간을 안 붙인다."""
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
            await pilot.press("h")
            await pilot.pause(0.2)
            assert scr._reset_left("session") is None
            heads = [str(c.label) for c in
                     scr.query_one(DataTable).columns.values()]
            assert "5h%" in heads, f"맨 5h% 제목이어야(잔여시간 없이): {heads}"
            assert not any("(" in h for h in heads if h.startswith("5h%")), heads
    finally:
        await teardown(srv, task, sock)
