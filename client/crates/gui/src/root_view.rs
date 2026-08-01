//! GUI 뷰. **그리기만 한다** — 무엇을 그릴지는 `base` 가 정한다.
//!
//! 짝이 되는 TUI 뷰는 `tui::root_view`. 두 파일을 나란히 놓고 보면
//! 다른 것은 엘리먼트 타입과 색 지정뿐이고, 상태·액션·키는 전부 core 에서 온다.

use base::i18n::t;
use base::{Action, BINDINGS, Block, BlockList, BlockState, keymap};

use crate::mono_font;
use warpui::color::ColorU;
use warpui::elements::{
    Container, Flex, MainAxisSize, ParentElement, Rect, Stack, Text,
};
use warpui::fonts::FamilyId;
use warpui::keymap::FixedBinding;
use warpui::{
    AppContext, Element, Entity, SingletonEntity as _, TypedActionView, View, ViewContext,
};

/// 배색. TUI 쪽은 터미널 팔레트를 쓰므로 색만은 공유하지 않는다 —
/// 대신 **무엇을 강조할지**(선택·성공·실패)는 두 뷰가 같다.
mod palette {
    use warpui::color::ColorU;

    pub const BG: ColorU = ColorU { r: 0x1a, g: 0x1b, b: 0x26, a: 0xff };
    pub const FG: ColorU = ColorU { r: 0xc0, g: 0xca, b: 0xf5, a: 0xff };
    pub const DIM: ColorU = ColorU { r: 0x56, g: 0x5f, b: 0x89, a: 0xff };
    pub const SELECTED_BG: ColorU = ColorU { r: 0x28, g: 0x34, b: 0x57, a: 0xff };
    pub const OK: ColorU = ColorU { r: 0x9e, g: 0xce, b: 0x6a, a: 0xff };
    pub const ERR: ColorU = ColorU { r: 0xf7, g: 0x76, b: 0x8e, a: 0xff };
    pub const RUNNING: ColorU = ColorU { r: 0xe0, g: 0xaf, b: 0x68, a: 0xff };
}

/// core 의 키 바인딩 표를 GUI 키맵에 등록한다.
///
/// 여기서 `BINDINGS` 를 순회하는 것이 요점이다 — 키 목록을 GUI 용으로 다시 적지 않는다.
pub fn init(ctx: &mut AppContext) {
    use warpui::keymap::macros::*;

    ctx.register_fixed_bindings(
        BINDINGS
            .iter()
            .map(|binding| FixedBinding::new(binding.key, binding.action, id!("RootView"))),
    );
}

pub struct RootView {
    blocks: BlockList,
    font: FamilyId,
}

impl RootView {
    pub fn new(ctx: &mut ViewContext<Self>) -> Self {
        // 터미널 클라이언트이므로 고정폭이 기본이고, 그 위에 보조 글꼴이 깔려야 한글이
        // 두부가 안 된다. 규칙은 전부 `mono_font` 에 있고 여기서는 부르기만 한다(뷰는
        // 얇게) — 두 뷰가 각자 적으면 한쪽에만 보조 글꼴이 빠진다.
        let font = warpui::fonts::Cache::handle(ctx).update(ctx, |cache, _| mono_font::install(cache));
        // 키 입력을 받으려면 포커스가 있어야 한다.
        ctx.focus_self();
        Self {
            blocks: BlockList::sample(),
            font,
        }
    }

    fn text(&self, s: impl Into<String>, size: f32, color: ColorU) -> Box<dyn Element> {
        Text::new_inline(s.into(), self.font, size)
            .with_color(color)
            .finish()
    }

    fn state_color(state: BlockState) -> ColorU {
        match state {
            BlockState::Running => palette::RUNNING,
            BlockState::Exited(0) => palette::OK,
            BlockState::Exited(_) => palette::ERR,
        }
    }

    /// 블록 하나. 선택된 것만 배경을 깔고, 펼침 상태면 출력까지 그린다.
    fn render_block(&self, block: &Block, selected: bool, expanded: bool) -> Box<dyn Element> {
        let mut column = Flex::column().with_main_axis_size(MainAxisSize::Min);

        // 머리줄: 상태 표식 + 명령 + cwd
        column = column.with_child(
            Flex::row()
                .with_spacing(8.)
                .with_child(self.text(
                    block.state.badge(),
                    13.,
                    Self::state_color(block.state),
                ))
                .with_child(self.text(block.command.clone(), 14., palette::FG))
                .with_child(self.text(block.cwd.clone(), 12., palette::DIM))
                .finish(),
        );

        // 본문: 펼쳐졌고 선택된 블록만. 접힌 블록은 머리줄만 남는다.
        if expanded && selected {
            for line in &block.output {
                column = column.with_child(self.text(format!("  {line}"), 13., palette::DIM));
            }
        }

        let content = column.finish();
        if selected {
            Container::new(content)
                .with_background_color(palette::SELECTED_BG)
                .with_uniform_padding(6.)
                .finish()
        } else {
            Container::new(content).with_uniform_padding(6.).finish()
        }
    }
}

impl Entity for RootView {
    type Event = ();
}

impl View for RootView {
    fn ui_name() -> &'static str {
        "RootView"
    }

    fn render(&self, _: &AppContext) -> Box<dyn Element> {
        let selected = self.blocks.selected_index();
        let expanded = self.blocks.is_expanded();

        let mut column = Flex::column().with_main_axis_size(MainAxisSize::Max);
        column = column.with_child(self.text(t("pytmux-gui · 블록 데모"), 15., palette::FG));
        column = column.with_child(self.text(keymap::help_line(), 12., palette::DIM));

        for (index, block) in self.blocks.blocks().iter().enumerate() {
            column = column.with_child(self.render_block(block, index == selected, expanded));
        }

        Stack::new()
            .with_child(Rect::new().with_background_color(palette::BG).finish())
            .with_child(Container::new(column.finish()).with_uniform_padding(12.).finish())
            .finish()
    }
}

impl TypedActionView for RootView {
    type Action = Action;

    fn handle_action(&mut self, action: &Action, ctx: &mut ViewContext<Self>) {
        if *action == Action::Quit {
            // GUI 에서는 창을 닫는 것이 종료다.
            ctx.close_window();
            return;
        }
        // 상태가 실제로 바뀐 경우에만 다시 그린다.
        if self.blocks.apply(*action) {
            ctx.notify();
        }
    }
}
