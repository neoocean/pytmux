use super::*;

fn edit(name: &str) -> SessionEdit {
    SessionEdit::new(name)
}

fn press(e: &mut SessionEdit, key: Key) -> SessionEditKey {
    e.press(key, Mods::NONE)
}

#[test]
fn it_starts_with_the_current_name_and_the_cursor_at_the_end() {
    // 이어 쓰려고 여는 것이지 앞에 끼워 넣으려고 여는 것이 아니다(파이썬과 같다).
    let e = edit("playground");
    assert_eq!(e.text(), "playground");
    assert_eq!(e.cursor(), 10);
}

#[test]
fn typing_and_erasing_walk_the_name() {
    let mut e = edit("dev");
    assert_eq!(press(&mut e, Key::Char('1')), SessionEditKey::Consumed);
    assert_eq!(e.text(), "dev1");
    press(&mut e, Key::Backspace);
    press(&mut e, Key::Backspace);
    assert_eq!(e.text(), "de");
    press(&mut e, Key::Home);
    assert_eq!(e.cursor(), 0);
    press(&mut e, Key::Delete);
    assert_eq!(e.text(), "e");
    press(&mut e, Key::End);
    press(&mut e, Key::Char('x'));
    assert_eq!(e.text(), "ex");
}

#[test]
fn the_edges_do_not_run_off_the_ends() {
    // 빈 이름에서 Backspace·←, 끝에서 Delete·→ 가 패닉하거나 커서를 밖으로 내보내면
    // 안 된다 — 그리는 쪽이 커서 자리로 글자를 자른다.
    let mut e = edit("");
    press(&mut e, Key::Backspace);
    press(&mut e, Key::Delete);
    press(&mut e, Key::Left);
    press(&mut e, Key::Right);
    assert_eq!(e.text(), "");
    assert_eq!(e.cursor(), 0);

    let mut e = edit("ab");
    press(&mut e, Key::End);
    press(&mut e, Key::Right);
    press(&mut e, Key::Delete);
    assert_eq!((e.text().as_str(), e.cursor()), ("ab", 2));
}

#[test]
fn the_cursor_is_counted_in_characters_not_bytes() {
    // 바이트로 세면 한글에서 커서가 글자 가운데로 들어가고, 지우면 글자가 깨진다.
    let mut e = edit("한글세션");
    assert_eq!(e.cursor(), 4);
    press(&mut e, Key::Left);
    press(&mut e, Key::Backspace);
    assert_eq!(e.text(), "한글션");
}

#[test]
fn confirmed_ime_text_goes_into_the_buffer_not_the_pane() {
    // ★ 이 경로가 한글의 **유일한** 입구다 — 입력기는 조합이 끝난 글자를 키가 아니라
    //   확정 문자열로 준다. 이 통로가 막히면 편집 중에 친 한글이 셸에 찍힌다.
    let mut e = edit("작업");
    press(&mut e, Key::Home);
    e.insert_str("새 ");
    assert_eq!(e.text(), "새 작업");
    assert_eq!(e.cursor(), 2);
}

#[test]
fn control_characters_never_reach_the_name() {
    // 탭·개행이 이름에 들어가면 상태줄이 깨지고, 그 이름으로 다시 못 찾는다.
    let mut e = edit("");
    e.insert_str("a\tb\nc");
    assert_eq!(e.text(), "abc");
}

#[test]
fn a_modifier_chord_is_swallowed_instead_of_typed_or_leaked() {
    // `Ctrl+C` 가 이름에 `c` 를 넣으면 안 되고, 그렇다고 패널로 새서도 안 된다 —
    // 편집 중 **모든** 키를 먹는 것이 이 표의 계약이다.
    let mut e = edit("dev");
    let out = e.press(Key::Char('c'), Mods { ctrl: true, alt: false });
    assert_eq!(out, SessionEditKey::Consumed);
    assert_eq!(e.text(), "dev");
    // 표에 없는 키도 마찬가지다.
    assert_eq!(press(&mut e, Key::PageDown), SessionEditKey::Consumed);
    assert_eq!(e.text(), "dev");
}

#[test]
fn enter_commits_the_trimmed_name_and_escape_cancels() {
    let mut e = edit("dev");
    e.insert_str("  ");
    assert_eq!(
        press(&mut e, Key::Enter),
        SessionEditKey::Commit("dev".to_owned()),
        "앞뒤 공백이 이름에 남았다"
    );
    let mut e = edit("dev");
    assert_eq!(press(&mut e, Key::Escape), SessionEditKey::Cancel);
    // ⚠ `Shift+Esc` 도 취소다 — 이 키는 평소 패널에 ESC 를 주는 길인데, 편집 중에
    //   그리로 새면 셸이 ESC 를 받고 편집칸은 안 닫힌다.
    let mut e = edit("dev");
    assert_eq!(press(&mut e, Key::ShiftEscape), SessionEditKey::Cancel);
}

#[test]
fn a_click_inside_the_box_only_moves_the_cursor() {
    let mut e = edit("playground");
    e.set_cursor(4);
    assert_eq!(e.cursor(), 4);
    // 범위 밖을 눌러도(프레임 사이에 이름이 줄었다) 끝으로 붙는다.
    e.set_cursor(99);
    assert_eq!(e.cursor(), e.len());
}
