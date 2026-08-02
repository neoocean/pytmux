//! 경량 다국어화(i18n) — 한국어(ko)·영어(en). 파이썬 정본은 `pytmuxlib/i18n.py`.
//!
//! # 파이썬과 무엇이 같고(호환 표면) 무엇이 다른가(내부 표현)
//!
//! **같아야 하는 것** — 사용자가 만지는 표면과 두 클라가 공유하는 상태:
//! - 로케일 우선순위: 런타임 `lang` 명령의 영속(`.lang` 파일) > 설정 파일 `lang` >
//!   환경 `LC_ALL`/`LANG`(`ko*`→ko, 그 외→en). 파이썬 `resolve`/`load_persisted` 와 동형.
//! - `.lang` 파일의 자리·내용: `state_base(엔드포인트) + ".lang"` 에 `ko`/`en` 한 낱말.
//!   **파이썬 클라와 같은 파일**이라 한쪽에서 `lang en` 하면 다른 클라도 다음 기동부터
//!   영어다(경로 계산은 엔드포인트를 아는 proto 쪽 — [`crate::i18n`] 은 경로를 주입받는다).
//! - `lang ko|en` 명령·`language` 설정 줄·전환 피드백(`언어: {name}`).
//!
//! **다른 것** — 카탈로그의 키. 파이썬은 `"dialog.kill_tab_title"` 같은 도메인 키에
//! ko·en 값을 **둘 다** 싣는다. 우리는 **한국어 원문이 곧 키**다(gettext 의 msgid 방식):
//!
//! - 이 저장소의 원문 언어는 한국어고, 한국어 문자열을 단언하는 테스트가 140줄 있다.
//!   키를 따로 만들면 그 140줄이 전부 키 조회로 바뀐다 — 원문이 키면 **기본 로케일(ko)
//!   에서 `t()` 는 항등**이라 테스트도 화면도 그대로다.
//! - `t(&'static str) -> &'static str` 이 성립한다(en 값도 정적 표에 있다). 그래서
//!   `label() -> &'static str` 같은 기존 시그니처를 하나도 안 바꾸고 끼어든다.
//! - en 이 빠진 문자열은 **한국어로 폴백**한다 — 파이썬의 단계적 롤아웃과 같은 우아한
//!   degrade 다(번역이 빠져도 깨진 키가 아니라 원문이 보인다).
//!
//! 번역 대상은 **사용자가 보는 표면만**이다. 로그·내부 키·명령 이름(`split-window` 등
//! 서버 어휘)은 번역하지 않는다 — 명령 이름은 두 클라 사용자가 외운 철자 그 자체다.
//!
//! # 로케일이 전역인 이유
//!
//! 클라 프로세스 하나에 사용자 하나다(파이썬도 모듈 전역). 뷰 둘(TUI·GUI)이 같은 값을
//! 봐야 하고, 렌더 시점에 읽으므로 원자 하나면 된다. 전환은 즉시 화면에 반영된다 —
//! 이미 만들어 둔 String(지난 알림 등)은 옛 언어로 남는다(파이썬과 같다).
//!
//! 그 전제가 **거짓인 자리가 하나** 있다: 한 이진 안에서 병렬로 도는 테스트다. 다른
//! 언어에서 재려고 전역을 뒤집으면 그 창 동안 남의 테스트가 남의 로케일에서 단언한다 —
//! 그래서 [`with_locale`] 이 있고, 테스트는 [`set_locale`] 을 부르지 않는다(그 항목의
//! 사고 기록 참조).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};

mod en_claude;
mod en_core;
mod en_gui;
mod en_proto;
// 서버가 지어 보내는 글(정본 카탈로그에서 뽑은 표) — 아래 fold 의 **마지막**이다.
mod en_server;

/// 지원 로케일. 첫 항목이 원본/폴백 언어다(파이썬 `LOCALES` 와 같다).
pub const LOCALES: &[&str] = &["ko", "en"];

/// `true` 면 영어. 로케일이 둘뿐이라 bool 하나가 표현으로 충분하고, 셋째 언어가
/// 생기면 이 표현부터 바꾸면 된다(그때까지 enum 은 과하다).
static EN_ACTIVE: AtomicBool = AtomicBool::new(false);

/// 이 **스레드에만** 적용되는 덮어쓰기. `None` 이면 전역(`EN_ACTIVE`)을 따른다.
///
/// # 왜 있나 — 뒤집은 전역이 남의 단언에 샜다
///
/// 로케일이 프로세스 전역이라, 한 이진 안에서 **병렬로 도는 테스트** 하나가 잠깐
/// `set_locale("en")` 하면 그 창 동안 다른 테스트가 **남의 로케일에서 단언**한다.
/// 실제로 그랬다(2026-08-02): `session_tests` 의 배지 테스트가 en 구간에 들어간 사이
/// `the_sync_badge_is_always_there_when_sync_is_on` 이 `[동기화]` 대신 영어를 보고
/// 떨어졌고, `server_strings_conformance` 는 34개 전부가 한국어로 나왔다. 둘 다
/// **혼자 돌리면 초록**이라 "부하 플레이크"로 읽히기 쉬운 모양이다.
///
/// 종전 처방은 잠금(`Mutex`)이었는데 **그것으로는 못 막는다** — 잠그는 쪽은 뒤집는
/// 테스트뿐이고, 읽는 쪽 수백 개가 같은 잠금을 들 리가 없다. 그래서 뒤집기를 잠그는
/// 대신 **스레드 밖으로 안 나가게** 한다: 러너가 테스트마다 스레드를 주므로 덮어쓰기가
/// 그 테스트 안에서 끝난다. 전역은 그대로 두므로 제품 경로(`lang` 명령 → 즉시 반영)는
/// 한 글자도 안 바뀐다.
thread_local! {
    static LOCAL_EN: std::cell::Cell<Option<bool>> = const { std::cell::Cell::new(None) };
}

