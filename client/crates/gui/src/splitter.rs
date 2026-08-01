//! 크롬 오버레이 — 서버가 **문자**(`│`·`─`·`┌`…)로 그린 경계 칸을 GUI 에서는 **실제
//! 선**으로 옮겨 그린다. 잡을 수 있는 분할 경계에는 그 위에 네이티브 스플리터 바를 얹는다.
//!
//! # 왜 선문자를 그대로 안 쓰나 (2026-07-31 사용자 지시)
//!
//! 정본은 TUI 앱이라 테두리를 선문자로 그릴 수밖에 없다. 우리 GUI 는 네이티브 앱이라
//! 그럴 이유가 없다 — 선문자는 글꼴마다 굵기·이음새가 달라 칸 사이에 틈이 보이고,
//! 폭이 애매한 글자(East Asian Ambiguous)라 폰트 폴백에 따라 자리가 흔들린다.
//! **서버와의 호환은 그대로다**: 캔버스도 좌표도 마우스 판정도 안 건드리고, 그 칸을
//! 글자로 그리지 않고 선으로 그릴 뿐이다(TUI 는 종전대로 문자다).
//!
//! # 왜 오버레이인가
//!
//! 경계는 서버가 캔버스에 합성해 보낸다(클라 위젯이 아니다). 서버를 바꾸지 않고
//! GUI 만 네이티브로 만들려면, 경계 **칸**을 바탕색으로 덮고 그 가운데에 얇은 바를
//! 그리는 것이 캔버스 렌더를 그대로 두는 가장 작은 길이다. 어느 칸이 경계인가는
//! proto 가 안다(`SessionState::dividers`) — 여기는 픽셀로 옮겨 그릴 뿐이다.
//!
//! # 셀 기하는 자리표에서 읽는다
//!
//! 칸 폭·높이는 글꼴이 정하므로 **잰 값**을 쓴다 — 마우스 셀 산수와 같은 원천
//! (`CELL_PROBE` 자리표, `PositionCache`)이다. 캔버스 그리기(자식 `paint`)가 자리표를
//! 남긴 **뒤에** 읽으므로 항상 이번 프레임의 값이다. 자리표가 없으면(첫 프레임 등)
//! 바를 안 그린다 — 마우스가 못 푸는 프레임에는 바도 없는 것이 맞다.

use warpui::color::ColorU;
use warpui::elements::{CornerRadius, Fill, Radius};
use warpui::geometry::rect::RectF;
use warpui::geometry::vector::{Vector2F, vec2f};
use warpui::{
    AfterLayoutContext, AppContext, Element, EventContext, LayoutContext, PaintContext,
    SizeConstraint,
};
use warpui_core::elements::Point;
use warpui_core::event::DispatchedEvent;

use crate::theme;

/// 경계 하나 — 캔버스 **셀** 좌표의 사각형과 상태.
pub struct Bar {
    /// 세로 바인가(`orient == "lr"` — 좌우 분할의 경계는 세로선이다).
    pub vertical: bool,
    pub x: u16,
    pub y: u16,
    pub w: u16,
    pub h: u16,
    /// 잡고 있거나(드래그) 마우스가 올라와 있나 — FOCUS 색으로 강조.
    pub active: bool,
}

/// 경계 문자 칸 하나를 **실제 선**으로 옮긴 것.
///
/// # 왜 칸 단위인가
///
/// 패널 하나를 통째로 둥근 사각형 테두리로 그리면 두 가지를 잃는다: ⑴ 위 변 가운데의
/// **제목 자리**(정본은 그 칸에 글자를 넣어 선을 끊는다) ⑵ 이웃 패널과 **맞닿은 변**
/// (`├`·`┬`·`┼` 로 합쳐지는 자리 — 통짜 사각형 둘은 그 자리에 선을 두 번 그린다).
///
/// 칸 단위로 옮기면 둘 다 공짜다: 제목 글자가 든 칸은 애초에 경계 문자가 아니라 선이 안
/// 그려지고, 합쳐진 칸은 비트 그대로 세 갈래·네 갈래가 나온다.
pub struct Seg {
    pub x: u16,
    pub y: u16,
    /// 칸 **가운데에서** 뻗는 방향(위·아래·왼·오른). `canvas::box_bits` 의 비트다.
    pub bits: u8,
    /// 그 칸의 글자색 — 활성/비활성 테두리 색이 여기 실려 온다(캔버스가 이미 정한 값이라
    /// 뷰가 다시 판정하지 않는다).
    pub color: ColorU,
}

impl Seg {
    const UP: u8 = 0b1000;
    const DOWN: u8 = 0b0100;
    const LEFT: u8 = 0b0010;
    const RIGHT: u8 = 0b0001;
}

/// 얇은 바의 픽셀 두께. 칸 폭보다 얇아야 "선"으로 읽힌다.
const BAR_PX: f32 = 4.;

/// 패널 테두리 선의 두께. 스플리터 바보다 얇다 — 저건 **잡는 것**이고 이건 **경계**라,
/// 같은 굵기면 어느 것이 잡히는지 손이 헷갈린다.
const FRAME_PX: f32 = 1.5;

pub struct SplitterOverlay {
    child: Box<dyn Element>,
    bars: Vec<Bar>,
    /// 패널 테두리(경계 문자 칸)를 옮긴 선분들. 비어 있으면 아무것도 안 그린다.
    segs: Vec<Seg>,
    /// 셀 자리표 id(`SessionView::CELL_PROBE`) — 셀 기하의 원천.
    probe_id: &'static str,
    origin: Option<Point>,
}

impl SplitterOverlay {
    pub fn new(
        child: Box<dyn Element>,
        bars: Vec<Bar>,
        segs: Vec<Seg>,
        probe_id: &'static str,
    ) -> Self {
        Self { child, bars, segs, probe_id, origin: None }
    }

