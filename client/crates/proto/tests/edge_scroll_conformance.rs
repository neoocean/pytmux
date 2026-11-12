//! 교차구현 적합성 — 선택을 끌다 가장자리에 닿았을 때 두 클라가 **같은 델타·같은 간격**으로
//! 구르는가(pytmux-579).
//!
//! 픽스처는 정본에서 뽑았다: `python3 scripts/gen_edge_scroll_fixture.py`
//! (출처 = `pytmuxlib/clientwidgets.py` 의 `MultiplexerView._edge_scroll_delta`).

use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    tick_ms: u64,
    max: i32,
    cases: Vec<Case>,
}

#[derive(Deserialize)]
struct Case {
    pane_y: i32,
    pane_h: i32,
    y: i32,
    delta: i32,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/edge_scroll.json")).expect("픽스처를 읽을 수 없다")
}

#[test]
fn edge_scroll_delta_matches_the_canon_on_every_row() {
    let fx = fixture();
    // 공허 통과 막기 — 굴러야 하는 줄과 안 굴러야 하는 줄이 둘 다 있어야 한다.
    assert!(fx.cases.iter().any(|c| c.delta > 0));
    assert!(fx.cases.iter().any(|c| c.delta < 0));
    assert!(fx.cases.iter().any(|c| c.delta == 0));
    let wrong: Vec<String> = fx
        .cases
        .iter()
        .filter_map(|c| {
            let got = base::edge_scroll::delta(c.pane_y, c.pane_h, c.y);
            (got != c.delta).then(|| {
                format!("패널 y={} h={} · 포인터 y={} → 정본 {} · 우리 {got}", c.pane_y, c.pane_h, c.y, c.delta)
            })
        })
        .collect();
    assert!(wrong.is_empty(), "정본과 다른 델타:\n{}", wrong.join("\n"));
}

#[test]
fn edge_scroll_tick_and_cap_match_the_canon() {
    let fx = fixture();
    assert_eq!(base::edge_scroll::TICK.as_millis() as u64, fx.tick_ms, "구르는 간격");
    assert_eq!(base::edge_scroll::MAX, fx.max, "한 번에 구르는 최대 행");
}
