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

use crate::keymap::{Action, PALETTE};
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
}

impl HookEvent {
    pub fn name(self) -> &'static str {
        match self {
            HookEvent::ClientAttached => "client-attached",
            HookEvent::AfterNewWindow => "after-new-window",
            HookEvent::AlertBell => "alert-bell",
        }
    }

    /// 우리가 **실제로 발화하는** 것들. 설정 화면·도움말이 이 목록을 보인다.
    pub const ALL: &'static [HookEvent] = &[
        HookEvent::ClientAttached,
        HookEvent::AfterNewWindow,
        HookEvent::AlertBell,
    ];
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
                "걸어 둔 훅이 없다 (set-hook <이벤트> <명령>)",
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
/// 정본과 같은 규칙: `-u` 가 **어디 있든** 그 다음 낱말이 지울 이벤트고, 그 밖에는
/// `-` 로 시작하는 낱말을 전부 버린 뒤 첫째가 이벤트·나머지가 명령이다. 명령은 낱말을
/// 다시 공백으로 이어 붙인다(그래서 `run-shell echo hi` 가 통째로 남는다).
pub fn parse_set_hook(args: &str) -> Option<SetHook> {
    let words: Vec<&str> = args.split_whitespace().collect();
    if let Some(at) = words.iter().position(|w| *w == "-u") {
        let event = words.get(at + 1)?;
        return Some(SetHook::Unset {
            event: (*event).to_owned(),
        });
    }
    let plain: Vec<&str> = words
        .into_iter()
        .filter(|w| !w.starts_with('-'))
        .collect();
    if plain.len() < 2 {
        return None;
    }
    Some(SetHook::Set {
        event: plain[0].to_owned(),
        command: plain[1..].join(" "),
    })
}

/// 가장자리를 재는 자. 상태가 아니라 **전이**를 본다(모듈 문서 참조).
#[derive(Debug, Clone, Default)]
pub struct HookWatcher {
    attached: bool,
    prev_windows: usize,
    prev_bell: bool,
}

impl HookWatcher {
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

/// 훅에 걸린 명령 한 줄을 **무엇으로 돌릴지**.
///
/// # 왜 두 갈래인가
///
/// 우리 클라에는 "명령 한 줄을 통째로 해석하는" 입구가 없다 — 팔레트는 이름을 골라
/// **액션**을 일으키고, 인자가 필요한 것은 물음을 띄운다. 훅은 사람이 없는 자리라
/// 물음을 띄울 수 없으므로, 인자가 있는 줄은 **물음을 건너뛰고 대답을 바로 넣는다.**
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HookRun {
    /// 인자 없는 것 — 팔레트에서 고른 것과 **같은 길**로 간다(확인 화면도 그대로 뜬다).
    Act(Action),
    /// 인자 있는 것 — 그 물음에 이 대답이 나온 것처럼 처리한다.
    Answer(Prompt, String),
}

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

/// 명령 한 줄을 돌릴 수 있는 것으로 옮긴다. 모르는 이름은 `None` — **조용히 넘긴다**
/// (정본도 그렇다. 훅이 도는 자리에는 오류를 볼 사람이 없다).
///
/// 순서가 규칙이다: **줄 전체가 팔레트 이름과 같은지 먼저** 본다. 팔레트에는
/// `split-window -h` 처럼 **플래그를 이름에 품은** 항목이 있어서, 첫 낱말만 떼면 그게
/// `split-window` + 인자 `-h` 로 잘못 갈린다.
pub fn resolve(line: &str) -> Option<HookRun> {
    let line = line.trim();
    if line.is_empty() {
        return None;
    }
    let whole = line.to_ascii_lowercase();
    if let Some(entry) = PALETTE
        .iter()
        .find(|e| e.name.to_ascii_lowercase() == whole)
    {
        return Some(HookRun::Act(entry.action));
    }
    // 자르는 자리는 **core 한 벌**이다(pytmux-7) — 팔레트가 거르는 자리와 같아야
    // `remote-attach host1` 이 목록에서는 걸리는데 여기서는 안 걸리는 일이 없다.
    let (name, arg) = crate::screens::split_first_space(line);
    let arg = arg.trim();
    let lower = name.to_ascii_lowercase();
    if let Some((_, prompt)) = ARG_COMMANDS.iter().find(|(n, _)| *n == lower) {
        // 인자가 비었으면 물음을 띄우는 편이 낫다 — `run-shell` 만 적어 둔 훅에
        // 빈 명령을 돌리면 아무 일도 안 하고 이유도 안 남는다.
        if !arg.is_empty() {
            return Some(HookRun::Answer(*prompt, arg.to_owned()));
        }
    }
    // 인자 없는 별칭·이름(예: `redraw`)은 팔레트가 이미 잡았다. 남은 것은 모르는 이름.
    PALETTE
        .iter()
        .find(|e| e.name.to_ascii_lowercase() == lower)
        .map(|e| HookRun::Act(e.action))
}

#[cfg(test)]
#[path = "hooks_tests.rs"]
mod tests;
