"""크로스플랫폼 PTY 백엔드 추상층 (docs/WINDOWS_PORT.md §6-1 ①②).

`server.py` 가 OS 별 PTY/프로세스 분기를 직접 알지 않고 이 모듈의 `spawn()` 과
반환된 `PtyProcess` 의 메서드만 부르도록 갇히는 곳이다. 세 백엔드를 제공한다.

  * **Unix** (`_UnixPty`): 기존과 동일하게 `pty.fork()` 로 fork+exec+PTY 를 한 번에
    만들고, master fd 를 asyncio `add_reader` 로 읽는다. `fcntl`/`termios`/`os.killpg`
    /`os.waitpid` 의미를 그대로 보존한다.
  * **Windows 기본** (`_OwnedConPty`): 직접 소유 ConPTY(§1.1② 돌파 레시피 — 숨은 콘솔
    + 동기 128KB 명명 파이프 + 블로킹 read). raw 바이트를 읽어 winpty-rs 의 32KB
    청크경계 디코드 손상을 구조적으로 회피한다(2026-06-12 기본 전환).
  * **Windows 롤백** (`_WinPty`): `PYTMUX_PTY_BACKEND=pywinpty` 일 때 쓰는 저수준
    `winpty.PTY` 경로. Proactor 루프가 ConPTY 핸들에 `add_reader` 를 못 거는 한계(§6②)
    때문에, 양 Windows 백엔드 모두 **전용 리더 스레드**가 read 후
    `loop.call_soon_threadsafe` 로 바이트를 이벤트 루프에 펌프한다.

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

from .protocol import FEED_SLICE  # PTY feed 슬라이스 크기(순환 import 없음)


IS_WINDOWS = os.name == "nt"

DEFAULT_READ = 65536

# 직접 소유 ConPTY 리더의 한 read 상한(#1.5). 64KB 대신 FEED_SLICE 로 끊어 읽으면 ①
# 백프레셔로 pause 가 걸렸을 때 in-flight read 가 최대 이 크기만 비집고 들어와(64KB 가
# 아니라) pause 가 더 빨리 듣고, ② 전달 청크가 서버 인라인 처리 한계(FEED_SLICE) 이하라
# 64KB 드레인 태스크 없이 곧장 ingest 된다.
_OWNED_READ_CHUNK = FEED_SLICE

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
        # **기본 백엔드 = 직접 소유 ConPTY(_OwnedConPty)** — §1.1② 돌파 레시피(숨은 콘솔
        # AllocConsole + 동기 128KB 명명 파이프 + 블로킹 read). raw 바이트를 읽어 pyte 에
        # 그대로 먹이므로 winpty-rs 의 32KB 청크경계 no-carry 디코드 손상(U+FFFD 폭주)을
        # 구조적으로 회피한다. 실 Claude 패널 + 250KB CJK/이모지/박스 버스트 라이브 검증
        # (2026-06-12): 안정 화면 손상이 pywinpty 와 동등(잔여 일시 FFFD 는 번들 OpenConsole
        # 공통이라 양 백엔드 1:1 동일) + 청크경계 무손상 우위. detached(데몬) 조건에서
        # 대화형 cmd 완전 스트리밍 실증.
        #   롤백: PYTMUX_PTY_BACKEND=pywinpty(또는 winpty) → 검증된 _WinPty 강제.
        #   안전망: owned 가 미지원이거나 spawn 이 던지면 조용히 _WinPty 로 폴백.
        choice = (os.environ.get("PYTMUX_PTY_BACKEND") or "").strip().lower()
        if choice not in ("pywinpty", "winpty"):
            try:
                from . import conpty as _conpty
                if _conpty.conpty_supported():
                    return _OwnedConPty(argv, cols=cols, rows=rows, cwd=cwd, env=env)
            except Exception:
                pass  # 폴백: pywinpty
        return _WinPty(argv, cols=cols, rows=rows, cwd=cwd, env=env)
    return _UnixPty(argv, cols=cols, rows=rows, cwd=cwd, env=env)


class PtyProcess:
    """백엔드 공통 표면. 구체 구현은 `_UnixPty`/`_WinPty`."""

    pid: int = -1

    def start_reader(self, loop, on_data: OnData, on_eof: OnEof) -> None:
        raise NotImplementedError

    def stop_reader(self) -> None:
        raise NotImplementedError

    def pause_reader(self) -> None:
        """읽기를 일시 중단한다(콜백/핸들은 보존 — resume_reader 로 재개).

        대량 출력 드레인 중(server._feed_drain) 더 읽지 않게 막아 커널 PTY 버퍼가
        producer 를 백프레셔하게 한다. 기본은 no-op(백엔드별 선택 구현 — POSIX 만
        의미가 있고 Windows 리더 스레드는 미지원이라 best-effort)."""

    def resume_reader(self) -> None:
        """pause_reader 로 멈춘 읽기를 재개한다. 기본 no-op."""

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

    def pause_reader(self) -> None:
        # stop_reader 와 달리 콜백(_on_data/_on_eof/_loop)을 보존해 resume 가능.
        if self._reading and self._loop is not None:
            try:
                self._loop.remove_reader(self._fd)
            except (OSError, ValueError):
                pass
            self._reading = False

    def resume_reader(self) -> None:
        if (not self._reading and self._loop is not None
                and self._on_data is not None and self._fd >= 0):
            self._loop.add_reader(self._fd, self._readable)
            self._reading = True

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
    """ConPTY 백엔드 — **저수준 `winpty.PTY` 직접** 사용 + 리더-스레드 펌프(#1.1① 일부·#1.5).

    주의: #1.1②(멀티바이트 경계 손상)는 **미해결**이다 — 아래 멀티바이트 절 참조.

    과거엔 고수준 `winpty.PtyProcess` 를 썼는데, 그건 내부에 **자체 리더 스레드 +
    localhost 소켓**을 두고 ConPTY 바이트를 `str→소켓 bytes→str` 로 나르며, 우리
    리더가 그 위에서 다시 `str→bytes` 로 재인코딩했다(패널당 스레드 2개·소켓 1개 +
    트랜스코드 4회, §1.1①). 저수준 `winpty.PTY` 로 내려가면 그 여분 스레드·소켓·
    트랜스코드 홉이 사라지고(C 디코드 1 + 우리 인코드 1), 리더 스레드가 read 전에
    백프레셔 게이트(_resume_evt)를 확인해 #1.5(in-flight 64KB read 가 드레인 중 끼어듦)도
    완화된다 — 저수준 read 는 보통 ConPTY 화면 diff 단위(작음)라 청크가 작다(단, CJK
    플러드처럼 producer 가 리더를 앞지르면 read 가 32768B 상한까지 차며, 이때 §1.1②
    경계 손상이 난다).

    멀티바이트 경계 손상(§1.1② — **미해결, 실측 재현됨 2026-06-11**): 이전 주석은
    `PTY.read()` 가 "완결 디코드한 유효 str" 이라 경계 절단이 없다고 했으나 **틀렸다**.
    pywinpty 가 래핑하는 winpty-rs 의 `read()` 는 `ReadFile` 로 **최대 32768 바이트**를
    읽어 `MultiByteToWideChar(CP_UTF8)` 로 디코드하는데, **read 경계를 넘는 미완결
    멀티바이트를 carry 하지 않는다**(청크마다 독립 디코드). 따라서 CJK/이모지가 32768B
    경계에 걸리면 winpty-rs 가 우리에게 str 을 넘기기 전에 이미 U+FFFD 로 영구 손상한다.
    실측(office 박스): CJK 플러드 시 **연속 읽기에서도** U+FFFD 발생(348KB→24개, 696KB
    →50개; max 청크 ~11k자 ≈ 33KB > 32768). 손상은 winpty-rs 안에서 일어나 우리 층에서
    carry 로 복구 불가(이미 U+FFFD). pywinpty 의 `PTY.read()` 는 str 전용이고 conout
    핸들도 미노출이라 raw 바이트 우회가 불가능하다. **진짜 해결책 = ConPTY 를 직접
    소유**(ctypes CreatePseudoConsole + overlapped 명명 파이프로 raw 바이트 읽기 → 서버
    `pyte.ByteStream` 의 incremental decoder 가 경계 carry, Unix 와 동일). 익명 CreatePipe
    로는 이 빌드에서 conhost 출력이 read 단에 도달하지 않아 winpty-rs 처럼 overlapped
    명명 파이프가 필요함을 실측 확인. 상세·진행은 docs/IMPROVEMENT_OPPORTUNITIES.md §1.1.

    참고(검토 완료·기각, 2026-06-11): pywinpty `PTY(...,backend=1)` 의 **WinPTY agent
    백엔드**(콘솔 화면버퍼 스크랩)는 32KB 경계 CJK 손상은 피하지만 ① 아스트랄/이모지(서로게이트
    쌍)를 100% U+FFFD 로 파괴(`ReadConsoleOutputW` 셀당 WCHAR 1개 한계, AgentConfig 무관)하고
    ② 대량 플러드 스크롤백을 합쳐 CJK 도 일부만 포착(손실성 화면 모델)한다 — 순 열화라 쓰지
    않는다. 남은 진짜 후보는 번들 OpenConsole 경로 재현뿐(§1.1 (a)).

    write(§1.1③): `PTY.write` 는 str 만 받으므로 입력은 `bytes→utf-8 디코드` 한다 —
    정상 UTF-8(한글 붙여넣기 포함)은 무손실이고, **순수 비-UTF-8 raw 바이트 전송은
    pywinpty 한계상 불가**(라이브러리가 str 전용). 위 ConPTY 직접 소유 시 raw WriteFile
    로 함께 해소된다.

    종료(§1.2): close() 가 ConPTY 핸들을 드롭하면 **콘솔 hangup 이 attach 된 자식
    트리(셸+손주)를 정리**한다(실측 확인 — 고아 없음). terminate/kill 은 직접 자식
    셸을 TerminateProcess 로 즉시 내리고, 손주 정리는 close() 가 맡는다.
    """

    def __init__(self, argv, *, cols: int, rows: int, cwd, env):
        import shutil
        import subprocess
        from winpty import PTY  # 지연 import(POSIX 에 winpty 부재)

        self._pty = None
        self._loop = None
        self._on_data: Optional[OnData] = None
        self._on_eof: Optional[OnEof] = None
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # 백프레셔용 게이트: set=읽기 허용(기본), clear=리더 스레드를 다음 read 전에
        # 멈춤(pause_reader). POSIX 의 remove_reader 와 같은 역할 — 대량 출력 드레인
        # 중(server._feed_drain) 더 읽지 않아 ConPTY 버퍼가 producer 를 막게 한다.
        self._resume_evt = threading.Event()
        self._resume_evt.set()
        self._eof_fired = False
        self._exit: Optional[int] = None

        # 고수준 PtyProcess.spawn 의 인자 구성을 그대로 재현: argv[0] 을 PATH 에서
        # 해석하고, 나머지를 cmdline 으로, env 는 "K=V\0...\0" 문자열로 넘긴다.
        appname = shutil.which(argv[0]) or argv[0]
        cmdline = (" " + subprocess.list2cmdline(list(argv[1:]))
                   if len(argv) > 1 else None)
        env_str = None
        if env is not None:
            env_str = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0"

        self._pty = PTY(max(1, cols), max(1, rows))  # 주의: (cols, rows) 순서
        if cmdline is None:
            self._pty.spawn(appname, cwd=cwd, env=env_str)
        else:
            self._pty.spawn(appname, cmdline=cmdline, cwd=cwd, env=env_str)
        self.pid = self._pty.pid

    def start_reader(self, loop, on_data: OnData, on_eof: OnEof) -> None:
        self._loop = loop
        self._on_data = on_data
        self._on_eof = on_eof
        self._reader = threading.Thread(
            target=self._read_loop, name=f"conpty-{self.pid}", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        pty = self._pty
        while not self._stop.is_set():
            # 백프레셔: pause_reader 로 게이트가 닫히면 드레인이 끝날 때까지 read 를
            # 멈춘다(게이트를 read *전에* 확인하므로 일시정지 중엔 read 가 아예 안 일어남
            # — POSIX remove_reader 와 동치). stop_reader/close 가 게이트+_stop 으로 깨운다.
            if not self._resume_evt.is_set():
                self._resume_evt.wait()
                if self._stop.is_set():
                    break
            try:
                # blocking=True: 데이터 올 때까지 블록(유휴 CPU 0). 저수준 read 는
                # ConPTY 화면 diff 단위라 청크가 작아, in-flight read 1건이 드레인에
                # 끼어들어도 무해·유한(#1.5). cancel_io()/EOF 는 WinptyError 로 깨운다.
                s = pty.read(blocking=True)
            except Exception:  # WinptyError(EOF/닫힘/cancel_io) 등 — 정리로 본다
                break
            if not s:
                if not pty.isalive():
                    break
                continue
            # winpty C 레이어가 완결 디코드한 유효 str → utf-8 인코드는 무손실.
            b = s.encode("utf-8", "replace")
            loop = self._loop
            if loop is not None:
                loop.call_soon_threadsafe(self._deliver, b)
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
        self._stop.set()       # daemon 스레드는 다음 read/EOF 에서 빠져나감
        self._resume_evt.set()  # 백프레셔로 멈춰 있던 리더를 깨워 _stop 을 보게 한다
        self._cancel_read()     # 블로킹 read 를 깨워 즉시 빠져나가게 한다

    def pause_reader(self) -> None:
        # 리더 스레드를 다음 read 전에 멈춰 ConPTY 버퍼가 백프레셔하게 한다(메모리
        # 폭증·이벤트 루프 플러딩 방지). POSIX _UnixPty.pause_reader(remove_reader)와
        # 동일 의도. 과거 Windows 는 base no-op 이라 드레인 중에도 리더가 계속 읽어
        # _feedbuf 가 무한정 커지고 loop 가 _deliver 콜백으로 포화됐다(§10 ②).
        self._resume_evt.clear()

    def resume_reader(self) -> None:
        self._resume_evt.set()

    def _cancel_read(self) -> None:
        """진행 중인 블로킹 read 를 취소해 리더 스레드를 깨운다(best-effort)."""
        pty = getattr(self, "_pty", None)  # __new__ 인스턴스(테스트) 안전
        if pty is not None:
            try:
                pty.cancel_io()
            except Exception:
                pass

    def write(self, data: bytes) -> None:
        # PTY.write 는 str 전용 → bytes 를 utf-8 디코드(§1.1③ 멀티바이트 주의).
        pty = self._pty
        if pty is None:
            return
        try:
            pty.write(data.decode("utf-8", "replace"))
        except Exception:
            pass  # 이미 닫힘/죽음 — 무해(연결 끊긴 정상 경로)

    def set_winsize(self, rows: int, cols: int) -> None:
        pty = self._pty
        if pty is None:
            return
        try:
            pty.set_size(max(1, cols), max(1, rows))  # 주의: (cols, rows) 순서
        except Exception:
            pass

    # NOTE(#28): 아래 정리 경로의 광역 `except Exception` 은 의도적이다 — pywinpty 는
    # OSError 가 아닌 자체 `winpty.WinptyError` 를 던질 수 있어, 좁히면 종료/정리 중
    # 그 예외가 새어 teardown 을 깨뜨린다. 정리 경로라 best-effort 로 삼킨다.
    def _terminate_child(self) -> None:
        """직접 자식 셸을 즉시 종료(TerminateProcess). 손주는 close() 의 콘솔
        hangup 이 정리한다(§1.2). Windows 엔 콘솔 외부에서 보낼 graceful 시그널이
        없어 graceful/force 가 사실상 동일하다."""
        if self.pid and self.pid > 0:
            try:
                import signal
                os.kill(self.pid, signal.SIGTERM)  # Windows: TerminateProcess
            except (OSError, ProcessLookupError, ValueError):
                pass

    def terminate(self) -> None:
        self._terminate_child()

    def kill(self) -> None:
        self._terminate_child()

    def reap(self, *, block: bool = False) -> Optional[int]:
        # Windows 프로세스는 좀비가 없어(회수 불필요) 생존/종료코드만 본다. close()
        # 후엔 _pty 가 없으니 pid 로 종료를 기다린다(WaitForSingleObject — 효율적).
        if block and self.pid and self.pid > 0:
            try:
                from . import proc
                proc._win_wait_dead(self.pid, 5.0)
            except Exception:
                pass
        pty = self._pty
        if pty is None:
            return self._exit
        try:
            if pty.isalive():
                return None
            return pty.get_exitstatus()
        except Exception:
            return self._exit

    def close(self) -> None:
        self._stop.set()
        self._resume_evt.set()  # 멈춘 리더를 깨워 빠져나가게 한다(teardown 교착 방지)
        pty, self._pty = self._pty, None
        if pty is not None:
            try:
                self._exit = pty.get_exitstatus()
            except Exception:
                pass
            self._cancel_read_on(pty)  # 블로킹 read 깨우기
        # pty 로컬 참조가 소멸하고 리더 스레드의 지역 ref 도 빠져나가면 refcount=0 →
        # Cython __dealloc__ 가 pseudoconsole 을 해제 → 콘솔 hangup 으로 attach 된
        # 자식 트리(셸+손주)가 종료된다(§1.2 — 고아 방지, 실측 확인).

    @staticmethod
    def _cancel_read_on(pty) -> None:
        try:
            pty.cancel_io()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Windows: 직접 소유 ConPTY(raw 바이트) — §1.1② 멀티바이트 손상 해결
# ─────────────────────────────────────────────────────────────────────────────
class _OwnedConPty(PtyProcess):
    """우리가 직접 소유하는 ConPTY 백엔드 — **raw 바이트** 입출력으로 §1.1②(멀티바이트
    경계 손상)·§1.1③(비-UTF-8 write 손상)을 동시에 해결한다.

    `conpty._ConPty`(ctypes `CreatePseudoConsole`)가 의사 콘솔을 소유하고 우리 파이프로
    raw 바이트를 읽고 쓴다. pywinpty(`_WinPty`)와 달리 **read 경로에서 디코드를 전혀 하지
    않는다** — winpty-rs 가 ReadFile 청크마다 carry 없이 `MultiByteToWideChar` 하던 손상
    원점을 우회한다. 받은 raw 바이트는 서버 feed 경로(`pyte.ByteStream`)의 영속 incremental
    decoder 가 경계 carry 해 Unix PTY 와 동일하게 무손상으로 처리된다.

    리더 모델은 `_WinPty` 와 동일(Proactor 가 콘솔 핸들에 add_reader 불가 → 전용 스레드가
    블로킹 read 후 `call_soon_threadsafe` 로 이벤트 루프에 펌프). `close()` 가
    `ClosePseudoConsole` 로 conhost 를 내리면 출력 쓰기단이 닫혀 블로킹 read 가 EOF(0)로
    빠져나오므로 별도 취소가 필요 없다.

    파이프는 동기 명명 파이프(128KB 버퍼) — write 는 keystroke·중소 paste 는 블로킹 없이
    들어가고, 버퍼를 넘는 대량 paste 버스트만 conhost 드레인까지 블록할 수 있다(opt-in 한계).
    """

    def __init__(self, argv, *, cols: int, rows: int, cwd, env):
        from . import conpty

        self._cp = None
        self._loop = None
        self._on_data: Optional[OnData] = None
        self._on_eof: Optional[OnEof] = None
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # 백프레셔 게이트(_WinPty 와 동일): set=읽기 허용(기본), clear=다음 read 전에 멈춤.
        self._resume_evt = threading.Event()
        self._resume_evt.set()
        self._eof_fired = False
        self._exit: Optional[int] = None

        cp = conpty._ConPty(max(1, cols), max(1, rows))
        cp.spawn(list(argv), cwd=cwd, env=env)
        self._cp = cp
        self.pid = cp.pid

    def start_reader(self, loop, on_data: OnData, on_eof: OnEof) -> None:
        self._loop = loop
        self._on_data = on_data
        self._on_eof = on_eof
        self._reader = threading.Thread(
            target=self._read_loop, name=f"owned-conpty-{self.pid}", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        cp = self._cp
        while not self._stop.is_set():
            if not self._resume_evt.is_set():
                self._resume_evt.wait()
                if self._stop.is_set():
                    break
            try:
                # FEED_SLICE 단위로 끊어 읽어 백프레셔 응답성 + 인라인 ingest(#1.5).
                data = cp.read(_OWNED_READ_CHUNK)   # 블로킹, raw 바이트
            except OSError:
                break
            if not data:
                break   # EOF: 자식 종료 / conhost 닫힘 / close()
            loop = self._loop
            if loop is not None:
                # raw 바이트를 그대로 전달(디코드 안 함) — 손상 원점 회피.
                loop.call_soon_threadsafe(self._deliver, data)
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
        self._stop.set()
        self._resume_evt.set()   # 백프레셔로 멈춘 리더를 깨워 _stop 을 보게 한다
        # 블로킹 read 는 close()/ClosePseudoConsole 의 EOF 로 빠져나온다.

    def pause_reader(self) -> None:
        self._resume_evt.clear()

    def resume_reader(self) -> None:
        self._resume_evt.set()

    def write(self, data: bytes) -> None:
        cp = self._cp
        if cp is None:
            return
        try:
            cp.write(data)   # raw 바이트(§1.1③ — 디코드/재인코드 없음)
        except OSError:
            pass

    def set_winsize(self, rows: int, cols: int) -> None:
        cp = self._cp
        if cp is None:
            return
        try:
            cp.resize(cols, rows)   # 주의: (cols, rows) 순서
        except OSError:
            pass

    def terminate(self) -> None:
        cp = self._cp
        if cp is not None:
            cp.terminate()

    def kill(self) -> None:
        cp = self._cp
        if cp is not None:
            cp.terminate()   # Windows 엔 graceful/force 구분이 없음

    def reap(self, *, block: bool = False) -> Optional[int]:
        cp = self._cp
        if cp is None:
            return self._exit
        try:
            return cp.wait(5000) if block else cp.exit_code()
        except OSError:
            return self._exit

    def close(self) -> None:
        self._stop.set()
        self._resume_evt.set()
        cp, self._cp = self._cp, None
        if cp is not None:
            try:
                ec = cp.exit_code()
                if ec is not None:
                    self._exit = ec
            except OSError:
                pass
            # ClosePseudoConsole → conhost hangup → 자식 트리 종료 + 블로킹 read EOF.
            try:
                cp.close()
            except OSError:
                pass
