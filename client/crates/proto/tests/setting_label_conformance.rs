//! 설정 줄 이름·값 낱말 == 파이썬 정본 — 적합성 오라클.
//!
//! 설정 화면이 옵션 키(`inactive-dim`) 대신 사람 말(`비활성 패널 흐리게`)을 적게 되면서
//! **48줄짜리 낱말 표**가 생겼다. 그 부류의 손번역은 이 저장소가 세 번 갈라뜨린 자리라,
//! 표는 `scripts/gen_setting_labels.py` 가 정본에서 뜬 픽스처와 **글자까지** 맞춘다.
//!
//! 두 방향을 다 잰다:
//! - ko: 우리 표의 낱말이 정본 `setting.<key>`/`setval.<값>` 과 같은가
//! - en: 그 낱말을 `en` 로케일로 옮기면 정본의 영어와 같은가
//!
//! 뒤엣것이 따로 필요한 이유는 `category_i18n.rs` 머리말과 같다 — ko 만 맞추면 en 으로
//! 쓰는 사람만 어긋남을 보고, 그건 늦게 발견된다.
//!
//! **통합 테스트인 이유**: 로케일이 프로세스 전역이라 유닛(병렬)에서 바꾸면 다른 테스트의
//! 한국어 단언이 무너진다.

use std::collections::BTreeMap;

use base::config::{SETTING_LABELS, SETTING_VALUE_LABELS, SETTINGS, SettingKind,
                                 SettingValues, ValueDisplay, setting_label, setting_value_label};
use base::i18n;

#[derive(serde::Deserialize)]
struct Fx {
    setting_ko: BTreeMap<String, String>,
    setting_en: BTreeMap<String, String>,
    setval_ko: BTreeMap<String, String>,
    setval_en: BTreeMap<String, String>,
    used_values: Vec<String>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/setting_labels.json"))
        .expect("픽스처를 못 읽었다")
}

/// 정본에서 **플러그인이 기여하는** 설정 키(`categories.json` — 같은 생성기 묶음).
///
/// 낱말 표에는 있는데 정적 `SETTINGS` 에는 없는 줄이 정당한지 가르는 자다.
fn plugin_setting_keys() -> Vec<String> {
    #[derive(serde::Deserialize)]
    struct Row {
        key: String,
    }
    #[derive(serde::Deserialize)]
    struct Cats {
        plugin_settings: Vec<Row>,
    }
    let cats: Cats = serde_json::from_str(include_str!("fixtures/categories.json"))
        .expect("categories 픽스처를 못 읽었다");
    cats.plugin_settings.into_iter().map(|r| r.key).collect()
}

/// 정본에 **라벨이 없는** 줄들 — 여기만 우리가 이름을 짓는다.
///
/// ⚠ 2026-08-01 정정: 종전 이 상수의 이름과 주석은 "정본에 그 줄이 **없다**"고 적었는데
/// 사실이 아니다. 셋 다 정본 `SETTINGS` 에 있고(`ambiguous-width`·`prefix` 옆,
/// `win-mouse-motion`·`window-size` 는 `동작`), `setting.<key>` 번역만 없어 **정본은
/// 원본 키를 그대로 보인다**(3차 대조 컷에서 `ambiguous-width` 가 그렇게 찍혔다).
/// 우리는 이름을 지어 보인다 — 그쪽이 이 화면의 취지에 맞아 그대로 두되, 근거를 여기
/// 바로잡아 적는다. 교훈은 ⑫ 때와 같다: **"정본에 없다"를 적기 전에 정본을 본다.**
///
/// 목록으로 **박아 두는** 이유: 새 설정을 넣으면서 이름을 안 지으면 조용히 여기로
/// 떨어져 "정본에 없으니 통과"가 된다. 늘려야 통과하게 두면 그때 한 번 멈춘다.
const NO_CANON_LABEL: &[&str] = &["ambiguous-width", "win-mouse-motion", "window-size"];

