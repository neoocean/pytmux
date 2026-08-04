//! 서버와 주고받는 메시지.
//!
//! 프로토콜은 pytmux 의 `pytmuxlib/serverio.py`(생산)와 `pytmuxlib/clientio.py`(소비)가
//! 권위다. 여기서는 **클라가 이해해야 하는 것만** 옮긴다 — 모르는 필드는 조용히 버린다
//! (서버가 필드를 늘려도 구 클라가 깨지지 않게).

use serde::{Deserialize, Serialize};

/// 클라↔서버 와이어 프로토콜 버전.
///
/// 서버(`protocol.PROTO_VERSION`)와 **같아야 한다**. 불일치하면 서버가 명시적으로
/// 거절한다 — 조용한 오작동보다 낫다는 게 서버 쪽 설계 의도다.
pub const PROTO_VERSION: u32 = 1;

/// 한 프레임 페이로드 상한(64MiB). 서버의 `MAX_FRAME` 과 같다.
///
/// **값으로 공유되는 상수**라 한쪽만 바뀌면 조용히 깨진다. 그래서 적합성 테스트가
/// 이 값들을 직접 못박는다.
pub const MAX_FRAME: usize = 64 * 1024 * 1024;

/// 인증 전(핸드셰이크) 프레임 상한(64KiB). 서버의 `HANDSHAKE_MAX_FRAME` 과 같다.
pub const HANDSHAKE_MAX_FRAME: usize = 64 * 1024;

/// 패널 최소·최대 치수. 서버가 `clamp_dim` 으로 강제하는 범위와 같다.
pub const MIN_W: u16 = 3;
pub const MIN_H: u16 = 3;
pub const MAX_W: u16 = 2000;
pub const MAX_H: u16 = 2000;

/// 스타일 속성 묶음. 와이어에서는 JSON 객체이며, 기본 스타일이면 비어 있다.
///
/// 지금은 통째로 보관만 한다 — 화면에 **무슨 글자가 있는가**를 다루는 P2 에서는
/// 색이 필요 없고, 실제로 칠하는 것은 P3 의 일이다. 키를 미리 정의하지 않는 이유는
/// 서버가 속성을 늘려도 이 계층이 깨지지 않게 하기 위해서다.
pub type Style = serde_json::Map<String, serde_json::Value>;

/// 한 행의 스타일 런: 와이어에서 `[텍스트, 스타일객체]` 2원소 배열.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Run {
    pub text: String,
    pub style: Style,
}

impl Run {
    /// 스타일 없는 런. 테스트와 합성 결과 조립에 쓴다.
    pub fn plain(text: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            style: Style::new(),
        }
    }
}

/// 화면 한 행 = 런들의 나열.
pub type Row = Vec<Run>;

