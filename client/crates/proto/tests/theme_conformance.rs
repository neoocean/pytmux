//! 의미 색 **어휘 적합성** — 정본이 실을 수 있는 이름을 우리가 전부 아는가.
//!
//! # 왜 이 게이트인가
//!
//! 서버는 런에 색이 아니라 이름을 싣고(`{"theme": {"b": "primary"}}`), 각 클라가 자기
//! 테마에서 푼다. 그래서 **모르는 이름은 조용히 무색이 된다** — 예외도 로그도 없고,
//! 런에 실린 리터럴 글자색만 남는다. 어두운 리터럴이면 그 자리는 통째로 안 보인다.
//!
//! 실제로 그렇게 됐다(pytmux-16, 2026-08-03): ime-indicator 는 한글 배지에 `success`,
//! 영문 배지에 `primary` 를 쓰는데 우리 표에 `primary` 가 없어 `[EN]` 이 **검은 글자만**
//! 남았다. 캡처 화소로 확인 — 한글 컷 배지 바탕색 640화소, 영문 컷 **0화소**. 제보에는
//! "영문 모드에서는 배지가 사라진다"로 적혔고, 넉 달 동안 아무 오라클도 울지 않았다.
//!
//! 어휘의 정본은 `clientutil._THEME_FALLBACK` 의 키다(정본이 뜻을 아는 이름의 전부).
//! 생성기가 그것을 뽑아 픽스처에 넣고, 여기서 **전수**로 잰다.
//!
//! ⚠ 재는 것은 **"아는가"** 이지 "같은 색인가"가 아니다. GUI 는 tokyonight 로, 정본은
//! Textual 테마로 각자 푸는 것이 설계다(값이 아니라 이름이 옮겨 다닌다).

use proto::session::theme::{self, Resolution};

#[derive(serde::Deserialize)]
struct Fixture {
    /// 정본이 뜻을 아는 이름 전부(`_THEME_FALLBACK` 의 키).
    names: Vec<String>,
    /// 그중 지금 플러그인이 실제로 런에 싣는 것.
    emitted: Vec<String>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/theme_names.json")).expect("픽스처를 읽을 수 없다")
}

#[test]
fn the_theme_vocabulary_is_fully_known() {
    let fx = fixture();
    assert!(!fx.names.is_empty(), "픽스처가 비었다 — 생성기가 아무것도 안 뽑았다");
    let unknown: Vec<&String> = fx
        .names
        .iter()
        .filter(|n| theme::resolve(n) == Resolution::Unknown)
        .collect();
    assert!(
        unknown.is_empty(),
        "정본이 아는 이름을 우리가 모른다: {unknown:?}\n  \
         → `proto::session::theme::resolve` 에 줄을 더한다. 칠하지 않는 것이 정답이면 \
         `Resolution::Default` 로 **명시**할 것(그래야 '몰라서 무색'과 갈린다)."
    );
}

#[test]
fn every_name_a_plugin_actually_emits_gets_a_color() {
    // `emitted` 는 화면에 실제로 그려지는 이름들이라 `Default`(무색)여서는 안 된다 —
    // 단 `foreground` 만은 "기본 글자색"이라는 뜻 자체가 무색이다.
    let fx = fixture();
    for name in &fx.emitted {
        let r = theme::resolve(name);
        assert_ne!(r, Resolution::Unknown, "플러그인이 싣는 `{name}` 을 모른다");
        if name != "foreground" {
            assert!(
                matches!(r, Resolution::Color(_)),
                "플러그인이 `{name}` 으로 칠하라고 했는데 우리는 무색으로 둔다 — \
                 그 자리는 리터럴 글자색만 남아 어두운 바탕에서 안 보인다(pytmux-16)"
            );
        }
    }
}

#[test]
fn the_ime_badge_is_visible_in_both_modes() {
    // pytmux-16 의 **가장 작은 재현**. ime-indicator 의 표를 그대로 재현한다:
    //   _THEME = {"한": "success", "EN": "primary"}
    // 둘 다 바탕색을 얻어야 한다 — 한쪽만 얻으면 그 모드에서 배지가 사라진다.
    for (label, name) in [("한", "success"), ("EN", "primary")] {
        assert!(
            theme::color(name).is_some(),
            "`[{label}]` 배지의 바탕색(`{name}`)이 안 나온다 — 그 모드에서 배지가 사라진다"
        );
    }
    // 두 상태가 **다른 색**이라야 한다. 같으면 배지는 보이지만 한/영을 못 가른다.
    assert_ne!(
        theme::color("success"),
        theme::color("primary"),
        "한/영 배지가 같은 색이면 배지가 있으나 마나다"
    );
}

#[test]
fn an_unknown_name_is_reported_as_unknown_not_as_default() {
    // 이 구분이 없으면 위 게이트가 **아무것도 안 잰다**(모르는 이름도 `None` 이라
    // "일부러 무색"과 같아 보인다). 그 구분이 이 표의 요점이다.
    assert_eq!(theme::resolve("이런-이름은-없다"), Resolution::Unknown);
    assert_eq!(theme::resolve("foreground"), Resolution::Default);
    assert!(matches!(theme::resolve("success"), Resolution::Color(_)));
}
