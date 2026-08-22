//! 교차구현 적합성 — 팝업이 **정본과 같은 자리**에 서는가.
//!
//! # 왜 자리를 재나
//!
//! 정본은 두 달을 쓰며 화면마다 설 자리를 정해 뒀다(사용자 지시 2026-08-01: "자잘한
//! 레이아웃은 의도를 가지고 튜닝되어 있다"). 그 의도는 CSS 한 낱말(`align: center
//! bottom`)에 들어 있고, 낱말이라 **옮기다 빠뜨려도 아무 소리가 안 난다**.
//!
//! 실제로 셋이 제각각이었다 — 정본은 화면마다 다르고, GUI 는 전부 가운데, TUI 는 전부
//! 위였다. 그래서 `esc :` 프롬프트가 정본에서는 바닥인데 GUI 에서는 가운데 떴다.
//!
//! # 무엇을 재나
//!
//! 우리 화면 하나하나가 대응하는 정본 클래스(`Screen::canon_class`)의 앵커와 같은가.
//! 픽스처는 정본 CSS 에서 뽑는다(`scripts/gen_screen_anchor_fixture.py`) — 정본이 자리를
//! 바꾸면 여기가 먼저 운다.

use base::screens::{Anchor, Screen};
use serde::Deserialize;
use std::collections::BTreeMap;

#[derive(Deserialize)]
struct Fx {
    anchors: BTreeMap<String, String>,
    /// 클래스 CSS 를 **호출이 뒤집는** 자리 — `클래스` → {`title` → 앵커}(§10-21ⓐ3).
    overrides: BTreeMap<String, BTreeMap<String, String>>,
    docks: BTreeMap<String, BTreeMap<String, String>>,
    prompt_order: Vec<String>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/screen_anchors.json"))
        .expect("픽스처를 못 읽었다 — scripts/gen_screen_anchor_fixture.py 로 다시 뽑을 것")
}

/// 우리 화면 전부. **와일드카드 없는 match** 가 누락을 컴파일로 막는다 — 화면을 하나
/// 더하면 여기서 막히고, 그때 "이 판은 어디에 서나"를 한 번은 묻게 된다.
fn all_screens() -> Vec<Screen> {
    let all = vec![
        Screen::Keys,
        Screen::ClaudeDetail,
        Screen::Tabs,
        Screen::Tree,
        Screen::Buffers,
        Screen::Prompt,
        Screen::Confirm,
        Screen::Commands,
        Screen::Version,
        Screen::ShellOutput,
        Screen::RestartCheck,
        Screen::MergeRemote,
        Screen::Layouts,
        Screen::Notices,
        Screen::Menu,
        Screen::Plugins,
        Screen::Options,
        Screen::Hooks,
        Screen::InfoTabs,
        Screen::Compose,
        Screen::Settings,
        Screen::SearchResults,
    ];
    // 변형을 빠뜨리면 이 화면은 아래 검사를 한 번도 안 받는다(`keymap` 의 액션 전수와
    // 같은 장치). 숫자를 박는 대신 **중복 없음 + 개수**로 잡는다.
    let mut seen: Vec<String> = all.iter().map(|s| format!("{s:?}")).collect();
    seen.sort();
    let before = seen.len();
    seen.dedup();
    assert_eq!(before, seen.len(), "목록에 같은 화면이 두 번 있다");
    all
}

fn parse(word: Option<&str>, what: &str) -> Anchor {
    match word {
        Some("top") => Anchor::Top,
        Some("middle") => Anchor::Middle,
        Some("bottom") => Anchor::Bottom,
        other => panic!("정본 픽스처에 {what} 의 앵커가 없다: {other:?}"),
    }
}

/// 정본이 이 판을 어디에 세우나 — **클래스 CSS 를 호출 인자가 덮어쓴다**.
///
/// 덮어쓰기를 안 보면 `InfoScreen` 으로 뜨는 판이 전부 위라고 읽는다. 정본은 짧은 판
/// (`version`)만 `center=True` 로 가운데 세운다.
fn want(fx: &Fx, class: &str, variant: Option<&str>) -> Anchor {
    if let Some(name) = variant {
        let over = fx.overrides.get(class).and_then(|m| m.get(name));
        return parse(
            over.map(String::as_str),
            &format!("{class}({name}) 덮어쓰기"),
        );
    }
    parse(fx.anchors.get(class).map(String::as_str), class)
}

#[test]
fn the_fixture_is_not_empty() {
    let fx = fixture();
    assert!(!fx.anchors.is_empty(), "앵커가 비었다 — 통과가 아니라 고장이다");
    assert!(!fx.prompt_order.is_empty(), "프롬프트 차례가 비었다");
}

#[test]
fn every_screen_stands_where_canon_puts_it() {
    let fx = fixture();
    for screen in all_screens() {
        let Some(class) = screen.canon_class() else {
            continue; // 네이티브 전용 — 대조할 정본이 없다(그 사실은 아래 테스트가 잰다).
        };
        assert_eq!(
            screen.anchor(),
            want(&fx, class, screen.canon_variant()),
            "{screen:?}({class}) 가 정본과 다른 자리에 선다"
        );
    }
}

