//! 설정 사이드바 탭의 **한국어** 이름 == 파이썬 정본 — 적합성 오라클.
//!
//! `category_i18n.rs` 와 **파일을 가른 이유**: 로케일은 프로세스 전역인데 저쪽은 en 으로
//! 바꾼 채 여러 단언을 돈다. 같은 파일에 두면 두 테스트가 같은 프로세스에서 병렬로 돌아
//! 로케일을 서로 뺏는다(실제로 한 번 그렇게 떨어뜨렸다). 통합 테스트는 **파일마다 자기
//! 프로세스**라, 로케일을 고정해야 하는 오라클은 자기 파일을 갖는다.

use std::collections::BTreeMap;

use base::config::{SETTINGS_CATS, settings_cat_label};
use base::i18n;

#[derive(serde::Deserialize)]
struct Fx {
    setcat_ko: BTreeMap<String, String>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/categories.json")).expect("픽스처를 못 읽었다")
}
/// ★ **한국어 쪽**도 잰다 — 이것이 없어서 3차 대조까지 못 봤다.
///
/// 우리 `t()`/`tc()` 는 ko 에서 **항등**이다. 그래서 "분류 이름 = 화면 이름"인 자리는
/// 거저 맞고, **다른 자리는 조용히 틀린다**: 정본은 `setcat.입력` 을 `입력/키` 로,
/// `setcat.고급` 을 `고급/플러그인` 으로 적는데 우리는 분류 이름을 그대로 적고 있었다.
/// en 은 `en_core.rs` 에 정본 낱말이 있어 맞았으므로 `…canonical_english` 는 통과했다 —
/// **게이트 셋을 다 통과한 채 한국어 화면만 틀려 있었다.**
#[test]
fn every_settings_tab_shows_the_canonical_korean() {
    let fx = fixture();
    assert!(!fx.setcat_ko.is_empty(), "픽스처가 비었다 — 통과가 아니라 고장이다");
    i18n::set_locale("ko");

    let mut wrong = Vec::new();
    for cat in SETTINGS_CATS {
        let want = fx
            .setcat_ko
            .get(*cat)
            .unwrap_or_else(|| panic!("정본에 없는 설정 카테고리: {cat}"));
        let got = settings_cat_label(cat);
        if got != want {
            wrong.push(format!("{cat}: 보임 {got:?} · 정본 {want:?}"));
        }
    }
    assert!(wrong.is_empty(), "설정 탭의 한국어 이름이 정본과 다르다:\n  {}", wrong.join("\n  "));
}
