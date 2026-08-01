//! 플러그인이 기여한 **데이터 표면**(설계 Tier A)의 화면쪽 그림자.
//!
//! 설계 = `docs/internal/PLUGIN_COMPAT_TEXTUAL_GUI_2026-08-01.md` §4.1 · §8.
//!
//! # 왜 `proto::PluginSurface` 와 같은 모양이 여기 한 벌 더 있나
//!
//! 계층 때문이다. `proto` 가 `base` 를 의존하므로(액션→명령 표) 반대 방향은 순환이다.
//! 그래서 **뷰가 옮겨 담는다** — `TabFacts`·`MenuToggles` 와 같은 갈래이고, 옮겨 담는
//! 코드는 `proto` 쪽 `From` 한 곳뿐이다. 화면 로직(무엇이 목록에 서고, 어느 탭에 걸리고,
//! 어느 줄이 이미 우리 것인가)은 전부 이 타입 위에 선다.
//!
//! # 이 모듈이 지키는 것 — "같은 목록을 보는가"
//!
//! 정본 클라는 플러그인 훅을 자기 프로세스에서 바로 불러 팔레트·메뉴·설정에 **끼운다**.
//! 우리는 그 자료를 소켓으로 받아 같은 자리에 끼워야 하는데, 두 경로가 생기는 순간
//! 갈라질 수 있다. 갈라지는 모양은 셋이고 전부 실제로 났다:
//!
//! 1. **두 번 선다** — `clock-mode`·`calendar-mode`·`auto-resume`·`prompt-clear` 는 우리가
//!    이미 네이티브로 든 이름인데(`PALETTE`) 서버 목록에도 있어 팔레트에 두 줄이 됐다.
//!    [`PluginSurface::palette_rows`] 가 그 넷을 걸러 낸다.
//! 2. **탭이 없어 안 보인다** — 플러그인이 낸 분류(`탐색`·`Perforce`)는 정적 탭 표에 없어
//!    그 명령들이 `전체` 탭에서만 보였다. [`PluginSurface::palette_cats`] 가 이어 붙인다.
//! 3. **지워도 남는다** — 메뉴의 플러그인 줄이 정적 표라, 서버가 그 플러그인을 안 실어도
//!    화면에 남았다(delete-to-disable 이 우리 쪽에서만 거짓). 이제 줄의 출처가 서버다.

use crate::config::{SETTINGS, SETTINGS_CATS, Setting};
use crate::keymap::{Action, PALETTE, PALETTE_CATS};

/// 플러그인이 기여한 팔레트 한 줄.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PluginCommand {
    pub name: String,
    pub desc: String,
    pub cat: String,
}

/// 플러그인이 기여한 메뉴 한 줄. `key` 는 **그 플러그인의 명령 이름**이다(정본 계약).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PluginMenuItem {
    pub key: String,
    pub label: String,
}

/// 플러그인이 기여한 설정 한 줄 — 코어 설정과 **같은 화면에 같은 모양**으로 선다.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PluginSetting {
    pub key: String,
    pub cat: String,
    /// `bool`·`enum`·`link` 등(정본 `clientutil.SETTINGS` 의 `type`).
    pub kind: String,
    /// `enum` 이면 고를 값들.
    pub values: Vec<String>,
}

/// 서버가 부는 플러그인 표면 한 벌.
///
/// 비어 있는 것과 **안 온 것**은 다르다 — 델타 프레임에는 이 자료가 없고, 그때 목록을
/// 지우면 플러그인 기여가 매 틱 깜빡인다. 그 판단은 `proto` 쪽(상태 병합)이 한다.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PluginSurface {
    pub commands: Vec<PluginCommand>,
    /// 인자를 안 받는 명령 이름들.
    pub noarg: Vec<String>,
    pub menu_items: Vec<PluginMenuItem>,
    pub settings: Vec<PluginSetting>,
    /// 코어에 없던 설정 분류(좌측 세로탭에 이어 붙일 것). 순서가 뜻이라 리스트다.
    pub setting_cats: Vec<String>,
}

/// 설정 화면의 한 줄이 어디서 왔나. 자리(index)는 **코어 뒤에 플러그인**이 이어지는
/// 한 줄기라, 화면·키·사이드바가 같은 번호를 쓴다(정본 `settings_order` 와 같은 차례).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SettingsRow {
    Core(usize),
    Plugin(usize),
}

