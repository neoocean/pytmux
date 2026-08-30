//! GUI 크롬의 디자인 토큰 한 벌 — Warp 스타일 모던 다크 (계획: pytmux
//! `docs/internal/PYTMUX_CLIENT_GUI_NATIVE_PLAN_2026-07-30.md`, 사용자 결정 2026-07-30).
//!
//! # 왜 파일 하나인가
//!
//! 크롬(탭바·팝업·상태줄·버튼)을 위젯화하면 라운드 반경·표면색·그림자 같은 값이 여러
//! 렌더 함수에 흩어진다. 흩어진 값은 한쪽만 고쳐져 화면이 슬라이스마다 다른 앱처럼
//! 보이게 된다 — 그래서 값의 주인을 여기 한 곳으로 못박는다. 터미널 **캔버스** 색은
//! 여기가 아니라 `session_view::palette` 다(서버 팔레트 이름과 1:1 인 표라 성격이 다르다).
//!
//! 색 계열은 캔버스 팔레트와 같은 tokyonight 에서 골랐다 — 크롬과 캔버스가 다른
//! 배색이면 그게 곧 결함처럼 보인다.

use warpui::color::ColorU;
use warpui::elements::{CornerRadius, DropShadow, Radius};

const fn c(r: u8, g: u8, b: u8) -> ColorU {
    ColorU { r, g, b, a: 0xff }
}

/// 창 바탕. `palette::BG` 와 같은 값 — 캔버스와 크롬이 같은 바닥 위에 있다.
pub const BG: ColorU = c(0x1a, 0x1b, 0x26);
/// 크롬 띠(탭바·상태줄) 표면. 바탕보다 **어둡다** — Warp 도 탭 띠가 본문보다 가라앉는다.
pub const SURFACE: ColorU = c(0x16, 0x16, 0x1e);
/// 떠 있는 판(팝업·팔레트) 표면. 바탕보다 밝다 — 그림자와 함께 "위에 떠 있음"을 만든다.
pub const ELEV: ColorU = c(0x1f, 0x23, 0x35);
/// 떠 있는 판의 테두리.
///
/// # 왜 이 밝기인가 (pytmux-372)
///
/// 종전 값(`#3b4261`)은 창 바탕 대비 **1.74:1** 이라 판의 경계가 사실상 없었다 — 판
/// 표면(`ELEV`)도 바탕과 1.10:1 이므로, 그림자를 뺀 판은 「어디까지가 판인가」를 말하는
/// 것이 아무것도 없었다. 지금 값은 바탕 대비 **3.63:1** 로 비문자 UI 기준(3:1)을 넘는다.
///
/// ⛔ 판 **표면**을 밝히는 길로는 안 풀린다. 정본도 판 표면과 바탕이 1.37:1 로 거의 같고
/// (`clientutil::_THEME_FALLBACK` 의 `panel`↔`background`), 경계를 말하는 것은 테두리다 —
/// 표면을 올리면 그 위 글자 대비가 같이 떨어져 얻는 것보다 잃는 것이 크다.
/// 재는 것은 `the_panel_border_is_visible_against_the_window`.
pub const BORDER: ColorU = c(0x62, 0x72, 0xa4);
/// 마우스가 올라간 자리의 배경.
pub const HOVER: ColorU = c(0x29, 0x2e, 0x42);
/// 창 **닫기** 버튼에 마우스가 올라간 자리의 배경(`pytmux-1`).
///
/// 다른 창 버튼과 다른 색인 이유: 되돌릴 수 없는 것은 하나뿐이고, 두 OS 가 공통으로
/// 그것만 빨갛게 칠한다(Windows 캡션 버튼 · 맥 신호등의 빨간 점). 손이 눈보다 먼저
/// 안다 — [`HOVER`] 로 통일하면 최소화와 닫기가 같은 그림이 된다.
pub const CLOSE_HOVER: ColorU = c(0xc4, 0x2b, 0x1c);
/// 레터박스 여백의 **무광(matte)** 바탕(pytmux-381).
///
/// # 왜 창 바탕(`BG`)이 아닌가
///
/// 여러 클라가 같은 탭을 볼 때 격자는 정책이 정한 한 크기이고, 그보다 큰 창에는 오른쪽·아래에
/// 남는 자리가 생긴다. 그 자리가 **창 바탕 그대로**면 「깨진 그림」으로 읽힌다(제보 2026-08-24).
/// 한 톤 가라앉히면 같은 자리가 **의도된 여백**이 된다 — 정본이 `panel` 색으로 하는 그 일이다
/// (`clientio.py::_composite` 의 matte 띠).
///
/// 값은 [`SURFACE`] 와 같다 — 이 테마에서 「가라앉은 표면」이 이미 그 뜻이고, 두 자리가 같은
/// 톤이면 화면이 한 벌로 읽힌다. **상수를 따로 세운 이유**는 뜻이 다르기 때문이다: 저것은
/// 크롬 띠, 이것은 살아 있지 않은 영역이다(나중에 갈라야 할 날이 오면 값만 바꾼다).
pub const MATTE: ColorU = c(0x16, 0x16, 0x1e);

