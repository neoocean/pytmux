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
    let dir = std::env::temp_dir().join("pytmux-endpoint-test");
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
    let dir = std::env::temp_dir().join("pytmux-endpoint-junk");
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
}

#[test]
fn missing_token_file_is_not_an_error() {
    assert_eq!(read_token_at(Path::new("/nonexistent/default.token")), None);
}
