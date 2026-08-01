//! 복사 펼치기(`copy-unwrap`) == 파이썬 정본 — 적합성 오라클.
//!
//! 기대값을 손으로 안 적는 이유: 이 함수는 게이트가 여섯이고 상수들은 실측으로 굳은
//! 값이다. 손으로 적으면 **우리가 이해한 규칙**을 재게 된다.
//! `scripts/gen_unwrap_cases.py` 가 정본 함수를 직접 호출해 뜬 짝과 대조한다.

use std::collections::BTreeMap;

use proto::unwrap_copy_text;

#[derive(serde::Deserialize)]
struct Case {
    name: String,
    text: String,
    width: usize,
    first_col: usize,
    want: String,
}

#[derive(serde::Deserialize)]
struct Fx {
    cases: Vec<Case>,
    hang_max: usize,
    min_fill: usize,
    slack: usize,
    tail_stop: String,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/unwrap_copy.json")).expect("픽스처를 못 읽었다")
}

#[test]
fn every_case_matches_the_canon_result() {
    let fx = fixture();
    assert!(!fx.cases.is_empty(), "픽스처가 비었다 — 통과가 아니라 고장이다");
    // ★ **바뀌는 경우가 실제로 있어야** 한다 — 전부 무변경이면 아무것도 안 하는
    //   구현이 통과한다(이 저장소가 두 번 겪은 공허 통과).
    let changed = fx.cases.iter().filter(|c| c.want != c.text).count();
    assert!(changed >= 3, "픽스처에 바뀌는 경우가 {changed}개뿐이다");

    let mut wrong: BTreeMap<&str, String> = BTreeMap::new();
    for case in &fx.cases {
        let got = unwrap_copy_text(&case.text, case.width, case.first_col);
        if got != case.want {
            wrong.insert(&case.name, format!("보임 {got:?} · 정본 {:?}", case.want));
        }
    }
    assert!(
        wrong.is_empty(),
        "정본과 다른 결과 {}건:\n  {}",
        wrong.len(),
        wrong.iter().map(|(k, v)| format!("{k}: {v}")).collect::<Vec<_>>().join("\n  ")
    );
}

/// 상수도 정본 것인가 — 값이 갈리면 위 케이스가 우연히 맞을 뿐이다.
#[test]
fn the_gates_use_the_canon_constants() {
    let fx = fixture();
    assert_eq!(fx.hang_max, 12, "매달림 들여쓰기 상한이 바뀌었다");
    assert_eq!(fx.min_fill, 24, "최소 채움이 바뀌었다");
    assert_eq!(fx.slack, 2, "여유 칸이 바뀌었다");
    assert_eq!(fx.tail_stop, ":;{}\\", "의도된 줄 끝 글자가 바뀌었다");
}
