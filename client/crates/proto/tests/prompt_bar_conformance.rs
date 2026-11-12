//! 프롬프트 바 규칙 적합성(pytmux-587) — 정본 `claude-prompt-history/render.py` 의
//! `draw_preview`·`draw_pinned` 가 쓴 답지(`scripts/gen_prompt_bar.py` →
//! `fixtures/prompt_bar.json`)에 대고 [`proto::prompt_bar`] 를 잰다.
//!
//! 재는 것: 어느 패널에 바가 서나 · 몇 줄 · 무슨 글(접기·자르기·`…`·`▾`) · 비키는 조건 다섯
//! (미리보기 · 스크롤 · 첫 줄 표식 · 화면에 보임 · `/clear`) · 패널 크기 하한 · 패널 대상 명령 표.
//! 뷰는 이 답을 그림으로 옮길 뿐이다 — 그 배선은 gui `session_view_tests.rs` 가 잰다.

use std::collections::BTreeMap;

use proto::prompt_bar::{pinned_for, preview_rows, CORE_PANE_SCOPED, PINNED_MAX_ROWS};

#[derive(serde::Deserialize)]
struct Fixture {
    core_pane_scoped: Vec<String>,
    pinned_max_rows: usize,
    cases: BTreeMap<String, Case>,
}

#[derive(serde::Deserialize)]
struct Case {
    input: Input,
    expect: Expect,
}

#[derive(serde::Deserialize)]
struct Input {
    #[serde(rename = "W")]
    w: usize,
    #[serde(rename = "H")]
    h: usize,
    #[serde(rename = "panes")]
    panes: Vec<Pane>,
    #[serde(rename = "hist")]
    hist: BTreeMap<String, Vec<String>>,
    #[serde(rename = "grid", default)]
    grid: Vec<String>,
    #[serde(rename = "scroll", default)]
    scroll: BTreeMap<String, usize>,
    #[serde(rename = "target", default)]
    target: Option<i64>,
    #[serde(rename = "max_lines", default = "three")]
    max_lines: usize,
}

fn three() -> usize {
    3
}

#[derive(serde::Deserialize)]
struct Pane {
    id: i64,
    x: usize,
    y: usize,
    w: usize,
    h: usize,
}

#[derive(serde::Deserialize, Debug, PartialEq)]
struct Expect {
    preview: Option<Preview>,
    pinned: BTreeMap<String, Pinned>,
}

#[derive(serde::Deserialize, Debug, PartialEq)]
struct Preview {
    pane: i64,
    rows: Vec<String>,
}

#[derive(serde::Deserialize, Debug, PartialEq)]
struct Pinned {
    rect: [usize; 4],
    rows: Vec<String>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/prompt_bar.json")).expect("prompt_bar.json")
}

/// 격자의 한 행에서 `x0..x1` 칸의 글(답지 격자는 칸 하나가 글자 하나다).
fn row_text(grid: &[String], y: usize, x0: usize, x1: usize) -> String {
    grid.get(y)
        .map(|r| r.chars().skip(x0).take(x1.saturating_sub(x0)).collect())
        .unwrap_or_default()
}

#[test]
fn the_prompt_bar_rules_match_the_python_canonical() {
    let fx = fixture();
    assert!(!fx.cases.is_empty(), "답지가 비었다 — 생성기 고장");
    assert_eq!(fx.pinned_max_rows, PINNED_MAX_ROWS);
    for (name, case) in &fx.cases {
        let inp = &case.input;
        let last = |pid: i64| -> Option<String> {
            inp.hist.get(&pid.to_string()).and_then(|h| h.last().cloned())
        };
        let mut got = Expect { preview: None, pinned: BTreeMap::new() };
        if let Some(target) = inp.target
            && let Some(pane) = inp.panes.iter().find(|p| p.id == target)
            && let Some(rows) = preview_rows(last(target).as_deref(), inp.max_lines, pane.w, pane.h)
        {
            got.preview = Some(Preview { pane: target, rows });
        }
        for pane in &inp.panes {
            if pane.y >= inp.h {
                continue;
            }
            let x0 = pane.x;
            let x1 = (pane.x + pane.w).min(inp.w);
            let y_end = (pane.y + pane.h).min(inp.h);
            let rows: Vec<String> = (pane.y..y_end).map(|y| row_text(&inp.grid, y, x0, x1)).collect();
            let scrolled = inp.scroll.get(&pane.id.to_string()).is_some_and(|s| *s > 0);
            let is_target = inp.target == Some(pane.id);
            if let Some(bar) =
                pinned_for(last(pane.id).as_deref(), &rows, pane.w, pane.h, scrolled, is_target)
            {
                let y1 = (pane.y + bar.len()).min(inp.h);
                let rows = bar.into_iter().map(|r| r.trim_end().to_owned()).collect();
                got.pinned.insert(pane.id.to_string(), Pinned { rect: [pane.y, y1, x0, x1], rows });
            }
        }
        assert_eq!(got, case.expect, "{name}: 정본과 다른 바");
    }
}

#[test]
fn the_core_pane_scoped_table_is_the_canon_one() {
    let fx = fixture();
    let mut ours: Vec<String> = CORE_PANE_SCOPED.iter().map(|s| (*s).to_owned()).collect();
    ours.sort();
    assert_eq!(ours, fx.core_pane_scoped, "코어 패널 대상 명령 표가 정본과 갈렸다");
}
