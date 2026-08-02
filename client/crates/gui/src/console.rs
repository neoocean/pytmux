//! 터미널에서 띄웠을 때만 **부모 콘솔에 붙는다**(Windows).
//!
//! # 왜 이 짝이 필요한가
//!
//! `main.rs` 가 `windows_subsystem = "windows"` 로 링크하는 이유는 탐색기·바로가기로
//! 띄울 때 검은 cmd 창이 먼저 뜨는 것을 없애기 위해서다(§10-20ⓒ). 그런데 그렇게만
//! 하면 이 이진이 내는 글 — `--help`, 인자 오류, **서버 기동 실패 사유** — 이 갈 곳이
//! 없어진다. 이 저장소가 "실패가 통째로 사라진다"고 부르는 그 부류다(2026-07-28 원격
//! 실측: 데몬 stderr 가 `/dev/null` 이라 `ModuleNotFoundError` 가 어디에도 안 남았다).
//!
//! `AttachConsole(ATTACH_PARENT_PROCESS)` 는 **부모가 콘솔을 갖고 있을 때만** 성공한다:
//!
//! - 터미널에서 띄웠다 → 붙는다 → 글이 종전처럼 보인다.
//! - 탐색기에서 띄웠다 → 부모(explorer)에 콘솔이 없다 → 실패 → **창을 만들지 않는다**.
//!
//! 즉 판정을 우리가 하지 않는다. "콘솔이 있으면 쓰고 없으면 조용히 지나간다"가 이
//! 함수의 전부이고, 그래서 실패는 오류가 아니다.
//!
//! # 핸들을 다시 세우는 이유
//!
//! 붙기만 하면 콘솔은 생기지만 이 프로세스의 표준 핸들은 그대로다(GUI 서브시스템으로
//! 뜬 프로세스는 비어 있다). 러스트 std 는 `GetStdHandle` 을 **첫 출력 때 한 번** 읽어
//! 캐시하므로, 붙은 직후 `CONOUT$`/`CONIN$` 를 열어 `SetStdHandle` 로 꽂아 두면 그
//! 뒤의 `println!`·`eprintln!` 이 평소 경로 그대로 콘솔로 간다. 이 함수를 `main` 의
//! **첫 줄**에서 부르는 이유가 그것이다 — 한 줄이라도 먼저 출력하면 늦는다.

/// 부모 콘솔에 붙었으면 `true`. 붙을 콘솔이 없으면 `false`(오류가 아니다).
#[cfg(windows)]
pub fn attach_parent() -> bool {
    use windows::Win32::Foundation::{GENERIC_READ, GENERIC_WRITE, HANDLE};
    use windows::Win32::Storage::FileSystem::{
        CreateFileW, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
    };
    use windows::Win32::Foundation::INVALID_HANDLE_VALUE;
    use windows::Win32::System::Console::{
        ATTACH_PARENT_PROCESS, AttachConsole, GetStdHandle, STD_ERROR_HANDLE, STD_HANDLE,
        STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, SetStdHandle,
    };
    use windows::core::{PCWSTR, w};

    // SAFETY: 조회성 호출이고, 실패는 값으로 돌아온다(부모에 콘솔이 없는 정상 경우가
    // 실패다 — 탐색기에서 띄웠을 때).
    if unsafe { AttachConsole(ATTACH_PARENT_PROCESS) }.is_err() {
        return false;
    }

    // 붙은 콘솔을 이 프로세스의 표준 핸들로 꽂는다.
    //
    // ⚠ **이미 유효한 핸들이 있으면 손대지 않는다.** 리다이렉트(`> out.txt`)나 파이프
    //   (`… | Select-String`)로 띄우면 그 자리에 부모가 준 핸들이 이미 있는데, 그걸
    //   `CONOUT$` 로 덮으면 출력이 **파일·파이프가 아니라 콘솔로** 새어 나간다 —
    //   호출한 쪽에서는 "아무것도 안 나온다"로 보인다(실측: 첫 판이 그랬다).
    let open = |name: PCWSTR, target: STD_HANDLE| {
        // SAFETY: 표준 핸들 조회.
        if let Ok(existing) = unsafe { GetStdHandle(target) } {
            if !existing.is_invalid() && existing != INVALID_HANDLE_VALUE {
                return;
            }
        }
        // SAFETY: 콘솔 의사 파일을 여는 표준 방법. 실패는 Err 로 돌아온다.
        let handle: windows::core::Result<HANDLE> = unsafe {
            CreateFileW(
                name,
                GENERIC_READ.0 | GENERIC_WRITE.0,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None,
            )
        };
        if let Ok(handle) = handle {
            // SAFETY: 방금 연 유효한 핸들을 꽂는다.
            let _ = unsafe { SetStdHandle(target, handle) };
        }
    };
    open(w!("CONOUT$"), STD_OUTPUT_HANDLE);
    open(w!("CONOUT$"), STD_ERROR_HANDLE);
    open(w!("CONIN$"), STD_INPUT_HANDLE);
    true
}

