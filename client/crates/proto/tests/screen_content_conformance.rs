//! **판 내용 대조**(pytmux-571) — 정본 판의 제목·꼬리줄·줄을 GUI 판의 모델과 맞댄다.
//!
//! 판마다 이미 선 자가 여럿이다(키 반응 `screen_key_conformance` · 자리 `screen_anchor_conformance` · 어휘
//! `screen_row_conformance` · 트리/스위처 줄 `tree_switcher_conformance` · 설정 줄 `setting_label_conformance` …).
//! 이 자는 그것을 **다시 세우지 않고** 두 가지를 한다:
//!
//! 1. **전수표**([`COVERAGE`]) — `base::Screen::all()` 의 판마다 «줄(항목·차례·문구)을 어느 자가 재나» 를 적고,
//!    아무 자도 안 재는 판은 이슈와 함께 `Unmeasured` 로 세운다(상한 · pytmux-647 이 0 으로 내린다).
//! 2. **빈칸을 잰다** — ⓐ 제목·꼬리줄(전 판 · 로케일 둘 · `Screen::title`/`hint` 대 정본 i18n 열쇠) ·
//!    ⓑ 인자 폼 판(`Options`/`Layouts`)의 줄(정본 `COMMAND_OPTIONS` 대 `base::COMMAND_OPTIONS`).
//!
//! 픽스처는 `scripts/gen_screen_contents.py` 가 정본에서 뽑는다(AST + `i18n.t`). 갈린 칸은 이슈와 함께
//! 표에 서야 하고 그 수에 상한이 있다 — 맞으면 그 줄이 낡아서 운다.

use base::Screen;
use base::i18n;
use serde_json::Value;
use std::collections::BTreeSet;

fn fixture() -> Value {
    serde_json::from_str(include_str!("fixtures/screen_contents.json"))
        .expect("screen_contents.json 을 못 읽는다 — python3 scripts/gen_screen_contents.py")
}

