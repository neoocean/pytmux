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

from .segment import row_span

#: `app` 에 붙는 필드 이름. 플러그인 네임스페이스를 지켜 코어 필드와 안 섞이게 한다.
_BLOCKS = "pane_blocks"
_PICK = "_block_pick"

#: 코어가 모르는 이 모드의 이름. `app.mode` 가 이 값이면 키는 전부 우리 것이다.
MODE = "block"


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
    """
    pick = getattr(app, _PICK, None)
    if pick is None:
        return
    pane, index = pick
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
    if c != "select-blocks":
        return False
    enter(app)
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
