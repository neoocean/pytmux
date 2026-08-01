//! 키 입력 → **패널로 보낼 바이트**, 그리고 "이 키는 패널의 것인가 클라의 것인가".
//!
//! # 왜 이 계층인가
//!
//! 인코딩은 백엔드와 무관하다 — crossterm 의 `KeyEvent` 든 GUI 의 키 이벤트든, 결국
//! 셸이 기대하는 바이트열은 같다. 그래서 표는 여기 한 벌만 두고 뷰는 자기 이벤트를
//! [`Key`] 로 옮기기만 한다(그렇게 하면 두 뷰가 서로 다른 키를 보내는 일이 구조적으로
//! 불가능해진다 — 이 크레이트가 존재하는 이유와 같다).
//!
//! # 모드가 있어야 한다
//!
//! 터미널 클라는 **모든 키를 자식에게 줘야** 쓸모가 있다(`j` 가 탭 이동이면 vim 에서
//! j 를 못 누른다). 그래서 파이썬 클라와 **같은 모델**을 쓴다:
//!
//! - `Normal`: 거의 모든 키가 패널로 간다. `Esc` 만 삼켜 명령 모드로 들어간다.
//! - `Command`: [`BINDINGS`](crate::BINDINGS) 표대로 클라를 조작하고, 아무 키나 한 번
//!   처리하면 `Normal` 로 돌아온다.
//!
//! 같은 모델을 쓰는 이유는 사용자가 두 클라를 번갈아 쓴다는 것이다 — 손버릇이 갈리면
//! 그게 곧 결함으로 느껴진다.
//!
//! # ESC 는 두 가지 뜻이다
//!
//! 자식 프로그램(vim·Claude)도 ESC 를 필요로 한다. 그래서 `Command` 모드에서 ESC 를
//! 다시 누르면 **ESC 한 바이트를 패널로 보낸다** — "모드 진입"과 "ESC 보내기"를 한
//! 키로 가르는 방법이 이것뿐이다(터미널은 키 뗌 이벤트가 없다).

/// 백엔드 중립 키. 뷰가 자기 이벤트를 이것으로 옮긴다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Key {
    Char(char),
    Enter,
    Tab,
    BackTab,
    Backspace,
    Delete,
    Escape,
    /// `Shift+ESC`. **모드에 안 들어가고** 패널에 ESC 를 준다(파이썬 `e_sesc`).
    ///
    /// 별도 키인 이유: 이 크레이트의 [`Mods`] 에는 shift 가 없다(대문자·`BackTab` 처럼
    /// **키 쪽에 접어** 둔다). `Escape` 는 접을 대문자가 없어 여기서 이름을 가른다 —
    /// `BackTab` 과 같은 자리다.
    ShiftEscape,
    Up,
    Down,
    Left,
    Right,
    /// `Shift+←` / `Shift+→`. [`ShiftEscape`](Key::ShiftEscape) 와 같은 이유로 키 쪽에
    /// 접었다 — 탭바 포커스에서 **탭을 옮기는** 키라(파이썬 `e_tb`) 방향키와 갈라야 한다.
    ///
    /// 패널로 보낼 때는 수정자 없는 화살표와 **같은 바이트**를 쓴다([`encode`]). 수정자를
    /// 실은 CSI(`ESC [ 1 ; 2 D`)를 보낼지는 이 표와 무관한 별개 물음이고, 여기서 바꾸면
    /// 지금 잘 도는 앱의 Shift+화살표 동작이 조용히 달라진다.
    ShiftLeft,
    ShiftRight,
    /// `Shift+↑ ↓ Home End` · `Shift+Enter`. 위 둘과 **같은 이유로** 키 쪽에 접었다 —
    /// 작성창(`crate::editor`)에서 **선택을 늘리는** 키라 수정자 없는 것과 갈라야 한다.
    ///
    /// 표([`crate::BINDINGS`])를 찾을 때는 이름이 없어 `None` 이다. 그것이 파이썬과
    /// 같은 자리다 — 파이썬 esc 모드도 `shift+up` 을 `up` 으로 안 읽는다(Textual 이
    /// 다른 이름으로 준다). 패널로 보낼 때의 바이트는 수정자 없는 것과 **같다**
    /// ([`encode`]) — 지금 도는 앱의 손버릇을 바꾸지 않으려는 것이다.
    ShiftUp,
    ShiftDown,
    ShiftHome,
    ShiftEnd,
    /// `Shift+Enter` — 작성창의 **줄바꿈**이다(`Enter` 는 전송).
    ///
    /// 단말이 이 조합을 LF 로 보내면 `Ctrl+J` 로 도착하므로 작성창은 **둘 다** 받는다
    /// (파이썬 `_ComposeTextArea` 와 같다).
    ShiftEnter,
    Home,
    End,
    PageUp,
    PageDown,
    Insert,
    /// `Shift+Delete`. Insert 키가 없는 맥 자판에서 작성창을 여는 **동형 별칭**이다
    /// (파이썬 `e_ins` 가 `insert`·`shift+delete` 둘 다 받는 것과 같다).
    ShiftDelete,
    /// F1~F12.
    Function(u8),
}

/// 눌린 수정키. `shift` 는 글자에 이미 반영돼 오므로 담지 않는다.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Mods {
    pub ctrl: bool,
    pub alt: bool,
}

