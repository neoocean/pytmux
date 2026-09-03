//! 라이브 세션 뷰(GUI) — 서버가 보내 주는 화면을 그린다.
//!
//! 짝이 되는 TUI 뷰는 `tui::session_view`. 두 파일을 나란히 놓고 보면
//! **다른 것은 엘리먼트 타입과 색 지정뿐**이다 — 상태 누적(`SessionState`)·격자 합성
//! (`Canvas`)·연결(`ServerLink`)은 전부 `proto` 한 벌을 쓴다.
//!
//! # 이 단계에서 그리는 것 (P2)
//!
//! 캔버스뿐이다. 탭바는 P3, 블록은 P4, Claude 구역은 P5, 입력은 P7 이고 그때마다 TUI 가
//! 이미 밟은 자리를 따라간다. 한 슬라이스에 다 넣으면 무엇이 깨졌는지 가릴 수 없다.
//!
//! # 색은 GUI 가 정해야 한다 — TUI 와 갈리는 유일한 자리
//!
//! TUI 는 팔레트 색을 **이름 그대로** 터미널에 넘긴다(사용자 테마가 실제 RGB 를 정한다).
//! GUI 에는 물려받을 테마가 없으므로 [`palette`] 에서 구체적인 값을 고른다. 그래서 두 뷰의
//! 그림이 색까지 같지는 않다 — **같아야 하는 것은 글자와 배치**이고, 그건 같은 `Canvas` 에서
//! 나온다.

use std::time::{Duration, Instant};

use claude::discover::{Watcher, projects_dir};
use claude::source::{DetailKind, RemoteTranscripts, Source, detail_lines};
use claude::{Item as ClaudeItem, ToolState};
use base::keys::{Key, KeyOutcome, ModeState, Mods, ScrollAmount};
use base::screens::{Anchor, Prompt, Screen, ScreenKey, Screens};
use base::i18n::{t, tc, tf};
use base::{Action, InputMode};
use proto::blocks::{Block, Tone};
use proto::command::{
    Command, Outgoing, Scroll, SizeReporter, action_to_command_with_tabs,
    copy_note, selection_to_buffer,
};
use proto::footer;
use proto::mouse::{self, MouseKind};
use proto::style::{CellStyle, Color as CellColor, NamedColor};
use proto::status;
use proto::session::Severity;
use proto::{LinkEvent, Selection, ServerLink, ServerMessage, SessionState};
use warpui::color::ColorU;
use warpui::elements::{
    Align, Border, Clipped, ConstrainedBox, Container, CrossAxisAlignment, DispatchEventResult,
    Empty, EventHandler, Expanded, Flex, Hoverable, MainAxisSize, MouseStateHandle, ParentElement,
    Point, Rect, Stack, Text,
};
use warpui::fonts::{FamilyId, Properties, Style as FontStyle, Weight};
use warpui::geometry::vector::{Vector2F, vec2f};
use warpui::{
    AfterLayoutContext, AppContext, Element, Entity, EventContext, LayoutContext, PaintContext,
    SingletonEntity as _, SizeConstraint, TypedActionView, View, ViewContext,
};
use warpui_core::clipboard::ClipboardContent;
use warpui_core::event::DispatchedEvent;

use crate::{mono_font, theme, titlebar};

/// 서버 팔레트의 구체 값. 이름은 `NamedColor` 와 1:1 이다.
///
/// # 어느 표인가 — **xterm 표준 16색** (pytmux-187 · 사람의 결정 2026-08-24)
///
/// `SGR 30–37`·`90–97` 열여섯 칸은 **xterm 의 기본 표**를 그대로 놓는다.
///
/// 제보는 *"GUI 가 TUI 와 다른 색으로 그린다"* 였는데, 그 「같은 색」에 **«그대로»라는
/// 값이 없었다**. 정본(TUI)은 16색을 자기가 그리지 않는다 — SGR 코드를 호스트 단말에
/// 넘기고 **그 단말의 배색**(제보 환경 = Windows Terminal 기본 Campbell)이 RGB 를
/// 정한다. GUI 는 단말 안이 아니라 창이라 그 표를 읽을 길이 없다. ⇒ 두 클라는 같은 표를
/// 다르게 매핑한 것이 **아니라 애초에 다른 표를 봤다**. 매핑 결함이 아니므로 「고치면
/// 같아진다」가 아니고, **어느 표를 기본으로 삼나**가 갈림길이었다.
///
/// 갈래 셋을 pytmux-391 로 물었다 — ⓐ xterm 표준으로 바꾼다 · ⓑ tokyonight 를 두고 이
/// 축을 «허용되는 갈림»으로 닫는다 · ⓒ 설정으로 뺀다. **사람이 ⓐ 를 골랐다**: 단말
/// 기본 배색이 대개 이 표에서 파생되므로 「정본이 의미의 권위」에 가장 가깝다.
/// ⚠ 그 값은 **창 배색이 통째로 달라지는 것**이고, 그 경고가 선택지에 적힌 채로 골랐다.
///
/// ⛔ **여기서 눈대중으로 한 톨도 옮기지 마라.** 이 열여섯 값의 근거는 취향이 아니라
/// 「xterm 의 표와 같다」 하나다 — 한 칸이라도 흔들리면 그 근거가 통째로 사라지고 다음
/// 사람은 무엇이 결정이고 무엇이 표류인지 못 가린다.
/// `the_sixteen_colors_are_the_xterm_standard_table` 이 열여섯 칸을 전수로 잰다.
/// 배색이 마음에 안 들면 값을 흔들 일이 아니라 **표를 고르는 갈림길을 다시 여는** 일이다.
///
/// ⚠ **바뀐 것은 그 열여섯 칸뿐이다.** 창 바탕(`BG`)·본문(`FG`)·흐린 글자(`DIM`)·
/// 고른 줄(`SELECTED_BG`)은 크롬(`crate::theme`)과 한 벌이라 그대로 tokyonight 다 —
/// 사람이 답한 물음이 「SGR 30–37·90–97 을 어느 표로 삼을까」였고 그 넷은 그 칸이 아니다.
///
/// # 옛 표가 남긴 것 (지우지 않는다)
///
/// tokyonight 판에서 이 이슈가 잡은 결함이 둘이고 **부류가 다르다** — ⑴ `BR_MAGENTA` 가
/// `MAGENTA` 와 **같은 값**이라 SGR 35·95 가 한 색으로 붙던 것(CL 72731) ⑵ 밝은 노랑·
/// 밝은 청록이 제 기준색보다 **도리어 어둡던** 것(SGR 93·96 이 33·36 보다 어둡게 그려져
/// 강조가 뜻과 반대였다). xterm 표는 그 둘 다 구조적으로 없지만 **오라클은 그대로 산다**
/// — 그 둘은 팔레트 취향 밖의 성질이라 표를 갈아도 물음이 그대로다
/// (`the_sixteen_palette_names_are_sixteen_colors` ·
/// `the_bright_half_of_every_pair_is_actually_brighter`).
pub(crate) mod palette {
    use warpui::color::ColorU;

    const fn c(r: u8, g: u8, b: u8) -> ColorU {
        ColorU { r, g, b, a: 0xff }
    }

    pub const BG: ColorU = c(0x1a, 0x1b, 0x26);
    pub const FG: ColorU = c(0xc0, 0xca, 0xf5);
    /// 흐린 글자 — 꼬리줄(판 바닥 안내)·부속 칸·비활성 안내가 이 색이다.
    ///
    /// # 왜 밝혔나 (pytmux-372)
    ///
    /// 종전 값(`#565f89`)은 판 표면 대비 **2.51:1** 로 본문 기준(4.5:1)에 크게 못 미쳤다 —
    /// 그런데 이 색이 지고 있던 글자는 *장식*이 아니라 **그 판에서 무슨 키를 누르는지**
    /// 였다(꼬리줄). 안 읽히는 안내는 없는 안내다. 지금은 **5.19:1** 이다.
    ///
    /// 「흐리다」는 역할은 그대로다: 본문(`FG`)이 여전히 이 색보다 **2배 밝다**. 흐림은
    /// 절대 밝기가 아니라 본문과의 **차이**로 읽히므로, 둘의 간격이 남아 있으면 뜻이 산다.
    ///
    /// ⚠ 한 단 아래(`#7f88b3`)는 계산상 4.4996:1 로 **기준선에 걸터앉는다** — 부동소수 한
    /// 톨로 게이트가 갈리는 값을 토큰으로 두지 않는다(그 시험이 실제로 그렇게 떨어졌다).
    pub const DIM: ColorU = c(0x8b, 0x93, 0xbd);
    /// 활성 탭 배경. 데모 뷰가 쓰던 선택 배경과 같은 값이다.
    ///
    /// [`crate::theme::ACTIVE`] 와 **같은 값이라야 한다** — 두 표에 같은 뜻이 갈라져 있고
    /// (캔버스 쪽 이름 / 크롬 쪽 이름), 값이 갈리면 같은 «고른 줄»이 화면마다 달라진다.
    /// 사유·밝기 근거는 그쪽 문서에 있다(pytmux-372).
    pub const SELECTED_BG: ColorU = c(0x37, 0x46, 0x72);

    // ── xterm 표준 16색 (+ 어두운 바탕 보정 두 칸) ───────────────────────────
    //
    // 값은 xterm 의 기본 리소스(`XTerm-col.ad` / `charproc.c`)이고 X11 색이름을 함께
    // 적어 둔다 — 다음 사람이 「이게 정말 그 표인가」를 밖에서 대조할 수 있어야 한다.
    // ⛔ 여기에 **손으로** 값을 고르는 자리는 없다(위 모듈 문서). 아래 보정도 손이 아니라
    //    규칙이고, 그 규칙과 그 결과를 시험이 **다시 계산해서** 잰다.
    //
    // # 어두운 바탕 보정 (pytmux-412 ⓐ3 · 사람의 결정 2026-08-31)
    //
    // xterm 의 표는 **밝은 바탕**을 전제로 골라진 값이다(xterm 자신의 기본 배경이 흰색이다).
    // 그 표를 `BG`(`#1a1b26`) 위에 그대로 놓으면 어두운 칸이 바탕에 붙는다 — 제보가 짚은
    // 것이 `SGR 34`(1.82:1)다.
    //
    // ★ **연산은 xterm 자신의 선례다.** xterm 은 이 표에서 딱 한 칸을 손으로 밝혔다 —
    //   `color12` 는 X11 `blue` 가 아니라 `rgb:5c/5c/ff` 다(순 파랑은 어두운 바탕에서 안
    //   읽힌다). 그 값은 「흰색 쪽으로 t 만큼 섞는다」(`c' = c + (255-c)·t`)를 `color4` 에
    //   t≈0.36 으로 먹인 것과 **거의 같다**: 계산값 `#5c5cf4`, xterm 의 값 `#5c5cff`.
    //   ⇒ 우리가 새로 지어낸 연산이 아니라 **그 표가 이미 쓴 연산**이다.
    //
    // 규칙(=`the_dark_background_correction_is_the_rule_not_a_taste` 가 그대로 재계산한다):
    //
    //   ⑴ `BG` 대비가 **3:1**(WCAG 비문자·큰글자 기준) 미만인 칸만 손댄다.
    //      ⚠ 4.5:1 이 아니다 — 파랑은 짝(`color12`, 3.61:1)을 넘지 않고는 거기 못 간다.
    //   ⑵ **3.1:1** 을 넘기는 가장 작은 t(0.01 단위)를 쓴다. 필요한 만큼만 옮긴다.
    //      ⚠ 기준선이 3.0 인데 3.1 을 요구하는 것은 **여유 0.1 을 일부러 두는 것**이다 —
    //      3.0 에 딱 맞추면 부동소수 한 톨로 t 가 갈린다(실측: 같은 규칙을 파이썬과
    //      러스트로 계산했더니 0.04 와 0.03 으로 갈렸다). 이 저장소는 그 부류를 토큰으로
    //      두지 않기로 이미 정했다(`theme::DIM` 의 「한 단 아래는 4.4996 이라 걸터앉는다」).
    //   ⑶ 그러고도 **밝은 짝이 1.05배 이상 밝아야** 한다. 「밝은 쪽이 더 밝다」는 이 표의
    //      성질이고(`the_bright_half_of_every_pair_is_actually_brighter`), 재는 휘도는
    //      **그 오라클이 쓰는 것과 같은 식**이라야 한다(감마 인코딩된 값 그대로 · 선형화
    //      하지 않는다). ⛔ 한 물음을 두 술어로 물으면 규칙이 통과시킨 값을 오라클이
    //      떨어뜨린다 — 실제로 그렇게 한 번 붉었다.
    //   ⑷ ⑵·⑶ 을 **함께** 못 지키는 칸은 xterm 값 그대로 둔다. 못 고치는 것도 결과다.
    //
    // 손대는 칸은 **하나**다:
    //
    //   SGR 34  #0000ee  1.82 → t=0.32 → #5252f3  3.14   (짝 3.61 · 짝이 1.11배 밝다)
    //
    // ★ **왜 파랑만 되나** — 그 칸의 짝(`color12`)이 xterm 이 손수 밝힌 `#5c5cff` 라서다.
    //   xterm 이 어두운 바탕을 위해 낸 그 한 칸이, 지금 우리가 `color4` 를 밀어 올릴
    //   **자리를 만들어 준다.** 같은 연산 · 같은 이유 · 한 칸 아래.
    // ⛔ **SGR 31 은 보정 못 한다**(2.93:1 인데도). 짝인 `color9` 가 순수 `#ff0000` 이라
    //    t=0.05 만 돼도 31 이 91 을 앞지른다 — 읽히게 만들려다 표의 성질을 깨는 것이다.
    //    그 칸이 지던 「읽혀야 하는 붉은색」은 크롬으로 옮겼다([`crate::theme::ERROR`]).
    // ⚠ 나머지 열넷은 **한 톨도 안 움직였다** — 초록·노랑·청록은 이미 8~10:1 이고,
    //    검정(SGR 30)이 어두운 바탕에서 안 보이는 것은 어느 표에서나 그렇고 결함이 아니다.

    /// xterm `color0` = X11 `black`.
    pub const BLACK: ColorU = c(0x00, 0x00, 0x00);
    /// xterm `color1` = X11 `red3`.
    ///
    /// ⚠ **보정 못 한다**(pytmux-412 · 아래 §어두운 바탕 보정 ⑷). 창 바탕 위 2.93:1 로
    /// 기준선 아래인데, 짝인 `color9` 가 **순수 `#ff0000`** 이라 조금만 밝혀도 SGR 31 이
    /// 91 을 앞질러 「밝은 쪽이 더 밝다」가 깨진다(실측: t=0.05 에서 이미 뒤집힌다).
    /// ⇒ 읽혀야 하는 붉은색은 캔버스가 아니라 **크롬**의 것이고, 그 자리는
    /// [`crate::theme::ERROR`] 다(6.46:1 · pytmux-412 ⓑ1).
    pub const RED: ColorU = c(0xcd, 0x00, 0x00);
    /// xterm `color2` = X11 `green3`.
    pub const GREEN: ColorU = c(0x00, 0xcd, 0x00);
    /// xterm `color3` = X11 `yellow3`.
    pub const YELLOW: ColorU = c(0xcd, 0xcd, 0x00);
    /// xterm `color4` = X11 `blue2`(`#0000ee`) — **어두운 바탕 보정을 거친 값**(아래 참조).
    ///
    /// 이 칸이 이 이슈의 제보 그 자체다: `#0000ee` 는 창 바탕(`#1a1b26`) 위에서 **1.82:1**
    /// 이라 사실상 안 읽힌다. 파랑의 휘도 계수는 0.0722 로 초록(0.7152)의 **십분의 일**이라,
    /// 채도를 유지한 채 읽히는 칸까지 갈 수 없는 색은 열여섯 중 이것 하나다.
    pub const BLUE: ColorU = c(0x52, 0x52, 0xf3);
    /// xterm `color5` = X11 `magenta3`.
    pub const MAGENTA: ColorU = c(0xcd, 0x00, 0xcd);
    /// xterm `color6` = X11 `cyan3`.
    pub const CYAN: ColorU = c(0x00, 0xcd, 0xcd);
    /// xterm `color7` = X11 `gray90`.
    pub const WHITE: ColorU = c(0xe5, 0xe5, 0xe5);

    /// xterm `color8` = X11 `gray50`.
    pub const BR_BLACK: ColorU = c(0x7f, 0x7f, 0x7f);
    /// xterm `color9` = X11 `red`.
    pub const BR_RED: ColorU = c(0xff, 0x00, 0x00);
    /// xterm `color10` = X11 `green`.
    pub const BR_GREEN: ColorU = c(0x00, 0xff, 0x00);
    /// xterm `color11` = X11 `yellow`.
    pub const BR_YELLOW: ColorU = c(0xff, 0xff, 0x00);
    /// xterm `color12` = `rgb:5c/5c/ff`.
    ///
    /// ⚠ **X11 `blue` 가 아니다** — xterm 이 이 한 칸만 손으로 밝힌다(순 파랑은 어두운
    /// 바탕에서 안 읽힌다). 「밝은 쪽이 더 밝다」가 여기서 성립하는 것도 그 덕이다.
    pub const BR_BLUE: ColorU = c(0x5c, 0x5c, 0xff);
    /// xterm `color13` = X11 `magenta`.
    pub const BR_MAGENTA: ColorU = c(0xff, 0x00, 0xff);
    /// xterm `color14` = X11 `cyan`.
    pub const BR_CYAN: ColorU = c(0x00, 0xff, 0xff);
    /// xterm `color15` = X11 `white`.
    pub const BR_WHITE: ColorU = c(0xff, 0xff, 0xff);
}

/// 원격 탭 표시색. 파이썬 클라(`clientutil::REMOTE_PINK`)·TUI 와 같은 값이다 —
/// 세 클라가 원격을 서로 다른 색으로 그리면 그게 곧 혼란이다.
const REMOTE_PINK: ColorU = ColorU { r: 0xff, g: 0x5f, b: 0xd7, a: 0xff };

/// 같은 분홍의 **어두운 변형** — 비활성 패널 테두리용(정본 `REMOTE_PINK_DIM`).
///
/// 두 값인 이유: 활성/비활성이 한 색이면 원격 탭 안에서 **어느 패널이 키를 받는지**가
/// 사라진다. 로컬이 파랑 두 단(밝은/어두운)으로 그 일을 하는 것과 같은 자리다.
const REMOTE_PINK_DIM: ColorU = ColorU { r: 0xaf, g: 0x5f, b: 0x87, a: 0xff };

/// 비활성 탭의 **Claude 작업 완료** 알림색(정본 `TabBar.render_line` 의 `done_st`).
///
/// 값은 눈대중이 아니라 정본에서 그대로 옮겼다 — `theme_color("warning")` 이고 이 앱은
/// 테마를 안 바꾸므로 언제나 textual-dark 의 `#FEA62B` 다
/// (`clientutil::_THEME_FALLBACK["warning"]`). 두 클라가 같은 뜻을 다른 색으로 말하면
/// 그것이 곧 결함이라, [`REMOTE_PINK`] 와 같은 이유로 여기 값을 못박는다.
///
/// ⚠ **이미 뜻이 박힌 색과 겹치면 안 된다**: [`theme::FOCUS`](crate::theme::FOCUS) 는
/// "키보드가 이것을 고르고 있다", [`theme::INVERT_BG`](crate::theme::INVERT_BG) 는
/// "이 상태가 켜져 있다"다 — 셋 다 다른 값이라야 세 뜻이 한 그림이 되지 않는다
/// (`theme.rs` 가 그 둘을 굳이 갈라 둔 이유를 주석에 적어 뒀다).
///
/// 탭바 바탕([`theme::SURFACE`](crate::theme::SURFACE) `#16161e`) 위 대비는 **9.17:1**
/// 이다(WCAG 상대휘도 · `an_amber_tab_label_is_readable_on_the_tab_strip` 가 잰다).
///
/// ★ **`esc` 모드 표식 칩도 같은 값을 쓴다**(pytmux-380 · [`mode_chip`](SessionView::mode_chip)).
/// 우연이 아니다 — 정본의 `accent` 와 `warning` 이 textual-dark 에서 **같은 색**이고, 이
/// 앱에서 그 둘은 한 역할(「알림·주의」)이다. 그래서 상수를 둘로 늘리지 않고 이 하나를
/// 두 자리가 쓴다 — 값이 하나인데 이름이 둘이면 다음 사람이 한쪽만 고친다.
const CLAUDE_DONE_AMBER: ColorU = ColorU { r: 0xfe, g: 0xa6, b: 0x2b, a: 0xff };


/// 한/영 배지를 다시 묻는 주기. **정본과 같은 값**이다(`plugins/ime-indicator/__init__.py`
/// 의 `si(0.05, …)`).
///
/// # 왜 0.3초가 아닌가 (pytmux-378)
///
/// 종전 값은 300ms 였고 주석의 근거는 *"창 밖 프로세스(입력기 창)에 메시지를 보내는 일이라
/// 30Hz 로 두드릴 일이 아니고, 사람이 한/영을 바꾼 것을 0.3초 안에 보면 충분하다"* 였다.
/// 앞 절반은 여전히 옳지만(30Hz 로 두드리지 않는다 — 20Hz 다) **뒤 절반이 제보로
/// 반증됐다**: 정본을 옆에 두고 쓰면 6배 차이가 눈에 걸린다(사용자 2026-08-23).
///
/// 그 걱정이 값을 과하게 만든 것이지 걱정 자체가 틀린 것은 아니었다 — 그런데 **정본이 같은
/// 창 밖 질의를 50ms 로 이미 하고 있고**(그쪽은 ctypes 왕복이라 더 비싸다) 문제가 없다.
/// 그래서 「우리가 정한 값」이 아니라 **정본의 값**을 쓴다: 이 배지는 두 클라가 같은 사실을
/// 같은 속도로 말해야 하는 자리이고, 값이 갈리면 그 갈림이 곧 제보가 된다.
/// 두 클라가 갈리지 않는지는 `tests/test_plugin_ime_indicator.py` 가 두 파일을 대조해 잰다.
const IME_PERIOD: std::time::Duration = std::time::Duration::from_millis(50);

/// 이 뷰가 [`BorderTint`](base::tint::BorderTint) 를 그리는 색.
///
/// `Local` 은 `None` 이다 — **캔버스가 이미 칠한 색을 그대로 둔다**는 뜻이고, 그 색에는
/// 서버의 활성/비활성 판정이 이미 들어 있다.
///
/// degraded 는 활성·비활성이 **같은 빨강**이다: 정본도 `error` 한 색으로 칠하고 굵기만
/// 나눈다(선에는 굵기가 없다). 그리고 그 순간 중요한 것은 "어느 패널이 활성인가"가
/// 아니라 "끊기고 있다"는 사실이다.
fn tint_color(tint: base::tint::BorderTint, active: bool) -> Option<ColorU> {
    use base::tint::BorderTint;
    match tint {
        BorderTint::Local => None,
        BorderTint::Remote => Some(if active { REMOTE_PINK } else { REMOTE_PINK_DIM }),
        // 끊긴 패널 테두리는 **크롬의 의미색**이다(pytmux-412 ⓑ1) — SGR 표가 아니다.
        BorderTint::Degraded => Some(theme::ERROR),
    }
}

/// proto 색 → 이 뷰의 색. 셀 스타일과 상태줄이 **같은 표**를 쓴다.
///
/// ⚠ **그 「같은 표」가 값을 치른다**(pytmux-412). 열여섯 칸이 xterm 표준으로 갈리면서
/// 크롬의 의미색(심각도·블록·툴 상태)도 함께 갈렸는데, 그 자리들은 **SGR 30–37·90–97 이
/// 아니라** 우리가 읽히라고 고른 값이다 — 어두운 바탕 위에서 `RED` 가 6.46:1 → 2.93:1 로
/// 떨어졌고 그것을 재는 오라클이 없어 전량 초록으로 지나갔다. 갈래(크롬에 자기 상수를
/// 주나 · 이대로 두나)는 사람이 고를 일이라 pytmux-412 에 세워 뒀다.
fn to_gui_color(color: &CellColor) -> ColorU {
    match *color {
        CellColor::Rgb { r, g, b } => ColorU { r, g, b, a: 0xff },
        CellColor::Named(n) => named(n),
    }
}

fn named(color: NamedColor) -> ColorU {
    match color {
        NamedColor::Black => palette::BLACK,
        NamedColor::Red => palette::RED,
        NamedColor::Green => palette::GREEN,
        NamedColor::Yellow => palette::YELLOW,
        NamedColor::Blue => palette::BLUE,
        NamedColor::Magenta => palette::MAGENTA,
        NamedColor::Cyan => palette::CYAN,
        NamedColor::White => palette::WHITE,
        NamedColor::BrightBlack => palette::BR_BLACK,
        NamedColor::BrightRed => palette::BR_RED,
        NamedColor::BrightGreen => palette::BR_GREEN,
        NamedColor::BrightYellow => palette::BR_YELLOW,
        NamedColor::BrightBlue => palette::BR_BLUE,
        NamedColor::BrightMagenta => palette::BR_MAGENTA,
        NamedColor::BrightCyan => palette::BR_CYAN,
        NamedColor::BrightWhite => palette::BR_WHITE,
    }
}

fn convert(color: CellColor) -> ColorU {
    match color {
        CellColor::Rgb { r, g, b } => ColorU { r, g, b, a: 0xff },
        CellColor::Named(n) => named(n),
    }
}

/// 한 런의 전경·배경. **반전은 여기서 푼다** — 아래 렌더는 색을 그대로 쓴다.
///
/// 반전을 렌더까지 끌고 가면 배경을 칠할지 말지를 두 군데서 판단하게 되고, 그 둘이
/// 어긋나는 순간 커서·선택 강조가 안 보인다(터미널에서 반전은 장식이 아니라 **신호**다).
fn colors(style: &CellStyle) -> (ColorU, Option<ColorU>) {
    let fg = style.fg.map_or(palette::FG, convert);
    let bg = style.bg.map(convert);
    if style.reverse {
        (bg.unwrap_or(palette::BG), Some(fg))
    } else {
        (fg, bg)
    }
}

/// 한 런이 요구하는 **글꼴 변형** — 굵게(SGR 1)·기울임(SGR 3) (pytmux-133).
///
/// # 왜 오버레이가 아닌가
///
/// 밑줄·취소선은 칸을 가로지르는 **선**이라 오버레이가 그리지만, 이 둘은 글자 모양
/// 자체가 다른 것이라 그릴 수 있는 곳이 `Text` 뿐이다.
///
/// # 없는 변형을 **합성하지 않는다** (사용자 결정 2026-08-04)
///
/// `select_font` 는 그 family 안에서 **가장 가까운 얼굴**을 고른다 — 굵은 얼굴이 없는
/// family 면 보통 얼굴이 그대로 돌아온다. 그래서 여기서 할 일은 요구를 얹는 것뿐이고,
/// 없으면 화면은 종전과 같다. 가짜 볼드(획을 겹쳐 그리기)는 **안 만든다**: 고정폭
/// 격자에서 넓어진 글자는 옆 칸을 침범하고, 그러면 잃는 것(자리)이 얻는 것(굵기)보다
/// 크다. 대신 `parity.rs` 에 갈림으로 선언한다.
///
/// # 왜 폭이 안 틀어지나
///
/// 고정폭 family 는 **모든 얼굴이 같은 전진폭**을 갖는다(Consolas·Menlo·DejaVu Sans
/// Mono 실측). `render_row` 가 ASCII 조각의 폭을 안 못박고 자연폭에 맡기는 것이 그
/// 전제 위에 서 있으므로, 이 값이 그 전제를 깨면 줄이 통째로 밀린다 — 변형 글꼴을
/// 늘릴 때 확인할 자리다.
fn font_properties(style: &CellStyle) -> Properties {
    Properties {
        weight: if style.bold { Weight::Bold } else { Weight::Normal },
        style: if style.italic { FontStyle::Italic } else { FontStyle::Normal },
    }
}

/// 보조 글꼴로 갈 조각에서 **없는 얼굴을 요구하지 않도록** 변형을 깎는다(pytmux-133).
///
/// # 왜 필요한가 — 라이브가 잡은 것
///
/// 기울임을 그냥 걸었더니 한글이 **두부(▯)** 로 떨어졌다(실측 2026-08-04, 캡처
/// `2026-08-04-text-attrs.md`). 한글은 고정폭 family 에 없어서 보조 글꼴이 그리는데
/// (`mono_font::FALLBACK_CANDIDATES`), 그 이름들(`Malgun Gothic`·`Apple SD Gothic Neo`·
/// `Noto Sans CJK KR`)에는 **기울임 얼굴이 없다**. 그러면 글리프를 못 찾아 두부가 된다 —
/// 즉 "안 기울어진다"가 아니라 **글자가 통째로 사라진다**. 굵게는 그 셋에 얼굴이 있어
/// 멀쩡히 그려진다(같은 캡처에서 확인).
///
/// # 왜 ASCII 인가 (이 판정의 한계)
///
/// 정확한 물음은 *"이 글자를 그릴 얼굴에 그 변형이 있나"* 인데, 그건 글리프를 실제로
/// 찾아봐야 안다. 여기서 쓰는 것은 그 대용이다 — `render_row` 가 폭을 못박을지 정할 때
/// 이미 쓰는 바로 그 판정(ASCII 냐)이고, "ASCII 가 아니면 보조 글꼴로 간다"는 뜻이다.
/// 사용자 결정(2026-08-04)이 *"실제 변형 글꼴만, 없으면 안 그린다"* 이므로 **덜 그리는
/// 쪽으로 틀린다** — 기울임 얼굴이 있는 비ASCII 글꼴이 나중에 들어오면 그때 이 자리를
/// 넓힌다(두부보다 안 기울어진 글자가 낫다).
fn fallback_safe(props: Properties, boxed: bool) -> Properties {
    if boxed {
        Properties { style: FontStyle::Normal, ..props }
    } else {
        props
    }
}

/// Claude 트랜스크립트를 들여다보는 간격.
///
/// 트랜스크립트는 **서버 메시지와 무관하게 자란다** — Claude 가 밖에서 덧붙이는 파일이라
/// 알려 주는 이벤트가 없다. 그래서 프레임이 없어도 들여다봐야 하는데, 매번 만지면 펌프
/// 주기가 그것만 하게 된다(TUI 의 `CLAUDE_POLL` 과 같은 값·같은 이유). 수정 시각이
/// 그대로면 파싱까지 가지도 않는다.
const CLAUDE_POLL: Duration = Duration::from_millis(400);

/// 휠 한 칸이 움직이는 줄 수. 파이썬 클라·TUI 와 같은 값이다 — 세 클라의 감각이 갈리면
/// 같은 손짓이 화면마다 다르게 움직인다.
const WHEEL_LINES: i32 = 3;

/// 하단 한 줄에 뜨는 한 마디(§10-21ⓝ·ⓦ).
///
/// `at` 이 `None` 이면 **시한이 없다** — 끊김처럼 "지금 상태"인 줄이 그렇다.
#[derive(Debug, Clone)]
struct Flash {
    text: String,
    severity: Severity,
    at: Option<Instant>,
}

impl Flash {
    /// 시한이 없나(끊김처럼 "지금 상태"인 줄).
    #[cfg(test)]
    pub(crate) fn has_no_deadline(&self) -> bool {
        self.at.is_none()
    }
}

/// 판 안 클릭 자리의 **번호를 한 줄로** 나눠 주는 것(§10-21ⓣ).
///
/// hover 상태 풀(`panel_click_states`)의 색인은 **그 화면 안에서 유일**해야 한다. 종전엔
/// 자리가 한 종류(사이드바 탭)뿐이라 그 자리의 색인을 그대로 썼는데, 설정 값칸이 눌리게
/// 되면서 한 화면에 자리가 여러 갈래(줄 이름·토글·낱말·화살표)가 됐다. 갈래마다 0 부터
/// 세면 **남의 자리의 hover 가 내 자리에 붙는다** — 눈에는 "엉뚱한 데가 밝아진다"로 보인다.
#[derive(Debug, Default)]
struct PanelIds(usize);

impl PanelIds {
    /// 다음 번호를 뗀다. 그리는 차례가 곧 번호라, 배치가 바뀌면 번호도 함께 움직인다
    /// (그래서 화면이 바뀔 때 풀을 버린다 — `SessionView::handle_key` 의 hover 버리기).
    fn next(&mut self) -> usize {
        let id = self.0;
        self.0 += 1;
        id
    }
}

pub struct SessionView {
    /// 돌고 있는 셸 명령의 결과가 도착하는 자리(`run-shell`·`if-shell`).
    ///
    /// **스레드로 뺀 이유**: 상한이 15초다. 이벤트 루프에서 동기로 부르면 그동안 화면이
    /// 통째로 멈춘다(클립보드를 스레드로 뺀 것과 같은 이유 — 파이썬 클라는 여기서
    /// 막히지만 그건 따라 할 이유가 없다).
    shell_result: std::sync::Arc<std::sync::Mutex<Option<ShellOutcome>>>,
    /// 원격 탭에 그림을 올리는 `scp` 의 결과가 도착하는 자리(pytmux-159).
    ///
    /// **스레드로 뺀 이유**는 위와 같다 — 상한이 30초라 이벤트 루프에서 부르면 창이 그동안
    /// 통째로 멈춘다(정본도 워커로 뺀다). 붙여넣기는 그 결과가 온 **뒤에야** 뜻이 생긴다:
    /// 어느 경로를 붙일지가 성공 여부로 갈리기 때문이다.
    paste_result: std::sync::Arc<std::sync::Mutex<Option<RemoteImage>>>,
    /// 사용자 설정(패리티 G5·G6b). **한 번 읽어 들고 있는다** — 설정 화면에서 바꾸면
    /// 여기를 고치고 파일에 쓴다(그래야 이번 판에 바로 먹는다).
    config: base::Config,
    state: SessionState,
    /// 서버 연결. **뷰가 들고 있다** — TUI 는 이벤트 루프가 쥐지만, GUI 에는 그 자리에
    /// 해당하는 루프가 없고 주기 작업이 `ctx.spawn` 으로 뷰에 되돌아온다(`main.rs`).
    link: ServerLink,
    /// 연결이 끝났을 때의 사유.
    ended: Option<String>,
    /// 지금 키가 패널의 것인가 클라의 것인가. 전이 규칙은 core 가 갖는다
    /// ([`ModeState`]) — 뷰마다 적으면 한쪽에서만 모드가 안 풀린다.
    mode: ModeState,
    /// 아직 서버로 못 보낸 것들. `pump` 가 꺼내 보낸다.
    ///
    /// **프레임 종류가 달라도 한 큐다** — 사용자가 한 순서 그대로 나가야 한다.
    /// 종류별로 나누면 붙여넣기와 Enter 의 순서가 뒤집혀 빈 명령이 실행된다
    /// (TUI 가 실제로 밟은 결함 — 설계문서 §7 P7 슬라이스 2).
    pending: Vec<Outgoing>,
    /// RTT ping 발신기(G9u) — `tick_clock` 이 0.5초에 한 번 ping 을 큐에 넣는다.
    pinger: proto::rtt::Pinger,
    /// 캔버스를 덮고 있는 화면들(패리티 G2). **규칙은 core 가 갖는다** — 두 뷰가 각자
    /// bool 을 들고 있던 자리를 스택 하나로 합쳤다.
    screens: Screens,
    /// 창을 닫아 달라는 요청이 들어왔는가.
    /// 캔버스 **밖**(탭바·상태줄)으로 나간 포커스. 뜻은 core 가 든다.
    chrome: base::Chrome,
    /// 명령 인자 이력(파이썬 arghist 와 같은 파일 — proto 모듈 문서 · TUI 와 한 벌).
    arghist: proto::arghist::ArgHist,
    /// 크롬(탭·`[+]`·`[x]`·배지) Hoverable 의 마우스 상태 — 프레임을 넘어 살아야
    /// 눌림/호버 추적이 된다. 자리 수는 프레임마다 다르므로(탭 수) 색인으로 키운다.
    /// `RefCell` 인 이유는 TUI 의 `chrome_zones` 와 같다(렌더는 `&self`).
    chrome_click_states: std::cell::RefCell<Vec<MouseStateHandle>>,
    /// 떠 있는 **판 안** 위젯(설정 탭·팔레트 탭·메뉴 줄·확인 버튼)의 마우스 상태.
    ///
    /// 크롬 풀과 갈라 두는 이유: 색인이 **렌더 순서**라, 한 풀을 쓰면 탭 수가 바뀔 때
    /// 판 안 위젯의 자리가 통째로 밀린다(그 프레임의 hover 가 엉뚱한 줄에 붙는다).
    panel_click_states: std::cell::RefCell<Vec<MouseStateHandle>>,
    /// 창 버튼(최소화·최대화·닫기)의 마우스 상태(`pytmux-1`). **또 갈라 둔다** — 이
    /// 자리 수는 OS 가 정하고(맥은 0) 탭 수와 무관한데, 크롬 풀에 얹으면 탭이 하나
    /// 늘거나 줄 때마다 창 버튼의 hover 가 엉뚱한 칸으로 옮겨 간다.
    titlebar_click_states: std::cell::RefCell<Vec<MouseStateHandle>>,
    /// 상태줄 세션 이름(`#S`) 자리와 편집 중 글자칸들의 마우스 상태(pytmux-3).
    ///
    /// **또 갈라 둔다** — 이 자리 수는 **이름의 글자 수**를 따라 프레임마다 변한다.
    /// 크롬 풀에 얹으면 한 글자를 칠 때마다 뒤따르는 배지들의 색인이 통째로 밀려,
    /// hover 가 엉뚱한 배지에 붙는다(창 버튼을 가른 것과 같은 사정).
    status_click_states: std::cell::RefCell<Vec<MouseStateHandle>>,
    /// 세션 이름을 **그 자리에서** 고치는 중인가(pytmux-3 제보). 판을 안 띄우므로
    /// [`Screens`] 스택에 자리가 없다 — 값도 키 라우팅도 뷰가 직접 든다.
    ///
    /// ⛔ 이것이 `Some` 인 동안에는 **모든 키가 편집칸의 것**이다. 한 갈래라도 빠뜨리면
    /// 그 키가 패널로 새서 편집 중에 셸이 글자를 받는다(`session_edit_key` 참조).
    session_edit: Option<base::SessionEdit>,
    /// 드래그 중인 탭(리스트 위치) — 판정은 core `drag_drop`(G9w · TUI 와 한 벌).
    tab_drag: Option<usize>,
    /// 드래그 중 가리키는 드롭 대상 탭(강조용).
    tab_drag_over: Option<usize>,
    /// 상태줄을 마지막으로 다시 그린 시각(`status-interval`).
    last_status: Instant,
    /// 커서 깜빡임을 마지막으로 뒤집은 시각(`cursor-blink-interval`).
    last_blink: Instant,
    /// 지금 깜빡임이 **보이는 반주기**인가(`pytmux/pytmux-161`).
    ///
    /// ⛔ 깜빡임을 껐을 때도 이 값은 `true` 여야 한다 — 「안 보임」에서 껐다고 커서가
    /// 영영 사라지면 되돌릴 입구가 없다(`tick_cursor_blink` 가 그 자리를 지킨다).
    blink_on: bool,
    /// 이 뷰가 그린 프레임 수와 그 **간격**(`debug-stats` · pytmux-457).
    ///
    /// ⛔ `render(&self)` 라 `&mut self` 가 없다 — 그래서 `Cell`/`RefCell` 이다.
    /// 이 둘을 `pump` 에서 세지 않는 이유: pump 는 33ms 타이머라 **그리지 않은 틱도
    /// 센다**. 재려는 것은 「그렸나」이고, 그 사실을 아는 자리는 `render` 하나다.
    frames: std::cell::Cell<u64>,
    /// 직전 `render` 시각 — 다음 렌더와의 차가 한 표본이다.
    last_render: std::cell::Cell<Option<Instant>>,
    /// 최근 프레임 간격(ms · 최대 [`base::diag::FRAME_WINDOW`] 개).
    frame_ms: std::cell::RefCell<std::collections::VecDeque<f64>>,
    /// 마지막으로 그린 캔버스의 칸 수 — 한 프레임에 실제로 한 그리기 일의 크기다.
    painted_cells: std::cell::Cell<usize>,
    /// 입력기 배지(`한`/`EN`) — OS 에 물어본 값. 모르면 `None`(배지를 안 그린다).
    ime_badge: Option<&'static str>,
    /// **조합 중인** 글자(`ㅎ`→`하`→`한`). 비어 있으면 조합 중이 아니다.
    ///
    /// ⛔ 이것을 패널로 보내지 않는다 — 보내면 셸이 자모를 받아 `치명ㄷ` 부류가 된다
    /// (`ime.rs` 머리말이 그 사고를 적어 뒀다). 확정될 때 상류가 `TypedCharacters` 로
    /// 따로 주고, 그것만 평소 경로로 나간다. 여기 있는 동안은 **그림일 뿐**이다.
    preedit: String,
    /// 입력기를 마지막으로 물어본 시각. **매 프레임 묻지 않는다** — 창 밖 프로세스에
    /// 메시지를 보내는 일이라, 30Hz 로 두드리면 그만큼 남의 입력기 창을 괴롭힌다.
    last_ime: Instant,
    /// 「배지는 내가 그린다」를 이 연결에서 이미 알렸나(pytmux-392).
    ///
    /// 연결마다 다시 알린다 — 사실은 `ClientConn.plugin_state` 에 매달려 **연결이 끊기면
    /// 함께 사라진다**(`servercmd::_cmd_client_fact` 문서). 안 다시 알리면 재접속 뒤
    /// 서버가 글자 런을 다시 보내 그림과 겹친다.
    told_ime_render: bool,
    /// 이벤트 훅의 **가장자리**를 재는 자(패리티 G8u). 상태가 아니라 전이에 발화한다.
    hook_watch: base::hooks::HookWatcher,
    /// 드라이런 회신을 기다리는 재시작(있으면 그 종류).
    ///
    /// 왜 상태로 두나: 점검은 **왕복**이다 — 무엇을 하려던 것인지 잊으면 회신이 와도 쓸
    /// 데가 없다(입력 화면이 물음을 기억해 두는 것과 같은 자리).
    pending_restart: Option<base::restart::Kind>,
    /// 마지막으로 세운 창 제목(설정 `set-titles`). 같은 값을 다시 안 세우려는 것이다.
    last_title: Option<String>,
    /// 마지막으로 창에게 말한 **끌 수 있는 띠**의 높이(`refresh_titlebar_band`).
    /// `last_title` 과 같은 모양 — 값이 바뀐 때만 창을 두드린다.
    titlebar_band: Option<f32>,
    /// 이번 펌프에서 창에게 시킬 일(`pytmux-1`). `fullscreen_requested` 와 같은 모양으로
    /// 한 프레임 실어 나른다 — 액션 처리에는 창이 없다.
    ///
    /// **닫기는 여기 안 온다** — 그건 `quit_requested` 와 같은 길을 탄다(뒤처리가 이미
    /// 그쪽 한 곳에 있다). 그래서 이 값에 남는 것은 최소화·최대화 둘뿐이다.
    window_op: Option<titlebar::Button>,
    /// 서버에 마지막으로 알린 창 포커스(None=아직 안 알렸다 · pytmux-421).
    window_focus: Option<bool>,
    quit_requested: bool,
    /// 이번 펌프에서 창을 전체 화면으로 넣었다 뺄까(§10-21ⓘ3).
    ///
    /// 액션 처리(`apply_action`)에는 창이 없다 — `ViewContext` 를 쥔 자리에서 해야 해서
    /// `quit_requested` 와 같은 모양으로 한 프레임 실어 나른다. **상태를 드는 것이
    /// 아니다**(전체 화면인지의 진실은 창에 있다) — 이건 "해 달라"는 요청 한 번이다.
    fullscreen_requested: bool,
    /// 이번 펌프에서 **OS 클립보드를 읽어 붙여넣을까**(`paste-clipboard` · pytmux-159).
    ///
    /// `fullscreen_requested` 와 같은 모양·같은 이유다: 클립보드를 읽으려면 `ViewContext`
    /// 가 있어야 하는데 `apply_action` 에는 창이 없다. 팔레트·키 어느 입구로 들어오든
    /// 여기 한 칸에 모였다가 창을 쥔 자리에서 **한 함수**로 끝난다 — 입구가 둘이어도
    /// 경로가 둘이면 그 둘은 조용히 갈라진다.
    paste_requested: bool,

    // ── 마우스(P7) · 스플리터(§4.2) ─────────────────────────────────────────
    /// 지금 끌고 있는 분할 경계의 id. 놓으면 비운다.
    dragging: Option<i64>,
    /// 마우스가 올라와 있는 분할 경계의 id(N3 — 스플리터 바 강조·커서 모양).
    divider_hover: Option<i64>,
    /// 마우스가 올라와 있는 **뜻이 있는 범위**(§10-21ⓥ2·ⓧ2) — 링크·경로.
    ///
    /// 상태로 드는 이유: 강조를 그리는 것은 렌더고 커서 모양을 세우는 것은 이벤트라,
    /// 둘이 같은 값을 봐야 "밑줄은 그어졌는데 커서는 화살표"가 안 생긴다.
    span_hover: Option<proto::SpanHit>,
    /// 버튼은 눌렸는데 아직 안 움직인 자리(캔버스 좌표)와 그 아래 패널.
    ///
    /// 클릭과 드래그는 **놓을 때까지 구분되지 않는다** — 누른 순간에 정하려 하면 둘 중
    /// 하나를 못 하게 된다(파이썬 클라·TUI 도 같은 미결 상태를 둔다).
    press: Option<(i64, u16, u16)>,
    /// 끌고 있는 선택. 놓으면 비운다.
    selection: Option<Selection>,
    /// 블록 선택 모드에서 고른 블록 — **(패널 id, 목록 안의 자리)**(pytmux-18).
    ///
    /// # 왜 패널 id 를 같이 드나
    ///
    /// 블록 목록은 패널마다 따로다(`SessionState::blocks`). 자리만 들면 그 사이에 포커스가
    /// 옮겨졌을 때 **옆 패널의 같은 번호 블록**이 골라진 것처럼 보이고, 그 상태로 복사하면
    /// 화면에 강조된 것과 다른 글이 클립보드로 간다 — 조용한 오답이다. 드래그 선택이
    /// `Selection::pane` 을 드는 것과 같은 이유다.
    ///
    /// 자리(index)를 절대 행이 아니라 **목록 인덱스**로 드는 이유: 마지막 블록은 아직
    /// 자라는 중이라 끝 행이 매 프레임 바뀐다. 인덱스는 새 블록이 붙어도 안 움직인다
    /// (목록이 상한에서 잘려 앞이 밀리면 어긋나는데, 그때는 강조가 한 칸 밀릴 뿐
    /// 범위 계산은 그 자리의 블록으로 다시 한다).
    block_pick: Option<(i64, usize)>,
    /// 지금 **앱에게 넘기는 중인** 드래그(패널 id, 버튼). Shift+드래그로만 선다.
    ///
    /// 시작한 패널을 붙잡아 두는 이유는 선택과 같다 — 포인터가 이웃 패널로 넘어가도
    /// press 를 받은 앱이 release 도 받아야 한다. 안 그러면 그 앱은 버튼이 영원히
    /// 눌린 줄 안다(TUI 와 같은 상태·같은 이유).
    mouse_fwd: Option<(i64, u8)>,
    /// 복사 결과 한 마디. **줄을 늘리지 않고** 요약 머리줄 끝에 붙는다.
    /// 하단 한 줄에 뜨는 **지나가는 말**(§10-21ⓝ·ⓦ) — 복사 결과와 서버 오류가 여기
    /// 한 자리로 모인다.
    ///
    /// 종전에는 복사 결과가 **맨 위 머리줄** 끝에 붙었다. 그 자리를 고른 근거는 "아래
    /// 구역은 블록도 Claude 도 없으면 안 그려져 여기 두면 안 보일 때가 있다"였는데,
    /// ⓓ 로 그 구역이 화면에서 빠지고 **늘 그려지는 하단 한 줄**이 생기면서 그 근거가
    /// 사라졌다. 제보도 "서버 오류가 나타나는 그 자리에 나타나야 한다"로 확정됐다.
    flash: Option<Flash>,
    /// `Ctrl` 을 쥔 채 탭 스위처를 돌고 있나(§10-21ⓕ2). 뗌이 확정이다.
    alt_tab: bool,
    /// 마지막 복사 요청의 **패널 폭과 시작 열**(정본 `_copy_unwrap_geom`).
    ///
    /// 왜 기억해 두나: 접힘을 되돌리려면 그 글이 **몇 칸짜리 판에서 접혔는지**를 알아야
    /// 하는데, 회신(`Selection`)은 글자만 싣는다. 요청을 보내는 자리에서 재 둔다.
    copy_geom: Option<(usize, usize)>,
    /// 첫 캔버스를 이미 알렸는가(진단 한 줄을 한 번만 남기려고).
    drew: bool,
    /// 서버에 알린 격자 크기. **바뀌었을 때만** 다시 알린다(TUI 와 같은 정본).
    size: SizeReporter,
    /// 마지막으로 **잰** 칸 크기(픽셀). 캔버스가 격자를 잡을 때 쓴다(§10-21ⓙ).
    ///
    /// # 왜 잰 값인가 · 왜 `Cell` 인가
    ///
    /// 칸너비는 글꼴과 배율이 정하므로 계산으로는 못 얻는다. 원천은 마우스 셀 산수·
    /// 스플리터 오버레이와 **같은 자리표**(`CELL_PROBE`)이고, 그것을 읽는 자리가
    /// [`report_size`](Self::report_size)(`&mut self`)인데 쓰는 자리는 `render`(`&self`)라
    /// 안쪽 가변성이 필요하다.
    ///
    /// 값은 **한 프레임 낡았다**. 그래도 맞는 이유: 이 값이 바뀌는 것은 글꼴 배율이나
    /// 창 배율이 바뀔 때뿐이고, 그때는 다음 프레임에 곧 따라온다(첫 프레임에는 아직
    /// 없어 종전 배치로 그린다).
    cell_px: std::cell::Cell<Option<(f32, f32)>>,
    /// 캔버스 왼쪽·위 여백과 창 크기(px) — 전부 **잰 값**이다(`report_size`).
    ///
    /// `cell_px` 만으로는 판을 캔버스 **안 어느 칸**에 세울 수 없다: 칸 크기는 알아도
    /// 캔버스가 창의 어디서 시작하는지를 모르기 때문이다. 작성창을 커서 줄에 세우는
    /// 자리(pytmux-370 · [`Anchor::AtPaneCursor`])가 그 값을 처음으로 요구했다.
    canvas_px: std::cell::Cell<Option<CanvasBox>>,
    /// 카운트다운을 마지막으로 그린 **초**(pytmux-371 ④). 초가 바뀔 때만 다시 그린다.
    countdown_secs: i64,
    font: FamilyId,
    /// 지금 **깔려 있는** 글꼴 이름(설정 `font-family` 의 값 그대로 · 빈 값 = 자동).
    ///
    /// 설정이 이것과 갈리면 [`pump`](Self::pump) 가 다시 깐다(pytmux-408 ④) — 설정을
    /// 바꾸고 **재시작해야 보인다면 그 설정 화면은 반쪽이다**(`flip_config` 가 같은
    /// 이유로 그 자리에서 반영한다). 글꼴 등록에는 `ctx` 가 필요해 대답을 받는 자리
    /// (`apply_answer`)에서는 못 하고, `ctx` 를 쥔 프레임 진입에서 화해시킨다.
    font_want: String,
    /// 크롬(탭·팝업 틀·상태줄) 글꼴 — 가변폭. 못 찾으면 `font`(고정폭) 그대로다.
    /// 캔버스는 항상 `font` 로 그린다(격자는 고정폭이 계약이다).
    ui_font: FamilyId,

    // ── Claude 구역(P5) ─────────────────────────────────────────────────────
    /// 지금 보이는 패널의 Claude 항목.
    claude: Vec<ClaudeItem>,
    /// 걸려 있는 권한 모드. 머리줄에 한 낱말로 붙는다.
    claude_mode: Option<String>,
    /// 이 구역이 펼쳐져 있나(§10-20ⓔ). 기본은 접힘.
    ///
    /// **어디에도 저장하지 않는다.** 후보가 둘이었다 — 설정 파일(영속)과 여기(연결 수명).
    /// 설정을 고르면 정본과 공유하는 설정 표면에 **정본에 없는 칸**이 생기고
    /// (`check_fixtures`·표면 원장이 먼저 운다), 그 칸의 뜻을 정본이 영영 모른다.
    /// 이 구역 자체가 우리 것뿐이라(정본에는 없다) 상태도 우리 창의 수명에 묶는다.
    /// 이 머신의 트랜스크립트 파일을 보는 눈(로컬 패널용).
    watcher: Watcher,
    /// 상류가 실어 보낸 원문 꼬리(원격 패널용).
    remote: RemoteTranscripts,
    /// 마지막으로 파일을 들여다본 시각.
    last_look: Instant,
    /// 전역 검색 회신을 기다리고 있나(pytmux-27 · 정본 `_want_search_all` 과 같은
    /// 게이트). `search_all` 을 보낼 때 켜고, `search_results` 가 오면(기다리던
    /// 회신이든 아니든) 끈다 — 뒤늦거나 남의 요청의 회신을 결과 판으로 착각해
    /// 열지 않기 위해서다.
    awaiting_search_all: bool,
}

/// 팔레트 한 줄의 정체 — 코어 표의 자리이거나 플러그인이 기여한 자리다(설계 Tier A).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PaletteHit {
    /// `base::PALETTE` 의 자리.
    Core(usize),
    /// `state.plugin_surface().commands` 의 자리(서버가 준 런타임 목록).
    Plugin(usize),
}

/// 팔레트에서 고른 줄이 일으키는 일.
#[derive(Debug, Clone, PartialEq)]
enum PalettePick {
    /// 코어 액션 — **액션으로 되돌려 같은 길**을 태운다(확인 화면 등을 건너뛰지 않게).
    Action(Action),
    /// 플러그인 명령 이름. 실행 화면은 아직 없다(설계 P4) — 조용히 버리지 않는다.
    Plugin(String),
}

impl PalettePick {
    /// 이 줄의 **명령 이름**(팔레트 표에 적힌 그대로 — 옵션까지 품는다).
    ///
    /// 인자를 이어 친 줄을 core 의 한 줄 해석기에 넘길 때 쓴다(pytmux-7). 친 글자가
    /// 아니라 **고른 줄**에서 이름을 가져와야 부분만 친 경우(`rem host1`)도 뜻이 산다.
    fn name(&self) -> Option<String> {
        match self {
            PalettePick::Action(action) => base::PALETTE
                .iter()
                .find(|e| e.action == *action)
                .map(|e| e.name.to_owned()),
            PalettePick::Plugin(name) => Some(name.clone()),
        }
    }
}

/// 캔버스가 창의 **어디에 얼마만큼** 그려졌나(px). 전부 렌더가 남긴 자리표에서 **잰 값**
/// 이다 — 이 파일의 규율 그대로, 계산하면 렌더와 어긋난다.
#[derive(Debug, Clone, Copy)]
struct CanvasBox {
    /// 캔버스 첫 칸의 왼쪽 x.
    left: f32,
    /// 캔버스 첫 줄의 위 y.
    top: f32,
    /// 창 너비(지금은 안 쓰지만 자리표 한 벌로 묶어 둔다 — 따로 재면 두 값이 갈린다).
    #[allow(dead_code)]
    win_w: f32,
    /// 창 높이. 바닥에서 위로 띄우는 여백을 여기서 뺀다.
    win_h: f32,
}

impl SessionView {
    pub fn new(link: ServerLink, ctx: &mut ViewContext<Self>) -> Self {
        // 고정폭 + 보조 글꼴. 규칙은 `mono_font` 한 곳이다(데모 뷰와 같은 것을 부른다).
        // 크롬 글꼴(가변폭)은 같은 캐시에 더 깐다 — 없으면 고정폭으로 그린다.
        // ★ **설정을 여기서 한 번 읽는다**(pytmux-408). 글꼴은 창이 서기 전에 정해지는데
        //   `with_fonts` 가 설정을 읽는 자리는 그보다 뒤다 — 그 차례 때문에 `font-family`
        //   가 첫 프레임에 안 먹으면 「적었는데 다음 기동에나 먹는다」가 된다.
        //   ⚠ `Config::load` 는 파일 한 번 읽기라 두 번 불러도 값이 같다(순수).
        let want = base::Config::load().font_family;
        let (font, ui_font, missed) = warpui::fonts::Cache::handle(ctx).update(ctx, |cache, _| {
            let (mono, missed) = mono_font::install_preferred(cache, &want);
            (mono, theme::install_ui(cache).unwrap_or(mono), missed)
        });
        // 키를 받으려면 포커스가 있어야 한다. 없으면 이벤트가 이 뷰까지 오지 않고,
        // 증상은 "창은 떴는데 아무 키도 안 먹는다"다(데모 뷰가 이미 같은 줄을 갖는다).
        ctx.focus_self();
        let mut view = Self::with_fonts(link, font, ui_font);
        view.font_want = want;
        // ⛔ **조용히 넘어가지 않는다**(pytmux-408 ③). 「적었는데 아무 일도 안 일어난다」가
        //    이 부류에서 제일 나쁜 결과다 — 사용자는 자기 오타인지 앱이 무시한 것인지 못
        //    가린다. 떨어진 것은 맞지만 **무엇이 없었는지**는 말한다.
        if let Some(want) = missed {
            view.state.note_notice(base::i18n::tf(
                "글꼴 «{name}» 이 이 상자에 없다 — 기본 글꼴로 그린다",
                &[("name", &want)],
            ));
        }
        view
    }

    /// 창 문맥 **없이** 세운다 — 글꼴만 밖에서 받는다.
    ///
    /// # 왜 갈랐나
    ///
    /// [`new`](Self::new) 가 `ViewContext` 를 요구하는 바람에 **테스트가 이 뷰를 만들 수
    /// 없었다.** 그래서 GUI 에는 TUI 의 `outgoing_after_*` 에 해당하는 큐 오라클이 없었고,
    /// 2026-07-29 G8p 에서 `pump()` 배선이 통째로 빠진 것을 워크스페이스 1287개 테스트 중
    /// 어느 것도 못 잡았다(라이브 스크린샷이 잡았다).
    ///
    /// 여기서 `ctx` 가 실제로 하는 일은 둘뿐이다 — 글꼴 등록과 포커스. 글꼴은 **값 하나**라
    /// 밖에서 받으면 되고, 포커스는 창이 있을 때만 뜻이 있다. 그래서 그 둘만 위에 남기고
    /// 나머지 전부를 여기로 내렸다.
    pub fn with_font(link: ServerLink, font: FamilyId) -> Self {
        // 테스트가 이 문으로 들어온다 — 크롬도 같은 글꼴로 그린다(시험 폰트는 어차피
        // 빈 Line 이라 오라클은 글자 존재·순서만 잰다).
        Self::with_fonts(link, font, font)
    }

    /// [`with_font`](Self::with_font) 에 크롬 글꼴이 더해진 판 — 창이 있는 실행 경로.
    pub fn with_fonts(link: ServerLink, font: FamilyId, ui_font: FamilyId) -> Self {
        // 설정은 **한 번만** 읽는다 — 설정 화면이 바꾸면 이 값을 고치고 파일에 쓴다.
        let config = base::Config::load();
        let mut state = SessionState::new();
        state.set_inactive_dim(config.inactive_dim, config.inactive_dim_ratio);
        // 폭 판정은 프로세스 전역이다(compose::set_ambiguous_wide 문서 참조).
        proto::compose::set_ambiguous_wide(config.ambiguous_width == "wide");
        let arghist = proto::arghist::ArgHist::for_socket(link.socket());
        Self {
            state,
            link,
            arghist,
            ended: None,
            // 설정 파일의 prefix 를 그대로 쓴다(패리티 G5) — 파이썬 클라와 **같은
            // 파일**이라 두 클라를 오가도 손버릇이 같다.
            mode: ModeState::with_prefix(config.prefix),
            config,
            shell_result: Default::default(),
            paste_result: Default::default(),
            pending: Vec::new(),
            pinger: proto::rtt::Pinger::default(),
            screens: Screens::new(),
            chrome: Default::default(),
            chrome_click_states: Default::default(),
            panel_click_states: Default::default(),
            titlebar_click_states: Default::default(),
            status_click_states: Default::default(),
            session_edit: None,
            tab_drag: None,
            tab_drag_over: None,
            last_status: Instant::now(),
            last_blink: Instant::now(),
            blink_on: true,
            frames: std::cell::Cell::new(0),
            last_render: std::cell::Cell::new(None),
            frame_ms: std::cell::RefCell::new(std::collections::VecDeque::new()),
            painted_cells: std::cell::Cell::new(0),
            ime_badge: None,
            preedit: String::new(),
            last_ime: Instant::now(),
            told_ime_render: false,
            hook_watch: Default::default(),
            pending_restart: None,
            last_title: None,
            titlebar_band: None,
            window_op: None,
            window_focus: None,
            quit_requested: false,
            fullscreen_requested: false,
            paste_requested: false,
            dragging: None,
            divider_hover: None,
            span_hover: None,
            press: None,
            selection: None,
            block_pick: None,
            mouse_fwd: None,
            flash: None,
            alt_tab: false,
            copy_geom: None,
            drew: false,
            // 핸드셰이크에서 알린 크기와 같아야 한다 — 다르면 첫 프레임부터 한 번
            // 헛되이 재배치를 부른다(`main.rs` 의 attach 인자).
            size: SizeReporter::new(80, 24),
            // 아직 안 쟀다 — 첫 프레임은 종전 배치로 그리고, 자리표가 남는 즉시 격자를
            // 잡는다(§10-21ⓙ).
            cell_px: std::cell::Cell::new(None),
            canvas_px: std::cell::Cell::new(None),
            countdown_secs: 0,
            font,
            font_want: String::new(),
            ui_font,
            claude: Vec::new(),
            claude_mode: None,
            watcher: Watcher::new(projects_dir()),
            remote: RemoteTranscripts::default(),
            // 붙자마자 한 번 본다 — 첫 프레임에 이미 대화가 있으면 빈 구역을 보일
            // 이유가 없다.
            last_look: Instant::now() - CLAUDE_POLL,
            awaiting_search_all: false,
        }
    }

    /// warpui 의 키 이벤트를 core 의 중립 키로 옮긴다.
    ///
    /// 이름 표는 core 가 갖는다(`keys::from_name`) — 뷰가 키 이름을 직접 적으면 두 뷰가
    /// 서로 다른 이름을 알아듣기 시작한다(계층 게이트가 막는 바로 그것). TUI 의
    /// `key_from_event` 와 **같은 규칙**이고, 다른 것은 이벤트 타입뿐이다.
    fn key_from_keystroke(keystroke: &warpui::keymap::Keystroke) -> Option<(Key, Mods)> {
        let mods = Mods {
            ctrl: keystroke.ctrl,
            // meta/cmd 도 ESC 접두(Alt)로 취급한다 — 단말이 구분해 주지 않는다.
            alt: keystroke.alt || keystroke.meta || keystroke.cmd,
        };
        // 폴백 글자: 이름을 모르면 이름 자체의 첫 글자를 본다(GUI 는 TUI 의 `chars` 에
        // 해당하는 값을 따로 주지 않는다 — `key` 가 이미 그 글자다).
        let key = base::keys::from_name(
            &keystroke.key,
            keystroke.shift,
            keystroke.key.chars().next(),
        )?;
        Some((key, mods))
    }

    /// 이 키가 "지금 OS 클립보드를 붙여넣어 달라"인가 — `Ctrl+V` · `Cmd`(`Super`)`+V` ·
    /// `Ctrl+Shift+V`.
    ///
    /// # 왜 `Ctrl+V` **도** 인가 (pytmux-364)
    ///
    /// 종전에는 `Ctrl+Shift+V` 뿐이었고, 사유는 「`Ctrl+V`(0x16)는 패널 안 프로그램이 쓰는
    /// 바이트라 가로채면 vim 의 비주얼 블록·PSReadLine 의 붙여넣기가 조용히 사라진다」였다.
    /// **관찰은 지금도 옳다** — 뒤집는 이유는 관찰이 틀려서가 아니다.
    ///
    /// ⛔ **그 트레이드오프는 GUI 가 새로 할 것이 아니었다.** 정본이 이미 반대로 정해 두었다:
    /// `pytmuxlib/clientio.py` 가 `ctrl+v`/`super+v` 를 **크롬 키로** 잡고, 자리가 `F12`·
    /// prefix 바로 다음 · 패널 패스스루와 `bind -n` **앞**이다. 곧 정본에서도 패널 안
    /// 프로그램은 `0x16` 을 못 받는다 — 그 값은 이미 치르기로 한 것이다. 단말은 `Ctrl+V` 를
    /// **줄 수 있으므로** 허용 갈림 셋(ⓐ 단말이 못 주는 키 · ⓑ 픽셀 그림 · ⓒ OS 창 통합)
    /// 어디에도 안 들어간다(CLAUDE.md ★★ · `pytmux/pytmux-185` 와 같은 매듭).
    ///
    /// 되돌리는 길을 여기 만들지 않는다 — `0x16` 을 패널에 돌려주는 설정이 필요하다면
    /// **정본에 먼저** 생겨야 한다(정본에 없는 조작 표면을 GUI 가 먼저 만들지 않는다).
    ///
    /// # 세 벌인 이유
    ///
    /// - `Ctrl+V` — 정본과 **같은 손버릇**. 이것이 없어서 제보가 왔다.
    /// - `Cmd+V`/`Super+V` — 정본 `super+v` 의 짝. 맥에서는 `Ctrl+Shift+V` 가 관례가 아니라,
    ///   이것이 없으면 그 상자에는 **입구가 하나도 없다**.
    /// - `Ctrl+Shift+V` — 터미널 에뮬레이터 관례. 이미 손에 익은 사람이 있어 별칭으로 남긴다.
    ///
    /// `Alt` 가 섞이면 다른 조합이다(넓게 잡으면 그 조합이 통째로 사라진다). `Ctrl` 과
    /// `Cmd` 를 **함께** 쥔 것도 아니다 — 둘은 서로 다른 계열의 입구이고, 겹쳐 누른 것은
    /// 어느 쪽도 아닌 제3의 조합이다.
    ///
    /// 순수 함수로 빼 두는 이유: 이 판정이 틀리면 **아무 소리 없이** 어긋난다 — 좁으면
    /// 붙여넣기가 안 되고, 넓으면 패널 안 프로그램의 키가 사라진다. 창을 띄우지 않고
    /// 물을 수 있는 자리에 둔다.
    fn is_paste_chord(keystroke: &warpui::keymap::Keystroke) -> bool {
        if !keystroke.key.eq_ignore_ascii_case("v") || keystroke.alt {
            return false;
        }
        // `meta` 도 함께 본다 — 이 백엔드에서 맥의 `Cmd` 와 윈도·리눅스의 `Super` 가
        // 갈라져 오는 자리다. 정본의 `super+v` 는 그 셋을 한 이름으로 부른다.
        let cmd = keystroke.cmd || keystroke.meta;
        (keystroke.ctrl && !cmd) || (cmd && !keystroke.ctrl)
    }

    /// 이 키가 **전체 화면 토글**인가(§10-21ⓘ3) — `Alt`+`Enter`.
    ///
    /// # 왜 GUI 만 할 수 있나
    ///
    /// 정본(Textual TUI)의 풀스크린은 **호스트 단말의 일**이라 우리가 건드릴 자리가
    /// 없다 — 허용되는 갈림 ⓒ(OS 창 통합)다.
    ///
    /// ⚠ **가로채면 패널 안 프로그램의 `Alt`+`Enter` 가 사라진다.** `Ctrl+Shift+V`·
    /// `Ctrl+Tab` 때와 같은 판단이고, 제보가 그것을 요구한 것으로 읽는다. 그리고
    /// `Alt`+`Enter` 는 Windows 콘솔의 오랜 풀스크린 관습이라 손버릇도 맞다.
    ///
    /// 순수 함수인 이유도 같다 — 이 판정이 틀리면 아무 소리 없이 어긋난다(좁으면 안
    /// 먹고, 넓으면 패널의 키가 사라진다).
    /// ⚠ 키 **이름**을 여기 적지 않는다 — 이름 표의 주인은 core 다
    /// (`base::keys::from_name` · 계층 게이트 ④가 그것을 강제한다). 그래서 조합을
    /// 중립 키로 옮긴 뒤 그 값과 견준다.
    fn is_fullscreen_chord(keystroke: &warpui::keymap::Keystroke) -> bool {
        (keystroke.alt || keystroke.meta)
            && !(keystroke.ctrl || keystroke.shift || keystroke.cmd)
            && matches!(Self::key_from_keystroke(keystroke), Some((Key::Enter, _)))
    }

    /// 이 키가 **탭 전환**인가(§10-21ⓕ) — `Ctrl+Tab` / `Ctrl+Shift+Tab`.
    ///
    /// # 왜 GUI 만 할 수 있나
    ///
    /// 제보의 근거가 그대로 설계다: *"정본 Textual TUI 는 터미널 앱 안에서 도니까 이
    /// 조합을 쓸 수 없었다. `pytmux-gui` 는 독립 앱이라 쓸 수 있다."*
    ///
    /// ⚠ **가로채면 패널 안 프로그램의 `Ctrl+Tab` 이 사라진다** — 제보가 그것을 감수한
    /// 결정이다(`Ctrl+Shift+V` 때와 같은 판단이고, 같은 이유로 순수 함수다).
    /// 돌려주는 값은 **방향**이다(앞으로 = `true`).
    ///
    /// 액션(`NextTab`)을 바로 안 돌려주는 이유: 제보 ⓕ2 가 요구하는 것은 "누른 채
    /// 도는" 동선이고, **ⓕ 의 동작은 거기서 저절로 나온다** — 짧게 눌렀다 떼면 스위처가
    /// 열리자마자(커서는 다음 탭에 있다 = ⓔ2) 확정되므로 곧 "다음 탭으로"다. 두 제보를
    /// 각자 배선하면 같은 키가 두 갈래로 처리된다.
    fn tab_switch_chord(keystroke: &warpui::keymap::Keystroke) -> Option<bool> {
        if !keystroke.ctrl || keystroke.alt || keystroke.meta || keystroke.cmd {
            return None;
        }
        if !keystroke.key.eq_ignore_ascii_case("tab") {
            return None;
        }
        Some(!keystroke.shift)
    }

    /// `Ctrl` 을 쥔 채 도는 탭 스위처의 한 걸음(§10-21ⓕ·ⓕ2).
    ///
    /// **합성 키로 기존 길을 탄다** — 선택 이동도 확정도 이미 `screens.press` 가 하는
    /// 일이라, 여기서 따로 구현하면 같은 화면이 두 가지 규칙으로 움직인다.
    #[cfg(test)]
    pub(crate) fn alt_tab_step_for_test(&mut self, forward: bool) -> bool {
        self.alt_tab_step(forward)
    }

    fn alt_tab_step(&mut self, forward: bool) -> bool {
        // ★ **판이 떠 있으면 그 판의 탭이 먼저다**(§10-21ⓗ⑷). ⓕ 가 `Ctrl+Tab` 을 세션
        //   전역으로 가져갔는데, 제보는 *"이 판 위에서는 판 안 분류 탭을 옮긴다"* 이므로
        //   두 자리의 우선순위를 정해야 했다. 판이 위에 있으니 판이 이긴다 — 화면이 떠
        //   있으면 **모든 키가 그 화면의 것**이라는 core 규칙과 같은 결이다.
        //
        //   ⚠ 스위처 자신(`Screen::Tabs`)은 예외다. 그것이 곧 이 동선이고, `alt_tab`
        //   중에는 아래 걸음이 계속 돌아야 한다.
        if !self.alt_tab
            && let Some(screen) = self.screens.top()
            && matches!(screen, Screen::Commands | Screen::InfoTabs)
        {
            let key = if forward { Key::Right } else { Key::Left };
            return self.handle_key(key, Mods::NONE);
        }
        if !self.alt_tab {
            if !self.apply_action(Action::ShowTabs) {
                return true;
            }
            // 탭이 하나뿐이면 안 열린다(core 판정) — 그때는 모드도 안 켠다.
            if self.screens.top() != Some(Screen::Tabs) {
                return true;
            }
            self.alt_tab = true;
            // 열자마자 커서는 **다음 탭**이다(ⓔ2). 뒤로 도는 첫 걸음은 거기서 두 칸
            // 올라가야 **이전 탭**이 된다.
            if !forward {
                self.handle_key(Key::Up, Mods::NONE);
                self.handle_key(Key::Up, Mods::NONE);
            }
            return true;
        }
        let key = if forward { Key::Down } else { Key::Up };
        self.handle_key(key, Mods::NONE)
    }

    /// `Ctrl` 을 뗐다 — 쥔 채 돌고 있었으면 **확정**한다(§10-21ⓕ2).
    ///
    /// # 포커스를 잃으면 (열어 뒀던 물음의 답)
    ///
    /// 창이 포커스를 잃는 동안 뗌을 못 받을 수 있다. 그때는 **아무것도 하지 않는다** —
    /// 확정도 취소도 아니다. 스위처는 평범한 화면이라 `Enter`·`Esc` 가 그대로 듣고,
    /// 그래서 **갇히는 모드가 아니다**. 몰래 탭을 바꾸는 쪽(확정)이나 사용자가 고른
    /// 것을 버리는 쪽(취소)보다 이쪽이 놀랍지 않다.
    pub fn release_ctrl(&mut self) -> bool {
        if !self.alt_tab {
            return false;
        }
        self.alt_tab = false;
        // 그 사이에 화면이 닫혔으면(Esc) 확정할 것이 없다.
        if self.screens.top() != Some(Screen::Tabs) {
            return false;
        }
        self.handle_key(Key::Enter, Mods::NONE)
    }

    /// 이 키가 "앱 글자 크기를 바꿔 달라"인가(§10-21ⓐ) — `Ctrl+=`/`Ctrl++`/`Ctrl+-`/`Ctrl+0`.
    ///
    /// # 왜 가로채도 되나
    ///
    /// `Ctrl+Shift+V` 와 **같은 판단**이다: 이 클라에는 글자 크기를 대신 바꿔 줄 바깥
    /// 터미널이 없다(TUI 는 호스트 단말이 한다). 브라우저·터미널 에뮬레이터의 관습이
    /// 그대로 이 조합이라 손버릇도 맞는다.
    ///
    /// # `=` 와 `+` 를 둘 다 받는 이유
    ///
    /// 이 조합의 관습적 이름은 "Ctrl 확대"인데, 사람이 실제로 누르는 것은 **Shift 없이
    /// `=`** 이거나 **Shift 를 눌러 `+`** 다. 하나만 받으면 절반의 사람에게 안 먹고,
    /// 그건 조용하다. 숫자패드 `+`(`add`)도 같은 자리다.
    ///
    /// 순수 함수로 빼는 이유도 붙여넣기와 같다 — 창 없이 물을 수 있어야 오라클이 선다.
    fn font_scale_chord(keystroke: &warpui::keymap::Keystroke) -> Option<Action> {
        if !keystroke.ctrl || keystroke.alt || keystroke.meta || keystroke.cmd {
            return None;
        }
        match keystroke.key.as_str() {
            "=" | "+" | "add" => Some(Action::FontScale { up: true }),
            "-" | "_" | "subtract" => Some(Action::FontScale { up: false }),
            "0" => Some(Action::FontScaleReset),
            _ => None,
        }
    }

    /// 키 하나. 반환값은 "다시 그려야 하는가".
    /// 물음이 방금 열렸으면 인자 이력을 core 에 채운다(지연 채움 — TUI 와 같은 자리).
    fn refill_prompt_history(&mut self) {
        if let Some(prompt) = self.screens.asking_unfilled() {
            let history = proto::arghist::bucket(prompt)
                .map_or_else(Vec::new, |bucket| self.arghist.recent(bucket));
            self.screens.set_prompt_history(history);
        }
    }

    /// 활성 패널의 오버레이를 켜고 끄고, 그 **사실**을 서버에 올린다(설계 Tier B).
    ///
    /// 그림은 서버가 준다 — 우리가 아는 것은 "이 패널에 이걸 켰다"뿐이다. 한 패널엔
    /// 오버레이 하나라, 밀려난 것이 있으면 그 끔도 같이 올린다(안 올리면 서버가 두
    /// 그림을 겹쳐 보낸다).
    fn push_overlay_toggle(&mut self, name: &str) {
        let t = self.state.toggle_overlay(name);
        self.push_overlay(name, t);
    }

    /// 오버레이를 **명시적으로** 켜거나 끈다(§10-21ⓡ). 토글과 같은 프레임을 낸다.
    fn push_overlay_set(&mut self, name: &str, on: bool) {
        let t = self.state.set_overlay(name, on);
        self.push_overlay(name, t);
    }

    fn push_overlay(&mut self, name: &str, t: Option<proto::session::OverlayToggle>) {
        let Some(t) = t else {
            // ⛔ **말없이 돌아가지 않는다**(pytmux-366). `toggle_overlay`/`set_overlay` 의
            //    첫 줄은 `self.layout.as_ref()?` 라, 아직 레이아웃을 못 받았으면 조용히
            //    `None` 이다 — 그러면 시각을 눌러도·`prefix t` 를 쳐도 **아무 일도 안
            //    일어나고 자취도 없다**. 제보가 가른 후보 넷 중 어느 것인지 사용자도
            //    로그도 못 가르던 이유가 이 침묵이었다.
            //
            //    이 저장소의 규율과 같은 자리다: 「못 하는 것을 눌리는 것처럼 그리지
            //    않는다」. 여기서는 이미 눌린 뒤라, 최소한 **왜 안 됐는지**는 말한다.
            log::info!("오버레이 {name} 요청 — 아직 레이아웃이 없어 대상 패널을 모른다");
            // ⚠ 인자 이름 `t` 가 i18n 의 `t` 를 가린다 — 여기서만 길게 부른다.
            self.state
                .note_notice(base::i18n::t("아직 화면을 받지 못했다 — 잠시 뒤 다시"));
            return;
        };
        if let Some(closed) = t.closed {
            self.pending.push(Outgoing::Command(Command::PluginOverlay {
                name: closed,
                pane: t.pane,
                on: false,
            }));
        }
        self.pending.push(Outgoing::Command(Command::PluginOverlay {
            name: name.to_owned(),
            pane: t.pane,
            on: t.on,
        }));
    }

    pub fn handle_key(&mut self, key: Key, mods: Mods) -> bool {
        // 직전 이벤트가 연 물음의 이력을 지금 채운다(이 키부터 후보가 산다).
        self.refill_prompt_history();
        // ★ 세션 이름 제자리 편집(pytmux-3): 상태줄 `#S` 를 눌러 그 자리가 입력칸이 된
        //   동안에는 **모든 키를 여기서 먹는다**. 판을 안 띄우므로 화면 스택이 비어
        //   있고, 그래서 이 분기가 없으면 키가 전부 패널로 흐른다 — 편집 중에 셸이
        //   글자를 받는다.
        //
        //   자리는 화면 검사 **앞**이다(정본은 모달 검사 직후). 화면이 떠 있으면 그
        //   화면의 것이라는 규칙은 그대로 지킨다 — 판이 열리면 편집칸을 먼저 닫는다.
        if self.session_editing() {
            if self.screens.top().is_some() {
                self.session_edit = None;
            } else {
                return self.session_edit_key(key, mods);
            }
        }
        // ★ 화면이 떠 있으면 **모든 키가 화면의 것**이다(core 규칙). 여기서 안 가로채면
        // 화면 뒤 패널로 키가 새고, 사용자는 자기가 무엇을 조작하는지 알 수 없다.
        // 어느 화면에서 고른 것인지는 **누르기 전에** 알아 둬야 한다 — 확정과 동시에
        // 화면이 닫히므로 그 뒤에 물으면 이미 없다.
        // ★ 패널 번호(`prefix q`)가 떠 있으면 **이 키가 그것을 지운다**(파이썬도 모드라
        // 그렇다). 숫자면 그 패널로 가고 거기서 끝, 아니면 지우기만 하고 평소 경로다 —
        // 번호를 띄운 채로 다른 일을 하려던 사람의 키를 삼키지 않는다.
        if self.state.pane_numbers() {
            self.state.clear_pane_numbers();
            if let Key::Char(c) = key
                && let Some(n) = c.to_digit(10)
                && let Some(id) = self.state.pane_by_number(n as usize)
            {
                self.pending.push(Outgoing::Command(Command::SelectPaneId { id }));
                return true;
            }
        }
        // ★ 사용자가 건 키(`bind`)가 **표보다 먼저**다 — 안 그러면 내장 키와 겹칠 때
        // 사용자가 적은 것이 영영 안 먹고, 그건 "설정했는데 안 먹는다"의 한 가지다.
        if !self.config.binds.is_empty() {
            let after_prefix = self.mode.mode() == InputMode::Prefix;
            if let Some(action) =
                base::config::user_action(&self.config.binds, after_prefix, key, mods)
            {
                // prefix 모드는 **키 하나만** 붙잡는다(tmux 와 같다).
                if after_prefix {
                    self.mode.reset();
                }
                self.apply_action(action);
                return true;
            }
        }
        // ★ 팝업이 떠 있으면 **키는 팝업의 것**이다(파이썬과 같다). 팝업은 트리 밖이라
        // 활성 패널이 될 수 없어서, id 를 실어 보내야 그 PTY 에 닿는다.
        //
        // prefix 만은 우리 것으로 남긴다 — 그게 prefix 의 존재 이유이고, 그 길이 없으면
        // 팝업 안 명령이 끝나기 전에는 `popup-close` 를 부를 방법이 없다.
        if self.screens.top().is_none()
            && self.mode.mode() == InputMode::Normal
            && let Some(popup) = self.state.popup().map(|p| p.id)
            && (key, mods) != self.mode.prefix()
            && let Some(bytes) = base::keys::encode(key, mods)
        {
            self.pending
                .push(Outgoing::InputToPane { pane: popup, data: bytes });
            return true;
        }
        let screen_before = self.screens.top();
        // 정보 팝업의 REC 탭에서 [c]/[o] 는 **동작 키**다(G9u — TUI 와 같은 표).
        //
        // ⛔ **무엇이 있나는 `proto` 한 곳이 쥔다**(pytmux-373 ⑶). 종전에는 이 자리가
        //    `info_tab() == 0` 과 `capture.is_some()` 을 **직접** 적어 판정을 두 벌로
        //    들고 있었고, 그래서 같은 둘이 목록에는 항목으로 안 섰다.
        if screen_before == Some(Screen::InfoTabs) && mods == base::Mods::NONE {
            let picked = self.screens.info_tab();
            if let base::Key::Char(ch) = key
                && proto::info::tab_actions(&self.state, picked)
                    .iter()
                    .any(|a| a.key == ch)
            {
                self.run_info_action(ch);
                return true;
            }
        }
        // 팔레트는 **확정과 동시에 필터가 지워진다** — 고른 줄이 어느 항목인지 알려면
        // 누르기 전 필터가 필요하다.
        let filter_before = self.screens.typed_filter().to_owned();
        // ★ 인자도 같이 잡는다(pytmux-7) — 팔레트 입력줄은 이제 **필터이자 명령줄**이라
        //   첫 공백 뒤가 인자다. 확정과 동시에 지워지는 것은 필터와 같다.
        let arg_before = self.screens.typed_arg().to_owned();
        // ★ 팔레트 탭도 `filter_before` 와 **같은 이유로** 키를 먹기 전에 잡는다:
        //   `Chosen(row)` 의 row 는 **그때 걸러져 있던 목록** 안 자리라, 되돌릴 때 쓰는
        //   필터와 분류가 둘 다 그때 것이어야 한다. 하나라도 어긋나면 분류 탭에서 고른
        //   줄이 엉뚱한 명령을 실행한다 — 조용히 틀리는 부류다.
        // 분류 이름은 **옮겨 담는다** — 플러그인이 낸 분류가 섞이면서 이 값이 화면
        // 상태에서 빌린 것이 됐고, 그 아래에서 `screens.press` 가 그것을 빌려야 한다.
        let cat_before = self.screens.palette_cat().map(str::to_owned);
        // 메뉴도 같다 — `Chosen(row)` 의 row 는 **그때 보이던 층**의 자리다(계층화 뒤로는
        // 최상위와 서브메뉴가 서로 다른 줄 목록이다).
        let menu_rows_before = self.screens.menu_rows();
        // 취소도 **끝난 것**이다 — 무엇을 묻고 있었는지는 키를 먹기 전에 잡아야 안다
        // (`Closed` 로 오는 '아니오'는 대답을 안 실어 온다).
        let asking_before = self.screens.asking();
        // 고른 줄도 키를 먹기 **전에** 잡는다 — 판을 닫는 키는 그 자리에서 선택을
        // 0 으로 되돌리므로, 한 판 물러난 뒤에 물어보면 이미 늦다.
        let selected_before = self.screens.selected();
        // ★ 플러그인 화면의 **글자 키**는 스펙이 정한다(설계 P5 — `ncd` 의 `c` = 여기로
        //   cd). 화면 키 처리보다 먼저 보는 이유: 목록 화면에서 글자는 원래 "닫기"라
        //   뒤에 두면 우리 키가 그 판을 먼저 닫는다. 표에 있는 글자만 먹는다.
        if self.screens.top() == Some(Screen::PluginView)
            && let Some((id, act)) = self.state.plugin_screen().and_then(|spec| {
                spec.key_action(key, mods).map(|a| (spec.id.clone(), a.to_owned()))
            })
        {
            let row = self.screens.selected();
            let input = self
                .state
                .plugin_screen()
                .and_then(|spec| spec.rows.get(row).map(|r| r.key.clone()));
            self.pending.push(Outgoing::Command(Command::PluginAction { id, act, row, input }));
            return true;
        }
        // ★ 다열 판(설계 §4.3 `panel`)의 ←→·PgUp/PgDn 은 **열/판 단위**다. 그 기하는
        //   칸 예산이 정하므로 그리는 쪽만 알고, 뜻은 core 가 든다(`set_plugin_grid`).
        //   ⚠ **열 때가 아니라 여기서** 넣는다 — 창이 바뀌면 열당 줄 수가 함께 바뀐다.
        if self.screens.top() == Some(Screen::PluginView) {
            let (per_col, cols) = self.panel_grid();
            self.screens.set_plugin_grid(per_col, cols);
        }
        if let Some(outcome) = self.screens.press(key, mods) {
            // ★ 정보 팝업은 **자리 맞추기가 먼저다** — `←→` 가 넘긴 자리를 접고 `↑↓` 가
            //   민 커서를 자른 뒤라야 아래 `Applied` 가 **실재하는 줄**을 가리킨다.
            self.settle_info_tabs();
            // 설정·플러그인 판도 같다(pytmux-374 ⑶) — `End`·`PageDown` 이 두고 간 자리를
            // 여기서 접어야 뒤따르는 `Applied`/`AppliedDir` 이 실재하는 줄을 가리킨다.
            self.settle_settings_cursor();
            // 플러그인 글 판도 같다(pytmux-184 ⑵) — 끝에서 더 눌러 둔 만큼 `↑` 가 헛돈다.
            self.settle_plugin_scroll();
            // 그리고 플러그인 **목록** 판(pytmux-432) — 위 셋 중 어느 것도 그 판을 안 봤다.
            self.settle_plugin_cursor();
            // 고른 것을 **뷰가 해석한다** — core 는 목록의 내용을 모른다(그 경계 덕에
            // 목록 화면이 늘어도 core 는 그대로다).
            match outcome {
                // 인자 폼은 **줄이 아니라 값**을 고른 것이라 `row` 를 안 본다.
                ScreenKey::Chosen(_) if screen_before == Some(Screen::Options) => {
                    self.apply_option_pick();
                }
                ScreenKey::Chosen(row) if screen_before == Some(Screen::MergeRemote) => {
                    if let Some((src, _)) = self.state.merge_candidates().get(row) {
                        // 대상은 **지금 활성 패널**이다 — 먼저 못박아 두지 않으면
                        // 서버가 그 사이에 바뀐 활성 패널에 붙인다(파이썬과 같은 순서).
                        if let Some(id) = self.state.tabs().active_pane {
                            self.pending
                                .push(Outgoing::Command(Command::SelectPaneId { id }));
                        }
                        self.pending.push(Outgoing::Command(Command::JoinPane {
                            src: *src,
                            horizontal: self.screens.merge_horizontal(),
                        }));
                    }
                }
                ScreenKey::Chosen(row) if screen_before == Some(Screen::Layouts) => {
                    if let Some((_, preset)) = base::LAYOUT_PRESETS.get(row) {
                        self.pending.push(Outgoing::Command(Command::SelectLayout {
                            preset,
                        }));
                    }
                }
                // 자동 재개 판에서 `a` 를 눌렀다(pytmux-183) — 정본 `hide_cb` 와 같은 일:
                // 뒤집는 명령을 보낸다(판은 core 가 이미 닫았다).
                ScreenKey::Chosen(_) if screen_before == Some(Screen::Autoresume) => {
                    self.apply_action(Action::ToggleAutoresume);
                }
                // 플러그인이 준 목록에서 골랐다 — **그 줄의 뜻**을 되돌려준다(P4).
                ScreenKey::Chosen(row) if screen_before == Some(Screen::PluginView) => {
                    self.plugin_view_chosen(row);
                }
                // 전역 검색 결과에서 골랐다(pytmux-27) — 그 탭·패널·줄로 뛴다.
                ScreenKey::Chosen(row) if screen_before == Some(Screen::SearchResults) => {
                    self.search_result_chosen(row);
                }
                ScreenKey::Chosen(row) if screen_before == Some(Screen::Menu) => {
                    // ★ 메뉴에서 고른 것도 **키로 누른 것과 같은 길**을 탄다 — 그래야
                    // `패널 삭제` 를 골라도 확인 화면이 뜬다(팔레트와 같은 규칙).
                    match menu_rows_before.get(row) {
                        Some(base::MenuRow::Item(entry)) => {
                            self.apply_action(entry.action);
                        }
                        // 플러그인 줄은 **그 플러그인의 명령 이름**이다(정본 계약).
                        // 우리가 네이티브로 든 이름이면 그 액션을 타고(시계·달력),
                        // 아니면 팔레트와 **같은 알림**을 남긴다 — 조용히 버리면 사용자는
                        // 자기가 잘못 골랐다고 읽는다(설계 §8-5).
                        Some(base::MenuRow::Plugin(i)) => {
                            let key = self
                                .screens
                                .plugins()
                                .menu_items
                                .get(*i)
                                .map(|item| item.key.clone());
                            if let Some(key) = key {
                                match base::plugins::native_action(&key) {
                                    Some(action) => {
                                        self.apply_action(action);
                                    }
                                    // 네이티브로 안 든 이름은 **서버에 화면을 묻는다**(P4).
                                    None => self.pending.push(Outgoing::Command(
                                        Command::PluginOpen { name: key, args: Vec::new() },
                                    )),
                                }
                            }
                        }
                        _ => {}
                    }
                }
                ScreenKey::Chosen(row) => {
                    // ★ 팔레트에서 고른 것은 **액션으로 되돌려 같은 길**을 태운다 —
                    // 그래야 `kill-pane` 을 골라도 확인 화면이 뜬다(명령을 바로 보내면
                    // 그 화면을 건너뛴다).
                    if screen_before == Some(Screen::Commands) {
                        match self.palette_pick(cat_before.as_deref(), &filter_before, row) {
                            // ★ 인자를 그 줄에서 이어 쳤으면 **물음 판을 안 띄운다**
                            //   (pytmux-7 — 정본 `:` 줄과 같은 손). 고른 줄의 이름에
                            //   친 인자를 붙여 core 의 한 줄 해석기에 넘긴다. 이름을
                            //   **친 글자가 아니라 고른 줄**에서 가져오는 이유: 부분만
                            //   쳤어도(`rem host1`) 고른 명령이 뜻이다.
                            Some(pick) if !arg_before.is_empty() => {
                                self.run_with_arg(pick, &arg_before);
                            }
                            Some(PalettePick::Action(action)) => {
                                self.apply_action(action);
                            }
                            // ★ 플러그인 명령은 **아직 화면이 없다**(설계 P4).
                            //   조용히 버리지 않는다 — 목록에 보이는데 눌러도 아무 일이
                            //   안 나면 사용자는 자기가 잘못 골랐다고 읽는다(설계 §8-5).
                            // ★ 플러그인 명령은 **이름만 보낸다**(pytmux-35). 종전에는
                            //   전부 "화면을 달라"(`plugin_open`)였는데, 상태를 바꾸는
                            //   명령에는 통째로 틀린 길이라 서버가 거절했고 사용자에게는
                            //   죽은 줄로 보였다 — 팔레트에 보이는데 안 먹는 줄 열여덟이
                            //   전부 그것이다.
                            //
                            //   갈래는 **서버가 정한다**: 상태형이면 거기서 끝나고, 아니면
                            //   서버가 화면 스펙 경로로 넘어간다. 그 표를 우리가 들면
                            //   서버와 갈리고, 갈린 순간 명령은 **조용히** 죽는다(이
                            //   결함이 생긴 원인 그대로). 회신이 없으면 서버가 알림을
                            //   보낸다(설계 §8-5) — 조용히 끝나는 길은 어느 쪽에도 없다.
                            Some(PalettePick::Plugin(name)) => {
                                self.pending.push(Outgoing::Command(Command::PluginCmd {
                                    name,
                                    args: Vec::new(),
                                }));
                            }
                            None => {}
                        }
                    } else {
                        for command in self.picked(screen_before, row) {
                            self.pending.push(Outgoing::Command(command));
                        }
                    }
                }
                // 플러그인이 물은 것의 답 — 그 화면의 액션으로 되돌려준다.
                ScreenKey::Answered(base::Prompt::PluginAsk, answer) => {
                    self.answer_plugin_ask(Some(answer));
                }
                ScreenKey::Answered(prompt, answer) => self.apply_answer(prompt, answer),
                // 트리에서 d/x — 파이썬처럼 **먼저 그 자리로 옮기고** 확인을 세운다
                // (확인 없는 닫기는 없다 — 탭이면 탭 닫기, 패널이면 패널 닫기).
                ScreenKey::TreeKill(row) => {
                    if let Some(target) = self.state.tree_rows().get(row).cloned() {
                        if let Some(window) = target.window {
                            let wid = self.state.tabs().wid_of(window);
                            self.pending.push(Outgoing::Command(Command::SelectWindow {
                                index: window,
                                wid,
                            }));
                        }
                        match target.pane {
                            Some(id) => {
                                self.pending
                                    .push(Outgoing::Command(Command::SelectPaneId { id }));
                                self.screens.confirm(Prompt::KillPane);
                            }
                            None => self.confirm_kill_tab(),
                        }
                    }
                }

                // 작성창을 다 썼다. **`paste` 로 보낸다** — `input` 으로 보내면 셸이
                // 타이핑으로 받아 줄마다 실행된다(`Command::Paste` 문서).
                ScreenKey::Injected(text) => {
                    if text.is_empty() {
                        // 빈 채로 보내면 아무 일도 안 일어나는데, 그 사실이 화면에 안
                        // 보이면 "키가 안 먹었다"로 읽힌다.
                        self.state.note_notice(t(Screen::COMPOSE_EMPTY));
                    } else {
                        self.clear_prompt_then_paste(text);
                    }
                }
                // 설정 화면 — 고른 줄이 토글이면 평소 액션 경로로, 값을 받는 줄이면
                // 물음을 띄운다. **화면은 그대로 있다**(그게 Applied 다).
                // 정보 팝업의 `Enter` — 동작 줄이면 돌리고(판 유지), 아니면 닫는다.
                // ⚠ 값(`row`)이 아니라 **접힌 뒤의 커서**를 본다(위 `settle_info_tabs`).
                ScreenKey::Applied(_) if screen_before == Some(Screen::InfoTabs) => {
                    self.run_info_row();
                }
                ScreenKey::Applied(row) if screen_before == Some(Screen::Plugins) => {
                    if let Some(command) = self.plugin_toggle(row) {
                        self.pending.push(Outgoing::Command(command));
                    }
                }
                // `Enter` 는 앞으로, `←→` 는 방향대로 — 값을 고르는 길은 **한 벌**이다.
                ScreenKey::Applied(row) | ScreenKey::AppliedDir(row, _) => {
                    let forward = !matches!(outcome, ScreenKey::AppliedDir(_, false));
                    // ⚠ `screen_before` 를 보고 옮긴다 — 이 팔에 닿을 때 판은 아직
                    //   그대로지만(둘 다 `Enter` 로 안 닫힌다) 판정의 근거를 «누른
                    //   순간의 판»으로 두는 편이 다음 사람에게 덜 위험하다.
                    let pick = Self::settings_row(screen_before, row, &self.config)
                        .and_then(|row| {
                            base::config::setting_pick_dir(
                                row,
                                &self.setting_values(),
                                forward,
                            )
                        });
                    self.apply_setting_pick(pick);
                }
                // 플러그인이 물은 것을 **취소**했다 — 아무 일도 안 일어나고, 그 스펙은
                // 들고 있을 이유가 없다(다음에 열 때 낡은 물음이 되살아나면 안 된다).
                ScreenKey::Closed if asking_before == Some(base::Prompt::PluginAsk) => {
                    self.answer_plugin_ask(None);
                }
                // 플러그인 화면을 `Esc` 로 닫았다 — **한 판 물러난다**(상세 → 목록).
                // 서버에 다시 묻지 않는 이유: p4 를 또 부르면 느리고, 그 사이 목록이
                // 달라지면 방금 보던 자리를 잃는다.
                ScreenKey::Closed if screen_before == Some(Screen::PluginView) => {
                    if self.state.pop_plugin_screen() {
                        let is_list =
                            self.state.plugin_screen().is_some_and(|spec| spec.is_selectable());
                        self.screens.open_plugin_view(is_list);
                        // ★ **보던 자리로** 돌아간다. 서버에 다시 안 묻는 이유가 "방금
                        //   보던 자리를 잃지 않으려고"인데, 정작 판을 닫는 키가 선택을
                        //   0 으로 되돌려 자리를 잃고 있었다(P4 부터 있던 구멍).
                        self.screens.select_row(selected_before);
                    }
                }
                ScreenKey::Consumed | ScreenKey::Closed => {}
            }
            // 화면이 바뀌었으면 판 안 hover 를 **버린다**. 상태 풀의 색인은 렌더 순서라,
            // 안 버리면 사라진 위젯의 "올라와 있음"이 다음 화면의 같은 번호 줄에 붙어
            // 엉뚱한 데가 밝아 보인다(그 줄을 안 눌렀는데 눌릴 것처럼 보인다).
            if self.screens.top() != screen_before {
                self.panel_click_states.borrow_mut().clear();
            }
            return true;
        }
        // ★ esc 모드에서는 **크롬이 먼저 본다**. 포커스가 캔버스 밖에 있으면 방향키는
        // 탭바·배지의 것이고, 가장자리에서는 방향키가 포커스를 밖으로 내보낸다
        // (패리티 `e_up`·`e_tb`·`e_down`). 크롬이 안 먹으면 평소 표로 떨어진다.
        if self.mode.mode() == InputMode::Command
            && let Some(handled) = self.press_chrome(key, mods)
        {
            return handled;
        }
        // ★ 오버레이가 **스펙으로 가져간 키**(달력의 ←/→/Home)는 패널로 안 보낸다.
        //   활성 패널이 이미 달력에 덮여 있으니 셸 입력을 가리지 않는다(정본
        //   `client_overlay_key` 와 같은 규칙이고, 어느 키인지도 그 표 한 벌이 정한다).
        //   평소 모드에서만 본다 — prefix/스크롤/esc 모드의 방향키는 그 모드의 것이다.
        if self.mode.mode() == InputMode::Normal
            && self.screens.top().is_none()
            && let Some(name) = base::keys::binding_name_with(key, mods)
            && let Some((overlay, pane, act)) = self.state.overlay_key(&name)
        {
            self.pending.push(Outgoing::Command(Command::PluginOverlayAction {
                name: overlay,
                pane,
                act,
            }));
            return true;
        }
        // 모드 전이는 core 의 상태기계가 끝낸다. 여기서는 결과를 프레임으로 옮기기만 한다.
        let outcome = self.mode.press_in(&self.config.mode_keys, key, mods);
        // 블록 모드를 벗어나면 고른 것도 **같이** 버린다 — 안 버리면 강조가 화면에 남아
        // "아직 고르는 중"이라고 말하는데 키는 이미 패널로 간다(esc 모드에서 크롬
        // 포커스를 같이 푸는 것과 같은 규율. 자리도 나란히 둔다).
        self.drop_block_pick_unless_selecting();
        // esc 모드를 벗어나면 크롬 포커스도 **같이** 푼다 — 안 풀면 다음 esc 때 지난번
        // 포커스가 살아나 방향키가 패널이 아니라 탭바를 움직인다.
        if self.mode.mode() != InputMode::Command {
            self.chrome.reset();
        }
        match outcome {
            KeyOutcome::ToPane(bytes) => {
                self.pending.push(Outgoing::Input(bytes));
                true
            }
            KeyOutcome::Scroll { amount, .. } => {
                self.scroll(amount);
                true
            }
            KeyOutcome::Block(what) => self.block_key(what),
            KeyOutcome::ModeChanged(_) => true,
            KeyOutcome::Action(action) => self.apply_action(action),
            KeyOutcome::Ignored => false,
        }
    }

    /// 지금 상태에서 크롬이 알아야 하는 사실들. 판정은 core 가 한다(TUI 와 같은 함수).
    fn chrome_ctx<'a>(
        &self,
        tabs: &'a [usize],
        badges: &'a [base::Badge],
    ) -> base::ChromeCtx<'a> {
        base::ChromeCtx {
            pane_above: self.state.pane_above(),
            pane_below: self.state.pane_below(),
            tabs,
            active: self
                .state
                .tabs()
                .tabs
                .iter()
                .position(|t| t.active)
                .unwrap_or(0),
            badges,
        }
    }

    /// 크롬이 쓰는 탭 값들 — **표시 번호**(1-based, 시각 순서)다.
    ///
    /// index 가 아니다: 크롬이 고른 탭은 [`Action::SelectTab`] 으로 나가고, 그 액션의
    /// 뜻은 숫자키 경로와 **같은 하나**(표시 번호)여야 한다.
    fn chrome_tabs(&self) -> Vec<usize> {
        self.state.tabs().visual_numbers()
    }

    /// esc 모드의 키를 크롬이 먹는지 본다. 먹었으면 "다시 그릴 것이 있나"를 돌려준다.
    ///
    /// 모드를 푸는 자리가 여기인 이유: 이 경로는 `ModeState::press_in` 을 안 지나므로 그
    /// 안의 모드 전이도 안 일어난다. 크롬이 "끝났다"고 한 것과 모드가 풀리는 것이 어긋나면
    /// esc 배지가 켜진 채로 키가 패널로 가기 시작한다.
    fn press_chrome(&mut self, key: Key, mods: Mods) -> Option<bool> {
        use base::ChromeKey;
        let tabs = self.chrome_tabs();
        let badges = self.state.badges();
        let ctx = self.chrome_ctx(&tabs, &badges);
        match self.chrome.press(&ctx, key, mods)? {
            ChromeKey::Redraw => Some(true),
            // **머문다** — 모드도 포커스도 그대로 두고 액션만 흘린다.
            ChromeKey::Stay(action) => Some(self.apply_action(action)),
            ChromeKey::Done(action) => {
                self.mode.reset();
                self.chrome.reset();
                Some(self.apply_action(action))
            }
            ChromeKey::Leave => {
                self.mode.reset();
                self.chrome.reset();
                Some(true)
            }
        }
    }

    /// 액션 하나. **키로 들어오든 다른 경로로 들어오든 여기 한 곳을 지난다** — 두 자리에서
    /// 해석하면 키로 하는 일과 메뉴로 하는 일이 갈라진다.
    /// 테스트가 액션 전수를 먹일 수 있게 여는 문(G1 측정 — `session_view_tests.rs`).
    ///
    /// 별칭 하나를 두는 이유: `apply_action` 자체를 `pub` 로 열면 뷰 밖에서 액션을 밀어
    /// 넣는 길이 생기고, 그러면 "키 → 액션 → 명령" 한 줄기라는 계약이 흐려진다.
    /// 표시용 스크롤바(§10-21ⓨ2) — 창 없이 물을 수 있는 관측점.
    #[cfg(test)]
    pub(crate) fn scroll_hints_for_test(&self) -> Vec<crate::splitter::ScrollHint> {
        self.scroll_hints()
    }

    /// 상태 읽기(테스트) — 배치·패널 사각형을 오라클이 확인할 때.
    #[cfg(test)]
    pub(crate) fn state_for_test(&self) -> &proto::SessionState {
        &self.state
    }

    /// 전체 화면 요청이 남아 있나(§10-21ⓘ3). 창 없이 물을 수 있는 유일한 관측점이다.
    #[cfg(test)]
    pub(crate) fn fullscreen_requested_for_test(&self) -> bool {
        self.fullscreen_requested
    }

    #[cfg(test)]
    pub(crate) fn apply_action_for_test(&mut self, action: Action) -> bool {
        self.apply_action(action)
    }

    /// 클립보드 한 장을 먹인다 — **읽기를 뺀** 붙여넣기 전부(`paste_from_clipboard`).
    ///
    /// 진짜 클립보드에 그림을 넣어야 잴 수 있는 함수는 헤드리스 러너가 못 잰다. 읽기는
    /// 창을 쥔 자리에 있고, 여기로는 **읽은 것**이 들어온다.
    #[cfg(test)]
    pub(crate) fn paste_clipboard_for_test(&mut self, content: ClipboardContent) -> bool {
        self.paste_from_clipboard(content)
    }

    /// 팔레트·`bind` 로 들어온 붙여넣기 요청이 한 칸에 서 있나(창 없이 묻는 유일한 관측점).
    #[cfg(test)]
    pub(crate) fn paste_requested_for_test(&self) -> bool {
        self.paste_requested
    }

    /// 팔레트 이름 하나를 이 뷰의 색으로 — 팔레트 오라클이 여덟 짝을 재는 문(pytmux-187).
    #[cfg(test)]
    pub(crate) fn named_for_test(color: proto::style::NamedColor) -> ColorU {
        named(color)
    }

    /// 셀 폭·높이를 잴 수 있나(그린 뒤에만 안다). 폭 오라클이 갈래를 가르는 데 쓴다.
    #[cfg(test)]
    pub(crate) fn cell_px_for_test(&self) -> Option<(f32, f32)> {
        self.cell_px.get()
    }

    /// 시험이 **잰 값을 대신 넣는** 자리(헤드리스에는 자리표가 없다).
    ///
    /// ⛔ 프로덕션은 절대 안 부른다 — 자리표에서 재는 것이 규율이고, 여기 값을 심는 것은
    ///    「그때 이렇게 쟀다면」을 재현하기 위한 것뿐이다. 그래서 `#[cfg(test)]` 다.
    #[cfg(test)]
    pub(crate) fn note_canvas_box_for_test(&self, left: f32, top: f32, win_w: f32, win_h: f32) {
        self.canvas_px.set(Some(CanvasBox { left, top, win_w, win_h }));
    }

    /// 작성창 자리 산수의 결과(시험이 읽는다).
    #[cfg(test)]
    pub(crate) fn pane_cursor_box_for_test(&self) -> Option<(f32, f32, f32)> {
        self.pane_cursor_box()
    }

    /// 깜빡임 한 틱(`pytmux/pytmux-161`). `expired` 면 **반주기가 지난 것으로 치고** 부른다.
    ///
    /// # 왜 시계를 밀어 주나
    ///
    /// 진짜로 기다리면 시험이 최소 100ms 를 태우고, 그 시험은 **부하에 진다**(이 저장소가
    /// 벽시계 시험으로 이미 겪은 자리다). 재는 것은 「시간이 얼마나 흘렀나」가 아니라
    /// 「반주기가 지나면 뒤집히나 · 껐을 때 되살아나나」라, 시각은 밖에서 정하는 것이 맞다
    /// (`report_ime` 가 OS 를 밖으로 뺀 것과 같은 규율).
    #[cfg(test)]
    pub(crate) fn tick_cursor_blink_for_test(&mut self, expired: bool) -> bool {
        self.last_blink = if expired {
            Instant::now()
                - std::time::Duration::from_millis(self.config.cursor_blink_ms as u64 + 1)
        } else {
            // ⛔ 「아직 안 지났다」도 **세운다**. 안 세우면 앞 호출이 남긴 시각을 물려받아
            //    시험이 자기 앞줄에 진다(실측: 껐을 때의 틱이 시계를 뒤로 민 채로 뒀다).
            Instant::now()
        };
        self.tick_cursor_blink()
    }

    fn apply_action(&mut self, action: Action) -> bool {
        match action {
            Action::Quit => {
                self.quit_requested = true;
                return true;
            }
            // 창 일이라 여기서 못 한다 — `ViewContext` 를 쥔 자리로 한 프레임 넘긴다.
            Action::ToggleFullscreen => {
                self.fullscreen_requested = true;
                return true;
            }
            // 클립보드 읽기도 창 일이다(같은 자리·같은 모양) — pytmux-159·363.
            // 팔레트의 `paste-clipboard` 와 `Ctrl+V` 가 **여기서 만난다**.
            Action::PasteClipboard => {
                self.paste_requested = true;
                return true;
            }
            Action::ToggleClaudeDetail => {
                self.screens.open(Screen::ClaudeDetail);
                return true;
            }
            Action::ShowKeys => {
                self.screens.open(Screen::Keys);
                return true;
            }
            Action::ShowTabs => {
                // ★ 첫 선택은 **다음 탭**이다(§10-21ⓔ2 · 정본과 같다) — 뜻은 core 가
                //   정한다. 탭이 하나뿐이면 안 연다(고를 것이 없는 목록은 "아무 일도
                //   안 일어난다"와 같다).
                let rows: Vec<(bool, bool)> = self
                    .state
                    .switcher_rows()
                    .iter()
                    .map(|r| (r.window.is_some() && r.pane.is_none(), r.active))
                    .collect();
                if !self.screens.open_tab_switcher(&rows) {
                    return true;
                }
                // 패널 하위행은 tree 회신이 **뒤늦게** 채운다(파이썬과 같다 — 열림은
                // 즉시, esc Tab Enter 리듬을 지킨다).
                self.pending.push(Outgoing::Command(Command::RequestTree));
                return true;
            }
            // 트리·버퍼는 **서버에 청해야** 그릴 것이 생긴다. 화면은 바로 열고(안 열면
            // 사용자는 키가 안 먹은 줄 안다) 회신이 오면 그 화면이 채워진다.
            // ★ 되돌릴 수 없는 것 앞에는 확인을 세운다(파이썬 클라와 같다). G1a 에서는
            // 이 키들이 바로 나갔고, 그때는 파이썬보다 위험했다.
            Action::KillPane => {
                self.screens.confirm(Prompt::KillPane);
                return true;
            }
            Action::KillTab => {
                self.confirm_kill_tab();
                return true;
            }
            Action::RenameTab => {
                let now = self
                    .state
                    .tabs()
                    .tabs
                    .iter()
                    .find(|t| t.active)
                    .map(|t| t.name.clone())
                    .unwrap_or_default();
                self.screens.ask(Prompt::RenameTab, &now);
                return true;
            }
            Action::MoveTab => {
                self.screens.ask(Prompt::MoveTab, "");
                return true;
            }
            // 캔버스 위에서 블록 하나를 고른다(pytmux-18). 들어갈지 말지는 **여기서**
            // 정한다 — core 는 그 패널에 블록이 있는지 모른다(`ModeState::enter_block`).
            Action::SelectBlocks => {
                self.enter_block_select();
                return true;
            }
            Action::SearchScrollback => {
                // 어느 입구로 왔든(스크롤 모드 `/`·메뉴) 물음이 닫힌 뒤는 스크롤
                // 모드다(파이썬 `_prompt_done` 의 `mode = "scroll"` — TUI 와 같다).
                self.mode.enter_scroll();
                self.screens.ask(Prompt::SearchScrollback, "");
                return true;
            }
            // 전역 검색(pytmux-27). `SearchScrollback` 과 갈리는 자리 — 이건 스크롤
            // 모드로 안 들어간다(결과 판이 따로 열리고, 점프할 때 그때 스크롤 모드다).
            Action::SearchAll => {
                self.screens.ask(Prompt::SearchAll, "");
                return true;
            }
            Action::ShowCommands => {
                self.screens.open_palette();
                return true;
            }
            // 서버가 채우는 탭(버전)이 있어 **열면서 함께 청한다**(TUI 와 같다).
            Action::ShowInfoTabs => {
                self.screens.open_info_tabs();
                // 커서를 **첫 내용 줄**에 놓는다(정본과 같다 — 동작 단추 위가 아니다).
                self.settle_info_tabs();
                self.pending
                    .push(Outgoing::Command(Command::RequestVersion));
                return true;
            }
            // ★ **프롬프트 인계**(패리티 G9c) — TUI 와 같은 값을 쓴다(판정은 proto).
            Action::ShowCompose => {
                let seed = self.current_prompt_text();
                self.screens.open_compose(&seed);
                return true;
            }
            Action::ShowSettings => {
                self.screens.open(Screen::Settings);
                return true;
            }
            // 커서 판(pytmux-375). 값은 설정 화면과 **같은 다섯**이고 새로 지은 것은
            // 화면이다 — 왜 화면이 따로 서는지는 `render_cursor` 가 적는다.
            Action::ShowDebugStats => {
                self.screens.open(Screen::DebugStats);
                return true;
            }
            Action::ShowCursor => {
                self.screens.open(Screen::Cursor);
                return true;
            }
            // 좌하단 `[자동재개]` 표식을 눌러 여는 판(pytmux-183). **뒤집지 않는다** —
            // 그 이유는 `Screen::Autoresume` 문서에 있다.
            Action::ShowAutoresume => {
                self.screens.open(Screen::Autoresume);
                return true;
            }
            // 패널로 **바이트를 보내는** 액션이다(키 입력과 같은 길). 명령이 아니다.
            Action::RenamePane => {
                let now = self.state.flags().pane_title.clone();
                self.screens.ask(Prompt::RenamePane, &now);
                return true;
            }
            Action::ShowPaneNumbers => {
                self.state.toggle_pane_numbers();
                return true;
            }
            // 목표 자리는 탭 목록을 봐야 안다 — 이미 끝이면 아무 일도 안 한다(같은
            // 자리로 옮기는 명령은 화면만 한 번 출렁이게 한다).
            Action::SwapTab => {
                self.screens.ask(Prompt::SwapTab, "");
                return true;
            }
            Action::PipePane => {
                self.screens.ask(Prompt::PipePane, "");
                return true;
            }
            Action::JoinPane => {
                self.screens.ask(Prompt::JoinPane, "");
                return true;
            }
            Action::DisplayMessage => {
                self.screens.ask(Prompt::DisplayMessage, "");
                return true;
            }
            // 설정 파일을 다시 읽는다. 서버와 무관하고 **이번 판에 바로 먹는다**.
            Action::SourceFile => {
                self.config = base::Config::load();
                self.mode.set_prefix(self.config.prefix);
                self.state
                    .set_inactive_dim(self.config.inactive_dim, self.config.inactive_dim_ratio);
                        self.state.note_notice(t("설정을 다시 읽었다"));
                return true;
            }
            // 지금 탭이 원격이 아니면 **합칠 대상이 없다** — 빈 화면을 띄우는 대신
            // 왜 안 되는지 말한다(파이썬도 안내만 하고 끝낸다).
            Action::DisplayPopup => {
                self.screens.ask(Prompt::DisplayPopup, "");
                return true;
            }
            Action::RunShell => {
                self.screens.ask(Prompt::RunShell, "");
                return true;
            }
            Action::IfShell => {
                self.screens.ask(Prompt::IfShell, "");
                return true;
            }
            Action::MergeRemoteTab => {
                if self.state.active_remote_host().is_none() {
                    self.state.note_notice(t("지금 탭이 원격이 아니라 합칠 것이 없다"));
                    return true;
                }
                if self.state.merge_candidates().is_empty() {
                    self.state
                        .note_notice(t("같은 원격에 다른 탭이 없다"));
                    return true;
                }
                self.screens.open(Screen::MergeRemote);
                self.screens
                    .clamp_selection(self.state.merge_candidates().len());
                return true;
            }
            Action::RequestRestartCheck => {
                self.pending
                    .push(Outgoing::Command(Command::RequestRestartCheck));
                self.screens.open(Screen::RestartCheck);
                return true;
            }
            Action::RequestVersion => {
                self.pending
                    .push(Outgoing::Command(Command::RequestVersion));
                self.screens.open(Screen::Version);
                return true;
            }
            Action::BindKey => {
                self.screens.ask(Prompt::BindKey, "");
                return true;
            }
            Action::UnbindKey => {
                self.screens.ask(Prompt::UnbindKey, "");
                return true;
            }
            Action::SetOption => {
                self.screens.ask(Prompt::SetOption, "");
                return true;
            }
            Action::ShowOptions => {
                self.screens.open(Screen::Settings);
                return true;
            }
            Action::SetHook => {
                self.screens.ask(Prompt::SetHook, "");
                return true;
            }
            Action::ShowHooks => {
                self.screens.open(Screen::Hooks);
                return true;
            }
            Action::ShowCommandOptions(command) => {
                // 표에 없는 이름이면 **아무 일도 안 한다**(빈 폼을 띄우지 않는다).
                return self.screens.open_options(command);
            }
            Action::SendKeys => {
                self.screens.ask(Prompt::SendKeys, "");
                return true;
            }
            Action::ShowLayouts => {
                self.screens.open(Screen::Layouts);
                self.screens
                    .clamp_selection(base::LAYOUT_PRESETS.len());
                return true;
            }
            Action::SaveTabLayout => {
                self.screens.ask(Prompt::SaveTabLayout, "");
                return true;
            }
            Action::LoadTabLayout(new) => {
                self.screens.ask(
                    if new { Prompt::LoadTabLayoutNew } else { Prompt::LoadTabLayout },
                    "",
                );
                return true;
            }
            // §10-21ⓓ — 화면에서 뺀 요약 구역의 새 입구.
            Action::ShowSummary => {
                self.screens.open(Screen::Summary);
                return true;
            }
            Action::ShowNotices => {
                self.screens.open(Screen::Notices);
                return true;
            }
            Action::ShowMenu => {
                self.screens.open(Screen::Menu);
                self.screens.clamp_selection(self.screens.menu_rows().len());
                return true;
            }
            Action::SendEscape => {
                self.pending.push(Outgoing::Input(vec![0x1b]));
                return true;
            }
            Action::SendBacktick => {
                self.pending.push(Outgoing::Input(b"`".to_vec()));
                return true;
            }
            Action::ToggleInactiveDim => {
                self.flip_config("inactive-dim");
                return true;
            }
            // 글자 배율(§10-21ⓐ) — 걸음·끝값의 주인은 core 다. 여기는 지금 값에서 한
            // 걸음 옮겨 **설정 파일에 적는** 일만 한다(`set_number` 가 그 길이고,
            // 그래서 설정 화면에서 고친 것과 키로 고친 것이 한 자리로 모인다).
            Action::FontScale { up } => {
                let next = base::config::font_scale_step(self.config.font_scale, up);
                // 끝에 닿아 값이 그대로면 **말해 준다** — 아무 일도 안 일어나면
                // 사용자는 키가 안 먹은 줄 안다(끝에서 멈추는 설계라 더 그렇다).
                //
                // ⚠ 문구를 **낱말로 조립하지 않는다**(2026-08-02p 교훈): 방향을 인자로
                //   넘기면 그 낱말만 다른 언어로 남는다. 문장을 통째로 둘 둔다.
                if (next - self.config.font_scale).abs() < f32::EPSILON {
                    let scale = format!("{next:.1}");
                    self.state.note_notice(if up {
                        tf("글자 크기: {scale}× — 더 키울 수 없다", &[("scale", &scale)])
                    } else {
                        tf("글자 크기: {scale}× — 더 줄일 수 없다", &[("scale", &scale)])
                    });
                    return true;
                }
                self.set_number("font-scale", next);
                self.note_font_scale();
                return true;
            }
            Action::FontScaleReset => {
                self.set_number("font-scale", 1.0);
                self.note_font_scale();
                return true;
            }
            // 달력도 시계와 같은 길이다(Tier B) — 우리는 **켠 사실**만 올리고 그림·
            // 넘겨 본 달은 서버가 든다. 손으로 옮긴 달력 한 벌이 여기서 사라졌다.
            Action::ToggleCalendar => {
                self.push_overlay_toggle("calendar");
                return true;
            }
            // 로케일도 클라 안에서만 끝난다(per-user — `base::i18n` 모듈 문서).
            // 적용 → 영속 → 피드백. 다음 렌더부터 표면이 그 언어다.
            Action::SetLang(loc) => {
                let loc = base::i18n::set_locale(loc);
                base::i18n::persist(loc);
                let name = if loc == "en" { "English" } else { "한국어" };
                self.state.note_notice(base::i18n::tf(
                    "언어: {name}",
                    &[("name", name)],
                ));
                return true;
            }
            // ★ 시계를 **서버가 그린다**(설계 Tier B · P3). 어느 패널이 시계 모드인지는
            //   여전히 이 클라만 아는 사실이라, 그 사실을 올려 보낸다(§4.4) — 무엇을
            //   어떻게 그릴지는 플러그인이 정한다. 그림은 `plugin_cells` 로 온다.
            Action::ToggleClock => {
                self.push_overlay_toggle("clock");
                return true;
            }
            // 명시적 켜기/끄기(§10-21ⓡ) — 토글과 **같은 길**로 보낸다. 판정(멱등·상호
            // 배타)은 core 가 하고 우리는 그 결과를 프레임으로 옮기기만 한다.
            Action::SetOverlay { name, on } => {
                self.push_overlay_set(name, on);
                return true;
            }
            // ★ 서버가 이미 받고 있던 플러그인 토글(pytmux-35). 값을 안 실으면 서버가
            //   뒤집는다 — 현재값의 권위는 서버이고, 결과는 다음 status 로 따라온다.
            //   종전에는 이 이름들이 `plugin_open` 으로 가서 "화면 스펙 없음"으로 거절됐다.
            Action::PluginToggle { action } => {
                self.pending.push(Outgoing::Command(Command::PluginToggle {
                    action,
                    value: None,
                }));
                return true;
            }
            Action::PluginDo { action } => {
                self.pending
                    .push(Outgoing::Command(Command::PluginDo { action }));
                return true;
            }
            // Claude 한도 오버레이도 같은 길이다(Tier B) — 그림·데이터는 서버가 든다
            // (`/usage` 스크랩은 서버가 하고, 한도 막대 줄도 서버가 만든다). 우리는
            // **켠 사실**만 올린다. 오버레이 이름은 플러그인 디렉토리 이름 그대로다.
            Action::ToggleUsageView => {
                self.push_overlay_toggle("claude-token-usage-view");
                return true;
            }
            Action::ShowPlugins => {
                self.screens.open(Screen::Plugins);
                self.screens.clamp_selection(self.state.plugins().len());
                return true;
            }
            // ★ 확인 없이 나가는 길이 없어야 한다 — kill-server 는 **다른 클라의 작업까지**
            // 내린다(이 저장소의 사고 기록이 정확히 그 부류다).
            Action::KillServer => {
                self.screens.confirm(Prompt::KillServer);
                return true;
            }
            // ★ 재시작 둘은 확인 화면을 **바로** 세우지 않는다 — 먼저 드라이런을 묻고
            // (파이썬 `begin_restart`), 안전하면 확인 없이 진행하고 실패하면 **무엇이
            // 실패했는지 적어** 다시 묻는다. 종전 우리 restart-server 는 그 점검을
            // 건너뛰어 파이썬보다 위험했다.
            Action::RestartServer => {
                self.begin_restart(base::restart::Kind::Server);
                return true;
            }
            Action::RestartAll => {
                self.begin_restart(base::restart::Kind::All);
                return true;
            }
            // 소켓을 다시 세운다. GUI 는 링크를 **뷰가** 들고 있어 여기서 바로 한다
            // (TUI 는 루프가 들고 있어 표식만 세운다 — 그 비대칭은 링크 소유의 차이다).
            Action::Reconnect => {
                self.reconnect_now();
                return true;
            }
            Action::RemoteAttach => {
                self.screens.ask(Prompt::RemoteAttach, "");
                return true;
            }
            Action::RemoteNewTab => {
                self.screens.ask(Prompt::RemoteNewTab, "");
                return true;
            }
            Action::RemoteDetach => {
                self.screens.ask(Prompt::RemoteDetach, "");
                return true;
            }
            Action::ShowTree => {
                self.screens.open(Screen::Tree);
                self.pending
                    .push(Outgoing::Command(Command::RequestTree));
                return true;
            }
            Action::ShowBuffers => {
                self.screens.open(Screen::Buffers);
                self.pending
                    .push(Outgoing::Command(Command::RequestBuffers));
                return true;
            }
            _ => {}
        }
        // 탭을 고르는 액션 셋(번호·첫·마지막)은 탭바를 봐야 정해진다 — 판정은 proto
        // 한 곳이 하고(`action_to_command_with_tabs`) 뷰는 탭바만 빌려준다. TUI 도
        // 같은 함수를 부른다.
        match action_to_command_with_tabs(action, self.state.tabs()) {
            Some(command) => {
                let command = self.with_default_path(command);
                self.pending.push(Outgoing::Command(command));
                true
            }
            None => false,
        }
    }

    /// 입력기가 **확정한 글자**(한글 등). 반환값은 "다시 그려야 하는가".
    ///
    /// # 왜 키가 아니라 여기로 오나
    ///
    /// 한글은 자판 한 번이 글자 하나가 아니다 — `ㄱ`+`ㅏ` 가 조합돼 `가` 가 되고, 그
    /// 결과가 문자열 하나로 온다. 키 경로로 처리하면 조합 중인 자모가 그대로 패널에
    /// 찍히고(`rk`), 정작 완성된 글자는 아무 데도 안 간다.
    ///
    /// # 왜 붙여넣기가 아니라 입력인가
    ///
    /// 이건 **사람이 친 것**이다. `paste` 로 보내면 서버가 bracketed paste 로 감쌀 수 있고,
    /// 그러면 패널 안 프로그램이 "붙여넣기"로 취급해 자동완성·히스토리가 다르게 돈다.
    ///
    /// # 모드
    ///
    /// 평소 모드에서만 패널로 보낸다. 명령 모드에서 확정된 글자는 명령이 아니므로 **버린다**
    /// — 거기서 패널로 흘리면 사용자가 pytmux 에게 말하는 중에 셸에 글자가 찍힌다.
    pub fn handle_typed(&mut self, text: &str) -> bool {
        match Self::typed_target(
            self.mode.mode(),
            self.screens.top().is_some(),
            self.session_editing(),
            text,
        ) {
            TypedTo::Drop => false,
            // ★ **한글은 이 길로만 온다**(pytmux-3). 조합이 끝난 글자는 키가 아니라
            //   확정 문자열로 오므로, 이 갈래가 없으면 편집 중에 친 한글이 그대로
            //   패널(셸)로 샌다 — 정본이 `on_paste` 를 같은 이유로 잡았다.
            TypedTo::SessionEdit => self.session_edit_typed(text),
            TypedTo::Pane => {
                self.pending.push(Outgoing::Input(text.as_bytes().to_vec()));
                true
            }
            // 판이 열려 있으면 **그 판의 입력처**로 넣는다(§10-21ⓜ2). 낱자로 풀어
            // 보내는 이유: 판의 입력은 `Key::Char` 를 받는 자리 하나뿐이고, 그래야
            // 필터·프롬프트·작성창이 **같은 길**로 글자를 먹는다.
            TypedTo::Screen => {
                let mut any = false;
                for ch in text.chars() {
                    any |= self.handle_key(Key::Char(ch), Mods::NONE);
                }
                any
            }
        }
    }

    /// 조합 중인 글자를 **들고만 있는다**(§10-21ⓞ2 ⑵). 그림은 `overlay_preedit` 가 그린다.
    ///
    /// # 왜 패널로 안 보내나
    ///
    /// 조합 중 문자열은 매 자판마다 통째로 바뀐다(`ㅎ` → `하` → `한`). 그대로 흘리면 셸이
    /// 자모를 **글자로** 받아 `치명ㄷ` 부류가 된다 — `ime.rs` 머리말이 그 사고를 적어 뒀다.
    /// 확정되면 상류가 `TypedCharacters` 로 따로 주고, 그것만 평소 경로로 나간다.
    ///
    /// # 왜 모드를 안 보나
    ///
    /// `handle_typed` 는 모드에 따라 패널/판/버림으로 갈리지만, 이건 **아무 데도 안 보낸다**.
    /// 어느 모드에서 조합하든 "지금 무엇을 치고 있나"는 보여야 하고, 그 그림이 향하는 곳은
    /// 커서 자리 하나뿐이다.
    pub fn handle_preedit(&mut self, text: &str) -> bool {
        if self.preedit == text {
            return false;
        }
        // ★ 창을 볼 수 없는 자리에서 이 경로의 관측점이다(붙여넣기와 같은 판단) — 조합은
        //   1초 안에 확정돼 스크린샷 타이밍에 걸리므로, 처음 배선을 확인한 것은 이 줄이었다.
        //   그 뒤로 두 구멍을 오라클로 막았다: 얹는 **호출부**는 `composite_for_paint` 로
        //   갈라 재고, 상류 **구독**(`on_marked_text`)은 소스 오라클이 잡는다.
        log::debug!("조합 중: {:?}({}자)", text, text.chars().count());
        text.clone_into(&mut self.preedit);
        true
    }

    /// **화면에 실제로 그려질** 캔버스 — 서버 합성 위에 클라가 얹는 것까지 포함한다.
    ///
    /// `render` 가 캔버스를 얻는 **유일한 길**이다. 갈라 둔 이유는 창 없이 잴 수 있는
    /// 자리를 만들기 위해서다: 종전엔 얹는 호출이 `render` 안에 있어서, 그 한 줄을 지워도
    /// 오라클이 전부 초록이었다(오라클이 `overlay_preedit` 를 **직접** 불렀다). 루트
    /// CLAUDE.md 가 말하는 *"표시 기능은 호출부까지 단언 — 뮤테이션에 '호출 제거'를
    /// 포함할 것"* 의 실물이다.
    ///
    /// 클라만 아는 것을 여기 더 얹게 되면(예: 조합 중인 글자) 그 배선도 함께 잡힌다.
    pub(crate) fn composite_for_paint(&self) -> Option<proto::canvas::Canvas> {
        let mut canvas = self.state.composite()?;
        // 조합 중인 글자는 **서버 화면에 없다**(확정 전이라 안 간다). 합성이 끝난 캔버스
        // 위에 클라가 직접 얹는다 — 넓은 글자 뒤 칸도 `put` 이 잡아 준다.
        self.overlay_preedit(&mut canvas);
        Some(canvas)
    }

    /// 조합 중인 글자를 **커서 자리에 겹쳐** 캔버스에 얹는다.
    ///
    /// 서버 화면에는 이 글자가 없다(확정 전이라 안 간다) — 그릴 사람은 클라뿐이다.
    /// 밑줄로 "아직 확정 안 됐다"를 말한다(정본 터미널의 조합 표시와 같은 관례).
    /// 패널 오른쪽 끝을 넘기면 거기서 자른다 — 넘겨 쓰면 옆 패널을 침범한다.
    fn overlay_preedit(&self, canvas: &mut proto::canvas::Canvas) {
        if self.preedit.is_empty() {
            return;
        }
        let Some(cur) = self.cursor_cell() else {
            return;
        };
        // 오른쪽 경계는 **활성 패널**의 것이다(화면 폭이 아니다 — 좌우 분할에서 옆 패널을
        // 덮어쓴다). 못 얻으면 캔버스 폭으로 떨어진다.
        let (cols, _) = canvas.size();
        let right = self
            .state
            .active_pane()
            .and_then(|p| self.state.pane_rect(p))
            .map_or(cols, |(px, _, w, _)| (px as usize + w as usize).min(cols));
        // "아직 확정 안 됐다"를 **반전 + 밑줄**로 말한다.
        //
        // 원래 고르려던 것은 밑줄 하나였다(터미널의 조합 표시 관례). 그런데 그때 이
        // 클라는 밑줄을 **아예 안 그렸고**(pytmux-123 — `colors()` 가 fg·bg·reverse 만
        // 봤다), 안 그려지는 표시를 고르면 조합 글자가 확정 글자와 똑같아 보인다. 그래서
        // 반전으로 넘기고 `underline` 은 세워만 뒀다 — *"나중에 그리게 되면 그때 뜻이
        // 맞는다"*. 그 나중이 왔다(오버레이가 SGR 4 를 긋는다). 반전은 **남긴다**: 이미
        // 그 모습으로 받아들여진 표시라 지금 빼면 조합 표시가 조용히 옅어진다.
        let style = proto::style::CellStyle {
            reverse: true,
            underline: true,
            ..Default::default()
        };
        let mut x = cur.x as usize;
        for ch in self.preedit.chars() {
            let cells = proto::compose::char_advance(ch);
            // 폭 0 은 칸을 안 먹는다(pytmux-389) — 캔버스 셀은 글자 하나라 놓을 데가 없다.
            if cells == 0 {
                continue;
            }
            if x + cells > right {
                break;
            }
            canvas.put(x, cur.y as usize, ch, style.clone());
            x += cells;
        }
    }

    /// 확정된 글자를 패널로 보낼 것인가.
    ///
    /// 창 없이 물을 수 있게 갈라 둔다 — 이 판정이 틀리면 **명령 모드에서 셸에 글자가
    /// 찍히거나**(넓을 때) **한글이 통째로 사라진다**(좁을 때). 둘 다 조용하다.
    /// # 왜 셋인가 (§10-21ⓜ2)
    ///
    /// 종전 판정은 **둘**이었다(패널로 보내거나 버리거나). 그래서 제보가 났다:
    /// *"`esc` `:` 상태에서 한글을 아예 못 친다"* — 판이 열려 있어도 확정된 글자가
    /// **버려졌다**. 영문이 되던 이유는 ASCII 가 키 이벤트로도 오기 때문이고, 한글은
    /// 조합이 끝난 뒤 **확정 글자로만** 온다.
    ///
    /// 넓게 푸는 것(모드를 안 보고 다 패널로)은 반대쪽 결함을 살린다 — 명령 모드에서
    /// 셸에 글자가 찍힌다. 그래서 **판이 열렸나**를 한 칸 더 본다.
    ///
    /// # 왜 넷이 됐나 (pytmux-3)
    ///
    /// 상태줄 세션 이름의 **제자리 편집**은 판을 안 띄운다 — 그래서 `screen_open` 이
    /// 거짓이고 모드도 `Normal` 이라, 종전 표에서는 확정된 글자가 **패널로 갔다**.
    /// 편집 중에 한글을 치면 셸에 찍히는 그 갈래다(영문은 키 이벤트로도 와서 안 새고
    /// 한글만 새는 부류라, 눈으로만 보면 "한글이 안 된다"로 보인다).
    fn typed_target(
        mode: InputMode,
        screen_open: bool,
        session_editing: bool,
        text: &str,
    ) -> TypedTo {
        if text.is_empty() {
            return TypedTo::Drop;
        }
        if screen_open {
            return TypedTo::Screen;
        }
        if session_editing {
            return TypedTo::SessionEdit;
        }
        if mode == InputMode::Normal {
            TypedTo::Pane
        } else {
            // 판 없는 모드 키(esc·prefix·스크롤)에서 확정된 글자는 명령이 아니다 —
            // 패널로 흘리면 사용자가 pytmux 에게 말하는 중에 셸에 글자가 찍힌다.
            TypedTo::Drop
        }
    }

    /// 붙여넣기. **마커는 클라가 감싸지 않는다** — 감싸도 되는지는 패널 안 프로그램이
    /// 정하고(DECSET 2004) 그 상태를 아는 것은 PTY 출력을 파싱하는 서버뿐이다.
    /// 복사한 글에서 **앱이 접은 줄바꿈**을 되돌린다(설정 `copy-unwrap`).
    ///
    /// 기하를 모르면(요청을 우리가 안 보냈거나 그 사이 배치가 바뀌면) 손대지 않는다 —
    /// 판정 근거 없이 이어붙이면 사용자가 고른 그대로가 아니게 된다.
    pub fn unwrap_copied(&mut self, text: String) -> String {
        let geom = self.copy_geom.take();
        if !self.config.copy_unwrap {
            return text;
        }
        match geom {
            Some((width, first_col)) => {
                proto::unwrap_copy_text(&text, width, first_col)
            }
            None => text,
        }
    }

    // ── 블록 선택(pytmux-18) ────────────────────────────────────────────────
    //
    // 제보 그대로다: *"warp 처럼 「명령 하나 + 그 출력」을 블록으로 고를 수 있어야 한다"* —
    // ⑴ 고르기 ⑵ `↑`/`↓` 로 한 블록씩 ⑶ `Ctrl`+`C` 로 그 블록 전체 복사.
    //
    // ★ **재료는 이미 다 있었다.** 경계는 서버의 셸 통합(OSC 133)이 절대 행으로 알려
    //   주고(`proto::blocks`), 범위를 글로 바꾸는 것은 드래그 복사가 쓰는 `CopyRange`
    //   한 명령이 이미 한다. 없던 것은 **캔버스 위의 상호작용**뿐이라, 새로 만든 것도
    //   그것뿐이다(모드 하나 · 고른 자리 하나 · 강조 하나).

    /// 블록 선택 모드로 들어간다. 고를 것이 없으면 **안 들어가고 그렇다고 말한다**.
    ///
    /// # 왜 빈 목록에서 안 들어가나 (제보의 ⚠ 셋째)
    ///
    /// 블록 경계는 셸 통합(OSC 133)이 보내 주는 것이라, 그 스크립트를 안 읽은 셸
    /// (`cmd.exe`·통합 전 세션)에는 블록이 **하나도 없다**. 그 패널에서 모드에 들여보내면
    /// 배지만 켜진 채 `↑`/`↓`·`Ctrl+C` 가 통째로 죽는다 — 사용자에게는 "이 기능이
    /// 고장났다"로 보이고, 진짜 원인(셸 통합)은 화면 어디에도 안 적혀 있다. 그래서
    /// **들어가는 대신 이유를 한 줄로 말한다.**
    ///
    /// ⚠ **그 한 줄이 패널마다 달라야 한다**(pytmux-21). Claude 패널의 경계는 OSC 133 이
    /// 아니라 화면 글의 프롬프트 마커에서 나오므로(`promptblocks.py`), 거기서 "셸 통합을
    /// 켜라"고 말하면 **고칠 수 없는 것을 고치라는 안내**가 된다 — 셸 통합을 아무리 깔아도
    /// Claude 는 OSC 를 안 보낸다. 그 패널에서 비어 있다는 것은 아직 프롬프트를 한 번도
    /// 안 보냈다는 뜻이고, 그것이 사용자가 할 수 있는 일이다.
    ///
    /// # 첫 선택이 왜 마지막 블록인가
    ///
    /// 방금 친 명령의 출력을 집으려는 것이 이 기능의 첫 쓰임이고, 그것이 목록의 끝이다.
    /// 첫 블록에서 시작하면 스크롤백이 긴 패널에서 `↑`을 수십 번 눌러야 한다.
    fn enter_block_select(&mut self) -> bool {
        let Some(pane) = self.state.active_pane() else {
            return false;
        };
        let count = self.state.blocks(pane).len();
        if count == 0 {
            let why = if self.state.is_claude_pane(pane) {
                t("이 패널에는 아직 고를 턴이 없다 — 프롬프트를 한 번 보내면 턴 단위로 골라진다")
            } else {
                t("이 패널에는 블록이 없다 — 셸 통합(OSC 133)이 명령 경계를 알려 줘야 생긴다")
            };
            self.note_flash(why.to_owned(), Severity::Warn);
            return false;
        }
        self.mode.enter_block();
        self.block_pick = Some((pane, count - 1));
        true
    }

    /// 블록 모드를 벗어났으면 고른 것도 버린다. 모드가 풀리는 **모든 길**이 여기를 지난다.
    ///
    /// 포커스가 **다른 패널로 옮겨간 것**도 벗어난 것으로 친다. 블록 목록은 패널마다
    /// 따로라, 그때 모드를 붙잡고 있으면 `↑`/`↓` 가 지금 보고 있지 않은 패널의 블록을
    /// 옮긴다 — 화면에는 아무 반응이 없고 `Ctrl+C` 만 엉뚱한 글을 담는다.
    fn drop_block_pick_unless_selecting(&mut self) {
        let stale = self
            .block_pick
            .is_some_and(|(pane, _)| self.state.active_pane() != Some(pane));
        if stale {
            self.mode.reset();
        }
        if self.mode.mode() != InputMode::Block {
            self.block_pick = None;
        }
    }

    /// 블록 모드 안의 키 하나. 반환값은 "다시 그릴 것이 있나".
    fn block_key(&mut self, what: base::keys::BlockKey) -> bool {
        use base::keys::BlockKey;
        let Some((pane, index)) = self.block_pick else {
            return false;
        };
        let count = self.state.blocks(pane).len();
        if count == 0 {
            return false;
        }
        // 목록이 상한에서 잘리거나 패널이 바뀌었을 수 있다 — 자리를 **읽을 때마다** 접는다.
        let index = index.min(count - 1);
        match what {
            // 목록은 오래된 것 → 최근 순이고 화면도 그 순서로 아래로 흐른다. 그래서
            // `↓`(Next)가 곧 **더 최근**이다 — 화면에서 아래로 가는 것과 같은 방향이라야
            // 손이 안 어긋난다.
            BlockKey::Next => {
                if index + 1 >= count {
                    return false;
                }
                self.block_pick = Some((pane, index + 1));
                true
            }
            BlockKey::Prev => {
                if index == 0 {
                    return false;
                }
                self.block_pick = Some((pane, index - 1));
                true
            }
            BlockKey::Copy => self.copy_selected_block(),
        }
    }

    /// 고른 블록 전체(명령 + 그 출력)를 복사한다.
    ///
    /// **드래그 복사와 같은 길**이다 — 같은 `CopyRange` 를 보내고, 회신
    /// (`ServerMessage::Selection`)이 오면 접힘 되돌리기·페이스트 버퍼·클립보드·
    /// "N자 복사됨" 한 줄까지 그 경로가 그대로 한다. 여기서 따로 클립보드를 건드리면
    /// 두 복사가 서로 다른 규칙(`copy-unwrap` 등)을 타기 시작한다.
    ///
    /// 열 범위가 `0..w-1` 인 이유: 블록은 **줄 단위**다. 서버의 추출
    /// (`model.Pane.extract_range`)은 첫 줄을 `x0` 부터, 끝 줄을 `x1` 까지 뽑으므로
    /// 줄 전체를 원하면 패널 폭 끝을 준다(넘겨도 서버가 클램프한다).
    fn copy_selected_block(&mut self) -> bool {
        let Some((pane, index)) = self.block_pick else {
            return false;
        };
        let Some((y0, y1)) = self.block_rows(pane, index) else {
            return false;
        };
        let Some((_, _, w, _)) = self.state.pane_rect(pane) else {
            return false;
        };
        // 접힘을 되돌릴 기하 — 드래그 복사가 재는 것과 같은 값이다(폭, 첫 열).
        self.copy_geom = Some((w as usize, 0));
        self.pending.push(Outgoing::Command(Command::CopyRange {
            pane,
            y0,
            x0: 0,
            y1,
            x1: w.saturating_sub(1),
        }));
        true
    }

    /// 이 패널의 `index` 번째 블록이 차지하는 **절대 행 범위**(양끝 포함).
    ///
    /// 판정은 proto 한 곳이 한다([`proto::blocks::row_span`]) — 강조를 그리는 자리와
    /// 복사할 범위를 정하는 자리가 각자 세면, 화면에 밝은 것과 클립보드에 담기는 것이
    /// 조용히 어긋난다.
    fn block_rows(&self, pane: i64, index: usize) -> Option<(usize, usize)> {
        let bottom = self.state.pane_live_bottom(pane)?;
        proto::blocks::row_span(self.state.blocks(pane), index, bottom)
    }

    /// 고른 블록을 캔버스 어디에 강조할 것인가 — **뷰포트에 걸친 부분만**.
    ///
    /// 창 없이 물을 수 있는 관측점이다(`scroll_hints` 와 같은 자리·같은 이유).
    ///
    /// # 왜 잘라 내나
    ///
    /// 블록은 스크롤백 좌표라 화면보다 길 수 있다(수백 줄짜리 빌드 로그가 흔하다).
    /// 안 자르면 강조가 패널 밖으로 새어 이웃 패널·크롬 위에 그려진다. 통째로 화면
    /// 밖이면 `None` — 그릴 것이 없다는 뜻이지 선택이 풀린 것은 아니다.
    fn block_mark(&self) -> Option<crate::splitter::BlockPick> {
        // 판이 떠 있으면 안 그린다 — 그때 키는 그 판의 것이라, 강조가 남아 있으면
        // "여기서 ↑↓ 가 블록을 옮긴다"는 거짓말이 된다(`cursor_cell` 과 같은 규칙).
        if self.screens.top().is_some() {
            return None;
        }
        let (pane, index) = self.block_pick?;
        // 포커스가 옮겨갔으면 안 그린다. 모드를 푸는 것은 다음 키가 하고
        // ([`drop_block_pick_unless_selecting`](Self::drop_block_pick_unless_selecting)),
        // 여기는 `&self` 라 고칠 수 없다 — 그림만은 즉시 사실과 맞춘다.
        if self.state.active_pane() != Some(pane) {
            return None;
        }
        let (y0, y1) = self.block_rows(pane, index)?;
        let top = self.state.pane_top(pane)?;
        let (px, py, w, h) = self.state.pane_rect(pane)?;
        let last = top + usize::from(h).saturating_sub(1);
        if y1 < top || y0 > last {
            return None;
        }
        let from = y0.max(top) - top;
        let to = y1.min(last) - top;
        Some(crate::splitter::BlockPick {
            x: px,
            y: py + from as u16,
            w,
            h: (to - from + 1) as u16,
        })
    }

    /// 원문을 `paste` 명령으로 넘겨 판정을 맡긴다(TUI·파이썬 클라와 같다).
    pub fn handle_paste(&mut self, text: &str) -> bool {
        self.mode.reset();
        self.drop_block_pick_unless_selecting();
        if text.is_empty() {
            return false;
        }
        // OS 네이티브 선택으로 긁으면 패널 테두리가 같이 딸려 온다 — 그대로 붙이면
        // 명령줄이 망가진다(설정 `strip-box-drawing`, 기본 켜짐).
        let text = if self.config.strip_box_drawing {
            proto::strip_box_drawing(text)
        } else {
            text.to_owned()
        };
        // ★ 작성창이 떠 있으면 붙여넣기는 **그 버퍼로** 간다. 안 그러면 팝업을 띄운 채
        // 붙여넣은 글이 뒤 셸에 찍힌다(파이썬 `_active_compose_screen` 과 같은 자리).
        if self.screens.paste_into_compose(&text) {
            return true;
        }
        self.pending.push(Outgoing::Command(Command::Paste { text }));
        true
    }

    /// OS 클립보드 한 장을 **알맞게** 붙여넣는다(`paste-clipboard` · pytmux-159).
    ///
    /// 정본 `paste_os_clipboard`/`_do_paste_clipboard` 와 **같은 차례**다:
    ///
    /// 1. **글자가 있으면 글자다.** 그림·파일 목록보다 먼저 본다 — 정본이 그 순서이고,
    ///    HTML 을 복사하면 창 계층이 글자와 그림을 **둘 다** 실어 주기 때문이다(그때
    ///    사용자가 기대하는 것은 글자다).
    /// 2. **그림이면 임시 파일로 떨구고 그 «경로»를 붙인다.** PTY 너머로 비트맵이 못 가서
    ///    정본이 정한 계약이다(`clip::save_image`). Claude Code CLI 등이 경로를 첨부
    ///    이미지로 읽는다.
    /// 3. **떨구지 못했으면 `Alt+V`(= `ESC v`) 한 벌**을 패널에 보낸다 — 안쪽 앱이 공유
    ///    클립보드를 **자기가** 읽게 하는 정본의 폴백이다. 작성창이 떠 있을 때는 그 길이
    ///    없으므로(그건 앱 전용이다) 안내만 남긴다.
    /// 4. 아무것도 없으면 **그렇다고 말한다.**
    ///
    /// ⛔ **말없이 끝나는 팔이 없다.** 제보(pytmux-364)의 핵심이 「무동작이라 클립보드가
    /// 빈 건지 클라가 못 받은 건지 서버가 안 넣은 건지 가를 수 없다」였다 — 붙였으면
    /// 로그가, 못 붙였으면 **화면에 한 줄**이 남는다.
    ///
    /// 인자로 **읽은 내용을 받는** 이유: 진짜 클립보드에 그림을 넣어야 잴 수 있는 함수는
    /// 헤드리스 러너가 못 잰다. 읽기는 창을 쥔 자리에 두고 판단만 여기로 뺀다.
    fn paste_from_clipboard(&mut self, content: ClipboardContent) -> bool {
        // ★ 창을 볼 수 없는 자리에서 이 경로의 **유일한 관측점**이다. 붙여넣기가 안 될 때
        //   갈라야 하는 층이 셋인데(⑴조합이 창까지 왔나 ⑵클립보드에 무엇이 있나 ⑶서버가
        //   패널에 넣었나) 화면만 보면 셋이 전부 "아무 일도 안 남"으로 똑같아 보인다.
        //   실제로 이 줄이 없어서 한 번 헤맸다(2026-07-28).
        log::info!(
            "붙여넣기 요청: 클립보드 {}자 · 그림 {}장",
            content.plain_text.chars().count(),
            content.images.as_ref().map_or(0, Vec::len)
        );
        if !content.plain_text.is_empty() {
            return self.handle_paste(&content.plain_text);
        }
        let image = content.images.as_ref().and_then(|images| images.first());
        let Some(image) = image else {
            self.state
                .note_notice(t("클립보드가 비어있거나 읽을 수 없음"));
            return true;
        };
        if let Some(path) = clip::save_image(&image.data, &image.mime_type) {
            // ★ **원격 탭이면 그 경로가 저쪽에 없다.** 파일은 이 상자에 생겼다 — 그대로
            //   붙이면 원격 앱이 "그런 파일 없음"이라 말하고, 사용자는 붙여넣기가 깨진 줄
            //   안다. 정본과 같이 `scp` 로 먼저 옮긴다(`_do_paste_clipboard`).
            if let Some(host) = self.state.active_remote_host() {
                self.send_image_to_remote(host.to_owned(), path);
                return true;
            }
            // 경로를 그대로 흘린다 — 작성창으로 갈지 패널로 갈지는 `handle_paste` 가
            // 이미 아는 일이다(정본 `_paste_image_path` 와 같은 갈림).
            self.state
                .note_notice(tf("클립보드 이미지 → 경로 붙여넣기: {path}", &[("path", &path)]));
            return self.handle_paste(&path);
        }
        // 떨구지 못했다 — 정본의 폴백으로 간다.
        if self.screens.editor().is_some() {
            self.state
                .note_notice(t("작성창엔 이미지를 넣을 수 없음(경로 저장 실패)"));
            return true;
        }
        self.pending.push(Outgoing::Input(vec![0x1b, b'v']));
        self.state.note_notice(t("이미지 붙여넣기 → 내부 앱(Alt+V)"));
        true
    }

    /// 원격 탭에 그림을 **올려 두고** 그 경로를 붙인다(`scp` · 정본과 같은 자리).
    ///
    /// 여기서 기다리지 않는 이유: `scp` 상한이 30초다. 이벤트 루프에서 부르면 창이 그동안
    /// 통째로 멈춘다 — 셸 명령을 스레드로 뺀 것과 **같은 규칙**이고(`spawn_shell`), 정본도
    /// 워커로 뺀다. 결과는 다음 펌프가 줍는다([`take_remote_image`](Self::take_remote_image)).
    fn send_image_to_remote(&mut self, host: String, local: String) {
        // 원격 자리는 파일 이름만 옮긴다(정본과 같은 `/tmp/<이름>`). 원격이 어떤 상자든
        // `/tmp` 는 있고, 이름을 그대로 두어야 사용자가 무엇을 붙였는지 알아본다.
        let name = std::path::Path::new(&local)
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| "pytmux-clip".to_owned());
        let remote = format!("/tmp/{name}");
        // 말없이 몇 초를 끌지 않는다 — 그 침묵이 곧 「아무 일도 안 난다」로 읽힌다.
        self.state.note_notice(t("클립보드 붙여넣기 중… 잠시만요"));
        let slot = self.paste_result.clone();
        std::thread::spawn(move || {
            let ok = clip::scp_to_remote(&host, &local, &remote);
            if let Ok(mut slot) = slot.lock() {
                // 실패하면 **로컬 경로**를 붙인다(정본과 같다) — 손에 쥔 것이 있어야
                // 사용자가 스스로 옮길 수 있다.
                *slot = Some(RemoteImage {
                    path: if ok { remote } else { local },
                    ok,
                });
            }
        });
    }

    /// 도착한 `scp` 결과를 붙인다. 붙였으면 `true`(= 다시 그릴 일이 있다).
    ///
    /// 실패도 붙이는 이유는 [`RemoteImage`] 문서에 있다 — 다만 **경고와 함께** 준다.
    fn take_remote_image(&mut self) -> bool {
        let Some(image) = self.paste_result.lock().ok().and_then(|mut s| s.take()) else {
            return false;
        };
        // ⚠ 원문을 **리터럴로** 부른다(변수로 넘기면 i18n 게이트의 사각지대에 들어가
        //   영어 짝이 빠져도 아무도 안 운다 — `i18n_coverage.rs` 머리말).
        let said = if image.ok {
            tf(
                "클립보드 이미지 → 원격 전송 후 경로 붙여넣기: {path}",
                &[("path", &image.path)],
            )
        } else {
            tf(
                "이미지 원격 전송 실패(scp 오류) — 로컬 경로 붙여넣기: {path}",
                &[("path", &image.path)],
            )
        };
        self.state.note_notice(said);
        self.handle_paste(&image.path);
        true
    }

    /// 스크롤 프레임 하나를 큐에 넣는다.
    ///
    /// 반 페이지가 여기서 줄 수가 된다 — 그 값을 아는 것은 캔버스를 가진 뷰뿐이다
    /// (core 는 UI 를 모른다). 파이썬 클라·TUI 와 같은 계산이다(`rows / 2`, 최소 1).
    /// 키로 하는 스크롤. **대상은 비운다** — 키에는 커서가 없으므로 "지금 보고 있는 것"이
    /// 활성 패널뿐이고, 그 판단은 서버가 한다(휠은 다르다 — [`wheel_scroll`](Self::wheel_scroll)).
    fn scroll(&mut self, amount: ScrollAmount) {
        let half = (self.state.composite().map_or(12, |c| c.size().1).max(2) as i32) / 2;
        let frame = match amount {
            ScrollAmount::Lines(n) => Scroll::by(n),
            ScrollAmount::HalfPageUp => Scroll::by(half),
            ScrollAmount::HalfPageDown => Scroll::by(-half),
            ScrollAmount::Top => Scroll::top(),
            ScrollAmount::Bottom => Scroll::bottom(),
        };
        self.pending.push(Outgoing::Scroll(frame.for_pane(None)));
    }

    /// 휠 한 칸이 실제로 보낼 프레임.
    ///
    /// # 왜 대상 패널을 채우나
    ///
    /// 키로 하는 스크롤과 **다른 규칙**이다: 키에는 커서가 없어서 "지금 보고 있는 것"이
    /// 활성 패널뿐이지만, 휠에는 커서가 있다. 분할된 화면에서 활성 패널만 굴리면 옆 패널을
    /// 보며 휠을 돌리는 사람은 **자기 눈앞이 아닌 곳**이 움직이는 것을 본다.
    ///
    /// 캔버스 밖(탭바·아래 요약 구역)이면 비운다 — 거기서 억지로 패널을 고르면 화면 끝
    /// 패널이 반응한다. 모르면 모른다고 하고 서버 판단(활성 패널)에 맡긴다.
    ///
    /// **프레임까지 만들어 돌려주는 이유**는 이 함수가 창 없이 물을 수 있는 마지막
    /// 자리이기 때문이다(사용자 결정: "로직은 밀고 뷰는 얇게"). 대상만 고르고 프레임
    /// 조립을 호출부에 남기면, 고른 대상을 프레임에 **안 싣는** 결함이 오라클 밖으로
    /// 빠져나간다. 좌표 판정 자체는 `proto` 의 [`SessionState::pane_at`] 한 곳이 갖는다.
    fn wheel_scroll(state: &SessionState, up: bool, at: Option<(u16, u16)>) -> Scroll {
        // 팝업이 떠 있으면 팝업이 굴러간다(모달 — `wheel_pane` 문서).
        let pane = state.wheel_pane(at);
        Scroll::by(if up { WHEEL_LINES } else { -WHEEL_LINES }).for_pane(pane)
    }

    /// 휠 한 칸. **모드와 무관하게** 굴러간다 — 손이 마우스에 있는 사람에게 모드를
    /// 요구하면 그건 스크롤이 안 되는 것과 같다.
    ///
    /// `at` 은 커서 아래 셀(캔버스 밖이거나 아직 못 재면 `None`).
    /// **커서 아래 앱이 휠을 원하면 그 앱에게 넘긴다.** less·htop 에서 휠이 pytmux 의
    /// 스크롤백을 움직이면, 사용자는 자기가 보던 문서 대신 그 프로그램의 화면 기록이
    /// 흘러가는 것을 본다. 단 **선택을 끌고 있는 동안은 넘기지 않는다** — 그때 휠은
    /// 선택을 늘리려는 동작이다(파이썬 클라·TUI 와 같은 우선순위).
    pub fn handle_wheel(&mut self, up: bool, at: Option<(u16, u16)>) -> bool {
        // 화면(팝업)이 떠 있으면 휠은 **그 목록**을 굴린다(N2) — 키 ↑/↓ 와 같은 길이라
        // 선택 이동·스크롤 규칙이 화면마다 갈리지 않는다.
        if self.screens.top().is_some() {
            let key = if up { Key::Up } else { Key::Down };
            return self.handle_key(key, Mods::default());
        }
        if self.selection.is_none()
            && let Some((x, y)) = at
            && self.forward_mouse(
                x,
                y,
                if up {
                    MouseKind::WheelUp
                } else {
                    MouseKind::WheelDown
                },
                0,
            )
        {
            return true;
        }
        let frame = Self::wheel_scroll(&self.state, up, at);
        self.pending.push(Outgoing::Scroll(frame));
        true
    }

    pub fn quit_requested(&self) -> bool {
        self.quit_requested
    }

    /// 쌓인 것을 **쌓인 순서 그대로** 서버로 보낸다.
    ///
    /// 큐를 두는 이유는 TUI 와 같다: 키 핸들러는 `&mut self` 만 있는 자리에서 불리고,
    /// 거기서 곧장 소켓에 쓰면 실패 처리와 순서가 핸들러마다 흩어진다.
    ///
    /// 보내기 실패는 **한 번만** 알린다 — 연결이 끊기면 매 프레임 같은 줄이 쏟아져
    /// 진짜 원인이 로그에서 묻힌다. 화면에는 `ended` 로 이미 드러난다.
    fn flush_outgoing(&mut self) {
        let mut failed = None;
        for item in self.pending.drain(..) {
            if let Err(reason) = self.link.send(&item) {
                failed = Some(reason);
                break;
            }
        }
        // 끊김 처리는 루프 **밖**이다 — `drain` 이 `self` 를 빌리고 있는 동안에는
        // 알림 이력(=`self` 전체)을 만질 수 없다.
        if let Some(reason) = failed
            && self.ended.is_none()
        {
            log::warn!("서버로 못 보냈다: {reason}");
            self.note_ended(reason);
        }
    }

    /// 그 패널이 아직 활성이 아니면 포커스를 옮긴다.
    ///
    /// 이미 활성인 패널을 다시 고르면 서버가 full 프레임을 한 번 더 보낸다 — 클릭할
    /// 때마다 화면 전체가 다시 오는 셈이라 거른다(TUI 와 같은 규칙).
    ///
    /// 팝업은 옮길 것이 없다 — 트리 밖이라 활성 패널이 될 수 없고, 떠 있는 동안 키는
    /// 이미 팝업으로 간다. 팝업 안 앱에 마우스를 넘길 때(shift 클릭) 이 가드가 없으면
    /// 헛 SelectPaneId 가 서버로 샌다(TUI 와 같은 가드).
    fn focus_pane(&mut self, id: i64) {
        if self.state.popup().map(|p| p.id) == Some(id) {
            return;
        }
        if self.state.active_pane() != Some(id) {
            self.pending
                .push(Outgoing::Command(Command::SelectPaneId { id }));
        }
    }

    /// 이 **누름**이 곧바로 패널 안 앱에게 갈 것인가(아니면 클릭/드래그 판정을 미룰까).
    ///
    /// 조건 셋이 모두 서야 넘어간다:
    ///
    /// 1. **평소 모드다.** 명령·스크롤 모드는 사용자가 pytmux 에게 말을 걸고 있는 중이고,
    ///    그 자리에서 마우스만 앱으로 새면 모드가 반쪽이 된다.
    /// 2. **그 자리의 앱이 마우스를 원한다.** 안 켠 앱에 리포트를 보내면 그 바이트가
    ///    프롬프트에 **글자로 찍힌다**.
    /// 3. **`mouse-drag-copy` 가 이 누름을 앱에게 주기로 했다**(`DragCopy::press_to_app`).
    ///
    /// # 3번이 왜 그 모양인가 (pytmux-19 · pytmux-422)
    ///
    /// `mouse-drag-copy` 가 켜져 있으면(기본) 평드래그는 **복사**이고, 앱에게 드래그를
    /// 줄 자리는 Shift 뿐이다 — 이 클라에는 마우스 캡처를 대신 풀어 줄 바깥 터미널이
    /// 없어서, 평드래그를 넘기면 화면의 글자를 꺼낼 방법이 사라진다.
    ///
    /// `shift` 값은 그 둘을 **맞바꾼다**(pytmux-422) — 평드래그가 앱에게 가고 복사는
    /// Shift 다. 위 걱정은 그대로 풀린다: 넘기는 것은 **마우스를 켠 앱 위**뿐이고
    /// (조건 2), 그 밖에서는 평드래그가 여전히 선택이다.
    ///
    /// ⛔ **세 값의 판정을 여기 다시 적지 마라** — `base::config::DragCopy` 한 곳이 쥔다.
    ///
    /// ⚠ 그렇다고 **평클릭까지** 앱에서 뺏으면 안 된다. 클릭은 드래그가 아니고 복사와
    /// 겹치지도 않는다 — 그 자리를 비워 뒀더니 패널 안 앱의 버튼·링크가 통째로 죽었다
    /// (제보: *"Claude 프롬프트 바를 눌러도 안 움직인다"*). 클릭은 **누를 때가 아니라
    /// 뗄 때** 정해지므로([`handle_mouse_up`](Self::handle_mouse_up)) 여기가 아니라
    /// 거기서 넘긴다. 정본도 같은 구조다(`clientwidgets.py` 의 `_sel_pending`).
    ///
    /// 끄면(`mouse-drag-copy off`) 정본처럼 누름부터 바로 넘어간다.
    ///
    /// 창 없이 물을 수 있게 순수 함수로 둔다 — 이 판정이 넓으면 남의 드래그가 사라지고
    /// 좁으면 마우스 1급 앱을 아예 못 쓴다. 둘 다 조용한 어긋남이다.
    fn press_goes_to_app(
        state: &SessionState,
        mode: InputMode,
        (x, y): (u16, u16),
        shift: bool,
        drag_copy: base::config::DragCopy,
    ) -> bool {
        drag_copy.press_to_app(shift)
            && mode == InputMode::Normal
            && state.mouse_pane_at(x, y).is_some()
    }

    /// 이 **클릭**(끌지 않고 뗀 것)이 패널 안 앱에게 갈 것인가.
    ///
    /// 드래그와 달리 복사와 다투지 않으므로 Shift 를 요구하지 않는다 — 앱의 버튼·링크·
    /// Claude 의 프롬프트 바가 사는 자리다(pytmux-19). 나머지 조건은 누름과 같다.
    fn click_goes_to_app(state: &SessionState, mode: InputMode, (x, y): (u16, u16)) -> bool {
        mode == InputMode::Normal && state.mouse_pane_at(x, y).is_some()
    }

    /// 이 좌표의 마우스를 받을 앱이 있는가(있으면 그 패널 id).
    ///
    /// **평소 모드에서만 넘긴다.** 명령·스크롤 모드는 사용자가 pytmux 에게 말을 걸고 있는
    /// 중이고, 그 자리에서 마우스만 앱으로 새면 모드가 반쪽이 된다(tmux 도 prefix·
    /// copy-mode 에서는 안 넘긴다). TUI 의 `mouse_target` 과 같은 규칙이다.
    fn mouse_target(&self, x: u16, y: u16) -> Option<i64> {
        (self.mode.mode() == InputMode::Normal)
            .then(|| self.state.mouse_pane_at(x, y).map(|p| p.id))
            .flatten()
    }

    /// 이 좌표의 앱에게 마우스 리포트를 넘긴다. 넘겼으면 `true`.
    ///
    /// 인코딩은 [`mouse::encode`](proto::mouse::encode) 한 곳이 안다 —
    /// 뷰가 바이트를 직접 만들면 두 클라가 서로 다른 리포트를 보내기 시작한다.
    fn forward_mouse(&mut self, x: u16, y: u16, kind: MouseKind, button: u8) -> bool {
        if self.mouse_target(x, y).is_none() {
            return false;
        }
        let Some(target) = self.state.mouse_pane_at(x, y) else {
            return false;
        };
        let Some(data) = mouse::encode(target.mode, target.rect, x, y, kind, button) else {
            return false;
        };
        self.pending.push(Outgoing::Mouse { pane: target.id, data });
        true
    }

    /// 절대 행 좌표로. 화면을 아직 못 받은 패널이면 `None`.
    fn abs_in_pane(&self, pane: i64, x: u16, y: u16) -> Option<proto::Point> {
        let (cx, cy) = self.state.clamp_to_pane(pane, x, y)?;
        self.state.pane_abs(pane, cx, cy)
    }

    /// 눌렀다. **여기서 포커스를 옮기지 않는다.**
    ///
    /// 누른 자리만 보고는 클릭인지 드래그인지 알 수 없다. 눌렀을 때 포커스를 옮기면
    /// 옆 패널의 글을 긁는 사람이 **매번 포커스를 빼앗긴다**(TUI 가 실제로 밟은 결함 —
    /// 판정을 뗌으로 옮겼다). 경계선만 예외다: 거기는 드래그밖에 없다.
    /// # Shift 는 "앱에 넘김"이다
    ///
    /// 평드래그는 이미 **복사**라 앱에게 넘길 자리가 없다. 그래서 Shift 를 넘김 제스처로
    /// 쓴다 — 파이썬 클라·TUI 와 같은 배정이다. 이게 없으면 마우스 1급 앱 안에서는
    /// **드래그를 아예 못 한다**.
    pub fn handle_mouse_down(&mut self, at: (u16, u16), shift: bool) -> bool {
        // ★ `set mouse off` 는 **클라가 마우스를 아예 안 보는 것**이다(파이썬과 같다).
        if !self.config.mouse {
            return false;
        }
        // 화면(팝업)이 떠 있으면 캔버스 마우스는 죽는다(N2) — 캔버스가 이제 스크림
        // 아래에 **계속 그려지므로**, 안 끊으면 팝업 밑의 패널이 클릭·드래그를 받는다.
        if self.screens.top().is_some() {
            return false;
        }
        let (x, y) = at;
        // ★ 오버레이가 광고한 자리(달력의 `‹`/`›`)가 먼저다 — 화살표를 그려 놓고
        // 클릭이 안 먹으면 그 화살표가 거짓말이 된다. **뜻은 우리가 모른다**: 서버가
        // 준 이름을 그대로 되돌려 보내고, 다음 셀 프레임이 답이다.
        // ⛔ **바로 아래 「판을 누르면 닫는다」보다 앞이라야 한다** — 뒤에 두면 달력
        //    패널의 클릭이 곧 닫기라 화살표를 누를 길이 아예 없다(정본 `clientwidgets.py`
        //    가 화살표 갈래에 적어 둔 그 이유 그대로다).
        if let Some((name, pane, act)) = self.state.overlay_zone_at(x, y) {
            self.pending.push(Outgoing::Command(Command::PluginOverlayAction {
                name,
                pane,
                act,
            }));
            return true;
        }
        // ★ 오버레이가 덮은 패널은 **아무 데나 눌러도 닫힌다**(pytmux-156 · 정본
        //   `clientwidgets.py:544` 의 "[x] 버튼 폐지"). 종전에는 닫는 길이 상태줄의 작은
        //   시각/날짜 표식을 **다시 정확히 찾아 누르는 것** 하나뿐이었다.
        //   ⛔ Claude 클릭존(`open_zone_at`·`send_zone_at`)보다 **앞**인 것도 정본 순서
        //      그대로다: 그 자리는 오버레이 **밑**이라 사람 눈에 안 보이는데, 뒤에 두면
        //      시계를 닫으려던 클릭이 권한모드 판을 연다.
        //   ⚠ 안 켜진 패널이면 `None` 이라 아래 갈래가 그대로 산다(경계선 드래그 포함).
        if let Some(pane) = self.state.pane_at(x, y)
            && let Some((name, t)) = self.state.close_overlay_on_pane(pane)
        {
            // 서버에도 끔을 올린다(Tier B — 그림은 서버 것이다). 안 올리면 우리만
            // 껐다고 믿고 화면에는 그대로 남는다.
            self.push_overlay(&name, Some(t));
            return true;
        }
        // ★ 패널 **안**의 자리가 화면을 여는 것이면 그 길로 보낸다(pytmux-2 · 23 —
        // Claude 의 권한모드 footer 와 토큰 수치). 여는 화면 이름도, 그 화면이 있는지도
        // 서버가 정한다 — 우리는 누른 패널을 함께 실어 보낼 뿐이다. 그 패널을 안 실으면
        // 비활성 Claude 패널의 footer 를 눌렀을 때 **활성 패널의 모드**가 바뀐다.
        if let Some((name, pane)) = self.state.open_zone_at(x, y) {
            self.pending.push(Outgoing::Command(Command::PluginOpen {
                name,
                args: vec![pane.to_string()],
            }));
            return true;
        }
        // ★ 패널 안의 자리가 **화면이 아니라 그 패널에 치는 것**이면 그대로 친다
        //   (Claude busy footer 의 `esc to interrupt`). 무엇을 치는지는 서버가 정하고
        //   우리는 그 패널로 넘길 뿐이라, 사람이 그 자리에서 ESC 를 친 것과 같은 길이다.
        //   **활성 패널을 안 바꾼다** — 비활성 Claude 패널을 멈추려고 누른 것이 지금
        //   보는 패널로 가면 안 된다(정본이 `send_input_pane` 을 쓰는 이유).
        if let Some((pane, data)) = self.state.send_zone_at(x, y) {
            self.pending.push(Outgoing::InputToPane { pane, data });
            return true;
        }
        if let Some(divider) = self.state.divider_at(x, y) {
            self.dragging = Some(divider.split_id);
            return false;
        }
        // ★ 밑줄이 그어진 자리는 **그 뜻이 먼저**다(§10-21ⓥ2·ⓧ2). 그어 놓고 눌렀는데
        //   선택 드래그가 시작되면 그 밑줄이 거짓말이 된다. 경계보다는 뒤다 — 저건
        //   잡는 자리이고 이건 글 위다(겹치지 않는다).
        if !shift && let Some(hit) = self.span_at(x, y) {
            self.act_on_span(&hit);
            return true;
        }
        if shift {
            // 넘김이 안 될 때 층을 가르는 줄(`RUST_LOG=debug`). 셋 중 무엇이 빠졌는지가
            // 화면에서는 전부 "아무 일도 안 남"으로 똑같이 보인다.
            let track = self
                .state
                .pane_at(x, y)
                .and_then(|id| self.state.panes().iter().find(|p| p.id == id))
                .map(|p| (p.id, p.mouse, p.mouse_sgr));
            log::debug!(
                "Shift+누름 ({x},{y}) · 모드 {:?} · 패널/추적 {track:?}",
                self.mode.mode()
            );
        }
        if Self::press_goes_to_app(
            &self.state,
            self.mode.mode(),
            at,
            shift,
            self.config.mouse_drag_copy,
        ) && let Some(pane) = self.mouse_target(x, y)
        {
            // 비활성 패널이면 먼저 포커스를 옮긴다 — 앱을 조작하는 중인데 키가 딴 데로
            // 가면 안 된다(파이썬 클라·TUI 와 같은 순서).
            self.focus_pane(pane);
            if self.forward_mouse(x, y, MouseKind::Press, 1) {
                self.selection = None;
                self.press = None;
                self.mouse_fwd = Some((pane, 1));
                return true;
            }
        }
        self.selection = None;
        self.press = self.state.pane_at(x, y).map(|id| (id, x, y));
        false
    }

    /// 오른쪽 버튼을 눌렀다 — **그 자리의 패널을 대상으로 메뉴를 연다**(§10-21ⓕ3).
    ///
    /// # 새 화면이 아니라 새 입구다
    ///
    /// 메뉴는 이미 있다(`prefix Enter` · [`Screen::Menu`]) — 항목표도 core 에 한 벌이다
    /// (`base::keymap` 의 `MENU_*`). 여기서 하는 일은 **그 판을 여는 두 번째 길**을 내는
    /// 것뿐이라, 항목이 갈리거나 실행 경로가 둘이 될 자리가 없다.
    ///
    /// # 정본이 적어 둔 규칙 둘을 그대로 가져온다
    ///
    /// ⑴ **마우스 패스스루 앱 위에서도 우클릭은 pytmux 메뉴가 먼저다**
    ///    (`clientwidgets.py` — 왼쪽 버튼과 갈리는 자리다. 왼쪽은 앱에게 넘기지만
    ///    오른쪽은 우리가 먹는다. 안 그러면 마우스를 쓰는 앱 위에서는 메뉴로 가는 길이
    ///    아예 없다).
    /// ⑵ **평소 모드에서만** 연다(정본의 `button == 3` 은 평소 모드 가드 안에 있다).
    ///
    /// # 늦게 도착한 우클릭
    ///
    /// 정본에는 **이미 닫힌 화면에 우클릭이 늦게 닿아 크래시**한 기록이 있다
    /// (`clientio.py`). 우리 쪽 같은 자리는 "판이 떠 있으면 캔버스 마우스가 죽는다"
    /// 규칙이 막는다 — 판이 있으면 여기서 곧장 돌아간다.
    pub fn handle_right_mouse_down(&mut self, at: Option<(u16, u16)>) -> bool {
        // `set mouse off` 는 클라가 마우스를 아예 안 보는 것이다(왼쪽과 같은 판정).
        if !self.config.mouse {
            return false;
        }
        // 판이 떠 있으면 캔버스 마우스는 죽는다(N2) — 늦게 도착한 우클릭이 그 판 밑의
        // 패널을 건드리지 않게 하는 자리이기도 하다.
        if self.screens.top().is_some() {
            return false;
        }
        // 평소 모드에서만. prefix·스크롤 모드에서 열면 그 모드의 다음 키를 메뉴가 먹는다.
        if self.mode.mode() != InputMode::Normal {
            return false;
        }
        let Some((x, y)) = at else {
            return false;   // 캔버스 밖(탭바 등) — 정본도 패널 위에서만 연다
        };
        // 대상은 **누른 그 패널**이다(정본 `client.py` 도 그 패널을 강조까지 한다).
        // 비활성이면 먼저 포커스를 옮긴다 — 메뉴의 항목이 "이 패널"에 걸리므로, 안 옮기면
        // 오른쪽으로 누른 패널과 메뉴가 조작할 패널이 갈린다.
        if let Some(pane) = self.state.pane_at(x, y) {
            self.focus_pane(pane);
        }
        // ⚠ 여기서 넘김(`forward_mouse`)을 **안 한다**. 마우스를 쓰는 앱 위에서도 메뉴가
        //   먼저라는 것이 정본의 규칙이고, 그 규칙이 없으면 그런 앱 위에서는 메뉴로 가는
        //   길이 아예 없다.
        self.screens.open(Screen::Menu);
        true
    }

    /// 밑줄이 그어진 자리를 눌렀다 — 링크는 열고 경로는 복사한다(§10-21ⓥ2·ⓧ2).
    ///
    /// **한 배관에 제공자 둘**이다: 무엇인지는 core 가 정하고(`base::spans`) 여기서는
    /// 그 갈래마다 무엇을 할지만 고른다. 갈래가 늘어도 hover·강조·커서는 그대로다.
    ///
    /// 결과는 **알림으로 말한다**. 열지 못했거나 복사하지 못한 것이 화면에 안 남으면
    /// 사용자는 "눌렀는데 아무 일도 안 났다"만 본다 — 그건 안 눌린 것과 구분이 안 된다.
    fn act_on_span(&mut self, hit: &proto::SpanHit) {
        use base::spans::SpanKind;
        match hit.kind {
            SpanKind::Url => {
                if proto::info::open_link(&hit.text) {
                    self.state.note_notice(tf("링크를 열었다: {url}", &[("url", &hit.text)]));
                } else {
                    // 안 여는 것과 못 여는 것이 여기서 합쳐진다 — 둘 다 사용자에게는
                    // "안 열렸다"이고, 스킴 때문이면 그건 **일부러** 안 연 것이다.
                    self.state.note_error(tf("링크를 열지 못했다: {url}", &[("url", &hit.text)]));
                }
            }
            SpanKind::Path => {
                // 존을 만들 때 이미 풀 수 있음을 확인했다(`span_at`) — 여기서 `None` 이면
                // 그 사이에 cwd 가 사라진 것이다(탭이 바뀌었다).
                //
                // 기준은 **그 범위가 있던 패널**이다(`hit.pane`). 활성 패널로 풀면 옆
                // 패널 글에서 밑줄은 멀쩡하고 복사한 값만 틀린다 — 조용한 오답이다.
                let Some(full) = proto::info::resolve_path(self.state.pane_cwd(hit.pane), &hit.text)
                else {
                    return;
                };
                // 도구가 하나도 없으면 `false` 다 — 그때 "복사됨"이라고 말하면 거짓말이
                // 되고, 붙여넣기가 안 되는 이유를 찾을 수 없게 된다(`clip::copy` 문서).
                if clip::copy(&full) {
                    self.state.note_notice(tf("경로를 복사했다: {path}", &[("path", &full)]));
                } else {
                    self.state.note_error(tf("경로를 복사하지 못했다: {path}", &[("path", &full)]));
                }
            }
        }
    }

    /// 끌고 있다. 경계선이면 비율을, 패널 위면 선택을 늘린다.
    pub fn handle_mouse_drag(&mut self, at: Option<(u16, u16)>) -> bool {
        if self.tab_drag.is_some() {
            // 드롭 대상 표시만 갱신한다 — 판정은 뗄 때 한 번이다(TUI 와 같다).
            let over = self.hovered_tab();
            if over != self.tab_drag_over {
                self.tab_drag_over = over;
                return true;
            }
            return false;
        }
        // 화면이 떠 있으면 캔버스 드래그도 죽는다(N2 — `handle_mouse_down` 과 같은 이유).
        if self.screens.top().is_some() {
            return false;
        }
        // 캔버스 밖(탭바·상태줄 위)의 드래그는 캔버스 일이 아니다.
        let Some(at) = at else {
            return false;
        };
        let (x, y) = at;
        if let Some(split_id) = self.dragging {
            // 잡은 **그 경계로만** 계산한다 — 좌표 아래 경계를 매번 다시 찾으면, 마우스가
            // 빨라 경계를 앞질렀을 때 다른 분할로 갈아타 화면이 접힌다(TUI 와 같은 이유).
            let Some(divider) = self
                .state
                .dividers()
                .iter()
                .find(|d| d.split_id == split_id)
            else {
                self.dragging = None; // 배치가 바뀌어 그 분할이 사라졌다
                return false;
            };
            let ratio = divider.ratio_at(x, y);
            self.pending
                .push(Outgoing::Command(Command::ResizeSplit { split_id, ratio }));
            return true;
        }
        // 앱에게 넘기는 중이면 **그 앱이 드래그를 원할 때만** 계속 준다. 1000 만 켠 앱에
        // 모션을 보내면 누른 적 없는 자리에서 눌린 것처럼 읽힌다.
        if let Some((pane, button)) = self.mouse_fwd {
            let wants = self
                .state
                .panes()
                .iter()
                .find(|p| p.id == pane)
                .is_some_and(|p| p.mouse_mode().wants_drag());
            if wants {
                self.forward_mouse(x, y, MouseKind::Drag, button);
            }
            return true;
        }
        let Some((pane, px, py)) = self.press else {
            return false;
        };
        // 앵커는 **누른 자리**다(지금 자리가 아니다) — 첫 칸을 놓치면 사용자가 고른
        // 시작 글자가 복사본에서 빠진다.
        if self.selection.is_none() {
            if (x, y) == (px, py) {
                return false; // 아직 안 움직였다. 클릭일 수도 있다.
            }
            let Some(anchor) = self.abs_in_pane(pane, px, py) else {
                return false; // 화면을 아직 못 받은 패널 — 절대 좌표의 기준점이 없다
            };
            self.selection = Some(Selection::new(pane, anchor));
        }
        if let Some(focus) = self.abs_in_pane(pane, x, y)
            && let Some(selection) = self.selection.as_mut()
        {
            selection.extend_to(focus);
        }
        true
    }

    /// 버튼 없이 움직였다(N3). 분할 경계 위인지만 본다 — 강조가 바뀔 때만 다시 그린다.
    pub fn handle_mouse_move(&mut self, at: Option<(u16, u16)>) -> bool {
        let alive = self.config.mouse && self.screens.top().is_none();
        let hover = alive
            .then(|| at.and_then(|(x, y)| self.state.divider_at(x, y).map(|d| d.split_id)))
            .flatten();
        let mut dirty = false;
        if hover != self.divider_hover {
            self.divider_hover = hover;
            dirty = true;
        }
        // ★ 범위 hover(§10-21ⓥ2·ⓧ2) — 경계 위면 그쪽이 먼저다(잡는 자리가 우선).
        let span = (alive && hover.is_none() && !self.dragging.is_some())
            .then(|| at.and_then(|(x, y)| self.span_at(x, y)))
            .flatten();
        if span != self.span_hover {
            self.span_hover = span;
            dirty = true;
        }
        dirty
    }

    /// 그 자리의 범위 — **풀 수 있는 것만** 돌려준다(§10-21ⓧ2).
    ///
    /// 경로는 전체 경로로 풀 수 있어야 뜻이 있다(복사할 것이 그 값이다). 못 풀면
    /// **존을 안 만든다** — 밑줄을 그어 놓고 눌러도 아무 일이 없으면 그 밑줄이 거짓말이다.
    fn span_at(&self, x: u16, y: u16) -> Option<proto::SpanHit> {
        let hit = self.state.span_at(x, y)?;
        if hit.kind == base::spans::SpanKind::Path
            && proto::info::resolve_path(self.state.pane_cwd(hit.pane), &hit.text).is_none()
        {
            return None;
        }
        Some(hit)
    }

    /// 놓았다. **여기서 클릭과 드래그가 갈린다.**
    pub fn handle_mouse_up(&mut self, at: Option<(u16, u16)>) -> bool {
        if let Some(src) = self.tab_drag.take() {
            self.tab_drag_over = None;
            self.drop_tab(src, at);
            return true;
        }
        // 화면이 떠 있으면 캔버스 뗌도 죽는다(N2). 미결 상태만 비운다 — 팝업이 뜨기
        // 직전에 누른 press/선택이 남아 있으면 팝업을 닫은 첫 클릭이 드래그로 읽힌다.
        if self.screens.top().is_some() {
            self.press = None;
            self.selection = None;
            self.dragging = None;
            return false;
        }
        let Some(at) = at else {
            return false;
        };
        self.dragging = None;
        // 넘기는 중이었으면 **반드시 뗌까지** 보낸다. 안 보내면 그 앱은 버튼이 영원히
        // 눌린 줄 알고, 이후 모든 이동을 드래그로 읽는다.
        if let Some((pane, button)) = self.mouse_fwd.take() {
            // 좌표가 패널 밖으로 나갔으면 **패널 안으로 접어** 보낸다 — 뗌 자체가
            // 사라지는 것보다 낫다.
            if let Some((x, y)) = self.state.clamp_to_pane(pane, at.0, at.1) {
                self.forward_mouse(x, y, MouseKind::Release, button);
            }
            self.press = None;
            return true;
        }
        let press = self.press.take();
        // 끌었다 = 복사. 서버에게 뽑아 달라 하고 강조는 지운다(회신이 오면 클립보드로
        // 가고, 반전이 화면에 남아 있으면 아직 진행 중인 것처럼 보인다).
        if let Some(selection) = self.selection.take()
            && !selection.is_collapsed()
            // 설정으로 끌 수 있다(`mouse-drag-copy off`). `shift` 는 복사한다 —
            // 그 값이 바꾼 것은 «어느 드래그가 선택이 되나»이지 복사 여부가 아니다.
            && self.config.mouse_drag_copy.copies()
        {
            let (a, b) = selection.ordered();
            // 접힘을 되돌릴 때 쓸 기하를 지금 재 둔다 — 회신에는 글자만 온다.
            self.copy_geom = self
                .state
                .layout()
                .and_then(|l| l.panes.iter().find(|p| p.id == selection.pane))
                .map(|p| (p.w as usize, a.col as usize));
            self.pending.push(Outgoing::Command(Command::CopyRange {
                pane: selection.pane,
                y0: a.line,
                x0: a.col,
                y1: b.line,
                x1: b.col,
            }));
            return true;
        }
        // 안 끌었다 = 클릭.
        let Some((id, px, py)) = press else {
            return false;
        };
        let _ = at;
        // 비활성 패널이면 먼저 포커스를 옮긴다 — 앱을 조작하는 중인데 키가 딴 데로 가면
        // 안 된다(정본과 같은 순서). 이미 활성이면 아무 프레임도 안 나간다.
        self.focus_pane(id);
        // ★ **마우스를 켠 앱 위의 클릭은 그 앱에게 간다**(pytmux-19).
        //
        //   종전에는 여기서 포커스만 옮기고 끝냈다. 그래서 패널 안 앱의 클릭이 통째로
        //   죽어 있었다 — 제보는 *"Claude 프롬프트 바를 눌러도 그 자리로 안 간다"* 였지만,
        //   같은 구멍에 권한모드 푸터·링크·앱 버튼이 전부 들어 있었다. 실패가 조용하다:
        //   포커스는 옮겨지니 클릭이 "먹은 것처럼" 보이고 아무 일도 안 일어난다.
        //
        //   ⚠ 좌표는 **누른 자리**다(뗀 자리가 아니다 — 정본 `_sel_pending` 과 같다).
        //   손이 한 칸 흔들린 채 떼는 것은 흔하고, 그때 앱이 받는 자리가 눌린 자리와
        //   다르면 옆 버튼이 눌린다.
        //
        //   누름과 뗌을 **둘 다** 보낸다. 누름만 보내면 그 앱은 버튼이 영원히 눌린 줄
        //   알고 이후 모든 이동을 드래그로 읽는다(`mouse_fwd` 경로가 같은 이유로 뗌을
        //   반드시 보낸다).
        if Self::click_goes_to_app(&self.state, self.mode.mode(), (px, py)) {
            self.forward_mouse(px, py, MouseKind::Press, 1);
            self.forward_mouse(px, py, MouseKind::Release, 1);
        }
        true
    }

    /// Claude 항목을 갱신한다. 바뀌었으면 `true`.
    ///
    /// TUI 는 이벤트 루프가 이 일을 하고 뷰는 결과만 받지만, GUI 에는 그 자리에 해당하는
    /// 루프가 없다 — 주기 작업이 [`pump`](Self::pump) 로 돌아온다(`main.rs`). 그래서
    /// **여기가 GUI 의 이벤트 루프**이고, 파일을 만지는 것도 렌더가 아니라 여기다
    /// (렌더는 `&self` 만 받는다 = 그리면서 상태를 안 바꾼다는 계약).
    fn pump_claude(&mut self) -> bool {
        if self.last_look.elapsed() < CLAUDE_POLL {
            return false;
        }
        self.last_look = Instant::now();
        // 어느 트랜스크립트를 볼지는 공유 규칙이 정한다 — 뷰가 각자 답하면 원격 패널
        // 자리에 로컬 대화가 뜨는 결함이 한쪽에서만 되살아난다.
        let picked = claude::source::pick(
            self.state.active_cwd().map(str::to_owned),
            self.state.active_pane(),
        );
        let fresh = match picked {
            // 파일 쪽은 **바뀌었을 때만** 다시 만든다(수정 시각이 그대로면 파싱도 안 한다).
            Source::LocalFile(cwd) => {
                let changed = self.watcher.set_cwd(&cwd) | self.watcher.refresh();
                changed.then(|| {
                    (
                        self.watcher.items().to_vec(),
                        self.watcher.mode().map(str::to_owned),
                    )
                })
            }
            // 상류 쪽은 프레임이 올 때 이미 파싱됐다.
            Source::Upstream(pane) => self.remote.snapshot(pane),
            Source::Nothing => None,
        };
        match fresh {
            Some((items, mode)) => {
                let changed = items != self.claude || mode != self.claude_mode;
                self.claude = items;
                self.claude_mode = mode;
                changed
            }
            None => false,
        }
    }

    /// 창에 맞는 격자 크기를 서버에 알린다(바뀌었을 때만).
    ///
    /// # 왜 이게 필요했나 (실측 2026-07-28)
    ///
    /// 종전 GUI 는 붙을 때 **80×24 를 알리고 그걸로 끝**이었다 — 창을 아무리 키워도 캔버스는
    /// 80×24 였고(창의 나머지는 빈 배경), 줄이면 화면이 창 밖으로 넘쳤다. TUI 는 터미널
    /// 크기를 폴링해 알리는데 GUI 에는 그 자리가 아예 없었다.
    ///
    /// # 크롬 높이를 계산하지 않는다
    ///
    /// 위에 붙는 것(탭바·끊김 알림·오류)과 아래 구역(블록·Claude)은 상황마다 줄 수가
    /// 다르다. 그래서 **렌더가 남긴 자리표 둘**로 잰다: 캔버스 첫 글자
    /// ([`CELL_PROBE`](Self::CELL_PROBE))가 왼쪽·위 여백과 칸 크기를, 아래 구역 머리줄
    /// ([`FOOTER_PROBE`](Self::FOOTER_PROBE))이 캔버스가 끝나는 자리를 알려 준다.
    /// 좌표 보정과 같은 규율이다 — *계산하면 렌더와 어긋난다.*
    ///
    /// 아래 구역이 없으면(블록도 Claude 도 없을 때) 창 바닥에서 여백만 뺀다.
    fn report_size(&mut self, ctx: &mut ViewContext<Self>) {
        let Some(probe) = ctx.element_position_by_id(Self::CELL_PROBE) else {
            return; // 첫 프레임 — 아직 잴 것이 없다
        };
        // 캔버스가 격자를 잡는 데 쓴다(§10-21ⓙ). 격자 크기를 알리지 못하는 프레임
        // (창이 너무 작다 등)에도 칸 크기는 유효하므로 **먼저** 남긴다.
        self.note_cell_size(probe.width(), probe.height());
        let window = ctx.window_id();
        let Some(bounds) = ctx.window_bounds(&window) else {
            return;
        };
        // 아래 구역이 **자기 높이만큼** 캔버스를 밀어낸다. 창 바닥까지의 거리를 빼면
        // 그 아래 빈 자리도 크롬으로 세어, 캔버스가 프레임마다 한 줄씩 줄어든다
        // (실측 2026-07-28 — 175x10 → 175x3 으로 접혔다).
        let footer_px = ctx
            .element_position_by_id(Self::FOOTER_PROBE)
            .map_or(0., |f| f.height() * self.footer_lines() as f32);
        // 판을 캔버스 안 어느 칸에 세울지 아는 데 필요하다(pytmux-370). 같은 자리표에서
        // 함께 뽑는다 — 두 번 재면 두 값이 갈린다.
        self.canvas_px.set(Some(CanvasBox {
            left: probe.origin_x(),
            top: probe.origin_y(),
            win_w: bounds.width(),
            win_h: bounds.height(),
        }));
        let Some((cols, rows)) =
            Self::grid_for(probe, bounds.width(), bounds.height(), footer_px)
        else {
            return;
        };
        if let Some(frame) = self.size.update(cols, rows) {
            log::info!("창에 맞춘 격자: {}x{}", frame.cols, frame.rows);
            self.pending.push(Outgoing::Resize(frame));
        }
    }

    /// 잰 칸 크기를 남긴다. 말이 안 되는 값(0·무한대)은 **안 받는다** — 그걸 받으면
    /// 격자가 한 점으로 접히고, 증상은 "캔버스가 통째로 비었다"가 된다.
    pub fn note_cell_size(&self, w: f32, h: f32) {
        if w.is_finite() && h.is_finite() && w > 0.5 && h > 0.5 {
            self.cell_px.set(Some((w, h)));
        }
    }

    /// 아래 요약 구역이 **최대** 몇 줄까지 쓰나(머리줄 포함). 구역이 아예 없으면 0.
    ///
    /// # 왜 지금 줄 수가 아니라 예산인가
    ///
    /// 지금 그려진 줄 수로 재면 **블록이나 Claude 항목이 하나 늘 때마다 캔버스가 한 줄씩
    /// 줄었다 늘었다** 한다 — 그때마다 서버가 전 세션을 재배치하고 그 프레임이 같은
    /// 세션의 다른 클라에게도 간다(실측 2026-07-28: 47→46→47 로 떨렸다). 예산은 고정이라
    /// (`footer::ROWS`) 한 번 잡아 두면 흔들리지 않는다.
    fn footer_lines(&self) -> usize {
        // 배지 줄은 **늘 있다** — `e_down` 이 갈 곳이라 접히면 안 된다.
        let badges = 1;
        // 메시지 줄(`render_message`)도 **늘 세어 둔다**. 있을 때만 세면 끊기는 순간
        // 캔버스가 한 줄 줄고 붙는 순간 다시 늘어 — 예산을 고정으로 두는 바로 그 이유의
        // 재발이다. 안 세면 더 나쁘다: 라이브에서 그 줄이 **창 밖으로 밀려 안 보였다**
        // (2026-07-30 실측 — 상태줄까지만 그려지고 메시지가 사라졌다).
        let message = 1;
        // ★ 요약 구역의 몫은 **없다**(§10-21ⓓ) — 화면에서 빠져 판으로 갔다. 그만큼
        // 캔버스가 늘 두 줄 넓어진다(종전에는 블록이나 Claude 가 있으면 머리줄 한 줄을
        // 상시로 먹었다).
        badges + message
    }

    /// 자리표와 창 크기로 격자를 잰다. 잴 수 없으면 `None`.
    ///
    /// `footer_px` 는 아래 요약 구역이 쓰는 높이(없으면 0).
    ///
    /// **모자라게 잡는다.** 한 줄 남는 것은 빈 줄 하나지만, 한 줄 넘치면 아래 구역이 창
    /// 밖으로 밀려 **블록·복사 알림이 통째로 안 보인다**.
    fn grid_for(
        probe: warpui::geometry::rect::RectF,
        window_w: f32,
        window_h: f32,
        footer_px: f32,
    ) -> Option<(u16, u16)> {
        let (cw, ch) = (probe.width(), probe.height());
        if !(cw.is_finite() && ch.is_finite() && window_w.is_finite() && window_h.is_finite())
            || cw <= 0.5
            || ch <= 0.5
        {
            return None;
        }
        let left = probe.origin_x();
        let top = probe.origin_y();
        let footer_px = if footer_px.is_finite() { footer_px.max(0.) } else { 0. };
        let usable_w = window_w - left - Self::PAD;
        let usable_h = window_h - top - footer_px - Self::PAD;
        if usable_w < cw || usable_h < ch {
            return None; // 창이 한 칸도 못 담을 만큼 작다 — 알리지 않는다
        }
        Some(((usable_w / cw) as u16, (usable_h / ch) as u16))
    }

    /// 도착한 서버 메시지를 반영한다. **화면이 달라졌으면** `true`.
    ///
    /// 한 프레임에 몰려 온 것을 한 번에 반영한다 — 하나씩 반영하며 매번 다시 그리면
    /// 반쯤 그려진 화면이 보인다(TUI 루프와 같은 이유).
    /// 시계가 켜져 있으면 시각을 갱신한다(패리티 G7b). 다시 그려야 하면 `true`.
    ///
    /// 이벤트 루프가 매 프레임 부른다. **초가 안 바뀌면 false** 라 30Hz 루프가 매 프레임
    /// repaint 를 걸지 않는다 — 시계 하나 때문에 화면 전체를 30배로 그릴 이유는 없다.
    /// 지금(epoch 초). 시계가 뒤로 가면 0 — 그 창에서는 카운트다운을 안 그린다.
    fn now_secs() -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_secs() as i64)
    }

    /// 떠 있는 판에 **카운트다운 줄이 있으면** 초마다 다시 그린다(pytmux-371 ④).
    ///
    /// # 왜 조건이 붙나
    ///
    /// `true` 를 돌려주면 화면 **전체**를 다시 그린다(`tick_cursor_blink` 머리말과 같은
    /// 값). 카운트다운이 없는 평상시에도 초당 한 번 전 세션을 다시 그릴 이유가 없다 —
    /// 그래서 그 줄이 실제로 떠 있을 때만 센다.
    fn tick_countdown(&mut self) -> bool {
        if self.screens.top() != Some(Screen::PluginView) {
            return false;
        }
        let now = Self::now_secs();
        let live = self
            .state
            .plugin_screen()
            .is_some_and(|spec| spec.rows.iter().any(|r| r.countdown(now).is_some()));
        if !live {
            return false;
        }
        // 초가 바뀐 프레임에서만 dirty 다(같은 초에 여러 번 그리면 값이 그대로다).
        !std::mem::replace(&mut self.countdown_secs, now).eq(&now)
    }

    pub fn tick_clock(&mut self) -> bool {
        let mut dirty = false;
        dirty |= self.tick_countdown();
        // RTT ping(G9u) — 그림은 안 바뀌니 dirty 를 안 세운다(TUI 와 같은 규칙).
        if let Some(ping) = self.pinger.tick() {
            self.pending.push(ping);
        }
        // 시계는 **서버가 초를 센다**(P3). 여기서 시각을 넣던 것은 우리가 그리던
        // 시절의 손이고, 지금은 새 `plugin_cells` 프레임이 곧 "다시 그려라"다 —
        // 지금 화면 갱신과 같은 길이라 클라에 따로 시계를 둘 이유가 없다.
        dirty |= self.tick_flash();
        dirty |= self.tick_cursor_blink();
        dirty | self.tick_status()
    }

    /// 커서 깜빡임을 반주기마다 뒤집는다(`cursor-blink` · `pytmux/pytmux-161`).
    /// 화면이 달라지면 `true`.
    ///
    /// # 왜 꺼져 있을 때도 할 일이 있나
    ///
    /// 「안 보임」 반주기에서 사람이 깜빡임을 끄면 그 상태가 그대로 굳어 **커서가
    /// 영영 사라진다** — 되돌릴 입구(설정 화면)는 있지만, 커서가 없는 화면에서 그
    /// 원인이 「깜빡임을 껐다」임을 알아낼 방법이 없다. 그래서 꺼져 있으면 언제나
    /// 보이는 쪽으로 되돌린다.
    ///
    /// # 왜 매 프레임이 아니라 반주기인가
    ///
    /// `tick_status` 와 같은 값이다 — `true` 를 돌려주면 화면 전체를 다시 그린다.
    /// 30Hz 로 뒤집으면 커서 하나 때문에 초당 서른 번 그리게 된다.
    fn tick_cursor_blink(&mut self) -> bool {
        if !self.config.cursor_blink {
            return !std::mem::replace(&mut self.blink_on, true);
        }
        // `max(1)` 은 0 을 막는 것이 아니라(파서가 100 아래를 안 받는다) **다음 사람이
        // 그 하한을 낮췄을 때** 여기가 0 나눗셈 없는 무한 루프가 되지 않게 하는 것이다.
        let period =
            std::time::Duration::from_millis(self.config.cursor_blink_ms.max(1) as u64);
        if self.last_blink.elapsed() < period {
            return false;
        }
        self.last_blink = Instant::now();
        self.blink_on = !self.blink_on;
        true
    }

    /// 상태줄을 `status-interval` 초마다 다시 그린다.
    ///
    /// 서버 메시지가 없어도 `%H:%M` 은 흘러야 한다 — 안 재우면 시각이 다음 키를 누를
    /// 때까지 멈춰 있고, 그건 시계가 **고장 난 것처럼** 보인다.
    ///
    /// 매 프레임 다시 그리지 않는 이유도 같은 값이다: 15초짜리 형식을 30Hz 로 다시
    /// 그리면 아무것도 안 바뀌는 프레임을 초당 서른 번 그린다.
    fn tick_status(&mut self) -> bool {
        let period =
            std::time::Duration::from_secs(self.config.status_interval.max(1) as u64);
        if self.last_status.elapsed() < period {
            return false;
        }
        self.last_status = std::time::Instant::now();
        true
    }

    /// 입력기 배지를 새로 물어본다. 바뀌었으면 `true`(다시 그린다).
    ///
    /// 주기는 [`IME_PERIOD`] — **정본과 같은 값**이다(pytmux-378).
    fn tick_ime(&mut self) -> bool {
        if self.last_ime.elapsed() < IME_PERIOD {
            return false;
        }
        self.last_ime = std::time::Instant::now();
        // ★ **「배지는 내가 그린다」를 먼저 알린다**(pytmux-392). 이 사실이 서버에 서면
        //   `ime-indicator` 플러그인이 이 클라에게 글자 런을 **안 보낸다** — 우리는 같은
        //   자리에 그림을 그리므로 런이 오면 겹친다. 판정을 플러그인 한 곳에 두려는 것이고
        //   (런을 문구로 알아보고 지우는 길은 규칙을 두 벌로 만든다), 사실은 연결에 매달려
        //   있으니 **연결마다** 다시 알린다.
        if !self.told_ime_render {
            self.told_ime_render = true;
            self.pending.push(Outgoing::Command(Command::ClientFact {
                name: "ime_render".to_owned(),
                value: Some("client".to_owned()),
            }));
        }
        self.report_ime(crate::ime::badge())
    }

    /// 바뀐 한/영을 **사실로 올린다**. OS 를 여기서 안 묻는 이유는 그래야 오라클이
    /// 이 배선을 부를 수 있어서다 — 위 `tick_ime` 은 창 밖 입력기 창에 물어야 해서
    /// 테스트가 값을 정할 수 없다(그 탓에 이 배선은 오래 라이브 스크린샷으로만
    /// 잡혔다). 바뀌었으면 `true`(다시 그린다).
    fn report_ime(&mut self, now: Option<&'static str>) -> bool {
        if now == self.ime_badge {
            return false;
        }
        self.ime_badge = now;
        // ★ 우리가 하는 일은 **사실을 알리는 것까지**다(설계 Tier D · P7). 한/영은 OS 가
        // 우리 창에만 알려 주니 서버가 스스로 알 수 없지만, **그릴지·어디에·무슨 색으로**
        // 는 플러그인이 정한다(Tier B) — 그래야 규칙이 정본과 한 벌이다. 종전에는 우리가
        // 그림까지 들고 있었고 자리가 갈려 있었다(활성 패널 첫 행 vs 정본의 커서 줄).
        // 그림은 다음 `plugin_cells` 프레임으로 온다.
        self.pending.push(Outgoing::Command(Command::ClientFact {
            name: "ime".to_owned(),
            value: now.map(str::to_owned),
        }));
        true
    }

    /// 설정의 글꼴과 **지금 깔린 것**을 맞춘다 → 다시 깔았나(pytmux-408 ④).
    ///
    /// # 왜 프레임 진입인가
    ///
    /// 글꼴 등록에는 `ctx` 가 필요한데, 값이 바뀌는 자리(설정 화면의 대답·`:set`·
    /// `source-file`)는 전부 `ctx` 가 없는 경로다. 값 바꾸는 자리마다 `ctx` 를 끌고
    /// 들어가면 그 셋이 각자 글꼴을 깔게 되고, 하나만 빠지면 **어떤 길로 바꿨느냐에 따라
    /// 먹기도 안 먹기도** 한다. 그래서 「바뀌었으면 맞춘다」 한 곳으로 모은다.
    ///
    /// 비교는 문자열 하나라 매 프레임 돌아도 값이 없다시피 하고, 같으면 캐시를 안 건드린다.
    fn reconcile_font(&mut self, ctx: &mut ViewContext<Self>) -> bool {
        if self.config.font_family == self.font_want {
            return false;
        }
        let want = self.config.font_family.clone();
        let (font, missed) = warpui::fonts::Cache::handle(ctx)
            .update(ctx, |cache, _| mono_font::install_preferred(cache, &want));
        self.font = font;
        self.font_want = want;
        // 셀 크기가 글꼴을 타므로 자리표를 버린다 — 다음 프레임이 다시 잰다.
        self.cell_px.set(None);
        if let Some(name) = missed {
            self.state.note_notice(base::i18n::tf(
                "글꼴 «{name}» 이 이 상자에 없다 — 기본 글꼴로 그린다",
                &[("name", &name)],
            ));
        }
        true
    }

    pub fn pump(&mut self, ctx: &mut ViewContext<Self>) -> bool {
        let font_changed = self.reconcile_font(ctx);
        let (dirty, clip) = self.pump_messages();
        // 클립보드 쓰기는 창 계층의 일이라 여기 남는다(아래 절반이 창을 모르는 이유).
        if let Some(text) = clip {
            let mut content = warpui::clipboard::ClipboardContent::default();
            content.plain_text = text;
            ctx.clipboard().write(content);
        }
        self.report_size(ctx);
        let dirty = dirty || font_changed;
        // ★ 제목은 **창이 있는 판에서만** 세운다(`pump_headless` 에는 창이 없다). 크기
        // 보고와 같은 자리다 — 그 둘이 이 판에만 있는 이유가 같다.
        self.refresh_title(ctx);
        self.refresh_titlebar_band(ctx);
        self.refresh_window_focus(ctx);
        self.pump_tail(dirty)
    }

    /// `pump` 에서 **창이 필요한 둘**(클립보드 쓰기·크기 보고)만 뺀 판.
    ///
    /// 오라클이 부르는 자리다 — 나머지 배선은 실제와 **같은 순서로** 지난다. 순서가 계약인
    /// 이유: `report_size` 는 큐에 밀어 넣기만 하고 실제 전송은 `pump_tail` 의
    /// `flush_outgoing` 이 한다. 둘의 앞뒤가 바뀌면 창 크기 알림이 한 프레임 늦는다.
    #[cfg(test)]
    pub(crate) fn pump_headless(&mut self) -> bool {
        let (dirty, _clip) = self.pump_messages();
        self.pump_tail(dirty)
    }

    /// 퍼올리기의 **꼬리** — 두 판이 공유한다(따로 적으면 조용히 갈린다).
    fn pump_tail(&mut self, mut dirty: bool) -> bool {
        // ★ 여기 있는 이유는 **순서** 때문이다. 크기 보고(`report_size`)보다 뒤라야 서버가
        // 새 크기로 다시 그려 준다 — 앞에 두면 옛 크기의 전체 프레임이 한 번 헛되이 온다.
        // 상태 누적기는 소켓을 모르므로 표식만 세우고, 보내는 것은 뷰의 일이다.
        if self.state.take_redraw_request() {
            log::info!("기준 없는 화면 델타 — 다시 그리기를 청한다");
            self.pending
                .push(Outgoing::Command(Command::RequestRedraw));
        }
        dirty |= self.pump_claude();
        // ★ 정보 팝업의 자리는 **프레임마다** 맞춘다(pytmux-373 ⑶). 키·클릭만으로는
        //   모자란다 — 그 판의 줄 수는 서버가 바꾼다(RTT 표본이 오면 서버 탭이 그만큼
        //   길어진다). 안 맞추면 커서가 없는 줄을 가리킨 채 `Enter` 를 기다린다.
        self.settle_info_tabs();
        self.flush_outgoing();
        // 첫 캔버스가 생긴 순간을 한 번만 남긴다. **창을 볼 수 없는 자리에서 유일한
        // 관측점**이다 — "붙었다"(소켓)와 "그릴 것이 왔다"(프레임)는 다른 사건이고,
        // 이 줄이 없으면 둘을 가를 방법이 없다.
        if !self.drew && self.state.composite().is_some() {
            self.drew = true;
            if let Some((w, h)) = self.state.composite().map(|c| c.size()) {
                log::info!("첫 캔버스 {w}x{h}");
            }
        }
        dirty
    }

    /// 창을 안 쓰는 절반 — 셸 결과 · 시계 · 서버 메시지 · 다시 그리기 청구.
    ///
    /// 돌려주는 `Option<String>` 은 **OS 클립보드에 쓸 것**이다. 여기서 직접 쓰지 않는
    /// 이유는 그것만이 창 계층을 요구하기 때문이다 — 그 한 줄 때문에 나머지 전부가
    /// 테스트 밖에 있었다.
    fn pump_messages(&mut self) -> (bool, Option<String>) {
        let mut clip = None;
        // 셸 결과는 스레드에서 온다 — 퍼올리기와 같은 자리에서 줍는다(TUI 는
        // `tick_clock` 이 같은 일을 한다).
        let (shell, next) = self.take_shell_result();
        if let Some(action) = next {
            self.apply_action(action);
        }
        // 원격에 올린 그림도 스레드에서 온다 — 셸 결과와 **같은 자리**에서 줍는다.
        let pasted = self.take_remote_image();
        // 시계는 서버 메시지가 없어도 흐른다 — 퍼올리기와 같은 자리에서 재운다
        // (GUI 에는 TUI 의 프레임 루프에 해당하는 자리가 여기뿐이다).
        let mut dirty = self.tick_clock() || shell || pasted;
        // 입력기 배지는 서버 메시지와 무관하게 바뀐다 — 시계와 같은 자리에서 잰다.
        dirty |= self.tick_ime();
        let mut arrived = 0usize;
        for event in self.link.drain() {
            arrived += 1;
            match event {
                // 상류 트랜스크립트 꼬리는 상태 누적기가 아니라 여기서 받는다 — 이건
                // 화면 상태가 아니라 **패널마다 따로 파싱해 두는 원문**이다.
                LinkEvent::Message(msg) => match *msg {
                    ServerMessage::Claude { pane, tail } => {
                        self.remote.apply(pane, &tail);
                    }
                    // pong 은 시계를 든 쪽의 일이다(G9u — TUI 와 같은 규칙). 다시 그릴
                    // 일은 정보 팝업이 떠 있을 때뿐이다.
                    ServerMessage::Pong { ts } => {
                        if let Some(ts) = ts {
                            let now = self.pinger.now();
                            if now >= ts {
                                self.state.rtt_mut().sample(now, now - ts);
                            }
                        }
                        if matches!(self.screens.top(), Some(Screen::InfoTabs)) {
                            dirty = true;
                        }
                    }
                    // 선택 회신은 **상태가 아니라 부수효과**다 — 클립보드로 가야 하고,
                    // 그건 상태 누적기가 할 수 없다. 그래서 `apply` 앞에서 가로챈다.
                    ServerMessage::Selection { text } => {
                        // 앱이 접은 줄바꿈은 붙여넣는 곳에서 뜻이 없다 — 여기서 되돌린다
                        // (설정 `copy-unwrap`, 기본 켜짐 · 정본과 같은 자리).
                        let text = self.unwrap_copied(text);
                        // 서버 페이스트 버퍼와 OS 클립보드를 **둘 다** 채운다. 클립보드가
                        // 안 되는 상자에서도 pytmux 안에서의 붙여넣기는 되어야 한다.
                        if let Some(cmd) = selection_to_buffer(&text) {
                            self.pending.push(Outgoing::Command(cmd));
                            let chars = text.chars().count();
                            // GUI 는 창 계층의 클립보드를 쓴다 — TUI 의
                            // `clip`(외부 도구)은 **바깥 터미널밖에 없는**
                            // 클라를 위한 것이고, 여기서 그걸 부르면 PowerShell cold
                            // start(0.5~2초) 동안 창이 멈춘다. 쓰는 것은 호출부다(위).
                            clip = Some(text);
                            self.note_flash(copy_note(chars, true), Severity::Ok);
                            dirty = true;
                        }
                    }
                    other => {
                        let kind = other.kind();
                        // ★ 플러그인 화면이 왔다(P4) — 스펙이 목록인지 글인지에 따라
                        //   키가 달라지므로 열 때 그것을 알려 준다(core 는 스펙을 모른다).
                        let opened = match &other {
                            ServerMessage::PluginScreen(spec) => Some(spec.is_selectable()),
                            _ => None,
                        };
                        let was_close = matches!(other, ServerMessage::PluginScreenClose { .. });
                        // 훅이 볼 사건(붙음·탭 늘어남·벨)은 **어떤 메시지가 왔는지**를
                        // 봐야 알 수 있다(패리티 G8u · TUI 와 같은 규칙).
                        let was_layout = matches!(other, ServerMessage::Layout(_));
                        let was_status = matches!(other, ServerMessage::Status(_));
                        let was_restart_check =
                            matches!(other, ServerMessage::RestartCheck { .. });
                        let was_search_results =
                            matches!(other, ServerMessage::SearchResults(_));
                        let changed = self.state.apply(other);
                        // 패널 앱이 `OSC 52` 로 넣어 달라 한 글을 **여기서** OS 클립보드에
                        // 넣는다(pytmux-420 ①). `apply` 안에서 안 하는 이유는 그쪽이 순수한
                        // 상태 접기라서다 — 시험·재생이 같은 프레임을 여러 번 먹이면 그
                        // 상자의 클립보드가 조용히 덮인다. 실패해도 조용하다: 앱이 한
                        // 복사라 사용자에게 띄울 토스트가 아니다(선택할 때마다 뜬다).
                        if let Some(text) = self.state.take_clipboard() {
                            // `set set-clipboard off` 면 걷기만 하고 안 쓴다 — 걷는 것은
                            // 꺼져 있어도 해야 한다(안 걷으면 켠 순간 옛 복사가 튀어나온다).
                            if self.config.set_clipboard {
                                let _ = clip::copy(&text);
                            }
                        }
                        // ★ 플러그인 표면을 화면 상태에 실어 준다(설계 Tier A · P2).
                        //   키 처리(메뉴 층·설정 분류 이동)와 그리기가 **같은 목록**을
                        //   봐야 한다 — 둘이 갈리면 "고른 줄과 실행된 줄이 다르다"가
                        //   되고 그건 눈으로 못 찾는다.
                        if was_status {
                            self.screens.set_plugins(self.state.plugin_surface().clone());
                        }
                        // 전역 검색 결과(pytmux-27) — **기다리던 회신일 때만** 연다
                        // (정본 `_want_search_all` 과 같은 게이트). 뒤늦거나 남의
                        // 요청의 회신이면 상태만 갈아 끼워지고 화면은 그대로다.
                        if was_search_results && self.awaiting_search_all {
                            self.awaiting_search_all = false;
                            self.screens.open(Screen::SearchResults);
                            self.screens.select_row(0);
                        }
                        if let Some(is_list) = opened {
                            // ★ 물음·확인은 **이 클라가 이미 잘하는 일**이다(입력 이력·
                            //   버튼 둘·기본이 '아니오'). 플러그인이 물었다고 그 화면을
                            //   한 벌 더 만들면 되돌릴 수 없는 것 앞의 규칙이 두 곳에 생긴다.
                            // ★ 물음 문구의 주인은 **플러그인**이다(`ask_text` — 첫 줄이
                            //   물음, 나머지가 상세). 안 실어 주면 되돌릴 수 없는 것 앞에서
                            //   "플러그인이 물었다:" 한 줄만 보인다.
                            // ★ 입력칸의 **초기값도 스펙이 정한다**(pytmux-35). 고치는
                            //   화면인데 지금 값이 안 실리면 '편집'이 아니라 '다시 치기'가
                            //   된다 — `claude-rules`(시작 규칙)·`namesync`(경로·키워드)가
                            //   그 부류다. 실을 것이 없는 물음은 종전대로 빈 칸이다.
                            let (kind, ask, sel, seed) = match self.state.plugin_screen() {
                                Some(spec) => (
                                    spec.kind.clone(),
                                    spec.ask_text(),
                                    spec.selected,
                                    spec.text.clone(),
                                ),
                                None => (String::new(), String::new(), 0, String::new()),
                            };
                            match kind.as_str() {
                                "prompt" => {
                                    self.screens.ask_with_detail(
                                        base::Prompt::PluginAsk,
                                        &seed,
                                        ask,
                                    );
                                }
                                "confirm" => {
                                    self.screens.confirm_with(base::Prompt::PluginAsk, ask);
                                }
                                _ => {
                                    self.screens.open_plugin_view(is_list);
                                    // ★ 커서 자리도 **스펙이 정한다**. 목록을 갈아 끼우는
                                    //   것은 늘 사용자의 손짓에 대한 답이라(디렉터리 이동·
                                    //   태그) 어디에 놓아야 하는지는 만든 쪽이 안다.
                                    //   **고르는 화면일 때만** — 글 화면(상세)에서 커서를
                                    //   건드리면 `Esc` 로 목록에 돌아왔을 때 자리를 잃는다.
                                    if is_list {
                                        self.screens.select_row(sel);
                                    }
                                }
                            }
                        }
                        // 서버가 닫으라고 했으면 판도 접는다(플러그인이 흐름을 끝냈다).
                        if was_close
                            && self.state.plugin_screen().is_none()
                            && self.screens.top() == Some(Screen::PluginView)
                        {
                            self.screens.close_top();
                        }
                        // ★ 드라이런 회신이 곧 게이트다(TUI 와 같은 자리). 안 보면 회신이
                        // 화면에만 쌓이고 재시작은 영영 시작되지 않는다.
                        if was_restart_check {
                            dirty |= self.gate_restart();
                        }
                        // 화면이 멎었을 때 층을 가르는 줄(`RUST_LOG=debug`). "안 왔다"와
                        // "왔는데 화면을 안 바꿨다"는 화면에서 똑같이 보인다.
                        if !changed {
                            log::debug!("반영 안 된 메시지: {kind}");
                        }
                        dirty |= changed;
                        let mut events = Vec::new();
                        if was_layout {
                            events.extend(self.hook_watch.saw_layout());
                        }
                        if was_status {
                            let tabs = self.state.tabs();
                            let count = tabs.tabs.len();
                            let bell = tabs.tabs.iter().any(|tab| tab.bell);
                            events.extend(self.hook_watch.saw_status(count, bell));
                        }
                        if !events.is_empty() {
                            self.fire_hooks(&events);
                            dirty = true;
                        }
                    }
                },
                LinkEvent::Ended(reason) => {
                    // ⛔ **로그에도 남긴다**(pytmux-171). 종전에는 사유가 화면의 빨간 한
                    //    줄로만 갔다 — 제보자는 `--frame-dump` 그림에서 **눈으로** 보고서야
                    //    알았고 `stdout`/`stderr` 은 둘 다 비어 있었다. 끊김은 진단이
                    //    필요한 사건이라 자취가 화면 밖에도 있어야 하고, 그 줄이 없으면
                    //    다음 사람은 같은 그림을 보고도 지나친다.
                    //
                    //    `error!` 인 이유: 기본 로그 수준에서도 보여야 한다(`debug!` 로
                    //    두면 `RUST_LOG` 를 미리 켜 둔 사람만 본다 — 끊김은 미리 켜 둘
                    //    수 있는 사건이 아니다).
                    log::error!("서버 연결이 끝났다: {reason}");
                    self.note_ended(reason);
                    dirty = true;
                }
            }
        }
        // 화면이 멎었을 때 층을 가르는 줄(기본값에서는 안 나온다 — `RUST_LOG=debug`).
        // ⑴서버 메시지가 아예 안 오나 ⑵와서 반영이 안 되나를 이 한 줄이 가른다. 실제로
        // 2026-07-28 에 "키는 서버까지 가는데 그림만 안 바뀌는" 자리를 만나 넣었다.
        // 서버 오류가 왔으면 하단 한 줄로 옮긴다(§10-21ⓝ — 자리가 하나가 됐다).
        // 펌프 끝에서 하는 이유: 렌더에서 하면 그리기가 상태를 고치게 된다.
        dirty |= self.adopt_error();
        if arrived > 0 {
            log::debug!("펌프: 메시지 {arrived}개 · 다시 그림={dirty}");
        }
        (dirty, clip)
    }

    pub fn is_ended(&self) -> bool {
        self.ended.is_some() || self.state.is_closed()
    }

    /// 배율을 먹인 글자 크기(§10-21ⓐ).
    ///
    /// # 왜 여기 한 자리인가
    ///
    /// 제보가 "패널 캔버스만이 아니라 **앱 전체**"라고 못박았다. 글자를 만드는 자리는
    /// 셋뿐이라([`text`](Self::text)·[`ui_text`](Self::ui_text)·[`render_row`](Self::render_row))
    /// 곱하는 자리를 이 함수 하나로 모으면 새 화면이 늘어도 자동으로 따라온다 — 호출부
    /// 마다 곱하면 **한 곳을 빠뜨렸을 때 그 줄만 안 커지고**, 그건 조용한 어긋남이다.
    fn scaled(&self, size: f32) -> f32 {
        size * self.config.font_scale
    }

    fn text(&self, s: impl Into<String>, size: f32, color: ColorU) -> Box<dyn Element> {
        Text::new_inline(s.into(), self.font, self.scaled(size))
            .with_color(color)
            .finish()
    }

    /// **격자에 못박은** 한 줄 — 글자 그림(표·그래프)을 그리는 자리에 쓴다.
    ///
    /// [`text`](Self::text) 는 줄을 통짜로 셰이퍼에 넘긴다. ASCII 만 있으면 고정폭
    /// 글꼴이 알아서 칸을 맞추지만, **비 ASCII 는 폴백 글꼴에서 오고 그 진폭은 다르다** —
    /// 세로 막대(`▁▂▃…`)·축(`┤┄─`)이 섞인 줄은 그래서 조금씩 밀린다. 캔버스 줄이 쓰는
    /// 규칙([`grid_segments`](Self::grid_segments) + 칸너비 못박기)을 그대로 쓴다.
    ///
    /// 칸너비를 아직 못 쟀으면(첫 프레임) 종전처럼 셰이퍼에 맡긴다 — 한 프레임 뒤에
    /// 제자리로 온다(`render_row` 와 같은 처방).
    fn mono_row(&self, line: &str, size: f32, color: ColorU) -> Box<dyn Element> {
        let Some((cell_w, _)) = self.cell_px.get() else {
            return self.text(line.to_owned(), size, color);
        };
        let mut row = Flex::row().with_main_axis_size(MainAxisSize::Min);
        for (piece, cells) in Self::grid_segments(line) {
            let boxed = piece.chars().any(|c| !c.is_ascii() || c.is_ascii_control());
            let cell = self.text(piece, size, color);
            // ASCII 조각은 자연폭이 곧 `칸수 × 칸너비`다 — 거기에 잰 값을 덮어씌우면
            // 부동소수 한 톨 차이로 마지막 글자가 흐려질 수 있다(`render_row` 와 같은 이유).
            row = row.with_child(if boxed {
                // 캔버스 줄과 **같은 상자**다 — 여기만 자르면 표·그래프의 기호가 잘린다.
                CellBox::new(cell, cell_w * cells as f32).finish()
            } else {
                cell
            });
        }
        row.finish()
    }

    /// 크롬 글자 — 가변폭([`theme`]). 캔버스·팝업 본문은 [`text`](Self::text)(고정폭)다.
    fn ui_text(&self, s: impl Into<String>, size: f32, color: ColorU) -> Box<dyn Element> {
        Text::new_inline(s.into(), self.ui_font, self.scaled(size))
            .with_color(color)
            .finish()
    }

    /// **판 바닥의 안내줄** — 이 줄만 접힌다(pytmux-371 ⓐ).
    ///
    /// 판 폭은 화면이 정하고 내용이 못 정한다(pytmux-158). 그 규칙 아래에서 긴 안내줄은
    /// 아래 `Clipped` 의 «마지막 그물»에 걸려 **오른쪽이 통째로 잘렸다** — 실측
    /// (2026-08-30 · 1280x800 · 배율 1.5) Period 판이 `… · o machine · s scenari` 에서
    /// 끊겨 `u /usage` 가 사라졌고, Warn 판은 `s scena` 에서 끊겼다. Machine·Session·
    /// Limit 은 안내가 짧아 우연히 들어맞아서, **화면마다 다르게 잘렸다.**
    ///
    /// ⛔ 이것은 미관이 아니다. pytmux-185 계약에서 **꼬리줄이 광고하는 조작이 곧 그
    /// 화면의 최소 요건**이라(pytmux-371 이 요건표를 그 줄로 적었다), 광고가 안 보이면
    /// 요건이 사라진다. 잘린 자리에 `…` 를 넣는 길(elide)도 있지만 그건 **잃은 것을
    /// 정직하게 말할 뿐** 여전히 잃는다.
    ///
    /// 그래서 판을 넓히지 않고(폭 규칙을 지킨다) 이 줄만 [`soft_wrap`] 을 켠다 —
    /// [`Text::new_inline`] 은 `soft_wrap: false` 이고 [`Text::new`] 는 참이 기본이다.
    /// 넘치면 아래로 한 줄 접히고 판이 그만큼만 높아진다.
    fn hint_text(
        &self,
        s: impl Into<String>,
        font: FamilyId,
        size: f32,
        color: ColorU,
    ) -> Box<dyn Element> {
        Text::new(s.into(), font, self.scaled(size)).with_color(color).finish()
    }

    /// 같은 크롬 글자인데 **굵기를 고른다**. 정본이 굵게 쓰는 자리(탭 완료 알림
    /// `done_st`·모드 표식)를 같은 무게로 그리기 위한 것이다.
    fn ui_text_weighted(
        &self,
        s: impl Into<String>,
        size: f32,
        color: ColorU,
        bold: bool,
    ) -> Box<dyn Element> {
        Text::new_inline(s.into(), self.ui_font, self.scaled(size))
            .with_color(color)
            .with_style(Properties {
                weight: if bold { Weight::Bold } else { Weight::Normal },
                style: FontStyle::Normal,
            })
            .finish()
    }

    /// 배율을 바꾼 뒤 한 마디. **값을 그대로 보인다** — 배율은 "지금 몇 배인가"가
    /// 유일한 상태이고, 화면에서 그것을 읽을 다른 자리가 (설정 화면 말고는) 없다.
    fn note_font_scale(&mut self) {
        let scale = format!("{:.1}", self.config.font_scale);
        self.state
            .note_notice(tf("글자 크기: {scale}×", &[("scale", &scale)]));
    }

    /// 칩 — pill 배경 위 한 낱말(모드 배지·세션 표식). 띠(SURFACE) 위에 앉으므로
    /// 배경은 한 단 밝은 HOVER 다.
    fn chip(&self, s: impl Into<String>, fg: ColorU) -> Box<dyn Element> {
        Container::new(self.ui_text(s, 11., fg))
            .with_horizontal_padding(8.)
            .with_vertical_padding(2.)
            .with_background_color(theme::HOVER)
            .with_corner_radius(theme::PILL_RADIUS)
            .finish()
    }

    /// 입력기 배지 `[한]`/`[EN]` — **글자를 받는 판**의 입력줄 오른쪽 끝에 붙는다(pytmux-14).
    ///
    /// # 왜 판 안에도 필요한가
    ///
    /// 캔버스 쪽 배지는 서버 플러그인이 그린다(`ime-indicator` 의 `plugin_cells` → Tier B).
    /// 그 자리 규칙은 *"커서가 있는 줄, 활성 패널 오른쪽 끝"* 이고, 근거는 **"이 배지는
    /// '다음 글자가 무엇이 될지'를 말하는데 그때 눈은 커서에 있다"** 였다.
    ///
    /// 판이 열리면 그 근거가 그대로 판을 가리킨다 — 커서는 판 안 입력줄에 있고, 캔버스
    /// 배지는 판 **뒤**에 깔려 보이지도 않는다. 그래서 같은 원칙을 일반화한다:
    /// **지금 글자를 받는 곳의 오른쪽 끝**. 두 경우가 한 규칙이 된다.
    ///
    /// # 왜 서버가 못 그리나
    ///
    /// 판은 클라가 그리는 크롬이라 서버 화면 모델에 **없다**. 판 안 입력줄의 자리를 아는
    /// 것은 그 판을 그리는 클라뿐이다(정본도 같은 사정이라 자기 위젯 자리를 쓴다).
    /// 갈리면 안 되는 것은 **모양**인데, 그건 의미 색 어휘 한 벌
    /// ([`proto::session::theme`])을 함께 쓰는 것으로 지킨다 — 정본 플러그인의
    /// `_THEME = {"한": "success", "EN": "primary"}` 와 같은 이름이다.
    /// ⚠ 여기서 `theme::resolve` 를 안 쓰고 팔레트를 직접 집으면 pytmux-16 이 재발한다
    /// (`primary` 가 표에 없어 `[EN]` 이 통째로 안 보였다).
    /// 비율 하나를 **가로 막대**로(pytmux-371 ③ · 스펙의 `bar` = 천분율).
    ///
    /// 트랙(빈 부분)까지 그리는 이유: 막대만 그리면 «작은 값»과 «없는 값»이 같은 그림이
    /// 된다. 트랙이 있으면 그 줄이 전체의 얼마인지 눈이 바로 잡는다.
    ///
    /// 색은 [`theme::INVERT_BG`](crate::theme::INVERT_BG) — 정본이 막대에 쓰는 `primary`
    /// 의 뜻이고, 이 테마에서 그 뜻을 지는 파랑이다.
    fn ratio_bar(&self, permille: u16) -> Box<dyn Element> {
        const W: f32 = 120.;
        const H: f32 = 8.;
        let radius = warpui::elements::CornerRadius::with_all(
            warpui::elements::Radius::Pixels(H / 2.),
        );
        // 0 이어도 **한 픽셀은 남긴다** — 0 과 «막대 없음」을 화면에서 갈라 준다.
        let filled = (W * f32::from(permille.min(1000)) / 1000.).max(1.);
        let fill = ConstrainedBox::new(
            Rect::new()
                .with_background_color(theme::INVERT_BG)
                .with_corner_radius(radius)
                .finish(),
        )
        .with_width(filled)
        .with_height(H)
        .finish();
        let track = Container::new(
            Flex::row()
                .with_main_axis_size(MainAxisSize::Min)
                .with_child(fill)
                .finish(),
        )
        .with_background_color(theme::HOVER)
        .with_corner_radius(radius);
        ConstrainedBox::new(track.finish()).with_width(W).with_height(H).finish()
    }

    fn ime_chip(&self) -> Option<Box<dyn Element>> {
        let label = self.ime_badge?;
        // 정본 플러그인과 **같은 의미 이름**. 모르는 이름이면 안 칠하는 대신 안 그린다 —
        // 검은 글자만 남아 "배지가 사라진" 것처럼 보이는 것이 pytmux-16 의 모양이었다.
        let name = if label == "한" { "success" } else { "primary" };
        let bg = match proto::session::theme::resolve(name) {
            proto::session::theme::Resolution::Color(c) => to_gui_color(&c),
            _ => return None,
        };
        // ★ **글자가 아니라 그림이다**(pytmux-392) — 캔버스 배지와 **같은 어휘**를 쓴다:
        //   알약 + 손잡이, 손잡이가 왼쪽이면 한글. 두 자리(캔버스·판)가 같은 모양이라야
        //   같은 뜻으로 읽힌다(pytmux-14 가 문구를 하나로 못박은 것과 같은 이유이고,
        //   여기서는 «문구» 자리에 «모양» 이 온 것이다).
        let (w, h) = (18., 10.);
        let pad = h / 6.;
        let d = h - pad * 2.;
        let knob = Rect::new()
            .with_background_color(palette::BLACK)
            .with_corner_radius(warpui::elements::CornerRadius::with_all(
                warpui::elements::Radius::Percentage(50.),
            ));
        let knob = ConstrainedBox::new(knob.finish()).with_width(d).with_height(d).finish();
        let mut row = Flex::row()
            .with_main_axis_size(MainAxisSize::Max)
            .with_cross_axis_alignment(CrossAxisAlignment::Center);
        // 한글이면 손잡이가 왼쪽, 영문이면 오른쪽 — 자리가 곧 상태다.
        row = if label == "한" {
            row.with_child(knob).with_child(Expanded::new(1., Empty::new().finish()).finish())
        } else {
            row.with_child(Expanded::new(1., Empty::new().finish()).finish()).with_child(knob)
        };
        let track = ConstrainedBox::new(
            Container::new(row.finish())
                .with_horizontal_padding(pad)
                .finish(),
        )
        .with_width(w)
        .with_height(h)
        .finish();
        Some(
            Container::new(track)
                .with_background_color(bg)
                .with_corner_radius(theme::PILL_RADIUS)
                .finish(),
        )
    }

    /// 입력줄 하나를 **줄 통째**로 만든다 — 왼쪽에 친 글, 오른쪽 끝에 입력기 배지.
    ///
    /// 배지가 없으면(비 Windows·상태 미상) 줄을 안 감싸고 그대로 돌려준다: 빈 `Flex` 로
    /// 감싸면 줄 높이·정렬이 배지 유무에 따라 흔들린다.
    fn input_line(&self, line: Box<dyn Element>) -> Box<dyn Element> {
        let Some(chip) = self.ime_chip() else {
            return line;
        };
        Flex::row()
            .with_main_axis_size(MainAxisSize::Max)
            .with_main_axis_alignment(warpui::elements::MainAxisAlignment::SpaceBetween)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_child(line)
            .with_child(chip)
            .finish()
    }

    /// **반전 칩**(§10-21ⓖ·ⓧ) — 배경을 채우고 글자를 빼낸다.
    ///
    /// 쓰는 자리 둘: `esc` 모드 표식(ⓖ — *"배경·글자 색을 반전해 눈에 띄게"*)과
    /// **켜진 토글 배지**(ⓧ — *"눌러 스크롤바가 뜨면 버튼도 토글 상태임을 알 수 있어야"*).
    /// 두 제보가 같은 그림을 요구하므로 헬퍼도 하나다.
    fn chip_on(&self, s: impl Into<String>) -> Box<dyn Element> {
        Container::new(self.ui_text(s, 11., theme::INVERT_FG))
            .with_horizontal_padding(8.)
            .with_vertical_padding(2.)
            .with_background_color(theme::INVERT_BG)
            .with_corner_radius(theme::PILL_RADIUS)
            .finish()
    }

    /// 모드 표식 칩. **번역을 타고**, `esc` 모드만 정본의 색으로 그린다.
    ///
    /// # 왜 [`chip_on`](Self::chip_on) 이 아닌가 (pytmux-380)
    ///
    /// `chip_on` 은 파랑 반전([`theme::INVERT_BG`])이고 정본의 `esc` 모드 표식은
    /// **호박색 반전 + 검은 굵은 글자**다(`clientwidgets.py`: `bgcolor=tc("accent")` ·
    /// `color="black"` · `bold=True`). 파랑은 이 테마에서 **선택·강조**의 색이라(탭 활성
    /// 배경 계열) 모달 상태가 그 색을 쓰면 "무언가 골라져 있다"로 읽힌다 — 정본이 굳이
    /// 경고색을 고른 이유가 그것이다.
    ///
    /// 나머지 모드(`[prefix]`·`[block]`)는 정본에 짝이 없어 종전 반전 칩 그대로다.
    /// **갈림을 색으로 남기는 것**이기도 하다: 정본과 맞춘 자리와 우리가 지은 자리가
    /// 화면에서 구별된다.
    fn mode_chip(&self, mode: InputMode, badge: &'static str) -> Box<dyn Element> {
        if mode != InputMode::Command {
            return self.chip_on(t(badge));
        }
        Container::new(self.ui_text_weighted(t(badge), 11., palette::BLACK, true))
            .with_horizontal_padding(8.)
            .with_vertical_padding(2.)
            .with_background_color(CLAUDE_DONE_AMBER)
            .with_corner_radius(theme::PILL_RADIUS)
            .finish()
    }

    /// 네이티브 토글 그림(N5) — 켜짐이면 손잡이가 오른쪽·ACTIVE 바탕, 꺼짐이면
    /// 왼쪽·HOVER 바탕. **그림일 뿐**이다 — 값을 바꾸는 길은 종전 그대로(Enter/Space
    /// 키·`flip_config`)이고, 여기는 상태를 눈으로 옮긴다.
    ///
    /// ★ **테두리를 항상 그린다**(pytmux-180) — `theme::ACTIVE` 는 `palette::SELECTED_BG`
    /// (행 선택 배경)와 비트 단위로 같은 값이라, 행이 선택된 채 켜진 토글은 트랙 배경이
    /// 행 배경에 파묻혀 알약의 경계가 사라진다. 두 색을 갈라 세우는 대신(다른 화면에서도
    /// 쓰는 상수라 영향 범위가 넓다) 트랙 테두리로 배경과 무관하게 경계를 확보한다.
    fn toggle(&self, on: bool) -> Box<dyn Element> {
        let knob = Rect::new()
            .with_background_color(if on { palette::FG } else { palette::DIM })
            .with_corner_radius(warpui::elements::CornerRadius::with_all(
                warpui::elements::Radius::Percentage(50.),
            ))
            .finish();
        let knob = ConstrainedBox::new(knob).with_width(10.).with_height(10.).finish();
        let mut align = Align::new(knob);
        align = if on { align.right() } else { align.left() };
        let inner = ConstrainedBox::new(align.finish()).with_width(26.).with_height(14.).finish();
        Container::new(inner)
            .with_background_color(if on { theme::ACTIVE } else { theme::HOVER })
            .with_corner_radius(theme::PILL_RADIUS)
            .with_border(Border::all(1.).with_border_color(theme::BORDER))
            .with_horizontal_padding(2.)
            .finish()
    }

    /// 떠 있는 판의 목록 행 예산(N2). 캔버스 행수에서 판의 틀(헤더·패딩·행간)이 먹는
    /// 몫을 덜어낸다 — 캔버스 행수를 그대로 쓰면 판이 창 밖으로 넘친다(대체가 아니라
    /// 플로팅이라, 넘친 줄은 잘리는 게 아니라 **창 밖에 그려진다**. 라이브 실측 N2).
    fn panel_budget(&self) -> usize {
        self.panel_rows(self.screens.top().unwrap_or(Screen::Notices))
    }

    /// 그 판이 쓰는 **줄 수**(§10-21 ⓗ·ⓢ·ⓥ·ⓐ2·ⓚ2) — 비율의 주인은 core 다.
    ///
    /// 종전에는 화면의 8/9 에서 넷을 뺀 값 하나였다(= 거의 전체 화면). 그 값이 판마다
    /// 같은 것 자체는 문제가 아니었고, 문제는 **그 값이 상한일 뿐이라 실제 높이는
    /// 내용이 정했다**는 것이다 — 줄이 적으면 판이 작아지고 굴리면 크기가 변했다.
    /// 판의 남는 줄을 **빈 자리로 채운다**(§10-21 ⓗ·ⓢ·ⓥ·ⓐ2·ⓚ2).
    ///
    /// 폭은 못박았지만(`with_width`) 세로는 자식이 정한다 — 줄이 적으면 판이 줄고,
    /// 굴리다 끝에 가까워지면 남은 줄이 예산보다 적어 **판이 작아진다**(ⓥ 가 본 것이
    /// 그것이다). 빈 줄로 채우면 같은 판은 언제나 같은 높이다.
    #[cfg(test)]
    pub(crate) fn panel_budget_for_test(&self) -> usize {
        self.panel_budget()
    }

    /// 빈 줄을 **몇 개** 채우게 되는지(테스트가 그 수만 본다 — 엘리먼트는 못 센다).
    #[cfg(test)]
    pub(crate) fn pad_rows_count_for_test(&self, drawn: usize, budget: usize) -> usize {
        drawn + budget.saturating_sub(drawn)
    }

    /// 다열 판의 한 칸 — 이름은 왼쪽, 부가 칸은 **오른쪽 끝**에(정본 `_item_segment`).
    ///
    /// 크기를 오른자리에 세우는 이유는 읽기다: 왼쪽에 붙이면 이름 길이에 따라 숫자가
    /// 들쭉날쭉해 열끼리 비교가 안 된다. 자리가 모자라면 **이름을 자른다** — 크기를
    /// 자르면 그 숫자가 거짓이 되지만 이름은 잘려도 `…` 가 그 사실을 말한다.
    fn panel_cell(label: &str, extra: &str, cells: usize) -> String {
        let want = footer::width(extra);
        if want + 2 > cells {
            return footer::elide(label, cells);
        }
        let room = cells - want - 1;
        let name = footer::elide(label, room);
        let pad = room.saturating_sub(footer::width(&name));
        format!("{name}{} {extra}", " ".repeat(pad))
    }

    /// 다열 판의 기하 — `(열당 줄 수, 열 수)`. 다열 판이 아니면 `(0, 1)`.
    ///
    /// 열 수를 푸는 산술은 **proto 가 든다**(`PluginScreen::column_count`) — 여기서 다시
    /// 적으면 커서가 건너뛰는 폭과 그려지는 열 수가 갈릴 수 있고, 그 어긋남은 "←를
    /// 눌렀는데 엉뚱한 줄로 뛴다"로 보인다.
    ///
    /// 머리·꼬리줄도 판의 줄을 먹는다 — 안 빼면 그만큼 판이 아래로 넘친다(글 판이
    /// 구역 선을 예산에 세는 것과 같은 규율).
    fn panel_grid(&self) -> (usize, usize) {
        let Some(spec) = self.state.plugin_screen() else {
            return (0, 1);
        };
        if spec.kind != "panel" {
            return (0, 1);
        }
        let cols = spec.column_count(Self::PANEL_COLS);
        let lines = usize::from(!spec.head.is_empty()) + usize::from(!spec.foot.is_empty());
        (self.panel_budget().saturating_sub(lines).max(1), cols)
    }

    fn pad_rows(&self, column: Flex, drawn: usize, budget: usize) -> Flex {
        let mut column = column;
        for _ in drawn..budget {
            // ⛔ **채움 줄도 항목 줄과 같은 상자**를 쓴다(pytmux-369). 맨 글자로 두면
            //    한 줄이 항목보다 2px 낮아, 목록이 짧을수록(= 채움이 많을수록) 판이
            //    그만큼 짧아진다 — 분류 탭을 옮길 때마다 판이 들썩였다.
            column = column.with_child(self.panel_row_box(self.text(" ", 13., palette::DIM)));
        }
        column
    }

    /// 판의 **한 줄 상자** — 「한 줄 = 몇 px 인가」를 정하는 **유일한 자리**(pytmux-369).
    ///
    /// # 왜 헬퍼인가
    ///
    /// 종전에는 채움 줄과 항목 줄이 **각자** 지었다: 항목만 `with_uniform_padding(1.)`
    /// 로 감싸 한 줄이 2px 더 높았다. 줄 **수**는 `panel_budget` 이 상수로 쥐고 있었으니
    /// (pytmux-58) 「줄 수 = 높이」라는 불변식이 픽셀에서만 조용히 깨져 있었다.
    ///
    /// ⚠ 그 함정을 코드가 **이미 알고 있었다** — `palette_item` 의 *"안쪽 열의 행간은
    /// 판의 것과 같은 상수다"* 가 행간은 맞췄는데, 바로 다음 줄의 *"패딩은 줄마다다"*
    /// 가 패딩을 안 맞췄다. 두 곳이 각자 짓는 한 다음 판에서 또 갈린다.
    ///
    /// # ⛔ [`pad_rows`](Self::pad_rows) 를 쓰는 판은 **내용 줄도** 이걸 지난다 (pytmux-373 ⑴)
    ///
    /// pytmux-369 는 팔레트 **하나만** 고쳤다. 나머지 셋(Status·알림·플러그인 글)은
    /// 내용 줄을 맨 `text`/`mono_row` 로 두고 채움 줄만 이 상자를 썼고, 그래서 한 판의
    /// 높이가 `예산 × 한 줄 + (예산 - 그린 줄) × 2px` 였다 — **줄 수는 상수인데 픽셀이
    /// 내용을 탔다.** Status 판은 탭마다 줄 수가 달라 그것이 곧 "탭을 바꾸면 판이
    /// 들썩인다"로 보였다(제보 2026-08-23).
    ///
    /// ⚠ 제보 분석은 뿌리를 **폴백 글꼴의 줄 높이**로 짚었는데 그쪽은 아니다 —
    /// [`Text`](warpui::elements::Text) 의 높이는 `글자 크기 × line_height_ratio` 라
    /// 글리프가 어느 글꼴에서 오든 같다(`warpui_core` 의 `Line::height`). 진폭(가로)만
    /// 글꼴을 타고 세로는 안 탄다 — 그래서 남은 원인은 이 2px 하나다.
    fn panel_row_box(&self, child: Box<dyn Element>) -> Box<dyn Element> {
        Container::new(child).with_uniform_padding(1.).finish()
    }

    fn panel_rows(&self, screen: Screen) -> usize {
        let rows = self.state.composite().map_or(12, |c| c.size().1) as usize;
        let (num, den) = screen.height_ratio();
        (rows * num / den).saturating_sub(2).max(5)
    }

    /// 지금 탭 상황(정본 `client.py` 와 같은 셈) — 확인 문구가 이걸 보고 갈린다.
    ///
    /// **로컬 탭만 센다**: 마지막 로컬 탭을 닫으면 서버 세션이 비어 앱이 통째로 끝나는데,
    /// 전체 수로 세면 원격 탭이 함께 열려 있을 때 그 경고가 빠진다(정본이 실제로 그
    /// 결함을 고쳤다).
    fn tab_facts(&self) -> base::TabFacts {
        let tabs = &self.state.tabs().tabs;
        base::TabFacts {
            local: tabs.iter().filter(|t| !t.remote).count(),
            has_remote: tabs.iter().any(|t| t.remote),
            active_pinned: tabs.iter().any(|t| t.active && t.pinned),
        }
    }

    /// 활성 탭 이름 — 고정 탭 경고의 `{name}` 슬롯을 채운다(물음은 `&'static str` 이라
    /// 그때그때 다른 값을 못 담는다 — `Screens::confirm_question` 이 detail 로 채운다).
    fn active_tab_name(&self) -> String {
        self.state
            .tabs()
            .tabs
            .iter()
            .find(|t| t.active)
            .map(|t| t.name.clone())
            .unwrap_or_default()
    }

    /// 탭 닫기 확인을 **상황에 맞는 문구로** 연다.
    fn confirm_kill_tab(&mut self) {
        let prompt = base::Prompt::kill_tab(&self.tab_facts());
        if prompt == base::Prompt::KillTabPinned {
            let name = self.active_tab_name();
            self.screens.confirm_with(prompt, name);
        } else {
            self.screens.confirm(prompt);
        }
    }

    /// 크롬 자리 `i` 에 마우스가 올라와 있나 — hover 배경용. Hoverable 이 hover 변화 때
    /// 스스로 `notify` 하므로(상류 hoverable.rs) 렌더에서 읽어도 낡지 않는다.
    /// `i` 번째 크롬 자리에 마우스가 올라와 있나 — **누를 수 있을 때만** 참이다.
    ///
    /// # 왜 `config.mouse` 를 보나 (§10-21ⓑ2 의 뿌리 · 실측 2026-08-02)
    ///
    /// 제보는 *"오른쪽 하단 시각·날짜를 클릭하면 시계·달력이 떠야 하는데 **hover 효과만
    /// 나고 눌러도 아무 일이 없다**"* 였다. 라이브로 재 보니 그 기능은 **멀쩡히 동작한다**
    /// (제보자가 쓴 릴리스 이진에서도 시계·달력이 떴다). 같은 증상을 **정확히** 만드는
    /// 것은 `set mouse off` 였다 — 그러면 [`chrome_click`](Self::chrome_click) 이 첫 줄에서
    /// 돌아가는데, hover 강조는 그 설정을 안 보고 그대로 그려졌다.
    ///
    /// 즉 결함은 "클릭이 안 먹는다"가 아니라 **"안 먹는 것을 눌리는 것처럼 그렸다"** 다.
    /// 이 저장소가 팔레트·설정 표에서 되풀이해 온 규율("못 하는 것을 목록에 두면 고르는
    /// 순간 아무 일도 안 일어나고, 그건 '있는데 안 먹는다'로 읽힌다")의 마우스 판이고,
    /// `SessionState::badges` 가 `⇕` 를 **마우스가 켜졌을 때만** 싣는 것과 같은 결이다.
    fn chrome_hovered(&self, i: usize) -> bool {
        Self::hover_shown(self.config.mouse, self.mouse_over(&self.chrome_click_states, i))
    }

    /// hover 강조를 그릴까 — **순수 판정**.
    ///
    /// 순수 함수로 빼는 이유는 [`is_paste_chord`](Self::is_paste_chord) 와 같다: 이
    /// 판정이 틀리면 아무 소리 없이 어긋난다(넓으면 안 먹는 것이 눌리는 것처럼 보이고,
    /// 좁으면 눌리는 것이 죽어 보인다). 창 없이 물을 수 있는 자리에 둔다 — 그리고
    /// `MouseState` 에는 밖에서 "여기 마우스가 있다"를 세울 길이 없어(비공개 필드),
    /// 이 갈래를 재는 길이 이것뿐이기도 하다.
    pub(crate) fn hover_shown(mouse_enabled: bool, over: bool) -> bool {
        mouse_enabled && over
    }

    /// 두 풀이 같은 방식으로 "그 자리에 마우스가 있나"를 묻는다 — 자물쇠 다루는 코드를
    /// 두 번 적으면 한쪽만 고쳐진다.
    fn mouse_over(
        &self,
        pool: &std::cell::RefCell<Vec<MouseStateHandle>>,
        i: usize,
    ) -> bool {
        pool.borrow()
            .get(i)
            .is_some_and(|s: &MouseStateHandle| {
                s.lock().is_ok_and(|s| s.is_mouse_over_element())
            })
    }

    /// 탭바. **무엇을 적을지는 `proto::tabs` 가 정한다**(`Tab::label`) — 두 뷰가 각자
    /// 조립하면 같은 탭이 화면마다 달라 보인다. 여기서 정하는 것은 색과 강조뿐이다.
    ///
    /// TUI 는 활성 탭을 `▐` 표식으로만 가른다(터미널에서 그게 더 잘 읽힌다). GUI 는
    /// **탭마다 엘리먼트가 따로 있으므로** 배경으로 가른다 — 같은 정보를 각 매체에 맞는
    /// 방법으로 보이는 것이고, 이게 뷰가 따로 있는 이유다.
    /// 크롬 클릭 하나를 처리한다 — Enter 와 같은 길이다(`[x]` 는 확인 화면을 지나고,
    /// 배지는 화면을 연다 — `apply_action` 이 그 갈래를 안다). `set mouse off` 는
    /// 크롬 클릭도 끈다(TUI·파이썬과 같은 뜻).
    /// 탭 하나를 드래그 시작점으로 감싼다(G9w). 누름=드래그 시작 · 전환은 뗄 때
    /// (파이썬·TUI 와 같은 순서) — 그래서 `on_click` 이 아니라 `on_mouse_down` 이다.
    /// 놓는 자리는 창 전체 `EventHandler` 의 mouse-up 이 받고, 드롭 대상 탭은 각 탭의
    /// hover 상태(`MouseState`)로 알아낸다 — 픽셀 존 없이 **레이아웃이 잰다**.
    fn draggable_tab(&self, i: usize, child: Box<dyn Element>) -> Box<dyn Element> {
        Hoverable::new(self.chrome_mouse_state(i), |_| child)
            .on_mouse_down(move |evt, _, _| {
                evt.dispatch_typed_action(ViewAction::TabPress(i));
            })
            // ★ **`with_propagate_drag()` 가 없으면 드래그가 여기서 죽는다**(2026-07-31 실측).
            //   `Hoverable` 은 `suppress_drag` 가 **기본 참**이라, 눌린 상태에서는
            //   `LeftMouseDragged` 를 통째로 삼킨다(상류 `hoverable.rs`). 그래서 탭을 잡고
            //   끄는 동안 창 전체 `EventHandler` 의 `on_mouse_dragged` 가 **한 번도** 안 불렸고,
            //   드롭 대상(`tab_drag_over`)이 영영 `None` 이라 강조도 재정렬도 안 났다.
            //   뗌(`LeftMouseUp`)은 안 삼켜서 "드롭은 되는데 늘 빈 자리"로 보였다 —
            //   그게 G9w 이후 라이브에서 잡히던 증상의 정체다.
            .with_propagate_drag()
            .finish()
    }

    /// 마우스가 지금 어느 탭 위에 있나(드래그의 드롭 대상 — 탭은 렌더 순서상
    /// 크롬 자리 0..탭수 를 쓴다).
    fn hovered_tab(&self) -> Option<usize> {
        let states = self.chrome_click_states.borrow();
        (0..self.state.tabs().tabs.len()).find(|&i| {
            states
                .get(i)
                .is_some_and(|s| s.lock().is_ok_and(|s| s.is_mouse_over_element()))
        })
    }

    /// 탭 드래그를 놓았다(G9w). 자리는 hover/캔버스 좌표가, 뜻은 core `drag_drop` 이
    /// 정한다 — TUI `drop_tab` 과 같은 표.
    fn drop_tab(&mut self, src: usize, at: Option<(u16, u16)>) {
        use base::chrome::{DragDrop, DragTab, DragTarget, drag_drop};
        let target = if let Some(i) = self.hovered_tab() {
            DragTarget::Tab(i)
        } else if let Some((pane, horizontal)) =
            at.and_then(|(x, y)| self.state.tab_drop_at(x, y))
        {
            DragTarget::Content { pane, horizontal }
        } else {
            DragTarget::Other
        };
        let tabs = self.state.tabs().tabs.clone();
        let facts: Vec<DragTab> =
            tabs.iter().map(|t| DragTab { remote: t.remote, pinned: t.pinned }).collect();
        match drag_drop(&facts, src, target) {
            Some(DragDrop::Select(i)) => {
                if let Some(tab) = tabs.get(i) {
                    self.pending
                        .push(Outgoing::Command(Command::SelectWindow {
                            index: tab.index,
                            wid: tab.wid,
                        }));
                }
            }
            Some(DragDrop::Reorder { from, to }) => {
                if let (Some(f), Some(t)) = (tabs.get(from), tabs.get(to)) {
                    self.pending.push(Outgoing::Command(Command::MoveTab {
                        index: f.index,
                        to: t.index,
                    }));
                }
            }
            Some(DragDrop::SetPin { index, on }) => {
                if let Some(tab) = tabs.get(index) {
                    self.pending.push(Outgoing::Command(Command::SetPinned {
                        index: Some(tab.index),
                        on,
                    }));
                }
            }
            // 대상 패널을 먼저 활성화하고 합친다(두 명령·순서 고정 — TUI 와 같다).
            Some(DragDrop::Join { pane, src, horizontal }) => {
                let index = match tabs.get(src) {
                    Some(tab) => tab.index,
                    None => return,
                };
                self.pending.push(Outgoing::Command(Command::SelectPaneId { id: pane }));
                self.pending
                    .push(Outgoing::Command(Command::JoinPane { src: index, horizontal }));
            }
            None => {}
        }
    }

    pub fn chrome_click(&mut self, target: base::chrome::ClickTarget) -> bool {
        // ★ **이 한 줄이 pytmux-366 의 이분법을 가른다** — 「클릭이 앱에 닿았나」.
        //
        //   그 이슈의 세 입구(시각 클릭 · `prefix t` · `:clock-mode`)는 모두
        //   `push_overlay_toggle` 로 모이는데, 라이브에서 시각만 무동작이었다. 뒤 둘은
        //   맥에서 뜨는 것을 화소로 확인했으므로(2026-09-01) 남은 물음은 **클릭이 여기
        //   까지 왔나** 하나인데, 그것을 재려면 종전에는 상류의 `dispatching typed
        //   action` 을 `RUST_LOG=debug` 로 켜야 했다 — 잴 사람이 그 한 줄을 못 켜서
        //   회차가 판정 없이 끝난 적이 있다(2026-08-30).
        //
        //   ⚠ 로그만 남기고 **알림은 안 띄운다**: 크롬 클릭은 잦고, 정본 TUI 는 마우스가
        //     꺼졌을 때 아무 말도 안 한다(단말이 아예 이벤트를 안 보낸다). 여기서 말풍선을
        //     띄우면 그것이 정본에 없는 갈림이 된다.
        log::info!("크롬 클릭: {target:?}");
        if !self.config.mouse {
            // ⛔ 종전에는 **여기서 말없이** 돌아갔다. 같은 증상의 옛 제보(2026-08-02)의
            //    뿌리가 정확히 `set mouse off` 였는데(위 `chrome_hovered` 의 긴 주석),
            //    그때도 이 갈래는 자취를 안 남겨 라이브로 재현할 때까지 못 가렸다.
            log::info!("… 그런데 `set mouse off` 라 아무 일도 안 한다");
            return false;
        }
        // 하단 한 줄 닫기(§10-21ⓦ⑵)는 **뷰 로컬**이라 액션 표를 안 지난다 — 서버도
        // 정본도 "내 화면의 이 줄을 지워라"라는 명령을 갖지 않는다.
        // ⚠ 이력은 안 지운다(`note_error_history` 가 이미 따로 갖고 있다) — 지우면
        //   그 줄을 눌러 이력으로 가는 동선(ⓦ⑶)이 무의미해진다.
        if matches!(target, base::chrome::ClickTarget::DismissMessage) {
            self.flash = None;
            return true;
        }
        // ★ 세션 이름 자리(pytmux-3)도 액션 표를 안 지난다 — 여는 것이 화면이 아니라
        //   **이 클라의 편집 상태**다. 이미 편집 중이면 커서만 누른 자리로 옮긴다
        //   (파이썬 `on_mouse_down` 의 그 분기와 같다).
        match target {
            base::chrome::ClickTarget::SessionName => return self.begin_session_rename(),
            base::chrome::ClickTarget::SessionCursor(i) => {
                let Some(edit) = self.session_edit.as_mut() else {
                    // 편집이 아닌데 글자칸이 눌렸다 — 프레임 사이에 편집이 끝났다.
                    return self.begin_session_rename();
                };
                edit.set_cursor(i);
                return true;
            }
            _ => {}
        }
        // ★ 플러그인 표식(pytmux-20)도 액션 표를 안 지난다 — **무엇이 열리는지는 서버가
        //   정한다**. 표식이 실어 온 이름을 그대로 되돌려 보내고, 그 이름의 뜻은 우리가
        //   모른다(오버레이의 `do` 와 같은 규약). 이름이 없으면 아무 일도 안 한다 —
        //   그런 표식은 애초에 클릭 대상으로 안 감쌌지만, 프레임 사이에 목록이 바뀌면
        //   낡은 자리를 누를 수 있다.
        if let base::chrome::ClickTarget::PluginBadge(i) = target {
            let Some(name) = self
                .state
                .plugin_badges()
                .get(i)
                .and_then(|b| b.opens())
                .map(str::to_owned)
            else {
                return false;
            };
            self.pending.push(Outgoing::Command(Command::PluginOpen {
                name,
                args: Vec::new(),
            }));
            return true;
        }
        let tabs = self.chrome_tabs();
        let badges = self.state.badges();
        let ctx = self.chrome_ctx(&tabs, &badges);
        if let Some(action) = base::chrome::click(target, &ctx) {
            self.apply_action(action);
        }
        true
    }

    /// `i` 번째 크롬 자리의 마우스 상태 — 없으면 만들어 둔다(자리 수는 탭 수를 따라
    /// 프레임마다 달라진다. 색인은 렌더 순서라 프레임 안에서 안정적이다).
    fn chrome_mouse_state(&self, i: usize) -> MouseStateHandle {
        let mut states = self.chrome_click_states.borrow_mut();
        while states.len() <= i {
            states.push(Default::default());
        }
        states[i].clone()
    }

    /// 판 안 위젯 `i` 번째의 마우스 상태(크롬 풀과 별개 — 위 필드 문서 참조).
    fn panel_mouse_state(&self, i: usize) -> MouseStateHandle {
        let mut states = self.panel_click_states.borrow_mut();
        while states.len() <= i {
            states.push(Default::default());
        }
        states[i].clone()
    }

    /// 판 안 한 자리를 클릭 대상으로 감싼다.
    ///
    /// 크롬과 같은 구조다 — **자리는 레이아웃이 재고 뜻은 core 가 정한다**
    /// (`screens::PanelTarget`). 이 화면들은 여기 오기 전까지 전부 키보드 전용이었다.
    fn clickable_panel(
        &self,
        i: usize,
        target: base::PanelTarget,
        child: Box<dyn Element>,
    ) -> Box<dyn Element> {
        Hoverable::new(self.panel_mouse_state(i), |_| child)
            .on_click(move |evt, _, _| {
                evt.dispatch_typed_action(ViewAction::PanelClick(target));
            })
            .finish()
    }

    // ── 세션 이름 제자리 편집(pytmux-3) ────────────────────────────────────────
    //
    // 제보 그대로다: *"세션 이름을 클릭하면 그 자리에서 바로 글자를 고쳐 리네임할 수
    // 있어야 한다"* — **판을 안 띄운다.** 그래서 이 편집에는 키를 가져갈 화면이 없고,
    // 값·키 라우팅·커밋을 뷰가 직접 잇는다(버퍼와 키 표는 core: `base::SessionEdit`).

    /// 지금 화면에 `#S` 가 펼쳐진 자리가 **있나**.
    ///
    /// 기본 `status-left` 는 두 클라 모두 `" "` 라, 사용자가 `set status-left " #S "`
    /// 같은 것을 넣기 전에는 **누를 대상이 없는 것이 정상**이다(제보의 ⛔ 그대로).
    fn session_zone_visible(&self) -> bool {
        let sctx = self.state.status_ctx();
        [&self.config.status_left, &self.config.status_right].iter().any(|fmt| {
            status::expand_parts(fmt, &sctx)
                .iter()
                .any(|(kind, text)| *kind == status::StatusRun::Session && !text.is_empty())
        })
    }

    /// 편집 중인가 — **묻는 김에 유령이면 끝낸다**.
    ///
    /// ⛔ 편집 중에 `#S` 자리가 사라지면(형식이 바뀌었다·세션 이름이 비었다) 보이지도
    /// 않는 입력칸이 키를 계속 삼켜 **패널이 먹통**이 된다(파이썬이 렌더 꼬리에서 같은
    /// 것을 막는다). 여기는 그리기가 `&self` 라 그 자리에 못 두므로, 키가 들어오는
    /// 길목에서 판정한다 — 먹통을 만드는 것이 바로 그 키들이다.
    fn session_editing(&mut self) -> bool {
        if self.session_edit.is_some() && !self.session_zone_visible() {
            self.session_edit = None;
        }
        self.session_edit.is_some()
    }

    /// 세션 이름 자리를 눌렀다 — 그 자리를 입력칸으로 연다.
    ///
    /// `#S` 가 안 펼쳐졌으면 아무 일도 안 일어난다(누를 자리가 없다). 다른 모드
    /// (prefix·scroll·esc)에 있었다면 평소 모드로 되돌린다 — 그 모드들이 편집을
    /// 가로채지는 않지만(키 라우팅이 앞선다), 편집을 끝낸 뒤 엉뚱한 모드에 남아 있으면
    /// 안 된다(파이썬 `begin_session_rename` 과 같은 뒷정리).
    pub(crate) fn begin_session_rename(&mut self) -> bool {
        // ⚠ 판이 떠 있으면 안 연다 — **키가 그 판의 것**이라(core 규칙) 열어 봐야 글자를
        //   못 받는 입력칸이 된다. 상태줄은 판 뒤에서도 눌리므로 여기서 막는다.
        if self.session_edit.is_some()
            || self.screens.top().is_some()
            || !self.session_zone_visible()
        {
            return false;
        }
        self.session_edit = Some(base::SessionEdit::new(&self.state.status_ctx().session));
        self.mode.reset();
        self.chrome.reset();
        true
    }

    /// 편집 중 키 하나. **여기서 전부 먹는다** — 한 갈래라도 흘리면 편집 중에 셸이
    /// 글자를 받는다. 표는 core 가 갖는다(두 클라가 같아야 하는 것이다).
    fn session_edit_key(&mut self, key: Key, mods: Mods) -> bool {
        let Some(edit) = self.session_edit.as_mut() else {
            return false;
        };
        match edit.press(key, mods) {
            base::SessionEditKey::Consumed => {}
            base::SessionEditKey::Cancel => self.session_edit = None,
            base::SessionEditKey::Commit(name) => {
                self.session_edit = None;
                // 비었거나 그대로면 안 보낸다 — 서버도 같은 판정을 하지만 뜻 없는
                // 왕복을 만들지 않는다(파이썬과 같다).
                if !name.is_empty() && name != self.state.status_ctx().session {
                    self.pending
                        .push(Outgoing::Command(Command::RenameSession { name }));
                }
            }
        }
        true
    }

    /// 확정된 글자(입력기)를 편집칸에 넣는다 — 한글의 **유일한** 입구다.
    fn session_edit_typed(&mut self, text: &str) -> bool {
        let Some(edit) = self.session_edit.as_mut() else {
            return false;
        };
        edit.insert_str(text);
        true
    }

    /// 편집 중인 이름과 커서(오라클용 — 창 없는 판에서 읽는다).
    #[cfg(test)]
    pub(crate) fn session_edit_for_test(&self) -> Option<(String, usize)> {
        self.session_edit.as_ref().map(|e| (e.text(), e.cursor()))
    }

    /// 판 안 위젯 `i` 에 마우스가 올라와 있나 — hover 강조용.
    /// 판 안 `i` 번째 위젯에 마우스가 올라와 있나 — 크롬과 **같은 규칙**이다
    /// ([`chrome_hovered`](Self::chrome_hovered) 의 §왜 `config.mouse` 를 보나).
    /// `panel_click` 도 첫 줄에서 같은 설정을 보고 돌아간다.
    fn panel_hovered(&self, i: usize) -> bool {
        Self::hover_shown(self.config.mouse, self.mouse_over(&self.panel_click_states, i))
    }

    /// 판 안을 클릭했다 — core 가 커서를 옮기고, **실행이 필요하면 평소 `Enter` 경로**를
    /// 그대로 탄다(클릭에만 있는 지름길을 만들지 않는다 — 확인 화면을 건너뛰는 갈래가
    /// 생기는 것을 막는다).
    pub fn panel_click(&mut self, target: base::PanelTarget) -> bool {
        use base::screens::PanelEffect;
        if !self.config.mouse {
            return false;
        }
        match self.screens.panel_click(target) {
            PanelEffect::Moved => {}
            PanelEffect::Enter => {
                self.handle_key(Key::Enter, Mods::NONE);
            }
            // 화살표도 **키 경로 그대로** — 값 고르기의 규칙(감기·범위)이 저쪽에 있다.
            PanelEffect::Dir(forward) => {
                self.handle_key(if forward { Key::Right } else { Key::Left }, Mods::NONE);
            }
            // 낱말을 직접 찍었다 — 자리는 뷰가 알고 **값은 core 가 고른다**.
            PanelEffect::Pick { row, index } => {
                let pick = Self::settings_row(self.screens.top(), row, &self.config)
                    .and_then(|row| {
                        base::config::setting_pick_at(row, &self.setting_values(), index)
                    });
                self.apply_setting_pick(pick);
            }
            // 판 안 단추 — 팔레트·키가 이미 가는 그 길이다(지름길이 아니다).
            PanelEffect::Act(action) => {
                self.apply_action(action);
            }
        }
        // 탭을 눌러 옮겼으면 커서도 그 탭에 맞춘다(키 경로가 `press` 뒤에 하는 그 일).
        self.settle_info_tabs();
        true
    }

    /// 창 버튼 `i` 번째의 마우스 상태(크롬·판 풀과 또 별개 — 위 필드 문서 참조).
    fn titlebar_mouse_state(&self, i: usize) -> MouseStateHandle {
        let mut states = self.titlebar_click_states.borrow_mut();
        while states.len() <= i {
            states.push(Default::default());
        }
        states[i].clone()
    }

    /// 머리줄 — **OS 타이틀바를 대신하는 줄**이다(`pytmux-1` · 자리 확정 2026-08-04).
    ///
    /// 세 가지를 겸한다: 끌어서 창 옮기기(상류가 한다 — `titlebar` 모듈 머리말) · 제목
    /// 표시(`pytmux-gui · <엔드포인트>` — 종전 머리줄의 그 글자 그대로) · 창 버튼.
    ///
    /// ⛔ **`config.mouse` 를 안 본다.** `set mouse off` 는 pytmux 안의 마우스를 끄는
    /// 설정이지 창 장식의 마우스가 아니다 — OS 타이틀바가 그 설정을 볼 리 없듯이, 그
    /// 자리를 물려받은 이 줄도 안 본다. ⚠ 그러지 않으면 `set mouse off` 한 사람이
    /// **창을 닫지 못한다**(그 설정을 되돌릴 화면조차 마우스로 못 연다).
    fn render_titlebar(&self) -> Box<dyn Element> {
        let title = self.text(
            format!("pytmux-gui · {}", self.link.socket()),
            12.,
            palette::DIM,
        );
        let buttons = titlebar::BUTTONS
            .iter()
            .enumerate()
            .map(|(i, button)| {
                let hovered = self.mouse_over(&self.titlebar_click_states, i);
                let fg = if hovered { palette::FG } else { palette::DIM };
                let slot = titlebar::slot(
                    self.config.font_scale,
                    self.text(button.glyph(), 12., fg),
                    hovered.then(|| button.hover_bg()),
                );
                let button = *button;
                Hoverable::new(self.titlebar_mouse_state(i), |_| slot)
                    // ★ `on_click`(뗄 때)이라야 한다. 누름은 **상류의 창 끌기와 같은
                    //   이벤트**라, 여기서 Hoverable 이 그것을 "먹었다"고 표시해 주는
                    //   덕에 버튼 자리가 드래그 영역에서 빠진다(제보의 ⚠ 그대로).
                    //   `on_mouse_down` 으로 바꾸면 누르는 즉시 창이 닫힌다.
                    .on_click(move |evt, _, _| {
                        evt.dispatch_typed_action(ViewAction::WindowButton(button));
                    })
                    .finish()
            })
            .collect();
        titlebar::row(self.config.font_scale, title, buttons)
    }

    /// 창 버튼 하나 — **뜻만 정한다.** 창을 실제로 건드리는 줄은 `handle_action` 의
    /// 꼬리에 있다(그 자리에만 `ViewContext` 가 있다).
    ///
    /// 갈래를 여기 두는 이유: 창 없는 판에서 잴 수 있어야 한다. 세 버튼이 같은 일을
    /// 하거나 한 버튼이 아무 일도 안 하는 회귀는 **화면으로는 안 보인다** — 눌러 봐야
    /// 알고, 눌러 보는 것은 라이브뿐이다.
    ///
    /// 닫기가 [`quit_requested`](Self::quit_requested) 로 접히는 이유: 상류
    /// `close_window` 는 이 백엔드에서 창을 안 닫는다(2026-07-30 실측 — `handle_action`
    /// 꼬리의 주석). 그 뒤처리가 이미 한 곳에 있으므로 갈래를 둘로 늘리지 않는다.
    pub(crate) fn press_window_button(&mut self, button: titlebar::Button) -> bool {
        match button {
            titlebar::Button::Close => self.quit_requested = true,
            other => self.window_op = Some(other),
        }
        // 다시 그릴 것은 없다 — 창이 바뀌면 창이 알린다(리사이즈·최소화 모두).
        false
    }

    /// 이번 프레임에 창에게 시킬 일(오라클용 — 창 없는 판에서 읽는다).
    #[cfg(test)]
    pub(crate) fn window_op_for_test(&self) -> Option<titlebar::Button> {
        self.window_op
    }

    /// 크롬 한 자리를 클릭 대상으로 감싼다 — 자리는 레이아웃(Hoverable)이 재고,
    /// 그 자리가 무슨 일이 되는지는 core 가 정한다(`chrome::click` · TUI 와 한 벌).
    fn clickable_chrome(
        &self,
        i: usize,
        target: base::chrome::ClickTarget,
        child: Box<dyn Element>,
    ) -> Box<dyn Element> {
        Hoverable::new(self.chrome_mouse_state(i), |_| child)
            .on_click(move |evt, _, _| {
                evt.dispatch_typed_action(ViewAction::ChromeClick(target));
            })
            .finish()
    }

    fn render_tabs(&self) -> Box<dyn Element> {
        let tabs = &self.state.tabs().tabs;
        // `tab-bar auto` — 탭이 하나뿐이면 줄을 아낀다(파이썬과 같다).
        //
        // ★ 종전에는 여기에 **모드 배지가 있으면 줄을 남긴다**는 조건이 더 있었다.
        //   §10-21ⓖ 로 그 배지가 하단 상태줄로 내려가면서 조건도 같이 사라졌다 —
        //   그래서 "탭 하나 + esc 모드"에서는 이제 탭바가 없다. 제보가 요구한 결과이고,
        //   모드는 하단에서 **더 잘 보인다**(반전 칩이다).
        if !self.config.tab_bar_always && tabs.len() <= 1 {
            return self.text("", 12., palette::DIM);
        }
        if tabs.is_empty() {
            return self.ui_text(t("(탭 없음)"), 12., palette::DIM);
        }
        let mut row = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_spacing(4.);
        // ★ 모드 배지는 **여기 없다**(§10-21ⓖ) — 하단 상태줄로 내려갔다(정본이 시스템
        //   배지를 두는 자리, 감시류가 2026-07-30 에 같은 이유로 먼저 내려간 그 자리다).
        // 세션 전역 표식(줌·동기화·자동재개…)은 탭바 **앞**에 붙는다 — 탭 뒤에 붙이면
        // 탭이 많을 때 화면 밖으로 밀려 나가는데, 그중 `[동기화]` 는 **모르고 치면 모든
        // 패널에 같은 명령이 도는** 상태라 안 보이면 안 된다(패리티 G6). 감시류
        // ([벨감시]·[활동감시])는 여기가 아니라 하단 상태줄이다(파이썬 정본의 시스템
        // 배지 자리 — 사용자 요청 2026-07-30).
        // ★ **시스템 표식은 여기 없다**(pytmux-183) — 하단 상태줄로 내려갔다.
        //
        //   종전에는 `[줌]`·`[동기화]`·`[자동재개]`·`[프롬프트클리어]` 가 탭바 **앞**에
        //   붙었고, 근거는 *"모르고 두면 입력이 달라지는 상태라 눈앞에 있어야 한다"*
        //   였다. 목표는 지금도 옳지만 **정본은 그 목표를 좌하단에서 이룬다**
        //   (`clientwidgets.py` 의 「시스템 배지 영역(SYNC/AR 직후)」 = 좌하단 클러스터).
        //   하단 상태줄도 **늘 그려지므로** 「항상 보인다」는 그대로다.
        //
        //   ⚠ 감시류(`monitor_badges`)가 2026-07-30 에 **같은 이유로 먼저** 내려갔다 —
        //   그때 남은 이 넷만 위에 있어서 정본에서 한 무리인 것이 GUI 에서 두 자리로
        //   갈려 있었다. 이제 한 자리다.
        // 크롬 포커스가 이 줄에 있으면 **고른 자리 하나만** 테두리로 강조한다.
        let chrome_tabs = self.chrome_tabs();
        let badges = self.state.badges();
        let ctx = self.chrome_ctx(&chrome_tabs, &badges);
        let spot = self.chrome.spot(&ctx);
        // 라벨(번호 포함)은 proto 가 만든다 — 번호는 **목록 전체**가 정한다(시각 순서).
        let labels = self.state.tabs().labels(&self.config.remote_title);
        // ★ **고정(핀) 탭은 뒤로 모인다**(pytmux-62 · 정본 `_visual_tab_order`).
        //
        //   정본은 탭바를 `[비고정…] [+] ‖ [고정…]` 으로 그린다 — 고정한 탭이 제자리에
        //   남아 있으면 «고정했다»는 사실이 **글리프 하나(`*`)뿐**이고, 그 하나는 이름
        //   글자들 사이에 묻힌다(제보 2026-08-24: *"gui에서는 핀이 반영되지 않는다"* —
        //   `*` 는 오고 있었다). 자리와 구분자가 그 사실을 말하게 한다.
        //
        //   ⚠ 번호는 이미 시각 순서다(`Tabs::labels` → `visual_numbers`) — 그리는 순서만
        //     그 번호를 안 따르고 있었다. 곧 «보이는 순서 = 번호» 가 깨져 있었다.
        let order: Vec<usize> = (0..tabs.len())
            .filter(|&i| !tabs[i].pinned)
            .chain((0..tabs.len()).filter(|&i| tabs[i].pinned))
            .collect();
        let pinned_from = tabs.iter().filter(|t| !t.pinned).count();
        for (slot, &i) in order.iter().enumerate() {
            let tab = &tabs[i];
            // 고정 구역이 시작하는 자리에 `+` 와 구분자를 끼운다 — 정본의 차례다.
            if slot == pinned_from {
                row = row.with_child(self.plus_button(tabs.len(), spot));
                row = row.with_child(self.pin_separator());
            }
            // 색은 [`tab_label_color`](Self::tab_label_color) 한 자리에서(정본 사슬 순서).
            let fg = Self::tab_label_color(tab);
            // ★ **굵기도 정본을 따른다**(pytmux-376 의 남은 반쪽). 그 함수의 ⚠ 주석은
            //   *"크롬 글자에는 굵기 갈래가 아예 없다"* 였는데, `ui_text_weighted` 가
            //   생기면서 그 전제가 사라졌다 — 정본 `done_st` 는 `bold=True` 다.
            let bold = !tab.active && tab.claude_done;
            let focused = spot == Some(base::TabSpot::Tab(i));
            // 드래그 중 드롭 대상은 포커스와 같은 강조 — "놓으면 여기로 간다".
            let drop_here = self.tab_drag_over == Some(i) && self.tab_drag != Some(i);
            let mut inner = Flex::row()
                .with_main_axis_size(MainAxisSize::Min)
                .with_cross_axis_alignment(CrossAxisAlignment::Center)
                .with_spacing(6.)
                .with_child(self.ui_text_weighted(labels[i].clone(), 13., fg, bold));
            // 닫기 × 는 **활성 탭 안**에 산다 — 뜻은 종전의 `[x]`(활성 탭 닫기, 확인
            // 화면을 지난다)와 같고 자리만 탭 안으로 들어왔다. 자리 색인도 종전
            // 그대로(탭들+1)라 상태줄 색인이 안 밀린다.
            if tab.active {
                inner = inner.with_child(self.close_button(tabs.len(), spot));
            }
            let mut boxed = Container::new(inner.finish())
                .with_horizontal_padding(10.)
                .with_vertical_padding(4.)
                .with_corner_radius(theme::TAB_RADIUS);
            // 배경은 활성 > hover. 포커스·드롭 대상은 배경이 아니라 **테두리**다 —
            // "지금 조작 대상"과 "지금 보는 탭"이 같은 탭에 겹쳐도 둘 다 보인다.
            if tab.active {
                boxed = boxed.with_background_color(theme::ACTIVE);
            } else if self.chrome_hovered(i) {
                boxed = boxed.with_background_color(theme::HOVER);
            }
            if focused || drop_here {
                boxed = boxed.with_border(Border::all(1.5).with_border_color(theme::FOCUS));
            }
            // 누름 = 드래그 시작(G9w — 전환은 뗄 때. 파이썬·TUI 와 같은 순서라
            // 제자리에서 놓으면 클릭=전환으로 접힌다).
            row = row.with_child(self.draggable_tab(i, boxed.finish()));
        }
        // ★ `+` 는 **항상** 그린다. 포커스가 왔을 때만 나타나면 그 자리가 있는지
        // 모르는 사람은 영영 못 찾는다(파이썬 탭바도 `[+]` 를 늘 달고 있다).
        // ⚠ 고정 탭이 있으면 위 루프가 **고정 구역 앞에서** 이미 그렸다(정본의 차례).
        if pinned_from == tabs.len() {
            row = row.with_child(self.plus_button(tabs.len(), spot));
        }
        // 띠 전체 — 캔버스보다 가라앉은 표면 위에 탭이 떠 있다(Warp 의 탭 띠).
        Container::new(row.finish())
            .with_background_color(theme::SURFACE)
            .with_uniform_padding(4.)
            .with_corner_radius(theme::TAB_RADIUS)
            .finish()
    }

    /// 탭 라벨의 **글자색** — 정본 `TabBar.render_line` 의 갈래 순서 그대로다.
    ///
    /// # 왜 뷰가 아니라 함수인가
    ///
    /// 헤드리스 프레임에는 글꼴이 없어 글자 색이 안 남는다(`PaintedText` 는 글과 자리만
    /// 쥔다) — 그리는 자리에 `if` 로 두면 **아무도 못 재는 판정**이 된다. 빼 두면
    /// `frame_segments` 처럼 오라클이 직접 부른다.
    ///
    /// # 정본과의 대조 (`clientwidgets.py::TabBar.render_line`)
    ///
    /// | 정본 갈래 | 정본 스타일 | 여기 |
    /// |---|---|---|
    /// | `active` | 파랑 배경(원격은 분홍 배경) | 배경 [`theme::ACTIVE`](crate::theme::ACTIVE) — 글자는 안 바꾼다 |
    /// | `claude_done` | `done_st` = 주황 글자 + bold | [`CLAUDE_DONE_AMBER`] |
    /// | `remote` | 분홍 글자 | [`REMOTE_PINK`] |
    /// | 그 밖 | 기본 전경 | `palette::FG` |
    ///
    /// ⛔ **`claude_done` 이 `remote` 보다 앞이다** — 정본이 그 순서다. 뒤집으면 원격
    /// 탭에서 Claude 가 끝난 것이 조용해진다.
    ///
    /// ★ **활성 탭은 안 물들인다.** 정본도 `active` 갈래가 `claude_done` 보다 **앞**이라
    /// 활성 탭은 배경이 말한다 — 그리고 활성 탭은 지금 보고 있는 탭이라 "바뀌었다"를
    /// 색으로 알릴 까닭이 애초에 없다.
    ///
    /// ⛔ **`bell`·`activity` 는 여기서 색을 안 바꾼다 — 정본이 안 바꾼다**(pytmux-376).
    /// 그 둘을 색으로 가르는 것은 정본에서 **하단 상태줄의 창 목록**이지 탭바가 아니고,
    /// 탭바에서 두 클라가 지금 말하는 것은 글리프(`!`·`#`)뿐이라 이미 같다. 여기에만
    /// 색을 더하면 고치는 것이 아니라 **새 갈림을 만드는** 것이다(pytmux-185 ④ — 갈림은
    /// 선언해야 하고, 이 부류는 허용된 셋 어디에도 안 든다). 탭바에서도 벨/활동을 색으로
    /// 말하게 할지는 **두 클라를 함께** 바꾸는 결정이라 사람 몫이다.
    ///
    /// ★ **정본의 bold 도 이제 함께 온다**(2026-08-24). 종전 이 자리에는 *"크롬 글자는
    /// 굵기 갈래가 아예 없다"* 고 적혀 있었는데, [`ui_text_weighted`](Self::ui_text_weighted)
    /// 가 생기면서 그 전제가 사라졌다 — 굵기는 **부르는 자리**가 같은 조건으로 정한다
    /// (`!active && claude_done`). 이 함수가 색만 쥐는 것은 그대로다: 색은 오라클이 직접
    /// 부를 수 있는 순수 판정이고, 굵기는 그리는 자리의 인자다.
    fn tab_label_color(tab: &proto::tabs::Tab) -> ColorU {
        if tab.active {
            // 원격은 분홍 — 파이썬 클라·TUI 와 같은 관습이다(`clientutil::REMOTE_PINK`).
            if tab.remote { REMOTE_PINK } else { palette::FG }
        } else if tab.claude_done {
            CLAUDE_DONE_AMBER
        } else if tab.remote {
            REMOTE_PINK
        } else {
            palette::FG
        }
    }


    /// 고정(핀) 구역의 왼쪽 구분자 `‖`(정본 `TabBar.PIN_SEP` · pytmux-62).
    ///
    /// 정본은 이 글자를 `primary` 색 굵게로 그린다. 우리도 같은 뜻을 쓰되 값은 이 테마의
    /// 강조색이다 — 구분자는 **경계**를 말하는 것이라 글자보다 흐리면 경계가 안 보이고,
    /// 탭 이름보다 튀면 이름을 읽는 데 방해가 된다.
    fn pin_separator(&self) -> Box<dyn Element> {
        Container::new(self.ui_text_weighted("‖", 13., theme::INVERT_BG, true))
            .with_horizontal_padding(6.)
            .finish()
    }

    /// 활성 탭 안의 닫기 ×. 종전 `[x]` 와 같은 자리 색인(탭들+1)·같은 액션
    /// (`Spot(Close)` → 확인 화면)이다.
    fn close_button(
        &self,
        ntabs: usize,
        spot: Option<base::TabSpot>,
    ) -> Box<dyn Element> {
        let slot = ntabs + 1;
        let focused = spot == Some(base::TabSpot::Close);
        let hovered = self.chrome_hovered(slot);
        let fg = if hovered || focused { palette::FG } else { palette::DIM };
        let mut boxed = Container::new(self.ui_text("×", 12., fg))
            .with_horizontal_padding(4.)
            .with_corner_radius(theme::PILL_RADIUS);
        if hovered {
            boxed = boxed.with_background_color(theme::HOVER);
        }
        if focused {
            boxed = boxed.with_border(Border::all(1.5).with_border_color(theme::FOCUS));
        }
        self.clickable_chrome(
            slot,
            base::chrome::ClickTarget::Spot(base::TabSpot::Close),
            boxed.finish(),
        )
    }

    /// 새 탭 + 버튼. 종전 `[+]` 와 같은 자리 색인(탭들+0)·같은 액션이다.
    fn plus_button(
        &self,
        ntabs: usize,
        spot: Option<base::TabSpot>,
    ) -> Box<dyn Element> {
        let focused = spot == Some(base::TabSpot::New);
        let hovered = self.chrome_hovered(ntabs);
        let fg = if hovered || focused { palette::FG } else { palette::DIM };
        let mut boxed = Container::new(self.ui_text("+", 14., fg))
            .with_horizontal_padding(7.)
            .with_vertical_padding(1.)
            .with_corner_radius(theme::PILL_RADIUS);
        if hovered {
            boxed = boxed.with_background_color(theme::HOVER);
        }
        if focused {
            boxed = boxed.with_border(Border::all(1.5).with_border_color(theme::FOCUS));
        }
        self.clickable_chrome(
            ntabs,
            base::chrome::ClickTarget::Spot(base::TabSpot::New),
            boxed.finish(),
        )
    }

    /// 하단 배지 줄 — `e_down` 이 내려오는 자리.
    ///
    /// 늘 그린다. 목록은 proto 가 만들고([`SessionState::badges`]) 어느 것이 골라졌는지는
    /// core 가 안다 — 이 함수는 낱말을 잇기만 한다(TUI 와 같은 규칙).
    /// 이 배지가 지금 **켜져 있나**(§10-21ⓧ).
    ///
    /// # 왜 일반 개념인가
    ///
    /// 제보의 마지막 문단이 그 걱정을 적어 뒀다: *"같은 판단이 필요한 배지가 더 있을 수
    /// 있다 … 배지 그리기 자리에 '켜짐'을 **일반 개념으로** 넣는 편이 낫다 — 하나씩 특수
    /// 처리하면 다음 배지에서 또 빠진다."* 그래서 배지마다 갈래를 두되 **한 함수 안**에
    /// 모은다 — 새 배지가 늘면 `match` 가 여기서 컴파일을 막는다.
    ///
    /// 켜짐이 **없는** 배지도 있다: `알림`·`서버`·`시계`·`달력` 은 누르면 화면이 열리는
    /// **버튼**이지 토글이 아니다(열려 있음을 배지가 말할 이유가 없다 — 화면이 이미
    /// 눈앞에 있다).
    #[cfg(test)]
    pub(crate) fn badge_is_on_for_test(&self, badge: base::Badge) -> bool {
        self.badge_is_on(badge)
    }

    fn badge_is_on(&self, badge: base::Badge) -> bool {
        match badge {
            base::Badge::Notices
            | base::Badge::Host
            | base::Badge::Clock
            | base::Badge::Calendar => false,
        }
    }

    /// 왼쪽 상태 형식(`status-left`)을 **런으로 펴서** 그린다(pytmux-3).
    ///
    /// 종전에는 통짜 문자열 `Text` 하나였다. 그래서는 `#S` 가 어디부터 어디까지인지 몰라
    /// 누를 자리를 만들 수 없다 — 정본이 같은 이유로 왼쪽을 `_expand_parts` 에 태웠다.
    ///
    /// ⚠ **바깥 `left` 의 여백(6px)이 조각 사이로 새면 안 된다.** 그래서 런들을 여백
    /// 0 인 자기 줄에 담아 **한 자식**으로 넘긴다 — 그림은 종전과 같은 글자·같은 간격이다.
    /// 돌려주는 `bool` 은 **이 줄에 `#S` 가 있었나**다 — 양쪽에 다 뒀으면 왼쪽이
    /// 편집 자리다(파이썬 `_render_main` 의 "먼저 그린 쪽 우선"과 같은 규칙).
    fn render_status_left(
        &self,
        sctx: &status::StatusCtx,
        fg: ColorU,
        ids: &mut usize,
    ) -> (Box<dyn Element>, bool) {
        let runs = status::expand_parts(&self.config.status_left, sctx);
        // 조각이 없으면(형식이 빈 문자열) 빈 `Text` 하나 — 종전과 같은 그림이고,
        // 자리표(`FOOTER_PROBE`)의 주인이 사라지지 않는다.
        if runs.is_empty() {
            return (self.status_text("", fg, true), false);
        }
        let mut row = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_spacing(0.);
        let mut has_session = false;
        for (i, (kind, text)) in runs.into_iter().enumerate() {
            let probe = i == 0;
            row = row.with_child(match kind {
                status::StatusRun::Session => {
                    has_session = true;
                    self.render_session_run(&text, fg, ids, probe)
                }
                _ => self.status_text(&text, fg, probe),
            });
        }
        (row.finish(), has_session)
    }

    /// 상태줄 글자 한 조각. `probe` 면 이 조각이 [`FOOTER_PROBE`](Self::FOOTER_PROBE)
    /// 자리표를 진다 — 한 프레임에 **하나만** 참이라야 한다.
    fn status_text(&self, text: &str, fg: ColorU, probe: bool) -> Box<dyn Element> {
        let mut t = Text::new_inline(text.to_owned(), self.ui_font, self.scaled(12.)).with_color(fg);
        if probe {
            t = t.with_saved_char_position(0, Self::FOOTER_PROBE.to_owned());
        }
        t.finish()
    }

    /// 세션 이름(`#S`) 한 런 — 평소엔 **누를 자리**, 편집 중엔 **입력칸**(pytmux-3).
    ///
    /// 판을 안 띄우므로 "지금 여기를 고치는 중"을 보여 줄 곳이 이 자리뿐이다. 그래서
    /// 편집 중에는 반전 칩(모드 배지와 같은 문법)이 되고 커서 자리가 한 번 더 뒤집힌다.
    fn render_session_run(
        &self,
        name: &str,
        fg: ColorU,
        ids: &mut usize,
        probe: bool,
    ) -> Box<dyn Element> {
        let Some(edit) = self.session_edit.as_ref() else {
            let i = Self::take_id(ids);
            // 눌리는 자리는 hover 로 그 사실을 보인다(N4 — 다른 클릭존과 같은 규율).
            let mut boxed = Container::new(self.status_text(name, fg, probe))
                .with_horizontal_padding(2.)
                .with_corner_radius(theme::PILL_RADIUS);
            if self.status_hovered(i) {
                boxed = boxed.with_background_color(theme::HOVER);
            }
            return self.clickable_status(
                i,
                base::chrome::ClickTarget::SessionName,
                boxed.finish(),
            );
        };
        let buf: Vec<char> = edit.text().chars().collect();
        let cur = edit.cursor();
        let mut row = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_spacing(0.);
        // ⚠ **글자마다 자기 자리를 준다** — 그래야 누른 자리로 커서가 간다(파이썬은
        //   절대 x 를 셀 폭으로 되짚는데, 여기는 셀 격자가 없다). 덤으로 한글에서
        //   폭 계산이 어긋날 여지가 아예 없어진다.
        for (i, ch) in buf.iter().enumerate() {
            let at_cursor = i == cur;
            let cell = self.session_edit_cell(&ch.to_string(), at_cursor, probe && i == 0);
            let id = Self::take_id(ids);
            row = row.with_child(self.clickable_status(
                id,
                base::chrome::ClickTarget::SessionCursor(i),
                cell,
            ));
        }
        // 커서가 끝에 있으면 한 칸을 덧대 커서를 보이게 한다(파이썬 `_session_segs` 와
        // 같다) — 안 그러면 이름 끝에서 커서가 사라져 편집 중인지 알 수 없다.
        if cur >= buf.len() {
            let cell = self.session_edit_cell(" ", true, probe && buf.is_empty());
            let id = Self::take_id(ids);
            row = row.with_child(self.clickable_status(
                id,
                base::chrome::ClickTarget::SessionCursor(buf.len()),
                cell,
            ));
        }
        Container::new(row.finish())
            .with_horizontal_padding(2.)
            .with_background_color(theme::INVERT_BG)
            .with_corner_radius(theme::PILL_RADIUS)
            .finish()
    }

    /// 입력칸 한 글자. 커서 자리는 **한 번 더 뒤집는다**(반전 칩 위의 반전).
    fn session_edit_cell(&self, ch: &str, at_cursor: bool, probe: bool) -> Box<dyn Element> {
        let fg = if at_cursor { theme::INVERT_BG } else { theme::INVERT_FG };
        let text = self.status_text(ch, fg, probe);
        if at_cursor {
            Container::new(text)
                .with_background_color(theme::INVERT_FG)
                .finish()
        } else {
            text
        }
    }

    /// 자리 번호 하나를 뗀다(`PanelIds` 와 같은 규칙 — 그리는 차례가 곧 번호다).
    fn take_id(ids: &mut usize) -> usize {
        let id = *ids;
        *ids += 1;
        id
    }

    /// 상태줄 자리 `i` 에 마우스가 올라와 있나.
    fn status_hovered(&self, i: usize) -> bool {
        Self::hover_shown(self.config.mouse, self.mouse_over(&self.status_click_states, i))
    }

    /// 상태줄 한 자리를 클릭 대상으로 감싼다 — 크롬과 **같은 구조**이고 풀만 다르다
    /// (`status_click_states` 필드 문서의 사정).
    fn clickable_status(
        &self,
        i: usize,
        target: base::chrome::ClickTarget,
        child: Box<dyn Element>,
    ) -> Box<dyn Element> {
        let state = {
            let mut states = self.status_click_states.borrow_mut();
            while states.len() <= i {
                states.push(Default::default());
            }
            states[i].clone()
        };
        Hoverable::new(state, |_| child)
            .on_click(move |evt, _, _| {
                evt.dispatch_typed_action(ViewAction::ChromeClick(target));
            })
            .finish()
    }

    /// 시스템 표식 칩 하나(pytmux-183) — 줌·동기화·자동재개·프롬프트클리어.
    ///
    /// **자동재개만 눌린다.** 정본이 클릭존을 둔 것이 `AR` 하나이기 때문이고
    /// (`clientwidgets.py` 의 `_ar_zone`), 나머지에 우리 마음대로 붙이면 정본에 없는
    /// 조작 표면을 GUI 가 먼저 만드는 것이 된다. 그 판정은 [`base::chrome::SysBadge::action`]
    /// 이 든다 — 뷰가 낱말로 가르면 로케일이 바뀌는 순간 조용히 틀린다.
    ///
    /// 눌리는 것은 hover 로 그 사실을 보인다(N4 — 다른 클릭존과 같은 규율: 「못 하는
    /// 것을 눌리는 것처럼 그리지 않는다」의 짝이다).
    fn sys_badge_chip(&self, badge: base::chrome::SysBadge) -> Box<dyn Element> {
        let chip = self.chip(badge.label(), palette::BR_YELLOW);
        if badge.action().is_none() {
            return chip;
        }
        // 자리 색인은 크롬이 쓰는 자리들 **뒤**에서 잇는다 — 겹치면 hover 가 엉뚱한
        // 칩에 붙는다(`chrome_slot_end` 가 그 끝을 안다).
        let index = self.chrome_slot_end();
        let inner = if self.chrome_hovered(index) {
            Container::new(self.ui_text(badge.label(), 11., palette::BR_YELLOW))
                .with_horizontal_padding(8.)
                .with_vertical_padding(2.)
                .with_background_color(theme::HOVER)
                .with_border(Border::all(1.).with_border_color(theme::FOCUS))
                .with_corner_radius(theme::PILL_RADIUS)
                .finish()
        } else {
            chip
        };
        self.clickable_chrome(index, base::chrome::ClickTarget::SysBadge(badge), inner)
    }

    /// 상태줄 배지 칩 하나 — **왼쪽 무리든 오른쪽 무리든 같은 그림**(pytmux-367).
    ///
    /// # 왜 한 함수인가
    ///
    /// 알림 배지가 오른쪽 무리의 머리로 옮겨 가면서 이 칩을 짓는 자리가 둘이 됐다.
    /// 두 벌로 적으면 hover·켜짐·포커스 테두리 중 하나가 한쪽에서만 빠지고, 그 종류의
    /// 어긋남은 **누르기 전에는 안 보인다**.
    ///
    /// # 자리 색인은 «그리는 무리» 가 아니라 «목록에서의 자리» 다
    ///
    /// `badges` 안 위치로 색인을 짓는다 — 그래야 알림을 오른쪽에 그려도 그 색인이 안
    /// 움직이고, 아래 우측 구간·플러그인 배지의 색인과도 안 겹친다(`chrome_slot_end`
    /// 가 같은 산수를 쓴다).
    fn status_badge_chip(
        &self,
        badge: base::Badge,
        badges: &[base::Badge],
        picked: Option<base::Badge>,
        fg: ColorU,
    ) -> Box<dyn Element> {
        let mut text = badge.label().to_string();
        // 곁가지(개수·호스트 이름)는 뷰가 붙인다 — core 는 그 값을 모른다.
        match badge {
            base::Badge::Notices => {
                text.push_str(&format!(" {}", self.state.notices().len()))
            }
            base::Badge::Host => {
                if let Some(host) = self.state.active_remote_host() {
                    text.push_str(&format!(" {host}"));
                }
            }
            _ => {}
        }
        // 배지 클릭 = Enter 와 같은 액션(파이썬 상태줄 버튼과 같은 표면).
        // 상태 색인은 탭바 자리들 **뒤**에서 시작한다.
        let state_index = self.state.tabs().tabs.len()
            + 2
            + badges.iter().position(|b| *b == badge).unwrap_or(0);
        // ★ **켜짐**은 배지 모양이 말한다(§10-21ⓧ) — 반전 칩이다.
        //
        // 제보: *"눌러 스크롤바가 뜨면 버튼도 색이 바뀌는 등 토글 상태임을 알 수
        // 있어야 한다."* 상태는 이미 우리가 안다 — 안 하던 것은 그것을 **모양에
        // 싣는 일**뿐이었다.
        //
        // ⚠ 켜짐을 **일반 개념으로** 둔다(`Badge::is_on`): 하나씩 특수 처리하면
        //   다음 토글 배지에서 또 빠진다.
        let on = self.badge_is_on(badge);
        let (fg, bg) = if on {
            (theme::INVERT_FG, Some(theme::INVERT_BG))
        } else {
            (fg, None)
        };
        // 칩 버튼(N4): hover 배경, 키보드 포커스는 FOCUS 테두리(탭바와 같은 문법).
        let mut boxed = Container::new(self.ui_text(text, 11., fg))
            .with_horizontal_padding(8.)
            .with_vertical_padding(2.)
            .with_corner_radius(theme::PILL_RADIUS);
        if let Some(bg) = bg {
            boxed = boxed.with_background_color(bg);
        } else if self.chrome_hovered(state_index) {
            boxed = boxed.with_background_color(theme::HOVER);
        }
        // ⚠ FOCUS 테두리는 **다른 뜻**이다 — "키보드가 이 배지를 고르고 있다"이지
        //   켜짐이 아니다.
        if picked == Some(badge) {
            boxed = boxed.with_border(Border::all(1.5).with_border_color(theme::FOCUS));
        }
        self.clickable_chrome(
            state_index,
            base::chrome::ClickTarget::Badge(badge),
            boxed.finish(),
        )
    }

    fn render_status(&self) -> Box<dyn Element> {
        let tabs = self.chrome_tabs();
        let badges = self.state.badges();
        let ctx = self.chrome_ctx(&tabs, &badges);
        let picked = self.chrome.badge(&ctx);
        // 형식은 **설정이 정하고 펼치는 것은 proto 가 한다** — 뷰가 각자 펼치면 같은
        // 형식 문자열이 클라마다 다른 글자를 낸다.
        let sctx = self.state.status_ctx();
        let fg = status::color(&self.config.status_fg).map_or(palette::DIM, |c| to_gui_color(&c));
        let mut left = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_spacing(6.);
        // ★ **캔버스가 끝나는 자리의 자리표가 여기 있다**(§10-21ⓓ 로 옮겨 왔다).
        //
        // 종전 주인은 요약 구역의 머리줄이었는데, 그 구역을 화면에서 빼면서 자리표도
        // 같이 사라질 참이었다 — 그러면 `report_size` 의 `footer_px` 가 늘 0 이 되어
        // 캔버스가 **배지·메시지 두 줄만큼 넘치고**, 그 줄들이 창 밖으로 밀린다
        // (2026-07-30 에 실제로 겪은 증상이다).
        //
        // 상태줄은 **늘 그려지므로** 자리표의 주인으로 더 낫다. 종전 주인은 블록도
        // Claude 도 없으면 안 그려져서, 그때는 같은 넘침이 이미 조용히 있었다.
        //
        // ⛔ **쪼갤 때 프로브를 첫 조각에 남긴다**(pytmux-3). 왼쪽을 런으로 펴면서
        //    이 `Text` 가 여럿이 됐는데, 자리표가 사라지면 위의 넘침이 그대로 돌아온다.
        //    조각이 하나도 없는 경우(형식이 빈 문자열)에도 빈 `Text` 를 남기는 이유가
        //    그것이다 — 종전에도 그 경우엔 빈 `Text` 하나였다(같은 그림·같은 측정).
        let mut status_ids = 0usize;
        let (left_fmt, left_has_session) = self.render_status_left(&sctx, fg, &mut status_ids);
        left = left.with_child(left_fmt);
        // ★ `esc` 모드 표식은 **여기**다(§10-21ⓖ) — 탭바에서 내려왔다. 정본이 시스템
        //   배지를 두는 자리이고, 감시류([벨감시]·[활동감시])가 2026-07-30 에 같은
        //   이유로 먼저 내려온 곳이다. 반전 칩이라 종전(노란 글자)보다 눈에 띈다.
        if let Some(badge) = self.mode.mode().badge() {
            left = left.with_child(self.mode_chip(self.mode.mode(), badge));
        }
        for badge in &badges {
            // ★ **알림은 오른쪽 무리의 머리로 간다**(pytmux-367 · 정본 `_render_main`
            //   §10-8: host/시각/날짜 **앞**). 「오른쪽 끝」이 아니라 오른쪽 무리의
            //   머리라는 점이 핵심이다. 나머지 배지(`⇕`)는 정본에서도 왼쪽이라 그대로다.
            if *badge == base::Badge::Notices {
                continue;
            }
            left = left.with_child(self.status_badge_chip(*badge, &badges, picked, fg));
        }
        // ★ 시스템 표식(줌·동기화·자동재개·프롬프트클리어) — **정본의 좌하단 클러스터**
        //   다(pytmux-183). 감시류 **앞**에 두어 정본 순서(Z·SYNC·AR 다음이 REC 류)를
        //   지킨다. 자리를 옮긴 이유는 탭바 쪽 주석에 있다.
        for badge in self.state.flags().tab_badges() {
            left = left.with_child(self.sys_badge_chip(badge));
        }
        // 감시류 표식([활동감시]·[벨감시]) — 파이썬 정본이 시스템 배지(Z·SYNC·AR·REC)를
        // 두는 하단 상태줄 자리다(사용자 요청 2026-07-30: 탭바 → 여기로). 버튼이 아니라
        // **상태 표식**이라 클릭존 없이 칩으로만 그린다.
        for badge in self.state.flags().monitor_badges() {
            left = left.with_child(self.chip(badge, palette::BR_YELLOW));
        }
        // ★ 플러그인이 낸 표식(Tier B ③ · P6) — 바로 위 주석이 "파이썬 정본이 시스템
        // 배지(Z·SYNC·AR·**REC**)를 두는 자리"라고 적어 둔 그 자리다. REC 는 우리에게
        // 없었다(플러그인이 채우는 칸이라 `Badge` 열거형에 넣을 수 없었다).
        // 이제 서버가 자료로 준다 — 글자와 **의미 색 이름**만 받아 우리 테마로 푼다.
        //
        // ★ **누르는 자리가 생겼다**(pytmux-20). 종전 주석은 *"누르는 자리는 Tier C
        //   화면이 와야 생긴다 — 그래서 서버도 `do` 를 안 싣는다"* 였는데, 한도 판
        //   (`usage-panel`)이 그 화면을 내면서 조건이 섰다. 우리는 **무엇이 열리는지
        //   모른다** — 표식이 실어 온 이름을 그대로 `plugin_open` 으로 되돌려 보낸다.
        //   `do` 가 없는 표식(REC·모델·경고)은 종전대로 그리기만 한다.
        for (i, badge) in self.state.plugin_badges().iter().enumerate() {
            // 색 이름은 런과 **같은 표**로 푼다(`proto::session::theme`) — 배지라고
            // 다른 표를 두면 같은 이름이 두 자리에서 다른 색이 된다.
            // 칩은 배경이 고정(HOVER)이라 글자색 한 칸만 쓴다: 바탕색 이름이 왔으면
            // 그것이 이 배지의 **주된 색**이므로 글자에 쓴다(정본은 ` REC ` 를 빨간
            // 바탕에 흰 글자로 그린다 — 칩에서는 빨간 글자가 같은 뜻이다).
            let name = badge.theme.b.as_deref().or(badge.theme.f.as_deref());
            let color = name
                .and_then(proto::session::theme::color)
                .map(|c| to_gui_color(&c))
                .unwrap_or(palette::FG);
            let chip = self.chip(badge.say().trim(), color);
            left = left.with_child(match badge.opens() {
                // 눌리는 자리는 hover 로 그 사실을 보인다(N4 — 다른 클릭존과 같은 규율).
                // 색인은 위 배지 자리들 **뒤**에서 이어진다 — 한 프레임의 렌더 순서가
                // 곧 색인이라 겹치면 hover 가 엉뚱한 칩에 붙는다.
                Some(_) => self.clickable_chrome(
                    // ⛔ 우측 구간과 **겹치던 자리**다(pytmux-367 을 고치며 드러났다):
                    //    아래 `part_index` 도 `tabs+2+badges.len()` 에서 시작해, 첫
                    //    플러그인 배지와 첫 클릭 구간이 같은 `MouseState` 를 나눠 썼다 —
                    //    한쪽에 마우스를 올리면 엉뚱한 쪽이 밝아진다. 구간 쪽을 플러그인
                    //    배지 **뒤**로 밀어 갈랐다(`chrome_slot_end` 도 같은 산수다).
                    self.state.tabs().tabs.len() + 2 + badges.len() + i,
                    base::chrome::ClickTarget::PluginBadge(i),
                    chip,
                ),
                None => chip,
            });
        }
        // ★ Claude 모델·한도 배지는 **여기 없다** — 위 `plugin_badges` 줄이 그린다
        //   (M4 P6 후반). 종전에는 우리가 날 필드(`claude_model`·`tok5h_pct`)로 자기
        //   문자열을 조립했는데(`claude_badge`), 그러면 정본과 **두 벌**이 되어 같은
        //   상태를 서로 다르게 보였다 — 실제로 갈려 있었다(정본은 카운트다운·경고까지
        //   그렸고 우리는 안 그렸다). 이제 규칙은 `plugins/claude-code/statusbadges.py`
        //   한 벌이고 우리는 받은 것을 칩으로 그린다.
        // ★ 입력기 배지는 여기 없다 — **활성 패널 우상단**으로 옮겼다(정본과 같은 자리,
        // 3차 대조 ⓕ). 이 배지는 "다음 글자가 무엇이 될지"를 말하는데 그때 눈은 커서에
        // 있지 상태줄에 있지 않다. 두 곳에 그리면 같은 것을 두 번 말하는 크롬이 된다.
        // 우측은 구간으로 편다(G9w — 파이썬 `_expand_parts`). 시각/날짜/호스트 구간이
        // 클릭 존이 된다 — 뜻은 이미 있는 배지 액션 그대로다(TUI 와 같은 표). 자리
        // 색인은 배지들 뒤에서 잇는다(렌더 순서 = 색인).
        let parts = status::expand_parts(&self.config.status_right, &sctx);
        let mut part_index =
            self.state.tabs().tabs.len() + 2 + badges.len() + self.state.plugin_badges().len();
        // 구간 사이에 여백을 끼우지 않는다 — `%H:%M %Y-%m-%d` 의 붙은 모양 그대로.
        let mut right = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_spacing(0.);
        // ★ **알림 배지가 이 무리의 머리다**(pytmux-367). 정본 `_render_main` §10-8 이
        //   그 규칙을 주석으로 못 박아 두었다: *"알림 이력 배지는 우측 배지열의 **맨
        //   왼쪽**에 붙인다(host/시각/날짜 앞)"*. 곧 「오른쪽 끝」이 아니라 **오른쪽
        //   무리의 머리**이고, 그래서 `parts` 루프 **앞**에 그린다.
        //
        //   칩이 자기 여백(8px)을 들고 있어 `spacing(0)` 이어도 host 와 안 붙는다.
        if badges.contains(&base::Badge::Notices) {
            right = right.with_child(self.status_badge_chip(
                base::Badge::Notices,
                &badges,
                picked,
                fg,
            ));
        }
        for (kind, text) in parts {
            // `#S` 를 `status-right` 에 둔 설정에서도 같은 자리를 누를 수 있다. 양쪽에
            // 다 뒀으면 **왼쪽이 편집 자리**이고 여기는 글자로만 남는다(파이썬과 같다) —
            // 편집칸이 한 화면에 둘이면 어느 쪽에 치고 있는지 알 수 없다.
            if kind == status::StatusRun::Session {
                right = right.with_child(if left_has_session {
                    self.ui_text(text, 12., fg)
                } else {
                    self.render_session_run(&text, fg, &mut status_ids, false)
                });
                continue;
            }
            match status::run_badge(kind) {
                Some(badge) => {
                    // 클릭되는 구간은 hover 로 그 사실을 보인다(N4).
                    let mut boxed = Container::new(self.ui_text(text, 12., fg))
                        .with_horizontal_padding(2.)
                        .with_corner_radius(theme::PILL_RADIUS);
                    if self.chrome_hovered(part_index) {
                        boxed = boxed.with_background_color(theme::HOVER);
                    }
                    right = right.with_child(self.clickable_chrome(
                        part_index,
                        base::chrome::ClickTarget::Badge(badge),
                        boxed.finish(),
                    ));
                    part_index += 1;
                }
                None => right = right.with_child(self.ui_text(text, 12., fg)),
            }
        }
        // 좌·우 그룹을 양 끝으로(N4) — 시계·날짜가 오른쪽 끝에 붙는 관습 그대로.
        let bar = Flex::row()
            .with_main_axis_size(MainAxisSize::Max)
            .with_main_axis_alignment(warpui::elements::MainAxisAlignment::SpaceBetween)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_child(left.finish())
            .with_child(right.finish())
            .finish();
        // 띠 표면(N4): 사용자가 색을 정했으면 그 색, 아니면 크롬 표면(SURFACE) —
        // 탭바와 같은 띠로 읽히게 한다(종전 "안 정하면 투명"에서 의도적으로 바꿈).
        let bg = status::color(&self.config.status_bg)
            .map_or(theme::SURFACE, |c| to_gui_color(&c));
        Container::new(bar)
            .with_background_color(bg)
            .with_horizontal_padding(6.)
            .with_vertical_padding(3.)
            .with_corner_radius(theme::TAB_RADIUS)
            .finish()
    }

    /// 상태줄이 위인가(`status-position top`).
    fn status_on_top(&self) -> bool {
        self.config.status_position == "top"
    }

    /// 블록 부류 → 이 뷰의 색.
    ///
    /// TUI 는 팔레트 **이름**을 터미널에 넘기지만(사용자 테마가 실제 RGB 를 정한다) GUI 에는
    /// 물려받을 테마가 없어 값을 직접 든다. 그래서 두 뷰의 색이 똑같지는 않다 — 같아야
    /// 하는 것은 **어느 블록이 어느 부류인가**이고, 그건 [`Block::tone`] 한 곳이 정한다.
    fn block_color(tone: Tone) -> ColorU {
        match tone {
            Tone::Ok => theme::OK,
            Tone::Failed => theme::ERROR,
            // 종료코드를 **모르는** 것은 성공이 아니다. 초록으로 칠하면 사용자가 끝났고
            // 잘됐다고 읽는다(TUI 와 같은 규칙).
            Tone::Unknown => theme::WARN,
            Tone::Running => palette::CYAN,
            Tone::Idle => palette::DIM,
            // 턴은 성패 축 밖이다 — 초록/빨강/노랑 어디에도 안 붙는 색이라야 목록을
            // 훑을 때 "이건 다른 부류"가 한눈에 온다.
            Tone::Turn => theme::TURN,
        }
    }

    /// 블록 한 줄이 실제로 적을 것 — (명령, cwd). 표식은 폭이 고정이라 여기 없다.
    ///
    /// **폭 예산을 지킨다.** TUI 는 `TuiText::truncate()` 가 터미널 폭에서 잘라 주지만
    /// GUI 의 `Text` 는 그냥 길어져 **창 밖으로 흘러나간다**(실측 2026-07-28).
    ///
    /// 순서가 뜻을 갖는다: 명령이 먼저 먹고 cwd 가 남은 것을 먹는다. **자리가 모자라면
    /// cwd 를 통째로 뺀다** — 명령이 주인공이고 경로는 곁다리라, 둘을 똑같이 잘라 반쪽씩
    /// 남기면 어느 쪽도 못 읽는 줄이 된다.
    ///
    /// 뷰 밖에서 시험할 수 있게 순수 함수로 둔다(사용자 결정: "로직은 밀고 뷰는 얇게").
    fn block_parts(block: &Block, cols: usize) -> (String, Option<String>) {
        /// 표식 자리(`···` 3칸) + 뒤 공백.
        const BADGE: usize = 4;
        /// 이보다 좁으면 cwd 는 안 그린다 — 경로 앞 두어 글자만 남으면 없느니만 못하다.
        const CWD_MIN: usize = 10;

        let left = cols.saturating_sub(BADGE);
        let command = footer::elide(block.command_text(), left);
        let left = left.saturating_sub(footer::width(&command) + 1);
        let cwd = block
            .cwd
            .as_deref()
            .filter(|_| left >= CWD_MIN)
            .map(|cwd| footer::elide(cwd, left));
        (command, cwd)
    }

    /// 블록 한 줄: 표식 · 명령 · cwd.
    ///
    /// TUI 는 한 문자열로 만들어 한 색을 입히지만, GUI 는 엘리먼트가 따로 있으므로
    /// **cwd 를 흐리게** 뺀다 — 명령이 주인공이고 경로는 곁다리다. 무엇을 적을지는
    /// 그래도 공유 정본이 정한다(`badge`·`command_text`).
    fn render_block(&self, block: &Block, cols: usize) -> Box<dyn Element> {
        let color = Self::block_color(block.tone());
        let (command, cwd) = Self::block_parts(block, cols);
        let mut row = Flex::row().with_main_axis_size(MainAxisSize::Min).with_spacing(6.);
        row = row.with_child(self.text(block.badge(), 12., color));
        row = row.with_child(self.text(command, 13., color));
        if let Some(cwd) = cwd {
            row = row.with_child(self.text(cwd, 11., palette::DIM));
        }
        row.finish()
    }

    /// Claude 항목의 색.
    ///
    /// **모르는 것을 성공과 같이 칠하지 않는다** — 결과가 안 온 툴 호출은 진행 중이지
    /// 끝난 게 아니다(블록의 `??` 와 같은 규칙). 같은 이유로 **권한이 막은 것은 실패와
    /// 다른 색**이다: 빨강으로 뭉치면 "고쳐야 할 것"과 "안 시킨 것"이 한 덩어리가 되고,
    /// 그 둘은 사용자가 할 일이 정반대다.
    fn claude_color(item: &ClaudeItem) -> ColorU {
        match item.state() {
            Some(ToolState::Ok) => theme::OK,
            Some(ToolState::Failed) => theme::ERROR,
            Some(ToolState::Running) => theme::WARN,
            Some(ToolState::Denied) => theme::TURN,
            // 사람이 친 것과 Claude 가 말한 것 — 상태가 없다.
            None => match item.kind {
                claude::ItemKind::Prompt => palette::CYAN,
                _ => palette::FG,
            },
        }
    }

    /// Claude 항목 한 줄: 표식 · 이름 · 요약.
    ///
    /// 무엇을 적을지는 `Item::badge`/`Item::name` 이 정한다(TUI 와 같은 정본). 여기서
    /// 정하는 것은 색과 폭 예산뿐이다.
    fn render_claude(&self, item: &ClaudeItem, cols: usize) -> Box<dyn Element> {
        let color = Self::claude_color(item);
        let mut row = Flex::row().with_main_axis_size(MainAxisSize::Min).with_spacing(6.);
        row = row.with_child(self.text(item.badge(), 12., color));
        let mut left = cols.saturating_sub(4);
        if let Some(name) = item.name() {
            let name = footer::elide(name, left.min(24));
            left = left.saturating_sub(footer::width(&name) + 1);
            row = row.with_child(self.text(name, 12., color));
        }
        row = row.with_child(self.text(
            footer::elide(&item.title, left),
            13.,
            palette::FG,
        ));
        row.finish()
    }

    /// 화면 아래 요약 구역 — 블록(P4)과 Claude(P5).
    ///
    /// 화면 **위에 덧붙이지 않고 아래에** 붙인다. 이 구역은 무슨 일이 있었는지 훑는
    /// 용도이고 실제 내용은 캔버스가 이미 그린다 — 가리면 오히려 방해가 된다(TUI 와 같다).
    ///
    /// 줄 수는 [`footer`] 가 정한다. **구역이 커지면 안 된다** — 크롬이 커진 만큼 서버
    /// 캔버스가 밀리고, 그건 새 크기를 알려야만 고쳐진다(P5 결정 ③). 그래서 Claude 가
    /// 붙어도 이 구역은 **안 자란다** — 블록과 나눠 쓴다.
    /// 끊겼다 — 사유를 맨 아래 한 줄에 걸고 **알림 이력에도 남긴다**.
    ///
    /// 이력에 남기는 이유: 그 한 줄은 다시 붙는 순간 사라지는데, 사용자가 그 줄을 눌러
    /// 여는 곳이 이력이다. 안 남기면 정작 누른 사람이 **빈 이력**을 본다(2026-07-30 라이브
    /// 실측 — "아직 알림이 없다"만 떴다). `last_error` 는 안 세운다(`note_error_history`).
    fn note_ended(&mut self, reason: String) {
        if let Some(line) = status::message_line(Some(&reason), None) {
            self.state.note_error_history(line);
        }
        self.ended = Some(reason);
    }

    /// 크롬 자리 색인이 **끝나는 자리** — 탭들(`0..n`) · `+`(n) · `×`(n+1) · 상태줄
    /// (배지들 + 클릭되는 우측 구간)이 쓴 자리 수.
    ///
    /// 새 클릭 표면은 여기서부터 잇는다. 색인이 겹치면 두 표면이 **같은 `MouseState`**
    /// 를 나눠 써서, 한쪽에 마우스를 올렸을 때 엉뚱한 쪽이 밝아진다(자리 수를 손으로
    /// 두 번 적지 않으려고 함수 하나로 둔다 — `render_status` 의 색인 산수와 같은 식).
    fn chrome_slot_end(&self) -> usize {
        let sctx = self.state.status_ctx();
        let clickable_parts = status::expand_parts(&self.config.status_right, &sctx)
            .into_iter()
            .filter(|(kind, _)| status::run_badge(*kind).is_some())
            .count();
        // ⚠ 플러그인 배지도 자리를 쓴다 — 빠져 있어서 새 클릭 표면이 그 위에 겹칠
        //   수 있었다(`render_status` 의 색인 산수와 **같은 식**이라야 한다).
        self.state.tabs().tabs.len()
            + 2
            + self.state.badges().len()
            + self.state.plugin_badges().len()
            + clickable_parts
    }

    /// 끊김·서버 오류 한 줄 — **창 맨 아래**(사용자 요청 2026-07-30).
    ///
    /// # 왜 맨 아래인가
    ///
    /// 종전 자리는 탭바 **바로 밑**이었다. 그 자리는 줄이 하나 생기는 순간 캔버스를
    /// 아래로 밀고(서버가 새 크기로 다시 그린다) 사라질 때 되밀어, 끊겼다 붙는 동안
    /// 화면이 출썩인다. 파이썬 정본도 이 글을 **상태줄 메시지 자리**(맨 아래)에 놓는다.
    ///
    /// # 클릭 = 알림 이력
    ///
    /// 이 줄은 **지금 것 하나**만 보인다 — 지나간 메시지는 알림 화면에만 남는다. 그래서
    /// 상태줄 `[알림]` 배지와 **같은 클릭 대상**([`Badge::Notices`])으로 감싼다: 눌러 본
    /// 사람이 "그 앞의 메시지"에 닿을 수 있어야 한다(TUI 도 같은 배지로 같은 곳을 연다).
    fn render_message(&self, column: Flex) -> Flex {
        let Some(flash) = self.live_flash() else {
            return column;
        };
        // ★ 색은 **심각도가 정한다**(§10-21ⓝ). 종전에는 이 줄이 빨강 고정이었는데,
        //   복사 결과까지 그 자리에 오면서 성공이 오류로 읽히게 됐다. 알림 이력이 이미
        //   `Severity` 로 색을 가르므로 **그 표를 그대로** 쓴다(두 자리가 다른 표를 들면
        //   같은 사건이 화면마다 다른 색이 된다).
        let color = Self::severity_color(flash.severity);
        let slot = self.chrome_slot_end();
        let mut boxed = Container::new(self.ui_text(flash.text.clone(), 13., color))
            .with_horizontal_padding(6.)
            .with_vertical_padding(1.)
            .with_corner_radius(theme::PILL_RADIUS);
        if self.chrome_hovered(slot) {
            boxed = boxed.with_background_color(theme::HOVER);
        }
        // 글은 눌러 **이력**으로 가고(ⓦ⑶ — 이미 있던 배선), 닫기는 그 옆이다(ⓦ⑵).
        let line = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_spacing(4.)
            .with_child(self.clickable_chrome(
                slot,
                base::chrome::ClickTarget::Badge(base::Badge::Notices),
                boxed.finish(),
            ))
            .with_child(self.dismiss_button(slot + 1));
        column.with_child(line.finish())
    }

    /// 하단 한 줄의 닫기 `×`(§10-21ⓦ⑵).
    fn dismiss_button(&self, slot: usize) -> Box<dyn Element> {
        let hovered = self.chrome_hovered(slot);
        let mut boxed = Container::new(
            self.ui_text("×", 12., if hovered { palette::FG } else { palette::DIM }),
        )
        .with_horizontal_padding(4.)
        .with_corner_radius(theme::PILL_RADIUS);
        if hovered {
            boxed = boxed.with_background_color(theme::HOVER);
        }
        self.clickable_chrome(
            slot,
            base::chrome::ClickTarget::DismissMessage,
            boxed.finish(),
        )
    }

    /// 알림 이력과 **같은 표**로 심각도 → 색.
    fn severity_color(severity: Severity) -> ColorU {
        match severity {
            Severity::Error => theme::ERROR,
            Severity::Warn => theme::WARN,
            Severity::Ok => theme::OK,
            Severity::Info => palette::FG,
        }
    }

    /// 지금 보여야 하는 한 마디. 시한이 지났으면 `None`(§10-21ⓦ⑴).
    ///
    /// # 왜 끊김은 안 사라지나
    ///
    /// 연결이 끝난 것은 **지나가는 사건이 아니라 지금 상태**다 — 그 줄이 사라지면
    /// 화면은 멀쩡해 보이는데 아무것도 안 오는 창이 된다. 그래서 시한은 지나가는
    /// 말에만 건다.
    fn live_flash(&self) -> Option<Flash> {
        if let Some(reason) = self.ended.as_deref() {
            return status::message_line(Some(reason), None)
                .map(|text| Flash { text, severity: Severity::Error, at: None });
        }
        let flash = self.flash.as_ref()?;
        match flash.at {
            Some(at) if at.elapsed() >= Self::FLASH_TTL => None,
            _ => Some(flash.clone()),
        }
    }

    #[cfg(test)]
    pub(crate) fn note_flash_for_test(&mut self, text: String, severity: Severity) {
        self.note_flash(text, severity);
    }

    #[cfg(test)]
    pub(crate) fn live_flash_for_test(&self) -> Option<Flash> {
        self.live_flash()
    }

    /// 하단 한 줄에 한 마디를 띄운다(시한 시작).
    fn note_flash(&mut self, text: String, severity: Severity) {
        self.flash = Some(Flash { text, severity, at: Some(std::time::Instant::now()) });
    }

    /// 서버 오류가 왔으면 그것을 하단 한 줄로 **옮긴다**(ⓝ 로 자리가 하나가 됐다).
    ///
    /// 상태에서 걷어내는 이유: 두 자리가 같은 오류를 들고 있으면 닫아도 다음 프레임에
    /// 다시 뜬다. 이력은 `note_error_history` 가 이미 따로 갖고 있다.
    fn adopt_error(&mut self) -> bool {
        let Some(err) = self.state.last_error().map(str::to_owned) else {
            return false;
        };
        self.state.clear_error();
        if let Some(line) = status::message_line(None, Some(&err)) {
            self.note_flash(line, Severity::Error);
        }
        true
    }

    /// 시한이 막 지났나 — 지났으면 그 프레임에 다시 그려 줄을 지운다.
    fn tick_flash(&mut self) -> bool {
        let Some(flash) = &self.flash else { return false };
        let Some(at) = flash.at else { return false };
        if at.elapsed() < Self::FLASH_TTL {
            return false;
        }
        self.flash = None;
        true
    }

    /// 블록·Claude **요약 판**(§10-21ⓓ).
    ///
    /// # 왜 화면 아래가 아니라 판인가
    ///
    /// 제보 그대로다: *"이 판은 GUI 에만 있고 pytmux 사용에 직접적인 영향을 주지
    /// 않으므로, 화면에서 빼고 별도 명령어나 메뉴로 접근하게 한다."* 종전에는 이 구역이
    /// 화면 아래 한 자리를 상시로 먹었고(접혀 있어도 머리줄 한 줄), 그만큼 **서버
    /// 캔버스가 좁았다** — 훑는 용도의 요약이 화면의 주인공을 밀어내던 셈이다.
    /// 2026-08-02l 에서 "기본 접힘"으로 줄인 그 근거를 끝까지 민 결과이기도 하다.
    ///
    /// 판이 되면서 **접기(`Fold`)가 사라졌다** — 판은 열면 다 보이는 것이 자연스럽고,
    /// 접힌 판은 "왜 안 보이지"가 된다. 예산(`footer::ROWS`)은 그대로 쓴다: 판이라도
    /// 목록이 무한정 길면 그것이 곧 ⓗ·ⓢ 가 말하는 "내용이 판을 정한다"가 된다.
    fn render_summary(&self, column: Flex) -> Flex {
        let blocks = self.state.active_blocks();
        let (block_rows, claude_rows) = footer::split(!blocks.is_empty(), !self.claude.is_empty());
        if block_rows == 0 && claude_rows == 0 {
            return column.with_child(self.text(t("블록도 Claude 항목도 없다"), 13., palette::DIM));
        }
        // 폭 예산은 **캔버스와 같다** — 요약이 그림보다 넓으면 창이 그만큼 늘어나거나
        // 글자가 창 밖으로 나간다. 캔버스가 아직 없으면 서버에 알린 값(80)을 쓴다.
        let cols = self
            .state
            .composite()
            .map_or(80, |canvas| canvas.size().0) as usize;

        // 개수 한 줄은 그대로 둔다 — 판을 열자마자 "몇 개인가"가 보여야 한다.
        // (종전 머리줄이 하던 말이고, 이제 판 안의 첫 줄이다.)
        let head = footer::head(
            blocks.len(),
            self.claude.len(),
            self.claude_mode.as_deref(),
            self.state.active_tab_is_remote(),
            footer::Fold::Open,
        );
        let column = column.with_child(self.text(head, 12., palette::DIM));
        let mut inner = Flex::column();
        for block in footer::tail(blocks, block_rows) {
            inner = inner.with_child(self.render_block(block, cols));
        }
        for item in footer::tail(&self.claude, claude_rows) {
            inner = inner.with_child(self.render_claude(item, cols));
        }
        column.with_child(
            Container::new(inner.finish())
                .with_border(Border::all(1.).with_border_color(palette::DIM))
                .with_corner_radius(theme::PILL_RADIUS)
                .with_horizontal_padding(4.)
                .finish(),
        )
    }

    /// 플러그인이 준 화면(설계 Tier C · P4) — 목록과 글 두 모양.
    ///
    /// 내용·제목·안내·키는 전부 **스펙**에 있다. 이 함수가 아는 것은 "목록은 줄마다 한
    /// 칸, 글은 줄 단위 스크롤"뿐이라, 플러그인이 화면 흐름을 바꿔도 여기를 안 고친다.
    fn render_plugin_view(&self, column: Flex) -> Flex {
        let Some(spec) = self.state.plugin_screen() else {
            return column;
        };
        let mut budget = self.panel_budget();
        let mut column = column;
        // ★ **판 위 한 줄**(`spec.head`) — 정본 토큰 팝업의 공유 머리줄이 이 자리로 온다
        //   (pytmux-419 ②). 정본은 그 다섯이 한 팝업의 탭이라 머리줄을 나눠 쓰는데,
        //   여기는 판이 여럿이라 판마다 같은 줄이 실려 온다.
        //
        //   ⚠ **예산을 먼저 뗀다**: 이 줄도 한 줄을 먹는다. 안 떼면 목록이 그만큼
        //     아래로 넘친다(같은 실수를 `text` 갈래가 구분선에서 한 번 했다).
        //   ⚠ `hint_text` 로 그린다 — 접히게 하려는 것이다(pytmux-371 ⓐ · CL 74348).
        //     이 줄은 길고(Σ·캐시·머신 귀속·미상이 한 줄이다) 잘리면 값이 사라진다.
        //   ⛔ `note` 와 다른 자리다: 저것은 실패·빈 목록이라 평상시엔 비고, 이것은
        //      평상시에 늘 있는 자료다.
        //
        //   ☠ **`panel` 은 빼야 한다 — 그 갈래가 제 머리줄을 «제 서식으로» 이미 그린다**
        //     (아래 `"panel" =>` 의 `mono_row(… PANEL_COLS … CYAN)` · CL 73249). 이 공통
        //     줄이 CL 74528 로 서면서 그 판정을 안 봐, `mdir` 의 볼륨줄이 **두 번** 떴다:
        //     여기서 한 번(UI 글꼴 · DIM), 판 안에서 한 번(고정폭 · 시안). 정본 mdir 은
        //     그 줄을 판 안에 **한 줄만** 그리므로 ⓖ3(전면 1:1 대조)의 갈림이기도 하다.
        //   ⛔ **여기를 지우고 공통 줄에 맡기지 마라** — 판의 머리줄은 열 폭(`PANEL_COLS`)
        //     에 맞춰 자르고 고정폭으로 그려야 아래 격자와 자리가 맞는다. 그리고 판은
        //     제 기하를 `panel_grid()` 가 따로 세는데(거기서 `head`·`foot` 줄을 이미
        //     뺀다) 이 공통 줄은 그 셈 **밖**이라, 그리는 순간 판이 한 줄만큼 넘친다.
        if !spec.head.is_empty() && spec.kind != "panel" {
            budget = budget.saturating_sub(1);
            column = column.with_child(self.hint_text(spec.say_head(), self.ui_font, 12., palette::DIM));
        }
        let budget = budget;
        // 실패했거나 비었을 때의 한 줄 — **빈 목록과 실패는 다르다**(스펙의 `note`).
        if !spec.note.is_empty() {
            column = column.with_child(self.text(spec.say_note(), 13., palette::DIM));
        }
        match spec.kind.as_str() {
            "list" => {
                let selected = self.screens.selected().min(spec.rows.len().saturating_sub(1));
                let start = (selected + 1).saturating_sub(budget);
                for (row, item) in spec.rows.iter().enumerate().skip(start).take(budget) {
                    // 부가 칸(`cols`)은 뒤에 흐리게 — 정본 목록 화면과 같은 짜임이다.
                    // ★ 줄마다 **뜻이 있으면 그 색**으로(pytmux-11·12 A). 정본은 디렉터리를
                    //   붉게, 숨은 파일을 보라로, 고른 줄을 노랗게 칠한다 — 제보가 *"컬러
                    //   스킴 일치가 특히 중요하다"* 고 못박은 자리다. 뜻이 없으면 기본색.
                    let fg = proto::rowtag::color(&item.tag)
                        .map_or(palette::FG, |c| to_gui_color(&c));
                    let mut line = Flex::row()
                        .with_main_axis_size(MainAxisSize::Min)
                        .with_cross_axis_alignment(CrossAxisAlignment::Center)
                        .with_spacing(8.)
                        // ★ **트리**면 깊이와 펼침을 그린다(pytmux-11 B). 목록형은
                        //   `depth == 0` · `expand` 가 비어 종전 그대로다.
                        //   들여쓰기를 서버가 글자로 안 넣는 이유: 그러면 이름에 공백이
                        //   섞여 `label` 이 더는 자료가 아니게 된다.
                        .with_child(self.text(
                            format!(
                                "{}{}",
                                "  ".repeat(item.depth as usize),
                                // 접힘과 **잎**은 다르다 — 빈 디렉터리에 눌러도 안 열리는
                                // 화살표를 붙이면 그 화살표가 거짓말이 된다.
                                match item.expand.as_str() {
                                    "open" => "▾ ",
                                    "shut" => "▸ ",
                                    _ => "  ",
                                }
                            ),
                            13.,
                            palette::DIM,
                        ))
                        .with_child(self.text(item.say_label(), 13., fg));
                    // 칸은 플러그인이 **적은 말**이라 우리 로케일로 다시 읽는다
                    // (이름은 자료라 그대로 — `PluginRow::say_cols`).
                    for col in item.say_cols() {
                        line = line.with_child(self.text(col, 12., palette::DIM));
                    }
                    let boxed = Container::new(line.finish()).with_uniform_padding(1.);
                    column = column.with_child(self.clickable_panel(
                        row,
                        base::PanelTarget::Row(row),
                        if row == selected {
                            boxed.with_background_color(palette::SELECTED_BG).finish()
                        } else {
                            boxed.finish()
                        },
                    ));
                }
            }
            "text" => {
                let mut drawn = 0usize;
                // 본문도 **이 클라의 로케일로**(`say_text`) — 이 판에 산문이 오기
                // 시작했다(원격 제어). 자료(한도 막대)는 카탈로그에 없어 그대로 지난다.
                //
                // ★ 구역 사이에는 **실제 선**을 긋는다(§10-21ⓛ2). 어디가 경계인지는
                //   플러그인이 정하고(`sections`), 여기서는 그 사이에 무엇을 그릴지만
                //   정한다 — 정본은 같은 자리를 선문자로 긋는다(테두리와 같은 갈림).
                //   그 선도 **한 줄을 먹는다**: 예산을 안 세면 판이 아래로 넘친다.
                let body = Self::join_sections(&spec.say_sections());
                // ★ **스크롤 상한**(pytmux-184) — `press()` 의 `Key::Down`/`PageDown` 은
                // 줄 수를 몰라 무조건 증가만 시킨다(그 자리는 core 라 이 스펙의 줄 수를
                // 모른다 — `press_list` 와 같은 경계). 여기서 그려지는 줄 수를 아는
                // 뷰가 상한을 매긴다 — 안 매기면 `scroll` 이 총 줄 수를 넘겨
                // `skip(scroll)` 이 빈 이터레이터가 되고 판이 통째로 빈다.
                let max_scroll = body.lines().count().saturating_sub(budget);
                let scroll = self.screens.scroll().min(max_scroll);
                for line in body.lines().skip(scroll).take(budget) {
                    if line == Self::SECTION_MARK {
                        drawn += 1;
                        column = column.with_child(
                            Container::new(Flex::column().finish())
                                .with_vertical_padding(4.)
                                .with_border(
                                    Border::top(1.).with_border_color(theme::BORDER),
                                )
                                .finish(),
                        );
                        continue;
                    }
                    drawn += 1;
                    // ★ **긴 줄을 자른다**(§10-21ⓚ2). 폭을 못박아도 줄이 안 접히면
                    //   상한을 넘겨 밀고 나간다 — `p4changes` 의 CL 설명 한 줄이 정확히
                    //   그 부류다(제보: "판이 화면을 통째로 가린다"). 자를 때 `…` 를
                    //   붙이는 것은 요약 구역이 쓰던 규칙 그대로다(`footer::elide`).
                    let line = footer::elide(line, Self::PANEL_COLS);
                    // ★ **격자에 못박아** 그린다(pytmux-9 ⑵ 와 같은 규칙). 이 판에는
                    //   글자 그림이 온다 — 한도 막대(`█▏▎…░`)가 그렇고, 그 글자들은
                    //   폴백 글꼴에서 와 진폭이 다르다. 통짜로 넘기면 막대 길이가
                    //   행마다 어긋나 보이고, 그건 **값을 잘못 읽게 만드는** 어긋남이다.
                    // 채움 줄과 **같은 상자**다(pytmux-373 ⑴) — 여기도 `pad_rows` 로
                    // 줄 수를 맞추므로, 상자를 빼면 그 맞춤이 픽셀에서 깨진다.
                    column = column
                        .with_child(self.panel_row_box(self.mono_row(&line, 13., palette::FG)));
                }
                column = self.pad_rows(column, drawn, budget);
            }
            // 표는 목록과 **같은 자료**(rows)를 칸 맞춰 그린 것이다. 갈라 두는 이유는
            // 정본과 같다 — 목록은 고르는 화면이고 표는 읽으며 고르는 화면이라, 칸의
            // 세로줄이 맞아야 읽힌다.
            "table" => {
                let selected = self.screens.selected().min(spec.rows.len().saturating_sub(1));
                let start = (selected + 1).saturating_sub(budget);
                for (row, item) in spec.rows.iter().enumerate().skip(start).take(budget) {
                    // 목록과 **같은 규칙**으로 색을 푼다(pytmux-12 A) — mdir 이 이 갈래다.
                    let fg = proto::rowtag::color(&item.tag)
                        .map_or(palette::FG, |c| to_gui_color(&c));
                    let mut line = Flex::row()
                        .with_main_axis_size(MainAxisSize::Min)
                        .with_cross_axis_alignment(CrossAxisAlignment::Center)
                        .with_spacing(8.)
                        .with_child(
                            ConstrainedBox::new(
                                Flex::row()
                                    .with_main_axis_size(MainAxisSize::Min)
                                    // ★ **표도 트리를 인다**(pytmux-419 ③). 정본 `[기간]`
                                    //   탭은 월→주→일→시각을 한 트리로 보이고, 서버는
                                    //   줄마다 `depth`·`expand` 를 **이미 싣고 있었다**
                                    //   (`screenspec._tree_rows`). 이 갈래만 그 둘을 안
                                    //   읽어서 판이 통째로 평면이었다 — `"list"` 갈래는
                                    //   오래전부터 읽는다(pytmux-11 B). 계약이 없던 것이
                                    //   아니라 소비자 하나가 뒤처진 것이다.
                                    //
                                    //   ⚠ **이름 상자 «안에»** 둔다: 표는 칸의 세로줄이
                                    //   맞아야 읽히는 화면이라, 들여쓰기를 상자 밖에 두면
                                    //   깊이마다 뒤 칸이 밀려 표가 아니게 된다.
                                    .with_child(self.text(
                                        format!(
                                            "{}{}",
                                            "  ".repeat(item.depth as usize),
                                            // 잎에 화살표를 붙이면 그 화살표가 거짓말이다
                                            // (눌러도 안 열린다) — 서버가 빈 값으로 그것을
                                            // 말한다.
                                            match item.expand.as_str() {
                                                "open" => "▾ ",
                                                "shut" => "▸ ",
                                                _ => "",
                                            }
                                        ),
                                        13.,
                                        palette::DIM,
                                    ))
                                    .with_child(self.text(item.say_label(), 13., fg))
                                    .finish(),
                            )
                            .with_width(220.)
                            .finish(),
                        );
                    // ★ **비율이 오면 막대로 그린다**(pytmux-371 ③). 정본은 같은 뜻을
                    //   `█` 글자로 그리는데(격자라 그 길뿐이다) 여기는 면이다 — 사용자
                    //   지시대로 «GUI 기반 인터페이스»이고, 서버는 비율만 싣는다.
                    //
                    //   ⚠ **칸보다 먼저** 그린다: 이름 상자가 고정 폭이라 막대의 x 가
                    //   줄마다 같아지고, 그래야 여러 줄이 **막대 차트로** 읽힌다(칸 뒤에
                    //   두면 칸 길이에 따라 막대가 들쭉날쭉해져 길이 비교가 안 된다).
                    //   막대가 없는 줄(계정·신선도)은 그 자리가 비어 칸이 앞으로 온다 —
                    //   그 줄은 비교 대상이 아니라 설명이라 그래도 된다.
                    if let Some(permille) = item.bar {
                        line = line.with_child(self.ratio_bar(permille));
                    }
                    // 칸은 플러그인이 **적은 말**이라 우리 로케일로 다시 읽는다
                    // (이름은 자료라 그대로 — `PluginRow::say_cols`).
                    //
                    // ★ **칸마다 뜻이 있으면 그 색으로**(pytmux-419 ⑥). 정본 `[기간]` 탭은
                    //   `5h%`·`1w%` 를 비율에 따라 초록·노랑·빨강으로 칠한다 — 같은 줄
                    //   안에서도 `Tokens` 칸은 한 색이라 **줄 태그(`tag`)로는 못 말하는**
                    //   갈림이고, 그래서 서버가 칸마다 이름을 싣는다(`coltags`).
                    //
                    //   ⚠ 색은 **크롬 의미색**이다(`theme::{OK,WARN,ERROR}`) — 여기에
                    //     `palette::{GREEN,YELLOW,RED}` 를 끌어오면 pytmux-412 ⓑ1 이 떼어
                    //     놓은 것(«SGR 표는 정본 충실도 · 크롬은 가독»)이 조용히 되살아난다.
                    //   ⛔ 눈금(≥50 주의 · ≥80 위험)을 여기서 다시 세지 않는다: 판정은
                    //     정본 `usagehead.pct_level` 한 벌이고 여기는 **이름을 색으로만**
                    //     푼다. 두 클라가 각자 임계를 적으면 갈리는 날 아무도 안 운다.
                    for (i, col) in item.say_cols().into_iter().enumerate() {
                        let fg = match item.col_level(i) {
                            Some(proto::celltag::Level::Ok) => theme::OK,
                            Some(proto::celltag::Level::Warn) => theme::WARN,
                            Some(proto::celltag::Level::Crit) => theme::ERROR,
                            // 뜻이 없는 칸은 종전 그대로 흐리게 — 대부분의 표가 그렇다.
                            None => palette::DIM,
                        };
                        line = line.with_child(self.text(col, 12., fg));
                    }
                    // ★ **카운트다운**(pytmux-371 ④) — 정본 `[한도]` 탭이 다음 리셋까지를
                    //   큰 글자로 센다. 서버는 **언제인지**만 싣고(`until`) 남은 시간은
                    //   여기서 굴린다 — 글자를 받아 그리면 초마다 프레임이 와야 한다.
                    //
                    //   ⚠ 지난 시각이면 아무것도 안 그린다(`countdown` 이 `None`). `0:00:00`
                    //   이 굳어 있으면 그것이 「지금 리셋된다」로 읽히는데, 실은 실측이
                    //   낡았다는 뜻이고 그 사실은 판의 신선도 줄이 따로 말한다.
                    if let Some(left) = item.countdown(Self::now_secs()) {
                        line = line.with_child(self.text(left, 15., palette::FG));
                    }
                    let boxed = Container::new(line.finish()).with_uniform_padding(1.);
                    column = column.with_child(self.clickable_panel(
                        row,
                        base::PanelTarget::Row(row),
                        if row == selected {
                            boxed.with_background_color(palette::SELECTED_BG).finish()
                        } else {
                            boxed.finish()
                        },
                    ));
                }
            }
            // ★ **다열 판**(설계 §4.3 · pytmux-126). 표와 같은 자료(`rows`)를 여러 열로
            //   흘려 담고 위아래에 스펙이 준 한 줄씩을 둔다 — 정본 `mdir` 의 모양이다.
            //
            //   ⛔ 표와 합치지 않는다: 표는 **칸의 세로줄이 맞아야 읽히는** 화면이고
            //      다열은 **한 화면에 몇 개가 보이나**가 요점이라, 같은 자료를 놓고도
            //      폭 예산을 정반대로 쓴다(표는 칸에, 다열은 열 수에).
            "panel" => {
                let (per_col, cols) = self.panel_grid();
                let per = per_col * cols;
                let selected = self.screens.selected().min(spec.rows.len().saturating_sub(1));
                // 스크롤은 **판 단위**다 — 정본 Mdir 의 도스 감각 그대로(부드러운 스크롤이
                // 없다). 한 줄씩 밀면 열 경계가 매 줄 바뀌어 눈이 자리를 잃는다.
                let start = if per > 0 { selected / per * per } else { 0 };
                if !spec.head.is_empty() {
                    column = column.with_child(self.mono_row(
                        &footer::elide(&spec.say_head(), Self::PANEL_COLS),
                        13.,
                        palette::CYAN,
                    ));
                }
                // 한 열이 쓸 칸. 열 사이 한 칸은 구분에 준다(정본은 `│` 를 긋는다).
                let cell_cols = (Self::PANEL_COLS / cols).saturating_sub(1).max(1);
                let cell_px = self.cell_px.get().map(|(w, _)| w * cell_cols as f32);
                let mut grid = Flex::row()
                    .with_main_axis_size(MainAxisSize::Min)
                    .with_cross_axis_alignment(CrossAxisAlignment::Start)
                    .with_spacing(8.);
                for c in 0..cols {
                    let mut col = Flex::column().with_cross_axis_alignment(CrossAxisAlignment::Start);
                    for r in 0..per_col {
                        // 채움은 **세로 우선**이다(정본과 같은 차례) — 가로로 채우면
                        // 같은 디렉터리가 두 클라에서 다른 순서로 보인다.
                        let row = start + c * per_col + r;
                        let Some(item) = spec.rows.get(row) else {
                            // 빈 자리도 **줄을 먹는다** — 안 채우면 열마다 높이가 달라져
                            // 판 아래의 꼬리줄이 들썩인다.
                            col = col.with_child(self.text(" ", 13., palette::DIM));
                            continue;
                        };
                        let fg = proto::rowtag::color(&item.tag)
                            .map_or(palette::FG, |c| to_gui_color(&c));
                        // 부가 칸은 **첫 것만** 오른쪽에 붙인다(정본은 좁은 열에서 크기만
                        // 보이고 시각은 열이 넓을 때만 붙인다). 칸은 플러그인이 적은
                        // 말이라 우리 로케일로 읽는다(`say_cols`).
                        let extra = item.say_cols().into_iter().next().unwrap_or_default();
                        let text = Self::panel_cell(&item.say_label(), &extra, cell_cols);
                        let boxed = Container::new(self.mono_row(&text, 13., fg))
                            .with_uniform_padding(1.);
                        let cell = if row == selected {
                            boxed.with_background_color(palette::SELECTED_BG).finish()
                        } else {
                            boxed.finish()
                        };
                        col = col.with_child(self.clickable_panel(
                            row,
                            base::PanelTarget::Row(row),
                            cell,
                        ));
                    }
                    let col = col.finish();
                    grid = grid.with_child(match cell_px {
                        Some(w) => ConstrainedBox::new(col).with_width(w).finish(),
                        None => col,
                    });
                }
                column = column.with_child(grid.finish());
                if !spec.foot.is_empty() {
                    column = column.with_child(self.mono_row(
                        &footer::elide(&spec.say_foot(), Self::PANEL_COLS),
                        13.,
                        palette::DIM,
                    ));
                }
            }
            // 폼은 **줄마다 값**이 붙은 목록이다. 값을 바꾸는 길은 `Enter`(그 줄의 액션)
            // 하나로 두고, 무엇을 물을지는 플러그인이 다음 스펙(`prompt`)으로 정한다 —
            // 값 편집 규칙을 스펙에 담기 시작하면 스펙이 화면마다 늘어난다(설계 §10).
            "form" => {
                let selected = self.screens.selected().min(spec.rows.len().saturating_sub(1));
                let start = (selected + 1).saturating_sub(budget);
                for (row, item) in spec.rows.iter().enumerate().skip(start).take(budget) {
                    // 값도 **플러그인이 적은 말**이라 우리 로케일로 읽는다(목록·표와 같은
                    // 규칙 — `PluginRow::say_cols`). 여기만 `cols` 를 날로 쓰던 동안
                    // 설정 판의 `끔`·`완료마다` 가 영어 사용자에게 한국어로 떴다.
                    let value = item.say_cols().into_iter().next().unwrap_or_default();
                    // ★ 켜기/끄기 값은 네이티브 토글 그림으로(pytmux-182 ⑵) — 서버가
                    // 켬/끔 값을 로케일 불문 고정 기호로 낸다(`screenspec.py`
                    // `pscreen.spec_on_mark`/`_off_mark` = "●"/"○"). 클릭 대상은 그대로
                    // 행 전체(`PanelTarget::Row` — "고르고 곧장 Enter") 이므로 그림만
                    // 바꿔도 동작은 안 갈린다. 셋 이상(순환값)은 그림이 없어 글자로 남는다.
                    let value_widget: Box<dyn Element> = match value.as_str() {
                        "●" => self.toggle(true),
                        "○" => self.toggle(false),
                        _ => self.text(value, 13., palette::CYAN),
                    };
                    let line = Flex::row()
                        .with_main_axis_size(MainAxisSize::Min)
                        .with_cross_axis_alignment(CrossAxisAlignment::Center)
                        .with_spacing(8.)
                        .with_child(
                            ConstrainedBox::new(
                                Flex::row()
                                    .with_main_axis_size(MainAxisSize::Min)
                                    .with_child(self.text(item.say_label(), 13., palette::FG))
                                    .finish(),
                            )
                            .with_width(196.)
                            .finish(),
                        )
                        .with_child(value_widget);
                    let boxed = Container::new(line.finish()).with_uniform_padding(1.);
                    column = column.with_child(self.clickable_panel(
                        row,
                        base::PanelTarget::Row(row),
                        if row == selected {
                            boxed.with_background_color(palette::SELECTED_BG).finish()
                        } else {
                            boxed.finish()
                        },
                    ));
                }
            }

            // ★ 모르는 모양은 **조용히 버리지 않는다**(설계 §8-5) — 빈 판이 뜨면
            //   사용자는 자기가 잘못 골랐다고 읽는다. P5 가 나머지 넷을 더한다.
            other => {
                column = column.with_child(self.text(
                    tf("이 화면 모양({kind})은 이 클라에서 아직 못 그립니다 — 터미널 클라(pytmux)에서 쓰세요",
                       &[("kind", other)]),
                    13.,
                    palette::DIM,
                ));
            }
        }
        column
    }

    /// 전역 검색 결과(pytmux-27) — 탭·패널·줄·미리보기 4열. 정본 `SearchResultsScreen`
    /// 과 같은 자료를 GUI 관례(픽셀 폭 칸 — `render_plugin_view` 의 "table" 모양과
    /// 같은 짜임)로 그린다. 상한·무응답 상류 안내는 **판 안**에 둔다(테두리 제목은
    /// 넘치면 조용히 잘려 no-silent-caps 를 어긴다).
    fn render_search_results(&self, column: Flex) -> Flex {
        let Some(sr) = self.state.search_results() else {
            // ★ **판이 침묵하지 않는다**(pytmux-405). 회신이 아직 없으면 종전에는 머리줄만
            //   있는 **빈 상자**가 떴다 — 사용자에게 그것은 「고장」과 구별되지 않는다.
            //   결과가 0건인 경우(`items.is_empty()`)는 아래에서 이미 말하고 있었고,
            //   빠져 있던 것은 **아직 안 온 경우**다.
            return column.with_child(self.text(t("찾는 중…"), 13., palette::DIM));
        };
        let mut column = column;
        let notes = sr.notes();
        if !notes.is_empty() {
            column = column.with_child(self.text(notes, 12., palette::DIM));
        }
        if sr.items.is_empty() {
            return column.with_child(self.text(t("검색 결과가 없다"), 13., palette::DIM));
        }
        let budget = self.panel_budget();
        let selected = self.screens.selected().min(sr.items.len() - 1);
        let start = (selected + 1).saturating_sub(budget);
        for (row, item) in sr.items.iter().enumerate().skip(start).take(budget) {
            // 원격 히트는 정본과 같은 색으로 갈린다(`REMOTE_PINK`).
            let tab_fg = if item.remote { REMOTE_PINK } else { palette::DIM };
            let line = Flex::row()
                .with_main_axis_size(MainAxisSize::Min)
                .with_cross_axis_alignment(CrossAxisAlignment::Center)
                .with_spacing(8.)
                .with_child(
                    ConstrainedBox::new(self.text(
                        footer::elide(&format!("{}:{}", item.win + 1, item.tab), 24),
                        12.,
                        tab_fg,
                    ))
                    .with_width(180.)
                    .finish(),
                )
                .with_child(
                    ConstrainedBox::new(self.text(
                        footer::elide(&item.title, 16),
                        12.,
                        palette::DIM,
                    ))
                    .with_width(120.)
                    .finish(),
                )
                .with_child(
                    ConstrainedBox::new(self.text(
                        if item.line >= 0 { item.line.to_string() } else { String::new() },
                        12.,
                        palette::DIM,
                    ))
                    .with_width(48.)
                    .finish(),
                )
                .with_child(self.text(item.text.clone(), 13., palette::FG));
            let boxed = Container::new(line.finish()).with_uniform_padding(1.);
            column = column.with_child(self.clickable_panel(
                row,
                base::PanelTarget::Row(row),
                if row == selected {
                    boxed.with_background_color(palette::SELECTED_BG).finish()
                } else {
                    boxed.finish()
                },
            ));
        }
        column
    }

    /// 플러그인이 물은 것에 답했다(`prompt` 의 글 · `confirm` 의 예). 취소면 `None` 이고
    /// 그때는 **아무 일도 안 일어난다** — 되돌릴 수 없는 것 앞의 규칙 그대로다.
    fn answer_plugin_ask(&mut self, answer: Option<String>) {
        let Some(spec) = self.state.plugin_screen() else {
            return;
        };
        let id = spec.id.clone();
        let act = spec.enter_action().map(str::to_owned);
        self.state.pop_plugin_screen();
        let (Some(act), Some(answer)) = (act, answer) else {
            return;
        };
        self.pending.push(Outgoing::Command(Command::PluginAction {
            id,
            act,
            row: 0,
            input: Some(answer),
        }));
    }

    /// 플러그인 화면에서 `Enter` 를 눌렀다 — **고른 줄의 뜻**을 되돌려준다.
    ///
    /// 자리(번호)가 아니라 그 줄의 `key` 를 싣는 이유: 목록은 서버가 다시 만들 수 있고,
    /// 그 사이 줄이 늘거나 줄면 자리는 **다른 줄**을 가리킨다.
    fn plugin_view_chosen(&mut self, row: usize) {
        let Some(spec) = self.state.plugin_screen() else {
            return;
        };
        let Some(act) = spec.enter_action().map(str::to_owned) else {
            return;                     // 이 화면은 Enter 에 뜻이 없다
        };
        // ⛔ **여기서 자른다.** `End` 는 커서를 `usize::MAX` 로 두고 «자르는 것은 그리는
        // 쪽»이 규약이다(`press_list`·`press_settings`) — 그 규약대로면 이 자리도
        // 자르는 쪽이다. 안 자르면 `End` 다음 `Enter` 가 **줄 없는 번호**와 `input:
        // None` 을 실어 보내고, 서버는 그것을 조용히 무시한다(화면에는 아무 일도 안
        // 난 것으로 보인다 · pytmux-417 ①의 곁가지).
        let row = row.min(spec.rows.len().saturating_sub(1));
        let id = spec.id.clone();
        let input = spec.rows.get(row).map(|r| r.key.clone());
        self.pending.push(Outgoing::Command(Command::PluginAction { id, act, row, input }));
    }

    /// 검색 결과 한 줄을 골랐다 — 그 자리로 `search_goto` 를 보낸다(pytmux-27).
    ///
    /// `route` 는 해석하지 않고 그 항목이 실어 온 그대로 되돌린다 — 좌표계를 아는 건
    /// 서버 하나다(로컬이면 빈 배열, 원격 히트면 서버가 캐스케이드로 릴레이한다).
    /// 점프 뒤에는 스크롤 모드다(정본 `_prompt_done` 의 `mode = "scroll"` 과 같은 자리 —
    /// 뛴 자리는 라이브 하단 밖이라, 평소 모드로 돌아오면 방향키가 패널로 가 버린다).
    fn search_result_chosen(&mut self, row: usize) {
        let Some(sr) = self.state.search_results() else { return };
        let Some(item) = sr.items.get(row) else { return };
        // `query` 는 이 결과판을 만든 검색어다(항목의 미리보기 글이 아니다) — 서버가
        // 점프 시점에 그 줄이 스크롤백 상한에서 밀려났으면 **같은 쿼리로 근처를
        // 재탐색**해 자리를 되찾는다(`search_all_hit_line`).
        let cmd = Command::SearchGoto {
            wid: item.wid,
            win: item.win,
            pane: item.pane,
            line: item.line,
            route: item.route.clone(),
            query: sr.query.clone(),
        };
        self.pending.push(Outgoing::Command(cmd));
        self.mode.enter_scroll();
    }

    /// 팔레트 필터 한 벌 — **이름 또는 설명**으로 거른다(정본과 같은 규칙).
    ///
    /// 한 곳에 모으는 이유: 이 목록이 ⑴그리는 줄 ⑵탭의 개수 ⑶고른 줄 되돌리기 셋 다에
    /// 쓰인다. 하나라도 다른 규칙을 쓰면 "보이는데 못 고르는 줄"이 생긴다.
    fn palette_rows(cat: Option<&str>, filter: &str) -> Vec<usize> {
        base::palette_matches_with(cat, filter, |name| {
            proto::command::command_help(name)
        })
    }

    /// 팔레트 한 줄의 정체 — 코어 표의 자리이거나 **플러그인이 기여한** 자리다.
    ///
    /// 갈라 두는 이유: 코어 표는 `static` 이라 자리로 가리킬 수 있지만, 플러그인 목록은
    /// **서버가 런타임에 주는 값**이라 자리를 못 박는다(설계 Tier A).
    fn palette_hits(&self, cat: Option<&str>, filter: &str) -> Vec<PaletteHit> {
        let mut out: Vec<PaletteHit> =
            Self::palette_rows(cat, filter).into_iter().map(PaletteHit::Core).collect();
        // 코어 **뒤**에 붙인다 — 이미 굳은 손버릇(첫 줄이 무엇인가)을 안 흔든다.
        //
        // 거르는 규칙은 core 가 쥔다(`PluginSurface::palette_rows`): 등급 정렬이 같아야
        // 같은 글자에 두 목록이 같은 식으로 걸리고, **이미 네이티브로 든 이름**
        // (`clock-mode` 등)이 두 번 서지 않는다.
        out.extend(
            self.screens.plugins().palette_rows(cat, filter).into_iter().map(PaletteHit::Plugin),
        );
        out
    }

    /// 팔레트에서 고른 줄의 액션(패리티 G3c).
    ///
    /// 줄 번호는 **걸러진 목록** 안 자리다 — 원래 표의 자리로 되돌려야 한다.
    fn palette_pick(&self, cat: Option<&str>, filter: &str, row: usize) -> Option<PalettePick> {
        match self.palette_hits(cat, filter).get(row)? {
            PaletteHit::Core(i) => base::PALETTE.get(*i).map(|e| PalettePick::Action(e.action)),
            PaletteHit::Plugin(i) => self
                .screens
                .plugins()
                .commands
                .get(*i)
                .map(|c| PalettePick::Plugin(c.name.clone())),
        }
    }

    /// 지금 고른 명령이 인자를 받으면 **그 문구**(없으면 `None`) — pytmux-7 요구 ⑶.
    ///
    /// 물음 판이 쓰던 문구와 **같은 것**이다(`Prompt::label`) — 이어 치는 길과 판을
    /// 띄우는 길이 다른 말을 하면 사용자는 둘을 다른 기능으로 읽는다.
    fn palette_arg_hint(&self) -> Option<String> {
        let name = self
            .palette_pick(
                self.screens.palette_cat(),
                self.screens.typed_filter(),
                self.screens.selected(),
            )?
            .name()?;
        // 이름만으로 물어본다 — 인자를 이미 쳤든 아니든 문구는 같다.
        let (head, _) = base::screens::split_first_space(&name);
        let prompt = base::hooks::arg_prompt(head)?;
        // 물음 판이 쓰는 그 문구 그대로다 — 두 길이 다른 말을 하면 안 된다.
        Some(tf("{q} (이어서 치기)", &[("q", prompt.question())]))
    }

    /// 팔레트 입력줄에서 **인자까지 이어 친** 줄을 실행한다(pytmux-7).
    ///
    /// # 왜 물음 판을 안 띄우나
    ///
    /// 제보: *"명령 인자는 정본 TUI 처럼 그 줄에서 이어 친다 — 별도 입력 팝업을 띄우지
    /// 않는다."* 정본에서 `:` 를 치면 화면 바닥의 그 한 줄이 **필터이자 명령줄**이고,
    /// 인자는 거기서 이어 친다. 우리는 그 줄을 이미 판 맨 아래에 두었으니 남은 것은
    /// 그 줄이 인자를 먹게 하는 것이었다.
    ///
    /// # 해석은 core 한 벌이 한다
    ///
    /// 줄을 뜻으로 옮기는 규칙은 [`base::hooks::resolve`] 다 — 훅(`set-hook`)이 돌리는
    /// 줄과 **같은 해석기**다. 여기서 따로 파싱하면 `:remote-attach box1` 과
    /// `set-hook … 'remote-attach box1'` 이 다르게 동작하기 시작한다.
    ///
    /// # 물음 판은 안 없어진다
    ///
    /// 인자 없이 고르면 종전대로 물음이 뜬다(인자 이력도 거기 그대로다) — 제보는
    /// 이어 치는 길을 **더하라**는 것이지 다른 길을 없애라는 것이 아니었다.
    fn run_with_arg(&mut self, pick: PalettePick, arg: &str) {
        let Some(name) = pick.name() else {
            return;
        };
        // 플러그인 명령은 **갈래를 우리가 안 정한다**(pytmux-35) — 이름과 인자를 그대로
        // 보내고 서버가 상태형인지 화면인지 정한다.
        if let PalettePick::Plugin(name) = pick {
            self.pending.push(Outgoing::Command(Command::PluginCmd {
                name,
                args: vec![arg.to_owned()],
            }));
            return;
        }
        match base::hooks::resolve(&format!("{name} {arg}")) {
            // 인자를 받는 명령 — 물음의 답으로 **바로** 넘긴다(판을 안 띄운다).
            Some(base::hooks::HookRun::Answer(prompt, answer)) => {
                self.apply_answer(prompt, answer);
            }
            // 인자를 안 받는 명령에 인자를 쳤다 — 명령은 살리고 인자는 버린다.
            // (조용히 아무 일도 안 하는 것보다 낫다. 정본도 같은 자리에서 이름을 살린다.)
            Some(base::hooks::HookRun::Act(action)) => {
                self.apply_action(action);
            }
            // 여기 오면 고른 줄의 이름이 표에 없다는 뜻이다 — 액션으로 되돌린다.
            None => {
                if let PalettePick::Action(action) = pick {
                    self.apply_action(action);
                }
            }
        }
    }

    /// 명령 팔레트 화면(패리티 G3c).
    /// 팔레트 **카테고리 탭줄**(레이아웃 맞추기 ⑩ — 정본 `CommandListScreen` 의 탭 그룹).
    ///
    /// 탭마다 **일치 수**를 적는다. 정본이 그러는 이유가 이 화면의 요점이다: 친 글자가
    /// 다른 탭에만 걸릴 때, 개수가 없으면 화면은 그냥 "결과 없음"이고 사용자는 이름을
    /// 잘못 안 줄 안다. 결과가 있는 비활성 탭은 밝게, 없는 탭은 흐리게.
    fn palette_tabs(&self, filter: &str) -> Box<dyn Element> {
        // 목록과 **같은 규칙**으로 센다 — 이름만 세면 탭줄이 거짓말을 한다
        // (설명으로 걸린 줄이 있는 탭이 `(0)` 으로 보인다). 플러그인 줄도 함께 센다.
        let plugins = self.screens.plugins();
        let counts = plugins.palette_tab_counts(filter, |name| {
            proto::command::command_help(name)
        });
        let now = self.screens.palette_tab();
        let searching = !filter.trim().is_empty();
        let mut row = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_spacing(4.);
        // 탭 이름: `전체` + 코어 분류 + **플러그인이 낸 분류**(`탐색`·`Perforce`).
        // 없으면 그 명령들이 `전체` 탭에서만 보인다.
        let labels: Vec<String> = std::iter::once(
            base::i18n::tc("cat", base::PALETTE_CAT_ALL).to_owned(),
        )
        .chain(
            plugins.palette_cats().into_iter().map(|c| base::i18n::tc("cat", c).to_owned()),
        )
        .collect();
        for (i, label) in labels.into_iter().enumerate() {
            let n = counts.get(i).copied().unwrap_or(0);
            let on = i == now;
            let fg = if on || (searching && n > 0) { palette::FG } else { palette::DIM };
            // 개수는 **검색 중에만** 적는다 — 안 치고 있을 때의 총계는 아무 판단에도 안
            // 쓰이고 탭줄만 길어진다(정본도 비활성 탭에 검색 중에만 수를 적는다).
            let text =
                if searching { format!("{label} ({n})") } else { label.to_string() };
            let mut boxed = Container::new(self.ui_text(text, 12., fg))
                .with_horizontal_padding(6.)
                .with_vertical_padding(2.)
                .with_corner_radius(theme::TAB_RADIUS);
            if on {
                boxed = boxed.with_background_color(theme::ACTIVE);
            } else if self.panel_hovered(i) {
                boxed = boxed.with_background_color(theme::HOVER);
            }
            row = row.with_child(self.clickable_panel(
                i,
                base::PanelTarget::PaletteTab(i),
                boxed.finish(),
            ));
        }
        Container::new(row.finish()).with_padding_bottom(4.).finish()
    }

    /// 팔레트 **한 항목**을 세 칸으로(§10-21ⓞ) 그리고, 설명이 길면 접는다(ⓗ⑶).
    ///
    /// 돌려주는 것은 **한 덩이와 그것이 먹는 줄 수**다 — 접히면 둘 이상이고, 부르는
    /// 쪽이 그 수를 예산에서 뺀다(판 높이는 고정이므로 접힌 만큼 뒤가 밀려서는 안 된다).
    ///
    /// # 왜 줄마다가 아니라 한 덩이인가 (pytmux-157)
    ///
    /// 종전에는 접힌 줄마다 `Container` 를 따로 만들어 각각 배경을 깔았다. 그 줄들은
    /// 판의 열(`with_spacing(PANEL_ROW_SPACING)`)에 **형제로** 붙으므로 사이에 그
    /// 간격만큼 판 배경이 드러난다 — 실측(1920×1200 · 배율 1.5)으로 3px 짬이 났고,
    /// 하이라이트가 줄마다 끊겨 **한 항목이 선택 가능한 두 행처럼** 읽혔다(`ncd`).
    ///
    /// 그래서 배경은 **바깥 한 겹**에만 있고, 접힌 줄은 그 안의 열에 든다. 안쪽 열도
    /// 같은 `PANEL_ROW_SPACING` 을 쓰므로 **판 높이는 종전과 같다** — 달라지는 것은
    /// 그 짬을 무엇이 칠하느냐뿐이다(판 배경 → 항목 배경).
    ///
    /// # 칸 폭은 왜 상수인가
    ///
    /// 내용(가장 긴 이름)으로 정하면 필터를 칠 때마다 칼럼이 흔들린다 — 판 기하를
    /// 내용에서 뗀 판 공통 규칙(ⓗ·ⓢ·ⓥ)과 같은 이유다.
    ///
    /// # 색 셋
    ///
    /// 이름은 밝게, 옵션은 노랑, 설명은 흐리게. 고른 줄 배경(`SELECTED_BG`) 위에서도
    /// 셋이 다 읽혀야 한다 — 제보가 "색으로 구분"을 요구한 자리다.
    fn palette_item(
        &self,
        name: &str,
        desc: &str,
        selected: bool,
        left: usize,
    ) -> (Box<dyn Element>, usize) {
        let (cmd, opts) = proto::palette::split_name(name);
        // 폭이 캔버스를 따라 움직이므로 칸 수도 거기서 나온다(pytmux-368) — 상수에
        // 묶어 두면 판만 넓어지고 설명은 그대로 접힌다.
        let desc_cols = self
            .palette_cols()
            .saturating_sub(Self::PAL_NAME_COLS + Self::PAL_OPTS_COLS + 2);
        // 접힌 줄이 예산을 넘기지 않게 자른다 — 마지막 한 줄만 남았는데 설명이 세 줄이면
        // 그 항목이 판을 밀고 나간다.
        let mut wrapped = proto::palette::wrap(desc, desc_cols);
        wrapped.truncate(left.max(1));
        if wrapped.is_empty() {
            wrapped.push(String::new());
        }
        let cell = self.cell_px.get().map(|(w, _)| w);
        let col = |me: &Self, text: String, cols: usize, color| -> Box<dyn Element> {
            let t = me.text(text, 13., color);
            match cell {
                Some(w) => ConstrainedBox::new(t).with_width(w * cols as f32).finish(),
                None => t,
            }
        };
        // 안쪽 열의 행간은 판의 것과 **같은 상수**다 — 다르면 접힌 항목의 높이가 판의
        // 다른 줄과 어긋나 예산 셈(줄 수)과 실제 픽셀이 갈린다.
        let mut rows = Flex::column()
            .with_main_axis_size(MainAxisSize::Min)
            .with_spacing(Self::PANEL_ROW_SPACING);
        // 먹는 줄 수는 **접힌 조각 수**다(자식 수가 아니다 — 자식은 이제 하나다).
        let count = wrapped.len();
        for (i, chunk) in wrapped.into_iter().enumerate() {
            // 접힌 줄은 **설명 칸 아래**에 이어 붙는다(이름·옵션 칸은 비운다).
            let (n, o) = if i == 0 { (cmd.to_owned(), opts.to_owned()) } else { (String::new(), String::new()) };
            let line = Flex::row()
                .with_main_axis_size(MainAxisSize::Min)
                .with_child(col(self, n, Self::PAL_NAME_COLS, palette::FG))
                .with_child(col(self, o, Self::PAL_OPTS_COLS, palette::YELLOW))
                .with_child(col(self, chunk, desc_cols, palette::DIM))
                .finish();
            // 패딩은 줄마다다 · 배경은 아래 한 겹뿐이다. ⛔ 상자는 **`panel_row_box`
            // 한 곳**이 짓는다(pytmux-369) — 채움 줄이 같은 것을 써야 판 높이가 목록
            // 길이와 무관해진다.
            rows = rows.with_child(self.panel_row_box(line));
        }
        let item = if selected {
            Container::new(rows.finish())
                .with_background_color(palette::SELECTED_BG)
                .finish()
        } else {
            rows.finish()
        };
        (item, count)
    }

    fn render_palette(&self, column: Flex) -> Flex {
        // ★ 이 줄은 이제 **필터이자 명령줄**이다(pytmux-7) — 이름 쪽만 거른다.
        //   자르는 자리는 core 한 벌(`split_first_space`)이라 목록과 입력줄이 안 갈린다.
        let filter = self.screens.typed_filter();
        let arg = self.screens.typed_arg();
        let matches = self.palette_hits(self.screens.palette_cat(), filter);
        // ★ **입력 줄은 판의 맨 아래**다(2026-08-01 사용자 지시).
        //
        // 정본에서 `:` 를 치면 화면 **바닥**에 입력 박스가 서고(`PromptScreen` — 3행,
        // `dock: bottom`) 후보·목록은 그 **위로** 자란다. 우리는 그 두 역할(`:` 입력과
        // 명령 목록)을 팔레트 한 판이 겸하므로, 판 안에서 같은 기하를 만들어야 한다 —
        // 입력이 판 위쪽에 있으면 화면에서는 목록 **위**가 되어, 터미널에서 프롬프트가
        // 늘 아래에 있다는 감각과 어긋난다("시선이 하단에 가 있다").
        //
        // 분류 탭은 정본 팔레트와 같이 **머리**에 둔다(`#cmdtabs` — 상자의 머리줄이다).
        let mut column = column.with_child(self.palette_tabs(filter));
        // 입력줄은 **두 색**이다(pytmux-7 요구 ⑵ — "명령과 인자는 다른 색").
        // 팔레트 목록의 이름·옵션 색과 같은 짝을 쓴다(같은 뜻은 같은 색이라야 눈이 안 헷갈린다).
        // 입력기 배지는 이 줄의 오른쪽 끝(pytmux-14) — 여기가 지금 글자를 받는 곳이다.
        let input = |me: &Self| {
            // 인자를 안 쳤으면 **줄을 안 쪼갠다** — 조각을 나누면 그 자체가 그림을
            // 바꾸고(사이 간격), 이 줄을 재는 오라클도 두 모양을 알아야 한다.
            if arg.is_empty() {
                return me.input_line(me.text(format!("> {filter}_"), 14., palette::CYAN));
            }
            me.input_line(
                Flex::row()
                    .with_main_axis_size(MainAxisSize::Min)
                    .with_child(me.text(format!("> {filter}"), 14., palette::CYAN))
                    .with_child(me.text(format!(" {arg}_"), 14., palette::YELLOW))
                    .finish(),
            )
        };
        // ⓗ⑵ — **←→ 로 분류를 옮겨도 높이가 안 변한다**. 후보 수가 달라도 판은 같다.
        let budget = self.panel_budget().saturating_sub(1);
        if matches.is_empty() {
            let column = column.with_child(self.text(t("맞는 명령이 없다"), 13., palette::DIM));
            return self.pad_rows(column, 1, budget).with_child(input(self));
        }
        let selected = self.screens.selected().min(matches.len() - 1);
        let mut drawn = 0usize;
        for (row, hit) in matches.iter().enumerate() {
            if drawn >= budget {
                break;
            }
            let (name, desc) = match hit {
                PaletteHit::Core(i) => {
                    let entry = &base::PALETTE[*i];
                    // 설명은 파이썬 정본의 것(픽스처 — `command_help`) · 없는 이름은 액션
                    // 라벨로 접는다. CommandListScreen 의 역할이 이 줄이다(G9q).
                    // `cmd` 문맥: 같은 ko 원문이 액션 라벨과 겹치되 정본 영어가 다른 설명.
                    let desc = proto::command::command_help(entry.name)
                        .map(|d| tc("cmd", d).to_owned())
                        .unwrap_or_else(|| entry.action.label().to_owned());
                    (entry.name.to_owned(), desc)
                }
                // 플러그인 기여는 서버가 준 글을 그대로 쓴다(정본 팔레트와 같은 글).
                PaletteHit::Plugin(i) => {
                    let c = &self.state.plugin_surface().commands[*i];
                    (c.name.clone(), c.desc.clone())
                }
            };
            // 한 항목 = 한 자식(접혀도 그렇다 · pytmux-157) — 그러니 늘어난 줄 수는
            // 자식 수가 아니라 **그 항목이 말해 주는 값**으로 센다.
            let (item, rows) = self.palette_item(&name, &desc, row == selected, budget - drawn);
            column = column.with_child(item);
            drawn += rows;
        }
        // 목록 **뒤**에 입력 — 화면에서는 목록 아래, 곧 판의 맨 밑이다.
        self.pad_rows(column, drawn, budget).with_child(input(self))
    }

    /// 재시작 드라이런 회신을 게이트로 쓴다 — 안전하면 곧장, 아니면 **실패 항목을 적어**
    /// 다시 묻는다(파이썬 `_gate_restart_on_check` · TUI 와 같은 판정).
    fn gate_restart(&mut self) -> bool {
        use base::restart;
        let Some(kind) = self.pending_restart.take() else {
            return false;
        };
        let (safe, rows) =
            restart::evaluate(self.state.restart_probe(), restart::relaunch_ok(), kind);
        if safe {
            self.do_restart(kind);
            return true;
        }
        let prompt = match kind {
            restart::Kind::All => Prompt::RestartAll,
            restart::Kind::Server => Prompt::RestartServer,
        };
        self.screens
            .confirm_with(prompt, restart::failure_detail(&rows));
        true
    }

    /// 재시작을 **드라이런부터** 시작한다. 지난 회신은 먼저 버린다(그 값으로 판정하면
    /// 그 사이 패널이 닫혔을 때 거짓으로 통과시킨다).
    fn begin_restart(&mut self, kind: base::restart::Kind) {
        self.state.clear_restart_check();
        self.pending_restart = Some(kind);
        self.state
            .note_notice(t("재시작 안전성 점검 중… (부작용 없는 드라이런)"));
        self.pending
            .push(Outgoing::Command(Command::RequestRestartCheck));
    }

    /// 재시작을 **실제로** 실행한다. `All` 이면 이 클라도 다시 띄우고 빠진다.
    ///
    /// 먼저 우리를 다시 띄우고 **그다음** 서버에 시킨다 — 뒤집으면 서버가 사라진 뒤에
    /// 자식이 붙으려 해 실패한다.
    fn do_restart(&mut self, kind: base::restart::Kind) {
        use base::restart;
        if kind == restart::Kind::All {
            match restart::relaunch() {
                Ok(pid) => {
                    self.state
                        .note_notice(tf(
                            "전체 재시작 — 클라를 다시 띄웠다 (pid {pid})",
                            &[("pid", &pid.to_string())],
                        ));
                    self.quit_requested = true;
                }
                Err(why) => {
                    // 다시 못 띄웠으면 **서버도 안 건드린다** — 서버만 새것이고 클라가
                    // 옛 코드로 남으면 사용자가 기대한 것과 다른 상태가 된다.
                    self.state
                        .note_error(tf("전체 재시작 취소: {why}", &[("why", &why)]));
                    return;
                }
            }
        } else {
            self.state.note_notice(t("서버를 재시작한다 (셸은 산다)"));
        }
        self.pending
            .push(Outgoing::Command(Command::RestartServer));
    }

    /// 정체된 소켓을 버리고 **같은 엔드포인트에** 다시 붙는다(`reconnect`).
    ///
    /// 다시 탐색하지 않는다 — 사용자가 지목한 것과 다른 서버에 붙을 수 있다.
    fn reconnect_now(&mut self) {
        let socket = self.link.socket().to_owned();
        let (cols, rows) = self.size.reported();
        match proto::ServerLink::attach_to(&socket, cols, rows) {
            Ok(fresh) => {
                self.link = fresh;
                self.ended = None;
                // 사실은 연결에 매달려 있다 — 새 연결에는 다시 알려야 한다(pytmux-392).
                self.told_ime_render = false;
                self.state
                    .note_notice(tf("다시 붙었다: {socket}", &[("socket", &socket)]));
            }
            // 못 붙었으면 **끝내지 않는다** — 옛 링크가 아직 살아 있을 수 있고 사용자는
            // 다시 시도할 수 있다.
            Err(e) => self
                .state
                .note_error(tf("다시 붙지 못했다: {err}", &[("err", &e.to_string())])),
        }
    }

    /// 설정 `set-titles` 가 켜져 있으면 창 제목을 지금 상태로 갱신한다.
    ///
    /// TUI 는 바깥 단말에 OSC 2 를 흘리고 우리는 **우리 창**을 고친다 — 제목 문자열을
    /// 만드는 것은 둘 다 `proto` 다(`window_title`). 그래야 두 클라의 제목이 같다.
    ///
    /// 같은 제목을 다시 세우지 않는다 — 퍼올리기는 프레임마다 돌고, 매번 세우면 창
    /// 관리자가 그때마다 제목을 다시 그린다.
    ///
    /// ★ 상류에 `set_window_title` 이 **이미 있다**(winit 백엔드 = Windows·Linux).
    /// 로드맵은 "mac·headless 만 있다"고 적어 두고 이 표면을 막힌 것으로 분류했는데,
    /// 그건 틀렸다 — 상류를 손댈 일이 없었다.
    fn refresh_title(&mut self, ctx: &mut ViewContext<Self>) {
        if !self.config.set_titles {
            return;
        }
        let title = self.state.window_title(&self.config.set_titles_string);
        if self.last_title.as_deref() == Some(title.as_str()) {
            return;
        }
        ctx.windows().set_window_title(ctx.window_id(), &title);
        self.last_title = Some(title);
    }

    /// **끌 수 있는 띠**의 높이를 창에게 다시 말한다(`pytmux-1`).
    ///
    /// 머리줄 높이는 글자 배율을 타는데(§10-21ⓐ), 창은 그 배율을 모른다 — 처음 한 번은
    /// `main` 이 말해 주고(`tell_window_the_band`), 그 뒤에 배율이 바뀌면 여기가 말한다.
    /// ⛔ 안 말하면 **보이는 줄과 잡히는 띠가 어긋난다**: 배율을 키우면 줄만 두꺼워지고
    /// 아래쪽 절반은 안 끌리며, 배율을 줄이면 탭바 위쪽이 끌린다(탭이 안 눌린다).
    ///
    /// ⛔ **「창은 한 번 말하면 안 잊는다」를 가정하지 않는다**(pytmux-365 후보 ①).
    ///
    /// 종전에는 `refresh_title` 과 같은 모양이었다 — 우리가 마지막으로 말한 값을 기억해
    /// **그 값이 바뀔 때만** 창을 두드렸다. 그 손은 창이 값을 잊는 경로가 **하나라도**
    /// 있으면 그 뒤로 영영 다시 안 말한다는 뜻이고, 증상은 「머리줄을 끌어도 창이 안
    /// 움직인다」다(전체 화면 왕복 뒤 · 창 자원 재생성 뒤).
    ///
    /// 이제 **창에게 물어본다**. 우리 기억이 아니라 창이 실제로 쓰는 값과 견주므로,
    /// 잊는 경로가 새로 생겨도 다음 프레임에 저절로 되돌아온다 — 가정이 아니라 관측이다.
    /// 비용은 프레임마다 getter 한 번이다.
    fn refresh_titlebar_band(&mut self, ctx: &mut ViewContext<Self>) {
        let band = titlebar::band_height(self.config.font_scale);
        let Some(window) = ctx.windows().platform_window(ctx.window_id()) else {
            return; // 창이 없다 — 다음 프레임에 다시 본다(기억을 굳히지 않는다)
        };
        // 정수 픽셀로 견준다 — 왕복이 부동소수 끝자리를 흔들어도 매 프레임 두드리지
        // 않게(그러면 이 함수가 조용한 프레임을 시끄러운 프레임으로 만든다).
        if (window.titlebar_height() as f32 - band).abs() < 0.5 {
            self.titlebar_band = Some(band);
            return;
        }
        window.set_titlebar_height(band as f64);
        self.titlebar_band = Some(band);
    }

    /// 창 포커스가 바뀌었으면 서버에 알린다(pytmux-421).
    ///
    /// 서버는 이것으로 **포커스 리포트(DECSET 1004)를 켠 패널에만** `ESC[I`/`ESC[O` 를
    /// 쓴다 — 앱은 그 신호로 깜빡임·폴링·자동 새로고침을 멈춘다.
    ///
    /// # 왜 이벤트가 아니라 프레임마다 물어보나
    ///
    /// `refresh_titlebar_band` 와 같은 이유다 — **우리 기억이 아니라 창이 실제로 쓰는
    /// 값**과 견주므로, 포커스 전이를 놓치는 경로가 새로 생겨도 다음 프레임에 저절로
    /// 되돌아온다. 비용은 프레임마다 getter 한 번이고, **바뀔 때만** 프레임을 보낸다.
    ///
    /// 첫 프레임에는 `window_focus` 가 `None` 이라 **지금 상태를 한 번 보낸다** — 서버의
    /// 기본은 「포커스 있음」이므로 포커스 없이 뜬 창이 그 사실을 알릴 길이 그것뿐이다.
    fn refresh_window_focus(&mut self, ctx: &mut ViewContext<Self>) {
        // ⛔ 창 핸들에 물어보지 않는다 — 상류 `platform::Window` 트레이트에는 포커스를
        // 되읽는 자리가 없고, 그것을 더하려면 구현 넷(mac·winit·headless·test)을 다
        // 건드려야 한다. 창 관리자는 이미 답을 안다: `active_window_id` 의 정의가
        // 「타이핑 포커스를 가진 창」이고, 앱 자체가 비활성이면 `None` 이다.
        let on = ctx.windows().active_window() == Some(ctx.window_id());
        if self.window_focus == Some(on) {
            return;
        }
        self.window_focus = Some(on);
        self.pending.push(Outgoing::Focus { on });
    }

    /// 활성 패널 입력칸에 **지금 들어 있는 글**(패리티 G9c). 못 긁으면 빈 문자열.
    ///
    /// 두 뷰가 같은 함수를 부른다(판정은 `proto::prompt_box`) — 여기서 하는 일은 활성
    /// 패널 id 를 찾는 것뿐이다.
    fn current_prompt_text(&self) -> String {
        self.state
            .tabs()
            .active_pane
            .and_then(|pane| self.state.prompt_text(pane))
            .unwrap_or_default()
    }

    /// 작성창을 투입한다 — **프롬프트를 비운 뒤** 새 글을 넣는다(패리티 G9c).
    ///
    /// # 왜 비우나
    ///
    /// 작성창은 입력칸에 있던 글로 시작한다(인계). 비우지 않고 붙이면 그 글이 **두 번**
    /// 들어간다. 파이썬도 같은 순서다(`open_compose` 의 `done` 콜백).
    ///
    /// # 왜 여는 시점이 아니라 지금 비우나
    ///
    /// 사용자 요청(2026-06-22): 열 때 비우면 `Esc` 로 취소했을 때 프롬프트가 **사라진
    /// 채로** 남는다. 투입 직전에 비우면 취소는 아무것도 안 건드린다.
    ///
    /// # 왜 다시 읽나
    ///
    /// 열어 둔 사이에 프롬프트가 변했을 수 있다(원격 제어·자동 재개가 넣은 입력). 지울
    /// 길이는 **지금 화면**을 기준으로 세야 잔여가 안 남는다.
    ///
    /// 백스페이스는 **글자 수**만큼 보낸다(바이트가 아니다) — 한글 한 자에 세 번 보내면
    /// 앞 글자까지 지워진다. 빈 입력칸에서의 추가 백스페이스는 Claude 에서 무동작이다.
    fn clear_prompt_then_paste(&mut self, text: String) {
        let now = self.current_prompt_text();
        if !now.is_empty() {
            self.pending
                .push(Outgoing::Input(vec![0x7f; now.chars().count()]));
        }
        self.pending.push(Outgoing::Command(Command::Paste { text }));
    }

    /// 탭으로 나뉜 정보 팝업(패리티 `InfoTabsScreen`). 내용은 `proto` 가 만든다 —
    /// TUI 와 **같은 줄**이라야 같은 팝업이다.
    ///
    /// # 판의 짜임 — 정본과 같은 넷 (pytmux-373 ⑵)
    ///
    /// **탭줄 + 오른쪽 위 `[x]`** · **고를 수 있는 동작 줄**(`▸ [c] …`) · 내용 줄 ·
    /// **바닥 「닫기」 막대**. 종전에는 가운데 둘이 통째로 없었다 — 닫는 자리도, 동작을
    /// 고르는 자리도 없었고, `[c]`/`[o]` 는 `proto` 가 얹어 준 **흐린 글자 한 줄**이라
    /// 키를 직접 치는 수밖에 없었다. 그래서 꼬리줄의 `↑↓ 항목` 이 거짓이었고, 남는
    /// 자리는 아래가 통째로 비어 보였다(제보의 표 넷째·다섯째 줄).
    ///
    /// # 굴리기는 커서의 **부수 효과**다
    ///
    /// 정본 본문은 `ListView` 라 `↑↓` 가 항목 커서를 옮기고 화면은 따라 굴린다. 여기서도
    /// 커서(`screens.info_row()`)가 예산 밖으로 나가면 그만큼 시작 줄을 민다 — 플러그인
    /// 목록 판이 쓰는 그 처방과 같다. 범위 맞추기는 `settle_info_tabs` 한 곳이 한다.
    fn render_info_tabs(&self, column: Flex) -> Flex {
        let tabs = proto::info::tabs(&self.state, self.link.socket(), self.pinger.now());
        if tabs.is_empty() {
            return column;
        }
        let picked = self.screens.info_tab().min(tabs.len() - 1);
        // 이 탭에서 **할 수 있는 것** — 목록 맨 위에 누를 수 있는 줄로 선다(정본과 같은 차례).
        let actions = proto::info::tab_actions(&self.state, picked);
        let lines = &tabs[picked].1;
        let rows = actions.len() + lines.len();
        let close_on = self.screens.info_close_focused();
        // 커서는 여기서 **자르기만** 한다(안 자르면 없는 줄을 가리킨다) — 자리를 정하는
        // 것은 `settle_info_tabs` 다. 렌더는 `&self` 라 상태를 못 고친다.
        let cursor = self.screens.info_row().min(rows.saturating_sub(1));
        // ★ **진짜 탭**이다(pytmux-9 ⑶). 종전에는 배경 상자를 씌운 글자였고, 제보는
        //   *"TUI 출력을 그대로 가져오고 있는데 진짜 탭으로 그려야 한다"* 였다.
        //   문법은 팔레트 분류탭·크롬 탭바와 **같은 것**을 쓴다(알약 · hover · 클릭) —
        //   같은 뜻의 것이 판마다 다르게 생기면 그것도 갈림이다.
        let mut bar = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_spacing(4.);
        for (i, (title, _)) in tabs.iter().enumerate() {
            let on = i == picked;
            let mut boxed =
                Container::new(self.ui_text((*title).to_owned(), 12., if on {
                    palette::FG
                } else {
                    palette::DIM
                }))
                .with_horizontal_padding(6.)
                .with_vertical_padding(2.)
                .with_corner_radius(theme::TAB_RADIUS);
            if on {
                boxed = boxed.with_background_color(theme::ACTIVE);
            } else if self.panel_hovered(i) {
                boxed = boxed.with_background_color(theme::HOVER);
            }
            bar = bar.with_child(self.clickable_panel(
                i,
                base::PanelTarget::InfoTab(i),
                boxed.finish(),
            ));
        }
        // ★ **클릭 표적 색인은 한 프레임 안에서 안 겹쳐야 한다**(hover 상태 풀의 열쇠다).
        //   탭 `n` 개 → `[x]` 가 `n` → 본문 줄이 `n+1+row` → 바닥 막대가 마지막.
        let close_i = tabs.len();
        let head = Flex::row()
            .with_main_axis_size(MainAxisSize::Max)
            .with_main_axis_alignment(warpui::elements::MainAxisAlignment::SpaceBetween)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_child(bar.finish())
            .with_child(self.clickable_panel(
                close_i,
                base::PanelTarget::InfoClose,
                self.info_close_chip(close_on || self.panel_hovered(close_i)),
            ))
            .finish();
        let mut column =
            column.with_child(Container::new(head).with_padding_bottom(4.).finish());
        // 판이 쓰는 줄은 **탭줄 1 + 본문 예산 + 바닥 막대 1** 이다. 셋을 안 빼면 그만큼
        // 판이 아래로 넘친다(다른 판이 구역 선을 예산에 세는 것과 같은 규율).
        let budget = self.panel_budget().saturating_sub(2);
        // 커서가 예산 밖이면 그만큼 민다 — 이것이 「굴리기」의 전부다.
        let start = (cursor + 1).saturating_sub(budget);
        let body_i = close_i + 1;
        let mut drawn = 0usize;
        for row in start..rows {
            if drawn >= budget {
                break;
            }
            drawn += 1;
            let on = row == cursor && !close_on;
            let child: Box<dyn Element> = match actions.get(row) {
                // 동작 줄 — 정본은 `▸ ` 를 앞에 붙여 목록 맨 위에 **버튼처럼** 둔다.
                // ⚠ 색은 **이 이슈의 몫이 아니다**(pytmux-372 가 팝업 전반의 대비를 든다) —
                //    여기서는 이미 있는 토큰만 쓴다: 바탕보다 가라앉은 판(=단추)과
                //    다른 판이 이미 쓰는 고른 줄 색.
                Some(act) => Container::new(Self::full_row(
                    self.panel_row_box(self.mono_row(&format!("▸ {}", act.label), 13., palette::FG)),
                ))
                .with_background_color(if on { palette::SELECTED_BG } else { theme::HOVER })
                .finish(),
                None => {
                    // ★ **격자에 못박아** 그린다(pytmux-9 ⑵). 이 줄들은 글자 그림이다 —
                    //   RTT 그래프의 세로 막대(`▁▂▃…`)·축(`┤┄─`)이 전부 비 ASCII 라 폴백
                    //   글꼴에서 오고, 그 글꼴의 진폭은 고정폭 글꼴과 다르다. 한 줄을 통짜로
                    //   셰이퍼에 넘기면 칸이 조금씩 밀려 **그래프가 축과 어긋나 보인다**.
                    //   캔버스 줄이 쓰는 규칙(`grid_segments` + 칸너비 못박기)을 그대로 쓴다.
                    //
                    // ⛔ **판의 줄 상자를 지나야 한다**(pytmux-373 ⑴). 맨 `mono_row` 로 두면
                    //    이 줄이 채움 줄(`pad_rows` — 같은 상자를 쓴다)보다 **2px 낮아**,
                    //    줄 수는 예산으로 같은데 픽셀 합이 `(예산 - 그린 줄) × 2` 만큼 달라진다.
                    //    탭마다 줄 수가 다르므로 그것이 곧 **탭을 바꿀 때 판이 들썩이는** 것이다.
                    let boxed = self.panel_row_box(self.mono_row(
                        &lines[row - actions.len()],
                        13.,
                        palette::FG,
                    ));
                    if on {
                        Container::new(Self::full_row(boxed))
                            .with_background_color(palette::SELECTED_BG)
                            .finish()
                    } else {
                        boxed
                    }
                }
            };
            column = column.with_child(self.clickable_panel(
                body_i + row,
                base::PanelTarget::Row(row),
                child,
            ));
        }
        let column = self.pad_rows(column, drawn, budget);
        // 바닥 「닫기」 막대 — 정본 `#itclosebtn` 과 같은 자리다. 위 `[x]` 와 **별개로**
        // 두는 이유도 정본과 같다: 좁은 화면·긴 목록에서 손이 닿는 곳에 하나가 더 있다.
        column.with_child(self.clickable_panel(
            body_i + rows,
            base::PanelTarget::InfoClose,
            self.info_close_bar(close_on || self.panel_hovered(body_i + rows)),
        ))
    }

    /// 판 **가로 가득**을 차지하는 한 줄 — 고른 줄·단추의 배경이 글자 폭에서 끊기지
    /// 않게 한다(정본 `#itbody ListItem Label { width: 1fr }`).
    ///
    /// ⚠ 배경만 넓히는 것이지 **높이는 안 건드린다** — 그 값은 `panel_row_box` 한 곳이
    /// 정한다(pytmux-373 ⑴).
    fn full_row(child: Box<dyn Element>) -> Box<dyn Element> {
        Flex::row()
            .with_main_axis_size(MainAxisSize::Max)
            .with_child(child)
            .finish()
    }

    /// 판 오른쪽 위의 닫기 `[x]`(정본 `#itclose`).
    ///
    /// `hot` 은 **`←→` 포커스가 왔거나 마우스가 올라왔을 때**다 — 정본은 그 둘을 다른
    /// 색으로 가르지만(`-focus` = `$warning`), 우리는 "지금 이걸 누르면 닫힌다"라는 한
    /// 가지 사실만 말하면 되므로 한 색이다.
    fn info_close_chip(&self, hot: bool) -> Box<dyn Element> {
        Container::new(self.ui_text("[x]", 12., if hot { palette::FG } else { theme::ERROR }))
            .with_horizontal_padding(6.)
            .with_vertical_padding(2.)
            .with_corner_radius(theme::TAB_RADIUS)
            .with_background_color(if hot { theme::CLOSE_HOVER } else { theme::HOVER })
            .finish()
    }

    /// 판 바닥의 「닫기」 막대(정본 `#itclosebtn`) — 가로 가득·가운데.
    ///
    /// 한 줄 높이는 다른 줄과 **같은 상자**가 정한다(`panel_row_box`) — 여기만 따로
    /// 지으면 판 높이가 또 줄 수와 갈린다(pytmux-373 ⑴).
    fn info_close_bar(&self, hot: bool) -> Box<dyn Element> {
        Container::new(
            Flex::row()
                .with_main_axis_size(MainAxisSize::Max)
                .with_main_axis_alignment(warpui::elements::MainAxisAlignment::Center)
                .with_child(self.panel_row_box(self.ui_text(t("닫기"), 13., palette::FG)))
                .finish(),
        )
        .with_background_color(if hot { theme::CLOSE_HOVER } else { theme::SURFACE })
        .finish()
    }

    /// 정보 팝업의 **자리를 맞춘다** — `←→` 포커스 접기와 `↑↓` 커서 자르기 둘 다.
    ///
    /// # 왜 뷰가 부르나
    ///
    /// 탭이 몇 개인지(REC 탭은 플러그인이 있을 때만 선다)도, 그 탭에 줄이 몇인지도
    /// **뷰만 안다**. core 는 한 칸씩 올리고 내리기만 하고 범위는 여기서 받는다 —
    /// 플러그인 판이 `set_plugin_grid` 로, 목록 판이 `clamp_selection` 으로 하는 그 규약이다.
    ///
    /// ⛔ 렌더에서 못 한다(`&self`). 그래서 **키를 받은 자리와 클릭을 받은 자리** 둘에서
    /// 부른다 — 그 둘이 이 판의 상태를 바꾸는 전부다.
    fn settle_info_tabs(&mut self) {
        if self.screens.top() != Some(Screen::InfoTabs) {
            return;
        }
        let tabs = proto::info::tabs(&self.state, self.link.socket(), self.pinger.now());
        self.screens.wrap_info_focus(tabs.len());
        let picked = self.screens.info_tab().min(tabs.len().saturating_sub(1));
        let actions = proto::info::tab_actions(&self.state, picked).len();
        let rows = actions + tabs.get(picked).map_or(0, |(_, l)| l.len());
        self.screens.place_info_cursor(actions, rows);
    }

    /// 플러그인 **글 판**의 스크롤을 내용 길이 안으로 되돌린다(pytmux-184 ⑵).
    ///
    /// # 왜 뷰가 부르나
    ///
    /// 위 [`settle_info_tabs`](Self::settle_info_tabs) 와 같은 규약이다 — 몇 줄인지 아는
    /// 것은 그리는 쪽이고, `Screens::press` 의 `Key::Down`/`PageDown` 은 core 라 이 스펙의
    /// 줄 수를 모른다(그래서 상한 없이 더하기만 한다).
    ///
    /// ⛔ **그리는 자리가 이미 자르는데 왜 또 접나.** [`render_plugin_view`]
    /// (Self::render_plugin_view) 의 `min(max_scroll)` 은 **보이는 것**만 자른다 —
    /// 그래서 판이 통째로 비는 증상(제보의 3번째 스크린샷)은 멎었다. 그러나 core 의
    /// `scroll` 은 그대로 자라므로, 끝에서 스무 번 더 누른 뒤의 `↑` 는 **스무 번
    /// 헛돈다**: 그림은 안 움직이는데 키는 먹은 것이라, 사용자에게는 "이 판은 위로 안
    /// 간다"로 보인다(`settle_settings_cursor` 가 적어 둔 것과 같은 증상이다).
    ///
    /// ⚠ 목록형(`is_list`)은 자리를 `selected` 가 들고 `settle_settings_cursor` 계열이
    /// 접는다 — 여기서는 글 판만 본다.
    fn settle_plugin_scroll(&mut self) {
        if self.screens.top() != Some(Screen::PluginView) {
            return;
        }
        let budget = self.panel_budget();
        let Some(lines) = self.state.plugin_screen().and_then(|spec| {
            (spec.kind == "text")
                .then(|| Self::join_sections(&spec.say_sections()).lines().count())
        }) else {
            return;
        };
        self.screens.clamp_scroll(lines.saturating_sub(budget));
    }

    /// 플러그인 **목록 판**의 커서를 줄 수 안으로 되돌린다(pytmux-432).
    ///
    /// # 이 자리가 비어 있었다
    ///
    /// 같은 규약의 형제가 셋인데(`settle_info_tabs` · `settle_settings_cursor` ·
    /// `settle_plugin_scroll`) **어느 것도 `Screen::PluginView` 의 목록을 안 봤다**:
    /// 윗형은 `Settings`·`Plugins`·`Cursor` 만 보고, 바로 위 글 판 형제는 제 문서에
    /// *"목록형은 `settle_settings_cursor` 계열이 접는다"* 고 적었는데 **그쪽은 이 화면을
    /// 안 받는다.** 그래서 `press_list` 의 `Down`·`PageDown`·`End` 가 두고 간 자리를
    /// 아무도 안 접었다.
    ///
    /// ☠ 특히 `End` 는 `usize::MAX` 를 둔다 — *"끝이 몇 번째인지는 뷰가 안다"* 는 규약
    /// 위에 선 값인데(pytmux-417 ①) 그 뷰가 없었다. 실측(2026-09-01 · macOS · 실 GUI ·
    /// `:usage-panel`): `End` 뒤 `↑` 를 **세 번** 눌러도 커서가 마지막 줄에 그대로 있다.
    /// 되돌리려면 10^19 번을 눌러야 한다.
    ///
    /// ⛔ **그리는 쪽이 이미 `min(rows-1)` 로 자르는데 왜 또 접나** — 그 자름은 형제
    /// 문서들이 적어 둔 그대로 **보이는 것**만 자른다. core 의 `selected` 는 그대로
    /// 자라므로 사용자에게는 "이 판은 위로 안 간다"로 보인다.
    ///
    /// ⚠ 목록·표·판(`list`·`table`·`panel`) 모두 자리를 `spec.rows` 로 세므로 한 줄로 접는다.
    /// 글 판(`text`)은 `rows` 가 비어 여기서 일찍 돌아간다 — 그쪽은 위 형제가 본다.
    fn settle_plugin_cursor(&mut self) {
        if self.screens.top() != Some(Screen::PluginView) {
            return;
        }
        let Some(rows) = self.state.plugin_screen().map(|spec| spec.rows.len()) else {
            return;
        };
        if rows == 0 {
            return;
        }
        self.screens.clamp_selection(rows);
    }

    /// 설정·플러그인 판의 커서를 **줄 수 안으로** 되돌린다(pytmux-374 ⑶).
    ///
    /// # 왜 뷰가 부르나
    ///
    /// 위 [`settle_info_tabs`](Self::settle_info_tabs) 와 같은 규약이다 — 끝이 몇
    /// 번째인지 아는 것은 그리는 쪽이다. 설정 줄 수는 core 도 알지만(`settings_len`)
    /// **플러그인 관리자의 목록은 서버가 나르고 core 는 안 든다**(`state.plugins()`), 그래서
    /// 두 판이 한 `press_settings` 를 쓰는 한 자르는 자리는 여기 하나라야 한다.
    ///
    /// ⛔ **`End` 만의 일이 아니다** — `Down`·`PageDown` 도 위쪽 상한을 안 걸고 지나므로
    /// 안 접으면 아래에서 넘긴 만큼 `Up` 이 **헛돈다**(그림은 그대로인데 키가 안 먹는
    /// 것으로 보인다). 종전에는 그 넷이 전부 판을 닫아 이 자리가 드러날 일이 없었다.
    /// `debug-stats` 판이 보일 값을 **이 뷰에서** 모은다(pytmux-457).
    ///
    /// # 왜 두 칸이 늘 «못 쟀다» 인가
    ///
    /// 글리프 캐시(`warpui_core::fonts::Cache`)와 씬 원소(`Scene`)는 **상류 스냅샷의
    /// 사유 필드**라 크기를 내주는 길이 없다(`glyphs_by_char` 등은 비공개 `DashMap`).
    /// 그것을 재려면 상류를 고쳐야 하고, 그러면 MIT 경계 문서(`PROVENANCE.md`)가 관리하는
    /// 스냅샷에 드리프트가 생긴다 — 진단 한 줄의 값보다 그 비용이 크다.
    /// ⛔ 그래서 **0 으로 적지 않고 「못 쟀다」로 남긴다**: 0 은 「캐시가 비었다」로
    /// 읽히고, 그건 우리가 모르는 사실이다(`base::diag` 의 그 규율).
    /// 대신 우리가 실제로 하는 그리기 일의 크기는 [`Self::painted_cells`] 가 잰다.
    fn runtime_stats(&self) -> base::diag::RuntimeStats {
        let layout = self.state.layout();
        base::diag::RuntimeStats {
            pid: std::process::id(),
            frames: self.frames.get(),
            frame_ms: self.frame_ms.borrow().iter().copied().collect(),
            // 위 머리말 — 상류가 크기를 안 내준다.
            glyph_cache: None,
            scene_nodes: None,
            painted_cells: Some(self.painted_cells.get()),
            queue_depth: self.pending.len(),
            rtt_ms: self.state.rtt().last.map(|s| s * 1000.0),
            rss: Self::rss_bytes(),
            grid: layout.map(|l| (l.cols, l.rows)).unwrap_or((0, 0)),
            tabs: self.state.tabs().tabs.len(),
            panes: layout.map(|l| l.panes.len()).unwrap_or(0),
            screen_depth: self.screens.depth(),
        }
    }

    /// 상주 메모리(바이트). **못 알아내는 OS 에서는 `None`** — 추측하지 않는다.
    ///
    /// 리눅스는 `/proc/self/statm` 한 줄로 끝난다(둘째 칸 = 상주 페이지 수). macOS 는
    /// `task_info` 를, Windows 는 `GetProcessMemoryInfo` 를 부르는 일이라 이 크레이트의
    /// 의존을 늘려야 해서 안 했다 — 이슈가 「메모리(**가능한 OS 만**)」로 적은 자리다.
    fn rss_bytes() -> Option<u64> {
        #[cfg(target_os = "linux")]
        {
            let text = std::fs::read_to_string("/proc/self/statm").ok()?;
            let pages: u64 = text.split_whitespace().nth(1)?.parse().ok()?;
            // `sysconf(_SC_PAGESIZE)` 를 부를 libc 가 없다 — 리눅스에서 4KiB 가 아닌
            // 페이지는 드물고, 틀려도 「자라나」의 판정은 안 바뀐다(그 사실을 적어 둔다).
            Some(pages * 4096)
        }
        #[cfg(not(target_os = "linux"))]
        {
            None
        }
    }

    fn settle_settings_cursor(&mut self) {
        let rows = match self.screens.top() {
            Some(Screen::Settings) => self.screens.plugins().settings_len(),
            Some(Screen::Plugins) => self.state.plugins().len(),
            // 커서 판은 줄 수가 **정적**이다(설정 다섯 · pytmux-375) — 그래도 접는 자리는
            // 여기 하나라야 한다(세 판이 한 `press_settings` 를 쓴다 · 위 머리말).
            Some(Screen::Cursor) => base::config::CURSOR_SETTINGS.len(),
            _ => return,
        };
        self.screens.clamp_selection(rows);
    }

    /// 정보 팝업에서 **고른 줄을 실행한다**(`Enter`·클릭 · 정본 `_run_action` 동형).
    ///
    /// 동작 줄이면 그 동작을 돌리고 **판은 그대로 둔다**(결과를 그 자리에서 봐야 한다).
    /// 그냥 내용 줄이면 정본과 같이 닫는다.
    fn run_info_row(&mut self) {
        let tabs = proto::info::tabs(&self.state, self.link.socket(), self.pinger.now());
        let picked = self.screens.info_tab().min(tabs.len().saturating_sub(1));
        let actions = proto::info::tab_actions(&self.state, picked);
        match actions.get(self.screens.info_row()) {
            Some(act) => self.run_info_action(act.key),
            None => {
                self.screens.close_top();
            }
        }
    }

    /// 동작 하나를 키로 돌린다 — **핫키와 항목 클릭이 같은 자리를 지난다**.
    ///
    /// ⛔ 두 길이 각자 적으면 하나만 고쳐진다(이 판에서 실제로 그랬다: `[c]`/`[o]` 는
    /// 키에만 있고 목록에는 없었다).
    fn run_info_action(&mut self, key: char) {
        match key {
            'c' => self.pending.push(Outgoing::Command(Command::SetCapture)),
            'o' => {
                proto::info::open_capture_dir(&self.state.flags().capture_path);
            }
            _ => {}
        }
    }

    /// 여러 줄 작성창(패리티 `e_ins` · `ComposePromptScreen`). TUI 와 같은 그림이다.
    ///
    /// 선택과 커서를 **배경색**으로 칠한다(TUI 는 반전). 글자 단위로 배경을 다르게 하려면
    /// 칸마다 요소를 만들어야 하는데, 작성창 한 줄은 길어야 수십 글자라 감당할 만하다.
    fn render_compose(&self, column: Flex) -> Flex {
        let Some(editor) = self.screens.editor() else {
            return column;
        };
        let selection = editor.selection();
        let (crow, ccol) = editor.cursor();
        let mut column = column;
        for (row, line) in editor.lines().iter().enumerate() {
            let chars: Vec<char> = line.chars().collect();
            let mut cells = Flex::row();
            // 이 줄에 칸을 하나라도 놓았나 — 빈 줄이 사라지는 것을 막는다(§10-21ⓒ2).
            let mut drew = false;
            // 커서는 **줄 끝에도** 선다 — 칠할 글자가 없으므로 한 칸을 덧댄다.
            for col in 0..=chars.len() {
                let glyph = chars.get(col).copied().unwrap_or(' ');
                let selected = selection
                    .is_some_and(|(start, end)| (row, col) >= start && (row, col) < end);
                let cursor = (row, col) == (crow, ccol);
                if col == chars.len() && !cursor && !selected {
                    continue;
                }
                let label = self.text(glyph.to_string(), 14., palette::FG);
                cells = cells.with_child(if cursor || selected {
                    Container::new(label)
                        .with_background_color(palette::SELECTED_BG)
                        .finish()
                } else {
                    Container::new(label).finish()
                });
                drew = true;
            }
            // ★ **빈 줄도 한 칸을 놓는다**(§10-21ⓒ2 — 제보: "`Shift`+`Enter` 로 빈 줄을
            //   연달아 넣어도 연속으로 안 보인다").
            //
            //   위 루프는 빈 줄에서 `col == 0 == chars.len()` 한 번 돌고 그 자리에서
            //   걸러진다(커서도 선택도 없으면). 그러면 이 행 상자에 **자식이 하나도
            //   없어 높이가 0** 이고, 화면에서 줄이 통째로 사라진다 — 커서가 놓인 빈
            //   줄만 보이던 것이 그 증거였다.
            //
            //   ⚠ 이 공백은 **그림뿐**이다. 작성창의 글은 `editor.lines()` 가 쥐고
            //   있고 복사·전송은 거기서 가므로, 여기 놓은 칸이 내용에 새지 않는다.
            if !drew {
                cells = cells.with_child(Container::new(self.text(" ", 14., palette::FG)).finish());
            }
            // 입력기 배지는 **커서가 있는 줄**의 오른쪽 끝이다(pytmux-14) — 작성창은
            // 여러 줄이라 "입력줄"이 곧 커서 줄이고, 캔버스 쪽 규칙(커서 줄)과 같다.
            let line = Box::new(cells) as Box<dyn Element>;
            column = column.with_child(if row == crow { self.input_line(line) } else { line });
        }
        column
    }

    /// 설정 화면에 적을 지금 값들(패리티 G5b). TUI 와 같은 표다.
    ///
    /// 서버 플래그는 `state` 가, prefix 는 `mode` 가 쥐고 있다 — 값의 출처가 둘이라 여기서
    /// 모은다(core 는 proto 를 모르므로 저쪽에서 모을 수 없다).
    fn setting_values(&self) -> base::config::SettingValues {
        let flags = self.state.flags();
        base::config::SettingValues {
            inactive_dim: self.config.inactive_dim,
            border_status: flags.border_status,
            single_border: flags.single_border,
            coalesce_repaints: flags.coalesce_repaints,
            nest_auto_attach: flags.nest_auto_attach,
            win_mouse_motion: flags.win_mouse_motion,
            exit_empty: flags.exit_empty,
            mouse: self.config.mouse,
            mouse_drag_copy: self.config.mouse_drag_copy,
            tab_bar_always: self.config.tab_bar_always,
            default_path: self.config.default_path.clone(),
            claude_command: self.config.claude_command.clone(),
            strip_box_drawing: self.config.strip_box_drawing,
            copy_unwrap: self.config.copy_unwrap,
            set_clipboard: self.config.set_clipboard,
            alt_scroll: self.config.alt_scroll,
            set_titles: self.config.set_titles,
            set_titles_string: self.config.set_titles_string.clone(),
            inactive_dim_ratio: self.config.inactive_dim_ratio,
            font_scale: self.config.font_scale,
            font_family: self.config.font_family.clone(),
            cursor_style: self.config.cursor_style.clone(),
            cursor_color: self.config.cursor_color.clone(),
            cursor_blink: self.config.cursor_blink,
            cursor_blink_ms: self.config.cursor_blink_ms,
            cursor_thickness: self.config.cursor_thickness,
            mode_keys: self.config.mode_keys.clone(),
            status_left: self.config.status_left.clone(),
            status_right: self.config.status_right.clone(),
            status_bg: self.config.status_bg.clone(),
            status_fg: self.config.status_fg.clone(),
            status_position: self.config.status_position.clone(),
            remote_title: self.config.remote_title.clone(),
            status_interval: self.config.status_interval,
            mouse_drag_threshold: self.config.mouse_drag_threshold,
            ambiguous_width: self.config.ambiguous_width.clone(),
            vt_parser: flags.vt_parser.clone(),
            window_size: flags.window_size.clone(),
            sync: flags.sync,
            monitor_activity: flags.monitor_activity,
            monitor_bell: flags.monitor_bell,
            auto_rename: flags.auto_rename,
            prefix: self.mode.prefix(),
        }
    }


    /// core 가 고른 설정 변경을 실제로 적용한다.
    ///
    /// ★ **키와 마우스가 이 함수 하나로 만난다**(§10-21ⓣ). 값칸 클릭이 생기면서 진입이
    /// 둘이 됐는데, 적용을 두 곳에 적으면 그 둘은 반드시 다르게 낡는다 — 실제로 이
    /// 저장소가 두 번 만든 갈라짐이다. 무엇을 할지는 core 가 정하고
    /// (`setting_pick_dir` · `setting_pick_at`), 여기는 그것을 집행만 한다.
    fn apply_setting_pick(&mut self, pick: Option<base::config::SettingPick>) {
        use base::config::SettingPick;
        match pick {
            Some(SettingPick::Act(action)) => {
                self.apply_action(action);
            }
            Some(SettingPick::Ask(prompt, seed)) => self.screens.ask(prompt, &seed),
            Some(SettingPick::Flip(key)) => self.flip_config(key),
            Some(SettingPick::Set(key, value)) => self.set_config(key, value),
            Some(SettingPick::SetNumber(key, value)) => self.set_number(key, value),
            None => {}
        }
    }

    /// 설정 파일이 쥔 on/off 하나를 뒤집는다(패리티 G6b). 두 뷰가 같은 표다.
    fn flip_config(&mut self, key: &str) {
        let Some((next, written)) = base::config::flip_config(key, &self.config)
        else {
            return;
        };
        self.config = next;
        // 이번 판에 바로 먹는다 — 설정을 바꾸고 재시작해야 한다면 그 화면은 반쪽이다.
        self.state
            .set_inactive_dim(self.config.inactive_dim, self.config.inactive_dim_ratio);
        if let Err(err) = written {
            self.state
                .note_error(tf("설정을 저장하지 못했다: {err}", &[("err", &err.to_string())]));
        }
    }


    /// 새 탭·분할이 시작할 자리(와 Claude 탭이 실행할 명령)를 **설정값으로** 바꾼다.
    ///
    /// 규칙 자체는 `proto` 가 든다([`proto::command::with_config_paths`]) — 뷰에 두면
    /// 뷰가 늘 때마다 각자 적게 되고, 갈리면 같은 키가 클라마다 다른 디렉토리에서 뜬다.
    /// 여기 남는 것은 **설정을 꺼내 넘기는 한 줄**뿐이다.
    fn with_default_path(&self, command: Command) -> Command {
        proto::command::with_config_paths(
            command,
            &self.config.default_path,
            &self.config.claude_command,
        )
    }


    /// `bind-key`/`unbind-key` 한 줄을 반영한다(패리티 G8j). 두 뷰가 같은 표다.
    ///
    /// 파일에 쓰고 **다시 읽는다** — `set` 과 같은 이유로 파싱 규칙을 한 곳에 둔다.
    fn apply_bind_line(&mut self, spec: &str, bind: bool) {
        let spec = spec.trim();
        if spec.is_empty() {
            return;
        }
        let written = if bind {
            base::config::write_bind(&format!("bind {spec}"))
        } else {
            base::config::erase_bind(spec)
        };
        match written {
            Ok(_) => {
                self.config = base::Config::load();
                self.state.note_notice(if bind {
                    format!("bind {spec}")
                } else {
                    format!("unbind {spec}")
                });
            }
            Err(err) => self
                .state
                .note_error(tf("바인딩을 저장하지 못했다: {err}", &[("err", &err.to_string())])),
        }
    }
    /// `set <옵션> <값>` 한 줄을 반영한다(패리티 G8i). 두 뷰가 같은 표다.
    ///
    /// **설정 파일에 쓰는 길로만** 보낸다 — 서버 옵션은 이름이 다르고(`set_sync` 등)
    /// 여기서 갈라 주기 시작하면 두 어휘가 섞인다. 서버 것은 설정 화면·팔레트가 입구다.
    fn apply_set_line(&mut self, line: &str) {
        let mut parts = line.trim().splitn(2, char::is_whitespace);
        let (Some(option), Some(value)) = (parts.next(), parts.next()) else {
            self.state
                .note_error(tf("설정 형식이 아니다: {line}", &[("line", line)]));
            return;
        };
        let value = value.trim();
        // 파일에 쓰고 **다시 읽는다** — 파싱 규칙이 한 곳(Config::parse)에만 있어야
        // "파일로 적은 것"과 "여기서 적은 것"이 갈리지 않는다.
        match base::Config::write_option(option, value) {
            Ok(_) => {
                self.config = base::Config::load();
                self.mode.set_prefix(self.config.prefix);
                self.state
                    .set_inactive_dim(self.config.inactive_dim, self.config.inactive_dim_ratio);
                        self.state.note_notice(format!("set {option} {value}"));
            }
            Err(err) => self
                .state
                .note_error(tf("설정을 저장하지 못했다: {err}", &[("err", &err.to_string())])),
        }
    }

    /// 셸 명령을 **스레드에서** 돌린다(패리티 G8p). 두 뷰가 같은 표다.
    fn spawn_shell(&mut self, cmd: String, then: Option<String>, otherwise: Option<String>) {
        if cmd.trim().is_empty() {
            return;
        }
        let slot = self.shell_result.clone();
        // 결과는 다음 tick 이 줍는다 — 뷰를 스레드에서 만지지 않는다.
        std::thread::spawn(move || {
            let (code, text) = clip::run_shell(&cmd);
            if let Ok(mut slot) = slot.lock() {
                *slot = Some(ShellOutcome { code, text, then, otherwise });
            }
        });
    }

    /// 도착한 셸 결과를 반영한다. `(다시 그릴까, 이어서 부를 액션)`.
    ///
    /// 액션을 **여기서 안 부르는** 이유: `if-shell` 의 뒤 명령은 확인 화면을 세울 수도
    /// 있어 뷰 문맥이 필요한데, 이 함수는 주기 갱신에서 불린다. 할 일만 돌려주고
    /// 실행은 문맥이 있는 호출부가 한다.
    fn take_shell_result(&mut self) -> (bool, Option<base::Action>) {
        let Some(outcome) = self.shell_result.lock().ok().and_then(|mut s| s.take()) else {
            return (false, None);
        };
        match outcome.then {
            Some(name) => {
                // 성공이면 then, 실패면 otherwise(있을 때) — 파이썬 `_if_shell` 그대로.
                let picked = if outcome.code == 0 {
                    Some(name)
                } else if let Some(otherwise) = outcome.otherwise {
                    Some(otherwise)
                } else {
                    self.state
                        .note_notice(tf(
                            "조건이 실패했다(코드 {code})",
                            &[("code", &outcome.code.to_string())],
                        ));
                    return (true, None);
                };
                if let Some(name) = picked {
                    match base::PALETTE.iter().find(|e| e.name == name) {
                        Some(entry) => return (true, Some(entry.action)),
                        None => self
                            .state
                            .note_error(tf("모르는 명령이다: {name}", &[("name", &name)])),
                    }
                }
            }
            None => {
                // 출력이 비면 화면을 안 띄운다 — 빈 상자는 "안 됐다"로 읽힌다.
                if outcome.text.trim().is_empty() {
                    self.state
                        .note_notice(tf(
                            "끝났다(코드 {code})",
                            &[("code", &outcome.code.to_string())],
                        ));
                } else {
                    // 결과를 **서버 버퍼에도** 넣는다(파이썬 `set_buffer` — 붙여넣기로
                    // 바로 쓸 수 있고, 다른 클라에서도 보인다).
                    self.pending.push(Outgoing::Command(Command::SetBuffer {
                        text: outcome.text.clone(),
                    }));
                    self.state.set_shell_output(&outcome.text);
                    self.screens.open(Screen::ShellOutput);
                }
            }
        }
        (true, None)
    }

    /// 설정 파일의 숫자 옵션 하나를 놓는다(패리티 G8h). 두 뷰가 같은 표다.
    fn set_number(&mut self, key: &str, value: f32) {
        let Some((next, written)) = base::config::set_number(key, value, &self.config)
        else {
            return;
        };
        self.config = next;
        // 딤 세기는 **바로** 화면에 먹어야 한다 — 숫자를 올리는 사람은 그 변화를 보려고
        // 누른다.
        self.state
            .set_inactive_dim(self.config.inactive_dim, self.config.inactive_dim_ratio);
        if let Err(err) = written {
            self.state
                .note_error(tf("설정을 저장하지 못했다: {err}", &[("err", &err.to_string())]));
        }
    }

    /// 설정 파일의 옵션 하나를 **주어진 값으로** 놓는다(패리티 G8g). 두 뷰가 같은 표다.
    fn set_config(&mut self, key: &str, value: &str) {
        let Some((next, written)) = base::config::set_config(key, value, &self.config)
        else {
            return;
        };
        self.config = next;
        proto::compose::set_ambiguous_wide(self.config.ambiguous_width == "wide");
        if let Err(err) = written {
            self.state
                .note_error(tf("설정을 저장하지 못했다: {err}", &[("err", &err.to_string())]));
        }
    }

    /// 설정 화면에서 받은 새 prefix 를 반영한다(패리티 G5b). TUI 와 같은 표다.
    ///
    /// 저장이 실패해도 **이번 판에는 적용한다** — 방금 바꾼 것이 즉시 안 먹는 쪽이 더
    /// 놀랍다. 대신 실패를 조용히 넘기지 않는다(조용히 넘기면 다음 기동에 값이 사라진
    /// 이유를 알 수 없다 — 파이썬도 같은 이유로 오류 등급으로 띄운다).
    fn apply_prefix_answer(&mut self, answer: &str) {
        match base::config::set_prefix(answer) {
            Some((prefix, written)) => {
                self.mode.set_prefix(prefix);
                if let Err(err) = written {
                    self.state
                        .note_error(tf("설정을 저장하지 못했다: {err}", &[("err", &err.to_string())]));
                }
            }
            None => self
                .state
                .note_error(tf("읽을 수 없는 키 표기: {answer}", &[("answer", answer)])),
        }
    }

    /// 고른 줄의 플러그인을 뒤집는 명령(패리티 G7). TUI 와 같은 표다.
    ///
    /// **낙관적으로 안 고친다** — 화면은 서버가 다음 `status` 로 알려 주는 값을 그린다.
    /// 우리가 먼저 뒤집으면 서버가 거절했을 때(그런 이름이 없다 등) 화면만 틀어진다.
    fn plugin_toggle(&self, row: usize) -> Option<Command> {
        let plugin = self.state.plugins().get(row)?;
        Some(Command::SetPluginEnabled {
            name: plugin.name.clone(),
            on: !plugin.enabled,
        })
    }

    /// 원격 탭 머지 피커(패리티 G8n).
    fn render_merge_remote(&self, column: Flex) -> Flex {
        let items = self.state.merge_candidates();
        let selected = self.screens.selected().min(items.len().saturating_sub(1));
        let dir = if self.screens.merge_horizontal() { t("좌우 │") } else { t("상하 ─") };
        let mut column = column.with_child(self.text(
            tf("분할 방향: {dir}", &[("dir", dir)]),
            12.,
            palette::DIM,
        ));
        for (row, (_, label)) in items.iter().enumerate() {
            let text = self.text(label.clone(), 13., palette::FG);
            column = column.with_child(if row == selected {
                Container::new(text)
                    .with_background_color(palette::SELECTED_BG)
                    .with_uniform_padding(1.)
                    .finish()
            } else {
                Container::new(text).with_uniform_padding(1.).finish()
            });
        }
        column
    }

    /// 레이아웃 프리셋 목록(패리티 G8d). 문구는 파이썬 것 그대로다.
    fn render_layouts(&self, column: Flex) -> Flex {
        let presets = base::LAYOUT_PRESETS;
        let selected = self.screens.selected().min(presets.len() - 1);
        let mut column = column;
        for (row, (label, _)) in presets.iter().enumerate() {
            let text = self.text(t(label), 13., palette::FG);
            column = column.with_child(if row == selected {
                Container::new(text)
                    .with_background_color(palette::SELECTED_BG)
                    .with_uniform_padding(1.)
                    .finish()
            } else {
                Container::new(text).with_uniform_padding(1.).finish()
            });
        }
        column
    }

    /// 알림 이력(패리티 G6c). 새것이 위다.
    fn render_notices(&self, column: Flex) -> Flex {
        if self.state.notices().len() == 0 {
            return column.with_child(self.text(t("아직 알림이 없다"), 13., palette::DIM));
        }
        let budget = self.panel_budget();
        let mut column = column;
        let mut drawn = 0usize;
        for notice in self
            .state
            .notices()
            .skip(self.screens.scroll())
            .take(budget.saturating_sub(1))
        {
            drawn += 1;
            let color = match notice.severity {
                proto::session::Severity::Error => theme::ERROR,
                proto::session::Severity::Warn => theme::WARN,
                proto::session::Severity::Ok => theme::OK,
                proto::session::Severity::Info => palette::FG,
            };
            // 채움 줄과 **같은 상자**다(pytmux-373 ⑴) — 안 씌우면 알림이 하나 늘 때마다
            // 판이 2px 씩 자란다.
            column = column.with_child(self.panel_row_box(self.text(notice.line(), 13., color)));
        }
        // ⓥ — 끝에 가까워져 남은 줄이 적어도 판은 그대로다.
        self.pad_rows(column, drawn, budget.saturating_sub(1))
    }

    /// F10 메뉴(패리티 G1d · 계층은 레이아웃 맞추기 ⑪). 파이썬 `MENU_ITEMS` 와 **같은
    /// 문구**이고, 늘어놓는 차례는 `MENU_TOPLEVEL`/`MENU_GROUPS` 다.
    fn render_menu(&self, column: Flex) -> Flex {
        use base::MenuRow;
        let rows = self.screens.menu_rows();
        let selected = self.screens.selected().min(rows.len().saturating_sub(1));
        let toggles = self.menu_toggles();
        let budget = self.panel_budget();
        let mut column = column;
        // 서브메뉴에 있으면 **어디에 들어와 있는지**를 머리줄로 준다 — 없으면 열 줄짜리
        // 목록이 갑자기 다른 열 줄로 바뀐 것처럼 보인다.
        if let Some(group) = self.screens.menu_group() {
            column = column.with_child(self.ui_text(
                format!("← {}", base::menu_group_label(group)),
                12.,
                palette::DIM,
            ));
        }
        for (row, entry) in rows.iter().enumerate().take(budget.saturating_sub(1)) {
            let child: Box<dyn Element> = match entry {
                // 구분선은 고를 수 없다 — **파괴적 동작을 손가락에서 떼어 놓는 자리**다.
                MenuRow::Separator => Container::new(
                    Flex::row()
                        .with_child(self.text("─".repeat(24), 11., palette::DIM))
                        .finish(),
                )
                .with_uniform_padding(1.)
                .finish(),
                MenuRow::Group(group) => {
                    // `›` 가 "여기서 더 들어간다"의 표식이다(고르면 실행이 아니라 진입).
                    let label = self.text(
                        format!("{}  ›", base::menu_group_label(group)),
                        13.,
                        palette::FG,
                    );
                    self.menu_box(label, row == selected, self.panel_hovered(row))
                }
                MenuRow::Item(entry) => {
                    // 토글은 지금 값을 옆에 단다(정본 `●`/`○`) — 누르기 전에 어느 쪽인지
                    // 알아야 한다.
                    let label = match base::menu_toggle_mark(entry.key, &toggles) {
                        Some(mark) => format!("{}  {mark}", t(entry.label)),
                        None => t(entry.label).to_owned(),
                    };
                    self.menu_box(
                        self.text(label, 13., palette::FG),
                        row == selected,
                        self.panel_hovered(row),
                    )
                }
                // 플러그인이 낸 줄 — **문구의 주인이 플러그인**이라 서버가 준 글을 쓴다
                // (정적 표에 옮겨 적으면 플러그인이 문구를 고쳐도 우리는 옛 글을 보인다).
                MenuRow::Plugin(i) => {
                    let label = self
                        .screens
                        .plugins()
                        .menu_items
                        .get(*i)
                        .map(|item| t(&item.label).to_owned())
                        .unwrap_or_default();
                    self.menu_box(
                        self.text(label, 13., palette::FG),
                        row == selected,
                        self.panel_hovered(row),
                    )
                }
            };
            // 구분선은 고를 수 없으니 클릭 대상도 아니다 — 감싸면 hover 만 뜨고 아무
            // 일도 안 일어나는 줄이 생긴다.
            column = column.with_child(if entry.selectable() {
                self.clickable_panel(row, base::PanelTarget::Row(row), child)
            } else {
                child
            });
        }
        column
    }

    /// 메뉴 토글 다섯의 지금 값 — 서버 status + 활성 탭에서 모은다.
    fn menu_toggles(&self) -> base::MenuToggles {
        let flags = self.state.flags();
        let tabs = self.state.tabs();
        base::MenuToggles {
            zoom: flags.zoomed,
            sync: flags.sync,
            autoresume: flags.autoresume,
            prompt_clear: flags.prompt_clear,
            toggle_pin: tabs.tabs.iter().find(|t| t.active).is_some_and(|t| t.pinned),
        }
    }

    /// 메뉴 한 줄의 상자(고른 줄만 배경 강조 — 목록들과 같은 문법).
    fn menu_box(&self, label: Box<dyn Element>, selected: bool, hovered: bool) -> Box<dyn Element> {
        let boxed = Container::new(label).with_uniform_padding(1.);
        if selected {
            boxed.with_background_color(palette::SELECTED_BG).finish()
        } else if hovered {
            boxed.with_background_color(theme::HOVER).finish()
        } else {
            boxed.finish()
        }
    }

    /// 플러그인 관리 화면(패리티 G7).
    fn render_plugins(&self, column: Flex) -> Flex {
        let plugins = self.state.plugins();
        if plugins.is_empty() {
            // full status 를 아직 못 받았거나 서버가 옛 버전이다 — 빈 목록을 말없이
            // 보이면 "플러그인이 하나도 없다"로 읽힌다.
            return column.with_child(self.text(
                t("서버가 플러그인 목록을 안 보냈다 (옛 서버이거나 아직 첫 full status 전)"),
                13.,
                palette::DIM,
            ));
        }
        let selected = self.screens.selected().min(plugins.len() - 1);
        let mut column = column;
        for (row, plugin) in plugins.iter().enumerate() {
            let box_mark = if plugin.enabled { "[x]" } else { "[ ]" };
            let text = format!(
                "{box_mark} {:<26}{}",
                plugin.name,
                if plugin.description.is_empty() {
                    String::new()
                } else {
                    format!("— {}", plugin.description)
                }
            );
            let fg = if plugin.enabled { palette::FG } else { palette::DIM };
            let label = self.text(text, 13., fg);
            column = column.with_child(if row == selected {
                Container::new(label)
                    .with_background_color(palette::SELECTED_BG)
                    .with_uniform_padding(1.)
                    .finish()
            } else {
                Container::new(label).with_uniform_padding(1.).finish()
            });
        }
        column
    }

    /// 설정 화면 **왼쪽 세로 카테고리 탭**(레이아웃 맞추기 ⑨ — 정본 `SettingsScreen` 의
    /// 좌측 탭). 지금 줄이 속한 카테고리가 활성이고, `Tab`/`Shift+Tab` 이 그 사이를 돈다.
    ///
    /// 왜 목록 위 머리줄만으로는 모자란가: 34줄은 한 화면에 안 들어가 **지금 어디쯤인지**가
    /// 스크롤 밖으로 밀린다. 옆에 늘 붙은 탭줄은 그 자리를 잃지 않는다.
    fn settings_sidebar(&self, active: Option<usize>, ids: &mut PanelIds) -> Box<dyn Element> {
        let mut side = Flex::column().with_main_axis_size(MainAxisSize::Min).with_spacing(2.);
        // 코어 분류 뒤에 **플러그인이 낸 분류**(`Claude`)가 이어진다 — 정본과 같은 차례.
        let cats = self.screens.plugins().setting_cats();
        for (i, cat) in cats.iter().enumerate() {
            let id = ids.next();
            let on = active == Some(i);
            let label = self.ui_text(
                base::config::settings_cat_label(cat),
                12.,
                if on { palette::FG } else { palette::DIM },
            );
            let mut boxed = Container::new(label)
                .with_horizontal_padding(8.)
                .with_vertical_padding(3.)
                .with_corner_radius(theme::TAB_RADIUS);
            if on {
                boxed = boxed.with_background_color(theme::ACTIVE);
            } else if self.panel_hovered(id) {
                boxed = boxed.with_background_color(theme::HOVER);
            }
            side = side.with_child(self.clickable_panel(
                id,
                base::PanelTarget::SettingsCat(i),
                boxed.finish(),
            ));
        }
        Container::new(side.finish())
            .with_padding_right(10.)
            .with_border(Border::right(1.).with_border_color(theme::BORDER))
            .finish()
    }

    /// 설정 값 한 칸을 그린다 — 정본은 고를 수 있는 것을 **전부** 늘어놓고 지금 값만
    /// 강조한다. 지금 값만 적으면 그 줄이 무엇을 받는지는 눌러 봐야 안다.
    ///
    /// 켜기/끄기만 네이티브 토글 그림으로 남긴다(N5) — 스위치는 두 상태가 그림에
    /// 이미 들어 있어, 낱말 둘을 늘어놓는 것보다 네이티브 화면에서 읽기 쉽다.
    /// 셋 이상은 그 그림이 없으므로 **분절 알약**으로 편다.
    /// ★ §10-21ⓣ — **값칸은 제자리에서 조작된다**(제보의 핵심).
    ///
    /// 종전에는 이 함수가 **그리기만** 했다. 줄만 눌리게 해도 값은 여전히 키로 바꿔야
    /// 하므로, 제보가 말한 "특히 토글과 셀렉터"는 그때도 그대로 남는다. 그래서 조각마다
    /// 클릭 대상이 있다: 토글은 눌러서 뒤집고, 낱말은 **그것을 직접 찍고**, 스테퍼
    /// 화살표는 좌우 두 칸이다.
    ///
    /// 무엇이 되는지는 전부 core 가 정한다(`PanelTarget::Setting*` → `setting_pick_*`).
    fn value_widget(
        &self,
        line: Flex,
        display: &base::config::ValueDisplay,
        row: usize,
        ids: &mut PanelIds,
    ) -> Flex {
        use base::config::ValueDisplay;
        match display {
            ValueDisplay::Choices { labels, cur } => {
                let on_off = labels.len() == 2
                    && *labels
                        == vec![
                            base::config::setting_value_label("on"),
                            base::config::setting_value_label("off"),
                        ];
                // '?'(구버전 서버 — 미상)를 토글로 그리면 거짓말이다.
                if let (true, Some(i)) = (on_off, cur) {
                    // 토글은 그림 하나가 곧 스위치다 — 누르면 뒤집는다(평소 `Enter` 길).
                    return line.with_child(self.clickable_panel(
                        ids.next(),
                        base::PanelTarget::SettingStep { row, forward: true },
                        self.toggle(*i == 0),
                    ));
                }
                let mut line = line;
                for (i, label) in labels.iter().enumerate() {
                    let id = ids.next();
                    let picked = Some(i) == *cur;
                    let color = if picked { palette::FG } else { palette::DIM };
                    let mut cell = Container::new(self.ui_text(label.clone(), 12., color))
                        .with_horizontal_padding(7.)
                        .with_vertical_padding(1.)
                        .with_corner_radius(theme::TAB_RADIUS);
                    if picked {
                        cell = cell.with_background_color(theme::ACTIVE);
                    } else if self.panel_hovered(id) {
                        // 고른 것이 아닌데 hover 면 "여기를 누르면 이 값이 된다"의 신호다.
                        cell = cell.with_background_color(theme::HOVER);
                    }
                    line = line.with_child(self.clickable_panel(
                        id,
                        base::PanelTarget::SettingChoice { row, index: i },
                        cell.finish(),
                    ));
                }
                if cur.is_none() {
                    let unknown = base::i18n::tc("setting", "미상(서버)");
                    line = line.with_child(self.ui_text(
                        format!("({unknown})"),
                        12.,
                        palette::DIM,
                    ));
                }
                line
            }
            ValueDisplay::Stepper(value) => {
                // 화살괄호가 곧 단추다 — 정본이 `‹ 0.20 ›` 로 "좌우로 바뀐다"를 알리는
                // 그 자리에, 네이티브에서는 실제로 누를 수 있는 것이 있어야 한다.
                let mut arrow = |line: Flex, word: &'static str, forward: bool| {
                    let id = ids.next();
                    let color = if self.panel_hovered(id) { palette::FG } else { palette::DIM };
                    line.with_child(self.clickable_panel(
                        id,
                        base::PanelTarget::SettingStep { row, forward },
                        Container::new(self.ui_text(word, 13., color))
                            .with_horizontal_padding(4.)
                            .finish(),
                    ))
                };
                let line = arrow(line, "‹", false);
                let line = line.with_child(self.text(value.clone(), 13., palette::CYAN));
                arrow(line, "›", true)
            }
            ValueDisplay::Text { shown, unset } => {
                // 자유 입력은 **물음 판**을 연다(평소 `Enter` 길) — 그 자리에서 글자를
                // 받을 칸이 없으니 값칸을 누른 것이 곧 "고쳐 쓰겠다"다.
                line.with_child(self.clickable_panel(
                    ids.next(),
                    base::PanelTarget::SettingStep { row, forward: true },
                    self.text(
                        shown.clone(),
                        13.,
                        if *unset { palette::DIM } else { palette::CYAN },
                    ),
                ))
            }
            ValueDisplay::Link(word) => line.with_child(self.clickable_panel(
                ids.next(),
                base::PanelTarget::SettingStep { row, forward: true },
                Flex::row()
                    .with_main_axis_size(MainAxisSize::Min)
                    .with_child(self.ui_text("→", 13., palette::DIM))
                    .with_child(self.ui_text(*word, 13., palette::FG))
                    .finish(),
            )),
        }
    }

    /// 설정 화면(패리티 G5b). on/off 값은 네이티브 토글 그림으로(N5) — 값을 바꾸는
    /// 길(Enter 키 → `flip_config`)은 종전 그대로다.
    fn render_settings(&self, column: Flex) -> Flex {
        // 판 안 클릭 자리의 **번호를 한 줄로 준다**(§10-21ⓣ). 사이드바와 값칸이 같은
        // 풀을 쓰므로 각자 0 부터 세면 hover 가 서로의 자리에 붙는다 — 실제로 그 풀은
        // 화면 하나당 하나다(`panel_click_states`).
        let mut ids = PanelIds::default();
        let values = self.setting_values();
        let plugins = self.screens.plugins();
        // 보이는 창(고른 줄·첫 줄·줄 수·전체)은 **한 자리**에서 잰다 — 오른쪽 막대가
        // 같은 값을 봐야 「막대가 가리키는 자리」와 「실제로 보이는 줄」이 안 갈린다.
        let (selected, start, budget, total) = self.settings_window();
        let mut list = Flex::column().with_main_axis_size(MainAxisSize::Min).with_spacing(2.);
        let mut cat = String::new();
        for row in start..total.min(start + budget) {
            let Some(setting) = plugins.setting_at(row) else {
                continue;
            };
            if setting.cat() != cat {
                cat = setting.cat().to_owned();
                // 카테고리는 **문맥 키**다 — "동작" 이 폼 라벨(Action)과 겹친다
                // (`en_core.rs` 의 `setcat` 갈래 · 파이썬 `setcat.동작` 과 같은 갈림).
                list = list.with_child(
                    Container::new(self.ui_text(
                        base::config::settings_cat_label(&cat).to_owned(),
                        11.,
                        palette::DIM,
                    ))
                    .with_padding_top(6.)
                    .finish(),
                );
            }
            // 이름은 **사람 말**이다 — `inactive-dim` 을 읽고 아는 사람은 이미
            // `set-option` 을 칠 줄 안다(정본 `setting.<key>` 카탈로그와 같은 낱말).
            let label = base::config::setting_label(setting.key()).to_owned();
            // 이름칸은 **고르기만** 한다 — 여기를 눌렀는데 값이 바뀌면 놀란다.
            let name_id = ids.next();
            let mut line = Flex::row()
                .with_main_axis_size(MainAxisSize::Min)
                .with_cross_axis_alignment(CrossAxisAlignment::Center)
                .with_spacing(8.)
                .with_child(self.clickable_panel(
                    name_id,
                    base::PanelTarget::SettingRow(row),
                    // 이름칸을 **고정 폭**으로 잡아 값칸의 세로줄을 맞춘다(정본은 26칸
                    // 패딩으로 같은 일을 한다 — 여기선 픽셀이라 상자로 잡는다).
                    ConstrainedBox::new(
                        Flex::row()
                            .with_main_axis_size(MainAxisSize::Min)
                            .with_child(self.ui_text(
                                label,
                                13.,
                                if self.panel_hovered(name_id) { palette::CYAN } else { palette::FG },
                            ))
                            .finish(),
                    )
                    .with_width(196.)
                    .finish(),
                ));
            let display = match setting {
                base::SettingRef::Core(s) => s.display(&values),
                // 플러그인 줄의 **지금 값은 모른다**(표면은 목록이지 값이 아니다) —
                // 모르는 것을 아는 척하지 않는다(`PluginSetting::display`).
                base::SettingRef::Plugin(s) => s.display(),
            };
            line = self.value_widget(line, &display, row, &mut ids);
            let mut boxed = Container::new(line.finish())
                .with_horizontal_padding(6.)
                .with_vertical_padding(1.)
                .with_corner_radius(theme::TAB_RADIUS);
            if row == selected {
                boxed = boxed.with_background_color(theme::ACTIVE);
            }
            list = list.with_child(boxed.finish());
        }
        let body = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Start)
            .with_spacing(10.)
            .with_child(self.settings_sidebar(base::config::settings_cat_of(selected), &mut ids))
            .with_child(list.finish())
            .finish();
        // ★ 오른쪽 **표시용 스크롤바**(pytmux-374 ⑴ · 정본 `#sets` 의
        //   `scrollbar-color: $accent` 자리). 그릴 것이 없으면 core 가 `None` 을 준다.
        column.with_child(match self.settings_scroll() {
            Some((thumb_start, thumb_len)) => {
                PanelScrollBar::new(body, thumb_start, thumb_len).finish()
            }
            None => body,
        })
    }

    /// 설정 판의 **보이는 창** — `(고른 줄, 창의 첫 줄, 창의 줄 수, 전체 줄 수)`.
    ///
    /// ⛔ **두 곳에서 재지 않는다** — 그리는 쪽과 스크롤바가 각자 셈하면 「막대는 끝을
    /// 가리키는데 목록은 아직 중간」 같은 조용한 어긋남이 난다(그 막대는 자리를 말하려고
    /// 그리는 것이라, 틀린 자리를 말하면 없느니만 못하다).
    ///
    /// 창을 미는 규칙은 「고른 줄이 창의 **마지막**에 오게」다(N5) — 아래로 내려갈 때
    /// 한 줄씩 따라 붙고, 위로 올라가면 `start` 가 0 에서 멎는다.
    fn settings_window(&self) -> (usize, usize, usize, usize) {
        // 줄 목록은 **코어 뒤에 플러그인**이다(정본 `settings_order` 와 같은 차례).
        let total = self.screens.plugins().settings_len();
        let budget = self.panel_budget();
        let selected = self.screens.selected().min(total.saturating_sub(1));
        (selected, (selected + 1).saturating_sub(budget), budget, total)
    }

    /// 설정 판 오른쪽 막대의 `(썸 시작, 썸 길이)` — 트랙을 1.0 으로 본 값.
    ///
    /// **값의 주인은 core 다**(`base::scrollbar::list_fraction`) — 캔버스 패널의 막대와
    /// 같은 산수를 쓰고, 여기서는 그 값을 픽셀로 옮기기만 한다.
    fn settings_scroll(&self) -> Option<(f64, f64)> {
        let (_, start, budget, total) = self.settings_window();
        base::scrollbar::list_fraction(budget, total, start)
    }

    /// 시험용 관측점 — 창 없이 「지금 막대가 어디를 가리키나」를 묻는다.
    #[cfg(test)]
    pub(crate) fn settings_scroll_for_test(&self) -> Option<(f64, f64)> {
        self.settings_scroll()
    }

    /// 판 안 줄 번호 → **설정 표(`SETTINGS`)의 줄 번호**. 그 줄이 지금 안 먹으면 `None`.
    ///
    /// # 왜 한 함수인가 (`pytmux/pytmux-375`)
    ///
    /// 커서 판은 설정 다섯만 모은 판이라 그 판의 `row` 는 0~4 이고, 값을 고르는 표
    /// (`setting_pick_dir`·`setting_pick_at`)가 아는 것은 `SETTINGS` 의 색인이다. 그
    /// 사이를 옮기는 자리가 둘이 되면 **키 경로와 마우스 경로가 다르게 낡는다** —
    /// [`apply_setting_pick`](Self::apply_setting_pick) 이 적어 둔 그 사고를 여기서
    /// 되풀이하지 않으려고 진입 둘이 이 함수 하나를 지난다.
    ///
    /// 설정·플러그인 판에서는 판 안 번호가 곧 표의 번호라 그대로 돌려준다.
    fn settings_row(
        screen: Option<Screen>,
        row: usize,
        config: &base::config::Config,
    ) -> Option<usize> {
        if screen != Some(Screen::Cursor) {
            return Some(row);
        }
        // ⛔ `block` 의 두께 줄은 **적용할 자리가 없다**. 여기서 안 막으면 화면은 아무
        //    반응이 없는데 설정 파일만 조용히 움직인다 — 판이 그 줄을 흐리게 그려 놓고
        //    값은 바뀌는 것이 가장 나쁜 조합이다.
        if !base::config::cursor_row_applies(row, &config.cursor_style) {
            return None;
        }
        base::config::cursor_setting_row(row)
    }

    /// 커서 판(`cursor` · `pytmux/pytmux-375` ⓐ) — 설정 다섯 + **견본 한 칸**.
    ///
    /// # 왜 설정 화면을 그냥 쓰면 안 되나
    ///
    /// 값도 같은 다섯이고 손도 같다(`press_settings` 를 함께 탄다). 갈리는 것은
    /// **보이는 것**이다: 설정 판은 캔버스를 덮어 커서를 가리므로 «바꾸면서 결과를
    /// 못 본다»(제보 2026-08-23). 그리고 그 다섯에 닿는 길이 「표시」 분류를 찾아
    /// 내려가는 것뿐이었다. 새로 지은 것은 **화면이지 값이 아니다.**
    ///
    /// # 왜 견본이 판 «안» 인가
    ///
    /// 「판을 커서 안 가리는 자리에 세운다」는 길도 있었는데, 커서는 캔버스 어디에나
    /// 있을 수 있어 그 자리가 상수가 아니다 — 판이 커서를 피해 다니게 만들면
    /// pytmux-373 이 신고한 「판 기하가 내용에 따라 움직인다」를 새로 만드는 셈이다.
    /// 견본은 언제나 같은 자리, 같은 크기다.
    ///
    /// # 높이가 안 흔들린다
    ///
    /// 줄이 **언제나 다섯**이고(안 먹는 줄도 지우지 않고 흐리게 둔다) 견본 칸은
    /// `ConstrainedBox` 로 못박혀 두께를 키워도 안 커진다. 그래서 이 판은 `pad_rows`
    /// 없이도 늘 같은 높이다(pytmux-373 ⑴ 이 요구한 성질).
    fn render_cursor(&self, column: Flex) -> Flex {
        let mut ids = PanelIds::default();
        let values = self.setting_values();
        let rows = base::config::CURSOR_SETTINGS.len();
        let selected = self.screens.selected().min(rows.saturating_sub(1));
        let mut list = Flex::column()
            .with_main_axis_size(MainAxisSize::Min)
            .with_spacing(2.);
        for row in 0..rows {
            // 이름으로 찾는다 — 색인을 박아 두면 `SETTINGS` 에 줄 하나를 끼우는 것만으로
            // 이 판이 말없이 다른 설정을 보인다(`CURSOR_SETTINGS` 머리말).
            let Some(setting) =
                base::config::cursor_setting_row(row).and_then(|at| base::config::SETTINGS.get(at))
            else {
                continue;
            };
            let applies = base::config::cursor_row_applies(row, &self.config.cursor_style);
            let name_id = ids.next();
            let name_color = if !applies {
                palette::DIM
            } else if self.panel_hovered(name_id) {
                palette::CYAN
            } else {
                palette::FG
            };
            let mut line = Flex::row()
                .with_main_axis_size(MainAxisSize::Min)
                .with_cross_axis_alignment(CrossAxisAlignment::Center)
                .with_spacing(8.)
                .with_child(self.clickable_panel(
                    name_id,
                    base::PanelTarget::SettingRow(row),
                    // 이름칸 폭을 못박아 값칸의 세로줄을 맞춘다(설정 판과 같은 손이되
                    // 이 판은 사이드바가 없어 조금 좁다).
                    ConstrainedBox::new(
                        Flex::row()
                            .with_main_axis_size(MainAxisSize::Min)
                            .with_child(self.ui_text(
                                base::config::setting_label(setting.key).to_owned(),
                                13.,
                                name_color,
                            ))
                            .finish(),
                    )
                    .with_width(150.)
                    .finish(),
                ));
            line = if applies {
                self.value_widget(line, &setting.display(&values), row, &mut ids)
            } else {
                // ⛔ 안 먹는 줄에 **누를 것**을 두지 않는다 — 눌러도 아무 일이 없는
                //    화살표는 「고장 났다」로 읽힌다. 값 대신 **왜 없는지**를 적는다.
                line.with_child(self.ui_text(
                    t("채운 네모는 칸을 반전한다 — 두께를 줄 자리가 없다"),
                    12.,
                    palette::DIM,
                ))
            };
            let mut boxed = Container::new(line.finish())
                .with_horizontal_padding(6.)
                .with_vertical_padding(1.)
                .with_corner_radius(theme::TAB_RADIUS);
            if row == selected {
                boxed = boxed.with_background_color(theme::ACTIVE);
            }
            list = list.with_child(boxed.finish());
        }
        column
            .with_child(self.cursor_sample())
            .with_child(list.finish())
    }

    /// 커서 견본 한 칸 — **지금 설정 그대로** 그린다.
    ///
    /// # 어느 것도 흉내가 아니다
    ///
    /// 모양은 [`splitter::cursor_ink`](crate::splitter::cursor_ink) 가, 색은 진짜 커서와
    /// 같은 `proto::status::color` 가, 깜빡임은 같은 `blink_on` 이 정한다. 「어느 변을
    /// 긋나」를 여기서 다시 적으면 판이 보여 주는 그림과 실제 커서가 갈리고, 그러면 이
    /// 판의 존재 이유(「바꾸는 즉시 **맞게** 보인다」)가 통째로 사라진다.
    ///
    /// 칸 크기도 **캔버스의 진짜 칸**이다 — 견본이 실제보다 크거나 작으면 두께를 고르는
    /// 판단 자체가 어긋난다. 아직 못 쟀으면(첫 프레임) 보통 값으로 그리고 한 프레임 뒤에
    /// 제자리로 온다(`mono_row` 와 같은 처방).
    fn cursor_sample(&self) -> Box<dyn Element> {
        use crate::splitter::CursorInk;
        let (cw, ch) = self.cell_px.get().unwrap_or((9., 20.));
        let color = proto::status::color(&self.config.cursor_color)
            .map(convert)
            .unwrap_or(theme::FOCUS);
        let ink = crate::splitter::cursor_ink(Self::cursor_style(&self.config.cursor_style));
        // 칸보다 굵은 선은 칸 밖으로 샌다 — 자르는 규칙도 진짜 커서와 같다.
        let width = self.config.cursor_thickness.clamp(1., cw.min(ch));
        let cell = Container::new(Empty::new().finish());
        // 깜빡임의 「안 보임」 반주기에는 **아무것도 안 그린다** — 판에서만 늘 보이면
        // 그 값을 켜 놓고 결과를 못 보는 자리가 다시 생긴다.
        let cell = match ink {
            _ if !self.blink_on => cell.with_background_color(palette::BG),
            CursorInk::Fill => cell.with_background_color(color),
            CursorInk::Edges {
                top,
                left,
                bottom,
                right,
            } => cell.with_background_color(palette::BG).with_border(
                Border::new(width)
                    .with_sides(top, left, bottom, right)
                    .with_border_color(color),
            ),
        };
        let sample = ConstrainedBox::new(cell.finish())
            .with_width(cw)
            .with_height(ch)
            .finish();
        // 견본을 «글자 옆»에 둔다 — 커서는 늘 글 끝에 있고, 맨 칸 하나만 띄우면
        // 두께가 실제 글자에 대해 얼마나 굵은지가 안 보인다.
        let row = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_spacing(10.)
            .with_child(self.ui_text(t("미리보기"), 11., palette::DIM))
            .with_child(self.text("$ pytmux", 13., palette::FG))
            .with_child(sample)
            .finish();
        Container::new(row)
            .with_padding_bottom(8.)
            .with_border(Border::bottom(1.).with_border_color(theme::BORDER))
            .finish()
    }

    /// 인자 폼(패리티 G8v). 내용(줄 글자)은 TUI 와 한 벌(core `row_text`)이고, 고른
    /// 줄은 `▶` 대신 배경 강조로 가른다(N5 — 목록들과 같은 문법).
    fn render_options(&self, column: Flex) -> Flex {
        let Some(options) = self.screens.options() else {
            return column;
        };
        let selected = self.screens.selected();
        let mut column = column;
        for (i, spec) in options.specs.iter().enumerate() {
            let sel = self.screens.option_sel().get(i).copied().unwrap_or(0);
            let label = self.text(
                base::options::row_text(spec, sel),
                14.,
                palette::FG,
            );
            let mut boxed = Container::new(label)
                .with_horizontal_padding(6.)
                .with_vertical_padding(1.)
                .with_corner_radius(theme::TAB_RADIUS);
            if i == selected {
                boxed = boxed.with_background_color(theme::ACTIVE);
            }
            column = column.with_child(boxed.finish());
        }
        column.with_child(self.text(
            format!(
                ": {}",
                base::options::line(options, self.screens.option_sel())
            ),
            13.,
            palette::DIM,
        ))
    }

    /// 인자 폼에서 고른 것을 실행한다. 액션은 **키로 누른 것과 같은 길**로 간다.
    fn apply_option_pick(&mut self) {
        use base::options::OptionPick;
        let Some(options) = self.screens.options() else {
            return;
        };
        match base::options::pick(options, self.screens.option_sel()) {
            Some(OptionPick::Act(action)) => {
                self.apply_action(action);
            }
            Some(OptionPick::Set(key, value)) => self.set_config(key, value),
            Some(OptionPick::Flip(key)) => self.flip_config(key),
            None => {}
        }
    }

    /// 대답 하나를 **실제로 적용한다**(패리티 G4 · G8u). TUI 와 같은 표다.
    ///
    /// 입구가 둘이다 — 사람이 물음에 답한 길과, **훅이 물음을 건너뛰고 대답을 바로
    /// 넣는 길**([`base::hooks::HookRun::Answer`]).
    fn apply_answer(&mut self, prompt: Prompt, answer: String) {
        // 인자 이력에 남긴다(파이썬 arghist — 다음에 같은 물음에서 후보로 뜬다).
        if let Some(bucket) = proto::arghist::bucket(prompt) {
            self.arghist.record(bucket, &answer);
        }
        // ★ 재시작 둘은 **부수효과가 하나가 아니다**(클라 재기동 + 서버 명령). 명령 하나로
        // 옮기는 아래 표에 넣을 수 없으므로 여기서 갈라 준다(TUI 와 같은 자리).
        if prompt == Prompt::RestartAll || prompt == Prompt::RestartServer {
            let kind = if prompt == Prompt::RestartAll {
                base::restart::Kind::All
            } else {
                base::restart::Kind::Server
            };
            self.do_restart(kind);
            return;
        }
        // 상태줄 넷은 **빈 대답이 뜻을 갖는다** — 색은 "테마 그대로", 형식은
        // "아무것도 안 그린다". 그래서 되돌리기로 바꾸지 않고 적힌 그대로 쓴다.
        if let Some(key) = base::config::prompt_key(prompt) {
            self.set_config(key, answer.trim());
        } else if prompt == Prompt::DisplayPopup {
            // 파이썬과 같은 문법(`[-w N] [-h N] <cmd>` · `-C` 닫기) — 판정은 proto 한 벌.
            match proto::command::parse_popup_line(&answer) {
                proto::command::PopupLine::Open(command) => {
                    self.pending.push(Outgoing::Command(command))
                }
                proto::command::PopupLine::Close => {
                    self.pending.push(Outgoing::Command(Command::PopupClose))
                }
                proto::command::PopupLine::Usage => self
                    .state
                    .note_notice(t("display-popup [-w N] [-h N] <명령> · 닫기는 -C")),
            }
        } else if prompt == Prompt::RunShell {
            self.spawn_shell(answer.clone(), None, None);
        } else if prompt == Prompt::IfShell {
            // `조건 | 명령` — 파이프로 가른다(셸 명령에 공백이 흔해 첫 낱말로 자르면
            // 조건을 한 낱말로만 쓸 수 있다).
            let mut parts = answer.splitn(3, '|').map(str::trim);
            match (parts.next(), parts.next()) {
                (Some(cond), Some(then)) if !then.is_empty() => self.spawn_shell(
                    cond.to_owned(),
                    Some(then.to_owned()),
                    parts.next().filter(|s| !s.is_empty()).map(str::to_owned),
                ),
                _ => self.state.note_error(t("조건과 명령을 `|` 로 갈라 적는다")),
            }
        } else if prompt == Prompt::BindKey {
            self.apply_bind_line(&answer, true);
        } else if prompt == Prompt::UnbindKey {
            self.apply_bind_line(&answer, false);
        } else if prompt == Prompt::SetOption {
            self.apply_set_line(&answer);
        } else if prompt == Prompt::SetHook {
            self.apply_hook_line(&answer);
        } else if prompt == Prompt::SendKeys {
            let bytes = base::keys::parse_send_keys(&answer);
            if !bytes.is_empty() {
                self.pending.push(Outgoing::Input(bytes));
            }
        } else if prompt == Prompt::DisplayMessage {
            if !answer.trim().is_empty() {
                self.state.note_notice(answer.clone());
            }
        } else if prompt == Prompt::DefaultPath {
            // 빈 대답은 되돌리기다 — 서버 기본(`current`)으로.
            let value = if answer.trim().is_empty() {
                "current"
            } else {
                answer.trim()
            };
            self.set_config("default-path", value);
        } else if prompt == Prompt::SetPrefix {
            self.apply_prefix_answer(&answer);
        } else if let Some(command) = Self::answered(prompt, &answer) {
            // 이 회신을 기다리고 있다는 표식(정본 `_want_search_all` 과 같은 게이트) —
            // 뒤늦거나(이전 검색의 느린 상류 회신) 남의 요청의 회신을 결과 판으로
            // 착각해 열지 않으려는 것이다. 끄는 자리는 그 메시지를 받는 곳(§apply).
            if matches!(command, Command::SearchAll { .. }) {
                self.awaiting_search_all = true;
            }
            self.pending.push(Outgoing::Command(command));
        }
    }

    /// `set-hook` 한 줄. **설정 파일에 적지 않는다** — 파이썬도 런타임 값이다.
    fn apply_hook_line(&mut self, line: &str) {
        use base::hooks::SetHook;
        match base::hooks::parse_set_hook(line) {
            Some(SetHook::Set { event, command }) => {
                self.config.hooks.set(&event, &command);
                self.state.note_notice(tf(
                    "훅 {event} → {command}",
                    &[("event", &event), ("command", &command)],
                ));
            }
            Some(SetHook::Unset { event }) => {
                if self.config.hooks.unset(&event) {
                    self.state
                        .note_notice(tf("훅 {event} 를 풀었다", &[("event", &event)]));
                } else {
                    self.state.note_error(t("그런 훅이 없다"));
                }
            }
            None => self
                .state
                .note_error(t("훅은 <이벤트> <명령> 또는 -u <이벤트> 로 적는다")),
        }
    }

    /// 사건이 났다 — 걸린 훅이 있으면 **사람이 친 것과 같은 길**로 돌린다(패리티 G8u).
    fn fire_hooks(&mut self, events: &[base::hooks::HookEvent]) {
        use base::hooks::HookRun;
        for event in events {
            let Some(line) = self.config.hooks.get(event.name()).map(str::to_owned) else {
                continue;
            };
            match base::hooks::resolve(&line) {
                Some(HookRun::Act(action)) => {
                    self.apply_action(action);
                }
                Some(HookRun::Answer(prompt, answer)) => self.apply_answer(prompt, answer),
                // 모르는 이름은 조용히 넘긴다(정본과 같다).
                None => {}
            }
        }
    }

    /// 물음에 나온 대답이 뜻하는 명령(패리티 G4).
    ///
    /// **빈 대답은 아무 일도 아니다** — 이름을 지우고 Enter 를 친 사람이 탭 이름을
    /// 빈 문자열로 만들려던 것은 아니다(서버가 그걸 받으면 탭바에서 그 탭이 사라진 것처럼
    /// 보인다).
    fn answered(prompt: Prompt, answer: &str) -> Option<Command> {
        let answer = answer.trim();
        match prompt {
            Prompt::RenameTab => (!answer.is_empty()).then(|| Command::RenameWindow {
                name: answer.to_owned(),
            }),
            // 숫자가 아니면 아무 일도 안 한다 — 짐작해서 0번으로 옮기면 사용자는 자기가
            // 무엇을 했는지 모른다.
            Prompt::MoveTab => answer
                .parse::<usize>()
                .ok()
                .map(|index| Command::MoveWindow { index }),
            Prompt::KillPane => Some(Command::KillPane),
            // 넷 다 하는 일은 같다 — **문구만** 상황을 안다(무엇을 잃는지가 다르다).
            Prompt::KillTab
            | Prompt::KillTabLast
            | Prompt::KillTabLastRemote
            | Prompt::KillTabPinned => Some(Command::KillWindow),
            // 설정은 **서버에 시킬 일이 아니다** — 우리 설정 파일과 이번 판의 키맵을
            // 고치는 것이라 호출부에서 따로 처리한다(위 `Answered` 갈래).
            // 빈 제목은 **되돌리기**다(서버가 기본 제목으로 돌린다) — 지우고 Enter 를
            // 친 사람의 뜻이 그거다.
            Prompt::RenamePane => Some(Command::SetPaneTitle {
                title: answer.to_owned(),
            }),
            // 숫자가 아니면 아무 일도 안 한다 — 짐작해서 0번과 바꾸면 사용자는 자기가
            // 무엇을 했는지 모른다(탭 이동과 같은 규칙).
            Prompt::SwapTab => answer
                .parse::<usize>()
                .ok()
                .map(|index| Command::SwapTab { index }),
            // 플러그인이 물은 것은 **서버 명령이 아니라** 그 플러그인의 액션이라
            // 여기서 명령으로 옮기지 않는다(`answer_plugin_ask` 가 앞에서 가로챈다).
            Prompt::PluginAsk => None,
            // 빈 이름은 아무 일도 아니다 — 이름 없는 배치는 다시 못 찾는다.
            Prompt::SaveTabLayout => (!answer.is_empty()).then(|| Command::SaveTabLayout {
                name: answer.to_owned(),
            }),
            Prompt::LoadTabLayout => (!answer.is_empty()).then(|| Command::LoadTabLayout {
                name: answer.to_owned(),
                new: false,
            }),
            Prompt::LoadTabLayoutNew => (!answer.is_empty()).then(|| Command::LoadTabLayout {
                name: answer.to_owned(),
                new: true,
            }),
            // 빈 대답이 **뜻을 갖는다** — 파이프를 끈다(원격 떼기와 같은 부류).
            Prompt::PipePane => Some(Command::PipePane {
                cmd: answer.to_owned(),
            }),
            Prompt::JoinPane => answer.parse::<usize>().ok().map(|src| Command::JoinPane {
                src,
                horizontal: false,
            }),
            // 서버에 시킬 일이 아니다 — 우리 상태줄·설정 파일이라 호출부가 처리한다.
            Prompt::DisplayMessage | Prompt::DefaultPath | Prompt::ClaudeCommand | Prompt::SetOption
            | Prompt::SendKeys | Prompt::BindKey | Prompt::UnbindKey
            | Prompt::RunShell | Prompt::IfShell | Prompt::DisplayPopup => None,
            // 상태줄 넷과 커서 색은 **설정 파일**이 주인이라 서버에 시킬 일이 없다.
            Prompt::StatusLeft | Prompt::StatusRight | Prompt::StatusBg | Prompt::StatusFg
            | Prompt::CursorColor | Prompt::FontFamily => None,
            // 훅도 클라 안에서 끝난다(`base::hooks`).
            Prompt::SetHook => None,
            Prompt::SetPrefix => None,
            Prompt::KillServer => Some(Command::KillServer),
            // 재시작 둘은 아래 갈래에서 이미 처리된다(부수효과가 둘이라 명령 하나로
            // 못 옮긴다).
            Prompt::RestartServer | Prompt::RestartAll => None,
            // 빈 host 는 아무 일도 아니다 — 어느 상자에 붙을지 모르는 채로 보내면
            // 서버가 빈 이름으로 ssh 를 띄운다.
            // `C via B`(다중홉) 문법까지 여기서 갈린다 — 파싱은 proto 한 곳이다.
            Prompt::RemoteAttach => Command::remote_attach(answer),
            Prompt::RemoteNewTab => (!answer.is_empty()).then(|| Command::RemoteNewTab {
                host: answer.to_owned(),
            }),
            // 떼기는 **빈 값이 곧 뜻**이다(전부) — 위 둘과 갈리는 자리다.
            Prompt::RemoteDetach => Some(Command::RemoteDetach {
                host: answer.to_owned(),
            }),
            // 빈 대답은 검색이 아니다(파이썬도 값이 있어야 보낸다). 새 검색은 위(과거)
            // 방향부터 — `/` 물음의 문구가 그 약속이다.
            Prompt::SearchScrollback => (!answer.is_empty()).then(|| Command::Search {
                query: Some(answer.to_owned()),
                down: false,
            }),
            // 빈 대답은 검색이 아니다(정본도 값이 있어야 보낸다). `awaiting_search_all`
            // 는 이 명령을 실제로 큐에 넣는 호출부(§`Action::SearchAll` 처리)가 켠다 —
            // 여기서는 명령 모양만 짓는다.
            Prompt::SearchAll => (!answer.is_empty())
                .then(|| Command::SearchAll { query: answer.to_owned() }),
        }
    }

    /// 입력·확인 화면(패리티 G4).
    fn render_prompt(&self, column: Flex, screen: Screen) -> Flex {
        let Some(_prompt) = self.screens.asking() else {
            return column;
        };
        // ★ 상세를 **물음 위에** 먼저 그린다(TUI 와 같은 자리). 계산해 두고 안 그리면
        // 사용자는 무엇을 보고 판단할지 알 수 없다.
        let mut column = column;
        for line in self.screens.confirm_detail().lines().filter(|l| !l.is_empty()) {
            // 13px **본문**이 지는 뜻이다 — 크롬 의미색이지 SGR 표가 아니다(pytmux-412 ⓑ1).
            column = column.with_child(self.text(line.to_owned(), 13., theme::ERROR));
        }
        let mut column = column.with_child(self.text(self.screens.confirm_question(), 14., palette::FG));
        if screen == Screen::Prompt {
            // ★ **후보가 먼저, 입력이 아래**다(정본 `#pcand` → `#prow` 차례 —
            //   `Screen::candidates_above_input`). 정본이 이 차례를 컨테이너 흐름으로
            //   못박은 이유: 둘 다 바닥 고정이라 적층이 뒤집혔고, 그러면 모바일에서
            //   후보가 키보드에 가렸다(사용자 요청). 판이 바닥에 붙는 이상 우리도 같다 —
            //   입력 줄이 판의 **맨 아래**여야 눈과 손이 한 자리에 머문다.
            let picked = self.screens.prompt_pick();
            debug_assert!(screen.candidates_above_input());
            for (i, cand) in self.screens.prompt_matches().iter().enumerate() {
                let label = self.text(format!("  {cand}"), 13., palette::DIM);
                column = column.with_child(if picked == Some(i) {
                    Container::new(label)
                        .with_background_color(palette::SELECTED_BG)
                        .with_uniform_padding(1.)
                        .finish()
                } else {
                    Container::new(label).with_uniform_padding(1.).finish()
                });
            }
            // 커서를 **실제 위치**에 그린다(pytmux-174) — 종전엔 `_` 를 언제나 끝에
            // 붙였는데, 이제 `Left`/`Home`/`Shift+…` 로 중간에서 편집할 수 있으니 커서도
            // 따라가야 한다. 선택 범위가 있으면 그 구간을 배경으로 칠한다.
            // 입력기 배지는 이 줄의 오른쪽 끝(pytmux-14).
            column = column.with_child(self.input_line(self.prompt_input_row()));
        }
        if screen == Screen::Confirm {
            column = column.with_child(self.confirm_buttons());
        }
        column
    }

    /// 한 줄 입력의 `"> "` 줄 — 커서를 **실제 글자 위치**에 세우고, 선택 범위가 있으면
    /// 그 구간의 배경을 칠한다(pytmux-174). `core` 는 글자 인덱스만 들고 있고, 그것을
    /// 세 조각(앞·[선택]·뒤)으로 잘라 그리는 것은 뷰의 일이다.
    fn prompt_input_row(&self) -> Box<dyn Element> {
        let chars: Vec<char> = self.screens.typed().chars().collect();
        let slice = |a: usize, b: usize| -> String { chars[a..b].iter().collect::<String>() };
        let mut row = Flex::row().with_main_axis_size(MainAxisSize::Min);
        row = row.with_child(self.text("> ", 14., palette::CYAN));
        match self.screens.prompt_selection() {
            Some((s, e)) => {
                if s > 0 {
                    row = row.with_child(self.text(slice(0, s), 14., palette::CYAN));
                }
                row = row.with_child(
                    Container::new(self.text(slice(s, e), 14., palette::CYAN))
                        .with_background_color(palette::SELECTED_BG)
                        .finish(),
                );
                if e < chars.len() {
                    row = row.with_child(self.text(slice(e, chars.len()), 14., palette::CYAN));
                }
            }
            None => {
                let cursor = self.screens.prompt_cursor().min(chars.len());
                if cursor > 0 {
                    row = row.with_child(self.text(slice(0, cursor), 14., palette::CYAN));
                }
                // 정본의 커서 자리 표식과 같은 뜻이다 — 종전엔 이 글자가 늘 끝에 있었다.
                row = row.with_child(self.text("│", 14., palette::FG));
                if cursor < chars.len() {
                    row = row.with_child(self.text(slice(cursor, chars.len()), 14., palette::CYAN));
                }
            }
        }
        row.finish()
    }

    /// 확인 화면의 버튼 줄(정본과 같은 자리) — 고른 쪽을 강조한다.
    ///
    /// 문장만 있고 버튼이 없으면 **무엇이 기본인지 화면에 없다**(대조 문서 §9). 기본은
    /// 늘 '아니오'이고, 그 사실이 눈에 보여야 되돌릴 수 없는 일 앞에서 손이 멈춘다.
    /// 재시작 점검 판의 **「지금 재시작」 단추**(§10-21ⓓ3).
    ///
    /// # 왜 안전하지 않으면 비활성인가
    ///
    /// 제보가 "안전하면 그 판에서 눌러 바로 재시작"이라고 조건을 붙였다. 안전하지 않을
    /// 때 단추를 지우면 **왜 못 하는지가 화면에서 사라지고**, 그냥 누르게 두면 이 판은
    /// 그것을 막으려고 있는 것을 스스로 무너뜨린다. 그래서 흐리게 남기고 위 표의 FAIL
    /// 줄이 이유를 말한다.
    ///
    /// # 왜 지름길이 아닌가
    ///
    /// 누르면 [`Action::RestartAll`] 이 **평소 경로**로 돈다 — 팔레트의 `restart-all` 과
    /// 같은 길이라 드라이런을 다시 돌고, FAIL 이면 확인 판이 뜬다. 되돌릴 수 없는 것
    /// 앞의 게이트를 클릭이 건너뛰지 않는다.
    fn restart_button(&self, safe: bool) -> Box<dyn Element> {
        let label = self.ui_text(
            t("지금 재시작 (restart-all)"),
            13.,
            if safe { palette::FG } else { palette::DIM },
        );
        let mut boxed = Container::new(label)
            .with_horizontal_padding(14.)
            .with_vertical_padding(4.)
            .with_corner_radius(theme::PILL_RADIUS);
        if !safe {
            return Container::new(boxed.finish()).with_padding_top(8.).finish();
        }
        // 클릭 자리 번호는 이 판에 하나뿐이다 — 확인 판의 단추들과 같은 풀이지만 판이
        // 다르고, 판이 바뀔 때 풀을 버린다(`handle_key` 의 hover 버리기).
        let id = 0;
        boxed = boxed
            .with_background_color(if self.panel_hovered(id) { theme::HOVER } else { theme::ACTIVE })
            .with_border(Border::all(1.5).with_border_color(theme::FOCUS));
        Container::new(self.clickable_panel(
            id,
            base::PanelTarget::Button(base::Action::RestartAll),
            boxed.finish(),
        ))
        .with_padding_top(8.)
        .finish()
    }

    fn confirm_buttons(&self) -> Box<dyn Element> {
        let pick = self.screens.confirm_pick();
        let mut row = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_spacing(8.);
        for (i, name) in
            self.screens.confirm_buttons().iter().enumerate()
        {
            let picked = i == pick;
            let fg = if picked { palette::FG } else { palette::DIM };
            let mut boxed = Container::new(self.ui_text((*name).to_owned(), 13., fg))
                .with_horizontal_padding(14.)
                .with_vertical_padding(4.)
                .with_corner_radius(theme::PILL_RADIUS);
            if picked {
                // 되돌릴 수 없는 판이면 강조가 **붉다**(정본 `.sel.danger`). 아무 데나
                // 칠하면 붉은색이 값을 잃으므로 경계는 core 가 쥔다.
                // `theme::FOCUS` 의 위험 쪽 쌍이다 — 그러니 같은 표(크롬)에 산다.
                let accent = if self.screens.confirm_is_dangerous() {
                    theme::ERROR
                } else {
                    theme::FOCUS
                };
                boxed = boxed
                    .with_background_color(theme::ACTIVE)
                    .with_border(Border::all(1.5).with_border_color(accent));
            } else if self.panel_hovered(i) {
                boxed = boxed.with_background_color(theme::HOVER);
            }
            row = row.with_child(self.clickable_panel(
                i,
                base::PanelTarget::ConfirmButton(i),
                boxed.finish(),
            ));
        }
        Container::new(row.finish()).with_vertical_padding(6.).finish()
    }

    /// 목록에서 고른 줄이 뜻하는 명령들(패리티 G3a·G3b).
    ///
    /// **뷰가 해석한다** — core 는 목록의 내용을 모르고 줄 번호만 돌려준다. 여기가 그
    /// 번호를 각 목록의 어휘(탭 index · 패널 id · 버퍼 번호)로 옮기는 유일한 자리다.
    fn picked(&self, screen: Option<Screen>, row: usize) -> Vec<Command> {
        match screen {
            // 스위처 줄은 탭일 수도, 그 밑의 패널일 수도 있다 — 트리와 같은 팔이다.
            Some(Screen::Tabs) => {
                let rows = self.state.switcher_rows();
                let Some(picked) = rows.get(row) else {
                    return Vec::new();
                };
                let mut out = Vec::new();
                if let Some(index) = picked.window {
                    let wid = self.state.tabs().wid_of(index);
                    out.push(Command::SelectWindow { index, wid });
                }
                if let Some(id) = picked.pane {
                    out.push(Command::SelectPaneId { id });
                }
                out
            }
            Some(Screen::Tree) => {
                let rows = self.state.tree_rows();
                let Some(picked) = rows.get(row) else {
                    return Vec::new();
                };
                let mut out = Vec::new();
                // 세션 줄은 가리키는 탭이 없다 — 고를 수 없는 줄이다.
                if let Some(index) = picked.window {
                    let wid = self.state.tabs().wid_of(index);
                    out.push(Command::SelectWindow { index, wid });
                }
                // 패널 줄이면 **탭을 바꾼 뒤 그 패널로** 간다. 순서가 뒤집히면 아직
                // 안 보이는 탭의 패널을 고르는 셈이 된다.
                if let Some(id) = picked.pane {
                    out.push(Command::SelectPaneId { id });
                }
                out
            }
            Some(Screen::Buffers) => self
                .state
                .buffers()
                .get(row)
                // 서버가 매긴 번호를 그대로 쓴다 — 클라가 다시 매기면 엉뚱한 버퍼가 붙는다.
                .map(|item| vec![Command::PasteBuffer { index: item.index }])
                .unwrap_or_default(),
            _ => Vec::new(),
        }
    }

    /// 트리 목록(패리티 G3b).
    fn render_tree_list(&self, column: Flex) -> Flex {
        let rows = self.state.tree_rows();
        if rows.is_empty() {
            return column.with_child(self.text(t("개요를 기다리는 중…"), 13., palette::DIM));
        }
        let selected = self.screens.selected().min(rows.len() - 1);
        let cols = self.state.composite().map_or(80, |c| c.size().0) as usize;
        let mut column = column;
        for (i, row) in rows.iter().enumerate() {
            let text = format!(
                "{}{} {}",
                "  ".repeat(row.depth),
                if row.active { "•" } else { " " },
                row.label
            );
            let label = self.text(footer::elide(&text, cols), 13., palette::FG);
            column = column.with_child(if i == selected {
                Container::new(label)
                    .with_background_color(palette::SELECTED_BG)
                    .with_uniform_padding(1.)
                    .finish()
            } else {
                Container::new(label).with_uniform_padding(1.).finish()
            });
        }
        column
    }

    /// 버퍼 목록(패리티 G3b).
    fn render_buffer_list(&self, column: Flex) -> Flex {
        let items = self.state.buffers();
        if items.is_empty() {
            return column.with_child(self.text(t("버퍼가 없다"), 13., palette::DIM));
        }
        let selected = self.screens.selected().min(items.len() - 1);
        let cols = self.state.composite().map_or(80, |c| c.size().0) as usize;
        let mut column = column;
        for (i, item) in items.iter().enumerate() {
            let text = format!("{}: {}", item.index, item.preview);
            let label = self.text(footer::elide(&text, cols), 13., palette::FG);
            column = column.with_child(if i == selected {
                Container::new(label)
                    .with_background_color(palette::SELECTED_BG)
                    .with_uniform_padding(1.)
                    .finish()
            } else {
                Container::new(label).with_uniform_padding(1.).finish()
            });
        }
        column
    }

    /// 탭 스위처(패리티 G3a).
    ///
    /// 라벨은 탭바와 **같은 정본**(`Tab::label`)을 쓴다 — 목록과 탭바가 다른 이름을 보이면
    /// 사용자는 자기가 어느 탭을 고르는지 확신할 수 없다. 원격 탭 색도 같은 규약이다.
    fn render_tab_list(&self, column: Flex) -> Flex {
        let items = self.state.switcher_rows();
        if items.is_empty() {
            return column.with_child(self.text(t("(탭 없음)"), 13., palette::DIM));
        }
        let selected = self.screens.selected().min(items.len() - 1);
        let mut column = column;
        for (row, item) in items.iter().enumerate() {
            // 원격 여부는 줄이 아니라 **탭이 안다** — 탭 줄만 분홍이다(패널 하위행은
            // 로컬 탭에만 끼므로 늘 흐린 평색이다 — TUI 와 같은 규칙).
            let remote = item.pane.is_none()
                && self
                    .state
                    .tabs()
                    .tabs
                    .iter()
                    .any(|t| Some(t.index) == item.window && t.remote);
            let fg = if remote {
                REMOTE_PINK
            } else if item.pane.is_some() {
                palette::DIM
            } else {
                palette::FG
            };
            let text = format!(
                "{} {}{}",
                if item.active { "•" } else { " " },
                "    ".repeat(item.depth),
                if item.pane.is_some() {
                    format!("└ {}", item.label)
                } else {
                    item.label.clone()
                }
            );
            // 지금 고른 줄은 **배경**으로 가른다(탭바의 활성 탭과 같은 방법).
            let label = self.text(text, 13., fg);
            column = column.with_child(if row == selected {
                Container::new(label)
                    .with_background_color(palette::SELECTED_BG)
                    .with_uniform_padding(1.)
                    .finish()
            } else {
                Container::new(label).with_uniform_padding(1.).finish()
            });
        }
        column
    }

    /// 키 도움말 화면(패리티 G2).
    ///
    /// 내용은 [`keymap::key_help_lines`](base::keymap::key_help_lines) 가
    /// 만든다 — 표가 core 에 있으므로 도움말도 거기서 만든다. 뷰가 각자 조립하면 **한쪽
    /// 도움말만 낡고**, 그건 "이 키가 있는데 왜 안 되지"로 나타난다.
    ///
    /// 여기서 정하는 것은 색과 **높이 예산**뿐이다. 캔버스가 쓰던 높이를 넘으면 아래
    /// 요약 구역이 화면 밖으로 밀린다(플랜 화면과 같은 규칙).
    fn render_keys(&self, column: Flex) -> Flex {
        let lines = base::keymap::key_help_lines(&self.config.binds);
        let (cols, _) = self.state.composite().map_or((80, 12), |c| c.size());
        let budget = self.panel_budget();
        let start = self.screens.scroll().min(lines.len().saturating_sub(1));
        let mut column = column;
        for (key, what) in lines.iter().skip(start).take(budget) {
            let mut row = Flex::row().with_main_axis_size(MainAxisSize::Min).with_spacing(8.);
            // 소제목 줄은 설명이 비어 있다 — 그 줄은 흐리게, 키 줄은 강조로 가른다.
            if what.is_empty() {
                row = row.with_child(self.text(key.clone(), 12., palette::DIM));
            } else {
                row = row.with_child(self.text(key.clone(), 13., palette::CYAN));
                row = row.with_child(self.text(
                    footer::elide(what, cols.saturating_sub(20) as usize),
                    13.,
                    palette::FG,
                ));
            }
            column = column.with_child(row.finish());
        }
        column
    }

    /// 플랜 전문·거부 사유 전용 화면(캔버스를 덮는다).
    ///
    /// **덮되 크기는 안 건드린다.** 이 화면이 열려도 서버에 알린 캔버스 크기는 그대로라
    /// 닫는 순간 원래 화면이 그대로 돌아온다 — 크기를 바꿨다면 새 크기를 알리고 다시
    /// 받아야 하고, 그 왕복 동안 화면이 출렁인다(TUI 와 같은 규칙).
    ///
    /// 무엇을 보일지는 [`detail_lines`] 가 정한다. 여기서 정하는 것은 색과 **높이 예산**
    /// 뿐이다 — 캔버스가 쓰던 높이를 넘으면 아래 요약 구역이 화면 밖으로 밀려, 이 화면을
    /// 여는 것만으로 다른 것이 사라지는 셈이 된다. **잘랐으면 잘랐다고 말한다.**
    fn render_detail(&self, column: Flex) -> Flex {
        let lines = detail_lines(&self.claude);
        if lines.is_empty() {
            return column
                .with_child(self.text(t("보여 줄 플랜도 거부도 없다"), 13., palette::DIM));
        }
        let (cols, _) = self
            .state
            .composite()
            .map_or((80, 12), |c| c.size());
        let budget = self.panel_budget();
        let cut = lines.len() > budget;
        let mut column = column;
        for (text, kind) in lines.into_iter().take(budget.saturating_sub(cut as usize)) {
            let color = match kind {
                DetailKind::PlanHead => palette::BLUE,
                DetailKind::DeniedHead => theme::TURN,
                DetailKind::Body | DetailKind::Blank => palette::FG,
            };
            column = column.with_child(self.text(
                footer::elide(&text, cols as usize),
                13.,
                color,
            ));
        }
        if cut {
            column = column.with_child(self.text(t("… (잘림)"), 12., palette::DIM));
        }
        column
    }

    /// 캔버스 좌표계를 재는 자리표. 렌더가 여기에 **한 글자의 실제 사각형**을 남기고,
    /// 마우스 핸들러가 그것으로 픽셀 → 셀을 푼다.
    ///
    /// # 왜 계산하지 않나
    ///
    /// 글자 폭·줄 높이·크롬 높이를 우리가 계산하면 **렌더와 어긋난다**. TUI 가 P7
    /// 슬라이스 5에서 배운 것이 정확히 이것이다 — "좌표 보정은 계산하지 않고 렌더가
    /// 남긴 값을 쓴다"(위에 붙는 크롬 줄 수가 상황마다 다르다: 끊김 알림·오류·대기 문구).
    /// GUI 는 거기에 글꼴 문제까지 겹친다: 폴백 글꼴이 걸리면 우리가 고른 글꼴의 폭이
    /// 아니다.
    ///
    /// `Text::with_saved_char_position` 이 남기는 사각형은 그 글자의 **가로 전진폭과 줄
    /// 높이**다. 그래서 자리표 하나로 원점·칸너비·줄높이가 전부 나온다.
    const CELL_PROBE: &'static str = "pytmux:cell-probe";

    /// 아래 요약 구역이 시작하는 자리. 캔버스가 어디까지 쓸 수 있는지를 여기서 잰다.
    const FOOTER_PROBE: &'static str = "pytmux:footer-probe";

    /// 판 안 한 줄이 쓸 수 있는 **표시 폭**(칸) — 넘치면 자른다(§10-21ⓚ2).
    ///
    /// 폭을 픽셀로 못박았으므로(`with_width`) 줄도 그 안에 들어와야 한다. 픽셀↔칸은
    /// 글꼴이 정하는데 그 값은 그리는 중에만 알 수 있어, 여기서는 **넉넉한 칸 수**를
    /// 상수로 둔다 — 자르는 목적은 "판을 밀고 나가지 않게"이지 정확한 우변 정렬이
    /// 아니다(그건 ⓙ 슬라이스가 셀 격자를 잡을 때 이야기다).
    const PANEL_COLS: usize = 110;

    /// 판 안 두 줄 사이 간격(px). **한 벌**이라야 하는 이유가 있다(pytmux-157):
    /// 팔레트에서 접힌 설명은 항목 안쪽 열에 들고, 그 열이 이 값과 다른 행간을 쓰면
    /// 접힌 항목만 높이가 어긋나 「예산 = 줄 수」 셈과 실제 픽셀이 갈린다.
    const PANEL_ROW_SPACING: f32 = 2.;

    /// 팔레트 칸 폭(칸 수) — 이름·옵션(§10-21ⓞ). 설명은 남는 폭을 다 쓴다.
    ///
    /// 상수인 이유: 내용으로 정하면 필터를 칠 때마다 칼럼이 흔들린다(판 기하를 내용에서
    /// 뗀 규칙과 같은 이유). 값은 정본 표의 가장 긴 이름(`select-pane`·`display-message`
    /// 류)이 안 잘리는 선에서 골랐다.
    const PAL_NAME_COLS: usize = 22;
    const PAL_OPTS_COLS: usize = 10;

    /// 지나가는 말이 하단 한 줄에 머무는 시간(§10-21ⓦ⑴).
    ///
    /// 8초인 이유: 복사 결과("20 chars copied")는 한 번 눈에 들어오면 끝이라 짧아도
    /// 되지만, 오류는 **읽고 판단할 시간**이 필요하다. 둘을 다른 값으로 두면 "왜 이건
    /// 빨리 사라지지"가 되므로 긴 쪽에 맞춘다 — 놓쳐도 이력에 남는다(그 줄을 누르면
    /// 그 화면으로 간다).
    const FLASH_TTL: std::time::Duration = std::time::Duration::from_secs(8);

    /// 창 가장자리 여백(렌더의 `with_uniform_padding`). 이 값만 우리가 정한 것이라 안다.
    const PAD: f32 = 8.;

    /// 판 안쪽 여백과 테두리 두께(px). **폭 셈이 이 둘을 뺀다**(`panel_inner_width`) —
    /// 글자로 두 번 적으면 안쪽 폭과 바깥 폭이 조용히 갈린다(pytmux-158).
    const PANEL_PAD: f32 = 14.;
    const PANEL_BORDER: f32 = 1.;

    /// 판의 바깥 폭(px). **화면이 정하고 내용은 못 정한다.**
    ///
    /// 나머지 판은 좁게 둔다(760) — 창 끝까지 퍼지면 칸 사이가 벌어진다. 「내용이 못
    /// 정한다」는 규칙은 [[pytmux-158]] 이 세운 것이고 그대로 살아 있다.
    ///
    /// # 팔레트만 **담는 그릇**을 따라간다 (pytmux-368)
    ///
    /// 정본의 `esc :` 는 **단말 좌우 폭 전체**를 쓴다. 우리는 900px 상수였고, 캔버스가
    /// 그보다 훨씬 넓은 창에서는 목록이 가운데 갇혀 설명이 더 일찍 접혔다 —
    /// 사용자 요청은 *"위치와 크기를 맞춰주세요"* 다.
    ///
    /// ⛔ 그릇은 **창이 아니라 터미널 캔버스**다: 정본이 말하는 「단말 전체」가 곧
    /// 캔버스이고, 창을 쓰면 크롬(탭바·상태줄)까지 폭에 끼어 정본과 결이 갈린다.
    ///
    /// ⚠ 이건 「빠뜨린 것」이 아니라 **종전 판단을 뒤집는 것**이다(그 주석이 반대 근거를
    /// 적어 두고 있었다: *"창 끝까지 퍼지면 칸 사이가 벌어진다"*). 그 걱정은
    /// [`palette_cols`](Self::palette_cols) 가 푼다 — 이름·옵션 칸은 **상수 폭**이라
    /// 넓어진 자리는 전부 설명 칸이 먹는다. 곧 이름과 설명 사이는 안 벌어지고,
    /// 늘어난 폭은 접히던 설명이 펴지는 데 쓰인다(정본 첨부와 같은 모양).
    ///
    /// 캔버스를 아직 못 받았으면 종전 상수로 떨어진다 — 첫 프레임 전에도 판은 뜬다.
    fn panel_width(&self, screen: Screen) -> f32 {
        if screen != Screen::Commands {
            return 760.;
        }
        match (self.state.composite().map(|c| c.size().0), self.cell_px.get()) {
            (Some(cols), Some((cell_w, _))) if cols > 0 && cell_w > 0. => {
                (cols as f32 * cell_w).max(760.)
            }
            _ => 900.,
        }
    }

    /// 판 **안쪽**(여백·테두리를 뺀) 폭 — 줄 하나가 실제로 쓸 수 있는 자리.
    fn panel_inner_width(&self, screen: Screen) -> f32 {
        self.panel_width(screen) - 2. * (Self::PANEL_PAD + Self::PANEL_BORDER)
    }

    /// 팔레트 한 줄이 쓸 수 있는 **칸 수** — 안쪽 폭에서 나온다(pytmux-368).
    ///
    /// [`PANEL_COLS`](Self::PANEL_COLS) 상수를 안 쓰는 이유: 폭이 이제 캔버스를 따라
    /// 움직이는데 칸 수만 110 에 묶여 있으면, 넓어진 자리를 **아무도 안 쓴다**(판만
    /// 넓어지고 글은 그대로 접힌다). 픽셀과 칸을 한 값에서 유도해야 둘이 안 갈린다.
    ///
    /// 글꼴 크기를 아직 모르면 상수로 떨어진다(그리기 전에는 셀 폭을 알 수 없다).
    fn palette_cols(&self) -> usize {
        match self.cell_px.get() {
            Some((cell_w, _)) if cell_w > 0. => {
                ((self.panel_inner_width(Screen::Commands) / cell_w) as usize)
                    .max(Self::PAL_NAME_COLS + Self::PAL_OPTS_COLS + 12)
            }
            _ => Self::PANEL_COLS,
        }
    }

    /// 이벤트의 픽셀 좌표를 셀 좌표로. 자리표가 아직 없으면 `None`.
    fn cell_from_event(
        evt: &warpui::presenter::EventContext<'_>,
        position: warpui::geometry::vector::Vector2F,
    ) -> Option<(u16, u16)> {
        let probe = evt.element_position_by_id(Self::CELL_PROBE);
        let cell = probe.and_then(|p| Self::cell_at(p, position.x(), position.y()));
        // ★ 창을 볼 수 없는 자리에서 **좌표 보정의 유일한 관측점**이다. 한 번만 남긴다.
        //
        // 이게 없으면 "경계선이 안 끌린다"의 원인이 ⑴자리표가 안 남았나 ⑵원점이 틀렸나
        // ⑶경계선을 못 맞췄나 중 어느 것인지 못 가른다 — 실제로 한 번 그 자리에 섰다.
        static ONCE: std::sync::Once = std::sync::Once::new();
        ONCE.call_once(|| match probe {
            Some(p) => log::info!(
                "셀 자리표: 원점 ({:.1},{:.1}) 칸 {:.2}x{:.2} · 첫 클릭 ({:.1},{:.1}) → {cell:?}",
                p.origin_x(),
                p.origin_y(),
                p.width(),
                p.height(),
                position.x(),
                position.y()
            ),
            None => log::warn!(
                "셀 자리표가 없다 — 마우스를 셀 좌표로 못 푼다(캔버스가 아직 없거나                  ASCII 글자가 한 줄에도 없다)"
            ),
        });
        cell
    }

    /// 픽셀 좌표를 캔버스 셀 좌표로. 잴 수 없으면 `None`.
    ///
    /// **모르면 모른다고 한다.** 자리표가 아직 안 남았거나(첫 프레임) 이상한 값이면
    /// 클릭을 짐작해서 처리하지 않는다 — 엉뚱한 패널로 포커스가 가는 것은 아무 일도
    /// 안 일어나는 것보다 나쁘다.
    fn cell_at(probe: warpui::geometry::rect::RectF, px: f32, py: f32) -> Option<(u16, u16)> {
        let (w, h) = (probe.width(), probe.height());
        if !(w.is_finite() && h.is_finite()) || w <= 0.5 || h <= 0.5 {
            return None;
        }
        let col = ((px - probe.origin_x()) / w).floor();
        let row = ((py - probe.origin_y()) / h).floor();
        // 캔버스 위(탭바)·왼쪽 여백은 캔버스가 아니다.
        if col < 0. || row < 0. {
            return None;
        }
        Some((col as u16, row as u16))
    }

    /// 떠 있는 화면 하나를 플로팅 판으로(N2) — 틀(제목·힌트·표면·라운드·그림자)은
    /// 여기서 정하고, **내용은 종전 `render_*` 그대로**다(문자 본문 — 줄 위젯화는 N5).
    ///
    /// 어느 화면이 어느 내용을 그리는가의 정본은 아래 match 하나다 — 화면 종류가 늘면
    /// 여기서 컴파일이 운다(TUI 와 같은 배치).
    fn render_screen_panel(&self, screen: Screen) -> Box<dyn Element> {
        // ★ 작성창의 `Esc` 메뉴 모드는 **머리줄이 유일한 신호**다(TUI 와 같은 처방 —
        // 파이썬은 문구만 바꿨을 때 "모드 전환을 알 수 없다"는 보고를 받았다).
        let esc_menu = self.screens.editor().is_some_and(|e| e.esc_mode());
        let mut header = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_cross_axis_alignment(CrossAxisAlignment::Center)
            .with_spacing(8.);
        if esc_menu {
            header = header.with_child(self.chip(t(Screen::COMPOSE_ESC_BADGE), theme::ERROR));
        }
        // 확인 판은 **물음이 제목을 정한다**(정본과 같다) — 마지막 탭이면 `pytmux 종료`.
        // 판을 여는 순간 가장 먼저 읽히는 글이라, 거기서 손이 멈춘다.
        // ★ 플러그인이 준 판은 **제목의 주인이 스펙**이다(`base::screens` 가 그렇게 적어
        //   뒀는데 여기서 안 읽고 있었다 — P4 부터 mdir·ncd·p4changes 가 전부
        //   `플러그인 화면` 이라는 한 제목으로 떴다. 어느 판을 열었는지 화면이 말해 주지
        //   않으면 스펙이 제목을 싣는 뜻이 없다). 스펙이 안 주면 종전 폴백 그대로다.
        let spec_title = (screen == Screen::PluginView)
            .then(|| self.state.plugin_screen().map(|s| s.say_title()))
            .flatten()
            .filter(|t| !t.is_empty())
            // 검색 결과의 진짜 제목은 **회신이 준다**(쿼리+건수 — 정본 `headline_text()`).
            // 상한·무응답 상류는 여기 안 싣는다 — 판 안(`notes()`)의 몫이다.
            .or_else(|| {
                (screen == Screen::SearchResults)
                    .then(|| self.state.search_results().map(|sr| sr.headline()))
                    .flatten()
            });
        let title = self.screens.confirm_title().unwrap_or(screen.title());
        header = header.with_child(match spec_title {
            Some(t) => self.text(t, 14., palette::FG),
            None => self.ui_text(title, 14., palette::FG),
        });
        // ★ 힌트는 **판 아래**다(정본과 같은 틀 — 대조 문서 §「팝업 공통」). 제목 옆에
        //   붙이면 제목줄이 길어져 제목이 밀리고, 눈이 제목·힌트·본문을 한 줄에서
        //   나눠 읽어야 한다. 아래로 내리면 "제목 → 본문 → 이 화면에서 쓸 키" 순서다.
        let hint = if esc_menu { t(Screen::COMPOSE_HINT_ESC) } else { screen.hint() };
        // 안내도 같다 — 그 판에서 무슨 키가 무엇을 하는지는 플러그인이 안다
        // (`ncd` 의 `c`, 달력의 `‹` 처럼 판마다 다르다).
        let spec_hint = (screen == Screen::PluginView)
            .then(|| self.state.plugin_screen().map(|s| s.say_hint()))
            .flatten()
            .filter(|h| !h.is_empty())
            // ★ 팔레트에서 고른 명령이 **인자를 받으면 그 문구를 여기 싣는다**
            //   (pytmux-7 요구 ⑶ — "입력을 방해하지 않는 선에서 도움말"). 판이 안
            //   늘어나는 자리가 여기뿐이다: 안내줄은 원래 늘 있다.
            .or_else(|| (screen == Screen::Commands).then(|| self.palette_arg_hint()).flatten());
        let mut column = Flex::column()
            .with_main_axis_size(MainAxisSize::Min)
            // ★ **줄이 판 폭을 채운다**(pytmux-372). 종전에는 줄마다 자연폭이라 고른 줄의
            //   띠가 **글자 폭만큼만** 칠해졌다 — 정본은 줄 전체를 띠로 채우고, 그 차이가
            //   제보에서 "시인성이 떨어진다"의 큰 몫이었다(색보다 면적이 먼저 읽힌다).
            //   판 폭은 아래 `PanelBox` 가 정한 값이라 늘릴 대상이 분명하다.
            //   ⚠ 정보 판의 줄은 이미 [`full_row`](Self::full_row) 로 채운다(pytmux-373) —
            //     둘이 겹쳐도 뜻이 같아 그림은 하나다. 그 helper 는 **그 판만** 덮었다.
            .with_cross_axis_alignment(CrossAxisAlignment::Stretch)
            .with_spacing(Self::PANEL_ROW_SPACING)
            .with_child(
                Container::new(header.finish())
                    .with_padding_bottom(8.)
                    .with_border(Border::bottom(1.).with_border_color(theme::BORDER))
                    .finish(),
            );
        column = match screen {
            Screen::Compose => self.render_compose(column),
            Screen::InfoTabs => self.render_info_tabs(column),
            Screen::ClaudeDetail => self.render_detail(column),
            Screen::Keys => self.render_keys(column),
            Screen::Tabs => self.render_tab_list(column),
            Screen::Tree => self.render_tree_list(column),
            Screen::Buffers => self.render_buffer_list(column),
            Screen::Prompt | Screen::Confirm => self.render_prompt(column, screen),
            Screen::Commands => self.render_palette(column),
            Screen::Settings => self.render_settings(column),
            Screen::Cursor => self.render_cursor(column),
            // 읽는 판 — 줄은 `base::diag` 가 짓고 값은 `runtime_stats()` 가 모은다
            // (pytmux-457). 정본이 이 표를 `InfoScreen` 에 띄우는 것과 같은 손이다.
            Screen::DebugStats => self
                .runtime_stats()
                .lines()
                .into_iter()
                .skip(self.screens.scroll())
                .fold(column, |c, row| c.with_child(self.text(row, 13., palette::FG))),
            Screen::Plugins => self.render_plugins(column),
            Screen::PluginView => self.render_plugin_view(column),
            Screen::SearchResults => self.render_search_results(column),
            Screen::Menu => self.render_menu(column),
            Screen::Notices => self.render_notices(column),
            Screen::Summary => self.render_summary(column),
            Screen::Options => self.render_options(column),
            Screen::Hooks => self
                .config
                .hooks
                .lines()
                .iter()
                .skip(self.screens.scroll())
                .fold(column, |c, row| {
                    c.with_child(self.text(row.clone(), 13., palette::FG))
                }),
            Screen::Layouts => self.render_layouts(column),
            Screen::MergeRemote => self.render_merge_remote(column),
            Screen::ShellOutput => self
                .state
                .shell_output()
                .iter()
                .skip(self.screens.scroll())
                .fold(column, |c, row| {
                    c.with_child(self.text(row.clone(), 13., palette::FG))
                }),
            // 날 값(`serialize_ok: true`)이 아니라 **판정**을 보인다. 그리고 안전하면
            // 그 자리에서 재시작한다(§10-21ⓓ3) — 판이 읽는 것에서 고르는 것이 됐다.
            Screen::RestartCheck => {
                let (safe, rows) = proto::info::restart_check_lines(&self.state);
                let column = rows.into_iter().fold(column, |c, row| {
                    c.with_child(self.text(row, 13., palette::FG))
                });
                column.with_child(self.restart_button(safe))
            }
            // 서버 줄 + 클라 줄 + 빌드 이름(§10-21ⓐ3). 줄을 짓는 것은 판정이라
            // proto 가 한다 — 뷰가 지으면 두 클라가 다른 말을 하기 시작한다.
            Screen::Version => proto::info::version_lines(&self.state)
                .into_iter()
                .fold(column, |c, row| c.with_child(self.text(row, 13., palette::FG))),
            // 자동 재개 설명(pytmux-183) — 줄은 proto 가 짓는다(정본과 **같은 글**).
            Screen::Autoresume => proto::info::autoresume_lines(&self.state)
                .into_iter()
                .fold(column, |c, row| c.with_child(self.text(row, 13., palette::FG))),
        };
        // 힌트 줄(판 바닥) — 위 구분선의 짝이다.
        let column = column.with_child(
            Container::new(match spec_hint {
                Some(h) => self.hint_text(h, self.font, 11., palette::DIM),
                None => self.hint_text(hint, self.ui_font, 11., palette::DIM),
            })
                .with_padding_top(8.)
                .with_border(Border::top(1.).with_border_color(theme::BORDER))
                .finish(),
        );
        // ★ **판 폭을 못박는 자리는 여기 하나다**(§10-21 ⓗ·ⓢ·ⓥ·ⓐ2·ⓚ2 · pytmux-158).
        //
        //   `PanelBox` 가 «잰 값이 아니라 정한 값»을 돌려주므로 판 폭이 내용에서 완전히
        //   떨어진다. `Clipped` 는 그 폭을 넘겨 그린 글자를 판 밖으로 안 내보낸다 —
        //   줄이는 것은 각 줄의 `elide` 몫이고 이건 **마지막 그물**이다.
        //   ⛔ 종전에는 판 바깥에 `ConstrainedBox::with_width` 하나였는데 그것으로는
        //   **아래쪽 한도만** 지켜졌다(왜인지는 `PanelBox` 의 머리말). 그래서 긴 줄
        //   하나가 판을 그만큼 넓혔다.
        //   ⚠ 작성창은 예외다 — 아래에서 `Expanded` 로 **전폭**을 받는다.
        let body = if screen == Screen::Compose {
            column.finish()
        } else {
            Clipped::new(
                PanelBox::new(column.finish(), self.panel_inner_width(screen)).finish(),
            )
            .finish()
        };
        let panel = Container::new(body)
            .with_background_color(theme::ELEV)
            .with_uniform_padding(Self::PANEL_PAD)
            .with_corner_radius(theme::PANEL_RADIUS)
            .with_border(Border::all(Self::PANEL_BORDER).with_border_color(theme::BORDER))
            .with_drop_shadow(theme::panel_shadow())
            .finish();
        // ★ 판이 **어디에 서는지는 core 가 정한다**(`Screen::anchor` — 정본 CSS `align`
        //   을 옮긴 표). 뷰가 각자 정하면 같은 화면이 클라마다 다른 데 뜬다: 실제로
        //   정본은 화면마다 다르고 우리는 전부 가운데여서, `esc :` 프롬프트가 정본에서는
        //   바닥인데 여기서는 가운데 떴다(사용자 지시 2026-08-01).
        //
        //   - 바닥: `:` 프롬프트 · 팔레트 · 작성창 — **치던 흐름의 연장**이다. 손과 눈이
        //     화면 아래에 있는데 판이 가운데나 위에 뜨면 시선이 한 번 튄다.
        //   - 위: 읽는 판(버전·훅·셸 결과·재시작 점검·키 도움말) — 긴 글이라 첫 줄이 늘
        //     같은 자리라야 한다(정본 `InfoScreen`).
        //   - 가운데: 고르러 여는 판.
        let anchor = screen.anchor();
        // 폭은 위에서 이미 못박혔다(`panel_width` · `PanelBox`) — 여기서는 자리만 잡는다.
        // ⛔ 여기에 `ConstrainedBox::with_width` 를 다시 얹지 말 것: 폭을 말하는 자리가
        //    둘이 되고, 둘 중 **아무 일도 안 하는 쪽**이 판을 정하는 것처럼 읽힌다
        //    (그 오독이 pytmux-158 이었다).
        if anchor == Anchor::AtPaneCursor {
            return self.place_at_pane_cursor(panel);
        }
        let aligned = Align::new(panel);
        match anchor {
            Anchor::Bottom => aligned.bottom_center().finish(),
            Anchor::Top => aligned.top_center().finish(),
            Anchor::Middle => aligned.finish(),
            // 위에서 이미 돌아갔다 — 여기 오면 갈래를 빠뜨린 것이다.
            Anchor::AtPaneCursor => unreachable!("AtPaneCursor 는 위에서 처리한다"),
        }
    }

    /// 판을 **활성 패널의 커서 줄**에 세운다(pytmux-370 · 정본 `ComposePromptScreen`).
    ///
    /// 정본이 하는 산수를 그대로 옮긴다(`on_mount`):
    ///
    /// - 가로 — 패널 **안쪽** x 에 맞추고 폭도 그 패널의 폭이다. 종전에는 창 전폭이라
    ///   판이 패널 밖으로 삐져나왔다.
    /// - 세로 — 입력 줄이 프롬프트 줄 **한 칸 아래**에 오게 바닥에서 띄운다. 여러 줄로
    ///   늘면 그 줄을 고정한 채 **위로** 자란다(그래서 바닥 정렬 + 아래 여백이다).
    ///
    /// ⛔ **못 재면 짐작하지 않는다** — 첫 프레임이라 자리표가 없거나 서버가 준 커서가
    ///    패널 밖이면 종전 자리(바닥 가운데)로 내려간다. 자리를 짐작해 그리면 그 그림이
    ///    새 거짓말이 되고, 그 부류는 이 저장소가 이미 여러 번 물렸다.
    fn place_at_pane_cursor(&self, panel: Box<dyn Element>) -> Box<dyn Element> {
        let Some((left, bottom, width)) = self.pane_cursor_box() else {
            return Align::new(panel).bottom_center().finish();
        };
        let sized = ConstrainedBox::new(panel).with_width(width).finish();
        Align::new(
            Container::new(sized)
                .with_padding_left(left)
                .with_padding_bottom(bottom)
                .finish(),
        )
        .bottom_left()
        .finish()
    }

    /// `(왼쪽 여백, 아래 여백, 판 폭)` px — 전부 **잰 값**에서 나온다. 못 재면 `None`.
    fn pane_cursor_box(&self) -> Option<(f32, f32, f32)> {
        let (cell_w, cell_h) = self.cell_px.get()?;
        let cv = self.canvas_px.get()?;
        let pane = self.state.active_pane()?;
        let (cx, cy) = self.state.pane_cursor(pane)?;
        // `pane_rect` 는 테두리를 뺀 **안쪽**이라 정본의 「패널 안쪽 x·폭」과 같은 값이다.
        let (px, py, pw, ph) = self.state.pane_rect(pane)?;
        if cx >= pw || cy >= ph {
            return None; // 커서가 패널 밖 — 그런 프레임에 자리를 지어내지 않는다
        }
        let left = cv.left + px as f32 * cell_w;
        // 입력 줄이 커서 줄 **바로 아래** 칸에 오게: 판의 아래쪽 끝이 그 줄의 끝이다.
        let panel_bottom = cv.top + (py + cy + 2) as f32 * cell_h;
        let bottom = (cv.win_h - panel_bottom).max(0.);
        Some((left, bottom, (pw as f32 * cell_w).max(cell_w)))
    }

    /// 캔버스의 **경계 문자 칸**을 실제 선으로 옮길 목록(2026-07-31 사용자 지시).
    ///
    /// # 왜 캔버스를 훑나(배치를 안 보고)
    ///
    /// 어느 칸이 테두리인지는 이미 **캔버스가 정해 놓았다** — 서버 배치 → `draw_frames` →
    /// 합성(`┬`·`┼`)까지 끝난 결과가 그 칸의 글자다. 배치에서 다시 계산하면 그 합성 규칙을
    /// **두 번째로** 구현하게 되고, 그 둘은 반드시 갈린다. 색도 그 칸의 것을 그대로 쓴다
    /// (활성/비활성 판정이 이미 들어 있다).
    ///
    /// 분할 경계 칸은 **뺀다** — 거기는 스플리터 바가 자기 그림을 그리고, 선까지 겹치면
    /// 잡는 자리가 두 겹으로 보인다.
    /// # 색을 우리가 덮어쓰는 자리 하나 (§10-21ⓩ)
    ///
    /// 원격·degraded 는 **서버가 안 칠한다** — 정본도 클라가 자기 합성 단계에서 칠한다
    /// (`clientio.py` 의 `_box_style_sig`). 그래서 그 두 뜻일 때만 칸 색을 덮어쓰고,
    /// 평소(`Local`)는 캔버스 색을 그대로 둔다. 무엇이 우선인지는 core 가 정한다
    /// ([`base::tint::border`]).
    fn frame_segments(
        canvas: &proto::canvas::Canvas,
        layout: Option<&proto::message::Layout>,
        tint: base::tint::BorderTint,
    ) -> Vec<crate::splitter::Seg> {
        let Some(layout) = layout else {
            return Vec::new();
        };
        // ★ **크롬 칸만** 옮긴다 — 패널 **안**의 선문자는 그 앱의 글자다. 캔버스를 통째로
        //   훑으면 `htop` 이 그린 `┌` 까지 선으로 바꾸게 되는데, 그건 "테두리를 네이티브로"가
        //   아니라 남의 화면을 고쳐 그리는 것이다.
        let mut chrome: std::collections::BTreeSet<(usize, usize)> = Default::default();
        // 활성 패널의 테두리 칸 — 원격 분홍이 두 단이라 어느 칸이 활성인지 알아야 한다.
        // 두 패널이 나눠 쓰는 칸(이음새)은 **활성 쪽이 이긴다**: 정본도 활성 테두리를
        // 위에 그린다(밝은 쪽이 "여기가 키를 받는다"를 말한다).
        let mut active_cells: std::collections::BTreeSet<(usize, usize)> = Default::default();
        for pane in &layout.panes {
            let Some([x, y, w, h]) = pane.boxrect else { continue };
            let (x, y, w, h) = (x as usize, y as usize, w as usize, h as usize);
            if w < 2 || h < 2 {
                continue;
            }
            for gx in x..x + w {
                chrome.insert((gx, y));
                chrome.insert((gx, y + h - 1));
                if pane.active {
                    active_cells.insert((gx, y));
                    active_cells.insert((gx, y + h - 1));
                }
            }
            for gy in y..y + h {
                chrome.insert((x, gy));
                chrome.insert((x + w - 1, gy));
                if pane.active {
                    active_cells.insert((x, gy));
                    active_cells.insert((x + w - 1, gy));
                }
            }
        }
        // 제목줄(`pane-border-status`)의 채움도 `─` 다 — 같은 크롬이다.
        for bar in &layout.titlebars {
            for gx in bar.x as usize..(bar.x + bar.w) as usize {
                chrome.insert((gx, bar.y as usize));
            }
        }
        // 분할 경계 칸 중 **곧은 선**은 뺀다 — 거기는 스플리터 바가 자기 그림을 그리고,
        // 선까지 겹치면 잡는 자리가 두 겹으로 보인다.
        //
        // ★ **이음새 칸(`┬`·`┴`·`├`·`┤`·`┼`)은 빼지 않는다**(§10-21ⓟ). 서버가 주는
        //   경계 사각형은 노드 rect **전체**를 덮으므로 그 양 끝이 가로 테두리와 만나는
        //   칸이고, 그 칸까지 바에게 주면 가로 테두리가 거기서 끊긴 채 세로선만 지나가
        //   **T 자로 튀어나온 것**처럼 보인다. 제보의 스크린샷이 그 그림이었다.
        //
        //   갈래는 **비트로** 가른다: 경계와 같은 축의 비트만 있으면 곧은 선(바의 것),
        //   수직 성분이 섞였으면 이음새(테두리의 것)다.
        let divider_owns = |x: usize, y: usize, bits: u8| {
            const UP_DOWN: u8 = 0b1100;
            const LEFT_RIGHT: u8 = 0b0011;
            layout.dividers.iter().any(|d| {
                let inside = x >= d.x as usize
                    && x < (d.x + d.w) as usize
                    && y >= d.y as usize
                    && y < (d.y + d.h) as usize;
                // 좌우 분할(`lr`)의 경계는 세로선이다.
                let straight = if d.orient == "lr" { bits == UP_DOWN } else { bits == LEFT_RIGHT };
                inside && straight
            })
        };
        chrome
            .into_iter()
            .filter_map(|(x, y)| {
                let cell = canvas.cell(x, y)?;
                let bits = proto::canvas::box_bits(cell.ch)?;
                if divider_owns(x, y, bits) {
                    return None;
                }
                // 평소에는 캔버스 색을 그대로(서버의 활성/비활성 판정이 들어 있다).
                // 원격·degraded 만 우리가 덮어쓴다 — 그 둘은 서버가 모르는 사실이다.
                let color = tint_color(tint, active_cells.contains(&(x, y)))
                    .unwrap_or_else(|| colors(&cell.style).0);
                Some(crate::splitter::Seg { x: x as u16, y: y as u16, bits, color })
            })
            .collect()
    }

    /// 지금 테두리가 무엇을 말해야 하나 — 재료를 모아 core 에 묻는다(§10-21ⓩ).
    ///
    /// 원격은 **이름이 아니라 탭의 `remote` 플래그**로 본다(서버가 그렇게 해 뒀다 —
    /// 사용자가 탭 이름을 `⇄…` 로 지어도 속지 않으려고). degraded 는 우리가 재는 값이다
    /// (`rtt.degraded` — pong 왕복이 임계를 넘으면 켜진다).
    fn border_tint(&self) -> base::tint::BorderTint {
        base::tint::border(self.state.active_tab_is_remote(), self.state.rtt().degraded)
    }

    /// 구역 사이에 세우는 **자리표 줄**(§10-21ⓛ2).
    ///
    /// 왜 문자열 한 줄인가: 이 판은 스크롤을 **줄 수**로 센다(`screens.scroll()`). 선을
    /// 줄 밖의 무엇으로 두면 스크롤 셈에서 빠져 굴릴수록 어긋난다. 그래서 선도 한 줄을
    /// 차지하는 것으로 세고, 그릴 때만 글자 대신 선이 된다.
    ///
    /// 본문에 이 글자가 그대로 올 일은 없다(제어문자다) — 자료와 섞이지 않는다.
    const SECTION_MARK: &'static str = "\u{1}";

    /// 구역들을 자리표 줄로 이어 한 덩이로. 구역이 하나면 그대로다.
    fn join_sections(sections: &[String]) -> String {
        sections.join(&format!("\n{}\n", Self::SECTION_MARK))
    }

    /// 블록 문자 칸들을 **사각형**으로 옮긴다(§10-21ⓘ).
    ///
    /// # 왜 패널 안까지 옮기나 (테두리와 다른 판단)
    ///
    /// [`frame_segments`](Self::frame_segments) 는 **크롬 칸만** 옮긴다 — 패널 안의 `┌` 는
    /// 그 앱이 그린 그림이라 우리가 고쳐 그릴 것이 아니다. 블록은 반대다: `█` 의 뜻은
    /// "이 칸의 이만큼이 이 색"이 전부라, 사각형이 글리프보다 **더 정확한 그림**이다.
    /// 폴백 글꼴의 진폭에 맡기면 오히려 행마다 밀린다(제보 ⓘ 의 마스코트가 그 증상).
    fn block_cells(canvas: &proto::canvas::Canvas) -> Vec<crate::splitter::Block> {
        let (w, h) = canvas.size();
        let mut out = Vec::new();
        for y in 0..h {
            for x in 0..w {
                let Some(cell) = canvas.cell(x, y) else { continue };
                if cell.continuation {
                    continue;
                }
                // 사분면은 사각형이 **둘**이다(pytmux-177) — 칸 하나가 블록 여럿을 낸다.
                let Some(fills) = proto::canvas::block_fills(cell.ch) else {
                    continue;
                };
                let (fg, _) = colors(&cell.style);
                for fill in fills {
                    out.push(crate::splitter::Block {
                        x: x as u16,
                        y: y as u16,
                        fill: *fill,
                        color: fg,
                    });
                }
            }
        }
        out
    }

    /// 지금 화면이 요구하는 글자 선들 — `render` 가 오버레이에 넘기는 그 값.
    ///
    /// 규칙([`text_rules`](Self::text_rules))과 갈라 둔 이유는 **오라클**이다: 순수
    /// 함수만 재면 이걸 부르는 줄을 지워도 테스트가 통과한다(`client/CLAUDE.md` 가
    /// 경고하는 공허 통과). 이 함수는 서버 메시지 → 합성 캔버스 → 규칙까지를 한 줄로
    /// 묶으므로, 테스트가 **진짜 프레임을 먹여** 그릴 것이 생기는지 볼 수 있다.
    fn rule_marks(&self) -> Vec<crate::splitter::TextRule> {
        self.composite_for_paint()
            .as_ref()
            .map(Self::text_rules)
            .unwrap_or_default()
    }

    /// 그 앱이 그은 선 전부 — 밑줄(SGR 4 · pytmux-123)과 취소선(SGR 9 · pytmux-133).
    ///
    /// 한 칸이 **둘 다** 가질 수 있으므로 속성마다 따로 훑는다. 한 번에 훑으며 "선이
    /// 있나"로 묶으면 밑줄만 있는 칸과 취소선만 있는 칸이 한 구간으로 이어져, 없던
    /// 선이 생긴다.
    fn text_rules(canvas: &proto::canvas::Canvas) -> Vec<crate::splitter::TextRule> {
        let mut out = Self::rule_runs(canvas, crate::splitter::RuleAt::Under, |s| s.underline);
        out.extend(Self::rule_runs(canvas, crate::splitter::RuleAt::Through, |s| s.strike));
        out
    }

    /// `want` 가 켜진 칸들을 **이어진 구간**으로 모은다.
    ///
    /// # 왜 이게 없었나
    ///
    /// 서버는 이 속성들을 정상적으로 싣고 파서도 정상적으로 읽는다(`CellStyle`).
    /// 마지막 한 걸음, **칠하는 쪽만** 없었다 — `colors()` 는 fg·bg·reverse 만 본다.
    /// 그래서 아무 오라클도 안 울었다(스타일 왕복 테스트는 값이 살아 있는지만 본다).
    /// `man`·`less`·`git diff` 가 이 속성들을 쓰므로 실제로 정보가 사라졌다.
    ///
    /// # 왜 칸을 이어 붙이나
    ///
    /// 칸마다 따로 그리면 인접한 사각형 사이에 반픽셀 틈이 생겨 **점선처럼** 보인다.
    /// 색이 같고 붙어 있으면 한 구간이다. 색이 갈리면 끊는다 — 서로 다른 색의 선을
    /// 한 줄로 이으면 그중 하나가 거짓이 된다.
    ///
    /// 넓은 글자의 뒤 칸(`continuation`)도 **같은 스타일**을 갖고 있어 자연히 이어진다.
    fn rule_runs(
        canvas: &proto::canvas::Canvas,
        at: crate::splitter::RuleAt,
        want: impl Fn(&proto::style::CellStyle) -> bool,
    ) -> Vec<crate::splitter::TextRule> {
        let (w, h) = canvas.size();
        let mut out: Vec<crate::splitter::TextRule> = Vec::new();
        for y in 0..h {
            let mut run: Option<crate::splitter::TextRule> = None;
            for x in 0..w {
                let color = canvas
                    .cell(x, y)
                    .filter(|cell| want(&cell.style))
                    .map(|cell| colors(&cell.style).0);
                match (&mut run, color) {
                    // 이어진다 — 끝만 늘린다.
                    (Some(cur), Some(c)) if cur.color == c && cur.x1 == x as u16 => {
                        cur.x1 = x as u16 + 1;
                    }
                    // 끊겼거나 색이 갈렸다 — 앞 구간을 내려놓고 새로 연다.
                    (_, Some(c)) => {
                        out.extend(run.take());
                        run = Some(crate::splitter::TextRule {
                            y: y as u16,
                            x0: x as u16,
                            x1: x as u16 + 1,
                            color: c,
                            at,
                        });
                    }
                    (_, None) => out.extend(run.take()),
                }
            }
            out.extend(run);
        }
        out
    }

    /// 이 칸을 **글자 대신 사각형**으로 그리나 — `render_row` 가 비울 칸을 정할 때 쓴다.
    ///
    /// [`block_cells`](Self::block_cells) 와 **같은 판정**이라야 한다(테두리 쪽과 같은
    /// 규율): 한쪽만 바뀌면 글리프와 사각형이 겹쳐 보이거나 그림이 통째로 사라진다.
    fn block_cell_set(
        canvas: &proto::canvas::Canvas,
    ) -> std::collections::BTreeSet<(u16, u16)> {
        Self::block_cells(canvas)
            .into_iter()
            .map(|b| (b.x, b.y))
            .collect()
    }

    /// 런 하나를 **격자에 놓을 조각들**로 나눈다. 각 조각은 `(글자들, 칸 수)`.
    ///
    /// # 왜 나누나 (§10-21ⓙ)
    ///
    /// 캔버스는 한 줄을 스타일이 같은 **런 단위 문자열**로 셰이퍼에 넘겨 왔다 — 가로
    /// 자리를 **글리프의 전진폭**이 정한다는 뜻이다. 고정폭 글꼴에 있는 글자는 그것이
    /// 곧 칸너비지만, 한글은 폴백 글꼴(`Malgun Gothic` 등)에서 오고 그 진폭은 칸너비의
    /// 정수배가 아니다. 그러면 **그 뒤가 전부 밀린다** — 제보 ⓙ 의 "표 오른쪽 끝이
    /// 들쑥날쑥"이 그것이다.
    ///
    /// 그래서 **격자를 클라가 잡는다**: 고정폭이 보장되는 글자(인쇄 가능한 ASCII)는
    /// 이어 붙여 한 덩이로 두고, 그 밖의 글자는 **한 글자씩** 떼어 자기 칸에 못박는다.
    /// 못박는 일은 부르는 쪽이 한다(여기는 나누기만 — 그래야 글꼴 없이 시험된다).
    ///
    /// # 왜 ASCII 는 안 떼나
    ///
    /// 한 칸에 하나씩 엘리먼트를 만들면 80×24 만 해도 1,920 개다. ASCII 는 우리가 고른
    /// 고정폭 글꼴이 반드시 갖고 있고 그 진폭이 곧 칸너비라(자리표를 그 글자로 재는
    /// 이유가 그것이다) 이어 붙여도 자리가 안 밀린다. 화면의 대부분이 여기 속한다.
    fn grid_segments(text: &str) -> Vec<(String, usize)> {
        let mut out: Vec<(String, usize)> = Vec::new();
        let mut ascii = String::new();
        let mut ascii_cells = 0usize;
        for ch in text.chars() {
            let cells = proto::compose::char_advance(ch);
            // ★ **군집의 다음 조각은 앞 조각에 붙는다**(pytmux-407 ⓐ). 둘째 이모지·
            //   둘째 지역 지시자·피부톤 수정자는 제 폭이 있지만 **칸을 안 쓴다** —
            //   격자(서버)가 그렇게 세기 때문이고, 여기서 떼면 `👨‍👩‍👧` 가 셰이퍼에
            //   낱개로 들어가 **이모지 셋**으로 그려진다(그것이 이 이슈의 증상이었다).
            //
            //   ⚠ ASCII 를 모으는 중이면 볼 것이 없다 — 군집의 조각은 ASCII 가 아니고,
            //     ASCII 뒤에 붙는 것(키캡의 `U+20E3` 등)은 폭 0 이라 아래 갈래가 받는다.
            if ascii.is_empty()
                && let Some(last) = out.last_mut()
                && proto::compose::joins_previous(&last.0, ch)
            {
                last.0.push(ch);
                continue;
            }
            // ★ **폭 0 글자는 앞 조각에 얹는다**(pytmux-389). 변이 선택자·ZWJ·결합
            //   표시는 자기 칸이 없다 — 한 칸을 주면 그 줄이 한 칸씩 밀린다. 그리고
            //   **떼지 않고 붙여야** 셰이퍼가 `⚠`+`U+FE0F` 를 한 조각으로 받는다
            //   (갈라 놓으면 색 이모지가 흑백 글자로 그려진다).
            if cells == 0 {
                if !ascii.is_empty() {
                    ascii.push(ch);
                } else if let Some(last) = out.last_mut() {
                    last.0.push(ch);
                }
                // 줄 맨 앞에 온 폭 0 글자는 얹힐 데가 없다 — 버린다(놓을 칸이 없다).
                continue;
            }
            // 인쇄 가능한 ASCII 만 이어 붙인다 — 제어문자는 폭이 뭔지 알 수 없다.
            if ch.is_ascii() && !ch.is_ascii_control() {
                ascii.push(ch);
                ascii_cells += cells;
                continue;
            }
            if !ascii.is_empty() {
                out.push((std::mem::take(&mut ascii), ascii_cells));
                ascii_cells = 0;
            }
            out.push((ch.to_string(), cells));
        }
        if !ascii.is_empty() {
            out.push((ascii, ascii_cells));
        }
        out
    }

    /// `at` 칸에 놓인 **글자 하나**를 자기 조각으로 떼어 낸다(`pytmux/pytmux-161`).
    ///
    /// `base` 는 첫 조각이 시작하는 칸 번호다. `at` 이 이 조각들 밖이면 **그대로**
    /// 돌려준다 — 커서가 없는 런에서도 부를 수 있게 하려는 것이고, 그래야 부르는 쪽에
    /// "이 런에 커서가 있나"를 다시 세는 코드가 안 생긴다.
    ///
    /// # 왜 칸이 아니라 글자를 떼나
    ///
    /// 한글은 두 칸을 먹는다. 칸 단위로 자르면 그 글자를 반으로 쪼개게 되는데, 반쪽
    /// 글자는 그릴 수가 없다. 터미널도 같은 판정이다 — 커서는 **글자**에 놓인다.
    ///
    /// # 왜 폭이 안 밀리나
    ///
    /// ASCII 조각의 자연폭이 `칸수 × 칸너비`라는 전제([`grid_segments`] 머리말) 위에서,
    /// 한 조각을 셋으로 쪼개도 폭의 합은 같다. ASCII 가 아닌 글자는 애초에 한 글자가
    /// 한 조각이라 쪼갤 것이 없다.
    fn isolate_cell(
        segments: Vec<(String, usize)>,
        base: usize,
        at: usize,
    ) -> Vec<(String, usize)> {
        let mut out: Vec<(String, usize)> = Vec::with_capacity(segments.len() + 2);
        let mut col = base;
        let mut done = false;
        for (piece, cells) in segments {
            // 이미 뗐거나 이 조각 밖이면 그대로. 한 글자짜리도 더 쪼갤 것이 없다.
            if done || at < col || at >= col + cells || piece.chars().count() <= 1 {
                col += cells;
                out.push((piece, cells));
                continue;
            }
            let mut before = String::new();
            let mut before_cells = 0usize;
            let mut chars = piece.chars().peekable();
            // 이 조각에서 지금까지 본 글 — **군집 판정에 앞의 것이 필요하다**
            // (`joins_previous` 는 앞 글의 꼬리를 본다).
            let mut seen = String::new();
            while let Some(ch) = chars.next() {
                // 얹히는 글자는 칸을 안 쓴다 — 폭 0 이거나 군집이 이어질 때(pytmux-407 ⓐ).
                let w = if proto::compose::attaches(&seen, ch) {
                    0
                } else {
                    proto::compose::char_advance(ch)
                };
                seen.push(ch);
                // 혼자 떼면 그 글자와 갈라진다(pytmux-389) — 앞에 붙여 둔다.
                if w == 0 {
                    before.push(ch);
                    continue;
                }
                if col + before_cells + w > at {
                    if !before.is_empty() {
                        out.push((std::mem::take(&mut before), before_cells));
                    }
                    // 뗄 글자에 얹힌 것(변이 선택자 등)은 **함께** 뗀다.
                    let mut pick = ch.to_string();
                    while let Some(&next) = chars.peek() {
                        // 얹힌 것(변이 선택자 · 군집의 다음 조각)은 **함께** 뗀다 —
                        // 갈라 두면 커서 자리에서 군집이 쪼개진다.
                        if !proto::compose::attaches(&pick, next) {
                            break;
                        }
                        pick.push(next);
                        chars.next();
                    }
                    out.push((pick, w));
                    let rest: String = chars.collect();
                    if !rest.is_empty() {
                        out.push((rest, cells - before_cells - w));
                    }
                    done = true;
                    break;
                }
                before.push(ch);
                before_cells += w;
            }
            col += cells;
        }
        out
    }

    /// 이 칸을 **글자 대신 선**으로 그리나 — `render_row` 가 그 칸을 비울지 정할 때 쓴다.
    ///
    /// [`frame_segments`](Self::frame_segments) 와 **같은 판정**이라야 한다: 한쪽만 바뀌면
    /// 선이 두 겹으로 보이거나(글자가 남음) 테두리가 통째로 사라진다(선이 안 그려짐).
    fn frame_cells(
        canvas: &proto::canvas::Canvas,
        layout: Option<&proto::message::Layout>,
    ) -> std::collections::BTreeSet<(u16, u16)> {
        // 색은 안 보므로 tint 는 아무거나 — `Local` 이 "안 덮어쓴다"라 가장 싸다.
        Self::frame_segments(canvas, layout, base::tint::BorderTint::Local)
            .into_iter()
            .map(|seg| (seg.x, seg.y))
            .collect()
    }

    /// 스크롤한 패널의 **표시용 스크롤바**(§10-21ⓨ2) — 패널 오른쪽 테두리 위.
    ///
    /// # 언제 보이나
    ///
    /// **위로 올라간 패널에만** 선다. 늘 그리면 라이브 화면 오른쪽에 늘 꽉 찬 막대가
    /// 붙어 시끄럽고, 그건 "지금 어디쯤인가"를 말하려던 것과 반대다. 스크롤백이 없거나
    /// 라이브면 core 가 `None` 을 준다.
    ///
    /// # 왜 패널마다인가
    ///
    /// 스크롤은 **패널마다 따로** 산다(활성 패널만 그리면 옆 패널을 올려 둔 것을 잊는다).
    ///
    /// 판이 떠 있으면 안 그린다 — 캔버스가 스크림 아래로 내려가 있는데 그 위에 막대만
    /// 또렷하면 눈이 그리로 간다(마우스를 끊는 것과 같은 규칙).
    fn scroll_hints(&self) -> Vec<crate::splitter::ScrollHint> {
        if self.screens.top().is_some() {
            return Vec::new();
        }
        self.state
            .panes()
            .iter()
            .filter_map(|pane| {
                let scroll = self.state.pane_scroll(pane.id)?;
                let top = self.state.pane_top(pane.id)?;
                let (start, len) =
                    base::scrollbar::overlay_fraction(pane.h as usize, top, scroll)?;
                // 테두리 열은 내용 사각형의 **바로 오른쪽**이다(`boxrect` 의 끝 열).
                let [bx, _, bw, _] = pane.boxrect?;
                Some(crate::splitter::ScrollHint {
                    x: bx + bw.saturating_sub(1),
                    y: pane.y,
                    h: pane.h,
                    start,
                    len,
                })
            })
            .collect()
    }

    /// 마우스가 올라온 범위의 밑줄(§10-21ⓥ2·ⓧ2). 없으면 빈 목록이다.
    fn span_marks(&self) -> Vec<crate::splitter::SpanMark> {
        self.span_hover
            .iter()
            .map(|hit| crate::splitter::SpanMark { y: hit.y, x0: hit.x0, x1: hit.x1 })
            .collect()
    }

    /// 활성 패널의 커서가 놓인 **캔버스 칸**(§10-21ⓒ). 그릴 것이 없으면 `None`.
    ///
    /// # 왜 이 배선이 통째로 없었나
    ///
    /// 서버는 `screen` 프레임마다 커서를 **델타가 아니라 통째로** 준다. 상태까지 올라와
    /// 있었는데([`SessionState::pane_cursor`]) 뷰가 한 번도 안 읽었다 — 전 저장소에서 그
    /// 값을 읽는 곳이 정의와 proto 자기 테스트뿐이었다. `client/CLAUDE.md` 가 경고한
    /// 부류 그대로다(*"GUI 쪽 배선 누락은 라이브 스크린샷만이 잡는다"*).
    ///
    /// # 왜 활성 패널 하나뿐인가
    ///
    /// 커서는 "다음 글자가 어디에 찍히나"이고, 그것은 **키를 받는 패널** 하나뿐이다.
    /// 패널마다 그리면 화면에 커서가 여럿 보여 오히려 어디를 보는지 알 수 없다(정본도
    /// 단말의 하드웨어 커서 하나다).
    ///
    /// # 화면이 떠 있으면 안 그린다
    ///
    /// 판이 열려 있는 동안 키는 그 판의 것이다(core 규칙). 그때 패널 커서를 그리면
    /// "여기 치면 들어간다"는 거짓말이 된다.
    /// # 깜빡임의 「안 보임」 반주기에도 안 그린다 (`pytmux/pytmux-161`)
    ///
    /// 판정을 **여기 한 곳**에 두는 값: 커서를 그리는 자리가 둘이다(오버레이의 선,
    /// 캔버스의 반전 칸). 각자 깜빡임을 보면 두 모양이 엇갈려 깜빡인다.
    fn cursor_cell(&self) -> Option<crate::splitter::Cursor> {
        if self.screens.top().is_some() {
            return None;
        }
        if !self.blink_on {
            return None;
        }
        let pane = self.state.active_pane()?;
        let (cx, cy) = self.state.pane_cursor(pane)?;
        // 패널 안 좌표 → 캔버스 좌표. `pane_rect` 는 **테두리를 뺀 안쪽**이라 그대로 더한다.
        let (px, py, w, h) = self.state.pane_rect(pane)?;
        // 서버가 준 커서가 패널 밖이면 안 그린다 — 밀린 자리에 상자를 그리면 그것이
        // 새 거짓말이 된다(그런 프레임이 오면 그건 ⓙ3·ⓨ 쪽 이야기다).
        if cx >= w || cy >= h {
            return None;
        }
        Some(crate::splitter::Cursor {
            x: px + cx,
            y: py + cy,
            style: Self::cursor_style(&self.config.cursor_style),
            // 모르는 색 이름이면 `None` = 테마 그대로다(`status-bg` 와 같은 규칙).
            color: proto::status::color(&self.config.cursor_color).map(convert),
            thickness: self.config.cursor_thickness,
        })
    }

    /// 레터박스 — 내 창이 **공유 격자보다 클 때** 남는 L자 띠(pytmux-381).
    ///
    /// 정본과 **같은 판정**이다(`clientio.py::_composite` 851~854): 내 뷰가 담는 칸이 공유
    /// 격자보다 크면 라이브 영역의 오른쪽·아래 경계를 기억하고 그 밖을 무광으로 칠한다.
    /// 단일 클라(정상 경로)에서는 두 값이 같아 **발동하지 않는다** — 그래서 평소 그림은
    /// 종전과 한 픽셀도 다르지 않다.
    ///
    /// ⚠ 「각자 자기 화면 크기로 그린다」는 이것으로 안 채워진다(판 하나 = pty 하나). 없애는
    /// 것은 **어색함**이다 — 사용자가 고른 갈래 ⓕ 의 범위가 그것이다.
    fn letterbox(&self, canvas: &proto::canvas::Canvas) -> Option<crate::splitter::Matte> {
        let (live_cols, live_rows) = canvas.size();
        // 내가 서버에 알린 칸 수 = 내 창이 담는 격자(그 값을 보고 서버가 정책을 돌린다).
        let (cols, rows) = self.size.reported();
        let (cols, rows) = (usize::from(cols), usize::from(rows));
        if cols <= live_cols && rows <= live_rows {
            return None;
        }
        Some(crate::splitter::Matte {
            live_cols: live_cols as u16,
            live_rows: live_rows as u16,
            cols: cols.max(live_cols) as u16,
            rows: rows.max(live_rows) as u16,
            color: theme::MATTE,
        })
    }

    /// 캔버스에 얹을 한/영 배지 — **자리와 색만** 정한다(그림은 오버레이가 그린다).
    ///
    /// # 자리 규칙 (pytmux-392)
    ///
    /// 기준은 정본과 같다(`plugins/ime-indicator/cells.py`): **커서가 있는 줄**의 활성 패널
    /// 오른쪽 끝. 새로 더한 것은 **글자를 가리면 비켜서기**이고, 그 판정은 UI 를 모르는
    /// 자리에 있다([`base::ime_badge::badge_spot`]) — 픽셀 하네스 없이 격자만으로 재려는
    /// 것이다.
    ///
    /// 판이 떠 있으면 캔버스 배지는 안 그린다 — 그때 글자를 받는 곳은 그 판이고, 배지는
    /// 판 안 입력줄로 간다(pytmux-14 의 규칙 그대로).
    fn ime_overlay(&self, canvas: &proto::canvas::Canvas) -> Option<crate::splitter::ImeBadge> {
        if self.screens.top().is_some() {
            return None;
        }
        let label = self.ime_badge?;
        let pane = self.state.active_pane()?;
        let (px, py, w, h) = self.state.pane_rect(pane)?;
        // 줄: 커서가 보이면 그 줄, 아니면 패널의 마지막 줄(정본 `badge_row` 의 셋째 갈래).
        let row = match self.state.pane_cursor(pane) {
            Some((_, cy)) if cy < h => py + cy,
            _ => py + h.saturating_sub(1),
        };
        // 배지가 차지하는 칸 수는 정본 문구(`[한]`·`[EN]`)와 **같게** 둔다 — 그림이 글자
        // 자리를 그대로 쓰면 두 클라에서 「같은 크기의 무언가」가 같은 자리에 선다.
        const CELLS: usize = 4;
        let spot = base::ime_badge::badge_spot(
            usize::from(row),
            usize::from(px),
            usize::from(px + w),
            (usize::from(py), usize::from(py + h)),
            CELLS,
            0,
            |x, y| canvas.cell(x, y).is_none_or(|c| c.ch == ' ' && c.style.bg.is_none()),
        )?;
        // 색은 정본이 정한 **의미 이름**을 이 클라의 표에서 푼다(pytmux-16 의 그 자리 —
        // 표에 없는 이름이면 안 그린다. 검은 글자만 남는 것이 그때의 증상이었다).
        let name = if label == "한" { "success" } else { "primary" };
        let track = match proto::session::theme::resolve(name) {
            proto::session::theme::Resolution::Color(c) => to_gui_color(&c),
            _ => return None,
        };
        Some(crate::splitter::ImeBadge {
            x: spot.col as u16,
            y: spot.row as u16,
            cells: CELLS as u16,
            // 손잡이가 **왼쪽이면 한글**이다(토글 어휘 — 왼쪽이 «모국어» 쪽).
            left: label == "한",
            track,
            knob: palette::BLACK,
        })
    }

    /// 설정 낱말 → 그리는 쪽의 모양. 모르는 낱말은 **기본값**이다.
    ///
    /// 파서(`Config::parse`)가 이미 어휘를 거르지만 여기서 또 접는 이유: 설정 파일만이
    /// 이 값의 유일한 입구가 아니다(`set cursor-style …` 한 줄·설정 화면). 어느 길로
    /// 들어와도 화면에서 커서가 사라지는 일은 없어야 한다.
    fn cursor_style(name: &str) -> crate::splitter::CursorStyle {
        use crate::splitter::CursorStyle;
        match name {
            "block" => CursorStyle::Block,
            "underline" => CursorStyle::Underline,
            "bar" => CursorStyle::Bar,
            _ => CursorStyle::Hollow,
        }
    }

    /// 캔버스가 **반전해 그릴** 커서 칸(`cursor-style block` 일 때만 · 캔버스 좌표).
    ///
    /// 오버레이가 그 모양을 못 그리는 사정은 `splitter::SplitterOverlay::paint_cursor`
    /// 의 머리말에 있다 — 다 그린 위에 칠하면 그 칸의 글자가 덮인다.
    fn cursor_block_cell(&self) -> Option<(u16, u16)> {
        let cursor = self.cursor_cell()?;
        (cursor.style == crate::splitter::CursorStyle::Block)
            .then_some((cursor.x, cursor.y))
    }

    /// 캔버스 한 줄. 같은 스타일끼리 묶인 런을 가로로 잇는다.
    ///
    /// 런마다 엘리먼트를 만드는 이유: 한 줄 안에서도 색이 바뀐다. 줄 전체를 한 `Text` 로
    /// 만들면 그 줄의 색이 하나로 뭉개진다.
    /// 아직 안 쟀으면 이 줄에서 **폭이 1칸인 글자 하나**에 자리표를 남긴다
    /// ([`CELL_PROBE`](Self::CELL_PROBE)). 남겼으면 `probed` 를 세운다.
    ///
    /// # 아무 글자나 고르면 안 된다
    ///
    /// 두 칸짜리(한글·CJK)에 남기면 그 사각형의 폭이 칸너비의 **두 배**라 마우스 좌표가
    /// 절반으로 접힌다. 폭이 애매한 글자(`┌`·`│` 같은 East Asian Ambiguous)도 피한다 —
    /// 이 저장소가 이미 밟은 자리다(`AMBIGUOUS_WIDTH_2026-06-25`). 그래서 **ASCII 만**
    /// 고른다(공백 포함 — 빈 칸도 한 칸이고, 화면 대부분이 그것이다).
    ///
    /// # 왜 첫 줄로 못박지 않나 (2026-07-28 실측)
    ///
    /// 처음에는 0행에만 남겼는데 **자리표가 한 번도 안 생겼다**. 캔버스 0행은 패널
    /// 테두리(`┌───┐`)이고 거기엔 ASCII 가 하나도 없다. 증상은 "경계선이 안 끌린다"였고,
    /// 층을 가른 것은 이 함수가 아니라 [`cell_from_event`](Self::cell_from_event) 에
    /// 넣은 진단 한 줄이었다.
    /// # 격자는 클라가 잡는다 (§10-21ⓙ)
    ///
    /// 런을 통째로 넘기지 않고 [`grid_segments`](Self::grid_segments) 로 나눠, **고정폭이
    /// 보장되지 않는 글자마다 자기 칸에 못박는다**(`ConstrainedBox::with_width`). 칸너비는
    /// 자리표에서 **잰 값**이다 — 계산하면 렌더와 어긋난다는 이 파일의 규율 그대로다.
    /// 아직 안 쟀으면(첫 프레임) 종전처럼 셰이퍼에 맡긴다: 한 프레임 뒤에 제자리로 온다.
    ///
    /// # 세로는 왜 안 못박나 (pytmux-15 ⑴ 의 답)
    ///
    /// 제보는 *"한글이 든 줄만 위아래 간격이 벌어진다"* 였고 가설은 "폴백 글꼴의
    /// ascent/descent 가 그 행의 키를 키운다"였다. **그 일은 이 스택에서 일어날 수 없다** —
    /// 줄 높이는 `Line::height() = line_height_ratio × font_size`(`warpui_core/src/
    /// text_layout.rs`)로 **글꼴과 무관**하고, 글리프는 그 줄 상자 안에서 가운데 정렬된다
    /// (`default_compute_baseline_position`). 키가 넘치면 넘쳐 그릴 뿐 **줄 높이는 안 는다**.
    /// 이 줄들은 전부 같은 `font_size`(`self.scaled(13.)`)를 쓰므로 행 피치가 내용에 안 탄다
    /// (실측도 같았다 — ASCII 10줄과 한글 10줄이 같은 y 에서 끝났다).
    ///
    /// ⛔ 그러니 **`ConstrainedBox::with_height` 로 행을 못박지 말 것**. 고쳐야 할 것이
    /// 없는데다, `Text::layout` 은 줄 높이가 `max_height` 를 넘으면 그 줄을 **아예 안
    /// 그린다**(`LaidOutText::None` — autosize 를 안 준 우리 경로가 그렇다). 없는 병을
    /// 고치려다 **한글이 통째로 사라지는** 진짜 병을 만든다.
    fn render_row(
        &self,
        y: usize,
        runs: Vec<(String, CellStyle)>,
        frame: &std::collections::BTreeSet<(u16, u16)>,
        blocks: &std::collections::BTreeSet<(u16, u16)>,
        cursor_x: Option<u16>,
        probed: &mut bool,
    ) -> Box<dyn Element> {
        let mut row = Flex::row().with_main_axis_size(MainAxisSize::Min);
        let cell_w = self.cell_px.get().map(|(w, _)| w);
        let cursor_color = proto::status::color(&self.config.cursor_color)
            .map(convert)
            .unwrap_or(crate::theme::FOCUS);
        let mut cx = 0usize;
        for (text, style) in runs {
            // 이 런이 시작하는 칸 — 아래에서 커서 칸을 찾을 때 쓴다(`cx` 는 곧 런 전체를
            // 훑고 지나간다).
            let run_x0 = cx;
            // ★ 크롬의 경계 문자와 블록 문자는 **글자로 안 그린다** — 오버레이가 그 칸을
            //   실제 선(2026-07-31 사용자 지시)·사각형(§10-21ⓘ)으로 그린다. 자리를 비우는
            //   것이지 지우는 것이 아니다: 둘 다 한 칸짜리라 공백으로 바꿔도 그 줄의 폭이
            //   그대로다(두 칸짜리로 바꾸면 뒤 글자가 전부 밀리고 마우스 셀 산수까지
            //   어긋난다).
            let mut text2 = String::with_capacity(text.len());
            for ch in text.chars() {
                let at = (cx as u16, y as u16);
                let blank = (frame.contains(&at) && proto::canvas::box_bits(ch).is_some())
                    || blocks.contains(&at);
                text2.push(if blank { ' ' } else { ch });
                cx += proto::compose::char_advance(ch);
            }
            let (fg, bg) = colors(&style);
            let props = font_properties(&style);
            // ★ 채운 네모 커서(`cursor-style block`)는 그 **칸을 반전**해 그린다
            //   (`pytmux/pytmux-161`). 오버레이가 위에서 칠하면 그 글자를 덮으므로,
            //   터미널이 하는 대로 여기서 색만 뒤집는다 — 글자는 그대로 읽힌다.
            //   반전할 칸은 자기 조각으로 떼어 낸다(안 떼면 그 런 전체가 뒤집힌다).
            let mut segments = Self::grid_segments(&text2);
            if let Some(at) = cursor_x {
                segments = Self::isolate_cell(segments, run_x0, at as usize);
            }
            let mut seg_x = run_x0;
            for (piece, cells) in segments {
                let inverted = cursor_x
                    .is_some_and(|at| seg_x <= at as usize && (at as usize) < seg_x + cells);
                seg_x += cells;
                // 반전 칸의 글자색은 **바탕색**이다 — SGR 반전(`colors`)과 같은 규칙이라
                // 두 자리가 갈리지 않는다. 그 칸에 앱이 칠한 배경이 있으면 그 색이 글자가
                // 된다(없으면 캔버스 바탕).
                let (fg, bg) = if inverted {
                    (bg.unwrap_or(palette::BG), Some(cursor_color))
                } else {
                    (fg, bg)
                };
                // 이 조각 안에서 자리표를 붙일 ASCII 글자의 위치(없으면 안 붙인다).
                let mark = (!*probed)
                    .then(|| {
                        piece
                            .chars()
                            .position(|c| c.is_ascii() && !c.is_ascii_control())
                    })
                    .flatten();
                // ★ 못박는 것은 **ASCII 가 아닌 조각만**이다. ASCII 는 고정폭 글꼴이
                //   그리므로 자연폭이 곧 `칸수 × 칸너비`고, 거기에 우리가 잰 값을 덮어
                //   씌우면 부동소수 반올림 한 톨 차이로 마지막 글자가 흐려질 수 있다
                //   (`layout_line` 은 max_width 를 넘으면 페이드한다).
                let boxed = piece.chars().any(|c| !c.is_ascii() || c.is_ascii_control());
                // 캔버스도 **같은 배율**을 탄다(§10-21ⓐ) — 여기만 빼면 "앱 전체"가 아니다.
                // 칸 크기가 바뀌면 `report_size` 가 자리표를 다시 재어 서버에 새 격자를
                // 알린다(창을 키운 것과 같은 길이라 새 배관이 없다).
                let mut cell = Text::new_inline(piece.clone(), self.font, self.scaled(13.))
                    .with_color(fg)
                    // 굵게·기울임(pytmux-133). 조각이 보조 글꼴로 갈 것이면 기울임을 뺀다.
                    .with_style(fallback_safe(props, boxed));
                if let Some(index) = mark {
                    *probed = true;
                    cell = cell.with_saved_char_position(index, Self::CELL_PROBE.to_owned());
                }
                let mut cell = cell.finish();
                if let (Some(w), true) = (cell_w, boxed) {
                    // ⛔ `ConstrainedBox` 가 아니다 — 그것은 한도를 아이에게 내려보내
                    //    `Text` 가 그 폭에서 **페이드**한다(pytmux-270 · `CellBox` 문서).
                    cell = CellBox::new(cell, w * cells as f32).finish();
                }
                row = row.with_child(match bg {
                    Some(bg) => Container::new(cell).with_background_color(bg).finish(),
                    None => cell,
                });
            }
        }
        row.finish()
    }
}

impl Entity for SessionView {
    type Event = ();
}

impl View for SessionView {
    fn ui_name() -> &'static str {
        "SessionView"
    }

    fn render(&self, _: &AppContext) -> Box<dyn Element> {
        // ★ **프레임은 여기서 센다**(pytmux-457). `pump` 는 33ms 타이머라 그리지 않은
        //   틱도 세고, 재려는 것은 「그렸나」다. 간격 표본도 같은 자리에서 뜬다 —
        //   상류가 프레임 시간을 안 내주므로 우리가 잴 수 있는 것은 **렌더 사이의
        //   시간**이고, 표에도 그렇게 적는다(없는 값을 지어내지 않는다).
        self.frames.set(self.frames.get().saturating_add(1));
        let now = Instant::now();
        if let Some(prev) = self.last_render.replace(Some(now)) {
            let mut window = self.frame_ms.borrow_mut();
            window.push_back(now.duration_since(prev).as_secs_f64() * 1000.0);
            while window.len() > base::diag::FRAME_WINDOW {
                window.pop_front();
            }
        }
        let mut column = Flex::column().with_main_axis_size(MainAxisSize::Max);
        // ★ 머리줄은 **여기 없다**(`pytmux-1`) — 이 열의 첫 줄이 아니라 창의 맨 위
        //   한 줄로 나갔다(아래 `render` 꼬리의 `titlebar::row`). 종전에는 앱 이름과
        //   엔드포인트를 가운데 정렬로 띄우는 평범한 한 줄이었고, 그 위에 OS 타이틀바가
        //   따로 있었다. 제보가 그 둘을 **한 줄로 합쳤다**: OS 띠를 없애고 이 줄이
        //   제목·창 버튼·창 끌기를 겸한다.
        //
        //   ⛔ 다시 여기로 들여놓지 말 것 — 바깥 여백(8px) 안에 들어가는 순간 창 버튼이
        //   모서리에서 떨어지고, 창에게 말해 둔 띠(`refresh_titlebar_band`)와 자리가
        //   어긋나 "머리줄 아래 8px 이 안 끌린다"가 된다.
        if self.status_on_top() {
            column = column.with_child(self.render_status());
        }
        column = column.with_child(self.render_tabs());

        // 끊김·오류 한 줄은 여기가 아니라 **맨 아래**다(`render_message` — 사용자 요청
        // 2026-07-30). 탭바 밑에 두면 줄이 생길 때마다 캔버스가 밀리고, 사라질 때 또
        // 되밀려 화면이 출썩인다.

        // 캔버스는 **항상** 그린다(N2) — 떠 있는 화면은 딤 스크림과 함께 위에 얹는다.
        // 종전의 "대체" 방식은 팝업을 여는 순간 화면 상황이 통째로 사라졌다.
        match self.composite_for_paint() {
            Some(canvas) => {
                let (_, height) = canvas.size();
                // 이 프레임이 실제로 그린 칸 수 — 상류가 씬 원소 수를 안 내주므로
                // **우리가 아는 그리기 일의 크기**가 이것이다(pytmux-457).
                let (cw, ch) = canvas.size();
                self.painted_cells.set(usize::from(cw) * usize::from(ch));
                // 자리표는 **딱 한 번만** 남긴다 — 같은 id 를 여러 줄이 쓰면 마지막에
                // 그려진 줄의 값이 남아 원점이 화면 아래로 밀린다. 그 한 번이 몇
                // 행인지는 내용이 정한다(테두리 줄에는 ASCII 가 없다).
                let mut probed = false;
                // 캔버스 줄들은 **한 덩어리**로 묶는다(N3) — 스플리터 오버레이의 원점이
                // 곧 캔버스 원점이라야 셀→픽셀 산수에 다른 보정이 안 낀다.
                let mut rows = Flex::column().with_main_axis_size(MainAxisSize::Min);
                // 어느 칸을 선으로 그리나 — 줄을 그리기 **전에** 한 번 정한다(줄마다
                // 다시 재면 같은 판정을 행 수만큼 돌린다).
                let frame = Self::frame_cells(&canvas, self.state.layout());
                // 블록 문자 칸도 같은 규율로 한 번만 고른다(§10-21ⓘ) — 오버레이가
                // 사각형으로 그릴 자리이자, 줄에서 비울 자리다.
                let blocks = Self::block_cells(&canvas);
                let block_at: std::collections::BTreeSet<(u16, u16)> =
                    blocks.iter().map(|b| (b.x, b.y)).collect();
                // 채운 네모 커서는 **줄이 그린다**(`pytmux/pytmux-161`) — 오버레이가
                // 위에서 칠하면 그 칸의 글자가 덮인다. 여기서 한 번만 묻는다: 줄마다
                // 다시 물으면 같은 판정을 행 수만큼 돌린다(위 `frame`·`blocks` 와 같은
                // 규율).
                let cursor_cell = self.cursor_block_cell();
                for y in 0..height {
                    rows = rows.with_child(self.render_row(
                        y,
                        canvas.row_runs(y),
                        &frame,
                        &block_at,
                        cursor_cell.filter(|(_, cy)| *cy as usize == y).map(|(cx, _)| cx),
                        &mut probed,
                    ));
                }
                // 테두리가 무슨 상태를 말하나 — 원격·degraded 면 우리가 칠한다(§10-21ⓩ).
                let tint = self.border_tint();
                // 경계 칸 위 네이티브 스플리터 바 — 잡고 있거나 hover 면 FOCUS 강조.
                let bars = self
                    .state
                    .dividers()
                    .iter()
                    .map(|d| crate::splitter::Bar {
                        vertical: d.orient == "lr",
                        x: d.x,
                        y: d.y,
                        w: d.w,
                        h: d.h,
                        active: self.dragging == Some(d.split_id)
                            || self.divider_hover == Some(d.split_id),
                        // 경계도 테두리의 일부다 — 원격 탭에서 테두리만 분홍이고 경계가
                        // 파랗게 남으면 그 자리가 도리어 눈에 띈다(§10-21ⓩ). 활성 색은
                        // **잡는 신호**라 tint 를 안 탄다(그건 상태가 아니라 조작이다).
                        tint: tint_color(tint, false),
                        // 테두리가 주인인 이음새 칸은 바가 건드리지 않는다(§10-21ⓟ).
                        // `frame` 이 이미 "선으로 그릴 칸"이라 그 교집합이 곧 이음새다 —
                        // 판정이 한 곳이라 둘이 갈릴 수 없다.
                        skip: frame
                            .iter()
                            .copied()
                            .filter(|(fx, fy)| {
                                *fx >= d.x && *fx < d.x + d.w && *fy >= d.y && *fy < d.y + d.h
                            })
                            .collect(),
                    })
                    .collect();
                // ★ 남는 높이는 **캔버스가 받는다**(`pytmux-162`) — 아래에 빈 위젯을 따로
                //   두지 않는다. 상태줄이 창 바닥에 붙는 것은 그대로이고(받은 자리를 다
                //   쓰므로 뒤 형제가 밀리는 자리도 같다), 달라지는 것은 **테두리 상자가
                //   그 자리까지 내려간다**는 것뿐이다. 아래 `Expanded(Empty)` 갈래를 보라.
                column = column.with_child(
                    Expanded::new(
                        1.,
                        crate::splitter::SplitterOverlay::new(
                            rows.finish(),
                            bars,
                            Self::frame_segments(&canvas, self.state.layout(), tint),
                            blocks,
                            self.cursor_cell(),
                            self.block_mark(),
                            self.scroll_hints(),
                            self.span_marks(),
                            self.rule_marks(),
                            Self::CELL_PROBE,
                            height as u16,
                        )
                        .with_ime(self.ime_overlay(&canvas))
                        .with_matte(self.letterbox(&canvas))
                        .finish(),
                    )
                    .finish(),
                );
            }
            // 배치를 아직 못 받았다. 빈 화면은 "멈춘 것"과 구분되지 않는다.
            //
            // 여기서는 남는 공간을 빈 위젯이 접는다 — 요약·상태줄이 **창 바닥에 붙는다**
            // (TUI 의 마지막 행들과 같은 자리). 이게 없으면 상태줄 아래에 빈 띠가 남아
            // 두 클라의 배치가 갈린다. ⛔ **캔버스가 있는 갈래에 이 줄을 되살리지 마라**
            // (`pytmux-162`): 유연한 자식이 둘이 되면 남는 높이가 반씩 갈려, 캔버스는
            // 아랫변을 절반만 내리고 나머지 절반이 다시 빈 띠로 남는다.
            None => {
                column = column
                    .with_child(self.text(t("첫 화면을 기다리는 중…"), 14., palette::DIM));
                column =
                    column.with_child(Expanded::new(1., Empty::new().finish()).finish());
            }
        }
        // ★ 요약 구역은 **여기 없다**(§10-21ⓓ) — `summary` 명령이 여는 판으로 옮겼다.
        // 상태줄은 기본이 **맨 아래**다 — `e_down` 이 "아래로 나간다"라야 동선이 말이
        // 된다. `status-position top` 이면 탭바보다 위로 올린다.
        if !self.status_on_top() {
            column = column.with_child(self.render_status());
        }
        // ★ 메시지는 **모든 것의 아래**다 — 상태줄보다도 뒤에 붙는다(TUI 와 같은 순서).
        column = self.render_message(column);

        // ★ 머리줄(= 이 창의 타이틀바)은 **바깥 여백 밖**이다(`pytmux-1`): 창 버튼이
        //   모서리에 닿아야 하고, 끌 수 있는 띠라고 창에게 말해 둔 높이와 자리가 같아야
        //   한다. 나머지는 종전대로 8px 안에 들어간다.
        let inside = Flex::column()
            .with_main_axis_size(MainAxisSize::Max)
            .with_child(self.render_titlebar())
            .with_child(
                Expanded::new(
                    1.,
                    Container::new(column.finish())
                        .with_uniform_padding(8.)
                        .finish(),
                )
                .finish(),
            )
            .finish();
        let mut body = Stack::new()
            .with_child(Rect::new().with_background_color(palette::BG).finish())
            .with_child(inside);
        // 떠 있는 화면(팝업 계층) — 스크림이 아래를 가라앉히고 판이 뜬다(N2).
        // 캔버스 마우스는 화면이 떠 있는 동안 core 판정 전에 뷰가 끊는다(`handle_mouse_*`).
        //
        // ⛔ **딤은 화면마다 다르다**(pytmux-370). 종전에는 `top()` 이 무엇이든 창 전체를
        //    덮었다 — 화면별 갈래가 아예 없었다. 작성창은 **위에 보이는 것을 보면서 쓰는
        //    자리**라 정본이 아무것도 어둡게 하지 않는다. 그 판정의 주인은 core 다
        //    (`Screen::dims_behind` — 자리표와 같은 결).
        if let Some(screen) = self.screens.top() {
            if screen.dims_behind() {
                body = body
                    .with_child(Rect::new().with_background_color(theme::DIM_SCRIM).finish());
            }
            body = body.with_child(self.render_screen_panel(screen));
        }
        let body = body.finish();

        // N3: 경계 hover·커서 — 이벤트 콜백은 뷰를 못 만지므로 이 프레임의 사실
        // (경계 사각형·마우스 생사)을 값으로 들고 간다. 프레임이 바뀌면 클로저도
        // 새로 잡힌다(렌더마다 다시 만들어진다).
        let dividers: Vec<(bool, u16, u16, u16, u16)> = self
            .state
            .dividers()
            .iter()
            .map(|d| (d.orient == "lr", d.x, d.y, d.w, d.h))
            .collect();
        let canvas_mouse_alive = self.config.mouse && self.screens.top().is_none();
        // 머리줄(= 타이틀바)의 아래 경계. 창에게 말해 둔 값과 **같은 함수**에서 나온다 —
        // 두 자리에서 각자 계산하면 배율을 바꿀 때 한쪽만 따라간다.
        let band = titlebar::band_height(self.config.font_scale);
        // 커서 모양은 이벤트 안에서 세워야 해서 값 하나로 실어 보낸다(위 `None if`).
        let span_hovered = self.span_hover.is_some();

        // 키·휠은 여기서 받는다. **모든 키**를 받아야 하므로 바인딩 표로 걸지 않는다 —
        // 그 집합은 열거할 수 없다(`a` 가 표에 없다고 패널에 안 가면 타이핑이 안 된다).
        // 원시 이벤트를 잡아 core 의 모드가 가른다. TUI 가 `on_any_key` 를 신설한 것과
        // 같은 이유이고, 바인딩을 이 뷰에 걸지 않는 덕에 **같은 키가 두 번 처리되는**
        // 일도 구조적으로 없다.
        EventHandler::new(body)
            .on_keydown(|evt, _app, keystroke| {
                // ★ 붙여넣기는 **Ctrl+V**(정본과 같은 손버릇) · **Cmd/Super+V**(정본
                // `super+v`) · **Ctrl+Shift+V**(터미널 관례 별칭)다. 이 클라에는 붙여넣기를
                // 대신 해 줄 바깥 터미널이 없어서(TUI 는 bracketed paste 로 받는다) 여기서
                // 직접 받는다. 가로채면 패널 안 프로그램이 `0x16` 을 못 받는데, 그 값은
                // **정본이 이미 치르기로 한 것**이다(`is_paste_chord` 문서 · pytmux-364).
                // ⛔ 자리도 정본과 같다 — 패널 패스스루와 `bind -n` **앞**이다.
                if Self::is_paste_chord(keystroke) {
                    evt.dispatch_typed_action(ViewAction::PasteRequest);
                    return DispatchEventResult::StopPropagation;
                }
                // ★ 글자 크기(§10-21ⓐ)도 여기서 가로챈다 — 바깥 터미널이 없어서
                //   우리가 안 받으면 아무도 안 받는다(붙여넣기와 같은 사정).
                if let Some(action) = Self::font_scale_chord(keystroke) {
                    evt.dispatch_typed_action(ViewAction::Act(action));
                    return DispatchEventResult::StopPropagation;
                }
                // ★ 탭 전환(§10-21ⓕ·ⓕ2) — 독립 앱이라 이 조합을 가로챌 수 있다
                //   (정본은 터미널 안이라 못 한다). 패널 안 프로그램의 `Ctrl+Tab` 이
                //   사라지는 것은 제보가 감수한 결정이다.
                if let Some(forward) = Self::tab_switch_chord(keystroke) {
                    evt.dispatch_typed_action(ViewAction::AltTab(forward));
                    return DispatchEventResult::StopPropagation;
                }
                // ★ 전체 화면(§10-21ⓘ3) — 상류 창이 `toggle_fullscreen()` 을 주는데
                //   이 크레이트가 한 번도 안 부르고 있었다(ⓕ2 의 수정키 뗌과 같은
                //   모양의 미배선).
                if Self::is_fullscreen_chord(keystroke) {
                    evt.dispatch_typed_action(ViewAction::Act(base::Action::ToggleFullscreen));
                    return DispatchEventResult::StopPropagation;
                }
                match Self::key_from_keystroke(keystroke) {
                    Some((key, mods)) => evt.dispatch_typed_action(ViewAction::RawKey(key, mods)),
                    // 모르는 키는 조용히 버린다 — 자식에게 쓰레기를 보내지 않는다.
                    None => {}
                }
                DispatchEventResult::StopPropagation
            })
            // 휠은 **커서 아래 패널**을 굴린다. 그래서 델타만으로는 부족하고 위치가
            // 필요하다(`on_scroll_wheel` 이 위치를 주도록 고쳤다 — PROVENANCE §1).
            // 입력기가 확정한 글자(한글). **키와 다른 경로**로 온다 — 조합 중에는
            // 아무것도 안 오고, 끝나야 문자열 하나가 온다.
            // ★ 수정키 **뗌**을 받는다(§10-21ⓕ2) — 상류가 주고 있었는데 이 크레이트가
            //   한 번도 안 받던 이벤트다. `Ctrl` 을 떼면 스위처가 확정된다.
            .on_modifier_state_changed(|evt, _app, key_code, state| {
                use warpui::platform::keyboard::KeyCode;
                if matches!(state, warpui_core::event::KeyState::Released)
                    && matches!(key_code, KeyCode::ControlLeft | KeyCode::ControlRight)
                {
                    evt.dispatch_typed_action(ViewAction::CtrlReleased);
                }
                DispatchEventResult::PropagateToParent
            })
            .on_typed_characters(|evt, _app, chars| {
                evt.dispatch_typed_action(ViewAction::Typed(chars.to_owned()));
                DispatchEventResult::StopPropagation
            })
            // ★ **조합 중**인 글자를 받는다(§10-21ⓞ2 ⑵). 상류는 이걸 계속 주고 있었는데
            //   이 크레이트에 받는 자리가 없어 소비자가 닿을 수 없었다 — 그래서 사람이
            //   `ㅎ`→`하`→`한` 을 만드는 동안 **화면이 비어 있었다**. 확정 콜백의 짝이다.
            .on_marked_text(|evt, _app, text, _sel| {
                evt.dispatch_typed_action(ViewAction::Preedit(text.to_owned()));
                DispatchEventResult::StopPropagation
            })
            .on_scroll_wheel(|evt, _app, position, delta, _mods| {
                // 가로 스크롤(트랙패드 옆쓸기)은 세로 델타가 0 이다. 그걸 "아래로"로
                // 읽으면 옆으로 쓸 때마다 화면이 흘러내린다.
                if delta.y() == 0. {
                    return DispatchEventResult::StopPropagation;
                }
                let at = Self::cell_from_event(evt, position);
                evt.dispatch_typed_action(ViewAction::Wheel { up: delta.y() > 0., at });
                DispatchEventResult::StopPropagation
            })
            // 마우스 좌표는 픽셀이라 셀로 풀어야 한다. **여기서 푸는 이유**는 자리표를
            // 읽을 수 있는 곳이 이벤트 문맥뿐이기 때문이다 — 뷰에는 픽셀이 아예 안
            // 들어간다(뷰가 픽셀 산수를 하면 렌더와 어긋나기 시작한다).
            // ★ Shift 는 "패널 안 앱에게 넘김"이다(평드래그는 이미 복사다). 그래서
            // 누름에는 수정키가 필요하고, `on_left_mouse_down` 이 그것을 주도록
            // 고쳤다(PROVENANCE §1).
            .on_left_mouse_down_with_modifiers(move |evt, _app, position, mods| {
                if let Some(at) = Self::cell_from_event(evt, position) {
                    evt.dispatch_typed_action(ViewAction::MouseDown(at, mods.shift));
                    return DispatchEventResult::StopPropagation;
                }
                // ★ **머리줄에서 온 누름은 안 먹었다고 돌려준다**(`pytmux-1`).
                //
                //   창 끌기와 더블클릭 최대화는 상류가 이미 갖고 있는데(winit 은
                //   `event_loop`, 맥은 `host_view.m` 의 `mouseInTitleBar`), 그 갈래는
                //   **앱이 안 먹은 누름에만** 온다. 이 `EventHandler` 는 창 전체를
                //   덮고 모든 왼쪽 누름을 삼키고 있었으므로, 그대로 두면 우리가 머리줄을
                //   아무리 그려도 창은 한 픽셀도 안 움직인다.
                //
                //   창 버튼은 여기까지 안 온다 — 각 버튼의 `Hoverable` 이 자식으로서
                //   먼저 "먹었다"를 돌려주고, `EventHandler` 는 자식이 먹은 이벤트를
                //   자기 콜백에 안 넘긴다. 제보가 걱정한 "드래그 영역과 클릭존이
                //   겹친다"가 그 구조로 풀린다.
                if position.y() < band {
                    return DispatchEventResult::PropagateToParent;
                }
                DispatchEventResult::StopPropagation
            })
            // 캔버스 밖(탭바 등)의 드래그·뗌도 뷰에 알린다(`None`) — 탭 드래그(G9w)의
            // 놓는 순간이 그 좌표 밖에서 온다.
            // ★ 오른쪽 버튼(§10-21ⓕ3) — 종전에는 왼쪽만 보고 있었다. 상류가 주는
            //   이벤트인데 이 크레이트가 안 받던 것이라, ⓘ3(전체 화면)·ⓕ2(수정키 뗌)와
            //   같은 모양의 미배선이다.
            .on_right_mouse_down(|evt, _app, position| {
                let at = Self::cell_from_event(evt, position);
                evt.dispatch_typed_action(ViewAction::RightMouseDown(at));
                DispatchEventResult::StopPropagation
            })
            .on_mouse_dragged(|evt, _app, position| {
                let at = Self::cell_from_event(evt, position);
                evt.dispatch_typed_action(ViewAction::MouseDrag(at));
                DispatchEventResult::StopPropagation
            })
            .on_left_mouse_up(move |evt, _app, position| {
                let at = Self::cell_from_event(evt, position);
                // 뗌은 **머리줄에서도 알린다** — 탭을 잡고 머리줄까지 끌어올린 채 놓는
                // 사람이 있고, 그 뗌을 안 받으면 드래그가 다음 클릭까지 살아 있는다.
                evt.dispatch_typed_action(ViewAction::MouseUp(at));
                // 그러고도 안 먹었다고 돌려준다 — 맥의 **더블클릭 최대화**가 뗌에서
                // 판정되기 때문이다(`host_view.m` 의 `mouseUp` → `handleTitleBarDoubleClick`,
                // 그것도 "warp 이 안 먹었을 때만"). winit 쪽은 누름에서 판정하므로 이
                // 줄이 없어도 되지만, 두 OS 를 다르게 적으면 한쪽이 조용히 썩는다.
                if at.is_none() && position.y() < band {
                    return DispatchEventResult::PropagateToParent;
                }
                DispatchEventResult::StopPropagation
            })
            // 버튼 없는 이동(N3) — `on_mouse_in` 은 원소 안에서의 **모든** MouseMoved 에
            // 발화한다(진입 한정이 아니다 — event_handler.rs 디스패치 참조). 경계 위에서는
            // 리사이즈 커서를 세워 "잡을 수 있음"을 손이 먼저 알게 한다.
            .on_mouse_in(
                move |evt, _app, position| {
                    let at = Self::cell_from_event(evt, position);
                    let over = canvas_mouse_alive
                        .then(|| {
                            at.and_then(|(x, y)| {
                                dividers.iter().find(|d| {
                                    x >= d.1 && x < d.1 + d.3 && y >= d.2 && y < d.2 + d.4
                                })
                            })
                        })
                        .flatten();
                    match over {
                        Some(&(vertical, ..)) => evt.set_cursor(
                            if vertical {
                                warpui::platform::Cursor::ResizeLeftRight
                            } else {
                                warpui::platform::Cursor::ResizeUpDown
                            },
                            warpui::scene::ZIndex::Normal(0),
                        ),
                        // ★ 범위 위면 **손가락**(§10-21ⓥ2·ⓧ2) — 밑줄만 그으면 "누를 수
                        //   있다"가 손에 안 온다. 값은 **직전 이동**에서 잰 것이다(이 닫힘은
                        //   렌더 때 만들어진다) — 마우스는 연달아 움직이므로 한 이벤트
                        //   뒤에 따라붙고, 그 지연은 눈에 안 띈다.
                        None if span_hovered => evt.set_cursor(
                            warpui::platform::Cursor::PointingHand,
                            warpui::scene::ZIndex::Normal(0),
                        ),
                        None => evt.reset_cursor(),
                    }
                    evt.dispatch_typed_action(ViewAction::MouseMove(at));
                    DispatchEventResult::StopPropagation
                },
                None,
            )
            .finish()
    }
}

/// 확정된 글자(IME 조합 결과)가 갈 곳(§10-21ⓜ2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TypedTo {
    /// 패널 안 프로그램에게.
    Pane,
    /// 지금 열려 있는 판의 입력처(팔레트 필터·프롬프트·작성창).
    Screen,
    /// 상태줄 세션 이름의 **제자리 입력칸**(pytmux-3). 판이 아니라 크롬이라 위와 갈린다.
    SessionEdit,
    /// 아무 데도 — 모드 키를 쓰는 중이다.
    Drop,
}

/// 이 뷰가 받는 것 — 이미 뜻이 정해진 액션과 **아직 판정 전의 키**.
///
/// core 의 [`Action`] 에 키를 넣지 않는 이유는 TUI 와 같다: 그건 **의도**의 목록이고
/// (도움말·바인딩 표가 그걸 쓴다) 원시 키는 의도가 아니다. 뷰 계층에서만 감싸고, 모든
/// 키가 `handle_key` 한 곳을 지나 모드가 갈라 준다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ViewAction {
    /// 아직 판정 전의 키.
    RawKey(Key, Mods),
    /// 크롬(탭·`[+]`·`[x]`·하단 배지)을 클릭했다 — 자리는 Hoverable(레이아웃)이 재고,
    /// 뜻은 core 가 정한다(`chrome::click` — 68332 빚).
    ChromeClick(base::chrome::ClickTarget),
    /// 휠 한 칸. `up` 이면 과거 방향, `at` 은 커서 아래 셀(캔버스 밖이면 `None`).
    Wheel { up: bool, at: Option<(u16, u16)> },
    /// 버튼 없이 움직였다(N3) — 분할 경계 hover 강조용. 커서 모양은 이벤트 콜백이
    /// 직접 세운다(EventContext 가 거기에만 있다).
    MouseMove(Option<(u16, u16)>),
    /// 창이 통째로 준 붙여넣기 payload(키가 아니다).
    Paste(String),
    /// 입력기가 확정한 글자(한글 등). 키가 아니라 **조합 결과**다.
    Typed(String),
    /// 입력기가 **조합 중인** 글자. 빈 문자열이면 조합이 끝났거나 취소됐다.
    Preedit(String),
    /// 왼쪽 버튼을 눌렀다(캔버스 셀 좌표 · Shift 를 함께 눌렀나).
    ///
    /// Shift 를 여기서 싣는 이유: 넘김 판정은 **누름 시점의** 수정키로 정해진다. 뗌에서
    /// 다시 보면 그 사이에 Shift 를 놓은 사람의 드래그가 중간에 성격을 바꾼다.
    MouseDown((u16, u16), bool),
    /// 오른쪽 버튼을 눌렀다 — 그 자리의 패널을 대상으로 **메뉴를 연다**(§10-21ⓕ3).
    ///
    /// 캔버스 밖(탭바 등)이면 `None` 이다. 그때는 아무 일도 안 한다 — 정본도 패널 위에서만
    /// 연다(`clientwidgets` 의 `button == 3` 은 패널 위젯의 것이다).
    RightMouseDown(Option<(u16, u16)>),
    /// 누른 채 옮겼다.
    MouseDrag(Option<(u16, u16)>),
    /// 놓았다.
    MouseUp(Option<(u16, u16)>),
    /// 탭 위에서 눌렀다 — 드래그 시작(G9w). 전환은 뗄 때 정해진다.
    TabPress(usize),
    /// 떠 있는 판 **안**을 클릭했다(설정 탭·팔레트 탭·메뉴 줄·확인 버튼).
    PanelClick(base::PanelTarget),
    /// "지금 OS 클립보드를 읽어 붙여넣어 달라"(Ctrl+Shift+V).
    ///
    /// 읽기는 `AppContext` 가 필요해 이벤트 콜백 안에서 못 한다 — 뷰로 넘겨 거기서 한다.
    PasteRequest,
    /// `Ctrl+Tab`/`Ctrl+Shift+Tab` 한 걸음(§10-21ⓕ·ⓕ2). 값은 방향이다.
    AltTab(bool),
    /// `Ctrl` 을 뗐다 — 쥔 채 돌던 스위처의 **확정**(§10-21ⓕ2).
    CtrlReleased,
    /// 창 버튼(최소화·최대화·닫기)을 눌렀다(`pytmux-1`).
    ///
    /// `Act(Action)` 으로 안 보내는 이유: 이건 **pytmux 의 의도가 아니다**. `base::Action`
    /// 은 도움말·바인딩 표·팔레트가 함께 쓰는 목록인데, 터미널 안에서 도는 정본 클라에는
    /// 창이 없어 최소화라는 명령 자체가 성립하지 않는다. 넣으면 그 표에 **한 클라에서만
    /// 뜻이 있는 항목**이 생기고, 팔레트에서 고르면 아무 일도 안 일어난다(이 저장소가
    /// 되풀이해 온 "못 하는 것을 목록에 두지 않는다").
    WindowButton(titlebar::Button),
    /// 이벤트 콜백이 **키보다 먼저** 판정한 액션(지금은 글자 배율 셋 — §10-21ⓐ).
    ///
    /// `RawKey` 로 안 보내는 이유: 그 길은 core 의 모드·바인딩 표를 지나는데, 이 조합은
    /// 그 표 밖에서 **뷰가 가로챈** 것이다(붙여넣기와 같은 부류). 액션으로 실어 보내면
    /// 팔레트에서 고른 것과 **완전히 같은 자리**로 떨어진다.
    Act(Action),
}

impl TypedActionView for SessionView {
    type Action = ViewAction;

    /// 들어온 것을 모두 **한 곳**에서 처리한다 — 두 자리에서 해석하면 키로 하는 일과
    /// 다른 경로로 하는 일이 갈라진다(TUI 와 같은 구조).
    fn handle_action(&mut self, action: &ViewAction, ctx: &mut ViewContext<Self>) {
        let dirty = match action {
            ViewAction::RawKey(key, mods) => self.handle_key(*key, *mods),
            ViewAction::Act(action) => self.apply_action(*action),
            ViewAction::AltTab(forward) => self.alt_tab_step(*forward),
            ViewAction::CtrlReleased => self.release_ctrl(),
            ViewAction::ChromeClick(target) => self.chrome_click(*target),
            ViewAction::WindowButton(button) => self.press_window_button(*button),
            ViewAction::PanelClick(target) => self.panel_click(*target),
            ViewAction::Wheel { up, at } => self.handle_wheel(*up, *at),
            ViewAction::MouseMove(at) => self.handle_mouse_move(*at),
            ViewAction::MouseDown(at, shift) => self.handle_mouse_down(*at, *shift),
            ViewAction::RightMouseDown(at) => self.handle_right_mouse_down(*at),
            ViewAction::MouseDrag(at) => self.handle_mouse_drag(*at),
            ViewAction::MouseUp(at) => self.handle_mouse_up(*at),
            ViewAction::TabPress(i) => {
                self.tab_drag = Some(*i);
                self.tab_drag_over = None;
                false
            }
            ViewAction::Paste(text) => self.handle_paste(text),
            ViewAction::Typed(text) => self.handle_typed(text),
            ViewAction::Preedit(text) => self.handle_preedit(text),
            ViewAction::PasteRequest => {
                // GUI 에는 bracketed paste 를 대신 해 줄 바깥 터미널이 없다. 창 계층이
                // 아는 클립보드를 읽어 흘린다 — 마커를 감쌀지는 여전히 서버가 정한다.
                // 무엇을 어떻게 붙일지는 **팔레트로 들어온 것과 같은 함수**가 정한다
                // (입구가 둘이어도 경로는 하나 — pytmux-159·363·364).
                let content = ctx.clipboard().read();
                self.paste_from_clipboard(content)
            }
        };
        if dirty {
            ctx.notify();
        }
        // ★ 전체 화면(§10-21ⓘ3) — **창을 쥔 자리**가 여기다.
        //
        //   상태를 우리가 안 든다: 토글은 상류가 창에 대고 하고, "지금 전체 화면인가"의
        //   진실은 `fullscreen_state()` 에 있다. 사본을 들면 OS 가 바꾼 상태(맥의 초록
        //   버튼·`F11`)와 갈려 토글이 한 번 헛돈다.
        //
        //   격자 재보고는 따로 안 한다 — 창 크기가 바뀌면 자리표가 다시 재지고
        //   `report_size` 가 그 값으로 서버에 알린다(평소 리사이즈와 같은 길).
        // ★ 클립보드 읽기(`paste-clipboard` · pytmux-159) — 전체 화면과 **같은 자리**다.
        //   `apply_action` 에는 창이 없어 한 칸에 실려 왔고, 여기서 `PasteRequest` 와
        //   똑같은 함수로 떨어진다.
        if std::mem::take(&mut self.paste_requested) {
            let content = ctx.clipboard().read();
            if self.paste_from_clipboard(content) {
                ctx.notify();
            }
        }
        if std::mem::take(&mut self.fullscreen_requested) {
            if let Some(window) = ctx.windows().platform_window(ctx.window_id()) {
                window.toggle_fullscreen();
            }
            ctx.notify();
        }
        // ★ 창 버튼(`pytmux-1`) — 전체 화면과 **같은 자리**다(창을 쥔 곳이 여기뿐이다).
        //
        //   ⛔ 뜻을 우리가 다시 적지 않는다: 상류 `ViewContext` 가 셋을 그대로 준다.
        //   "최대화 중인가"의 상태도 안 든다 — 사본을 들면 OS 가 바꾼 상태(맥 초록 버튼·
        //   창 스냅·`F11`)와 갈려 토글이 한 번 헛돈다(전체 화면에서 배운 것).
        match self.window_op.take() {
            Some(titlebar::Button::Minimize) => ctx.minimize_window(),
            Some(titlebar::Button::Maximize) => ctx.toggle_maximized_window(),
            // 닫기는 여기 안 온다(`press_window_button` 이 `quit_requested` 로 접는다).
            Some(titlebar::Button::Close) | None => {}
        }
        // 큐에 쌓인 것은 다음 펌프가 보낸다. 여기서 바로 보내지 않는 이유는 실패 처리와
        // 순서가 핸들러마다 흩어지지 않게 하려는 것이다(`flush_outgoing`).
        if self.quit_requested {
            // GUI 에서는 창을 닫는 것이 종료다(데모 뷰와 같다).
            ctx.close_window();
            // ★ **그런데 창이 안 닫힌다**(2026-07-30 실측). 상류
            // `ViewContext::close_window` 는 `TerminationMode::Cancellable` 로 비동기
            // 요청을 넣는데, 이 백엔드에서는 창도 프로세스도 그대로 남았다 — `prefix d`
            // (detach)를 눌러도 클라가 살아 있었다. `detach-client`·`p_d` 는 패리티 표에
            // **Done 으로 세어져 있었고**, 그 사실은 라이브를 돌릴 때까지 아무도 몰랐다
            // (오라클은 "quit_requested 가 섰나"까지만 봤다).
            //
            // 여기서 명시적으로 끝낸다. detach 는 "이 클라가 빠진다"이고 `restart-all` 은
            // **이미 후계 프로세스를 띄운 뒤**라, 남아 있는 것이 어느 쪽이든 틀렸다
            // (후자는 클라가 둘로 늘어난다).
            //
            // 상류를 고치는 것이 옳은 자리이지만 그건 별개 조사다(PROVENANCE §1 —
            // 시그니처를 바꾸지 않는다). 그때까지 이 줄이 계약을 지킨다.
            std::process::exit(0);
        }
    }
}

#[cfg(test)]
#[path = "session_view_tests.rs"]
mod tests;

/// 글자 속성이 그리는 자리까지 닿나 — pytmux-33 축 ⑷ 의 자.
#[cfg(test)]
#[path = "attr_render_conformance.rs"]
mod attr_render_conformance;

/// 캔버스 한 조각의 **칸 폭을 못박되 글리프는 안 자르는** 상자(pytmux-270).
///
/// # 무엇이 잘리고 있었나
///
/// 격자를 지키려고 비 ASCII 조각을 [`ConstrainedBox`] 로 `칸수 × 칸너비` 에 묶었다.
/// 그런데 그 상자는 아이의 `constraint.max` 를 좁히고, [`Text`] 는 그 값을 그대로
/// `layout_line(..., max_width, clip_config)` 에 넘긴다. 그리고 `ClipConfig::default()`
/// 는 `{direction: End, style: Fade}` 다 — **넘치는 만큼이 오른쪽에서 지워진다.**
///
/// 보조 글꼴(`Segoe UI Symbol`·`Segoe UI Emoji`)은 비례폭이라 전진폭이 칸 너비의
/// 정수배가 아니고, 그래서 `★`·`❋` 는 오른쪽이 잘리고 `⚠️` 는 왼쪽 반쪽만 남았다.
/// ⇒ **격자를 지키려고 넣은 장치가 격자를 넘는 글리프를 지우고 있었다.**
///
/// ★ 그 판정이 예측하는 **자르는 방향**(`End` = 오른쪽)이 제보의 화소 대조와 정확히
/// 맞는다 — 그래서 「그럴듯한 기전」이 아니라 증거다.
///
/// # 이 상자가 하는 일
///
/// [`PanelBox`] 와 **같은 처방**이다: 아이에게는 한도를 안 걸고(자연폭으로 그리게 두고)
/// **돌려주는 크기만 정한 값**으로 준다. 그래서 격자는 그대로 서고, 칸을 넘는 글리프는
/// 잘리는 대신 이웃 칸으로 조금 넘친다.
///
/// ⛔ 넘치게 두는 쪽을 고른 이유: 정본 TUI 는 이 판단을 **호스트 단말에 맡기고**, 단말은
/// 넘치게 둔다. 줄여 넣는 쪽(그 조각만 글꼴 크기를 낮춘다)은 같은 줄 안에서 글자 크기가
/// 들쭉날쭉해져 정본과 더 멀어진다.
struct CellBox {
    child: Box<dyn Element>,
    width: f32,
    size: Option<Vector2F>,
    origin: Option<Point>,
}

impl CellBox {
    fn new(child: Box<dyn Element>, width: f32) -> Self {
        Self { child, width, size: None, origin: None }
    }
}

impl Element for CellBox {
    fn layout(
        &mut self,
        constraint: SizeConstraint,
        ctx: &mut LayoutContext,
        app: &AppContext,
    ) -> Vector2F {
        // ⛔ **가로 한도를 안 내려보낸다** — 내려보내는 순간 `Text` 가 그 값으로 페이드한다.
        //    세로는 부모 것 그대로다.
        let inner = SizeConstraint::new(
            vec2f(0., constraint.min.y()),
            vec2f(f32::INFINITY, constraint.max.y()),
        );
        let child = self.child.layout(inner, ctx, app);
        // 아이가 얼마나 넓든 이 칸이 격자에서 차지하는 폭은 우리가 정한 값이다.
        let size = vec2f(self.width, child.y());
        self.size = Some(size);
        size
    }

    fn after_layout(&mut self, ctx: &mut AfterLayoutContext, app: &AppContext) {
        self.child.after_layout(ctx, app);
    }

    fn paint(&mut self, origin: Vector2F, ctx: &mut PaintContext, app: &AppContext) {
        self.origin = Some(Point::from_vec2f(origin, ctx.scene.z_index()));
        self.child.paint(origin, ctx, app);
    }

    fn size(&self) -> Option<Vector2F> {
        self.size
    }

    fn origin(&self) -> Option<Point> {
        self.origin
    }

    fn dispatch_event(
        &mut self,
        event: &DispatchedEvent,
        ctx: &mut EventContext,
        app: &AppContext,
    ) -> bool {
        self.child.dispatch_event(event, ctx, app)
    }
}

/// 판의 **가로 크기를 못박는** 상자 — 안이 무엇을 그리든 이 상자의 폭은 준 값이다.
///
/// # 왜 `ConstrainedBox` 로는 안 되나 (pytmux-158)
///
/// `ConstrainedBox::with_width` 는 이름과 달리 폭을 **정하지 않는다**. 아이에게
/// `min = max = 폭` 을 **주기만** 하고, 돌려주는 크기는 `self.child.layout(..)` 이 잰
/// 값 그대로다 — 아이가 그 한도를 지킬 때만 폭이 그 값이 된다. 그런데 판 사슬의 두
/// 마디가 안 지킨다:
///
/// - [`Container`] 는 `아이 크기 + 여백` 을 그대로 돌려준다(자기 `constraint` 를 안 본다).
/// - 가로 [`Flex`] 는 **자기 아이들에게 가로 최대를 무한대로** 준다
///   (`SizeConstraint::child_constraint_along_axis`) — 그래서 줄이 `Flex::row` 로
///   지어져 있으면 그 안의 글자는 한도를 아예 못 받고 자연 폭으로 눕는다. 그 합은
///   `constrain_horizontal_bounds_to_parent` 를 안 켠 이상 잘리지도 않는다.
///
/// 그래서 **아래쪽 한도(최소 폭)만 지켜졌다** — 세로 `Flex` 는 마지막에
/// `size.x = size.x.max(constraint.min.x())` 를 하기 때문이다. 위쪽은 열려 있어서
/// 긴 줄 하나가 판을 그만큼 넓혔다: `:ncd` 에서 `C:\` 만 있을 때는 좁고
/// `Microsoft Mouse and Keyboard Center` 가 목록에 들어오면 넓어졌다.
///
/// # 그래서 이 상자가 하는 일
///
/// **잰 값이 아니라 정한 값을 돌려준다**(`layout` 의 마지막 줄). 아이에게는 가로만 꽉
/// 조인 한도를 내려보내므로 한도를 지키는 아이([`Text`] 가 그렇다 — 스스로 잘라 준다)는
/// 그 안에서 알아서 줄고, 안 지키는 아이가 넘겨 그린 것은 [`Clipped`] 가 판 밖으로
/// 안 내보낸다.
///
/// ⚠ **세로는 안 건드린다.** 판 높이는 줄 수가 정하고 그 수는 `panel_budget` 이 이미
/// 화면 높이로 잰다 — 여기서 또 정하면 두 곳이 같은 것을 말하게 된다.
struct PanelBox {
    child: Box<dyn Element>,
    width: f32,
    size: Option<Vector2F>,
    origin: Option<Point>,
}

/// 떠 있는 판의 목록 오른쪽에 세우는 **표시용 스크롤바**(pytmux-374 ⑴).
///
/// # 왜 캔버스의 그것(`splitter::ScrollHint`)을 못 쓰나
///
/// 저것은 **셀 격자** 위에 그린다 — `SplitterOverlay` 가 칸 크기를 알고 있어 「테두리
/// 열의 몇 번째 행」으로 자리를 말할 수 있다. 판은 셀이 아니라 위젯이라 그 좌표가 아예
/// 없다. 그래서 **자리는 여기서 재고 값은 core 가 준다**(`base::scrollbar::list_fraction`)
/// — 반올림·썸 하한·「그릴 것이 없다」 판정을 두 벌로 두지 않는 것이 요점이다.
///
/// # 왜 폭을 «먹나»
///
/// 캔버스 쪽은 테두리 **선 위**에 얹어 칸을 안 먹는다(격자를 건드리면 서버에 보고하는
/// 행·열이 바뀐다). 판에는 그 제약이 없고, 대신 글자 위에 겹치면 값칸의 오른쪽 끝이
/// 가려진다 — 그래서 실제로 폭을 차지한다(정본 `#sets` 의 `scrollbar-size-vertical: 2`
/// 와 같은 자리다).
///
/// # 왜 «판의» 오른쪽 끝인가
///
/// 목록 열의 오른쪽 끝은 **가장 긴 줄**이 정하므로 필터도 없는 이 판에서조차 값이
/// 바뀔 때마다 흔들린다. 부모가 준 최대 폭(= `PanelBox` 가 못박은 판 안쪽 폭)에 붙이면
/// 막대가 언제나 같은 자리에 서고, 그것이 정본 `#sets` 의 오른쪽 가장자리와도 같다.
struct PanelScrollBar {
    child: Box<dyn Element>,
    /// 트랙을 1.0 으로 본 (썸 시작, 썸 길이) — 값은 core 가 정한다.
    start: f64,
    len: f64,
    size: Option<Vector2F>,
    origin: Option<Point>,
}

impl PanelScrollBar {
    /// 막대 자체의 폭과 목록에서 띄우는 간격(px).
    ///
    /// 캔버스 쪽 표시 막대(`splitter::HINT_PX` = 3)와 같은 굵기다 — 한 앱 안에서 같은
    /// 뜻의 표시가 굵기까지 같아야 「같은 것」으로 읽힌다.
    const BAR_PX: f32 = 3.;
    const GAP_PX: f32 = 6.;
    const TAKES: f32 = Self::BAR_PX + Self::GAP_PX;

    fn new(child: Box<dyn Element>, start: f64, len: f64) -> Self {
        Self { child, start, len, size: None, origin: None }
    }
}

impl Element for PanelScrollBar {
    fn layout(
        &mut self,
        constraint: SizeConstraint,
        ctx: &mut LayoutContext,
        app: &AppContext,
    ) -> Vector2F {
        // 아이에게는 막대 몫을 뺀 폭을 준다 — 안 빼면 긴 줄이 막대 밑으로 들어간다.
        let inner = SizeConstraint::new(
            vec2f((constraint.min.x() - Self::TAKES).max(0.), constraint.min.y()),
            vec2f((constraint.max.x() - Self::TAKES).max(0.), constraint.max.y()),
        );
        let child = self.child.layout(inner, ctx, app);
        // 판 안쪽 폭이 유한하면 그것을 쓴다(위 머리말 §왜 판의 오른쪽 끝인가).
        let width = if constraint.max.x().is_finite() {
            constraint.max.x()
        } else {
            child.x() + Self::TAKES
        };
        let size = vec2f(width, child.y());
        self.size = Some(size);
        size
    }

    fn after_layout(&mut self, ctx: &mut AfterLayoutContext, app: &AppContext) {
        self.child.after_layout(ctx, app);
    }

    fn paint(&mut self, origin: Vector2F, ctx: &mut PaintContext, app: &AppContext) {
        self.origin = Some(Point::from_vec2f(origin, ctx.scene.z_index()));
        self.child.paint(origin, ctx, app);
        let Some(size) = self.size else {
            return;
        };
        let (w, h) = (size.x(), size.y());
        if h <= 0. {
            return;
        }
        let x = origin.x() + w - Self::BAR_PX;
        let bar = |y: f32, height: f32, color: warpui::color::ColorU, ctx: &mut PaintContext| {
            ctx.scene
                .draw_rect_without_hit_recording(warpui::geometry::rect::RectF::new(
                    vec2f(x, y),
                    vec2f(Self::BAR_PX, height),
                ))
                .with_background(warpui::elements::Fill::Solid(color))
                .with_corner_radius(warpui::elements::CornerRadius::with_all(
                    warpui::elements::Radius::Percentage(50.),
                ));
        };
        // 트랙 먼저, 그 위에 썸. 트랙이 없으면 「끝까지 내려왔다」와 「막대가 짧다」가
        // 구별되지 않는다(정본도 `scrollbar-background` 를 따로 칠한다).
        bar(origin.y(), h, theme::BORDER, ctx);
        let thumb_h = (h * self.len as f32).max(Self::BAR_PX);
        // 시작 자리는 **트랙 안에서** 자른다 — 반올림 한 톨이 판 밖으로 새면 그림자
        // 위에 점이 하나 찍힌다.
        let thumb_y = origin.y() + (h * self.start as f32).clamp(0., (h - thumb_h).max(0.));
        bar(thumb_y, thumb_h, theme::FOCUS, ctx);
    }

    fn size(&self) -> Option<Vector2F> {
        self.size
    }

    fn origin(&self) -> Option<Point> {
        self.origin
    }

    fn dispatch_event(
        &mut self,
        event: &DispatchedEvent,
        ctx: &mut EventContext,
        app: &AppContext,
    ) -> bool {
        self.child.dispatch_event(event, ctx, app)
    }

    #[cfg(test)]
    fn debug_text_content(&self) -> Option<String> {
        self.child.debug_text_content()
    }
}

impl PanelBox {
    fn new(child: Box<dyn Element>, width: f32) -> Self {
        Self { child, width, size: None, origin: None }
    }
}

impl Element for PanelBox {
    fn layout(
        &mut self,
        constraint: SizeConstraint,
        ctx: &mut LayoutContext,
        app: &AppContext,
    ) -> Vector2F {
        // 가로만 꽉 조이고 세로는 부모 것 그대로 내려보낸다.
        let inner = SizeConstraint::new(
            vec2f(self.width, constraint.min.y()),
            vec2f(self.width, constraint.max.y()),
        );
        let child = self.child.layout(inner, ctx, app);
        // ⛔ 이 한 줄이 이 상자의 전부다 — 아이가 얼마나 넓든 폭은 우리가 정한 값이다.
        let size = vec2f(self.width, child.y());
        self.size = Some(size);
        size
    }

    fn after_layout(&mut self, ctx: &mut AfterLayoutContext, app: &AppContext) {
        self.child.after_layout(ctx, app);
    }

    fn paint(&mut self, origin: Vector2F, ctx: &mut PaintContext, app: &AppContext) {
        self.origin = Some(Point::from_vec2f(origin, ctx.scene.z_index()));
        self.child.paint(origin, ctx, app);
    }

    fn size(&self) -> Option<Vector2F> {
        self.size
    }

    fn origin(&self) -> Option<Point> {
        self.origin
    }

    fn dispatch_event(
        &mut self,
        event: &DispatchedEvent,
        ctx: &mut EventContext,
        app: &AppContext,
    ) -> bool {
        self.child.dispatch_event(event, ctx, app)
    }

    #[cfg(test)]
    fn debug_text_content(&self) -> Option<String> {
        self.child.debug_text_content()
    }
}

/// 스레드에서 돌린 `scp` 한 번의 결과 — 원격 탭에 붙일 **그림의 경로**(pytmux-159).
///
/// 성공·실패 **양쪽 다 붙인다**(정본과 같다). 실패했다고 아무것도 안 붙이면 사용자는
/// 스크린샷을 다시 찍는 수밖에 없는데, 로컬 경로라도 손에 쥐면 스스로 옮길 수 있다 —
/// 다만 그것이 원격에서 안 열린다는 **경고와 함께** 준다.
struct RemoteImage {
    /// 붙일 경로. 성공이면 원격 자리, 실패면 이 상자의 자리다.
    path: String,
    ok: bool,
}

/// 스레드에서 돌린 셸 명령의 결과.
struct ShellOutcome {
    code: i32,
    text: String,
    /// `if-shell` 이면 **성공했을 때 부를** 팔레트 명령 이름. `run-shell` 이면 `None`.
    then: Option<String>,
    /// `if-shell` 의 **실패 갈래**(파이썬 `_if_shell` 의 `else_cmd`). 셋째 인자라
    /// 흔히 비어 있다.
    otherwise: Option<String>,
}
