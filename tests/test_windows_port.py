"""Windows 네이티브 포팅 회귀 가드(docs/WINDOWS_PORT.md).

POSIX 전용 모듈(fcntl/termios 등)이 없는 환경을 시뮬레이션해, 포팅으로 이식한
모듈들이 Windows 에서도 import 되는지 못박는다. 실제 Windows 박스가 없어도 이
머신(macOS)에서 "그 모듈이 없을 때" 를 흉내 내 깨짐을 잡는다.
"""
import builtins
import importlib
import sys

import harness  # noqa: F401 (sys.path 설정)


class _BlockImport:
    """주어진 모듈 이름들의 import 를 ModuleNotFoundError 로 막는 컨텍스트.

    이미 import 된 캐시도 비우고, __import__ 를 가로채 해당 이름을 차단한다.
    Windows 에 fcntl/termios 가 없는 상황을 macOS 에서 재현하기 위함.
    """

    def __init__(self, *names):
        self.names = set(names)
        self._saved_modules = {}
        self._orig_import = None

    def __enter__(self):
        # 차단 대상 + 그를 캐시한 protocol 을 sys.modules 에서 치워 fresh import 유도.
        for n in list(sys.modules):
            if n in self.names or n == "pytmuxlib.protocol":
                self._saved_modules[n] = sys.modules.pop(n)
        self._orig_import = builtins.__import__

        def guard(name, *a, **k):
            root = name.split(".")[0]
            if name in self.names or root in self.names:
                raise ModuleNotFoundError(f"No module named {name!r} (simulated)")
            return self._orig_import(name, *a, **k)

        builtins.__import__ = guard
        return self

    def __exit__(self, *exc):
        builtins.__import__ = self._orig_import
        # 시뮬레이션 중 새로 캐시된 모듈을 비우고 원래 모듈을 복원.
        for n in list(sys.modules):
            if n in self.names or n == "pytmuxlib.protocol":
                sys.modules.pop(n, None)
        sys.modules.update(self._saved_modules)
        return False


async def test_protocol_imports_without_fcntl_termios():
    """fcntl/termios 가 없어도 pytmuxlib.protocol 이 import 돼야 한다(Windows 차단점).

    과거: protocol.py 가 모듈 최상단에서 import fcntl/termios → Windows 에서 model/
    server 까지 연쇄로 깨졌다. 이제 set_winsize 안에서 지연 import 하므로 모듈 import
    자체는 성립한다.
    """
    with _BlockImport("fcntl", "termios"):
        mod = importlib.import_module("pytmuxlib.protocol")
        # 모듈이 실제로 로드됐고 핵심 심볼이 노출되는지 확인.
        assert hasattr(mod, "set_winsize")
        assert hasattr(mod, "read_msg") and hasattr(mod, "write_msg")
        assert mod.MIN_W >= 1 and mod.MIN_H >= 1
    # 컨텍스트를 벗어나면 정상 protocol 이 복원돼 set_winsize 가 동작해야 한다(Unix).
    import pytmuxlib.protocol as proto
    assert callable(proto.set_winsize)


async def test_shell_argv_os_branch():
    """proc.shell_argv 가 OS 별 셸로 분기한다(server pipe-pane / client run-shell 공용).

    POSIX 는 /bin/sh -c, Windows(nt)는 cmd /c. IS_WINDOWS 를 패치해 양쪽을 검증한다.
    """
    from unittest import mock
    from pytmuxlib import proc

    with mock.patch.object(proc, "IS_WINDOWS", False):
        assert proc.shell_argv("echo hi") == ["/bin/sh", "-c", "echo hi"]
    with mock.patch.object(proc, "IS_WINDOWS", True), mock.patch.dict(
            "os.environ", {"COMSPEC": r"C:\Windows\System32\cmd.exe"}):
        assert proc.shell_argv("dir") == [r"C:\Windows\System32\cmd.exe", "/c", "dir"]
    # COMSPEC 미설정 Windows → cmd.exe 폴백.
    with mock.patch.object(proc, "IS_WINDOWS", True), \
            mock.patch.dict("os.environ", {}, clear=True):
        assert proc.shell_argv("dir") == ["cmd.exe", "/c", "dir"]