/// 서버 → 클라 메시지.
///
/// `t` 필드로 갈린다. 모르는 종류는 [`ServerMessage::Unknown`] 으로 떨어져 **연결을
/// 끊지 않는다** — 서버가 새 메시지를 추가해도 구 클라는 무시하고 계속 돈다.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "t")]
pub enum ServerMessage {
    #[serde(rename = "layout")]
    Layout(Layout),
    #[serde(rename = "screen")]
    Screen(Screen),
    /// **바뀐 행만** 실은 화면 갱신. 서버는 행 수가 그대로이고 바뀐 행이 70% 이하이면
    /// full 대신 이걸 보낸다(`serverio._screen_frame`).
    ///
    /// # 이걸 모르면 화면이 멎는다 (실측 2026-07-28)
    ///
    /// 이 변형이 없던 동안 델타는 [`ServerMessage::Unknown`] 으로 떨어져 **조용히**
    /// 버려졌다. 증상이 고약하다 — 낱글자 타이핑처럼 몇 줄만 바뀌는 것은 화면에 아예
    /// 안 나타나고, 명령 출력처럼 화면을 크게 갈아엎는 것은(70% 초과 → full) 정상으로
    /// 보인다. 그래서 "가끔 화면이 멎는다"로만 보였다.
    ///
    /// `cursor`·`wrap`·`top` 은 델타가 아니라 **매번 전체**가 온다(파이썬 클라와 같다).
    #[serde(rename = "screen-delta")]
    ScreenDelta(ScreenDelta),
    #[serde(rename = "status")]
    Status(Status),
    /// `request_tree` 회신 — 세션 → 탭 → 패널의 개요(패리티 G3b).
    #[serde(rename = "tree")]
    Tree(Tree),
    /// `request_buffers` 회신 — 페이스트 버퍼 목록.
    #[serde(rename = "buffers")]
    Buffers {
        #[serde(default)]
        items: Vec<BufferItem>,
    },
    /// 패널의 블록 목록(§10-13). 서버는 `caps` 로 광고한 클라에게만 보낸다.
    #[serde(rename = "blocks")]
    Blocks {
        pane: i64,
        #[serde(default, deserialize_with = "de_blocks")]
        blocks: Vec<crate::blocks::Block>,
    },
    /// [`Command::CopyRange`](crate::command::Command::CopyRange) 의 회신 — 서버가
    /// 스크롤백에서 뽑아 준 선택 텍스트.
    ///
    /// **화면 상태가 아니다.** 이건 클립보드로 가야 하고 그건 외부 프로세스를 띄우는
    /// 일이라, 상태 누적기가 아니라 이벤트 루프가 받는다.
    ///
    /// 서버는 4MB 에서 자른다(`servercmd._COPY_RANGE_MAX`) — 잘렸다는 표시는 오지
    /// 않으므로 클라가 알 수 있는 것은 받은 길이뿐이다.
    #[serde(rename = "selection")]
    Selection {
        #[serde(default)]
        text: String,
    },
    /// 이 패널의 Claude 트랜스크립트 **원문 꼬리**(JSONL). 서버는 `caps` 로 광고한
    /// 클라에게만 보낸다.
    ///
    /// # 왜 원문인가
    ///
    /// 이걸로 채우려는 자리는 **원격 패널**이다 — 그 트랜스크립트는 상류 기계에 있어서
    /// 이 클라가 열 수 없다. 상류가 항목까지 만들어 보내는 길도 있었지만, 그러려면
    /// 파이썬에 표시용 파서를 새로 써야 하고 그 파서가 이 크레이트와 어긋나는 순간
    /// **같은 대화가 탭에 따라 달라 보인다**(설계문서 §7 P5 의 비용 재측정 · 사용자
    /// 결정 2026-07-28). 원문을 받으면 파서는 하나로 남는다.
    ///
    /// 상한은 상류가 건다(64KB/80줄) — 반대 근거는 '사적'이 아니라 **양**이었다.
    /// 페더레이션 경계에서 한 번 더 자른다(`serverremote._sanitize_claude_tail`).
    #[serde(rename = "claude")]
    Claude {
        pane: i64,
        #[serde(default)]
        tail: String,
    },
    /// 이 패널 셸의 작업 디렉터리. 서버는 `caps` 로 광고한 클라에게만 보낸다.
    ///
    /// 패널 글 안의 **상대경로를 푸는 기준**이다(§10-21ⓧ2 / pytmux-24). 값의 출처는
    /// 셸이 보낸 `OSC 7` 이라 프로브가 0 이다 — 서버가 pid 로 `/proc`·PEB·`lsof` 를
    /// 뒤지는 길은 동기 호출이라 레이아웃마다 부를 수 없다.
    ///
    /// 블록 목록과 **따로** 오는 이유는 크기다: 값은 문자열 하나인데 블록은 최대
    /// 500개라, 경로만 풀면 되는 클라가 그 목록을 다 받는 것이 `caps` 게이트가
    /// 막으려던 바로 그 비용이다.
    ///
    /// `cwd: null` 은 **모르게 됐다**는 뜻이다(셸 통합이 꺼졌거나 다른 셸로 바뀌었다).
    /// 그때는 기준을 버려야 한다 — 옛 기준으로 계속 풀면 조용히 틀린 경로를 복사한다.
    #[serde(rename = "cwd")]
    Cwd {
        pane: i64,
        #[serde(default)]
        cwd: Option<String>,
    },
    /// 서버가 연결을 정상 종료한다.
    #[serde(rename = "bye")]
    Bye,
    #[serde(rename = "error")]
    Error {
        #[serde(default)]
        msg: String,
    },
    /// `request_restart_check` 회신 — 작업 보존 재시작이 안전한가.
    ///
    /// 필드가 여덟이고 **늘어날 수 있다**. 화면에는 사람이 읽을 줄로 풀어 보이므로
    /// 통째로 받아 둔다(모르는 필드를 버리면 서버가 늘렸을 때 그 줄이 안 보인다).
    #[serde(rename = "restart_check")]
    RestartCheck {
        #[serde(flatten)]
        fields: serde_json::Map<String, serde_json::Value>,
    },
    /// `request_version` 회신 — 서버 코드 버전·업타임·pid.
    #[serde(rename = "version")]
    Version {
        #[serde(default)]
        version: String,
        #[serde(default)]
        uptime: f64,
        #[serde(default)]
        pid: i64,
    },
    /// 서버가 사람에게 하는 말(remote-attach 결과 등).
    ///
    /// # 이걸 모르면 **아무 일도 안 일어난 것처럼 보인다**
    ///
    /// `remote-attach` 가 실패하면 서버는 이걸 보내고 끝낸다 — 오류 프레임이 아니다.
    /// 이 변형이 없던 동안 그건 `Unknown` 으로 떨어져 조용히 버려졌고, 사용자에게는
    /// "명령을 쳤는데 아무 일도 안 남"으로만 보였다(서버 쪽 주석이 그 갭을 기록해 둔
    /// 자리이기도 하다).
    #[serde(rename = "notice")]
    Notice {
        #[serde(default)]
        text: String,
        /// 등급 — `ok`/`info`/`warn`/`error`. 없거나 모르는 값이면 `info` 로 본다
        /// (구 서버와 같게 동작해야 한다).
        #[serde(default, rename = "sev")]
        sev: Option<String>,
        /// `text` 를 **우리 로케일로 다시 지을 재료**(로케일 ⓑ). 서버가 지은 글은
        /// 서버 프로세스의 로케일을 타므로, 자리가 있는 알림(`자동재개: '{msg}'
        /// 주입(패널 {pane})`)은 원문이 키가 못 된다 — 원문 포맷과 값을 따로 받아
        /// [`crate::i18n_say`] 가 `tf` 로 짓는다.
        ///
        /// 서버는 `key`+`kw` 도 같이 싣지만 **그건 정본 클라의 도메인 키**다
        /// (우리 카탈로그는 한국어 원문이 키라 그 이름으로는 못 찾는다).
        #[serde(default)]
        i18n: crate::session::I18nMap,
    },
    /// 플러그인이 **무엇을 그릴지**(설계 Tier C · P4). 목록/글 두 모양뿐이고, 다음
    /// 동작은 클라가 `plugin_action` 으로 되묻는다 — 행동은 서버(=플러그인)가 정한다.
    #[serde(rename = "plugin_screen")]
    PluginScreen(crate::session::PluginScreen),
    /// 그 화면을 닫으라(플러그인이 흐름을 끝냈다).
    #[serde(rename = "plugin_screen_close")]
    PluginScreenClose {
        #[serde(default)]
        id: String,
    },
    /// 플러그인이 **화면에 얹을 글자**(설계 Tier B · P3 — 셀 기여).
    ///
    /// 정본은 시계·달력을 자기 프로세스에서 그린다. 우리는 파이썬을 못 읽으므로 서버가
    /// **무엇을 어디에 쓸지**를 런으로 준다 — 로직은 플러그인 한 벌로 남는다.
    #[serde(rename = "plugin_cells")]
    PluginCells(crate::session::PluginCells),
    #[serde(rename = "ok")]
    Ok(serde_json::Value),
    #[serde(rename = "pong")]
    Pong {
        /// 우리가 ping 에 실어 보낸 monotonic 초 — 서버가 그대로 echo 한다.
        /// 지금 시각에서 빼면 그게 왕복 지연이다(클라 쪽 대기 장부가 필요 없다).
        #[serde(default)]
        ts: Option<f64>,
    },
    #[serde(other)]
    Unknown,
}

