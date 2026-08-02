//! 팔레트 설명의 영어 번역 == 파이썬 정본 — 적합성 오라클.
//!
//! 문구의 주인은 정본이다(로드맵 원칙 1): `clientutil` 이 i18n.register 로 싣는
//! `cmd.<name>` 영어를 생성기(`gen_client_surface_fixture.py`)가 `command_help_en`
//! 으로 뽑고, 이 테스트가 우리 카탈로그(`en_proto.rs` + 일부 en_core.rs)의 실제
//! 번역 결과와 **글자 단위로** 대조한다. 표를 손으로 다듬으면 여기서 죽는다.
//!
//! **통합 테스트인 이유** — 로케일은 프로세스 전역이라 유닛(병렬 스레드)에서 en 으로
//! 바꾸면 다른 테스트의 한국어 단언이 무너진다(`i18n_switch.rs` 머리말과 같은 논리).
//! 통합 테스트는 파일마다 자기 프로세스다.

use std::collections::HashMap;

use base::i18n;
use proto::command::command_help;

#[derive(serde::Deserialize)]
struct Fx {
    command_help: HashMap<String, String>,
    command_help_en: HashMap<String, String>,
}

#[test]
fn every_palette_description_translates_to_the_canonical_english() {
    let fx: Fx = serde_json::from_str(include_str!("fixtures/client_surface.json"))
        .expect("픽스처를 못 읽었다");
    assert!(!fx.command_help.is_empty(), "픽스처가 비었다 — 통과가 아니라 고장이다");
    // 전역을 뒤집으면 같은 이진의 형제 테스트가 남의 로케일에서 단언한다
    // (2026-08-02 사고 — `base::i18n::with_locale` 항목). 이 스레드에만 건다.
    let _en = i18n::locale_guard("en");
    let mut missing = Vec::new();
    for (name, ko) in &fx.command_help {
        // 뷰(TUI·GUI 팔레트)와 같은 조회 경로: command_help → tc("cmd", ko).
        let shown = command_help(name).expect("픽스처에 있는 이름이 command_help 에 없다");
        assert_eq!(shown, ko, "{name}: command_help 가 픽스처와 다른 원문을 돌려준다");
        let Some(want) = fx.command_help_en.get(name) else {
            // 정본에 en 이 없으면 ko 폴백이 정답이다(파이썬과 같은 degrade).
            assert_eq!(i18n::tc("cmd", shown), *ko, "{name}: 정본에 없는 번역을 만들었다");
            continue;
        };
        let got = i18n::tc("cmd", shown);
        if got != want {
            missing.push(format!("  {name}:\n    보임: {got}\n    정본: {want}"));
        }
    }
    assert!(
        missing.is_empty(),
        "정본과 다른(또는 빠진) 팔레트 번역 {}건:\n{}",
        missing.len(),
        missing.join("\n")
    );
}
