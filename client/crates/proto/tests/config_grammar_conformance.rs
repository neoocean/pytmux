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
struct NamedKey {
    token: String,
    canon: String,
}

#[derive(Deserialize)]
struct Fx {
    /// 정본 `_NAMED_KEYS` 의 열쇠 전수(이름 키 칸의 모집단).
    named_key_table: Vec<String>,
    /// 키 철자마다 정본 `_tmux_key_to_textual` 이 낸 이름(pytmux-600).
    named_keys: Vec<NamedKey>,
    /// 정본 `client.py::reload_config` 가 `cfg` 에서 다시 꺼내는 칸 전수(pytmux-574).
    reload_keys: Vec<String>,
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
///
/// 2026-11-13(CL 84389): 넷(`status` · `status-format` · `mouse-debug` · `mouse-log`)을 받아
/// 비었다(pytmux-575 — `base::config::runtime_set`).
const DIVERGENT: &[(&str, &str, &str)] = &[];

/// [`DIVERGENT`] 의 상한 — ⛔ **올리지 않는다.** 줄을 고쳤으면 같은 CL 에서 내린다.
const DIVERGENT_CEILING: usize = 0;

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
        // 사용자 명령 별칭(pytmux-573) — 적은 이름 그대로 · 명령은 낱말을 공백 하나로 이은 것.
        "aliases" => {
            let mut m = Map::new();
            for (name, command) in c.aliases.iter() {
                m.insert(name.to_owned(), json!(command));
            }
            Value::Object(m)
        }
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

/// 정본이 저장한 키 이름(Textual 표기 `ctrl+left`·`pageup`·`f5`·`G`)을 **사용자가 실제로 누르는
/// 키**로. 설정 파일의 철자가 아니라 **정본이 그 철자를 접은 결과**에서 출발한다 — 그래야
/// 「정본이 이 키로 읽은 줄을, 우리도 이 키를 누르면 돌리나」를 잰다(우리 `Bind` 가 저장한 표기를
/// 다시 읽으면 「저장한 대로 읽었다」만 잰다).
///
/// `None` = 우리 이름 표기로 **나타낼 수 없는 키**(`shift+left` 같은 것 — 이 클라의 키 이름
/// 표에 없다).
fn press_canon(textual: &str) -> Option<(base::keys::Key, base::keys::Mods)> {
    let mut mods = base::keys::Mods { ctrl: false, alt: false };
    let mut shift = false;
    let mut parts: Vec<&str> = textual.split('+').collect();
    let base_name = parts.pop()?;
    for m in parts {
        match m {
            "ctrl" => mods.ctrl = true,
            "alt" => mods.alt = true,
            "shift" => shift = true,
            _ => return None,
        }
    }
    let mut chars = base_name.chars();
    let key = match (chars.next(), chars.next()) {
        (Some(c), None) if !shift => base::keys::Key::Char(c),
        _ => base::keys::from_name(base_name, shift, None)?,
    };
    base::keys::binding_name_with(key, mods).map(|_| (key, mods))
}

/// `bind` 줄을 정본이 읽은 키로 눌렀을 때 **걸린 명령이 도나**. 정본은 그 명령이 정본 명령이면
/// 돌린다(`_run_command`) — 우리는 `user_action` 이 할 일을 내야 돈다.
fn bound_command_runs(case: &Case, config: &base::Config) -> Option<Result<(), String>> {
    let mut words = case.line.split_whitespace();
    if words.next()? != "bind" {
        return None;
    }
    let read = case.read.as_ref()?;
    let (after_prefix, table) = match (read.get("bindings"), read.get("root_bindings")) {
        (Some(t), _) => (true, t),
        (None, Some(t)) => (false, t),
        _ => return None,
    };
    let (canon_key, command) = table.as_object()?.iter().next()?;
    let head = command.as_str()?.split_whitespace().next()?;
    if !canon_commands().contains(head) {
        return Some(Err(format!("정본도 `{head}` 를 모른다 — 대표 줄을 고를 것")));
    }
    let Some((k, mods)) = press_canon(canon_key) else {
        return Some(Err(format!("정본 키 `{canon_key}` 를 우리가 못 누른다")));
    };
    Some(
        match base::config::user_action(&config.binds, &config.aliases, after_prefix, k, mods) {
            Some(_) => Ok(()),
            None => Err(format!(
                "`{canon_key}` 를 눌러도 아무 일도 안 한다(정본은 그 명령을 돌린다)"
            )),
        },
    )
}

/// 런타임 전용 옵션을 받는 함수의 모양([`base::config::runtime_set`]) — 변이가 갈아 끼운다.
type RuntimeSetFn = fn(&mut base::config::RuntimeOpts, &str, &str) -> Option<base::config::RuntimeSet>;

/// 한 칸의 판정 — 맞으면 `Ok`, 갈리면 무엇이 갈렸나.
fn verdict(case: &Case, parsed: &base::Config) -> Result<(), String> {
    verdict_with(case, parsed, base::config::runtime_set)
}

fn verdict_with(case: &Case, parsed: &base::Config, runtime_set: RuntimeSetFn) -> Result<(), String> {
    let blank = base::Config::parse("");
    if case.runtime {
        // 정본 런타임 `set` 이 받는 옵션 — 우리 쪽은 그 줄로 무엇이든 바뀌어야 받은 것이다.
        // 런타임 전용 셋(`base::config::runtime_set` — 설정 파일에 안 쓴다)은 그 값 묶음이,
        // 나머지는 설정 파일을 거쳐 다시 읽은 설정이 바뀌어야 한다(`session_view::apply_set_line`).
        if let Some(rest) = case.line.trim().strip_prefix("set ") {
            let (option, value) = rest.trim().split_once(' ').unwrap_or((rest.trim(), ""));
            let mut rt = base::config::RuntimeOpts::default();
            if runtime_set(&mut rt, option, value.trim()).is_some() {
                return if rt != base::config::RuntimeOpts::default() {
                    Ok(())
                } else {
                    Err("런타임 값이 그 줄로 안 바뀐다(정본 `apply_option` 은 바꾼다)".into())
                };
            }
        }
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
    if let Some(Err(why)) = bound_command_runs(case, parsed) {
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
fn every_key_spelling_folds_to_the_key_canon_binds() {
    // pytmux-600 ⓑ — `bind -n F5 …` 가 `F5` 로 저장되고 누를 때 찾는 이름은 `f5` 라 발동하지
    // 않았다. 철자마다: 정본이 접은 이름의 키를 **실제로 누를 때** 우리가 찾는 이름 ==
    // 우리 `normalize_bind_key` 가 그 철자를 접은 이름.
    let fx = fixture();
    let mut wrong = Vec::new();
    let mut measured = 0;
    for nk in &fx.named_keys {
        let Some((k, mods)) = press_canon(&nk.canon) else {
            // 우리가 못 누르는 키 — 지금은 하나도 없어야 한다(있으면 이름 표에 빠진 키다).
            wrong.push(format!("`{}` → 정본 `{}` — 그 키에 우리 이름이 없다", nk.token, nk.canon));
            continue;
        };
        let want = base::keys::binding_name_with(k, mods).expect("press_canon 이 이름을 확인했다");
        let got = base::config::normalize_bind_key(&nk.token);
        measured += 1;
        if got != want {
            wrong.push(format!("`{}` → 정본 `{}` = 우리 이름 `{want}` · 우리가 접은 것 `{got}`", nk.token, nk.canon));
        }
    }
    assert!(wrong.is_empty(), "키 철자가 정본과 다르게 접힌다:\n  {}", wrong.join("\n  "));
    // 모집단 — 정본 이름 키 표의 열쇠가 전부 토큰에 있다.
    for name in &fx.named_key_table {
        assert!(
            fx.named_keys.iter().any(|nk| nk.token.eq_ignore_ascii_case(name)),
            "정본 이름 키 `{name}` 의 철자가 픽스처에 없다"
        );
    }
    assert_eq!(measured, fx.named_keys.len(), "못 잰 철자가 있다");
}

#[test]
fn an_alias_line_runs_its_command_from_the_prompt_and_from_bind() {
    // pytmux-573 관문 — `alias sp split-window -h` 를 읽은 설정에서 `sp` 가 명령 프롬프트(명령 줄
    // 실행기)와 `bind` 두 입구에서 **그 명령과 같은 일**을 하나. 정본은 이름 해석 앞에서 별칭을
    // 풀고 뒤에 친 인자를 붙인다(`self.aliases[c] + " " + args`).
    let fx = fixture();
    let mut measured = 0;
    for case in fx.cases.iter().filter(|c| c.line.starts_with("alias ")) {
        let read = case.read.as_ref().and_then(|r| r.get("aliases")).and_then(Value::as_object);
        for (name, expansion) in read.into_iter().flatten() {
            let expansion = expansion.as_str().expect("별칭 명령은 글자다");
            let config = base::Config::parse(&format!("{}\nbind -n F9 {name}", case.line));
            let want = base::cmdline::run_line(expansion, &base::cmdline::Aliases::default());
            assert!(want.is_some(), "대표 줄의 명령 `{expansion}` 을 우리가 모른다 — 대표 줄을 고를 것");
            assert_eq!(base::cmdline::run_line(name, &config.aliases), want, "프롬프트 `:{name}`");
            let pressed = base::config::user_action(
                &config.binds,
                &config.aliases,
                false,
                base::keys::Key::Function(9),
                base::keys::Mods { ctrl: false, alt: false },
            );
            assert_eq!(pressed, want, "`bind -n F9 {name}`");
            measured += 1;
        }
    }
    assert!(measured > 0, "별칭 대표 줄이 하나도 없다 — 통과가 아니라 고장이다");
}

#[test]
fn source_file_rereads_every_field_canon_reloads() {
    // pytmux-574 — 정본 `reload_config(path)` 가 갱신하는 칸마다: 그 칸을 바꾸는 대표 줄 하나를
    // **임시 파일에 써서** `source-file <그 경로>` 의 읽기(`Config::load_from`)로 읽으면 그 칸이
    // 정본이 읽은 값으로 서나. 뷰는 다시 읽은 설정을 통째로 갈아 끼우고(`self.config`) 설정 밖
    // 상태는 기동과 같은 한 함수(`proto::config_apply`)로 민다 — 아래 둘째 시험이 그쪽을 잰다.
    let fx = fixture();
    let dir = std::env::temp_dir().join(format!("pytmux-reload-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("임시 디렉터리");
    let mut wrong = Vec::new();
    for key in &fx.reload_keys {
        let Some(case) = fx
            .cases
            .iter()
            .filter(|c| divergent(&c.key).is_none())
            .find(|c| c.read.as_ref().is_some_and(|r| r.contains_key(key)))
        else {
            wrong.push(format!("`{key}` 를 바꾸는 대표 줄이 픽스처에 없다"));
            continue;
        };
        let path = dir.join(format!("{}.conf", key));
        std::fs::write(&path, format!("{}\n", case.line)).expect("임시 설정 파일");
        let reread = base::Config::load_from(path.to_str());
        if let Err(why) = verdict(case, &reread) {
            wrong.push(format!("`{key}` [{}] — {why}", case.line));
        }
    }
    // 없는 경로 — 정본은 그 후보를 건너뛰고 **기본 탐색으로 떨어진다**(아무 말도 안 한다).
    let missing = dir.join("no-such-file.conf");
    assert_eq!(
        base::Config::load_from(missing.to_str()),
        base::Config::load(),
        "없는 경로를 주면 기본 탐색과 같아야 한다(정본 `load_config` 의 candidates)"
    );
    let _ = std::fs::remove_dir_all(&dir);
    assert!(wrong.is_empty(), "다시 읽어도 안 바뀌는 칸:\n  {}", wrong.join("\n  "));
}

#[test]
fn reload_pushes_the_state_outside_the_config_like_startup() {
    // pytmux-574 — 설정 밖의 상태(폭 판정 전역)는 기동과 같은 한 함수가 민다. 종전 `source-file`
    // 은 그 함수를 안 지나 `set ambiguous-width wide` 가 다시 읽기로는 안 먹었다.
    let mut state = proto::SessionState::new();
    let wide = base::Config::parse("set ambiguous-width wide");
    proto::config_apply::apply_config_globals(&wide, &mut state);
    assert!(proto::compose::ambiguous_wide(), "wide 를 다시 읽었는데 폭 판정이 안 바뀌었다");
    proto::config_apply::apply_config_globals(&base::Config::parse(""), &mut state);
    assert!(!proto::compose::ambiguous_wide(), "기본으로 다시 읽었는데 폭 판정이 남았다");
}

#[test]
fn the_ruler_bites_when_a_line_is_skipped() {
    // 변이 — 「`Config::parse` 가 그 지시어·옵션 줄을 건너뛴다」를 흉내 낸다: 그 줄 대신
    // **빈 파일**을 읽은 결과로 같은 판정을 돌리면 반드시 갈려야 한다. 안 갈리는 칸은 기본값을
    // 먹였거나 비교가 아무것도 안 재는 것이다(부정 단언만 남은 오라클).
    let fx = fixture();
    let skipped = base::Config::parse("");
    let mut toothless = Vec::new();
    // 런타임 전용 셋도 같다 — 그 줄을 **안 받는** 실행기(`None`)면 운다.
    let deaf: RuntimeSetFn = |_, _, _| None;
    for case in &fx.cases {
        if verdict_with(case, &skipped, deaf).is_ok() {
            toothless.push(case.key.clone());
        }
    }
    assert!(
        toothless.is_empty(),
        "그 줄을 건너뛰어도 자가 안 운다: {toothless:?}"
    );
}