impl ServerMessage {
    /// 진단용 이름 한 낱말(와이어의 `t` 값과 같다).
    ///
    /// 내용은 안 싣는다 — 화면 한 판이 로그에 통째로 쏟아지고, 그 안에는 사용자의 글이
    /// 들어 있다. **무엇이 왔나**만 알면 "화면이 멎었다"의 층은 갈린다.
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Layout(_) => "layout",
            Self::Screen(_) => "screen",
            Self::ScreenDelta(_) => "screen-delta",
            Self::Status(_) => "status",
            Self::Tree(_) => "tree",
            Self::Buffers { .. } => "buffers",
            Self::PluginScreen(_) => "plugin_screen",
            Self::PluginScreenClose { .. } => "plugin_screen_close",
            Self::PluginCells(_) => "plugin_cells",
            Self::Blocks { .. } => "blocks",
            Self::Selection { .. } => "selection",
            Self::Claude { .. } => "claude",
            Self::Cwd { .. } => "cwd",
            Self::Bye => "bye",
            Self::Error { .. } => "error",
            Self::Notice { .. } => "notice",
            Self::Version { .. } => "version",
            Self::RestartCheck { .. } => "restart_check",
            Self::Ok(_) => "ok",
            Self::Pong { .. } => "pong",
            // ★ 이 값이 로그에 보이면 **서버가 우리가 모르는 종류를 보내고 있다**는
            // 뜻이다. 그 메시지는 화면을 못 바꾸므로 증상은 "가끔 화면이 멎는다"가 된다.
            Self::Unknown => "unknown",
        }
    }
}

