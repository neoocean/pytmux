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
    (Screen::Keys, Here),
    (Screen::Tabs, By("tree_switcher_conformance.rs")),
    (Screen::Tree, By("tree_switcher_conformance.rs")),
    (Screen::Buffers, Here),
    (Screen::Prompt, By("prompt_args_conformance.rs")),
    (Screen::Confirm, By("prompt_choices_conformance.rs")),
    (Screen::Commands, By("command_conformance.rs")),
    (Screen::Version, Here),
    (Screen::ShellOutput, Here),
    (Screen::RestartCheck, By("server_strings_conformance.rs")),
    (Screen::MergeRemote, Here),
    (Screen::Layouts, Here),
    (Screen::Notices, By("notice_levels_conformance.rs")),
    (Screen::Menu, By("category_conformance.rs")),
    (Screen::Plugins, By("plugin_filter_conformance.rs")),
    (Screen::PluginView, PerPlugin("screen_row_conformance.rs")),
    (Screen::Options, Here),
    (Screen::Hooks, By("hook_events_conformance.rs")),
    (Screen::InfoTabs, Here),
    (Screen::Compose, By("prompt_box_conformance.rs")),
    (Screen::Settings, By("setting_label_conformance.rs")),
    (Screen::Summary, Here),
    (Screen::SearchResults, Here),
    (Screen::Cursor, NoCanon),
    (Screen::DebugStats, By("debug_stats_conformance.rs")),
    (Screen::SavedLayouts, Here),
];
/// `Unmeasured` 의 상한 — ⛔ 올리지 않는다. 0 = 판 전수가 재진다(pytmux-647 이 아홉을 `Here` 로 옮겼다 —
/// 아래 `the_rows_of_the_list_screens_match_the_canon`).
const UNMEASURED_CEILING: usize = 0;

/// 줄이 정본과 갈리는 판 — (판, 이슈, 사유). ⛔ 상한을 올리지 않는다. 맞으면 그 줄이 낡아서 운다.
///
/// 같은 CL 에서 고친 것 넷: 버전 판의 빈 줄 하나(`version_lines`) · 검색 판의 `gwin` 번호와 줄 없는 히트의 `0`
/// (`SearchHit::cells`) · run-shell 판의 `\r` 줄 나누기(`py_splitlines`).
const ROW_DIVERGENT: &[(&str, &str, &str)] = &[];
const ROW_DIVERGENT_CEILING: usize = 0;

/// 제목·꼬리줄이 정본과 갈리는 칸 — (판, `title`|`hint`, 이슈). 상한을 올리지 않는다.
///
/// 꼬리줄은 판의 **키 사실**이라 정본 낱말로 바꾸려면 판마다 키를 다시 따져야 한다 — 그 일이 pytmux-648 이다.
/// 같은 CL 에서 고친 것: `SearchResults` 제목의 영어 짝(en 에서도 한국어였다).
const TEXT_DIVERGENT: &[(Screen, &str, &str)] = &[];
const TEXT_DIVERGENT_CEILING: usize = 0;

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

// ── 판 «안» 의 줄 — 정본 판을 고정 자료로 지은 줄(`rows`) 대 GUI 판의 모델 (pytmux-647) ───────────

fn msg(v: Value) -> proto::ServerMessage {
    serde_json::from_value(v).expect("메시지 모양")
}

fn strs(v: &Value) -> Vec<String> {
    v.as_array().expect("배열").iter().map(|s| s.as_str().unwrap_or("").to_owned()).collect()
}

/// 정본 줄이 우리 줄에 **차례대로** 다 있나(우리는 더할 수만 있다 — 원칙 2).
fn ordered_subset(canon: &[String], ours: &[String]) -> Result<(), String> {
    let mut at = 0;
    for want in canon {
        match ours[at..].iter().position(|o| o == want) {
            Some(i) => at += i + 1,
            None => return Err(format!("정본 줄 {want:?} 가 우리 판에 없다(또는 차례가 다르다)")),
        }
    }
    Ok(())
}

