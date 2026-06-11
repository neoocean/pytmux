"""크로스플랫폼 프로세스/데몬화 추상층 (docs/WINDOWS_PORT.md §6-1 ③).

서버 데몬을 띄우고(부모가 죽어도 살아남게) 종료하는 OS 의존 분기를 가둔다.
패널 셸 PTY 프로세스의 생애주기는 pytmuxlib.pty_backend 가 따로 담당하고, 이
모듈은 **백그라운드 서버 데몬** 자체의 기동/종료만 책임진다.

  * **Unix**: 현재 launcher 의 이중 fork+setsid 데몬화 대신, 서버 하위명령을
    `start_new_session=True`(=setsid) 로 분리 기동한다. 부모가 종료하면 자식은
    init 으로 재부모화되어 컨트롤링 터미널과 무관하게 살아남는다.
  * **Windows**: fork 가 없으므로 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP |
    CREATE_NO_WINDOW` 플래그로 콘솔/프로세스그룹에서 분리해 서버를 기동한다.
    + 가능하면 창 없는 `pythonw.exe` 로 띄워 데몬이 콘솔 창을 만들지 않게 한다
    (클라이언트는 기존 터미널에 그대로 전경 attach). 배경: 사용자가 보던
    "딸려 뜨는 PowerShell 창"은 데몬이 콘솔을 띄운 것이었고, 그 창을 닫으면
    서버가 죽어 attach 한 클라이언트도 함께 종료됐다.

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
# CREATE_NO_WINDOW: 콘솔 서브시스템 실행파일(python.exe)을 띄울 때 새 콘솔 창이
# 뜨지 않게 한다. DETACHED_PROCESS 만으로는 일부 기동 경로(.cmd 래퍼·py 런처
# 경유 등)에서 콘솔 창이 깜빡이거나 그대로 남는 사례가 있어 함께 건다.
_CREATE_NO_WINDOW = 0x08000000

__all__ = ["IS_WINDOWS", "spawn_detached", "terminate", "is_alive",
           "server_argv", "shell_argv", "no_window_kwargs",
           "open_in_file_manager", "process_cwd", "foreground_command"]


def no_window_kwargs() -> dict:
    """Windows 에서 콘솔 앱(clip.exe·cmd /c·tasklist·taskkill 등)을 subprocess 로
    띄울 때 **콘솔 창이 번쩍이지 않게** 할 creationflags 를 담은 kwargs 를 돌려준다.
    POSIX 에선 빈 dict(무영향). subprocess.run/Popen 에 `**proc.no_window_kwargs()`
    로 펼쳐 쓴다 — 사용자 요청: 윈도우 실행 시 PowerShell/cmd 창이 함께 뜨지 않게.
    (데몬 spawn 은 spawn_detached 가 이미 DETACHED|NO_WINDOW 로 처리.)"""
    if IS_WINDOWS:
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def _windowless_python() -> Optional[str]:
    """Windows 에서 창 없는 인터프리터(pythonw.exe) 절대경로(없으면 None).

    백그라운드 서버 데몬은 콘솔이 필요 없으므로 같은 디렉터리의 pythonw.exe 를
    선호한다. python.exe(콘솔 서브시스템)는 기동 경로에 따라 콘솔 창을 띄울 수
    있지만 pythonw.exe(GUI 서브시스템)는 절대 콘솔을 만들지 않는다.
    """
    if not IS_WINDOWS:
        return None
    exe = sys.executable or ""
    base = os.path.basename(exe).lower()
    # 이미 pythonw.exe 면 그대로. python.exe → 같은 폴더의 pythonw.exe 시도.
    if base == "pythonw.exe":
        return exe
    if base == "python.exe":
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return None


def open_in_file_manager(path: str) -> bool:
    """경로(보통 디렉터리)를 OS 파일 관리자로 연다(클라이언트 머신 기준). 성공 추정 시
    True. Windows=탐색기(os.startfile), macOS=open, Linux=xdg-open. 콘솔 앱이 아닌
    GUI 호출이라 창 깜빡임이 없고, 실패는 조용히 False(호출부가 메시지 표시)."""
    if not path:
        return False
    try:
        if IS_WINDOWS:
            os.startfile(path)  # type: ignore[attr-defined]  # Windows 전용
            return True
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, path], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True)
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


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
    # 백그라운드 데몬은 창 없는 pythonw.exe 를 선호(없으면 sys.executable).
    py = python or _windowless_python() or sys.executable
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
        kwargs["creationflags"] = (
            _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW)
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
        return _win_is_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 존재하지만 시그널 권한 없음 → 살아 있음
    except OSError:
        return False


def _win_is_alive(pid: int) -> bool:
    r"""Windows: tasklist 로 pid 존재 확인. **PID 컬럼을 정확히 대조**한다.

    과거엔 `str(pid) in tasklist_output` 으로 판정했는데, 이는 메모리 사용량 컬럼
    (예: `68,892 K`)에 pid 숫자열이 부분일치하면 **죽은 프로세스를 살았다고 오판**한다
    (pid 892 → "68,**892** K" 매치). `/FI "PID eq <pid>"` 필터가 이미 행을 좁히지만,
    필터는 best-effort 라 정확 대조를 안전망으로 둔다. `/FO CSV /NH` 로 받아 csv 로
    파싱하고 **두 번째 컬럼(PID)이 정확히 일치**하는 행이 있을 때만 살아 있다고 본다
    (대상 없으면 tasklist 가 stdout 에 "INFO: No tasks ..." 를 출력 → 파싱 행 0개).
    """
    import csv
    import io

    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            **no_window_kwargs()).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    target = str(pid)
    for row in csv.reader(io.StringIO(out)):
        # CSV 행: "이미지","PID","세션","세션#","메모리". PID 컬럼만 정확 대조.
        if len(row) >= 2 and row[1].strip() == target:
            return True
    return False


def process_cwd(pid: int) -> Optional[str]:
    """대상 프로세스(패널 셸)의 현재 작업 디렉토리(cwd)를 추정한다. 실패 시 None.

    Windows 는 `/proc`·`lsof` 가 없으므로 ctypes 로 대상 프로세스의 PEB →
    RTL_USER_PROCESS_PARAMETERS.CurrentDirectory(UNICODE_STRING)를 직접 읽는다
    (psutil 등 외부 의존 없이). POSIX 에선 None 을 돌려, 호출부(servertree
    `_pane_cwd`)의 `/proc`·`lsof` 경로가 처리하게 둔다 — 이 헬퍼는 Windows 갭만
    메운다. ncd(현재 디렉토리 강조)·default-path=current 가 이 cwd 에 의존한다."""
    if not IS_WINDOWS or pid <= 0:
        return None
    return _win_process_cwd(pid)


def _win_process_cwd(pid: int) -> Optional[str]:
    r"""Windows 전용: 대상 프로세스의 PEB 를 읽어 cwd 를 구한다.

    경로: OpenProcess(QUERY_INFORMATION|VM_READ) → NtQueryInformationProcess 로
    PebBaseAddress → ReadProcessMemory 로 PEB.ProcessParameters →
    RTL_USER_PROCESS_PARAMETERS.CurrentDirectory.DosPath(UNICODE_STRING) →
    Buffer(UTF-16LE) 를 읽는다. 구조체 오프셋은 32/64비트가 다르므로 우리 프로세스
    포인터 크기로 분기한다(셸 자식은 부모와 동일 비트수가 정상). 권한·레이아웃 차이
    등 어떤 실패든 None 으로 graceful — cwd 추정 실패는 ncd 가 루트에서 시작할 뿐."""
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010

        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        OpenProcess = kernel32.OpenProcess
        OpenProcess.restype = wintypes.HANDLE
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        ReadProcessMemory = kernel32.ReadProcessMemory
        ReadProcessMemory.restype = wintypes.BOOL
        ReadProcessMemory.argtypes = [
            wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

        class PROCESS_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("Reserved1", ctypes.c_void_p),
                ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved2", ctypes.c_void_p * 2),
                ("UniqueProcessId", ctypes.c_void_p),
                ("Reserved3", ctypes.c_void_p),
            ]

        NtQueryInformationProcess = ntdll.NtQueryInformationProcess
        NtQueryInformationProcess.restype = ctypes.c_long  # NTSTATUS
        NtQueryInformationProcess.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]

        is64 = ctypes.sizeof(ctypes.c_void_p) == 8
        # PEB.ProcessParameters · RTL_USER_PROCESS_PARAMETERS.CurrentDirectory ·
        # UNICODE_STRING.Buffer 의 비트수별 오프셋(문서화된 고정값).
        params_off = 0x20 if is64 else 0x10
        curdir_off = 0x38 if is64 else 0x24
        buf_off = 0x08 if is64 else 0x04
        ptr_size = 8 if is64 else 4

        h = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                        False, pid)
        if not h:
            return None
        try:
            pbi = PROCESS_BASIC_INFORMATION()
            ret = ctypes.c_ulong()
            if NtQueryInformationProcess(h, 0, ctypes.byref(pbi),
                                         ctypes.sizeof(pbi),
                                         ctypes.byref(ret)) != 0:
                return None
            peb = pbi.PebBaseAddress
            if not peb:
                return None

            def _read(addr, size):
                buf = (ctypes.c_char * size)()
                n = ctypes.c_size_t()
                ok = ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size,
                                       ctypes.byref(n))
                return buf.raw if ok and n.value == size else None

            def _read_ptr(addr):
                raw = _read(addr, ptr_size)
                return int.from_bytes(raw, "little") if raw else None

            params = _read_ptr(peb + params_off)
            if not params:
                return None
            # CurrentDirectory.DosPath: UNICODE_STRING{Length(USHORT), …, Buffer}.
            len_raw = _read(params + curdir_off, 2)
            if not len_raw:
                return None
            length = int.from_bytes(len_raw, "little")  # 바이트 길이
            if length == 0:
                return None
            buf_ptr = _read_ptr(params + curdir_off + buf_off)
            if not buf_ptr:
                return None
            data = _read(buf_ptr, length)
            if not data:
                return None
            path = data.decode("utf-16-le", "replace").rstrip("\x00")
            # cmd.exe 는 끝에 `\` 가 붙는 경우가 있다(루트 제외하고 정규화).
            return os.path.normpath(path) if path else None
        finally:
            CloseHandle(h)
    except Exception:
        return None


def foreground_command(pid: int) -> Optional[str]:
    """패널 셸(pid)의 '현재 포그라운드 명령' 이름을 추정한다(자동 탭이름·ssh/원격 감지).

    POSIX 는 servertree 가 `tcgetpgrp`+`ps` 로 직접 구하므로 이 헬퍼는 **Windows 갭만**
    메운다(#7). ConPTY 엔 포그라운드 프로세스 그룹 개념이 없어, 셸의 **가장 깊은 자손
    프로세스**를 그 시점의 실행 명령으로 보고 그 exe 이름(.exe 제거, 소문자 아님)을 돌려준다
    (셸 -> ssh, 셸 -> python -> child 등). 자손이 없으면(idle) 셸 자신의 이름을 돌려준다
    (POSIX 가 idle 시 fg pgrp = 셸 이름을 주는 것과 동일). 실패 시 None — 고정 탭이름 폴백."""
    if not IS_WINDOWS or pid <= 0:
        return None
    return _win_foreground_command(pid)


def _win_foreground_command(pid: int) -> Optional[str]:
    r"""Windows: ToolHelp 스냅샷으로 pid 의 가장 깊은 자손 프로세스 exe 이름을 구한다.

    CreateToolhelp32Snapshot(SNAPPROCESS) -> Process32FirstW/NextW 로 (pid, ppid, exe)
    를 모아 자식 맵을 만들고, pid 에서 BFS 로 가장 깊은 자손을 고른다(여러 leaf 면 깊이
    최댓값). 자손이 없으면 셸 자신의 exe. exe 는 basename 에서 `.exe`(대소문자 무시) 제거.
    어떤 실패든 None 으로 graceful — 자동 이름은 고정 탭이름으로 폴백된다."""
    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        INVALID = wintypes.HANDLE(-1).value

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.Process32FirstW.argtypes = [wintypes.HANDLE,
                                      ctypes.POINTER(PROCESSENTRY32W)]
        k.Process32NextW.argtypes = [wintypes.HANDLE,
                                     ctypes.POINTER(PROCESSENTRY32W)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]

        snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID:
            return None
        exe_of: dict[int, str] = {}
        children: dict[int, list[int]] = {}
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                cpid = int(entry.th32ProcessID)
                ppid = int(entry.th32ParentProcessID)
                exe_of[cpid] = entry.szExeFile
                children.setdefault(ppid, []).append(cpid)
                ok = k.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k.CloseHandle(snap)

        if pid not in exe_of:
            return None
        # pid 에서 BFS — 가장 깊은 자손(leaf 후보)을 고른다. ppid 재사용 오탐을 막으려
        # 방문 집합으로 사이클/재사용을 차단한다.
        best_pid, best_depth = pid, 0
        seen = {pid}
        frontier = [(pid, 0)]
        while frontier:
            cur, depth = frontier.pop()
            if depth > best_depth:
                best_pid, best_depth = cur, depth
            for ch in children.get(cur, ()):
                if ch not in seen:
                    seen.add(ch)
                    frontier.append((ch, depth + 1))
        name = exe_of.get(best_pid, "")
        if not name:
            return None
        base = os.path.basename(name)
        if base.lower().endswith(".exe"):
            base = base[:-4]
        return base or None
    except Exception:
        return None


def _win_taskkill(pid: int, *, force: bool, timeout: float = 10.0) -> None:
    """taskkill 1회 호출(/T=자식 트리, /F=강제). 실패·타임아웃은 조용히 무시.

    graceful(/F 없음) 은 창 없는 콘솔/분리 프로세스에서 WM_CLOSE 응답을 기다리며
    오래 블록될 수 있어, 호출부가 짧은 timeout 을 줄 수 있게 한다(타임아웃 시
    TimeoutExpired → SubprocessError 로 삼키고 에스컬레이트가 처리)."""
    cmd = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        cmd.append("/F")
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout,
                       **no_window_kwargs())
    except (OSError, subprocess.SubprocessError):
        pass


def _win_wait_dead(pid: int, timeout: float) -> bool:
    r"""대상 프로세스가 timeout 초 안에 종료되길 기다린다. 죽었으면 True.

    grace 동안 tasklist(is_alive)를 반복 폴링하면 호출당 수백 ms 라 비용이 크다.
    대신 ctypes `OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject` 로 **한 번에**
    대기한다(프로세스 핸들이 시그널되면=종료되면 즉시 깨어남). 핸들을 못 열면
    (이미 종료/접근 불가) 죽은 것으로 간주(True)."""
    try:
        import ctypes
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        OpenProcess = k.OpenProcess
        OpenProcess.restype = wintypes.HANDLE
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        h = OpenProcess(SYNCHRONIZE, False, pid)
        if not h:
            return True
        try:
            r = k.WaitForSingleObject(h, int(max(0.0, timeout) * 1000))
            return r != WAIT_TIMEOUT
        finally:
            k.CloseHandle(h)
    except Exception:
        # ctypes 경로 실패 시 보수적으로 is_alive 한 번만 확인.
        return not is_alive(pid)


def terminate(pid: int, *, force: bool = False, grace: float = 3.0) -> None:
    """프로세스(와 그 자식 트리)를 종료한다. 이미 없으면 조용히 무시.

    force=False 는 graceful(SIGTERM / taskkill), True 는 강제(SIGKILL / taskkill /F).

    Windows graceful 의 함정(#1.2): `taskkill /T`(/F 없음)는 대상의 **최상위 창에
    WM_CLOSE** 를 보낼 뿐이라, **창 없는 콘솔/분리(detached) 프로세스**(우리 서버
    데몬·콘솔 서브시스템 셸)는 그 신호를 받을 창이 없어 **종료되지 않는다**. 그러면
    트리가 그대로 남아 고아가 된다. 그래서 force=False 라도 graceful 시도 후 `grace`
    초 동안 생존을 확인하고, **아직 살아 있으면 `/F /T` 로 에스컬레이트**해 트리를
    확실히 내린다(force=True 는 처음부터 `/F /T`). POSIX 는 의미가 분명해(killpg
    SIGTERM/SIGKILL) 에스컬레이트 없이 그대로 둔다 — 호출부가 필요 시 SIGKILL 한다.
    """
    if pid <= 0:
        return
    if IS_WINDOWS:
        if force:
            _win_taskkill(pid, force=True)
            return
        # graceful 시도(짧은 timeout — 창 없는 대상에서 오래 블록 방지) → grace 동안
        # 종료를 한 번에 대기 → 안 죽었으면 /F /T 에스컬레이트.
        _win_taskkill(pid, force=False, timeout=2.0)
        if _win_wait_dead(pid, grace):
            return
        _win_taskkill(pid, force=True)  # 고아 방지 — 강제 트리 종료
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
