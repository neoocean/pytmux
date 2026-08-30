//! 캔버스 **밖으로 나가는 포커스** — 탭바와 하단 배지(패리티 `e_up`·`e_tb`·`e_down`).
//!
//! # 왜 별도 모듈인가
//!
//! 지금까지 이 클라의 키는 전부 "패널 아니면 화면"이었다. 화면(팔레트·설정)은 뜨는 순간
//! 캔버스를 **대신** 그리고 모든 키를 가져간다. 크롬 포커스는 그 둘 어디에도 안 맞는다 —
//! 캔버스는 그대로 있고, 포커스만 그 **밖**(위의 탭바 · 아래의 배지)으로 나간다.
//!
//! 파이썬 클라가 세워 둔 규칙 하나로 요약된다: **포커스는 누른 방향으로 캔버스를 떠나
//! 그쪽에 있는 크롬으로 간다.** 최상단 패널에서 `↑` 면 위의 탭바로, 최하단에서 `↓` 면
//! 아래의 배지로. 그래서 진입 판정에 "위/아래에 다른 패널이 있는가"가 필요하다.
//!
//! # 왜 상태를 여기 두나
//!
//! 포커스가 어디에 있고 무엇이 골라져 있는지는 **두 뷰가 같아야 하는 것**이다. 뷰가 각자
//! 들면 GUI 에서는 `[+]` 가 순환에 끼고 TUI 에서는 안 끼는 식으로 갈린다. 대신 판정에
//! 필요한 사실(패널이 위에 있나 · 탭이 몇 개인가 · 배지가 무엇이 떠 있나)은 이 크레이트가
//! 알 수 없으므로 [`ChromeCtx`] 로 **받는다** — core 는 의존이 0개라는 계약 그대로다.

use crate::{Action, Key, Mods};

/// 포커스가 지금 어디 있나.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ChromeFocus {
    /// 캔버스 안(평소). 방향키는 패널 이동이다.
    #[default]
    Pane,
    /// 위쪽 탭바.
    TabBar,
    /// 아래쪽 배지 줄.
    Badges,
}

/// 탭바 위에서 고를 수 있는 자리 — 탭들 다음에 `[+]`, `[x]` 가 온다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TabSpot {
    /// [`ChromeCtx::tabs`] 의 몇 번째 탭인가.
    Tab(usize),
    /// 새 탭 `[+]`.
    New,
    /// 지금 탭 닫기 `[x]`.
    Close,
}

/// 하단 배지 한 칸.
///
/// 파이썬 상태줄의 버튼 동선(`_status_buttons`)과 **같은 자리**지만 목록은 우리 것이다 —
/// 저쪽의 `model`·`usage`·`rec`·`perm` 은 플러그인이 채우는 칸이라 우리에게는 없다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Badge {
    /// 지나간 알림 목록.
    Notices,
    /// 서버 정보로 가는 입구. **늘 있다**(파이썬 상태줄의 호스트 버튼과 같은 자리).
    ///
    /// 원격 탭을 보고 있으면 뷰가 그 호스트 이름을 뒤에 붙인다 — 그래서 낱말은 `서버`
    /// 하나지만 화면에는 `서버 box1` 로 보인다. 종전에는 **원격일 때만** 떴는데, 그러면
    /// 로컬에서 서버 정보로 가는 배지 동선이 아예 없다(파이썬에는 늘 있다).
    Host,
    /// 시계 오버레이 토글.
    Clock,
    /// 달력 오버레이 토글.
    Calendar,
}

