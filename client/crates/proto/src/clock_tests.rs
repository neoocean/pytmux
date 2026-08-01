//! 시계 오버레이의 **딤**과 폰트 고르기(패리티 G7b).
//!
//! ⚠ 2026-08-02(설계 Tier B · P3)에 **"어디에 그리나"를 재던 오라클 넷을 지웠다** —
//! 그리는 일이 이 크레이트에서 사라졌기 때문이다(시계 그림은 서버가 런으로 준다).
//! 지금 여기 남은 것은 **아직 우리 일**인 둘이다: 뒤를 흐리게 하는 규칙(`darken`,
//! 딤은 런으로 못 나른다)과 폰트 고르기(`font_for` — 달력이 부른다).
//! 글리프 자체는 픽스처 대조가 지킨다(`tests/clock_conformance.rs`).

use super::*;
use crate::style::{CellStyle, Color, NamedColor};

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
