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
    /// `remote` = 활성 탭이 원격인 판(정본 스텁 `RemoteRec` — 호스트 `box1` · 병합 index 3).
    #[serde(default)]
    ctx: Option<String>,
}

#[derive(Deserialize)]
struct MenuCase {
    key: String,
    effects: Vec<Value>,
}

#[derive(Deserialize)]
struct Fx {
    cases: Vec<Case>,
    menu: Vec<MenuCase>,
}

/// 정본 스텁(`gen_command_line_fixture.py`)의 원격 판 — 호스트와 활성 탭의 병합 index.
const REMOTE_HOST: &str = "box1";
const REMOTE_ACTIVE: usize = 3;

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/command_lines.json"))
        .expect("픽스처를 읽을 수 없다 — scripts/gen_command_line_fixture.py 로 다시 뽑을 것")
}

/// 못 맞는 줄 — (픽스처 칸의 열쇠, 이슈, 사유). 열쇠로 찾는다.
///
/// 2026-11-13(CL 84389): 스물하나를 전부 고쳐 비었다(pytmux-572 잔여 · 624). 새 줄은 사유와
/// 이슈가 있어야만 선다.
const DIVERGENT: &[(&str, &str, &str)] = &[];

/// [`DIVERGENT`] 의 상한 — ⛔ **올리지 않는다.** 줄을 고쳤으면 같은 CL 에서 내린다.
const DIVERGENT_CEILING: usize = 0;

/// 이 자가 쓰는 탭바 — 정본 생성기의 스텁(`status.windows` 다섯 · `active` 째가 활성)과 같은 모양.
fn tabs(active: usize) -> proto::tabs::TabBar {
    proto::tabs::TabBar {
        tabs: (0..5)
            .map(|i| proto::tabs::Tab {
                index: i,
                name: format!("w{i}"),
                active: i == active,
                ..Default::default()
            })
            .collect(),
        ..Default::default()
    }
}

/// 정본 스텁의 판 — 탭바 · 패널(`layout = {"active": 11, "panes": [11, 12, 13]}`) · 원격.
struct Ctx {
    tabs: proto::tabs::TabBar,
    remote: Option<(&'static str, usize)>,
}

const PANES: &[i64] = &[11, 12, 13];

impl Ctx {
    fn of(case_ctx: Option<&str>) -> Self {
        match case_ctx {
            Some("remote") => Ctx { tabs: tabs(REMOTE_ACTIVE), remote: Some((REMOTE_HOST, REMOTE_ACTIVE)) },
            Some(other) => panic!("픽스처의 모르는 ctx `{other}` — 이 자에 그 판을 더할 것"),
            None => Ctx { tabs: tabs(0), remote: None },
        }
    }

