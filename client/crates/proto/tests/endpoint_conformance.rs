//! 교차구현 적합성 — 클라와 서버가 **같은 서버**를 가리키는가.
//!
//! 이 표가 어긋나면 증상이 "연결 실패"가 아니다. 클라가 서버를 못 찾아 **새 서버를
//! 띄우고**, 사용자는 자기 탭이 사라진 화면을 본다. Windows 는 규칙이 통째로 다르므로
//! (루프백 TCP + 포트파일 + `%LOCALAPPDATA%`) macOS 에서 개발하는 동안 이 표가 유일한
//! 방어다 — 없으면 Windows 박스에 가서야 틀린 걸 안다.
//!
//! 픽스처는 서버 구현에서 뽑았다: `python3 scripts/gen_endpoint_fixture.py`
//! 경로는 **구성요소 배열**이다(구분자는 OS 마다 다르므로 문자열로 비교하면 안 된다).

use std::path::{Path, PathBuf};

use proto::endpoint::{Endpoint, Rules};
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    scenarios: Vec<Scenario>,
}

#[derive(Deserialize)]
struct Scenario {
    name: String,
    os: String,
    #[serde(default)]
    env: std::collections::BTreeMap<String, String>,
    uid: u32,
    candidates: Vec<Candidate>,
}

#[derive(Deserialize)]
#[serde(tag = "kind")]
enum Candidate {
    #[serde(rename = "unix")]
    Unix { path: Vec<String>, token: Vec<String> },
    #[serde(rename = "tcp")]
    Tcp {
        host: String,
        port: u16,
        portfile: Vec<String>,
        token: Vec<String>,
    },
}

/// 실제 경로를 구성요소 배열로. OS 구분자에 좌우되지 않게 이 형태로만 비교한다.
fn components(path: &Path) -> Vec<String> {
    path.components()
        .filter_map(|c| match c {
            std::path::Component::Normal(s) => Some(s.to_string_lossy().to_string()),
            // 드라이브 문자(`C:`)는 그대로 한 조각으로 본다.
            std::path::Component::Prefix(p) => Some(p.as_os_str().to_string_lossy().to_string()),
            _ => None,
        })
        .collect()
}

fn rules_for(scenario: &Scenario) -> Rules {
    let var = |name: &str| scenario.env.get(name).map(PathBuf::from);
    Rules {
        windows: scenario.os == "windows",
        xdg_runtime_dir: var("XDG_RUNTIME_DIR"),
        localappdata: var("LOCALAPPDATA"),
        pytmux_home: var("PYTMUX_HOME"),
        uid: scenario.uid,
        home: Some(PathBuf::from("/home-fallback")),
    }
}

#[test]
fn endpoint_rules_match_the_server_on_both_operating_systems() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/endpoints.json")).expect("픽스처를 읽을 수 없다");
    assert!(!fixture.scenarios.is_empty(), "픽스처가 비었다");

    for scenario in &fixture.scenarios {
        let got = rules_for(scenario).candidates();
        assert_eq!(
            got.len(),
            scenario.candidates.len(),
            "[{}] 후보 개수가 다르다: {got:?}",
            scenario.name
        );
        for (i, (ours, theirs)) in got.iter().zip(&scenario.candidates).enumerate() {
            match (ours, theirs) {
                (
                    Endpoint::Unix { path, token },
                    Candidate::Unix {
                        path: want_path,
                        token: want_token,
                    },
                ) => {
                    assert_eq!(&components(path), want_path, "[{}] 후보 {i} 소켓 경로", scenario.name);
                    assert_eq!(&components(token), want_token, "[{}] 후보 {i} 토큰", scenario.name);
                }
                (
                    Endpoint::Tcp {
                        host,
                        port,
                        portfile,
                        token,
                    },
                    Candidate::Tcp {
                        host: want_host,
                        port: want_port,
                        portfile: want_portfile,
                        token: want_token,
                    },
                ) => {
                    assert_eq!(host, want_host, "[{}] 후보 {i} 호스트", scenario.name);
                    assert_eq!(port, want_port, "[{}] 후보 {i} 포트", scenario.name);
                    assert_eq!(
                        &components(portfile),
                        want_portfile,
                        "[{}] 후보 {i} 포트파일",
                        scenario.name
                    );
                    assert_eq!(&components(token), want_token, "[{}] 후보 {i} 토큰", scenario.name);
                }
                _ => panic!(
                    "[{}] 후보 {i} 의 전송 종류가 서버와 다르다(unix ↔ tcp)",
                    scenario.name
                ),
            }
        }
    }
}
