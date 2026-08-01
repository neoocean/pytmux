use std::cell::Cell;
use std::rc::Rc;

use super::TuiEventHandler;
use crate::elements::tui::test_support::with_event_context;
use crate::elements::tui::{TuiChildView, TuiElement, TuiEvent, TuiPresentationContext};
use crate::event::KeyEventDetails;
use crate::keymap::Keystroke;
use crate::{App, EntityId, EntityIdMap};

fn key_event(key: &str) -> TuiEvent {
    TuiEvent::KeyDown {
        keystroke: Keystroke {
            key: key.to_owned(),
            ..Default::default()
        },
        chars: key.to_owned(),
        details: KeyEventDetails::default(),
        is_composing: false,
    }
}

#[test]
fn invokes_callback_on_matching_key_and_reports_handled() {
    App::test((), |app| async move {
        app.read(|app_ctx| {
            let hits = Rc::new(Cell::new(0u32));
            let counter = hits.clone();
            let mut handler =
                TuiEventHandler::new(().finish()).on_key("enter", move |_event, _ctx, _app| {
                    counter.set(counter.get() + 1);
                });

            with_event_context(|event_ctx| {
                let handled = handler.dispatch_event(&key_event("enter"), event_ctx, app_ctx);
                assert!(handled);
                assert_eq!(hits.get(), 1);

                // A non-matching key is left unhandled for ancestors, runs no callback.
                let handled = handler.dispatch_event(&key_event("esc"), event_ctx, app_ctx);
                assert!(!handled);
                assert_eq!(hits.get(), 1);
            });
        });
    });
}

#[test]
fn child_consumes_the_event_before_the_wrapper() {
    App::test((), |app| async move {
        app.read(|app_ctx| {
            let inner_hits = Rc::new(Cell::new(0u32));
            let outer_hits = Rc::new(Cell::new(0u32));
            let inner_counter = inner_hits.clone();
            let outer_counter = outer_hits.clone();

            let inner = TuiEventHandler::new(().finish()).on_key("enter", move |_, _, _| {
                inner_counter.set(inner_counter.get() + 1)
            });
            let mut outer = TuiEventHandler::new(inner.finish()).on_key("enter", move |_, _, _| {
                outer_counter.set(outer_counter.get() + 1)
            });

            let handled = with_event_context(|event_ctx| {
                outer.dispatch_event(&key_event("enter"), event_ctx, app_ctx)
            });

            assert!(handled);
            assert_eq!(inner_hits.get(), 1);
            assert_eq!(outer_hits.get(), 0);
        });
    });
}

#[test]
fn present_recurses_into_the_wrapped_child() {
    let root = EntityId::from_usize(1);
    let embedded = EntityId::from_usize(2);
    let mut parent_by_child = EntityIdMap::default();

    {
        let mut rendered_views = EntityIdMap::default();
        let mut ctx = TuiPresentationContext::new(root, &mut rendered_views, &mut parent_by_child);
        let child_node = TuiChildView::from_rendered(embedded, Box::new(()), ctx.rendered_views);
        let mut handler = TuiEventHandler::new(child_node.finish());
        handler.present(&mut ctx);
    }

    assert_eq!(parent_by_child.get(&embedded), Some(&root));
}

mod any_key {
    //! Catch-all dispatch. A terminal client forwards *every* typed key to its
    //! child process, and that set cannot be written out as bindings (every
    //! printable character, every modifier combination) — so unmatched keys need
    //! a single fallback rather than being reported unhandled.
    use std::cell::RefCell;
    use std::rc::Rc;

    use super::{App, TuiChildView, TuiElement, TuiEvent, TuiEventHandler, key_event,
                with_event_context};

    #[test]
    fn fallback_receives_keys_that_match_no_binding() {
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let seen: Rc<RefCell<Vec<String>>> = Rc::new(RefCell::new(Vec::new()));
                let sink = seen.clone();
                let mut handler =
                    TuiEventHandler::new(().finish()).on_any_key(move |event, _, _| {
                        if let TuiEvent::KeyDown { keystroke, .. } = event {
                            sink.borrow_mut().push(keystroke.key.clone());
                        }
                    });
                with_event_context(|event_ctx| {
                    for name in ["a", "한", "f5"] {
                        let handled =
                            handler.dispatch_event(&key_event(name), event_ctx, app_ctx);
                        assert!(handled, "catch-all 이 있으면 처리로 보고해야 한다: {name}");
                    }
                });
                assert_eq!(*seen.borrow(), vec!["a", "한", "f5"]);
            });
        });
    }

    #[test]
    fn specific_bindings_keep_priority_over_the_fallback() {
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let hits: Rc<RefCell<Vec<&'static str>>> = Rc::new(RefCell::new(Vec::new()));
                let (bound, fallback) = (hits.clone(), hits.clone());
                let mut handler = TuiEventHandler::new(().finish())
                    .on_key("q", move |_, _, _| bound.borrow_mut().push("bound"))
                    .on_any_key(move |_, _, _| fallback.borrow_mut().push("fallback"));
                with_event_context(|event_ctx| {
                    handler.dispatch_event(&key_event("q"), event_ctx, app_ctx);
                    handler.dispatch_event(&key_event("z"), event_ctx, app_ctx);
                });
                assert_eq!(*hits.borrow(), vec!["bound", "fallback"]);
            });
        });
    }

    #[test]
    fn without_a_fallback_unmatched_keys_stay_unhandled() {
        // 기존 거동(조상이 반응할 수 있게 남긴다)이 안 바뀌었음을 못박는다.
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let mut handler = TuiEventHandler::new(().finish());
                with_event_context(|event_ctx| {
                    assert!(!handler.dispatch_event(&key_event("a"), event_ctx, app_ctx));
                });
            });
        });
    }
}

