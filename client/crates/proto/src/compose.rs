//! 와이어의 행 런을 셀 격자로 합성한다.
//!
//! # 계약
//!
//! 이 모듈은 pytmux 파이썬 클라이언트와 **글자 하나까지 같은 결과**를 내야 한다. 서버가
//! 보내는 것은 이미 렌더된 행이고(행마다 `[텍스트, 스타일]` 런의 목록), 클라가 하는 일은
//! 그 런들을 폭 `cols` 의 셀 격자에 앉히는 것뿐이다.
//!
//! 규칙은 세 줄이다:
//!
//! 1. 런의 글자를 왼쪽부터 순서대로 셀에 넣는다. `cols` 를 넘으면 버린다.
//! 2. **넓은 글자(폭 2)는 다음 셀을 연속 셀로 표시**하고 두 칸 전진한다.
//! 3. 줄을 문자열로 만들 때 연속 셀은 **빼고** 이어 붙인다.
//!
//! 2·3 이 함께 지켜져야 "줄의 시각적 폭 == cols" 가 성립한다. 한쪽만 하면 한글이 있는
//! 줄에서 뒤쪽 글자가 한 칸씩 밀린다.
//!
//! # 폭 판정
//!
//! 파이썬 쪽은 `2 if wcwidth(ch) == 2 else 1` 이다. 폭 0(결합 문자)도, 폭 -1(비출력)도
//! **1 로 떨어진다** — 넓은 것만 2 다. 여기서도 같게 한다. `unicode-width` 가 `None`
//! (제어문자)이나 `Some(0)`(결합)을 주는 경우가 그 자리다.
//!
//! 이 판정이 파이썬의 `wcwidth` 와 갈리면 격자가 어긋나는데, 그건 적합성 테스트
//! (`tests/conformance.rs`)가 60개 표본으로 잡는다.

use unicode_width::UnicodeWidthChar;

use crate::message::Row;

/// 넓은 글자가 차지한 자리를 표시하는 값. 줄을 만들 때 걸러낸다.
const CONTINUATION: char = '\u{0}';

/// 모호폭을 넓게 볼까(설정 `ambiguous-width`). **프로세스 전역**이다.
///
/// # 왜 전역인가
///
/// 폭 판정은 합성·캔버스·꼬리줄 등 **아주 많은 자리**에서 불린다. 인자로 흘리면 그
/// 경로를 전부 고쳐야 하고, 한 곳만 빠뜨리면 그 줄만 어긋난다(증상은 "가끔 화면이
/// 밀린다"). 파이썬도 같은 이유로 모듈 전역(`cellwidth._AMBIG_WIDE`)을 쓴다.
///
/// 값은 기동 때 설정에서 한 번 넣고, 설정 화면에서 바뀌면 다시 넣는다.
static AMBIGUOUS_WIDE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// 모호폭 모드를 켜고 끈다(설정 `ambiguous-width` = `wide`/`narrow`).
pub fn set_ambiguous_wide(on: bool) {
    AMBIGUOUS_WIDE.store(on, std::sync::atomic::Ordering::Relaxed);
}

/// 지금 모호폭 모드.
pub fn ambiguous_wide() -> bool {
    AMBIGUOUS_WIDE.load(std::sync::atomic::Ordering::Relaxed)
}

/// 한 글자가 차지하는 칸 수. 모호폭은 위 설정을 따른다.
pub fn char_cells(ch: char) -> usize {
    char_cells_in(ch, ambiguous_wide())
}

