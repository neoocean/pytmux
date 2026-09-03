//! 탭 — `status` 메시지에서 읽어 낸다.
//!
//! # 서버 모델이 이미 맞다
//!
//! pytmux 는 **단일 세션 모델**이라(설계문서 §4.1) 탭 목록이 곧 최상위 전환 단위다.
//! 게다가 **원격 탭이 이미 같은 목록에 섞여** 온다 — 서버가 페더레이션 링크의 탭을
//! 전역 index 로 이어 붙여 준다. 그래서 클라가 로컬/원격을 합치는 일을 따로 하지 않는다.
//!
//! # 원격 판정은 플래그로 한다
//!
//! 원격 탭의 이름은 `⇄호스트:이름` 꼴이지만 **이름을 파싱하면 안 된다**. 서버가
//! `remote: true` 를 명시적으로 보내며, 서버 주석이 그렇게 쓰라고 못박고 있다
//! (`serverremote.py::_remote_tabs` — "이름 ⇄ 접두사 파싱 대신 명시 플래그 — 이름은
//! 표시 전용으로 남긴다"). 이름은 사용자에게 보여 주는 용도로만 쓴다.

use serde::Deserialize;

/// 접지 않는 표시 형식 — **고르는 화면**의 기본이다(§10-21ⓓ2).
///
/// 좁은 자리(탭바)를 위해 접는 설정이므로, 넓은 목록에서까지 접으면 다른 서버의 같은
/// 이름 탭이 구분되지 않는다. 낱말을 박아 두지 않고 상수로 두는 이유는 그 뜻("접지
/// 않는다")을 부르는 자리에서 읽히게 하려는 것이다.
pub const FULL_TITLE: &str = "full";

/// 탭 하나.
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
pub struct Tab {
    /// 전역 index. 로컬 탭이 먼저, 원격 탭이 뒤에 이어진다.
    /// `select_window` 에 그대로 넘기는 값이다.
    pub index: usize,
    /// 표시용 이름. 원격이면 `⇄호스트:이름` 꼴이지만 **파싱하지 말 것**.
    #[serde(default)]
    pub name: String,
    /// 안정 window id. 서버 재시작 때 재발급되므로 `boot_id` 와 함께 써야 한다.
    /// 구버전 상류는 안 보낸다 — 그때는 `index` 로 폴백한다.
    #[serde(default)]
    pub wid: Option<i64>,
    #[serde(default)]
    pub active: bool,
    /// 페더레이션된 원격 서버의 탭인가.
    #[serde(default)]
    pub remote: bool,
    #[serde(default)]
    pub bell: bool,
    #[serde(default)]
    pub activity: bool,
    /// Claude 작업이 끝났다는 표식.
    #[serde(default)]
    pub claude_done: bool,
    /// 이 탭의 Claude **상태 집계** — `idle` · `busy` · `limit`(없으면 Claude 가 없다).
    ///
    /// 서버가 이미 보내던 칸이다(`plugins/claude-code` 의 `wd["claude"] = _tab_claude(t)`)
    /// — 정본은 그것을 글리프 `○`·`◐`·`⊘` 로 탭 앞에 찍는다(`client_tab_glyph`).
    /// 우리는 그 **뜻**만 받아 아이콘으로 그린다(pytmux-461).
    ///
    /// ⛔ 이름에서 글리프를 파싱하지 않는다 — [`Tab::display`] 머리말이 `⇄` 에 대해
    /// 적어 둔 것과 같은 규율이다. 뜻은 칸으로 오고, 그림은 그 뜻에서 나온다.
    #[serde(default)]
    pub claude: Option<String>,
    #[serde(default)]
    pub pinned: bool,
}

impl Tab {
    /// 이 탭을 가리키는 안정 키. `wid` 가 있으면 그것을, 없으면 위치를 쓴다.
    ///
    /// 위치는 탭이 닫히거나 순서가 바뀌면 어긋나므로 **`wid` 가 있으면 반드시 그것**을
    /// 쓴다. 서버가 `wid` 를 도입한 이유가 그것이다.
    pub fn key(&self, boot_id: Option<&str>) -> String {
        match self.wid {
            Some(wid) => format!("{}:{wid}", boot_id.unwrap_or("-")),
            None => format!("{}:#{}", boot_id.unwrap_or("-"), self.index),
        }
    }

