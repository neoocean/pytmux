//! 서버가 말해 준 것의 현재 상태.
//!
//! 서버는 메시지를 **점진적으로** 보낸다 — `layout` 이 배치를 바꾸고, 패널마다 `screen`
//! 이 오고, `status` 가 탭바를 갱신한다. 그래서 클라는 "지금 화면이 어떻게 생겼나"를
//! 스스로 누적해 들고 있어야 한다.
//!
//! # 서버가 권위다
//!
//! 명령을 보낸 뒤 로컬 상태를 낙관적으로 고치지 않는다. 서버 명령 테이블 대부분이
//! `FULL` 이라 **전체 재동기**가 뒤따라오므로, 기다리면 정확한 상태가 온다. 낙관적
//! 갱신은 두 상태가 어긋날 여지만 만든다.
//!
//! # UI 를 모른다
//!
//! 이 타입은 무엇을 그릴지 알지만 **어떻게 그릴지는 모른다**. GUI·TUI 가 같은 상태를
//! 각자의 엘리먼트로 그린다.

use std::collections::HashMap;

use base::i18n::{t, tf};

use crate::blocks::Block;
use crate::canvas::Canvas;
use crate::compose::compose_rows;
use crate::message::{BufferItem, Layout, PaneLayout, Screen, ServerMessage, TitleBar, Tree};
use crate::style::{CellStyle, Color, NamedColor};
use crate::tabs::TabBar;

/// 이력에 남기는 알림 개수. 넘으면 오래된 것부터 버린다.
pub const NOTICE_LIMIT: usize = 200;

/// 알림 등급 — 색과 기호를 정한다(파이썬 §10-8 과 같은 네 단계).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    Ok,
    Info,
    Warn,
    Error,
}

impl Severity {
    /// 와이어의 `sev`. **모르는 값은 `Info`** 다 — 서버가 등급을 늘려도 클라가 안 깨진다.
    pub fn parse(value: Option<&str>) -> Self {
        match value {
            Some("ok") => Severity::Ok,
            Some("warn") => Severity::Warn,
            Some("error") => Severity::Error,
            _ => Severity::Info,
        }
    }

    /// 목록에 붙는 기호. 색은 뷰가 정하지만 기호는 여기서 — 두 뷰가 각자 고르면 같은
    /// 알림이 화면마다 달라 보인다.
    pub fn mark(self) -> &'static str {
        match self {
            Severity::Ok => "✓",
            Severity::Info => "·",
            Severity::Warn => "!",
            Severity::Error => "✕",
        }
    }
}

/// 알림 한 줄.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Notice {
    pub severity: Severity,
    pub text: String,
    /// 이 알림이 생긴 시각(`HH:MM:SS`). 이력의 값어치는 **언제**에 있다.
    pub at: String,
    /// 누가 낸 알림인가.
    pub source: Source,
}

/// 알림을 낸 쪽 — 정본 알림 이력의 출처 열과 같은 갈래다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    /// 이 클라가 낸 것(설정 다시 읽음·복사됨·끊김 …).
    Local,
    /// 서버가 보낸 것(`notice`·`error` 메시지).
    Server,
}

impl Source {
    /// 목록에 적을 낱말. 정본과 같은 소문자 영문이다 — 이 열은 **눈으로 훑는 표식**이라
    /// 번역하면 폭이 흔들리고 정렬이 깨진다.
    pub fn label(self) -> &'static str {
        match self {
            Source::Local => "local",
            Source::Server => "server",
        }
    }
}

impl Notice {
    /// 지금 시각을 찍어 만든다(`Local` 출처).
    pub fn new(severity: Severity, text: String) -> Self {
        Self::from(severity, text, Source::Local)
    }

    pub fn from(severity: Severity, text: String, source: Source) -> Self {
        Self { severity, text, at: crate::clock::now_text(), source }
    }

    /// 목록에 적을 한 줄 — `{기호} {시각} {출처} {글}`.
    ///
    /// 정본 알림 이력과 같은 열 구성이다(대조 문서 §8). 기호만 있고 시각·출처가 없으면
    /// "언제 무엇이 있었나"를 이력에서 못 읽는다 — 이력의 존재 이유가 그것이다.
    /// 출처는 `server` 가 가장 길어 6칸으로 맞춘다(열이 흔들리면 훑기가 안 된다).
    pub fn line(&self) -> String {
        format!("{} {} {:<6} {}", self.severity.mark(), self.at, self.source.label(), self.text)
    }
}

/// 터치 스크롤바가 차지한 자리 — 그리는 쪽과 누르는 쪽이 **같은 값**을 본다.
///
/// 활성 패널 오른쪽 끝 **한 열**이다(`x` 는 그 열, `y..y+h` 가 그 세로 범위).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TouchScrollZone {
    /// 이 바가 조종하는 패널.
    pub pane: i64,
    /// 바가 그려지는 열.
    pub x: usize,
    /// 바의 첫 행.
    pub y: usize,
    /// 바의 높이(= 패널 높이).
    pub h: usize,
}

/// 서버가 광고하는 플러그인 하나(패리티 G7).
///
/// # 왜 서버가 알려 줘야 하나
///
/// 파이썬 클라는 **자기 프로세스 안에서** 플러그인 패키지를 읽어 이 목록을 만든다
/// (`plugins.plugin_overview()`). 우리는 파이썬 모듈을 못 읽고, pytmux 트리가 어디 있는지도
/// 모른다 — 아는 것은 소켓뿐이다. `status` 의 `disabled_plugins` 는 **꺼진 것의 이름**뿐이라
/// "설치된 것 전부"를 복원할 수 없었다. 그래서 서버가 full status 에 개요를 싣는다
/// (pytmux CL 68070 — 이 저장소가 서버를 건드린 첫 자리다).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PluginInfo {
    pub name: String,
    pub description: String,
    pub category: String,
    pub enabled: bool,
}

/// 플러그인이 기여한 **데이터 표면** — 서버가 실어 주는 명령·메뉴·설정
/// (설계 `docs/internal/PLUGIN_COMPAT_TEXTUAL_GUI_2026-08-01.md` Tier A).
///
/// # 왜 서버가 주나
///
/// 이 자료는 플러그인의 파이썬 훅이 돌려주는 것이고, 정본 클라는 자기 프로세스에서 바로
/// 부른다. 우리는 파이썬을 못 읽는다 — 그래서 **플러그인이 기여한 명령·메뉴 줄·설정이
/// 통째로 안 보였다**(mdir·ncd 같은 플러그인은 입구조차 없었다). 서버는 어차피 플러그인을
/// 로드하고 있으니 그 자료를 그대로 준다.
///
/// 비어 있는 것과 **안 온 것**은 다르다: 델타 프레임에는 이 키가 없고, 그때 목록을 지우면
/// 플러그인 기여가 매 틱 깜빡인다(`plugins` 개요와 같은 규칙).
///
/// # 왜 타입이 `base` 것인가 (2026-08-01, P2)
///
/// 화면 로직(무엇이 목록에 서고, 어느 탭에 걸리고, 어느 줄이 이미 우리 네이티브인가)이
/// `base::plugins` 에 있다. 여기 같은 모양을 한 벌 더 두면 **옮겨 담는 코드**가 생기고,
/// 옮겨 담는 코드는 필드가 늘 때 한쪽만 늘어난다. 파싱은 여기, 뜻은 저기.
pub use base::plugins::{PluginCommand, PluginMenuItem, PluginSetting, PluginSurface};

/// 플러그인이 준 **화면 한 판**(설계 Tier C · P4).
///
/// # 왜 스펙인가
///
/// `mdir`·`ncd`·`p4changes` 의 화면은 정본에서 Textual 위젯이다 — 우리는 그것을 띄울 수
/// 없다. 대신 플러그인이 **무엇을 그릴지**를 자료로 주고 우리는 두 모양(목록·글)만 그린다.
/// 그래서 플러그인은 파이썬 한 벌로 남고, 화면 흐름이 바뀌어도 우리를 안 고친다.
///
/// 모르는 `kind` 는 **조용히 버리지 않는다** — 그리는 쪽이 "이 클라는 이 화면을 아직 못
/// 그린다"를 사람에게 보인다(설계 §8-5).
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Deserialize)]
pub struct PluginScreen {
    #[serde(default)]
    pub id: String,
    /// `list` · `text`(P4). 나머지 넷은 P5.
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub hint: String,
    #[serde(default)]
    pub rows: Vec<PluginRow>,
    #[serde(default)]
    pub text: String,
    /// 실패했거나 비었을 때 화면에 적을 한 줄. **빈 목록과 실패는 다르다.**
    #[serde(default)]
    pub note: String,
    #[serde(default)]
    pub selected: usize,
    /// 키 → 플러그인 액션 이름. 이 표에 있는 키만 되돌려준다.
    #[serde(default)]
    pub keys: std::collections::BTreeMap<String, String>,
    /// `title`·`hint`·`note` 를 우리 로케일로 다시 지을 재료(있는 것만 온다).
    #[serde(default)]
    pub i18n: I18nMap,
}

impl PluginScreen {
    /// 제목 — **이 클라의 로케일로**.
    pub fn say_title(&self) -> String {
        i18n_say(&self.i18n, "title", &self.title)
    }

    /// 안내줄 — 이 클라의 로케일로.
    pub fn say_hint(&self) -> String {
        i18n_say(&self.i18n, "hint", &self.hint)
    }

    /// 빈/실패 한 줄 — 이 클라의 로케일로.
    pub fn say_note(&self) -> String {
        i18n_say(&self.i18n, "note", &self.note)
    }

    /// **글 판(`text`)의 본문** — 이 클라의 로케일로.
    ///
    /// # 왜 `text` 만 오래 번역이 안 됐나
    ///
    /// 제목·안내·한 줄은 처음부터 `say_*` 를 탔는데 본문은 아니었다. 그때 이 칸에
    /// 오던 것이 한도 막대(`usage-panel`)처럼 **자료**뿐이라 번역할 것이 없었기
    /// 때문이다. 산문이 오는 첫 판(원격 제어 · pytmux-2 잔여)에서 그 전제가 깨졌다.
    ///
    /// ⚠ **`prompt` 판의 `text` 는 여기로 오면 안 된다.** 그 칸은 입력의 **초기값**
    /// (지금 규칙·지금 경로)이라 사람이 친 자료다 — 번역하면 사람이 저장한 글이
    /// 저장할 때마다 달라진다. 그래서 이 함수는 글 판 렌더 한 자리에서만 부른다.
    ///
    /// 자료가 와도 안전하다: 카탈로그에 없는 글은 `t()` 가 그대로 돌려준다.
    pub fn say_text(&self) -> String {
        i18n_say(&self.i18n, "text", &self.text)
    }

    /// `Enter` 에 걸린 플러그인 액션 이름(없으면 이 화면의 Enter 는 뜻이 없다).
    ///
    /// # 왜 뷰가 `keys["enter"]` 를 직접 안 읽나
    ///
    /// 계층 게이트(`scripts/check_layering.sh`)가 뷰 코드의 **키 이름 문자열**을 막는다.
    /// 그 규칙의 뜻은 "키 목록을 뷰가 따로 적기 시작하면 두 클라가 갈린다"이고, 여기
    /// `"enter"` 는 서버 스펙의 어휘이지 우리 키맵이 아니지만 — 그 구분을 사람 눈에
    /// 맡기면 규칙이 흐려진다. **어휘를 아는 곳을 하나로** 둔다.
    pub fn enter_action(&self) -> Option<&str> {
        self.keys.get("enter").map(String::as_str)
    }

    /// 그 **글자 키**에 걸린 액션(설계 P5). 스펙이 자기 키를 정하므로 플러그인마다
    /// 다른 손이 생긴다 — `ncd` 의 `c`(여기로 cd)가 그 첫 자리다.
    ///
    /// 화면 밖 키(닫기 등)와 겹칠 걱정은 없다: 이 표에 있는 글자만 우리가 먹는다.
    pub fn char_action(&self, c: char) -> Option<&str> {
        self.keys.get(&c.to_string()).map(String::as_str)
    }

    /// 그 **키**에 걸린 액션 — 글자와 **이름 있는 키**를 한 자리에서 본다(pytmux-11 B).
    ///
    /// # 왜 글자만으로는 부족했나
    ///
    /// 스펙의 키 표는 오래 글자뿐이었다(`ncd` 의 `c`). 그런데 트리는 `←→` 로 접고 펴는
    /// 것이 손버릇이고, 그 둘은 글자가 아니다 — 이름을 못 실으면 화면이 트리가 될 수
    /// 없다. 그래서 어휘를 **몇 개의 이름**까지 넓혔다.
    ///
    /// ⚠ 넓힌 것은 **여기까지**다. `↑↓`·`Enter`·`Esc` 는 목록 화면의 뜻이 이미 정해져
    /// 있어(고르기·확정·닫기) 스펙이 뺏으면 판마다 손이 달라진다.
    pub fn key_action(&self, key: base::Key, mods: base::Mods) -> Option<&str> {
        // `Alt+글자` — 정본 mdir 의 정렬(`Alt+N/E/S/T/O`)·마스크(`Alt+F`)가 그 손이다.
        // 글자 키로 옮기면 손버릇이 갈리고, 이미 `t`(태그)가 정렬의 `t`(시각)와 부딪힌다.
        if mods.alt {
            let base::Key::Char(c) = key else { return None };
            return self.keys.get(&format!("alt-{c}")).map(String::as_str);
        }
        if mods != base::Mods::NONE {
            return None;
        }
        let name = match key {
            base::Key::Char(c) => return self.char_action(c),
            base::Key::Right => "right",
            base::Key::Left => "left",
            _ => return None,
        };
        self.keys.get(name).map(String::as_str)
    }

    /// 물음·확인 화면에 적을 글 — **첫 줄이 물음이고 나머지가 상세**다.
    ///
    /// # 왜 여기 있나
    ///
    /// 종전에는 `confirm`·`prompt` 를 열 때 스펙의 `title`·`note` 를 **아무도 안 읽어서**,
    /// 사람에게는 "플러그인이 물었다:" 한 줄과 예/아니오 버튼만 보였다. 무엇을 지우는지
    /// 모른 채 누르는 화면이 되돌릴 수 없는 것 앞에 서 있던 셈이다(`Prompt::question` 의
    /// PluginAsk 주석은 이미 "문구의 주인은 플러그인"이라고 적고 있었는데 배선이 없었다).
    ///
    /// 어휘를 아는 곳을 하나로 둔다 — 뷰가 `title`/`note` 를 따로 이어 붙이기 시작하면
    /// 같은 물음이 화면마다 달라 보인다(`enter_action` 과 같은 규율).
    pub fn ask_text(&self) -> String {
        let mut out = self.title.clone();
        if !self.note.is_empty() {
            if !out.is_empty() {
                out.push('\n');
            }
            out.push_str(&self.note);
        }
        out
    }

    /// 이 화면이 **고르는 화면**인가(목록·표·폼). 아니면 읽거나 답하는 화면이다.
    ///
    /// core 는 스펙을 안 들으므로 뷰가 이 값을 넘겨 준다(`open_plugin_view`).
    pub fn is_selectable(&self) -> bool {
        matches!(self.kind.as_str(), "list" | "table" | "form")
    }
}

/// 플러그인이 화면에 얹는 글자들(설계 Tier B · P3).
///
/// # 왜 `dim` 이 따로 있나
///
/// 시계는 패널을 **덮되 뒤가 비쳐 보인다**. 그 흐리게 하기는 새 글자를 얹는 것이 아니라
/// **이미 있는 셀을 바꾸는** 일이라 런으로 못 나른다 — 화면을 들고 있는 쪽만 할 수 있다.
/// 서버는 "어느 패널이 덮였나"만 말하고 계산은 각 클라가 자기 방식으로 한다.
#[derive(Debug, Clone, Default, PartialEq, serde::Deserialize)]
pub struct PluginCells {
    /// `content`(내용 장식) | `overlay`(패널 덮기) — 정본의 그리기 순서 그대로다.
    #[serde(default)]
    pub layer: String,
    /// 뒤를 흐리게 할 패널 id 들.
    #[serde(default)]
    pub dim: Vec<u64>,
    #[serde(default)]
    pub runs: Vec<PluginRun>,
    /// 누를 수 있는 자리들(달력의 `‹`/`›`). **뜻은 안 온다** — 우리는 `do` 를 그대로
    /// 되돌려 보낼 뿐이고 그것이 무슨 일인지는 플러그인이 정한다(설계 §4.4).
    #[serde(default)]
    pub zones: Vec<PluginZone>,
    /// 그 오버레이가 가져가는 키들. 오버레이가 **활성 패널**에 떠 있을 때만 가로챈다 —
    /// 패널이 이미 덮여 있으니 셸 입력을 가리지 않는다(정본도 같은 규칙이다).
    #[serde(default)]
    pub keys: Vec<PluginKey>,
}