impl Mods {
    pub const NONE: Mods = Mods {
        ctrl: false,
        alt: false,
    };

    pub const CTRL: Mods = Mods {
        ctrl: true,
        alt: false,
    };

    pub const ALT: Mods = Mods {
        ctrl: false,
        alt: true,
    };
}

/// 클라의 입력 모드.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum InputMode {
    /// 키가 패널(자식 셸)로 간다.
    #[default]
    Normal,
    /// 키가 클라 명령으로 해석된다.
    Command,
    /// 키가 스크롤백 이동으로 해석된다([`SCROLL_BINDINGS`]).
    Scroll,
    /// prefix 키(`Ctrl+B`)를 누른 직후 — **다음 키 하나**가 pytmux 의 것이다.
    ///
    /// tmux·파이썬 클라와 같은 모델이다. `Command`(ESC) 모드와 나란히 두는 이유는 둘이
    /// 서로 다른 어휘이기 때문이다 — prefix 는 tmux 관습(`%`·`c`·`&`), esc 는 이 클라의
    /// 것(방향키·번호). 파이썬 클라도 둘을 함께 갖는다.
    Prefix,
}

impl InputMode {
    /// 탭바 앞에 붙는 배지. 평소 모드는 배지가 없다 — 크롬을 조용히 두려는 것이다.
    ///
    /// 이름을 뷰가 아니라 여기서 정하는 이유는 키 표와 같다: 두 뷰가 각자 이름을 지으면
    /// 같은 모드가 화면마다 달라 보인다.
    pub fn badge(self) -> Option<&'static str> {
        match self {
            InputMode::Normal => None,
            InputMode::Command => Some("[esc]"),
            InputMode::Scroll => Some("[scroll]"),
            InputMode::Prefix => Some("[prefix]"),
        }
    }
}

/// 스크롤백을 얼마나 움직일 것인가.
///
/// 줄 수가 아니라 **뜻**이다 — 반 페이지는 캔버스 높이를 알아야 줄 수가 되는데, 그 높이를
/// 아는 것은 뷰다(core 는 UI 를 모른다는 계약). 부호는 서버 `Pane.scroll_by` 와 같게
/// **과거 방향이 +** 다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScrollAmount {
    Lines(i32),
    HalfPageUp,
    HalfPageDown,
    /// 스크롤백 맨 위.
    Top,
    /// 라이브(맨 아래)로 복귀.
    Bottom,
}

/// 키 하나를 어떻게 처리할지.
#[derive(Debug, Clone, PartialEq)]
pub enum KeyOutcome {
    /// 이 바이트열을 패널로 보낸다.
    ToPane(Vec<u8>),
    /// 클라 액션을 실행한다.
    Action(crate::Action),
    /// 모드만 바뀐다(보낼 것도, 실행할 것도 없다).
    ModeChanged(InputMode),
    /// 스크롤백을 움직인다.
    ///
    /// `leave` 면 처리한 뒤 평소 모드로 돌아온다 — 파이썬 클라에서 `q`·`Esc`·`Enter` 는
    /// **라이브 하단 복귀와 모드 탈출이 같은 동작**이다. 둘을 따로 두면 "모드는 나갔는데
    /// 화면은 과거에 멈춰 있는" 상태가 만들어진다.
    Scroll { amount: ScrollAmount, leave: bool },
    /// 아무 뜻도 없는 키 — 조용히 버린다(자식에게 쓰레기를 보내지 않는다).
    Ignored,
}

/// 하네스·스크립트가 쓰는 **키 토큰** 하나를 읽는다(`--frame-keys` 등).
///
/// 낱글자는 그 글자, `esc`·`enter`·`tab`·`up`·`down`·`left`·`right`·`space` 는
/// 그 키, `ctrl-<글자>` 는 조합이다. 모르는 토큰은 `None` — 표기가 이 크레이트에
/// 있는 이유는 계층 게이트의 규칙 그대로다(키 정의는 core 한 곳).
pub fn parse_token(token: &str) -> Option<(Key, Mods)> {
    let token = token.trim();
    Some(match token {
        "esc" => (Key::Escape, Mods::NONE),
        "enter" => (Key::Enter, Mods::NONE),
        "tab" => (Key::Tab, Mods::NONE),
        "up" => (Key::Up, Mods::NONE),
        "down" => (Key::Down, Mods::NONE),
        "left" => (Key::Left, Mods::NONE),
        "right" => (Key::Right, Mods::NONE),
        "space" => (Key::Char(' '), Mods::NONE),
        // 작성창(`esc Insert`)을 연출하려면 이 키가 토큰으로 있어야 한다 — 사용자
        // 가이드 스크린샷이 이 화면만 못 찍는 구멍이 있었다(2026-07-30).
        "insert" => (Key::Insert, Mods::NONE),
        _ => {
            if let Some(c) = token.strip_prefix("ctrl-").and_then(|r| r.chars().next()) {
                (Key::Char(c), Mods::CTRL)
            } else if token.chars().count() == 1 {
                (Key::Char(token.chars().next()?), Mods::NONE)
            } else {
                return None;
            }
        }
    })
}

