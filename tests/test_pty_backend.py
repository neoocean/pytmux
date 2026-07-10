"""pty_backend 추상층 회귀 테스트(헤드리스).

Unix 백엔드의 spawn → start_reader(스레드 무관 콜백) → write → set_winsize →
terminate/kill → reap → close 경로를 실제 PTY 로 검증한다. Windows(ConPTY) 경로는
pywinpty 가 필요해 이 머신에선 import 가용 여부만 확인하고 본 동작은 건너뛴다
(Windows 박스에서 별도 검증).
"""
import asyncio
import os

import harness  # noqa: F401 (sys.path 설정)
from run import skip  # 명시 SKIP 회계
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


async def test_unix_write_no_silent_truncation():
    """H1: _UnixPty.write 가 부분 write/EAGAIN 에도 입력 **전체**를 쓴다 — 큰 페이스트가
    PTY 입력버퍼 포화로 조용히 잘리던 것 차단. 슬레이브를 별도 스레드로 드레인하며
    256KiB 를 써서 받은 총량 == 보낸 총량을 확인(논블로킹 master 라 EAGAIN 다발)."""
    if pty_backend.IS_WINDOWS:
        return
    import pty as ptymod
    import select
    import threading
    import tty
    master, slave = ptymod.openpty()
    tty.setraw(slave)                       # 라인 디시플린 변환 제거(바이트 그대로)
    os.set_blocking(master, False)
    obj = pty_backend._UnixPty.__new__(pty_backend._UnixPty)
    obj._fd = master
    payload = b"x" * (256 * 1024)
    got = bytearray()

    def _drain():
        while len(got) < len(payload):
            r, _, _ = select.select([slave], [], [], 2.0)
            if not r:
                break
            try:
                b = os.read(slave, 65536)
            except OSError:
                break
            if not b:
                break
            got.extend(b)

    t = threading.Thread(target=_drain)
    t.start()
    try:
        obj.write(payload)
    finally:
        t.join(5)
        os.close(master)
        os.close(slave)
    assert len(got) == len(payload), (len(got), len(payload))


async def test_owned_close_joins_threads():
    """M3: _OwnedConPty.close() 가 reader/watcher daemon 스레드를 join 해 teardown 을
    결정적으로 만든다(in-flight ReadFile 의 use-after-close 경합·stale EOF 방지).
    conpty 없이(_cp=None) join 로직만 검증 — 전 OS 에서 동작."""
    import threading
    obj = pty_backend._OwnedConPty.__new__(pty_backend._OwnedConPty)
    obj._cp = None
    obj._stop = threading.Event()
    obj._resume_evt = threading.Event()
    obj._exit = None
    obj._watcher = None
    done = threading.Event()

    def _worker():
        obj._stop.wait(2)          # close 의 _stop.set 으로 깨어 종료
        done.set()

    obj._reader = threading.Thread(target=_worker, daemon=True)
    obj._reader.start()
    obj.close()
    assert done.is_set(), "reader 스레드가 close 의 _stop.set 후 join 됨"
    assert obj._reader is None and obj._watcher is None, "스레드 참조 해제"


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


async def test_force_utf8_codepage_env_optout():
    """PYTMUX_KEEP_CODEPAGE 가 설정되면 콘솔 코드페이지 UTF-8 강제를 건너뛴다(레거시
    cp949 출력 앱 전용 탈출구) — helper spawn 없이 False. env 게이트가 첫 검사라
    POSIX 에서도 안전하게 호출된다(그 아래는 Windows API)."""
    orig = os.environ.get("PYTMUX_KEEP_CODEPAGE")
    os.environ["PYTMUX_KEEP_CODEPAGE"] = "1"
    try:
        assert conpty.force_utf8_codepage(None) is False
    finally:
        if orig is None:
            os.environ.pop("PYTMUX_KEEP_CODEPAGE", None)
        else:
            os.environ["PYTMUX_KEEP_CODEPAGE"] = orig


