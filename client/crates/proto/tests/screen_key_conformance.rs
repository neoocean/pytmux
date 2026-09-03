//! **화면 키 적합성** — 정본의 `on_key` 를 기계로 읽어 우리 판에 **실제로 눌러** 본다
//! (pytmux-454).
//!
//! # 왜 이 자가 필요한가
//!
//! 옆의 상호작용 계약([`interaction.rs`])은 화면마다 **키 하나**(F5)를 눌러 보고, 「정본과
//! 같은가」는 사람이 적는 [`Verdict`] 선언이었다. 그 선언은 첫 회차에 **여덟 중 일곱이
//! 거짓**이었다(pytmux-273) — 정본 쪽을 기계로 읽는 자가 없어서다.
//!
//! 모드 전이 축(`mode_transition_conformance`)이 세션 뷰에 대해 한 일을 이 자가 **화면**에
//! 대해 한다: 정본 `clientscreens.py` 의 화면 클래스 `on_key`·`BINDINGS`·`_NAV_KEYS` 를
//! `scripts/gen_screen_keys.py` 가 AST 로 걸어 픽스처로 뽑고, 여기서 그 키를
//! [`base::Screens`] 에 눌러 결과를 견준다.
//!
//! # 무엇을 견주나 — 「닫히나」
//!
//! 화면 계약의 알맹이는 **취소 조건**이다(pytmux-185 가 든 셋 중 하나). 정본이 닫는 키에
//! 우리가 안 닫으면 사용자는 판을 못 빠져나오고, 정본이 안 닫는 키에 우리가 닫으면
//! **판이 조용히 사라진다** — pytmux-174·181·273 이 전부 후자였다.
//!
//! 그래서 견주는 값은 키마다 **닫히나/남나** 하나다. 픽스처의 `close` 는 닫힘이고
//! `consume`·`ignore` 는 남음이다(둘의 갈림 — 키가 목록 위젯으로 흘러가나 — 은 우리
//! 층에서 관측할 값이 아니다. 우리 core 에는 흘려보낼 위젯이 없다).
//!
//! 그리고 **표 밖의 키**(`catch_all`)를 따로 잰다. 표에 적힌 키만 재면 사용자가 실제로
//! 마주치는 쪽을 영영 안 재게 된다 — `mode_transition_conformance` 의 `*` 줄이 잡은 것이
//! 그 자리였다.
//!
//! # 포커스 이동
//!
//! 정본이 `_NAV_KEYS`·`_NAV` 로 커서를 옮기는 키는 우리도 **무언가를 옮겨야** 한다
//! (선택 줄이든 스크롤이든). 「먹었다」로만 재면 아무 일도 안 하면서 초록인 판을 못 잡는다.
//!
//! # ⚠ 여기서 못 재는 것
//!
//! - AST 가 못 읽은 분기(`event.character` 로 가르는 팔레트의 글자 입력 등)는 픽스처가
//!   `unreadable` 로 남기고, 이 자는 **그 화면을 「다 쟀다」로 세지 않는다**. 조용히 Same
//!   으로 접는 것이 이 표가 거짓이 되는 첫걸음이다.
//! - 상태에 걸린 분기(`guard`)는 그 상태를 우리 core 가 안 들 수도 있어 견주지 않는다 —
//!   대신 [`the_guarded_branches_are_named`] 가 그 목록을 드러낸다.

use std::collections::{BTreeMap, BTreeSet};

#[path = "common/divergence.rs"]
mod divergence;
#[path = "common/open_screen.rs"]
mod open_screen;

use base::keys::{Key, Mods};
use base::screens::Screen;
use divergence::key_of;
use open_screen::opened;
use serde::Deserialize;

#[derive(Deserialize)]
struct KeyRow {
    outcome: String,
    #[serde(default)]
    guard: Option<String>,
}

#[derive(Deserialize)]
struct CanonScreen {
    on_key: bool,
    catch_all: Option<String>,
    keys: BTreeMap<String, KeyRow>,
    nav: Vec<String>,
    #[serde(default)]
    bindings: Vec<String>,
    unreadable: Vec<String>,
}

#[derive(Deserialize)]
struct Fx {
    screens: BTreeMap<String, CanonScreen>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/screen_keys.json"))
        .expect("픽스처를 읽을 수 없다 — scripts/gen_screen_keys.py 로 다시 뽑을 것")
}

