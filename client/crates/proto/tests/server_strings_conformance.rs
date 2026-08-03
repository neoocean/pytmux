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
    /// 원문 포맷 + 인자까지 실려 오는 것들(ko 포맷 → en 포맷). 길이 둘이다 —
    /// 스펙·셀·배지는 `i18n.phrase`, 알림은 `_notice_msg` 가 자기가 받은 ko 포맷을
    /// 그대로 싣는다. 실어 보내는 것만으로는 부족하다 — 그 **포맷 원문의 번역**이 우리
    /// 표에 있어야 `tf` 가 영어를 짓는다. 아래 두 테스트가 그 둘을 나눠 잰다.
    carried: BTreeMap<String, String>,
    /// 화면 스펙에 **직접 적힌** 한국어 — 카탈로그를 안 거쳐서 영어 표에 못 들어간다.
    /// 파일:줄 과 문구가 함께 온다(고칠 자리를 바로 가리키려고).
    wire_literals: Vec<String>,
    /// 위 목록을 **어디서** 찾았나(파일:함수). 목록이 0 인 지금, 이것이 "다 옮겼다"와
    /// "스캐너가 눈을 감았다"를 가르는 유일한 증거다.
    wire_scanned: Vec<String>,
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
/// 16 → 12(2026-08-02n) → **0**(2026-08-02o). 마지막 걸음은 두 가지였다:
/// ⑴ 알림(`_notice_msg`)도 재료를 싣게 했다 — `key`+`kw` 는 정본 클라의 도메인 키라
///    우리에겐 무용지물이었다(우리 카탈로그는 한국어 원문이 키다).
/// ⑵ ★ **세는 자리를 고쳤다.** 12 중 **일곱은 소켓을 안 건너는 글**이었다(클라 로컬
///    Textual 화면·`display_message`)이고 하나는 아무 데서도 안 쓰는 죽은 항목이었다.
///    네임스페이스로 세면 그런 것까지 "영어 사용자에게 한국어로 뜬다"로 집계된다 —
///    이제 생성기가 **짓는 코드**(스펙·셀·배지·알림)에서 센다.
///
/// 0 이 됐다고 로케일이 끝난 것은 아니다. 같은 CL 이 드러낸 다른 축이 아래
/// [`WIRE_LITERALS_TODAY`] 다.
const UNTRANSLATABLE_TODAY: usize = 0;

/// 화면 스펙에 **직접 적힌** 한국어의 수(=카탈로그를 안 거쳐 영어 표에 못 들어간 것).
///
/// ★ 이 축은 2026-08-02o 에 생겼는데, **생기기 전까지 22개가 게이트 밖에 있었다**.
/// 픽스처가 카탈로그에서 뽑히니, 스펙에 손으로 적은 한국어는 생성기의 눈에 아예 안
/// 보였다 — 영어 사용자에게 그대로 한국어로 뜨는데 게이트는 초록이었다. 같은 CL 에서
/// 넷(p4changes·ncd·prompt-history·claude-resume)을 카탈로그로 옮겨 22 → 10 이었다.
///
/// **10 → 0 (2026-08-02p)**: 남은 열은 전부 `mdir` 이었다. 다만 그 열을 옮기려고 자를
/// 대 보니 **자가 짧았다** — 스캐너가 wire dict 를 짓는 함수 안만 보고 있어서, 같은
/// 파일의 모듈 레벨 표(`_REASONS` 10 · `_VERBS` 5 · 안내줄)와 한 겹 위·아래의 함수
/// (`_begin`·`_apply`·`_result_note`)에 있던 **같은 성질의 글 34개**가 수에 안 잡혔다.
/// 스캐너를 고치니 10 이 아니라 **44** 였고, 그 44 를 옮겨 0 이 됐다.
///
/// 그래서 이 0 은 "mdir 을 옮겼다"보다 **"안 세지는 자리로 피할 길을 좁혔다"** 가
/// 값이다 — 옮기기 전에 자부터 고치지 않았으면 34개가 조용히 남았다.
const WIRE_LITERALS_TODAY: usize = 0;

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

/// 스펙에 **직접 적힌** 한국어의 수 — 늘리는 CL 이 이유를 적게 한다.
///
/// 위 두 테스트가 카탈로그를 재는 동안 이 축은 **카탈로그 밖**을 잰다. 둘을 같이 두는
/// 이유가 그것이다: 카탈로그만 재면 "표에 있는 것은 다 영어다"는 참인데 화면은 한국어일
/// 수 있다(2026-08-02o 실측).
#[test]
fn nobody_writes_korean_straight_into_a_screen_spec() {
    let fx = fixture();
    assert_eq!(
        fx.wire_literals.len(),
        WIRE_LITERALS_TODAY,
        "화면 스펙에 직접 적힌 한국어의 수가 움직였다. 늘었으면 그 문구는 영어 \
         사용자에게 한국어로 뜬다 — 카탈로그(`i18n.register`)로 옮기고 `i18n.t`/\
         `i18n.phrase` 로 실을 것. 지금 남은 것:\n{:#?}",
        fx.wire_literals
    );
}

/// **0 은 두 가지 뜻이 될 수 있다** — 다 옮겼거나, 스캐너가 아무 데도 안 봤거나.
///
/// 위 테스트가 0 을 요구하게 된 순간부터, 생성기의 AST 스캔이 조용히 망가지면(파서
/// 예외를 삼키거나, 함수 판별이 낡거나) 이 게이트는 **영원히 초록**이다. 그래서 훑은
/// 자리의 목록을 같이 싣고, 그것이 비지 않았는지 · 스펙을 짓는 자리들이 실제로 들어
/// 있는지를 잰다(빈 결과는 통과가 아니다 — 라이선스 게이트가 밟은 그 함정).
#[test]
fn the_scanner_still_looks_at_the_places_that_build_screens() {
    let fx = fixture();
    assert!(
        !fx.wire_scanned.is_empty(),
        "스캐너가 훑은 자리가 하나도 없다 — 통과가 아니라 고장이다(생성기를 볼 것)"
    );
    // 화면을 짓는 자리는 **함수 이름으로** 찾는다(줄 번호·순서가 아니라) — 표가
    // 재정렬돼도 안 낡는다. 셋 다 다른 플러그인이라 하나가 죽어도 드러난다.
    // ⚠ `ncd` 의 자리 이름은 2026-08-04(pytmux-11 B)에 바뀌었다 — 그 화면이 평면
    //    목록에서 **트리**가 되면서 `_dir_spec` 이 `_tree_spec` 이 됐다. 앵커가
    //    이름이라 그때 이 줄이 울었고, 그것이 이 오라클이 하는 일이다.
    for want in ["mdir/__init__.py:_spec", "ncd/__init__.py:_tree_spec",
                 "mdir/__init__.py:_result_note"] {
        assert!(
            fx.wire_scanned.iter().any(|s| s.ends_with(want)),
            "스펙을 짓는 자리 {want:?} 가 훑은 목록에서 사라졌다 — 스캐너가 한 겹을 \
             다시 못 보게 됐는지 볼 것(그렇게 되면 `wire_literals` 0 은 거짓이다). \
             지금 목록:\n{:#?}",
            fx.wire_scanned
        );
    }
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
