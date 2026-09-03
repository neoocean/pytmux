//! 키바인딩 — **두 백엔드가 공유하는 단 하나의 정의**.
//!
//! GUI 와 TUI 는 키 이름을 같은 문자열 문법으로 쓴다(`warpui_core::keymap` 의 표기).
//! 그래서 바인딩 표를 여기 한 벌만 두고 양쪽이 순회하며 붙인다. 한쪽에만 키를 추가하는
//! 실수가 구조적으로 불가능해진다.

/// 사용자가 일으킬 수 있는 일.
///
/// 액션은 **의도**이지 조작이 아니다("아래 화살표"가 아니라 "다음 블록 선택"). 그래야
/// 키를 바꿔도 상태 전이 코드가 그대로 남는다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    SelectNext,
    SelectPrev,
    SelectFirst,
    SelectLast,
    /// 선택된 블록의 출력을 접거나 편다.
    ToggleExpand,
    /// Claude 의 플랜 전문·거부 사유를 전용 화면으로 열고 닫는다.
    ///
    /// 아래 요약 구역은 5줄 고정이라(뷰의 `CHROME_ROWS`) 여러 줄짜리 본문이 들어갈 자리가
    /// 없다. 구역을 키우면 그만큼 서버 캔버스가 화면 밖으로 밀리므로, 넓은 것은 **덮어서**
    /// 보여 주고 크기는 그대로 둔다.
    ToggleClaudeDetail,
    // ── prefix 모드가 부르는 것들(패리티 G1) ────────────────────────────────
    //
    // 이름은 **의도**로 짓는다. `SelectNext` 같은 옛 이름이 탭 이동을 겸하고 있었는데,
    // 그건 블록 목록 데모에서 온 이름이라 세션 뷰에서는 무슨 일이 일어나는지 안 보였다.
    /// 좌우로 나눈다(pytmux 기준 `horizontal` — tmux 와 반대다).
    SplitLeftRight,
    /// 위아래로 나눈다.
    SplitTopBottom,
    /// 지금 패널을 닫는다.
    KillPane,
    NewTab,
    /// 새 탭을 **지금 패널의 디렉토리**에서 열고 거기서 Claude Code CLI 를 띄운다
    /// (`esc c` · pytmux-137).
    ///
    /// [`NewTab`](Action::NewTab) 과 가르는 것 둘: ⑴ 실행할 명령이 붙는다(무엇을
    /// 실행할지는 `claude-command` 설정이 정한다 — 경로·플래그가 사람마다 다르다)
    /// ⑵ **`default-path` 를 안 따른다**. 이 키의 값은 「이 디렉토리에서 바로
    /// 붙는다」에 있어서, 설정이 `home` 이면 요구가 조용히 안 지켜진다.
    NewClaudeTab,
    /// 지금 탭을 닫는다.
    KillTab,
    NextTab,
    PrevTab,
    /// 직전에 보던 탭으로.
    LastTab,
    /// 번호로 탭을 고른다(0~9). 화면에 보이는 번호가 곧 이 값이다.
    SelectTab(u8),
    /// 화면을 통째로 다시 받는다(`prefix r`).
    Redraw,
    /// 활성 패널을 창 전체로 키웠다 줄인다(`prefix z`).
    Zoom,
    /// 다음 패널로(`prefix o`).
    NextPane,
    /// 직전에 보던 패널로(`prefix ;`).
    LastPane,
    /// 미리 정해진 배치를 순환한다(`prefix Space`).
    CycleLayout,
    /// 패널들을 한 칸씩 돌린다(`prefix Ctrl+o`).
    RotatePanes,
    /// 활성 패널을 이웃과 맞바꾼다(`prefix {` / `}`).
    SwapPane { forward: bool },
    /// 활성 패널을 새 탭으로 떼어낸다(`prefix !`).
    BreakPane,
    /// 방향으로 패널을 고른다(`prefix ←↑↓→`).
    SelectPane(Dir),
    /// 방향으로 패널 경계를 민다(`prefix H J K L`).
    ResizePane(Dir),
    /// 지금 탭의 고정(핀)을 켜고 끈다(`prefix P`).
    TogglePin,
    /// 서버의 페이스트 버퍼 맨 앞을 패널에 붙인다(`prefix ]`).
    PasteBuffer,
    /// **OS 클립보드**를 활성 패널에 붙인다(`paste-clipboard` · `Ctrl+V`).
    ///
    /// ⚠ [`PasteBuffer`](Action::PasteBuffer) 와 **다른 기능**이다 — 이름이 닮아서 한
    /// 자리로 접고 싶어지는데, 저쪽은 **서버가 든 버퍼**이고 이쪽은 **이 상자의 OS
    /// 클립보드**다. 정본도 둘을 따로 둔다(`paste-buffer` · `paste-clipboard`).
    ///
    /// 글자면 그대로 붙이고, 그림이면 **임시 파일로 떨궈 그 경로를 붙인다**(정본이 정한
    /// 계약 — `clip::save_image` 문서). 어느 쪽인지는 클립보드를 읽어 봐야 알므로 이
    /// 액션은 「읽어서 알맞게 해 달라」 하나다(정본 `paste_os_clipboard` 과 같다).
    ///
    /// ⛔ 이 액션은 **뷰가 창을 쥔 자리**에서만 끝난다 — 클립보드를 읽는 데 창 문맥이
    /// 필요해서다. 그래서 `action_to_command` 는 여기서 명령을 만들지 않는다.
    PasteClipboard,
    /// 키 도움말 화면을 열고 닫는다(`esc ?`).
    ShowKeys,
    /// 탭 스위처를 연다(`esc Tab`).
    ShowTabs,
    /// 세션·탭·패널 개요(트리)를 연다(`prefix w`).
    ShowTree,
    /// 페이스트 버퍼 목록을 연다(`prefix =`).
    ShowBuffers,
    /// 지금 탭의 이름을 바꾼다(`prefix ,` — 입력 화면이 뜬다).
    RenameTab,
    /// 지금 탭을 다른 자리로 옮긴다(`prefix .` — 입력 화면이 뜬다).
    MoveTab,
    /// 명령 팔레트를 연다(`prefix :` · `esc :`).
    ShowCommands,
    /// 설정 화면을 연다(파이썬의 `:settings`).
    ShowSettings,
    /// 서버와 그 아래 모든 탭·셸을 끝낸다(`kill-server`). **확인 없이 부르지 않는다.**
    KillServer,
    /// 서버 코드를 갈아 끼운다 — 셸·PTY 는 살아 있다(`restart-server`).
    RestartServer,
    /// 다른 상자의 pytmux 탭을 이 탭바에 붙인다(`remote-attach <host>`).
    RemoteAttach,
    /// 다른 상자에 **새 셸**을 띄워 탭으로 붙인다(`remote-new-tab <host>`).
    RemoteNewTab,
    /// 원격 붙임을 푼다(`remote-detach [host]` — 빈 값이면 전부).
    RemoteDetach,
    /// 플러그인 관리 화면을 연다(`plugins` · 별칭 `plugin-manager`).
    ShowPlugins,
    /// 활성 패널을 큰 시계로 덮는다(`prefix t` — `clock` 플러그인).
    ToggleClock,
    /// 활성 패널의 제목을 바꾼다(`prefix T` — 입력 화면이 뜬다).
    RenamePane,
    /// 패널마다 번호를 띄운다(`prefix q`). 숫자를 누르면 그 패널로 간다.
    ShowPaneNumbers,
    /// F10 메뉴를 연다(`prefix Enter`).
    ShowMenu,
    /// 지나간 알림 목록을 연다(`notice-history`).
    ShowNotices,
    /// 블록·Claude **요약 판**을 연다(`summary` · §10-21ⓓ).
    ///
    /// 종전에는 화면 아래에 늘 붙어 있던 구역이다. 제보가 *"GUI 에만 있고 pytmux
    /// 사용에 직접적인 영향을 주지 않으므로 화면에서 빼고 별도 명령어나 메뉴로"* 라고
    /// 해서 판으로 옮겼다 — 훑는 용도의 요약이 화면의 주인공(패널)을 밀어내던 자리다.
    ShowSummary,
    /// 활성 패널에 **ESC 한 바이트**를 보낸다(`esc e` · `Shift+ESC`).
    ///
    /// 왜 따로 있나: 명령 모드 안에서 vim·less 에게 ESC 를 주려면 길이 필요한데, 그냥
    /// `ESC` 를 누르면 그건 모드에서 나가는 키다(파이썬도 같은 이유로 `e` 를 둔다).
    SendEscape,
    /// 활성 패널에 **리터럴 백틱**을 보낸다(`` esc ` ``).
    ///
    /// 파이썬 클라에서 백틱이 명령 모드 진입 키라, 백틱 자체를 치려면 길이 따로 있어야
    /// 한다. 우리는 진입 키가 `ESC` 지만 **손버릇을 맞춘다**.
    SendBacktick,
    /// 앱 **전체** 글자 크기를 한 걸음 키우거나 줄인다(`Ctrl+=`/`Ctrl+-` · §10-21ⓐ).
    ///
    /// # 왜 GUI 만인가
    ///
    /// 정본(TUI)의 글자 크기는 **호스트 단말**이 정한다 — 우리가 건드릴 자리가 없다.
    /// `Ctrl+=`/`Ctrl+-` 를 가로채는 것도 독립 앱이라 되는 일이고(같은 논리가 `Ctrl+Tab`
    /// = §10-21ⓕ).
    ///
    /// ⚠ **갈림을 어디에 적나** — HANDOFF §10-21 이 여러 자리에서 "패리티 표에 `iv` 로
    /// 선언"이라고 적지만 그 장치는 **없다**: `iv`/`KNOWN_DIVERGENCES` 는 Rust TUI 를
    /// 지우면서 함께 사라졌고(2026-08-01), 지금 `parity.rs` 의 칸은 하나이며 그 표의
    /// 줄은 **정본 픽스처가 정한다** — 정본에 없는 표면은 실을 줄 자체가 없다. 그래서
    /// GUI 전용 표면의 대장은 적합성 게이트의 허용 목록이다:
    /// `category_conformance.rs` 의 `PALETTE_OURS`(팔레트 이름)·`SETTINGS_OURS`(설정 줄).
    /// 거기 없으면 게이트가 운다 = 조용히 늘지 않는다.
    ///
    /// 규칙(끝값·걸음·반올림)의 주인은 [`crate::config::font_scale_step`] 이다 — 뷰가
    /// 각자 더하면 두 자리에서 다르게 잘리고 그 어긋남이 설정 파일에 굳는다.
    FontScale { up: bool },
    /// 글자 크기를 기본(1.0)으로 되돌린다(`Ctrl+0`).
    ///
    /// 따로 있는 이유: 배율은 **자기가 자기 입구를 작게 만든다** — 0.5 까지 줄여 놓고
    /// 설정 화면을 찾아 들어가는 것보다 키 하나로 돌아오는 길이 있어야 한다.
    FontScaleReset,
    /// 활성 패널을 이번 달 달력으로 덮는다(`calendar-mode` — `calendar` 플러그인).
    ///
    /// **키가 없다** — 파이썬도 안 준다(시계만 `prefix t` 를 쓴다). 팔레트가 입구다.
    ToggleCalendar,
    /// 오버레이를 **명시적으로** 켜거나 끈다(`open-clock`·`close-clock`·`open-calendar`·
    /// `close-calendar`).
    ///
    /// # 왜 토글로는 부족한가 (§10-21ⓡ)
    ///
    /// 제보: `close-clock`·`close-calendar` 가 안 먹는다. 우리에게 토글밖에 없어서 그
    /// 이름들이 **플러그인 줄**로 남았고, 고르면 서버에 "화면을 다오"로 가서
    /// *"이 플러그인은 화면 스펙을 제공하지 않습니다"* 로 거절당했다 — 상태를 바꾸는
    /// 명령을 화면 여는 길로 보낸 것이다. 이름이 코어 표에 있으면 그 줄은 플러그인
    /// 목록에서 빠지고([`crate::plugins::native_action`]) 우리가 실행한다.
    ///
    /// 뜻은 core 가 정한다(정본 계약 그대로): **켜기는 멱등** · 대상은 활성 패널 ·
    /// 시계와 달력은 **상호 배타**. 그 판정은 `proto::SessionState::set_overlay` 다.
    SetOverlay { name: &'static str, on: bool },
    /// 플러그인이 **서버에** 들고 있는 토글 하나를 그 플러그인의 서버 액션으로 직접 친다
    /// (pytmux-35). 무인자면 서버가 뒤집는다 — 현재값의 권위는 서버다.
    ///
    /// # 왜 이 이름들이 죽어 있었나
    ///
    /// 코어 표에 없는 이름은 **플러그인 줄**로 남고, 고르면 `plugin_open`("화면을 다오")
    /// 으로 가서 *"이 플러그인은 화면 스펙을 제공하지 않습니다"* 로 거절당한다. 상태를
    /// 바꾸는 명령을 화면 여는 길로 보낸 것이다 — `SetOverlay` 가 고친 것과 **같은 뿌리**.
    ///
    /// # 왜 서버가 못 하나 (래칫이 요구하는 사유)
    ///
    /// 못 하는 게 아니다 — **서버는 처음부터 이 액션들을 받고 있다**(정본 훅이 치는 그
    /// 이름 그대로). 갈린 것은 "팔레트 이름 → 서버 액션"을 아는 자리이고, 정본은 그것을
    /// 파이썬 클라 훅에 뒀다. 그 훅은 파이썬 객체를 주고받아 **소켓을 못 건넌다**(설계
    /// M4 §7 이 선언형으로 바꾸려는 바로 그것). 그때까지는 이 표가 우리 몫이다.
    ///
    /// ⚠ 여기 넣는 것은 **인자 없이 뜻이 온전한** 것만이다. `claude-auto-redraw`(3-state)
    /// 처럼 인자를 파싱해야 하는 것은 팔레트가 인자를 못 받는 동안(pytmux-7) 반쪽이 된다.
    PluginToggle { action: &'static str },
    /// 인자 없이 서버에 한 번 시키는 플러그인 명령(`refresh_usage`). 위 토글의 짝이다.
    PluginDo { action: &'static str },
    /// 활성 패널을 Claude 한도 막대 + 리셋 카운트다운으로 덮는다
    /// (`usage-view` — `claude-token-usage-view` 플러그인).
    ///
    /// ⚠ **정본의 `usage-view` 는 세 모드**(popup·tab·pane)이고 기본이 `popup` 인데,
    /// 우리에게 있는 것은 **`pane`(오버레이) 하나**다 — 나머지 둘은 Textual 화면이라
    /// 여기에 짝이 없다. 그래서 팔레트의 `usage-view` 는 이 오버레이로 간다.
    /// 종전에는 같은 자리가 "이 클라엔 화면이 없다" 알림으로 끝났으니 **덜 하던 것이
    /// 늘어난 것**이지 다르게 하는 것이 아니다. 팝업/탭이 오면 그때 갈래를 나눈다.
    ToggleUsageView,
    /// 입력을 창 안 모든 패널로 복제할지 토글(`synchronize-panes`).
    ToggleSync,
    ToggleMonitorActivity,
    ToggleMonitorBell,
    /// 탭 이름 자동 갱신 토글.
    ToggleAutoRename,
    /// 패널 테두리에 제목을 항상 보일지 토글(서버 옵션 `pane-border-status`).
    ToggleBorderStatus,
    /// 비활성 패널 딤을 토글(설정 파일 `inactive-dim`).
    ToggleInactiveDim,
    /// 값 없이 뒤집는 서버 옵션들 — 어느 것인지는 [`ServerOpt`] 가 든다.
    ToggleServerOption(ServerOpt),
    /// 스크롤백을 비운다(`clear-history`).
    ClearHistory,
    /// 지금 탭을 한 칸 왼쪽/오른쪽·맨 앞/맨 뒤로 옮긴다.
    ///
    /// 목표 자리는 **탭 목록을 봐야** 안다 — 그래서 명령이 아니라 액션이고, 뷰가 옮긴다
    /// (`SelectLast` 가 같은 이유로 명령이 없는 것과 같은 자리다).
    MoveTabBy(TabMove),
    /// `from` 자리의 탭을 `to` 자리로 옮긴다(자리를 **골라서** 옮기는 판).
    ///
    /// [`MoveTabBy`](Action::MoveTabBy) 와 갈리는 점: 저쪽은 **지금 활성 탭**을 옮기고
    /// 이쪽은 **고른 탭**을 옮긴다. 탭바 포커스에서 `Shift+←→` 로 옮길 때 고른 탭과 활성
    /// 탭이 다를 수 있어서 갈랐다 — 저쪽을 쓰면 엉뚱한 탭이 움직인다(파이썬 `e_tb` 도
    /// `move_tab index=… to=…` 로 **자리를 실어** 보낸다).
    ///
    /// 키 표에도 팔레트에도 없다. 자리 둘을 알아야 뜻이 생기므로 **탭바 포커스에서만**
    /// 만들어진다([`chrome::Chrome::press`](crate::chrome::Chrome::press)).
    MoveTabAt { from: u8, to: u8 },
    /// 지금 탭을 다른 자리와 맞바꾼다(`swap-tab` — 입력 화면이 뜬다).
    SwapTab,
    /// 탭 고정을 **명시적으로** 켜거나 끈다(`pin-tab`/`unpin-tab`).
    SetPinned(bool),
    /// 값이 정해진 서버 옵션 하나를 **그 값으로** 놓는다(`vt-parser`·`window-size`).
    ///
    /// 토글([`Action::ToggleServerOption`])과 갈리는 이유: 이건 둘이 아니라 셋 중 하나라
    /// "뒤집는다"가 뜻이 없다. 다음 값으로 넘기는 것은 화면이 정하고 여기엔 **정해진
    /// 값**이 실린다 — 그래야 팔레트에서도 같은 액션을 쓸 수 있다.
    SetEnum(EnumOpt, &'static str),
    /// 패널 내용을 페이스트 버퍼로 캡처한다. `true` 면 스크롤백 전체.
    CapturePane(bool),
    /// 패널 출력을 외부 명령으로 흘린다(`pipe-pane` — 입력 화면).
    PipePane,
    /// 다른 탭의 패널을 지금 탭으로 합친다(`join-pane` — 입력 화면).
    JoinPane,
    /// 서버 버전·업타임을 묻는다(`version`).
    RequestVersion,
    /// 재시작이 안전한지 **실행 없이** 점검한다(`restart-check`).
    RequestRestartCheck,
    /// 같은 원격의 다른 탭을 지금 탭에 패널로 합친다(`merge-remote-tab` — 피커).
    MergeRemoteTab,
    /// 서버에 라이브 PTY 팝업을 띄운다(`display-popup` — 입력 화면).
    ///
    /// [`Action::RunShell`] 과 다르다: 저쪽은 **우리가** 셸을 돌려 출력만 보이고,
    /// 이건 서버가 PTY 를 띄워 **대화형 명령**(vim·top)도 된다.
    DisplayPopup,
    /// 떠 있는 팝업을 닫는다(`display-popup -C`).
    PopupClose,
    /// 셸 명령 하나를 돌리고 결과를 보인다(`run-shell` — 입력 화면).
    RunShell,
    /// 조건 명령이 성공하면 다른 명령을 돌린다(`if-shell <조건> <명령>` — 입력 화면).
    IfShell,
    /// 상태줄에 한 줄 띄운다(`display-message` — 입력 화면).
    DisplayMessage,
    /// 설정 파일을 다시 읽는다(`source-file`).
    SourceFile,
    /// 옵션 하나를 설정한다(`set <옵션> <값>` — 입력 화면).
    SetOption,
    /// 지금 옵션 값들을 본다(`show-options`) — 설정 화면을 연다.
    ShowOptions,
    /// 사건에 명령을 건다(`set-hook <이벤트> <명령>` · `-u <이벤트>` — 입력 화면).
    SetHook,
    /// 선택지가 정해진 인자를 화면에서 고른다(`crate::options` — 패리티 G8v).
    ///
    /// 값은 `COMMAND_OPTIONS` 의 명령 이름이다. 표에 없는 이름이면 화면이 안 뜬다.
    ShowCommandOptions(&'static str),
    /// 패널 동기화를 **그 값으로** 정한다(토글이 아니다 — `crate::options` 문서 참조).
    SetSync(bool),
    /// 활동/벨 감시를 그 값으로 정한다.
    SetMonitor { bell: bool, on: bool },
    /// 탭 자동 이름을 그 값으로 정한다.
    SetAutoRename(bool),
    /// 서버 옵션을 그 값으로 정한다.
    SetServerOption(ServerOpt, bool),
    /// 걸어 둔 훅들을 본다(`show-hooks`) — 읽는 화면을 연다.
    ShowHooks,
    /// 패널에 키를 주입한다(`send-keys` — 입력 화면).
    SendKeys,
    /// 키에 명령을 건다(`bind-key <키> <명령>` — 입력 화면).
    BindKey,
    /// 건 키를 푼다(`unbind-key <키>` — 입력 화면).
    UnbindKey,
    /// 레이아웃 프리셋 목록을 연다(`select-layout`).
    ShowLayouts,
    /// 현재 탭 배치를 이름으로 저장한다(`layout-save` — 입력 화면).
    SaveTabLayout,
    /// 저장한 배치를 불러온다(`layout-load` / `layout-load-new` — 입력 화면).
    ///
    /// `true` 면 **새 탭에** 연다. 덮어쓰기와 새 탭은 되돌릴 수 있는 정도가 달라서
    /// (덮어쓰기는 지금 배치를 지운다) 액션부터 갈라 둔다.
    LoadTabLayout(bool),
    /// 서버 전체 배치를 영속한다(`save-layout`).
    SaveLayout,
    /// 영속한 전체 배치를 되돌린다(`restore-layout`).
    RestoreLayout,
    /// 죽은 패널에 셸을 다시 띄운다(`respawn-pane`).
    RespawnPane,
    /// 스크롤백을 훑는 모드로 들어간다([`InputMode::Scroll`](crate::InputMode)).
    ///
    /// 왜 별도 모드인가: 평소 모드의 `PgUp`·화살표는 **패널로 가야 한다**(less·vim 이
    /// 그 키를 쓴다). 파이썬 클라도 같은 이유로 스크롤을 모드 안에 두고 `esc` `[` 로
    /// 들어간다 — tmux 의 copy-mode 관습이다.
    EnterScroll,
    /// 캔버스 위에서 블록을 고르는 모드로 들어간다
    /// ([`InputMode::Block`](crate::InputMode) · pytmux-18 · `esc b`).
    ///
    /// # 왜 액션은 하나뿐인가
    ///
    /// 들어간 뒤의 조작(`↑`/`↓`/`Ctrl+C`)은 액션이 아니라 **모드 안의 키**다
    /// ([`crate::keys::BlockKey`] 문서) — 그 셋은 모드 밖에서는 패널 안 프로그램의
    /// 것이라 팔레트 줄로 올릴 뜻이 없다.
    ///
    /// # 왜 [`EnterScroll`](Self::EnterScroll) 처럼 표에서 바로 모드로 안 접히나
    ///
    /// **고를 블록이 있어야** 이 모드가 뜻을 갖기 때문이다
    /// ([`ModeState::enter_block`](crate::keys::ModeState::enter_block)).
    SelectBlocks,
    /// 활성 Claude 패널에서 **이전/다음에 입력한 프롬프트** 자리로 뛴다
    /// (`esc Ctrl+↑`/`Ctrl+↓` — 파이썬 `e_jump`). `up` 이면 과거 방향이다.
    ///
    /// # 왜 이 액션만 모드를 스크롤로 바꾸나
    ///
    /// 뛰면 뷰가 라이브 하단을 벗어난다. 그 상태에서 평소 모드로 돌아오면 방향키가
    /// 패널로 가 버려서, 뛴 자리 주변을 읽을 방법이 없다 — 그래서 파이썬도 여기서
    /// **스크롤 모드로 들어간다**. 그 규칙은 [`ModeState`](crate::keys::ModeState) 에
    /// 있다(뷰가 각자 적으면 한쪽만 모드가 안 바뀐다).
    ///
    /// 스크롤 모드 **안에서도** 같은 키가 계속 먹는다(파이썬 `_handle_scroll_key`) —
    /// 턴 경계를 연달아 오가는 것이 이 키의 쓰임이다.
    JumpPrompt { up: bool },
    /// 스크롤백 검색 물음을 연다(스크롤 모드 `/` · 메뉴 search — 파이썬
    /// `_handle_scroll_key`). 대답은 서버 `search` 로 간다 — 검색 자체는 서버가
    /// 한다(스크롤백이 거기 있다). 물음이 닫히면 스크롤 모드다(뷰가 지킨다 —
    /// 파이썬 `_prompt_done` 의 `mode = "scroll"` 과 같은 자리).
    SearchScrollback,
    /// 전역 검색 물음을 연다(`esc f`·메뉴 search_all — pytmux-27). 열려 있는 모든
    /// 로컬+원격 탭·패널을 훑는다 — [`Action::SearchScrollback`] 은 활성 패널
    /// 하나 안에서 다음 히트로 넘어가는 것뿐이라 다른 화면·다른 서버 명령이다.
    /// 대답은 서버 `search_all` 로 가고, 회신(결과 목록)이 오면 결과 화면이 열린다.
    SearchAll,
    /// 지난 검색을 반복한다(스크롤 모드 `n`/`N`). `down` 이면 아래(최근) 방향.
    /// 검색어는 서버가 기억한다(`Pane.search_query`) — 클라가 다시 실을 것이 없다.
    SearchAgain { down: bool },
    /// 여러 줄 작성창을 연다(`esc Insert` · `esc Shift+Delete` — 파이썬 `e_ins`).
    ///
    /// **옵트인이다** — 여는 동안 자식의 인라인 기능(슬래시 메뉴·`@` 자동완성·↑ 히스토리)은
    /// 못 쓴다. 필요할 때만 열고, 다 쓰면 그 텍스트가 패널에 통째로 들어간다
    /// ([`crate::editor`] 모듈 문서).
    ShowCompose,
    /// 탭으로 나뉜 읽기 전용 정보 팝업을 연다(파이썬 `InfoTabsScreen`).
    ///
    /// 입구는 **하단 상태줄의 호스트 배지**다(파이썬과 같다 — `e_down` 으로 내려가
    /// `Enter`). 팔레트에도 둔다: 배지 동선을 모르는 사람에게는 그쪽이 입구다.
    ShowInfoTabs,
    /// 프롬프트 단위 클리어를 뒤집는다(claude-code 플러그인 `set_prompt_clear`).
    ///
    /// 자동재개와 같은 부류의 순수 토글이다 — 화면이 없고, 상태는 상태줄 표식
    /// `[프롬프트클리어]` 로 보인다(proto `StatusFlags`).
    TogglePromptClear,
    /// 토큰리밋 **자동재개**를 뒤집는다(`prefix R` — 파이썬 `p_R`).
    ///
    /// 플러그인(claude-code)이 소유한 조작이지만 **화면이 없는 순수 토글**이라, 결정 2
    /// (「플러그인 = 화면까지 재현」)와 G7 결론(「켜고 끌 수는 있지만 그리지는 않는다」)이
    /// 여기서는 갈리지 않는다. 그리는 것은 상태줄 표식 하나다(`[자동재개]`).
    ToggleAutoresume,
    /// 자동 재개 **설명 판**을 연다(pytmux-183 · 정본 `open_autoresume_info`).
    ///
    /// [`ToggleAutoresume`](Action::ToggleAutoresume) 과 가르는 것: 저쪽은 **바로
    /// 뒤집고**(키·팔레트의 손) 이쪽은 **판을 세운다**(좌하단 표식을 눌렀을 때의 손).
    /// 정본이 그 둘을 따로 두는 이유는 `Screen::Autoresume` 문서에 있다.
    ShowAutoresume,
    /// **커서 판**을 연다(`cursor` · GUI 전용 · pytmux-375).
    ///
    /// 모양·두께·색·깜빡임·주기 다섯이 이미 설정 화면에 있는데도 입구를 따로 두는
    /// 이유는 [`Screen::Cursor`](crate::screens::Screen::Cursor) 가 적는다 — 요약하면
    /// **바꾸면서 결과를 못 보던** 자리를 판 안의 견본으로 푼다.
    ///
    /// ⛔ 키를 안 준다. 이 판은 자주 여는 자리가 아니라(커서를 하루에 몇 번 바꾸겠나)
    /// 기본 키 표에 자리를 하나 먹으면 그만큼 정본과 멀어진다 — 입구는 팔레트 하나다
    /// (`font-scale-*` 이 키를 갖는 것과 갈리는 지점이고, 그쪽은 키가 **주** 입구다).
    ShowCursor,
    /// **런타임 계측 판**을 연다(`debug-stats` · pytmux-457).
    ///
    /// 정본에도 같은 이름이 있고 같은 뜻이다 — *"내 프로세스를 잰다"*. 항목은 1:1 이
    /// 아니다(런타임이 다르다): 저쪽은 파이썬 힙·GC 세대이고 이쪽은 그린 프레임·
    /// 프레임 시간·글리프 캐시·씬 원소·큐 깊이·RTT 다. 줄을 만드는 것은
    /// [`crate::diag::RuntimeStats`] 이고 값을 모으는 것은 뷰다.
    ///
    /// ⛔ 키를 안 준다 — 진단 명령이라 자주 여는 자리가 아니고, 정본도 팔레트/명령
    /// 한 줄이 유일한 입구다(`ShowCursor` 와 같은 판단).
    ShowDebugStats,
    /// UI 언어를 이 로케일로 바꾼다(`lang ko|en` — 파이썬 `cmd_lang`).
    ///
    /// **클라 안에서 끝난다** — 로케일은 per-user 라 서버에 보낼 것이 없다(파이썬도
    /// 서버 opts 를 안 건드린다). 뷰가 [`crate::i18n::set_locale`] + 영속을 하고,
    /// 다음 렌더부터 표면이 그 언어로 그려진다.
    SetLang(&'static str),
    /// 정체된 소켓을 버리고 **다시 붙는다**(`reconnect`/`resync`).
    ///
    /// 서버 PTY·세션·돌고 있는 Claude 는 그대로다 — 바꾸는 것은 이 클라의 연결뿐이다.
    /// 왜 필요한가: 채널이 막혀 화면이 굳으면 클라를 끄고 다시 띄우는 것 말고는 길이
    /// 없었다. 그 사이 탭 배치·스크롤 위치가 초기화된다.
    Reconnect,
    /// 창을 **전체 화면**으로 넣었다 뺐다(§10-21ⓘ3 · `Alt`+`Enter`).
    ///
    /// # 이것은 허용되는 갈림이다 (ⓒ OS 창 통합)
    ///
    /// 정본(Textual TUI)의 풀스크린은 **호스트 단말의 일**이라 우리가 건드릴 자리가
    /// 없다(`pytmuxlib` 전체에 창 전환 코드 0건). GUI 는 자기 창을 가지므로 할 수 있다 —
    /// 패리티 표에 `iv` 로 선언한다.
    ///
    /// # 상태는 **우리가 안 든다**
    ///
    /// 진실은 창에 있다(`fullscreen_state()`). 뷰가 사본을 들면 OS 가 바꾼 상태(맥의
    /// 초록 버튼 · `F11`)와 갈린다 — 그러면 토글이 한 번 헛돈다.
    ToggleFullscreen,
    /// 서버 재시작 + **이 클라 자신의 재기동**(`restart-all`).
    ///
    /// 둘 다 드라이런([`crate::restart`])을 먼저 지난다 — 되돌릴 수 없는 동작이다.
    RestartAll,
    Quit,
}

impl Action {
    /// 도움말 줄에 쓸 짧은 이름.
    pub fn label(&self) -> &'static str {
        crate::i18n::t(match self {
            Action::SplitLeftRight => "좌우 분할",
            Action::SplitTopBottom => "상하 분할",
            Action::KillPane => "패널 닫기",
            Action::NewTab => "새 탭",
            Action::NewClaudeTab => "새 탭 (Claude Code)",
            Action::KillTab => "탭 닫기",
            Action::NextTab => "다음 탭",
            Action::PrevTab => "이전 탭",
            Action::LastTab => "직전 탭",
            Action::SelectTab(_) => "번호로 탭",
            Action::Redraw => "다시 그리기",
            Action::Zoom => "줌",
            Action::NextPane => "다음 패널",
            Action::LastPane => "직전 패널",
            Action::CycleLayout => "레이아웃 순환",
            Action::RotatePanes => "패널 회전",
            Action::SwapPane { .. } => "패널 교환",
            Action::BreakPane => "새 탭으로",
            Action::SelectPane(_) => "패널 이동",
            Action::ResizePane(_) => "패널 크기",
            Action::TogglePin => "탭 고정",
            Action::PasteBuffer => "버퍼 붙여넣기",
            Action::PasteClipboard => "클립보드 붙여넣기",
            Action::ShowKeys => "키 도움말",
            Action::ShowTabs => "탭 스위처",
            Action::ShowTree => "트리(개요)",
            Action::ShowBuffers => "버퍼 선택",
            Action::RenameTab => "탭 이름변경",
            Action::MoveTab => "탭 이동",
            Action::ShowCommands => "명령 팔레트",
            Action::ShowSettings => "설정",
            Action::KillServer => "서버 종료",
            Action::RestartServer => "서버 재시작",
            Action::RemoteAttach => "원격 붙이기",
            Action::RemoteNewTab => "원격 새 탭",
            Action::RemoteDetach => "원격 떼기",
            Action::ShowPlugins => "플러그인",
            Action::RenamePane => "패널 제목 변경",
            Action::ShowPaneNumbers => "패널 번호",
            Action::ShowMenu => "메뉴",
            Action::ShowNotices => "알림 이력",
            Action::ShowSummary => "블록·Claude 요약",
            Action::SelectBlocks => "블록 고르기",
            Action::SendEscape => "패널에 ESC",
            Action::SendBacktick => "패널에 `",
            Action::ToggleClock => "시계",
            Action::FontScale { up } => {
                if *up { "글자 크게" } else { "글자 작게" }
            }
            Action::FontScaleReset => "글자 크기 기본",
            Action::ToggleCalendar => "달력",
            Action::SetOverlay { name, on } => match (*name, *on) {
                ("clock", true) => "시계 켜기",
                ("clock", false) => "시계 끄기",
                ("calendar", true) => "달력 켜기",
                _ => "달력 끄기",
            },
            // 이름은 **정본 팔레트의 낱말**을 따른다 — 같은 줄을 두 클라가 다르게 부르면
            // 사용자가 같은 기능인지 모른다.
            Action::PluginToggle { action } => match *action {
                "set_claude_auto_retry" => "전송 재시도",
                "set_auto_token_on_exit" => "종료 시 토큰 기록",
                "set_claude_auto_mode" => "Claude 자동 모드",
                _ => "토큰 진단 로그",
            },
            Action::PluginDo { .. } => "사용량 새로 고침",
            Action::ToggleUsageView => "Claude 한도",
            Action::ToggleSync => "패널 동기화",
            Action::ToggleMonitorActivity => "활동 감시",
            Action::ToggleMonitorBell => "벨 감시",
            Action::ToggleAutoRename => "자동 이름",
            Action::ToggleBorderStatus => "패널 제목 항상",
            Action::ToggleInactiveDim => "비활성 딤",
            Action::ToggleServerOption(opt) => opt.label(),
            Action::ClearHistory => "스크롤백 비우기",
            Action::MoveTabBy(to) => to.label(),
            Action::MoveTabAt { .. } => "고른 탭 옮기기",
            Action::SwapTab => "탭 교환",
            Action::SetPinned(true) => "탭 고정",
            Action::SetPinned(false) => "탭 고정 해제",
            Action::SetEnum(opt, _) => opt.label(),
            Action::CapturePane(false) => "패널 캡처",
            Action::CapturePane(true) => "패널 캡처(스크롤백 전체)",
            Action::PipePane => "패널 출력 파이프",
            Action::JoinPane => "패널 합치기",
            Action::RequestVersion => "버전",
            Action::RequestRestartCheck => "재시작 점검",
            Action::MergeRemoteTab => "원격 탭 머지",
            Action::DisplayPopup => "팝업",
            Action::PopupClose => "팝업 닫기",
            Action::RunShell => "셸 명령",
            Action::IfShell => "조건부 셸",
            Action::DisplayMessage => "메시지 표시",
            Action::SourceFile => "설정 다시 읽기",
            Action::SetOption => "옵션 설정",
            Action::ShowOptions => "옵션 보기",
            Action::SetHook => "훅 걸기",
            Action::ShowCommandOptions(_) => "인자 고르기",
            Action::SetSync(_) => "패널 동기화 정하기",
            Action::SetMonitor { .. } => "감시 정하기",
            Action::SetAutoRename(_) => "자동 이름 정하기",
            Action::SetServerOption(..) => "서버 옵션 정하기",
            Action::ShowHooks => "훅 보기",
            Action::SendKeys => "키 주입",
            Action::BindKey => "키 바인딩",
            Action::UnbindKey => "키 바인딩 해제",
            Action::ShowLayouts => "레이아웃 프리셋",
            Action::SaveTabLayout => "탭 배치 저장",
            Action::LoadTabLayout(false) => "탭 배치 불러오기",
            Action::LoadTabLayout(true) => "탭 배치 → 새 탭",
            Action::SaveLayout => "전체 배치 저장",
            Action::RestoreLayout => "전체 배치 복원",
            Action::RespawnPane => "패널 되살리기",
            Action::SelectNext => "다음",
            Action::SelectPrev => "이전",
            Action::SelectFirst => "처음",
            Action::SelectLast => "끝",
            Action::ToggleExpand => "접기/펴기",
            Action::ToggleClaudeDetail => "플랜/거부",
            Action::EnterScroll => "스크롤",
            Action::SearchScrollback => "스크롤백 검색",
            Action::SearchAll => "모든 탭·패널 검색",
            Action::SearchAgain { down: false } => "검색 반복 ↑",
            Action::SearchAgain { down: true } => "검색 반복 ↓",
            Action::JumpPrompt { up: true } => "이전 프롬프트로",
            Action::JumpPrompt { up: false } => "다음 프롬프트로",
            Action::ShowCompose => "작성창",
            Action::ShowInfoTabs => "상태 (서버·세션)",
            Action::ToggleAutoresume => "자동재개",
            Action::ShowAutoresume => "자동재개 설명",
            Action::ShowCursor => "커서",
            Action::ShowDebugStats => "debug-stats",
            Action::TogglePromptClear => "프롬프트 클리어",
            Action::SetLang(_) => "언어",
            Action::Reconnect => "재접속",
            Action::ToggleFullscreen => "전체 화면",
            Action::RestartAll => "전체 재시작",
            Action::Quit => "종료",
        })
    }
}

/// 방향 — 패널 선택·크기 조절이 쓴다.
///
/// 값의 철자는 **서버 어휘**(`left`/`right`/`up`/`down`)로 옮겨진다(`proto::command`).
/// 여기서는 이름만 갖는다 — core 는 서버를 모른다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Dir {
    Left,
    Right,
    Up,
    Down,
}

/// 키 이름 하나와 그것이 일으키는 액션.
#[derive(Debug, Clone, Copy)]
pub struct Binding {
    /// `warpui_core::keymap` 표기의 키 이름(`"j"`, `"down"`, `"escape"` 등).
    pub key: &'static str,
    pub action: Action,
    /// 도움말 줄에 이 키를 대표로 보여 줄지. 같은 액션에 여러 키가 걸려 있어도
    /// 도움말은 하나만 보여 주려는 것이다.
    pub show_in_help: bool,
}

const fn b(key: &'static str, action: Action, show_in_help: bool) -> Binding {
    Binding {
        key,
        action,
        show_in_help,
    }
}

/// **블록 목록 데모 판**의 키 표 — 서버 링크가 없을 때 뜨는 창([`crate::block`] ·
/// `gui::root_view`)이 등록하는 것이고, **세션 뷰의 esc 모드는 이 표를 안 본다**.
///
/// # 왜 갈랐나 (pytmux-466 · 449 ⑶)
///
/// 종전에는 이 여덟이 아래 [`BINDINGS`] 에 섞여 있어 **데모 판의 키가 세션 esc 모드로
/// 샜다.** 그 결과가 갈림 대장의 esc 줄 일곱이었고, 그중 `q` 만 결과가 있었다 —
/// 정본의 `esc q` 는 모드만 푸는데 우리는 **창을 닫았다**(서버·패널은 살아 detach 와
/// 같지만, 손버릇이 갈리면 창이 닫힌다). 사람 결정(2026-09-03)이 «표를 가른다» 였다.
///
/// ⛔ 두 표는 **겹치지 않는다**(`the_two_tables_do_not_overlap` 이 지킨다) — 겹치면
/// 「어느 표를 보나」가 화면마다 달라져 갈랐던 이유가 도로 사라진다.
///
/// `escape` 가 여기 있고 저기 없는 이유: esc 모드에서는 [`crate::keys::interpret`] 이
/// 표보다 **먼저** 가로채 모드만 푼다(둘째 ESC 를 패널로 안 보낸다 · 56632 불변).
/// 그래서 저 표의 `escape` 줄은 도달 불가였다.
pub static BLOCK_BINDINGS: &[Binding] = &[
    // `j`/`k` 만 남았다. 화살표는 **파이썬이 패널 이동에 쓴다**(아래 esc 표) — 같은 키가
    // 두 클라에서 다른 일을 하면 손버릇이 갈리고, 그건 이 트랙이 없애려는 바로 그것이다.
    b("j", Action::SelectNext, true),
    b("k", Action::SelectPrev, true),
    b("g", Action::SelectFirst, true),
    // `shift-G` 로 적는 이유: 이 표는 **두 뷰가 같은 문법으로** 읽어야 한다. GUI 는
    // `Keystroke::parse` 로, TUI 는 같은 파서를 거쳐 등록한다. 대문자 하나만 적으면
    // GUI 가 첫 프레임에 패닉했다(2026-07-28, GUI 를 Windows 에서 처음 띄우며 발견 —
    // GUI 가 P1 에 멈춰 있어 아무도 이 표를 등록해 본 적이 없었다).
    b("shift-G", Action::SelectLast, true),
    b("enter", Action::ToggleExpand, true),
    b("space", Action::ToggleExpand, false),
    b("q", Action::Quit, true),
    b("escape", Action::Quit, false),
];

/// **세션 뷰 esc(명령) 모드**의 키 표([`crate::keys::command_action`] 이 정본으로 본다).
/// 순서가 곧 [`key_help_lines`] 의 esc 절 순서다.
pub static BINDINGS: &[Binding] = &[
    // Claude 플랜/거부 전문. 파이썬에는 없는 화면이라 파이썬이 안 쓰는 글자로 옮겼다
    // (`p` 는 파이썬에서 **상하 분할**이다 — G1c 이전에는 우리 `p` 가 이 화면이었다).
    b("v", Action::ToggleClaudeDetail, true),
    // ── 파이썬 esc 모드와 같은 글자(G1c) ──────────────────────────────────
    //
    // 전부 `show_in_help: false` 다. 이 표의 **한 줄짜리** 도움말은 블록 데모 뷰의
    // 것이고, 아홉 줄을 더 넣으면 100칸 화면에서 잘린다(그때는 도움말이 없느니만 못하다).
    // 키 도움말 **화면**에는 아래 `key_help_lines` 가 전부 싣는다.
    b("left", Action::SelectPane(Dir::Left), false),
    b("right", Action::SelectPane(Dir::Right), false),
    b("up", Action::SelectPane(Dir::Up), false),
    b("down", Action::SelectPane(Dir::Down), false),
    b("n", Action::NewTab, false),
    // 정본 `esc c` 와 같은 글자다(pytmux-137). **ESC 모드에서 비어 있던 글자**라
    // 손버릇이 겹치지 않는다 — prefix 의 `c`(새 탭 · tmux 관습)도 `esc n`(새 탭)도
    // 그대로다.
    b("c", Action::NewClaudeTab, false),
    b("p", Action::SplitTopBottom, false),
    b("shift-P", Action::TogglePin, false),
    // 파이썬 클라의 `esc f`(e_f)와 같은 글자다(pytmux-27). 전역 검색 물음을 연다.
    b("f", Action::SearchAll, false),
    // 명령 모드 안에서 vim·less 에게 ESC 를 주는 길. 그냥 `ESC` 는 모드에서 나가는 키다.
    b("e", Action::SendEscape, false),
    b("`", Action::SendBacktick, false),
    // tmux 의 copy-mode 관습(prefix `[`)을 그대로 쓴다 — 파이썬 클라도 `esc` `[` 다.
    b("[", Action::EnterScroll, true),
    // 블록 고르기(pytmux-18). **정본에 없는 글자**라 안전하다 — 정본의 esc 모드 키
    // 열일곱에 `b` 는 없다(`client_surface.json` 의 `esc_keys`). 정본이 나중에 같은
    // 글자를 쓰게 되면 그때는 정본을 따른다(손버릇이 갈리는 쪽이 더 나쁘다).
    b("b", Action::SelectBlocks, false),
    // 파이썬 클라의 `esc ?`(e_help)와 같은 글자다. 도움말은 **키를 외우지 않아도 되게**
    // 하는 것이라, 그 입구가 클라마다 다르면 목적을 반쯤 잃는다.
    //
    // `show_in_help: false` 인 이유: 이 표의 한 줄짜리 도움말은 **블록 데모 뷰**가 쓰는데,
    // 거기에 이 키까지 넣으면 100칸 화면에서 줄이 잘린다. 도움말 화면에는
    // `key_help_lines` 가 따로 싣는다(아래 — 그 화면을 여는 키를 그 화면이 안 보이면
    // 닫는 법을 모르게 된다).
    b("?", Action::ShowKeys, false),
    // 파이썬 클라의 `esc Tab`(e_tab)과 같은 글자다. `show_in_help: false` 인 이유는
    // `?` 와 같다 — 이 표의 한 줄짜리 도움말은 블록 데모 뷰의 것이고, 거기에 다 넣으면
    // 100칸 화면에서 줄이 잘린다. 도움말 **화면**에는 아래 `key_help_lines` 가 싣는다.
    b("tab", Action::ShowTabs, false),
    // 파이썬 클라의 `esc :`(e_colon)과 같은 글자다. prefix 에도 같은 것이 있다 —
    // 두 모드 어느 쪽에서 부르든 같은 화면이어야 한다.
    b(":", Action::ShowCommands, false),
    // 파이썬 클라의 `esc Ctrl+↑/↓`(e_jump)와 같은 키다. **이 표에서 수정키가 붙은 첫
    // 키**라, `command_action` 이 수정키 조합을 통째로 버리던 것을 여기서 열었다
    // (그전에는 이 두 줄을 적어도 영영 안 찾아졌다).
    b("ctrl-up", Action::JumpPrompt { up: true }, false),
    b("ctrl-down", Action::JumpPrompt { up: false }, false),
    // OS 관례(Cmd/Ctrl+,)를 좇은 신규 단축키다(pytmux-178) — 정본 TUI 에도 없어 패리티
    // 대상이 아니다. `Esc` 프리픽스를 타는 이유: 이 표(BINDINGS)는 ESC 모드 전용이고,
    // pty 로 가는 평상시 입력을 가로채는 자리가 따로 없다 — 진짜 전역(프리픽스 없는)
    // Ctrl+, 는 그 가로채는 층 자체를 새로 내는 더 큰 일이라 이번엔 손대지 않았다.
    b("ctrl-,", Action::ShowSettings, false),
    // 파이썬 클라의 `esc Insert`(e_ins)와 같은 키다. `shift-delete` 는 Insert 키가 없는
    // 맥 자판용 **동형 별칭**이고, 파이썬도 둘 다 받는다(그쪽에서도 esc 모드의
    // shift+delete 는 달리 쓰이지 않아 무해하다).
    b("insert", Action::ShowCompose, false),
    b("shift-delete", Action::ShowCompose, false),
];

/// prefix 모드(기본 `Ctrl+B`)의 키 표 — **파이썬 클라의 `PREFIX_KEYS` 와 같은 자리**.
///
/// # 왜 별도 표인가
///
/// 위 [`BINDINGS`] 는 블록 목록 데모 뷰의 것이다(`j`/`k` 로 블록을 고른다). 세션 뷰의
/// prefix 는 tmux 관습을 따르는 다른 어휘라, 한 표에 섞으면 같은 키가 화면마다 다른 일을
/// 하게 된다.
///
/// # 파이썬과 같은 글자를 쓴다
///
/// 패리티의 기준은 **사용자의 손버릇**이다. `%` 가 좌우 분할인 것은 tmux 에서 왔고 파이썬
/// 클라가 그대로 쓴다 — 네이티브가 다른 글자를 고르면 두 클라를 오갈 때마다 손이 어긋난다.
/// 아직 못 옮긴 키는 여기 없고, **없다는 사실 자체가 `tests/parity.rs` 에 점수로 남는다**.
pub static PREFIX_BINDINGS: &[Binding] = &[
    b("%", Action::SplitLeftRight, true),
    b("\"", Action::SplitTopBottom, true),
    b("x", Action::KillPane, true),
    b("c", Action::NewTab, true),
    b("&", Action::KillTab, true),
    b("n", Action::NextTab, true),
    b("p", Action::PrevTab, true),
    b("l", Action::LastTab, true),
    b("r", Action::Redraw, true),
    b("[", Action::EnterScroll, true),
    // ── G1b: 서버에 이미 있던 조작들 ────────────────────────────────────────
    b("z", Action::Zoom, true),
    b("o", Action::NextPane, true),
    b(";", Action::LastPane, true),
    b("space", Action::CycleLayout, true),
    b("ctrl-o", Action::RotatePanes, true),
    b("{", Action::SwapPane { forward: false }, true),
    b("}", Action::SwapPane { forward: true }, false),
    b("!", Action::BreakPane, true),
    b("left", Action::SelectPane(Dir::Left), true),
    b("right", Action::SelectPane(Dir::Right), false),
    b("up", Action::SelectPane(Dir::Up), false),
    b("down", Action::SelectPane(Dir::Down), false),
    // 대문자는 `shift-` 를 붙여 적는다 — 이 표를 읽는 곳이 셋이고 셋 다 같은 문법이어야
    // 한다(2026-07-28 에 GUI 가 첫 프레임에 패닉한 자리).
    b("shift-H", Action::ResizePane(Dir::Left), true),
    b("shift-J", Action::ResizePane(Dir::Down), false),
    b("shift-K", Action::ResizePane(Dir::Up), false),
    b("shift-L", Action::ResizePane(Dir::Right), false),
    b("shift-P", Action::TogglePin, true),
    // 파이썬 `p_R` — 토큰리밋 자동재개 토글. 플러그인 소유 조작이지만 화면이 없는 순수
    // 토글이라 키 하나가 표면의 전부다(`Action::ToggleAutoresume` 문서 참조).
    b("shift-R", Action::ToggleAutoresume, true),
    b("]", Action::PasteBuffer, true),
    b("w", Action::ShowTree, true),
    b("=", Action::ShowBuffers, true),
    b(",", Action::RenameTab, true),
    b(".", Action::MoveTab, true),
    b(":", Action::ShowCommands, true),
    // tmux 의 `prefix t` 와 같은 자리다(clock 플러그인).
    b("t", Action::ToggleClock, true),
    b("shift-T", Action::RenamePane, true),
    b("q", Action::ShowPaneNumbers, true),
    b("enter", Action::ShowMenu, true),
    // detach = 이 클라를 닫는다. 네이티브 클라에서는 창을 닫는 것이 곧 detach 다
    // (서버와 다른 클라는 그대로 산다).
    b("d", Action::Quit, true),
];

/// prefix 모드에서 이 표에 없는 키는 **조용히 버리고 모드만 푼다**(tmux 와 같다).
///
/// 번호 키(`0`~`9`)만은 표가 아니라 규칙이다 — 열 줄을 적는 대신 여기서 판정한다.
pub fn prefix_number(key: char) -> Option<Action> {
    key.to_digit(10).map(|n| Action::SelectTab(n as u8))
}

/// F10 메뉴 한 줄 — **파이썬 `MENU_ITEMS` 의 문구 그대로**와 그것이 일으키는 액션.
///
/// # 왜 문구를 그대로 쓰나
///
/// 메뉴는 **이름을 모르는 사람**의 입구다(팔레트는 아는 사람의 것이다). 문구가 두 클라에서
/// 다르면 눈으로 찾던 줄이 없어진 것처럼 보인다.
///
/// # 무엇을 싣나
///
/// **할 수 있는 것만** 싣는다. 못 하는 줄을 실으면 골랐을 때 아무 일도 안 일어나고, 메뉴에서
/// 그건 팔레트보다 더 나쁘다(메뉴를 여는 사람은 목록을 다 읽는다). 못 하는 것의 목록은
/// 패리티 표가 센다. 지금은 파이썬 `MENU_ITEMS` 31줄을 **전부** 덮었다.
///
/// 화면에 늘어놓는 **차례**는 이 표가 아니라 [`MENU_TOPLEVEL`]·[`MENU_GROUPS`] 가 정한다
/// (평면 31줄은 세로로 너무 길다 — 정본도 같은 이유로 접었다).
#[derive(Debug, PartialEq, Eq)]
pub struct MenuEntry {
    /// 파이썬 `MENU_ITEMS` 의 **키**. 라벨이 아니라 이것이 계층 표(`MENU_GROUPS`·
    /// `MENU_TOPLEVEL`)와 토글 목록이 항목을 부르는 이름이다 — 라벨로 부르면 문구를
    /// 한 글자 고칠 때마다 세 표가 조용히 어긋난다.
    pub key: &'static str,
    pub label: &'static str,
    pub action: Action,
}

const fn me(key: &'static str, label: &'static str, action: Action) -> MenuEntry {
    MenuEntry { key, label, action }
}

/// 메뉴 항목. 순서도 파이썬과 같다(눈이 외운 자리가 그대로여야 한다).
// 파이썬 `MENU_ITEMS`(31줄)와 **같은 순서·같은 31줄**이다(마지막 줄 search 는 G9t).
pub static MENU: &[MenuEntry] = &[
    me("split_lr", "패널 분할 │ (좌우)", Action::SplitLeftRight),
    me("split_tb", "패널 분할 ─ (상하)", Action::SplitTopBottom),
    me("zoom", "패널 줌 토글 ⛶", Action::Zoom),
    me("rotate", "패널 회전 ↻", Action::RotatePanes),
    me("swap_pane", "패널 교환 (다음 패널과)", Action::SwapPane { forward: true }),
    me("break_pane", "패널 → 새 탭으로 분리", Action::BreakPane),
    me("join_pane", "패널 → 다른 탭에 합치기 (join-pane <탭>)", Action::JoinPane),
    me("merge_remote_tab", "원격 탭 → 현재 탭에 pane 으로 머지 (같은 서버)", Action::MergeRemoteTab),
    me("rename_pane", "패널 제목 변경", Action::RenamePane),
    me("select_layout", "레이아웃 프리셋…", Action::ShowLayouts),
    me("next_layout", "다음 레이아웃 프리셋", Action::CycleLayout),
    me("search", "스크롤백 검색", Action::SearchScrollback),
    me("search_all", "모든 탭·패널 검색", Action::SearchAll),
    me("kill_pane", "패널 삭제 ✕", Action::KillPane),
    me("sync", "입력 동기화 토글", Action::ToggleSync),
    me("autoresume", "토큰리밋 자동재개 토글", Action::ToggleAutoresume),
    me("prompt_clear", "프롬프트 단위 클리어 토글", Action::TogglePromptClear),
    me("new_window", "새 탭", Action::NewTab),
    me("new_claude_window", "새 탭에서 Claude Code 실행", Action::NewClaudeTab),
    me("rename_window", "탭 이름 변경", Action::RenameTab),
    me("kill_window", "탭 삭제", Action::KillTab),
    me("toggle_pin", "탭 고정 토글 (오른쪽 구역으로)", Action::TogglePin),
    me("choose_tree", "탭 선택기(트리)", Action::ShowTree),
    me("next_window", "다음 탭", Action::NextTab),
    me("prev_window", "이전 탭", Action::PrevTab),
    me("layout_save", "레이아웃 저장(현재 탭)", Action::SaveTabLayout),
    me("layout_load_over", "레이아웃 불러오기(현재 탭 덮어쓰기)", Action::LoadTabLayout(false)),
    me("layout_load_new", "레이아웃 불러오기(새 탭)", Action::LoadTabLayout(true)),
    me("mouse_help", "마우스 제스처 도움말", Action::ShowKeys),
    me("command", "명령 입력", Action::ShowCommands),
    me("settings", "⚙ 설정…", Action::ShowSettings),
    me("detach", "detach (앱 종료, 셸 유지)", Action::Quit),
    me("kill_server", "서버 종료 (모든 탭/셸 종료)", Action::KillServer),
];

// ── 플러그인이 낸 메뉴 줄은 여기 없다 ────────────────────────────────────────
//
// 2026-08-01(설계 P2)까지는 `calendar-mode`·`clock-mode` 두 줄이 이 표에 **손으로**
// 적혀 있었다. 그러면 세 가지가 조용히 틀린다:
//
// 1. 서버가 그 플러그인을 안 실어도 줄이 남는다 — delete-to-disable 이 우리 쪽에서만
//    거짓이 된다(코어가 플러그인을 직접 모른다는 계약을 화면이 깬다).
// 2. 새 플러그인이 낸 줄은 영영 안 뜬다(정본에만 생긴다).
// 3. 문구를 플러그인이 고쳐도 우리는 옛 글을 보인다.
//
// 이제 줄의 출처는 **서버가 부는 표면**이다(`plugins::PluginSurface::menu_items`) —
// 화면이 그것을 [`menu_rows`] 에 넘긴다.

/// 메뉴 **서브메뉴**의 멤버 — 파이썬 `MENU_GROUPS` 그대로(키는 `MENU` 의 키).
///
/// 평면 31줄은 세로로 너무 길다(정본이 2026-06-18 에 같은 이유로 접었다). 묶을 수 있는
/// 것을 그룹으로 접고 자주 쓰는 것·세션 동작만 최상위에 둔다. **모든 액션은 여전히
/// 서브메뉴를 지나 도달 가능**하고, 실행은 leaf 키 그대로다.
pub static MENU_GROUPS: &[(&str, &[&str])] = &[
    (
        "pane",
        &[
            "split_lr",
            "split_tb",
            "zoom",
            "rotate",
            "swap_pane",
            "break_pane",
            "join_pane",
            "merge_remote_tab",
            "rename_pane",
            "kill_pane",
        ],
    ),
    (
        "layout",
        &["select_layout", "next_layout", "layout_save", "layout_load_over", "layout_load_new"],
    ),
    (
        "tab",
        &[
            "new_window",
            "new_claude_window",
            "rename_window",
            "kill_window",
            "toggle_pin",
            "choose_tree",
            "next_window",
            "prev_window",
        ],
    ),
];

/// 그룹 진입점·서브메뉴 머리줄에 적을 이름(파이썬 `MENU_GROUP_LABELS`).
pub static MENU_GROUP_LABELS: &[(&str, &str)] = &[
    ("pane", "패널"),
    ("layout", "레이아웃"),
    ("tab", "탭"),
    ("plugin", "플러그인"),
];

/// 지금 on/off 를 옆에 적는 항목(파이썬 `MENU_TOGGLES`). 고른 뒤에도 메뉴를 안 닫는다 —
/// 토글은 보통 여러 개를 잇달아 만진다.
pub static MENU_TOGGLES: &[&str] = &["autoresume", "prompt_clear", "sync", "toggle_pin", "zoom"];

/// 메뉴 최상위 한 줄(파이썬 `MENU_TOPLEVEL` 의 `"group:<g>"` · `"--"` · 낱개 키).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MenuTop {
    /// 서브메뉴 진입점.
    Group(&'static str),
    /// 고를 수 없는 구분선 — **파괴적 동작을 손가락에서 떼어 놓는 자리**다.
    Separator,
    /// 바로 실행되는 항목(`MENU` 의 키).
    Item(&'static str),
}

/// 최상위 표시 순서 — 파이썬 `MENU_TOPLEVEL` 과 **같은 줄·같은 차례**다.
pub static MENU_TOPLEVEL: &[MenuTop] = &[
    MenuTop::Group("pane"),
    MenuTop::Group("layout"),
    MenuTop::Group("tab"),
    MenuTop::Separator,
    MenuTop::Item("search"),
    MenuTop::Item("search_all"),
    MenuTop::Item("command"),
    MenuTop::Item("settings"),
    MenuTop::Item("mouse_help"),
    MenuTop::Item("sync"),
    MenuTop::Item("autoresume"),
    MenuTop::Item("prompt_clear"),
    MenuTop::Separator,
    MenuTop::Item("detach"),
    MenuTop::Item("kill_server"),
];

/// 메뉴 한 층에 그릴 줄. 뷰가 `MENU_TOPLEVEL` 을 직접 풀지 않는 이유는 늘 같다 —
/// 두 뷰가 각자 풀면 한쪽에만 있는 줄이 생긴다(계층 게이트와 같은 뿌리).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum MenuRow {
    /// 서브메뉴 진입점(그룹 키). 라벨은 [`menu_group_label`].
    Group(&'static str),
    /// 구분선 — 고를 수 없다.
    Separator,
    /// 실행되는 항목.
    Item(&'static MenuEntry),
    /// **플러그인이 낸 줄** — 서버 표면(`PluginSurface::menu_items`)의 자리다.
    ///
    /// 라벨·명령 이름을 여기 안 담고 번호만 드는 이유: 이 enum 은 `Copy` 이고(화면·키
    /// 처리가 줄을 자주 복사한다) 런타임 문자열은 `Copy` 가 아니다. 자리로 가리키면
    /// 문구의 주인이 서버 한 곳으로 남는다.
    Plugin(usize),
}

impl MenuRow {
    /// 이 줄에 커서를 올릴 수 있나(구분선은 못 올린다).
    pub fn selectable(&self) -> bool {
        !matches!(self, MenuRow::Separator)
    }
}

/// `key` 를 가진 메뉴 항목.
pub fn menu_entry(key: &str) -> Option<&'static MenuEntry> {
    MENU.iter().find(|e| e.key == key)
}

/// 그룹 진입점에 적을 이름(로케일 적용).
/// 표에 없는 그룹이면 빈 문자열이다 — `MENU_TOPLEVEL` 이 드는 이름은 전부 표에 있고
/// (적합성 테스트가 강제한다), 없는 이름을 지어내 보여 주는 것보다 낫다.
pub fn menu_group_label(group: &str) -> &'static str {
    let Some((_, ko)) = MENU_GROUP_LABELS.iter().find(|(g, _)| *g == group) else {
        return "";
    };
    // 문맥 키다 — "탭"·"패널"·"레이아웃" 은 다른 자리에서도 쓰이는 흔한 낱말이라
    // 평문 키로 두면 남의 번역과 부딪힌다(`setcat` 이 같은 이유로 문맥 키를 쓴다).
    crate::i18n::tc("menugroup", ko)
}

/// 그 항목이 on/off 를 옆에 다는 토글인가.
pub fn menu_is_toggle(key: &str) -> bool {
    MENU_TOGGLES.contains(&key)
}

/// 메뉴 토글 다섯의 **지금 값**. 뷰가 채운다 — core 는 서버 어휘를 모른다
/// (`chrome::ChromeCtx` 와 같은 갈래).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct MenuToggles {
    pub zoom: bool,
    pub sync: bool,
    pub autoresume: bool,
    pub prompt_clear: bool,
    /// **활성 탭**이 고정돼 있나(나머지 넷과 달리 탭마다 다른 값이다).
    pub toggle_pin: bool,
}

/// 그 줄 뒤에 붙일 표식 — 정본과 같은 `●`(켜짐)·`○`(꺼짐). 토글이 아니면 `None`.
///
/// # 왜 값을 화면에 다나
///
/// `[동기화]` 를 모르고 켜면 **친 글자가 모든 패널로 복제된다**. 메뉴에 "토글"이라고만
/// 적혀 있으면 누르기 전에는 지금 어느 쪽인지 알 수 없고, 그건 되돌리기 전에 이미
/// 일어나는 부류다.
pub fn menu_toggle_mark(key: &str, now: &MenuToggles) -> Option<&'static str> {
    let on = match key {
        "zoom" => now.zoom,
        "sync" => now.sync,
        "autoresume" => now.autoresume,
        "prompt_clear" => now.prompt_clear,
        "toggle_pin" => now.toggle_pin,
        _ => return None,
    };
    Some(if on { "●" } else { "○" })
}

/// 지금 보여 줄 메뉴 한 층. `open_group` 이 `None` 이면 최상위, 아니면 그 서브메뉴다.
///
/// 모르는 그룹 이름은 **빈 층**이 아니라 최상위로 떨어뜨린다 — 빈 팝업은 사용자에게
/// "메뉴가 고장 났다"로 보이고, 그 상태에서 빠져나갈 줄도 없다.
/// `plugin_items` 는 서버가 부는 플러그인 메뉴 줄 수다(`PluginSurface::menu_items.len()`).
/// **0 이면 `플러그인` 그룹 자체가 없다** — 빈 서브메뉴 진입점이 남으면 "눌러도 아무것도
/// 없는 줄"이 되고, 그건 정본에서 플러그인 디렉토리를 지웠을 때의 화면과도 다르다.
pub fn menu_rows(open_group: Option<&str>, plugin_items: usize) -> Vec<MenuRow> {
    if let Some(group) = open_group {
        if group == "plugin" {
            if plugin_items > 0 {
                return (0..plugin_items).map(MenuRow::Plugin).collect();
            }
        } else if let Some((_, keys)) = MENU_GROUPS.iter().find(|(g, _)| *g == group)
            && !keys.is_empty()
        {
            return keys.iter().filter_map(|k| menu_entry(k).map(MenuRow::Item)).collect();
        }
    }
    let mut rows: Vec<MenuRow> = Vec::new();
    for top in MENU_TOPLEVEL {
        match top {
            MenuTop::Group(g) => {
                rows.push(MenuRow::Group(g));
                // 정본 `MenuScreen._toplevel_entries`: 플러그인 줄이 있으면 `group:tab`
                // **바로 뒤**에 끼운다. 정적 표에 못 두는 이유는 위 「플러그인이 낸 메뉴
                // 줄은 여기 없다」 주석 참조.
                if *g == "tab" && plugin_items > 0 {
                    rows.push(MenuRow::Group("plugin"));
                }
            }
            MenuTop::Separator => rows.push(MenuRow::Separator),
            MenuTop::Item(k) => rows.extend(menu_entry(k).map(MenuRow::Item)),
        }
    }
    rows
}

/// 값이 셋 중 하나인 서버 옵션.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EnumOpt {
    /// VT 파서 백엔드(`pyte`/`native`). **재시작해야 발효된다.**
    VtParser,
    /// 여러 클라가 볼 때의 공유 크기 규칙(`smallest`/`latest`/`largest`).
    WindowSize,
}