/// 모호폭(East Asian Width = `A`)을 **넓게** 볼지 정하는 판(설정 `ambiguous-width`).
///
/// # 왜 설정인가
///
/// `─`·`│`·`°` 같은 글자는 폰트·로케일에 따라 한 칸이기도 두 칸이기도 하다. 서버와 클라의
/// 판정이 갈리면 **그 줄 전체가 밀린다** — 화면이 깨지는 가장 흔한 원인이다. 그래서
/// 파이썬도 이걸 설정으로 두고 양쪽이 같은 값을 쓰게 한다.
///
/// 판정은 `unicode-width` 의 `width_cjk`(CJK 문맥 = 모호폭 2)와 `width` 의 차이로 얻는다
/// — 표를 우리가 다시 적으면 유니코드 판이 오를 때마다 갈린다.
pub fn char_cells_in(ch: char, ambiguous_wide: bool) -> usize {
    if ch.width() == Some(2) {
        return 2;
    }
    if ambiguous_wide && ch.width_cjk() == Some(2) {
        return 2;
    }
    1
}

/// 이 글자가 격자에서 **밀어내는 칸수**(advance). 폭 0 글자는 `0` 이다.
///
/// # 왜 [`char_cells`] 와 따로 있나 (pytmux-389)
///
/// [`char_cells`] 는 *"이 글자를 몇 칸으로 그리나"* 이고 **파이썬
/// `cellwidth.char_cells`(`2 if wcwidth==2 else 1`)와 글자 하나까지 같아야 한다**는
/// 계약이 있다(이 파일 머리말 · `tests/conformance.rs` 가 표본 60개로 잰다). 그래서
/// 거기서는 폭 0 도 `1` 로 떨어진다 — 그 값을 고치면 다른 자리가 조용히 어긋난다.
///
/// 그런데 **자리를 나눌 때** 묻는 것은 다른 질문이다: *"이 글자가 다음 글자를 몇 칸
/// 밀어내나"*. 변이 선택자(`U+FE0E`·`U+FE0F`)·ZWJ(`U+200D`)·결합 표시는 **앞 글자에
/// 얹히는** 것이라 아무도 밀지 않는다. 그 둘을 같은 함수로 물으면 그 글자가 든 줄이
/// **한 칸씩 오른쪽으로 밀린다** — 실측 2026-08-24(맥 · `--frame-dump`):
///
/// ```text
/// |A|  ← `|` 가 3번째 칸 (옳다)
/// |⚠ | ← `|` 가 4번째 칸 — 한 칸 밀렸다 (U+FE0F 가 칸을 하나 먹었다)
/// ```
///
/// ⚠ 제어문자는 `width()` 가 `None` 이라 여기서 **0 이 아니다** — 종전대로 한 칸으로
/// 센다(폭을 알 수 없는 것과 폭이 0 인 것은 다르다).
pub fn char_advance(ch: char) -> usize {
    if ch.width() == Some(0) {
        return 0;
    }
    char_cells(ch)
}

/// 행 런들을 폭 `cols` 의 셀 격자로 합성한다.
///
/// 반환값의 각 원소가 화면 한 줄이고, **시각적 폭이 정확히 `cols`** 다(문자 수가 아니라).
pub fn compose_rows(rows: &[Row], cols: usize) -> Vec<String> {
    rows.iter().map(|row| compose_row(row, cols)).collect()
}

fn compose_row(row: &Row, cols: usize) -> String {
    let mut cells = vec![' '; cols];
    let mut cx = 0usize;

    for run in row {
        for ch in run.text.chars() {
            if cx >= cols {
                break;
            }
            cells[cx] = ch;
            if char_cells(ch) == 2 && cx + 1 < cols {
                cells[cx + 1] = CONTINUATION;
                cx += 2;
            } else {
                cx += 1;
            }
        }
    }

    cells.into_iter().filter(|c| *c != CONTINUATION).collect()
}