/// 줄을 누가 재나.
#[derive(Debug, Clone, Copy)]
enum Rows {
    /// 이미 선 자(파일 이름 — `proto/tests/` 에 실재해야 한다).
    By(&'static str),
    /// 이 파일의 `the_option_form_rows_match_the_canon`.
    Here,
    /// 정본 짝이 없다(`canon_class() == None`) — 갈림 대장이 센다.
    NoCanon,
    /// 정본 짝이 판마다 다른 클래스다(플러그인 판) — 그 줄을 재는 자.
    PerPlugin(&'static str),
    /// 아직 아무 자도 안 잰다 — 이슈.
    Unmeasured(&'static str),
}

use Rows::{By, Here, NoCanon, PerPlugin, Unmeasured};

/// `Screen::all()` 26판의 «줄» 전수표.
const COVERAGE: &[(Screen, Rows)] = &[
    (Screen::Keys, Unmeasured("pytmux-647")),
    (Screen::Tabs, By("tree_switcher_conformance.rs")),
    (Screen::Tree, By("tree_switcher_conformance.rs")),
    (Screen::Buffers, Unmeasured("pytmux-647")),
    (Screen::Prompt, By("prompt_args_conformance.rs")),
    (Screen::Confirm, By("prompt_choices_conformance.rs")),
    (Screen::Commands, By("command_conformance.rs")),
    (Screen::Version, Unmeasured("pytmux-647")),
    (Screen::ShellOutput, Unmeasured("pytmux-647")),
    (Screen::RestartCheck, By("server_strings_conformance.rs")),
    (Screen::MergeRemote, Unmeasured("pytmux-647")),
    (Screen::Layouts, Here),
    (Screen::Notices, By("notice_levels_conformance.rs")),
    (Screen::Menu, By("category_conformance.rs")),
    (Screen::Plugins, By("plugin_filter_conformance.rs")),
    (Screen::PluginView, PerPlugin("screen_row_conformance.rs")),
    (Screen::Options, Here),
    (Screen::Hooks, By("hook_events_conformance.rs")),
    (Screen::InfoTabs, Unmeasured("pytmux-647")),
    (Screen::Compose, By("prompt_box_conformance.rs")),
    (Screen::Settings, By("setting_label_conformance.rs")),
    (Screen::Summary, Unmeasured("pytmux-647")),
    (Screen::SearchResults, Unmeasured("pytmux-647")),
    (Screen::Cursor, NoCanon),
    (Screen::DebugStats, By("debug_stats_conformance.rs")),
    (Screen::SavedLayouts, Unmeasured("pytmux-647")),
];
/// `Unmeasured` 의 상한 — ⛔ 올리지 않는다(pytmux-647 이 0 으로 내린다).
const UNMEASURED_CEILING: usize = 9;

/// 제목·꼬리줄이 정본과 갈리는 칸 — (판, `title`|`hint`, 이슈). 상한을 올리지 않는다.
///
/// 꼬리줄은 판의 **키 사실**이라 정본 낱말로 바꾸려면 판마다 키를 다시 따져야 한다 — 그 일이 pytmux-648 이다.
/// 같은 CL 에서 고친 것: `SearchResults` 제목의 영어 짝(en 에서도 한국어였다).
const TEXT_DIVERGENT: &[(Screen, &str, &str)] = &[
    (Screen::Tabs, "hint", "pytmux-648"),
    (Screen::Confirm, "hint", "pytmux-648"),
    (Screen::Commands, "hint", "pytmux-648"),
    (Screen::MergeRemote, "title", "pytmux-648"),
    (Screen::Layouts, "title", "pytmux-648"),
    (Screen::Notices, "title", "pytmux-648"),
    (Screen::Notices, "hint", "pytmux-648"),
    (Screen::Plugins, "title", "pytmux-648"),
    (Screen::Plugins, "hint", "pytmux-648"),
    (Screen::Options, "title", "pytmux-648"),
    (Screen::Settings, "hint", "pytmux-648"),
    (Screen::SearchResults, "title", "pytmux-648"),
    (Screen::SearchResults, "hint", "pytmux-648"),
];
const TEXT_DIVERGENT_CEILING: usize = 13;

/// 정본 인자 폼에 있는데 GUI 표에 없는 명령 — (명령, 이슈). 상한을 올리지 않는다.
const OPTIONS_NOT_OURS: &[(&str, &str)] = &[];

/// 꼬리줄의 우리 문법(괄호)을 벗긴다.
fn bare(s: &str) -> String {
    s.trim().trim_start_matches('(').trim_end_matches(')').trim().to_owned()
}

/// 정본 글(자리표 `{x}` 포함)이 우리 글과 맞나 — 자리표 밖 글자가 차례대로 다 있어야 한다.
fn matches_template(canon: &str, ours: &str) -> bool {
    let mut rest = ours;
    for (i, part) in canon.split('{').enumerate() {
        let lit = if i == 0 { part } else { part.split_once('}').map_or("", |(_, l)| l) };
        let lit = lit.trim();
        if lit.is_empty() {
            continue;
        }
        match rest.find(lit) {
            Some(at) => rest = &rest[at + lit.len()..],
            None => return false,
        }
    }
    true
}

#[test]
fn every_gui_screen_is_in_the_coverage_table_and_the_rulers_exist() {
    let listed: Vec<Screen> = COVERAGE.iter().map(|(s, _)| *s).collect();
    let missing: Vec<&Screen> = Screen::all().iter().filter(|s| !listed.contains(s)).collect();
    assert!(missing.is_empty(), "전수표에 없는 판: {missing:?}");
    assert_eq!(listed.len(), Screen::all().len(), "전수표에 같은 판이 두 번 있다");
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests");
    let mut wrong = Vec::new();
    for (screen, rows) in COVERAGE {
        match rows {
            By(file) | PerPlugin(file) if !dir.join(file).exists() => {
                wrong.push(format!("{screen:?}: 자 `{file}` 가 없다"))
            }
            NoCanon if screen.canon_class().is_some() => {
                wrong.push(format!("{screen:?}: 정본 짝이 있는데 NoCanon"))
            }
            Unmeasured(issue) if !issue.starts_with("pytmux-") => wrong.push(format!("{screen:?}: 이슈가 없다")),
            _ => {}
        }
        if matches!(rows, By(_) | Here | Unmeasured(_)) && screen.canon_class().is_none() {
            wrong.push(format!("{screen:?}: 정본 짝이 없는데 자를 댔다"));
        }
    }
    assert!(wrong.is_empty(), "전수표의 흠:\n  {}", wrong.join("\n  "));
    let unmeasured = COVERAGE.iter().filter(|(_, r)| matches!(r, Unmeasured(_))).count();
    assert!(unmeasured <= UNMEASURED_CEILING, "아무 자도 안 재는 판이 {unmeasured} — 상한 {UNMEASURED_CEILING}");
    assert_eq!(unmeasured, UNMEASURED_CEILING, "줄었다 — 상한을 같은 CL 에서 내릴 것");
}

#[test]
fn titles_and_hints_match_the_canon_in_both_locales() {
    let fx = fixture();
    let mut wrong = Vec::new();
    let mut stale = Vec::new();
    let mut measured = 0;
    for screen in Screen::all() {
        let Some(class) = screen.canon_class() else { continue };
        let Some(canon) = fx["screens"].get(class) else {
            wrong.push(format!("{screen:?}: 정본 클래스 {class} 가 픽스처에 없다"));
            continue;
        };
        for kind in ["title", "hint"] {
            let Some(text) = canon.get(kind).filter(|v| !v.is_null()) else {
                continue; // 정본이 고정 글을 안 쓴다(부르는 쪽이 준다) — 잴 것 없음
            };
            measured += 1;
            let mut bad = Vec::new();
            for loc in ["ko", "en"] {
                let want = text[loc].as_str().unwrap();
                let ours = i18n::with_locale(loc, || if kind == "title" { screen.title() } else { screen.hint() });
                let ok = if kind == "hint" { bare(want) == bare(ours) } else { matches_template(want, ours) };
                if !ok {
                    bad.push(format!("[{loc}] 정본 {want:?} · 우리 {ours:?}"));
                }
            }
            let declared = TEXT_DIVERGENT.iter().find(|(s, k, _)| s == screen && *k == kind);
            match (bad.is_empty(), declared) {
                (true, Some((_, _, issue))) => stale.push(format!("{screen:?} {kind}({issue}) — 이제 맞는다")),
                (false, None) => wrong.push(format!("{screen:?} {kind}: {}", bad.join(" · "))),
                _ => {}
            }
        }
    }
    assert!(measured >= 10, "잰 칸이 {measured} 개뿐이다 — 픽스처를 못 읽었다");
    assert!(wrong.is_empty(), "판의 제목·꼬리줄이 정본과 갈린다:\n  {}", wrong.join("\n  "));
    assert!(stale.is_empty(), "낡은 예외:\n  {}", stale.join("\n  "));
    assert!(TEXT_DIVERGENT.len() <= TEXT_DIVERGENT_CEILING, "상한을 넘었다(올리지 않는다)");
    assert_eq!(TEXT_DIVERGENT.len(), TEXT_DIVERGENT_CEILING, "줄었다 — 상한을 내릴 것");
}

#[test]
fn the_option_form_rows_match_the_canon() {
    let fx = fixture();
    let canon = fx["options"].as_object().expect("options");
    let mut wrong = Vec::new();
    let ours: BTreeSet<&str> = base::COMMAND_OPTIONS.iter().map(|o| o.command).collect();
    for (cmd, specs) in canon {
        if cmd == "select-layout" {
            // 레이아웃 판(`Screen::Layouts`)은 이 표가 아니라 `base::LAYOUT_PRESETS` 를 그린다.
            let want: Vec<(String, String)> = specs[0]["choices"]
                .as_array()
                .unwrap()
                .iter()
                .map(|c| (c[0].as_str().unwrap().to_owned(), c[1].as_str().unwrap().to_owned()))
                .collect();
            let got: Vec<(String, String)> =
                base::LAYOUT_PRESETS.iter().map(|(l, v)| (l.to_string(), v.to_string())).collect();
            if want != got {
                wrong.push(format!("select-layout: 레이아웃 판 줄 정본 {want:?}\n      우리 {got:?}"));
            }
            continue;
        }
        let Some(mine) = base::COMMAND_OPTIONS.iter().find(|o| o.command == cmd) else {
            if !OPTIONS_NOT_OURS.iter().any(|(c, _)| c == cmd) {
                wrong.push(format!("{cmd}: 정본 인자 폼이 GUI 표에 없다"));
            }
            continue;
        };
        let specs = specs.as_array().unwrap();
        if specs.len() != mine.specs.len() {
            wrong.push(format!("{cmd}: 칸 수 정본 {} · 우리 {}", specs.len(), mine.specs.len()));
            continue;
        }
        for (want, got) in specs.iter().zip(mine.specs) {
            if want["label"] != got.label {
                wrong.push(format!("{cmd}: 칸 이름 정본 {} · 우리 {:?}", want["label"], got.label));
            }
            let want: Vec<(String, String)> = want["choices"]
                .as_array()
                .unwrap()
                .iter()
                .map(|c| (c[0].as_str().unwrap().to_owned(), c[1].as_str().unwrap().to_owned()))
                .collect();
            let got: Vec<(String, String)> =
                got.choices.iter().map(|c| (c.label.to_owned(), c.value.to_owned())).collect();
            if want != got {
                wrong.push(format!("{cmd}: 줄 정본 {want:?}\n      우리 {got:?}"));
            }
        }
    }
    for cmd in &ours {
        if !canon.contains_key(*cmd) {
            wrong.push(format!("{cmd}: GUI 에만 있는 인자 폼(정본에 없다)"));
        }
    }
    assert!(wrong.is_empty(), "인자 폼 판의 줄이 정본과 갈린다:\n  {}", wrong.join("\n  "));
}
