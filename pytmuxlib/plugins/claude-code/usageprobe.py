"""M19: 그림자 `/usage` 질의 — 사용자에게 안 보이는 숨은 대화형 `claude` 세션을
PTY 로 띄워 `/usage` 패널을 렌더·스크랩해 사용량 한도를 얻는다(방법 B, §10).

핵심: print 모드(`claude -p`)는 `/usage` 를 한 줄로 잘라 한도 숫자를 안 주므로(§10.4
B2 기각), **대화형 TUI 패널**을 pyte 로 렌더해 긁어야 세션(5시간)/주간 한도 % 와 리셋
시각이 나온다. 사용자 화면(현재 세션)엔 전혀 안 뜬다(off-screen 서브프로세스).

블로킹 함수다 — 서버는 `loop.run_in_executor` 로 호출한다. 실패/타임아웃은 전부
None(예외 안 냄).

크로스플랫폼(docs/internal/WINDOWS_PORT.md): I/O 는 `_Session` 백엔드로 갇힌다.
  * **POSIX**(`_PosixSession`): `pty.openpty`+`subprocess`(close_fds·start_new_session)
    로 fork 안전(멀티스레드 executor)하게 띄우고, `select` 로 타임아웃 read 한다.
  * **Windows**(`_WinSession`): fork·`select`·`termios` 가 없으므로 ConPTY(`pywinpty`)
    로 띄운다. pywinpty 의 `read()` 는 **블로킹**이라 동기 펌프에서 타임아웃을 못 거니
    `pty_backend._WinPty` 와 같은 모델로 **전용 리더 스레드**가 블로킹 read 후 바이트를
    버퍼에 쌓고, 메인 펌프는 그 버퍼를 시간기반으로 폴링한다(타임아웃 보장).
세션 생성은 `_open_session` 팩토리로 분리해 테스트가 실 `claude` 없이 캔드 패널을
재생할 수 있게 한다."""
from __future__ import annotations

import os
import subprocess
import threading
import time

from .claude import (claude_account, claude_account_full,
                     claude_managed_settings_yes, claude_model, parse_usage)

IS_WINDOWS = os.name == "nt"

_READ = 65536

# 조직 관리 설정(managed settings) 최초 1회 승인 게이트(2026-07-15 요청): OTEL/텔레
# 메트리 관련 관리 설정이 걸린 조직 계정은 claude 부팅 직후 이 화면으로 멈춰 서서,
# 위 "shortcuts"/"shift+tab"/"for agents" 부팅 신호가 영영 안 뜨고 boot_timeout 으로
# 조용히 실패한다(그림자 프로브는 무인 실행이라 사람이 답할 수 없음). 화면의 기본
# 선택(❯ 1. Yes, I trust these settings)을 그대로 확정하는 Enter 1회만 주입해 통과
# 시킨다 — 이후 실제 부팅 신호 대기로 이어간다.
#
# 안전(2026-07-16 검수 SEC-1): 헤더 문구만 보고 무턱대고 Enter 를 치면 향후 claude
# 빌드가 옵션 순서를 바꾸거나 기본을 "No, exit" 로 두면 **미지의 선택을 확정**한다.
# 게다가 수락은 사용자 config 에 머신 전역으로 남아, 프로브가 usage_refresh_sec 마다
# 도는 탓에 조직이 앞으로 푸시할 임의 관리설정(텔레메트리 엔드포인트·권한정책)까지
# 사용자가 검토화면을 보기도 전에 조용히 자동승인될 수 있다. 그래서 ① 긍정 기본선택
# 줄(❯/> 셀렉터가 "Yes, I trust these settings" 위에 있을 때)이 실제로 화면에 떠
# 있을 때만 Enter 를 치고 ② 부팅 대기(_open 직후 첫 wait_for)에서만 발동시킨다 —
# /usage·/status 스크랩 중엔 이 자동승인을 열지 않는다(부팅 이후엔 이 화면이 안 뜬다).
#
# 판정 자체는 claude.claude_managed_settings_yes 하나로 단일화(2026-07-24) — 사용자
# 패널의 자동 승인(servermixin._scan_managed_settings)과 같은 화면을 같은 규칙으로
# 본다. 여기 이름(_managed_yes_selected)은 기존 호출부/테스트 호환 별칭.
_managed_yes_selected = claude_managed_settings_yes
_MANAGED_SETTINGS_ACCEPT = b"\r"