/// 줄의 **시각적 폭**. 합성 결과가 계약(`== cols`)을 지키는지 확인할 때 쓴다.
pub fn display_width(line: &str) -> usize {
    line.chars().map(char_cells).sum()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::message::Run;

    fn run(text: &str) -> Run {
        Run::plain(text)
    }

    fn compose(runs: Vec<Run>, cols: usize) -> String {
        compose_row(&runs, cols)
    }

    #[test]
    fn narrow_text_fills_cells_left_to_right() {
        assert_eq!(compose(vec![run("abc")], 5), "abc  ");
    }

    #[test]
    fn runs_are_concatenated_in_order() {
        assert_eq!(compose(vec![run("ab"), run("cd")], 6), "abcd  ");
    }

    #[test]
    fn wide_characters_consume_two_cells() {
        // "가" 는 두 칸이므로 폭 5 짜리 줄에 "가" + "ab" 면 남는 칸은 하나다.
        let line = compose(vec![run("가ab")], 5);
        assert_eq!(line, "가ab ");
        assert_eq!(display_width(&line), 5, "시각적 폭은 cols 와 같아야 한다");
    }

    #[test]
    fn line_width_is_cols_not_char_count() {
        // 이 계약이 깨지면 한글이 있는 줄에서 뒤쪽이 밀린다.
        let line = compose(vec![run("가나다")], 8);
        assert_eq!(line.chars().count(), 5, "글자 수는 cols 보다 작다");
        assert_eq!(display_width(&line), 8);
    }

    #[test]
    fn overflowing_text_is_dropped() {
        assert_eq!(compose(vec![run("abcdef")], 3), "abc");
    }

    #[test]
    fn wide_character_at_the_right_edge_does_not_write_past_the_end() {
        // 마지막 한 칸에 넓은 글자가 오면 연속 셀을 둘 자리가 없다. 파이썬은 그대로
        // 한 칸에 넣고 한 칸만 전진한다 — 밖으로 쓰지 않는 것이 요점이다.
        let line = compose(vec![run("ab가")], 3);
        assert_eq!(line.chars().count(), 3);
    }

    #[test]
    fn combining_and_control_characters_count_as_one() {
        // 파이썬은 폭 0·-1 을 전부 1 로 떨어뜨린다. 여기서도 같아야 한다.
        assert_eq!(char_cells('\u{0301}'), 1, "결합 문자");
        assert_eq!(char_cells('a'), 1);
        assert_eq!(char_cells('가'), 2);
    }

    #[test]
    fn empty_row_becomes_blank_line_of_cols_width() {
        let line = compose_row(&Vec::new(), 4);
        assert_eq!(line, "    ");
        assert_eq!(display_width(&line), 4);
    }
}

#[cfg(test)]
mod ambiguous_tests {
    use super::*;

    #[test]
    fn ambiguous_chars_widen_only_in_wide_mode() {
        // ★ `─`(EAW=A)는 폰트·로케일에 따라 한 칸이기도 두 칸이기도 하다. 서버와 클라의
        // 판정이 갈리면 **그 줄 전체가 밀린다**.
        assert_eq!(char_cells_in('─', false), 1);
        assert_eq!(char_cells_in('─', true), 2);
        assert_eq!(char_cells_in('°', false), 1);
        assert_eq!(char_cells_in('°', true), 2);
    }

    #[test]
    fn the_unambiguous_widths_do_not_move() {
        // 모호폭 모드가 다른 글자까지 건드리면 안 된다.
        for ch in ['a', ' ', '한', '漢', '\u{0301}'] {
            assert_eq!(
                char_cells_in(ch, false),
                char_cells_in(ch, true),
                "{ch:?} 의 폭이 모드에 따라 달라졌다"
            );
        }
        assert_eq!(char_cells_in('한', false), 2);
        assert_eq!(char_cells_in('a', true), 1);
    }

    #[test]
    fn the_global_switch_moves_char_cells() {
        // 전역이라 순서가 있다 — 끝에 되돌린다(다른 테스트가 좁은 모드를 기대한다).
        set_ambiguous_wide(true);
        assert_eq!(char_cells('─'), 2);
        set_ambiguous_wide(false);
        assert_eq!(char_cells('─'), 1);
    }
}
