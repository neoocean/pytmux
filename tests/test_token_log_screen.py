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


async def test_remote_popup_pink_border_local_popup_accent():
    """사용자 요청(2026-06-23): 원격(remote-attach) 보기 중 분홍 토큰 배지를 눌러 연
    팝업은 박스 테두리·제목이 분홍(REMOTE_PINK)이라 로컬 팝업(accent)과 한눈에
    구분된다. remote=True 면 분홍, 기본(로컬)이면 분홍이 아니어야 한다."""
    from harness import make_app, server_only, teardown
    from textual.color import Color
    from pytmuxlib.clientutil import REMOTE_PINK

    pink = Color.parse(REMOTE_PINK)
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            # 원격 팝업 → 분홍 테두리·제목 + 출처 호스트 라벨(§3.3)
            app.push_screen(screens.TokenLogScreen(
                _hour_records(), remote=True, remote_host="playground"))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            assert scr.query_one("#tklogtitle").styles.color == pink, "원격 제목=분홍"
            top = scr.query_one("#tklogbox").styles.border_top
            assert top is not None and top[1] == pink, f"원격 테두리=분홍, got {top}"
            # 출처 호스트가 `⇄host` 로 제목 옆에 분홍으로 표기(데이터 출처 명시)
            host = scr.query_one("#tkloghost")
            assert "⇄playground" in str(host.render()), \
                f"원격 출처 호스트 표기, got {host.render()!r}"
            assert host.styles.color == pink, "출처 호스트=분홍"
            # 뷰 전환(p=세션 뷰, _refresh 가 #tklogtitle 텍스트를 갈아끼움)에도 출처
            # 호스트 라벨은 별개 라벨이라 유지된다.
            await pilot.press("p")
            await pilot.pause(0.2)
            assert "⇄playground" in str(
                scr.query_one("#tkloghost").render()), "뷰 전환에도 출처 유지"
            await pilot.press("escape")
            await pilot.pause(0.2)
            # 로컬 팝업(기본) → 분홍 아님 + 호스트 라벨 빈칸
            app.push_screen(screens.TokenLogScreen(_hour_records()))
            await pilot.pause(0.3)
            scr2 = app.screen_stack[-1]
            assert scr2.query_one("#tklogtitle").styles.color != pink, "로컬 제목≠분홍"
            top2 = scr2.query_one("#tklogbox").styles.border_top
            assert top2 is None or top2[1] != pink, f"로컬 테두리≠분홍, got {top2}"
            assert str(scr2.query_one("#tkloghost").render()).strip() == "", \
                "로컬 팝업은 출처 호스트 라벨 빈칸"
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


async def test_tree_leaf_left_jumps_to_parent_and_collapses():
    """요청 2026-06-22: 계층 트리의 leaf(시각) 행에 커서가 있을 때 ← 를 누르면 더
    이상 접을 게 없으므로 **부모(오늘 일 행)로 올라가 그 부모를 접고** 커서를 부모로
    옮긴다(표준 트리 동작 — leaf 막다른 끝에서 ← 로 상위 입도로 빠져나오기). 펼쳐진
    노드에서 ← 는 종전대로 자기 자신을 접는다(기존 동작 보존)."""
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
            # 오늘 행=row0(▼). 그 아래 시각(leaf) 행으로 커서를 옮긴다(row1).
            assert str(table.get_row_at(0)[0]).startswith("▼ "), "오늘 행 펼침"
            assert scr._tree_nodes[1]["kind"] == "hour", "row1=시각 leaf"
            table.move_cursor(row=1)
            await pilot.pause(0.05)
            # leaf 에서 ← → 부모(오늘 행, row0)가 접히고 커서가 부모로.
            await pilot.press("left")
            await pilot.pause(0.1)
            assert hour_count() == 0, "leaf ← 로 부모(오늘 행)가 접혀 시각 행이 사라져야"
            assert str(table.get_row_at(0)[0]).startswith("▶ "), "접힌 오늘 행=▶"
            assert table.cursor_coordinate.row == 0, "커서가 부모(오늘) 행으로 이동"
            # 최상위 leaf(부모 없음)에서 ← 는 무동작·무크래시 — 접힌 오늘 행(이제 leaf
            # 아님이지만 펼쳐지지 않은 상태)에서 ← 는 부모가 없으니 그대로.
            await pilot.press("left")
            await pilot.pause(0.05)
            assert app.screen_stack[-1] is scr, "← 가 팝업을 닫지 않음(no-crash)"
    finally:
        await teardown(srv, task, sock)


