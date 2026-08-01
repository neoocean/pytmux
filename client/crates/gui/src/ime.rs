//! 입력기(IME) 상태 배지 — 지금 한글 조합인가 영문인가.
//!
//! # 왜 필요한가
//!
//! 정본은 상태줄에 `[한]`/`[EN]` 을 **늘 보인다**(대조 문서 §1 — 우리에겐 그 자리가
//! 없었다). 없으면 사용자는 자기가 친 글자가 왜 한글로 들어갔는지 화면으로 알 수 없다.
//! 이 저장소도 그 함정을 실제로 밟았다: 정본 스크린샷을 찍는 하네스가 `claude` 를
//! 패널에 넣으려다 `치명ㄷ` 을 넣었고, 원인이 입력기라는 것을 한참 뒤에 알았다.
//!
//! # 왜 OS 에 물어보나
//!
//! winit 의 IME 이벤트로는 **조합 중**만 알 수 있다(`Ime::Preedit`). "지금 영문이다"는
//! 조합이 없을 때의 상태라 이벤트로는 안 온다 — 그래서 아무것도 안 치고 있을 때의
//! 배지를 못 만든다. 정본은 OS 상태를 읽는 플러그인으로 그걸 해결한다(사용자 결정
//! 2026-07-31: 같은 방식으로 간다).
//!
//! # 범위
//!
//! **Windows 전용**이다. 맥/리눅스에서는 [`hangul_mode`] 가 `None` 을 돌려주고 배지는
//! 아예 안 뜬다 — 모르는 것을 "영문"이라고 단정하지 않는다.

/// 지금 한글 조합 모드인가. 못 알아내면 `None`(배지를 안 그린다).
#[cfg(target_os = "windows")]
pub fn hangul_mode() -> Option<bool> {
    use windows::Win32::Foundation::{HWND, LPARAM, WPARAM};
    use windows::Win32::UI::Input::Ime::ImmGetDefaultIMEWnd;
    use windows::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, SMTO_ABORTIFHUNG, SendMessageTimeoutW,
    };

    const WM_IME_CONTROL: u32 = 0x0283;
    const IMC_GETCONVERSIONMODE: usize = 0x0001;
    const IME_CMODE_NATIVE: usize = 0x0001;

    // 전경 창을 쓰는 이유: 입력기 상태는 **지금 키를 받는 창**의 것이다. 우리 창이
    // 앞에 없으면 그 값은 우리 것이 아니므로 배지를 안 그린다(아래 `is_ours`).
    let fg = unsafe { GetForegroundWindow() };
    if fg == HWND::default() {
        return None;
    }
    let ime = unsafe { ImmGetDefaultIMEWnd(fg) };
    if ime == HWND::default() {
        return None;
    }
    let mut out = 0usize;
    // ★ **타임아웃 있는** 메시지다. 그냥 `SendMessage` 면 입력기 창이 멎었을 때 우리
    //   렌더 스레드가 같이 멎는다 — 배지 하나 때문에 화면 전체가 얼면 안 된다.
    let ok = unsafe {
        SendMessageTimeoutW(
            ime,
            WM_IME_CONTROL,
            WPARAM(IMC_GETCONVERSIONMODE),
            LPARAM(0),
            SMTO_ABORTIFHUNG,
            50,
            Some(&mut out as *mut usize as *mut _),
        )
    };
    if ok.0 == 0 {
        return None;
    }
    Some(out & IME_CMODE_NATIVE != 0)
}

/// Windows 가 아니면 언제나 `None` — 배지가 없다.
#[cfg(not(target_os = "windows"))]
pub fn hangul_mode() -> Option<bool> {
    None
}

/// 배지에 적을 낱말. 상태를 모르면 `None`.
///
/// 낱말은 **번역하지 않는다** — 정본과 같은 `[한]`/`[EN]` 이고, 이 배지는 눈으로 훑는
/// 표식이라 로케일마다 폭이 달라지면 상태줄이 흔들린다.
pub fn badge() -> Option<&'static str> {
    hangul_mode().map(|on| if on { "한" } else { "EN" })
}
