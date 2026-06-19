"""OS 키보드 입력소스(IME) 실시간 질의 — macOS TIS / Windows IMM ctypes 바인딩(§10-B).

조사 결론(docs/internal/IME_INSTANT_STATE_SCENARIO.md): 확정 입력 스크립트 추정만으로는
한/영 키로 **모드만 바꾸고 아직 입력하지 않은** 동안 배지가 직전 상태로 남는다.

- **macOS** 는 HIToolbox 의 `TISCopyCurrentKeyboardInputSource` 가 현재 입력소스를
  돌려주지만, **장수명 프로세스에선 첫 호출 값에 freeze 된다** — HIToolbox 의 캐시는
  입력소스 변경 distributed notification 으로만 갱신되는데 그 알림은 프로세스가
  CFRunLoop 을 **실제로 돌려야** 전달되기 때문이다. 클라이언트는 asyncio 이벤트
  루프만 돌고 CFRunLoop 은 안 돌리므로 한/영 을 바꿔도 인프로세스 폴링은 영영
  시작값(영문)에 머문다(2026-06-17 재현·확정: cached/serviced 고정, 새 프로세스만
  추종. 개발 박스는 입력소스가 영구 ABC 라 이 freeze 가 안 보였다). 그래서 macOS 는
  **별도 감시 헬퍼 자식 프로세스**(`--watch`)를 띄운다 — 그 헬퍼는 진짜 CFRunLoop 을
  돌며 입력소스 변경 알림을 구독해, 바뀔 때마다(그리고 기동 시 1회) 현재 소스 ID 를
  stdout 한 줄로 흘린다. 클라이언트는 그 줄을 비차단으로 읽어 즉시 배지에 반영한다
  (이벤트 구동이라 CPU 사실상 0). `current_source_id` 한 발 질의는 초기 상태·Windows
  경로에만 쓴다(새로 만든 프로세스의 첫 호출은 fresh 라 정확).
- **Windows** 는 우리 콘솔이 포그라운드 창이라, `GetForegroundWindow → ImmGetDefaultIMEWnd`
  로 그 창의 IME 기본창을 얻어 `WM_IME_CONTROL(IMC_GETCONVERSIONMODE)` 를 보내면 현재
  변환모드를 돌려준다. `IME_CMODE_NATIVE` 비트가 곧 한글(켜짐)/영문(꺼짐)이며 한/영 키가
  바로 이 비트를 토글한다 — **입력 없이** 모드만 바꿔도 폴링이 따라온다. 메시지가
  포그라운드 창의 다른 스레드(터미널 프로세스)로 가 블록될 수 있어 `SendMessageTimeoutW`
  (ABORTIFHUNG, 50ms)로 보내 이벤트 루프가 멈추지 않게 한다.

알림 구독(macOS kTISNotify…, Windows WM_INPUTLANGCHANGE 후킹)은 전용 런루프/메시지펌프가
필요해 복잡도 대비 이득이 없어 양쪽 모두 **폴링 채택**.

실패는 전부 None 으로 수렴한다 — 그 외 플랫폼, ssh 원격 호스트에서 도는 클라, 포그라운드
창/IME 창 부재, 프레임워크 로드 실패 등. 호출부(__init__.client_key)가 기존 확정 입력
휴리스틱으로 폴백하므로 이 모듈이 없거나 실패해도 배지는 동작한다."""
from __future__ import annotations

import sys

# 플랫폼별 ctypes 바인딩 캐시. None=미시도, False=불가 확정(재시도 안 함 — 폴링 경로라
# 스팸 방지). 성공 시 첫 원소가 플랫폼 태그('darwin'/'win32')인 튜플.
_libs = None

_HIT_PATH = ("/System/Library/Frameworks/Carbon.framework/Frameworks/"
             "HIToolbox.framework/HIToolbox")
_kCFStringEncodingUTF8 = 0x08000100

# Windows IMM 질의 상수.
_WM_IME_CONTROL = 0x0283
_IMC_GETCONVERSIONMODE = 0x0001
_IME_CMODE_NATIVE = 0x0001
_SMTO_ABORTIFHUNG = 0x0002


def _setup_darwin():
    import ctypes
    import ctypes.util
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    hit = ctypes.CDLL(_HIT_PATH)
    cf.CFStringGetCStringPtr.restype = ctypes.c_char_p
    cf.CFStringGetCStringPtr.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    hit.TISCopyCurrentKeyboardInputSource.restype = ctypes.c_void_p
    hit.TISGetInputSourceProperty.restype = ctypes.c_void_p
    hit.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kid = ctypes.c_void_p.in_dll(hit, "kTISPropertyInputSourceID")
    return ("darwin", ctypes, cf, hit, kid)


