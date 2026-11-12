//! **명령 줄 실행기** — 명령 줄 한 줄을 «무엇을 할지»로 옮기는 자리는 여기 하나다(pytmux-572).
//!
//! # 왜 한 벌인가
//!
//! 명령 줄은 다섯 입구에서 온다 — 명령 프롬프트(팔레트 입력줄에 이어 친 인자) · 설정 파일
//! `bind` · `if-shell` 의 then/else · `set-hook` 훅 · 사용자 별칭(`alias`). 정본은 그 전부를
//! `clientcmd._run_command` **한 함수**로 돌린다. 종전 우리 쪽은 입구마다 해석이 달랐다:
//! `Bind::action` 과 `take_shell_result` 는 줄 전체를 팔레트 이름으로만 찾아 `send-keys C-c`
//! 같은 **인자 있는 명령이 조용히 무동작**이었고, 훅·프롬프트만 [`crate::hooks`] 의 해석기를
//! 지났다. 두 벌이면 갈린다 — 그래서 [`run_line`] 하나로 모았다.
//!
//! # 차례는 정본과 같다
//!
//! 1. 앞뒤 공백을 떼고, 빈 줄이면 아무 일도 아니다.
//! 2. 명령 이름 토큰의 **한영 오타**를 되돌린다(정본 `hangul_to_qwerty` — 이름은 늘 ASCII).
//! 3. 이름을 소문자로 접고 **사용자 별칭**을 먼저 푼다 — 별칭 뒤에 친 인자는 그대로 붙는다
//!    (정본 `self.aliases[c] + " " + " ".join(args)`). 별칭이 별칭을 부르면 다시 푼다 —
//!    깊이 [`MAX_DEPTH`] 를 넘으면 멈춘다(정본 `_depth > 8`).
//! 4. 그 다음이 이름 해석(내장 별칭 [`crate::resolve_command_name`])과 인자다.
//!
//! ⚠ 이 모듈은 **무엇을 할지**만 정한다. 실제로 하는 것(큐에 넣기·화면 띄우기)은 뷰가 한다 —
//! `base` 는 UI 도 와이어도 모른다(계층 게이트). 결과가 서버로 무엇을 내보내는지는
//! `proto/tests/command_line_conformance.rs` 가 정본 픽스처와 줄마다 잰다.

use crate::keymap::{Action, PALETTE};
use crate::options::OptionPick;
use crate::screens::Prompt;

/// 별칭 풀기의 깊이 한도 — 정본 `_run_command(line, _depth)` 의 `_depth > 8` 과 같다.
///
/// 별칭이 자기를 부르면(`alias a a`) 이 한도에서 멈추고 **아무 일도 안 한다**(정본도 그 자리에서
/// 조용히 돌아간다).
pub const MAX_DEPTH: usize = 8;

/// 명령 줄 하나가 뜻하는 일.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Run {
    /// 팔레트에서 고른 것과 **같은 길**로 가는 액션(확인 화면도 그대로 뜬다).
    Act(Action),
    /// 인자를 받는 명령 — 그 물음에 이 대답이 나온 것처럼 처리한다.
    Answer(Prompt, String),
    /// 우리 설정 파일의 켜고끄기를 뒤집는다(지금 값은 설정이 안다 — `strip-box-drawing`).
    Flip(&'static str),
    /// `if-shell <조건> <then> [else]` — 조건을 셸로 돌려 성공이면 then, 아니면 else 를
    /// **다시 이 실행기로** 돌린다(정본 `_if_shell` 이 `_run_command` 를 재귀로 부른다).
    IfShell { cond: String, then: String, otherwise: Option<String> },
    /// `source-file [경로]` — 경로가 없으면 기본 탐색 경로를 다시 읽는다.
    SourceFile(Option<String>),
}

/// 사용자 명령 별칭 — 설정 파일의 `alias <이름> <명령…>`.
///
/// 적은 순서를 지키고, 같은 이름을 다시 적으면 **자리는 그대로 두고 값만** 바꾼다(정본
/// `dict` 대입과 같다 — [`crate::hooks::Hooks`] 와 같은 규칙).
///
/// 이름은 **적힌 그대로** 든다. 찾을 때는 친 이름을 소문자로 접어 찾는다 — 정본이 그렇다
/// (`c = parts[0].lower()` 로 `self.aliases` 를 찾는다). 그래서 대문자가 든 별칭 이름은
/// 정본에서도 영영 안 걸린다 — 갈리지 않게 같은 규칙을 따른다.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Aliases {
    entries: Vec<(String, String)>,
}

