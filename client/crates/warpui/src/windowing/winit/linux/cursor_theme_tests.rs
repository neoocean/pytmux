//! `CursorThemeCrawler` 의 테마 해석 순서.
//!
//! **상류는 이 파일을 `virtual-fs`(AGPL) 로 썼다.** 그 크레이트를 라이선스 경계에서
//! 빼면서(PROVENANCE §2) 의존만 지우고 **이 파일은 남겨 뒀는데**, 이 파일은 Linux
//! 전용(`#[cfg(target_os = "linux")]` 아래)이라 macOS·Windows 에서는 컴파일조차
//! 되지 않아 **몇 달간 아무도 몰랐다.** 2026-08-01 에 CI 가 Linux 를 처음 돌리자
//! `E0432`(그 크레이트 없음) + `E0282` 로 한꺼번에 드러났다.
//!
//! `Cargo.toml` 의 그 주석이 "Linux 를 대상 OS 로 고르면 tempfile 기반으로 다시
//! 써서 되살린다"고 적어 뒀고, 지금이 그때다(`pytmux-gui-linux-x64` 를 CI 가
//! 굽는다). 다만 **의존은 늘리지 않았다** — 여기 필요한 것은 디렉토리 몇 개와
//! 파일 두 개뿐이라 표준 라이브러리로 충분하다.

use std::fs;
use std::path::{Path, PathBuf};

use super::CursorThemeCrawler;

/// 테스트 하나가 쓰는 임시 트리. 떨어질 때 스스로 지운다.
///
/// 이름에 **테스트 이름과 pid** 를 넣는다 — `cargo test` 는 테스트를 스레드로
/// 병렬 실행하고, CI 는 같은 상자에서 여러 잡을 돌린다. 고정 이름을 쓰면 서로의
/// 트리를 지우며 **간헐로만** 붉어진다.
struct Sandbox {
    root: PathBuf,
}

impl Sandbox {
    fn new(name: &str) -> Self {
        let root = std::env::temp_dir()
            .join(format!("pytmux-cursor-theme-{}-{}", name, std::process::id()));
        // 앞선 run 이 패닉으로 남긴 것이 있으면 지우고 시작한다(있어도 없어도 된다).
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("임시 트리를 못 만들었다");
        Self { root }
    }

    /// 트리 안의 절대 경로. 상류의 `dirs.tests().join(rel)` 자리다.
    fn path(&self, rel: &str) -> PathBuf {
        self.root.join(rel)
    }

    fn mkdir(&self, rel: &str) {
        fs::create_dir_all(self.path(rel)).expect("디렉토리를 못 만들었다");
    }

    /// 상류의 `Stub::FileWithContent`. 내용은 **상류 그대로** 둔다(앞의 공백까지)
    /// — 파서가 그 모양을 견디는지도 이 테스트가 재는 것 중 하나다.
    fn write(&self, rel: &str, content: &str) {
        let path = self.path(rel);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).expect("상위 디렉토리를 못 만들었다");
        }
        fs::write(path, content).expect("파일을 못 썼다");
    }
}

