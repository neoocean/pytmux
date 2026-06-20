"""calendar 플러그인의 셀 그리드 합성 헬퍼 — 앱 상태 비의존 순수 함수.

원래 `pytmuxlib/clientrender.py` 의 자유함수였으나(IMPROVEMENT #12 추출), 달력 기능
전체를 이 플러그인 디렉토리 안으로 모으는 delete-to-disable 마무리(완전 격리)로 여기로
옮겼다. 디렉토리를 통째로 지우면 이 그리기 코드도 함께 사라지고 코어에 죽은 코드가
남지 않는다 — `put_cell`(코어 client 도 쓰는 범용 그리드 프리미티브)과 `_CLOCK_FONT`
(시계·달력 두 플러그인이 공유하는 3×5 블록 폰트 자산)만 코어 공용 모듈에 남는다.

`client_overlay` 훅에서만 지연 import 되므로(서버 프로세스는 이 모듈을 읽지 않는다)
clientrender/clientutil 의 헬퍼를 모듈 최상단에서 import 해도 안전하다. 회귀는
`tests/test_plugin_calendar_render.py` 가 셀 그리드 출력 불변으로 가드한다."""
from __future__ import annotations

import calendar as _calendar
from datetime import datetime as _datetime

from pytmuxlib.clientrender import dim_pane, put_cell
from pytmuxlib.clientutil import _CLOCK_FONT, _dim_cell