def _model_session_records():
    """모델 티어(opus·haiku)가 섞인 다일·다세션 레코드. 모델 색 분할이 살아 있으면
    막대 셀 span 에 magenta(opus)·green(haiku)이 나타난다(단색이면 안 나타남) — 범례·
    막대색 제거(요청 2026-06-22) 검증용. sonnet(cyan)은 단색 cyan 과 겹쳐 제외한다."""
    base = 1_700_000_000.0
    models = ["opus-4.8", "haiku-4.5"]
    recs = []
    for i in range(4):
        recs.append({"ts": base + i * 86400, "tab": 0, "pane": 1,
                     "session": 10 + i, "account": "me@x.org",
                     "tokens": 2000 * (i + 1), "model": models[i % 2]})
    return recs


async def test_period_and_session_drop_model_color_and_legend():
    """요청 2026-06-22: 첫 탭(Period)과 Session 탭은 **모델별 색 구분을 없애고** 사용량만
    단색 막대로 보인다 — 상단(#tktop)에 모델 색 범례(█)가 없고, 막대 셀에 모델색
    (opus=magenta·haiku=green)이 섞이지 않는다. 모델별 비율은 Recon 탭에서 본다."""
    from textual.widgets import DataTable
    from harness import make_app, server_only, teardown

    # 단색 막대는 cyan(스타일 통째) — 모델 분할이면 셀 span 에 아래 색이 박힌다.
    model_span_colors = {"magenta", "green", "yellow", "#808080"}

    def _assert_no_model(scr):
        top = scr._tktop_text
        text = top.plain if hasattr(top, "plain") else str(top)
        assert "█" not in text, f"#tktop 에 모델 범례(█) 없어야: {text!r}"
        table = scr.query_one(DataTable)
        for i in range(table.row_count):
            for cell in table.get_row_at(i):
                for sp in getattr(cell, "spans", []) or []:
                    assert str(sp.style) not in model_span_colors, \
                        f"막대에 모델 색({sp.style}) 남으면 안 됨(row {i})"

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(_model_session_records()))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            assert scr._view == "time"
            _assert_no_model(scr)                  # Period(계층 트리)
            await pilot.press("p")                 # Session 뷰로 전환
            await pilot.pause(0.2)
            assert scr._view == "session"
            _assert_no_model(scr)                  # Session
    finally:
        await teardown(srv, task, sock)


async def test_recon_top_fits_two_lines():
    """요청 2026-06-22: [대사](Recon) 상단(#tktop)을 **2줄 이내**로 — 긴 막대 설명
    괄호를 빼 한 줄로 줄였다. 범례가 있으면 둘째 줄(개행 1개)까지, 없으면 한 줄."""
    from textual.widgets import Static
    from harness import make_app, server_only, teardown

    ivs = _recon_intervals(20)
    for iv in ivs:                       # 모델 분해를 실어 범례(둘째 줄)도 그리게
        iv["models"] = {"opus": 400, "sonnet": 200}
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(_hour_records(),
                                                   reconcile=ivs))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            await pilot.press("r")
            await pilot.pause(0.2)
            assert scr._recon_mode
            top = scr._tktop_text
            text = top.plain if hasattr(top, "plain") else str(top)
            assert text.count("\n") <= 1, f"대사 상단은 2줄 이내여야: {text!r}"
            assert "→" in text, f"구간 시간 범위(→) 표기: {text!r}"
            # 긴 막대 설명 괄호가 빠졌는지(ko/en 양쪽 — wrap 으로 3줄 되던 원인 제거).
            assert all(s not in text for s in
                       ("(막대=", "(bars=", "measured", "스크롤)")), \
                f"긴 괄호 설명은 빠져야: {text!r}"
    finally:
        await teardown(srv, task, sock)