impl Aliases {
    pub fn set(&mut self, name: &str, command: &str) {
        match self.entries.iter_mut().find(|(k, _)| k == name) {
            Some(slot) => slot.1 = command.to_owned(),
            None => self.entries.push((name.to_owned(), command.to_owned())),
        }
    }

    pub fn get(&self, name: &str) -> Option<&str> {
        self.entries
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, v)| v.as_str())
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn iter(&self) -> impl Iterator<Item = (&str, &str)> {
        self.entries.iter().map(|(k, v)| (k.as_str(), v.as_str()))
    }
}

/// 명령 줄 한 줄을 할 일로 옮긴다. 모르는 이름은 `None` — **조용히 넘긴다**(정본 `_run_command`
/// 의 끝 「알 수 없는 명령은 조용히 무시」와 같다).
pub fn run_line(line: &str, aliases: &Aliases) -> Option<Run> {
    run_at(line, aliases, 0)
}

/// `setw` 가 인자로 가르는 갈래 — 정본 elif 사슬의 차례 그대로(automatic-rename → monitor-* →
/// synchronize-panes → pane-border-status). `monitor-*` 는 `monitor-bell` 이 있으면 벨, 아니면
/// 활동이다(정본 `"monitor-bell" in args`).
pub const SETW_TARGETS: &[&str] =
    &["automatic-rename", "monitor-activity", "monitor-bell", "synchronize-panes", "pane-border-status"];

/// 이 **이름**을 실행기가 받나(별칭 표·사용자 별칭 밖의 이름 해석) — 패리티 래칫이 묻는 자리다.
///
/// `setw` 는 이름만으로는 아무 일도 안 한다(인자로 가른다) — 그 갈래 전부를 받으면 받는 것이다.
pub fn knows(name: &str) -> bool {
    let name = fold_name(name);
    if name == "setw" {
        return SETW_TARGETS.iter().all(|t| knows(t));
    }
    resolve(&name, "").is_some()
        || crate::hooks::arg_prompt(crate::resolve_command_name(&name)).is_some()
}

/// 이 줄의 첫 낱말이 **사용자 별칭**인가(명령 프롬프트가 팔레트 목록 대신 실행기로 보낼지 정한다).
pub fn is_alias(line: &str, aliases: &Aliases) -> bool {
    let head = line.split_whitespace().next().unwrap_or("");
    !head.is_empty() && aliases.get(&fold_name(head)).is_some()
}

fn fold_name(head: &str) -> String {
    // 비-한글은 그대로 두는 변환이라 늘 지나도 된다(정본은 `has_hangul` 로 먼저 묻는다 — 결과는 같다).
    crate::screens::hangul_to_qwerty(head).to_lowercase()
}

fn run_at(line: &str, aliases: &Aliases, depth: usize) -> Option<Run> {
    if depth > MAX_DEPTH {
        return None;
    }
    let line = line.trim();
    if line.is_empty() {
        return None;
    }
    let (head, rest) = match line.split_once(char::is_whitespace) {
        Some((h, r)) => (h, r.trim()),
        None => (line, ""),
    };
    let name = fold_name(head);
    if let Some(expanded) = aliases.get(&name) {
        let next = if rest.is_empty() {
            expanded.to_owned()
        } else {
            format!("{expanded} {rest}")
        };
        return run_at(&next, aliases, depth + 1);
    }
    resolve(&name, rest)
}

/// 정본 `shlex.split` 과 같은 규칙으로 쪼갠다 — 따옴표가 안 닫혔으면 공백으로 쪼갠다(정본이
/// `ValueError` 에서 `line.split()` 으로 떨어지는 것과 같다).
pub fn shell_words(text: &str) -> Vec<String> {
    shlex(text).unwrap_or_else(|| text.split_whitespace().map(str::to_owned).collect())
}

