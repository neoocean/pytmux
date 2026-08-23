//! 캔버스를 덮는 화면(팝업·모달)의 **스택과 키 라우팅** — 패리티 G2.
//!
//! # 왜 core 에 있나
//!
//! 화면이 열려 있는 동안 키가 어디로 가는지는 **두 뷰가 같아야 한다**. 뷰마다 적으면 한쪽
//! 에서만 `Esc` 가 안 먹거나, 한쪽에서만 화면 뒤 패널로 키가 새는 일이 생긴다 — 그리고 그
//! 증상은 조용하다. 그리기는 뷰의 몫이고, **무엇이 떠 있고 키를 누가 먹나**는 여기다.
//!
//! 실제로 이 저장소는 그 갈라짐을 이미 한 번 만들었다: 플랜·거부 화면이 GUI 와 TUI 에
//! 각각 `detail_open: bool` 로 들어 있었다(같은 규칙을 두 번 적은 것). G2 에서 그 둘을
//! 이 스택 하나로 합친다.
//!
//! # 규칙 셋
//!
//! 1. **열려 있으면 모든 키를 먹는다.** 화면 뒤 패널로 새는 키가 있으면 사용자는 자기가
//!    무엇을 조작하고 있는지 알 수 없다.
//! 2. **모르는 키는 삼킨다 — 아무 일도 안 한다.** ⚠ 2026-08-17(pytmux-273)까지는 여기가
//!    "방향키는 화면의 것, 나머지는 닫는다"였다 — 파이썬 클라의 `InfoScreen` 규약을 **모든
//!    화면의 기본값**으로 잘못 옮긴 것이다. 정본에서 "아무 키나 닫는다"를 실제로 갖는
//!    화면은 `InfoScreen` 계열(`Keys`·`Version`·`ShellOutput`·`RestartCheck`·`Hooks`)
//!    **하나뿐**이고, 그 다섯은 여전히 그렇게 군다(`press` 안에서 명시적으로 남겨 뒀다).
//!    그 밖의 화면(목록·확인·플러그인 판 등)은 정의된 키가 아니면 삼킨다 — 관계없는 키를
//!    눌렀다고 판이 조용히 사라지면 사용자는 자기가 뭘 잃었는지 모른다.
//! 3. **캔버스 크기는 안 건드린다.** 화면은 덮을 뿐이라 서버에 알린 격자가 그대로다
//!    (건드리면 서버 재배치가 따라오고, 닫을 때 화면이 한 번 더 출렁인다).

use crate::keys::{Key, Mods};

/// 팝업이 화면 **세로 어디에** 서는가. 가로는 정본이 전부 가운데라 축이 하나다.
///
/// 값의 뜻과 어느 화면이 어디인지는 [`Screen::anchor`] 가 표로 적어 둔다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Anchor {
    /// 화면 위쪽부터. 읽는 판(정본 `InfoScreen`).
    Top,
    /// 화면 한가운데. 고르러 여는 판.
    Middle,
    /// 화면 바닥에 붙는다. 치던 흐름의 연장(`:` 프롬프트·팔레트·작성창).
    Bottom,
}

/// 떠 있을 수 있는 화면.
///
/// 목록이 여기 한 곳이라 뷰가 "모르는 화면"을 만날 수 없다(match 가 컴파일로 막는다).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Screen {
    /// 키 도움말 — prefix·esc·스크롤 모드의 표를 그대로 보인다.
    Keys,
    /// Claude 의 플랜 전문·거부 사유.
    ClaudeDetail,
    /// 탭 스위처 — 열려 있는 탭을 **고르기만** 하다가 `Enter` 로 전환한다.
    ///
    /// 파이썬 클라와 같은 동선이다(`esc Tab` · Tab 다음 · Shift+Tab 이전 · Enter 확정 ·
    /// Esc 취소). 키 **뗌**으로 확정하는 Alt+Tab 동선은 터미널에서 불가능하다 — 터미널이
    /// 키 뗌을 보고하지 않는다.
    Tabs,
    /// 세션·탭·패널 개요(`prefix w`). 서버 `request_tree` 회신을 그린다.
    Tree,
    /// 페이스트 버퍼 목록(`prefix =`). 고른 것을 패널에 붙인다.
    Buffers,
    /// 한 줄 입력(`prefix ,` 이름 바꾸기 등). 무엇을 물었는지는 [`Prompt`] 가 안다.
    Prompt,
    /// 예/아니오 확인. **되돌릴 수 없는 것 앞에만** 세운다.
    Confirm,
    /// 명령 팔레트(`prefix :` · `esc :`). 이름을 쳐서 좁히고 골라 실행한다.
    ///
    /// 목록과 입력이 **한 화면에 함께 있는** 유일한 화면이다 — 글자는 필터로 쌓이고
    /// 방향키는 선택을 옮긴다.
    Commands,
    /// 서버·클라 버전(`version`). **읽는 화면**이라 아무 키나 닫는다.
    Version,
    /// 셸 명령의 결과(`run-shell`). 읽는 화면이다.
    ShellOutput,
    /// 재시작 점검 결과(`restart-check`). 읽는 화면이다.
    RestartCheck,
    /// 같은 원격의 다른 탭을 지금 탭에 패널로 합칠 대상 고르기(`merge-remote-tab`).
    ///
    /// 목록형이지만 **`h`/`v` 가 분할 방향을 바꾼다**(파이썬과 같다) — 고르기 전에
    /// 어느 쪽으로 붙일지 정해야 해서다.
    MergeRemote,
    /// 레이아웃 프리셋 목록(`select-layout`). 고르면 바로 적용된다.
    Layouts,
    /// 지나간 알림들(`:notice-history`). 읽는 화면이지만 **`InfoScreen` 이 아니다** —
    /// 정본 `NoticeHistoryScreen` 은 `escape` 만 닫고 그 밖의 키는 삼킨다(pytmux-273 ②).
    ///
    /// 왜 필요한가: 알림은 상태줄에서 한 줄만, 그것도 다음 알림이 오면 사라진다.
    /// remote-attach 실패처럼 **놓치면 왜 안 됐는지 알 수 없는** 것이 그 줄로 온다.
    Notices,
    /// F10 메뉴(`prefix Enter`). 파이썬 `MENU_ITEMS` 와 **같은 문구**를 쓴다.
    ///
    /// 팔레트와 나란히 있는 이유: 팔레트는 **이름을 아는 사람**의 입구이고 메뉴는
    /// **모르는 사람**의 입구다. 파이썬 클라도 둘 다 갖는다.
    Menu,
    /// 플러그인 관리(파이썬의 `:plugins`). 설정 화면과 같은 규칙이다 — `Enter` 가
    /// 화면을 안 닫는다(여러 개를 연달아 켜고 끄는 화면이다).
    Plugins,
    /// **플러그인이 준 화면**(설계 Tier C · P4 — `mdir`·`p4changes` 등).
    ///
    /// 위 [`Screen::Plugins`] 와 이름이 비슷하지만 정반대다: 저쪽은 플러그인을 켜고 끄는
    /// **우리** 화면이고, 이쪽은 플러그인이 **무엇을 그릴지 준** 판이다. 내용·제목·키는
    /// 전부 서버가 준 스펙에 있고, 여기서는 "어떻게 움직이나"만 안다.
    PluginView,
    /// 선택지가 정해진 인자를 고르는 화면(파이썬 `CommandOptionsScreen` · 패리티 G8v).
    ///
    /// 다른 목록형과 갈리는 점 둘 — **↑↓ 는 줄을, ←→ 는 값을** 옮긴다(고르는 것이 줄이
    /// 아니라 값이다). 그리고 무엇을 고르고 있는지 화면 아래에 **만들어지는 명령줄**이
    /// 그대로 보인다.
    Options,
    /// 걸어 둔 이벤트 훅 목록(`show-hooks`). **읽는 화면**이라 아무 키나 닫는다.
    ///
    /// 파이썬은 범용 `InfoScreen` 에 `title="hooks"` 로 띄운다 — 우리는 화면 종류가
    /// 제목·키 안내를 들고 있으므로 한 갈래를 준다.
    Hooks,
    /// 탭으로 나뉜 **읽기 전용** 정보 팝업(파이썬 `InfoTabsScreen`).
    ///
    /// 다른 읽는 화면과 갈리는 점 하나 — **←→ 가 탭을 바꾼다**. 그래서 "아무 키나 닫기"가
    /// 아니라 방향키 넷이 자기 것이다.
    ///
    /// 파이썬은 상태줄의 두 버튼(REC 캡처·토큰 사용량)이 **한 팝업**을 열되 서로 다른 탭을
    /// 펴도록 통합한 것이다. 그 두 탭은 플러그인이 채우므로 우리에게는 없고, 코어가 늘
    /// 갖는 **서버** 탭이 우리 것의 첫 탭이다.
    InfoTabs,
    /// 여러 줄 작성창(파이썬 `ComposePromptScreen` · `esc Insert`).
    ///
    /// 다른 화면과 갈리는 점 둘 — **모든 키가 이 화면의 것**이다(수정키 조합도 · `Esc` 도).
    /// 그리고 닫는 방법이 둘이라 뜻이 다르다: `Enter` 는 쓴 것을 패널에 넣고, `Esc` `Esc`
    /// 는 안 넣는다(그래도 **초안은 남는다**). 편집 규칙은 [`crate::editor`] 가 든다.
    Compose,
    /// 설정(파이썬의 `:settings`). 지금 값을 보이고 **그 자리에서** 바꾼다.
    ///
    /// 다른 목록형과 갈리는 점 하나 — `Enter` 가 **화면을 안 닫는다**. 설정은 보통 두세
    /// 개를 연달아 바꾸고, 한 번에 하나씩 닫히면 그때마다 다시 열어야 한다. 그리고 값이
    /// 바뀐 것을 **같은 화면에서 확인**하는 것이 이 화면의 존재 이유다.
    Settings,
    /// 블록·Claude **요약 판**(§10-21ⓓ — 종전엔 화면 아래에 늘 붙어 있던 구역).
    ///
    /// 화면에서 뺀 이유는 제보 그대로다: *"이 판은 GUI 에만 있고 pytmux 사용에 직접적인
    /// 영향을 주지 않으므로, 화면에서 빼고 별도 명령어나 메뉴로 접근하게 한다."*
    /// 훑는 용도의 요약이 화면의 주인공(패널)을 밀어내던 자리다.
    Summary,
    /// 전역 검색 결과(파이썬 `SearchResultsScreen` · `search_all` 회신 — pytmux-27).
    ///
    /// 목록형이다 — 탭·패널·줄·미리보기 한 줄이 히트 하나. `Enter` 로 고르면 그 탭·
    /// 패널·스크롤로 뛴다(`search_goto`). 상한·누락 상류 안내는 **판 안**에 둔다(테두리
    /// 제목은 넘치면 조용히 잘려 no-silent-caps 를 어긴다 — 정본 `notes_text()` 와 같은 자리).
    SearchResults,
    /// **자동 재개 설명 + 켜고 끄기 판**(pytmux-183 · 정본 `open_autoresume_info`).
    ///
    /// 좌하단 `[자동재개]` 표식을 눌러 연다. 정본과 같은 `InfoScreen` 계열이고 손도
    /// 같다 — 설명을 읽고 `a` 로 뒤집으면 닫힌다.
    ///
    /// ⛔ **누르자마자 뒤집지 않는 이유**가 정본에 있다: 자동재개는 「모르고 켜 두면
    /// 자리를 비운 사이에 대화가 이어지는」 상태라, 클릭 한 번에 뒤집히면 이번엔
    /// **모르고 꺼 버리는** 자리가 생긴다. 그래서 판이 한 번 선다.
    Autoresume,
}

