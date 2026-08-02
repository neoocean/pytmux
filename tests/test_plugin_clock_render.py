"""clock 플러그인 render.py(큰 시계 오버레이) 회귀 — 완전 격리 후 가드.

`draw_clock_overlay` 는 앱 상태 비의존 순수 함수라 앱·소켓 없이 직접 호출해 셀 그리드
출력을 단언한다. clientrender.py 에서 plugins/clock/render.py 로 옮긴 뒤(완전한
delete-to-disable) 화면 출력이 불변임을 고정한다.

2026-08-02d 부터 render.py 는 **런 생성기(`cells.py`)의 소비자**다 — 그림을 정하는
규칙이 한 벌이 되면서 「같은 입력, 두 경로」 대조가 잴 것을 잃었다(둘 다 같이 틀리면
대조는 조용하다). 그래서 재는 것을 **모양 자체**로 옮겼다:
`test_the_drawn_clock_is_pinned_to_a_golden`(달력 골든과 같은 자리).
"""
from datetime import datetime

import harness  # noqa: F401  (경로 설정)
from rich.style import Style

from pytmuxlib.plugins.clock.render import draw_clock_overlay

# 의미 색 이름 → 실제 색. 정본은 `theme_color(app, name)` 을 넘긴다(클라 테마가 권위).
_theme = {"success": "green", "foreground": "white"}.get


def _grid(w, h):
    """(char, style) 셀 h×w 그리드를 공백으로 초기화."""
    base = Style()
    return [[(" ", base) for _ in range(w)] for _ in range(h)]


def _text_rows(cells):
    """셀 그리드를 글자만 뽑아 행 문자열 리스트로(스타일 무시, 배치 검증용)."""
    return ["".join(c[0] for c in row) for row in cells]


async def test_clock_overlay_big_and_fallback():
    now = datetime(2026, 6, 6, 12, 34, 56)
    panes = [{"id": 1, "x": 0, "y": 0, "w": 60, "h": 10}]
    # 큰 시계: 클럭 폰트가 들어갈 공간 → 글자가 여러 행에 그려진다(공백 아닌 셀 다수)
    cells = _grid(60, 10)
    draw_clock_overlay(cells, panes, {1}, 60, 10, _theme, now=now)
    filled = sum(1 for row in cells for c in row if c[0] not in (" ", ""))
    assert filled > 12, filled    # 8글자×3행 폰트의 획들
    # clock_panes 에 없으면 무동작
    cells2 = _grid(60, 10)
    draw_clock_overlay(cells2, panes, set(), 60, 10, _theme, now=now)
    assert all(c[0] == " " for row in cells2 for c in row)
    # 좁은 패널 → 단순 시각 문자열 폴백("12:34:56" 한 줄)
    small = [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 3}]
    cells3 = _grid(10, 3)
    draw_clock_overlay(cells3, small, {1}, 10, 3, _theme, now=now)
    joined = "".join(_text_rows(cells3))
    assert "12:34:56" in joined


async def test_the_drawn_clock_is_pinned_to_a_golden():
    """**그림 자체를 못박는다** — 자리 셈이 한 벌이 된 뒤로는(`cells.py`) 정본과
    네이티브 클라가 같은 런을 받으므로, 그림이 바뀌면 두 클라가 **함께** 바뀐다.
    그러면 "두 경로 대조" 오라클로는 아무것도 안 잡힌다(둘 다 같이 틀린다).

    아래 두 판은 통합 **직전**의 그림을 그대로 뜬 것이다(2026-08-02d 실측) — 이 통합이
    모양을 안 바꿨다는 증거이자, 앞으로 규칙을 고칠 때 따라와야 할 기준이다.
    글자 사이 한 칸·중앙 정렬·폴백 한 줄까지 포함한 한 판."""
    now = datetime(2026, 6, 6, 12, 34, 56)

    def drawn(w, h):
        cells = _grid(w, h)
        draw_clock_overlay(cells, [{"id": 1, "x": 0, "y": 0, "w": w, "h": h}],
                           {1}, w, h, _theme, now=now)
        return _text_rows(cells)

    # ① 반칸 폰트(3행) — 40×6
    assert drawn(40, 6) == [
        "                                        ",
        "      █ ▀▀█  ▄  ▀▀█ █ █  ▄  █▀▀ █▀▀     ",
        "      █ █▀▀  ▄  ▀▀█ ▀▀█  ▄  ▀▀█ █▀█     ",
        "      ▀ ▀▀▀     ▀▀▀   ▀     ▀▀▀ ▀▀▀     ",
        "                                        ",
        "                                        ",
    ], "\n".join(drawn(40, 6))
    # ② 폰트가 안 들어가는 판 — 단순 시각 한 줄(가운데)
    assert drawn(14, 3) == [
        "              ",
        "   12:34:56   ",
        "              ",
    ], "\n".join(drawn(14, 3))


