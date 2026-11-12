//! 접속 수명이 **정본과 같은 갈래로** 가나(pytmux-585).
//!
//! 픽스처(`fixtures/lifecycle.json`)는 `scripts/gen_lifecycle_fixture.py` 가 정본
//! `clientconn._reader_task`·`_force_reconnect` 를 스텁 위에서 **실제로 돌려** 뽑는다. 같은
//! 시나리오를 `base::lifecycle::Lifecycle` 에 먹여 ① 시도 횟수 ② 남는 글(알림·종료) 을 맞댄다.
//! 선언된 갈림 하나(relaunch 순서 — `base::lifecycle` 모듈 문서의 DIVERGE)는 **그 자리를
//! 이름으로** 단언한다 — 조용히 넘기면 다음 갈림도 그 틈으로 샌다.

use std::collections::BTreeMap;

use base::lifecycle::{Budget, Effect, Input, Lifecycle};
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    constants: Constants,
    texts: BTreeMap<String, Text>,
    scenarios: Vec<Scenario>,
}

#[derive(Deserialize)]
struct Constants {
    delay: f64,
    restart: u32,
    force: u32,
    drop: u32,
    drop_window: f64,
    drop_max: usize,
}

#[derive(Deserialize)]
struct Text {
    ko: String,
    en: String,
}

#[derive(Deserialize)]
struct Scenario {
    name: String,
    connect_ok: bool,
    #[serde(default)]
    flags: BTreeMap<String, serde_json::Value>,
    steps: Vec<Step>,
}

#[derive(Deserialize)]
struct Step {
    step: String,
    attempts: u32,
    log: Vec<serde_json::Value>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/lifecycle.json")).expect("픽스처를 못 읽었다")
}

/// 한 걸음을 우리 상태기계로 — (시도 횟수, 남은 글들, relaunch 를 시켰나).
fn run_step(lc: &mut Lifecycle, input: Input, ok: bool, now: f64) -> (u32, Vec<String>, bool) {
    let mut texts = Vec::new();
    let mut attempts = 0;
    let mut relaunch = false;
    let mut pending = lc.on(input, now);
    while !pending.is_empty() {
        let mut next = Vec::new();
        for effect in pending {
            match effect {
                Effect::Notice(t) | Effect::Error(t) => texts.push(t),
                Effect::End(why) => texts.push(why.message()),
                Effect::Relaunch => relaunch = true,
                Effect::ResetNet => {}
                Effect::Attempt(budget) => {
                    // 정본은 붙으면 1회, 못 붙으면 예산 전부를 쓴다.
                    attempts += if ok { 1 } else { budget.attempts() };
                    next.extend(lc.on(Input::Attempted(ok), now));
                }
            }
        }
        pending = next;
    }
    (attempts, texts, relaunch)
}

fn canon_texts(step: &Step) -> (Vec<String>, bool) {
    let mut out = Vec::new();
    let mut relaunch = false;
    for entry in &step.log {
        if let Some(m) = entry.get("msg").and_then(|m| m.as_str()) {
            out.push(m.to_owned());
        }
        if let Some(exit) = entry.get("exit") {
            if let Some(m) = exit.as_str() {
                out.push(m.to_owned());
            }
            relaunch |= entry.get("relaunch").and_then(|r| r.as_bool()).unwrap_or(false);
        }
    }
    (out, relaunch)
}

#[test]
fn the_constants_are_the_canonical_ones() {
    let c = fixture().constants;
    assert_eq!(c.delay, base::lifecycle::RECONNECT_DELAY);
    assert_eq!(c.restart, Budget::Restart.attempts());
    assert_eq!(c.force, Budget::Force.attempts());
    assert_eq!(c.drop, Budget::Drop.attempts());
    assert_eq!(c.drop_window, base::lifecycle::DROP_WINDOW);
    assert_eq!(c.drop_max, base::lifecycle::DROP_MAX);
}