impl EnumOpt {
    pub fn label(self) -> &'static str {
        crate::i18n::t(match self {
            EnumOpt::VtParser => "VT 파서",
            EnumOpt::WindowSize => "창 크기 규칙",
        })
    }

    /// 고를 수 있는 값들 — **서버가 읽는 철자** 그대로.
    pub fn choices(self) -> &'static [&'static str] {
        match self {
            EnumOpt::VtParser => &["pyte", "native"],
            EnumOpt::WindowSize => &["smallest", "latest", "largest"],
        }
    }

    /// `now` 다음 값(끝이면 처음으로). 모르는 값이면 첫 번째다.
    pub fn next(self, now: &str) -> &'static str {
        self.step(now, true)
    }

    /// `forward` 면 다음, 아니면 **이전** 값(정본 설정 화면의 `←→ 값 변경`).
    ///
    /// 뒤로 도는 길이 필요한 이유: 선택지가 셋 이상이면(`window-size` 셋 · `vt-parser` 둘)
    /// 하나 지나쳤을 때 앞으로 한 바퀴 더 도는 수밖에 없다.
    pub fn step(self, now: &str, forward: bool) -> &'static str {
        let choices = self.choices();
        let at = choices.iter().position(|c| *c == now).unwrap_or(usize::MAX);
        let len = choices.len();
        choices[if forward { at.wrapping_add(1) % len } else { at.wrapping_add(len - 1) % len }]
    }
}