/// 누를 수 있는 자리 하나. 좌표는 런과 같은 **창 절대 좌표**다.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Deserialize)]
pub struct PluginZone {
    #[serde(default)]
    pub x: i64,
    #[serde(default)]
    pub y: i64,
    #[serde(default)]
    pub w: i64,
    #[serde(default)]
    pub h: i64,
    #[serde(default)]
    pub pane: i64,
    /// 어느 오버레이의 자리인가(서버가 찍어 준다). 되돌려 보낼 때 그대로 싣는다.
    #[serde(default)]
    pub name: String,
    /// 서버에 되돌려 줄 이름. 우리는 뜻을 모른다.
    #[serde(default, rename = "do")]
    pub act: String,
    /// 누르면 열 **플러그인 화면 이름**(비었으면 이 자리는 화면을 여는 자리가 아니다).
    ///
    /// 자리가 셋으로 갈리는 이유는 되돌려 보내는 길이 다르기 때문이다: 달력 화살표는
    /// 그 오버레이의 **상태**를 바꾸니 `plugin_overlay_action` 으로 가고, Claude 의
    /// 권한모드·토큰 자리는 **화면**을 여니 `plugin_open` 으로 간다(pytmux-2 · 23).
    /// 배지의 [`PluginBadge::open`] 과 같은 규약이고, 서버는 마찬가지로 **그 화면이
    /// 실제로 있을 때만** 싣는다.
    #[serde(default)]
    pub opens: String,
    /// 누르면 **그 패널에 칠 글자**(비었으면 이 자리는 치는 자리가 아니다).
    ///
    /// 세 번째 갈래다(pytmux-2 잔여). 어떤 자리는 화면도 오버레이 상태도 아니고
    /// *"그 패널에 이것을 친다"* 가 전부다 — Claude busy footer 의 `esc to interrupt`
    /// 가 그렇고, 정본도 그 자리를 `send_input_pane(pid, ESC)` 한 줄로 처리한다.
    ///
    /// **뜻은 여전히 우리 것이 아니다.** `\x1b` 가 무슨 뜻인지는 패널 안 프로그램이
    /// 정하고, 우리는 서버가 정한 바이트를 그 패널로 넘긴다 — 사람이 그 자리에서
    /// ESC 를 친 것과 **같은 경로**다(`Outgoing::InputToPane`). 그래서 이 갈래에는
    /// 되돌려 보낼 이름도 회신도 없다: 다음 프레임의 패널 화면이 답이다.
    #[serde(default)]
    pub send: String,
}

/// 오버레이가 가져가는 키 하나. 이름은 이 클라의 표기다([`base::keys`] 의 `binding_name`).
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Deserialize)]
pub struct PluginKey {
    #[serde(default)]
    pub key: String,
    #[serde(default)]
    pub pane: i64,
    #[serde(default)]
    pub name: String,
    #[serde(default, rename = "do")]
    pub act: String,
}

/// 서버가 지은 글 한 조각을 **이 클라의 로케일로 다시 지을 재료**(로케일 ⓑ).
///
/// # 왜 글이 아니라 재료인가
///
/// 서버가 이미 지은 글(`text`)은 **서버 프로세스의 로케일**이다. 서버가 ko 면 영어
/// 사용자도 한국어를 본다. 고정 리터럴은 우리가 한국어 원문을 키로 번역해 이미 풀렸지만
/// (로케일 ⓐ), `{pct}%/5h 사용` 처럼 **자리가 있는 글**은 값이 매번 달라 원문이 키가
/// 못 된다. 그래서 원문 포맷과 값을 따로 받아 [`crate::i18n_say`] 가 `tf` 로 짓는다.
///
/// 파이썬 쪽 짝은 `pytmuxlib.i18n.phrase()` 다.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Deserialize)]
pub struct Phrase {
    /// 한국어 **원문 포맷**(`"{pct}%/5h 사용"`). 우리 카탈로그의 키이기도 하다 —
    /// 못 찾으면 이 원문이 그대로 보인다(우아한 degrade).
    #[serde(default)]
    pub fmt: String,
    /// 자리 값. 서버가 이미 문자열로 만들어 보낸다(수·시각의 표기 규칙은 서버 몫이다).
    #[serde(default)]
    pub args: std::collections::BTreeMap<String, String>,
}

/// 한 메시지 안의 **필드 이름 → 재료**. 필드마다 칸을 늘리는 대신 하나로 묶는다
/// (화면 스펙은 `title`·`hint`·`note` 셋이 있어 칸을 늘리면 여섯이 된다).
pub type I18nMap = std::collections::BTreeMap<String, Phrase>;

/// 필드 하나를 이 클라의 로케일로 읽는다. 재료가 없으면 **서버가 지은 글 그대로**다
/// (구버전 서버·번역 대상이 아닌 글 — 그래도 고정 리터럴은 `t()` 가 잡는다).
pub fn i18n_say(map: &I18nMap, field: &str, fallback: &str) -> String {
    match map.get(field) {
        Some(p) if !p.fmt.is_empty() => {
            let args: Vec<(&str, &str)> =
                p.args.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
            base::i18n::tf(&p.fmt, &args)
        }
        _ => base::i18n::t(fallback).to_owned(),
    }
}

/// 얹을 글자 한 덩어리. 자리는 **창 절대 좌표**다(서버가 패널 내용 영역으로 이미 옮겼다).
#[derive(Debug, Clone, Default, PartialEq, serde::Deserialize)]
pub struct PluginRun {
    #[serde(default)]
    pub x: i64,
    #[serde(default)]
    pub y: i64,
    #[serde(default)]
    pub text: String,
    /// `{"text": {"fmt": …, "args": …}}` — 이 런의 글을 우리 로케일로 다시 지을 재료.
    #[serde(default)]
    pub i18n: I18nMap,
    /// 서버가 화면 런에 쓰는 것과 **같은 축약 스타일**(`crate::style`). 새 표기가 아니다.
    #[serde(default)]
    pub style: crate::message::Style,
    /// 의미 색 이름(`{"f": "success"}`). 있으면 **이 클라의 테마**에서 풀어 그 자리를
    /// 덮는다 — 서버가 hex 를 실으면 서버가 UI 를 알게 된다(설계 §10 위험표).
    #[serde(default)]
    pub theme: ThemeRef,
}

/// 오버레이를 하나 켜거나 껐다 — 서버에 올려 보낼 사실.
///
/// `closed` 는 **같은 패널에서 밀려난 오버레이**다(한 패널엔 하나). 이것까지 올려야
/// 서버가 두 그림을 겹쳐 보내지 않는다 — 안 올리면 시계 위에 달력이 얹힌다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayToggle {
    pub pane: i64,
    pub on: bool,
    pub closed: Option<String>,
}

/// 런이 싣는 **의미 색** — 전경/배경 각각의 이름(`success`·`foreground`).
///
/// 왜 이름인가: 색을 정하는 것은 **이 클라의 테마**다. 달력의 '오늘'은 강조색을
/// **배경**에 깔므로 전경만으로는 못 나른다 — 그래서 자리마다 이름을 따로 싣는다.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Deserialize)]
pub struct ThemeRef {
    #[serde(default)]
    pub f: Option<String>,
    #[serde(default)]
    pub b: Option<String>,
}

/// 상태줄에 붙는 **표식 한 칸**(설계 §1.2 의 ③ · P6).
///
/// 종전에는 이 자리가 통째로 비어 있었다 — `Badge` 열거형에 우리가 아는 다섯이
/// 박혀 있고, 정본의 `rec`·`model`·`usage`·`perm` 은 "플러그인이 채우는 칸이라
/// 우리에게는 없다"고 적혀 있었다. 이제 **서버가 자료로 준다**: 우리는 글자와 의미
/// 색만 받아 칩으로 그린다. 플러그인을 지우면 배지도 함께 사라진다.
///
/// **누르는 자리는 아직 없다.** 정본의 REC 배지는 누르면 캡처 정보 팝업이 뜨는데 그
/// 화면은 Tier C(④)이고 우리에겐 아직 없다 — 그래서 서버도 `do` 를 안 싣는다. 없는
/// 것을 실어 두면 "선언은 있고 배선이 없는" 칸이 하나 더 생긴다.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Deserialize)]
pub struct PluginBadge {
    /// 이 배지를 낸 플러그인 이름(레지스트리가 찍는다).
    #[serde(default)]
    pub name: String,
    /// 보일 글자. 정본과 같은 문자열이라 좌우 여백까지 그대로 온다(` REC `).
    ///
    /// ⚠ **직접 읽지 말고 [`PluginBadge::say`] 를 쓴다** — 이 값은 서버 로케일이다.
    #[serde(default)]
    pub text: String,
    /// 이 배지의 글을 우리 로케일로 다시 지을 재료(`{"text": …}`).
    #[serde(default)]
    pub i18n: I18nMap,
    /// 서버가 화면 런에 쓰는 것과 **같은 축약 스타일**. 새 표기가 아니다.
    #[serde(default)]
    pub style: crate::message::Style,
    /// 의미 색 이름 — 런과 같은 규약이다(hex 는 안 온다).
    #[serde(default)]
    pub theme: ThemeRef,
    /// 이 표식의 **갈래**(`model`·`usage`·`pending`·`warn`). 정본이 클릭존을 붙이는 데
    /// 쓰는 이름이고, 서버는 처음부터 실어 보내고 있었다.
    ///
    /// 우리는 이 값으로 **판단하지 않는다** — 무엇이 열리는지는 [`open`](Self::open) 이
    /// 말한다. 갈래를 보고 클라가 화면을 고르면 그 표가 서버와 갈리기 시작한다.
    #[serde(default)]
    pub kind: String,
    /// 누르면 열 **플러그인 명령 이름**(없으면 이 표식은 안 눌린다).
    ///
    /// 뜻은 모른 채 그대로 `plugin_open` 으로 되돌려 보낸다 — 오버레이의 `do` 와 같은
    /// 규약이다(설계 §4.4: 행동은 서버가 정한다). 서버는 **그 화면이 실제로 있을 때만**
    /// 싣는다: 안 그러면 눌리는 것처럼 보이고 아무 일도 안 나는 칸이 생긴다.
    #[serde(default, rename = "do")]
    pub open: String,
}

impl PluginBadge {
    /// 보일 글자 — **이 클라의 로케일로**. `text` 를 직접 읽으면 서버 로케일이 샌다.
    pub fn say(&self) -> String {
        i18n_say(&self.i18n, "text", &self.text)
    }

    /// 누르면 열 플러그인 명령(없으면 `None` — 그리기만 하는 표식이다).
    pub fn opens(&self) -> Option<&str> {
        (!self.open.is_empty()).then_some(self.open.as_str())
    }
}

impl PluginRun {
    /// 이 런의 글 — **이 클라의 로케일로**.
    ///
    /// ⚠ 로케일이 바뀌면 **폭도 바뀐다**(`세션 5h` ↔ `Session 5h`). 런은 좌표를 갖고
    /// 오므로 서버가 잰 폭과 어긋날 수 있다 — 그래서 지금은 **줄 하나가 통째로 한 런**인
    /// 자리에만 재료를 싣는다(그 줄은 옆에 붙는 것이 없어 밀 것도 없다).
    pub fn say(&self) -> String {
        i18n_say(&self.i18n, "text", &self.text)
    }
}

/// 목록 한 줄. `key` 는 **그 줄의 뜻**(CL 번호·파일 이름)이고 액션에 그대로 실어 보낸다 —
/// 자리(번호)로 가리키면 목록이 바뀔 때 엉뚱한 줄이 열린다.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Deserialize)]
pub struct PluginRow {
    #[serde(default)]
    pub key: String,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub cols: Vec<String>,
    /// 이 줄이 **무엇인가** — 색을 정하는 의미 이름(`dir`·`hidden`·`tagged` …).
    ///
    /// # 왜 스타일이 아니라 이름인가 (pytmux-11·12 A)
    ///
    /// 제보: *"컬러 스킴 일치가 특히 중요하다."* 정본은 줄마다 색을 달리 칠하는데 그
    /// 판정이 Textual 화면 안에 있어 서버가 못 불렀고, 이 구조체에는 실을 칸도 없었다 —
    /// 그래서 네이티브 클라의 mdir 은 줄이 **전부 같은 색**이었다.
    ///
    /// 이제 판정은 `plugins/mdir/rowtag.py` 한 벌이고 서버가 **이름만** 싣는다. hex 를
    /// 실으면 서버가 UI 를 알게 된다(설계 §10). 값으로 바꾸는 것은 [`crate::rowtag`] 다.
    ///
    /// 빈 문자열이면 특별한 뜻이 없다 — 그 줄은 기본색으로 뜬다.
    #[serde(default)]
    pub tag: String,
    /// 트리에서 이 줄의 **깊이**(0 = 뿌리). 목록형 화면은 0 이다.
    ///
    /// 들여쓰기를 **글자로 미리 넣지 않는** 이유(pytmux-11 B): 그러면 이름에 공백이
    /// 섞여 `label` 이 더는 자료가 아니게 되고, 타이핑 찾기·복사가 그 공백을 물고 간다.
    #[serde(default)]
    pub depth: u16,
    /// 펼침 상태 — `open`(펼침) · `shut`(접힘) · `""`(펼 것이 없다).
    ///
    /// 세 갈래인 이유: 접힘과 **잎**은 다르다. 둘을 하나로 묶으면 빈 디렉터리에도
    /// `▸` 가 붙어 눌러도 아무 일이 없는 화살표가 생긴다(정본이 그래서 셋을 가른다).
    #[serde(default)]
    pub expand: String,
    /// 이 줄의 **글**을 우리 로케일로 다시 지을 재료(`{"label": …}`).
    ///
    /// # 왜 칸(`cols`)처럼 그냥 번역하지 않나
    ///
    /// [`say_cols`](Self::say_cols) 가 적어 둔 갈림 그대로다: `label` 은 보통 **자료**라
    /// (파일 이름·CL 번호) 번역하면 `복사` 라는 이름의 파일이 `Copy` 로 보인다. 그런데
    /// 화면에 따라서는 그 자리가 **말**이다 — 권한모드 선택의 `auto — 모든 동작 자동
    /// 수락…`(pytmux-2)이 첫 사례다.
    ///
    /// 그래서 판정을 우리가 하지 않고 **플러그인이 실어 보낸다**: 재료가 오면 말이고,
    /// 안 오면 자료다. 오늘 이 칸을 채우는 화면은 권한모드 하나이고 mdir·ncd 의 이름은
    /// 종전 그대로 손대지 않는다.
    #[serde(default)]
    pub i18n: I18nMap,
}

impl PluginRow {
    /// 줄의 글 — **말이면** 이 클라의 로케일로, 자료면 그대로.
    pub fn say_label(&self) -> String {
        match self.i18n.get("label") {
            Some(p) if !p.fmt.is_empty() => i18n_say(&self.i18n, "label", &self.label),
            // ⚠ `i18n_say` 의 폴백은 `t(fallback)` 이라 **번역한다** — 이름을 그리로
            // 흘리면 위 문단의 `복사` 문제가 그대로 돌아온다. 그래서 여기서 끊는다.
            _ => self.label.clone(),
        }
    }

    /// 부가 칸을 **이 클라의 로케일로**.
    ///
    /// # 왜 칸만인가 (2026-08-02p)
    ///
    /// `label` 은 **자료**다 — 파일 이름·디렉터리 이름·CL 번호. 거기에 `t()` 를 걸면
    /// `복사` 라는 이름의 파일이 `Copy` 로 보인다. 반대로 `cols` 는 플러그인이 **적은
    /// 말**이다(`<상위>`·`<드라이브>`·실패 사유). 그래서 번역은 칸에만 건다.
    ///
    /// 이 갈림이 없던 동안 `mdir` 의 `<상위>`·`<드라이브>` 는 영어 사용자에게 한국어로
    /// 떴다 — 카탈로그로 옮겨도 **서버 로케일**로 지어지므로 여기서 다시 짓지 않으면
    /// 그대로다(`title`·`hint`·`note` 만 [`i18n_say`] 를 거치고 있었다).
    pub fn say_cols(&self) -> Vec<String> {
        self.cols
            .iter()
            .map(|c| base::i18n::t(c).to_owned())
            .collect()
    }
}

