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


def draw_ime_indicator(cells, W, H, label, st, y=0, reserve_right=4):
    """행 `y` 의 오른쪽 끝에 `[label]` 배지를 우측정렬로 그린다.

    배치(2026-06-11 사용자 요청으로 우상단 고정 → 커서 줄로 변경): 기본은 **커서가
    있는 줄**(호출부가 y 로 전달)의 오른쪽 끝 — 조합(preedit)이 보이는 커서 줄과 같은
    높이라 한/영 상태를 시선 이동 없이 확인한다. `reserve_right` 만큼 우측을 비운다 —
    y=0(첫 행)에서 탭 닫기 `[x]`(콘텐츠 우상단, client_render 뒤에 그려짐)와 겹치지
    않게 하기 위함이고, 다른 행엔 [x] 가 없어 호출부가 0 을 넘겨 진짜 오른쪽 끝까지
    쓴다. 행 오른쪽 끝이 패널 테두리(│)면 그 위에 덮는다 — 상단 테두리에 그리던
    기존과 같은 '의도된 오버레이'(_ime_zone 으로 테두리 강조 검사가 예외 처리).
    한글(와이드 2칸)은 본체 셀 + 빈 연속 셀("")로 써 정렬을 보존한다.

    반환: 그린 칸 범위 `(x0, x_end_exclusive)`(테두리 강조 테스트가 이 구간을 [x] 처럼
    예외로 두게 함) 또는 폭 부족·y 범위 밖으로 생략하면 `None`."""
    if not (0 <= y < H):
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
            cells[y][cx] = (ch, st)
            if cw == 2 and cx + 1 < W:
                cells[y][cx + 1] = ("", st)   # 와이드 연속 셀
        cx += cw
    return (x0, x_end)
