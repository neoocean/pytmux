"""usage-view 플러그인 테스트 — reset 파서·카운트다운 순수 함수, 오버레이 렌더 회귀,
delete-to-disable 계약.

하이픈 패키지(`pytmuxlib/plugins/claude-token-usage-view`)라 `from ... import` 문법이 안 되므로
`importlib.import_module` 로 모듈을 가져온다(claude-code 계약 테스트와 동일 — PLUGIN_
MANUAL §3.3). 설계: docs/internal/USAGE_VIEW_DESIGN.md."""
import importlib
from datetime import datetime, timedelta

import harness  # noqa: F401  (sys.path 주입)
from rich.style import Style

import pytmuxlib.plugins as plugins

reset = importlib.import_module("pytmuxlib.plugins.claude-token-usage-view.reset")
overlay = importlib.import_module("pytmuxlib.plugins.claude-token-usage-view.overlay")


# --------------------------------------------------------------------------- #
# reset.py — 시각 파서/포맷 (순수 함수)
# --------------------------------------------------------------------------- #

async def test_parse_reset_time_only_today_and_rollover():
    now = datetime(2026, 6, 11, 10, 0, 0)
    # 시각만, 오늘 미래 → 오늘 그 시각(타임존 괄호 무시).
    assert reset.parse_reset_to_dt("2pm (Asia/Seoul)", now) == \
        datetime(2026, 6, 11, 14, 0, 0)
    # 분 포함.
    assert reset.parse_reset_to_dt("1:40pm", now) == datetime(2026, 6, 11, 13, 40)
    # 이미 지난 시각 → 내일로 롤오버.
    late = datetime(2026, 6, 11, 15, 0, 0)
    assert reset.parse_reset_to_dt("2pm", late) == datetime(2026, 6, 12, 14, 0, 0)
    # 자정/정오 경계(12am=00시, 12pm=12시).
    assert reset.parse_reset_to_dt("12am", now) == datetime(2026, 6, 12, 0, 0)
    assert reset.parse_reset_to_dt("12pm", now) == datetime(2026, 6, 11, 12, 0)


async def test_parse_reset_month_day():
    now = datetime(2026, 6, 11, 10, 0, 0)
    # 날짜+시각 → 올해 그 날짜.
    assert reset.parse_reset_to_dt("Jun 13 at 3am (Asia/Seoul)", now) == \
        datetime(2026, 6, 13, 3, 0)
    # 이미 지난 올해 날짜 → 내년.
    assert reset.parse_reset_to_dt("Jun 1 at 3am", now) == \
        datetime(2027, 6, 1, 3, 0)
    # "13 Jun" 어순도.
    assert reset.parse_reset_to_dt("13 Jun", now) == datetime(2026, 6, 13, 0, 0)


async def test_parse_reset_unparseable():
    now = datetime(2026, 6, 11, 10, 0, 0)
    for s in (None, "", "soon", "later today", 123):
        assert reset.parse_reset_to_dt(s, now) is None


async def test_fmt_countdown_stages():
    assert reset.fmt_countdown(timedelta(seconds=0)) == "now"
    assert reset.fmt_countdown(timedelta(seconds=-5)) == "now"
    assert reset.fmt_countdown(timedelta(seconds=42)) == "42s"
    assert reset.fmt_countdown(timedelta(minutes=5, seconds=9)) == "5m 09s"
    assert reset.fmt_countdown(timedelta(hours=2, minutes=5, seconds=3)) == "2h 05m 03s"
    assert reset.fmt_countdown(timedelta(days=1, hours=2)) == "1d 02h 00m"


async def test_urgency_thresholds():
    assert reset.urgency(timedelta(minutes=29)) == "red"       # <30m
    assert reset.urgency(timedelta(minutes=45)) == "yellow"    # <1h
    assert reset.urgency(timedelta(hours=3)) == "cyan"
    # 경계: 정확히 30분은 빨강 아님(< 비교), 1시간은 노랑 아님.
    assert reset.urgency(timedelta(minutes=30)) == "yellow"
    assert reset.urgency(timedelta(minutes=60)) == "cyan"


