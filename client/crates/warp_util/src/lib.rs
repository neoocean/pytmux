//! `warp_util` — 잡동사니 유틸.
//!
//! **AGPL 원본을 대체하는 자체 구현(MIT).** 원본은 4천여 줄이지만 `warpui`/`warpui_core`
//! 가 쓰는 것은 `path::ShellFamily` 하나뿐이다(호출부 전수 확인: `clipboard_utils.rs`
//! 의 경로 이스케이프, `platform/mod.rs` 의 OS별 기본 셸 판정). PROVENANCE.md §2.

pub mod path;