/// 판 하나를 잰다 — `Ok` 면 정본과 같다.
fn measure(screen: &str, rows: &Value, inputs: &Value) -> Result<(), String> {
    match screen {
        "Buffers" => {
            let mut state = proto::SessionState::new();
            state.apply(msg(serde_json::json!({"t": "buffers", "items": inputs["buffers"]})));
            let ours: Vec<String> = state.buffers().iter().map(proto::info::buffer_row).collect();
            let want = strs(&rows["buffers"]["items"]);
            (ours == want).then_some(()).ok_or(format!("정본 {want:?} · 우리 {ours:?}"))
        }
        "SavedLayouts" => {
            let mut screens = base::Screens::new();
            screens.open_saved_layouts(strs(&inputs["layouts"]), false);
            let want = strs(&rows["saved_layouts"]["items"]);
            (screens.saved_layouts() == want.as_slice())
                .then_some(())
                .ok_or(format!("정본 {want:?} · 우리 {:?}", screens.saved_layouts()))
        }
        "MergeRemote" => {
            let mut state = proto::SessionState::new();
            state.apply(msg(serde_json::json!({"t": "status", "windows": inputs["merge_tabs"]})));
            let ours: Vec<String> = state.merge_candidates().into_iter().map(|(_, l)| l).collect();
            let want = strs(&rows["merge_remote"]);
            (ours == want).then_some(()).ok_or(format!("정본 {want:?} · 우리 {ours:?}"))
        }
        "ShellOutput" => {
            for (text, want) in inputs["shell"].as_array().unwrap().iter().zip(rows["shell"].as_array().unwrap()) {
                let text = text.as_str().unwrap();
                // GUI 는 빈 출력이면 판을 안 띄운다(`outcome.text.trim().is_empty()`) — 정본도 같다(`text.strip()`).
                let ours = if text.trim().is_empty() {
                    None
                } else {
                    let mut state = proto::SessionState::new();
                    state.set_shell_output(text);
                    Some(state.shell_output().to_vec())
                };
                let want = (!want.is_null()).then(|| strs(want));
                if ours != want {
                    return Err(format!("{text:?}: 정본 {want:?} · 우리 {ours:?}"));
                }
            }
            Ok(())
        }
        "Keys" => {
            // 정본 `list-keys` = 마우스 제스처 + 사용자 바인딩(머리줄·빈 줄 제외한 자료 줄). GUI 판은 그 위에
            // 키 표를 더 싣는다 — 정본 줄이 차례대로 다 있어야 한다.
            let binds: Vec<base::config::Bind> = inputs["binds"]
                .as_object()
                .unwrap()
                .iter()
                .filter_map(|(k, v)| base::Config::parse(&format!("bind {k} {}", v.as_str().unwrap())).binds.into_iter().next())
                .collect();
            let ours: Vec<String> = base::keymap::list_keys_lines(&binds)
                .into_iter()
                .flat_map(|(k, w)| [format!("{k} — {w}"), format!("{k} → {w}")])
                .collect();
            let want: Vec<String> = strs(&rows["keys"])
                .into_iter()
                .filter(|l| l.starts_with("  ") || l.contains(" → "))
                .map(|l| l.trim().to_owned())
                .collect();
            ordered_subset(&want, &ours)
        }
        "InfoTabs" => {
            let mut state = proto::SessionState::new();
            state.apply(msg(serde_json::json!({"t": "status", "windows": [], "capture": true,
                                                "capture_path": "/tmp/c/pane-1.log", "capture_size": 10})));
            let ours: Vec<String> = proto::info::tabs(&state, "x", 0.0).into_iter().map(|t| t.0.to_string()).collect();
            ordered_subset(&strs(&rows["info_tab_names"]), &ours)
        }
        "SearchResults" => {
            let mut state = proto::SessionState::new();
            state.apply(msg(inputs["search"].clone()));
            let ours: Vec<Vec<String>> = state
                .search_results()
                .map(|sr| sr.items.iter().map(|it| it.cells().to_vec()).collect())
                .unwrap_or_default();
            let want: Vec<Vec<String>> = rows["search"]["cells"].as_array().unwrap().iter().map(strs).collect();
            (ours == want).then_some(()).ok_or(format!("정본 {want:?}\n      우리 {ours:?}"))
        }
        "Version" => {
            let mut state = proto::SessionState::new();
            state.apply(msg(serde_json::json!({"t": "version", "version": "p4:123", "uptime": 5.0, "pid": 42})));
            let lines = proto::info::version_lines(&state);
            let v = &rows["version"];
            let blank: Vec<u64> = lines.iter().enumerate().filter(|(_, l)| l.is_empty()).map(|(i, _)| i as u64).collect();
            let want_blank: Vec<u64> = v["blank"].as_array().unwrap().iter().filter_map(Value::as_u64).collect();
            let joined = lines.join("\n");
            let ok = lines.len() as u64 == v["count"].as_u64().unwrap()
                && blank == want_blank
                && lines.first().map(String::as_str) == v["head"].as_str()
                && strs(&v["has"]).iter().all(|s| joined.contains(s.as_str()));
            ok.then_some(()).ok_or(format!("정본 모양 {v} · 우리 {lines:?}"))
        }
        "Summary" => {
            // 정본 = 머리줄 + 블록 **전부**(굴릴 수 있다). GUI 판도 전부 싣는다(pytmux-653) — 머리줄은
            // `footer::head`(Claude 항목이 없을 때), 블록 줄은 판이 그리는 세 조각의 글 `Block::summary_line`.
            let mut state = proto::SessionState::new();
            state.apply(msg(serde_json::json!({"t": "layout", "cols": 80, "rows": 24, "active": 1,
                "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24, "active": true}]})));
            state.apply(msg(serde_json::json!({"t": "blocks", "pane": 1, "blocks": inputs["blocks"]})));
            let blocks = state.active_blocks();
            let mut ours = vec![proto::footer::head(blocks.len(), 0, None, false, proto::footer::Fold::Open)];
            ours.extend(blocks.iter().map(proto::Block::summary_line));
            let want = strs(&rows["summary"]);
            (ours == want).then_some(()).ok_or(format!("정본 {want:?}\n      우리 {ours:?}"))
        }
        other => Err(format!("{other}: 재는 법이 없다")),
    }
}

#[test]
fn the_rows_of_the_list_screens_match_the_canon() {
    let fx = fixture();
    let mut wrong = Vec::new();
    let mut stale = Vec::new();
    let here: Vec<String> = COVERAGE
        .iter()
        .filter(|(_, r)| matches!(r, Here))
        .map(|(s, _)| format!("{s:?}"))
        .filter(|s| s != "Options" && s != "Layouts") // 이 둘은 `the_option_form_rows_match_the_canon`
        .collect();
    assert_eq!(here.len(), 9, "줄을 이 자가 재는 판이 아홉이 아니다: {here:?}");
    for loc in ["ko", "en"] {
        let rows = &fx["rows"][loc];
        let inputs = &rows["inputs"];
        for screen in &here {
            // 정보 판의 탭 이름도 로케일 둘에서 맞댄다 — 정본이 카탈로그를 쓰게 됐다(pytmux-654).
            let got = i18n::with_locale(loc, || measure(screen, rows, inputs));
            let declared = ROW_DIVERGENT.iter().find(|(s, _, _)| s == screen);
            match (got, declared) {
                (Ok(()), Some((_, issue, _))) => stale.push(format!("{screen}({issue}) — 이제 맞는다")),
                (Err(why), None) => wrong.push(format!("[{loc}] {screen}: {why}")),
                _ => {}
            }
        }
    }
    assert!(wrong.is_empty(), "판의 줄이 정본과 갈린다:\n  {}", wrong.join("\n  "));
    assert!(stale.is_empty(), "낡은 예외:\n  {}", stale.join("\n  "));
    assert!(ROW_DIVERGENT.len() <= ROW_DIVERGENT_CEILING, "상한을 넘었다(올리지 않는다)");
    assert_eq!(ROW_DIVERGENT.len(), ROW_DIVERGENT_CEILING, "줄었다 — 상한을 내릴 것");
}

/// 키 도움말 두 판의 **절 차례**(pytmux-656) — `list-keys` 판(`Screen::Keys`)과 설정 판 «키» 탭.
///
/// 정본: `list-keys` = 마우스 제스처 → 사용자 바인딩 · «키» 탭 = ESC 모드 → prefix → 사용자 바인딩. GUI 는 정본 절이
/// **그 차례 그대로 먼저** 오고, GUI 가 더 싣는 절(키 표·스크롤·블록 등)은 뒤에만 선다(더하기만).
#[test]
fn the_key_panels_order_their_sections_like_the_canon() {
    let fx = fixture();
    let binds: Vec<base::config::Bind> = base::Config::parse("bind x split-window -h").binds;
    let sections = |lines: &[(String, String)]| -> Vec<String> {
        // 절 머리줄 → 절 이름(같은 줄이 `key_help_sections` 의 그 절 첫 줄이다).
        let heads: Vec<(String, &str)> = base::keymap::key_help_sections(&binds)
            .into_iter()
            .map(|(s, rows)| (rows[0].0.clone(), s.name()))
            .collect();
        lines
            .iter()
            .filter_map(|(k, w)| w.is_empty().then(|| heads.iter().find(|(h, _)| h == k).map(|(_, n)| n.to_string())).flatten())
            .collect()
    };
    for loc in ["ko", "en"] {
        let rows = &fx["rows"][loc];
        let (ours_list, ours_settings) = i18n::with_locale(loc, || {
            (
                sections(&base::keymap::list_keys_lines(&binds)),
                sections(&base::keymap::settings_key_lines(&binds)),
            )
        });
        for (what, canon, ours) in [
            ("list-keys", strs(&rows["keys_sections"]), ours_list),
            ("설정 «키» 탭", strs(&rows["settings_key_sections"]), ours_settings),
        ] {
            assert!(canon.len() >= 2, "[{loc}] {what}: 정본 절이 {canon:?} — 생성기를 의심할 것");
            assert_eq!(
                &ours[..canon.len().min(ours.len())],
                canon.as_slice(),
                "[{loc}] {what}: 정본 절 차례 {canon:?} 가 앞서야 한다 · 우리 {ours:?}"
            );
        }
    }
}
