//! 상호작용 계약 축 — **새 표면은 정본과 같은 «반응»부터 갖춘다**(pytmux-185).
//!
//! # 왜 표(`parity.rs`) 하나로는 모자라나
//!
//! 옆의 패리티 래칫은 *"그 표면이 있나"* 를 센다(`Done`/`Partial`/`Missing`). 그 축에서
//! 만점인 화면도 **누르면 다르게 군다** — 있는 것과 같게 구는 것은 다른 질문이고, 지금까지
//! 후자를 재는 자는 아무 데도 없었다. 2026-08-09 하루에 등록된 GUI 제보 아홉 중 여섯이
//! 그 갈림이었다(pytmux-173·174·175·176·181·184). 전부 "화면은 있는데 키가 다르게 먹는다"다.
//!
//! # 이 축이 재는 것 — 「제 것이 아닌 키」 하나
//!
//! 계약 전체(키 반응·취소 조건·포커스 이동)를 한 축이 다 잴 수는 없다. 여기서 재는 것은
//! **그중 하나**, 화면이 자기 것이 아닌 키를 만났을 때 무엇을 하나다. 그 하나를 고른 이유는
//! 제보가 그 자리에 몰렸기 때문이다 — pytmux-174 는 `Home`·`End`·`←→` 가 입력 팝업을
//! 닫았고, pytmux-181 은 힌트에 적힌 셋 말고 **아무 키나** 플러그인 팝업을 닫았다.
//! 그 둘의 근본 원인은 각각 [`base::Screens`] 의 `press_prompt`·`press_list` 에 있는
//! `_ => close_top()` 한 팔이다.
//!
//! ⚠ **이 축은 "정본과 같다"를 증명하지 않는다.** 재는 것은 ⑴ 우리 코드가 실제로 무엇을
//! 하나(측정)와 ⑵ 그것이 정본과 같은지에 대한 **선언**이다. 선언은 근거를 요구받지만
//! (`Verdict::Same` 은 무엇이 그것을 쟀는지 적어야 한다) 선언이 곧 측정은 아니다 —
//! 정본 쪽을 **기계로** 읽는 자는 아직 없고, 그 자를 만드는 것이 pytmux-33 의 일이다.
//!
//! ☠ **이 표를 처음 채우면서 정본 소스를 손으로 읽어 대조했더니, 「같다」로 적으려던 여덟
//! 줄 중 일곱이 거짓이었다**(2026-08-17 · 그 결과가 pytmux-273 이다). 갈린 자리 셋:
//! ⑴ 읽는 판 다섯의 `Home`·`End`(정본 `InfoScreen` 은 커서를 옮기고 우리는 판을 닫는다)
//! ⑵ 알림 이력(정본은 `Esc`·`c` 만 먹고 **모르는 키를 무시한다**) ⑶ 확인 화면(정본은 모르는
//! 키에 **아무 일도 안 한다**). ⇒ **「정본과 같아 보인다」를 근거로 쓰지 마라** — 그 판의
//! `on_key` 를 열어 보는 값이 이만큼 싸다.
//!
//! # 규칙 넷
//!
//! 1. **빠짐이 없다.** 표는 [`base::Screen::all()`] 과 집합이 정확히 같다. 화면을 하나
//!    더하면 그 변형이 `all()` 에서 컴파일로 강제되고, 여기서는 그 줄을 적을 때까지 운다.
//! 2. **측정한 칸과 선언한 칸을 섞지 않는다.** [`Contract::stray`] 는 **코드에 물어서**
//!    맞춰야 하고(`the_declared_stray_key_is_what_the_code_actually_does`),
//!    [`Contract::canon`] 은 사람이 적는다.
//! 3. **선언에는 근거를 단다.** 허용된 갈림은 [`Ground`] 에 있는 부류여야 하고(pytmux-33 이
//!    정한 셋 + 정본에 짝이 없는 자리), 「같다」는 **무엇이 그것을 쟀는지**를, 결함은 이슈
//!    번호를, 아직 못 잰 줄은 **무엇을 재야 하나**를 적는다.
//! 4. ⛔ **못 잰 줄 수는 올리지 않는다.** 지금 있는 것을 다 못 쟀다는 사실이 **새 화면을
//!    안 재도 된다는 뜻은 아니다** — 그것이 pytmux-185 가 요청한 계약의 알맹이다.