async def test_owned_conpty_spawn_forces_utf8_codepage():
    """Windows: _ConPty.spawn 이 셸 attach **전** force_utf8_codepage 를 같은 의사콘솔로
    1회 부른다 — 비-UTF-8 OEM 코드페이지(cp949 등) 시스템에서 UTF-8 byte-write 앱
    (Claude Code 등)의 한글 mojibake+ESC 소실을 막는 배선(실박스 보고 2026-07-09).
    순서가 '전'인 이유: DBCS(cp949)→65001 chcp 는 conhost 화면 클리어를 동반해, 셸
    프롬프트가 그려진 뒤 실행되면 새 탭이 Enter 전까지 빈 화면이 된다(실박스 보고
    2026-07-10 회귀). 호출 시점의 pid==-1(아직 CreateProcessW 전)로 순서를 못박는다.
    실제 CP 변경 효과는 라이브 검증(WINDOWS_TESTING.md) — 여기선 호출 배선만."""
    if not pty_backend.IS_WINDOWS:
        skip("Windows 전용(ConPTY)")
    calls = []
    orig = conpty.force_utf8_codepage
    holder = {}
    conpty.force_utf8_codepage = lambda hpc, timeout_ms=1500: (
        calls.append(holder["cp"].pid if "cp" in holder else None), True)[1]

    real_init = conpty._ConPty.__init__

    def init_wrap(self, *a, **k):
        real_init(self, *a, **k)
        holder["cp"] = self          # spawn 전에 인스턴스를 잡아 pid 관찰

    conpty._ConPty.__init__ = init_wrap
    try:
        pty = pty_backend._OwnedConPty(["cmd.exe"], cols=80, rows=24,
                                       cwd=None, env=dict(os.environ))
        try:
            assert len(calls) == 1, "spawn 당 1회 코드페이지 강제"
            assert calls[0] == -1, "셸 CreateProcessW 이전(pid 미할당) 호출이어야 함"
        finally:
            pty.terminate()
            pty.close()
    finally:
        conpty.force_utf8_codepage = orig
        conpty._ConPty.__init__ = real_init


async def test_conpty_ensure_utf8_codepage_once():
    """Windows: ensure_utf8_codepage 는 의사콘솔당 1회 게이트 — 풀 채움 시점에 이미
    강제된 콘솔은 spawn 이 중복 호출하지 않는다(지각 chcp 재실행 차단 겸 비용 절약)."""
    if not pty_backend.IS_WINDOWS:
        skip("Windows 전용(ConPTY)")
    calls = []
    orig = conpty.force_utf8_codepage
    conpty.force_utf8_codepage = lambda hpc, timeout_ms=1500: (
        calls.append(hpc), True)[1]
    try:
        cp = conpty._ConPty(20, 6)
        try:
            cp.ensure_utf8_codepage()
            cp.ensure_utf8_codepage()      # 풀 선강제 후 spawn 경로 재호출 시뮬레이션
            assert len(calls) == 1, "의사콘솔당 1회만 강제"
        finally:
            cp.close()
    finally:
        conpty.force_utf8_codepage = orig


async def test_spawn_selects_backend(monkeypatch=None):
    """PYTMUX_PTY_BACKEND 선택 분기 — 실제 spawn 없이 스텁으로 검증(Windows 전용)."""
    if not pty_backend.IS_WINDOWS:
        skip("Windows 전용(ConPTY)")
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
        os.environ["PYTMUX_PTY_BACKEND"] = "owned"   # 명시 owned
        pty_backend.spawn(["cmd.exe"], cols=40, rows=10)
        os.environ.pop("PYTMUX_PTY_BACKEND", None)    # 미설정 = 기본 owned (flip)
        pty_backend.spawn(["cmd.exe"], cols=40, rows=10)
        os.environ["PYTMUX_PTY_BACKEND"] = ""         # 빈값 = 기본 owned
        pty_backend.spawn(["cmd.exe"], cols=40, rows=10)
        os.environ["PYTMUX_PTY_BACKEND"] = "pywinpty" # 롤백 → pywinpty 강제
        pty_backend.spawn(["cmd.exe"], cols=40, rows=10)
        os.environ["PYTMUX_PTY_BACKEND"] = "winpty"   # 롤백 별칭 → pywinpty 강제
        pty_backend.spawn(["cmd.exe"], cols=40, rows=10)
    finally:
        pty_backend._OwnedConPty = orig_owned
        pty_backend._WinPty = orig_win
        if orig_env is None:
            os.environ.pop("PYTMUX_PTY_BACKEND", None)
        else:
            os.environ["PYTMUX_PTY_BACKEND"] = orig_env
    # 기본(미설정/빈값)·명시 owned 3건 → owned; pywinpty/winpty 롤백 2건 → winpty
    assert calls["owned"] == 3, calls
    assert calls["winpty"] == 2, calls


