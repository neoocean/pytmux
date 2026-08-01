//! 루프백 TCP 로 붙는 경로 — **Windows 가 쓰는 전송**을 어느 호스트에서나 검증한다.
//!
//! Windows 서버는 AF_UNIX 대신 루프백 TCP 로 듣고, 실제 포트를 포트파일에 게시하며,
//! 토큰을 필수로 요구한다. 그 세 가지는 OS 와 무관한 코드라 macOS 에서도 그대로 돈다 —
//! 여기서 안 잡으면 Windows 박스에 가서야 안다.
//!
//! 진짜 pytmux 서버 대신 **프레임만 흉내 내는 가짜 서버**를 쓴다. 확인하려는 것이
//! 서버 로직이 아니라 붙는 절차(포트 해석 → 토큰 → hello)이기 때문이다.

use std::io::{BufReader, BufWriter};
use std::net::TcpListener;
use std::path::PathBuf;

use proto::endpoint::Endpoint;
use proto::framing::{read_frame, write_frame};
use proto::message::HANDSHAKE_MAX_FRAME;
use proto::{Connection, ServerMessage};

struct Scratch(PathBuf);

impl Scratch {
    fn new(name: &str) -> Self {
        let dir = std::env::temp_dir().join(format!("pytmux-tcp-test-{name}"));
        let _ = std::fs::remove_dir_all(&dir);
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

fn endpoint_for(scratch: &Scratch, port: u16) -> Endpoint {
    Endpoint::Tcp {
        host: "127.0.0.1".into(),
        port,
        portfile: scratch.path("default.port"),
        token: scratch.path("default.token"),
    }
}

#[test]
fn attaches_over_loopback_tcp_using_the_portfile_and_token() {
    let scratch = Scratch::new("attach");
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    // 서버가 실제 포트를 게시하고(에페메럴이라 클라는 이 파일로만 안다) 토큰을 둔다.
    std::fs::write(scratch.path("default.port"), format!("{port}\n")).unwrap();
    std::fs::write(scratch.path("default.token"), "s3cr3t\n").unwrap();

    let server = std::thread::spawn(move || {
        let (sock, _) = listener.accept().unwrap();
        let mut reader = BufReader::new(sock.try_clone().unwrap());
        let hello = read_frame(&mut reader, HANDSHAKE_MAX_FRAME).unwrap();
        // 붙자마자 layout → 패널별 screen 을 보낸다(진짜 서버와 같은 순서).
        let mut writer = BufWriter::new(sock);
        write_frame(
            &mut writer,
            &serde_json::json!({"t":"layout","cols":80,"rows":24,
                                "panes":[{"id":1,"x":0,"y":0,"w":80,"h":24}],"active":1}),
        )
        .unwrap();
        write_frame(
            &mut writer,
            &serde_json::json!({"t":"screen","pane":1,"rows":[[["hi",{}]]],"cursor":null}),
        )
        .unwrap();
        // 클라가 다 읽기 전에 소켓을 닫으면 EOF 가 먼저 도착한다.
        std::thread::sleep(std::time::Duration::from_millis(300));
        hello
    });

    // 포트 0 = "포트파일을 읽어라"는 서버 규약. 그 해석까지 여기서 검증된다.
    let mut conn = Connection::attach_to_endpoint(&endpoint_for(&scratch, 0), 80, 24).unwrap();

    let frame = conn.first_frame(std::time::Duration::from_secs(5)).unwrap();
    assert_eq!(frame.layout.map(|l| l.cols), Some(80));
    assert_eq!(frame.screens.len(), 1);

    let hello = server.join().unwrap();
    assert_eq!(hello["t"], "hello");
    assert_eq!(hello["token"], "s3cr3t", "토큰이 hello 에 실려야 한다");
    assert_eq!(hello["cols"], 80);

    // 확정 포트가 기억돼야 진단에서 어디에 붙었는지 보인다.
    assert_eq!(conn.socket(), format!("tcp:127.0.0.1:{port}"));
}

#[test]
fn the_first_frame_includes_what_the_server_sends_after_the_screens() {
    // ★ 실측 결함(2026-07-28). 서버의 초기 순서는 layout → screen → status → blocks →
    // claude 인데, `first_frame` 은 마지막 screen 을 받는 즉시 돌아갔다. 그래서
    // `frame.blocks` 는 **구조적으로 늘 비어** 있었고, 그걸 찍는 `examples/attach` 는
    // 셸 통합이 멀쩡히 돌아도 항상 "블록 0개"라고 말했다.
    //
    // 오류가 아니라 **그럴듯한 0** 이라 더 나빴다 — 그 값을 믿고 ConPTY 가 OSC 를
    // 삼키는지까지 팠다(멀쩡했다). 여기서 잡는다.
    //
    // 소켓을 **바로 닫는** 것도 일부러다: 다 보내고 끝내는 서버에서 이미 손에 쥔
    // 프레임을 EOF 하나로 버리면 "붙었는데 아무것도 못 받았다"가 된다.
    let scratch = Scratch::new("aftershot");
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::fs::write(scratch.path("default.port"), format!("{port}\n")).unwrap();
    std::fs::write(scratch.path("default.token"), "tok\n").unwrap();

    let server = std::thread::spawn(move || {
        let (sock, _) = listener.accept().unwrap();
        let mut reader = BufReader::new(sock.try_clone().unwrap());
        let _ = read_frame(&mut reader, HANDSHAKE_MAX_FRAME).unwrap();
        let mut writer = BufWriter::new(sock);
        for msg in [
            serde_json::json!({"t":"layout","cols":80,"rows":24,
                               "panes":[{"id":7,"x":0,"y":0,"w":80,"h":24}],"active":7}),
            serde_json::json!({"t":"screen","pane":7,"rows":[[["hi",{}]]],"cursor":null}),
            serde_json::json!({"t":"status","tabs":[]}),
            serde_json::json!({"t":"blocks","pane":7,"blocks":[
                {"cmd":"ls -la","state":"done","exit":0,"cwd":"/tmp","start":1,"end":2}]}),
        ] {
            write_frame(&mut writer, &msg).unwrap();
        }
        // 다 보냈으면 바로 닫는다(진단용 가짜 서버가 실제로 이렇게 동작한다).
    });

    let mut conn = Connection::attach_to_endpoint(&endpoint_for(&scratch, 0), 80, 24).unwrap();
    let frame = conn.first_frame(std::time::Duration::from_secs(5)).unwrap();

    assert_eq!(frame.screens.len(), 1, "그림은 그대로 와야 한다");
    let blocks = frame.blocks(7);
    assert_eq!(blocks.len(), 1, "screen 뒤에 온 blocks 를 놓쳤다");
    assert_eq!(blocks[0].command, "ls -la");
    assert_eq!(blocks[0].badge(), "ok");

    server.join().unwrap();
}

#[test]
fn refuses_to_attach_over_tcp_without_a_token() {
    // 루프백은 같은 머신의 **다른 로컬 사용자**에게도 열려 있다 — 토큰이 유일한
    // 경계다. 없이 붙으면 서버가 어차피 거절하므로, 여기서 멈춰 원인을 알려 준다.
    let scratch = Scratch::new("notoken");
    std::fs::write(scratch.path("default.port"), "1\n").unwrap();
    let err = Connection::attach_to_endpoint(&endpoint_for(&scratch, 0), 80, 24).unwrap_err();
    let text = err.to_string();
    assert!(text.contains("인증 토큰"), "{text}");
    assert!(text.contains("default.token"), "어느 파일인지 알려야 한다: {text}");
}

#[test]
fn a_missing_portfile_is_reported_as_such() {
    // 서버가 죽으며 포트파일을 지운 경우. "연결 실패"보다 원인이 또렷하다.
    let scratch = Scratch::new("noport");
    std::fs::write(scratch.path("default.token"), "tok\n").unwrap();
    let err = Connection::attach_to_endpoint(&endpoint_for(&scratch, 0), 80, 24).unwrap_err();
    let text = err.to_string();
    assert!(text.contains("포트"), "{text}");
    // 문장이 파일을 가리키면 값도 파일이어야 한다 — 토큰 쪽과 같은 규율이다.
    assert!(
        text.contains("default.port"),
        "어느 파일을 못 읽었는지 알려야 한다: {text}"
    );
}

#[test]
fn a_dead_port_is_named_in_the_failure() {
    // 죽은 서버가 남긴 stale 포트파일. **어느 포트를 물었는지가 유일한 단서**다:
    // Windows 에서는 리스너 없는 루프백 connect 가 거절이 아니라 타임아웃으로
    // 돌아와(방화벽이 SYN 을 버린다) 증상만 보면 원인을 못 가른다. 설정값(0)을
    // 찍으면 "포트파일을 못 읽었다"와 같은 문장이 되어 진단이 붕괴한다.
    let scratch = Scratch::new("deadport");
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    drop(listener); // 아무도 안 듣는 포트

    std::fs::write(scratch.path("default.port"), format!("{port}\n")).unwrap();
    std::fs::write(scratch.path("default.token"), "tok\n").unwrap();

    let err = Connection::attach_to_endpoint(&endpoint_for(&scratch, 0), 80, 24).unwrap_err();
    let text = err.to_string();
    assert!(
        text.contains(&format!("tcp:127.0.0.1:{port}")),
        "실제로 시도한 포트가 있어야 한다: {text}"
    );
    assert!(
        !text.contains("tcp:127.0.0.1:0"),
        "설정값(0)이 아니라 확정 포트로 말해야 한다: {text}"
    );
}

#[test]
fn a_message_survives_the_tcp_round_trip() {
    let scratch = Scratch::new("roundtrip");
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::fs::write(scratch.path("default.token"), "tok\n").unwrap();

    std::thread::spawn(move || {
        let (sock, _) = listener.accept().unwrap();
        let mut reader = BufReader::new(sock.try_clone().unwrap());
        let _ = read_frame(&mut reader, HANDSHAKE_MAX_FRAME);
        let mut writer = BufWriter::new(sock);
        write_frame(&mut writer, &serde_json::json!({"t":"error","msg":"그런 탭 없음"})).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(200));
    });

    let mut conn =
        Connection::attach_to_endpoint(&endpoint_for(&scratch, port), 80, 24).unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    loop {
        assert!(std::time::Instant::now() < deadline, "메시지가 안 왔다");
        if let Some(ServerMessage::Error { msg }) = conn.next_message().unwrap() {
            assert_eq!(msg, "그런 탭 없음");
            break;
        }
    }
}
