//! 셀 스타일 — 서버가 보내는 축약 키를 해석한다.
//!
//! # 와이어 모양
//!
//! 스타일은 JSON 객체이고 **기본값인 속성은 키 자체가 없다**:
//!
//! | 키 | 뜻 |
//! |----|----|
//! | `f` | 전경색 |
//! | `b` | 배경색 |
//! | `bo` | 굵게 |
//! | `it` | 기울임 |
//! | `un` | 밑줄 |
//! | `rv` | 반전 |
//! | `st` | 취소선 |
//!
//! 색은 이름(`red`, `bright_cyan`) 또는 16진수(`#cd0000`)로 온다.
//!
//! # 이름을 추측하면 안 된다
//!
//! 서버의 색 이름은 pyte 계보라 ANSI 통념과 다르다. 실측으로 확인된 두 가지:
//!
//! - **SGR 93/103(밝은 노랑)은 `bright_brown`** 이다. `bright_yellow` 가 아니다.
//! - **SGR 105(밝은 마젠타 배경)는 `bfightmagenta`** — 오타처럼 보이지만 **의도된 것**이다.
//!   pytmux 의 `vtconst.py:117` 에 "원 pyte 오타 보존 — 렌더 바이트 동일성"이라고
//!   주석까지 달려 있다. 이름을 '고치면' 그 배경색이 조용히 사라진다.
//!
//! 전수 표는 `tests/fixtures/styles.json` 에 서버에서 뽑아 두었고,
//! `tests/style_conformance.rs` 가 이 파서가 그 표를 전부 아는지 확인한다.
//!
//! # 파이썬 클라와 의도적으로 다른 점
//!
//! 파이썬 클라는 색 이름을 Rich 에 그대로 넘기는데, Rich 는 `bright_brown` 과
//! `bfightmagenta` 를 모른다. 그래서 그 경로에서 **색이 통째로 버려진다**(예외 폴백이
//! `reverse`/`bold` 만 남긴다 — 기울임·밑줄·취소선까지 잃는다). 즉 지금 파이썬 클라에서
//! 밝은 노랑 글자는 색 없이 나온다.
//!
//! 이쪽은 그 두 이름을 제대로 매핑한다. **버그를 따라 하지 않는 의도적 차이**이며,
//! 그래서 두 클라의 화면이 그 색에서만 다르다.

use crate::message::Style as StyleMap;

/// 색 하나.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Color {
    /// 터미널 팔레트의 8색 + 밝은 8색.
    Named(NamedColor),
    Rgb { r: u8, g: u8, b: u8 },
}

/// 팔레트 색. 실제 RGB 는 테마가 정하므로 여기서는 이름만 든다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NamedColor {
    Black,
    Red,
    Green,
    Yellow,
    Blue,
    Magenta,
    Cyan,
    White,
    BrightBlack,
    BrightRed,
    BrightGreen,
    BrightYellow,
    BrightBlue,
    BrightMagenta,
    BrightCyan,
    BrightWhite,
}

