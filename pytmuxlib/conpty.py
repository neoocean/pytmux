"""직접 소유하는 ConPTY(의사 콘솔) 저수준 래퍼 — **raw 바이트** 입출력
(docs/IMPROVEMENT_OPPORTUNITIES.md §1.1②).

pywinpty(winpty-rs)의 ConPTY 백엔드는 `read()` 가 **str 전용**이고 내부에서 ReadFile
청크(≤32768B)마다 `MultiByteToWideChar` 로 **carry 없이** 디코드해, 멀티바이트(CJK/이모지)
가 청크 경계에 걸리면 우리가 바이트를 받기 전에 이미 U+FFFD 로 영구 손상한다(실측 재현).
서버 feed 경로(`pyte.ByteStream`)는 영속 incremental decoder 로 바이트 경계를 carry
하므로 **raw 바이트만 받으면 Unix PTY 와 동일하게 무손상**이다.

이 모듈은 ctypes 로 ConPTY API 를 직접 호출해 의사 콘솔을 소유하고, 입출력을 우리
파이프로 직접 읽고 쓴다(winpty-rs/pywinpty 우회). 따라서 read 가 raw bytes 를 돌려주고
(디코드 안 함), write 도 raw `WriteFile`(§1.1③ — 비-UTF-8 입력 무손상)이다. PseudoConsole
3종(Create/Resize/Close)은 **pywinpty 동봉 `conpty.dll` 의 `Conpty`-접두 export**
(ConptyCreatePseudoConsole 등)로 라우팅한다 — 이 OOB DLL 이 옆의 번들 `OpenConsole.exe` 를
호스트로 띄워, 시스템 conhost 와 달리 init 핸드셰이크(`\x1b[c`·`\x1b[?9001h`)를 내보낸다
(2026-06-11 실측: pywinpty 와 핸드셰이크 동일 = 호스트 패리티 달성). 번들 부재 시 시스템
conhost(평문 export)로 폴백.

⚠️ **현재 비동작 — 기본 백엔드는 pywinpty(`_WinPty`)다. owned 를 켜지 말 것**
(`PYTMUX_PTY_BACKEND=owned` 은 패널이 백지가 된다). 미해결 잔여 블로커는
§1.1②(docs/IMPROVEMENT_OPPORTUNITIES.md). 2026-06-11 정밀 이등분(probe `_probe_owned_*`,
detached=데몬 동일 조건, pywinpty 양성대조):

블로커는 **독립된 두 단계**로 갈렸다 — 하나는 해결, 하나는 미해결:
1. **자식 attach (해결됨, 단 미배선)**: 콘솔-less(DETACHED) 프로세스에서 `CreateProcessW`+
   `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` 가 자식을 의사콘솔에 **attach 하지 못한다**(자식
   stdout 이 부모의 nul std 핸들을 상속 → `GetConsoleScreenBufferInfo`=ERROR_INVALID_HANDLE,
   buf=0). MS EchoCon 표준 레시피도 동일 실패 = 문서화된 API 의 콘솔-less 한계(내 코드 버그
   아님). **숨은 콘솔**(`AllocConsole`+`CONOUT$/CONIN$` 재오픈+`SetStdHandle`)을 두면 자식이
   정상 attach(buf=100, pywinpty 와 동일)된다 — 이 한 줄이 핵심 발견. 단 (2)가 미해결이라
   제품에는 배선하지 않음(비동작 백엔드 위해 데몬에 콘솔 할당은 부당). bInheritHandles 는
   **FALSE** 여야 함(TRUE 면 자식이 부모 CONOUT$ 를 상속해 attach 를 덮어쓰고 filetype=0).
2. **conout VT-diff 스트리밍 정지 (미해결, 진짜 마지막 블로커)**: attach 후에도 OpenConsole 이
   init 핸드셰이크(~23B)만 우리 conout 에 쓰고 **이후 자식 출력을 흘리지 않는다**(자식은
   콘솔버퍼에 렌더됨 — resize 반영·buf 갱신 확인 — 되지만 VT diff 가 conout 으로 안 나옴 →
   자식이 back-pressure 로 블록). 파이프 종류(익명/명명)·resize 킥·DA 응답(`\x1b[?...c`) 모두
   무효. pywinpty(winpty-rs)는 같은 번들 OpenConsole 로 1.26MB 전량 스트리밍하므로 차이는
   winpty-rs 의 conout 처리 내부에 있고 공개 API 로 재현 못 함. ← 차기 공략 지점.

설계 메모(2026-06-11 실측, 전부 데몬과 동일한 detached 조건):
- **파이프 종류는 원인이 아니다**: 익명 `CreatePipe`(MS 공식 샘플)·overlapped 명명 파이프
  둘 다 위 (2) 동일(핸드셰이크 후 정지). 과거 "명명 파이프로 가면 해결" 가설은 틀렸다.
- **resize 킥**: spawn 끝의 크기 토글은 시스템 conhost 의 초기 페인트 1회는 유도하나, 번들
  OpenConsole 의 (2) 정지는 못 푼다. 반복 킥도 무효(자식 버퍼 크기만 바뀜).
- **호스트 패리티 달성**: 번들 OpenConsole 출력이 `\x1b[c`·`\x1b[?9001h`(win32-input-mode)로
  시작 = pywinpty 와 동일. 시스템 conhost 출력엔 그게 없다(평문 CreatePseudoConsole 위임).
- 핸들 타입을 엄격히(`c_void_p`) 지정해 64비트 핸들 절단(흔한 ctypes×x64 함정)을 막는다.
- read/write 는 overlapped(ERROR_IO_PENDING 후 `GetOverlappedResult(bWait=True)`), 전용
  스레드에서 블록(`pty_backend._OwnedConPty`). `close()` 가 `CancelIoEx`+`ClosePseudoConsole`
  로 in-flight read 를 깨우고 쓰기단을 닫아 EOF(0)로 빠져나온다 → 스레드 정상 종료.
- conin 배선 주의: `_make_overlapped_pipe` 반환은 (우리단, conhost단). 뒤집으면 conhost 가
  입력을 못 읽어 자식(cmd)이 콘솔입력 EOF 로 즉시 종료한다(ping 처럼 stdin 안 읽는 자식은
  생존).
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

# overlapped 명명 파이프(§1.1② — 익명 파이프 대체) 상수.
_PIPE_ACCESS_INBOUND = 0x00000001
_PIPE_ACCESS_OUTBOUND = 0x00000002
_FILE_FLAG_OVERLAPPED = 0x40000000
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_ERROR_IO_PENDING = 997
_ERROR_BROKEN_PIPE = 109
_ERROR_HANDLE_EOF = 38
_ERROR_OPERATION_ABORTED = 995
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# 명명 파이프 이름 충돌 방지용 프로세스-전역 카운터(난수/시각 의존 회피).
_pipe_seq = 0


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

    _ULONG_PTR = ctypes.c_size_t

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", _ULONG_PTR), ("InternalHigh", _ULONG_PTR),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", _HANDLE)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # ── 번들 OpenConsole 경로(§1.1② 진짜 해결) ────────────────────────────────
    # 시스템 conhost 의 CreatePseudoConsole 은 **콘솔-less 데몬**(서버=DETACHED_PROCESS)
    # 에서 자식의 지속 출력을 우리 파이프로 스트리밍하지 못한다(초기 페인트 1회뿐 —
    # 2026-06-11 실측). 반면 pywinpty(winpty-rs)는 같은 detached 조건에서 정상인데, 그
    # 차이는 winpty-rs 가 패키지에 **동봉한 MS ConPTY 재배포본 `conpty.dll`** 을 쓰고 그
    # CreatePseudoConsole 이 옆에 있는 **번들 `OpenConsole.exe`** 를 PTY host 로 띄우기
    # 때문이다(시스템 conhost 대신). 그래서 PseudoConsole 3종(Create/Resize/Close)을 이
    # 번들 DLL 로 라우팅하면 detached 스트리밍이 살아난다. HPCON 은 그 DLL 내부 상태라
    # Create/Resize/Close 는 반드시 **같은 DLL** 로 호출해야 한다(섞으면 안 됨).
    _dll_dir_cookie = None  # os.add_dll_directory 핸들(GC 되면 경로 제거 → 살려 둔다)

    def _load_bundled_conpty_dll():
        """pywinpty 동봉 conpty.dll 로드(번들 OpenConsole.exe 를 host 로 띄움). 실패=None."""
        global _dll_dir_cookie
        try:
            import winpty  # POSIX 부재 가능 — 예외는 아래서 흡수
            pkg = os.path.dirname(winpty.__file__)
            dll = os.path.join(pkg, "conpty.dll")
            if not (os.path.exists(dll) and
                    os.path.exists(os.path.join(pkg, "OpenConsole.exe"))):
                return None
            # 동봉 의존 DLL 탐색 경로 추가(cookie 를 살려 둬야 이후 로드까지 유효).
            try:
                _dll_dir_cookie = os.add_dll_directory(pkg)
            except (OSError, AttributeError):
                pass
            return ctypes.WinDLL(dll, use_last_error=True)
        except Exception:
            return None

    # PYTMUX_CONPTY_DLL=system 이면 강제로 시스템 conhost(A/B·디버그용). 기본은 번들 우선.
    _conpty_src_pref = (os.environ.get("PYTMUX_CONPTY_DLL") or "").strip().lower()
    _cpc = None if _conpty_src_pref == "system" else _load_bundled_conpty_dll()
    CONPTY_DLL_SOURCE = "bundled" if _cpc is not None else "system"
    if _cpc is None:
        _cpc = _k32  # 번들 부재 시 시스템 conhost 로 폴백(detached 스트리밍은 안 되지만 안전망)

    # 핸들/포인터는 전부 c_void_p 로 — 미지정 시 c_int(32비트)로 절단돼 어떤 핸들도
    # 무효가 되는 ctypes×x64 함정을 차단한다.
    _k32.CreatePipe.argtypes = [ctypes.POINTER(_HANDLE), ctypes.POINTER(_HANDLE),
                                _LPVOID, wintypes.DWORD]
    _k32.CreatePipe.restype = wintypes.BOOL
    # PseudoConsole 3종 함수 핸들 — Create/Resize/Close 동일 DLL 필수(HPCON 은 그 DLL
    # 내부 상태). 번들(OOB) DLL 은 **`Conpty`-접두 export**(ConptyCreatePseudoConsole 등)가
    # 진짜 OOB 구현이다 — 평문 `CreatePseudoConsole` 은 (실측) 시스템 conhost 로 위임돼
    # detached 에서 자식 attach 가 실패한다. winpty-rs 도 ConptyCreatePseudoConsole 을 쓴다.
    # 시스템 폴백(_cpc is _k32)일 땐 평문 이름만 있으므로 평문을 쓴다.
    if CONPTY_DLL_SOURCE == "bundled":
        _create_pc = _cpc.ConptyCreatePseudoConsole
        _resize_pc = _cpc.ConptyResizePseudoConsole
        _close_pc = _cpc.ConptyClosePseudoConsole
    else:
        _create_pc = _cpc.CreatePseudoConsole
        _resize_pc = _cpc.ResizePseudoConsole
        _close_pc = _cpc.ClosePseudoConsole
    _create_pc.argtypes = [_COORD, _HANDLE, _HANDLE, wintypes.DWORD,
                           ctypes.POINTER(_HPCON)]
    _create_pc.restype = ctypes.c_long  # HRESULT
    _resize_pc.argtypes = [_HPCON, _COORD]
    _resize_pc.restype = ctypes.c_long
    _close_pc.argtypes = [_HPCON]
    _close_pc.restype = None
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
    # overlapped 명명 파이프 I/O API.
    _k32.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, _LPVOID]
    _k32.CreateNamedPipeW.restype = _HANDLE
    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, _LPVOID,
        wintypes.DWORD, wintypes.DWORD, _HANDLE]
    _k32.CreateFileW.restype = _HANDLE
    _k32.CreateEventW.argtypes = [_LPVOID, wintypes.BOOL, wintypes.BOOL,
                                  wintypes.LPCWSTR]
    _k32.CreateEventW.restype = _HANDLE
    _k32.ResetEvent.argtypes = [_HANDLE]
    _k32.ResetEvent.restype = wintypes.BOOL
    _k32.GetOverlappedResult.argtypes = [
        _HANDLE, ctypes.POINTER(_OVERLAPPED), ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL]
    _k32.GetOverlappedResult.restype = wintypes.BOOL
    _k32.CancelIoEx.argtypes = [_HANDLE, ctypes.POINTER(_OVERLAPPED)]
    _k32.CancelIoEx.restype = wintypes.BOOL
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


def _make_overlapped_pipe(inbound: bool):
    """overlapped 명명 파이프 한 쌍을 만든다(§1.1② — 익명 CreatePipe 대체).

    익명 파이프는 `FILE_FLAG_OVERLAPPED` 를 못 받아 동기 ReadFile 만 가능한데, 콘솔-less
    데몬(서버가 DETACHED_PROCESS)에서 ConPTY(conhost) 출력이 그 동기 read 단에 도달하지
    않는다(실측 2026-06-11: cmd 배너·echo·CJK 출력 0바이트). winpty-rs/Windows Terminal
    처럼 **overlapped 명명 파이프**로 conout/conin 을 만들면 conhost 출력이 정상 도달한다.

    inbound=True  → conout: 우리(서버)가 *읽고* conhost 가 쓴다(PIPE_ACCESS_INBOUND).
    inbound=False → conin : 우리(서버)가 *쓰고* conhost 가 읽는다(PIPE_ACCESS_OUTBOUND).

    반환: (our_end, conhost_end). conhost_end 는 CreatePseudoConsole 에 넘긴 뒤 닫는다.
    our_end 는 overlapped. 같은 프로세스에서 CreateFile 로 상대단을 곧장 열어 연결하므로
    ConnectNamedPipe 가 필요 없다."""
    global _pipe_seq
    _pipe_seq += 1
    name = r"\\.\pipe\pytmux-conpty-%d-%d-%d" % (
        os.getpid(), _pipe_seq, 1 if inbound else 0)
    access = _PIPE_ACCESS_INBOUND if inbound else _PIPE_ACCESS_OUTBOUND
    # 서버단(우리): overlapped, 단일 인스턴스.
    srv = _k32.CreateNamedPipeW(
        name, access | _FILE_FLAG_OVERLAPPED | _FILE_FLAG_FIRST_PIPE_INSTANCE,
        _PIPE_TYPE_BYTE | _PIPE_WAIT, 1, 0, 0, 0, None)
    if not srv or srv == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    # conhost단(상대): 우리가 읽으면 conhost 는 GENERIC_WRITE, 반대면 GENERIC_READ.
    cli_access = _GENERIC_WRITE if inbound else _GENERIC_READ
    cli = _k32.CreateFileW(name, cli_access, 0, None, _OPEN_EXISTING, 0, None)
    if not cli or cli == _INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        _k32.CloseHandle(_HANDLE(srv))
        raise ctypes.WinError(err)
    return _HANDLE(srv), _HANDLE(cli)


class _ConPty:
    """ctypes 로 직접 소유하는 ConPTY. raw 바이트 read/write(overlapped 명명 파이프).

    수명: __init__(파이프+의사콘솔) → spawn(자식) → read()/write()/resize() →
    close(ClosePseudoConsole→자식 트리 hangup→핸들 해제).
    """

    def __init__(self, cols: int, rows: int):
        if not IS_WINDOWS:
            raise NotImplementedError("ConPTY 는 Windows 전용")
        self.pid = -1
        self._hpc = _HPCON()
        self._proc = _HANDLE()      # 자식 프로세스 핸들(wait/terminate)
        self._read = _HANDLE()      # 우리가 읽는 쪽(conhost 출력) — overlapped
        self._write = _HANDLE()     # 우리가 쓰는 쪽(conhost 입력) — overlapped
        self._read_evt = _HANDLE()  # overlapped read 완료 이벤트(수동 리셋)
        self._write_evt = _HANDLE()  # overlapped write 완료 이벤트(수동 리셋)
        self._closed = False
        self._exit: Optional[int] = None
        self._attrbuf = None        # 자식 spawn 후까지 유지(GC 방지)
        self._cmdbuf = None
        self._envbuf = None

        # conout(우리가 읽음) / conin(우리가 씀) overlapped 명명 파이프. conhost 측
        # 핸들은 CreatePseudoConsole 이 내부 복제하므로 직후 닫는다.
        # _make_overlapped_pipe 반환은 (우리단, conhost단) 순서다 — conout 은 우리가
        # 읽고 conin 은 우리가 쓰므로, 우리단을 self._read/_write 로, conhost단을
        # CreatePseudoConsole 의 hOutput/hInput 으로 넘긴다(이 순서를 뒤집으면 conhost 가
        # 입력을 못 읽어 자식이 콘솔입력 EOF 로 즉시 종료한다).
        h_read_out, h_pty_out = _make_overlapped_pipe(inbound=True)
        try:
            h_write_in, h_pty_in = _make_overlapped_pipe(inbound=False)
        except OSError:
            _k32.CloseHandle(h_read_out); _k32.CloseHandle(h_pty_out)
            raise
        hr = _create_pc(
            _COORD(max(1, cols), max(1, rows)), h_pty_in, h_pty_out, 0,
            ctypes.byref(self._hpc))
        # 의사콘솔이 복제한 conhost 측 핸들은 즉시 닫는다(우리 read/write 단만 보존).
        _k32.CloseHandle(h_pty_in); _k32.CloseHandle(h_pty_out)
        if hr != 0:
            _k32.CloseHandle(h_write_in); _k32.CloseHandle(h_read_out)
            raise OSError("CreatePseudoConsole failed: hr=0x%08x" % (hr & 0xFFFFFFFF))
        self._read = h_read_out
        self._write = h_write_in
        self._cols = max(1, cols)
        self._rows = max(1, rows)
        # 완료 이벤트(수동 리셋, 초기 비신호). read/write 가 재사용한다.
        self._read_evt = _HANDLE(_k32.CreateEventW(None, True, False, None))
        self._write_evt = _HANDLE(_k32.CreateEventW(None, True, False, None))

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
        # bInheritHandles=FALSE: 자식 std 핸들은 의사콘솔 attribute 가 의사콘솔로 연결한다.
        # TRUE 로 하면 자식이 부모의 std 핸들(데몬의 CONOUT$ 등)을 상속해 attach 를 덮어써
        # filetype=0 깨진 핸들이 된다(실측). MS EchoCon 표준 레시피도 FALSE.
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
        # conhost 킥(필수): conhost 는 spawn 직후 자체적으로는 출력을 내보내지 않고
        # **첫 ResizePseudoConsole 신호를 받아야** 화면 페인트·이후 출력 diff 를 우리
        # 출력 파이프로 흘린다(실측 2026-06-11: 킥 없으면 anon/named 모두 conout 0바이트,
        # 자식은 살아있음). winpty-rs/Windows Terminal 도 attach 직후 초기 resize 를 보낸다.
        # 같은 크기 resize 는 무시될 수 있어 1행 줄였다가 본래 크기로 토글해 변경을 만든다.
        try:
            _resize_pc(self._hpc,
                       _COORD(self._cols, max(1, self._rows - 1)))
            _resize_pc(self._hpc, _COORD(self._cols, self._rows))
        except OSError:
            pass
        return self.pid

    def read(self, maxlen: int = 65536) -> bytes:
        """블로킹 read(overlapped). 자식/콘솔이 닫히면 b"" (EOF). raw 바이트(디코드 안 함).

        overlapped 명명 파이프라 ReadFile 가 즉시 완료(동기)되거나 ERROR_IO_PENDING 후
        GetOverlappedResult(bWait=True)로 블록한다. close() 의 CancelIoEx/핸들 드롭이
        대기를 ERROR_OPERATION_ABORTED/BROKEN_PIPE 로 깨워 b"" 로 빠져나온다."""
        if self._closed or not self._read:
            return b""
        buf = (ctypes.c_byte * maxlen)()
        n = wintypes.DWORD(0)
        ov = _OVERLAPPED()
        ov.hEvent = self._read_evt
        _k32.ResetEvent(self._read_evt)
        ok = _k32.ReadFile(self._read, buf, maxlen, ctypes.byref(n), ctypes.byref(ov))
        if not ok:
            err = ctypes.get_last_error()
            if err == _ERROR_IO_PENDING:
                if not _k32.GetOverlappedResult(
                        self._read, ctypes.byref(ov), ctypes.byref(n), True):
                    return b""      # ABORTED/BROKEN_PIPE/EOF → 종료
            else:
                return b""          # BROKEN_PIPE/HANDLE_EOF/기타 → 종료
        if n.value == 0:
            return b""
        return bytes(buf[:n.value])

    def write(self, data: bytes) -> int:
        """raw 바이트 write(overlapped, §1.1③ — 디코드/재인코드 없음). 쓴 바이트 수 반환.

        overlapped 라 대량 paste 도 블로킹하지 않고 커널이 펌프한다(익명 파이프의 write
        오버랩 불가 제약 해소). 호출자 관점은 동기(완료까지 대기)."""
        if self._closed or not self._write or not data:
            return 0
        n = wintypes.DWORD(0)
        ov = _OVERLAPPED()
        ov.hEvent = self._write_evt
        _k32.ResetEvent(self._write_evt)
        cbuf = (ctypes.c_byte * len(data)).from_buffer_copy(data)
        ok = _k32.WriteFile(self._write, cbuf, len(data), ctypes.byref(n),
                            ctypes.byref(ov))
        if not ok:
            if ctypes.get_last_error() != _ERROR_IO_PENDING:
                return 0
            if not _k32.GetOverlappedResult(
                    self._write, ctypes.byref(ov), ctypes.byref(n), True):
                return 0
        return int(n.value)

    def resize(self, cols: int, rows: int) -> None:
        if self._closed or not self._hpc:
            return
        _resize_pc(self._hpc, _COORD(max(1, cols), max(1, rows)))

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

        overlapped read 가 GetOverlappedResult 로 대기 중일 수 있으므로 먼저 CancelIoEx 로
        in-flight I/O 를 취소(ERROR_OPERATION_ABORTED 로 깨움)하고, ClosePseudoConsole 로
        conhost 를 내려 쓰기단이 닫히게 한 뒤 핸들·이벤트를 해제한다 → 리더 스레드가 b""(EOF)
        로 자연 종료."""
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
        # in-flight overlapped read/write 를 취소해 대기 중인 스레드를 깨운다.
        for h_attr in ("_read", "_write"):
            h = getattr(self, h_attr)
            if h:
                try:
                    _k32.CancelIoEx(h, None)
                except OSError:
                    pass
        if self._hpc:
            try:
                _close_pc(self._hpc)
            except OSError:
                pass
            self._hpc = _HPCON()
        for h_attr in ("_write", "_read", "_proc", "_read_evt", "_write_evt"):
            h = getattr(self, h_attr)
            if h:
                try:
                    _k32.CloseHandle(h)
                except OSError:
                    pass
                setattr(self, h_attr, _HANDLE())
