//! **이벤트 훅** — 어떤 일이 생기면 명령 한 줄을 자동으로 돌린다(`set-hook`/`show-hooks`).
//!
//! # 이건 **클라이언트** 기능이다
//!
//! 로드맵 §5.1 은 오래 "서버가 사건을 안 알려 줘서 막혔다"고 적고 있었다. **틀렸다.**
//! 파이썬 정본(`client._fire_hook` 호출부 셋)은 서버가 보내 주는 사건을 기다리지 않는다 —
//! **이미 받고 있는 것에서 사건을 유도한다**:
//!
//! | 사건 | 정본이 무엇을 보고 발화하나 |
//! |---|---|
//! | `client-attached` | **첫 `layout`** 이 왔다(붙었다는 뜻) |
//! | `after-new-window` | `status.windows` 의 **개수가 늘었다** |
//! | `alert-bell` | `status.windows` 중 하나에 **벨이 새로 켜졌다** |
//!
//! 셋 다 우리가 이미 받는 메시지다. 서버 변경은 **필요 없다**(이 트랙에서 "막혔다"를
//! 잘못 적은 것이 다섯 번째다 — 규칙: 없다고 적기 전에 이미 받는 것을 본다).
//!
//! # 가장자리 판정이 규칙이다
//!
//! 셋 다 **상태가 아니라 전이**에 걸린다. 매 프레임 "탭이 있다"로 발화하면 훅 명령이
//! 초당 여러 번 돈다 — 그건 훅이 아니라 폭주다. [`HookWatcher`] 가 직전 값을 들고 있는
//! 이유가 그것이다.
//!
//! 탭 개수는 **직전 값이 0이면 발화하지 않는다**(정본의 `if self._prev_winc and …`).
//! 붙자마자 탭 셋이 보이는 것은 "탭이 셋 생긴" 것이 아니라 원래 있던 것이다.

use crate::screens::Prompt;

/// 코어가 스스로 발화하는 사건 셋. 이름은 정본과 **글자까지 같아야 한다** —
/// 설정 파일을 두 클라가 공유하므로(로드맵 결정 3) 여기서 철자가 갈리면 한쪽에서 적은
/// 훅이 다른 쪽에서 조용히 안 먹는다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HookEvent {
    /// 서버에 붙어 첫 배치를 받았다.
    ClientAttached,
    /// 탭이 새로 생겼다.
    AfterNewWindow,
    /// 어느 탭에서 벨이 울렸다.
    AlertBell,
    /// 활성 패널의 Claude 가 사용량 한도에 걸렸다(pytmux-588 · **플러그인발** 사건 —
    /// 정본 `claude-code` 의 `claude.py:saver_hook_events`). 재료는 이미 받는 status 의
    /// `active_pane`·`panes_claude[].claude`(문자열 상태)·`claude_account` 다 — 서버 변경 없음.
    ClaudeLimit,
}

impl HookEvent {
    pub fn name(self) -> &'static str {
        match self {
            HookEvent::ClientAttached => "client-attached",
            HookEvent::AfterNewWindow => "after-new-window",
            HookEvent::AlertBell => "alert-bell",
            HookEvent::ClaudeLimit => "claude-limit",
        }
    }

    /// 우리가 **실제로 발화하는** 것들. 정본 전수와의 대조는
    /// `proto/tests/hook_events_conformance.rs`(답지 = 정본 AST + `saver_hook_events`).
    pub const ALL: &'static [HookEvent] = &[
        HookEvent::ClientAttached,
        HookEvent::AfterNewWindow,
        HookEvent::AlertBell,
        HookEvent::ClaudeLimit,
    ];
}

