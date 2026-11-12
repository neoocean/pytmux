//! 클라 **크래시 자가 재기동**의 규칙(pytmux-639 G10) — 정본 `client.run_client` 와 같은 표.
//!
//! 정본 클라는 `app.run()` 밖으로 예외가 새면(= 크래시) 트레이스백을
//! `<state_base>.client.crash.log` 에 남기고, **한도 안에서** 같은 인자로 자기를 다시 띄운다.
//! 서버(데몬)가 셸·PTY·세션을 쥐고 있으므로 새 클라가 다시 붙으면 작업이 그대로 이어진다
//! (tmux 모델). 연쇄 크래시(뜨자마자 또 죽음)가 무한 재실행이 되지 않게 횟수를 환경 변수로
//! 대를 넘겨 센다.
//!
//! | 항목 | 값(정본) |
//! |---|---|
//! | 세는 변수 | `PYTMUX_CLIENT_CRASH_N` ([`ENV`]) |
//! | 연쇄로 보는 창 | 이번 실행이 30초 안에 죽었나([`LOOP_WINDOW`] · `_CRASH_LOOP_WINDOW`) |
//! | 한도 | 5 ([`MAX`] · `_CLIENT_CRASH_RELAUNCH_MAX`) — 첫 실행까지 **최대 여섯 번** 뜬다 |
//! | 포기 | 표준 오류에 [`giveup_text`] · 종료 코드 1 |
//! | 정상 종료 · `restart-all` | 변수를 지운다(다음 세션은 깨끗이 시작) |
//!
//! 여기는 **뷰도 프로세스도 모르는** 판정만 든다 — 패닉 훅과 재실행은 `gui/src/main.rs`.
//! 값이 정본과 같은지는 `proto/tests/lifecycle_conformance.rs` 가 정본 `run_client` 를 실제로
//! 돌려 뽑은 픽스처(`lifecycle.json` 의 `crash`)로 잰다.

/// 대를 넘겨 세는 환경 변수 — 정본과 **같은 이름**(정본 클라·GUI 어느 쪽에서 띄웠든 같은 셈).
pub const ENV: &str = "PYTMUX_CLIENT_CRASH_N";
/// 재기동 한도 — 정본 `_CLIENT_CRASH_RELAUNCH_MAX`.
pub const MAX: u32 = 5;
/// 이 시간(초) 안에 다시 죽으면 연쇄로 센다 — 정본 `_CRASH_LOOP_WINDOW`. 오래 잘 돌다 죽은 것은
/// 새 셈(1)이다 — 어쩌다 난 크래시가 복구 예산을 깎지 않게.
pub const LOOP_WINDOW: f64 = 30.0;

/// 크래시 뒤에 할 일.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Decision {
    /// 변수에 `n` 을 적고 같은 인자로 다시 띄운다.
    Relaunch(u32),
    /// 한도를 넘었다 — 알리고 끝낸다(종료 코드 1).
    GiveUp,
}

/// 정본 `_crash_relaunch_count` — 숫자가 아니면 0.
pub fn count(env_value: Option<&str>) -> u32 {
    env_value.and_then(|v| v.trim().parse().ok()).unwrap_or(0)
}

/// 크래시 판정 — `env_value` 는 [`ENV`] 의 지금 값, `elapsed` 는 이번 실행이 산 시간(초).
pub fn decide(env_value: Option<&str>, elapsed: f64) -> Decision {
    let n = if elapsed < LOOP_WINDOW { count(env_value) + 1 } else { 1 };
    if n <= MAX { Decision::Relaunch(n) } else { Decision::GiveUp }
}

/// 크래시 기록 한 덩어리의 머리줄 — 정본 `_log_client_crash` 와 같은 모양.
pub fn log_header(stamp: &str, pid: u32, ver: &str) -> String {
    format!("\n==== {stamp} pid={pid} ver={ver} ====\n")
}

/// 포기할 때의 글 — 정본은 카탈로그 밖 원문(한국어 고정)이다. 그대로 쓴다.
pub fn giveup_text(crash_log: &str) -> String {
    format!(
        "pytmux: 클라이언트가 반복 크래시해 자동복구를 멈춥니다.\n  트레이스백: {crash_log}\n"
    )
}

#[cfg(test)]
#[path = "crash_tests.rs"]
mod tests;
