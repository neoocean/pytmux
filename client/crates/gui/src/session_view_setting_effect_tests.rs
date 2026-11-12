//! 설정 효과 — 정본 설정 줄마다 **값을 바꾸면 이 뷰에서 무엇이 달라지나**(pytmux-568).
//!
//! 패리티 래칫은 «그 이름의 줄이 있나»만 센다. 그 물음으로는 `mouse-drag-threshold` 가 Done 으로 세지면서
//! GUI 에서 아무 데도 안 읽혔다(기본값도 3 대 1). 여기서는 정본 `SETTINGS`(픽스처 `setting_effects.json`
//! 의 `rows` — `gen_setting_effects.py` 가 정본에서 뽑는다)와 설정 문법 자가 찾은 런타임 옵션
//! (`config_grammar.json` 의 `apply_options`)을 모집단으로, 줄마다 값 A·B 를 세우고 **같은 입력**을 먹여
//! 관측값이 달라지는지 잰다.
//!
//! # 관측값의 종류
//!
//! - **서버 줄** — 설정 판에서 그 값을 골랐을 때 서버로 **나간 프레임**이 정본 `apply_setting` 이 낸
//!   프레임(`server_frames`)과 JSON 그대로 같다. 바꾼 값이 다르면 프레임도 다르므로 그 자체가 효과다.
//! - **클라 줄** — [`probe`] 가 값을 세우고 그 값을 읽는 자리를 실제로 누르거나(마우스·붙여넣기·복사·명령)
//!   한 프레임을 그려(글자·색·세로 자리) 관측값 문자열을 낸다.
//! - **소스 줄**([`SOURCE_ONLY`]) — 이 하네스로 못 누르는 줄의 자리. 그때는 소비처 줄이 소스에 있는지만 잰다.
//!   지금 **0줄**이다(pytmux-645 가 일곱을 행동으로 올렸다 · 상한 0).
//!
//! 선언된 무효과(`SettingNote::NoEffectHere` — `alt-scroll`)는 거꾸로 «달라지지 않는다» 를 **양성으로**
//! 잰다 — 소비처가 생기면 선언이 낡았다고 운다.

use super::*;
use serde_json::Value;

fn fixture() -> Value {
    serde_json::from_str(include_str!("../../proto/tests/fixtures/setting_effects.json"))
        .expect("setting_effects.json 을 못 읽는다 — python3 scripts/gen_setting_effects.py")
}

/// 이 하네스로 못 누르는 줄 — (키, 소비처가 읽는 `self.config.<칸>` 또는 정확한 소스 조각, 이슈).
/// ⛔ 늘리지 않는다([`SOURCE_ONLY_CEILING`]).
///
/// 비었다(pytmux-645 · 2026-11-13) — 일곱 줄을 행동으로 올렸다: 모호폭(`set_ambig` 프레임) · 언어(설정 판 →
/// `lang_setter`) · mode-keys(스크롤 모드 키가 내는 `scroll` 프레임) · mouse-debug/-log(진단 로그 파일) ·
/// set-titles/-string(`title_to_set`). 비어 있어도 표를 두는 것은 다음 줄이 같은 모양·같은 상한으로 서게 하려는 것이다.
const SOURCE_ONLY: &[(&str, &str, &str)] = &[];
const SOURCE_ONLY_CEILING: usize = 0;

thread_local! {
    /// `language` 줄이 로케일 함수에 넘긴 값(전역 로케일 대신 여기 적는다).
    static LAST_LANG: std::cell::Cell<Option<&'static str>> = const { std::cell::Cell::new(None) };
}

fn record_lang(loc: &str) -> &'static str {
    let loc = if loc == "en" { "en" } else { "ko" };
    LAST_LANG.with(|c| c.set(Some(loc)));
    loc
}

/// 정본 `SETTINGS` 에 있는데 GUI 설정 판에 줄이 없는 것 — (키, 이슈).
/// 비었다 — `monitor-bell` 이 다시 보인다(pytmux-642 · 사람 결정 2026-11-13).
const NOT_IN_OUR_SETTINGS: &[(&str, &str)] = &[];

fn screen_rows(n: usize) -> ServerMessage {
    let rows: Vec<Value> = (0..n)
        .map(|i| serde_json::json!([[format!("line-{i}-0123456789abcdef"), {}]]))
        .collect();
    serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1, "rows": rows, "cursor": null, "wrap": [], "top": 0
    }))
    .unwrap()
}