/// 정본 클래스 이름 → 그 클래스를 맡은 우리 화면들.
///
/// 하나가 여럿을 맡는다 — 정본 `InfoScreen` 하나가 우리에게는 여섯 판이다(제목·키 안내를
/// 판 종류가 들기 때문). 그래서 **전부** 눌러 본다.
fn ours(class: &str) -> Vec<Screen> {
    Screen::all()
        .iter()
        .copied()
        .filter(|s| s.canon_class() == Some(class))
        .collect()
}

/// 정본에 있으나 우리가 맡는 판이 없는 클래스와 **그 이유**.
///
/// ⛔ 이유 없는 예외는 그냥 빠뜨린 것이다.
static UNCLAIMED: &[(&str, &str)] = &[(
    "_SettingInputScreen",
    "설정 값을 손으로 치는 한 줄 입력 — 우리 설정 판은 값을 ←→ 로 돌리거나 \
     `Screen::Prompt` 를 다시 띄우므로 이 클래스에 대응하는 판이 없다. \
     `screen_anchor_conformance` 의 EXCUSED 와 같은 줄이다(설계 면제)",
)];

/// 「제 것 아닌 키」 — `interaction.rs` 와 **같은 키**를 쓴다(두 축이 다른 키를 쓰면
/// 서로 다른 사실을 재면서 같은 이름으로 말하게 된다). 어느 판도 F 키를 자기 것이라고
/// 적지 않았다.
const STRAY: Key = Key::Function(5);
/// 정본 픽스처에서 그 키를 가리키는 이름(그 판이 「제 것」이라 적었나를 볼 때 쓴다).
const STRAY_NAME: &str = "f5";

fn closes(screen: Screen, name: &str) -> Option<bool> {
    let (key, mods) = key_of(name)?;
    let mut screens = opened(screen);
    let outcome = screens.press(key, mods);
    outcome?;
    Some(!screens.is_open())
}

/// 픽스처가 말하는 결과 → 「닫히나」. `None` 은 **못 잰다**는 뜻이다.
///
/// `close_maybe` 는 정본이 도우미 메서드 안에서 **조건부로** 닫는 자리다(팔레트의
/// `Enter` · 설정 판의 `Enter`). 그 조건은 우리 core 가 안 드는 상태에 걸려 있어
/// 여기서 「닫는다/안 닫는다」로 접으면 **맞는 것을 고치게 된다** — 그래서 대조에서
/// 빼되 [`the_unmeasured_branches_are_named_not_hidden`] 가 이름을 드러낸다.
fn want_closes(outcome: &str) -> Option<bool> {
    match outcome {
        "close" => Some(true),
        "consume" | "ignore" => Some(false),
        "close_maybe" => None,
        other => panic!("픽스처가 모르는 결과를 말한다: {other}"),
    }
}

#[test]
fn every_canon_screen_key_lands_the_same_way_here() {
    let fx = fixture();
    let mut wrong: Vec<String> = Vec::new();
    let mut pressed = 0usize;
    for (class, canon) in &fx.screens {
        if !canon.on_key {
            continue;
        }
        for screen in ours(class) {
            for (name, row) in &canon.keys {
                if row.guard.is_some() {
                    continue;
                }
                let Some(got) = closes(screen, name) else {
                    // 못 읽는 이름은 **버리지 않는다** — 아래 시험이 목록을 지킨다.
                    continue;
                };
                let Some(want) = want_closes(&row.outcome) else {
                    continue;
                };
                pressed += 1;
                if got != want {
                    wrong.push(format!(
                        "{screen:?}({class}) {name:?}: 정본은 {} 인데 우리는 {}",
                        if want { "닫는다" } else { "안 닫는다" },
                        if got { "닫는다" } else { "안 닫는다" },
                    ));
                }
            }
        }
    }
    assert!(pressed > 40, "누른 키가 {pressed} 뿐이다 — 대조가 통째로 샜다");
    assert!(
        wrong.is_empty(),
        "정본과 다르게 닫히는 키 {}:\n  {}\n\
         ⛔ 이 갈림은 pytmux-185 의 허용 목록(단말이 못 주는 키 · 픽셀 그림 · OS 창 통합)\n\
         어디에도 안 든다 — 고치거나, 왜 정본이 틀렸는지를 정본에서 고칠 것.",
        wrong.len(),
        wrong.join("\n  ")
    );
}