/// 발화 한 번 — 사건과 그 훅 명령에 실을 환경(pytmux-588).
///
/// 정본 `_fire_hook(event, env)` 은 env 를 `_run_command` 동안만 `os.environ` 에 심고
/// 되돌린다. 그래서 그 값이 닿는 곳은 훅 줄이 **그 순간** 띄우는 클라측 셸(`run-shell`·
/// `if-shell` 조건)뿐이다. 코어 세 사건은 정본도 env 를 **안 싣는다**(빈 목록이 정본과 같다).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HookFire {
    pub event: HookEvent,
    pub env: Vec<(String, String)>,
}

impl From<HookEvent> for HookFire {
    fn from(event: HookEvent) -> Self {
        HookFire { event, env: Vec::new() }
    }
}

/// 걸어 둔 훅들 — `이벤트 → 명령 한 줄`.
///
/// # 왜 `Vec` 인가(해시맵이 아니라)
///
/// `show-hooks` 가 **적은 순서대로** 보여야 한다. 파이썬 `dict` 가 삽입 순서를 지키므로
/// 저쪽 화면과 줄 순서가 같으려면 우리도 순서를 들어야 한다. 같은 이벤트를 다시 걸면
/// **자리는 그대로 두고 값만** 바꾼다(파이썬 `dict` 대입과 같다) — 값 하나 고쳤다고
/// 목록이 재배열되면 무엇이 바뀌었는지 눈으로 못 쫓는다.
///
/// # 이벤트 이름을 검사하지 않는 이유
///
/// 파이썬은 아무 문자열이나 키로 받는다(플러그인이 자기 사건 이름을 쓴다 — `claude-limit`
/// 등). 우리가 [`HookEvent::ALL`] 로 좁히면 플러그인 훅을 적어 둔 **공유 설정 파일이
/// 우리 쪽에서만 반쯤 사라진다**. 모르는 이름은 들고만 있고 발화하지 않는다.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Hooks {
    entries: Vec<(String, String)>,
}

impl Hooks {
    pub fn set(&mut self, event: &str, command: &str) {
        match self.entries.iter_mut().find(|(k, _)| k == event) {
            Some(slot) => slot.1 = command.to_owned(),
            None => self.entries.push((event.to_owned(), command.to_owned())),
        }
    }

    /// 지운다. 지울 것이 있었으면 `true` — 호출부가 "그런 훅 없다"를 말할 수 있다.
    pub fn unset(&mut self, event: &str) -> bool {
        let before = self.entries.len();
        self.entries.retain(|(k, _)| k != event);
        self.entries.len() != before
    }

    pub fn get(&self, event: &str) -> Option<&str> {
        self.entries
            .iter()
            .find(|(k, _)| k == event)
            .map(|(_, v)| v.as_str())
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// `show-hooks` 화면에 그릴 줄들. 화살표까지 파이썬과 같다(`f"{k} → {v}"`).
    ///
    /// 비어 있으면 **빈 목록이 아니라 한 줄**을 돌려준다 — 아무것도 없는 화면은
    /// "훅이 없다"와 "화면이 고장났다"를 구별해 주지 않는다.
    pub fn lines(&self) -> Vec<String> {
        if self.entries.is_empty() {
            return vec![String::from(crate::i18n::t(
                "걸어 둔 훅이 없습니다 (set-hook <이벤트> <명령>)",
            ))];
        }
        self.entries
            .iter()
            .map(|(k, v)| format!("{k} → {v}"))
            .collect()
    }
}

/// `set-hook` 한 줄이 뜻하는 것.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SetHook {
    Set { event: String, command: String },
    Unset { event: String },
}

