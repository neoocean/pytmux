//! 엔드포인트 규칙 — **두 OS 규칙을 어느 호스트에서나** 본다.
//!
//! `Rules` 가 환경을 주입받으므로 macOS 에서 Windows 규칙을 검증할 수 있다. 환경변수를
//! 실제로 만지지 않으므로 테스트끼리 간섭하지도 않는다(예전 판은 `set_var` 로 전역을
//! 흔들어 한 테스트에 몰아 넣어야 했다).

use super::*;

fn unix_rules() -> Rules {
    Rules {
        windows: false,
        xdg_runtime_dir: None,
        localappdata: None,
        pytmux_home: None,
        uid: 501,
        home: Some(PathBuf::from("/Users/me")),
    }
}

fn windows_rules() -> Rules {
    Rules {
        windows: true,
        localappdata: Some(PathBuf::from("C:\\Users\\me\\AppData\\Local")),
        ..unix_rules()
    }
}

/// 런마다 **제 몫의** 디렉터리(pytmux-424).
///
/// ⛔ 종전에는 이름이 `pytmux-endpoint-test` 로 고정이라 **같은 포트파일 한 장**을 두
/// 런이 나눠 썼다 — 한쪽이 `remove_file` 로 지운 찰나에 다른 쪽이 `looks_live()` 를
/// 물으면 「포트파일이 없으면 서버가 없다」가 거짓으로 뒤집힌다. 혼자 돌리면 안 나므로
/// 「부하 플레이크」로 읽히는 부류다.
fn scratch(name: &str) -> PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static SEQ: AtomicU64 = AtomicU64::new(0);
    std::env::temp_dir().join(format!(
        "pytmux-{name}-{}-{}",
        std::process::id(),
        SEQ.fetch_add(1, Ordering::Relaxed)
    ))
}

#[test]
fn unix_prefers_xdg_and_falls_back_to_tmp() {
    let rules = Rules {
        xdg_runtime_dir: Some(PathBuf::from("/run/user/1000")),
        ..unix_rules()
    };
    let c = rules.candidates();
    assert_eq!(c.len(), 2, "두 위치를 모두 봐야 한다: {c:?}");
    assert_eq!(
        c[0],
        Endpoint::Unix {
            path: "/run/user/1000/pytmux/default.sock".into(),
            token: "/run/user/1000/pytmux/default.sock.token".into(),
        }
    );
    // XDG 유무는 세션마다 갈린다 — 폴백을 안 보면 같은 서버를 못 찾아 새 서버가 뜬다.
    assert_eq!(
        c[1],
        Endpoint::Unix {
            path: "/tmp/pytmux-501/default.sock".into(),
            token: "/tmp/pytmux-501/default.sock.token".into(),
        }
    );
}

#[test]
fn unix_without_xdg_has_one_candidate() {
    let c = unix_rules().candidates();
    assert_eq!(c.len(), 1);
    assert_eq!(c[0].display(), "/tmp/pytmux-501/default.sock");
}

#[test]
fn pytmux_home_puts_runtime_under_state() {
    // 서버는 <home>/state 에 둔다(런타임·설정·DB 를 형제로 가른다). <home> 자체를
    // 가리키면 소켓을 못 찾는다 — 조용히 새 서버가 뜨는 부류의 버그다.
    let rules = Rules {
        pytmux_home: Some(PathBuf::from("/custom/home")),
        xdg_runtime_dir: Some(PathBuf::from("/run/user/1000")),
        ..unix_rules()
    };
    let c = rules.candidates();
    assert_eq!(c.len(), 1, "통합 홈이면 그 소켓 하나가 canonical 이다");
    // **`display()` 로 비교하지 않는다.** 이 경로는 `join` 으로 조립되므로 문자열에는
    // 호스트의 구분자가 그대로 박힌다 — Windows 에서는 `/custom/home\state\default.sock`
    // 이라 POSIX 문자열과 어긋나고, 그러면 규칙이 아니라 구분자를 보는 단언이 된다.
    // `Path` 로 비교하면 Windows 도 `/` 를 구분자로 쳐서 **규칙만** 남는다(위
    // `unix_prefers_xdg_and_falls_back_to_tmp` 와 같은 방식). 여기서 볼 것은 `state` 가
    // 끼는지다.
    assert_eq!(
        c[0],
        Endpoint::Unix {
            path: "/custom/home/state/default.sock".into(),
            token: "/custom/home/state/default.sock.token".into(),
        }
    );
}