/// 패널 배치. 좌표·크기는 **서버가 이미 계산해서** 준다 — 클라는 배치만 한다.
///
/// `dividers` 는 **그리는 것이 아니라 마우스 히트테스트용**이다(파이썬 클라도
/// `_divider_at` 에서만 쓴다) — 경계선 자체는 패널마다 오는 [`PaneLayout::boxrect`] 로
/// 그린다.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Layout {
    pub cols: u16,
    pub rows: u16,
    #[serde(default)]
    pub panes: Vec<PaneLayout>,
    /// 현재 활성 패널의 id.
    #[serde(default)]
    pub active: i64,
    #[serde(default)]
    pub bordered: bool,
    #[serde(default)]
    pub border_status: bool,
    /// 패널 제목줄(pane-border-status 가 켜졌을 때만 채워져 온다).
    #[serde(default)]
    pub titlebars: Vec<TitleBar>,
    /// 분할 경계. 마우스로 잡아 끌 손잡이다.
    #[serde(default)]
    pub dividers: Vec<Divider>,
    /// 떠 있는 팝업 패널(`display-popup`). 없으면 `None`.
    ///
    /// **트리 밖이다** — `panes` 에 안 들어오고 여기로만 온다. 화면도 따로 오고
    /// (`screen` 메시지의 `pane` 이 이 id), 입력도 이 id 로 보내야 그 PTY 에 닿는다.
    #[serde(default)]
    pub popup: Option<PopupLayout>,
}

