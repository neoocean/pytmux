pub(crate) mod app;
pub mod delegate;
// 판정은 순수해서 어느 상자에서나 재지만, 쓰는 자리(창 서브클래스)는 Windows 뿐이다.
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub(crate) mod drag_latch;
mod event_loop;
pub(crate) mod fonts;
#[cfg(any(target_os = "linux", target_os = "freebsd"))]
pub mod linux;

mod notifications;
#[cfg(target_family = "wasm")]
pub mod wasm;

mod window;

#[cfg(target_os = "windows")]
pub mod windows;

use app::CustomEvent;
#[cfg(any(target_os = "linux", target_os = "freebsd"))]
pub use app::WindowingSystem;
use event_loop::EventLoop;
use window::Window;
#[cfg(any(target_os = "linux", target_os = "freebsd"))]
pub use window::get_os_window_manager_name;
