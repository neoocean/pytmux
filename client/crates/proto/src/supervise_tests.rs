//! 감독자 배선 — 링크 사건이 상태기계를 거쳐 **실제로 다시 붙는가**(pytmux-585).
//! 소켓 없는 기록판 링크로 잰다. 실 소켓은 `tests/lifecycle_link.rs`(가짜 서버) ·
//! `tests/live_lifecycle.rs`(실 서버).

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use super::*;
use crate::link::LinkEvent;

type Pushers = Arc<Mutex<Vec<std::sync::mpsc::Sender<LinkEvent>>>>;

/// 부를 때마다 새 기록판 링크를 돌려주는 접속자 — 받은 이름을 적고, 밀어 넣을 쪽을 남긴다.
fn recording_connector(ok: bool) -> (Connector, Arc<Mutex<Vec<String>>>, Pushers) {
    let seen = Arc::new(Mutex::new(Vec::new()));
    let pushers: Pushers = Arc::default();
    let (s, p) = (seen.clone(), pushers.clone());
    let connect: Connector = Arc::new(move |spec, _, _| {
        s.lock().unwrap().push(spec.to_owned());
        if !ok {
            return Err("거절".to_owned());
        }
        let (link, tx, _sent) = ServerLink::detached(spec);
        p.lock().unwrap().push(tx);
        Ok(link)
    });
    (connect, seen, pushers)
}