fn status_one_tab(name: &str) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "status", "session": "main",
        "windows": [{"index": 0, "name": name, "active": true, "remote": name.starts_with('⇄')}]
    }))
    .unwrap()
}

/// 한 프레임을 그려 글자·글자색·세로 자리·면 색을 한 문자열로.
fn paint(messages: Vec<ServerMessage>, setup: impl FnOnce(&mut SessionView) + 'static) -> String {
    painted_scene_setup(messages, &[], setup, |scene| {
        let texts: Vec<String> = scene
            .painted_texts()
            .map(|t| format!("{}@{:.0}:{:?}", t.text, t.bounds.origin().y(), t.color))
            .collect();
        let fills: Vec<String> = scene
            .layers()
            .flat_map(|layer| layer.rects.iter())
            .filter_map(|r| match r.background {
                warpui::elements::Fill::Solid(c) => Some(format!("{c:?}@{:.0}", r.bounds.origin().y())),
                _ => None,
            })
            .collect();
        format!("{texts:?}|{fills:?}")
    })
}

fn sent_frames(sent: &Sent) -> String {
    let out = sent.lock().unwrap().clone();
    let frames: Vec<String> = out.iter().map(|o| o.to_frame().to_string()).collect();
    frames.join(" ")
}

/// 클라 줄 하나의 관측값 — 값 `v` 를 세우고 같은 입력을 먹인다. 모르는 줄이면 `None`.
fn probe(key: &str, v: &str) -> Option<String> {
    let on = v == "on";
    let v = v.to_owned();
    Some(match key {
        "inactive-dim" | "inactive-dim-ratio" => {
            let ratio_row = key == "inactive-dim-ratio";
            paint(vec![layout_two_panes_active_right(), screen_rows(3)], move |view| {
                if ratio_row {
                    view.config.inactive_dim = true;
                    view.config.inactive_dim_ratio = v.parse().unwrap();
                } else {
                    view.config.inactive_dim = on;
                }
                proto::config_apply::apply_config_globals(&view.config, &mut view.state);
            })
        }
        "tab-bar" => paint(vec![layout_one_pane(), status_one_tab("ONLY-TAB")], move |view| {
            view.config.tab_bar_always = v == "always";
        }),
        "status-position" => paint(vec![layout_one_pane(), status_one_tab("T")], move |view| {
            view.config.status_left = "LEFT-ORACLE".to_owned();
            view.config.status_position = v;
        }),
        "remote-title" => paint(vec![layout_one_pane(), status_one_tab("⇄office:zsh")], move |view| {
            view.config.remote_title = v;
        }),
        "status-left" | "status-right" | "status-bg" | "status-fg" => {
            let key = key.to_owned();
            paint(vec![layout_one_pane(), status_one_tab("T")], move |view| match key.as_str() {
                "status-left" => view.config.status_left = v,
                "status-right" => view.config.status_right = v,
                "status-bg" => view.config.status_bg = v,
                _ => view.config.status_fg = v,
            })
        }
        "status" | "status-format" => {
            let key = key.to_owned();
            paint(vec![layout_one_pane(), status_one_tab("T")], move |view| {
                view.config.status_left = "MAIN-ORACLE".to_owned();
                if key == "status" {
                    view.apply_set_line(&format!("status {v}"));
                } else {
                    view.apply_set_line("status 2");
                    view.apply_set_line(&format!("status-format 1 {v}"));
                }
            })
        }
        "mouse" | "mouse-drag-copy" | "mouse-drag-threshold" => {
            let (mut view, sent) = mouse_app_view(3);
            match key {
                "mouse" => view.config.mouse = on,
                "mouse-drag-copy" => {
                    view.config.mouse_drag_copy = match v.as_str() {
                        "off" => base::config::DragCopy::Off,
                        "shift" => base::config::DragCopy::Shift,
                        _ => base::config::DragCopy::On,
                    }
                }
                _ => view.config.mouse_drag_threshold = v.parse().unwrap(),
            }
            let (selecting, _) = press_drag_release(&mut view, &sent, (7, 3), (9, 3));
            format!("selecting={selecting} {}", sent_frames(&sent))
        }
        "alt-scroll" => {
            let (mut view, sent) = mouse_app_view(3);
            view.config.alt_scroll = on;
            view.handle_wheel(true, Some((7, 3)));
            view.handle_wheel(false, Some((7, 3)));
            view.pump_headless();
            sent_frames(&sent)
        }
        "copy-unwrap" => {
            let (mut view, _tx, _sent) = harness();
            view.config.copy_unwrap = on;
            let (text, width, first_col) = folded_case();
            view.copy_geom = Some((width, first_col));
            view.unwrap_copied(text)
        }
        "set-clipboard" => {
            let (mut view, tx, _sent) = harness();
            view.config.set_clipboard = on;
            view.os_copier = counting_copier;
            tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
            let osc52: ServerMessage = serde_json::from_value(serde_json::json!({
                "t": "clipboard", "pane": 1, "data": "67aZ7J28IOq4gA=="
            }))
            .unwrap();
            tx.send(LinkEvent::Message(Box::new(osc52))).unwrap();
            view.pump_headless();
            format!("{:?}", view.window_clip_for_test)
        }
        "strip-box-drawing" => {
            let (mut view, tx, sent) = harness();
            tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
            view.pump_headless();
            view.config.strip_box_drawing = on;
            format!("{:?}", pasted_text(&mut view, &sent, "│ a │", PasteEntry::Clipboard))
        }
        "prefix" => {
            let (mut view, tx, sent) = harness();
            tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
            view.pump_headless();
            // `apply_set_line` 은 쓰고 **다시 읽는다** — 시험의 쓰기 사물함과 읽는 자리가 달라 값이 안 남는다.
            // 그래서 그 함수가 하는 두 일(설정 칸 · 키 모드)을 그대로 한다.
            let prefix = base::config::parse_key(&v).expect("prefix 낱말");
            view.config.prefix = prefix;
            view.mode.set_prefix(prefix);
            view.handle_key(Key::Char('a'), Mods::CTRL);
            view.pump_headless();
            view.handle_key(Key::Char('%'), Mods::NONE);
            view.pump_headless();
            sent_frames(&sent)
        }
        "default-path" | "claude-command" => {
            let (mut view, _tx, _sent) = harness();
            if key == "default-path" {
                view.config.default_path = v;
            } else {
                view.config.claude_command = v;
            }
            let claude = proto::command::action_to_command(base::Action::NewClaudeTab).expect("claude 탭");
            let tab = view.with_default_path(claude);
            let win = view.with_default_path(
                proto::command::action_to_command(base::Action::NewTab).expect("새 탭"),
            );
            format!("{} {}", tab.to_frame(), win.to_frame())
        }
        "ambiguous-width" => {
            // 서버로 나가는 `set_ambig` 프레임과 뷰의 폭 판정(pytmux-578). 폭은 프로세스 전역이기도 해서
            // 재기 전 값으로 돌려 둔다 — 형제 시험이 보는 폭을 바꾸지 않게.
            let before = proto::compose::ambiguous_wide();
            let (mut view, tx, sent) = harness();
            tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
            view.pump_headless();
            view.apply_set_line(&format!("ambiguous-width {v}"));
            view.pump_headless();
            let got = format!("wide={} {}", view.ambig_wide, sent_frames(&sent));
            proto::compose::set_ambiguous_wide(before);
            got
        }
        "language" => {
            // 설정 판 줄을 실제로 골라 `Action::SetLang` 이 로케일 함수에 무엇을 넘기나 — 전역은 안 건드린다.
            let (mut view, _tx, _sent) = harness();
            view.lang_setter = record_lang;
            LAST_LANG.with(|c| c.set(None));
            let row = row_of("language");
            let idx = ["ko", "en"].iter().position(|c| *c == v).unwrap();
            // 이미 그 로케일이면 판은 아무것도 안 한다(`None`) — 그것도 관측값이다.
            view.apply_setting_pick(base::config::setting_pick_at(row, &view.setting_values(), idx));
            format!("{:?}", LAST_LANG.with(|c| c.get()))
        }
        "mode-keys" => {
            let (mut view, tx, sent) = harness();
            tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
            view.pump_headless();
            view.config.mode_keys = v;
            // 스크롤 모드는 키 표가 연다(`prefix [` → `ModeChanged(Scroll)`) — 액션으로는 안 들어간다.
            view.handle_key(Key::Char('b'), Mods::CTRL);
            view.handle_key(Key::Char('['), Mods::NONE);
            view.pump_headless();
            sent.lock().unwrap().clear();
            view.handle_key(Key::Char('u'), Mods::CTRL);
            view.handle_key(Key::Char('n'), Mods::CTRL);
            view.pump_headless();
            sent_frames(&sent)
        }
        "mouse-debug" | "mouse-log" => {
            let (mut view, sent) = mouse_app_view(3);
            let path = std::env::temp_dir()
                .join(format!("pytmux-effect-{}-{key}-{v}.mouse.log", std::process::id()));
            let _ = std::fs::remove_file(&path);
            view.set_mouse_log_path_for_test(path.clone());
            view.apply_set_line(&format!("{key} {v}"));
            let _ = press_drag_release(&mut view, &sent, (7, 3), (7, 3));
            let logged = std::fs::read_to_string(&path).map(|t| t.lines().count()).unwrap_or(0);
            let _ = std::fs::remove_file(&path);
            format!("logged_lines>0={}", logged > 0)
        }
        "set-titles" | "set-titles-string" => {
            let (mut view, tx, _sent) = harness();
            tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
            tx.send(LinkEvent::Message(Box::new(status_one_tab("TITLE-TAB")))).unwrap();
            view.pump_headless();
            if key == "set-titles" {
                view.config.set_titles = on;
            } else {
                view.config.set_titles = true;
                view.config.set_titles_string = v;
            }
            format!("{:?}", view.title_to_set())
        }
        "status-interval" => {
            let (mut view, _tx, _sent) = harness();
            view.config.status_interval = v.parse().unwrap();
            view.last_status = std::time::Instant::now() - std::time::Duration::from_secs(10);
            format!("{}", view.tick_status())
        }
        "plugins" | "list-keys" => {
            let (mut view, _tx, _sent) = harness();
            let row = row_of(key);
            if v == "pick" {
                view.apply_setting_pick(base::config::setting_pick(row, &view.setting_values()));
            }
            format!("{:?}", view.screens.top())
        }
        _ => return None,
    })
}

