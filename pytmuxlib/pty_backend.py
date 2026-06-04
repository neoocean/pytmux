"""크로스플랫폼 PTY 백엔드 추상층 (docs/WINDOWS_PORT.md §6-1 ①②).

`server.py` 가 OS 별 PTY/프로세스 분기를 직접 알지 않고 이 모듈의 `spawn()` 과
반환된 `PtyProcess` 의 메서드만 부르도록 갇히는 곳이다. 두 백엔드를 제공한다.

  * **Unix** (`_UnixPty`): 기존과 동일하게 `pty.fork()` 로 fork+exec+PTY 를 한 번에
    만들고, master fd 를 asyncio `add_reader` 로 읽는다. `fcntl`/`termios`/`os.killpg`
    /`os.waitpid` 의미를 그대로 보존한다.
  * **Windows** (`_WinPty`): fork 가 없으므로 ConPTY(`pywinpty`) 로 셸을 띄우고,
    Proactor 루프가 ConPTY 파이프에 `add_reader` 를 못 거는 한계(§6②) 때문에
    **전용 리더 스레드**가 블로킹 read 후 `loop.call_soon_threadsafe` 로 바이트를
    이벤트 루프에 펌프한다. (poc/winpty_poc.py 에서 검증한 모델 그대로.)

두 백엔드는 동일한 표면을 노출한다:

    pty = spawn(argv, cols=.., rows=.., cwd=.., env=..)
    pty.start_reader(loop, on_data, on_eof)   # on_data(bytes), on_eof()
    pty.write(b"...")
    pty.set_winsize(rows, cols)
    pty.terminate()     # graceful  (Unix: SIGHUP / Windows: TerminateProcess)
    pty.kill()          # force     (Unix: SIGKILL / Windows: TerminateProcess)
    pty.reap(block=..)  # 좀비 회수 → 종료코드 또는 None
    pty.close()         # fd/핸들 해제

`on_data`/`on_eof` 는 **항상 이벤트 루프 스레드에서** 호출된다(Windows 도
`call_soon_threadsafe` 경유). 따라서 server.py 의 기존 콜백 코드가 스레드 안전성을
신경 쓸 필요 없이 그대로 옮겨온다.
"""
from __future__ import annotations

import os
import struct
import threading
from typing import Callable, Optional


IS_WINDOWS = os.name == "nt"

DEFAULT_READ = 65536

# Unix 시그널 상수(Windows 에는 SIGHUP 이 없어 자리표시자). 메서드 본문에서
# 전역으로 참조하므로 호출 시점(=Unix 런타임)에만 의미를 가진다.
try:
    import signal as _signal_mod
    _SIGHUP = _signal_mod.SIGHUP
    _SIGKILL = _signal_mod.SIGKILL
except (ImportError, AttributeError):
    _SIGHUP, _SIGKILL = 1, 9

OnData = Callable[[bytes], None]
OnEof = Callable[[], None]

__all__ = ["IS_WINDOWS", "PtyProcess", "spawn", "adopt"]


def adopt(fd: int, pid: int, *, cols: int, rows: int) -> "PtyProcess":
    """이미 살아 있는 셸의 master fd + 자식 pid 를 fork 없이 새 PtyProcess 로 감싼다.

    작업 보존 재시작(re-exec) 후 상속된 master fd 를 다시 채택하는 경로
    (docs/RESTART_SCENARIO.md ⓓ). PID 가 그대로라 reap/killpg 의미가 유효하다.
    POSIX 전용 — Windows ConPTY 핸들은 execv 상속 모델이 없어 지원하지 않는다(§6).
    """
    if IS_WINDOWS:
        raise NotImplementedError("fd adoption 은 POSIX 전용(ConPTY 핸들 비상속)")
    return _UnixPty.adopt(fd, pid, cols=cols, rows=rows)


def spawn(argv, *, cols: int, rows: int,
          cwd: Optional[str] = None, env: Optional[dict] = None) -> "PtyProcess":
    """셸/프로그램을 의사 터미널에 띄우고 핸들을 돌려준다.

    argv: 실행할 명령 토큰 리스트(예: ["/bin/zsh"], ["cmd.exe"]).
    cols/rows: 초기 터미널 크기.
    cwd: 시작 디렉터리(None=상속). env: 환경(None=현재 프로세스 환경 상속).
    """
    if IS_WINDOWS:
        return _WinPty(argv, cols=cols, rows=rows, cwd=cwd, env=env)
    return _UnixPty(argv, cols=cols, rows=rows, cwd=cwd, env=env)