async def test_clock_overlay_grows_to_full_cell_font_on_big_pane():
    """화면이 넉넉하면 시계가 **한 칸 높이 픽셀**의 큰 폰트로 커진다(요청 2026-07-26).

    종전 폰트는 반칸 글자(`▀`/`▄`)로 5 픽셀행을 3 행에 욱여넣어, 패널이 아무리 커도
    글자가 그 이상 커지지 않았다. 이제 공간이 되면 5행·글자 6칸짜리 전각 블록(`█`)으로
    그린다 — 오라클은 **글자 자체**를 본다: 큰 폰트는 반칸 글자가 하나도 없고 획이
    5행에 걸치며, 좁은 패널은 종전대로 반칸 3행이어야 한다(폴백 보존)."""
    now = datetime(2026, 6, 6, 12, 34, 56)

    def drawn_rows(w, h):
        cells = _grid(w, h)
        draw_clock_overlay(cells, [{"id": 1, "x": 0, "y": 0, "w": w, "h": h}],
                           {1}, w, h, _theme, now=now)
        rows = _text_rows(cells)
        return rows, [r for r in rows if r.strip()]

    # ① 큰 패널: 전각 블록만 · 획이 5행
    rows, filled = drawn_rows(70, 12)
    joined = "".join(rows)
    assert "█" in joined and "▀" not in joined and "▄" not in joined, joined
    assert len(filled) == 5, filled
    # 폭도 커진다 — 글자 8개 × 6칸 + 간격 7 = 55칸(반칸 폰트는 31칸)
    span = max(len(r.rstrip()) - (len(r) - len(r.lstrip())) for r in filled)
    assert span >= 50, (span, filled)

    # ② 큰 폰트가 안 들어가는 패널: 종전 반칸 3행 폰트로 폴백
    rows2, filled2 = drawn_rows(40, 6)
    joined2 = "".join(rows2)
    assert "▀" in joined2, joined2
    assert len(filled2) == 3, filled2

    # ③ 그마저 안 되면 단순 시각 문자열(종전 폴백 유지)
    rows3, _ = drawn_rows(12, 3)
    assert "12:34:56" in "".join(rows3)


async def test_clock_overlay_dims_background_emoji_to_placeholder():
    """배경 화면을 딤할 때 컬러 이모지(예 ✅)는 터미널이 스타일을 무시하고 밝게 남으므로
    오버레이 딤이 placeholder(·)로 치환해야 한다(#25). 시계가 큰 폰트로 안 덮는 모서리에
    둔 이모지가 ·로 바뀌고, 시계 영역 밖(다른 패널)의 이모지는 보존됨을 단언한다."""
    now = datetime(2026, 6, 6, 12, 34, 56)
    # 패널 1=시계 켜짐(딤 대상), 패널 2=시계 꺼짐(보존). ✅=U+2705.
    panes = [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 3},
             {"id": 2, "x": 10, "y": 0, "w": 6, "h": 3}]
    cells = _grid(16, 3)
    cells[2][0] = ("✅", Style())     # 시계 패널 좌하단(폴백 시각이 안 닿는 칸)
    cells[0][12] = ("✅", Style())    # 시계 안 켠 패널 — 보존돼야 함
    draw_clock_overlay(cells, panes, {1}, 16, 3, _theme, now=now)
    assert cells[2][0][0] == "·", "딤된 시계 패널의 이모지는 ·로 치환"
    assert cells[0][12][0] == "✅", "시계 안 켠 패널의 이모지는 보존"