/// 레이아웃 프리셋 — **파이썬 `COMMAND_OPTIONS["select-layout"]` 의 문구와 값** 그대로.
///
/// 값(`tiled` 등)은 서버가 읽는 이름이라 틀리면 조용히 아무 일도 안 하고, 문구는 사용자가
/// 눈으로 찾는 것이라 다르면 못 찾는다 — 둘 다 저쪽에서 가져온다.
pub static LAYOUT_PRESETS: &[(&str, &str)] = &[
    ("바둑판 tiled", "tiled"),
    ("가로 균등 even-horizontal", "even-horizontal"),
    ("세로 균등 even-vertical", "even-vertical"),
    ("메인 세로 main-vertical", "main-vertical"),
    ("메인 가로 main-horizontal", "main-horizontal"),
];

/// 탭을 어디로 옮기나.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TabMove {
    Left,
    Right,
    First,
    Last,
}

impl TabMove {
    pub fn label(self) -> &'static str {
        crate::i18n::t(match self {
            TabMove::Left => "탭 왼쪽으로",
            TabMove::Right => "탭 오른쪽으로",
            TabMove::First => "탭 맨 앞으로",
            TabMove::Last => "탭 맨 뒤로",
        })
    }

    /// `now` 번째 탭을 `len` 개 중에서 옮길 **목표 자리**. 이미 끝이면 `None` —
    /// 그때 명령을 보내면 서버가 같은 자리로 옮기며 화면만 한 번 출렁인다.
    pub fn target(self, now: usize, len: usize) -> Option<usize> {
        if len == 0 {
            return None;
        }
        let last = len - 1;
        let to = match self {
            TabMove::Left => now.checked_sub(1)?,
            TabMove::Right => (now + 1).min(last),
            TabMove::First => 0,
            TabMove::Last => last,
        };
        (to != now).then_some(to)
    }
}

