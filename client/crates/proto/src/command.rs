//! 서버로 보내는 명령.
//!
//! # 새 명령을 만들지 않는다
//!
//! 서버에는 이미 명령 테이블이 있다(`pytmuxlib/servercmd.py` 의 `_CMD_TABLE`). 이 클라는
//! **그 이름을 그대로 쓴다**. 새 이름을 만들면 서버를 고쳐야 하고, 그러면 파이썬 클라와
//! 네이티브 클라가 서로 다른 명령 집합을 갖게 된다.
//!
//! 와이어 모양은 `{"t":"cmd","action":"<이름>", ...인자}` 다.
//!
//! 이름이 어긋나도 **아무 소리가 안 난다** — 서버는 표에 없는 action 을 플러그인 훅으로
//! 넘기고 아무도 안 집으면 조용히 끝낸다. 그래서 이름은 서버에서 뽑은 표와 대조한다:
//! `tests/command_conformance.rs`(픽스처 = `scripts/gen_command_fixture.py`).
//!
//! # 응답
//!
//! 대부분의 명령은 서버 테이블에 `FULL` 로 선언돼 있어 **요청한 클라에게 전체 재동기**
//! (layout + screen + status)가 온다. 즉 명령을 보낸 뒤 화면이 알아서 갱신되므로,
//! 클라가 낙관적으로 로컬 상태를 고칠 필요가 없다 — 서버가 권위다.
//!
//! **예외 둘**(`kill_window`=HANDLED · `kill_pane`=DYNAMIC)은 트리 콜백 broadcast 로
//! 갱신된다. 이 "대부분"이 어디까지인지도 같은 적합성 테스트가 표에서 확인한다 —
//! 서버가 조용히 `FULL` 을 뺏으면 증상은 "명령은 먹었는데 화면이 안 바뀐다"이고,
//! 이름 대조만으로는 안 잡힌다.

use serde::Serialize;

