//! 명령 줄의 **인자 자리 후보** — 정본 `PromptScreen._arg_candidates`·`_set_value_candidates`
//! (pytmux-621).
//!
//! 정본 `:` 줄은 이력이 있는 명령(`remote-attach` 등) 뒤 인자 자리에 오면 **최근에 친 인자**를
//! 후보로 띄우고(↑↓ 고르기 · Tab 채우기 · Enter 실행), `set <옵션> ` 뒤 값 자리에 오면 그 옵션의
//! **선택지**를 띄운다. GUI 팔레트는 인자를 노란 글로 받을 뿐 후보가 없었다.
//!
//! 표 둘(`COMMAND_ARGHIST`·`SET_OPTION_CHOICES`)과 규칙은 정본과 **글자까지** 같아야 한다 —
//! `proto/tests/prompt_args_conformance.rs` 가 정본 함수를 직접 부른 픽스처와 맞댄다.
//!
//! core 에는 IO 가 없다 — 이력의 주인(`proto::arghist` 의 파일)은 뷰이고, 여기는 이력을
//! 함수로 받는다.

/// 이력이 있는 명령 → 그 이력의 버킷(정본 `clientutil.COMMAND_ARGHIST`).
pub static COMMAND_ARGHIST: &[(&str, &str)] = &[
    ("remote-attach", "remote-host"),
    ("remote-new-tab", "remote-host"),
    ("remote-new-window", "remote-host"),
    ("remote-detach", "remote-host"),
    ("layout-save", "layout-name"),
    ("layout-load", "layout-name"),
    ("layout-load-new", "layout-name"),
    ("run-shell", "run-shell"),
    ("send-keys", "send-keys"),
];

/// `set <옵션>` 의 값 선택지(정본 `clientutil.SET_OPTION_CHOICES` — 차례가 뜻이다).
pub static SET_OPTION_CHOICES: &[(&str, &[&str])] = &[
    ("ambiguous-width", &["auto", "narrow", "wide"]),
    ("alt-scroll", &["on", "off"]),
    ("mouse", &["on", "off"]),
    ("mouse-drag-copy", &["on", "off", "shift"]),
    ("mouse-drag-threshold", &["1", "2", "3", "5", "8"]),
    ("copy-unwrap", &["on", "off"]),
    ("set-clipboard", &["on", "off"]),
    ("mouse-debug", &["on", "off"]),
    ("mode-keys", &["vi", "emacs"]),
    ("tab-bar", &["always", "auto"]),
    ("status-position", &["bottom", "top"]),
    ("remote-title", &["full", "host", "name"]),
    ("status", &["on", "off"]),
    ("set-titles", &["on", "off"]),
    ("strip-box-drawing", &["on", "off"]),
];

/// 후보 한 줄의 부류 — 줄 옆에 흐리게 적는 말(정본 `screen.arg_recent`·`screen.arg_choice`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArgKind {
    /// 예전에 친 인자.
    Recent,
    /// `set` 옵션의 선택지.
    Choice,
}

impl ArgKind {
    pub fn label(self) -> &'static str {
        crate::i18n::tc(
            "arg",
            match self {
                ArgKind::Recent => "이전 입력",
                ArgKind::Choice => "선택지",
            },
        )
    }
}

/// 인자 자리의 후보들. `cmd` 는 **사용자가 친 그대로의** 명령 토큰이다(줄을 다시 짤 때 —
/// 정본 `_arg_cmd`). 후보가 비어도 `Some` 이면 「인자 자리다」라는 뜻이다(그때 정본은 후보
/// 영역을 숨기고 명령 이름 후보도 안 띄운다).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgCands {
    pub cmd: String,
    pub items: Vec<(String, ArgKind)>,
}

/// 정본 `norm_sep` — 공백·`_` 를 `-` 로(비교는 소문자로 한 뒤에 한다).
fn norm_sep(s: &str) -> String {
    s.replace([' ', '_'], "-")
}

