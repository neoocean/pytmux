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

use divergence::{Axis, Class, Dir, LEDGER, canon_binding_names};
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
        .filter(|row| row.dir == Dir::GuiOnly && row.axis == axis)
        .map(|row| row.name)
        .collect()
}

/// 축 하나의 전수와 대장(`GuiOnly`)을 맞대 본다.
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
    let keys: Vec<(Dir, Axis, &str)> =
        LEDGER.iter().map(|row| (row.dir, row.axis, row.name)).collect();
    let mut sorted = keys.clone();
    sorted.sort_unstable();
    assert_eq!(keys, sorted, "대장은 방향·축·이름순이라야 한다");
    let unique: BTreeSet<(Dir, Axis, &str)> = keys.iter().copied().collect();
    assert_eq!(unique.len(), keys.len(), "같은 줄이 두 번 있다");
}


// ══ 새 축 — 정본에 있는데 GUI 가 다르게 하는 것(pytmux-598) ═════════════════════════

/// `CanonDiffers` 중 `Todo`(이슈를 단 못 맞는 줄)의 상한 — ⛔ **올리지 않는다.** 고쳤으면 같은 CL
/// 에서 줄을 지우고 내린다. 지금 셋: `monitor-bell`(642) · `pytmux-config-write`·`tilde-user`(643).
const CANON_DIFFERS_TODO_CEILING: usize = 3;

/// 남의 파일이라 아직 `DIVERGE(pytmux-598)`(이 대장 이슈 자신)으로 남은 표식 — 줄 이름으로 바꾸는 것은
/// 그 파일의 주인 몫이다(2026-11-13 · 두 파일 다 재접속·수명 작업이 쥐고 있다). 상한을 넘거나 낡으면 운다.
const LEGACY_598_MARKERS: &[(&str, usize)] = &[("gui/src/main.rs", 1), ("base/src/lifecycle.rs", 2)];

/// 아래 낱말 게이트에 걸리지만 **낡은 주석 고치기(pytmux-626)** 가 쥔 자리 — 그쪽 CL 이 고치면
/// 이 줄은 낡아서 운다(그때 지운다). (파일, 주석 안의 조각).
const WAITING_626: &[(&str, &str)] = &[
    ("base/src/screens.rs", "그 두 탭은 플러그인이 채우므로 우리에게는 없고"),
];

/// 주석에 이 낱말이 있으면 같은 주석 덩어리 안에 `DIVERGE(` 표식이 있어야 한다.
///
/// ⚠ **좁게 둔다**(재심 2026-11-13 실측): `정본만`·`GUI 만의`·`GUI 에만` 까지 넣으면 비시험
/// 소스에서만 37곳이 걸리고 대부분이 GuiOnly 서술(이미 축마다 센다)이나 «정본만큼» 류였다.
/// 넓히면 면제가 늘고 면제는 아무도 안 읽는다. 출구는 **표식뿐**이다.
const DIVERGENCE_WORDS: &[&str] = &[
    "TUI 만",
    "TUI 에만",
    "우리에겐 없",
    "우리에게는 없",
    "GUI 에 없",
    "GUI 에는 없",
    "네이티브 클라는 아직",
    "정본과 한 칸 다르",
    "한 칸 갈린다",
    "제보가 감수한",
];

/// `client/crates` 의 뿌리.
fn crates_root() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

/// 걷는 자리: 우리 크레이트(상류 스냅샷 제외) + 정본 플러그인 파이썬. (상대 경로, 시험 쪽인가, 글).
fn sources() -> Vec<(String, bool, String)> {
    fn walk(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(&path, out);
            } else if matches!(path.extension().and_then(|e| e.to_str()), Some("rs" | "py")) {
                out.push(path);
            }
        }
    }
    let root = crates_root();
    let mut files = Vec::new();
    for krate in ["base", "proto", "gui", "claude", "clip"] {
        walk(&root.join(krate).join("src"), &mut files);
    }
    walk(&root.join("proto").join("tests"), &mut files);
    walk(&root.join("..").join("..").join("pytmuxlib").join("plugins"), &mut files);
    assert!(files.len() > 100, "걸은 파일이 {} 개뿐이다 — 자리를 잘못 짚었다", files.len());
    let mut out = Vec::new();
    for path in files {
        let rel = path
            .strip_prefix(&root)
            .map(|p| p.to_string_lossy().replace('\\', "/"))
            .unwrap_or_else(|_| path.to_string_lossy().replace('\\', "/"));
        let rel = rel.trim_start_matches("../../").to_owned();
        let test = rel.ends_with("_tests.rs") || rel.starts_with("proto/tests/") || rel.contains("/tests/");
        if let Ok(text) = std::fs::read_to_string(&path) {
            out.push((rel, test, text));
        }
    }
    out
}

