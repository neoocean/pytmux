//! 적합성 — 팔레트의 **인자 자리 후보**가 정본 `PromptScreen._arg_candidates` 와 같은가
//! (pytmux-621).
//!
//! 픽스처는 정본을 직접 불러 뽑았다(`scripts/gen_prompt_args.py`). 표 둘(`COMMAND_ARGHIST`·
//! `SET_OPTION_CHOICES`)은 **글자까지**, 줄마다의 답(인자 자리인가 · 명령 토큰 · 후보 · 부류)은
//! 차례까지 같아야 한다.

use std::collections::BTreeMap;

use base::promptargs::{ArgKind, COMMAND_ARGHIST, SET_OPTION_CHOICES, arg_candidates};

#[derive(serde::Deserialize)]
struct Case {
    raw: String,
    arg_spot: bool,
    cmd: Option<String>,
    items: Vec<String>,
    kinds: Vec<String>,
}

#[derive(serde::Deserialize)]
struct Fx {
    command_arghist: BTreeMap<String, String>,
    set_option_choices: BTreeMap<String, Vec<String>>,
    history: BTreeMap<String, Vec<String>>,
    cases: Vec<Case>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/prompt_args.json"))
        .expect("prompt_args.json 을 못 읽는다 — scripts/gen_prompt_args.py 로 다시 뽑을 것")
}

#[test]
fn the_two_tables_are_canon() {
    let fx = fixture();
    let ours: BTreeMap<String, String> =
        COMMAND_ARGHIST.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
    assert_eq!(ours, fx.command_arghist, "COMMAND_ARGHIST 가 정본과 다르다");
    let ours: BTreeMap<String, Vec<String>> = SET_OPTION_CHOICES
        .iter()
        .map(|(k, v)| (k.to_string(), v.iter().map(|s| s.to_string()).collect()))
        .collect();
    assert_eq!(ours, fx.set_option_choices, "SET_OPTION_CHOICES 가 정본과 다르다");
}

#[test]
fn every_line_gets_the_canon_candidates() {
    let fx = fixture();
    let hist = |bucket: &str| fx.history.get(bucket).cloned().unwrap_or_default();
    let mut wrong = Vec::new();
    for case in &fx.cases {
        let got = arg_candidates(&case.raw, hist);
        let (spot, cmd, items, kinds) = match &got {
            Some(c) => (
                true,
                Some(c.cmd.clone()),
                c.items.iter().map(|(s, _)| s.clone()).collect::<Vec<_>>(),
                c.items
                    .iter()
                    .map(|(_, k)| if *k == ArgKind::Choice { "choice" } else { "recent" }.to_owned())
                    .collect::<Vec<_>>(),
            ),
            None => (false, None, Vec::new(), Vec::new()),
        };
        if spot != case.arg_spot || cmd != case.cmd || items != case.items || kinds != case.kinds {
            wrong.push(format!(
                "{:?}: 우리 {got:?} · 정본 자리 {} {:?} {:?}",
                case.raw, case.arg_spot, case.cmd, case.items
            ));
        }
    }
    assert!(wrong.is_empty(), "정본과 다른 인자 후보:\n  {}", wrong.join("\n  "));
}
