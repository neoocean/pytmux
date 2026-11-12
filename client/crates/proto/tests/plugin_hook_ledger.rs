//! **플러그인 클라 훅 대장 게이트**(pytmux-570) — «안 건너온 훅» 이 조용히 늘지 않게.
//!
//! 대장은 [`common/plugin_hooks.rs`] 다. 여기서는 정본에서 뽑은 칸 전수(`plugin_hooks.json` ·
//! `scripts/gen_plugin_hooks.py`)와 맞대기만 한다:
//!
//! 1. 정본 칸이 전부 분류됐나(정본 플러그인이 훅을 하나 더 쓰면 운다) · 대장에 죽은 줄이 없나.
//! 2. `Declared` 다리 이름이 정본 서버에 실재하나 · `Ported` 자리가 실재하나(파일 + 낱말).
//! 3. `Missing` 은 이슈를 대고, 그 수는 상한을 못 넘는다(R7 의 닫힘 = 0).

#[path = "common/plugin_hooks.rs"]
mod plugin_hooks;

use plugin_hooks::{Cell, HOOKS, cell_of};
use serde_json::Value;
use std::collections::BTreeSet;

/// `Missing` 칸의 상한 — ⛔ **올리지 않는다.** 0 = 요구 R7 이 닫혔다(마지막 칸 `client_status × ime-indicator`
/// 는 pytmux-646 이 옮겼다).
const MISSING_CEILING: usize = 0;

fn fixture() -> Value {
    serde_json::from_str(include_str!("fixtures/plugin_hooks.json"))
        .expect("plugin_hooks.json 을 못 읽는다 — python3 scripts/gen_plugin_hooks.py")
}

fn cells(fx: &Value) -> Vec<(String, String)> {
    fx["cells"]
        .as_array()
        .expect("cells")
        .iter()
        .map(|c| (c["hook"].as_str().unwrap().to_owned(), c["plugin"].as_str().unwrap().to_owned()))
        .collect()
}

#[test]
fn every_canon_hook_cell_is_classified() {
    let fx = fixture();
    let all = cells(&fx);
    assert!(all.len() >= 100, "정본 칸이 {} 개뿐이다 — 생성기를 의심할 것", all.len());
    let unclassified: Vec<String> = all
        .iter()
        .filter(|(h, p)| cell_of(h, p).is_none())
        .map(|(h, p)| format!("{h} × {p}"))
        .collect();
    assert!(
        unclassified.is_empty(),
        "정본 플러그인이 쓰는데 대장에 없는 클라 훅 칸: {unclassified:?}\n\
         common/plugin_hooks.rs 에 Declared(다리)·Ported(자리)·Missing(이슈)·NotClient(사유) 로 적을 것"
    );
}

#[test]
fn no_ledger_row_is_dead() {
    let fx = fixture();
    let all: BTreeSet<(String, String)> = cells(&fx).into_iter().collect();
    let hooks: BTreeSet<String> = all.iter().map(|(h, _)| h.clone()).collect();
    let dead: Vec<String> = HOOKS
        .iter()
        .filter(|(h, p, _, _)| {
            if *p == "*" { !hooks.contains(*h) } else { !all.contains(&(h.to_string(), p.to_string())) }
        })
        .map(|(h, p, _, _)| format!("{h} × {p}"))
        .collect();
    assert!(dead.is_empty(), "정본이 더는 안 쓰는 칸이 대장에 남았다 — 지울 것: {dead:?}");
    let keys: Vec<(&str, &str)> = HOOKS.iter().map(|(h, p, _, _)| (*h, *p)).collect();
    let unique: BTreeSet<&(&str, &str)> = keys.iter().collect();
    assert_eq!(unique.len(), keys.len(), "같은 칸이 두 번 있다");
}

#[test]
fn declared_bridges_exist_and_ported_sites_are_real() {
    let fx = fixture();
    let bridges: BTreeSet<&str> = fx["bridges"].as_array().unwrap().iter().filter_map(Value::as_str).collect();
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    let mut wrong = Vec::new();
    for (hook, plugin, cell, why) in HOOKS {
        if why.is_empty() {
            wrong.push(format!("{hook} × {plugin}: 근거가 없다"));
        }
        match cell {
            Cell::Declared(bridge) if !bridges.contains(bridge) => {
                wrong.push(format!("{hook} × {plugin}: 다리 `{bridge}` 가 정본 서버에 없다"))
            }
            Cell::Ported(site) => {
                let (path, needle) = site.split_once("::").unwrap_or((site, ""));
                match std::fs::read_to_string(root.join(path)) {
                    Err(_) => wrong.push(format!("{hook} × {plugin}: `{path}` 가 없다")),
                    Ok(text) if !needle.is_empty() && !text.contains(needle) => {
                        wrong.push(format!("{hook} × {plugin}: `{path}` 에 `{needle}` 가 없다(옮긴 자리가 사라졌다)"))
                    }
                    Ok(_) => {}
                }
            }
            Cell::Missing(issue) if !issue.starts_with("pytmux-") => {
                wrong.push(format!("{hook} × {plugin}: Missing 인데 이슈가 없다"))
            }
            Cell::NotClient(why) if why.is_empty() => wrong.push(format!("{hook} × {plugin}: 사유가 없다")),
            _ => {}
        }
    }
    assert!(wrong.is_empty(), "대장의 흠:\n  {}", wrong.join("\n  "));
}

#[test]
fn missing_cells_are_capped() {
    let fx = fixture();
    let missing: Vec<String> = cells(&fx)
        .iter()
        .filter_map(|(h, p)| match cell_of(h, p) {
            Some((Cell::Missing(issue), _)) => Some(format!("{h} × {p} ({issue})")),
            _ => None,
        })
        .collect();
    assert!(missing.len() <= MISSING_CEILING, "GUI 에 없는 칸이 상한을 넘었다(올리지 않는다): {missing:?}");
    assert_eq!(missing.len(), MISSING_CEILING, "줄었다({missing:?}) — 상한을 같은 CL 에서 내릴 것");
}

#[test]
fn print_the_hook_ledger() {
    let fx = fixture();
    let (mut d, mut p, mut m, mut n) = (0, 0, 0, 0);
    for (h, pl) in cells(&fx) {
        match cell_of(&h, &pl).map(|(c, _)| c) {
            Some(Cell::Declared(_)) => d += 1,
            Some(Cell::Ported(_)) => p += 1,
            Some(Cell::Missing(_)) => m += 1,
            Some(Cell::NotClient(_)) => n += 1,
            None => {}
        }
    }
    println!(
        "플러그인 클라 훅 대장: 플러그인 {} · 클라 훅 {} · 칸 {} = 선언형 {d} · 옮김 {p} · 없음 {m} · 클라 아님 {n}",
        fx["plugins"].as_array().unwrap().len(),
        fx["client_hooks"].as_array().unwrap().len(),
        cells(&fx).len()
    );
}