/// 떠 있는 팝업 패널의 자리(서버 `_popup_layout`).
#[derive(Debug, Clone, Default, Deserialize)]
pub struct PopupLayout {
    pub id: i64,
    /// 상자(테두리 포함) 자리.
    pub x: u16,
    pub y: u16,
    pub w: u16,
    pub h: u16,
    /// 내용(테두리 안) 자리 — 화면을 여기에 얹는다.
    #[serde(default)]
    pub cx: u16,
    #[serde(default)]
    pub cy: u16,
    #[serde(default)]
    pub cw: u16,
    #[serde(default)]
    pub ch: u16,
    #[serde(default)]
    pub title: String,
    /// 팝업 안 앱의 마우스 트래킹(일반 패널의 `mouse`/`mouse_sgr` 와 같은 뜻).
    /// 구버전 서버는 이 칸을 안 싣는다 — 기본 0/false 는 "패스스루 없음"이라
    /// 종전 동작 그대로다(exit-empty 처럼 거짓말이 되는 기본값이 아니다).
    #[serde(default)]
    pub mouse: u8,
    #[serde(default)]
    pub mouse_sgr: bool,
}

impl PopupLayout {
    /// 팝업 안 앱의 마우스 추적 상태(패스스루 판정용 — `PaneLayout::mouse_mode` 동형).
    pub fn mouse_mode(&self) -> crate::mouse::MouseMode {
        crate::mouse::MouseMode {
            track: self.mouse,
            sgr: self.mouse_sgr,
        }
    }
}

/// 분할 하나의 경계선. 좌우 분할이면 세로 한 칸 폭, 상하 분할이면 가로 한 칸 높이다.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Divider {
    /// 이 경계가 속한 분할의 id. 리사이즈 명령이 이것으로 대상을 가리킨다.
    pub split_id: i64,
    /// `"lr"`(좌우) 또는 `"tb"`(상하).
    #[serde(default)]
    pub orient: String,
    pub x: u16,
    pub y: u16,
    pub w: u16,
    pub h: u16,
    /// 이 분할이 차지한 전체 사각형 `[x, y, w, h]`.
    ///
    /// 비율을 계산하려면 경계선 자체가 아니라 **분할 전체**의 크기가 필요하다 —
    /// 마우스가 이 사각형 안 어디에 있는가가 곧 새 비율이다.
    #[serde(default)]
    pub rect: [u16; 4],
}

impl Divider {
    /// 이 경계를 마우스가 잡았는가(캔버스 좌표).
    pub fn contains(&self, x: u16, y: u16) -> bool {
        x >= self.x && x < self.x + self.w && y >= self.y && y < self.y + self.h
    }

