//! 시계 폰트가 파이썬 것과 같은가 — 픽스처 대조(패리티 G7b).
//!
//! # 왜 표로 묶는가
//!
//! 글리프는 `▀`/`▄`/`█` 로 그린 그림이라 **한 칸만 어긋나도 숫자가 다르게 보인다**. 손으로
//! 옮겨 적으면 그 어긋남을 사람 눈으로 잡아야 하고, 두 클라를 나란히 놓기 전에는 안 보인다.
//!
//! 폰트 **고르는 기준**도 같이 묶는다. 그게 갈리면 같은 크기 패널에서 두 클라가 다른 크기의
//! 시계를 그린다 — 기능은 같은데 화면이 다른, 가장 설명하기 어려운 종류의 차이다.

use std::collections::BTreeMap;

use proto::clock;
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    small: FontSpec,
    big: FontSpec,
    gap: usize,
    picks: Vec<Pick>,
}

#[derive(Deserialize)]
struct FontSpec {
    rows: usize,
    cols: usize,
    glyphs: BTreeMap<String, Vec<String>>,
}

#[derive(Deserialize)]
struct Pick {
    avail_w: usize,
    avail_h: usize,
    chars: usize,
    big: bool,
    rows: usize,
    cols: usize,
    width: usize,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/clock_font.json")).expect("픽스처를 읽을 수 없다")
}

fn ours(glyphs: &'static [(char, &'static [&'static str])]) -> BTreeMap<String, Vec<String>> {
    glyphs
        .iter()
        .map(|(c, rows)| {
            (
                c.to_string(),
                rows.iter().map(|r| (*r).to_owned()).collect(),
            )
        })
        .collect()
}

#[test]
fn both_fonts_match_the_python_glyphs() {
    let fx = fixture();
    assert_eq!(ours(clock::SMALL), fx.small.glyphs, "작은 폰트가 다르다");
    assert_eq!(ours(clock::BIG), fx.big.glyphs, "큰 폰트가 다르다");
}

#[test]
fn the_font_sizes_match() {
    let fx = fixture();
    assert_eq!((clock::SMALL_ROWS, clock::SMALL_COLS), (fx.small.rows, fx.small.cols));
    assert_eq!((clock::BIG_ROWS, clock::BIG_COLS), (fx.big.rows, fx.big.cols));
    assert_eq!(clock::GAP, fx.gap);
}

#[test]
fn every_glyph_is_a_rectangle() {
    // 줄 길이가 들쭉날쭉하면 그 글자 뒤가 통째로 밀린다.
    for (glyphs, cols) in [
        (clock::SMALL, clock::SMALL_COLS),
        (clock::BIG, clock::BIG_COLS),
    ] {
        for (c, rows) in glyphs {
            for line in *rows {
                assert_eq!(line.chars().count(), cols, "{c:?} 의 줄 폭이 다르다: {line:?}");
            }
        }
    }
}

#[test]
fn we_pick_the_same_font_at_the_same_sizes() {
    // ★ 경계 한 칸이 여기서 갈린다(픽스처가 폭 55·높이 5 를 노려 뽑는다).
    for pick in fixture().picks {
        let font = clock::font_for(pick.avail_w, pick.avail_h, pick.chars);
        assert_eq!(
            (font.big, font.rows, font.cols, font.width),
            (pick.big, pick.rows, pick.cols, pick.width),
            "{}x{} 에서 폰트 선택이 갈렸다",
            pick.avail_w,
            pick.avail_h
        );
    }
}

#[test]
fn the_fixture_is_not_empty() {
    // 빈 픽스처는 "전부 통과"처럼 보인다 — 라이선스 게이트에서 이미 한 번 밟은 부류다.
    let fx = fixture();
    assert!(fx.small.glyphs.len() >= 11 && fx.picks.len() >= 5);
}

#[test]
fn the_clock_only_needs_digits_and_a_colon() {
    // `HH:MM:SS` 를 그릴 수 있는가 — 글자 하나가 빠지면 그 자리가 빈 칸으로 남는다.
    for c in "0123456789:".chars() {
        assert!(
            clock::SMALL.iter().any(|(g, _)| *g == c),
            "작은 폰트에 {c:?} 가 없다"
        );
        assert!(clock::BIG.iter().any(|(g, _)| *g == c), "큰 폰트에 {c:?} 가 없다");
    }
}
