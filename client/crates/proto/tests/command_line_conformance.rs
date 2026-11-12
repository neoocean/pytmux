//! **명령 줄 적합성** — 같은 명령 줄을 두 클라가 같은 일로 돌리나(pytmux-572).
//!
//! # 왜 이 자가 필요한가
//!
//! 설정 파일 `bind`·훅·`if-shell` 의 then/else·사용자 별칭은 **명령 줄 한 줄**을 건넨다.
//! 이름 표(`command_alias_conformance.rs`)는 «그 이름을 받나» 만 재고, 인자가 무엇을 바꾸는지는
//! 아무도 안 쟀다 — 그 사이에서 `bind x send-keys C-c` 가 읽히되 눌러도 아무 일도 안 했다.
//!
//! # 어떻게 재나
//!
//! 생성기 `scripts/gen_command_line_fixture.py` 가 정본 `_run_command` 를 **그대로 불러** 줄마다
//! 낸 효과를 싣는다. 여기서는 같은 줄을 우리 실행기([`base::cmdline::run_line`])에 먹이고 그 결과를
//! **뷰가 내보내는 것**으로 옮겨 견준다 — 액션은 [`proto::command::action_to_command_with_tabs`]
//! (+ [`proto::command::with_config_paths`]), 대답은 [`proto::answer::answer_command`] 로. 둘 다
//! GUI 세션 뷰가 부르는 그 함수다.
//!
//! 견주는 것은 **나가는 것**(서버 명령 프레임 · 패널 입력 바이트 · 셸 명령 · `if-shell` ·
//! 다시 읽는 경로 · `set`)이다. 정본이 클라 안에서만 끝내는 줄(화면·알림)은 「우리도 그 줄을
//! 받는가 + 우리도 아무것도 안 내보내는가」로 잰다.
//!
//! # 못 맞는 줄은 사유와 이슈가 있어야만 선다
//!
//! [`DIVERGENT`] · [`DIVERGENT_CEILING`] — `config_grammar_conformance.rs` 와 같은 규칙(⛔ 상한을
//! 올리지 않는다 · 고치면 그 줄이 「이제 맞는다 — 지울 것」으로 운다).

use std::collections::BTreeSet;

use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Deserialize)]
struct Case {
    key: String,
    line: String,
    effects: Vec<Value>,
}

#[derive(Deserialize)]
struct Fx {
    cases: Vec<Case>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/command_lines.json"))
        .expect("픽스처를 읽을 수 없다 — scripts/gen_command_line_fixture.py 로 다시 뽑을 것")
}