    /// 마우스 위치가 뜻하는 새 비율. 파이썬 클라와 **같은 계산**이다
    /// (`clientwidgets.on_mouse_move`) — 두 클라의 드래그 감각이 갈리면 안 된다.
    ///
    /// 0.05~0.95 로 자르는 것도 같다. 끝까지 밀면 한쪽이 사라지는데, 그건 리사이즈가
    /// 아니라 패널 닫기라 다른 명령이어야 한다.
    pub fn ratio_at(&self, x: u16, y: u16) -> f64 {
        let [rx, ry, rw, rh] = self.rect;
        let (pos, origin, span) = if self.orient == "lr" {
            (x, rx, rw)
        } else {
            (y, ry, rh)
        };
        let avail = span.saturating_sub(1);
        let ratio = if avail == 0 {
            0.5
        } else {
            f64::from(pos.saturating_sub(origin)) / f64::from(avail)
        };
        ratio.clamp(0.05, 0.95)
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct PaneLayout {
    pub id: i64,
    pub x: u16,
    pub y: u16,
    pub w: u16,
    pub h: u16,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub active: bool,
    /// 이 패널을 감싸는 테두리 사각형 `[x, y, w, h]`. **내용 영역(`x`/`y`/`w`/`h`)보다
    /// 한 칸씩 큰** 바깥 사각형이다.
    ///
    /// 테두리를 안 그리는 배치(패널 하나 + `single-border` off)에서는 아예 안 온다 —
    /// 그때 내용이 화면을 꽉 쓴다. 이름이 `boxrect` 인 것은 `box` 가 Rust 예약어라서다.
    #[serde(default, rename = "box")]
    pub boxrect: Option<[u16; 4]>,
    /// 패널 안 프로그램이 켠 마우스 추적 레벨(0/1/2/3).
    ///
    /// **서버만 알 수 있는 값**이다 — 켜는 것은 패널 안 프로그램의 DECSET 이고, 그것을
    /// 보는 것은 PTY 출력을 파싱하는 쪽뿐이다. 서버가 광고를 낮춰 보낼 수도 있으므로
    /// (Windows 에서 3→2) 클라는 해석하지 말고 그대로 믿는다.
    #[serde(default)]
    pub mouse: u8,
    /// 1006(SGR 확장 좌표)을 켰는가.
    #[serde(default)]
    pub mouse_sgr: bool,
}

impl PaneLayout {
    /// 이 패널의 마우스 추적 상태.
    pub fn mouse_mode(&self) -> crate::mouse::MouseMode {
        crate::mouse::MouseMode {
            track: self.mouse,
            sgr: self.mouse_sgr,
        }
    }
}

/// 패널 위에 얹히는 제목줄 한 개(pane-border-status).
#[derive(Debug, Clone, Default, Deserialize)]
pub struct TitleBar {
    pub x: u16,
    pub y: u16,
    pub w: u16,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub active: bool,
}

/// 한 패널의 화면 내용.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Screen {
    pub pane: i64,
    /// 행마다 `[텍스트, 스타일]` 런의 목록.
    #[serde(default, deserialize_with = "de_rows")]
    pub rows: Vec<Row>,
    /// `[x, y]` 또는 커서를 그리지 않을 때 `null`.
    #[serde(default)]
    pub cursor: Option<(u16, u16)>,
    /// 줄바꿈이 이어진 행 인덱스들(복사할 때 줄을 잇기 위한 정보).
    #[serde(default)]
    pub wrap: Vec<usize>,
    /// 스크롤백에서 이 뷰포트가 시작하는 절대 행 번호.
    #[serde(default)]
    pub top: usize,
    /// 라이브에서 위로 올라간 행수(`scr`). **0 이 아닐 때만** 온다.
    ///
    /// 터치 스크롤바가 썸 위치·점프 거리를 계산하는 데 쓴다(정본 `serverio` 주석 —
    /// 라이브에서는 필드가 아예 안 붙어 대역폭·와이어 골든이 불변이다). 구서버는 안
    /// 보내고, 그때 우리는 0 으로 읽는다 = "스크롤백 위로 안 올라갔다"이므로 바가
    /// 맨 아래를 가리킨다(정확도만 떨어지고 어긋나지 않는다).
    #[serde(default, rename = "scr")]
    pub scroll: usize,
}

/// `tree` 회신의 몸통. 표시용이라 **서버가 보내 준 그대로** 담는다.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Tree {
    #[serde(default)]
    pub sessions: Vec<TreeSession>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct TreeSession {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub windows: Vec<TreeWindow>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct TreeWindow {
    #[serde(default)]
    pub index: usize,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub active: bool,
    #[serde(default)]
    pub pinned: bool,
    #[serde(default)]
    pub panes: Vec<TreePane>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct TreePane {
    #[serde(default)]
    pub id: i64,
    #[serde(default)]
    pub title: String,
    /// 지금 그 패널에서 도는 앱(`fg`). 빈 문자열이면 서버가 못 알아낸 것이다.
    #[serde(default)]
    pub cmd: String,
    /// ssh 등 **원격에서 도는** 앱인가(서버 판정 — 클라가 이름으로 짐작하지 않는다).
    #[serde(default)]
    pub remote: bool,
}

/// 페이스트 버퍼 한 칸. `preview` 는 서버가 첫 줄 50자로 잘라 준다.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
pub struct BufferItem {
    #[serde(rename = "i")]
    pub index: usize,
    #[serde(default)]
    pub preview: String,
}

/// 한 패널의 **바뀐 행만** 담은 갱신([`ServerMessage::ScreenDelta`]).
#[derive(Debug, Clone, Default, Deserialize)]
pub struct ScreenDelta {
    pub pane: i64,
    /// `[행 번호, 그 행의 런들]` 목록. 번호는 **뷰포트 안 행 인덱스**다.
    #[serde(default, deserialize_with = "de_delta_rows")]
    pub rows: Vec<(usize, Row)>,
    #[serde(default)]
    pub cursor: Option<(u16, u16)>,
    /// 전체 목록이 매번 온다(델타가 아니다) — 통째로 교체한다.
    #[serde(default)]
    pub wrap: Vec<usize>,
    #[serde(default)]
    pub top: usize,
    /// 라이브에서 위로 올라간 행수(`scr`). **0 이 아닐 때만** 온다.
    ///
    /// 터치 스크롤바가 썸 위치·점프 거리를 계산하는 데 쓴다(정본 `serverio` 주석 —
    /// 라이브에서는 필드가 아예 안 붙어 대역폭·와이어 골든이 불변이다). 구서버는 안
    /// 보내고, 그때 우리는 0 으로 읽는다 = "스크롤백 위로 안 올라갔다"이므로 바가
    /// 맨 아래를 가리킨다(정확도만 떨어지고 어긋나지 않는다).
    #[serde(default, rename = "scr")]
    pub scroll: usize,
}

/// 상태줄·탭바 정보. 필드가 많고 자주 늘어나므로 통째로 보관하고 필요할 때 꺼낸다.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Status {
    #[serde(flatten)]
    pub fields: serde_json::Map<String, serde_json::Value>,
}

/// 와이어의 블록 목록을 타입 있는 형태로.
fn de_blocks<'de, D>(d: D) -> Result<Vec<crate::blocks::Block>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let raw: Vec<crate::blocks::BlockWire> = Vec::deserialize(d)?;
    Ok(raw.into_iter().map(Into::into).collect())
}

/// `[[텍스트, 스타일], ...]` 중첩 배열을 [`Row`] 목록으로 받는다.
fn de_rows<'de, D>(d: D) -> Result<Vec<Row>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let raw: Vec<Vec<(String, Style)>> = Vec::deserialize(d)?;
    Ok(raw
        .into_iter()
        .map(|row| {
            row.into_iter()
                .map(|(text, style)| Run { text, style })
                .collect()
        })
        .collect())
}

