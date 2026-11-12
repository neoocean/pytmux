//! 패널로 나가는 **키 바이트**가 정본과 같은가(§10-21ⓩ2).
//!
//! # 왜 이 축이 없었나
//!
//! 패리티 래칫은 **표면**(명령·설정·화면)을 센다. 그런데 두 클라가 갈릴 수 있는 축이
//! 하나 더 있다 — **패널 안 프로그램에게 보내는 바이트**다. 그 축을 아무도 안 재는 동안
//! 수정자 붙은 커서 키가 통째로 갈려 있었고(우리는 수정자를 버리고 있었다), 제보
//! *"`Ctrl`+`End` 로 맨 아래로 못 간다"* 로 드러났다.
//!
//! 픽스처는 정본에서 뽑는다: `python3 scripts/gen_key_bytes.py`
//! (출처 = `pytmuxlib/clientutil.py` 의 `SPECIAL`).
//!
//! # 이름을 우리 표현으로 옮기는 다리
//!
//! 정본은 `"shift+home"` 처럼 **이름 하나**로 적고, 우리는 `(Key, Mods)` 둘로 쓴다.
//! 그 다리가 아래 `bridge` 다 — 다리 자체가 틀리면 이 오라클이 공허해지므로,
//! **정본의 모든 이름이 다리에 있는지**를 따로 단언한다.

use std::collections::BTreeMap;

use base::keys::{Key, Mods, encode};

fn fixture() -> BTreeMap<String, Vec<u8>> {
    let raw = include_str!("fixtures/key_bytes.json");
    serde_json::from_str(raw).expect("key_bytes.json 을 못 읽는다")
}

/// 정본 이름 → 우리 `(Key, Mods)`. 모르는 이름이면 `None`.
fn bridge(name: &str) -> Option<(Key, Mods)> {
    const CTRL: Mods = Mods { ctrl: true, alt: false };
    const NONE: Mods = Mods::NONE;
    Some(match name {
        "space" => (Key::Char(' '), NONE),
        "enter" => (Key::Enter, NONE),
        "tab" => (Key::Tab, NONE),
        "backspace" => (Key::Backspace, NONE),
        "delete" => (Key::Delete, NONE),
        "escape" => (Key::Escape, NONE),
        "insert" => (Key::Insert, NONE),
        "up" => (Key::Up, NONE),
        "down" => (Key::Down, NONE),
        "left" => (Key::Left, NONE),
        "right" => (Key::Right, NONE),
        "home" => (Key::Home, NONE),
        "end" => (Key::End, NONE),
        "pageup" => (Key::PageUp, NONE),
        "pagedown" => (Key::PageDown, NONE),
        "shift+tab" => (Key::BackTab, NONE),
        "shift+enter" => (Key::ShiftEnter, NONE),
        "shift+escape" => (Key::ShiftEscape, NONE),
        "shift+up" => (Key::ShiftUp, NONE),
        "shift+down" => (Key::ShiftDown, NONE),
        "shift+left" => (Key::ShiftLeft, NONE),
        "shift+right" => (Key::ShiftRight, NONE),
        "shift+home" => (Key::ShiftHome, NONE),
        "shift+end" => (Key::ShiftEnd, NONE),
        // ★ Ctrl 은 우리 쪽에서 **수정자**다(정본은 이름에 접어 넣는다).
        "ctrl+home" => (Key::Home, CTRL),
        "ctrl+end" => (Key::End, CTRL),
        _ => {
            let rest = name.strip_prefix('f')?;
            let n: u8 = rest.parse().ok()?;
            (Key::Function(n), NONE)
        }
    })
}

