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
//! # 범위 (pytmux-590 · 사람 결정 2026-11-12: 맥과 리눅스도 낸다)
//!
//! - **Windows** — 전경 창의 IMM 변환 모드(`IME_CMODE_NATIVE`).
//! - **macOS** — 현재 입력 소스(TIS `kTISPropertyInputSourceID`) — 정본 `oskbd._current_darwin`
//!   과 같은 호출. 판정은 `base::ime_source::is_korean`(정본 `is_korean` 과 답지로 맞댄다).
//!   ⚠ **메인 스레드에서만** 부른다(macOS 14+ 의 HIToolbox 는 다른 스레드에서 부르면 단언으로
//!   죽을 수 있다) — `tick_ime` 은 UI(=메인) 스레드다. 정본이 따로 감시 자식을 두는 「장수명
//!   프로세스 freeze」는 CFRunLoop 을 안 도는 프로세스의 일이고, GUI 는 NSApplication 런루프가
//!   돌아 입력 소스 변경 알림이 배달된다.
//! - **Linux** — 입력기 CLI 를 묻는다: `fcitx5-remote -n` → `ibus engine`(정본 `oskbd` 의 같은
//!   차례 · 같은 파서). 프로세스라 **배경 스레드**가 300ms 마다 묻고 `tick_ime` 은 캐시만 읽는다.
//!   ⚠ 한계: ibus-hangul 은 엔진 **안에서** 한/영을 바꿔(엔진 이름 `hangul` 그대로) 그 안의 영문
//!   모드는 못 가른다 — 「한글 엔진이 걸려 있다」까지만 안다(Fcitx5 는 입력기 자체가 바뀌어 정확).
//! - 그 밖 / 못 읽으면 `None` — 배지는 아예 안 뜬다. 모르는 것을 "영문"이라고 단정하지 않는다.

