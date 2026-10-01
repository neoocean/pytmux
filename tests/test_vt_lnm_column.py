"""맨 LF 는 열을 보존한다 — LNM(DEC 모드 20) 회귀 가드(pytmux-511).

`Pane` 은 화면을 만들 때마다 LNM(LF→CR+LF)을 **켜 두고** 있었다. 맨 `\\n` 의 뜻은
IND(한 줄 아래 · 열은 그대로)이므로 그 줄은 통째로 왼쪽으로 밀려 그려진다.

Unix 에서는 pty 회선규율의 ONLCR 이 커널에서 `\\n` → `\\r\\n` 을 만들어 주기 때문에
맨 `\\n` 이 애초에 안 와서 이 결함이 가려져 있었다. Windows ConPTY 는 회선규율이
없고 그 델타 렌더러가 「한 줄 아래 같은 열」을 맨 `\\n` **한 바이트**로 쓴다 — 거기서
Claude Code 패널의 표·들여쓰기가 두 칸 왼쪽에 그려지고, ConPTY 가 안 바뀐 칸을
CUF(`ESC[nC`)로 건너뛰는 탓에 그 오배치가 지워지지 않았다(pytmux-483·pytmux-510).

⛔ 이 파일의 관문은 **바이트 하나**다 — `\\n` 과 `\\r\\n` 이 서로 다른 결과를 내야 한다.
대조군(`\\r\\n` · `ESC E` · 앱이 청한 `CSI 20 h`)을 함께 두어, 열 보존을 「아무 개행이나
열을 안 건드린다」로 잘못 고치는 수정도 걸리게 한다.
"""
import harness  # noqa: F401 (경로 설정)
from run import skip
from pytmuxlib import vtconst
from pytmuxlib.model import Pane
from pytmuxlib.nativescreen import NativeScreen
from pytmuxlib.vtparse import VTTokenizer

try:
    import pyte
    _HAVE_PYTE = True
except Exception:          # 선택적 차분 오라클 — 없으면 그 테스트만 skip
    pyte = None
    _HAVE_PYTE = False

COLS, ROWS = 40, 10


def _pane(*chunks):
    p = Pane(-1, -1, COLS, ROWS)
    for c in chunks:
        p.feed(c)
    return p


def _row(pane, y):
    """패널 화면의 y 행(0-기준) 텍스트 — 오른쪽 공백은 뗀다."""
    line = pane.screen.buffer[y]
    return "".join(line[x].data for x in range(pane.screen.columns)).rstrip()


# ── ① 핵심: 맨 LF 는 열을 보존한다 ───────────────────────────────────────────

async def test_bare_lf_keeps_column():
    """CUP(2,3) → 맨 `\\n` → 글자. 그 글자는 **3열**에 앉아야 한다(IND)."""
    p = _pane(b"\x1b[2;3H\x1b[K\nX")
    assert _row(p, 2) == "  X", f"맨 LF 가 열을 버렸다: {_row(p, 2)!r}"
    assert p.screen.cursor.x == 3, f"커서 열 {p.screen.cursor.x} (3 이어야)"


async def test_crlf_resets_column():
    """대조군 — `\\r\\n` 은 열을 1 로 되돌린다(CR 이 그 일을 한다)."""
    p = _pane(b"\x1b[2;3H\x1b[K\r\nX")
    assert _row(p, 2) == "X", f"CRLF 가 열을 안 되돌렸다: {_row(p, 2)!r}"
    assert p.screen.cursor.x == 1


async def test_lnm_is_off_on_every_screen():
    """세 화면(생성·respawn·alt) 전부 LNM 이 꺼져 있어야 한다.

    ⛔ `set_mode(vtconst.LNM)` 호출을 **다시 심는** 회귀를 겨눈다 — 값을 만드는
    쪽만 보면 호출 복원이 안 잡히므로 세 경로를 각각 밟는다."""
    p = _pane()
    assert vtconst.LNM not in p._main.mode, "생성 직후 LNM 이 켜져 있다"
    p.reinit(-1, -1, COLS, ROWS)
    assert vtconst.LNM not in p._main.mode, "respawn 뒤 LNM 이 켜져 있다"
    p.feed(b"\x1b[?1049h")
    assert p.alt_active, "alt 전환이 안 됐다(전제 실패)"
    assert vtconst.LNM not in p.screen.mode, "alt 화면에 LNM 이 켜져 있다"
    # alt 화면에서도 맨 LF 는 열을 보존한다.
    p.feed(b"\x1b[2;3H\nX")
    assert _row(p, 2) == "  X", f"alt: 맨 LF 가 열을 버렸다: {_row(p, 2)!r}"


# ── ② NEL 은 LNM 과 무관하게 CR+LF 다 ────────────────────────────────────────

