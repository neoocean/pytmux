"""정본(파이썬 Textual) 클라의 **블록 고르기** — 명령 하나 + 그 출력을 골라 복사한다.

# 왜 이 자리인가 (pytmux-469 · 449 ⑴)

이 표면은 GUI 에 먼저 섰다(pytmux-18). [[pytmux-33]] ⓖ3 ⑷ 가 *"GUI 에 있는 것 중
정본에서도 구현 가능한 것은 정본에도"* 라고 정했고, 갈림 대장이 그 줄을 **할 일**로
들고 있었다 — 못 그릴 이유가 없었기 때문이다.

⛔ **코어가 아니라 플러그인에 있다.** 블록은 선택 기능이고 `blocks/` 를 지우면 조용히
사라져야 한다(delete-to-disable). 그래서 여기서 여는 것은 코어의 훅 셋뿐이다:

- `client_caps` — 「나는 블록을 그린다」를 hello 에 싣는다. 이 플러그인이 없으면 그
  능력이 안 실리고 서버는 블록 프레임을 **한 바이트도** 안 보낸다(§10-13 계약).
- `client_mode_key` — 코어가 모르는 모드(`block`)의 키는 그 모드를 세운 플러그인 것이다.
- `client_render` — 고른 블록을 캔버스 위에 반전으로 얹는다.

# 재료는 이미 다 있었다

경계는 서버가 절대 행으로 알려 주고(`segment.to_wire`), 범위를 글로 바꾸는 것은 드래그
복사가 쓰는 `copy_range` 한 명령이 이미 한다. 없던 것은 **캔버스 위의 상호작용**뿐이라,
새로 만든 것도 그것뿐이다(모드 하나 · 고른 자리 하나 · 강조 하나).

# GUI 와 같게 군다

[[pytmux-185]] 의 최소 요건은 **키 반응 · 취소 조건 · 포커스 이동**이다. 그래서 입구
(`esc b` · 팔레트 `select-blocks`) · 키(`↑`/`↓`·`Ctrl+C`·`Esc`/`q`/`Enter`) · 빈 패널일
때의 문구 둘 · 첫 선택이 **마지막 블록**인 것까지 GUI(`session_view::enter_block_select`)
를 그대로 따른다. 갈리면 그건 결함이다.
"""
from __future__ import annotations

from pytmuxlib import i18n

from .segment import row_span, sticky_at

#: `app` 에 붙는 필드 이름. 플러그인 네임스페이스를 지켜 코어 필드와 안 섞이게 한다.
_BLOCKS = "pane_blocks"
_PICK = "_block_pick"

#: 코어가 모르는 이 모드의 이름. `app.mode` 가 이 값이면 키는 전부 우리 것이다.
MODE = "block"

#: 이번 프레임에 그린 **스티키 바**(pytmux-520) — `{패널 id: (행, x0, x1, 시작 행)}`.
#: 클릭 판정이 읽는다. 그리는 자리와 누르는 자리가 같은 값을 보게 하려는 것이다.
_STICKY = "_block_sticky"


# ---- 상태 ────────────────────────────────────────────────────────────────────
def attach_client(app):
    """클라 인스턴스에 블록 상태를 설치한다(clock 플러그인의 `clock_panes` 와 같은 자리).

    ⚠ **패널마다 따로**여야 한다. 활성 패널의 목록으로 남의 패널을 강조하면 밝은 데와
    복사되는 데가 어긋나고, 그건 조용하다.
    """
    setattr(app, _BLOCKS, {})
    #: (패널 id, 목록 안 자리) 또는 None. 모드가 풀리면 함께 버린다.
    setattr(app, _PICK, None)


def blocks_of(app, pane_id):
    return getattr(app, _BLOCKS, {}).get(pane_id) or []