#[test]
fn every_canonical_branch_ends_the_same_way() {
    base::i18n::with_locale("ko", || {
        for sc in fixture().scenarios {
            let mut lc = Lifecycle::new(true);
            let flag = |k: &str| sc.flags.get(k).and_then(|v| v.as_bool()).unwrap_or(false);
            if sc.flags.get("net_auto_reconnect").and_then(|v| v.as_bool()) == Some(false) {
                lc.auto = false;
            }
            if flag("_ending") {
                // 정본 `_ending` = bye·거절로 이미 끝내는 중 — 우리는 그 사건 자체를 먹인다.
                lc.on(Input::Bye, 0.0);
            }
            if flag("_reconnecting") {
                lc.on(Input::Restarting { relaunch: flag("_relaunch_on_restart") }, 0.0);
            }
            for (i, step) in sc.steps.iter().enumerate() {
                let input = match step.step.as_str() {
                    "eof" => Input::Eof,
                    "force" => Input::Manual,
                    other => panic!("모르는 걸음 {other}"),
                };
                let (attempts, texts, relaunch) = run_step(&mut lc, input, sc.connect_ok, i as f64);
                let (want, want_relaunch) = canon_texts(step);
                if flag("_relaunch_on_restart") {
                    // DIVERGE(pytmux-598): relaunch 순서 — 정본(POSIX)은 EOF 에서 시도 없이
                    // execv, (Windows)는 제자리 재접속 + «클라 코드 미갱신» 안내. 우리는
                    // OS 와 무관하게 **제자리에서 붙은 뒤** 다시 띄운다.
                    assert_eq!(attempts, 1, "[{}] 먼저 제자리에서 붙어야 한다", sc.name);
                    assert_eq!(texts, [base::lifecycle::restart_done()], "[{}]", sc.name);
                    assert!(relaunch, "[{}] 붙은 뒤 다시 띄워야 한다", sc.name);
                    assert!(
                        want_relaunch || want.iter().any(|t| t.contains("Windows")),
                        "[{}] 정본이 이 갈래를 바꿨다 — 선언을 다시 볼 것: {want:?}",
                        sc.name
                    );
                    continue;
                }
                assert_eq!(attempts, step.attempts, "[{} #{i}] 시도 횟수", sc.name);
                assert_eq!(texts, want, "[{} #{i}] 남는 글", sc.name);
                assert_eq!(relaunch, want_relaunch, "[{} #{i}] relaunch", sc.name);
            }
        }
    });
}

#[test]
fn the_texts_are_the_canonical_catalog_in_both_locales() {
    use base::lifecycle::{Ending, Refusal};
    let texts = fixture().texts;
    let ko = |key: &str| texts[key].ko.clone();
    let en = |key: &str| texts[key].en.clone();
    let pairs: Vec<(&str, Box<dyn Fn() -> String>)> = vec![
        ("msg.server_restarting", Box::new(|| base::lifecycle::server_restarting().to_owned())),
        ("msg.restart_done", Box::new(|| base::lifecycle::restart_done().to_owned())),
        ("msg.reconnected_resync", Box::new(|| base::lifecycle::reconnected_resync().to_owned())),
        ("msg.reconnecting", Box::new(|| base::lifecycle::reconnecting(false).to_owned())),
        ("msg.reconnecting_auto", Box::new(|| base::lifecycle::reconnecting(true).to_owned())),
        ("msg.reconnect_failed_net", Box::new(|| base::lifecycle::reconnect_failed_net().to_owned())),
        ("msg.server_terminated", Box::new(|| Ending::Terminated.message())),
        ("msg.server_lost", Box::new(|| Ending::Lost.message())),
        ("msg.reconnect_failed", Box::new(|| Ending::ReconnectFailed.message())),
        ("msg.auth_failed", Box::new(|| Ending::Refused(Refusal::AuthFailed).message())),
    ];
    for (key, ours) in &pairs {
        assert_eq!(base::i18n::with_locale("ko", ours), ko(key), "{key} ko");
        assert_eq!(base::i18n::with_locale("en", ours), en(key), "{key} en");
    }
    // 자리 있는 글 — 정본 `str.format` 과 같은 자리에 같은 값.
    let mismatch = Ending::Refused(Refusal::ProtoMismatch { server: Some(2), client: 1 });
    let fill = |s: String| s.replace("{server}", "2").replace("{client}", "1");
    assert_eq!(base::i18n::with_locale("ko", || mismatch.message()), fill(ko("msg.proto_mismatch")));
    assert_eq!(base::i18n::with_locale("en", || mismatch.message()), fill(en("msg.proto_mismatch")));
}
