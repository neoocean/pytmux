//! 스티키 바 규칙 적합성(pytmux-520) — 정본 `plugins/blocks/segment.sticky_at` 이 쓴 답지
//! (`scripts/gen_blocks_sticky.py` → `fixtures/blocks_sticky.json`)에 대고
//! [`proto::blocks::sticky_at`] 을 잰다.
//!
//! 규칙은 짧지만 경계가 셋이다(라이브 · 시작 줄 자체 · 마지막 줄). 두 클라가 각자 적으면
//! 그 경계에서 **한쪽만 바가 뜨고**, 그건 조용하다 — 사용자는 「GUI 에서는 안 보인다」로
//! 읽는다. 그래서 답지는 정본이 쓰고 우리는 읽는다(`rtt_graph.json` 과 같은 규약).

use std::collections::BTreeMap;

use proto::blocks::{sticky_at, Block, BlockState};

#[derive(serde::Deserialize)]
struct Fixture {
    cases: BTreeMap<String, Case>,
}

#[derive(serde::Deserialize)]
struct Case {
    blocks: Vec<Wire>,
    top: usize,
    scroll: usize,
    live_bottom: usize,
    expect: Option<usize>,
}

#[derive(serde::Deserialize)]
struct Wire {
    #[serde(default)]
    cmd: String,
    #[serde(default)]
    start: usize,
    #[serde(default)]
    end: Option<usize>,
}

fn block(w: &Wire) -> Block {
    Block {
        command: w.cmd.clone(),
        state: BlockState::Done,
        exit: None,
        cwd: None,
        start_row: w.start,
        end_row: w.end,
    }
}

#[test]
fn the_sticky_rule_matches_the_python_canonical() {
    let fx: Fixture =
        serde_json::from_str(include_str!("fixtures/blocks_sticky.json")).unwrap();
    assert!(fx.cases.len() >= 8, "픽스처가 얇다: {}", fx.cases.len());
    // 답이 「있다」인 사례와 「없다」인 사례가 **둘 다** 있어야 이 비교가 뜻이 있다.
    assert!(fx.cases.values().any(|c| c.expect.is_some()));
    assert!(fx.cases.values().any(|c| c.expect.is_none()));
    for (name, case) in &fx.cases {
        let blocks: Vec<Block> = case.blocks.iter().map(block).collect();
        let got = sticky_at(&blocks, case.top, case.scroll, case.live_bottom);
        assert_eq!(got, case.expect, "정본과 다른 답: {name}");
    }
}
