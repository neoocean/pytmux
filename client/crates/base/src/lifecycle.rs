//! 접속 수명 — 끊기면 언제 다시 붙고, 몇 번 해 보고, 무엇이면 포기하나(pytmux-585).
//!
//! 정본은 `pytmuxlib/clientconn.py` 의 `_reader_task` · `_reconnect` · `_drop_reconnect_ok` ·
//! `_reconnect_after_drop` · `_force_reconnect` 와 `client.py` 의 `restarting`/`bye` 분기다.
//! 그 갈래를 **뷰도 소켓도 시계도 없이** 여기 한 벌로 옮겼다 — 소켓을 다시 여는 일은
//! `proto::supervise` 가, 문구를 거는 일은 뷰가 한다. 시각은 인자로 받는다(시험이
//! 20초 창을 기다리지 않고 잰다).
//!
//! 정본과 같은지는 `proto/tests/lifecycle_conformance.rs` 가 잰다 — 정본 갈래를 **실제로
//! 돌려** 뽑은 픽스처(`client/scripts/gen_lifecycle_fixture.py`)와 대조한다.
//!
//! # 정본과 일부러 다른 둘
//!
//! - `// DIVERGE(exit-keeps-window): ⓒ` 끝난 뒤에도 창을 닫지 않는다. 정본은 `self.exit(message=…)`
//!   로 앱을 끝내고 문구를 단말에 남기지만, GUI 에는 «단말» 이 없다 — 창을 닫으면 문구가
//!   사라진다. 그래서 문구를 바닥 줄에 걸고 창을 남긴다. 그 귀결로 끝난 뒤의 수동
//!   `reconnect` 가 뜻을 갖는다([`Input::Manual`] 이 끝난 상태도 받는다).
//! - `// DIVERGE(relaunch-order): ⓒ` 정본은 `restarting{relaunch}` 뒤 EOF 를 보면
//!   **곧장** 자기를 다시 띄운다(`os.execv`). GUI 이진은 파이썬보다 훨씬 빨리 떠서, 서버가
//!   아직 execv 중일 때 새 프로세스가 «서버 없음» 으로 읽고 `start-server` 로 **두 번째
//!   서버**를 세울 수 있다. 그래서 먼저 제자리에서 다시 붙고, 붙은 **뒤에** 다시 띄운다
//!   ([`Effect::Relaunch`]). 정본의 Windows 갈래(제자리 재접속)와 같은 길을 먼저 지나는 셈이다.

use crate::i18n::{t, tf};

/// 시도 사이 간격(초) — 정본 `_RECONNECT_DELAY`.
pub const RECONNECT_DELAY: f64 = 0.02;
/// 평상 EOF 재접속을 세는 창(초) — 정본 `_DROP_RECONNECT_WINDOW`.
pub const DROP_WINDOW: f64 = 20.0;
/// 그 창 안에서 허용하는 재접속 수 — 정본 `_DROP_RECONNECT_MAX`.
pub const DROP_MAX: usize = 6;
/// degraded 가 이만큼 이어지면 스스로 다시 붙는다 — 정본 `net_recover_n` 기본값.
pub const RECOVER_N: u32 = 20;

/// 몇 번 해 보나.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Budget {
    /// 서버가 `restarting` 을 알린 뒤(≈6초) — `_RECONNECT_RETRIES_RESTART`.
    Restart,
    /// 수동 `reconnect` · degraded 자동 회복(≈3초, 서버는 산다) — `_RECONNECT_RETRIES_FORCE`.
    Force,
    /// 예고 없는 EOF(백프레셔 드롭 · 서버 죽음, ≈0.5초) — `_RECONNECT_RETRIES_DROP`.
    Drop,
}

impl Budget {
    pub fn attempts(self) -> u32 {
        match self {
            Budget::Restart => 300,
            Budget::Force => 150,
            Budget::Drop => 25,
        }
    }
}

/// 서버가 핸드셰이크에서 거절했다(`{"t":"error","error":…}`) — 다시 붙어도 같다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Refusal {
    /// `proto_mismatch` — 서버 판(`server_proto`)이 함께 온다.
    ProtoMismatch { server: Option<u32>, client: u32 },
    /// `auth_failed` — 토큰이 안 맞거나 다른 사용자의 서버다.
    AuthFailed,
}

