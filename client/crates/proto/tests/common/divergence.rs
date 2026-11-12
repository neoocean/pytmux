//! **갈림 대장** — 정본과 GUI 가 갈리는 표면 전수와 그 분류. **방향이 둘**이다.
//!
//! # 왜 이 자리인가 (pytmux-33 ⓖ3 · 2026-09-02)
//!
//! ⓖ3 의 지시는 방향이 **둘**이다: ⑶ 정본에 있는 것은 GUI 가 전부 지원하고 ⑷ **GUI 에
//! 있는 것 중 정본에서도 구현 가능한 것은 정본에도** 낸다. 앞의 것은 재는 자가 여럿이다
//! (`parity.rs` 의 표는 줄을 **정본 픽스처가 정하고**, `screen_anchor_conformance.rs` 는
//! 정본 화면이 다 맡아졌나를 본다). 뒤의 것은 **아무도 안 셌다** — 정본에 없는 표면은
//! 그 표들에 실릴 줄 자체가 없기 때문이다.
//!
//! 그래서 여기 하나로 모은다. **줄은 사람이 적고, 그 줄이 전수인지는 기계가 센다**
//! (`divergence_ledger.rs`).
//!
//! # 두 방향 (pytmux-598 · 2026-11-13)
//!
//! - [`Dir::GuiOnly`] — **GUI 에만 있는 것**(종전 25줄). 축마다 정본 픽스처와 맞대 전수를 센다.
//! - [`Dir::CanonDiffers`] — **정본에 있는데 GUI 가 안 하거나 다르게 하는 것**. 종전에는
//!   코드 옆 주석에만 흩어져 있었고 주석은 게이트가 안 읽는다. 이제 줄마다 코드 쪽에
//!   `// DIVERGE(<줄 이름>): …` 표식이 있어야 하고(파이썬은 `# DIVERGE(…)`), 표식은 대장 줄이나
//!   이슈를 가리켜야 한다 — 양방향을 `divergence_ledger.rs` 가 강제한다.
//!
//! 모든 줄에 [`Row::loses`] 가 있다 — **그 줄 때문에 정본 사용자가 얻던 기능·정보·반응이
//! GUI 에서 빠지는가**(계획 §2 원칙 2 · 사람 결정 2026-11-12). 「없음」 이면 그렇게 적는다.
//! GuiOnly 25줄의 판정은 재심 표(`pytmux/client-reports-2026-11-13-divergence-review` §1)다.
//!
//! # 분류가 이 대장의 요점이다
//!
//! [[pytmux-185]] 가 허용하는 갈림은 셋뿐이다 — ⓐ 단말이 못 주는 키 · ⓑ 픽셀 그림 ·
//! ⓒ OS 창 통합. *"그 밖의 갈림은 결함으로 본다."* 그래서 [`Class`] 는 그 셋에
//! **둘**을 더한다:
//!
//! - [`Class::SameFeature`] — 갈림이 아니다. **기능은 정본에도 있고** 우리 쪽에 이름·
//!   입구가 하나 더 있을 뿐이다. 값은 정본 픽스처의 그 자리이고, **게이트가 그 자리를
//!   실제로 찾아본다** — 산문이 아니라 확인되는 주장이라야 한다. `CanonDiffers` 에는 못 쓴다
//!   («같다» 면 갈림이 아니다).
//! - [`Class::Todo`] — 결함이다. 정본에도 낼 수 있는데 없다(또는 정본의 것을 뺀다). 값은 그
//!   일을 든 이슈. 그 수에 상한이 있다(`divergence_ledger.rs` 의 `TODO_CEILING`).
//!
//! ⛔ **`SameFeature` 의 문턱을 낮추지 말 것.** *"정본이 안 가진 것"* 은 예외가 아니라
//! **할 일**이다. 자리를 못 대면 `Todo` 다.

#![allow(dead_code)] // 시험 바이너리 셋이 나눠 쓴다 — 한쪽은 일부만 본다