/// Windows 밖에서는 할 일이 없다 — 콘솔 서브시스템이라는 개념 자체가 없다.
/// **참을 돌려준다**: POSIX 이진은 언제나 표준 스트림을 갖는다.
#[cfg(not(windows))]
pub fn attach_parent() -> bool {
    true
}

/// 콘솔에 쓴다. **실패해도 죽지 않는다.**
///
/// `println!`·`eprintln!` 은 쓰기 실패에 패닉한다. GUI 서브시스템 이진은 셸이
/// 기다려 주지 않아 파이프가 우리보다 먼저 닫힐 수 있고(실측: `pytmux-gui --help > 파일`
/// 이 `failed printing to stdout: The pipe is being closed` 로 죽었다), 그러면 사용자가
/// 보는 것은 **사용법이 아니라 패닉**이다. 진단을 내려다 죽는 진단은 진단이 아니다.
pub fn say(text: &str, to_stderr: bool) {
    use std::io::Write;
    let _ = if to_stderr {
        write!(std::io::stderr(), "{text}")
    } else {
        write!(std::io::stdout(), "{text}")
    };
}

/// 시동 실패를 **화면에** 알린다 — 콘솔이 없을 때의 마지막 수단.
///
/// # 왜 필요한가
///
/// 탐색기·바로가기로 띄우면 붙을 콘솔이 없다([`attach_parent`] 가 `false`). 그 자리에서
/// 서버 기동이 실패하면 종전 코드는 `eprintln!` 하고 `exit(1)` 했는데, **그 글이 갈 곳이
/// 없다** — 사용자에게는 "아이콘을 눌렀는데 아무 일도 안 일어난다"로 보인다. 데모 폴백을
/// 걷어낸 자리(§10-20ⓓ)라 더 아프다: 종전에는 적어도 창은 떴다.
///
/// 콘솔이 있으면 이 함수는 아무것도 하지 않는다 — 글은 이미 보였고, 팝업이 한 번 더 뜨면
/// 터미널에서 쓰는 사람에게는 방해다.
#[cfg(windows)]
pub fn show_fatal(message: &str) {
    use windows::Win32::UI::WindowsAndMessaging::{MB_ICONERROR, MB_OK, MessageBoxW};
    use windows::core::HSTRING;

    let title = HSTRING::from(base::i18n::t("pytmux 를 시작하지 못했습니다"));
    let body = HSTRING::from(message);
    // SAFETY: 널 종료 와이드 문자열 둘을 넘기는 표준 호출. 창 주인은 없다(아직 창이 없다).
    unsafe {
        MessageBoxW(
            None,
            &body,
            &title,
            MB_OK | MB_ICONERROR,
        );
    }
}

/// 맥·리눅스에는 이 폴백이 없다 — 두 OS 모두 **터미널에서 띄우는 것이 정상 입구**이고
/// (`.app` 번들이 아직 없다), 없는 GUI 대화상자를 흉내 내면 의존만 는다.
#[cfg(not(windows))]
pub fn show_fatal(_message: &str) {}