/// 활성 탭·선택 줄 배경(캔버스 팔레트의 `SELECTED_BG` 와 같은 값).
///
/// # 왜 밝혔나, 그리고 왜 여기까지만인가 (pytmux-372)
///
/// 종전 값(`#283457`)은 판 표면 대비 **1.27:1** — 고른 줄이 어느 줄인지 색으로는 거의
/// 말하지 않았다. 지금은 **1.69:1** 이고, 판의 줄이 [`CrossAxisAlignment::Stretch`] 로
/// **줄 전체**를 채우게 되면서(같은 이슈) 같은 값이 훨씬 강하게 읽힌다.
///
/// ⛔ **3:1 까지 올리는 길은 이 색 하나로는 없다.** 올리면 그 위의 글자가 같이 죽는다 —
/// 본문 글자(`palette::FG`)는 이 띠 위에서 5.70:1 인데 띠를 3:1(≈`#4a5486` 이상)로
/// 올리면 4.5:1 아래로 떨어지고, 부속 칸의 흐린 글자는 그보다 먼저 죽는다. 정본은 그
/// 지점에서 **글자를 반전한다**(`primary` 바탕 + 흰 글자 = 3.01:1 / 4.53:1). 그것이
/// 이 색의 남은 절반이고, 줄을 그리는 스무 자리가 「고른 줄인가」를 글자색까지 알아야
/// 하는 일이라 같은 CL 에 넣지 않았다(pytmux-372 에 실측과 함께 남긴다).
pub const ACTIVE: ColorU = c(0x37, 0x46, 0x72);
/// 키보드 크롬 포커스 강조(테두리) — 노랑은 이 앱에서 "지금 조작 대상"의 색이다.
pub const FOCUS: ColorU = c(0xe0, 0xaf, 0x68);

/// **반전 칩**의 배경(§10-21ⓖ·ⓧ) — 글자는 [`INVERT_FG`] 로 뺀다.
///
/// # 왜 새 색 한 쌍인가
///
/// 종전 칩은 배경 없이 노란 **글자**였다. 제보가 *"배경·글자 색을 반전해 눈에 띄게"*
/// 라고 했고, 같은 자리의 다른 제보(ⓧ)는 *"토글이 **켜졌다**는 것을 배지 모양으로
/// 보여야 한다"* 였다 — 둘 다 "배경을 채운 칩"을 요구한다. 색을 두 자리에서 각자
/// 고르면 같은 뜻이 화면마다 다른 그림이 되므로 여기 한 쌍으로 둔다.
///
/// ⚠ [`FOCUS`] 와 **다른 색이라야 한다**: FOCUS 는 "키보드가 이것을 고르고 있다"이고
/// 이것은 "이 상태가 켜져 있다"다. 겹치면 고르는 중과 켜짐이 한 그림이 된다
/// (`render_status` 가 그 둘을 나란히 그린다).
pub const INVERT_BG: ColorU = c(0x7a, 0xa2, 0xf7);

/// 반전 칩의 글자 — 배경 위에서 읽히도록 어두운 쪽으로 뺀다.
pub const INVERT_FG: ColorU = c(0x16, 0x16, 0x1e);
/// 팝업 뒤 캔버스를 가라앉히는 딤. 알파가 뜻이다 — 아래가 비쳐 보이되 읽히지는 않게.
pub const DIM_SCRIM: ColorU = ColorU { r: 0x10, g: 0x10, b: 0x18, a: 0xb0 };