def _setup_win32():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    imm32 = ctypes.WinDLL("imm32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND
    imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
    # 결과는 포인터 크기 DWORD_PTR 로 lpdwResult 에 쓰인다 → c_size_t.
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    return ("win32", ctypes, user32, imm32)


def _setup():
    """ctypes 바인딩 1회 준비. 실패하면 False 를 캐시해 이후 호출은 즉시 None."""
    global _libs
    if _libs is not None:
        return _libs
    try:
        if sys.platform == "darwin":
            _libs = _setup_darwin()
        elif sys.platform == "win32":
            _libs = _setup_win32()
        else:
            _libs = False
    except Exception:        # 어떤 실패든 '불가'로 확정(베스트에포트 — 폴백 휴리스틱)
        _libs = False
    return _libs


def _current_darwin(libs) -> str | None:
    _tag, ctypes, cf, hit, kid = libs
    try:
        src = hit.TISCopyCurrentKeyboardInputSource()
        if not src:
            return None
        try:
            ref = hit.TISGetInputSourceProperty(src, kid)
            if not ref:
                return None
            p = cf.CFStringGetCStringPtr(ref, _kCFStringEncodingUTF8)
            if p:
                return p.decode()
            buf = ctypes.create_string_buffer(256)
            if cf.CFStringGetCString(ref, buf, 256, _kCFStringEncodingUTF8):
                return buf.value.decode()
            return None
        finally:
            cf.CFRelease(ctypes.c_void_p(src))
    except Exception:
        return None


def _current_win32(libs) -> str | None:
    """포그라운드 창의 IME 변환모드를 질의해 합성 입력소스 ID 를 만든다 —
    한글(NATIVE)이면 'windows.ime.hangul', 아니면 'windows.ime.latin'. 포그라운드
    창/IME 창 부재나 SendMessage 타임아웃은 None(폴백)."""
    _tag, ctypes, user32, imm32 = libs
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        ime = imm32.ImmGetDefaultIMEWnd(hwnd)
        if not ime:
            return None
        res = ctypes.c_size_t(0)
        ok = user32.SendMessageTimeoutW(
            ime, _WM_IME_CONTROL, _IMC_GETCONVERSIONMODE, 0,
            _SMTO_ABORTIFHUNG, 50, ctypes.byref(res))
        if not ok:                       # 타임아웃/실패 — 직전 상태 유지
            return None
        return ("windows.ime.hangul" if (res.value & _IME_CMODE_NATIVE)
                else "windows.ime.latin")
    except Exception:
        return None


def current_source_id() -> str | None:
    """현재 키보드 입력소스 ID(macOS: 'com.apple.keylayout.ABC' 등 실 ID, Windows:
    합성 'windows.ime.hangul'/'windows.ime.latin') 또는 불가 시 None."""
    libs = _setup()
    if not libs:
        return None
    if libs[0] == "win32":
        return _current_win32(libs)
    return _current_darwin(libs)


def spawn_watcher():
    """macOS 입력소스 변경 감시 헬퍼 자식 프로세스를 기동한다(인프로세스 TIS freeze
    우회 — 모듈 docstring 참조). 헬퍼는 진짜 CFRunLoop 을 돌며 변경 시 현재 소스 ID 를
    stdout 한 줄로 흘린다. stdout 은 비차단으로 설정해 호출부가 `read_latest` 로
    드레인한다. 부모 종료를 헬퍼가 감지하도록 stdin 파이프를 유지한다(헬퍼가 stdin
    EOF 로 자가 종료 — `client_unload` 누락/강제종료에도 좀비가 안 남는다).

    비 macOS·바인딩 실패·spawn 실패는 None → 호출부가 폴백(확정 입력 휴리스틱 또는
    Windows 인프로세스 질의)으로 동작한다."""
    if sys.platform != "darwin" or not _setup():
        return None
    try:
        import subprocess
        import fcntl
        import os
        proc = subprocess.Popen(
            [sys.executable, __file__, "--watch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, close_fds=True)
        fd = proc.stdout.fileno()
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        return proc
    except Exception:
        return None


def _drain(fd, prev_buf=b""):
    """fd(감시 헬퍼 stdout 또는 에이전트 소켓)에서 가용 바이트를 비차단으로 모두 읽어
    `(마지막 완성 줄|None, 잔여버퍼, closed)` 를 돌린다. 한 틱에 변경이 여러 줄 쌓여도
    **최신 줄만** 반영한다(중간 상태 깜빡임 방지). `closed`=피어/헬퍼가 fd 를 닫음(EOF).
    읽기 실패도 closed=True(폴백 신호). read_latest(헬퍼)·read_agent(소켓) 공용 코어."""
    import os
    buf = prev_buf
    closed = False
    try:
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:                 # EOF(헬퍼 종료/피어 close)
                closed = True
                break
            buf += chunk
    except BlockingIOError:                # 더 읽을 게 없음(EAGAIN)
        pass
    except Exception:
        return (None, buf, True)
    if b"\n" not in buf:
        return (None, buf, closed)
    *lines, buf = buf.split(b"\n")         # buf = 마지막 개행 뒤 미완성 조각
    for line in reversed(lines):
        line = line.strip()
        if line:
            try:
                return (line.decode(), buf, closed)
            except Exception:
                return (None, buf, closed)
    return (None, buf, closed)


def read_latest(proc, prev_buf=b""):
    """감시 헬퍼 stdout 에서 최신 소스 ID 와 잔여 버퍼를 `(sid|None, buf)` 로 돌린다
    (헬퍼 EOF 판정은 호출부가 proc.poll() 로 하므로 closed 는 버린다)."""
    sid, buf, _closed = _drain(proc.stdout.fileno(), prev_buf)
    return (sid, buf)


def connect_agent(path):
    """역포워드된(ssh -R) 로컬 IME 에이전트 unix 소켓에 연결한다(§9.1 전송로 ②).
    성공 시 비차단 소켓, 경로 부재/연결 실패는 None → 호출부가 휴리스틱 폴백. 원격
    클라가 `PYTMUX_IME_SOCK`(=ssh -R 원격 끝점)로 이 함수를 부른다."""
    if not path:
        return None
    try:
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(path)
        s.setblocking(False)
        return s
    except Exception:
        return None


def read_agent(sock, prev_buf=b""):
    """IME 에이전트 소켓에서 최신 소스 ID·잔여·closed 를 `(sid|None, buf, closed)` 로
    드레인한다(read_latest 의 소켓판 — EOF/오류를 closed 로 알려 호출부가 폴백·재연결)."""
    return _drain(sock.fileno(), prev_buf)


# 한글 입력소스 판별 토큰(소문자 대조) — 애플 기본(Korean.2SetKorean 등)과
# 서드파티(구름 han2/han3, 하늘 등)를 폭넓게 잡는다. 매칭 안 되면 'EN' 취급.
_KOREAN_TOKENS = ("korean", "hangul", "han2", "han3")


def is_korean(source_id: str | None) -> bool:
    """입력소스 ID 가 한글 IME 인지(베스트에포트 부분 문자열 대조)."""
    if not source_id:
        return False
    s = source_id.lower()
    return any(t in s for t in _KOREAN_TOKENS)


# 입력소스 변경 distributed notification 이름(macOS). 헬퍼가 이걸 구독한다.
_TIS_NOTIFY = b"com.apple.Carbon.TISNotifySelectedKeyboardInputSourceChanged"
_kCFNotificationDeliverImmediately = 4    # CFNotificationSuspensionBehavior


def _watch_darwin():
    """감시 헬퍼 본체(`python oskbd.py --watch` 로 실행). 진짜 CFRunLoop 을 돌며
    입력소스 변경 distributed notification 을 구독해, 바뀔 때마다(+기동 시 1회) 현재
    소스 ID 를 stdout 한 줄로 흘린다. CFRunLoop 이 도는 프로세스라 HIToolbox 캐시가
    살아 있어 `TISCopyCurrentKeyboardInputSource` 가 항상 fresh 다(인프로세스 freeze
    회피의 핵심). 부모가 죽으면 stdin EOF 를 감지해 자가 종료한다."""
    libs = _setup()
    if not libs or libs[0] != "darwin":
        return
    import ctypes
    import os
    import threading
    _tag, _ct, cf, hit, _kid = libs

    cf.CFRunLoopRun.restype = None
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFNotificationCenterGetDistributedCenter.restype = ctypes.c_void_p
    _CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    cf.CFNotificationCenterAddObserver.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, _CB, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_int]

    def _emit():
        sid = _current_darwin(libs)       # CFRunLoop 가동 중이라 fresh
        if sid:
            try:
                sys.stdout.write(sid + "\n")
                sys.stdout.flush()
            except Exception:
                os._exit(0)               # 부모가 파이프를 닫음 → 종료

    @_CB
    def _on_change(center, observer, name, obj, info):
        _emit()

    name = cf.CFStringCreateWithCString(
        None, _TIS_NOTIFY, _kCFStringEncodingUTF8)
    dc = cf.CFNotificationCenterGetDistributedCenter()
    cf.CFNotificationCenterAddObserver(
        dc, None, _on_change, name, None, _kCFNotificationDeliverImmediately)

    # 부모 사망 감시: stdin EOF 면(부모 종료/강제종료로 파이프 닫힘) 즉시 종료.
    def _wait_parent():
        try:
            sys.stdin.buffer.read()       # EOF 까지 블록
        except Exception:
            pass
        os._exit(0)
    threading.Thread(target=_wait_parent, daemon=True).start()

    _emit()                               # 기동 시 초기 상태 1회
    cf.CFRunLoopRun()                     # 알림 수신 — 영구 블록(_wait_parent 가 종료)
    # 참조 유지(콜백 GC 방지) — 도달하지 않지만 의도 명시.
    _ = (_on_change, name)


if __name__ == "__main__":
    if "--watch" in sys.argv:
        _watch_darwin()
