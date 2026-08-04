//! 사용자 설정 — **파이썬 클라와 같은 파일을 읽는다**(패리티 G5, 사용자 결정 2026-07-28).
//!
//! # 왜 같은 파일인가
//!
//! 두 클라를 오가는 사람이 설정을 두 번 적지 않게 하려는 것이다. 값: **파일 형식이 파이썬
//! 클라의 사정에 묶인다** — 형식이 바뀌면 여기도 같이 고쳐야 한다. 그래서 형식을 넓게 읽지
//! 않고 **아는 것만** 읽는다(모르는 줄은 조용히 넘긴다 — 파이썬 클라가 쓰는 지시어를
//! 우리가 모른다고 그 파일을 못 읽는 것이 되면 안 된다).
//!
//! # 탐색 순서 (파이썬 `keymap.load_config` 와 같다)
//!
//! `$PYTMUX_CONFIG` → `$PYTMUX_HOME/config` → `$XDG_CONFIG_HOME/pytmux/config`
//! → `~/.config/pytmux/config` → `~/.pytmux.conf`
//!
//! 순서가 같아야 하는 이유: 다르면 **같은 상자에서 두 클라가 다른 설정으로 돈다**. 그건
//! "설정을 공유한다"는 결정의 목적을 정확히 뒤집는다.
//!
//! # 지금 읽는 것
//!
//! `set prefix <키>` 하나다. 나머지 지시어는 아직 이 클라가 할 수 있는 일이 아니라서
//! 안 읽는다 — 읽어 놓고 안 쓰면 "설정했는데 안 먹는다"가 된다(패리티 표가 그 목록을
//! 센다). 늘어나는 것은 G5 의 다음 조각이다.

use std::path::PathBuf;

use crate::keymap::{Action, EnumOpt, ServerOpt};
use crate::keys::{Key, Mods};
use crate::screens::Prompt;

/// 사용자 설정.
#[derive(Debug, Clone, PartialEq)]
pub struct Config {
    /// prefix 키(기본 `Ctrl+B` — tmux·파이썬 클라와 같다).
    pub prefix: (Key, Mods),
    /// 비활성 패널을 한 톤 옅게 그릴까(기본 켜짐 — 파이썬과 같다).
    ///
    /// 외곽선 없이도 **어느 패널이 내 키를 받는지** 알게 하는 값이다. 꺼 두면 테두리
    /// 색만으로 구분해야 하는데, 그건 테마에 따라 거의 안 보일 수 있다.
    pub inactive_dim: bool,
    /// 흐리게 하는 세기(0.0~0.8). 파이썬 기본값 0.18 과 같다.
    pub inactive_dim_ratio: f32,
    /// 마우스를 쓸까(기본 켜짐). 끄면 클릭·드래그·휠이 **패널로 그대로** 간다.
    pub mouse: bool,
    /// 평드래그를 놓으면 복사할까(기본 켜짐).
    pub mouse_drag_copy: bool,
    /// 탭바를 **항상** 보일까(`always`), 탭이 둘 이상일 때만 보일까(`auto`).
    pub tab_bar_always: bool,
    /// 새 탭·분할이 시작할 자리(`current`/`home`/절대경로). 서버가 해석한다.
    pub default_path: String,
    /// 선택이 시작되기까지 움직여야 하는 칸 수(1~20, 파이썬 기본값 1).
    ///
    /// 손이 떨려 한 칸 밀린 클릭이 선택으로 읽히는 것을 막는 값이다.
    pub mouse_drag_threshold: u16,
    /// 모호폭(East Asian Width = `A`) 글자를 두 칸으로 볼까(`auto`/`narrow`/`wide`).
    ///
    /// 파이썬 기본값은 `auto` — 우리는 `auto` 를 `narrow` 로 본다(그쪽도 환경 판정이
    /// 실패하면 narrow 다).
    pub ambiguous_width: String,
    /// 스크롤 모드의 손버릇(`vi`/`emacs`). 파이썬 기본값은 `vi` 다.
    pub mode_keys: String,
    /// 사용자가 `bind` 로 건 키들(패리티 G8j).
    pub binds: Vec<Bind>,
    /// 붙여넣을 때 패널 테두리(박스드로잉) 글자를 뺄까(기본 켜짐).
    ///
    /// OS 네이티브 선택으로 긁으면 테두리 세로줄(`│`)이 같이 딸려 온다 — 그대로 붙이면
    /// 명령줄이 망가진다.
    pub strip_box_drawing: bool,
    /// 복사할 때 **앱이 접은 줄바꿈**을 되돌릴까(기본 켜짐 — 정본과 같다).
    ///
    /// 패널 안 프로그램은 자기 폭에 맞춰 문단을 접는다. 그 화면을 긁으면 접힌 자리마다
    /// 줄바꿈이 딸려 오는데, 붙여넣는 곳의 폭은 다르므로 그 줄바꿈은 뜻이 없다 —
    /// 오히려 문단을 조각낸다. 규칙은 `proto::unwrap`(정본 추출).
    pub copy_unwrap: bool,
    /// 스크롤 모드에서 **탭으로 쓰는 스크롤 UI** 를 띄울까(기본 켜짐 — 정본과 같다).
    ///
    /// # 왜 기본이 켜짐인가
    ///
    /// 이것을 필요로 하는 사람은 **휠이 안 오는 단말**을 쓰는 사람인데(iPhone Blink 등 —
    /// 스와이프를 단말이 자기 스크롤백으로 소비한다), 그 사람에게는 이 UI 가 스크롤백에
    /// 닿는 **유일한 길**이다. 켜져 있어야 발견되고, 필요 없으면 끄면 된다
    /// (`set touch-scroll off`). 라이브 화면은 안 건드린다 — 스크롤 모드에서만 그린다.
    pub touch_scroll: bool,
    /// 단말의 **대체 스크롤 모드**(DECSET 1007)를 끌까(기본 켜짐 = 끈다).
    ///
    /// # 왜 이 설정이 있나
    ///
    /// 일부 단말(iTerm2 · 일부 SSH 클라)은 대체 화면에서 **휠을 ↑/↓ 화살표로 바꿔**
    /// 보낸다. 그러면 우리는 진짜 휠 이벤트를 못 받고 화살표만 활성 패널로 새어,
    /// 증상은 "휠을 굴려도 스크롤백이 안 열린다"다. `ESC[?1007l` 로 그 모드를 끄면
    /// 단말이 SGR(1006) 휠 이벤트를 그대로 넘긴다.
    ///
    /// **TUI 만의 설정이다** — GUI 는 호스트 단말이 없고 winit 에서 진짜 휠을 받는다.
    /// 그래도 설정 파일은 두 클라가 공유하므로(G5 결정 3) 값은 양쪽 다 읽고 보인다.
    pub alt_scroll: bool,
    /// 창(또는 단말) 제목을 세션 상태로 갱신할까(파이썬 `set-titles`, 기본 꺼짐).
    ///
    /// 왜 기본이 꺼짐인가: 제목은 **바깥 것**이다. 탭 이름을 쓰는 단말 사용자에게 우리가
    /// 그 자리를 덮어쓰는 것은 놀라운 일이라, 파이썬도 옵트인으로 둔다.
    pub set_titles: bool,
    /// 그 제목의 **형식 문자열**(파이썬 `set-titles-string`, 기본 `#S:#I:#W`).
    ///
    /// 토큰은 상태줄과 같은 것을 쓴다(`proto::status`) — 두 자리가 다른 문법을 쓰면
    /// 사용자가 한쪽에서 배운 것을 다른 쪽에서 못 쓴다.
    pub set_titles_string: String,
    /// 상태줄 왼쪽에 그릴 **형식 문자열**(`#S`·`%H:%M` 같은 토큰 — `proto::status`).
    ///
    /// 파이썬 기본값과 같다(`" "` — 왼쪽은 비워 두고 오른쪽에 몰아 적는다).
    pub status_left: String,
    /// 상태줄 오른쪽. 파이썬 기본값과 같다.
    pub status_right: String,
    /// 상태줄 배경색 이름. 빈 값이면 **테마 그대로**(파이썬의 `None`).
    pub status_bg: String,
    /// 상태줄 글자색 이름. 빈 값이면 테마 그대로.
    pub status_fg: String,
    /// 상태줄을 어디에 붙일까 — `bottom`(기본) · `top`.
    ///
    /// 파이썬과 같은 기본값이다. 우리 탭바는 **늘 위**라 `top` 은 탭바 **위**를 뜻한다.
    pub status_position: String,
    /// 상태줄을 몇 초마다 다시 그릴까(1~60, 파이썬 기본값 15).
    ///
    /// 서버 메시지가 없어도 `%H:%M` 이 흘러야 하므로 이 주기가 필요하다.
    pub status_interval: u16,
    /// 사건이 생기면 자동으로 돌릴 명령들(`set-hook` · 패리티 G8u).
    ///
    /// 설정 파일의 `hook`/`set-hook` 줄을 그대로 담는다 — **모르는 이벤트 이름도**
    /// 버리지 않는다(플러그인이 자기 사건을 쓴다. `hooks` 모듈 문서 참조).
    pub hooks: crate::hooks::Hooks,
    /// UI 언어(`lang ko|en` · 별칭 `language`). 빈 값이면 "안 정했다" — 시동이
    /// 환경 변수로 넘어간다([`crate::i18n::resolve`]). 파이썬과 **같은 파일·같은
    /// 키**라 두 클라가 설정을 공유한다(G5 결정).
    pub lang: String,
    /// 앱 **전체** 글자 크기 배율([`FONT_SCALE_LO`]~[`FONT_SCALE_HI`], 기본 1.0).
    ///
    /// # 왜 캔버스만이 아닌가 (제보 2026-08-02 §10-21ⓐ)
    ///
    /// 제보가 "패널 캔버스만이 아니라 **앱 전체**(탭바·상태줄·오버레이 포함)"라고
    /// 못박았다. 그래서 뷰는 이 값을 **글자를 만드는 두 자리**(`text`·`ui_text`)에
    /// 곱하고, 캔버스 줄도 같은 배율을 탄다.
    ///
    /// # GUI 만의 설정이다
    ///
    /// 정본(TUI)의 글자 크기는 **호스트 단말**이 정한다 — 우리가 건드릴 자리가 없다.
    /// 그래도 설정 파일은 두 클라가 공유하므로(G5 결정 3) 파이썬 쪽 파서가 모르는
    /// 줄로 조용히 넘긴다(`keymap.load_config` 의 if/elif 는 모르는 옵션을 버린다 —
    /// 확인함). 이 갈림은 패리티 표에 **`iv`** 로 선언한다.
    pub font_scale: f32,
}

