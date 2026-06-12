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


# ---- §2.9 비활성 패널 dim 스타일 ----
async def test_dim_inactive_style_blends_darker_preserving_attrs():
    """`_dim_inactive_style` 은 전경/배경 실색을 검정 쪽으로 ratio 만큼 블렌드해 한 톤
    옅게 만들고(터미널 의존 ANSI dim 대신), bold/italic 등 다른 속성은 보존한다. 기본
    전경(None)은 평문이 새카매지지 않게 중간 회색 폴백을 둔다."""
    from pytmuxlib.clientutil import _dim_inactive_style
    st = Style(color="#ffffff", bgcolor="#202020", bold=True)
    d = _dim_inactive_style(st, 0.30)
    # 전경 255 → 30% 어두워진 178, 배경도 어두워짐, bold 보존
    assert d.color.get_truecolor().red == 178, d.color.get_truecolor()
    assert d.bgcolor.get_truecolor().red < 0x20, d.bgcolor.get_truecolor()
    assert d.bold is True, "bold 등 속성 보존"
    # 세기가 클수록 더 어둡다(단조)
    assert _dim_inactive_style(st, 0.50).color.get_truecolor().red < 178
    # 기본 전경(None) → 회색 폴백(새카맣지 않게), 0 이면 사실상 무변(폴백만)
    d0 = _dim_inactive_style(Style(), 0.30)
    assert d0.color is not None and d0.color.get_truecolor().red > 0, "평문 폴백 회색"
