//! 작성창 편집 회귀(패리티 `e_ins` · `ComposePromptScreen`).
//!
//! 여기서 지키는 것은 **파이썬 클라와 같은 손버릇**이다. 편집기는 사용자가 가장 세게
//! 기대를 갖는 자리라, 한 키만 달라도 "이 클라는 이상하다"가 된다.

use super::*;

fn typed(editor: &mut Editor, text: &str) {
    for c in text.chars() {
        editor.press(Key::Char(c), Mods::NONE);
    }
}

#[test]
fn a_fresh_editor_starts_empty_with_the_cursor_at_the_front() {
    let editor = Editor::new("");
    assert_eq!(editor.text(), "");
    assert_eq!(editor.cursor(), (0, 0));
    assert_eq!(editor.selection(), None);
}

#[test]
fn a_seed_puts_the_cursor_at_the_end_of_the_document() {
    // 이어 쓰려고 여는 것이지 앞에 끼워 넣으려고 여는 것이 아니다(파이썬 on_mount 와 같다).
    let editor = Editor::new("첫 줄\n둘째");
    assert_eq!(editor.cursor(), (1, 2));
}

#[test]
fn enter_sends_and_shift_enter_makes_a_newline() {
    // ★ 이 짝이 이 화면의 존재 이유 절반이다 — Claude Code 프롬프트가 그렇고, 기본
    // TextArea 는 반대다(Enter 가 줄바꿈). 뒤집히면 보내려던 것이 줄만 바뀐다.
    let mut editor = Editor::new("");
    typed(&mut editor, "가");
    assert_eq!(
        editor.press(Key::ShiftEnter, Mods::NONE),
        EditorKey::Consumed
    );
    typed(&mut editor, "나");
    assert_eq!(editor.text(), "가\n나");
    assert_eq!(editor.press(Key::Enter, Mods::NONE), EditorKey::Inject);
}

#[test]
fn ctrl_j_is_the_same_newline_as_shift_enter() {
    // 단말이 Shift+Enter 를 LF 로 보내면 그 조합이 Ctrl+J 로 도착한다. 하나만 받으면
    // **그 단말에서만** 줄바꿈이 안 된다.
    let mut editor = Editor::new("a");
    editor.press(Key::Char('j'), Mods::CTRL);
    typed(&mut editor, "b");
    assert_eq!(editor.text(), "a\nb");
}

#[test]
fn ctrl_s_also_sends() {
    let mut editor = Editor::new("x");
    assert_eq!(editor.press(Key::Char('s'), Mods::CTRL), EditorKey::Inject);
}

#[test]
fn escape_opens_a_menu_instead_of_cancelling() {
    // ★ `Esc` 한 번이 곧 취소면, 편집 중 습관적으로 누른 `Esc` 가 쓰던 글을 날린다.
    let mut editor = Editor::new("긴 글");
    assert_eq!(editor.press(Key::Escape, Mods::NONE), EditorKey::Consumed);
    assert!(editor.esc_mode(), "메뉴 모드에 안 들어갔다");
    assert_eq!(editor.text(), "긴 글", "글이 사라졌다");
}

#[test]
fn the_second_escape_cancels() {
    let mut editor = Editor::new("x");
    editor.press(Key::Escape, Mods::NONE);
    assert_eq!(editor.press(Key::Escape, Mods::NONE), EditorKey::Cancel);
}

#[test]
fn colon_in_the_menu_opens_the_palette_and_the_colon_is_not_typed() {
    // TextArea 가 키를 먼저 받으므로 `:` 가 버퍼에 들어가기 전에 가로채야 한다
    // (파이썬 `_ComposeTextArea._on_key` 가 같은 이유로 먼저 본다).
    let mut editor = Editor::new("");
    editor.press(Key::Escape, Mods::NONE);
    assert_eq!(
        editor.press(Key::Char(':'), Mods::NONE),
        EditorKey::OpenPalette
    );
    assert_eq!(editor.text(), "", "`:` 가 버퍼에 들어갔다");
}

#[test]
fn any_other_key_just_leaves_the_menu_and_is_swallowed() {
    // 모드를 빠져나오는 그 키가 편집까지 하면 사용자는 무엇이 들어갔는지 모른다.
    let mut editor = Editor::new("");
    editor.press(Key::Escape, Mods::NONE);
    assert_eq!(editor.press(Key::Char('a'), Mods::NONE), EditorKey::Consumed);
    assert!(!editor.esc_mode());
    assert_eq!(editor.text(), "", "모드를 빠져나온 키가 찍혔다");
}

// ── 블록 선택 — **이 화면이 존재하는 이유** ────────────────────────────────────
//
// 자식 프롬프트 입력기가 못 하는 바로 그것이다. 여기가 안 되면 이 화면을 만든 값이 없다.

#[test]
fn shift_arrows_select_and_typing_replaces_the_selection() {
    let mut editor = Editor::new("abcdef");
    // 커서는 끝(0,6). 왼쪽으로 셋 고른다 → "def".
    for _ in 0..3 {
        editor.press(Key::ShiftLeft, Mods::NONE);
    }
    assert_eq!(editor.selection(), Some(((0, 3), (0, 6))));
    typed(&mut editor, "X");
    assert_eq!(editor.text(), "abcX");
}

#[test]
fn a_backwards_selection_is_reported_in_order() {
    // 뒤에서 앞으로 끌면 앵커가 커서보다 뒤에 있다. 정렬해서 안 돌려주면 **역방향
    // 선택만** 강조가 사라지거나 지우기가 어긋난다.
    let mut editor = Editor::new("abc");
    editor.press(Key::Home, Mods::NONE);
    editor.press(Key::ShiftRight, Mods::NONE);
    editor.press(Key::ShiftRight, Mods::NONE);
    assert_eq!(editor.selection(), Some(((0, 0), (0, 2))));
}