async def test_nel_resets_column():
    """`ESC E`(NEL)는 열을 1 로 되돌린다.

    종전엔 `vtconst.ESCAPE["E"]` 가 `linefeed` 를 가리켜 NEL 의 CR 이 **LNM 이
    켜져 있다는 우연**에 얹혀 있었다. LNM 을 끄면서 같이 안 고치면 NEL 이 조용히
    IND 로 격하되므로 여기서 못박는다."""
    p = _pane(b"\x1b[2;3H\x1b[K\x1bEX")
    assert _row(p, 2) == "X", f"NEL 이 CR 을 잃었다: {_row(p, 2)!r}"
    assert p.screen.cursor.x == 1
    assert vtconst.ESCAPE["E"] == "newline", "ESC E 가 다시 linefeed 로 돌아갔다"


async def test_index_keeps_column():
    """대조군 — `ESC D`(IND)는 맨 LF 와 같아야 한다(열 보존)."""
    p = _pane(b"\x1b[2;3H\x1b[K\x1bDX")
    assert _row(p, 2) == "  X", f"IND 가 열을 버렸다: {_row(p, 2)!r}"


# ── ③ 앱이 청하면 LNM 을 따른다 ──────────────────────────────────────────────

async def test_app_can_turn_lnm_on_and_off():
    """`CSI 20 h`/`CSI 20 l` — 기본값을 끈 것이 「모드를 없앴다」가 되지 않게."""
    p = _pane(b"\x1b[20h\x1b[2;3H\x1b[K\nX")
    assert _row(p, 2) == "X", f"앱이 켠 LNM 이 안 먹었다: {_row(p, 2)!r}"
    p.feed(b"\x1b[20l\x1b[5;3H\x1b[K\nY")
    assert _row(p, 5) == "  Y", f"LNM 해제가 안 먹었다: {_row(p, 5)!r}"


# ── ④ ConPTY 델타 프레임 모양 — 제보 캡처에서 줄인 것 ────────────────────────

# 제보 캡처(`captures/<머신>/20260921_115046_0_2.system_p13.log`)에서 그대로 뽑은
# 모양: CUP → EL → **맨 LF** → 2칸 들여쓴 본문. Claude Code 가 ConPTY 를 지나며
# 이렇게 나오고, LNM 이 켜져 있으면 표 윗변이 1열에 서서 두 칸 왼쪽으로 밀렸다.
_CONPTY_FRAME = (
    b"\x1b[2;3H\x1b[K"
    b"\n\xe2\x94\x8c\xe2\x94\x80\xe2\x94\x80\xe2\x94\x80\xe2\x94\x90"   # ┌───┐
    b"\x1b[4;3H\x1b[K"
    b"\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80\xe2\x94\x80\xe2\x94\x98"   # └───┘
)


async def test_conpty_delta_frame_keeps_indent():
    """캡처 모양 그대로 — 표의 두 변이 **같은 열**(3열)에 서야 한다."""
    p = _pane(_CONPTY_FRAME)
    top, bottom = _row(p, 2), _row(p, 4)
    assert top == "  ┌───┐", f"윗변이 밀렸다: {top!r}"
    assert bottom == "  └───┘", f"밑변이 밀렸다: {bottom!r}"


async def test_conpty_delta_frame_matches_oracle():
    """차분 — 독립 VT 구현(pyte · LNM 기본 off)과 화면이 같아야 한다."""
    if not _HAVE_PYTE:
        skip("pyte 미설치 — 차분 오라클 없음(절대 가드는 위 테스트가 진다)")
    p = _pane(_CONPTY_FRAME)
    ref = pyte.Screen(COLS, ROWS)
    pyte.ByteStream(ref).feed(_CONPTY_FRAME)
    mine = [_row(p, y) for y in range(ROWS)]
    theirs = [l.rstrip() for l in ref.display]
    assert mine == theirs, f"오라클과 다르다\n  pytmux {mine}\n  pyte   {theirs}"


# ── ⑤ 파서→화면 배선(Pane 우회 없이도 같은 결과) ─────────────────────────────

async def test_tokenizer_on_bare_screen_keeps_column():
    """Pane 의 우회층을 빼고 VTTokenizer→NativeScreen 만으로도 같아야 한다
    (기본값이 `Pane` 에만 있는 우연이 아님을 본다)."""
    s = NativeScreen(COLS, ROWS)
    VTTokenizer(s).feed(b"\x1b[2;3H\x1b[K\nX")
    assert vtconst.LNM not in s.mode, "NativeScreen 기본값에 LNM 이 있다"
    row = "".join(s.buffer[2][x].data for x in range(s.columns)).rstrip()
    assert row == "  X", f"맨 LF 가 열을 버렸다: {row!r}"