/// 못 맞는 줄 — (픽스처 칸의 열쇠, 이슈, 사유). 열쇠로 찾는다.
const DIVERGENT: &[(&str, &str, &str)] = &[
    (
        "select-tab -1",
        "pytmux-572",
        "음수 번호(끝에서 센다) — 실행기는 탭 수를 모르고 `Action::SelectTab` 은 양수 번호만 든다",
    ),
    (
        "resize-pane -D 5",
        "pytmux-572",
        "칸 수 인자 — `Action::ResizePane` 이 칸 수를 안 든다(우리는 서버 기본 걸음)",
    ),
    (
        "rotate-window -D",
        "pytmux-572",
        "역방향 회전 — `Action::RotatePanes` 는 정방향뿐이다",
    ),
    ("swap-pane -t", "pytmux-572", "번호로 고르는 교환(`-s`/`-t`) — 액션이 없다"),
    (
        "select-layout preset",
        "pytmux-572",
        "프리셋 이름 인자 — 우리는 순환(`cycle_layout`)만 보낸다",
    ),
    ("join-pane -h", "pytmux-572", "정본은 `orient` 만 보내고 우리는 원본 패널 번호를 묻는다"),
    ("detach -a", "pytmux-572", "`detach_others` — 액션이 없다"),
    ("paste-buffer 2", "pytmux-572", "버퍼 번호 인자 — `Action::PasteBuffer` 는 번호를 안 든다"),
    ("move-tab 2", "pytmux-572", "정본은 1-based 번호를 0-based 로 접고 우리는 그대로 보낸다"),
    ("swap-tab 3", "pytmux-572", "`move-tab 2` 와 같다(1-based → 0-based)"),
    (
        "inactive-dim off",
        "pytmux-572",
        "정본은 이번 판만 바꾸고(설정 파일에 안 쓴다) 우리는 설정 파일에 쓴다",
    ),
    ("strip-box-drawing on", "pytmux-572", "`inactive-dim off` 와 같다(우리는 설정 파일에 쓴다)"),
    ("inactive-dim-ratio", "pytmux-572", "`inactive-dim off` 와 같다(우리는 설정 파일에 쓴다)"),
    ("send-keys -l", "pytmux-572", "글자 그대로(`-l`) — 우리 `parse_send_keys` 는 이름 키를 늘 접는다"),
    ("window-size", "pytmux-572", "맨이름 = 순환(값 없이 보낸다) — 우리 폼에는 순환 선택지가 없다"),
    (
        "pin",
        "pytmux-572",
        "정본은 활성 탭의 병합 index 를 싣는다 — `action_to_command_with_tabs` 가 `TogglePin` 에만 \
         싣고 `SetPinned` 에는 안 싣는다(원격 탭이 활성이면 엉뚱한 로컬 탭이 고정될 수 있다)",
    ),
    ("unpin", "pytmux-572", "`pin` 과 같다(`SetPinned(false)` 에 index 가 없다)"),
    (
        "pane-border-status on",
        "pytmux-572",
        "값을 정해 보내는 액션이 없다 — `ToggleBorderStatus` 는 늘 토글이다",
    ),
    ("setw pane-border-status off", "pytmux-572", "`pane-border-status on` 과 같다"),
    (
        "layout-list",
        "pytmux-572",
        "정본은 저장된 배치 목록을 물어(`list_layouts`) 고르게 하고 우리는 이름을 묻는다",
    ),
    (
        "run-shell words",
        "pytmux-572",
        "따옴표 없이 친 여러 낱말 — 정본은 첫 낱말만 셸에 넘기고(`_run_shell(args[0])`) 우리는 줄의 \
         나머지 전부를 넘긴다(종전 GUI 동작 · 맞출지는 사람 결정)",
    ),
];

/// [`DIVERGENT`] 의 상한 — ⛔ **올리지 않는다.** 줄을 고쳤으면 같은 CL 에서 내린다.
const DIVERGENT_CEILING: usize = 21;

/// 이 자가 쓰는 탭바 — 정본 생성기의 스텁(`status.windows` 다섯 · 첫째가 활성)과 같은 모양.
fn tabs() -> proto::tabs::TabBar {
    proto::tabs::TabBar {
        tabs: (0..5)
            .map(|i| proto::tabs::Tab {
                index: i,
                name: format!("w{i}"),
                active: i == 0,
                ..Default::default()
            })
            .collect(),
        ..Default::default()
    }
}

