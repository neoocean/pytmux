"""usage-view 현재-패널 오버레이 — 앱 상태 비의존 순수 함수(clock/calendar render.py
미러). `client_overlay` 훅에서만 지연 import 되므로(서버는 안 읽음) clientrender/
clientutil/clientscreens 헬퍼를 최상단에서 import 해도 안전하다.

put_cell(범용 그리드 프리미티브)·_CLOCK_FONT(시계·달력과 공유하는 3×5 폰트)·
_darken_style(균일 dim)·usage_bar_lines(한도 막대 줄)은 모두 코어 공용이라 이 플러그인을
지워도 코어에 죽은 코드가 남지 않는다."""
from __future__ import annotations

from datetime import datetime

from pytmuxlib.clientrender import put_cell
from pytmuxlib.clientscreens import usage_bar_lines
from pytmuxlib.clientutil import _CLOCK_FONT, _darken_style

from .reset import fmt_countdown
from .screen import soonest_reset


def draw_usage_overlay(cells, panes, view_panes, W, H, text_st, digit_st,
                       usage, age_sec=None, now=None):
    """usage-view 가 켜진 패널을 한도 막대 + 다음 리셋 카운트다운으로 덮는다(뒤는 dim).
    `text_st`=막대/안내 텍스트 Style, `digit_st`=카운트다운 숫자 Style. usage 가 없으면
    안내 한 줄만 그린다(빈 화면 금지). `now` 는 테스트 결정성용."""
    if not view_panes:
        return
    now = now or datetime.now()
    for p in panes:
        if p["id"] not in view_panes:
            continue
        px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]
        # 1) 뒤 화면 흐리게(실색 블렌드 — 터미널 무관 균일, clock/calendar 와 동일).
        for yy in range(py, min(py + ph, H)):
            for xx in range(px, min(px + pw, W)):
                c, st = cells[yy][xx]
                cells[yy][xx] = (c, _darken_style(st))
        # 2) 한도 막대 줄(없으면 안내).
        lines = usage_bar_lines(usage, max(8, min(pw - 4, 60)),
                                age_sec=age_sec, right_align=True) or \
            ["한도 데이터 없음 — Claude 패널에서 /usage 실행 후 갱신"]
        oy = py + 1
        last = oy
        for i, ln in enumerate(lines):
            yrow = oy + i
            if yrow >= py + ph:
                break
            for j, ch in enumerate(ln):
                put_cell(cells, px + 2 + j, yrow, ch, text_st, W, H)
            last = yrow
        # 3) 다음 리셋 카운트다운 — 공간 충분하면 블록 HH:MM:SS, 아니면 한 줄.
        _, dt = soonest_reset(usage, now)
        if dt is None:
            continue
        td = dt - now
        total = int(td.total_seconds())
        cy = last + 2
        if 0 <= total < 86400 and pw >= 30 and (py + ph - cy) >= 5:
            h, rem = divmod(total, 3600)
            m, s = divmod(rem, 60)
            text = f"{h:02d}:{m:02d}:{s:02d}"
            glyphs = [_CLOCK_FONT.get(c, ["   "] * 5) for c in text]
            cw = sum(len(g[0]) for g in glyphs) + (len(glyphs) - 1)
            ox = px + max(0, (pw - cw) // 2)
            for row in range(5):
                gx = ox
                for g in glyphs:
                    for c in g[row]:
                        if c != " ":
                            put_cell(cells, gx, cy + row, c, digit_st, W, H)
                        gx += 1
                    gx += 1            # 글자 사이 간격
        elif cy < py + ph:
            label = "다음 리셋까지 " + fmt_countdown(td)
            for j, ch in enumerate(label):
                put_cell(cells, px + 2 + j, cy, ch, digit_st, W, H)
