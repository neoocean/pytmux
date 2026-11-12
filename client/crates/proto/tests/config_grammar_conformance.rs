//! **설정 문법 적합성** — 같은 설정 파일을 두 클라가 같은 뜻으로 읽나(pytmux-567).
//!
//! # 왜 이 자가 필요한가
//!
//! 패리티 래칫의 모집단은 «설정 판에 뜨는 줄»(`clientutil.SETTINGS`)과 팔레트 이름이다.
//! 설정 파일은 사람이 손으로 쓰는 자리라 판에 안 뜨는 문법이 더 많다 — `alias` · `bind -n` ·
//! `hook`/`set-hook` · 판 밖의 `set` 옵션. 그 어느 것도 자가 안 셌다.
//!
//! # 어떻게 재나
//!
//! 생성기 `scripts/gen_config_grammar_fixture.py` 가 정본 파서(`keymap.py::load_config`)를
//! **그대로 불러** 대표 줄마다 「무엇을 읽었나」를 싣는다. 여기서는 같은 줄을 우리
//! [`base::Config::parse`] 에 먹여 **같은 칸에 같은 값이 섰나**를 잰다(양성 대조 — 「안 읽힌 게
//! 없다」가 아니라 「이 값이 읽혔다」). `bind` 줄은 한 걸음 더 간다: 그 키를 실제로 눌렀을 때
//! **걸린 명령이 도나**(정본은 아무 명령 줄이나 `_run_command` 로 돌린다).
//!
//! 런타임에만 받는 옵션(정본 `apply_option` − 파일 파서)은 「우리 `Config::parse` 가 그 줄로
//! 무엇이든 바꾸나」로 잰다 — GUI 의 런타임 `set` 은 설정 파일에 쓰고 다시 읽으므로
//! (`session_view::apply_set_line`) 그것이 곧 GUI 가 그 옵션을 받나다.
//!
//! # 못 받는 줄은 사유와 이슈가 있어야만 선다
//!
//! [`DIVERGENT`] 에 줄마다 이슈 번호와 사유가 있고, 그 수는 [`DIVERGENT_CEILING`] 을 못 넘는다
//! (`interaction.rs` 의 `UNMEASURED_CEILING` 과 같은 규칙 — **올리지 않는다**). 고쳐서 맞게
//! 되면 그 줄이 「이제 맞는다 — 지울 것」으로 울고, 상한도 같이 내린다.
//!
//! ⚠ 이 자는 **읽기**만 본다. 쓰는 쪽은 `config_write_conformance.rs` 가 잰다.

use std::collections::BTreeSet;

use serde::Deserialize;
use serde_json::{Map, Value, json};

#[derive(Deserialize)]
struct Case {
    key: String,
    line: String,
    #[serde(default)]
    read: Option<Map<String, Value>>,
    #[serde(default)]
    runtime: bool,
}

#[derive(Deserialize)]
struct Fx {
    directives: Vec<String>,
    bind_flags: Vec<String>,
    load_options: Vec<String>,
    apply_options: Vec<String>,
    cases: Vec<Case>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/config_grammar.json")).expect(
        "픽스처를 읽을 수 없다 — scripts/gen_config_grammar_fixture.py 로 다시 뽑을 것",
    )
}

/// 정본이 받는 명령 이름들(정본 `_run_command` 의 분기 전수 — 다른 생성기가 뽑는다).
fn canon_commands() -> BTreeSet<String> {
    #[derive(Deserialize)]
    struct Surface {
        client_cmds: Vec<String>,
    }
    let fx: Surface =
        serde_json::from_str(include_str!("fixtures/client_surface.json")).expect("client_surface");
    fx.client_cmds.into_iter().collect()
}

/// GUI 가 **못 받는** 줄 — (픽스처 칸의 열쇠, 이슈, 사유). 열쇠로 찾는다(줄 번호가 아니다).
const DIVERGENT: &[(&str, &str, &str)] = &[
    (
        "bind args",
        "pytmux-572",
        "`Bind::action` 이 팔레트 이름 그대로만 찾는다 — 인자 있는 명령(`select-tab 3`)은 \
         읽히되 누르면 아무 일도 안 한다",
    ),
    (
        "bind -n",
        "pytmux-600",
        "`normalize_bind_key` 가 이름 키(`F5`)를 소문자로 안 접는다 — 저장은 `F5`, 누를 때 찾는 \
         이름은 `f5` 라 눌러도 안 걸린다",
    ),
    (
        "set tabbar",
        "pytmux-600",
        "`Config::parse` 가 옵션 별칭 `tabbar` 를 모른다(`OPT_ALIASES` 는 쓰기만 본다)",
    ),
    (
        "alias",
        "pytmux-573",
        "`Config::parse` 가 `alias` 줄을 건너뛴다(사용자 명령 별칭 칸이 없다)",
    ),
    (
        "set mouse-debug",
        "pytmux-575",
        "런타임 `set` 만 받는 옵션 — GUI 에 없다",
    ),
    ("set mouse-log", "pytmux-575", "`mouse-debug` 의 별칭 — GUI 에 없다"),
    (
        "set status",
        "pytmux-575",
        "다줄 상태줄(`set status N`) — GUI 에 없다",
    ),
    (
        "set status-format",
        "pytmux-575",
        "보조 상태줄 포맷 — GUI 에 없다",
    ),
];

