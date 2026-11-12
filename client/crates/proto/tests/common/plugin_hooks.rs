//! **플러그인 클라 훅 대장** — 정본 `Registry` 의 클라 훅 × 플러그인 전수가 GUI 에서 어떻게 되나(pytmux-570).
//!
//! 정본 클라의 플러그인 훅은 파이썬 객체를 주고받아 소켓을 못 건넌다. 칸마다 넷 중 하나다:
//!
//! - [`Cell::Declared`] — 서버가 **선언형 다리**로 나른다(`plugin_surface.<칸>` · `plugin_cells` · `plugin_badges` …).
//!   값은 다리 이름이고, 그 이름은 정본 서버에 실재해야 한다(픽스처 `bridges`).
//! - [`Cell::Ported`] — 손으로 옮겼다. 값은 `<크레이트 경로>::<그 파일에 있는 낱말>` — 시험이 파일을 열어 본다.
//! - [`Cell::Missing`] — GUI 에 없다. **이슈 번호 없이는 못 선다** — 그 수에 상한이 있고 R7 의 닫힘 조건은 0 이다.
//! - [`Cell::NotClient`] — 클라에서 할 일이 GUI 에는 없다(사유).
//!
//! 칸 전수는 생성기가 정본에서 뽑는다(`scripts/gen_plugin_hooks.py` → `plugin_hooks.json`) — 정본 플러그인이 훅을
//! 하나 더 쓰기 시작하면 `plugin_hook_ledger.rs` 가 운다. 플러그인 이름 `*` 은 «그 훅을 등록한 플러그인 전부» 다
//! (같은 다리로 건너는 칸이 열셋씩 되는 훅 — `commands` 등). 구체 줄이 `*` 보다 먼저다.
//!
//! ⚠ «옮겼다» 는 «같게 군다» 가 아니다 — `Ported` 칸의 동작 일치는 칸마다의 적합성 자가 잰다.