# --------------------------------------------------------------------------- #
# overlay.py — 셀 그리드 합성 (순수 함수)
# --------------------------------------------------------------------------- #

def _grid(w, h):
    base = Style()
    return [[(" ", base) for _ in range(w)] for _ in range(h)]


def _text(cells):
    return "".join("".join(c[0] for c in row) for row in cells)


async def test_overlay_draws_bars_and_clock():
    now = datetime(2026, 6, 11, 10, 0, 0)
    text_st = Style(color="white")
    digit_st = Style(color="green", bold=True)
    panes = [{"id": 1, "x": 0, "y": 0, "w": 64, "h": 14}]
    usage = {"session": {"pct": 41, "reset": "2pm"},          # 4시간 후 → 블록 시계
             "week_all": {"pct": 14, "reset": "Jun 13 at 3am"}}
    cells = _grid(64, 14)
    overlay.draw_usage_overlay(cells, panes, {1}, 64, 14, text_st, digit_st,
                               usage, age_sec=None, now=now)
    # 카운트다운 블록 글리프(█)가 그려졌는지 — 블록 행 수로 확인(리터럴 숫자 아님).
    blocks = sum(1 for row in cells for c in row if c[0] == "█")
    assert blocks > 20, blocks        # "04:00:00" 8글자×5행 블록 폰트 획
    assert "41%" in _text(cells)      # usage_bar_lines 의 세션 % (막대 줄)


async def test_overlay_countdown_text_fallback_when_narrow():
    """블록 시계가 안 들어가는 좁은/낮은 패널은 한 줄 카운트다운 텍스트로 폴백."""
    now = datetime(2026, 6, 11, 10, 0, 0)
    text_st = Style(color="white"); digit_st = Style(color="green")
    # 폭은 막대용으로 충분하되 블록 시계(pw>=30·하단 5행)는 안 되는 좁은 패널.
    panes = [{"id": 1, "x": 0, "y": 0, "w": 28, "h": 6}]
    usage = {"session": {"pct": 41, "reset": "2pm"}}
    cells = _grid(28, 6)
    overlay.draw_usage_overlay(cells, panes, {1}, 28, 6, text_st, digit_st,
                               usage, now=now)
    assert "다음 리셋까지" in _text(cells)   # 블록 대신 한 줄 카운트다운


async def test_usage_bar_lines_right_align():
    """usage-view 가 켜는 right_align=True 면 % 숫자가 막대 옆이 아니라 줄 오른쪽
    끝(width)에 우측정렬되고(리셋은 막대 뒤), 기본(False)은 종전대로 리셋이 줄 끝."""
    from pytmuxlib.clientscreens import usage_bar_lines
    from pytmuxlib.clientutil import _char_cells

    usage = {"session": {"pct": 18, "reset": "5:59pm (Asia/Seoul)"},
             "week_all": {"pct": 3, "reset": "Jun 18, 12:59pm"}}
    # right_align: 각 줄이 % 로 끝나고, 셀 폭이 정확히 width 라 % 가 우측 끝에 정렬.
    ra = usage_bar_lines(usage, 76, right_align=True)
    assert ra[0].endswith("18%") and ra[1].endswith("3%"), ra
    assert all(sum(_char_cells(c) for c in ln) == 76 for ln in ra[:2]), \
        [sum(_char_cells(c) for c in ln) for ln in ra[:2]]
    # 타임존 괄호는 생략되고 리셋은 막대 뒤(% 앞)에 남는다.
    assert "↻5:59pm" in ra[0] and "(Asia/Seoul)" not in ra[0]
    # 기본(False): 종전 포맷 — 리셋이 줄 끝, % 는 막대 바로 옆.
    base = usage_bar_lines(usage, 76)
    assert base[0].endswith("5:59pm") and base[0].lstrip().startswith("세션")


