//! **갈림 대장 게이트** — GUI 에만 있는 표면이 조용히 늘지 않게.
//!
//! 대장(줄과 분류)은 [`common/divergence.rs`] 에 있다 — 그 파일 머리말이 *왜* 이 자가
//! 필요한지를 적는다. 여기서는 **세기만** 한다:
//!
//! 1. 축마다 「정본에 없는 것」을 전수로 뽑아 대장과 **집합이 정확히 같은지** 본다.
//!    대장에 없는 것이 하나라도 있으면 운다 = 분류되지 않은 갈림이 생겼다.
//! 2. `SameFeature` 가 댄 **정본의 자리를 실제로 찾아본다** — 산문이 아니라 확인되는
//!    주장이라야 한다. 정본이 그 이름을 지우면 그 줄이 먼저 운다.
//! 3. `Todo` 는 **이슈를 대야** 한다. 이슈 없는 «할 일»은 아무도 안 하는 일이다.
//! 4. 아직 갈림이 **하나도 없는 축**도 센다 — 0 을 안 재면 첫 갈림이 조용히 들어온다.
//!
//! ⚠ 재는 것은 **「정본에 그 이름·키·화면이 있나」**다. 「같게 구나」는 옆의
//! `interaction.rs`(화면)와 `mode_transition_conformance.rs`(모드)가 잰다.

#[path = "common/divergence.rs"]
mod divergence;

use std::collections::BTreeSet;

use divergence::{Axis, Class, LEDGER, canon_binding_names};
use serde_json::Value;

fn fixture() -> Value {
    serde_json::from_str(include_str!("fixtures/client_surface.json"))
        .expect("픽스처를 읽을 수 없다 — scripts/gen_client_surface_fixture.py 로 다시 뽑을 것")
}

/// 분류 픽스처 — **플러그인이 기여한 명령·설정까지** 든 정본 목록이다.
///
/// ⛔ 팔레트·설정 축을 `client_surface.json` 으로 세면 안 된다: 그 칸은 코어
/// (`clientutil.COMMANDS`)뿐이라 정본의 **플러그인 명령 열넷**(`clock-mode`·`auto-resume`
/// 따위)이 통째로 「정본에 없는 것」으로 세진다(실측). 정본은 그것을 delete-to-disable
/// 플러그인으로 갖고 있고 우리도 같은 이름으로 갖는다 — 갈림이 아니다.
fn categories() -> Value {
    serde_json::from_str(include_str!("fixtures/categories.json"))
        .expect("픽스처를 읽을 수 없다 — scripts/gen_categories.py 로 다시 뽑을 것")
}

/// 픽스처의 한 칸을 **이름 집합**으로. 칸은 map(설정·명령)일 수도 list(화면·별칭)일 수도 있다.
fn names(fx: &Value, field: &str) -> BTreeSet<String> {
    match &fx[field] {
        Value::Object(map) => map.keys().cloned().collect(),
        Value::Array(items) => items
            .iter()
            .filter_map(|v| v.as_str().map(str::to_owned))
            .collect(),
        other => panic!("픽스처에 '{field}' 칸이 없거나 모양이 다르다: {other:?}"),
    }
}

fn ledger_names(axis: Axis) -> BTreeSet<&'static str> {
    LEDGER
        .iter()
        .filter(|row| row.axis == axis)
        .map(|row| row.name)
        .collect()
}

/// 축 하나의 전수와 대장을 맞대 본다.
fn census(axis: Axis, found: BTreeSet<String>) {
    let listed = ledger_names(axis);
    let unlisted: Vec<&String> = found.iter().filter(|n| !listed.contains(n.as_str())).collect();
    let stale: Vec<&&str> = listed
        .iter()
        .filter(|n| !found.contains(**n))
        .collect();
    assert!(
        unlisted.is_empty(),
        "[{}] 정본에 없는데 대장에 안 적혔다: {unlisted:?}\n\
         ⛔ 분류하지 않은 갈림은 «없는 것»과 구별되지 않는다. \
         common/divergence.rs 에 줄을 더하고 [[pytmux-185]] 의 기준으로 분류할 것 \
         (ⓐ 단말이 못 주는 키 · ⓑ 픽셀 그림 · ⓒ OS 창 통합 · 기능은 정본에도 있음 · 할 일).",
        axis.label()
    );
    assert!(
        stale.is_empty(),
        "[{}] 대장에 죽은 줄이 남아 있다: {stale:?}\n\
         정본이 그것을 갖게 됐거나 우리가 지운 것이다 — 대장에서도 뺄 것.",
        axis.label()
    );
}

