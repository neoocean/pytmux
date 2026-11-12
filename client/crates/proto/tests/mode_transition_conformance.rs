//! **모드 전이 적합성** — 「그 키를 누르면 모드가 어떻게 되나」를 정본에서 뽑아 대조한다.
//!
//! # 왜 이 자가 필요한가 (pytmux-33 ⓖ3 · 2026-09-02)
//!
//! [`pytmux-185`] 가 GUI 의 **최소 요건**으로 못박은 것은 그림이 아니다 — *"정본에 같은
//! 기능이 있으면 **키 반응 · 취소 조건 · 포커스 이동**이 GUI 의 최소 요건"* 이다. 그런데
//! 이 저장소에서 그 축을 재는 자가 **하나도 없었다**:
//!
//! - `parity.rs` 는 *"그 키가 있나"* 만 묻는다. 있는데 **다르게 굴면** 초록이다.
//! - `interaction.rs`(상호작용 계약)는 **화면**의 키를 잰다 — 세션 뷰의 모드는 그 밖이다.
//! - `key_bytes_conformance.rs` 는 **평소 모드**에서 패널로 나가는 바이트를 잰다 —
//!   esc/prefix 모드 안의 바이트는 그 밖이다.
//!
//! 그 사이로 실제로 넷이 새 있었다(이 파일이 처음 재서 잡은 것 · 고침은 같은 CL):
//!
//! | | 정본 | GUI(고치기 전) |
//! | --- | --- | --- |
//! | esc + 방향키 | 모드 **유지**(연속으로 패널을 옮긴다) | 한 번 옮기고 모드가 풀렸다 |
//! | esc + 모르는 키 | 모드 **종료**(`else: self._exit_esc()`) | 모드 유지 — 그 뒤 타이핑이 통째로 사라진다 |
//! | esc + 둘째 `ESC` | 모드만 빠지고 **패널로 ESC 를 안 보낸다**(사용자 요청 · 56632 불변) | 패널로 ESC 를 보냈다 |
//! | esc + `Shift+ESC` | 패널에 ESC 를 보내고 모드 종료 | 아무 일도 안 했다 |
//!
//! ⛔ 셋째가 요점이다 — **패리티 표는 그 갈림을 `Done` 으로 적고 있었다**
//! (`e_esc` 줄: *"명령 모드에서 두 번째 ESC 가 패널로 간다"*). 손으로 적은 줄은 우리가
//! 하는 일을 적지 정본이 하는 일을 적지 않는다. 그래서 이 파일은 **정본 소스**에서 뽑는다.
//!
//! # 무엇을 대조하나
//!
//! `scripts/gen_client_surface_fixture.py` 가 정본의 `_handle_esc_mode`·`_handle_prefix`
//! 를 AST 로 걸어 분기마다 두 가지를 적는다 — `mode`(`exit`/`stay`/`scroll`)와
//! `sends`(패널로 나가는 바이트 상수). 마지막 `else` 는 **모르는 키**의 답이라 `"*"` 다.
//!
//! ⚠ 재는 것은 [`ModeState`] 한 층이다. 「그 액션이 옳은 일을 하나」는 여기 물음이 아니다
//! (그건 패리티 표와 세션 뷰 오라클의 몫이다) — 여기는 **모드와 바이트**만 본다.

use std::collections::BTreeMap;

#[path = "common/divergence.rs"]
mod divergence;

use base::keys::{InputMode, Key, KeyOutcome, ModeState, Mods};
use divergence::key_of;
use serde::Deserialize;

#[derive(Deserialize)]
struct Effect {
    mode: String,
    sends: Option<String>,
}

#[derive(Deserialize)]
struct Fx {
    esc_key_modes: BTreeMap<String, Effect>,
    prefix_key_modes: BTreeMap<String, Effect>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/client_surface.json"))
        .expect("픽스처를 읽을 수 없다 — scripts/gen_client_surface_fixture.py 로 다시 뽑을 것")
}

