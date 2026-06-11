"""pty_backend 추상층 회귀 테스트(헤드리스).

Unix 백엔드의 spawn → start_reader(스레드 무관 콜백) → write → set_winsize →
terminate/kill → reap → close 경로를 실제 PTY 로 검증한다. Windows(ConPTY) 경로는
pywinpty 가 필요해 이 머신에선 import 가용 여부만 확인하고 본 동작은 건너뛴다
(Windows 박스에서 별도 검증).
"""
import asyncio
import os

import harness  # noqa: F401 (sys.path 설정)
from pytmuxlib import conpty, pty_backend


async def test_spawn_read_eof():
    """짧게 출력하고 끝나는 명령 → 바이트 수신 + EOF 콜백 + reap."""
    if pty_backend.IS_WINDOWS:
        return  # ConPTY 경로는 Windows 에서 별도 검증
    loop = asyncio.get_running_loop()
    chunks: list[bytes] = []
    eof = asyncio.Event()

    pty = pty_backend.spawn(["/bin/echo", "PYTMUX_BACKEND_OK"],
                            cols=40, rows=10)
    pty.start_reader(loop, chunks.append, eof.set)
    await asyncio.wait_for(eof.wait(), timeout=5)

    out = b"".join(chunks)
    assert b"PYTMUX_BACKEND_OK" in out, out
    # EOF 후 reaper 가 종료 상태를 회수한다(블로킹 안전 — 이미 죽음).
    status = pty.reap(block=True)
    assert status is not None
    pty.close()


async def test_write_winsize_terminate():
    """셸에 입력을 써서 출력 확인 → set_winsize 무해 → terminate(SIGHUP) 로 종료."""
    if pty_backend.IS_WINDOWS:
        return
    loop = asyncio.get_running_loop()
    buf = bytearray()
    eof = asyncio.Event()

    env = dict(os.environ)
    env["PS1"] = ""  # 프롬프트 잡음 줄이기(있어도 마커 검출엔 무관)
    pty = pty_backend.spawn(["/bin/sh"], cols=40, rows=10, env=env)
    pty.start_reader(loop, buf.extend, eof.set)

    pty.write(b"echo PYTMUX_RW_OK\n")
    for _ in range(60):  # 최대 ~3s 동안 마커 등장 대기
        if b"PYTMUX_RW_OK" in bytes(buf):
            break
        await asyncio.sleep(0.05)
    assert b"PYTMUX_RW_OK" in bytes(buf), bytes(buf)

    pty.set_winsize(20, 60)  # 예외 없이 통과해야 함

    pty.terminate()  # SIGHUP → 셸 종료 → EOF
    try:
        await asyncio.wait_for(eof.wait(), timeout=5)
    except asyncio.TimeoutError:
        pty.kill()  # 보수적으로 강제 종료
    pty.reap(block=False)
    pty.close()


async def test_close_idempotent():
    """close()/reap() 를 두 번 불러도 예외가 없어야 한다."""
    if pty_backend.IS_WINDOWS:
        return
    loop = asyncio.get_running_loop()
    eof = asyncio.Event()
    pty = pty_backend.spawn(["/bin/echo", "x"], cols=20, rows=5)
    pty.start_reader(loop, lambda b: None, eof.set)
    await asyncio.wait_for(eof.wait(), timeout=5)
    pty.reap(block=True)
    pty.close()
    pty.close()  # 두 번째 호출 무해
    assert pty.reap(block=False) is None


# ─────────────────────────────────────────────────────────────────────────────
# 직접 소유 ConPTY 백엔드(§1.1② raw 바이트) — 크로스플랫폼 안전 단위 + Windows 수명
# ─────────────────────────────────────────────────────────────────────────────
async def test_conpty_supported_matches_platform():
    """conpty_supported() 는 Windows 에서 True, POSIX 에서 False(import 는 양쪽 안전)."""
    assert conpty.conpty_supported() is bool(pty_backend.IS_WINDOWS)


async def test_build_env_block():
    """env 블록: None→None, dict→wchar "K=V\\0...\\0\\0"(이중 널 종료).

    create_unicode_buffer 는 wchar_t 폭을 따른다 — Windows 2바이트(UTF-16,
    CreateProcessW 가 기대하는 그 블록), POSIX 4바이트(UTF-32). 바이트 검증은
    실행 플랫폼의 wchar 폭으로 디코드한다(고정 utf-16-le 가정은 macOS/리눅스
    러너에서 깨짐 — 2026-06-11 크로스플랫폼 정정)."""
    import ctypes
    assert conpty._build_env_block(None) is None
    buf = conpty._build_env_block({"A": "1", "BB": "22"})
    enc = "utf-16-le" if ctypes.sizeof(ctypes.c_wchar) == 2 else "utf-32-le"
    raw = bytes(buf).decode(enc)
    assert "A=1" in raw and "BB=22" in raw
    assert raw.endswith("\x00\x00")        # 이중 널 종료
    # 키-값 사이는 단일 널로 구분
    assert "A=1\x00BB=22" in raw


