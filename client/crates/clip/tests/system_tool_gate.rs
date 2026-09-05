//! 게이트 — Windows 시스템 도구를 **이름으로** 띄우는 자리가 늘지 않는다.
//!
//! # 무엇을 막나 (검수 2026-08-09 B-3)
//!
//! Rust `std::process::Command` 는 Windows 에서 프로그램을 자기가 찾고, 그 차례에
//! **application path**(`current_exe()` 의 디렉터리)가 시스템 디렉터리보다 **앞**에 있다
//! (`library/std/src/sys/process/windows.rs::search_paths` · 1.92.0 원문). 이 제품은
//! 단일 실행 파일로 배포되고 사람들은 그것을 `Downloads` 에서 그대로 띄우므로, 그 폴더에
//! `powershell.exe` 한 장을 놓는 것으로 남의 코드가 돈다.
//!
//! 그래서 규칙은 하나다: **띄우기 전에 [`clip::system_tool`] 을 지난다.**
//!
//! # 왜 소스를 읽나
//!
//! 이 규칙은 **Windows 에서만 효과가 있고 이 상자는 macOS 다**. 동작으로 재려면 그 OS 가
//! 필요한데, 그러면 「그 OS 에서만 재는 규칙」이 되고 이 저장소는 그 부류로 이미 여러 번
//! 넘어졌다(`check_licenses.sh` 가 Windows 에서 아무것도 안 재고 통과하던 자리 ·
//! 셸 게이트가 WSL 런처를 집던 자리). 소스를 읽으면 **어느 상자에서도** 재진다.
//!
//! ⚠ 사각지대는 안다 — 이름을 변수에 담아 넘기면 이 자가 못 본다. 그래서 규칙을 **띄우는
//! 함수 한 곳**(`feed`)에 두었고, 여기서는 그 한 곳을 우회하는 새 자리가 생기는 것을 잡는다.
//! ⛔ 상류 스냅샷(`warpui`·`warpui_core`)은 대상이 아니다 — 우리 코드가 아니고, 우리
//! 제품의 링크 열기는 `proto::info::open_link` 를 지난다(`warpui` 의 `open_url_in_system`
//! 은 우리 경로에 없다). 그 사실이 바뀌면 아래 `OURS` 에 크레이트를 더한다.

use std::path::{Path, PathBuf};

/// 우리가 쓴 크레이트(상류 스냅샷 제외).
const OURS: &[&str] = &[
    "base", "proto", "claude", "clip", "gui", "command", "markdown_parser",
];

/// `%SystemRoot%` 아래에 사는 이름들 — 이대로 `Command::new` 에 넘기면 안 된다.
const SYSTEM_TOOLS: &[&str] = &["explorer", "clip", "cmd", "rundll32", "powershell"];

fn crates_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("crates/")
        .to_path_buf()
}

fn rs_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for e in entries.flatten() {
        let p = e.path();
        if p.is_dir() {
            rs_files(&p, out);
        } else if p.extension().is_some_and(|x| x == "rs") {
            out.push(p);
        }
    }
}

#[test]
fn no_one_spawns_a_windows_system_tool_by_bare_name() {
    let mut files = Vec::new();
    for name in OURS {
        rs_files(&crates_dir().join(name), &mut files);
    }
    assert!(files.len() > 50, "소스를 못 읽었다면 통과가 아니라 고장이다: {}", files.len());

    let mut bad = Vec::new();
    for path in &files {
        let Ok(text) = std::fs::read_to_string(path) else {
            continue;
        };
        for (i, line) in text.lines().enumerate() {
            for tool in SYSTEM_TOOLS {
                for lit in [format!("Command::new(\"{tool}\")"),
                            format!("Command::new(\"{tool}.exe\")")] {
                    if line.contains(&lit) {
                        bad.push(format!("{}:{} — {}", path.display(), i + 1, lit));
                    }
                }
            }
        }
    }
    assert!(
        bad.is_empty(),
        "Windows 시스템 도구를 이름으로 띄운다 — clip::system_tool 을 지날 것:\n  {}",
        bad.join("\n  ")
    );
}

/// `Command::new(` 를 그냥 써도 되는 자리와 **그 근거**. 근거를 못 적으면 못 쓴다.
///
/// ⛔ 위의 이름 검사만으로는 부족하다 — 실제 결함은 `Command::new(opener)` 처럼 **변수**
/// 였고 리터럴 검사는 그것을 한 줄도 못 봤다(고치기 전에 돌려 봐서 안다). 그래서 이쪽은
/// 반대로 센다: 우리 `src/` 의 `Command::new(` 는 **전부** 여기 이름이 있거나
/// `system_tool(` 을 지나야 한다.
const SPAWN_ALLOW: &[(&str, &str, &str)] = &[
    (
        "proto/src/boot.rs",
        "Command::new(&argv[0])",
        "argv[0] 은 boot::which 가 PATH 에서 찾아 준 **절대경로**이거나 $PYTMUX_BIN 이다",
    ),
    (
        "base/src/restart.rs",
        "Command::new(exe)",
        "current_exe() — 절대경로다(ⓓ3 재시작)",
    ),
    (
        "clip/src/lib.rs",
        "Command::new(&argv[0])",
        "run_shell — argv[0] 은 $COMSPEC·system_tool(\"cmd\")·/bin/sh 셋뿐이다",
    ),
    (
        "proto/src/info.rs",
        "Command::new(opener)",
        "opener 는 **바로 위 줄**의 `let Some(opener) = clip::system_tool(…) else \
         { return false; }` 가 낸 값이다 — 못 찾으면 안 띄운다(fail-closed · \
         검수 2026-09-05 G-5). 한 줄에 못 담는 이유가 그 fail-closed 자체다.",
    ),
];

