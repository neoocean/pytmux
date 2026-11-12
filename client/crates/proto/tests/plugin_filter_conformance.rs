//! 적합성 — 플러그인 관리 판의 **이름 검색**이 정본 `PluginManagerScreen._matches` 와 같은가
//! (pytmux-591).
//!
//! 픽스처는 정본 `_matches` 를 직접 불러 뽑았다(`scripts/gen_plugin_filter.py`). 구분자
//! (`p4 show`·`p4_show`)·대소문자·한글 자모(`ㅔ4` = `p4`)·설명 일치·빈 검색어를 담는다.

use base::screens::plugin_filter;

#[derive(serde::Deserialize)]
struct Item {
    name: String,
    desc: String,
}

#[derive(serde::Deserialize)]
struct Case {
    query: String,
    rows: Vec<usize>,
}

#[derive(serde::Deserialize)]
struct Fx {
    items: Vec<Item>,
    cases: Vec<Case>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/plugin_filter.json"))
        .expect("plugin_filter.json 을 못 읽는다 — scripts/gen_plugin_filter.py 로 다시 뽑을 것")
}

#[test]
fn every_query_filters_the_same_rows_as_canon() {
    let fx = fixture();
    let items: Vec<(&str, &str)> =
        fx.items.iter().map(|i| (i.name.as_str(), i.desc.as_str())).collect();
    let mut wrong = Vec::new();
    for case in &fx.cases {
        let got = plugin_filter(&items, &case.query);
        if got != case.rows {
            wrong.push(format!("{:?}: 우리 {got:?} · 정본 {:?}", case.query, case.rows));
        }
    }
    assert!(wrong.is_empty(), "정본과 다르게 거른 검색어:\n  {}", wrong.join("\n  "));
}

/// 픽스처가 갈래를 실제로 담았나 — 전부 걸리거나 전부 안 걸리면 이 대조는 공허하다.
#[test]
fn the_fixture_exercises_both_hit_and_miss() {
    let fx = fixture();
    let n = fx.items.len();
    assert!(fx.cases.iter().any(|c| c.rows.is_empty()), "안 걸리는 검색어가 없다");
    assert!(
        fx.cases.iter().any(|c| !c.rows.is_empty() && c.rows.len() < n),
        "일부만 걸리는 검색어가 없다"
    );
}
