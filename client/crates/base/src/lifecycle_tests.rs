//! 접속 수명 갈래 전수(pytmux-585). 정본과의 대조는 `proto/tests/lifecycle_conformance.rs`
//! 가 픽스처로 하고, 여기서는 정본이 «돌려 볼 수 없는» 갈래(시각 · 끝난 뒤 · 중복)를 잰다.

use super::*;

fn fresh() -> Lifecycle {
    Lifecycle::new(true)
}

fn ends(effects: &[Effect]) -> Option<&Ending> {
    effects.iter().find_map(|e| match e {
        Effect::End(why) => Some(why),
        _ => None,
    })
}

#[test]
fn a_plain_eof_tries_the_drop_budget_and_a_success_resyncs() {
    let mut lc = fresh();
    assert_eq!(lc.on(Input::Eof, 0.0), vec![Effect::Attempt(Budget::Drop)]);
    assert!(lc.attempting());
    assert_eq!(
        lc.on(Input::Attempted(true), 0.1),
        vec![Effect::ResetNet, Effect::Notice(reconnected_resync().to_owned())]
    );
    assert!(!lc.attempting());
    assert!(lc.ended().is_none());
}

#[test]
fn a_failed_drop_reconnect_is_server_lost() {
    let mut lc = fresh();
    lc.on(Input::Eof, 0.0);
    let out = lc.on(Input::Attempted(false), 0.5);
    assert_eq!(ends(&out), Some(&Ending::Lost));
    assert_eq!(lc.ended(), Some(&Ending::Lost));
}

#[test]
fn the_seventh_drop_inside_twenty_seconds_gives_up_without_trying() {
    let mut lc = fresh();
    for i in 0..DROP_MAX {
        let now = i as f64;
        assert_eq!(lc.on(Input::Eof, now), vec![Effect::Attempt(Budget::Drop)], "{i}");
        lc.on(Input::Attempted(true), now);
    }
    let out = lc.on(Input::Eof, 6.0);
    assert_eq!(out, vec![Effect::End(Ending::Lost)]);
}

#[test]
fn drops_older_than_the_window_do_not_count() {
    let mut lc = fresh();
    for i in 0..DROP_MAX {
        lc.on(Input::Eof, i as f64);
        lc.on(Input::Attempted(true), i as f64);
    }
    // 첫 드롭(0초)에서 20초가 지났다 — 창 안에는 다섯만 남는다.
    assert_eq!(lc.on(Input::Eof, 20.5), vec![Effect::Attempt(Budget::Drop)]);
}

#[test]
fn auto_off_ends_on_the_first_eof() {
    let mut lc = fresh();
    lc.auto = false;
    assert_eq!(lc.on(Input::Eof, 0.0), vec![Effect::End(Ending::Lost)]);
}

#[test]
fn restarting_is_announced_and_the_next_eof_waits_for_the_new_server() {
    let mut lc = fresh();
    assert_eq!(
        lc.on(Input::Restarting { relaunch: false }, 0.0),
        vec![Effect::Notice(server_restarting().to_owned())]
    );
    assert_eq!(lc.on(Input::Eof, 0.1), vec![Effect::Attempt(Budget::Restart)]);
    assert_eq!(
        lc.on(Input::Attempted(true), 2.0),
        vec![Effect::Notice(restart_done().to_owned())]
    );
    // 재시작 표식은 한 번 쓰고 사라진다 — 다음 EOF 는 평상 드롭이다.
    assert_eq!(lc.on(Input::Eof, 3.0), vec![Effect::Attempt(Budget::Drop)]);
}

#[test]
fn a_restart_eof_does_not_spend_the_drop_window() {
    let mut lc = fresh();
    for i in 0..10 {
        lc.on(Input::Restarting { relaunch: false }, i as f64);
        assert_eq!(lc.on(Input::Eof, i as f64), vec![Effect::Attempt(Budget::Restart)]);
        lc.on(Input::Attempted(true), i as f64);
    }
    assert_eq!(lc.on(Input::Eof, 10.0), vec![Effect::Attempt(Budget::Drop)]);
}

#[test]
fn a_failed_restart_reconnect_is_reconnect_failed() {
    let mut lc = fresh();
    lc.on(Input::Restarting { relaunch: false }, 0.0);
    lc.on(Input::Eof, 0.0);
    assert_eq!(lc.on(Input::Attempted(false), 6.0), vec![Effect::End(Ending::ReconnectFailed)]);
}