#[test]
fn selection_can_span_lines_and_deleting_joins_them() {
    let mut editor = Editor::new("첫째\n둘째\n셋째");
    // 끝(2,2)에서 위로 하나 → 고른 것은 줄 경계를 넘는 "\n셋째"다.
    editor.press(Key::ShiftUp, Mods::NONE);
    assert_eq!(editor.selection(), Some(((1, 2), (2, 2))));
    editor.press(Key::Backspace, Mods::NONE);
    assert_eq!(editor.text(), "첫째\n둘째", "줄이 안 이어졌다");
    assert_eq!(editor.cursor(), (1, 2));
}

#[test]
fn selecting_to_the_start_of_a_line_takes_the_whole_line() {
    let mut editor = Editor::new("첫째\n둘째\n셋째");
    editor.press(Key::ShiftUp, Mods::NONE);
    editor.press(Key::ShiftHome, Mods::NONE);
    assert_eq!(editor.selection(), Some(((1, 0), (2, 2))));
    editor.press(Key::Backspace, Mods::NONE);
    // 두 줄이 통째로 사라지고 **빈 줄 하나**가 남는다(끝의 개행이 그 줄이다).
    assert_eq!(editor.text(), "첫째\n");
}

#[test]
fn ctrl_a_selects_the_whole_document() {
    let mut editor = Editor::new("하나\n둘\n셋");
    editor.press(Key::Char('a'), Mods::CTRL);
    assert_eq!(editor.selection(), Some(((0, 0), (2, 1))));
    typed(&mut editor, "새");
    assert_eq!(editor.text(), "새");
}

#[test]
fn a_plain_arrow_drops_the_selection_without_deleting() {
    // 고른 뒤 방향키를 누르면 선택이 풀리는 것이 어느 편집기든 같다. 지워지면 큰일이다.
    let mut editor = Editor::new("abc");
    editor.press(Key::Char('a'), Mods::CTRL);
    editor.press(Key::Left, Mods::NONE);
    assert_eq!(editor.selection(), None);
    assert_eq!(editor.text(), "abc");
}

#[test]
fn ctrl_home_and_ctrl_end_walk_the_whole_document() {
    let mut editor = Editor::new("하나\n둘\n셋");
    editor.press(Key::Home, Mods::CTRL);
    assert_eq!(editor.cursor(), (0, 0));
    editor.press(Key::End, Mods::CTRL);
    assert_eq!(editor.cursor(), (2, 1));
}

// ── 지우기·잇기 ───────────────────────────────────────────────────────────────

#[test]
fn backspace_at_the_start_of_a_line_joins_it_to_the_one_above() {
    // 아무 일도 안 하면 여러 줄로 갈린 것을 되돌릴 방법이 없다.
    let mut editor = Editor::new("가\n나");
    editor.press(Key::Home, Mods::NONE);
    editor.press(Key::Backspace, Mods::NONE);
    assert_eq!(editor.text(), "가나");
    assert_eq!(editor.cursor(), (0, 1));
}

#[test]
fn delete_at_the_end_of_a_line_pulls_the_next_one_up() {
    let mut editor = Editor::new("가\n나");
    editor.press(Key::Home, Mods::CTRL);
    editor.press(Key::End, Mods::NONE);
    editor.press(Key::Delete, Mods::NONE);
    assert_eq!(editor.text(), "가나");
}

#[test]
fn backspace_at_the_very_front_does_nothing() {
    let mut editor = Editor::new("가");
    editor.press(Key::Home, Mods::CTRL);
    editor.press(Key::Backspace, Mods::NONE);
    assert_eq!(editor.text(), "가");
}

// ── 한글 ──────────────────────────────────────────────────────────────────────

#[test]
fn columns_are_characters_not_bytes() {
    // ★ 바이트로 세면 한글에서 커서가 **글자 가운데**로 들어가고, 백스페이스 한 번이
    // 글자를 반쪽 낸다. 이 저장소 전체가 문자 인덱스로 셈한다.
    let mut editor = Editor::new("한글");
    assert_eq!(editor.cursor(), (0, 2));
    editor.press(Key::Backspace, Mods::NONE);
    assert_eq!(editor.text(), "한");
}

// ── 붙여넣기 ──────────────────────────────────────────────────────────────────

#[test]
fn pasting_multiline_text_keeps_the_line_breaks() {
    // 한 줄로 이어 붙이면 사용자가 복사한 모양이 무너진다.
    let mut editor = Editor::new("");
    editor.insert_str("첫 줄\n둘째 줄");
    assert_eq!(editor.text(), "첫 줄\n둘째 줄");
    assert_eq!(editor.cursor(), (1, 4), "커서가 붙여넣은 것의 끝에 없다");
}

#[test]
fn pasting_over_a_selection_replaces_it() {
    let mut editor = Editor::new("abc");
    editor.press(Key::Char('a'), Mods::CTRL);
    editor.insert_str("XY");
    assert_eq!(editor.text(), "XY");
}

// ── 이 화면은 **모든 키를 먹는다** ────────────────────────────────────────────

#[test]
fn unknown_modifier_combinations_are_swallowed_not_leaked() {
    // 규칙 1(모듈 `screens` 문서): 화면이 떠 있으면 모든 키가 화면의 것이다. 새면 셸에
    // 제어코드가 간다.
    let mut editor = Editor::new("");
    for key in [Key::Char('q'), Key::Tab, Key::PageUp, Key::Function(5)] {
        assert_eq!(editor.press(key, Mods::CTRL), EditorKey::Consumed, "{key:?}");
    }
    assert_eq!(editor.text(), "");
}