async def test_owned_conpty_lifecycle_windows():
    """Windows 수명 경로: spawn→pid>0→resize→terminate→close→reap(블로킹).

    바이트 I/O 는 실 콘솔이 필요해 헤드리스로 검증 불가(start_reader 안 부름) — 여기선
    의사콘솔 생성·자식 attach·리사이즈·종료·핸들 정리의 무예외 수명만 본다. 멀티바이트
    왕복은 docs/internal/WINDOWS_TESTING.md 의 라이브 검증(validate_backend.py)·실 제품으로 확인."""
    if not pty_backend.IS_WINDOWS:
        skip("Windows 전용(ConPTY)")
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


async def test_owned_conpty_watcher_fires_eof_on_child_exit():
    """자식이 스스로 종료(셸 exit)하면 감시 스레드가 콘솔을 hangup 해 블로킹 read 를
    EOF 로 깨워 _fire_eof 가 불린다.

    버그 재현: 동기 ReadFile 은 conhost 가 살아있으면 자식이 죽어도 b"" 를 주지 않아
    reader 가 영원히 블록 → EOF 미발생 → 패널 좀비(원격 페더레이션에선 원격 탭 유지).
    가짜 _ConPty 로 그 상황을 모사한다: read 는 close 전까진 데이터 없이 블록하고,
    wait 는 _alive 동안 None(WaitForSingleObject 타임아웃), 종료 후 종료코드를 준다.
    감시자가 wait 로 종료를 보고 close() → read 가 EOF → reader 가 _fire_eof."""
    import threading
    import time

    p = pty_backend._OwnedConPty.__new__(pty_backend._OwnedConPty)
    p.pid = 4321
    p._stop = threading.Event()
    p._resume_evt = threading.Event(); p._resume_evt.set()
    p._eof_fired = False
    eof_called = threading.Event()

    class _FakeCp:
        def __init__(self):
            self._closed = threading.Event()
            self._alive = True
            self.close_calls = 0

        def read(self, maxlen):
            # close 전까진 데이터 없이 블록(자식 죽어도 EOF 안 오는 버그 상황),
            # close() 후엔 b"" (EOF).
            while not self._closed.is_set():
                time.sleep(0.005)
            return b""

        def wait(self, timeout_ms):
            if self._alive:
                time.sleep(0.005)        # WaitForSingleObject 타임아웃 모사
                return None
            return 0                     # 종료코드(≠None) → 자식 종료

        def close(self):
            self.close_calls += 1
            self._closed.set()

    fake = _FakeCp()
    p._cp = fake
    loop = asyncio.get_running_loop()
    got = bytearray()
    p.start_reader(loop, got.extend, eof_called.set)
    try:
        await asyncio.sleep(0.05)
        assert not eof_called.is_set(), "자식 생존 중엔 EOF 가 불리면 안 됨"
        assert fake.close_calls == 0, "생존 중엔 콘솔 hangup 금지"
        fake._alive = False              # 셸 exit 모사
        await asyncio.sleep(0.08)
        assert fake.close_calls >= 1, "감시자가 종료 감지 후 콘솔 hangup 해야"
        assert eof_called.is_set(), "hangup 으로 read EOF → _fire_eof 가 불려야"
    finally:
        p.stop_reader()
        p._reader.join(timeout=0.5)
        p._watcher.join(timeout=0.5)


