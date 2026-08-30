//! `ime_badge` — 자리 고르기(pytmux-392). 격자만으로 전부 잰다.

use super::*;

/// 글자가 든 칸을 문자열로 그려 `blank` 술어를 만든다. `#` = 글자, `.` = 빈 칸.
fn grid<'a>(rows: &'a [&'a str]) -> impl Fn(usize, usize) -> bool + 'a {
    move |col: usize, row: usize| {
        rows.get(row)
            .and_then(|r| r.chars().nth(col))
            .map(|c| c == '.')
            .unwrap_or(true)
    }
}

#[test]
fn an_empty_row_puts_the_badge_at_the_canon_spot() {
    let rows = ["..........", ".........."];
    let spot = badge_spot(0, 0, 10, (0, 2), 4, 0, grid(&rows)).unwrap();
    assert_eq!((spot.col, spot.row), (6, 0), "오른쪽 끝 정렬이 아니다");
    assert!(!spot.dodged && !spot.overlaps);
}

#[test]
fn the_reserved_right_edge_is_left_alone() {
    // 정본은 탭 닫기 `[x]` 자리를 비운다 — 그 규칙은 그대로 쓴다.
    let rows = [".........."];
    let spot = badge_spot(0, 0, 10, (0, 1), 4, 4, grid(&rows)).unwrap();
    assert_eq!(spot.col, 2);
}

#[test]
fn text_under_the_canon_spot_makes_the_badge_step_left() {
    // 기준 자리(6..10)에 글자가 있다 → 왼쪽으로 조금씩 물러난다.
    let rows = ["......##..", ".........."];
    let spot = badge_spot(0, 0, 10, (0, 2), 4, 0, grid(&rows)).unwrap();
    assert_eq!(spot.row, 0, "같은 줄에 자리가 있는데 줄을 옮겼다");
    assert!(spot.col <= 2, "글자를 안 피했다: col={}", spot.col);
    assert!(spot.dodged, "비켜섰는데 그 사실을 안 남겼다");
    assert!(!spot.overlaps);
}

#[test]
fn a_full_row_sends_the_badge_one_row_up() {
    // 커서 줄이 통째로 찼다 → **위** 줄로. 아래가 아닌 이유는 프롬프트가 아래에서
    // 자라기 때문이다(그 자리는 곧 덮인다).
    let rows = ["..........", "##########", ".........."];
    let spot = badge_spot(1, 0, 10, (0, 3), 4, 0, grid(&rows)).unwrap();
    assert_eq!(spot.row, 0, "위 줄로 안 갔다");
    assert!(spot.dodged);
}

#[test]
fn when_the_row_above_is_also_full_it_tries_below() {
    let rows = ["##########", "##########", ".........."];
    let spot = badge_spot(1, 0, 10, (0, 3), 4, 0, grid(&rows)).unwrap();
    assert_eq!(spot.row, 2);
}

#[test]
fn a_completely_full_pane_still_shows_the_badge() {
    // ⛔ 배지가 사라지면 「지금 한글인가」를 알 길이 없다 — 겹치는 것이 없는 것보다 낫다.
    let rows = ["##########", "##########", "##########"];
    let spot = badge_spot(1, 0, 10, (0, 3), 4, 0, grid(&rows)).unwrap();
    assert_eq!((spot.col, spot.row), (6, 1), "기준 자리로 안 돌아왔다");
    assert!(spot.overlaps, "겹친 사실을 안 남겼다");
}

#[test]
fn a_pane_too_narrow_for_the_badge_draws_nothing() {
    // 정본도 이때는 안 그린다(`badge_span` 이 None) — 그 규칙과 같게 둔다.
    let rows = ["..."];
    assert!(badge_spot(0, 0, 3, (0, 1), 4, 0, grid(&rows)).is_none());
    // 오른쪽 예약이 폭을 다 먹는 경우도 같다.
    assert!(badge_spot(0, 0, 6, (0, 1), 4, 4, grid(&rows)).is_none());
}

#[test]
fn the_badge_stays_inside_the_active_pane() {
    // 좌우 분할에서 활성 패널이 오른쪽이면 왼쪽 패널로 넘어가지 않는다.
    let rows = ["....########", "............"];
    let spot = badge_spot(0, 4, 12, (0, 2), 4, 0, grid(&rows)).unwrap();
    assert!(spot.col >= 4, "남의 패널로 넘어갔다: col={}", spot.col);
}
