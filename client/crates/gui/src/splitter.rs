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
    /// 이 바가 **건너뛰는** 칸들 — 테두리 이음새(`┬`·`┴`·`├`·`┤`·`┼`)다(§10-21ⓟ).
    ///
    /// 서버가 주는 경계 사각형은 노드 rect **전체 높이**(좌우 분할이면 위·아래 테두리
    /// 줄까지)를 덮는다. 그 끝 칸은 이음새라 가로 테두리도 지나가는데, 바가 그 칸을
    /// 바탕색으로 덮고 세로선만 그리면 **가로 테두리가 거기서 끊기고 세로선만 남아**
    /// T 자로 튀어나온 것처럼 보인다. 제보의 스크린샷이 정확히 그 그림이었다.
    ///
    /// 그래서 이음새 칸의 주인은 **테두리**다([`Seg`]) — 바는 그 사이만 그린다.
    pub skip: std::collections::BTreeSet<(u16, u16)>,
    /// 평상시 선 색을 덮어쓰는 값 — 원격(분홍)·degraded(빨강)일 때만 있다(§10-21ⓩ).
    ///
    /// 경계도 테두리의 일부라, 테두리만 분홍인데 경계가 파랗게 남으면 그 자리가 도리어
    /// 눈에 띈다. `None` 이면 종전대로 [`theme::BORDER`] 다.
    pub tint: Option<ColorU>,
}

