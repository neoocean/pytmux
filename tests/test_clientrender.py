"""clientrender.py 자유함수(셀 그리드 합성) 회귀 — #12 추출 가드.

`put_cell` 은 앱 상태 비의존 범용 그리드 프리미티브(코어 client 와 시계·달력
플러그인이 공유)라 앱·소켓 없이 직접 호출해 셀 그리드 출력을 단언한다. 시계·달력
오버레이 그리기는 각 플러그인 render.py 로 옮겼고 회귀는 test_plugin_clock_render.py /
test_plugin_calendar_render.py 가 가드한다.
"""
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