/// `status` 가 실어 오는 **세션 전역 상태**(패리티 G6).
///
/// # 왜 따로 두나
///
/// 탭바는 탭마다의 것이고 이건 **지금 보고 있는 창 전체**의 것이다(줌·동기화·감시).
/// 파이썬 클라는 이걸 상태줄에 낱말로 붙인다 — 안 보이면 사용자는 자기가 줌 안에 있다는
/// 것도, 입력이 모든 패널로 복제되고 있다는 것도 모른다. **후자는 특히 위험하다.**
#[derive(Debug, Clone, Default, PartialEq)]
pub struct StatusFlags {
    /// 활성 패널이 창 전체로 확대돼 있는가.
    pub zoomed: bool,
    /// 입력이 창 안 모든 패널로 복제되는가(`synchronize-panes`).
    pub sync: bool,
    /// 활성 패널의 제목(서버가 OSC 로 받은 것).
    pub pane_title: String,
    /// 탭 이름을 서버가 자동으로 바꾸는가.
    pub auto_rename: bool,
    /// 패널 테두리에 제목을 항상 보이는가(서버 옵션 `pane-border-status`).
    pub border_status: bool,
    pub monitor_activity: bool,
    pub monitor_bell: bool,
    /// 패널 하나일 때도 테두리를 그리는가.
    pub single_border: bool,
    pub coalesce_repaints: bool,
    pub nest_auto_attach: bool,
    pub win_mouse_motion: bool,
    /// exit-empty 현재값(서버 전역 옵션 — 세션 0개면 서버 종료).
    ///
    /// `Option` 인 이유: 이 칸은 2026-07-30 에야 서버 status 에 실렸다(그 전엔
    /// 파이썬 클라도 '미상'). 구버전 서버는 안 보내는데, 기본 `false` 로 받으면
    /// 서버 기본(on)과 반대인 **거짓말**이 된다 — 모르면 모른다(`None` → `?`)고 둔다.
    pub exit_empty: Option<bool>,
    /// VT 파서 백엔드 이름(서버 옵션). 서버가 안 보내면 빈 문자열이다.
    pub vt_parser: String,
    /// 공유 크기 규칙 이름(서버 옵션).
    pub window_size: String,
    /// 토큰리밋 **자동재개**가 켜져 있나(claude-code 플러그인 — `prefix R` 로 뒤집는다).
    ///
    /// 서버가 활성 패널 기준으로 `status` 에 싣는다. 플러그인이 없으면 그 칸이 안 와
    /// 기본값 `false` 로 떨어진다 — 그때는 `prefix R` 도 무동작이라 앞뒤가 맞는다.
    pub autoresume: bool,
    /// 프롬프트 단위 클리어가 켜져 있나(claude-code 플러그인 — 완료마다 문서화+`/clear`).
    ///
    /// 자동재개와 같은 자리다: 서버가 활성 패널 기준으로 `status` 에 싣고, 플러그인이
    /// 없으면 칸이 안 와 `false` 다.
    pub prompt_clear: bool,
    /// 출력 캡처(REC)가 켜져 있나 — **rec 서버 플러그인**이 status 에 싣는다.
    /// `None` 이면 플러그인 부재(그때는 REC 탭 자체가 없다 — delete-to-disable 동형).
    pub capture: Option<bool>,
    /// 캡처 파일 경로(켜져 있고 파일이 준비된 뒤에만).
    pub capture_path: String,
    /// 캡처 파일 크기(바이트).
    pub capture_size: i64,
}

impl StatusFlags {
    /// `status` 메시지에서 뽑는다. 없는 필드는 기본값이다(구버전 서버 대비).
    pub fn from_status(status: &crate::message::Status) -> Self {
        let flag = |key: &str| {
            status
                .fields
                .get(key)
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false)
        };
        let text = |key: &str| {
            status
                .fields
                .get(key)
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_owned()
        };
        Self {
            zoomed: flag("zoomed"),
            sync: flag("sync"),
            pane_title: status
                .fields
                .get("pane_title")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_owned(),
            auto_rename: flag("auto_rename"),
            border_status: flag("border_status"),
            single_border: flag("single_border"),
            coalesce_repaints: flag("coalesce_repaints"),
            nest_auto_attach: flag("nest_auto_attach"),
            win_mouse_motion: flag("win_mouse_motion"),
            exit_empty: status
                .fields
                .get("exit_empty")
                .and_then(serde_json::Value::as_bool),
            vt_parser: text("vt_parser"),
            window_size: text("window_size"),
            monitor_activity: flag("monitor_activity"),
            monitor_bell: flag("monitor_bell"),
            autoresume: flag("autoresume"),
            prompt_clear: flag("prompt_clear"),
            capture: status.fields.get("capture").and_then(serde_json::Value::as_bool),
            capture_path: text("capture_path"),
            capture_size: status
                .fields
                .get("capture_size")
                .and_then(serde_json::Value::as_i64)
                .unwrap_or(0),
        }
    }

    /// 상태줄에 붙일 낱말들. **켜진 것만** 나온다 — 꺼진 것까지 적으면 줄이 길어져
    /// 정작 켜진 것이 눈에 안 띈다.
    ///
    /// 두 뷰가 같은 낱말을 쓰도록 여기서 만든다(색은 뷰가 정한다).
    /// [`tab_badges`](Self::tab_badges) + [`monitor_badges`](Self::monitor_badges)
    /// 순서 그대로다 — 자리를 안 가르는 뷰(TUI)는 이걸 그대로 쓴다.
    pub fn badges(&self) -> Vec<&'static str> {
        let mut out = self.tab_badges();
        out.extend(self.monitor_badges());
        out
    }

    /// 탭바 앞에 붙는 표식 — 모르고 두면 **입력·동작이 달라지는** 상태들(줌·동기화·
    /// 자동재개·프롬프트클리어). 눈앞(위쪽)에 있어야 하는 부류다(패리티 G6).
    pub fn tab_badges(&self) -> Vec<&'static str> {
        let mut out = Vec::new();
        if self.zoomed {
            out.push(t("[줌]"));
        }
        // ★ 동기화는 **입력이 복제되는 상태**다. 모르고 치면 모든 패널에서 같은 명령이
        // 돈다 — 표식 중 가장 위험한 것이라 항상 보인다.
        if self.sync {
            out.push(t("[동기화]"));
        }
        // ★ 자동재개는 **꺼져 있을 때가 기본**이라, 켜져 있다는 사실이 보여야 한다 —
        // 켜 두면 토큰 한도에 걸린 뒤 클라 없이도 서버가 대화를 이어 붙인다. 모르고
        // 켜 둔 채 자리를 비우면 의도 없이 진행되는 셈이라 동기화와 같은 부류다.
        if self.autoresume {
            out.push(t("[자동재개]"));
        }
        // 자동재개와 같은 이유다 — 켜 두면 완료마다 패널이 문서화+`/clear` 를 돌린다.
        if self.prompt_clear {
            out.push(t("[프롬프트클리어]"));
        }
        out
    }

    /// 감시류 표식(활동감시·벨감시) — 입력을 바꾸지 않는 **상시 상태**라, 파이썬 정본이
    /// 시스템 배지를 두는 하단 상태줄 곁이 자리다(사용자 요청 2026-07-30 — GUI 가
    /// 이걸 하단에 그린다).
    pub fn monitor_badges(&self) -> Vec<&'static str> {
        let mut out = Vec::new();
        if self.monitor_activity {
            out.push(t("[활동감시]"));
        }
        // 감춘 표면(§10-21ⓜ) — 켜져 있어도 표식을 안 그린다. 기능은 그대로다.
        if self.monitor_bell && !base::keymap::is_hidden("monitor-bell") {
            out.push(t("[벨감시]"));
        }
        out
    }
}

/// 트리 목록의 한 줄. 화면은 이걸 그리고, 고른 줄의 `window`/`pane` 으로 명령을 만든다.
#[derive(Debug, Clone, PartialEq)]
pub struct TreeRow {
    /// 들여쓰기 깊이(0 = 세션 · 1 = 탭 · 2 = 패널). 그리는 쪽이 공백으로 옮긴다.
    pub depth: usize,
    pub label: String,
    /// 이 줄이 가리키는 탭 index. 세션 줄이면 `None`(고를 수 없다).
    pub window: Option<usize>,
    /// 이 줄이 가리키는 패널 id. 탭 줄이면 `None`.
    pub pane: Option<i64>,
    /// 지금 활성 탭인가(표식용).
    pub active: bool,
}

/// 마우스 패스스루의 대상 — [`SessionState::mouse_pane_at`] 의 답.
///
/// 참조가 아니라 값인 이유: 대상이 일반 패널(`PaneLayout`)일 수도 팝업
/// (`PopupLayout`)일 수도 있어, 뷰가 쓰는 세 가지(어디로 · 어떤 사각형 기준으로 ·
/// 어떤 인코딩으로)만 추려 한 모양으로 돌려준다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MouseTarget {
    /// 리포트를 받을 패널 id (`Outgoing::Mouse` 의 pane).
    pub id: i64,
    /// 내용 사각형 `(x, y, w, h)` — [`crate::mouse::encode`] 의 rect 인자.
    pub rect: (u16, u16, u16, u16),
    /// 추적 수준·인코딩(SGR 여부).
    pub mode: crate::mouse::MouseMode,
}

/// 의미 색 이름 → 이 클라의 색. **플러그인 이름이 안 나오는 표**다.
///
/// # 왜 이름만 받나
///
/// 종전에는 여기 `clock_digit()`·`calendar()` 처럼 **플러그인마다 한 자리씩** 있었다.
/// 그건 INV5 가 빚이라고 부르는 것이다 — 플러그인을 지워도 그 이름이 Rust 에 남고,
/// 새 오버레이가 생기면 여기도 같이 고쳐야 한다(그리고 잊는다).
///
/// 이제 서버가 런에 **의미 이름**을 실어 준다(`{"f": "success"}`). 우리가 아는 것은
/// "이 이름은 이 색"뿐이고, 어느 자리에 어떤 이름을 쓸지는 플러그인 한 벌이 정한다.
/// 정본은 같은 이름을 `theme_color(app, name)` 으로 자기 테마에서 푼다 — 값이 아니라
/// **이름이 옮겨 다니는** 것이 이 설계의 요점이다(설계 §10 위험표).
/// ⛔ **어휘가 갈리면 화면이 조용히 빈다**(pytmux-16, 2026-08-03 실측). 모르는 이름은
/// 색을 안 칠하는데, 런에 실린 리터럴이 어두운 글자면 그 자리는 **아무것도 안 보인다** —
/// 예외도 로그도 없다. ime-indicator 가 정확히 그 함정을 밟았다:
///
/// ```text
/// _THEME = {"한": "success", "EN": "primary"}       # 정본 플러그인
/// run = {"style": {"f": "black", "bo": 1}, "theme": {"b": _THEME[label]}}
/// ```
///
/// `success` 는 알아서 `[한]` 이 밝은 초록 바탕에 떴는데 **`primary` 가 표에 없어**
/// `[EN]` 은 검은 글자만 남았다. 캡처의 화소로 확인했다 — 한글 컷에는 배지 바탕색이
/// 640화소, 영문 컷에는 **0화소**. 제보에는 "영문 모드에서는 배지가 사라진다"로 적혔다.
///
/// 그래서 어휘를 **정본에서 뽑아 고정**한다(`scripts/gen_theme_names.py` →
/// `tests/fixtures/theme_names.json`). 정본의 어휘는 `clientutil._THEME_FALLBACK` 의
/// 키 전부이고, 그 하나하나를 여기서 **명시로** 처리해야 한다. "몰라서 None"과
/// "알지만 기본색"을 구분하려고 [`resolve`] 가 [`Resolution`] 을 돌려주고,
/// `the_theme_vocabulary_is_fully_known` 이 픽스처로 전수를 잰다.
pub mod theme {
    use crate::style::{Color, NamedColor};

    /// 이름을 어떻게 처리했나. `color()` 만으로는 **모르는 이름**과 **일부러 기본색**이
    /// 둘 다 `None` 이라 갈리지 않는다 — 게이트가 재는 것이 바로 그 구분이다.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum Resolution {
        /// 이 색으로 칠한다.
        Color(Color),
        /// 뜻은 알지만 **칠하지 않는다**(= 기본색이 정답).
        Default,
        /// 모르는 이름. 새 이름이 생겼다는 뜻이니 표에 한 줄을 더한다.
        Unknown,
    }

    /// 이름 하나의 처리. 표의 정본은 여기 하나다.
    pub fn resolve(name: &str) -> Resolution {
        use Resolution::{Color as C, Default as D, Unknown as U};
        match name {
            // 정본 `success`(강조 초록). 시계 숫자·달력 제목·오늘 바탕이 이 이름을 쓴다.
            "success" => C(Color::Named(NamedColor::BrightGreen)),
            // ime 영문 배지·정본의 주 강조. **없어서 [EN] 이 안 보였다**(pytmux-16).
            "primary" => C(Color::Named(NamedColor::BrightBlue)),
            // claude 상태 표식의 바탕. primary 보다 가라앉은 파랑이라야 표식이 글자를
            // 안 잡아먹는다(정본도 `secondary` 가 `primary` 보다 어둡다).
            "secondary" => C(Color::Named(NamedColor::Blue)),
            "accent" => C(Color::Named(NamedColor::BrightYellow)),
            "warning" => C(Color::Named(NamedColor::Yellow)),
            "error" => C(Color::Named(NamedColor::BrightRed)),
            // ── 아래는 "칠하지 않는 것이 정답"인 이름들 ──
            // "특별한 색이 아니다"가 곧 기본값이다.
            "foreground" => D,
            // 바탕 계열: 우리 바탕은 **우리 테마**가 정한다(`gui::theme`). 서버가 준
            // 이름으로 캔버스 바닥을 덮으면 크롬과 캔버스 배색이 갈린다.
            "background" | "surface" | "panel" => D,
            // 정본 Textual 테마의 파생 변수(진한 파랑 두 단계). 우리 팔레트에는 대응이
            // 없고, 지금 이 이름을 런에 싣는 플러그인도 없다 — 생기면 색을 준다.
            "primary-darken-2" | "primary-darken-3" => D,
            _ => U,
        }
    }

    /// 모르는 이름이면 `None` — 그 자리는 런에 실린 리터럴이나 기본색이 지킨다.
    /// (이름을 모른다고 글자를 안 그리지는 않는다.)
    pub fn color(name: &str) -> Option<Color> {
        match resolve(name) {
            Resolution::Color(c) => Some(c),
            Resolution::Default | Resolution::Unknown => None,
        }
    }
}

