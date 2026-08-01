//! `command` — 프로세스 실행.
//!
//! **AGPL 원본을 대체하는 자체 구현(MIT).** 원본은 명령 실행에 텔레메트리·셸 통합
//! 계층을 얹은 크레이트지만, `warpui` 가 쓰는 것은 `blocking::Command` 와
//! `wsl::is_wsl` 둘뿐이다. 앞의 것은 **Linux 창 관리자 탐지**
//! (`windowing/winit/linux/window_manager.rs`), 뒤의 것은 **Linux 에서 URL 여는 전략**
//! (`windowing/winit/delegate.rs`)에서 쓴다. PROVENANCE.md §2.

/// 블로킹 프로세스 실행. `std::process` 의 것을 그대로 쓴다.
pub mod blocking {
    pub use std::process::{Child, Command, Output, Stdio};
}

/// WSL(Windows Subsystem for Linux) 탐지.
///
/// 상류가 `platform/linux/mod.rs::is_wsl()` 에서 부르고, 그 값이 URL 여는 전략을
/// 가른다(WSL 이면 `wslview`·`rundll32` 로 **윈도우 쪽** 브라우저를 열고, 아니면
/// 평범한 리눅스 경로). 셸 통합이 필요한 일이 아니라 커널 릴리스 문자열을 읽는
/// 일이라 자체 구현으로 충분하다.
pub mod wsl {
    use std::sync::OnceLock;

    /// 이 프로세스가 WSL 안에서 도는가.
    ///
    /// 판정 근거 둘 — 어느 하나면 참이다:
    ///
    /// 1. `WSL_DISTRO_NAME`·`WSL_INTEROP` 환경변수(WSL 이 로그인 셸에 심는다).
    /// 2. `/proc/sys/kernel/osrelease` 에 `microsoft` 가 들어 있다
    ///    (WSL1 `…-Microsoft`, WSL2 `…-microsoft-standard-WSL2` — **대소문자가
    ///    갈리므로 접어서 비교한다**).
    ///
    /// 환경변수만 보면 안 된다 — systemd 서비스·`sudo` 처럼 그 변수가 안 실려오는
    /// 자리가 있다. `/proc` 만 봐도 안 된다 — 컨테이너 안에서는 가려질 수 있다.
    ///
    /// ⚠ **`cfg(target_os)` 로 가르지 않는다.** 리눅스 밖에서는 두 근거가 모두
    /// 성립하지 않아 자연히 `false` 이고(`/proc` 이 없다), 가르지 않아야 **macOS·
    /// Windows 빌드에서도 이 코드가 타입 검사를 받는다** — 리눅스에서만 컴파일되는
    /// 코드가 조용히 썩는 것이 바로 이 함수가 없어서 생긴 일이다(2026-08-01, Linux
    /// 이진을 처음 구울 때 `E0433` 로 드러났다).
    pub fn is_wsl() -> bool {
        static CACHED: OnceLock<bool> = OnceLock::new();
        *CACHED.get_or_init(|| {
            from_parts(
                std::env::var_os("WSL_DISTRO_NAME").is_some()
                    || std::env::var_os("WSL_INTEROP").is_some(),
                std::fs::read_to_string("/proc/sys/kernel/osrelease")
                    .ok()
                    .as_deref(),
            )
        })
    }

    /// 판정 그 자체 — 환경도 파일도 안 읽는다.
    ///
    /// 갈라 둔 이유는 오라클이다. `is_wsl` 이 직접 `std::env`·`/proc` 를 읽으면
    /// 그 함수는 **도는 상자에서만** 재지고(이 맥에서는 늘 `false`), 세 갈래 중
    /// 한 갈래도 못 잰다. 여기서 갈라 두면 표본을 주입해 전수로 잰다.
    fn from_parts(env_marks_wsl: bool, osrelease: Option<&str>) -> bool {
        env_marks_wsl
            || osrelease.is_some_and(|s| s.to_ascii_lowercase().contains("microsoft"))
    }

    #[cfg(test)]
    mod tests {
        use super::from_parts;

        #[test]
        fn env_marker_alone_is_enough() {
            // WSL 이 로그인 셸에 심는 변수. `/proc` 를 못 읽는 자리에서도 참이다.
            assert!(from_parts(true, None));
        }

        #[test]
        fn osrelease_alone_is_enough_in_both_wsl_generations() {
            // WSL2 는 소문자, WSL1 은 **대문자 M** 이다 — 접어서 비교하지 않으면
            // WSL1 을 통째로 놓친다(이 한 줄이 이 오라클의 존재 이유다).
            assert!(from_parts(false, Some("5.15.90.1-microsoft-standard-WSL2")));
            assert!(from_parts(false, Some("4.4.0-19041-Microsoft")));
        }

        #[test]
        fn plain_linux_and_no_proc_are_not_wsl() {
            // 음성이 없으면 "늘 참"으로 만들어도 통과한다.
            assert!(!from_parts(false, Some("6.8.0-45-generic")));
            // macOS·Windows 에는 `/proc/sys/kernel/osrelease` 가 없다 → `None`.
            // 이 갈래가 `cfg(target_os)` 없이도 맞는 답을 내는 근거다.
            assert!(!from_parts(false, None));
        }
    }
}