#![allow(dead_code)]

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Cell {
    Declared(&'static str),
    Ported(&'static str),
    Missing(&'static str),
    NotClient(&'static str),
}

use Cell::{Declared, NotClient, Ported};

/// (훅, 플러그인 또는 `*`, 칸, 근거).
pub static HOOKS: &[(&str, &str, Cell, &str)] = &[
    // ── 자료 훅 — 서버의 `plugin_surface` 가 같은 자료를 나른다(serverio._plugin_surface) ──
    ("commands", "*", Declared("plugin_surface.commands"), "팔레트 줄 — `base::plugins::PluginSurface::palette_rows`"),
    ("noarg", "*", Declared("plugin_surface.noarg"), "인자 없는 명령 표"),
    ("pane_scoped", "*", Declared("plugin_surface.pane_scoped"), "패널 대상 명령(pytmux-587)"),
    ("settings", "*", Declared("plugin_surface.settings"), "설정 줄 + `setting_cats`"),
    ("command_options", "*", Declared("plugin_surface.command_options"), "선택지 판(pytmux-634)"),
    ("command_option_current", "*", Declared("plugin_surface.command_options"), "선택지의 지금 값 — 같은 칸에 실린다(pytmux-634)"),
    // ── 글루 설치 — 그 글루가 닿는 다리로 센다 ─────────────────────────────────
    ("attach_client", "blocks", Ported("proto/src/blocks.rs::caps"), "블록 상태 — `blocks` 메시지를 받는 쪽"),
    ("attach_client", "calendar", Declared("plugin_native"), "달력 상태는 서버가 쥐고 셀·자료로 준다"),
    ("attach_client", "claude-code", Declared("plugin_badges"), "Claude 상태줄 칸·클릭존 — 배지 다리 + `proto::session` 클릭존"),
    ("attach_client", "claude-name-sync", Declared("plugin_screen"), "편집기 진입점 — 판 스펙"),
    ("attach_client", "claude-prompt-history", Ported("proto/src/prompt_bar.rs::client_render"), "이력 표·고정 바(pytmux-587)"),
    ("attach_client", "claude-resume", Declared("plugin_screen"), "피커 진입점 — 판 스펙"),
    ("attach_client", "claude-token-usage-view", Declared("plugin_cells"), "패널 오버레이 집합 — 셀 다리"),
    ("attach_client", "clock", Declared("plugin_native"), "시계 상태는 서버가 쥔다"),
    ("attach_client", "ime-indicator", Ported("gui/src/ime.rs::oskbd"), "입력 소스 질의(pytmux-590)"),
    ("attach_client", "mdir", Declared("plugin_screen"), "목록 요청 글루 — 판 스펙"),
    ("attach_client", "ncd", Declared("plugin_screen"), "트리 요청 글루 — 판 스펙"),
    ("attach_client", "p4-show-submitted-changelists", Declared("plugin_screen"), "목록·상세 요청 — 판 스펙"),
    ("attach_client", "rec", Ported("proto/src/info.rs::client_status_tabs"), "`show_capture_info` → 캡처 탭(pytmux-589)"),
    // ── 명령 ───────────────────────────────────────────────────────────────────
    ("handle_command", "blocks", Ported("base/src/keymap.rs::\"select-blocks\""), "블록 고르기 모드는 네이티브 액션"),
    ("handle_command", "calendar", Ported("gui/src/session_view.rs::push_overlay_toggle"), "클라가 스스로 처리하는 명령(`plugin_client_cmds.json`)"),
    ("handle_command", "claude-code", Declared("plugin_command_action"), "서버가 액션 이름을 받는다(`plugin_server_actions.json`)"),
    ("handle_command", "claude-name-sync", Declared("plugin_screen"), "`plugin_open` → 판"),
    ("handle_command", "claude-prompt-history", Declared("plugin_command_action"), "서버 액션"),
    ("handle_command", "claude-resume", Declared("plugin_screen"), "`plugin_open` → 판"),
    ("handle_command", "claude-token-usage-view", Ported("gui/src/session_view.rs::push_overlay_toggle"), "오버레이 토글"),
    ("handle_command", "clock", Ported("gui/src/session_view.rs::push_overlay_toggle"), "오버레이 토글"),
    ("handle_command", "ime-indicator", Declared("plugin_command_action"), "서버 액션(`cmdmap`)"),
    ("handle_command", "mdir", Declared("plugin_screen"), "`plugin_open` → 판"),
    ("handle_command", "ncd", Declared("plugin_screen"), "`plugin_open` → 판"),
    ("handle_command", "p4-show-submitted-changelists", Declared("plugin_screen"), "`plugin_open` → 판"),
    ("handle_command", "rec", Declared("plugin_command_action"), "서버 액션"),
    // ── 메시지 ─────────────────────────────────────────────────────────────────
    ("handle_message", "blocks", Ported("proto/src/blocks.rs::caps"), "`blocks` 메시지"),
    ("handle_message", "claude-code", Declared("plugin_screen"), "회신이 판 스펙으로 온다"),
    ("handle_message", "*", Declared("plugin_screen"), "회신(목록·상세)이 판 스펙으로 온다 — 클라가 판을 짓지 않는다"),
    // ── blocks ────────────────────────────────────────────────────────────────
    ("client_caps", "blocks", Ported("proto/src/blocks.rs::caps"), "`hello` 의 `caps: [\"blocks\"]`"),
    ("client_mode_key", "blocks", Ported("base/src/block.rs::SelectBlocks"), "블록 고르기 모드의 키"),
    ("client_panes_changed", "blocks", Ported("gui/src/session_view.rs::drop_block_pick_unless_selecting"), "사라진 패널의 고르기 자리를 접는다(pytmux-605)"),
    ("client_click", "blocks", Ported("gui/src/session_view.rs::client_click"), "블록 목록 클릭"),
    ("client_click", "claude-prompt-history", Ported("gui/src/session_view.rs::client_click"), "고정 바 클릭 → 이력 판(pytmux-587)"),
    ("client_hit", "blocks", Ported("gui/src/session_view.rs::client_hit"), "밑줄 후보 판정"),
    ("client_hit", "claude-prompt-history", Ported("gui/src/session_view.rs::client_hit"), "바 밑 글은 밑줄 후보가 아니다(pytmux-541)"),
    ("client_statusbar_badges", "blocks", Ported("gui/src/session_view.rs::block_mode_badge"), "블록 모드 배지"),
    ("client_render", "blocks", Ported("gui/src/session_view.rs::block_mode_badge"), "블록 칸 장식"),
    // ── 오버레이(clock · calendar · token-usage-view) — 서버가 셀로 그린다 ─────────────
    ("client_overlay", "*", Declared("plugin_cells"), "패널 덮개 셀"),
    ("client_close_overlay", "*", Declared("plugin_overlay_action"), "닫기 — 서버 액션"),
    ("client_overlay_covers", "*", Declared("plugin_dim_panes"), "덮인 패널 — 서버가 알려 준다"),
    ("client_overlay_key", "calendar", Declared("plugin_overlay_action"), "달력 월 이동 — `proto::session` 의 오버레이 키 규칙"),
    ("client_tick", "calendar", Declared("plugin_cells"), "1초 갱신은 서버가 셀을 다시 보낸다"),
    ("client_tick", "claude-token-usage-view", Declared("plugin_cells"), "같은 이유"),
    ("client_tick", "clock", Declared("plugin_cells"), "같은 이유"),
    ("client_tick", "ime-indicator", Ported("gui/src/ime.rs::_LINUX_MIN_GAP"), "입력 소스 폴링"),
    // ── ime-indicator ─────────────────────────────────────────────────────────
    ("client_key", "ime-indicator", Ported("gui/src/ime.rs::current_source_id"), "확정 키 휴리스틱이 메우던 «지금 입력기» 를 OS 질의로 낸다(창 앱은 늘 묻을 수 있다)"),
    ("client_input_badge", "ime-indicator", Ported("gui/src/session_view.rs::fn ime_chip"), "판 안 입력줄 배지(pytmux-14·392)"),
    ("client_render", "ime-indicator", Declared("plugin_cells"), "캔버스 배지는 서버 셀"),
    ("client_status", "ime-indicator", Ported("proto/src/session.rs::fn absorb_ime_show"), "status 의 `ime_show` → 판 안 배지가 끄기를 따른다(pytmux-646)"),
    ("client_unload", "ime-indicator", NotClient("폴링 타이머 해제 — GUI 폴링은 뷰 수명과 함께 끝나 내릴 것이 없다"), "플러그인 끄기는 서버가 셀을 멈춘다"),
    // ── claude-code ───────────────────────────────────────────────────────────
    ("client_render", "claude-code", Ported("proto/src/session.rs::perm"), "푸터 클릭존(권한 모드·원격 제어 · pytmux-603)"),
    ("client_status", "claude-code", Ported("base/src/hooks.rs::claude-limit"), "`claude-limit` 훅(pytmux-588)"),
    ("client_statusbar", "claude-code", Declared("plugin_badges"), "상태줄 Claude 칸 · esc 하단 동선(pytmux-604)"),
    ("client_statusbar_init", "*", Declared("plugin_badges"), "상태줄 칸의 자료"),
    ("client_statusbar_update", "*", Declared("plugin_badges"), "상태줄 칸의 갱신"),
    ("client_prompt_text", "claude-code", Ported("proto/src/session.rs::client_prompt_text"), "작성창 시드 — 입력박스 긁기"),
    ("client_tab_glyph", "claude-code", Ported("proto/src/tabs.rs::client_tab_glyph"), "탭 글리프"),
    // ── claude-prompt-history ─────────────────────────────────────────────────
    ("client_render", "claude-prompt-history", Ported("proto/src/prompt_bar.rs::client_render"), "고정 바·미리보기(pytmux-587)"),
    ("client_status", "claude-prompt-history", Ported("proto/src/session.rs::ph_panes"), "`ph_panes`·`ph_max_lines`"),
    // ── rec ───────────────────────────────────────────────────────────────────
    ("client_statusbar_badges", "rec", Declared("plugin_badges"), "REC 배지 + 클릭(pytmux-589)"),
    ("client_status_tabs", "rec", Ported("proto/src/info.rs::client_status_tabs"), "정보 판의 캡처 탭"),
];

/// 한 칸의 분류 — 구체 줄이 먼저, 없으면 `*`.
pub fn cell_of(hook: &str, plugin: &str) -> Option<(Cell, &'static str)> {
    HOOKS
        .iter()
        .find(|(h, p, _, _)| *h == hook && *p == plugin)
        .or_else(|| HOOKS.iter().find(|(h, p, _, _)| *h == hook && *p == "*"))
        .map(|(_, _, c, why)| (*c, *why))
}
