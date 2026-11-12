//! 상태줄 메시지 등급표 적합성(pytmux-641 ②) — 정본 `clientnotices._SPEC` 답지
//! (`scripts/gen_notice_levels.py` → `fixtures/notice_levels.json`)에 대고
//! `proto::session::Severity` 를 잰다: 기호 · 테마 색 이름 · 자동으로 사라지는 시간 · 손으로 닫는
//! 등급인가 · 모르는 값의 기본 등급.

use proto::session::Severity;

#[derive(serde::Deserialize)]
struct Fixture {
    default: String,
    levels: Vec<Level>,
}

#[derive(serde::Deserialize)]
struct Level {
    name: String,
    symbol: String,
    theme: String,
    auto_close_ms: u64,
    dismissable: bool,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/notice_levels.json")).expect("답지를 못 읽었다")
}

#[test]
fn every_severity_matches_the_canon_spec() {
    let fx = fixture();
    let names: Vec<&str> = fx.levels.iter().map(|l| l.name.as_str()).collect();
    let ours: Vec<&str> = Severity::ALL.iter().map(|s| s.canon_name()).collect();
    assert_eq!(ours, names, "등급의 차례·이름이 갈렸다");
    for level in &fx.levels {
        let sev = Severity::parse(Some(&level.name));
        assert_eq!(sev.canon_name(), level.name, "parse 가 {} 를 못 읽는다", level.name);
        assert_eq!(sev.mark(), level.symbol, "{}: 기호", level.name);
        assert_eq!(sev.theme_name(), level.theme, "{}: 색 이름", level.name);
        assert_eq!(sev.auto_close_ms(), level.auto_close_ms, "{}: 시한", level.name);
        assert_eq!(sev.dismissable(), level.dismissable, "{}: 닫는 등급", level.name);
    }
    // 모르는 값 = 정본 기본 등급(`normalize`).
    assert_eq!(Severity::parse(Some("bogus")).canon_name(), fx.default);
}
