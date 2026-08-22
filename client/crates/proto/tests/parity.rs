//! 패리티 게이트 — 파이썬 클라와의 격차를 **테스트가 센다**(로드맵 G0).
//!
//! # 왜 문서가 아니라 테스트인가
//!
//! "무엇이 남았나" 목록은 적는 순간부터 낡는다(pytmux HANDOFF §10 머리말이 경고하는
//! 패턴이고, 이 저장소도 이름 11개를 손으로 적어 두고 자기끼리 맞춰 보던 자리를 이미
//! 밟았다). 그래서 목록은 **파이썬 구현에서 뽑고**
//! (`scripts/gen_client_surface_fixture.py`) 여기서는 분류만 한다.
//!
//! 규칙 셋:
//!
//! 1. **빠짐도 군더더기도 없다.** 아래 표는 픽스처와 집합이 정확히 같아야 한다. 파이썬이
//!    명령을 하나 늘리면, 그것을 분류할 때까지 이 테스트가 운다.
//! 2. **점수는 양방향 래칫이다.** 덮은 수가 줄면 실패하고 **늘어도 실패한다** — 늘었으면
//!    같은 CL 에서 숫자를 고치게 해서 "언제 무엇이 늘었나"가 이력에 남는다(pytmux 의
//!    `test_wait_convention.py` 와 같은 장치).
//! 3. **`Done` 은 사용자가 실제로 부를 수 있을 때만**이다. 서버 명령이 배선돼 있어도
//!    부를 키·화면이 없으면 `Partial` 이다 — 그 구분이 없으면 진행률이 거짓말을 한다.
//!
//! 진행률: `cargo test -p proto --test parity -- --nocapture`

use std::collections::{BTreeMap, BTreeSet};

use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    commands: BTreeMap<String, String>,
    prefix_keys: BTreeMap<String, String>,
    esc_keys: BTreeMap<String, String>,
    menu_items: Vec<String>,
    settings: BTreeMap<String, String>,
    set_options: Vec<String>,
    screens: Vec<String>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/client_surface.json"))
        .expect("픽스처를 읽을 수 없다 — scripts/gen_client_surface_fixture.py 로 다시 뽑을 것")
}

/// 이 항목을 네이티브 클라가 어디까지 갖고 있나.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Cover {
    /// 사용자가 실제로 부를 수 있다.
    Done,
    /// 절반만 — 명령은 배선됐는데 입구가 없거나, 조작의 일부만 된다.
    Partial,
    Missing,
}
use Cover::{Done, Missing, Partial};

/// 표면 하나 — 칸은 **GUI 하나**다.
///
/// # 왜 한 칸인가 (2026-08-01 에 되돌렸다)
///
/// 이 표는 잠깐 `tui`/`gui` 두 칸이었다(L2). Rust TUI 가 **지워지면서** 그 축이 사라졌다 —
/// 제품은 정본 Textual TUI 와 이 GUI 둘이고, 정본과의 대조가 곧 이 표다.
///
/// ★ **G1 재측정(2026-08-01) — 액션 축은 잰 값이다.** 3열로 쪼갤 때 tui 값을 그대로
/// 복사했으므로 "TUI 에만 있던 Done 이 섞여 있나"가 열려 있었다. 189줄을 눈으로 훑는
/// 대신 **액션 전수를 GUI 에 먹여** 뷰가 아무 일도 안 하는 것을 셌고
/// (`gui` 의 `every_action_does_something_in_this_view`), 죽은 액션은 둘뿐이었다 —
/// 둘 다 정당하다(`EnterScroll` 은 모드 전이 · `ToggleExpand` 는 블록 목록 데모 뷰의 것).
/// 리포트 = `docs/reports/2026-08-01-parity-ratchet-g1-remeasure.md`.
///
/// ★ **설정 36·화면 17 축도 쟀다(2026-08-02p).** 액션 축에 던진 질문을 두 축에 그대로
/// 던진 것이다 — 설정은 *"그 줄의 효과가 어딘가에 닿나"*(`base` 의
/// `every_setting_row_reaches_something_that_reads_it` — 36줄 전부 닿는다. 예외 둘은
/// 이미 알려진 특별 취급), 화면은 *"열 길이 있나"*(`gui` 의
/// `every_screen_has_a_way_to_open_it` — 액션으로 못 여는 셋은 전부 서버 회신이 연다).
/// 두 축 다 **손으로 적어 둔 예외 목록이 틀렸다**(설정 다섯이 거짓 사망 · 화면 여덟 중
/// 다섯은 이미 열렸다) — 기계로 재는 이유가 그것이다.
/// 리포트 = `docs/internal/client/reports/2026-08-02-locale-mdir-and-the-ruler.md`.
///
/// ⚠ 세 측정 다 재는 것은 **"무엇인가 한다"이지 "맞는 일을 한다"가 아니다**.
struct Item {
    name: &'static str,
    cover: Cover,
    /// 어떻게 되는가 · 무엇이 모자라나. **`Partial` 의 설명이 곧 다음 할 일**이다.
    note: &'static str,
}

