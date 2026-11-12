//! 이벤트 훅 사건 적합성(pytmux-588) — 정본이 쓴 답지
//! (`scripts/gen_hook_events.py` → `fixtures/hook_events.json`)에 대고 잰다.
//!
//! 두 가지를 본다:
//!
//! 1. **사건 이름 전수** — 정본 코어(`_fire_hook("…")` AST) ∪ 플러그인발(`saver_hook_events`)
//!    이 우리 `HookEvent::ALL` 과 같다. 정본에 사건이 늘면 여기가 먼저 운다 — 종전에는
//!    `claude-limit` 을 이름만 들고 발화하지 않았고 그 사실은 주석에만 있었다.
//! 2. **`claude-limit` 전이** — 정본이 status 열에 낸 `(event, env)` 를 우리
//!    `proto::session::claude_limit_facts` + `HookWatcher::saw_claude` 가 그대로 낸다.

use std::collections::BTreeMap;

use base::hooks::{HookEvent, HookWatcher};
use proto::message::ServerMessage;

#[derive(serde::Deserialize)]
struct Fixture {
    core: Vec<String>,
    plugin: Vec<String>,
    cases: BTreeMap<String, Vec<Step>>,
}

#[derive(serde::Deserialize)]
struct Step {
    status: serde_json::Value,
    fires: Vec<Fire>,
}

#[derive(serde::Deserialize)]
struct Fire {
    event: String,
    env: BTreeMap<String, String>,
}

fn fixture() -> Fixture {
    let raw = include_str!("fixtures/hook_events.json");
    serde_json::from_str(raw).expect("hook_events.json 이 안 읽힌다")
}

#[test]
fn every_canon_hook_event_is_one_we_fire() {
    let fx = fixture();
    let mut canon: Vec<String> = fx.core.iter().chain(fx.plugin.iter()).cloned().collect();
    canon.sort();
    let mut ours: Vec<String> = HookEvent::ALL.iter().map(|e| e.name().to_owned()).collect();
    ours.sort();
    assert_eq!(ours, canon, "정본이 발화하는 훅 사건 전수와 우리 표가 갈렸다");
}

#[test]
fn claude_limit_transitions_match_the_python_canonical() {
    let fx = fixture();
    assert!(!fx.cases.is_empty(), "답지가 비었다 — 생성기 고장");
    for (name, steps) in &fx.cases {
        let mut watcher = HookWatcher::default();
        for (i, step) in steps.iter().enumerate() {
            let mut status = step.status.clone();
            status["t"] = serde_json::json!("status");
            let msg: ServerMessage = serde_json::from_value(status).expect("status 모양");
            let ServerMessage::Status(status) = msg else {
                panic!("{name}[{i}]: status 가 아니다");
            };
            let (state, account) = proto::session::claude_limit_facts(&status);
            let got: Vec<(String, BTreeMap<String, String>)> = watcher
                .saw_claude(state.as_deref(), &account)
                .into_iter()
                .map(|f| (f.event.name().to_owned(), f.env.into_iter().collect()))
                .collect();
            let want: Vec<(String, BTreeMap<String, String>)> =
                step.fires.iter().map(|f| (f.event.clone(), f.env.clone())).collect();
            assert_eq!(got, want, "{name}[{i}]: 정본과 다른 발화");
        }
    }
}