    /// 사용자에게 보여 줄 이름. 원격 탭은 접두사를 떼고 호스트를 따로 돌려준다.
    ///
    /// **판정은 `remote` 플래그로 하고**, 이름은 그 뒤에 꾸미기 위해서만 쪼갠다.
    /// 모양이 예상과 다르면 통째로 이름으로 쓴다 — 파싱 실패가 탭을 사라지게 하면 안 된다.
    pub fn display(&self) -> TabLabel<'_> {
        if !self.remote {
            return TabLabel {
                host: None,
                name: &self.name,
            };
        }
        let rest = self.name.strip_prefix('⇄').unwrap_or(&self.name);
        match rest.split_once(':') {
            Some((host, name)) => TabLabel {
                host: Some(host),
                name,
            },
            None => TabLabel {
                host: None,
                name: rest,
            },
        }
    }

    /// 탭바에 찍을 한 조각 — 이름 + 서버가 알려 준 주목 표식들.
    ///
    /// # 왜 뷰가 아니라 여기인가
    ///
    /// 두 뷰가 각자 조립하면 **같은 탭이 화면마다 달라 보인다** — 한쪽에만 종 표시가
    /// 빠지는 식이다. 그 어긋남은 조용하고(둘을 나란히 놓고 봐야 안다) 고칠 때도 두 곳을
    /// 고쳐야 한다. 색·강조는 뷰가 정하지만 **무엇을 적을지는 한 곳**이다.
    ///
    /// 표식 셋은 동시에 설 수 있다(벨이 울린 채로 활동이 있고 Claude 가 끝날 수 있다).
    /// `number` 는 **표시 번호**(1-based, 시각 순서 — [`Tabs::visual_numbers`]).
    ///
    /// 형식은 파이썬 정본 `TabBar._labels` 그대로다:
    /// `{핀}{상태 글리프}{번호}:{이름}{플래그}`.
    ///
    /// # 왜 이모지가 아니라 ASCII 인가
    ///
    /// 종전에는 핀 `📌`·벨 `🔔`·활동 `•` 이었다. 정본이 ASCII(`*`·`!`·`#`)를 쓰는 이유는
    /// 취향이 아니라 **이모지 폭이 터미널마다 다르기 때문**이다(정본 `PIN_GLYPH` 주석).
    /// 폭이 어긋나면 탭바 클릭존이 밀리고 넘침 계산([`window`])이 틀린다 — 우리 TUI 도
    /// 같은 격자 위에 그리므로 같은 이유가 그대로 적용된다.
    ///
    /// 플래그는 **하나만** 선다(벨이 활동을 이긴다) — 정본과 같다. 둘을 겹쳐 찍던
    /// 종전 판은 탭 이름이 그만큼 길어져 넘침이 빨라졌다.
    /// # 원격 제목은 **그릴 때만** 접는다 (§10-21ⓓ2)
    ///
    /// `mode` 는 설정 `remote-title`(`full`·`host`·`name`)이다. 이름 자체는 서버가 짓고
    /// `remote-detach` 의 인자이기도 하므로 값으로 쓰는 자리는 원래 이름 그대로다 —
    /// 여기서 접는 것은 탭바에 찍는 글자뿐이다. 로컬 탭은 무엇을 골랐든 그대로다
    /// (판정은 `remote` 플래그로 한다 — 이름을 파싱하지 않는다).
    pub fn label(&self, number: usize, mode: &str) -> String {
        let label = self.display();
        let mut out = String::new();
        if self.pinned {
            out.push_str("* ");
        }
        if self.claude_done {
            out.push_str("✓ ");
        }
        out.push_str(&number.to_string());
        out.push(':');
        match label.host {
            // 원격은 이미 **색으로** 구분된다(§1.7-a 분홍) — 그래서 아이콘·호스트를
            // 접어도 무엇인지 안 잃는다.
            Some(host) => match mode {
                "name" => out.push_str(label.name),
                "host" => out.push_str(&format!("{host}:{}", label.name)),
                _ => out.push_str(&format!("⇄{host}:{}", label.name)),
            },
            None => out.push_str(label.name),
        }
        if self.bell {
            out.push('!');
        } else if self.activity {
            out.push('#');
        }
        out
    }
}

/// 화면에 붙일 이름 조각.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TabLabel<'a> {
    /// 원격 탭이면 호스트 이름.
    pub host: Option<&'a str>,
    pub name: &'a str,
}

/// `status` 에서 읽어 낸 탭바 상태.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct TabBar {
    pub tabs: Vec<Tab>,
    /// 서버 인스턴스 부팅 식별자. `wid` 를 네임스페이싱하는 데 쓴다.
    pub boot_id: Option<String>,
    pub session: String,
    /// 활성 패널 id.
    pub active_pane: Option<i64>,
    pub zoomed: bool,
    pub sync: bool,
    pub pane_title: String,
}

