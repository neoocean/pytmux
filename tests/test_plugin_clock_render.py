"""clock 플러그인 render.py(큰 시계 오버레이) 회귀 — 완전 격리 후 가드.

`draw_clock_overlay` 는 앱 상태 비의존 순수 함수라 앱·소켓 없이 직접 호출해 셀 그리드
출력을 단언한다. clientrender.py 에서 plugins/clock/render.py 로 옮긴 뒤(완전한
delete-to-disable) 화면 출력이 불변임을 고정한다.
"""
from datetime import datetime

import harness  # noqa: F401  (경로 설정)
from rich.style import Style

from pytmuxlib.plugins.clock.render import draw_clock_overlay


def _grid(w, h):
    """(char, style) 셀 h×w 그리드를 공백으로 초기화."""
    base = Style()
    return [[(" ", base) for _ in range(w)] for _ in range(h)]


def _text_rows(cells):
    """셀 그리드를 글자만 뽑아 행 문자열 리스트로(스타일 무시, 배치 검증용)."""
    return ["".join(c[0] for c in row) for row in cells]


async def test_clock_overlay_big_and_fallback():
    now = datetime(2026, 6, 6, 12, 34, 56)
    digit = Style(color="green", bold=True)
    panes = [{"id": 1, "x": 0, "y": 0, "w": 60, "h": 10}]
    # 큰 시계: 클럭 폰트가 들어갈 공간 → 글자가 여러 행에 그려진다(공백 아닌 셀 다수)
    cells = _grid(60, 10)
    draw_clock_overlay(cells, panes, {1}, 60, 10, digit, now=now)
    filled = sum(1 for row in cells for c in row if c[0] not in (" ", ""))
    assert filled > 12, filled    # 8글자×5행 폰트의 획들
    # clock_panes 에 없으면 무동작
    cells2 = _grid(60, 10)
    draw_clock_overlay(cells2, panes, set(), 60, 10, digit, now=now)
    assert all(c[0] == " " for row in cells2 for c in row)
    # 좁은 패널 → 단순 시각 문자열 폴백("12:34:56" 한 줄)
    small = [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 3}]
    cells3 = _grid(10, 3)
    draw_clock_overlay(cells3, small, {1}, 10, 3, digit, now=now)
    joined = "".join(_text_rows(cells3))
    assert "12:34:56" in joined