/// 글자 배율의 아래·위 끝과 한 걸음.
///
/// 끝을 두는 이유: 0 이하면 글자가 사라지고(창을 못 되돌린다), 너무 크면 격자가
/// 1×1 이 되어 서버에 그 크기를 보고한다 — 둘 다 **되돌릴 입구까지 같이 사라지는**
/// 자리다. 걸음이 0.1 인 것은 한 번 눌러 눈에 보이되 두세 번에 과하지 않은 폭이다.
pub const FONT_SCALE_LO: f32 = 0.5;
pub const FONT_SCALE_HI: f32 = 3.0;
pub const FONT_SCALE_STEP: f32 = 0.1;

/// 배율 한 걸음. **규칙의 주인은 core 다** — 뷰가 각자 더하면 두 자리에서 다르게
/// 잘리고(끝값·반올림), 그 어긋남은 설정 파일에 그대로 굳는다.
///
/// 끝에서는 **돌지 않고 멈춘다**. `Number` 설정 줄은 한 바퀴 도는데(키 하나로 조작하니까)
/// 이쪽은 키가 둘이라 돌 필요가 없고, 돌면 "한 번 더 키웠는데 갑자기 작아진다"가 된다.
#[must_use]
pub fn font_scale_step(now: f32, up: bool) -> f32 {
    let next = if up { now + FONT_SCALE_STEP } else { now - FONT_SCALE_STEP };
    // ★ 한 자리에서 접는다 — 값을 **격자 위에** 앉힌다.
    //
    // ⚠ 처음에는 "안 접으면 `1.0000001` 이 설정 파일에 적힌다"고 적었는데 **틀렸다**:
    //   파일은 `number_text` 가 `{:.2}` 로, 화면은 `{:.1}×` 로 적어서 둘 다 드리프트를
    //   가려 준다(실측: 열 걸음 뒤 `2.0000002`, 적히는 글자는 양쪽 다 `2.00`).
    //   접는 진짜 이유는 **가려 주는 것과 같은 값인 것은 다르다**는 것이다 — 배율은
    //   `==` 로 견주는 자리가 있고(끝에 닿았나 = 뷰가 "더 못 키운다"를 가르는 판정),
    //   격자를 벗어난 값은 그 판정을 조용히 흔든다. 오라클도 **정확 비교**로 잰다.
    let next = (next * 10.).round() / 10.;
    next.clamp(FONT_SCALE_LO, FONT_SCALE_HI)
}

impl Default for Config {
    fn default() -> Self {
        Self {
            prefix: (Key::Char('b'), Mods::CTRL),
            inactive_dim: true,
            inactive_dim_ratio: 0.18,
            mouse: true,
            mouse_drag_copy: true,
            tab_bar_always: true,
            default_path: "current".to_owned(),
            strip_box_drawing: true,
            copy_unwrap: true,
            touch_scroll: true,
            alt_scroll: true,
            set_titles: false,
            set_titles_string: "#S:#I:#W".to_owned(),
            binds: Vec::new(),
            mode_keys: "vi".to_owned(),
            status_left: " ".to_owned(),
            status_right: " #{pane_title}#h %H:%M %Y-%m-%d ".to_owned(),
            status_bg: String::new(),
            status_fg: String::new(),
            status_position: "bottom".to_owned(),
            status_interval: 15,
            mouse_drag_threshold: 1,
            ambiguous_width: "auto".to_owned(),
            hooks: crate::hooks::Hooks::default(),
            lang: String::new(),
            font_scale: 1.0,
        }
    }
}

/// 설정 **쓰기**를 다른 파일로 돌려놓는 자리(테스트 전용).
static WRITE_SINK: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();

/// 이 프로세스의 설정 쓰기를 `path` 로 돌린다. **테스트 전용**이고, 처음 한 번만 먹는다.
///
/// # 왜 필요했나 (2026-08-02 실측)
///
/// 액션 축 오라클(`gui` 의 `every_action_does_something_in_this_view`)은 **액션 전수**를
/// 뷰에 먹인다. 그 안에는 설정 파일을 고치는 것들이 섞여 있어(`ToggleInactiveDim` ·
/// `SetLang` · 새로 든 `FontScale`) `cargo test` 한 번이 **돌린 사람의 진짜 config 를
/// 고쳤다**. 토글은 티가 안 나지만 글자 배율은 다음 기동에 눈에 보인다.
///
/// # 왜 환경변수가 아닌가
///
/// `PYTMUX_CONFIG` 를 `set_var` 로 세우는 길은 이 저장소가 **금지**한 것이다 — 프로세스
/// 전역이라 형제 테스트와 경합한다(`config_tests.rs` 의 설정 축 측정이 그래서 파일을
/// 안 건드린다). `OnceLock` 은 먼저 부른 쪽이 이기고 그 뒤로 안 바뀌므로 경합이 없다.
///
/// 제품 경로에서는 아무도 안 부른다 → `get()` 이 늘 `None` 이라 동작이 종전과 같다.
pub fn redirect_writes(path: PathBuf) {
    let _ = WRITE_SINK.set(path);
}

impl Config {
    /// 설정 파일을 찾아 읽는다. 없으면 기본값이다(오류가 아니다).
    pub fn load() -> Self {
        match Self::path() {
            Some(path) => std::fs::read_to_string(path)
                .map(|text| Self::parse(&text))
                .unwrap_or_default(),
            None => Self::default(),
        }
    }

    /// 읽을 파일. 탐색 순서는 파이썬 클라와 같다.
    pub fn path() -> Option<PathBuf> {
        let mut candidates: Vec<PathBuf> = Vec::new();
        if let Some(explicit) = std::env::var_os("PYTMUX_CONFIG") {
            candidates.push(PathBuf::from(explicit));
        }
        if let Some(home) = std::env::var_os("PYTMUX_HOME") {
            candidates.push(PathBuf::from(home).join("config"));
        }
        if let Some(xdg) = std::env::var_os("XDG_CONFIG_HOME") {
            candidates.push(PathBuf::from(xdg).join("pytmux").join("config"));
        }
        if let Some(home) = home_dir() {
            candidates.push(home.join(".config").join("pytmux").join("config"));
            candidates.push(home.join(".pytmux.conf"));
        }
        candidates.into_iter().find(|p| p.is_file())
    }

    /// **쓸** 파일. 명시 경로가 없을 때 파이썬 `config_path_for_write` 와 같은 순서다.
    ///
    /// 읽기(`path`)와 다른 점: 하나도 없으면 `None` 이 아니라 **만들 자리**를 돌려준다
    /// (`$PYTMUX_HOME/config`, 없으면 XDG). 설정 화면에서 처음 값을 바꾸는 사람은
    /// 파일이 아직 없다 — 거기서 "파일이 없다"로 실패하면 설정을 영영 못 남긴다.
    pub fn path_for_write() -> PathBuf {
        // ★ 테스트가 세운 사물함이 **가장 먼저**다 — 아래를 보라(`redirect_writes`).
        if let Some(sink) = WRITE_SINK.get() {
            return sink.clone();
        }
        if let Some(explicit) = std::env::var_os("PYTMUX_CONFIG") {
            return PathBuf::from(explicit);
        }
        if let Some(existing) = Self::path() {
            return existing;
        }
        if let Some(home) = std::env::var_os("PYTMUX_HOME") {
            return PathBuf::from(home).join("config");
        }
        let xdg = std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .or_else(|| home_dir().map(|h| h.join(".config")))
            .unwrap_or_else(|| PathBuf::from("."));
        xdg.join("pytmux").join("config")
    }