const fn i(name: &'static str, cover: Cover, note: &'static str) -> Item {
    Item { name, cover, note }
}

/// 명령 팔레트에 뜨는 이름(`clientutil.COMMANDS`).
static COMMANDS: &[Item] = &[
    i("automatic-rename", Done, "팔레트"),
    i("bind-key", Done, "팔레트 → `[-n] 키 명령` (설정 파일에 남는다)"),
    i("break-pane", Done, "prefix !"),
    i("capture-pane", Done, "팔레트 - 보이는 영역 · -S 로 전체"),
    i("choose-buffer", Done, "prefix ="),
    i("choose-tree", Done, "prefix w"),
    i("clear-history", Done, "팔레트 - 스크롤백 비우기"),
    i("coalesce-repaints", Done, "팔레트"),
    i(
        "commands",
        Done,
        "팔레트(86/87 + 파이썬 설명·타이핑 필터) — paste-clipboard 만 밖(뷰별 능력, every_python_command_is_in_the_palette 의 예외 목록). 종전 사유의 25개는 낡은 수였다",
    ),
    i("detach-client", Done, "prefix d — 창을 닫는다"),
    i("display-message", Done, "팔레트 → 알림 이력에 남는다"),
    i("display-popup", Done, "팔레트 → 명령 물음 · 서버 라이브 PTY 팝업"),
    i("exit-empty", Done, "팔레트"),
    i("help", Done, "팔레트로 간다 — 파이썬도 help·commands·?·list-commands 넷이 CommandListScreen 하나다"),
    i("if-shell", Done, "팔레트 → `조건 | 명령` · 성공하면 평소 경로로"),
    i("inactive-dim", Done, "팔레트 · 설정 화면"),
    i("inactive-dim-ratio", Done, "설정 화면 - Enter 로 올리고 한 바퀴"),
    i("join-pane", Done, "팔레트 → 탭 번호"),
    i("kill-pane", Done, "prefix x → 확인 화면 → y"),
    i("kill-server", Done, "팔레트 → 확인 화면 → y"),
    i("kill-tab", Done, "prefix & → 확인 화면 → y"),
    i("lang", Done, "팔레트 → 인자 폼(한국어/English) · 즉시 적용 · .lang 영속(파이썬과 같은 파일)"),
    i("last-tab", Done, "prefix l · esc shift-G"),
    i("layout-load", Done, "팔레트 → 이름 입력(현재 탭 덮어쓰기)"),
    i("layout-load-new", Done, "팔레트 → 이름 입력(새 탭)"),
    i("layout-save", Done, "팔레트 → 이름 입력"),
    i("list-commands", Done, "팔레트로 간다(commands 와 같은 곳) — 86/87 + 설명"),
    i("list-keys", Done, "esc ? (도움말 화면)"),
    i("merge-remote-tab", Done, "피커 → h/v 방향 → 대상 패널 못박고 join_pane"),
    i("monitor-activity", Done, "팔레트 · 상태줄에 [활동감시]"),
    i("monitor-bell", Done, "팔레트 · 상태줄에 [벨감시]"),
    i("mouse-help", Done, "팔레트 → 키 도움말(마우스 절 포함) — 파이썬도 list-keys 의 별칭이다(clientcmd 951)"),
    i("move-tab", Done, "prefix . (자리 입력)"),
    i("move-tab-first", Done, "팔레트"),
    i("move-tab-last", Done, "팔레트"),
    i("move-tab-left", Done, "팔레트 - 끝이면 아무 일도 안 한다"),
    i("move-tab-right", Done, "팔레트"),
    i("nest-auto-attach", Done, "팔레트"),
    i("new-claude-tab", Done, "esc c · 팔레트 — 지금 디렉토리에서 Claude Code 가 도는 새 탭(pytmux-137)"),
    i("new-tab", Done, "prefix c (지금 패널의 디렉토리에서)"),
    i("next-layout", Done, "prefix Space"),
    i("next-tab", Done, "prefix n · esc j / esc down"),
    i("paste-buffer", Done, "prefix ] (맨 앞 버퍼) · 번호 선택은 G3"),
    i("paste-clipboard", Done, "GUI Ctrl+Shift+V · TUI bracketed paste"),
    i("pin-tab", Done, "팔레트 - 켜기(뒤집기 아님)"),
    i("pin-toggle", Done, "prefix P"),
    i("pipe-pane", Done, "팔레트 → 명령 입력(비우면 끄기)"),
    i("plugins", Done, "팔레트(별칭 plugin-manager) → 관리 화면"),
    i("previous-tab", Done, "prefix p · esc k / esc up"),
    i(
        "reconnect",
        Done,
        "팔레트(별칭 resync) — 정체된 소켓을 버리고 같은 엔드포인트에 다시 붙는다",
    ),
    i("redraw", Done, "prefix r"),
    i("remote-attach", Done, "팔레트 → host 입력"),
    i("remote-detach", Done, "팔레트 → host 입력(비우면 전부)"),
    i("remote-new-tab", Done, "팔레트 → host 입력"),
    i("rename-pane", Done, "prefix T · 팔레트 · 메뉴"),
    i("rename-tab", Done, "prefix , (입력 화면)"),
    i("resize-pane", Done, "prefix H J K L · prefix z (줌) · 경계선 드래그"),
    i("respawn-pane", Done, "팔레트 - 죽은 패널에 셸 다시"),
    i(
        "restart-all",
        Done,
        "팔레트 → 드라이런 → 안전하면 곧장, 실패하면 실패 항목을 적어 재확인 → 서버 재시작 + 클라 재기동",
    ),
    i("restart-check", Done, "팔레트 - 드라이런 결과 화면(부작용 없음)"),
    i("restart-server", Done, "팔레트 → 드라이런 게이트 → 실행(파이썬과 같은 순서)"),
    i("restore-layout", Done, "팔레트 - 전체 배치 복원"),
    i("rotate-window", Done, "prefix Ctrl+o"),
    i("run-shell", Done, "팔레트 → 명령 · 결과 화면(40줄) — 스레드에서 돈다"),
    i("save-layout", Done, "팔레트 - 전체 배치 영속"),
    i("select-layout", Done, "프리셋 목록 화면 → 고르면 적용"),
    i("select-pane", Done, "prefix 화살표 · prefix o · prefix ; · 마우스 클릭"),
    i("select-tab", Done, "prefix 0~9"),
    i("send-escape", Done, "팔레트 · esc e · Shift+ESC"),
    i("send-keys", Done, "팔레트 → 키 표기(hello Enter · C-c)"),
    i("set", Done, "팔레트 → `옵션 값` 한 줄(설정 파일에 쓴다)"),
    i("set-hook", Done, "팔레트 → 입력 화면 · -u 로 풀기 · 설정 파일의 hook 줄도 읽는다"),
    i("settings", Done, "팔레트 · 화면에서 값을 바꾼다(33줄)"),
    i("show-hooks", Done, "읽는 화면 - 파이썬과 같은 `이벤트 → 명령` 줄"),
    i("show-options", Done, "팔레트 - 설정 화면을 연다"),
    i("single-border", Done, "팔레트 - 값 없이 서버가 뒤집는다"),
    i("source-file", Done, "팔레트 - 설정을 다시 읽고 이번 판에 적용"),
    i("split-window", Done, "prefix % (좌우) · prefix \" (상하)"),
    i("strip-box-drawing", Done, "설정 화면 - 붙여넣기에서 테두리 제거"),
    i("swap-pane", Done, "prefix { }"),
    i("swap-tab", Done, "팔레트 → 번호 입력"),
    i("synchronize-panes", Done, "팔레트 · 상태줄에 [동기화] 표식"),
    i("unbind-key", Done, "팔레트 → `[-n] 키`"),
    i("unpin-tab", Done, "팔레트 - 끄기"),
    i("version", Done, "팔레트 - 서버 버전·가동 시간"),
    i("vt-parser", Done, "설정 화면 → 다음 값 · 팔레트"),
    i("win-mouse-motion", Done, "팔레트"),
    i("window-size", Done, "설정 화면 → 다음 값 · 팔레트"),
];