mod paste {
    //! 붙여넣기는 **키가 아니다** — 터미널이 payload 하나를 통째로 준다(bracketed
    //! paste). 키 콜백으로는 표현도 복원도 못 하므로 별도 경로가 필요하다.
    use std::cell::RefCell;
    use std::rc::Rc;

    use super::{App, TuiChildView, TuiElement, TuiEvent, TuiEventHandler, key_event,
                with_event_context};

    fn paste_event(text: &str) -> TuiEvent {
        TuiEvent::Paste {
            text: text.to_owned(),
        }
    }

    pub(super) fn wheel_event() -> TuiEvent {
        TuiEvent::ScrollWheel {
            position: crate::elements::tui::TuiPoint::new(0, 0),
            delta: (0, 1),
            precise: false,
            modifiers: Default::default(),
        }
    }

    #[test]
    fn the_whole_payload_reaches_the_callback_at_once() {
        // 여러 줄이 한 번에 와야 한다. 줄 단위로 쪼개져 오면 셸이 줄마다 실행한다.
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let seen: Rc<RefCell<Vec<String>>> = Rc::new(RefCell::new(Vec::new()));
                let sink = seen.clone();
                let mut handler = TuiEventHandler::new(().finish()).on_paste(move |event, _, _| {
                    if let TuiEvent::Paste { text } = event {
                        sink.borrow_mut().push(text.clone());
                    }
                });
                with_event_context(|event_ctx| {
                    let handled =
                        handler.dispatch_event(&paste_event("a\nb\n"), event_ctx, app_ctx);
                    assert!(handled, "붙여넣기가 처리로 보고되지 않았다");
                });
                assert_eq!(*seen.borrow(), vec!["a\nb\n"]);
            });
        });
    }

    #[test]
    fn the_key_catch_all_does_not_swallow_a_paste() {
        // catch-all 은 "모든 키를 자식에게"를 위한 것이다. 여기에 붙여넣기까지 흘러들면
        // 키 경로가 payload 를 키스트로크처럼 다루게 된다.
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let hits: Rc<RefCell<Vec<&'static str>>> = Rc::new(RefCell::new(Vec::new()));
                let (keys, pastes) = (hits.clone(), hits.clone());
                let mut handler = TuiEventHandler::new(().finish())
                    .on_any_key(move |_, _, _| keys.borrow_mut().push("key"))
                    .on_paste(move |_, _, _| pastes.borrow_mut().push("paste"));
                with_event_context(|event_ctx| {
                    handler.dispatch_event(&key_event("a"), event_ctx, app_ctx);
                    handler.dispatch_event(&paste_event("x"), event_ctx, app_ctx);
                });
                assert_eq!(*hits.borrow(), vec!["key", "paste"]);
            });
        });
    }

    #[test]
    fn a_wheel_event_does_not_reach_the_paste_callback() {
        // 둘 다 "키가 아닌 이벤트"라 한 팔로 뭉치기 쉽다. 뭉치면 휠 한 번이 빈
        // 붙여넣기가 된다.
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let mut handler =
                    TuiEventHandler::new(().finish()).on_paste(|_, _, _| unreachable!());
                with_event_context(|event_ctx| {
                    assert!(!handler.dispatch_event(&wheel_event(), event_ctx, app_ctx));
                });
            });
        });
    }

    #[test]
    fn without_a_paste_handler_the_event_stays_unhandled() {
        // 키 콜백만 걸린 엘리먼트가 붙여넣기를 삼켜 버리면 조상이 그것을 못 본다.
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let mut handler =
                    TuiEventHandler::new(().finish()).on_any_key(|_, _, _| unreachable!());
                with_event_context(|event_ctx| {
                    assert!(!handler.dispatch_event(&paste_event("x"), event_ctx, app_ctx));
                });
            });
        });
    }
}

mod scroll {
    //! 휠. 클릭·호버는 [`TuiHoverable`](super::super::TuiHoverable) 의 몫이지만 휠은
    //! 한 위젯에 대한 제스처가 아니다 — 터미널 클라는 **보고 있는 패널**을 굴린다.
    use std::cell::RefCell;
    use std::rc::Rc;

