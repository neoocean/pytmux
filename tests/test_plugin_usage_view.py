"""usage-view 플러그인 테스트 — reset 파서·카운트다운 순수 함수, 오버레이 렌더 회귀,
delete-to-disable 계약.

하이픈 패키지(`pytmuxlib/plugins/claude-token-usage-view`)라 `from ... import` 문법이 안 되므로
`importlib.import_module` 로 모듈을 가져온다(claude-code 계약 테스트와 동일 — PLUGIN_
MANUAL §3.3). 설계: docs/USAGE_VIEW_DESIGN.md."""
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