/// 서버 명령 프레임을 정본 스텁과 같은 모양으로(`t` 를 떼고 `null` 칸을 뗀다).
fn wire(cmd: proto::command::Command) -> Value {
    let mut frame = cmd.to_frame();
    if let Value::Object(m) = &mut frame {
        m.remove("t");
        m.retain(|_, v| !v.is_null());
    }
    json!({ "wire": frame })
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// 우리 실행기의 결과가 **뷰에서 내보내는 것**. `local` 은 클라 안에서 끝나는 일이다.
fn our_effects(run: &base::cmdline::Run) -> Vec<Value> {
    use base::cmdline::Run;
    use base::{Action, Prompt};
    let local = || vec![json!({ "local": true })];
    match run {
        // GUI 세션 뷰가 직접 패널에 쓰는 둘(`session_view::apply_action`).
        Run::Act(Action::SendEscape) => vec![json!({ "input": "1b" })],
        Run::Act(Action::SendBacktick) => vec![json!({ "input": "60" })],
        Run::Act(Action::SourceFile) => vec![json!({ "reload": null })],
        // 판을 열면서 서버 절반을 묻는다(`session_view::apply_action` · pytmux-382).
        Run::Act(Action::ShowDebugStats) => vec![wire(proto::command::Command::DebugStats)],
        // 재시작은 드라이런을 먼저 묻는다(`session_view::begin_restart`).
        Run::Act(Action::RestartServer | Action::RestartAll) => {
            vec![wire(proto::command::Command::RequestRestartCheck)]
        }
        Run::Act(action) => match proto::command::action_to_command_with_tabs(*action, &tabs()) {
            Some(cmd) => vec![wire(proto::command::with_config_paths(cmd, "current", "claude"))],
            None => local(),
        },
        Run::Answer(Prompt::SendKeys, keys) => {
            let bytes = base::keys::parse_send_keys(keys);
            if bytes.is_empty() { vec![] } else { vec![json!({ "input": hex(&bytes) })] }
        }
        Run::Answer(Prompt::RunShell, cmd) => vec![json!({ "shell": cmd })],
        Run::Answer(Prompt::SetOption, line) => {
            let (opt, value) = line.split_once(' ').unwrap_or((line.as_str(), ""));
            vec![json!({ "set": [opt, value] })]
        }
        Run::Answer(Prompt::DisplayPopup, line) => match proto::command::parse_popup_line(line) {
            proto::command::PopupLine::Open(cmd) => vec![wire(cmd)],
            proto::command::PopupLine::Close => vec![wire(proto::command::Command::PopupClose)],
            proto::command::PopupLine::Usage => local(),
        },
        Run::Answer(prompt, answer) => match proto::answer::answer_command(*prompt, answer) {
            Some(cmd) => vec![wire(cmd)],
            None => local(),
        },
        Run::Flip(_) => local(),
        Run::IfShell { cond, then, otherwise } => {
            vec![json!({ "if": [cond, then, otherwise] })]
        }
        Run::SourceFile(path) => vec![json!({ "reload": path })],
    }
}

/// 나가는 것만 — `local` 을 걸러 낸다.
fn outgoing(effects: &[Value]) -> Vec<Value> {
    effects.iter().filter(|e| e.get("local").is_none()).cloned().collect()
}

/// 정본 프레임에 없는 `path`(새 탭·분할의 기본 자리)는 우리 쪽에서도 뗀다 — 정본 `send_cmd`
/// 가 **보낼 때** 붙이는 칸이라 생성기 스텁은 그 전의 모양을 싣는다.
fn align_path(canon: &[Value], ours: &mut [Value]) {
    for (c, o) in canon.iter().zip(ours.iter_mut()) {
        let canon_has = c.pointer("/wire/path").is_some();
        if !canon_has
            && let Some(Value::Object(m)) = o.get_mut("wire")
            && m.get("path") == Some(&json!("current"))
        {
            m.remove("path");
        }
    }
}

fn verdict(case: &Case) -> Result<(), String> {
    let run = base::cmdline::run_line(&case.line, &base::cmdline::Aliases::default());
    let canon = outgoing(&case.effects);
    let Some(run) = run else {
        return Err(format!("우리 실행기가 그 줄을 모른다(정본 효과 {:?})", case.effects));
    };
    let mut ours = outgoing(&our_effects(&run));
    align_path(&canon, &mut ours);
    if canon == ours {
        Ok(())
    } else {
        Err(format!("정본 {canon:?} · 우리 {ours:?} (실행기 {run:?})"))
    }
}

fn divergent(key: &str) -> Option<&'static (&'static str, &'static str, &'static str)> {
    DIVERGENT.iter().find(|(k, _, _)| *k == key)
}

