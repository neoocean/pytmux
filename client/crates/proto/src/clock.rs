//! 시계 오버레이(패리티 G7b) — `clock` 플러그인의 화면을 재현한다.
//!
//! # 왜 proto 에 있나
//!
//! 합성 캔버스(`SessionState::composite`) 위에 그리는 일이라 그 옆에 있어야 한다.
//! 더 중요한 이유는 **두 뷰가 각자 그리면 시계가 갈린다**는 것이다 — GUI 와 TUI 가 다른
//! 폰트로 다른 자리에 그리면 같은 기능이 아니게 된다. 여기 한 벌만 둔다.
//!
//! # 폰트는 파이썬에서 뽑아 왔다
//!
//! 글리프와 **폰트 고르는 기준**은 `scripts/gen_clock_font_fixture.py` 가
//! `clientutil._CLOCK_FONT`/`_CLOCK_FONT_BIG`/`clock_font_for` 에서 뽑고,
//! `tests/clock_conformance.rs` 가 이 표와 대조한다. 손으로 옮겨 적으면 `▀`/`▄`/`█` 이
//! 한 칸만 어긋나도 숫자가 다르게 보인다.

use crate::style::CellStyle;

/// 지금 시각(`HH:MM:SS`, **지역 시간**). 이벤트 루프가 주기적으로 불러 상태에 넣는다.
///
/// 여기서 시각을 읽고 `composite()` 는 상태에 담긴 문자열만 그린다 — 합성을 시간에 묶으면
/// 같은 상태를 두 번 그려도 결과가 달라져 오라클을 쓸 수 없다.
pub fn now_text() -> String {
    chrono::Local::now().format("%H:%M:%S").to_string()
}

/// 오늘 날짜 `(년, 월, 일)`, **지역 시간**. `now_text` 와 같은 자리에 두는 이유는 둘 다
/// 같은 주기 작업이 부르기 때문이다(시계는 초, 달력은 자정).
pub fn today() -> (i32, u32, u32) {
    use chrono::Datelike;
    let now = chrono::Local::now();
    (now.year(), now.month(), now.day())
}

/// 글자 사이 간격(칸). 파이썬 `clock_font_for` 의 기본값이자 렌더의 호출값이다.
pub const GAP: usize = 1;

/// 반칸 글자로 5픽셀행을 3셀에 욱여넣은 폰트(좁은 패널용).
pub const SMALL_ROWS: usize = 3;
pub const SMALL_COLS: usize = 3;

/// 한 칸 = 한 픽셀인 큰 폰트. 가로를 2칸으로 넓히는 이유는 터미널 셀이 세로로 길어서다
/// (1×1 셀로 두면 숫자가 홀쭉해진다).
pub const BIG_ROWS: usize = 5;
pub const BIG_COLS: usize = 6;

/// `(글자, 줄들)`. 표를 배열로 두는 이유: 이 크레이트는 의존이 가벼워 `HashMap` 상수를
/// 만들 수 없고, 11개짜리 선형 탐색은 프레임당 8글자에 아무 값도 아니다.
type Glyphs = &'static [(char, &'static [&'static str])];

pub const SMALL: Glyphs = &[
    ('0', &["█▀█", "█ █", "▀▀▀"]),
    ('1', &["  █", "  █", "  ▀"]),
    ('2', &["▀▀█", "█▀▀", "▀▀▀"]),
    ('3', &["▀▀█", "▀▀█", "▀▀▀"]),
    ('4', &["█ █", "▀▀█", "  ▀"]),
    ('5', &["█▀▀", "▀▀█", "▀▀▀"]),
    ('6', &["█▀▀", "█▀█", "▀▀▀"]),
    ('7', &["▀▀█", "  █", "  ▀"]),
    ('8', &["█▀█", "█▀█", "▀▀▀"]),
    ('9', &["█▀█", "▀▀█", "▀▀▀"]),
    (':', &[" ▄ ", " ▄ ", "   "]),
];

pub const BIG: Glyphs = &[
    ('0', &["██████", "██  ██", "██  ██", "██  ██", "██████"]),
    ('1', &["    ██", "    ██", "    ██", "    ██", "    ██"]),
    ('2', &["██████", "    ██", "██████", "██    ", "██████"]),
    ('3', &["██████", "    ██", "██████", "    ██", "██████"]),
    ('4', &["██  ██", "██  ██", "██████", "    ██", "    ██"]),
    ('5', &["██████", "██    ", "██████", "    ██", "██████"]),
    ('6', &["██████", "██    ", "██████", "██  ██", "██████"]),
    ('7', &["██████", "    ██", "    ██", "    ██", "    ██"]),
    ('8', &["██████", "██  ██", "██████", "██  ██", "██████"]),
    ('9', &["██████", "██  ██", "██████", "    ██", "██████"]),
    (':', &["      ", "  ██  ", "      ", "  ██  ", "      "]),
];

/// 고른 폰트와 그 치수.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Font {
    pub glyphs: Glyphs,
    pub rows: usize,
    pub cols: usize,
    /// `chars` 글자를 [`GAP`] 간격으로 늘어놓은 전체 폭. 호출부가 중앙 정렬에 쓴다.
    pub width: usize,
    pub big: bool,
}