async def test_owned_conpty_watcher_quiet_on_teardown():
    """정상 teardown(stop/close 가 _stop set)일 땐 감시자가 콘솔을 건드리지 않는다 —
    그 경로는 backend close() 가 직접 read 를 깨우므로 이중 close 를 피한다."""
    import threading

    p = pty_backend._OwnedConPty.__new__(pty_backend._OwnedConPty)
    p._stop = threading.Event()

    class _FakeCp:
        def __init__(self):
            self.close_calls = 0

        def wait(self, timeout_ms):
            return None                  # 계속 생존

        def close(self):
            self.close_calls += 1

    fake = p._cp = _FakeCp()
    p._stop.set()                        # teardown 선반영
    watcher = threading.Thread(target=p._watch_exit, daemon=True)
    watcher.start()
    watcher.join(timeout=0.5)
    assert not watcher.is_alive(), "_stop 이면 즉시 종료해야"
    assert fake.close_calls == 0, "teardown 경로에선 감시자가 close 하지 않아야"


async def test_owned_conpty_real_child_exit_fires_eof_windows():
    """실 Windows: owned-ConPTY 자식(cmd.exe)이 **스스로 종료**하면 감시 스레드가
    실 conhost 를 hangup 해 블로킹 read 를 EOF 로 깨워 on_eof 가 불린다.

    위 `test_owned_conpty_watcher_fires_eof_on_child_exit` 가 가짜 `_ConPty` 로
    감시자 *로직* 만 보던 것을, 여기선 **실 ConPTY + 실 conhost** 로 끌어올린다 —
    그동안 office1 박스에서 수동으로 보던 '원격 페더레이션 좀비 탭' 회귀(동기
    ReadFile 은 conhost 생존 시 자식 종료에도 EOF 를 안 줘 reader 가 영원히
    블록)를 GHA windows-latest 러너가 자동 검증한다. 출력 *왕복* 이 아니라
    프로세스 *종료 감지* → 콘솔 hangup → EOF 경로라, 실 인터랙티브 콘솔이 필요한
    바이트 왕복(scripts/validate_conpty.py)과 달리 헤드리스 CI 에서도 성립한다.

    `cmd /c exit` 는 attach 직후 즉시 끝난다 → 감시자 `wait`(폴 200ms)가 종료를
    보고 `close()` → reader 의 블로킹 read 가 b"" → `_fire_eof`. 좀비였다면 EOF 가
    영영 안 와 wait_for 가 타임아웃(=실패)으로 회귀를 드러낸다."""
    if not pty_backend.IS_WINDOWS:
        skip("Windows 전용(ConPTY)")
    pty = pty_backend._OwnedConPty(["cmd.exe", "/c", "exit"], cols=80, rows=24,
                                   cwd=None, env=dict(os.environ))
    loop = asyncio.get_running_loop()
    eof = asyncio.Event()
    got = bytearray()
    # _fire_eof 는 _read_loop 가 call_soon_threadsafe 로 루프 스레드에 올리므로
    # on_eof(=eof.set) 도 루프 스레드에서 실행된다 → asyncio.Event.set 안전.
    pty.start_reader(loop, got.extend, eof.set)
    try:
        # 폴 200ms + 콘솔 hangup + 콜드스타트 cmd 핸드셰이크 여유로 넉넉히 8초.
        await asyncio.wait_for(eof.wait(), 8.0)
    finally:
        pty.stop_reader()
        pty.terminate()
        pty.close()
        if pty._reader:
            pty._reader.join(timeout=1.0)
        if pty._watcher:
            pty._watcher.join(timeout=1.0)
    assert eof.is_set(), "실 자식 종료 후 감시자가 EOF 를 깨워야(좀비 회귀 방지)"