/// `set-hook` 의 인자를 읽는다 — `<이벤트> <명령…>` 또는 `-u <이벤트>`.
///
/// 정본과 같은 규칙: 이벤트 이름 **앞의** `-` 낱말만 깃발이고(`-u` 면 지우기), 이름 뒤는
/// 원문 그대로 명령이다(그래서 `run-shell "echo hi"` 가 따옴표째 남는다).
pub fn parse_set_hook(args: &str) -> Option<SetHook> {
    // ★ 깃발(`-g`·`-u` …)은 **이벤트 이름 앞의 것만**이다(pytmux-536 · 정본
    //   `clientcmd.parse_set_hook` 와 같은 규칙). 종전에는 `-` 로 시작하는 낱말을 어디서든
    //   걸러 내 명령 안의 `run-shell -b …` 의 `-b` 가 사라졌다.
    //   명령은 이름 뒤의 **원문 그대로**다 — 따옴표를 지키므로 발화 때 다시 쪼개도 같은
    //   명령이 된다.
    let mut rest = args.trim_start();
    let mut unset = false;
    loop {
        let (word, tail) = match rest.split_once(char::is_whitespace) {
            Some((w, t)) => (w, t.trim_start()),
            None => (rest, ""),
        };
        if word.is_empty() {
            return None;
        }
        if word.starts_with('-') {
            if word == "-u" {
                unset = true;
            }
            rest = tail;
            continue;
        }
        if unset {
            return Some(SetHook::Unset { event: word.to_owned() });
        }
        let command = tail.trim_end();
        if command.is_empty() {
            return None;
        }
        return Some(SetHook::Set { event: word.to_owned(), command: command.to_owned() });
    }
}

/// 가장자리를 재는 자. 상태가 아니라 **전이**를 본다(모듈 문서 참조).
#[derive(Debug, Clone, Default)]
pub struct HookWatcher {
    attached: bool,
    prev_windows: usize,
    prev_bell: bool,
    prev_limit: bool,
}

impl HookWatcher {
    /// status 하나의 Claude 재료로 `claude-limit` 상승 에지를 잰다(정본 `saver_hook_events`).
    ///
    /// - `active_state` — **이번 메시지의** `panes_claude` 중 id == `active_pane` 항목의
    ///   `claude`(문자열 `"limit"`/`"busy"`/`"idle"` 또는 없음). 들고 있던 값을 넣지 않는다 —
    ///   정본은 메시지 하나만 본다.
    /// - `account` — 같은 메시지의 `claude_account`(별칭 · 없으면 빈 문자열).
    ///
    /// 직전 값의 시작은 「한도 아님」이다 — 그래서 **붙자마자 이미 한도면 발화한다**
    /// (탭 개수와 다르다: 정본 `prev.get("limit")` 이 처음엔 없다).
    pub fn saw_claude(&mut self, active_state: Option<&str>, account: &str) -> Option<HookFire> {
        let is_limit = active_state == Some("limit");
        let rising = is_limit && !self.prev_limit;
        self.prev_limit = is_limit;
        rising.then(|| HookFire {
            event: HookEvent::ClaudeLimit,
            env: vec![
                ("PYTMUX_HOOK_EVENT".to_owned(), "claude-limit".to_owned()),
                ("PYTMUX_ACCOUNT".to_owned(), account.to_owned()),
            ],
        })
    }

    /// 배치를 받았다. **처음일 때만** `client-attached` 다.
    pub fn saw_layout(&mut self) -> Option<HookEvent> {
        if self.attached {
            return None;
        }
        self.attached = true;
        Some(HookEvent::ClientAttached)
    }

    /// 개요를 받았다. 늘어난 탭·새로 켜진 벨을 **순서대로** 돌려준다(정본의 순서다).
    pub fn saw_status(&mut self, windows: usize, any_bell: bool) -> Vec<HookEvent> {
        let mut out = Vec::new();
        // 직전이 0이면 발화하지 않는다 — 처음 받은 목록은 "생긴" 것이 아니다.
        if self.prev_windows != 0 && windows > self.prev_windows {
            out.push(HookEvent::AfterNewWindow);
        }
        self.prev_windows = windows;
        if any_bell && !self.prev_bell {
            out.push(HookEvent::AlertBell);
        }
        self.prev_bell = any_bell;
        out
    }
}

