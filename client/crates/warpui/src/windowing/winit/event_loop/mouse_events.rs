//! winit 마우스 버튼 → 우리 [`Event`](crate::event::Event) — pytmux 추가(PROVENANCE §1 · pytmux-631).
//!
//! 상류는 이 변환을 `WindowEvent` 의 큰 `match` 안에 적어 두어 창 없이는 못 쟀다. 가운데·오른쪽
//! 버튼의 뗌·끌기를 더하면서 버튼을 가르는 부분만 **순수 함수**로 떼었다 — 입력은 버튼·자리·
//! 수식키뿐이다(`window_state` 를 안 받는다). 호출부(`mod.rs`)는 그 값을 넘길 뿐이다.

use pathfinder_geometry::vector::Vector2F;
use winit::event::MouseButton;

use crate::event::{Event, ModifiersState};

/// 버튼 누름. `mac` 이면 Ctrl+왼쪽은 오른쪽 누름이다(맥 OS 관례 — 상류 그대로).
pub(super) fn pressed(
    button: MouseButton,
    position: Vector2F,
    modifiers: ModifiersState,
    click_count: u32,
    mac: bool,
) -> Option<Event> {
    Some(match button {
        MouseButton::Left if modifiers.ctrl && mac => Event::RightMouseDown {
            position,
            cmd: modifiers.cmd,
            shift: modifiers.shift,
            click_count,
        },
        MouseButton::Left => Event::LeftMouseDown {
            position,
            click_count,
            is_first_mouse: false,
            modifiers,
        },
        MouseButton::Right => Event::RightMouseDown {
            position,
            cmd: modifiers.cmd,
            shift: modifiers.shift,
            click_count,
        },
        MouseButton::Middle => Event::MiddleMouseDown {
            position,
            cmd: modifiers.cmd,
            shift: modifiers.shift,
            ctrl: modifiers.ctrl,
            click_count,
        },
        _ => return None,
    })
}

/// 버튼 뗌. 모르는 버튼(뒤·앞)은 버린다 — 소비자가 없다.
pub(super) fn released(button: MouseButton, position: Vector2F, modifiers: ModifiersState) -> Option<Event> {
    Some(match button {
        MouseButton::Left => Event::LeftMouseUp { position, modifiers },
        MouseButton::Middle => Event::MiddleMouseUp { position, modifiers },
        MouseButton::Right => Event::RightMouseUp { position, modifiers },
        _ => return None,
    })
}

/// 포인터가 움직였다 — 쥔 버튼이 있으면 그 버튼의 끌기, 없으면 그냥 이동.
pub(super) fn moved(pressed: Option<MouseButton>, position: Vector2F, modifiers: ModifiersState) -> Event {
    match pressed {
        Some(MouseButton::Left) => Event::LeftMouseDragged { position, modifiers },
        Some(MouseButton::Middle) => Event::MiddleMouseDragged { position, modifiers },
        Some(MouseButton::Right) => Event::RightMouseDragged { position, modifiers },
        _ => Event::MouseMoved {
            position,
            cmd: modifiers.cmd,
            shift: modifiers.shift,
            is_synthetic: false,
        },
    }
}

#[cfg(test)]
#[path = "mouse_events_tests.rs"]
mod tests;
