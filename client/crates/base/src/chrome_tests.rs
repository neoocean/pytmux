use super::*;
use crate::keys::{Key, Mods};

const TABS: [usize; 3] = [0, 1, 2];
const BADGES: [Badge; 3] = [Badge::Notices, Badge::Clock, Badge::Calendar];

/// 흔한 판: 패널이 하나뿐이라 위아래로 나갈 수 있고, 탭 셋 · 배지 셋.
fn ctx() -> ChromeCtx<'static> {
    ChromeCtx {
        pane_above: false,
        pane_below: false,
        tabs: &TABS,
        active: 1,
        badges: &BADGES,
    }
}

fn up(c: &mut Chrome, ctx: &ChromeCtx) -> Option<ChromeKey> {
    c.press(ctx, Key::Up, Mods::NONE)
}

#[test]
fn the_top_edge_sends_focus_to_the_tab_bar() {
    let mut chrome = Chrome::default();
    assert_eq!(up(&mut chrome, &ctx()), Some(ChromeKey::Redraw));
    assert_eq!(chrome.focus(), ChromeFocus::TabBar);
    // 진입 자리는 **지금 보고 있는 탭**이다 — 0번에서 시작하면 손이 매번 되돌아가야 한다.
    assert_eq!(chrome.spot(&ctx()), Some(TabSpot::Tab(1)));
}

#[test]
fn a_pane_above_keeps_the_arrow_for_the_pane() {
    // ★ 이것이 없으면 분할된 창에서 **위 패널로 갈 수가 없다** — 캔버스를 떠나는 것이
    // 캔버스 안 이동을 잡아먹는다.
    let mut chrome = Chrome::default();
    let ctx = ChromeCtx {
        pane_above: true,
        ..ctx()
    };
    assert_eq!(up(&mut chrome, &ctx), None);
    assert_eq!(chrome.focus(), ChromeFocus::Pane);
}

#[test]
fn the_bottom_edge_sends_focus_to_the_badges() {
    let mut chrome = Chrome::default();
    assert_eq!(
        chrome.press(&ctx(), Key::Down, Mods::NONE),
        Some(ChromeKey::Redraw)
    );
    assert_eq!(chrome.focus(), ChromeFocus::Badges);
    assert_eq!(chrome.badge(&ctx()), Some(Badge::Notices));
}

#[test]
fn no_badges_means_the_arrow_stays_with_the_pane() {
    // 배지가 없는데 포커스를 옮기면 **보이지 않는 것에 갇힌다**(파이썬도 안 들어간다).
    let mut chrome = Chrome::default();
    let ctx = ChromeCtx {
        badges: &[],
        ..ctx()
    };
    assert_eq!(chrome.press(&ctx, Key::Down, Mods::NONE), None);
    assert_eq!(chrome.focus(), ChromeFocus::Pane);
}

#[test]
fn the_tab_bar_cycles_through_the_tabs_then_new_then_close() {
    let mut chrome = Chrome::default();
    let ctx = ChromeCtx { active: 0, ..ctx() };
    up(&mut chrome, &ctx);
    let seen: Vec<TabSpot> = (0..6)
        .map(|_| {
            let spot = chrome.spot(&ctx).unwrap();
            chrome.press(&ctx, Key::Right, Mods::NONE);
            spot
        })
        .collect();
    assert_eq!(
        seen,
        vec![
            TabSpot::Tab(0),
            TabSpot::Tab(1),
            TabSpot::Tab(2),
            TabSpot::New,
            TabSpot::Close,
            // 한 바퀴 돌아 처음으로.
            TabSpot::Tab(0),
        ]
    );
}

#[test]
fn enter_on_a_tab_switches_to_that_tab_and_leaves() {
    let mut chrome = Chrome::default();
    let ctx = ChromeCtx { active: 0, ..ctx() };
    up(&mut chrome, &ctx);
    chrome.press(&ctx, Key::Right, Mods::NONE);
    assert_eq!(
        chrome.press(&ctx, Key::Enter, Mods::NONE),
        // ★ 자리가 아니라 **탭 index** 가 실린다 — `select_window` 가 받는 값이다.
        Some(ChromeKey::Done(Action::SelectTab(1)))
    );
}

