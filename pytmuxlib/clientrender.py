"""셀 그리드 합성 헬퍼 — 앱 상태 비의존 순수 함수(#12 추출).

client.py 의 거대 클로저(build_client_app)에 갇혀 있던 그리기 헬퍼를 모듈 자유함수로
빼냈다(docs/internal/HANDOFF §11.4 / IMPROVEMENT #12). 셀 그리드(cells)를 in-place 로 다루는
순수 함수라 앱 인스턴스가 필요 없다. 회귀는 ptyshot 골든(`tests/test_ptyshot.py`)이
화면 출력 불변으로 가드한다.

여기 남은 `put_cell` 은 시계/달력뿐 아니라 코어 client 도 쓰는 범용 그리드
프리미티브다. 시계·달력 전용 오버레이 그리기(`draw_clock_overlay`/
`draw_calendar_overlay`)는 각 플러그인의 `render.py`(plugins/clock·calendar)로 옮겼다
— 디렉토리를 지우면 그 그리기 코드도 함께 사라지는 완전한 delete-to-disable."""
from __future__ import annotations

from .clientutil import _char_cells


def put_cell(cells, x, y, ch, st, W, H):
    """단일폭 글자를 cell 그리드에 정렬을 깨지 않고 써넣는다.

    배경에 한글 등 와이드 문자(2칸: 본체+빈 연속셀 "")가 있을 때 그 절반만 덮으면
    짝 셀이 어긋나 행 전체가 밀린다(예: clock-mode 시계가 깨짐). 덮어쓰는 자리의
    와이드 짝 셀을 공백으로 정리해 정렬을 보존한다(오버레이가 배경 글자 일부를
    지우는 것은 의도된 동작)."""
    if not (0 <= x < W and 0 <= y < H):
        return
    row = cells[y]
    if row[x][0] == "" and x > 0:
        # 이 자리가 와이드 문자의 둘째(연속) 칸 → 왼쪽 본체를 공백으로.
        row[x - 1] = (" ", row[x - 1][1])
    elif _char_cells(row[x][0]) == 2 and x + 1 < W and row[x + 1][0] == "":
        # 이 자리가 와이드 문자의 본체 → 오른쪽 연속 칸을 공백으로.
        row[x + 1] = (" ", row[x + 1][1])
    row[x] = (ch, st)


def dim_pane(cells, px, py, pw, ph, W, H, cell_fn):
    """오버레이(clock/calendar/usage) 뒤 패널 영역을 흐리게 하는 공통 프리앰블(1-8).

    세 오버레이가 글자 단위로 복제하던 이중 루프를 한 곳으로 모은다. cell_fn=(ch, st)
    → (ch, st) 셀 변환을 받는다(clock/calendar 는 _dim_cell 로 컬러 이모지를 placeholder
    치환, usage 는 _darken_style 로 균일 dim). 패널 rect 를 화면 경계(W,H)로 클램프."""
    for yy in range(py, min(py + ph, H)):
        row = cells[yy]
        for xx in range(px, min(px + pw, W)):
            c, st = row[xx]
            row[xx] = cell_fn(c, st)