/// 설정 한 줄의 실체(그리는 쪽이 값을 읽을 수 있게).
#[derive(Debug, Clone, Copy)]
pub enum SettingRef<'a> {
    Core(&'a Setting),
    Plugin(&'a PluginSetting),
}

impl PluginSetting {
    /// 화면에 적을 값칸(코어 [`Setting::display`] 와 **같은 어휘**를 쓴다).
    ///
    /// ⚠ 지금 값은 **모른다**: 표면은 줄의 모양만 나르고 값은 안 나른다(Tier A 는
    /// 목록이다). 모르는 것을 아는 척하지 않고 `?`(미상)로 둔다 — 그 편이 "서버가 준
    /// 값을 보고 있다"는 거짓보다 낫다. 값까지 나르는 것은 설계 P5(폼)의 몫이다.
    pub fn display(&self) -> crate::config::ValueDisplay {
        use crate::config::ValueDisplay;
        match self.kind.as_str() {
            "link" => ValueDisplay::Link(crate::i18n::tc("setting", "열기")),
            "bool" => ValueDisplay::Choices {
                labels: ["on", "off"]
                    .iter()
                    .map(|v| crate::config::setting_value_label(v))
                    .collect(),
                cur: None,
            },
            "enum" => ValueDisplay::Choices {
                labels: self
                    .values
                    .iter()
                    .map(|v| crate::config::setting_value_label(v))
                    .collect(),
                cur: None,
            },
            _ => ValueDisplay::Text {
                shown: format!("({})", crate::i18n::tc("setting", "미상(서버)")),
                unset: true,
            },
        }
    }
}

impl SettingRef<'_> {
    /// 설정 파일·서버가 쓰는 정규 키(화면 이름은 `config::setting_label`).
    pub fn key(&self) -> &str {
        match self {
            SettingRef::Core(s) => s.key,
            SettingRef::Plugin(s) => &s.key,
        }
    }

    pub fn cat(&self) -> &str {
        match self {
            SettingRef::Core(s) => s.cat,
            SettingRef::Plugin(s) => &s.cat,
        }
    }
}

/// 이름이 우리 **코어 표에 이미 있나**(= 네이티브로 든 것).
///
/// 팔레트 이름이 `split-window -h` 처럼 플래그를 품기도 해 기본형으로 견준다
/// (적합성 테스트가 설명을 찾는 방식과 같아야 둘이 갈라지지 않는다).
pub fn native_action(name: &str) -> Option<Action> {
    PALETTE
        .iter()
        .find(|e| e.name.split(' ').next().unwrap_or(e.name) == name)
        .map(|e| e.action)
}

impl PluginSurface {
    /// 팔레트에 **새로** 실을 줄들의 자리(걸러진 차례대로).
    ///
    /// 규칙 둘:
    /// - 코어 표에 이미 있는 이름은 뺀다 — 그 줄은 우리가 네이티브로 실행하고, 두 번
    ///   그리면 같은 이름 두 줄 중 하나만 동작하는 화면이 된다.
    /// - 거르는 규칙은 코어와 **같은 것**을 쓴다([`crate::keymap::palette_matches_plugin`]).
    ///   두 목록이 다른 규칙으로 걸리면 같은 글자에 한쪽만 걸린다.
    pub fn palette_rows(&self, cat: Option<&str>, filter: &str) -> Vec<usize> {
        let rows = self
            .commands
            .iter()
            .map(|c| (c.name.as_str(), c.desc.as_str(), c.cat.as_str()));
        crate::keymap::palette_matches_plugin(cat, filter, rows)
            .into_iter()
            .filter(|i| {
                self.commands.get(*i).is_some_and(|c| native_action(&c.name).is_none())
            })
            .collect()
    }

    /// 그 탭(분류)에 걸리는 줄 수 — 코어와 합쳐 탭줄에 적는 숫자다.
    pub fn palette_count(&self, cat: Option<&str>, filter: &str) -> usize {
        self.palette_rows(cat, filter).len()
    }

    /// 팔레트 **탭 차례** — 코어 탭 뒤에 플러그인이 낸 분류를 등장 순서로 잇는다.
    ///
    /// 정본의 탭 차례가 `COMMANDS + plugins.commands` 의 카테고리 등장 순서라 같은 결과가
    /// 된다. 이 자리가 없으면 `탐색`(mdir·ncd)·`Perforce` 의 명령이 `전체` 탭에만 보인다.
    pub fn palette_cats(&self) -> Vec<&str> {
        let mut out: Vec<&str> = PALETTE_CATS.to_vec();
        for c in &self.commands {
            // 네이티브로 이미 든 줄은 분류를 새로 만들지 않는다(그 줄은 코어 탭에 있다).
            if native_action(&c.name).is_some() {
                continue;
            }
            if !out.iter().any(|have| *have == c.cat) {
                out.push(c.cat.as_str());
            }
        }
        out
    }

