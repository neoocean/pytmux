//! 시계 오버레이가 **어디에 무엇을 그리나**(패리티 G7b).
//!
//! 글리프 자체는 픽스처 대조가 지킨다(`tests/clock_conformance.rs`). 여기서 지키는 것은
//! 그리는 **자리**와 뒤 화면을 다루는 방식이다.

use super::*;
use crate::style::{CellStyle, Color, NamedColor};

fn digit_style() -> CellStyle {
    CellStyle {
        fg: Some(Color::Named(NamedColor::BrightCyan)),
        ..Default::default()
    }
}

/// 캔버스를 글자만 뽑아 줄 목록으로.
fn lines(canvas: &Canvas) -> Vec<String> {
    let (cols, rows) = canvas.size();
    (0..rows)
        .map(|y| {
            (0..cols)
                .map(|x| canvas.cell(x, y).map_or(' ', |c| c.ch))
                .collect()
        })
        .collect()
}

fn filled(canvas: &Canvas, ch: char) -> Canvas {
    let (cols, rows) = canvas.size();
    let mut out = Canvas::new(cols, rows);
    for y in 0..rows {
        for x in 0..cols {
            if let Some(cell) = out.cell_mut(x, y) {
                cell.ch = ch;
            }
        }
    }
    out
}

#[test]
fn the_clock_lands_in_the_middle_of_the_pane() {
    let mut canvas = Canvas::new(80, 24);
    draw(&mut canvas, (0, 0, 80, 24), "12:34:56", digit_style());
    let drawn: Vec<usize> = lines(&canvas)
        .iter()
        .enumerate()
        .filter(|(_, l)| l.trim().chars().any(|c| c != ' '))
        .map(|(i, _)| i)
        .collect();
    // 큰 폰트 5행이 24행 한가운데(9..14)에 온다.
    assert_eq!(drawn, vec![9, 10, 11, 12, 13], "{:?}", drawn);
}

#[test]
fn a_small_pane_gets_the_small_font() {
    let mut canvas = Canvas::new(40, 10);
    draw(&mut canvas, (0, 0, 40, 10), "12:34:56", digit_style());
    let drawn = lines(&canvas)
        .iter()
        .filter(|l| l.chars().any(|c| c != ' '))
        .count();
    assert_eq!(drawn, SMALL_ROWS, "작은 폰트 3행이 아니다");
}

#[test]
fn a_pane_too_small_for_any_font_still_shows_the_time() {
    // ★ 아무것도 안 그리면 사용자는 `prefix t` 가 안 먹은 줄 안다.
    let mut canvas = Canvas::new(12, 2);
    draw(&mut canvas, (0, 0, 12, 2), "12:34:56", digit_style());
    let text: String = lines(&canvas).join("");
    assert!(text.contains("12:34:56"), "{text:?}");
}

#[test]
fn the_clock_stays_inside_its_pane() {
    // 두 패널로 나뉜 화면에서 왼쪽만 시계를 켰다 — 오른쪽은 손대면 안 된다.
    let mut canvas = filled(&Canvas::new(80, 24), 'x');
    draw(&mut canvas, (0, 0, 40, 24), "12:34:56", digit_style());
    for row in lines(&canvas) {
        let right: String = row.chars().skip(40).collect();
        assert!(
            right.chars().all(|c| c == 'x'),
            "오른쪽 패널을 건드렸다: {right:?}"
        );
    }
}

#[test]
fn what_was_behind_is_dimmed_not_erased() {
    // 파이썬과 같다 — 시계를 켠 채로도 그 패널에서 무슨 일이 벌어지는지 어렴풋이 보인다.
    let mut canvas = filled(&Canvas::new(30, 8), 'x');
    draw(&mut canvas, (0, 0, 30, 8), "12:34:56", digit_style());
    let text: String = lines(&canvas).join("");
    assert!(text.contains('x'), "뒤 화면이 지워졌다");
}

#[test]
fn dimming_drops_bold() {
    // 많은 터미널이 bold 를 밝게 그려서, 안 풀면 딤이 상쇄된다.
    let bright = CellStyle {
        fg: Some(Color::Named(NamedColor::BrightWhite)),
        bold: true,
        ..Default::default()
    };
    let dimmed = darken(&bright);
    assert!(!dimmed.bold);
    assert_ne!(dimmed.fg, bright.fg, "밝은 흰색이 그대로 남았다");
}

#[test]
fn dimming_keeps_the_other_attributes() {
    // 밑줄·기울임까지 잃으면 뒤 화면이 원래 무엇이었는지 알 수 없다.
    let fancy = CellStyle {
        fg: Some(Color::Rgb { r: 200, g: 100, b: 50 }),
        italic: true,
        underline: true,
        ..Default::default()
    };
    let dimmed = darken(&fancy);
    assert!(dimmed.italic && dimmed.underline);
    assert_eq!(dimmed.fg, Some(Color::Rgb { r: 90, g: 45, b: 22 }));
}

#[test]
fn an_unknown_character_keeps_its_slot() {
    // 모르는 글자를 건너뛰면 그 뒤 글자가 통째로 밀려 시계가 어긋난다. `?` 를 섞어도
    // 뒤따르는 숫자의 자리가 안 변해야 한다.
    let mut with_gap = Canvas::new(80, 24);
    draw(&mut with_gap, (0, 0, 80, 24), "1?:34:56", digit_style());
    let mut without = Canvas::new(80, 24);
    draw(&mut without, (0, 0, 80, 24), "12:34:56", digit_style());
    // 마지막 글자(6)가 그려진 **칸**이 두 판에서 같아야 한다.
    // ★ 바이트 자리가 아니라 칸이다 — 블록 글자(`█`)는 여러 바이트라, `rfind` 를 그대로
    //   쓰면 앞 글자가 비었는지에 따라 값이 흔들린다(첫 판에서 이 오라클이 그렇게 틀렸다).
    let last_col = |canvas: &Canvas| {
        lines(canvas)
            .iter()
            .filter_map(|l| {
                l.chars()
                    .enumerate()
                    .filter(|(_, c)| *c != ' ')
                    .map(|(i, _)| i)
                    .last()
            })
            .max()
    };
    assert_eq!(last_col(&with_gap), last_col(&without));
}