impl Badge {
    /// 배지에 적을 낱말. 개수 같은 곁가지는 뷰가 뒤에 붙인다.
    pub fn label(self) -> &'static str {
        crate::i18n::t(match self {
            Badge::Notices => "알림",
            Badge::Host => "서버",
            Badge::Clock => "시계",
            Badge::Calendar => "달력",
        })
    }

    /// `Enter` 가 일으키는 일.
    pub fn action(self) -> Action {
        match self {
            Badge::Notices => Action::ShowNotices,
            // 파이썬과 **같은 것**을 연다 — 서버 정보 **탭**이다
            // (`show_status_tabs(initial=2)`). 종전에는 그 탭 묶음이 없어 버전 화면으로
            // 대신했다.
            Badge::Host => Action::ShowInfoTabs,
            Badge::Clock => Action::ToggleClock,
            Badge::Calendar => Action::ToggleCalendar,
        }
    }
}

/// 크롬이 키를 읽으려면 알아야 하는 사실들 — 전부 **바깥**(proto·뷰)이 안다.
#[derive(Debug, Clone, Copy)]
pub struct ChromeCtx<'a> {
    /// 활성 패널 **위쪽**(같은 열 범위)에 다른 패널이 있나. 있으면 `↑` 는 패널 이동이다.
    pub pane_above: bool,
    /// 아래쪽에 다른 패널이 있나.
    pub pane_below: bool,
    /// 탭마다의 **표시 번호**(1-based, 시각 순서) — 리스트와 같은 순서로.
    ///
    /// ⚠ index 가 아니라 **번호**다(2026-08-01 에 바꿨다). 종전에는 index 를 담아
    /// [`Action::SelectTab`] 에 그대로 실었는데, 같은 액션이 숫자키 경로에서는 표시
    /// 번호를 실어 와서 **한 액션이 두 뜻**을 갖고 있었다 — 그래서 숫자키가 한 칸 밀린
    /// 탭을 골랐다. 뜻은 하나(`SelectTab` = 표시 번호)로 두고, 번호→탭 해석은
    /// `proto::command::action_to_command_with_tabs` 한 곳이 한다.
    pub tabs: &'a [usize],
    /// 활성 탭이 [`tabs`](ChromeCtx::tabs) 의 몇 번째인가(진입 시 여기서 시작한다).
    pub active: usize,
    /// 지금 떠 있는 배지들. 비면 `↓` 로 내려갈 곳이 없다.
    pub badges: &'a [Badge],
}

/// 마우스가 크롬에서 누른 것 하나 — 뷰가 히트테스트로 알아낸 **자리**다.
///
/// 어느 픽셀/칸이 어느 자리인지는 뷰(레이아웃)만 알지만, 그 자리를 누르면 무슨 일이
/// 나는지는 **한 벌**이어야 한다([`click`]) — 뷰가 각자 정하면 GUI 에서는 탭 클릭이
/// 전환인데 TUI 에서는 아닌 식으로 갈린다(이 모듈이 존재하는 이유와 같다).
/// **시스템 표식** — 모르고 두면 입력·동작이 달라지는 상태(줌·동기화·자동재개·
/// 프롬프트클리어). 정본의 좌하단 클러스터에 앉는다(pytmux-183).
///
/// # 왜 낱말이 아니라 뜻인가
///
/// 종전에는 `Vec<&'static str>`(이미 번역된 낱말)이었다. 그러면 뷰가 **어느 칩이 무엇인지**
/// 알 길이 글자 비교뿐이고, 그 비교는 로케일이 바뀌는 순간 조용히 틀린다 — 자동재개
/// 칩에만 클릭을 붙이려는데 그 판정을 글자로 할 수는 없다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SysBadge {
    /// 활성 패널이 창 전체를 덮고 있다.
    Zoom,
    /// **입력이 복제되는 상태** — 표식 중 가장 위험하다(모르고 치면 모든 패널에서
    /// 같은 명령이 돈다).
    Sync,
    /// 토큰 한도에 걸린 뒤 서버가 대화를 이어 붙인다. **꺼져 있을 때가 기본**이라
    /// 켜져 있다는 사실이 보여야 한다.
    AutoResume,
    /// 완료마다 패널이 문서화 + `/clear` 를 돌린다.
    PromptClear,
}