/// 모드에 따라 키를 해석한다. 모드 전이는 호출부가 [`KeyOutcome::ModeChanged`] 로 받는다.
///
/// `Normal` 에서 ESC 는 **삼킨다**(모드 진입). `Command` 에서 ESC 는 반대로 **패널로
/// 보낸다** — 그래야 자식 프로그램이 ESC 를 받을 길이 남는다(모듈 문서 참고).
pub fn interpret(mode: InputMode, key: Key, mods: Mods) -> KeyOutcome {
    interpret_with(mode, key, mods, (Key::Char('b'), Mods::CTRL))
}

/// prefix 키를 지정하는 판(설정이 있는 자리에서 쓴다 — 패리티 G5).
pub fn interpret_with(
    mode: InputMode,
    key: Key,
    mods: Mods,
    prefix: (Key, Mods),
) -> KeyOutcome {
    interpret_full(mode, key, mods, prefix, "")
}

/// `mode-keys` 까지 아는 판(패리티 G8l). 뷰가 설정을 들고 있으므로 여기서 받는다.
pub fn interpret_full(
    mode: InputMode,
    key: Key,
    mods: Mods,
    prefix: (Key, Mods),
    mode_keys: &str,
) -> KeyOutcome {
    let is_prefix = (key, mods) == prefix;
    match mode {
        InputMode::Normal => {
            // ★ `Shift+ESC` 는 **모드에 안 들어가고** 패널에 ESC 를 준다(파이썬 `e_sesc`).
            // 이게 없으면 vim 안에서 ESC 를 칠 때마다 모드에 들어갔다 나와야 한다.
            if key == Key::ShiftEscape {
                return KeyOutcome::ToPane(vec![0x1b]);
            }
            if key == Key::Escape && mods == Mods::NONE {
                return KeyOutcome::ModeChanged(InputMode::Command);
            }
            if is_prefix {
                return KeyOutcome::ModeChanged(InputMode::Prefix);
            }
            match encode(key, mods) {
                Some(bytes) => KeyOutcome::ToPane(bytes),
                None => KeyOutcome::Ignored,
            }
        }
        InputMode::Command => {
            if key == Key::Escape {
                // 두 번째 ESC = 자식에게 ESC 를 보낸다(+ 모드 복귀는 호출부가 한다).
                return KeyOutcome::ToPane(vec![0x1b]);
            }
            // 번호 키는 표가 아니라 규칙이다(prefix 와 같다) — 열 줄을 적는 대신 여기서
            // 판정한다. 파이썬 esc 모드의 `1–9` 와 같은 자리다.
            if let Key::Char(c) = key
                && mods == Mods::NONE
                && c.is_ascii_digit()
                && let Some(action) = crate::keymap::prefix_number(c)
            {
                return KeyOutcome::Action(action);
            }
            match command_action(key, mods) {
                // 스크롤 모드 진입은 서버와 무관한 **모드 전이**다. 액션으로 흘려보내면
                // 뷰마다 다르게 해석할 여지가 생긴다(액션은 서버 명령으로 옮겨지는 것이
                // 기본 경로다).
                Some(crate::Action::EnterScroll) => KeyOutcome::ModeChanged(InputMode::Scroll),
                Some(action) => KeyOutcome::Action(action),
                None => KeyOutcome::Ignored,
            }
        }
        InputMode::Scroll => {
            // ★ 프롬프트 점프는 **스크롤 모드 안에서도** 같은 키다(파이썬
            // `_handle_scroll_key` 가 vi/emacs 의 `k`·`ctrl+p` 보다 먼저 본다). 뛴 자리
            // 주변을 읽다가 다시 뛰는 것이 이 키의 쓰임이라, 한 번 뛰고 모드를 나가면
            // 반쪽이다.
            if let Some(jump) = jump_action(key, mods) {
                return KeyOutcome::Action(jump);
            }
            // 검색(파이썬 `_handle_scroll_key` 의 `/`·`n`·`N`). 검색은 서버가 한다 —
            // 여기는 물음/반복 액션만 낸다. `n`/`N` 은 vi 의 `j`/`k` 와 안 겹치고,
            // emacs 의 `Ctrl+N`(줄 스크롤)과는 수정키가 다르다.
            if mods == Mods::NONE {
                match key {
                    Key::Char('/') => {
                        return KeyOutcome::Action(crate::Action::SearchScrollback);
                    }
                    Key::Char('n') => {
                        return KeyOutcome::Action(crate::Action::SearchAgain { down: false });
                    }
                    Key::Char('N') => {
                        return KeyOutcome::Action(crate::Action::SearchAgain { down: true });
                    }
                    _ => {}
                }
            }
            match scroll_action_in(mode_keys, key, mods) {
                Some((amount, leave)) => KeyOutcome::Scroll { amount, leave },
                None => KeyOutcome::Ignored,
            }
        }
        InputMode::Prefix => {
            // prefix 를 두 번 누르면 **그 바이트가 패널로 간다**(tmux 와 같다). 이게
            // 없으면 패널 안 프로그램이 Ctrl+B 를 영영 못 받는다 — 그 키를 쓰는 앱이
            // (emacs·less 의 뒤로 이동) 통째로 막힌다.
            if is_prefix {
                return match encode(key, mods) {
                    Some(bytes) => KeyOutcome::ToPane(bytes),
                    None => KeyOutcome::Ignored,
                };
            }
            match prefix_action(key, mods) {
                Some(crate::Action::EnterScroll) => KeyOutcome::ModeChanged(InputMode::Scroll),
                Some(action) => KeyOutcome::Action(action),
                // 표에 없는 키는 **조용히 버린다**. 모드는 호출부가 푼다(tmux 와 같다 —
                // 잘못 누른 prefix 뒤의 키가 패널로 새면 그게 더 놀랍다).
                None => KeyOutcome::Ignored,
            }
        }
    }
}