/// 탭 라운드.
pub const TAB_RADIUS: CornerRadius = CornerRadius::with_all(Radius::Pixels(6.));
/// 떠 있는 판 라운드.
pub const PANEL_RADIUS: CornerRadius = CornerRadius::with_all(Radius::Pixels(10.));
/// 칩(배지)·원형 버튼 — pill.
pub const PILL_RADIUS: CornerRadius = CornerRadius::with_all(Radius::Percentage(50.));

/// 떠 있는 판의 그림자.
pub fn panel_shadow() -> DropShadow {
    DropShadow {
        color: ColorU { r: 0, g: 0, b: 0, a: 0x80 },
        offset: warpui::geometry::vector::vec2f(0., 6.),
        blur_radius: 24.,
        spread_radius: 0.,
    }
}

/// 크롬 글꼴(가변폭) 후보. **처음 뜨는 것**을 쓴다 — 규칙과 이유는 `mono_font` 와 같고,
/// 순서만 "그 OS 의 UI 가 기본으로 쓰는 것"이 앞이다.
///
/// macOS 시스템 글꼴(SF Pro)은 이름으로 못 얻는다(닷 프리픽스 숨김 글꼴) — 항상 있는
/// `Helvetica Neue` 가 macOS 몫이다.
pub const UI_CANDIDATES: &[&str] = &[
    "Helvetica Neue",
    "Segoe UI",
    "Noto Sans",
    "DejaVu Sans",
    "Liberation Sans",
    "Arial",
];