/// [`DIVERGENT`] 의 상한 — ⛔ **올리지 않는다.** 줄을 고쳤으면 같은 CL 에서 내린다.
const DIVERGENT_CEILING: usize = 8;

/// tmux 표기(`C-a`·`M-x`)를 정본이 들고 있는 Textual 표기(`ctrl+a`·`alt+x`)로.
fn textual(tmux: &str) -> String {
    let mut rest = tmux;
    let mut mods = Vec::new();
    loop {
        if let Some(r) = rest.strip_prefix("C-") {
            mods.push("ctrl");
            rest = r;
        } else if let Some(r) = rest.strip_prefix("M-") {
            mods.push("alt");
            rest = r;
        } else if let Some(r) = rest.strip_prefix("S-") {
            mods.push("shift");
            rest = r;
        } else {
            break;
        }
    }
    mods.push(rest);
    mods.join("+").to_ascii_lowercase()
}

/// 키 이름 표기의 차이(`ctrl+x` ↔ `ctrl-x` · 대소문자)를 지운다 — 표기가 아니라 **어느 키냐**를
/// 비교하려는 것이다. 그 키가 **실제로 눌리나**는 [`bound_command_runs`] 가 따로 잰다.
fn key_norm(key: &str) -> String {
    key.replace('+', "-").to_ascii_lowercase()
}

/// 우리 설정에서 정본 칸 하나에 해당하는 값. `Err` 는 **짝을 아직 안 정한 칸**이다(새 칸이
/// 정본에 생겼다는 뜻 — 조용히 넘기지 않는다).
fn gui_read(c: &base::Config, key: &str, canon: &Value) -> Result<Value, String> {
    let binds = |after_prefix: bool| {
        let mut m = Map::new();
        for b in c.binds.iter().filter(|b| b.after_prefix == after_prefix) {
            m.insert(key_norm(&b.key), json!(b.command));
        }
        Value::Object(m)
    };
    Ok(match key {
        "prefix" => json!(textual(&base::config::key_to_tmux(c.prefix))),
        "mouse" => json!(c.mouse),
        "mouse_drag_copy" => json!(c.mouse_drag_copy.as_str()),
        "set_clipboard" => json!(c.set_clipboard),
        "mouse_drag_threshold" => json!(c.mouse_drag_threshold),
        "copy_unwrap" => json!(c.copy_unwrap),
        "status_bg" => json!(c.status_bg),
        "status_fg" => json!(c.status_fg),
        "mode_keys" => json!(c.mode_keys),
        "status_left" => json!(c.status_left),
        "status_right" => json!(c.status_right),
        "status_position" => json!(c.status_position),
        "remote_title" => json!(c.remote_title),
        "status_interval" => json!(c.status_interval),
        "tab_bar_always" => json!(c.tab_bar_always),
        "set_titles" => json!(c.set_titles),
        "title_fmt" => json!(c.set_titles_string),
        "default_path" => json!(c.default_path),
        "claude_command" => json!(c.claude_command),
        "ambiguous_width" => json!(c.ambiguous_width),
        "lang" => json!(c.lang),
        "inactive_dim" => json!(c.inactive_dim),
        "inactive_dim_ratio" => json!(f64::from(c.inactive_dim_ratio)),
        "strip_box_drawing" => json!(c.strip_box_drawing),
        // 정본 칸 이름은 「끌까」(`disable_alt_scroll`), 우리는 같은 뜻의 `alt_scroll` 이다
        // (`set alt-scroll on` = 대체 스크롤을 끈다 · 두 쪽 머리말이 같은 말을 한다).
        "disable_alt_scroll" => json!(c.alt_scroll),
        "net_rtt_threshold" => json!(c.net_rtt_threshold),
        "bindings" => binds(true),
        "root_bindings" => binds(false),
        "hooks" => {
            let mut m = Map::new();
            for event in canon.as_object().into_iter().flat_map(|o| o.keys()) {
                if let Some(cmd) = c.hooks.get(event) {
                    m.insert(event.clone(), json!(cmd));
                }
            }
            Value::Object(m)
        }
        // 우리 설정에는 사용자 별칭 칸이 **없다** — 그 줄은 읽히지 않는다(pytmux-573).
        "aliases" => Value::Null,
        other => {
            return Err(format!(
                "정본 칸 `{other}` 의 짝을 이 시험이 모른다 — `gui_read` 에 더할 것"
            ));
        }
    })
}