impl TabBar {
    /// `status` 메시지에서 뽑는다. 모르는 필드는 전부 무시한다.
    pub fn from_status(status: &crate::message::Status) -> Self {
        let get = |k: &str| status.fields.get(k);
        Self {
            tabs: get("windows")
                .and_then(|v| serde_json::from_value(v.clone()).ok())
                .unwrap_or_default(),
            boot_id: get("boot_id")
                .and_then(|v| v.as_str())
                .map(str::to_owned),
            session: get("session")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_owned(),
            active_pane: get("active_pane").and_then(serde_json::Value::as_i64),
            zoomed: get("zoomed").and_then(serde_json::Value::as_bool).unwrap_or(false),
            sync: get("sync").and_then(serde_json::Value::as_bool).unwrap_or(false),
            pane_title: get("pane_title")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_owned(),
        }
    }

    pub fn active(&self) -> Option<&Tab> {
        self.tabs.iter().find(|t| t.active)
    }

    /// 로컬 탭 수. 원격 탭은 항상 뒤에 붙으므로 이 값이 곧 경계다.
    pub fn local_count(&self) -> usize {
        self.tabs.iter().filter(|t| !t.remote).count()
    }

    /// 탭마다의 **표시 번호**(1-based) — 리스트와 같은 순서로 돌려준다.
    ///
    /// 번호는 `index+1` 이 아니라 **시각 순서**로 매긴다: 비고정 탭 먼저, 고정 탭 나중
    /// (정본 `_visual_tab_numbers`). 고정 탭은 오른쪽 구역으로 밀려 그려지므로, index 로
    /// 매기면 **보이는 순서와 번호가 어긋난다** — 그러면 사용자가 화면에서 읽은 번호로
    /// `prefix 숫자` 를 눌렀을 때 다른 탭으로 간다.
    ///
    /// 로컬 정규화 상태(서버가 [비고정][고정]으로 정렬해 보낸 경우)에서는 `index+1` 과
    /// 같은 값이 나온다 — 어긋나는 것은 원격 탭이 섞이거나 정규화 전일 때다.
    pub fn visual_numbers(&self) -> Vec<usize> {
        let mut order: Vec<usize> = (0..self.tabs.len()).filter(|&i| !self.tabs[i].pinned).collect();
        order.extend((0..self.tabs.len()).filter(|&i| self.tabs[i].pinned));
        let mut out = vec![0usize; self.tabs.len()];
        for (n, i) in order.into_iter().enumerate() {
            out[i] = n + 1;
        }
        out
    }

    /// **표시 번호**(1-based, 시각 순서) → 그 탭. 없으면 `None`.
    ///
    /// 정본 `TabBar.tab_for_number` 와 같은 자리다. `prefix 숫자`·`esc 숫자` 는 사용자가
    /// **화면에서 읽은 번호**를 누르는 것이므로 [`visual_numbers`](TabBar::visual_numbers) 와
    /// 같은 순서를 따라야 한다 — 번호를 index 로 바로 쓰면 고정 탭이 섞이거나 정규화
    /// 전일 때 다른 탭으로 간다.
    ///
    /// 돌려주는 것이 index 가 아니라 **탭 전체**인 이유: 호출부가 `wid` 도 함께 실어야
    /// 하기 때문이다([`Command::SelectWindow`](crate::command::Command::SelectWindow) —
    /// index 만 보내면 레이스에서 옆 탭이 열린다).
    /// 전역 index → 그 탭. 목록·트리처럼 **이미 index 를 들고 있는** 자리가 `wid` 를
    /// 얹으려고 부른다([`Command::SelectWindow`](crate::command::Command::SelectWindow)).
    ///
    /// 못 찾으면 `None` 이고, 그때 호출부는 index 만 보낸다 — 종전과 같은 동작이다.
    pub fn by_index(&self, index: usize) -> Option<&Tab> {
        self.tabs.iter().find(|t| t.index == index)
    }

    /// 그 index 탭의 `wid`(있으면). `SelectWindow` 를 만드는 자리의 한 줄용이다.
    pub fn wid_of(&self, index: usize) -> Option<i64> {
        self.by_index(index).and_then(|t| t.wid)
    }

    pub fn by_number(&self, number: usize) -> Option<&Tab> {
        let nums = self.visual_numbers();
        self.tabs.iter().zip(nums).find(|(_, n)| *n == number).map(|(t, _)| t)
    }

