"""claude-prompt-history 클라이언트 렌더 — 직전 프롬프트 미리보기 패널(transient).

`:` 명령 프롬프트에서 `prompt-history`(pane_scoped)를 작성하는 동안에만, 코어가 세우는
`app._cmd_target_pane`(대상=활성 패널)의 **외곽선 안쪽 위에서부터** 직전 프롬프트를
오버레이로 그린다. 멀티라인 프롬프트는 1행부터 시작해 여러 줄로 늘리되 최대
`app.ph_max_lines` 행(설정 가능)까지. 서버 행 예약 없이 content 위에 덮어 그리므로
명령 작성이 끝나(프롬프트 닫힘) `_cmd_target_pane=None` 이 되면 다음 합성에서 사라진다.

textual 비의존(rich.Style + clientutil 헬퍼). client_render 훅이 지연 import 한다."""
from __future__ import annotations


def draw_preview(app, cells, W, H):
    """직전 프롬프트 미리보기를 대상 패널 위에 그린다. 그릴 게 없으면 no-op."""
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

    bar_st = Style(color="white", bgcolor=theme_color(app, "primary-darken-2"),
                   bold=True)
    # 잘림 표시(▾)·시작 마커(▷) 색은 본문과 같은 바 스타일을 쓴다(단순·고대비).
    truncated = len(plines) > nshow

    for row in range(nshow):
        gy = py + row
        if not (0 <= gy < H):
            continue
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