/// prefix 모드 키(`clientutil.PREFIX_KEYS`). 네이티브에는 prefix 모드가 아직 없다.
static PREFIX_KEYS: &[Item] = &[
    i("p_P", Done, "탭 고정 토글"),
    i(
        "p_R",
        Done,
        "prefix R — 토큰리밋 자동재개 토글(인자 없는 토글) · 켜지면 상태줄에 [자동재개]",
    ),
    i("p_T", Done, "prefix T - 패널 제목 입력"),
    i("p_amp", Done, "탭 닫기"),
    i("p_arrows", Done, "화살표로 패널 이동"),
    i("p_bang", Done, "패널을 새 탭으로"),
    i("p_c", Done, "새 탭"),
    i("p_co", Done, "Ctrl+o 패널 회전"),
    i("p_colon", Done, "prefix : — 명령 팔레트(치면 좁혀진다 · Enter 실행)"),
    i("p_comma", Done, "prefix , — 이름 입력 화면(현재 이름이 채워져 있다)"),
    i("p_d", Done, "detach = 이 클라 창을 닫는다"),
    i("p_dot", Done, "prefix . — 옮길 자리(번호) 입력"),
    i("p_dq", Done, "상하 분할"),
    i("p_enter", Done, "prefix Enter - 메뉴(30줄 · 파이썬 32줄 — 격차는 MenuScreen 이 센다)"),
    i("p_eq", Done, "prefix = — 버퍼 목록에서 골라 붙여넣기"),
    i("p_hjkl", Done, "H J K L 로 경계 밀기(3칸)"),
    i("p_l", Done, "직전 탭"),
    i("p_lb", Done, "prefix [ — 스크롤 모드"),
    i("p_np", Done, "n / p 로 다음·이전 탭"),
    i("p_num", Done, "0~9 로 탭 선택"),
    i("p_o", Done, "다음 패널"),
    i("p_pct", Done, "좌우 분할"),
    i("p_q", Done, "prefix q - 패널 번호 · 숫자로 그 패널"),
    i("p_r", Done, "화면 다시 그리기"),
    i("p_rb", Done, "] 로 버퍼 맨 앞 붙여넣기"),
    i("p_semi", Done, "직전 패널"),
    i("p_space", Done, "레이아웃 순환"),
    i("p_swap", Done, "{ } 로 이웃과 교환"),
    i("p_t", Done, "시계 오버레이 — clock 플러그인 화면 재현"),
    i("p_w", Done, "prefix w — 트리(개요) 목록에서 탭·패널 선택"),
    i("p_x", Done, "패널 닫기"),
    i("p_z", Done, "줌 토글"),
];