/// 서버에 보낼 명령.
///
/// 각 변형이 `servercmd.py` 의 `@_cmd("...")` 하나에 대응한다.
#[derive(Debug, Clone, PartialEq)]
pub enum Command {
    /// 전역 index 의 탭으로 전환. 원격 탭도 같은 index 공간이라 그대로 넘긴다.
    ///
    /// `wid` 는 그 탭의 **안정 id**다(있으면 싣는다 — 정본 p4 68765 와 같은 계약).
    /// index 는 **위치값**이라 다른 클라가 탭을 만들거나 지우면 서버의 `_reindex` 로
    /// 재할당된다. 내가 번호→index 를 계산한 시점과 서버가 이 명령을 처리하는 시점
    /// 사이에 그 일이 나면 옛 index 는 **다른 탭**을 가리킨다(정본 제보: ESC+6 을
    /// 눌렀는데 간헐적으로 7번 탭이 열린다). `wid` 가 실려 있으면 서버가 같은 탭을
    /// 다시 찾아 그 자리의 **현재** index 를 쓰고, 못 찾으면(그 사이 닫혔으면)
    /// index 로 폴백한다 — 그래서 구서버와도 그대로 맞물린다(모르는 키는 무시).
    SelectWindow { index: usize, wid: Option<i64> },
    NextWindow,
    PrevWindow,
    /// 직전 탭으로 되돌아간다.
    LastWindow,
    /// 새 탭. `path` 는 서버가 해석한다(`current`/`home`/절대경로).
    ///
    /// `cmd` 가 있으면 그 탭에서 **그 명령을 먼저 실행**한다(`esc c` = Claude Code 탭 ·
    /// pytmux-137). 없으면 종전대로 셸만 뜬다 — 서버는 `cmd` 를 모르는 구판에서도
    /// 이 칸을 그냥 무시하므로 프레임 모양은 앞뒤로 호환된다.
    ///
    /// ⛔ 여기에 실을 값을 `action_to_command` 가 **정하지 못한다**(그 함수는 설정을
    /// 모른다 — `path` 와 같은 사정이다). 기본값을 싣고 뷰가 설정값으로 갈아 끼운다.
    NewWindow { path: String, cmd: Option<String> },
    /// 현재 탭을 닫는다.
    KillWindow,
    /// 현재 탭을 index 위치로 옮긴다.
    /// 지금 탭을 **방향으로** 옮긴다(`move_current_tab` — `left`/`right`/`first`/`last`).
    ///
    /// ★ 자리를 세지 않는다. 서버가 `sess.active_index` 를 기준으로 계산하고 **핀 구역
    /// 안으로 클램프**한다(`servertree.move_current_tab`). 종전에는 우리가 자리를 세서
    /// `index` 로 보냈는데, 서버는 그 칸을 **안 읽는다** — 그래서 이 명령은 한 번도 안
    /// 먹었다(2026-07-29, 칸 이름 게이트가 잡았다).
    MoveCurrentTab { direction: &'static str },
    /// 지금 탭을 **그 자리로** 옮긴다(`move_window` — `prefix .` 의 숫자 대답).
    MoveWindow { index: usize },
    RenameWindow { name: String },
    /// 세션 이름 바꾸기 — 상태줄 `#S` 자리의 **제자리 편집**이 커밋할 때 보낸다(pytmux-3).
    ///
    /// 서버에 이미 있던 명령이다(`servercmd._cmd_rename_session` · disposition `FULL`) —
    /// 새 표면을 붙이면서 서버를 건드릴 일이 없었다.
    RenameSession { name: String },
    /// 패널 분할. `horizontal` 은 **pytmux 기준**이다(§5 — tmux 와 반대).
    Split { horizontal: bool, path: String },
    SelectPaneId { id: i64 },
    KillPane,
    /// 분할 하나의 비율을 바꾼다(경계선 드래그).
    ///
    /// 방향을 안 싣는 이유: 어느 쪽으로 얼마나가 아니라 **어디에 놓였는가**를 보낸다.
    /// 서버가 배치의 권위라 비율 하나면 충분하고, 상대 이동으로 보내면 프레임이 하나
    /// 유실될 때마다 경계가 마우스에서 조금씩 어긋난다.
    ResizeSplit { split_id: i64, ratio: f64 },
    /// 활성 패널에 텍스트를 붙여넣는다.
    ///
    /// # 왜 `input` 프레임이 아닌가
    ///
    /// 클라가 `input` 으로 원문을 보내면 셸은 그것을 **타이핑으로** 받는다 — 여러 줄
    /// 붙여넣기가 줄마다 실행된다. 그걸 막는 것이 bracketed paste(`ESC[200~ … ESC[201~`)
    /// 인데, **감싸도 되는지는 클라가 알 수 없다**: 그 모드를 켜는 것은 패널 안에서 도는
    /// 프로그램(DECSET 2004)이고, 그 상태를 아는 것은 PTY 출력을 파싱하는 서버뿐이다
    /// (`model.Pane.bracketed`). 안 켠 프로그램에 마커를 보내면 마커가 **글자로 찍힌다**.
    ///
    /// 그래서 원문을 그대로 보내고 서버가 판정한다(`server._write_paste`). 덤으로 서버
    /// 쪽 부수효과도 같이 따라온다 — 붙여넣은 프롬프트가 Claude 헤더 추적에 잡히고
    /// (`server_paste` 훅), 스크롤 중이었다면 live 로 복귀한다.
    Paste { text: String },
    /// 드래그로 고른 범위의 텍스트를 **서버에게 뽑아 달라고** 한다. 회신은
    /// [`ServerMessage::Selection`](crate::message::ServerMessage::Selection) 이다.
    ///
    /// # 왜 클라가 직접 안 뽑나
    ///
    /// 클라가 가진 것은 **지금 뷰포트**뿐이다(`screen` 은 보이는 줄만 온다). 드래그 중에
    /// 휠을 굴리거나 출력이 흘러가면 선택이 한 화면을 넘고, 그러면 화면 밖 줄은 클라
    /// 안에 아예 없다. 스크롤백을 가진 쪽은 서버다(`model.Pane.extract_range`).
    ///
    /// 좌표는 **절대 행 인덱스**다 — 뷰포트 기준으로 보내면, 요청이 날아가는 사이에
    /// 패널이 한 줄이라도 스크롤되면 서버가 다른 줄을 뽑는다. 절대 인덱스의 기준점은
    /// 서버가 `screen` 에 실어 보낸 [`Screen::top`](crate::message::Screen::top) 이다.
    /// 열은 **패널 안 열**이고, 범위는 `(y0,x0)..(y1,x1)` **포함**이다.
    CopyRange {
        pane: i64,
        y0: usize,
        x0: u16,
        y1: usize,
        x1: u16,
    },
    /// 서버의 페이스트 버퍼 맨 앞에 넣는다(tmux 의 paste-buffer 와 같은 자리).
    ///
    /// OS 클립보드와 **둘 다** 채우는 이유: OS 클립보드는 외부 도구가 없으면 실패하고
    /// (디스플레이 없는 ssh 세션이 그렇다), 그때도 pytmux 안에서의 붙여넣기는 되어야
    /// 한다. 파이썬 클라의 `copy_text` 도 같은 두 곳에 넣는다.
    SetBuffer { text: String },
    /// "이 화면을 통째로 다시 보내 달라."
    ///
    /// 기준(직전 full `screen`) 없이 `screen-delta` 만 온 패널을 되살리는 유일한 길이다 —
    /// 바탕이 없으면 바뀐 행을 얹을 데가 없어 그 패널이 빈 채로 굳는다. 원격 탭이면
    /// 서버가 상류로 릴레이해 그쪽 full 을 끌어온다(파이썬 클라와 같은 처방).
    RequestRedraw,
    // ── 패리티 G1b: 서버에 이미 있던 조작들 ─────────────────────────────────
    /// 활성 패널을 창 전체로 키웠다 줄인다.
    Zoom,
    /// 다음 패널로.
    CyclePane,
    /// 직전에 보던 패널로.
    LastPane,
    /// 미리 정해진 배치를 순환한다.
    CycleLayout,
    /// 패널들을 한 칸씩 돌린다.
    Rotate { forward: bool },
    /// 활성 패널을 이웃과 맞바꾼다.
    SwapPane { forward: bool },
    /// 활성 패널을 새 탭으로 떼어낸다.
    BreakPane,
    /// 방향으로 패널을 고른다.
    SelectPaneDir { dir: Dir },
    /// 방향으로 패널 경계를 민다. `cells` 는 한 번에 미는 칸 수다.
    ResizeDir { dir: Dir, cells: u16 },
    /// 탭 고정(핀)을 켜고 끈다.
    ///
    /// # 왜 자리를 실어야 하나 (§10-21ⓒ3 — 제보 "원격 탭이 핀이 안 된다")
    ///
    /// 서버는 자리를 안 실으면 `sess.active_index` 를 쓰는데, 그것은 **로컬 탭의
    /// 자리**다. 원격(병합) 탭이 활성일 때 그 값은 보고 있는 탭이 아니라서, 토글이
    /// **엉뚱한 로컬 탭**에 걸린다 — 사용자에게는 "원격 탭은 핀이 안 된다"로 보인다.
    ///
    /// 정본이 그 함정을 주석으로 남겨 뒀다(`clientcmd.py`): *"활성 탭의 병합 index 를
    /// 명시해 보낸다(**원격 active 는 sess.active_index 와 다르므로 index 생략 불가**)"*.
    /// 우리도 같은 것을 싣는다 — `None` 은 탭바를 아직 못 받은 판뿐이다.
    TogglePin { index: Option<usize> },
    /// 페이스트 버퍼의 `index` 번째를 패널에 붙인다(0 = 맨 앞).
    PasteBuffer { index: usize },
    /// 세션·탭·패널 개요를 보내 달라(회신 = [`ServerMessage::Tree`]).
    RequestTree,
    /// 페이스트 버퍼 목록을 보내 달라(회신 = [`ServerMessage::Buffers`]).
    RequestBuffers,
    /// 플러그인 **화면**을 열어 달라(설계 Tier C · P4 — 회신 = `plugin_screen` 스펙).
    ///
    /// 왜 명령이 하나 더 필요한가: 플러그인의 화면은 정본에서 Textual 위젯이라 우리가
    /// 띄울 수 없다. 서버가 **무엇을 그릴지**를 자료로 주면 우리는 목록/글 두 모양만
    /// 그리면 된다 — 플러그인 코드는 한 벌로 남는다.
    PluginOpen { name: String, args: Vec<String> },
    /// 플러그인 **명령 한 줄**을 실행해 달라(pytmux-35).
    ///
    /// # 왜 `PluginOpen` 과 갈라야 하나
    ///
    /// 우리는 플러그인 명령을 오래 `plugin_open`("화면을 다오")으로만 보냈다. 화면을 여는
    /// 명령에는 맞지만 **상태를 바꾸는 명령**에는 통째로 틀린 길이라 서버가 거절했고,
    /// 팔레트에 보이는데 눌러도 안 먹는 줄이 열여덟이었다.
    ///
    /// # 우리는 어느 쪽인지 **모른다**
    ///
    /// 한 이름이 화면인지 상태인지는 플러그인이 안다. 그 표를 우리가 들면 서버와 갈리고,
    /// 갈린 순간 명령은 **조용히 아무 일도 안 한다** — 이 결함이 생긴 원인 그대로다.
    /// 그래서 고른 이름을 그냥 보내고 서버가 갈래를 정한다(못 알아들으면 서버가 화면
    /// 경로로 넘어간다).
    PluginCmd { name: String, args: Vec<String> },
    /// 플러그인 화면에서 **고른 줄과 누른 키**를 되돌려준다(설계 §4.3).
    ///
    /// ⚠ 액션 이름의 칸은 `do` 다 — `action` 은 명령 디스패처의 것이라(이 프레임의
    /// `action` 은 `plugin_action` 이다) 같은 이름을 쓰면 서로 덮는다.
    ///
    /// `input` 에 그 줄의 **`key`**(뜻)를 싣는다. 자리(번호)만 보내면 목록이 바뀔 때
    /// 엉뚱한 줄이 열린다 — 서버가 자리로 되찾게 두지 않는 것이 이 스펙의 계약이다.
    PluginAction { id: String, act: String, row: usize, input: Option<String> },
    /// 이 클라의 **패널 오버레이 상태**를 서버에 알린다(설계 Tier B · P3 · §4.4).
    ///
    /// 시계가 어느 패널에 떠 있는지는 클라만 안다(정본에서도 클라 플러그인 상태다).
    /// 서버가 같은 그림을 그리려면 그 **사실**을 들어야 한다 — 그릴지·어떻게는 여전히
    /// 플러그인이 정한다. 회신은 없다: 다음 프레임의 `plugin_cells` 가 곧 답이다.
    PluginOverlay { name: String, pane: i64, on: bool },
    /// 오버레이의 **클릭존/키가 실어 준 이름**을 그대로 되돌려 보낸다(Tier B).
    ///
    /// 우리는 `act` 가 무슨 뜻인지 모른다 — 달력의 `‹` 가 지난달인지 지난해인지는
    /// 플러그인이 정하고, 우리는 "그 자리를 눌렀다"만 말한다(설계 §4.4). 회신은 없다:
    /// 다음 `plugin_cells` 프레임이 곧 답이다.
    PluginOverlayAction { name: String, pane: i64, act: String },
    /// **이 클라만 아는 사실**을 서버에 알린다(설계 Tier D · §4.4 · P7).
    ///
    /// 오늘 목록은 입력기 한/영 하나다 — OS 가 그 상태를 **우리 창에만** 알려 주므로
    /// 서버가 스스로 알 수 없다. 다만 알려 주는 것은 사실뿐이고, **그릴지·어디에·무슨
    /// 색으로는 플러그인이 정한다**(Tier B). 그래야 규칙이 한 벌로 남는다 —
    /// 종전에는 우리가 그 그림을 손으로 들고 있었고, 자리가 정본과 갈려 있었다.
    ///
    /// `value` 가 `None` 이면 그 사실을 **지운다**(끄는 것도 사실이다).
    /// 회신은 없다: 다음 `plugin_cells` 프레임이 곧 답이다.
    ClientFact { name: String, value: Option<String> },
    /// **플러그인이 서버에 들고 있는 토글 하나**를 그 플러그인의 서버 액션으로 직접 친다.
    ///
    /// # 왜 `PluginOpen` 이 아닌가 (pytmux-35)
    ///
    /// 우리는 플러그인 명령을 **전부** `plugin_open`("화면을 다오")으로 보내 왔다. 화면을
    /// 여는 명령(`ncd`·`mdir`)에는 맞지만 **상태를 바꾸는 명령**에는 통째로 틀린 길이라,
    /// 서버가 *"이 플러그인은 화면 스펙을 제공하지 않습니다"* 로 거절하고 사용자에게는
    /// **팔레트에 보이는데 눌러도 안 먹는 줄**로 보였다(스물셋).
    ///
    /// 그런데 **서버는 처음부터 그 액션을 받고 있었다** — 정본 클라의 플러그인 훅이
    /// `send_cmd("set_claude_auto_retry", value=…)` 로 치는 바로 그 이름이다. 갈린 것은
    /// 서버가 아니라 **이름을 아는 자리**였다(정본은 파이썬 훅, 우리는 없었다).
    ///
    /// 값을 안 실으면 서버가 토글한다 — 정본도 무인자면 `value=None` 을 보내고 서버의
    /// `set_*(value=None)` 이 뒤집는다. 켜기/끄기를 따로 두면 클라가 현재값을 알아야 하고
    /// 그 값의 권위는 서버다(`ToggleSync` 와 같은 규율).
    PluginToggle { action: &'static str, value: Option<bool> },
    /// 인자 없이 서버에 **한 번 시키는** 플러그인 명령(`refresh_usage` 등).
    ///
    /// 위 토글의 짝이다 — 값이 없는 것이지 "토글"이 아니다. 둘을 한 변종으로 묶으면
    /// `Option<bool>` 의 `None` 이 "토글해라"와 "값이 없는 명령"을 겸하게 되어, 나중에
    /// 누가 값을 실을 때 뜻이 갈린다.
    PluginDo { action: &'static str },
    /// 입력을 창 안 모든 패널로 복제할지 토글한다(`synchronize-panes`).
    ///
    /// 인자를 안 실으면 서버가 토글한다 — 켜고 끄는 두 명령을 두면 클라가 상태를 알아야
    /// 하고, 그 상태는 서버가 권위다.
    ToggleSync { value: Option<bool> },
    /// 활동·벨 감시를 토글한다. `which` 는 `"activity"` 또는 `"bell"`.
    ToggleMonitor { which: &'static str, value: Option<bool> },
    /// 탭 이름 자동 갱신을 토글한다.
    ToggleAutoRename { value: Option<bool> },
    /// 패널 테두리에 제목을 항상 보일지 토글한다(서버 옵션).
    ToggleBorderStatus,
    /// 서버 옵션 하나를 **값 없이** 토글한다(서버가 뒤집는다).
    ///
    /// 하나로 묶은 이유: 여섯 개가 전부 `set_<이름>` + `value` 없음이라 변형을 여섯 벌
    /// 두면 표만 길어지고 규칙은 같다. 이름은 [`ServerOption`] 이 못박는다 — 문자열을
    /// 호출부가 적으면 오타가 조용히 아무 일도 안 하는 명령이 된다.
    SetOption(ServerOption, Option<bool>),
    /// 스크롤백을 비운다(`clear-history`).
    ClearHistory,
    /// 지금 탭을 `index` 자리와 **바꾼다**(`swap-tab`).
    ///
    /// [`Command::MoveCurrentTab`] 과 갈리는 점: 저쪽은 **밀어 넣고**(사이 탭들이 한 칸씩
    /// 밀린다) 이쪽은 **자리를 맞바꾼다**. 서버 명령이 둘로 나뉜 이유이기도 하다.
    SwapTab { index: usize },
    /// `index` 자리의 탭을 `to` 자리로 **밀어 넣는다**(`move-tab`).
    ///
    /// [`Command::MoveCurrentTab`] 과 갈리는 점: 저쪽은 **지금 탭**을 옮기고 이쪽은 자리를
    /// 실어 **고른 탭**을 옮긴다. 탭바 포커스의 `Shift+←→` 가 이것을 쓴다 — 고른 탭과 활성
    /// 탭이 다를 수 있어서다.
    MoveTab { index: usize, to: usize },
    /// 패널 내용을 서버의 페이스트 버퍼로 캡처한다. `full` 이면 스크롤백 전체.
    CapturePane { full: bool },
    /// 패널 출력을 외부 명령으로 흘린다(`pipe-pane`). 빈 명령은 **끄기**다.
    PipePane { cmd: String },
    /// 다른 탭의 패널을 지금 탭으로 합친다(`join-pane`).
    JoinPane { src: usize, horizontal: bool },
    /// 서버에 **라이브 PTY 팝업**을 띄운다(`display-popup`).
    ///
    /// 셸을 우리가 돌리는 `run-shell` 과 다르다 — 이건 서버가 PTY 를 띄우고 우리는
    /// 그 화면을 그리고 키를 넘긴다. 그래서 **대화형 명령**(vim·top)도 된다.
    /// `w`/`h` 는 칸 수 희망값(`-w N`/`-h N` — tmux 표기). 없으면 서버가 중앙
    /// 기본 rect 를 잡는다(`servertree._popup_rect`).
    PopupOpen { cmd: String, title: String, w: Option<u32>, h: Option<u32> },
    /// 떠 있는 팝업을 닫는다(`display-popup -C`).
    PopupClose,
    /// 서버 버전·업타임을 묻는다(`version`).
    RequestVersion,
    /// 작업 보존 재시작이 안전한지 **실행 없이** 점검한다(`restart-check`).
    RequestRestartCheck,
    /// 값이 정해진 서버 옵션 하나를 그 값으로 놓는다(`vt-parser`·`window-size`).
    SetEnum { action: &'static str, value: &'static str },
    /// 레이아웃 프리셋을 적용한다(`select-layout`).
    SelectLayout { preset: &'static str },
    /// 현재 탭의 배치를 이름으로 저장한다(`layout-save`).
    SaveTabLayout { name: String },
    /// 저장한 배치를 불러온다. `new` 면 **새 탭에**, 아니면 현재 탭을 덮어쓴다.
    LoadTabLayout { name: String, new: bool },
    /// 서버 전체 배치를 영속한다(`save-layout`).
    SaveLayout,
    /// 영속한 전체 배치를 되돌린다(`restore-layout`).
    RestoreLayout,
    /// 탭 고정을 **명시적으로** 켜거나 끈다(`pin-tab`/`unpin-tab`).
    ///
    /// [`Command::TogglePin`] 과 나란히 두는 이유: 팔레트·메뉴에서 "고정한다"를 고른
    /// 사람은 **켜지기를** 기대하지 뒤집히기를 기대하지 않는다.
    SetPinned {
        /// 어느 탭인가(리스트 위치 = index). `None` 이면 활성 탭(서버 기본).
        /// 드래그로 핀 경계를 넘긴 탭(G9v)은 활성이 아닐 수 있어 자리를 싣는다.
        index: Option<usize>,
        on: bool,
    },
    /// 죽은 패널에 셸을 다시 띄운다(`respawn-pane`).
    RespawnPane,
    /// 서버와 그 아래 모든 탭·셸을 끝낸다.
    ///
    /// **이 저장소에서 가장 비싼 명령**이다 — 같은 서버에 붙은 다른 클라의 작업까지
    /// 통째로 내린다. 뷰는 확인 화면 없이 이걸 큐에 넣지 않는다.
    KillServer,
    /// 서버 코드를 갈아 끼운다(작업 보존 re-exec). 셸·PTY 는 살아 있다.
    RestartServer,
    /// 다른 상자의 pytmux 탭을 이 탭바에 붙인다.
    ///
    /// `via` 는 **다중홉**(ProxyJump)이다 — `C via B` 는 "C 로 직접 ssh 가 안 되고 B 를
    /// 거쳐야 한다"는 뜻이고, 서버가 그것을 `ssh -J B C` 로 편다. 있을 때만 싣는다:
    /// 1홉 프레임이 종전과 한 바이트도 안 달라져 구서버와 그대로 맞물린다.
    /// 만드는 것은 [`Command::remote_attach`] 한 곳이다(문법을 두 뷰가 각자 파싱하면
    /// 한쪽에서만 `via` 가 먹는다).
    RemoteAttach { host: String, via: Option<String> },
    /// 다른 상자에 **새 셸**을 띄워 탭으로 붙인다.
    ///
    /// 와이어 이름이 `remote_new_window` 인 것에 주의 — 사용자 어휘는 '탭'이고 서버
    /// 어휘는 '윈도우'다(이 저장소 전체가 같은 어긋남을 안고 있다).
    RemoteNewTab { host: String },
    /// 원격 붙임을 푼다. `host` 가 비면 **전부**다.
    RemoteDetach { host: String },
    /// 활성 패널의 제목을 바꾼다(`prefix T`).
    SetPaneTitle { title: String },
    /// 플러그인 하나를 켜고 끈다(패리티 G7).
    ///
    /// 여기만은 **값을 싣는다**(다른 토글은 서버가 뒤집는다) — 파이썬 클라도 `on` 을
    /// 실어 보낸다. 목록 화면은 각 줄의 현재 상태를 이미 알고 있어서, 뒤집을 값을
    /// 아는 쪽이 클라다.
    SetPluginEnabled { name: String, on: bool },
    /// 활성 Claude 패널에서 **이전/다음에 입력한 프롬프트** 자리로 스크롤한다
    /// (`esc Ctrl+↑`/`Ctrl+↓` — 패리티 `e_jump`).
    ///
    /// # 서버 표에 없는 명령이다
    ///
    /// 이 이름은 `_CMD_TABLE` 이 아니라 **claude-code 플러그인**이 소유한다
    /// (`serverio._dispatch_plugin_cmd` → 플러그인 `server_command` 훅). 그래서 픽스처
    /// 생성기가 표만 뽑던 동안에는 "서버에 없는 이름"으로 보였다 — 지금은 플러그인 훅도
    /// 함께 긁는다(`scripts/gen_command_fixture.py`).
    ///
    /// 플러그인이 없으면 아무도 안 집고 **조용히 끝난다**(delete-to-disable). 파이썬
    /// 클라도 같은 자리에서 같은 일을 한다 — 키는 무동작이 되고 모드만 바뀐다.
    JumpPrompt { direction: &'static str },
    /// 토큰리밋 **자동재개**를 뒤집는다(`prefix R` — 파이썬 `p_R`).
    ///
    /// 인자를 안 싣는 것이 곧 "뒤집어라"다(`set_plugin_enabled` 와 달리 값을 안 보낸다 —
    /// 서버가 지금 값을 갖고 있고 파이썬 클라도 인자 없이 부른다).
    ///
    /// `jump_prompt` 와 같은 자리다 — 표가 아니라 **claude-code 플러그인**이 소유하는
    /// 이름이라, 플러그인이 없으면 아무도 안 집고 조용히 끝난다(delete-to-disable).
    SetAutoresume,
    /// 프롬프트 단위 클리어 토글(claude-code 플러그인 — 자동재개와 같은 자리).
    SetPromptClear,
    /// 스크롤백 검색(`search`). 검색은 **서버가 한다** — 스크롤백은 서버에 있고
    /// (`jump_prompt` 와 같은 이유), 서버가 맞은 줄로 스크롤을 옮겨 새 프레임을
    /// 민다. `query` 가 없으면 지난 검색어의 반복이다(파이썬 `n`/`N`).
    Search { query: Option<String>, down: bool },
    /// 전역 검색(`search_all` · esc `f`·메뉴 — pytmux-27). 열려 있는 **모든** 로컬
    /// 탭·패널(+원격 중계)의 스크롤백을 훑는다 — `Search` 는 활성 패널 하나 안에서
    /// 다음 히트로 넘어가는 것뿐이라 다른 명령이다. 회신은 `search_results`
    /// (disposition `HANDLED` — 요청 클라에게만 온다, 남의 화면에 안 뜬다).
    SearchAll { query: String },
    /// 검색 결과 한 줄이 가리키는 자리로 뛴다(`search_goto`). 로컬(`route` 비어
    /// 있음)이면 요청 클라가 곧바로 그 탭·패널·스크롤을 본다(disposition `FULL`).
    /// 원격 히트(`route` 있음)는 서버가 캐스케이드로 릴레이한다 — 이 클라는 `route`
    /// 를 해석하지 않고 받은 그대로 되돌려 보낸다(좌표계를 아는 건 서버 하나).
    SearchGoto {
        wid: Option<i64>,
        win: usize,
        pane: i64,
        line: i64,
        route: Vec<String>,
        query: String,
    },
    /// 서버가 **제 프로세스를 잰** 한 장을 달라(`debug_stats` · pytmux-382). 회신은
    /// `debug_stats`(disposition `HANDLED` — 요청 클라에게만 온다). `debug-stats` 판이
    /// 열릴 때 한 번 보내고, 판은 회신이 올 때까지 「서버가 아직 답하지 않았다」로 있는다.
    DebugStats,
    /// 출력 캡처(REC) 토글(`set_capture` — rec 서버 플러그인 소유). 값을 안 실으면
    /// 반전이다(파이썬 `[c]` 와 같다). 플러그인이 없으면 서버가 조용히 무시한다.
    SetCapture,
}

/// 값 없이 토글하는 서버 옵션들.
///
/// 이름을 여기 한 곳에만 둔다 — 호출부가 문자열을 적으면 오타가 **조용히 아무 일도 안 하는
/// 명령**이 된다(서버는 모르는 action 을 플러그인 훅으로 넘기고 끝낸다).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ServerOption {
    /// 패널 하나일 때도 테두리를 그릴까.
    SingleBorder,
    /// 화면 갱신을 모아서 보낼까(성능).
    CoalesceRepaints,
    /// 세션이 비면 서버를 끝낼까.
    ExitEmpty,
    /// 원격에서 pytmux 를 띄우면 자동으로 remote-attach 할까.
    NestAutoAttach,
    /// Windows 에서 마우스 이동을 추적할까.
    WinMouseMotion,
}

impl ServerOption {
    /// 와이어 action 이름.
    pub fn action(self) -> &'static str {
        match self {
            ServerOption::SingleBorder => "set_single_border",
            ServerOption::CoalesceRepaints => "set_coalesce",
            ServerOption::ExitEmpty => "set_exit_empty",
            ServerOption::NestAutoAttach => "set_nest_auto_attach",
            ServerOption::WinMouseMotion => "set_win_mouse_motion",
        }
    }
}

/// 방향의 **서버 어휘**. core 의 [`Dir`](base::Dir) 를 와이어 값으로 옮긴다.
///
/// 철자를 여기 한 곳에만 둔다 — `"up"` 을 두 곳에 적으면 한쪽만 고쳐지는 날이 온다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Dir(&'static str);

impl Dir {
    pub const LEFT: Dir = Dir("left");
    pub const RIGHT: Dir = Dir("right");
    pub const UP: Dir = Dir("up");
    pub const DOWN: Dir = Dir("down");

    pub fn as_str(self) -> &'static str {
        self.0
    }
}

impl From<base::Dir> for Dir {
    fn from(dir: base::Dir) -> Self {
        match dir {
            base::Dir::Left => Dir::LEFT,
            base::Dir::Right => Dir::RIGHT,
            base::Dir::Up => Dir::UP,
            base::Dir::Down => Dir::DOWN,
        }
    }
}

impl Command {
    /// `servercmd.py` 테이블에서의 이름.
    ///
    /// **이름이 적히는 곳은 여기 하나다.** 와일드카드 팔이 없으므로 변형을 추가하면
    /// 여기서 컴파일이 막힌다. 이름이 서버에 실제로 있는지는
    /// `tests/command_conformance.rs` 가 `servercmd.py` 에서 뽑은 표와 대조한다 —
    /// 이 파일 안에서 목록을 다시 적어 자기끼리 맞춰 보지 말 것.
    pub fn action(&self) -> &'static str {
        match self {
            Command::SelectWindow { .. } => "select_window",
            Command::NextWindow => "next_window",
            Command::PrevWindow => "prev_window",
            Command::LastWindow => "last_window",
            Command::NewWindow { .. } => "new_window",
            Command::KillWindow => "kill_window",
            Command::MoveCurrentTab { .. } => "move_current_tab",
            Command::MoveWindow { .. } => "move_window",
            Command::RenameWindow { .. } => "rename_window",
            Command::RenameSession { .. } => "rename_session",
            Command::Split { .. } => "split",
            Command::SelectPaneId { .. } => "select_pane_id",
            Command::KillPane => "kill_pane",
            Command::ResizeSplit { .. } => "resize",
            Command::Paste { .. } => "paste",
            Command::CopyRange { .. } => "copy_range",
            Command::SetBuffer { .. } => "set_buffer",
            Command::RequestRedraw => "request_redraw",
            Command::Zoom => "zoom",
            Command::CyclePane => "cycle_pane",
            Command::LastPane => "last_pane",
            Command::CycleLayout => "cycle_layout",
            Command::Rotate { .. } => "rotate",
            Command::SwapPane { .. } => "swap_pane",
            Command::BreakPane => "break_pane",
            Command::SelectPaneDir { .. } => "select_pane",
            Command::ResizeDir { .. } => "resize_dir",
            Command::TogglePin { .. } => "set_pinned",
            Command::PasteBuffer { .. } => "paste_buffer",
            Command::RequestTree => "request_tree",
            Command::RequestBuffers => "request_buffers",
            Command::PluginToggle { action, .. } | Command::PluginDo { action } => action,
            Command::PluginOpen { .. } => "plugin_open",
            Command::PluginCmd { .. } => "plugin_cmd",
            Command::PluginAction { .. } => "plugin_action",
            Command::PluginOverlay { .. } => "plugin_overlay",
            Command::PluginOverlayAction { .. } => "plugin_overlay_action",
            Command::ClientFact { .. } => "client_fact",
            Command::ToggleSync { .. } => "set_sync",
            Command::ToggleMonitor { .. } => "set_monitor",
            Command::ToggleAutoRename { .. } => "set_auto_rename",
            Command::ToggleBorderStatus => "set_border_status",
            Command::SetOption(opt, _) => opt.action(),
            Command::ClearHistory => "clear_history",
            Command::SwapTab { .. } => "swap_window",
            Command::MoveTab { .. } => "move_tab",
            Command::SetPinned { .. } => "set_pinned",
            Command::CapturePane { .. } => "capture_pane",
            Command::PipePane { .. } => "pipe_pane",
            Command::JoinPane { .. } => "join_pane",
            Command::PopupOpen { .. } => "popup_open",
            Command::PopupClose => "popup_close",
            Command::RequestVersion => "request_version",
            Command::RequestRestartCheck => "request_restart_check",
            Command::SetEnum { action, .. } => action,
            Command::SelectLayout { .. } => "select_layout",
            Command::SaveTabLayout { .. } => "save_tab_layout",
            Command::LoadTabLayout { .. } => "load_tab_layout",
            Command::SaveLayout => "save_layout",
            Command::RestoreLayout => "restore_layout",
            Command::RespawnPane => "respawn_pane",
            Command::KillServer => "kill_server",
            Command::RestartServer => "restart_server",
            Command::RemoteAttach { .. } => "remote_attach",
            Command::RemoteNewTab { .. } => "remote_new_window",
            Command::RemoteDetach { .. } => "remote_detach",
            Command::SetPluginEnabled { .. } => "set_plugin_enabled",
            Command::SetPaneTitle { .. } => "set_pane_title",
            Command::JumpPrompt { .. } => "jump_prompt",
            Command::SetAutoresume => "set_autoresume",
            Command::SetPromptClear => "set_prompt_clear",
            Command::Search { .. } => "search",
            Command::SearchAll { .. } => "search_all",
            Command::SearchGoto { .. } => "search_goto",
            Command::DebugStats => "debug_stats",
            Command::SetCapture => "set_capture",
        }
    }

    /// 와이어에 실을 JSON. `t`/`action` 은 여기서 채운다.
    pub fn to_frame(&self) -> serde_json::Value {
        use serde_json::json;
        let mut extra = match self {
            // wid 는 **있을 때만** 싣는다 — 구서버(키를 모르는)에 보내는 프레임이
            // 종전과 한 바이트도 안 달라진다. 정본도 같은 규칙이다.
            Command::SelectWindow { index, wid } => match wid {
                Some(wid) => json!({ "index": index, "wid": wid }),
                None => json!({ "index": index }),
            },
            Command::NextWindow => json!({}),
            Command::PrevWindow => json!({}),
            Command::LastWindow => json!({}),
            // `cmd` 는 **있을 때만** 싣는다 — 늘 실으면 셸 탭 프레임에 `"cmd":null`
            // 이 붙어 서버 로그·픽스처가 지저분해지고, 구판 서버가 그 칸을 어떻게
            // 읽는지에 기대게 된다.
            Command::NewWindow { path, cmd } => match cmd {
                Some(cmd) => json!({ "path": path, "cmd": cmd }),
                None => json!({ "path": path }),
            },
            Command::KillWindow => json!({}),
            Command::MoveCurrentTab { direction } => json!({ "where": direction }),
            Command::MoveWindow { index } => json!({ "index": index }),
            Command::RenameWindow { name } => json!({ "name": name }),
            Command::RenameSession { name } => json!({ "name": name }),
            // ★ 서버가 읽는 이름은 `orient` 다(`servercmd._cmd_split` → `msg.get("orient",
            // "lr")`). 여기서 `horizontal` 을 보내던 동안 서버는 그 칸을 못 찾아
            // **늘 기본값 `lr` 로 떨어졌고**, 그래서 상하 분할이 한 번도 안 됐다
            // (2026-07-29 라이브에서 잡았다 — 적합성 게이트는 **이름만** 보고 칸은 안 본다).
            Command::Split { horizontal, path } => {
                json!({ "orient": if *horizontal { "lr" } else { "tb" }, "path": path })
            }
            Command::SelectPaneId { id } => json!({ "id": id }),
            Command::KillPane => json!({}),
            Command::ResizeSplit { split_id, ratio } => {
                json!({ "split_id": split_id, "ratio": ratio })
            }
            Command::Paste { text } => json!({ "text": text }),
            Command::CopyRange {
                pane,
                y0,
                x0,
                y1,
                x1,
            } => json!({ "pane": pane, "y0": y0, "x0": x0, "y1": y1, "x1": x1 }),
            Command::SetBuffer { text } => json!({ "text": text }),
            Command::RequestRedraw => json!({}),
            Command::Zoom
            | Command::CyclePane
            | Command::LastPane
            | Command::CycleLayout
            | Command::BreakPane
             => json!({}),
            // 자리를 실어야 원격(병합) 탭에도 걸린다(§10-21ⓒ3) — `None` 이면 서버가
            // 활성 탭으로 접는데, 그 기본값은 **로컬 탭**이라 원격에서는 어긋난다.
            Command::TogglePin { index } => json!({ "index": index }),
            Command::Rotate { forward } | Command::SwapPane { forward } => {
                json!({ "forward": forward })
            }
            Command::SelectPaneDir { dir } => json!({ "dir": dir.as_str() }),
            Command::ResizeDir { dir, cells } => {
                json!({ "dir": dir.as_str(), "cells": cells })
            }
            Command::PasteBuffer { index } => json!({ "index": index }),
            Command::PluginOpen { name, args } | Command::PluginCmd { name, args } => {
                json!({ "name": name, "args": args })
            }
            Command::PluginAction { id, act, row, input } => {
                json!({ "id": id, "do": act, "row": row, "input": input })
            }
            Command::PluginOverlay { name, pane, on } => {
                json!({ "name": name, "pane": pane, "on": on })
            }
            Command::PluginOverlayAction { name, pane, act } => {
                json!({ "name": name, "pane": pane, "do": act })
            }
            Command::ClientFact { name, value } => json!({ "name": name, "value": value }),
            Command::RequestTree
            | Command::RequestBuffers
            // 값을 안 실으면 서버가 토글한다(`_cmd_set_sync`·`_cmd_set_auto_rename`).
            | Command::PluginToggle { value: None, .. }
            | Command::PluginDo { .. }
            | Command::ToggleSync { value: None }
            | Command::ToggleAutoRename { value: None }
            | Command::ToggleBorderStatus
            | Command::SetOption(_, None)
            | Command::ClearHistory
            | Command::RespawnPane
            | Command::SaveLayout
            | Command::RestoreLayout
            | Command::PopupClose
            | Command::RequestVersion
            | Command::RequestRestartCheck
            | Command::KillServer
            | Command::RestartServer => json!({}),
            Command::RemoteAttach { host, via } => match via {
                Some(via) => json!({ "host": host, "via": via }),
                None => json!({ "host": host }),
            },
            Command::RemoteNewTab { host } => json!({ "host": host }),
            // 빈 host 는 **키를 아예 안 싣는다** — 파이썬 클라와 같다(빈 문자열을 실으면
            // 서버는 "그 이름의 원격"을 찾다가 아무것도 못 떼고 조용히 끝난다).
            Command::RemoteDetach { host } => {
                if host.is_empty() {
                    json!({})
                } else {
                    json!({ "host": host })
                }
            }
            Command::SetPluginEnabled { name, on } => json!({ "name": name, "on": on }),
            Command::SetPaneTitle { title } => json!({ "title": title }),
            Command::JumpPrompt { direction } => json!({ "direction": direction }),
            // 인자를 안 싣는 것이 곧 토글이다(서버가 지금 값을 갖고 있다).
            Command::SetAutoresume => json!({}),
            Command::SetPromptClear => json!({}),
            Command::SetCapture => json!({}),
            Command::Search { query, down } => {
                let direction = if *down { "down" } else { "up" };
                match query {
                    // 값 없는 반복(`n`/`N`) — 파이썬도 direction 만 싣는다.
                    None => json!({ "direction": direction }),
                    Some(q) => json!({ "query": q, "direction": direction }),
                }
            }
            Command::SearchAll { query } => json!({ "query": query }),
            // `wid` 는 **있을 때만** 싣는다(`SelectWindow` 와 같은 규칙 — 구서버 호환).
            // `route` 는 로컬 히트면 빈 배열 그대로 보낸다(서버가 빈 배열=로컬로 읽는다).
            Command::DebugStats => json!({}),
            Command::SearchGoto { wid, win, pane, line, route, query } => {
                let mut value = json!({
                    "win": win, "pane": pane, "line": line, "route": route, "query": query,
                });
                if let Some(wid) = wid {
                    value["wid"] = json!(wid);
                }
                value
            }
            Command::SwapTab { index } => json!({ "index": index }),
            Command::MoveTab { index, to } => json!({ "index": index, "to": to }),
            Command::SetPinned { index, on } => match index {
                Some(index) => json!({ "index": index, "value": on }),
                None => json!({ "value": on }),
            },
            Command::PopupOpen { cmd, title, w, h } => {
                let mut value = json!({ "cmd": cmd, "title": title });
                // 없는 희망값은 칸을 아예 안 싣는다 — 서버 `msg.get("w")` 의 None 과
                // 같은 뜻이 되고, null 을 싣는 것보다 와이어가 정직하다.
                if let Some(w) = w {
                    value["w"] = json!(w);
                }
                if let Some(h) = h {
                    value["h"] = json!(h);
                }
                value
            }
            Command::CapturePane { full } => json!({ "full": full }),
            Command::PipePane { cmd } => json!({ "cmd": cmd }),
            // 서버는 `tb`(상하)/`lr`(좌우)로 읽는다.
            Command::JoinPane { src, horizontal } => {
                json!({ "src": src, "orient": if *horizontal { "lr" } else { "tb" } })
            }
            Command::SetEnum { value, .. } => json!({ "value": value }),
            Command::SelectLayout { preset } => json!({ "preset": preset }),
            Command::SaveTabLayout { name } => json!({ "name": name }),
            Command::LoadTabLayout { name, new } => json!({ "name": name, "new": new }),
            // ★ `value` 를 실으면 **그 값으로 정해지고**, 안 실으면 서버가 뒤집는다
            // (`servertree.set_monitor` 의 `value=None` 갈래). 인자 폼(G8v)이 켜기·끄기를
            // 따로 고를 수 있는 이유가 이것이다 — 서버는 처음부터 값을 받고 있었다.
            Command::ToggleMonitor { which, value } => match value {
                Some(on) => json!({ "which": which, "value": on }),
                None => json!({ "which": which }),
            },
            Command::PluginToggle { value: Some(on), .. }
            | Command::ToggleSync { value: Some(on) }
            | Command::ToggleAutoRename { value: Some(on) }
            | Command::SetOption(_, Some(on)) => json!({ "value": on }),
        };
        let obj = extra.as_object_mut().expect("json! 이 객체를 만든다");
        obj.insert("t".into(), json!("cmd"));
        obj.insert("action".into(), json!(self.action()));
        extra
    }

    /// `붙을 상자 (host)` 대답 하나를 `remote_attach` 명령으로. 빈 값이면 `None`.
    ///
    /// # 문법이 왜 여기 있나
    ///
    /// 다중홉은 `C via B` 로 적는다. 이 파싱을 뷰가 각자 하면 **한쪽 클라에서만 via 가
    /// 먹는다** — 그런 어긋남은 라이브에서만 드러나고, 그때 사용자는 "이 클라에서는
    /// 3대 구성이 안 된다"로 읽는다.
    ///
    /// 규칙은 정본 `clientcmd` 그대로다:
    /// - ` via ` 를 **구분자로만** 쓰고 양쪽은 **원시 문자열**로 남긴다(도메인 계정
    ///   `NATGAMES\\user@host` 의 백슬래시를 토크나이저가 삼키지 않게).
    /// - **마지막** ` via ` 에서 자른다(rpartition) — 호스트 이름 안에 그 낱말이 들어
    ///   있어도 마지막 것이 구분자다.
    /// - 한쪽이 비면 구분자로 안 친다(`x via ` 는 그냥 호스트 이름이다).
    pub fn remote_attach(answer: &str) -> Option<Command> {
        let answer = answer.trim();
        if answer.is_empty() {
            return None;
        }
        match answer.rsplit_once(" via ") {
            Some((head, tail)) if !head.trim().is_empty() && !tail.trim().is_empty() => {
                Some(Command::RemoteAttach {
                    host: head.trim().to_owned(),
                    via: Some(tail.trim().to_owned()),
                })
            }
            _ => Some(Command::RemoteAttach { host: answer.to_owned(), via: None }),
        }
    }

    /// 변형마다 표본 하나씩.
    ///
    /// 적합성 테스트가 **전수** 순회하는 근거다 — 여기 빠진 변형은 서버 대조를 안 받는다.
    /// 빠뜨림은 같은 파일의 `all_covers_every_variant` 가 막는다(exhaustive match +
    /// 개수 단언). 인자값은 아무거나여도 된다. 대조하는 것은 이름이지 값이 아니다.
    pub fn all() -> Vec<Command> {
        vec![
            Command::SelectWindow { index: 0, wid: Some(7) },
            Command::NextWindow,
            Command::PrevWindow,
            Command::LastWindow,
            Command::NewWindow {
                path: "current".into(),
                cmd: None,
            },
            Command::KillWindow,
            Command::MoveCurrentTab { direction: "left" },
            Command::MoveWindow { index: 1 },
            Command::RenameWindow { name: "x".into() },
            Command::RenameSession { name: "x".into() },
            Command::Split {
                horizontal: true,
                path: "current".into(),
            },
            Command::SelectPaneId { id: 2 },
            Command::KillPane,
            Command::ResizeSplit {
                split_id: 3,
                ratio: 0.5,
            },
            Command::Paste {
                text: "x".into(),
            },
            Command::CopyRange {
                pane: 4,
                y0: 10,
                x0: 0,
                y1: 12,
                x1: 5,
            },
            Command::SetBuffer {
                text: "x".into(),
            },
            Command::RequestRedraw,
            Command::Zoom,
            Command::CyclePane,
            Command::LastPane,
            Command::CycleLayout,
            Command::Rotate { forward: true },
            Command::SwapPane { forward: false },
            Command::BreakPane,
            Command::SelectPaneDir { dir: Dir::LEFT },
            Command::ResizeDir { dir: Dir::UP, cells: 3 },
            Command::TogglePin { index: None },
            Command::PasteBuffer { index: 0 },
            Command::RequestTree,
            Command::RequestBuffers,
            Command::PluginOpen { name: "mdir".into(), args: vec![] },
            Command::PluginAction {
                id: "p4changes".into(),
                act: "describe".into(),
                row: 0,
                input: Some("68995".into()),
            },
            Command::ToggleSync { value: None },
            Command::ToggleMonitor { which: "activity", value: None },
            Command::ToggleAutoRename { value: None },
            Command::ToggleBorderStatus,
            Command::SetOption(ServerOption::SingleBorder, None),
            Command::ClearHistory,
            Command::RespawnPane,
            Command::SwapTab { index: 2 },
            Command::MoveTab { index: 0, to: 1 },
            Command::CapturePane { full: true },
            Command::PipePane { cmd: "tee /tmp/log".into() },
            Command::JoinPane { src: 1, horizontal: false },
            Command::PopupOpen { cmd: "top".into(), title: String::new(), w: None, h: None },
            Command::PopupClose,
            Command::RequestVersion,
            Command::RequestRestartCheck,
            Command::SetEnum { action: "set_vt_parser", value: "pyte" },
            Command::SelectLayout { preset: "tiled" },
            Command::SaveTabLayout { name: "dev".into() },
            Command::LoadTabLayout { name: "dev".into(), new: false },
            Command::SaveLayout,
            Command::RestoreLayout,
            Command::SetPinned { index: None, on: true },
            Command::KillServer,
            Command::RestartServer,
            Command::RemoteAttach { host: "box1".into(), via: Some("box0".into()) },
            Command::RemoteNewTab { host: "box1".into() },
            Command::RemoteDetach { host: String::new() },
            Command::SetPluginEnabled { name: "clock".into(), on: false },
            Command::SetPaneTitle { title: "build".into() },
            Command::JumpPrompt { direction: "up" },
            Command::SetAutoresume,
            Command::SetPromptClear,
            Command::Search { query: None, down: false },
            Command::SetCapture,
            Command::SearchAll { query: "에러".into() },
            Command::SearchGoto {
                wid: Some(7),
                win: 0,
                pane: 3,
                line: 120,
                route: vec![],
                query: "에러".into(),
            },
            Command::DebugStats,
            // ★ 아래 셋은 **오래 빠져 있었다** — `VARIANT_COUNT` 가 안 따라 올라가서
            // (67) 색인 67·68 이 검사 범위 밖으로 나갔고, 그래서 "all() 에 넣는 것까지
            // 강제한다"던 가드가 조용히 이 셋을 안 봤다(실측 2026-08-02i · P7).
            // 세 번째(ClientFact)를 같은 자리에 더하려다 발견해 함께 메운다.
            Command::PluginOverlay { name: "clock".into(), pane: 1, on: true },
            Command::PluginOverlayAction {
                name: "calendar".into(),
                pane: 1,
                act: "prev".into(),
            },
            Command::ClientFact { name: "ime".into(), value: Some("한".into()) },
        ]
    }
}

/// 서버로 나가는 것 하나. **프레임 종류가 셋**이라 한 큐로 모은다.
///
/// # 왜 한 큐인가
///
/// 종류별로 큐를 따로 두면 이벤트 루프가 종류 단위로 보내게 되고, 그 순간 **사용자가
/// 한 순서가 뒤집힌다**: `echo ` 를 치고 → 붙여넣고 → Enter 를 치면, 입력 큐(`echo `,
/// Enter)를 먼저 비운 뒤 붙여넣기가 가서 셸이 빈 명령을 실행하고 붙여넣은 글자만 남는다.
/// 실제로 그 모양이었다(붙여넣기가 명령 프레임이 된 2026-07-27j 이후).
#[derive(Debug, Clone, PartialEq)]
pub enum Outgoing {
    Command(Command),
    /// 패널로 보낼 키 바이트.
    Input(Vec<u8>),
    /// **그 패널로만** 보낼 키 바이트(팝업).
    ///
    /// [`Outgoing::Input`] 과 갈리는 이유: 평소 입력은 서버가 **활성 패널**로 흘리는데,
    /// 팝업 패널은 트리 밖이라 활성이 될 수 없다. id 를 실어야 그 PTY 에 닿는다.
    InputToPane { pane: i64, data: Vec<u8> },
    /// 패널 **안 프로그램**에게 넘길 마우스 리포트([`Input::mouse`]).
    ///
    /// 키 입력과 종류가 같은 프레임이지만 변형을 나눈 이유는, 이 둘이 서버에서 **다른
    /// 경로를 탄다**는 것이 클라 쪽 계약이기 때문이다(입력 동기화·프롬프트 추적을 건너뛴다).
    /// 한 변형에 플래그로 담으면 그 플래그를 안 세운 실수가 조용히 지나간다.
    Mouse { pane: i64, data: Vec<u8> },
    Scroll(Scroll),
    Resize(Resize),
    /// RTT 측정 ping(`{"t":"ping","ts":..}` — G9u). 서버가 `ts` 를 echo 한 `pong` 을
    /// 즉시 돌려준다. 명령이 아니라 자기 프레임 종류라 변형을 나눈다(`Resize` 와 같다).
    Ping { ts: f64 },
    /// 창이 포커스를 얻거나 잃었다(`{"t":"focus","on":..}` · pytmux-421).
    ///
    /// 서버는 이것으로 **포커스 리포트(DECSET 1004)를 켠 패널에만** `ESC[I`/`ESC[O` 를
    /// 쓴다 — 앱은 그 신호로 깜빡임·폴링·자동 새로고침을 멈춘다. 안 보내면 앱은
    /// 「단말이 늘 포커스」로 알고 살아, 배경에서도 포그라운드처럼 계속 돈다.
    ///
    /// 명령이 아니라 자기 프레임 종류다(`Resize`·`Ping` 과 같다).
    Focus { on: bool },
}

impl Outgoing {
    /// 와이어에 실을 JSON. 종류를 아는 곳은 여기 하나다.
    pub fn to_frame(&self) -> serde_json::Value {
        match self {
            Outgoing::Command(cmd) => cmd.to_frame(),
            Outgoing::Input(bytes) => {
                serde_json::to_value(Input::new(bytes)).expect("Input 은 항상 직렬화된다")
            }
            Outgoing::InputToPane { pane, data } => {
                serde_json::to_value(Input::to_pane(*pane, data))
                    .expect("Input 은 항상 직렬화된다")
            }
            Outgoing::Mouse { pane, data } => serde_json::to_value(Input::mouse(*pane, data))
                .expect("Input 은 항상 직렬화된다"),
            Outgoing::Scroll(scroll) => {
                serde_json::to_value(scroll).expect("Scroll 은 항상 직렬화된다")
            }
            Outgoing::Resize(resize) => {
                serde_json::to_value(resize).expect("Resize 는 항상 직렬화된다")
            }
            Outgoing::Ping { ts } => serde_json::json!({ "t": "ping", "ts": ts }),
            Outgoing::Focus { on } => serde_json::json!({ "t": "focus", "on": on }),
        }
    }
}

/// 클라의 **내용 영역** 크기를 다시 알린다. 명령이 아니라 자기 프레임 종류다
/// (`serverio` 의 `t == "resize"` 분기).
///
/// 서버는 이 크기에 **정확히 맞춰** 캔버스를 만들고, 같은 세션의 모든 클라에게 전체
/// 프레임을 다시 보낸다. 그래서 알리지 않으면 창을 키워도 캔버스가 안 자라고, 반대로
/// 줄이면 캔버스가 화면 밖으로 밀린다.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Resize {
    pub t: &'static str,
    pub cols: u16,
    pub rows: u16,
}

impl Resize {
    /// 서버가 `clamp_dim` 으로 강제하는 범위에 맞춰 자른다 — 핸드셰이크(`Hello`)와 같은
    /// 규칙이라 첫 크기와 이후 크기가 다른 규칙을 타지 않는다.
    pub fn new(cols: u16, rows: u16) -> Self {
        Self {
            t: "resize",
            cols: cols.clamp(crate::message::MIN_W, crate::message::MAX_W),
            rows: rows.clamp(crate::message::MIN_H, crate::message::MAX_H),
        }
    }
}

/// 서버에 알린 크기를 기억한다. **바뀌었을 때만** 프레임을 만든다.
///
/// # 왜 기억하나
///
/// 터미널 크기는 이벤트가 아니라 **폴링**으로 안다(TUI 런타임은 crossterm 의 Resize 를
/// 다시 그리기 신호로만 쓰고 이벤트로 흘려보내지 않는다). 매번 보내면 30Hz 루프가
/// 그때마다 서버를 재배치시키고, 서버는 그 결과를 **같은 세션의 모든 클라**에게 보낸다 —
/// 아무것도 안 바뀐 프레임으로 남의 화면까지 깜빡이게 하는 셈이다.
///
/// 클램프한 뒤에 비교하는 이유: 아주 작은 창에서는 서로 다른 터미널 크기가 같은 클램프
/// 값으로 접힌다. 클램프 전 값으로 비교하면 서버가 이미 아는 크기를 계속 다시 보낸다.
#[derive(Debug, Clone)]
pub struct SizeReporter {
    last: (u16, u16),
}

impl SizeReporter {
    /// 핸드셰이크에서 이미 알린 크기로 시작한다.
    pub fn new(cols: u16, rows: u16) -> Self {
        let first = Resize::new(cols, rows);
        Self {
            last: (first.cols, first.rows),
        }
    }

