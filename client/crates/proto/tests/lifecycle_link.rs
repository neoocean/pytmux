//! 접속 수명 — **실 소켓**으로 끊고 다시 붙는다(pytmux-585). 서버는 프레임만 흉내 내는
//! 가짜다(tcp_attach.rs 와 같은 까닭: 재는 것은 서버 로직이 아니라 클라의 반응이다).
//!
//! 여기서만 재는 것: Windows host 재시작은 후속 서버가 **새 에페메럴 포트**로 포트파일을
//! 덮는다. 다시 붙는 이름이 확정 포트(`tcp:127.0.0.1:<옛 포트>`)면 영영 못 붙는다 — 그래서
//! 감독자는 붙기 전 이름(`tcp:127.0.0.1:0`)을 넘기고 포트파일을 다시 읽어야 한다.

use std::io::{BufReader, BufWriter};
use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use proto::endpoint::Endpoint;
use proto::framing::{read_frame, write_frame};
use proto::message::HANDSHAKE_MAX_FRAME;
use proto::supervise::{Connector, Happening, Supervisor};
use proto::{Connection, ServerLink};

struct Scratch(PathBuf);

impl Scratch {
    fn new(name: &str) -> Self {
        use std::sync::atomic::{AtomicU64, Ordering};
        static SEQ: AtomicU64 = AtomicU64::new(0);
        let dir = std::env::temp_dir().join(format!(
            "pytmux-lc-{name}-{}-{}",
            std::process::id(),
            SEQ.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("default.token"), "s3cr3t\n").unwrap();
        Self(dir)
    }
    fn endpoint(&self) -> Endpoint {
        Endpoint::Tcp {
            host: "127.0.0.1".into(),
            port: 0,
            portfile: self.0.join("default.port"),
            token: self.0.join("default.token"),
        }
    }
    fn listen(&self) -> TcpListener {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::fs::write(self.0.join("default.port"), format!("{port}\n")).unwrap();
        listener
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// 받은 이름을 적고, 이 스크래치의 엔드포인트(포트파일을 **다시 읽는다**)로 붙는 접속자.
fn connector(endpoint: Endpoint, seen: Arc<Mutex<Vec<String>>>) -> Connector {
    Arc::new(move |spec, cols, rows| {
        seen.lock().unwrap().push(spec.to_owned());
        Connection::attach_to_endpoint(&endpoint, cols, rows)
            .map(ServerLink::from_connection)
            .map_err(|e| e.to_string())
    })
}

fn accept_hello(listener: &TcpListener) -> (std::net::TcpStream, serde_json::Value) {
    let (sock, _) = listener.accept().unwrap();
    let hello = read_frame(&mut BufReader::new(sock.try_clone().unwrap()), HANDSHAKE_MAX_FRAME).unwrap();
    (sock, hello)
}

fn send(sock: &std::net::TcpStream, value: serde_json::Value) {
    write_frame(&mut BufWriter::new(sock.try_clone().unwrap()), &value).unwrap();
}

fn pump_until(
    sup: &mut Supervisor,
    link: &mut ServerLink,
    mut done: impl FnMut(&Happening) -> bool,
) -> Vec<Happening> {
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut all = Vec::new();
    loop {
        let got = sup.pump(link, (80, 24));
        let hit = got.iter().any(&mut done);
        all.extend(got);
        if hit {
            return all;
        }
        assert!(Instant::now() < deadline, "시한 안에 안 왔다: {all:?}");
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[test]
fn a_host_mode_restart_on_a_new_port_is_followed_through_the_portfile() {
    let scratch = Scratch::new("restart");
    let first = scratch.listen();
    let mut link = ServerLink::from_connection(
        Connection::attach_to_endpoint(&scratch.endpoint(), 80, 24).unwrap(),
    );
    let (old, _hello) = accept_hello(&first);
    assert_eq!(link.origin(), "tcp:127.0.0.1:0", "붙기 전 이름(포트 미확정)이어야 한다");
    assert_ne!(link.socket(), link.origin(), "선행 조건: socket() 은 확정 포트다");
    let seen = Arc::new(Mutex::new(Vec::new()));
    let mut sup = Supervisor::with_connector(true, connector(scratch.endpoint(), seen.clone()));

    // 정본 `_restart_server_host`: 통지 → 후속 서버가 **새 포트**로 포트파일을 덮음 → 옛 서버 종료.
    send(&old, serde_json::json!({"t": "restarting"}));
    let second = scratch.listen();
    // 정본도 통지 뒤 0.1~0.2초 있다 닫는다(write_msg flush 틈) — 곧장 닫으면 RST 가 통지를
    // 앞지를 수 있다(tcp_attach.rs 의 error 시험과 같은 틈).
    std::thread::sleep(Duration::from_millis(200));
    drop(first);
    drop(old);
    let server = std::thread::spawn(move || {
        let (sock, hello) = accept_hello(&second);
        send(&sock, serde_json::json!({"t":"layout","cols":80,"rows":24,
            "panes":[{"id":7,"x":0,"y":0,"w":80,"h":24}],"active":7}));
        std::thread::sleep(Duration::from_millis(500));
        hello
    });
    let all = pump_until(&mut sup, &mut link, |h| {
        matches!(h, Happening::Notice(t) if t == base::lifecycle::restart_done())
    });
    assert!(all.iter().any(|h| matches!(h, Happening::Reconnected)), "{all:?}");
    assert!(seen.lock().unwrap().iter().all(|s| s == "tcp:127.0.0.1:0"), "{:?}", seen.lock().unwrap());
    // 새 링크의 첫 프레임이 실제로 흐른다(같은 퍼올리기에 실려 왔을 수 있다).
    if !all.iter().any(|h| matches!(h, Happening::Message(m) if m.kind() == "layout")) {
        pump_until(&mut sup, &mut link, |h| {
            matches!(h, Happening::Message(m) if m.kind() == "layout")
        });
    }
    let hello = server.join().unwrap();
    assert_eq!(hello["t"], "hello");
    assert_eq!(hello["token"], "s3cr3t", "다시 붙는 hello 에 토큰이 없다");
}

#[test]
fn an_unannounced_close_by_a_live_server_is_a_drop_and_resyncs() {
    let scratch = Scratch::new("drop");
    let listener = scratch.listen();
    let mut link = ServerLink::from_connection(
        Connection::attach_to_endpoint(&scratch.endpoint(), 80, 24).unwrap(),
    );
    let (sock, _) = accept_hello(&listener);
    let seen = Arc::new(Mutex::new(Vec::new()));
    let mut sup = Supervisor::with_connector(true, connector(scratch.endpoint(), seen));
    // 정본 `_drop_slow_client`: bye 없이 닫는다. 서버는 산다(같은 리스너가 다시 받는다).
    drop(sock);
    let server = std::thread::spawn(move || {
        let (sock, _) = accept_hello(&listener);
        std::thread::sleep(Duration::from_millis(300));
        drop(sock);
    });
    let all = pump_until(&mut sup, &mut link, |h| {
        matches!(h, Happening::Notice(t) if t == base::lifecycle::reconnected_resync())
    });
    assert!(all.iter().any(|h| matches!(h, Happening::Reconnected)), "{all:?}");
    assert!(sup.ended().is_none());
    server.join().unwrap();
}

#[test]
fn bye_and_a_handshake_refusal_end_without_a_second_connection() {
    for (frame, want) in [
        (serde_json::json!({"t": "bye"}), base::lifecycle::Ending::Terminated),
        (
            serde_json::json!({"t": "error", "error": "proto_mismatch", "server_proto": 9}),
            base::lifecycle::Ending::Refused(base::lifecycle::Refusal::ProtoMismatch {
                server: Some(9),
                client: proto::message::PROTO_VERSION,
            }),
        ),
    ] {
        let scratch = Scratch::new("end");
        let listener = scratch.listen();
        let mut link = ServerLink::from_connection(
            Connection::attach_to_endpoint(&scratch.endpoint(), 80, 24).unwrap(),
        );
        let (sock, _) = accept_hello(&listener);
        let seen = Arc::new(Mutex::new(Vec::new()));
        let mut sup = Supervisor::with_connector(true, connector(scratch.endpoint(), seen.clone()));
        send(&sock, frame);
        std::thread::sleep(Duration::from_millis(200));
        drop(sock);
        let want_text = want.message();
        pump_until(&mut sup, &mut link, |h| matches!(h, Happening::Ended(t) if *t == want_text));
        assert_eq!(sup.ended(), Some(&want));
        assert!(seen.lock().unwrap().is_empty(), "끝났는데 다시 붙었다");
    }
}
