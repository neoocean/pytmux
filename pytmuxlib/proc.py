"""크로스플랫폼 프로세스/데몬화 추상층 (docs/WINDOWS_PORT.md §6-1 ③).

서버 데몬을 띄우고(부모가 죽어도 살아남게) 종료하는 OS 의존 분기를 가둔다.
패널 셸 PTY 프로세스의 생애주기는 pytmuxlib.pty_backend 가 따로 담당하고, 이
모듈은 **백그라운드 서버 데몬** 자체의 기동/종료만 책임진다.

  * **Unix**: 현재 launcher 의 이중 fork+setsid 데몬화 대신, 서버 하위명령을
    `start_new_session=True`(=setsid) 로 분리 기동한다. 부모가 종료하면 자식은
    init 으로 재부모화되어 컨트롤링 터미널과 무관하게 살아남는다.
  * **Windows**: fork 가 없으므로 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`
    플래그로 콘솔/프로세스그룹에서 분리해 서버를 기동한다.

종료는 프로세스 **트리**(자식 셸 포함)를 함께 정리한다:
  * Unix    : `killpg(getpgid(pid), SIGTERM→SIGKILL)`.
  * Windows : `taskkill /PID <pid> /T`(/F=강제) — /T 로 자식 트리까지.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional


IS_WINDOWS = os.name == "nt"

# Windows 전용 생성 플래그(POSIX 에선 0).
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200

__all__ = ["IS_WINDOWS", "spawn_detached", "terminate", "is_alive",
           "server_argv", "shell_argv"]


def shell_argv(cmd: str) -> List[str]:
    """문자열 명령을 OS 기본 셸로 실행하는 argv 로 만든다.

    pipe-pane(server) / run-shell·if-shell·display-popup(client) 처럼 사용자
    명령을 셸에 통째로 넘길 때 쓴다. POSIX: ``/bin/sh -c <cmd>``,
    Windows: ``cmd /c <cmd>``(COMSPEC 우선).
    """
    if IS_WINDOWS:
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", cmd]
    return ["/bin/sh", "-c", cmd]


def server_argv(sock_path: str, *, python: Optional[str] = None,
                entry: Optional[str] = None) -> List[str]:
    """서버를 전경 실행하는 하위명령 argv 를 만든다(`pytmux --socket .. server`).

    entry: pytmux.py 진입점 경로(기본 = 이 패키지 상위의 pytmux.py).
    """
    py = python or sys.executable
    if entry is None:
        entry = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "pytmux.py")
    return [py, entry, "--socket", sock_path, "server"]


def spawn_detached(argv: List[str], *, cwd: Optional[str] = None,
                   env: Optional[dict] = None) -> int:
    """부모 생애와 무관하게 살아남는 분리 프로세스를 띄우고 pid 를 돌려준다.

    표준 입출력은 모두 devnull 로 돌린다(데몬). close_fds 로 상속 fd 누수를 막는다.
    """
    devnull = subprocess.DEVNULL
    kwargs: dict = dict(cwd=cwd, env=env, stdin=devnull, stdout=devnull,
                        stderr=devnull, close_fds=True)
    if IS_WINDOWS:
        kwargs["creationflags"] = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    else:
        # setsid: 새 세션/프로세스그룹의 리더가 되어 컨트롤링 터미널에서 분리되고,
        # 종료 시 그룹 전체(자식 셸 포함)를 killpg 로 한 번에 정리할 수 있다.
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)
    return proc.pid


def is_alive(pid: int) -> bool:
    """pid 프로세스가 살아 있는지 확인."""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        # tasklist 로 존재 확인(권한 불문). 출력에 PID 가 있으면 살아 있음.
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 존재하지만 시그널 권한 없음 → 살아 있음
    except OSError:
        return False


def terminate(pid: int, *, force: bool = False) -> None:
    """프로세스(와 그 자식 트리)를 종료한다. 이미 없으면 조용히 무시.

    force=False 는 graceful(SIGTERM / taskkill), True 는 강제(SIGKILL / taskkill /F).
    """
    if pid <= 0:
        return
    if IS_WINDOWS:
        cmd = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            cmd.append("/F")
        try:
            subprocess.run(cmd, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    import signal
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(os.getpgid(pid), sig)
    except (OSError, ProcessLookupError):
        # 그룹을 못 찾으면 단일 프로세스라도 시도.
        try:
            os.kill(pid, sig)
        except (OSError, ProcessLookupError):
            pass
