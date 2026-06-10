"""clock 플러그인의 셀 그리드 합성 헬퍼 — 앱 상태 비의존 순수 함수.

원래 `pytmuxlib/clientrender.py` 의 자유함수였으나(IMPROVEMENT #12 추출), 시계 기능
전체를 이 플러그인 디렉토리 안으로 모으는 delete-to-disable 마무리(완전 격리)로 여기로
옮겼다. 디렉토리를 통째로 지우면 이 그리기 코드도 함께 사라지고 코어에 죽은 코드가
남지 않는다 — `put_cell`(코어 client 도 쓰는 범용 그리드 프리미티브)과 `_CLOCK_FONT`
(시계·달력 두 플러그인이 공유하는 3×5 블록 폰트 자산)만 코어 공용 모듈에 남는다.

`client_overlay` 훅에서만 지연 import 되므로(서버 프로세스는 이 모듈을 읽지 않는다)
clientrender/clientutil 의 헬퍼를 모듈 최상단에서 import 해도 안전하다. 회귀는
`tests/test_plugin_clock_render.py` 가 셀 그리드 출력 불변으로 가드한다."""
from __future__ import annotations

from datetime import datetime as _datetime

from pytmuxlib.clientrender import put_cell
from pytmuxlib.clientutil import _CLOCK_FONT, _darken_style


def draw_clock_overlay(cells, panes, clock_panes, W, H, digit_st, now=None):
    """clock-mode 패널을 큰 시계로 덮는다. 뒤의 패널 출력은 흐리게(dim) 계속 보인다.
    `panes`=레이아웃 패널 rect 목록, `clock_panes`=시계 켠 패널 id 집합,
    `digit_st`=숫자 Style(호출부가 theme 로 해석). `now`=시각 datetime(테스트
    결정성용; None 이면 현재 시각). 큰 시계 공간이 부족하면 단순 시각 문자열로
    폴백한다."""
    if not clock_panes:
        return
    now = now or _datetime.now()
    text = now.strftime("%H:%M:%S")
    glyphs = [_CLOCK_FONT.get(c, ["   "] * 5) for c in text]
    cw = sum(len(g[0]) for g in glyphs) + (len(glyphs) - 1)
    ch_h = 5
    for p in panes:
        if p["id"] not in clock_panes:
            continue
        px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]
        # 1) 뒤 화면 흐리게(실색 블렌드 — §10, 터미널 무관 균일)
        for yy in range(py, min(py + ph, H)):
            for xx in range(px, min(px + pw, W)):
                c, st = cells[yy][xx]
                cells[yy][xx] = (c, _darken_style(st))
        # 2) 큰 시계(공간 충분) 또는 단순 시각
        if pw >= cw and ph >= ch_h:
            ox = px + (pw - cw) // 2
            oy = py + (ph - ch_h) // 2
            for row in range(ch_h):
                gx = ox
                for g in glyphs:
                    for c in g[row]:
                        if c != " ":
                            put_cell(cells, gx, oy + row, c, digit_st, W, H)
                        gx += 1
                    gx += 1   # 글자 사이 간격
        else:
            ox = px + max(0, (pw - len(text)) // 2)
            oy = py + ph // 2
            for j, c in enumerate(text):
                put_cell(cells, ox + j, oy, c, digit_st, W, H)
