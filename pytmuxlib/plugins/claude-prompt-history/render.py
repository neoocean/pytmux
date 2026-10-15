"""claude-prompt-history 클라이언트 렌더 — 직전 프롬프트 미리보기 패널(transient).

`:` 명령 프롬프트에서 `prompt-history`(pane_scoped)를 작성하는 동안에만, 코어가 세우는
`app._cmd_target_pane`(대상=활성 패널)의 **외곽선 안쪽 위에서부터** 직전 프롬프트를
오버레이로 그린다. 멀티라인 프롬프트는 1행부터 시작해 여러 줄로 늘리되 최대
`app.ph_max_lines` 행(설정 가능)까지. 서버 행 예약 없이 content 위에 덮어 그리므로
명령 작성이 끝나(프롬프트 닫힘) `_cmd_target_pane=None` 이 되면 다음 합성에서 사라진다.

**딤 제외(가독성)**: 이 바는 명령 프롬프트(ModalScreen=PromptScreen) 뒤에 그려지는데,
코어 backdrop-dim(clientio._composite)이 그 위를 `_darken_style` 로 덮어 흰 글자가 회색
(≈grey114)으로 뭉개져 배경(진파랑 바)과 대비가 무너진다(제보: 텍스트가 배경색과
비슷해 안 읽힘). perm-mode footer 행과 동일한 패턴으로 미리보기 행을 `app._undim_rows`
에 실어 딤에서 제외 → 원래 밝기(순백 글자/진파랑 바)로 또렷이 보이게 한다. client_render
훅은 backdrop-dim 루프보다 먼저 도므로(clientio 983 < 1163) 같은 프레임에 반영된다.

textual 비의존(rich.Style + clientutil 헬퍼). client_render 훅이 지연 import 한다."""
from __future__ import annotations


def _ph_undim_clear(app):
    """직전 프레임에 우리가 등록한 undim 행을 `app._undim_rows` 에서 회수한다.
    아래 draw_preview 의 여러 early-return 이 stale 행(딤 안 되는 유령 밝은 줄)을
    남기지 않도록 매 프레임 맨 앞에서 부른다. perm-mode 등 다른 소유자의 행은 건드리지
    않는다(내 기여분 `_ph_undim_rows` 만 뺀다)."""
    prev = getattr(app, "_ph_undim_rows", None)
    if not prev:
        return
    cur = getattr(app, "_undim_rows", None)
    if cur:
        app._undim_rows = cur - prev
    app._ph_undim_rows = set()


def _ph_undim_set(app, rows):
    """이번 프레임에 그린 미리보기 행들을 딤 제외 집합에 실는다(내 기여분도 기록)."""
    if not rows:
        _ph_undim_clear(app)
        return
    cur = getattr(app, "_undim_rows", None)
    app._undim_rows = (set(cur) if cur else set()) | rows
    app._ph_undim_rows = set(rows)