use base::keys::{Key, Mods};
use base::screens::{Prompt, Screen, ScreenKey, Screens};

/// 화면이 **자기 것이 아닌 키**를 만나면 무엇을 하나. 이 칸은 **측정값**이다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Stray {
    /// 닫힌다. 읽는 판에서는 계약이고(정본 `InfoScreen`), 고르는 판에서는 대개 결함이다.
    Closes,
    /// 아무 일도 안 난다 — 판은 그대로 떠 있다.
    Stays,
}

/// 갈림의 **근거 부류** — pytmux-33 이 정한 셋과, 정본에 짝이 아예 없는 자리.
///
/// ⛔ 여기 없는 사유로는 갈림을 허용하지 않는다. "그 밖의 갈림은 결함으로 본다"가 그
/// 이슈가 정한 기준이고, 부류를 늘리는 것은 그 기준을 고치는 일이라 사람이 정한다.
///
/// ⛔ **아직 안 쓰는 부류를 지우지 마라**(그래서 `allow`). 이 열거는 «지금 쓰이는 사유»가
/// 아니라 **허용되는 사유의 전부**다 — 셋을 지우면 다음 사람은 자기 사유를 새로 지어낼 수
/// 있게 되고, 그때 이 표는 갈림을 세는 자가 아니라 갈림을 정당화하는 자리가 된다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[allow(dead_code)]
enum Ground {
    /// ⓐ 단말이 전달할 수 없는 키(`Ctrl+Tab` 계열 · 키 뗌으로 확정하는 동선).
    TerminalCannot,
    /// ⓑ 픽셀 단위 그림(실제 선·둥근 모서리·이미지 미리보기).
    Pixels,
    /// ⓒ OS 창 통합(타이틀바 등).
    OsWindow,
    /// 정본에 짝이 없다 — 이 판은 GUI 가 처음 만든 것이다.
    ///
    /// ⛔ 이 근거는 [`Screen::canon_class`] 가 `None` 이라고 말하는 줄에만 쓸 수 있다
    /// (`a_native_only_row_is_one_base_agrees_is_native_only`). 짝이 있는 화면에
    /// "정본에 없다"고 적는 것이 이 표가 조용히 거짓이 되는 첫걸음이다.
    NativeOnly,
}

