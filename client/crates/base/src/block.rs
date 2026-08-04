//! 블록 모델 — 명령 한 번의 실행이 블록 하나다.
//!
//! P1 에서는 **고정 표본**으로만 채운다. 진짜 블록은 pytmux 서버가 경계를 알려 줘야
//! 만들어지는데(설계문서 §5: 서버는 지금 렌더된 행만 보내고 명령 경계·종료코드·cwd 가
//! 없다), 그건 P4 의 일이다. 여기서 모델을 먼저 세우는 이유는 **두 뷰가 같은 것을
//! 그리는지**를 P1 에서 확인하기 위해서다.

/// 블록의 진행 상태.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BlockState {
    /// 아직 돌고 있다.
    Running,
    /// 끝났다. 종료코드를 담는다.
    Exited(i32),
}

impl BlockState {
    /// 성공으로 끝났는가. 돌고 있는 중이면 아직 모른다(`None`).
    pub fn succeeded(&self) -> Option<bool> {
        match self {
            BlockState::Running => None,
            BlockState::Exited(code) => Some(*code == 0),
        }
    }

    /// 상태줄에 붙일 짧은 표식. 색은 뷰가 정한다 — 여기서는 글자만 준다.
    pub fn badge(&self) -> &'static str {
        match self {
            BlockState::Running => "···",
            BlockState::Exited(0) => "ok",
            BlockState::Exited(_) => "err",
        }
    }
}

/// 명령 한 번의 실행.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Block {
    /// 사용자가 친 명령.
    pub command: String,
    /// 명령이 돈 디렉터리.
    pub cwd: String,
    /// 출력. 줄 단위로 보관한다 — 뷰가 접거나 잘라 쓸 수 있어야 하기 때문이다.
    pub output: Vec<String>,
    pub state: BlockState,
}

impl Block {
    pub fn new(
        command: impl Into<String>,
        cwd: impl Into<String>,
        output: impl IntoIterator<Item = impl Into<String>>,
        state: BlockState,
    ) -> Self {
        Self {
            command: command.into(),
            cwd: cwd.into(),
            output: output.into_iter().map(Into::into).collect(),
            state,
        }
    }

    /// 접힌 상태에서 보여 줄 한 줄 요약.
    pub fn summary(&self) -> String {
        let lines = self.output.len();
        // ★ `cmd` 를 **마지막에** 채운다 — 명령 문자열에 `{state}` 같은 글자가 들어
        // 있어도 다시 치환되지 않는다(먼저 채우면 그 안까지 긁는다).
        crate::i18n::tf(
            "{cmd} · {state} · {n}줄",
            &[
                ("state", self.state.badge()),
                ("n", &lines.to_string()),
                ("cmd", &self.command),
            ],
        )
    }
}

/// 블록들과 그중 선택된 위치.
///
/// 선택은 **항상 유효한 인덱스**이거나, 목록이 비었을 때만 의미가 없다. 이 불변식을
/// 여기서 지키므로 뷰는 `selected_block()` 이 주는 것만 그리면 된다.
#[derive(Clone, Debug, Default)]
pub struct BlockList {
    blocks: Vec<Block>,
    selected: usize,
    /// 선택된 블록의 출력을 펼쳐 보이는가.
    expanded: bool,
}

impl BlockList {
    pub fn new(blocks: Vec<Block>) -> Self {
        Self {
            blocks,
            selected: 0,
            expanded: true,
        }
    }

    /// P1 데모용 고정 표본. P4 에서 서버가 주는 실제 블록으로 대체된다.
    pub fn sample() -> Self {
        Self::new(vec![
            Block::new(
                "cargo check --workspace",
                "~/p4/playground/scripts/pytmux-client",
                ["    Checking warpui_core v0.1.0", "    Finished in 7.83s"],
                BlockState::Exited(0),
            ),
            Block::new(
                "python3 tests/run.py test_server",
                "~/p4/playground/scripts/pytmux",
                ["running 42 tests", "42 passed, 0 failed"],
                BlockState::Exited(0),
            ),
            Block::new(
                "p4 submit -c 67429",
                "~/p4/playground/scripts/pytmux-client",
                ["Change 67429 submitted."],
                BlockState::Exited(0),
            ),
            Block::new(
                "grep -rn 'OSC 133' pytmuxlib/",
                "~/p4/playground/scripts/pytmux",
                ["(결과 없음 — 셸 통합이 아직 없다)"],
                BlockState::Exited(1),
            ),
            Block::new(
                "cargo run -p gui",
                "~/p4/playground/scripts/pytmux-client",
                ["   Compiling gui v0.1.0"],
                BlockState::Running,
            ),
        ])
    }

    pub fn blocks(&self) -> &[Block] {
        &self.blocks
    }

    pub fn selected_index(&self) -> usize {
        self.selected
    }

    pub fn is_expanded(&self) -> bool {
        self.expanded
    }

    pub fn selected_block(&self) -> Option<&Block> {
        self.blocks.get(self.selected)
    }

    pub fn is_empty(&self) -> bool {
        self.blocks.is_empty()
    }

