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

// ── 블록 문자 표(§10-21ⓘ) ────────────────────────────────────────────────────
//
// 이 표는 "글꼴에 맡기지 않고 우리가 그린다"의 재료다. 값이 틀리면 그림이 조용히
// 어긋나므로(마스코트가 그랬다) 뜻을 기계로 고정한다.

#[test]
fn a_full_block_fills_the_whole_cell() {
    let f = block_fills('█').expect("█ 이 표에 없다")[0];
    assert_eq!((f.x0, f.y0, f.x1, f.y1), (0., 0., 1., 1.));
    assert_eq!(f.alpha, 1.);
}

#[test]
fn the_upper_half_block_fills_the_top_not_the_bottom() {
    // ★ 위아래가 뒤집히면 마스코트가 통째로 뒤집힌다 — 그런데 "어긋난다"로만 보여
    //   눈으로는 원인을 못 가른다. 방향을 여기서 못박는다.
    let f = block_fills('▀').unwrap()[0];
    assert_eq!((f.y0, f.y1), (0., 0.5), "▀ 는 칸의 **위** 절반이다");
    let f = block_fills('▄').unwrap()[0];
    assert_eq!((f.y0, f.y1), (0.5, 1.), "▄ 는 칸의 **아래** 절반이다");
}

#[test]
fn the_left_and_right_half_blocks_are_mirror_images() {
    let l = block_fills('▌').unwrap()[0];
    let r = block_fills('▐').unwrap()[0];
    assert_eq!((l.x0, l.x1), (0., 0.5));
    assert_eq!((r.x0, r.x1), (0.5, 1.));
}

#[test]
fn the_eighth_blocks_grow_one_eighth_at_a_time() {
    // ▁▂▃▄▅▆▇█ 은 아래에서 한 조각씩 자란다. 하나라도 어긋나면 막대 그래프가
    // 계단이 아니라 톱니가 된다.
    let bars = ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];
    for (i, ch) in bars.iter().enumerate() {
        let f = block_fills(*ch).unwrap()[0];
        let want = (7 - i) as f32 / 8.;
        assert!(
            (f.y0 - want).abs() < 1e-6,
            "{ch} 의 윗변이 {} 인데 {want} 여야 한다",
            f.y0
        );
        assert_eq!(f.y1, 1., "{ch} 는 바닥까지 채운다");
    }
}

#[test]
fn shades_are_the_whole_cell_at_lower_alpha() {
    // 음영은 **모양이 아니라 진하기**다. 사각형을 줄이면 격자에 구멍이 뚫린다.
    for (ch, want) in [('░', 0.25), ('▒', 0.5), ('▓', 0.75)] {
        let f = block_fills(ch).unwrap()[0];
        assert_eq!((f.x0, f.y0, f.x1, f.y1), (0., 0., 1., 1.), "{ch}");
        assert_eq!(f.alpha, want, "{ch}");
    }
}

#[test]
fn a_letter_is_not_a_block() {
    // 넓게 잡으면 글자가 사각형으로 덮여 사라진다.
    //
    // ⚠ **`▖` 는 2026-08-23 에 이 목록에서 나갔다**(pytmux-177): 사분면이 표에 들어오면서
    //   이제 **블록이 맞다**. 종전에 여기 있던 것은 「사분면을 안 싣기로 한」 그때의
    //   결정을 못박은 것이었지 사분면이 글자라는 뜻이 아니었다.
    for ch in ['a', ' ', '─', '│', '가'] {
        assert!(block_fills(ch).is_none(), "{ch} 를 블록으로 읽었다");
    }
}

#[test]
fn the_quadrants_are_blocks_too() {
    // pytmux-177 — 빠져 있는 동안 이 글자들은 폴백 글꼴로 그려졌고, 그것이 곧 pytmux-55 가
    // 고친 그 결함(진폭이 칸너비의 정수배가 아니라 행마다 어긋난다)이다.
    for ch in ['▖', '▗', '▘', '▝', '▙', '▚', '▛', '▜', '▞', '▟'] {
        let fills = block_fills(ch).unwrap_or_else(|| panic!("{ch} 가 표에 없다"));
        assert!(!fills.is_empty(), "{ch} 의 사각형이 없다");
        // 셋을 채우는 것(`▙▛▜▟`)과 대각(`▚▞`)은 사각형 둘, 나머지는 하나다.
        let want = if "▙▚▛▜▞▟".contains(ch) { 2 } else { 1 };
        assert_eq!(fills.len(), want, "{ch} 의 사각형 수가 다르다: {fills:?}");
    }
}

