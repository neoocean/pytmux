//! 선택을 끌다 패널 위/아래 **가장자리**에 닿으면 저절로 구르는 규칙(pytmux-579).
//!
//! 정본 `clientwidgets.py` 의 `MultiplexerView._edge_scroll_delta` · `_AUTOSCROLL_SEC` ·
//! `_AUTOSCROLL_MAX` 를 그대로 옮긴 **순수 함수**다. 값은 정본에서 뽑은 픽스처
//! (`proto/tests/fixtures/edge_scroll.json` · `client/scripts/gen_edge_scroll_fixture.py`)와
//! `proto/tests/edge_scroll_conformance.rs` 가 맞댄다.
//!
//! # 왜 타이머인가 (정본 주석 그대로)
//!
//! 마우스 이동 이벤트는 포인터가 **움직일 때만** 온다. 가장자리 밖에 멈춰 있으면 이동이
//! 끊겨 스크롤도 멈추므로, 뷰는 이 델타를 들고 [`TICK`] 마다 한 번씩 굴린다.
//!
//! # 왜 경계 행 **자체**를 포함하나
//!
//! 포인터가 창 밖으로 나가면 이동 보고가 멎거나 좌표가 끝 행으로 접히는 자리가 있다.
//! «경계를 넘어야 구른다» 로 만들면 사람이 실제로 닿을 수 없는 조건이 된다 — tmux
//! copy-mode·에디터도 **끝 줄에 닿으면** 구른다.

use std::time::Duration;

/// 한 번 구르는 간격(정본 `_AUTOSCROLL_SEC = 0.06` — 약 16행/초, 읽으며 끌 수 있는 속도).
pub const TICK: Duration = Duration::from_millis(60);

/// 한 번에 구르는 최대 행 수(정본 `_AUTOSCROLL_MAX = 3`) — 경계에서 멀수록 빠르다.
pub const MAX: i32 = 3;

/// 포인터 행 `y` 로 자동 스크롤 델타를 낸다(안쪽 = 0). **+ = 위/과거, − = 아래/최신**
/// (서버 `scroll` 의 `delta` 와 같은 부호).
///
/// `pane_y`·`pane_h` 는 선택을 시작한 패널의 위 행·높이(캔버스 칸). `y` 는 캔버스 위로
/// 나가면 음수일 수 있다. 선택 패널을 모르면(정본 `_sel_rect` 가 None) 부르는 쪽이 `0` 으로 본다.
///
/// 속도: 경계 행 = 1행, 밖으로 두 칸 나갈 때마다 1행씩 더해 최대 [`MAX`] 행.
pub fn delta(pane_y: i32, pane_h: i32, y: i32) -> i32 {
    let bottom = pane_y + pane_h - 1;
    let dist = if y <= pane_y {
        pane_y - y + 1 // 첫 행 **이상**(위 경계 포함) — 경계 행 = 1
    } else if y >= bottom {
        -(y - bottom + 1) // 마지막 행 **이하**(아래 경계 포함)
    } else {
        return 0;
    };
    let mag = MAX.min(1 + (dist.abs() - 1) / 2);
    if dist > 0 { mag } else { -mag }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_inside_is_still_and_the_edges_roll_one_row() {
        // 패널 y=2..=9(높이 8).
        assert_eq!(delta(2, 8, 5), 0, "안쪽인데 굴렀다");
        assert_eq!(delta(2, 8, 2), 1, "위 경계 행 = 1행 위로");
        assert_eq!(delta(2, 8, 9), -1, "아래 경계 행 = 1행 아래로");
    }

    #[test]
    fn farther_out_rolls_faster_up_to_the_cap() {
        assert_eq!(delta(2, 8, 1), 1);
        assert_eq!(delta(2, 8, 0), 2);
        assert_eq!(delta(2, 8, -10), MAX);
        assert_eq!(delta(2, 8, 11), -2);
        assert_eq!(delta(2, 8, 40), -MAX);
    }

    #[test]
    fn a_one_row_pane_rolls_up_first() {
        // 정본 갈래 순서(`y <= py` 가 먼저) — 한 줄짜리 패널의 그 한 줄은 위로 구른다.
        assert_eq!(delta(4, 1, 4), 1);
    }
}
