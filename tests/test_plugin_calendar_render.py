"""calendar 플러그인 render.py(달력 오버레이) 회귀 — 완전 격리 후 가드.

`draw_calendar_overlay` 는 앱 상태 비의존 순수 함수라 앱·소켓 없이 직접 호출해 셀
그리드 출력을 단언한다. clientrender.py 에서 plugins/calendar/render.py 로 옮긴 뒤
(완전한 delete-to-disable) 화면 출력이 불변임을 고정한다.
"""
from datetime import datetime

import harness  # noqa: F401  (경로 설정)
from rich.style import Style

from pytmuxlib.plugins.calendar.render import draw_calendar_overlay


def _grid(w, h):
    """(char, style) 셀 h×w 그리드를 공백으로 초기화."""
    base = Style()
    return [[(" ", base) for _ in range(w)] for _ in range(h)]


def _text_rows(cells):
    """셀 그리드를 글자만 뽑아 행 문자열 리스트로(스타일 무시, 배치 검증용)."""
    return ["".join(c[0] for c in row) for row in cells]


def _cal_styles():
    return {
        "day": Style(color="white"),
        "title": Style(color="green", bold=True),
        "today": Style(color="black", bgcolor="green", bold=True),
        "big_today": Style(color="green", bold=True),
        "border": Style(color="blue"),
    }


async def test_calendar_overlay_grid_has_title_and_today_highlight():
    now = datetime(2026, 6, 6)        # 2026-06, 오늘=6일
    panes = [{"id": 1, "x": 0, "y": 0, "w": 30, "h": 12}]
    cells = _grid(30, 12)
    draw_calendar_overlay(cells, panes, {1}, 30, 12, _cal_styles(), now=now)
    joined = "".join(_text_rows(cells))
    assert "2026-06" in joined                      # 제목
    assert "Mo" in joined and "Su" in joined        # 요일 헤더
    # 오늘(6일)은 today 스타일(bgcolor=green)로 칠해진 셀이 있어야 한다
    today_cells = [c for row in cells for c in row
                   if c[0] == "6" and c[1].bgcolor is not None]
    assert today_cells, "오늘 날짜 강조 셀 없음"
    # calendar_panes 비면 무동작
    cells2 = _grid(30, 12)
    draw_calendar_overlay(cells2, panes, set(), 30, 12, _cal_styles(), now=now)
    assert all(c[0] == " " for row in cells2 for c in row)


async def test_calendar_overlay_small_pane_falls_back_to_date_string():
    now = datetime(2026, 6, 6)
    panes = [{"id": 1, "x": 0, "y": 0, "w": 12, "h": 3}]
    cells = _grid(12, 3)
    draw_calendar_overlay(cells, panes, {1}, 12, 3, _cal_styles(), now=now)
    assert "2026-06-06" in "".join(_text_rows(cells))


async def test_calendar_overlay_month_offset_shifts_title_and_drops_today():
    """offsets 로 표시 월을 옮긴다 — -1=지난달(2026-05), +1=다음달(2026-07), 연 경계
    -6=2025-12. 오프셋 달엔 '오늘' 강조가 없다(그 달엔 오늘이 없으므로)."""
    now = datetime(2026, 6, 6)        # 2026-06, 오늘=6일
    panes = [{"id": 1, "x": 0, "y": 0, "w": 30, "h": 12}]

    def render(off):
        cells = _grid(30, 12)
        draw_calendar_overlay(cells, panes, {1}, 30, 12, _cal_styles(),
                              now=now, offsets={1: off})
        return cells

    # 지난달/다음달 제목
    assert "2026-05" in "".join(_text_rows(render(-1)))
    assert "2026-07" in "".join(_text_rows(render(1)))
    # 해를 넘어가는 큰 오프셋(연 경계)
    assert "2025-12" in "".join(_text_rows(render(-6)))
    assert "2027-06" in "".join(_text_rows(render(12)))
    # 오프셋 달엔 today 강조(bgcolor) 셀이 없다
    nxt = render(1)
    assert not [c for row in nxt for c in row
                if c[1].bgcolor is not None and c[0] != " "], "넘긴 달에 오늘 강조 없음"
    # 이번 달(offset 0)은 여전히 오늘 강조가 있다
    cur = render(0)
    assert [c for row in cur for c in row
            if c[0] == "6" and c[1].bgcolor is not None], "이번 달 오늘 강조 유지"


