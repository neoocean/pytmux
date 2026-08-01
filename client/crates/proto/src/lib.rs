//! pytmux 서버와 말하는 계층.
//!
//! 서버(`pytmuxlib/serverio.py`)는 클라에게 **이미 렌더된 행**을 30Hz 로 밀어 준다.
//! 그래서 이 크레이트에는 VT 파서가 없다 — 필요한 것은 프레이밍, 메시지 해석, 그리고
//! 받은 행을 셀 격자에 앉히는 합성뿐이다.
//!
//! # 적합성
//!
//! 합성 결과는 파이썬 클라이언트와 **글자 하나까지 같아야** 한다. 그것을 새 오라클
//! 없이 확인할 수 있는 이유는 pytmux 에 이미 그 합성 결과를 SHA-256 으로 동결한
//! 골든이 있기 때문이다(`tests/fixtures/replay_golden.json`, p4 66957). 자세한 경위는
//! `tests/conformance.rs` 와 `scripts/gen_wire_fixture.py` 참조.
//!
//! # UI 무의존
//!
//! `base` 와 마찬가지로 이 크레이트도 UI 를 모른다. GUI·TUI 어느 쪽에서도
//! 같은 코드로 서버와 말한다.

// 연결 계층은 이제 OS 중립이다(전송만 갈린다 — `transport` 참조). 예전에는 여기가
// `#[cfg(unix)]` 였고, 그게 Windows 지원의 첫 관문이었다.
pub mod client;
pub mod blocks;

pub mod canvas;
pub mod clock;
pub mod command;
pub mod compose;
pub mod arghist;
pub mod endpoint;
pub mod footer;
pub mod framing;
pub mod link;
pub mod message;
pub mod mouse;
pub mod selection;
pub mod prompt_box;
pub mod rtt;
pub mod session;
pub mod info;
pub mod status;
pub mod style;
pub mod tabs;
pub mod unwrap;
pub mod transport;

pub use client::{AttachError, Connection, Frame};
// 하단 배지 목록은 이 크레이트가 만든다(`SessionState::badges`) — 뜻은 core 가 든다.
pub use base::Badge;
pub use blocks::{Block, BlockState};
pub use canvas::{Canvas, Cell};
pub use command::{Command, Input};
pub use compose::{compose_rows, display_width};
pub use framing::{FrameError, read_frame, write_frame};
pub use link::{LinkEvent, ServerLink};
pub use selection::{Point, Selection};
pub use session::SessionState;
pub use status::StatusCtx;
pub use style::{CellStyle, Color, NamedColor};
pub use unwrap::unwrap_copy_text;
pub use tabs::{Tab, TabBar, TabLabel};
pub use message::{Hello, Layout, PaneLayout, Row, Run, Screen, ServerMessage, Status};

/// 붙여넣을 글에서 **패널 테두리 글자**를 뺀다(설정 `strip-box-drawing`).
///
/// OS 네이티브 선택(터미널 자체의 드래그)으로 긁으면 우리가 그린 테두리 세로줄이 같이
/// 딸려 온다. 그대로 셸에 붙이면 명령줄이 망가진다 — 파이썬 클라도 같은 이유로 이걸
/// 기본으로 켠다.
///
/// 지우는 것은 **박스드로잉 블록**(U+2500~U+257F)뿐이다. 그 밖의 글자는 사용자가 진짜로
/// 붙이려던 것일 수 있어 손대지 않는다.
pub fn strip_box_drawing(text: &str) -> String {
    text.chars().filter(|c| !('\u{2500}'..='\u{257f}').contains(c)).collect()
}
