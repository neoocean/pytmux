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