#[test]
fn windows_uses_loopback_tcp_with_a_portfile() {
    let c = windows_rules().candidates();
    assert_eq!(c.len(), 1, "LOCALAPPDATA 는 안정적이라 후보가 하나다");
    let Endpoint::Tcp {
        host,
        port,
        portfile,
        token,
    } = &c[0]
    else {
        panic!("Windows 는 TCP 여야 한다: {c:?}");
    };
    assert_eq!(host, "127.0.0.1");
    assert_eq!(*port, 0, "0 = 포트파일을 읽어라(서버가 에페메럴 포트를 잡는다)");
    assert!(portfile.ends_with("pytmux/default.port"), "{portfile:?}");
    assert!(token.ends_with("pytmux/default.token"), "{token:?}");
}

#[test]
fn windows_pytmux_home_also_goes_under_state() {
    let rules = Rules {
        pytmux_home: Some(PathBuf::from("D:\\work\\.pytmux")),
        ..windows_rules()
    };
    let Endpoint::Tcp { portfile, .. } = &rules.candidates()[0] else {
        panic!("Windows 는 TCP");
    };
    assert!(
        portfile.ends_with("state/default.port"),
        "통합 홈에서도 런타임은 state 아래다: {portfile:?}"
    );
}

#[test]
fn a_live_looking_endpoint_is_decided_by_a_file_not_by_connecting() {
    // Windows 루프백에서 죽은 포트로 connect 하면 즉시 거절이 아니라 타임아웃까지
    // 매달린다(방화벽이 SYN 을 조용히 버린다) — 그래서 파일로 판정한다.
    let dir = scratch("endpoint-test");
    let _ = std::fs::create_dir_all(&dir);
    let portfile = dir.join("default.port");
    let _ = std::fs::remove_file(&portfile);
    let ep = Endpoint::Tcp {
        host: "127.0.0.1".into(),
        port: 0,
        portfile: portfile.clone(),
        token: dir.join("default.token"),
    };
    assert!(!ep.looks_live(), "포트파일이 없으면 서버가 없다");
    std::fs::write(&portfile, "54321\n").unwrap();
    assert!(ep.looks_live());
    assert_eq!(ep.resolve_port(), Some(54321), "포트파일에서 포트를 읽는다");
    let _ = std::fs::remove_file(&portfile);
}

#[test]
fn an_explicit_port_needs_no_portfile() {
    let ep = Endpoint::Tcp {
        host: "127.0.0.1".into(),
        port: 4242,
        portfile: PathBuf::from("/nonexistent"),
        token: PathBuf::from("/nonexistent"),
    };
    assert!(ep.looks_live());
    assert_eq!(ep.resolve_port(), Some(4242));
}

#[test]
fn junk_in_the_portfile_is_not_a_port() {
    let dir = scratch("endpoint-junk");
    let _ = std::fs::create_dir_all(&dir);
    let portfile = dir.join("default.port");
    std::fs::write(&portfile, "서버가 죽으며 남긴 쓰레기").unwrap();
    let ep = Endpoint::Tcp {
        host: "127.0.0.1".into(),
        port: 0,
        portfile: portfile.clone(),
        token: dir.join("default.token"),
    };
    assert_eq!(ep.resolve_port(), None);
    let _ = std::fs::remove_file(&portfile);
}