async def test_client_shell_argv_delegates_to_proc():
    """client._shell_argv 는 proc.shell_argv 로 위임한다(중복 제거 회귀 가드)."""
    from unittest import mock
    from pytmuxlib import proc
    from pytmuxlib.client import _shell_argv

    with mock.patch.object(proc, "IS_WINDOWS", False):
        assert _shell_argv("echo hi") == ["/bin/sh", "-c", "echo hi"]
    with mock.patch.object(proc, "IS_WINDOWS", True), \
            mock.patch.dict("os.environ", {}, clear=True):
        assert _shell_argv("dir") == ["cmd.exe", "/c", "dir"]


async def test_replay_record_guarded_on_windows():
    """replay.run_record 는 Windows 에서 PTY import 없이 명확히 거부한다(후순위).

    과거: Windows 에서 호출 시 함수 내부 `import pty`(termios 의존)가
    ModuleNotFoundError 로 깨졌다. 이제 os.name=='nt' 면 메시지 후 코드 2 반환.
    """
    from unittest import mock
    from pytmuxlib.replay import run_record

    with mock.patch("os.name", "nt"):
        rc = run_record("/nonexistent/should-not-open.raw", 80, 24, ["echo", "x"])
    assert rc == 2


async def test_fg_command_guarded_on_windows():
    """Server._fg_command 는 Windows 에서 os.tcgetpgrp 호출 전에 None 으로 폴백.

    os.tcgetpgrp 는 Windows 에 아예 없어 AttributeError 가 나는데, 함수의
    except 는 OSError 만 잡는다. IS_WINDOWS 가드가 먼저 끊지 않으면 자동 탭이름
    루프가 깨진다. tcgetpgrp 가 AttributeError 를 던지게 해 가드 효력을 못박는다.
    """
    from unittest import mock
    from pytmuxlib import pty_backend
    from pytmuxlib.server import Server

    # create=True: 실제 Windows 에는 os.tcgetpgrp 가 아예 없어 패치 대상이 없으므로
    # (mock 이 원본 속성을 못 찾아 AttributeError) 강제로 만들어 패치한다. macOS 에선
    # 원래 존재하므로 무해.
    with mock.patch.object(pty_backend, "IS_WINDOWS", True), \
            mock.patch("os.tcgetpgrp", side_effect=AttributeError, create=True):
        # self 를 쓰지 않는 메서드라 더미 인스턴스 없이 호출 가능.
        assert Server._fg_command(None, -1) is None


async def test_winpty_backpressure_gate():
    """_WinPty.pause_reader/resume_reader 가 리더 게이트(Event)를 제대로 여닫고,
    stop/close 가 멈춘 리더를 깨우는지(§10 ② Windows 백프레셔).

    실제 winpty 없이 __new__ 로 인스턴스를 만들어 게이트 상태만 검증한다 — 과거
    Windows 는 pause/resume 가 base no-op 이라 드레인 중에도 리더가 무한정 읽어
    _feedbuf 가 폭증하고 loop 가 _deliver 로 포화됐다.
    """
    import threading
    from pytmuxlib.pty_backend import _WinPty

    wp = _WinPty.__new__(_WinPty)        # __init__(winpty.spawn) 우회
    wp._stop = threading.Event()
    wp._resume_evt = threading.Event()
    wp._resume_evt.set()

    assert wp._resume_evt.is_set(), "기본은 읽기 허용"
    wp.pause_reader()
    assert not wp._resume_evt.is_set(), "pause → 게이트 닫힘(리더 멈춤)"
    wp.resume_reader()
    assert wp._resume_evt.is_set(), "resume → 게이트 열림"
    # stop_reader 는 _stop 을 세우고 게이트도 열어(멈춘 리더가 _stop 을 보게) 깨운다.
    wp.pause_reader()
    wp.stop_reader()
    assert wp._stop.is_set() and wp._resume_evt.is_set(), \
        "stop 은 멈춘 리더를 깨워 빠져나가게 해야(교착 방지)"


async def test_render_only_resize_without_fcntl():
    """렌더 전용 패널(pty=None) resize 는 fcntl 없는 Windows 에서도 안 깨진다.

    set_winsize 는 fcntl/termios 를 지연 import 하므로 ModuleNotFoundError(=
    ImportError 하위)가 날 수 있다. resize 폴백이 이를 삼켜야 한다.
    """
    from pytmuxlib.model import Pane

    pane = Pane(-1, -1, 80, 24)  # pty=None → 렌더 전용 폴백 경로
    with _BlockImport("fcntl", "termios"):
        pane.resize(30, 100)  # 예외 없이 통과해야 함
    assert (pane.cols, pane.rows) == (30, 100)