#[test]
fn every_canonical_key_sends_the_same_bytes() {
    let fx = fixture();
    assert!(fx.len() >= 30, "픽스처가 너무 작다 — 생성기가 헛돌았다({})", fx.len());
    let mut wrong = Vec::new();
    for (name, want) in &fx {
        let Some((key, mods)) = bridge(name) else {
            panic!(
                "정본 이름 '{name}' 이 다리에 없다 — 새 키가 생겼으면 다리에 더할 것\n\
                 (다리를 안 늘리면 그 키는 이 오라클을 **한 번도 안 받는다**)"
            );
        };
        let got = encode(key, mods).unwrap_or_default();
        if &got != want {
            wrong.push(format!("{name}: 정본 {want:?} · 우리 {got:?}"));
        }
    }
    assert!(
        wrong.is_empty(),
        "패널로 나가는 바이트가 정본과 다르다:\n  {}\n\
         두 클라가 같은 키에 다른 바이트를 보내면 패널 안 앱이 다르게 움직인다.",
        wrong.join("\n  ")
    );
}

#[test]
fn the_modifier_cursor_keys_are_actually_in_the_fixture() {
    // ★ 공허 방지 — 이 여덟이 곧 제보의 자리다. 픽스처에서 빠지면 위 단언이 그 부류를
    //   **한 번도 안 재고** 통과한다.
    let fx = fixture();
    for name in [
        "shift+home", "shift+end", "shift+left", "shift+right",
        "shift+up", "shift+down", "ctrl+home", "ctrl+end",
    ] {
        assert!(fx.contains_key(name), "{name} 이 정본 표에 없다");
    }
}

#[test]
fn a_modified_cursor_key_is_not_the_bare_one() {
    // 종전 우리 코드가 그랬다 — 수정자를 버리고 **맨 키와 같은 바이트**를 보냈다.
    // 그러면 패널 안 앱은 `Ctrl`+`End` 를 그냥 `End` 로 받는다(제보 그대로).
    let bare_end = encode(Key::End, Mods::NONE).unwrap();
    let ctrl_end = encode(Key::End, Mods { ctrl: true, alt: false }).unwrap();
    assert_ne!(bare_end, ctrl_end, "Ctrl+End 가 End 와 같은 바이트다");
    let bare_home = encode(Key::Home, Mods::NONE).unwrap();
    let shift_home = encode(Key::ShiftHome, Mods::NONE).unwrap();
    assert_ne!(bare_home, shift_home, "Shift+Home 이 Home 과 같은 바이트다");
}

// ── 수식 키 격자(pytmux-608) ─────────────────────────────────────────────────────────
//
// `scripts/gen_key_grid.py` 가 수식 × 키 격자의 칸마다 «호스트 단말 바이트 → Textual XTermParser →
// 정본 `key_to_bytes`» 로 정본이 패널에 보내는 바이트를 뽑았다. 같은 조합의 `encode` 가 그것과 같아야 한다.

/// 정본이 그 조합을 **받을 수 없어** GUI 만 xterm 바이트를 보내는 칸(허용 갈림 ⓐ — 단말이 못 준다).
/// (칸 이름 술어, 왜). 걸린 칸이 더는 안 갈리면 그것도 실패다(낡은 예외).
const CANON_CANNOT: &[(&str, &str)] = &[(
    "f3+mods",
    "Textual 이 `CSI 1;N R`(수식 F3)를 **커서 위치 보고(CPR)**로 읽어 삼킨다 — 정본은 수식 F3 를 못 받는다. \
     GUI 는 정본이 받을 수 있었다면 보냈을 xterm 바이트를 보낸다",
)];

fn grid() -> Vec<serde_json::Value> {
    let v: serde_json::Value = serde_json::from_str(include_str!("fixtures/key_grid.json"))
        .expect("key_grid.json 을 못 읽는다 — python3 scripts/gen_key_grid.py");
    v["rows"].as_array().expect("rows").clone()
}

fn canon_cannot(row: &serde_json::Value) -> Option<&'static str> {
    let modded = ["shift", "ctrl", "alt"].iter().any(|m| row[*m].as_bool() == Some(true));
    (row["key"] == "f3" && modded).then_some(CANON_CANNOT[0].0)
}