#[test]
fn a_key_the_canon_screen_never_names_lands_the_same_way_here() {
    // ⛔ 이 시험이 알맹이다 — 표에 적힌 키만 재면 **표 밖**이 어떻게 되는지는 영영 안 잰다.
    let fx = fixture();
    let mut wrong: Vec<String> = Vec::new();
    for (class, canon) in &fx.screens {
        let Some(catch) = canon.catch_all.as_deref() else {
            continue;
        };
        for screen in ours(class) {
            // 이 판이 그 키를 자기 것으로 적었으면 「표 밖」이 아니다.
            if canon.keys.contains_key(STRAY_NAME)
                || canon.bindings.iter().any(|b| b == STRAY_NAME)
            {
                continue;
            }
            let mut screens = opened(screen);
            if screens.press(STRAY, Mods::NONE).is_none() {
                wrong.push(format!(
                    "{screen:?}({class}): 판이 떠 있는데 키가 **새어 나갔다** — \
                     뒤 패널로 가는 키가 있으면 사용자는 자기가 무엇을 조작하는지 알 수 없다"
                ));
                continue;
            }
            let got = !screens.is_open();
            let Some(want) = want_closes(catch) else { continue };
            if got != want {
                wrong.push(format!(
                    "{screen:?}({class}) 제 것 아닌 키: 정본은 {} 인데 우리는 {}",
                    if want { "닫는다" } else { "안 닫는다" },
                    if got { "닫는다" } else { "안 닫는다" },
                ));
            }
        }
    }
    assert!(
        wrong.is_empty(),
        "제 것 아닌 키에서 갈린 판 {}:\n  {}",
        wrong.len(),
        wrong.join("\n  ")
    );
}

#[test]
fn a_canon_navigation_key_moves_something_here_too() {
    // 「먹었다」로만 재면 **아무 일도 안 하면서 초록**인 판을 못 잡는다(pytmux-185 의
    // 최소 요건 셋 중 「포커스 이동」).
    let fx = fixture();
    let mut wrong: Vec<String> = Vec::new();
    let mut moved = 0usize;
    for (class, canon) in &fx.screens {
        for screen in ours(class) {
            for name in &canon.nav {
                let Some((key, mods)) = key_of(name) else { continue };
                let mut screens = opened(screen);
                // ⛔ **끝에서 재면 거짓 실패가 난다** — `Up`·`PageUp`·`Home` 은 이미 맨
                //   위면 옮길 데가 없어 「아무것도 안 옮긴다」로 보인다. 정본도 같다.
                //   그래서 먼저 가운데로 내려놓고 잰다.
                for _ in 0..3 {
                    screens.press(Key::Down, Mods::NONE);
                }
                let before = (screens.selected(), screens.scroll());
                let outcome = screens.press(key, mods);
                if outcome.is_none() {
                    wrong.push(format!("{screen:?}({class}) {name:?}: 키가 새어 나갔다"));
                    continue;
                }
                if !screens.is_open() {
                    wrong.push(format!(
                        "{screen:?}({class}) {name:?}: 정본은 커서를 옮기는데 우리는 판을 닫는다"
                    ));
                    continue;
                }
                let after = (screens.selected(), screens.scroll());
                if before == after {
                    wrong.push(format!(
                        "{screen:?}({class}) {name:?}: 정본은 커서를 옮기는데 우리는 \
                         아무것도 안 옮긴다(먹기만 한다)"
                    ));
                    continue;
                }
                moved += 1;
            }
        }
    }
    assert!(moved > 10, "옮긴 키가 {moved} 뿐이다 — 대조가 통째로 샜다");
    assert!(
        wrong.is_empty(),
        "정본이 커서를 옮기는데 우리는 안 옮기는 키 {}:\n  {}",
        wrong.len(),
        wrong.join("\n  ")
    );
}