    /// 설정 한 줄을 파일에 남긴다. 쓴 경로를 돌려준다.
    ///
    /// 임시 파일에 쓰고 옮긴다 — 도중에 죽어도 **반쯤 쓰인 config** 가 남지 않는다
    /// (반쯤 쓰인 파일은 다음 기동에 설정이 통째로 날아간 것처럼 보인다).
    pub fn write_option(key: &str, value: &str) -> std::io::Result<PathBuf> {
        let target = Self::path_for_write();
        let before = std::fs::read_to_string(&target).unwrap_or_default();
        let after = edit_option(&before, key, value);
        if let Some(parent) = target.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent)?;
        }
        // 원자 교체 — 임시 이름은 **프로세스마다 다르다**(L3: 세 클라가 같은 파일에
        // 쓴다. 같은 임시 이름을 나눠 쓰면 서로의 절반을 rename 할 수 있다).
        crate::atomicfile::write(&target, &after)?;
        Ok(target)
    }

    /// 파일 내용을 읽는다. **모르는 줄은 조용히 넘긴다.**
    pub fn parse(text: &str) -> Self {
        let mut config = Self::default();
        // ★ BOM 을 떼고 읽는다. 안 떼면 **첫 줄만** 조용히 사라진다 — `\u{feff}set` 은
        // `set` 이 아니라서 그 줄이 통째로 무시되고, 나머지 줄은 멀쩡히 먹으므로
        // 증상이 "이 설정 하나만 안 먹는다"가 된다.
        //
        // 가짜 위험이 아니다: Windows PowerShell 5.1 의 `Set-Content -Encoding utf8`·
        // `Out-File` 이 **기본으로 BOM 을 붙인다**. 2026-07-30 라이브에서 실제로 밟았다
        // (설정 파일을 그 명령으로 써 두고 "왜 안 먹지"를 한참 봤다). 이 저장소는 같은
        // 함정을 `.ps1` 쪽에서 이미 한 번 겪었다(docs/reports/README.md).
        let text = text.strip_prefix('\u{feff}').unwrap_or(text);
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let mut parts = line.split_whitespace();
            let Some(word) = parts.next() else { continue };
            // `bind <키> <명령>` · `bind -n <키> <명령>`(prefix 없이 바로).
            if word == "bind" {
                if let Some(bind) = Bind::parse(line) {
                    config.binds.push(bind);
                }
                continue;
            }
            // `hook <이벤트> <명령>` · `set-hook <이벤트> <명령>` — 파이썬 `keymap.py`
            // 가 받는 두 철자 그대로다. 값에 공백이 드므로 위 `set` 과 같은 규칙으로
            // **줄의 나머지 전부**를 명령으로 삼는다.
            if word == "hook" || word == "set-hook" {
                if let Some(rest) = line.split_once(char::is_whitespace).map(|(_, r)| r)
                    && let Some(crate::hooks::SetHook::Set { event, command }) =
                        crate::hooks::parse_set_hook(rest)
                {
                    config.hooks.set(&event, &command);
                }
                continue;
            }
            if word != "set" {
                continue;
            }
            let Some(option) = parts.next() else { continue };
            // ★ 값은 **줄의 나머지 전부**다(파이썬 `" ".join(parts[2:])` 와 같다).
            //
            // 첫 낱말만 받으면 공백이 든 값이 잘린다 — `status-right` 의 기본 형식
            // (`#{pane_title}#h %H:%M %Y-%m-%d`)이 `#{pane_title}#h` 로 잘려 시각이
            // 통째로 사라진다. 이 결함은 상태줄이 생기기 전까지 드러나지 않았다
            // (그전 옵션은 전부 한 낱말짜리였다).
            let value: String = parts.collect::<Vec<_>>().join(" ");
            if value.is_empty() {
                continue;
            }
            let value = value.as_str();
            // `bind` 는 `set` 이 아니다 — 위에서 걸러졌으므로 여기서 다시 본다.
            match normalize_opt(option).as_str() {
                "prefix" => {
                    if let Some(prefix) = parse_key(value) {
                        config.prefix = prefix;
                    }
                }
                "inactive-dim" => config.inactive_dim = on_off(value),
                "mouse" => config.mouse = on_off(value),
                "mouse-drag-copy" => config.mouse_drag_copy = on_off(value),
                // 파이썬은 `always`/`auto` 두 값이다. 모르는 값이면 기본값 그대로 —
                // 오타 하나 때문에 탭바가 사라지면 무엇이 잘못됐는지 알 수 없다.
                "tab-bar" => match value {
                    "always" => config.tab_bar_always = true,
                    "auto" => config.tab_bar_always = false,
                    _ => {}
                },
                "default-path" => config.default_path = value.to_owned(),
                // 형식 문자열은 **그대로 받는다** — 무엇이 유효한 토큰인지는 펼치는
                // 쪽(`proto::status`)이 알고, 모르는 `#x` 는 글자 그대로 남는다.
                "status-left" => config.status_left = value.to_owned(),
                "status-right" => config.status_right = value.to_owned(),
                "status-bg" => config.status_bg = value.to_owned(),
                "status-fg" => config.status_fg = value.to_owned(),
                "status-position" => {
                    if value == "top" || value == "bottom" {
                        config.status_position = value.to_owned();
                    }
                }
                "status-interval" => {
                    if let Ok(n) = value.parse::<u16>() {
                        config.status_interval = n.clamp(1, 60);
                    }
                }
                "strip-box-drawing" => config.strip_box_drawing = on_off(value),
                "copy-unwrap" => config.copy_unwrap = on_off(value),
                "touch-scroll" => config.touch_scroll = on_off(value),
                "alt-scroll" => config.alt_scroll = on_off(value),
                "set-titles" => config.set_titles = on_off(value),
                // 빈 값도 뜻이 있다("제목을 비운다") — 되돌리기로 바꾸지 않는다.
                "set-titles-string" => config.set_titles_string = value.to_owned(),
                // 모르는 값이면 기본값 그대로 — 오타 하나로 스크롤 키가 사라지면
                // 무엇이 잘못됐는지 알 수 없다.
                "mouse-drag-threshold" => {
                    if let Ok(n) = value.parse::<u16>() {
                        config.mouse_drag_threshold = n.clamp(1, 20);
                    }
                }
                "ambiguous-width" => {
                    if matches!(value, "auto" | "narrow" | "wide") {
                        config.ambiguous_width = value.to_owned();
                    }
                }
                "mode-keys" => {
                    if value == "vi" || value == "emacs" {
                        config.mode_keys = value.to_owned();
                    }
                }
                // 두 철자를 받는 것은 `OPT_ALIASES` 와 같은 이유다 — 파이썬 클라가
                // 쓴 파일에 어느 쪽이 적혀 있을지 모른다. 모르는 값은 기본(빈 값 =
                // 환경 따라가기) 그대로 — 오타로 UI 언어가 굳으면 원인을 못 찾는다.
                "lang" | "language" => {
                    let lower = value.to_ascii_lowercase();
                    if crate::i18n::LOCALES.contains(&lower.as_str()) {
                        config.lang = lower;
                    }
                }
                // 범위를 벗어난 값은 **자른다**(파이썬과 같다) — 0.9 를 적었다고 설정
                // 파일을 통째로 못 읽는 것이 되면 안 된다.
                "inactive-dim-ratio" => {
                    if let Ok(ratio) = value.parse::<f32>() {
                        config.inactive_dim_ratio = ratio.clamp(0.0, 0.8);
                    }
                }
                "font-scale" => {
                    if let Ok(scale) = value.parse::<f32>() {
                        config.font_scale = scale.clamp(FONT_SCALE_LO, FONT_SCALE_HI);
                    }
                }
                _ => {}
            }
        }
        config
    }
}

/// 설정 화면의 한 줄.
///
/// 파이썬 `clientutil.SETTINGS` 와 같은 모양(키·카테고리·적용 방법)이지만 **줄 수가 다르다**
/// — 저쪽은 34줄이고 여기는 지금 5줄이다. 못 바꾸는 것을 줄로 실으면 골랐을 때 아무 일도
/// 안 일어나고, 그건 "설정이 있는데 안 먹는다"로 읽힌다. 못 하는 것의 목록은 화면이 아니라
/// **패리티 표**가 센다(팔레트에서 이미 내린 같은 결정).
#[derive(Debug, Clone, Copy)]
pub struct Setting {
    /// 정규(하이픈) 옵션명 — 화면에 적히는 이름이자 설정 파일에 쓰는 키다.
    pub key: &'static str,
    /// 화면 묶음. 파이썬의 `SETTINGS_CATS` 와 같은 이름을 쓴다.
    pub cat: &'static str,
    pub kind: SettingKind,
}

/// 그 줄을 어떻게 바꾸나.
#[derive(Debug, Clone, Copy)]
pub enum SettingKind {
    /// 서버가 쥔 켜기/끄기 — 액션 하나로 뒤집는다. **영속은 서버가 한다**(opts.json).
    Toggle(Action),
    /// 한 줄 입력으로 받는 값. 받은 값은 **우리가 설정 파일에 쓴다**.
    Text(Prompt),
    /// **설정 파일**이 쥔, 값이 정해진 옵션 — `Enter` 가 다음 값으로 넘긴다.
    ConfigEnum(&'static [&'static str]),
    /// 설정 파일이 쥔 숫자 — `Enter` 가 `step` 만큼 올리고 `hi` 를 넘으면 `lo` 로 돈다.
    ///
    /// 올리기만 하는 이유: 화면에 `◀ ▶` 를 그리려면 좌우 키가 필요한데, 그건 이 화면의
    /// 다른 줄(패널 이동)과 겹친다. 한 바퀴 도는 편이 키 하나로 끝난다.
    Number { lo: f32, hi: f32, step: f32 },
    /// 값이 없고 **다른 화면을 여는** 줄(파이썬의 `link` 타입).
    Link(Action),
    /// 값이 셋 중 하나인 서버 옵션 — `Enter` 가 **다음 값으로** 넘긴다.
    ///
    /// 목록을 따로 안 띄우는 이유: 값이 둘·셋뿐이라 화면을 하나 더 세우는 것보다
    /// 그 자리에서 넘기는 편이 손이 덜 간다(파이썬 설정 화면도 `◀ ▶` 로 넘긴다).
    Enum(EnumOpt),
    /// **설정 파일**이 쥔 켜기/끄기. 우리가 뒤집고, 쓰고, 이번 판에 바로 적용한다.
    ///
    /// [`SettingKind::Toggle`] 과 갈리는 점: 저쪽은 서버가 값의 주인이라 명령만 보내고
    /// 화면은 서버 회신이 확정한다. 이쪽은 **우리가 주인**이다.
    ConfigToggle,
    /// UI 언어 줄. 값의 주인이 [`crate::i18n`](런타임 로케일 + `.lang` 영속)이라
    /// 다른 어느 kind 와도 다르다 — `Enter` 가 다음 로케일로 넘긴다(둘뿐이라 토글).
    Lang,
}

/// 화면에 지금 값을 적으려면 필요한 것들. 값의 출처가 둘(서버 상태·설정)이라 모아 받는다.
///
/// 서버 플래그를 `bool` 로 받는 이유: 그 타입(`StatusFlags`)은 proto 에 있고 이 크레이트는
/// proto 를 모른다(의존 0개 계약). 뷰가 풀어서 넘긴다.
/// `Default` 를 두는 이유: 설정 줄이 늘 때마다 **테스트의 리터럴 여섯 곳**이 같이 깨지는
/// 것을 막는다. 값이 뜻을 갖는 자리는 테스트가 그 필드만 적는다(`..Default::default()`).
#[derive(Debug, Clone)]
pub struct SettingValues {
    /// 비활성 패널 딤(설정 파일).
    pub inactive_dim: bool,
    /// 패널 테두리에 제목을 항상 보일까(서버 옵션).
    pub border_status: bool,
    pub single_border: bool,
    pub coalesce_repaints: bool,
    pub nest_auto_attach: bool,
    pub win_mouse_motion: bool,
    /// exit-empty 현재값(서버 옵션). 구버전 서버는 안 보낸다 — `None` 이면 `?` 다.
    pub exit_empty: Option<bool>,
    /// VT 파서 백엔드(서버 옵션). 서버가 `status` 로 알려 준다.
    pub vt_parser: String,
    /// 공유 크기 규칙(서버 옵션).
    pub window_size: String,
    pub mouse: bool,
    pub mouse_drag_copy: bool,
    pub tab_bar_always: bool,
    pub default_path: String,
    pub strip_box_drawing: bool,
    /// 복사할 때 접힌 줄을 펼까(설정 파일).
    pub copy_unwrap: bool,
    /// 탭으로 쓰는 스크롤 UI 를 띄울까(설정 파일).
    pub touch_scroll: bool,
    pub alt_scroll: bool,
    /// 창(또는 단말) 제목을 세션 상태로 갱신할까(파이썬 `set-titles`, 기본 꺼짐).
    ///
    /// 왜 기본이 꺼짐인가: 제목은 **바깥 것**이다. 탭 이름을 쓰는 단말 사용자에게 우리가
    /// 그 자리를 덮어쓰는 것은 놀라운 일이라, 파이썬도 옵트인으로 둔다.
    pub set_titles: bool,
    /// 그 제목의 **형식 문자열**(파이썬 `set-titles-string`, 기본 `#S:#I:#W`).
    ///
    /// 토큰은 상태줄과 같은 것을 쓴다(`proto::status`) — 두 자리가 다른 문법을 쓰면
    /// 사용자가 한쪽에서 배운 것을 다른 쪽에서 못 쓴다.
    pub set_titles_string: String,
    pub inactive_dim_ratio: f32,
    /// 앱 전체 글자 배율(설정 파일 — GUI 만의 줄, §10-21ⓐ).
    pub font_scale: f32,
    pub mode_keys: String,
    pub mouse_drag_threshold: u16,
    pub ambiguous_width: String,
    pub status_left: String,
    pub status_right: String,
    pub status_bg: String,
    pub status_fg: String,
    pub status_position: String,
    pub status_interval: u16,
    pub sync: bool,
    pub monitor_activity: bool,
    pub monitor_bell: bool,
    pub auto_rename: bool,
    pub prefix: (Key, Mods),
}