impl Screen {
    /// **전수 목록** — 화면 하나도 빠지지 않는다.
    ///
    /// 오라클용이다(`base::keymap::all_actions` 와 같은 자리·같은 이유): "이 화면을 열
    /// 길이 있나"를 재려면 목록이 있어야 하고, 목록을 크레이트마다 다시 적으면 그
    /// 목록들이 서로 다르게 낡는다.
    ///
    /// 빠짐은 **컴파일러가 막는다** — 아래 `match` 에 와일드카드가 없으므로 변형을
    /// 더하면 여기가 안 컴파일된다(그때 배열에도 더하게 된다).
    pub fn all() -> &'static [Screen] {
        // 이 match 는 값을 쓰려는 것이 아니라 **빠짐을 막으려고** 있다.
        const fn exhaustive(screen: Screen) -> usize {
            match screen {
                Screen::Keys => 0,
                Screen::ClaudeDetail => 1,
                Screen::Tabs => 2,
                Screen::Tree => 3,
                Screen::Buffers => 4,
                Screen::Prompt => 5,
                Screen::Confirm => 6,
                Screen::Commands => 7,
                Screen::Version => 8,
                Screen::ShellOutput => 9,
                Screen::RestartCheck => 10,
                Screen::Autoresume => 24,
                Screen::MergeRemote => 11,
                Screen::Layouts => 12,
                Screen::Notices => 13,
                Screen::Menu => 14,
                Screen::Plugins => 15,
                Screen::PluginView => 16,
                Screen::Options => 17,
                Screen::Hooks => 18,
                Screen::InfoTabs => 19,
                Screen::Compose => 20,
                Screen::Settings => 21,
                Screen::Summary => 22,
                Screen::SearchResults => 23,
            }
        }
        const ALL: &[Screen] = &[
            Screen::Keys,
            Screen::ClaudeDetail,
            Screen::Tabs,
            Screen::Tree,
            Screen::Buffers,
            Screen::Prompt,
            Screen::Confirm,
            Screen::Commands,
            Screen::Version,
            Screen::ShellOutput,
            Screen::RestartCheck,
            Screen::MergeRemote,
            Screen::Layouts,
            Screen::Notices,
            Screen::Menu,
            Screen::Plugins,
            Screen::PluginView,
            Screen::Options,
            Screen::Hooks,
            Screen::InfoTabs,
            Screen::Compose,
            Screen::Settings,
            Screen::Summary,
            Screen::SearchResults,
            Screen::Autoresume,
        ];
        // 중복·자리 어긋남은 여기서 잡는다(빠짐은 위 match 가 이미 막았다).
        debug_assert!(
            ALL.iter().enumerate().all(|(i, s)| exhaustive(*s) == i),
            "Screen::all() 의 차례가 어긋났다 — 전수 목록이 아니다"
        );
        ALL
    }

    /// 화면 머리에 붙는 제목. 이름을 뷰가 지으면 같은 화면이 화면마다 달라 보인다.
    pub fn title(self) -> &'static str {
        crate::i18n::t(match self {
            Screen::Keys => "키 도움말",
            Screen::ClaudeDetail => "플랜 · 거부",
            Screen::Tabs => "탭 전환",
            Screen::Tree => "트리 (개요)",
            Screen::Buffers => "버퍼 선택",
            Screen::Prompt => "입력",
            Screen::Confirm => "확인",
            Screen::Commands => "명령",
            Screen::Settings => "설정",
            Screen::Plugins => "플러그인",
            // 제목의 주인은 **스펙**이다(플러그인이 정한다) — 뷰가 그것으로 덮어 그린다.
            // 여기 값은 스펙이 제목을 안 줬을 때의 폴백이다.
            Screen::PluginView => "플러그인 화면",
            Screen::Menu => "메뉴",
            Screen::Notices => "알림 이력",
            Screen::Hooks => "훅",
            Screen::Options => "인자",
            Screen::Layouts => "레이아웃",
            Screen::Version => "버전",
            Screen::RestartCheck => "재시작 점검",
            Screen::Autoresume => "자동 재개",
            Screen::ShellOutput => "셸 결과",
            Screen::MergeRemote => "원격 탭 머지",
            // 파이썬 `compose.title` 과 같은 문구다.
            Screen::Compose => "프롬프트 작성 (블록 선택 편집)",
            // 파이썬 `dialog.status_title` 과 같은 자리다.
            Screen::InfoTabs => "상태",
            // §10-21ⓓ — 종전 화면 아래 구역의 머리줄이 하던 말을 판 제목이 한다.
            Screen::Summary => "블록 · Claude 요약",
            // 진짜 제목(쿼리+건수)은 **회신이 준다** — 이건 회신 오기 전·못 받았을
            // 때의 폴백이다(PluginView 와 같은 자리).
            Screen::SearchResults => "전역 검색",
        })
    }

    /// 이 화면이 **어디에 서는가**(세로) — 정본 CSS `align` 을 그대로 옮긴 것.
    ///
    /// # 왜 화면마다 다른가 (정본이 정한 것)
    ///
    /// | 앵커 | 화면 | 왜 |
    /// |---|---|---|
    /// | [`Bottom`](Anchor::Bottom) | `:` 프롬프트 · 팔레트 · 작성창 | **치던 흐름의 연장**이다. 손과 눈이 방금 `:` 를 친 화면 아래에 있는데 판이 가운데나 위에 뜨면 시선이 한 번 튄다 |
    /// | [`Top`](Anchor::Top) | 읽는 판(훅·셸 결과·키 도움말) | 긴 글이라 **위에서 시작해야** 첫 줄이 늘 같은 자리다(정본 `InfoScreen`) |
    /// | [`Middle`](Anchor::Middle) | 나머지 — **고르러 여는** 판, 그리고 **짧은 읽는 판**(버전·재시작 점검) | 목록이 짧으면 판도 작고, 가운데가 눈의 기본 자리다 |
    ///
    /// # 읽는 판인데 가운데인 예외 둘 — 버전·재시작 점검(§10-21ⓐ3·ⓓ3)
    ///
    /// 위 표의 "긴 글이라 위에서 시작"은 **긴 글일 때만** 근거다. 이 둘은 길이가 정해져
    /// 있어(다섯 줄·열 줄 남짓) 위에 붙이면 화면 대부분이 빈 채로 남는다. 그래서
    /// **정본이 이 둘을 예외로 둔다** — `InfoScreen(..., center=True)`(`clientconn.py`
    /// 의 `_show_version_popup`·`_show_restart_check_popup`). 우리가 위에 세운 것은 그
    /// 예외를 안 옮긴 것이었다.
    ///
    /// 정본의 이 예외는 CSS 가 아니라 **호출 인자**라, 클래스 CSS 만 읽던 픽스처에는
    /// 안 잡혔다 — 그래서 잰 적이 없었다(`gen_screen_anchor_fixture.py` 의 `overrides`
    /// 가 이제 그 인자까지 뽑는다).
    ///
    /// # 왜 core 에 두나
    ///
    /// 뷰가 각자 정하면 **같은 화면이 클라마다 다른 데 뜬다** — 실제로 그랬다(2026-08-01
    /// 사용자 지시: "자잘한 레이아웃은 의도를 가지고 튜닝되어 있다"). 정본 값과의 대조는
    /// `screen_anchor_conformance.rs` 가 픽스처로 잰다.
    pub fn anchor(self) -> Anchor {
        match self {
            // 치던 흐름의 연장 — 정본 `align: center bottom`.
            Screen::Prompt | Screen::Commands | Screen::Compose => Anchor::Bottom,
            // 읽는 판 — 정본은 전부 범용 `InfoScreen`(`align: center top`)으로 띄운다.
            // 우리는 화면 종류가 제목·키 안내를 들고 있어 갈래를 나눴을 뿐, 자리는 같다.
            Screen::Keys
            | Screen::Hooks
            | Screen::ShellOutput
            // 자동 재개 판도 같은 자리다 — 정본이 `center=True` 없이 띄우므로 클래스
            // 기본(`align: center top`)을 그대로 탄다(pytmux-183 · 앵커 픽스처가
            // 처음의 `middle` 추측을 잡아 줬다).
            | Screen::Autoresume
            // 네이티브 전용(정본에 짝이 없다) — 플랜 전문·거부 사유도 **읽는 판**이라
            // 같은 관습을 따른다.
            | Screen::ClaudeDetail => Anchor::Top,
            // 읽는 판인데 **짧아서** 가운데인 예외 — 정본도 이 둘만 `center=True` 다
            // (위 「예외」 · §10-21ⓐ3·ⓓ3). 재시작 점검은 그 위에 **고르는 판**이기도
            // 하다(단추가 있다) — 가운데가 두 번 맞는 자리다.
            Screen::Version | Screen::RestartCheck => Anchor::Middle,
            // 고르러 여는 판.
            Screen::Tabs
            | Screen::Tree
            | Screen::Buffers
            | Screen::Confirm
            | Screen::MergeRemote
            | Screen::Layouts
            | Screen::Notices
            | Screen::Menu
            | Screen::Plugins
            | Screen::Options
            | Screen::InfoTabs
            // 플러그인이 준 판도 **고르러 여는 판**이다(목록이든 글이든 그 흐름의 안이다).
            | Screen::PluginView
            | Screen::Settings
            // 요약은 **훑는 판**이다 — 목록이라 고르러 여는 판과 같은 자리가 맞다.
            | Screen::Summary
            // 검색 결과도 **고르러 여는 판**이다(목록 → Enter 로 그 자리로).
            | Screen::SearchResults => Anchor::Middle,
        }
    }

    /// 이 판이 화면 세로의 **몇 분의 몇**을 쓰나(§10-21 ⓗ·ⓢ·ⓥ·ⓐ2·ⓚ2).
    ///
    /// # 왜 이 값이 생겼나 — 제보 다섯이 한 이야기였다
    ///
    /// | 제보 | 증상 |
    /// |---|---|
    /// | ⓗ | 팔레트가 상하 전체를 쓴다 · 분류를 옮기면 높이가 변한다 |
    /// | ⓢ | 설정 판도 화면 전체 높이를 쓴다 |
    /// | ⓥ | 알림 이력을 굴리면 **판 크기가 변한다** |
    /// | ⓐ2⑴ | 상태 판이 고른 탭에 따라 커졌다 작아졌다 |
    /// | ⓚ2 | `p4changes` 판이 화면을 통째로 가린다 |
    ///
    /// 다섯 다 뿌리가 하나다: **판의 기하를 내용이 정한다**. 그래서 개별 처방이 아니라
    /// 규칙 하나를 둔다 — 높이는 여기가 정하고, 모자란 줄은 빈 자리로 두고, 넘치면
    /// 안에서 스크롤한다.
    ///
    /// # 왜 판마다 다른가
    ///
    /// ⓗ 는 팔레트를 **절반**이라고 못박았고, ⓢ 는 설정에 대해 "전체는 아니다"까지만
    /// 왔다. 그래서 공통 상한을 낮추되(2/3) 팔레트만 그 말대로 절반이다 — 기록이
    /// *"공통 상한을 낮추되 판별로 덮어쓸 수 있는 모양이 안전하다"* 고 적은 그대로다.
    pub fn height_ratio(self) -> (usize, usize) {
        match self {
            // 제보가 "높이는 화면의 절반"이라고 못박았다.
            Screen::Commands => (1, 2),
            _ => (2, 3),
        }
    }

    /// 프롬프트에서 **후보 목록이 입력 줄 위에** 오는가(정본 `#pcand` → `#prow` 차례).
    ///
    /// 정본이 이 차례를 컨테이너 흐름으로 못박은 이유: 둘 다 바닥 고정이라 적층 순서가
    /// Textual 버전에 따라 뒤집혔고, 그러면 **모바일에서 후보가 키보드에 가렸다**
    /// (사용자 요청). 자리(바닥)만 맞추고 차례가 뒤집히면 같은 자리에 다른 화면이 선다.
    ///
    /// 값이 상수인데 함수인 이유: 뷰 둘이 **같은 곳을 보게** 하려는 것이다. 각자 상수를
    /// 적으면 한쪽만 뒤집혀도 아무 소리가 안 난다.
    pub fn candidates_above_input(self) -> bool {
        true
    }

    /// 이 판이 **뒤를 가라앉히나**(딤 스크림) — pytmux-370.
    ///
    /// # 왜 core 가 드나
    ///
    /// 자리표(`anchor`)와 **같은 결**이다: 화면마다 다르고, 뷰가 각자 정하면 같은 화면이
    /// 클라마다 다르게 보인다. 종전 GUI 는 `screens.top()` 이 무엇이든 창 전체를 덮었다 —
    /// 화면별 갈래가 아예 없었다.
    ///
    /// # 작성창만 `false` 인 이유
    ///
    /// 정본은 작성창에서 아무것도 어둡게 하지 않는다. **위에 보이는 것을 보면서 쓰는
    /// 자리**이기 때문이다 — 뒤 글이 안 읽히면 그 화면의 값이 절반 사라진다.
    /// 제보(2026-08-23)가 첨부 다섯 장으로 그 차이를 보였다.
    ///
    /// ⚠ 나머지는 종전대로 `true` 다. 제보는 작성창 하나를 말했고, 딤을 모든 판에서
    /// 걷을지는 **이 이슈 밖**이다 — 자료로 두었으니 한 번에 한 화면씩 옮길 수 있다.
    pub fn dims_behind(self) -> bool {
        !matches!(self, Screen::Compose)
    }

    /// 정본에서 이 화면에 대응하는 **클래스 이름**. 네이티브 전용이면 `None`.
    ///
    /// 앵커 적합성 테스트가 이것으로 픽스처를 찾는다. 여럿이 한 정본 화면에 대응하는
    /// 것은 정상이다(읽는 판 다섯이 전부 `InfoScreen` 이다) — 정본은 범용 한 장에
    /// 제목만 갈아 끼우고, 우리는 화면 종류가 제목·키 안내를 들고 있다.
    /// 정본에서 이 판을 띄우는 **호출의 이름** — 클래스 CSS 를 덮어쓰는 인자가 있는
    /// 자리에만 있다(§10-21ⓐ3).
    ///
    /// 정본의 범용 `InfoScreen` 은 CSS 로 `align: center top` 인데, **호출이
    /// `center=True` 로 그것을 뒤집는 자리**가 있다. 클래스 CSS 만 읽던 픽스처는 그
    /// 예외를 통째로 못 봤고, 그래서 우리 버전 판이 정본과 다른 자리에 서는 것을 아무도
    /// 안 쟀다. 이름은 그 호출의 `title=` 인자다 — 정본에서 그 판을 가리키는 유일한 이름이다.
    pub fn canon_variant(self) -> Option<&'static str> {
        match self {
            Screen::Version => Some("version"),
            // 정본은 이 판의 제목을 카탈로그 키로 짓는다 — 그 키가 곧 이름이다.
            Screen::RestartCheck => Some("restartcheck.title"),
            // ⚠ 자동 재개 판은 **여기 없다**(pytmux-183). 정본
            // `open_autoresume_info` 는 `InfoScreen(..., title=t("ar.title"))` 을
            // **`center=True` 없이** 부르므로 클래스 CSS 기본(`align: center top`)을
            // 그대로 탄다 — 덮어쓰는 자리가 아니라 이 표에 이름을 적을 것이 없다.
            // (처음엔 `middle` 로 적었다가 앵커 픽스처가 잡았다.)
            _ => None,
        }
    }

    pub fn canon_class(self) -> Option<&'static str> {
        Some(match self {
            Screen::Keys
            | Screen::Hooks
            | Screen::Version
            | Screen::ShellOutput
            | Screen::RestartCheck
            // 정본도 `InfoScreen` 을 쓴다 — `hide_key="a"` 로 뒤집고 닫는 그 판이다
            // (`clientconn.py::open_autoresume_info`).
            | Screen::Autoresume => "InfoScreen",
            // 정본에 짝이 없다 — 이 구역은 GUI 만 갖고 있던 것이다(§10-21ⓓ).
            Screen::Summary => return None,
            Screen::Tabs => "TabSwitcherScreen",
            Screen::Tree => "ChooseTreeScreen",
            Screen::Buffers => "ChooseBufferScreen",
            Screen::Prompt => "PromptScreen",
            Screen::Confirm => "ConfirmScreen",
            Screen::Commands => "CommandListScreen",
            Screen::MergeRemote => "MergeRemoteTabScreen",
            Screen::Layouts => "ChooseLayoutScreen",
            Screen::Notices => "NoticeHistoryScreen",
            Screen::Menu => "MenuScreen",
            Screen::Plugins => "PluginManagerScreen",
            // 정본의 짝은 그 플러그인의 Textual 화면이라 이름이 하나로 안 정해진다.
            Screen::PluginView => return None,
            Screen::Options => "CommandOptionsScreen",
            Screen::InfoTabs => "InfoTabsScreen",
            Screen::Compose => "ComposePromptScreen",
            Screen::Settings => "SettingsScreen",
            // 정본에 짝이 없다 — Claude 플랜 전문·거부 사유는 네이티브가 만든 판이다.
            Screen::ClaudeDetail => return None,
            Screen::SearchResults => "SearchResultsScreen",
        })
    }

    /// 머리줄에 붙는 **키 안내**. 화면 종류마다 키가 다르므로 여기서 갈라 준다.
    ///
    /// 뷰가 각자 적으면 한쪽 안내만 낡는다 — 그리고 안내가 틀리면 도움말이 없느니만
    /// 못하다(2026-07-29: 목록 화면에 "아무 키나 닫기"라고 적혀 있었는데 실제로는
    /// `Enter` 가 확정이었다).
    pub fn hint(self) -> &'static str {
        crate::i18n::t(match self {
            Screen::Keys | Screen::ClaudeDetail => "(아무 키나 닫기 · ↑↓ 스크롤)",
            Screen::Tabs => "(Tab/↑↓ 고르기 · Enter 전환 · Esc 취소)",
            Screen::Tree => "(↑↓ 고르기 · Enter 이동 · Esc 취소)",
            Screen::Buffers => "(↑↓ 고르기 · Enter 붙여넣기 · Esc 취소)",
            Screen::Prompt => "(Enter 확정 · Esc 취소)",
            // 정본과 같은 안내다(`↔ 이동 · Enter 확정 · y/n · Esc 취소`). Enter 가
            // **고른 버튼**을 누르는 것이라, 안내도 "Enter 예"라고 적으면 안 된다.
            Screen::Confirm => "(←→ 고르기 · Enter 확정 · y/n · Esc 취소)",
            Screen::Commands => "(치면 좁혀진다 · ←→ 분류 · ↑↓ 고르기 · Enter 실행 · Esc 취소)",
            Screen::Settings => "(↑↓ 고르기 · ←→ 값 · Tab 카테고리 · Enter 바꾸기 · Esc 닫기)",
            // Space 도 받는 것은 파이썬 클라와 같다 — 체크박스 목록의 손버릇이다.
            Screen::Plugins => "(↑↓ 고르기 · Enter/Space 켜고끄기 · Esc 닫기)",
            // 안내도 스펙이 준다(플러그인이 자기 키를 안다) — 이건 폴백이다.
            Screen::PluginView => "(↑↓ 이동 · Enter 실행 · Esc 닫기)",
            Screen::Menu => "(↑↓ 고르기 · → 하위 · ← 뒤로 · Enter 실행 · Esc 취소)",
            Screen::Notices | Screen::Hooks => "(아무 키나 닫기 · ↑↓ 스크롤)",
            Screen::Layouts => "(↑↓ 고르기 · Enter 적용 · Esc 취소)",
            Screen::Options => "(↑↓ 줄 · ←→ 값 · Enter 실행 · Esc 취소)",
            Screen::Version => "(아무 키나 닫기)",
            Screen::RestartCheck => "(아무 키나 닫기 · ↑↓ 스크롤)",
            // 정본과 같은 손이다: `a` 가 뒤집고 닫는다.
            Screen::Autoresume => "(a 켜고 끄기 · Esc 닫기)",
            Screen::ShellOutput => "(아무 키나 닫기 · ↑↓ 스크롤)",
            Screen::MergeRemote => "(↑↓ 고르기 · h/v 방향 · Enter 합치기 · Esc 취소)",
            // 파이썬 `compose.hint`·`compose.hint_esc` 와 같은 문구다. `Esc` 를 누른
            // 뒤에는 **안내가 바뀌어야** 한다 — 종전 파이썬이 문구만 바꾸고 색을 안
            // 바꿨을 때 "esc 를 눌러도 모드 전환을 알 수 없다"는 보고가 왔다.
            Screen::Compose => {
                "(Enter 전송 · Shift+Enter 줄바꿈 · Esc 메뉴 · Shift+방향키/Ctrl+A 선택)"
            }
            Screen::InfoTabs => "(←→ 탭 · ↑↓ 스크롤 · Esc 닫기)",
            Screen::Summary => "(아무 키나 닫기 · ↑↓ 스크롤)",
            // 진짜 안내(상한·누락 상류)는 회신이 준다 — 이건 회신 오기 전의 폴백.
            Screen::SearchResults => "(↑↓ 고르기 · Enter 이동 · Esc 취소)",
        })
    }

    /// `Esc` 메뉴 모드일 때의 안내(파이썬 `compose.hint_esc`).
    ///
    /// ⚠ 아래 셋은 const 라 여기서 [`crate::i18n::t`] 를 못 부른다 — **그리는 쪽이
    /// `t(...)` 로 감싼다**(번역은 `i18n/en_core.rs` 에 있다).
    pub const COMPOSE_HINT_ESC: &'static str =
        "ESC 메뉴 — : 명령 · Esc 취소 · 그 외 키 편집 복귀";

    /// `Esc` 메뉴 모드 배지(파이썬 `compose.esc_badge`).
    pub const COMPOSE_ESC_BADGE: &'static str = "ESC 모드";

    /// 빈 채로 보내려 했을 때의 알림(파이썬 `compose.empty`).
    pub const COMPOSE_EMPTY: &'static str = "(빈 내용 — 투입할 것이 없습니다)";
}

