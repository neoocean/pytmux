//! 분류 이름의 영어 == 파이썬 정본 — 적합성 오라클.
//!
//! 카테고리를 정본에서 뽑아 놓고 **화면에 적는 이름**만 손으로 옮기면, ko 는 맞는데 en 만
//! 어긋나는 화면이 된다(그 어긋남은 en 로 쓰는 사람만 본다 — 늦게 발견된다). 그래서 같은
//! 픽스처의 `cat_en`·`setcat_en`·`menu_group_en` 과 우리 조회 경로의 결과를 대조한다.
//!
//! **통합 테스트인 이유** — 로케일은 프로세스 전역이라 유닛(병렬 스레드)에서 en 으로
//! 바꾸면 다른 테스트의 한국어 단언이 무너진다(`help_i18n.rs` 머리말과 같은 논리).

use std::collections::BTreeMap;

use base::config::{SETTINGS_CATS, settings_cat_label};
use base::{MENU_GROUP_LABELS, PALETTE_CAT_ALL, PALETTE_CATS, i18n,
                         menu_group_label, palette_cat_label};

#[derive(serde::Deserialize)]
struct Fx {
    cat_en: BTreeMap<String, String>,
    setcat_en: BTreeMap<String, String>,
    menu_group_en: BTreeMap<String, String>,
    plugin_menu_en: BTreeMap<String, String>,
    /// 플러그인이 낸 메뉴 줄의 ko 문구 — 서버가 보낼 글 그대로다(정적 표에 없다).
    plugin_menu_labels: BTreeMap<String, String>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/categories.json")).expect("픽스처를 못 읽었다")
}

#[test]
fn every_category_name_shows_the_canonical_english() {
    let fx = fixture();
    assert!(!fx.cat_en.is_empty(), "픽스처가 비었다 — 통과가 아니라 고장이다");
    // 이 스레드에만 건다 — 전역을 뒤집으면 형제 테스트가 남의 로케일에서 단언한다.
    let _en = i18n::locale_guard("en");

    let mut wrong = Vec::new();
    let mut check = |what: &str, ko: &str, got: &str, canon: Option<&String>| {
        let Some(want) = canon else {
            // 정본에 en 이 없으면 ko 폴백이 정답이다(파이썬과 같은 degrade — 예: `Claude`).
            if got != ko {
                wrong.push(format!("{what} {ko}: 정본에 없는 번역을 만들었다 → {got}"));
            }
            return;
        };
        if got != want {
            wrong.push(format!("{what} {ko}: 보임 {got} · 정본 {want}"));
        }
    };

    // 팔레트 탭 — `전체` 가상 탭까지.
    check("cat", PALETTE_CAT_ALL, palette_cat_label(PALETTE_CAT_ALL), fx.cat_en.get(PALETTE_CAT_ALL));
    for cat in PALETTE_CATS {
        check("cat", cat, palette_cat_label(cat), fx.cat_en.get(*cat));
    }
    // 설정 사이드바.
    for cat in SETTINGS_CATS {
        check("setcat", cat, settings_cat_label(cat), fx.setcat_en.get(*cat));
    }
    // 메뉴 그룹 — 키는 그룹 이름(`pane`)이고 정본 카탈로그도 그 키로 적는다.
    for (group, ko) in MENU_GROUP_LABELS {
        check("menugroup", ko, menu_group_label(group), fx.menu_group_en.get(*group));
    }

    // 플러그인이 낸 메뉴 줄. 문구의 주인이 플러그인이라 en 도 그쪽에서 나온다 —
    // 여기 안 재면 그 줄만 한국어인 채로 영어 메뉴에 남는다.
    //
    // 줄 자체는 **서버가 런타임에** 부는 것이라(설계 Tier A · P2) 정적 표에서 못 찾는다.
    // 픽스처의 ko 라벨이 곧 서버가 보낼 글이고, 화면은 그것을 `t()` 에 통과시킨다.
    for (key, ko) in &fx.plugin_menu_labels {
        check("menu", ko, i18n::t(ko), fx.plugin_menu_en.get(key));
    }

    assert!(
        wrong.is_empty(),
        "정본과 다른 분류 번역 {}건:\n  {}",
        wrong.len(),
        wrong.join("\n  ")
    );
}