async def test_recon_scroll_noop_when_all_intervals_visible():
    """항목6 감사(요청 2026-06-22): 구간이 폭에 다 들어오면 _max_off==0 이라 ← 는
    **정상적으로 무동작**(더 옛 구간이 없음 = 버그 아님). x_off 가 0 에 머물고 크래시
    없이 그래프가 계속 보인다. (스크롤이 실제로 먹는 경우는
    test_recon_tab_shows_time_axis_chart_and_scrolls 가 검증한다.)"""
    from harness import make_app, server_only, teardown

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(_hour_records(),
                                                   reconcile=_recon_intervals(3)))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            await pilot.press("r")
            await pilot.pause(0.2)
            chart = scr.query_one("#tkchart", screens._ReconChart)
            chart._build()                       # _max_off 갱신
            assert chart._max_off == 0, "구간이 다 보이면 더 옛 구간이 없어 max_off=0"
            await pilot.press("left")
            await pilot.pause(0.05)
            assert chart.x_off == 0, "← 는 안전한 무동작(x_off 그대로)"
            assert app.screen_stack[-1] is scr and chart.display, "무크래시·그래프 유지"
    finally:
        await teardown(srv, task, sock)


async def test_limit_tab_model_section_cycle_and_apply():
    """요청 2026-06-22(항목7): 모델·컨텍스트 변경을 토큰 팝업 [한도] 탭의 첫 두 행으로
    통합(독립 모달 ModelCtxScreen 대신). 행0=모델·행1=컨텍스트, ←→ 로 값 변경·Enter 로
    적용(/model 주입=_apply_model_config). 적용 후에도 팝업은 닫히지 않는다."""
    from textual.widgets import DataTable
    from harness import make_app, server_only, teardown

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(
                _hour_records(), initial_mode="limit", model="opus"))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            assert scr._limit_mode, "한도 탭으로 열려야"
            table = scr.query_one(DataTable)
            table.focus()
            await pilot.pause(0.05)
            # 행0=모델(현재 opus), 행1=컨텍스트(기본/Default).
            assert "opus" in str(table.get_row_at(0)[0]), table.get_row_at(0)
            r1 = str(table.get_row_at(1)[0])
            assert ("기본" in r1 or "Default" in r1), r1
            # 커서 행0에서 → 로 모델 값이 다음으로(opus→sonnet), 팝업은 안 닫힘.
            table.move_cursor(row=0)
            msel0 = scr._mc_msel
            await pilot.press("right")
            await pilot.pause(0.05)
            assert scr._mc_msel == (msel0 + 1) % len(scr._mc_models), scr._mc_msel
            assert app.screen_stack[-1] is scr, "→ 가 팝업을 닫지 않음"
            # 행1(컨텍스트)에서 → 로 컨텍스트가 1m 으로.
            table.move_cursor(row=1)
            await pilot.press("right")
            await pilot.pause(0.05)
            assert scr._mc_csel == 1, scr._mc_csel
            # Enter 로 현재 선택 적용 → _apply_model_config 호출(팝업 유지).
            applied = []
            app._apply_model_config = lambda res: applied.append(res)
            table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause(0.05)
            assert applied, "Enter 로 _apply_model_config 호출돼야"
            model, ctx = applied[-1]
            assert model == scr._mc_models[scr._mc_msel][1], applied
            assert ctx == scr._mc_ctx[scr._mc_csel][1], applied
            assert app.screen_stack[-1] is scr, "Enter 적용 후에도 팝업 유지"
            # 비-모델/컨텍스트 행(빈 줄/한도 상세)에서 ←→ 는 무동작이고 팝업도 유지.
            table.move_cursor(row=2)
            await pilot.press("left")
            await pilot.pause(0.05)
            assert app.screen_stack[-1] is scr, "비-편집 행 ← 는 팝업을 닫지 않음"
    finally:
        await teardown(srv, task, sock)


