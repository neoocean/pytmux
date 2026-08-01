//! [`TuiEventHandler`]: wraps a child element and runs callbacks for keys the
//! child itself did not handle. (Mouse gestures — clicks and hover — live on
//! [`TuiHoverable`](super::TuiHoverable), mirroring the GUI split between
//! `EventHandler` and `Hoverable`.)
//!
//! # Construction
//! Wrap a child with [`TuiEventHandler::new`] and register handlers with
//! [`on_key`](TuiEventHandler::on_key), matching against the
//! [`Keystroke::key`](crate::keymap::Keystroke) string (e.g. `"enter"`,
//! `"a"`). Layout, render, height, and cursor are transparent — they delegate
//! to the wrapped child.
//!
//! # Dispatch policy
//! On [`dispatch_event`](TuiElement::dispatch_event) the event is offered to the
//! child first. If the child consumes it, dispatch stops. Otherwise, for a
//! `KeyDown` event, the first registered binding whose key matches is invoked
//! (with the event, the [`TuiEventContext`], and the [`AppContext`]) and the
//! event is reported handled. If no binding matched and a catch-all was
//! registered with [`on_any_key`](TuiEventHandler::on_any_key), that runs
//! instead and the event is reported handled. A `Paste` event goes to the
//! callback registered with [`on_paste`](TuiEventHandler::on_paste) — never to
//! the key callbacks, since a paste is a payload rather than a keystroke — and a
//! `ScrollWheel` event goes to [`on_scroll`](TuiEventHandler::on_scroll). Left
//! button press/drag/release go to [`on_mouse`](TuiEventHandler::on_mouse).
//! Otherwise the event is left unhandled so ancestors can react.

use super::{
    TuiConstraint, TuiElement, TuiEvent, TuiEventContext, TuiLayoutContext, TuiPaintContext,
    TuiPaintSurface, TuiPresentationContext, TuiScreenPoint, TuiScreenPosition, TuiSize,
};
use crate::AppContext;
use crate::keymap::Keystroke;

type KeyCallback = Box<dyn for<'a> FnMut(&TuiEvent, &mut TuiEventContext<'a>, &AppContext)>;

struct KeyBinding {
    /// The binding written in [`Keystroke`] grammar, parsed once at
    /// registration.
    ///
    /// Storing the parsed form rather than the raw string is what makes a
    /// binding table portable between the TUI and the GUI: the GUI registers
    /// the same strings through `Keystroke::parse`, so comparing raw strings
    /// here would silently diverge on every modified key (`shift-G` reaches
    /// this element as key `G` with `shift` set).
    keystroke: Keystroke,
    callback: KeyCallback,
}

impl KeyBinding {
    /// Whether this binding is the key that arrived, modifiers included.
    ///
    /// The modifiers must match exactly. Comparing only the key name would let
    /// `ctrl-q` fire a binding registered for `q`, which for a terminal client
    /// means a control sequence meant for the child process is swallowed by the
    /// UI instead.
    fn matches(&self, keystroke: &Keystroke) -> bool {
        self.keystroke.key == keystroke.key
            && self.keystroke.ctrl == keystroke.ctrl
            && self.keystroke.alt == keystroke.alt
            && self.keystroke.shift == keystroke.shift
            && self.keystroke.cmd == keystroke.cmd
    }
}

pub struct TuiEventHandler {
    child: Box<dyn TuiElement>,
    bindings: Vec<KeyBinding>,
    fallback: Option<KeyCallback>,
    paste: Option<KeyCallback>,
    scroll: Option<KeyCallback>,
    mouse: Option<KeyCallback>,
}

impl TuiEventHandler {
    pub fn new(child: Box<dyn TuiElement>) -> Self {
        Self {
            child,
            bindings: Vec::new(),
            fallback: None,
            paste: None,
            scroll: None,
            mouse: None,
        }
    }

