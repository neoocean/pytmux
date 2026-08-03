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