/// 이 키가 프롬프트 점프(`Ctrl+↑`/`Ctrl+↓`)인가 — 그렇다면 그 액션.
///
/// 표([`BINDINGS`](crate::BINDINGS))를 다시 보는 이유: 키를 여기 적으면 esc 모드와
/// 스크롤 모드가 서로 다른 키를 알아듣기 시작한다. 표가 정본이고 여기서는 **그 표에서
/// 찾은 것이 점프인지만** 가른다.
fn jump_action(key: Key, mods: Mods) -> Option<crate::Action> {
    match command_action(key, mods) {
        Some(action @ crate::Action::JumpPrompt { .. }) => Some(action),
        _ => None,
    }
}

/// 이 키가 **기본** prefix(`Ctrl+B`)인가.
///
/// 설정으로 바꾼 prefix 는 [`ModeState`] 가 들고 있다(G5) — 이 함수는 설정을 못 읽는
/// 자리(순수 함수 [`interpret`])의 기본값이다.
pub fn is_prefix(key: Key, mods: Mods) -> bool {
    key == Key::Char('b') && mods == Mods::CTRL
}

/// prefix 모드에서 이 키에 걸린 액션([`PREFIX_BINDINGS`](crate::PREFIX_BINDINGS) 이 정본).
pub fn prefix_action(key: Key, mods: Mods) -> Option<crate::Action> {
    // Alt 는 이 표에 없다 — 있으면 `alt-` 접두를 붙여 적어야 한다.
    if mods.alt {
        return None;
    }
    // 번호는 표가 아니라 규칙이다(0~9 를 열 줄 적지 않는다).
    if !mods.ctrl
        && let Key::Char(c) = key
        && c.is_ascii_digit()
    {
        return crate::keymap::prefix_number(c);
    }
    // `Ctrl+o`(패널 회전)처럼 수정키가 붙은 키가 있어서 이름에 접두를 붙인다. 표기는
    // warpui `Keystroke` 문법 그대로다 — 표를 읽는 셋이 같은 문법이어야 한다.
    let name = match binding_name(key) {
        Some(name) if mods.ctrl => format!("ctrl-{name}"),
        Some(name) => name,
        None => return None,
    };
    crate::PREFIX_BINDINGS
        .iter()
        .find(|b| b.key == name)
        .map(|b| b.action)
}

/// 모드를 들고 키를 받는 작은 상태기계.
///
/// # 왜 뷰가 아니라 여기인가
///
/// [`interpret`] 은 순수 함수라 **모드 전이를 실행하지 않는다** — 그건 호출부가 한다.
/// 그런데 그 전이 규칙(아래 셋)이야말로 두 클라의 손버릇을 정하는 것이고, 뷰마다 적으면
/// 조용히 갈린다: 한쪽에서만 명령 하나 뒤에 모드가 안 풀리면 그 클라는 "가끔 키가 안
/// 먹는" 것처럼 느껴진다. 원인이 모드라는 단서는 화면에 배지 하나뿐이다.
///
/// 규칙 셋:
/// 1. 패널로 보냈으면 평소 모드로 돌아온다(명령 모드의 두 번째 ESC 가 그 경우다).
/// 2. 액션 하나를 처리했으면 평소 모드로 — **모드는 한 동작만 붙잡는다**(파이썬 클라
///    관습). 연속 조작은 `esc` 를 다시 누른다.
/// 3. 스크롤은 `leave` 일 때만 빠져나온다. 라이브 복귀와 모드 탈출은 한 동작이라야
///    "모드는 나갔는데 화면은 과거에 멈춘" 상태가 안 생긴다.
#[derive(Debug, Clone, Copy)]
pub struct ModeState {
    mode: InputMode,
    /// prefix 키. 설정(`set prefix C-a`)이 있으면 그 값이다(패리티 G5).
    prefix: (Key, Mods),
}

impl Default for ModeState {
    fn default() -> Self {
        Self {
            mode: InputMode::default(),
            prefix: (Key::Char('b'), Mods::CTRL),
        }
    }
}

impl ModeState {
    /// 설정에서 읽은 prefix 로 시작한다.
    pub fn with_prefix(prefix: (Key, Mods)) -> Self {
        Self {
            mode: InputMode::default(),
            prefix,
        }
    }

    /// 지금 prefix 키(도움말·상태 표시에 쓴다).
    pub fn prefix(self) -> (Key, Mods) {
        self.prefix
    }

    /// prefix 를 바꾼다(설정 화면). **다시 띄우지 않고 이번 판에 바로 먹는다** — 설정을
    /// 바꾸고 재시작해야 한다면 그 화면은 반쪽이다.
    pub fn set_prefix(&mut self, prefix: (Key, Mods)) {
        self.prefix = prefix;
    }

    pub fn mode(self) -> InputMode {
        self.mode
    }

