//! 서버 연결을 별도 스레드로 돌리고 메시지를 채널로 넘긴다.
//!
//! # 왜 스레드인가
//!
//! TUI 런타임의 이벤트 루프는 크로스텀 입력을 250ms 타임아웃으로 폴링한다. 그 루프
//! 안에서 소켓을 블로킹 읽기 하면 입력이 그만큼 늦고, 반대로 소켓을 논블로킹으로 돌리면
//! 바쁜 대기가 된다. 읽기를 스레드로 빼면 둘 다 피할 수 있다 — 소켓은 마음껏 블로킹하고,
//! 루프는 채널을 즉시(`try_recv`) 훑는다.
//!
//! # 왜 명령은 같은 스레드에서 보내지 않나
//!
//! 명령 송신은 뷰가 하는 일이라 메인 쪽에 있어야 한다. 그래서 쓰기 절반만 메인이 들고
//! 있고(`CommandSink`), 읽기 절반은 스레드가 가져간다. `UnixStream` 은 복제가 되므로
//! 한 연결의 양쪽을 이렇게 나눠 쥘 수 있다.

use std::sync::mpsc::{Receiver, TryRecvError, channel};
use std::thread;

use base::i18n::t;

use crate::client::CommandSink;
use crate::command::Outgoing;
use crate::message::ServerMessage;
use crate::{AttachError, Connection};

/// 읽기 스레드가 보내는 것.
#[derive(Debug)]
pub enum LinkEvent {
    Message(Box<ServerMessage>),
    /// 연결이 끝났다. 사유를 함께 준다.
    Ended(String),
}

/// [`ServerLink::detached`] 가 보낸 것을 쌓아 두는 자리.
pub type Sent = std::sync::Arc<std::sync::Mutex<Vec<Outgoing>>>;

/// 서버와의 연결. 읽기는 스레드가, 쓰기는 이쪽이 담당한다.
pub struct ServerLink {
    events: Receiver<LinkEvent>,
    socket: String,
    /// 소켓 대신 **기록**하는 자리([`ServerLink::detached`]). 평소에는 `None`.
    record: Option<Sent>,
    /// 명령을 보내는 쪽. 연결이 없는 테스트에서는 `None`.
    sink: Option<CommandSink>,
}

impl ServerLink {
    /// 서버를 찾아 붙고 읽기 스레드를 띄운다.
    pub fn attach(cols: u16, rows: u16) -> Result<Self, AttachError> {
        let conn = Connection::attach(cols, rows)?;
        Ok(Self::from_connection(conn))
    }

    /// **지정된** 엔드포인트에 붙는다. 경로면 unix 소켓, `tcp:host:port` 면 루프백 TCP.
    ///
    /// 런처(`pytmux --native`)가 이미 고른 서버를 그대로 넘길 때 쓴다 — 여기서 다시
    /// 찾으면 사용자가 지목한 것과 다른 서버에 붙을 수 있다.
    pub fn attach_to(spec: &str, cols: u16, rows: u16) -> Result<Self, AttachError> {
        let conn = Connection::attach_to(std::path::Path::new(spec), cols, rows)?;
        Ok(Self::from_connection(conn))
    }

    fn from_connection(mut conn: Connection) -> Self {
        let socket = conn.socket();
        // 쓰기 절반을 먼저 떼어 낸다 — 아래에서 conn 을 스레드로 넘기기 때문이다.
        let sink = conn.split_sink().ok();
        let (tx, events) = channel();
        thread::spawn(move || {
            loop {
                match conn.next_message() {
                    // 타임아웃(= 아직 없다). 서버는 사용자가 가만히 있으면 조용하다.
                    Ok(None) => continue,
                    Ok(Some(msg)) => {
                        if tx.send(LinkEvent::Message(Box::new(msg))).is_err() {
                            break; // 메인이 끝났다
                        }
                    }
                    Err(e) => {
                        let _ = tx.send(LinkEvent::Ended(e.to_string()));
                        break;
                    }
                }
            }
        });
        Self {
            events,
            socket,
            sink,
            record: None,
        }
    }

    /// **소켓 없이** 만든 링크 — 보낸 것을 기록만 하고, 받을 것은 테스트가 밀어 넣는다.
    ///
    /// # 왜 프로덕션 코드에 있나
    ///
    /// 뷰를 통째로 세워 키를 먹이는 오라클을 위해서다. 그런 오라클이 없으면 **뷰 배선이
    /// 통째로 빠져도 아무 테스트가 안 운다** — 이 저장소가 실제로 그 자리에 두 번 섰다
    /// (GUI `pump()` 누락, 2026-07-29 G8p). 링크를 흉내 내는 대신 **진짜 링크를 소켓 없이**
    /// 만들면 `send`·`drain` 이 평소 경로 그대로 돌아, 테스트가 지나는 길과 사용자가
    /// 지나는 길이 같아진다.
    ///
    /// 돌려주는 셋: 링크 · 받을 것을 밀어 넣는 쪽 · 보낸 것이 쌓이는 자리.
    pub fn detached(socket: &str) -> (Self, std::sync::mpsc::Sender<LinkEvent>, Sent) {
        let (tx, events) = channel();
        let record: Sent = Default::default();
        (
            Self {
                events,
                socket: socket.to_owned(),
                sink: None,
                record: Some(record.clone()),
            },
            tx,
            record,
        )
    }

