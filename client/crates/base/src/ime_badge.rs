//! 한/영 배지가 **어느 칸에 앉나** — UI 무의존 순수 판정(pytmux-392).
//!
//! # 왜 뷰가 아니라 여기인가
//!
//! GUI 는 이 배지를 격자 글자가 아니라 **캔버스 위에 뜬 그림**으로 그린다(pytmux-185 가
//! 허용하는 갈림 ⓑ). 그림이라 «글자를 가리면 비켜설» 수 있는데, 그 «비켜서기»는 그리기가
//! 아니라 **판정**이다 — 어느 칸이 비었나를 보고 자리를 고르는 일이다. 뷰에 두면 픽셀
//! 하네스 없이는 못 재고(이 저장소의 GUI 는 화소 오라클이 없다), 여기 두면 격자만으로
//! 전부 잰다.
//!
//! # 기준 자리는 정본이 정한다
//!
//! 줄(커서가 있는 줄)과 오른쪽 끝 정렬·`[x]` 만큼 비켜서기는 정본
//! `plugins/ime-indicator/cells.py`(`badge_row`·`badge_span`)의 규칙이고 그대로 쓴다.
//! 이 모듈이 더하는 것은 **그 자리가 글자에 걸릴 때 어떻게 물러나나** 하나다.
//!
//! ⛔ **못 찾으면 안 그리는 쪽으로 접지 않는다.** 배지가 사라지면 사용자는 「지금 한글인가」를
//! 알 길이 없다 — 다 막혔으면 기준 자리에 그대로 그린다(겹치는 것이 없는 것보다 낫다).

/// 배지가 앉을 칸.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BadgeSpot {
    /// 왼쪽 끝 칸(창 절대 좌표).
    pub col: usize,
    /// 줄(창 절대 좌표).
    pub row: usize,
    /// 기준 자리에서 물러났나 — 「비켜섰다」를 화면 밖에서 알 수 있게 남긴다.
    pub dodged: bool,
    /// 물러날 자리를 못 찾아 **겹친 채로** 앉았나.
    pub overlaps: bool,
}

/// 배지가 앉을 자리를 고른다.
///
/// - `want_row` — 정본 규칙이 고른 줄(커서 줄).
/// - `left_edge`/`right_edge` — 활성 패널의 칸 범위(오른쪽은 exclusive).
/// - `row_span` — 활성 패널의 `(첫 줄, 마지막 줄 exclusive)`.
/// - `width` — 배지가 차지할 칸 수.
/// - `reserve_right` — 오른쪽에 비워 둘 칸 수(정본은 탭 닫기 `[x]` 자리를 비운다).
/// - `blank(col, row)` — 그 칸이 **비어 있나**(공백이거나 글자가 없다).
///
/// 차례: 기준 자리 → 그 줄에서 왼쪽으로 한 칸씩 → 위 줄 → 아래 줄 → (다 막히면) 기준 자리.
pub fn badge_spot(
    want_row: usize,
    left_edge: usize,
    right_edge: usize,
    row_span: (usize, usize),
    width: usize,
    reserve_right: usize,
    blank: impl Fn(usize, usize) -> bool,
) -> Option<BadgeSpot> {
    if width == 0 || right_edge <= left_edge {
        return None;
    }
    let x_end = right_edge.checked_sub(reserve_right)?;
    let anchor = x_end.checked_sub(width)?;
    if anchor < left_edge {
        // 폭이 모자란다 — 정본도 이때는 안 그린다(`badge_span` 이 None).
        return None;
    }
    let free = |col: usize, row: usize| (col..col + width).all(|c| blank(c, row));

    // ★ 줄 차례에 뜻이 있다: **커서 줄이 먼저**다(눈이 거기 있다). 위 줄이 아래 줄보다
    //   먼저인 이유는 프롬프트가 아래에서 자라기 때문이다 — 아래 줄은 다음 출력이
    //   덮을 자리라, 거기 앉으면 곧 다시 비켜서야 한다.
    let mut rows = vec![want_row];
    if want_row > row_span.0 {
        rows.push(want_row - 1);
    }
    if want_row + 1 < row_span.1 {
        rows.push(want_row + 1);
    }
    for (i, &row) in rows.iter().enumerate() {
        let mut col = anchor;
        loop {
            if free(col, row) {
                return Some(BadgeSpot {
                    col,
                    row,
                    dodged: i > 0 || col != anchor,
                    overlaps: false,
                });
            }
            if col == left_edge {
                break;
            }
            col -= 1;
        }
    }
    Some(BadgeSpot {
        col: anchor,
        row: want_row,
        dodged: false,
        overlaps: true,
    })
}

#[cfg(test)]
#[path = "ime_badge_tests.rs"]
mod tests;
