//! 링크를 지켜본다 — 끊기면 정본처럼 **스스로 다시 붙는다**(pytmux-585).
//!
//! 무엇을 언제 할지는 [`base::lifecycle`] 이 정한다(뷰도 소켓도 없는 상태기계). 여기서는
//! 그 결정을 소켓에 옮긴다:
//!
//! - 링크가 낸 것 가운데 수명에 관한 셋(`restarting` · `bye` · 핸드셰이크 `error`)과 읽기의
//!   끝(`LinkEvent::Ended`)을 상태기계에 먹인다. 나머지는 그대로 뷰로 흘린다.
//! - `Attempt(budget)` 이면 **스레드에서** 정본과 같은 횟수 × 20ms 로 다시 붙어 본다. UI
//!   스레드에서 하면 루프백 TCP 의 connect 상한(0.5초) × 시도 수만큼 창이 멎는다(Windows).
//! - 다시 붙을 때는 링크의 [`ServerLink::origin`](붙기 전 이름 — 포트 미확정)을 쓴다. 확정
//!   포트로 붙으면 Windows host 재시작이 새로 게시한 포트를 못 따라간다. 토큰도 시도마다
//!   다시 읽는다(정본 `_hello_msg` 와 같다).
//!
//! 뷰 없이 돌아가므로 실 서버를 띄운 라이브 시험(`tests/live_lifecycle.rs`)도 이 판을 그대로
//! 쓴다 — 시험이 지나는 길과 창이 지나는 길이 같다.

use std::sync::Arc;
use std::sync::mpsc::{Receiver, TryRecvError, channel};
use std::time::{Duration, Instant};

use base::lifecycle::{Budget, Effect, Ending, Input, Lifecycle, RECONNECT_DELAY, Refusal};

use crate::endpoint::{self, Endpoint};
use crate::link::{LinkEvent, ServerLink};
use crate::message::{PROTO_VERSION, ServerMessage};

/// 이름 하나와 크기로 붙는 법. 기본은 [`ServerLink::attach_to`]. 시험은 바꿔 끼운다.
pub type Connector = Arc<dyn Fn(&str, u16, u16) -> Result<ServerLink, String> + Send + Sync>;

/// 뷰가 받을 것.
#[derive(Debug)]
pub enum Happening {
    /// 수명과 무관한 서버 메시지 — 종전대로 처리한다.
    Message(Box<ServerMessage>),
    /// 지나가는 알림 한 줄.
    Notice(String),
    /// 지나가는 오류 한 줄(끝나지 않았다).
    Error(String),
    /// 읽기가 끝났다 — 진단 사유(로그 몫. 화면 문구는 [`Happening::Ended`] 가 준다).
    Closed(String),
    /// 새 링크로 갈아 끼웠다 — 연결에 매달린 사실(IME 렌더 알림 등)을 다시 보낸다.
    Reconnected,
    /// 새 채널이니 RTT 히스테리시스를 비운다.
    ResetNet,
    /// 붙은 뒤 이 클라를 다시 띄운다(`restart-all`).
    Relaunch,
    /// 끝났다 — 바닥 줄에 남길 문구(정본 `msg.*` 와 같은 글).
    Ended(String),
}

pub struct Supervisor {
    machine: Lifecycle,
    connect: Connector,
    worker: Option<Receiver<Result<ServerLink, String>>>,
    epoch: Instant,
    delay: Duration,
    /// 지금 링크의 끝을 이미 먹였나. `ServerLink::drain` 은 읽기 스레드가 끝난 뒤 **부를
    /// 때마다** 백스톱 `Ended` 를 다시 내므로(스레드가 Ended 를 못 보내고 죽었을 때를 위한 것),
    /// 그대로 먹이면 링크 하나의 끝이 EOF 여러 번이 된다 — 강제 재접속 실패 뒤(정본: 연결 없이
    /// 남는다)에 드롭 재접속이 저절로 돌고, 드롭 창(20초에 6회)도 헛되이 탄다.
    saw_end: bool,
}

impl std::fmt::Debug for Supervisor {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Supervisor")
            .field("machine", &self.machine)
            .field("attempting", &self.worker.is_some())
            .finish()
    }
}

/// 이 이름이 이 상자 안의 연결인가(정본 `ipc.is_local_endpoint`).
pub fn is_local(spec: &str) -> bool {
    match endpoint::parse(spec) {
        Endpoint::Unix { .. } => true,
        Endpoint::Tcp { host, .. } => crate::client::is_loopback_host(&host),
    }
}

fn default_connector() -> Connector {
    Arc::new(|spec, cols, rows| ServerLink::attach_to(spec, cols, rows).map_err(|e| e.to_string()))
}

impl Supervisor {
    /// 이 링크에 맞는 감독자. 기록판(소켓 없는 시험 링크)이면 다시 붙을 곳이 없다 —
    /// 시도는 곧장 실패한다(기다림 없이).
    pub fn for_link(link: &ServerLink) -> Self {
        if link.is_detached() {
            let mut me = Self::with_connector(
                true,
                // 진단(로그) 몫이라 번역하지 않는다.
                Arc::new(|_, _, _| Err("detached link: nowhere to reconnect".to_owned())),
            );
            me.delay = Duration::ZERO;
            return me;
        }
        Self::with_connector(is_local(link.origin()), default_connector())
    }

    pub fn with_connector(local: bool, connect: Connector) -> Self {
        Self {
            machine: Lifecycle::new(local),
            connect,
            worker: None,
            epoch: Instant::now(),
            delay: Duration::from_secs_f64(RECONNECT_DELAY),
            saw_end: false,
        }
    }

