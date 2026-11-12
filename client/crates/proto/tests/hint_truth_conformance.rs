//! 자 — **꼬리줄의 진실**(pytmux-655): 판의 제목·꼬리줄이 말하는 키를 GUI 판이 실제로 받는가.
//!
//! 648 이 판의 제목·꼬리줄을 정본 글로 맞췄다. 글이 같아도 **그 글이 말하는 키를 GUI 판이 안
//! 받으면** 글이 거짓말을 한다(Layouts 판이 «←→ 값» 이라고 하는데 목록이라 ←→ 를 삼켰다).
//! 종전 자들은 이 물음을 안 묻는다 — `screen_key_conformance` 는 정본 `on_key` 의 키를 재고,
//! `interaction` 은 «제 것 아닌 키» 를 잰다. 꼬리줄의 **글**에서 출발하는 자는 없었다.
//!
//! # 어떻게 재나
//!
//! - 모집단 = `Screen::all()` × 그 판의 `title()`·`hint()`(한국어 — 정본 글)에서 뽑은 키 낱말.
//! - 판마다 `open_screen::opened` 로 **제대로** 열고(두 축과 같은 여는 길) 목록 판에는 줄 30 ·
//!   보이는 줄 5 를 준다(뷰가 키 직전에 넣는 그 값 — `set_list_view`).
//! - 키를 누르기 전·후의 판 상태(`Screens` 의 `Debug` 전부)와 결과(`ScreenKey`)를 본다.
//!   **«일어났다»** = 결과가 `Consumed` 가 아니거나(닫힘·고름·값 바꿈·복사…) 판 상태가 바뀜.
//!   ↑ · PgUp · Home · ← · Shift+Tab 은 **반대 키를 먼저** 눌러 둔다(맨 위에서 ↑ 는 원래
//!   아무 일도 없다 — 그것은 못 받는 것이 아니다).
//! - 「아무 키나 닫기」 는 낯선 글자 하나로, 「타이핑 검색」 은 글자 하나로 잰다.
//! - **클릭**은 판의 클릭 자리(`Screens::panel_click(PanelTarget)` — 뷰가 마우스 자리를 이 값으로
//!   바꿔 넘기는 한 입구)를 눌러 잰다([`CLICK_TARGETS`]). 거기 자리가 없는 판의 «클릭» 은 운다.
//!   ⚠ 마우스 좌표 → 자리 변환(뷰의 히트 박스)은 이 자 밖이다 — 그것은 GUI 시험의 몫이다.
//!
//! 못 받는 키는 [`NOT_HONOURED`] 에 이슈와 함께 적는다 — 상한이 있고 **올리지 않는다**(목표 0).

#[path = "common/open_screen.rs"]
mod open_screen;

use base::keys::{Key, Mods};
use base::screens::{Screen, ScreenKey, Screens};

/// 꼬리줄이 말하는데 판이 안 받는 키 — (판, 키 낱말, 이슈). 상한을 올리지 않는다.
const NOT_HONOURED: &[(Screen, &str, &str)] = &[];
const NOT_HONOURED_CEILING: usize = 0;

/// «클릭» 을 말하는 판과 그 판의 **클릭 자리**(`Screens::panel_click` 이 받는 것 — 뷰는 마우스가
/// 떨어진 자리를 이 값으로 바꿔 넘긴다). 꼬리줄의 «클릭 탭»·«클릭 카테고리» 는 둘째 탭을 눌렀을 때
/// 판 상태가 바뀌어야 참이다.
const CLICK_TARGETS: &[(Screen, base::screens::PanelTarget)] = &[
    (Screen::Commands, base::screens::PanelTarget::PaletteTab(1)),
    (Screen::Settings, base::screens::PanelTarget::SettingsCat(1)),
];

/// 낱말 하나가 부르는 키(들). 모두 «일어나야» 한다.
#[derive(Debug, Clone)]
enum Probe {
    Keys(Vec<Key>),
    AnyKey,
    Typing,
    Click,
}