/// 지금 한글 조합 모드인가. 못 알아내면 `None`(배지를 안 그린다).
#[cfg(target_os = "windows")]
pub fn hangul_mode() -> Option<bool> {
    use windows::Win32::Foundation::{HWND, LPARAM, WPARAM};
    use windows::Win32::UI::Input::Ime::ImmGetDefaultIMEWnd;
    use windows::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, GetWindowThreadProcessId, SMTO_ABORTIFHUNG, SendMessageTimeoutW,
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
    // ★ `is_ours` — 주석만 있고 검사가 없었다(pytmux-392 캡처 중 발견 · 2026-10-16).
    //   그래서 우리 창이 뒤에 있으면 배지가 **남의 전경 앱**의 한/영을 보였고, 그 앱이
    //   UIPI 로 질의를 거부하면(ACCESS_DENIED) 사라졌다 — 어느 쪽도 우리 창의 상태가 아니다.
    let mut owner = 0u32;
    unsafe { GetWindowThreadProcessId(fg, Some(&mut owner)) };
    if !is_ours(owner, std::process::id()) {
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

/// 전경 창의 소유 프로세스가 우리인가 — 아니면 그 입력기 상태는 우리 것이 아니다.
/// 0(못 읽음)은 우리 것이 아니다.
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
fn is_ours(owner_pid: u32, my_pid: u32) -> bool {
    owner_pid != 0 && owner_pid == my_pid
}

#[cfg(test)]
mod tests {
    use super::is_ours;

    #[test]
    fn the_badge_only_reads_our_own_foreground_window() {
        assert!(is_ours(4242, 4242));
        assert!(!is_ours(1111, 4242), "남의 전경 창의 한/영을 우리 배지로 그리면 안 된다");
        assert!(!is_ours(0, 4242), "소유자를 못 읽으면 우리 것이 아니다");
    }
}

/// 지금 한글 조합 모드인가(macOS · 리눅스 — 입력 소스 ID 를 읽어 `is_korean`).
#[cfg(any(target_os = "macos", target_os = "linux"))]
pub fn hangul_mode() -> Option<bool> {
    source_id().map(|id| base::ime_source::is_korean(Some(&id)))
}

/// 그 밖의 OS 는 언제나 `None` — 배지가 없다.
#[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
pub fn hangul_mode() -> Option<bool> {
    None
}

/// 지금 입력 소스 ID — 정본 `oskbd.current_source_id()` 와 **같은 말**(맥은 실 ID, 윈도는 합성
/// `windows.ime.hangul`/`latin`, 리눅스는 합성 `fcitx5:<입력기>`/`ibus:<엔진>`). `--ime-probe` 가
/// 정본과 맞대는 값이다.
#[cfg(target_os = "windows")]
pub fn source_id() -> Option<String> {
    hangul_mode().map(|on| if on { "windows.ime.hangul" } else { "windows.ime.latin" }.to_owned())
}

/// macOS — HIToolbox `TISCopyCurrentKeyboardInputSource` 의 `kTISPropertyInputSourceID`.
/// ⚠ 메인 스레드에서만(모듈 머리말).
#[cfg(target_os = "macos")]
pub fn source_id() -> Option<String> {
    mac::source_id()
}

/// 리눅스 — 배경 스레드가 마지막으로 읽은 값(첫 호출이 그 스레드를 띄운다).
#[cfg(target_os = "linux")]
pub fn source_id() -> Option<String> {
    linux::source_id()
}

#[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
pub fn source_id() -> Option<String> {
    None
}

#[cfg(target_os = "macos")]
mod mac {
    use std::ffi::{c_char, c_void, CStr};

    // 새 크레이트 없이 프레임워크를 직접 부른다(라이선스 게이트 무변 · 정본 ctypes 와 같은 호출).
    #[link(name = "Carbon", kind = "framework")]
    unsafe extern "C" {
        fn TISCopyCurrentKeyboardInputSource() -> *const c_void;
        fn TISGetInputSourceProperty(source: *const c_void, key: *const c_void) -> *const c_void;
        static kTISPropertyInputSourceID: *const c_void;
    }

    #[link(name = "CoreFoundation", kind = "framework")]
    unsafe extern "C" {
        fn CFStringGetCString(s: *const c_void, buf: *mut c_char, size: isize, encoding: u32) -> u8;
        fn CFRelease(p: *const c_void);
    }

    unsafe extern "C" {
        /// libSystem — 지금 스레드가 메인 스레드인가(1 이면 그렇다).
        fn pthread_main_np() -> i32;
    }

    const UTF8: u32 = 0x0800_0100; // kCFStringEncodingUTF8

    pub fn source_id() -> Option<String> {
        // ★ 메인 스레드가 아니면 **묻지 않는다**(실측 2026-11-13: 시험 스레드에서 부르자 시험
        //   이진이 SIGABRT 로 통째 죽었다 — HIToolbox 의 메인 큐 단언). 모르면 None(배지 없음)이
        //   규칙이므로 이것이 안전한 쪽이다.
        // SAFETY: 인자 없는 libSystem 함수.
        if unsafe { pthread_main_np() } != 1 {
            return None;
        }
        // SAFETY: Copy 규칙 — 받은 소스는 우리가 `CFRelease` 한다. 속성은 Get 규칙(빌린 것)이라
        // 놓지 않는다. 버퍼 크기는 정본과 같은 256.
        unsafe {
            let src = TISCopyCurrentKeyboardInputSource();
            if src.is_null() {
                return None;
            }
            let id = TISGetInputSourceProperty(src, kTISPropertyInputSourceID);
            let mut buf = [0 as c_char; 256];
            let ok = !id.is_null()
                && CFStringGetCString(id, buf.as_mut_ptr(), buf.len() as isize, UTF8) != 0;
            CFRelease(src);
            if !ok {
                return None;
            }
            let text = CStr::from_ptr(buf.as_ptr()).to_string_lossy().into_owned();
            (!text.is_empty()).then_some(text)
        }
    }
}

#[cfg(target_os = "linux")]
mod linux {
    use std::io::Read;
    use std::process::{Command, Stdio};
    use std::sync::{Mutex, Once};
    use std::time::{Duration, Instant};

    /// 묻는 간격·명령 하나의 상한 — 정본 `_LINUX_MIN_GAP`·`_LINUX_TIMEOUT` 과 같은 값.
    const GAP: Duration = Duration::from_millis(300);
    const TIMEOUT: Duration = Duration::from_millis(300);

    static LATEST: Mutex<Option<String>> = Mutex::new(None);
    static START: Once = Once::new();

    pub fn source_id() -> Option<String> {
        START.call_once(|| {
            // 프로세스 수명의 스레드다(앱이 끝나면 함께 끝난다). 띄우기 실패면 배지가 없을 뿐이다.
            let _ = std::thread::Builder::new().name("ime-poll".into()).spawn(|| loop {
                let id = query();
                if let Ok(mut slot) = LATEST.lock() {
                    *slot = id;
                }
                std::thread::sleep(GAP);
            });
        });
        LATEST.lock().ok().and_then(|s| s.clone())
    }

    /// Fcitx5 → IBus 차례(정본 `_current_linux`).
    fn query() -> Option<String> {
        run("fcitx5-remote", &["-n"])
            .and_then(|out| base::ime_source::parse_fcitx5(&out))
            .or_else(|| run("ibus", &["engine"]).and_then(|out| base::ime_source::parse_ibus(&out)))
    }

    /// 명령 하나 — 상한 안에 rc 0 으로 끝나야 그 출력이다. 없거나 멎으면 `None`.
    fn run(cmd: &str, args: &[&str]) -> Option<String> {
        // 프로그램 이름 규칙은 `clip::system_tool` 한 자리다(리눅스에서는 이름 그대로 — 입력기
        // CLI 는 사용자의 PATH 에 산다 · 시스템 도구 게이트 `system_tool_gate.rs`).
        let mut child = Command::new(clip::system_tool(cmd)?)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .ok()?;
        let started = Instant::now();
        loop {
            match child.try_wait() {
                Ok(Some(status)) => {
                    if !status.success() {
                        return None;
                    }
                    let mut out = String::new();
                    child.stdout.take()?.read_to_string(&mut out).ok()?;
                    return Some(out);
                }
                Ok(None) if started.elapsed() < TIMEOUT => {
                    std::thread::sleep(Duration::from_millis(10));
                }
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return None;
                }
            }
        }
    }
}

/// 배지에 적을 낱말. 상태를 모르면 `None`.
///
/// 낱말은 **번역하지 않는다** — 정본과 같은 `[한]`/`[EN]` 이고, 이 배지는 눈으로 훑는
/// 표식이라 로케일마다 폭이 달라지면 상태줄이 흔들린다.
pub fn badge() -> Option<&'static str> {
    hangul_mode().map(|on| if on { "한" } else { "EN" })
}