/// 서버 세션의 현재 모습.
#[derive(Debug, Default, Clone)]
pub struct SessionState {
    layout: Option<Layout>,
    /// 패널 id → 마지막으로 받은 화면.
    screens: HashMap<i64, Screen>,
    /// 마지막 드라이런 회신의 **원본 칸들**(판정용). 회신 전에는 비어 있다.
    restart_check_fields: serde_json::Map<String, serde_json::Value>,
    /// 어느 패널이 **Claude 패널**인가(서버 `status` 의 `panes_claude`).
    ///
    /// 이 게이트가 없으면 셸 프롬프트(`~/dir ❯ `)를 입력 텍스트로 오긁는다 — 파이썬
    /// `client_prompt_text` 도 같은 이유로 `pane_claude` 로 먼저 거른다. **활성 윈도우의
    /// 패널만** 실려 오므로, 탭을 바꾸면 이 표도 그 탭의 것으로 갈린다.
    claude_panes: std::collections::HashSet<i64>,
    /// 지금 Claude 모델 이름(서버 `status` 의 `claude_model` — 예 `opus-5`).
    claude_model: Option<String>,
    /// 5시간 한도 사용률 %(서버 `tok5h_pct`). 분모를 모르면 서버가 안 보낸다.
    claude_5h_pct: Option<u8>,
    /// 플러그인이 낸 상태줄 표식들(Tier B ③ · 서버 `status` 의 `plugin_badges`).
    ///
    /// **매 status 마다 통째로 갈아 끼운다** — 안 오면 없는 것이다(위 파싱부 주석).
    plugin_badges: Vec<PluginBadge>,
    /// 패널 id → 서버가 판정한 블록 목록(§10-13).
    ///
    /// 셸 통합을 안 깐 사용자에게는 영원히 비어 있다 — 그게 정상이다.
    blocks: HashMap<i64, Vec<Block>>,
    tabs: TabBar,
    /// 서버가 연결을 닫았는가.
    closed: bool,
    /// RTT 표본과 응답성(G9u) — pong 이 올 때마다 뷰가 채운다.
    rtt: crate::rtt::RttHist,
    /// 마지막으로 받은 오류 메시지.
    last_error: Option<String>,
    /// `request_version` 회신을 사람이 읽을 한 줄로. 묻기 전에는 `None`.
    version: Option<String>,
    /// `request_restart_check` 회신을 사람이 읽을 줄들로. 묻기 전에는 비어 있다.
    restart_check: Vec<String>,
    /// `run-shell` 이 마지막으로 낸 출력(줄 단위). 상한은 파이썬과 같은 40줄이다.
    shell_output: Vec<String>,
    /// 지나간 알림들(패리티 G6c). **새것이 앞**이다 — 화면이 그대로 위에서부터 그린다.
    ///
    /// 길이를 묶어 두는 이유: 오래 붙어 있는 클라에서 이 목록만 끝없이 자란다. 파이썬도
    /// 이력 화면에 상한이 있다.
    notices: std::collections::VecDeque<Notice>,
    /// 기준(직전 full `screen`) 없이 델타만 온 패널.
    ///
    /// 그 패널은 바뀐 행을 얹을 바탕이 없어 **영원히 빈 채로 남는다**(원격 attach 에서
    /// 상류의 첫 full 이 '보는 클라 없음'으로 드롭될 때 실제로 생긴다). 그래서 다시
    /// 그려 달라고 한 번 청한다 — 여기 담긴 것은 그 **중복 요청을 막는 표식**이고,
    /// full 을 받으면 지운다. 파이썬 클라의 `_delta_no_base` 와 같은 자리다.
    delta_no_base: std::collections::HashSet<i64>,
    /// 서버가 광고한 플러그인 목록(패리티 G7). **full status 에만** 실려 온다.
    /// 플러그인이 기여한 데이터 표면(Tier A). 서버가 full status 에 실어 준다.
    plugin_surface: PluginSurface,
    plugins: Vec<PluginInfo>,
    /// 지금 떠 있는 플러그인 화면들(설계 Tier C · P4).
    ///
    /// **스택인 이유**: 목록에서 상세로 들어갔다가 `Esc` 로 **목록으로 돌아와야** 한다.
    /// 서버에 다시 물으면 p4 를 또 부르고(느리다) 그 사이 목록이 달라질 수도 있다 —
    /// 방금 보던 판을 그대로 되살리는 편이 정본의 손과도 같다.
    plugin_screens: Vec<PluginScreen>,
    /// 서버가 준 셀 기여(설계 Tier B · P3). 시계가 여기로 온다 — **우리가 그리지
    /// 않는다.** 로직(어느 폰트·어디에 중앙 정렬)이 플러그인 한 벌로 남는다.
    plugin_cells: PluginCells,
    /// 서버가 알려 준 세션 이름(`status.session`). 상태줄 `#S` 가 쓴다.
    session: String,
    /// **켠 사실**만 우리 것이다 — `{오버레이 이름: 켠 패널 집합}`.
    ///
    /// 그림도, 그 오버레이가 넘겨 본 달 같은 상태도 서버가 든다(설계 Tier B · P3).
    /// 여기 남는 이유는 토글이 "지금 켜져 있나"를 물어야 하기 때문이고, 그래서 이름이
    /// 열쇠다 — 오버레이가 하나 늘어도 이 자료형은 안 바뀐다(INV5).
    overlays: std::collections::HashMap<String, std::collections::HashSet<i64>>,
    /// 비활성 패널을 흐리게 하는 세기. `None` 이면 끔(설정 `inactive-dim`).
    ///
    /// **왜 상태에 두나**: 합성이 이 값을 쓰는데 합성은 proto 에 있고 설정은 core 에
    /// 있다(그리고 proto 는 core 의 `Config` 를 안 읽는다 — 뷰가 넣어 준다).
    inactive_dim: Option<f32>,
    /// 입력기 배지(`[한]`/`[EN]`). **뷰가 넣는다** — 입력기 상태는 OS 것이고 proto 는
    /// 그것을 모른다(`inactive_dim` 과 같은 갈래).
    ///
    /// 왜 상태줄이 아니라 여기인가: 이 배지는 **다음 글자가 무엇이 될지**를 말한다.
    /// 그때 눈은 커서에 있지 상태줄에 있지 않다 — 정본도 그래서 2026-06-16 에 이
    /// 배지를 화면 끝에서 **활성 패널 우상단**으로 옮겼다.
    /// 패널 번호를 띄우고 있나(`prefix q` — tmux `display-panes`).
    ///
    /// **다음 키 하나로 사라진다**(파이썬도 모드라 그렇다) — 그 판정은 뷰가 한다.
    pane_numbers: bool,
    /// 터치 스크롤바를 쓰나(설정 `touch-scroll`, 기본 켬 — 뷰가 넣는다).
    touch_scroll: bool,
    /// 지금 스크롤 모드인가(뷰의 `InputMode` 를 옮겨 받는다).
    ///
    /// proto 가 모드를 갖는 것이 아니라 **그 사실을 안다**: 스크롤바는 스크롤 모드에서만
    /// 그려야 하고(라이브 화면의 마지막 열을 덮으므로), 그리는 자리는 두 뷰가 공유하는
    /// [`composite`](Self::composite) 다.
    scroll_mode: bool,
    /// 상태줄에 걸리는 세션 전역 표식들(패리티 G6). 서버가 `status` 에 매번 실어 준다 —
    /// 종전에는 **탭 목록만 꺼내 쓰고 나머지를 버리고 있었다**.
    flags: StatusFlags,
    /// `request_tree` 회신(패리티 G3b). **요청해야 오는 것**이라 없을 수 있다.
    tree: Option<Tree>,
    /// `request_buffers` 회신 — 페이스트 버퍼 목록.
    buffers: Vec<BufferItem>,
    /// 위 요청을 아직 안 보냈다는 표식. 뷰가 [`take_redraw_request`](Self::take_redraw_request)
    /// 로 가져간다 — 상태 누적기는 서버로 무엇을 보낼 수 없다.
    want_redraw: bool,
}

impl SessionState {
    /// 새 상태. **`touch_scroll` 만 `true` 로 시작한다** — 설정 기본이 켬이고
    /// (`base::Config::default`), 뷰가 설정을 읽기 전 한 프레임이라도 꺼져 보이면
    /// `⇕` 배지가 깜빡인다. 나머지는 `Default` 그대로다(모르는 것은 끈다).
    pub fn new() -> Self {
        Self { touch_scroll: true, ..Self::default() }
    }

    /// 메시지 하나를 반영한다. **화면이 달라졌으면** `true`.
    ///
    /// 반환값으로 다시 그릴지 정한다 — 서버는 조용할 때도 `pong` 같은 것을 보내므로
    /// 모든 메시지에 repaint 를 걸면 낭비다.
    pub fn apply(&mut self, msg: ServerMessage) -> bool {
        match msg {
            ServerMessage::Layout(layout) => {
                // 새 배치에 없는 패널의 화면은 버린다 — 안 그러면 죽은 패널의 스냅샷이
                // 영원히 남아 메모리와 혼란을 함께 키운다.
                let alive: Vec<i64> = layout.panes.iter().map(|p| p.id).collect();
                self.screens.retain(|id, _| alive.contains(id));
                self.blocks.retain(|id, _| alive.contains(id));
                self.layout = Some(layout);
                true
            }
            ServerMessage::Screen(screen) => {
                // full 이 왔다 = 기준이 생겼다. 다음 델타부터는 얹을 바탕이 있다.
                self.delta_no_base.remove(&screen.pane);
                self.screens.insert(screen.pane, screen);
                true
            }
            ServerMessage::ScreenDelta(delta) => self.apply_delta(delta),
            // 요청해서 받은 목록들(G3b). 화면이 열려 있으면 그 화면이 이걸 그린다.
            ServerMessage::Tree(tree) => {
                self.tree = Some(tree);
                true
            }
            ServerMessage::Buffers { items } => {
                self.buffers = items;
                true
            }
            ServerMessage::PluginScreen(screen) => {
                // 같은 화면의 **갱신**이면 덮고, 새 화면이면 쌓는다(목록 → 상세).
                match self.plugin_screens.last_mut() {
                    Some(top) if top.id == screen.id && top.kind == screen.kind => {
                        *top = screen;
                    }
                    _ => self.plugin_screens.push(screen),
                }
                true
            }
            ServerMessage::PluginCells(cells) => {
                let changed = cells != self.plugin_cells;
                self.plugin_cells = cells;
                changed
            }
            ServerMessage::PluginScreenClose { id } => {
                let before = self.plugin_screens.len();
                self.plugin_screens.retain(|s| s.id != id);
                before != self.plugin_screens.len()
            }
            ServerMessage::Status(status) => {
                // 세션 이름은 full status 에만 실릴 수 있다 — 안 왔으면 들고 있던 것을
                // 지킨다(델타마다 비우면 `#S` 가 깜빡인다).
                if let Some(name) = status.fields.get("session").and_then(|v| v.as_str())
                    && !name.is_empty()
                {
                    self.session = name.to_owned();
                }
                let tabs = TabBar::from_status(&status);
                let flags = StatusFlags::from_status(&status);
                // Claude 모델·5시간 한도 — **안 왔으면 들고 있던 것을 지킨다**(델타마다
                // 비우면 배지가 깜빡인다. 파이썬 클라도 같은 규칙이다 —
                // `clientstatus.py`: "usage 가 비어 와도 마지막 비-빈 값을 유지").
                if let Some(model) = status.fields.get("claude_model").and_then(|v| v.as_str())
                    && !model.is_empty()
                {
                    self.claude_model = Some(model.to_owned());
                }
                if let Some(pct) = status.fields.get("tok5h_pct").and_then(|v| v.as_i64()) {
                    // 서버가 100 을 넘겨 보낸 적이 있다(파이썬도 클램프한다 —
                    // "999%/5h" 버그를 그렇게 막았다).
                    self.claude_5h_pct = Some(pct.clamp(0, 100) as u8);
                }
                // 패널별 Claude 여부. **안 왔으면 들고 있던 것을 지킨다** — 델타마다
                // 비우면 작성창 인계가 status 사이에서 깜빡인다(플러그인 개요와 같은 규칙).
                if let Some(panes) = status.fields.get("panes_claude").and_then(|v| v.as_array())
                {
                    self.claude_panes = panes
                        .iter()
                        .filter(|entry| {
                            entry.get("claude").and_then(serde_json::Value::as_bool) == Some(true)
                        })
                        .filter_map(|entry| entry.get("id").and_then(serde_json::Value::as_i64))
                        .collect();
                }
                // 상태줄 표식(Tier B ③) — ★ **안 오면 「없음」이다.** 위 이웃들과
                // 반대 규칙이라 적어 둔다: 저것들은 델타에 안 실릴 수 있어 "안 왔으면
                // 지킨다"가 맞지만, 배지는 서버가 **매 status 마다 다시 만들고 비면
                // 키를 뺀다**. 여기서 지키면 캡처를 끈 뒤에도 REC 가 영영 남는다.
                self.plugin_badges = status
                    .fields
                    .get("plugin_badges")
                    .and_then(|v| serde_json::from_value(v.clone()).ok())
                    .unwrap_or_default();
                // 개요는 **full 에만** 온다(목록은 서버가 도는 동안 안 변한다). 델타
                // status 마다 비우면 관리 화면이 깜빡이므로, 안 왔으면 들고 있던 것을
                // 지킨다 — 대신 켜짐/꺼짐은 매번 오는 `disabled_plugins` 로 덮는다.
                //
                // 바뀐 것만 대입한다(통째로 갈아 끼우고 `true` 를 돌려주면 status 가 올
                // 때마다 화면을 다시 그린다 — 이 반환값의 존재 이유가 그걸 막는 것이다).
                let mut plugins_changed = false;
                if let Some(surface) = plugin_surface_from_status(&status)
                    && surface != self.plugin_surface
                {
                    self.plugin_surface = surface;
                    plugins_changed = true;
                }
                if let Some(overview) = plugins_from_status(&status)
                    && overview != self.plugins
                {
                    self.plugins = overview;
                    plugins_changed = true;
                }
                if let Some(disabled) = disabled_plugins_from_status(&status) {
                    for plugin in self.plugins.iter_mut() {
                        let enabled = !disabled.iter().any(|off| *off == plugin.name);
                        if plugin.enabled != enabled {
                            plugin.enabled = enabled;
                            plugins_changed = true;
                        }
                    }
                }
                let changed = tabs != self.tabs || flags != self.flags || plugins_changed;
                self.tabs = tabs;
                self.flags = flags;
                changed
            }
            ServerMessage::Blocks { pane, blocks } => {
                let changed = self.blocks.get(&pane) != Some(&blocks);
                self.blocks.insert(pane, blocks);
                changed
            }
            ServerMessage::Bye => {
                self.closed = true;
                true
            }
            ServerMessage::Error { msg } => {
                self.push_notice(Notice::from(Severity::Error, msg.clone(), Source::Server));
                self.last_error = Some(msg);
                true
            }
            ServerMessage::RestartCheck { fields } => {
                // 사람이 읽을 줄로 푼다. **이름순**이라 서버가 필드를 늘려도 자리가
                // 흔들리지 않는다(`serde_json::Map` 은 넣은 순서를 지킬 수도, 아닐 수도
                // 있다 — 여기서 정렬해 못박는다).
                let mut rows: Vec<String> = fields
                    .iter()
                    .map(|(k, v)| format!("{k}: {v}"))
                    .collect();
                rows.sort();
                self.restart_check = rows;
                // ★ 원본 칸도 들고 있는다. 줄만 남기면 **판정에 쓸 값이 없다** —
                // 드라이런 게이트(`base::restart::evaluate`)가 이 칸들을 본다.
                self.restart_check_fields = fields;
                true
            }
            ServerMessage::Version { version, uptime, pid } => {
                // ★ 값 중 서버가 준 문자열(version)을 **마지막에** 끼운다 — 값 안의
                // `{...}` 가 재치환되지 않게(`i18n::tf` 는 순차 치환이다).
                self.version = Some(tf(
                    "서버 {version} · 가동 {min}분 · pid {pid}",
                    &[
                        ("min", ((uptime / 60.0) as i64).to_string().as_str()),
                        ("pid", pid.to_string().as_str()),
                        ("version", version.as_str()),
                    ],
                ));
                true
            }
            ServerMessage::Notice { text, sev, i18n } => {
                let severity = Severity::parse(sev.as_deref());
                // 재료가 왔으면 **우리 로케일로** 다시 짓는다(로케일 ⓑ). 안 왔으면
                // 서버가 지은 글을 원문 키로 번역해 본다 — 자리가 없는 알림은 그것만으로
                // 영어가 된다(로케일 ⓐ).
                let text = i18n_say(&i18n, "text", &text);
                // 오류 등급만 상태줄 한 줄에도 건다 — 나머지는 이력에만 쌓는다(모든
                // 알림이 상태줄을 차지하면 정작 오류가 묻힌다).
                if severity == Severity::Error {
                    self.last_error = Some(text.clone());
                }
                self.push_notice(Notice::from(severity, text, Source::Server));
                true
            }
            // 선택 회신은 **화면이 아니라 클립보드로** 간다. 프로세스를 띄우는 일이라
            // 상태 누적기가 할 수 없고, 이벤트 루프가 `apply` 앞에서 가로챈다. 여기까지
            // 왔다면 그 가로채기가 빠진 것이다 — 조용히 사라지지 않게 팔을 적어 둔다.
            ServerMessage::Selection { .. } => false,
            // 트랜스크립트 꼬리도 화면 상태가 아니다 — 파싱해서 Claude 항목으로
            // 만드는 것은 이벤트 루프의 일이고(뷰는 그리기만 한다) 여기까지 오면
            // 그 가로채기가 빠진 것이다.
            ServerMessage::Claude { .. } => false,
            // tree/ok/pong/unknown 은 화면을 바꾸지 않는다.
            _ => false,
        }
    }

    /// 바뀐 행만 온 갱신을 캐시에 얹는다. 화면이 달라졌으면 `true`.
    ///
    /// # 기준이 없으면 얹지 않고 **다시 그려 달라고 청한다**
    ///
    /// 바탕이 없는 채로 바뀐 행만 들고 있으면 나머지 행을 영영 모른다 — 화면 절반이
    /// 빈 채로 굳는 것이 조용히 아무것도 안 하는 것보다 나쁘다. 파이썬 클라와 같은
    /// 처방이고(`_delta_no_base`), 요청은 패널마다 한 번으로 눌러 둔다.
    fn apply_delta(&mut self, delta: crate::message::ScreenDelta) -> bool {
        let Some(screen) = self.screens.get_mut(&delta.pane) else {
            if self.delta_no_base.insert(delta.pane) {
                self.want_redraw = true;
            }
            return false;
        };
        for (y, row) in delta.rows {
            if y < screen.rows.len() {
                screen.rows[y] = row;
            } else if y == screen.rows.len() {
                // 서버가 행 수를 늘린 그 한 줄. 그 너머는 **버린다** — 사이가 빈 채로
                // 늘리면 없는 줄이 빈 줄로 그려져 화면이 조용히 어긋난다.
                screen.rows.push(row);
            }
        }
        // 델타가 아니라 매번 전체가 오는 것들(파이썬 클라와 같다).
        screen.cursor = delta.cursor;
        screen.wrap = delta.wrap;
        screen.top = delta.top;
        screen.scroll = delta.scroll;
        true
    }

