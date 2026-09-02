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