    /// 액션을 적용한다. **상태가 실제로 바뀌었을 때만** `true` 를 돌려준다.
    ///
    /// 뷰는 이 반환값으로 다시 그릴지 정한다 — 목록 끝에서 아래로 더 누르는 것 같은
    /// 무효 입력에 매 프레임 repaint 를 걸지 않기 위해서다.
    pub fn apply(&mut self, action: crate::Action) -> bool {
        use crate::Action;
        match action {
            Action::SelectNext => {
                if self.blocks.is_empty() || self.selected + 1 >= self.blocks.len() {
                    return false;
                }
                self.selected += 1;
                true
            }
            Action::SelectPrev => {
                if self.selected == 0 {
                    return false;
                }
                self.selected -= 1;
                true
            }
            Action::SelectFirst => {
                if self.selected == 0 {
                    return false;
                }
                self.selected = 0;
                true
            }
            // ── 세션 뷰의 액션(패리티 G1) ─────────────────────────────────
            // 블록 목록은 이것들과 아무 상관이 없다. **와일드카드로 받지 않는 이유**는
            // 새 액션이 늘 때 여기서 컴파일이 막혀야 "이 목록도 봐야 하나"를 한 번은
            // 생각하게 되기 때문이다.
            Action::SplitLeftRight
            | Action::SplitTopBottom
            | Action::KillPane
            | Action::NewTab
            | Action::KillTab
            | Action::NextTab
            | Action::PrevTab
            | Action::LastTab
            | Action::SelectTab(_)
            | Action::Redraw
            | Action::Zoom
            | Action::NextPane
            | Action::LastPane
            | Action::CycleLayout
            | Action::RotatePanes
            | Action::SwapPane { .. }
            | Action::BreakPane
            | Action::SelectPane(_)
            | Action::ResizePane(_)
            | Action::TogglePin
            | Action::PasteBuffer
            | Action::ShowKeys
            | Action::ShowTabs
            | Action::ShowTree
            | Action::ShowBuffers
            | Action::RenameTab
            | Action::MoveTab
            | Action::ShowCommands
            | Action::ShowSettings
            | Action::KillServer
            | Action::RestartServer
            | Action::RemoteAttach
            | Action::RemoteNewTab
            | Action::RemoteDetach
            | Action::ShowPlugins
            | Action::ToggleClock
            | Action::ShowSummary
            | Action::FontScale { .. }
            | Action::FontScaleReset
            | Action::RenamePane
            | Action::ShowPaneNumbers
            | Action::ShowMenu
            | Action::ShowNotices
            | Action::SendEscape
            | Action::SendBacktick
            | Action::ToggleCalendar
            | Action::SetOverlay { .. }
            | Action::PluginToggle { .. }
            | Action::PluginDo { .. }
            | Action::ToggleUsageView
            | Action::ToggleBorderStatus
            | Action::ToggleInactiveDim
            | Action::ToggleServerOption(_)
            | Action::ClearHistory
            | Action::MoveTabBy(_)
            | Action::MoveTabAt { .. }
            | Action::SwapTab
            | Action::SetPinned(_)
            | Action::SetEnum(..)
            | Action::CapturePane(_)
            | Action::PipePane
            | Action::JoinPane
            | Action::RequestVersion
            | Action::RequestRestartCheck
            | Action::MergeRemoteTab
            | Action::DisplayPopup
            | Action::PopupClose
            | Action::RunShell
            | Action::IfShell
            | Action::DisplayMessage
            | Action::SourceFile
            | Action::SetOption
            | Action::ShowOptions
            | Action::SetHook
            | Action::ShowHooks
            | Action::ShowCommandOptions(_)
            | Action::SetSync(_)
            | Action::SetMonitor { .. }
            | Action::SetAutoRename(_)
            | Action::SetServerOption(..)
            | Action::SendKeys
            | Action::BindKey
            | Action::UnbindKey
            | Action::ShowLayouts
            | Action::SaveTabLayout
            | Action::LoadTabLayout(_)
            | Action::SaveLayout
            | Action::RestoreLayout
            | Action::RespawnPane
            | Action::ToggleSync
            | Action::ToggleMonitorActivity
            | Action::ToggleMonitorBell
            | Action::JumpPrompt { .. }
            | Action::ShowCompose
            | Action::ShowInfoTabs
            | Action::ToggleAutoresume
            | Action::SetLang(_)
            | Action::TogglePromptClear
            | Action::SearchScrollback
            | Action::SearchAgain { .. }
            | Action::Reconnect
            | Action::ToggleFullscreen
            | Action::RestartAll
            | Action::ToggleAutoRename => false,
            Action::SelectLast => {
                let last = self.blocks.len().saturating_sub(1);
                if self.selected == last {
                    return false;
                }
                self.selected = last;
                true
            }
            Action::ToggleExpand => {
                self.expanded = !self.expanded;
                true
            }
            // 종료는 상태 전이가 아니라 런타임에 대한 요청이라 여기서 처리하지 않는다.
            Action::Quit => false,
            // 플랜/거부 전용 화면은 **블록 목록의 상태가 아니다** — 뷰가 자기 화면을
            // 덮는 일이라 여기서는 아무 일도 안 일어난다.
            Action::ToggleClaudeDetail => false,
            // 스크롤 모드 진입은 입력 모드의 전이다(`keys::interpret`). 블록 목록과
            // 무관하므로 여기서는 아무 일도 안 일어난다.
            Action::EnterScroll | Action::ToggleScroll => false,
        }
    }
}

#[cfg(test)]
#[path = "block_tests.rs"]
mod tests;