async def test_calendar_overlay_records_nav_click_zones():
    """nav_zones 가 주어지면 제목 ‹/› 의 클릭 영역을 패널별로 기록한다 — 좌=이전 달
    (delta -1), 우=다음 달(delta +1). 기록된 행/열에 실제 화살표 글리프가 그려져 있다."""
    now = datetime(2026, 6, 6)
    panes = [{"id": 1, "x": 0, "y": 0, "w": 30, "h": 12}]
    cells = _grid(30, 12)
    zones = {}
    draw_calendar_overlay(cells, panes, {1}, 30, 12, _cal_styles(),
                          now=now, nav_zones=zones)
    assert 1 in zones and len(zones[1]) == 2
    assert sorted(d for (_, _, _, d) in zones[1]) == [-1, 1]
    for (x0, x1, y, delta) in zones[1]:
        glyph = "‹" if delta == -1 else "›"
        row = "".join(c[0] for c in cells[y])
        assert glyph in row[x0:x1], f"{glyph} not in click zone {x0}:{x1}@{y}"
    # 단순 날짜 폴백(작은 패널)엔 화살표가 없어 zone 도 기록되지 않는다
    small = {}
    draw_calendar_overlay(_grid(12, 3), [{"id": 1, "x": 0, "y": 0, "w": 12, "h": 3}],
                          {1}, 12, 3, _cal_styles(), now=now, nav_zones=small)
    assert small == {}, "작은 폴백엔 클릭존 없음"


async def test_calendar_overlay_key_home_returns_to_current_month():
    """요청 2026-06-22(감사+회귀가드): 달력 오버레이가 활성 패널에 떠 있을 때 Home
    (또는 '.')을 누르면 표시 월이 **이번 달(offset 0)** 로 복귀한다. ←/→ 는 이전/다음
    달. 기능은 이미 `_CalendarPlugin.client_overlay_key` 에 구현돼 있어(clientio.on_key
    가 ctrl+v·shift+esc 다음, 스크롤백/패스스루보다 **앞**에서 디스패치) 이를 고정하는
    가드 — 라이브에서 안 되면 이 디스패치 순서/활성 패널 매칭을 점검."""
    import importlib
    cal = importlib.import_module("pytmuxlib.plugins.calendar")

    class _Ev:
        def __init__(self, key, character=None):
            self.key = key
            self.character = character

    class _App:
        def __init__(self):
            self.calendar_panes = {1}
            self.calendar_offset = {1: -3}     # 3달 전을 보던 중
            self.layout = {"active": 1}
            self.composited = 0

        def _composite(self):
            self.composited += 1

    app = _App()
    # Home → 이번 달(0)로 복귀, 소비(True), 재합성 호출.
    assert cal.PLUGIN.client_overlay_key(app, _Ev("home")) is True
    assert app.calendar_offset[1] == 0, "Home 으로 이번 달 복귀"
    assert app.composited >= 1, "재합성(_composite) 호출"
    # '.' 문자도 동일하게 이번 달로.
    app.calendar_offset[1] = 5
    assert cal.PLUGIN.client_overlay_key(app, _Ev("x", ".")) is True
    assert app.calendar_offset[1] == 0
    # ←/→ 는 이전/다음 달(기존 동작 보존).
    cal.PLUGIN.client_overlay_key(app, _Ev("left"))
    assert app.calendar_offset[1] == -1
    cal.PLUGIN.client_overlay_key(app, _Ev("right"))
    assert app.calendar_offset[1] == 0
    # 활성 패널에 달력이 없으면 소비하지 않음(False) → 키가 셸로 흘러야.
    app.layout = {"active": 99}
    assert cal.PLUGIN.client_overlay_key(app, _Ev("home")) is False
    app.layout = {"active": 1}
    app.calendar_panes = set()
    assert cal.PLUGIN.client_overlay_key(app, _Ev("home")) is False