def handle_message(app, msg):
    """서버의 `blocks` 프레임. 코어가 모르는 `t` 라 여기로 온다.

    ⛔ **목록을 통째로 갈아 끼운다**(증분이 아니다) — 서버가 그렇게 보낸다. 빈 목록이
    오면 그 패널의 블록이 사라진 것이고(스크롤백 회전), 그때 고른 자리도 함께 접힌다.
    """
    if msg.get("t") != "blocks":
        return False
    pane = msg.get("pane")
    if pane is None:
        return True
    wire = msg.get("blocks")
    table = getattr(app, _BLOCKS, None)
    if table is None:
        return True
    # 신뢰 등급이 낮은 값이다 — 블록의 글자는 애초에 **패널 안 아무 프로그램**이 보낸
    # OSC 이고, 원격 링크 너머의 서버는 이 버전이 아닐 수 있다. 목록이 아니면 버린다.
    table[pane] = [b for b in wire if isinstance(b, dict)] if isinstance(wire, list) else []
    _clamp_pick(app)
    return True


def forget_panes(app, live_ids):
    """레이아웃에 없는 패널의 블록을 버린다(`pane_content` 형제들과 같은 자리·같은 이유).

    ⛔ 안 버리면 캐시가 **무한히 는다** — 신뢰 못 할 상류가 패널 id 를 흘리면 그것만으로
    메모리가 자란다(이 저장소가 이미 물린 적 있는 부류다).
    """
    table = getattr(app, _BLOCKS, None)
    if not table:
        return
    for pid in [k for k in table if k not in live_ids]:
        del table[pid]
    _clamp_pick(app)


def _clamp_pick(app):
    """고른 자리를 지금 목록 안으로 접는다. 접을 데가 없으면 모드째 나간다.

    목록은 상한(500)에서 잘리고 스크롤백 회전으로도 줄어든다 — **읽을 때마다** 접지
    않으면 `↑`/`↓` 가 없는 자리를 가리키고 `Ctrl+C` 가 엉뚱한 글을 담는다.

    ★ **포커스가 다른 패널로 옮겨간 것도 «접을 데가 없는» 것으로 친다**(검수
    2026-09-05 T-1). 블록 목록은 패널마다 따로다 — 모드를 붙잡고 있으면 `↑`/`↓` 가
    **안 보이는 패널**의 자리를 옮기고 `Ctrl+C` 는 그 패널의 글을 담는데, 화면에는
    아무 반응이 없다(`client_render` 가 활성 아닌 패널은 그리기만 건너뛴다). GUI 는
    이 계약을 이미 갖고 있고(`session_view::drop_block_pick_unless_selecting`),
    [[pytmux-185]] 는 정본이 **같게 굴 것**을 요구한다.
    """
    pick = getattr(app, _PICK, None)
    if pick is None:
        return
    pane, index = pick
    if (getattr(app, "layout", None) or {}).get("active") != pane:
        _leave(app)
        return
    count = len(blocks_of(app, pane))
    if count == 0:
        _leave(app)
        return
    if index >= count:
        setattr(app, _PICK, (pane, count - 1))


def _leave(app):
    setattr(app, _PICK, None)
    if getattr(app, "mode", None) == MODE:
        app.mode = "normal"
        app.status.refresh()


# ---- 입구 ────────────────────────────────────────────────────────────────────
def enter(app):
    """블록 고르기 모드로. 고를 것이 없으면 **안 들어가고 그렇다고 말한다**.

    # 왜 빈 목록에서 안 들어가나

    블록 경계는 셸 통합(OSC 133)이 보내 주는 것이라, 그 스크립트를 안 읽은 셸에는
    블록이 **하나도 없다**. 그 패널에서 모드에 들여보내면 배지만 켜진 채 키가 통째로
    죽는다 — 사용자에게는 "이 기능이 고장났다"로 보이고 진짜 원인은 화면 어디에도 안
    적혀 있다.

    ⚠ **그 한 줄이 패널마다 달라야 한다**(pytmux-21). Claude 패널의 경계는 OSC 가
    아니라 화면 글의 프롬프트 마커에서 나오므로(`promptblocks.py`), 거기서 "셸 통합을
    켜라"고 말하면 **고칠 수 없는 것을 고치라는 안내**가 된다.

    첫 선택이 **마지막 블록**인 이유: 방금 친 명령의 출력을 집으려는 것이 이 기능의 첫
    쓰임이고 그것이 목록의 끝이다. 첫 블록에서 시작하면 `↑`을 수십 번 눌러야 한다.
    """
    pane = app.layout.get("active")
    if pane is None:
        return False
    blocks = blocks_of(app, pane)
    if not blocks:
        app.display_message(i18n.t("blocks.none_claude" if _is_claude(app, pane)
                                   else "blocks.none_shell"), severity="warn")
        return False
    setattr(app, _PICK, (pane, len(blocks) - 1))
    app.mode = MODE
    app.status.refresh()
    app._composite()
    return True


