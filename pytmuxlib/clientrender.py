"""셀 그리드 합성 헬퍼 — 앱 상태 비의존 순수 함수(#12 추출).

client.py 의 거대 클로저(build_client_app)에 갇혀 있던 그리기 헬퍼를 모듈 자유함수로
빼냈다(docs/HANDOFF §11.4 / IMPROVEMENT #12). 셀 그리드(cells)를 in-place 로 다루는
순수 함수라 앱 인스턴스가 필요 없다. 회귀는 ptyshot 골든(`tests/test_ptyshot.py`)이
화면 출력 불변으로 가드한다."""
from __future__ import annotations

import calendar as _calendar
from datetime import datetime as _datetime

from .clientutil import _CLOCK_FONT, _char_cells, _darken_style


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


def draw_clock_overlay(cells, panes, clock_panes, W, H, digit_st, now=None):
    """clock-mode 패널을 큰 시계로 덮는다(앱 비의존 추출, #12). 뒤의 패널 출력은
    흐리게(dim) 계속 보인다. `panes`=레이아웃 패널 rect 목록, `clock_panes`=시계
    켠 패널 id 집합, `digit_st`=숫자 Style(호출부가 theme 로 해석). `now`=시각
    datetime(테스트 결정성용; None 이면 현재 시각). 큰 시계 공간이 부족하면 단순
    시각 문자열로 폴백한다."""
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