def draw_calendar_overlay(cells, panes, calendar_panes, W, H, styles, now=None,
                          offsets=None, nav_zones=None):
    """달력 모드 패널을 달력으로 덮는다(clock-mode 미러). 뒤의 패널 출력은 흐리게(dim)
    계속 보이고 오늘 날짜는 강조. `styles`=해석된 Style dict
    (`day`/`title`/`today`/`big_today`/`border`), `now`=기준 datetime(테스트
    결정성용; None 이면 현재). `offsets`=패널 id→표시할 월의 '이번 달' 기준 오프셋
    (예: -1 이면 지난달, +1 이면 다음달; 없으면 0=이번 달). 오프셋이 0 이 아닌 패널은
    '오늘' 강조가 없다(그 달엔 오늘이 없으므로). 제목은 ‹ YYYY-MM › 으로 좌우 화살표를
    붙여 ←/→ 로 달을 넘길 수 있음을 넌지시 알린다. `nav_zones`=주어지면 패널 id→
    [(x0, x1, y, delta), …] 로 ‹/› 클릭 영역을 채운다(코어 마우스 핸들러가 클릭을
    delta 만큼 월 이동으로 디스패치; 단순 날짜 폴백엔 화살표가 없어 zone 도 없다).
    패널이 아주 크면 시계 폰트로 '큰 달력'(#16), 충분하면 일반 그리드, 좁으면 단순
    날짜 문자열로 단계적 폴백한다."""
    if not calendar_panes:
        return

    def _record_title_zones(pid, tx, oy):
        """제목 ‹ YYYY-MM › 의 좌(‹ )·우( ›) 두 칸을 각각 이전/다음 달 클릭존으로
        기록한다(클릭 타깃을 넉넉히 2칸; 가운데 날짜 숫자와는 안 겹친다)."""
        if nav_zones is None:
            return
        tlen = len(title)
        nav_zones.setdefault(pid, []).extend([
            (tx, tx + 2, oy, -1),                 # "‹ " → 이전 달
            (tx + tlen - 2, tx + tlen, oy, +1),   # " ›" → 다음 달
        ])
    now = now or _datetime.now()
    offsets = offsets or {}
    day_st = styles["day"]
    title_st = styles["title"]
    today_st = styles["today"]
    cur_yr, cur_mo, cur_day = now.year, now.month, now.day
    wds = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]
    for p in panes:
        if p["id"] not in calendar_panes:
            continue
        # 패널별 월 오프셋을 적용해 표시할 연·월을 정한다(이번 달=0). 오프셋이 0이
        # 아니면 그 달엔 오늘이 없으므로 today=0(어떤 날짜와도 안 맞아 강조 없음).
        off = offsets.get(p["id"], 0)
        m0 = cur_yr * 12 + (cur_mo - 1) + off
        yr, mo = m0 // 12, m0 % 12 + 1
        today = cur_day if off == 0 else 0
        weeks = _calendar.Calendar(firstweekday=6).monthdayscalendar(yr, mo)
        title = f"‹ {yr}-{mo:02d} ›"   # ‹ YYYY-MM › (←/→ 넘김 힌트)
        px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]
        # 1) 뒤 화면 흐리게(실색 블렌드 — §10, 터미널 무관 균일). 컬러 이모지는
        # 스타일을 무시하고 밝게 남으므로 _dim_cell 이 placeholder(·)로 치환한다(#25).
        dim_pane(cells, px, py, pw, ph, W, H, _dim_cell)
        # 1.5) 아주 큰 패널이면 시계 폰트(3×5)로 날짜를 큼직하게 — '큰 달력'(#16).
        # 한 날짜칸은 숫자 두 자리(3+1+3=7) + 칸 사이 1, 한 주는 글자 5 + 간격 1.
        # DCW=한 날짜칸 폭(숫자 3 + 자리사이 1 + 숫자 3 = 7 + 여유 1 = 8), DGAP=칸 사이,
        # DIG=자리(숫자) 사이 간격(§10-A #9: 2→1 로 좁혀 한 날짜의 두 자리가
        # 한 덩어리로 읽히게 — 날짜칸 사이 간격 DGAP/DCW 는 그대로라 날짜끼리는
        # 안 붙는다. 두 자리 폭 3+1+3=7 이 DCW(8) 안에서 가운데 정렬된다).
        DCW, DGAP, RHB, DIG = 8, 3, 6, 1   # DGAP↑(날짜칸 사이 더 띄움), DIG↓(자리 사이 좁힘)
        gw_big = 7 * DCW + 6 * DGAP          # 칸 7개 + 사이 간격
        nl_big = 4 + len(weeks) * RHB        # 제목+빈줄+요일+빈줄 + 주×6 (년월↔요일·요일↔날짜 각 한 줄)
        if pw >= gw_big + 2 and ph >= nl_big + 2:
            big_today = styles["big_today"]
            ox = px + (pw - gw_big) // 2
            oy = py + (ph - nl_big) // 2
            tx = ox + (gw_big - len(title)) // 2
            for j, c in enumerate(title):                 # 제목
                put_cell(cells, tx + j, oy, c, title_st, W, H)
            _record_title_zones(p["id"], tx, oy)          # ‹/› 클릭존
            for col, wd in enumerate(wds):                # 요일(칸 중앙, 년월 아래 한 줄 띄움)
                hx = ox + col * (DCW + DGAP) + (DCW - len(wd)) // 2
                for k, c in enumerate(wd):
                    put_cell(cells, hx + k, oy + 2, c, day_st, W, H)
            for wi, week in enumerate(weeks):             # 주별 날짜(큰 글자)
                ry = oy + 4 + wi * RHB                     # +4: 년월↔요일·요일↔날짜 빈 줄 각 한 칸
                for col, day in enumerate(week):
                    if not day:
                        continue
                    st = big_today if day == today else day_st
                    s = str(day)
                    gw = len(s) * 3 + (len(s) - 1) * DIG
                    gx0 = ox + col * (DCW + DGAP) + (DCW - gw) // 2
                    for di, ch in enumerate(s):
                        glyph = _CLOCK_FONT.get(ch, ["   "] * 5)
                        dx = gx0 + di * (3 + DIG)
                        for r, gl in enumerate(glyph):
                            for k, gc in enumerate(gl):
                                if gc != " ":
                                    put_cell(cells, dx + k, ry + r,
                                             gc, st, W, H)
            continue                                      # 큰 달력 완료(외곽선 없음)
        # 2) 칸 폭(colw)·주 간격(rowh)을 가용 공간에 맞춰 키운다 — 넓고 높은
        # 화면일수록 큰 달력(사용자 요청). 한 칸은 숫자 2 + 여백이라 colw≥3.
        # grid_w = 6칸 간격 + 마지막 칸 숫자 2. 외곽선 패딩 2칸을 여유로 둔다.
        colw, rowh = 4, 1               # 시작 4 → 날짜 사이 최소 2칸 여백
        while colw < 8 and pw >= (6 * (colw + 1) + 2) + 2:
            colw += 1
        while rowh < 3 and ph >= (3 + (len(weeks) - 1) * (rowh + 1) + 1) + 2:
            rowh += 1
        grid_w = 6 * colw + 2
        # 제목 + 빈줄 + 요일 + 빈줄 + 주(첫 주 1줄 + 이후 rowh) — 년월↔요일·요일↔날짜 각 한 줄
        nlines = 4 + (len(weeks) - 1) * rowh + 1
        # 3) 달력 그리드(공간 충분) 또는 단순 날짜
        if pw >= grid_w and ph >= nlines:
            ox = px + (pw - grid_w) // 2
            oy = py + (ph - nlines) // 2
            tx = ox + (grid_w - len(title)) // 2
            for j, c in enumerate(title):       # 제목(YYYY-MM, 중앙)
                put_cell(cells, tx + j, oy, c, title_st, W, H)
            _record_title_zones(p["id"], tx, oy)   # ‹/› 클릭존
            for col, wd in enumerate(wds):       # 요일 헤더(칸 간격 colw, 년월 아래 한 줄 띄움)
                for k, c in enumerate(wd):
                    put_cell(cells, ox + col * colw + k, oy + 2,
                             c, day_st, W, H)
            for wi, week in enumerate(weeks):    # 주별 날짜(줄 간격 rowh)
                ry = oy + 4 + wi * rowh           # +4: 년월↔요일·요일↔날짜 빈 줄 각 한 칸
                for col, day in enumerate(week):
                    if not day:
                        continue
                    st = today_st if day == today else day_st
                    cxp = ox + col * colw
                    for k, c in enumerate(f"{day:2d}"):
                        put_cell(cells, cxp + k, ry, c, st, W, H)
            # (외곽선 제거: 사용자 요청으로 달력 둘레 박스를 그리지 않는다.)
        else:
            # 이번 달이면 오늘 날짜까지, 넘긴 달이면 연-월만(그 달엔 오늘이 없음).
            s = f"{yr}-{mo:02d}-{cur_day:02d}" if off == 0 else f"{yr}-{mo:02d}"
            ox = px + max(0, (pw - len(s)) // 2)
            oy = py + ph // 2
            for j, c in enumerate(s):
                put_cell(cells, ox + j, oy, c, title_st, W, H)
