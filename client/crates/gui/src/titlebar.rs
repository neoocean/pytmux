//! 창 머리줄 — **OS 타이틀바를 없애고 그 자리를 앱이 받는다**(`pytmux/pytmux-1`).
//!
//! # 무엇이 뒤집혔나
//!
//! §10-20ⓐ(CL 69235)는 창 버튼을 **OS 에게 맡겼다**(`TitleBar::Native`). 근거는
//! *"창 버튼 관습이 OS 마다 다르다 — 자리를 한 벌로 박으면 한쪽이 늘 어색하다"* 였다.
//! 제보(2026-08-02 · 자리 확정 2026-08-04)가 **그 근거를 기각했다**: 맨 위 OS 띠를
//! 없애고 그 역할을 앱 안 머리줄(`pytmux-gui · <엔드포인트>` 를 띄우던 줄)이 받는다.
//! 사용자 판단이므로 근거 기록과 주석·오라클을 **같은 CL 에서** 뒤집는다 — 안 뒤집으면
//! 다음 세션이 "결정된 사항"으로 읽고 되돌린다.
//!
//! # 머리줄 하나가 세 가지를 겸한다
//!
//! ⑴ **끌어서 창 옮기기**(드래그 영역) ⑵ **제목 표시** ⑶ **창 버튼**.
//!
//! ⑴·더블클릭 최대화는 **우리가 안 적는다** — 상류가 이미 한다. winit 백엔드는
//! "앱이 안 먹은 왼쪽 누름이 위에서 `titlebar_height` 안이면 `drag_window()`,
//! 두 번 누름이면 `toggle_maximized()`" 를 갖고 있고(`windowing/winit/event_loop`),
//! 맥은 같은 판정을 `host_view.m` 의 `mouseInTitleBar` + `performWindowDragWithEvent`
//! 로 한다. ⛔ **그래서 우리가 할 일은 두 가지뿐이다**: 그 띠의 높이를 창에게
//! 말해 주는 것([`band_height`] → `set_titlebar_height`)과, 머리줄에서 온 누름을
//! **안 먹었다고 돌려주는 것**(`SessionView` 의 `on_left_mouse_down_with_modifiers`).
//! 두 번째가 빠지면 상류 갈래에 영영 안 닿는다 — 이 크레이트의 루트 `EventHandler` 는
//! 모든 왼쪽 누름을 `StopPropagation` 으로 삼키고 있었다.
//!
//! ⑶ 은 OS 로 갈린다. 제보의 자리 확정이 **Windows 기준**(오른쪽 끝)이라고 못박으면서
//! *"맥은 왼쪽이 관례라 OS 로 갈린다. 그 갈림은 여기서 정하지 않는다"* 고 남겼다.
//! 그래서 이렇게 한다:
//!
//! | | 창 버튼을 누가 그리나 | 어디에 |
//! |---|---|---|
//! | Windows · Linux | **우리**([`BUTTONS`]) | 머리줄 오른쪽 끝 |
//! | macOS | **OS**(신호등 셋) | 머리줄 왼쪽 — `hide_title_bar` 는 띠만 없애고 신호등은 남긴다 |
//!
//! 맥에서 우리 것을 그리면 신호등과 합쳐 여섯 개가 되고, 신호등을 숨기는 것은 상류
//! (`standardWindowButton`)를 건드리는 일이라 **정해지지 않은 갈림을 코드로 정하는
//! 셈**이 된다. 맥은 신호등이 이미 관례의 자리(왼쪽)에 있고, 띠가 사라진 지금 그것은
//! **앱 머리줄 안에** 앉는다 — 제보가 요구한 그림이 그대로 성립한다.
//!
//! ⚠ 그 대신 맥에서는 신호등이 우리 머리줄 위에 **겹쳐** 뜬다. 그래서 왼쪽에
//! [`MAC_LIGHTS_W`] 만큼을 비운다([`row`]).

use warpui::Element;
use warpui::color::ColorU;
use warpui::elements::{
    ConstrainedBox, Container, CrossAxisAlignment, Empty, Expanded, Flex, MainAxisAlignment,
    MainAxisSize, ParentElement,
};

use crate::theme;

