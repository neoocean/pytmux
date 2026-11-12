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
                    // DIVERGE(relaunch-order): relaunch 순서 — 정본(POSIX)은 EOF 에서 시도 없이
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

fn raw() -> serde_json::Value {
    serde_json::from_str(include_str!("fixtures/lifecycle.json")).expect("픽스처를 못 읽었다")
}

/// degraded 자동 회복(pytmux-639) — 정본 `_net_sample` 을 실제로 돌린 차례와 우리 RTT 이력
/// (`proto::rtt::RttHist`)이 **같은 표본에서** 회복을 걸고 외곽선이 같은 표본에서 켜지나.
#[test]
fn degraded_watchdog_fires_like_the_canon() {
    let fx = raw();
    let d = &fx["degraded"];
    assert_eq!(d["recover_n"].as_u64(), Some(u64::from(base::lifecycle::RECOVER_N)));
    let threshold = d["threshold"].as_f64().unwrap();
    let scenarios = d["scenarios"].as_array().unwrap();
    assert!(scenarios.len() >= 3);
    for sc in scenarios {
        let name = sc["name"].as_str().unwrap();
        let mut hist = proto::rtt::RttHist::default();
        hist.local = sc["local"].as_bool().unwrap();
        hist.threshold = threshold;
        let mut fired = Vec::new();
        let mut degraded = Vec::new();
        for (i, ch) in sc["seq"].as_str().unwrap().chars().enumerate() {
            let rtt = if ch == 'b' { threshold * 2.0 } else { threshold / 4.0 };
            if hist.sample(i as f64, rtt) {
                fired.push(i as u64 + 1);
            }
            degraded.push(hist.degraded);
        }
        let want: Vec<u64> = sc["fired"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| f["at"].as_u64().unwrap())
            .collect();
        assert_eq!(fired, want, "{name}: 회복을 건 표본");
        let want_deg: Vec<bool> =
            sc["degraded"].as_array().unwrap().iter().map(|b| b.as_bool().unwrap()).collect();
        assert_eq!(degraded, want_deg, "{name}: 빨간 외곽선");
    }
}

/// 크래시 자가 재기동(pytmux-639 G10) — 정본 `run_client` 를 실제로 돌린 결과와 `base::crash`.
#[test]
fn crash_relaunch_decides_like_the_canon() {
    let fx = raw();
    let c = &fx["crash"];
    assert_eq!(c["env"].as_str(), Some(base::crash::ENV));
    assert_eq!(c["max"].as_u64(), Some(u64::from(base::crash::MAX)));
    assert_eq!(c["window"].as_f64(), Some(base::crash::LOOP_WINDOW));
    assert_eq!(
        c["log_header"].as_str().map(|s| format!("\n{s}\n")),
        Some(base::crash::log_header("{stamp}", 0, "{ver}").replace("pid=0", "pid={pid}"))
    );
    let mut crashed = 0;
    for case in c["cases"].as_array().unwrap() {
        if !case["crash"].as_bool().unwrap() {
            // 정상 종료는 셈을 지운다(정본 2560) — 우리 `restart::relaunch` 가 지운다(코드 리뷰 몫).
            assert!(case["env_after"].is_null(), "{case}");
            continue;
        }
        crashed += 1;
        let prev = case["prev"].as_str();
        let elapsed = case["elapsed"].as_f64().unwrap();
        let ours = base::crash::decide(prev, elapsed);
        match case["relaunch"].as_str() {
            Some(n) => assert_eq!(
                ours,
                base::crash::Decision::Relaunch(n.parse().unwrap()),
                "{case}"
            ),
            None => {
                assert_eq!(ours, base::crash::Decision::GiveUp, "{case}");
                assert_eq!(case["exit"].as_i64(), Some(1), "{case}");
                assert_eq!(
                    case["stderr"].as_str().unwrap(),
                    base::crash::giveup_text("{path}"),
                    "포기 글이 정본과 다르다"
                );
            }
        }
    }
    assert!(crashed >= 10, "크래시 갈래가 너무 적다: {crashed}");
}