impl Refusal {
    /// 와이어의 `error` 낱말에서. 모르는 낱말이면 `None`(그때는 종전대로 알림 한 줄).
    pub fn from_wire(code: &str, server_proto: Option<u32>, client: u32) -> Option<Self> {
        match code {
            "proto_mismatch" => Some(Refusal::ProtoMismatch { server: server_proto, client }),
            "auth_failed" => Some(Refusal::AuthFailed),
            _ => None,
        }
    }
}

/// 어떻게 끝났나 — 끝난 뒤 바닥 줄에 남는 한 마디.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Ending {
    /// `bye` — 의도된 종료(`msg.server_terminated`).
    Terminated,
    /// 예고 없이 사라졌고 다시 못 붙었다(`msg.server_lost`).
    Lost,
    /// `restarting` 뒤 다시 못 붙었다(`msg.reconnect_failed`).
    ReconnectFailed,
    /// 핸드셰이크 거절(`msg.proto_mismatch` · `msg.auth_failed`).
    Refused(Refusal),
}

impl Ending {
    /// 정본 카탈로그와 같은 글(ko 원문이 키 — en 은 `i18n/en_core.rs`).
    pub fn message(&self) -> String {
        match self {
            Ending::Terminated => t("pytmux: 서버가 종료되었습니다").to_owned(),
            Ending::Lost => t(LOST).to_owned(),
            Ending::ReconnectFailed => t("pytmux: 서버 재접속 실패").to_owned(),
            Ending::Refused(Refusal::AuthFailed) => t(AUTH_FAILED).to_owned(),
            Ending::Refused(Refusal::ProtoMismatch { server, client }) => {
                let server = server.map_or_else(|| "?".to_owned(), |n| n.to_string());
                tf(PROTO_MISMATCH, &[("server", &server), ("client", &client.to_string())])
            }
        }
    }
}

const LOST: &str = "pytmux: 서버 연결이 끊겼고 재접속에 실패했습니다 — 서버 프로세스가 외부에서 강제 종료됐는지 확인하세요(pythonw 일괄 kill 등)";
const AUTH_FAILED: &str = "pytmux: 서버가 인증을 거부했습니다 — 토큰이 맞지 않습니다(다른 사용자의 서버이거나 토큰 파일이 지워졌습니다)";
const PROTO_MISMATCH: &str = "pytmux: 서버와 프로토콜 버전이 다릅니다(서버 {server} · 클라 {client}) — 서버와 클라를 같은 판으로 맞추세요";

/// 지나가는 알림 문구(정본 `display_message` 의 키들).
pub fn server_restarting() -> &'static str {
    t("pytmux: 서버 재시작 중…")
}
pub fn restart_done() -> &'static str {
    t("pytmux: 서버 재시작 완료 — 재접속됨")
}
pub fn reconnected_resync() -> &'static str {
    t("pytmux: 재접속됨 — 화면 재동기")
}
pub fn reconnecting(auto: bool) -> &'static str {
    if auto { t("pytmux: 재접속 중… (자동 회복)") } else { t("pytmux: 재접속 중…") }
}
pub fn reconnect_failed_net() -> &'static str {
    t("pytmux: 재접속 실패 — 네트워크 확인")
}

/// 상태기계에 먹이는 사건.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Input {
    /// 서버가 `{"t":"restarting"}` 을 보냈다.
    Restarting { relaunch: bool },
    /// 서버가 `{"t":"bye"}` 를 보냈다.
    Bye,
    /// 핸드셰이크 거절.
    Refused(Refusal),
    /// 읽기가 끝났다(EOF · 손상 프레임 · 쓰기 실패로 접힌 소켓).
    Eof,
    /// 진행 중이던 시도 묶음이 끝났다 — 붙었나.
    Attempted(bool),
    /// 사용자의 `reconnect`/`resync`.
    Manual,
    /// degraded 가 [`RECOVER_N`] 표본 이어졌다(원격 연결에서만 뜻이 있다).
    Degraded,
}

/// 상태기계가 시키는 일.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Effect {
    /// 지나가는 알림.
    Notice(String),
    /// 지나가는 오류(끝내지 않는다).
    Error(String),
    /// 옛 연결을 닫고(남아 있으면) 이만큼 다시 붙어 본다.
    Attempt(Budget),
    /// 새 채널이니 RTT 히스테리시스·degraded 를 비운다.
    ResetNet,
    /// 붙은 뒤 이 클라를 다시 띄운다(`restart-all` · 위 DIVERGE).
    Relaunch,
    /// 끝났다.
    End(Ending),
}

/// 진행 중인 시도가 무엇을 위한 것인가.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Pending {
    Restart,
    Drop,
    Force,
}

