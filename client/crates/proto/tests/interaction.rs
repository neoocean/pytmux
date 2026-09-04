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
//! ★ **2026-09-03(pytmux-454): `canon` 칸이 선언에서 측정으로 섰다.** 정본 쪽을 기계로
//! 읽는 자가 생겼다 — [`screen_key_conformance`] 가 `clientscreens.py` 의 화면 클래스
//! `on_key`·`BINDINGS`·`_NAV_KEYS` 를 AST 로 걸어(`scripts/gen_screen_keys.py`) 그 키를
//! [`base::Screens`] 에 **실제로 눌러** 대조한다. 그래서 이 표의 `Same` 은 이제 대부분
//! 「그 시험이 쟀다」를 가리키고, **못 잰 줄은 0 이다**(`UNMEASURED_CEILING` 도 0).
//!
//! 그 자를 세우자 **다섯이 갈려 있었고 같은 CL 에서 고쳤다** — 팔레트·인자 폼·메뉴가
//! 제 것 아닌 키에 판을 닫았고(정본은 흘려보낸다), 팔레트의 `Home`·`End` 도 닫았다
//! (정본은 커서를 옮긴다). pytmux-181·273 이 목록·설정 판에서 없앤 `_ => close_top()`
//! 이 세 판에 남아 있던 것이다.
//!
//! ⚠ **그래도 이 축이 남는다.** 저쪽은 「정본이 이름을 적은 키」와 「표 밖의 키 하나」를
//! 재고, 이쪽은 **화면 전수**에 대해 그 결과를 사람이 읽을 표로 세우고 갈림의 근거를
//! 요구한다. 정본에 짝이 없는 판 셋(`PluginView`·`Summary`·`Cursor`)은
//! 저쪽이 잴 것이 없어 여기서만 판정된다.
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

#[path = "common/open_screen.rs"]
mod open_screen;

use open_screen::opened;