impl SysBadge {
    /// 칩에 적을 낱말. 두 뷰가 같은 낱말을 쓰도록 여기 한 곳에서 만든다.
    pub fn label(self) -> &'static str {
        crate::i18n::t(match self {
            SysBadge::Zoom => "[줌]",
            SysBadge::Sync => "[동기화]",
            SysBadge::AutoResume => "[자동재개]",
            SysBadge::PromptClear => "[프롬프트클리어]",
        })
    }

    /// 눌렀을 때 일어나는 일 — 없으면 **표식일 뿐 버튼이 아니다**.
    ///
    /// 정본이 클릭존을 둔 것은 `AR` 하나뿐이다(`clientwidgets.py` 의 `_ar_zone` →
    /// `open_autoresume_info`). 나머지에 우리 마음대로 클릭을 붙이면 **정본에 없는
    /// 조작 표면**을 GUI 가 먼저 만드는 것이 된다(CLAUDE.md).
    ///
    /// ⛔ 여는 것이 **토글이 아니라 판**인 것도 정본 그대로다: 눌러서 바로 뒤집으면
    /// 「모르고 켜 두는 상태」를 모르고 **꺼 버리는** 자리가 하나 더 생긴다. 정본은
    /// 설명을 보여 주고 `a` 로 뒤집게 한다.
    pub fn action(self) -> Option<Action> {
        match self {
            SysBadge::AutoResume => Some(Action::ShowAutoresume),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClickTarget {
    Spot(TabSpot),
    Badge(Badge),
    /// 탭바 넘침 화살표(`◀`/`▶` — G9x). **뷰 로컬 스크롤**이라 액션이 아니다 —
    /// [`click`] 은 `None` 을 돌려주고 뷰가 자기 스크롤을 옮긴다(파이썬 `scroll_by`).
    TabScroll { left: bool },
    /// 아래 요약 구역의 머리줄(§10-20ⓔ). **뷰 로컬 접힘**이라 액션이 아니다 —
    /// 서버에 보낼 명령이 없고(정본에는 이 구역 자체가 없다) 뷰가 자기 상태를 뒤집는다.
    /// 다만 그 결과로 **크롬 높이가 바뀌어 서버에 새 크기가 나간다** — `TabScroll` 과
    /// 달리 화면 밖으로 파급이 있는 유일한 뷰 로컬 클릭이다.
    /// 하단 한 줄의 **닫기**(§10-21ⓦ⑵).
    ///
    /// 액션 표를 안 지난다 — 지우는 것은 **이 클라의 표시**이고 서버도 정본도 그런
    /// 명령을 갖지 않는다(같은 이유로 종전 `FooterFold` 도 여기 있었다).
    DismissMessage,
    /// 플러그인이 낸 상태줄 표식 `i` 번째(pytmux-20).
    ///
    /// 액션 표를 안 지난다 — **무엇이 열리는지는 서버가 정한다**(그 표식의 `do` 가
    /// 플러그인 명령 이름을 싣는다). 우리 액션 열거형에 넣으면 그 표가 서버와 갈리고,
    /// 플러그인을 지워도 이름이 여기 남는다(INV5 가 빚이라 부르는 것).
    ///
    /// 자리로 가리키는 이유: 이름은 `String` 이라 이 열거형의 `Copy` 를 깬다. 자리는
    /// 그린 프레임 안의 것이고, 뷰가 누른 즉시 그 프레임의 목록에서 되찾는다.
    PluginBadge(usize),
    /// 상태줄의 세션 이름(`#S`) 자리 — 누르면 **거기가 입력칸이 된다**(pytmux-3).
    ///
    /// 액션 표를 안 지난다. 여는 것이 화면이 아니라 **이 클라의 편집 상태**라 서버에
    /// 보낼 명령이 없다(보낼 것은 다 쓰고 `Enter` 를 눌렀을 때의 `rename_session`
    /// 하나뿐이다) — `DismissMessage` 와 같은 부류다.
    SessionName,
    /// 편집 **중**인 세션 이름의 `i` 번째 글자 — 누르면 커서만 그리로 간다.
    ///
    /// 파이썬은 이 자리를 절대 x 로 받아 셀 폭으로 되짚지만(`session_edit_cursor_at`),
    /// GUI 에는 셀 격자가 없다 — 글자마다 자기 자리를 갖는 편이 같은 일을 **자릿수
    /// 계산 없이** 한다(한글에서 폭으로 되짚다 어긋나는 부류가 아예 없어진다).
    SessionCursor(usize),
    /// 시스템 표식 하나(pytmux-183) — 지금은 **자동재개만** 눌린다([`SysBadge::action`]).
    SysBadge(SysBadge),
}

/// 클릭 하나를 액션으로 — **Enter 와 같은 길**이다(파이썬 `on_mouse_down`/`_hit` 도
/// 같은 명령을 보낸다: 탭=`select_window` · `[+]`=`new_window`).
///
/// `[x]` 가 [`Action::KillTab`] 인 것도 Enter 와 같다 — 그 액션은 확인 화면을 지나므로
/// 클릭 한 번에 탭이 사라지지 않는다. 범위 밖 인덱스는 `None`(낡은 존을 눌렀다 —
/// 렌더와 클릭 사이에 탭이 줄어든 경우다).
pub fn click(target: ClickTarget, ctx: &ChromeCtx) -> Option<Action> {
    Some(match target {
        ClickTarget::Spot(TabSpot::Tab(i)) => Action::SelectTab(*ctx.tabs.get(i)? as u8),
        ClickTarget::Spot(TabSpot::New) => Action::NewTab,
        ClickTarget::Spot(TabSpot::Close) => Action::KillTab,
        ClickTarget::Badge(badge) => badge.action(),
        // 표식일 뿐인 것(줌·동기화·프롬프트클리어)은 `None` 이라 여기서 빠진다 —
        // 정본이 클릭존을 둔 것은 자동재개 하나다.
        ClickTarget::SysBadge(badge) => badge.action()?,
        ClickTarget::TabScroll { .. }
        | ClickTarget::DismissMessage
        | ClickTarget::PluginBadge(_)
        | ClickTarget::SessionName
        | ClickTarget::SessionCursor(_) => return None,
    })
}

/// 크롬이 키를 먹고 난 결과.
#[derive(Debug, Clone, PartialEq)]
pub enum ChromeKey {
    /// 포커스만 움직였다 — 다시 그리기만 한다.
    Redraw,
    /// 액션을 실행하고 **크롬에 머문다**(탭 옮기기·새 탭 — 연속 조작이 뜻이 있는 것들).
    Stay(Action),
    /// 액션을 실행하고 크롬과 esc 모드에서 **함께** 나온다.
    Done(Action),
    /// 아무것도 실행하지 않고 크롬과 esc 모드에서 나온다(`Esc`).
    Leave,
}

/// 크롬 포커스의 상태. [`ModeState`](crate::keys::ModeState) 와 나란히 뷰가 든다.
#[derive(Debug, Clone, Copy, Default)]
pub struct Chrome {
    focus: ChromeFocus,
    /// 탭바 위의 자리 — 탭들 + `[+]` + `[x]` 를 이은 목록의 색인.
    spot: usize,
    badge: usize,
}

/// 탭바 자리 목록의 길이 — 탭들 뒤에 `[+]` 와 `[x]` 둘이 더 있다.
fn spots(tabs: usize) -> usize {
    tabs + 2
}

fn spot_at(tabs: usize, i: usize) -> TabSpot {
    if i < tabs {
        TabSpot::Tab(i)
    } else if i == tabs {
        TabSpot::New
    } else {
        TabSpot::Close
    }
}

impl Chrome {
    pub fn focus(self) -> ChromeFocus {
        self.focus
    }

    /// 포커스가 캔버스 밖에 있나(뷰가 강조를 그릴지 정하는 값).
    pub fn is_active(self) -> bool {
        self.focus != ChromeFocus::Pane
    }

    /// 포커스를 캔버스로 되돌린다.
    ///
    /// **esc 모드가 풀리면 반드시 불러야 한다.** 안 부르면 다음에 esc 를 눌렀을 때 지난번
    /// 포커스가 그대로 살아나 방향키가 패널이 아니라 탭바를 움직인다.
    pub fn reset(&mut self) {
        self.focus = ChromeFocus::Pane;
    }

    /// 탭바에서 지금 골라진 자리. 탭바 포커스가 아니면 `None`.
    pub fn spot(self, ctx: &ChromeCtx) -> Option<TabSpot> {
        (self.focus == ChromeFocus::TabBar)
            .then(|| spot_at(ctx.tabs.len(), self.spot.min(spots(ctx.tabs.len()) - 1)))
    }

    /// 하단에서 지금 골라진 배지. 배지 포커스가 아니면 `None`.
    pub fn badge(self, ctx: &ChromeCtx) -> Option<Badge> {
        (self.focus == ChromeFocus::Badges)
            .then(|| ctx.badges.get(self.badge).copied())
            .flatten()
    }

    /// esc 모드에서 키 하나를 크롬이 먹을지 정한다.
    ///
    /// `None` 이면 크롬의 키가 아니다 — 호출부가 평소 esc 모드 표로 넘긴다.
    pub fn press(&mut self, ctx: &ChromeCtx, key: Key, mods: Mods) -> Option<ChromeKey> {
        // ★ 파이썬이 포커스 동선 **앞에서** 처리하는 키들은 여기서 안 먹는다 — esc 모드
        // 어디서든 통해야 하는 것들이다(번호로 탭 전환 · 리터럴 백틱 · Shift+ESC).
        // 포커스 중이라고 이것들이 막히면 "탭바에 올라가면 번호 전환이 안 된다"가 된다.
        let pass_through = matches!(key, Key::ShiftEscape | Key::Char('`'))
            || matches!(key, Key::Char(c) if c.is_ascii_digit());
        if pass_through {
            return None;
        }
        // Ctrl/Alt 조합도 크롬의 것이 아니다 — 크롬에 그 조합이 하나도 없어서, 먹으면
        // 포커스 중에만 조용히 사라지는 키가 생긴다.
        if mods != Mods::NONE {
            return None;
        }
        match self.focus {
            ChromeFocus::Pane => self.enter(ctx, key),
            ChromeFocus::TabBar => Some(self.press_tab_bar(ctx, key)),
            ChromeFocus::Badges => Some(self.press_badges(ctx, key)),
        }
    }

    /// 캔버스 가장자리에서 크롬으로 나가는 판정.
    fn enter(&mut self, ctx: &ChromeCtx, key: Key) -> Option<ChromeKey> {
        match key {
            // 위에 패널이 더 있으면 `↑` 는 **패널 이동**이다 — 그 자리를 뺏으면 분할된
            // 창에서 위 패널로 못 간다.
            Key::Up if !ctx.pane_above && !ctx.tabs.is_empty() => {
                self.focus = ChromeFocus::TabBar;
                self.spot = ctx.active.min(ctx.tabs.len() - 1);
                Some(ChromeKey::Redraw)
            }
            // 배지가 하나도 없으면 내려갈 곳이 없다 — 그때는 `None` 이라 평소의 패널
            // 이동으로 떨어진다(파이썬 `_enter_status_focus` 가 False 를 돌려주는 자리).
            Key::Down if !ctx.pane_below && !ctx.badges.is_empty() => {
                self.focus = ChromeFocus::Badges;
                self.badge = 0;
                Some(ChromeKey::Redraw)
            }
            _ => None,
        }
    }

    fn press_tab_bar(&mut self, ctx: &ChromeCtx, key: Key) -> ChromeKey {
        let n = ctx.tabs.len();
        let total = spots(n);
        self.spot = self.spot.min(total - 1);
        match key {
            // ★ 고른 탭을 옮긴다 — **자리도 같이 따라간다.** 안 따라가면 한 번 옮긴 뒤
            // 손 아래의 탭이 바뀌어, 계속 누르면 서로 다른 탭이 하나씩 밀린다.
            Key::ShiftLeft | Key::ShiftRight => {
                let TabSpot::Tab(i) = spot_at(n, self.spot) else {
                    return ChromeKey::Redraw;
                };
                let left = key == Key::ShiftLeft;
                // 끝에서는 안 움직인다(순환하지 않는다) — 파이썬도 같다. 순환시키면
                // 맨 앞 탭에 Shift+← 한 번이 탭을 맨 뒤로 던진다.
                if (left && i == 0) || (!left && i + 1 >= n) {
                    return ChromeKey::Redraw;
                }
                let to = if left { i - 1 } else { i + 1 };
                self.spot = to;
                ChromeKey::Stay(Action::MoveTabAt {
                    from: i as u8,
                    to: to as u8,
                })
            }
            // `↑` 도 왼쪽이다(파이썬과 같다) — 탭바는 한 줄이라 위아래가 따로 없다.
            Key::Left | Key::Up => {
                self.spot = (self.spot + total - 1) % total;
                ChromeKey::Redraw
            }
            Key::Right => {
                self.spot = (self.spot + 1) % total;
                ChromeKey::Redraw
            }
            Key::Enter => match spot_at(n, self.spot) {
                TabSpot::Tab(i) => ChromeKey::Done(Action::SelectTab(ctx.tabs[i] as u8)),
                TabSpot::New => ChromeKey::Done(Action::NewTab),
                TabSpot::Close => ChromeKey::Done(Action::KillTab),
            },
            // 새 탭은 **머문다** — 여러 개를 연달아 만드는 것이 자연스럽다(파이썬과 같다).
            Key::Char('+') | Key::Char('a') => ChromeKey::Stay(Action::NewTab),
            // ⚠ 닫는 것은 고른 탭이 아니라 **지금 탭**이다. 파이썬도 그렇다
            // (`confirm_kill_tab` 이 활성 탭을 본다) — 원격 탭이면 분리로 갈라지는 판정이
            // 활성 탭 기준이라 그 자리를 옮기면 규칙이 둘로 갈린다.
            Key::Char('x') | Key::Char('d') | Key::Delete => match spot_at(n, self.spot) {
                TabSpot::Tab(_) => ChromeKey::Done(Action::KillTab),
                _ => ChromeKey::Redraw,
            },
            // 내려오면 패널로 돌아오되 **esc 모드는 유지**한다 — 연속 조작이 끊기지 않는다.
            Key::Down => {
                self.focus = ChromeFocus::Pane;
                ChromeKey::Redraw
            }
            Key::Escape => {
                self.focus = ChromeFocus::Pane;
                ChromeKey::Leave
            }
            // 표에 없는 키는 **삼킨다**(파이썬과 같다). 포커스 중에 엉뚱한 명령이 도는
            // 것이 아무 일도 안 일어나는 것보다 놀랍다.
            _ => ChromeKey::Redraw,
        }
    }

    fn press_badges(&mut self, ctx: &ChromeCtx, key: Key) -> ChromeKey {
        let n = ctx.badges.len();
        self.badge = self.badge.min(n - 1);
        match key {
            Key::Left => {
                self.badge = (self.badge + n - 1) % n;
                ChromeKey::Redraw
            }
            Key::Right => {
                self.badge = (self.badge + 1) % n;
                ChromeKey::Redraw
            }
            Key::Enter => {
                let badge = ctx.badges[self.badge];
                self.focus = ChromeFocus::Pane;
                ChromeKey::Done(badge.action())
            }
            Key::Up => {
                self.focus = ChromeFocus::Pane;
                ChromeKey::Redraw
            }
            Key::Escape => {
                self.focus = ChromeFocus::Pane;
                ChromeKey::Leave
            }
            // 파이썬은 여기서 **포커스를 풀고 키를 버린다**(`else: _exit_status_focus()`).
            // 탭바와 다른 이유: 배지 줄은 들어갈 일이 드물어, 모르는 키를 삼키면 갇힌 것
            // 처럼 보인다.
            _ => {
                self.focus = ChromeFocus::Pane;
                ChromeKey::Redraw
            }
        }
    }
}

// ── 탭 드래그(G9v — 파이썬 `TabBar.on_mouse_up` 동형) ─────────────────────────

/// 드래그 판정에 필요한 탭 하나의 사실.
#[derive(Debug, Clone, Copy)]
pub struct DragTab {
    pub remote: bool,
    pub pinned: bool,
}

/// 놓은 자리 — **어디**인지는 뷰(히트테스트)가 알아낸다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DragTarget {
    /// 탭바의 다른(또는 같은) 탭 위.
    Tab(usize),
    /// 캔버스(콘텐츠) 위 — 커서 아래 패널과, 그 패널의 어느 쪽인가.
    Content { pane: i64, horizontal: bool },
    /// 그 외(여백·`[+]`·`[x]` 등).
    Other,
}

/// 놓았을 때 일어나는 일. 명령으로 옮기는 것은 뷰다(join 은 명령이 **둘**이라 —
/// `select_pane_id` + `join_pane` — Action 하나로 접을 수 없다. MergeRemote 선례).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DragDrop {
    /// 제자리(또는 무의미한 자리) — 클릭과 같다: 그 탭으로 전환.
    Select(usize),
    /// 재정렬(`move_tab`).
    Reorder { from: usize, to: usize },
    /// 핀 경계를 넘겨 놓았다 — 그 탭의 고정 토글(`set_pinned {index,value}`).
    SetPin { index: usize, on: bool },
    /// 캔버스에 놓았다 — 그 패널에 분할로 합치기(`select_pane_id` + `join_pane`).
    Join { pane: i64, src: usize, horizontal: bool },
}