/// 접속 수명 상태.
#[derive(Debug, Clone)]
pub struct Lifecycle {
    /// 정본 `net_auto_reconnect`(기본 켜짐 — 정본 설정 파일은 이 값을 안 읽는다).
    pub auto: bool,
    /// 로컬 연결(AF_UNIX · 루프백) — degraded 자동 회복을 안 한다(정본 `_net_local`).
    pub local: bool,
    restarting: bool,
    relaunch: bool,
    pending: Option<Pending>,
    drop_ts: Vec<f64>,
    ended: Option<Ending>,
}

impl Lifecycle {
    pub fn new(local: bool) -> Self {
        Self {
            auto: true,
            local,
            restarting: false,
            relaunch: false,
            pending: None,
            drop_ts: Vec::new(),
            ended: None,
        }
    }

    /// 끝났으면 그 까닭.
    pub fn ended(&self) -> Option<&Ending> {
        self.ended.as_ref()
    }

    /// 다시 붙는 중인가.
    pub fn attempting(&self) -> bool {
        self.pending.is_some()
    }

    /// 정본 `_drop_reconnect_ok` — **물을 때마다** 지금을 세고 창 안 개수를 본다.
    fn drop_ok(&mut self, now: f64) -> bool {
        self.drop_ts.retain(|t| now - t < DROP_WINDOW);
        self.drop_ts.push(now);
        self.drop_ts.len() <= DROP_MAX
    }

    fn end(&mut self, why: Ending) -> Vec<Effect> {
        self.ended = Some(why.clone());
        self.pending = None;
        vec![Effect::End(why)]
    }

    pub fn on(&mut self, input: Input, now: f64) -> Vec<Effect> {
        // 끝난 뒤에는 수동 재접속만 받는다(위 DIVERGE ⓒ).
        if self.ended.is_some() && input != Input::Manual {
            return Vec::new();
        }
        match input {
            Input::Restarting { relaunch } => {
                self.restarting = true;
                self.relaunch |= relaunch;
                vec![Effect::Notice(server_restarting().to_owned())]
            }
            Input::Bye => self.end(Ending::Terminated),
            Input::Refused(r) => self.end(Ending::Refused(r)),
            Input::Eof => {
                // 강제 재접속이 막 옛 소켓을 닫아 깨운 리더(정본 `_force_reconnecting` 가드)
                // · 이미 다시 붙는 중.
                if self.pending.is_some() {
                    return Vec::new();
                }
                if self.restarting {
                    self.restarting = false;
                    self.pending = Some(Pending::Restart);
                    return vec![Effect::Attempt(Budget::Restart)];
                }
                if self.auto && self.drop_ok(now) {
                    self.pending = Some(Pending::Drop);
                    return vec![Effect::Attempt(Budget::Drop)];
                }
                self.end(Ending::Lost)
            }
            Input::Attempted(ok) => match self.pending.take() {
                None => Vec::new(),
                Some(Pending::Restart) if ok => {
                    let mut out = vec![Effect::Notice(restart_done().to_owned())];
                    if std::mem::take(&mut self.relaunch) {
                        out.push(Effect::Relaunch);
                    }
                    out
                }
                Some(Pending::Restart) => self.end(Ending::ReconnectFailed),
                Some(Pending::Drop) if ok => {
                    vec![Effect::ResetNet, Effect::Notice(reconnected_resync().to_owned())]
                }
                Some(Pending::Drop) => self.end(Ending::Lost),
                Some(Pending::Force) if ok => {
                    vec![Effect::ResetNet, Effect::Notice(reconnected_resync().to_owned())]
                }
                // 정본: 문구만 띄우고 앱은 남는다(연결 없는 채로) — 수동으로 다시 할 수 있다.
                Some(Pending::Force) => vec![Effect::Error(reconnect_failed_net().to_owned())],
            },
            Input::Manual => {
                if self.pending.is_some() {
                    return Vec::new();
                }
                self.ended = None;
                self.restarting = false;
                self.pending = Some(Pending::Force);
                vec![Effect::Notice(reconnecting(false).to_owned()), Effect::Attempt(Budget::Force)]
            }
            Input::Degraded => {
                if !self.auto || self.local || self.pending.is_some() {
                    return Vec::new();
                }
                self.pending = Some(Pending::Force);
                vec![Effect::Notice(reconnecting(true).to_owned()), Effect::Attempt(Budget::Force)]
            }
        }
    }
}

#[cfg(test)]
#[path = "lifecycle_tests.rs"]
mod tests;