def draw_preview(app, cells, W, H):
    """직전 프롬프트 미리보기를 대상 패널 위에 그린다. 그릴 게 없으면 no-op."""
    _ph_undim_clear(app)            # 이전 프레임 등록분 회수(early-return stale 방지)
    pid = getattr(app, "_cmd_target_pane", None)
    if pid is None:
        return
    entry = (getattr(app, "ph_panes", {}) or {}).get(pid)
    if not entry:
        return
    hist = entry.get("h") or []
    if not hist:
        return
    prompt = hist[-1]
    if not prompt:
        return
    # 대상 패널 rect 찾기.
    pane = next((p for p in app.layout.get("panes", []) if p["id"] == pid), None)
    if pane is None:
        return
    px, py, pw, phh = pane["x"], pane["y"], pane["w"], pane["h"]
    if pw < 6 or phh < 1:
        return

    from rich.style import Style
    from pytmuxlib.clientutil import _char_cells, theme_color

    max_lines = max(1, min(int(getattr(app, "ph_max_lines", 3)), phh))
    plines = prompt.split("\n")
    nshow = min(max_lines, len(plines), phh)

    # 순백(#FFFFFF) 볼드 — ANSI "white"(팔레트 7)는 다수 터미널서 옅은 회색으로 떠
    # 진파랑 바와 대비가 약했다. 명시 truecolor 로 못 박아 항상 순백으로 그린다(위
    # 딤 제외와 함께 배경과 확실히 구분). 진파랑 배경은 Claude 헤더와 같은 정체성 유지.
    bar_st = Style(color="#FFFFFF", bgcolor=theme_color(app, "primary-darken-2"),
                   bold=True)
    # 잘림 표시(▾)·시작 마커(▷) 색은 본문과 같은 바 스타일을 쓴다(단순·고대비).
    truncated = len(plines) > nshow

    drawn_rows = set()
    for row in range(nshow):
        gy = py + row
        if not (0 <= gy < H):
            continue
        drawn_rows.add(gy)
        # 바 배경으로 한 줄 채움.
        for xx in range(px, min(px + pw, W)):
            cells[gy][xx] = (" ", bar_st)
        text = plines[row]
        # 첫 줄엔 시작 마커, 마지막 표시줄이 잘림이면 끝에 ▾ 를 위한 여백 확보.
        prefix = "▷ " if row == 0 else "  "
        budget = max(0, pw - 2)           # 좌우 1칸 여백
        gx = px + 1
        drawn = prefix + text
        if truncated and row == nshow - 1:
            drawn = drawn + "  "          # ▾ 자리 확보(아래서 덮음)
        used = 0
        for chh in drawn:
            wch = _char_cells(chh)
            if used + wch > budget:
                break
            if 0 <= gx < W:
                cells[gy][gx] = (chh, bar_st)
                if wch == 2 and 0 <= gx + 1 < W and used + 1 < budget:
                    cells[gy][gx + 1] = ("", bar_st)
            gx += wch
            used += wch
        # 마지막 표시줄이 잘렸으면 우측 끝에 ▾ 더 표시(더 있음).
        if truncated and row == nshow - 1:
            mx = px + pw - 2
            if 0 <= mx < W:
                cells[gy][mx] = ("▾", bar_st)

    # 그린 행들을 딤 제외 집합에 실어, 명령 프롬프트 backdrop-dim 이 이 바를 회색으로
    # 뭉개지 않게 한다(가독성). 그릴 게 없으면 회수만 한다.
    _ph_undim_set(app, drawn_rows)


# ---- 고정 프롬프트 바(pytmux-560) ─────────────────────────────────────────────
#: 고정 바의 최대 행수. 사용자 요구가 「여러 줄이면 최대 2줄」이다 — 바가 패널 위를
#: 가리는 오버레이라 더 키우면 그만큼 앱 글이 가려진다.
PINNED_MAX_ROWS = 2

#: 이번 프레임에 그린 고정 바 — `{패널 id: (y0, y1, x0, x1)}`(y1·x1 은 끝 다음).
#: 클릭·히트 판정이 이 표를 읽는다(그린 자리와 누르는 자리가 같은 값을 보게).
_PINNED = "_ph_pinned"

#: 줄 첫 글자가 이것이면 「제출된 프롬프트 줄」(또는 claude 자신의 스티키 바)이다.
#: claude-code 의 `PROMPT_MARK_FIRST` 와 같은 뜻이지만 그 플러그인을 import 하지 않는다
#: (delete-to-disable — 하드 참조 금지). `›` 는 옛 판 claude 의 스티키 바 표식이다.
_MARKS = (">", "❯", "›")

#: 대화를 비우는 claude 명령(`/clear` 와 그 별칭). 이것이 가장 최근 제출이면 바가 서지
#: 않는다(pytmux-561) — 화면은 새 대화인데 바만 지운 대화의 프롬프트를 띄우게 된다.
#: 이력은 팝업·점프의 재료라 지우지 않고 바만 경계를 안다.
_CLEAR_CMDS = ("/clear", "/reset", "/new")