impl Drop for Sandbox {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn crawler(dirs: Vec<PathBuf>) -> CursorThemeCrawler {
    CursorThemeCrawler { directories: dirs }
}

fn icons(sb: &Sandbox, names: &[&str]) -> Vec<PathBuf> {
    names.iter().map(|n| sb.path(n)).collect()
}

const INHERITS_DARMOK: &str = r#"
            [Icon Theme]
            Inherits=Darmok
            "#;

#[test]
fn test_no_themes_found() {
    let sb = Sandbox::new("no_themes_found");
    sb.mkdir("icons");

    assert_eq!(crawler(icons(&sb, &["icons"])).determine_cursor_theme(), None);
}

#[test]
fn test_default_theme_found() {
    let sb = Sandbox::new("default_theme_found");
    sb.mkdir("icons/default/cursors");

    assert_eq!(
        crawler(icons(&sb, &["icons"])).determine_cursor_theme(),
        Some("default".to_string())
    );
}

#[test]
fn test_known_theme_found() {
    let sb = Sandbox::new("known_theme_found");
    sb.mkdir("icons/Yaru/cursors");

    assert_eq!(
        crawler(icons(&sb, &["icons"])).determine_cursor_theme(),
        Some("Yaru".to_string())
    );
}

#[test]
fn test_default_theme_found_via_index() {
    let sb = Sandbox::new("default_theme_found_via_index");
    sb.mkdir("icons/Darmok/cursors");
    sb.mkdir("icons/default");
    sb.write("icons/default/index.theme", INHERITS_DARMOK);

    assert_eq!(
        crawler(icons(&sb, &["icons"])).determine_cursor_theme(),
        Some("default".to_string())
    );
}

#[test]
fn test_default_theme_is_prioritized_over_known_theme() {
    let sb = Sandbox::new("default_prioritized_over_known");
    sb.mkdir("icons/Darmok/cursors");
    sb.mkdir("icons/Yaru/cursors");
    sb.mkdir("icons/default");
    sb.write("icons/default/index.theme", INHERITS_DARMOK);

    assert_eq!(
        crawler(icons(&sb, &["icons"])).determine_cursor_theme(),
        Some("default".to_string())
    );
}

#[test]
fn test_multiple_directories() {
    let sb = Sandbox::new("multiple_directories");
    sb.mkdir("icons2/Darmok/cursors");
    sb.mkdir("icons/default");
    sb.write("icons/default/index.theme", INHERITS_DARMOK);

    assert_eq!(
        crawler(icons(&sb, &["icons", "icons2"])).determine_cursor_theme(),
        Some("default".to_string())
    );
}

#[test]
fn test_resolution_order() {
    let sb = Sandbox::new("resolution_order");
    sb.mkdir("icons2/Darmok/cursors");
    sb.mkdir("icons/default");
    sb.mkdir("icons2/default");
    sb.write(
        "icons/default/index.theme",
        r#"
                [Icon Theme]
                Inherits=Jalad
                "#,
    );
    sb.write(
        "icons2/default/index.theme",
        r#"
                [Icon Theme]
                Inherits=Darmok
                "#,
    );

    // Case 1: `icons` 의 index 를 먼저 찾는다. 그것이 가리키는 `Jalad` 는 없으므로 None.
    assert_eq!(
        crawler(icons(&sb, &["icons", "icons2"])).determine_cursor_theme(),
        None
    );

    // Case 2: `icons2` 의 index 를 먼저 찾는다. 그것이 가리키는 `Darmok` 은 있다.
    assert_eq!(
        crawler(icons(&sb, &["icons2", "icons"])).determine_cursor_theme(),
        Some("default".to_string())
    );
}

/// 위 일곱이 쓰는 샌드박스가 **실제로 격리되는가**. 상류 harness 를 우리 것으로
/// 갈아 끼웠으니 그 대체물 자체를 한 번 잰다 — 안 그러면 `mkdir` 이 아무 데도 안
/// 만들어도 `determine_cursor_theme()` 이 `None` 을 돌려 **전부 초록**일 수 있다.
#[test]
fn sandbox_isolates_and_cleans_up() {
    let leftover;
    {
        let sb = Sandbox::new("isolation");
        sb.mkdir("icons/default/cursors");
        let made = sb.path("icons/default/cursors");
        assert!(made.is_dir(), "mkdir 이 실제로 만들지 않았다: {made:?}");

        sb.write("icons/default/index.theme", INHERITS_DARMOK);
        let content = fs::read_to_string(sb.path("icons/default/index.theme")).unwrap();
        assert!(content.contains("Inherits=Darmok"), "{content:?}");

        leftover = sb.root.clone();
    }
    assert!(!Path::new(&leftover).exists(), "떨어질 때 안 지웠다: {leftover:?}");
}
