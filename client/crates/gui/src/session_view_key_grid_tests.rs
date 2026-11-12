//! 수식 키 격자 — **창 이벤트에서 패널 바이트까지**(pytmux-608).
//!
//! `proto/tests/key_bytes_conformance.rs` 는 `base::keys::encode` 가 정본 격자(`key_grid.json`)와
//! 같은지 잰다. 이 자는 그 앞 — 상류 `Keystroke` 가 `keydown_action` 을 지나 **원시 키로 가는지,
//! 아니면 GUI 가 가로채는지** — 를 잰다. 가로채는 칸은 [`INTERCEPTS`] 에 이름과 사유로 선언한다.
//!
//! # INTERCEPTS — 선언된 가로채기만 허용한다
//!
//! 전부 허용 갈림 ⓒ(OS 창 통합)다: 정본은 호스트 단말 안에 살아 그 조합을 **단말이** 처리한다
//! (붙여넣기 · 글자 크기 · 탭 · 전체 화면). GUI 에는 바깥 단말이 없어 우리가 받는다.
//! 갈림 대장(`proto/tests/common/divergence.rs`)에 키 입력 축이 서면 이 표를 그리로 옮긴다
//! (`DIVERGE(pytmux-598)`). 걸린 줄이 더는 안 가로채지면 그것도 실패다(낡은 예외).

use super::*;
use serde_json::Value;

/// (칸 이름, 왜).
const INTERCEPTS: &[(&str, &str)] = &[
    ("ctrl+shift+v", "ⓒ 붙여넣기 — 터미널 관례 별칭(`paste_entry` · 정본은 바깥 단말이 붙여넣는다)"),
    ("ctrl+=", "ⓒ 글자 크기 키우기(`font_scale_chord` · 정본은 호스트 단말이 한다)"),
    ("ctrl+-", "ⓒ 글자 크기 줄이기(`font_scale_chord`)"),
    ("ctrl+0", "ⓒ 글자 크기 되돌리기(`font_scale_chord`)"),
    ("ctrl+tab", "ⓒ 다음 탭(`tab_switch_chord` · 독립 앱이라 받을 수 있다)"),
    ("ctrl+shift+tab", "ⓒ 이전 탭(`tab_switch_chord`)"),
    ("alt+enter", "ⓒ 전체 화면(`is_fullscreen_chord`)"),
];

fn grid() -> Vec<Value> {
    let v: Value = serde_json::from_str(include_str!("../../proto/tests/fixtures/key_grid.json"))
        .expect("key_grid.json 을 못 읽는다 — python3 scripts/gen_key_grid.py");
    v["rows"].as_array().expect("rows").clone()
}

/// 칸 → 상류가 줄 `Keystroke`(Shift 는 글자에 접혀 대문자로 온다) + 그 키가 낸 글자.
/// Ctrl·Alt 가 섞인 칸은 배열이 글자를 안 낸다고 본다(AltGr 갈래는 아래 따로 잰다).
fn keystroke(row: &Value) -> (warpui::keymap::Keystroke, String) {
    let key = row["key"].as_str().unwrap();
    let shift = row["shift"].as_bool().unwrap();
    let ctrl = row["ctrl"].as_bool().unwrap();
    let alt = row["alt"].as_bool().unwrap();
    let name = if shift && key.len() == 1 && key.chars().all(|c| c.is_ascii_alphabetic()) {
        key.to_ascii_uppercase()
    } else {
        key.to_owned()
    };
    let chars = if ctrl || alt || name.chars().count() != 1 { String::new() } else { name.clone() };
    (warpui::keymap::Keystroke { ctrl, alt, shift, cmd: false, meta: false, key: name }, chars)
}

#[test]
fn every_grid_chord_reaches_the_pane_with_the_canonical_bytes_or_is_a_declared_intercept() {
    let rows = grid();
    let mut wrong = Vec::new();
    let mut stale: Vec<&str> = INTERCEPTS.iter().map(|i| i.0).collect();
    for row in &rows {
        if row["chrome"].as_bool() == Some(true) {
            continue; // 정본이 패널로 보내기 전에 먹는 키 — 모드 전이 자들이 잰다
        }
        let chord = row["chord"].as_str().unwrap();
        let (ks, chars) = keystroke(row);
        let action = SessionView::keydown_action(&ks, &chars);
        let declared = INTERCEPTS.iter().any(|i| i.0 == chord);
        match action {
            Some(ViewAction::RawKey(key, mods)) => {
                if declared {
                    continue; // 아래 stale 판정 — 안 가로챘다
                }
                let want: Vec<u8> = serde_json::from_value(row["bytes"].clone()).unwrap();
                let got = base::keys::encode(key, mods).unwrap_or_default();
                // 정본이 못 받는 칸(수식 F3 · ⓐ)은 proto 자의 CANON_CANNOT 이 쥔다.
                let f3 = row["key"] == "f3";
                if got != want && !f3 {
                    wrong.push(format!("{chord}: 정본 {want:?} · 우리 {got:?}"));
                }
            }
            _ if declared => stale.retain(|s| *s != chord),
            other => wrong.push(format!("{chord}: 선언 안 된 가로채기 {other:?}")),
        }
    }
    assert!(wrong.is_empty(), "격자 칸이 정본과 갈린다:\n  {}", wrong.join("\n  "));
    assert!(stale.is_empty(), "INTERCEPTS 줄이 더는 안 가로채진다 — 지울 것: {stale:?}");
}

/// Cmd(맥)·Super 는 Alt 로 친다 — 정본은 호스트 단말에서 Cmd 를 받을 수 없다(ⓒ · DIVERGE(pytmux-598)).
/// 지금 동작을 못 박는다(사람 결정 2026-11-13): Cmd+← 는 Alt+← 와 같은 바이트다.
#[test]
fn cmd_is_sent_as_alt_a_declared_divergence() {
    let cmd_left = warpui::keymap::Keystroke {
        ctrl: false,
        alt: false,
        shift: false,
        cmd: true,
        meta: false,
        key: "left".to_owned(),
    };
    let Some(ViewAction::RawKey(key, mods)) = SessionView::keydown_action(&cmd_left, "") else {
        panic!("Cmd+← 가 원시 키로 안 갔다");
    };
    assert_eq!(base::keys::encode(key, mods).as_deref(), Some(&b"\x1b[1;3D"[..]));
}

/// AltGr(Windows 의 Ctrl+Alt)가 **글자를 낸 칸**은 그 글자다(ⓒ · pytmux-34 #4) — 격자 칸
/// `alt+ctrl+…`(글자 없음)은 ESC+제어 코드로 가므로 두 갈래를 여기서 가른다.
#[test]
fn altgr_with_a_printed_char_sends_the_char_not_the_chord() {
    let ks = warpui::keymap::Keystroke {
        ctrl: true,
        alt: true,
        shift: false,
        cmd: false,
        meta: false,
        key: "q".to_owned(),
    };
    let Some(ViewAction::RawKey(key, mods)) = SessionView::keydown_action(&ks, "@") else {
        panic!("AltGr 글자가 원시 키로 안 갔다");
    };
    assert_eq!(base::keys::encode(key, mods).as_deref(), Some(&b"@"[..]));
    let Some(ViewAction::RawKey(key, mods)) = SessionView::keydown_action(&ks, "") else {
        panic!("Ctrl+Alt+Q 가 원시 키로 안 갔다");
    };
    assert_eq!(base::keys::encode(key, mods).as_deref(), Some(&b"\x1b\x11"[..]));
}