async def test_usage_bar_lines_track_char_fills_empty():
    """track_char 를 주면 막대의 빈 부분(채움 뒤)이 그 글자로 채워진다(기본 ' '=공백,
    종전 동작). right_align 전제. 채움 칸수는 pct 에 비례하고 나머지는 트랙 글자."""
    from pytmuxlib.clientscreens import usage_bar_lines
    usage = {"session": {"pct": 0, "reset": "2pm"},
             "week_all": {"pct": 100, "reset": "Jun 18"}}
    # 기본(공백): 트랙 글자 '░' 가 나타나지 않는다.
    base = usage_bar_lines(usage, 76, right_align=True)
    assert "░" not in "".join(base)
    # track_char='░': 0% 줄은 트랙이 가득(채움 0), 100% 줄은 트랙이 전혀 없다(채움 가득).
    t = usage_bar_lines(usage, 76, right_align=True, track_char="░")
    assert "░" in t[0], t[0]                 # 0% → 빈 트랙 존재
    assert "░" not in t[1], t[1]             # 100% → 채움만, 트랙 없음
    assert "█" in t[1], t[1]


async def test_usage_bar_lines_prefers_full_account():
    """account_full(전체 이메일)이 있으면 별칭(account) 대신 전체를 계정 줄에 보인다
    (요청: 이메일을 줄이지 않고 전체 표시). 없으면 별칭으로 폴백."""
    from pytmuxlib.clientscreens import usage_bar_lines
    base = {"session": {"pct": 10, "reset": "2pm"}}
    # 전체 이메일 우선
    u = dict(base, account="wo…@nexongames.co.kr",
             account_full="woojin@nexongames.co.kr")
    assert usage_bar_lines(u, 80)[-1].endswith("woojin@nexongames.co.kr")
    # account_full 부재 → 별칭 폴백(재시작 직후 DB 스냅샷 경로)
    u2 = dict(base, account="wo…@nexongames.co.kr")
    assert usage_bar_lines(u2, 80)[-1].endswith("wo…@nexongames.co.kr")


async def test_usage_screen_close_button_dismisses():
    """팝업 우상단 닫기 버튼(#uclose) 클릭 시 dismiss. 박스 안 클릭은 닫지 않는다."""
    from harness import make_app, server_only, teardown

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.4)
            app._run_command("usage-view")
            await pilot.pause(0.25)
            scr = app.screen_stack[-1]
            assert scr.__class__.__name__ == "UsageScreen"

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

            # 닫기 [x](#uclose) 클릭 → 닫힘
            ev = _Ev(_W("uclose", parent=_W("uhead", parent=_W("ubox"))))
            scr.on_click(ev)
            await pilot.pause(0.05)
            assert app.screen_stack[-1] is not scr, "[x] 클릭은 팝업을 닫는다"
            assert ev.stopped
    finally:
        await teardown(srv, task, sock)


async def test_usage_screen_footer_buttons_tappable():
    """하단 동작 버튼(갱신/팝업·탭/패널)을 클릭/터치하면 키보드 단축키와 같은 동작을
    한다(요청 — 모바일에서 [u]/[t]/[a] 키를 못 누른다)."""
    from harness import make_app, server_only, teardown

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.4)
            sent = []
            app.send_cmd = lambda c, **kw: sent.append(c)
            app._run_command("usage-view")
            await pilot.pause(0.25)
            scr = app.screen_stack[-1]
            assert scr.__class__.__name__ == "UsageScreen"
            sent.clear()                       # 열 때의 refresh_usage 제거

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

            def click(wid):
                ev = _Ev(_W(wid, parent=_W("uhint", parent=_W("ubox"))))
                scr.on_click(ev)
                return ev

            # 갱신 버튼 → refresh_usage
            assert click("uref").stopped
            await pilot.pause(0.05)
            assert "refresh_usage" in sent, sent
            # 팝업/탭 전환 → full 클래스 토글
            assert not scr.has_class("full")
            click("utgl")
            await pilot.pause(0.05)
            assert scr.has_class("full"), "팝업/탭 전환 토글"
            # 패널 보기 → 닫고 open_usage_view('pane')
            modes = []
            app.open_usage_view = lambda mode="popup": modes.append(mode)
            click("upane")
            await pilot.pause(0.05)
            assert modes == ["pane"], modes
            assert app.screen_stack[-1] is not scr
    finally:
        await teardown(srv, task, sock)


