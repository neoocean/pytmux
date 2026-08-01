//! 로케일 **전환**의 오라클 — 유닛이 아니라 통합 테스트인 이유.
//!
//! 로케일은 프로세스 전역이다. 유닛 테스트(한 프로세스, 병렬 스레드)에서 en 으로
//! 바꾸면 같은 순간 도는 다른 테스트의 한국어 단언이 무너진다 — 이 저장소는 전역을
//! 만지는 테스트를 이미 한 번 걷어냈다(`endpoint_tests` 머리말). 통합 테스트는
//! **파일마다 자기 프로세스**라 여기서는 마음껏 바꾼다.
//!
//! 뷰 쪽 배선 오라클(TUI·GUI 의 `session_view_tests`)은 반대로 전역을 안 바꾸는
//! 설계다: 지금 로케일과 같은 값(ko)을 골라 배선 증거(알림·영속 파일)만 본다.
//! 실제 "영어가 되는가"는 이 파일이 정본이다.

use base::config::{Config, SETTINGS, SettingPick, setting_pick, SettingValues};
use base::i18n;
use base::options::{OptionPick, options_for, pick};
use base::Action;

/// 전환 시나리오 전부를 **한 테스트**에 순서대로 담는다.
///
/// 갈라 두면 이 파일 안에서도 병렬 스레드가 전역 로케일을 서로 밟는다 — 한 덩어리면
/// 순서가 곧 격리다(이 파일의 존재 이유와 같은 논리).
#[test]
fn the_locale_switch_end_to_end() {
    // ── 폼의 두 선택지가 각각 그 로케일의 액션을 만든다 ─────────────────────
    let lang = options_for("lang").expect("lang 폼이 표에 없다");
    assert!(matches!(
        pick(lang, &[0]),
        Some(OptionPick::Act(Action::SetLang("ko")))
    ));
    assert!(matches!(
        pick(lang, &[1]),
        Some(OptionPick::Act(Action::SetLang("en")))
    ));

    // ── 설정 화면의 language 줄: 지금 값의 **반대쪽**으로 넘긴다 ───────────────
    let row = SETTINGS
        .iter()
        .position(|s| s.key == "language")
        .expect("language 줄이 설정 화면에 없다");
    let values = SettingValues::default();
    assert!(matches!(
        setting_pick(row, &values),
        Some(SettingPick::Act(Action::SetLang("en")))
    ));

    // ── en 전환: t 가 실제로 영어를 돌려주고, 값 표시·다음 전환도 따라온다 ────
    assert_eq!(i18n::set_locale("en"), "en");
    assert_eq!(i18n::locale(), "en");
    assert_eq!(i18n::t("언어"), "Language");
    assert_eq!(
        i18n::tf("언어: {name}", &[("name", "English")]),
        "Language: English"
    );
    // 번역이 없는 문자열은 **원문으로 폴백**한다(파이썬의 단계적 degrade 와 같다).
    assert_eq!(i18n::t("이 표에 없는 문자열"), "이 표에 없는 문자열");
    // 문맥 번역: 같은 원문 "동작" 이 폼에서는 Action, 설정 카테고리에서는 Behavior
    // (파이썬 `setcat.동작` 과 같은 낱말). 모르는 문맥은 평문 번역으로 떨어진다.
    assert_eq!(i18n::t("동작"), "Action");
    assert_eq!(i18n::tc("setcat", "동작"), "Behavior");
    assert_eq!(i18n::tc("아무문맥", "동작"), "Action");
    // 설정 화면의 값 칸이 지금 로케일을 보이고, Enter 는 이제 ko 로 넘긴다.
    assert_eq!(SETTINGS[row].value(&values), "en");
    assert!(matches!(
        setting_pick(row, &values),
        Some(SettingPick::Act(Action::SetLang("ko")))
    ));

    // ── ko 복귀: 항등으로 돌아온다 ─────────────────────────────────────────
    assert_eq!(i18n::set_locale("ko"), "ko");
    assert_eq!(i18n::t("언어"), "언어");
    // 미지원 값은 폴백(ko)이다 — 깨진 영속 파일이 UI 를 이상한 상태로 못 만든다.
    assert_eq!(i18n::set_locale("fr"), "ko");

    // ── 시동 우선순위: 영속 > 설정 > 환경 ──────────────────────────────────
    let dir = std::env::temp_dir().join(format!("pytmux-i18n-switch-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let lang_file = dir.join("default.sock.lang");
    // 영속이 en 이면 설정이 ko 라도 en 이다.
    std::fs::write(&lang_file, "en").unwrap();
    assert_eq!(i18n::init(Some(lang_file.clone()), Some("ko")), "en");
    // 영속이 없으면 설정이 정한다.
    std::fs::remove_file(&lang_file).unwrap();
    assert_eq!(i18n::init(Some(lang_file.clone()), Some("ko")), "ko");
    // 첫 `lang` 선택이 영속을 만들고, 다음 시동은 그것을 읽는다 — 전체 왕복.
    i18n::persist(i18n::set_locale("en"));
    assert_eq!(std::fs::read_to_string(&lang_file).unwrap(), "en");
    assert_eq!(i18n::init(Some(lang_file.clone()), Some("ko")), "en");

    // ── 설정 파일 두 철자(`lang`·`language`) 를 다 읽는다 ─────────────────────
    assert_eq!(Config::parse("set lang en").lang, "en");
    assert_eq!(Config::parse("set language ko").lang, "ko");
    // 모르는 값은 "안 정했다"로 남는다 — 오타가 UI 언어를 굳히면 원인을 못 찾는다.
    assert_eq!(Config::parse("set lang klingon").lang, "");

    // 다음 프로세스는 없지만, 관례대로 ko 로 되돌려 둔다.
    i18n::set_locale("ko");
    let _ = std::fs::remove_dir_all(&dir);
}
