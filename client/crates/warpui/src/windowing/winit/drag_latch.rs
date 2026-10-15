//! winit 의 OS 끌기 걸쇠(`WindowState::dragging`)가 **영영 걸리는 것**을 막는 판정
//! (pytmux/pytmux-155 · pytmux-365 · 검수 pytmux-34 #1).
//!
//! # 무엇이 걸리나
//!
//! 우리가 쓰는 winit 포크(`warpdotdev/winit@a4e0ecb` · cargo git 의존이라 이 저장소에
//! 사본이 없다)는 `drag_window()`·`drag_resize_window()` 를 둘 다 한 함수
//! (`handle_os_dragging`)로 보낸다:
//!
//! ```text
//! if !state.dragging { state.dragging = true } else { return }   // ← 이미 참이면 말없이 끝
//! ReleaseCapture(); PostMessageW(WM_NCLBUTTONDOWN, HTCAPTION|HTxxx)
//! ```
//!
//! 그 걸쇠를 푸는 곳은 **`WM_EXITSIZEMOVE` 하나뿐**이다. 그런데 붙여 보낸
//! `WM_NCLBUTTONDOWN` 을 `DefWindowProc` 가 이동·크기 조절 모달 루프로 **안 받아 주면**
//! (`WM_ENTERSIZEMOVE` 가 안 온다) `WM_EXITSIZEMOVE` 도 안 오고, 걸쇠는 프로세스가 죽을
//! 때까지 참으로 남는다. 그 뒤로는 머리줄 끌기도 가장자리 리사이즈도 `Ok(())` 를 돌려주며
//! **아무 일도 안 한다** — 155·365 의 「둘 다 안 된다 · 다시 띄우면 된다」와 같은 모양이다.
//!
//! 루프가 거절되는 경로로 알려진 것: 크기 조절 테두리(`WS_THICKFRAME`)가 없는 창에
//! `HTTOP…` 를 보낼 때(보더리스 전체 화면), 누름이 붙여 보낸 메시지가 처리되기 **전에**
//! 이미 떼어졌을 때(빠른 클릭 · UI 스레드가 바쁜 콜드 스타트).
//!
//! # 어떻게 푸나 — 포크를 고치지 않고
//!
//! 창을 서브클래싱해 붙여 보낸 `WM_NCLBUTTONDOWN` 의 처리를 **감싼다**. `DefWindowProc`
//! 은 그 메시지 안에서 모달 루프를 **동기로** 돈다 — 그래서 그 호출이 돌아왔을 때
//! `WM_ENTERSIZEMOVE` 를 한 번도 못 봤다면 루프가 거절된 것이다. 그때 winit 에게
//! `WM_EXITSIZEMOVE` 를 직접 건네면 winit 이 스스로 걸쇠를 내린다(그 처리기가 하는 일이
//! 정확히 그것이다). 걸쇠가 안 걸려 있었으면 그 메시지는 아무 일도 안 한다.
//!
//! 여기 있는 것은 그 판정의 **순수한 절반**이다 — Win32 를 안 타서 어느 상자에서나 잰다.
//! 실제 서브클래스는 `windows/window_ext.rs` 에 있다.

/// `WM_NCLBUTTONDOWN` 의 히트 코드 중 **OS 가 이동·크기 조절 루프를 여는 것**.
///
/// `HTCAPTION`(2) = 이동, `HTLEFT`(10)…`HTBOTTOMRIGHT`(17) = 가장자리 여덟. winit 이 붙여
/// 보내는 값이 정확히 이 아홉이다(`handle_os_dragging` 의 호출부 둘).
pub fn is_os_drag_hit(hit: usize) -> bool {
    const HTCAPTION: usize = 2;
    const HTLEFT: usize = 10;
    const HTBOTTOMRIGHT: usize = 17;
    hit == HTCAPTION || (HTLEFT..=HTBOTTOMRIGHT).contains(&hit)
}

/// 붙여 보낸 누름 하나를 감싸는 동안의 관측.
///
/// `begin` → (그 안에서 `entered` 가 0번 이상) → `finish`. 감싸는 호출이 겹칠 수 있으므로
/// (모달 루프 안에서 또 누름이 온다) `begin` 이 앞의 관측을 돌려주고 `finish` 가 되돌린다.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct OsDragWatch {
    watching: bool,
    entered: bool,
}