impl Default for SettingValues {
    fn default() -> Self {
        Self {
            inactive_dim: false,
            border_status: false,
            single_border: false,
            coalesce_repaints: false,
            nest_auto_attach: false,
            win_mouse_motion: false,
            exit_empty: None,
            vt_parser: String::new(),
            window_size: String::new(),
            mouse: false,
            mouse_drag_copy: false,
            tab_bar_always: false,
            default_path: String::new(),
            strip_box_drawing: false,
            copy_unwrap: false,
            touch_scroll: false,
            alt_scroll: false,
            set_titles: false,
            set_titles_string: String::new(),
            inactive_dim_ratio: 0.0,
            font_scale: 1.0,
            mode_keys: String::new(),
            mouse_drag_threshold: 1,
            ambiguous_width: String::new(),
            status_left: String::new(),
            status_right: String::new(),
            status_bg: String::new(),
            status_fg: String::new(),
            status_position: String::new(),
            status_interval: 15,
            sync: false,
            monitor_activity: false,
            monitor_bell: false,
            auto_rename: false,
            // `Key` 에는 `Default` 가 없다(어느 글자가 기본인지는 키 표가 정할 일이
            // 아니다). 여기서는 이 클라의 기본 prefix 를 못박는다.
            prefix: (Key::Char('b'), Mods::CTRL),
        }
    }
}

/// 설정 화면이 순회하는 목록. **설정 추가 = 여기 한 줄**(파이썬과 같은 구조).
///
/// **카테고리 순서로 늘어놓는다**(표시 → 입력 → 동작 → 상태줄 → 고급 → 키, 파이썬
/// `SETTINGS_CATS` 와 같다). 화면은 `cat` 이 바뀔 때 머리줄을 찍으므로, 섞여 있으면
/// 같은 카테고리 머리줄이 여러 번 나온다(2026-07-29 라이브 스크린샷이 그렇게 잡혔다).
pub const SETTINGS: &[Setting] = &[
    Setting {
        key: "inactive-dim",
        cat: "표시",
        kind: SettingKind::ConfigToggle,
    },
    Setting {
        key: "inactive-dim-ratio",
        cat: "표시",
        kind: SettingKind::Number { lo: 0.0, hi: 0.8, step: 0.02 },
    },
    // ★ **GUI 만의 줄이다**(§10-21ⓐ) — 정본의 글자 크기는 호스트 단말이 정한다.
    //   설정 파일은 공유하지만 파이썬 파서가 모르는 옵션을 조용히 넘기므로 안전하다.
    Setting {
        key: "font-scale",
        cat: "표시",
        kind: SettingKind::Number {
            lo: FONT_SCALE_LO,
            hi: FONT_SCALE_HI,
            step: FONT_SCALE_STEP,
        },
    },
    Setting {
        key: "tab-bar",
        cat: "표시",
        kind: SettingKind::ConfigEnum(&["always", "auto"]),
    },
    Setting {
        key: "status-position",
        cat: "표시",
        kind: SettingKind::ConfigEnum(&["bottom", "top"]),
    },
    Setting {
        key: "single-border",
        cat: "표시",
        kind: SettingKind::Toggle(Action::ToggleServerOption(ServerOpt::SingleBorder)),
    },
    Setting {
        key: "pane-border-status",
        cat: "표시",
        kind: SettingKind::Toggle(Action::ToggleBorderStatus),
    },
    // 파이썬 `SETTINGS` 의 `{"key": "language", "cat": "표시", "backend": "lang"}` 과
    // 같은 자리다. `ConfigEnum` 이 아닌 이유: 값의 주인이 설정 파일이 아니라
    // **런타임 로케일 + `.lang` 영속**이다(`i18n` 모듈 문서 — 영속이 설정 파일을 이긴다).
    Setting {
        key: "language",
        cat: "표시",
        kind: SettingKind::Lang,
    },
    Setting {
        key: "mouse",
        cat: "입력",
        kind: SettingKind::ConfigToggle,
    },
    Setting {
        key: "mouse-drag-copy",
        cat: "입력",
        kind: SettingKind::ConfigToggle,
    },
    Setting {
        key: "mouse-drag-threshold",
        cat: "입력",
        kind: SettingKind::Number { lo: 1.0, hi: 20.0, step: 1.0 },
    },
    Setting {
        key: "copy-unwrap",
        cat: "입력",
        kind: SettingKind::ConfigToggle,
    },
    Setting {
        key: "touch-scroll",
        cat: "입력",
        kind: SettingKind::ConfigToggle,
    },
    Setting {
        key: "mode-keys",
        cat: "입력",
        kind: SettingKind::ConfigEnum(&["vi", "emacs"]),
    },
    // 파이썬 `SETTINGS` 와 같은 범주·같은 이름이다(`{"key": "alt-scroll", "cat": "입력"}`).
    Setting {
        key: "alt-scroll",
        cat: "입력",
        kind: SettingKind::ConfigToggle,
    },
    Setting {
        key: "ambiguous-width",
        cat: "입력",
        kind: SettingKind::ConfigEnum(&["auto", "narrow", "wide"]),
    },
    Setting {
        key: "prefix",
        cat: "입력",
        kind: SettingKind::Text(Prompt::SetPrefix),
    },
    Setting {
        key: "strip-box-drawing",
        cat: "입력",
        kind: SettingKind::ConfigToggle,
    },
    Setting {
        key: "default-path",
        cat: "동작",
        kind: SettingKind::Text(Prompt::DefaultPath),
    },
    // 파이썬 `SETTINGS` 의 `{"key": "set-titles", "cat": "동작"}` 과 같은 자리다.
    Setting {
        key: "set-titles",
        cat: "동작",
        kind: SettingKind::ConfigToggle,
    },
    Setting {
        key: "status-interval",
        cat: "동작",
        kind: SettingKind::Number { lo: 1.0, hi: 60.0, step: 1.0 },
    },
    Setting {
        key: "automatic-rename",
        cat: "동작",
        kind: SettingKind::Toggle(Action::ToggleAutoRename),
    },
    Setting {
        key: "monitor-activity",
        cat: "동작",
        kind: SettingKind::Toggle(Action::ToggleMonitorActivity),
    },
    Setting {
        key: "synchronize-panes",
        cat: "동작",
        kind: SettingKind::Toggle(Action::ToggleSync),
    },
    Setting {
        key: "coalesce-repaints",
        cat: "동작",
        kind: SettingKind::Toggle(Action::ToggleServerOption(ServerOpt::CoalesceRepaints)),
    },
    Setting {
        key: "nest-auto-attach",
        cat: "동작",
        kind: SettingKind::Toggle(Action::ToggleServerOption(ServerOpt::NestAutoAttach)),
    },
    Setting {
        key: "exit-empty",
        cat: "동작",
        kind: SettingKind::Toggle(Action::ToggleServerOption(ServerOpt::ExitEmpty)),
    },
    Setting {
        key: "win-mouse-motion",
        cat: "동작",
        kind: SettingKind::Toggle(Action::ToggleServerOption(ServerOpt::WinMouseMotion)),
    },
    Setting {
        key: "vt-parser",
        cat: "동작",
        kind: SettingKind::Enum(EnumOpt::VtParser),
    },
    Setting {
        key: "window-size",
        cat: "동작",
        kind: SettingKind::Enum(EnumOpt::WindowSize),
    },
    // 상태줄 — 파이썬 `SETTINGS` 의 「상태줄」 묶음과 같은 넷이다.
    Setting {
        key: "status-left",
        cat: "상태줄",
        kind: SettingKind::Text(Prompt::StatusLeft),
    },
    Setting {
        key: "status-right",
        cat: "상태줄",
        kind: SettingKind::Text(Prompt::StatusRight),
    },
    Setting {
        key: "status-bg",
        cat: "상태줄",
        kind: SettingKind::Text(Prompt::StatusBg),
    },
    Setting {
        key: "status-fg",
        cat: "상태줄",
        kind: SettingKind::Text(Prompt::StatusFg),
    },
    Setting {
        key: "plugins",
        cat: "고급",
        kind: SettingKind::Link(Action::ShowPlugins),
    },
    Setting {
        key: "list-keys",
        cat: "고급",
        kind: SettingKind::Link(Action::ShowKeys),
    },
];

/// 설정 화면 **왼쪽 세로 탭**의 순서 — 파이썬 `clientutil.SETTINGS_CATS` 에서 우리가
/// 실제로 가진 카테고리만 남긴 것이다(차례는 저쪽 그대로).
///
/// 왜 `SETTINGS` 를 훑어 만들지 않나: 그러면 표의 줄 차례가 곧 탭 차례가 되어, 설정 한 줄을
/// 옮기는 것만으로 **탭이 재배열된다**. 정본에서 탭 순서는 표와 따로 선언된 것이라 여기서도
/// 따로 못박는다(`category_conformance.rs` 가 정본 순서의 부분수열임을 강제).
///
/// 정본에 있고 우리에겐 없는 둘: `Claude`(플러그인 기여 — 우리는 아직 그 설정을 안 싣는다) ·
/// `키`(정본은 런타임에 짓는 읽기 전용 레퍼런스 — 우리는 `고급`의 `list-keys` 링크가 그 자리다).
pub static SETTINGS_CATS: &[&str] = &["표시", "입력", "동작", "상태줄", "고급"];

/// 그 카테고리에 속한 설정들의 **표 안 자리**(`SETTINGS` 의 번호).
///
/// 번호를 돌려주는 이유는 팔레트 필터와 같다 — 화면은 묶어서 그리지만 고른 것을 실행하려면
/// 원래 표의 자리가 필요하다. 걸러진 목록을 따로 들고 다니면 그 둘이 어긋난다.
pub fn settings_in_cat(cat: &str) -> Vec<usize> {
    SETTINGS
        .iter()
        .enumerate()
        .filter(|(_, s)| s.cat == cat)
        .map(|(i, _)| i)
        .collect()
}

/// `row` 번째 설정이 속한 카테고리의 [`SETTINGS_CATS`] 안 번호(↑↓ 로 줄을 옮길 때 왼쪽
/// 탭이 따라오게 하는 값). 모르는 카테고리면 `None`.
pub fn settings_cat_of(row: usize) -> Option<usize> {
    let cat = SETTINGS.get(row)?.cat;
    SETTINGS_CATS.iter().position(|c| *c == cat)
}

