"""ime-indicator 플러그인의 셀 그리드 배지 그리기 — 앱 상태 비의존 순수 함수.

`client_render` 훅에서만 지연 import 되므로(서버 프로세스는 이 모듈을 읽지 않는다)
clientutil 헬퍼를 모듈 최상단에서 import 해도 안전하다. 디렉토리를 통째로 지우면 이
그리기 코드도 함께 사라진다(delete-to-disable). 회귀는
`tests/test_plugin_ime_indicator.py` 가 셀 그리드 출력으로 가드한다."""
from __future__ import annotations

from pytmuxlib.clientutil import _char_cells


def _text_width(s: str) -> int:
    """문자열의 표시 폭(한글 등 와이드=2칸)."""
    return sum(_char_cells(c) for c in s)


def draw_ime_indicator(cells, W, H, label, st, reserve_right=4):
    """화면 우상단(첫 행 y=0)에 `[label]` 배지를 우측정렬로 그린다.

    배치: 다중 패널이면 첫 행은 최상단 패널의 **상단 테두리 선**이고, 단일 패널(무테)이면
    콘텐츠 첫 행이다. 어느 쪽이든 우측 `reserve_right` 칸은 비워 둔다 — 무테 단일 패널에서
    탭 닫기 `[x]`(콘텐츠 우상단, client_render 뒤에 그려져 겹치면 덮어씀)와 겹치지 않게
    하기 위함이다. 한글(와이드 2칸)은 본체 셀 + 빈 연속 셀("")로 써 정렬을 보존한다.

    반환: 그린 칸 범위 `(x0, x_end_exclusive)`(테두리 강조 테스트가 이 구간을 [x] 처럼
    예외로 두게 함) 또는 폭 부족/높이 부족으로 생략하면 `None`."""
    if H < 1:
        return None
    text = "[" + label + "]"
    w = _text_width(text)
    x_end = W - reserve_right        # 배지가 차지할 오른쪽 경계(exclusive)
    x0 = x_end - w
    if x0 < 0:
        return None                  # 폭 부족 → 배지 생략
    cx = x0
    for ch in text:
        cw = _char_cells(ch)
        if 0 <= cx < W:
            cells[0][cx] = (ch, st)
            if cw == 2 and cx + 1 < W:
                cells[0][cx + 1] = ("", st)   # 와이드 연속 셀
        cx += cw
    return (x0, x_end)