impl Color {
    /// 서버가 보낸 색 문자열을 해석한다. 모르는 값이면 `None`.
    ///
    /// 모르는 값을 기본색으로 조용히 떨어뜨리지 않고 `None` 을 돌려주는 이유는,
    /// 적합성 테스트가 "이 파서가 서버의 모든 색을 아는가"를 물을 수 있게 하기 위해서다.
    pub fn parse(value: &str) -> Option<Self> {
        if let Some(hex) = value.strip_prefix('#') {
            if hex.len() != 6 {
                return None;
            }
            let byte = |i: usize| u8::from_str_radix(&hex[i..i + 2], 16).ok();
            return Some(Color::Rgb {
                r: byte(0)?,
                g: byte(2)?,
                b: byte(4)?,
            });
        }
        let named = match value {
            "black" => NamedColor::Black,
            "red" => NamedColor::Red,
            "green" => NamedColor::Green,
            // 서버는 표준 노랑을 `yellow` 로 보낸다...
            "yellow" | "brown" => NamedColor::Yellow,
            "blue" => NamedColor::Blue,
            "magenta" => NamedColor::Magenta,
            "cyan" => NamedColor::Cyan,
            "white" => NamedColor::White,
            "bright_black" => NamedColor::BrightBlack,
            "bright_red" => NamedColor::BrightRed,
            "bright_green" => NamedColor::BrightGreen,
            // ...그런데 밝은 노랑은 `bright_brown` 이다(pyte 계보). 위와 이름 체계가
            // 어긋나 있어 추측으로는 못 맞힌다.
            "bright_brown" | "bright_yellow" => NamedColor::BrightYellow,
            "bright_blue" => NamedColor::BrightBlue,
            // `bfightmagenta` 는 upstream pyte 의 오타를 pytmux 가 **의도적으로 보존**한
            // 것이다(vtconst.py:117). 서버가 실제로 이 값을 보내므로 반드시 받아야 한다.
            "bright_magenta" | "bfightmagenta" => NamedColor::BrightMagenta,
            "bright_cyan" => NamedColor::BrightCyan,
            "bright_white" => NamedColor::BrightWhite,
            _ => return None,
        };
        Some(Color::Named(named))
    }
}

/// 셀 하나에 적용할 스타일.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CellStyle {
    pub fg: Option<Color>,
    pub bg: Option<Color>,
    pub bold: bool,
    pub italic: bool,
    pub underline: bool,
    pub reverse: bool,
    pub strike: bool,
}

impl CellStyle {
    /// 와이어의 스타일 객체를 해석한다.
    ///
    /// 모르는 색 이름은 그 색만 버리고 **나머지 속성은 지킨다**. 파이썬 클라는 이 경우
    /// 예외 폴백으로 기울임·밑줄·취소선까지 잃는데, 그건 따라 할 이유가 없다.
    pub fn from_map(map: &StyleMap) -> Self {
        let color = |key: &str| {
            map.get(key)
                .and_then(serde_json::Value::as_str)
                .and_then(Color::parse)
        };
        let flag = |key: &str| map.get(key).is_some_and(|v| v != false && !v.is_null());
        Self {
            fg: color("f"),
            bg: color("b"),
            bold: flag("bo"),
            italic: flag("it"),
            underline: flag("un"),
            reverse: flag("rv"),
            strike: flag("st"),
        }
    }

