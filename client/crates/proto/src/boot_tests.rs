//! `boot` 의 계약 — 무엇을 부르나 · 언제 띄우나 · 실패 사유가 살아남나.

use std::path::PathBuf;

use super::*;

fn search(bin: Option<&str>, on_path: Option<&str>, script: Option<&str>, python: Option<&str>) -> Search {
    Search {
        bin: bin.map(str::to_owned),
        on_path: on_path.map(PathBuf::from),
        script: script.map(PathBuf::from),
        python: python.map(str::to_owned),
    }
}

#[test]
fn an_explicit_binary_wins_over_the_installed_one_and_the_tree() {
    // 순서가 뒤집히면 **설치본을 쓰는 사용자가 옆 트리의 낡은 정본으로** 서버를 띄운다.
    let all = search(Some("/opt/pytmux"), Some("/usr/bin/pytmux"), Some("/src/pytmux.py"), Some("python3"));
    assert_eq!(all.launcher(), Ok(Launcher(vec!["/opt/pytmux".to_owned()])));

    let installed = search(None, Some("/usr/bin/pytmux"), Some("/src/pytmux.py"), Some("python3"));
    assert_eq!(installed.launcher(), Ok(Launcher(vec!["/usr/bin/pytmux".to_owned()])));

    let tree = search(None, None, Some("/src/pytmux.py"), Some("python3"));
    assert_eq!(
        tree.launcher(),
        Ok(Launcher(vec!["python3".to_owned(), "/src/pytmux.py".to_owned()]))
    );
}

#[test]
fn a_box_without_python_is_told_that_and_not_that_pytmux_is_missing() {
    // ★ 서버는 파이썬이다. 이 클라는 네이티브 이진이라 파이썬 없는 상자에도 설치되고,
    //   **그 상자에서는 서버를 띄울 수 없다**. 두 갈래를 한 문장으로 뭉치면 사용자가
    //   할 일이 뒤바뀐다("pytmux 를 설치" ≠ "파이썬을 설치").
    let no_python = search(None, None, Some("/src/pytmux.py"), None);
    assert_eq!(
        no_python.launcher(),
        Err(Missing::Python {
            script: PathBuf::from("/src/pytmux.py")
        })
    );
    assert!(matches!(
        search(None, None, None, Some("python3")).launcher(),
        Err(Missing::Nothing { .. })
    ));
    assert!(matches!(
        Search::default().launcher(),
        Err(Missing::Nothing { .. })
    ));
}

#[test]
fn a_dead_end_message_always_offers_what_is_still_possible() {
    // 파이썬이 없어도 이 클라는 **이미 떠 있는 서버**에 붙을 수 있다. 그 길을 안
    // 적으면 사용자가 보는 것은 막다른 길이다.
    for missing in [
        Missing::Python {
            script: PathBuf::from("/src/pytmux.py"),
        },
        Missing::Nothing {
            tried: "…".to_owned(),
        },
    ] {
        let text = BootError::NoLauncher(missing).to_string();
        assert!(text.contains("--socket"), "남은 길이 없다: {text}");
        assert!(text.contains("demo"), "남은 길이 없다: {text}");
    }
    // 파이썬 갈래는 **파이썬**을 말해야 한다.
    let text = BootError::NoLauncher(Missing::Python {
        script: PathBuf::from("/src/pytmux.py"),
    })
    .to_string();
    assert!(text.contains("파이썬"), "무엇이 없는지 안 말한다: {text}");
    assert!(text.contains("/src/pytmux.py"), "어느 정본인지 안 말한다: {text}");
}

#[test]
fn an_empty_env_override_is_not_a_launcher() {
    // `PYTMUX_BIN=` 로 비워 둔 것은 지정이 아니다 — 그대로 쓰면 빈 argv 로 spawn 한다.
    let s = search(Some(""), Some("/usr/bin/pytmux"), None, None);
    assert_eq!(s.launcher(), Ok(Launcher(vec!["/usr/bin/pytmux".to_owned()])));
}

#[test]
fn we_call_the_canonical_subcommand() {
    // 정본의 `start-server` 는 **멱등**이고 인증까지 기다린다. 다른 이름을 부르면
    // (예: `server`) 전경 데몬이 떠서 GUI 가 그 자리에 매달린다.
    let launcher = Launcher(vec!["pytmux".to_owned()]);
    assert_eq!(
        launcher.start_server_argv(),
        vec!["pytmux".to_owned(), "start-server".to_owned()]
    );
}