/// 갈림의 방향.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Dir {
    /// GUI 에만 있는 표면 — 정본 픽스처에 짝이 없다.
    GuiOnly,
    /// 정본에 있는데 GUI 가 안 하거나 다르게 한다. 코드에 `DIVERGE(<이름>)` 표식이 있어야 한다.
    CanonDiffers,
}

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
    /// 창 계층이 패널·`bind -n` **앞에서** 가로채는 조합(`SessionView::keydown_action`).
    /// 이름은 `key_grid.json` 의 칸 이름 그대로 — 전수는 gui 의 `session_view_key_grid_tests.rs`
    /// 가 격자 260칸을 실제로 눌러 센다.
    WindowChord,
    /// 패널로 가는 키의 바이트·수식 해석(Cmd · AltGr · 레거시 단말이 못 주는 칸).
    PaneKey,
    /// 마우스 손짓의 뜻·자리.
    Mouse,
    /// 판 안의 손(키 반응·입력 방식)이 정본과 다른 자리.
    ScreenHand,
    /// 판에 보이는 항목·문구가 정본과 다른 자리.
    ScreenItems,
    /// 기동·재접속·종료의 차례.
    Lifecycle,
    /// 명령줄 인자·환경 변수·경로 해석.
    Cli,
}

impl Axis {
    pub fn label(self) -> &'static str {
        match self {
            Axis::Palette => "팔레트",
            Axis::Setting => "설정",
            Axis::Screen => "화면",
            Axis::EscKey => "esc 키",
            Axis::WindowChord => "창 조합키",
            Axis::PaneKey => "패널 키",
            Axis::Mouse => "마우스",
            Axis::ScreenHand => "판의 손",
            Axis::ScreenItems => "판의 항목",
            Axis::Lifecycle => "수명",
            Axis::Cli => "명령줄",
        }
    }

    pub const ALL: [Axis; 11] = [
        Axis::Palette,
        Axis::Setting,
        Axis::Screen,
        Axis::EscKey,
        Axis::WindowChord,
        Axis::PaneKey,
        Axis::Mouse,
        Axis::ScreenHand,
        Axis::ScreenItems,
        Axis::Lifecycle,
        Axis::Cli,
    ];
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
    /// (`<칸>:<이름>` · 게이트가 그 자리를 찾아본다). `GuiOnly` 에서만.
    SameFeature(&'static str),
    /// ⓧ 결함 — 정본에도 낼 수 있는데 없다 · 정본의 것을 뺀다. 값은 그 일을 든 이슈.
    Todo(&'static str),
}

/// 대장 한 줄.
pub struct Row {
    pub dir: Dir,
    pub axis: Axis,
    /// 축이 세는 이름 그대로(팔레트 이름 · 설정 키 · `Screen` 의 이름 · 키 표기 · 격자 칸).
    /// `CanonDiffers` 줄은 이 이름이 코드 표식 `DIVERGE(<이름>)` 의 열쇠다.
    pub name: &'static str,
    pub class: Class,
    /// **왜** 그 분류인가. 비어 있으면 그 분류는 다음 사람에게 아무 말도 안 한다.
    pub why: &'static str,
    /// 팔레트 줄만 쓰는 칸 — 정본이 모르는 이름이라 분류를 우리가 정한다.
    pub cat: &'static str,
    /// 정본 사용자가 GUI 에서 **잃는 것**. 「없음 — …」 이거나 잃는 것과 그 이슈.
    pub loses: &'static str,
}

const fn r(axis: Axis, name: &'static str, class: Class, why: &'static str, loses: &'static str) -> Row {
    Row { dir: Dir::GuiOnly, axis, name, class, why, cat: "", loses }
}

const fn p(
    name: &'static str,
    cat: &'static str,
    class: Class,
    why: &'static str,
    loses: &'static str,
) -> Row {
    Row { dir: Dir::GuiOnly, axis: Axis::Palette, name, class, why, cat, loses }
}

/// 정본에 있는데 GUI 가 다르게 하는 줄.
const fn d(axis: Axis, name: &'static str, class: Class, why: &'static str, loses: &'static str) -> Row {
    Row { dir: Dir::CanonDiffers, axis, name, class, why, cat: "", loses }
}

/// 재심 판정 «빼는 것 없음»(재심 표 §1) — 같은 말을 스물한 번 적지 않는다.
const NOTHING: &str = "없음 — 더하기만 한다(재심 2026-11-13)";
/// 창 계층 조합키 다섯이 함께 잃는 것(pytmux-628 · 사람 결정: ⓒ 로 선언).
const CHORD_LOSES: &str = "그 키에 건 `bind -n` 이 GUI 에서 발화하지 않고 패널 안 프로그램이 그 키를 \
     못 받는다(정본은 단말이 주면 `bind -n` → 패널 바이트) — 사람 결정으로 ⓒ 를 감수한다(pytmux-628)";

/// **갈림 전수.** 방향·축·이름순.
pub static LEDGER: &[Row] = &[
    // ══ GuiOnly ═══════════════════════════════════════════════════════════════
    // ── 팔레트 ────────────────────────────────────────────────────────────────
    p(
        "cursor",
        "설정/기타",
        Class::Pixels,
        "커서 다섯을 한 판에 모으고 견본을 함께 그린다(pytmux-375). 정본의 커서는 \
         **호스트 단말의 하드웨어 커서**라 저쪽에 이 판도 이 이름도 있을 수 없다",
        "없음 — 정본에 짝이 없다(커서 모양은 호스트 단말)",
    ),
    p(
        "display-panes",
        "패널",
        Class::SameFeature("prefix_keys:p_q"),
        "기능은 정본에도 있다 — 저쪽 입구는 `prefix q`(패널 번호 표시)이고 이름만 우리 \
         것이다(tmux 의 이름을 팔레트에 실었다). ⚠ 여기 종전에 적혀 있던 *\"정본 SETTINGS \
         에는 있다\"* 는 **틀렸다**(2026-09-02 실측: 정본 설정 38줄에 그 이름이 없다)",
        "없음 — `prefix q` 도 GUI 에 있다(1.5초 자동 닫힘 차이는 pytmux-610)",
    ),
    p(
        "font-scale-down",
        "설정/기타",
        Class::Pixels,
        "앱 전체 글자 크기(§10-21ⓐ) — 정본의 글자 크기는 호스트 단말이 정한다. \
         키(`Ctrl+-`)가 주 입구이고 팔레트는 그 키를 모르는 사람의 입구다",
        "이름 자체는 없음 — 주 입구 키 `Ctrl+-` 가 잃는 것은 창 조합키 줄 `ctrl+-` 가 적는다",
    ),
    p("font-scale-reset", "설정/기타", Class::Pixels, "같은 이유 — `Ctrl+0`", "이름은 없음 — 키는 창 조합키 줄 `ctrl+0`"),
    p("font-scale-up", "설정/기타", Class::Pixels, "같은 이유 — `Ctrl+=`", "이름은 없음 — 키는 창 조합키 줄 `ctrl+=`"),
    p(
        "fullscreen",
        "설정/기타",
        Class::OsWindow,
        "정본의 풀스크린은 호스트 단말의 일이다(§10-21ⓘ3 · 허용되는 갈림 ⓒ). \
         키(`Alt+Enter`)가 주 입구다",
        "이름은 없음 — 키는 창 조합키 줄 `alt+enter`",
    ),
    p(
        "menu",
        "설정/기타",
        Class::SameFeature("prefix_keys:p_enter"),
        "기능은 정본에도 있다 — 저쪽 입구는 `prefix Enter` 이고 팔레트 이름이 없을 뿐이다",
        "없음 — `prefix Enter`·`F10` 도 GUI 에 있다",
    ),
    p(
        "notice-history",
        "설정/기타",
        Class::SameFeature("screens:NoticeHistoryScreen"),
        "판은 정본에도 있다 — 저쪽 입구는 상태줄 배지 포커스(`esc ↓`)라 팔레트 이름이 없다",
        "없음 — 알림 배지 입구도 GUI 에 있다(`base::chrome` `Badge::Notices`)",
    ),
    p(
        "pane-border-status",
        "패널",
        Class::SameFeature("settings:pane-border-status"),
        "정본 설정 줄과 **같은 이름**이다 — 저쪽은 설정 화면이 입구이고 팔레트에는 안 실었다",
        NOTHING,
    ),
    p(
        "plugin-manager",
        "설정/기타",
        Class::SameFeature("client_cmds:plugin-manager"),
        "정본 명령 해석기가 **같은 이름을 받는다**(`plugins` 의 별칭) — 팔레트에만 안 실렸다",
        NOTHING,
    ),
    p(
        "popup-close",
        "설정/기타",
        Class::SameFeature("client_cmds:popup-close"),
        "정본 명령 해석기가 같은 이름을 받는다 — 팔레트에만 안 실렸다",
        NOTHING,
    ),
    p(
        "resync",
        "설정/기타",
        Class::SameFeature("client_cmds:resync"),
        "정본 명령 해석기가 같은 이름을 받는다(`reconnect` 의 별칭)",
        NOTHING,
    ),
    p(
        "status",
        "설정/기타",
        Class::SameFeature("screens:InfoTabsScreen"),
        "판은 정본에도 있다 — GUI 의 `status` 는 **상태 정보 탭 판**(`ShowInfoTabs`)을 연다. 정본의 \
         같은 판은 상태줄 호스트 배지 → `show_status_tabs(initial=2)` 가 연다. ⚠ 종전 근거 \
         `set_options:status` 는 **틀렸다** — `set status` 는 상태줄 켜기/끄기(다른 기능)다(재심 #13)",
        "없음 — 처음 여는 탭이 정본(서버 탭)과 같은지는 못 쟀다",
    ),
    // `summary` 는 **정본에도 섰다**(pytmux-538) — `blocks` 플러그인이 같은 이름을 팔레트에 싣고
    // 범용 `InfoScreen` 으로 띄우므로 이 대장에서 빠졌다(`select-blocks` 가 pytmux-469 로 빠진 것과 같다).
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
        "없음 — 정본 커서는 반전 칸 + 호스트 하드웨어 커서",
    ),
    r(Axis::Setting, "cursor-blink-interval", Class::Pixels, "깜빡임 주기도 같다(pytmux-161)", NOTHING),
    r(Axis::Setting, "cursor-color", Class::Pixels, "커서 색도 같다(pytmux-161)", NOTHING),
    r(
        Axis::Setting,
        "cursor-style",
        Class::Pixels,
        "정본은 호스트 단말의 하드웨어 커서를 쓴다 — 모양을 정하는 자리가 저쪽에 없다(pytmux-161)",
        "없음 — 자리 정보는 둘 다 준다(기본 그림이 외곽선 대 반전으로 다른 것은 ⓑ 안이다)",
    ),
    r(
        Axis::Setting,
        "cursor-thickness",
        Class::Pixels,
        "정본은 커서를 스스로 안 그려서 선 굵기를 정할 자리가 없다(pytmux-375)",
        NOTHING,
    ),
    r(
        Axis::Setting,
        "font-family",
        Class::Pixels,
        "정본의 고정폭 글꼴도 호스트 단말이 정한다 — 우리만 캔버스를 직접 그린다(pytmux-408)",
        NOTHING,
    ),
    r(
        Axis::Setting,
        "font-scale",
        Class::Pixels,
        "정본의 글자 크기는 호스트 단말이 정한다 — 저쪽에 짝이 있을 수 없다(§10-21ⓐ)",
        NOTHING,
    ),
    // ── 화면 ──────────────────────────────────────────────────────────────────
    r(
        Axis::Screen,
        "Cursor",
        Class::Pixels,
        "위 커서 다섯 줄과 **같은 근거**다 — 정본은 커서를 스스로 안 그리므로 그 판이 \
         설 자리가 없다(pytmux-375)",
        "없음 — 짝이 없다(계약 `Allowed(NativeOnly)` 도 같은 판정)",
    ),
    r(
        Axis::Screen,
        "PluginView",
        Class::SameFeature("commands:plugins"),
        "정본도 플러그인 화면을 그린다 — 다만 저쪽은 **플러그인마다 다른 Textual 클래스**라 \
         이름이 하나로 안 정해진다(우리는 한 판이 여러 모양을 다 그린다). 모양·`do` 배선·\
         줄의 어휘를 재는 자는 따로 셋이다(`plugin_screen_conformance` · \
         `tests/test_plugin_do_wiring.py` · `screen_row_conformance`)",
        "판마다 빠지던 키·항목은 pytmux-611~618 이 고쳤다(전부 닫힘 2026-11-13) — 남은 것은 판의 \
         그림 차이(ⓑ)와 아래 `판의 항목` 줄들",
    ),
    // ── esc 키 ────────────────────────────────────────────────────────────────
    //
    // ★ 뿌리가 갈렸다(pytmux-466) — 블록 목록 데모 판의 키는 `base::BLOCK_BINDINGS` 로 갔고, 그
    //   일곱이 esc 모드에서 정본의 모르는 키와 같이 구는지를 `mode_transition_conformance` 가 잰다.
    //   ⇒ 남은 줄은 셋(`[`·`ctrl-,`·`v`)이다. `b` 는 정본도 갖는다(`select-blocks` · 픽스처 `esc_keys.e_b`).
    r(
        Axis::EscKey,
        "[",
        Class::SameFeature("prefix_keys:p_lb"),
        "스크롤 모드 진입. 정본은 그 입구가 `prefix [` 하나이고 우리는 esc 에도 뒀다 — \
         tmux 의 copy-mode 관습을 두 모드 어느 쪽에서도 쓸 수 있게 한 것이다",
        "없음 — 정본 esc `[` 는 모드만 푼다(그 키는 저쪽에서 아무 일도 안 한다)",
    ),
    r(
        Axis::EscKey,
        "ctrl-,",
        Class::SameFeature("commands:settings"),
        "설정 화면. 기능은 정본에도 있고(팔레트 `settings`) OS 관례(Cmd/Ctrl+,)를 좇은 \
         단축키만 우리 것이다(pytmux-178)",
        "없음 — 정본은 모드만 푼다",
    ),
    r(
        Axis::EscKey,
        "v",
        Class::SameFeature("command_cats:claude-detail"),
        "Claude 플랜/거부 전문 판을 연다 — 기능은 정본에도 있고(`:claude-detail`) 글자 하나가 \
         우리 쪽 지름길일 뿐이다",
        "없음 — 정본은 모드만 푼다",
    ),
    // ── 패널 키 ───────────────────────────────────────────────────────────────
    r(
        Axis::PaneKey,
        "f3-modified",
        Class::TerminalKey,
        "수식 F3(`Shift/Ctrl/Alt+F3`) — 정본은 단말이 그 칸을 CPR 과 겹치는 `CSI 1;nR` 로 줘 못 받는다 \
         (`key_bytes_conformance` 의 `CANON_CANNOT` 한 줄)",
        "없음 — 정본이 못 받는 칸을 GUI 가 더 줄 뿐이다",
    ),
    r(
        Axis::PaneKey,
        "legacy-lost",
        Class::TerminalKey,
        "레거시 단말(kitty 키보드 규약 없음)이 **못 주는 칸** — 격자 `key_grid.json` 의 \
         `legacy_lost` 칸 전부(실측 39). GUI 는 창 이벤트에서 그 조합을 직접 받는다",
        "없음 — 정본도 그 단말에서는 못 받는다(받는 단말에서는 같은 바이트다)",
    ),
    // ── 판의 항목 ─────────────────────────────────────────────────────────────
    r(
        Axis::ScreenItems,
        "PluginView:token-log-models-daily",
        Class::Todo("pytmux-644"),
        "토큰 로그의 **모델·일별** 판은 정본 `TokenLogScreen` 띠에 없다 — 서버 `screenspec._HUB_EXTRA` 가 \
         띠 **뒤에** 붙인다(더하기만 · 순환에서 빠진다 · pytmux-617). 정본에도 낼 수 있다(ⓖ3 ⑷)",
        "없음 — 정본의 차례·첫 화면·순환은 그대로다",
    ),
    // ── 명령줄 ────────────────────────────────────────────────────────────────
    //
    // 하네스 손잡이 — 창을 찍고 입력기를 재는 자리라 정본(단말 안의 앱)에 짝이 있을 수 없다.
    // 이름이 `gui/src/main.rs` 에 실재하는지 `divergence_ledger.rs` 가 본다.
    r(
        Axis::Cli,
        "--crash-probe",
        Class::OsWindow,
        "메인 스레드에서 일부러 패닉을 내 충돌 기록을 재는 하네스(정본에 짝이 없다)",
        "없음 — 하네스다",
    ),
    r(
        Axis::Cli,
        "--frame-dump",
        Class::OsWindow,
        "첫 화면을 PNG 로 덤프하고 끝낸다(확인용 하네스)",
        "없음 — 하네스다",
    ),
    r(Axis::Cli, "--frame-keys", Class::OsWindow, "덤프 전에 키를 넣는다(하네스)", "없음 — 하네스다"),
    r(
        Axis::Cli,
        "--ime-probe",
        Class::OsWindow,
        "창을 안 띄우고 OS 입력 소스를 한 번 읽어 찍는다(pytmux-590 하네스)",
        "없음 — 하네스다",
    ),
    r(
        Axis::Cli,
        "demo",
        Class::OsWindow,
        "서버 없이 데모 화면을 띄운다(스크린샷·개발 손잡이)",
        "없음 — 하네스다",
    ),
    // ══ CanonDiffers ══════════════════════════════════════════════════════════
    // ── 설정 ──────────────────────────────────────────────────────────────────
    d(
        Axis::Setting,
        "alt-scroll",
        Class::OsWindow,
        "정본은 호스트 단말에 `ESC[?1007l/h` 를 쓴다(`client.py` `apply_option`). GUI 에는 호스트 \
         단말이 없고 winit 에서 진짜 휠을 받는다. 값은 읽고 쓰며(설정 파일 공유) 판은 «이 앱에서는 \
         효과 없음» 을 단다(`SETTING_NOTES` · 사람 결정 2026-11-12)",
        "없음 — 그 설정이 고치던 문제(단말이 휠을 화살표로 바꿈)가 GUI 에는 없다",
    ),
    d(
        Axis::Setting,
        "monitor-bell",
        Class::Todo("pytmux-642"),
        "§10-21ⓜ 의 예전 결정으로 GUI 설정 판·팔레트에서 감췄다(`base::keymap::HIDDEN_SURFACES`). \
         명령 줄·`bind` 로는 먹는다. 원칙 2(«빼지 않는다»)와 부딪쳐 사람 결정 대기",
        "설정 판의 `monitor-bell` 줄과 팔레트 입구",
    ),
    // ── 창 조합키 (`key_grid::INTERCEPTS` 의 자리 — pytmux-628) ───────────────────
    d(
        Axis::WindowChord,
        "alt+enter",
        Class::OsWindow,
        "전체 화면(`is_fullscreen_chord`) — 정본의 전체 화면은 호스트 단말의 일이다",
        CHORD_LOSES,
    ),
    d(
        Axis::WindowChord,
        "ctrl+-",
        Class::OsWindow,
        "글자 크기 줄이기(`font_scale_chord`) — 정본의 글자 크기는 호스트 단말이 정한다",
        CHORD_LOSES,
    ),
    d(Axis::WindowChord, "ctrl+0", Class::OsWindow, "글자 크기 되돌리기(`font_scale_chord`)", CHORD_LOSES),
    d(Axis::WindowChord, "ctrl+=", Class::OsWindow, "글자 크기 키우기(`font_scale_chord`)", CHORD_LOSES),
    d(
        Axis::WindowChord,
        "ctrl+shift+tab",
        Class::OsWindow,
        "이전 탭(`tab_switch_chord`) — 독립 앱이라 받을 수 있다(정본은 단말 안이라 단말이 먹는다)",
        CHORD_LOSES,
    ),
    d(
        Axis::WindowChord,
        "ctrl+shift+v",
        Class::OsWindow,
        "붙여넣기 — 터미널 관례 별칭(`paste_entry`). 정본에서는 바깥 단말이 그 키로 붙여넣는다",
        "없음 — 정본에서도 그 키는 패널로 안 가고 바깥 단말이 붙여넣는다",
    ),
    d(Axis::WindowChord, "ctrl+tab", Class::OsWindow, "다음 탭(`tab_switch_chord`)", CHORD_LOSES),
    // ── 패널 키 ───────────────────────────────────────────────────────────────
    d(
        Axis::PaneKey,
        "altgr",
        Class::OsWindow,
        "Windows 의 AltGr(= Ctrl+Alt)가 글자를 낸 칸은 그 글자를 보낸다 — 정본은 호스트 단말이 \
         AltGr 를 글자로 풀어 준다(pytmux-34 #4)",
        "없음 — 같은 글자가 간다",
    ),
    d(
        Axis::PaneKey,
        "cmd-as-alt",
        Class::OsWindow,
        "맥 Cmd·Super 는 Alt 로 친다 — 정본은 호스트 단말에서 Cmd 를 받을 수 없다(사람 결정 2026-11-13)",
        "없음 — 정본은 그 조합을 아예 못 받는다",
    ),
    // ── 마우스 ────────────────────────────────────────────────────────────────
    d(
        Axis::Mouse,
        "ctrl-click-mac",
        Class::OsWindow,
        "맥에서 상류(winit)가 Ctrl+왼쪽 클릭을 우클릭으로 바꿔 준다 → pytmux 메뉴. 정본 `on_mouse_down` \
         은 Ctrl+클릭을 무동작으로 둔다(「컨텍스트 메뉴는 순수 우클릭으로만」)",
        "없음 — 맥 관례이고 다른 OS 의 Ctrl+클릭은 정본처럼 무동작이다",
    ),
    d(
        Axis::Mouse,
        "tab-close-x",
        Class::Pixels,
        "현재 탭 닫기 × 의 **자리** — 정본은 활성 패널 상단 테두리 오른쪽에 `[x]` 글자, GUI 는 활성 \
         탭 칩 안의 × (같은 뜻·같은 확인 갈래·늘 그림 · pytmux-594)",
        "없음 — 같은 길이 다른 자리에 있다",
    ),
    // ── 판의 손 ───────────────────────────────────────────────────────────────
    d(
        Axis::ScreenHand,
        "PluginView:name-sync-form",
        Class::Pixels,
        "규칙 폼 — 정본 `RuleFormScreen` 은 칸마다 입력란이고, GUI 는 줄마다 값이 붙은 판에서 `Enter` 가 \
         그 칸을 묻는다(TUI 식 입력 위젯을 흉내내지 않는다 · pytmux-615). 칸 넷·`Ctrl+S` 저장·비면 거절은 같다",
        "없음 — 같은 칸·같은 저장·같은 거절",
    ),
    // ── 판의 항목 ─────────────────────────────────────────────────────────────
    d(
        Axis::ScreenItems,
        "DebugStats:client-half",
        Class::OsWindow,
        "`debug-stats` 판의 클라 절반 — 각자 **제 프로세스**를 잰다(정본 파이썬 힙·GC 세대 · GUI 그린 \
         프레임·글리프 캐시·RTT). 서버 절반은 줄까지 같다(`debug_stats_conformance` · pytmux-627 ②)",
        "없음 — 정본의 그 값(파이썬 힙)은 GUI 프로세스에 없다",
    ),
    // ── 수명 ──────────────────────────────────────────────────────────────────
    d(
        Axis::Lifecycle,
        "exit-keeps-window",
        Class::OsWindow,
        "끝난 뒤에도 창을 닫지 않고 문구를 바닥 줄에 건다 — 정본은 `self.exit(message=…)` 로 앱을 \
         끝내고 문구를 단말에 남긴다. GUI 에는 남길 단말이 없다(`base::lifecycle` 문서)",
        "없음 — 문구는 남고, 끝난 뒤 수동 `reconnect` 가 뜻을 갖는다",
    ),
    d(
        Axis::Lifecycle,
        "nested-guard",
        Class::OsWindow,
        "정본은 패널 안에서 또 띄우면 거절한다(`launcher.nesting_blocked` — `$PYTMUX`·`$LC_PYTMUX`). \
         GUI 는 거절하지 않는다 — 제 창에 그리므로 패널 안에 패널이 겹치는 중첩이 생기지 않는다",
        "없음 — 막던 사고(같은 단말 안의 이중 그리기)가 GUI 에는 없다",
    ),
    d(
        Axis::Lifecycle,
        "relaunch-order",
        Class::OsWindow,
        "전체 재시작 — 정본은 `restarting{relaunch}` 뒤 EOF 에서 곧장 execv 한다. GUI 는 제자리에서 다시 \
         붙은 **뒤에** 띄운다(빨리 뜨는 이진이 execv 중인 서버를 «없음» 으로 읽어 두 번째 서버를 \
         세우지 않게). 정본 Windows 갈래와 같은 길",
        "없음 — 차례만 다르고 결과(새 클라 하나 · 서버 하나)는 같다",
    ),
    // ── 명령줄 ────────────────────────────────────────────────────────────────
    d(
        Axis::Cli,
        "pytmux-config-write",
        Class::Todo("pytmux-643"),
        "설정 쓰기 자리 — 정본 `config_path_for_write` 는 `$PYTMUX_CONFIG` 를 **파일이 있을 때만** 본다. \
         GUI 는 서 있으면 그 자리에 쓴다(격리 레시피가 그 동작에 기댄다)",
        "`$PYTMUX_CONFIG` 가 없는 파일을 가리킬 때 정본은 다음 후보(홈 설정)에 쓰고 GUI 는 새 파일을 만든다",
    ),
    d(
        Axis::Cli,
        "tilde-user",
        Class::Todo("pytmux-643"),
        "`~user/…` — 정본 `expanduser` 는 그 사용자가 있으면 그 홈으로 편다. GUI 는 펴지 않고 상대경로로 본다",
        "`--socket`/상태 경로에 `~other/…` 를 쓴 사람의 뜻",
    ),
];

