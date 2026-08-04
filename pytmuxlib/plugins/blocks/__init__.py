"""블록 플러그인 — 셸 통합(OSC 133)으로 명령 경계를 알아내 클라에 알린다.

# 왜 플러그인인가

블록은 선택 기능이다. 셸 통합을 안 깐 사용자에게는 아무 일도 일어나지 않아야 하고,
이 디렉토리를 지우면 기능이 조용히 사라져야 한다(delete-to-disable). 코어가 건드리는
곳은 OSC 훅 하나뿐이다(`vtparse` → `Pane._on_osc` → 이 훅).

# 기존 클라는 한 바이트도 더 받지 않는다

클라가 hello 에 `caps: ["blocks"]` 를 실어 보내야만 `blocks` 메시지를 보낸다. 파이썬
Textual 클라는 그 능력을 광고하지 않으므로 프레임이 늘지 않는다 — 새 기능이 기존
클라의 대역폭·파싱 비용을 건드리지 않게 하는 계약이다.

# 서버가 권위다

블록은 서버가 만든다. 클라는 받아 그리기만 한다 — 두 클라(파이썬·네이티브)가 각자
경계를 추정하면 서로 다른 블록을 보게 된다.
"""
from .segment import MAX_BLOCKS, Segmenter

#: 패널에 붙는 세그멘터를 담는 속성명. 플러그인 네임스페이스를 지켜 코어 필드와
#: 섞이지 않게 한다.
_ATTR = "_blocks_segmenter"


def _segmenter(pane):
    seg = getattr(pane, _ATTR, None)
    if seg is None:
        seg = Segmenter()
        setattr(pane, _ATTR, seg)
    return seg


def _absolute_row(pane):
    """커서가 있는 **절대** 스크롤백 행.

    뷰포트 기준 좌표를 쓰면 스크롤할 때 블록이 따라 움직인다. 스크롤백 길이 + 커서 행이
    스크롤과 무관한 좌표다.
    """
    screen = getattr(pane, "screen", None)
    if screen is None:
        return 0
    history = getattr(screen, "history", None)
    base = len(history.top) if history is not None else 0
    cursor = getattr(screen, "cursor", None)
    return base + (getattr(cursor, "y", 0) if cursor is not None else 0)


def pane_osc(pane, code, param):
    """코어가 넘겨 준 OSC 를 블록 경계로 해석한다.

    타이틀(0/1/2)은 코어가 이미 처리해 여기 오지 않는다. 우리가 보는 것은 133(경계)·
    7(cwd)·633(명령 텍스트) 뿐이고, 나머지는 세그멘터가 무시한다.
    """
    if code not in ("133", "7", "633"):
        return
    seg = _segmenter(pane)
    if seg.on_osc(code, param, row=_absolute_row(pane)):
        # 다음 flush 가 이 패널의 블록을 보내도록 표시한다.
        pane._blocks_dirty = True


def blocks_wire(pane):
    """이 패널의 블록을 와이어 형태로. 블록이 없으면 None(= 보낼 것 없음)."""
    seg = getattr(pane, _ATTR, None)
    if seg is None or not seg.blocks:
        return None
    return seg.to_wire()


def pane_cwd(pane):
    """이 패널 셸의 작업 디렉터리. 모르면 None.

    출처는 **셸이 OSC 7 로 알려 준 값**이라 프로브가 0 이다 — 서버의
    `_pane_cwd(pane)`(pid → /proc·PEB·lsof)와 달리 아무것도 물어보지 않는다. 그래서
    패널 글의 상대경로를 푸는 기준으로 이걸 쓴다(§10-21ⓧ2 / pytmux-24).

    블록 목록과 **따로** 내주는 이유: 값은 문자열 하나인데 블록은 최대 500개다. 경로만
    풀면 되는 클라에게 그 목록을 통째로 보내는 건 caps 게이트가 막으려던 바로 그 비용이다.
    """
    seg = getattr(pane, _ATTR, None)
    return getattr(seg, "cwd", None) if seg is not None else None


def blocks_dirty(pane):
    return bool(getattr(pane, "_blocks_dirty", False))


def clear_blocks_dirty(pane):
    pane._blocks_dirty = False


class _BlocksPlugin:
    """레지스트리가 훅을 찾아가는 객체. 실제 로직은 위 모듈 함수들에 있다."""

    name = "blocks"
    description = "셸 통합(OSC 133)으로 명령 경계를 감지해 블록을 클라에 보낸다"

    #: 페더레이션: 이 서버가 업스트림에 붙을 때 광고할 능력. 이게 있어야 업스트림이
    #: 블록을 내려보내고, 원격 탭을 보는 클라도 블록을 받는다. `cwd` 도 여기서 나온다 —
    #: 안 광고하면 원격 탭에서만 조용히 경로가 안 풀린다.
    upstream_caps = ("blocks", "cwd")

    #: 코어가 OSC 를 넘겨 주는 훅.
    pane_osc = staticmethod(pane_osc)
    #: flush 가 보낼 것이 있는지 묻는 훅.
    blocks_dirty = staticmethod(blocks_dirty)
    blocks_wire = staticmethod(blocks_wire)
    clear_blocks_dirty = staticmethod(clear_blocks_dirty)
    #: 패널 cwd(경로 존의 기준). 블록과 갈라 내주는 이유는 함수 docstring.
    pane_cwd = staticmethod(pane_cwd)


PLUGIN = _BlocksPlugin()

__all__ = [
    "MAX_BLOCKS",
    "PLUGIN",
    "blocks_dirty",
    "blocks_wire",
    "clear_blocks_dirty",
    "pane_cwd",
    "pane_osc",
]