    /// 평소 모드로 되돌린다.
    ///
    /// 키가 아닌 입력(붙여넣기)이 들어오면 그 자체가 "패널에 뭔가를 넣는 중"이라는 뜻이라
    /// 명령 모드를 붙잡고 있을 이유가 없다. 안 풀면 붙여넣기 직후의 타이핑이 통째로
    /// 명령으로 먹힌다.
    /// 스크롤 모드로 들어간다.
    ///
    /// 키가 아닌 입구(메뉴의 search)가 검색을 열 때 쓴다 — 검색 결과는 라이브 하단
    /// 밖이라, 평소 모드로 두면 방향키가 패널로 가 맞은 줄 주변을 못 훑는다(파이썬
    /// `_prompt_done` 의 `mode = "scroll"` 과 같은 자리다. 키로 연 경우는
    /// [`press_in`](Self::press_in) 이 이미 같은 규칙을 적용한다).
    pub fn enter_scroll(&mut self) {
        self.mode = InputMode::Scroll;
    }

    pub fn reset(&mut self) {
        self.mode = InputMode::Normal;
    }

    /// 키 하나를 해석하고 **모드 전이까지 마친다**. 남은 일(바이트 전송·액션 실행·
    /// 스크롤 프레임 만들기)은 돌려받은 값으로 뷰가 한다.
    pub fn press(&mut self, key: Key, mods: Mods) -> KeyOutcome {
        self.press_in("", key, mods)
    }

    /// `mode-keys` 설정을 아는 판(패리티 G8l).
    pub fn press_in(&mut self, mode_keys: &str, key: Key, mods: Mods) -> KeyOutcome {
        let outcome = interpret_full(self.mode, key, mods, self.prefix, mode_keys);
        match &outcome {
            KeyOutcome::ToPane(_) => self.mode = InputMode::Normal,
            KeyOutcome::ModeChanged(next) => self.mode = *next,
            // ★ 프롬프트 점프만은 평소 모드로 안 돌아간다 — **스크롤 모드로 들어간다**
            // (파이썬 clientio 도 `_exit_esc()` 뒤 `self.mode = "scroll"` 이다).
            // 뛰면 뷰가 라이브 하단을 벗어나므로, 평소 모드로 돌아오면 방향키가 패널로
            // 가 버려 뛴 자리 주변을 읽을 길이 없다. 규칙을 여기 두는 이유는 나머지
            // 셋과 같다 — 뷰가 각자 적으면 한쪽만 모드가 안 바뀐다.
            KeyOutcome::Action(crate::Action::JumpPrompt { .. }) => {
                self.mode = InputMode::Scroll
            }
            // 검색도 같은 부류다 — 훑는 중이라 부른 것이고, 맞은 줄 주변을 계속
            // 훑어야 한다(파이썬 `_prompt_done` 도 `mode = "scroll"` 로 되돌린다).
            KeyOutcome::Action(
                crate::Action::SearchScrollback | crate::Action::SearchAgain { .. },
            ) => self.mode = InputMode::Scroll,
            KeyOutcome::Action(_) => self.mode = InputMode::Normal,
            KeyOutcome::Scroll { leave, .. } => {
                if *leave {
                    self.mode = InputMode::Normal;
                }
            }
            // prefix 모드에서 모르는 키는 **모드를 푼다**(tmux 와 같다). 안 풀면 잘못
            // 누른 prefix 뒤의 타이핑이 통째로 표에 부딪혀 사라지고, 사용자에게는
            // "키가 안 먹는다"로만 보인다.
            KeyOutcome::Ignored => {
                if self.mode == InputMode::Prefix {
                    self.mode = InputMode::Normal;
                }
            }
        }
        outcome
    }
}

/// 스크롤 모드에서 이 키가 하는 일([`SCROLL_BINDINGS`] 표가 정본).
pub fn scroll_action(key: Key, mods: Mods) -> Option<(ScrollAmount, bool)> {
    scroll_action_in("", key, mods)
}

/// `mode-keys` 설정을 아는 판(패리티 G8l).
///
/// 표 밖의 키를 **모드에 따라** 더 건다: `vi` 면 `j`/`k`/`Ctrl+U`/`Ctrl+D`, `emacs` 면
/// `Ctrl+N`/`Ctrl+P`/`Ctrl+V`/`Alt+V`. 파이썬 `_handle_scroll_key` 와 같은 짝이다.
///
/// **설정을 안 읽던 동안 이 키들을 안 걸어 둔 이유**가 이것이다: 무조건 걸면 vi 모드를
/// 안 쓰는 사용자에게 없던 키가 생긴다(`j` 가 아래로 흐른다).
pub fn scroll_action_in(mode_keys: &str, key: Key, mods: Mods) -> Option<(ScrollAmount, bool)> {
    let half_up = || Some((ScrollAmount::HalfPageUp, false));
    let half_down = || Some((ScrollAmount::HalfPageDown, false));
    match (mode_keys, key, mods) {
        ("vi", Key::Char('k'), Mods::NONE) => return Some((ScrollAmount::Lines(1), false)),
        ("vi", Key::Char('j'), Mods::NONE) => return Some((ScrollAmount::Lines(-1), false)),
        ("vi", Key::Char('u'), Mods::CTRL) => return half_up(),
        ("vi", Key::Char('d'), Mods::CTRL) => return half_down(),
        ("emacs", Key::Char('p'), Mods::CTRL) => return Some((ScrollAmount::Lines(1), false)),
        ("emacs", Key::Char('n'), Mods::CTRL) => return Some((ScrollAmount::Lines(-1), false)),
        ("emacs", Key::Char('v'), Mods::ALT) => return half_up(),
        ("emacs", Key::Char('v'), Mods::CTRL) => return half_down(),
        _ => {}
    }
    if mods != Mods::NONE {
        return None;
    }
    let name = binding_name(key)?;
    SCROLL_BINDINGS
        .iter()
        .find(|b| b.key == name)
        .map(|b| (b.amount, b.leave))
}

