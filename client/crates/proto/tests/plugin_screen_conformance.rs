//! 적합성 — 정본이 낼 수 있는 **플러그인 화면 모양**을 GUI 가 전부 아는가.
//!
//! # 왜 있나 (pytmux-33 ⓖ3 · 축 ⑶ "플러그인 화면")
//!
//! ⓖ3(전면 1:1 대조)이 "아직 못 재는 축" 넷을 셌고, **플러그인 화면**은 자가 아예
//! 없는 축이었다. 옆의 둘(`plugin_client_cmds`·`plugin_server_actions`)은 *"이 이름을
//! 치면 화면이 나오나"* 까지만 센다 — 나온 화면을 **그릴 줄 아는가**는 아무도 안
//! 물었다.
//!
//! 그 자리가 비면 어떻게 되는지는 이 저장소가 이미 안다. 선언형 화면 스펙의 계약은
//! `kind` 한 낱말이고 GUI 는 자기가 아는 모양만 그린다. 정본이 일곱째 모양을 내기
//! 시작하면 GUI 는 *"이 화면 모양은 아직 못 그립니다"* 한 줄을 띄우고 — 조용히
//! 버리지는 않지만(설계 §8-5) — **아무 게이트도 안 운다.** 사용자에게는 죽은 명령이고,
//! 그것이 ⓡ(`close-clock`)·pytmux-20 이 남긴 부류 그대로다.
//!
//! ⛔ 정본 훅의 독스트링은 아직도 `"kind": "list"|"text"` 둘이라고 적는데
//! (`pytmuxlib/plugins/__init__.py`) 실측하면 **여섯**이다. 글로 적힌 계약이 이미
//! 낡았다는 것이 이 자를 손으로 안 적는 이유다 — 세는 것은
//! `scripts/gen_plugin_screens.py` 가 정본 소스에서 한다.
//!
//! # 왜 GUI 의 **원문**을 읽나
//!
//! 두 뷰가 같은 스펙을 받고도 다르게 그리는데, GUI 쪽은 그린 것을 되읽을 수 없다
//! (`Scene` 은 글리프 id 만 들고 `pump_headless` 는 렌더를 안 돈다). pytmux-133 이
//! 같은 벽에서 쓴 방편이 **원문을 읽어 배선을 확인하는 가드**였고, 여기서도 그것을
//! 쓴다 — 값싸고, *"이 모양을 맡는 갈래가 있나"* 라는 이 자의 질문에는 정확히 맞다.
//!
//! ⚠ **이 자가 재는 것은 배선이지 그림이 아니다.** 갈래가 있는데 안 그리는 것은 못
//! 잡는다(그 축은 ⓖ3 의 ⑷ = 렌더 축이 든다).
//!
//! ⛔ `proto` 가 `gui` 를 코드로 의존하는 것이 아니다 — **글자로 읽을 뿐**이다.
//! 적합성 테스트가 전부 이 크레이트에 모여 있어(픽스처가 여기 있다) 여기 두었다.

use std::collections::BTreeSet;

/// 정본에서 뽑은 화면 모양들(`scripts/gen_plugin_screens.py`).
#[derive(serde::Deserialize)]
struct Fixture {
    kinds: Vec<String>,
    unresolved: Vec<String>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/plugin_screens.json"))
        .expect("plugin_screens.json 을 못 읽는다")
}

/// GUI 원문. 배선을 여기서 읽는다(모듈 주석의 "왜 원문을 읽나").
const GUI_SRC: &str = include_str!("../../gui/src/session_view.rs");