/// 정본 칸 값의 표기를 맞춘다(지금은 키 이름 표기뿐).
fn canon_norm(key: &str, value: &Value) -> Value {
    match (key, value) {
        ("bindings" | "root_bindings", Value::Object(m)) => Value::Object(
            m.iter().map(|(k, v)| (key_norm(k), v.clone())).collect(),
        ),
        _ => value.clone(),
    }
}

/// 설정 파일의 키 표기(`r`·`C-x`·`M-z`·`F5`)를 **사용자가 실제로 누르는 키**로.
///
/// 우리 `Bind` 가 저장한 표기를 다시 읽지 않는다 — 그러면 「저장한 대로 읽었다」만 재고
/// 「그 키를 누르면 걸린다」를 못 잰다. 수정키는 tmux 접두(`C-`·`M-`)로 떼고, 나머지는
/// 백엔드가 주는 키 이름을 읽는 [`base::keys::from_name`] 에 넘긴다(글자 하나면 그 글자).
fn press(token: &str) -> Option<(base::keys::Key, base::keys::Mods)> {
    let mut rest = token;
    let mut mods = base::keys::Mods { ctrl: false, alt: false };
    loop {
        if let Some(r) = rest.strip_prefix("C-") {
            mods.ctrl = true;
            rest = r;
        } else if let Some(r) = rest.strip_prefix("M-") {
            mods.alt = true;
            rest = r;
        } else {
            break;
        }
    }
    let mut chars = rest.chars();
    let key = match (chars.next(), chars.next()) {
        (Some(c), None) => base::keys::Key::Char(c),
        _ => base::keys::from_name(&rest.to_ascii_lowercase(), false, None)?,
    };
    Some((key, mods))
}

/// `bind` 줄의 키를 실제로 눌렀을 때 **걸린 명령이 도나**. 정본은 그 명령이 정본 명령이면
/// 돌린다(`_run_command`) — 우리는 `user_action` 이 액션을 내야 돈다.
fn bound_command_runs(line: &str, config: &base::Config) -> Option<Result<(), String>> {
    let mut words = line.split_whitespace();
    if words.next()? != "bind" {
        return None;
    }
    let mut key = words.next()?;
    let after_prefix = key != "-n";
    if !after_prefix {
        key = words.next()?;
    }
    let head = words.next()?;
    if !canon_commands().contains(head) {
        return Some(Err(format!("정본도 `{head}` 를 모른다 — 대표 줄을 고를 것")));
    }
    let Some((k, mods)) = press(key) else {
        return Some(Err(format!("키 `{key}` 를 우리가 못 읽는다")));
    };
    Some(
        match base::config::user_action(&config.binds, after_prefix, k, mods) {
            Some(_) => Ok(()),
            None => Err(format!("`{key}` 를 눌러도 아무 일도 안 한다(정본은 그 명령을 돌린다)")),
        },
    )
}

/// 한 칸의 판정 — 맞으면 `Ok`, 갈리면 무엇이 갈렸나.
fn verdict(case: &Case, parsed: &base::Config) -> Result<(), String> {
    let blank = base::Config::parse("");
    if case.runtime {
        // 정본 런타임 `set` 이 받는 옵션 — 우리 쪽은 그 줄로 무엇이든 바뀌어야 받은 것이다.
        return if *parsed != blank {
            Ok(())
        } else {
            Err("그 줄로 아무 칸도 안 바뀐다(정본 `apply_option` 은 받는다)".into())
        };
    }
    let read = case.read.as_ref().ok_or("픽스처 칸에 `read` 가 없다")?;
    let mut diffs = Vec::new();
    for (key, want) in read {
        let want = canon_norm(key, want);
        let got = gui_read(parsed, key, &want)?;
        if got != want {
            diffs.push(format!("`{key}`: 정본 {want} · 우리 {got}"));
        }
    }
    if let Some(Err(why)) = bound_command_runs(&case.line, parsed) {
        diffs.push(why);
    }
    if diffs.is_empty() {
        Ok(())
    } else {
        Err(diffs.join(" · "))
    }
}

