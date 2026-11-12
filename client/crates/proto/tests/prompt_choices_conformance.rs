//! 팔레트 **선택지 모드** 적합성(pytmux-634) — 정본 `PromptScreen` 의 선택지 모드 답지
//! (`scripts/gen_prompt_choices.py` → `fixtures/prompt_choices.json`)에 대고 `base::Screens` 의
//! 팔레트를 실제로 눌러 잰다. 답지가 정본 프롬프트와 같은지는 `tests/test_prompt_choice_mode.py`.
//!
//! 재는 것: 명령 이름만 친 팔레트가 선택지 모드에 드나(정본 게이트 · `inactive-dim` 은 안 든다) ·
//! 선택지 값의 차례 · `→`/`↓` 다음 · `←`/`↑` 이전(끝에서 돈다) · `Enter` 가 내는 줄 · 그동안
//! `←→` 가 분류 탭을 안 옮긴다 · 첫 강조 = 지금 값.

use base::keys::{Key, Mods};
use base::screens::{Screen, ScreenKey, Screens};

#[derive(serde::Deserialize)]
struct Fixture {
    rows: Vec<Row>,
    plugin_commands: Vec<String>,
}

#[derive(serde::Deserialize)]
struct Row {
    name: String,
    plugin: bool,
    choice_mode: bool,
    choices: Vec<(String, String)>,
    lines: Vec<String>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/prompt_choices.json")).expect("답지를 못 읽었다")
}

/// 서버 표면 그대로 — 플러그인 명령 풀과 선택지 명령.
fn surface(fx: &Fixture) -> base::PluginSurface {
    base::PluginSurface {
        commands: fx
            .plugin_commands
            .iter()
            .map(|n| base::PluginCommand { name: n.clone(), ..Default::default() })
            .collect(),
        command_options: fx
            .rows
            .iter()
            .filter(|r| r.plugin)
            .map(|r| base::plugins::PluginCommandOption {
                name: r.name.clone(),
                choices: r.choices.clone(),
                state: String::new(),
            })
            .collect(),
        ..Default::default()
    }
}

fn typed(fx: &Fixture, line: &str) -> Screens {
    let mut s = Screens::new();
    s.set_plugins(surface(fx));
    s.open_palette();
    for c in line.chars() {
        s.press(Key::Char(c), Mods::NONE);
    }
    s
}

#[test]
fn the_choice_mode_gate_and_values_match_the_canon_prompt() {
    let fx = fixture();
    assert!(fx.rows.len() >= 20, "답지가 너무 작다");
    let mut wrong = Vec::new();
    for row in &fx.rows {
        let s = typed(&fx, &row.name);
        let got = s.palette_choices();
        match (row.choice_mode, &got) {
            (true, Some(ch)) => {
                let values: Vec<&str> = ch.choices.iter().map(|(_, v)| v.as_str()).collect();
                let want: Vec<&str> = row.choices.iter().map(|(_, v)| v.as_str()).collect();
                if values != want {
                    wrong.push(format!("{}: 값 {values:?} ≠ 정본 {want:?}", row.name));
                }
            }
            (false, None) => {}
            (want, got) => wrong.push(format!(
                "{}: 정본 선택지 모드 = {want} · 우리 = {}",
                row.name,
                got.is_some()
            )),
        }
    }
    assert!(wrong.is_empty(), "선택지 모드가 갈렸다:\n  {}", wrong.join("\n  "));
}

#[test]
fn arrows_walk_the_choices_and_enter_runs_the_canon_line() {
    let fx = fixture();
    let mut measured = 0;
    for row in fx.rows.iter().filter(|r| r.choice_mode) {
        let n = row.lines.len();
        let mut s = typed(&fx, &row.name);
        let tab = s.palette_tab();
        assert_eq!(s.press(Key::Right, Mods::NONE), Some(ScreenKey::Consumed));
        assert_eq!(s.palette_tab(), tab, "{}: 선택지 모드에서 → 가 분류 탭을 옮겼다", row.name);
        assert_eq!(s.palette_choices().unwrap().pick, 1 % n, "{}: →", row.name);
        s.press(Key::Left, Mods::NONE);
        s.press(Key::Up, Mods::NONE);
        assert_eq!(s.palette_choices().unwrap().pick, n - 1, "{}: ← ↑ 가 끝에서 안 돌았다", row.name);
        s.press(Key::Down, Mods::NONE);
        assert_eq!(s.palette_choices().unwrap().pick, 0, "{}: ↓", row.name);
        s.press(Key::Right, Mods::NONE);
        let out = s.press(Key::Enter, Mods::NONE);
        assert_eq!(out, Some(ScreenKey::RunLine(row.lines[1 % n].clone())), "{}: Enter", row.name);
        assert_ne!(s.top(), Some(Screen::Commands), "{}: Enter 뒤 팔레트가 남았다", row.name);
        measured += 1;
    }
    assert!(measured >= 20, "잰 명령이 {measured} 뿐이다");
}

#[test]
fn the_first_highlight_is_the_current_value_and_typing_resets_to_it() {
    let fx = fixture();
    let mut s = typed(&fx, "synchronize-pane");
    // 정본 `_current_choice_idx` — 지금 값과 같은 선택지(토글·켜기·끄기 중 `off`).
    s.set_palette_option_values(vec![("synchronize-panes".into(), "off".into())]);
    s.press(Key::Char('s'), Mods::NONE);
    let ch = s.palette_choices().expect("선택지 모드가 아니다");
    assert_eq!(ch.choices[ch.pick].1, "off", "첫 강조가 지금 값이 아니다");
    s.press(Key::Right, Mods::NONE);
    assert_eq!(s.palette_choices().unwrap().pick, 0, "끝에서 돌아 첫째로");
    // 글을 고치면 지금 값 자리로 돌아간다(정본은 입력이 바뀔 때마다 `_refresh_hint`).
    s.press(Key::Backspace, Mods::NONE);
    s.press(Key::Char('s'), Mods::NONE);
    let ch = s.palette_choices().unwrap();
    assert_eq!(ch.choices[ch.pick].1, "off");
    // 인자를 치면 선택지 모드가 아니다 — ←→ 는 다시 분류 탭이다.
    s.press(Key::Char(' '), Mods::NONE);
    s.press(Key::Char('o'), Mods::NONE);
    assert!(s.palette_choices().is_none(), "인자를 쳤는데 선택지 모드다");
}