/// 키 하나를 화면이 어떻게 처리했나.
///
/// `Copy` 가 아닌 이유: 대답([`ScreenKey::Answered`])이 문자열을 들고 온다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScreenKey {
    /// 화면이 먹었다(스크롤 등). 패널로 보내지 않는다.
    Consumed,
    /// 이 키로 화면이 닫혔다. 역시 패널로 보내지 않는다 — **닫는 키가 패널에도 가면**
    /// 화면을 닫으려던 `q` 가 셸에 찍힌다.
    Closed,
    /// 물었던 것에 **대답이 나왔다**(그리고 화면은 닫혔다).
    ///
    /// 확인 화면의 대답은 `"y"` 하나뿐이다 — '아니오'는 [`ScreenKey::Closed`] 로 온다
    /// (아무 일도 안 일어나는 것이 곧 아니오다).
    Answered(Prompt, String),
    /// 목록에서 **고른** 것이 있다(그리고 화면은 닫혔다). 값은 목록 안 위치다.
    ///
    /// 무엇을 고른 것인지(탭 index·버퍼 번호…)는 **뷰가 안다** — core 는 목록의 내용을
    /// 모른다. 이 경계 덕에 목록형 화면이 늘어도 core 는 그대로다.
    Chosen(usize),
    /// 작성창에서 다 썼다 — 이 글을 패널에 넣는다(그리고 화면은 닫혔다).
    ///
    /// [`ScreenKey::Answered`] 와 갈라 둔 이유: 저쪽은 **한 줄**이고 무엇을 물었는지를
    /// 같이 든다. 이건 여러 줄이고 물음이 없다 — 패널에 그대로 들어갈 본문이다.
    Injected(String),
    /// 트리에서 `d`/`x` 로 **그 줄을 닫겠다**고 했다(화면은 닫혔다 — 파이썬
    /// `ChooseTreeScreen.on_key` 와 같은 손). 값은 목록 안 위치 — 그 줄이 탭인지
    /// 패널인지는 뷰가 안다([`ScreenKey::Chosen`] 과 같은 경계). 뷰는 파이썬처럼
    /// **먼저 그 자리로 옮기고** 확인 화면을 세운다(확인 없는 닫기는 없다).
    TreeKill(usize),
    /// 골랐고 **화면은 그대로 있다**(설정 화면).
    ///
    /// [`ScreenKey::Chosen`] 과 나눠 둔 이유: 뷰가 이 둘을 같게 다루면 설정을 하나 바꿀
    /// 때마다 화면이 닫힌다. 값이 바뀐 것을 같은 화면에서 보는 것이 설정 화면의 요점이다.
    Applied(usize),
    /// 설정 화면에서 그 줄의 값을 **방향과 함께** 바꿨다(`←`/`→`).
    ///
    /// [`ScreenKey::Applied`] 와 갈라 둔 이유: 저쪽은 "골랐다"(Enter)라 방향이 없다.
    /// 한 값에 방향을 얹으면 목록형 화면들이 쓰는 `Applied` 의 뜻이 흐려진다.
    AppliedDir(usize, bool),
}

/// 무엇을 묻고 있나. **대답을 어디에 쓸지**가 이 값에 달렸다.
///
/// core 는 대답을 명령으로 옮기지 않는다(서버 어휘를 모른다) — 뷰가 이 값을 보고 옮긴다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Prompt {
    /// 지금 탭의 새 이름(`prefix ,`).
    RenameTab,
    /// 지금 탭을 옮길 자리(`prefix .`).
    MoveTab,
    /// 지금 패널을 닫을까(`prefix x`).
    KillPane,
    /// 지금 탭을 닫을까(`prefix &`).
    KillTab,
    /// 활성 패널의 새 제목(`prefix T`).
    RenamePane,
    /// 지금 탭과 맞바꿀 자리(`swap-tab`).
    SwapTab,
    /// **플러그인이 물은 것**(설계 Tier C · P5 — `prompt`·`confirm` 스펙).
    ///
    /// 왜 전용 화면을 안 만드나: 물음·확인은 이 클라가 이미 잘하는 일이다(입력 이력·
    /// 버튼 두 개·기본이 '아니오'). 플러그인이 물었다고 그 화면을 한 벌 더 만들면
    /// **되돌릴 수 없는 것 앞의 규칙**이 두 곳에 생긴다.
    PluginAsk,
    /// 패널 출력을 흘릴 명령(`pipe-pane`). **빈 대답은 끄기**다.
    PipePane,
    /// 합칠 패널이 있는 탭 번호(`join-pane`).
    JoinPane,
    /// 상태줄에 띄울 한 줄(`display-message`).
    DisplayMessage,
    /// 새 탭·분할이 시작할 자리(설정 `default-path`).
    DefaultPath,
    /// `esc c` 가 새 탭에서 실행할 명령(설정 `claude-command`). **빈 대답도 값이다** —
    /// 그때는 그냥 셸 탭이 열린다(`default-path` 와 달리 되돌리기가 아니다).
    ClaudeCommand,
    /// 상태줄 왼쪽/오른쪽 **형식 문자열**(`#S`·`%H:%M` — `proto::status`).
    StatusLeft,
    StatusRight,
    /// 상태줄 색 이름. 빈 값이면 테마 그대로다.
    StatusBg,
    StatusFg,
    /// 커서 색 이름(`pytmux/pytmux-161`). 상태줄 색과 **같은 표기**이고, 빈 값이면
    /// 테마 그대로다.
    CursorColor,
    /// `set <옵션> <값>` 한 줄.
    SetOption,
    /// `<이벤트> <명령>` 또는 `-u <이벤트>` — 이벤트 훅 걸기(`set-hook`).
    SetHook,
    /// 패널에 주입할 키들(`send-keys`).
    SendKeys,
    /// 팝업에서 띄울 명령(`display-popup`). 비우면 셸이다.
    DisplayPopup,
    /// 돌릴 셸 명령(`run-shell`).
    RunShell,
    /// `<조건> <명령>` — 조건이 성공하면 그 명령(`if-shell`).
    IfShell,
    /// `[-n] <키> <명령>` — 키에 명령 걸기.
    BindKey,
    /// 풀 키(`[-n] <키>`).
    UnbindKey,
    /// 저장할 배치 이름(`layout-save`).
    SaveTabLayout,
    /// 불러올 배치 이름 — 현재 탭을 덮어쓴다(`layout-load`).
    LoadTabLayout,
    /// 불러올 배치 이름 — **새 탭에** 연다(`layout-load-new`).
    LoadTabLayoutNew,
    /// 새 prefix 키(설정 화면). 대답은 tmux 표기(`C-a`)다.
    SetPrefix,
    /// 서버를 끝낼까(`kill-server`). **이 저장소에서 가장 비싼 확인**이다 — 이 하나가
    /// 다른 사람이 쓰고 있는 탭까지 통째로 내린다.
    KillServer,
    /// 서버를 재시작할까(`restart-server`). 셸은 살지만 화면이 끊겼다 돌아온다.
    RestartServer,
    /// 서버와 이 클라를 함께 재시작할까(`restart-all`).
    RestartAll,
    /// 어느 상자에 붙을까(`remote-attach`).
    RemoteAttach,
    /// 어느 상자에 새 셸을 띄울까(`remote-new-tab`).
    RemoteNewTab,
    /// 어느 원격을 뗄까(`remote-detach` — 비우면 전부).
    RemoteDetach,
    /// 스크롤백에서 찾을 글(`search` — 스크롤 모드 `/`). 위(과거) 방향부터 찾는다.
    SearchScrollback,
    /// 모든 로컬+원격 탭·패널에서 찾을 글(`search_all` — `esc f`·메뉴 · pytmux-27).
    SearchAll,
    /// **마지막 로컬 탭**을 닫는다 — 이걸 닫으면 서버가 통째로 끝난다.
    KillTabLast,
    /// 마지막 로컬 탭인데 **원격 탭도 열려 있다** — 그 보기까지 끊긴다.
    KillTabLastRemote,
    /// **고정 탭**을 닫는다(고정은 "상시 유지" 의도라 한 단계 더 막는다).
    KillTabPinned,
}

/// 탭 닫기 확인이 알아야 하는 사실들. 뷰가 채운다 — core 는 탭 목록을 모른다
/// (`chrome::ChromeCtx`·`MenuToggles` 와 같은 갈래).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct TabFacts {
    /// **로컬** 탭 수. 원격(페더레이션) 탭은 세지 않는다 — 마지막 로컬 탭을 닫으면
    /// 서버 세션이 비어 앱이 통째로 끝나는데, 전체 수로 세면 원격 탭이 함께 열려 있을 때
    /// 그 경고가 빠진다(정본이 실제로 그 결함을 고쳤다).
    pub local: usize,
    pub has_remote: bool,
    pub active_pinned: bool,
}

impl Prompt {
    /// 지금 상황에 맞는 「탭 닫기」 물음(정본 `client.py` 의 네 갈래와 같은 판정).
    ///
    /// 판정을 core 에 두는 이유는 늘 같다 — 두 뷰가 각자 갈래를 세면 한쪽 클라만
    /// 경고를 빠뜨린다. 그리고 **빠뜨리는 쪽이 하필 되돌릴 수 없는 자리**다.
    pub fn kill_tab(facts: &TabFacts) -> Prompt {
        match (facts.local <= 1, facts.has_remote, facts.active_pinned) {
            (true, true, _) => Prompt::KillTabLastRemote,
            (true, false, _) => Prompt::KillTabLast,
            (false, _, true) => Prompt::KillTabPinned,
            _ => Prompt::KillTab,
        }
    }

    /// 확인 판의 **제목**(정본은 마지막 탭이면 `pytmux 종료` 로 바꾼다). 없으면 화면의
    /// 기본 제목(`확인`)을 쓴다.
    ///
    /// 제목을 바꾸는 것이 장식이 아닌 이유: 판을 여는 순간 **가장 먼저 읽히는 글**이
    /// 제목이다. 거기 "pytmux 종료" 가 있으면 본문을 다 읽기 전에 손이 멈춘다.
    pub fn confirm_title(self) -> Option<&'static str> {
        Some(crate::i18n::t(match self {
            Prompt::KillTabLast | Prompt::KillTabLastRemote => "pytmux 종료",
            Prompt::KillTabPinned => "고정 탭 닫기",
            Prompt::KillTab => "탭 닫기",
            Prompt::KillPane => "패널 닫기",
            Prompt::KillServer => "서버 종료",
            Prompt::RestartAll => "재시작 확인",
            _ => return None,
        }))
    }

    /// 확인 버튼 두 낱말 `[긍정, 부정]`.
    ///
    /// 왜 동사인가: `예`/`아니오` 는 물음을 다시 읽어야 무엇에 답하는지 안다. 동사는
    /// 버튼만 보고도 무슨 일이 나는지 읽힌다 — 되돌릴 수 없는 판에서 그 차이가 크다.
    /// 정본도 같다(`ConfirmScreen` 기본 `닫기`/`취소`, 호출부가 더 센 말을 덮어쓴다).
    pub fn confirm_labels(self) -> [&'static str; 2] {
        let yes = match self {
            // 정본 `dialog.kill_server_yes` — "닫기"로는 **무엇이** 닫히는지 안 보인다.
            Prompt::KillServer => "종료",
            Prompt::RestartAll => "재시작",
            _ => "닫기",
        };
        [crate::i18n::t(yes), crate::i18n::t("취소")]
    }

    /// 이 물음이 **되돌릴 수 없는가**(정본 `confirm_popup(..., danger=…)`).
    ///
    /// 정본은 이때 고른 버튼을 `$error`(붉은색)로 칠한다. 문구가 아니라 **색**인 이유:
    /// 판을 여는 사람은 본문을 다 읽지 않는다 — 그래서 정본은 제목을 바꾸고(우리도
    /// 그렇게 했다) 그 위에 색을 한 겹 더 얹는다. 두 신호가 같은 것을 말한다.
    ///
    /// ⚠ **아무 데나 붉게 칠하면 붉은색이 값을 잃는다.** 평범한 탭 닫기·패널 닫기는
    /// 되돌릴 수 있는 축(셸 하나)이라 정본도 안 칠한다 — 그 경계를 그대로 옮긴다.
    pub fn is_dangerous(self) -> bool {
        matches!(
            self,
            // 마지막 로컬 탭 = 앱이 통째로 끝난다 · 고정 탭 = "상시 유지" 의도를 깬다.
            Prompt::KillTabLast
                | Prompt::KillTabLastRemote
                | Prompt::KillTabPinned
                // 서버와 **모든** 탭·셸이 내려간다.
                | Prompt::KillServer
                // 서버를 내렸다 올린다 — 붙어 있는 다른 클라도 함께 끊긴다.
                | Prompt::RestartAll
        )
    }

    /// 물음 안의 `{name}` 슬롯을 [`Screens::detail`] 로 채우는 물음인가.
    ///
    /// 채우는 물음은 detail 을 **따로 줄로 보이면 안 된다** — 같은 낱말이 두 번 뜬다.
    pub fn detail_fills_a_slot(self) -> bool {
        matches!(self, Prompt::KillTabPinned)
    }

    /// 화면에 적을 물음. 뷰가 각자 지으면 같은 물음이 화면마다 달라 보인다.
    pub fn question(self) -> &'static str {
        crate::i18n::t(match self {
            Prompt::RenameTab => "탭 이름:",
            Prompt::MoveTab => "옮길 자리(번호):",
            Prompt::KillPane => "이 패널을 닫을까?",
            // 정본 `dialog.kill_tab_msg` 와 같은 자리다. 아래 셋은 **상황이 다르면 다른
            // 것을 잃는다**는 것을 적는다 — 되돌릴 수 없는 화면에서 문구가 상황을 모르면
            // 사용자는 무엇이 사라지는지 모른 채 누른다.
            Prompt::KillTab => "이 탭을 닫을까? 탭의 셸이 종료된다.",
            Prompt::KillTabLast => "이 탭을 닫으면 pytmux 가 종료된다(모든 셸 종료). 닫을까?",
            Prompt::KillTabLastRemote => {
                "이것이 마지막 로컬 탭이다. 닫으면 pytmux 가 종료되고(모든 셸 종료) 열려 있는 원격 탭 보기도 함께 끊긴다. 닫을까?"
            }
            Prompt::KillTabPinned => {
                "고정 탭 '{name}' 을(를) 닫을까? 고정 탭은 상시 유지용이다 — 탭의 셸이 종료된다."
            }
            Prompt::RenamePane => "패널 제목:",
            Prompt::SwapTab => "맞바꿀 탭 번호:",
            // 문구의 주인은 **플러그인**이다(스펙의 `text`) — 뷰가 그것으로 덮어 그린다.
            // 여기 값은 스펙이 물음을 안 줬을 때의 폴백이다.
            Prompt::PluginAsk => "플러그인이 물었다:",
            // 빈 대답이 **뜻을 갖는** 자리라 물음에 적는다(원격 떼기와 같은 부류).
            Prompt::PipePane => "출력을 흘릴 명령 (비우면 끄기):",
            Prompt::JoinPane => "합칠 패널이 있는 탭 번호:",
            Prompt::DisplayMessage => "띄울 메시지:",
            Prompt::DefaultPath => "새 탭이 시작할 자리 (current · home · 절대경로):",
            Prompt::ClaudeCommand => "esc c 가 실행할 명령 (비우면 셸만):",
            // 토큰을 물음에 적는다 — 문법을 모르면 무엇을 칠지 알 수 없고, 이 자리에
            // 도움말 화면을 하나 더 세울 만큼의 문법도 아니다.
            Prompt::StatusLeft => "상태줄 왼쪽 (#S 세션 · #I 탭 · #W 이름 · #h 호스트 · %H:%M):",
            Prompt::StatusRight => "상태줄 오른쪽 (#{pane_title} · #h · %H:%M %Y-%m-%d):",
            Prompt::StatusBg => "상태줄 배경색 (예: blue · brightblack · 비우면 테마):",
            Prompt::StatusFg => "상태줄 글자색 (비우면 테마):",
            Prompt::CursorColor => "커서 색 (예: blue · brightblack · #ff8800 · 비우면 테마):",
            Prompt::SetOption => "설정 (예: mouse off · prefix C-a):",
            // 물음에 **발화하는 이벤트 이름을 적는다** — 목록을 따로 열지 않고도
            // 무엇을 걸 수 있는지 보이게 하는 자리다.
            Prompt::SetHook => {
                "훅 (client-attached · after-new-window · alert-bell) <명령> · 지우기 -u <이벤트>:"
            }
            Prompt::SendKeys => "보낼 키 (예: hello Enter · C-c):",
            Prompt::DisplayPopup => "팝업에서 띄울 명령 (비우면 셸):",
            Prompt::RunShell => "돌릴 셸 명령:",
            Prompt::IfShell => "조건 | 성공 명령 | 실패 명령(생략 가능) (예: git st | redraw | display-message):",
            Prompt::BindKey => "바인딩 ([-n] <키> <명령> · 예: r source-file):",
            Prompt::UnbindKey => "풀 키 ([-n] <키>):",
            Prompt::SaveTabLayout => "저장할 배치 이름:",
            // ★ 덮어쓴다는 말을 물음에 적는다 — 지금 배치가 사라지는 것을 모르고
            // 이름을 치는 사람이 있다.
            Prompt::LoadTabLayout => "불러올 배치 이름 (지금 탭을 덮어쓴다):",
            Prompt::LoadTabLayoutNew => "불러올 배치 이름 (새 탭으로):",
            Prompt::SetPrefix => "새 prefix 키 (C-a · M-x 표기):",
            Prompt::KillServer => "서버를 끝낼까? (이 서버의 모든 탭과 셸이 사라진다)",
            Prompt::RestartServer => "서버를 재시작할까? (셸은 살고 화면이 잠깐 끊긴다)",
            Prompt::RestartAll => "서버와 이 클라를 함께 재시작할까? (셸은 산다)",
            Prompt::RemoteAttach => "붙을 상자 (host):",
            Prompt::RemoteNewTab => "새 셸을 띄울 상자 (host):",
            Prompt::RemoteDetach => "뗄 상자 (host · 비우면 전부):",
            // 파이썬 `search.prompt_up` 과 같은 문구 — ↑ 는 "위(과거)부터"라는 뜻이다.
            Prompt::SearchScrollback => "search ↑ (이전 방향)",
            // 파이썬 `search.all_prompt` 과 같은 문구.
            Prompt::SearchAll => "모든 탭·패널 검색",
        })
    }
}