fn divergent(key: &str) -> Option<&'static (&'static str, &'static str, &'static str)> {
    DIVERGENT.iter().find(|(k, _, _)| *k == key)
}

#[test]
fn every_line_reads_as_canon_reads_or_is_a_named_divergence() {
    let fx = fixture();
    let mut new = Vec::new();
    let mut stale = Vec::new();
    for case in &fx.cases {
        let got = verdict(case, &base::Config::parse(&case.line));
        match (got, divergent(&case.key)) {
            (Ok(()), Some((key, issue, _))) => {
                stale.push(format!("`{key}`({issue}) — 이제 맞는다. DIVERGENT 에서 지우고 상한을 내릴 것"))
            }
            (Err(why), None) => new.push(format!("`{}` [{}] — {why}", case.key, case.line)),
            _ => {}
        }
    }
    assert!(
        new.is_empty() && stale.is_empty(),
        "설정 문법이 정본과 갈린다.\n\
         새 갈림(이슈를 세우고 DIVERGENT 에 사유와 함께 — 상한은 안 올린다):\n  {}\n\
         낡은 줄:\n  {}",
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
    // 양방향이다: 늘면 「상한을 올리지 말고 고쳐라」, 줄면 「상한도 내려라」 — 그래야 줄어든
    // 자리를 다음 갈림이 조용히 차지하지 못한다.
    assert!(
        DIVERGENT.len() <= DIVERGENT_CEILING,
        "못 받는 줄이 {} 로 늘었다(상한 {DIVERGENT_CEILING}) — ⛔ 상한을 올리지 말고 고친다",
        DIVERGENT.len()
    );
    assert_eq!(
        DIVERGENT.len(),
        DIVERGENT_CEILING,
        "못 받는 줄이 줄었다 — `DIVERGENT_CEILING` 도 {} 로 내릴 것",
        DIVERGENT.len()
    );
}

#[test]
fn the_population_is_the_whole_canon_parser() {
    // 「대표 줄」이 정본 파서가 가르는 것 **전부**를 덮나 — 생성기가 이미 실패로 막지만,
    // 픽스처를 손으로 고친 경우까지 여기서 다시 센다.
    let fx = fixture();
    assert!(!fx.directives.is_empty() && !fx.load_options.is_empty() && !fx.apply_options.is_empty(),
        "정본에서 뽑은 집합이 비었다 — 통과가 아니라 고장이다");
    let heads: BTreeSet<&str> =
        fx.cases.iter().filter_map(|c| c.line.split_whitespace().next()).collect();
    for d in &fx.directives {
        assert!(heads.contains(d.as_str()), "지시어 `{d}` 의 대표 줄이 없다");
    }
    for flag in &fx.bind_flags {
        assert!(
            fx.cases.iter().any(|c| c.line.starts_with(&format!("bind {flag} "))),
            "`bind {flag}` 의 대표 줄이 없다"
        );
    }
    let set_keys: BTreeSet<String> = fx
        .cases
        .iter()
        .filter_map(|c| c.key.strip_prefix("set ").map(str::to_owned))
        .collect();
    for opt in fx.load_options.iter().chain(&fx.apply_options) {
        assert!(set_keys.contains(opt), "옵션 `{opt}` 의 대표 줄이 없다");
    }
    // 런타임 칸은 정확히 「apply_option − 파일 파서」다.
    let load: BTreeSet<&String> = fx.load_options.iter().collect();
    let want: BTreeSet<String> = fx
        .apply_options
        .iter()
        .filter(|o| !load.contains(o))
        .map(|o| format!("set {o}"))
        .collect();
    let got: BTreeSet<String> =
        fx.cases.iter().filter(|c| c.runtime).map(|c| c.key.clone()).collect();
    assert_eq!(got, want, "런타임 칸이 정본 `apply_option` − `load_config` 와 다르다");
}

#[test]
fn the_ruler_bites_when_a_line_is_skipped() {
    // 변이 — 「`Config::parse` 가 그 지시어·옵션 줄을 건너뛴다」를 흉내 낸다: 그 줄 대신
    // **빈 파일**을 읽은 결과로 같은 판정을 돌리면 반드시 갈려야 한다. 안 갈리는 칸은 기본값을
    // 먹였거나 비교가 아무것도 안 재는 것이다(부정 단언만 남은 오라클).
    let fx = fixture();
    let skipped = base::Config::parse("");
    let mut toothless = Vec::new();
    for case in &fx.cases {
        if verdict(case, &skipped).is_ok() {
            toothless.push(case.key.clone());
        }
    }
    assert!(
        toothless.is_empty(),
        "그 줄을 건너뛰어도 자가 안 운다: {toothless:?}"
    );
}
