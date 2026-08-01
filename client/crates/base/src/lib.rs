//! pytmux 클라이언트의 **백엔드 중립 계층**.
//!
//! # 이 크레이트의 존재 이유
//!
//! 클라이언트는 두 가지 모습으로 돈다 — 네이티브 창(GUI)과 터미널(TUI). 두 백엔드는
//! 엘리먼트 라이브러리가 서로 다르므로 **뷰 코드는 두 벌**이 될 수밖에 없다. 하지만
//! 상태·명령·키바인딩까지 두 벌이 되면 두 클라이언트가 조용히 갈라진다.
//!
//! 그래서 이 크레이트는 **UI 의존이 하나도 없다.** `warpui` 도 `warpui_core` 도 쓰지
//! 않는다(`Cargo.toml` 을 보면 UI 의존이 아예 없다). 백엔드 타입이 상태 계층으로 새어
//! 들어오는 것을 문서가 아니라 **컴파일로** 막는다:
//!
//! ```sh
//! cargo check -p base   # UI 크레이트 없이 이것만으로 빌드된다
//! ```
//!
//! # 담는 것 / 담지 않는 것
//!
//! - 담는다: 화면에 무엇이 있는지(상태), 무슨 일이 일어날 수 있는지(액션), 어떤 키가
//!   무슨 액션인지(키맵), 액션이 상태를 어떻게 바꾸는지(전이).
//! - 담지 않는다: 어떻게 그리는지. 색·여백·글꼴은 각 뷰의 몫이다.

pub mod atomicfile;
pub mod block;
pub mod chrome;
pub mod config;
pub mod editor;
pub mod hooks;
pub mod i18n;
pub mod keymap;
pub mod options;
pub mod plugins;
pub mod keys;
pub mod restart;
pub mod screens;
pub mod scrollbar;

pub use block::{Block, BlockList, BlockState};
pub use chrome::{Badge, Chrome, ChromeCtx, ChromeFocus, ChromeKey, TabSpot};
pub use config::Config;
pub use hooks::{HookEvent, HookRun, HookWatcher, Hooks, SetHook};
pub use options::{COMMAND_OPTIONS, CommandOptions, Choice, OptionPick, OptionSpec};
pub use plugins::{PluginCommand, PluginMenuItem, PluginSetting, PluginSurface, SettingRef,
    SettingsRow};
pub use keymap::{
    Action, BINDINGS, Binding, Dir, MENU, MENU_GROUPS, MENU_GROUP_LABELS,
    MENU_TOGGLES,
    MOUSE_GESTURES, MenuToggles, menu_toggle_mark,
    MENU_TOPLEVEL, MenuEntry, MenuRow, MenuTop, PALETTE, PALETTE_CATS, PREFIX_BINDINGS,
    PALETTE_CAT_ALL, PaletteEntry, EnumOpt, LAYOUT_PRESETS, ServerOpt, TabMove, menu_entry,
    menu_group_label, menu_is_toggle, menu_rows, palette_cat_label, palette_matches,
    palette_matches_in, palette_matches_with, palette_tab_cat, palette_tab_counts,
    palette_tab_counts_with, palette_tab_labels,
    palette_tab_with_results,
};
pub use screens::{PanelTarget, Prompt, Screen, ScreenKey, Screens, TabFacts};
pub use keys::{
    InputMode, Key, KeyOutcome, Mods, SCROLL_BINDINGS, ScrollAmount, ScrollBinding, encode,
    interpret,
};
