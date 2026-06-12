"""claude-code 클라이언트 콘텐츠-레이어 렌더(Phase 2c).

코어 client.py 의 `_draw_claude_headers`·`_scan_footer_zones` 를 이리로 이전했다.
코어 `_composite` 는 `plugins.client_render(app, cells, W, H)` 훅으로만 닿고, 이
모듈의 함수들이 `app` 의 Claude 상태(pane_claude/claude_header_on/_hdr_focus)를
읽어 헤더를 그리고, footer 클릭존(_perm_zone/_remote_zone)·활성 헤더 행
(_active_hdr_row)을 app 에 채운다.

무게: textual 을 import 하지 않는다(rich.Style 과 clientutil 헬퍼만 — 둘 다 가볍다).
이 모듈은 매 프레임 호출되는 client_render 훅이 지연 import 한다(첫 호출 후 캐시)."""
from __future__ import annotations


def render(app, cells, W, H):
    """콘텐츠 위에 Claude 헤더를 그리고 footer 클릭존을 스캔한다(매 _composite 1회).
    순서: footer 존을 먼저 비우고 다시 채운 뒤(코어 ESC 포커스 강조·클릭이 읽음) 헤더를 그린다.
    코어 `_draw_tab_close` 가 뒤이어 `_active_hdr_row` 를 읽어 [x] 를 헤더 행에 올린다."""
    _scan_all_footer_zones(app, W, H)
    _draw_headers(app, cells, W, H)


def _scan_all_footer_zones(app, W, H):
    """모든 패널의 content 에서 footer 클릭존을 스캔해 app._perm_zone/_remote_zone 을
    매 프레임 새로 채운다(코어 _composite 가 하던 clear+scan 을 이리로 이전)."""
    from pytmuxlib.clientutil import _char_cells
    perm, remote = {}, {}
    panes = app.layout.get("panes", [])
    pane_claude = getattr(app, "pane_claude", {})
    for p in panes:
        content = app.pane_content.get(p["id"])
        if not content:
            continue
        rows, _cursor = content
        ci = pane_claude.get(p["id"])
        if not (ci and ci.get("claude")):
            continue
        for ry, row in enumerate(rows):
            if ry >= p["h"]:
                break
            gy = p["y"] + ry
            if not (0 <= gy < H):
                continue
            text = "".join(seg[0] for seg in row)
            low = text.lower()
            stripped = text.strip()
            if not stripped:
                continue
            # 줄의 실제 글자 범위(앞뒤 공백 제외)를 클릭존 x 범위로 — 와이드 인지.
            lead = len(text) - len(text.lstrip())
            x0 = p["x"] + sum(_char_cells(c) for c in text[:lead])
            x1 = min(p["x"] + p["w"],
                     x0 + sum(_char_cells(c) for c in stripped))
            # 권한모드 footer(claude.py:claude_perm_mode 와 같은 신호)
            if ("shift+tab to" in low or "mode on (shift" in low
                    or "⏵⏵" in text or "auto-accept" in low):
                perm[p["id"]] = (x0, x1, gy)
            if "remote control" in low:
                remote[p["id"]] = (x0, x1, gy)
    app._perm_zone = perm
    app._remote_zone = remote