use base::keys::{Key, Mods};
use base::screens::{Screen, ScreenKey, Screens};

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
        Same("clientscreens.py InfoScreen._NAV_KEYS 직접 대조 — up/down/pageup/pagedown \
         과 함께 home/end 를 먹는다. `Screens::press` 의 InfoScreen 계열 분기가 이제 \
         Home→맨 위, End→맨 아래를 처리하고 그 밖의 키는 여전히 닫는다(pytmux-273 ①)"),
        "읽는 판(키 도움말). 「아무 키나 닫기」는 정본 `InfoScreen` 과 같고, **`Home`·`End`도 \
         이제 커서를 옮긴다**(고쳐졌다 — 종전엔 이 둘도 판을 닫았다)",
    ),
    c(
        Screen::Tabs,
        Stays,
        Same("screen_key_conformance — 정본 `TabSwitcherScreen.on_key` 를 AST 로 읽어 \
         (`scripts/gen_screen_keys.py`) 눌러 봤다: `escape` 만 닫고 그 밖은 흘려보낸다 \
         (pytmux-454)"),
        "탭 스위처. `press_list` 를 지나 **제 것 아닌 키를 삼킨다** — 정본과 같다 \
         (종전엔 안 쟀다)",
    ),
    c(
        Screen::Tree,
        Stays,
        Same("screen_key_conformance — 정본 `ChooseTreeScreen.on_key` 를 AST 로 읽어 \
         눌러 봤다: `escape` 만 닫고 그 밖은 흘려보낸다. `d`/`x`(그 줄 닫기)는 정본이 \
         **조건부로** 닫아(`close_maybe`) 대조에서 뺐고, 그 사실은 그 시험의 \
         `the_unmeasured_branches_are_named_not_hidden` 가 이름으로 드러낸다 \
         (pytmux-454)"),
        "세션·탭·패널 개요. 제 것 아닌 키를 삼키는 것이 정본과 같다",
    ),
    c(
        Screen::Buffers,
        Stays,
        Same("screen_key_conformance — 정본 `ChooseBufferScreen.on_key` 를 AST 로 읽어 \
         눌러 봤다: `escape` 만 닫고 그 밖은 흘려보낸다(pytmux-454)"),
        "페이스트 버퍼 목록. 제 것 아닌 키를 삼키는 것이 정본과 같다",
    ),
    c(
        Screen::Prompt,
        Stays,
        Same("clientscreens.py Input 위젯 직접 대조 — 정의 안 된 키(Home/End/←→/Shift+ \
         선택 포함)에 아무 일도 안 한다. `press_prompt` 에 커서·선택 상태를 얹어 그 넷을 \
         실제 편집으로 배선했다(pytmux-174)"),
        "한 줄 입력. `Home`·`End`·`←→`·`Shift+` 선택 키가 이제 **편집**(커서 이동·선택)이다 \
         — 종전엔 취소로 갔다",
    ),
    c(
        Screen::Confirm,
        Stays,
        Same("clientscreens.py ConfirmScreen.on_key 직접 대조 — esc·y/n·Enter·←→·Tab \
         밖의 키는 갈래가 없어 아무 일도 안 한다. `press_confirm` 을 그 다섯 갈래 + \
         기본 무시로 다시 짰다(pytmux-273 ③)"),
        "예/아니오. 기본이 '아니오'인 것도, **모르는 키를 무시하는 것도** 이제 정본과 같다 \
         — 종전엔 모르는 키가 전부 '아니오'로 닫혔다",
    ),
    c(
        Screen::Commands,
        Stays,
        Same("screen_key_conformance — 정본 `CommandListScreen.on_key` 를 AST 로 읽어 \
         눌러 봤다. **그 자를 세우자 셋이 갈려 있었고 같은 CL 에서 고쳤다**(pytmux-454): \
         제 것 아닌 키가 판을 닫았고(정본은 흘려보낸다) `home`·`end` 도 닫았다(정본은 \
         커서를 첫/마지막 줄로 옮긴다). `Enter` 는 정본이 도우미 안에서 조건부로 닫아 \
         (`close_maybe`) 대조에서 뺐다"),
        "명령 팔레트. **제 것 아닌 키가 이제 판을 안 닫고**(종전 Closes) `Home`·`End` 가 \
         커서를 옮긴다. 글자를 받는 방식도 정본과 같다 — 맨 앞 `:` 를 버리고 \
         (pytmux-175) 한글 자모를 QWERTY 로 되돌린다(pytmux-176, \
         `screens_tests.rs` 오라클)",
    ),
    c(
        Screen::Version,
        Closes,
        Same("clientscreens.py InfoScreen._NAV_KEYS 직접 대조 — `Keys` 와 같은 \
         `InfoScreen`. Home/End 를 커서 이동으로 배선했다(pytmux-273 ①)"),
        "읽는 판(서버·클라 판) — `Keys` 와 같은 `InfoScreen`, `Home`·`End` 도 이제 맞다",
    ),
    c(
        Screen::ShellOutput,
        Closes,
        Same("clientscreens.py InfoScreen._NAV_KEYS 직접 대조(같은 InfoScreen 계열, \
         pytmux-273 ①)"),
        "읽는 판(`run-shell` 결과) — 같은 `InfoScreen`, `Home`·`End` 도 이제 맞다",
    ),
    c(
        Screen::RestartCheck,
        Closes,
        Same("clientscreens.py InfoScreen._NAV_KEYS 직접 대조(같은 InfoScreen 계열, \
         pytmux-273 ①)"),
        "읽는 판(재시작 점검) — 같은 `InfoScreen`, `Home`·`End` 도 이제 맞다",
    ),
    c(
        Screen::MergeRemote,
        Stays,
        Same("screen_key_conformance — 정본 `MergeRemoteTabScreen.on_key` 를 AST 로 \
         읽어 눌러 봤다: `escape` 가 닫고 `h`/`v` 는 분할 방향만 바꾸며(판 유지) \
         그 밖은 흘려보낸다 — 셋 다 우리와 같다(pytmux-454)"),
        "원격 탭 합치기. `h`/`v` 도 제 것 아닌 키도 정본과 같다",
    ),
    c(
        Screen::Layouts,
        Stays,
        Same("screen_key_conformance — 정본 `ChooseLayoutScreen.on_key` 를 AST 로 읽어 \
         눌러 봤다: `escape` 만 닫고 그 밖은 흘려보낸다(pytmux-454)"),
        "레이아웃 프리셋. 제 것 아닌 키를 삼키는 것이 정본과 같다",
    ),
    c(
        Screen::Notices,
        Stays,
        Same("clientscreens.py NoticeHistoryScreen.on_key 직접 대조 — `Esc`·`c` 만 먹고 \
         그 밖의 키는 무시한다(이 축이 재는 F5 도 그 «그 밖»이다). `Screens::press` 의 \
         Notices 분기를 Esc 만 닫게 고쳤다(pytmux-273 ②)"),
        "지나간 알림. F5 같은 제 것 아닌 키는 이제 정본처럼 무시한다(고쳐졌다 — 종전엔 \
         아무 키나 닫았다). ⚠ `c`(전문 복사)·`Enter`(펼치기)는 **아직 없다** — 그 둘은 \
         이 축(닫기 여부) 밖의 별도 기능 공백으로 남는다",
    ),
    c(
        Screen::Menu,
        Stays,
        Same("screen_key_conformance — 정본 `MenuScreen.on_key` 를 AST 로 읽어 눌러 \
         봤다: `escape`·`f10` 둘만 먹고 그 밖은 흘려보낸다. **그 자를 세우자 갈려 \
         있었고 같은 CL 에서 고쳤다** — 우리는 제 것 아닌 키에 메뉴를 닫고 있었다 \
         (pytmux-454)"),
        "F10 메뉴. **제 것 아닌 키가 이제 메뉴를 안 닫는다**(종전 Closes) — 층을 \
         오르내리는 판이라 잘못 누른 글자 하나에 들어온 자리를 잃던 자리다. `←` 로 \
         그룹을 빠져나오는 것도 정본과 같다",
    ),
    c(
        Screen::Plugins,
        Stays,
        Same("clientscreens.py PluginManagerScreen.on_key 직접 대조 — `escape`·`space`·\
         `backspace`·**찍히는 글자 하나**만 `event.stop()` 하고 그 밖은 흘려보낸다\
         (이 축이 재는 F5 는 `event.character` 가 없어 그 «그 밖»이다). \
         `press_settings` 의 `_ => close_top()` 을 삼키기로 고쳤다(pytmux-374 ⑵)"),
        "플러그인 켜고끄기. `Enter` 가 안 닫는 것도, **제 것 아닌 키를 무시하는 것도** \
         이제 정본과 같다 — 종전엔 아무 키나 닫았다. ⚠ 정본의 **글자 치기 = 이름 \
         필터**(`#plgsearch`)는 아직 없다 — 그 둘은 이 축(닫기 여부) 밖의 기능 공백이다",
    ),
    c(
        Screen::PluginView,
        Stays,
        Same("clientscreens.py 목록/폼 화면(Textual ListView/OptionList) 직접 대조 — \
         정의 안 된 키에 아무 일도 안 한다. `press_list` 를 Esc 만 닫게, 그 밖은 삼키게 \
         고쳤다(pytmux-181)"),
        "플러그인이 준 판(`mdir`·`claude-settings`). 목록·폼(`is_list`)에서 이제 \
         **Esc 로만 닫힌다** — 종전엔 아무 키나 닫았다. ★ 같은 변형의 **글 판**\
         (`kind:\"text\"` · usage limit)의 스크롤 상한도 고쳤다 — 렌더 쪽에서 내용 \
         줄 수로 자른다(pytmux-184 ⑵)",
    ),
    c(
        Screen::Options,
        Stays,
        Same("screen_key_conformance — 정본 `CommandOptionsScreen.on_key` 를 AST 로 \
         읽어 눌러 봤다: `escape`·`enter` 가 닫고 `←→` 는 값을 돌리며(판 유지) 그 밖은 \
         흘려보낸다. **그 자를 세우자 갈려 있었고 같은 CL 에서 고쳤다** — 우리는 제 것 \
         아닌 키에 폼을 닫고 있었다(pytmux-454)"),
        "인자 폼. ↑↓ 줄 · ←→ 값에 더해 **제 것 아닌 키가 이제 폼을 안 닫는다** \
         (종전 Closes)",
    ),
    c(
        Screen::Hooks,
        Closes,
        Same("clientscreens.py InfoScreen._NAV_KEYS 직접 대조(같은 InfoScreen 계열, \
         pytmux-273 ①)"),
        "읽는 판(걸어 둔 훅) — 같은 `InfoScreen`, `Home`·`End` 도 이제 맞다",
    ),
    c(
        Screen::InfoTabs,
        Closes,
        Same("screen_key_conformance — 정본 `InfoTabsScreen.on_key` 를 AST 로 읽어 \
         눌러 봤다: `escape` 가 닫고 ←→·Tab·↑↓·PgUp/PgDn·Home/End 는 판을 안 닫으며 \
         **제 것 아닌 키는 닫는다**(꼬리의 `self.dismiss(None)`) — 전부 우리와 같다. \
         `enter`·`space` 는 정본이 「고른 줄이 동작 단추면 돌리고 아니면 닫는다」라 \
         조건부(`close_maybe`)여서 대조에서 뺐고, 그 판정의 자리는 우리도 뷰다 \
         (`ScreenKey::Applied`) — pytmux-454"),
        "탭 있는 읽기 판 + 고를 수 있는 동작 줄. ←→ 는 **탭과 닫기 `[x]` 를 한 바퀴** 돌고 \
         (정본 `_sel` 의 `% (n+1)`) ↑↓ 는 **항목 커서**이며 `Enter` 는 동작 줄이면 그것을 \
         돌리고(판 유지) 아니면 닫는다 — 셋 다 정본 `InfoTabsScreen.on_key` 를 열어 맞췄다 \
         (pytmux-373 ⑵⑶⑷). 종전에는 ↑↓ 가 글 굴리기였고 `[x]` 가 아예 없었다. \
         `Home`·`End`·`space` 도 정본과 같은 팔에 뒀다. \
         ⚠ **한 칸 남았다 — `PageUp`/`PageDown` 의 «몇 줄»은 안 쟀다**: 정본은 5줄이고 \
         우리는 판 공통 상수 `PAGE`(10)다. 그 상수를 이 판만 다르게 하는 것이 옳은지가 \
         아직 판단이 안 서서 안 건드렸다. \
         ⛔ **usage limit 팝업(pytmux-184)은 이 판이 아니다** — \
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
        Stays,
        Same("clientscreens.py SettingsScreen.on_key 직접 대조 — `escape`·`enter`·\
         `left`/`right`·`tab`/`shift+tab` **넷만** `event.stop()` 하고 그 밖의 키는 \
         `ListView` 로 흘러가 판을 안 닫는다. `press_settings` 를 그 규약으로 고쳤다\
         (pytmux-374 ⑵). ⚠ **한 칸 갈린다** — 정본에서 `Home`·`End`·`PageUp`·`PageDown` \
         은 `ScrollableContainer` 기본 바인딩이라 **글만 굴리고 커서는 제자리**인데, \
         우리 판에는 커서와 따로 사는 스크롤이 없어 커서를 옮긴다(그 사유는 \
         `press_settings` 머리말)"),
        "설정. **제 것 아닌 키가 판을 안 닫고**(고쳐졌다 — pytmux-273 이 `press_list` 에 \
         한 것과 같은 처방) `Home`·`End`·`PageUp`·`PageDown` 이 목록을 옮긴다. \
         네이티브 위젯으로 다시 그리는 일은 pytmux-182 다(그것은 이 축이 아니라 그림 축)",
    ),
    c(
        Screen::Summary,
        Closes,
        Allowed(Ground::NativeOnly),
        "블록·Claude 요약 판 — 정본에 짝이 없다(§10-21ⓓ)",
    ),
    c(
        Screen::SearchResults,
        Stays,
        Same("clientscreens.py SearchResultsScreen.on_key(3408~3410) 직접 대조 — \
         `escape` 만 먹고(`event.stop()`) 그 밖은 `ListView` 기본 처리로 흘러 아무 일도 \
         안 한다. `press_list` 를 고쳐 이 판도 같이 움직였다(pytmux-181·273)"),
        "전역 검색 결과(pytmux-27). 정본 `SearchResultsScreen.on_key`(clientscreens.py \
         3408~3410)는 `escape` 만 먹고(`event.stop()`) 그 밖의 키는 `ListView` 기본 \
         처리로 흘러 **아무 일도 안 한다**(판이 그대로 있다) — `press_list` 수정으로 \
         우리도 이제 같다(종전엔 아무 키나 닫았다)",
    ),
    c(
        Screen::Autoresume,
        Closes,
        Same("clientconn.py open_autoresume_info 직접 대조 — 정본은 범용 InfoScreen 을 hide_key=a 로 띄운다. 곧 `a` 는 뒤집고 닫고, 그 밖의 키는 InfoScreen 규약대로 닫힌다(_NAV_KEYS 넷 + Home/End 만 스크롤). 우리도 같은 갈래다"),
        "자동 재개 설명 + 켜고 끄기(pytmux-183). 좌하단 `[자동재개]` 표식을 눌러 연다 — `a` 가 뒤집고 닫는 것까지 정본과 같다",
    ),
    c(
        Screen::Cursor,
        Stays,
        Allowed(Ground::NativeOnly),
        "커서 판(pytmux-375) — 모양·두께·색·깜빡임·주기 다섯과 견본 한 칸. 정본에 짝이 \
         없다: 저쪽 커서는 **호스트 단말의 하드웨어 커서**라 그 값을 가질 자리가 아예 \
         없다(pytmux-161 이 설정 넷에 대해 적은 것과 같은 근거). 그래서 대조할 `on_key` \
         가 없고, 이 판이 **새로 만들어진** 것인 만큼 계약은 처음부터 지켜서 짓는다 — \
         손을 설정 판과 같은 `press_settings` 에 태워 **제 것 아닌 키가 판을 안 닫게** \
         했다(pytmux-374 ⑵·273 이 걸린 그 함정). 갈리는 것은 `Tab` 하나뿐이다(이 판에는 \
         분류가 없어 삼킨다)",
    ),
    c(
        Screen::DebugStats,
        Closes,
        Same("screen_key_conformance — 정본은 이 표를 범용 `InfoScreen` 에 띄운다 \
         (`clientcmd.py` 의 `push_screen(InfoScreen(clientdiag.render(...), \
         title=debug-stats))`) — 아무 키나 닫고 `↑↓`·`PgUp/PgDn`·`Home/End` 가 \
         굴린다. `canon_class` 가 `InfoScreen` 이라 그 시험이 이 판도 눌러 본다 \
         (pytmux-457)"),
        "런타임 계측 판. **손은 정본과 같고 항목이 갈린다** — 저쪽은 파이썬 힙·GC \
         세대를 재고 이쪽은 그린 프레임·프레임 시간·글리프 캐시·씬 원소·큐 깊이·RTT 를 \
         잰다(런타임이 다르다). 그 갈림은 대장 ⓑ 한 줄이고, 이 축이 재는 「같게 구나」는 \
         갈리지 않는다",
    ),
];