/// 정본과의 갈림을 어떻게 보나.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Verdict {
    /// 같다. **무엇이 그것을 쟀는지** 적는다(시험 이름·픽스처·리포트).
    Same(&'static str),
    /// 갈렸고 허용된다. 근거 부류를 댄다.
    Allowed(Ground),
    /// 갈렸고 결함이다. **이슈 번호**를 댄다(`pytmux-###`).
    Defect(&'static str),
    /// 아직 정본 쪽을 안 쟀다. [`Contract::note`] 에 **무엇을 재야 하나**를 적는다.
    ///
    /// ⛔ 이것은 기본값이 아니라 **빚**이다 — 규칙 4 를 볼 것.
    Unmeasured,
}

struct Contract {
    screen: Screen,
    /// **측정값** — 규칙 2.
    stray: Stray,
    canon: Verdict,
    /// 무엇을 하는 화면이고, 위 판정이 무엇을 뜻하나.
    note: &'static str,
}

const fn c(screen: Screen, stray: Stray, canon: Verdict, note: &'static str) -> Contract {
    Contract {
        screen,
        stray,
        canon,
        note,
    }
}

use Stray::{Closes, Stays};
use Verdict::{Allowed, Defect, Same, Unmeasured};

/// 화면 하나 = 줄 하나. 차례는 [`Screen::all()`] 과 같다(사람이 견주기 쉽게).
static CONTRACTS: &[Contract] = &[
    c(
        Screen::Keys,
        Closes,
        Defect("pytmux-273"),
        "읽는 판(키 도움말). 「아무 키나 닫기」까지는 정본 `InfoScreen` 과 같은데 \
         **`Home`·`End` 가 갈렸다** — 정본은 그 둘로 커서를 맨 위/아래로 옮기고(`_NAV_KEYS`) \
         우리는 그 둘로도 판을 닫는다",
    ),
    c(
        Screen::ClaudeDetail,
        Closes,
        Allowed(Ground::NativeOnly),
        "Claude 플랜 전문·거부 사유 — 정본에 짝이 없는 판이다",
    ),
    c(
        Screen::Tabs,
        Closes,
        Unmeasured,
        "탭 스위처. 정본 `TabSwitcherScreen` 이 «제 것 아닌 키» 를 어떻게 하는지 안 쟀다 — \
         `press_list` 를 지나므로 pytmux-181 이 참이면 이 줄도 같이 움직인다",
    ),
    c(
        Screen::Tree,
        Closes,
        Unmeasured,
        "세션·탭·패널 개요. 정본 `ChooseTreeScreen` 쪽을 안 쟀다(같은 `press_list`)",
    ),
    c(
        Screen::Buffers,
        Closes,
        Unmeasured,
        "페이스트 버퍼 목록. 정본 `ChooseBufferScreen` 쪽을 안 쟀다(같은 `press_list`)",
    ),
    c(
        Screen::Prompt,
        Closes,
        Defect("pytmux-174"),
        "한 줄 입력. `Home`·`End`·`←→`·`Shift+` 선택 키가 편집이 아니라 **취소**로 간다 — \
         정본은 그 키로 커서를 옮긴다(`press_prompt` 의 `_ =>` 한 팔)",
    ),
    c(
        Screen::Confirm,
        Closes,
        Defect("pytmux-273"),
        "예/아니오. 기본이 '아니오'인 것은 정본과 같은데, **모르는 키에서 갈렸다** — \
         정본 `ConfirmScreen.on_key` 는 esc·y/n·Enter·←→·Tab 만 먹고 그 밖의 키에는 \
         **아무 일도 안 한다**(판이 그대로 있다). 우리는 그것을 '아니오'로 확정하고 닫는다",
    ),
    c(
        Screen::Commands,
        Closes,
        Defect("pytmux-175"),
        "명령 팔레트. 닫기 자체는 정본과 같아 보이나, **글자를 받는 방식**이 갈렸다 — \
         맨 앞 `:` 를 정본은 버리고 우리는 넣는다(pytmux-175) · 한글 자모를 정본은 \
         QWERTY 로 되돌리고 우리는 안 되돌린다(pytmux-176)",
    ),
    c(
        Screen::Version,
        Closes,
        Defect("pytmux-273"),
        "읽는 판(서버·클라 판) — `Keys` 와 같은 `InfoScreen` 이라 `Home`·`End` 가 같이 갈렸다",
    ),
    c(
        Screen::ShellOutput,
        Closes,
        Defect("pytmux-273"),
        "읽는 판(`run-shell` 결과) — 같은 `InfoScreen`(`Home`·`End`)",
    ),
    c(
        Screen::RestartCheck,
        Closes,
        Defect("pytmux-273"),
        "읽는 판(재시작 점검) — 같은 `InfoScreen`(`Home`·`End`)",
    ),
    c(
        Screen::MergeRemote,
        Closes,
        Unmeasured,
        "원격 탭 합치기. `h`/`v` 는 우리 것이 맞는데(정본과 같다) 나머지 키는 안 쟀다",
    ),
    c(
        Screen::Layouts,
        Closes,
        Unmeasured,
        "레이아웃 프리셋. 정본 `ChooseLayoutScreen` 쪽을 안 쟀다(같은 `press_list`)",
    ),
    c(
        Screen::Notices,
        Closes,
        Defect("pytmux-273"),
        "지나간 알림. **읽는 판이 아니었다** — 정본 `NoticeHistoryScreen` 은 `Esc`·`c`(전문 \
         복사)만 먹고 그 밖의 키를 **무시하며** `Enter` 로 전문을 펼친다(설계 G3). 우리는 \
         아무 키나 닫고 `c`·`Enter` 가 없다",
    ),
    c(
        Screen::Menu,
        Closes,
        Unmeasured,
        "F10 메뉴. `←` 로 그룹을 빠져나오는 것까지는 정본과 맞췄는데, 그 밖의 키는 안 쟀다",
    ),
    c(
        Screen::Plugins,
        Closes,
        Unmeasured,
        "플러그인 켜고끄기. `Enter` 가 안 닫는 것은 정본과 같다 — 나머지 키는 안 쟀다",
    ),
    c(
        Screen::PluginView,
        Closes,
        Defect("pytmux-181"),
        "플러그인이 준 판(`mdir`·`claude-settings`). 목록·폼(`is_list`)에서 힌트에 적힌 \
         셋 말고 **아무 키나** 닫는다 — 기대는 «Esc 로만 닫힌다»다(`press_list` 의 `_ =>` \
         한 팔). ★ 같은 변형의 **글 판**(`kind:\"text\"` · usage limit)에는 스크롤 상한이 \
         없어 내용 끝을 넘어 빈 화면까지 간다 — pytmux-184 ⑵",
    ),
    c(
        Screen::Options,
        Closes,
        Unmeasured,
        "인자 폼. ↑↓ 줄 · ←→ 값까지는 정본 `CommandOptionsScreen` 과 맞췄고 나머지는 안 쟀다",
    ),
    c(
        Screen::Hooks,
        Closes,
        Defect("pytmux-273"),
        "읽는 판(걸어 둔 훅) — 같은 `InfoScreen`(`Home`·`End`)",
    ),
    c(
        Screen::InfoTabs,
        Closes,
        Unmeasured,
        "탭 있는 읽기 판. ←→ 가 탭이고 ↑↓ 가 스크롤인 것까지는 정본 `InfoTabsScreen` 과 \
         맞췄고, 나머지 키는 안 쟀다. ⛔ **usage limit 팝업(pytmux-184)은 이 판이 아니다** — \
         GUI 에서 그것은 `PluginView` 의 글 판이다(그 줄을 볼 것)",
    ),
    c(
        Screen::Compose,
        Stays,
        Same("정본도 키의 주인이 `_ComposeTextArea._on_key` 하나다(그 판의 `BINDINGS` 밖 키는 TextArea 가 삼킨다) — 우리 쪽은 `base::editor` 이고 editor_tests 가 편집 키를 잰다"),
        "여러 줄 작성창. **제 것 아닌 키가 없다** — 편집기가 모르는 키는 조용히 버린다",
    ),
    c(
        Screen::Settings,
        Closes,
        Unmeasured,
        "설정. `Enter` 가 안 닫는 것은 정본과 같다 — 나머지 키는 안 쟀다. \
         네이티브 위젯으로 다시 그리는 일은 pytmux-182 다(그것은 이 축이 아니라 그림 축)",
    ),
    c(
        Screen::Summary,
        Closes,
        Allowed(Ground::NativeOnly),
        "블록·Claude 요약 판 — 정본에 짝이 없다(§10-21ⓓ)",
    ),
];

/// 지금 점수. **양방향 래칫**이다 — 늘어도 줄어도 여기를 고쳐야 한다(`parity.rs` 규칙 2).
///
/// `(같다, 허용된 갈림, 결함, 못 쟀다)`.
static SCORE: (usize, usize, usize, usize) = (1, 2, 10, 10);

/// ⛔ **이 수는 올리지 않는다**(규칙 4).
///
/// 못 잰 줄이 열인 것은 이 축을 세우기 전의 빚이다. 그 빚이 **새 화면의 면허가 되면**
/// 이 표는 아무 일도 안 한다 — 화면을 더하는 사람은 `Unmeasured` 로 적고 지나가면 되고,
/// 그러면 pytmux-185 가 막으려던 바로 그 재생산이 표 안에서 일어난다.
///
/// 줄일 때는 이 수도 함께 내린다(그래야 "언제 무엇을 쟀나"가 이력에 남는다).
const UNMEASURED_CEILING: usize = 10;

/// 재는 데 쓰는 **제 것 아닌 키**. F5 를 고른 이유는 어느 판도 F 키를 자기 것이라고
/// 적지 않았기 때문이다(스펙이 F 키를 쓰는 플러그인 판은 뷰가 먼저 가로챈다 — 이 축은
/// core 의 라우팅을 잰다).
const STRAY: Key = Key::Function(5);

/// 화면 하나를 **제대로** 연다.
///
/// `open()` 한 줄로 열리지 않는 판이 여럿이다(작성창은 버퍼가, 인자 폼은 명령이, 플러그인
/// 판은 목록인지 글인지가 함께 서야 한다). 반쯤 선 판에 키를 먹이면 이 축이 재는 것은
/// 제품이 아니라 **테스트가 만든 이상한 상태**가 된다.
///
/// ⛔ 와일드카드가 없다 — 화면이 늘면 여기가 안 컴파일된다(그때 여는 길을 적게 된다).
fn opened(screen: Screen) -> Screens {
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
        | Screen::ClaudeDetail
        | Screen::Tree
        | Screen::Buffers
        | Screen::Version
        | Screen::ShellOutput
        | Screen::RestartCheck
        | Screen::MergeRemote
        | Screen::Layouts
        | Screen::Notices
        | Screen::Menu
        | Screen::Plugins
        | Screen::Hooks
        | Screen::Settings
        | Screen::Summary => screens.open(screen),
    }
    assert_eq!(
        screens.top(),
        Some(screen),
        "{screen:?} 를 열었는데 맨 위가 그것이 아니다"
    );
    screens
}