    /// **마지막으로 알린** 크기. 다시 붙을 때 핸드셰이크에 실을 값이다(`reconnect`).
    ///
    /// 왜 필요한가: 새 소켓의 `hello` 에 크기를 실어야 서버가 그 크기로 캔버스를 만든다.
    /// 여기 값을 안 쓰고 짐작하면 다시 붙은 직후 한 프레임이 어긋난 크기로 온다.
    pub fn reported(&self) -> (u16, u16) {
        self.last
    }

    /// 지금 크기를 준다. 알릴 것이 생겼으면 프레임을 돌려준다.
    pub fn update(&mut self, cols: u16, rows: u16) -> Option<Resize> {
        let next = Resize::new(cols, rows);
        if (next.cols, next.rows) == self.last {
            return None;
        }
        self.last = (next.cols, next.rows);
        Some(next)
    }
}

/// 뷰포트를 스크롤백 위로 옮긴다. 명령이 아니라 **자기 프레임 종류**다
/// (`serverio` 의 `t == "scroll"` 분기 — `_CMD_TABLE` 에는 없다).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Scroll {
    pub t: &'static str,
    /// 대상 패널. 없으면 서버가 활성 패널로 보낸다.
    ///
    /// 휠은 **커서 아래 패널**을 채워 보낸다(파이썬 클라도 같다). 그러려면 화면 좌표를
    /// 캔버스 좌표로 옮겨야 하는데, 캔버스의 시작 행은 그때그때 붙는 크롬 줄(끊김·오류
    /// 알림)에 따라 달라진다 — 그래서 그 값은 계산하지 않고 **렌더가 남긴 것**을 쓴다
    /// (`SessionView::canvas_top`). 키로 하는 스크롤은 활성 패널이므로 비운다.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pane: Option<i64>,
    /// 과거 방향이 +(서버 `Pane.scroll_by` 와 같은 부호).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delta: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub top: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bottom: Option<bool>,
}

