"""파이썬/Textual 클라가 **터미널에 실제로 쓰는 색 바이트** — pytmux-205 의 뒤쪽 절반.

# 무엇을 재나

`tests/test_claude_banner_colors.py` 가 **앞쪽 절반**을 이미 못박았다 — 정본 배너 바이트를
먹이면 서버가 세 줄 전부를 `#d77757` 한 색으로 담는다(= SGR 파싱·와이어는 결백하다).
남은 절반이 **클라 → 호스트 터미널** 이고, 그 구간은 여태 아무도 안 쟀다. 이 파일이 그
자리다: 진짜 클라를 PTY 아래 띄우고 패널에 무언가를 흘린 뒤 **클라가 뱉은 SGR** 을 본다.

# 이 파일이 찾아낸 것 (pytmux-205 의 원인 · 2026-08-22 실측)

제보: Claude Code 마스코트가 코랄(`#d77757`)이 아니라 **자홍 `#f4005f`**, 같은 줄의 회색
글자가 **올리브 `#625e4c`** 로 나온다. 그 두 값은 이 저장소에도 Claude Code 번들에도 없어서
「호스트 터미널의 팔레트겠거니」로 한 번 접혔던 값인데, **우리가 칠한 색이었다**:

    패널 앱의 `ESC[31m`  → 클라가 화면에 `38;2;244;0;95`  (= #f4005f 자홍)
    패널 앱의 `ESC[90m`  → 클라가 화면에 `38;2;98;94;76`  (= #625e4c 올리브)

자리는 Textual 의 `ANSIToTruecolor` 필터다. 화면에 나가는 것은 결국 truecolor 라 「빨강」을
어느 hex 로 적을지 누군가는 정해야 하는데, 그 표(`App.ansi_theme_dark`)의 기본값이 **Rich
MONOKAI** 이고 그 테마의 ANSI 색이 정확히 위 두 값이다(빨강 1·9 = `#f4005f` · 밝은검정 8 =
`#625e4c`). ⇒ 앱이 ANSI 색을 내면 pytmux 가 **남의 테마 색**으로 바꿔 그렸다.

고친 자리는 `clientutil.ANSI_PALETTE_THEME`(표준 xterm/VGA 16색) 한 곳이고, 그것을
`client.PytmuxApp.__init__` 이 `ansi_theme_dark`/`_light` 로 건다. 고친 뒤 실측:

    `ESC[31m` → `38;2;128;0;0`(#800000 빨강) · `ESC[90m` → `38;2;128;128;128`(#808080 회색)

⛔ **`ansi_color=True`(필터 끄기)로는 못 고친다** — 실측하면 ANSI 가 ANSI 로 나가는 게
아니라 색이 **통째로 빠진다**(`39;49`). 그래서 끄지 않고 **표를 바꾼다**.

# 색 깊이 — 같은 픽스처인데 환경이 값을 정한다 (실측)

| 환경 | 마스코트(`#d77757`) | 회색(`#999999`) |
| --- | --- | --- |
| `COLORTERM=truecolor` | `38;2;215;119;87` (정확) | `38;2;153;153;153` (정확) |
| `TERM=…-256color` (COLORTERM 없음) | `38;5;173` (= `#d7875f` · 근사) | `38;5;246` |
| `TERM=xterm` (16색) | `91` | `37` |
| 위 16색 + `TEXTUAL_COLOR_SYSTEM=truecolor` | `38;2;215;119;87` (정확) | `38;2;153;153;153` |

★ 이 표가 함께 못박는 것이 하나 더 있다: **어느 환경이든 세 줄이 같이 움직인다**(줄이 안
갈린다). 제보 화면은 줄마다 갈렸는데(1·6·7 정확 · 2·3·5 자홍), 그 갈림은 색 깊이로는
설명되지 않고 **런마다 truecolor 인가 ANSI 인가**로 설명된다 — 위 §원인이 그것이다.

⚠ **낮은 색 환경에서 무엇을 낼지는 이 파일이 정하지 않는다**(지금 값을 적어 둘 뿐이다).
기본을 올리는 선택(16 → 256, 또는 언제나 truecolor)은 truecolor 를 못 받는 터미널에서
색이 통째로 빠지는 쪽이라 사람이 정한다. 정해지면 이 표를 고친다.
"""
import os
import re
import shutil
import sys
import tempfile

import harness
import ptyshot
from run import skip                       # 명시 SKIP 회계

MASCOT_GLYPHS = "▐▛▜▝▘█▌"                  # 배너 마스코트를 이루는 블록·사분면 글자
# 회색(`#999999`) 글자를 **세 줄에서 한 조각씩** 집는다 — 한 줄만 집으면 「줄이 안 갈린다」를
# 못 잰다(그 줄 하나가 균일한 것으로는 아무것도 안 말한다). 1번 줄=버전 · 2번=부제 · 3번=경로.
_GREY_TEXT = ("v2.1.2", "Opus 5", "Claude Max", "src/example")
_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "claude", "banner_mascot.ansi")
_DRAWN = lambda txt: any(c in txt for c in "┌─│┐└┘") or "[+]" in txt
_SGR = re.compile(r"\x1b\[([0-9;]*)m")