/// 글꼴 캐시에 크롬 글꼴을 깔고 family 를 돌려준다. **못 찾아도 패닉하지 않는다** —
/// 크롬은 고정폭으로도 그려진다(부른 쪽이 mono 로 폴백한다). 한글·기호 폴백은
/// `mono_font::install` 이 이미 깔았다(같은 캐시 한 벌).
pub fn install_ui(cache: &mut warpui::fonts::Cache) -> Option<warpui::fonts::FamilyId> {
    match crate::mono_font::pick(UI_CANDIDATES, |n| cache.load_system_font(n)) {
        Ok((name, family)) => {
            log::info!("크롬 글꼴: {name}");
            Some(family)
        }
        Err(tried) => {
            log::warn!("{tried} — 크롬을 고정폭으로 그린다");
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_major_desktop_has_a_ui_font_candidate() {
        // 목록이 한쪽 OS 로 기울면 그 밖에서는 크롬이 조용히 고정폭 폴백이 된다.
        assert!(UI_CANDIDATES.contains(&"Helvetica Neue"), "macOS 후보가 빠졌다");
        assert!(UI_CANDIDATES.contains(&"Segoe UI"), "Windows 후보가 빠졌다");
        assert!(
            UI_CANDIDATES.contains(&"Noto Sans") || UI_CANDIDATES.contains(&"DejaVu Sans"),
            "리눅스 후보가 빠졌다"
        );
    }

    #[test]
    fn the_scrim_is_translucent_not_opaque() {
        // 딤이 불투명하면 "팝업이 캔버스를 대체"하던 옛 모양으로 돌아간 것이다 —
        // 알파가 이 토큰의 뜻이다.
        assert!(DIM_SCRIM.a < 0xff && DIM_SCRIM.a > 0x40, "딤 알파가 뜻을 잃었다");
    }

    // ── 대비 (pytmux-372) ──────────────────────────────────────────────────────
    //
    // ⛔ **색은 눈으로 보면 다시 흐려진다.** 이 앱은 같은 부류의 제보를 이미 세 번 받았고
    // (pytmux-44·180·372) 그중 둘은 「한 화면만」 고쳐졌다. 그래서 판정을 사람이 아니라
    // 게이트에 둔다 — 토큰을 만지는 다음 사람은 이 시험을 지나야 한다.
    //
    // 기준은 WCAG 2.x 상대명도 대비다: **본문 글자 4.5:1 · 비문자 UI 3:1**.

    /// sRGB 한 칸의 선형화(WCAG 정의).
    fn channel(v: u8) -> f64 {
        let c = v as f64 / 255.0;
        if c <= 0.03928 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) }
    }

    fn luminance(c: ColorU) -> f64 {
        0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b)
    }

    /// 두 색의 대비(1.0 ~ 21.0). 순서는 무관하다.
    fn contrast(a: ColorU, b: ColorU) -> f64 {
        let (x, y) = (luminance(a), luminance(b));
        let (hi, lo) = if x > y { (x, y) } else { (y, x) };
        (hi + 0.05) / (lo + 0.05)
    }

    #[test]
    fn the_contrast_meter_itself_is_right() {
        // ★ 이 오라클이 먼저다. 자가 검증이 없으면 아래 셋은 **아무 값이나 통과**시킨다.
        let white = c(0xff, 0xff, 0xff);
        let black = c(0x00, 0x00, 0x00);
        assert!((contrast(white, black) - 21.0).abs() < 0.05, "흑백이 21:1 이 아니다");
        assert!((contrast(white, white) - 1.0).abs() < 0.001, "같은 색이 1:1 이 아니다");
        // 정본이 실제로 서 있는 값 하나로 교차 확인한다(textual-dark 의 `primary`↔`panel`).
        let canon = contrast(c(0x01, 0x78, 0xd4), c(0x24, 0x2f, 0x38));
        assert!((canon - 3.01).abs() < 0.05, "정본 기준값과 어긋난다: {canon:.2}");
    }

    #[test]
    fn the_panel_border_is_visible_against_the_window() {
        // 판 표면은 바탕과 거의 같은 색이다(정본도 그렇다) — 그러니 «어디까지가 판인가»를
        // 말하는 것은 테두리 하나다. 비문자 UI 기준 3:1.
        let ratio = contrast(BORDER, BG);
        assert!(ratio >= 3.0, "판 경계가 바탕에 묻힌다: {ratio:.2}:1");
    }

    #[test]
    fn the_footer_hint_is_readable_on_a_panel() {
        // 꼬리줄은 장식이 아니라 **그 판에서 무슨 키를 누르는지**다. 본문 기준 4.5:1.
        let ratio = contrast(crate::session_view::palette::DIM, ELEV);
        assert!(ratio >= 4.5, "꼬리줄 안내가 안 읽힌다: {ratio:.2}:1");
    }

    #[test]
    fn the_selected_row_is_brighter_than_the_panel_and_still_carries_text() {
        // 고른 줄의 띠와 그 위 글자는 **한 쌍으로** 판정한다 — 띠만 올리면 글자가 죽고,
        // 글자만 보면 띠가 안 보인다. 아래 두 수가 이 색의 상한을 함께 정한다
        // (3:1 까지 가는 길 = 글자 반전. 사유는 `ACTIVE` 문서).
        let band = contrast(ACTIVE, ELEV);
        let text = contrast(crate::session_view::palette::FG, ACTIVE);
        assert!(band >= 1.6, "고른 줄이 판에 묻힌다: {band:.2}:1");
        assert!(text >= 4.5, "고른 줄 위 글자가 안 읽힌다: {text:.2}:1");
        // 그리고 종전 값(1.27:1)으로 되돌아가지 않는지 — 회귀는 조용히 온다.
        assert!(band > 1.5, "선택 띠가 옛 값으로 되돌아갔다: {band:.2}:1");
    }

    #[test]
    fn dim_is_still_dimmer_than_the_body_text() {
        // 꼬리줄을 밝히면서 **흐림의 뜻**을 잃으면 안 된다 — 흐림은 절대 밝기가 아니라
        // 본문과의 차이로 읽힌다.
        let dim = luminance(crate::session_view::palette::DIM);
        let fg = luminance(crate::session_view::palette::FG);
        assert!(fg / dim >= 1.8, "본문과 흐린 글자가 한 밝기가 됐다: {:.2}배", fg / dim);
    }

    #[test]
    fn the_two_names_of_the_selected_row_carry_the_same_value() {
        // 캔버스 표(`palette`)와 크롬 표(`theme`)에 같은 뜻이 갈라져 있다. 값이 갈리면
        // 같은 «고른 줄»이 화면마다 달라진다(pytmux-180 이 그 부류였다).
        assert_eq!(ACTIVE, crate::session_view::palette::SELECTED_BG);
    }
}
