//! 팔레트 칸 나누기·접기 오라클(§10-21ⓞ·ⓗ⑶).

use super::*;

#[test]
fn a_name_without_options_keeps_the_second_column_empty() {
    assert_eq!(split_name("kill-pane"), ("kill-pane", ""));
}

#[test]
fn the_first_space_is_the_boundary() {
    // 정본의 표가 옵션을 이름 안에 품는다 — 그 규칙이 여기 한 벌로 있어야 두 클라가
    // 같은 자리에서 자른다.
    assert_eq!(split_name("split-window -h"), ("split-window", "-h"));
    assert_eq!(split_name("resize-pane -Z"), ("resize-pane", "-Z"));
}

#[test]
fn multi_word_options_stay_one_piece() {
    // `-t next` 를 더 쪼개려면 "무엇이 값인가"를 알아야 하는데 그 지식은 서버의 표에 있다.
    assert_eq!(split_name("select-pane -t next"), ("select-pane", "-t next"));
}

#[test]
fn short_text_is_not_wrapped() {
    assert_eq!(wrap("짧다", 20), vec!["짧다".to_owned()]);
}

#[test]
fn wrapping_breaks_at_word_boundaries() {
    let lines = wrap("one two three four", 9);
    assert_eq!(lines, vec!["one two".to_owned(), "three".to_owned(), "four".to_owned()]);
}

#[test]
fn every_wrapped_line_fits_the_width() {
    // ★ 이것이 이 함수의 계약이다 — 한 줄이라도 넘치면 판을 밀고 나간다(접기의 목적이
    //   무너진다).
    use unicode_width::UnicodeWidthChar;
    let text = "현재 패널을 이번 달 달력으로 덮기(토글, 좌우로 이전·다음 달)";
    for cols in [6, 10, 17, 31] {
        for line in wrap(text, cols) {
            let w: usize = line.chars().map(|c| c.width().unwrap_or(0)).sum();
            assert!(w <= cols, "폭 {cols} 인데 {w} 칸이 나왔다: {line:?}");
        }
    }
}

#[test]
fn hangul_counts_two_cells_not_one() {
    // 글자 수로 세면 절반에서 넘친다 — 그 실수가 곧 오른쪽 끝이 들쭉날쭉해지는 이유다.
    assert_eq!(wrap("가나다라", 4), vec!["가나".to_owned(), "다라".to_owned()]);
}

#[test]
fn a_word_longer_than_the_width_is_broken_inside() {
    let lines = wrap("supercalifragilistic", 7);
    assert!(lines.len() > 1, "안 끊었다: {lines:?}");
    assert!(lines.iter().all(|l| l.chars().count() <= 7), "{lines:?}");
    assert_eq!(lines.concat(), "supercalifragilistic", "글자를 흘렸다");
}

#[test]
fn wrapping_keeps_every_character() {
    // 접기가 글을 먹으면 설명이 조용히 사라진다.
    let text = "one two three four five six";
    assert_eq!(wrap(text, 11).join(" "), text);
}

#[test]
fn zero_width_asks_for_nothing() {
    assert!(wrap("무엇이든", 0).is_empty());
}