/// 지금 떠 있는 화면들.
#[derive(Debug, Clone, Default)]
pub struct Screens {
    stack: Vec<Screen>,
    /// 맨 위 화면의 스크롤 위치(줄). 화면을 바꾸면 0으로 돌아간다.
    scroll: usize,
    /// 지금 묻고 있는 것(입력·확인 화면일 때만). 무엇을 물었는지 잊으면 대답을 어디에
    /// 쓸지 알 수 없다.
    asking: Option<Prompt>,
    /// 입력 화면에 지금까지 친 글자.
    typed: String,
    /// 입력 화면의 커서(글자 인덱스, pytmux-174) — 정본 Textual `Input` 처럼 중간에서
    /// 편집할 수 있어야 한다. `typed` 가 바뀌면 끝으로 옮긴다(새로 열거나 후보를 채울 때).
    prompt_cursor: usize,
    /// 커서와 짝을 이루는 선택 범위의 반대쪽 끝(글자 인덱스). `None` 이면 선택 없음
    /// (`Shift+`좌우/Home/End 로 생기고, 방향키만 누르면 풀린다).
    prompt_sel_anchor: Option<usize>,
    /// 확인 화면에서 고른 버튼([`CONFIRM_YES`]/[`CONFIRM_NO`]). 열 때마다 '아니오'다.
    confirm_pick: usize,
    /// 물음의 **인자 이력 후보**(최근-우선 전체 목록 — 파이썬 arghist).
    ///
    /// `None` 은 "아직 안 채웠다"다 — 이력의 주인(영속 파일)은 뷰가 알아서, 물음이
    /// 열린 뒤 첫 키 처리 진입점에서 [`Screens::set_prompt_history`] 로 채운다
    /// (core 는 IO 가 없다는 계약). 채웠는데 비면 이력 없는 평소 물음이다.
    prompt_history: Option<Vec<String>>,
    /// 지금 골라 둔 후보(필터된 목록 안 위치). `None` 이면 아무것도 안 골랐다 —
    /// Enter 는 친 글 그대로 간다.
    prompt_pick: Option<usize>,
    /// 원격 머지의 분할 방향(참 = 좌우). 화면 안에서 `h`/`v` 로 바꾼다.
    ///
    /// 여기 두는 이유: 두 뷰가 각자 들면 같은 화면이 화면마다 다른 방향으로 붙는다.
    merge_horizontal: bool,
    /// 목록형 화면의 선택 위치. 스크롤과 따로 두는 이유: 읽는 화면은 선택이 없고,
    /// 목록 화면은 **선택이 곧 커서**라 스크롤과 다른 값이다.
    selected: usize,
    /// 인자 폼이 다루고 있는 명령과 줄마다 고른 값(패리티 G8v).
    ///
    /// 여기 두는 이유는 다른 화면 상태와 같다 — 두 뷰가 각자 들면 **같은 화면이 뷰마다
    /// 다른 값을 보인다.**
    options: Option<&'static crate::options::CommandOptions>,
    option_sel: Vec<usize>,
    /// 작성창의 버퍼(패리티 `e_ins`). 화면이 떠 있을 때만 값이 있다.
    editor: Option<crate::editor::Editor>,
    /// 정보 팝업에서 펴 있는 탭(패리티 `InfoTabsScreen`).
    ///
    /// 스크롤과 따로 두는 이유는 목록 화면의 `selected` 와 같다 — **탭을 바꾸면 스크롤이
    /// 0으로 돌아가야** 하고, 둘을 한 값으로 쓰면 그 규칙을 적을 자리가 없다.
    info_tab: usize,
    /// 팔레트에서 펴 있는 카테고리 탭(0 = `전체`, 그 뒤가 [`crate::PALETTE_CATS`]).
    ///
    /// `info_tab` 과 갈라 두는 이유: 두 화면이 같은 값을 쓰면 팔레트를 닫았다 열 때 정보
    /// 팝업에서 보던 탭이 따라온다. 화면마다 자기 자리를 갖는 것이 이 구조체의 규칙이다.
    palette_tab: usize,
    /// 메뉴에서 **들어가 있는 서브메뉴**(없으면 최상위). 파이썬 `MENU_GROUPS` 의 키다.
    ///
    /// 화면을 닫거나 새로 열면 최상위로 돌아간다 — 지난번에 들어가 있던 그룹이 따라오면
    /// 메뉴를 연 사람은 자기가 못 본 사이에 화면이 바뀐 것으로 읽는다.
    menu_group: Option<&'static str>,
    /// 확인 화면이 물음 위에 보일 글(재시작 드라이런의 실패 목록 등).
    detail: String,
    /// 지금 떠 있는 플러그인 화면이 **목록인가**(아니면 글이다). 뷰가 열 때 정한다 —
    /// 스펙은 proto 가 들고 core 는 그것을 모른다(`MenuToggles` 와 같은 갈래).
    plugin_list: bool,
    /// 그 판이 **다열**이면 `(열당 줄 수, 열 수)`, 아니면 `(0, 1)`(설계 §4.3 `panel`).
    ///
    /// # 왜 core 가 이 수를 드나
    ///
    /// **자리는 뷰가 재고 뜻은 여기서 정한다** — `PanelTarget` 과 같은 갈림이다. 한 열에
    /// 몇 줄이 들어가는지는 그리는 쪽만 알지만(칸 예산), *"←→ 는 한 열을 건넌다"* 는
    /// 손버릇이라 뷰마다 다시 적으면 그 순간 클라마다 갈린다.
    ///
    /// 뷰가 키를 먹기 **직전에** 넣는다 — 열 수는 창 크기와 함께 변하므로 열 때 한 번
    /// 잡아 두면 리사이즈 뒤의 ←→ 가 엉뚱한 줄로 뛴다.
    plugin_grid: (usize, usize),
    /// 서버가 부는 **플러그인 표면**(설계 Tier A). 뷰가 매 프레임 옮겨 담는다.
    ///
    /// 왜 화면 상태에 두나: 메뉴의 층·설정의 분류 이동이 **이 목록의 길이와 분류**에
    /// 달려 있다(플러그인이 없으면 `플러그인` 그룹 자체가 없다). 뷰가 들고 있으면 키
    /// 처리와 그리기가 서로 다른 목록을 볼 수 있다 — 그때 증상은 "고른 줄과 실행된 줄이
    /// 다르다"라 눈으로는 못 찾는다.
    plugins: crate::plugins::PluginSurface,
    /// 마지막으로 작성창에 쓰던 글. **취소해도 남는다.**
    ///
    /// 파이썬 `_compose_draft` 와 같은 자리다. 왜 남기나: `Esc` 로 닫는 것은 "이 글을
    /// 패널에 안 넣겠다"이지 "쓴 것을 버리겠다"가 아니다. 안 남기면 길게 쓰다가 습관적으로
    /// `Esc` 를 두 번 누른 사람이 전부 잃는다.
    compose_draft: String,
}

impl Screens {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn top(&self) -> Option<Screen> {
        self.stack.last().copied()
    }

    pub fn is_open(&self) -> bool {
        !self.stack.is_empty()
    }

    pub fn scroll(&self) -> usize {
        self.scroll
    }

    pub fn selected(&self) -> usize {
        self.selected
    }

    /// 원격 머지의 분할 방향(참 = 좌우).
    pub fn merge_horizontal(&self) -> bool {
        self.merge_horizontal
    }

    /// 지금 무엇을 묻고 있나(입력·확인 화면일 때).
    pub fn asking(&self) -> Option<Prompt> {
        self.asking
    }

    /// 입력 화면에 지금까지 친 글자(**줄 통째** — 이름과 인자를 다 담는다).
    pub fn typed(&self) -> &str {
        &self.typed
    }

    /// 입력 화면의 커서 위치(글자 인덱스, pytmux-174) — 뷰가 그 자리에 커서를 그린다.
    pub fn prompt_cursor(&self) -> usize {
        self.prompt_cursor
    }

    /// 입력 화면의 선택 범위 — 정렬된 `(시작, 끝)` 글자 인덱스. 없으면(또는 폭이
    /// 0이면) `None` — 뷰가 그 범위만 배경을 칠한다.
    pub fn prompt_selection(&self) -> Option<(usize, usize)> {
        self.prompt_sel_anchor
            .map(|a| if a <= self.prompt_cursor { (a, self.prompt_cursor) } else { (self.prompt_cursor, a) })
            .filter(|(s, e)| s != e)
    }

    fn prompt_chars(&self) -> Vec<char> {
        self.typed.chars().collect()
    }

    /// 친 줄의 **이름 쪽**(첫 공백 앞) — 팔레트가 거를 때 쓴다(pytmux-7).
    pub fn typed_filter(&self) -> &str {
        split_first_space(&self.typed).0
    }

    /// 친 줄의 **인자 쪽**(첫 공백 뒤). 인자를 안 쳤으면 빈 문자열이다.
    pub fn typed_arg(&self) -> &str {
        split_first_space(&self.typed).1
    }

    /// 인자 폼을 연다. 표에 없는 이름이면 **아무 일도 안 한다**(`false`).
    ///
    /// 열지 않고 조용히 넘기는 이유: 빈 폼을 띄우면 사용자는 고를 것이 없는 화면 앞에서
    /// `Esc` 를 찾게 된다. 표에 없다는 것은 우리가 그 값을 보낼 수 없다는 뜻이다.
    pub fn open_options(&mut self, command: &str) -> bool {
        let Some(options) = crate::options::options_for(command) else {
            return false;
        };
        self.open(Screen::Options);
        self.options = Some(options);
        self.option_sel = vec![0; options.specs.len()];
        true
    }