/// `match <머리> {` 블록의 몸통을 잘라 낸다 — 여는 중괄호부터 짝이 맞는 닫는 중괄호까지.
///
/// 정규식으로 갈래를 긁으면 **안쪽 match** 의 갈래까지 딸려 온다(목록 갈래 안의
/// `"open" => "▾ "` 가 그 부류다). 그래서 깊이를 세어 자른다.
fn match_body<'a>(src: &'a str, head: &str) -> &'a str {
    let at = src.find(head).unwrap_or_else(|| panic!("GUI 원문에서 `{head}` 를 못 찾는다"));
    let open = at + head.len();
    let mut depth = 1usize;
    for (i, ch) in src[open..].char_indices() {
        match ch {
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    return &src[open..open + i];
                }
            }
            _ => {}
        }
    }
    panic!("`{head}` 의 닫는 괄호를 못 찾는다");
}

/// 그 몸통에서 **바깥 깊이의** 글자 갈래(`"list" => …`)를 모은다.
fn arms(body: &str) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    let bytes: Vec<char> = body.chars().collect();
    let mut depth = 0usize;
    let mut i = 0usize;
    while i < bytes.len() {
        match bytes[i] {
            '{' | '(' | '[' => depth += 1,
            '}' | ')' | ']' => depth = depth.saturating_sub(1),
            '"' if depth == 0 => {
                // 갈래 머리 후보 — 닫는 따옴표까지 읽고 `=>` 가 뒤따르는지 본다.
                let start = i + 1;
                let mut j = start;
                while j < bytes.len() && bytes[j] != '"' {
                    if bytes[j] == '\\' {
                        j += 1;
                    }
                    j += 1;
                }
                let lit: String = bytes[start..j.min(bytes.len())].iter().collect();
                let rest: String = bytes[(j + 1).min(bytes.len())..].iter().take(8).collect();
                // `"a" | "b" =>` 도 갈래다 — 사이의 `|` 를 건너뛰고 본다.
                let rest = rest.trim_start();
                if rest.starts_with("=>") || rest.starts_with('|') {
                    out.insert(lit);
                }
                i = j;
            }
            _ => {}
        }
        i += 1;
    }
    out
}

/// GUI 가 **판으로 그리는** 모양들 — `render_plugin_view` 의 갈래.
fn drawn() -> BTreeSet<String> {
    arms(match_body(GUI_SRC, "match spec.kind.as_str() {"))
}

/// GUI 가 **이 클라의 물음판으로 넘기는** 모양들 — 화면이 열릴 때의 갈래.
///
/// 물음·확인은 판을 한 벌 더 만들지 않고 클라가 이미 잘하는 자리로 보낸다(원문 주석:
/// *"되돌릴 수 없는 것 앞의 규칙이 두 곳에 생긴다"*). 그러니 **그리는 갈래에 없는
/// 것이 정상**이고, 이 자는 둘을 합쳐서 센다.
fn routed() -> BTreeSet<String> {
    arms(match_body(GUI_SRC, "match kind.as_str() {"))
}

/// ★ 이 자의 본문 — 정본이 내는 모양을 GUI 가 하나도 빠짐없이 맡는가.
#[test]
fn every_canon_screen_kind_is_handled_by_the_gui() {
    let fx = fixture();
    let handled: BTreeSet<String> = drawn().union(&routed()).cloned().collect();
    let orphans: Vec<&String> = fx.kinds.iter().filter(|k| !handled.contains(*k)).collect();
    assert!(
        orphans.is_empty(),
        "정본이 내는 화면 모양인데 GUI 에 맡는 갈래가 없다: {orphans:?}\n\
         지금 GUI 가 아는 것 — 그리는 갈래 {:?} · 물음으로 넘기는 갈래 {:?}\n\
         (그대로 두면 그 명령은 GUI 에서 '아직 못 그립니다' 한 줄로 끝난다.\n\
          `session_view.rs` 에 갈래를 더하거나, 못 하는 이유를 이 파일에 적을 것.)",
        drawn(),
        routed()
    );
}