/// 값 없이 뒤집는 서버 옵션들(core 쪽 어휘). 와이어 이름은 proto 가 안다 — core 는
/// 서버 어휘를 모른다는 계약이라 여기서는 **뜻만** 든다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ServerOpt {
    SingleBorder,
    CoalesceRepaints,
    ExitEmpty,
    NestAutoAttach,
    WinMouseMotion,
}

impl ServerOpt {
    /// 화면에 적을 이름.
    pub fn label(self) -> &'static str {
        crate::i18n::t(match self {
            ServerOpt::SingleBorder => "단일 테두리",
            ServerOpt::CoalesceRepaints => "갱신 모으기",
            ServerOpt::ExitEmpty => "빈 서버 종료",
            ServerOpt::NestAutoAttach => "중첩 자동 attach",
            ServerOpt::WinMouseMotion => "Windows 마우스 이동",
        })
    }
}

/// 명령 팔레트 한 줄 — **파이썬 클라의 명령 이름**과 그것이 일으키는 액션.
///
/// # 왜 파이썬 이름인가
///
/// 팔레트는 "이름으로 부르는" 입구다. 이름이 두 클라에서 다르면 사용자가 외운 것이
/// 반쪽만 통한다 — 그래서 `clientutil.COMMANDS` 의 철자를 그대로 쓴다(인자가 있는 것은
/// tmux 표기 그대로 `split-window -h` 처럼 적는다).
///
/// # 왜 액션인가(명령이 아니라)
///
/// 팔레트에서 고른 것도 **키로 누른 것과 같은 길**을 타야 한다. 그래야 `kill-pane` 을
/// 팔레트로 고를 때도 확인 화면이 뜬다(명령을 바로 보내면 그 화면을 건너뛴다).
pub struct PaletteEntry {
    pub name: &'static str,
    /// 팔레트 탭 그룹 — **파이썬 `clientutil.COMMANDS` 의 세 번째 칸** 그대로다.
    ///
    /// 손으로 옮기지 않는다: `scripts/gen_categories.py` 가 정본(+플러그인 레지스트리)에서
    /// 뽑은 `tests/fixtures/categories.json` 과 `category_conformance.rs` 가 대조한다.
    /// 정본이 모르는 이름(우리 전용 별칭 등)만 그 테스트의 허용 목록에 이름으로 적힌다.
    pub cat: &'static str,
    pub action: Action,
}