/// 그 카테고리의 **첫 줄** 자리 — 왼쪽 탭을 클릭했을 때 뛸 곳(정본 `_cat_first`).
pub fn settings_cat_first(cat: &str) -> Option<usize> {
    SETTINGS.iter().position(|s| s.cat == cat)
}

/// `Tab`/`Shift+Tab` 으로 카테고리를 넘길 때 갈 줄(정본 설정 화면의 `Tab` 동선).
///
/// 끝에서 처음으로 **돈다** — 탭이 다섯뿐이라 막다른 끝을 두면 "Tab 이 안 먹는다"로
/// 읽힌다(탭 순환은 파이썬도 같다).
pub fn settings_cat_step(row: usize, forward: bool) -> usize {
    if SETTINGS_CATS.is_empty() {
        return row;
    }
    let now = settings_cat_of(row).unwrap_or(0);
    let len = SETTINGS_CATS.len();
    let next = if forward { (now + 1) % len } else { (now + len - 1) % len };
    settings_cat_first(SETTINGS_CATS[next]).unwrap_or(row)
}

/// 사이드바 탭의 **화면 이름**(정본 `setcat.<cat>` 의 ko 값).
///
/// ★ 분류 이름과 화면 이름이 **다른 자리가 있다**: 정본은 `입력` 탭을 `입력/키` 로,
/// `고급` 탭을 `고급/플러그인` 으로 적는다(그 탭에 무엇이 들었는지를 이름이 말한다).
/// 종전 우리는 분류 이름을 그대로 적었고, `t()` 가 ko 에서 항등이라 **한국어에서만**
/// 틀렸다 — en 은 `en_core.rs` 에 `Input/Keys`·`Advanced/Plugins` 가 있어 맞았다.
/// 게이트가 en 만 재서 셋을 다 통과했다(3차 대조 2026-08-01 에서 눈으로 걸렸다).
pub static SETTINGS_CAT_LABELS: &[(&str, &str)] = &[
    ("표시", "표시"),
    ("입력", "입력/키"),
    ("동작", "동작"),
    ("상태줄", "상태줄"),
    ("고급", "고급/플러그인"),
];

/// 왼쪽 탭에 적을 이름(로케일 적용). 파이썬 `setcat.<cat>` 과 같은 문맥 키를 쓴다 —
/// "동작"·"입력"은 다른 자리에서도 쓰이는 낱말이라 평문 키로 두면 남의 번역과 부딪힌다.
/// 플러그인이 낸 분류(`Claude`)도 여기로 온다 — 그래서 `&'static` 이 아니라 **빌린**
/// 이름을 돌려준다(런타임 값이라 정적 표에 없다).
pub fn settings_cat_label<'a>(cat: &'a str) -> &'a str {
    match SETTINGS_CAT_LABELS.iter().find(|(c, _)| *c == cat) {
        Some((_, ko)) => crate::i18n::tc("setcat", ko),
        // 표에 없는 분류는 이름 그대로다(정본 `t(..., default=cat)` 와 같은 degrade).
        None => crate::i18n::tc("setcat", cat),
    }
}

/// 설정 줄의 **사람 말 이름**(정본 `setting.<key>` 카탈로그와 같은 낱말).
///
/// 왜 옵션 키를 그대로 안 쓰나: 설정은 **이름을 모르는 사람**이 여는 화면이다.
/// `inactive-dim` 을 읽고 무엇인지 아는 사람은 이미 `set-option` 을 칠 줄 안다.
///
/// 표는 `scripts/gen_setting_labels.py` 가 정본에서 뜬 것과 **글자까지 같다**
/// (`setting_label_conformance.rs` 가 그것을 잰다). 셋만 우리가 지었다 —
/// `ambiguous-width`·`win-mouse-motion`·`window-size` 는 정본에 그 줄이 없다.
pub static SETTING_LABELS: &[(&str, &str)] = &[
    ("inactive-dim", "비활성 패널 흐리게"),
    ("pane-border-status", "패널 헤더 표시"),
    ("single-border", "단일 패널 테두리"),
    ("tab-bar", "탭 바 표시"),
    ("inactive-dim-ratio", "흐리게 세기"),
    // 정본에 이 줄이 없다(GUI 만의 설정) → `NO_CANON_LABEL` 에 사유와 함께 적어 뒀다.
    ("font-scale", "글자 크기 배율"),
    ("status-position", "상태줄 위치"),
    ("language", "언어"),
    ("prefix", "prefix 키"),
    ("mouse", "마우스"),
    ("mouse-drag-copy", "드래그 복사(외곽선 없이)"),
    ("mouse-drag-threshold", "드래그 인정 최소 이동(칸)"),
    ("ambiguous-width", "모호폭 문자 처리"),
    ("copy-unwrap", "복사 시 접힌 줄 펴기"),
    ("touch-scroll", "탭으로 쓰는 스크롤 UI"),
    ("mode-keys", "복사 모드 키"),
    ("strip-box-drawing", "붙여넣기 테두리 제거"),
    ("alt-scroll", "휠 스크롤백(1007)"),
    ("set-titles", "터미널 제목 설정"),
    ("coalesce-repaints", "리페인트 합치기"),
    ("nest-auto-attach", "중첩 자동 승격"),
    ("win-mouse-motion", "Windows 마우스 이동 추적"),
    ("exit-empty", "세션 0개 시 종료"),
    ("default-path", "새 패널 시작 경로"),
    ("status-interval", "상태줄 갱신 주기(초)"),
    ("vt-parser", "VT 파서 백엔드"),
    ("window-size", "창 크기 규칙"),
    ("synchronize-panes", "입력 동기화"),
    ("monitor-activity", "활동 모니터"),
    ("automatic-rename", "탭 자동 이름"),
    ("status-left", "상태줄 왼쪽 포맷"),
    ("status-right", "상태줄 오른쪽 포맷"),
    ("status-bg", "상태줄 배경색"),
    ("status-fg", "상태줄 글자색"),
    ("list-keys", "키 바인딩 목록…"),
    ("plugins", "플러그인 관리…"),
    // ── 플러그인이 낸 설정 줄의 이름(설계 Tier A · P2) ──────────────────────
    // 이 넷은 코어 `i18n.py` 가 아니라 claude-code 플러그인이 `i18n.register` 로 넣는다
    // (생성기가 그래서 `plugins.load()` 를 부른다 — `gen_setting_labels.py` 머리말).
    // 줄 자체는 서버가 런타임에 부는 것이고(`plugin_surface.settings`), 여기 있는 것은
    // **그 줄의 사람 말**뿐이다. 표에 없는 키는 키 그대로 보인다(`setting_label`).
    ("claude-settings", "Claude 설정…"),
    ("model", "Claude 모델/컨텍스트…"),
    ("claude-rules", "Claude 시작 규칙…"),
    ("claude-token-log", "Claude 토큰 사용량…"),
];

/// 설정 **값**의 사람 말(정본 `setval.<값>`). 표에 없는 값은 원값 그대로 보인다 —
/// `vi`·`pyte`·`native` 처럼 기술적인 값은 정본도 안 옮긴다(옮기면 오히려 못 찾는다).
pub static SETTING_VALUE_LABELS: &[(&str, &str)] = &[
    ("always", "항상"),
    ("auto", "자동"),
    ("bottom", "아래"),
    ("en", "English"),
    ("ko", "한국어"),
    ("off", "꺼짐"),
    ("on", "켜짐"),
    ("top", "위"),
];

/// `key` 줄의 화면 이름. 표에 없으면 키 그대로(정본 `t(..., default=key)` 와 같다).
///
/// 문맥 키(`tc`)를 쓰는 이유: `언어`·`마우스` 같은 짧은 낱말은 다른 화면에도 있어
/// 평문 키로 두면 남의 번역과 부딪힌다(정본이 `setting.` 네임스페이스를 쓰는 이유).
pub fn setting_label<'a>(key: &'a str) -> &'a str {
    match SETTING_LABELS.iter().find(|(k, _)| *k == key) {
        Some((_, ko)) => crate::i18n::tc("setting", ko),
        // 표에 없는 키는 **키 그대로** 보인다. 종전에는 `?` 로 떨어질 수 있었는데, 그건
        // 아무것도 안 알려 주면서 화면만 망가뜨린다 — 플러그인이 낸 새 설정이 그 자리다
        // (이름을 모르는 것은 낱말 표가 낡은 것이지 그 줄이 없는 것이 아니다).
        None => key,
    }
}

/// 값 하나의 화면 낱말(정본 `_vlabel`).
pub fn setting_value_label(value: &str) -> String {
    match SETTING_VALUE_LABELS.iter().find(|(v, _)| *v == value) {
        Some((_, ko)) => crate::i18n::tc("setval", ko).to_string(),
        None => value.to_string(),
    }
}

/// 한 설정 줄의 값을 **화면에 어떻게 펼칠까**(정본 `_val_display`).
///
/// 왜 문자열 하나로 안 두나: 종전 우리 화면은 지금 값만 적었다(`off`). 그러면 그 줄이
/// **무엇을 받는지**는 눌러 봐야 안다 — `tab-bar` 가 on/off 인지 always/auto 인지,
/// `window-size` 에 무엇이 있는지 화면 어디에도 없었다. 정본은 고를 수 있는 것을 전부
/// 늘어놓고 지금 값만 강조한다.
///
/// 뷰가 문자열을 받는 대신 이 형을 받는 이유는 늘 같다 — **강조는 색과 반전**이라
/// 글자에 안 담긴다. 담아서 넘기면 두 뷰가 각자 파싱하게 된다.
#[derive(Debug, Clone, PartialEq)]
pub enum ValueDisplay {
    /// 고를 수 있는 것 전부. `cur` 는 지금 값의 자리 — **모르면 `None`**(서버가 아직
    /// 안 알려 준 토글). 모르는 것을 아는 척하면 사용자가 그 값을 믿고 판단한다.
    Choices { labels: Vec<String>, cur: Option<usize> },
    /// 숫자 — 정본은 `‹ 0.20 ›` 처럼 양쪽 화살괄호로 "좌우로 바뀐다"를 알린다.
    Stepper(String),
    /// 자유 입력. `unset` 이면 아직 안 정한 것이라 화면에서 흐리게 적는다.
    Text { shown: String, unset: bool },
    /// 값이 없고 다른 화면을 여는 줄.
    Link(&'static str),
}

impl Setting {
    /// 화면에 적을 지금 값(파이썬 `setting_current` 와 같은 정규형 — `on`/`off`·`C-a`).
    pub fn value(&self, values: &SettingValues) -> String {
        // 서버가 아직 안 알려 준 값은 `?` — '모르는 것을 안다고 하면' 사용자가 그 값을
        // 믿고 판단한다.
        let text = self.value_inner(values);
        if text.is_empty() { "?".to_string() } else { text }
    }

