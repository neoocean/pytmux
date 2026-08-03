use std::cell::RefCell;
use std::ops::Range;

use pathfinder_geometry::vector::Vector2F;

use super::{
    AfterLayoutContext, AppContext, DispatchEventResult, Element, Event, EventContext,
    LayoutContext, PaintContext, Point, SizeConstraint, ZIndex,
};
use crate::event::{DispatchedEvent, EventDiscriminants, KeyState, ModifiersState};
use crate::keymap::Keystroke;
use crate::platform::keyboard::KeyCode;

type Handler = Box<dyn FnMut(&mut EventContext, &AppContext, Vector2F) -> DispatchEventResult>;
/// 수정키까지 받는 마우스 콜백(왼쪽 버튼 누름/뗌/드래그).
///
/// 터미널 클라는 **Shift+드래그를 패널 안 앱에게 넘기는** 제스처로 쓴다 — 평드래그는 이미
/// 복사라 앱에게 줄 자리가 그것뿐이다. 이벤트(`Event::LeftMouse*`)는 처음부터 수정키를
/// 싣고 있었고, 콜백만 그것을 버렸다.
type MouseHandler = Box<
    dyn FnMut(&mut EventContext, &AppContext, Vector2F, &ModifiersState) -> DispatchEventResult,
>;
type KeyHandler = Box<dyn FnMut(&mut EventContext, &AppContext, &Keystroke) -> DispatchEventResult>;
/// 입력기(IME)가 **확정한 글자**를 받는 콜백.
///
/// 한글·CJK 는 자판 한 번이 글자 하나가 아니다 — 조합이 끝나야 글자가 나오고, 그 결과는
/// 키가 아니라 문자열로 온다(`Event::TypedCharacters`). 키 콜백만 있으면 조합 결과가
/// 통째로 사라진다.
type TypedHandler =
    Box<dyn FnMut(&mut EventContext, &AppContext, &str) -> DispatchEventResult>;
/// 입력기가 **조합 중인** 글자를 받는 콜백(`Event::SetMarkedText`/`ClearMarkedText`).
///
/// [`TypedHandler`] 의 짝이다. 확정만 받으면 사람이 `ㅎ`→`하`→`한` 을 만들어 가는 동안
/// **화면에 아무것도 없다** — 자기가 무엇을 치고 있는지 못 본다. 상류는 그 상태를 이미
/// 이벤트로 주고 있는데(winit `Ime::Preedit` → `SetMarkedText`) 이 크레이트에 받는 자리가
/// 없어 소비자가 닿을 수 없었다(pytmux-15 · `ModifierKeyChanged` 와 같은 모양의 구멍).
///
/// 조합이 끝나거나 취소되면 빈 문자열로 부른다 — 소비자는 "지우기"를 따로 안 다뤄도 된다.
type MarkedTextHandler =
    Box<dyn FnMut(&mut EventContext, &AppContext, &str, Range<usize>) -> DispatchEventResult>;
/// 휠 콜백. **커서 위치를 함께 준다** — 휠은 "무엇 위에서 굴렸나"가 뜻의 일부인
/// 제스처라(터미널 클라는 커서 아래 패널을 굴린다) 델타만으로는 해석할 수 없다.
/// 이벤트(`Event::ScrollWheel`)는 처음부터 위치를 싣고 있었고, 콜백만 그것을 버렸다.
type ScrollHandler = Box<
    dyn FnMut(
        &mut EventContext,
        &AppContext,
        Vector2F,
        &Vector2F,
        &ModifiersState,
    ) -> DispatchEventResult,
>;
type ModifierStateChangedHandler =
    Box<dyn FnMut(&mut EventContext, &AppContext, &KeyCode, &KeyState) -> DispatchEventResult>;

#[derive(Debug, Clone, Copy)]
pub struct MouseInBehavior {
    /// Whether to fire the `mouse_in` event on synthetic events, which are events the UI
    /// framework generates so in order to trigger hover effects when the underlying view has
    /// changed even though the mouse hasn't actually moved. Typically elements should handle
    /// sythetic hovers, but there are some cases where it's the incorrect behavior.
    pub fire_on_synthetic_events: bool,
    /// Whether to fire the `mouse_in` event when the element is covered by another element.
    /// This is true by default, but some elements may want to configure this behavior.
    pub fire_when_covered: bool,
}