const fn pe(name: &'static str, cat: &'static str, action: Action) -> PaletteEntry {
    PaletteEntry { name, cat, action }
}

/// 화면에서 **감춘** 표면 이름(§10-21ⓜ) — 기능은 남기고 **입구만** 닫는다.
///
/// # 왜 지우지 않나
///
/// 제보는 *"`bell monitor` 를 화면에서 숨긴다 — 당장은 지원하지 않겠다"* 였다(사용자
/// 결정). **지우는 것이 아니다**: 표를 지우면 그 명령을 아는 사람의 키·설정 파일이
/// 조용히 죽고, 패리티 표에서도 "우리가 못 하는 것"이 되어 정본과의 차이가 실제보다
/// 커 보인다. 우리는 여전히 할 수 있고, **보여 주지 않을** 뿐이다.
///
/// 거르는 자리는 **화면을 만드는 곳 전부**여야 한다 — 하나라도 빠지면 그 입구로 다시
/// 보인다(팔레트·설정 화면·상태줄 표식). 그래서 목록을 한 곳에 두고 각자 물어본다.
pub static HIDDEN_SURFACES: &[&str] = &["monitor-bell"];

/// 그 이름이 화면에서 감춰졌나.
pub fn is_hidden(name: &str) -> bool {
    HIDDEN_SURFACES.contains(&name)
}

/// 팔레트에 뜨는 것 — **지금 이 클라가 실제로 할 수 있는 것만** 싣는다.
///
/// 못 하는 이름을 목록에 두면 고르는 순간 아무 일도 안 일어나고, 그건 "명령이 있는데 안
/// 먹는다"로 읽힌다. 못 하는 것들은 목록이 아니라 **패리티 표**가 센다
/// (`proto/tests/parity.rs`).
pub static PALETTE: &[PaletteEntry] = &[
    // ★ **폼이 먼저다.** 필터로 좁히면 첫 줄이 골라지는데, 이름만 아는 사람에게
    // 먼저 걸려야 하는 것은 "무엇을 고를 수 있나"를 보여 주는 폼이다. 플래그를 이름에
    // 품은 아래 줄들은 **지우지 않는다** — 한 번에 고르는 손버릇이 이미 있다.
    pe("split-window", "패널", Action::ShowCommandOptions("split-window")),
    pe("split-window -h", "패널", Action::SplitLeftRight),
    pe("split-window -v", "패널", Action::SplitTopBottom),
    pe("kill-pane", "패널", Action::KillPane),
    pe("resize-pane", "패널", Action::ShowCommandOptions("resize-pane")),
    pe("resize-pane -Z", "패널", Action::Zoom),
    pe("select-pane", "패널", Action::ShowCommandOptions("select-pane")),
    pe("select-pane -t next", "패널", Action::NextPane),
    pe("select-pane -t last", "패널", Action::LastPane),
    pe("rotate-window", "패널", Action::RotatePanes),
    pe("swap-pane -D", "패널", Action::SwapPane { forward: true }),
    pe("swap-pane -U", "패널", Action::SwapPane { forward: false }),
    pe("break-pane", "패널", Action::BreakPane),
    pe("next-layout", "패널", Action::CycleLayout),
    pe("new-tab", "탭", Action::NewTab),
    pe("new-claude-tab", "탭", Action::NewClaudeTab),
    pe("kill-tab", "탭", Action::KillTab),
    pe("next-tab", "탭", Action::NextTab),
    pe("previous-tab", "탭", Action::PrevTab),
    pe("last-tab", "탭", Action::LastTab),
    pe("rename-tab", "탭", Action::RenameTab),
    pe("move-tab", "탭", Action::MoveTab),
    pe("pin-toggle", "탭", Action::TogglePin),
    pe("choose-tree", "탭", Action::ShowTree),
    pe("choose-buffer", "복사/버퍼", Action::ShowBuffers),
    // ★ **`paste-buffer` 앞이다**(정본 `clientutil.py:1032` 의 2026-06-16 요청).
    //   둘 다 `paste` 전체 접두 일치라 **선언 순서가 곧 기본 선택**이고, 「OS 클립보드가
    //   더 흔한 의도」라 먼저 잡히게 한 것이다. 자리를 바꾸면 같은 타이핑이 정반대
    //   후보를 고른다(pytmux-363 이 그 갈림을 제보로 잡았다).
    pe("paste-clipboard", "복사/버퍼", Action::PasteClipboard),
    pe("paste-buffer", "복사/버퍼", Action::PasteBuffer),
    pe("synchronize-panes", "패널", Action::ShowCommandOptions("synchronize-panes")),
    pe("monitor-activity", "모니터", Action::ShowCommandOptions("monitor-activity")),
    pe("automatic-rename", "탭", Action::ShowCommandOptions("automatic-rename")),
    pe("pane-border-status", "패널", Action::ToggleBorderStatus),
    pe("inactive-dim", "설정/기타", Action::ShowCommandOptions("inactive-dim")),
    pe("inactive-dim-ratio", "설정/기타", Action::ShowCommandOptions("inactive-dim-ratio")),
    pe("strip-box-drawing", "설정/기타", Action::ShowCommandOptions("strip-box-drawing")),
    pe("single-border", "설정/기타", Action::ShowCommandOptions("single-border")),
    pe("coalesce-repaints", "설정/기타", Action::ShowCommandOptions("coalesce-repaints")),
    pe("exit-empty", "설정/기타", Action::ShowCommandOptions("exit-empty")),
    pe("nest-auto-attach", "설정/기타", Action::ShowCommandOptions("nest-auto-attach")),
    pe("win-mouse-motion", "설정/기타", Action::ShowCommandOptions("win-mouse-motion")),
    pe("move-tab-left", "탭", Action::MoveTabBy(TabMove::Left)),
    pe("move-tab-right", "탭", Action::MoveTabBy(TabMove::Right)),
    pe("move-tab-first", "탭", Action::MoveTabBy(TabMove::First)),
    pe("move-tab-last", "탭", Action::MoveTabBy(TabMove::Last)),
    pe("swap-tab", "탭", Action::SwapTab),
    pe("pin-tab", "탭", Action::SetPinned(true)),
    pe("unpin-tab", "탭", Action::SetPinned(false)),
    pe("vt-parser", "설정/기타", Action::ShowCommandOptions("vt-parser")),
    pe("window-size", "설정/기타", Action::ShowCommandOptions("window-size")),
    pe("capture-pane", "복사/버퍼", Action::ShowCommandOptions("capture-pane")),
    pe("capture-pane -S", "복사/버퍼", Action::CapturePane(true)),
    pe("pipe-pane", "복사/버퍼", Action::PipePane),
    pe("join-pane", "패널", Action::JoinPane),
    pe("version", "설정/기타", Action::RequestVersion),
    // 파이썬 팔레트에는 없는 이름이다(저쪽 입구는 상태줄 버튼뿐). 배지 동선을 모르는
    // 사람에게 입구를 하나 더 두는 것이라, 우리 쪽에만 있는 것이 낫다고 봤다.
    pe("status", "설정/기타", Action::ShowInfoTabs),
    // 파이썬 팔레트에도 `auto-resume` 로 있다(플러그인이 기여하는 이름).
    pe("auto-resume", "Claude", Action::ToggleAutoresume),
    // 같은 플러그인의 다른 토글 — 파이썬도 `prompt-clear` 로 기여한다.
    pe("prompt-clear", "Claude", Action::TogglePromptClear),
    // UI 언어(파이썬 `lang` — 인자 폼에서 한국어/English 를 고른다).
    pe("lang", "설정/기타", Action::ShowCommandOptions("lang")),
    // 명령 목록 계열 — 파이썬은 넷 다 CommandListScreen 하나로 간다(`clientcmd.py`
    // 402행: help·commands·?·list-commands). 우리의 그 화면이 **팔레트 자신**이라
    // (이름+설명+타이핑 필터+실행) 여기로 돌아온다.
    pe("commands", "설정/기타", Action::ShowCommands),
    pe("list-commands", "설정/기타", Action::ShowCommands),
    pe("help", "설정/기타", Action::ShowCommands),
    // 파이썬에서 `mouse-help` 는 `list-keys` 의 별칭이다(951행 — 같은 도움말 화면에
    // 마우스 절이 있다). 우리도 같은 화면으로 보낸다.
    pe("mouse-help", "설정/기타", Action::ShowKeys),
    // 파이썬 목록의 `select-tab` 은 프롬프트 프리필로 간다 — 우리는 번호를 고르는
    // 화면(스위처)이 이미 있어 그쪽이 낫다(prefix 0~9 와 같은 목적지).
    pe("select-tab", "탭", Action::ShowTabs),
    // 파이썬은 `reconnect`·`resync` 두 철자를 받는다. 팔레트는 이름을 쳐서 좁히는
    // 화면이라 별칭이 있으면 손버릇이 갈리지 않는다.
    pe("reconnect", "설정/기타", Action::Reconnect),
    pe("resync", "설정/기타", Action::Reconnect),
    // 키를 모르는 사람의 입구 — 팔레트에도 낸다(제보가 요구한 `Alt`+`Enter` 외에).
    pe("fullscreen", "설정/기타", Action::ToggleFullscreen),
    pe("restart-all", "설정/기타", Action::RestartAll),
    pe("restart-check", "설정/기타", Action::RequestRestartCheck),
    pe("merge-remote-tab", "설정/기타", Action::MergeRemoteTab),
    pe("display-popup", "설정/기타", Action::DisplayPopup),
    pe("popup-close", "설정/기타", Action::PopupClose),
    pe("run-shell", "설정/기타", Action::RunShell),
    pe("if-shell", "설정/기타", Action::IfShell),
    pe("display-message", "설정/기타", Action::DisplayMessage),
    pe("source-file", "설정/기타", Action::SourceFile),
    pe("set", "설정/기타", Action::SetOption),
    pe("show-options", "설정/기타", Action::ShowOptions),
    pe("set-hook", "설정/기타", Action::SetHook),
    pe("show-hooks", "설정/기타", Action::ShowHooks),
    pe("send-keys", "복사/버퍼", Action::SendKeys),
    pe("bind-key", "설정/기타", Action::BindKey),
    pe("unbind-key", "설정/기타", Action::UnbindKey),
    pe("send-escape", "복사/버퍼", Action::SendEscape),
    pe("select-layout", "패널", Action::ShowLayouts),
    pe("layout-save", "레이아웃", Action::SaveTabLayout),
    pe("layout-load", "레이아웃", Action::LoadTabLayout(false)),
    pe("layout-load-new", "레이아웃", Action::LoadTabLayout(true)),
    pe("save-layout", "레이아웃", Action::SaveLayout),
    pe("restore-layout", "레이아웃", Action::RestoreLayout),
    pe("clear-history", "복사/버퍼", Action::ClearHistory),
    pe("respawn-pane", "패널", Action::RespawnPane),
    pe("redraw", "설정/기타", Action::Redraw),
    pe("settings", "설정/기타", Action::ShowSettings),
    pe("rename-pane", "패널", Action::RenamePane),
    pe("display-panes", "패널", Action::ShowPaneNumbers),
    pe("menu", "설정/기타", Action::ShowMenu),
    pe("notice-history", "설정/기타", Action::ShowNotices),
    // GUI 만의 판(§10-21ⓓ) — 화면에서 뺀 요약 구역의 새 입구다.
    pe("summary", "설정/기타", Action::ShowSummary),
    // ★ GUI 만의 판(pytmux-375). 정본의 커서는 **호스트 단말의 하드웨어 커서**라
    //   저쪽에 이 이름이 있을 수 없다(`SETTINGS_OURS` 가 같은 근거를 설정 다섯에
    //   대해 적는다). 키를 안 주므로 **팔레트가 유일한 입구**다 — 그 사정은
    //   `Action::ShowCursor` 가 쥔다.
    pe("cursor", "설정/기타", Action::ShowCursor),
    // ★ 정본에도 **같은 이름**이 있다(pytmux-457). 종전에는 패리티 래칫의
    //   `NOT_IN_PALETTE` 가 이 줄을 면제했다 — 사유가 *"파이썬 힙을 재는 명령이라
    //   GUI 에는 잴 것이 없다"* 였는데, 이제 **제 런타임을 재는 판**이 생겨 그 사유가
    //   사라졌다. 면제와 분류는 다른 일이고, 없앤 것은 뒤엣것(「GUI 에 아직 없다」)이다.
    pe("debug-stats", "설정/기타", Action::ShowDebugStats),
    // GUI 만의 모드(pytmux-18). 키(`esc b`)가 주 입구이고 팔레트는 그 키를 모르는
    // 사람의 입구다 — 요약 판(위)이 목록을 **보여 주는** 자리라면 이쪽은 캔버스 위에서
    // 그 블록을 **집는** 자리다.
    pe("select-blocks", "복사/버퍼", Action::SelectBlocks),
    // GUI 만의 줄(§10-21ⓐ) — 키(`Ctrl+=`/`Ctrl+-`/`Ctrl+0`)가 주 입구이고 팔레트는
    // 그 키를 모르는 사람의 입구다. 이름은 설정 키(`font-scale`)와 같은 낱말을 쓴다.
    pe("font-scale-up", "설정/기타", Action::FontScale { up: true }),
    pe("font-scale-down", "설정/기타", Action::FontScale { up: false }),
    pe("font-scale-reset", "설정/기타", Action::FontScaleReset),
    pe("clock-mode", "설정/기타", Action::ToggleClock),
    pe("calendar-mode", "설정/기타", Action::ToggleCalendar),
    // ★ 명시적 켜기/끄기(§10-21ⓡ). 이 넷이 코어 표에 **있어야** 플러그인 줄에서 빠지고
    //   우리가 실행한다 — 없으면 "팔레트엔 보이는데 눌러도 안 먹는" 줄이 된다.
    pe("open-clock", "설정/기타", Action::SetOverlay { name: "clock", on: true }),
    pe("close-clock", "설정/기타", Action::SetOverlay { name: "clock", on: false }),
    pe("open-calendar", "설정/기타", Action::SetOverlay { name: "calendar", on: true }),
    pe("close-calendar", "설정/기타", Action::SetOverlay { name: "calendar", on: false }),
    // 정본은 세 모드지만 우리에게 있는 것은 pane(오버레이) 하나다 — Action 주석 참조.
    pe("usage-view", "Claude", Action::ToggleUsageView),
    // ★ 서버가 이미 받고 있던 플러그인 토글들(pytmux-35). 이름이 코어 표에 **있어야**
    //   플러그인 줄에서 빠지고 우리가 실행한다 — 없으면 "보이는데 안 먹는" 줄이 된다.
    //   대응은 정본 훅이 치는 액션 이름 그대로다(`plugins/claude-code/__init__.py`).
    pe("auto-retry", "Claude", Action::PluginToggle { action: "set_claude_auto_retry" }),
    pe("auto-token-on-exit", "Claude", Action::PluginToggle { action: "set_auto_token_on_exit" }),
    pe("claude-auto-mode", "Claude", Action::PluginToggle { action: "set_claude_auto_mode" }),
    pe("claude-token-debug", "Claude", Action::PluginToggle { action: "set_token_debug" }),
    pe("claude-usage", "Claude", Action::PluginDo { action: "refresh_usage" }),
    pe("plugins", "설정/기타", Action::ShowPlugins),
    pe("plugin-manager", "설정/기타", Action::ShowPlugins),
    pe("kill-server", "설정/기타", Action::KillServer),
    pe("restart-server", "설정/기타", Action::RestartServer),
    pe("remote-attach", "설정/기타", Action::RemoteAttach),
    pe("remote-new-tab", "설정/기타", Action::RemoteNewTab),
    pe("remote-detach", "설정/기타", Action::RemoteDetach),
    pe("list-keys", "설정/기타", Action::ShowKeys),
    pe("detach-client", "설정/기타", Action::Quit),
];

