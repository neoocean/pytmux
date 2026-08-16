//! `splitter.rs` 의 순수 산수 — 경계 칸 하나가 **어디에** 선을 긋나.
//!
//! ⚠ 그림 자체(스크린샷)는 여기서 못 잰다. 헤드리스 하네스의 시험 글꼴은 칸 폭이 0이라
//! 오버레이의 `paint` 가 `cw <= 0.5` 가드에서 돌아간다(`session_view_tests.rs` 의 그
//! 머리말과 같은 자리). 그래서 픽셀로 옮기는 **산수**를 함수로 떼어 여기서 못박고,
//! 라이브 그림은 `client/CLAUDE.md` 가 적은 대로 스크린샷이 잡는다.

use super::*;

/// 칸 하나: 왼쪽 위 (0, 0) · 폭 10 · 높이 20.
const CW: f32 = 10.;
const CH: f32 = 20.;

/// 그 선분이 덮는 세로 구간(위, 아래).
fn span_y(r: &RectF) -> (f32, f32) {
    (r.origin_y(), r.origin_y() + r.height())
}

/// 가로로 긴 것이 가로선, 세로로 긴 것이 세로선이다.
fn horizontal(rects: &[RectF]) -> Vec<&RectF> {
    rects.iter().filter(|r| r.width() > r.height()).collect()
}

fn vertical(rects: &[RectF]) -> Vec<&RectF> {
    rects.iter().filter(|r| r.height() > r.width()).collect()
}

#[test]
fn without_slack_the_line_sits_in_the_middle_of_its_cell() {
    // 오라클이 먼저다 — 안 내렸을 때의 자리를 못박아야 "내려갔다"가 뜻을 가진다.
    // `┴`(위·왼·오른)의 가로선은 칸의 세로 가운데다.
    let bits = Seg::UP | Seg::LEFT | Seg::RIGHT;
    let rects = seg_rects(bits, 0., 100., CW, CH, 0.);
    let h = horizontal(&rects);
    assert_eq!(h.len(), 2, "가로 성분이 둘(왼·오른)이라야 한다: {rects:?}");
    for r in h {
        let (top, bottom) = span_y(r);
        assert!(
            (top + bottom) / 2. - (100. + CH / 2.) < 0.01,
            "가로선이 칸 가운데(110)가 아니다: {top}~{bottom}"
        );
    }
}

#[test]
fn the_bottom_edge_drops_by_the_slack() {
    // ★ `pytmux-162` 의 본체. 캔버스가 못 채운 빈 높이만큼 아랫변이 내려가야 한다.
    const DROP: f32 = 37.;
    let bits = Seg::UP | Seg::LEFT | Seg::RIGHT;
    let flat = seg_rects(bits, 0., 100., CW, CH, 0.);
    let dropped = seg_rects(bits, 0., 100., CW, CH, DROP);
    let y_of = |rects: &[RectF]| {
        let h = horizontal(rects);
        span_y(h[0]).0
    };
    let moved = y_of(&dropped) - y_of(&flat);
    assert!(
        (moved - DROP).abs() < 0.01,
        "아랫변이 빈 높이만큼 안 내려갔다: {moved} ≠ {DROP}"
    );
}

#[test]
fn the_side_wall_grows_to_meet_the_dropped_edge() {
    // ⛔ 가로선만 내리면 상자가 끊긴다 — 세로 성분이 **같이** 길어져야 한다.
    const DROP: f32 = 37.;
    let bits = Seg::UP | Seg::LEFT | Seg::RIGHT; // `┴`
    let flat = seg_rects(bits, 0., 100., CW, CH, 0.);
    let dropped = seg_rects(bits, 0., 100., CW, CH, DROP);
    let (flat_v, dropped_v) = (vertical(&flat), vertical(&dropped));
    assert_eq!(flat_v.len(), 1, "세로 성분이 하나여야 한다: {flat:?}");
    let grew = dropped_v[0].height() - flat_v[0].height();
    assert!(
        (grew - DROP).abs() < 0.01,
        "세로변이 안 길어졌다 — 아랫변이 옆벽에서 떨어진다: {grew} ≠ {DROP}"
    );
    // 위쪽 끝은 그대로다(칸 꼭대기) — 아래로만 자란다.
    assert!(
        (span_y(dropped_v[0]).0 - span_y(flat_v[0]).0).abs() < 0.01,
        "세로변이 위로도 움직였다: {:?} vs {:?}",
        span_y(dropped_v[0]),
        span_y(flat_v[0])
    );
    // 그 끝이 내려간 가로선에 닿는다.
    let edge = span_y(horizontal(&dropped)[0]).0;
    assert!(
        span_y(dropped_v[0]).1 >= edge,
        "세로변이 아랫변까지 못 닿는다: {:?} < {edge}",
        span_y(dropped_v[0]).1
    );
}

#[test]
fn only_the_last_row_drops() {
    // 기준은 **격자의 행 수**다 — "가장 아래에 있는 선분"으로 고르면 패널 안의 선까지
    // 끌어내린다(배치를 아직 못 받은 프레임에서 실제로 그렇게 된다).
    let mut overlay = probe_overlay(24);
    overlay.slack = 37.;
    assert_eq!(overlay.seg_drop(23), 37., "맨 아랫줄이 안 내려간다");
    assert_eq!(overlay.seg_drop(22), 0., "맨 아랫줄이 아닌데 내려갔다");
    assert_eq!(overlay.seg_drop(0), 0., "윗변이 내려갔다");
}

#[test]
fn a_grid_of_no_rows_never_drops() {
    // 행 수를 모르는 프레임(캔버스가 없다)에서는 내릴 자리가 없다.
    let mut overlay = probe_overlay(0);
    overlay.slack = 37.;
    assert_eq!(overlay.seg_drop(0), 0.);
}

/// 산수만 재는 자리라 자식은 아무것도 안 그리는 빈 위젯이면 된다.
fn probe_overlay(rows: u16) -> SplitterOverlay {
    SplitterOverlay::new(
        warpui::elements::Empty::new().finish(),
        Vec::new(),
        Vec::new(),
        Vec::new(),
        None,
        None,
        Vec::new(),
        Vec::new(),
        Vec::new(),
        "test-probe",
        rows,
    )
}
