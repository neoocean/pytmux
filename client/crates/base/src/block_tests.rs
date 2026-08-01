//! 상태 전이 오라클.
//!
//! 이 테스트가 통과하면 **두 백엔드가 같은 규칙으로 움직인다**. 뷰는 그리기만 하고
//! 판단은 전부 여기 있기 때문이다.

use super::*;
use crate::Action;

fn list() -> BlockList {
    BlockList::new(vec![
        Block::new("a", "/tmp", ["1"], BlockState::Exited(0)),
        Block::new("b", "/tmp", ["2"], BlockState::Exited(1)),
        Block::new("c", "/tmp", ["3"], BlockState::Running),
    ])
}

#[test]
fn selection_starts_at_the_top() {
    assert_eq!(list().selected_index(), 0);
    assert_eq!(list().selected_block().map(|b| b.command.as_str()), Some("a"));
}

#[test]
fn next_and_prev_walk_the_list() {
    let mut l = list();
    assert!(l.apply(Action::SelectNext));
    assert_eq!(l.selected_index(), 1);
    assert!(l.apply(Action::SelectPrev));
    assert_eq!(l.selected_index(), 0);
}

#[test]
fn selection_stops_at_the_ends_and_reports_no_change() {
    // 끝에서 더 눌러도 넘어가지 않고, 다시 그릴 이유가 없다고 알려야 한다.
    let mut l = list();
    assert!(!l.apply(Action::SelectPrev), "맨 위에서 위로 = 변화 없음");
    assert_eq!(l.selected_index(), 0);

    l.apply(Action::SelectLast);
    assert_eq!(l.selected_index(), 2);
    assert!(!l.apply(Action::SelectNext), "맨 아래에서 아래로 = 변화 없음");
    assert_eq!(l.selected_index(), 2);
}

#[test]
fn first_and_last_jump_and_are_idempotent() {
    let mut l = list();
    assert!(l.apply(Action::SelectLast));
    assert_eq!(l.selected_index(), 2);
    assert!(!l.apply(Action::SelectLast), "이미 끝이면 변화 없음");

    assert!(l.apply(Action::SelectFirst));
    assert_eq!(l.selected_index(), 0);
    assert!(!l.apply(Action::SelectFirst), "이미 처음이면 변화 없음");
}

#[test]
fn toggle_expand_flips_and_always_redraws() {
    let mut l = list();
    let before = l.is_expanded();
    assert!(l.apply(Action::ToggleExpand));
    assert_ne!(l.is_expanded(), before);
    assert!(l.apply(Action::ToggleExpand));
    assert_eq!(l.is_expanded(), before);
}

#[test]
fn quit_is_not_a_state_transition() {
    // 종료는 런타임에 대한 요청이라 상태를 바꾸지 않는다 — 뷰가 따로 처리한다.
    let mut l = list();
    assert!(!l.apply(Action::Quit));
    assert_eq!(l.selected_index(), 0);
}

#[test]
fn empty_list_is_navigable_without_panicking() {
    let mut l = BlockList::new(vec![]);
    assert!(l.is_empty());
    assert_eq!(l.selected_block(), None);
    assert!(!l.apply(Action::SelectNext));
    assert!(!l.apply(Action::SelectPrev));
    assert!(!l.apply(Action::SelectLast));
}

#[test]
fn selection_is_always_a_valid_index_when_non_empty() {
    // 불변식: 목록이 비어 있지 않으면 selected_block() 은 항상 무언가를 준다.
    let mut l = list();
    for action in [
        Action::SelectNext,
        Action::SelectNext,
        Action::SelectNext,
        Action::SelectLast,
        Action::SelectPrev,
        Action::SelectFirst,
        Action::SelectPrev,
    ] {
        l.apply(action);
        assert!(l.selected_block().is_some(), "{action:?} 뒤에 선택이 깨졌다");
    }
}

#[test]
fn block_state_badges_distinguish_success_from_failure() {
    assert_eq!(BlockState::Exited(0).badge(), "ok");
    assert_eq!(BlockState::Exited(1).badge(), "err");
    assert_eq!(BlockState::Running.badge(), "···");
    assert_eq!(BlockState::Exited(0).succeeded(), Some(true));
    assert_eq!(BlockState::Exited(2).succeeded(), Some(false));
    assert_eq!(BlockState::Running.succeeded(), None, "돌고 있으면 아직 모른다");
}

#[test]
fn summary_reports_command_state_and_line_count() {
    let block = Block::new("ls -la", "/tmp", ["a", "b"], BlockState::Exited(0));
    assert_eq!(block.summary(), "ls -la · ok · 2줄");
}

#[test]
fn sample_covers_every_block_state() {
    // 데모 표본이 성공·실패·진행중을 다 담아야 두 뷰의 색 분기가 눈에 보인다.
    let sample = BlockList::sample();
    let states: Vec<_> = sample.blocks().iter().map(|b| b.state).collect();
    assert!(states.iter().any(|s| matches!(s, BlockState::Exited(0))));
    assert!(states.iter().any(|s| matches!(s, BlockState::Exited(c) if *c != 0)));
    assert!(states.iter().any(|s| matches!(s, BlockState::Running)));
}