#[test]
fn explicit_specs_parse_both_shapes() {
    match parse("/tmp/x/default.sock") {
        Endpoint::Unix { path, token } => {
            assert_eq!(path, PathBuf::from("/tmp/x/default.sock"));
            assert_eq!(token, PathBuf::from("/tmp/x/default.sock.token"));
        }
        other => panic!("경로는 unix 소켓이다: {other:?}"),
    }
    match parse("tcp:127.0.0.1:5555") {
        Endpoint::Tcp { host, port, .. } => {
            assert_eq!(host, "127.0.0.1");
            assert_eq!(port, 5555);
        }
        other => panic!("tcp: 는 TCP 다: {other:?}"),
    }
}

#[test]
fn a_named_tcp_spec_moves_the_portfile_and_the_token_but_not_the_address() {
    // `tcp:[NAME@]HOST:PORT` — 이름은 **상태파일**만 가른다(서버 ipc.py 와 같은 규칙).
    // 한 상태 디렉터리에 서버가 둘 이상일 때 포트파일·토큰이 서로를 밟지 않는 자리다
    // (pytmux/pytmux-152). 규칙이 여기서 갈리면 클라가 남의 토큰을 내밀어 끊긴다.
    let dir = Rules::from_env().state_dir();
    match parse("tcp:srvA@127.0.0.1:5555") {
        Endpoint::Tcp {
            host,
            port,
            portfile,
            token,
        } => {
            assert_eq!(host, "127.0.0.1", "이름은 호스트에 안 섞인다");
            assert_eq!(port, 5555);
            assert_eq!(portfile, dir.join("srvA.port"));
            assert_eq!(token, dir.join("srvA.token"));
        }
        other => panic!("tcp: 는 TCP 다: {other:?}"),
    }
    // 이름을 안 쓰면 종전 그대로 — 발견 규약(`default`)은 안 움직인다.
    match parse("tcp:127.0.0.1:5555") {
        Endpoint::Tcp {
            portfile, token, ..
        } => {
            assert_eq!(portfile, dir.join("default.port"));
            assert_eq!(token, dir.join("default.token"));
        }
        other => panic!("tcp: 는 TCP 다: {other:?}"),
    }
    // 이름 규칙에 안 맞는 `@` 는 이름이 아니다 — 파일명으로 새면 상태 디렉터리 밖을
    // 읽는다. 그때는 `default` 자리를 쓰고 주소 해석은 connect 에 맡긴다.
    for spec in ["tcp:../../etc@127.0.0.1:5555", "tcp:a/b@127.0.0.1:5555"] {
        match parse(spec) {
            Endpoint::Tcp {
                portfile, token, ..
            } => {
                assert_eq!(portfile, dir.join("default.port"), "{spec}");
                assert_eq!(token, dir.join("default.token"), "{spec}");
            }
            other => panic!("tcp: 는 TCP 다: {other:?}"),
        }
    }
}

#[test]
fn what_display_prints_is_what_parse_takes_back() {
    // 오류 메시지에 뜬 문자열을 사람이 그대로 `--socket` 에 다시 칠 수 있어야 한다.
    for spec in ["tcp:127.0.0.1:5555", "tcp:srvA@127.0.0.1:5555"] {
        assert_eq!(parse(spec).display(), spec);
    }
    // 이름은 `.lang` 형제 파일까지 따라간다(파이썬 `state_base + ".lang"` 과 같은 자리).
    let dir = Rules::from_env().state_dir();
    assert_eq!(
        parse("tcp:srvA@127.0.0.1:5555").lang_file(),
        dir.join("srvA.lang")
    );
    // 마우스 진단 로그도 같은 `state_base` 를 쓴다(정본 `state_base + ".mouse.log"` · pytmux-575).
    for spec in ["tcp:srvA@127.0.0.1:5555", "/tmp/pytmux-x/default.sock"] {
        let ep = parse(spec);
        let lang = ep.lang_file().into_os_string().into_string().expect("utf-8");
        let base = lang.strip_suffix(".lang").expect("lang 은 state_base + .lang 이다");
        assert_eq!(ep.mouse_log_file(), std::path::PathBuf::from(format!("{base}.mouse.log")));
    }
}