    /// 시도 사이 간격을 바꾼다(시험 — 정본 간격은 [`RECONNECT_DELAY`]).
    pub fn with_delay(mut self, delay: Duration) -> Self {
        self.delay = delay;
        self
    }

    pub fn lifecycle(&self) -> &Lifecycle {
        &self.machine
    }

    pub fn ended(&self) -> Option<&Ending> {
        self.machine.ended()
    }

    pub fn attempting(&self) -> bool {
        self.machine.attempting()
    }

    fn now(&self) -> f64 {
        self.epoch.elapsed().as_secs_f64()
    }

    /// 퍼올리기 한 번 — 시도 결과를 줍고, 링크가 낸 것을 가른다. **기다리지 않는다.**
    pub fn pump(&mut self, link: &mut ServerLink, size: (u16, u16)) -> Vec<Happening> {
        let mut out = Vec::new();
        if let Some(rx) = self.worker.as_ref() {
            let done = match rx.try_recv() {
                Ok(Ok(fresh)) => {
                    *link = fresh;
                    self.saw_end = false;
                    out.push(Happening::Reconnected);
                    Some(true)
                }
                Ok(Err(why)) => {
                    out.push(Happening::Closed(why));
                    Some(false)
                }
                Err(TryRecvError::Empty) => None,
                Err(TryRecvError::Disconnected) => Some(false),
            };
            if let Some(ok) = done {
                self.worker = None;
                self.feed(Input::Attempted(ok), link, size, &mut out);
            }
        }
        for event in link.drain() {
            match event {
                LinkEvent::Message(msg) => match *msg {
                    ServerMessage::Restarting { relaunch } => {
                        self.feed(Input::Restarting { relaunch }, link, size, &mut out);
                    }
                    ServerMessage::Bye => self.feed(Input::Bye, link, size, &mut out),
                    ServerMessage::Error { msg, error, server_proto } => {
                        match error
                            .as_deref()
                            .and_then(|code| Refusal::from_wire(code, server_proto, PROTO_VERSION))
                        {
                            Some(refusal) => {
                                self.feed(Input::Refused(refusal), link, size, &mut out)
                            }
                            None => out.push(Happening::Message(Box::new(ServerMessage::Error {
                                msg,
                                error,
                                server_proto,
                            }))),
                        }
                    }
                    other => out.push(Happening::Message(Box::new(other))),
                },
                LinkEvent::Ended(reason) => {
                    if std::mem::replace(&mut self.saw_end, true) {
                        continue; // 같은 링크의 백스톱 반복(위 `saw_end` 문서)
                    }
                    out.push(Happening::Closed(reason));
                    self.feed(Input::Eof, link, size, &mut out);
                }
            }
        }
        out
    }

    /// 사용자의 `reconnect`.
    pub fn manual(&mut self, link: &mut ServerLink, size: (u16, u16)) -> Vec<Happening> {
        let mut out = Vec::new();
        self.feed(Input::Manual, link, size, &mut out);
        out
    }

    /// degraded 가 오래 이어졌다(원격 연결에서만 뜻이 있다 — 상태기계가 거른다).
    pub fn degraded(&mut self, link: &mut ServerLink, size: (u16, u16)) -> Vec<Happening> {
        let mut out = Vec::new();
        self.feed(Input::Degraded, link, size, &mut out);
        out
    }

    /// 보내기가 실패했다 — 소켓은 이미 접혔다(`CommandSink::fold_on_error`). EOF 와 같다.
    pub fn send_failed(&mut self, link: &mut ServerLink, size: (u16, u16)) -> Vec<Happening> {
        let mut out = Vec::new();
        if !std::mem::replace(&mut self.saw_end, true) {
            self.feed(Input::Eof, link, size, &mut out);
        }
        out
    }

    fn feed(&mut self, input: Input, link: &mut ServerLink, size: (u16, u16), out: &mut Vec<Happening>) {
        let now = self.now();
        for effect in self.machine.on(input, now) {
            match effect {
                Effect::Notice(text) => out.push(Happening::Notice(text)),
                Effect::Error(text) => out.push(Happening::Error(text)),
                Effect::ResetNet => out.push(Happening::ResetNet),
                Effect::Relaunch => out.push(Happening::Relaunch),
                Effect::End(why) => out.push(Happening::Ended(why.message())),
                Effect::Attempt(budget) => {
                    // 강제 재접속은 살아 있는 옛 소켓을 먼저 닫는다(정본 `_force_reconnect`).
                    // 그 소켓의 리더가 내는 EOF 는 상태기계가 버린다(진행 중).
                    if budget == Budget::Force {
                        link.close();
                    }
                    self.spawn(budget, link.origin().to_owned(), size);
                }
            }
        }
    }

    fn spawn(&mut self, budget: Budget, origin: String, (cols, rows): (u16, u16)) {
        let (tx, rx) = channel();
        let connect = self.connect.clone();
        let delay = self.delay;
        std::thread::spawn(move || {
            let mut last = String::new();
            for _ in 0..budget.attempts() {
                match connect(&origin, cols, rows) {
                    Ok(link) => {
                        let _ = tx.send(Ok(link));
                        return;
                    }
                    Err(why) => last = why,
                }
                if !delay.is_zero() {
                    std::thread::sleep(delay);
                }
            }
            let _ = tx.send(Err(last));
        });
        self.worker = Some(rx);
    }
}

#[cfg(test)]
#[path = "supervise_tests.rs"]
mod tests;