def _is_claude(app, pane_id):
    """이 패널이 Claude 인가 — 안내 문구가 갈리는 유일한 자리.

    claude-code 플러그인이 없으면 항상 False 다(셸 문구). 그것이 맞다 — 그 플러그인이
    없으면 턴 경계도 안 생긴다.
    """
    fn = getattr(app, "is_claude_pane", None)
    try:
        return bool(fn(pane_id)) if fn is not None else False
    except Exception:
        return False


def handle_command(app, c, args):
    if c == "select-blocks":
        enter(app)
        return True
    if c == "summary":
        open_summary(app)
        return True
    return False


# ---- 요약 판(pytmux-538) ─────────────────────────────────────────────────────
#: 블록의 **부류 표식** — 네이티브 클라의 `proto::blocks::Tone`/`badge()` 와 같은 표다.
#: 두 클라의 요약 판에 같은 블록이 다른 글자로 서면 그것이 갈림이다.
def _badge(block):
    state = str(block.get("state") or "")
    exit_code = block.get("exit")
    if state == "turn":
        return "❯"
    if state == "running":
        return "···"
    if state == "done":
        if exit_code is None:
            return "??"
        return "ok" if exit_code == 0 else "err"
    return "…"


def summary_lines(app, pane):
    """요약 판의 줄들 — 머리줄 한 줄 + 블록마다 한 줄(오래된 것이 위 · 최근이 아래).

    네이티브 클라의 `render_summary` 와 같은 재료다(`footer::head` 의 머리줄 ·
    `render_block` 의 표식 + 명령 + cwd). 저쪽은 꼬리 다섯만 그리지만 이 판은 굴릴 수
    있어 **전부** 싣는다 — 잘라 내면 「블록이 안 생긴다」와 구분이 안 되는 그 증상이다."""
    blocks = blocks_of(app, pane)
    lines = [i18n.t("blocks.summary_head", n=len(blocks))]
    for b in blocks:
        cmd = str(b.get("cmd") or "") or i18n.t("blocks.summary_no_cmd")
        cwd = str(b.get("cwd") or "")
        line = f"{_badge(b):<3} {cmd}"
        if cwd:
            line += f"   {cwd}"
        lines.append(line)
    return lines


def open_summary(app):
    """`summary` — 활성 패널의 블록 목록을 읽는 판(범용 `InfoScreen`)으로 띄운다.

    고르는 판이 아니라 **훑는 판**이다(네이티브 클라의 `Screen::Summary` 가 그렇다 —
    아무 키나 닫고 ↑↓ 가 굴린다). 블록이 없으면 판 대신 한 줄로 말한다(`enter` 와 같은
    처방 — 빈 판은 「고장났다」로 읽힌다)."""
    pane = app.layout.get("active")
    if pane is None:
        return False
    if not blocks_of(app, pane):
        app.display_message(i18n.t("blocks.none_claude" if _is_claude(app, pane)
                                   else "blocks.none_shell"), severity="warn")
        return False
    from pytmuxlib.clientscreens import InfoScreen
    app.push_screen(InfoScreen(summary_lines(app, pane), title=i18n.t("blocks.summary_title")))
    return True