    /// 상태줄 표식(줌·동기화·감시·자동 이름 …).
    pub fn flags(&self) -> &StatusFlags {
        &self.flags
    }

    /// 서버가 광고한 플러그인 목록(패리티 G7). 첫 full status 전에는 비어 있다.
    pub fn plugins(&self) -> &[PluginInfo] {
        &self.plugins
    }

    /// 플러그인이 기여한 명령·메뉴·설정([`PluginSurface`]).
    pub fn plugin_surface(&self) -> &PluginSurface {
        &self.plugin_surface
    }

    /// 지금 그릴 플러그인 화면(없으면 `None`).
    pub fn plugin_screen(&self) -> Option<&PluginScreen> {
        self.plugin_screens.last()
    }

    /// 한 판 물러난다 — 상세에서 `Esc` 를 누르면 **방금 보던 목록**으로 돌아간다.
    /// 돌려주는 값은 "아직 화면이 남았나"다(false 면 화면을 통째로 닫는다).
    pub fn pop_plugin_screen(&mut self) -> bool {
        self.plugin_screens.pop();
        !self.plugin_screens.is_empty()
    }

    /// 화면을 통째로 접는다(다른 화면으로 갈아탈 때).
    pub fn clear_plugin_screens(&mut self) {
        self.plugin_screens.clear();
    }

    /// 활성 패널의 오버레이를 켜고 끈다(패리티 G7b/G7c · `prefix t` · 달력).
    ///
    /// 패널이 없으면 `None` — 붙기 전에 눌린 키다. 한 패널엔 **오버레이 하나**라
    /// (정본 규칙) 켜면 그 패널의 다른 것을 닫고 그 사실을 `closed` 로 돌려준다 —
    /// 서버에도 그 끔을 알려야 두 그림이 겹쳐 그려지지 않는다.
    pub fn toggle_overlay(&mut self, name: &str) -> Option<OverlayToggle> {
        let active = self.layout.as_ref()?.active;
        let on_now = self.overlays.get(name).is_some_and(|on| on.contains(&active));
        self.set_overlay(name, !on_now)
    }

    /// 오버레이를 **명시적으로** 켜거나 끈다(`open-clock`·`close-calendar` 등).
    ///
    /// # 왜 토글과 따로 있나 (§10-21ⓡ)
    ///
    /// 제보: `close-clock`·`close-calendar` 가 안 먹는다. 뿌리는 우리에게 **토글밖에
    /// 없어서** 그 이름들이 팔레트에서 서버로 넘어가(`plugin_open`) "화면 스펙이 없다"로
    /// 거절당한 것이다 — 화면을 여는 명령이 아니라 **상태를 바꾸는 명령**이라 그 경로가
    /// 통째로 틀렸다.
    ///
    /// 정본의 계약 셋을 그대로 가져온다(`plugins/calendar/__init__.py` · 회귀
    /// `tests/test_client.py`): **켜기는 멱등** · 끄기는 끔 · 대상은 **활성 패널** ·
    /// 시계와 달력은 **상호 배타**(하나를 켜면 다른 하나는 닫힌다).
    ///
    /// 돌려주는 `closed` 는 "이 켜기 때문에 닫힌 다른 오버레이"다 — 뷰가 서버에도 그
    /// 사실을 알려야 서버가 그리던 셀을 지운다.
    pub fn set_overlay(&mut self, name: &str, on: bool) -> Option<OverlayToggle> {
        let active = self.layout.as_ref()?.active;
        if !on {
            // 멱등: 안 켜져 있어도 "껐다"로 답한다. 여기서 `None` 을 돌려주면 서버에
            // 알림이 안 가고, 서버가 그리던 셀이 남을 수 있다(끄기는 반복해도 안전하다).
            if let Some(set) = self.overlays.get_mut(name) {
                set.remove(&active);
            }
            return Some(OverlayToggle { pane: active, on: false, closed: None });
        }
        let closed: Vec<String> = self
            .overlays
            .iter_mut()
            .filter(|(other, on)| other.as_str() != name && on.contains(&active))
            .map(|(other, on)| {
                on.remove(&active);
                other.clone()
            })
            .collect();
        self.overlays.entry(name.to_owned()).or_default().insert(active);
        Some(OverlayToggle { pane: active, on: true, closed: closed.into_iter().next() })
    }

    /// 입력기 배지 글(뷰가 넣는다). 상태를 모르는 판에서는 `None` — 모르는 것을
    /// "영문"이라고 단정하지 않는다.

    /// 비활성 패널 딤을 켜고 끈다(설정에서 읽은 값을 뷰가 넣는다).
    pub fn set_inactive_dim(&mut self, on: bool, ratio: f32) {
        self.inactive_dim = on.then_some(ratio.clamp(0.0, 0.8));
    }

    /// 터치 스크롤바를 쓰나(설정 `touch-scroll`). 뷰가 설정에서 읽어 넣는다.
    pub fn set_touch_scroll(&mut self, on: bool) {
        self.touch_scroll = on;
    }

    pub fn touch_scroll(&self) -> bool {
        self.touch_scroll
    }

    /// 지금 스크롤 모드인가를 알려 준다(뷰가 모드를 바꿀 때마다 넣는다).
    pub fn set_scroll_mode(&mut self, on: bool) {
        self.scroll_mode = on;
    }

    /// 터치 스크롤바가 차지한 자리 — **없으면 그리지도 안 눌리지도 않는다**.
    ///
    /// 조건은 정본(`clientio._composite`)과 같다:
    /// - 스크롤 모드일 것(라이브 화면의 마지막 열을 덮으므로 평소에는 안 그린다)
    /// - 설정 `touch-scroll` 이 켜져 있을 것
    /// - **팝업이 안 떠 있을 것** — 팝업은 스크롤바를 덮어 그린다. 존만 남기면
    ///   사용자가 보는 것(팝업)과 탭이 하는 일(뒤 패널 스크롤)이 어긋난다
    /// - 활성 패널이 2칸 이상 넓고, 바를 그릴 만큼(3행) 높을 것
    ///
    /// 그리는 쪽과 누르는 쪽이 **같은 함수**를 봐야 좌표가 안 어긋난다 — 정본은 그리기
    /// 부수효과로 존을 남기는데, 우리는 순수 함수로 두고 양쪽이 부른다.
    pub fn touch_scroll_zone(&self) -> Option<TouchScrollZone> {
        if !self.scroll_mode || !self.touch_scroll {
            return None;
        }
        let layout = self.layout.as_ref()?;
        if layout.popup.is_some() {
            return None;
        }
        let pane = layout.panes.iter().find(|p| p.id == layout.active)?;
        if pane.w < 2 || (pane.h as usize) < base::scrollbar::MIN_H {
            return None;
        }
        Some(TouchScrollZone {
            pane: pane.id,
            x: (pane.x + pane.w - 1) as usize,
            y: pane.y as usize,
            h: pane.h as usize,
        })
    }

    /// 스크롤바 안 **상대 행** `iy` 를 눌렀을 때 보낼 프레임. 누를 것이 없으면 `None`.
    ///
    /// `▲`/`▼` 는 반 화면(PgUp/PgDn 과 같은 양), 트랙은 그 자리로 점프한다. 점프는 절대
    /// 위치가 아니라 **지금과의 차**를 보낸다 — 그래서 `scr` 을 안 주는 구서버에서도
    /// 프로토콜 추가 없이 동작한다.
    pub fn touch_scroll_tap(&self, iy: usize) -> Option<crate::command::Scroll> {
        let zone = self.touch_scroll_zone()?;
        let scroll = |delta: i32| {
            (delta != 0).then(|| crate::command::Scroll::by(delta).for_pane(Some(zone.pane)))
        };
        match base::scrollbar::hit(zone.h, iy)? {
            base::scrollbar::Hit::Up => scroll(base::scrollbar::half_page(zone.h)),
            base::scrollbar::Hit::Down => scroll(-base::scrollbar::half_page(zone.h)),
            base::scrollbar::Hit::Jump(frac) => scroll(base::scrollbar::jump_delta(
                self.pane_top(zone.pane).unwrap_or(0),
                self.pane_scroll(zone.pane).unwrap_or(0),
                frac,
            )),
        }
    }

    /// 패널 번호를 띄우거나 지운다(`prefix q`). 켜졌으면 `true`.
    pub fn toggle_pane_numbers(&mut self) -> bool {
        self.pane_numbers = !self.pane_numbers;
        self.pane_numbers
    }

    /// 지금 번호가 떠 있나.
    pub fn pane_numbers(&self) -> bool {
        self.pane_numbers
    }

    /// 번호를 지운다. 지웠으면 `true` — 뷰가 "이 키로 사라졌다"를 알 수 있다.
    pub fn clear_pane_numbers(&mut self) -> bool {
        let was = self.pane_numbers;
        self.pane_numbers = false;
        was
    }

    /// 번호 `n` 번 패널의 id. 번호는 레이아웃 순서(파이썬과 같은 0부터)다.
    pub fn pane_by_number(&self, n: usize) -> Option<i64> {
        self.layout.as_ref()?.panes.get(n).map(|p| p.id)
    }

    /// 화면 좌표 클릭이 오버레이의 **누를 수 있는 자리**에 맞았나 —
    /// 맞았으면 `(오버레이 이름, 패널, 되돌려 줄 이름)`.
    ///
    /// 이름의 뜻은 **모른다**. 달력의 `‹` 가 지난달인지 지난해인지는 플러그인이 정하고
    /// (설계 §4.4) 우리는 그 자리를 눌렀다는 사실만 올린다 — 그래서 새 오버레이가
    /// 생겨도 여기는 안 바뀐다.
    /// 화면 좌표 클릭이 **화면을 여는 자리**에 맞았나 — 맞았으면 `(화면 이름, 패널)`.
    ///
    /// [`overlay_zone_at`](Self::overlay_zone_at) 과 같은 자리 목록을 보되 `opens` 가
    /// 실린 것만 고른다. 부르는 쪽은 이것을 **먼저** 물어야 한다: 같은 자리를
    /// `plugin_overlay_action` 으로 보내면 서버가 그 이름을 아무도 안 집어 조용히
    /// 사라진다(눌렀는데 아무 일도 안 나는, 이 저장소의 상습 결함).
    pub fn open_zone_at(&self, x: u16, y: u16) -> Option<(String, i64)> {
        let z = self.zone_at(x, y)?;
        (!z.opens.is_empty()).then(|| (z.opens.clone(), z.pane))
    }

    /// 화면 좌표 클릭이 **그 패널에 치는 자리**에 맞았나 — 맞았으면 `(패널, 칠 바이트)`.
    ///
    /// 세 번째 갈래다([`PluginZone::send`]). Claude busy footer 의 `esc to interrupt`
    /// 가 이 길로 오고, 우리는 그 바이트를 그 패널에 넣는다 — **활성 패널을 안 바꾼다**
    /// (비활성 Claude 패널의 footer 를 눌러 놓고 지금 보는 패널을 멈추면 안 된다.
    /// 정본이 `send_input_pane` 을 쓰는 이유가 그것이다).
    pub fn send_zone_at(&self, x: u16, y: u16) -> Option<(i64, Vec<u8>)> {
        let z = self.zone_at(x, y)?;
        (!z.send.is_empty()).then(|| (z.pane, z.send.clone().into_bytes()))
    }

    /// `(x,y)` 를 덮는 자리 하나 — 팝업 뒤는 안 본다.
    fn zone_at(&self, x: u16, y: u16) -> Option<&PluginZone> {
        // 팝업이 떠 있으면 뒤에 가려진 화살표는 클릭 대상이 아니다(68295 빚 —
        // `pane_at` 의 팝업 우선 규칙과 같은 이유).
        if self.popup().is_some() {
            return None;
        }
        let (x, y) = (x as i64, y as i64);
        self.plugin_cells
            .zones
            .iter()
            .find(|z| x >= z.x && x < z.x + z.w.max(1) && y >= z.y && y < z.y + z.h.max(1))
    }

    pub fn overlay_zone_at(&self, x: u16, y: u16) -> Option<(String, i64, String)> {
        let zone = self.zone_at(x, y)?;
        // 화면을 여는 자리도, 패널에 치는 자리도 이 길이 아니다 — `plugin_overlay_action`
        // 으로 보내면 서버가 **그 오버레이를 켠 적이 있나**를 먼저 보고(`servercmd` 의
        // `state is None → return`), Claude footer 처럼 오버레이가 아닌 자리는 그 상태가
        // 영영 없어 **조용히 사라진다**(눌렀는데 아무 일도 안 나는, 이 저장소의 상습
        // 결함). 그런 자리는 `open_zone_at`·`send_zone_at` 이 가져간다.
        if !zone.opens.is_empty() || !zone.send.is_empty() {
            return None;
        }
        Some((zone.name.clone(), zone.pane, zone.act.clone()))
    }

    /// 활성 패널의 오버레이가 이 키를 가져가나 — 가져가면
    /// `(오버레이 이름, 패널, 되돌려 줄 이름)`.
    ///
    /// **활성 패널에 떠 있을 때만** 가로챈다: 그 패널은 이미 오버레이에 덮여 있으니
    /// 셸 입력을 가리지 않는다(정본 `client_overlay_key` 와 같은 규칙).
    pub fn overlay_key(&self, name: &str) -> Option<(String, i64, String)> {
        let active = self.layout.as_ref()?.active;
        let key = self
            .plugin_cells
            .keys
            .iter()
            .find(|k| k.pane == active && k.key == name)?;
        Some((key.name.clone(), active, key.act.clone()))
    }

    /// 마지막으로 받은 트리(`request_tree` 회신). 요청 전이면 `None`.
    pub fn tree(&self) -> Option<&Tree> {
        self.tree.as_ref()
    }

    /// 마지막으로 받은 페이스트 버퍼 목록.
    pub fn buffers(&self) -> &[BufferItem] {
        &self.buffers
    }

    /// 트리를 **한 줄짜리 목록**으로 편다(패리티 G3b).
    ///
    /// # 왜 proto 인가
    ///
    /// 두 뷰가 각자 펴면 같은 트리가 화면마다 다르게 보인다(들여쓰기·라벨·순서). 그리고
    /// 목록 화면은 **줄 번호로 고르므로**, 펴는 순서가 갈리면 GUI 에서 고른 것과 TUI 에서
    /// 고른 것이 달라진다 — 조용한 어긋남이다.
    ///
    /// 세션 줄은 **접어 넣지 않는다**: 세션이 하나뿐인 것이 보통이고, 그때 세션 줄은
    /// 고를 수도 없는 줄 하나를 목록 맨 위에 얹을 뿐이다(파이썬 클라도 탭부터 보인다).
    pub fn tree_rows(&self) -> Vec<TreeRow> {
        let Some(tree) = &self.tree else {
            return Vec::new();
        };
        let many = tree.sessions.len() > 1;
        let mut rows = Vec::new();
        for session in &tree.sessions {
            if many {
                rows.push(TreeRow {
                    depth: 0,
                    label: session.name.clone(),
                    window: None,
                    pane: None,
                    active: false,
                });
            }
            for window in &session.windows {
                let pin = if window.pinned { "*" } else { "" };
                rows.push(TreeRow {
                    depth: usize::from(many),
                    label: format!("{}{pin} {}", window.index, window.name),
                    window: Some(window.index),
                    pane: None,
                    active: window.active,
                });
                // 패널이 하나뿐이면 그 줄은 탭 줄과 같은 말을 한다 — 안 싣는다.
                if window.panes.len() < 2 {
                    continue;
                }
                for pane in &window.panes {
                    let what = if pane.cmd.is_empty() { "?" } else { &pane.cmd };
                    let where_ = if pane.remote { "ssh" } else { "local" };
                    let title = if pane.title.is_empty() {
                        String::new()
                    } else {
                        format!(" · {}", pane.title)
                    };
                    rows.push(TreeRow {
                        depth: usize::from(many) + 1,
                        label: format!("[{where_}] {what}{title}"),
                        window: Some(window.index),
                        pane: Some(pane.id),
                        active: false,
                    });
                }
            }
        }
        rows
    }