/// 지금 이 스레드가 볼 로케일이 en 인가. 덮어쓰기 > 전역.
///
/// TLS 파괴 뒤에도 부를 수 있으므로 `try_with` 다 — 그때는 전역으로 떨어진다.
fn en_active() -> bool {
    LOCAL_EN
        .try_with(|cell| cell.get())
        .ok()
        .flatten()
        .unwrap_or_else(|| EN_ACTIVE.load(Ordering::Relaxed))
}

/// 살아 있는 동안 **이 스레드의** 로케일을 덮어쓰고, 떨어질 때 원래대로 돌린다.
///
/// 되돌리기가 `Drop` 이라 단언이 터져도(=패닉해도) 다음 것이 남의 로케일을 물려받지
/// 않는다. 함수 하나가 통째로 다른 언어일 때 쓴다 — 몸통을 들여쓰지 않아도 된다.
#[must_use = "떨어뜨리면 그 자리에서 로케일이 돌아간다 — `let _guard = …` 로 붙잡을 것"]
pub struct LocaleGuard(Option<bool>);

impl Drop for LocaleGuard {
    fn drop(&mut self) {
        let _ = LOCAL_EN.try_with(|cell| cell.set(self.0));
    }
}

/// 이 스레드를 `loc` 로케일로 두는 안내자. [`LocaleGuard`] 참조.
pub fn locale_guard(loc: &str) -> LocaleGuard {
    LocaleGuard(LOCAL_EN.with(|cell| cell.replace(Some(loc == "en"))))
}

/// `loc` 로케일에서 `f` 를 부르고 **이 스레드의** 로케일을 원래대로 돌린다.
///
/// 재는 구간이 짧을 때 쓴다. 단언은 이 블록 **밖**에서 하는 편이 낫다 — 안에서
/// 터뜨리면 실패 메시지를 짓는 동안에도 로케일이 바뀐 채다.
pub fn with_locale<T>(loc: &str, f: impl FnOnce() -> T) -> T {
    let _guard = locale_guard(loc);
    f()
}

/// 활성 로케일을 바꾼다. 미지원 값이면 폴백(ko). 적용된 로케일을 돌려준다.
///
/// ⚠ **전역을 바꾼다** — 이 프로세스의 모든 스레드가 본다. 테스트에서는 부르지 말고
/// [`with_locale`] 을 쓸 것(위 항목의 사고). 제품에서 이것을 부르는 자리는
/// `lang` 명령 하나다.
pub fn set_locale(loc: &str) -> &'static str {
    let en = loc == "en";
    EN_ACTIVE.store(en, Ordering::Relaxed);
    if en { "en" } else { "ko" }
}

/// 지금 활성 로케일.
pub fn locale() -> &'static str {
    if en_active() { "en" } else { "ko" }
}

/// 한국어 원문 → 지금 로케일의 문자열. en 이고 번역이 있으면 영어, 아니면 원문 그대로.
///
/// 입력이 `'static` 이면 출력도 `'static` 이다 — 정적 표의 라벨을 그대로 지난다.
pub fn t<'a>(ko: &'a str) -> &'a str {
    if !en_active() {
        return ko;
    }
    en_map().get(ko).copied().unwrap_or(ko)
}

/// 문맥이 있는 번역 — gettext 의 msgctxt 에 해당한다.
///
/// 원문이 키인 방식의 약점 하나가 **동음이의**다: "동작" 은 설정 카테고리로는
/// Behavior 고 인자 폼 줄 이름으로는 Action 이다(파이썬은 키가 달라 안 부딪힌다 —
/// `setcat.동작` 대 폼 라벨). 문맥 키(`ctx\u{4}원문`)가 있으면 그것을, 없으면 평소
/// [`t`] 로 떨어진다. 표기는 gettext 와 같은 EOT(U+0004) 구분자다.
pub fn tc<'a>(ctx: &str, ko: &'a str) -> &'a str {
    if !en_active() {
        return ko;
    }
    let key = format!("{ctx}\u{0004}{ko}");
    if let Some(v) = en_map().get(key.as_str()) {
        return v;
    }
    en_map().get(ko).copied().unwrap_or(ko)
}