impl Scroll {
    fn new() -> Self {
        Self {
            t: "scroll",
            pane: None,
            delta: None,
            top: None,
            bottom: None,
        }
    }

    /// 상대 이동. 서버는 스크롤백 길이로 클램프한다.
    pub fn by(delta: i32) -> Self {
        Self {
            delta: Some(delta),
            ..Self::new()
        }
    }

    /// 스크롤백 맨 위.
    pub fn top() -> Self {
        Self {
            top: Some(true),
            ..Self::new()
        }
    }

    /// 라이브(맨 아래) 복귀.
    pub fn bottom() -> Self {
        Self {
            bottom: Some(true),
            ..Self::new()
        }
    }

    /// 대상 패널을 지정한다. `None` 이면 서버가 활성 패널로 보낸다.
    pub fn for_pane(mut self, pane: Option<i64>) -> Self {
        self.pane = pane;
        self
    }
}

/// 키 입력을 패널로 보낸다. 명령과 프레임 종류가 다르다.
#[derive(Debug, Clone, Serialize)]
pub struct Input {
    pub t: &'static str,
    /// **base64 문자열**이다. 서버는 `base64.b64decode(msg["data"])` 로 읽으므로
    /// (`serverio._handle_input`) 바이트열을 JSON 배열로 보내면 그 입력이 버려진다 —
    /// 종전 이 타입은 `Vec<u8>` 이었고 serde 기본 직렬화가 정확히 그 배열을 만들었다
    /// (아직 아무도 입력을 안 보내던 시기라 드러나지 않았다. 서버 쪽도 같은 CL 에서
    /// TypeError 가드를 넣었다 — 그 전에는 그 프레임 하나가 연결을 죽였다).
    pub data: String,
    /// 대상 패널. 서버는 없으면 활성 패널로 보낸다 — 파이썬 클라는 항상 실어 보내고
    /// (팝업 라우팅 때문) 우리는 활성 패널을 서버 판단에 맡긴다.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pane: Option<i64>,
    /// 마우스 패스스루 표시. 서버는 이 플래그가 서면 바이트를 **그 패널 PTY 에만** 쓰고
    /// 나머지 부수효과를 전부 건너뛴다(`serverio._handle_input`).
    ///
    /// 건너뛰는 것이 요점이다: 입력 동기화(sync 켠 창의 모든 패널에 같은 입력) · 프롬프트
    /// 추적(Claude 헤더) · 스크롤 중이면 live 복귀. 마우스는 **위치 기반**이라 이 셋 중
    /// 어느 것도 뜻이 없고, 특히 sync 창에서는 한 번의 클릭이 모든 패널에 뿌려진다.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mouse: Option<bool>,
}