#[test]
fn every_spawn_in_our_source_is_either_guarded_or_argued_for() {
    let mut files = Vec::new();
    for name in OURS {
        rs_files(&crates_dir().join(name).join("src"), &mut files);
    }
    assert!(files.len() > 30, "소스를 못 읽었다면 통과가 아니라 고장이다: {}", files.len());

    let root = crates_dir();
    let mut unguarded = Vec::new();
    let mut used = vec![false; SPAWN_ALLOW.len()];
    for path in &files {
        // 시험 파일은 대상이 아니다 — 입력을 합성하려고 프로그램을 띄우는 것이 정상이다.
        if path.to_string_lossy().contains("_tests.rs") {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(path) else {
            continue;
        };
        let rel = path.strip_prefix(&root).unwrap_or(path).to_string_lossy()
            .replace('\\', "/");
        for (i, line) in text.lines().enumerate() {
            if !line.contains("Command::new(") || line.trim_start().starts_with("//") {
                continue;
            }
            if line.contains("system_tool(") {
                continue;
            }
            match SPAWN_ALLOW.iter().position(|(f, marker, _)| {
                rel == *f && line.contains(marker)
            }) {
                Some(k) => used[k] = true,
                None => unguarded.push(format!("{rel}:{} — {}", i + 1, line.trim())),
            }
        }
    }
    assert!(
        unguarded.is_empty(),
        "가드도 근거도 없이 프로그램을 띄운다 — clip::system_tool 을 지나거나 \
         SPAWN_ALLOW 에 근거를 적을 것:\n  {}",
        unguarded.join("\n  ")
    );
    // ⛔ 안 쓰이는 예외는 지운다 — 남겨 두면 다음 사람이 그 자리가 아직 있다고 읽는다.
    let stale: Vec<&str> = SPAWN_ALLOW.iter().zip(&used)
        .filter(|(_, u)| !**u).map(|((f, _, _), _)| *f).collect();
    assert!(stale.is_empty(), "SPAWN_ALLOW 에 죽은 항목이 있다: {stale:?}");
}

#[test]
fn the_mapping_is_absolute_and_only_for_names_we_know() {
    // 표는 순수 함수라 **이 상자에서도** 잰다(그것이 이 파일이 있는 이유다).
    let root = Some("C:\\WINDOWS");
    assert_eq!(
        clip::system_tool_at(root, "powershell").as_deref(),
        Some("C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe")
    );
    // 탐색기만 System32 가 아니다.
    assert_eq!(
        clip::system_tool_at(root, "explorer").as_deref(),
        Some("C:\\WINDOWS\\explorer.exe")
    );
    assert_eq!(
        clip::system_tool_at(root, "clip.exe").as_deref(),
        Some("C:\\WINDOWS\\System32\\clip.exe")
    );
    // 뒤 구분자를 붙여 줘도 두 번 안 찍는다.
    assert_eq!(
        clip::system_tool_at(Some("C:\\WINDOWS\\"), "cmd").as_deref(),
        Some("C:\\WINDOWS\\System32\\cmd.exe")
    );
    // 모르는 이름·루트 없음·빈 루트 → None(= 부르는 쪽이 이름 그대로 쓴다).
    assert_eq!(clip::system_tool_at(root, "pbcopy"), None);
    assert_eq!(clip::system_tool_at(None, "cmd"), None);
    assert_eq!(clip::system_tool_at(Some("  "), "cmd"), None);
}

#[test]
fn on_this_box_the_name_passes_through_unchanged() {
    // ⛔ Windows 가 아닌 곳에서 경로를 지어내면 **복사가 통째로 죽는다**
    //    (`pbcopy` 가 `C:\...\pbcopy.exe` 가 되는 부류). 여기서 그것을 못 박는다.
    if !cfg!(windows) {
        for name in ["pbcopy", "xclip", "wl-copy", "clip", "powershell"] {
            assert_eq!(clip::system_tool(name).as_deref(), Some(name));
        }
    }
}

/// ☠ 검수 2026-09-05 G-5 — **못 찾으면 이름으로 되돌아가지 않는다**(fail-closed).
///
/// 종전에는 `%SystemRoot%` 가 없거나 그 자리에 파일이 없으면 맨 이름을 돌려줬다.
/// 그런데 이 함수가 막으려던 것이 바로 **그 맨 이름의 탐색**이다(이진 옆 폴더의
/// `clip.exe`·`powershell.exe` 가 시스템 것보다 먼저 잡힌다 — 검수 2026-08-09 B-3).
/// 폴백은 그 구멍을 그대로 다시 연다.
#[test]
fn a_tool_we_pinned_is_never_fetched_by_bare_name() {
    // 표에 있는 이름 = 우리가 자리를 못박은 것. 루트가 없으면 **경로가 없다**.
    for name in ["clip", "cmd", "powershell", "explorer", "rundll32"] {
        assert!(clip::system_tool_tail(name).is_some(), "{name} 이 표에서 빠졌다");
        assert_eq!(clip::system_tool_at(None, name), None, "{name}");
        assert_eq!(clip::system_tool_at(Some(""), name), None, "{name}");
    }
    // 표 **밖**의 이름은 종전대로 그대로 쓴다 — 못박은 적 없는 것까지 막으면 POSIX
    // 도구(`pbcopy`·`xclip`·`open`)가 통째로 죽는다.
    for name in ["pbcopy", "xclip", "wl-copy", "open", "xdg-open", "pwsh"] {
        assert_eq!(clip::system_tool_tail(name), None, "{name}");
        assert_eq!(clip::system_tool(name).as_deref(), Some(name), "{name}");
    }
}
