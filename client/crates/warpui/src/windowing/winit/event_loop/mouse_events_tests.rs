//! winit 버튼 변환의 단위 자(pytmux-631) — 가운데·오른쪽의 누름·뗌·끌기와 Ctrl 칸.

use pathfinder_geometry::vector::vec2f;
use winit::event::MouseButton;

use super::{moved, pressed, released};
use crate::event::{Event, ModifiersState};

fn ctrl() -> ModifiersState {
    ModifiersState { ctrl: true, ..ModifiersState::default() }
}

#[test]
fn middle_and_right_buttons_have_up_and_drag() {
    let at = vec2f(3., 4.);
    let none = ModifiersState::default();
    assert!(matches!(released(MouseButton::Middle, at, none), Some(Event::MiddleMouseUp { .. })));
    assert!(matches!(released(MouseButton::Right, at, none), Some(Event::RightMouseUp { .. })));
    assert!(matches!(released(MouseButton::Left, at, none), Some(Event::LeftMouseUp { .. })));
    assert!(released(MouseButton::Back, at, none).is_none());
    assert!(matches!(moved(Some(MouseButton::Middle), at, none), Event::MiddleMouseDragged { .. }));
    assert!(matches!(moved(Some(MouseButton::Right), at, none), Event::RightMouseDragged { .. }));
    assert!(matches!(moved(Some(MouseButton::Left), at, none), Event::LeftMouseDragged { .. }));
    assert!(matches!(moved(None, at, none), Event::MouseMoved { .. }));
}

#[test]
fn middle_press_carries_ctrl_and_mac_ctrl_left_is_a_right_press() {
    let at = vec2f(0., 0.);
    assert!(matches!(
        pressed(MouseButton::Middle, at, ctrl(), 1, false),
        Some(Event::MiddleMouseDown { ctrl: true, .. })
    ));
    assert!(matches!(
        pressed(MouseButton::Middle, at, ModifiersState::default(), 1, false),
        Some(Event::MiddleMouseDown { ctrl: false, .. })
    ));
    assert!(matches!(pressed(MouseButton::Left, at, ctrl(), 1, true), Some(Event::RightMouseDown { .. })));
    assert!(matches!(pressed(MouseButton::Left, at, ctrl(), 1, false), Some(Event::LeftMouseDown { .. })));
    assert!(matches!(pressed(MouseButton::Right, at, ctrl(), 1, false), Some(Event::RightMouseDown { .. })));
}