/// 지금 점수. **양방향 래칫**이다 — 늘어도 줄어도 여기를 고쳐야 한다(`parity.rs` 규칙 2).
///
/// `(같다, 허용된 갈림, 결함, 못 쟀다)`.
// 24→24 · 허용 3→2: `ClaudeDetail` 줄이 사라졌다(pytmux-468 걸음 4). 그 판은 「정본에
// 짝이 없는 우리 것」이라 `Allowed(NativeOnly)` 였는데, 정본이 그것을 갖게 되면서
// (`:claude-detail`) 우리 쪽 전용 화면을 걷었다 — 남은 것은 `esc v` 라는 **지름길**이고
// 그건 화면이 아니라 키라 이 축이 아니라 갈림 대장이 센다.
static SCORE: (usize, usize, usize, usize) = (24, 2, 0, 0);

/// ⛔ **이 수는 올리지 않는다**(규칙 4). **지금은 0 이다**(pytmux-454).
///
/// 못 잰 줄이 여덟이던 것은 이 축을 세우기 전의 빚이었다. 그 빚이 **새 화면의 면허가
/// 되면** 이 표는 아무 일도 안 한다 — 화면을 더하는 사람은 `Unmeasured` 로 적고
/// 지나가면 되고, 그러면 pytmux-185 가 막으려던 바로 그 재생산이 표 안에서 일어난다.
///
/// 0 이 됐다는 것은 **새 화면에 「못 쟀다」로 지나가는 길이 아예 없다**는 뜻이다 —
/// 정본에 짝이 있으면 `screen_key_conformance` 가 재고, 없으면 `Allowed(NativeOnly)`
/// 여야 하며 그것은 `base` 의 `canon_class` 가 동의해야 한다.
const UNMEASURED_CEILING: usize = 0;