fn until(
    sup: &mut Supervisor,
    link: &mut ServerLink,
    mut done: impl FnMut(&Happening) -> bool,
) -> Vec<Happening> {
    let deadline = Instant::now() + Duration::from_secs(10);
    let mut all = Vec::new();
    loop {
        let got = sup.pump(link, (80, 24));
        let hit = got.iter().any(&mut done);
        all.extend(got);
        if hit {
            return all;
        }
        assert!(Instant::now() < deadline, "시한 안에 안 왔다: {all:?}");
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn notices(all: &[Happening]) -> Vec<&str> {
    all.iter()
        .filter_map(|h| match h {
            Happening::Notice(t) | Happening::Ended(t) => Some(t.as_str()),
            _ => None,
        })
        .collect()
}

#[test]
fn an_unannounced_end_reconnects_to_the_origin_and_resyncs() {
    let (connect, seen, _p) = recording_connector(true);
    let (mut link, tx, _sent) = ServerLink::detached("/tmp/sup-a.sock");
    let mut sup = Supervisor::with_connector(true, connect);
    tx.send(LinkEvent::Ended("닫혔다".into())).unwrap();
    let all = until(&mut sup, &mut link, |h| matches!(h, Happening::Reconnected));
    assert!(matches!(all[0], Happening::Closed(ref r) if r == "닫혔다"), "{all:?}");
    assert_eq!(seen.lock().unwrap().as_slice(), ["/tmp/sup-a.sock"]);
    assert_eq!(notices(&all), [base::lifecycle::reconnected_resync()]);
    assert!(sup.ended().is_none());
}

#[test]
fn restarting_then_end_says_restart_done() {
    let (connect, _seen, _p) = recording_connector(true);
    let (mut link, tx, _sent) = ServerLink::detached("/tmp/sup-b.sock");
    let mut sup = Supervisor::with_connector(true, connect);
    tx.send(LinkEvent::Message(Box::new(ServerMessage::Restarting { relaunch: false })))
        .unwrap();
    tx.send(LinkEvent::Ended("execv".into())).unwrap();
    let all = until(&mut sup, &mut link, |h| {
        matches!(h, Happening::Notice(t) if t == base::lifecycle::restart_done())
    });
    assert_eq!(
        notices(&all),
        [base::lifecycle::server_restarting(), base::lifecycle::restart_done()]
    );
}

#[test]
fn a_dead_server_ends_as_server_lost_after_the_drop_budget() {
    let (connect, seen, _p) = recording_connector(false);
    let (mut link, tx, _sent) = ServerLink::detached("/tmp/sup-c.sock");
    let mut sup = Supervisor::with_connector(true, connect).with_delay(Duration::ZERO);
    tx.send(LinkEvent::Ended("EOF".into())).unwrap();
    let all = until(&mut sup, &mut link, |h| matches!(h, Happening::Ended(_)));
    assert_eq!(seen.lock().unwrap().len(), Budget::Drop.attempts() as usize);
    assert_eq!(sup.ended(), Some(&Ending::Lost));
    assert!(notices(&all).contains(&Ending::Lost.message().as_str()));
}

#[test]
fn bye_ends_as_terminated_without_trying() {
    let (connect, seen, _p) = recording_connector(true);
    let (mut link, tx, _sent) = ServerLink::detached("/tmp/sup-d.sock");
    let mut sup = Supervisor::with_connector(true, connect);
    tx.send(LinkEvent::Message(Box::new(ServerMessage::Bye))).unwrap();
    tx.send(LinkEvent::Ended("닫혔다".into())).unwrap();
    let all = sup.pump(&mut link, (80, 24));
    assert_eq!(sup.ended(), Some(&Ending::Terminated));
    assert!(!sup.attempting());
    assert!(seen.lock().unwrap().is_empty());
    assert!(notices(&all).contains(&Ending::Terminated.message().as_str()));
}

#[test]
fn a_handshake_refusal_ends_with_its_reason_and_other_errors_pass_through() {
    let (connect, seen, _p) = recording_connector(true);
    let (mut link, tx, _sent) = ServerLink::detached("/tmp/sup-e.sock");
    let mut sup = Supervisor::with_connector(true, connect);
    tx.send(LinkEvent::Message(Box::new(ServerMessage::Error {
        msg: "그런 탭 없음".into(),
        error: None,
        server_proto: None,
    })))
    .unwrap();
    tx.send(LinkEvent::Message(Box::new(ServerMessage::Error {
        msg: String::new(),
        error: Some("proto_mismatch".into()),
        server_proto: Some(2),
    })))
    .unwrap();
    tx.send(LinkEvent::Ended("닫혔다".into())).unwrap();
    let all = sup.pump(&mut link, (80, 24));
    assert!(
        matches!(&all[0], Happening::Message(m) if matches!(**m, ServerMessage::Error { ref msg, .. } if msg == "그런 탭 없음")),
        "{all:?}"
    );
    let ended = sup.ended().cloned();
    assert_eq!(
        ended,
        Some(Ending::Refused(Refusal::ProtoMismatch { server: Some(2), client: PROTO_VERSION }))
    );
    assert!(seen.lock().unwrap().is_empty(), "거절은 다시 붙지 않는다");
}

#[test]
fn manual_reconnect_replaces_the_link_and_stays_alive_on_failure() {
    let (connect, _seen, _p) = recording_connector(false);
    let (mut link, _tx, _sent) = ServerLink::detached("/tmp/sup-f.sock");
    let mut sup = Supervisor::with_connector(true, connect).with_delay(Duration::ZERO);
    let first = sup.manual(&mut link, (80, 24));
    assert_eq!(notices(&first), [base::lifecycle::reconnecting(false)]);
    let all = until(&mut sup, &mut link, |h| matches!(h, Happening::Error(_)));
    assert!(sup.ended().is_none(), "{all:?}");
}

#[test]
fn a_detached_link_never_reaches_a_socket() {
    let (link, _tx, _sent) = ServerLink::detached("/tmp/sup-g.sock");
    let sup = Supervisor::for_link(&link);
    assert!(sup.lifecycle().local);
}

#[test]
fn locality_follows_the_canonical_rule() {
    assert!(is_local("/tmp/x.sock"));
    assert!(is_local("tcp:127.0.0.1:0"));
    assert!(is_local("tcp:name@localhost:5"));
    assert!(!is_local("tcp:10.0.0.5:5000"));
}

#[test]
fn one_link_ends_once_even_though_drain_repeats_its_backstop() {
    // `drain` 은 읽기 스레드가 끝난 뒤 부를 때마다 백스톱 `Ended` 를 낸다. 그것을 EOF 여럿으로
    // 먹이면 강제 재접속 실패 뒤(정본: 연결 없이 남는다)에도 드롭 재접속이 저절로 돈다.
    let (connect, seen, _p) = recording_connector(false);
    let (mut link, tx, _sent) = ServerLink::detached("/tmp/sup-h.sock");
    drop(tx); // 송신측이 사라졌다 = 스레드가 끝난 링크
    let mut sup = Supervisor::with_connector(true, connect).with_delay(Duration::ZERO);
    let all = until(&mut sup, &mut link, |h| matches!(h, Happening::Ended(_)));
    for _ in 0..20 {
        assert!(sup.pump(&mut link, (80, 24)).is_empty());
    }
    assert_eq!(seen.lock().unwrap().len(), Budget::Drop.attempts() as usize, "{all:?}");
}