/// 두 값 — 줄마다 «기본과 다른 값» 을 하나 고른다(정본 픽스처의 기본값이 A).
fn two_values(key: &str, kind: &str, default: &str) -> (String, String) {
    let other = match key {
        "inactive-dim-ratio" => "0.6",
        "tab-bar" => if default == "auto" { "always" } else { "auto" },
        "status-position" => if default == "top" { "bottom" } else { "top" },
        "remote-title" => "name",
        "mouse-drag-copy" => "off",
        "mouse-drag-threshold" => "1",
        "prefix" => "C-a",
        "default-path" => "home",
        "claude-command" => "my-claude --x",
        "status-interval" => "5",
        "status-left" | "status-right" => "OTHER-ORACLE",
        "status-bg" | "status-fg" => "red",
        "status" => "off",
        "status-format" => "FMT-ORACLE",
        "language" => if default == "en" { "ko" } else { "en" },
        "mode-keys" => "emacs",
        "ambiguous-width" => if default == "wide" { "narrow" } else { "wide" },
        "mouse-debug" | "mouse-log" => "on",
        "set-titles-string" => "#W-ORACLE",
        "plugins" | "list-keys" => "pick",
        _ if kind == "bool" => if default == "on" { "off" } else { "on" },
        _ => panic!("{key}: 두 번째 값을 정하지 않았다"),
    };
    let default = match key {
        "status" => "1".to_owned(),
        "status-format" => "".to_owned(),
        "mouse-debug" | "mouse-log" => "off".to_owned(),
        "set-titles-string" => "#S:#I:#W".to_owned(),
        "plugins" | "list-keys" => "none".to_owned(),
        _ => default.to_owned(),
    };
    (default, other.to_owned())
}