/// 필터에 걸리는 항목들의 **원래 번호**.
///
/// 번호를 돌려주는 이유: 화면은 걸러진 목록을 그리지만, 고른 것을 실행하려면 원래 표의
/// 자리를 알아야 한다. 걸러진 목록을 따로 만들어 들고 다니면 그 둘이 어긋난다.
///
/// **양쪽 다 소문자로** 맞춰 본다. 이름에는 tmux 플래그(`-Z`·`-D`)가 대문자로 들어 있어
/// 한쪽만 낮추면 그 항목은 영영 안 찾힌다. 부분 일치다(`kill` → `kill-pane`·`kill-tab`).
pub fn palette_matches(filter: &str) -> Vec<usize> {
    palette_matches_in(None, filter)
}

/// 팔레트 **탭 순서** — 파이썬 `CommandListScreen` 이 `COMMANDS + plugins.commands` 의
/// 카테고리 등장 순서로 만드는 그 차례에서, 우리가 실제로 가진 것만 남긴 것이다.
///
/// 왜 `PALETTE` 를 훑어 만들지 않나: 저 표는 **고르는 손버릇** 순으로 늘어놓은 것이라
/// (폼이 먼저, 플래그 줄이 뒤) 등장 순서가 정본과 다르다 — 실제로 훑어 보면
/// `모니터` 가 `설정/기타` 앞에 온다. 탭 차례는 눈이 외우는 것이라 정본을 따른다.
///
/// 맨 앞의 **`전체`** 가상 탭은 이 표에 없다(모든 줄을 모으는 것이라 카테고리가 아니다) —
/// [`palette_matches_in`] 에 `None` 을 주는 것이 그 탭이다.
pub static PALETTE_CATS: &[&str] = &["패널", "탭", "복사/버퍼", "설정/기타", "레이아웃", "모니터", "Claude"];

/// 맨 앞 가상 탭의 이름(파이썬 `cat.전체`). 카테고리가 아니라 **모든 줄**이라
/// [`PALETTE_CATS`] 에는 없다.
pub const PALETTE_CAT_ALL: &str = "전체";

/// 팔레트 탭에 적을 이름(로케일 적용). 파이썬 `cat.<name>` 과 같은 문맥 키다.
pub fn palette_cat_label(cat: &'static str) -> &'static str {
    crate::i18n::tc("cat", cat)
}

/// 팔레트 탭 이름들 — 맨 앞이 `전체`, 그 뒤가 [`PALETTE_CATS`] 다.
///
/// 뷰가 직접 이어 붙이지 않는 이유는 늘 같다: 두 뷰가 각자 이으면 한쪽에만 `전체` 가 있거나
/// 차례가 갈린다. [`palette_tab_counts`] 가 돌려주는 개수와 **자리가 맞는다**.
pub fn palette_tab_labels() -> Vec<&'static str> {
    std::iter::once(crate::i18n::tc("cat", PALETTE_CAT_ALL))
        .chain(PALETTE_CATS.iter().map(|c| palette_cat_label(c)))
        .collect()
}

/// `tab` 번째 탭이 거르는 카테고리(0=`전체` 는 `None`).
pub fn palette_tab_cat(tab: usize) -> Option<&'static str> {
    tab.checked_sub(1).and_then(|i| PALETTE_CATS.get(i).copied())
}

/// 검색 비교용 정규형 — 공백·언더바를 하이픈으로 모으고 소문자로(파이썬 `norm_sep`).
///
/// 사용자가 단어 구분자를 무엇으로 치든 같은 명령에 걸리게 한다: `rename ` · `rename_` ·
/// `rename-` 이 전부 `rename-tab` 에 걸린다. **검색어와 후보에 똑같이** 적용해야 뜻이 있다.
fn norm_sep(s: &str) -> String {
    s.chars()
        .map(|c| if c == ' ' || c == '_' { '-' } else { c.to_ascii_lowercase() })
        .collect()
}

/// `cat` 안에서 필터에 걸리는 항목들의 **원래 번호**. `cat` 이 `None` 이면 `전체` 탭이다.
///
/// 이름만 본다 — **설명까지 보려면** [`palette_matches_with`] 를 쓴다(설명의 주인이
/// proto 라 core 가 직접 못 든다).
pub fn palette_matches_in(cat: Option<&str>, filter: &str) -> Vec<usize> {
    palette_matches_with(cat, filter, |_| None)
}

/// 이름 **또는 설명**으로 거르고 **관련도로 정렬**한다(정본 `_matches` + `_relevance_rank`).
///
/// 등급(작을수록 위): 0 정확 일치 · 1 이름 접두 · 2 단어 접두 · 3 중간 부분일치 ·
/// 4 설명에만. **정렬이 없으면 설명 매칭이 해롭다** — `remote-attach` 를 다 쳤는데 설명에
/// 그 낱말이 든 `nest-auto-attach` 가 표에서 앞이라 먼저 골라진다(실제로 오라클이 그걸
/// 잡았다). 이름을 아는 사람의 길이 막히면 팔레트의 값이 통째로 뒤집힌다.
///
/// # 왜 설명을 인자로 받나
///
/// 설명의 주인은 정본에서 뽑은 픽스처이고 그건 proto 에 있다(`command_help`). core 가
/// 그것을 들면 계층이 뒤집힌다 — 그래서 **규칙은 여기**, **자료는 뷰가** 준다.
///
/// # 왜 설명도 보나
///
/// 이름을 모르는 사람이 팔레트를 여는 일이 실제로 있다("분할이 뭐였더라"). 정본은
/// 그래서 설명도 본다 — 이름만 보면 팔레트가 **아는 사람 전용**이 된다.
pub fn palette_matches_with<'a>(
    cat: Option<&str>,
    filter: &str,
    desc: impl Fn(&'a str) -> Option<&'a str>,
) -> Vec<usize> {
    let raw = filter.trim().to_lowercase();
    let needle = norm_sep(&raw);
    let mut hits: Vec<(u8, usize)> = PALETTE
        .iter()
        .enumerate()
        .filter(|(_, entry)| cat.is_none_or(|c| entry.cat == c))
        .filter_map(|(i, entry)| {
            if raw.is_empty() {
                return Some((0, i));
            }
            let name = norm_sep(entry.name);
            let rank = if name == needle {
                0
            } else if name.starts_with(&needle) {
                1
            } else if name.split('-').any(|w| w.starts_with(&needle)) {
                2
            } else if name.contains(&needle) {
                3
            } else if desc(entry.name).is_some_and(|d| d.to_lowercase().contains(&raw)) {
                // 설명은 사람 말이라 구분자 정규화를 하지 않는다 — `-` 가 뜻을 가진 글이다.
                4
            } else {
                return None;
            };
            Some((rank, i))
        })
        .collect();
    // **안정 정렬**이라 같은 등급 안에서는 표 차례가 그대로다(그 차례는 고르는 손버릇 순).
    hits.sort_by_key(|(rank, _)| *rank);
    hits.into_iter().map(|(_, i)| i).collect()
}

/// 같은 규칙으로 **플러그인이 기여한 명령**을 거른다(설계 Tier A).
///
/// 코어 표와 갈라 두는 이유: 코어 표는 `static` 이라 자리(index)로 가리킬 수 있지만
/// 플러그인 목록은 **서버가 주는 런타임 값**이라 자리를 못 박는다. 그래서 이름·설명·분류를
/// 받아 **걸린 자리**만 돌려주고, 무엇을 그릴지는 호출부가 정한다.
///
/// 등급 규칙은 [`palette_matches_with`] 와 **같은 것을 쓴다** — 두 목록이 다른 규칙으로
/// 걸리면 같은 글자에 코어 명령만 걸리거나 플러그인 명령만 걸리는 일이 생긴다.
///
/// 이름·설명·분류를 **빌려서** 받는다(`'static` 이 아니다) — 서버가 준 값은 런타임
/// `String` 이라 `'static` 을 요구하면 호출부가 이 함수를 못 쓰고 규칙을 다시 적게 된다
/// (실제로 그렇게 갈라져 있었다: 팔레트가 등급 정렬 없는 자기 규칙으로 걸렀다).
pub fn palette_matches_plugin<'a>(
    cat: Option<&str>,
    filter: &str,
    rows: impl Iterator<Item = (&'a str, &'a str, &'a str)>,
) -> Vec<usize> {
    let raw = filter.trim().to_lowercase();
    let needle = norm_sep(&raw);
    let mut hits: Vec<(u8, usize)> = rows
        .enumerate()
        .filter(|(_, (_, _, c))| cat.is_none_or(|want| *c == want))
        .filter_map(|(i, (name, desc, _))| {
            if raw.is_empty() {
                return Some((0, i));
            }
            Some((rank_of(&norm_sep(name), &needle, desc, &raw)?, i))
        })
        .collect();
    hits.sort_by_key(|(rank, _)| *rank);
    hits.into_iter().map(|(_, i)| i).collect()
}

/// 이름·설명 하나를 등급으로. [`palette_matches_with`] 와 플러그인 쪽이 **같은 규칙**을
/// 쓰게 하는 자리다(둘이 갈리면 같은 글자에 한쪽만 걸린다).
fn rank_of(name: &str, needle: &str, desc: &str, raw: &str) -> Option<u8> {
    Some(if name == needle {
        0
    } else if name.starts_with(needle) {
        1
    } else if name.split('-').any(|w| w.starts_with(needle)) {
        2
    } else if name.contains(needle) {
        3
    } else if desc.to_lowercase().contains(raw) {
        // 설명은 사람 말이라 구분자 정규화를 하지 않는다 — `-` 가 뜻을 가진 글이다.
        4
    } else {
        return None;
    })
}

/// 각 탭에 걸린 개수 — `전체` 를 맨 앞에 둔 [`PALETTE_CATS`] 순이다.
///
/// 정본이 탭마다 일치 수를 적는 이유가 이것이다: 친 글자가 **다른 탭**에만 걸릴 때,
/// 개수가 없으면 화면은 그냥 "결과 없음"이고 사용자는 이름을 잘못 안 줄 안다.
pub fn palette_tab_counts(filter: &str) -> Vec<usize> {
    palette_tab_counts_with(filter, |_| None)
}

/// 같은 것을 **설명까지 보는 규칙**으로(뷰가 `command_help` 를 준다).
///
/// 이 짝이 없으면 탭줄이 거짓말을 한다 — 목록은 설명으로 걸러 보여 주는데 개수는 이름만
/// 세면, 사용자는 "(0)" 인 탭에 결과가 있는 것을 본다.
pub fn palette_tab_counts_with<'a>(
    filter: &str,
    desc: impl Fn(&'a str) -> Option<&'a str> + Copy,
) -> Vec<usize> {
    std::iter::once(palette_matches_with(None, filter, desc).len())
        .chain(PALETTE_CATS.iter().map(|c| palette_matches_with(Some(c), filter, desc).len()))
        .collect()
}

/// 지금 탭에 걸린 것이 없으면 **결과가 있는 첫 탭**의 번호(0=`전체`). 정본과 같은 규칙 —
/// 빈 목록을 보여 주는 대신 결과가 있는 곳으로 옮겨 준다.
pub fn palette_tab_with_results(now: usize, filter: &str) -> usize {
    palette_tab_with_results_of(now, filter, |_| None)
}