/// `[[행 번호, [[텍스트, 스타일], …]], …]` 를 `(행 번호, 행)` 목록으로 받는다.
fn de_delta_rows<'de, D>(d: D) -> Result<Vec<(usize, Row)>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let raw: Vec<(usize, Vec<(String, Style)>)> = Vec::deserialize(d)?;
    Ok(raw
        .into_iter()
        .map(|(y, row)| {
            (
                y,
                row.into_iter()
                    .map(|(text, style)| Run { text, style })
                    .collect(),
            )
        })
        .collect())
}

/// 이 클라가 이해하는 확장 기능. 서버는 **광고한 것만** 보낸다.
///
/// 광고하지 않으면 해당 메시지가 아예 오지 않는다 — 기능이 조용히 안 되는 것처럼
/// 보이므로 기본으로 전부 광고한다. 서버가 모르는 이름은 무시하므로 구버전 서버에
/// 붙어도 안전하다.
pub const CAPS: &[&str] = &["blocks", "claude", "cwd", "plugin_surface", "plugin_screen"];

/// 클라 → 서버 첫 프레임.
#[derive(Debug, Clone, Serialize)]
pub struct Hello {
    pub t: &'static str,
    pub proto: u32,
    pub cols: u16,
    pub rows: u16,
    /// 이 클라가 이해하는 확장 기능([`CAPS`]).
    pub caps: &'static [&'static str],
    /// 연결 인증 토큰(Windows 루프백 TCP 에서 필수, Unix 소켓에서도 있으면 보낸다).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub token: Option<String>,
    /// 붙을 세션 이름. 없으면 서버가 기본 세션을 준다.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session: Option<String>,
}