    /// 서버로 하나를 보낸다. 실패하면 사유를 돌려준다(연결이 끊긴 경우 등).
    ///
    /// 종류(명령·입력·스크롤)는 [`Outgoing`] 이 안다. 여기서 갈라 부르면 이벤트 루프가
    /// 종류별로 큐를 비우게 되고, 그 순간 사용자가 한 순서가 뒤집힌다.
    ///
    /// 보낸 뒤 화면을 직접 고치지 않는다 — 서버가 갱신을 보내 준다.
    pub fn send(&mut self, item: &Outgoing) -> Result<(), String> {
        // 기록판(`detached`)은 **성공한다** — 실패로 두면 뷰가 `ended` 를 세워, 오라클이
        // 보려던 배선이 첫 항목에서 끊긴다.
        if let Some(record) = self.record.as_ref() {
            record.lock().map_err(|_| t("기록판이 망가졌다").to_owned())?.push(item.clone());
            return Ok(());
        }
        match self.sink.as_mut() {
            Some(sink) => sink.send_outgoing(item).map_err(|e| e.to_string()),
            None => Err(t("보낼 연결이 없다").into()),
        }
    }

    pub fn socket(&self) -> &str {
        &self.socket
    }

    /// 지금까지 도착한 것을 전부 꺼낸다. **기다리지 않는다**.
    ///
    /// 한 번에 몰아 꺼내는 이유: 서버가 한 프레임에 layout + 패널별 screen + status 를
    /// 연달아 보내므로, 하나씩 처리하고 매번 다시 그리면 **반쯤 그려진 화면**이 보인다.
    pub fn drain(&self) -> Vec<LinkEvent> {
        let mut out = Vec::new();
        loop {
            match self.events.try_recv() {
                Ok(event) => out.push(event),
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    // 스레드가 Ended 를 못 보내고 죽은 경우(패닉 등)의 백스톱.
                    if !out
                        .iter()
                        .any(|e| matches!(e, LinkEvent::Ended(_)))
                    {
                        out.push(LinkEvent::Ended(t("읽기 스레드가 끝났다").into()));
                    }
                    break;
                }
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc::channel;

    /// `drain` 의 계약만 따로 본다(실 소켓 없이).
    fn link_with(events: Vec<LinkEvent>) -> ServerLink {
        let (tx, rx) = channel();
        for e in events {
            tx.send(e).unwrap();
        }
        drop(tx); // 송신측 종료 = 스레드가 끝난 상태
        ServerLink {
            events: rx,
            socket: "test".into(),
            sink: None,
            record: None,
        }
    }

    #[test]
    fn sending_without_a_connection_reports_instead_of_panicking() {
        let mut link = link_with(Vec::new());
        let item = Outgoing::Command(crate::command::Command::NextWindow);
        assert!(link.send(&item).is_err());
    }

    #[test]
    fn drain_takes_everything_that_arrived_at_once() {
        // 한 프레임의 여러 메시지를 몰아 꺼내야 반쯤 그려진 화면을 피한다.
        let link = link_with(vec![
            LinkEvent::Message(Box::new(ServerMessage::Pong { ts: None })),
            LinkEvent::Message(Box::new(ServerMessage::Pong { ts: None })),
        ]);
        let drained = link.drain();
        // 송신측이 닫혔으므로 끝 신호가 하나 더 붙는다.
        assert!(drained.len() >= 2);
    }

    #[test]
    fn a_dead_reader_thread_is_reported_even_without_an_end_message() {
        // 스레드가 패닉으로 죽으면 Ended 를 못 보낸다. 그때도 화면이 조용히 멈추면 안 된다.
        let link = link_with(Vec::new());
        let drained = link.drain();
        assert!(
            drained
                .iter()
                .any(|e| matches!(e, LinkEvent::Ended(_))),
            "끊김이 보고되지 않았다"
        );
    }

    #[test]
    fn a_detached_link_records_instead_of_sending() {
        // 뷰 오라클이 딛는 바닥이다 — 여기서 `send` 가 실패하면 뷰가 `ended` 를 세워
        // 오라클이 보려던 배선이 첫 항목에서 끊긴다.
        let (mut link, tx, sent) = ServerLink::detached("test");
        let item = Outgoing::Command(crate::command::Command::NextWindow);
        assert!(link.send(&item).is_ok(), "기록판이 보내기를 거절했다");
        assert_eq!(sent.lock().unwrap().as_slice(), &[item]);
        // 받는 쪽도 평소 경로 그대로다.
        tx.send(LinkEvent::Message(Box::new(ServerMessage::Pong { ts: None }))).unwrap();
        assert_eq!(link.drain().len(), 1);
    }

    #[test]
    fn end_is_not_duplicated_when_the_thread_reported_it() {
        let link = link_with(vec![LinkEvent::Ended("서버가 닫았다".into())]);
        let ended = link
            .drain()
            .into_iter()
            .filter(|e| matches!(e, LinkEvent::Ended(_)))
            .count();
        assert_eq!(ended, 1, "끊김 보고가 중복됐다");
    }
}