/// 재는 데 쓰는 **제 것 아닌 키**. F5 를 고른 이유는 어느 판도 F 키를 자기 것이라고
/// 적지 않았기 때문이다(스펙이 F 키를 쓰는 플러그인 판은 뷰가 먼저 가로챈다 — 이 축은
/// core 의 라우팅을 잰다).
const STRAY: Key = Key::Function(5);

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

/// 붙여넣기가 **어디로 가나** — `paste-clipboard` 의 계약 절반(pytmux-159·364).
///
/// # 왜 여기인가
///
/// 이 축이 재는 질문(「같게 구나」)의 붙여넣기판이다. 정본은 작성창이 떠 있으면
/// `_active_compose_screen` 으로 라우팅해 **작성 버퍼**에 넣고, 없으면 활성 패널에 넣는다
/// (`client.py::_do_paste_clipboard`). 갈리면 증상이 고약하다 — 팝업을 띄운 채 붙여넣은
/// 글이 **뒤 셸에 찍힌다**.
///
/// ⚠ **키 판정(`Ctrl+V` 가 붙여넣기인가)은 여기서 못 잰다.** 그 판정은 창 계층 위에 있어
/// (`gui::SessionView::is_paste_chord`) 이 크레이트에서 안 보인다 — 그쪽은 `gui` 의
/// `plain_ctrl_v_asks_for_a_paste_just_like_the_canon` 이 순수 함수로 잰다. 여기서 재는
/// 것은 **판정 뒤의 라우팅**이고, 둘이 합쳐야 한 계약이다.
#[test]
fn a_paste_lands_in_the_compose_buffer_while_it_is_open() {
    // ⑴ 작성창이 떠 있으면 그 버퍼다.
    let mut screens = opened(Screen::Compose);
    assert!(
        screens.paste_into_compose("붙인 글"),
        "작성창이 떠 있는데 붙여넣기가 그 버퍼로 안 갔다 — 그 글은 뒤 셸에 찍힌다"
    );
    assert!(
        screens.editor().map(|e| e.text()).unwrap_or_default().contains("붙인 글"),
        "작성창이 받았다고 했는데 버퍼에 없다"
    );

    // ⑵ 팔레트가 **작성창 위에** 떠 있어도 작성창이다(정본은 스택 어디서든 찾아 넣는다 —
    //    `esc :` 로 팔레트를 열고 `paste-clipboard` 를 치는 그 동선이 요점이다).
    screens.open_palette();
    assert_eq!(screens.top(), Some(Screen::Commands));
    assert!(
        screens.paste_into_compose("둘째"),
        "팔레트가 위에 떠 있다고 붙여넣기가 작성창을 놓쳤다"
    );

    // ⑶ 작성창이 없으면 **화면이 안 받는다** — 그래야 뷰가 패널로 보낸다.
    let mut none = Screens::default();
    assert!(
        !none.paste_into_compose("패널로 갈 글"),
        "작성창이 없는데 화면이 붙여넣기를 삼켰다 — 그러면 패널에 아무것도 안 들어간다"
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