/// 첫 토큰 → 이력이 있는 정규 명령 이름(정본 `_arghist_canon` — 구분자·대소문자 무시).
pub fn arghist_canon(first: &str) -> Option<&'static str> {
    if first.is_empty() {
        return None;
    }
    let q = norm_sep(&first.to_lowercase());
    COMMAND_ARGHIST.iter().find(|(name, _)| norm_sep(name) == q).map(|(name, _)| *name)
}

/// 그 명령의 이력 버킷(없으면 `None`).
pub fn arghist_bucket(first: &str) -> Option<&'static str> {
    let canon = arghist_canon(first)?;
    COMMAND_ARGHIST.iter().find(|(n, _)| *n == canon).map(|(_, b)| *b)
}

/// 제출된 명령 줄에서 **이력에 남길 (버킷, 인자)** — 정본 `_record_arg`. 대상이 아니면 `None`.
pub fn record_of(line: &str) -> Option<(&'static str, String)> {
    let line = line.trim_start();
    let (first, rest) = line.split_once(char::is_whitespace)?;
    let bucket = arghist_bucket(first)?;
    let arg = rest.trim();
    (!arg.is_empty()).then(|| (bucket, arg.to_owned()))
}

/// `set <옵션> ` 값 자리의 후보(정본 `_set_value_candidates`). 값 자리가 아니면 `None`.
fn set_value_candidates(stripped: &str) -> Option<ArgCands> {
    let parts: Vec<&str> = stripped.split_whitespace().collect();
    if parts.len() < 2 || !matches!(parts[0].to_lowercase().as_str(), "set" | "set-option") {
        return None;
    }
    let key = norm_sep(&parts[1].to_lowercase());
    let choices = SET_OPTION_CHOICES.iter().find(|(k, _)| norm_sep(k) == key)?.1;
    // 옵션 이름 뒤에 공백(값 시작)이 있거나 부분 값이 있어야 값 자리다.
    if parts.len() == 2 && !stripped.ends_with([' ', '\t']) {
        return None;
    }
    let partial = parts.get(2).map(|p| p.to_lowercase()).unwrap_or_default();
    let mut matches: Vec<&str> = choices.iter().copied().filter(|c| c.starts_with(&partial)).collect();
    if matches.len() == 1 && matches[0] == partial {
        matches.clear();
    }
    Some(ArgCands {
        cmd: format!("{} {}", parts[0], parts[1]),
        items: matches.into_iter().map(|c| (c.to_owned(), ArgKind::Choice)).collect(),
    })
}

/// 인자 자리면 그 후보(정본 `_arg_candidates`). 아직 명령 이름을 치는 중이면 `None`.
///
/// `history(버킷)` 은 그 버킷의 최근-우선 이력이다(뷰의 `ArgHist::recent`).
pub fn arg_candidates(raw: &str, history: impl Fn(&str) -> Vec<String>) -> Option<ArgCands> {
    let stripped = raw.trim_start();
    if stripped.is_empty() {
        return None;
    }
    let first = stripped.split_whitespace().next()?;
    // 명령 토큰 뒤에 공백(또는 인자)이 있어야 인자 자리다.
    if stripped.len() <= first.len() {
        return None;
    }
    if let Some(found) = set_value_candidates(stripped) {
        return Some(found);
    }
    let bucket = arghist_bucket(first)?;
    let partial = stripped[first.len()..].trim().to_lowercase();
    let hist = history(bucket);
    let mut matches: Vec<String> = if partial.is_empty() {
        hist
    } else {
        hist.into_iter().filter(|a| a.to_lowercase().starts_with(&partial)).collect()
    };
    if matches.len() == 1 && matches[0].to_lowercase() == partial {
        matches.clear();
    }
    Some(ArgCands {
        cmd: first.to_owned(),
        items: matches.into_iter().map(|a| (a, ArgKind::Recent)).collect(),
    })
}

/// 친 줄에 **부분 인자**가 있나 — 정본 Enter 의 갈래(없으면 강조 후보로 실행, 있으면 친 그대로).
pub fn has_partial_arg(raw: &str) -> bool {
    let stripped = raw.trim_start();
    let first = stripped.split_whitespace().next().unwrap_or("");
    !stripped[first.len()..].trim().is_empty()
}