    pub fn finish(self) -> Box<dyn Element> {
        Box::new(self)
    }

    /// 경계 문자 칸들을 실제 선으로. 칸 **가운데**를 지나게 그려서 이웃 칸의 선과
    /// 이어진다(끝을 칸 경계까지 늘리는 이유 — 반 칸만 그리면 칸마다 틈이 생긴다).
    fn paint_frames(&self, origin: Vector2F, cw: f32, ch: f32, ctx: &mut PaintContext) {
        let half = FRAME_PX / 2.;
        for seg in &self.segs {
            let x0 = origin.x() + seg.x as f32 * cw;
            let y0 = origin.y() + seg.y as f32 * ch;
            let (cx, cy) = (x0 + cw / 2., y0 + ch / 2.);
            let mut line = |rect: RectF| {
                ctx.scene
                    .draw_rect_without_hit_recording(rect)
                    .with_background(Fill::Solid(seg.color));
            };
            if seg.bits & Seg::LEFT != 0 {
                line(RectF::new(vec2f(x0, cy - half), vec2f(cx - x0 + half, FRAME_PX)));
            }
            if seg.bits & Seg::RIGHT != 0 {
                line(RectF::new(vec2f(cx - half, cy - half), vec2f(x0 + cw - cx + half, FRAME_PX)));
            }
            if seg.bits & Seg::UP != 0 {
                line(RectF::new(vec2f(cx - half, y0), vec2f(FRAME_PX, cy - y0 + half)));
            }
            if seg.bits & Seg::DOWN != 0 {
                line(RectF::new(vec2f(cx - half, cy - half), vec2f(FRAME_PX, y0 + ch - cy + half)));
            }
        }
    }
}

impl Element for SplitterOverlay {
    fn layout(
        &mut self,
        constraint: SizeConstraint,
        ctx: &mut LayoutContext,
        app: &AppContext,
    ) -> Vector2F {
        self.child.layout(constraint, ctx, app)
    }

    fn after_layout(&mut self, ctx: &mut AfterLayoutContext, app: &AppContext) {
        self.child.after_layout(ctx, app);
    }

    fn paint(&mut self, origin: Vector2F, ctx: &mut PaintContext, app: &AppContext) {
        self.origin = Some(Point::from_vec2f(origin, ctx.scene.z_index()));
        // 자식(캔버스 줄들)이 먼저 — 자리표도 이때 남는다.
        self.child.paint(origin, ctx, app);
        if self.bars.is_empty() && self.segs.is_empty() {
            return;
        }
        let Some(cell) = ctx.position_cache.get_position(self.probe_id) else {
            return;
        };
        let (cw, ch) = (cell.width(), cell.height());
        if !(cw.is_finite() && ch.is_finite()) || cw <= 0.5 || ch <= 0.5 {
            return;
        }
        // 테두리를 **먼저** — 스플리터 바는 그 위에 얹혀야 잡는 자리가 또렷하다.
        self.paint_frames(origin, cw, ch, ctx);
        for bar in &self.bars {
            let cover = RectF::new(
                vec2f(origin.x() + bar.x as f32 * cw, origin.y() + bar.y as f32 * ch),
                vec2f(bar.w as f32 * cw, bar.h as f32 * ch),
            );
            // 경계 문자 칸을 바탕색으로 덮는다 — 문자와 바가 겹쳐 보이면 둘 다 지저분하다.
            ctx.scene
                .draw_rect_without_hit_recording(cover)
                .with_background(Fill::Solid(theme::BG));
            // ★ 평소에는 **테두리와 같은 굵기의 선**이다(2026-08-01 사용자 지시).
            //
            // 종전에는 늘 `BAR_PX`(4px) 알약이라, 패널 테두리(1.5px)와 나란히 놓이면
            // 경계만 유독 굵어 "선문자가 남은 것"처럼 보였다 — 정본은 이 자리도 한 겹
            // 선이다. **잡을 수 있다는 신호는 잡으려 할 때 나오면 된다**: 마우스를
            // 올리거나 끌고 있을 때만 굵은 알약으로 자란다(그때는 손가락이 목표를
            // 찾는 중이라 굵은 편이 낫다).
            let px = if bar.active { BAR_PX } else { FRAME_PX };
            let thin = if bar.vertical {
                RectF::new(
                    vec2f(cover.center().x() - px / 2., cover.origin_y()),
                    vec2f(px, cover.height()),
                )
            } else {
                RectF::new(
                    vec2f(cover.origin_x(), cover.center().y() - px / 2.),
                    vec2f(cover.width(), px),
                )
            };
            let color = if bar.active { theme::FOCUS } else { theme::BORDER };
            let painted = ctx
                .scene
                .draw_rect_without_hit_recording(thin)
                .with_background(Fill::Solid(color));
            // 둥근 끝은 **굵을 때만** 뜻이 있다 — 1.5px 선에 50% 반경을 주면 끝이
            // 뭉개져 이웃 테두리와 이음새가 어긋나 보인다.
            if bar.active {
                painted.with_corner_radius(CornerRadius::with_all(Radius::Percentage(50.)));
            }
        }
    }

    fn dispatch_event(
        &mut self,
        event: &DispatchedEvent,
        ctx: &mut EventContext,
        app: &AppContext,
    ) -> bool {
        self.child.origin().is_some() && self.child.dispatch_event(event, ctx, app)
    }

    fn size(&self) -> Option<Vector2F> {
        self.child.size()
    }

    fn origin(&self) -> Option<Point> {
        self.origin
    }

    fn parent_data(&self) -> Option<&dyn std::any::Any> {
        self.child.parent_data()
    }
}