fn shlex(text: &str) -> Option<Vec<String>> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut in_word = false;
    let mut chars = text.chars();
    while let Some(c) = chars.next() {
        match c {
            '\'' => {
                in_word = true;
                loop {
                    match chars.next()? {
                        '\'' => break,
                        x => cur.push(x),
                    }
                }
            }
            '"' => {
                in_word = true;
                loop {
                    match chars.next()? {
                        '"' => break,
                        '\\' => match chars.next()? {
                            x @ ('"' | '\\' | '$' | '`') => cur.push(x),
                            '\n' => {}
                            x => {
                                cur.push('\\');
                                cur.push(x);
                            }
                        },
                        x => cur.push(x),
                    }
                }
            }
            '\\' => {
                in_word = true;
                match chars.next() {
                    Some('\n') => {}
                    Some(x) => cur.push(x),
                    None => return None,
                }
            }
            c if c.is_whitespace() => {
                if in_word {
                    out.push(std::mem::take(&mut cur));
                    in_word = false;
                }
            }
            c => {
                in_word = true;
                cur.push(c);
            }
        }
    }
    if in_word {
        out.push(cur);
    }
    Some(out)
}

/// 이름 하나(별칭을 이미 푼, 소문자)와 그 뒤 원문.
fn resolve(name: &str, rest: &str) -> Option<Run> {
    let words = shell_words(rest);
    // ── `setw` 는 이름이 아니라 **인자로** 가른다(정본 `c == "setw" and … in args`). 갈래의
    //    차례도 정본 elif 사슬 그대로다: automatic-rename → monitor-* → synchronize-panes →
    //    pane-border-status. 맞는 갈래가 없으면 정본도 아무 일도 안 한다.
    if name == "setw" {
        let has = |w: &str| words.iter().any(|a| a == w);
        for target in SETW_TARGETS {
            let hit = match *target {
                "monitor-activity" => words.iter().any(|a| a.contains("monitor-")) && !has("monitor-bell"),
                "monitor-bell" => has("monitor-bell"),
                t => has(t),
            };
            if hit {
                return resolve(target, rest);
            }
        }
        return None;
    }
    let canonical = crate::resolve_command_name(name);
    // 내장 별칭이 플래그를 품은 이름으로 갈 수 있다(`zoom` → `resize-pane -Z`) — 그 경우는
    // 그 줄 그대로가 뜻이다.
    if canonical.contains(' ') && rest.is_empty() {
        return palette_exact(canonical).map(Run::Act);
    }
    let canonical = canonical.split(' ').next().unwrap_or(canonical);
    if let Some(decided) = with_args(canonical, rest, &words) {
        return decided;
    }
    // 줄 전체가 팔레트 이름(`split-window -h` · `select-pane -t next`)이면 그 줄이다 — 팔레트에
    // **플래그를 이름에 품은** 줄이 있어서 첫 낱말만 떼면 엉뚱하게 갈린다.
    let whole = if rest.is_empty() {
        canonical.to_owned()
    } else {
        format!("{canonical} {rest}")
    };
    if let Some(action) = palette_exact(&whole)
        && !matches!(action, Action::ShowCommandOptions(_))
    {
        return Some(Run::Act(action));
    }
    // 인자 폼이 있는 명령 — 인자를 그 폼의 선택지로 읽는다(사람이 폼에서 고른 것과 같은 길).
    if let Some(options) = crate::options::options_for(canonical) {
        return option_run(options, &words);
    }
    if let Some(prompt) = crate::hooks::arg_prompt(canonical) {
        // 인자가 비었으면 물음을 띄우는 편이 낫다 — 사람이 친 자리면 무엇을 이어 칠지 보이고,
        // 훅이 도는 자리면 빈 명령을 돌리는 것보다 이유가 남는다.
        return Some(if rest.is_empty() {
            match palette_exact(canonical) {
                Some(action) => Run::Act(action),
                None => Run::Answer(prompt, String::new()),
            }
        } else {
            Run::Answer(prompt, rest.to_owned())
        });
    }
    // 인자를 안 받는 이름 — 인자는 버리고 명령은 살린다(정본도 그 갈래에서 인자를 안 본다).
    palette_exact(canonical).map(Run::Act)
}

fn palette_exact(name: &str) -> Option<Action> {
    PALETTE.iter().find(|e| e.name == name).map(|e| e.action)
}