/// 머리줄 높이(논리 픽셀 · 글자 배율 1 기준).
///
/// 값의 근거: 이 줄이 대신하는 OS 타이틀바가 그만하다(Windows 32 · macOS 28). 더 얇으면
/// 끌 자리가 손에 안 잡히고, 더 두꺼우면 캔버스에서 그만큼을 뺏는다.
pub const HEIGHT: f32 = 30.;

/// 창 버튼 한 칸의 폭. 높이는 줄 전체를 쓴다(Windows 캡션 버튼과 같은 모양 — 모서리까지
/// 꽉 채워야 "구석으로 던져서 누르기"가 된다).
pub const SLOT_W: f32 = 34.;

/// 맥 신호등 셋이 먹는 폭 — 왼쪽에서 비워 둘 값.
///
/// 값의 출처는 상류다(`platform/mac/objc/window.m` 의 `configure_titlebar_height`):
/// 왼쪽 여백 12 + 버튼 14 셋 + 사이 6 둘 = **66**. 거기에 12 를 더해 제목이 신호등에
/// 바짝 붙지 않게 한다. ⚠ 상류가 그 상수를 바꾸면 여기도 따라가야 한다 — 어긋나면
/// 제목이 신호등 위로 올라탄다(좁은 창에서 먼저 보인다).
pub const MAC_LIGHTS_W: f32 = 78.;

/// 창 버튼 하나. **pytmux 의 액션이 아니다** — 서버도 정본 클라도 이런 명령을 갖지
/// 않는다(터미널 안에서 도는 클라에는 창이 없다). 그래서 `base::Action` 이 아니라
/// 뷰 계층의 값이고, 뜻은 상류 `ViewContext` 의 창 조작 셋이 그대로 갖는다.
///
/// ⚠ 맥에서는 [`BUTTONS`] 가 비어 있어 **아무도 이 값을 만들지 않는다**(신호등이 OS
/// 것이다). 그래도 코드는 한 벌로 둔다 — `#[cfg]` 로 타입까지 지우면 크로스 게이트
/// (`check_windows.sh`)가 검사하는 것과 맥에서 읽는 것이 다른 파일이 된다.
#[cfg_attr(target_os = "macos", allow(dead_code))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Button {
    Minimize,
    Maximize,
    Close,
}

impl Button {
    /// 그릴 글자. **고정폭 글꼴**로 그린다 — 가변폭 UI 글꼴(Helvetica Neue 등)에는
    /// 선문자·기하 도형이 없어 두부가 뜬다(캔버스가 이미 같은 이유로 고정폭을 쓴다).
    pub fn glyph(self) -> &'static str {
        match self {
            Button::Minimize => "─",
            Button::Maximize => "□",
            // `×`(U+00D7)는 이 앱이 이미 탭 닫기에 쓰는 글자다 — 없는 글꼴이 사실상 없다.
            Button::Close => "×",
        }
    }

    /// 마우스가 올라갔을 때의 배경. **닫기만 빨갛다** — 되돌릴 수 없는 것 하나에만
    /// 다른 색을 주는 것이 두 OS 공통의 관습이고, 손이 먼저 안다.
    pub fn hover_bg(self) -> ColorU {
        match self {
            Button::Close => theme::CLOSE_HOVER,
            _ => theme::HOVER,
        }
    }

}

/// 우리가 그리는 창 버튼. **맥에서는 비어 있다** — 그쪽은 OS 신호등이 그 일을 한다
/// (모듈 머리말의 표).
#[cfg(not(target_os = "macos"))]
pub const BUTTONS: &[Button] = &[Button::Minimize, Button::Maximize, Button::Close];
/// 맥은 OS 신호등이 왼쪽에 남는다 — 우리가 그리면 창 버튼이 여섯 개가 된다.
#[cfg(target_os = "macos")]
pub const BUTTONS: &[Button] = &[];