/// esc 모드 키(`clientutil.ESC_MODE_KEYS`).
static ESC_KEYS: &[Item] = &[
    i("e_P", Done, "esc P — 탭 고정(핀) 토글"),
    i("e_arrows", Done, "esc ←↑↓→ — 패널 이동(G1c)"),
    i("e_bt", Done, "esc ` — 리터럴 백틱"),
    i("e_colon", Done, "esc : — 같은 팔레트"),
    i("e_down", Done, "↓ 최하단 → 하단 배지 포커스 · ←→ 순환 · Enter 실행"),
    i("e_e", Done, "esc e — 패널에 ESC"),
    i("e_esc", Done, "명령 모드에서 두 번째 ESC 가 패널로 간다"),
    // esc f — 열린 **모든 탭·패널**의 스크롤백을 한 번에 훑어 결과 목록을 띄우고 그
    // 자리로 점프한다(pytmux-27). `SearchResultsScreen` 줄과 짝이다.
    i("e_f", Done, "esc f — 전역 검색 물음(`search_all`)을 연다 · 메뉴 search_all 과 같은 자리"),
    i("e_help", Done, "esc ? — 키 도움말 화면"),
    i(
        "e_ins",
        Done,
        "esc Insert · Shift+Delete — 여러 줄 작성창(블록 선택 편집 → 투입)",
    ),
    i(
        "e_jump",
        Done,
        "esc Ctrl+↑/↓ — 이전·다음 프롬프트로 점프(스크롤 모드로 들어가 연타)",
    ),
    i("e_c", Done, "esc c — 지금 디렉토리에서 Claude Code 가 도는 새 탭(pytmux-137)"),
    i("e_n", Done, "esc n — 새 탭(G1c)"),
    i("e_num", Done, "esc 1~9 — 번호로 탭 전환"),
    i("e_p", Done, "esc p — 상하 분할(G1c)"),
    i("e_sesc", Done, "Shift+ESC — 모드에 안 들어가고 패널에 ESC"),
    i("e_tab", Done, "esc Tab — 탭 스위처(Tab/↑↓ 고르기 · Enter 전환 · Esc 취소)"),
    i("e_tb", Done, "←→ · Enter · +/a · x/d · Shift+←→ 이동 · ↓/Esc 복귀"),
    i("e_up", Done, "↑ 최상단 → 탭바 포커스([+]·[x] 포함)"),
];