/// 판의 글에서 키 낱말을 뽑는다 — (낱말, 잴 것).
fn words(text: &str) -> Vec<(String, Probe)> {
    let mut out: Vec<(String, Probe)> = Vec::new();
    let mut push = |w: &str, p: Probe| {
        if !out.iter().any(|(x, _)| x == w) {
            out.push((w.to_owned(), p));
        }
    };
    if text.contains("↑↓") {
        push("↑↓", Probe::Keys(vec![Key::Down, Key::Up]));
    }
    if text.contains("←→") {
        push("←→", Probe::Keys(vec![Key::Right, Key::Left]));
    }
    if text.contains("Enter") || text.contains('⏎') {
        push("Enter", Probe::Keys(vec![Key::Enter]));
    }
    if text.contains("Esc") {
        push("Esc", Probe::Keys(vec![Key::Escape]));
    }
    if text.contains("Shift+Tab") {
        push("Shift+Tab", Probe::Keys(vec![Key::BackTab]));
    }
    if text.replace("Shift+Tab", "").contains("Tab") {
        push("Tab", Probe::Keys(vec![Key::Tab]));
    }
    if text.contains("Home/End") {
        push("Home/End", Probe::Keys(vec![Key::End, Key::Home]));
    }
    if text.contains("PgUp/PgDn") {
        push("PgUp/PgDn", Probe::Keys(vec![Key::PageDown, Key::PageUp]));
    }
    if text.contains("Space") {
        push("Space", Probe::Keys(vec![Key::Char(' ')]));
    }
    if text.contains("아무 키나") {
        push("아무 키나", Probe::AnyKey);
    }
    if text.contains("타이핑") || text.contains("치면") {
        push("타이핑", Probe::Typing);
    }
    if text.contains("클릭") {
        push("클릭", Probe::Click);
    }
    // 글자 키 — 「 c 복사」·「y/n」·「h/v 방향」·「r 원격 제어」 처럼 ASCII 소문자 한 글자가 낱말로 선다.
    let chars: Vec<char> = text.chars().collect();
    for (i, ch) in chars.iter().enumerate() {
        if !ch.is_ascii_lowercase() {
            continue;
        }
        let before = if i == 0 { ' ' } else { chars[i - 1] };
        let after = chars.get(i + 1).copied().unwrap_or(' ');
        let sep = |c: char| c.is_whitespace() || "(/·".contains(c);
        let after_ok = after.is_whitespace() || after == '/' || after == ')';
        if sep(before) && after_ok {
            let w = ch.to_string();
            push(&w, Probe::Keys(vec![Key::Char(*ch)]));
        }
    }
    out
}

fn fresh(screen: Screen) -> Screens {
    let mut s = open_screen::opened(screen);
    s.set_list_view(5, 30);
    s
}

/// 키 하나가 무엇이든 «일으켰나».
fn happened(screen: Screen, primer: Option<Key>, key: Key) -> bool {
    let mut s = fresh(screen);
    if let Some(p) = primer {
        let _ = s.press(p, Mods::NONE);
        if s.top() != Some(screen) {
            // 반대 키가 판을 닫았다 — 그 키 자체는 따로 잰다. 깨끗한 판에서 바로 잰다.
            s = fresh(screen);
        }
    }
    let before = format!("{s:?}");
    let outcome = s.press(key, Mods::NONE);
    let moved = format!("{s:?}") != before;
    moved || !matches!(outcome, Some(ScreenKey::Consumed) | None)
}

/// 거꾸로 가는 키는 반대 키를 먼저 눌러 둔다.
fn primer_of(key: Key) -> Option<Key> {
    match key {
        Key::Up => Some(Key::Down),
        Key::Left => Some(Key::Right),
        Key::Home => Some(Key::End),
        Key::PageUp => Some(Key::PageDown),
        Key::BackTab => Some(Key::Tab),
        // 원격 머지의 방향 `h`/`v` — 이미 그 방향이면 아무 일도 없는 것이 맞다. 반대쪽을 먼저.
        Key::Char('v') => Some(Key::Char('h')),
        Key::Char('h') => Some(Key::Char('v')),
        _ => None,
    }
}