#[test]
fn every_screen_declares_its_interaction_contract() {
    // 규칙 1 — 빠짐도 군더더기도 없다. 화면을 더하면서 이 표를 안 건드리는 길이 없어야
    // 하고, 그것이 pytmux-185 가 요청한 «앞으로 차이가 새로 안 생기게 만드는 일»이다.
    let declared: Vec<Screen> = CONTRACTS.iter().map(|x| x.screen).collect();
    let missing: Vec<&Screen> = Screen::all()
        .iter()
        .filter(|s| !declared.contains(s))
        .collect();
    assert!(
        missing.is_empty(),
        "화면이 있는데 상호작용 계약이 없다: {missing:?}\n\
         새 화면이면 «제 것 아닌 키가 어떻게 되나»와 «정본과 같은가»를 적을 것 — \
         적지 않으면 그 갈림은 아무 데도 안 남는다(pytmux-185)."
    );
    let extra: Vec<&Screen> = declared
        .iter()
        .filter(|s| !Screen::all().contains(s))
        .collect();
    assert!(extra.is_empty(), "표에만 있고 화면에는 없다: {extra:?}");
    assert_eq!(
        declared.len(),
        Screen::all().len(),
        "같은 화면이 두 줄이다 — 두 줄이 갈리면 어느 쪽이 계약인지 알 수 없다"
    );
}