#[test]
fn every_command_line_does_what_canon_does_or_is_a_named_divergence() {
    let fx = fixture();
    let mut new = Vec::new();
    let mut stale = Vec::new();
    for case in &fx.cases {
        match (verdict(case), divergent(&case.key)) {
            (Ok(()), Some((key, issue, _))) => stale
                .push(format!("`{key}`({issue}) — 이제 맞는다. DIVERGENT 에서 지우고 상한을 내릴 것")),
            (Err(why), None) => new.push(format!("`{}` [{}] — {why}", case.key, case.line)),
            _ => {}
        }
    }
    assert!(
        new.is_empty() && stale.is_empty(),
        "명령 줄이 정본과 갈린다.\n새 갈림:\n  {}\n낡은 줄:\n  {}",
        new.join("\n  "),
        stale.join("\n  ")
    );
}

#[test]
fn every_divergence_names_a_real_case_and_an_issue() {
    let fx = fixture();
    let keys: BTreeSet<&str> = fx.cases.iter().map(|c| c.key.as_str()).collect();
    for (key, issue, why) in DIVERGENT {
        assert!(keys.contains(key), "DIVERGENT 의 `{key}` 는 픽스처에 없는 칸이다");
        assert!(issue.starts_with("pytmux-"), "`{key}` 에 이슈 번호가 없다");
        assert!(!why.trim().is_empty(), "`{key}` 에 사유가 없다");
    }
    let unique: BTreeSet<&str> = DIVERGENT.iter().map(|(k, _, _)| *k).collect();
    assert_eq!(unique.len(), DIVERGENT.len(), "DIVERGENT 에 같은 칸이 두 번 섰다");
}

#[test]
fn the_divergence_count_only_goes_down() {
    assert!(
        DIVERGENT.len() <= DIVERGENT_CEILING,
        "못 맞는 줄이 {} 로 늘었다(상한 {DIVERGENT_CEILING}) — ⛔ 상한을 올리지 말고 고친다",
        DIVERGENT.len()
    );
    assert_eq!(
        DIVERGENT.len(),
        DIVERGENT_CEILING,
        "못 맞는 줄이 줄었다 — `DIVERGENT_CEILING` 도 {} 로 내릴 것",
        DIVERGENT.len()
    );
}

#[test]
fn the_ruler_bites_when_the_executor_drops_arguments() {
    // 변이 — 「실행기가 인자를 버리고 이름만 본다」(종전 `Bind::action` 의 모양)를 흉내 낸다:
    // 인자 있는 줄을 **이름만** 실행기에 먹이면 정본과 같던 줄 중 인자가 뜻을 바꾸는 줄은 반드시
    // 갈려야 한다. 하나도 안 갈리면 이 자는 인자를 안 재는 것이다.
    let fx = fixture();
    let mut bitten = 0;
    for case in fx.cases.iter().filter(|c| c.line.contains(' ') && divergent(&c.key).is_none()) {
        let head = case.line.split_whitespace().next().unwrap_or_default();
        let blind = Case { key: case.key.clone(), line: head.to_owned(), effects: case.effects.clone() };
        if verdict(&blind).is_err() {
            bitten += 1;
        }
    }
    assert!(bitten >= 20, "인자를 버려도 갈리는 줄이 {bitten} 뿐이다 — 자가 인자를 안 잰다");
}

#[test]
fn the_ruler_bites_when_the_executor_knows_nothing() {
    // 변이 — 실행기가 아무것도 모른다(모든 줄 `None`)면 정본이 무엇이든 내보내는 줄은 전부 운다.
    let fx = fixture();
    let loud = fx.cases.iter().filter(|c| !outgoing(&c.effects).is_empty()).count();
    assert!(loud >= 60, "정본이 무엇이든 내보내는 줄이 {loud} 뿐이다 — 대표 줄이 빈약하다");
}