async def test_usage_screen_colorize_tracks_grey():
    """팝업의 _colorize_tracks: 트랙 글자('░')는 회색 '█' 막대로 치환되고(화면에 '░'
    안 보임), 채움('█')·그 외 글자는 기본색 그대로 — '막대=흰색·빈 부분=회색'(요청)."""
    screen = importlib.import_module(
        "pytmuxlib.plugins.claude-token-usage-view.screen")
    UsageScreen = screen.UsageScreen
    txt = UsageScreen._colorize_tracks(["AB░░", "█░"])
    plain = txt.plain
    assert "░" not in plain, plain          # 트랙 글자는 화면에 안 보임
    assert plain == "AB██\n██", repr(plain)  # '░'→'█' 치환, 줄바꿈 보존
    # 치환된 '█'(원래 '░' 자리)에 회색 스타일 span 이 있다.
    styled_grey = [sp for sp in txt.spans if "grey" in str(sp.style)]
    assert styled_grey, txt.spans


async def test_overlay_noop_when_not_enabled():
    text_st = Style(); digit_st = Style()
    panes = [{"id": 1, "x": 0, "y": 0, "w": 64, "h": 14}]
    cells = _grid(64, 14)
    overlay.draw_usage_overlay(cells, panes, set(), 64, 14, text_st, digit_st,
                               {"session": {"pct": 9, "reset": "2pm"}})
    assert all(c[0] == " " for row in cells for c in row)


async def test_overlay_no_data_message():
    text_st = Style(); digit_st = Style()
    panes = [{"id": 1, "x": 0, "y": 0, "w": 64, "h": 14}]
    cells = _grid(64, 14)
    overlay.draw_usage_overlay(cells, panes, {1}, 64, 14, text_st, digit_st, None)
    assert "데이터 없음" in _text(cells)   # 빈 화면 금지 — 안내 표시


# --------------------------------------------------------------------------- #
# delete-to-disable 계약
# --------------------------------------------------------------------------- #

_UV_CMDS = {"usage-view", "token-viewer", "usage-clock"}


def _registry_without_usage_view():
    """claude-token-usage-view 플러그인을 뺀 Registry — 디렉토리 삭제와 동치
    (_discover 직접 사용). 필터는 플러그인 name 속성(=디렉토리명) 기준."""
    found = plugins._discover()
    return plugins.Registry([p for p in found
                             if getattr(p, "name", "") != "claude-token-usage-view"])


async def test_contract_sanity_usage_view_present_when_loaded():
    """전제: 플러그인이 있으면 usage-view 명령이 노출(헛검증 방지)."""
    reg = plugins.load()
    names = {n for (n, *_rest) in reg.commands}
    assert "usage-view" in names, "usage-view 플러그인이 로드되지 않음"


async def test_contract_no_usage_view_without_plugin():
    """플러그인 부재 시 usage-view 명령/무인자/자동완성이 전부 사라진다."""
    reg = _registry_without_usage_view()
    names = {n for (n, *_rest) in reg.commands}
    assert not (_UV_CMDS & names), f"명령 누수: {_UV_CMDS & names}"
    assert not (reg.noarg & _UV_CMDS), "noarg 누수"
    assert not (_UV_CMDS & set(reg.completions)), "completions 누수"


async def test_contract_client_hooks_noop_without_plugin():
    """클라 오버레이/틱/닫기/명령 훅이 안전한 기본값을 돌려준다."""
    reg = _registry_without_usage_view()
    reg.client_overlay(None, None, 0, 0, None)          # no-op
    assert reg.client_tick(None) is False
    assert reg.client_close_overlay(None, 1) is False
    assert reg.handle_command(None, "usage-view", []) is False


