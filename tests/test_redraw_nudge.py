"""리드로우 넛지(winsize 한 줄 토글)가 Windows 에서 «간격을 두고» 되돌리는가 (pytmux-552).

`:redraw`·재접속·재시작 복원(`_induce_redraw_all`)과 Claude 자동 완화
(`_auto_redraw_pane`)는 패널 PTY 의 winsize 를 한 줄 줄였다 되돌려 앱이 화면 전체를
다시 그리게 한다. POSIX 에서는 즉시 되돌려도 커널이 SIGWINCH 를 보내 충분하다.

Windows(ConPTY)에서는 **즉시 되돌리면 무동작**이었다 — 앱(Claude Code)은 크기 변경
이벤트를 받은 뒤 콘솔 크기를 다시 읽는데 그때는 이미 원래 크기라 다시 안 그린다.
실측: conhost 버퍼에 쓰레기 5행을 써 넣고 즉시 토글 → 5행 그대로, 20~500ms 간격 토글 →
0행. 사용자 세션 40분 동안 자동 완화(`idle` 모드)가 한 번도 효과를 못 낸 까닭이다.

여기서 재는 것은 그 규칙이다 — 앱을 띄우지 않고 `set_winsize` 호출 순서와 시점만 본다:
- POSIX: 줄이기·되돌리기가 **같은 호출 안에서** 순서대로(대조군 — 종전 동작 불변).
- Windows: 줄이기만 즉시, 되돌리기는 `call_later(간격>0)` 로 **미뤄진다**.
- 되돌릴 때는 **그 시점의** 패널 치수를 쓴다(그 사이 레이아웃이 바뀌어도 맞는다).
- 플러그인 `_auto_redraw_pane` 이 사본이 아니라 그 한 자리를 쓴다(사본이 무동작의 원흉).
"""
import importlib

import harness  # noqa: F401 (경로 설정)
from harness import patched
from pytmuxlib import pty_backend, serverpersist
from pytmuxlib.serverpersist import ServerPersistMixin


class _Pty:
    def __init__(self, log):
        self.log = log

    def set_winsize(self, rows, cols):
        self.log.append((rows, cols))


class _Pane:
    def __init__(self, rows=30, cols=100):
        self.rows, self.cols = rows, cols
        self.log = []
        self.pty = _Pty(self.log)


class _Loop:
    def __init__(self):
        self.later = []

    def call_later(self, delay, fn, *args):
        self.later.append((delay, fn, args))


class _Srv(ServerPersistMixin):
    def __init__(self, loop, panes=()):
        self.loop = loop
        self._panes = list(panes)

    def _all_panes(self):
        return list(self._panes)


def test_posix_restores_immediately():
    """대조군: POSIX 는 종전대로 한 호출 안에서 줄였다 되돌린다."""
    p = _Pane()
    loop = _Loop()
    with patched(pty_backend, IS_WINDOWS=False):
        _Srv(loop)._nudge_winsize(p)
    assert p.log == [(29, 100), (30, 100)], p.log
    assert loop.later == [], "POSIX 에서 되돌리기를 미루면 안 된다(종전 동작)"


def test_windows_defers_restore():
    """Windows 는 바꾸기만 즉시 — 되돌리기는 간격(>0)을 두고 미룬다."""
    p = _Pane()
    loop = _Loop()
    with patched(pty_backend, IS_WINDOWS=True):
        _Srv(loop)._nudge_winsize(p)
    assert p.log == [(31, 100)], "즉시 되돌리면 앱이 크기 변화를 못 본다: %r" % p.log
    assert len(loop.later) == 1, loop.later
    delay, fn, args = loop.later[0]
    assert delay > 0, "간격 0 은 즉시 토글과 같다(무동작)"
    assert delay == serverpersist._WIN_NUDGE_GAP
    fn(*args)
    assert p.log == [(31, 100), (30, 100)], p.log


def test_windows_grows_instead_of_shrinking():
    """☠ Windows 는 **늘렸다** 되돌린다(pytmux-553).

    conhost 는 줄일 때 맨 윗줄을 버려 버퍼가 한 행 위로 밀리고, 앱이 다시 안 그리면 그대로
    남는다(=«한 줄씩 어긋남»). 늘리면 아래에 빈 줄이 붙었다 빠질 뿐이다. 되돌려 «줄임 먼저»
    로 돌아가면 이 시험이 운다. 실 conhost 로 잰 것은
    test_real_conhost_keeps_the_buffer_in_place."""
    p = _Pane(rows=30)
    with patched(pty_backend, IS_WINDOWS=True):
        _Srv(_Loop())._nudge_winsize(p)
    assert p.log[0][0] > 30, "Windows 넛지가 높이를 줄였다 — conhost 버퍼가 한 행 밀린다: %r" % p.log