class _PosixSession:
    """POSIX: pty.openpty + subprocess, select 기반 타임아웃 read."""

    def __init__(self, argv, cwd, env, cols, rows):
        import pty
        import struct

        self._master, slave = pty.openpty()
        try:
            import fcntl
            import termios
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass
        try:
            self._proc = subprocess.Popen(
                argv, stdin=slave, stdout=slave, stderr=slave,
                cwd=cwd, env=env, close_fds=True, start_new_session=True)
        finally:
            try:
                os.close(slave)
            except OSError:
                pass

    def read(self, timeout: float) -> bytes:
        import select
        try:
            r, _, _ = select.select([self._master], [], [], timeout)
        except OSError:
            return b""
        if not r:
            return b""
        try:
            return os.read(self._master, _READ)
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        try:
            os.write(self._master, data)
        except OSError:
            pass

    def kill(self) -> None:
        try:
            self._proc.kill()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=3)
        except Exception:
            pass

    def close(self) -> None:
        try:
            os.close(self._master)
        except OSError:
            pass


class _WinSession:
    """Windows: ConPTY(pywinpty). 블로킹 read 를 리더 스레드가 버퍼에 쌓고, 메인
    펌프는 버퍼를 폴링한다(pty_backend._WinPty 의 스레드 펌프 모델을 동기 버전으로)."""

    def __init__(self, argv, cwd, env, cols, rows):
        from winpty import PtyProcess

        spec = argv if len(argv) > 1 else argv[0]
        self._proc = PtyProcess.spawn(
            spec, cwd=cwd, env=env, dimensions=(max(1, rows), max(1, cols)))
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader = threading.Thread(
            target=self._read_loop, name=f"usageprobe-{self._proc.pid}",
            daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        proc = self._proc
        while not self._stop.is_set():
            try:
                s = proc.read(_READ)            # 블로킹 — 데이터/EOF 까지 대기
            except EOFError:
                break
            except Exception:                   # ConPTY 종료 시 잡다한 오류 방어
                break
            if s:
                b = s.encode("utf-8", "replace")  # pywinpty 가 이미 디코드한 str → bytes
                with self._lock:
                    self._buf += b
            elif not proc.isalive():
                break

    def read(self, timeout: float) -> bytes:
        end = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._buf:
                    out = bytes(self._buf)
                    self._buf.clear()
                    return out
            if time.monotonic() >= end:
                return b""
            time.sleep(0.02)

    def write(self, data: bytes) -> None:
        try:
            self._proc.write(data.decode("utf-8", "replace"))
        except Exception:
            pass

    def kill(self) -> None:
        try:
            self._proc.terminate(force=True)
        except Exception:
            pass

    def close(self) -> None:
        self._stop.set()                        # 리더 스레드(daemon)는 read/EOF 후 종료
        try:
            self._proc.close()
        except Exception:
            pass


# ☠☠ **`PtyProcess.spawn` 은 GIL 을 쥔 채 돈다 — 서버 안에서 부르면 서버가 언다**
# (2026-11-19 실측 · pywinpty 3.0.3 · Python 3.12). 그 호출이 이 상자에서 8~13초 걸리는
# 동안 같은 프로세스의 다른 스레드가 **7.1초 + 5.8초** 멎었다(`read`·`write`·`isalive`
# 는 GIL 을 놓는다 — 문제는 spawn 하나). 프로브는 executor 스레드에서 돌지만 GIL 은
# 하나라 **이벤트 루프가 통째로 멈추고**, 그동안 사용자의 키 입력·엔터가 소켓에 쌓였다가
# 수십 초 뒤 한꺼번에 들어갔다. 라이브 서버의 error.log 에서 「idle client evicted (no msg
# in 74s·163s·279s …)」가 매번 **같은 초의** `usage_probe` 타임아웃과 짝지어 남은 것이 그
# 흔적이다(클라는 0.5초마다 ping 을 보내니 퇴거 = 서버 루프 정지).
# ⇒ Windows 에서는 ConPTY 를 **자식 파이썬 프로세스**가 띄우고, 서버는 그 자식의 파이프만
#   읽는다(`CreateProcess`·파이프 read 는 GIL 을 놓는다). 끝낼 때는 그 자식의 pid 트리를
#   `taskkill /T` 로 거둔다 — pywinpty 의 `terminate(force=True)` 는 이 상자에서
#   `Access is denied` 로 실패해 숨은 claude 를 못 죽이고 있었다(같은 실측).
#   되돌리기: `PYTMUX_USAGEPROBE_INPROC=1` (종전 `_WinSession` — 서버가 다시 언다).
#: 자식 호스트가 ConPTY 를 띄우고 READY 를 알릴 때까지의 예산(초). 파이썬 기동(이 상자
#: 4~8초) + pywinpty spawn(8~13초)에 부하 여유를 얹었다. 부팅 예산과 따로 센다 —
#: 종전에도 spawn 시간은 부팅 대기 밖이었다.
SPAWN_TIMEOUT = 45.0

# 자식 호스트 본문(`python -c`). pytmuxlib 를 import 하지 않는다 — 기동을 싸게 두고,
# 서버 모듈 상태와 무관하게. 프로토콜: 첫 줄 `READY <pid>\n`(또는 `ERR …\n`) 뒤로는
# ConPTY 출력 바이트(UTF-8) 그대로 stdout 으로, stdin 바이트는 그대로 ConPTY 입력으로.
# stdin EOF(= 부모가 닫았거나 죽었다)면 자식 claude 를 끝내고 나간다.
_WIN_HOST_SRC = r'''
import codecs, json, os, sys, threading
out = sys.stdout.buffer
try:
    from winpty import PtyProcess
    argv, cwd, cols, rows = json.loads(sys.argv[1])
    proc = PtyProcess.spawn(argv if len(argv) > 1 else argv[0], cwd=cwd,
                            env=dict(os.environ), dimensions=(rows, cols))
except Exception as e:
    out.write(("ERR %r\n" % (e,)).encode("utf-8", "replace")); out.flush()
    os._exit(1)
out.write(("READY %d\n" % proc.pid).encode()); out.flush()
def _pump_in():
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    inp = sys.stdin.buffer
    while True:
        b = inp.read1(4096)
        if not b:
            break
        s = dec.decode(b)
        if s:
            try:
                proc.write(s)
            except Exception:
                break
    try:
        proc.terminate(force=True)
    except Exception:
        pass
    os._exit(0)
threading.Thread(target=_pump_in, daemon=True).start()
while True:
    try:
        s = proc.read(65536)
    except Exception:
        break
    if s:
        try:
            out.write(s.encode("utf-8", "replace")); out.flush()
        except Exception:
            break
    elif not proc.isalive():
        break
os._exit(0)
'''


def _host_python() -> str:
    """자식 호스트를 돌릴 인터프리터. 서버는 대개 `pythonw.exe` 다 — 파이프를 넘기면
    그것도 되지만, 옆의 `python.exe` 를 `CREATE_NO_WINDOW` 로 띄우는 편이 stdio 가
    확실하다(창은 안 뜬다)."""
    import sys
    exe = sys.executable or "python"
    base = os.path.basename(exe).lower()
    if base == "pythonw.exe":
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(cand):
            return cand
    return exe


class _WinHostSession:
    """Windows: ConPTY 를 자식 파이썬 프로세스(`_WIN_HOST_SRC`)에 띄우는 세션.
    `_WinSession` 과 같은 표면(read/write/kill/close)이라 `query_usage` 는 모른다.
    왜 따로 띄우는가 = 위 ☠☠ 주석(spawn 의 GIL 점유가 서버 루프를 얼린다)."""

    def __init__(self, argv, cwd, env, cols, rows,
                 spawn_timeout: float = SPAWN_TIMEOUT, cancel=None):
        import json
        exe = _host_python()
        self._proc = subprocess.Popen(
            [exe, "-c", _WIN_HOST_SRC,
             json.dumps([list(argv), cwd, max(1, cols), max(1, rows)])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env,
            # 프로브 cwd(사용자 프로젝트)에서 `-c` 를 돌리면 그 폴더의 .py 가 stdlib
            # 이름을 가릴 수 있다 — 호스트는 인터프리터 폴더에서 돈다. ConPTY 자식의
            # cwd 는 argv 와 함께 넘긴 값이다.
            cwd=os.path.dirname(exe) or None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.pid = self._proc.pid
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._err: bytes | None = None
        self._reader = threading.Thread(
            target=self._read_loop, name=f"usageprobe-host-{self.pid}",
            daemon=True)
        self._reader.start()
        deadline = time.monotonic() + spawn_timeout
        why = None
        while not self._ready.wait(0.2):
            if cancel is not None and cancel.is_set():
                why = b"cancelled"
                break
            if time.monotonic() >= deadline:
                why = b"spawn timeout"
                break
        if why is None and self._err is not None:
            why = self._err
        if why is not None:
            self.kill()
            self.close()
            raise RuntimeError("usageprobe host: %s"
                               % why.decode("utf-8", "replace").strip())

    def _read_loop(self) -> None:
        f = self._proc.stdout
        try:
            try:
                line = f.readline()
            except Exception:
                line = b""
            if not line.startswith(b"READY"):
                self._err = line or b"host exited before READY"
                self._ready.set()
                return
            self._ready.set()
            while True:
                try:
                    b = f.read1(_READ)
                except Exception:
                    break
                if not b:
                    break
                with self._lock:
                    self._buf += b
        finally:
            try:
                f.close()   # stdout 은 리더만 닫는다(close() 의 ☠ 주석)
            except Exception:
                pass

    def read(self, timeout: float) -> bytes:
        end = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._buf:
                    out = bytes(self._buf)
                    self._buf.clear()
                    return out
            if time.monotonic() >= end:
                return b""
            time.sleep(0.02)

    def write(self, data: bytes) -> None:
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except Exception:
            pass

    def kill(self) -> None:
        """자식 호스트와 그 아래(OpenConsole·claude)를 **pid 트리로만** 거둔다 —
        이름 매칭으로 넓히지 않는다(CLAUDE.md ⛔ 일괄 kill 금지)."""
        if self._proc.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(self.pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=15)
            except Exception:
                pass
        try:
            self._proc.kill()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5)
        except Exception:
            pass

    def close(self) -> None:
        # ☠ stdout 은 **여기서 닫지 않는다** — 리더 스레드가 그 버퍼의 readline/read1 에
        # 막혀 있으면 close 가 같은 버퍼 락을 기다려 영영 안 돌아온다(2026-11-19 실측: 전체
        # 스위트가 이 줄에서 절단됐다). 호스트가 죽어도 EOF 가 늦을 수 있다 — Popen 이 자식
        # 쪽 끝을 상속 가능하게 쥔 짧은 창에 같은 프로세스의 다른 CreateProcess(패널 셸
        # 등)가 그 끝을 물려받으면, 그 프로세스가 끝날 때까지 쓰기 끝이 산다. stdout 은
        # 리더(daemon)가 EOF 에서 스스로 닫는다.
        try:
            self._proc.stdin.close()
        except Exception:
            pass


class _ProbeCancelled(Exception):
    """`query_usage` 의 `cancel` 이 섰다 — 펌프를 빠져나가는 표지."""


def _open_session(argv, cwd, env, cols, rows, cancel=None):
    """플랫폼별 `_Session` 을 연다(실패 시 예외 — 호출부가 None 으로 흡수).
    테스트는 이 함수를 몽키패치해 실 `claude` 없이 캔드 패널을 재생한다.
    `cancel`(threading.Event) 은 자식 호스트의 READY 대기를 끊는다."""
    if IS_WINDOWS:
        if os.environ.get("PYTMUX_USAGEPROBE_INPROC") == "1":
            return _WinSession(argv, cwd, env, cols, rows)
        return _WinHostSession(argv, cwd, env, cols, rows, cancel=cancel)
    return _PosixSession(argv, cwd, env, cols, rows)


#: 부팅 신호(`shortcuts`/`shift+tab`/`for agents`)를 기다리는 예산(초).
#
# ☠ **12.0 이었다 — 여유가 10% 뿐이었다**(pytmux-382 · 2026-09-03 office1 실측).
# 그 상자(Windows · 조직 관리 설정이 걸린 계정)에서 부팅 신호까지 **10.2 · 10.2 ·
# 11.4초**가 걸린다. 그것도 «전용 프로세스»에서 잰 값이고, 제품은 같은 일을 **서버의
# executor 스레드**에서 한다 — 이벤트 루프·ConPTY 리더 스레드 셋과 같은 GIL 을 나눠
# 쓰므로 그보다 느리다. 여유 0.6초는 그 차이를 못 버틴다.
#
# ⛔ 그리고 **초과하면 값이 아니라 침묵이 남는다**: `wait_for` 가 False 면 `query_usage`
# 는 그 예산을 전부 태운 뒤 `None` 을 돌려주고, 호출부는 첫 실패만 로그에 남긴다
# (`_usage_probe_err` 래치). 곧 «비싼데 아무 말 없는» 실패다 — 그래서 예산은
# **여유를 배수로** 잡고(아래), 대신 느려지면 그 사실이 보이게 계측을 달았다.
BOOT_TIMEOUT = 25.0
#: `/usage` 를 보낸 뒤 `% used` 가 뜰 때까지의 예산(초). 같은 실측에서 5.0~5.7초.
PANEL_TIMEOUT = 12.0
#: 「느리다」의 문턱 — 부팅이 예산의 이 비율을 넘게 쓰면 호출부가 한 줄 남긴다.
SLOW_FRACTION = 0.6
#: `query_usage` 안의 고정 펌프 합(초): /usage 뒤 0.4 + Esc 뒤 0.2 + /status 뒤 0.4.
_FIXED_PUMPS = 1.0


def worst_case_seconds(boot_timeout: float = BOOT_TIMEOUT,
                       panel_timeout: float = PANEL_TIMEOUT) -> float:
    """`query_usage` 가 **최악에** 쓰는 시간(초) — 바깥 캡의 근거(pytmux-34 #10).

    부팅 대기는 관리 설정 승인 화면을 넘기면 **한 번 더** 열리고(`wait_for` 의
    `end = … + maxs`), 패널 대기는 `/usage` 와 `/status`(계정·모델 폴백 — 거의 항상
    돈다) 두 번 쓴다. 각 `wait_for` 는 마지막 `pump(0.3)` 하나만큼 넘칠 수 있다.
    손으로 합을 적어 둔 캡(62초)이 이 최악(≈74초)보다 작아, 시간 초과가 바쁨 플래그를
    일찍 풀고 숨은 claude 를 겹쳐 띄웠다."""
    # Windows 는 자식 호스트가 READY 를 알릴 때까지(SPAWN_TIMEOUT)도 더한다 — 종전엔
    # spawn 이 루프를 얼려 바깥 캡이 그 동안 못 울렸지만, 이제 루프가 살아 있어 캡이
    # spawn 도중에 울릴 수 있다.
    spawn = SPAWN_TIMEOUT if IS_WINDOWS else 0.0
    return (spawn + 2 * boot_timeout + 2 * panel_timeout + _FIXED_PUMPS
            + 4 * 0.3 + 0.5)


# 숨은 세션에 붙이는 인자(pytmux-669 — query_usage 안의 주석 참조).
PROBE_ARGS = ["--strict-mcp-config"]


def query_usage(cmd: str = "claude", cwd: str | None = None,
                cols: int = 95, rows: int = 45,
                boot_timeout: float = BOOT_TIMEOUT,
                panel_timeout: float = PANEL_TIMEOUT,
                timings: dict | None = None,
                cancel=None):
    """숨은 `claude` 를 띄워 `/usage` 패널을 스크랩·파싱한다. 결과 dict(parse_usage,
    추가로 그림자 세션 계정 `account`: 일치 확인용·없으면 None) 또는 None. 입력 주입은
    `/usage`+Enter (계정 미식별 시에만 Esc+`/status` 1회 추가 — 아래) 뿐이고
    끝나면 즉시 kill 한다.

    `account`: 이 숨은 세션이 로그인된 계정(claude_account 별칭). 폰/데스크탑 앱과
    **다른 계정**이면 한도 %·리셋이 실제로 달라지므로(요청), 사용자가 눈으로
    대조할 수 있게 함께 싣는다. 계정 라벨은 /status(Status 탭)에만 있어(부팅·
    Usage 탭엔 부재 — 2026-06-11 실관찰) 거기까지 못 잡으면 None.

    `model`: 이 숨은 세션의 활성 모델(claude_model 파싱값, 예 'opus-4.8'). 라이브
    Claude 패널은 모델 배지를 **상시 표시하지 않아**(idle 푸터엔 'auto mode on …'
    뿐, 배지는 /model 변경 직후 등에만 잠깐 — 2026-06-22 실관찰) 화면 스크랩만으론
    토큰이 model NULL('?')로 적재되는 일이 잦다. 그림자 프로브는 부팅 배지·/status
    의 Model 라인에서 활성 모델을 잡아 model 폴백을 제공한다(서버 _scan_claude 가
    라이브 배지 우선·없으면 이 값으로 채움). /usage 패널은 'Sonnet only' 같은 한도
    카테고리 라벨이 모델로 오인돼 출처에서 제외한다. 못 잡으면 None.

    `cancel`(threading.Event|None): 서버가 바깥에서 자르거나 종료할 때 세운다 — 다음
    펌프(≤0.3초)에서 대기를 그만두고 숨은 claude 를 거둔 뒤 None 을 돌려준다."""
    t_start = time.monotonic()
    if timings is not None:
        # ⛔ **먼저 «실패» 로 채운다** — 아래 어느 갈래로 빠져나가도(예외·조기 return)
        # 호출부가 「돌긴 돌았다」를 알 수 있어야 한다. 성공 갈래만 채우면 정작 알고
        # 싶은 실패 회차가 빈손으로 남는다.
        timings.update(boot=None, panel=None, total=None, ok=False)

    def _stamp(**kw):
        if timings is not None:
            timings.update(kw)
            timings["total"] = time.monotonic() - t_start

    try:
        from pytmuxlib.nativescreen import NativeScreen
        from pytmuxlib.vtparse import VTTokenizer
    except Exception:
        return None
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env.pop("PYTMUX", None)
    env.pop("LC_PYTMUX", None)
    # ☠☠ **이 한 줄이 사용자 패널의 렌더러를 지킨다**(pytmux-414 · 실측 2026-08-27).
    #   Claude Code 2.1.247 에는 *fullscreen boot canary* 가 있다 — fullscreen 으로 부팅하면
    #   `~/.claude.json` 에 `fullscreenBootPending[pid]` 를 적고 **첫 프레임 + 10초**를
    #   살아남아야 그 기록을 지운다. 기록이 남은 채 pid 가 죽어 있으면 다음 실행이
    #   스트라이크 +1 로 세고, **2회**면 `fullscreenAutoDisabled` 를 써서 그 머신의
    #   fullscreen 을 통째로 끈다(키 = 버전·hostname·platform).
    #   ⇒ 이 프로브는 진짜 대화형 `claude` 를 띄웠다가 `finally: sess.kill()` 로
    #     **강제 종료**한다(POSIX SIGKILL · Windows TerminateProcess). 어느 쪽도 자식의
    #     `process.on("exit")` 훅을 못 돌리므로 죽은 pid 기록이 남는다. 프로브가
    #     2~8분마다 도니까 스트라이크는 금방 2 가 되고, 그때부터 **사용자의** claude 패널이
    #     classic 렌더러로 조용히 떨어진다(설정에 `"tui": "fullscreen"` 을 적어 두었어도).
    #     실측: claude 설치 09:10 → 09:18:21 에 `strikes: 2` 도달(8분).
    #   ★ 그래서 canary 를 «끄는» 것이 아니라 **애초에 안 무장시킨다** — 이 env 를 주면
    #     진입경로가 `env_off` 가 되어 기록 자체가 안 생긴다. 프로브는 alt-screen 이
    #     필요 없다(아래 스크래퍼가 `alt_hook=None` 이다).
    #   ⛔ **`CLAUDE_CODE_NO_FLICKER=1` 로 대신하지 마라** — canary 는 같이 피하지만 자식이
    #     fullscreen 으로 떠서 이 스크래퍼의 전제("2J+draw 로 단일 버퍼에 그린다")를 깬다.
    #   ⚠ 이것이 alt-screen 처리 결함을 고치는 것이 아니다 — 회계(canary) 문제다.
    #     반증으로 `CLAUDE_CODE_NO_FLICKER=1` 로 돌려도 `/usage` 파싱은 성공했다.
    env["CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN"] = "1"
    # Windows 는 PATH/PATHEXT 검색이 spawn 백엔드마다 다를 수 있어 미리 .exe 로 해석.
    argv = [cmd]
    if IS_WINDOWS:
        import shutil
        argv = [shutil.which(cmd) or cmd]
    # pytmux-669: 숨은 세션은 `/usage` 만 읽는다 — **MCP 서버는 필요 없다**. 인자 없이
    # 띄우면 사용자 범위 MCP 서버를 회차마다 전부 다시 띄운다(실측 2026-11-25: python 2 +
    # conhost · node · cmd+node). 프로세스 기동이 느린 상자에서는 그 기동이 프로브의 부팅
    # 예산을 먹고, 사용자가 쓰는 세션들의 기동과 겹쳐 그쪽 입력까지 멈춘다(pytmux-668).
    # `--mcp-config` 없이 주면 「아무 MCP 서버도 안 쓴다」가 된다.
    argv += PROBE_ARGS
    if cancel is not None and cancel.is_set():
        return None
    try:
        # cancel 은 있을 때만 넘긴다 — 시험이 갈아끼우는 `_open_session` 은 위치 인자
        # 다섯만 받는다.
        sess = _open_session(argv, cwd, env, cols, rows,
                             **({"cancel": cancel} if cancel is not None else {}))
    except Exception:
        return None

    # 숨은 세션 TUI 스크랩용 화면(구 pyte.Screen 대응 — M4b native 단일화). alt-screen
    # 전환은 무시(alt_hook=None)해도 /usage TUI 는 2J+draw 로 단일 버퍼에 그려 disp()
    # 가 같은 가시 콘텐츠를 낸다.
    sc = NativeScreen(cols, rows)
    st = VTTokenizer(sc)

    def pump(sec: float) -> None:
        end = time.monotonic() + sec
        while time.monotonic() < end:
            if cancel is not None and cancel.is_set():
                raise _ProbeCancelled()   # 아래 except 가 None 으로, finally 가 kill 로
            data = sess.read(0.15)
            if data:
                st.feed(data)

    def disp() -> str:
        return "\n".join(sc.display)

    managed_settings_seen = False

    def wait_for(subs, maxs: float, accept_managed: bool = False) -> bool:
        """subs(문자열 또는 후보 튜플) 중 하나라도 화면에 뜰 때까지 대기. `accept_managed`
        (부팅 대기에서만 True)이면 그 사이 조직 관리 설정 승인 화면이 뜨고 **긍정
        기본선택(❯ Yes, I trust these settings)이 실제로 선택돼 있을 때만** Enter 1회를
        확정해 넘기고 대기를 이어간다 — 그림자 프로브는 무인 실행이라 사람이 답할 수
        없다(2026-07-15). 셀렉터가 다른 옵션에 있거나 문구가 바뀌면 치지 않는다(SEC-1)."""
        nonlocal managed_settings_seen
        cands = (subs,) if isinstance(subs, str) else tuple(subs)
        end = time.monotonic() + maxs
        while time.monotonic() < end:
            pump(0.3)
            scr = disp()
            if any(c in scr for c in cands):
                return True
            if (accept_managed and not managed_settings_seen
                    and _managed_yes_selected(scr)):
                managed_settings_seen = True
                sess.write(_MANAGED_SETTINGS_ACCEPT)
                end = time.monotonic() + maxs   # 승인 후 실제 부팅 대기시간 재확보
        return False

    try:
        # 입력 프롬프트 준비까지 대기. 트러스트 대화상자 등으로 안 뜨면 타임아웃
        # → None(안전). claude 버전마다 부팅 화면 힌트가 달라 여러 신호 중 하나라도
        # 잡히면 준비로 본다: 구버전 "? for shortcuts", v2.1.x 푸터("shift+tab to
        # cycle"·"← for agents"). 어느 쪽도 입력 박스가 떴다는 신뢰 신호다.
        if not wait_for(("shortcuts", "shift+tab", "for agents"), boot_timeout,
                        accept_managed=True):
            _stamp(boot=None)           # 부팅 신호를 못 봤다 = 예산을 다 태웠다
            return None
        _stamp(boot=time.monotonic() - t_start)
        # 부팅 화면에 계정/조직 표시가 있으면 먼저 캡처(/usage 가 화면을 덮기 전).
        # 별칭(acct, 로그·DB 영속용)과 전체 이메일(acct_full, 사용자 본인 화면 표시용)을
        # 같은 텍스트에서 함께 잡는다 — footer 와 동일한 프라이버시 분리(별칭=디스크,
        # 전체=휘발성 표시). 팝업이 전체 이메일을 보이도록(요청).
        acct = claude_account(disp())
        acct_full = claude_account_full(disp())
        model = claude_model(disp())   # 부팅/welcome 화면 배지(있으면)
        sess.write(b"/usage\r")
        t_boot = time.monotonic()
        _stamp(panel=None if not wait_for("% used", panel_timeout)
               else time.monotonic() - t_boot)
        pump(0.4)
        screen = disp()
        usage = parse_usage(screen)
        if usage is not None:
            # /usage 화면에도 계정 신호가 있으면 보강(부팅서 못 잡았을 때).
            acct = acct or claude_account(screen)
            acct_full = acct_full or claude_account_full(screen)
            # 주의: /usage 패널에서는 모델을 뽑지 않는다 — 'Current week (Sonnet
            # only)' 같은 **한도 카테고리** 라벨이 활성 모델로 오인되기 때문(실측).
            # 모델은 부팅 배지·/status(Model 라인)에서만 잡는다(아래).
            if not acct or not model:
                # 계정 식별 폴백(2026-06-11 §5.5 관찰): 부팅 화면·/usage(Usage 탭)
                # 에는 계정 라벨이 **아예 없다**(실캡처 — limits 스냅샷 20/20 이
                # account None 이던 원인). 같은 설정 패널의 Status 탭(/status)에만
                # `Organization:`/`Email:` 라벨이 있으므로, 패널을 닫고 /status 를
                # 한 번 더 스크랩한다(같은 숨은 세션 재사용·토큰 비용 0·계정이
                # 이미 잡혔으면 생략). /status 탭은 활성 모델도 보여줘(2026-06-22)
                # 배지가 화면에 안 떠 model 이 None 일 때 여기서 함께 채운다.
                sess.write(b"\x1b")             # /usage 패널 닫기(Esc to cancel)
                pump(0.2)
                sess.write(b"/status\r")
                if wait_for(("Organization:", "Email:", "Login method"),
                            panel_timeout):
                    pump(0.4)
                    acct = acct or claude_account(disp())
                    acct_full = acct_full or claude_account_full(disp())
                    model = model or claude_model(disp())
            usage["account"] = acct
            usage["model"] = model
            # 전체 이메일(없으면 별칭 폴백). DB 영속 컬럼엔 없으므로 디스크엔 안 남고,
            # 라이브 status 로만 흘러 팝업/오버레이가 전체를 표시한다.
            usage["account_full"] = acct_full or acct
            _stamp(ok=True)
        return usage
    except Exception:
        return None
    finally:
        sess.kill()
        sess.close()
