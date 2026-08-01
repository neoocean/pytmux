"""calendar 플러그인의 셀 그리드 합성 — **런 생성기의 소비자**.

그림을 정하는 일은 여기 없다. 어느 단의 폰트로 어디에 무엇을 쓸지는 `cells.py` 가
한 벌로 정하고(그 한 벌을 네이티브 클라도 `plugin_cells` 로 받는다), 이 모듈은 그
런을 정본의 셀 격자에 얹는다 — 딤과 색 해석처럼 **화면을 들고 있는 쪽만 할 수 있는
일**만 남는다.

`client_overlay` 훅에서만 지연 import 되므로(서버 프로세스는 이 모듈을 읽지 않는다)
clientrender/clientutil 의 헬퍼를 모듈 최상단에서 import 해도 안전하다. 회귀는
`tests/test_plugin_calendar_render.py` 가 셀 그리드 출력 불변으로 가드한다."""
from __future__ import annotations

from pytmuxlib.clientrender import dim_pane, put_cell
from pytmuxlib.clientutil import _dim_cell

from .cells import calendar_cells


def _style(run, theme):
    """런의 축약 스타일 + 의미 색 → rich Style.

    **색의 권위는 클라 테마**다 — 런은 이름만 싣고(`success`·`foreground`) 실제 색은
    여기서 `theme(name)` 으로 푼다. 이름이 없는 자리는 런에 실린 리터럴(`black`)을
    쓴다."""
    from rich.style import Style
    st = run.get("style") or {}
    th = run.get("theme") or {}
    fg = theme(th["f"]) if "f" in th else st.get("f")
    bg = theme(th["b"]) if "b" in th else st.get("b")
    return Style(color=fg, bgcolor=bg, bold=bool(st.get("bo")))


def draw_calendar_overlay(cells, panes, calendar_panes, W, H, theme, now=None,
                          offsets=None, nav_zones=None):
    """달력 모드 패널을 달력으로 덮는다(clock-mode 미러). 뒤의 패널 출력은 흐리게(dim)
    계속 보이고, 오늘 날짜는 강조.

    `theme`=의미 색 이름을 실제 색으로 푸는 함수(`lambda n: theme_color(app, n)`),
    `now`=기준 datetime(테스트 결정성용; None 이면 현재), `offsets`=패널 id→표시할
    월의 '이번 달' 기준 오프셋(없으면 0=이번 달). `nav_zones`=주어지면 패널 id→
    `[(x0, x1, y, delta), …]` 로 `‹`/`›` 클릭 영역을 채운다(코어 마우스 핸들러가
    클릭을 delta 만큼 월 이동으로 디스패치; 단순 날짜 폴백엔 화살표가 없어 zone 도
    없다)."""
    if not calendar_panes:
        return
    on = [p for p in panes if p["id"] in calendar_panes]
    # 1) 뒤 화면 흐리게(실색 블렌드 — §10, 터미널 무관 균일). 컬러 이모지는 스타일을
    # 무시하고 밝게 남으므로 _dim_cell 이 placeholder(·)로 치환한다(#25).
    for p in on:
        dim_pane(cells, p["x"], p["y"], p["w"], p["h"], W, H, _dim_cell)
    # 2) 런을 얹는다 — 자리·글자·의미 색은 cells.py 가 정한 그대로다.
    runs, zones = calendar_cells(on, {pid: {"offset": off}
                                      for pid, off in (offsets or {}).items()},
                                 now=now)
    for run in runs:
        st = _style(run, theme)
        for i, ch in enumerate(run["text"]):
            put_cell(cells, run["x"] + i, run["y"], ch, st, W, H)
    # 3) 클릭존은 코어 마우스 핸들러의 표기로 옮긴다(delta = 이 클릭이 옮길 달 수).
    if nav_zones is not None:
        for z in zones:
            nav_zones.setdefault(z["pane"], []).append(
                (z["x"], z["x"] + z["w"], z["y"],
                 -1 if z["do"] == "prev" else 1))