    /// 인자 폼이 다루고 있는 명령(없으면 `None`).
    pub fn options(&self) -> Option<&'static crate::options::CommandOptions> {
        self.options
    }

    /// 줄마다 지금 고른 값의 번호.
    pub fn option_sel(&self) -> &[usize] {
        &self.option_sel
    }

    /// 명령 팔레트를 연다(필터는 비어 있고 **`전체` 탭**부터다).
    ///
    /// 왜 `전체` 부터인가: 이름을 아는 사람의 화면이라 대개 바로 치기 시작한다. 카테고리
    /// 탭에서 시작하면 친 글자가 그 탭에만 걸려 "그런 명령이 없다"로 보인다(파이썬도
    /// `_ci = 0` 으로 연다 — "바로 전부 탐색").
    pub fn open_palette(&mut self) {
        self.open(Screen::Commands);
        self.palette_tab = 0;
    }

    /// 팔레트에서 펴 있는 카테고리 탭(0 = `전체`).
    pub fn palette_tab(&self) -> usize {
        self.palette_tab
    }

    /// 지금 탭이 거르는 카테고리(`전체` 면 `None`) — 뷰가 목록을 만들 때 쓴다.
    ///
    /// `'static` 이 아닌 이유: 플러그인이 낸 분류(`탐색`·`Perforce`)는 서버가 주는
    /// 런타임 값이다. 정적 표만 돌려주면 그 탭들은 목록을 못 거른다.
    pub fn palette_cat(&self) -> Option<&str> {
        self.palette_tab
            .checked_sub(1)
            .and_then(|i| self.plugins.palette_cats().get(i).copied())
    }

    /// 작성창을 연다. `seed` 가 비면 **직전 초안**으로 시작한다(파이썬과 같다).
    ///
    /// 시드를 인자로 받는 이유: 활성 패널 프롬프트에 들어 있는 글을 인계해 오는 것은
    /// **화면을 긁어야** 아는 일이고, 그건 뷰의 몫이다(core 는 화면을 모른다).
    pub fn open_compose(&mut self, seed: &str) {
        let seed = if seed.is_empty() {
            self.compose_draft.clone()
        } else {
            seed.to_owned()
        };
        self.open(Screen::Compose);
        self.editor = Some(crate::editor::Editor::new(&seed));
    }

    /// 정보 팝업에서 펴 있는 탭 번호.
    pub fn info_tab(&self) -> usize {
        self.info_tab
    }

    /// 정보 팝업을 연다. 탭 내용은 **뷰가 안다**(core 는 서버도 호스트도 모른다).
    pub fn open_info_tabs(&mut self) {
        self.open(Screen::InfoTabs);
        self.info_tab = 0;
    }

    /// 탭 수에 맞춰 **접는다**(자르는 것이 아니라 순환). 몇 개인지 아는 것은 그리는
    /// 쪽이라 여기서 받는다(목록 화면의 `clamp_selection` 과 같은 규약).
    ///
    /// 양쪽으로 도는 것이 요점이다. 탭이 둘뿐일 때 끝에서 막히면 어느 쪽 화살표가 살아
    /// 있는지 매번 시험해야 한다 — 인자 폼의 값 순환과 같은 이유다.
    pub fn wrap_info_tab(&mut self, len: usize) {
        if len == 0 {
            self.info_tab = 0;
            return;
        }
        // `Left` 가 0에서 아래로 내려가면 `usize::MAX` 로 표시해 둔다(여기서만 뜻이 있다).
        self.info_tab = if self.info_tab == usize::MAX {
            len - 1
        } else {
            self.info_tab % len
        };
    }

    /// 지금 작성창의 버퍼(떠 있을 때만).
    pub fn editor(&self) -> Option<&crate::editor::Editor> {
        self.editor.as_ref()
    }

    /// 붙여넣기를 작성창으로 돌린다. 작성창이 없으면 `false` — 그때는 평소대로 패널이다.
    ///
    /// 이 갈래가 없으면 팝업을 띄운 채 붙여넣은 글이 **뒤 셸에 찍힌다**(파이썬이
    /// `_active_compose_screen` 으로 라우팅하는 자리와 같다).
    pub fn paste_into_compose(&mut self, text: &str) -> bool {
        // 팔레트가 위에 떠 있어도(작성창 → `Esc` `:`) 작성창으로 간다 — 파이썬도 스택
        // 어디서든 작성창을 찾아 넣는다. 그것이 `paste-image` 동선의 요점이다.
        match self.editor.as_mut() {
            Some(editor) => {
                editor.insert_str(text);
                true
            }
            None => false,
        }
    }

    /// 한 줄 입력을 연다. `seed` 는 미리 채워 둘 값(이름 바꾸기의 현재 이름 등).
    ///
    /// 미리 채우는 이유: 이름을 **바꾸는** 것이지 새로 짓는 것이 아니다 — 빈 칸에서
    /// 시작하면 한 글자만 고치려는 사람이 전체를 다시 쳐야 한다.
    pub fn ask(&mut self, prompt: Prompt, seed: &str) {
        self.open(Screen::Prompt);
        self.asking = Some(prompt);
        self.typed = seed.to_owned();
        // 커서는 **끝**에서 시작한다(정본 Textual `Input` 과 같다) — 이름을 고치는
        // 사람은 대개 뒤에 이어 치거나 지운다.
        self.prompt_cursor = self.typed.chars().count();
        self.prompt_sel_anchor = None;
        // 앞선 확인 화면의 상세를 **버린다**. 물음 판도 상세를 그리므로(`render_prompt`),
        // 안 버리면 "이 탭을 닫으면 …" 같은 붉은 경고가 엉뚱한 물음 위에 남는다.
        self.detail.clear();
        // 이력은 **미채움**으로 연다 — 뷰가 다음 진입점에서 자기 arghist 로 채운다
        // (`asking_unfilled` 문서). 그래서 40여 개의 ask 호출부가 그대로다.
        self.prompt_history = None;
        self.prompt_pick = None;
    }

    /// 물음 위에 **플러그인이 준 글**을 함께 세운다(설계 Tier C).
    ///
    /// [`Self::ask`] 와 가른 이유: 저쪽 40여 개 호출부의 물음은 `Prompt::question` 이
    /// 정하고, 이쪽은 **문구의 주인이 플러그인**이다. 같은 함수에 빈 인자를 하나 더
    /// 다는 것보다 그 차이가 이름에 보이는 편이 낫다.
    pub fn ask_with_detail(&mut self, prompt: Prompt, seed: &str, detail: String) {
        self.ask(prompt, seed);
        self.detail = detail;
    }

    /// 인자 이력이 있는 물음(파이썬 arghist — remote-attach 의 호스트 등).
    ///
    /// `history` 는 최근-우선 전체 목록이다. 화면에는 친 글로 **좁혀진** 것만 보이고
    /// (`prompt_matches`), `↑`/`↓` 로 고르고 `Tab`/`→` 가 입력칸에 채운다 — Enter 는
    /// 언제나 **입력칸의 글**을 보낸다(후보는 제안이지 강제가 아니다).
    pub fn ask_with_history(&mut self, prompt: Prompt, seed: &str, history: Vec<String>) {
        self.open(Screen::Prompt);
        self.asking = Some(prompt);
        self.typed = seed.to_owned();
        self.prompt_cursor = self.typed.chars().count();
        self.prompt_sel_anchor = None;
        self.prompt_history = Some(history);
        self.prompt_pick = None;
    }

    /// 이력을 **아직 안 채운** 열린 물음. 뷰의 키 처리 진입점이 이걸 보고 한 번 채운다.
    pub fn asking_unfilled(&mut self) -> Option<Prompt> {
        (matches!(self.top(), Some(Screen::Prompt)) && self.prompt_history.is_none())
            .then_some(self.asking)
            .flatten()
    }

    /// 열린 물음의 이력 후보를 채운다(최근-우선 전체 목록 — 좁히기는 core 가 한다).
    pub fn set_prompt_history(&mut self, history: Vec<String>) {
        self.prompt_history = Some(history);
        self.prompt_pick = None;
    }

    /// 친 글로 좁혀진 이력 후보(최근-우선 · 최대 5). 물음 화면이 아니면 비어 있다.
    ///
    /// 다섯인 이유: 후보는 **기억을 되살리는** 것이지 목록을 훑는 것이 아니다 —
    /// 파이썬도 후보 영역을 작게 자른다(MAX_CAND).
    pub fn prompt_matches(&self) -> Vec<&str> {
        if !matches!(self.top(), Some(Screen::Prompt)) {
            return Vec::new();
        }
        self.prompt_history
            .as_deref()
            .unwrap_or_default()
            .iter()
            .filter(|h| h.starts_with(&self.typed) && h.as_str() != self.typed)
            .take(5)
            .map(String::as_str)
            .collect()
    }

    /// 지금 골라 둔 후보 위치(`prompt_matches` 안 색인).
    pub fn prompt_pick(&self) -> Option<usize> {
        self.prompt_pick
    }

    /// 예/아니오를 묻는다. **되돌릴 수 없는 것 앞에만** 세운다.
    pub fn confirm(&mut self, prompt: Prompt) {
        self.confirm_with(prompt, String::new());
    }

    /// 확인 화면에서 지금 고른 버튼([`CONFIRM_YES`]/[`CONFIRM_NO`]) — 뷰가 강조에 쓴다.
    pub fn confirm_pick(&self) -> usize {
        self.confirm_pick
    }

    /// 확인 화면의 버튼 낱말들(`[예, 아니오]`).
    ///
    /// 뷰가 각자 적으면 같은 대화가 화면마다 다른 낱말을 보인다. 정본은 여기에 상황별
    /// 동사(`닫기`)를 쓰는데, 그건 물음마다 다른 글이라 다음 슬라이스로 미룬다 —
    /// 지금은 두 뷰가 **같은 낱말·같은 자리**를 갖는 것이 먼저다.
    pub fn confirm_buttons(&self) -> [&'static str; 2] {
        self.asking
            .map(Prompt::confirm_labels)
            .unwrap_or([crate::i18n::t("예"), crate::i18n::t("아니오")])
    }

    /// 지금 판에 적을 물음 — 슬롯이 있으면 [`Self::detail`] 로 채운 글.
    ///
    /// 왜 여기서 채우나: [`Prompt::question`] 은 `&'static str` 이라 그때그때 다른 값을
    /// 못 담는다. 그렇다고 뷰가 채우면 **두 뷰의 문장이 갈라진다**.
    pub fn confirm_question(&self) -> String {
        let Some(prompt) = self.asking else {
            return String::new();
        };
        // 플러그인이 물은 것은 **그 플러그인이 문구를 정한다**(스펙의 `title`). 여기
        // 폴백("플러그인이 물었다:")만 보이면 사람은 무엇을 지우는지 모른 채 누른다.
        if prompt == Prompt::PluginAsk
            && let Some(first) = self.detail.lines().next()
            && !first.is_empty()
        {
            return first.to_owned();
        }
        if prompt.detail_fills_a_slot() {
            crate::i18n::tf(prompt.question(), &[("name", &self.detail)])
        } else {
            crate::i18n::t(prompt.question()).to_string()
        }
    }

    /// 물음 **위에 따로** 보일 여러 줄(없으면 빈 글). 슬롯 채우기용 detail 은 여기 안 온다.
    pub fn confirm_detail(&self) -> &str {
        match self.asking {
            Some(p) if p.detail_fills_a_slot() => "",
            // 플러그인 물음은 **첫 줄이 물음으로 올라갔다** — 여기서 또 그리면 같은 글이
            // 두 번 뜬다. 남은 줄(지울 이름들 등)만 상세로 내려온다.
            Some(Prompt::PluginAsk) => match self.detail.split_once('\n') {
                Some((_, rest)) => rest,
                None => "",
            },
            _ => &self.detail,
        }
    }

    /// 지금 판이 되돌릴 수 없는 것 앞에 서 있나 — 뷰가 버튼 색을 고르는 데 쓴다.
    pub fn confirm_is_dangerous(&self) -> bool {
        self.asking.is_some_and(Prompt::is_dangerous)
    }

    /// 지금 확인 판에 적을 제목(없으면 `None` — 뷰가 화면 기본 제목을 쓴다).
    pub fn confirm_title(&self) -> Option<&'static str> {
        self.asking.and_then(Prompt::confirm_title)
    }

    /// 물음 위에 **그때그때 다른 글**을 함께 보이는 판(재시작 드라이런의 실패 목록).
    ///
    /// [`Prompt::question`] 은 정적이다 — 화면마다 같은 물음이라야 하기 때문이다. 그런데
    /// "무엇이 실패했나"는 매번 다르고, **그것을 보고 사용자가 판단한다.** 그 글을 물음에
    /// 접어 넣을 수 없으므로 자리를 하나 더 준다.
    pub fn confirm_with(&mut self, prompt: Prompt, detail: String) {
        self.open(Screen::Confirm);
        self.asking = Some(prompt);
        self.detail = detail;
        // 포커스는 늘 **'아니오'에서 시작**한다(정본과 같다) — 이 화면의 취지다.
        self.confirm_pick = CONFIRM_NO;
    }

    /// 확인 화면이 물음 위에 보일 글(없으면 빈 문자열).
    pub fn detail(&self) -> &str {
        &self.detail
    }

    /// 선택 위치를 목록 길이에 맞춰 자른다. 길이를 아는 것은 그리는 쪽이다.
    ///
    /// **목록이 줄었을 때가 진짜 이유다** — 탭이 하나 닫히면 선택이 목록 밖을 가리키고,
    /// 그대로 `Enter` 를 치면 없는 탭으로 전환하려 든다.
    pub fn clamp_selection(&mut self, len: usize) {
        self.selected = self.selected.min(len.saturating_sub(1));
    }

    /// 커서를 그 줄에 놓는다 — **목록의 주인이 자리를 정하는** 자리용(플러그인 화면 스펙).
    ///
    /// 평소 목록은 커서가 이 클라의 것이라 서버가 건드리지 않는다. 플러그인 화면은
    /// 다르다: 어느 디렉터리로 들어갔는지·태그를 찍고 다음 줄로 내려갈지를 **스펙을
    /// 만든 쪽**이 안다. 그 칸(`PluginScreen::selected`)을 아무도 안 읽던 동안 목록을
    /// 갈아 끼워도 커서는 옛 자리에 남아 있었다.
    pub fn select_row(&mut self, row: usize) {
        self.selected = row;
        self.scroll = 0;
    }

    /// 목록형 화면인가(선택 커서를 그리는가).
    pub fn is_list(screen: Screen) -> bool {
        matches!(
            screen,
            Screen::Tabs
                | Screen::Tree
                | Screen::Buffers
                | Screen::Commands
                | Screen::Settings
                | Screen::Plugins
                | Screen::Menu
                | Screen::Layouts
                | Screen::MergeRemote
                | Screen::SearchResults
        )
    }

    /// 화면을 연다. **같은 화면이 이미 맨 위면 닫는다**(토글).
    ///
    /// 토글인 이유: 이 화면들을 여는 것은 키 하나(`?`·`p`)이고, 같은 키를 다시 누르는 것이
    /// 사용자가 기대하는 닫기다. 스택에 같은 화면이 두 번 쌓이면 `Esc` 를 두 번 눌러야
    /// 빠져나오는데, 그건 아무도 예상하지 않는다.
    pub fn open(&mut self, screen: Screen) {
        self.scroll = 0;
        self.selected = 0;
        self.asking = None;
        self.typed.clear();
        self.menu_group = None;
        // 지난 확인의 상세가 남으면 **다음 물음 위에 엉뚱한 글**이 붙는다.
        self.detail.clear();
        if self.top() == Some(screen) {
            self.stack.pop();
            return;
        }
        self.stack.push(screen);
    }

    /// 탭 스위처를 **정본과 같은 자리에서** 연다(§10-21ⓔ2).
    ///
    /// # 왜 첫 선택이 0 이 아닌가
    ///
    /// 정본이 일부러 **다음 탭**에 놓는다 — *"첫 화면부터 다음 탭이 선택돼 있어
    /// `esc Tab Enter` 가 곧 '다음 탭으로 전환'이다"*(`client.py::open_tab_switcher`,
    /// 사용자 요청 2026-07-15 · Alt+Tab 동선). 우리 `open` 은 무조건 `selected = 0`
    /// 이라 같은 손버릇이 다른 탭을 골랐다.
    ///
    /// # 왜 core 가 정하나
    ///
    /// 뷰가 열 때마다 자기 셈으로 커서를 옮기면 두 클라가 갈린다. 뜻은 여기서 정하고
    /// 뷰는 줄 목록만 준다.
    ///
    /// # 탭이 하나뿐이면 안 연다
    ///
    /// 고를 것이 없는 목록을 띄우는 것은 "아무 일도 안 일어난다"와 같다(정본은 그때
    /// 활성 탭을 깜빡여 알린다). 안 열었으면 `false` 를 돌려주니 뷰가 그 신호를 쓴다.
    /// `rows` 는 줄마다 **(이 줄이 탭인가, 활성 탭인가)** 다.
    ///
    /// 두 값을 다 받는 이유: 스위처 목록에는 탭 줄 밑에 **패널 하위행**이 섞인다. 탭만
    /// 세지 않으면 "탭이 둘 이상인가" 판정이 틀리고, 탭만 건너뛰지 않으면 "다음 탭"이
    /// 같은 탭의 패널이 되어 Alt+Tab 동선이 무너진다.
    pub fn open_tab_switcher(&mut self, rows: &[(bool, bool)]) -> bool {
        if rows.iter().filter(|(is_tab, _)| *is_tab).count() < 2 {
            return false;
        }
        self.open(Screen::Tabs);
        self.selected = Self::next_tab_row(rows);
        true
    }

    /// 활성 탭 **다음 탭 줄**의 자리(정본 `initial = (pos + 1) % len`).
    fn next_tab_row(rows: &[(bool, bool)]) -> usize {
        let at = rows.iter().position(|(is_tab, active)| *is_tab && *active);
        // 활성 탭을 못 찾으면 첫 줄 — 종전 동작 그대로다(무엇도 안 고른 것보다 낫다).
        let Some(at) = at else { return 0 };
        // 그 다음의 **탭 줄**로 간다(패널 하위행은 건너뛴다). 한 바퀴 돌아 제자리면
        // 그대로 둔다 — 탭이 하나뿐인 경우는 위에서 이미 걸렀다.
        for step in 1..=rows.len() {
            let i = (at + step) % rows.len();
            if rows[i].0 {
                return i;
            }
        }
        at
    }

    /// 맨 위 화면을 닫는다. 닫을 것이 있었으면 `true`.
    pub fn close_top(&mut self) -> bool {
        self.scroll = 0;
        self.selected = 0;
        self.asking = None;
        self.typed.clear();
        self.prompt_cursor = 0;
        self.prompt_sel_anchor = None;
        self.menu_group = None;
        self.detail.clear();
        self.stack.pop().is_some()
    }

    /// 스크롤 위치를 내용 길이에 맞춰 자른다. 길이를 아는 것은 그리는 쪽이다.
    pub fn clamp_scroll(&mut self, max: usize) {
        self.scroll = self.scroll.min(max);
    }

    /// 화면이 열려 있을 때의 키. 열려 있지 않으면 `None` — 그때는 평소 경로다.
    ///
    /// 규칙 2가 여기 있다: 방향키·페이지 키는 화면의 것이고 **나머지는 닫는다**.
    pub fn press(&mut self, key: Key, mods: Mods) -> Option<ScreenKey> {
        if !self.is_open() {
            return None;
        }
        // ★ 작성창은 **모든 키**가 자기 것이다 — `Ctrl+A`(전체 선택)·`Ctrl+J`(줄바꿈)까지.
        // 그래서 아래 "수정키 조합은 화면이 알 바 아니다" 규칙보다 **먼저** 본다. 순서를
        // 뒤집으면 편집 중 `Ctrl+A` 가 화면을 닫는다.
        if matches!(self.top(), Some(Screen::Compose)) {
            return Some(self.press_compose(key, mods));
        }
        // 입력 화면에서는 수정키 조합도 **입력의 일부일 수 있다**(Shift+글자). 대문자는
        // 이미 글자로 오므로 여기서는 Ctrl/Alt 만 거른다 — 그건 여전히 화면 밖의 것이다.
        if matches!(self.top(), Some(Screen::Prompt)) && !mods.ctrl && !mods.alt {
            return Some(self.press_prompt(key));
        }
        // 수정키가 붙은 조합은 화면이 알 바가 아니다 — 닫고 나서 다시 누르게 한다.
        if mods != Mods::NONE {
            self.close_top();
            return Some(ScreenKey::Closed);
        }
        // 팔레트는 목록 + 입력이 한 화면에 있다 — 글자는 필터로, 방향키는 선택으로.
        if matches!(self.top(), Some(Screen::Commands)) && !mods.ctrl && !mods.alt {
            return Some(self.press_palette(key));
        }
        // 입력·확인은 **대답을 받는** 화면이라 규칙이 또 다르다.
        match self.top() {
            Some(Screen::Prompt) => return Some(self.press_prompt(key)),
            Some(Screen::Confirm) => return Some(self.press_confirm(key)),
            _ => {}
        }
        // 원격 머지는 목록형이지만 `h`/`v` 를 **자기 키로** 먹는다.
        if matches!(self.top(), Some(Screen::MergeRemote)) {
            match key {
                Key::Char('h') => {
                    self.merge_horizontal = true;
                    return Some(ScreenKey::Consumed);
                }
                Key::Char('v') => {
                    self.merge_horizontal = false;
                    return Some(ScreenKey::Consumed);
                }
                _ => return Some(self.press_list(key)),
            }
        }
        // 트리는 목록형이지만 `d`/`x` 를 **자기 키로** 먹는다(그 줄 닫기).
        if matches!(self.top(), Some(Screen::Tree))
            && matches!(key, Key::Char('d') | Key::Char('x'))
        {
            let row = self.selected;
            self.close_top();
            return Some(ScreenKey::TreeKill(row));
        }
        // 메뉴는 목록형이지만 **계층**이 있다(그룹으로 들어가고 나온다).
        if matches!(self.top(), Some(Screen::Menu)) {
            return Some(self.press_menu(key));
        }
        // 정보 팝업은 **←→ 가 탭**이고 ↑↓ 는 스크롤이다.
        if matches!(self.top(), Some(Screen::InfoTabs)) {
            return Some(self.press_info_tabs(key));
        }
        // 인자 폼은 ↑↓ 가 줄, ←→ 가 값이다.
        if matches!(self.top(), Some(Screen::Options)) {
            return Some(self.press_options(key));
        }
        // 플러그인이 준 화면 — 목록이면 고르는 화면, 글이면 읽는 화면이다.
        // 그 갈림은 **스펙**에 있고 뷰가 열 때 알려 준다(core 는 스펙을 안 든다).
        if matches!(self.top(), Some(Screen::PluginView)) {
            if self.plugin_list {
                // ★ **다열 판은 한 열이 한 묶음**이다(설계 §4.3 `panel` · pytmux-126).
                //   정본 mdir 의 손 그대로 ←→ 가 한 열, PgUp/PgDn 이 한 판을 건넌다 —
                //   열 채움이 세로 우선이라 ↑↓ 만으로도 전부 닿지만, 열이 셋이면 끝까지
                //   가는 데 세 배가 걸린다.
                //   ⛔ 위쪽 상한은 여기서 안 건다 — 줄 수를 아는 것은 스펙을 든 뷰이고
                //      (`press_list` 도 같은 규약이다) 그리는 자리가 그 값을 자른다.
                let (per_col, cols) = self.plugin_grid;
                if cols > 1 && per_col > 0 {
                    match key {
                        Key::Left => {
                            self.selected = self.selected.saturating_sub(per_col);
                            return Some(ScreenKey::Consumed);
                        }
                        Key::Right => {
                            self.selected += per_col;
                            return Some(ScreenKey::Consumed);
                        }
                        Key::PageUp => {
                            self.selected = self.selected.saturating_sub(per_col * cols);
                            return Some(ScreenKey::Consumed);
                        }
                        Key::PageDown => {
                            self.selected += per_col * cols;
                            return Some(ScreenKey::Consumed);
                        }
                        _ => {}
                    }
                }
                return Some(self.press_list(key));
            }
            return Some(match key {
                Key::Up => {
                    self.scroll = self.scroll.saturating_sub(1);
                    ScreenKey::Consumed
                }
                Key::Down => {
                    self.scroll += 1;
                    ScreenKey::Consumed
                }
                Key::PageUp => {
                    self.scroll = self.scroll.saturating_sub(PAGE);
                    ScreenKey::Consumed
                }
                Key::PageDown => {
                    self.scroll += PAGE;
                    ScreenKey::Consumed
                }
                Key::Escape => {
                    self.close_top();
                    ScreenKey::Closed
                }
                // 정본에 이 규약을 갖는 화면은 `InfoScreen` 하나뿐이다(pytmux-273) —
                // 이 판(플러그인이 준 읽기 전용 화면, 예: usage 팝업)은 그 계열이 아니라
                // 모르는 키를 **삼킨다**(아무 일도 안 함). 관계없는 키를 눌렀다고 판이
                // 조용히 사라지면 사용자가 무엇을 잃었는지 모른다.
                _ => ScreenKey::Consumed,
            });
        }
        // 설정·플러그인은 목록형이지만 `Enter` 가 화면을 안 닫는다(위 참조).
        if matches!(self.top(), Some(Screen::Settings | Screen::Plugins)) {
            return Some(self.press_settings(key));
        }
        // ★ 자동 재개 판의 `a` — 정본 `open_autoresume_info` 의 `hide_key="a"` 그대로다
        //   (뒤집고 **닫는다**; 다시 열어 새 상태를 확인하는 동선이다 · pytmux-183).
        //   `Chosen(0)` 으로 돌려주는 이유: 무엇을 보낼지(= `set_autoresume`)는 뷰의
        //   일이고, core 는 서버 명령을 모른다(다른 「고르는 판」들과 같은 경계).
        if self.top() == Some(Screen::Autoresume)
            && key == Key::Char('a')
            && mods == Mods::NONE
        {
            self.close_top();
            return Some(ScreenKey::Chosen(0));
        }
        // 목록형 화면은 **고르는** 화면이라 같은 키가 다른 일을 한다.
        if self.top().is_some_and(Self::is_list) {
            return Some(self.press_list(key));
        }
        match key {
            Key::Up => {
                self.scroll = self.scroll.saturating_sub(1);
                Some(ScreenKey::Consumed)
            }
            Key::Down => {
                self.scroll += 1;
                Some(ScreenKey::Consumed)
            }
            Key::PageUp => {
                self.scroll = self.scroll.saturating_sub(PAGE);
                Some(ScreenKey::Consumed)
            }
            Key::PageDown => {
                self.scroll += PAGE;
                Some(ScreenKey::Consumed)
            }
            // 정본 `InfoScreen._NAV_KEYS` 는 up/down/pageup/pagedown 과 **함께
            // home/end 를 먹는다**(pytmux-273 ①) — 우리는 그 넷만 받아 home/end 가
            // `_` 로 떨어져 판을 닫고 있었다. 닿는 화면: Keys·Version·ShellOutput·
            // RestartCheck·Hooks(전부 `canon_class` 가 InfoScreen).
            Key::Home
                if matches!(
                    self.top(),
                    Some(
                        Screen::Keys
                            | Screen::Version
                            | Screen::ShellOutput
                            | Screen::RestartCheck
                            | Screen::Hooks
                            | Screen::Autoresume
                    )
                ) =>
            {
                self.scroll = 0;
                Some(ScreenKey::Consumed)
            }
            Key::End
                if matches!(
                    self.top(),
                    Some(
                        Screen::Keys
                            | Screen::Version
                            | Screen::ShellOutput
                            | Screen::RestartCheck
                            | Screen::Hooks
                            | Screen::Autoresume
                    )
                ) =>
            {
                // 총 줄 수를 core 는 모른다(뷰가 그린다·`PageDown` 과 같은 경계) — 큰 값을
                // 넣어 두면 `skip()` 이 나머지를 전부 건너뛰어 사실상 "끝"이 된다.
                self.scroll = usize::MAX / 2;
                Some(ScreenKey::Consumed)
            }
            Key::Escape => {
                self.close_top();
                Some(ScreenKey::Closed)
            }
            // `Notices` 는 정본 `NoticeHistoryScreen` 처럼 **아무 키나 안 닫는다**
            // (pytmux-273 ②) — `escape`(위에서 처리) 만 닫고, 그 밖은 삼킨다.
            _ if matches!(self.top(), Some(Screen::Notices)) => Some(ScreenKey::Consumed),
            // 나머지(InfoScreen 계열 등)는 정본 그대로 아무 키나 닫는다(규칙 2).
            _ => {
                self.close_top();
                Some(ScreenKey::Closed)
            }
        }
    }
}