impl Input {
    pub fn new(data: impl AsRef<[u8]>) -> Self {
        Self {
            t: "input",
            data: b64_encode(data.as_ref()),
            pane: None,
            mouse: None,
        }
    }

    /// 특정 패널로(활성 패널을 바꾸지 않는다).
    pub fn to_pane(pane: i64, data: impl AsRef<[u8]>) -> Self {
        Self {
            t: "input",
            data: b64_encode(data.as_ref()),
            pane: Some(pane),
            mouse: None,
        }
    }

    /// 마우스 리포트를 그 패널 안 프로그램에게. **패널을 반드시 지목한다** — 마우스는
    /// 위치 기반이라 "활성 패널"이라는 폴백이 뜻을 잃는다(커서는 다른 패널 위에 있다).
    pub fn mouse(pane: i64, data: impl AsRef<[u8]>) -> Self {
        Self {
            t: "input",
            data: b64_encode(data.as_ref()),
            pane: Some(pane),
            mouse: Some(true),
        }
    }
}

/// 표준 base64(패딩 포함). 의존성을 늘리지 않으려고 직접 쓴다 — 입력 프레임 하나에
/// crate 를 더 붙일 이유가 없고, 이 표는 RFC 4648 그대로다.
fn b64_encode(raw: &[u8]) -> String {
    const TABLE: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((raw.len() + 2) / 3 * 4);
    for chunk in raw.chunks(3) {
        let b = [
            chunk[0],
            chunk.get(1).copied().unwrap_or(0),
            chunk.get(2).copied().unwrap_or(0),
        ];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        out.push(TABLE[(n >> 18) as usize & 63] as char);
        out.push(TABLE[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 {
            TABLE[(n >> 6) as usize & 63] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            TABLE[n as usize & 63] as char
        } else {
            '='
        });
    }
    out
}

/// 표준 base64 디코드(패딩 포함, 공백 무시). 못 읽으면 `None`.
///
/// 인코더의 짝이다 — OSC 52 로 온 클립보드 본문을 푸는 데 쓴다(pytmux-420 ①).
/// ⛔ **관대하게 굴지 않는다.** 표 밖 글자·어긋난 길이는 「잘렸거나 우리 것이 아니다」는
/// 뜻이고, 그것을 억지로 풀면 «다른 글»이 사용자의 클립보드에 앉는다.
pub(crate) fn b64_decode(text: &str) -> Option<Vec<u8>> {
    fn val(c: u8) -> Option<u32> {
        match c {
            b'A'..=b'Z' => Some((c - b'A') as u32),
            b'a'..=b'z' => Some((c - b'a') as u32 + 26),
            b'0'..=b'9' => Some((c - b'0') as u32 + 52),
            b'+' => Some(62),
            b'/' => Some(63),
            _ => None,
        }
    }
    let body: Vec<u8> = text
        .bytes()
        .filter(|b| !b.is_ascii_whitespace())
        .collect();
    if body.is_empty() || body.len() % 4 != 0 {
        return None;
    }
    let mut out = Vec::with_capacity(body.len() / 4 * 3);
    for quad in body.chunks(4) {
        let pad = quad.iter().rev().take_while(|&&c| c == b'=').count();
        if pad > 2 {
            return None;
        }
        let mut n = 0u32;
        for (i, &c) in quad.iter().enumerate() {
            let v = if c == b'=' {
                // 패딩은 **마지막 조각의 꼬리에서만** 합법이다.
                if i < 4 - pad {
                    return None;
                }
                0
            } else {
                val(c)?
            };
            n = (n << 6) | v;
        }
        out.push((n >> 16) as u8);
        if pad < 2 {
            out.push((n >> 8) as u8);
        }
        if pad < 1 {
            out.push(n as u8);
        }
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_round_trips_and_refuses_garbage() {
        for s in ["a", "ab", "abc", "가나다", "여러 줄\n둘째"] {
            let enc = b64_encode(s.as_bytes());
            assert_eq!(b64_decode(&enc).as_deref(), Some(s.as_bytes()), "{s:?}");
        }
        // 잘린 것(길이 어긋남) · 표 밖 글자 · 가운데 패딩은 전부 거절한다.
        assert!(b64_decode("QUJD*").is_none());
        assert!(b64_decode("QUJ").is_none());
        assert!(b64_decode("Q=JD").is_none());
        assert!(b64_decode("").is_none());
    }

    #[test]
    fn frames_carry_the_kind_and_the_action_name() {
        let frame = Command::SelectWindow { index: 3, wid: None }.to_frame();
        assert_eq!(frame["t"], "cmd");
        assert_eq!(frame["action"], "select_window");
        assert_eq!(frame["index"], 3);
        // wid 를 모르면 **키를 아예 안 싣는다** — 구서버에 가는 프레임이 종전과 같다.
        assert!(frame.get("wid").is_none(), "wid 가 없는데 키가 실렸다: {frame}");
    }

    #[test]
    fn search_frames_match_the_python_wire_shape() {
        // ★ G9t — 서버 `_cmd_search` 는 query(새 검색)와 direction 을 읽는다.
        //   반복(`n`/`N`)은 **query 칸 자체가 없어야** 한다 — null 을 실으면
        //   `if query:` 는 통과하지만 모양이 파이썬 클라와 달라진다.
        let fresh = Command::Search { query: Some("에러".into()), down: false }.to_frame();
        assert_eq!(fresh["action"], "search");
        assert_eq!(fresh["query"], "에러");
        assert_eq!(fresh["direction"], "up");
        let repeat = Command::Search { query: None, down: true }.to_frame();
        assert_eq!(repeat["direction"], "down");
        assert!(repeat.get("query").is_none(), "반복에 query 칸이 실렸다: {repeat}");
    }

    /// `all()` 이 변형을 빠뜨리지 못하게 하는 가드.
    ///
    /// 이름이 **맞는지**는 여기서 안 본다 — 그건 서버 표와 대조할 일이고
    /// `tests/command_conformance.rs` 가 한다. 여기서 지키는 것은 "전수 순회의 전수"다.
    /// 새 변형은 아래 match 에서 컴파일이 막히고(와일드카드 없음), 그다음 개수 단언이
    /// `all()` 에 넣는 것까지 강제한다.
    fn variant_index(cmd: &Command) -> usize {
        match cmd {
            Command::SelectWindow { .. } => 0,
            Command::NextWindow => 1,
            Command::PrevWindow => 2,
            Command::LastWindow => 3,
            Command::NewWindow { .. } => 4,
            Command::KillWindow => 5,
            Command::MoveCurrentTab { .. } => 6,
            Command::MoveWindow { .. } => 59,
            Command::RenameWindow { .. } => 7,
            Command::Split { .. } => 8,
            Command::SelectPaneId { .. } => 9,
            Command::KillPane => 10,
            Command::ResizeSplit { .. } => 11,
            Command::Paste { .. } => 12,
            Command::CopyRange { .. } => 13,
            Command::SetBuffer { .. } => 14,
            Command::RequestRedraw => 15,
            Command::Zoom => 16,
            Command::CyclePane => 17,
            Command::LastPane => 18,
            Command::CycleLayout => 19,
            Command::Rotate { .. } => 20,
            Command::SwapPane { .. } => 21,
            Command::BreakPane => 22,
            Command::SelectPaneDir { .. } => 23,
            Command::ResizeDir { .. } => 24,
            Command::TogglePin { .. } => 25,
            Command::PasteBuffer { .. } => 26,
            Command::RequestTree => 27,
            Command::RequestBuffers => 28,
            Command::PluginOpen { .. } => 65,
            // `plugin_cmd` 도 아래 두 줄과 같은 부류다(이름이 곧 명령 · `all()` 에 없다) —
            // 종전에는 홀로 70 을 쥐고 있었는데 그 자리는 `all()` 이 안 훑는 칸이라
            // **`VARIANT_COUNT` 밖으로 새는 값**이었다. 같은 자리에 접었다(pytmux-3 에서
            // 70 이 진짜 변형의 자리가 되면서 드러났다).
            Command::PluginCmd { .. } => 65,
            Command::PluginAction { .. } => 66,
            Command::PluginOverlay { .. } => 67,
            Command::PluginOverlayAction { .. } => 68,
            Command::ClientFact { .. } => 69,
            Command::ToggleSync { .. } => 29,
            Command::ToggleMonitor { .. } => 30,
            Command::ToggleAutoRename { .. } => 31,
            Command::ToggleBorderStatus => 39,
            Command::SetOption(..) => 40,
            Command::ClearHistory => 41,
            Command::SwapTab { .. } => 43,
            Command::MoveTab { .. } => 58,
            Command::CapturePane { .. } => 51,
            Command::PipePane { .. } => 52,
            Command::JoinPane { .. } => 53,
            Command::PopupOpen { .. } => 56,
            Command::PopupClose => 57,
            Command::RequestVersion => 54,
            Command::RequestRestartCheck => 55,
            Command::SetEnum { .. } => 50,
            Command::SelectLayout { .. } => 45,
            Command::SaveTabLayout { .. } => 46,
            Command::LoadTabLayout { .. } => 47,
            Command::SaveLayout => 48,
            Command::RestoreLayout => 49,
            Command::SetPinned { .. } => 44,
            Command::RespawnPane => 42,
            Command::KillServer => 32,
            Command::RestartServer => 33,
            Command::RemoteAttach { .. } => 34,
            Command::RemoteNewTab { .. } => 35,
            Command::RemoteDetach { .. } => 36,
            Command::SetPluginEnabled { .. } => 37,
            Command::SetPaneTitle { .. } => 38,
            Command::JumpPrompt { .. } => 60,
            Command::SetAutoresume => 61,
            Command::SetPromptClear => 62,
            Command::Search { .. } => 63,
            Command::SetCapture => 64,
            Command::SearchAll { .. } => 71,
            Command::SearchGoto { .. } => 72,
            Command::DebugStats => 73,
            // 플러그인 액션은 **이름이 곧 명령**이라 변형 하나에 여러 이름이 실린다 —
            // 자리는 하나면 충분하다(이 표는 "변형을 빠짐없이 훑었나"를 재는 것이다).
            Command::PluginToggle { .. } => 65,
            Command::PluginDo { .. } => 66,
            Command::RenameSession { .. } => 70,
        }
    }

    /// `variant_index` 가 돌려주는 값의 가짓수. 변형을 늘리면 여기도 늘려야 한다.
    const VARIANT_COUNT: usize = 74;

    #[test]
    fn all_covers_every_variant() {
        let mut seen = vec![false; VARIANT_COUNT];
        for cmd in Command::all() {
            let i = variant_index(&cmd);
            assert!(!seen[i], "{cmd:?} 가 all() 에 두 번 들어 있다");
            seen[i] = true;
        }
        let missing: Vec<usize> = (0..VARIANT_COUNT).filter(|&i| !seen[i]).collect();
        assert!(
            missing.is_empty(),
            "all() 에 빠진 변형이 있다(variant_index {missing:?}) — \
             빠진 변형은 서버 명령 표와 대조되지 않는다"
        );
    }

    #[test]
    fn the_frame_action_is_the_action_name() {
        // 두 경로가 갈라지면 와이어에 나가는 이름과 클라가 아는 이름이 달라진다.
        for cmd in Command::all() {
            assert_eq!(cmd.to_frame()["action"], cmd.action(), "{cmd:?}");
        }
    }

    #[test]
    fn new_window_carries_a_start_path() {
        // 파이썬 클라도 new_window/split 에 path 를 항상 실어 보낸다. 빠지면 서버가
        // 기본값을 쓰긴 하지만, 설정된 시작 디렉토리가 무시된다.
        let frame = Command::NewWindow {
            path: "current".into(),
            cmd: None,
        }
        .to_frame();
        assert_eq!(frame["path"], "current");
        // 셸 탭에는 `cmd` 칸이 **아예 없다**(위 `to_frame` 의 이유).
        assert!(frame.get("cmd").is_none());
    }

    #[test]
    fn a_claude_tab_keeps_its_own_directory_and_takes_the_configured_command() {
        // ★ **이 오라클이 pytmux-137 의 요구 그 자체다.**
        //
        // `default-path home` 이 걸려 있어도 Claude 탭은 **지금 패널의 디렉토리**에서
        // 뜬다. 그 예외를 안 두면 설정이 요구를 조용히 덮는데, 증상은 "열리긴 하는데
        // 엉뚱한 디렉토리"라 키가 안 먹은 것처럼 보이지도 않는다.
        let claude = action_to_command(base::Action::NewClaudeTab).unwrap();
        assert_eq!(
            with_config_paths(claude, "home", "my-claude --resume"),
            Command::NewWindow {
                path: "current".into(),
                cmd: Some("my-claude --resume".into()),
            }
        );
    }

    #[test]
    fn a_plain_new_tab_and_split_still_obey_default_path() {
        // ⛔ 위 예외가 **옆자리까지 먹지 않았는지** 같이 잰다 — 한 줄로 둘을 고치면
        // 설정을 켜 둔 사람의 새 탭·분할이 조용히 자리를 옮긴다.
        let tab = action_to_command(base::Action::NewTab).unwrap();
        assert_eq!(
            with_config_paths(tab, "home", "claude"),
            Command::NewWindow {
                path: "home".into(),
                cmd: None,
            }
        );
        let split = action_to_command(base::Action::SplitTopBottom).unwrap();
        assert_eq!(
            with_config_paths(split, "home", "claude"),
            Command::Split {
                horizontal: false,
                path: "home".into(),
            }
        );
    }

    #[test]
    fn an_empty_claude_command_is_a_plain_shell_tab() {
        // 설정을 비워 둔 사람에게는 그냥 새 탭이다 — 서버가 빈 `cmd` 를 없는 것으로
        // 본다(`servertree.new_window`). 여기서 `None` 으로 접지 않는 이유는, 접으면
        // 「설정이 비었다」와 「이 명령은 셸 탭이다」가 구별되지 않기 때문이다.
        let claude = action_to_command(base::Action::NewClaudeTab).unwrap();
        assert_eq!(
            with_config_paths(claude, "home", ""),
            Command::NewWindow {
                path: "current".into(),
                cmd: Some(String::new()),
            }
        );
    }

    #[test]
    fn a_claude_tab_carries_the_command() {
        // `esc c` — 지금 디렉토리(`current`)에서 그 명령이 도는 새 탭(pytmux-137).
        let frame = Command::NewWindow {
            path: "current".into(),
            cmd: Some("claude".into()),
        }
        .to_frame();
        assert_eq!(frame["path"], "current");
        assert_eq!(frame["cmd"], "claude");
    }

    #[test]
    fn remote_tabs_use_the_same_index_space() {
        // 원격 탭도 전역 index 로 선택한다 — 별도 명령이 없다.
        let frame = Command::SelectWindow { index: 7, wid: None }.to_frame();
        assert_eq!(frame["index"], 7);
    }

    #[test]
    fn paste_carries_the_text_verbatim_without_bracketed_markers() {
        // 마커를 클라가 붙이면 안 된다 — 감싸도 되는지는 **패널 안에서 도는 프로그램**이
        // 정하고(DECSET 2004) 그 상태는 서버만 안다. 안 켠 프로그램에 보내면 마커가
        // 글자로 찍히고, 두 번 감싸면 안쪽 마커가 본문으로 들어간다.
        let frame = Command::Paste {
            text: "첫 줄\n둘째 줄\n".into(),
        }
        .to_frame();
        assert_eq!(frame["action"], "paste");
        assert_eq!(frame["text"], "첫 줄\n둘째 줄\n");
        let raw = frame.to_string();
        assert!(!raw.contains("200~"), "클라가 bracketed 마커를 붙였다: {raw}");
        assert!(!raw.contains("201~"), "클라가 bracketed 마커를 붙였다: {raw}");
    }

    #[test]
    fn resize_is_only_reported_when_it_actually_changed() {
        // 매 프레임 보내면 서버가 매번 재배치하고 그 결과를 **같은 세션의 모든 클라**에게
        // 보낸다 — 안 바뀐 화면으로 남의 화면까지 깜빡이게 한다.
        let mut reporter = SizeReporter::new(80, 24);
        assert_eq!(reporter.update(80, 24), None, "핸드셰이크 크기를 다시 보냈다");
        let frame = reporter.update(100, 30).expect("바뀐 크기를 안 알렸다");
        assert_eq!((frame.t, frame.cols, frame.rows), ("resize", 100, 30));
        assert_eq!(reporter.update(100, 30), None, "같은 크기를 두 번 알렸다");
    }

    #[test]
    fn sizes_are_compared_after_clamping_not_before() {
        // 아주 작은 창에서는 서로 다른 터미널 크기가 같은 클램프 값으로 접힌다.
        // 클램프 전 값으로 비교하면 서버가 이미 아는 크기를 계속 다시 보낸다.
        let mut reporter = SizeReporter::new(1, 1);
        assert_eq!(reporter.update(2, 2), None, "클램프하면 같은 크기다");
        let frame = Resize::new(0, 9999);
        assert_eq!((frame.cols, frame.rows), (crate::message::MIN_W, crate::message::MAX_H));
    }

    #[test]
    fn every_outgoing_kind_carries_its_own_frame_type() {
        // 종류를 아는 곳이 여기 하나다. 한 종류가 다른 `t` 로 나가면 서버는 그 프레임을
        // 조용히 무시한다(모르는 `t` 는 아무 분기도 안 탄다).
        let frames = [
            Outgoing::Command(Command::NextWindow).to_frame(),
            Outgoing::Input(b"x".to_vec()).to_frame(),
            Outgoing::Scroll(Scroll::by(1)).to_frame(),
            Outgoing::Resize(Resize::new(80, 24)).to_frame(),
            Outgoing::Focus { on: true }.to_frame(),
        ];
        let kinds: Vec<&str> = frames.iter().map(|f| f["t"].as_str().unwrap()).collect();
        assert_eq!(kinds, vec!["cmd", "input", "scroll", "resize", "focus"]);
    }

    /// 포커스 프레임의 **모양**(pytmux-421). 서버는 `msg.get("on")` 한 칸만 읽는다 —
    /// 이름이나 타입이 어긋나면 서버가 `bool(None)` = `False` 로 읽어, 창이 포커스를
    /// 얻어도 **영원히 blur** 다. 조용한 오답이라 눈으로 못 찾는다.
    #[test]
    fn focus_frames_say_which_way_the_focus_went() {
        let on = Outgoing::Focus { on: true }.to_frame();
        assert_eq!((on["t"].as_str(), on["on"].as_bool()), (Some("focus"), Some(true)));
        let off = Outgoing::Focus { on: false }.to_frame();
        assert_eq!(off["on"].as_bool(), Some(false));
    }

    #[test]
    fn scroll_frames_carry_only_the_field_that_applies() {
        // 서버는 bottom → top → delta 순으로 본다. 셋을 같이 실으면 뜻이 겹친다.
        let by = serde_json::to_value(Scroll::by(-3)).unwrap();
        assert_eq!(by["delta"], -3);
        assert!(by.get("top").is_none() && by.get("bottom").is_none(), "{by}");
        let bottom = serde_json::to_value(Scroll::bottom()).unwrap();
        assert_eq!(bottom["bottom"], true);
        assert!(bottom.get("delta").is_none(), "{bottom}");
        assert!(bottom.get("pane").is_none(), "활성 패널은 서버가 정한다");
    }

    #[test]
    fn input_is_a_different_frame_kind_than_commands() {
        // 2026-07-27g 갱신: 종전 이 테스트는 `data` 가 **JSON 배열**임을 단언해
        // 결함을 못박고 있었다 — 서버는 base64 문자열을 요구한다
        // (`serverio._handle_input` 의 `base64.b64decode`). 자기 구현을 자기가
        // 확인하면 이렇게 된다(엔드포인트 픽스처가 잡아낸 것과 같은 부류).
        let frame = serde_json::to_value(Input::new(b"ls\n")).unwrap();
        assert_eq!(frame["t"], "input");
        assert_eq!(frame["data"], "bHMK");
    }
}

#[cfg(test)]
mod input_tests {
    use super::*;

    /// 서버 계약: `data` 는 **base64 문자열**이다(`serverio._handle_input` 이
    /// `base64.b64decode` 로 읽는다). 배열로 보내면 그 입력이 조용히 버려진다.
    #[test]
    fn input_frame_carries_base64_text_not_a_byte_array() {
        let value = serde_json::to_value(Input::new("echo hi\n")).unwrap();
        assert_eq!(value["t"], "input");
        assert_eq!(value["data"], "ZWNobyBoaQo=");
        assert!(value["data"].is_string(), "배열이면 서버가 못 읽는다: {value}");
        assert!(value.get("pane").is_none(), "활성 패널은 서버가 정한다");
    }

    #[test]
    fn the_popup_line_reads_like_the_python_client() {
        // 파이썬 `clientcmd.py` 의 문법 그대로: `[-w N] [-h N] <cmd>` · `-C` 닫기 ·
        // 모르는 플래그는 조용히 버린다 · 숫자가 아닌 값은 값도 플래그도 버린다.
        use super::{PopupLine, parse_popup_line};
        assert_eq!(
            parse_popup_line("-w 40 -h 10 top -d"),
            PopupLine::Open(Command::PopupOpen {
                cmd: "top".into(),
                title: "top".into(),
                w: Some(40),
                h: Some(10),
            })
        );
        assert_eq!(parse_popup_line("git log -C"), PopupLine::Close);
        assert_eq!(parse_popup_line("   "), PopupLine::Usage);
        assert_eq!(
            parse_popup_line("-w abc htop"),
            PopupLine::Open(Command::PopupOpen {
                cmd: "htop".into(),
                title: "htop".into(),
                w: None,
                h: None,
            })
        );
    }

    #[test]
    fn popup_open_puts_wants_on_the_wire_only_when_present() {
        // 없는 희망값은 칸을 아예 안 싣는다 — 서버 `msg.get("w")` 의 None 과 같은 뜻.
        let bare = Command::PopupOpen {
            cmd: "top".into(),
            title: "top".into(),
            w: None,
            h: None,
        }
        .to_frame();
        assert!(bare.get("w").is_none(), "{bare}");
        assert!(bare.get("h").is_none(), "{bare}");
        let sized = Command::PopupOpen {
            cmd: "top".into(),
            title: "top".into(),
            w: Some(40),
            h: Some(10),
        }
        .to_frame();
        assert_eq!(sized["w"], 40);
        assert_eq!(sized["h"], 10);
        assert_eq!(sized["action"], "popup_open");
    }

    #[test]
    fn base64_matches_the_reference_table_including_padding() {
        // 패딩 경계(길이 %3 = 0·1·2) — 한 자리만 틀려도 입력이 깨져 나간다.
        for (raw, want) in [
            ("", ""),
            ("f", "Zg=="),
            ("fo", "Zm8="),
            ("foo", "Zm9v"),
            ("foob", "Zm9vYg=="),
            ("\u{1b}[A", "G1tB"),
        ] {
            let got = serde_json::to_value(Input::new(raw)).unwrap();
            assert_eq!(got["data"], want, "raw={raw:?}");
        }
        // 비-ASCII(한글)도 UTF-8 바이트 그대로 실린다.
        let ko = serde_json::to_value(Input::new("한")).unwrap();
        assert_eq!(ko["data"], "7ZWc");
    }

    #[test]
    fn explicit_pane_is_carried_when_asked() {
        let value = serde_json::to_value(Input::to_pane(7, "x")).unwrap();
        assert_eq!(value["pane"], 7);
        assert_eq!(value["data"], "eA==");
    }
}


/// core 의 액션을 서버 명령으로 옮긴다.
///
/// 액션은 **의도**이고 명령은 **서버 어휘**다. 이 자리에서만 둘을 잇는다 — 뷰가 서버
/// 명령 이름을 직접 알면 GUI·TUI 가 각자 다른 이름을 쓰기 시작하고, 그 어긋남은
/// "한쪽 클라에서만 안 되는 기능"으로 한참 뒤에 발견된다.
///
/// `None` 은 **서버가 할 일이 없다**는 뜻이지 미구현이 아니다:
/// - `SelectLast` — 마지막 탭의 index 는 탭바를 봐야 안다. 그건 상태를 가진 뷰의 몫이다.
/// - `ToggleExpand`·`ToggleClaudeDetail` — 클라 안에서만 끝나는 화면이다.
/// - `EnterScroll` — 모드 전이라 `keys::interpret` 에서 이미 끝난다.
/// - `Quit` — 클라가 죽는 것이지 서버에 시킬 일이 아니다.
/// 팔레트가 이름 옆에 보여 줄 명령 설명(파이썬 `clientutil.COMMANDS` 의 desc).
///
/// 출처는 패리티 픽스처다 — 정본에서 뽑은 것을 그대로 쓰므로(로드맵 원칙 1) 문구가
/// 파이썬과 어긋날 수 없다. 픽스처가 낡으면 `gen_client_surface_fixture.py` 로 다시
/// 뽑는다. 없는 이름(우리 전용 별칭 등)은 `None` — 뷰가 설명 없이 이름만 그린다.
pub fn command_help(name: &str) -> Option<&'static str> {
    use std::collections::HashMap;
    use std::sync::OnceLock;
    static MAP: OnceLock<HashMap<String, String>> = OnceLock::new();
    let map = MAP.get_or_init(|| {
        #[derive(serde::Deserialize)]
        struct Fx {
            #[serde(default)]
            command_help: HashMap<String, String>,
        }
        serde_json::from_str::<Fx>(include_str!("../tests/fixtures/client_surface.json"))
            .map(|fx| fx.command_help)
            .unwrap_or_default()
    });
    // 팔레트 이름이 `split-window -h` 처럼 플래그를 품기도 한다 — 기본형으로 찾는다.
    let base = name.split(' ').next().unwrap_or(name);
    map.get(name).or_else(|| map.get(base)).map(String::as_str)
}

/// `display-popup` 물음의 대답 한 줄이 뜻하는 것.
#[derive(Debug, Clone, PartialEq)]
pub enum PopupLine {
    /// 이 명령으로 팝업을 연다.
    Open(Command),
    /// `-C` — 떠 있는 팝업을 닫는다.
    Close,
    /// 명령이 없다 — 사용법을 보일 자리다.
    Usage,
}

/// `display-popup` 대답을 파이썬 클라와 **같은 문법**으로 읽는다
/// (`clientcmd.py` — `[-w N] [-h N] <command>` · `-C` 는 닫기 · 모르는 `-플래그` 는
/// 조용히 버린다). 두 뷰가 각자 읽으면 같은 줄이 클라마다 다른 팝업이 된다.
pub fn parse_popup_line(line: &str) -> PopupLine {
    let args: Vec<&str> = line.split_whitespace().collect();
    if args.iter().any(|a| *a == "-C") {
        return PopupLine::Close;
    }
    let mut want_w = None;
    let mut want_h = None;
    let mut cmd_parts: Vec<&str> = Vec::new();
    let mut skip = false;
    for (i, a) in args.iter().enumerate() {
        if skip {
            skip = false;
            continue;
        }
        if (*a == "-w" || *a == "-h") && i + 1 < args.len() {
            skip = true;
            // 숫자가 아니면 값도 플래그도 버린다(파이썬 `isdigit` 과 같은 관용).
            if let Ok(value) = args[i + 1].parse::<u32>() {
                if *a == "-w" {
                    want_w = Some(value);
                } else {
                    want_h = Some(value);
                }
            }
            continue;
        }
        if a.starts_with('-') {
            continue;
        }
        cmd_parts.push(a);
    }
    let cmd = cmd_parts.join(" ");
    if cmd.is_empty() {
        return PopupLine::Usage;
    }
    // 제목은 파이썬처럼 명령 앞 40자다.
    let title: String = cmd.chars().take(40).collect();
    PopupLine::Open(Command::PopupOpen { cmd, title, w: want_w, h: want_h })
}

/// 탭 목록을 봐야 정해지는 액션까지 옮긴다 — **두 뷰가 부르는 한 곳**이다.
///
/// # 왜 따로 두나
///
/// [`action_to_command`] 는 상태를 모른다. 그런데 탭을 고르는 액션 셋(`SelectTab` ·
/// `SelectFirst` · `SelectLast`)은 **탭바를 봐야** 무엇을 보낼지 정해진다:
///
/// - `SelectTab(n)` 의 `n` 은 사용자가 화면에서 읽은 **표시 번호**(1-based, 시각 순서)다.
///   그것을 index 로 그대로 쓰면 **한 칸 밀린다**(번호 3 = index 2). 종전 판이 그랬고,
///   고정 탭이 섞이면 더 크게 어긋났다.
/// - 어느 경우든 그 탭의 `wid` 를 함께 실어야 레이스에서 옆 탭이 열리지 않는다
///   ([`Command::SelectWindow`]).
///
/// 종전에는 뷰마다 `SelectLast` 만 따로 처리하고 나머지는 [`action_to_command`] 로
/// 흘렸다 — 두 뷰가 각자 다른 만큼만 고쳐 갈라질 자리다(이 저장소가 이미 몇 번 밟았다).
pub fn action_to_command_with_tabs(
    action: base::Action,
    tabs: &crate::tabs::TabBar,
) -> Option<Command> {
    use base::Action;
    let select = |tab: Option<&crate::tabs::Tab>| {
        tab.map(|t| Command::SelectWindow { index: t.index, wid: t.wid })
    };
    match action {
        Action::SelectTab(n) => select(tabs.by_number(n as usize)),
        // 첫/마지막도 **시각 순서**다 — 고정 탭은 오른쪽 구역으로 밀리므로 리스트의
        // 첫/끝과 화면의 첫/끝이 다를 수 있다.
        Action::SelectFirst => select(tabs.by_number(1)),
        Action::SelectLast => select(tabs.by_number(tabs.tabs.len())),
        // ★ 고정도 **탭바를 봐야 한다**(§10-21ⓒ3). 서버는 자리를 안 실으면
        //   `sess.active_index`(= 로컬 탭의 자리)로 접는데, 원격(병합) 탭이 활성일 때
        //   그 값은 보고 있는 탭이 아니다 — 토글이 엉뚱한 로컬 탭에 걸리고, 사용자에겐
        //   "원격 탭은 핀이 안 된다"로 보인다. 정본이 같은 자리에 그 함정을 적어 뒀다.
        Action::TogglePin => Some(Command::TogglePin {
            index: tabs.active().map(|t| t.index),
        }),
        other => action_to_command(other),
    }
}

/// 새 탭·분할이 **설정값**으로 시작하게 자리와 명령을 갈아 끼운다.
///
/// [`action_to_command`] 는 설정을 모른다(proto 는 `Config` 를 안 읽는다) — `current` ·
/// `claude` 같은 **기본값**을 박아 돌려주고, 그것을 여기서 한 번에 갈아 끼운다.
///
/// # 왜 뷰가 아니라 여기인가
///
/// 종전에는 이 규칙이 뷰 안에 있었다. 규칙이 뷰 안에 있으면 **뷰가 늘 때마다 각자 적고**,
/// 한쪽만 고치면 같은 키가 클라마다 다른 디렉토리에서 뜬다. 규칙을 여기 두면 기계가 잰다.
///
/// # 갈래 셋
///
/// - **Claude 탭**(`cmd` 가 실린 새 탭 · `esc c` · pytmux-137): 자리는 **안 갈고**
///   (`current` 를 지킨다) 명령만 `claude_command` 로 갈아 끼운다. 이 키의 값은
///   「지금 디렉토리에서 바로 붙는다」에 있어서, `default-path home` 이 그것을 조용히
///   덮으면 요구가 안 지켜진다.
/// - **평범한 새 탭·분할**: 자리를 `default_path` 로 간다(종전 그대로).
/// - **나머지**: 그대로 돌려준다.
pub fn with_config_paths(
    command: Command,
    default_path: &str,
    claude_command: &str,
) -> Command {
    match command {
        Command::NewWindow { path, cmd: Some(_) } => Command::NewWindow {
            path,
            cmd: Some(claude_command.to_owned()),
        },
        Command::NewWindow { .. } => Command::NewWindow {
            path: default_path.to_owned(),
            cmd: None,
        },
        Command::Split { horizontal, .. } => Command::Split {
            horizontal,
            path: default_path.to_owned(),
        },
        other => other,
    }
}

/// 상태를 안 보는 액션→명령. 탭을 고르는 셋은 여기서 `None` 이다 —
/// [`action_to_command_with_tabs`] 를 쓸 것.
pub fn action_to_command(action: base::Action) -> Option<Command> {
    use base::Action;
    match action {
        Action::SelectNext => Some(Command::NextWindow),
        Action::SelectPrev => Some(Command::PrevWindow),
        // ↓ 셋 다 탭바를 봐야 한다(`action_to_command_with_tabs`). 여기서 index 를
        //   짐작해 돌려주면 **틀린 탭을 고르고도 조용하다** — 그래서 None 이다.
        Action::SelectFirst => None,
        Action::SelectTab(_) => None,
        Action::SelectLast => None,
        Action::ToggleExpand => None,
        Action::ToggleClaudeDetail => None,
        // 스크롤 모드 전이는 **뷰의 모드 상태**다(서버는 모드를 모른다). 나갈 때 라이브
        // 맨 아래로 되돌리는 `scroll` 프레임은 뷰가 따로 보낸다.
        Action::EnterScroll => None,
        // 프롬프트 점프는 **서버가 계산한다**(claude-code 플러그인 `claude_jump_prompt`
        // 가 스크롤백의 턴 경계를 찾는다) — 클라는 스크롤백을 안 갖고 있어 셀 수 없다.
        // 방향의 철자는 서버 어휘이므로 여기서 옮긴다.
        Action::JumpPrompt { up } => Some(Command::JumpPrompt {
            direction: if up { "up" } else { "down" },
        }),
        Action::ToggleAutoresume => Some(Command::SetAutoresume),
        Action::TogglePromptClear => Some(Command::SetPromptClear),
        // 반복은 곧장 명령이다 — 검색어는 서버가 기억한다(`Pane.search_query`).
        Action::SearchAgain { down } => Some(Command::Search { query: None, down }),
        // 물음을 여는 쪽 — 대답이 명령이 된다(`apply_answer`). 여기서 낼 것이 없다.
        Action::SearchScrollback => None,
        // 이 액션도 화면(물음)을 여는 것이라 명령이 아니다 — `SearchAll` 명령은
        // 그 물음이 대답을 받은 뒤에야 나간다(§apply_answer 의 `answered()`).
        Action::SearchAll => None,
        // 로케일은 per-user 라 서버가 알 일이 없다(파이썬도 서버 opts 를 안 건드린다).
        // 적용·영속은 뷰가 한다(`base::i18n`).
        Action::SetLang(_) => None,
        // 재접속은 **소켓을 다시 세우는 일**이라 서버에 보낼 것이 없다 — 오히려 지금
        // 소켓이 막혀 있어서 부르는 것이다(그 위로 무엇을 보내도 안 나간다).
        Action::Reconnect => None,
        // 전체 재시작은 **드라이런을 먼저** 지나야 한다(`base::restart`). 여기서 바로
        // `restart_server` 를 돌려주면 점검을 건너뛰게 되고, 그건 되돌릴 수 없다.
        Action::RestartAll => None,
        // 창을 전체 화면으로 넣는 것은 **이 클라의 창** 일이다 — 서버는 창이 없다
        // (§10-21ⓘ3 · 허용되는 갈림 ⓒ OS 창 통합).
        Action::ToggleFullscreen => None,
        Action::Quit => None,
        // ── prefix 모드(패리티 G1) ──────────────────────────────────────────
        //
        // 분할의 `horizontal` 은 **pytmux 기준**이다(설계문서 §5 — tmux 와 반대). 파이썬
        // 클라의 `%`(좌우)가 서버에는 `horizontal: true` 로 간다.
        Action::SplitLeftRight => Some(Command::Split {
            horizontal: true,
            path: "current".into(),
        }),
        Action::SplitTopBottom => Some(Command::Split {
            horizontal: false,
            path: "current".into(),
        }),
        Action::KillPane => Some(Command::KillPane),
        // 새 탭은 **지금 패널의 디렉토리**에서 연다(파이썬 클라 `prefix c` 와 같다).
        Action::NewTab => Some(Command::NewWindow {
            path: "current".into(),
            cmd: None,
        }),
        // `esc c` — 그 탭에서 Claude Code CLI 가 돈다(pytmux-137). 두 칸 다 **기본값**
        // 이다: `path` 는 뷰가 `default-path` 로 갈아 끼우지 **않고**(이 키의 값이
        // 「지금 디렉토리」에 있다), `cmd` 는 뷰가 `claude-command` 설정으로 갈아
        // 끼운다. 자리를 비워 두지 않는 이유는 `path` 와 같다 — 뷰를 안 지나는
        // 경로(테스트·다른 호출부)에서도 뜻이 통하는 명령이라야 한다.
        Action::NewClaudeTab => Some(Command::NewWindow {
            path: "current".into(),
            cmd: Some("claude".into()),
        }),
        Action::KillTab => Some(Command::KillWindow),
        Action::NextTab => Some(Command::NextWindow),
        Action::PrevTab => Some(Command::PrevWindow),
        Action::LastTab => Some(Command::LastWindow),
        Action::Redraw => Some(Command::RequestRedraw),
        // ── G1b ────────────────────────────────────────────────────────────
        Action::Zoom => Some(Command::Zoom),
        Action::NextPane => Some(Command::CyclePane),
        Action::LastPane => Some(Command::LastPane),
        Action::CycleLayout => Some(Command::CycleLayout),
        Action::RotatePanes => Some(Command::Rotate { forward: true }),
        Action::SwapPane { forward } => Some(Command::SwapPane { forward }),
        Action::BreakPane => Some(Command::BreakPane),
        Action::SelectPane(dir) => Some(Command::SelectPaneDir { dir: dir.into() }),
        // 한 번에 미는 칸 수는 서버 기본값과 같은 3이다(`_cmd_resize_dir`).
        Action::ResizePane(dir) => Some(Command::ResizeDir {
            dir: dir.into(),
            cells: 3,
        }),
        // 자리는 탭바를 봐야 안다 — `action_to_command_with_tabs` 가 채운다.
        Action::TogglePin => Some(Command::TogglePin { index: None }),
        Action::PasteBuffer => Some(Command::PasteBuffer { index: 0 }),
        // ⛔ **여기서 명령이 안 난다** — 무엇을 붙일지는 클립보드를 읽어야 알고, 읽는 데는
        // 창 문맥이 필요하다(글자면 그대로, 그림이면 임시 파일 경로). 뷰가 읽은 뒤에야
        // `paste` 하나가 만들어진다 — 작성창(`ShowCompose`)과 **같은 자리**다.
        Action::PasteClipboard => None,
        // 판을 여는 것은 **클라 안의 일**이다 — 뒤집는 명령(`set_autoresume`)은 그 판
        // 안에서 `a` 를 눌렀을 때 난다(`ToggleAutoresume` 이 이미 그 명령을 든다).
        Action::ShowAutoresume => None,
        // 화면을 여는 것은 **클라 안의 일**이다 — 서버는 이 클라가 무엇을 덮어 보이는지
        // 알 필요가 없다(플랜 화면과 같은 자리).
        Action::ShowKeys | Action::ShowTabs => None,
        // 목록을 **먼저 청해야** 화면에 그릴 것이 생긴다(회신이 오면 상태에 쌓인다).
        // 이름·자리는 **물어봐야** 안다 — 화면이 대답을 받아 그때 명령이 된다(G4).
        Action::RenameTab | Action::MoveTab | Action::ShowCommands | Action::ShowSettings => None,
        // 되돌릴 수 없거나 인자가 필요한 것들 — 화면(확인·입력)을 거쳐야 명령이 된다.
        // 여기서 바로 명령을 돌려주면 키 한 번에 서버가 죽는다.
        // 화면을 여는 액션이라 서버에 시킬 것이 없다.
        Action::ShowPlugins => None,
        // 작성창도 화면이다. 서버로 나가는 것은 **다 쓴 뒤의 `paste`** 하나뿐이고,
        // 그건 화면이 대답을 돌려줄 때 만들어진다(입력 화면과 같은 자리).
        Action::ShowCompose => None,
        // 정보 팝업도 화면이다. 다만 **서버에 물어야 채워지는 탭**이 있어(버전·가동
        // 시간) 뷰가 열면서 `request_version` 을 함께 청한다 — 트리·버퍼와 같은 자리다.
        Action::ShowInfoTabs => None,
        // 오버레이 토글은 **한 명령으로 안 떨어진다** — 뷰가 `push_overlay_toggle` 로
        // `plugin_overlay` 를 (때로 두 개: 닫는 것 + 켜는 것) 보낸다. 한 패널엔 한
        // 오버레이라 서로를 닫기 때문이다. 그림은 다음 `plugin_cells` 프레임이 답이다.
        // 오버레이는 **상태를 보고** 명령이 정해진다(어느 패널인가·무엇이 닫히는가) —
        // 그래서 여기서는 `None` 이고 뷰가 `push_overlay*` 로 만든다.
        Action::ToggleClock
        | Action::ToggleCalendar
        | Action::ToggleUsageView
        | Action::SetOverlay { .. } => None,
        // ★ 플러그인 토글은 **명령이 곧 액션 이름**이다(pytmux-35) — 서버가 처음부터
        //   받고 있던 그 이름을 그대로 친다. 값을 안 실으면 서버가 뒤집는다.
        Action::PluginToggle { action } => Some(Command::PluginToggle { action, value: None }),
        Action::PluginDo { action } => Some(Command::PluginDo { action }),
        // 글자 배율은 **이 창 안의 일**이다 — 서버는 픽셀을 모른다. 다만 배율이 바뀌면
        // 격자(행·열)가 달라지고, 그것은 뷰가 자리표를 다시 재어 `Resize` 로 알린다
        // (`report_size` — 창 크기가 바뀔 때와 같은 길이라 새 명령이 필요 없다).
        Action::FontScale { .. } | Action::FontScaleReset => None,
        // 요약 판은 **클라 안의 것**이다 — 재료(블록·Claude 항목)를 이미 들고 있다.
        Action::ShowSummary => None,
        // 커서 판도 같다(pytmux-375) — 다섯 값의 주인은 **설정 파일**이고 그 파일은
        // 클라가 읽고 쓴다. 서버는 커서를 그리지도 않는다(자리만 프레임으로 준다).
        Action::ShowCursor => None,
        // 이 클라의 런타임을 재는 판이라 서버에 물을 것이 없다(pytmux-457) —
        // 값은 뷰가 자기에게서 모은다.
        Action::ShowDebugStats => None,
        // 블록 고르기도 같다 — 모드 전이라 서버에 보낼 것이 없다(복사할 때에야
        // `CopyRange` 가 나간다. 그 명령은 마우스 드래그 복사와 **같은 하나**다).
        Action::SelectBlocks => None,
        // 패널로 **바이트를 보내는** 것이라 명령이 아니다 — 뷰가 `Outgoing::Input` 으로
        // 흘린다(키 입력과 같은 길).
        Action::SendEscape | Action::SendBacktick => None,
        // 화면(입력·목록·오버레이)을 거쳐야 명령이 된다.
        Action::RenamePane
        | Action::ShowPaneNumbers
        | Action::ShowMenu
        | Action::ShowNotices => None,
        Action::KillServer
        | Action::RestartServer
        | Action::RemoteAttach
        | Action::RemoteNewTab
        | Action::RemoteDetach => None,
        Action::ShowTree => Some(Command::RequestTree),
        Action::ShowBuffers => Some(Command::RequestBuffers),
        Action::ToggleSync => Some(Command::ToggleSync { value: None }),
        Action::ToggleMonitorActivity => Some(Command::ToggleMonitor { which: "activity", value: None }),
        Action::ToggleMonitorBell => Some(Command::ToggleMonitor { which: "bell", value: None }),
        Action::ToggleAutoRename => Some(Command::ToggleAutoRename { value: None }),
        Action::ToggleBorderStatus => Some(Command::ToggleBorderStatus),
        Action::ToggleServerOption(opt) => Some(Command::SetOption(match opt {
            base::ServerOpt::SingleBorder => ServerOption::SingleBorder,
            base::ServerOpt::CoalesceRepaints => ServerOption::CoalesceRepaints,
            base::ServerOpt::ExitEmpty => ServerOption::ExitEmpty,
            base::ServerOpt::NestAutoAttach => ServerOption::NestAutoAttach,
            base::ServerOpt::WinMouseMotion => ServerOption::WinMouseMotion,
        }, None)),
        Action::ClearHistory => Some(Command::ClearHistory),
        Action::SetPinned(on) => Some(Command::SetPinned { index: None, on }),
        Action::SetEnum(opt, value) => Some(Command::SetEnum {
            action: match opt {
                base::EnumOpt::VtParser => "set_vt_parser",
                base::EnumOpt::WindowSize => "set_window_size",
            },
            value,
        }),
        Action::CapturePane(full) => Some(Command::CapturePane { full }),
        Action::RequestVersion => Some(Command::RequestVersion),
        Action::RequestRestartCheck => Some(Command::RequestRestartCheck),
        Action::PopupClose => Some(Command::PopupClose),
        // 명령 문자열을 화면에서 받아야 한다.
        Action::DisplayPopup => None,
        // 대상은 화면에서 고른다.
        Action::MergeRemoteTab => None,
        // 셸은 **클라가 돌린다** — 서버에 시킬 일이 아니다.
        Action::RunShell | Action::IfShell => None,
        Action::SaveLayout => Some(Command::SaveLayout),
        Action::RestoreLayout => Some(Command::RestoreLayout),
        // 프리셋·이름은 화면을 거쳐야 정해진다.
        Action::ShowLayouts | Action::SaveTabLayout | Action::LoadTabLayout(_) => None,
        // 값을 화면에서 받아야 명령이 된다.
        Action::PipePane | Action::JoinPane | Action::DisplayMessage => None,
        // 설정 파일을 다시 읽는 것이라 서버와 무관하다.
        Action::SourceFile => None,
        // 값·키를 화면에서 받아야 한다(`ShowOptions` 는 화면만 연다).
        Action::SetOption | Action::ShowOptions | Action::SendKeys => None,
        // 훅은 **클라 안에서 끝난다** — 서버는 사건을 알려 주지도, 훅을 들고 있지도
        // 않는다(파이썬도 같다. `base::hooks` 모듈 문서 참조).
        Action::SetHook | Action::ShowHooks => None,
        // 인자 폼(패리티 G8v). 화면을 여는 것은 서버에 시킬 일이 아니고, **값을 정하는**
        // 넷은 종전 토글과 같은 명령에 `value` 만 실어 보낸다 — 서버는 값이 없으면
        // 뒤집고 있으면 그 값으로 정한다(`base::options` 모듈 문서).
        Action::ShowCommandOptions(_) => None,
        Action::SetSync(on) => Some(Command::ToggleSync { value: Some(on) }),
        Action::SetMonitor { bell, on } => Some(Command::ToggleMonitor {
            which: if bell { "bell" } else { "activity" },
            value: Some(on),
        }),
        Action::SetAutoRename(on) => Some(Command::ToggleAutoRename { value: Some(on) }),
        Action::SetServerOption(opt, on) => Some(Command::SetOption(
            match opt {
                base::ServerOpt::SingleBorder => ServerOption::SingleBorder,
                base::ServerOpt::CoalesceRepaints => ServerOption::CoalesceRepaints,
                base::ServerOpt::ExitEmpty => ServerOption::ExitEmpty,
                base::ServerOpt::NestAutoAttach => ServerOption::NestAutoAttach,
                base::ServerOpt::WinMouseMotion => ServerOption::WinMouseMotion,
            },
            Some(on),
        )),
        // 설정 파일을 고치는 일이라 서버와 무관하다.
        Action::BindKey | Action::UnbindKey => None,
        // 목표 자리는 탭 목록을 봐야 안다 — 뷰가 옮긴다.
        // ★ 방향은 **서버가 센다**(핀 구역 클램프까지) — 우리가 자리를 세서 보내던
        // 것이 `move_current_tab` 의 `where` 칸과 안 맞아 한 번도 안 먹었다.
        Action::MoveTabBy(to) => Some(Command::MoveCurrentTab {
            direction: match to {
                base::TabMove::Left => "left",
                base::TabMove::Right => "right",
                base::TabMove::First => "first",
                base::TabMove::Last => "last",
            },
        }),
        Action::SwapTab => None,
        // 이쪽은 자리 둘이 액션에 이미 실려 있어 **뷰를 거칠 것이 없다**.
        Action::MoveTabAt { from, to } => Some(Command::MoveTab {
            index: from as usize,
            to: to as usize,
        }),
        Action::RespawnPane => Some(Command::RespawnPane),
        // 설정 파일이 주인이라 서버에 시킬 일이 없다(뷰가 `flip_config` 로 처리한다).
        Action::ToggleInactiveDim => None,
    }
}

#[cfg(test)]
mod action_tests {
    use super::*;
    use base::Action;

    #[test]
    fn every_action_is_decided_here_not_left_to_the_view() {
        // 와일드카드 없는 match 가 컴파일로 누락을 막지만, "None 인 것이 정말 서버가
        // 할 일이 없어서인가"는 컴파일러가 못 묻는다. 목록을 못박아 둔다 — 새 액션이
        // 조용히 None 으로 떨어지면 그 기능은 **아무 데서도 안 일어난다**.
        let server_side = [Action::SelectNext, Action::SelectPrev];
        for action in server_side {
            assert!(action_to_command(action).is_some(), "{action:?} 가 서버로 안 간다");
        }
        // 탭바를 봐야 정해지는 셋 — 여기서 None 인 것이 맞지만 **거기서는 Some** 이어야
        // 한다. 이 대조가 없으면 셋 중 하나를 옮기다 빠뜨려도 "설계대로 None" 으로 읽혀
        // 그 키가 조용히 죽는다(이 테스트가 막으려는 바로 그 모양이다).
        let needs_tabs = [Action::SelectFirst, Action::SelectLast, Action::SelectTab(1)];
        let bar = crate::tabs::TabBar {
            tabs: vec![crate::tabs::Tab { index: 0, ..crate::tabs::Tab::default() }],
            ..crate::tabs::TabBar::default()
        };
        for action in needs_tabs {
            assert!(action_to_command(action).is_none(), "{action:?} 는 탭바를 봐야 한다");
            assert!(
                action_to_command_with_tabs(action, &bar).is_some(),
                "{action:?} 가 탭바를 줘도 서버로 안 간다"
            );
        }
        let client_side = [
            Action::ToggleExpand,
            Action::ToggleClaudeDetail,
            Action::EnterScroll,
            Action::Quit,
        ];
        for action in client_side {
            assert!(action_to_command(action).is_none(), "{action:?} 를 서버로 보냈다");
        }
    }

    #[test]
    fn moving_between_tabs_uses_the_servers_own_verbs() {
        // 이름이 틀리면 서버는 플러그인 훅으로 넘기고 **조용히 끝낸다**(적합성 테스트가
        // 그래서 따로 있다). 여기서는 의도가 엉뚱한 명령에 붙지 않았는지만 본다.
        assert_eq!(action_to_command(Action::SelectNext), Some(Command::NextWindow));
        assert_eq!(action_to_command(Action::SelectPrev), Some(Command::PrevWindow));
        // 탭을 고르는 셋은 탭바를 봐야 하므로 여기서는 None 이다 — 짐작한 index 를
        // 돌려주면 **틀린 탭을 고르고도 조용하다**. 해석은 아래 테스트가 잰다.
        assert_eq!(action_to_command(Action::SelectFirst), None);
        assert_eq!(action_to_command(Action::SelectLast), None);
        assert_eq!(action_to_command(Action::SelectTab(3)), None);
    }

    /// 표시 번호 → (index, wid). **번호는 index 가 아니다**(번호 3 = index 2).
    ///
    /// 종전 판은 `SelectTab(n)` 의 n 을 index 로 그대로 썼다 — `esc 3` 이 4번 탭을
    /// 열었다. 고정 탭이 섞이면 더 크게 어긋난다(고정은 오른쪽 구역으로 밀린다).
    #[test]
    fn a_tab_number_is_not_a_tab_index() {
        use crate::tabs::{Tab, TabBar};
        let tab = |index: usize, wid: i64, pinned: bool| Tab {
            index,
            wid: Some(wid),
            pinned,
            ..Tab::default()
        };
        // 화면에 보이는 순서: 1=index1(비고정) · 2=index2(비고정) · 3=index0(고정).
        let bar = TabBar {
            tabs: vec![tab(0, 100, true), tab(1, 101, false), tab(2, 102, false)],
            ..TabBar::default()
        };
        assert_eq!(
            action_to_command_with_tabs(Action::SelectTab(1), &bar),
            Some(Command::SelectWindow { index: 1, wid: Some(101) }),
            "번호 1 은 시각 순서의 첫 탭(비고정)이다"
        );
        assert_eq!(
            action_to_command_with_tabs(Action::SelectTab(3), &bar),
            Some(Command::SelectWindow { index: 0, wid: Some(100) }),
            "번호 3 은 오른쪽 구역의 고정 탭이다 — index 3 이 아니다"
        );
        assert_eq!(
            action_to_command_with_tabs(Action::SelectFirst, &bar),
            Some(Command::SelectWindow { index: 1, wid: Some(101) })
        );
        assert_eq!(
            action_to_command_with_tabs(Action::SelectLast, &bar),
            Some(Command::SelectWindow { index: 0, wid: Some(100) })
        );
        // 없는 번호는 아무것도 안 보낸다(탭이 줄어든 뒤 낡은 번호를 눌렀다).
        assert_eq!(action_to_command_with_tabs(Action::SelectTab(9), &bar), None);
    }

    /// 다중홉 `C via B` — ` via ` 는 **구분자로만** 쓰고 양쪽은 원시 문자열이다.
    ///
    /// 정본(`clientcmd`)과 같은 규칙인지를 값으로 못박는다. 특히 백슬래시가 든 도메인
    /// 계정이 그대로 살아 있어야 한다 — 토크나이저를 태우면 거기서 조용히 사라진다.
    #[test]
    fn a_multi_hop_answer_splits_on_the_last_via() {
        let attach = |s: &str| Command::remote_attach(s);
        assert_eq!(
            attach("box1"),
            Some(Command::RemoteAttach { host: "box1".into(), via: None })
        );
        assert_eq!(
            attach("boxC via boxB"),
            Some(Command::RemoteAttach { host: "boxC".into(), via: Some("boxB".into()) })
        );
        // 마지막 것이 구분자다(호스트 이름 안에 그 낱말이 들어 있어도).
        assert_eq!(
            attach("a via b via c"),
            Some(Command::RemoteAttach { host: "a via b".into(), via: Some("c".into()) })
        );
        // 백슬래시 보존(도메인 계정).
        assert_eq!(
            attach(r"NATGAMES\user@boxC via boxB"),
            Some(Command::RemoteAttach {
                host: r"NATGAMES\user@boxC".into(),
                via: Some("boxB".into()),
            })
        );
        // 한쪽이 비면 구분자가 아니다.
        assert_eq!(
            attach("x via "),
            Some(Command::RemoteAttach { host: "x via".into(), via: None })
        );
        assert_eq!(attach("   "), None, "빈 대답은 아무 일도 아니다");
    }

    /// 1홉 프레임은 **종전과 한 바이트도 안 달라야** 한다(구서버 호환).
    #[test]
    fn a_single_hop_frame_carries_no_via_key() {
        let frame = Command::remote_attach("box1").unwrap().to_frame();
        assert_eq!(frame["host"], "box1");
        assert!(frame.get("via").is_none(), "1홉인데 via 키가 실렸다: {frame}");
        let frame = Command::remote_attach("boxC via boxB").unwrap().to_frame();
        assert_eq!(frame["via"], "boxB");
    }

    /// 구서버 호환: `wid` 를 모르는 탭이면 프레임이 종전과 같다.
    #[test]
    fn a_tab_without_a_wid_sends_the_old_frame() {
        use crate::tabs::{Tab, TabBar};
        let bar = TabBar {
            tabs: vec![crate::tabs::Tab { index: 0, wid: None, ..Tab::default() }],
            ..TabBar::default()
        };
        let cmd = action_to_command_with_tabs(Action::SelectTab(1), &bar).expect("탭이 있다");
        assert_eq!(cmd, Command::SelectWindow { index: 0, wid: None });
        assert!(cmd.to_frame().get("wid").is_none());
    }
}


/// 선택 회신 하나를 **서버 페이스트 버퍼에 넣을 명령**으로. 넣을 것이 없으면 `None`.
///
/// 빈 회신에 아무 일도 안 하는 이유: 공백만 끌었거나 선택이 스크롤백 밖으로 밀려난
/// 경우 서버는 빈 문자열을 준다. 그걸 그대로 넣으면 **멀쩡하던 클립보드가 지워진다** —
/// 사용자는 방금 복사해 둔 것을 잃는다(파이썬 클라도 빈 회신은 조용히 버린다).
pub fn selection_to_buffer(text: &str) -> Option<Command> {
    (!text.is_empty()).then(|| Command::SetBuffer {
        text: text.to_owned(),
    })
}

/// 복사 결과 한 마디.
///
/// **성공과 실패를 가려 적는다.** OS 클립보드가 안 되는 상자(도구 없는 ssh 세션)에서도
/// 서버 페이스트 버퍼는 채워지므로 pytmux 안에서의 붙여넣기는 된다 — 그런데 그냥
/// "복사됨"이라고만 하면, 다른 앱에 붙여넣으려던 사용자는 왜 안 되는지 알 방법이 없다.
///
/// 글자 수는 바이트가 아니라 **글자**로 센다 — 한글 한 자가 3자로 보이면 사용자가 자기
/// 선택을 못 알아본다.
pub fn copy_note(chars: usize, to_clipboard: bool) -> String {
    use base::i18n::tf;

    let n = chars.to_string();
    if to_clipboard {
        tf("{n}자 복사됨", &[("n", &n)])
    } else {
        tf("{n}자 복사됨(pytmux 버퍼만 — OS 클립보드 도구 없음)", &[("n", &n)])
    }
}

#[cfg(test)]
mod copy_tests {
    use super::*;

    #[test]
    fn an_empty_selection_never_clears_the_clipboard() {
        // ★ 공백만 끌었거나 선택이 스크롤백 밖으로 밀리면 서버는 빈 문자열을 준다.
        // 그걸 넣으면 사용자가 **방금 복사해 둔 것을 잃는다**.
        assert!(selection_to_buffer("").is_none());
        assert!(selection_to_buffer("x").is_some());
    }

    #[test]
    fn a_split_says_which_way_in_the_word_the_server_reads() {
        // ★ 이 오라클이 없어서 **상하 분할이 한 번도 안 됐다**(2026-07-29 G8v 라이브).
        // 우리는 `horizontal` 이라는 칸을 보냈고 서버는 `orient` 를 찾다가 못 찾아
        // 늘 기본값 `lr` 로 떨어졌다 — 명령 이름은 맞아서 적합성 게이트도 조용했다.
        let lr = Command::Split {
            horizontal: true,
            path: "current".into(),
        }
        .to_frame();
        let tb = Command::Split {
            horizontal: false,
            path: "current".into(),
        }
        .to_frame();
        assert_eq!(lr["orient"], "lr", "{lr}");
        assert_eq!(tb["orient"], "tb", "{tb}");
        // 서버가 안 읽는 칸을 보내면 "보냈는데 안 먹는다"가 된다.
        assert!(lr.get("horizontal").is_none(), "{lr}");
    }

    #[test]
    fn a_failed_clipboard_is_said_out_loud() {
        // "복사됨"만 말하면 다른 앱에 붙여넣으려던 사용자는 왜 안 되는지 못 찾는다.
        let ok = copy_note(3, true);
        let no = copy_note(3, false);
        assert_ne!(ok, no);
        assert!(no.contains("OS 클립보드"), "{no}");
        assert!(ok.starts_with("3자"), "{ok}");
    }
}
