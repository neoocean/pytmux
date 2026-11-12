//! 적합성 — **트리 판**(pytmux-593)과 **탭 스위처**(pytmux-620)의 줄이 정본과 같은가.
//!
//! 픽스처는 정본을 직접 돌려 뽑았다(`scripts/gen_tree_switcher.py` — `ChooseTreeScreen.compose`
//! · `open_tab_switcher` · `_fill_tab_switcher_panes` · `TabSwitcherScreen.add_panes`). 여기서는
//! **같은 입력**(tree 회신 · status 탭 · 세션 이름)을 [`SessionState`] 에 먹여 `tree_rows`·
//! `switcher_rows` 가 같은 줄을 같은 차례로 내는지 본다.
//!
//! 글의 맨 앞 **활성 표식**(정본 `▾`/`▸`·스위처 `▸`/공백)과 패널 줄의 **들여쓰기**는 우리
//! 글이 아니라 그리는 쪽의 몫이다(`TreeRow::active`·`depth` — 허용 ⓑ). 그 둘을 떼고 나머지
//! 글은 **글자까지** 같아야 한다. 떼는 대신 활성 여부·깊이는 칸으로 맞댄다.

use proto::session::SessionState;
use serde::Deserialize;

#[derive(Deserialize)]
struct Row {
    label: String,
    kind: String,
    window: usize,
    pane: Option<i64>,
    #[serde(default)]
    active: bool,
}

#[derive(Deserialize)]
struct Fx {
    tree_input: serde_json::Value,
    tabs_input: serde_json::Value,
    session: String,
    tree_rows: Vec<Row>,
    switcher_rows: Vec<Row>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/tree_switcher.json"))
        .expect("tree_switcher.json 을 못 읽는다 — scripts/gen_tree_switcher.py 로 다시 뽑을 것")
}

fn state(fx: &Fx) -> SessionState {
    let mut state = SessionState::new();
    state.apply(
        serde_json::from_value(serde_json::json!({
            "t": "status", "session": fx.session, "windows": fx.tabs_input,
        }))
        .expect("status"),
    );
    state.apply(serde_json::from_value(fx.tree_input.clone()).expect("tree"));
    state
}

/// 정본 줄에서 그리는 쪽 몫(활성 표식 · 들여쓰기)을 뗀 글과 그 활성 여부.
fn canon_text(label: &str) -> (String, bool) {
    let trimmed = label.trim_start();
    for (mark, active) in [("▾ ", true), ("▸ ", true)] {
        if let Some(rest) = trimmed.strip_prefix(mark) {
            return (rest.to_owned(), active);
        }
    }
    (trimmed.to_owned(), false)
}

#[test]
fn tree_rows_match_canon_row_by_row() {
    let fx = fixture();
    let ours = state(&fx).tree_rows();
    let mut wrong = Vec::new();
    assert_eq!(ours.len(), fx.tree_rows.len(), "줄 수가 다르다: 우리 {ours:#?}");
    for (i, (got, want)) in ours.iter().zip(&fx.tree_rows).enumerate() {
        let (text, _) = canon_text(&want.label);
        let is_pane = want.kind == "pane";
        if got.label != text
            || got.window != Some(want.window)
            || got.pane != want.pane
            || got.depth != usize::from(is_pane)
        {
            wrong.push(format!("{i}: 우리 {got:?} · 정본 {:?}", want.label));
        }
        // 트리의 `▾` 는 **활성 탭**이고 `▸` 는 아니다 — 칸으로 맞댄다.
        if !is_pane && got.active != want.label.starts_with('▾') {
            wrong.push(format!("{i}: 활성 표식이 다르다 — 우리 {}", got.active));
        }
    }
    assert!(wrong.is_empty(), "정본과 다른 트리 줄:\n  {}", wrong.join("\n  "));
}

#[test]
fn switcher_rows_match_canon_row_by_row() {
    let fx = fixture();
    let ours = state(&fx).switcher_rows();
    assert_eq!(ours.len(), fx.switcher_rows.len(), "줄 수가 다르다: 우리 {ours:#?}");
    let mut wrong = Vec::new();
    for (i, (got, want)) in ours.iter().zip(&fx.switcher_rows).enumerate() {
        let (text, _) = canon_text(&want.label);
        let is_pane = want.kind == "pane";
        if got.label != text
            || got.window != Some(want.window)
            || got.pane != want.pane
            || got.depth != usize::from(is_pane)
            || (!is_pane && got.active != want.active)
        {
            wrong.push(format!("{i}: 우리 {got:?} · 정본 {:?}", want.label));
        }
    }
    assert!(wrong.is_empty(), "정본과 다른 스위처 줄:\n  {}", wrong.join("\n  "));
}

/// 픽스처가 갈래를 담았나 — 다른 세션의 같은 번호 창 · 원격 패널 · 고정 탭 재배치.
#[test]
fn the_fixture_covers_the_four_divergences() {
    let fx = fixture();
    // 다른 세션 패널(90·91)은 스위처에 **안** 붙는다.
    assert!(fx.switcher_rows.iter().all(|r| r.pane != Some(90)));
    assert!(fx.tree_rows.iter().any(|r| r.pane == Some(90)), "트리는 전 세션을 보인다");
    // 원격 탭 패널(7·8)은 붙는다.
    assert!(fx.switcher_rows.iter().any(|r| r.pane == Some(7)));
    // 고정 탭(index 1)은 맨 뒤 탭 줄이다.
    let last_tab = fx.switcher_rows.iter().rev().find(|r| r.kind == "tab").expect("탭 줄");
    assert_eq!(last_tab.window, 1);
}