    /// 고를 수 있는 값들(정규 철자). 고르는 줄이 아니면 `None`.
    ///
    /// `Toggle`/`ConfigToggle` 이 `on`/`off` 인 것은 정본 `_val_display` 의
    /// `["on", "off"] if t == "bool"` 과 같은 자리다.
    pub fn choices(&self) -> Option<&'static [&'static str]> {
        match self.kind {
            SettingKind::Toggle(_) | SettingKind::ConfigToggle => Some(&["on", "off"]),
            SettingKind::ConfigEnum(values) => Some(values),
            SettingKind::Enum(opt) => Some(opt.choices()),
            // 값의 주인은 런타임 로케일이지만, 고르는 줄이라는 점은 같다.
            SettingKind::Lang => Some(&["ko", "en"]),
            SettingKind::Number { .. } | SettingKind::Text(_) | SettingKind::Link(_) => None,
        }
    }

    /// 이 줄을 화면에 어떻게 펼칠까(정본 `_val_display`).
    pub fn display(&self, values: &SettingValues) -> ValueDisplay {
        let now = self.value_inner(values);
        if let Some(choices) = self.choices() {
            return ValueDisplay::Choices {
                labels: choices.iter().map(|c| setting_value_label(c)).collect(),
                cur: choices.iter().position(|c| *c == now),
            };
        }
        match self.kind {
            SettingKind::Link(_) => ValueDisplay::Link(crate::i18n::tc("setting", "열기")),
            SettingKind::Number { .. } => ValueDisplay::Stepper(if now.is_empty() {
                crate::i18n::tc("setting", "미상(서버)").to_string()
            } else {
                now
            }),
            _ => ValueDisplay::Text {
                unset: now.trim().is_empty(),
                shown: if now.trim().is_empty() {
                    format!("({})", crate::i18n::tc("setting", "미설정"))
                } else {
                    now
                },
            },
        }
    }

    fn value_inner(&self, values: &SettingValues) -> String {
        let on = |b: bool| if b { "on" } else { "off" }.to_string();
        match self.key {
            "prefix" => key_to_tmux(values.prefix),
            "inactive-dim" => on(values.inactive_dim),
            "pane-border-status" => on(values.border_status),
            "single-border" => on(values.single_border),
            "coalesce-repaints" => on(values.coalesce_repaints),
            "nest-auto-attach" => on(values.nest_auto_attach),
            "win-mouse-motion" => on(values.win_mouse_motion),
            // 서버가 status 에 싣는다(2026-07-30 서버 CL — 그 전 서버는 안 보내니
            // `None` → `?`. 파이썬 클라도 같은 날 같은 칸을 읽기 시작했다).
            "exit-empty" => match values.exit_empty {
                Some(b) => on(b),
                None => String::new(),
            },
            // 서버가 `status` 로 알려 주는 값 그대로. 아직 못 받았으면 빈 문자열이라
            // 아래에서 `?` 가 된다.
            "mouse" => on(values.mouse),
            "mouse-drag-copy" => on(values.mouse_drag_copy),
            "tab-bar" => if values.tab_bar_always { "always" } else { "auto" }.to_string(),
            "default-path" => values.default_path.clone(),
            "strip-box-drawing" => on(values.strip_box_drawing),
            "copy-unwrap" => on(values.copy_unwrap),
            "touch-scroll" => on(values.touch_scroll),
            "alt-scroll" => on(values.alt_scroll),
            "set-titles" => on(values.set_titles),
            "mode-keys" => values.mode_keys.clone(),
            "status-left" => values.status_left.clone(),
            "status-right" => values.status_right.clone(),
            // 빈 값은 "안 정했다" = 테마 그대로다. `?` 로 두면 모르는 값처럼 보인다.
            "status-bg" => theme_or(&values.status_bg),
            "status-fg" => theme_or(&values.status_fg),
            "status-position" => values.status_position.clone(),
            "status-interval" => values.status_interval.to_string(),
            "ambiguous-width" => values.ambiguous_width.clone(),
            "mouse-drag-threshold" => values.mouse_drag_threshold.to_string(),
            "inactive-dim-ratio" => format!("{:.2}", values.inactive_dim_ratio),
            // 배율은 한 자리면 충분하다(걸음이 0.1) — `1.00` 은 자릿수만 늘어 읽기 나쁘다.
            "font-scale" => format!("{:.1}×", values.font_scale),
            // 링크 줄은 값이 없다 — `…` 로 "여기서 다른 화면이 열린다"를 알린다.
            "list-keys" | "plugins" => "…".to_string(),
            // 값의 주인은 런타임 로케일이다 — 서버도 `SettingValues` 도 모른다.
            "language" => crate::i18n::locale().to_string(),
            "vt-parser" => values.vt_parser.clone(),
            "window-size" => values.window_size.clone(),
            "synchronize-panes" => on(values.sync),
            "monitor-activity" => on(values.monitor_activity),
            "monitor-bell" => on(values.monitor_bell),
            "automatic-rename" => on(values.auto_rename),
            _ => String::new(),
        }
    }
}