/// 감싼 호출이 끝났을 때의 판정.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OsDragVerdict {
    /// 감시 대상이 아니었다(히트 코드가 이동·크기 조절이 아니다).
    NotWatched,
    /// OS 가 모달 루프를 열었다 — `WM_EXITSIZEMOVE` 가 이미 걸쇠를 내렸다.
    LoopRan,
    /// OS 가 루프를 **안 열었다** — 걸쇠가 걸렸을 수 있으니 내려야 한다.
    LoopRefused,
}

impl OsDragWatch {
    /// 누름 하나를 감싸기 시작한다. 돌려준 값은 [`Self::finish`] 에 그대로 넘긴다.
    pub fn begin(&mut self, hit: usize) -> OsDragWatch {
        let saved = *self;
        if is_os_drag_hit(hit) {
            *self = OsDragWatch {
                watching: true,
                entered: false,
            };
        } else {
            self.watching = false;
        }
        saved
    }

    /// `WM_ENTERSIZEMOVE` 를 봤다.
    pub fn entered(&mut self) {
        self.entered = true;
    }

    /// 감싼 호출이 돌아왔다. 판정을 내고 앞의 관측을 되돌린다.
    pub fn finish(&mut self, saved: OsDragWatch) -> OsDragVerdict {
        let verdict = match (self.watching, self.entered) {
            (false, _) => OsDragVerdict::NotWatched,
            (true, true) => OsDragVerdict::LoopRan,
            (true, false) => OsDragVerdict::LoopRefused,
        };
        // 겹친 호출 안에서 루프가 돌았으면 바깥 호출도 «봤다» 로 친다 — 걸쇠는 하나다.
        let inner_entered = self.entered;
        *self = saved;
        if inner_entered && self.watching {
            self.entered = true;
        }
        verdict
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_nine_hits_winit_posts_are_watched_and_nothing_else() {
        // winit 이 붙여 보내는 값: HTCAPTION + 가장자리 여덟. 그 밖(HTCLIENT=1 · HTSYSMENU=3 ·
        // HTCLOSE=20)까지 감시하면 루프와 무관한 누름에 걸쇠 내림을 쏜다.
        for hit in [2usize, 10, 11, 12, 13, 14, 15, 16, 17] {
            assert!(is_os_drag_hit(hit), "{hit} 를 안 본다");
        }
        for hit in [0usize, 1, 3, 9, 18, 20] {
            assert!(!is_os_drag_hit(hit), "{hit} 까지 본다");
        }
    }

    #[test]
    fn a_refused_loop_is_reported_so_the_latch_gets_released() {
        // ★ 155/365 의 핵심 — 루프가 안 열린 누름을 «안 열렸다» 로 내야 걸쇠를 내린다.
        //   이 판정이 `LoopRan` 이나 `NotWatched` 로 접히면 걸쇠는 영영 걸린 채다.
        let mut w = OsDragWatch::default();
        let saved = w.begin(12); // HTTOP — 보더리스 전체 화면에서 OS 가 거절하는 그것
        assert_eq!(w.finish(saved), OsDragVerdict::LoopRefused);
    }

    #[test]
    fn a_loop_that_ran_needs_nothing_from_us() {
        // 루프가 돌았으면 winit 의 `WM_EXITSIZEMOVE` 가 이미 내렸다 — 또 쏠 이유가 없다.
        let mut w = OsDragWatch::default();
        let saved = w.begin(2);
        w.entered();
        assert_eq!(w.finish(saved), OsDragVerdict::LoopRan);
    }

    #[test]
    fn a_non_drag_hit_is_left_alone() {
        let mut w = OsDragWatch::default();
        let saved = w.begin(1);
        assert_eq!(w.finish(saved), OsDragVerdict::NotWatched);
    }

    #[test]
    fn nested_presses_do_not_lose_the_outer_observation() {
        // 루프 안에서 또 누름이 와도(겹친 호출) 바깥 판정이 흔들리면 안 된다.
        let mut w = OsDragWatch::default();
        let outer = w.begin(2);
        w.entered();
        let inner = w.begin(2);
        assert_eq!(w.finish(inner), OsDragVerdict::LoopRefused);
        assert_eq!(w.finish(outer), OsDragVerdict::LoopRan);
        assert_eq!(w, OsDragWatch::default(), "바깥이 끝났는데 관측이 남았다");
    }

    #[test]
    fn a_loop_seen_inside_a_nested_press_counts_for_the_outer_one() {
        let mut w = OsDragWatch::default();
        let outer = w.begin(2);
        let inner = w.begin(15);
        w.entered();
        assert_eq!(w.finish(inner), OsDragVerdict::LoopRan);
        assert_eq!(w.finish(outer), OsDragVerdict::LoopRan);
    }
}
