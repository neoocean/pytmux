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
    /// **친 명령만의 일** — 팔레트 액션 하나로는 인자가 안 실리거나, 키·클릭과 뜻이 다른
    /// 줄(정본 `_run_command` 의 갈래 그대로). 무엇이 나가는지는 `proto::command::line_command`
    /// 한 곳이 정한다(뷰와 적합성 자가 같은 함수를 부른다).
    Line(Line),
}

/// 탭 번호를 받는 세 명령(정본 `_tab_target_index` 를 지나는 갈래).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TabVerb {
    Select,
    Move,
    Swap,
}

/// 명령 줄로만 오는 일([`Run::Line`]).
///
/// ★ 키·클릭과 **입구가 다른** 것이 여기 선다(pytmux-624): 정본은 친 `kill-tab`·`kill-pane`
///   을 확인 없이 바로 보내고, 확인 판은 `[x]`·`esc x`·`prefix &`·`prefix x` 의 몫이다. 그래서
///   친 줄은 [`Action::KillTab`](확인을 세운다)이 아니라 [`Line::KillTab`] 이다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Line {
    /// 친 번호 그대로 — 양수는 1-based, 음수는 끝에서(정본 `_tab_target_index`).
    Tab(TabVerb, i64),
    /// `resize-pane -L|-R|-U|-D [N]` — 칸 수(기본 3 · 정본 `_first_int(args) or 3`).
    ResizePane { dir: crate::Dir, cells: u16 },
    /// `rotate-window [-D]`.
    Rotate { forward: bool },
    /// `swap-pane -s A -t B` — 패널 번호(0-based, display-panes 번호). 숫자가 아니면 `None`
    /// (정본 `_pane_id_by_index` 가 비숫자를 `None` 으로 접는다 → 활성 패널).
    SwapPaneTo { src: Option<usize>, dst: Option<usize> },
    /// `select-layout <프리셋>` — 이름은 정본처럼 **그대로** 보낸다.
    SelectLayout(String),
    /// `join-pane [-h]` — 정본은 `orient` 만 보낸다(대상 = 서버가 정하는 직전 탭).
    JoinPane { horizontal: bool },
    /// `detach -a` — 같은 세션의 다른 클라를 뗀다.
    DetachOthers,
    /// `paste-buffer [N]`.
    PasteBuffer(usize),
    /// 저장된 탭 배치 목록을 묻는다(`layout-list` · 맨이름 `layout-load`) — 회신이 오면 고른다.
    ListLayouts { new: bool },
    /// `pane-border-status [on|top|off]` — `None` 은 뒤집기.
    BorderStatus(Option<bool>),
    /// 패널로 바로 보낼 바이트(`send-keys -l` — 글자 그대로).
    Input(Vec<u8>),
    /// `window-size` 맨이름 = 순환(값 없이 보낸다).
    WindowSizeCycle,
    /// `window-size <모르는 값>` — 정본은 사용법만 알린다.
    WindowSizeUsage,
    /// 친 `kill-tab` — 확인 없이(로컬 = `kill_window` · 활성 원격 탭 = 그 탭만 떼기).
    KillTab,
    /// 친 `kill-pane` — 확인 없이.
    KillPane,
    /// `inactive-dim [on|off]` — **이번 판만**(설정 파일에 안 쓴다). `None` 은 뒤집기.
    InactiveDim(Option<bool>),
    /// `strip-box-drawing [on|off]` — 이번 판만. `None` 은 뒤집기.
    StripBoxDrawing(Option<bool>),
    /// `inactive-dim-ratio [값]` — 이번 판만. `None` 은 지금 값을 알리기만.
    InactiveDimRatio(Option<String>),
}

/// `inactive-dim-ratio` 의 값 — 정본 `max(0.0, min(0.8, float(arg)))`. 숫자가 아니면 `None`.
pub fn dim_ratio(arg: &str) -> Option<f32> {
    arg.trim().parse::<f32>().ok().filter(|v| v.is_finite()).map(|v| v.clamp(0.0, 0.8))
}

/// 이 액션을 뷰가 **확인 판 뒤에** 세우나 — 키·클릭·메뉴가 지나는 입구(pytmux-624).
///
/// 정본에서 확인을 거치는 것은 `confirm_kill_tab`(탭 닫기 · `[x]`·`esc x`·`prefix &`) ·
/// `prefix x` 의 `kill-pane? (y/N)` · `confirm_kill_server` 셋이다. 세션 뷰의
/// `apply_action` 이 이 표대로 확인 판을 세운다(`session_view_tests` 가 이 표를 전수로 잰다).
pub fn confirm_gate(action: Action) -> bool {
    matches!(action, Action::KillTab | Action::KillPane | Action::KillServer)
}

