//! RTT **실값 차트**의 한 줄(pytmux-519 · 허용 갈림 ⓑ).
//!
//! # 왜 (제보 2026-09-26)
//!
//! *"현재 상태는 TUI 에서 가져온 그래프 그대로입니다 … 실제 값을 표현하도록 정교하게"*.
//! N13(pytmux-462)이 글자 `▁▂▃…` 를 막대로 바꿨지만 값은 여전히 글자 그래프의 **1/8
//! 양자화**(`proto::rtt::graph_cells` 의 `eighths`)였다 — 48칸 × 5줄 격자의 칸을 색칠한
//! 것이라 계단 블록으로 보였다. 여기서는 값을 **픽셀**로 그린다(`proto::rtt::bar_px`) —
//! 막대 높이가 값에 비례하고, 임계는 점선이고, 측정 없음은 바닥의 점이다.
//!
//! # 왜 «한 줄»인가
//!
//! 상태 판은 줄의 판이다 — 커서·예산·높이 오라클(pytmux-373)이 전부 「한 줄 = 한 상자」로
//! 산다. 차트를 통째로 한 요소로 두면 그 모델이 깨진다(판 높이가 탭마다 달라진다). 그래서
//! 그래프 `GRAPH_H` 줄이 **각자 제 밴드**를 그리되, 막대가 줄 사이 틈에서 끊겨 보이지 않게
//! 아래 틈만큼 **더 내려 그린다**(`gap_below`). 밴드마다 자기 아래가 어디인지(`band_bottom`)
//! 알므로 전체 높이의 막대 하나를 조각내 그리는 것과 같다.
//!
//! 글자 그래프 경로(`graph_lines` · 정본 픽스처 적합성)는 그대로다 — TUI 가 쓴다.

use warpui::color::ColorU;
use warpui::elements::Fill;
use warpui::geometry::rect::RectF;
use warpui::geometry::vector::{Vector2F, vec2f};
use warpui::{
    AfterLayoutContext, AppContext, Element, EventContext, LayoutContext, PaintContext,
    SizeConstraint,
};
use warpui_core::elements::Point;
use warpui_core::event::DispatchedEvent;

/// 그래프 한 줄(밴드)의 그림 — 막대 조각 · 축선 · 임계 점선 · 측정 없음 표식.
pub struct RttSlice {
    /// 칸마다 막대의 **전체** 픽셀 높이(차트 바닥에서) — [`proto::rtt::bar_px`].
    bars: Vec<Option<f32>>,
    /// 임계선의 전체 픽셀 높이(바닥에서) — 스케일 밖이면 없음.
    threshold: Option<f32>,
    /// 이 밴드의 바닥이 차트 바닥에서 몇 px 위인가(맨 아래 줄이 0).
    band_bottom: f32,
    /// 밴드 높이(px) — 한 줄의 칸 높이.
    band_h: f32,
    /// 아래 밴드와의 틈(px). 막대가 그 틈에서 끊겨 보이지 않게 그만큼 더 내려 그린다.
    /// 맨 아래 줄은 0.
    gap_below: f32,
    /// 한 칸의 폭(px).
    cell_w: f32,
    ok: ColorU,
    warn: ColorU,
    grid: ColorU,
    dim: ColorU,
    size: Option<Vector2F>,
    origin: Option<Point>,
}

/// 임계 점선의 한 토막과 그 사이(px).
const DASH: f32 = 3.;
const DASH_GAP: f32 = 3.;

impl RttSlice {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        bars: Vec<Option<f32>>,
        threshold: Option<f32>,
        band_bottom: f32,
        band_h: f32,
        gap_below: f32,
        cell_w: f32,
        colors: (ColorU, ColorU, ColorU, ColorU),
    ) -> Self {
        let (ok, warn, grid, dim) = colors;
        Self {
            bars,
            threshold,
            band_bottom,
            band_h,
            gap_below,
            cell_w,
            ok,
            warn,
            grid,
            dim,
            size: None,
            origin: None,
        }
    }

    pub fn finish(self) -> Box<dyn Element> {
        Box::new(self)
    }

    fn width(&self) -> f32 {
        self.bars.len() as f32 * self.cell_w
    }

    /// 이 밴드 안에서 막대 하나의 조각 — `(밴드 위에서의 y 오프셋, 높이)`. 막대가 이
    /// 밴드에 안 닿으면 `None`. 높이에는 아래 틈(`gap_below`)이 **포함**된다(막대가 아래
    /// 밴드로 이어지는 중이라 그 틈도 막대다).
    ///
    /// 순수 산수라 시험이 그대로 잰다 — 그림을 되읽을 수 없으니 이 함수가 그림의 답지다.
    pub fn segment(&self, bar_px: f32) -> Option<(f32, f32)> {
        let in_band = (bar_px - self.band_bottom).clamp(0., self.band_h);
        if in_band <= 0. {
            return None;
        }
        Some((self.band_h - in_band, in_band + self.gap_below))
    }

    /// 임계선이 이 밴드(또는 바로 아래 틈)에 있으면 밴드 위에서의 y 오프셋.
    pub fn threshold_offset(&self) -> Option<f32> {
        let t = self.threshold?;
        let low = self.band_bottom - self.gap_below;
        let high = self.band_bottom + self.band_h;
        if t < low || t >= high {
            return None;
        }
        Some(self.band_h - (t - self.band_bottom))
    }

    fn rect(&self, ctx: &mut PaintContext, x: f32, y: f32, w: f32, h: f32, color: ColorU) {
        if w <= 0. || h <= 0. {
            return;
        }
        ctx.scene
            .draw_rect_without_hit_recording(RectF::new(vec2f(x, y), vec2f(w, h)))
            .with_background(Fill::Solid(color));
    }
}