# ---- 모드 안의 키 ────────────────────────────────────────────────────────────
def client_mode_key(app, event):
    """`app.mode == "block"` 동안의 키 하나. 소비했으면 True.

    ⛔ **나머지는 버린다 — 패널로 흘리지 않는다.** 흘리면 블록을 고르는 동안 친 글자가
    셸에 찍힌다(정본 esc·스크롤 모드와 같은 규율이고 GUI 도 같다).
    """
    if getattr(app, "mode", None) != MODE:
        return False
    _clamp_pick(app)
    pick = getattr(app, _PICK, None)
    if pick is None:                      # 목록이 비어 모드가 이미 풀렸다
        return True
    key = event.key
    # ★ 나가는 키는 셋 다 같은 뜻이다 — 스크롤 모드의 `q`·`Esc`·`Enter` 와 같은 배정이라
    #   고르기를 끝냈다는 말을 세 손버릇 어느 쪽으로도 할 수 있다.
    if key in ("escape", "enter") or event.character == "q":
        _leave(app)
        app._composite()
        return True
    if key == "down":
        _move(app, +1)
        return True
    if key == "up":
        _move(app, -1)
        return True
    if key == "ctrl+c":
        copy_selected(app)
        return True
    return True


def _move(app, step):
    pane, index = getattr(app, _PICK)
    count = len(blocks_of(app, pane))
    nxt = index + step
    # 목록은 오래된 것 → 최근 순이고 화면도 그 순서로 아래로 흐른다. 그래서 `↓` 가 곧
    # **더 최근**이다 — 화면에서 아래로 가는 것과 같은 방향이라야 손이 안 어긋난다.
    if not (0 <= nxt < count):
        return
    setattr(app, _PICK, (pane, nxt))
    app._composite()


def copy_selected(app):
    """고른 블록 전체(명령 + 그 출력)를 복사한다.

    **드래그 복사와 같은 길**이다 — 같은 `copy_range` 를 보내고, 회신(`selection`)이
    오면 접힘 되돌리기·클립보드·"N자 복사됨" 한 줄까지 그 경로가 그대로 한다. 여기서
    따로 클립보드를 건드리면 두 복사가 서로 다른 규칙(`copy-unwrap` 등)을 타기 시작한다.

    열 범위가 `0..w-1` 인 이유: 블록은 **줄 단위**다. 서버의 추출
    (`model.Pane.extract_range`)은 첫 줄을 `x0` 부터 끝 줄을 `x1` 까지 뽑으므로 줄
    전체를 원하면 패널 폭 끝을 준다(넘겨도 서버가 클램프한다).
    """
    pick = getattr(app, _PICK, None)
    if pick is None:
        return False
    pane, index = pick
    span = _span(app, pane, index)
    rect = _pane_rect(app, pane)
    if span is None or rect is None:
        return False
    y0, y1 = span
    w = rect[2]
    # 접힘을 되돌릴 기하 — 드래그 복사가 재는 것과 같은 값이다(폭, 첫 열).
    app._copy_unwrap_geom = (w, 0)
    app.send_cmd("copy_range", pane=pane, y0=y0, x0=0, y1=y1, x1=max(0, w - 1))
    return True


# ---- 그림 ────────────────────────────────────────────────────────────────────
def client_render(app, cells, W, H):
    """스티키 바(위로 굴린 패널의 「지금 보는 출력의 프롬프트」)와 고른 블록의 강조."""
    _render_sticky(app, cells, W, H)
    _render_pick(app, cells, W, H)


