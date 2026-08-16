//! GUI 뷰. **그리기만 한다** — 무엇을 그릴지는 `base` 가 정한다.
//!
//! 짝이 되는 TUI 뷰는 `tui::root_view`. 두 파일을 나란히 놓고 보면
//! 다른 것은 엘리먼트 타입과 색 지정뿐이고, 상태·액션·키는 전부 core 에서 온다.

use base::i18n::t;
use base::{Action, BINDINGS, Block, BlockList, BlockState, keymap};

use crate::{mono_font, titlebar};
use warpui::color::ColorU;
use warpui::elements::{
    Container, Expanded, Flex, Hoverable, MainAxisSize, ParentElement, Rect, Stack, Text,
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
            .map(|binding| FixedBinding::new(binding.key, DemoAction::Key(binding.action), id!("RootView"))),
    );
}

/// 이 뷰가 받는 것 — core 의 액션과 **창 버튼**.
///
/// 창 버튼을 [`Action`] 에 넣지 않는 이유는 세션 뷰의 `ViewAction::WindowButton` 과
/// 같다: `Action` 은 정본 클라와 나눠 쓰는 **의도**의 목록인데 터미널 안 클라에는 창이
/// 없다. 그래서 뷰 계층에서만 감싼다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DemoAction {
    Key(Action),
    Window(titlebar::Button),
}

pub struct RootView {
    blocks: BlockList,
    font: FamilyId,
    /// 창 버튼의 마우스 상태(`pytmux-1`) — 세션 뷰의 `titlebar_click_states` 와 같은
    /// 자리다. 프레임을 넘어 살아야 hover 가 추적된다.
    titlebar_click_states: std::cell::RefCell<Vec<warpui::elements::MouseStateHandle>>,
    /// 창에게 말해 둔 띠 높이와 **같은 배율**을 쓴다(`main::tell_window_the_band` 가
    /// 기동 때 같은 값을 읽는다). 데모 창에는 설정을 다시 읽을 펌프가 없다.
    font_scale: f32,
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
            titlebar_click_states: Default::default(),
            font_scale: base::Config::load().font_scale,
        }
    }

    /// 창 버튼 `i` 번째의 마우스 상태.
    fn titlebar_mouse_state(&self, i: usize) -> warpui::elements::MouseStateHandle {
        let mut states = self.titlebar_click_states.borrow_mut();
        while states.len() <= i {
            states.push(Default::default());
        }
        states[i].clone()
    }

    /// 머리줄 — 세션 뷰와 **같은 줄**이다(`titlebar` 모듈). 데모 창도 OS 장식이 없으므로
    /// ⛔ 이 줄이 없으면 **마우스로 창을 닫을 자리가 사라진다**(Windows·Linux 기준. 맥은
    /// 신호등이 남는다). 데모는 `pytmux-gui demo` 로 문서에 있는 실행 갈래다.
    fn render_titlebar(&self) -> Box<dyn Element> {
        let title = self.text(t("pytmux-gui · 블록 데모"), 12., palette::DIM);
        let buttons = titlebar::BUTTONS
            .iter()
            .enumerate()
            .map(|(i, button)| {
                let hovered = self
                    .titlebar_click_states
                    .borrow()
                    .get(i)
                    .is_some_and(|s: &warpui::elements::MouseStateHandle| {
                        s.lock().is_ok_and(|s| s.is_mouse_over_element())
                    });
                let fg = if hovered { palette::FG } else { palette::DIM };
                let slot = titlebar::slot(
                    self.font_scale,
                    self.text(button.glyph(), 12., fg),
                    hovered.then(|| button.hover_bg()),
                );
                let button = *button;
                Hoverable::new(self.titlebar_mouse_state(i), |_| slot)
                    .on_click(move |evt, _, _| {
                        evt.dispatch_typed_action(DemoAction::Window(button));
                    })
                    .finish()
            })
            .collect();
        titlebar::row(self.font_scale, title, buttons)
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
        // ★ 앱 이름은 **머리줄로 옮겼다**(`pytmux-1`) — 그 줄이 이 창의 타이틀바다.
        //   여기 한 번 더 적으면 같은 이름이 두 줄에 뜬다.
        column = column.with_child(self.text(keymap::help_line(), 12., palette::DIM));

        for (index, block) in self.blocks.blocks().iter().enumerate() {
            column = column.with_child(self.render_block(block, index == selected, expanded));
        }

        // 머리줄은 바깥 여백 **밖**이다(세션 뷰와 같은 이유 — 창 버튼이 모서리에 닿아야
        // 하고, 창에게 말해 둔 띠와 자리가 같아야 한다).
        let inside = Flex::column()
            .with_main_axis_size(MainAxisSize::Max)
            .with_child(self.render_titlebar())
            .with_child(
                Expanded::new(
                    1.,
                    Container::new(column.finish())
                        .with_uniform_padding(12.)
                        .finish(),
                )
                .finish(),
            )
            .finish();
        Stack::new()
            .with_child(Rect::new().with_background_color(palette::BG).finish())
            .with_child(inside)
            .finish()
    }
}

impl TypedActionView for RootView {
    type Action = DemoAction;

    fn handle_action(&mut self, action: &DemoAction, ctx: &mut ViewContext<Self>) {
        let action = match action {
            DemoAction::Key(action) => *action,
            // 창 버튼 — 뜻은 상류 `ViewContext` 가 그대로 준다(세션 뷰와 같은 셋).
            DemoAction::Window(titlebar::Button::Minimize) => {
                ctx.minimize_window();
                return;
            }
            DemoAction::Window(titlebar::Button::Maximize) => {
                ctx.toggle_maximized_window();
                return;
            }
            // 닫기는 `Quit` 와 **같은 길**이다 — 데모 창에서는 그 둘이 같은 뜻이다.
            DemoAction::Window(titlebar::Button::Close) => Action::Quit,
        };
        if action == Action::Quit {
            // GUI 에서는 창을 닫는 것이 종료다. 이 요청은 상류의 `should_close_window`
            // 로 돌아오고, 거기서 `crate::quit_now` 가 프로세스를 끝낸다
            // (`main::app_callbacks` · `pytmux/pytmux-163`) — 종전에는 그 물음에 답할
            // 콜백이 없어 **데모 창도 닫히지 않고 남았다**.
            ctx.close_window();
            return;
        }
        // 상태가 실제로 바뀐 경우에만 다시 그린다.
        if self.blocks.apply(action) {
            ctx.notify();
        }
    }
}