/// 인자 폼이 있는 명령(`synchronize-panes on` · `split-window -h` …).
fn option_run(options: &crate::options::CommandOptions, words: &[String]) -> Option<Run> {
    let spec = options.specs.first()?;
    // 인자 중 선택지 값과 같은 낱말 — 없으면 값이 빈 선택지(토글·보이는 영역), 그것도 없으면
    // 정본이 인자 없을 때 하는 일을 이 표의 첫 줄로 고른다(`split-window` = 상하 `-v`).
    let index = spec
        .choices
        .iter()
        .position(|c| !c.value.is_empty() && words.iter().any(|w| w.eq_ignore_ascii_case(c.value)))
        .or_else(|| spec.choices.iter().position(|c| c.value.is_empty()))
        .or_else(|| bare_default(options.command, spec))?;
    match crate::options::pick(options, &[index])? {
        OptionPick::Act(action) => Some(Run::Act(action)),
        OptionPick::Set(key, value) => Some(Run::Answer(Prompt::SetOption, format!("{key} {value}"))),
        OptionPick::Flip(key) => Some(Run::Flip(key)),
    }
}

/// 인자가 없을 때 정본이 하는 일이 «값이 빈 선택지» 가 아닌 명령들. `None` 이면 정본도 그
/// 자리에서 아무 일도 안 한다(`select-pane` 맨이름 · `lang` 맨이름 = 사용법만).
fn bare_default(command: &str, spec: &crate::options::OptionSpec) -> Option<usize> {
    let want = match command {
        // 정본: `orient = "lr" if "-h" in args else "tb"`.
        "split-window" => "-v",
        _ => return None,
    };
    spec.choices.iter().position(|c| c.value == want)
}