impl Default for MouseInBehavior {
    fn default() -> Self {
        Self {
            fire_on_synthetic_events: true,
            fire_when_covered: true,
        }
    }
}

pub struct EventHandler {
    child: Box<dyn Element>,
    /// Allow this element to handle events even if a descendent already handled it.
    always_handle: bool,
    left_mouse_down: Option<RefCell<MouseHandler>>,
    left_mouse_up: Option<RefCell<MouseHandler>>,
    middle_mouse_down: Option<RefCell<Handler>>,
    right_mouse_down: Option<RefCell<Handler>>,
    forward_mouse_down: Option<RefCell<Handler>>,
    back_mouse_down: Option<RefCell<Handler>>,
    mouse_in: Option<RefCell<Handler>>,
    mouse_in_behavior: MouseInBehavior,
    mouse_out: Option<RefCell<Handler>>,
    mouse_dragged: Option<RefCell<MouseHandler>>,
    scroll_wheel: Option<RefCell<ScrollHandler>>,
    keydown: Option<RefCell<KeyHandler>>,
    typed_characters: Option<RefCell<TypedHandler>>,
    marked_text: Option<RefCell<MarkedTextHandler>>,
    modifier_state_changed: Option<RefCell<ModifierStateChangedHandler>>,
    origin: Option<Point>,
    // This is a short-term solution for properly handling events on stacks. A stack will always
    // put its children on higher z-indexes than its origin, so a hit test using the standard
    // `z_index` method would always result in the event being covered (by the children of the
    // stack). Instead, we track the upper-bound of z-indexes _contained by_ the child element.
    // Then we use that upper bound to do the hit testing, which means a parent will always get
    // events from its children, regardless of whether they are stacks or not.
    child_max_z_index: Option<ZIndex>,
}

impl EventHandler {
    pub fn new(child: Box<dyn Element>) -> Self {
        Self {
            child,
            always_handle: false,
            left_mouse_down: None,
            left_mouse_up: None,
            middle_mouse_down: None,
            right_mouse_down: None,
            forward_mouse_down: None,
            back_mouse_down: None,
            mouse_in: None,
            mouse_out: None,
            mouse_dragged: None,
            scroll_wheel: None,
            keydown: None,
            typed_characters: None,
            marked_text: None,
            modifier_state_changed: None,
            origin: None,
            child_max_z_index: None,
            mouse_in_behavior: Default::default(),
        }
    }

    pub fn with_always_handle(mut self) -> Self {
        self.always_handle = true;
        self
    }