/// 파이썬 `on_mouse_up` 의 판정표 그대로:
///
/// 1. 콘텐츠 드롭(+로컬 소스) → 합치기. 원격 소스는 호스트 짝이 맞아야 하는데
///    (`_drag_merge_ok`) 그 대조가 없으므로 **로컬만** — 틀린 명령을 보내는 것보다
///    안 보내는 편이 낫다(원격은 `merge-remote-tab` 명령이 그 자리다).
/// 2. 핀 상태가 **다른** 탭 위(둘 다 로컬) → 소스의 핀 토글(경계 넘김 = §12 ②).
/// 3. 다른 탭 위(둘 다 로컬·핀 같음) → 재정렬. 원격이 끼면 순서가 업스트림 소유라
///    재정렬 대신 전환으로 접는다(파이썬의 else 갈래와 같다).
/// 4. 그 외 → 전환(클릭과 같은 뜻 — 누른 자리에서 놓으면 그 탭으로 간다).
pub fn drag_drop(tabs: &[DragTab], src: usize, target: DragTarget) -> Option<DragDrop> {
    let s = tabs.get(src)?;
    Some(match target {
        DragTarget::Content { pane, horizontal } if !s.remote => {
            DragDrop::Join { pane, src, horizontal }
        }
        DragTarget::Tab(t) if t != src => match tabs.get(t) {
            Some(other) if !s.remote && !other.remote && other.pinned != s.pinned => {
                DragDrop::SetPin { index: src, on: !s.pinned }
            }
            Some(other) if !s.remote && !other.remote => DragDrop::Reorder { from: src, to: t },
            _ => DragDrop::Select(src),
        },
        _ => DragDrop::Select(src),
    })
}

#[cfg(test)]
#[path = "chrome_tests.rs"]
mod tests;
