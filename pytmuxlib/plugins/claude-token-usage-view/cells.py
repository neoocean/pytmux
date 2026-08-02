"""usage-view 패널 오버레이의 **런 생성기** — UI 무의존(rich/textual 을 안 읽는다).

# 왜 여기인가

시계(2026-08-02e)·달력(08-02b)이 먼저 간 길이다. 그리는 규칙을 한 벌로 모으고 두
소비자가 **같은 런**을 받는다:

- 정본(`overlay.py`) — 런을 `put_cell` 로 격자에 찍는다(딤은 정본이 직접 한다).
- 네이티브 클라 — 서버가 `plugin_cells` 로 같은 런을 보낸다(설계 Tier B).

이 오버레이가 Tier B 의 **마지막 소비자**였다. 시계·달력과 달리 재료가 클라에 묶여
있어 미뤄져 있었다 — 한도 줄을 만드는 `usage_bar_lines` 가 textual 을 읽는
`clientscreens` 에 살았다. 그것을 `pytmuxlib/usagebar.py` 로 빼고(같은 CL), 카운트다운
버킷 선택(`soonest_reset`)도 `reset.py` 로 내려서 서버가 같은 재료를 쓴다.

# 색은 여기서 안 정한다

런은 `theme` 에 **의미 이름**(`foreground`·`success`)만 싣고 실제 색은 각 클라가 자기
테마에서 푼다(설계 §10 위험표 — 서버가 hex 를 실으면 서버가 UI 를 알게 된다).

# 글은 서버가 짓는다 — 그 한계를 어디까지 걷어냈나 (2026-08-02, 로케일 ⓑ)

한도 줄과 안내 문구는 `i18n.t()` 로 **짓는 쪽의 로케일**을 탄다. 정본이 그릴 때는 그
클라의 로케일이지만, 서버가 그려 보내면 **서버 로케일**이 실린다.

⚠ **종전에 이 자리에 적어 둔 처방("프로토콜에 클라 로케일 협상을 넣어야 한다")은
과했다.** 갈래가 셋이다:

1. **고정 리터럴** — 클라가 한국어 원문을 키로 번역한다(로케일 ⓐ). 프로토콜 무변경.
2. **라벨 + 값** — 원문 포맷과 값을 따로 실으면 클라가 자기 표에서 포맷을 번역해
   치환한다(`i18n.phrase` → 런의 `i18n` 칸). 여기 카운트다운 줄이 그 시민이다.
3. **폭이 자리를 정하는 줄** — 한도 막대(`usage_bar_lines`)는 라벨 폭이 막대 시작점을
   정하므로 클라가 번역하면 **정렬이 깨진다**. 이것만 남았고, 답은 "서버가 그 클라의
   로케일로 짓는다"다. 셀 프레임은 이미 **클라별**로 만들어지므로(`client._cells_at`)
   길은 열려 있다 — 필요한 것은 `t()` 가 로케일을 인자로 받는 것뿐이다(전역을 바꾸는
   방식은 async 에서 다른 클라의 프레임과 섞인다).
"""
from __future__ import annotations

from datetime import datetime as _datetime

from pytmuxlib import i18n
from pytmuxlib.blockfont import _CLOCK_FONT, _CLOCK_FONT_ROWS, segments
from pytmuxlib.usagebar import usage_bar_lines

from .reset import fmt_countdown, soonest_reset

# 두 자리의 스타일 — `(축약 스타일, 의미 색)`. 축약 표기는 서버가 화면 런에 쓰는
# 것과 같다(`model._style_key`).
TEXT = ({}, {"f": "foreground"})
DIGIT = ({"bo": 1}, {"f": "success"})

# 막대 줄이 시작하는 패널 안쪽 여백(왼쪽 2칸 · 위 1줄) — 정본이 쓰던 값 그대로.
_PAD_X, _PAD_Y = 2, 1


