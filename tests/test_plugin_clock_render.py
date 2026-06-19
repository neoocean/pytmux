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


async def test_clock_overlay_dims_background_emoji_to_placeholder():
    """배경 화면을 딤할 때 컬러 이모지(예 ✅)는 터미널이 스타일을 무시하고 밝게 남으므로
    오버레이 딤이 placeholder(·)로 치환해야 한다(#25). 시계가 큰 폰트로 안 덮는 모서리에
    둔 이모지가 ·로 바뀌고, 시계 영역 밖(다른 패널)의 이모지는 보존됨을 단언한다."""
    now = datetime(2026, 6, 6, 12, 34, 56)
    digit = Style(color="green", bold=True)
    # 패널 1=시계 켜짐(딤 대상), 패널 2=시계 꺼짐(보존). ✅=U+2705.
    panes = [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 3},
             {"id": 2, "x": 10, "y": 0, "w": 6, "h": 3}]
    cells = _grid(16, 3)
    cells[2][0] = ("✅", Style())     # 시계 패널 좌하단(폴백 시각이 안 닿는 칸)
    cells[0][12] = ("✅", Style())    # 시계 안 켠 패널 — 보존돼야 함
    draw_clock_overlay(cells, panes, {1}, 16, 3, digit, now=now)
    assert cells[2][0][0] == "·", "딤된 시계 패널의 이모지는 ·로 치환"
    assert cells[0][12][0] == "✅", "시계 안 켠 패널의 이모지는 보존"
