"""OS 키보드 입력소스(IME) 실시간 질의 — macOS TIS / Windows IMM ctypes 바인딩(§10-B).

조사 결론(docs/internal/IME_INSTANT_STATE_SCENARIO.md): 확정 입력 스크립트 추정만으로는
한/영 키로 **모드만 바꾸고 아직 입력하지 않은** 동안 배지가 직전 상태로 남는다.

- **macOS** 는 HIToolbox 의 `TISCopyCurrentKeyboardInputSource` 가 GUI 앱이 아닌 **CLI
  프로세스에서도** 같은 로그인 세션의 현재 입력소스를 돌려준다(이 박스 실측: 최초
  호출 ~33ms(프레임워크 로드), 이후 호출당 ~1µs — 0.05초 폴링도 사실상 무비용).
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


# 한글 입력소스 판별 토큰(소문자 대조) — 애플 기본(Korean.2SetKorean 등)과
# 서드파티(구름 han2/han3, 하늘 등)를 폭넓게 잡는다. 매칭 안 되면 'EN' 취급.
_KOREAN_TOKENS = ("korean", "hangul", "han2", "han3")


def is_korean(source_id: str | None) -> bool:
    """입력소스 ID 가 한글 IME 인지(베스트에포트 부분 문자열 대조)."""
    if not source_id:
        return False
    s = source_id.lower()
    return any(t in s for t in _KOREAN_TOKENS)