impl Bar {
    /// 이 바가 실제로 칠하는 **연속 구간들** — 이음새에서 끊긴다.
    ///
    /// 세로 바면 `(시작 y, 칸 수)`, 가로 바면 `(시작 x, 칸 수)`. 구간으로 자르는 이유:
    /// 칸마다 따로 칠하면 굵은 알약의 둥근 끝이 칸마다 생겨 점선처럼 보인다.
    pub fn runs(&self) -> Vec<(u16, u16)> {
        let (start, len) = if self.vertical { (self.y, self.h) } else { (self.x, self.w) };
        let mut out: Vec<(u16, u16)> = Vec::new();
        for step in 0..len {
            let pos = start + step;
            let cell = if self.vertical { (self.x, pos) } else { (pos, self.y) };
            if self.skip.contains(&cell) {
                continue;
            }
            match out.last_mut() {
                // 바로 앞 칸에서 이어지면 같은 구간이다.
                Some(run) if run.0 + run.1 == pos => run.1 += 1,
                _ => out.push((pos, 1)),
            }
        }
        out
    }
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

/// 블록 문자 칸 하나 — 칸의 일부를 채운 **사각형**으로 옮긴 것(§10-21ⓘ).
///
/// # 왜 글자로 안 그리나
///
/// 테두리와 **같은 이유**다: 블록 문자는 우리가 고른 고정폭 글꼴에 거의 없어 폴백으로
/// 가고, 폴백의 진폭이 칸너비의 정수배가 아니면 그림이 행마다 밀린다. 마스코트가
/// 정확히 그 증상이었다(제보 §10-21ⓘ).
///
/// 다른 점 하나: 테두리는 **크롬 칸만** 옮기는데(패널 안 `htop` 의 선까지 고쳐 그리면
/// 남의 화면을 바꾸는 것이다) 블록은 **패널 안도 옮긴다**. 블록은 선문자와 달리
/// "이 칸의 이만큼이 이 색"이라는 뜻이 전부라, 사각형으로 그린 것이 글리프보다
/// **더 정확한 그림**이다(덜 정확한 것이 아니다).
pub struct Block {
    pub x: u16,
    pub y: u16,
    pub fill: proto::canvas::BlockFill,
    /// 그 칸의 글자색 — 배경은 캔버스가 이미 칠했다.
    pub color: ColorU,
}

/// 활성 패널의 **커서 칸**(§10-21ⓒ) — 캔버스 셀 좌표.
///
/// 서버는 `screen` 프레임마다 커서 위치를 통째로 준다(`SessionState::pane_cursor`).
/// 그런데 **뷰가 그것을 한 번도 안 읽고 있었다** — 전 저장소에서 그 값을 읽는 곳이
/// 정의와 proto 자기 테스트뿐이었다(GUI 0건). 그래서 "터미널에 커서가 안 보인다"가 됐다.
pub struct Cursor {
    pub x: u16,
    pub y: u16,
}

/// 패널 오른쪽 **외곽선 위**에 얹는 표시용 스크롤바(§10-21ⓨ2).
///
/// 칸을 안 먹는다 — 테두리를 실제 선으로 그리는 덕에 그 선 위에 겹쳐 그릴 수 있고,
/// 캔버스 격자를 안 건드리니 서버에 보고하는 행·열도 안 바뀐다.
///
/// 조작용 터치 스크롤바(`base::scrollbar::chars`)와 **다른 것**이다: 저건 한 열을 먹고
/// 탭을 받는다. 섞으면 touch-scroll 을 끈 사람에게 표시까지 사라진다.
pub struct ScrollHint {
    /// 패널 **테두리**의 오른쪽 열과 안쪽 위/아래(셀 좌표).
    pub x: u16,
    pub y: u16,
    pub h: u16,
    /// 트랙을 1.0 으로 본 (썸 시작, 썸 길이) — 값은 core 가 정한다.
    pub start: f64,
    pub len: f64,
}

/// 마우스가 올라온 **범위**의 밑줄(§10-21ⓥ2·ⓧ2) — 캔버스 셀 좌표 `[x0, x1)`.
///
/// 왜 밑줄인가: 배경을 칠하면 그 자리의 글자가 선택(드래그 복사)처럼 보이고, 색을 바꾸면
/// 그 앱이 칠한 색을 우리가 덮는다. 밑줄은 **글자를 안 건드리고** 링크 관습과도 맞는다.
pub struct SpanMark {
    pub y: u16,
    pub x0: u16,
    pub x1: u16,
}

/// 밑줄 두께.
const MARK_PX: f32 = 1.5;

/// 표시용 스크롤바의 두께. 테두리(1.5px)보다 굵어야 **선 위에 얹힌 것**으로 읽힌다.
const HINT_PX: f32 = 3.;

/// 커서 테두리의 두께.
const CURSOR_PX: f32 = 2.;

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
    /// 블록 문자 칸들을 옮긴 사각형들(§10-21ⓘ).
    blocks: Vec<Block>,
    /// 활성 패널의 커서 칸(없으면 안 그린다 — 커서를 감춘 패널·화면이 뜬 동안).
    cursor: Option<Cursor>,
    /// 스크롤한 패널의 표시용 막대(없으면 안 그린다).
    hints: Vec<ScrollHint>,
    /// 마우스가 올라온 범위의 밑줄(없으면 안 그린다).
    marks: Vec<SpanMark>,
    /// 셀 자리표 id(`SessionView::CELL_PROBE`) — 셀 기하의 원천.
    probe_id: &'static str,
    origin: Option<Point>,
}

impl SplitterOverlay {
    pub fn new(
        child: Box<dyn Element>,
        bars: Vec<Bar>,
        segs: Vec<Seg>,
        blocks: Vec<Block>,
        cursor: Option<Cursor>,
        hints: Vec<ScrollHint>,
        marks: Vec<SpanMark>,
        probe_id: &'static str,
    ) -> Self {
        Self { child, bars, segs, blocks, cursor, hints, marks, probe_id, origin: None }
    }