    /// 탭줄에 적을 개수 — `전체` 를 맨 앞에 둔 [`Self::palette_cats`] 차례다.
    ///
    /// 코어 개수만 세면 탭줄이 거짓말을 한다: `탐색` 탭에 mdir·ncd 두 줄이 보이는데
    /// 숫자는 `(0)` 이 된다. 코어 설명(`desc`)은 뷰가 준다(그 자료의 주인이 proto 다).
    pub fn palette_tab_counts<'d>(
        &self,
        filter: &str,
        desc: impl Fn(&'d str) -> Option<&'d str> + Copy,
    ) -> Vec<usize> {
        let all = crate::keymap::palette_matches_with(None, filter, desc).len()
            + self.palette_count(None, filter);
        std::iter::once(all)
            .chain(self.palette_cats().into_iter().map(|c| {
                crate::keymap::palette_matches_with(Some(c), filter, desc).len()
                    + self.palette_count(Some(c), filter)
            }))
            .collect()
    }

    /// 지금 탭에 걸린 것이 없으면 **결과가 있는 첫 탭**(0=`전체`) — 정본 `_rebuild` 와 같다.
    pub fn palette_tab_with_results<'d>(
        &self,
        now: usize,
        filter: &str,
        desc: impl Fn(&'d str) -> Option<&'d str> + Copy,
    ) -> usize {
        if filter.trim().is_empty() {
            return now;
        }
        let counts = self.palette_tab_counts(filter, desc);
        if counts.get(now).copied().unwrap_or(0) > 0 {
            return now;
        }
        counts.iter().position(|n| *n > 0).unwrap_or(now)
    }

    /// 설정 화면에 그릴 줄들 — **코어 뒤에 플러그인**(정본 `settings_order` 와 같은 차례).
    pub fn settings_rows(&self) -> Vec<SettingsRow> {
        (0..SETTINGS.len())
            .map(SettingsRow::Core)
            .chain((0..self.settings.len()).map(SettingsRow::Plugin))
            .collect()
    }

    /// 설정 줄 수(코어 + 플러그인).
    pub fn settings_len(&self) -> usize {
        SETTINGS.len() + self.settings.len()
    }

    /// `row` 번째 설정 줄.
    pub fn setting_at(&self, row: usize) -> Option<SettingRef<'_>> {
        match SETTINGS.get(row) {
            Some(s) => Some(SettingRef::Core(s)),
            None => self.settings.get(row - SETTINGS.len()).map(SettingRef::Plugin),
        }
    }

    /// 설정 **사이드바 차례** — 코어 분류 뒤에 플러그인이 낸 분류(정본 `Claude`).
    pub fn setting_cats(&self) -> Vec<&str> {
        let mut out: Vec<&str> = SETTINGS_CATS.to_vec();
        for cat in &self.setting_cats {
            if !out.iter().any(|have| *have == cat) {
                out.push(cat.as_str());
            }
        }
        // 표면이 분류를 안 알려 줬는데 줄의 분류가 새 것일 수도 있다 — 줄이 어느 탭에도
        // 안 잡히면 화면에서 영영 안 보이므로 여기서 메운다.
        for s in &self.settings {
            if !out.iter().any(|have| *have == s.cat) {
                out.push(s.cat.as_str());
            }
        }
        out
    }

    /// `row` 번째 설정이 속한 분류의 사이드바 번호.
    pub fn setting_cat_of(&self, row: usize) -> Option<usize> {
        let cat = self.setting_at(row)?.cat().to_owned();
        self.setting_cats().iter().position(|c| *c == cat)
    }

    /// 그 분류의 **첫 줄** 자리(사이드바를 클릭했을 때 뛸 곳 — 정본 `_cat_first`).
    pub fn setting_cat_first(&self, cat: &str) -> Option<usize> {
        (0..self.settings_len()).find(|row| {
            self.setting_at(*row).is_some_and(|s| s.cat() == cat)
        })
    }

    /// `Tab`/`Shift+Tab` 으로 분류를 넘길 때 갈 줄. 끝에서 **돈다**(막다른 끝은
    /// "Tab 이 안 먹는다"로 읽힌다).
    pub fn setting_cat_step(&self, row: usize, forward: bool) -> usize {
        let cats = self.setting_cats();
        if cats.is_empty() {
            return row;
        }
        let now = self.setting_cat_of(row).unwrap_or(0);
        let len = cats.len();
        let next = if forward { (now + 1) % len } else { (now + len - 1) % len };
        self.setting_cat_first(cats[next]).unwrap_or(row)
    }
}