/// 팔레트(명령 판)에서 **고른** 줄이 하는 일 — 키 입구와 갈리는 것은 kill 둘뿐이다(pytmux-624).
pub fn palette_run(action: Action) -> Run {
    // 정본의 명령 입구 둘(`:` 줄 · 명령 목록 판)은 고른 이름을 결국 `_run_command` 로 돌린다 —
    // 목록 판은 인자 없는 명령이 아니면 `kill-tab ` 을 프롬프트에 채워 Enter 를 기다린다.
    // 그래서 팔레트(우리의 `:` 줄)에서 고른 `kill-tab`·`kill-pane` 은 **친 명령**이다(확인 없이).
    match action {
        Action::KillTab => Run::Line(Line::KillTab),
        Action::KillPane => Run::Line(Line::KillPane),
        other => Run::Act(other),
    }
}

/// 메뉴(`prefix Enter` · 우클릭)에서 고른 줄이 하는 일 — 정본 `_run_menu_action` 의 갈래.
///
/// 대부분은 키로 누른 것과 같은 길(액션)이지만, 정본 메뉴의 `kill_pane` 은 **확인 없이**
/// `kill_pane` 을 보낸다(같은 일을 `prefix x` 는 묻는다). 그 갈림을 여기 한 곳이 든다 —
/// 표는 `gen_command_line_fixture.py` 가 정본을 불러 뽑은 `menu` 칸과 견준다.
pub fn menu_run(key: &str, action: Action) -> Run {
    match key {
        "kill_pane" => Run::Line(Line::KillPane),
        _ => Run::Act(action),
    }
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
        //   음수(끝에서)·0·큰 번호는 탭 수를 알아야 풀린다 — [`Line::Tab`] 으로 넘긴다.
        "select-tab" if !rest.is_empty() => tab_target(words).map(|n| {
            match u8::try_from(n).ok().filter(|n| *n > 0) {
                Some(n) => Run::Act(Action::SelectTab(n)),
                None => Run::Line(Line::Tab(TabVerb::Select, n)),
            }
        }),
        // 정본은 1-based 번호를 0-based 자리로 접어 보낸다(`_tab_target_index`) — 그 셈은 탭
        // 수를 아는 쪽(`proto::command::line_command`)이 한다. 번호가 없으면 정본도 무동작이다.
        "move-tab" if !rest.is_empty() => tab_target(words).map(|n| Run::Line(Line::Tab(TabVerb::Move, n))),
        "swap-tab" if !rest.is_empty() => tab_target(words).map(|n| Run::Line(Line::Tab(TabVerb::Swap, n))),
        // 친 `kill-tab`·`kill-pane` 은 **확인 없이** 나간다(정본 `_run_command` · pytmux-624).
        // 확인 판은 키·클릭(`Action::KillTab`·`Action::KillPane`)의 몫이다.
        "kill-tab" => Some(Run::Line(Line::KillTab)),
        "kill-pane" => Some(Run::Line(Line::KillPane)),
        "rotate-window" if !rest.is_empty() => Some(Run::Line(Line::Rotate { forward: !has("-D") })),
        "select-layout" if !rest.is_empty() => {
            words.first().map(|preset| Run::Line(Line::SelectLayout(preset.clone())))
        }
        // 맨이름도 같다 — 정본은 `orient` 만 싣는다(물음을 띄우지 않는다).
        "join-pane" => Some(Run::Line(Line::JoinPane { horizontal: has("-h") })),
        "detach-client" if has("-a") => Some(Run::Line(Line::DetachOthers)),
        "paste-buffer" if !rest.is_empty() => {
            Some(Run::Line(Line::PasteBuffer(first_unsigned(words).unwrap_or(0))))
        }
        "layout-list" => Some(Run::Line(Line::ListLayouts { new: false })),
        "layout-load" if positional.is_empty() => Some(Run::Line(Line::ListLayouts { new: has("-n") })),
        "layout-load-new" if positional.is_empty() => Some(Run::Line(Line::ListLayouts { new: true })),
        // 정본: `on`/`top` = 켜기 · `off` = 끄기 · 그 밖 = 뒤집기(값 없이).
        "pane-border-status" => Some(Run::Line(Line::BorderStatus(if has("on") || has("top") {
            Some(true)
        } else if has("off") {
            Some(false)
        } else {
            None
        }))),
        // 세 켜고끄기는 정본에서 **이번 판만** 바꾼다(설정 파일의 값은 기본값일 뿐이다).
        "inactive-dim" => Some(Run::Line(Line::InactiveDim(on_off(words)))),
        "strip-box-drawing" => Some(Run::Line(Line::StripBoxDrawing(on_off(words)))),
        "inactive-dim-ratio" => Some(Run::Line(Line::InactiveDimRatio(words.first().cloned()))),
        "window-size" if rest.is_empty() => Some(Run::Line(Line::WindowSizeCycle)),
        "window-size" => {
            let value = words[0].to_lowercase();
            if ["smallest", "latest", "largest"].contains(&value.as_str()) {
                return None;
            }
            Some(Run::Line(Line::WindowSizeUsage))
        }
        // tmux 의 `-b`(백그라운드) 깃발은 늘 그렇다 — 명령으로 읽지 않는다.
        //
        // 정본은 그 뒤 **첫 낱말**만 셸에 넘긴다(`_run_shell(args[0])`) — 따옴표 없이 친 여러
        //   낱말(`run-shell echo hi there`)은 `echo` 만 돈다. 사람 결정(2026-11-13): 정본을
        //   따른다. 여러 낱말 명령은 따옴표로 감싼다(`run-shell 'echo hi there'`).
        "run-shell" if !rest.is_empty() => words
            .iter()
            .find(|w| *w != "-b")
            .map(|cmd| Run::Answer(Prompt::RunShell, cmd.clone())),
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
        // `-l` = 글자 그대로 — 이름 키(`Enter`·`C-c`)를 접지 않고 낱말을 **이어 붙여** 보낸다
        // (정본 `_send_keys` 의 `literal` 갈래 · 낱말 사이에 공백을 안 넣는 것도 정본 그대로).
        "send-keys" => {
            let bytes: Vec<u8> = positional.iter().flat_map(|w| w.bytes()).collect();
            (!bytes.is_empty()).then(|| Run::Line(Line::Input(bytes)))
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
                let cells = first_unsigned(words)
                    .and_then(|n| u16::try_from(n).ok())
                    .filter(|n| *n > 0)
                    .unwrap_or(3);
                words
                    .iter()
                    .find_map(|w| flag_dir(w))
                    .map(|dir| Run::Line(Line::ResizePane { dir, cells }))
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
        // `-s`/`-t` <번호> — display-panes 의 0-based 번호(정본 `_pane_id_by_index`).
        "swap-pane" => {
            let pane = |flag: &str| opt_value(words, flag).and_then(|w| ascii_digits(w));
            Some(Run::Line(Line::SwapPaneTo { src: pane("-s"), dst: pane("-t") }))
        }
        "capture-pane" => Some(Run::Act(Action::CapturePane(has("-S") || has("-a")))),
        // 맨이름은 **순환**이다(정본 `cycle_layout`). 팔레트에서 이 줄을 고르면 프리셋 판이
        // 뜨지만(그건 팔레트의 손), 명령 줄로 오면 정본과 같이 돈다.
        "select-layout" if rest.is_empty() => Some(Run::Act(Action::CycleLayout)),
        // 맨이름은 **전부 떼기**다(정본 `remote_detach` 에 host 없이) — 물음을 띄우지 않는다.
        "remote-detach" if rest.is_empty() => Some(Run::Answer(Prompt::RemoteDetach, String::new())),
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

/// 정본 `_opt_value` — 깃발 바로 뒤 낱말.
fn opt_value<'a>(words: &'a [String], flag: &str) -> Option<&'a str> {
    let i = words.iter().position(|w| w == flag)?;
    words.get(i + 1).map(String::as_str)
}