def _run(x, y, text, st, phrase=None):
    """런 하나. `phrase` 를 주면 **클라가 자기 로케일로 다시 지을 재료**도 싣는다.

    ⚠ 재료를 싣는 것은 **줄 하나가 통째로 한 런**인 자리에만 한다. 번역하면 폭이 달라
    지는데(`다음 리셋까지` ↔ `Until next reset`) 런은 좌표를 갖고 가므로, 옆에 붙는 런이
    있으면 그것을 밀어 겹친다. 한도 막대 줄은 라벨 폭이 막대 시작점을 정하므로 여기에
    해당하지 않는다 — 그 줄은 `usage_bar_lines` 가 서버에서 정렬까지 마친 결과다."""
    style, theme = st
    run = {"x": x, "y": y, "text": text, "style": dict(style)}
    if theme:
        run["theme"] = dict(theme)
    if phrase is not None:
        run["i18n"] = {"text": phrase}
    return run


def usage_cells(panes, usage, age_sec=None, now=None):
    """`runs` — usage-view 가 켜진 패널들의 런.

    `panes` = `[{"id","x","y","w","h"}]`(**켜진 것만** 넘긴다), `usage` = claude-code 가
    긁어 둔 `/usage` 한도 dict(없으면 안내 한 줄), `age_sec` = 실측 경과(초),
    `now` = 기준 시각(테스트 결정성용; None 이면 현재).

    자리는 **창 절대 좌표**다. 카운트다운은 공간이 되면 블록 글자 HH:MM:SS,
    아니면 한 줄 문자열로 폴백한다(정본의 종전 판정 그대로)."""
    now = now or _datetime.now()
    runs = []
    for p in panes:
        px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]
        # ① 한도 막대 줄(데이터 없으면 안내 한 줄 — 빈 화면 금지).
        lines = usage_bar_lines(usage, max(8, min(pw - 4, 60)),
                                age_sec=age_sec, right_align=True) or \
            [i18n.t("uview.overlay_no_data")]
        last = py + _PAD_Y
        for i, ln in enumerate(lines):
            yrow = py + _PAD_Y + i
            if yrow >= py + ph:
                break
            if ln:
                runs.append(_run(px + _PAD_X, yrow, ln, TEXT))
            last = yrow
        # ② 다음 리셋 카운트다운 — 공간 충분하면 블록 HH:MM:SS, 아니면 한 줄.
        _, dt = soonest_reset(usage, now)
        if dt is None:
            continue
        td = dt - now
        total = int(td.total_seconds())
        cy = last + 2
        if 0 <= total < 86400 and pw >= 30 and (py + ph - cy) >= _CLOCK_FONT_ROWS:
            h, rem = divmod(total, 3600)
            m, s = divmod(rem, 60)
            text = f"{h:02d}:{m:02d}:{s:02d}"
            glyphs = [_CLOCK_FONT.get(c, ["   "] * _CLOCK_FONT_ROWS)
                      for c in text]
            cw = sum(len(g[0]) for g in glyphs) + (len(glyphs) - 1)
            ox = px + max(0, (pw - cw) // 2)
            for row in range(_CLOCK_FONT_ROWS):
                # 글자 사이 한 칸을 그대로 둔 한 줄로 잇고, **비공백 덩어리만** 런으로
                # 만든다 — 글리프의 공백은 뒤가 비쳐 보이는 자리다(`blockfont.segments`).
                line = " ".join(g[row] for g in glyphs)
                for dx, seg in segments(line):
                    runs.append(_run(ox + dx, cy + row, seg, DIGIT))
        elif cy < py + ph:
            # 이 줄은 그 행에 **혼자** 있다 → 번역으로 폭이 달라져도 밀 것이 없다.
            label, spec = i18n.phrase("uview.overlay_next_reset_in",
                                      left=fmt_countdown(td))
            runs.append(_run(px + _PAD_X, cy, label, DIGIT, phrase=spec))
    return runs
