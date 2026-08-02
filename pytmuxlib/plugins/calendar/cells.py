"""달력 오버레이의 **런 생성기** — UI 무의존(rich/textual 을 안 읽는다).

# 왜 여기인가

달력을 그리는 규칙은 **한 벌**이라야 한다. 종전에는 정본이 `render.py` 에서 셀 격자에
직접 그렸고, 네이티브 클라는 그 그림을 못 내 Rust 로 **손으로 옮긴 두 번째 달력**
(`client/crates/proto/src/calendar.rs`)을 들고 있었다. 두 벌은 갈린다 — 실제로
갈렸었다(시계 숫자·달력 제목 색이 서로 달랐던 것이 클라 대조 문서 §13).

그래서 자리를 정하는 일(어느 단의 폰트로, 어디에 중앙 정렬하나)을 전부 이 모듈로
모으고, **두 소비자가 같은 런을 받아** 각자의 방식으로 얹는다:

- 정본(`render.py`) — 런을 `put_cell` 로 격자에 찍는다(딤은 정본이 직접 한다).
- 네이티브 클라 — 서버가 `plugin_cells` 로 같은 런을 보낸다(설계 Tier B · P3).

시계(P3)는 두 벌을 **오라클로 대조**하는 데서 멈췄지만(설계 미결 #1), 달력은 아예
한 벌로 합친다. 대조할 두 벌이 없으면 갈릴 일도 없다.

# 색은 여기서 안 정한다

런은 `theme` 에 **의미 이름**(`success`·`foreground`)만 싣고 실제 색은 각 클라가 자기
테마에서 푼다. 서버가 hex 를 실으면 서버가 UI 를 알게 되고(설계 §10 위험표), 사용자가
테마를 바꿔도 달력만 옛 색으로 남는다.
"""
from __future__ import annotations

import calendar as _calendar
from datetime import datetime as _datetime

from pytmuxlib.blockfont import (_CLOCK_FONT, _CLOCK_FONT_BIG,
                                 _CLOCK_FONT_BIG_COLS, _CLOCK_FONT_BIG_ROWS,
                                 _CLOCK_FONT_COLS, _CLOCK_FONT_ROWS, segments)

# 네 자리의 스타일 — `(축약 스타일, 의미 색)`. 축약 스타일은 서버가 화면 런에 쓰는
# 것과 같은 표기(`model._style_key`)라 새 표기를 만들지 않는다. 색은 이름뿐이다.
#
# `today` 만 **글자색이 리터럴**(black)이다: 그 자리는 "테마 강조색 바탕에 검은 글자"가
# 곧 모양이라(정본 `Style(color="black", bgcolor=theme_color(app,"success"))`) 바탕을
# 테마에서 풀고 글자는 그 위에서 읽히기만 하면 된다.
DAY = ({}, {"f": "foreground"})
TITLE = ({"bo": 1}, {"f": "success"})
TODAY = ({"f": "black", "bo": 1}, {"b": "success"})
BIG_TODAY = ({"bo": 1}, {"f": "success"})

_WDS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]

# 큰 달력의 칸 셈 — DGAP=날짜칸 사이, DIG=한 날짜의 두 자리 사이(§10-A #9: 2→1 로
# 좁혀 한 날짜의 두 자리가 한 덩어리로 읽히게).
_DGAP, _DIG = 3, 1


def _run(x, y, text, st):
    style, theme = st
    run = {"x": x, "y": y, "text": text, "style": dict(style)}
    if theme:
        run["theme"] = dict(theme)
    return run


def month_of(now, off):
    """`(연, 월, 강조할 날)` — 오프셋이 0 이 아니면 강조할 날은 0(그 달엔 오늘이 없다)."""
    m0 = now.year * 12 + (now.month - 1) + off
    return m0 // 12, m0 % 12 + 1, (now.day if off == 0 else 0)


