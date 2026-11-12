//! 입력 소스 ID → 한/영 — UI·OS 무의존 순수 판정(pytmux-590).
//!
//! OS 에 묻는 것(맥 TIS · 윈도 IMM · 리눅스 입력기 CLI)은 gui `ime.rs` 의 몫이고, **물어서
//! 받은 글을 무슨 뜻으로 읽나**는 여기 한 자리다 — 정본 `plugins/ime-indicator/oskbd.py` 의
//! `is_korean`·`parse_fcitx5`·`parse_ibus` 와 같은 답을 낸다(답지 = `scripts/gen_ime_source.py`
//! → `proto/tests/fixtures/ime_source.json`, 대조 = `proto/tests/ime_source_conformance.rs`).
//!
//! # 리눅스의 한계(정본과 같다)
//!
//! ibus-hangul 은 한/영을 **엔진 안에서** 바꾼다 — 엔진 이름은 `hangul` 그대로라 IBus 에서는
//! 「한글 엔진이 걸려 있다」까지만 안다(그 엔진 안의 영문 모드는 못 가른다). Fcitx5 는 보통 한/영
//! 키가 입력기 자체를 바꾸므로(`keyboard-us` ↔ `hangul`) 정확하다.

/// 한글 입력 소스로 읽는 토큰(소문자 부분 문자열 · 정본 `_KOREAN_TOKENS`). 애플 기본
/// (`com.apple.inputmethod.Korean.2SetKorean`) · 서드파티(구름 han2/han3) · 윈도 합성
/// (`windows.ime.hangul`) · 리눅스 합성(`fcitx5:hangul`·`ibus:hangul`)을 함께 잡는다.
const KOREAN_TOKENS: [&str; 4] = ["korean", "hangul", "han2", "han3"];

/// 입력 소스 ID 가 한글인가(정본 `is_korean`). `None`·빈 글은 한글이 아니다 — 다만 «모름»과
/// «영문»의 구분은 이 함수 밖이다(`None` 이면 배지를 안 그리는 것은 호출부가 정한다).
pub fn is_korean(source_id: Option<&str>) -> bool {
    let Some(s) = source_id.filter(|s| !s.is_empty()) else {
        return false;
    };
    let s = s.to_lowercase();
    KOREAN_TOKENS.iter().any(|t| s.contains(t))
}

fn first_line(out: &str) -> Option<&str> {
    out.lines().map(str::trim).find(|l| !l.is_empty())
}

/// `fcitx5-remote -n` 출력 → 합성 ID `fcitx5:<입력기>`(정본 `parse_fcitx5`).
pub fn parse_fcitx5(out: &str) -> Option<String> {
    first_line(out).map(|n| format!("fcitx5:{n}"))
}

/// `ibus engine` 출력 → 합성 ID `ibus:<엔진>`(정본 `parse_ibus`).
pub fn parse_ibus(out: &str) -> Option<String> {
    first_line(out).map(|n| format!("ibus:{n}"))
}

/// 배지 낱말(번역하지 않는다 — 정본과 같은 `한`/`EN`).
pub fn badge_word(source_id: Option<&str>) -> Option<&'static str> {
    source_id.map(|s| if is_korean(Some(s)) { "한" } else { "EN" })
}

#[cfg(test)]
#[path = "ime_source_tests.rs"]
mod tests;
