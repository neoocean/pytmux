//! **설정을 이번 판에 먹이는 한 함수** — 기동과 `source-file` 이 같이 지난다(pytmux-574).
//!
//! 대부분의 칸은 뷰가 `self.config` 에서 **쓸 때마다** 읽으므로 설정을 갈아 끼우는 것만으로
//! 따라온다. 아래 것은 다르다 — **설정 밖의 상태**(합성 상태의 딤)에 한 번 밀어 넣어야 먹는다.
//! 기동과 다시 읽기가 이 일을 따로 하면 갈린다.
//!
//! ⛔ **모호폭(`ambiguous-width`)은 여기서 안 민다**(pytmux-578 · 사람 결정 2026-11-13: 정본을
//! 따른다). 정본 `reload_config` 는 그 칸을 안 건드린다 — 모호폭은 기동 때 한 번(정본
//! `run_client` 의 감지) 정하고, 런타임에는 `set ambiguous-width` 만 바꾼다(서버에도
//! `set_ambig` 로 알린다). 종전(묶음 2)에는 다시 읽기가 폭 판정 전역을 밀었는데 그러면 **서버에
//! 안 알린 채** 클라 폭만 바뀌어 격자와 그림이 갈렸다.
//!
//! prefix 는 키 모드(`base::keys::ModeState`)가 들고 있어 호출부가 같이 민다.
//! 정본 `reload_config` 가 갱신하는 칸 전수와의 대조는 `tests/config_grammar_conformance.rs`
//! (`source_file_rereads_every_field_canon_reloads` · `reload_pushes_the_state_outside_the_config_like_startup`).

use crate::SessionState;

pub fn apply_config_globals(config: &base::Config, state: &mut SessionState) {
    state.set_inactive_dim(config.inactive_dim, config.inactive_dim_ratio);
}
