use std::cell::Cell;

use windows::Win32::Foundation::{COLORREF, FALSE, HWND, LPARAM, LRESULT, TRUE, WPARAM};
use windows::Win32::Graphics::Dwm::{DWMWA_CLOAK, DwmSetWindowAttribute};
use windows::Win32::UI::Shell::{DefSubclassProc, SetWindowSubclass};
use windows::Win32::UI::WindowsAndMessaging::{
    GWL_EXSTYLE, GWL_STYLE, GetWindowLongPtrW, LWA_ALPHA, SetLayeredWindowAttributes,
    SetWindowLongPtrW, WM_ENTERSIZEMOVE, WM_EXITSIZEMOVE, WM_NCLBUTTONDOWN, WS_EX_LAYERED,
    WS_THICKFRAME,
};

use crate::windowing::winit::drag_latch::{OsDragVerdict, OsDragWatch};
use windows_core::BOOL;
use winit::raw_window_handle::{HasWindowHandle, RawWindowHandle};
use winit::window::Window;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("Invalid WindowHandle")]
    InvalidWindowHandle,
    #[error("Unknown error")]
    Other(#[from] windows::core::Error),
}

/// Extension trait for Windows specific logic on a [`winit::window::Window`].
pub trait WindowExt {
    /// "Cloaks" the window. A cloaked window is one that is invisible, but can still be drawn to.
    fn set_cloaked(&self, cloaked: bool) -> Result<(), Error>;

    /// Sets the window's uniform opacity (`0.0` fully transparent, `1.0` fully
    /// opaque) using a layered window. Unlike `set_cloaked` or hiding, this keeps
    /// the window in the z-order and does not change focus, which is what the
    /// cross-window tab-drag preview relies on while hovering over a target
    /// window's tab bar.
    fn set_alpha(&self, alpha: f32) -> Result<(), Error>;

    /// 창에 **크기 조절 테두리**(`WS_THICKFRAME` = `WS_SIZEBOX`)가 있나.
    ///
    /// 이것이 없는 창에 `WM_NCLBUTTONDOWN(HTTOP…)` 를 보내면 OS 가 크기 조절 루프를 안
    /// 연다(보더리스 전체 화면이 그렇다 — winit 이 그때 `WS_SIZEBOX` 를 걷어 낸다).
    /// 그래서 가장자리 리사이즈 판정의 **바닥 진실**로 쓴다(pytmux-155/365). 핸들을 못
    /// 얻으면 `true`(= 종전 거동)를 준다 — 판정을 못 해서 리사이즈를 죽이지는 않는다.
    fn has_sizing_frame(&self) -> bool;

    /// winit 의 끌기 걸쇠가 영영 걸리지 않게 창을 서브클래싱한다(한 번만 부른다).
    ///
    /// 왜·어떻게는 [`crate::windowing::winit::drag_latch`] 머리말. 실패해도 창은 그대로
    /// 돈다(종전 거동) — 그래서 `Result` 대신 성공 여부만 준다.
    fn install_os_drag_latch_guard(&self) -> bool;
}

fn hwnd_of(window: &Window) -> Option<HWND> {
    match window.window_handle().map(|h| h.as_raw()) {
        Ok(RawWindowHandle::Win32(handle)) => Some(HWND(handle.hwnd.get() as _)),
        _ => None,
    }
}

/// 서브클래스 id. 같은 창에 두 번 걸면 SetWindowSubclass 가 같은 id 를 갱신만 한다.
const DRAG_LATCH_SUBCLASS_ID: usize = 0x7079_746d; // "pytm"

/// 진단 전용 스위치 — `PYTMUX_GUI_DRAG_LATCH_OBSERVE_ONLY=1` 이면 걸쇠를 **안 내리고**
/// 판정만 찍는다. 고치기 전 거동(걸쇠가 걸린 뒤로 끌기 요청이 OS 에 아예 안 닿는다)을 같은
/// 이진에서 A/B 로 재려고 둔다 — 포인터를 못 쓰는 상자에서 그 차이를 볼 길이 이 로그뿐이다
/// (걸쇠가 풀려 있으면 끌기 요청마다 판정 줄이 하나씩 나오고, 걸려 있으면 안 나온다).
fn observe_only() -> bool {
    static FLAG: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *FLAG.get_or_init(|| {
        std::env::var("PYTMUX_GUI_DRAG_LATCH_OBSERVE_ONLY").as_deref() == Ok("1")
    })
}

thread_local! {
    /// 붙여 보낸 누름 하나를 감싸는 동안의 관측. 창 메시지는 UI 스레드 한 곳에서만 돌므로
    /// 스레드 지역으로 충분하다(창이 여럿이어도 한 번에 한 모달 루프다).
    static OS_DRAG_WATCH: Cell<OsDragWatch> = Cell::new(OsDragWatch::default());
}