/// 제목을 창 한가운데 두기 위해 **양 끝에서 같게** 비우는 폭(배율 1 기준).
///
/// 한쪽만 비우면 제목이 그만큼 밀린다 — 제보의 자리 확정이 요구한 그림은
/// "왼쪽 여백 · **가운데** 제목 · 오른쪽 창 버튼" 이다.
///
/// ★ 버튼 수를 [`BUTTONS`] 에서 읽지 않고 **인자로 받는** 이유: 이 값은 OS 마다 다른데
/// 개발 상자는 하나다. 상수를 직접 읽으면 맥에서 도는 오라클이 Windows 쪽 숫자를
/// **한 번도 안 잰다**(그 갈래는 크로스 컴파일 게이트가 타입만 볼 뿐 값은 안 본다).
/// 인자로 받으면 두 갈래를 같은 상자에서 다 잴 수 있다.
pub fn reserved_width_for(buttons: usize) -> f32 {
    if buttons == 0 {
        MAC_LIGHTS_W
    } else {
        buttons as f32 * SLOT_W
    }
}

/// 창에게 알릴 띠 높이 — **끌 수 있는 자리**의 높이이자 이 줄의 높이다.
///
/// 글자 배율을 탄다(§10-21ⓐ — "캔버스도 같은 배율을 탄다. 여기만 빼면 앱 전체가
/// 아니다"). ⛔ 그래서 배율이 바뀌면 **창에게 다시 말해야 한다** — 안 말하면 보이는 줄과
/// 잡히는 띠가 어긋나고, 증상은 "줄은 커졌는데 아래쪽 절반이 안 끌린다"로 나온다.
pub fn band_height(font_scale: f32) -> f32 {
    HEIGHT * font_scale
}

/// 창 버튼 한 칸을 감싼다 — 폭은 [`SLOT_W`], 높이는 줄 전체, 글자는 가운데.
///
/// 모서리를 안 둥글리는 이유: 이 칸은 창 구석까지 닿아야 하고, 둥근 모서리는 그 구석에
/// 안 눌리는 몇 픽셀을 만든다(Windows 캡션 버튼도 사각이다).
pub fn slot(font_scale: f32, glyph: Box<dyn Element>, hovered: Option<ColorU>) -> Box<dyn Element> {
    let centered = Flex::row()
        .with_main_axis_size(MainAxisSize::Max)
        .with_main_axis_alignment(MainAxisAlignment::Center)
        .with_cross_axis_alignment(CrossAxisAlignment::Center)
        .with_child(glyph)
        .finish();
    let mut boxed = Container::new(centered);
    if let Some(bg) = hovered {
        boxed = boxed.with_background_color(bg);
    }
    ConstrainedBox::new(boxed.finish())
        .with_width(SLOT_W * font_scale)
        .with_height(band_height(font_scale))
        .finish()
}

