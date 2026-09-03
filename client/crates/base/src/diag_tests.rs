//! `debug-stats` 판이 **값을 실제로 낸다**를 재는 자리(pytmux-457 관문).
//!
//! ⛔ 「판이 뜬다」로 초록을 만들지 않는다 — 이 이슈가 못박은 관문은 *"판의 값이
//! 실제로 움직인다는 오라클(프레임을 두 번 돌리면 프레임 수가 는다) — 값 없는 판을
//! 초록으로 접지 않는다"* 다.

use super::{FRAME_WINDOW, RuntimeStats};

fn some() -> RuntimeStats {
    RuntimeStats {
        pid: 4242,
        frames: 7,
        frame_ms: vec![12.0, 4.0, 8.0],
        glyph_cache: Some(311),
        scene_nodes: Some(1902),
        painted_cells: Some(4800),
        queue_depth: 2,
        rtt_ms: Some(11.5),
        rss: Some(64 * 1_048_576),
        grid: (120, 40),
        tabs: 3,
        panes: 5,
        screen_depth: 1,
    }
}

#[test]
fn the_panel_says_what_it_measured() {
    let lines = some().lines();
    let all = lines.join("\n");
    for needle in ["pid 4242", "120×40", "64.0 MB", "311", "1902", "4800", "11.5 ms"] {
        assert!(all.contains(needle), "{needle:?} 가 판에 없다:\n{all}");
    }
    assert!(lines.len() > 10, "줄이 {} 뿐이다 — 표가 안 만들어졌다", lines.len());
}

#[test]
fn a_frame_that_was_drawn_twice_shows_up_as_two() {
    // 이 이슈의 관문 그 줄이다.
    let mut stats = RuntimeStats { frames: 1, ..RuntimeStats::default() };
    let before = stats.lines().join("\n");
    stats.frames += 1;
    let after = stats.lines().join("\n");
    assert_ne!(before, after, "프레임을 한 번 더 그렸는데 표가 그대로다");
    assert!(after.contains(" 2"), "늘어난 프레임 수가 표에 없다:\n{after}");
}

#[test]
fn what_we_could_not_measure_says_so_instead_of_zero() {
    // ⛔ 「못 쟀다」와 「쟀는데 0」은 다른 사실이다. 0 으로 적으면 다음 사람이
    //    「글리프 캐시가 비었다」로 읽는다.
    let blank = RuntimeStats::default();
    let all = blank.lines().join("\n");
    assert!(!all.contains("글리프 캐시 0"), "못 잰 값을 0 으로 적었다:\n{all}");
    assert!(all.contains("못 쟀다"), "못 잰 것을 그렇게 안 적었다:\n{all}");
    assert!(
        all.contains("프레임 시간 표본이 아직 없다"),
        "표본이 없는데 0ms 로 적었다:\n{all}"
    );
}

#[test]
fn the_middle_frame_is_the_median_not_the_mean() {
    // 프레임 하나가 튀어도 나머지가 멀쩡하면 그것은 「느려졌다」가 아니다 —
    // 평균은 그 둘을 섞어 버린다.
    let stats = RuntimeStats {
        frame_ms: vec![8.0, 8.0, 8.0, 8.0, 400.0],
        ..RuntimeStats::default()
    };
    let (mid, max) = stats.frame_median_max().expect("표본이 있는데 없다고 한다");
    assert_eq!(mid, 8.0, "중앙값이 평균으로 흘렀다");
    assert_eq!(max, 400.0, "튄 값이 사라졌다 — 그것도 사실이다");
}

#[test]
fn the_frame_window_is_small_enough_to_stay_recent() {
    // 창이 지나치게 길면 「방금 무거워졌다」를 못 본다(이 상수의 존재 이유).
    assert!((10..=240).contains(&FRAME_WINDOW), "창 {FRAME_WINDOW} 는 값이 없다");
}
