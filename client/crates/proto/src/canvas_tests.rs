//! 격자 합성 오라클 — 분할 배치와 셀별 스타일.

use super::*;
use crate::message::{Row, Run, Style};
use crate::style::{Color, NamedColor};

/// 런 하나짜리 행 하나(= 패널 한 줄).
fn plain(text: &str) -> Vec<Row> {
    vec![vec![Run::plain(text)]]
}

fn styled(text: &str, key: &str, value: serde_json::Value) -> Vec<Row> {
    let mut style = Style::new();
    style.insert(key.into(), value);
    vec![vec![Run {
        text: text.into(),
        style,
    }]]
}

#[test]
fn empty_canvas_is_blank_at_the_requested_size() {
    let canvas = Canvas::new(4, 2);
    assert_eq!(canvas.size(), (4, 2));
    assert_eq!(canvas.text(), "    \n    ");
}

#[test]
fn panes_land_at_the_coordinates_the_layout_gave() {
    // 좌우 분할: 폭 4짜리 두 패널을 0열과 5열에 놓는다.
    let mut canvas = Canvas::new(9, 1);
    canvas.blit_pane(&plain("왼쪽"), 0, 0, 4, 1);
    canvas.blit_pane(&plain("오른"), 5, 0, 4, 1);
    assert_eq!(canvas.row_text(0), "왼쪽 오른");
}

#[test]
fn vertical_split_puts_panes_on_different_rows() {
    let mut canvas = Canvas::new(4, 3);
    canvas.blit_pane(&plain("위"), 0, 0, 4, 1);
    canvas.blit_pane(&plain("아래"), 0, 2, 4, 1);
    assert_eq!(canvas.row_text(0), "위  ");
    assert_eq!(canvas.row_text(1), "    ", "가운데 줄은 비어 있다");
    assert_eq!(canvas.row_text(2), "아래");
}

#[test]
fn a_wide_char_in_the_last_cell_is_written_as_is() {
    // 마지막 한 칸에 넓은 글자가 오면 연속 셀을 둘 자리가 없다. 그때는 그대로 쓰고
    // 한 칸만 전진한다 — 그래서 그 줄만 시각적 폭이 격자보다 넓어진다.
    //
    // 이 동작은 `compose` 모듈과 같고, 그쪽은 파이썬 클라와 해시가 일치한다(적합성
    // 테스트 60표본). 즉 **일부러 맞춘 것**이다. 실제 서버 출력은 이 자리에 넓은 글자를
    // 놓지 않으므로(60표본 전부 폭이 정확히 cols) 현실에서는 나오지 않는다.
    let mut canvas = Canvas::new(3, 1);
    canvas.blit_pane(&plain("아래"), 0, 0, 3, 1);
    assert_eq!(canvas.row_text(0), "아래");
    assert_eq!(
        crate::compose::display_width(&canvas.row_text(0)),
        4,
        "이 경계에서만 폭이 넘친다(파이썬과 동일)"
    );
}

#[test]
fn content_is_clipped_to_the_pane_box_not_the_canvas() {
    // 패널이 자기 폭보다 긴 내용을 보내도 옆 패널을 침범하면 안 된다.
    let mut canvas = Canvas::new(8, 1);
    canvas.blit_pane(&plain("AAAAAAAA"), 0, 0, 3, 1);
    canvas.blit_pane(&plain("BB"), 4, 0, 2, 1);
    assert_eq!(canvas.row_text(0), "AAA BB  ");
}

#[test]
fn blitting_outside_the_canvas_is_ignored_rather_than_panicking() {
    // 창이 줄어드는 순간 서버 좌표가 잠깐 어긋날 수 있다.
    let mut canvas = Canvas::new(4, 2);
    canvas.blit_pane(&plain("XX"), 10, 10, 2, 1);
    canvas.blit_pane(&plain("YY"), 3, 0, 4, 1); // 오른쪽으로 넘침
    assert_eq!(canvas.row_text(0), "   Y");
    assert_eq!(canvas.row_text(1), "    ");
}

#[test]
fn extra_rows_beyond_the_pane_height_are_dropped() {
    let mut canvas = Canvas::new(2, 3);
    let rows: Vec<Row> = vec![plain("a"), plain("b"), plain("c")].concat();
    canvas.blit_pane(&rows, 0, 0, 2, 2);
    assert_eq!(canvas.row_text(0), "a ");
    assert_eq!(canvas.row_text(1), "b ");
    assert_eq!(canvas.row_text(2), "  ", "패널 높이를 넘는 행은 안 그린다");
}

#[test]
fn wide_characters_reserve_the_next_cell() {
    let mut canvas = Canvas::new(5, 1);
    canvas.blit_pane(&plain("가b"), 0, 0, 5, 1);
    // 연속 셀은 줄을 만들 때 빠지므로 글자 수는 4(가, b, 공백, 공백).
    assert_eq!(canvas.row_text(0), "가b  ");
    assert_eq!(
        crate::compose::display_width(&canvas.row_text(0)),
        5,
        "시각적 폭은 격자 폭과 같아야 한다"
    );
}

#[test]
fn styles_travel_with_the_cells() {
    let mut canvas = Canvas::new(6, 1);
    canvas.blit_pane(&styled("빨강", "f", "red".into()), 0, 0, 6, 1);
    let runs = canvas.row_runs(0);
    assert_eq!(runs[0].0, "빨강");
    assert_eq!(runs[0].1.fg, Some(Color::Named(NamedColor::Red)));
    // 안 칠한 나머지는 기본 스타일의 별도 런이다.
    assert!(runs.len() >= 2);
    assert!(runs[1].1.is_default());
}

#[test]
fn adjacent_cells_with_the_same_style_merge_into_one_run() {
    // 셀 하나씩 넘기면 80x24 에 1,920 조각이 생긴다.
    let mut canvas = Canvas::new(10, 1);
    canvas.blit_pane(&plain("abcdefghij"), 0, 0, 10, 1);
    let runs = canvas.row_runs(0);
    assert_eq!(runs.len(), 1, "같은 스타일이면 한 런으로 묶인다");
    assert_eq!(runs[0].0, "abcdefghij");
}

#[test]
fn different_styles_break_the_run() {
    let mut canvas = Canvas::new(6, 1);
    let mut rows = styled("AB", "f", "red".into());
    rows[0].push(Run::plain("CD"));
    canvas.blit_pane(&rows, 0, 0, 6, 1);
    let runs = canvas.row_runs(0);
    assert_eq!(runs[0].0, "AB");
    assert_eq!(runs[0].1.fg, Some(Color::Named(NamedColor::Red)));
    assert!(runs[1].0.starts_with("CD"));
    assert!(runs[1].1.is_default());
}

#[test]
fn a_later_pane_overwrites_an_earlier_one_where_they_overlap() {
    // 겹치면 나중에 그린 쪽이 이긴다(팝업 등). 조용히 섞이지 않는다.
    let mut canvas = Canvas::new(4, 1);
    canvas.blit_pane(&plain("aaaa"), 0, 0, 4, 1);
    canvas.blit_pane(&plain("BB"), 1, 0, 2, 1);
    assert_eq!(canvas.row_text(0), "aBBa");
}