async def test_xc_totals_shown_as_primary_sigma_with_cache():
    """§10-D P6: 트랜스크립트 실측(usage_xc full)이 오면 상단 Σ 를 그 실측값으로 1차
    표시하고(캐시 별도 표기), 스크랩 누계는 '활동~' 보조신호로 강등한다. 스크랩은
    cache 를 못 봐 실제의 ~0.4%만 잡으므로 그대로 Σ 로 쓰면 두 자릿수 배율 과소표시.
    P6b: 캐시는 읽기(read)/쓰기(creation)를 **분리** 표기한다(의미·단가 상이)."""
    import importlib
    from harness import make_app, server_only, teardown
    usagelog = importlib.import_module("pytmuxlib.plugins.claude-code.usagelog")

    xc = {"full": 9_900_000_000, "footer": 21000, "cache_read": 6_000_000,
          "cache_create": 2_000_000, "ratio": 471428.0}
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(
                _hour_records(), total_all=21000, xc_totals=xc))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            top = scr._tktop_text
            text = top.plain if hasattr(top, "plain") else str(top)
            ffull = usagelog._fmt_tokens(9_900_000_000)     # '9900M'
            fread = usagelog._fmt_tokens(6_000_000)         # '6M'  (읽기)
            fcreate = usagelog._fmt_tokens(2_000_000)       # '2M'  (쓰기)
            fscrape = usagelog._fmt_tokens(21000)           # '21k'
            assert f"Σ{ffull}" in text, f"실측 full 이 1차 Σ: {text!r}"
            assert fread in text, f"캐시 읽기 별도 표기: {text!r}"
            assert fcreate in text, f"캐시 쓰기 별도 표기: {text!r}"
            assert f"~{fscrape}" in text, f"스크랩은 활동~ 보조신호: {text!r}"
            # 스크랩 누계가 1차 Σ 자리를 차지하면 안 된다(과소표시 방지).
            assert f"Σ{fscrape}" not in text, f"스크랩이 Σ 1차면 안 됨: {text!r}"
    finally:
        await teardown(srv, task, sock)


async def test_no_xc_totals_falls_back_to_scrape_sigma():
    """구버전 서버/빈 usage_xc(xc_totals 없음) → 종전 스크랩 ~Σ 폴백(회귀 안전)."""
    import importlib
    from harness import make_app, server_only, teardown
    usagelog = importlib.import_module("pytmuxlib.plugins.claude-code.usagelog")
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(screens.TokenLogScreen(
                _hour_records(), total_all=21000))      # xc_totals 미지정
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            text = scr._tktop_text.plain
            assert f"~Σ{usagelog._fmt_tokens(21000)}" in text, \
                f"폴백은 스크랩 ~Σ: {text!r}"
    finally:
        await teardown(srv, task, sock)


async def test_remote_token_log_timeout_notice_only_when_pending():
    """§4.2 업스트림 웨지 타임아웃: 원격 토큰 로그 요청이 타임아웃까지 회신되지 않으면
    (_want_token_log 그대로 True) notice 를 띄우고 대기를 푼다. 응답이 이미 왔거나
    (want False) 더 새 요청이 떠 seq 가 어긋나면 무동작(옛 타이머가 새 요청/정상 응답을
    오염시키지 않음). _open_token_log 가 원격일 때만 set_timer 로 이 콜백을 건다."""
    from types import SimpleNamespace
    cc = importlib.import_module("pytmuxlib.plugins.claude-code")
    msgs = []

    def app(seq=5, want=True):
        return SimpleNamespace(
            _token_log_seq=seq, _want_token_log=want,
            display_message=lambda text, secs=2.0: msgs.append((text, secs)))

    # 정상 타임아웃: seq 일치 + 응답 미수신 → notice + 대기 해제(호스트명 포함)
    a = app(seq=5, want=True)
    cc._token_log_timeout(a, 5, "playground")
    assert a._want_token_log is False, "타임아웃이 대기를 풀어야"
    assert len(msgs) == 1 and "playground" in msgs[0][0], msgs

    # 응답 이미 도착(want False) → 무동작(_on_token_log_msg 가 want 를 내렸음)
    msgs.clear()
    cc._token_log_timeout(app(seq=5, want=False), 5, "playground")
    assert msgs == [], f"응답 후엔 notice 없음: {msgs!r}"

    # 더 새 요청이 떠 seq 불일치 → 무동작(옛 타이머 무력화), 새 대기 보존
    msgs.clear()
    a3 = app(seq=7, want=True)
    cc._token_log_timeout(a3, 5, "playground")
    assert msgs == [] and a3._want_token_log is True, "옛 타이머가 새 요청 오염 금지"
