//! 로케일을 **안 바꾸는** 검사만 여기 둔다.
//!
//! 로케일은 프로세스 전역이라, 유닛 테스트(한 프로세스 다중 스레드)에서 en 으로
//! 바꾸면 같은 순간 도는 다른 테스트의 한국어 단언이 무너진다. 전환을 만지는
//! 검사는 **자기 프로세스를 가진** 통합 테스트(`tests/i18n_switch.rs`)에 있다.

use super::*;

#[test]
fn ko_is_identity() {
    // 기본 로케일(ko)에서 t 는 항등이다 — 이 성질 덕에 기존 한국어 단언 140줄이
    // 이 슬라이스에서 한 줄도 안 바뀌었다.
    assert_eq!(t("탭 닫기"), "탭 닫기");
    assert_eq!(t("표에 없는 문자열"), "표에 없는 문자열");
}

#[test]
fn tc_is_identity_in_ko_like_t() {
    assert_eq!(tc("setcat", "동작"), "동작");
    assert_eq!(tc("없는문맥", "동작"), "동작");
}

#[test]
fn tf_fills_named_slots_in_ko() {
    assert_eq!(
        tf("언어: {name}", &[("name", "한국어")]),
        "언어: 한국어"
    );
    // 모르는 자리는 그대로 남는다 — 렌더가 죽지 않는다.
    assert_eq!(tf("언어: {name}", &[("other", "x")]), "언어: {name}");
}

#[test]
fn resolve_matches_python_rules() {
    // 설정 파일이 이기고, 대소문자는 무시한다.
    assert_eq!(resolve(Some("EN"), Some("ko_KR.UTF-8")), "en");
    assert_eq!(resolve(Some("ko"), None), "ko");
    // 모르는 설정값은 환경으로 넘어간다.
    assert_eq!(resolve(Some("fr"), Some("ko_KR.UTF-8")), "ko");
    // 환경: ko* 만 ko, 나머지(미설정·C/POSIX 포함)는 en — 파이썬 `resolve` 와 같다.
    assert_eq!(resolve(None, Some("ko_KR.UTF-8")), "ko");
    assert_eq!(resolve(None, Some("en_US.UTF-8")), "en");
    assert_eq!(resolve(None, Some("C")), "en");
    assert_eq!(resolve(None, None), "en");
}

#[test]
fn persisted_file_round_trip() {
    let dir = std::env::temp_dir().join(format!("pytmux-i18n-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("default.sock.lang");
    // 없으면 None → resolve 로 넘어간다.
    assert_eq!(load_persisted(&path), None);
    std::fs::write(&path, "en\n").unwrap();
    assert_eq!(load_persisted(&path).as_deref(), Some("en"));
    // 미지원 값은 None — 파일이 깨져도 기동이 안 죽는다(파이썬과 같다).
    std::fs::write(&path, "fr").unwrap();
    assert_eq!(load_persisted(&path), None);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn en_tables_have_no_identity_rows() {
    // 원문=번역인 줄은 표를 못 지키는 줄이다(폴백이 이미 그 일을 한다). 이런 줄이
    // 쌓이면 "번역했다"가 거짓이 된다.
    for table in [
        en_core::EN,
        en_proto::EN,
        en_gui::EN,
        en_claude::EN,
    ] {
        for (ko, en) in table {
            assert_ne!(ko, en, "원문과 같은 번역: {ko}");
        }
    }
}

#[test]
fn en_tables_have_no_duplicate_keys() {
    // 같은 원문이 두 표에 오르면 어느 번역이 이기는지가 **표 순서**에 묻힌다 —
    // 침묵하는 규칙은 규칙이 아니다. 겹치면 여기서 죽는다.
    let mut seen = std::collections::HashMap::new();
    for (name, table) in [
        ("en_core", en_core::EN),
        ("en_proto", en_proto::EN),
        ("en_gui", en_gui::EN),
        ("en_claude", en_claude::EN),
    ] {
        for (ko, _) in table {
            if let Some(prev) = seen.insert(*ko, name) {
                panic!("중복 키 \"{ko}\" — {prev} 와 {name} 양쪽에 있다");
            }
        }
    }
}