    /// 블록 문자 칸들을 사각형으로. 비율(`BlockFill`)을 칸 크기에 곱할 뿐이다 —
    /// 무엇을 채우나는 proto 가 정하고 여기는 몇 픽셀인가만 안다.
    fn paint_blocks(&self, origin: Vector2F, cw: f32, ch: f32, ctx: &mut PaintContext) {
        for blk in &self.blocks {
            let x0 = origin.x() + blk.x as f32 * cw;
            let y0 = origin.y() + blk.y as f32 * ch;
            let f = blk.fill;
            let rect = RectF::new(
                vec2f(x0 + f.x0 * cw, y0 + f.y0 * ch),
                vec2f((f.x1 - f.x0) * cw, (f.y1 - f.y0) * ch),
            );
            // 음영(`░`)은 같은 사각형을 흐리게 — 색을 섞지 않고 알파만 낮춘다.
            // 배경 위에 얹히므로 결과는 글꼴이 그리던 점묘와 같은 밝기다.
            let mut color = blk.color;
            if f.alpha < 1. {
                color.a = (color.a as f32 * f.alpha).round().clamp(0., 255.) as u8;
            }
            ctx.scene
                .draw_rect_without_hit_recording(rect)
                .with_background(Fill::Solid(color));
        }
    }

    /// 표시용 스크롤바를 패널 오른쪽 **테두리 선 위**에 얹는다(§10-21ⓨ2).
    ///
    /// 트랙은 테두리 세로줄의 안쪽 구간이고, 그 위에 FOCUS 색 막대를 그린다 — 테두리와
    /// 같은 색이면 "선이 좀 굵어졌다"로만 보여 아무 말도 안 한다.
    fn paint_hints(&self, origin: Vector2F, cw: f32, ch: f32, ctx: &mut PaintContext) {
        for hint in &self.hints {
            let track_y = origin.y() + hint.y as f32 * ch;
            let track_h = hint.h as f32 * ch;
            let y0 = track_y + track_h * hint.start as f32;
            let h = (track_h * hint.len as f32).max(HINT_PX);
            // 테두리 선의 **가운데**에 얹는다(선과 같은 축이라야 얹힌 것으로 읽힌다).
            let cx = origin.x() + (hint.x as f32 + 0.5) * cw;
            let rect = RectF::new(vec2f(cx - HINT_PX / 2., y0), vec2f(HINT_PX, h));
            ctx.scene
                .draw_rect_without_hit_recording(rect)
                .with_background(Fill::Solid(theme::FOCUS))
                .with_corner_radius(CornerRadius::with_all(Radius::Percentage(50.)));
        }
    }

    /// 마우스가 올라온 범위에 밑줄을 긋는다(§10-21ⓥ2·ⓧ2).
    fn paint_marks(&self, origin: Vector2F, cw: f32, ch: f32, ctx: &mut PaintContext) {
        for mark in &self.marks {
            let x0 = origin.x() + mark.x0 as f32 * cw;
            let w = (mark.x1.saturating_sub(mark.x0)) as f32 * cw;
            // 글자 바닥에서 살짝 띄운다 — 붙이면 받침이 있는 글자(한글)와 겹친다.
            let y = origin.y() + (mark.y + 1) as f32 * ch - MARK_PX - 1.;
            ctx.scene
                .draw_rect_without_hit_recording(RectF::new(vec2f(x0, y), vec2f(w, MARK_PX)))
                .with_background(Fill::Solid(theme::FOCUS));
        }
    }

