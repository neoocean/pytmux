"""셀 그리드 합성 헬퍼 — 앱 상태 비의존 순수 함수(#12 추출).

client.py 의 거대 클로저(build_client_app)에 갇혀 있던 그리기 헬퍼를 모듈 자유함수로
빼냈다(docs/internal/HANDOFF §11.4 / IMPROVEMENT #12). 셀 그리드(cells)를 in-place 로 다루는
순수 함수라 앱 인스턴스가 필요 없다. 회귀는 ptyshot 골든(`tests/test_ptyshot.py`)이
화면 출력 불변으로 가드한다.

여기 남은 `put_cell` 은 시계/달력뿐 아니라 코어 client 도 쓰는 범용 그리드
프리미티브다. 시계·달력 전용 오버레이 그리기(`draw_clock_overlay`/
`draw_calendar_overlay`)는 각 플러그인의 `render.py`(plugins/clock·calendar)로 옮겼다
— 디렉토리를 지우면 그 그리기 코드도 함께 사라지는 완전한 delete-to-disable."""
from __future__ import annotations

from .clientutil import _char_cells


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


def dim_pane(cells, px, py, pw, ph, W, H, cell_fn):
    """오버레이(clock/calendar/usage) 뒤 패널 영역을 흐리게 하는 공통 프리앰블(1-8).

    세 오버레이가 글자 단위로 복제하던 이중 루프를 한 곳으로 모은다. cell_fn=(ch, st)
    → (ch, st) 셀 변환을 받는다(clock/calendar 는 _dim_cell 로 컬러 이모지를 placeholder
    치환, usage 는 _darken_style 로 균일 dim). 패널 rect 를 화면 경계(W,H)로 클램프."""
    for yy in range(py, min(py + ph, H)):
        row = cells[yy]
        for xx in range(px, min(px + pw, W)):
            c, st = row[xx]
            row[xx] = cell_fn(c, st)


def dim_panes(cells, panes, W, H, cell_fn):
    """`dim_pane` 을 패널 목록에 돌린다 — 세 오버레이가 같은 두 줄을 쓰던 자리."""
    for p in panes:
        dim_pane(cells, p["x"], p["y"], p["w"], p["h"], W, H, cell_fn)


def run_style(run, theme):
    """오버레이 런의 축약 스타일 + 의미 색 → rich Style.

    **색의 권위는 클라 테마**다 — 런은 이름만 싣고(`success`·`foreground`) 실제 색은
    여기서 `theme(name)` 으로 푼다. 서버가 hex 를 실으면 서버가 UI 를 알게 되고(설계
    §10 위험표), 사용자가 테마를 바꿔도 그 오버레이만 옛 색으로 남는다. 이름이 없는
    자리는 런에 실린 리터럴을 쓴다(달력 '오늘'의 글자색 `black` — 강조색 바탕 위에서
    읽히기만 하면 되는 자리라 테마가 아니라 모양의 일부다)."""
    from rich.style import Style
    st = run.get("style") or {}
    th = run.get("theme") or {}
    fg = theme(th["f"]) if "f" in th else st.get("f")
    bg = theme(th["b"]) if "b" in th else st.get("b")
    return Style(color=fg, bgcolor=bg, bold=bool(st.get("bo")))


def paint_runs(cells, runs, W, H, theme):
    """오버레이 런 목록을 셀 격자에 얹는다 — **Tier B 런의 정본 소비자**.

    시계·달력·한도 오버레이가 **글자까지 같은 사본 셋**을 들고 있던 자리다(각자의
    `_style` + 같은 이중 루프). 셋이 된 순간 접는다 — 둘일 때는 우연이지만 셋이면
    규약이다. 네이티브 클라의 소비자(`proto`)와 짝이 되는 자리이기도 하다: 같은 런을
    받아 각자의 방식으로 얹는다.

    런 하나 = `{"x","y","text","style":{…}}` (+ 선택 `"theme"`: 의미 색 이름).
    자리는 **창 절대 좌표**이고, 만드는 쪽은 각 플러그인의 `cells.py` 다."""
    for run in runs:
        st = run_style(run, theme)
        x, y = run["x"], run["y"]
        for ch in run["text"]:
            # ★ **와이드 문자는 두 칸이다.** 글자마다 x 를 1 씩 밀면 한글이 든 런에서
            #   자리가 어긋나고 연속셀(`""`)이 안 생겨 **행 전체 폭이 틀어진다**
            #   (실측 2026-08-02i: `[한]` 배지가 44칸 행을 45로 만들었다). 이 규칙이
            #   여기 없던 이유는 첫 소비자 셋(시계·달력·한도)이 전부 ASCII 였기
            #   때문이고, 그건 "안 걸렸다"이지 "맞았다"가 아니다.
            w = _char_cells(ch)
            if w == 2 and x + 1 < W:
                # 오른쪽 칸에 걸친 **배경**의 짝을 먼저 정리한다(우리 글자를 쓴 뒤에
                # 정리하면 put_cell 이 방금 쓴 본체를 공백으로 지운다 — 순서가 규칙의
                # 일부다).
                put_cell(cells, x + 1, y, " ", st, W, H)
            put_cell(cells, x, y, ch, st, W, H)
            if w == 2 and 0 <= x + 1 < W and 0 <= y < H:
                cells[y][x + 1] = ("", st)      # 와이드 짝의 연속셀
            x += w