/// 정본이 **인자로 무엇을 보낼지 정하는** 명령 — 팔레트 줄 하나로는 그 인자가 안 실리는 것들.
///
/// `None` = 이 함수가 안 정한다(뒤의 일반 규칙으로) · `Some(None)` = 정본이 그 자리에서 **아무
/// 일도 안 한다**(예: 조건만 있는 `if-shell`) · `Some(Some(run))` = 그 일.
fn with_args(name: &str, rest: &str, words: &[String]) -> Option<Option<Run>> {
    let positional: Vec<&str> =
        words.iter().map(String::as_str).filter(|w| !w.starts_with('-')).collect();
    let has = |flag: &str| words.iter().any(|w| w == flag);
    Some(match name {
        // `select-tab N` — 화면에 보이는 번호(1-based)다. 액션도 같은 번호를 든다
        // (`proto::command::action_to_command_with_tabs` 가 시각 순서로 푼다).
        "select-tab" if !rest.is_empty() => {
            first_positive(words).and_then(|n| u8::try_from(n).ok()).map(|n| Run::Act(Action::SelectTab(n)))
        }
        // tmux 의 `-b`(백그라운드) 깃발은 늘 그렇다 — 명령으로 읽지 않는다.
        //
        // ⚠ 정본은 그 뒤 **첫 낱말**만 셸에 넘긴다(`_run_shell(args[0])`) — 따옴표로 감싼
        //   명령(`run-shell 'echo hi'`)은 두 클라가 같다. 따옴표 **없이** 친 여러 낱말
        //   (`run-shell echo hi there`)은 우리가 줄의 나머지 전부를 넘긴다(종전 GUI 동작 ·
        //   반토막 셸 명령을 안 만든다). 그 한 칸은 `command_line_conformance` 의 갈림
        //   대장에 사유와 함께 선다 — 맞출지는 사람이 정한다.
        "run-shell" if !rest.is_empty() => {
            let cmd: Vec<&String> = words.iter().filter(|w| *w != "-b").collect();
            match cmd.as_slice() {
                [] => None,
                [one] => Some(Run::Answer(Prompt::RunShell, (*one).clone())),
                _ => {
                    let raw = rest.strip_prefix("-b").map_or(rest, str::trim_start);
                    Some(Run::Answer(Prompt::RunShell, raw.to_owned()))
                }
            }
        }
        "if-shell" if !rest.is_empty() => (words.len() >= 2).then(|| Run::IfShell {
            cond: words[0].clone(),
            then: words[1].clone(),
            otherwise: words.get(2).cloned(),
        }),
        // 경로가 있을 때만 여기다 — 맨이름은 팔레트 줄(`Action::SourceFile` = 기본 탐색)이다.
        "source-file" if !rest.is_empty() => {
            words.first().map(|path| Run::SourceFile(Some(path.clone())))
        }
        "send-keys" if !rest.is_empty() && !has("-l") => {
            Some(Run::Answer(Prompt::SendKeys, positional.join(" ")))
        }
        "set" if !rest.is_empty() => {
            (positional.len() >= 2).then(|| Run::Answer(Prompt::SetOption, positional.join(" ")))
        }
        "rename-tab" if !rest.is_empty() => {
            let label = positional.join(" ");
            (!label.is_empty()).then(|| Run::Answer(Prompt::RenameTab, label))
        }
        "layout-save" | "layout-load" | "layout-load-new" if !rest.is_empty() => {
            let label = positional.join(" ");
            let prompt = match name {
                "layout-save" => Prompt::SaveTabLayout,
                "layout-load" if has("-n") => Prompt::LoadTabLayoutNew,
                "layout-load" => Prompt::LoadTabLayout,
                _ => Prompt::LoadTabLayoutNew,
            };
            (!label.is_empty()).then(|| Run::Answer(prompt, label))
        }
        "pipe-pane" if !rest.is_empty() => Some(Run::Answer(Prompt::PipePane, positional.join(" "))),
        "display-message" if !rest.is_empty() => {
            Some(Run::Answer(Prompt::DisplayMessage, words.join(" ")))
        }
        "rename-pane" if !rest.is_empty() => Some(Run::Answer(Prompt::RenamePane, words.join(" "))),
        "resize-pane" if !rest.is_empty() => {
            if has("-Z") {
                Some(Run::Act(Action::Zoom))
            } else {
                words.iter().find_map(|w| flag_dir(w)).map(|d| Run::Act(Action::ResizePane(d)))
            }
        }
        "select-pane" if !rest.is_empty() => {
            if let Some(i) = words.iter().position(|w| w == "-T") {
                Some(Run::Answer(Prompt::RenamePane, words[i + 1..].join(" ")))
            } else if let Some(d) = ["-L", "-R", "-U", "-D"].into_iter().find(|f| has(f)) {
                flag_dir(d).map(|d| Run::Act(Action::SelectPane(d)))
            } else {
                // 정본이 모르는 깃발 — 우리 팔레트 줄(`select-pane -t next`)이면 그 줄이다.
                return None;
            }
        }
        "swap-pane" if !rest.is_empty() && !has("-s") && !has("-t") => {
            Some(Run::Act(Action::SwapPane { forward: !has("-U") }))
        }
        "capture-pane" => Some(Run::Act(Action::CapturePane(has("-S") || has("-a")))),
        // 맨이름은 **순환**이다(정본 `cycle_layout`). 팔레트에서 이 줄을 고르면 프리셋 판이
        // 뜨지만(그건 팔레트의 손), 명령 줄로 오면 정본과 같이 돈다.
        "select-layout" if rest.is_empty() => Some(Run::Act(Action::CycleLayout)),
        // 맨이름은 **전부 떼기**다(정본 `remote_detach` 에 host 없이) — 물음을 띄우지 않는다.
        "remote-detach" if rest.is_empty() => Some(Run::Answer(Prompt::RemoteDetach, String::new())),
        "inactive-dim-ratio" if !rest.is_empty() => {
            let value = words.first()?;
            value
                .parse::<f32>()
                .ok()
                .map(|_| Run::Answer(Prompt::SetOption, format!("inactive-dim-ratio {value}")))
        }
        _ => return None,
    })
}

fn flag_dir(word: &str) -> Option<crate::Dir> {
    Some(match word {
        "-L" => crate::Dir::Left,
        "-R" => crate::Dir::Right,
        "-U" => crate::Dir::Up,
        "-D" => crate::Dir::Down,
        _ => return None,
    })
}

fn first_positive(words: &[String]) -> Option<usize> {
    // `-t N` 이 먼저, 없으면 첫 정수 낱말(정본 `_tab_target_index`).
    if let Some(i) = words.iter().position(|w| w == "-t")
        && let Some(n) = words.get(i + 1).and_then(|w| w.parse::<usize>().ok())
    {
        return (n > 0).then_some(n);
    }
    words
        .iter()
        .filter(|w| !w.starts_with('-'))
        .find_map(|w| w.parse::<usize>().ok())
        .filter(|n| *n > 0)
}

#[cfg(test)]
#[path = "cmdline_tests.rs"]
mod tests;