    fn line(&self) -> proto::command::LineCtx<'_> {
        proto::command::LineCtx {
            tabs: &self.tabs,
            panes: PANES,
            active_pane: Some(PANES[0]),
            remote: self.remote,
        }
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
fn our_effects(run: &base::cmdline::Run, ctx: &Ctx) -> Vec<Value> {
    use base::cmdline::Run;
    use base::{Action, Prompt};
    let local = || vec![json!({ "local": true })];
    match run {
        // 확인 판을 세우는 액션(`session_view::apply_action` — 표는 `base::cmdline::confirm_gate`
        // 한 곳 · 뷰가 그 표를 따르는지는 `session_view_tests` 가 잰다). 판만 뜨고 아무것도 안 나간다.
        Run::Act(action) if base::cmdline::confirm_gate(*action) => vec![json!({ "local": "confirm" })],
        Run::Line(line) => match proto::command::line_command(line, &ctx.line()) {
            proto::command::LineOut::Send(cmd) => vec![wire(cmd)],
            proto::command::LineOut::Input(bytes) => vec![json!({ "input": hex(&bytes) })],
            proto::command::LineOut::Nothing => vec![],
            proto::command::LineOut::Local | proto::command::LineOut::BadTab(_) => local(),
        },
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
        Run::Act(action) => match proto::command::action_to_command_with_tabs(*action, &ctx.tabs) {
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

/// 확인 판을 세웠나 — 정본은 `confirm_*` · `prompt:confirm` 으로, 우리는 `confirm` 으로 남는다.
fn confirms(effects: &[Value]) -> bool {
    effects.iter().any(|e| {
        e.get("local")
            .and_then(Value::as_str)
            .is_some_and(|s| s.starts_with("confirm") || s == "prompt:confirm")
    })
}

/// 같은 일인가 — **나가는 것**이 같고, 확인 판을 세우는지도 같아야 한다(pytmux-624: 확인 판
/// 뒤의 kill 은 판만 뜨고 아무것도 안 나가므로 나가는 것만 보면 «안 나감 = 안 나감» 으로 통과할
/// 수 있다).
fn same(canon_all: &[Value], ours_all: &[Value]) -> Result<(), String> {
    let canon = outgoing(canon_all);
    let mut ours = outgoing(ours_all);
    align_path(&canon, &mut ours);
    if canon != ours {
        return Err(format!("정본 {canon:?} · 우리 {ours:?}"));
    }
    if confirms(canon_all) != confirms(ours_all) {
        return Err(format!("확인 판이 갈린다 — 정본 {canon_all:?} · 우리 {ours_all:?}"));
    }
    Ok(())
}

fn verdict(case: &Case) -> Result<(), String> {
    let run = base::cmdline::run_line(&case.line, &base::cmdline::Aliases::default());
    let Some(run) = run else {
        return Err(format!("우리 실행기가 그 줄을 모른다(정본 효과 {:?})", case.effects));
    };
    let ctx = Ctx::of(case.ctx.as_deref());
    same(&case.effects, &our_effects(&run, &ctx)).map_err(|why| format!("{why} (실행기 {run:?})"))
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
        let blind = Case {
            key: case.key.clone(),
            line: head.to_owned(),
            effects: case.effects.clone(),
            ctx: case.ctx.clone(),
        };
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

#[test]
fn every_menu_row_goes_through_the_same_gate_as_canon() {
    // pytmux-624 — 메뉴의 «확인을 거치나 / 바로 나가나» 를 정본 `_run_menu_action` 과 줄마다 견준다.
    // 메뉴 줄 → 액션은 우리 메뉴 표(`base::menu_entry`), 그 액션이 지나는 길은
    // `base::cmdline::menu_run` — 세션 뷰의 메뉴 선택이 부르는 그 함수다.
    let fx = fixture();
    assert!(fx.menu.len() >= 4, "메뉴 칸이 빈약하다 — 생성기의 MENU_KEYS 를 볼 것");
    let ctx = Ctx::of(None);
    let mut wrong = Vec::new();
    for row in &fx.menu {
        let Some(entry) = base::menu_entry(&row.key) else {
            wrong.push(format!("`{}` — 우리 메뉴 표에 없다", row.key));
            continue;
        };
        let run = base::cmdline::menu_run(&row.key, entry.action);
        if let Err(why) = same(&row.effects, &our_effects(&run, &ctx)) {
            wrong.push(format!("`{}` — {why}", row.key));
        }
    }
    assert!(wrong.is_empty(), "메뉴 줄이 정본과 갈린다:\n  {}", wrong.join("\n  "));
}

#[test]
fn typed_kills_skip_the_confirm_board_and_keys_keep_it() {
    // pytmux-624 의 표 — 정본 픽스처에서 **친 명령** 칸을 뽑아 그 성격(바로 / 확인)이 우리와
    // 같은지 본다. 표의 명령이 픽스처에서 빠지면 운다(전수).
    let fx = fixture();
    let table = [
        ("kill-tab", false),
        ("killw", false),
        ("kill-tab remote", false),
        ("killw remote", false),
        ("kill-pane", false),
        ("killp", false),
        ("kill-server", true),
        ("detach", false),
        ("detach -a", false),
    ];
    for (key, confirm) in table {
        let case = fx.cases.iter().find(|c| c.key == key).unwrap_or_else(|| panic!("픽스처에 `{key}` 가 없다"));
        assert_eq!(confirms(&case.effects), confirm, "정본 `{key}` 의 성격이 표와 다르다 — 표를 정본에 맞출 것");
        assert!(verdict(case).is_ok(), "`{key}` 가 정본과 갈린다: {:?}", verdict(case));
    }
    // 키·클릭 입구(`[x]` · `esc x` · `prefix &` · `prefix x` · 팔레트에서 고른 줄)는 액션이고,
    // 그 셋은 확인 판을 세운다 — 정본 `confirm_kill_tab` · `kill-pane? (y/N)` · `confirm_kill_server`.
    for action in [base::Action::KillTab, base::Action::KillPane, base::Action::KillServer] {
        assert!(base::cmdline::confirm_gate(action), "{action:?} 가 확인 판을 안 세운다");
    }
}

#[test]
fn the_ruler_bites_when_typed_kills_go_through_the_confirm_board() {
    // 변이 — 종전 모양(친 `kill-tab` 이 `Action::KillTab` 으로 가서 확인 판을 세운다)을 흉내 낸다.
    let fx = fixture();
    let ctx = Ctx::of(None);
    for key in ["kill-tab", "kill-pane"] {
        let case = fx.cases.iter().find(|c| c.key == key).expect("픽스처에 칸이 있다");
        let action = if key == "kill-tab" { base::Action::KillTab } else { base::Action::KillPane };
        let old = base::cmdline::Run::Act(action);
        assert!(same(&case.effects, &our_effects(&old, &ctx)).is_err(), "`{key}` 를 확인 판으로 보내도 자가 안 운다");
    }
}