/// 스크롤 모드의 키 하나.
#[derive(Debug, Clone, Copy)]
pub struct ScrollBinding {
    /// [`BINDINGS`](crate::BINDINGS) 와 같은 표기의 키 이름.
    pub key: &'static str,
    pub amount: ScrollAmount,
    /// 처리 뒤 평소 모드로 돌아오는가.
    pub leave: bool,
}

const fn s(key: &'static str, amount: ScrollAmount, leave: bool) -> ScrollBinding {
    ScrollBinding { key, amount, leave }
}

/// 스크롤 모드 키 표.
///
/// 파이썬 클라(`clientio._handle_scroll_key`)와 **같은 키**를 건다 — 사용자가 두 클라를
/// 번갈아 쓰므로 손버릇이 갈리면 그게 곧 결함이다. 그쪽의 `j`/`k`·`ctrl+u` 류는
/// **설정(`mode-keys` = vi/emacs)에 딸린 것**이라 여기 넣지 않았다. 설정을 안 읽는 클라가
/// 그 키를 무조건 걸면, vi 모드를 안 쓰는 사용자에게는 없던 키가 생긴다.
pub static SCROLL_BINDINGS: &[ScrollBinding] = &[
    s("up", ScrollAmount::Lines(1), false),
    s("down", ScrollAmount::Lines(-1), false),
    s("pageup", ScrollAmount::HalfPageUp, false),
    s("pagedown", ScrollAmount::HalfPageDown, false),
    s("g", ScrollAmount::Top, false),
    // 맨 아래로 가되 **모드는 유지**한다(파이썬도 같다) — 계속 훑을 수 있어야 한다.
    s("shift-G", ScrollAmount::Bottom, false),
    s("end", ScrollAmount::Bottom, false),
    // 라이브 복귀 = 모드 탈출. 셋 다 파이썬 클라와 같은 키다.
    s("q", ScrollAmount::Bottom, true),
    s("escape", ScrollAmount::Bottom, true),
    s("enter", ScrollAmount::Bottom, true),
];

/// 키를 키 표 표기의 이름으로. 표를 찾는 쪽이 둘(명령·스크롤)이라 여기 하나만 둔다.
/// 수정키까지 붙인 키 이름(`ctrl-o`·`shift-G`). 사용자 바인딩(`bind`)이 쓴다.
///
/// [`binding_name`] 이 수정키를 안 보는 이유는 표(`BINDINGS`)의 문법이 `ctrl-` 접두를
/// 이름에 이미 담고 있어서다 — 여기서는 **눌린 키**에서 그 이름을 만든다.
pub fn binding_name_with(key: Key, mods: Mods) -> Option<String> {
    let base = binding_name(key)?;
    Some(match (mods.ctrl, mods.alt) {
        (true, true) => format!("ctrl-alt-{base}"),
        (true, false) => format!("ctrl-{base}"),
        (false, true) => format!("alt-{base}"),
        (false, false) => base,
    })
}

fn binding_name(key: Key) -> Option<String> {
    Some(match key {
        Key::Char(' ') => "space".to_owned(),
        // 대문자는 **`shift-` 를 붙여** 적는다. 이 표를 읽는 곳이 셋이고(여기 · TUI 의
        // 이벤트 핸들러 · GUI 의 키맵 등록) 셋 다 같은 문법이어야 한다 — GUI 쪽
        // `Keystroke::parse` 가 대문자 단독을 거부한다(2026-07-28, GUI 를 처음 띄우며
        // 드러났다: 그 전까지는 GUI 가 P1 에 멈춰 있어 아무도 이 표를 등록해 본 적이
        // 없었다). 아래 왕복 테스트가 세 소비자의 문법을 하나로 못박는다.
        Key::Char(c) if c.is_uppercase() => format!("shift-{c}"),
        Key::Char(c) => c.to_string(),
        Key::Enter => "enter".to_owned(),
        Key::Escape => "escape".to_owned(),
        Key::ShiftEscape => "shift-escape".to_owned(),
        Key::Up => "up".to_owned(),
        Key::Down => "down".to_owned(),
        // 좌우 화살표는 prefix 의 패널 이동이 쓴다(패리티 G1b). 여기 없으면 그 키가
        // 표에서 안 찾아지고, 증상은 "화살표만 안 먹는다"다.
        Key::Left => "left".to_owned(),
        Key::Right => "right".to_owned(),
        // 탭 스위처(`esc Tab`)가 쓴다. 역방향은 `shift-tab` — crossterm 이 BackTab 을
        // 따로 주므로 여기서 이름을 갈라 둔다(문법은 warpui `Keystroke` 그대로).
        Key::Tab => "tab".to_owned(),
        Key::BackTab => "shift-tab".to_owned(),
        Key::PageUp => "pageup".to_owned(),
        Key::PageDown => "pagedown".to_owned(),
        Key::End => "end".to_owned(),
        // 작성창을 여는 두 키(`e_ins`). `shift-delete` 는 Insert 키가 없는 맥 자판용
        // 별칭이라, 이름도 **표기 그대로** 접두를 붙여 적는다.
        // Home 은 우리 표에는 없지만 **오버레이가 스펙으로 가져간다**(달력의 '오늘로').
        // 이름이 없으면 서버가 준 `keys` 표에서 영영 안 찾아진다.
        Key::Home => "home".to_owned(),
        Key::Insert => "insert".to_owned(),
        Key::ShiftDelete => "shift-delete".to_owned(),
        _ => return None,
    })
}