def _draw_headers(app, cells, W, H):
    """Claude Code 패널 내부 맨 윗줄에 마지막 프롬프트를 스티키 헤더로 표시.
    스크롤과 무관(합성 시 항상 내용 최상단에 덮어 그림). 표시 여부는 전역 옵션
    claude_header_on(명령 `claude-header on|off`)으로 끄고 켠다."""
    from rich.style import Style
    from pytmuxlib.clientutil import _char_cells, theme_color
    # 활성 패널 헤더(프롬프트) 행 — 닫기 [x] 를 이 행으로 올려 그리기 위해 기록한다
    # (#15). 헤더가 없으면 None → [x] 는 콘텐츠 첫 행에 그대로(코어 _draw_tab_close).
    app._active_hdr_row = None
    active = app.layout.get("active")
    pane_claude = getattr(app, "pane_claude", {})
    if not getattr(app, "claude_header_on", False) or not pane_claude:
        return
    hdr_focus = getattr(app, "_hdr_focus", None)
    # 헤더 배경은 진한 파랑(primary-darken-2) — 본문/활성 테두리(primary)보다 한 단계
    # 어둡게. ESC 모드 헤더 포커스(#5)면 강조색(accent)으로 구분한다.
    base_st = Style(color="white",
                    bgcolor=theme_color(app, "primary-darken-2"), bold=True)
    # 비활성 패널의 헤더 바는 한 단계 더 어둡게(요청) — 활성(밝은 파랑)과 비활성을 더
    # 또렷이 구분한다. 활성 패널만 base_st(진파랑)를 쓴다.
    inactive_hdr_st = Style(color="white",
                            bgcolor=theme_color(app, "primary-darken-3"),
                            bold=True)
    focus_st = Style(color="black", bgcolor=theme_color(app, "accent"),
                     bold=True)
    for p in app.layout.get("panes", []):
        if not p.get("claude_hdr"):   # 서버가 헤더 행을 예약한 패널만(#1)
            continue
        info = pane_claude.get(p["id"])
        if not info or not info.get("claude") or not info.get("prompt"):
            continue
        # 서버가 내용 영역을 한 행 내렸으므로(cy=p["y"]) 헤더는 그 위 한 줄
        # (p["y"]-1, 예약된 행)에 그린다(#1).
        cx, cy, cw = p["x"], p["y"] - 1, p["w"]
        if cw < 6 or not (0 <= cy < H):
            continue
        if p["id"] == hdr_focus:
            hdr_st = focus_st
        elif p["id"] == active:
            hdr_st = base_st
        else:
            hdr_st = inactive_hdr_st   # 비활성 헤더 바는 더 어둡게(요청)
        for xx in range(cx, min(cx + cw, W)):   # 헤더 배경
            cells[cy][xx] = (" ", hdr_st)
        text_start = cx + 1                      # 좌측 1칸 여백
        # 활성 패널은 이 헤더 행 우측 끝에 닫기 [x](3칸)가 올라오므로(#15), 프롬프트
        # 본문은 그 직전 한 칸까지만(= 3 + 1 칸 비움) 늘어나게 한다.
        if p["id"] == active:
            app._active_hdr_row = cy
            budget = max(0, cw - 1 - 4)
            # 닫기 [x](우측 3칸)와 프롬프트 헤더 사이 한 칸은 헤더색이 아닌 터미널
            # 배경으로 비운다(빈 Style → 터미널 기본 배경, #).
            gapx = cx + cw - 4
            if 0 <= gapx < W:
                cells[cy][gapx] = (" ", Style())
        else:
            budget = max(0, cw - 1)
        gx = text_start
        for chh in "▷ " + info["prompt"]:
            wch = _char_cells(chh)
            if gx - text_start + wch > budget:
                break
            if 0 <= gx < W:
                cells[cy][gx] = (chh, hdr_st)
                if wch == 2 and gx + 1 - text_start < budget and \
                        0 <= gx + 1 < W:
                    cells[cy][gx + 1] = ("", hdr_st)
            gx += wch


def footer_zone_at(app, x, y):
    """좌표 (x,y) 가 Claude footer 클릭존(권한모드/원격제어) 안이면
    (pane_id, "perm"|"remote") 반환, 아니면 None(§10 호버 강조·클릭 공용)."""
    for pid, (zx0, zx1, zy) in getattr(app, "_perm_zone", {}).items():
        if zy == y and zx0 <= x < zx1:
            return (pid, "perm")
    for pid, (zx0, zx1, zy) in getattr(app, "_remote_zone", {}).items():
        if zy == y and zx0 <= x < zx1:
            return (pid, "remote")
    return None


def claude_header_panes(app):
    """헤더가 그려지는 Claude 패널 id 를 레이아웃 순서로 반환(#5 헤더 포커스)."""
    out = []
    if not getattr(app, "claude_header_on", False):
        return out
    pane_claude = getattr(app, "pane_claude", {})
    for p in app.layout.get("panes", []):
        if not p.get("claude_hdr"):   # 헤더 행이 실제 예약된 패널만(#1)
            continue
        info = pane_claude.get(p["id"])
        if info and info.get("claude") and info.get("prompt"):
            out.append(p["id"])
    return out