/// 글 안의 표식 전수 — (파일, 줄 번호, 가리킨 이름).
fn markers() -> Vec<(String, usize, String)> {
    let mut out = Vec::new();
    for (rel, _, text) in sources() {
        if rel.ends_with("proto/tests/divergence_ledger.rs") || rel.ends_with("common/divergence.rs") {
            continue; // 대장과 게이트 자신은 표식의 낱말을 설명으로 쓴다
        }
        for (no, line) in text.lines().enumerate() {
            let mut rest = line;
            while let Some(at) = rest.find("DIVERGE(") {
                let after = &rest[at + "DIVERGE(".len()..];
                let Some(end) = after.find(')') else { break };
                for name in after[..end].split(',') {
                    out.push((rel.clone(), no + 1, name.trim().to_owned()));
                }
                rest = &after[end..];
            }
        }
    }
    out
}

#[test]
fn every_canon_differs_row_is_well_formed_and_the_todos_are_capped() {
    let mut wrong = Vec::new();
    for row in LEDGER {
        if row.loses.trim().is_empty() {
            wrong.push(format!("{}: `loses` 가 비었다 — 「없음 — …」 이라도 적을 것", row.name));
        }
        if row.dir == Dir::CanonDiffers && matches!(row.class, Class::SameFeature(_)) {
            wrong.push(format!("{}: CanonDiffers 에 SameFeature — «같다» 면 갈림이 아니다", row.name));
        }
        if row.dir == Dir::CanonDiffers && !row.cat.is_empty() {
            wrong.push(format!("{}: CanonDiffers 줄에 팔레트 분류 칸", row.name));
        }
        if row.dir == Dir::CanonDiffers && (row.name.contains(',') || row.name.contains(')')) {
            wrong.push(format!("{}: 이름에 `,`·`)` — 표식 문법과 겹친다", row.name));
        }
    }
    assert!(wrong.is_empty(), "대장의 흠:\n  {}", wrong.join("\n  "));
    let todo: Vec<&str> = LEDGER
        .iter()
        .filter(|row| row.dir == Dir::CanonDiffers && matches!(row.class, Class::Todo(_)))
        .map(|row| row.name)
        .collect();
    assert!(
        todo.len() <= CANON_DIFFERS_TODO_CEILING,
        "정본과 다른데 허용 부류 밖인 줄이 {} 개 — 상한 {CANON_DIFFERS_TODO_CEILING}(올리지 않는다): {todo:?}",
        todo.len()
    );
    assert_eq!(
        todo.len(),
        CANON_DIFFERS_TODO_CEILING,
        "Todo 가 줄었다({todo:?}) — 상한을 같은 CL 에서 내릴 것"
    );
}

#[test]
fn every_marker_names_a_row_or_an_issue() {
    let rows: BTreeSet<&str> = divergence::marker_names().into_iter().collect();
    let mut wrong = Vec::new();
    let mut legacy: std::collections::BTreeMap<&str, usize> = Default::default();
    for (rel, no, name) in markers() {
        if name == "pytmux-598" {
            match LEGACY_598_MARKERS.iter().find(|(f, _)| rel.ends_with(f)) {
                Some((f, _)) => *legacy.entry(f).or_default() += 1,
                None => wrong.push(format!(
                    "{rel}:{no} `DIVERGE(pytmux-598)` — 대장 이슈 자신은 줄을 못 가리킨다. 대장 줄 이름으로 바꿀 것"
                )),
            }
            continue;
        }
        let issue = name
            .strip_prefix("pytmux-")
            .is_some_and(|n| !n.is_empty() && n.chars().all(|c| c.is_ascii_digit()));
        if !issue && !rows.contains(name.as_str()) {
            wrong.push(format!("{rel}:{no} `DIVERGE({name})` — 대장(CanonDiffers)에 그 줄이 없다"));
        }
    }
    assert!(wrong.is_empty(), "갈 곳 없는 표식:\n  {}", wrong.join("\n  "));
    for (file, cap) in LEGACY_598_MARKERS {
        let got = legacy.get(file).copied().unwrap_or(0);
        assert!(got <= *cap, "{file}: `DIVERGE(pytmux-598)` 가 {got} 개 — 상한 {cap}(늘리지 않는다)");
        assert_eq!(got, *cap, "{file}: 옛 표식이 {got} 개로 줄었다 — LEGACY_598_MARKERS 를 내릴 것");
    }
}

#[test]
fn every_canon_differs_row_has_a_marker_in_the_code() {
    let marked: BTreeSet<String> = markers().into_iter().map(|(_, _, name)| name).collect();
    let bare: Vec<&str> = divergence::marker_names()
        .into_iter()
        .filter(|name| !marked.contains(*name))
        .collect();
    assert!(
        bare.is_empty(),
        "대장에만 있고 코드 옆에 표식이 없는 줄: {bare:?}\n\
         그 자리에 `// DIVERGE(<이름>): <요지>` 를 달 것 — 다음 사람은 대장이 아니라 코드를 읽는다"
    );
}

