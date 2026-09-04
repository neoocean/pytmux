//! 화면 하나를 **제대로** 여는 한 자리 — 두 축(`interaction`·`screen_key_conformance`)이 같이 쓴다.
//!
//! `open()` 한 줄로 열리지 않는 판이 여럿이다(작성창은 버퍼가, 인자 폼은 명령이, 플러그인
//! 판은 목록인지 글인지가 함께 서야 한다). 반쯤 선 판에 키를 먹이면 재는 것은 제품이 아니라
//! **테스트가 만든 이상한 상태**가 된다.
//!
//! ⛔ 여는 길을 축마다 따로 적으면 두 축이 **서로 다른 판**을 재게 된다 — 그러면 한쪽이
//! 초록인 이유가 다른 쪽의 빨강과 무관해진다. 그래서 여기 한 벌만 둔다.
//!
//! ⛔ 와일드카드가 없다 — 화면이 늘면 여기가 안 컴파일된다(그때 여는 길을 적게 된다).

use base::screens::{Prompt, Screen, Screens};

pub fn opened(screen: Screen) -> Screens {
    let mut screens = Screens::new();
    match screen {
        Screen::Tabs => {
            // 탭 둘(둘 다 탭 줄) — 하나뿐이면 스위처가 아예 안 열린다.
            assert!(
                screens.open_tab_switcher(&[(true, false), (true, false)]),
                "탭 스위처가 안 열렸다"
            );
        }
        Screen::Prompt => screens.ask(Prompt::RenameTab, ""),
        Screen::Confirm => screens.confirm(Prompt::KillPane),
        Screen::Commands => screens.open_palette(),
        Screen::Options => {
            // 인자 폼이 있는 명령 하나. 표에 없는 이름이면 열리지 않으므로 확인한다.
            assert!(
                screens.open_options("split-window"),
                "인자 폼이 안 열렸다 — `split-window` 가 options 표에서 빠졌나"
            );
        }
        Screen::InfoTabs => screens.open_info_tabs(),
        Screen::Compose => screens.open_compose(""),
        // 목록형(`form`·`list`)으로 연다 — pytmux-181 이 신고한 그 판이다.
        Screen::PluginView => screens.open_plugin_view(true),
        Screen::Keys
        | Screen::Tree
        | Screen::Buffers
        | Screen::Version
        | Screen::ShellOutput
        | Screen::RestartCheck
        | Screen::Autoresume
        | Screen::MergeRemote
        | Screen::Layouts
        | Screen::Notices
        | Screen::Menu
        | Screen::Plugins
        | Screen::Hooks
        | Screen::Settings
        | Screen::Summary
        | Screen::SearchResults
        // 커서 판은 `open()` 한 줄이면 선다 — 줄 다섯이 정적이고(설정 표에서 온다)
        // 견본은 그림이라 core 가 들 상태가 없다(pytmux-375).
        // 런타임 계측 판도 `open()` 한 줄이면 선다 — 줄은 뷰가 모은 값에서 나오고
        // core 가 들 상태가 없다(pytmux-457).
        | Screen::DebugStats
        | Screen::Cursor => screens.open(screen),
    }
    assert_eq!(
        screens.top(),
        Some(screen),
        "{screen:?} 를 열었는데 맨 위가 그것이 아니다"
    );
    screens
}
