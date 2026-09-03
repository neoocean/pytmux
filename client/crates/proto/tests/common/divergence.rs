//! **갈림 대장** — GUI 에만 있는 표면 전수와 그 분류.
//!
//! # 왜 이 자리인가 (pytmux-33 ⓖ3 · 2026-09-02)
//!
//! ⓖ3 의 지시는 방향이 **둘**이다: ⑶ 정본에 있는 것은 GUI 가 전부 지원하고 ⑷ **GUI 에
//! 있는 것 중 정본에서도 구현 가능한 것은 정본에도** 낸다. 앞의 것은 재는 자가 여럿이다
//! (`parity.rs` 의 표는 줄을 **정본 픽스처가 정하고**, `screen_anchor_conformance.rs` 는
//! 정본 화면이 다 맡아졌나를 본다). 뒤의 것은 **아무도 안 셌다** — 정본에 없는 표면은
//! 그 표들에 실릴 줄 자체가 없기 때문이다.
//!
//! 종전에 그 자리를 지키던 것은 팔레트 이름과 설정 줄, **둘뿐**이었다
//! (`category_conformance.rs` 의 `PALETTE_OURS`·`SETTINGS_OURS`). 화면·키는 그 밖이라,
//! GUI 전용 화면이 넷 서 있어도 아무 게이트도 울지 않았다.
//!
//! 그래서 여기 하나로 모은다. **줄은 사람이 적고, 그 줄이 전수인지는 기계가 센다**
//! (`divergence_ledger.rs`). 여섯 축을 전수로 훑어 대장에 없는 것이 하나라도 있으면 운다.
//!
//! # 분류가 이 대장의 요점이다
//!
//! [[pytmux-185]] 가 허용하는 갈림은 셋뿐이다 — ⓐ 단말이 못 주는 키 · ⓑ 픽셀 그림 ·
//! ⓒ OS 창 통합. *"그 밖의 갈림은 결함으로 본다."* 그래서 [`Class`] 는 그 셋에
//! **둘**을 더한다:
//!
//! - [`Class::SameFeature`] — 갈림이 아니다. **기능은 정본에도 있고** 우리 쪽에 이름·
//!   입구가 하나 더 있을 뿐이다. 값은 정본 픽스처의 그 자리이고, **게이트가 그 자리를
//!   실제로 찾아본다** — 산문이 아니라 확인되는 주장이라야 한다.
//! - [`Class::Todo`] — 결함이다. 정본에도 낼 수 있는데 없다. 값은 그 일을 든 이슈.
//!
//! ⛔ **`SameFeature` 의 문턱을 낮추지 말 것.** *"정본이 안 가진 것"* 은 예외가 아니라
//! **할 일**이다(종전 `SETTINGS_OURS` 머리말이 같은 말을 한다). 자리를 못 대면 `Todo` 다.

#![allow(dead_code)] // 두 시험 바이너리가 나눠 쓴다 — 한쪽은 일부만 본다

/// 이 표면이 사는 축. 축마다 세는 자가 다르다(`divergence_ledger.rs`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Axis {
    /// 명령 팔레트에 뜨는 이름(`base::PALETTE`).
    Palette,
    /// 설정 화면의 줄(`base::SETTINGS`).
    Setting,
    /// 떠 있을 수 있는 판(`base::Screen::all`).
    Screen,
    /// esc(명령) 모드의 키(`base::BINDINGS`).
    EscKey,
}

impl Axis {
    pub fn label(self) -> &'static str {
        match self {
            Axis::Palette => "팔레트",
            Axis::Setting => "설정",
            Axis::Screen => "화면",
            Axis::EscKey => "esc 키",
        }
    }
}

