"""clock 플러그인의 셀 그리드 합성 — **런 생성기의 소비자**.

그림을 정하는 일은 여기 없다. 어느 단의 폰트로 어디에 무엇을 쓸지는 `cells.py` 가
한 벌로 정하고(그 한 벌을 네이티브 클라도 `plugin_cells` 로 받는다), 이 모듈은 그
런을 정본의 셀 격자에 얹는다 — 그것도 직접 하지 않는다: 얹는 일 자체가 세 오버레이에
공통이라 `clientrender.paint_runs` 한 곳으로 접었다(2026-08-02g). 여기 남는 것은
**이 오버레이만의 것**뿐이다 — 어느 패널을 덮고, 뒤를 어떻게 흐리게 하나.

2026-08-02d 이전에는 여기가 **두 번째 규칙**이었다(서버 `plugin_cells` 가 같은 판정을
다시 적고 있었다). 합치기 직전 두 경로의 그림이 세 크기에서 칸 단위로 같음을 확인했고,
합친 뒤로는 **모양 자체**를 `tests/test_plugin_clock_render.py` 의 그림 골든이 지킨다.

`client_overlay` 훅에서만 지연 import 되므로(서버 프로세스는 이 모듈을 읽지 않는다)
clientrender/clientutil 의 헬퍼를 모듈 최상단에서 import 해도 안전하다."""
from __future__ import annotations

from pytmuxlib.clientrender import dim_panes, paint_runs
from pytmuxlib.clientutil import _dim_cell

from .cells import clock_cells


def draw_clock_overlay(cells, panes, clock_panes, W, H, theme, now=None):
    """clock-mode 패널을 큰 시계로 덮는다. 뒤의 패널 출력은 흐리게(dim) 계속 보인다.

    `panes`=레이아웃 패널 rect 목록, `clock_panes`=시계 켠 패널 id 집합,
    `theme`=의미 색 이름을 실제 색으로 푸는 함수(`lambda n: theme_color(app, n)`),
    `now`=시각 datetime(테스트 결정성용; None 이면 현재 시각).

    글자 크기 판정(큰 폰트 → 반칸 폰트 → 단순 시각)은 여기가 아니라 `cells.py` 다."""
    if not clock_panes:
        return
    on = [p for p in panes if p["id"] in clock_panes]
    # 뒤 화면 흐리게(실색 블렌드 — §10, 터미널 무관 균일). 컬러 이모지는 스타일을
    # 무시하고 밝게 남으므로 _dim_cell 이 placeholder(·)로 치환한다(#25).
    dim_panes(cells, on, W, H, _dim_cell)
    paint_runs(cells, clock_cells(on, now=now), W, H, theme)