    pub fn on_keydown<F>(mut self, callback: F) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, &Keystroke) -> DispatchEventResult,
    {
        self.keydown = Some(RefCell::new(Box::new(callback)));
        self
    }

    /// 입력기가 확정한 글자(한글 등). 키 콜백과 **별개 경로**다.
    pub fn on_typed_characters<F>(mut self, callback: F) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, &str) -> DispatchEventResult,
    {
        self.typed_characters = Some(RefCell::new(Box::new(callback)));
        self
    }

    /// 입력기가 **조합 중인** 글자. 확정 콜백([`on_typed_characters`](Self::on_typed_characters))의
    /// 짝이고, 조합이 끝나거나 취소되면 **빈 문자열**로 한 번 더 불린다.
    pub fn on_marked_text<F>(mut self, callback: F) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, &str, Range<usize>) -> DispatchEventResult,
    {
        self.marked_text = Some(RefCell::new(Box::new(callback)));
        self
    }

    pub fn on_modifier_state_changed<F>(mut self, callback: F) -> Self
    where
        F: 'static
            + FnMut(&mut EventContext, &AppContext, &KeyCode, &KeyState) -> DispatchEventResult,
    {
        self.modifier_state_changed = Some(RefCell::new(Box::new(callback)));
        self
    }

    pub fn on_left_mouse_down<F>(self, mut callback: F) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, Vector2F) -> DispatchEventResult,
    {
        // 수정키를 안 보는 호출부(상류 코드 대부분)를 그대로 둔다 — 시그니처를 바꾸면
        // 재임포트할 때마다 그 자리를 전부 다시 고쳐야 한다.
        self.on_left_mouse_down_with_modifiers(move |ctx, app, position, _| callback(ctx, app, position))
    }

    /// 수정키까지 받는 판. 터미널 클라의 **Shift+드래그 넘김**이 이걸 쓴다.
    pub fn on_left_mouse_down_with_modifiers<F>(mut self, callback: F) -> Self
    where
        F: 'static
            + FnMut(
                &mut EventContext,
                &AppContext,
                Vector2F,
                &ModifiersState,
            ) -> DispatchEventResult,
    {
        self.left_mouse_down = Some(RefCell::new(Box::new(callback)));
        self
    }

    pub fn on_left_mouse_up<F>(self, mut callback: F) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, Vector2F) -> DispatchEventResult,
    {
        // 수정키를 안 보는 호출부(상류 코드 대부분)를 그대로 둔다 — 시그니처를 바꾸면
        // 재임포트할 때마다 그 자리를 전부 다시 고쳐야 한다.
        self.on_left_mouse_up_with_modifiers(move |ctx, app, position, _| callback(ctx, app, position))
    }

    /// 수정키까지 받는 판. 터미널 클라의 **Shift+드래그 넘김**이 이걸 쓴다.
    pub fn on_left_mouse_up_with_modifiers<F>(mut self, callback: F) -> Self
    where
        F: 'static
            + FnMut(
                &mut EventContext,
                &AppContext,
                Vector2F,
                &ModifiersState,
            ) -> DispatchEventResult,
    {
        self.left_mouse_up = Some(RefCell::new(Box::new(callback)));
        self
    }

    pub fn on_right_mouse_down<F>(mut self, callback: F) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, Vector2F) -> DispatchEventResult,
    {
        self.right_mouse_down = Some(RefCell::new(Box::new(callback)));
        self
    }

    pub fn on_middle_mouse_down<F>(mut self, callback: F) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, Vector2F) -> DispatchEventResult,
    {
        self.middle_mouse_down = Some(RefCell::new(Box::new(callback)));
        self
    }

    pub fn on_forward_mouse_down<F>(mut self, callback: F) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, Vector2F) -> DispatchEventResult,
    {
        self.forward_mouse_down = Some(RefCell::new(Box::new(callback)));
        self
    }

    pub fn on_back_mouse_down<F>(mut self, callback: F) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, Vector2F) -> DispatchEventResult,
    {
        self.back_mouse_down = Some(RefCell::new(Box::new(callback)));
        self
    }

    pub fn on_mouse_in<F>(mut self, callback: F, mouse_in_behavior: Option<MouseInBehavior>) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, Vector2F) -> DispatchEventResult,
    {
        self.mouse_in = Some(RefCell::new(Box::new(callback)));
        self.mouse_in_behavior = mouse_in_behavior.unwrap_or_default();
        self
    }

    pub fn on_mouse_out<F>(mut self, callback: F) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, Vector2F) -> DispatchEventResult,
    {
        self.mouse_out = Some(RefCell::new(Box::new(callback)));
        self
    }

    pub fn on_mouse_dragged<F>(self, mut callback: F) -> Self
    where
        F: 'static + FnMut(&mut EventContext, &AppContext, Vector2F) -> DispatchEventResult,
    {
        // 수정키를 안 보는 호출부(상류 코드 대부분)를 그대로 둔다 — 시그니처를 바꾸면
        // 재임포트할 때마다 그 자리를 전부 다시 고쳐야 한다.
        self.on_mouse_dragged_with_modifiers(move |ctx, app, position, _| callback(ctx, app, position))
    }

    /// 수정키까지 받는 판. 터미널 클라의 **Shift+드래그 넘김**이 이걸 쓴다.
    pub fn on_mouse_dragged_with_modifiers<F>(mut self, callback: F) -> Self
    where
        F: 'static
            + FnMut(
                &mut EventContext,
                &AppContext,
                Vector2F,
                &ModifiersState,
            ) -> DispatchEventResult,
    {
        self.mouse_dragged = Some(RefCell::new(Box::new(callback)));
        self
    }

    pub fn on_scroll_wheel<F>(mut self, callback: F) -> Self
    where
        F: 'static
            + FnMut(
                &mut EventContext,
                &AppContext,
                Vector2F,
                &Vector2F,
                &ModifiersState,
            ) -> DispatchEventResult,
    {
        self.scroll_wheel = Some(RefCell::new(Box::new(callback)));
        self
    }

    /// 수정키를 함께 넘기는 판.
    fn dispatch_mouse_callback(
        &self,
        callback: Option<&RefCell<MouseHandler>>,
        ctx: &mut EventContext,
        position: Vector2F,
        modifiers: &ModifiersState,
        app: &AppContext,
    ) -> bool {
        if let Some(callback) = callback.as_ref()
            && let Some(rect) = ctx.visible_rect(self.origin.unwrap(), self.size().unwrap())
            && rect.contains_point(position)
        {
            return match callback.borrow_mut()(ctx, app, position, modifiers) {
                DispatchEventResult::PropagateToParent => false,
                DispatchEventResult::StopPropagation => true,
            };
        }
        false
    }

    fn dispatch_callback(
        &self,
        callback: Option<&RefCell<Handler>>,
        ctx: &mut EventContext,
        position: Vector2F,
        app: &AppContext,
    ) -> bool {
        if let Some(callback) = callback.as_ref()
            && let Some(rect) = ctx.visible_rect(self.origin.unwrap(), self.size().unwrap())
            && rect.contains_point(position)
        {
            return match callback.borrow_mut()(ctx, app, position) {
                DispatchEventResult::PropagateToParent => false,
                DispatchEventResult::StopPropagation => true,
            };
        }
        false
    }
}