/// ASCII 숫자만으로 된 낱말(정본 `isascii() and isdigit()`).
fn ascii_digits(word: &str) -> Option<usize> {
    if !word.is_empty() && word.bytes().all(|b| b.is_ascii_digit()) {
        word.parse().ok()
    } else {
        None
    }
}

/// 정본 `_signed_int` — 앞의 `-` 하나를 허용하는 정수 낱말.
fn signed_int(word: &str) -> Option<i64> {
    let body = word.strip_prefix('-').unwrap_or(word);
    ascii_digits(body)?;
    word.parse().ok()
}

/// 정본 `_first_int` — 깃발·음수를 건너뛴 첫 양의 정수(칸 수·버퍼 번호).
fn first_unsigned(words: &[String]) -> Option<usize> {
    words.iter().filter(|w| !w.starts_with('-')).find_map(|w| ascii_digits(w))
}

/// 정본 `_tab_target_index` 의 앞 절반 — `-t N` 이 먼저, 없으면 첫 정수 낱말(음수 포함).
/// 범위 셈(탭 수)은 [`Line::Tab`] 을 푸는 쪽이 한다.
fn tab_target(words: &[String]) -> Option<i64> {
    opt_value(words, "-t")
        .and_then(signed_int)
        .or_else(|| words.iter().find_map(|w| signed_int(w)))
}

/// `on`/`off` 낱말 — 없으면 `None`(뒤집기 · 정본 `"on" in args` 가 먼저다).
fn on_off(words: &[String]) -> Option<bool> {
    if words.iter().any(|w| w == "on") {
        Some(true)
    } else if words.iter().any(|w| w == "off") {
        Some(false)
    } else {
        None
    }
}

#[cfg(test)]
#[path = "cmdline_tests.rs"]
mod tests;