#[test]
fn enter_on_plus_makes_a_tab_and_enter_on_x_closes_one() {
    let mut chrome = Chrome::default();
    let ctx = ctx();
    up(&mut chrome, &ctx);
    for _ in 0..2 {
        chrome.press(&ctx, Key::Right, Mods::NONE);
    }
    assert_eq!(chrome.spot(&ctx), Some(TabSpot::New));
    assert_eq!(
        chrome.press(&ctx, Key::Enter, Mods::NONE),
        Some(ChromeKey::Done(Action::NewTab))
    );
    let mut chrome = Chrome::default();
    up(&mut chrome, &ctx);
    for _ in 0..3 {
        chrome.press(&ctx, Key::Right, Mods::NONE);
    }
    assert_eq!(chrome.spot(&ctx), Some(TabSpot::Close));
    assert_eq!(
        chrome.press(&ctx, Key::Enter, Mods::NONE),
        Some(ChromeKey::Done(Action::KillTab))
    );
}

#[test]
fn making_a_tab_with_plus_stays_on_the_bar() {
    // 연달아 여러 개를 만드는 것이 자연스럽다(파이썬과 같다).
    let mut chrome = Chrome::default();
    let ctx = ctx();
    up(&mut chrome, &ctx);
    assert_eq!(
        chrome.press(&ctx, Key::Char('+'), Mods::NONE),
        Some(ChromeKey::Stay(Action::NewTab))
    );
    assert_eq!(chrome.focus(), ChromeFocus::TabBar);
}

#[test]
fn shift_arrows_move_the_selected_tab_and_the_cursor_follows() {
    // ★ 자리가 안 따라가면 계속 눌렀을 때 **서로 다른 탭이 하나씩** 밀린다.
    let mut chrome = Chrome::default();
    let ctx = ChromeCtx { active: 0, ..ctx() };
    up(&mut chrome, &ctx);
    assert_eq!(
        chrome.press(&ctx, Key::ShiftRight, Mods::NONE),
        Some(ChromeKey::Stay(Action::MoveTabAt { from: 0, to: 1 }))
    );
    assert_eq!(chrome.spot(&ctx), Some(TabSpot::Tab(1)));
    assert_eq!(
        chrome.press(&ctx, Key::ShiftRight, Mods::NONE),
        Some(ChromeKey::Stay(Action::MoveTabAt { from: 1, to: 2 }))
    );
}

#[test]
fn shift_arrows_do_not_wrap_at_the_ends() {
    // 순환시키면 맨 앞 탭에 Shift+← 한 번이 탭을 **맨 뒤로 던진다**.
    let mut chrome = Chrome::default();
    let ctx = ChromeCtx { active: 0, ..ctx() };
    up(&mut chrome, &ctx);
    assert_eq!(
        chrome.press(&ctx, Key::ShiftLeft, Mods::NONE),
        Some(ChromeKey::Redraw)
    );
    assert_eq!(chrome.spot(&ctx), Some(TabSpot::Tab(0)));
}

#[test]
fn shift_arrows_on_the_buttons_do_nothing() {
    let mut chrome = Chrome::default();
    let ctx = ctx();
    up(&mut chrome, &ctx);
    for _ in 0..2 {
        chrome.press(&ctx, Key::Right, Mods::NONE);
    }
    assert_eq!(chrome.spot(&ctx), Some(TabSpot::New));
    assert_eq!(
        chrome.press(&ctx, Key::ShiftRight, Mods::NONE),
        Some(ChromeKey::Redraw)
    );
    assert_eq!(chrome.spot(&ctx), Some(TabSpot::New));
}