/// 접힌 줄이 실제로 펴지는 사례(정본 `unwrap_copy_text` 픽스처에서 첫 번째로 바뀌는 것).
fn folded_case() -> (String, usize, usize) {
    let v: Value = serde_json::from_str(include_str!("../../proto/tests/fixtures/unwrap_copy.json")).unwrap();
    let case = v["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["text"] != c["want"])
        .expect("펴지는 사례가 없다");
    (
        case["text"].as_str().unwrap().to_owned(),
        case["width"].as_u64().unwrap() as usize,
        case["first_col"].as_u64().unwrap() as usize,
    )
}

fn row_of(key: &str) -> usize {
    base::config::SETTINGS
        .iter()
        .position(|s| s.key == key)
        .unwrap_or_else(|| panic!("우리 설정 판에 `{key}` 줄이 없다"))
}

/// 모집단 — 정본 SETTINGS 38줄 + 설정 문법 자의 런타임 옵션 중 그 밖의 것.
fn population() -> Vec<(String, String, String)> {
    let fx = fixture();
    let mut out: Vec<(String, String, String)> = fx["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| {
            (
                r["key"].as_str().unwrap().to_owned(),
                r["type"].as_str().unwrap().to_owned(),
                r["backend"].as_str().unwrap().to_owned(),
            )
        })
        .collect();
    let grammar: Value =
        serde_json::from_str(include_str!("../../proto/tests/fixtures/config_grammar.json")).unwrap();
    for name in grammar["apply_options"].as_array().unwrap() {
        let name = name.as_str().unwrap();
        // 밑줄 철자(`mouse_drag_copy`)와 `tabbar` 는 같은 줄의 별칭이다 — 문법 자가 잰다.
        if name.contains('_') || name == "tabbar" || out.iter().any(|(k, _, _)| k == name) {
            continue;
        }
        out.push((name.to_owned(), "opt".to_owned(), "config".to_owned()));
    }
    out
}