impl Screens {
    /// 인자 폼의 키(패리티 G8v). **↑↓ 는 줄, ←→ 는 값**이다.
    ///
    /// 값은 **순환한다**(끝에서 한 번 더 누르면 처음으로). 선택지가 둘뿐인 자리가 많아
    /// 끝에서 막히면 `←`·`→` 중 어느 쪽이 살아 있는지 매번 시험해야 한다.
    fn press_options(&mut self, key: Key) -> ScreenKey {
        let rows = self.option_sel.len();
        match key {
            Key::Down if rows > 0 => {
                self.selected = (self.selected + 1).min(rows - 1);
                ScreenKey::Consumed
            }
            Key::Up => {
                self.selected = self.selected.saturating_sub(1);
                ScreenKey::Consumed
            }
            Key::Left | Key::Right => {
                let Some(options) = self.options else {
                    return ScreenKey::Consumed;
                };
                let row = self.selected.min(rows.saturating_sub(1));
                let Some(spec) = options.specs.get(row) else {
                    return ScreenKey::Consumed;
                };
                let count = spec.choices.len();
                if count > 0 && let Some(sel) = self.option_sel.get_mut(row) {
                    *sel = if key == Key::Right {
                        (*sel + 1) % count
                    } else {
                        (*sel + count - 1) % count
                    };
                }
                ScreenKey::Consumed
            }
            Key::Enter => {
                let picked = self.selected;
                self.close_top();
                ScreenKey::Chosen(picked)
            }
            _ => {
                self.close_top();
                ScreenKey::Closed
            }
        }
    }
}

impl Screens {
    /// 정보 팝업의 키(패리티 `InfoTabsScreen`). **←→ 탭 · ↑↓ 스크롤 · 그 외 닫기.**
    ///
    /// 탭을 바꾸면 **스크롤을 0으로 되돌린다** — 안 그러면 긴 탭을 훑다 옆 탭으로 갔을 때
    /// 짧은 그 탭이 빈 화면으로 보인다(스크롤이 내용보다 아래에 있다).
    ///
    /// 탭은 **순환한다**. 파이썬도 그렇고, 탭이 둘뿐일 때 끝에서 막히면 어느 쪽 화살표가
    /// 살아 있는지 매번 시험해야 한다.
    fn press_info_tabs(&mut self, key: Key) -> ScreenKey {
        match key {
            Key::Right | Key::Tab => {
                self.info_tab += 1;
                self.scroll = 0;
                ScreenKey::Consumed
            }
            Key::Left | Key::BackTab => {
                // 0에서 왼쪽으로 가면 **마지막 탭**이어야 하는데, 탭 수를 아는 것은 뷰다.
                // `usize` 라 음수가 없으므로 큰 값을 표식으로 두고 뷰의 `wrap_info_tab`
                // 이 끝으로 접게 한다 — 그 접기가 곧 순환이다.
                self.info_tab = self.info_tab.checked_sub(1).unwrap_or(usize::MAX);
                self.scroll = 0;
                ScreenKey::Consumed
            }
            Key::Up => {
                self.scroll = self.scroll.saturating_sub(1);
                ScreenKey::Consumed
            }
            Key::Down => {
                self.scroll += 1;
                ScreenKey::Consumed
            }
            Key::PageUp => {
                self.scroll = self.scroll.saturating_sub(PAGE);
                ScreenKey::Consumed
            }
            Key::PageDown => {
                self.scroll += PAGE;
                ScreenKey::Consumed
            }
            _ => {
                self.close_top();
                ScreenKey::Closed
            }
        }
    }

    /// 작성창의 키(패리티 `e_ins`). 편집은 [`crate::editor`] 가 하고 여기서는 **화면이
    /// 어떻게 되는가**만 옮긴다.
    ///
    /// 닫는 길이 둘이고 뜻이 다르다: `Enter` 는 쓴 것을 패널에 넣고, `Esc` `Esc` 는 안
    /// 넣는다. **어느 쪽이든 초안은 남긴다** — `Esc` 는 "안 넣겠다"이지 "버리겠다"가 아니다.
    fn press_compose(&mut self, key: Key, mods: Mods) -> ScreenKey {
        let Some(editor) = self.editor.as_mut() else {
            // 버퍼 없이 화면만 떠 있는 상태는 만들지 않는다(`open_compose` 가 늘 함께
            // 세운다). 그래도 여기 오면 닫는 편이 키를 삼킨 채 굳는 것보다 낫다.
            self.close_top();
            return ScreenKey::Closed;
        };
        match editor.press(key, mods) {
            crate::editor::EditorKey::Consumed => ScreenKey::Consumed,
            crate::editor::EditorKey::Inject => {
                let text = editor.text();
                self.compose_draft = text.clone();
                self.close_compose();
                ScreenKey::Injected(text)
            }
            crate::editor::EditorKey::Cancel => {
                self.compose_draft = editor.text();
                self.close_compose();
                ScreenKey::Closed
            }
            // 팔레트를 **위에 얹는다** — 작성창은 스택에 남고, 거기서 무엇을 실행하든
            // 끝나면 다시 작성창이 최상단이다(파이썬 `open_command` 와 같다).
            crate::editor::EditorKey::OpenPalette => {
                self.open_palette();
                ScreenKey::Consumed
            }
        }
    }

    /// 작성창을 닫으면서 버퍼도 치운다. 안 치우면 다음에 열 때 지난 커서·선택이 살아난다.
    fn close_compose(&mut self) {
        self.editor = None;
        self.close_top();
    }
}

impl Screens {
    /// 목록형 화면의 키. 파이썬 클라의 탭 스위처와 같은 동선이다.
    ///
    /// `Tab`/`↓` 다음 · `Shift+Tab`/`↑` 이전 · `Enter` 확정 · 그 외 취소.
    /// **확정과 취소를 가르는 것이 요점**이다 — 고르는 동안에는 아무 일도 일어나지 않고,
    /// `Esc` 로 나가면 원래 탭이 그대로다(파이썬 클라와 같다).
    fn press_list(&mut self, key: Key) -> ScreenKey {
        match key {
            Key::Down | Key::Tab => {
                self.selected += 1;
                ScreenKey::Consumed
            }
            Key::Up | Key::BackTab => {
                self.selected = self.selected.saturating_sub(1);
                ScreenKey::Consumed
            }
            Key::Enter => {
                let picked = self.selected;
                self.close_top();
                ScreenKey::Chosen(picked)
            }
            Key::Escape => {
                self.close_top();
                ScreenKey::Closed
            }
            // 목록형 화면은 정본 `InfoScreen` 계열이 아니다(pytmux-181·273) — 관계없는
            // 키(문자 키·좌우·PageUp/Down 등)를 눌렀다고 조용히 닫히면 사용자가 의도치
            // 않게 화면을 잃는다. 정의된 키가 아니면 **삼킨다**.
            _ => ScreenKey::Consumed,
        }
    }
}