def _is_clear(prompt):
    """제출 글이 대화를 비우는 명령인가(인자·앞뒤 공백 무시)."""
    words = (prompt or "").split()
    return bool(words) and words[0].lower() in _CLEAR_CMDS


def _row_text(cells, gy, x0, x1):
    """합성 셀 한 행의 글자(와이드 글자 stub `""` 은 건너뛴다)."""
    return "".join(cells[gy][gx][0] for gx in range(x0, x1)
                   if cells[gy][gx][0] != "")


def _mark_body(text):
    """프롬프트 표식으로 시작하는 줄이면 표식 뒤 글, 아니면 None."""
    s = text.lstrip()
    if not s or s[0] not in _MARKS:
        return None
    return s[1:].strip()


def _clean(text):
    """그릴 글 — 탭은 공백, 나머지 제어문자는 공백으로 접는다(글자가 붙지 않게)."""
    return "".join(" " if (ord(c) < 0x20 or 0x7f <= ord(c) <= 0x9f) else c
                   for c in text)


def pinned_rows(prompt, budget, max_rows=PINNED_MAX_ROWS):
    """프롬프트를 폭 `budget` 칸에 접어 **최대 `max_rows` 행**의 글로 만든다.

    첫 행은 `❯ ` 로, 뒤 행은 두 칸 들여 쓴다. 빈 줄은 건너뛴다(빈 행으로 2줄 중 하나를
    버리지 않게). 넘치면 마지막 행 끝을 `…` 로 바꾼다 — 「더 있다」를 말하는 자리다.
    """
    from pytmuxlib.clientutil import _char_cells
    lines = [_clean(ln).strip() for ln in (prompt or "").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines or budget < 4:
        return []
    rows = []                     # [(글, 칸 수)]
    truncated = False
    for li, ln in enumerate(lines):
        cur, used = ("  " if rows else "❯ "), 2
        for ch in ln:
            w = _char_cells(ch)
            if used + w > budget:
                rows.append((cur, used))
                cur, used = "  ", 2
                if len(rows) > max_rows:
                    break
            cur += ch
            used += w
        rows.append((cur, used))
        if len(rows) >= max_rows:
            truncated = len(rows) > max_rows or li < len(lines) - 1
            break
    rows = rows[:max_rows]
    if truncated:
        text, used = rows[-1]
        while text and used + 1 > budget:
            used -= _char_cells(text[-1])
            text = text[:-1]
        rows[-1] = (text.rstrip() + "…", used + 1)
    return [text for text, _ in rows]


def draw_pinned(app, cells, W, H):
    """Claude 패널마다 첫 줄에 **가장 최근 제출 프롬프트**를 고정 바로 얹는다
    (pytmux-560 · 사용자 요구 2026-10-15: *"스크롤을 위로 올려야 이전 프롬프트가
    보인다 — 항상 보이게, 여러 줄이면 최대 2줄"*).

    # 왜 pytmux 가 그리나

    claude 자신의 스티키 바(fullscreen 렌더러)는 뷰포트가 위로 굴러 있을 때만 서고,
    블록 스티키 바(`plugins/blocks`)도 pytmux 스크롤 중에만 선다. 「항상」은 앱이 줄 수
    없어 여기서 낸다. 재료는 이미 status 로 와 있는 `ph_panes`(입력 추적 이력)다.

    # 비키는 조건 — 같은 것을 두 번 보이지 않게, 더 맞는 바를 가리지 않게

    ① 그 패널에 `:` 미리보기가 떠 있다(`draw_preview` 가 같은 자리를 쓴다)
    ② pytmux 스크롤 중이다 — 블록 스티키 바가 「보고 있는 곳의」 프롬프트를 낸다
    ③ 패널 첫 줄이 이미 프롬프트 표식으로 시작한다 — claude 자신의 스티키 바가 떠 있거나
       프롬프트 줄이 맨 위에 있다
    ④ 그 프롬프트가 지금 패널 안에 표식 줄로 보인다(방금 제출해 아직 화면에 있다)
    ⑤ 가장 최근 제출이 `/clear`(별칭 포함)다 — 그 앞 프롬프트는 지운 대화의 것이다
       (pytmux-561)
    """
    bars = {}
    setattr(app, _PINNED, bars)
    table = getattr(app, "ph_panes", None) or {}
    if not table:
        return
    target = getattr(app, "_cmd_target_pane", None)
    scrolls = getattr(app, "pane_scroll", None) or {}
    style = None
    for pane in (getattr(app, "layout", None) or {}).get("panes", []):
        pid = pane.get("id")
        hist = (table.get(pid) or {}).get("h") or []
        if not hist or not hist[-1] or pid == target or scrolls.get(pid):
            continue
        if _is_clear(hist[-1]):
            continue                                        # ⑤
        px, py, pw, ph = (pane.get("x", 0), pane.get("y", 0),
                          pane.get("w", 0), pane.get("h", 0))
        if pw < 8 or ph < 4 or not (0 <= py < H):
            continue
        x0, x1 = max(0, px), min(px + pw, W)
        y_end = min(py + ph, H)
        if _mark_body(_row_text(cells, py, x0, x1)) is not None:
            continue                                        # ③
        prompt = hist[-1]
        first = next((_clean(ln).strip() for ln in prompt.split("\n")
                      if ln.strip()), "")
        if _visible(cells, first, py, y_end, x0, x1):
            continue                                        # ④
        rows = pinned_rows(prompt, max(0, pw - 2), min(PINNED_MAX_ROWS, ph - 2))
        if not rows:
            continue
        if style is None:
            from rich.style import Style
            from pytmuxlib.clientutil import theme_color
            style = Style(color="#FFFFFF", bold=True,
                          bgcolor=theme_color(app, "primary-darken-2"))
        _paint_rows(cells, rows, px, py, x0, x1, W, H, style)
        bars[pid] = (py, min(py + len(rows), H), x0, x1)


def _visible(cells, first, y0, y1, x0, x1):
    """프롬프트 첫 줄이 패널 안 어느 표식 줄에 보이나(④).

    claude 는 긴 프롬프트를 접어 그리므로 행 글은 프롬프트의 **앞부분**일 수 있고,
    반대로 짧은 프롬프트는 행보다 짧다 — 둘 중 짧은 쪽 길이(최대 30자)의 앞부분을 맞댄다.
    """
    if not first:
        return False
    for gy in range(y0, y1):
        body = _mark_body(_row_text(cells, gy, x0, x1))
        if not body:
            continue
        k = min(len(body), len(first), 30)
        if body[:k] == first[:k]:
            return True
    return False


def _paint_rows(cells, rows, px, py, x0, x1, W, H, style):
    from pytmuxlib.clientutil import _char_cells
    for i, text in enumerate(rows):
        gy = py + i
        if not (0 <= gy < H):
            continue
        for gx in range(x0, x1):
            cells[gy][gx] = (" ", style)
        gx = px + 1                                         # 좌우 한 칸 여백
        for ch in text:
            wch = _char_cells(ch)
            if gx + wch > x1 - 1:
                break
            if 0 <= gx < W:
                cells[gy][gx] = (ch, style)
                if wch == 2 and gx + 1 < W:
                    cells[gy][gx + 1] = ("", style)
            gx += wch


def pinned_at(app, x, y):
    """(x, y) 가 이번 프레임에 그린 고정 바 위면 그 패널 id, 아니면 None.

    누르는 자리(`client_click`)와 밑줄을 빼는 자리(`client_hit`)가 **같은 표**를 읽는다
    (pytmux-541 의 규율 — 둘이 따로 재면 언젠가 어긋난다)."""
    for pid, (y0, y1, x0, x1) in (getattr(app, _PINNED, None) or {}).items():
        if y0 <= y < y1 and x0 <= x < x1:
            return pid
    return None