unsafe extern "system" fn drag_latch_subclass_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    _data: usize,
) -> LRESULT {
    match msg {
        WM_ENTERSIZEMOVE => {
            OS_DRAG_WATCH.with(|w| {
                let mut v = w.get();
                v.entered();
                w.set(v);
            });
            unsafe { DefSubclassProc(hwnd, msg, wparam, lparam) }
        }
        WM_NCLBUTTONDOWN => {
            // ⛔ 관측을 쥔 채로 아래 호출에 들어가지 않는다 — 그 안에서(모달 루프 안)
            //    `WM_ENTERSIZEMOVE` 가 같은 칸을 고친다.
            let saved = OS_DRAG_WATCH.with(|w| {
                let mut v = w.get();
                let saved = v.begin(wparam.0);
                w.set(v);
                saved
            });
            // winit 의 처리기 → DefWindowProc → (열리면) 모달 루프가 **이 안에서** 돈다.
            let result = unsafe { DefSubclassProc(hwnd, msg, wparam, lparam) };
            let verdict = OS_DRAG_WATCH.with(|w| {
                let mut v = w.get();
                let verdict = v.finish(saved);
                w.set(v);
                verdict
            });
            if verdict == OsDragVerdict::LoopRefused && observe_only() {
                // 진단 전용(A/B) — 걸쇠를 일부러 안 내려 고치기 전의 거동을 재현한다.
                log::info!(
                    "drag-155 OS 가 끌기 루프를 안 열었다(히트 {}) — 관찰 전용이라 걸쇠를 그대로 둔다",
                    wparam.0
                );
            } else if verdict == OsDragVerdict::LoopRefused {
                // ★ OS 가 이동·크기 조절 루프를 안 열었다 — winit 의 `dragging` 걸쇠가 걸린
                //   채로 남는다. winit 의 `WM_EXITSIZEMOVE` 처리기가 그것을 내리는 유일한
                //   자리라, 그 메시지를 직접 건넨다(걸쇠가 없었으면 아무 일도 안 한다).
                log::info!(
                    "drag-155 OS 가 끌기 루프를 안 열었다(히트 {}) — winit 걸쇠를 내린다",
                    wparam.0
                );
                unsafe { DefSubclassProc(hwnd, WM_EXITSIZEMOVE, WPARAM(0), LPARAM(0)) };
            } else if verdict == OsDragVerdict::LoopRan {
                log::debug!("drag-155 OS 끌기 루프가 열렸다가 닫혔다(히트 {})", wparam.0);
            }
            result
        }
        _ => unsafe { DefSubclassProc(hwnd, msg, wparam, lparam) },
    }
}

impl WindowExt for Window {
    fn has_sizing_frame(&self) -> bool {
        let Some(hwnd) = hwnd_of(self) else {
            return true;
        };
        // SAFETY: winit 이 준 살아 있는 최상위 창 핸들이다. 읽기만 한다.
        let style = unsafe { GetWindowLongPtrW(hwnd, GWL_STYLE) };
        style & (WS_THICKFRAME.0 as isize) != 0
    }

    fn install_os_drag_latch_guard(&self) -> bool {
        let Some(hwnd) = hwnd_of(self) else {
            return false;
        };
        // SAFETY: 같은 스레드(창을 만든 UI 스레드)에서 부른다 — SetWindowSubclass 의 요구다.
        //   프로시저는 상태를 스레드 지역에만 두므로 dwRefData 가 필요 없다.
        unsafe {
            SetWindowSubclass(
                hwnd,
                Some(drag_latch_subclass_proc),
                DRAG_LATCH_SUBCLASS_ID,
                0,
            )
            .as_bool()
        }
    }

    fn set_cloaked(&self, cloaked: bool) -> Result<(), Error> {
        let Ok(RawWindowHandle::Win32(handle)) = self
            .window_handle()
            .map(|window_handle| window_handle.as_raw())
        else {
            return Err(Error::InvalidWindowHandle);
        };

        let value = if cloaked { TRUE } else { FALSE };
        unsafe {
            DwmSetWindowAttribute(
                HWND(handle.hwnd.get() as _),
                DWMWA_CLOAK,
                &value as *const BOOL as *const _,
                size_of::<BOOL>() as u32,
            )?
        }

        Ok(())
    }

    fn set_alpha(&self, alpha: f32) -> Result<(), Error> {
        let Ok(RawWindowHandle::Win32(handle)) = self
            .window_handle()
            .map(|window_handle| window_handle.as_raw())
        else {
            return Err(Error::InvalidWindowHandle);
        };

        let hwnd = HWND(handle.hwnd.get() as _);
        let alpha_byte = (alpha.clamp(0.0, 1.0) * 255.0).round() as u8;

        // SAFETY: `hwnd` is a valid top-level window handle obtained from winit.
        // `SetLayeredWindowAttributes` requires the `WS_EX_LAYERED` extended
        // style, so add it if it isn't already present. We intentionally leave
        // the style set afterwards: a fully-opaque (alpha 255) layered window
        // composites identically on DWM, which avoids the repaint quirks of
        // toggling the style off when restoring opacity.
        unsafe {
            let ex_style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
            if ex_style & (WS_EX_LAYERED.0 as isize) == 0 {
                SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex_style | (WS_EX_LAYERED.0 as isize));
            }
            SetLayeredWindowAttributes(hwnd, COLORREF(0), alpha_byte, LWA_ALPHA)?;
        }

        Ok(())
    }
}