# ── 탭(터치)으로 쓰는 세로 스크롤바 ──────────────────────────────────────────
# **휠 이벤트를 앱에 넘기지 않는 터미널**을 위한 조작 경로다(제보/진단 2026-07-31,
# iPhone Blink → ssh → MSYS): 클릭은 SGR 로 정상 도달하는데 `wheel` 은 0건이라
# — 두 손가락 스와이프가 터미널 자기 스크롤백 UI로 소비된다(hterm 은 alt-screen
# 에서도 이전 스크롤백을 노출해 "pytmux 실행 이전"까지 올라간다) — pytmux 가 휠을
# 받을 방법이 원천적으로 없다. 그래서 **도달하는 유일한 입력인 탭**으로 스크롤백을
# 조작한다: 스크롤 모드에서 활성 패널 오른쪽 끝 한 열에 스크롤바를 그리고,
# ▲/▼ 탭 = 반 화면 위/아래, 트랙 탭 = 그 위치로 점프.
#
# 아래 세 함수는 셀 그리드도 앱도 안 건드리는 **순수 함수**다(단위 테스트 대상).
SCROLLBAR_UP = "▲"
SCROLLBAR_DOWN = "▼"
SCROLLBAR_TRACK = "│"
SCROLLBAR_THUMB = "█"
SCROLLBAR_MIN_H = 3     # ▲ + 트랙 1칸 + ▼ 미만이면 그리지 않는다


def scrollbar_chars(h, top, scroll):
    """세로 스크롤바 한 열의 글자 목록(길이 h). `h < 3` 이면 `[]`(미표시).

    좌표계는 서버 프레임의 두 값만으로 닫힌다 — `top`(뷰포트 첫 행의 **절대**
    인덱스)·`scroll`(라이브에서 위로 올라간 행수). 위로 더 갈 수 있는 최대치는
    `top + scroll`(맨 위면 top=0 이라 곧 scroll), 전체 행수는 `top + h + scroll`.
    그래서 썸 길이 = 트랙 × (보이는 h / 전체), 위치 = 아래에서부터 scroll 비율."""
    if h < SCROLLBAR_MIN_H:
        return []
    n = h - 2                                   # 화살표 두 칸을 뺀 트랙 길이
    max_scroll = max(0, top + scroll)
    total = max_scroll + h
    thumb = max(1, min(n, round(n * h / total))) if total > 0 else n
    # frac: 0.0 = 맨 위(스크롤 최대) … 1.0 = 맨 아래(라이브). 스크롤백이 없으면
    # 썸이 트랙 전체라 위치는 의미가 없다(0 으로 고정).
    frac = 0.0 if max_scroll <= 0 else (1.0 - scroll / max_scroll)
    start = max(0, min(n - thumb, round((n - thumb) * frac)))
    track = [SCROLLBAR_TRACK] * n
    for i in range(start, start + thumb):
        track[i] = SCROLLBAR_THUMB
    return [SCROLLBAR_UP] + track + [SCROLLBAR_DOWN]


def scrollbar_hit(h, iy):
    """스크롤바 안 상대 행 `iy` → 조작. `("up"|"down", None)` 또는
    `("jump", frac)`(frac 0.0=맨 위 … 1.0=맨 아래). 범위 밖/미표시면 None."""
    if h < SCROLLBAR_MIN_H or not (0 <= iy < h):
        return None
    if iy == 0:
        return ("up", None)
    if iy == h - 1:
        return ("down", None)
    n = h - 2
    return ("jump", (iy - 1) / (n - 1) if n > 1 else 0.0)


def scrollbar_jump_delta(h, top, scroll, frac):
    """트랙 탭이 요구하는 스크롤 델타(+위/-아래) — `send_scroll(delta=)` 에 그대로
    넣는다. 절대 위치 명령을 새로 만들지 않고 **현재 위치와의 차**로 옮기므로 구
    서버(scr 미전송 → scroll=0)에서도 프로토콜 추가 없이 동작한다(정확도만 떨어짐)."""
    max_scroll = max(0, top + scroll)
    return round((1.0 - frac) * max_scroll) - scroll