def _render_sticky(app, cells, W, H):
    """위로 굴린 패널마다 뷰포트 첫 줄이 **안에 있는 블록**의 명령(프롬프트)을 그 패널
    첫 줄에 띠로 얹는다(pytmux-520 · 사용자 제보 2026-09-26: *"위로 스크롤하면 이전
    프롬프트가 보이고 클릭하면 그 자리까지 스크롤"*).

    # 규칙은 `segment.sticky_at` 한 자리다

    라이브면 없다(바가 라이브 글을 가리면 안 된다) · 첫 줄이 곧 프롬프트 줄이면 없다
    (두 번 보이지 않게). GUI 는 같은 함수의 짝(`proto::blocks::sticky_at`)으로 같은 답을
    낸다 — 픽스처가 둘을 맞댄다.

    # 모드와 무관하다

    고른 블록의 강조(`_render_pick`)는 블록 모드에서만이지만 이 띠는 **굴린 상태**의
    표식이라 모드를 안 본다 — 휠로 올린 사람이 그 모드에 있을 리 없다.

    띠의 모양은 프롬프트 이력 미리보기 바(`claude-prompt-history/render.py`)와 같다 —
    순백 볼드 / `primary-darken-2`. 같은 뜻(「이 프롬프트」)의 띠가 두 모양이면 어느
    쪽이 무엇인지 사람이 가려야 한다.
    """
    bars = {}
    setattr(app, _STICKY, bars)
    table = getattr(app, _BLOCKS, None)
    if not table:
        return
    layout = getattr(app, "layout", None) or {}
    if not layout.get("panes"):
        return
    style = None
    for pane_id, wire in table.items():
        if not wire:
            continue
        scroll = (getattr(app, "pane_scroll", None) or {}).get(pane_id) or 0
        top = (getattr(app, "pane_top", None) or {}).get(pane_id)
        if not scroll or top is None:
            continue
        rect = _pane_rect(app, pane_id)
        if rect is None:
            continue
        px, py, pw, ph = rect
        if pw < 4 or ph < 1 or not (0 <= py < H):
            continue
        bottom = _live_bottom(app, pane_id)
        index = sticky_at(wire, top, scroll, bottom)
        if index is None:
            continue
        span = row_span(wire, index, bottom)
        if span is None:
            continue
        cmd = str(wire[index].get("cmd") or "")
        if style is None:
            from rich.style import Style
            from pytmuxlib.clientutil import theme_color
            style = Style(color="#FFFFFF", bold=True,
                          bgcolor=theme_color(app, "primary-darken-2"))
        from pytmuxlib.clientutil import _char_cells
        x1 = min(px + pw, W)
        for gx in range(max(0, px), x1):
            cells[py][gx] = (" ", style)
        # `▲` = 「이 프롬프트는 위에 있다 · 누르면 거기로」. 좌우 한 칸 여백.
        gx = px + 1
        used = 0
        budget = max(0, pw - 2)
        for ch in "▲ " + cmd:
            wch = _char_cells(ch)
            if used + wch > budget:
                break
            if 0 <= gx < W:
                cells[py][gx] = (ch, style)
                if wch == 2 and 0 <= gx + 1 < W:
                    cells[py][gx + 1] = ("", style)
            gx += wch
            used += wch
        bars[pane_id] = (py, max(0, px), x1, span[0])


def client_click(app, x, y, button=1):
    """캔버스 왼쪽 클릭 — 스티키 바 위면 그 프롬프트 줄이 첫 줄에 오게 굴린다
    (pytmux-520). 소비했으면 True.

    Δ 는 `top − 시작 행`(과거 방향이 +, `send_scroll` 의 부호) — 바가 가리키는 블록의
    시작 줄이 뷰포트 첫 줄이 된다. 자리는 **이번 프레임에 그린 그 값**(`_STICKY`)이라
    그린 곳과 누르는 곳이 어긋날 수 없다.
    """
    if button != 1:
        return False
    bars = getattr(app, _STICKY, None) or {}
    for pane_id, (row, x0, x1, start) in bars.items():
        if y != row or not (x0 <= x < x1):
            continue
        top = (getattr(app, "pane_top", None) or {}).get(pane_id)
        send = getattr(app, "send_scroll", None)
        if top is None or send is None:
            return False
        send(pane_id, delta=top - start)
        return True
    return False