#[test]
fn the_palette_names_canon_does_not_know_are_all_in_the_ledger() {
    let canon = names(&categories(), "command_cats");
    // 팔레트 이름이 `split-window -h` 처럼 플래그를 품기도 한다 — 기본형으로 찾는다
    // (`category_conformance.rs` 가 정본 분류를 찾는 방식과 같아야 둘이 안 갈린다).
    let found = base::PALETTE
        .iter()
        .map(|entry| entry.name.split(' ').next().unwrap_or(entry.name))
        .filter(|name| !canon.contains(*name))
        .map(str::to_owned)
        .collect();
    census(Axis::Palette, found);
}

#[test]
fn the_settings_canon_does_not_know_are_all_in_the_ledger() {
    let canon = names(&categories(), "setting_cats");
    let found = base::config::SETTINGS
        .iter()
        .map(|setting| setting.key)
        .filter(|key| !canon.contains(*key))
        .map(str::to_owned)
        .collect();
    census(Axis::Setting, found);
}

#[test]
fn the_screens_canon_does_not_have_are_all_in_the_ledger() {
    // 정본 짝이 없는 화면(`canon_class() == None`)이 곧 이 축의 전수다. 종전에는 그
    // 넷이 **아무 게이트에도 안 걸려 있었다** — `screen_anchor_conformance.rs` 는
    // 반대 방향(정본 화면이 다 맡아졌나)만 본다.
    let found = base::Screen::all()
        .iter()
        .filter(|screen| screen.canon_class().is_none())
        .map(|screen| format!("{screen:?}"))
        .collect();
    census(Axis::Screen, found);
}

#[test]
fn the_esc_keys_canon_does_not_have_are_all_in_the_ledger() {
    let fx = fixture();
    let (canon, unread) = canon_binding_names(
        fx["esc_key_modes"]
            .as_object()
            .expect("픽스처에 esc_key_modes 가 없다")
            .keys()
            .map(String::as_str),
    );
    // 못 읽은 이름을 **버리지 않는다** — 버리면 그 키가 「정본에 없다」로 세져 갈림이
    // 하나 조용히 는다. 하나뿐인 예외는 우리 키 층에 없는 Windows 아티팩트다.
    assert_eq!(
        unread,
        vec!["\u{0}".to_owned()],
        "정본 esc 키 이름을 우리 키로 못 읽는다 — common/divergence.rs 의 key_of 에 더할 것"
    );
    let found = base::BINDINGS
        .iter()
        .map(|binding| binding.key)
        .filter(|key| !canon.contains(*key))
        .map(str::to_owned)
        .collect();
    census(Axis::EscKey, found);
}

#[test]
fn the_axes_that_have_no_divergence_yet_still_get_counted() {
    // ⛔ 0 을 안 재면 **첫 갈림이 조용히 들어온다.** 아래 셋은 지금 전부 비어 있고,
    //    비어 있다는 사실 자체를 여기서 못박는다. 하나라도 생기면 `Axis` 에 축을
    //    더하고 대장에 줄을 적으라는 말이 이 실패의 뜻이다.
    let fx = fixture();

    let (prefix_canon, unread) = canon_binding_names(
        fx["prefix_key_modes"]
            .as_object()
            .expect("픽스처에 prefix_key_modes 가 없다")
            .keys()
            .map(String::as_str),
    );
    assert!(unread.is_empty(), "정본 prefix 키 이름을 못 읽는다: {unread:?}");
    let prefix: Vec<&str> = base::PREFIX_BINDINGS
        .iter()
        .map(|binding| binding.key)
        .filter(|key| !prefix_canon.contains(*key))
        .collect();
    assert!(
        prefix.is_empty(),
        "prefix 모드에 정본에 없는 키가 생겼다: {prefix:?} — 대장에 축을 더할 것"
    );

    let menu_canon = names(&fx, "menu_items");
    let menu: Vec<&str> = base::MENU
        .iter()
        .map(|entry| entry.key)
        .filter(|key| !menu_canon.contains(*key))
        .collect();
    assert!(
        menu.is_empty(),
        "메뉴에 정본에 없는 항목이 생겼다: {menu:?} — 대장에 축을 더할 것"
    );

    // 스크롤 모드 키. 정본 이름(`G`)과 우리 표기(`shift-G`)를 같은 문법으로 접는다.
    let (scroll_canon, unread) =
        canon_binding_names(names(&fx, "scroll_keys").iter().map(String::as_str));
    assert!(unread.is_empty(), "정본 스크롤 키 이름을 못 읽는다: {unread:?}");
    let scroll: Vec<&str> = base::keys::SCROLL_BINDINGS
        .iter()
        .map(|binding| binding.key)
        .filter(|key| !scroll_canon.contains(*key))
        .collect();
    assert!(
        scroll.is_empty(),
        "스크롤 모드에 정본에 없는 키가 생겼다: {scroll:?} — 대장에 축을 더할 것"
    );
}