#[test]
fn down_returns_to_the_pane_but_escape_leaves_the_mode() {
    // 둘을 가르는 것이 요점이다 — 아래로 내려오는 것은 **연속 조작**이고 Esc 는 끝이다.
    let mut chrome = Chrome::default();
    let ctx = ctx();
    up(&mut chrome, &ctx);
    assert_eq!(
        chrome.press(&ctx, Key::Down, Mods::NONE),
        Some(ChromeKey::Redraw)
    );
    assert_eq!(chrome.focus(), ChromeFocus::Pane);

    up(&mut chrome, &ctx);
    assert_eq!(
        chrome.press(&ctx, Key::Escape, Mods::NONE),
        Some(ChromeKey::Leave)
    );
    assert_eq!(chrome.focus(), ChromeFocus::Pane);
}

#[test]
fn the_badge_row_cycles_and_runs() {
    let mut chrome = Chrome::default();
    let ctx = ctx();
    chrome.press(&ctx, Key::Down, Mods::NONE);
    chrome.press(&ctx, Key::Right, Mods::NONE);
    assert_eq!(chrome.badge(&ctx), Some(Badge::Clock));
    assert_eq!(
        chrome.press(&ctx, Key::Enter, Mods::NONE),
        Some(ChromeKey::Done(Action::ToggleClock))
    );
    assert_eq!(chrome.focus(), ChromeFocus::Pane);
}

#[test]
fn the_badge_row_wraps_backwards() {
    let mut chrome = Chrome::default();
    let ctx = ctx();
    chrome.press(&ctx, Key::Down, Mods::NONE);
    chrome.press(&ctx, Key::Left, Mods::NONE);
    assert_eq!(chrome.badge(&ctx), Some(Badge::Calendar));
}

#[test]
fn every_badge_leads_somewhere() {
    // 배지를 늘려 놓고 **누를 곳을 안 만드는** 실수를 잡는다. 액션이 없으면 그 배지는
    // 눌러도 아무 일이 없고, 그건 안 보이는 것보다 나쁘다.
    for badge in [Badge::Notices, Badge::Host, Badge::Clock, Badge::Calendar] {
        assert!(!badge.label().is_empty(), "{badge:?} 에 낱말이 없다");
        let _ = badge.action();
    }
}

#[test]
fn digits_and_backtick_pass_through_even_while_focused() {
    // ★ esc 모드 어디서든 통해야 하는 키다(파이썬은 포커스 동선 **앞**에서 처리한다).
    // 여기서 먹으면 "탭바에 올라가면 번호 전환이 안 된다"가 된다.
    let mut chrome = Chrome::default();
    let ctx = ctx();
    up(&mut chrome, &ctx);
    for key in [Key::Char('2'), Key::Char('`'), Key::ShiftEscape] {
        assert_eq!(chrome.press(&ctx, key, Mods::NONE), None, "{key:?}");
    }
    // 넘겼다고 포커스가 풀리지는 않는다 — 푸는 것은 모드가 풀릴 때 호출부가 한다.
    assert_eq!(chrome.focus(), ChromeFocus::TabBar);
}

#[test]
fn modifier_combos_are_not_ours() {
    let mut chrome = Chrome::default();
    let ctx = ctx();
    up(&mut chrome, &ctx);
    assert_eq!(chrome.press(&ctx, Key::Char('c'), Mods::CTRL), None);
}

#[test]
fn an_unknown_key_is_swallowed_on_the_bar_but_drops_focus_on_the_badges() {
    let mut chrome = Chrome::default();
    let ctx = ctx();
    up(&mut chrome, &ctx);
    assert_eq!(
        chrome.press(&ctx, Key::Char('z'), Mods::NONE),
        Some(ChromeKey::Redraw)
    );
    assert_eq!(chrome.focus(), ChromeFocus::TabBar);

    chrome.reset();
    chrome.press(&ctx, Key::Down, Mods::NONE);
    assert_eq!(
        chrome.press(&ctx, Key::Char('z'), Mods::NONE),
        Some(ChromeKey::Redraw)
    );
    assert_eq!(chrome.focus(), ChromeFocus::Pane);
}

