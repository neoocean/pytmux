//! 맥 버튼 뗌·끌기 변환의 단위 자(pytmux-631).

use pathfinder_geometry::vector::vec2f;
use warpui_core::Event;
use warpui_core::event::ModifiersState;

use super::{ButtonPhase, button_up_or_drag};

#[test]
fn right_and_middle_buttons_have_up_and_drag_and_others_are_dropped() {
    let at = vec2f(1., 2.);
    let ctrl = ModifiersState { ctrl: true, ..ModifiersState::default() };
    assert!(matches!(button_up_or_drag(ButtonPhase::Up, 1, at, ctrl), Some(Event::RightMouseUp { modifiers, .. }) if modifiers.ctrl));
    assert!(matches!(button_up_or_drag(ButtonPhase::Dragged, 1, at, ctrl), Some(Event::RightMouseDragged { .. })));
    assert!(matches!(button_up_or_drag(ButtonPhase::Up, 2, at, ctrl), Some(Event::MiddleMouseUp { .. })));
    assert!(matches!(button_up_or_drag(ButtonPhase::Dragged, 2, at, ctrl), Some(Event::MiddleMouseDragged { .. })));
    for other in [0, 3, 4] {
        assert!(button_up_or_drag(ButtonPhase::Up, other, at, ctrl).is_none(), "버튼 {other}");
    }
}