/// 우리가 **누를 수 없는** 정본 분기와 그 이유.
///
/// 이유 없는 예외는 그냥 빠뜨린 것이다 — 아래 `the_untestable_list_is_exact` 가 이 목록이
/// 낡지 않게 지킨다.
static UNTESTABLE: &[(&str, &str)] = &[(
    "\u{0}",
    "Windows 콘솔이 Shift/Ctrl/Alt **단독** 키다운에도 주는 아티팩트(ctrl+@ · UnicodeChar 0)다. \
     정본은 Textual 이 그것을 키 이벤트로 만들어 버려 손으로 막지만, 우리 키 층에는 그 키가 없다",
)];

/// 「모르는 키」를 대신하는 키 — 어느 표에도 없어야 한다.
const UNKNOWN: (Key, Mods) = (Key::Char('i'), Mods::NONE);

fn want_mode(now: InputMode, want: &str) -> InputMode {
    match want {
        "exit" => InputMode::Normal,
        "stay" => now,
        "scroll" => InputMode::Scroll,
        other => panic!("픽스처가 모르는 모드를 말한다: {other}"),
    }
}

/// 이 결과가 패널로 보내는 바이트(16진). 액션으로 접힌 것도 **같은 뜻**이라 편다.
fn sent(outcome: &KeyOutcome) -> Option<String> {
    match outcome {
        KeyOutcome::ToPane(bytes) => Some(hex(bytes)),
        // 뷰가 바이트로 옮기는 두 액션 — 정본은 그 자리에서 바로 `send_input` 한다.
        // 여기서 펴 두지 않으면 「보내나」를 물을 수가 없다(모드만 남는다).
        KeyOutcome::Action(base::Action::SendEscape) => Some("1b".to_owned()),
        KeyOutcome::Action(base::Action::SendBacktick) => Some("60".to_owned()),
        _ => None,
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// 그 모드에 **실제로 들어가서** 키 하나를 누른다 — 모드를 손으로 세우지 않는다.
fn press_in_mode(mode: InputMode, key: Key, mods: Mods) -> (InputMode, KeyOutcome) {
    let mut state = ModeState::default();
    match mode {
        InputMode::Command => {
            state.press(Key::Escape, Mods::NONE);
        }
        InputMode::Prefix => {
            state.press(Key::Char('b'), Mods::CTRL);
        }
        other => panic!("이 자는 esc·prefix 두 모드만 잰다: {other:?}"),
    }
    assert_eq!(state.mode(), mode, "모드에 못 들어갔다");
    let outcome = state.press(key, mods);
    (state.mode(), outcome)
}

fn check(label: &str, mode: InputMode, table: &BTreeMap<String, Effect>) {
    let mut wrong: Vec<String> = Vec::new();
    for (name, want) in table {
        if name == "*" || UNTESTABLE.iter().any(|(n, _)| n == name) {
            continue;
        }
        let Some((key, mods)) = key_of(name) else {
            wrong.push(format!("{name:?}: 이 이름을 우리 키로 못 읽는다"));
            continue;
        };
        let (got_mode, outcome) = press_in_mode(mode, key, mods);
        let want_mode = want_mode(mode, &want.mode);
        if got_mode != want_mode {
            wrong.push(format!(
                "{label} {name:?}: 정본은 모드가 {want_mode:?} 인데 우리는 {got_mode:?}"
            ));
        }
        // 정본이 값을 안 적은 자리(`"?"`)는 **보내는지**까지만 견준다.
        let got = sent(&outcome);
        let ok = match want.sends.as_deref() {
            None => got.is_none(),
            Some("?") => got.is_some(),
            Some(bytes) => got.as_deref() == Some(bytes),
        };
        if !ok {
            wrong.push(format!(
                "{label} {name:?}: 정본이 패널로 보내는 것은 {:?} 인데 우리는 {:?}",
                want.sends, got
            ));
        }
    }
    assert!(
        wrong.is_empty(),
        "정본과 다르게 구는 키:\n  {}\n\
         ⛔ 이 갈림은 [[pytmux-185]] 의 허용 목록(단말이 못 주는 키 · 픽셀 그림 · OS 창 통합)\n\
         어디에도 안 든다 — 고치거나, 왜 정본이 틀렸는지를 정본에서 고칠 것.",
        wrong.join("\n  ")
    );
}

#[test]
fn the_esc_mode_reacts_exactly_like_canon() {
    check("esc", InputMode::Command, &fixture().esc_key_modes);
}

#[test]
fn the_prefix_mode_reacts_exactly_like_canon() {
    check("prefix", InputMode::Prefix, &fixture().prefix_key_modes);
}

/// 데모 판 표(`base::BLOCK_BINDINGS`)로 옮긴 키들. 정본 esc 모드에는 없는 글자라
/// **모르는 키와 똑같이 굴어야** 한다(pytmux-466 · 449 ⑶).
const MOVED_TO_BLOCK_TABLE: &[&str] =
    &["q", "j", "k", "g", "G", "enter", "space"];

#[test]
fn the_demo_pane_keys_do_not_leak_into_the_session_esc_mode() {
    // ⛔ 이 자가 없으면 그 갈림은 **아무도 안 잰다**: 아래 `check` 는 정본 픽스처에 있는
    //    이름만 돌고(이 일곱은 정본 esc 표에 없다), 갈림 대장은 「표에 있나」만 센다.
    //    실제로 종전에는 `esc q` 가 **창을 닫았고** 그것이 대장의 유일한 «결과 있는» 갈림
    //    이었다. 사람 결정은 «표를 가른다» 였고, 가른 뒤에도 그 일곱이 정본의 catch-all
    //    (`*` = 모드만 푼다)과 같이 구는지는 **눌러 봐야** 안다.
    let fx = fixture();
    let want = fx.esc_key_modes.get("*").expect("픽스처에 catch-all(`*`)이 없다");
    for name in MOVED_TO_BLOCK_TABLE {
        assert!(
            !fx.esc_key_modes.contains_key(*name),
            "정본 esc 모드에 `{name}` 이 생겼다 — 그러면 이 시험의 전제가 무너진다\n\
             (그때는 위 `check` 가 재는 키다: 이 목록에서 빼고 표에 되돌릴지 정할 것)"
        );
        let (key, mods) = key_of(name).expect("우리 키로 못 읽는 이름");
        let (got, outcome) = press_in_mode(InputMode::Command, key, mods);
        assert_eq!(
            got,
            want_mode(InputMode::Command, &want.mode),
            "esc `{name}`: 정본의 모르는 키와 모드가 다르다"
        );
        assert_eq!(sent(&outcome), None, "esc `{name}`: 패널로 바이트를 보냈다");
        assert_eq!(
            outcome,
            KeyOutcome::Ignored,
            "esc `{name}`: 아직 esc 표에 남아 액션을 낸다 — `BLOCK_BINDINGS` 로 옮길 것"
        );
    }
}

#[test]
fn an_unknown_key_follows_the_canon_catch_all() {
    // ⛔ 이 한 줄이 위 표의 둘째를 잡은 자리다. 「표에 있는 키」만 재면 **표 밖**이
    //   어떻게 되는지는 영영 안 잰다 — 그리고 사용자가 실제로 만나는 것이 그쪽이다.
    let fx = fixture();
    for (label, mode, table) in [
        ("esc", InputMode::Command, &fx.esc_key_modes),
        ("prefix", InputMode::Prefix, &fx.prefix_key_modes),
    ] {
        let want = table.get("*").expect("픽스처에 catch-all(`*`)이 없다");
        let (key, mods) = UNKNOWN;
        let (got, outcome) = press_in_mode(mode, key, mods);
        assert_eq!(
            got,
            want_mode(mode, &want.mode),
            "{label}: 모르는 키를 눌렀을 때의 모드가 정본과 다르다"
        );
        assert_eq!(sent(&outcome), None, "{label}: 모르는 키가 패널로 바이트를 보냈다");
    }
}

#[test]
fn the_unknown_key_really_is_unknown() {
    // 위 시험의 전제 — `i` 가 어느 표에도 없어야 그 시험이 뜻을 갖는다.
    let (key, mods) = UNKNOWN;
    let Key::Char(c) = key else { panic!("글자 키라야 한다") };
    assert!(mods == Mods::NONE, "수정키가 붙으면 표를 다르게 탄다");
    let name = c.to_string();
    assert!(
        !base::BINDINGS.iter().any(|b| b.key == name),
        "`{name}` 이 esc 표에 생겼다 — 다른 글자로 바꿀 것"
    );
    assert!(
        !base::PREFIX_BINDINGS.iter().any(|b| b.key == name),
        "`{name}` 이 prefix 표에 생겼다 — 다른 글자로 바꿀 것"
    );
    assert!(!c.is_ascii_digit(), "숫자는 표가 아니라 규칙이라 안 된다");
}

#[test]
fn the_untestable_list_is_exact() {
    let fx = fixture();
    for (name, why) in UNTESTABLE {
        assert!(!why.is_empty(), "{name:?}: 이유 없는 예외는 그냥 빠뜨린 것이다");
        assert!(
            fx.esc_key_modes.contains_key(*name) || fx.prefix_key_modes.contains_key(*name),
            "정본에 없는 분기가 예외 목록에 남아 있다: {name:?}"
        );
        assert!(
            key_of(name).is_none(),
            "이제 누를 수 있는 키가 예외 목록에 있다 — 빼고 잴 것: {name:?}"
        );
    }
}

#[test]
fn the_fixture_says_something_about_both_modes() {
    // 빈 표를 통과로 두면 「아무것도 안 재면서 초록」이 된다(생성기 머리말의 그 함정).
    let fx = fixture();
    assert!(fx.esc_key_modes.len() > 10, "esc 표가 너무 작다 — 뽑기가 깨졌다");
    assert!(fx.prefix_key_modes.len() > 20, "prefix 표가 너무 작다 — 뽑기가 깨졌다");
}

// ── 평소 모드 · 바인딩 차례 · 시간 규칙 · 정규화 (pytmux-606 · 607 · 610 · 583) ─────────────
//
// 위 자는 esc·prefix **모드 안**만 쟀다. 평소 모드에서 정본이 패널 앞에서 가로채는 키는
// 어느 자도 안 쟀고, GUI 는 그 키를 전부 패널로 흘렸다(백틱·F10·F12·Alt+숫자). 그리고
// prefix F12 는 「모드가 풀리나」만 재서 초록이었다 — **무엇이 일어나나**는 안 봤다.
// 아래는 `scripts/gen_session_keys_fixture.py` 가 정본 `on_key`·`_handle_prefix` 에서 뽑은
// `session_keys.json` 과 맞댄다.

use base::keys::{EscDebounce, Routed};

#[derive(Deserialize)]
struct Norm {
    jamo: bool,
    mac_opt: bool,
}

#[derive(Deserialize)]
struct SessionKeys {
    normal_key_modes: BTreeMap<String, String>,
    normal_alt_digit: bool,
    root_bindings_after: Vec<String>,
    prefix_order: Vec<String>,
    display_secs: f64,
    esc_debounce_secs: f64,
    mode_normalize: BTreeMap<String, Norm>,
    jamo: BTreeMap<String, String>,
    mac_opt_number_row: BTreeMap<String, String>,
}

fn session_keys() -> SessionKeys {
    serde_json::from_str(include_str!("fixtures/session_keys.json"))
        .expect("픽스처를 읽을 수 없다 — scripts/gen_session_keys_fixture.py 로 다시 뽑을 것")
}

/// 정본 평소 갈래 중 **뷰가 base 앞에서** 가로채는 키와 그 자리. base 한 층으로는 못 잰다 —
/// 이유 없는 예외는 빠뜨린 것이다(`a_normal_key_the_view_owns_is_named` 가 지킨다).
static VIEW_OWNED: &[(&str, &str)] = &[
    ("ctrl+v", "붙여넣기 — `gui::session_view::keydown_action` 의 `is_paste_chord` 가 base 앞에서 받는다(pytmux-364)"),
    ("super+v", "같은 자리(`Cmd`/`Super`+V)"),
];

/// 정본 낱말(`does`) → 평소 모드에서 우리가 내야 하는 결과.
fn normal_want(does: &str) -> Option<KeyOutcome> {
    Some(match does {
        "esc" => KeyOutcome::ModeChanged(InputMode::Command),
        "menu" => KeyOutcome::Action(base::Action::ShowMenu),
        "command_prompt" => KeyOutcome::Action(base::Action::ShowCommands),
        // 오버레이가 없으면 패널에 ESC — 오버레이 닫기는 뷰가 그 사실을 안다.
        "close_overlay_else_pane" => KeyOutcome::ToPane(vec![0x1b]),
        "paste" | "select_tab" => return None,
        other => panic!("픽스처가 모르는 낱말을 말한다: {other} — 이 표에 줄을 더할 것"),
    })
}

fn normal_name(name: &str) -> Option<(Key, Mods)> {
    match name.strip_prefix("char:") {
        Some(ch) => {
            let mut it = ch.chars();
            match (it.next(), it.next()) {
                (Some(c), None) => Some((Key::Char(c), Mods::NONE)),
                _ => None,
            }
        }
        None => key_of(name),
    }
}

#[test]
fn the_normal_mode_chrome_keys_do_what_canon_does() {
    // 관문(pytmux-606): 다섯 키 각각 「패널 바이트 0 + 그 액션·모드」.
    let fx = session_keys();
    let mut wrong = Vec::new();
    let mut pressed = 0;
    for (name, does) in &fx.normal_key_modes {
        if VIEW_OWNED.iter().any(|(n, _)| n == name) || name == "alt+<digit>" {
            continue;
        }
        let Some((key, mods)) = normal_name(name) else {
            wrong.push(format!("{name:?}: 우리 키로 못 읽는다"));
            continue;
        };
        let Some(want) = normal_want(does) else { continue };
        let mut state = ModeState::default();
        let got = state.press(key, mods);
        pressed += 1;
        if got != want {
            wrong.push(format!("평소 {name:?}({does}): 정본은 {want:?} 인데 우리는 {got:?}"));
        }
    }
    assert!(pressed >= 5, "누른 키가 {pressed} 뿐이다 — 대조가 샜다");
    assert!(wrong.is_empty(), "평소 모드에서 정본과 다르게 구는 키:\n  {}", wrong.join("\n  "));
}

#[test]
fn alt_digit_switches_tabs_and_never_reaches_the_pane() {
    let fx = session_keys();
    assert!(fx.normal_alt_digit, "정본 `_alt_digit` 규칙이 사라졌다 — 이 시험의 전제");
    assert_eq!(fx.normal_key_modes.get("alt+<digit>").map(String::as_str), Some("select_tab"));
    for d in '1'..='9' {
        let mut state = ModeState::default();
        let got = state.press(Key::Char(d), Mods::ALT);
        assert_eq!(
            got,
            KeyOutcome::Action(base::Action::SelectTab(d.to_digit(10).unwrap() as u8)),
            "Alt+{d}: 패널로 `ESC {d}` 가 아니라 그 번호 탭이어야 한다"
        );
        assert_eq!(state.mode(), InputMode::Normal);
    }
}

#[test]
fn mac_option_glyphs_in_normal_mode_switch_tabs_or_are_swallowed() {
    // 정본: ⌥1=¡ … 는 탭 전환, 탭 번호가 없는 -/= 자리는 **삼킨다**(패널에 안 찍힌다).
    let fx = session_keys();
    for (glyph, phys) in &fx.mac_opt_number_row {
        let c = glyph.chars().next().unwrap();
        let p = phys.chars().next().unwrap();
        let mut state = ModeState::default();
        let got = state.press(Key::Char(c), Mods::NONE);
        let want = match p.to_digit(10) {
            Some(n) => KeyOutcome::Action(base::Action::SelectTab(n as u8)),
            None => KeyOutcome::Ignored,
        };
        assert_eq!(got, want, "평소 {glyph:?}(⌥{phys})");
        assert_eq!(sent(&got), None, "{glyph:?} 가 패널로 샜다");
    }
}

#[test]
fn an_unknown_normal_key_still_goes_to_the_pane() {
    // 「모르는 키 = 패널」 — 크롬 키를 넓히다가 타이핑을 삼키면 그게 더 큰 결함이다.
    let mut state = ModeState::default();
    let got = state.press(UNKNOWN.0, UNKNOWN.1);
    assert_eq!(got, KeyOutcome::ToPane(b"i".to_vec()));
}

#[test]
fn a_normal_key_the_view_owns_is_named() {
    let fx = session_keys();
    for (name, why) in VIEW_OWNED {
        assert!(!why.is_empty(), "{name}: 이유 없는 예외");
        assert!(fx.normal_key_modes.contains_key(*name), "정본에 없는 예외가 남았다: {name}");
    }
}

/// 늘 걸리는 바인딩 — 「이 자리에서 바인딩을 보나」만 묻는다.
fn always(_after_prefix: bool, _key: Key, _mods: Mods) -> Option<&'static str> {
    Some("bound")
}

#[test]
fn builtin_chrome_keys_win_over_root_bindings_like_canon() {
    // 관문(pytmux-607): `bind -n Escape`·`bind -n F12`·root 에 prefix 키 → 내장이 이긴다.
    let fx = session_keys();
    let mut wrong = Vec::new();
    for name in &fx.root_bindings_after {
        if VIEW_OWNED.iter().any(|(n, _)| n == name) {
            continue;
        }
        // `Shift+ESC` 는 오버레이를 닫을 때만 먹는다 — 없으면 root 표를 지나 패널로 간다.
        let shift_esc = fx.normal_key_modes.get(name).map(String::as_str)
            == Some("close_overlay_else_pane");
        let keys: Vec<(Key, Mods)> = if name == "alt+<digit>" {
            vec![(Key::Char('1'), Mods::ALT)]
        } else {
            match normal_name(name) {
                Some(k) => vec![k],
                None => {
                    wrong.push(format!("{name:?}: 우리 키로 못 읽는다"));
                    continue;
                }
            }
        };
        for (key, mods) in keys {
            let mut state = ModeState::default();
            let got = state.press_routed("", key, mods, always);
            let bound = matches!(got, Routed::Bound(_));
            if bound != shift_esc {
                wrong.push(format!("{name:?}: 바인딩이 {}", if bound { "내장을 덮었다" } else { "안 걸렸다" }));
            }
        }
    }
    // prefix 키 자체(정본 `self.prefix_key` — 이름이 아니라 설정값이라 픽스처에 글자로 안 실린다).
    let mut state = ModeState::default();
    assert!(
        matches!(state.press_routed("", Key::Char('b'), Mods::CTRL, always), Routed::Key(_)),
        "root 에 prefix 키를 걸면 내장(prefix 진입)이 이겨야 한다"
    );
    assert_eq!(state.mode(), InputMode::Prefix);
    // 모르는 키는 root 표가 가져간다(그것이 `bind -n` 의 자리다).
    let mut state = ModeState::default();
    assert_eq!(state.press_routed("", UNKNOWN.0, UNKNOWN.1, always), Routed::Bound("bound"));
    assert!(wrong.is_empty(), "root 바인딩 차례가 정본과 다르다:\n  {}", wrong.join("\n  "));
}

#[test]
fn root_bindings_do_not_fire_inside_esc_scroll_or_block_modes() {
    // 정본 root 표는 평소 모드 갈래에만 있다 — 모드 안의 키는 그 모드의 것이다.
    for (label, enter) in [
        ("esc", (Key::Escape, Mods::NONE)),
        ("prefix [ → scroll", (Key::Char('b'), Mods::CTRL)),
    ] {
        let mut state = ModeState::default();
        state.press(enter.0, enter.1);
        if label.contains("scroll") {
            state.press(Key::Char('['), Mods::NONE);
            assert_eq!(state.mode(), InputMode::Scroll);
        }
        let mut asked = false;
        let got = state.press_routed("", Key::Char('k'), Mods::NONE, |_, _, _| {
            asked = true;
            Some("bound")
        });
        assert!(!asked && matches!(got, Routed::Key(_)), "{label}: 모드 안에서 바인딩을 봤다");
    }
    let mut state = ModeState::default();
    state.enter_block();
    let got = state.press_routed("", Key::Down, Mods::NONE, always);
    assert!(matches!(got, Routed::Key(KeyOutcome::Block(_))), "블록 모드에서 바인딩을 봤다");
}

#[test]
fn the_prefix_table_sits_between_passthrough_and_builtins_like_canon() {
    let fx = session_keys();
    assert_eq!(fx.prefix_order, ["prefix_twice", "user_bindings", "f12_toggle"]);
    // ① prefix 두 번 = 패스스루 — `bind C-b …` 도 그것을 못 덮는다.
    let mut state = ModeState::default();
    state.press(Key::Char('b'), Mods::CTRL);
    let got = state.press_routed("", Key::Char('b'), Mods::CTRL, always);
    assert_eq!(got, Routed::Key(KeyOutcome::ToPane(vec![0x02])), "prefix 두 번을 바인딩이 덮었다");
    // ② 사용자 표가 내장보다 먼저 — `bind F12 …` 는 중첩 토글을 덮는다. 모드는 한 키만.
    let mut state = ModeState::default();
    state.press(Key::Char('b'), Mods::CTRL);
    assert_eq!(state.press_routed("", Key::Function(12), Mods::NONE, always), Routed::Bound("bound"));
    assert_eq!(state.mode(), InputMode::Normal);
    assert!(state.prefix_on(), "바인딩이 가져갔는데 토글이 같이 돌았다");
}

#[test]
fn prefix_f12_toggles_the_outer_prefix_not_just_the_mode() {
    // 관문(pytmux-606): 모드만이 아니라 **액션까지**. 종전에는 표에 없어 모드만 풀렸고,
    // 위 `check` 는 모드만 재서 그것을 초록으로 셌다.
    let mut state = ModeState::default();
    state.press(Key::Char('b'), Mods::CTRL);
    assert_eq!(state.press(Key::Function(12), Mods::NONE), KeyOutcome::PrefixToggled(false));
    assert_eq!(state.mode(), InputMode::Normal);
    assert!(!state.prefix_on());
    // 꺼진 동안 prefix 키는 **패널로** 간다(안쪽 멀티플렉서가 받는다 — 그것이 토글의 쓰임).
    assert_eq!(state.press(Key::Char('b'), Mods::CTRL), KeyOutcome::ToPane(vec![0x02]));
    assert_eq!(state.mode(), InputMode::Normal);
}

#[test]
fn the_time_rules_are_the_canon_values() {
    // pytmux-610 — 둘 다 정본 소스에서 뽑은 값이다(손으로 적은 상수가 조용히 낡지 않게).
    let fx = session_keys();
    assert_eq!(base::keys::PANE_NUMBERS_FOR.as_secs_f64(), fx.display_secs);
    assert!((base::keys::ESC_DEBOUNCE.as_secs_f64() - fx.esc_debounce_secs).abs() < 1e-9);
}

#[test]
fn ten_repeated_escapes_change_the_mode_once() {
    // 관문(pytmux-610): 쥐고 있는 ESC(반복 이벤트 열 개) → 모드가 한 번만 바뀐다.
    let mut debounce = EscDebounce::default();
    let mut state = ModeState::default();
    let t0 = std::time::Instant::now();
    let mut changes = 0;
    for i in 0..10u64 {
        let now = t0 + std::time::Duration::from_millis(33 * i); // OS 반복 간격 ≈ 33ms
        if debounce.drops(Key::Escape, now) {
            continue;
        }
        let before = state.mode();
        state.press(Key::Escape, Mods::NONE);
        changes += usize::from(state.mode() != before);
    }
    assert_eq!(changes, 1, "반복 ESC 가 모드를 {changes} 번 뒤집었다");
    // 의도적 더블탭(100ms+)은 산다 — esc 진입 후 해제.
    let mut debounce = EscDebounce::default();
    assert!(!debounce.drops(Key::Escape, t0));
    assert!(!debounce.drops(Key::Escape, t0 + std::time::Duration::from_millis(120)));
    assert!(!debounce.drops(Key::Char('q'), t0), "ESC 가 아닌 키를 버렸다");
}

#[test]
fn the_normalization_tables_are_the_canon_tables() {
    let fx = session_keys();
    let jamo: BTreeMap<String, String> = base::keys::JAMO_QWERTY
        .iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect();
    assert_eq!(jamo, fx.jamo, "자모 표가 정본 `_JAMO` 와 다르다");
    let opt: BTreeMap<String, String> = base::keys::MAC_OPT_NUMBER_ROW
        .iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect();
    assert_eq!(opt, fx.mac_opt_number_row, "Option 표가 정본 `MAC_OPT_NUMBER_ROW` 와 다르다");
}

/// 그 모드에 들어가서 키 하나 — `(모드, 결과)`.
fn press_from(mode: InputMode, key: Key, mods: Mods) -> (InputMode, KeyOutcome) {
    let mut state = ModeState::default();
    match mode {
        InputMode::Command => {
            state.press(Key::Escape, Mods::NONE);
        }
        InputMode::Prefix => {
            state.press(Key::Char('b'), Mods::CTRL);
        }
        InputMode::Scroll => state.enter_scroll(),
        other => panic!("{other:?}"),
    }
    let out = state.press(key, mods);
    (state.mode(), out)
}

#[test]
fn hangul_and_option_glyphs_resolve_in_modes_like_canon() {
    // 관문(pytmux-583): 한글 상태에서 `esc` 뒤 `ㅂ` 이 `q` 로 · `⌥1` 이 탭 1 로. 모드마다 정본이
    // 거는 정규화가 다르다(`mode_normalize`) — 안 거는 모드에서는 **모르는 키**와 같아야 한다.
    let fx = session_keys();
    let mut wrong = Vec::new();
    for (label, mode) in [
        ("esc", InputMode::Command),
        ("prefix", InputMode::Prefix),
        ("scroll", InputMode::Scroll),
    ] {
        let norm = &fx.mode_normalize[label];
        for (src, phys, on) in fx
            .jamo
            .iter()
            .map(|(a, b)| (a, b, norm.jamo))
            .chain(fx.mac_opt_number_row.iter().map(|(a, b)| (a, b, norm.mac_opt)))
        {
            let c = src.chars().next().unwrap();
            let p = phys.chars().next().unwrap();
            let got = press_from(mode, Key::Char(c), Mods::NONE);
            let want = if on {
                press_from(mode, Key::Char(p), Mods::NONE)
            } else {
                press_from(mode, UNKNOWN.0, UNKNOWN.1)
            };
            if got != want {
                wrong.push(format!("{label} {src:?}: 정본은 {want:?} 인데 우리는 {got:?}"));
            }
        }
    }
    // 이 GUI 의 맥 백엔드가 ⌥1 을 주는 모양(`1` + Alt) — esc 모드에서 숫자와 같아야 한다.
    for d in '1'..='9' {
        let got = press_from(InputMode::Command, Key::Char(d), Mods::ALT);
        let want = press_from(InputMode::Command, Key::Char(d), Mods::NONE);
        if got != want {
            wrong.push(format!("esc Alt+{d}: {want:?} 이어야 하는데 {got:?}"));
        }
    }
    assert!(wrong.is_empty(), "모드 안 정규화가 정본과 다르다:\n  {}", wrong.join("\n  "));
}