/// 머리줄 한 벌 — 왼쪽 여백 · 가운데 제목 · 오른쪽 창 버튼.
///
/// `buttons` 는 [`BUTTONS`] 와 같은 길이·같은 순서라야 한다(뷰가 [`slot`] 으로 감싼
/// 것들). 맥처럼 비어 있으면 오른쪽에도 왼쪽과 같은 여백이 들어간다 — 제목의 가운데는
/// 창의 가운데다.
///
/// ⚠ 이 줄은 **바깥 여백 안에 들어가지 않는다**(호출부가 `Container` 패딩 밖에 둔다).
/// 창 버튼은 창 모서리에 닿아야 하고, 끌 수 있는 띠는 창 맨 위 `band_height` 픽셀이라고
/// 창에게 말해 둔 값과 **자리가 같아야** 하기 때문이다.
pub fn row(
    font_scale: f32,
    title: Box<dyn Element>,
    buttons: Vec<Box<dyn Element>>,
) -> Box<dyn Element> {
    // 폭은 **넘어온 버튼 수**가 정한다(상수를 다시 읽지 않는다) — 그래야 오라클이 두
    // 갈래를 같은 상자에서 잰다.
    let reserve = reserved_width_for(buttons.len()) * font_scale;
    let gap = || {
        ConstrainedBox::new(Empty::new().finish())
            .with_width(reserve)
            .finish()
    };
    let empty = buttons.is_empty();
    let mut right = Flex::row()
        .with_main_axis_size(MainAxisSize::Min)
        .with_cross_axis_alignment(CrossAxisAlignment::Center);
    for button in buttons {
        right = right.with_child(button);
    }
    let center = Flex::row()
        .with_main_axis_size(MainAxisSize::Max)
        .with_main_axis_alignment(MainAxisAlignment::Center)
        .with_cross_axis_alignment(CrossAxisAlignment::Center)
        .with_child(title)
        .finish();
    let row = Flex::row()
        .with_main_axis_size(MainAxisSize::Max)
        .with_cross_axis_alignment(CrossAxisAlignment::Center)
        .with_child(gap())
        .with_child(Expanded::new(1., center).finish())
        .with_child(if empty { gap() } else { right.finish() })
        .finish();
    // 크롬 띠 색을 깐다 — 탭바·상태줄과 같은 표면이다(`theme::SURFACE` 문서). 바탕색
    // 그대로 두면 이 줄이 **띠로 안 읽혀** 사라진 OS 타이틀바의 자리를 아무도 못 찾는다.
    let banded = Container::new(row)
        .with_background_color(theme::SURFACE)
        .finish();
    ConstrainedBox::new(banded)
        .with_height(band_height(font_scale))
        .finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_title_is_centred_because_both_ends_reserve_the_same_width() {
        // 제보의 자리 확정: "왼쪽 여백 · 가운데 제목 · 오른쪽 창 버튼". 한쪽만 비우면
        // 제목이 그만큼 밀린다 — 그 대칭이 이 함수 하나에 걸려 있다.
        // ★ **두 갈래를 같은 상자에서 잰다** — 개발 상자는 하나뿐이라 상수만 읽으면
        //   한쪽 숫자는 영영 안 재진다(그래서 `reserved_width_for` 가 인자를 받는다).
        // 맥 갈래(우리 버튼 0개) — OS 신호등이 왼쪽을 먹으니 그만큼 비운다.
        assert_eq!(reserved_width_for(0), MAC_LIGHTS_W);
        // Windows·Linux 갈래(셋) — 버튼 폭의 합이다.
        assert_eq!(reserved_width_for(3), 3. * SLOT_W);
        assert!(
            reserved_width_for(BUTTONS.len()) > 0.,
            "양 끝에 비울 폭이 0 이면 제목이 밀린다"
        );
    }

    #[test]
    fn the_band_follows_the_font_scale() {
        // ⛔ 이 값이 배율을 안 타면 글자만 커지고 잡히는 띠는 그대로다 —
        //    "줄은 커졌는데 아래쪽 절반이 안 끌린다"가 그 증상이다.
        assert_eq!(band_height(1.), HEIGHT);
        assert_eq!(band_height(2.), HEIGHT * 2.);
        assert!(band_height(0.5) < HEIGHT);
    }

    #[test]
    fn every_button_has_its_own_glyph_and_the_close_one_is_red() {
        // 세 칸이 같은 글자면 무엇을 누르는지 알 수 없다(양성 오라클 — 전수 대조).
        let all = [Button::Minimize, Button::Maximize, Button::Close];
        let mut glyphs: Vec<&str> = all.iter().map(|b| b.glyph()).collect();
        glyphs.sort_unstable();
        glyphs.dedup();
        assert_eq!(glyphs.len(), 3, "창 버튼 글자가 겹친다");
        // 되돌릴 수 없는 것 하나에만 다른 색을 준다.
        assert_eq!(Button::Close.hover_bg(), theme::CLOSE_HOVER);
        assert_eq!(Button::Minimize.hover_bg(), theme::HOVER);
        assert_eq!(Button::Maximize.hover_bg(), theme::HOVER);
    }

    #[test]
    fn we_draw_the_buttons_exactly_where_the_os_does_not() {
        // 모듈 머리말의 표를 기계로 못박는다. 맥에서 우리 것을 그리면 신호등과 합쳐
        // 여섯 개가 되고, 그 밖의 OS 에서 안 그리면 창을 닫을 자리가 아예 없다.
        if cfg!(target_os = "macos") {
            assert!(BUTTONS.is_empty(), "맥은 OS 신호등이 그 일을 한다");
        } else {
            assert_eq!(
                BUTTONS,
                &[Button::Minimize, Button::Maximize, Button::Close],
                "창 버튼 셋이 오른쪽 끝에 있어야 한다(Windows 기준 순서)"
            );
        }
    }
}
