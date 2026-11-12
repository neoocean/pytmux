//! **설정을 이번 판에 먹이는 한 함수** — 기동과 `source-file` 이 같이 지난다(pytmux-574).
//!
//! 대부분의 칸은 뷰가 `self.config` 에서 **쓸 때마다** 읽으므로 설정을 갈아 끼우는 것만으로
//! 따라온다. 아래 둘은 다르다 — **설정 밖의 상태**(합성 상태의 딤 · 프로세스 전역 폭 판정)에
//! 한 번 밀어 넣어야 먹는다. 기동과 다시 읽기가 이 일을 따로 하면 갈린다: 종전 `source-file`
//! 은 딤만 다시 밀고 폭 판정은 안 밀어서 `set ambiguous-width wide` 가 다시 읽기로는 안 먹었다.
//!
//! prefix 는 키 모드(`base::keys::ModeState`)가 들고 있어 호출부가 같이 민다.
//! 정본 `reload_config` 가 갱신하는 칸 전수와의 대조는 `tests/config_grammar_conformance.rs`
//! (`source_file_rereads_every_field_canon_reloads` · `reload_pushes_the_state_outside_the_config_like_startup`).

use crate::SessionState;

pub fn apply_config_globals(config: &base::Config, state: &mut SessionState) {
    state.set_inactive_dim(config.inactive_dim, config.inactive_dim_ratio);
    // 폭 판정은 프로세스 전역이다(compose::set_ambiguous_wide 문서 참조).
    crate::compose::set_ambiguous_wide(config.ambiguous_width == "wide");
}