#[test]
fn a_quadrant_covers_exactly_the_corners_it_names() {
    // 이름이 곧 자리다 — 어긋나면 그림이 뒤집힌 채로 «정렬은 맞게» 그려져 더 헷갈린다.
    let one = |ch| block_fills(ch).unwrap()[0];
    let f = one('▖');   // 좌하
    assert_eq!((f.x0, f.y0, f.x1, f.y1), (0., 0.5, 0.5, 1.));
    let f = one('▝');   // 우상
    assert_eq!((f.x0, f.y0, f.x1, f.y1), (0.5, 0., 1., 0.5));
    // `▚`(좌상+우하)는 대각이라 두 사각형이 서로 안 닿는다.
    let d = block_fills('▚').unwrap();
    assert_eq!((d[0].x0, d[0].y0, d[0].x1, d[0].y1), (0., 0., 0.5, 0.5));
    assert_eq!((d[1].x0, d[1].y0, d[1].x1, d[1].y1), (0.5, 0.5, 1., 1.));
}

#[test]
fn every_fill_stays_inside_its_cell() {
    // 하나라도 칸을 넘으면 이웃 칸을 덮는다 — 격자를 지키려고 옮긴 것이 격자를 깬다.
    for (ch, fills) in BLOCK_FILLS {
        assert!(!fills.is_empty(), "{ch} 에 사각형이 하나도 없다");
        for f in *fills {
            assert!(f.x0 >= 0. && f.y0 >= 0. && f.x1 <= 1. && f.y1 <= 1., "{ch} 가 칸을 넘는다");
            assert!(f.x0 < f.x1 && f.y0 < f.y1, "{ch} 의 사각형이 비었다");
            assert!(f.alpha > 0. && f.alpha <= 1., "{ch} 의 진하기가 범위 밖이다");
        }
    }
}

#[test]
fn the_block_table_has_no_duplicate_characters() {
    // 중복이 있으면 `find` 가 첫 줄만 쓰고 뒤는 조용히 죽는다.
    let mut seen: Vec<char> = BLOCK_FILLS.iter().map(|(c, _)| *c).collect();
    let before = seen.len();
    seen.sort_unstable();
    seen.dedup();
    assert_eq!(before, seen.len(), "블록 표에 중복 문자가 있다");
}

// ── 넓은 글자를 `put` 으로 놓기 (pytmux-17) ────────────────────────────────

/// 플러그인 런이 캔버스에 놓이는 방식 그대로. `session.rs` 의 루프와 **같은 모양**이라야
/// 이 오라클이 그 경로를 잰다(글자마다 `put`, 그 다음 표시폭만큼 건너뛴다).
fn put_run(canvas: &mut Canvas, x: usize, y: usize, text: &str, style: CellStyle) {
    let mut cx = x;
    for ch in text.chars() {
        canvas.put(cx, y, ch, style);
        cx += crate::compose::display_width(&ch.to_string()).max(1);
    }
}

fn green() -> CellStyle {
    CellStyle {
        bg: Some(Color::Named(NamedColor::BrightGreen)),
        ..Default::default()
    }
}

#[test]
fn a_wide_char_run_stays_one_run_so_the_badge_does_not_split() {
    // pytmux-17 의 **가장 작은 재현**: 입력기 배지 `[한]`.
    //
    // 종전에는 `put` 이 `한` 의 뒷칸을 안 건드려, 부르는 쪽이 건너뛴 그 칸이 **배지
    // 스타일이 아닌 채로** 남았다. `row_runs` 는 스타일이 다르면 런을 끊으므로 배지가
    // `[한` / ` ` / `]` 세 조각이 되고, 화면에서는 바탕이 끊긴 `[한 ]` 로 보였다.
    // 실측(1924×1247 캡처): 초록 2.98칸 → 빈틈 1.03칸 → 초록 1칸.
    let mut canvas = Canvas::new(10, 1);
    put_run(&mut canvas, 0, 0, "[한]", green());

    let runs = canvas.row_runs(0);
    let badge: Vec<&(String, CellStyle)> = runs.iter().filter(|(_, s)| *s == green()).collect();
    assert_eq!(
        badge.len(),
        1,
        "배지가 {}조각으로 갈렸다 — 넓은 글자 뒤 칸이 남았다는 뜻이다: {runs:?}",
        badge.len()
    );
    assert_eq!(badge[0].0, "[한]", "{runs:?}");
}

#[test]
fn the_cell_after_a_wide_char_is_a_continuation() {
    // `row_runs` 가 건너뛸 수 있으려면 **연속 셀**로 표시돼야 한다. 공백을 그냥 써 넣는
    // 것으로는 안 된다 — 그러면 그 칸이 진짜 글자가 되어 뒤가 한 칸 밀린다.
    let mut canvas = Canvas::new(6, 1);
    canvas.put(1, 0, '한', green());
    let after = canvas.cell(2, 0).expect("칸이 있어야 한다");
    assert!(after.continuation, "넓은 글자 뒤 칸이 연속 셀이 아니다");
    assert_eq!(after.style, green(), "연속 셀의 스타일이 앞칸과 달라 런이 끊긴다");
    // 화면에 나오는 글자 수는 그대로 — 연속 셀은 런에 안 실린다.
    assert_eq!(canvas.row_text(0), " 한   ");
}