# 앱이 «ANSI 색» 을 내는 한 줄. ⛔ 타이핑과 출력을 가른다(pytmux-141 의 교훈) — 셸이 빈
# 따옴표를 지워 **출력만** RRRRR/GGGGG 가 되므로, tty 로컬 에코만으로는 단언이 안 통과한다.
_ANSI_FEED = 'printf "\\033[31mRR""RRR\\033[90mGG""GGG\\033[0m\\n"\n'.encode()
_ANSI_RED, _ANSI_GREY = "RRRRR", "GGGGG"
# 종전에 화면에 나가던 값 = Rich MONOKAI 의 ANSI 색. 이것이 제보 화면의 그 두 색이다.
MONOKAI_RED, MONOKAI_GREY = "38;2;244;0;95", "38;2;98;94;76"


def _entry():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "pytmux.py")


def _sgr_runs(raw: str):
    """(SGR 파라미터, 그 뒤에 찍힌 글자) 쌍 — 클라가 실제로 쓴 순서 그대로."""
    out, last, pos = [], None, 0
    for m in _SGR.finditer(raw):
        if last is not None:
            out.append((last, raw[pos:m.start()]))
        last, pos = m.group(1), m.end()
    if last is not None:
        out.append((last, raw[pos:pos + 40]))
    return out


def _fg(params: str) -> str:
    """SGR 파라미터에서 **전경색 부분만** 뽑는다(`91;40` → `91`).

    마스코트 눈은 배경(`48;…`)을 함께 쓰므로 그것까지 넣고 비교하면 같은 색이 두 값으로
    갈려 보인다 — 이 시험이 묻는 것은 「전경을 무엇으로 칠했나」 하나다."""
    ps = params.split(";")
    if ps[:2] == ["38", "2"]:
        return ";".join(ps[:5])            # 38;2;r;g;b
    if ps[:2] == ["38", "5"]:
        return ";".join(ps[:3])            # 38;5;n
    for p in ps:                           # 30–37 · 90–97 = 팔레트 번호
        if p.isdigit() and (30 <= int(p) <= 37 or 90 <= int(p) <= 97):
            return p
    return params


def _capture(env: dict, feed: bytes, until) -> str:
    """진짜 클라를 PTY 아래 띄워 `feed` 를 패널에 흘리고 **클라가 쓴 바이트**를 돌려준다.

    ⛔ 화면 텍스트(`screen_text`)가 아니라 **원문**이다 — 이 파일이 묻는 것이 SGR 이라
    ANSI 를 지우면 잴 것이 없어진다."""
    d = tempfile.mkdtemp(prefix="colordepth-")
    sock = os.path.join(d, "c.sock")
    try:
        raw, alive = ptyshot.capture(
            [sys.executable, _entry(), "--socket", sock],
            cols=120, rows=40, seconds=60.0, env=env,
            feed=feed, ready=_DRAWN, until=until)
        txt = raw.decode("utf-8", "replace")
        assert alive, "클라가 캡처 시간 안에 스스로 종료(크래시)"
        assert not ptyshot.has_traceback(raw), ptyshot.screen_text(raw)[-1200:]
        assert until(ptyshot.screen_text(raw)), (
            "패널에 그 출력이 안 나왔다 — 셸이 명령을 못 받았다:\n"
            + ptyshot.screen_text(raw)[-800:])
        harness.assert_no_server_errors(sock)
        return txt
    finally:
        from pytmuxlib import launcher
        try:
            launcher.control_request(sock, {"t": "kill-server"})
        except Exception:
            pass
        shutil.rmtree(d, ignore_errors=True)


def _banner_paint(env: dict):
    """정본 배너 픽스처를 흘리고 **마스코트·회색 글자의 전경 SGR** 을 모은다.

    반환 = (마스코트 전경 집합, 회색 글자 전경 집합). 집합인 것에 뜻이 있다 — 세 줄이
    갈리면 원소가 둘 이상이 되므로, 「줄이 안 갈린다」를 크기 1 로 잰다."""
    txt = _capture(env, ("cat %s\n" % _FIXTURE).encode(), lambda t: "▘" in t)
    mascot, grey = set(), set()
    for params, text in _sgr_runs(txt):
        if any(c in text for c in MASCOT_GLYPHS):
            mascot.add(_fg(params))
        if any(k in text for k in _GREY_TEXT):
            grey.add(_fg(params))
    return mascot, grey