def draw_calendar_overlay(cells, panes, calendar_panes, W, H, styles, now=None):
    """달력 모드 패널을 이번 달 달력으로 덮는다(clock-mode 미러, 앱 비의존 추출 #12).
    뒤의 패널 출력은 흐리게(dim) 계속 보이고 오늘 날짜는 강조. `styles`=해석된 Style
    dict(`day`/`title`/`today`/`big_today`/`border`), `now`=기준 datetime(테스트
    결정성용; None 이면 현재). 패널이 아주 크면 시계 폰트로 '큰 달력'(#16), 충분하면
    일반 그리드+외곽선, 좁으면 단순 날짜 문자열로 단계적 폴백한다."""
    if not calendar_panes:
        return
    now = now or _datetime.now()
    day_st = styles["day"]
    title_st = styles["title"]
    today_st = styles["today"]
    yr, mo, today = now.year, now.month, now.day
    weeks = _calendar.Calendar(firstweekday=0).monthdayscalendar(yr, mo)
    title = f"{yr}-{mo:02d}"
    wds = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    for p in panes:
        if p["id"] not in calendar_panes:
            continue
        px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]
        # 1) 뒤 화면 흐리게(실색 블렌드 — §10, 터미널 무관 균일)
        for yy in range(py, min(py + ph, H)):
            for xx in range(px, min(px + pw, W)):
                c, st = cells[yy][xx]
                cells[yy][xx] = (c, _darken_style(st))
        # 1.5) 아주 큰 패널이면 시계 폰트(3×5)로 날짜를 큼직하게 — '큰 달력'(#16).
        # 한 날짜칸은 숫자 두 자리(3+1+3=7) + 칸 사이 1, 한 주는 글자 5 + 간격 1.
        # DCW=한 날짜칸 폭(숫자 3 + 자리사이 1 + 숫자 3 = 7 + 여유 1 = 8), DGAP=칸 사이,
        # DIG=자리(숫자) 사이 간격(§10-A #9: 2→1 로 좁혀 한 날짜의 두 자리가
        # 한 덩어리로 읽히게 — 날짜칸 사이 간격 DGAP/DCW 는 그대로라 날짜끼리는
        # 안 붙는다. 두 자리 폭 3+1+3=7 이 DCW(8) 안에서 가운데 정렬된다).
        DCW, DGAP, RHB, DIG = 8, 3, 6, 1   # DGAP↑(날짜칸 사이 더 띄움), DIG↓(자리 사이 좁힘)
        gw_big = 7 * DCW + 6 * DGAP          # 칸 7개 + 사이 간격
        nl_big = 2 + len(weeks) * RHB        # 제목+요일 + 주×6
        if pw >= gw_big + 2 and ph >= nl_big + 2:
            big_today = styles["big_today"]
            ox = px + (pw - gw_big) // 2
            oy = py + (ph - nl_big) // 2
            tx = ox + (gw_big - len(title)) // 2
            for j, c in enumerate(title):                 # 제목
                put_cell(cells, tx + j, oy, c, title_st, W, H)
            for col, wd in enumerate(wds):                # 요일(칸 중앙)
                hx = ox + col * (DCW + DGAP) + (DCW - len(wd)) // 2
                for k, c in enumerate(wd):
                    put_cell(cells, hx + k, oy + 1, c, day_st, W, H)
            for wi, week in enumerate(weeks):             # 주별 날짜(큰 글자)
                ry = oy + 2 + wi * RHB
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
            bst = styles["border"]                        # 외곽선
            bx0, by0, bx1, by1 = ox - 1, oy - 1, ox + gw_big, oy + nl_big
            put_cell(cells, bx0, by0, "╭", bst, W, H)
            put_cell(cells, bx1, by0, "╮", bst, W, H)
            put_cell(cells, bx0, by1, "╰", bst, W, H)
            put_cell(cells, bx1, by1, "╯", bst, W, H)
            for xx in range(bx0 + 1, bx1):
                put_cell(cells, xx, by0, "─", bst, W, H)
                put_cell(cells, xx, by1, "─", bst, W, H)
            for yy in range(by0 + 1, by1):
                put_cell(cells, bx0, yy, "│", bst, W, H)
                put_cell(cells, bx1, yy, "│", bst, W, H)
            continue                                      # 큰 달력 완료
        # 2) 칸 폭(colw)·주 간격(rowh)을 가용 공간에 맞춰 키운다 — 넓고 높은
        # 화면일수록 큰 달력(사용자 요청). 한 칸은 숫자 2 + 여백이라 colw≥3.
        # grid_w = 6칸 간격 + 마지막 칸 숫자 2. 외곽선 패딩 2칸을 여유로 둔다.
        colw, rowh = 4, 1               # 시작 4 → 날짜 사이 최소 2칸 여백
        while colw < 8 and pw >= (6 * (colw + 1) + 2) + 2:
            colw += 1
        while rowh < 3 and ph >= (2 + (len(weeks) - 1) * (rowh + 1) + 1) + 2:
            rowh += 1
        grid_w = 6 * colw + 2
        nlines = 2 + (len(weeks) - 1) * rowh + 1
        # 3) 달력 그리드(공간 충분) 또는 단순 날짜
        if pw >= grid_w and ph >= nlines:
            ox = px + (pw - grid_w) // 2
            oy = py + (ph - nlines) // 2
            tx = ox + (grid_w - len(title)) // 2
            for j, c in enumerate(title):       # 제목(YYYY-MM, 중앙)
                put_cell(cells, tx + j, oy, c, title_st, W, H)
            for col, wd in enumerate(wds):       # 요일 헤더(칸 간격 colw)
                for k, c in enumerate(wd):
                    put_cell(cells, ox + col * colw + k, oy + 1,
                             c, day_st, W, H)
            for wi, week in enumerate(weeks):    # 주별 날짜(줄 간격 rowh)
                ry = oy + 2 + wi * rowh
                for col, day in enumerate(week):
                    if not day:
                        continue
                    st = today_st if day == today else day_st
                    cxp = ox + col * colw
                    for k, c in enumerate(f"{day:2d}"):
                        put_cell(cells, cxp + k, ry, c, st, W, H)
            # 그리드 둘레 외곽선(§10 #14): 한 칸 패딩 두고 round 박스 —
            # 위·아래·좌·우로 한 칸씩 더 들어갈 공간이 있을 때만 그린다.
            if pw >= grid_w + 2 and ph >= nlines + 2:
                bst = styles["border"]
                bx0, by0, bx1, by1 = ox - 1, oy - 1, ox + grid_w, oy + nlines
                put_cell(cells, bx0, by0, "╭", bst, W, H)
                put_cell(cells, bx1, by0, "╮", bst, W, H)
                put_cell(cells, bx0, by1, "╰", bst, W, H)
                put_cell(cells, bx1, by1, "╯", bst, W, H)
                for xx in range(bx0 + 1, bx1):
                    put_cell(cells, xx, by0, "─", bst, W, H)
                    put_cell(cells, xx, by1, "─", bst, W, H)
                for yy in range(by0 + 1, by1):
                    put_cell(cells, bx0, yy, "│", bst, W, H)
                    put_cell(cells, bx1, yy, "│", bst, W, H)
        else:
            s = now.strftime("%Y-%m-%d")
            ox = px + max(0, (pw - len(s)) // 2)
            oy = py + ph // 2
            for j, c in enumerate(s):
                put_cell(cells, ox + j, oy, c, title_st, W, H)
