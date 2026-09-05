"""크로스플랫폼 프로세스/데몬화 추상층 (docs/internal/WINDOWS_PORT.md §6-1 ③).

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
           "open_in_file_manager", "process_cwd", "long_path",
           "foreground_command", "tree_command_names"]


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
                   env: Optional[dict] = None,
                   stderr_path: Optional[str] = None) -> int:
    """부모 생애와 무관하게 살아남는 분리 프로세스를 띄우고 pid 를 돌려준다.

    표준 입출력은 모두 devnull 로 돌린다(데몬). close_fds 로 상속 fd 누수를 막는다.

    stderr_path: 주면 자식 stderr 를 devnull 대신 **그 파일**로 돌린다(매 기동마다
    truncate). 데몬은 stderr 가 /dev/null 이라 **부팅 중 죽으면 이유가 통째로
    사라졌다** — 원격 stdio-proxy 의 자동 기동이 실패했을 때 사용자가 본 것은
    '인증 대기 시한 초과' 뿐이고 진짜 원인(ModuleNotFoundError 등)은 어디에도 남지
    않았다(2026-07-28 원격 탭 실패 실측). 서버가 자기 로그를 열기 **전** 단계의
    실패(import·구문·권한)를 잡는 유일한 지점이라 파일로 남긴다. 파일을 못 열면
    조용히 devnull 로 폴백한다(진단이 기동을 막으면 안 된다).
    """
    devnull = subprocess.DEVNULL
    errf = None
    if stderr_path:
        try:
            errf = open(stderr_path, "wb", buffering=0)
        except OSError:
            errf = None
    kwargs: dict = dict(cwd=cwd, env=env, stdin=devnull, stdout=devnull,
                        stderr=(errf or devnull), close_fds=True)
    if IS_WINDOWS:
        kwargs["creationflags"] = (
            _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW)
    else:
        # setsid: 새 세션/프로세스그룹의 리더가 되어 컨트롤링 터미널에서 분리되고,
        # 종료 시 그룹 전체(자식 셸 포함)를 killpg 로 한 번에 정리할 수 있다.
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, **kwargs)
    finally:
        if errf is not None:       # 자식이 dup 해 갔으니 부모 쪽 fd 는 닫는다
            errf.close()
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


def command_line(pid: int) -> Optional[str]:
    """그 pid 를 띄운 **명령줄 전체**(못 읽으면 None).

    「pid 하나만 겨냥한다」는 규율(§ CLAUDE.md)의 남은 구멍을 메우려고 낸다 — 겨냥이
    좁아도 **그 번호가 내가 생각한 그 프로세스인지**는 아무도 안 봤다(검수 2026-09-05
    S-2). pid 파일이 크래시·재부팅으로 남은 뒤 그 번호가 재사용되면, 「살아 있다」만
    보고 쏘는 코드는 **같은 사용자의 무관한 프로세스**를 죽인다.

    ⛔ **모르면 None 이다** — 「아마 우리 것」으로 접지 않는다. 부르는 쪽은 None 을
    「확인 못 했다」로 받아 **안 죽이는 쪽**으로 진다.

    - POSIX: `ps -p <pid> -o args=` (macOS·Linux 공통 · 실측 10ms 안).
    - Windows: exe 이름만으로는 못 가른다(서버는 `pythonw.exe` 다 — 우리 것이라는
      표시는 **인자**에 있다). CIM 으로 명령줄을 읽는다. 느리므로(수백 ms) 부르는
      쪽이 executor 로 돌린다.
    """
    if pid <= 0:
        return None
    import shutil
    if IS_WINDOWS:
        exe = shutil.which("powershell")
        if not exe:
            return None
        ps = ("(Get-CimInstance Win32_Process -Filter 'ProcessId=%d')"
              ".CommandLine" % pid)
        argv = [exe, "-NoProfile", "-NonInteractive", "-Command", ps]
    else:
        exe = shutil.which("ps")
        if not exe:
            return None
        argv = [exe, "-p", str(pid), "-o", "args="]
    try:
        out = subprocess.run(argv, capture_output=True, timeout=10.0,
                             **no_window_kwargs())
    except Exception:
        return None
    if out.returncode != 0:
        return None
    text = (out.stdout or b"").decode("utf-8", "replace").strip()
    return text or None


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


# GetLongPathNameW 프로토타입 지연 캐시(미로드=None, 로드실패=False) — _libproc 과 동일 관례.
_getlongpath = None


def long_path(path: Optional[str]) -> Optional[str]:
    r"""경로를 **온디스크 이름**으로 편다 — Windows 8.3 단축 표기(`WOOJIN~1`)를 긴
    이름(`woojinkim`)으로. 그 밖의 OS 에선 항등(단축 이름이라는 개념이 없다).

    왜 필요한가: Windows 는 프로세스의 cwd 를 PEB 에 **넣어 준 문자열 그대로** 들고
    있다. 셸을 단축 경로로 띄웠거나(에이전트 셸의 `TMP=C:\Users\WOOJIN~1\...` 같은 환경)
    사용자가 단축 이름으로 `cd` 했으면 그 표기가 그대로 나온다. 그런데 그 값을 쓰는
    쪽(ncd 트리·mdir·default-path=current)은 그것을 `os.scandir` 의 **긴 이름**과
    맞춰야 하고, `normcase` 는 대소문자만 흡수하므로 `WOOJIN~1` != `woojinkim` 에서
    사슬이 끊긴다 — 증상은 ncd 가 현재 자리를 못 찾아 **드라이브 목록으로 떨어지는**
    것이다(pytmux-237·-436..441).

    ⛔ `os.path.realpath` 로 대신하지 말 것: 심링크·junction 을 따라가고 **매핑 드라이브를
    UNC 로 바꾼다**(이 상자 실측 `R:\` -> `\mxfs\DATA_RX`). 그러면 ncd 의 드라이브 루트
    매칭이 도리어 깨진다. `GetLongPathNameW` 는 단축 성분만 펴고 나머지는 안 건드린다
    (실측: `R:\` -> `R:\` 그대로).

    실패는 전부 **입력을 그대로** 돌린다 — 없는 경로·끊긴 네트워크 드라이브(실측 `Z:\`
    는 rc 0)·API 오류. 이 함수는 표기를 다듬는 것이지 존재를 판정하는 것이 아니다."""
    global _getlongpath
    if not path or not IS_WINDOWS:
        return path
    if _getlongpath is None:
        try:
            import ctypes
            from ctypes import wintypes
            fn = ctypes.WinDLL("kernel32", use_last_error=True).GetLongPathNameW
            fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            fn.restype = wintypes.DWORD
            _getlongpath = fn
        except Exception:
            _getlongpath = False
    if not _getlongpath:
        return path
    try:
        import ctypes
        need = _getlongpath(path, None, 0)      # 필요한 버퍼 크기(NUL 포함)
        if not need:
            return path
        buf = ctypes.create_unicode_buffer(need)
        if not _getlongpath(path, buf, need):
            return path
        return buf.value or path
    except Exception:
        return path


def process_cwd(pid: int) -> Optional[str]:
    """대상 프로세스(패널 셸)의 현재 작업 디렉토리(cwd)를 추정한다. 실패 시 None.

    Windows 는 `/proc`·`lsof` 가 없으므로 ctypes 로 대상 프로세스의 PEB →
    RTL_USER_PROCESS_PARAMETERS.CurrentDirectory(UNICODE_STRING)를 직접 읽는다
    (psutil 등 외부 의존 없이). **macOS/BSD** 는 libproc `proc_pidinfo` 를 쓴다
    (아래 `_mac_process_cwd`). Linux 는 `/proc/<pid>/cwd` 가 더 단순해 호출부
    (servertree `_pane_cwd`)에 맡기고 None 을 돌린다. ncd(현재 디렉토리 강조)·
    default-path=current 가 이 cwd 에 의존한다."""
    if pid <= 0:
        return None
    if IS_WINDOWS:
        return _win_process_cwd(pid)
    if sys.platform == "darwin":
        return _mac_process_cwd(pid)
    return None


# libproc 상수/구조체(macOS). proc_pidinfo(pid, PROC_PIDVNODEPATHINFO, 0, &vpi, sz)
# 가 sz 를 그대로 돌려주면 성공이다.
_PROC_PIDVNODEPATHINFO = 9
_MAXPATHLEN = 1024
_VNODE_INFO_PAD = 152          # struct vnode_info(vinfo_stat+fsid+vid) 크기
_libproc = None                 # 지연 로드 캐시(미로드=None, 로드실패=False)


def _mac_process_cwd(pid: int) -> Optional[str]:
    """macOS 전용: libproc 로 대상 프로세스의 cwd 를 **시스템콜 한 번**에 읽는다.

    종전엔 `_pane_cwd` 가 `lsof -a -p PID -d cwd -Fn` 서브프로세스를 띄웠는데, 이게
    **단일 스레드 asyncio 루프 위에서 동기로** 돌았다(split/new-window/popup/respawn
    은 전부 sync `def` 라 await 자체가 불가). 실측 **중앙값 321ms**(상한=timeout 2s)
    동안 서버 전체가 정지 — 전 클라 프레임·입력·ping 이 멈추고 _liveness_loop 가
    굶은 클라를 죽은 것으로 오해할 수 있었다(코드검수 2026-07-17, blocking-on-loop
    4회차). libproc 는 **1µs** 라 Linux(/proc readlink)·Windows(PEB) 와 같은 등급의
    "동기지만 무시할 만한" 경로가 되어, 호출부 시그니처를 바꾸지 않고 원인을 없앤다.

    실패(권한·레이아웃 차이·미지원)는 전부 None → 호출부의 lsof 폴백이 받는다."""
    global _libproc
    import ctypes                     # 지연 import(_win_process_cwd 와 동일 관례)
    if _libproc is None:
        try:
            import ctypes.util
            path = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
            _libproc = ctypes.CDLL(path, use_errno=True)
        except OSError:
            _libproc = False
    if not _libproc:
        return None

    class _VnodeInfo(ctypes.Structure):
        _fields_ = [("pad", ctypes.c_byte * _VNODE_INFO_PAD)]

    class _VnodeInfoPath(ctypes.Structure):
        _fields_ = [("vip_vi", _VnodeInfo),
                    ("vip_path", ctypes.c_char * _MAXPATHLEN)]

    class _ProcVnodePathInfo(ctypes.Structure):
        _fields_ = [("pvi_cdir", _VnodeInfoPath), ("pvi_rdir", _VnodeInfoPath)]

    try:
        vpi = _ProcVnodePathInfo()
        n = _libproc.proc_pidinfo(pid, _PROC_PIDVNODEPATHINFO, 0,
                                  ctypes.byref(vpi), ctypes.sizeof(vpi))
        # 부분 채움/오류를 신뢰하지 않는다 — 커널이 정확히 sizeof 를 채웠을 때만 유효.
        if n != ctypes.sizeof(vpi):
            return None
        return vpi.pvi_cdir.vip_path.decode("utf-8", "replace") or None
    except (OSError, ValueError, AttributeError):
        return None


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
            # cmd.exe 는 끝에 `\` 가 붙는 경우가 있다(루트 제외하고 정규화). 그리고
            # PEB 는 **넣어 준 표기 그대로**라 8.3 단축일 수 있어 온디스크 이름으로 편다
            # (`long_path` 의 주석 — 안 펴면 ncd 사슬이 scandir 이름과 안 맞는다).
            return long_path(os.path.normpath(path)) if path else None
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


def _win_proc_table():
    """Windows: ToolHelp 스냅샷 1회로 `(exe_of, children)` 전체 프로세스 표를 만든다.

    CreateToolhelp32Snapshot(SNAPPROCESS) -> Process32FirstW/NextW 로 (pid, ppid, exe)
    를 모은다. 실패하면 None — 호출부는 graceful 폴백한다. `_win_foreground_command`
    (최심 자손)과 `tree_command_names`(자손 전체)가 같은 표를 쓴다."""
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
        return exe_of, children
    except Exception:
        return None


def _descendants(pid: int, children: dict) -> list:
    """`children`(ppid→[pid]) 표에서 pid 의 자손 pid 목록(자기 제외, BFS).
    ppid 재사용/사이클은 방문 집합으로 차단한다."""
    out, seen, frontier = [], {pid}, [pid]
    while frontier:
        cur = frontier.pop()
        for ch in children.get(cur, ()):
            if ch not in seen:
                seen.add(ch)
                out.append(ch)
                frontier.append(ch)
    return out


def tree_command_names(pid: int) -> Optional[list]:
    """pid 의 **자손** 프로세스 명령 문자열 목록(소문자). 구하지 못하면 None(판단 불가).

    쓰임: "이 패널 안에서 그 프로그램이 아직 도는가" 를 포그라운드 **하나**가 아니라
    트리 **전체**로 묻는 자리. Claude Code 가 `!`(bash 모드)로 셸을 띄우면 포그라운드
    (Windows 는 최심 자손)는 그 셸이지만 Claude 는 여전히 살아 있다 — 그 구분에 쓴다
    (claude-code `_claude_really_exited`, 제보 2026-07-29).

    POSIX: `ps -Ao pid=,ppid=,command=` 한 번으로 전체 표를 받아 BFS. **명령행 전체**라
    node 로 실행된 CLI(`node …/claude/cli.js`)도 잡힌다.
    Windows: ToolHelp 스냅샷의 exe 이름만(명령행은 못 얻는다) — best-effort.
    자손이 없으면 빈 목록(= 확실히 없음), 조회 실패는 None(= 모름)으로 구분한다."""
    if pid is None or pid <= 0:
        return None
    if IS_WINDOWS:
        table = _win_proc_table()
        if table is None:
            return None
        exe_of, children = table
        if pid not in exe_of:
            return None
        return [str(exe_of.get(c, "")).lower()
                for c in _descendants(pid, children)]
    try:
        out = subprocess.run(["ps", "-Ao", "pid=,ppid=,command="],
                             capture_output=True, text=True, timeout=5,
                             **no_window_kwargs()).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    cmd_of: dict = {}
    children: dict = {}
    for line in out.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) < 2:
            continue
        try:
            cpid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        cmd_of[cpid] = (parts[2] if len(parts) > 2 else "").lower()
        children.setdefault(ppid, []).append(cpid)
    if pid not in cmd_of:
        return None
    return [cmd_of.get(c, "") for c in _descendants(pid, children)]


def _win_foreground_command(pid: int) -> Optional[str]:
    r"""Windows: ToolHelp 스냅샷으로 pid 의 가장 깊은 자손 프로세스 exe 이름을 구한다.

    (pid, ppid, exe) 표(`_win_proc_table`)에서 pid 부터 BFS 로 가장 깊은 자손을 고른다
    (여러 leaf 면 깊이 최댓값). 자손이 없으면 셸 자신의 exe. exe 는 basename 에서
    `.exe`(대소문자 무시) 제거. 어떤 실패든 None 으로 graceful — 자동 이름은 고정
    탭이름으로 폴백된다."""
    try:
        table = _win_proc_table()
        if table is None:
            return None
        exe_of, children = table
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
