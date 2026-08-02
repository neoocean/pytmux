"""usage-view 현재-패널 오버레이 — **런 생성기의 소비자**(clock/calendar render.py 미러).

그림을 정하는 일은 여기 없다. 어느 줄을 어디에 쓸지는 `cells.py` 가 한 벌로 정하고
(그 한 벌을 네이티브 클라도 `plugin_cells` 로 받는다), 이 모듈은 그 런을 정본의 셀
격자에 얹는다 — 얹는 일 자체는 세 오버레이 공통이라 `clientrender.paint_runs` 로
접었다(2026-08-02g). 여기 남는 것은 **이 오버레이만의 것**이다: 어느 패널을 덮나 ·
뒤를 어떻게 흐리게 하나(시계·달력은 이모지를 placeholder 로 바꾸고, 여기는 균일 dim).

`client_overlay` 훅에서만 지연 import 되므로(서버는 안 읽음) clientrender/clientutil
헬퍼를 최상단에서 import 해도 안전하다."""
from __future__ import annotations

from pytmuxlib.clientrender import dim_panes, paint_runs
from pytmuxlib.clientutil import _darken_style

from .cells import usage_cells


def draw_usage_overlay(cells, panes, view_panes, W, H, theme, usage,
                       age_sec=None, now=None):
    """usage-view 가 켜진 패널을 한도 막대 + 다음 리셋 카운트다운으로 덮는다(뒤는 dim).

    `theme`=의미 색 이름을 실제 색으로 푸는 함수(`lambda n: theme_color(app, n)`),
    `usage` 가 없으면 안내 한 줄만 그린다(빈 화면 금지). `now` 는 테스트 결정성용."""
    if not view_panes:
        return
    on = [p for p in panes if p["id"] in view_panes]
    # 뒤 화면 흐리게(실색 블렌드 — 터미널 무관 균일, clock/calendar 와 동일).
    dim_panes(cells, on, W, H, lambda c, st: (c, _darken_style(st)))
    paint_runs(cells, usage_cells(on, usage, age_sec=age_sec, now=now),
               W, H, theme)