/// 반대쪽 — GUI 에만 있는 갈래는 **죽은 무게**다.
///
/// 정본이 안 내는 모양을 맡고 있으면 그 코드는 영영 안 돌면서 다음 사람에게
/// *"이것도 지원한다"* 고 거짓말한다. 고아 줄을 세는 규율은 `PALETTE_OURS` 의
/// `the_ours_only_allowlist_is_exact_and_sorted` 와 같다.
#[test]
fn the_gui_has_no_arm_for_a_kind_the_canon_never_sends() {
    let fx = fixture();
    let canon: BTreeSet<&str> = fx.kinds.iter().map(String::as_str).collect();
    let mine: BTreeSet<String> = drawn().union(&routed()).cloned().collect();
    let extra: Vec<&String> = mine.iter().filter(|k| !canon.contains(k.as_str())).collect();
    assert!(
        extra.is_empty(),
        "정본이 안 내는 모양을 GUI 가 맡고 있다: {extra:?}\n\
         정본이 그 모양을 그만 냈다면 갈래도 지울 것 \
         (`python3 client/scripts/gen_plugin_screens.py` 로 픽스처를 다시 뽑았는지 먼저 볼 것)."
    );
}

/// 모르는 모양을 **조용히 버리지 않는다**(설계 §8-5).
///
/// 위 둘이 초록이어도 이 갈래가 없으면 다음에 정본이 앞서 나간 순간 사용자는 **빈
/// 판**을 보고 자기가 잘못 골랐다고 읽는다. 게이트가 우는 것과 사용자가 아는 것은
/// 다른 일이라 둘 다 있어야 한다.
#[test]
fn an_unknown_kind_still_tells_the_user() {
    let body = match_body(GUI_SRC, "match spec.kind.as_str() {");
    assert!(
        body.contains("other =>"),
        "`render_plugin_view` 에 모르는 모양을 받는 갈래가 없다 — \
         일곱째 모양이 오면 빈 판이 뜬다(설계 §8-5)."
    );
}

/// 픽스처가 *"전수로 셌다"* 고 말할 자격이 있나.
///
/// 생성기는 정본 소스를 훑어 `kind` 를 모으는데, 글자가 아닌 값이 실리는 자리는
/// 못 푼다. 그런 자리를 **조용히 빼면** 이 파일의 다른 단언이 전부 거짓 초록이
/// 된다(저장소 규율: 파싱 실패를 초록으로 위장하지 않는다).
#[test]
fn the_fixture_resolved_every_kind_slot() {
    let fx = fixture();
    assert!(
        fx.unresolved.is_empty(),
        "정본에서 화면 모양을 못 푼 자리가 있다:\n  {}\n\
         그 자리가 남아 있는 한 이 파일의 '전수' 단언은 참이 아니다.",
        fx.unresolved.join("\n  ")
    );
}

/// 고르는 화면과 읽는 화면의 갈림이 정본의 모양과 어긋나지 않았나.
///
/// `is_selectable` 은 **뷰가 core 에 넘기는 한 비트**다(목록을 여나, 글을 여나).
/// 정본이 내는 모양마다 그 답이 정해져 있어야 하고, 특히 `prompt`·`confirm` 은
/// 판 자체를 안 여는 모양이라 여기서 참이면 **빈 목록판이 뒤에 남는다.**
#[test]
fn the_selectable_bit_matches_what_each_kind_is_for() {
    for kind in fixture().kinds {
        let spec = proto::session::PluginScreen { kind: kind.clone(), ..Default::default() };
        // ★ 다열 판(`panel`)도 **고르는 화면**이다 — 목록을 여러 열로 흘려 담은 것뿐이라
        //   커서가 있고 `Enter` 에 뜻이 있다(pytmux-126).
        let expected = matches!(kind.as_str(), "list" | "table" | "form" | "panel");
        assert_eq!(
            spec.is_selectable(),
            expected,
            "`{kind}` 의 고르기 비트가 뜻과 어긋난다 \
             (목록·표·폼·다열은 고르는 화면, 글·물음·확인은 아니다)"
        );
    }
}
