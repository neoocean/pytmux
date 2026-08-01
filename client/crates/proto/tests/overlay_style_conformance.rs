//! 교차구현 적합성 — 오버레이(시계·달력)의 **색 관습**이 정본과 같은가.
//!
//! 대조 문서(§13)가 잡은 어긋남이다: 시계 숫자와 달력 제목이 우리는 청록이고 정본은
//! 초록, 달력의 오늘은 우리가 주황 **글자**인데 정본은 초록 **배경**에 검은 글자였다.
//! 같은 그림에서 눈이 찾는 색이 달랐다.
//!
//! # 값을 대조하지 않는다
//!
//! 정본은 Textual 테마 변수(`success`)로 칠하고 `#4EBF71` 은 그 **폴백**일 뿐이다. 우리는
//! 터미널 이름색을 쓰므로 값을 옮기면 어두운 테마에서만 맞는 색이 된다. 옮길 수 있는 것은
//! **구조**다 — 어느 자리들이 같은 변수를 쓰는가, 강조가 글자색인가 배경인가, 굵은가.
//!
//! 픽스처는 정본 소스에서 뽑았다: `python3 scripts/gen_overlay_styles.py`.

use std::collections::BTreeMap;

use proto::session::overlay_style;
use proto::style::{Color, NamedColor};
use serde::Deserialize;

#[derive(Deserialize)]
struct Element {
    #[serde(default)]
    fg_var: Option<String>,
    #[serde(default)]
    fg_literal: Option<String>,
    #[serde(default)]
    bg_var: Option<String>,
    bold: bool,
}

#[derive(Deserialize)]
struct Fixture {
    elements: BTreeMap<String, Element>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/overlay_styles.json"))
        .expect("픽스처를 읽을 수 없다")
}

fn el<'a>(fx: &'a Fixture, key: &str) -> &'a Element {
    fx.elements.get(key).unwrap_or_else(|| panic!("픽스처에 '{key}' 가 없다"))
}

/// 정본이 **한 변수로 칠하는 자리들**을 우리도 한 색으로 칠하나.
///
/// 이것이 이 슬라이스의 알맹이다 — 시계와 달력이 따로 있는 코드라, 한쪽만 고치면 두
/// 오버레이가 같은 화면에서 다른 색이 된다(실제로 그랬다).
#[test]
fn the_places_canon_paints_with_one_theme_variable_share_one_color() {
    let fx = fixture();
    assert!(!fx.elements.is_empty(), "픽스처가 비었다 — 통과가 아니라 고장이다");

    let clock = el(&fx, "clock.digit");
    let title = el(&fx, "calendar.title");
    let big = el(&fx, "calendar.big_today");
    let today = el(&fx, "calendar.today");
    // 정본이 같은 변수를 쓴다는 것부터 확인한다(정본이 갈라지면 이 테스트의 전제가 깨진다).
    let var = clock.fg_var.as_deref().expect("clock.digit 은 테마 변수로 칠한다");
    for (name, other) in [
        ("calendar.title", title.fg_var.as_deref()),
        ("calendar.big_today", big.fg_var.as_deref()),
        ("calendar.today(bg)", today.bg_var.as_deref()),
    ] {
        assert_eq!(other, Some(var), "정본에서 {name} 이 시계와 다른 변수가 됐다");
    }

    // 우리 쪽: 그 네 자리가 전부 같은 색이라야 한다.
    let cal = overlay_style::calendar();
    assert_eq!(overlay_style::clock_digit().fg, Some(overlay_style::SUCCESS));
    assert_eq!(cal.title.fg, Some(overlay_style::SUCCESS));
    assert_eq!(cal.big_today.fg, Some(overlay_style::SUCCESS));
    assert_eq!(cal.today.bg, Some(overlay_style::SUCCESS));
}

/// 강조가 **글자색인가 배경인가**가 정본과 같은가.
///
/// 격자의 오늘만 배경을 깔고 큰 달력의 오늘은 글자색이다 — 블록 폰트는 칸이 커서 배경을
/// 깔면 화면의 절반이 색 덩어리가 된다. 이 갈림을 놓치면 큰 달력이 정본과 딴판이 된다.
#[test]
fn the_highlight_is_a_background_only_where_canon_makes_it_one() {
    let fx = fixture();
    let cal = overlay_style::calendar();

    let today = el(&fx, "calendar.today");
    assert_eq!(today.fg_literal.as_deref(), Some("black"), "정본이 바뀌었다");
    assert!(today.bg_var.is_some(), "정본 today 는 배경 강조다");
    assert_eq!(cal.today.fg, Some(Color::Named(NamedColor::Black)));
    assert!(cal.today.bg.is_some(), "격자의 오늘이 배경 강조가 아니다");

    let big = el(&fx, "calendar.big_today");
    assert!(big.bg_var.is_none(), "정본 big_today 는 글자색 강조다");
    assert!(cal.big_today.bg.is_none(), "큰 달력의 오늘에 배경을 깔았다");

    // 날짜는 정본에서 `foreground` — 특별한 색이 아니다 = 우리 기본값.
    assert_eq!(el(&fx, "calendar.day").fg_var.as_deref(), Some("foreground"));
    assert_eq!(cal.day.fg, None, "날짜에 색을 칠했다(정본은 기본 전경)");
}

/// 굵기까지 정본과 같은가 — 색만 맞추고 굵기를 놓치면 강조의 세기가 달라진다.
#[test]
fn boldness_matches_canon() {
    let fx = fixture();
    let cal = overlay_style::calendar();
    for (key, ours) in [
        ("clock.digit", overlay_style::clock_digit().bold),
        ("calendar.title", cal.title.bold),
        ("calendar.today", cal.today.bold),
        ("calendar.big_today", cal.big_today.bold),
        ("calendar.day", cal.day.bold),
    ] {
        assert_eq!(ours, el(&fx, key).bold, "{key} 의 굵기가 정본과 다르다");
    }
}