def calendar_cells(panes, states, now=None):
    """`(runs, zones)` — 달력이 켜진 패널들의 런과 `‹`/`›` 클릭존.

    `panes` = `[{"id","x","y","w","h"}]`(**달력이 켜진 것만** 넘긴다),
    `states` = `{패널 id: {"offset": n}}`(없으면 이번 달), `now` = 기준 시각
    (테스트 결정성용; None 이면 현재).

    자리는 **창 절대 좌표**다 — 호출부가 패널 내용 영역 좌표를 이미 넘겨 준다.
    패널이 아주 크면 블록 폰트의 '큰 달력', 충분하면 일반 그리드, 좁으면 단순 날짜
    문자열로 단계적 폴백한다(정본의 종전 판정 그대로).
    """
    now = now or _datetime.now()
    runs, zones = [], []
    for p in panes:
        pid = p["id"]
        off = int((states.get(pid) or {}).get("offset", 0))
        yr, mo, today = month_of(now, off)
        weeks = _calendar.Calendar(firstweekday=6).monthdayscalendar(yr, mo)
        title = f"‹ {yr}-{mo:02d} ›"   # ‹ YYYY-MM › (←/→ 넘김 힌트)
        px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]

        def _title(tx, oy):
            """제목과 그 좌우 화살표 클릭존(각 2칸 — 가운데 날짜 숫자와 안 겹친다)."""
            runs.append(_run(tx, oy, title, TITLE))
            zones.append({"x": tx, "y": oy, "w": 2, "h": 1,
                          "pane": pid, "do": "prev"})
            zones.append({"x": tx + len(title) - 2, "y": oy, "w": 2, "h": 1,
                          "pane": pid, "do": "next"})

        # ① 아주 큰 패널이면 블록 폰트로 날짜를 큼직하게 — '큰 달력'(#16). 폰트는
        #    **두 단**이다(요청 2026-07-26): 아주 넓으면 한 칸 높이 픽셀의 큰 폰트
        #    (5행·글자 6칸), 그만큼은 아니면 종전 반칸 폰트(3행·글자 3칸). 큰 쪽부터
        #    시도해 **들어가는 첫 단**을 쓴다.
        big = None
        for _font, _rows, _cols in ((_CLOCK_FONT_BIG, _CLOCK_FONT_BIG_ROWS,
                                     _CLOCK_FONT_BIG_COLS),
                                    (_CLOCK_FONT, _CLOCK_FONT_ROWS,
                                     _CLOCK_FONT_COLS)):
            _dcw = 2 * _cols + _DIG + 1      # 두 자리 + 자리사이 + 여유 1
            _rhb = _rows + 1                 # 폰트행 + 주 사이 한 줄
            _gw = 7 * _dcw + 6 * _DGAP       # 칸 7개 + 사이 간격
            _nl = 4 + len(weeks) * _rhb      # 제목+빈줄+요일+빈줄 + 주×(_rhb)
            if pw >= _gw + 2 and ph >= _nl + 2:
                big = (_font, _rows, _cols, _dcw, _rhb, _gw, _nl)
                break
        if big is not None:
            font, frows, fcols, DCW, RHB, gw_big, nl_big = big
            ox = px + (pw - gw_big) // 2
            oy = py + (ph - nl_big) // 2
            _title(ox + (gw_big - len(title)) // 2, oy)
            for col, wd in enumerate(_WDS):     # 요일(칸 중앙, 년월 아래 한 줄 띄움)
                hx = ox + col * (DCW + _DGAP) + (DCW - len(wd)) // 2
                runs.append(_run(hx, oy + 2, wd, DAY))
            for wi, week in enumerate(weeks):   # 주별 날짜(큰 글자)
                ry = oy + 4 + wi * RHB          # +4: 년월↔요일·요일↔날짜 빈 줄 각 한 칸
                for col, day in enumerate(week):
                    if not day:
                        continue
                    st = BIG_TODAY if day == today else DAY
                    s = str(day)
                    gw = len(s) * fcols + (len(s) - 1) * _DIG
                    gx0 = ox + col * (DCW + _DGAP) + (DCW - gw) // 2
                    glyphs = [font.get(c, [" " * fcols] * frows) for c in s]
                    for r in range(frows):
                        # 자리 사이 한 칸(_DIG)을 그대로 둔 한 줄로 잇고, **비공백
                        # 덩어리만** 런으로 만든다(글리프의 공백은 뒤가 비쳐 보이는
                        # 자리다 — blockfont.segments 주석).
                        line = (" " * _DIG).join(g[r] for g in glyphs)
                        for dx, seg in segments(line):
                            runs.append(_run(gx0 + dx, ry + r, seg, st))
            continue                            # 큰 달력 완료(외곽선 없음)

        # ② 칸 폭(colw)·주 간격(rowh)을 가용 공간에 맞춰 키운다 — 넓고 높은 화면일수록
        #    큰 달력. 한 칸은 숫자 2 + 여백이라 colw≥3. grid_w = 6칸 간격 + 마지막 칸
        #    숫자 2. 외곽선 패딩 2칸을 여유로 둔다.
        colw, rowh = 4, 1                       # 시작 4 → 날짜 사이 최소 2칸 여백
        while colw < 8 and pw >= (6 * (colw + 1) + 2) + 2:
            colw += 1
        while rowh < 3 and ph >= (3 + (len(weeks) - 1) * (rowh + 1) + 1) + 2:
            rowh += 1
        grid_w = 6 * colw + 2
        # 제목 + 빈줄 + 요일 + 빈줄 + 주(첫 주 1줄 + 이후 rowh)
        nlines = 4 + (len(weeks) - 1) * rowh + 1
        if pw >= grid_w and ph >= nlines:
            ox = px + (pw - grid_w) // 2
            oy = py + (ph - nlines) // 2
            _title(ox + (grid_w - len(title)) // 2, oy)
            for col, wd in enumerate(_WDS):     # 요일 헤더(칸 간격 colw)
                runs.append(_run(ox + col * colw, oy + 2, wd, DAY))
            for wi, week in enumerate(weeks):   # 주별 날짜(줄 간격 rowh)
                ry = oy + 4 + wi * rowh
                for col, day in enumerate(week):
                    if not day:
                        continue
                    st = TODAY if day == today else DAY
                    # ★ `f"{day:2d}"` 의 **앞 공백을 지우지 않는다**. 한 자리 날짜의
                    #   오늘 강조는 배경색 두 칸이 곧 모양이고(정본이 그 공백도 찍는다),
                    #   여기서 떼면 그 강조가 한 칸으로 줄어든다.
                    runs.append(_run(ox + col * colw, ry, f"{day:2d}", st))
            # (외곽선 제거: 사용자 요청으로 달력 둘레 박스를 그리지 않는다.)
        else:
            # ③ 단순 날짜 — 이번 달이면 오늘 날짜까지, 넘긴 달이면 연-월만.
            #    화살표가 없으니 클릭존도 없다.
            s = f"{yr}-{mo:02d}-{now.day:02d}" if off == 0 else f"{yr}-{mo:02d}"
            runs.append(_run(px + max(0, (pw - len(s)) // 2), py + ph // 2,
                             s, TITLE))
    return runs, zones
