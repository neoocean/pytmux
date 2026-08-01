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
pub const BORDER: ColorU = c(0x3b, 0x42, 0x61);
/// 마우스가 올라간 자리의 배경.
pub const HOVER: ColorU = c(0x29, 0x2e, 0x42);
/// 활성 탭·선택 줄 배경(캔버스 팔레트의 `SELECTED_BG` 와 같은 값).
pub const ACTIVE: ColorU = c(0x28, 0x34, 0x57);
/// 키보드 크롬 포커스 강조(테두리) — 노랑은 이 앱에서 "지금 조작 대상"의 색이다.
pub const FOCUS: ColorU = c(0xe0, 0xaf, 0x68);
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
}