/// 설정 화면에서 한 줄을 골랐을 때 뷰가 할 일.
///
/// 뷰가 `SETTINGS` 를 뒤져 직접 갈라도 되지만, 그러면 GUI 와 TUI 가 각자 갈라 **한쪽에서만
/// 안 먹는 설정**이 생긴다(이 저장소가 계층 게이트를 두는 이유와 같다).
#[derive(Debug, Clone)]
pub enum SettingPick {
    /// 이 액션을 평소 경로로 태운다(서버 토글).
    Act(Action),
    /// **설정 파일**의 이 옵션을 뒤집는다(뷰가 `flip_config` 를 부른다).
    Flip(&'static str),
    /// 설정 파일의 이 옵션을 **이 값으로** 놓는다.
    Set(&'static str, &'static str),
    /// 설정 파일의 이 숫자 옵션을 이 값으로 놓는다.
    SetNumber(&'static str, f32),
    /// 이 물음을 띄운다. 문자열은 **미리 채울 지금 값**이다.
    Ask(Prompt, String),
}

/// 설정 화면의 `row` 번째 줄을 골랐다(`Enter` — 앞으로 돈다).
pub fn setting_pick(row: usize, values: &SettingValues) -> Option<SettingPick> {
    setting_pick_dir(row, values, true)
}

/// 같은 것을 **방향과 함께**(정본의 `←→ 값 변경`).
///
/// `forward = false` 가 필요한 자리는 선택지가 셋 이상인 줄이다 — 하나 지나쳤을 때
/// 앞으로 한 바퀴 더 도는 수밖에 없으면 그건 "값을 고른다"가 아니라 "값을 감는다"다.
/// 값이 하나뿐인 줄(토글·물음·링크)에서는 방향이 뜻이 없어 앞뒤가 같다.
pub fn setting_pick_dir(
    row: usize,
    values: &SettingValues,
    forward: bool,
) -> Option<SettingPick> {
    let setting = SETTINGS.get(row)?;
    Some(match setting.kind {
        SettingKind::Toggle(action) => SettingPick::Act(action),
        SettingKind::Text(prompt) => SettingPick::Ask(prompt, setting.value(values)),
        SettingKind::ConfigToggle => SettingPick::Flip(setting.key),
        // 다음 값을 **여기서** 골라 뷰에 넘긴다 — 뷰가 목록을 순회하면 두 뷰가 갈린다.
        SettingKind::Link(action) => SettingPick::Act(action),
        SettingKind::Number { lo, hi, step } => {
            let now = setting.value(values).parse::<f32>().unwrap_or(lo);
            let next = if forward { now + step } else { now - step };
            // 양끝에서 반대쪽으로 감는다 — 0.0 에서 왼쪽이 막히면 "안 먹는다"로 읽힌다.
            let next = if next > hi + f32::EPSILON {
                lo
            } else if next < lo - f32::EPSILON {
                hi
            } else {
                next
            };
            SettingPick::SetNumber(setting.key, next)
        }
        SettingKind::ConfigEnum(choices) => {
            let now = setting.value(values);
            let at = choices.iter().position(|c| *c == now).unwrap_or(usize::MAX);
            let len = choices.len();
            let to = if forward { at.wrapping_add(1) % len } else { at.wrapping_add(len - 1) % len };
            SettingPick::Set(setting.key, choices[to])
        }
        // 지금 값의 **다음**(또는 이전)으로 넘긴다.
        SettingKind::Enum(opt) => {
            SettingPick::Act(Action::SetEnum(opt, opt.step(&setting.value(values), forward)))
        }
        // 로케일은 둘뿐이라 "다음" = 반대쪽이다. 평소 경로(액션)를 태워야 팔레트의
        // `lang` 과 설정 화면이 같은 길로 끝난다.
        SettingKind::Lang => SettingPick::Act(Action::SetLang(
            if crate::i18n::locale() == "ko" { "en" } else { "ko" },
        )),
    })
}

/// 값 목록에서 **그 낱말을 직접 찍었다**(§10-21ⓣ — 설정 판 마우스).
///
/// # 왜 방향판(`setting_pick_dir`)으로 안 되나
///
/// 화살표는 한 칸씩 돈다 — 선택지가 넷이면 세 번째를 고르려고 두 번 눌러야 하고, 그건
/// 마우스가 할 수 있는 일(목표를 바로 가리키기)을 버리는 것이다. 그래서 자리를 받는 길을
/// 따로 둔다.
///
/// # 그래도 값을 정하는 것은 여기다
///
/// 뷰는 "몇 번째 낱말을 눌렀나"만 넘긴다. 뷰가 값을 직접 계산하면 키 경로와 마우스
/// 경로가 갈리고, 갈린 두 경로는 반드시 다르게 낡는다(제보의 ⚠ 그대로다).
///
/// 지금 값을 다시 찍으면 `None` 이다 — 서버 토글은 **뒤집기**라, 이미 그 값인데 부르면
/// 반대로 간다(화면에 켜진 것을 눌렀는데 꺼지는 그림이다).
pub fn setting_pick_at(row: usize, values: &SettingValues, index: usize) -> Option<SettingPick> {
    let setting = SETTINGS.get(row)?;
    let choices = setting.choices()?;
    let want = *choices.get(index)?;
    if setting.value(values) == want {
        return None;
    }
    Some(match setting.kind {
        // 서버 토글·설정 토글은 **뒤집기**뿐이다 — 지금과 다른 값을 찍었으니 뒤집으면 된다.
        SettingKind::Toggle(action) => SettingPick::Act(action),
        SettingKind::ConfigToggle => SettingPick::Flip(setting.key),
        SettingKind::ConfigEnum(_) => SettingPick::Set(setting.key, want),
        SettingKind::Enum(opt) => SettingPick::Act(Action::SetEnum(opt, want)),
        SettingKind::Lang => SettingPick::Act(Action::SetLang(want)),
        // `choices()` 가 `None` 인 갈래라 위에서 이미 돌아갔다.
        SettingKind::Number { .. } | SettingKind::Text(_) | SettingKind::Link(_) => return None,
    })
}

/// prefix 를 바꾼다 — **설정 파일에 남기고** 새 값을 돌려준다.
///
/// 못 읽는 표기면 `None` 이고 **파일은 손대지 않는다**. 못 읽는 값을 그대로 적으면 다음
/// 기동에 로더가 그 줄을 버려 기본값으로 돌아가는데, 파일에는 사용자가 적은 줄이 남아 있어
/// "설정했는데 안 먹는다"의 가장 나쁜 형태가 된다.
///
/// 쓰기가 실패해도(권한·읽기 전용 파일) **이번 판에는 적용한다** — 지금 눌러 바꾼 것이
/// 즉시 안 먹는 쪽이 더 놀랍다. 실패는 호출부가 알려 준다.
pub fn set_prefix(answer: &str) -> Option<((Key, Mods), std::io::Result<PathBuf>)> {
    let parsed = parse_key(answer.trim())?;
    let written = Config::write_option("prefix", &key_to_tmux(parsed));
    Some((parsed, written))
}

/// 설정 파일의 on/off 옵션 하나를 뒤집는다 — 새 설정과 쓰기 결과를 함께 돌려준다.
///
/// `set_prefix` 와 같은 규칙이다: 쓰기가 실패해도 **이번 판에는 적용한다**(방금 바꾼 것이
/// 즉시 안 먹는 쪽이 더 놀랍다). 실패는 호출부가 알린다.
pub fn flip_config(key: &str, now: &Config) -> Option<(Config, std::io::Result<PathBuf>)> {
    let mut next = now.clone();
    let value = match key {
        "inactive-dim" => {
            next.inactive_dim = !now.inactive_dim;
            next.inactive_dim
        }
        "mouse" => {
            next.mouse = !now.mouse;
            next.mouse
        }
        "mouse-drag-copy" => {
            next.mouse_drag_copy = !now.mouse_drag_copy;
            next.mouse_drag_copy
        }
        "strip-box-drawing" => {
            next.strip_box_drawing = !now.strip_box_drawing;
            next.strip_box_drawing
        }
        "copy-unwrap" => {
            next.copy_unwrap = !now.copy_unwrap;
            next.copy_unwrap
        }
        "touch-scroll" => {
            next.touch_scroll = !now.touch_scroll;
            next.touch_scroll
        }
        "alt-scroll" => {
            next.alt_scroll = !now.alt_scroll;
            next.alt_scroll
        }
        "set-titles" => {
            next.set_titles = !now.set_titles;
            next.set_titles
        }
        _ => return None,
    };
    let written = Config::write_option(key, if value { "on" } else { "off" });
    Some((next, written))
}

/// 숫자 옵션을 **설정 파일에 적을 글**로.
///
/// 정수 옵션은 정수로 적는다 — `3.00` 은 파서가 `u16` 으로 못 읽어 **그 줄이 조용히
/// 무시된다**(파이썬 정본도 같다). 증상은 "설정 화면에서 올렸는데 다음 기동에 되돌아
/// 있다"라, 쓰는 자리와 읽는 자리가 어긋나도 아무도 안 운다.
///
/// 함수로 뺀 이유가 그것이다: 이 규칙을 [`set_number`] 안에 두면 **테스트가 같은 규칙을
/// 한 벌 더 적어야** 하고, 두 벌이 되는 순간 이 게이트는 자기 사본을 재게 된다
/// (`every_setting_row_reaches_something_that_reads_it` 이 실제로 그 자리에 섰다).
pub fn number_text(key: &str, value: f32) -> String {
    if matches!(key, "mouse-drag-threshold" | "status-interval") {
        format!("{}", value as i64)
    } else {
        format!("{value:.2}")
    }
}

/// 설정 파일의 숫자 옵션 하나를 놓는다.
pub fn set_number(
    key: &str,
    value: f32,
    now: &Config,
) -> Option<(Config, std::io::Result<PathBuf>)> {
    let mut next = now.clone();
    match key {
        "inactive-dim-ratio" => next.inactive_dim_ratio = value.clamp(0.0, 0.8),
        "font-scale" => next.font_scale = value.clamp(FONT_SCALE_LO, FONT_SCALE_HI),
        "mouse-drag-threshold" => next.mouse_drag_threshold = value.clamp(1.0, 20.0) as u16,
        "status-interval" => next.status_interval = value.clamp(1.0, 60.0) as u16,
        _ => return None,
    }
    Some((next, Config::write_option(key, &number_text(key, value))))
}

/// 이 물음의 대답이 **설정 파일의 어느 키**로 가는가. 물음이 설정용이 아니면 `None`.
///
/// # 왜 core 에 있나
///
/// 이 표가 뷰에 있으면 **두 뷰가 각자 적는다** — 한쪽에만 줄을 더하면 그 설정은 그 클라
/// 에서만 안 먹고, 증상은 "설정 화면에서 고쳐도 안 바뀐다"다. 여기 한 곳에 두면
/// [`SETTINGS`] 와 왕복이 맞는지 기계가 볼 수 있다(`every_text_setting_round_trips`).
pub fn prompt_key(prompt: Prompt) -> Option<&'static str> {
    // ⚠ **특별 취급이 필요한 둘은 일부러 뺀다.** 여기서 키를 돌려주면 호출부가 그
    // 처리를 건너뛴다 — 실제로 이 함수를 만들자마자 그 자리에 섰다:
    //
    // - `default-path`: 빈 대답이 **되돌리기**다(서버 기본 `current` 로).
    // - `prefix`: 대답이 값이 아니라 **키 표기**라 파싱해야 한다(`C-a`).
    //
    // 뺀 자리는 아래 왕복 오라클이 이름으로 들고 있다 — 조용히 늘어나지 않는다.
    if matches!(prompt, Prompt::DefaultPath | Prompt::SetPrefix) {
        return None;
    }
    SETTINGS
        .iter()
        .find(|s| matches!(s.kind, SettingKind::Text(p) if p == prompt))
        .map(|s| s.key)
}

/// 빈 색 이름은 "안 정했다" = 테마 그대로다. `?` 로 적으면 **모르는 값**처럼 보인다.
fn theme_or(name: &str) -> String {
    if name.is_empty() {
        crate::i18n::t("(테마)").to_owned()
    } else {
        name.to_owned()
    }
}

/// 설정 파일의 옵션 하나를 **주어진 값으로** 놓는다(`flip_config` 의 값 버전).
pub fn set_config(
    key: &str,
    value: &str,
    now: &Config,
) -> Option<(Config, std::io::Result<PathBuf>)> {
    let mut next = now.clone();
    match key {
        "tab-bar" => next.tab_bar_always = value == "always",
        "mode-keys" => next.mode_keys = value.to_owned(),
        "ambiguous-width" => next.ambiguous_width = value.to_owned(),
        "status-position" => next.status_position = value.to_owned(),
        "default-path" => next.default_path = value.to_owned(),
        "status-left" => next.status_left = value.to_owned(),
        "status-right" => next.status_right = value.to_owned(),
        "status-bg" => next.status_bg = value.to_owned(),
        "status-fg" => next.status_fg = value.to_owned(),
        _ => return None,
    }
    Some((next, Config::write_option(key, value)))
}

/// 중립 키를 tmux 표기로(`C-a`·`M-x`). `parse_key` 의 역방향이다.
///
/// 화면에 적을 때와 **설정 파일에 쓸 때** 둘 다 쓴다 — 표기가 갈리면 우리가 쓴 줄을
/// 파이썬 클라가 못 읽는다.
pub fn key_to_tmux((key, mods): (Key, Mods)) -> String {
    let mut out = String::new();
    if mods.ctrl {
        out.push_str("C-");
    }
    if mods.alt {
        out.push_str("M-");
    }
    match key {
        Key::Char(c) => out.push(c),
        _ => out.push('?'),
    }
    out
}

/// 같은 설정을 가리키는 다른 철자(파이썬 `_OPT_ALIASES`).
///
/// 이게 없으면 `set tabbar always` 가 적힌 파일에 `set tab-bar auto` 를 **덧붙인다** —
/// 같은 옵션이 두 줄이 되고, 로더는 나중 것을 쓰므로 사용자가 원래 적어 둔 줄이 조용히
/// 죽는다. `_`↔`-` 는 아래 정규화가 흡수하므로 실제로 남는 짝은 둘이다.
const OPT_ALIASES: &[(&str, &[&str])] = &[
    ("tab-bar", &["tabbar"]),
    ("default-path", &["default_path"]),
    ("inactive-dim", &["inactive_dim"]),
    ("inactive-dim-ratio", &["inactive_dim_ratio"]),
    ("mouse-drag-copy", &["mouse_drag_copy"]),
    ("mouse-drag-threshold", &["mouse_drag_threshold"]),
    ("strip-box-drawing", &["strip_box_drawing"]),
    ("touch-scroll", &["touch_scroll"]),
    ("lang", &["language"]),
];

/// 옵션 이름 비교용 정규형(`_`→`-`, 소문자).
fn normalize_opt(name: &str) -> String {
    name.replace('_', "-").to_ascii_lowercase()
}

/// `key` 와 같은 설정을 뜻하는 모든 철자(정규형).
fn alias_set(key: &str) -> Vec<String> {
    let key = normalize_opt(key);
    let mut names = vec![key.clone()];
    for (canon, aliases) in OPT_ALIASES {
        let group: Vec<String> = std::iter::once(normalize_opt(canon))
            .chain(aliases.iter().map(|a| normalize_opt(a)))
            .collect();
        if group.contains(&key) {
            names.extend(group);
        }
    }
    names.sort();
    names.dedup();
    names
}

/// `set <key> <value>` 를 반영한 새 파일 내용.
///
/// 정본은 파이썬 `keymap.py::set_config_option` 이고, 규칙은 픽스처
/// (`tests/fixtures/config_write.json`)가 잡아 둔다:
///
/// - 첫 비주석 `set <같은 옵션>` 줄을 **선행 공백을 지키며** 통째로 갈아 끼운다.
/// - 없으면 끝에 붙인다(끝 개행이 없으면 넣고).
/// - 주석·`bind`·`hook`·빈 줄은 손대지 않는다.
///
/// **파이썬과 갈리는 자리 하나** — 줄바꿈. 파이썬은 텍스트 모드로 쓰기 때문에 Windows 에서
/// 파일 전체가 CRLF 로 번역된다. 여기서는 **원본 줄의 터미네이터를 그대로 둔다**. 안 그러면
/// 값 하나를 바꿨는데 diff 가 파일 전체로 번지고, 그 파일이 버전 관리 아래 있으면 사용자는
/// 자기가 뭘 바꿨는지 못 본다. 두 클라 모두 읽을 때 `\r` 을 떼므로 뜻은 갈리지 않는다.
pub fn edit_option(text: &str, key: &str, value: &str) -> String {
    let names = alias_set(key);
    let newline = if text.contains("\r\n") { "\r\n" } else { "\n" };
    // 터미네이터를 붙인 채로 자른다 — 보존하려면 원본 바이트가 필요하다.
    let mut lines: Vec<String> = Vec::new();
    let mut rest = text;
    while !rest.is_empty() {
        match rest.find('\n') {
            Some(i) => {
                lines.push(rest[..=i].to_string());
                rest = &rest[i + 1..];
            }
            None => {
                lines.push(rest.to_string());
                rest = "";
            }
        }
    }

    for line in lines.iter_mut() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let mut tokens = trimmed.split_whitespace();
        if tokens.next() != Some("set") {
            continue;
        }
        let Some(option) = tokens.next() else { continue };
        if !names.contains(&normalize_opt(option)) {
            continue;
        }
        let indent: String = line.chars().take_while(|c| *c == ' ' || *c == '\t').collect();
        let terminator = if line.ends_with("\r\n") {
            "\r\n"
        } else if line.ends_with('\n') {
            "\n"
        } else {
            newline
        };
        *line = format!("{indent}set {key} {value}{terminator}");
        return lines.concat();
    }