async def test_contract_pane_scoped_present_then_gone():
    """pane_scoped 에 usage-view 가 있다가 부재 시 사라진다."""
    assert "usage-view" in plugins.load().pane_scoped
    assert "usage-view" not in _registry_without_usage_view().pane_scoped


# --------------------------------------------------------------------------- #
# 라이브 통합 — 실제 Textual 클라이언트 경로(팝업 푸시 + pane 오버레이 합성)
# --------------------------------------------------------------------------- #

async def test_usage_view_popup_and_pane_live():
    """실제 클라 앱에서 usage-view 명령이 ① popup 모달을 푸시하고 ② pane 오버레이를
    토글하며, 두 경로 모두 프레임 합성이 안 깨진다. claude-code 가 status 로 싣는
    usage_limits 를 주입해 데이터가 흐르는 경로를 탄다."""
    from harness import make_app, server_only, teardown

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.4)
            # 스크랩 usage 데이터 주입 → claude-code client_statusbar_update 가 흡수.
            # _dispatch→update_status→absorb 는 전부 동기라, 주입 직후 **즉시** 단언한다.
            # pause 를 두면 그 사이 서버 정기 flush(빈 status — server._usage=None 도
            # usage_limits 키로 실려 옴)가 흡수값을 None 으로 덮는 레이스가 있어(win/3.11
            # 헤드리스에서 간헐 적색), pause 없이 동기 흡수만 확인한다.
            app._dispatch({"t": "status", "windows": [],
                           "usage_limits": {
                               "session": {"pct": 41, "reset": "2pm"},
                               "week_all": {"pct": 14, "reset": "Jun 13 at 3am"}},
                           "usage_age_sec": 5})
            assert getattr(app.status, "usage_limits", None), "usage_limits 흡수 실패"
            # ① popup → UsageScreen 푸시 + 렌더.
            app._run_command("usage-view")
            await pilot.pause(0.25)
            top = app.screen_stack[-1]
            assert top.__class__.__name__ == "UsageScreen", top.__class__.__name__
            assert app.view._cells, "팝업 후 프레임 합성 실패"
            await pilot.press("escape")           # 닫기
            await pilot.pause(0.1)
            assert app.screen_stack[-1].__class__.__name__ != "UsageScreen"
            # ② pane 오버레이 → 활성 패널 토글 + 합성 무crash.
            app._run_command("usage-view pane")
            await pilot.pause(0.15)
            assert app.usage_view_panes, "pane 오버레이가 안 켜짐"
            assert app.view._cells, "오버레이 합성 후 렌더 깨짐"
            # 한 번 더 → 토글 오프.
            app._run_command("usage-view pane")
            await pilot.pause(0.1)
            assert not app.usage_view_panes, "pane 오버레이 토글 오프 실패"
    finally:
        await teardown(srv, task, sock)


async def test_usage_view_click_outside_closes():
    """usage-view 팝업(UsageScreen) 박스(#ubox) 바깥(백드롭) 클릭/터치 시 dismiss(None)
    로 닫힌다. 박스 안 클릭은 닫지 않는다(플러그인 관리·InfoScreen 과 동일 판정)."""
    from harness import make_app, server_only, teardown

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.4)
            app._run_command("usage-view")
            await pilot.pause(0.25)
            scr = app.screen_stack[-1]
            assert scr.__class__.__name__ == "UsageScreen", scr.__class__.__name__

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

            # 박스 안 클릭(자식 → … → #ubox) → 닫히지 않음
            ev_in = _Ev(_W("ubars", parent=_W("ubox", parent=_W("screen"))))
            scr.on_click(ev_in)
            await pilot.pause(0.05)
            assert app.screen_stack[-1] is scr, "박스 안 클릭은 닫지 않는다"
            # 박스 바깥(백드롭) 클릭 → 닫힘
            ev_out = _Ev(_W("backdrop", parent=None))
            scr.on_click(ev_out)
            await pilot.pause(0.05)
            assert app.screen_stack[-1] is not scr, "바깥 클릭은 팝업을 닫는다"
            assert ev_out.stopped
    finally:
        await teardown(srv, task, sock)