/// 떠 있는 판 **안의** 클릭 대상.
///
/// # 왜 core 가 아나
///
/// 자리는 뷰(레이아웃)가 재지만 **뜻은 여기서 정한다** — 크롬 클릭(`chrome::ClickTarget`)과
/// 같은 갈림이다. 두 뷰가 각자 해석하면 "설정 탭을 눌렀을 때 무슨 일이 나나"가 클라마다
/// 달라진다.
///
/// # 왜 이 넷뿐인가
///
/// 판 안에서 **키로 할 수 있는 일과 같은 것**만 둔다. 클릭이 키보다 더 할 수 있으면 그건
/// 도움말이 거짓말이 되는 길이다.
/// 명령 한 줄을 **이름과 인자**로 가른다 — 첫 공백에서 자른다.
///
/// # 왜 이 규칙의 주인이 core 인가 (pytmux-7)
///
/// 같은 줄을 세 곳이 읽는다: 팔레트가 거를 이름 · 훅이 돌릴 명령([`crate::hooks::resolve`])
/// · 뷰가 색을 달리 칠할 두 조각. 자리마다 자르면 **팔레트 목록과 입력줄이 갈린다** —
/// 사용자에게는 "이름은 맞는데 안 걸린다"로 보이고 그건 조용한 어긋남이다.
///
/// ⚠ 인자 쪽은 **더 안 쪼갠다**. 여러 낱말이면 통째로 하나다(`run-shell echo hi`) —
/// "무엇이 값인가"는 명령마다 다르고 그 지식은 여기 없다.
pub fn split_first_space(line: &str) -> (&str, &str) {
    match line.find(' ') {
        Some(i) => (&line[..i], line[i + 1..].trim_start()),
        None => (line, ""),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PanelTarget {
    /// 설정 화면 왼쪽 세로 탭 `i` 번째(`config::SETTINGS_CATS` 의 자리).
    SettingsCat(usize),
    /// 팔레트 카테고리 탭 `i` 번째(0 = `전체`).
    PaletteTab(usize),
    /// Status 판(정보 탭) `i` 번째. 키로는 `←→` 가 같은 일을 한다(pytmux-9 ⑶).
    InfoTab(usize),
    /// 지금 층의 `row` 번째 줄(메뉴·목록형).
    Row(usize),
    /// 확인 화면의 버튼([`CONFIRM_YES`]/[`CONFIRM_NO`]).
    ConfirmButton(usize),
    /// 설정 화면의 `row` 번째 줄 — **고르기만** 한다(§10-21ⓣ).
    ///
    /// [`Row`](PanelTarget::Row) 과 갈라 두는 이유: 저쪽은 고르고 **곧장 `Enter`** 인데,
    /// 설정에서 그러면 이름을 눌렀을 뿐인데 값이 바뀐다. 값을 바꾸는 것은 값칸의 일이다.
    SettingRow(usize),
    /// 설정 값칸의 **화살표**(스테퍼 `‹`·`›`) 또는 토글을 눌렀다 — 평소 `←→` 와 같은 길.
    SettingStep { row: usize, forward: bool },
    /// 설정 값칸의 **낱말을 직접 찍었다**(셀렉터).
    ///
    /// 키에는 없는 길이다: 화살표는 한 칸씩 도는데 마우스는 목표를 바로 가리킬 수 있다.
    /// 그래도 **값을 정하는 것은 core** 다([`crate::config::setting_pick_at`]) — 뷰는
    /// "몇 번째 낱말을 눌렀나"만 넘긴다.
    SettingChoice { row: usize, index: usize },
    /// 판 안 **단추**(§10-21ⓓ3 — 재시작 점검 판의 「지금 재시작」).
    ///
    /// 무엇이 일어나는지는 [`crate::Action`] 이 그대로 들고 있다 — 클릭에만 있는
    /// 지름길이 아니라 **팔레트·키가 이미 가는 그 길**이다. 그래서 확인 화면이나 드라이런
    /// 게이트를 건너뛰는 갈래가 생기지 않는다.
    Button(crate::Action),
}

/// 판 안 클릭이 그다음 **무엇이 되는가**.
///
/// 뜻을 bool 하나로 적던 자리다("`Enter` 를 태울까"). 설정 값칸이 생기면서 갈래가
/// 넷이 됐고, bool 로는 "←로 한 칸"과 "세 번째 낱말"을 구분할 수 없다.
///
/// ★ 갈래가 늘어도 **실행 경로는 키와 한 벌**이다 — 아래 셋 다 키가 이미 가는 길이고
/// (`Enter`·`←→`·값 고르기), 클릭에만 있는 지름길은 없다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PanelEffect {
    /// 커서만 옮겼다.
    Moved,
    /// 평소 `Enter` 경로를 그대로 탄다.
    Enter,
    /// 평소 `←→` 경로를 그대로 탄다(`true` = 오른쪽).
    Dir(bool),
    /// 설정 줄 `row` 의 `index` 번째 값을 골랐다 — 키의 값 고르기와 같은 표를 지난다.
    Pick { row: usize, index: usize },
    /// 판 안 단추를 눌렀다 — **평소 액션 경로**를 그대로 탄다(팔레트·키와 같은 길).
    Act(crate::Action),
}

impl Screens {
    /// 판 안을 클릭했다 — **커서를 그리로 옮기고**, 이어서 무엇이 되는지를 돌려준다.
    ///
    /// 클릭을 "옮기고 누르기"로 쪼개는 이유: 실행 경로가 키와 **한 벌**이 된다. 클릭에만
    /// 있는 지름길을 만들면 확인 화면을 건너뛰거나(파괴적 동작!) 층을 잘못 들어가는
    /// 갈래가 생긴다.
    pub fn panel_click(&mut self, target: PanelTarget) -> PanelEffect {
        match target {
            // §10-21ⓣ — 이름칸을 눌렀다. **값은 안 건드린다**(그건 값칸의 일이다).
            PanelTarget::SettingRow(row) => {
                self.selected = row;
                PanelEffect::Moved
            }
            PanelTarget::SettingStep { row, forward } => {
                self.selected = row;
                PanelEffect::Dir(forward)
            }
            PanelTarget::SettingChoice { row, index } => {
                self.selected = row;
                PanelEffect::Pick { row, index }
            }
            PanelTarget::SettingsCat(i) => {
                // 사이드바는 코어 분류 **뒤에 플러그인 분류**가 이어진 목록이다(정본과
                // 같은 차례) — 정적 표만 보면 `Claude` 탭 클릭이 조용히 무시된다.
                let cats = self.plugins.setting_cats();
                let Some(cat) = cats.get(i) else {
                    return PanelEffect::Moved;
                };
                if let Some(row) = self.plugins.setting_cat_first(cat) {
                    self.selected = row;
                }
                PanelEffect::Moved
            }
            PanelTarget::PaletteTab(i) => {
                if i > self.plugins.palette_cats().len() {
                    return PanelEffect::Moved;
                }
                self.palette_tab = i;
                self.selected = 0;
                PanelEffect::Moved
            }
            PanelTarget::InfoTab(i) => {
                // 탭 수는 뷰가 안다(내용이 정한다 — REC 탭은 플러그인이 있을 때만 선다).
                // 그래서 여기서는 자리만 세우고, 범위 맞추기는 `wrap_info_tab` 이 한다.
                self.info_tab = i;
                self.scroll = 0;    // 다른 탭의 스크롤 자리를 물려받으면 빈 화면이 뜬다
                PanelEffect::Moved
            }
            PanelTarget::Row(row) => {
                self.selected = row;
                PanelEffect::Enter
            }
            PanelTarget::ConfirmButton(button) => {
                self.confirm_pick = button.min(CONFIRM_NO);
                PanelEffect::Enter
            }
            // 판을 **닫지 않는다** — 재시작 점검은 누른 결과(드라이런 결과·알림)를 그
            // 자리에서 다시 보이는 판이다. 닫으면 방금 무엇이 됐는지 볼 곳이 없다.
            PanelTarget::Button(action) => PanelEffect::Act(action),
        }
    }

    /// 메뉴에서 들어가 있는 서브메뉴(없으면 최상위).
    pub fn menu_group(&self) -> Option<&'static str> {
        self.menu_group
    }

    /// 지금 그릴 메뉴 줄들. 뷰가 [`crate::menu_rows`] 를 직접 부르지 않고 이걸 쓰는 이유:
    /// **어느 층에 있는지**를 아는 것은 화면 상태이고, 그건 여기 한 곳에 있어야 한다.
    /// 플러그인 줄 수도 마찬가지라 여기서 함께 넘긴다.
    pub fn menu_rows(&self) -> Vec<crate::MenuRow> {
        crate::keymap::menu_rows(self.menu_group, self.plugins.menu_items.len())
    }

    /// 플러그인이 준 화면을 연다. `is_list` 면 고르는 화면, 아니면 읽는 화면이다.
    ///
    /// 이미 떠 있으면 **다시 열지 않는다** — 같은 판의 갱신(목록 새로고침)이 스크롤과
    /// 선택을 0 으로 되돌리면 사용자는 자기가 보던 자리를 잃는다.
    pub fn open_plugin_view(&mut self, is_list: bool) {
        self.plugin_list = is_list;
        if self.top() != Some(Screen::PluginView) {
            self.open(Screen::PluginView);
        }
    }

    /// 다열 판의 기하를 넣는다 — `(열당 줄 수, 열 수)`. 다열이 아니면 `(0, 1)`.
    ///
    /// ⛔ 여기서 커서를 안 건드린다. 창이 좁아져 열이 줄었다고 골라 둔 줄이 움직이면
    /// 사용자는 리사이즈만 했는데 다른 것을 지우게 된다.
    pub fn set_plugin_grid(&mut self, per_col: usize, cols: usize) {
        self.plugin_grid = (per_col, cols.max(1));
    }

    /// 서버가 부는 플러그인 표면을 갈아 끼운다(뷰가 상태 변화 때 부른다).
    ///
    /// 값이 그대로면 아무것도 안 한다 — 매 프레임 부르는 자리라, 같은 값을 다시 넣어
    /// 화면 상태(고른 줄 등)를 흔들 이유가 없다.
    pub fn set_plugins(&mut self, surface: crate::plugins::PluginSurface) {
        if self.plugins != surface {
            self.plugins = surface;
        }
    }

    /// 지금 아는 플러그인 표면.
    pub fn plugins(&self) -> &crate::plugins::PluginSurface {
        &self.plugins
    }

    /// `from` 에서 한 칸 움직인 뒤 **고를 수 있는** 줄. 구분선은 건너뛴다.
    ///
    /// 구분선에 커서가 서면 `Enter` 가 아무 일도 안 하고, 그건 "메뉴가 먹통"으로 읽힌다.
    fn menu_step(rows: &[crate::MenuRow], from: usize, down: bool) -> usize {
        let mut at = from;
        for _ in 0..rows.len() {
            at = if down {
                (at + 1).min(rows.len().saturating_sub(1))
            } else {
                at.saturating_sub(1)
            };
            if rows.get(at).is_some_and(crate::MenuRow::selectable) {
                return at;
            }
            // 끝에 닿았는데 그 줄이 구분선이면 되돌아가며 찾는다(맨 끝·맨 앞이 구분선인
            // 표를 넣어도 커서가 갇히지 않게).
            if (down && at + 1 >= rows.len()) || (!down && at == 0) {
                break;
            }
        }
        rows.iter()
            .enumerate()
            .filter(|(_, r)| r.selectable())
            .map(|(i, _)| i)
            .min_by_key(|i| i.abs_diff(from))
            .unwrap_or(from)
    }

    /// 메뉴의 키. 목록과 같되 **층을 오르내린다**.
    ///
    /// `Enter`/`→` 는 그룹이면 들어가고 항목이면 실행, `←` 는 한 층 나온다(최상위에서는
    /// 닫는다). 파이썬 메뉴와 같은 손이다 — 평면 31줄을 접은 것이 이 화면의 요점이라,
    /// 들어간 뒤 **나올 길**이 없으면 접은 것이 곧 가둔 것이 된다.
    fn press_menu(&mut self, key: Key) -> ScreenKey {
        let rows = self.menu_rows();
        // 표가 바뀌었을 수 있다 — 커서가 구분선이나 표 밖에 있으면 먼저 끌어온다.
        if !rows.get(self.selected).is_some_and(crate::MenuRow::selectable) {
            self.selected = Self::menu_step(&rows, self.selected, true);
        }
        match key {
            Key::Down | Key::Tab => {
                self.selected = Self::menu_step(&rows, self.selected, true);
                ScreenKey::Consumed
            }
            Key::Up | Key::BackTab => {
                self.selected = Self::menu_step(&rows, self.selected, false);
                ScreenKey::Consumed
            }
            Key::Left => {
                if self.menu_group.is_some() {
                    self.leave_menu_group();
                    ScreenKey::Consumed
                } else {
                    self.close_top();
                    ScreenKey::Closed
                }
            }
            Key::Enter | Key::Right => match rows.get(self.selected) {
                Some(crate::MenuRow::Group(group)) => {
                    self.menu_group = Some(group);
                    self.selected = 0;
                    ScreenKey::Consumed
                }
                Some(crate::MenuRow::Item(entry)) => {
                    let picked = self.selected;
                    // ★ **토글은 메뉴를 안 닫는다**(정본과 같다) — 토글은 보통 여러 개를
                    //   잇달아 만지고, 하나 누를 때마다 메뉴가 닫히면 다시 열어 같은 자리를
                    //   찾아야 한다. 돌려주는 값은 같으므로 뷰는 안 바뀐다.
                    if !crate::keymap::menu_is_toggle(entry.key) {
                        self.close_top();
                    }
                    ScreenKey::Chosen(picked)
                }
                // `→` 가 항목 위에서 눌렸거나 표가 비었다 — 아무 일도 안 한다.
                _ => ScreenKey::Consumed,
            },
            _ => {
                self.close_top();
                ScreenKey::Closed
            }
        }
    }

    /// 서브메뉴에서 최상위로. **나온 자리에 커서를 둔다** — 맨 위로 튕기면 방금 어디서
    /// 왔는지를 눈으로 다시 찾아야 한다.
    fn leave_menu_group(&mut self) {
        let Some(group) = self.menu_group.take() else {
            return;
        };
        self.selected = crate::keymap::menu_rows(None, self.plugins.menu_items.len())
            .iter()
            .position(|row| matches!(row, crate::MenuRow::Group(g) if *g == group))
            .unwrap_or(0);
    }

    /// 설정 화면의 키. 목록과 같지만 **`Enter` 가 닫지 않는다**.
    ///
    /// `Tab`/`Shift+Tab` 은 **카테고리 이동**이다(파이썬 설정 화면과 같은 동선 —
    /// `screen.settings_sub` 힌트의 "Tab/클릭 카테고리"). 줄 하나씩이 아니라 다음
    /// 카테고리의 첫 줄로 뛴다: 34줄을 Tab 으로 훑는 손은 없다.
    fn press_settings(&mut self, key: Key) -> ScreenKey {
        match key {
            Key::Down => {
                self.selected += 1;
                ScreenKey::Consumed
            }
            Key::Up => {
                self.selected = self.selected.saturating_sub(1);
                ScreenKey::Consumed
            }
            // 분류 이동은 **플러그인 줄까지 포함한** 목록 위에서 돈다 — 안 그러면
            // `Claude` 탭이 사이드바에 보이는데 Tab 으로는 못 간다.
            Key::Tab => {
                self.selected = self.plugins.setting_cat_step(self.selected, true);
                ScreenKey::Consumed
            }
            Key::BackTab => {
                self.selected = self.plugins.setting_cat_step(self.selected, false);
                ScreenKey::Consumed
            }
            // ←→ 는 **값 변경**이다(정본 힌트의 셋째 칸). `Enter` 와 갈라 두는 이유는
            // 선택지가 셋 이상인 줄 때문이다 — 한 방향뿐이면 하나 지나쳤을 때 한 바퀴를
            // 더 돌아야 한다.
            Key::Right => ScreenKey::AppliedDir(self.selected, true),
            Key::Left => ScreenKey::AppliedDir(self.selected, false),
            // Space 는 체크박스 목록의 손버릇이다(파이썬 플러그인 화면과 같다). 설정
            // 화면에서도 같은 뜻이라 갈라 두지 않는다.
            Key::Enter | Key::Char(' ') => ScreenKey::Applied(self.selected),
            _ => {
                self.close_top();
                ScreenKey::Closed
            }
        }
    }
}

// 두벌식 자모 → QWERTY 영문(정본 `pytmuxlib/clientutil.py` `_JAMO` 와 동형, pytmux-176).
fn jamo_to_qwerty_char(j: char) -> Option<char> {
    Some(match j {
        'ㅂ' => 'q', 'ㅈ' => 'w', 'ㄷ' => 'e', 'ㄱ' => 'r', 'ㅅ' => 't',
        'ㅛ' => 'y', 'ㅕ' => 'u', 'ㅑ' => 'i', 'ㅐ' => 'o', 'ㅔ' => 'p',
        'ㅁ' => 'a', 'ㄴ' => 's', 'ㅇ' => 'd', 'ㄹ' => 'f', 'ㅎ' => 'g',
        'ㅗ' => 'h', 'ㅓ' => 'j', 'ㅏ' => 'k', 'ㅣ' => 'l', 'ㅋ' => 'z',
        'ㅌ' => 'x', 'ㅊ' => 'c', 'ㅍ' => 'v', 'ㅠ' => 'b', 'ㅜ' => 'n',
        'ㅡ' => 'm',
        // 시프트(쌍자음/이중모음) → 대문자 영문
        'ㅃ' => 'Q', 'ㅉ' => 'W', 'ㄸ' => 'E', 'ㄲ' => 'R', 'ㅆ' => 'T',
        'ㅒ' => 'O', 'ㅖ' => 'P',
        _ => return None,
    })
}

