"""직접 소유하는 ConPTY(의사 콘솔) 저수준 래퍼 — **raw 바이트** 입출력
(docs/internal/IMPROVEMENT_OPPORTUNITIES.md §1.1②).

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

**2026-06-12: §1.1② 돌파 레시피 배선됨**(opt-in `PYTMUX_PTY_BACKEND=owned`, 라이브 검증 대기).
winpty-rs(`andfoy/winpty-rs` `pty_impl.rs`)를 detached(콘솔-less = 데몬 동일) 조건에서 충실히
복제한 probe 로, 직접 소유 ConPTY 가 **대화형 cmd.exe 출력을 완전 스트리밍**(배너+프롬프트+
`dir` 결과, U+FFFD 0)함을 실증했다. 과거 "attach 돼도 23B 핸드셰이크만 흐른다"는 단정은
오류였고, 실패 원인은 **overlapped+0버퍼 파이프 + 숨은 콘솔(AllocConsole/SetStdHandle) 누락**
이었다. 본 모듈은 그 레시피를 그대로 배선한다:

  ① **숨은 콘솔**: 콘솔-less 데몬은 `CreateProcessW`+PSEUDOCONSOLE attribute 만으로는 자식을
     attach 시키지 못한다(자식 stdout 이 부모 NUL std 핸들 상속 → `GetConsoleScreenBufferInfo`
     실패, buf=0; MS EchoCon 표준 레시피도 동일 = 문서화된 콘솔-less 한계). 프로세스당 1회
     `AllocConsole`(창은 `SW_HIDE`) + `CONOUT$`/`CONIN$` 재오픈 + `SetStdHandle`(STD_OUTPUT·
     STD_ERROR·STD_INPUT — ERROR 도 포함)을 해 두면 자식이 정상 attach(buf=100, pywinpty 와
     동일)된다. `_ensure_hidden_console()` 가 담당(idempotent, 스레드 안전).
  ② **동기 파이프, 128KB 버퍼**: overlapped 가 아니라 **동기(non-overlapped) 명명 파이프**를
     128KB 버퍼로 만든다(`_make_sync_pipe`). 명명 파이프 토폴로지는 conout 도달이 실증됨(익명
     파이프는 이 빌드에서 미도달) — 스트리밍을 막던 건 **overlapped+0버퍼**라 그 둘만 제거.
  ③ **블로킹 ReadFile**: 전용 리더 스레드가 동기 블로킹 ReadFile 로 raw 바이트를 읽는다
     (`pty_backend._OwnedConPty`). write 도 동기 블로킹 WriteFile(§1.1③ — 비-UTF-8 무손상).
     `close()` 의 `ClosePseudoConsole` 가 conhost 쓰기단을 닫아 read 가 EOF(0)/BROKEN_PIPE 로
     빠져나오고, `CancelIoEx` 가 in-flight read 를 깨운다 → 스레드 정상 종료.

서버 feed 경로(`pyte.ByteStream`)는 영속 incremental decoder 로 바이트 경계를 carry 하므로
**raw 바이트만 받으면 Unix PTY 와 동일하게 무손상**이다(read 가 디코드 안 함 = 손상 원점 회피).
PseudoConsole 3종(Create/Resize/Close)은 pywinpty 동봉 `conpty.dll` 의 `Conpty`-접두 export
로 라우팅한다(번들 OpenConsole 호스트 = 시스템 conhost 와 핸드셰이크 패리티). 번들 부재 시
시스템 conhost 폴백.

**잔여 갭(라이브 검증으로 판정)**: 비대화형 raw-writer 자식(`python -c "stdout.write × N"`)은
레시피를 전부 복제해도 23B 스톨(자식은 완벽 attach 하나 conhost 가 VT diff 를 emit 안 함;
pywinpty 는 같은 자식을 스트리밍 = 양성대조). 단 패널이 실제 돌리는 cmd/pwsh/**Claude** 는
콘솔 API 로 렌더하는 대화형이라 cmd 처럼 스트리밍될 것으로 본다 → run-pytmux 드라이버로 실
Claude 패널 무손상 스트리밍 검증이 §1.1② 해결 판정의 결정타. 상세: docs/internal/HANDOFF.md §9,
메모리 `pytmux-1-1-multibyte-winpty-corruption`.

설계 메모(전부 데몬과 동일한 detached 조건 실측):
- **호스트 패리티**: 번들 OpenConsole 출력이 `\x1b[c`·`\x1b[?9001h`(win32-input-mode)로 시작
  = pywinpty 와 동일. 시스템 conhost 출력엔 그게 없다(평문 CreatePseudoConsole 위임).
- 핸들 타입을 엄격히(`c_void_p`) 지정해 64비트 핸들 절단(흔한 ctypes×x64 함정)을 막는다.
- conin 배선 주의: `_make_sync_pipe` 반환은 (우리단, conhost단). 뒤집으면 conhost 가 입력을
  못 읽어 자식(cmd)이 콘솔입력 EOF 로 즉시 종료한다(ping 처럼 stdin 안 읽는 자식은 생존).
- bInheritHandles 는 **FALSE** 여야 함(TRUE 면 자식이 부모 CONOUT$ 를 상속해 attach 를
  덮어쓰고 filetype=0). 숨은 콘솔(①)과 무관하게 자식 std 핸들은 PSEUDOCONSOLE attribute 가
  의사콘솔로 연결한다.
- ⚠️ `SetStdHandle`(①)은 데몬 프로세스 전역 std 핸들을 숨은 콘솔로 돌린다. 데몬이 stderr 로
  로깅하면 그 출력이 숨은 콘솔로 가 안 보일 수 있다(owned 는 opt-in·실험적이라 용인).
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import threading
from ctypes import wintypes
from typing import Optional

# 이 모듈은 Windows 전용. import 자체는 POSIX 에서도 안전하도록(상수 정의만) 하되,
# 실제 호출 진입점에서 IS_WINDOWS 를 가정한다.
IS_WINDOWS = os.name == "nt"

STILL_ACTIVE = 259
_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400

# 동기(non-overlapped) 명명 파이프 상수(§1.1② 돌파 레시피 ②). overlapped 를 빼고 128KB
# 버퍼를 준다 — overlapped+0버퍼가 detached 스트리밍을 막던 원인이었다.
_PIPE_ACCESS_INBOUND = 0x00000001
_PIPE_ACCESS_OUTBOUND = 0x00000002
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_ERROR_BROKEN_PIPE = 109
_ERROR_HANDLE_EOF = 38
_ERROR_OPERATION_ABORTED = 995
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
# 파이프 버퍼 크기(레시피 ②). winpty-rs 와 동일한 128KB.
_PIPE_BUF = 128 * 1024

# 숨은 콘솔 setup 용 상수(레시피 ①). 표준 핸들 ID 는 음수라 DWORD(unsigned)로 표기.
_STD_INPUT_HANDLE = 0xFFFFFFF6   # -10
_STD_OUTPUT_HANDLE = 0xFFFFFFF5  # -11
_STD_ERROR_HANDLE = 0xFFFFFFF4   # -12
_SW_HIDE = 0

# 명명 파이프 이름 충돌 방지용 프로세스-전역 카운터(난수/시각 의존 회피). 워밍 풀
# (아래)이 백그라운드 스레드에서 _ConPty 를 만들면 전경 spawn 과 _make_sync_pipe 가
# 동시 진입할 수 있어, seq 증가를 락으로 보호한다(미보호 시 같은 seq→같은 파이프 이름→
# CreateNamedPipeW FIRST_PIPE_INSTANCE 충돌). 종전엔 spawn 이 이벤트 루프 단일 스레드라
# 경쟁이 없었다.
_pipe_seq = 0
_pipe_seq_lock = threading.Lock()

# 숨은 콘솔(레시피 ①)은 프로세스당 1회만 셋업한다 — 여러 패널이 동시에 spawn 해도 idempotent.
_console_lock = threading.Lock()
_console_ready = False

# ── ConPTY 워밍 풀(docs/WINDOWS_STARTUP_PERF §6A) ──────────────────────────────
# 새 탭 spawn 비용(~165–190ms)의 절반은 CreatePseudoConsole 이 번들 OpenConsole.exe
# 호스트 프로세스를 띄우는 ~90–130ms 다(셸 attach=CreateProcessW 는 ~75ms). 이 호스트
# 생성은 **cwd/argv 무관**이라(셸 attach 때 cwd·argv 를 준다) 미리 만들어 둘 수 있다.
# 풀은 셸을 아직 attach 하지 않은 _ConPty(의사콘솔만 준비)를 들고 있다가, 새 패널
# 요청 시 그것을 꺼내 resize→spawn(cwd) 한다 → 새 탭 체감 ~90ms 로 절반. 적중 후
# 백그라운드 스레드가 풀을 다시 채운다. 프로덕션(run_server)에서만 켠다 — 테스트는
# 실제 의사콘솔을 백그라운드로 만들지 않게 끈 채 둔다(_prewarm_session 과 동일 패턴).
_POOL_TARGET = 1
_pool: list = []
_pool_lock = threading.Lock()
_pool_dims = (120, 30)     # 미리 만들 때 크기(채택 시 요청 크기로 resize)
_pool_enabled = False
_pool_filling = False      # 리필 스레드 중복 기동 가드


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
    _k32.CancelIoEx.argtypes = [_HANDLE, ctypes.POINTER(_OVERLAPPED)]
    _k32.CancelIoEx.restype = wintypes.BOOL
    # 숨은 콘솔 setup(레시피 ①) — AllocConsole/CONOUT$·CONIN$ 재오픈/SetStdHandle.
    _k32.AllocConsole.argtypes = []
    _k32.AllocConsole.restype = wintypes.BOOL
    _k32.GetConsoleWindow.argtypes = []
    _k32.GetConsoleWindow.restype = _HANDLE
    _k32.GetStdHandle.argtypes = [wintypes.DWORD]
    _k32.GetStdHandle.restype = _HANDLE
    _k32.SetStdHandle.argtypes = [wintypes.DWORD, _HANDLE]
    _k32.SetStdHandle.restype = wintypes.BOOL
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _user32.ShowWindow.argtypes = [_HANDLE, ctypes.c_int]
        _user32.ShowWindow.restype = wintypes.BOOL
    except OSError:
        _user32 = None
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


def _ensure_hidden_console() -> None:
    """프로세스에 숨은 콘솔을 1회 셋업한다(§1.1② 돌파 레시피 ①).

    콘솔-less 데몬(서버=DETACHED_PROCESS)은 `CreateProcessW`+PSEUDOCONSOLE attribute 만으로는
    자식을 의사콘솔에 attach 시키지 못한다(자식 stdout 이 부모 NUL std 핸들 상속 →
    `GetConsoleScreenBufferInfo` 실패). `AllocConsole`(창은 SW_HIDE)로 콘솔을 붙이고
    `CONOUT$`/`CONIN$` 를 재오픈해 STD_OUTPUT·STD_ERROR·STD_INPUT 표준 핸들로 걸어 두면 자식이
    정상 attach(buf=100, pywinpty 와 동일)한다 — 이 셋업이 핵심 발견(2026-06-12).

    idempotent·스레드 안전(여러 패널 동시 spawn 안전). 이미 콘솔이 있으면 AllocConsole 은
    건너뛰고 표준 핸들만 (재)연결한다. 전부 best-effort — 실패해도 spawn 은 시도한다."""
    global _console_ready
    if _console_ready or not IS_WINDOWS:
        return
    with _console_lock:
        if _console_ready:
            return
        try:
            # 콘솔이 아직 없으면 새로 할당하고 창을 숨긴다(데몬은 GetConsoleWindow()=0).
            if not _k32.GetConsoleWindow():
                if _k32.AllocConsole() and _user32 is not None:
                    hwnd = _k32.GetConsoleWindow()
                    if hwnd:
                        try:
                            _user32.ShowWindow(_HANDLE(hwnd), _SW_HIDE)
                        except OSError:
                            pass
            # CONOUT$/CONIN$ 를 재오픈해 표준 핸들로 건다(레시피상 ERROR 도 OUT 과 동일 핸들).
            share = _FILE_SHARE_READ | _FILE_SHARE_WRITE
            access = _GENERIC_READ | _GENERIC_WRITE
            h_out = _k32.CreateFileW("CONOUT$", access, share, None,
                                     _OPEN_EXISTING, 0, None)
            if h_out and h_out != _INVALID_HANDLE_VALUE:
                _k32.SetStdHandle(_STD_OUTPUT_HANDLE, _HANDLE(h_out))
                _k32.SetStdHandle(_STD_ERROR_HANDLE, _HANDLE(h_out))
            h_in = _k32.CreateFileW("CONIN$", access, share, None,
                                    _OPEN_EXISTING, 0, None)
            if h_in and h_in != _INVALID_HANDLE_VALUE:
                _k32.SetStdHandle(_STD_INPUT_HANDLE, _HANDLE(h_in))
        except OSError:
            pass
        _console_ready = True


def _make_sync_pipe(inbound: bool):
    """동기(non-overlapped) 명명 파이프 한 쌍(§1.1② 돌파 레시피 ②). 128KB 버퍼.

    overlapped+0버퍼 명명 파이프는 콘솔-less 데몬에서 ConPTY(번들 OpenConsole) 출력이 우리
    read 단에 스트리밍되지 않았다(2026-06-12 실측: 초기 핸드셰이크 뒤 정지). overlapped 를
    빼고 128KB 버퍼를 주면 대화형 cmd 가 완전 스트리밍된다(=레시피 ②). 명명 파이프 토폴로지
    자체는 conout 도달이 실증돼 유지한다(익명 CreatePipe 는 이 빌드에서 read 단 미도달).

    inbound=True  → conout: 우리(서버)가 *읽고* conhost 가 쓴다(PIPE_ACCESS_INBOUND).
    inbound=False → conin : 우리(서버)가 *쓰고* conhost 가 읽는다(PIPE_ACCESS_OUTBOUND).

    반환: (our_end, conhost_end). conhost_end 는 CreatePseudoConsole 에 넘긴 뒤 닫는다.
    같은 프로세스에서 CreateFile 로 상대단을 곧장 열어 연결하므로 ConnectNamedPipe 불필요."""
    global _pipe_seq
    with _pipe_seq_lock:        # 워밍 풀 백그라운드 생성과 전경 spawn 의 seq 경쟁 차단
        _pipe_seq += 1
        seq = _pipe_seq
    name = r"\\.\pipe\pytmux-conpty-%d-%d-%d" % (
        os.getpid(), seq, 1 if inbound else 0)
    access = _PIPE_ACCESS_INBOUND if inbound else _PIPE_ACCESS_OUTBOUND
    # 서버단(우리): 동기(블로킹), 단일 인스턴스, 128KB 입출력 버퍼.
    srv = _k32.CreateNamedPipeW(
        name, access | _FILE_FLAG_FIRST_PIPE_INSTANCE,
        _PIPE_TYPE_BYTE | _PIPE_WAIT, 1, _PIPE_BUF, _PIPE_BUF, 0, None)
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


def enable_pool() -> None:
    """워밍 풀을 켜고 한 칸 미리 채운다(프로덕션 데몬 run_server 에서만 호출).

    Windows·owned-ConPTY 환경에서만 의미가 있다(다른 OS/백엔드는 풀을 안 쓴다).
    켠 직후 백그라운드 리필을 시작해, 콜드 스타트 첫 패널(prewarm) 이후 사용자의
    **첫 새 탭**이 풀 적중하도록 미리 준비한다."""
    global _pool_enabled
    # owned-ConPTY 가 실제 쓰일 때만 의미가 있다: 비-Windows, ConPTY 미지원, 또는
    # PYTMUX_PTY_BACKEND 로 pywinpty 강제 시엔 풀(owned 의사콘솔)이 안 쓰이므로 끈다.
    if not IS_WINDOWS or not conpty_supported():
        return
    if (os.environ.get("PYTMUX_PTY_BACKEND") or "").strip().lower() in (
            "pywinpty", "winpty"):
        return
    _pool_enabled = True
    _pool_refill_async()


def _pool_take():
    """풀에서 미리 만든 _ConPty 를 하나 꺼낸다(없으면 None). 꺼낸 뒤 호출부가 비동기
    리필을 트리거한다. 풀이 꺼져 있으면 항상 None(전경 생성 폴백)."""
    if not _pool_enabled:
        return None
    with _pool_lock:
        return _pool.pop() if _pool else None


def _pool_fill():
    """풀을 _POOL_TARGET 까지 채운다(백그라운드 스레드 본문). 의사콘솔 생성(~90ms)은
    ctypes 호출이 GIL 을 풀어 이벤트 루프를 막지 않는다. 생성 실패/풀 초과는 조용히
    정리한다(best-effort — 실패해도 전경 spawn 이 새로 만든다)."""
    global _pool_filling
    try:
        while True:
            with _pool_lock:
                if len(_pool) >= _POOL_TARGET:
                    return
            try:
                cp = _ConPty(*_pool_dims)
            except Exception:
                return
            with _pool_lock:
                if len(_pool) >= _POOL_TARGET:
                    spare = cp           # 레이스로 초과 → 락 밖에서 닫는다
                else:
                    _pool.append(cp)
                    spare = None
            if spare is not None:
                try:
                    spare.close()
                except OSError:
                    pass
                return
    finally:
        with _pool_lock:
            _pool_filling = False


def _pool_refill_async() -> None:
    """풀 리필 스레드를 (필요하면) 띄운다. 중복 기동은 _pool_filling 가드로 막는다."""
    global _pool_filling
    if not _pool_enabled:
        return
    with _pool_lock:
        if _pool_filling or len(_pool) >= _POOL_TARGET:
            return
        _pool_filling = True
    threading.Thread(target=_pool_fill, name="conpty-pool-fill",
                     daemon=True).start()


def _pool_drain() -> None:
    """풀에 남은 미사용 의사콘솔을 모두 닫는다(서버 종료 정리 — 고아 OpenConsole 방지)."""
    global _pool_enabled
    _pool_enabled = False
    with _pool_lock:
        items, _pool[:] = list(_pool), []
    for cp in items:
        try:
            cp.close()
        except OSError:
            pass


class _ConPty:
    """ctypes 로 직접 소유하는 ConPTY. raw 바이트 read/write(overlapped 명명 파이프).

    수명: __init__(파이프+의사콘솔) → spawn(자식) → read()/write()/resize() →
    close(ClosePseudoConsole→자식 트리 hangup→핸들 해제).
    """

    def __init__(self, cols: int, rows: int):
        if not IS_WINDOWS:
            raise NotImplementedError("ConPTY 는 Windows 전용")
        # 레시피 ①: 콘솔-less 데몬에 숨은 콘솔을 1회 붙여 자식 attach 를 성립시킨다.
        _ensure_hidden_console()
        self.pid = -1
        self._hpc = _HPCON()
        self._proc = _HANDLE()      # 자식 프로세스 핸들(wait/terminate)
        self._read = _HANDLE()      # 우리가 읽는 쪽(conhost 출력) — 동기 블로킹
        self._write = _HANDLE()     # 우리가 쓰는 쪽(conhost 입력) — 동기 블로킹
        self._closed = False
        self._exit: Optional[int] = None
        self._attrbuf = None        # 자식 spawn 후까지 유지(GC 방지)
        self._cmdbuf = None
        self._envbuf = None

        # conout(우리가 읽음) / conin(우리가 씀) 동기 명명 파이프. conhost 측 핸들은
        # CreatePseudoConsole 이 내부 복제하므로 직후 닫는다. _make_sync_pipe 반환은
        # (우리단, conhost단) 순서다 — conout 은 우리가 읽고 conin 은 우리가 쓰므로,
        # 우리단을 self._read/_write 로, conhost단을 CreatePseudoConsole 의 hOutput/hInput
        # 으로 넘긴다(이 순서를 뒤집으면 conhost 가 입력을 못 읽어 자식이 콘솔입력 EOF 로
        # 즉시 종료한다).
        h_read_out, h_pty_out = _make_sync_pipe(inbound=True)
        try:
            h_write_in, h_pty_in = _make_sync_pipe(inbound=False)
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
        # conhost 킥: winpty-rs/Windows Terminal 처럼 attach 직후 초기 resize 를 한 번
        # 보낸다(화면 페인트·diff 펌프를 깨운다). 돌파 레시피로 대화형 cmd 는 킥 없이도
        # 스트리밍되지만, 킥은 무해하고 호스트 정착을 돕는다. 같은 크기 resize 는 무시될
        # 수 있어 1행 줄였다가 본래 크기로 토글해 변경을 만든다.
        try:
            _resize_pc(self._hpc,
                       _COORD(self._cols, max(1, self._rows - 1)))
            _resize_pc(self._hpc, _COORD(self._cols, self._rows))
        except OSError:
            pass
        return self.pid

    def read(self, maxlen: int = 65536) -> bytes:
        """동기 블로킹 read(§1.1② 돌파 레시피 ③). 자식/콘솔이 닫히면 b"" (EOF).
        raw 바이트(디코드 안 함) — 멀티바이트 경계 손상 원점을 회피한다.

        동기 명명 파이프라 ReadFile 가 데이터가 올 때까지 블록한다(전용 리더 스레드에서).
        close() 의 ClosePseudoConsole 이 conhost 쓰기단을 닫으면 ERROR_BROKEN_PIPE 로,
        CancelIoEx 는 ERROR_OPERATION_ABORTED 로 대기를 깨워 b"" 로 빠져나온다."""
        if self._closed or not self._read:
            return b""
        buf = (ctypes.c_byte * maxlen)()
        n = wintypes.DWORD(0)
        ok = _k32.ReadFile(self._read, buf, maxlen, ctypes.byref(n), None)
        if not ok or n.value == 0:
            return b""          # BROKEN_PIPE/HANDLE_EOF/ABORTED/0바이트 → EOF·종료
        # c_byte 는 부호 있음(128~255 가 음수 int) → bytes(buf[:n]) 는 ValueError.
        # string_at 으로 raw 바이트를 그대로 복사한다(멀티바이트 무손상의 핵심).
        return ctypes.string_at(buf, n.value)

    def write(self, data: bytes) -> int:
        """동기 블로킹 write(§1.1③ — 디코드/재인코드 없음). 쓴 바이트 수 반환.

        128KB 파이프 버퍼라 일반 키스트로크·중소 paste 는 블로킹 없이 들어간다. 버퍼를
        넘는 대량 paste 버스트는 conhost 가 드레인할 때까지 블록할 수 있다(opt-in 한계)."""
        if self._closed or not self._write or not data:
            return 0
        n = wintypes.DWORD(0)
        cbuf = (ctypes.c_byte * len(data)).from_buffer_copy(data)
        ok = _k32.WriteFile(self._write, cbuf, len(data), ctypes.byref(n), None)
        if not ok:
            return 0
        return int(n.value)

    def resize(self, cols: int, rows: int) -> None:
        if self._closed or not self._hpc:
            return
        self._cols = max(1, cols)
        self._rows = max(1, rows)   # spawn() 의 resize 킥이 최신 크기를 쓰도록 보존
        _resize_pc(self._hpc, _COORD(self._cols, self._rows))

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

        동기 블로킹 read 가 대기 중일 수 있으므로 먼저 CancelIoEx 로 in-flight I/O 를 취소
        (ERROR_OPERATION_ABORTED 로 깨움)하고, ClosePseudoConsole 로 conhost 를 내려 쓰기단이
        닫히게 한 뒤 핸들을 해제한다 → 리더 스레드가 b""(EOF)로 자연 종료."""
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
        for h_attr in ("_write", "_read", "_proc"):
            h = getattr(self, h_attr)
            if h:
                try:
                    _k32.CloseHandle(h)
                except OSError:
                    pass
                setattr(self, h_attr, _HANDLE())