impl Element for EventHandler {
    fn layout(
        &mut self,
        constraint: SizeConstraint,
        ctx: &mut LayoutContext,
        app: &AppContext,
    ) -> Vector2F {
        self.child.layout(constraint, ctx, app)
    }

    fn after_layout(&mut self, ctx: &mut AfterLayoutContext, app: &AppContext) {
        self.child.after_layout(ctx, app);
    }

    fn paint(&mut self, origin: Vector2F, ctx: &mut PaintContext, app: &AppContext) {
        self.origin = Some(Point::from_vec2f(origin, ctx.scene.z_index()));
        self.child.paint(origin, ctx, app);
        self.child_max_z_index = Some(ctx.scene.max_active_z_index());
    }

    fn size(&self) -> Option<Vector2F> {
        self.child.size()
    }

    fn dispatch_event(
        &mut self,
        event: &DispatchedEvent,
        ctx: &mut EventContext,
        app: &AppContext,
    ) -> bool {
        let handled = self.child.dispatch_event(event, ctx, app);
        if handled && !self.always_handle {
            return true;
        }

        let Some(z_index) = self.child_max_z_index else {
            log::error!(
                "Dispatching event on EventHandler element which was never painted: event={:?}",
                EventDiscriminants::from(event.raw_event())
            );
            return false;
        };
        match event.at_z_index(z_index, ctx) {
            Some(Event::MouseMoved {
                position,
                is_synthetic,
                ..
            }) => {
                let MouseInBehavior {
                    fire_on_synthetic_events,
                    fire_when_covered,
                } = self.mouse_in_behavior;
                let is_covered = ctx.is_covered(Point::from_vec2f(
                    *position,
                    self.child_max_z_index.expect("child max z index not set"),
                ));
                let should_fire = (!is_synthetic || fire_on_synthetic_events)
                    && (fire_when_covered || !is_covered);
                if should_fire
                    && self.dispatch_callback(self.mouse_in.as_ref(), ctx, *position, app)
                {
                    return true;
                }
                if self.dispatch_callback(self.mouse_out.as_ref(), ctx, *position, app) {
                    return true;
                }
            }
            Some(Event::LeftMouseDragged { position, modifiers }) => {
                if self.dispatch_mouse_callback(
                    self.mouse_dragged.as_ref(),
                    ctx,
                    *position,
                    modifiers,
                    app,
                ) {
                    return true;
                }
                if self.dispatch_callback(self.mouse_in.as_ref(), ctx, *position, app) {
                    return true;
                }
                if self.dispatch_callback(self.mouse_out.as_ref(), ctx, *position, app) {
                    return true;
                }
            }
            Some(Event::LeftMouseDown {
                position, modifiers, ..
            }) => {
                if self.dispatch_mouse_callback(
                    self.left_mouse_down.as_ref(),
                    ctx,
                    *position,
                    modifiers,
                    app,
                ) {
                    return true;
                }
            }
            Some(Event::LeftMouseUp { position, modifiers }) => {
                if self.dispatch_mouse_callback(
                    self.left_mouse_up.as_ref(),
                    ctx,
                    *position,
                    modifiers,
                    app,
                ) {
                    return true;
                }
            }
            Some(Event::MiddleMouseDown { position, .. }) => {
                if self.dispatch_callback(self.middle_mouse_down.as_ref(), ctx, *position, app) {
                    return true;
                }
            }
            Some(Event::RightMouseDown { position, .. }) => {
                if self.dispatch_callback(self.right_mouse_down.as_ref(), ctx, *position, app) {
                    return true;
                }
            }
            Some(Event::BackMouseDown { position, .. }) => {
                if self.dispatch_callback(self.back_mouse_down.as_ref(), ctx, *position, app) {
                    return true;
                }
            }
            Some(Event::ForwardMouseDown { position, .. }) => {
                if self.dispatch_callback(self.forward_mouse_down.as_ref(), ctx, *position, app) {
                    return true;
                }
            }
            Some(Event::KeyDown { keystroke, .. }) => {
                if let Some(callback) = self.keydown.as_ref() {
                    return match callback.borrow_mut()(ctx, app, keystroke) {
                        DispatchEventResult::PropagateToParent => false,
                        DispatchEventResult::StopPropagation => true,
                    };
                }
            }
            Some(Event::TypedCharacters { chars }) => {
                if let Some(callback) = self.typed_characters.as_ref() {
                    return match callback.borrow_mut()(ctx, app, chars) {
                        DispatchEventResult::PropagateToParent => false,
                        DispatchEventResult::StopPropagation => true,
                    };
                }
            }
            Some(Event::SetMarkedText { marked_text, selected_range }) => {
                if let Some(callback) = self.marked_text.as_ref() {
                    return match callback.borrow_mut()(ctx, app, marked_text, selected_range.clone()) {
                        DispatchEventResult::PropagateToParent => false,
                        DispatchEventResult::StopPropagation => true,
                    };
                }
            }
            // 조합이 끝났다(확정 또는 취소). 소비자가 "지우기"를 따로 안 다뤄도 되게
            // **빈 문자열**로 같은 콜백을 부른다 — 두 경로로 나누면 한쪽만 배선해 조합
            // 잔상이 화면에 남는 날이 온다.
            Some(Event::ClearMarkedText) => {
                if let Some(callback) = self.marked_text.as_ref() {
                    return match callback.borrow_mut()(ctx, app, "", 0..0) {
                        DispatchEventResult::PropagateToParent => false,
                        DispatchEventResult::StopPropagation => true,
                    };
                }
            }
            Some(Event::ModifierKeyChanged { key_code, state }) => {
                if let Some(callback) = self.modifier_state_changed.as_ref() {
                    return match callback.borrow_mut()(ctx, app, key_code, state) {
                        DispatchEventResult::PropagateToParent => false,
                        DispatchEventResult::StopPropagation => true,
                    };
                }
            }
            Some(Event::ScrollWheel {
                position,
                delta,
                precise: _,
                modifiers,
            }) => {
                if let Some(callback) = self.scroll_wheel.as_ref()
                    && let Some(rect) = ctx.visible_rect(self.origin.unwrap(), self.size().unwrap())
                    && rect.contains_point(*position)
                {
                    return match callback.borrow_mut()(ctx, app, *position, delta, modifiers) {
                        DispatchEventResult::PropagateToParent => false,
                        DispatchEventResult::StopPropagation => true,
                    };
                }
            }
            _ => {}
        }
        handled
    }

    fn origin(&self) -> Option<Point> {
        self.child.origin()
    }
}

#[cfg(test)]
#[path = "event_handler_tests.rs"]
mod tests;