/// `Command` 모드에서 이 키에 걸린 액션([`BINDINGS`](crate::BINDINGS) 표가 정본).
///
/// 표를 여기서 다시 적지 않는다 — 키 이름을 두 곳에 적으면 갈라진다(계층 게이트가
/// 뷰에 키 이름을 직접 적는 것을 막는 것과 같은 이유).
pub fn command_action(key: Key, mods: Mods) -> Option<crate::Action> {
    // 수정키가 붙은 키도 **이름으로 찾는다**(`ctrl-up`). 종전에는 여기서 통째로
    // 버렸는데, 그러면 표에 `ctrl-up` 을 적어도 영영 안 찾아진다 — `e_jump` 를 걸면서
    // 드러났다. 표에 없는 조합은 그대로 `None` 이라 지금 동작은 안 바뀐다.
    let name = binding_name_with(key, mods)?;
    crate::BINDINGS
        .iter()
        .find(|b| b.key == name)
        .map(|b| b.action)
}

/// 백엔드가 준 **키 이름**(warpui 표기: `enter`·`pageup`·`f5`·`a` …)을 중립 키로 옮긴다.
///
/// 표가 여기 있는 이유: 키 이름은 이 크레이트의 어휘다([`BINDINGS`](crate::BINDINGS) 가
/// 같은 표기를 쓴다). 뷰가 이름을 직접 적으면 두 뷰가 서로 다른 이름을 알아듣기 시작하고,
/// 계층 게이트(`scripts/check_layering.sh`)도 정확히 그걸 막는다.
///
/// `shift` 는 이름에 이미 반영돼 오지만(대문자) `tab` 만은 예외다 — crossterm 이 BackTab
/// 도 `tab` 으로 접기 때문에 여기서 가른다. `fallback_char` 는 이름을 모를 때 볼 실제
/// 입력 글자(IME·조합 문자)다.
pub fn from_name(
    name: &str,
    shift: bool,
    fallback_char: Option<char>,
) -> Option<Key> {
    let key = match name {
        "enter" if shift => Key::ShiftEnter,
        "enter" => Key::Enter,
        "tab" if shift => Key::BackTab,
        "escape" if shift => Key::ShiftEscape,
        "left" if shift => Key::ShiftLeft,
        "right" if shift => Key::ShiftRight,
        "up" if shift => Key::ShiftUp,
        "down" if shift => Key::ShiftDown,
        "home" if shift => Key::ShiftHome,
        "end" if shift => Key::ShiftEnd,
        "delete" if shift => Key::ShiftDelete,
        "tab" => Key::Tab,
        "backspace" => Key::Backspace,
        "delete" => Key::Delete,
        "escape" => Key::Escape,
        "up" => Key::Up,
        "down" => Key::Down,
        "left" => Key::Left,
        "right" => Key::Right,
        "home" => Key::Home,
        "end" => Key::End,
        "pageup" => Key::PageUp,
        "pagedown" => Key::PageDown,
        "insert" => Key::Insert,
        " " | "space" => Key::Char(' '),
        _ => {
            if let Some(rest) = name.strip_prefix('f') {
                if let Ok(number) = rest.parse::<u8>() {
                    return Some(Key::Function(number));
                }
            }
            let mut chars = name.chars();
            match (chars.next(), chars.next()) {
                (Some(c), None) => Key::Char(c),
                _ => Key::Char(fallback_char?),
            }
        }
    };
    Some(key)
}