def _render_pick(app, cells, W, H):
    """고른 블록을 **뷰포트에 걸친 부분만** 반전으로 얹는다.

    드래그 선택 강조와 같은 모양(같은 `_with_reverse`)이라 두 강조가 한 화면에서
    이질적으로 보이지 않는다.

    # 왜 잘라 내나

    블록은 스크롤백 좌표라 화면보다 길 수 있다(수백 줄짜리 빌드 로그가 흔하다). 안
    자르면 강조가 패널 밖으로 새어 이웃 패널·크롬 위에 그려진다. 통째로 화면 밖이면
    아무것도 안 그린다 — 그릴 것이 없다는 뜻이지 선택이 풀린 것은 아니다.
    """
    from pytmuxlib.clientutil import _with_reverse
    if getattr(app, "mode", None) != MODE:
        return
    pick = getattr(app, _PICK, None)
    if pick is None:
        return
    pane, index = pick
    # 포커스가 옮겨갔으면 안 그린다. 모드를 푸는 것은 다음 키가 하고, 여기는 그림만
    # 즉시 사실과 맞춘다(GUI `block_mark` 와 같은 규칙).
    if app.layout.get("active") != pane:
        return
    span = _span(app, pane, index)
    rect = _pane_rect(app, pane)
    top = getattr(app, "pane_top", {}).get(pane)
    if span is None or rect is None or top is None:
        return
    px, py, pw, ph = rect
    y0, y1 = span
    last = top + max(0, ph - 1)
    if y1 < top or y0 > last:
        return
    for y in range(max(y0, top) - top, min(y1, last) - top + 1):
        gy = py + y
        if not (0 <= gy < H):
            continue
        for gx in range(max(0, px), min(px + pw, W)):
            ch, st = cells[gy][gx]
            cells[gy][gx] = (ch, _with_reverse(st))


def client_statusbar_badges(app, status, segs, w, w0=None):
    """`[block]` 배지 — 지금 이 모드라는 것을 화면이 말한다.

    ⛔ 배지가 없으면 **모드가 서 있는지 사용자가 알 길이 없다**(pytmux-467 이 `[prefix]`
    에서 세운 것과 같은 근거). 색은 esc(`accent`)·prefix(`primary`) 어느 쪽과도 달라야
    한다 — 세 배지가 같은 색이면 배지가 어느 모드인지 못 말한다.
    """
    if w0 is None or getattr(app, "mode", None) != MODE:
        return w0
    from rich.segment import Segment
    from rich.style import Style
    from pytmuxlib.clientutil import theme_color
    text = i18n.t("ui.block_mode_badge")
    segs.append(Segment(text, Style(color="black",
                                    bgcolor=theme_color(status, "success"),
                                    bold=True)))
    # ASCII 한 토막이라 폭 = 글자 수다(`[prefix]` 배지와 같다).
    return w0 + len(text)


# ---- 좌표 ────────────────────────────────────────────────────────────────────
def _span(app, pane, index):
    """이 블록의 절대 행 범위. 판정은 `segment.row_span` 한 자리가 한다."""
    return row_span(blocks_of(app, pane), index, _live_bottom(app, pane))


def _live_bottom(app, pane):
    """이 패널에서 **지금 살아 있는 마지막 줄**의 절대 행.

    뷰포트 첫 줄(`top`)은 스크롤한 만큼 위로 가 있으므로 라이브 하단은
    `top + scr + h - 1` 이다. 아직 안 끝난 블록의 끝을 여기서 얻는다 — 그 블록은 지금도
    자라는 중이라 서버가 끝을 안 알려 주고, 물어볼 수 있는 것은 "지금까지 어디까지
    찼나"뿐이다.
    """
    top = getattr(app, "pane_top", {}).get(pane) or 0
    scr = getattr(app, "pane_scroll", {}).get(pane) or 0
    rect = _pane_rect(app, pane)
    h = rect[3] if rect else 0
    return top + scr + max(0, h - 1)


def _pane_rect(app, pane):
    for p in app.layout.get("panes", []):
        if p.get("id") == pane:
            return (p.get("x", 0), p.get("y", 0), p.get("w", 0), p.get("h", 0))
    return None