#[test]
fn missing_token_file_is_not_an_error() {
    assert_eq!(read_token_at(Path::new("/nonexistent/default.token")), None);
}

// ── pytmux-586 G3: 고르는 것은 «파일이 있나» 가 아니라 «붙나» ──────────────────────────

/// AF_UNIX 경로 길이(맥 104바이트) 때문에 짧은 `/tmp` 아래에 짓는다.
#[cfg(unix)]
fn short_scratch(name: &str) -> PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static SEQ: AtomicU64 = AtomicU64::new(0);
    let dir = PathBuf::from(format!(
        "/tmp/pytmux-ep-{name}-{}-{}",
        std::process::id(),
        SEQ.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[cfg(unix)]
#[test]
fn a_dead_socket_file_is_not_chosen_over_a_live_server() {
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::net::UnixListener;
    let dir = short_scratch("probe");
    // 죽은 서버가 남긴 소켓 파일 — 묶었다가 놓으면 파일만 남는다(정본 서버가 죽은 자리).
    let dead = dir.join("dead.sock");
    drop(UnixListener::bind(&dead).unwrap());
    std::fs::set_permissions(&dead, std::fs::Permissions::from_mode(0o600)).unwrap();
    let live = dir.join("live.sock");
    let listener = UnixListener::bind(&live).unwrap();
    std::fs::set_permissions(&live, std::fs::Permissions::from_mode(0o600)).unwrap();
    let dead_ep = unix_endpoint(dead.clone());
    let live_ep = unix_endpoint(live.clone());
    assert!(dead_ep.looks_live(), "선행 조건: 파일은 남아 있다");
    assert!(!dead_ep.probe(), "죽은 소켓 파일을 산 것으로 읽었다");
    assert!(live_ep.probe(), "산 서버를 못 봤다");
    drop(listener);
    let _ = std::fs::remove_dir_all(&dir);
    let _ = (dead_ep, live_ep);
}

#[cfg(unix)]
#[test]
fn resolve_skips_a_dead_xdg_socket_and_finds_the_live_tmp_server() {
    // 대조표 §3-2 의 그 그림 — XDG 쪽에 죽은 소켓 파일, `/tmp/pytmux-<uid>` 쪽에 산 서버.
    // `uid` 를 이 런만의 가짜 값으로 지어 `/tmp` 자리도 시험 몫으로 만든다.
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::net::UnixListener;
    let xdg = short_scratch("xdg");
    let fake_uid = 3_000_000_000u32 + std::process::id();
    let tmp_dir = PathBuf::from(format!("/tmp/pytmux-{fake_uid}"));
    std::fs::create_dir_all(xdg.join("pytmux")).unwrap();
    std::fs::create_dir_all(&tmp_dir).unwrap();
    let dead = xdg.join("pytmux").join("default.sock");
    drop(UnixListener::bind(&dead).unwrap());
    std::fs::set_permissions(&dead, std::fs::Permissions::from_mode(0o600)).unwrap();
    let live = tmp_dir.join("default.sock");
    let _ = std::fs::remove_file(&live);
    let listener = UnixListener::bind(&live).unwrap();
    std::fs::set_permissions(&live, std::fs::Permissions::from_mode(0o600)).unwrap();
    let rules = Rules { xdg_runtime_dir: Some(xdg.clone()), uid: fake_uid, ..unix_rules() };
    assert_eq!(rules.candidates()[0], unix_endpoint(dead.clone()), "선행 조건: 죽은 쪽이 먼저");
    assert_eq!(rules.resolve(), Some(unix_endpoint(live.clone())));
    drop(listener);
    let _ = std::fs::remove_dir_all(&xdg);
    let _ = std::fs::remove_dir_all(&tmp_dir);
}

#[cfg(unix)]
#[test]
fn a_socket_open_to_others_or_a_symlink_is_refused_before_connecting() {
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::net::UnixListener;
    let dir = short_scratch("guard");
    let sock = dir.join("s.sock");
    let _listener = UnixListener::bind(&sock).unwrap();
    std::fs::set_permissions(&sock, std::fs::Permissions::from_mode(0o600)).unwrap();
    assert_eq!(validate_local_socket(&sock), Ok(()));
    assert!(unix_endpoint(sock.clone()).probe());
    // 남에게 열린 권한 — 정본 `S_IMODE & 0o077` 거절.
    std::fs::set_permissions(&sock, std::fs::Permissions::from_mode(0o666)).unwrap();
    let err = validate_local_socket(&sock).unwrap_err();
    assert!(err.contains("다른 사용자에게 열려"), "{err}");
    assert!(!unix_endpoint(sock.clone()).probe(), "열린 소켓에 붙었다");
    std::fs::set_permissions(&sock, std::fs::Permissions::from_mode(0o600)).unwrap();
    // 심볼릭 링크 — 따라가지 않는다.
    let link = dir.join("link.sock");
    std::os::unix::fs::symlink(&sock, &link).unwrap();
    assert!(validate_local_socket(&link).unwrap_err().contains("심볼릭 링크"));
    assert!(!unix_endpoint(link).probe());
    // 소켓이 아닌 파일.
    let plain = dir.join("plain.sock");
    std::fs::write(&plain, "x").unwrap();
    assert!(validate_local_socket(&plain).unwrap_err().contains("소켓이 아닌"));
    // 없는 파일은 이 검사의 관심사가 아니다(connect 가 낼 오류).
    assert_eq!(validate_local_socket(&dir.join("none.sock")), Ok(()));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn a_tcp_probe_needs_a_listener_not_just_a_portfile() {
    let dir = scratch("endpoint-tcp-probe");
    std::fs::create_dir_all(&dir).unwrap();
    let portfile = dir.join("default.port");
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::fs::write(&portfile, format!("{port}\n")).unwrap();
    let ep = Endpoint::Tcp {
        host: "127.0.0.1".into(),
        port: 0,
        portfile: portfile.clone(),
        token: dir.join("default.token"),
    };
    assert!(ep.probe());
    drop(listener);
    // 포트파일은 남았는데 듣는 이가 없다 — 정본 probe 는 False.
    assert!(ep.looks_live());
    assert!(!ep.probe(), "죽은 포트파일을 산 서버로 읽었다");
    let _ = std::fs::remove_dir_all(&dir);
}

// ── pytmux-586 G9: PYTMUX_HOME 은 정본처럼 편다(`abspath(expanduser(h))`) ─────────────

#[test]
fn pytmux_home_tilde_and_relative_paths_are_spread_like_canon() {
    use base::config::normalize_home_in;
    let home = Path::new("/Users/me");
    let cwd = Path::new("/work/tree");
    let n = |raw: &str| normalize_home_in(PathBuf::from(raw), Some(home), Some(cwd));
    assert_eq!(n("~/x"), Some(PathBuf::from("/Users/me/x")));
    assert_eq!(n("~"), Some(PathBuf::from("/Users/me")));
    assert_eq!(n("./.pytmux"), Some(PathBuf::from("/work/tree/.pytmux")));
    assert_eq!(n("../h"), Some(PathBuf::from("/work/h")));
    assert_eq!(n("/abs/h/"), Some(PathBuf::from("/abs/h")));
    // DIVERGE(pytmux-598): `~user` — 정본 expanduser 는 그 사용자가 있으면 그 홈으로 편다.
    // 우리는 펴지 않고 상대경로로 본다(드문 꼴 · 남의 홈을 상태 디렉터리로 쓸 일이 없다).
    assert_eq!(n("~other/x"), Some(PathBuf::from("/work/tree/~other/x")));
}
