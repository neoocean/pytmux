"""직접 소유하는 ConPTY(의사 콘솔) 저수준 래퍼 — **raw 바이트** 입출력
(docs/IMPROVEMENT_OPPORTUNITIES.md §1.1②).

pywinpty(winpty-rs)의 ConPTY 백엔드는 `read()` 가 **str 전용**이고 내부에서 ReadFile
청크(≤32768B)마다 `MultiByteToWideChar` 로 **carry 없이** 디코드해, 멀티바이트(CJK/이모지)
가 청크 경계에 걸리면 우리가 바이트를 받기 전에 이미 U+FFFD 로 영구 손상한다(실측 재현).
서버 feed 경로(`pyte.ByteStream`)는 영속 incremental decoder 로 바이트 경계를 carry
하므로 **raw 바이트만 받으면 Unix PTY 와 동일하게 무손상**이다.

이 모듈은 ctypes 로 Win32 `CreatePseudoConsole` 를 직접 호출해 의사 콘솔을 소유하고,
입출력을 우리 파이프로 직접 읽고 쓴다(winpty-rs/pywinpty 우회). 따라서 read 가 raw
bytes 를 돌려주고(디코드 안 함), write 도 raw `WriteFile`(§1.1③ — 비-UTF-8 입력 무손상)
이다.

설계 메모:
- **익명 파이프로 충분**(2026-06-11 실측): 과거 "익명 파이프로는 conhost 출력이 안 온다"
  결론은 **런처가 콘솔을 상속**한 탓(NonInteractive 도구 콘솔)이었고, 자식이 그 콘솔을
  붙잡았던 것이다. 런처가 **자기 콘솔을 가지면**(데몬 서버는 콘솔 없는 분리 프로세스라
  자연히 충족) 익명 파이프 + 표준 MS 레시피로 자식이 의사 콘솔에 정상 attach 한다.
- 핸들 타입을 엄격히(`c_void_p`) 지정해 64비트 핸들 절단(흔한 ctypes×x64 함정)을 막는다.
- read 는 **블로킹** 이라 호출자가 전용 스레드에서 돌린다(`pty_backend._OwnedConPty`).
  `close()` 가 `ClosePseudoConsole` 로 conhost 를 내리면 쓰기단이 닫혀 ReadFile 가 EOF(0)
  로 빠져나온다 → 스레드 정상 종료.
- write 는 익명 파이프라 오버랩 불가(버스트 paste 시 블로킹 가능) — keystroke 단위
  소량이라 v1 은 블로킹 허용. 필요 시 후속에서 오버랩 명명 파이프로 승급.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from ctypes import wintypes
from typing import Optional

# 이 모듈은 Windows 전용. import 자체는 POSIX 에서도 안전하도록(상수 정의만) 하되,
# 실제 호출 진입점에서 IS_WINDOWS 를 가정한다.
IS_WINDOWS = os.name == "nt"

STILL_ACTIVE = 259
_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400


def conpty_supported() -> bool:
    """이 OS 에 ConPTY API(CreatePseudoConsole)가 있으면 True(Win10 1809+/22631 충족)."""
    if not IS_WINDOWS:
        return False
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        return bool(getattr(k, "CreatePseudoConsole", None))
    except OSError:
        return False


if IS_WINDOWS:
    _HANDLE = wintypes.HANDLE
    _HPCON = wintypes.HANDLE
    _LPVOID = wintypes.LPVOID
    _SIZE_T = ctypes.c_size_t

    class _COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", _HANDLE), ("hStdOutput", _HANDLE), ("hStdError", _HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", _LPVOID)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", _HANDLE), ("hThread", _HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # 핸들/포인터는 전부 c_void_p 로 — 미지정 시 c_int(32비트)로 절단돼 어떤 핸들도
    # 무효가 되는 ctypes×x64 함정을 차단한다.
    _k32.CreatePipe.argtypes = [ctypes.POINTER(_HANDLE), ctypes.POINTER(_HANDLE),
                                _LPVOID, wintypes.DWORD]
    _k32.CreatePipe.restype = wintypes.BOOL
    _k32.CreatePseudoConsole.argtypes = [_COORD, _HANDLE, _HANDLE, wintypes.DWORD,
                                         ctypes.POINTER(_HPCON)]
    _k32.CreatePseudoConsole.restype = ctypes.c_long  # HRESULT
    _k32.ResizePseudoConsole.argtypes = [_HPCON, _COORD]
    _k32.ResizePseudoConsole.restype = ctypes.c_long
    _k32.ClosePseudoConsole.argtypes = [_HPCON]
    _k32.ClosePseudoConsole.restype = None
    _k32.InitializeProcThreadAttributeList.argtypes = [
        _LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_SIZE_T)]
    _k32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    _k32.UpdateProcThreadAttribute.argtypes = [
        _LPVOID, wintypes.DWORD, ctypes.c_void_p, _LPVOID, _SIZE_T,
        _LPVOID, ctypes.POINTER(_SIZE_T)]
    _k32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    _k32.DeleteProcThreadAttributeList.argtypes = [_LPVOID]
    _k32.DeleteProcThreadAttributeList.restype = None
    _k32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, _LPVOID, _LPVOID, wintypes.BOOL,
        wintypes.DWORD, _LPVOID, wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOEXW), ctypes.POINTER(_PROCESS_INFORMATION)]
    _k32.CreateProcessW.restype = wintypes.BOOL
    _k32.ReadFile.argtypes = [_HANDLE, _LPVOID, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), _LPVOID]
    _k32.ReadFile.restype = wintypes.BOOL
    _k32.WriteFile.argtypes = [_HANDLE, _LPVOID, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), _LPVOID]
    _k32.WriteFile.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [_HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.WaitForSingleObject.argtypes = [_HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.GetExitCodeProcess.argtypes = [_HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetExitCodeProcess.restype = wintypes.BOOL
    _k32.TerminateProcess.argtypes = [_HANDLE, wintypes.UINT]
    _k32.TerminateProcess.restype = wintypes.BOOL


def _build_env_block(env: Optional[dict]) -> Optional[ctypes.Array]:
    """CreateProcessW lpEnvironment 용 UTF-16 "K=V\\0...\\0\\0" 블록(None=상속)."""
    if env is None:
        return None
    parts = []
    for k, v in env.items():
        if k is None:
            continue
        parts.append("%s=%s" % (k, v))
    block = "\0".join(parts) + "\0\0"
    return ctypes.create_unicode_buffer(block, len(block))


class _ConPty:
    """ctypes 로 직접 소유하는 ConPTY. raw 바이트 read/write.

    수명: __init__(파이프+의사콘솔) → spawn(자식) → read()/write()/resize() →
    close(ClosePseudoConsole→자식 트리 hangup→핸들 해제).
    """

    def __init__(self, cols: int, rows: int):
        if not IS_WINDOWS:
            raise NotImplementedError("ConPTY 는 Windows 전용")
        self.pid = -1
        self._hpc = _HPCON()
        self._proc = _HANDLE()      # 자식 프로세스 핸들(wait/terminate)
        self._read = _HANDLE()      # 우리가 읽는 쪽(conhost 출력)
        self._write = _HANDLE()     # 우리가 쓰는 쪽(conhost 입력)
        self._closed = False
        self._exit: Optional[int] = None
        self._attrbuf = None        # 자식 spawn 후까지 유지(GC 방지)
        self._cmdbuf = None
        self._envbuf = None

        # 파이프: hPtyIn(자식이 읽음)↔hWriteIn(우리가 씀), hReadOut(우리가 읽음)↔
        #         hPtyOut(자식이 씀). conhost 측(hPtyIn/hPtyOut)은 CreatePseudoConsole
        #         이 내부 복제하므로 직후 닫는다.
        h_pty_in = _HANDLE(); h_write_in = _HANDLE()
        h_read_out = _HANDLE(); h_pty_out = _HANDLE()
        if not _k32.CreatePipe(ctypes.byref(h_pty_in), ctypes.byref(h_write_in), None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if not _k32.CreatePipe(ctypes.byref(h_read_out), ctypes.byref(h_pty_out), None, 0):
            _k32.CloseHandle(h_pty_in); _k32.CloseHandle(h_write_in)
            raise ctypes.WinError(ctypes.get_last_error())
        hr = _k32.CreatePseudoConsole(
            _COORD(max(1, cols), max(1, rows)), h_pty_in, h_pty_out, 0,
            ctypes.byref(self._hpc))
        # 의사콘솔이 복제한 conhost 측 핸들은 즉시 닫는다(우리 read/write 단만 보존).
        _k32.CloseHandle(h_pty_in); _k32.CloseHandle(h_pty_out)
        if hr != 0:
            _k32.CloseHandle(h_write_in); _k32.CloseHandle(h_read_out)
            raise OSError("CreatePseudoConsole failed: hr=0x%08x" % (hr & 0xFFFFFFFF))
        self._read = h_read_out
        self._write = h_write_in

    def spawn(self, argv, cwd: Optional[str] = None, env: Optional[dict] = None) -> int:
        """argv 를 의사 콘솔에 attach 해 띄운다. 자식 pid 반환."""
        appname = shutil.which(argv[0]) or argv[0]
        # 실행 파일 경로를 따옴표로 감싸고 나머지 인자를 표준 규칙으로 직렬화.
        if len(argv) > 1:
            cmdline = subprocess.list2cmdline([appname] + list(argv[1:]))
        else:
            cmdline = subprocess.list2cmdline([appname])

        # PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE 속성 리스트 구성.
        sz = _SIZE_T(0)
        _k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(sz))
        attrbuf = (ctypes.c_byte * sz.value)()
        if not _k32.InitializeProcThreadAttributeList(attrbuf, 1, 0, ctypes.byref(sz)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not _k32.UpdateProcThreadAttribute(
                attrbuf, 0, _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                self._hpc, ctypes.sizeof(_HPCON), None, None):
            err = ctypes.get_last_error()
            _k32.DeleteProcThreadAttributeList(attrbuf)
            raise ctypes.WinError(err)

        si = _STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        si.lpAttributeList = ctypes.cast(attrbuf, _LPVOID)
        pi = _PROCESS_INFORMATION()
        cmdbuf = ctypes.create_unicode_buffer(cmdline)
        envbuf = _build_env_block(env)
        flags = _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT
        ok = _k32.CreateProcessW(
            None, cmdbuf, None, None, False, flags,
            ctypes.cast(envbuf, _LPVOID) if envbuf is not None else None,
            cwd, ctypes.byref(si), ctypes.byref(pi))
        err = ctypes.get_last_error()
        _k32.DeleteProcThreadAttributeList(attrbuf)
        if not ok:
            raise ctypes.WinError(err)
        # 스레드 핸들은 불필요 → 닫고, 프로세스 핸들은 wait/terminate 용으로 보존.
        if pi.hThread:
            _k32.CloseHandle(pi.hThread)
        self._proc = _HANDLE(pi.hProcess)
        self.pid = int(pi.dwProcessId)
        # GC 로 버퍼가 사라지지 않게 인스턴스에 잡아 둔다(필요 끝나면 spawn 반환 후 무방).
        self._attrbuf = attrbuf
        self._cmdbuf = cmdbuf
        self._envbuf = envbuf
        return self.pid

    def read(self, maxlen: int = 65536) -> bytes:
        """블로킹 read. 자식/콘솔이 닫히면 b"" (EOF). raw 바이트(디코드 안 함)."""
        if self._closed or not self._read:
            return b""
        buf = (ctypes.c_byte * maxlen)()
        n = wintypes.DWORD(0)
        ok = _k32.ReadFile(self._read, buf, maxlen, ctypes.byref(n), None)
        if not ok or n.value == 0:
            return b""
        return bytes(buf[:n.value])

    def write(self, data: bytes) -> int:
        """raw 바이트 write(§1.1③ — 디코드/재인코드 없음). 쓴 바이트 수 반환."""
        if self._closed or not self._write or not data:
            return 0
        n = wintypes.DWORD(0)
        cbuf = (ctypes.c_byte * len(data)).from_buffer_copy(data)
        if not _k32.WriteFile(self._write, cbuf, len(data), ctypes.byref(n), None):
            return 0
        return int(n.value)

    def resize(self, cols: int, rows: int) -> None:
        if self._closed or not self._hpc:
            return
        _k32.ResizePseudoConsole(self._hpc, _COORD(max(1, cols), max(1, rows)))

    def is_alive(self) -> bool:
        if not self._proc:
            return False
        code = wintypes.DWORD(0)
        if not _k32.GetExitCodeProcess(self._proc, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE

    def exit_code(self) -> Optional[int]:
        """종료됐으면 종료코드, 살아있으면 None."""
        if self._exit is not None:
            return self._exit
        if not self._proc:
            return None
        code = wintypes.DWORD(0)
        if not _k32.GetExitCodeProcess(self._proc, ctypes.byref(code)):
            return None
        if code.value == STILL_ACTIVE:
            return None
        self._exit = int(code.value)
        return self._exit

    def wait(self, timeout_ms: int = 5000) -> Optional[int]:
        if self._proc:
            _k32.WaitForSingleObject(self._proc, timeout_ms)
        return self.exit_code()

    def terminate(self) -> None:
        """자식 프로세스를 즉시 종료(TerminateProcess). 손주는 close() 의 콘솔
        hangup 이 정리한다."""
        if self._proc:
            try:
                _k32.TerminateProcess(self._proc, 1)
            except OSError:
                pass

    def close(self) -> None:
        """ClosePseudoConsole → conhost hangup → attach 된 자식 트리 종료. 그 후 핸들 해제.

        의사콘솔을 먼저 닫으면 출력 쓰기단이 닫혀 블로킹 ReadFile 가 EOF 로 빠져나오므로
        리더 스레드가 자연 종료한다(별도 취소 불필요)."""
        if self._closed:
            return
        self._closed = True
        # 종료코드 보존 시도(닫기 전).
        try:
            ec = self.exit_code()
            if ec is not None:
                self._exit = ec
        except OSError:
            pass
        if self._hpc:
            try:
                _k32.ClosePseudoConsole(self._hpc)
            except OSError:
                pass
            self._hpc = _HPCON()
        for h_attr in ("_write", "_read", "_proc"):
            h = getattr(self, h_attr)
            if h:
                try:
                    _k32.CloseHandle(h)
                except OSError:
                    pass
                setattr(self, h_attr, _HANDLE())