fn measure(screen: Screen) -> Vec<(String, Result<(), String>)> {
    let text = format!("{} {}", screen.title(), screen.hint());
    words(&text)
        .into_iter()
        .map(|(word, probe)| {
            let got = match probe {
                Probe::Keys(keys) => {
                    let dead: Vec<String> = keys
                        .iter()
                        .filter(|k| !happened(screen, primer_of(**k), **k))
                        .map(|k| format!("{k:?}"))
                        .collect();
                    if dead.is_empty() { Ok(()) } else { Err(format!("아무 일도 없는 키 {dead:?}")) }
                }
                Probe::AnyKey => {
                    let mut s = fresh(screen);
                    let _ = s.press(Key::Char('z'), Mods::NONE);
                    if s.top() == Some(screen) { Err("낯선 글자에 안 닫혔다".into()) } else { Ok(()) }
                }
                Probe::Typing => {
                    if happened(screen, None, Key::Char('a')) { Ok(()) } else { Err("글자를 쳐도 아무 일도 없다".into()) }
                }
                Probe::Click => match CLICK_TARGETS.iter().find(|(s, _)| *s == screen) {
                    None => Err("«클릭» 을 말하는데 클릭 자리를 안 댔다(`CLICK_TARGETS`)".into()),
                    Some((_, target)) => {
                        let mut s = fresh(screen);
                        let before = format!("{s:?}");
                        let _ = s.panel_click(*target);
                        if format!("{s:?}") != before { Ok(()) } else { Err(format!("{target:?} 를 눌러도 아무 일도 없다")) }
                    }
                },
            };
            (word, got)
        })
        .collect()
}

#[test]
fn every_key_a_hint_names_actually_does_something() {
    let mut wrong = Vec::new();
    let mut stale = Vec::new();
    let mut measured = 0;
    for screen in Screen::all() {
        for (word, got) in measure(*screen) {
            measured += 1;
            let declared = NOT_HONOURED.iter().find(|(s, w, _)| s == screen && *w == word);
            match (got, declared) {
                (Err(why), None) => wrong.push(format!("{screen:?} «{word}»: {why}")),
                (Ok(()), Some((_, _, issue))) => stale.push(format!("{screen:?} «{word}»({issue}) — 이제 받는다")),
                _ => {}
            }
        }
    }
    assert!(measured >= 60, "잰 낱말이 {measured} 개뿐이다 — 낱말 뽑기가 헛돈다");
    assert!(wrong.is_empty(), "꼬리줄이 말하는데 판이 안 받는 키:\n  {}", wrong.join("\n  "));
    assert!(stale.is_empty(), "낡은 예외:\n  {}", stale.join("\n  "));
    assert!(NOT_HONOURED.len() <= NOT_HONOURED_CEILING, "상한을 넘었다(올리지 않는다)");
    assert_eq!(NOT_HONOURED.len(), NOT_HONOURED_CEILING, "줄었다 — 상한을 내릴 것");
}

#[test]
fn the_word_picker_reads_the_canon_hints() {
    let names = |t: &str| words(t).into_iter().map(|(w, _)| w).collect::<Vec<_>>();
    assert_eq!(
        names("(↑↓ PgUp/PgDn Home/End 이동 · ⏎ 전문 · c 복사 · Esc 닫기)"),
        vec!["↑↓", "Enter", "Esc", "Home/End", "PgUp/PgDn", "c"]
    );
    assert_eq!(names("(Tab 다음 · Shift+Tab 이전 · Enter 전환 · Esc 취소)"), vec!["Enter", "Esc", "Shift+Tab", "Tab"]);
    assert_eq!(names("원격 탭 머지 → 가로 │ · Enter 선택 · h/v 방향 · Esc"), vec!["Enter", "Esc", "h", "v"]);
    assert_eq!(names("(←→ 이동 · Enter 확정 · y/n · Esc 취소)"), vec!["←→", "Enter", "Esc", "y", "n"]);
}

#[test]
fn the_ruler_bites_a_key_that_does_nothing() {
    // 양성 짝: 판이 정말로 아무것도 안 하는 키는 «없음» 으로 잰다(F12 를 쓰는 판은 없다).
    assert!(!happened(Screen::Version, None, Key::Function(12)) || {
        // Version 판은 아무 키나 닫는다 — 닫으면 «일어났다» 가 맞다.
        let mut s = fresh(Screen::Version);
        let _ = s.press(Key::Function(12), Mods::NONE);
        s.top() != Some(Screen::Version)
    });
    // 설정 판에서 F12 는 판을 안 닫고 아무것도 안 바꾼다.
    assert!(!happened(Screen::Settings, None, Key::Function(12)), "F12 가 설정 판에서 무언가 했다고 잰다");
}

#[test]
fn print_the_hint_truth() {
    for screen in Screen::all() {
        let row: Vec<String> = measure(*screen)
            .into_iter()
            .map(|(w, g)| format!("{w}{}", if g.is_ok() { "" } else { "✗" }))
            .collect();
        println!("{screen:?}: {}", row.join(" · "));
    }
}