#[test]
fn the_declared_stray_key_is_what_the_code_actually_does() {
    // 규칙 2 — 이 칸은 **선언이 아니라 측정**이다. 배선이 바뀌면 여기가 먼저 운다.
    for contract in CONTRACTS {
        let mut screens = opened(contract.screen);
        let outcome = screens.press(STRAY, Mods::NONE);
        assert!(
            outcome.is_some(),
            "{:?}: 판이 떠 있는데 키가 **새어 나갔다** — 뒤 패널로 가는 키가 있으면 \
             사용자는 자기가 무엇을 조작하는지 알 수 없다",
            contract.screen
        );
        let actual = if screens.is_open() {
            Stray::Stays
        } else {
            Stray::Closes
        };
        assert_eq!(
            actual, contract.stray,
            "{:?}: 제 것 아닌 키({STRAY:?})의 결과가 표와 다르다 — 배선을 바꿨으면 \
             표의 그 줄과 `SCORE` 를 **같은 CL 에서** 옮길 것",
            contract.screen
        );
        // 닫혔다면 그 사실이 결과에도 실려야 한다 — 닫는 키가 패널에도 가면 판을
        // 닫으려던 `q` 가 셸에 찍힌다(`the_closing_key_never_reaches_the_pane` 과 같은 규율).
        if actual == Stray::Closes {
            assert_eq!(
                outcome,
                Some(ScreenKey::Closed),
                "{:?}: 닫혔는데 결과가 `Closed` 가 아니다",
                contract.screen
            );
        }
    }
}