/// 이 갈림이 **허용되는가**, 그렇다면 어느 근거로.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Class {
    /// ⓐ 단말이 전달할 수 없는 키라 정본이 가질 수 없다.
    TerminalKey,
    /// ⓑ 픽셀 단위 그림 — 정본의 그 자리는 **호스트 단말**이 그린다.
    Pixels,
    /// ⓒ OS 창 통합 — 정본은 남의 창 안에 산다.
    OsWindow,
    /// ⓓ 갈림이 아니다 — **기능은 정본에도 있다.** 값은 정본 픽스처의 자리
    /// (`<칸>:<이름>` · 게이트가 그 자리를 찾아본다).
    SameFeature(&'static str),
    /// ⓧ 결함 — 정본에도 낼 수 있는데 없다. 값은 그 일을 든 이슈.
    Todo(&'static str),
}

/// 대장 한 줄.
pub struct Row {
    pub axis: Axis,
    /// 축이 세는 이름 그대로(팔레트 이름 · 설정 키 · `Screen` 의 이름 · 키 표기).
    pub name: &'static str,
    pub class: Class,
    /// **왜** 그 분류인가. 비어 있으면 그 분류는 다음 사람에게 아무 말도 안 한다.
    pub why: &'static str,
    /// 팔레트 줄만 쓰는 칸 — 정본이 모르는 이름이라 분류를 우리가 정한다.
    pub cat: &'static str,
}

const fn r(axis: Axis, name: &'static str, class: Class, why: &'static str) -> Row {
    Row { axis, name, class, why, cat: "" }
}

const fn p(name: &'static str, cat: &'static str, class: Class, why: &'static str) -> Row {
    Row { axis: Axis::Palette, name, class, why, cat }
}

/// ⑴ 블록 표면 · ⑵ ClaudeDetail · ⑶ `esc q` 를 든 이슈([[pytmux-449]]).
const DECIDE: &str = "pytmux-449";

/// **GUI 에만 있는 표면 전수.** 축·이름순.
pub static LEDGER: &[Row] = &[
    // ── 팔레트 ────────────────────────────────────────────────────────────────
    p(
        "cursor",
        "설정/기타",
        Class::Pixels,
        "커서 다섯을 한 판에 모으고 견본을 함께 그린다(pytmux-375). 정본의 커서는 \
         **호스트 단말의 하드웨어 커서**라 저쪽에 이 판도 이 이름도 있을 수 없다",
    ),
    p(
        "display-panes",
        "패널",
        Class::SameFeature("prefix_keys:p_q"),
        "기능은 정본에도 있다 — 저쪽 입구는 `prefix q`(패널 번호 표시)이고 이름만 우리 \
         것이다(tmux 의 이름을 팔레트에 실었다). ⚠ 여기 종전에 적혀 있던 *\"정본 SETTINGS \
         에는 있다\"* 는 **틀렸다**(2026-09-02 실측: 정본 설정 38줄에 그 이름이 없다)",
    ),
    p(
        "font-scale-down",
        "설정/기타",
        Class::Pixels,
        "앱 전체 글자 크기(§10-21ⓐ) — 정본의 글자 크기는 호스트 단말이 정한다. \
         키(`Ctrl+-`)가 주 입구이고 팔레트는 그 키를 모르는 사람의 입구다",
    ),
    p("font-scale-reset", "설정/기타", Class::Pixels, "같은 이유 — `Ctrl+0`"),
    p("font-scale-up", "설정/기타", Class::Pixels, "같은 이유 — `Ctrl+=`"),
    p(
        "fullscreen",
        "설정/기타",
        Class::OsWindow,
        "정본의 풀스크린은 호스트 단말의 일이다(§10-21ⓘ3 · 허용되는 갈림 ⓒ). \
         키(`Alt+Enter`)가 주 입구다",
    ),
    p(
        "menu",
        "설정/기타",
        Class::SameFeature("prefix_keys:p_enter"),
        "기능은 정본에도 있다 — 저쪽 입구는 `prefix Enter` 이고 팔레트 이름이 없을 뿐이다",
    ),
    p(
        "notice-history",
        "설정/기타",
        Class::SameFeature("screens:NoticeHistoryScreen"),
        "판은 정본에도 있다 — 저쪽 입구는 상태줄 배지 포커스(`esc ↓`)라 팔레트 이름이 없다",
    ),
    p(
        "pane-border-status",
        "패널",
        Class::SameFeature("settings:pane-border-status"),
        "정본 설정 줄과 **같은 이름**이다 — 저쪽은 설정 화면이 입구이고 팔레트에는 안 실었다",
    ),
    p(
        "plugin-manager",
        "설정/기타",
        Class::SameFeature("client_cmds:plugin-manager"),
        "정본 명령 해석기가 **같은 이름을 받는다**(`plugins` 의 별칭) — 팔레트에만 안 실렸다",
    ),
    p(
        "popup-close",
        "설정/기타",
        Class::SameFeature("client_cmds:popup-close"),
        "정본 명령 해석기가 같은 이름을 받는다 — 팔레트에만 안 실렸다",
    ),
    p(
        "resync",
        "설정/기타",
        Class::SameFeature("client_cmds:resync"),
        "정본 명령 해석기가 같은 이름을 받는다(`reconnect` 의 별칭)",
    ),
    p(
        "select-blocks",
        "복사/버퍼",
        Class::Todo(DECIDE),
        "블록(명령 + 그 출력) 하나를 골라 옮기고 복사하는 모드(pytmux-18). 정본 클라는 \
         서버가 보내는 `blocks` 를 **안 그린다** — 못 그릴 이유는 없으므로 「정본이 가질 수 \
         없는 것」이 아니라 **아직 안 낸 것**이다",
    ),
    p(
        "status",
        "설정/기타",
        Class::SameFeature("set_options:status"),
        "기능은 정본에도 있다 — 저쪽 입구는 `set status` 이고 팔레트 이름이 없을 뿐이다",
    ),
    p(
        "summary",
        "설정/기타",
        Class::Todo(DECIDE),
        "블록 목록 + Claude 항목 요약 판(§10-21ⓓ). 앞 절반이 위 `select-blocks` 와 같은 \
         표면이라 함께 정해진다",
    ),
    // ── 설정 ──────────────────────────────────────────────────────────────────
    //
    // 일곱 줄이 **한 근거**다: 정본은 캔버스를 직접 안 그린다. 글꼴도 커서도 주인이
    // 호스트 단말(iTerm2·Windows Terminal)이라, 저쪽에 그 줄이 있으면 오히려 거짓말이
    // 된다(설정해도 단말이 안 듣는다). 어긋날 값이 없으니 두 클라가 갈라지지도 않는다.
    r(
        Axis::Setting,
        "cursor-blink",
        Class::Pixels,
        "깜빡임 여부는 호스트 단말의 설정이다(pytmux-161)",
    ),
    r(
        Axis::Setting,
        "cursor-blink-interval",
        Class::Pixels,
        "깜빡임 주기도 같다(pytmux-161)",
    ),
    r(Axis::Setting, "cursor-color", Class::Pixels, "커서 색도 같다(pytmux-161)"),
    r(
        Axis::Setting,
        "cursor-style",
        Class::Pixels,
        "정본은 호스트 단말의 하드웨어 커서를 쓴다 — 모양을 정하는 자리가 저쪽에 없다(pytmux-161)",
    ),
    r(
        Axis::Setting,
        "cursor-thickness",
        Class::Pixels,
        "정본은 커서를 스스로 안 그려서 선 굵기를 정할 자리가 없다(pytmux-375)",
    ),
    r(
        Axis::Setting,
        "font-family",
        Class::Pixels,
        "정본의 고정폭 글꼴도 호스트 단말이 정한다 — 우리만 캔버스를 직접 그린다(pytmux-408)",
    ),
    r(
        Axis::Setting,
        "font-scale",
        Class::Pixels,
        "정본의 글자 크기는 호스트 단말이 정한다 — 저쪽에 짝이 있을 수 없다(§10-21ⓐ)",
    ),
    // ── 화면 ──────────────────────────────────────────────────────────────────
    r(
        Axis::Screen,
        "ClaudeDetail",
        Class::Todo(DECIDE),
        "Claude 의 플랜 전문·거부 사유. 정본 `claude-code` 플러그인의 화면은 다섯이고 그 \
         판은 없다 — Textual 모달로 못 할 이유가 없다",
    ),
    r(
        Axis::Screen,
        "Cursor",
        Class::Pixels,
        "위 커서 다섯 줄과 **같은 근거**다 — 정본은 커서를 스스로 안 그리므로 그 판이 \
         설 자리가 없다(pytmux-375)",
    ),
    r(
        Axis::Screen,
        "PluginView",
        Class::SameFeature("commands:plugins"),
        "정본도 플러그인 화면을 그린다 — 다만 저쪽은 **플러그인마다 다른 Textual 클래스**라 \
         이름이 하나로 안 정해진다(우리는 한 판이 여섯 모양을 다 그린다). 모양·`do` 배선·\
         줄의 어휘를 재는 자는 따로 셋이다(`plugin_screen_conformance` · \
         `tests/test_plugin_do_wiring.py` · `screen_row_conformance`)",
    ),
    r(
        Axis::Screen,
        "Summary",
        Class::Todo(DECIDE),
        "위 팔레트 `summary` 와 같은 줄이다 — 판과 그 이름이 함께 정해진다",
    ),
    // ── esc 키 ────────────────────────────────────────────────────────────────
    //
    // ★ **뿌리가 갈렸다**(pytmux-466 · 449 ⑶ · 사람 결정 «표를 가른다»). 종전에는
    //   `base::BINDINGS` 한 표가 **블록 목록 데모 판**의 키 표이면서 동시에 **세션 뷰 esc
    //   모드**의 표라 데모 판의 키(`q`·`j`·`k`·`g`·`shift-G`·`enter`·`space`)가 esc
    //   모드로 함께 샜다 — 그 일곱 줄이 여기 있었고 `q` 만 결과가 있었다(정본은 모드만
    //   푸는데 우리는 창을 닫았다). 지금은 `base::BLOCK_BINDINGS` 가 그 표이고, 그 일곱이
    //   esc 모드에서 **정본의 모르는 키와 같이 구는지**를
    //   `mode_transition_conformance::the_demo_pane_keys_do_not_leak_into_the_session_esc_mode`
    //   가 실제로 눌러 잰다. ⇒ 남은 줄은 셋(`[`·`b`·`ctrl-,`·`v`)뿐이다.
    r(
        Axis::EscKey,
        "[",
        Class::SameFeature("prefix_keys:p_lb"),
        "스크롤 모드 진입. 정본은 그 입구가 `prefix [` 하나이고 우리는 esc 에도 뒀다 — \
         tmux 의 copy-mode 관습을 두 모드 어느 쪽에서도 쓸 수 있게 한 것이다",
    ),
    r(
        Axis::EscKey,
        "b",
        Class::Todo(DECIDE),
        "블록 고르기 모드로 들어간다 — 위 팔레트 `select-blocks` 와 **같은 줄**이다",
    ),
    r(
        Axis::EscKey,
        "ctrl-,",
        Class::SameFeature("commands:settings"),
        "설정 화면. 기능은 정본에도 있고(팔레트 `settings`) OS 관례(Cmd/Ctrl+,)를 좇은 \
         단축키만 우리 것이다(pytmux-178)",
    ),
    r(
        Axis::EscKey,
        "v",
        Class::Todo(DECIDE),
        "Claude 플랜/거부 전문 판을 연다 — 위 화면 `ClaudeDetail` 과 **같은 줄**이다",
    ),
];

/// 팔레트 축의 (이름, 분류) — `category_conformance.rs` 가 쓰던 `PALETTE_OURS` 의 자리.
pub fn palette_ours() -> Vec<(&'static str, &'static str)> {
    LEDGER
        .iter()
        .filter(|row| row.axis == Axis::Palette)
        .map(|row| (row.name, row.cat))
        .collect()
}

/// 설정 축의 이름 — `SETTINGS_OURS` 의 자리.
pub fn settings_ours() -> Vec<&'static str> {
    LEDGER
        .iter()
        .filter(|row| row.axis == Axis::Setting)
        .map(|row| row.name)
        .collect()
}

