//! 터치 스크롤바 — **휠을 안 넘겨 주는 단말**에서 스크롤백에 닿는 유일한 길.
//!
//! # 왜 있나
//!
//! 정본이 이 경로를 만든 이유(진단 2026-07-31, iPhone Blink → ssh → MSYS)를 그대로
//! 옮긴다: 그런 단말에서는 **클릭은 SGR 로 정상 도달하는데 휠은 0건**이다 — 두 손가락
//! 스와이프를 단말이 자기 스크롤백 UI 로 소비해 버려, 앱이 휠을 받을 방법이 원천적으로
//! 없다. 그래서 **도달하는 유일한 입력인 탭**으로 스크롤백을 조작한다: 스크롤 모드에서
//! 활성 패널 오른쪽 끝 한 열에 스크롤바를 그리고, `▲`/`▼` 탭 = 반 화면 위/아래,
//! 트랙 탭 = 그 자리로 점프.
//!
//! # 왜 순수 함수인가
//!
//! 셀 격자도 소켓도 안 건드린다 — 그래서 정본에서 뽑은 값과 **한 자리씩** 대조할 수 있다
//! (`tests/scrollbar_conformance.rs` · 픽스처 `scripts/gen_scrollbar_fixture.py`).
//! 반올림이 세 군데(썸 길이·썸 위치·점프 델타)라 눈으로 옮기면 한 칸씩 어긋난 바가
//! 나오고, 그건 두 화면을 나란히 놓아야만 보인다.
//!
//! # 좌표계
//!
//! 서버 프레임의 두 값만으로 닫힌다 — `top`(뷰포트 첫 행의 **절대** 인덱스)과
//! `scroll`(라이브에서 위로 올라간 행수). 위로 더 갈 수 있는 최대치는 `top + scroll`,
//! 전체 행수는 `top + h + scroll`.

/// 위 화살표.
pub const UP: char = '▲';
/// 아래 화살표.
pub const DOWN: char = '▼';
/// 트랙(썸이 없는 자리).
pub const TRACK: char = '│';
/// 썸(지금 보고 있는 구간).
pub const THUMB: char = '█';
/// `▲` + 트랙 한 칸 + `▼` 미만이면 그리지 않는다.
pub const MIN_H: usize = 3;

/// 스크롤바 한 열의 글자들(길이 `h`). 높이가 [`MIN_H`] 미만이면 빈 벡터(미표시).
///
/// 썸 길이 = 트랙 × (보이는 `h` / 전체), 위치는 아래에서부터 `scroll` 비율이다.
pub fn chars(h: usize, top: usize, scroll: usize) -> Vec<char> {
    if h < MIN_H {
        return Vec::new();
    }
    let n = h - 2; // 화살표 두 칸을 뺀 트랙 길이
    let max_scroll = top + scroll;
    let total = max_scroll + h;
    let thumb = if total > 0 {
        (round_half_away(n as f64 * h as f64 / total as f64)).clamp(1, n as i64) as usize
    } else {
        n
    };
    // frac: 0.0 = 맨 위(스크롤 최대) … 1.0 = 맨 아래(라이브). 스크롤백이 없으면 썸이
    // 트랙 전체라 위치는 뜻이 없다(0 으로 고정).
    let frac = if max_scroll == 0 {
        0.0
    } else {
        1.0 - scroll as f64 / max_scroll as f64
    };
    let start = round_half_away((n - thumb) as f64 * frac).clamp(0, (n - thumb) as i64) as usize;
    let mut out = Vec::with_capacity(h);
    out.push(UP);
    for i in 0..n {
        out.push(if i >= start && i < start + thumb { THUMB } else { TRACK });
    }
    out.push(DOWN);
    out
}

/// 스크롤바 안에서 누른 자리가 무엇인가.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Hit {
    /// `▲` — 반 화면 위로.
    Up,
    /// `▼` — 반 화면 아래로.
    Down,
    /// 트랙 — 그 자리로 점프(`0.0` = 맨 위 … `1.0` = 맨 아래).
    Jump(f64),
}