// 복합 자모(겹받침·이중모음) → 그것을 만드는 두벌식 낱자 시퀀스(정본 `_COMPOUND_JAMO`).
fn compound_jamo_parts(j: char) -> Option<[char; 2]> {
    Some(match j {
        'ㅘ' => ['ㅗ', 'ㅏ'], 'ㅙ' => ['ㅗ', 'ㅐ'], 'ㅚ' => ['ㅗ', 'ㅣ'],
        'ㅝ' => ['ㅜ', 'ㅓ'], 'ㅞ' => ['ㅜ', 'ㅔ'], 'ㅟ' => ['ㅜ', 'ㅣ'],
        'ㅢ' => ['ㅡ', 'ㅣ'],
        'ㄳ' => ['ㄱ', 'ㅅ'], 'ㄵ' => ['ㄴ', 'ㅈ'], 'ㄶ' => ['ㄴ', 'ㅎ'],
        'ㄺ' => ['ㄹ', 'ㄱ'], 'ㄻ' => ['ㄹ', 'ㅁ'], 'ㄼ' => ['ㄹ', 'ㅂ'],
        'ㄽ' => ['ㄹ', 'ㅅ'], 'ㄾ' => ['ㄹ', 'ㅌ'], 'ㄿ' => ['ㄹ', 'ㅍ'],
        'ㅀ' => ['ㄹ', 'ㅎ'], 'ㅄ' => ['ㅂ', 'ㅅ'],
        _ => return None,
    })
}

fn jamo_to_q(j: char, out: &mut String) {
    if let Some(parts) = compound_jamo_parts(j) {
        for p in parts {
            out.push(jamo_to_qwerty_char(p).unwrap_or(p));
        }
    } else {
        out.push(jamo_to_qwerty_char(j).unwrap_or(j));
    }
}

const HANGUL_CHO: [char; 19] = [
    'ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ',
    'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ',
];
const HANGUL_JUNG: [char; 21] = [
    'ㅏ', 'ㅐ', 'ㅑ', 'ㅒ', 'ㅓ', 'ㅔ', 'ㅕ', 'ㅖ', 'ㅗ', 'ㅘ',
    'ㅙ', 'ㅚ', 'ㅛ', 'ㅜ', 'ㅝ', 'ㅞ', 'ㅟ', 'ㅠ', 'ㅡ', 'ㅢ', 'ㅣ',
];
// 0 번째는 종성 없음(정본 `_JONG` 의 `_` 자리).
const HANGUL_JONG: [Option<char>; 28] = [
    None, Some('ㄱ'), Some('ㄲ'), Some('ㄳ'), Some('ㄴ'), Some('ㄵ'), Some('ㄶ'),
    Some('ㄷ'), Some('ㄹ'), Some('ㄺ'), Some('ㄻ'), Some('ㄼ'), Some('ㄽ'), Some('ㄾ'),
    Some('ㄿ'), Some('ㅀ'), Some('ㅁ'), Some('ㅂ'), Some('ㅄ'), Some('ㅅ'), Some('ㅆ'),
    Some('ㅇ'), Some('ㅈ'), Some('ㅊ'), Some('ㅋ'), Some('ㅌ'), Some('ㅍ'), Some('ㅎ'),
];

/// 한글(두벌식 IME 로 잘못 입력된 영문)을 QWERTY 영문으로 되돌린다 — 정본
/// `pytmuxlib/clientutil.py` `hangul_to_qwerty` 와 동형(pytmux-176). 완성형 음절은
/// 초/중/종성으로 분해, 낱자/복합자모는 표로 변환. 비-한글은 그대로 둔다.
fn hangul_to_qwerty(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        let o = ch as u32;
        if (0xAC00..=0xD7A3).contains(&o) {
            let s = o - 0xAC00;
            let (cho, jung, jong) = (s / 588, (s / 28) % 21, s % 28);
            jamo_to_q(HANGUL_CHO[cho as usize], &mut out);
            jamo_to_q(HANGUL_JUNG[jung as usize], &mut out);
            if let Some(j) = HANGUL_JONG[jong as usize] {
                jamo_to_q(j, &mut out);
            }
        } else if jamo_to_qwerty_char(ch).is_some() || compound_jamo_parts(ch).is_some() {
            jamo_to_q(ch, &mut out);
        } else {
            out.push(ch);
        }
    }
    out
}

impl Screens {
    /// 한 줄 입력의 키. 글자는 쌓고, `Enter` 는 확정, `Esc` 는 취소다.
    fn press_prompt(&mut self, key: Key) -> ScreenKey {
        // 이력 후보가 떠 있으면 ↑↓ 는 고르기, Tab/→ 는 채우기다(파이썬 arghist 의
        // 손 — 후보가 없으면 이 키들은 아래 기본 팔로 떨어져 종전과 같다).
        let matches = self.prompt_matches().len();
        if matches > 0 {
            match key {
                Key::Down => {
                    self.prompt_pick =
                        Some(self.prompt_pick.map_or(0, |p| (p + 1) % matches));
                    return ScreenKey::Consumed;
                }
                Key::Up => {
                    self.prompt_pick =
                        Some(self.prompt_pick.map_or(matches - 1, |p| (p + matches - 1) % matches));
                    return ScreenKey::Consumed;
                }
                Key::Tab | Key::Right => {
                    let pick = self.prompt_pick.unwrap_or(0);
                    if let Some(text) = self.prompt_matches().get(pick) {
                        self.typed = (*text).to_owned();
                        self.prompt_cursor = self.typed.chars().count();
                        self.prompt_sel_anchor = None;
                    }
                    self.prompt_pick = None;
                    return ScreenKey::Consumed;
                }
                _ => {}
            }
        }
        match key {
            Key::Char(c) => {
                let mut chars = self.prompt_chars();
                if let Some((s, e)) = self.prompt_selection() {
                    chars.splice(s..e, std::iter::once(c));
                    self.prompt_cursor = s + 1;
                } else {
                    chars.insert(self.prompt_cursor.min(chars.len()), c);
                    self.prompt_cursor += 1;
                }
                self.typed = chars.into_iter().collect();
                self.prompt_sel_anchor = None;
                // 글자가 바뀌면 좁혀진 목록도 바뀐다 — 낡은 선택을 버린다.
                self.prompt_pick = None;
                ScreenKey::Consumed
            }
            Key::Backspace => {
                let mut chars = self.prompt_chars();
                if let Some((s, e)) = self.prompt_selection() {
                    chars.splice(s..e, std::iter::empty());
                    self.prompt_cursor = s;
                } else if self.prompt_cursor > 0 && self.prompt_cursor <= chars.len() {
                    chars.remove(self.prompt_cursor - 1);
                    self.prompt_cursor -= 1;
                }
                self.typed = chars.into_iter().collect();
                self.prompt_sel_anchor = None;
                self.prompt_pick = None;
                ScreenKey::Consumed
            }
            // 커서 이동 넷 + Shift 짝(선택, pytmux-174) — 정본 Textual `Input` 이 이미
            // 하는 편집이다. 방향키만 누르면 선택이 풀린다(정본과 같다).
            Key::Left => {
                self.prompt_cursor = self.prompt_cursor.saturating_sub(1);
                self.prompt_sel_anchor = None;
                ScreenKey::Consumed
            }
            Key::Right => {
                self.prompt_cursor = (self.prompt_cursor + 1).min(self.prompt_chars().len());
                self.prompt_sel_anchor = None;
                ScreenKey::Consumed
            }
            Key::Home => {
                self.prompt_cursor = 0;
                self.prompt_sel_anchor = None;
                ScreenKey::Consumed
            }
            Key::End => {
                self.prompt_cursor = self.prompt_chars().len();
                self.prompt_sel_anchor = None;
                ScreenKey::Consumed
            }
            Key::ShiftLeft => {
                self.prompt_sel_anchor.get_or_insert(self.prompt_cursor);
                self.prompt_cursor = self.prompt_cursor.saturating_sub(1);
                ScreenKey::Consumed
            }
            Key::ShiftRight => {
                self.prompt_sel_anchor.get_or_insert(self.prompt_cursor);
                self.prompt_cursor = (self.prompt_cursor + 1).min(self.prompt_chars().len());
                ScreenKey::Consumed
            }
            Key::ShiftHome => {
                self.prompt_sel_anchor.get_or_insert(self.prompt_cursor);
                self.prompt_cursor = 0;
                ScreenKey::Consumed
            }
            Key::ShiftEnd => {
                self.prompt_sel_anchor.get_or_insert(self.prompt_cursor);
                self.prompt_cursor = self.prompt_chars().len();
                ScreenKey::Consumed
            }
            Key::Enter => {
                let answer = std::mem::take(&mut self.typed);
                let asked = self.asking;
                self.close_top();
                match asked {
                    Some(prompt) => ScreenKey::Answered(prompt, answer),
                    // 무엇을 물었는지 모르면 대답을 쓸 데가 없다 — 조용히 닫는다.
                    None => ScreenKey::Closed,
                }
            }
            Key::Escape => {
                // **친 글자는 버린다** — 반쯤 친 이름이 남아 있다가 다음에 열 때
                // 튀어나오면 사용자가 자기가 뭘 하는지 모른다.
                self.close_top();
                ScreenKey::Closed
            }
            // 그 밖의 키(pytmux-174·273)는 삼킨다 — 정본 `Input` 위젯도 모르는 키에
            // 아무 일도 안 한다. 편집 중 화면이 조용히 사라지는 쪽이 훨씬 나쁘다.
            _ => ScreenKey::Consumed,
        }
    }

    /// 팔레트의 키. 글자는 **필터**로 쌓이고 방향키는 선택을 옮긴다.
    ///
    /// 필터가 바뀌면 선택을 맨 위로 되돌린다 — 안 그러면 세 글자를 친 뒤 남은 항목이
    /// 하나인데 선택은 5번째를 가리키고 있어 `Enter` 가 아무 일도 안 한다.
    fn press_palette(&mut self, key: Key) -> ScreenKey {
        // 탭 수 = `전체` + 카테고리들. 순환한다(정본 CommandListScreen 과 같다 — 탭이
        // 여덟이라 끝에서 막히면 어느 화살표가 살아 있는지 매번 시험해야 한다).
        let tabs = self.plugins.palette_cats().len() + 1;
        match key {
            Key::Char(c) => {
                self.typed.push(c);
                // 팔레트는 이미 고정 ':' 프리픽스를 화면에 그리고 있다(정본
                // `on_input_changed`, pytmux-175) — 맨 앞에서부터 이어지는 ':' 는
                // 버리고(중간에 친 ':' 는 보존) 필터에 반영한다.
                if self.typed.starts_with(':') {
                    self.typed = self.typed.trim_start_matches(':').to_owned();
                }
                // 한글 IME 를 켠 채 명령 이름을 치면 자모/음절이 그대로 들어온다
                // (pytmux-176) — 정본 `hangul_to_qwerty` 와 동치인 변환으로 되돌린다.
                // 공백이 섞이면(=인자 구간) 건드리지 않아 한글 인자는 보존된다.
                if !self.typed.contains(' ') {
                    self.typed = hangul_to_qwerty(&self.typed);
                }
                self.selected = 0;
                self.rehome_palette_tab();
                ScreenKey::Consumed
            }
            Key::Backspace => {
                self.typed.pop();
                self.selected = 0;
                self.rehome_palette_tab();
                ScreenKey::Consumed
            }
            // ←→ 가 카테고리 탭이다(정본과 같은 손). 설정 화면이 `Tab` 을 쓰는 것과
            // 갈리는 것도 정본 그대로다 — 팔레트는 `Tab` 을 이미 ↓ 로 쓰고 있다.
            Key::Right => {
                self.palette_tab = (self.palette_tab + 1) % tabs;
                self.selected = 0;
                ScreenKey::Consumed
            }
            Key::Left => {
                self.palette_tab = (self.palette_tab + tabs - 1) % tabs;
                self.selected = 0;
                ScreenKey::Consumed
            }
            Key::Down | Key::Tab => {
                self.selected += 1;
                ScreenKey::Consumed
            }
            Key::Up | Key::BackTab => {
                self.selected = self.selected.saturating_sub(1);
                ScreenKey::Consumed
            }
            Key::Enter => {
                let picked = self.selected;
                self.close_top();
                ScreenKey::Chosen(picked)
            }
            _ => {
                self.close_top();
                ScreenKey::Closed
            }
        }
    }

    /// 친 글자가 **지금 탭에만 안 걸리면** 걸리는 첫 탭으로 옮겨 준다(정본 `_rebuild`).
    ///
    /// 이게 없으면 `패널` 탭에서 `kill-tab` 을 치는 순간 화면이 "맞는 명령이 없다"가 되고,
    /// 사용자는 이름을 잘못 안 줄 안다 — 실제로는 옆 탭에 있다.
    fn rehome_palette_tab(&mut self) {
        // 플러그인 줄도 함께 센다 — `mdir` 을 치면 `탐색` 탭으로 옮겨 가야 한다.
        self.palette_tab =
            self.plugins.palette_tab_with_results(self.palette_tab, &self.typed, |_| None);
    }

    /// 예/아니오. **버튼 둘**을 ←→ 로 오가고 `Enter` 는 고른 버튼을 누른다(정본과 같다).
    /// `y`/`n` 은 그 위의 지름길이고, 그 밖의 키는 전부 아니오다.
    ///
    /// **기본이 '아니오'인 것이 요점**이다 — 이 화면은 되돌릴 수 없는 것 앞에 서므로,
    /// 헷갈려서 아무 키나 눌렀을 때 일어나는 일이 "아무 일도 안 남"이어야 한다.
    ///
    /// ⚠ 2026-07-31 에 **`Enter` 의 뜻이 바뀌었다**: 종전에는 Enter 가 곧 '예'였다.
    /// 정본은 포커스가 '아니오'에서 시작하므로 Enter 도 '아니오'다 — 되돌릴 수 없는
    /// 화면에서 가장 반사적으로 눌리는 키가 '예'인 것은 이 화면의 취지와 어긋났다.
    /// 예로 확정하려면 `y` 를 치거나 ←/→ 로 '예' 버튼을 고르고 Enter 를 친다.
    fn press_confirm(&mut self, key: Key) -> ScreenKey {
        // 버튼은 둘뿐이라 방향키·Tab 은 전부 "반대쪽으로".
        if matches!(key, Key::Left | Key::Right | Key::Tab | Key::BackTab) {
            self.confirm_pick = 1 - self.confirm_pick;
            return ScreenKey::Consumed;
        }
        // 정본 `ConfirmScreen.on_key` 는 `escape`·`y/Y`·`n/N`·`enter`·`left/right/tab`
        // 만 먹는다(pytmux-273 ③) — 그 밖의 키는 갈래가 없어 화면이 그대로 남는다.
        // ⚠ 종전에는 이 다섯 밖의 모든 키가 "아니오"로 닫혔다: 위험 자체는 낮지만
        // (둘 다 '예'로는 안 간다), 되돌릴 수 없는 것 앞에서 오타 하나로 물음이
        // 사라지면 사용자는 자기가 무엇을 답했는지 모른다.
        let close_as = match key {
            Key::Char('y') | Key::Char('Y') => Some(true),
            Key::Char('n') | Key::Char('N') | Key::Escape => Some(false),
            Key::Enter => Some(self.confirm_pick == CONFIRM_YES),
            _ => None,
        };
        let Some(yes) = close_as else {
            return ScreenKey::Consumed;
        };
        let asked = self.asking;
        self.close_top();
        match (yes, asked) {
            (true, Some(prompt)) => ScreenKey::Answered(prompt, String::from("y")),
            _ => ScreenKey::Closed,
        }
    }
}

/// 확인 화면의 '예' 버튼 자리.
pub const CONFIRM_YES: usize = 0;
/// 확인 화면의 '아니오' 버튼 자리 — **여기서 시작한다**.
pub const CONFIRM_NO: usize = 1;

/// 페이지 키가 움직이는 줄 수. 화면 높이를 아는 것은 뷰지만, 그 값을 물어 오게 하면
/// 두 뷰가 다른 값을 쓰기 시작한다 — 읽는 화면이라 고정값으로 충분하다.
const PAGE: usize = 10;

#[cfg(test)]
#[path = "screens_tests.rs"]
mod tests;