/// ★ 덮어쓰기를 **선언한 화면이 실제로 있어야** 한다.
///
/// 정본이 `center=True` 를 지우면 픽스처의 덮어쓰기가 사라지고, 그러면 위 테스트가
/// "픽스처에 앵커가 없다"로 죽는다 — 그 죽음은 읽기 어렵다. 여기서 먼저 "정본이 예외를
/// 없앴다"로 말해 준다.
#[test]
fn every_declared_override_still_exists_in_canon() {
    let fx = fixture();
    for screen in all_screens() {
        let (Some(class), Some(variant)) = (screen.canon_class(), screen.canon_variant()) else {
            continue;
        };
        assert!(
            fx.overrides.get(class).is_some_and(|m| m.contains_key(variant)),
            "{screen:?} 가 정본 {class}({variant}) 의 덮어쓰기를 따른다고 선언했는데 \
             정본에는 그 예외가 없다 — 정본이 없앴으면 canon_variant 도 지울 것"
        );
    }
}

/// 반대 방향 — 정본이 예외를 **새로 만들었는데** 우리가 안 따라간 자리.
///
/// 이쪽이 없으면 정본만 조용히 움직인다(그게 이 픽스처가 생긴 이유 자체다).
#[test]
fn every_canon_override_is_followed() {
    let fx = fixture();
    let declared: Vec<(&str, &str)> = all_screens()
        .iter()
        .filter_map(|s| Some((s.canon_class()?, s.canon_variant()?)))
        .collect();
    let missing: Vec<String> = fx
        .overrides
        .iter()
        .flat_map(|(class, m)| m.keys().map(move |name| (class.as_str(), name.as_str())))
        .filter(|pair| !declared.contains(pair))
        .map(|(class, name)| format!("{class}({name})"))
        .collect();
    assert!(
        missing.is_empty(),
        "정본이 CSS 를 뒤집는데 우리 화면 어느 것도 안 따라간다: {missing:?}\n\
         그 판을 맡은 Screen 에 canon_variant 를 선언할 것."
    );
}

/// 정본 화면 중 **우리가 안 맡은 것**이 조용히 없어지지 않게.
///
/// 예외에는 이유가 붙어야 한다 — 이유 없는 예외는 그냥 빠뜨린 것이다.
#[test]
fn every_canon_screen_is_claimed_or_excused() {
    /// (정본 클래스, 왜 우리에겐 없나)
    const EXCUSED: &[(&str, &str)] = &[
        // 정본은 설정 값 입력을 별도 모달로 띄우지만 우리는 **같은 Prompt 화면**을 쓴다
        // (패리티 표 `_SettingInputScreen` 행과 같은 판정). 그래서 우리 쪽 앵커는 하나뿐이다.
        ("_SettingInputScreen", "우리는 설정 값 입력도 Prompt 화면 하나로 받는다"),
    ];
    let fx = fixture();
    let claimed: Vec<&str> = all_screens().iter().filter_map(|s| s.canon_class()).collect();
    let missing: Vec<&String> = fx
        .anchors
        .keys()
        .filter(|c| !claimed.contains(&c.as_str()))
        .filter(|c| !EXCUSED.iter().any(|(name, _)| *name == c.as_str()))
        .collect();
    assert!(
        missing.is_empty(),
        "정본에 있는데 우리 화면 어느 것도 안 맡았다 {missing:?}\n\
         맡을 화면이 없으면 EXCUSED 에 **이유와 함께** 적을 것."
    );
}

/// ★ 프롬프트 안 **요소 차례** — 후보가 위, 입력이 아래.
///
/// 정본이 이 차례를 그렇게 못박은 이유를 저쪽 주석이 적어 둔다: 둘 다 바닥 고정이라
/// 적층 순서가 Textual 버전에 따라 뒤집혔고, 그러면 **모바일에서 후보가 키보드에 가렸다**.
/// 그래서 컨테이너 정상 흐름으로 순서를 고정했다(사용자 요청).
///
/// 자리(바닥)만 맞추고 차례가 뒤집혀 있으면 같은 자리에 다른 화면이 서는 셈이다.
#[test]
fn the_prompt_puts_candidates_above_the_input() {
    let fx = fixture();
    let cand = fx.prompt_order.iter().position(|id| id == "pcand");
    let row = fx.prompt_order.iter().position(|id| id == "prow");
    let (cand, row) = (cand.expect("후보(#pcand)"), row.expect("입력(#prow)"));
    assert!(cand < row, "정본 픽스처가 뒤집혔다: {:?}", fx.prompt_order);
    // 그리고 그 묶음이 **바닥 고정**이라야 뜻이 산다.
    assert_eq!(
        fx.docks.get("PromptScreen").and_then(|d| d.get("pwrap")).map(String::as_str),
        Some("bottom"),
        "프롬프트 묶음이 바닥 고정이 아니다"
    );
    // 우리 쪽 계약: 같은 사실을 core 가 들고 있어야 뷰가 그것을 보고 그린다.
    assert!(
        Screen::Prompt.candidates_above_input(),
        "core 가 '후보가 위'를 모른다 — 뷰가 각자 정하면 한쪽만 뒤집힌다"
    );
    assert_eq!(Screen::Prompt.anchor(), Anchor::Bottom);
}