/// 칸 → GUI 가 그 키에서 받는 (Key, Mods) — `gui::key_from_keystroke` 와 같은 길(`from_name`).
fn grid_key(row: &serde_json::Value) -> (Key, Mods) {
    let key = row["key"].as_str().unwrap();
    let shift = row["shift"].as_bool().unwrap();
    // 상류는 Shift 를 글자에 접어 준다(대문자) — 글자 아닌 키만 shift 칸을 본다.
    let name = if shift && key.chars().all(|c| c.is_ascii_alphabetic()) && key.len() == 1 {
        key.to_ascii_uppercase()
    } else {
        key.to_owned()
    };
    let k = base::keys::from_name(&name, shift, name.chars().next()).expect("이름을 모른다");
    (k, Mods { ctrl: row["ctrl"].as_bool().unwrap(), alt: row["alt"].as_bool().unwrap() })
}

#[test]
fn every_grid_chord_sends_the_canonical_bytes() {
    let rows = grid();
    let mut wrong = Vec::new();
    let mut stale: Vec<&str> = CANON_CANNOT.iter().map(|c| c.0).collect();
    for row in &rows {
        if row["chrome"].as_bool() == Some(true) {
            continue; // 정본이 패널로 보내기 전에 먹는 키 — 바이트를 안 잰다
        }
        let chord = row["chord"].as_str().unwrap();
        let want: Vec<u8> = serde_json::from_value(row["bytes"].clone()).unwrap();
        let (key, mods) = grid_key(row);
        let got = encode(key, mods).unwrap_or_default();
        match (got == want, canon_cannot(row)) {
            (true, _) => {}
            (false, Some(tag)) => stale.retain(|s| *s != tag),
            (false, None) => wrong.push(format!("{chord}: 정본 {want:?} · 우리 {got:?}")),
        }
    }
    assert!(
        wrong.is_empty(),
        "수식 키가 정본과 다른 바이트를 보낸다({}칸 중 {}):\n  {}",
        rows.len(),
        wrong.len(),
        wrong.join("\n  ")
    );
    assert!(stale.is_empty(), "CANON_CANNOT 줄이 더는 안 갈린다 — 지울 것: {stale:?}");
}

/// 공허 방지 — 격자가 수식 부류마다 실제로 무언가를 보내는 칸을 갖는다.
#[test]
fn the_key_grid_covers_every_modifier_class() {
    let rows = grid();
    assert!(rows.len() >= 200, "격자가 {}칸뿐이다", rows.len());
    for chord in [
        "ctrl+up", "alt+up", "ctrl+shift+up", "alt+ctrl+left", "shift+f5", "ctrl+f12", "alt+f1",
        "shift+pageup", "ctrl+delete", "shift+insert", "alt+backspace", "ctrl+backspace", "alt+enter",
        "ctrl+enter", "alt+a", "alt+shift+a", "alt+한", "ctrl+/", "alt+.", "alt+ctrl+a", "alt+b",
    ] {
        let row = rows.iter().find(|r| r["chord"] == chord).unwrap_or_else(|| panic!("{chord} 칸이 없다"));
        assert!(
            !row["bytes"].as_array().unwrap().is_empty(),
            "{chord}: 정본이 아무것도 안 보낸다 — 공식이 빠졌다"
        );
    }
    let lost = rows.iter().filter(|r| r["legacy_lost"].as_bool() == Some(true)).count();
    assert!(lost >= 20, "레거시 단말이 못 주는 칸(ⓐ)이 {lost}개뿐이다 — csiu 길이 빠졌다");
}

/// 제보의 칸 — Ctrl+/ 는 두 쪽 다 0x1f(종전 GUI 는 버렸다).
#[test]
fn ctrl_slash_sends_0x1f_on_both_sides() {
    let rows = grid();
    let row = rows.iter().find(|r| r["chord"] == "ctrl+/").unwrap();
    assert_eq!(row["bytes"], serde_json::json!([0x1f]));
    assert_eq!(encode(Key::Char('/'), Mods::CTRL), Some(vec![0x1f]));
}
