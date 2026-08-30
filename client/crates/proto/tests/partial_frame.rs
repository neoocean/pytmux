//! **프레임 도중에 멈추는 서버** — 읽기 타임아웃이 스트림을 어긋내지 않는지 본다.
//!
//! # 무엇을 재나 (pytmux-169)
//!
//! 붙는 클라의 소켓에는 250ms 읽기 타임아웃이 걸려 있다(`client.rs`). 타임아웃은
//! "끊겼다"가 아니라 "아직 없다"라서 `next_message` 가 `Ok(None)` 을 돌려주고 호출부가
//! 다시 부르는데, 그 사이에 **이미 읽은 바이트가 사라지면 스트림 위치를 잃는다** —
//! 다음 호출이 JSON 본문 한복판을 4바이트 길이 프리픽스로 읽고, 그 값이 상한을 넘어
//! 연결이 끊긴다. 사용자가 보는 것은 창 아래의 붉은 띠
//! (`Disconnected: Frame too large: 577004915 bytes`)와 탭 없는 탭바다.
//!
//! 실측 조건은 **트리가 막 바뀐 뒤(새 패널의 셸이 첫 출력을 쏟는 동안) 붙는 부착**이었다 —
//! 그때 서버의 첫 화면 프레임이 커서 한 번에 안 오고, 그 틈이 250ms 를 넘는다.
//!
//! # 왜 단위 시험만으로는 모자라나
//!
//! `framing.rs` 안의 오라클은 대본대로 타임아웃을 내는 가짜 리더로 **바이트 단위**를
//! 재고, 여기서는 **진짜 소켓 + 진짜 `Connection`** 으로 같은 것을 재서 250ms 타임아웃이
//! 실제로 그 자리에 걸려 있는지까지 함께 본다. 전송이 TCP 인 것은 편의다(`tcp_attach.rs`
//! 와 같은 이유 — 어느 호스트에서나 돈다). 결함은 전송이 아니라 **읽는 쪽**에 있었다.

use std::io::Write;
use std::net::TcpListener;
use std::path::PathBuf;

use proto::endpoint::Endpoint;
use proto::framing::read_frame;
use proto::message::HANDSHAKE_MAX_FRAME;
use proto::{Connection, ServerMessage};

struct Scratch(PathBuf);