def test_windows_nudge_holds_the_scrollback():
    """늘린 동안의 H+1 행 재도색이 맨 윗줄을 스크롤백에 중복으로 밀어 넣지 않게 —
    넛지는 화면 모델의 스크롤백 수집을 잠시 멈춘다(호출부까지 잰다)."""
    import time

    from pytmuxlib.nativescreen import NativeScrollbackScreen
    p = _Pane(rows=5, cols=20)
    p._main = NativeScrollbackScreen(20, 5)
    with patched(pty_backend, IS_WINDOWS=True):
        _Srv(_Loop())._nudge_winsize(p)
    assert p._main.history_hold_until > time.monotonic(), "넛지가 스크롤백 보류를 안 걸었다"
    assert p._main.history_skip == 1
    for i in range(50):                             # 그 사이 진짜 출력이 50줄 흐른다
        p._main.draw("x%d" % i)
        p._main.linefeed()
        p._main.carriage_return()
    # ⛔ 버리는 것은 넛지가 만든 **한 줄**뿐이다(pytmux-34 서버 검수 #3 — 시간 창으로
    #   막으면 그 사이 진짜 출력이 통째로 빠졌다).
    assert len(p._main.history.top) == 50 - 5 + 1 - 1, len(p._main.history.top)
    # 안 쓰인 예산은 시한이 지나면 거둔다 — 나중의 진짜 줄을 버리지 않는다.
    p._main.history_skip, p._main.history_hold_until = 1, time.monotonic() - 1
    before = len(p._main.history.top)
    p._main.linefeed()
    assert len(p._main.history.top) == before + 1


async def test_real_conhost_keeps_the_buffer_in_place():
    """실 conhost E2E — 리사이즈에 반응하지 않는 자식(=앱이 다시 안 그리는 최악의 경우)을
    띄워 넛지 뒤 conhost 가 다시 칠한 화면을 읽는다(2026-10-15 실측의 회귀 가드).

    줄임 먼저(종전)였으면 `L01` 이 사라지고 `PROMPT>` 가 한 행 위로 올라간다."""
    import asyncio
    import sys

    from run import skip
    from pytmuxlib.model import Pane
    from pytmuxlib.replay import render_pane_lines
    if not pty_backend.IS_WINDOWS:
        skip("Windows 전용(conhost 리사이즈 정책)")
    cols, rows = 60, 20
    child = ("import sys,time\n"
             "sys.stdout.write('\\x1b[2J\\x1b[H')\n"
             f"for i in range(1,{rows}): sys.stdout.write('L%02d\\r\\n' % i)\n"
             "sys.stdout.write('PROMPT>'); sys.stdout.flush(); time.sleep(30)\n")

    async def go():
        loop = asyncio.get_running_loop()
        buf = bytearray()
        pty = pty_backend.spawn([sys.executable, "-c", child], cols=cols, rows=rows)
        pane = Pane(-1, -1, cols, rows)
        pane.pty = pty
        pty.start_reader(loop, lambda b: (buf.extend(b), pane.feed(b)), lambda: None)
        try:
            # 자식이 다 그릴 때까지 — 화면 맨 아랫줄에 PROMPT> 가 설 때까지 폴링.
            await harness.wait_for(
                lambda: render_pane_lines(pane)[rows - 1].startswith("PROMPT>"),
                timeout=8.0)
            srv = _Srv(loop)
            srv._nudge_winsize(pane)
            await asyncio.sleep(serverpersist._WIN_NUDGE_GAP + 1.2)
            return [l.rstrip() for l in render_pane_lines(pane)], pane
        finally:
            try:
                pty.kill()
            except Exception:                       # noqa: BLE001
                pass
            pty.stop_reader()

    lines, pane = await go()
    assert lines[0] == "L01", "conhost 버퍼가 한 행 위로 밀렸다: %r" % lines[:3]
    assert lines[rows - 1].startswith("PROMPT>"), lines[-3:]
    assert len(pane._main.history.top) == 0, \
        "넛지가 스크롤백에 중복 줄을 남겼다: %d줄" % len(pane._main.history.top)


def test_windows_restore_uses_current_size():
    """미룬 사이 레이아웃이 바뀌면 되돌릴 때 새 치수를 쓴다(옛 치수로 되돌리지 않는다)."""
    p = _Pane(rows=30, cols=100)
    loop = _Loop()
    with patched(pty_backend, IS_WINDOWS=True):
        _Srv(loop)._nudge_winsize(p)
    p.rows, p.cols = 40, 120          # 그 사이 리사이즈
    _d, fn, args = loop.later[0]
    fn(*args)
    assert p.log[-1] == (40, 120), p.log


def test_windows_restore_skips_closed_pane():
    """미룬 사이 패널이 닫히면(pty=None) 되돌리기는 조용히 넘어간다."""
    p = _Pane()
    loop = _Loop()
    with patched(pty_backend, IS_WINDOWS=True):
        _Srv(loop)._nudge_winsize(p)
    p.pty = None
    _d, fn, args = loop.later[0]
    fn(*args)                          # 예외 없이
    assert p.log == [(31, 100)]


def test_induce_redraw_all_uses_nudge_on_windows():
    """`:redraw`·재접속 경로도 같은 규칙을 탄다 — 패널마다 되돌리기가 미뤄진다."""
    a, b = _Pane(), _Pane(rows=10, cols=40)
    loop = _Loop()
    with patched(pty_backend, IS_WINDOWS=True):
        _Srv(loop, [a, b])._induce_redraw_all()
    assert a.log == [(31, 100)] and b.log == [(11, 40)], (a.log, b.log)
    assert len(loop.later) == 2


def test_plugin_auto_redraw_delegates_to_core():
    """플러그인 자동 완화가 코어 한 자리를 쓴다 — 자기 사본(즉시 토글)으로 돌아가면 운다."""
    mixin = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    calls = []

    class _S(mixin.ServerClaudeMixin):
        def _nudge_winsize(self, p):
            calls.append(p)

    p = _Pane()
    _S._auto_redraw_pane(_S.__new__(_S), p)
    assert calls == [p], "자동 완화가 코어 _nudge_winsize 를 안 거쳤다"
    assert p.log == [], "플러그인이 직접 set_winsize 를 불렀다(즉시 토글 사본)"