    /// 탭 스위처의 줄들 — 탭바의 **시각 순서** 그대로 + 로컬 탭의 패널 하위행.
    ///
    /// 파이썬 `open_tab_switcher`(07-16 확장)와 같은 규칙: 팝업은 즉시 열리고(탭
    /// 목록은 `status` 로 이미 안다), 패널 하위행은 서버 `tree` 회신이 오면 **뒤늦게**
    /// 끼어든다 — **패널 2개 이상인 로컬 탭** 밑에만(하나뿐이면 탭 줄과 같은 말이고,
    /// 원격 탭의 패널 구성은 업스트림 소유라 여기서 안 편다). 패널 줄에서 고르면
    /// 그 탭 + 그 패널로 간다(뷰의 `picked` 가 트리와 같은 팔로 푼다).
    pub fn switcher_rows(&self) -> Vec<TreeRow> {
        let mut rows = Vec::new();
        let labels = self.tabs().labels();
        for (i, tab) in self.tabs().tabs.iter().enumerate() {
            rows.push(TreeRow {
                depth: 0,
                label: labels[i].clone(),
                window: Some(tab.index),
                pane: None,
                active: tab.active,
            });
            if tab.remote {
                continue;
            }
            let Some(tree) = &self.tree else { continue };
            for session in &tree.sessions {
                for window in &session.windows {
                    if window.index != tab.index || window.panes.len() < 2 {
                        continue;
                    }
                    for pane in &window.panes {
                        let what = if pane.cmd.is_empty() { "?" } else { &pane.cmd };
                        let where_ = if pane.remote { "ssh" } else { "local" };
                        let title = if pane.title.is_empty() {
                            String::new()
                        } else {
                            format!(" · {}", pane.title)
                        };
                        rows.push(TreeRow {
                            depth: 1,
                            label: format!("[{where_}] {what}{title}"),
                            window: Some(window.index),
                            pane: Some(pane.id),
                            active: false,
                        });
                    }
                }
            }
        }
        rows
    }

    /// "다시 그려 달라"를 보내야 하는가(가져가면 표식을 내린다).
    ///
    /// 뷰가 이 값을 보고 [`Command::RequestRedraw`](crate::command::Command::RequestRedraw)
    /// 를 보낸다 — 상태 누적기는 소켓을 모른다.
    pub fn take_redraw_request(&mut self) -> bool {
        std::mem::take(&mut self.want_redraw)
    }

    pub fn layout(&self) -> Option<&Layout> {
        self.layout.as_ref()
    }

    pub fn tabs(&self) -> &TabBar {
        &self.tabs
    }

    pub fn is_closed(&self) -> bool {
        self.closed
    }

    pub fn last_error(&self) -> Option<&str> {
        self.last_error.as_deref()
    }

    /// 상태줄 한 줄에서 **표시만** 걷어낸다(§10-21ⓦ).
    ///
    /// # 이력은 남는다
    ///
    /// 지나간 오류는 알림 화면이 갖는다(`note_error_history`). 여기서 지우는 것은 "지금
    /// 보이는 한 줄"뿐이다 — 닫기가 이력까지 지우면 그 줄을 눌러 이력으로 가는 동선
    /// (ⓦ⑶)이 무의미해진다.
    pub fn clear_error(&mut self) {
        self.last_error = None;
    }

    /// 지금 보는 탭이 원격이면 그 **호스트**. 아니면 `None`.
    ///
    /// 판정 기준은 파이썬 `_active_remote_host` 와 같다 — 병합 탭바의 이름
    /// (`⇄host:name`)에서 호스트를 읽는다.
    pub fn active_remote_host(&self) -> Option<&str> {
        self.tabs
            .tabs
            .iter()
            .find(|t| t.active && t.remote)
            .and_then(|t| t.display().host)
    }

    /// `merge-remote-tab` 후보 — **같은 호스트의, 지금 탭이 아닌 원격 탭**들.
    ///
    /// 서버에 따로 물을 것이 없다: 페더레이션의 요점이 원격 탭을 **이 탭바에** 끼워
    /// 넣는 것이라, 목록은 이미 우리 손에 있다.
    ///
    /// 돌려주는 것은 `(전역 index, 화면에 적을 줄)`. index 는 서버 `join_pane` 이
    /// 원격 로컬 index 로 바꿔 업스트림에 릴레이하는 값이다(파이썬과 같다).
    pub fn merge_candidates(&self) -> Vec<(usize, String)> {
        let Some(host) = self.active_remote_host() else {
            return Vec::new();
        };
        self.tabs
            .tabs
            .iter()
            .filter(|t| t.remote && !t.active && t.display().host == Some(host))
            .map(|t| (t.index, format!("{}: {}", t.index + 1, t.name)))
            .collect()
    }

    /// `run-shell` 의 마지막 출력.
    pub fn shell_output(&self) -> &[String] {
        &self.shell_output
    }

    /// 셸 출력을 담는다. 40줄까지만(파이썬 `_run_shell` 과 같은 상한).
    pub fn set_shell_output(&mut self, text: &str) {
        self.shell_output = text.lines().take(40).map(str::to_owned).collect();
    }

    /// 재시작 점검 결과 줄들(`restart-check` 회신). 묻기 전에는 비어 있다.
    pub fn restart_check(&self) -> &[String] {
        &self.restart_check
    }

    /// 떠 있는 팝업(`display-popup`). 없으면 `None`.
    ///
    /// 입력을 여기 `id` 로 보내야 그 PTY 에 닿는다 — 팝업은 트리 밖이라 **활성 패널이
    /// 될 수 없다**(서버가 활성 패널로 흘리는 평소 경로로는 영영 안 간다).
    pub fn popup(&self) -> Option<&crate::message::PopupLayout> {
        self.layout.as_ref()?.popup.as_ref()
    }

    /// 활성 패널 **위쪽**(같은 열 범위)에 다른 패널이 있나.
    ///
    /// 크롬 포커스(`e_up`)가 이걸 본다 — 위에 패널이 더 있으면 `↑` 는 여전히 패널 이동이고,
    /// 없을 때만 포커스가 탭바로 나간다. 파이썬 `_pane_above` 와 같은 판정이다.
    pub fn pane_above(&self) -> bool {
        self.pane_beyond(true)
    }

    /// 아래쪽에 다른 패널이 있나(`e_down` 이 본다).
    pub fn pane_below(&self) -> bool {
        self.pane_beyond(false)
    }

    /// `↑`/`↓` 방향에 **가로로 겹치는** 다른 패널이 있는지.
    ///
    /// 겹침을 따지는 이유: 왼쪽 아래에 패널이 있어도 그건 `↓` 로 갈 수 있는 패널이 아니다.
    /// 겹침을 안 보면 옆 열의 패널 때문에 가장자리 판정이 틀린다.
    fn pane_beyond(&self, above: bool) -> bool {
        let Some(layout) = self.layout.as_ref() else {
            return false;
        };
        let Some(active) = layout.panes.iter().find(|p| p.active) else {
            return false;
        };
        layout.panes.iter().any(|p| {
            if p.id == active.id {
                return false;
            }
            let beyond = if above {
                p.y + p.h <= active.y
            } else {
                p.y >= active.y + active.h
            };
            let overlaps =
                !(p.x + p.w <= active.x || p.x >= active.x + active.w);
            beyond && overlaps
        })
    }

    /// 상태줄 형식 문자열이 쓸 값들([`crate::status::expand`]).
    ///
    /// **두 뷰가 각자 모으면 갈린다** — 한쪽에서만 `#I` 가 하나 어긋나는 식으로. 그래서
    /// 여기 한 곳이 만든다.
    pub fn status_ctx(&self) -> crate::status::StatusCtx {
        let active = self.tabs.tabs.iter().position(|t| t.active);
        crate::status::StatusCtx {
            session: self.session.clone(),
            // ★ **보이는 번호**(1부터)다. 내부 자리를 적으면 탭바의 번호와 하나씩
            // 어긋나고, 그건 눈으로 잡기 가장 어려운 부류다.
            tab_number: active.map(|i| i + 1),
            tab_name: active
                .and_then(|i| self.tabs.tabs.get(i))
                .map(|t| t.name.clone())
                .unwrap_or_default(),
            pane_title: self.flags.pane_title.clone(),
        }
    }

    /// 지금 하단에 떠 있는 배지들 — 크롬 포커스(`e_down`)가 순환하는 목록.
    ///
    /// **두 뷰가 각자 세면 갈린다**(한쪽에만 `원격` 이 뜨는 식으로). 그래서 목록은 여기
    /// 한 곳이 만든다. 있을 때만 싣는 `알림` 은 파이썬 `_status_buttons` 가 존재하는
    /// zone 만 세는 것과 같은 규칙이다.
    pub fn badges(&self) -> Vec<crate::Badge> {
        use crate::Badge;
        let mut out = Vec::new();
        if self.notices.len() > 0 {
            out.push(Badge::Notices);
        }
        // ★ **`서버`·`시계`·`달력` 은 여기 없다**(제보 §10-21ⓑ, 2026-08-02).
        //
        // 제보: *"왼쪽 하단의 `Server` `Clock` `Calendar` 표시가 없어야 한다 — 오른쪽
        // 하단의 `ALIENWARE 20:09 2026-08-02` 와 **완전히 같은 역할**이라 중복이다."*
        //
        // 그때 열려 있던 물음은 "지우면 그 마우스 동선이 사라지는데 어디에 두나"였고,
        // **ⓑ2 가 그 답이었다**: 오른쪽 글자가 이미 그 동작을 갖고 있다 — `#h` 구간은
        // `Badge::Host`, `%H:%M` 은 `Badge::Clock`, `%Y-%m-%d` 는 `Badge::Calendar` 로
        // 이어진다(`crate::status::run_badge`). 2026-08-02 라이브에서 셋 다 실제로
        // 동작하는 것을 확인하고 지웠다 — **먼저 재고 나서 지운 것**이지 옮길 곳을
        // 믿고 지운 것이 아니다.
        //
        // ⚠ `status_right` 에서 그 토큰들을 지운 사람에게는 **마우스 입구가 사라진다**.
        // 키·팔레트는 남는다(`prefix t` · `clock-mode` · `calendar-mode` · `status`) —
        // 정본도 상태줄 형식을 비우면 그 버튼들이 같이 사라지므로 결이 같다.
        // 터치 스크롤 `⇕` 는 **설정이 켜져 있을 때만**이다(정본과 같다 — 저쪽은
        // `touch_scroll and mouse_enabled`). 마우스를 끈 판에서는 누를 수가 없으므로
        // 자리만 차지한다.
        if self.touch_scroll {
            out.push(Badge::TouchScroll);
        }
        out
    }

    /// 창(또는 단말) 제목 — 설정 `set-titles-string` 을 지금 상태로 펼친 것.
    ///
    /// 토큰은 상태줄과 **같은 것**을 쓴다(`status::expand`) — 두 자리가 다른 문법을 쓰면
    /// 사용자가 한쪽에서 배운 것을 다른 쪽에서 못 쓴다. 그래서 여기서 만들고 뷰는
    /// 흘리기만 한다(TUI 는 OSC 2, GUI 는 창 제목).
    pub fn window_title(&self, fmt: &str) -> String {
        crate::status::expand(fmt, &self.status_ctx())
    }