#[test]
fn every_setting_row_uses_the_canonical_korean_name() {
    let fx = fixture();
    assert!(!fx.setting_ko.is_empty(), "픽스처가 비었다 — 통과가 아니라 고장이다");

    let mut wrong = Vec::new();
    for setting in SETTINGS {
        let ours = SETTING_LABELS.iter().find(|(k, _)| *k == setting.key).map(|(_, v)| *v);
        let Some(ours) = ours else {
            wrong.push(format!("{}: 우리 표에 이름이 없다(옵션 키가 그대로 보인다)", setting.key));
            continue;
        };
        match fx.setting_ko.get(setting.key) {
            Some(canon) if canon != ours => {
                wrong.push(format!("{}: 우리 {ours:?} · 정본 {canon:?}", setting.key))
            }
            Some(_) => {}
            None if NO_CANON_LABEL.contains(&setting.key) => {}
            None => wrong.push(format!(
                "{}: 정본에 라벨이 없는데 NO_CANON_LABEL 에도 없다 — 이름의 출처를 밝힐 것",
                setting.key
            )),
        }
    }
    // 우리 표에만 있고 설정 목록엔 없는 줄(설정을 지우고 이름을 안 지운 자국).
    //
    // **플러그인이 낸 줄은 예외**다(설계 Tier A · P2): 줄 자체는 서버가 런타임에 불고
    // (`plugin_surface.settings`) 정적 `SETTINGS` 에는 없다. 그래도 이름은 필요하므로
    // 낱말 표에만 있다 — 다만 **정본이 그 줄을 실제로 기여할 때만** 허용한다(플러그인이
    // 사라졌는데 이름만 남는 것도 같은 부류의 자국이다).
    let plugin_keys = plugin_setting_keys();
    for (key, _) in SETTING_LABELS {
        if !SETTINGS.iter().any(|s| s.key == *key) && !plugin_keys.contains(&(*key).to_owned()) {
            wrong.push(format!("{key}: 설정 표에 없는 이름이 남아 있다"));
        }
    }
    assert!(wrong.is_empty(), "설정 줄 이름이 정본과 다르다:\n  {}", wrong.join("\n  "));
}

#[test]
fn every_value_word_matches_canon_and_technical_values_stay_raw() {
    let fx = fixture();
    let mut wrong = Vec::new();
    for (value, ours) in SETTING_VALUE_LABELS {
        match fx.setval_ko.get(*value) {
            Some(canon) if canon != ours => {
                wrong.push(format!("{value}: 우리 {ours:?} · 정본 {canon:?}"))
            }
            Some(_) => {}
            None => wrong.push(format!("{value}: 정본 `setval` 에 없는 낱말을 만들었다")),
        }
    }
    // ★ 정본이 **안 옮기는** 값은 우리도 원값 그대로여야 한다. 옮기면 오히려 못 찾는다
    //   (`vi`·`pyte`·`native` 는 문서와 설정 파일에 그 철자로 적힌다).
    for value in &fx.used_values {
        if fx.setval_ko.contains_key(value) {
            continue;
        }
        let shown = setting_value_label(value);
        if shown != *value {
            wrong.push(format!("{value}: 정본은 원값 그대로인데 우리는 {shown:?}"));
        }
    }
    assert!(wrong.is_empty(), "값 낱말이 정본과 다르다:\n  {}", wrong.join("\n  "));
}

#[test]
fn the_english_side_matches_canon_too() {
    let fx = fixture();
    // 이 스레드에만 건다 — 전역을 뒤집으면 형제 테스트가 남의 로케일에서 단언한다
    // (2026-08-02 사고 — `base::i18n::with_locale` 항목). 되돌리기는 `Drop` 이 하므로
    // 아래 단언에서 터져도 다음 테스트가 영어를 물려받지 않는다.
    let _en = i18n::locale_guard("en");

    let mut wrong = Vec::new();
    for setting in SETTINGS {
        let Some(canon) = fx.setting_en.get(setting.key) else { continue };
        let got = setting_label(setting.key);
        if got != canon {
            wrong.push(format!("{}: 보임 {got:?} · 정본 {canon:?}", setting.key));
        }
    }
    for (value, _) in SETTING_VALUE_LABELS {
        let Some(canon) = fx.setval_en.get(*value) else { continue };
        let got = setting_value_label(value);
        if got != *canon {
            wrong.push(format!("setval {value}: 보임 {got:?} · 정본 {canon:?}"));
        }
    }

    // ★ 화면 낱말(`열기`·`미설정`)도 값칸을 타고 나간다. 표만 재면 그 둘이 한국어인
    //   채로 영어 화면에 남는다(정본 i18n 전수조사가 고친 부류의 누출).
    if let Some(row) = SETTINGS.iter().find(|s| matches!(s.kind, SettingKind::Link(_))) {
        let want = &fx.setting_en["open"];
        match row.display(&SettingValues::default()) {
            ValueDisplay::Link(word) if word == want => {}
            other => wrong.push(format!("링크 줄의 '열기': {other:?} · 정본 {want:?}")),
        }
    }
    if let Some(row) = SETTINGS.iter().find(|s| s.key == "default-path") {
        let want = format!("({})", fx.setting_en["unset"]);
        match row.display(&SettingValues::default()) {
            ValueDisplay::Text { shown, .. } if shown == want => {}
            other => wrong.push(format!("빈 자유 입력: {other:?} · 정본 {want:?}")),
        }
    }

    drop(_en);   // 실패 메시지는 **한국어로** 짓는다 — 재는 구간은 여기서 끝이다.
    assert!(wrong.is_empty(), "영어 표기가 정본과 다르다:\n  {}", wrong.join("\n  "));
}