    // 못 찾았다 — 끝에 붙인다. 마지막 줄에 개행이 없으면 먼저 넣는다(안 그러면 두 설정이
    // 한 줄로 붙어 **둘 다 죽는다**).
    if let Some(last) = lines.last_mut()
        && !last.ends_with('\n')
    {
        last.push_str(newline);
    }
    lines.push(format!("set {key} {value}{newline}"));
    lines.concat()
}

/// 사용자가 건 키 하나(`bind` 줄).
///
/// # 왜 명령을 **이름으로** 들고 있나
///
/// 명령 팔레트가 이미 파이썬 이름 → 액션 표를 갖고 있다([`PALETTE`](crate::PALETTE)).
/// 여기서 액션으로 미리 풀어 두면 그 표가 둘이 되고, 팔레트에 명령이 늘어도 `bind` 는
/// 모르는 채로 남는다. 누를 때 찾는 편이 표 하나로 끝난다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Bind {
    /// prefix 뒤에 눌러야 하나(`false` = `bind -n`, prefix 없이 바로).
    pub after_prefix: bool,
    /// 키 이름 — [`binding_name`](crate::keys::binding_name) 과 같은 표기(`shift-G`).
    pub key: String,
    /// 팔레트에 있는 명령 이름(`split-window -h` 등).
    pub command: String,
}

impl Bind {
    /// `bind <키> <명령>` 또는 `bind -n <키> <명령>` 한 줄. 모양이 아니면 `None`.
    pub fn parse(line: &str) -> Option<Self> {
        let mut parts = line.split_whitespace();
        if parts.next()? != "bind" {
            return None;
        }
        let first = parts.next()?;
        let (after_prefix, key) = if first == "-n" {
            (false, parts.next()?)
        } else {
            (true, first)
        };
        let command: Vec<&str> = parts.collect();
        if command.is_empty() {
            return None;
        }
        Some(Self {
            after_prefix,
            // tmux 표기(`C-x`)도 받아 우리 표기로 맞춘다 — raw 로 두면 절대 안 먹는다
            // (파이썬도 같은 이유로 `normalize_binding_key` 를 거친다).
            key: normalize_bind_key(key),
            command: command.join(" "),
        })
    }

    /// 이 바인딩이 걸린 액션. 팔레트에 없는 명령이면 `None`.
    pub fn action(&self) -> Option<Action> {
        crate::PALETTE
            .iter()
            .find(|e| e.name == self.command)
            .map(|e| e.action)
    }
}

/// `bind` 한 줄을 설정 파일에 **덧붙인다**(같은 키가 이미 있으면 그 줄을 갈아 끼운다).
///
/// `set` 과 달리 옵션 하나가 아니라 **키마다 한 줄**이라 `edit_option` 을 못 쓴다.
pub fn write_bind(line: &str) -> std::io::Result<PathBuf> {
    let Some(bind) = Bind::parse(line) else {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            crate::i18n::t("bind 줄이 아니다"),
        ));
    };
    rewrite_binds(|kept| {
        kept.push(format!(
            "bind {}{} {}",
            if bind.after_prefix { "" } else { "-n " },
            bind.key,
            bind.command
        ));
    }, Some(&bind))
}

/// `bind` 한 줄을 **지운다**. 없으면 파일은 그대로다.
pub fn erase_bind(spec: &str) -> std::io::Result<PathBuf> {
    let mut parts = spec.split_whitespace();
    let first = parts.next().unwrap_or_default();
    let (after_prefix, key) = if first == "-n" {
        (false, parts.next().unwrap_or_default())
    } else {
        (true, first)
    };
    let target = Bind {
        after_prefix,
        key: normalize_bind_key(key),
        command: String::new(),
    };
    rewrite_binds(|_| {}, Some(&target))
}

/// 설정 파일의 `bind` 줄들을 다시 쓴다 — `drop` 과 **같은 키**인 줄을 빼고, `add` 가
/// 넣는 줄을 끝에 붙인다. 그 밖의 줄(주석·`set`·모르는 지시어)은 **그대로 둔다**.
fn rewrite_binds(
    add: impl FnOnce(&mut Vec<String>),
    drop: Option<&Bind>,
) -> std::io::Result<PathBuf> {
    let target = Config::path_for_write();
    let before = std::fs::read_to_string(&target).unwrap_or_default();
    let newline = if before.contains("\r\n") { "\r\n" } else { "\n" };
    let mut kept: Vec<String> = Vec::new();
    for line in before.lines() {
        let same = drop.is_some_and(|d| {
            Bind::parse(line.trim())
                .is_some_and(|b| b.after_prefix == d.after_prefix && b.key == d.key)
        });
        if !same {
            kept.push(line.to_owned());
        }
    }
    add(&mut kept);
    if let Some(parent) = target.parent().filter(|p| !p.as_os_str().is_empty()) {
        std::fs::create_dir_all(parent)?;
    }
    let mut text = kept.join(newline);
    if !text.is_empty() {
        text.push_str(newline);
    }
    crate::atomicfile::write(&target, &text)?;
    Ok(target)
}

/// `bind` 의 키 표기를 [`binding_name`](crate::keys::binding_name) 표기로.
///
/// `C-x` → `ctrl-x`, `M-x` → `alt-x`, 대문자 하나 → `shift-X`. 나머지는 그대로 둔다
/// (`f5`·`enter` 같은 이름은 이미 우리 표기다).
pub fn normalize_bind_key(key: &str) -> String {
    let lower = key.to_ascii_lowercase();
    if let Some(rest) = lower.strip_prefix("c-") {
        return format!("ctrl-{rest}");
    }
    if let Some(rest) = lower.strip_prefix("m-") {
        return format!("alt-{rest}");
    }
    let mut chars = key.chars();
    if let (Some(c), None) = (chars.next(), chars.next())
        && c.is_ascii_uppercase()
    {
        return format!("shift-{c}");
    }
    key.to_owned()
}

/// 사용자가 건 키를 찾는다 — 이 키에 걸린 액션이 있으면 그것.
///
/// `after_prefix` 는 지금 모드다(prefix 를 누른 뒤인가). **모드가 다르면 안 찾는다** —
/// `bind -n q` 를 걸어 둔 사람이 prefix 뒤에 `q` 를 눌렀을 때 그게 발동하면 안 된다.
pub fn user_action(binds: &[Bind], after_prefix: bool, key: Key, mods: Mods) -> Option<Action> {
    let name = crate::keys::binding_name_with(key, mods)?;
    binds
        .iter()
        .filter(|b| b.after_prefix == after_prefix && b.key == name)
        .find_map(Bind::action)
}

/// `on`/`true`/`1`/`yes` 를 참으로(파이썬 `load_config` 와 같은 목록).
fn on_off(value: &str) -> bool {
    matches!(value.to_ascii_lowercase().as_str(), "on" | "true" | "1" | "yes")
}

/// 홈 디렉토리. `dirs` 같은 크레이트를 들이지 않는 이유는 이 크레이트의 **의존이 0개**라는
/// 계약 때문이다(PROVENANCE §2-a).
fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

/// tmux 표기의 키 하나(`C-a`·`ctrl+b`·`M-x`)를 중립 키로.
///
/// 파이썬 클라의 `_tmux_key_to_textual` 이 받는 표기 중 **prefix 로 쓸 수 있는 것만**
/// 읽는다 — prefix 는 글자 하나 + 수정키다(특수키를 prefix 로 두는 사람은 없고, 받아 두면
/// 그 조합을 패널에 못 보내는 길이 생긴다).
pub fn parse_key(token: &str) -> Option<(Key, Mods)> {
    let mut rest = token.trim();
    let mut ctrl = false;
    let mut alt = false;
    loop {
        let lower = rest.to_ascii_lowercase();
        let stripped = if let Some(r) = lower.strip_prefix("c-").or(lower.strip_prefix("ctrl-")) {
            ctrl = true;
            r.len()
        } else if let Some(r) = lower.strip_prefix("ctrl+") {
            ctrl = true;
            r.len()
        } else if let Some(r) = lower
            .strip_prefix("m-")
            .or(lower.strip_prefix("alt-"))
            .or(lower.strip_prefix("alt+"))
        {
            alt = true;
            r.len()
        } else {
            break;
        };
        rest = &rest[rest.len() - stripped..];
    }
    let mut chars = rest.chars();
    let c = chars.next()?;
    if chars.next().is_some() {
        return None; // 글자 하나가 아니다(특수키 이름 등)
    }
    // 수정키가 하나도 없으면 prefix 로 쓸 수 없다 — 그 글자를 영영 못 치게 된다.
    if !ctrl && !alt {
        return None;
    }
    Some((
        Key::Char(c.to_ascii_lowercase()),
        Mods {
            ctrl,
            alt,
        },
    ))
}

#[cfg(test)]
#[path = "config_tests.rs"]
mod tests;