/// 표식(`DIVERGE(<이름>)`)이 가리킬 수 있는 줄 이름 — `CanonDiffers` 줄 전부.
pub fn marker_names() -> Vec<&'static str> {
    LEDGER.iter().filter(|row| row.dir == Dir::CanonDiffers).map(|row| row.name).collect()
}

/// 한 축의 `CanonDiffers` 이름(예: 창 조합키 — gui 격자 자가 읽는다).
pub fn canon_differs(axis: Axis) -> Vec<&'static Row> {
    LEDGER
        .iter()
        .filter(|row| row.dir == Dir::CanonDiffers && row.axis == axis)
        .collect()
}

/// 팔레트 축의 (이름, 분류) — `category_conformance.rs` 가 쓰던 `PALETTE_OURS` 의 자리.
pub fn palette_ours() -> Vec<(&'static str, &'static str)> {
    LEDGER
        .iter()
        .filter(|row| row.dir == Dir::GuiOnly && row.axis == Axis::Palette)
        .map(|row| (row.name, row.cat))
        .collect()
}

/// 설정 축의 이름 — `SETTINGS_OURS` 의 자리.
pub fn settings_ours() -> Vec<&'static str> {
    LEDGER
        .iter()
        .filter(|row| row.dir == Dir::GuiOnly && row.axis == Axis::Setting)
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
        // 정본의 「찍히는 글자 하나」 갈래(`event.character … isprintable()` — 생성기가 의사키로
        // 적는다 · pytmux-591). 대표 글자 하나로 누른다 — 어느 판의 키 표에도 없는 글자라야
        // 그 판의 다른 팔에 안 걸린다(`q`·`h`·`v`·`c`·`d`·`x` 는 판마다 뜻이 있다).
        "<printable>" => plain(Key::Char('k')),
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
            // `f1`~`f12` — 종전에는 `f12` 하나만 읽어 정본 메뉴 판의 `f10`(닫기)을 **조용히
            // 빼고** 대조했다(pytmux-606 코멘트). 이름을 모르면 `None` 이라 그 키는 아무도 안 잰다.
            if let Some(n) = name.strip_prefix('f').and_then(|d| d.parse::<u8>().ok())
                && (1..=12).contains(&n)
            {
                return plain(Key::Function(n));
            }
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