/// 설정 화면 항목(`clientutil.SETTINGS`).
static SETTINGS: &[Item] = &[
    i(
        "alt-scroll",
        Done,
        "설정 화면 - TUI 가 기동 때 ESC[?1007l 로 단말 대체 스크롤을 끄고 끝낼 때 되돌린다 · GUI 는 해당 없음",
    ),
    i("ambiguous-width", Done, "설정 화면 - unicode-width 의 CJK 판정을 쓴다(auto=narrow)"),
    i("automatic-rename", Done, "설정 화면 · 팔레트"),
    i("claude-command", Done, "설정 화면 → 동작 · `esc c` 가 실행할 명령(빈 값이면 셸만)"),
    i("coalesce-repaints", Done, "설정 화면 · 팔레트"),
    i("default-path", Done, "설정 화면 → 입력"),
    i("exit-empty", Done, "설정 화면 - 현재값도 status 의 exit_empty 로 온다(2026-07-30 서버 CL)"),
    i("inactive-dim", Done, "설정 화면 · 팔레트 - 비활성 패널을 옅게"),
    i("inactive-dim-ratio", Done, "설정 화면 - Enter 로 올리고 한 바퀴"),
    i("language", Done, "설정 화면 「표시」 줄 · Enter 로 ko↔en · 영속은 lang 과 같은 길"),
    i("list-keys", Done, "설정 화면의 링크 - 키 도움말 화면"),
    i("copy-unwrap", Done, "설정 화면 - 복사할 때 앱이 접은 줄바꿈을 되돌린다(`proto::unwrap`)"),
    i("mode-keys", Done, "설정 화면 - vi/emacs 스크롤 키가 갈린다"),
    i("monitor-activity", Done, "설정 화면 · 팔레트"),
    i("monitor-bell", Done, "설정 화면 · 팔레트"),
    i("mouse", Done, "설정 화면 - 끄면 클라가 마우스를 아예 안 본다"),
    i("mouse-drag-copy", Done, "설정 화면"),
    i("mouse-drag-threshold", Done, "설정 화면 - 이 칸 수만큼 움직여야 선택이 시작된다"),
    i("nest-auto-attach", Done, "설정 화면 · 팔레트"),
    i("pane-border-status", Done, "설정 화면 · 팔레트"),
    i("plugins", Done, "설정의 링크 항목 — 관리 화면이 섰다"),
    i("prefix", Done, "설정 파일의 set prefix 를 읽는다(파이썬과 같은 파일·같은 탐색 순서)"),
    i(
        "remote-title",
        Done,
        "설정 화면 · 팔레트 - full/host/name · 원격 탭 제목을 **그릴 때만** 접는다(이름은 서버 계약이라 불변)",
    ),
    i(
        "set-titles",
        Done,
        "설정 화면 - TUI 는 OSC 2 로 단말 제목, GUI 는 창 제목 · 형식은 set-titles-string(#S:#I:#W)",
    ),
    i("single-border", Done, "설정 화면 · 팔레트"),
    i("status-bg", Done, "상태줄 배경 - 이름(brightblue)·#rrggbb · 빈 값이면 테마"),
    i("status-fg", Done, "상태줄 글자색 - 같은 표기 · 빈 값이면 테마"),
    i("status-interval", Done, "설정 화면 → 다음 값 · 이 초마다 상태줄을 다시 그린다"),
    i("status-left", Done, "형식 문자열 - #S/#I/#W/#h/#H/#{pane_title} + strftime"),
    i("status-position", Done, "설정 화면 · 팔레트 - bottom(기본)/top · 배지줄이 곧 상태줄이다"),
    i("status-right", Done, "형식 문자열 - 파이썬 기본값을 그대로 편다"),
    i("strip-box-drawing", Done, "설정 화면"),
    i("synchronize-panes", Done, "설정 화면 · 팔레트"),
    i("tab-bar", Done, "설정 화면 - auto 면 탭 하나일 때 감춘다"),
    i(
        "touch-scroll",
        Done,
        "설정 화면 · 상태줄 `⇕` 배지(누르면 스크롤 모드 토글) · 스크롤 모드에서 활성 패널 우측 한 열에 스크롤바 + 탭(▲▼=반 화면 · 트랙=점프). 산수는 정본 픽스처로 고정(scrollbar_conformance)",
    ),
    i("vt-parser", Done, "설정 화면 → 다음 값 · 팔레트"),
    i("win-mouse-motion", Done, "설정 화면 · 팔레트"),
    i("window-size", Done, "설정 화면 → 다음 값 · 팔레트"),
];