#[test]
fn relaunch_comes_only_after_the_in_place_reconnect_succeeded() {
    let mut lc = fresh();
    lc.on(Input::Restarting { relaunch: true }, 0.0);
    // EOF 에서 곧장 다시 띄우지 않는다(모듈 문서 DIVERGE — 두 번째 서버 경쟁).
    assert_eq!(lc.on(Input::Eof, 0.1), vec![Effect::Attempt(Budget::Restart)]);
    assert_eq!(
        lc.on(Input::Attempted(true), 1.0),
        vec![Effect::Notice(restart_done().to_owned()), Effect::Relaunch]
    );
}

#[test]
fn relaunch_is_dropped_when_the_reconnect_failed() {
    let mut lc = fresh();
    lc.on(Input::Restarting { relaunch: true }, 0.0);
    lc.on(Input::Eof, 0.1);
    let out = lc.on(Input::Attempted(false), 6.0);
    assert!(!out.contains(&Effect::Relaunch), "{out:?}");
}

#[test]
fn bye_ends_as_terminated_and_the_closing_eof_is_ignored() {
    let mut lc = fresh();
    assert_eq!(lc.on(Input::Bye, 0.0), vec![Effect::End(Ending::Terminated)]);
    assert!(lc.on(Input::Eof, 0.0).is_empty());
}

#[test]
fn a_refusal_ends_without_trying_again() {
    let mut lc = fresh();
    let out = lc.on(Input::Refused(Refusal::AuthFailed), 0.0);
    assert_eq!(out, vec![Effect::End(Ending::Refused(Refusal::AuthFailed))]);
    assert!(lc.on(Input::Eof, 0.0).is_empty());
}

#[test]
fn manual_reconnect_announces_and_uses_the_force_budget() {
    let mut lc = fresh();
    assert_eq!(
        lc.on(Input::Manual, 0.0),
        vec![Effect::Notice(reconnecting(false).to_owned()), Effect::Attempt(Budget::Force)]
    );
    // 옛 소켓을 닫아 깨운 리더의 EOF 는 조용히 버린다.
    assert!(lc.on(Input::Eof, 0.0).is_empty());
    // 진행 중이면 두 번째 요청은 무시.
    assert!(lc.on(Input::Manual, 0.0).is_empty());
    assert_eq!(
        lc.on(Input::Attempted(true), 0.5),
        vec![Effect::ResetNet, Effect::Notice(reconnected_resync().to_owned())]
    );
}

#[test]
fn a_failed_manual_reconnect_is_an_error_line_not_an_ending() {
    let mut lc = fresh();
    lc.on(Input::Manual, 0.0);
    assert_eq!(
        lc.on(Input::Attempted(false), 3.0),
        vec![Effect::Error(reconnect_failed_net().to_owned())]
    );
    assert!(lc.ended().is_none());
}

#[test]
fn manual_reconnect_revives_an_ended_window() {
    let mut lc = fresh();
    lc.on(Input::Bye, 0.0);
    assert!(lc.on(Input::Restarting { relaunch: false }, 0.0).is_empty());
    assert_eq!(lc.on(Input::Manual, 1.0)[1], Effect::Attempt(Budget::Force));
    assert!(lc.ended().is_none());
}

#[test]
fn degraded_recovery_is_only_for_remote_links() {
    let mut local = Lifecycle::new(true);
    assert!(local.on(Input::Degraded, 0.0).is_empty());
    let mut remote = Lifecycle::new(false);
    assert_eq!(
        remote.on(Input::Degraded, 0.0),
        vec![Effect::Notice(reconnecting(true).to_owned()), Effect::Attempt(Budget::Force)]
    );
    remote.auto = false;
    let mut off = remote.clone();
    off.on(Input::Attempted(true), 0.0);
    assert!(off.on(Input::Degraded, 1.0).is_empty());
}

#[test]
fn budgets_are_the_canonical_counts() {
    assert_eq!(Budget::Restart.attempts(), 300);
    assert_eq!(Budget::Force.attempts(), 150);
    assert_eq!(Budget::Drop.attempts(), 25);
}

#[test]
fn refusal_words_are_the_wire_words() {
    assert_eq!(
        Refusal::from_wire("proto_mismatch", Some(2), 1),
        Some(Refusal::ProtoMismatch { server: Some(2), client: 1 })
    );
    assert_eq!(Refusal::from_wire("auth_failed", None, 1), Some(Refusal::AuthFailed));
    assert_eq!(Refusal::from_wire("nope", None, 1), None);
    let text = Ending::Refused(Refusal::ProtoMismatch { server: None, client: 1 }).message();
    assert!(text.contains("서버 ?") && text.contains("클라 1"), "{text}");
}
