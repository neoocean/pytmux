//! 이 클라가 **어느 이진인가** — 배포 이름·플랫폼, 그리고 이 프로세스의 가동 시간.
//!
//! # 왜 필요한가 (§10-21ⓐ3)
//!
//! 버전 판이 보여 주던 줄은 **서버가 지은 것 하나**뿐이었다(`Server p4:… · up … · pid …`).
//! 그래서 화면 어디에도 **내가 지금 어느 이진을 보고 있나**가 안 나온다 — 상자에 여러
//! 판(윈도우·맥·리눅스)이 굴러다니는데 화면은 그 셋을 구분해 주지 않았다.
//!
//! ★ 서버와 클라는 **다른 OS 일 수 있다**(페더레이션·원격 attach). 그러니 이 값은 서버
//! 줄에 끼워 넣을 것이 아니라 **클라 줄**로 따로 서야 한다 — 두 줄이면 그 차이가 화면에
//! 그대로 드러난다.
//!
//! # 왜 core 인가
//!
//! "내가 무엇인가"는 판단이고 표기 규약이다(`client/build/README.md` 의 이름 표). 뷰가
//! 각자 적으면 같은 이진이 판마다 다른 이름으로 불린다.

use std::sync::OnceLock;
use std::time::{Duration, Instant};

/// 이 워크스페이스가 내는 이진의 이름. 산출물은 **하나**다(2026-08-01 에 Rust TUI 를
/// 지웠다 — `client/CLAUDE.md` 「무엇인가」).
pub const NAME: &str = "pytmux-gui";

/// 배포 이름의 OS 자리 — `client/build/README.md` 의 표와 같은 낱말.
///
/// 모르는 OS 는 `std` 의 이름을 그대로 쓴다. 억지로 셋 중 하나로 접으면 **거짓말을
/// 화면에 적게 된다**(freebsd 를 linux 로 적는 식).
pub fn os_slug() -> &'static str {
    match std::env::consts::OS {
        "macos" => "macos",
        other => other,
    }
}

/// 배포 이름의 아키텍처 자리 — 표의 `x64`·`arm64` 표기.
pub fn arch_slug() -> &'static str {
    match std::env::consts::ARCH {
        "x86_64" => "x64",
        "aarch64" => "arm64",
        other => other,
    }
}

/// 이 이진의 배포 이름 — 예: `pytmux-gui-windows-x64`.
///
/// `build/` 에 놓이는 파일 이름과 **같은 규칙**이라, 화면에 뜬 이름으로 어느 파일을
/// 받아야 하는지 바로 알 수 있다.
pub fn artifact() -> String {
    format!("{NAME}-{}-{}", os_slug(), arch_slug())
}

/// 사람이 읽을 OS 이름 — `Windows` · `macOS` · `Linux`.
///
/// 슬러그(`windows`)와 따로 두는 이유: 슬러그는 파일 이름의 일부라 소문자가 규약이고,
/// 화면에 적는 이름은 그 OS 가 스스로를 부르는 철자다.
pub fn os_label() -> &'static str {
    match std::env::consts::OS {
        "windows" => "Windows",
        "macos" => "macOS",
        "linux" => "Linux",
        other => other,
    }
}

/// 이 프로세스가 뜬 시각. 첫 호출에서 못박히므로 **기동 직후 한 번 부른다**
/// ([`mark_start`]) — 안 그러면 "버전 판을 처음 연 시각"이 기동 시각이 된다.
pub fn started() -> Instant {
    static START: OnceLock<Instant> = OnceLock::new();
    *START.get_or_init(Instant::now)
}

/// 기동 시각을 지금으로 못박는다. `main` 이 맨 앞에서 부른다.
pub fn mark_start() {
    let _ = started();
}

/// 이 클라가 떠 있은 시간.
pub fn uptime() -> Duration {
    started().elapsed()
}

/// 초를 `1d 02:03:04` / `02:03:04` 로 — **정본 `version.fmt_uptime` 과 같은 모양**.
///
/// 두 클라가 같은 판에서 다른 모양의 시간을 적으면 대조할 때마다 사람이 환산해야 한다.
/// 음수는 0 으로 접는다(서버 업타임을 회신 이후 경과로 외삽하다 시계가 뒤로 가는 자리).
pub fn fmt_uptime(seconds: f64) -> String {
    let total = if seconds.is_finite() && seconds > 0.0 { seconds as u64 } else { 0 };
    let (days, rest) = (total / 86_400, total % 86_400);
    let (hours, rest) = (rest / 3_600, rest % 3_600);
    let (minutes, secs) = (rest / 60, rest % 60);
    let hms = format!("{hours:02}:{minutes:02}:{secs:02}");
    if days > 0 { format!("{days}d {hms}") } else { hms }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// ★ 이름 규약이 문서와 갈리지 않게. `build/README.md` 의 표가 배포 이름의 정본이고,
    /// 코드가 다른 이름을 지으면 화면에 뜬 이름으로 파일을 못 찾는다.
    #[test]
    fn the_artifact_name_is_one_the_release_table_knows() {
        let readme = include_str!("../../../build/README.md");
        // 이 상자에서 나올 이름이 표에 있어야 한다(확장자는 OS 마다 달라 뺀다).
        assert!(
            readme.contains(&artifact()),
            "배포 표에 없는 이름을 짓는다: {} — build/README.md 와 한쪽이 낡았다",
            artifact()
        );
    }

    /// 세 OS 의 이름이 전부 표에 있는가 — 이 상자에서 도는 한 갈래만 재면 나머지 둘은
    /// 아무도 안 잰다(크로스 컴파일 게이트가 못 잡는 자리다. 문자열이라 컴파일은 된다).
    #[test]
    fn every_shipped_platform_is_in_the_release_table() {
        let readme = include_str!("../../../build/README.md");
        for name in ["pytmux-gui-windows-x64", "pytmux-gui-macos-arm64", "pytmux-gui-linux-x64"] {
            assert!(readme.contains(name), "배포 표에 {name} 이 없다");
        }
    }

    #[test]
    fn the_uptime_looks_like_canon() {
        // 정본 `version.fmt_uptime` 과 같은 모양이라야 두 판을 눈으로 견줄 수 있다.
        assert_eq!(fmt_uptime(0.0), "00:00:00");
        assert_eq!(fmt_uptime(59.9), "00:00:59");
        assert_eq!(fmt_uptime(3_723.0), "01:02:03");
        assert_eq!(fmt_uptime(86_400.0 + 3_723.0), "1d 01:02:03");
        // 음수·NaN 은 0 으로 접는다 — 화면에 `-1d` 가 뜨는 것보다 낫다.
        assert_eq!(fmt_uptime(-5.0), "00:00:00");
        assert_eq!(fmt_uptime(f64::NAN), "00:00:00");
    }

    #[test]
    fn the_start_instant_is_nailed_down_on_the_first_call() {
        // 두 번째 호출이 다른 값을 주면 업타임이 늘 0 근처가 된다.
        let first = started();
        assert_eq!(first, started());
        assert!(uptime() >= Duration::ZERO);
    }

    #[test]
    fn the_os_label_and_slug_are_not_the_same_spelling_by_accident() {
        // 슬러그는 파일 이름의 일부(소문자), 라벨은 사람이 읽는 철자다.
        assert!(!os_slug().is_empty());
        assert!(!os_label().is_empty());
        assert_eq!(os_slug(), os_slug().to_lowercase());
    }
}