/// 스크롤바 안 **상대 행** `iy` → 조작. 범위 밖이거나 미표시면 `None`.
pub fn hit(h: usize, iy: usize) -> Option<Hit> {
    if h < MIN_H || iy >= h {
        return None;
    }
    if iy == 0 {
        return Some(Hit::Up);
    }
    if iy == h - 1 {
        return Some(Hit::Down);
    }
    let n = h - 2;
    Some(Hit::Jump(if n > 1 { (iy - 1) as f64 / (n - 1) as f64 } else { 0.0 }))
}

/// 트랙 탭이 요구하는 스크롤 델타(+위 / -아래) — `scroll` 프레임에 그대로 넣는다.
///
/// 절대 위치 명령을 새로 만들지 않고 **지금 위치와의 차**로 옮긴다. 그래서 `scr` 을 안
/// 보내는 구서버(→ `scroll = 0`)에서도 프로토콜 추가 없이 동작한다(정확도만 떨어진다).
pub fn jump_delta(top: usize, scroll: usize, frac: f64) -> i32 {
    let max_scroll = (top + scroll) as f64;
    (round_half_away((1.0 - frac) * max_scroll) - scroll as i64) as i32
}

/// `▲`/`▼` 한 번이 옮기는 양 — 반 화면(PgUp/PgDn 과 같다). 최소 1.
pub fn half_page(h: usize) -> i32 {
    (h / 2).max(1) as i32
}

/// 파이썬 `round()` 는 **짝수로 반올림**(banker's)이지만 정본이 넣는 값에서 `.5` 가
/// 나오는 경우가 실제로 있다(트랙 길이가 짝수이고 비율이 정확히 절반일 때) — 거기서
/// 갈리면 썸이 한 칸 어긋난다. 그래서 반올림 규칙을 **픽스처로 확인**하고 맞춘다.
///
/// 확인 결과 정본과 어긋나는 경우가 나오면 이 함수를 바꾼다(테스트가 먼저 운다).
fn round_half_away(v: f64) -> i64 {
    // Rust 의 `f64::round` 는 half-away-from-zero, 파이썬은 half-to-even 이다.
    // 픽스처 대조가 이 선택을 검증한다 — 여기서 갈리면 `scrollbar_conformance` 가 운다.
    let floor = v.floor();
    let diff = v - floor;
    if (diff - 0.5).abs() < f64::EPSILON {
        // 정확히 .5 — 파이썬과 같이 **짝수 쪽**으로.
        let f = floor as i64;
        if f % 2 == 0 { f } else { f + 1 }
    } else {
        v.round() as i64
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_short_pane_gets_no_bar() {
        assert!(chars(2, 0, 0).is_empty(), "높이 2 는 미표시다");
        assert_eq!(chars(3, 0, 0).len(), 3);
        assert_eq!(hit(2, 0), None);
    }

    #[test]
    fn the_thumb_fills_the_track_without_scrollback() {
        let bar: String = chars(5, 0, 0).into_iter().collect();
        assert_eq!(bar, "▲███▼", "스크롤백이 없으면 썸이 트랙 전체다");
    }

    #[test]
    fn the_arrows_move_half_a_page() {
        assert_eq!(half_page(10), 5);
        assert_eq!(half_page(1), 1, "0 이 되면 탭이 아무 일도 안 한다");
    }

    /// 점프는 **차이**로 옮긴다 — 맨 위를 누르면 지금 스크롤만큼 더 올라간다.
    #[test]
    fn a_jump_is_a_difference_not_a_position() {
        assert_eq!(jump_delta(100, 0, 0.0), 100, "맨 위로 = 100줄 위로");
        assert_eq!(jump_delta(100, 0, 1.0), 0, "이미 맨 아래면 움직일 것이 없다");
        assert_eq!(jump_delta(50, 50, 1.0), -50, "맨 아래로 = 올라간 만큼 내려온다");
    }
}