/// 같은 것을 설명까지 보는 규칙으로.
pub fn palette_tab_with_results_of<'a>(
    now: usize,
    filter: &str,
    desc: impl Fn(&'a str) -> Option<&'a str> + Copy,
) -> usize {
    if filter.trim().is_empty() {
        return now;
    }
    let counts = palette_tab_counts_with(filter, desc);
    if counts.get(now).copied().unwrap_or(0) > 0 {
        return now;
    }
    counts.iter().position(|n| *n > 0).unwrap_or(now)
}

/// 키 도움말 화면에 실을 줄들 — `(키, 하는 일)`.
///
/// # 왜 뷰가 아니라 여기인가
///
/// 표가 여기 있으므로 도움말도 여기서 만든다. 뷰가 각자 조립하면 **한쪽 도움말만 낡는다**
/// — 그리고 그것은 "이 키가 있는데 왜 안 되지"로 나타난다(도움말은 사용자에게 계약처럼
/// 읽힌다).
///
/// 소제목은 `(구분선, "")` 로 끼워 넣는다. 화면 폭·색은 그리는 쪽이 정한다.
///
/// `binds` 는 설정 파일에서 읽은 **사용자 바인딩**이다(`Config::binds`). 인자로 받는
/// 이유는 core 에 IO 가 없다는 계약 때문이고, 여기 싣는 이유는 정본과 같다 — 자기가 건
/// 바인딩을 볼 곳이 없으면 `bind-key` 는 써 놓고 잊는 기능이 된다.
pub fn key_help_lines(binds: &[crate::config::Bind]) -> Vec<(String, String)> {
    use crate::i18n::t;
    let mut out = vec![("── prefix (Ctrl+B) ──".to_owned(), String::new())];
    for b in PREFIX_BINDINGS.iter().filter(|b| b.show_in_help) {
        // `prefix d` 는 같은 액션(Quit)이지만 뜻이 다르다 — esc 모드의 `q` 는 "이 클라를
        // 끝낸다"이고 prefix 의 `d` 는 tmux 의 **detach** 다. 라벨이 액션 하나에 묶여
        // 있으면 화면이 사용자에게 다른 말을 한다.
        let what = if b.key == "d" && b.action == Action::Quit {
            t("detach (이 클라만 빠진다)").to_owned()
        } else {
            b.action.label().to_owned()
        };
        out.push((b.key.to_owned(), what));
    }
    out.push(("0~9".to_owned(), t("번호로 탭").to_owned()));
    out.push(("Ctrl+B".to_owned(), t("패널에 Ctrl+B 보내기").to_owned()));
    out.push((t("── esc 모드 ──").to_owned(), String::new()));
    // ★ **표 전체**를 싣는다(`show_in_help` 로 거르지 않는다). 그 플래그는 블록 데모
    // 뷰의 **한 줄짜리** 도움말용이고, 이 화면은 줄 수 제한이 없다.
    //
    // 전에는 여기서도 거른 뒤 빠진 것을 손으로 덧붙였는데(`Tab`·`:`·`?`), 그 목록은
    // 조용히 낡는다 — G1c 에서 esc 글자 아홉 개를 더했을 때 도움말에 하나도 안 나왔다.
    // `escape` 를 걸러 내던 줄은 사라졌다(pytmux-466) — 그 키는 이제 **데모 판 표**에만
    // 있고 이 표에 없다. 그때의 사정(모드 전용 규칙이 표보다 먼저다)은 그대로다.
    for b in BINDINGS {
        out.push((b.key.to_owned(), b.action.label().to_owned()));
    }
    out.push(("Shift+ESC".to_owned(), t("패널에 ESC (모드 없이)").to_owned()));
    out.push(("1~9".to_owned(), t("번호로 탭").to_owned()));
    out.push(("?".to_owned(), t("이 화면").to_owned()));
    out.push((t("── 스크롤 모드 ──").to_owned(), String::new()));
    out.push(("↑ ↓ PgUp PgDn".to_owned(), t("스크롤백 이동").to_owned()));
    out.push(("q · Esc · Enter".to_owned(), t("라이브로 복귀").to_owned()));
    // ★ 블록 선택 모드(pytmux-18). 이 절이 **없으면 안 되는** 이유는 스크롤 모드와
    //   같다: 모드 안의 키는 표(`BINDINGS`)에 없어서, 여기 안 적으면 화면 어디에도
    //   안 나온다. 그리고 `Ctrl+C` 가 평소와 다른 일을 하는 유일한 자리라 더 그렇다.
    out.push((t("── 블록 선택 모드 ──").to_owned(), String::new()));
    out.push(("↑ ↓".to_owned(), t("한 블록씩 이동").to_owned()));
    out.push(("Ctrl+C".to_owned(), t("고른 블록 전체 복사").to_owned()));
    out.push(("q · Esc · Enter".to_owned(), t("고르기 끝").to_owned()));
    // ── 마우스 제스처 ─────────────────────────────────────────────────────────
    //
    // ★ 정본 `list-keys`(= `mouse-help`)의 절이다. 저쪽 주석이 이 절을 만든 이유를 적어
    //   둔다: **구현된 제스처가 명령에도 메뉴에도 안 떠 사장돼 있었다.** 우리 GUI 는 더
    //   심하다 — 터미널과 달리 제스처를 짐작할 단서가 화면에 없다.
    out.push((t("── 마우스 ──").to_owned(), String::new()));
    for (gesture, what) in MOUSE_GESTURES {
        out.push((t(gesture).to_owned(), t(what).to_owned()));
    }
    // ── 사용자 키 바인딩 ──────────────────────────────────────────────────────
    //
    // 정본과 같은 자리다. 자기가 건 바인딩을 볼 곳이 없으면 `bind-key` 는 **써 놓고 잊는**
    // 기능이 된다(그리고 왜 어떤 키가 안 먹는지도 못 가린다).
    out.push((t("── 사용자 키 바인딩 ──").to_owned(), String::new()));
    if binds.is_empty() {
        out.push((t("(없음)").to_owned(), String::new()));
    } else {
        for bind in binds {
            let scope = if bind.after_prefix { "prefix " } else { "(root) " };
            out.push((format!("{scope}{}", bind.key), bind.command.clone()));
        }
    }
    out
}

/// 마우스 제스처 한 줄들 — 정본 `i18n` 의 `keys.g_*` 와 **같은 일곱**이다.
///
/// 표로 두는 이유: 화면이 둘(GUI·TUI)이고 문구가 갈리면 "한쪽 클라에만 있는 제스처"처럼
/// 보인다. 그리고 제스처가 늘 때 이 표 한 줄이 곧 도움말 한 줄이다.
///
/// ⚠ **평드래그와 Shift+드래그를 한 줄로 묶지 말 것**(2026-08-01 에 풀었다). 종전에는
/// 여섯 줄이었고 그 둘이 한 줄이었는데, 둘은 **서로 다른 일**을 한다 — 평드래그는
/// 선택→복사이고 Shift+드래그는 그 드래그를 패널 안 앱에 넘긴다(p4 65423 에서 뒤바뀐
/// 자리다). 묶어 두면 "Shift 를 왜 쓰나"가 한 줄 꼬리로 밀려 안 읽힌다.
pub static MOUSE_GESTURES: &[(&str, &str)] = &[
    ("휠", "커서 아래 패널 스크롤 · 클릭 — 패널 포커스"),
    ("우클릭", "패널 메뉴(분할·줌·회전·삭제…)"),
    ("경계선 드래그", "패널 크기 조절"),
    ("패널 헤더 드래그", "패널을 들어 다른 패널과 swap · 탭으로 이동 · [+]에 놓아 새 탭"),
    ("드래그", "텍스트 선택 → 클립보드 복사(최소 이동 mouse-drag-threshold)"),
    ("Shift+드래그", "패널 안 앱에 마우스 전달(에디터 패널 스플리터 등)"),
    ("탭 드래그", "탭 재정렬 · 패널 위로 끌어 분할"),
];

/// 도움말 줄. 두 뷰가 같은 문자열을 쓴다.
///
/// **데모 판의 것**이다(pytmux-466) — 그 창의 바닥 한 줄이고, 세션 뷰의 esc 키는
/// 여기 안 온다(그쪽은 줄 수 제한이 없는 [`key_help_lines`] 가 전부 싣는다).
pub fn help_line() -> String {
    BLOCK_BINDINGS
        .iter()
        .filter(|b| b.show_in_help)
        .map(|b| format!("{} {}", b.key, b.action.label()))
        .collect::<Vec<_>>()
        .join(" · ")
}

/// 액션 하나마다 다른 번호 — **와일드카드 팔이 없어** 액션을 추가하면 여기서 컴파일이
/// 막힌다. 손으로 적은 목록은 조용히 낡는다(실제로 `ToggleClaudeDetail` 이 전수 배열에
/// 안 들어가 검사를 한 번도 안 받고 있었다).
///
/// ★ 2026-08-01 에 테스트 모듈 밖으로 냈다: **다른 크레이트의 오라클도 이 목록이 필요하다**
/// (뷰가 각 액션에 실제로 무엇을 하는가 — `gui` 쪽 G1 측정). `#[cfg(test)]` 안에 있으면
/// 그쪽에서 못 보고, 못 보면 각자 목록을 다시 적는다(`command.rs` 의 `variant_index` 와
/// 같은 방법이다).
fn variant_index(action: Action) -> usize {
    match action {
        Action::SelectNext => 0,
        Action::SelectPrev => 1,
        Action::SelectFirst => 2,
        Action::SelectLast => 3,
        Action::ToggleExpand => 4,
        Action::ToggleClaudeDetail => 5,
        Action::EnterScroll => 6,
        Action::Quit => 7,
        Action::SplitLeftRight => 8,
        Action::SplitTopBottom => 9,
        Action::KillPane => 10,
        Action::NewTab => 11,
        Action::NewClaudeTab => 114,
        Action::KillTab => 12,
        Action::NextTab => 13,
        Action::PrevTab => 14,
        Action::LastTab => 15,
        Action::SelectTab(_) => 16,
        Action::Redraw => 17,
        Action::Zoom => 18,
        Action::NextPane => 19,
        Action::LastPane => 20,
        Action::CycleLayout => 21,
        Action::RotatePanes => 22,
        Action::SwapPane { .. } => 23,
        Action::BreakPane => 24,
        Action::SelectPane(_) => 25,
        Action::ResizePane(_) => 26,
        Action::TogglePin => 27,
        Action::PasteBuffer => 28,
        Action::PasteClipboard => 116,
        Action::ShowKeys => 29,
        Action::ShowTabs => 30,
        Action::ShowTree => 31,
        Action::ShowBuffers => 32,
        Action::RenameTab => 33,
        Action::MoveTab => 34,
        Action::ShowCommands => 35,
        Action::ToggleSync => 36,
        Action::ToggleMonitorActivity => 37,
        Action::ToggleMonitorBell => 38,
        Action::ToggleAutoRename => 39,
        Action::ToggleBorderStatus => 54,
        Action::ToggleInactiveDim => 55,
        Action::ToggleServerOption(_) => 57,
        Action::ClearHistory => 58,
        Action::MoveTabBy(_) => 60,
        Action::MoveTabAt { .. } => 86,
        Action::SwapTab => 61,
        Action::SetPinned(_) => 62,
        Action::SetEnum(..) => 68,
        Action::CapturePane(_) => 69,
        Action::PipePane => 70,
        Action::JoinPane => 71,
        Action::RequestVersion => 72,
        Action::RequestRestartCheck => 80,
        Action::MergeRemoteTab => 81,
        Action::DisplayPopup => 84,
        Action::PopupClose => 85,
        Action::RunShell => 82,
        Action::IfShell => 83,
        Action::DisplayMessage => 73,
        Action::SourceFile => 74,
        Action::SetOption => 75,
        Action::ShowOptions => 76,
        Action::SetHook => 87,
        Action::ShowCommandOptions(_) => 89,
        Action::SetSync(_) => 90,
        Action::SetMonitor { .. } => 91,
        Action::SetAutoRename(_) => 92,
        Action::SetServerOption(..) => 93,
        Action::ShowHooks => 88,
        Action::SendKeys => 77,
        Action::BindKey => 78,
        Action::UnbindKey => 79,
        Action::ShowLayouts => 63,
        Action::SaveTabLayout => 64,
        Action::LoadTabLayout(_) => 65,
        Action::SaveLayout => 66,
        Action::RestoreLayout => 67,
        Action::RespawnPane => 59,
        Action::ShowSettings => 40,
        Action::KillServer => 41,
        Action::RestartServer => 42,
        Action::RemoteAttach => 43,
        Action::RemoteNewTab => 44,
        Action::RemoteDetach => 45,
        Action::ShowPlugins => 46,
        Action::RenamePane => 51,
        Action::ShowPaneNumbers => 52,
        Action::ShowMenu => 53,
        Action::ShowNotices => 56,
        Action::ShowSummary => 108,
        Action::SelectBlocks => 113,
        Action::SendEscape => 49,
        Action::SendBacktick => 50,
        Action::ToggleClock => 47,
        Action::ToggleCalendar => 48,
        Action::SetOverlay { .. } => 109,
        Action::PluginToggle { .. } => 110,
        Action::PluginDo { .. } => 111,
        Action::ToggleUsageView => 105,
        Action::JumpPrompt { .. } => 94,
        Action::ShowCompose => 95,
        Action::ShowInfoTabs => 96,
        Action::ToggleAutoresume => 97,
        Action::ShowAutoresume => 117,
        // ⚠ 104 는 종전 `ToggleScroll` 의 자리다(pytmux-377 로 그 액션을 걷었다).
        // 이 수는 **오라클 비트맵의 자리**일 뿐이라 뜻이 없다 — 다만 0..ACTION_COUNT 가
        // 빈틈없이 차야 `all_actions()` 의 전수 검사가 성립하므로, 구멍을 남기는 대신
        // 맨 끝 것을 그리로 옮기고 [`ACTION_COUNT`] 를 하나 줄였다.
        Action::ShowCursor => 104,
        Action::ShowDebugStats => 118,
        Action::Reconnect => 98,
        Action::RestartAll => 99,
        Action::SetLang(_) => 100,
        Action::TogglePromptClear => 101,
        Action::SearchScrollback => 102,
        Action::SearchAgain { .. } => 103,
        Action::FontScale { .. } => 106,
        Action::FontScaleReset => 107,
        Action::ToggleFullscreen => 112,
        Action::SearchAll => 115,
    }
}

const ACTION_COUNT: usize = 119;

/// **전수 목록** — 액션 하나도 빠지지 않는다(위 `variant_index` 의 와일드카드 없는 match 가
/// 빠짐을 막고, 아래 개수 단언이 중복·누락을 막는다).
///
/// 오라클용이다: "이 액션을 부를 길이 있나"(이 크레이트) · "뷰가 이 액션에 실제로 무엇을
/// 하나"(`gui`). 목록을 크레이트마다 다시 적으면 그 목록들이 서로 다르게 낡는다.
pub fn all_actions() -> Vec<Action> {
    let all = vec![
        Action::SelectNext,
        Action::SelectPrev,
        Action::SelectFirst,
        Action::SelectLast,
        Action::ToggleExpand,
        Action::ToggleClaudeDetail,
        Action::EnterScroll,
        Action::Quit,
        Action::SplitLeftRight,
        Action::SplitTopBottom,
        Action::KillPane,
        Action::NewTab,
        Action::NewClaudeTab,
        Action::KillTab,
        Action::NextTab,
        Action::PrevTab,
        Action::LastTab,
        Action::SelectTab(3),
        Action::Redraw,
        Action::Zoom,
        Action::NextPane,
        Action::LastPane,
        Action::CycleLayout,
        Action::RotatePanes,
        Action::SwapPane { forward: true },
        Action::BreakPane,
        Action::SelectPane(Dir::Left),
        Action::ResizePane(Dir::Left),
        Action::TogglePin,
        Action::PasteBuffer,
        Action::PasteClipboard,
        Action::ShowKeys,
        Action::ShowTabs,
        Action::ShowTree,
        Action::ShowBuffers,
        Action::RenameTab,
        Action::MoveTab,
        Action::ShowCommands,
        Action::ShowSettings,
        Action::KillServer,
        Action::RestartServer,
        Action::RemoteAttach,
        Action::RemoteNewTab,
        Action::RemoteDetach,
        Action::ShowPlugins,
        Action::RenamePane,
        Action::ShowPaneNumbers,
        Action::ShowMenu,
        Action::ShowNotices,
        Action::ShowSummary,
        Action::SelectBlocks,
        Action::SendEscape,
        Action::SendBacktick,
        Action::ToggleClock,
        Action::ToggleCalendar,
        Action::SetOverlay { name: "clock", on: true },
        Action::PluginToggle { action: "set_claude_auto_retry" },
        Action::PluginDo { action: "refresh_usage" },
        Action::ToggleUsageView,
        Action::ToggleSync,
        Action::ToggleMonitorActivity,
        Action::ToggleMonitorBell,
        Action::ToggleAutoRename,
        Action::ToggleBorderStatus,
        Action::ToggleInactiveDim,
        Action::ToggleServerOption(ServerOpt::SingleBorder),
        Action::ClearHistory,
        Action::MoveTabBy(TabMove::Left),
        Action::MoveTabAt { from: 0, to: 1 },
        Action::SwapTab,
        Action::SetPinned(true),
        Action::SetEnum(EnumOpt::VtParser, "pyte"),
        Action::CapturePane(false),
        Action::PipePane,
        Action::JoinPane,
        Action::RequestVersion,
        Action::RequestRestartCheck,
        Action::MergeRemoteTab,
        Action::DisplayPopup,
        Action::PopupClose,
        Action::RunShell,
        Action::IfShell,
        Action::DisplayMessage,
        Action::SourceFile,
        Action::SetOption,
        Action::ShowOptions,
        Action::SetHook,
        Action::ShowHooks,
        Action::ShowCommandOptions("split-window"),
        Action::SetSync(true),
        Action::SetMonitor { bell: false, on: true },
        Action::SetAutoRename(true),
        Action::SetServerOption(ServerOpt::SingleBorder, true),
        Action::SendKeys,
        Action::BindKey,
        Action::UnbindKey,
        Action::ShowLayouts,
        Action::SaveTabLayout,
        Action::LoadTabLayout(false),
        Action::SaveLayout,
        Action::RestoreLayout,
        Action::RespawnPane,
        Action::JumpPrompt { up: true },
        Action::ShowCompose,
        Action::ShowInfoTabs,
        Action::ToggleAutoresume,
        Action::ShowAutoresume,
        Action::ShowCursor,
        Action::ShowDebugStats,
        Action::Reconnect,
        Action::ToggleFullscreen,
        Action::RestartAll,
        Action::SetLang("ko"),
        Action::TogglePromptClear,
        Action::SearchScrollback,
        Action::SearchAll,
        Action::SearchAgain { down: false },
        Action::FontScale { up: true },
        Action::FontScaleReset,
    ];
    let mut seen = vec![false; ACTION_COUNT];
    for action in &all {
        let i = variant_index(*action);
        assert!(!seen[i], "{action:?} 가 목록에 두 번 있다");
        seen[i] = true;
    }
    assert!(
        seen.iter().all(|&x| x),
        "all_actions() 에 빠진 액션이 있다 — 빠진 것은 아래 검사를 안 받는다"
    );
    all
}