/// 팝업·모달 화면(`clientscreens.py` 의 `*Screen`).
static SCREENS: &[Item] = &[
    i(
        "ChooseBufferScreen",
        Done,
        "목록(index: preview)·Enter 붙여넣기·빈 상태 — 정본을 열어 보니 종전 사유(삭제·여러 줄 미리보기)가 파이썬에 없다(한 줄 preview·Enter 선택뿐)",
    ),
    i("ChooseLayoutScreen", Done, "프리셋 5개 - 파이썬과 같은 문구·값"),
    i(
        "ChooseTreeScreen",
        Done,
        "탭·패널 목록·이동 · d/x 로 그 자리 닫기(먼저 옮기고 확인 — 파이썬 on_key 동형). 종전 사유의 세션 조작·미리보기는 정본에 없었다",
    ),
    i(
        "CommandListScreen",
        Done,
        "그 화면의 역할을 팔레트가 한다 — 이름+파이썬 설명+타이핑 필터+실행(86/87 · 게이트가 지킨다). 카테고리 탭 대신 필터(모양 비목표)",
    ),
    i("CommandOptionsScreen", Done, "↑↓ 줄 · ←→ 값 · 만들어지는 명령줄이 보인다 · 18개 명령"),
    i(
        "ComposePromptScreen",
        Done,
        "박스·블록 선택·Enter 투입 · 프롬프트 인계(화면 긁기)와 투입 전 비우기까지",
    ),
    i("ConfirmScreen", Done, "y/Enter 예 · 그 외 아니오 · 파괴적 키(prefix x·&)가 이 위에 선다"),
    i(
        "InfoScreen",
        Done,
        "범용 1장 대신 전용 표면이 전부 대응 — options=설정 화면 · version·restart-check·run-shell·hooks·keys=전용 화면 · usage 류=알림(정본의 InfoScreen 용례 전수 대조)",
    ),
    i(
        "InfoTabsScreen",
        Done,
        "REC(플러그인 있을 때·[c]/[o])·서버(RTT 60분 그래프 — ping/pong 실측)·세션 — 정본의 토큰 탭은 2026-06-12 에 제거돼 없다",
    ),
    i(
        "MenuScreen",
        Done,
        "정본 줄 전부(파이썬 순서) — search 는 G9t 에서 서버 `search` 로, search_all 은 pytmux-27 에서 서버 `search_all` 로 배선",
    ),
    i("MergeRemoteTabScreen", Done, "같은 호스트의 다른 원격 탭 · h/v 로 방향"),
    i("NoticeHistoryScreen", Done, "등급 기호·색 · 새것이 위 · 상한 200"),
    i("PluginManagerScreen", Done, "[x]/[ ] 목록 · Enter/Space 토글"),
    i(
        "PromptScreen",
        Done,
        "한 줄 입력·확정·취소 + 인자 이력 후보(파이썬 arghist 와 같은 파일·같은 버킷 — ↑↓ 고르고 Tab 채움). 명령 이름 완성은 팔레트가 그 자리다",
    ),
    // 전역 검색 결과 판(pytmux-27) — 탭·패널·줄 미리보기 목록에서 Enter 로 그
    // 자리(탭+패널+스크롤)로 뛴다. 여는 길은 `esc f` 와 컨텍스트 메뉴 `search_all`.
    i("SearchResultsScreen", Done, "탭·패널·줄·미리보기 4열 · Enter 로 search_goto"),
    i("SettingsScreen", Done, "범주별 목록 — 파이썬 34줄 전부 대응(마지막 남았던 language 까지)"),
    i(
        "TabSwitcherScreen",
        Done,
        "탭 목록(시각 순서)·전환 · 패널 하위행을 tree 회신으로 뒤늦게 채우고(로컬·2패널 이상) 하위행 Enter 는 그 탭+그 패널로 — 파이썬 07-16 확장 동형",
    ),
    i("_SettingInputScreen", Done, "설정 값 입력 - 같은 Prompt 화면(prefix·default-path)"),
];

