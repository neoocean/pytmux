"""시계 오버레이의 **런 생성기** — UI 무의존(rich/textual 을 안 읽는다).

# 왜 여기인가

달력이 먼저 온 길이다(`plugins/calendar/cells.py` 머리말). 시계는 P3(2026-08-02)에서
서버 셀 기여를 얻으면서 **일부러 두 벌로 남겨 뒀다** — 정본은 `render.py` 에서 셀
격자에 직접 그리고, 서버는 `plugin_cells` 가 같은 규칙을 다시 적었다. 그 두 벌을
「같은 입력, 두 경로」 오라클이 대조하는 것이 그때의 안전장치였다.

2026-08-02d 에 한 벌로 합친다. 합치기 직전 그 오라클을 마지막으로 돌려 **세 크기
(70×12 큰 폰트 · 40×6 반칸 폰트 · 14×3 폴백)에서 두 경로의 그림이 칸 단위로 완전히
같음**을 확인했다 — 그래서 이 통합은 **그림을 안 바꾼다**.

# 대조를 잃는 대신 골든을 못박는다

두 벌이 사라지면 대조할 짝도 사라진다(설계 §9 · 08-02b 가 달력에서 겪은 그대로).
둘 다 같이 틀리는 변경은 대조로 안 잡히므로, 재는 것을 **모양 자체**로 옮긴다 —
`tests/test_plugin_clock_render.py` 의 그림 골든이 그 자리다. 규칙을 고치면 골든이
따라와야 한다.

# 색은 여기서 안 정한다

런은 `theme` 에 **의미 이름**(`success`)만 싣고 실제 색은 각 클라가 자기 테마에서
푼다. 서버가 hex 를 실으면 서버가 UI 를 알게 되고(설계 §10 위험표), 사용자가 테마를
바꿔도 시계만 옛 색으로 남는다.

# 누를 자리는 없다

달력과 달리 시계엔 `‹`/`›` 가 없어 `zones` 를 안 돌려준다(달력은 `(runs, zones)`).
누를 자리가 생기면 그때 달력과 같은 표기로 넓힌다 — 지금 빈 리스트를 돌려주면
"있는데 비었다"로 읽혀 소비자가 헛일을 한다.
"""
from __future__ import annotations

from datetime import datetime as _datetime

from pytmuxlib.blockfont import clock_font_for, segments

# 숫자 한 벌의 스타일 — `(축약 스타일, 의미 색)`. 축약 표기는 서버가 화면 런에 쓰는
# 것과 같다(`model._style_key`).
DIGIT = ({"bo": 1}, {"f": "success"})


def _run(x, y, text, st):
    style, theme = st
    run = {"x": x, "y": y, "text": text, "style": dict(style)}
    if theme:
        run["theme"] = dict(theme)
    return run


def clock_time(now=None):
    """시계가 말하는 **글자** — `HH:MM:SS`.

    격자 런과 네이티브 상태가 **같은 함수**를 지나야 한 화면에 두 시각이 안 뜬다
    (pytmux-459). 시각의 권위는 서버이고, 클라가 자기 시계를 읽지 않는 이유가 이것이다 —
    원격 세션에서 두 시계가 갈리면 어느 쪽이 맞는지 알 길이 없다.
    """
    return (now or _datetime.now()).strftime("%H:%M:%S")


def clock_cells(panes, now=None):
    """`runs` — 시계가 켜진 패널들의 런.

    `panes` = `[{"id","x","y","w","h"}]`(**시계가 켜진 것만** 넘긴다),
    `now` = 기준 시각(테스트 결정성용; None 이면 현재).

    자리는 **창 절대 좌표**다. 글자 크기는 패널이 허락하는 만큼 커진다
    (`clock_font_for`): 넓고 높으면 한 칸 높이 픽셀의 큰 폰트(5행·글자 6칸), 아니면
    반칸 폰트(3행·글자 3칸), 그마저 안 들어가면 단순 시각 문자열로 폴백한다.
    """
    text = clock_time(now)
    runs = []
    for p in panes:
        px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]
        font, ch_h, gcols, cw = clock_font_for(pw, ph, len(text))
        if pw >= cw and ph >= ch_h:
            ox = px + (pw - cw) // 2
            oy = py + (ph - ch_h) // 2
            for row in range(ch_h):
                # 글자 사이 한 칸을 그대로 둔 한 줄로 잇고, **비공백 덩어리만** 런으로
                # 만든다 — 글리프의 공백은 뒤가 비쳐 보이는 자리다(`blockfont.segments`).
                line = " ".join(font.get(c, [" " * gcols] * ch_h)[row]
                                for c in text)
                for dx, seg in segments(line):
                    runs.append(_run(ox + dx, oy + row, seg, DIGIT))
        else:
            # 큰 글자가 안 들어가면 단순 시각(정본 폴백과 같은 판정).
            runs.append(_run(px + max(0, (pw - len(text)) // 2),
                             py + ph // 2, text, DIGIT))
    return runs