#[cfg(test)]
mod tests {
    use super::*;


    #[test]
    fn every_action_can_be_reached_somehow() {
        // 액션을 만들어 놓고 **부를 길을 안 만드는** 실수를 잡는다. 입구는 셋이다:
        // 키 표 둘(esc·prefix)과 명령 팔레트. 셋 중 하나에도 없으면 그 액션은 죽은 코드다.
        //
        // 팔레트를 입구로 인정하는 이유(G6): `synchronize-panes` 같은 것은 파이썬에도
        // 키가 없고 **명령으로만** 부른다 — 억지로 키를 붙이면 파이썬과 손버릇이 갈린다.
        for action in all_actions() {
            if matches!(action, Action::SelectTab(_)) {
                assert!(prefix_number('3').is_some(), "번호 규칙이 사라졌다");
                continue;
            }
            // 자리 둘을 알아야 뜻이 생기는 액션이라 키 표에도 팔레트에도 실을 수 없다.
            // 그래도 **부를 길이 없는 것은 아니다** — 탭바 포커스가 만든다. 넘기기만 하면
            // 이 액션은 검사 밖으로 새므로, 그 입구가 살아 있는지를 여기서 직접 본다.
            if matches!(action, Action::MoveTabAt { .. }) {
                let tabs = [0usize, 1];
                let ctx = crate::chrome::ChromeCtx {
                    pane_above: false,
                    pane_below: false,
                    tabs: &tabs,
                    active: 0,
                    badges: &[],
                };
                let mut chrome = crate::chrome::Chrome::default();
                chrome.press(&ctx, crate::keys::Key::Up, crate::keys::Mods::NONE);
                assert!(
                    matches!(
                        chrome.press(&ctx, crate::keys::Key::ShiftRight, crate::keys::Mods::NONE),
                        Some(crate::chrome::ChromeKey::Stay(Action::MoveTabAt { .. }))
                    ),
                    "탭바 포커스가 MoveTabAt 를 만드는 유일한 입구인데 그 길이 끊겼다"
                );
                continue;
            }
            // ★ **변형**으로 견준다(값이 아니라). `SetEnum(VtParser, …)` 처럼 값을
            // 들고 다니는 액션은 팔레트에 실린 값과 전수 목록의 값이 다를 수 있는데,
            // 그때 "부를 길이 없다"는 것은 거짓이다 — 입구는 그 변형이다.
            let same = |other: Action| variant_index(other) == variant_index(action);
            // 검색 둘의 입구는 표 밖이다: 메뉴의 search 줄과 스크롤 모드의 `/`·`n`·`N`
            // (파이썬 `_handle_scroll_key` 동형 하드코딩 — `SCROLL_BINDINGS` 는
            // `ScrollAmount` 전용이라 액션을 못 싣는다). 길이 실재하는지 **실제로
            // 눌러** 확인한다 — 배선이 끊기면 여기서 걸린다.
            let scroll_hits = |key: char| {
                matches!(
                    crate::keys::interpret(
                        crate::InputMode::Scroll,
                        crate::keys::Key::Char(key),
                        crate::keys::Mods::NONE,
                    ),
                    crate::keys::KeyOutcome::Action(made) if same(made)
                )
            };
            if same(Action::SearchScrollback) {
                assert!(scroll_hits('/'), "스크롤 모드 `/` 가 검색 물음을 안 연다");
                assert!(
                    MENU.iter().any(|e| same(e.action)),
                    "메뉴에 search 줄이 없다(파이썬 31줄)"
                );
                continue;
            }
            if same(Action::SearchAgain { down: false }) {
                assert!(
                    scroll_hits('n') && scroll_hits('N'),
                    "스크롤 모드 `n`/`N` 반복이 끊겼다"
                );
                continue;
            }
            // 좌하단 `[자동재개]` 표식을 **누르는 것**이 이 액션의 유일한 입구다
            // (pytmux-183). 정본도 그렇다 — `open_autoresume_info` 는 `_ar_zone` 클릭
            // 하나로만 열리고 명령 이름이 없다. ⛔ 그래서 팔레트에 이름을 지어 넣지
            // 않는다: 정본에 없는 조작 표면을 GUI 가 먼저 만드는 일이 된다.
            //
            // 넘기기만 하면 이 액션이 검사 밖으로 새므로, `MoveTabAt` 때와 같이
            // **그 입구가 살아 있는지를 여기서 직접 본다**(클릭 표를 실제로 돌린다).
            if same(Action::ShowAutoresume) {
                assert_eq!(
                    crate::chrome::SysBadge::AutoResume.action(),
                    Some(Action::ShowAutoresume),
                    "자동재개 표식의 클릭 입구가 끊겼다"
                );
                continue;
            }
            let by_key = BINDINGS
                .iter()
                .chain(BLOCK_BINDINGS)
                .chain(PREFIX_BINDINGS)
                .any(|b| same(b.action));
            let by_palette = PALETTE.iter().any(|e| same(e.action));
            // 인자 폼(`crate::options`)도 **입구다.** 켜기/끄기처럼 값을 정하는 액션은
            // 키에도 팔레트에도 없고 그 화면에서만 생긴다 — 그 길을 안 세면 "부를 길이
            // 없다"가 거짓이 된다. 세는 방법은 `pick` 을 **실제로 돌려 보는 것**이라
            // 표만 있고 배선이 끊기면 여기서 걸린다.
            let by_options = crate::options::COMMAND_OPTIONS.iter().any(|options| {
                (0..options.specs[0].choices.len()).any(|i| {
                    matches!(
                        crate::options::pick(options, &[i]),
                        Some(crate::options::OptionPick::Act(made)) if same(made)
                    )
                })
            });
            assert!(
                by_key || by_palette || by_options,
                "{action:?} 를 부를 길이 없다"
            );
        }
    }

    #[test]
    fn the_prefix_table_uses_the_same_letters_as_the_python_client() {
        // ★ 패리티의 기준은 **손버릇**이다. 파이썬 클라(그리고 tmux)의 글자와 다르면
        // 두 클라를 오갈 때마다 손이 어긋난다. 여기 적힌 글자는
        // `clientutil.PREFIX_KEYS` 에서 온 것이고, 표 전체의 진행률은
        // `proto/tests/parity.rs` 가 센다.
        for (key, action) in [
            ("%", Action::SplitLeftRight),
            ("\"", Action::SplitTopBottom),
            ("x", Action::KillPane),
            ("c", Action::NewTab),
            ("&", Action::KillTab),
            ("n", Action::NextTab),
            ("p", Action::PrevTab),
            ("l", Action::LastTab),
            ("r", Action::Redraw),
        ] {
            let found = PREFIX_BINDINGS.iter().find(|b| b.key == key);
            assert_eq!(
                found.map(|b| b.action),
                Some(action),
                "prefix {key} 가 파이썬 클라와 다른 일을 한다"
            );
        }
    }

    #[test]
    fn the_help_screen_lists_the_key_that_opens_it() {
        // 그 화면을 여는 키가 그 화면에 안 보이면 사용자는 **닫는 법도 여는 법도** 모른다.
        let lines = key_help_lines(&[]);
        assert!(
            lines.iter().any(|(k, _)| k == "?"),
            "도움말 화면에 `?` 가 없다"
        );
    }

    #[test]
    fn the_help_screen_lists_both_key_tables() {
        // 한쪽 표만 실으면 사용자는 나머지 절반의 키가 없는 줄 안다.
        //
        // 철자는 **표의 것**이다(`tab`, 대문자 `Tab` 아님) — 손으로 덧붙이던 줄을 없애고
        // 표를 그대로 싣게 바꾼 뒤로, 여기 적힌 철자가 곧 표의 철자다.
        // ⚠ `j` 는 뺐다(pytmux-466): 그 키는 **데모 판 표**로 갔고 이 화면은 세션 뷰의
        //    것이라, 여기 있으면 화면이 없는 키를 광고하게 된다.
        let lines = key_help_lines(&[]);
        for key in ["%", "c", "v", "[", "tab", "?", "left", "n", "p", "e"] {
            assert!(lines.iter().any(|(k, _)| k == key), "도움말에 {key} 가 없다");
        }
    }

    /// 마우스 제스처가 도움말에 **전부** 실리나(레이아웃 맞추기 ⑬).
    ///
    /// 정본이 이 절을 만든 이유가 그대로 우리에게도 있다: 구현된 제스처가 명령에도 메뉴에도
    /// 안 떠 사장돼 있었다. GUI 는 더 심하다 — 터미널과 달리 제스처를 짐작할 단서가 없다.
    #[test]
    fn the_help_screen_lists_every_mouse_gesture() {
        let lines = key_help_lines(&[]);
        assert!(!MOUSE_GESTURES.is_empty(), "제스처 표가 비었다 — 통과가 아니라 고장이다");
        for (gesture, what) in MOUSE_GESTURES {
            assert!(
                lines.iter().any(|(k, v)| k == gesture && v == what),
                "도움말에 제스처 '{gesture}' 가 없다"
            );
        }
    }

    /// 사용자 바인딩이 **자기 자리에** 실리나 — 없으면 `(없음)`.
    ///
    /// 자기가 건 바인딩을 볼 곳이 없으면 `bind-key` 는 써 놓고 잊는 기능이 되고, 어떤 키가
    /// 왜 안 먹는지도 못 가린다(정본이 이 절을 둔 이유).
    #[test]
    fn the_help_screen_lists_the_user_bindings() {
        use crate::config::Bind;
        let empty = key_help_lines(&[]);
        assert!(empty.iter().any(|(k, _)| k == "(없음)"), "빈 목록 표시가 없다");

        let binds = vec![
            Bind { after_prefix: true, key: "r".into(), command: "source-file".into() },
            Bind { after_prefix: false, key: "f5".into(), command: "redraw".into() },
        ];
        let lines = key_help_lines(&binds);
        assert!(
            lines.iter().any(|(k, v)| k == "prefix r" && v == "source-file"),
            "prefix 바인딩이 안 실렸다"
        );
        assert!(
            lines.iter().any(|(k, v)| k == "(root) f5" && v == "redraw"),
            "root 바인딩이 안 실렸다"
        );
        assert!(!lines.iter().any(|(k, _)| k == "(없음)"), "바인딩이 있는데 (없음)이 남았다");
    }

    #[test]
    fn no_prefix_key_is_bound_twice() {
        let mut keys: Vec<_> = PREFIX_BINDINGS.iter().map(|b| b.key).collect();
        keys.sort_unstable();
        let before = keys.len();
        keys.dedup();
        assert_eq!(before, keys.len(), "prefix 표에 중복된 키가 있다");
    }

    #[test]
    fn no_key_is_bound_twice() {
        // 같은 키가 두 액션에 걸리면 어느 쪽이 이길지는 뷰마다 다를 수 있다.
        for (label, table) in [("esc", BINDINGS), ("block", BLOCK_BINDINGS)] {
            let mut keys: Vec<_> = table.iter().map(|b| b.key).collect();
            keys.sort_unstable();
            let before = keys.len();
            keys.dedup();
            assert_eq!(before, keys.len(), "{label} 표에 중복된 키 바인딩이 있다");
        }
    }

    #[test]
    fn the_two_tables_do_not_overlap() {
        // ⛔ 갈랐던 이유가 여기 걸린다(pytmux-466): 한 키가 두 표에 있으면 「어느 표를
        //    보나」가 화면마다 달라지고, 그러면 데모 판의 키가 다시 esc 모드로 샌다.
        let shared: Vec<&str> = BLOCK_BINDINGS
            .iter()
            .map(|b| b.key)
            .filter(|key| BINDINGS.iter().any(|b| b.key == *key))
            .collect();
        assert!(
            shared.is_empty(),
            "데모 판과 esc 모드가 같은 키를 든다: {shared:?} — 한쪽에서 지울 것"
        );
    }

    #[test]
    fn each_action_shows_at_most_one_help_key() {
        for table in [BINDINGS, BLOCK_BINDINGS] {
            for binding in table {
                let shown = table
                    .iter()
                    .filter(|b| b.action == binding.action && b.show_in_help)
                    .count();
                assert!(
                    shown <= 1,
                    "{:?} 가 도움말에 {shown}번 나온다",
                    binding.action
                );
            }
        }
    }

    #[test]
    fn help_line_lists_the_primary_keys() {
        let help = help_line();
        assert!(help.contains("j 다음"), "실제: {help}");
        assert!(help.contains("q 종료"), "실제: {help}");
        assert!(!help.contains("down"), "보조 키는 도움말에 안 나온다: {help}");
    }

    // ── 화면에서 감춘 표면(§10-21ⓜ) ─────────────────────────────────────────────

    #[test]
    fn a_hidden_surface_is_absent_from_every_screen_table() {
        // ★ 감추는 자리가 여럿이라(팔레트·설정 줄·설정 라벨·상태줄 표식) **하나만 빠뜨려도**
        //   그 입구로 다시 보인다. 목록과 표가 갈리지 않게 기계로 묶는다.
        use crate::config::{SETTINGS, SETTING_LABELS};
        for name in HIDDEN_SURFACES {
            assert!(
                !PALETTE.iter().any(|e| e.name.split(' ').next() == Some(*name)),
                "감춘 이름이 팔레트에 남아 있다: {name}"
            );
            assert!(
                !SETTINGS.iter().any(|s| s.key == *name),
                "감춘 이름이 설정 표에 남아 있다: {name}"
            );
            assert!(
                !SETTING_LABELS.iter().any(|(k, _)| k == name),
                "감춘 이름의 설정 라벨이 남아 있다: {name}"
            );
        }
    }

    #[test]
    fn the_hidden_list_is_not_empty_and_names_the_reported_one() {
        // 빈 목록이면 위 단언이 **아무것도 안 재고** 통과한다(공허 방지).
        assert!(HIDDEN_SURFACES.contains(&"monitor-bell"), "제보가 지목한 이름이 없다");
    }

    #[test]
    fn hiding_does_not_remove_the_ability() {
        // "지우는 것이 아니라 감추는 것"이 제보의 말이다 — 액션은 그대로 있어야 서버가 켜
        // 두었을 때 우리도 그 상태를 나른다.
        assert_eq!(Action::ToggleMonitorBell.label(), "벨 감시");
        assert!(
            all_actions().contains(&Action::ToggleMonitorBell),
            "감췄다고 액션까지 사라지면 그건 지운 것이다"
        );
    }
}