/// 표들의 이름과 알맹이 — 점수·분류·설명 검사가 **같은 목록**을 돈다.
///
/// 종전에는 테스트마다 다섯 줄을 다시 적었다. 표를 하나 더 만들면 어느 검사에는 넣고
/// 어느 검사에는 빠뜨리게 되고, 그러면 그 표는 조용히 검사 밖이다.
static TABLES: &[(&str, &[Item])] = &[
    ("commands", COMMANDS),
    ("prefix_keys", PREFIX_KEYS),
    ("esc_keys", ESC_KEYS),
    ("settings", SETTINGS),
    ("screens", SCREENS),
];

/// 알맹이만(이름 없이) 도는 자리용.
static TABLES_ONLY: &[&[Item]] = &[COMMANDS, PREFIX_KEYS, ESC_KEYS, SETTINGS, SCREENS];

/// 지금 점수. **양방향 래칫**이라 늘어도 줄어도 여기를 고쳐야 한다(모듈 문서 규칙 2).
///
/// `(표 이름, tui 덮음, tui 절반, gui 덮음, gui 절반)` — **뷰마다 한 쌍**이다(L2,
/// 계획 §6.1). 한 쌍만 두면 한 뷰가 뒤처지는 것을 이 숫자가 못 말한다: 실제로 GUI 가
/// 몇 달 뒤처져 있었고 그동안 점수는 만점이었다.
/// ⚠ **gui 칸은 아직 "잰 값"이 아니다**(2026-08-01). 3열로 쪼개기 전의 점수는 *정본 대
/// Rust 쪽 **아무** 뷰*였고(`CLIENT_PRODUCT_SET_2026-08-01.md` §4-G1), 쪼개면서 두 칸에
/// **같은 값을 넣었다**. 즉 지금 gui 칸은 "TUI 에만 있는 Done 을 GUI 것으로 셈하고 있을
/// 수 있다"는 종전 상태를 그대로 물려받았다.
///
/// 이 칸을 진짜로 재는 것(=표면 189개를 GUI 기준으로 전수 확인)이 **Rust TUI 퇴역의
/// 문턱**이다(같은 문서 §5 의 S3). 그때까지 이 숫자를 "GUI 가 다 된다"로 읽지 말 것.
static SCORE: &[(&str, usize, usize)] = &[
    ("commands", 88, 0),
    ("prefix_keys", 32, 0),
    ("esc_keys", 19, 0),
    ("settings", 38, 0),
    ("screens", 18, 0),
];

/// 픽스처의 명령 전부가 팔레트에 실려 있는가 — `commands`/`list-commands` 의 Done 을
/// 지키는 게이트다(파이썬이 명령을 늘리면 팔레트가 따라잡을 때까지 여기가 운다).
///
/// 예외: `monitor-bell` 과 `paste-clipboard` 는 뷰별 능력이다.
/// - `monitor-bell`: **사용자 결정으로 화면에서 감췄다**(§10-21ⓜ — "당장은 지원하지 않겠다").
///   못 하는 것이 아니라 **입구를 닫은** 것이다.
/// - `paste-clipboard`: **Ctrl+Shift+V 단축키가 주 입구**다. 팔레트에는 없지만 기능은 동작한다
///   (GUI 에서 클립보드 텍스트·이미지를 붙여넣을 수 있다).
#[test]
fn every_python_command_is_in_the_palette() {
    const NOT_IN_PALETTE: &[&str] = &["monitor-bell", "paste-clipboard"];
    let have: BTreeSet<&str> = base::PALETTE
        .iter()
        .flat_map(|e| [e.name, e.name.split(' ').next().unwrap_or(e.name)])
        .collect();
    let missing: Vec<String> = fixture()
        .commands
        .keys()
        .filter(|name| !NOT_IN_PALETTE.contains(&name.as_str()) && !have.contains(name.as_str()))
        .cloned()
        .collect();
    assert!(
        missing.is_empty(),
        "파이썬 명령이 팔레트에 없다(실을 수 없으면 NOT_IN_PALETTE 에 이유와 함께): {missing:?}"
    );
}