#[test]
fn a_verdict_names_its_ground() {
    // 규칙 3 — 근거 없는 판정은 다음 사람에게 아무 말도 안 한다.
    for contract in CONTRACTS {
        let screen = contract.screen;
        assert!(
            !contract.note.is_empty(),
            "{screen:?}: 무엇을 하는 화면인지 적혀 있지 않다"
        );
        match contract.canon {
            Same(evidence) => assert!(
                !evidence.is_empty(),
                "{screen:?}: 「같다」인데 **무엇이 그것을 쟀는지**가 없다 — \
                 근거 없는 「같다」는 안 잰 것과 구별되지 않는다"
            ),
            Defect(issue) => assert!(
                issue.starts_with("pytmux-") && issue["pytmux-".len()..].parse::<u32>().is_ok(),
                "{screen:?}: 결함인데 이슈 번호가 아니다({issue:?}) — \
                 번호가 없으면 고쳐진 뒤에도 이 줄이 남는다"
            ),
            // 부류는 타입이 이미 좁혔다(`Ground` 밖의 사유를 못 적는다).
            Allowed(_) => {}
            Unmeasured => assert!(
                contract.note.contains("안 쟀다") || contract.note.contains("못 쟀다"),
                "{screen:?}: 못 쟀다고 적었으면 **무엇을 재야 하나**가 설명에 있어야 한다"
            ),
        }
    }
}

#[test]
fn a_native_only_row_is_one_base_agrees_is_native_only() {
    // 「정본에 짝이 없다」는 [`Screen::canon_class`] 가 이미 아는 사실이다. 두 곳이 서로
    // 다른 답을 하면 그중 하나는 조용히 거짓이 된다 — 이 표 쪽이 거짓일 때가 특히 나쁘다
    // (갈림이 «허용»으로 덮인다).
    for contract in CONTRACTS {
        if matches!(contract.canon, Allowed(Ground::NativeOnly)) {
            assert!(
                contract.screen.canon_class().is_none(),
                "{:?}: 「정본에 짝이 없다」고 적었는데 base 는 정본 클래스 {:?} 를 안다",
                contract.screen,
                contract.screen.canon_class()
            );
        }
    }
}

#[test]
fn the_score_moves_only_on_purpose() {
    let mut score = (0, 0, 0, 0);
    for contract in CONTRACTS {
        match contract.canon {
            Same(_) => score.0 += 1,
            Allowed(_) => score.1 += 1,
            Defect(_) => score.2 += 1,
            Unmeasured => score.3 += 1,
        }
    }
    assert_eq!(
        score, SCORE,
        "상호작용 계약 점수가 달라졌다 — 움직였으면 **같은 CL 에서** `SCORE` 를 고칠 것 \
         (그래야 «언제 무엇이 움직였나»가 이력에 남는다)"
    );
}

#[test]
fn an_unmeasured_row_is_a_debt_not_a_default() {
    // 규칙 4 — 이 축의 알맹이다.
    let unmeasured = CONTRACTS
        .iter()
        .filter(|x| matches!(x.canon, Unmeasured))
        .count();
    assert!(
        unmeasured <= UNMEASURED_CEILING,
        "못 잰 줄이 {unmeasured} 로 늘었다(한도 {UNMEASURED_CEILING}) — \
         ⛔ **지금 있는 것을 다 못 쟀다는 사실은 새 화면을 안 재도 된다는 뜻이 아니다.** \
         새 판을 더했으면 정본의 같은 자리를 눌러 보고 「같다/허용/결함」 중 하나로 적는다 \
         (pytmux-185). 옛 줄을 쟀으면 `UNMEASURED_CEILING` 도 함께 내릴 것."
    );
}

#[test]
fn print_the_contract_score() {
    // 게이트가 아니라 **자**다(`parity.rs` 의 `print_the_score` 와 같은 자리).
    let (same, allowed, defect, unmeasured) = SCORE;
    println!("\n상호작용 계약(정본 대 GUI · 재는 것은 「제 것 아닌 키」 하나):");
    println!("  같다 {same} · 허용된 갈림 {allowed} · 결함 {defect} · 못 쟀다 {unmeasured} (전체 {})",
        CONTRACTS.len());
    for contract in CONTRACTS {
        if let Defect(issue) = contract.canon {
            println!("  결함 {issue:<12} {:?}", contract.screen);
        }
    }
    println!();
}