#[test]
fn every_divergence_word_in_a_comment_carries_a_marker() {
    let mut wrong = Vec::new();
    let mut waited: BTreeSet<(&str, &str)> = BTreeSet::new();
    for (rel, test, text) in sources() {
        if test || rel.ends_with("common/divergence.rs") {
            continue; // 시험 쪽 적중은 «예전엔 그렇게 적혀 있었다» 인용이다(실측)
        }
        let py = rel.ends_with(".py");
        let lines: Vec<&str> = text.lines().collect();
        let is_comment = |l: &str| {
            let t = l.trim_start();
            if py { t.starts_with('#') } else { t.starts_with("//") }
        };
        let mut i = 0;
        while i < lines.len() {
            if !is_comment(lines[i]) {
                i += 1;
                continue;
            }
            let start = i;
            while i < lines.len() && is_comment(lines[i]) {
                i += 1;
            }
            let block = lines[start..i].join("\n");
            if block.contains("DIVERGE(") {
                continue;
            }
            let Some(word) = DIVERGENCE_WORDS.iter().find(|w| block.contains(**w)) else {
                continue;
            };
            if let Some(hit) = WAITING_626
                .iter()
                .find(|(f, frag)| rel.ends_with(f) && block.contains(frag))
            {
                waited.insert(*hit);
                continue;
            }
            wrong.push(format!("{rel}:{} «{word}» — {}", start + 1, lines[start].trim()));
        }
    }
    assert!(
        wrong.is_empty(),
        "표식 없이 «정본과 다르다» 를 적은 주석:\n  {}\n\
         참이면 대장(CanonDiffers)에 줄을 더하고 그 주석에 `DIVERGE(<이름>)` 를 단다 · 낡았으면 고쳐 쓴다",
        wrong.join("\n  ")
    );
    let stale: Vec<&(&str, &str)> = WAITING_626.iter().filter(|w| !waited.contains(*w)).collect();
    assert!(stale.is_empty(), "WAITING_626 줄이 더는 안 걸린다(고쳐졌다) — 지울 것: {stale:?}");
}

#[test]
fn the_cli_rows_are_real_arguments_of_the_gui_binary() {
    let main = std::fs::read_to_string(crates_root().join("gui/src/main.rs")).expect("gui main.rs");
    let missing: Vec<&str> = LEDGER
        .iter()
        .filter(|row| row.dir == Dir::GuiOnly && row.axis == Axis::Cli)
        .map(|row| row.name)
        .filter(|name| !main.contains(&format!("\"{name}")))
        .collect();
    assert!(missing.is_empty(), "대장의 명령줄 손잡이가 gui 이진에 없다(죽은 줄): {missing:?}");
}

#[test]
fn the_pane_key_rows_match_the_key_grid() {
    let grid: Value = serde_json::from_str(include_str!("fixtures/key_grid.json")).expect("key_grid.json");
    let rows = grid["rows"].as_array().expect("rows");
    let lost = rows.iter().filter(|r| r["legacy_lost"].as_bool() == Some(true)).count();
    assert!(lost > 0, "격자에 legacy_lost 칸이 0 — `legacy-lost` 줄이 죽었다");
    assert!(LEDGER.iter().any(|r| r.name == "legacy-lost"), "`legacy-lost` 줄이 없다(격자 {lost}칸)");
}

#[test]
fn the_setting_rows_agree_with_the_setting_notes_and_the_hidden_surfaces() {
    use base::config::{SETTING_NOTES, SettingNote};
    let no_effect: BTreeSet<&str> = SETTING_NOTES
        .iter()
        .filter(|(_, n)| *n == SettingNote::NoEffectHere)
        .map(|(k, _)| *k)
        .collect();
    let ledger_no_effect: BTreeSet<&str> = divergence::canon_differs(Axis::Setting)
        .into_iter()
        .filter(|row| row.class == Class::OsWindow)
        .map(|row| row.name)
        .collect();
    assert_eq!(no_effect, ledger_no_effect, "«이 앱에서는 효과 없음» 줄과 대장 ⓒ 설정 줄이 갈린다");
    let hidden: BTreeSet<&str> = base::keymap::HIDDEN_SURFACES.iter().copied().collect();
    let ledger_hidden: BTreeSet<&str> = divergence::canon_differs(Axis::Setting)
        .into_iter()
        .filter(|row| matches!(row.class, Class::Todo(_)))
        .map(|row| row.name)
        .collect();
    assert_eq!(hidden, ledger_hidden, "화면에서 감춘 표면과 대장의 설정 Todo 줄이 갈린다(pytmux-642)");
}

#[test]
fn print_the_ledger() {
    // `--nocapture` 로 보는 표. 실패하지 않는다 — 게이트가 아니라 **자**다.
    println!("\n갈림 대장(pytmux-33 ⓖ3 · pytmux-598):");
    for dir in [Dir::GuiOnly, Dir::CanonDiffers] {
        println!("  {dir:?}");
        for axis in Axis::ALL {
            let rows: Vec<&divergence::Row> =
                LEDGER.iter().filter(|row| row.dir == dir && row.axis == axis).collect();
            if rows.is_empty() {
                continue;
            }
            let todo = rows.iter().filter(|row| matches!(row.class, Class::Todo(_))).count();
            println!("    {:<10} {:>2}줄 · 허용 {:>2} · 할 일 {todo}", axis.label(), rows.len(), rows.len() - todo);
        }
    }
    let todo = LEDGER.iter().filter(|row| matches!(row.class, Class::Todo(_))).count();
    println!("  합계 {}줄 · 할 일 {todo}\n", LEDGER.len());
}