    use super::paste::wheel_event;
    use super::{App, TuiChildView, TuiElement, TuiEvent, TuiEventHandler, key_event,
                with_event_context};

    #[test]
    fn the_wheel_reaches_the_callback_with_its_direction() {
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let seen: Rc<RefCell<Vec<isize>>> = Rc::new(RefCell::new(Vec::new()));
                let sink = seen.clone();
                let mut handler = TuiEventHandler::new(().finish()).on_scroll(move |event, _, _| {
                    if let TuiEvent::ScrollWheel { delta, .. } = event {
                        sink.borrow_mut().push(delta.1);
                    }
                });
                with_event_context(|event_ctx| {
                    let handled = handler.dispatch_event(&wheel_event(), event_ctx, app_ctx);
                    assert!(handled, "휠이 처리로 보고되지 않았다");
                });
                assert_eq!(*seen.borrow(), vec![1]);
            });
        });
    }

    #[test]
    fn keys_do_not_reach_the_scroll_callback() {
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let mut handler =
                    TuiEventHandler::new(().finish()).on_scroll(|_, _, _| unreachable!());
                with_event_context(|event_ctx| {
                    assert!(!handler.dispatch_event(&key_event("a"), event_ctx, app_ctx));
                });
            });
        });
    }

    #[test]
    fn without_a_scroll_handler_the_wheel_stays_unhandled() {
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let mut handler = TuiEventHandler::new(().finish());
                with_event_context(|event_ctx| {
                    assert!(!handler.dispatch_event(&wheel_event(), event_ctx, app_ctx));
                });
            });
        });
    }
}

mod mouse {
    //! 왼쪽 버튼 press/drag/release. 좌표로 해석하는 제스처(분할 경계 끌기)를 위한 것이라
    //! 위젯 단위인 [`TuiHoverable`](super::super::TuiHoverable) 와 목적이 다르다.
    use std::cell::RefCell;
    use std::rc::Rc;

    use super::paste::wheel_event;
    use super::{App, TuiChildView, TuiElement, TuiEvent, TuiEventHandler, key_event,
                with_event_context};
    use crate::elements::tui::TuiPoint;

    fn down(x: u16, y: u16) -> TuiEvent {
        TuiEvent::LeftMouseDown {
            position: TuiPoint::new(x, y),
            modifiers: Default::default(),
            click_count: 1,
            is_first_mouse: false,
        }
    }

    fn dragged(x: u16, y: u16) -> TuiEvent {
        TuiEvent::LeftMouseDragged {
            position: TuiPoint::new(x, y),
            modifiers: Default::default(),
        }
    }

    fn up(x: u16, y: u16) -> TuiEvent {
        TuiEvent::LeftMouseUp {
            position: TuiPoint::new(x, y),
            modifiers: Default::default(),
        }
    }

    #[test]
    fn press_drag_and_release_all_reach_the_same_callback() {
        // 하나라도 빠지면 뷰가 드래그 중간에 멈춘다(놓은 줄 모르고 계속 끌거나,
        // 잡은 줄 모르고 안 움직이거나).
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let seen: Rc<RefCell<Vec<&'static str>>> = Rc::new(RefCell::new(Vec::new()));
                let sink = seen.clone();
                let mut handler = TuiEventHandler::new(().finish()).on_mouse(move |event, _, _| {
                    sink.borrow_mut().push(match event {
                        TuiEvent::LeftMouseDown { .. } => "down",
                        TuiEvent::LeftMouseDragged { .. } => "drag",
                        TuiEvent::LeftMouseUp { .. } => "up",
                        _ => "other",
                    });
                });
                with_event_context(|event_ctx| {
                    for event in [down(1, 2), dragged(3, 4), up(3, 4)] {
                        assert!(
                            handler.dispatch_event(&event, event_ctx, app_ctx),
                            "{event:?} 가 처리로 보고되지 않았다"
                        );
                    }
                });
                assert_eq!(*seen.borrow(), vec!["down", "drag", "up"]);
            });
        });
    }

    #[test]
    fn keys_and_the_wheel_do_not_reach_the_mouse_callback() {
        // 휠도 마우스 이벤트지만 뜻이 다르다(스크롤 ↔ 제스처). 한 팔로 뭉치면 휠 한 번이
        // 경계선을 잡는 것이 된다.
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let mut handler =
                    TuiEventHandler::new(().finish()).on_mouse(|_, _, _| unreachable!());
                with_event_context(|event_ctx| {
                    assert!(!handler.dispatch_event(&key_event("a"), event_ctx, app_ctx));
                    assert!(!handler.dispatch_event(&wheel_event(), event_ctx, app_ctx));
                });
            });
        });
    }

    #[test]
    fn without_a_mouse_handler_the_press_stays_unhandled() {
        App::test((), |app| async move {
            app.read(|app_ctx| {
                let mut handler = TuiEventHandler::new(().finish());
                with_event_context(|event_ctx| {
                    assert!(!handler.dispatch_event(&down(1, 1), event_ctx, app_ctx));
                });
            });
        });
    }
}