#[test]
fn every_same_feature_row_points_at_a_place_canon_really_has() {
    // ⛔ 이 시험이 이 대장의 값을 정한다. `SameFeature` 는 *"기능은 정본에도 있다"* 는
    //    **주장**이고, 확인되지 않는 주장은 그냥 변명이다 — 실제로 종전 허용 목록에
    //    *"정본 SETTINGS 에는 있다"* 는 **틀린 줄**이 하나 있었다(`display-panes`).
    let fx = fixture();
    let cats = categories();
    let mut wrong = Vec::new();
    for row in LEDGER {
        let Class::SameFeature(anchor) = row.class else {
            continue;
        };
        let Some((field, name)) = anchor.split_once(':') else {
            wrong.push(format!("{}: 자리는 `<칸>:<이름>` 이라야 한다 — {anchor:?}", row.name));
            continue;
        };
        let table = if fx.get(field).is_some() { &fx } else { &cats };
        if !names(table, field).contains(name) {
            wrong.push(format!(
                "{}: 정본의 {field} 에 {name:?} 이(가) 없다 — 자리를 다시 대거나 Todo 로 내릴 것",
                row.name
            ));
        }
    }
    assert!(wrong.is_empty(), "확인되지 않는 «기능은 정본에도 있다»:\n  {}", wrong.join("\n  "));
}

#[test]
fn every_todo_row_names_an_issue_and_every_row_says_why() {
    let mut wrong = Vec::new();
    for row in LEDGER {
        if row.why.is_empty() {
            wrong.push(format!("{}: 이유가 없다 — 이유 없는 분류는 아무 말도 안 한다", row.name));
        }
        if let Class::Todo(issue) = row.class
            && !issue.starts_with("pytmux-")
        {
            wrong.push(format!("{}: 할 일인데 이슈를 안 댔다 ({issue:?})", row.name));
        }
        if row.axis == Axis::Palette && row.cat.is_empty() {
            wrong.push(format!("{}: 팔레트 줄인데 분류 칸이 비었다", row.name));
        }
        if row.axis != Axis::Palette && !row.cat.is_empty() {
            wrong.push(format!("{}: 팔레트가 아닌 줄에 분류 칸이 붙었다", row.name));
        }
    }
    assert!(wrong.is_empty(), "대장의 흠:\n  {}", wrong.join("\n  "));
}

#[test]
fn the_ledger_is_sorted_and_has_no_duplicates() {
    // 자리를 옮기는 것만으로 diff 가 잡음이 되면 진짜 변경(추가·삭제·분류)이 묻힌다.
    let keys: Vec<(Axis, &str)> = LEDGER.iter().map(|row| (row.axis, row.name)).collect();
    let mut sorted = keys.clone();
    sorted.sort_unstable();
    assert_eq!(keys, sorted, "대장은 축·이름순이라야 한다");
    let unique: BTreeSet<(Axis, &str)> = keys.iter().copied().collect();
    assert_eq!(unique.len(), keys.len(), "같은 줄이 두 번 있다");
}

#[test]
fn print_the_ledger() {
    // `--nocapture` 로 보는 표. 실패하지 않는다 — 게이트가 아니라 **자**다.
    println!("\n갈림 대장(GUI 에만 있는 표면 · pytmux-33 ⓖ3):");
    for axis in [Axis::Palette, Axis::Setting, Axis::Screen, Axis::EscKey] {
        let rows: Vec<&divergence::Row> =
            LEDGER.iter().filter(|row| row.axis == axis).collect();
        let todo = rows
            .iter()
            .filter(|row| matches!(row.class, Class::Todo(_)))
            .count();
        println!(
            "  {:<8} {:>2}줄 · 허용 {:>2} · 할 일 {todo}",
            axis.label(),
            rows.len(),
            rows.len() - todo
        );
    }
    let todo = LEDGER
        .iter()
        .filter(|row| matches!(row.class, Class::Todo(_)))
        .count();
    println!("  {:<8} {:>2}줄 · 허용 {:>2} · 할 일 {todo}\n", "합계", LEDGER.len(), LEDGER.len() - todo);
}