def _ansi_paint(env: dict):
    """앱이 낸 **ANSI 색**(31·90)을 클라가 무엇으로 칠하는지 모은다."""
    txt = _capture(env, _ANSI_FEED,
                   lambda t: _ANSI_RED in t and _ANSI_GREY in t)
    red, grey = set(), set()
    for params, text in _sgr_runs(txt):
        if _ANSI_RED in text:
            red.add(_fg(params))
        if _ANSI_GREY in text:
            grey.add(_fg(params))
    # 타이핑한 명령줄(에코)에는 색이 없다 — 그 런까지 세면 언제나 `0` 이 섞인다.
    return red - {"0"}, grey - {"0"}


async def test_app_ansi_colours_use_the_standard_palette():
    """★ **pytmux-205 의 본론** — 앱의 ANSI 색은 «표준 팔레트»로 나간다.

    앱이 `ESC[31m`(빨강)·`ESC[90m`(밝은 검정)을 내면 화면에는 표준 16색의 그 값이
    나가야 한다. ⛔ 종전에는 Textual 기본 ANSI 테마(Rich MONOKAI)를 지나 **자홍
    `#f4005f`·올리브 `#625e4c`** 가 나갔고, 그것이 제보 화면의 그 두 색이었다."""
    if ptyshot.IS_WINDOWS:
        skip("POSIX 전용 하네스(stdlib pty)")
    red, grey = _ansi_paint({"COLORTERM": "truecolor"})
    assert red == {"38;2;128;0;0"}, red            # #800000
    assert grey == {"38;2;128;128;128"}, grey      # #808080
    # ⛔ 되돌아오면 이 줄이 먼저 운다 — 제보의 그 색이 화면에 다시 나간 것이다.
    assert MONOKAI_RED not in red and MONOKAI_GREY not in grey, (red, grey)


async def test_truecolor_terminal_gets_the_exact_bytes():
    """터미널이 24비트를 말하면 클라는 **정본 값 그대로** 낸다(근사 금지).

    ⚠ 위 §원인을 고치면서 truecolor 런까지 팔레트로 끌려가면 안 된다 — ANSI 색만
    바뀌고 truecolor 는 손대지 않는다는 것을 여기서 함께 잰다."""
    if ptyshot.IS_WINDOWS:
        skip("POSIX 전용 하네스(stdlib pty)")
    mascot, grey = _banner_paint({"COLORTERM": "truecolor"})
    assert mascot == {"38;2;215;119;87"}, mascot
    assert grey == {"38;2;153;153;153"}, grey


async def test_sixteen_colour_terminal_hands_the_colour_to_the_palette():
    """16색 환경에서는 **팔레트 번호**가 나간다 — 그때는 터미널이 색을 정한다.

    `91`(밝은 빨강)·`37`(흰색)은 값이 아니라 **번호**다. ⛔ 이 시험은 「그 색이 맞다」가
    아니라 **「그 자리에서는 색을 우리가 안 정한다」**를 고정한다."""
    if ptyshot.IS_WINDOWS:
        skip("POSIX 전용 하네스(stdlib pty)")
    mascot, grey = _banner_paint({"COLORTERM": "", "TERM": "xterm"})
    assert mascot == {"91"}, mascot
    assert grey == {"37"}, grey


async def test_the_colour_depth_never_splits_the_rows():
    """★ 어느 환경이든 **세 줄이 함께 움직인다** — 색 깊이는 줄을 안 가른다.

    제보 화면은 줄마다 갈렸다(1·6·7 정확 · 2·3·5 자홍). 그 갈림이 색 깊이에서 나올 수
    있는지가 갈림길이었고 여기서 **아니다**로 닫는다 — 갈림의 자리는 색 깊이가 아니라
    «그 런이 truecolor 였나 ANSI 였나» 다(위 §원인)."""
    if ptyshot.IS_WINDOWS:
        skip("POSIX 전용 하네스(stdlib pty)")
    mascot, grey = _banner_paint({"COLORTERM": ""})   # TERM=…-256color (ptyshot 기본)
    assert mascot == {"38;5;173"}, mascot             # #d7875f — 근사이지만 **한 값**
    assert grey == {"38;5;246"}, grey


async def test_forcing_the_colour_system_beats_the_environment():
    """`TEXTUAL_COLOR_SYSTEM=truecolor` 는 16색 환경도 **이긴다** — 사람이 쓸 레버.

    Textual 이 `constants.COLOR_SYSTEM`(= 이 환경변수, 기본 `auto`)으로 콘솔을 만들므로
    환경이 낮게 잡혀도 사람이 올릴 수 있다. 낮은 색으로 떨어진 화면을 만났을 때 **원인이
    색 깊이인지 아닌지**를 한 줄로 가르는 자리라 시험으로 고정한다."""
    if ptyshot.IS_WINDOWS:
        skip("POSIX 전용 하네스(stdlib pty)")
    mascot, grey = _banner_paint({"COLORTERM": "", "TERM": "xterm",
                                  "TEXTUAL_COLOR_SYSTEM": "truecolor"})
    assert mascot == {"38;2;215;119;87"}, mascot
    assert grey == {"38;2;153;153;153"}, grey
