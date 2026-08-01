//! 인코딩 규칙 — **왜 그런가**를 고정한다.
//!
//! 바이트가 파이썬과 같은지는 `tests/mouse_conformance.rs` 가 본다. 여기서 보는 것은
//! 호출부가 기대는 성질들이다(범위 판정 · 추적 레벨의 뜻).

use super::*;

const RECT: (u16, u16, u16, u16) = (5, 3, 20, 10);

fn sgr() -> MouseMode {
    MouseMode {
        track: 2,
        sgr: true,
    }
}

#[test]
fn coordinates_are_pane_relative_and_one_based() {
    // 터미널 마우스 리포트는 1-based 다. 캔버스 좌표를 그대로 보내면 앱은 화면 저 멀리를
    // 눌렀다고 읽는다 — 분할 화면에서 오른쪽 패널일수록 크게 어긋난다.
    let bytes = encode(sgr(), RECT, 5, 3, MouseKind::Press, 1).unwrap();
    assert_eq!(String::from_utf8(bytes).unwrap(), "\u{1b}[<0;1;1M");
}

#[test]
fn a_point_outside_the_pane_produces_nothing() {
    // 클램프해서 억지로 넣으면 앱은 **누른 적 없는 자리**를 받는다. 보낼 것이 없으면
    // 없다고 말해야 호출부가 빈 프레임을 안 만든다.
    for (x, y) in [(4, 6), (8, 2), (25, 6), (8, 13)] {
        assert_eq!(
            encode(sgr(), RECT, x, y, MouseKind::Press, 1),
            None,
            "({x},{y}) 가 패널 안으로 읽혔다"
        );
    }
    // 경계는 안쪽이다(폭 20 → 마지막 열 24, 높이 10 → 마지막 행 12).
    assert!(encode(sgr(), RECT, 24, 12, MouseKind::Press, 1).is_some());
}

#[test]
fn the_legacy_encoding_loses_which_button_was_released() {
    // X10 에는 뗌에 버튼을 실을 자리가 없어 **항상 3**이다. SGR 은 `m` 으로 구분하므로
    // 버튼이 그대로 남는다. 이 차이를 모르고 X10 에 버튼을 실으면 앱은 뗌을 다른 버튼의
    // 누름으로 읽는다.
    let x10 = MouseMode {
        track: 2,
        sgr: false,
    };
    let left = encode(x10, RECT, 8, 6, MouseKind::Release, 1).unwrap();
    let right = encode(x10, RECT, 8, 6, MouseKind::Release, 3).unwrap();
    assert_eq!(left, right, "X10 뗌이 버튼에 따라 달라졌다");

    let left = encode(sgr(), RECT, 8, 6, MouseKind::Release, 1).unwrap();
    let right = encode(sgr(), RECT, 8, 6, MouseKind::Release, 3).unwrap();
    assert_ne!(left, right, "SGR 뗌이 버튼을 잃었다");
}

#[test]
fn tracking_levels_say_what_the_app_asked_for() {
    // 1000 만 켠 앱에 드래그를 보내면 **누른 적 없는 자리에서 눌린 것처럼** 읽힌다.
    let off = MouseMode {
        track: 0,
        sgr: false,
    };
    let click = MouseMode {
        track: 1,
        sgr: false,
    };
    let drag = MouseMode {
        track: 2,
        sgr: false,
    };
    assert!(!off.wants_mouse());
    assert!(click.wants_mouse() && !click.wants_drag());
    assert!(drag.wants_mouse() && drag.wants_drag());
    // 서버가 Windows 에서 3 을 2 로 캡해 보내도 우리는 값을 해석하지 않는다 — 3 이 오면
    // 드래그까지 원하는 것으로만 읽는다(any-motion 은 이 클라가 아직 안 보낸다).
    let any = MouseMode {
        track: 3,
        sgr: false,
    };
    assert!(any.wants_drag());
}