    /// 기본 스타일인가(칠할 것이 하나도 없는가).
    pub fn is_default(&self) -> bool {
        *self == Self::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn map(value: serde_json::Value) -> StyleMap {
        value.as_object().unwrap().clone()
    }

    #[test]
    fn empty_style_is_default() {
        assert!(CellStyle::from_map(&StyleMap::new()).is_default());
    }

    #[test]
    fn reads_every_attribute_key() {
        let style = CellStyle::from_map(&map(
            json!({"bo":1,"it":1,"un":1,"rv":1,"st":1,"f":"red","b":"blue"}),
        ));
        assert!(style.bold && style.italic && style.underline && style.reverse && style.strike);
        assert_eq!(style.fg, Some(Color::Named(NamedColor::Red)));
        assert_eq!(style.bg, Some(Color::Named(NamedColor::Blue)));
    }

    #[test]
    fn hex_colors_parse_to_rgb() {
        assert_eq!(
            Color::parse("#123456"),
            Some(Color::Rgb {
                r: 0x12,
                g: 0x34,
                b: 0x56
            })
        );
        assert_eq!(Color::parse("#abc"), None, "6자리가 아니면 거부");
        assert_eq!(Color::parse("#gggggg"), None, "16진수가 아니면 거부");
    }

    #[test]
    fn the_two_quirky_names_are_handled() {
        // 이 두 줄이 이 모듈의 존재 이유다. 추측으로 쓰면 여기서 틀린다.
        assert_eq!(
            Color::parse("bright_brown"),
            Some(Color::Named(NamedColor::BrightYellow)),
            "SGR 93/103 은 bright_yellow 가 아니라 bright_brown 으로 온다"
        );
        assert_eq!(
            Color::parse("bfightmagenta"),
            Some(Color::Named(NamedColor::BrightMagenta)),
            "SGR 105 의 이름은 upstream 오타를 의도적으로 보존한 것이다"
        );
    }

    #[test]
    fn unknown_color_drops_only_that_color() {
        // 파이썬 클라는 이 경우 예외 폴백으로 기울임·밑줄까지 잃는다. 그걸 따라 하지 않는다.
        let style = CellStyle::from_map(&map(json!({"f":"chartreuse","un":1,"it":1})));
        assert_eq!(style.fg, None);
        assert!(style.underline && style.italic, "나머지 속성은 지킨다");
    }

    #[test]
    fn unknown_color_name_is_reported_not_silently_defaulted() {
        assert_eq!(Color::parse("not_a_color"), None);
    }
}

/// 셀 스타일을 `ratio` 만큼 검정 쪽으로 흐리게(0.0 = 그대로, 0.8 = 아주 어둡게).
///
/// **파이썬과 갈리는 자리** — 저쪽은 실제 전경/배경 RGB 를 블렌드한다. 우리는 팔레트 색의
/// RGB 를 모른다(테마가 정한다). 그래서 RGB 는 같은 비율로 블렌드하고, 팔레트 색은
/// **밝은 쪽을 보통 쪽으로** 내린다(0.4 를 넘는 세기에서만 — 약한 딤에서 색이 통째로
/// 바뀌면 그게 더 눈에 띈다). `bold` 는 항상 푼다: 많은 터미널이 bold 를 밝게 그려서
/// 안 풀면 딤이 상쇄된다(파이썬이 ANSI dim 대신 실색 블렌드를 쓰는 이유와 같다).
pub fn darken(style: &CellStyle, ratio: f32) -> CellStyle {
    let ratio = ratio.clamp(0.0, 1.0);
    if ratio <= 0.0 {
        return *style;
    }
    let mut out = *style;
    out.bold = false;
    out.fg = Some(dim_color(
        style.fg.unwrap_or(Color::Named(NamedColor::White)),
        ratio,
    ));
    out.bg = style.bg.map(|c| dim_color(c, ratio));
    out
}

fn dim_color(color: Color, ratio: f32) -> Color {
    let keep = 1.0 - ratio;
    match color {
        Color::Rgb { r, g, b } => Color::Rgb {
            r: (r as f32 * keep) as u8,
            g: (g as f32 * keep) as u8,
            b: (b as f32 * keep) as u8,
        },
        // 팔레트 색은 RGB 를 모르니 **한 단 내리는** 것이 할 수 있는 전부다.
        Color::Named(named) if ratio >= 0.4 => Color::Named(match named {
            NamedColor::BrightBlack | NamedColor::Black => NamedColor::Black,
            NamedColor::BrightRed | NamedColor::Red => NamedColor::Red,
            NamedColor::BrightGreen | NamedColor::Green => NamedColor::Green,
            NamedColor::BrightYellow | NamedColor::Yellow => NamedColor::Yellow,
            NamedColor::BrightBlue | NamedColor::Blue => NamedColor::Blue,
            NamedColor::BrightMagenta | NamedColor::Magenta => NamedColor::Magenta,
            NamedColor::BrightCyan | NamedColor::Cyan => NamedColor::Cyan,
            // 흰 글자만은 회색으로 — White→White 면 딤이 아무 일도 안 한 것이 된다.
            NamedColor::BrightWhite | NamedColor::White => NamedColor::BrightBlack,
        }),
        // 약한 딤에서 팔레트 색을 바꾸면 색이 통째로 달라 보인다 — bold 만 푼다.
        Color::Named(named) => Color::Named(named),
    }
}