    /// 탭바에 찍을 라벨들(리스트 순서). 번호까지 붙은 최종 문자열이다.
    ///
    /// `mode` = 설정 `remote-title`(§10-21ⓓ2). 접는 것은 **좁은 자리**를 위한 것이므로
    /// 넓은 목록(탭 스위처)은 [`FULL_TITLE`] 로 부른다 — 거기서 호스트를 접으면 다른
    /// 서버의 같은 이름 탭이 구분되지 않아, 고르는 화면이 제 일을 못 한다.
    pub fn labels(&self, mode: &str) -> Vec<String> {
        let nums = self.visual_numbers();
        self.tabs.iter().zip(nums).map(|(t, n)| t.label(n, mode)).collect()
    }
}

// ── 탭바 넘침 창(G9x — 파이썬 `TabBar._entries` 의 스크롤 구역 동형) ───────────
//
// 탭이 폭을 넘치면 파이썬은 **보이는 창**을 유지한다: 선택 탭이 늘 보이게 스크롤을
// 보정하고, 잘린 쪽에 `◀`/`▶` 화살표를 세운다(클릭 = 한 탭씩 스크롤). 핀 우측
// 구역은 우리 탭바가 인라인 📌 글리프로 접었으므로 여기 없다 — 기하는 스크롤
// 구역만 옮긴다(정본: `clientwidgets.py` `_entries` 의 스크롤 보정·예약 규칙.
// `_entries` 는 Textual 위젯·플러그인 훅에 붙어 있어 픽스처 생성기로 못 뽑는다 —
// 규칙을 옮기고 아래 오라클이 그 규칙을 지킨다).

/// 넘침 계산 결과 — 그릴 범위와 화살표 유무, 보정된 스크롤.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TabWindow {
    /// 그릴 탭 범위(리스트 위치) `start..end`.
    pub start: usize,
    pub end: usize,
    /// 왼쪽에 잘린 탭이 있다(`◀` 를 그린다).
    pub left: bool,
    /// 오른쪽에 잘린 탭이 있다(`▶` 를 그린다).
    pub right: bool,
    /// 보정된 스크롤(다음 렌더의 시작값 — 뷰가 저장한다).
    pub scroll: usize,
}

/// 폭 예산 안에서 보일 탭 창을 정한다.
///
/// `widths` 는 탭 라벨의 표시 폭(칸), `scroll` 은 직전 값, `budget` 은 탭들에 쓸 수
/// 있는 칸 수다. 왼쪽 화살표가 한 칸을 먹고, 마지막 탭이 아니면 오른쪽 화살표 한 칸을
/// 예약한다(파이썬 규칙 그대로).
///
/// `sel` 은 **이번 프레임에 움직인** 선택(활성 탭 변경·크롬 포커스 이동)일 때만
/// `Some` 이다 — 그때만 보이게 보정한다. ★ 파이썬은 이 보정을 **매 렌더** 돌려서,
/// 활성 탭이 맨 왼쪽이면 `▶` 클릭이 다음 렌더에 즉시 되돌아가는 습성이 있다(화살표가
/// 죽는다). 버그까지 동형은 비목표(§1)라 여기서 갈라선다 — 손으로 민 스크롤은
/// 선택이 움직이기 전까지 유지된다.
pub fn tab_window(widths: &[usize], sel: Option<usize>, scroll: usize, budget: usize) -> TabWindow {
    let n = widths.len();
    if n == 0 {
        return TabWindow { start: 0, end: 0, left: false, right: false, scroll: 0 };
    }
    // 스크롤 클램프 + (선택이 움직였으면) 선택 탭이 보이게 보정(파이썬의 두 while).
    let mut scroll = scroll.min(n - 1);
    if let Some(sel) = sel.map(|s| s.min(n - 1)) {
        if sel < scroll {
            scroll = sel;
        }
        while scroll < sel
            && widths[scroll..=sel].iter().sum::<usize>() > budget.saturating_sub(2)
        {
            scroll += 1;
        }
    }
    let left = scroll > 0;
    let mut used = usize::from(left); // `◀` 한 칸
    let mut i = scroll;
    while i < n {
        let reserve = usize::from(i < n - 1); // `▶` 자리 예약
        if used + widths[i] > budget.saturating_sub(reserve) && i > scroll {
            break;
        }
        used += widths[i];
        i += 1;
    }
    TabWindow { start: scroll, end: i, left, right: i < n, scroll }
}

#[cfg(test)]
#[path = "tabs_tests.rs"]
mod tests;