/// 가용 공간에 맞는 폰트(파이썬 `clock_font_for` 와 같은 판정).
///
/// 기준이 갈리면 **같은 크기 패널에서 두 클라가 다른 크기의 시계를 그린다** — 픽스처가
/// 경계값(폭 55·높이 5)을 못박는다.
pub fn font_for(avail_w: usize, avail_h: usize, chars: usize) -> Font {
    let width_of = |cols: usize| chars * cols + chars.saturating_sub(1) * GAP;
    let big_w = width_of(BIG_COLS);
    if avail_w >= big_w && avail_h >= BIG_ROWS {
        return Font { glyphs: BIG, rows: BIG_ROWS, cols: BIG_COLS, width: big_w, big: true };
    }
    Font {
        glyphs: SMALL,
        rows: SMALL_ROWS,
        cols: SMALL_COLS,
        width: width_of(SMALL_COLS),
        big: false,
    }
}

fn glyph(font: &Font, c: char) -> Option<&'static [&'static str]> {
    font.glyphs.iter().find(|(g, _)| *g == c).map(|(_, rows)| *rows)
}

/// 오버레이 뒤에 남는 셀 하나를 어둡게.
///
/// **파이썬과 갈리는 자리** — 저쪽은 실제 전경/배경 RGB 를 검정 쪽으로 0.55 블렌드한다.
/// 우리는 팔레트 색의 RGB 를 모른다(테마가 정한다). 그래서 RGB 는 같은 비율로 블렌드하고,
/// 팔레트 색은 **밝은 쪽을 보통 쪽으로 내린다**. 둘 다 `bold` 를 푼다 — 많은 터미널이
/// bold 를 밝게 그려서 안 풀면 딤이 상쇄된다(파이썬이 실색 블렌드를 쓰는 이유와 같다).
pub fn darken(style: &CellStyle) -> CellStyle {
    crate::style::darken(style, DIM_RATIO)
}

/// 오버레이 뒤 화면을 흐리게 하는 세기. 파이썬 `_darken_style` 의 기본 비율이다.
pub const DIM_RATIO: f32 = 0.55;

// `draw` 는 **여기 없다**(2026-08-02, 설계 Tier B · P3). 시계 그림은 이제 서버가
// 런으로 준다(`plugin_cells`) — 우리가 그리면 규칙이 두 벌이 되고, 두 벌은 갈린다.
//
// 남은 것들이 왜 남았나:
//   `darken`      — 딤은 런으로 못 나른다(있는 셀을 바꾸는 일). 셀 기여가 쓴다.
//   `now_text`    — 알림 이력의 시각(`Notice::at`).
//   `today`       — 달력이 '오늘'을 강조하는 데 쓴다.
//   폰트 표·`font_for` — **달력이 직접 부른다**(`calendar.rs` 의 큰 날짜 글자).
//                  그래서 표와 그 짝(`gen_clock_font_fixture.py`·`clock_conformance.rs`)은
//                  달력이 같은 길로 올 때 함께 간다.

#[cfg(test)]
#[path = "clock_tests.rs"]
mod tests;