// ── 정본 키 이름을 우리 키로 ────────────────────────────────────────────────────

use base::keys::{Key, Mods};

/// 정본이 분기에 쓰는 키 이름 → 우리 키.
///
/// 이름이 둘인 것(Textual 의 `percent_sign` 과 글자 `%`)은 **같은 키**다 — 정본이 `k`(키
/// 이름)와 `ch`(글자)를 둘 다 견주므로 픽스처에도 둘 다 든다.
///
/// 자리가 여기인 이유: 이 표를 읽는 시험이 둘이다(`mode_transition_conformance` 는
/// 「그 키를 누르면 어떻게 되나」를, `divergence_ledger` 는 「정본에 없는 키가 무엇인가」를
/// 묻는다). 각자 적으면 두 자가 **서로 다른 키 집합**을 보게 된다.
pub fn key_of(name: &str) -> Option<(Key, Mods)> {
    let plain = |key| Some((key, Mods::NONE));
    match name {
        "space" => plain(Key::Char(' ')),
        "enter" => plain(Key::Enter),
        "tab" => plain(Key::Tab),
        "escape" => plain(Key::Escape),
        "insert" => plain(Key::Insert),
        "shift+delete" => plain(Key::ShiftDelete),
        "shift+escape" => plain(Key::ShiftEscape),
        "up" => plain(Key::Up),
        "down" => plain(Key::Down),
        "left" => plain(Key::Left),
        "right" => plain(Key::Right),
        "ctrl+up" => Some((Key::Up, Mods::CTRL)),
        "ctrl+down" => Some((Key::Down, Mods::CTRL)),
        "ctrl+o" => Some((Key::Char('o'), Mods::CTRL)),
        "f12" => plain(Key::Function(12)),
        "pageup" => plain(Key::PageUp),
        "pagedown" => plain(Key::PageDown),
        "end" => plain(Key::End),
        "home" => plain(Key::Home),
        "slash" => plain(Key::Char('/')),
        // `mode-keys`(vi·emacs)가 스크롤 모드에 더 거는 키들.
        "ctrl+u" => Some((Key::Char('u'), Mods::CTRL)),
        "ctrl+d" => Some((Key::Char('d'), Mods::CTRL)),
        "ctrl+n" => Some((Key::Char('n'), Mods::CTRL)),
        "ctrl+p" => Some((Key::Char('p'), Mods::CTRL)),
        "ctrl+v" => Some((Key::Char('v'), Mods::CTRL)),
        "alt+v" => Some((Key::Char('v'), Mods::ALT)),
        // Textual 의 키 이름 별칭.
        "colon" => plain(Key::Char(':')),
        "grave_accent" => plain(Key::Char('`')),
        "percent_sign" => plain(Key::Char('%')),
        "quotation_mark" => plain(Key::Char('"')),
        "ampersand" => plain(Key::Char('&')),
        "comma" => plain(Key::Char(',')),
        "period" => plain(Key::Char('.')),
        "semicolon" => plain(Key::Char(';')),
        "equals_sign" => plain(Key::Char('=')),
        "left_square_bracket" => plain(Key::Char('[')),
        "right_square_bracket" => plain(Key::Char(']')),
        _ => {
            let mut chars = name.chars();
            match (chars.next(), chars.next()) {
                (Some(c), None) if !c.is_control() => plain(Key::Char(c)),
                _ => None,
            }
        }
    }
}

/// 정본 분기 이름들을 **우리 키 표기**(`shift-G`·`ctrl-up`)의 집합으로.
///
/// 못 읽는 이름은 버리지 않고 그대로 남긴다 — 버리면 그 키가 「정본에 없다」로 세져
/// 조용히 갈림이 하나 늘어난다. 부르는 쪽이 그것을 보고 운다.
pub fn canon_binding_names<'a>(
    names: impl Iterator<Item = &'a str>,
) -> (std::collections::BTreeSet<String>, Vec<String>) {
    let mut known = std::collections::BTreeSet::new();
    let mut unread = Vec::new();
    for name in names {
        if name == "*" {
            continue;
        }
        match key_of(name).and_then(|(key, mods)| base::keys::binding_name_with(key, mods)) {
            Some(binding) => {
                known.insert(binding);
            }
            None => unread.push(name.to_owned()),
        }
    }
    (known, unread)
}
