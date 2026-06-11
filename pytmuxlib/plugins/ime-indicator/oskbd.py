"""OS 키보드 입력소스(IME) 실시간 질의 — macOS TIS ctypes 바인딩(§10-B 2026-06-11).

조사 결론(docs/IME_INSTANT_STATE_SCENARIO.md): 확정 입력 스크립트 추정만으로는
한/영 키로 **모드만 바꾸고 아직 입력하지 않은** 동안 배지가 직전 상태로 남는다.
macOS 는 HIToolbox 의 `TISCopyCurrentKeyboardInputSource` 가 GUI 앱이 아닌 **CLI
프로세스에서도** 같은 로그인 세션의 현재 입력소스를 돌려준다(이 박스 실측: 최초
호출 ~33ms(프레임워크 로드), 이후 호출당 ~1µs — 0.25초 폴링은 사실상 무비용).
알림 구독(kTISNotifySelectedKeyboardInputSourceChanged)은 CFRunLoop 전용 스레드가
필요해 복잡도 대비 이득이 없어 **폴링 채택**.

실패는 전부 None 으로 수렴한다 — 비 macOS, ssh 원격 호스트에서 도는 클라(Aqua
세션 없음), 프레임워크 로드 실패 등. 호출부(__init__.client_key)가 기존 확정 입력
휴리스틱으로 폴백하므로 이 모듈이 없거나 실패해도 배지는 동작한다."""
from __future__ import annotations

import sys

# (HIToolbox, CoreFoundation, kTISPropertyInputSourceID) 캐시.
# None=미시도, False=불가 확정(재시도 안 함 — 폴링 경로라 스팸 방지).
_libs = None

_HIT_PATH = ("/System/Library/Frameworks/Carbon.framework/Frameworks/"
             "HIToolbox.framework/HIToolbox")
_kCFStringEncodingUTF8 = 0x08000100


def _setup():
    """ctypes 바인딩 1회 준비. 실패하면 False 를 캐시해 이후 호출은 즉시 None."""
    global _libs
    if _libs is not None:
        return _libs
    if sys.platform != "darwin":
        _libs = False
        return _libs
    try:
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
        _libs = (ctypes, cf, hit, kid)
    except Exception:        # 어떤 실패든 '불가'로 확정(베스트에포트 — 폴백 휴리스틱)
        _libs = False
    return _libs


def current_source_id() -> str | None:
    """현재 키보드 입력소스 ID(예: 'com.apple.keylayout.ABC',
    'com.apple.inputmethod.Korean.2SetKorean') 또는 불가 시 None."""
    libs = _setup()
    if not libs:
        return None
    ctypes, cf, hit, kid = libs
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


# 한글 입력소스 판별 토큰(소문자 대조) — 애플 기본(Korean.2SetKorean 등)과
# 서드파티(구름 han2/han3, 하늘 등)를 폭넓게 잡는다. 매칭 안 되면 'EN' 취급.
_KOREAN_TOKENS = ("korean", "hangul", "han2", "han3")


def is_korean(source_id: str | None) -> bool:
    """입력소스 ID 가 한글 IME 인지(베스트에포트 부분 문자열 대조)."""
    if not source_id:
        return False
    s = source_id.lower()
    return any(t in s for t in _KOREAN_TOKENS)