class PtyProcess:
    """백엔드 공통 표면. 구체 구현은 `_UnixPty`/`_WinPty`."""

    pid: int = -1

    def start_reader(self, loop, on_data: OnData, on_eof: OnEof) -> None:
        raise NotImplementedError

    def stop_reader(self) -> None:
        raise NotImplementedError

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def set_winsize(self, rows: int, cols: int) -> None:
        raise NotImplementedError

    def terminate(self) -> None:
        """graceful 종료 요청(Unix SIGHUP)."""
        raise NotImplementedError

    def kill(self) -> None:
        """강제 종료(Unix SIGKILL)."""
        raise NotImplementedError

    def reap(self, *, block: bool = False) -> Optional[int]:
        """자식 좀비 회수. 종료됐으면 상태값, 아직이면 None."""
        raise NotImplementedError

    def close(self) -> None:
        """master fd / ConPTY 핸들 해제(이미 닫혔으면 무시)."""
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# Unix: pty.fork() + fcntl + add_reader
# ─────────────────────────────────────────────────────────────────────────────
class _UnixPty(PtyProcess):
    def __init__(self, argv, *, cols: int, rows: int, cwd, env):
        import pty

        self._fd = -1
        self.pid = -1
        self._loop = None
        self._on_data: Optional[OnData] = None
        self._on_eof: Optional[OnEof] = None
        self._reading = False

        pid, fd = pty.fork()
        if pid == 0:  # 자식: 프로그램으로 교체
            try:
                if cwd:
                    os.chdir(cwd)
            except OSError:
                pass
            child_env = dict(os.environ if env is None else env)
            try:
                os.execvpe(argv[0], list(argv), child_env)
            except Exception:
                os._exit(127)
        # 부모: master fd 를 쥔다.
        self.pid = pid
        self._fd = fd
        # close-on-exec: 이후 새 패널을 fork 할 때 자식 셸이 형제 패널들의 master fd 를
        # 상속해 fd 가 여러 프로세스에 살아남으면 패널 간 출력이 섞인다. 각 master 생성
        # 직후 CLOEXEC 를 걸어 다음 fork 의 자식이 어떤 형제 master 도 못 물려받게 한다.
        try:
            import fcntl
            flags = fcntl.fcntl(fd, fcntl.F_GETFD)
            fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
        except OSError:
            pass
        try:
            self.set_winsize(rows, cols)
        except OSError:
            pass
        os.set_blocking(fd, False)

    @classmethod
    def adopt(cls, fd: int, pid: int, *, cols: int, rows: int) -> "_UnixPty":
        """fork 없이 기존 fd+pid 를 감싸는 _UnixPty 를 만든다(재시작 후 fd 채택).

        re-exec 직전 넘길 fd 의 CLOEXEC 를 해제했으므로(서버 ⓐ), 여기서 다시 걸어
        §6 불변식(형제 패널 fd 누수 방지)을 복구한다. fd 는 상속돼 이미 열려 있다.
        """
        self = cls.__new__(cls)
        self._fd = fd
        self.pid = pid
        self._loop = None
        self._on_data = None
        self._on_eof = None
        self._reading = False
        try:
            import fcntl
            flags = fcntl.fcntl(fd, fcntl.F_GETFD)
            fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
        except OSError:
            pass
        try:
            self.set_winsize(rows, cols)
        except OSError:
            pass
        try:
            os.set_blocking(fd, False)
        except OSError:
            pass
        return self

    def fileno(self) -> int:
        return self._fd

    def start_reader(self, loop, on_data: OnData, on_eof: OnEof) -> None:
        self._loop = loop
        self._on_data = on_data
        self._on_eof = on_eof
        loop.add_reader(self._fd, self._readable)
        self._reading = True

    def _readable(self) -> None:
        import errno
        try:
            data = os.read(self._fd, DEFAULT_READ)
        except (BlockingIOError, InterruptedError):
            return
        except OSError as e:
            # macOS/BSD 는 슬레이브(자식)가 끝나면 master 읽기에서 EIO 를 던진다 →
            # 정상 EOF 로 처리. 그 외 일시적 오류는 패널을 닫지 않고 무시한다.
            if e.errno == errno.EIO:
                self._fire_eof()
            return
        if not data:
            self._fire_eof()
            return
        if self._on_data:
            self._on_data(data)

    def _fire_eof(self) -> None:
        self.stop_reader()
        cb, self._on_eof = self._on_eof, None
        if cb:
            cb()

    def stop_reader(self) -> None:
        if self._reading and self._loop is not None:
            try:
                self._loop.remove_reader(self._fd)
            except (OSError, ValueError):
                pass
        self._reading = False

    def write(self, data: bytes) -> None:
        os.write(self._fd, data)

    def set_winsize(self, rows: int, cols: int) -> None:
        import fcntl
        import termios
        rows = max(1, rows)
        cols = max(1, cols)
        fcntl.ioctl(self._fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))

    def terminate(self) -> None:
        self._signal_group(_SIGHUP)

    def kill(self) -> None:
        self._signal_group(_SIGKILL)

    def _signal_group(self, sig: int) -> None:
        if self.pid < 0:
            return
        try:
            os.killpg(os.getpgid(self.pid), sig)
        except (OSError, ProcessLookupError):
            pass

    def reap(self, *, block: bool = False) -> Optional[int]:
        if self.pid < 0:
            return None
        try:
            flags = 0 if block else os.WNOHANG
            pid, status = os.waitpid(self.pid, flags)
            if pid == 0:
                return None  # 아직 종료 안 됨(WNOHANG)
            return status
        except ChildProcessError:
            return None

    def close(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1


# ─────────────────────────────────────────────────────────────────────────────
# Windows: pywinpty(ConPTY) + 리더 스레드 펌프
# ─────────────────────────────────────────────────────────────────────────────
class _WinPty(PtyProcess):
    """ConPTY 백엔드. poc/winpty_poc.py 의 리더-스레드 펌프 모델을 일반화.

    주의(멀티바이트): pywinpty 의 고수준 `PtyProcess` 는 `read()`/`write()` 가 `str`
    이라, 멀티바이트(UTF-8) 시퀀스가 read 경계에서 잘리면 디코드가 깨질 수 있다. 본
    구현은 PoC 와 동일하게 utf-8 재인코딩으로 바이트 경로를 흉내 낸다. 실제 운영에서
    CJK/이모지 깨짐이 보이면 저수준 `winpty.PTY`(바이트) 경로로 교체한다(§6 NOTE).
    """

    def __init__(self, argv, *, cols: int, rows: int, cwd, env):
        from winpty import PtyProcess as _WinPtyProcess  # 지연 import

        self._loop = None
        self._on_data: Optional[OnData] = None
        self._on_eof: Optional[OnEof] = None
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._eof_fired = False

        spec = argv if len(argv) > 1 else argv[0]
        self._proc = _WinPtyProcess.spawn(
            spec, cwd=cwd, env=env, dimensions=(max(1, rows), max(1, cols)))
        self.pid = self._proc.pid

    def start_reader(self, loop, on_data: OnData, on_eof: OnEof) -> None:
        self._loop = loop
        self._on_data = on_data
        self._on_eof = on_eof
        self._reader = threading.Thread(
            target=self._read_loop, name=f"conpty-{self.pid}", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        proc = self._proc
        while not self._stop.is_set():
            try:
                s = proc.read(DEFAULT_READ)
            except EOFError:
                break
            except Exception:  # ConPTY 종료 시 잡다한 OSError 방어
                break
            if s:
                b = s.encode("utf-8", "replace")
                loop = self._loop
                if loop is not None:
                    loop.call_soon_threadsafe(self._deliver, b)
            elif not proc.isalive():
                break
        # EOF: 루프 스레드에서 콜백을 직접 부르지 않고 이벤트 루프로 넘긴다.
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._fire_eof)

    def _deliver(self, b: bytes) -> None:
        if not self._stop.is_set() and self._on_data:
            self._on_data(b)

    def _fire_eof(self) -> None:
        if self._eof_fired:
            return
        self._eof_fired = True
        cb, self._on_eof = self._on_eof, None
        if cb and not self._stop.is_set():
            cb()

    def stop_reader(self) -> None:
        self._stop.set()  # daemon 스레드는 다음 read/EOF 에서 빠져나감

    def write(self, data: bytes) -> None:
        # 고수준 API 는 str 을 받는다. 바이트 → utf-8 디코드(§6 NOTE 의 멀티바이트 주의).
        self._proc.write(data.decode("utf-8", "replace"))

    def set_winsize(self, rows: int, cols: int) -> None:
        self._proc.setwinsize(max(1, rows), max(1, cols))

    def terminate(self) -> None:
        try:
            self._proc.terminate(force=False)
        except Exception:
            pass

    def kill(self) -> None:
        try:
            self._proc.terminate(force=True)
        except Exception:
            pass

    def reap(self, *, block: bool = False) -> Optional[int]:
        try:
            if block:
                return self._proc.wait()
            if self._proc.isalive():
                return None
            return getattr(self._proc, "exitstatus", 0)
        except Exception:
            return None

    def close(self) -> None:
        self._stop.set()
        try:
            self._proc.close()
        except Exception:
            pass
