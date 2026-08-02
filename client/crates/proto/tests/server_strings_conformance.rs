//! 서버가 지어 보내는 글이 **이 클라에서 영어로 뜨는가**(로케일 ⓐ).
//!
//! 출처는 정본 카탈로그다(`scripts/gen_server_strings.py`). 여기서 다시 번역하면 두
//! 벌이 되고, 갈리는 순간 증상은 "한 화면만 한국어"다.
//!
//! # 두 갈래를 다르게 잰다
//!
//! - `fixed` — 자리가 없는 글. 우리는 **한국어 원문을 키로** 번역하므로 표에 있기만
//!   하면 프로토콜을 안 건드리고 영어로 뜬다. **전수 단언**한다.
//! - `formatted` — `{pct}%/5h 사용` 처럼 자리가 있는 글. 원문이 키가 못 된다(값이 매번
//!   다르다). 지금은 못 고치므로 **수를 래칫**으로 잡는다 — 늘면 그 CL 이 이유를 적게 된다.

use std::collections::BTreeMap;

/// `en` 로케일에서 한 번 재고 이 **스레드의** 로케일을 원래대로 돌린다.
///
/// ⚠ 종전에는 전역을 뒤집고 `Mutex` 로 감쌌는데, **그 잠금은 아무것도 못 막았다** —
/// 잠그는 쪽은 뒤집는 테스트뿐이고 읽는 쪽이 같은 잠금을 들 리가 없다. 게다가 이 파일
/// 자신도 아래 전수 단언에서는 잠금 없이 뒤집고 있어서, 병렬로 도는 형제 테스트가
/// 되돌린 `ko` 를 그 한가운데서 물려받아 **34개 전부가 한국어**로 떨어졌다
/// (2026-08-02 · 혼자 돌리면 초록이라 부하 플레이크로 읽히기 쉬운 모양이다).
///
/// ★ 이것이 로케일 ⓑ 분석이 서버에 대해 지적한 바로 그 위험이다("서버 `i18n` 로케일이
/// 프로세스 전역") — 테스트에서 먼저 났을 뿐이다. 그래서 이 저장소는 서버 쪽에서도
/// 전역을 뒤집는 대신 **재료를 실어 보내는 길**을 골랐다.
fn in_english<T>(f: impl FnOnce() -> T) -> T {
    base::i18n::with_locale("en", f)
}

#[derive(serde::Deserialize)]
struct Fixture {
    fixed: BTreeMap<String, String>,
    formatted: Vec<String>,
    /// `i18n.phrase` 로 **원문 포맷 + 인자**까지 실려 오는 것들(ko 포맷 → en 포맷).
    /// 실어 보내는 것만으로는 부족하다 — 그 **포맷 원문의 번역**이 우리 표에 있어야
    /// `tf` 가 영어를 짓는다. 아래 두 테스트가 그 둘을 나눠 잰다.
    carried: BTreeMap<String, String>,
}

fn fixture() -> Fixture {
    let raw = include_str!("fixtures/server_strings.json");
    serde_json::from_str(raw).expect("픽스처를 못 읽었다")
}

#[test]
fn every_fixed_string_the_server_ships_has_an_english_face() {
    let fx = fixture();
    assert!(
        !fx.fixed.is_empty(),
        "픽스처가 비었다 — 통과가 아니라 고장이다(생성기를 볼 것)"
    );
    // 단언은 블록 **밖**에서 한다 — 안에서 터뜨리면 실패 메시지를 짓는 동안에도
    // 이 스레드가 en 이라, 실패를 설명하는 글까지 언어가 뒤바뀐다.
    let missing = in_english(|| {
        let mut missing = Vec::new();
        for (ko, en) in fx.fixed.iter().chain(fx.carried.iter()) {
            let got = base::i18n::t(ko);
            if got != en {
                missing.push(format!("  {ko:?}\n    기대: {en:?}\n    실제: {got:?}"));
            }
        }
        missing
    });
    assert!(
        missing.is_empty(),
        "서버가 보내는 글 {}개가 영어로 안 뜬다 — `en_server.rs` 에 넣을 것\
         (정본 값은 픽스처에 있다):\n{}",
        missing.len(),
        missing.join("\n")
    );
}

/// 아직 **못 번역하는** 글의 수 = 합성된 것 − 재료로 실려 오는 것.
///
/// 16 → 12(2026-08-02n): claude-code 상태줄 넷이 `i18n.phrase` 로 옮겨갔다(M4 P6 후반).
/// 0 이 되는 날이 로케일 ⓑ 가 닫히는 날이다. 새로 생기는 것은 이 숫자를 올리게 되고,
/// 올리는 CL 이 "왜 또 하나 늘었나"를 적게 된다. **줄이는 길은 `i18n.phrase` 로 옮기는
/// 것뿐**이라, 이 숫자는 옮긴 만큼 정직하게 내려간다.
const UNTRANSLATABLE_TODAY: usize = 12;

#[test]
fn the_number_of_strings_we_cannot_translate_does_not_grow_silently() {
    let fx = fixture();
    let stuck: Vec<&String> = fx
        .formatted
        .iter()
        .filter(|s| !fx.carried.contains_key(*s))
        .collect();
    assert_eq!(
        stuck.len(),
        UNTRANSLATABLE_TODAY,
        "서버가 보내는 글 중 **아직 못 번역하는** 것의 수가 움직였다. 늘었으면 그 \
         문자열은 영어 사용자에게 한국어로 뜬다 — `i18n.phrase` 로 옮기거나, 이 \
         상수를 옮기며 이유를 적을 것. 남은 것:\n{stuck:#?}"
    );
    // 옮긴 것이 하나도 없으면 배관이 죽은 것이다(빈 결과는 통과가 아니다).
    assert!(
        !fx.carried.is_empty(),
        "재료로 실려 오는 글이 하나도 없다 — `i18n.phrase` 배관이 끊겼는지 볼 것"
    );
}

/// 재료가 오면 **그 자리에서 영어가 된다** — 배관이 실제로 도는지 잰다.
#[test]
fn a_carried_phrase_is_rebuilt_in_our_own_locale() {
    use proto::session::{I18nMap, Phrase, i18n_say};

    let mut map = I18nMap::new();
    map.insert(
        "text".to_owned(),
        Phrase {
            // 픽스처가 실어 오는 것과 같은 모양(한국어 원문 포맷 + 값).
            fmt: "다음 리셋까지 {left}".to_owned(),
            args: [("left".to_owned(), "01:23:45".to_owned())].into_iter().collect(),
        },
    );
    let got = in_english(|| i18n_say(&map, "text", "다음 리셋까지 01:23:45"));
    assert_eq!(
        got, "Until next reset 01:23:45",
        "재료가 왔는데 서버가 지은 글이 그대로 나왔다 — tf 경로가 안 도는 것이다"
    );
}

/// 재료가 **없으면** 서버가 지은 글 그대로다(구버전 서버·번역 대상이 아닌 글).
#[test]
fn without_the_ingredients_we_show_what_the_server_wrote() {
    use proto::session::{I18nMap, i18n_say};
    let got = in_english(|| i18n_say(&I18nMap::new(), "text", "서버가 지은 글"));
    assert_eq!(got, "서버가 지은 글", "재료가 없는데 글이 바뀌었다");
}
