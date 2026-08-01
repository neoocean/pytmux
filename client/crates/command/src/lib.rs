//! `command` — 프로세스 실행.
//!
//! **AGPL 원본을 대체하는 자체 구현(MIT).** 원본은 명령 실행에 텔레메트리·셸 통합
//! 계층을 얹은 크레이트지만, `warpui` 가 쓰는 것은 `blocking::Command` 하나이고
//! 그것도 **Linux 창 관리자 탐지 한 곳**(`windowing/winit/linux/window_manager.rs`)
//! 뿐이다. 거기서 필요한 것은 표준 라이브러리의 프로세스 실행 그대로라, 감싸지 않고
//! 재노출한다. PROVENANCE.md §2.

/// 블로킹 프로세스 실행. `std::process` 의 것을 그대로 쓴다.
pub mod blocking {
    pub use std::process::{Child, Command, Output, Stdio};
}
