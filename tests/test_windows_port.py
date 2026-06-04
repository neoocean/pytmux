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