#[test]
fn every_canon_screen_is_claimed_or_named_unclaimed() {
    let fx = fixture();
    let mut orphan: Vec<&str> = Vec::new();
    for class in fx.screens.keys() {
        if ours(class).is_empty() && !UNCLAIMED.iter().any(|(n, _)| n == class) {
            orphan.push(class);
        }
    }
    assert!(
        orphan.is_empty(),
        "정본에 있는데 맡은 판도 없고 사유도 없다: {orphan:?}\n\
         맡을 화면이 없으면 UNCLAIMED 에 **이유와 함께** 적을 것."
    );
    for (class, why) in UNCLAIMED {
        assert!(!why.is_empty(), "{class}: 이유 없는 예외는 그냥 빠뜨린 것이다");
        assert!(
            fx.screens.contains_key(*class),
            "정본에 없는 클래스가 사유 목록에 남아 있다: {class}"
        );
        assert!(
            ours(class).is_empty(),
            "이제 맡는 판이 있는 클래스가 사유 목록에 있다 — 빼고 잴 것: {class}"
        );
    }
}

#[test]
fn the_fixture_read_enough_of_canon_to_be_worth_anything() {
    // 빈 표를 통과로 두면 「아무것도 안 재면서 초록」이 된다.
    let fx = fixture();
    assert!(fx.screens.len() >= 15, "화면이 {} 뿐이다 — 뽑기가 깨졌다", fx.screens.len());
    let keys: usize = fx.screens.values().map(|s| s.keys.len()).sum();
    assert!(keys >= 50, "키가 {keys} 뿐이다 — 뽑기가 깨졌다");
    let with_catch = fx.screens.values().filter(|s| s.catch_all.is_some()).count();
    assert!(with_catch >= 15, "catch-all 을 읽은 판이 {with_catch} 뿐이다");
}

/// 못 읽은 분기와 상태에 걸린 분기를 **드러낸다** — 조용한 사각지대를 두지 않는다.
#[test]
fn the_unmeasured_branches_are_named_not_hidden() {
    let fx = fixture();
    let mut unread: BTreeSet<String> = BTreeSet::new();
    let mut guarded: BTreeSet<String> = BTreeSet::new();
    for (class, canon) in &fx.screens {
        for src in &canon.unreadable {
            unread.insert(format!("{class}: {src}"));
        }
        for (name, row) in &canon.keys {
            if let Some(guard) = &row.guard {
                guarded.insert(format!("{class} {name}: {guard}"));
            }
            if row.outcome == "close_maybe" {
                guarded.insert(format!(
                    "{class} {name}: **조건부로** 닫는다(close_maybe) — 조건이 \
                     도우미 메서드 안이거나 앞선 갈래가 먼저 빠져나간다"
                ));
            }
        }
    }
    // ⛔ **이 수는 올리지 않는다**(`interaction.rs` 규칙 4 와 같은 래칫). 못 읽은 분기가
    //   늘었다는 것은 정본이 우리가 못 읽는 모양으로 갔다는 뜻이고, 그때 이 자는 조용히
    //   덜 재게 된다.
    const UNREADABLE_CEILING: usize = 7;
    const GUARDED_CEILING: usize = 16;
    assert!(
        unread.len() <= UNREADABLE_CEILING,
        "AST 가 못 읽은 분기가 {} 로 늘었다(한도 {UNREADABLE_CEILING}):\n  {}\n\
         → 생성기(`scripts/gen_screen_keys.py`)가 그 모양을 읽게 하거나, 한도를 올린 \
         이유를 여기 적을 것.",
        unread.len(),
        unread.iter().cloned().collect::<Vec<_>>().join("\n  ")
    );
    assert!(
        guarded.len() <= GUARDED_CEILING,
        "상태에 걸린 분기가 {} 로 늘었다(한도 {GUARDED_CEILING}):\n  {}",
        guarded.len(),
        guarded.iter().cloned().collect::<Vec<_>>().join("\n  ")
    );
}

#[test]
fn print_the_screen_key_score() {
    // 게이트가 아니라 **자**다(`parity.rs` 의 `print_the_score` 와 같은 자리).
    let fx = fixture();
    let mut screens = 0usize;
    let mut keys = 0usize;
    for (class, canon) in &fx.screens {
        if canon.on_key {
            screens += ours(class).len();
            keys += canon.keys.len() * ours(class).len();
        }
    }
    println!("\n화면 키 적합성(정본 on_key 를 AST 로 읽어 실제로 눌러 본다):");
    println!("  정본 클래스 {} · 우리 판 {screens} · 눌러 본 키(중복 포함) {keys}",
        fx.screens.len());
    for (class, why) in UNCLAIMED {
        println!("  맡은 판 없음 {class} — {}", why.split(" — ").next().unwrap_or(why));
    }
    println!();
}
