//! 적합성 — `debug-stats` 판의 **서버 절반**이 정본 `clientdiag.render_server` 와 같은가
//! (pytmux-627 ②).
//!
//! 두 클라가 **같은 회신**(`debug_stats`)을 받아 각자 로케일로 줄을 짓는다. 그 줄은 차례·라벨·
//! 값 모양까지 같아야 한다 — 정본 주석이 그렇다고 적었지만 맞대는 시험이 없었다. 픽스처는
//! 정본 함수를 직접 부른 줄이다(`scripts/gen_debug_stats_fixture.py`).
//!
//! ⚠ **클라 절반은 대상 밖이다** — 정본은 파이썬 힙·GC 세대를, 우리는 그린 프레임·글리프
//! 캐시·RTT 를 잰다. 각자 제 프로세스를 재는 판이라 값이 같을 수 없고, 그 갈림은
//! `interaction.rs` 의 `DebugStats` 줄이 적는다(대장의 새 축은 pytmux-598).
//!
//! **통합 시험인 이유**: 로케일이 스레드 지역이라도 `with_locale` 로 가둬야 형제 시험과 안 엉킨다.

use base::i18n;
use proto::diag::ServerStats;

#[derive(serde::Deserialize)]
struct Case {
    locale: String,
    stats: serde_json::Value,
    lines: Vec<String>,
}

#[derive(serde::Deserialize)]
struct Waiting {
    ko: String,
    en: String,
}

#[derive(serde::Deserialize)]
struct Fx {
    now: f64,
    waiting: Waiting,
    cases: Vec<Case>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/debug_stats.json"))
        .expect("debug_stats.json 을 못 읽는다 — scripts/gen_debug_stats_fixture.py 로 다시 뽑을 것")
}

#[test]
fn the_server_half_reads_line_for_line_like_canon() {
    let fx = fixture();
    let mut wrong = Vec::new();
    for (i, case) in fx.cases.iter().enumerate() {
        let stats: ServerStats = serde_json::from_value(case.stats.clone()).expect("회신");
        let got = i18n::with_locale(&case.locale, || stats.lines(Some(fx.now)));
        if got != case.lines {
            wrong.push(format!(
                "#{i} ({}):\n    우리 {got:#?}\n    정본 {:#?}",
                case.locale, case.lines
            ));
        }
    }
    assert!(wrong.is_empty(), "정본과 다른 서버 절반:\n  {}", wrong.join("\n  "));
}

#[test]
fn the_waiting_line_is_canon() {
    let fx = fixture();
    assert_eq!(i18n::with_locale("ko", ServerStats::waiting_line), fx.waiting.ko);
    assert_eq!(i18n::with_locale("en", ServerStats::waiting_line), fx.waiting.en);
}