    /// Registers a catch-all `callback` for `KeyDown` events that matched no
    /// specific binding.
    ///
    /// A terminal client needs this: it must forward *every* key the user types
    /// to the child process, and the set of those keys cannot be enumerated as
    /// bindings (every printable character, every modifier combination). With
    /// only exact-match bindings, keys like `a` are reported unhandled and the
    /// pane never receives them. Registered separately from `on_key` so that
    /// specific bindings keep priority — the fallback runs only when none matched.
    pub fn on_any_key(
        mut self,
        callback: impl for<'a> FnMut(&TuiEvent, &mut TuiEventContext<'a>, &AppContext) + 'static,
    ) -> Self {
        self.fallback = Some(Box::new(callback));
        self
    }

    /// Registers `callback` to run when a `KeyDown` whose key equals `key`
    /// reaches this element unhandled by the child.
    pub fn on_key(
        mut self,
        key: impl Into<String>,
        callback: impl for<'a> FnMut(&TuiEvent, &mut TuiEventContext<'a>, &AppContext) + 'static,
    ) -> Self {
        let source = key.into();
        // Parse once, here, so a malformed binding is reported where it was
        // written instead of failing to match at some later keypress.
        let keystroke = Keystroke::parse(&source)
            .unwrap_or_else(|e| panic!("invalid key binding {source:?}: {e}"));
        self.bindings.push(KeyBinding {
            keystroke,
            callback: Box::new(callback),
        });
        self
    }

    /// Registers `callback` to run when a [`TuiEvent::Paste`] reaches this
    /// element unhandled by the child.
    ///
    /// A paste is not a key. The terminal delivers the whole payload as one
    /// event (bracketed paste, which the runtime turns on when it enters the
    /// alternate screen), so it can neither be written as a binding nor
    /// reconstructed from the key callbacks — a client that forwards every key
    /// but ignores this event silently drops every paste the user makes.
    ///
    /// Kept separate from [`on_any_key`](Self::on_any_key) on purpose: that
    /// callback is about keys the child process should receive verbatim, and a
    /// paste usually needs different handling (a multi-line payload sent as
    /// keystrokes runs line by line).
    pub fn on_paste(
        mut self,
        callback: impl for<'a> FnMut(&TuiEvent, &mut TuiEventContext<'a>, &AppContext) + 'static,
    ) -> Self {
        self.paste = Some(Box::new(callback));
        self
    }

    /// Registers `callback` to run when a [`TuiEvent::ScrollWheel`] reaches this
    /// element unhandled by the child.
    ///
    /// [`TuiHoverable`](super::TuiHoverable) handles clicks and hover, but a
    /// wheel is not a gesture on one widget: a terminal client scrolls the pane
    /// the user is looking at, and the element under the cursor is whatever text
    /// line happens to be there. Registering the callback on the wrapper keeps
    /// the whole view as the scroll target.
    pub fn on_scroll(
        mut self,
        callback: impl for<'a> FnMut(&TuiEvent, &mut TuiEventContext<'a>, &AppContext) + 'static,
    ) -> Self {
        self.scroll = Some(Box::new(callback));
        self
    }

    /// Registers `callback` for left-button press, drag, and release that reach
    /// this element unhandled by the child.
    ///
    /// [`TuiHoverable`](super::TuiHoverable) covers the click-on-one-widget case.
    /// This is for gestures the whole view interprets by coordinate — dragging a
    /// split boundary, say — where the widget under the cursor is incidental.
    /// Press, drag, and release go to the same callback because a drag is one
    /// gesture: splitting them across registrations invites handling the press
    /// while forgetting the release, which leaves the view stuck mid-drag.
    ///
    /// Pointer motion without a button is not routed here — a terminal in
    /// any-motion tracking mode reports it continuously, and no caller so far
    /// needs it.
    pub fn on_mouse(
        mut self,
        callback: impl for<'a> FnMut(&TuiEvent, &mut TuiEventContext<'a>, &AppContext) + 'static,
    ) -> Self {
        self.mouse = Some(Box::new(callback));
        self
    }
}

impl TuiElement for TuiEventHandler {
    fn layout(
        &mut self,
        constraint: TuiConstraint,
        ctx: &mut TuiLayoutContext,
        app: &AppContext,
    ) -> TuiSize {
        self.child.layout(constraint, ctx, app)
    }

    fn after_layout(&mut self, ctx: &mut TuiLayoutContext, app: &AppContext) {
        self.child.after_layout(ctx, app);
    }

    fn render(
        &mut self,
        origin: TuiScreenPosition,
        surface: &mut TuiPaintSurface<'_>,
        ctx: &mut TuiPaintContext,
    ) {
        self.child.render(origin, surface, ctx);
    }

    fn size(&self) -> Option<TuiSize> {
        self.child.size()
    }

    fn origin(&self) -> Option<TuiScreenPoint> {
        self.child.origin()
    }

    fn present(&mut self, ctx: &mut TuiPresentationContext<'_>) {
        self.child.present(ctx);
    }

    fn dispatch_event(
        &mut self,
        event: &TuiEvent,
        event_ctx: &mut TuiEventContext<'_>,
        app: &AppContext,
    ) -> bool {
        if self.child.dispatch_event(event, event_ctx, app) {
            return true;
        }

        if let TuiEvent::KeyDown { keystroke, .. } = event {
            for binding in &mut self.bindings {
                if binding.matches(keystroke) {
                    (binding.callback)(event, event_ctx, app);
                    return true;
                }
            }
            if let Some(fallback) = self.fallback.as_mut() {
                fallback(event, event_ctx, app);
                return true;
            }
        }

        if let (TuiEvent::Paste { .. }, Some(paste)) = (event, self.paste.as_mut()) {
            paste(event, event_ctx, app);
            return true;
        }

        if let (TuiEvent::ScrollWheel { .. }, Some(scroll)) = (event, self.scroll.as_mut()) {
            scroll(event, event_ctx, app);
            return true;
        }

        if matches!(
            event,
            TuiEvent::LeftMouseDown { .. }
                | TuiEvent::LeftMouseDragged { .. }
                | TuiEvent::LeftMouseUp { .. }
        ) && let Some(mouse) = self.mouse.as_mut()
        {
            mouse(event, event_ctx, app);
            return true;
        }

        false
    }
}

#[cfg(test)]
#[path = "event_handler_tests.rs"]
mod tests;