    /// 마지막 드라이런 회신을 **판정에 쓰는 값들**로 옮긴다(`base::restart::Probe`).
    ///
    /// 와이어 칸 이름을 아는 것은 여기다 — `core` 는 서버를 모른다. 회신이 아직 없으면
    /// 전부 기본값(`false`/`0`)이라 판정이 **막힌다**(그게 안전한 쪽이다).
    pub fn restart_probe(&self) -> base::restart::Probe {
        let f = &self.restart_check_fields;
        let flag = |key: &str| {
            f.get(key)
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false)
        };
        let count = |key: &str| f.get(key).and_then(serde_json::Value::as_i64).unwrap_or(0);
        base::restart::Probe {
            reexec: flag("reexec_supported"),
            sessions: flag("has_sessions"),
            serialize: flag("serialize_ok"),
            panes: count("panes"),
            panes_with_fd: count("panes_with_fd"),
        }
    }

    /// 드라이런 회신이 이미 왔나(게이트가 회신을 기다릴지 정한다).
    pub fn has_restart_check(&self) -> bool {
        !self.restart_check_fields.is_empty()
    }

    /// 다음 드라이런을 위해 지난 회신을 버린다.
    ///
    /// 안 버리면 **지난 회신으로 판정한다** — 그 사이 패널이 닫혔으면 그 값은 거짓이고,
    /// 되돌릴 수 없는 동작을 그 거짓으로 통과시키게 된다.
    pub fn clear_restart_check(&mut self) {
        self.restart_check_fields.clear();
        self.restart_check.clear();
    }

    /// 이 패널이 Claude 패널인가(서버 `status` 의 `panes_claude`).
    pub fn is_claude_pane(&self, pane: i64) -> bool {
        self.claude_panes.contains(&pane)
    }

    /// 플러그인이 낸 상태줄 표식들 — 뷰가 순서대로 칩으로 그린다(Tier B ③ · P6).
    ///
    /// 자리는 우리가 정한다. 정본과 우리의 배지 줄 생김새가 서로 다르니 서버가 자리를
    /// 정하면 한쪽이 망가진다 — 서버가 주는 것은 **무엇을 어떤 뜻의 색으로**까지다.
    ///
    /// ⚠ **`claude_badge()` 는 지웠다(M4 P6 후반).** 상태줄의 Claude 표식은 이제
    /// 플러그인이 자료로 준다(`plugin_badges` → [`Self::plugin_badges`]) — 우리가 날
    /// 필드로 문자열을 조립하면 정본과 **두 벌**이 되고, 실제로 갈려 있었다(정본은
    /// 카운트다운·경고까지 그렸다). 규칙은 `plugins/claude-code/statusbadges.py` 한 벌이다.
    pub fn plugin_badges(&self) -> &[PluginBadge] {
        &self.plugin_badges
    }


    /// 이 패널의 **라이브 입력칸에 지금 들어 있는 글**(패리티 G9c).
    ///
    /// 작성창(`esc Insert`)이 이 값으로 시작하고, 투입할 때 **그 길이만큼 백스페이스로
    /// 비운 뒤** 새 글을 넣는다. 시드와 비우기가 같은 값을 써야 중복도 잔여도 없다.
    ///
    /// 세 가지 반환값의 뜻이 다르다(`prompt_box` 모듈 문서): `None` = 긁을 수 없다(호출부는
    /// 초안으로) · `Some("")` = 입력칸이 실제로 빔 · `Some(글)`.
    ///
    /// **Claude 패널일 때만 긁는다.** 셸 패널에서 긁으면 프롬프트(`~/dir ❯ `)가 입력
    /// 텍스트로 잡힌다 — 그러면 작성창이 그 글로 시작하고, 투입할 때 그 길이만큼
    /// 백스페이스가 셸로 간다.
    pub fn prompt_text(&self, pane: i64) -> Option<String> {
        if !self.is_claude_pane(pane) {
            return None;
        }
        let screen = self.screens.get(&pane)?;
        // 런 텍스트를 그대로 이어 붙인다 — 파이썬 `client_prompt_text` 와 같다(폭에 맞춰
        // 채우지 않는다. 채우면 줄 끝 공백이 생겨 `rstrip` 뒤 결과는 같지만, 정본과 다른
        // 경로를 타는 것이라 픽스처 대조의 뜻이 옅어진다).
        let lines: Vec<String> = screen
            .rows
            .iter()
            .map(|row| row.iter().map(|run| run.text.as_str()).collect())
            .collect();
        crate::prompt_box::input_text(&lines, &screen.wrap, screen.cursor.map(|(_, y)| y as usize))
    }

    /// 서버가 알려 준 버전 한 줄(`version` 명령의 회신). 묻기 전에는 `None`.
    /// RTT 이력(읽기) — 정보 팝업 서버 탭이 그래프를 그린다.
    pub fn rtt(&self) -> &crate::rtt::RttHist {
        &self.rtt
    }

    /// RTT 이력(쓰기) — pong 이 올 때 뷰가 표본을 넣는다.
    pub fn rtt_mut(&mut self) -> &mut crate::rtt::RttHist {
        &mut self.rtt
    }

    pub fn version(&self) -> Option<&str> {
        self.version.as_deref()
    }

    /// 지나간 알림들. **새것이 앞**이다.
    pub fn notices(&self) -> impl ExactSizeIterator<Item = &Notice> {
        self.notices.iter()
    }

    fn push_notice(&mut self, notice: Notice) {
        self.notices.push_front(notice);
        self.notices.truncate(NOTICE_LIMIT);
    }

    /// **클라 쪽**에서 난 오류를 서버 오류와 같은 자리에 건다(설정 저장 실패 등).
    ///
    /// 자리를 나누지 않는 이유: 사용자에게는 "방금 한 것이 안 됐다" 한 가지이고, 자리가
    /// 둘이면 한쪽은 아무도 안 본다. 파이썬 클라도 `display_message(severity=error)` 로
    /// 같은 줄에 건다.
    /// **클라 쪽**에서 알리는 보통 소식(설정 다시 읽음·`display-message`).
    ///
    /// 오류와 나눠 두는 이유: 오류는 상태줄 한 줄을 차지하고 이건 이력에만 쌓인다
    /// (모든 알림이 상태줄을 차지하면 정작 오류가 묻힌다 — G6c 의 규칙).
    pub fn note_notice(&mut self, msg: impl Into<String>) {
        self.push_notice(Notice::new(Severity::Info, msg.into()));
    }

    pub fn note_error(&mut self, msg: impl Into<String>) {
        let msg = msg.into();
        self.push_notice(Notice::new(Severity::Error, msg.clone()));
        self.last_error = Some(msg);
    }

    /// 오류 등급으로 **이력에만** 남긴다 — 상태줄 한 줄(`last_error`)은 차지하지 않는다.
    ///
    /// 끊김이 그 경우다: 사유는 뷰가 이미 맨 아래 한 줄로 보이고 있으므로 같은 말을 두
    /// 번 걸 필요가 없고, `last_error` 까지 세우면 **다시 붙은 뒤에도** 그 줄이 "서버
    /// 오류"로 남는다(끊김 표시는 지워지지만 오류는 안 지워진다).
    ///
    /// 이력에는 남겨야 한다 — 그 한 줄은 다음 메시지가 오면 사라지고, 사용자가 그 줄을
    /// 눌러서 여는 곳이 바로 이력이다(`status::message_line` 문서).
    pub fn note_error_history(&mut self, msg: impl Into<String>) {
        self.push_notice(Notice::new(Severity::Error, msg.into()));
    }

    /// 배치에 있는 패널들. 서버가 준 순서 그대로다.
    pub fn panes(&self) -> &[PaneLayout] {
        self.layout.as_ref().map_or(&[], |l| &l.panes)
    }

    /// 분할 경계들(마우스 손잡이). 분할이 없으면 비어 있다.
    pub fn dividers(&self) -> &[crate::message::Divider] {
        self.layout.as_ref().map_or(&[], |l| &l.dividers)
    }

    /// 캔버스 좌표 (x, y) 에 있는 패널의 id.
    ///
    /// 테두리(`boxrect`)까지 그 패널로 친다 — 파이썬 클라의 `_pane_at` 과 같은 판정이다.
    /// 경계선은 두 패널이 한 셀을 나눠 쓰므로, 호출부가 **경계선을 먼저** 걸러야 한다
    /// (안 그러면 경계를 잡으려던 클릭이 이웃 패널 포커스로 샌다).
    ///
    /// **팝업이 떠 있으면 팝업이 먼저다**(68295 빚): 내용 안이면 팝업 패널, 밖이면
    /// `None` — 가려진 패널을 클릭이 조작하면 안 보이는 곳에서 상태가 바뀐다. 파이썬
    /// 클라는 아직 팝업 마우스가 없지만(같은 `_pane_at`), 그건 버그 동형의 비목표다
    /// (키보드는 저쪽도 팝업으로 라우팅한다 — 마우스만 새는 것이 이상한 쪽이다).
    pub fn pane_at(&self, x: u16, y: u16) -> Option<i64> {
        if let Some(popup) = self.popup() {
            let inside = x >= popup.cx
                && x < popup.cx + popup.cw
                && y >= popup.cy
                && y < popup.cy + popup.ch;
            return inside.then_some(popup.id);
        }
        self.panes()
            .iter()
            .find(|p| {
                let [bx, by, bw, bh] = p.boxrect.unwrap_or([p.x, p.y, p.w, p.h]);
                x >= bx && x < bx + bw && y >= by && y < by + bh
            })
            .map(|p| p.id)
    }

    /// 탭 드래그를 캔버스에 놓은 자리(파이썬 `_tabdrop_at` 동형 — G9v).
    ///
    /// 커서 아래 패널과, 그 패널의 어느 쪽인가(가운데 기준 가로 치우침 ≥ 세로면
    /// 좌우 분할). 팝업이 떠 있거나 1칸짜리 패널이면 `None` — 합칠 자리가 아니다.
    pub fn tab_drop_at(&self, x: u16, y: u16) -> Option<(i64, bool)> {
        if self.popup().is_some() {
            return None;
        }
        self.panes().iter().find_map(|p| {
            let [bx, by, bw, bh] = p.boxrect.unwrap_or([p.x, p.y, p.w, p.h]);
            let inside = x >= bx && x < bx + bw && y >= by && y < by + bh;
            if !inside || bw <= 1 || bh <= 1 {
                return None;
            }
            let dx = (f64::from(x - bx) / f64::from(bw) - 0.5).abs();
            let dy = (f64::from(y - by) / f64::from(bh) - 0.5).abs();
            Some((p.id, dx >= dy))
        })
    }

    /// 휠이 굴릴 패널 — **팝업이 먼저다**(모달: 팝업이 떠 있으면 커서가 어디 있든
    /// 팝업을 굴린다. 뒤 패널이 굴러가면 사용자는 그것을 볼 수도 없다).
    /// 팝업이 없으면 커서 아래 패널, 그것도 없으면 `None`(뷰가 활성 패널로 접는다).
    pub fn wheel_pane(&self, at: Option<(u16, u16)>) -> Option<i64> {
        if let Some(popup) = self.popup() {
            return Some(popup.id);
        }
        at.and_then(|(x, y)| self.pane_at(x, y))
    }

    /// 캔버스 좌표 (x, y) 를 잡은 분할 경계.
    ///
    /// 팝업이 떠 있으면 늘 `None` — 가려진 경계를 끌면 안 보이는 배치가 바뀐다.
    pub fn divider_at(&self, x: u16, y: u16) -> Option<&crate::message::Divider> {
        if self.popup().is_some() {
            return None;
        }
        self.dividers().iter().find(|d| d.contains(x, y))
    }

    /// 이 좌표의 마우스를 **받고 싶어 하는** 대상(안에서 도는 앱이 추적을 켰다).
    ///
    /// [`pane_at`](Self::pane_at) 과 두 가지가 다르다:
    ///
    /// - **테두리·제목줄은 안 친다.** 그 칸은 pytmux 의 것이다(경계선 드래그·패널 헤더).
    ///   앱에 넘기면 분할 조절을 못 하게 된다 — tmux 도 같은 경계다.
    /// - 추적을 안 켠 패널이면 `None` 이다. 안 켠 앱에 리포트를 보내면 그 바이트가
    ///   **프롬프트에 글자로 찍힌다**.
    ///
    /// 모드 판정(평소 모드에서만 넘긴다)은 여기서 안 한다 — 그건 뷰의 상태다.
    ///
    /// 팝업이 떠 있으면 **팝업이 먼저다**(다른 마우스 판정들과 같은 모달 규칙):
    /// 팝업 안 앱이 추적을 켰고 좌표가 내용 사각형 안이면 팝업으로 넘기고, 그 외에는
    /// `None` — 가려진 앱에 패스스루가 새면 안 된다. 팝업의 테두리·제목줄도 안 친다
    /// (내용 사각형 `cx..cw`/`cy..ch` 만 — 일반 패널의 경계와 같은 이유).
    pub fn mouse_pane_at(&self, x: u16, y: u16) -> Option<MouseTarget> {
        if let Some(popup) = self.popup() {
            let inside = x >= popup.cx
                && x < popup.cx + popup.cw
                && y >= popup.cy
                && y < popup.cy + popup.ch;
            return (inside && popup.mouse_mode().wants_mouse()).then(|| MouseTarget {
                id: popup.id,
                rect: (popup.cx, popup.cy, popup.cw, popup.ch),
                mode: popup.mouse_mode(),
            });
        }
        self.panes()
            .iter()
            .find(|p| {
                p.mouse_mode().wants_mouse()
                    && x >= p.x
                    && y >= p.y
                    && x < p.x + p.w
                    && y < p.y + p.h
            })
            .map(|p| MouseTarget {
                id: p.id,
                rect: (p.x, p.y, p.w, p.h),
                mode: p.mouse_mode(),
            })
    }

    /// 아직 화면을 못 받은 패널이 있는가. 첫 프레임이 다 왔는지 판단할 때 쓴다.
    pub fn is_complete(&self) -> bool {
        !self.panes().is_empty() && self.panes().iter().all(|p| self.screens.contains_key(&p.id))
    }

    /// 패널 하나를 합성한 텍스트 줄들. 폭은 배치가 알려 준 값을 쓴다.
    ///
    /// 화면이 아직 안 왔거나 배치에 없는 패널이면 `None`.
    pub fn compose_pane(&self, pane_id: i64) -> Option<Vec<String>> {
        let screen = self.screens.get(&pane_id)?;
        let pane = self.panes().iter().find(|p| p.id == pane_id)?;
        Some(compose_rows(&screen.rows, pane.w as usize))
    }

    /// 창 전체를 하나의 셀 격자로 합성한다.
    ///
    /// 패널을 각자의 좌표에 옮겨 놓으므로 **분할 배치가 그대로 나온다**. 배치를 아직
    /// 못 받았으면 `None`.
    pub fn composite(&self) -> Option<Canvas> {
        let layout = self.layout.as_ref()?;
        let mut canvas = Canvas::new(layout.cols as usize, layout.rows as usize);
        for pane in &layout.panes {
            if let Some(screen) = self.screens.get(&pane.id) {
                canvas.blit_pane(
                    &screen.rows,
                    pane.x as usize,
                    pane.y as usize,
                    pane.w as usize,
                    pane.h as usize,
                );
            }
        }
        // 비활성 패널을 한 톤 옅게 — **테두리를 그리기 전**이다(테두리는 활성/비활성이
        // 이미 색으로 갈려 있고, 딤까지 먹으면 두 번 어두워진다).
        if let Some(ratio) = self.inactive_dim {
            for pane in layout.panes.iter().filter(|p| p.id != layout.active) {
                for y in pane.y as usize..(pane.y + pane.h) as usize {
                    for x in pane.x as usize..(pane.x + pane.w) as usize {
                        if let Some(cell) = canvas.cell_mut(x, y) {
                            cell.style = crate::style::darken(&cell.style, ratio);
                        }
                    }
                }
            }
        }
        draw_frames(&mut canvas, layout);
        // ★ 입력기 배지는 **여기서 안 그린다**(2026-08-02i · P7). 손으로 옮긴 판이
        // 활성 패널 **첫 행**에 고정이라 정본(커서가 있는 줄)과 갈려 있었다 — 그 자리
        // 주석은 "정본과 같은 자리"라고 적고 있었지만 커서가 첫 줄일 때만 같았다.
        // 이제 그림은 플러그인이 `plugin_cells` 로 준다(우리는 한/영이라는 **사실**만
        // `client_fact` 로 올린다). 셀 런은 위 plugin_cells 경로가 얹는다.
        // 터치 스크롤바는 **입력기 배지 뒤**다(정본과 같은 순서). 둘이 같은 자리(활성
        // 패널 우측 끝)를 쓰는데, 가려도 되는 것은 배지 쪽이다 — 스크롤바에는 **클릭존이
        // 붙어 있어서**, 보이는 것과 탭이 하는 일이 어긋나면 안 된다.
        if let Some(zone) = self.touch_scroll_zone() {
            let bar = base::scrollbar::chars(
                zone.h,
                self.pane_top(zone.pane).unwrap_or(0),
                self.pane_scroll(zone.pane).unwrap_or(0),
            );
            let style = crate::style::CellStyle {
                fg: Some(crate::style::Color::Named(crate::style::NamedColor::BrightBlue)),
                bold: true,
                ..Default::default()
            };
            for (i, ch) in bar.into_iter().enumerate() {
                canvas.put(zone.x, zone.y + i, ch, style.clone());
            }
        }
        // ★ 플러그인 셀 기여(설계 Tier B · P3) — **테두리를 그린 뒤** 덮는다(먼저
        //   그리면 프레임이 글자 위에 얹힌다). 시계가 이 길로 온다: 우리는 어느 폰트를
        //   고르고 어디에 중앙 정렬하는지 **모른다**. 그건 플러그인 한 벌의 일이고
        //   우리가 아는 것은 "여기 이 글자를 이 스타일로"뿐이다.
        if !self.plugin_cells.dim.is_empty() || !self.plugin_cells.runs.is_empty() {
            // 딤이 먼저다 — 뒤를 흐리게 한 **다음** 글자를 얹어야 글자가 안 흐려진다.
            for pane in &layout.panes {
                if !self.plugin_cells.dim.contains(&(pane.id as u64)) {
                    continue;
                }
                let (cols, rows) = canvas.size();
                for y in (pane.y as usize)..((pane.y + pane.h) as usize).min(rows) {
                    for x in (pane.x as usize)..((pane.x + pane.w) as usize).min(cols) {
                        if let Some(cell) = canvas.cell_mut(x, y) {
                            cell.style = crate::clock::darken(&cell.style);
                        }
                    }
                }
            }
            for run in &self.plugin_cells.runs {
                let mut style = crate::style::CellStyle::from_map(&run.style);
                // 색의 권위는 **이 클라의 테마**다(설계 §10 위험표) — 서버가 hex 를
                // 실으면 서버가 UI 를 알게 된다. 런은 이름만 싣고 여기서 푼다.
                if let Some(name) = run.theme.f.as_deref() {
                    style.fg = theme::color(name);
                }
                if let Some(name) = run.theme.b.as_deref() {
                    style.bg = theme::color(name);
                }
                // ★ **와이드 문자는 두 칸이다.** 글자마다 한 칸씩 밀면 한글이 든 런
                //   (`[한]` 입력기 배지)에서 자리가 어긋난다 — `put` 이 뒷칸을 자리표로
                //   채우므로 우리가 그 폭만큼 건너뛰어야 겹치지 않는다. 정본도 같은
                //   자리에서 같은 실수를 하고 있었다(2026-08-02i 실측).
                //
                //   ⚠ **그 주석이 오래 거짓이었다**(pytmux-17): `put` 은 뒷칸을 안 채웠고,
                //   우리가 건너뛴 그 칸은 **배지 스타일이 아닌 채로** 남아 `row_runs` 가
                //   별도 런으로 뱉었다 → 화면에는 `[한 ]` 로 갈려 보였다. 이제 `put` 이
                //   `put_text` 와 같은 규칙으로 연속 셀을 채운다.
                let mut x = run.x as usize;
                // 서버가 지은 글이 아니라 **우리 로케일로 지은 글**을 찍는다(로케일 ⓑ).
                // 재료가 안 오면 `say()` 가 서버 글 그대로 돌려준다.
                let text = run.say();
                for ch in text.chars() {
                    canvas.put(x, run.y as usize, ch, style.clone());
                    x += crate::compose::display_width(&ch.to_string()).max(1);
                }
            }
        }
        // 팝업은 **모든 패널 위**다(트리 밖이라 blit 루프에 안 들어온다). 상자를 그리고
        // 그 안에 팝업 패널의 화면을 얹는다 — 뒤 화면은 **안 지운다**(테두리가 경계를
        // 알려 주고, 지우면 팝업이 화면 전체를 먹은 것처럼 보인다).
        if let Some(popup) = layout.popup.as_ref() {
            let style = crate::style::CellStyle {
                fg: Some(crate::style::Color::Named(crate::style::NamedColor::BrightCyan)),
                bold: true,
                ..Default::default()
            };
            canvas.draw_box(
                popup.x as usize,
                popup.y as usize,
                popup.w as usize,
                popup.h as usize,
                style,
            );
            if !popup.title.is_empty() && popup.w > 4 {
                let label: String = format!(" {} ", popup.title)
                    .chars()
                    .take(popup.w as usize - 2)
                    .collect();
                let start = popup.x as isize + 1;
                for (i, ch) in label.chars().enumerate() {
                    canvas.put_cell(start + i as isize, popup.y as isize, ch, style);
                }
            }
            if let Some(screen) = self.screens.get(&popup.id) {
                canvas.blit_pane(
                    &screen.rows,
                    popup.cx as usize,
                    popup.cy as usize,
                    popup.cw as usize,
                    popup.ch as usize,
                );
            }
        }
        // 패널 번호(`prefix q`)는 **가장 위**다 — 시계·달력 위에서도 번호가 보여야
        // 그 패널로 갈 수 있다.
        if self.pane_numbers {
            for (n, pane) in layout.panes.iter().enumerate() {
                let text = n.to_string();
                // 활성은 초록, 나머지는 노랑 바탕에 검은 글자(파이썬과 같다).
                let style = crate::style::CellStyle {
                    fg: Some(crate::style::Color::Named(crate::style::NamedColor::Black)),
                    bg: Some(crate::style::Color::Named(if pane.id == layout.active {
                        crate::style::NamedColor::Green
                    } else {
                        crate::style::NamedColor::Yellow
                    })),
                    bold: true,
                    ..Default::default()
                };
                let x0 = pane.x as isize
                    + (pane.w as isize - text.chars().count() as isize).max(0) / 2;
                let y0 = pane.y as isize + (pane.h / 2) as isize;
                for (i, c) in text.chars().enumerate() {
                    canvas.put_cell(x0 + i as isize, y0, c, style);
                }
            }
        }
        Some(canvas)
    }

    /// 패널의 블록 목록. 셸 통합이 없으면 빈 슬라이스다.
    pub fn blocks(&self, pane_id: i64) -> &[Block] {
        self.blocks.get(&pane_id).map_or(&[], Vec::as_slice)
    }

    /// 활성 패널의 블록. 화면에 붙일 목록으로 이걸 쓴다.
    pub fn active_blocks(&self) -> &[Block] {
        match self.layout.as_ref().map(|l| l.active) {
            Some(id) => self.blocks(id),
            None => &[],
        }
    }

    /// 블록을 하나라도 받았는가(= 셸 통합이 켜져 있는가).
    pub fn has_blocks(&self) -> bool {
        self.blocks.values().any(|v| !v.is_empty())
    }

    /// 지금 보고 있는 탭이 원격인가.
    pub fn active_tab_is_remote(&self) -> bool {
        self.tabs().active().is_some_and(|t| t.remote)
    }

    /// 지금 보고 있는 패널의 id. 배치를 아직 못 받았으면 `None`.
    ///
    /// 상류가 보내 준 트랜스크립트를 패널별로 들고 있다가 이 값으로 고른다 — 한 세션에
    /// Claude 패널이 여럿이면 아무거나 골라선 안 된다.
    pub fn active_pane(&self) -> Option<i64> {
        self.layout.as_ref().map(|l| l.active)
    }

    /// 지금 보고 있는 패널의 작업 디렉터리. 서버가 블록으로 알려 준 cwd 가 유일한
    /// 출처이므로 **셸 통합이 없으면 모른다**(그때 Claude 뷰도 안 뜬다).
    ///
    /// # 원격 탭에서는 알려 주지 않는다
    ///
    /// 원격 패널의 블록도 cwd 를 싣고 내려온다(상류가 relay 한다 — `serverremote.py`).
    /// 하지만 그 경로는 **상류 머신의 경로**이고, 이 클라가 그걸로 여는 트랜스크립트
    /// 폴더는 **이 머신의** `~/.claude/projects` 아래다. 두 머신의 디렉터리 구조가 닮아
    /// 있으면(같은 사람의 작업 트리라면 대개 그렇다) 폴더가 **실제로 존재하고**, 그러면
    /// 원격 패널 자리에 **로컬 세션의 대화가 뜬다** — 비어 보이는 것보다 나쁘다. 조용히
    /// 틀린 화면이라 사용자는 그게 남의 세션인 줄 모른다(2026-07-27g 실측 결함).
    ///
    /// 그래서 모르면 모른다고 한다. 원격 패널의 진짜 대화는 상류가 원문 꼬리를 실어
    /// 보내는 경로로 온다([`ServerMessage::Claude`](crate::ServerMessage) — 설계문서
    /// §7 P5 ⓑ'). 이 함수는 그 경로와 무관하게 "이 머신에서 직접 읽어도 되는가"만 답한다.
    ///
    /// **두 뷰가 각자 답하면 안 된다.** 한쪽만 원격 판정을 빠뜨리면 그 클라에서만 남의
    /// 대화가 뜨는데, 그건 그럴듯해서 아무도 의심하지 않는다.
    pub fn active_cwd(&self) -> Option<&str> {
        if self.active_tab_is_remote() {
            return None;
        }
        self.active_blocks()
            .iter()
            .rev()
            .find_map(|b| b.cwd.as_deref())
    }

    /// 패널 안에서의 커서 위치(열, 행). 커서를 그리지 않는 패널이면 `None`.
    pub fn pane_cursor(&self, pane_id: i64) -> Option<(u16, u16)> {
        self.screens.get(&pane_id)?.cursor
    }

    /// 이 패널 뷰포트 **첫 줄의 절대 행 번호**. 화면을 아직 못 받았으면 `None`.
    ///
    /// 서버가 `screen` 마다 실어 보내는 값이고, 서버의 스크롤백 추출
    /// (`model.Pane.extract_range`)이 쓰는 좌표계의 원점이다.
    pub fn pane_top(&self, pane_id: i64) -> Option<usize> {
        Some(self.screens.get(&pane_id)?.top)
    }

    /// 이 패널이 라이브에서 **위로 올라간 행수**. 화면을 아직 못 받았으면 `None`.
    ///
    /// 서버는 0 이 아닐 때만 `scr` 로 싣는다 — 라이브면 필드가 없고 우리는 0 으로 읽는다.
    pub fn pane_scroll(&self, pane_id: i64) -> Option<usize> {
        Some(self.screens.get(&pane_id)?.scroll)
    }

    /// 패널의 **내용** 사각형 `(x, y, w, h)`. 테두리는 뺀 안쪽이다.
    ///
    /// 선택은 테두리를 포함하지 않는다 — 테두리는 서버가 그린 글자가 아니라 클라가
    /// 얹은 크롬이고, 스크롤백에는 존재하지 않는다.
    pub fn pane_rect(&self, pane_id: i64) -> Option<(u16, u16, u16, u16)> {
        self.panes()
            .iter()
            .find(|p| p.id == pane_id)
            .map(|p| (p.x, p.y, p.w, p.h))
    }

    /// 캔버스 좌표를 그 패널의 내용 사각형 **안으로 접는다**.
    ///
    /// 드래그가 패널 밖으로 나가도 선택은 시작한 패널 안에 머물러야 한다 — 화면에서는
    /// 나란히 보여도 옆 패널의 줄은 남의 스크롤백이라 이어 붙일 수 없다. 파이썬 클라의
    /// `_clamp_sel` 과 같은 처리다.
    pub fn clamp_to_pane(&self, pane_id: i64, x: u16, y: u16) -> Option<(u16, u16)> {
        let (px, py, w, h) = self.pane_rect(pane_id)?;
        // 폭·높이가 0 인 패널은 접을 안쪽이 없다(창이 줄어드는 순간의 레이스).
        let (last_x, last_y) = (px + w.checked_sub(1)?, py + h.checked_sub(1)?);
        Some((x.clamp(px, last_x), y.clamp(py, last_y)))
    }

    /// 캔버스 좌표 → 그 패널 기준 **절대 좌표**. 패널 내용 밖이거나 화면을 아직 못
    /// 받았으면 `None`.
    ///
    /// 강조를 그릴 때 셀마다 부른다 — 밖이면 `None` 이므로 호출부가 사각형 검사를 따로
    /// 하지 않아도 된다.
    pub fn pane_abs(&self, pane_id: i64, x: u16, y: u16) -> Option<crate::selection::Point> {
        let (px, py, w, h) = self.pane_rect(pane_id)?;
        if x < px || y < py || x >= px + w || y >= py + h {
            return None;
        }
        let top = self.pane_top(pane_id)?;
        Some(crate::selection::Point::new(
            top + usize::from(y - py),
            x - px,
        ))
    }
}