/// `{name}` 꼴 자리를 채우는 번역. 파이썬 `t(key, **kw)` 의 `str.format` 에 해당한다.
///
/// 어순이 언어마다 달라서 값을 **번역 뒤에** 끼운다 — `format!` 은 컴파일 시점 리터럴만
/// 받으므로 이 자리는 소박한 치환이다. 모르는 자리는 그대로 남는다(렌더가 죽지 않게).
pub fn tf(ko: &str, args: &[(&str, &str)]) -> String {
    let mut out = t(ko).to_string();
    for (name, value) in args {
        out = out.replace(&format!("{{{name}}}"), value);
    }
    out
}

/// ko → en 표. 첫 조회 때 크레이트별 정적 표를 하나로 접는다.
///
/// 표가 크레이트별 파일(`i18n/en_*.rs`)로 갈라져 있는 이유: 문자열의 **주인 크레이트**
/// 옆에 번역을 두면 지울 때 같이 지워지고, 파일이 갈라져 있으면 동시 작업이 안 부딪힌다.
fn en_map() -> &'static HashMap<&'static str, &'static str> {
    static MAP: OnceLock<HashMap<&'static str, &'static str>> = OnceLock::new();
    MAP.get_or_init(|| {
        let mut map = HashMap::new();
        for table in [
            en_core::EN,
            en_proto::EN,
            en_gui::EN,
            en_claude::EN,
            // 마지막이라 같은 원문이 겹치면 **여기가 이긴다** — 서버가 보내는 글의
            // 권위는 정본이다(`en_server.rs` 머리말).
            en_server::EN,
        ] {
            for (ko, en) in table {
                map.insert(*ko, *en);
            }
        }
        map
    })
}

/// 초기 로케일 결정 — 파이썬 `resolve` 와 같은 규칙.
///
/// `config_lang`(설정 파일 `lang`)이 지원 로케일이면 그것, 아니면 환경(`LC_ALL` 우선,
/// 없으면 `LANG`)이 `ko` 로 시작하면 ko, 그 외(미설정·C/POSIX 포함)는 en.
/// 영속(`.lang`)은 이보다 우선한다 — 호출 순서는 [`init`] 이 안다.
pub fn resolve(config_lang: Option<&str>, env_locale: Option<&str>) -> &'static str {
    if let Some(lang) = config_lang {
        let lang = lang.to_ascii_lowercase();
        if LOCALES.contains(&lang.as_str()) {
            return if lang == "en" { "en" } else { "ko" };
        }
    }
    match env_locale {
        Some(raw) if raw.to_ascii_lowercase().starts_with("ko") => "ko",
        _ => "en",
    }
}

/// 지금 프로세스의 환경 변수에서 로케일 후보를 읽는다(`LC_ALL` > `LANG`).
pub fn env_locale() -> Option<String> {
    std::env::var("LC_ALL")
        .ok()
        .filter(|v| !v.is_empty())
        .or_else(|| std::env::var("LANG").ok().filter(|v| !v.is_empty()))
}

/// 런타임 `lang` 선택이 영속되는 파일. 시동 때 엔드포인트를 아는 쪽(proto/뷰 루프)이
/// 넣어 준다 — core 는 소켓 규칙을 모른다(계층 게이트).
static PERSIST_PATH: Mutex<Option<PathBuf>> = Mutex::new(None);

/// 영속 파일 자리를 등록한다(시동 때 한 번).
pub fn set_persist_path(path: PathBuf) {
    *PERSIST_PATH.lock().unwrap() = Some(path);
}

/// 영속된 로케일 선택을 읽는다. 없거나 미지원 값이면 `None`(→ [`resolve`] 로).
pub fn load_persisted(path: &Path) -> Option<String> {
    let v = std::fs::read_to_string(path).ok()?;
    let v = v.trim();
    LOCALES.contains(&v).then(|| v.to_string())
}

/// 지금 선택을 영속한다(best-effort — 실패해도 런타임엔 이미 적용됐다. 파이썬과 같다).
pub fn persist(loc: &str) {
    if let Some(path) = PERSIST_PATH.lock().unwrap().as_ref() {
        // 원자 교체 — 셋이 동시에 언어를 바꾸면 종전에는 반만 쓰인 `.lang` 이 남을 수
        // 있었다(읽는 쪽이 관대해 조용히 기본값으로 떨어진다 = 선택이 사라진다).
        let _ = crate::atomicfile::write(path, loc);
    }
}

/// 시동 한 번의 로케일 결정: 영속(`.lang`) > 설정 `lang` > 환경. 적용까지 한다.
///
/// `persist_path` 는 등록만 하고 **쓰지 않는다** — 파일을 만드는 것은 사용자의 첫
/// `lang` 선택이다(안 만들면 환경을 따라가는 사용자를 파일이 붙잡지 않는다).
pub fn init(persist_path: Option<PathBuf>, config_lang: Option<&str>) -> &'static str {
    let persisted = persist_path.as_deref().and_then(load_persisted);
    if let Some(path) = persist_path {
        set_persist_path(path);
    }
    let loc = match persisted {
        Some(v) => set_locale(&v),
        None => set_locale(resolve(config_lang, env_locale().as_deref())),
    };
    loc
}

#[cfg(test)]
#[path = "i18n_tests.rs"]
mod tests;