async def test_conpty_posix_raises():
    """POSIX 에서 _ConPty 생성은 NotImplementedError(가드)."""
    if pty_backend.IS_WINDOWS:
        return
    try:
        conpty._ConPty(80, 24)
    except NotImplementedError:
        return
    assert False, "POSIX 에서 _ConPty 는 NotImplementedError 여야 함"


async def test_spawn_selects_backend(monkeypatch=None):
    """PYTMUX_PTY_BACKEND 선택 분기 — 실제 spawn 없이 스텁으로 검증(Windows 전용)."""
    if not pty_backend.IS_WINDOWS:
        return
    calls = {"owned": 0, "winpty": 0}

    class _StubOwned:
        def __init__(self, *a, **k):
            calls["owned"] += 1

    class _StubWin:
        def __init__(self, *a, **k):
            calls["winpty"] += 1

    orig_owned = pty_backend._OwnedConPty
    orig_win = pty_backend._WinPty
    orig_env = os.environ.get("PYTMUX_PTY_BACKEND")
    pty_backend._OwnedConPty = _StubOwned
    pty_backend._WinPty = _StubWin
    try:
        os.environ["PYTMUX_PTY_BACKEND"] = "owned"
        pty_backend.spawn(["cmd.exe"], cols=40, rows=10)
        os.environ["PYTMUX_PTY_BACKEND"] = ""        # 기본 = pywinpty
        pty_backend.spawn(["cmd.exe"], cols=40, rows=10)
        os.environ["PYTMUX_PTY_BACKEND"] = "pywinpty"
        pty_backend.spawn(["cmd.exe"], cols=40, rows=10)
    finally:
        pty_backend._OwnedConPty = orig_owned
        pty_backend._WinPty = orig_win
        if orig_env is None:
            os.environ.pop("PYTMUX_PTY_BACKEND", None)
        else:
            os.environ["PYTMUX_PTY_BACKEND"] = orig_env
    assert calls["owned"] == 1, calls
    assert calls["winpty"] == 2, calls


async def test_owned_conpty_lifecycle_windows():
    """Windows 수명 경로: spawn→pid>0→resize→terminate→close→reap(블로킹).

    바이트 I/O 는 실 콘솔이 필요해 헤드리스로 검증 불가(start_reader 안 부름) — 여기선
    의사콘솔 생성·자식 attach·리사이즈·종료·핸들 정리의 무예외 수명만 본다. 멀티바이트
    왕복은 docs/WINDOWS_TESTING.md 의 라이브 검증(validate_backend.py)·실 제품으로 확인."""
    if not pty_backend.IS_WINDOWS:
        return
    pty = pty_backend._OwnedConPty(["cmd.exe"], cols=80, rows=24,
                                   cwd=None, env=dict(os.environ))
    try:
        assert pty.pid and pty.pid > 0
        assert pty.reap(block=False) is None      # 살아 있음
        pty.set_winsize(30, 100)                   # ResizePseudoConsole 무예외
    finally:
        pty.terminate()
        pty.close()
        pty.close()                                # 두 번째 호출 무해
    status = pty.reap(block=True)
    assert status is not None                      # 종료코드 회수


async def test_owned_conpty_reads_in_feed_slice_chunks():
    """#1.5: 직접 소유 ConPTY 리더가 FEED_SLICE 단위로 읽고, pause 시 더 안 읽는다.

    실 ConPTY 없이 가짜 _ConPty 를 __new__ 인스턴스에 주입해 리더 스레드 동작만 본다
    (백프레셔 게이트는 read *전에* 확인되므로 pause 후엔 기껏해야 in-flight 1건만 더 읽음)."""
    import threading
    import time
    from pytmuxlib.protocol import FEED_SLICE

    p = pty_backend._OwnedConPty.__new__(pty_backend._OwnedConPty)
    p._stop = threading.Event()
    p._resume_evt = threading.Event(); p._resume_evt.set()
    p._eof_fired = False
    p._on_eof = None
    sizes: list[int] = []

    class _FakeCp:
        def read(self, maxlen):
            sizes.append(maxlen)
            if p._stop.is_set():
                return b""
            time.sleep(0.004)
            return b"x" * 4

    p._cp = _FakeCp()
    loop = asyncio.get_running_loop()
    p._loop = loop
    got = bytearray()
    p._on_data = got.extend

    reader = threading.Thread(target=p._read_loop, daemon=True)
    reader.start()
    try:
        await asyncio.sleep(0.08)
        assert sizes, "읽기가 일어나야"
        assert all(m == FEED_SLICE for m in sizes), sizes[:5]   # 64KB 아님
        n_before = len(sizes)
        p.pause_reader()
        await asyncio.sleep(0.06)
        n_paused = len(sizes)
        assert n_paused - n_before <= 1, (n_before, n_paused)   # in-flight 최대 1건
        p.resume_reader()
        await asyncio.sleep(0.04)
        assert len(sizes) > n_paused, "resume 후 다시 읽어야"
        assert bytes(got), "데이터가 전달됨"
    finally:
        p.stop_reader()
        reader.join(timeout=0.5)