impl Hello {
    pub fn new(cols: u16, rows: u16) -> Self {
        Self {
            t: "hello",
            proto: PROTO_VERSION,
            cols: cols.clamp(MIN_W, MAX_W),
            rows: rows.clamp(MIN_H, MAX_H),
            caps: CAPS,
            token: None,
            session: None,
        }
    }

    pub fn with_token(mut self, token: Option<String>) -> Self {
        self.token = token;
        self
    }

    pub fn with_session(mut self, session: Option<String>) -> Self {
        self.session = session;
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn screen_message_parses_wire_shape() {
        let raw = r#"{"t":"screen","pane":3,
            "rows":[[["hi",{}],["there",{"fg":"red","bold":true}]],[["",{}]]],
            "cursor":[2,1],"wrap":[0],"top":17}"#;
        let msg: ServerMessage = serde_json::from_str(raw).unwrap();
        let ServerMessage::Screen(screen) = msg else {
            panic!("screen 으로 안 갈렸다");
        };
        assert_eq!(screen.pane, 3);
        assert_eq!(screen.rows.len(), 2);
        assert_eq!(screen.rows[0][0].text, "hi");
        assert_eq!(screen.rows[0][1].style["fg"], "red");
        assert_eq!(screen.cursor, Some((2, 1)));
        assert_eq!(screen.wrap, vec![0]);
        assert_eq!(screen.top, 17);
    }

    #[test]
    fn cursor_may_be_absent() {
        let raw = r#"{"t":"screen","pane":1,"rows":[],"cursor":null}"#;
        let msg: ServerMessage = serde_json::from_str(raw).unwrap();
        let ServerMessage::Screen(screen) = msg else {
            panic!()
        };
        assert_eq!(screen.cursor, None);
    }

    #[test]
    fn unknown_message_kinds_do_not_break_the_client() {
        // 서버가 메시지를 추가해도 구 클라가 연결을 끊으면 안 된다.
        let msg: ServerMessage =
            serde_json::from_str(r#"{"t":"brand_new_thing","x":1}"#).unwrap();
        assert!(matches!(msg, ServerMessage::Unknown));
    }

    #[test]
    fn unknown_fields_in_known_messages_are_ignored() {
        let raw = r#"{"t":"layout","cols":80,"rows":24,"panes":[],
                     "active":1,"some_future_field":{"a":1}}"#;
        let msg: ServerMessage = serde_json::from_str(raw).unwrap();
        let ServerMessage::Layout(layout) = msg else {
            panic!()
        };
        assert_eq!(layout.cols, 80);
    }

    #[test]
    fn hello_serializes_the_shape_the_server_expects() {
        let hello = Hello::new(80, 24).with_token(Some("tok".into()));
        let json = serde_json::to_value(&hello).unwrap();
        assert_eq!(json["t"], "hello");
        assert_eq!(json["proto"], PROTO_VERSION);
        assert_eq!(json["cols"], 80);
        assert_eq!(json["token"], "tok");
        assert!(json.get("session").is_none(), "없는 필드는 안 보낸다");
        // 능력을 광고하지 않으면 서버가 그 프레임을 아예 안 보낸다 — 기능이 조용히
        // 안 되고, 증상은 "그 패널엔 아무것도 없다"와 구분되지 않는다.
        assert_eq!(
            json["caps"],
            serde_json::json!(["blocks", "claude", "cwd", "plugin_surface", "plugin_screen"])
        );
    }

    #[test]
    fn hello_clamps_dimensions_like_the_server_does() {
        // 서버가 clamp_dim 으로 자르므로 클라도 같은 범위로 보낸다 — 서로 다른 값을
        // 기준 삼으면 레이아웃이 어긋난다.
        let tiny = Hello::new(1, 1);
        assert_eq!((tiny.cols, tiny.rows), (MIN_W, MIN_H));
        let huge = Hello::new(9999, 9999);
        assert_eq!((huge.cols, huge.rows), (MAX_W, MAX_H));
    }
}
