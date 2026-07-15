"""claude-code 클라이언트 콘텐츠-레이어 렌더(Phase 2c).

코어 client.py 의 `_scan_footer_zones` 를 이리로 이전했다. 코어 `_composite` 는
`plugins.client_render(app, cells, W, H)` 훅으로만 닿고, 이 모듈이 footer 클릭존
(_perm_zone/_remote_zone)을 app 에 채운다. (스티키 프롬프트 헤더 `_draw_headers` 는
2026-06-13 완전 제거 — 프롬프트 UI 는 claude-prompt-history 플러그인이 맡는다.)

무게: textual 을 import 하지 않는다(clientutil 헬퍼만 — 가볍다).
이 모듈은 매 프레임 호출되는 client_render 훅이 지연 import 한다(첫 호출 후 캐시)."""
from __future__ import annotations

import re

# 권한모드 footer 의 **모드 표시 부분**("⏵⏵ auto mode on" 등)만 클릭존으로 잡는
# 패턴 — 예전엔 footer 줄 전체가 존이라 힌트 문구("(shift+tab to cycle)"·"← for
# agents" 등)를 눌러도 팝업이 떠 오클릭이 잦았다(사용자 07-15). 글리프(⏵⏵/⏸)가
# 문구 앞에 붙어 있으면 존에 포함한다. 모드 문구 집합은 claude.py:claude_perm_mode
# 와 동기("is on" 접미 변형 포함 — 2026-06-25).
_PERM_LABEL_RE = re.compile(
    r"(?:(?:⏵⏵|⏸)\s*)?"
    r"(?:auto-accept\s+edits|accept\s+edits|auto\s+mode|plan\s+mode|"
    r"bypass\s+permissions)"
    r"(?:\s+(?:is\s+)?on\b)?", re.I)


def render(app, cells, W, H):
    """footer 클릭존을 스캔한다(매 _composite 1회)."""
    _scan_all_footer_zones(app, W, H)


def _scan_all_footer_zones(app, W, H):
    """모든 패널의 content 에서 footer 클릭존을 스캔해 app._perm_zone/_remote_zone 을
    매 프레임 새로 채운다(코어 _composite 가 하던 clear+scan 을 이리로 이전)."""
    from pytmuxlib.clientutil import _char_cells
    perm, remote, interrupt = {}, {}, {}
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
            # 권한모드 footer(claude.py:claude_perm_mode 와 같은 신호). 존은 줄
            # 전체가 아니라 **모드 표시 문구만** 덮는다("⏵⏵ auto mode on" 까지 —
            # 뒤의 "(shift+tab to cycle)" 힌트는 클릭존 밖, 사용자 07-15). 좁은 폭
            # 잘림 등으로 모드 문구가 안 보이면 예전대로 줄 전체 — 팝업 진입로는
            # 남긴다.
            if ("shift+tab to" in low or "mode on (shift" in low
                    or "⏵⏵" in text or "auto-accept" in low):
                m = _PERM_LABEL_RE.search(text)
                if m:
                    mx0 = p["x"] + sum(_char_cells(c) for c in text[:m.start()])
                    mx1 = min(p["x"] + p["w"], mx0 + sum(
                        _char_cells(c) for c in text[m.start():m.end()]))
                    perm[p["id"]] = (mx0, mx1, gy)
                else:
                    perm[p["id"]] = (x0, x1, gy)
            if "remote control" in low:
                remote[p["id"]] = (x0, x1, gy)
            # busy footer 의 'esc to interrupt' 만 덮는 좁은 클릭존 — perm 존(모드
            # 표시 문구)과 같은 줄의 다른 구간이라 겹치지 않지만, 클릭 핸들러는
            # 여전히 interrupt 를 perm 보다 먼저 검사한다(폭 잘림 fallback 으로 perm
            # 이 줄 전체일 땐 진부분집합이 되므로 순서가 유효하다).
            # 문구 시작('esc')부터 끝('interrupt')까지를 와이드 인지해 x 범위로 잡는다.
            imark = low.find("esc to interrupt")
            if imark >= 0:
                ix0 = p["x"] + sum(_char_cells(c) for c in text[:imark])
                iend = imark + len("esc to interrupt")
                ix1 = min(p["x"] + p["w"],
                          ix0 + sum(_char_cells(c) for c in text[imark:iend]))
                interrupt[p["id"]] = (ix0, ix1, gy)
    app._perm_zone = perm
    app._remote_zone = remote
    app._interrupt_zone = interrupt


def footer_zone_at(app, x, y):
    """좌표 (x,y) 가 Claude footer 클릭존(인터럽트/권한모드/원격제어) 안이면
    (pane_id, "interrupt"|"perm"|"remote") 반환, 아니면 None(§10 호버 강조·클릭 공용).
    인터럽트 존은 perm 존과 겹칠 수 있어(폭 잘림 fallback 시 perm=줄 전체) **먼저**
    검사해 우선권을 준다."""
    for pid, (zx0, zx1, zy) in getattr(app, "_interrupt_zone", {}).items():
        if zy == y and zx0 <= x < zx1:
            return (pid, "interrupt")
    for pid, (zx0, zx1, zy) in getattr(app, "_perm_zone", {}).items():
        if zy == y and zx0 <= x < zx1:
            return (pid, "perm")
    for pid, (zx0, zx1, zy) in getattr(app, "_remote_zone", {}).items():
        if zy == y and zx0 <= x < zx1:
            return (pid, "remote")
    return None