#[test]
fn only_a_missing_server_makes_us_start_one() {
    // 핸드셰이크 실패는 **서버가 대답했다**는 뜻이다. 거기에 한 벌을 더 띄우면
    // 사용자는 자기 탭이 사라진 화면을 본다.
    let io = || std::io::Error::other("boom");
    assert!(means_no_server(&AttachError::NoServer("후보들".into())));
    assert!(means_no_server(&AttachError::Connect {
        endpoint: "tcp:127.0.0.1:1".into(),
        source: io(),
    }));
    assert!(means_no_server(&AttachError::NoPort("default.port".into())));
    assert!(means_no_server(&AttachError::NoToken("default.token".into())));
    assert!(
        !means_no_server(&AttachError::Handshake(crate::framing::FrameError::Closed)),
        "대답한 서버 옆에 새 서버를 띄우면 안 된다"
    );
}

// --- 실제로 부른다 ---------------------------------------------------------
//
// 위 넷은 **무엇을 부를지**만 잰다. 부르는 코드(파이프·플래그·종료코드·사유 회수)는
// 한 번도 안 지나간다 — 이 저장소가 같은 자리에서 두 번 물린 모양이다(2026-08-02j:
// "손으로 만든 req 로만 재면 서버 배선은 안 재진다"). 그래서 가짜 런처를 실제로 띄운다.

/// 종료코드와 stderr 를 지정한 가짜 런처를 만들어 그 경로를 돌려준다.
fn fake_launcher(tag: &str, message: &str, code: i32) -> (PathBuf, PathBuf) {
    let dir = std::env::temp_dir().join(format!("pytmux-boot-{}-{}", std::process::id(), tag));
    std::fs::create_dir_all(&dir).expect("임시 디렉터리");
    let (name, body) = if cfg!(windows) {
        (
            "fake.cmd",
            format!("@echo off\r\necho {message} 1>&2\r\nexit /b {code}\r\n"),
        )
    } else {
        (
            "fake.sh",
            format!("#!/bin/sh\necho '{message}' >&2\nexit {code}\n"),
        )
    };
    let path = dir.join(name);
    std::fs::write(&path, body).expect("가짜 런처 쓰기");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).expect("실행 권한");
    }
    (dir, path)
}

#[test]
fn the_reason_the_canonical_launcher_printed_reaches_the_user() {
    // 정본은 실패 사유를 stderr 마지막 줄로 낸다(boot.log 에서 뽑은 것). 그 줄을
    // 흘리면 사용자에게 남는 것은 "안 뜬다" 뿐이다.
    let (dir, path) = fake_launcher("fail", "의존성이 없습니다", 1);
    let search = Search {
        bin: Some(path.display().to_string()),
        ..Default::default()
    };
    let err = start_server_with(&search).expect_err("실패해야 한다");
    match err {
        BootError::StartFailed { detail } => {
            assert!(detail.contains("의존성이 없습니다"), "사유가 사라졌다: {detail}");
        }
        other => panic!("실패 사유를 다른 것으로 읽었다: {other:?}"),
    }
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn a_launcher_that_succeeds_is_reported_as_success() {
    let (dir, path) = fake_launcher("ok", "서버 기동됨", 0);
    let search = Search {
        bin: Some(path.display().to_string()),
        ..Default::default()
    };
    assert!(start_server_with(&search).is_ok(), "성공을 실패로 읽었다");
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn without_a_launcher_we_say_what_we_looked_for() {
    // 못 찾은 것은 사용자가 고칠 수 있는 상태다(설치하거나 `$PYTMUX_BIN`). 그러려면
    // 무엇을 봤는지가 메시지에 있어야 한다.
    let err = start_server_with(&Search::default()).expect_err("런처가 없다");
    let text = err.to_string();
    assert!(matches!(err, BootError::NoLauncher(Missing::Nothing { .. })));
    assert!(text.contains("PYTMUX_BIN"), "찾아본 곳이 없다: {text}");
}

#[test]
fn the_last_nonempty_line_is_the_reason() {
    // 파이썬 쪽이 여러 줄을 낼 수 있다(경고 + 사유). 사유는 마지막 줄이다.
    assert_eq!(last_line(b"\n first \n\n  last  \n\n"), Some("last".to_owned()));
    assert_eq!(last_line(b"   \n\n"), None);
    assert_eq!(last_line(b""), None);
}