/// 키 → 패널로 보낼 바이트. 뜻이 없으면 `None`.
///
/// 화살표·Home/End 는 **CSI 형식**(`ESC [ A` …)으로 보낸다. 애플리케이션 커서 키 모드
/// (`ESC O A`)를 쓰는 프로그램도 있지만, 그 모드는 자식이 DECCKM 으로 선언하는 것이고
/// 우리는 그 상태를 모른다 — 서버가 그리는 화면만 받기 때문이다. CSI 형식은 두 모드
/// 모두에서 통용되므로 이쪽을 고른다(파이썬 클라도 같다).
pub fn encode(key: Key, mods: Mods) -> Option<Vec<u8>> {
    let base: Vec<u8> = match key {
        Key::Char(c) => {
            if mods.ctrl {
                // Ctrl+A..Z → 0x01..0x1a. 그 밖의 Ctrl 조합은 뜻이 정해져 있지 않다.
                let upper = c.to_ascii_uppercase();
                if upper.is_ascii_uppercase() {
                    vec![(upper as u8) - b'A' + 1]
                } else {
                    match c {
                        ' ' => vec![0],         // Ctrl+Space = NUL(관례)
                        '[' => vec![0x1b],
                        '\\' => vec![0x1c],
                        ']' => vec![0x1d],
                        _ => return None,
                    }
                }
            } else {
                let mut buf = [0u8; 4];
                c.encode_utf8(&mut buf).as_bytes().to_vec()
            }
        }
        // Shift 를 키 쪽에 접은 것들은 **패널로 갈 때 수정자 없는 것과 같은 바이트**다
        // (`Key::ShiftLeft` 주석 참조) — 이 갈래를 만들기 전과 같아야 지금 도는 앱의
        // 손버릇이 안 바뀐다.
        Key::Enter | Key::ShiftEnter => vec![b'\r'],
        Key::Tab => vec![b'\t'],
        Key::BackTab => b"\x1b[Z".to_vec(),
        Key::Backspace => vec![0x7f],
        Key::Delete | Key::ShiftDelete => b"\x1b[3~".to_vec(),
        Key::Escape | Key::ShiftEscape => vec![0x1b],
        Key::Up | Key::ShiftUp => b"\x1b[A".to_vec(),
        Key::Down | Key::ShiftDown => b"\x1b[B".to_vec(),
        // Shift 를 키 쪽에 접어 뒀지만 **패널로 갈 때는 화살표 그대로**다 — 이 갈래를
        // 만들기 전과 같은 바이트라야 지금 도는 앱의 손버릇이 안 바뀐다(`Key::ShiftLeft`).
        Key::Right | Key::ShiftRight => b"\x1b[C".to_vec(),
        Key::Left | Key::ShiftLeft => b"\x1b[D".to_vec(),
        Key::Home | Key::ShiftHome => b"\x1b[H".to_vec(),
        Key::End | Key::ShiftEnd => b"\x1b[F".to_vec(),
        Key::PageUp => b"\x1b[5~".to_vec(),
        Key::PageDown => b"\x1b[6~".to_vec(),
        Key::Insert => b"\x1b[2~".to_vec(),
        Key::Function(n) => match n {
            1 => b"\x1bOP".to_vec(),
            2 => b"\x1bOQ".to_vec(),
            3 => b"\x1bOR".to_vec(),
            4 => b"\x1bOS".to_vec(),
            5 => b"\x1b[15~".to_vec(),
            6 => b"\x1b[17~".to_vec(),
            7 => b"\x1b[18~".to_vec(),
            8 => b"\x1b[19~".to_vec(),
            9 => b"\x1b[20~".to_vec(),
            10 => b"\x1b[21~".to_vec(),
            11 => b"\x1b[23~".to_vec(),
            12 => b"\x1b[24~".to_vec(),
            _ => return None,
        },
    };
    // Alt(=Meta)는 ESC 접두다. 이미 ESC 로 시작하는 시퀀스에는 붙이지 않는다 —
    // 붙이면 `ESC ESC [ A` 가 되어 자식이 다른 키로 읽는다.
    if mods.alt && !base.starts_with(&[0x1b]) {
        let mut out = Vec::with_capacity(base.len() + 1);
        out.push(0x1b);
        out.extend_from_slice(&base);
        return Some(out);
    }
    Some(base)
}

#[cfg(test)]
#[path = "keys_tests.rs"]
mod tests;

/// `send-keys` 의 인자를 **패널로 보낼 바이트**로.
///
/// 파이썬 클라와 같은 표기다: 공백으로 나뉜 토큰이 `Enter`·`C-c` 같은 **키 이름**이면
/// 그 키로, 아니면 **글자 그대로** 보낸다(`send-keys hello Enter`).
///
/// 왜 여기 있나: 이름 표(`from_name`)와 인코딩(`encode`)이 둘 다 이 모듈에 있다. 뷰가
/// 자기 손으로 파싱하면 두 뷰가 서로 다른 키를 주입하기 시작한다.
pub fn parse_send_keys(text: &str) -> Vec<u8> {
    let mut out = Vec::new();
    for token in text.split_whitespace() {
        if let Some(bytes) = named_key(token) {
            out.extend(bytes);
        } else {
            out.extend(token.as_bytes());
        }
    }
    out
}

/// 토큰 하나가 **키 이름**이면 그 바이트열. 아니면 `None`.
fn named_key(token: &str) -> Option<Vec<u8>> {
    let lower = token.to_ascii_lowercase();
    // `C-c`·`M-x` 는 수정키 + 글자다(tmux 표기).
    if let Some(rest) = lower.strip_prefix("c-")
        && rest.chars().count() == 1
    {
        return encode(Key::Char(rest.chars().next()?), Mods::CTRL);
    }
    if let Some(rest) = lower.strip_prefix("m-")
        && rest.chars().count() == 1
    {
        return encode(Key::Char(rest.chars().next()?), Mods::ALT);
    }
    // 나머지는 이름 표에 맡긴다 — 다만 **글자 하나는 이름이 아니다**(그건 그냥 그 글자다).
    if token.chars().count() == 1 {
        return None;
    }
    let key = from_name(&lower, false, None)?;
    encode(key, Mods::NONE)
}
