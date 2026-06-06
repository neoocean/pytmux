"""clientrender.py 자유함수(셀 그리드 합성) 회귀 — #12 추출 가드.

put_cell / draw_clock_overlay / draw_calendar_overlay 는 앱 상태 비의존 순수
함수라 앱·소켓 없이 직접 호출해 셀 그리드 출력을 단언한다. client.py 거대 클로저
에서 빼낸 뒤(IMPROVEMENT #12) 화면 출력이 불변임을 고정한다.
"""
from datetime import datetime

import harness  # noqa: F401  (경로 설정)
from rich.style import Style

from pytmuxlib import clientrender


def _grid(w, h):
    """(char, style) 셀 h×w 그리드를 공백으로 초기화."""
    base = Style()
    return [[(" ", base) for _ in range(w)] for _ in range(h)]


def _text_rows(cells):
    """셀 그리드를 글자만 뽑아 행 문자열 리스트로(스타일 무시, 배치 검증용)."""
    return ["".join(c[0] for c in row) for row in cells]


# ---- put_cell ----
async def test_put_cell_clamps_and_wide_char_alignment():
    W, H = 6, 1
    cells = _grid(W, H)
    st = Style()
    clientrender.put_cell(cells, 0, 0, "x", st, W, H)
    assert cells[0][0][0] == "x"
    # 범위 밖은 무동작(예외 없이 무시)
    clientrender.put_cell(cells, 99, 0, "y", st, W, H)
    clientrender.put_cell(cells, -1, 0, "y", st, W, H)
    assert _text_rows(cells)[0] == "x     "
    # 와이드(한글) 본체 위에 단일폭을 쓰면 짝 연속칸("")을 공백으로 정리해 밀림 방지
    cells = _grid(W, H)
    cells[0][1] = ("한", st)
    cells[0][2] = ("", st)        # 와이드 연속칸
    clientrender.put_cell(cells, 1, 0, "A", st, W, H)
    assert cells[0][1][0] == "A" and cells[0][2][0] == " "


# ---- 시계 오버레이 ----
async def test_clock_overlay_big_and_fallback():
    now = datetime(2026, 6, 6, 12, 34, 56)
    digit = Style(color="green", bold=True)
    panes = [{"id": 1, "x": 0, "y": 0, "w": 60, "h": 10}]
    # 큰 시계: 클럭 폰트가 들어갈 공간 → 글자가 여러 행에 그려진다(공백 아닌 셀 다수)
    cells = _grid(60, 10)
    clientrender.draw_clock_overlay(cells, panes, {1}, 60, 10, digit, now=now)
    filled = sum(1 for row in cells for c in row if c[0] not in (" ", ""))
    assert filled > 12, filled    # 8글자×5행 폰트의 획들
    # clock_panes 에 없으면 무동작
    cells2 = _grid(60, 10)
    clientrender.draw_clock_overlay(cells2, panes, set(), 60, 10, digit, now=now)
    assert all(c[0] == " " for row in cells2 for c in row)
    # 좁은 패널 → 단순 시각 문자열 폴백("12:34:56" 한 줄)
    small = [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 3}]
    cells3 = _grid(10, 3)
    clientrender.draw_clock_overlay(cells3, small, {1}, 10, 3, digit, now=now)
    joined = "".join(_text_rows(cells3))
    assert "12:34:56" in joined


# ---- 달력 오버레이 ----
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
    clientrender.draw_calendar_overlay(cells, panes, {1}, 30, 12,
                                       _cal_styles(), now=now)
    joined = "".join(_text_rows(cells))
    assert "2026-06" in joined                      # 제목
    assert "Mo" in joined and "Su" in joined        # 요일 헤더
    # 오늘(6일)은 today 스타일(bgcolor=green)로 칠해진 셀이 있어야 한다
    today_cells = [c for row in cells for c in row
                   if c[0] == "6" and c[1].bgcolor is not None]
    assert today_cells, "오늘 날짜 강조 셀 없음"
    # calendar_panes 비면 무동작
    cells2 = _grid(30, 12)
    clientrender.draw_calendar_overlay(cells2, panes, set(), 30, 12,
                                       _cal_styles(), now=now)
    assert all(c[0] == " " for row in cells2 for c in row)


async def test_calendar_overlay_small_pane_falls_back_to_date_string():
    now = datetime(2026, 6, 6)
    panes = [{"id": 1, "x": 0, "y": 0, "w": 12, "h": 3}]
    cells = _grid(12, 3)
    clientrender.draw_calendar_overlay(cells, panes, {1}, 12, 3,
                                       _cal_styles(), now=now)
    assert "2026-06-06" in "".join(_text_rows(cells))
