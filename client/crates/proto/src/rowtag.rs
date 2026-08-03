//! 줄의 **의미 태그 → 색**(pytmux-11·12 A).
//!
//! # 무엇을 푸나
//!
//! 플러그인 화면의 줄은 `dir`·`hidden`·`drive`·`tagged`·`archive` 같은 **이름**을 달고
//! 온다([`crate::session::PluginRow::tag`]). 서버는 이름만 싣는다 — hex 를 실으면 서버가
//! UI 를 알게 된다(설계 §10 위험표). 값으로 바꾸는 것은 여기다.
//!
//! # 왜 [`crate::session::theme`] 와 갈리나
//!
//! 저쪽은 **의미 색**(`success`·`warning`)이라 각 클라가 **자기 테마**로 푼다. 여기는
//! 다르다 — 이 색들은 테마가 아니라 **제품의 정체성**이다(Norton Commander 계열의 그림).
//! 정본이 hex 를 하드코딩한 이유가 그것이고, 그래서 우리도 **정본과 같은 값**을 쓴다.
//! 테마로 풀면 같은 화면이 두 클라에서 다른 그림이 되고, 제보가 *"컬러 스킴 일치가 특히
//! 중요하다"* 고 못박은 것이 바로 그 지점이다.
//!
//! 표의 값은 **정본에서 뽑는다**(`scripts/gen_row_tags.py` → `tests/fixtures/row_tags.json`).
//! 손으로 옮기면 조용히 갈린다 — 이 저장소가 여러 번 밟은 자리라 전수를 오라클이 잰다.

use crate::style::Color;

/// 태그 하나의 색. 모르는 이름은 `None`(안 칠한다 — 그 줄은 기본색으로 뜬다).
///
/// ⚠ 모르는 이름을 **조용히** 넘기는 것이 이 함수의 위험이다(pytmux-16 이 그 부류다).
/// 그래서 어휘 전수를 픽스처가 지킨다 — 정본에 이름이 늘면 적합성 테스트가 운다.
pub fn color(tag: &str) -> Option<Color> {
    let rgb = match tag {
        // 일반 파일 — 회백.
        "text" => (0xaa, 0xaa, 0xaa),
        // 디렉터리 — 붉은색(Mdir 시그니처).
        "dir" => (0xff, 0x55, 0x55),
        // `[ Up-Dir ]` — 흰색.
        "updir" => (0xff, 0xff, 0xff),
        // 숨은 파일 — 보라.
        "hidden" => (0xaa, 0x00, 0xaa),
        // `[-C-]` 드라이브 — 주황.
        "drive" => (0xff, 0xaa, 0x00),
        // 고른(태그된) 줄 — 노랑. **갈래보다 먼저**다(고른 것은 무엇이든 노랗다).
        "tagged" => (0xff, 0xff, 0x55),
        // 압축 — 자홍.
        "archive" => (0xff, 0x55, 0xff),
        // 실행 파일: `.exe` 와 실행 비트는 같은 초록, `.com` 은 청록,
        // 스크립트(`.bat`·`.sh` …)는 노랑 — 정본의 확장자 표 그대로다.
        "exe" | "exec" => (0x55, 0xff, 0x55),
        "com" => (0x55, 0xff, 0xff),
        "script" => (0xff, 0xff, 0x55),
        // ncd 의 현재 디렉터리 강조 — 같은 어휘의 한 칸(그 화면에만 있다).
        "cwd" => (0xff, 0xff, 0x55),
        _ => return None,
    };
    Some(Color::Rgb { r: rgb.0, g: rgb.1, b: rgb.2 })
}

/// 이 표가 아는 이름 전부 — 픽스처 전수 대조에 쓴다.
pub const KNOWN: &[&str] = &[
    "archive", "com", "cwd", "dir", "drive", "exe", "exec", "hidden", "script",
    "tagged", "text", "updir",
];

#[cfg(test)]
#[path = "rowtag_tests.rs"]
mod tests;