impl Scratch {
    fn new(name: &str) -> Self {
        use std::sync::atomic::{AtomicU64, Ordering};
        static SEQ: AtomicU64 = AtomicU64::new(0);
        // ⛔ **고정 경로를 쓰지 않는다**(pytmux-424) — 같은 기계에서 `cargo test` 가
        //    둘 돌면 뒤엣것의 `remove_dir_all` 이 앞엣것의 트리를 **런 도중에** 지우고,
        //    그 증상은 엉뚱한 자리의 패닉이라 「부하 플레이크」로 읽힌다.
        let dir = std::env::temp_dir().join(format!(
            "pytmux-partial-frame-{name}-{}-{}",
            std::process::id(),
            SEQ.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        Self(dir)
    }
    fn path(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[test]
fn a_server_that_stalls_mid_frame_does_not_desync_the_client() {
    let scratch = Scratch::new("stall");
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::fs::write(scratch.path("default.port"), format!("{port}\n")).unwrap();
    std::fs::write(scratch.path("default.token"), "tok\n").unwrap();

    let server = std::thread::spawn(move || {
        let (mut sock, _) = listener.accept().unwrap();
        let mut reader = std::io::BufReader::new(sock.try_clone().unwrap());
        let _ = read_frame(&mut reader, HANDSHAKE_MAX_FRAME).unwrap();

        // layout 을 **반만** 보내고 읽기 타임아웃(250ms)보다 오래 쉰다. 진짜 서버가
        // 큰 화면 프레임을 쓰는 동안 벌어지는 일과 같은 모양이다.
        let layout = serde_json::to_vec(&serde_json::json!({
            "t": "layout", "cols": 80, "rows": 24,
            "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24}], "active": 1
        }))
        .unwrap();
        sock.write_all(&(layout.len() as u32).to_be_bytes()).unwrap();
        sock.write_all(&layout[..8]).unwrap();
        sock.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(600));
        sock.write_all(&layout[8..]).unwrap();

        // 그 뒤의 프레임도 온전히 읽혀야 한다 — 어긋난 스트림은 여기서 드러난다.
        let screen = serde_json::to_vec(&serde_json::json!({
            "t": "screen", "pane": 1, "rows": [[["hi", {}]]], "cursor": null
        }))
        .unwrap();
        sock.write_all(&(screen.len() as u32).to_be_bytes()).unwrap();
        sock.write_all(&screen).unwrap();
        sock.flush().unwrap();
        // 클라가 다 읽기 전에 닫으면 EOF 가 먼저 도착한다.
        std::thread::sleep(std::time::Duration::from_millis(1000));
    });

    let endpoint = Endpoint::Tcp {
        host: "127.0.0.1".into(),
        port: 0,
        portfile: scratch.path("default.port"),
        token: scratch.path("default.token"),
    };
    let mut conn = Connection::attach_to_endpoint(&endpoint, 80, 24).unwrap();

    let start = std::time::Instant::now();
    let mut seen: Vec<&str> = Vec::new();
    while start.elapsed() < std::time::Duration::from_secs(5) && seen.len() < 2 {
        match conn.next_message() {
            Ok(None) => continue,
            Ok(Some(ServerMessage::Layout(layout))) => {
                assert_eq!(layout.cols, 80);
                seen.push("layout");
            }
            Ok(Some(ServerMessage::Screen(screen))) => {
                assert_eq!(screen.pane, 1);
                seen.push("screen");
            }
            Ok(Some(_)) => {}
            // 고치기 전에는 여기로 떨어졌다: "프레임이 너무 크다: 574239020 바이트" —
            // 서버가 그런 프레임을 보낸 적이 없다. 본문을 길이로 읽은 것이다.
            Err(e) => panic!("스트림이 어긋났다: {e} (지금까지 {seen:?})"),
        }
    }
    assert_eq!(seen, vec!["layout", "screen"], "반만 온 프레임 뒤가 통째로 밀렸다");

    let _ = server.join();
}

#[test]
fn the_first_frame_survives_a_stall_in_the_middle_of_it() {
    // 같은 것을 **부착 직후 경로**(`first_frame`)로 잰다. 사용자가 실제로 본 화면이
    // 이 경로의 결과물이다 — 여기서 어긋나면 탭바가 `(no tabs)` 로 굳고 아래에 붉은
    // 띠가 뜬다(pytmux-169 의 그 프레임).
    let scratch = Scratch::new("firstframe");
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::fs::write(scratch.path("default.port"), format!("{port}\n")).unwrap();
    std::fs::write(scratch.path("default.token"), "tok\n").unwrap();

    let server = std::thread::spawn(move || {
        let (mut sock, _) = listener.accept().unwrap();
        let mut reader = std::io::BufReader::new(sock.try_clone().unwrap());
        let _ = read_frame(&mut reader, HANDSHAKE_MAX_FRAME).unwrap();

        let mut wire = Vec::new();
        for msg in [
            serde_json::json!({"t":"layout","cols":80,"rows":24,
                               "panes":[{"id":1,"x":0,"y":0,"w":40,"h":24},
                                        {"id":2,"x":40,"y":0,"w":40,"h":24}],"active":1}),
            serde_json::json!({"t":"screen","pane":1,"rows":[[["one",{}]]],"cursor":null}),
            serde_json::json!({"t":"screen","pane":2,"rows":[[["two",{}]]],"cursor":null}),
        ] {
            let payload = serde_json::to_vec(&msg).unwrap();
            wire.extend_from_slice(&(payload.len() as u32).to_be_bytes());
            wire.extend_from_slice(&payload);
        }
        // 새 패널의 첫 출력이 쏟아지는 동안처럼, 한복판에서 멈췄다 이어 쓴다.
        let cut = wire.len() / 2;
        sock.write_all(&wire[..cut]).unwrap();
        sock.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(600));
        sock.write_all(&wire[cut..]).unwrap();
        sock.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(1000));
    });

    let endpoint = Endpoint::Tcp {
        host: "127.0.0.1".into(),
        port: 0,
        portfile: scratch.path("default.port"),
        token: scratch.path("default.token"),
    };
    let mut conn = Connection::attach_to_endpoint(&endpoint, 80, 24).unwrap();
    let frame = conn
        .first_frame(std::time::Duration::from_secs(5))
        .expect("첫 화면을 못 받았다");

    assert_eq!(frame.layout.as_ref().map(|l| l.panes.len()), Some(2), "탭/패널이 통째로 비었다");
    assert_eq!(frame.screens.len(), 2, "패널 화면이 다 안 왔다");

    let _ = server.join();
}