#[test]
fn the_spot_survives_tabs_disappearing_underneath() {
    // 탭바에 올라가 끝자리를 고른 채 다른 세션이 탭을 닫으면 자리가 목록 밖으로 나간다.
    // 그때 **패닉하지 않고** 마지막 자리로 접힌다.
    let mut chrome = Chrome::default();
    let ctx = ctx();
    up(&mut chrome, &ctx);
    for _ in 0..3 {
        chrome.press(&ctx, Key::Right, Mods::NONE);
    }
    let one = [0usize];
    let shrunk = ChromeCtx {
        tabs: &one,
        active: 0,
        ..ctx
    };
    assert_eq!(chrome.spot(&shrunk), Some(TabSpot::Close));
    assert_eq!(
        chrome.press(&shrunk, Key::Enter, Mods::NONE),
        Some(ChromeKey::Done(Action::KillTab))
    );
}

#[test]
fn the_badge_index_survives_badges_disappearing() {
    let mut chrome = Chrome::default();
    let ctx = ctx();
    chrome.press(&ctx, Key::Down, Mods::NONE);
    chrome.press(&ctx, Key::Right, Mods::NONE);
    chrome.press(&ctx, Key::Right, Mods::NONE);
    let one = [Badge::Clock];
    let shrunk = ChromeCtx {
        badges: &one,
        ..ctx
    };
    assert_eq!(chrome.badge(&shrunk), None);
    assert_eq!(
        chrome.press(&shrunk, Key::Enter, Mods::NONE),
        Some(ChromeKey::Done(Action::ToggleClock))
    );
}

// ── 탭 드래그 판정(G9v — 파이썬 on_mouse_up 표) ───────────────────────────────

#[test]
fn a_drag_decides_like_the_python_mouse_up() {
    use crate::chrome::{DragDrop, DragTab, DragTarget, drag_drop};
    let l = DragTab { remote: false, pinned: false };
    let p = DragTab { remote: false, pinned: true };
    let r = DragTab { remote: true, pinned: false };
    let tabs = [l, l, p, r];
    // 제자리 = 전환(클릭과 같은 뜻).
    assert_eq!(drag_drop(&tabs, 0, DragTarget::Tab(0)), Some(DragDrop::Select(0)));
    // 로컬끼리 핀 같음 = 재정렬.
    assert_eq!(
        drag_drop(&tabs, 0, DragTarget::Tab(1)),
        Some(DragDrop::Reorder { from: 0, to: 1 })
    );
    // 핀 경계 넘김 = 소스 핀 토글(§12 ② — 재정렬이 아니다).
    assert_eq!(
        drag_drop(&tabs, 0, DragTarget::Tab(2)),
        Some(DragDrop::SetPin { index: 0, on: true })
    );
    assert_eq!(
        drag_drop(&tabs, 2, DragTarget::Tab(0)),
        Some(DragDrop::SetPin { index: 2, on: false })
    );
    // 원격이 끼면(소스든 대상이든) 순서는 업스트림 소유 — 전환으로 접는다.
    assert_eq!(drag_drop(&tabs, 3, DragTarget::Tab(0)), Some(DragDrop::Select(3)));
    assert_eq!(drag_drop(&tabs, 0, DragTarget::Tab(3)), Some(DragDrop::Select(0)));
    // 콘텐츠 드롭 = 합치기(로컬만 — 원격은 merge-remote-tab 의 자리).
    assert_eq!(
        drag_drop(&tabs, 1, DragTarget::Content { pane: 7, horizontal: true }),
        Some(DragDrop::Join { pane: 7, src: 1, horizontal: true })
    );
    assert_eq!(
        drag_drop(&tabs, 3, DragTarget::Content { pane: 7, horizontal: false }),
        Some(DragDrop::Select(3))
    );
    // 여백 = 전환 · 없는 소스 = 아무 일도 아님.
    assert_eq!(drag_drop(&tabs, 1, DragTarget::Other), Some(DragDrop::Select(1)));
    assert_eq!(drag_drop(&tabs, 9, DragTarget::Other), None);
}
