//! **타이핑 찾기 적합성** — 정본 `pytmuxlib/typeahead.py:find_jump` 대 `base::screens::typeahead_jump`
//! (pytmux-611·612).
//!
//! 정본 mdir·ncd 의 빨리찾기 규칙(지금 줄부터 감아 돌며 접두 일치 먼저 · 없으면 포함 ·
//! 대소문자 무시 · 빈 이름은 건너뜀)을 GUI 가 한 벌 더 든다. 사례는
//! `scripts/gen_typeahead_fixture.py` 가 정본 함수를 불러 적는다.

use serde::Deserialize;

#[derive(Deserialize)]
struct Case {
    start: usize,
    query: String,
    answer: Option<usize>,
}

#[derive(Deserialize)]
struct Fx {
    names: Vec<String>,
    cases: Vec<Case>,
}

#[test]
fn our_jump_lands_where_the_canon_one_does() {
    let fx: Fx = serde_json::from_str(include_str!("fixtures/typeahead.json"))
        .expect("typeahead.json 을 못 읽는다 — scripts/gen_typeahead_fixture.py 로 다시 뽑을 것");
    assert!(fx.cases.iter().any(|c| c.answer.is_some()), "공허한 픽스처");
    let mut wrong = Vec::new();
    for c in &fx.cases {
        let ours = base::screens::typeahead_jump(&fx.names, c.start, &c.query);
        if ours != c.answer {
            wrong.push(format!(
                "start={} query={:?}: 정본 {:?} · 우리 {:?}",
                c.start, c.query, c.answer, ours
            ));
        }
    }
    assert!(wrong.is_empty(), "정본과 다른 자리로 뛴다:\n  {}", wrong.join("\n  "));
}