impl Element for RttSlice {
    fn layout(
        &mut self,
        _constraint: SizeConstraint,
        _ctx: &mut LayoutContext,
        _app: &AppContext,
    ) -> Vector2F {
        let size = vec2f(self.width(), self.band_h);
        self.size = Some(size);
        size
    }

    fn after_layout(&mut self, _ctx: &mut AfterLayoutContext, _app: &AppContext) {}

    fn paint(&mut self, origin: Vector2F, ctx: &mut PaintContext, _app: &AppContext) {
        self.origin = Some(Point::from_vec2f(origin, ctx.scene.z_index()));
        let (ox, oy) = (origin.x(), origin.y());
        // 축선 — 글자 그래프의 `┤` 자리. 틈까지 내려 그려 한 줄기로 보이게 한다.
        self.rect(ctx, ox, oy, 1., self.band_h + self.gap_below, self.grid);
        // 막대 조각들 — 값에 비례한 높이(격자가 아니라 픽셀). 칸 사이 1px 는 비워 막대로
        // 읽히게 한다(붙이면 면 하나로 뭉친다).
        let bar_w = (self.cell_w - 1.).max(1.);
        for (i, bar) in self.bars.iter().enumerate() {
            let x = ox + i as f32 * self.cell_w + 1.;
            match bar {
                Some(h) => {
                    if let Some((dy, seg_h)) = self.segment(*h) {
                        let over = self.threshold.is_some_and(|t| *h > t);
                        let color = if over { self.warn } else { self.ok };
                        self.rect(ctx, x, oy + dy, bar_w, seg_h, color);
                    }
                }
                None => {
                    // 측정 없음 — 바닥의 점. 「0 에 가까움」(1px 막대)과 다른 그림이다.
                    if self.band_bottom <= 0. {
                        self.rect(ctx, x + bar_w / 2. - 1., oy + self.band_h - 2., 2., 2., self.dim);
                    }
                }
            }
        }
        // 임계 점선 — 글자 그래프의 `┄`. 막대 **위**에 긋는다(가려지면 임계가 안 보인다).
        if let Some(dy) = self.threshold_offset() {
            let mut x = ox + 1.;
            let end = ox + self.width();
            while x < end {
                self.rect(ctx, x, oy + dy, DASH.min(end - x), 1., self.warn);
                x += DASH + DASH_GAP;
            }
        }
    }

    fn dispatch_event(
        &mut self,
        _event: &DispatchedEvent,
        _ctx: &mut EventContext,
        _app: &AppContext,
    ) -> bool {
        false
    }

    fn size(&self) -> Option<Vector2F> {
        self.size
    }

    fn origin(&self) -> Option<Point> {
        self.origin
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn slice(bars: Vec<Option<f32>>, thr: Option<f32>, band_bottom: f32) -> RttSlice {
        let c = ColorU::black();
        RttSlice::new(bars, thr, band_bottom, 16., if band_bottom > 0. { 4. } else { 0. }, 8., (c, c, c, c))
    }

    #[test]
    fn a_bar_is_cut_into_band_segments_that_meet_across_the_gap() {
        // 전체 36px 막대 · 밴드 16px · 틈 4px → 맨 아래 밴드(바닥 0)는 16, 그 위(바닥 20)는
        // 16 + 틈 4 = 20(아래로 이어진다), 그 위(바닥 40)는 안 닿는다.
        assert_eq!(slice(vec![], None, 0.).segment(36.), Some((0., 16.)));
        assert_eq!(slice(vec![], None, 20.).segment(36.), Some((0., 20.)));
        assert_eq!(slice(vec![], None, 40.).segment(36.), None);
        // 밴드 중간에서 끝나는 막대 — 위에서부터의 오프셋이 그만큼이다.
        assert_eq!(slice(vec![], None, 20.).segment(30.), Some((6., 14.)));
        // 1px 막대(0 에 가까운 측정)도 맨 아래 밴드에 산다.
        assert_eq!(slice(vec![], None, 0.).segment(1.), Some((15., 1.)));
    }

    #[test]
    fn the_threshold_line_lands_in_exactly_one_band() {
        // 임계 30px: 바닥 20 의 밴드 안(20 ≤ 30 < 36) — 위에서 6px.
        assert_eq!(slice(vec![], Some(30.), 20.).threshold_offset(), Some(6.));
        assert_eq!(slice(vec![], Some(30.), 0.).threshold_offset(), None);
        assert_eq!(slice(vec![], Some(30.), 40.).threshold_offset(), None);
        // 틈에 떨어진 임계(18px — 바닥 20 밴드의 아래 틈 16..20)는 그 위 밴드가 긋는다.
        assert_eq!(slice(vec![], Some(18.), 20.).threshold_offset(), Some(18.));
        assert_eq!(slice(vec![], Some(18.), 0.).threshold_offset(), None);
        assert_eq!(slice(vec![], None, 0.).threshold_offset(), None);
    }
}
