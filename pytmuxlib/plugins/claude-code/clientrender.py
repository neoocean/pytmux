"""claude-code 클라이언트 콘텐츠-레이어 렌더(Phase 2c).

코어 client.py 의 `_scan_footer_zones` 를 이리로 이전했다. 코어 `_composite` 는
`plugins.client_render(app, cells, W, H)` 훅으로만 닿고, 이 모듈이 footer 클릭존
(_perm_zone/_remote_zone/_interrupt_zone/_tokens_zone)을 app 에 채운다. (스티키 프롬프트
헤더 `_draw_headers` 는 2026-06-13 완전 제거 — 프롬프트 UI 는 claude-prompt-history
플러그인이 맡는다.)

⚠ **규칙은 여기 없다** — 어느 문구가 누르는 자리인가는 [`footerzones`](footerzones)
한 벌이고 서버도 같은 함수를 부른다(pytmux-2 · pytmux-23). 이 모듈은 그 규칙을 이
클라의 화면 사정(패널 목록·pane_content)에 대고 돌릴 뿐이다.

무게: textual 을 import 하지 않는다(가볍다).
이 모듈은 매 프레임 호출되는 client_render 훅이 지연 import 한다(첫 호출 후 캐시)."""
from __future__ import annotations

from .footerzones import PRIORITY, scan_pane


def render(app, cells, W, H):
    """footer 클릭존을 스캔한다(매 _composite 1회)."""
    _scan_all_footer_zones(app, W, H)


def _scan_all_footer_zones(app, W, H):
    """모든 패널의 content 에서 footer 클릭존을 스캔해 app._perm_zone 등을 매 프레임
    새로 채운다(코어 _composite 가 하던 clear+scan 을 이리로 이전)."""
    found = {kind: {} for kind in PRIORITY}
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
        # 창 밖으로 밀린 행은 존이 될 수 없다 — 종전에는 행마다 `0 <= gy < H` 를
        # 봤고, 그 판정을 여기(줄을 고르는 자리)로 옮겼다. 규칙 쪽은 화면 높이를
        # 모르는 편이 낫다(서버는 그 클라의 H 를 모른다).
        lines = ["".join(seg[0] for seg in row) for row in rows]
        top = max(0, -p["y"])                    # 위로 잘린 행 수
        end = max(top, min(p["h"], H - p["y"]))  # 아래로 잘리는 자리
        for kind, zone in scan_pane(lines[top:end], p["x"], p["y"] + top,
                                    p["w"], end - top).items():
            found[kind][p["id"]] = zone
    # 종류 목록도 규칙 쪽 한 벌에서 온다 — 여기 손으로 적어 두면 규칙에 종류가 하나
    # 늘었을 때 이 줄이 KeyError 로 **매 프레임** 터진다(서버는 로그로만 삼킨다).
    for kind, zones in found.items():
        setattr(app, f"_{kind}_zone", zones)


def footer_zone_at(app, x, y):
    """좌표 (x,y) 가 Claude footer 클릭존(인터럽트/권한모드/원격제어/토큰) 안이면
    (pane_id, "interrupt"|"perm"|"remote"|"tokens") 반환, 아니면 None(§10 호버 강조·
    클릭 공용).

    겹칠 때 무엇이 먼저인가는 [`footerzones.PRIORITY`](footerzones) 가 정한다 — 종전엔
    그 순서가 이 함수 안에만 있었고, 그래서 같은 규칙을 쓰는 GUI 는 순서를 **못 물려받았다**
    (자리 목록에서 먼저 맞는 것을 집으므로 서버가 싣는 차례가 곧 그 클라의 우선순위다)."""
    for kind in PRIORITY:
        attr = f"_{kind}_zone"
        for pid, (zx0, zx1, zy) in getattr(app, attr, {}).items():
            if zy == y and zx0 <= x < zx1:
                return (pid, kind)
    return None