    /// 커서 칸을 **테두리 상자**로 그린다.
    ///
    /// # 왜 꽉 찬 블록이 아닌가 (첫 판의 의도된 한계)
    ///
    /// 터미널의 기본 커서는 꽉 찬 블록이고, 그 아래 글자는 **반전**돼 보인다. 이
    /// 오버레이는 캔버스를 **다 그린 뒤에** 얹히므로 블록을 칠하면 그 칸의 글자가
    /// 그대로 덮인다 — 커서가 놓인 글자를 못 읽게 만드는 것은 "커서가 보여야 한다"의
    /// 답이 될 수 없다. 반전을 하려면 그 칸을 런에서 갈라 **배경색**으로 칠해야 하고
    /// (터미널이 하는 방식), 그건 런 렌더를 손대는 일이라 §10-21ⓙ(셀 격자 슬라이스)와
    /// 같은 자리다. 그때 함께 옮긴다.
    fn paint_cursor(&self, origin: Vector2F, cw: f32, ch: f32, ctx: &mut PaintContext) {
        let Some(cur) = &self.cursor else { return };
        let x0 = origin.x() + cur.x as f32 * cw;
        let y0 = origin.y() + cur.y as f32 * ch;
        let mut line = |rect: RectF| {
            ctx.scene
                .draw_rect_without_hit_recording(rect)
                .with_background(Fill::Solid(theme::FOCUS));
        };
        line(RectF::new(vec2f(x0, y0), vec2f(cw, CURSOR_PX)));
        line(RectF::new(vec2f(x0, y0 + ch - CURSOR_PX), vec2f(cw, CURSOR_PX)));
        line(RectF::new(vec2f(x0, y0), vec2f(CURSOR_PX, ch)));
        line(RectF::new(vec2f(x0 + cw - CURSOR_PX, y0), vec2f(CURSOR_PX, ch)));
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
        if self.bars.is_empty()
            && self.segs.is_empty()
            && self.blocks.is_empty()
            && self.cursor.is_none()
        {
            return;
        }
        let Some(cell) = ctx.position_cache.get_position(self.probe_id) else {
            return;
        };
        let (cw, ch) = (cell.width(), cell.height());
        if !(cw.is_finite() && ch.is_finite()) || cw <= 0.5 || ch <= 0.5 {
            return;
        }
        // 블록이 맨 먼저 — 패널 **안**의 그림이라 크롬(테두리·바)이 그 위를 덮는 것이 맞다.
        self.paint_blocks(origin, cw, ch, ctx);
        // 테두리를 **먼저** — 스플리터 바는 그 위에 얹혀야 잡는 자리가 또렷하다.
        self.paint_frames(origin, cw, ch, ctx);
        for bar in &self.bars {
            // ★ 이음새 칸에서 끊어 그린다(§10-21ⓟ) — 그 칸은 테두리가 주인이다.
            //   통짜로 덮으면 가로 테두리가 거기서 끊기고 세로선만 남아 **T 자로
            //   튀어나온 것**처럼 보인다(제보의 스크린샷).
            for (pos, span) in bar.runs() {
                let cover = if bar.vertical {
                    RectF::new(
                        vec2f(origin.x() + bar.x as f32 * cw, origin.y() + pos as f32 * ch),
                        vec2f(bar.w as f32 * cw, span as f32 * ch),
                    )
                } else {
                    RectF::new(
                        vec2f(origin.x() + pos as f32 * cw, origin.y() + bar.y as f32 * ch),
                        vec2f(span as f32 * cw, bar.h as f32 * ch),
                    )
                };
                // 경계 문자 칸을 바탕색으로 덮는다 — 문자와 바가 겹쳐 보이면 둘 다
                // 지저분하다.
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
                // 잡는 중이면 FOCUS — 그건 **조작의 신호**라 상태색(tint)을 안 탄다.
                let color = if bar.active {
                    theme::FOCUS
                } else {
                    bar.tint.unwrap_or(theme::BORDER)
                };
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
        // 스크롤 힌트는 테두리 **위**에 얹는다(§10-21ⓨ2) — 그 선을 덮는 것이 뜻이다.
        self.paint_hints(origin, cw, ch, ctx);
        // 범위 밑줄(§10-21ⓥ2·ⓧ2) — 글자 **아래**에 긋는다(글자를 안 건드린다).
        self.paint_marks(origin, cw, ch, ctx);
        // 커서는 **맨 위**다 — 경계·바에 가리면 그 칸에 커서가 있는지 알 수 없다.
        self.paint_cursor(origin, cw, ch, ctx);
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