fn default_of(key: &str) -> String {
    fixture()["defaults"][key].as_str().unwrap_or("").to_owned()
}

#[test]
fn every_client_setting_changes_something_observable() {
    let no_effect: Vec<&str> = base::config::SETTING_NOTES
        .iter()
        .filter(|(_, n)| *n == base::config::SettingNote::NoEffectHere)
        .map(|(k, _)| *k)
        .collect();
    let mut dead = Vec::new();
    let mut unprobed = Vec::new();
    let mut table = Vec::new();
    for (key, kind, backend) in population() {
        if backend != "config" && backend != "lang" && backend != "link" {
            continue; // 서버 줄은 아래 프레임 시험이 잰다
        }
        if no_effect.contains(&key.as_str()) || SOURCE_ONLY.iter().any(|s| s.0 == key) {
            continue;
        }
        let (a, b) = two_values(&key, &kind, &default_of(&key));
        let (Some(pa), Some(pb)) = (probe(&key, &a), probe(&key, &b)) else {
            unprobed.push(key);
            continue;
        };
        if pa == pb {
            dead.push(format!("{key}: {a:?} 와 {b:?} 가 같은 관측값 — {pa}"));
        }
        table.push(format!("{key}: {a} → {b}"));
    }
    assert!(unprobed.is_empty(), "관측값을 안 댄 클라 줄(probe 를 더하거나 SOURCE_ONLY 에): {unprobed:?}");
    assert!(
        dead.is_empty(),
        "값을 바꿔도 아무것도 안 달라지는 줄 — «줄만 있고 효과 없음»:\n  {}",
        dead.join("\n  ")
    );
    assert!(table.len() >= 20, "잰 줄이 {} 개뿐이다 — 모집단을 못 읽었다", table.len());
    println!("설정 효과(클라 {}줄):\n  {}", table.len(), table.join("\n  "));
}

#[test]
fn declared_no_effect_rows_really_have_no_effect() {
    for (key, note) in base::config::SETTING_NOTES {
        if *note != base::config::SettingNote::NoEffectHere {
            continue;
        }
        let (a, b) = (probe(key, "on"), probe(key, "off"));
        assert!(a.is_some(), "{key}: 무효과 선언 줄인데 같은 입력을 먹일 probe 가 없다");
        assert_eq!(a, b, "{key}: «이 앱에서는 효과 없음» 인데 관측값이 달라졌다 — 선언이 낡았다");
    }
}

