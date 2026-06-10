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
