use super::*;

#[test]
fn a_fast_crash_counts_up_and_gives_up_after_five() {
    assert_eq!(decide(None, 1.0), Decision::Relaunch(1));
    assert_eq!(decide(Some("1"), 1.0), Decision::Relaunch(2));
    assert_eq!(decide(Some("4"), 1.0), Decision::Relaunch(5));
    assert_eq!(decide(Some("5"), 1.0), Decision::GiveUp);
}

#[test]
fn a_crash_after_a_long_run_starts_a_fresh_count() {
    assert_eq!(decide(Some("5"), LOOP_WINDOW), Decision::Relaunch(1));
    assert_eq!(decide(Some("5"), 3600.0), Decision::Relaunch(1));
}

#[test]
fn a_garbage_count_reads_as_zero_like_the_canon() {
    assert_eq!(count(Some("x")), 0);
    assert_eq!(decide(Some("x"), 1.0), Decision::Relaunch(1));
}

#[test]
fn the_texts_have_the_canonical_shape() {
    assert_eq!(log_header("2026-11-13 10:00:00", 42, "1.0"), "\n==== 2026-11-13 10:00:00 pid=42 ver=1.0 ====\n");
    assert!(giveup_text("/s/default.sock.client.crash.log").ends_with("트레이스백: /s/default.sock.client.crash.log\n"));
}