#[test]
fn source_only_rows_still_have_their_consumer_and_are_capped() {
    let src = [
        include_str!("session_view.rs"),
        include_str!("../../base/src/config.rs"),
        include_str!("../../proto/src/config_apply.rs"),
    ]
    .join("\n");
    // 스냅샷(`setting_values`)은 소비처가 아니다 — 그 함수 몸통을 뺀 글에서 찾는다.
    let snapshot_start = src.find("fn setting_values(&self)").expect("setting_values 를 못 찾았다");
    let snapshot_end = snapshot_start + src[snapshot_start..].find("\n    }\n").expect("몸통 끝");
    let outside = format!("{}{}", &src[..snapshot_start], &src[snapshot_end..]);
    for (key, needle, issue) in SOURCE_ONLY {
        assert!(issue.starts_with("pytmux-"), "{key}: 이슈가 없다");
        assert!(
            outside.contains(needle),
            "{key}: 소비처 `{needle}` 가 소스에서 사라졌다 — 효과가 끊겼거나 자리를 다시 대야 한다"
        );
    }
    assert!(SOURCE_ONLY.len() <= SOURCE_ONLY_CEILING, "소스로만 재는 줄이 상한을 넘었다(올리지 않는다)");
    assert_eq!(SOURCE_ONLY.len(), SOURCE_ONLY_CEILING, "줄었다 — 상한을 같은 CL 에서 내릴 것");
}

/// 설정 판의 줄 값을 `current` 로 둔 스냅샷(서버 줄 — 그 값의 반대를 고르게).
fn values_with(view: &SessionView, key: &str, current: &str) -> base::config::SettingValues {
    let mut v = view.setting_values();
    let on = current == "on";
    match key {
        "single-border" => v.single_border = on,
        "pane-border-status" => v.border_status = on,
        "automatic-rename" => v.auto_rename = on,
        "monitor-activity" => v.monitor_activity = on,
        "synchronize-panes" => v.sync = on,
        "coalesce-repaints" => v.coalesce_repaints = on,
        "nest-auto-attach" => v.nest_auto_attach = on,
        "exit-empty" => v.exit_empty = Some(on),
        "monitor-bell" => v.monitor_bell = on,
        "win-mouse-motion" => v.win_mouse_motion = on,
        "vt-parser" => v.vt_parser = current.to_owned(),
        "window-size" => v.window_size = current.to_owned(),
        other => panic!("{other}: 서버 줄 스냅샷 칸을 모른다"),
    }
    v
}

fn without_t(mut frame: Value) -> Value {
    if let Some(map) = frame.as_object_mut() {
        map.remove("t");
    }
    frame
}

#[test]
fn every_server_setting_sends_the_canon_frame_from_the_settings_screen() {
    let fx = fixture();
    let frames = fx["server_frames"].as_object().expect("server_frames");
    let mut wrong = Vec::new();
    let mut measured = 0;
    for (key, per) in frames {
        if NOT_IN_OUR_SETTINGS.iter().any(|(k, _)| k == key) {
            assert!(
                !base::config::SETTINGS.iter().any(|s| s.key == key),
                "{key}: 이제 우리 설정 판에 줄이 있다 — NOT_IN_OUR_SETTINGS 에서 지울 것"
            );
            continue;
        }
        let row = row_of(key);
        let choices = base::config::SETTINGS[row].choices().expect("서버 줄은 고르는 줄이다");
        for (value, want) in per.as_object().unwrap() {
            let other = choices.iter().find(|c| *c != value).expect("값이 둘 이상");
            // 두 길: 값을 눌러 고르기(`setting_pick_at`) · `Enter`/`→` 로 넘기기(`setting_pick_dir`).
            let idx = choices.iter().position(|c| c == value).unwrap();
            for path in ["at", "dir"] {
                let (mut view, _tx, sent) = harness();
                let values = values_with(&view, key, other);
                let pick = if path == "at" {
                    base::config::setting_pick_at(row, &values, idx)
                } else if choices.len() == 2 {
                    base::config::setting_pick_dir(row, &values, true)
                } else {
                    continue; // 셋 이상 값의 넘기기는 `setting_cycle_conformance` 가 잰다
                };
                view.apply_setting_pick(pick);
                view.pump_headless();
                let got: Vec<Value> = sent.lock().unwrap().iter().map(|o| without_t(o.to_frame())).collect();
                measured += 1;
                if got != vec![want.clone()] {
                    wrong.push(format!("{key}={value} ({path}): 정본 {want} · 우리 {got:?}"));
                }
            }
        }
    }
    assert!(measured >= 20, "잰 경우가 {measured} 개뿐이다 — 픽스처를 못 읽었다");
    assert!(wrong.is_empty(), "설정 판의 서버 줄이 정본과 다른 것을 보낸다:\n  {}", wrong.join("\n  "));
}