/// 훅에 걸린 명령 한 줄을 **무엇으로 돌릴지** — 명령 줄 실행기의 결과 그대로다(pytmux-572).
///
/// 종전에는 이 모듈이 자기 해석기와 두 갈래(`Act`·`Answer`)를 들고 있었다. 명령 줄을 푸는
/// 자리가 입구마다 따로면 갈린다 — 이제 [`crate::cmdline::run_line`] 한 벌이고, 이 이름은
/// 그 결과의 옛 이름으로만 남는다.
pub use crate::cmdline::Run as HookRun;

/// 인자를 받는 명령의 이름 → 그 인자를 묻는 물음.
///
/// 별칭까지 정본과 맞춘다(`rename-window` 는 `rename-tab` 의 별칭이다 — 정본
/// `clientcmd` 의 `c in (…)` 묶음과 같은 표). 별칭이 빠지면 **파이썬 문서를 보고 적은
/// 훅이 우리 쪽에서만 안 먹는다.**
static ARG_COMMANDS: &[(&str, Prompt)] = &[
    ("rename-tab", Prompt::RenameTab),
    ("rename-window", Prompt::RenameTab),
    ("renamet", Prompt::RenameTab),
    ("renamew", Prompt::RenameTab),
    ("rename-pane", Prompt::RenamePane),
    ("move-tab", Prompt::MoveTab),
    ("swap-tab", Prompt::SwapTab),
    ("pipe-pane", Prompt::PipePane),
    ("join-pane", Prompt::JoinPane),
    ("display-message", Prompt::DisplayMessage),
    ("display", Prompt::DisplayMessage),
    ("displaym", Prompt::DisplayMessage),
    ("set", Prompt::SetOption),
    ("set-option", Prompt::SetOption),
    ("send-keys", Prompt::SendKeys),
    ("display-popup", Prompt::DisplayPopup),
    ("run-shell", Prompt::RunShell),
    ("run", Prompt::RunShell),
    ("if-shell", Prompt::IfShell),
    ("bind-key", Prompt::BindKey),
    ("bind", Prompt::BindKey),
    ("bindkey", Prompt::BindKey),
    ("unbind-key", Prompt::UnbindKey),
    ("unbind", Prompt::UnbindKey),
    ("layout-save", Prompt::SaveTabLayout),
    ("layout-load", Prompt::LoadTabLayout),
    ("layout-load-new", Prompt::LoadTabLayoutNew),
    ("remote-attach", Prompt::RemoteAttach),
    ("remote-new-tab", Prompt::RemoteNewTab),
    ("remote-detach", Prompt::RemoteDetach),
];

/// 이 명령이 **인자를 받나** — 받으면 그 인자를 묻는 물음(아니면 `None`).
///
/// 팔레트가 인자를 그 줄에서 받게 되면서(pytmux-7) 필요해졌다: 지금 고른 명령이 인자를
/// 받는지 알아야 *"무엇을 이어 치면 되는지"* 를 안내줄에 적을 수 있다. 표는 위
/// [`ARG_COMMANDS`] 한 벌이고, 여기가 그 표를 밖에서 물어보는 유일한 문이다.
pub fn arg_prompt(name: &str) -> Option<Prompt> {
    let lower = name.to_ascii_lowercase();
    ARG_COMMANDS
        .iter()
        .find(|(n, _)| *n == lower)
        .map(|(_, prompt)| *prompt)
}

/// 명령 한 줄을 돌릴 수 있는 것으로 옮긴다 — 사용자 별칭 없이 [`crate::cmdline::run_line`].
///
/// 별칭까지 풀어야 하는 자리(설정을 든 뷰)는 `run_line(line, &config.aliases)` 를 직접 부른다.
pub fn resolve(line: &str) -> Option<HookRun> {
    crate::cmdline::run_line(line, &crate::cmdline::Aliases::default())
}

#[cfg(test)]
#[path = "hooks_tests.rs"]
mod tests;