#[test]
fn overwriting_the_tail_of_a_wide_char_does_not_leave_half_a_glyph() {
    // 반대 방향. 이미 놓인 넓은 글자의 **뒤 칸**을 좁은 글자로 덮으면 앞칸은 짝을 잃는다.
    // 그대로 두면 두 칸짜리 글자가 한 칸에 그려져 그 줄이 밀린다.
    let mut canvas = Canvas::new(6, 1);
    canvas.put(0, 0, '한', CellStyle::default());
    canvas.put(1, 0, 'X', CellStyle::default());
    assert_eq!(canvas.row_text(0), " X    ", "앞칸이 반쪽 글자로 남았다");
}

#[test]
fn a_narrow_run_is_unchanged_by_the_wide_char_rule() {
    // 회귀 가드: 좁은 글자만 있는 런(테두리·ASCII)은 종전과 **완전히 같아야** 한다.
    let mut canvas = Canvas::new(8, 1);
    put_run(&mut canvas, 0, 0, "[EN]", green());
    let runs = canvas.row_runs(0);
    let badge: Vec<&(String, CellStyle)> = runs.iter().filter(|(_, s)| *s == green()).collect();
    assert_eq!(badge.len(), 1, "{runs:?}");
    assert_eq!(badge[0].0, "[EN]");
}

// ── 문자소 군집 (pytmux-407) ──────────────────────────────────────────────────



#[test]
fn a_variation_selector_rides_the_letter_instead_of_taking_a_cell() {
    // 두 가지가 한꺼번에 걸려 있던 자리다(pytmux-407):
    //  (1) 그 글자가 **제 칸을 먹어** 뒤따르는 글자가 한 칸씩 밀렸다.
    //  (2) 경고 기호가 선택자와 **갈려** 셰이퍼에 홀로 가 흑백으로 그려졌다.
    let mut c = Canvas::new(10, 1);
    c.blit_pane(&plain("A\u{26a0}\u{fe0f}B"), 0, 0, 10, 1);
    assert_eq!(c.cell(0, 0).unwrap().ch, 'A');
    assert_eq!(c.cell(1, 0).unwrap().ch, '\u{26a0}');
    assert_eq!(c.cell(2, 0).unwrap().ch, 'B', "선택자가 칸을 먹어 줄이 밀렸다");
    // 그 칸의 **글**은 둘이 붙은 한 덩어리다 — 갈라 넘기면 색 이모지가 흑백이 된다.
    assert_eq!(c.cell(1, 0).unwrap().text(), "\u{26a0}\u{fe0f}");
    // 다시 내보낼 때도 잃지 않는다(복사·검색·재직렬화가 이 길을 탄다).
    assert!(
        c.row_text(0).contains("\u{26a0}\u{fe0f}"),
        "다시 내보내며 선택자를 잃었다: {:?}",
        c.row_text(0)
    );
}

#[test]
fn a_zero_width_char_with_nothing_to_ride_is_dropped() {
    // 대조군 — 줄 맨 앞의 폭 0 글자는 얹힐 데가 없다. 제 칸에 그리면 그것이
    // **화면에 없는 글자**가 되고 칸 산수도 어긋난다.
    let mut c = Canvas::new(4, 1);
    c.blit_pane(&plain("\u{fe0f}X"), 0, 0, 4, 1);
    assert_eq!(c.cell(0, 0).unwrap().ch, 'X', "맨 앞 폭 0 글자가 칸을 먹었다");
    assert!(c.cell(0, 0).unwrap().marks.is_empty());
}

#[test]
fn a_mark_rides_the_body_of_a_wide_letter_not_its_continuation() {
    // 넓은 글자는 칸 둘을 쓰고 뒤 칸은 **연속 셀**이다. 얹을 곳은 본체다 —
    // 연속 셀에 얹으면 그 줄을 다시 만들 때 `row_runs` 가 건너뛰어 사라진다.
    let mut c = Canvas::new(6, 1);
    c.blit_pane(&plain("\u{ac00}\u{fe0f}Z"), 0, 0, 6, 1);
    assert_eq!(c.cell(0, 0).unwrap().ch, '\u{ac00}');
    assert_eq!(c.cell(0, 0).unwrap().text(), "\u{ac00}\u{fe0f}", "본체에 안 얹혔다");
    assert!(c.cell(1, 0).unwrap().continuation);
    assert_eq!(c.cell(2, 0).unwrap().ch, 'Z');
    assert!(c.row_text(0).contains("\u{ac00}\u{fe0f}"), "{:?}", c.row_text(0));
}

#[test]
fn writing_a_new_letter_into_a_cell_drops_the_marks_that_rode_the_old_one() {
    // 안 지우면 **엉뚱한 글자에** 변이 선택자가 붙는다 — 그 칸만 다시 그려지는
    // 프레임에서 조용히 일어난다.
    let mut c = Canvas::new(4, 1);
    c.blit_pane(&plain("\u{26a0}\u{fe0f}"), 0, 0, 4, 1);
    assert!(!c.cell(0, 0).unwrap().marks.is_empty());
    c.put_cell(0, 0, 'Q', CellStyle::default());
    assert_eq!(c.cell(0, 0).unwrap().text(), "Q");
}