fn split(items: &[Item]) -> (usize, usize) {
    (
        items.iter().filter(|x| x.cover == Done).count(),
        items.iter().filter(|x| x.cover == Partial).count(),
    )
}

fn check_set(label: &str, table: &[Item], fixture: impl Iterator<Item = String>) {
    let want: BTreeSet<String> = fixture.collect();
    let have: BTreeSet<&str> = table.iter().map(|x| x.name).collect();
    let missing: Vec<&String> = want.iter().filter(|n| !have.contains(n.as_str())).collect();
    let extra: Vec<&&str> = have.iter().filter(|n| !want.contains(**n)).collect();
    assert!(
        missing.is_empty(),
        "{label}: 파이썬에 있는데 이 표에 없다 {missing:?}\n\
         새로 생긴 것이면 분류(Done/Partial/Missing)를 적을 것 — 분류하지 않으면 \
         '남은 일'에서 조용히 빠진다."
    );
    assert!(
        extra.is_empty(),
        "{label}: 이 표에만 있고 파이썬에는 없다 {extra:?}\n\
         파이썬이 지운 것이면 여기서도 지운다(네이티브 전용 기능은 이 표가 아니라 \
         리포트에 적는다)."
    );
}

#[test]
fn every_surface_is_classified() {
    let fx = fixture();
    check_set("commands", COMMANDS, fx.commands.keys().cloned());
    check_set("prefix_keys", PREFIX_KEYS, fx.prefix_keys.keys().cloned());
    check_set("esc_keys", ESC_KEYS, fx.esc_keys.keys().cloned());
    check_set("settings", SETTINGS, fx.settings.keys().cloned());
    check_set("screens", SCREENS, fx.screens.iter().cloned());
}

#[test]
fn the_score_moves_only_on_purpose() {
    for (label, items) in TABLES {
        let (_, want_done, want_partial) = SCORE
            .iter()
            .find(|(name, ..)| name == label)
            .unwrap_or_else(|| panic!("{label} 이 SCORE 에 없다"));
        assert_eq!(
            split(items),
            (*want_done, *want_partial),
            "{label} 의 점수가 달라졌다 — 늘었으면 **같은 CL 에서** SCORE 를 고치고, \
             줄었으면 무엇이 사라졌는지 확인할 것"
        );
    }
}

#[test]
fn a_classified_item_says_how() {
    // Done/Partial 인데 설명이 없으면 그 분류는 다음 사람에게 아무 말도 안 한다 —
    // 특히 Partial 은 **무엇이 모자라나**가 곧 다음 할 일이다.
    for items in TABLES_ONLY {
        for item in *items {
            if item.cover != Missing {
                assert!(
                    !item.note.is_empty(),
                    "{}: {:?} 인데 설명이 없다",
                    item.name,
                    item.cover
                );
            }
        }
    }
}

#[test]
fn the_fixture_keeps_the_surfaces_we_do_not_score_yet() {
    // 메뉴 항목은 **명령의 다른 입구**이고 set 옵션은 `set` 명령의 인자라 따로 세지
    // 않는다. 그래도 픽스처에는 남긴다 — 파이썬이 그 표를 지우거나 이름을 바꾸면
    // diff 에 보여야 하고, 그때 세는 단위를 다시 정한다.
    let fx = fixture();
    assert!(!fx.menu_items.is_empty(), "메뉴 항목이 비었다");
    assert!(!fx.set_options.is_empty(), "set 옵션이 비었다");
}

#[test]
fn print_the_score() {
    // `--nocapture` 로 보는 진행률. 실패하지 않는다 — 이건 게이트가 아니라 **자**다.
    //
    // ⚠ 이 수는 아직 GUI 기준으로 **잰 값이 아니다**(`SCORE`·`Item` 머리말).
    println!("\n패리티(로드맵 G0 기준 · 정본 대 GUI):");
    let (mut all_done, mut all_partial, mut total) = (0, 0, 0);
    for (label, items) in TABLES {
        let (done, partial) = split(items);
        all_done += done;
        all_partial += partial;
        total += items.len();
        println!(
            "  {label:<12} 덮음 {done:>3} · 절반 {partial:>3} · 남음 {:>3} (전체 {})",
            items.len() - done - partial,
            items.len()
        );
    }
    println!(
        "  {:<12} 덮음 {all_done:>3} · 절반 {all_partial:>3} · 남음 {:>3} (전체 {total})\n",
        "합계",
        total - all_done - all_partial
    );
}
