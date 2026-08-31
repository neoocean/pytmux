//! 칸의 **의미 등급**(pytmux-419 ⑥) — `ok`·`warn`·`crit`.
//!
//! # 왜 [`crate::rowtag`] 와 갈리나
//!
//! 저쪽은 **줄**이 무엇인가(`dir`·`hidden`·`archive`)이고 색이 곧 **제품의 정체성**이라
//! 정본과 같은 hex 를 쓴다(Norton Commander 계열의 그림). 여기는 **칸**이 얼마나 찼나이고
//! 색은 「읽혀야 하는 신호」다 — 그래서 값이 아니라 **등급**만 낸다. 값으로 바꾸는 것은
//! 뷰이고, GUI 는 제 크롬 의미색으로 푼다(`gui::theme::{OK,WARN,ERROR}` — pytmux-412 ⓑ1 이
//! 「크롬 의미색을 SGR 팔레트에서 뗀다」로 정한 자리다. 여기서 hex 를 주면 그 결정이
//! 조용히 되돌아간다).
//!
//! # 왜 이름이 옮겨 다니나
//!
//! 눈금(≥50 주의 · ≥80 위험 · 상태줄 한도 배지와 같은 임계)은 정본
//! `usagehead.pct_level` 한 벌이 쥔다. 서버가 색을 실으면 그 순간 서버가 UI 를 알게 되고
//! (설계 §10 위험표), 두 클라가 각자 임계를 적으면 갈리는 날 아무도 안 운다.
//!
//! 어휘는 **정본에서 뽑는다**(`scripts/gen_pct_levels.py` →
//! `tests/fixtures/pct_levels.json`) — 정본에 등급이 늘면 `celltag_tests.rs` 가 운다.
//! ⛔ 모르는 이름은 [`None`] 이다(안 칠한다). 짐작해서 칠하면 **틀린 뜻**이 화면에 뜬다.

/// 한 칸이 진 뜻. 색은 **뷰가 정한다** — 이 열거는 등급만 안다.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    /// 여유롭다.
    Ok,
    /// 주의 — 절반을 넘겼다.
    Warn,
    /// 위험 — 한도에 가깝다.
    Crit,
}

/// 이름 하나의 등급. 빈 이름·모르는 이름은 `None`(그 칸은 기본색으로 뜬다).
pub fn level(name: &str) -> Option<Level> {
    match name {
        "ok" => Some(Level::Ok),
        "warn" => Some(Level::Warn),
        "crit" => Some(Level::Crit),
        _ => None,
    }
}

/// 이 표가 아는 이름 전부 — 픽스처 전수 대조에 쓴다(**이름순**).
pub const KNOWN: &[&str] = &["crit", "ok", "warn"];

#[cfg(test)]
#[path = "celltag_tests.rs"]
mod tests;