/// 패널 경계선·제목을 격자 위에 그린다.
///
/// # 왜 내용을 다 앉힌 **뒤** 인가
///
/// 테두리는 내용 바깥 한 칸을 쓰지만, 화면이 아직 안 온 패널이 있으면 그 자리는 공백이다.
/// 내용을 먼저 깔고 그 위에 그려야 순서에 상관없이 같은 그림이 나온다.
///
/// # 그리는 순서가 곧 우선순위다
///
/// 비활성 → 활성 → 제목 순으로 그린다. 맞닿은 변은 **나중에 그린 쪽 색**이 남으므로,
/// 활성 패널의 테두리가 이웃 위로 온다(파이썬 클라와 같은 순서 = 같은 그림).

/// 입력기 배지를 **활성 패널의 첫 행 오른쪽 끝**에 그린다(정본과 같은 자리).
///
/// 정본 주석이 이 자리를 고른 이유를 적고 있다 — 닫기 `[x]` 는 테두리 행으로 한 칸
/// 올려 "콘텐츠를 안 가리고 IME 배지(첫 행 우상단)와도 안 겹치게" 한다. 즉 배지의
/// 자리가 먼저이고 나머지가 그것을 피한다.
///
/// 패널이 좁으면(배지가 절반을 넘으면) **안 그린다** — 화면을 덮어 가며 알릴 만한
/// 것은 아니다.
fn draw_frames(canvas: &mut Canvas, layout: &Layout) {
    let mut draw = |pane: &PaneLayout| {
        let Some([x, y, w, h]) = pane.boxrect else {
            return;                 // 테두리를 안 그리는 배치(패널 하나 + single-border off)
        };
        let style = if pane.id == layout.active {
            border_style(true)
        } else {
            border_style(false)
        };
        canvas.draw_box(x as usize, y as usize, w as usize, h as usize, style);
    };
    for pane in layout.panes.iter().filter(|p| p.id != layout.active) {
        draw(pane);
    }
    for pane in layout.panes.iter().filter(|p| p.id == layout.active) {
        draw(pane);
    }
    // 제목은 테두리를 **전부** 그린 뒤 별도 패스로 — 이웃 패널의 변이 이름을 덮어쓰지
    // 않게 한다(파이썬 클라 `_draw_title` 의 같은 이유).
    for pane in layout
        .panes
        .iter()
        .filter(|p| p.id != layout.active)
        .chain(layout.panes.iter().filter(|p| p.id == layout.active))
    {
        draw_title(canvas, pane, layout);
    }
    for bar in &layout.titlebars {
        draw_titlebar(canvas, bar);
    }
}

/// 이름을 위쪽 테두리 가운데에.
///
/// 서버는 모든 패널의 `title` 을 보내지만 **항상 보이지는 않는다** — 기본 이름(`shell`)은
/// 잡음이라 사용자가 이름을 바꿨거나 pane-border-status 가 켜졌을 때만 보인다. 파이썬
/// 클라와 같은 판정이라야 두 클라가 같은 화면을 그린다.
fn draw_title(canvas: &mut Canvas, pane: &PaneLayout, layout: &Layout) {
    let Some([x, y, w, _]) = pane.boxrect else {
        return;
    };
    let title = pane.title.trim();
    let renamed = !title.is_empty() && title != "shell";
    if title.is_empty() || w < 4 || !(layout.border_status || renamed) {
        return;
    }
    let label: String = format!(" {title} ")
        .chars()
        .take(w as usize - 2)
        .collect();
    let width = label.chars().count();
    // 가운데 정렬. 모서리는 침범하지 않는다.
    let start = x as usize + std::cmp::max(1, (w as usize).saturating_sub(width) / 2);
    let limit = x as usize + w as usize - 1;
    let style = border_style(pane.id == layout.active);
    let mut cx = start;
    for ch in label.chars() {
        if cx >= limit {
            break;
        }
        cx += canvas.put_text(cx, y as usize, &ch.to_string(), style);
    }
}

/// pane-border-status 제목줄: 라벨 뒤는 `─` 로 채운다.
fn draw_titlebar(canvas: &mut Canvas, bar: &TitleBar) {
    let label = format!(" {} ", bar.title);
    let style = if bar.active {
        titlebar_style(true)
    } else {
        titlebar_style(false)
    };
    let fill = CellStyle {
        fg: Some(Color::Named(NamedColor::BrightBlack)),
        ..CellStyle::default()
    };
    let mut cx = bar.x as usize;
    let end = bar.x as usize + bar.w as usize;
    for ch in label.chars() {
        if cx >= end {
            break;
        }
        cx += canvas.put_text(cx, bar.y as usize, &ch.to_string(), style);
    }
    while cx < end {
        canvas.put_text(cx, bar.y as usize, "─", fill);
        cx += 1;
    }
}

/// 테두리 색. 활성은 파랑+굵게, 비활성은 흐린 회색 — 파이썬 클라의 관습과 같다
/// (`primary`+bold / `grey42`). 팔레트 이름으로 두어 사용자 터미널 테마를 따른다.
fn border_style(active: bool) -> CellStyle {
    if active {
        CellStyle {
            fg: Some(Color::Named(NamedColor::Blue)),
            bold: true,
            ..CellStyle::default()
        }
    } else {
        CellStyle {
            fg: Some(Color::Named(NamedColor::BrightBlack)),
            ..CellStyle::default()
        }
    }
}

/// 제목줄 색(파이썬 `_TB_ACTIVE_STYLE`/`_TB_INACTIVE_STYLE`): 검은 글자 + 배경 반전.
fn titlebar_style(active: bool) -> CellStyle {
    CellStyle {
        fg: Some(Color::Named(NamedColor::Black)),
        bg: Some(Color::Named(if active {
            NamedColor::Cyan
        } else {
            NamedColor::White
        })),
        ..CellStyle::default()
    }
}

#[cfg(test)]
#[path = "session_tests.rs"]
mod tests;


/// full `status` 의 **플러그인 표면**(Tier A). 이 프레임에 없으면 `None` —
/// 델타라는 뜻이지 "기여가 없다"가 아니다(개요와 같은 규칙).
fn plugin_surface_from_status(status: &crate::message::Status) -> Option<PluginSurface> {
    let obj = status.fields.get("plugin_surface")?.as_object()?;
    let text = |v: Option<&serde_json::Value>| {
        v.and_then(serde_json::Value::as_str).unwrap_or_default().to_owned()
    };
    let strings = |key: &str| -> Vec<String> {
        obj.get(key)
            .and_then(serde_json::Value::as_array)
            .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_owned)).collect())
            .unwrap_or_default()
    };
    let rows = |key: &str| -> Vec<serde_json::Map<String, serde_json::Value>> {
        obj.get(key)
            .and_then(serde_json::Value::as_array)
            .map(|a| a.iter().filter_map(|v| v.as_object().cloned()).collect())
            .unwrap_or_default()
    };
    Some(PluginSurface {
        // 이름 없는 줄은 실어도 부를 수가 없다 — 누르면 아무 일도 안 나는 줄이 된다.
        commands: rows("commands")
            .into_iter()
            .filter_map(|r| {
                let name = text(r.get("name"));
                (!name.is_empty()).then(|| PluginCommand {
                    name,
                    desc: text(r.get("desc")),
                    cat: text(r.get("cat")),
                })
            })
            .collect(),
        noarg: strings("noarg"),
        menu_items: rows("menu_items")
            .into_iter()
            .filter_map(|r| {
                let key = text(r.get("key"));
                (!key.is_empty()).then(|| PluginMenuItem { key, label: text(r.get("label")) })
            })
            .collect(),
        settings: rows("settings")
            .into_iter()
            .filter_map(|r| {
                let key = text(r.get("key"));
                (!key.is_empty()).then(|| PluginSetting {
                    key,
                    cat: text(r.get("cat")),
                    kind: text(r.get("type")),
                    values: r
                        .get("values")
                        .and_then(serde_json::Value::as_array)
                        .map(|a| {
                            a.iter().filter_map(|v| v.as_str().map(str::to_owned)).collect()
                        })
                        .unwrap_or_default(),
                })
            })
            .collect(),
        setting_cats: strings("setting_cats"),
    })
}

/// full `status` 의 플러그인 개요. 이 프레임에 없으면 `None`(델타라는 뜻이지 "플러그인이
/// 없다"가 아니다 — 둘을 섞으면 델타가 올 때마다 목록이 사라진다).
fn plugins_from_status(status: &crate::message::Status) -> Option<Vec<PluginInfo>> {
    let rows = status.fields.get("plugins")?.as_array()?;
    Some(
        rows.iter()
            .filter_map(|row| {
                let obj = row.as_object()?;
                let text = |key: &str| {
                    obj.get(key)
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or_default()
                        .to_owned()
                };
                let name = text("name");
                // 이름 없는 줄은 토글할 수단이 없다(서버가 이름으로 받는다) — 실으면
                // 눌러도 아무 일이 안 나는 줄이 된다.
                if name.is_empty() {
                    return None;
                }
                Some(PluginInfo {
                    name,
                    description: text("description"),
                    category: text("category"),
                    enabled: obj
                        .get("enabled")
                        .and_then(serde_json::Value::as_bool)
                        .unwrap_or(true),
                })
            })
            .collect(),
    )
}

/// 매 `status` 에 실려 오는 **꺼진 플러그인 이름**들.
fn disabled_plugins_from_status(status: &crate::message::Status) -> Option<Vec<String>> {
    Some(
        status
            .fields
            .get("disabled_plugins")?
            .as_array()?
            .iter()
            .filter_map(|v| v.as_str().map(str::to_owned))
            .collect(),
    )
}
