//! 화면 스택과 키 라우팅 오라클(패리티 G2).
//!
//! 여기서 지키는 것은 **두 뷰가 같은 규칙을 쓴다**는 것이다. 그리기는 각자 하지만,
//! "무엇이 떠 있고 키를 누가 먹나"가 갈리면 한쪽에서만 `Esc` 가 안 먹는다.

use super::*;

#[test]
fn nothing_is_open_at_the_start() {
    let mut screens = Screens::new();
    assert!(!screens.is_open());
    assert_eq!(screens.top(), None);
    // 열린 것이 없으면 키는 **평소 경로**로 가야 한다 — None 이 그 신호다.
    assert_eq!(screens.press(Key::Char('a'), Mods::NONE), None);
}

#[test]
fn an_open_screen_eats_every_key() {
    // ★ 화면 뒤 패널로 새는 키가 있으면 사용자는 자기가 무엇을 조작하는지 알 수 없다.
    let mut screens = Screens::new();
    screens.open(Screen::Keys);
    for key in [Key::Char('a'), Key::Enter, Key::Tab, Key::Escape] {
        let mut s = screens.clone();
        assert!(s.press(key, Mods::NONE).is_some(), "{key:?} 가 새어 나갔다");
    }
    let _ = &screens;
}

#[test]
fn arrows_scroll_and_everything_else_closes() {
    // 파이썬 클라 `InfoScreen` 과 같은 규약 — 읽는 화면에서 아무 키나 누르면 닫힌다.
    let mut screens = Screens::new();
    screens.open(Screen::Keys);
    // 위·아래·페이지 **넷 다** 본다. 하나만 보면 나머지가 닫기로 새도 안 잡힌다
    // (2026-07-29 뮤테이션에서 실제로 `Up` 만 닫기로 바꿔도 통과했다).
    for key in [Key::Down, Key::Up, Key::PageDown, Key::PageUp] {
        assert_eq!(
            screens.press(key, Mods::NONE),
            Some(ScreenKey::Consumed),
            "{key:?} 가 화면을 닫았다"
        );
        assert!(screens.is_open(), "{key:?} 로 닫히면 안 된다");
    }
    screens.press(Key::Down, Mods::NONE);
    assert_eq!(screens.scroll(), 1);
    assert_eq!(screens.press(Key::Char('a'), Mods::NONE), Some(ScreenKey::Closed));
    assert!(!screens.is_open());
}

#[test]
fn the_closing_key_never_reaches_the_pane() {
    // `Closed` 도 "먹었다"의 한 종류다. 닫는 키가 패널에도 가면 화면을 닫으려던 `q` 가
    // 셸에 찍힌다.
    let mut screens = Screens::new();
    screens.open(Screen::Keys);
    assert_eq!(screens.press(Key::Char('q'), Mods::NONE), Some(ScreenKey::Closed));
}

#[test]
fn scrolling_never_goes_above_the_top() {
    let mut screens = Screens::new();
    screens.open(Screen::Keys);
    screens.press(Key::Up, Mods::NONE);
    screens.press(Key::PageUp, Mods::NONE);
    assert_eq!(screens.scroll(), 0, "0 위로 올라갔다(underflow)");
}

#[test]
fn the_view_clamps_the_scroll_to_the_content() {
    // 내용 길이를 아는 것은 그리는 쪽이다 — core 는 자를 줄만 안다.
    let mut screens = Screens::new();
    screens.open(Screen::Keys);
    for _ in 0..5 {
        screens.press(Key::PageDown, Mods::NONE);
    }
    screens.clamp_scroll(7);
    assert_eq!(screens.scroll(), 7);
}

#[test]
fn opening_the_same_screen_again_closes_it() {
    // 여는 키를 다시 누르는 것이 사용자가 기대하는 닫기다. 같은 화면이 두 번 쌓이면
    // Esc 를 두 번 눌러야 빠져나온다 — 아무도 예상하지 않는다.
    let mut screens = Screens::new();
    screens.open(Screen::Keys);
    screens.open(Screen::Keys);
    assert!(!screens.is_open());
}

#[test]
fn a_second_screen_stacks_and_the_top_one_closes_first() {
    let mut screens = Screens::new();
    screens.open(Screen::Keys);
    screens.open(Screen::ClaudeDetail);
    assert_eq!(screens.top(), Some(Screen::ClaudeDetail));
    screens.press(Key::Escape, Mods::NONE);
    assert_eq!(screens.top(), Some(Screen::Keys), "아래 화면까지 함께 닫혔다");
}

#[test]
fn switching_screens_starts_at_the_top_of_the_new_one() {
    // 스크롤이 남아 있으면 새 화면이 **중간부터** 보인다.
    let mut screens = Screens::new();
    screens.open(Screen::Keys);
    screens.press(Key::PageDown, Mods::NONE);
    screens.open(Screen::ClaudeDetail);
    assert_eq!(screens.scroll(), 0);
}

#[test]
fn a_modifier_combo_closes_instead_of_being_swallowed() {
    // Ctrl+C 같은 조합을 화면이 조용히 먹으면 사용자는 그 조합이 죽은 줄 안다.
    let mut screens = Screens::new();
    screens.open(Screen::Keys);
    assert_eq!(
        screens.press(Key::Char('c'), Mods::CTRL),
        Some(ScreenKey::Closed)
    );
}

#[test]
fn every_screen_says_its_name_and_its_keys() {
    // 안내가 틀리면 도움말이 없느니만 못하다 — 목록 화면에 "아무 키나 닫기"라고 적혀
    // 있으면 사용자는 Enter 가 확정이라는 것을 모른다(2026-07-29 에 실제로 그랬다).
    for screen in [Screen::Keys, Screen::ClaudeDetail, Screen::Tabs] {
        assert!(!screen.title().is_empty(), "{screen:?} 에 제목이 없다");
        assert!(!screen.hint().is_empty(), "{screen:?} 에 키 안내가 없다");
    }
    assert!(
        Screen::Tabs.hint().contains("Enter"),
        "목록 화면인데 확정 키를 안 알려 준다"
    );
    assert!(
        !Screen::Keys.hint().contains("Enter"),
        "읽는 화면에 확정 키가 적혀 있다"
    );
}

// ── 목록형 화면(패리티 G3a — 탭 스위처) ──────────────────────────────────────
//
// 읽는 화면과 **같은 키가 다른 일을 한다**: 방향키는 스크롤이 아니라 선택이고, Enter 는
// 확정이다. 그 차이를 여기서 못박는다.

#[test]
fn a_list_screen_moves_the_selection_not_the_scroll() {
    let mut screens = Screens::new();
    screens.open(Screen::Tabs);
    assert_eq!(screens.press(Key::Down, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.selected(), 1);
    assert_eq!(screens.scroll(), 0, "목록에서 스크롤이 움직였다");
    screens.press(Key::Up, Mods::NONE);
    assert_eq!(screens.selected(), 0);
}

#[test]
fn tab_and_shift_tab_walk_the_list() {
    // 파이썬 클라와 같은 동선(Tab 다음 · Shift+Tab 이전) — 손버릇이 패리티의 기준이다.
    let mut screens = Screens::new();
    screens.open(Screen::Tabs);
    screens.press(Key::Tab, Mods::NONE);
    screens.press(Key::Tab, Mods::NONE);
    assert_eq!(screens.selected(), 2);
    screens.press(Key::BackTab, Mods::NONE);
    assert_eq!(screens.selected(), 1);
}

#[test]
fn enter_confirms_and_says_what_was_picked() {
    let mut screens = Screens::new();
    screens.open(Screen::Tabs);
    screens.press(Key::Down, Mods::NONE);
    screens.press(Key::Down, Mods::NONE);
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Chosen(2))
    );
    assert!(!screens.is_open(), "확정 뒤에는 닫혀야 한다");
}

#[test]
fn escape_cancels_without_choosing_anything() {
    // ★ 고르는 동안에는 **아무 일도 일어나지 않는다**. Esc 로 나가면 원래 탭이 그대로다
    // (파이썬 클라와 같다) — 이게 없으면 훑어보는 것만으로 탭이 바뀐다.
    let mut screens = Screens::new();
    screens.open(Screen::Tabs);
    screens.press(Key::Down, Mods::NONE);
    assert_eq!(screens.press(Key::Escape, Mods::NONE), Some(ScreenKey::Closed));
    assert!(!screens.is_open());
}

#[test]
fn the_view_clamps_the_selection_when_the_list_shrinks() {
    // 탭이 하나 닫히면 선택이 목록 밖을 가리킨다 — 그대로 Enter 를 치면 **없는 탭**으로
    // 전환하려 든다.
    let mut screens = Screens::new();
    screens.open(Screen::Tabs);
    for _ in 0..5 {
        screens.press(Key::Down, Mods::NONE);
    }
    screens.clamp_selection(3);
    assert_eq!(screens.selected(), 2);
}

#[test]
fn the_selection_never_goes_above_the_first_row() {
    let mut screens = Screens::new();
    screens.open(Screen::Tabs);
    screens.press(Key::Up, Mods::NONE);
    assert_eq!(screens.selected(), 0);
}

#[test]
fn opening_a_list_starts_at_the_first_row() {
    let mut screens = Screens::new();
    screens.open(Screen::Tabs);
    screens.press(Key::Down, Mods::NONE);
    screens.close_top();
    screens.open(Screen::Tabs);
    assert_eq!(screens.selected(), 0, "옛 선택이 남았다");
}

// ── 입력·확인 화면(패리티 G4) ────────────────────────────────────────────────
//
// 여기서 지키는 것은 **되돌릴 수 없는 것 앞의 기본값**이다. 확인 화면은 헷갈려서 아무 키나
// 눌렀을 때 "아무 일도 안 남"이어야 한다.

#[test]
fn typing_builds_the_answer_and_enter_returns_it() {
    let mut screens = Screens::new();
    screens.ask(Prompt::RenameTab, "");
    for c in "빌드".chars() {
        assert_eq!(screens.press(Key::Char(c), Mods::NONE), Some(ScreenKey::Consumed));
    }
    assert_eq!(screens.typed(), "빌드");
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Answered(Prompt::RenameTab, "빌드".to_owned()))
    );
    assert!(!screens.is_open());
}

#[test]
fn a_rename_starts_from_the_current_name() {
    // 이름을 **바꾸는** 것이지 새로 짓는 것이 아니다 — 빈 칸에서 시작하면 한 글자만
    // 고치려는 사람이 전체를 다시 쳐야 한다.
    let mut screens = Screens::new();
    screens.ask(Prompt::RenameTab, "지금이름");
    assert_eq!(screens.typed(), "지금이름");
    screens.press(Key::Backspace, Mods::NONE);
    assert_eq!(screens.typed(), "지금이");
}

#[test]
fn escaping_a_prompt_throws_the_typing_away() {
    // 반쯤 친 이름이 남아 있다가 다음에 열 때 튀어나오면 사용자가 자기가 뭘 하는지 모른다.
    let mut screens = Screens::new();
    screens.ask(Prompt::RenameTab, "");
    screens.press(Key::Char('x'), Mods::NONE);
    assert_eq!(screens.press(Key::Escape, Mods::NONE), Some(ScreenKey::Closed));
    screens.ask(Prompt::RenameTab, "");
    assert_eq!(screens.typed(), "", "옛 입력이 남았다");
}

#[test]
fn a_confirm_says_yes_only_on_y_or_the_picked_button() {
    // `y`/`Y` 는 지름길이라 늘 '예'다.
    for key in [Key::Char('y'), Key::Char('Y')] {
        let mut screens = Screens::new();
        screens.confirm(Prompt::KillTab);
        assert_eq!(
            screens.press(key, Mods::NONE),
            Some(ScreenKey::Answered(Prompt::KillTab, "y".to_owned())),
            "{key:?}"
        );
    }
}

#[test]
fn enter_follows_the_picked_button_and_the_pick_starts_on_no() {
    // ★ 2026-07-31 정본 맞추기: 확인 화면에 **버튼 둘**이 생겼고 포커스는 '아니오'에서
    //   시작한다. 그래서 **Enter 는 이제 아니오**다 — 되돌릴 수 없는 화면에서 가장
    //   반사적으로 눌리는 키가 '예'이던 종전이 이 화면의 취지와 어긋났다.
    let mut screens = Screens::new();
    screens.confirm(Prompt::KillTab);
    assert_eq!(screens.confirm_pick(), crate::screens::CONFIRM_NO);
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Closed),
        "기본 포커스에서 Enter 가 '예'로 샜다"
    );

    // ←/→ 로 '예'를 고르면 그때 Enter 가 확정이다.
    let mut screens = Screens::new();
    screens.confirm(Prompt::KillTab);
    assert_eq!(screens.press(Key::Right, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.confirm_pick(), crate::screens::CONFIRM_YES);
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Answered(Prompt::KillTab, "y".to_owned())),
        "'예'를 골랐는데 Enter 가 확정이 아니다"
    );
}

#[test]
fn explicit_no_keys_close_a_confirm_as_no() {
    // ★ 기본이 '아니오'다 — `escape`·`n`/`N` 은 정본 `ConfirmScreen.on_key` 가 실제로
    // 먹는 닫는 키다(pytmux-273 ③).
    // `Tab` 은 여기 없다 — **버튼 사이를 오간다**(정본과 같다). 화면을 닫지 않는 키는
    // 위 목록에 두면 안 된다.
    for key in [Key::Char('n'), Key::Char('N'), Key::Escape] {
        let mut screens = Screens::new();
        screens.confirm(Prompt::KillPane);
        assert_eq!(screens.press(key, Mods::NONE), Some(ScreenKey::Closed), "{key:?}");
        assert!(!screens.is_open());
    }
    // Tab 은 고르기만 하고 화면은 그대로다.
    let mut screens = Screens::new();
    screens.confirm(Prompt::KillPane);
    assert_eq!(screens.press(Key::Tab, Mods::NONE), Some(ScreenKey::Consumed));
    assert!(screens.is_open(), "Tab 이 확인 화면을 닫았다");
}

#[test]
fn an_unrelated_key_on_a_confirm_does_nothing(){
    // ⚠ 종전에는 이 다섯(escape·y/Y·n/N·enter·left/right/tab) 밖의 모든 키가 "아니오"로
    // 닫혔다(pytmux-273 ③) — 정본 `ConfirmScreen.on_key` 는 그 다섯 밖의 키에 갈래가
    // 없어 화면이 그대로 남는다. 되돌릴 수 없는 것 앞에서 오타 하나로 물음이 사라지면
    // 사용자는 자기가 무엇을 답했는지 모른다.
    let mut screens = Screens::new();
    screens.confirm(Prompt::KillPane);
    assert_eq!(screens.press(Key::Char('x'), Mods::NONE), Some(ScreenKey::Consumed));
    assert!(screens.is_open(), "관계없는 키가 확인 화면을 닫았다");
}

#[test]
fn a_prompt_remembers_what_it_asked() {
    // 무엇을 물었는지 잊으면 대답을 어디에 쓸지 알 수 없다.
    let mut screens = Screens::new();
    screens.ask(Prompt::MoveTab, "");
    assert_eq!(screens.asking(), Some(Prompt::MoveTab));
    screens.press(Key::Escape, Mods::NONE);
    assert_eq!(screens.asking(), None, "닫힌 뒤에도 물음이 남았다");
}

#[test]
fn shifted_letters_reach_the_prompt() {
    // 대문자는 글자로 온다 — 수정키가 붙었다고 화면 밖으로 던지면 이름에 대문자를 못 쓴다.
    let mut screens = Screens::new();
    screens.ask(Prompt::RenameTab, "");
    screens.press(Key::Char('A'), Mods::NONE);
    assert_eq!(screens.typed(), "A");
}

#[test]
fn every_prompt_has_a_question() {
    for prompt in [Prompt::RenameTab, Prompt::MoveTab, Prompt::KillPane, Prompt::KillTab] {
        assert!(!prompt.question().is_empty(), "{prompt:?} 에 물음이 없다");
    }
}

// ── 명령 팔레트(패리티 G3c) ──────────────────────────────────────────────────
//
// 목록과 입력이 **한 화면에** 있다. 글자는 필터로 쌓이고 방향키는 선택을 옮긴다.

#[test]
fn typing_narrows_the_palette_and_resets_the_cursor() {
    // ★ 필터가 바뀌면 선택이 맨 위로 돌아가야 한다 — 안 그러면 세 글자를 친 뒤 남은
    // 항목이 하나인데 선택은 5번째를 가리키고 있어 Enter 가 아무 일도 안 한다.
    let mut screens = Screens::new();
    screens.open_palette();
    screens.press(Key::Down, Mods::NONE);
    screens.press(Key::Down, Mods::NONE);
    assert_eq!(screens.selected(), 2);
    screens.press(Key::Char('k'), Mods::NONE);
    assert_eq!(screens.typed(), "k");
    assert_eq!(screens.selected(), 0, "필터를 쳤는데 커서가 남았다");
}

#[test]
fn the_filter_matches_anywhere_in_the_name_and_ignores_case() {
    use crate::keymap::{PALETTE, palette_matches};
    let all = palette_matches("");
    assert_eq!(all.len(), PALETTE.len(), "빈 필터는 전부 보인다");
    let kill = palette_matches("kill");
    assert!(kill.len() >= 2, "kill-pane·kill-tab 둘 다 나와야 한다: {kill:?}");
    assert_eq!(palette_matches("KILL").len(), kill.len(), "대소문자를 가렸다");
    // 부분 일치 — 가운데 글자로도 찾힌다.
    assert!(!palette_matches("layout").is_empty());
    assert!(palette_matches("없는명령").is_empty());
}

#[test]
fn enter_picks_the_row_within_the_filtered_list() {
    let mut screens = Screens::new();
    screens.open_palette();
    screens.press(Key::Char('k'), Mods::NONE);
    screens.press(Key::Down, Mods::NONE);
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Chosen(1)),
        "걸러진 목록의 둘째 줄"
    );
}

/// ←→ 가 **카테고리 탭**을 돌리나(정본 `CommandListScreen` 의 손).
#[test]
fn arrows_walk_the_palette_categories() {
    use crate::keymap::PALETTE_CATS;
    let mut screens = Screens::new();
    screens.open_palette();
    assert_eq!(screens.palette_tab(), 0, "열면 '전체' 탭이다");
    assert_eq!(screens.palette_cat(), None, "'전체' 는 거르지 않는다");

    // 한 바퀴: 전체 → 카테고리들 → 다시 전체.
    for cat in PALETTE_CATS {
        assert_eq!(screens.press(Key::Right, Mods::NONE), Some(ScreenKey::Consumed));
        assert_eq!(screens.palette_cat(), Some(*cat), "→ 가 엉뚱한 분류로 갔다");
        assert_eq!(screens.top(), Some(Screen::Commands), "→ 가 화면을 닫았다");
    }
    screens.press(Key::Right, Mods::NONE);
    assert_eq!(screens.palette_cat(), None, "끝에서 '전체' 로 돌아와야 한다");
    // ← 는 되돌아간다(0에서 누르면 마지막 카테고리).
    screens.press(Key::Left, Mods::NONE);
    assert_eq!(screens.palette_cat(), Some(*PALETTE_CATS.last().unwrap()));
}

/// 탭을 바꾸면 그 탭의 것만 보이나(양성 오라클 — 개수와 **내용** 둘 다 잰다).
#[test]
fn a_category_tab_shows_only_that_category() {
    use crate::keymap::{PALETTE, palette_matches, palette_matches_in};
    let cat = "탭";
    let rows = palette_matches_in(Some(cat), "");
    assert!(!rows.is_empty(), "'{cat}' 탭이 비었다 — 통과가 아니라 고장이다");
    assert!(rows.len() < palette_matches("").len(), "거르지 않았다");
    for i in &rows {
        assert_eq!(PALETTE[*i].cat, cat, "{} 는 '{cat}' 이 아니다", PALETTE[*i].name);
    }
    // 그리고 그 탭이 자기 분류를 **빠짐없이** 담아야 한다(빠지면 그 명령은 전체 탭에만
    // 있고 분류 탭에서는 영영 안 보인다).
    let want = PALETTE.iter().filter(|e| e.cat == cat).count();
    assert_eq!(rows.len(), want);
}

/// 친 글자가 지금 탭에만 안 걸리면 **걸리는 탭으로 옮겨 준다**(정본 `_rebuild`).
///
/// 이게 없으면 `패널` 탭에서 `kill-tab` 을 치는 순간 "맞는 명령이 없다"가 되고,
/// 사용자는 이름을 잘못 안 줄 안다 — 실제로는 옆 탭에 있다.
#[test]
fn typing_hops_to_a_tab_that_has_results() {
    let mut screens = Screens::new();
    screens.open_palette();
    screens.press(Key::Right, Mods::NONE); // '패널' 탭
    assert_eq!(screens.palette_cat(), Some("패널"));
    for c in "kill-tab".chars() {
        screens.press(Key::Char(c), Mods::NONE);
    }
    let cat = screens.palette_cat();
    assert_ne!(cat, Some("패널"), "결과 없는 탭에 머물렀다");
    assert!(
        !crate::keymap::palette_matches_in(cat, screens.typed()).is_empty(),
        "옮겨 간 탭에도 결과가 없다: {cat:?}"
    );
}

/// 탭마다 적히는 **일치 수**가 그 탭의 실제 목록 길이와 같나(탭줄이 거짓말하면 사용자가
/// 결과가 있다는 탭으로 갔다가 빈 화면을 본다).
#[test]
fn the_tab_counts_match_the_lists_behind_them() {
    use crate::keymap::{PALETTE_CATS, palette_matches_in, palette_tab_counts, palette_tab_labels};
    for filter in ["", "kill", "layout", "없는명령"] {
        let counts = palette_tab_counts(filter);
        assert_eq!(counts.len(), PALETTE_CATS.len() + 1, "탭 수와 개수 칸이 다르다");
        assert_eq!(counts.len(), palette_tab_labels().len(), "이름과 개수의 자리가 다르다");
        assert_eq!(counts[0], palette_matches_in(None, filter).len(), "'전체' 개수가 틀렸다");
        for (i, cat) in PALETTE_CATS.iter().enumerate() {
            assert_eq!(
                counts[i + 1],
                palette_matches_in(Some(cat), filter).len(),
                "'{cat}' 개수가 틀렸다 (필터 {filter:?})"
            );
        }
        // 분류 탭 개수의 합 = 전체(모든 줄이 정확히 한 탭에 속한다 — 적합성 테스트가
        // 강제하는 "빠짐없이 덮는다"를 필터가 걸린 상태에서도 다시 잰다).
        assert_eq!(counts[1..].iter().sum::<usize>(), counts[0], "필터 {filter:?}");
    }
}

// ── 판 안 마우스(클릭 = 옮기고 누르기) ─────────────────────────────────────────

/// 설정 사이드바 탭을 누르면 **그 카테고리 첫 줄**로 간다(키의 `Tab` 과 같은 자리).
#[test]
fn clicking_a_settings_tab_jumps_to_that_category() {
    use crate::config::{SETTINGS, SETTINGS_CATS, settings_cat_first};
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    for (i, cat) in SETTINGS_CATS.iter().enumerate() {
        assert_eq!(
            screens.panel_click(PanelTarget::SettingsCat(i)),
            PanelEffect::Moved,
            "탭은 실행이 아니다"
        );
        assert_eq!(screens.selected(), settings_cat_first(cat).unwrap());
        assert_eq!(SETTINGS[screens.selected()].cat, *cat);
    }
    // 없는 탭은 아무 일도 안 한다(화면이 바뀌면 자리가 사라질 수 있다).
    let before = screens.selected();
    assert_eq!(screens.panel_click(PanelTarget::SettingsCat(999)), PanelEffect::Moved);
    assert_eq!(screens.selected(), before);
}

/// 팔레트 탭을 누르면 그 분류로 가고 커서가 맨 위로 돌아온다.
#[test]
fn clicking_a_palette_tab_switches_the_category() {
    use crate::keymap::PALETTE_CATS;
    let mut screens = Screens::new();
    screens.open_palette();
    screens.press(Key::Down, Mods::NONE);
    assert_eq!(screens.panel_click(PanelTarget::PaletteTab(2)), PanelEffect::Moved);
    assert_eq!(screens.palette_cat(), Some(PALETTE_CATS[1]));
    assert_eq!(screens.selected(), 0, "탭을 바꿨는데 커서가 남았다");
    // 0 은 `전체` 다.
    screens.panel_click(PanelTarget::PaletteTab(0));
    assert_eq!(screens.palette_cat(), None);
    assert_eq!(
        screens.panel_click(PanelTarget::PaletteTab(PALETTE_CATS.len() + 1)),
        PanelEffect::Moved
    );
}

/// 메뉴 줄 클릭은 **그 줄로 옮기고 `Enter` 를 태워 달라**고 답한다.
///
/// 클릭에만 있는 지름길을 만들지 않는 것이 요점이다 — 그러면 그룹 진입·확인 화면 같은
/// 갈래를 클릭이 건너뛰게 된다.
#[test]
fn clicking_a_menu_row_asks_for_the_normal_enter_path() {
    use crate::MenuRow;
    let mut screens = Screens::new();
    screens.open(Screen::Menu);
    assert_eq!(screens.panel_click(PanelTarget::Row(1)), PanelEffect::Enter, "실행 경로를 안 탄다");
    assert_eq!(screens.selected(), 1);
    // 뷰가 이어서 Enter 를 태우면 그 줄의 뜻대로 동작한다(여기서는 그룹 진입).
    let rows = screens.menu_rows();
    let MenuRow::Group(second) = rows[1] else { panic!("둘째 줄이 그룹이 아니다") };
    screens.press(Key::Enter, Mods::NONE);
    assert_eq!(screens.menu_group(), Some(second));
}

// ── 설정 판 마우스(§10-21ⓣ) ────────────────────────────────────────────────────

/// ★ 이름칸은 **고르기만** 한다.
///
/// [`PanelTarget::Row`] 로 뭉뚱그리면 이름을 눌렀을 뿐인데 값이 바뀐다 — 설정에서
/// `Enter` 는 값을 넘기는 키라서다. 값을 바꾸는 것은 값칸의 일이다.
#[test]
fn clicking_a_setting_name_only_moves_the_cursor() {
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    assert_eq!(screens.panel_click(PanelTarget::SettingRow(3)), PanelEffect::Moved);
    assert_eq!(screens.selected(), 3);
}

/// 값칸의 화살표·토글은 **평소 `←→` 경로**를 탄다 — 감기·범위 규칙이 저쪽에 있다.
#[test]
fn clicking_a_setting_arrow_takes_the_normal_direction_path() {
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    assert_eq!(
        screens.panel_click(PanelTarget::SettingStep { row: 5, forward: false }),
        PanelEffect::Dir(false)
    );
    // 누른 줄로 커서도 옮긴다 — 안 옮기면 다른 줄의 값이 바뀐다.
    assert_eq!(screens.selected(), 5);
    assert_eq!(
        screens.panel_click(PanelTarget::SettingStep { row: 5, forward: true }),
        PanelEffect::Dir(true)
    );
}

/// 낱말을 직접 찍으면 **그 자리**가 그대로 넘어온다(화살표처럼 한 칸씩 돌지 않는다).
#[test]
fn clicking_a_setting_word_picks_that_exact_slot() {
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    assert_eq!(
        screens.panel_click(PanelTarget::SettingChoice { row: 2, index: 1 }),
        PanelEffect::Pick { row: 2, index: 1 }
    );
    assert_eq!(screens.selected(), 2);
}

/// 확인 버튼 클릭 — **누른 버튼이 곧 답**이다.
#[test]
fn clicking_a_confirm_button_answers_with_that_button() {
    let mut screens = Screens::new();
    screens.confirm(Prompt::KillTab);
    assert_eq!(screens.confirm_pick(), CONFIRM_NO, "열 때는 늘 아니오다");

    // '예' 를 눌렀다 → 이어지는 Enter 가 예로 확정된다.
    assert_eq!(screens.panel_click(PanelTarget::ConfirmButton(CONFIRM_YES)), PanelEffect::Enter);
    assert_eq!(screens.confirm_pick(), CONFIRM_YES);
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Answered(Prompt::KillTab, "y".to_owned()))
    );

    // '아니오' 를 눌렀다 → 아무 일도 안 일어난다(되돌릴 수 없는 것 앞의 기본).
    let mut screens = Screens::new();
    screens.confirm(Prompt::KillTab);
    assert_eq!(screens.panel_click(PanelTarget::ConfirmButton(CONFIRM_NO)), PanelEffect::Enter);
    assert_eq!(screens.press(Key::Enter, Mods::NONE), Some(ScreenKey::Closed));
}

// ── 메뉴 계층(레이아웃 맞추기 ⑪) ───────────────────────────────────────────────

/// 메뉴를 열면 **최상위**가 보이고, 그룹 줄에서 Enter 하면 그 안으로 들어간다.
#[test]
fn enter_on_a_group_opens_the_submenu() {
    use crate::{MENU_GROUPS, MenuRow};
    let mut screens = Screens::new();
    screens.open(Screen::Menu);
    assert_eq!(screens.menu_group(), None, "열면 최상위다");
    let rows = screens.menu_rows();
    let MenuRow::Group(first) = rows[0] else {
        panic!("최상위 첫 줄이 그룹이 아니다: {:?}", rows[0]);
    };

    assert_eq!(screens.press(Key::Enter, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.menu_group(), Some(first));
    assert_eq!(screens.top(), Some(Screen::Menu), "그룹에 들어가며 화면이 닫혔다");
    assert_eq!(screens.selected(), 0, "들어가면 커서는 첫 줄이다");

    // 그 층에는 그 그룹의 멤버만, 정본 차례로 있다.
    let (_, members) = MENU_GROUPS.iter().find(|(g, _)| *g == first).unwrap();
    let keys: Vec<&str> = screens
        .menu_rows()
        .iter()
        .map(|row| match row {
            MenuRow::Item(entry) => entry.key,
            other => panic!("서브메뉴에 항목 아닌 줄: {other:?}"),
        })
        .collect();
    assert_eq!(keys, *members);
}

/// `←` 는 한 층 나오고, **나온 자리에 커서를 둔다**. 최상위에서는 화면을 닫는다.
#[test]
fn left_leaves_the_submenu_and_lands_on_the_group_it_came_from() {
    use crate::MenuRow;
    let mut screens = Screens::new();
    screens.open(Screen::Menu);
    // 둘째 그룹으로 들어간다(첫째면 "커서가 0으로 튕겼다"와 구분이 안 된다).
    screens.press(Key::Down, Mods::NONE);
    let rows = screens.menu_rows();
    let MenuRow::Group(second) = rows[screens.selected()] else {
        panic!("둘째 줄이 그룹이 아니다");
    };
    let at = screens.selected();
    screens.press(Key::Right, Mods::NONE);
    assert_eq!(screens.menu_group(), Some(second));

    assert_eq!(screens.press(Key::Left, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.menu_group(), None, "나오지 못했다");
    assert_eq!(screens.selected(), at, "나온 자리가 아니라 엉뚱한 줄에 섰다");
    assert_eq!(screens.top(), Some(Screen::Menu), "나오면서 화면까지 닫혔다");

    // 최상위에서 한 번 더 누르면 그때는 닫는다.
    assert_eq!(screens.press(Key::Left, Mods::NONE), Some(ScreenKey::Closed));
    assert_eq!(screens.top(), None);
}

/// ↑↓ 는 **구분선을 건너뛴다**. 구분선에 커서가 서면 Enter 가 아무 일도 안 하고,
/// 그건 "메뉴가 먹통"으로 읽힌다.
#[test]
fn moving_through_the_menu_never_lands_on_a_separator() {
    use crate::MenuRow;
    let mut screens = Screens::new();
    screens.open(Screen::Menu);
    let len = screens.menu_rows().len();
    assert!(
        screens.menu_rows().iter().any(|r| matches!(r, MenuRow::Separator)),
        "최상위에 구분선이 없다 — 이 오라클이 아무것도 안 재고 있다"
    );
    // 끝까지 내려갔다 끝까지 올라오며 한 번도 구분선에 서지 않는다.
    for key in [Key::Down, Key::Up] {
        for _ in 0..len + 2 {
            screens.press(key, Mods::NONE);
            let rows = screens.menu_rows();
            assert!(
                rows[screens.selected()].selectable(),
                "커서가 구분선에 섰다(자리 {})",
                screens.selected()
            );
        }
    }
}

/// 계층을 지나 **모든 줄에 닿을 수 있나** — 화면에서 도달 못 하는 항목은 없는 것과 같다.
#[test]
fn every_menu_entry_is_reachable_through_the_hierarchy() {
    use crate::{MENU, MenuRow};
    let mut seen = std::collections::BTreeSet::new();
    let mut screens = Screens::new();
    screens.open(Screen::Menu);
    let top = screens.menu_rows();
    for (at, row) in top.iter().enumerate() {
        match row {
            MenuRow::Item(entry) => {
                seen.insert(entry.key);
            }
            MenuRow::Group(group) => {
                // 실제로 눌러서 들어간다(표만 읽으면 키 배선이 빠져도 통과한다).
                let mut walk = Screens::new();
                walk.open(Screen::Menu);
                for _ in 0..top[..at].iter().filter(|r| r.selectable()).count() {
                    walk.press(Key::Down, Mods::NONE);
                }
                walk.press(Key::Right, Mods::NONE);
                assert_eq!(walk.menu_group(), Some(*group));
                for row in walk.menu_rows() {
                    if let MenuRow::Item(entry) = row {
                        seen.insert(entry.key);
                    }
                }
            }
            // 플러그인 줄은 정적 표(`MENU`)의 것이 아니라 서버가 준 줄이다 — 이 오라클이
            // 재는 것은 "정적 표의 모든 줄에 닿나"이고, 서버 줄은 아래 별도 오라클이 잰다.
            MenuRow::Plugin(_) | MenuRow::Separator => {}
        }
    }
    let missing: Vec<&str> =
        MENU.iter().map(|e| e.key).filter(|k| !seen.contains(k)).collect();
    assert!(missing.is_empty(), "계층 어디에서도 못 여는 메뉴 줄: {missing:?}");
}

/// 서브메뉴에서 고른 것은 **그 층의 자리**로 돌아온다.
#[test]
fn choosing_in_a_submenu_reports_the_row_of_that_layer() {
    use crate::MenuRow;
    let mut screens = Screens::new();
    screens.open(Screen::Menu);
    screens.press(Key::Right, Mods::NONE); // 첫 그룹으로
    screens.press(Key::Down, Mods::NONE);
    let rows = screens.menu_rows();
    let want = match rows[1] {
        MenuRow::Item(entry) => entry.key,
        ref other => panic!("{other:?}"),
    };
    assert_eq!(screens.press(Key::Enter, Mods::NONE), Some(ScreenKey::Chosen(1)));
    // 뷰가 그 번호로 `menu_rows()`(누르기 전 것)를 찾는다 — 같은 줄이라야 한다.
    assert!(matches!(rows[1], MenuRow::Item(entry) if entry.key == want));
    // 그리고 화면은 닫히고 층도 최상위로 돌아온다.
    assert_eq!(screens.top(), None);
    assert_eq!(screens.menu_group(), None, "다음에 열면 지난 그룹이 따라온다");
}

#[test]
fn the_palette_runs_actions_not_commands() {
    // 팔레트에서 고른 것도 **키로 누른 것과 같은 길**을 타야 한다 — 그래야 kill-pane 을
    // 고를 때도 확인 화면이 뜬다. 표가 액션을 들고 있는 것이 그 계약이다.
    use crate::keymap::PALETTE;
    let kill = PALETTE.iter().find(|e| e.name == "kill-pane").expect("표에 없다");
    assert_eq!(kill.action, crate::Action::KillPane);
}

#[test]
fn a_name_with_an_uppercase_flag_is_still_findable() {
    // ★ tmux 플래그는 대문자다(`resize-pane -Z`). 필터가 한쪽만 소문자로 낮추면 그
    // 항목은 **영영 안 찾힌다** — 목록에는 보이는데 쳐서는 못 찾는 종류의 결함이다.
    use crate::keymap::palette_matches;
    assert!(!palette_matches("-z").is_empty(), "소문자로 못 찾는다");
    assert!(!palette_matches("-Z").is_empty(), "대문자로 못 찾는다");
}

#[test]
fn escaping_the_palette_runs_nothing() {
    let mut screens = Screens::new();
    screens.open_palette();
    screens.press(Key::Char('k'), Mods::NONE);
    assert_eq!(screens.press(Key::Escape, Mods::NONE), Some(ScreenKey::Closed));
    assert!(!screens.is_open());
}

// ── 설정 화면(패리티 G5b) ──────────────────────────────────────────────────────

#[test]
fn settings_stays_open_after_enter() {
    // ★ 이 화면의 존재 이유가 여기 있다 — 값을 바꾸고 **같은 화면에서 확인**한다.
    // 다른 목록형처럼 닫히면 두세 개를 연달아 바꾸는 사람이 그때마다 다시 열어야 한다.
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Applied(0))
    );
    assert_eq!(screens.top(), Some(Screen::Settings), "Enter 가 화면을 닫았다");
}

#[test]
fn settings_moves_and_reports_the_row() {
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    screens.press(Key::Down, Mods::NONE);
    screens.press(Key::Down, Mods::NONE);
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Applied(2))
    );
    screens.press(Key::Up, Mods::NONE);
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Applied(1))
    );
}

/// 설정 판을 닫는 키는 **`Esc` 하나**다.
///
/// ⚠ 종전에 이 오라클은 `q` 도 닫는 것을 «계약»으로 적고 있었다 — 그것이 정본 규약이
/// 아니라는 것을 pytmux-374 가 `SettingsScreen.on_key` 를 열어 확인했다(멈추는 키는
/// `escape`·`enter`·`←→`·`tab`/`shift+tab` 넷뿐이고, 그 밖은 흘러가 판을 안 닫는다).
/// 그래서 이 시험은 **뒤집혔다** — 「안 닫는다」 쪽은 위
/// `a_stray_key_does_not_close_the_settings_panel` 이 잰다.
#[test]
fn only_escape_closes_the_settings_panel() {
    // Tab 은 여기 없다 — **카테고리 이동**이 됐다(아래 오라클).
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    assert_eq!(screens.press(Key::Escape, Mods::NONE), Some(ScreenKey::Closed));
    assert_eq!(screens.top(), None);
}

/// `Tab` 이 **다음 카테고리의 첫 줄**로 뛰나(파이썬 설정 화면의 Tab 동선).
///
/// 양성 오라클이다 — "Tab 이 화면을 안 닫는다"만 재면 Tab 을 그냥 먹어 버려도 통과한다.
/// 여기서는 **어디로 갔는지**를 카테고리 이름으로 잰다(줄 번호로 박으면 표를 재정렬할 때
/// 낡는다 — 이 저장소가 세 번 밟은 함정).
#[test]
fn tab_walks_the_settings_categories() {
    use crate::config::{SETTINGS, SETTINGS_CATS};
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    let cat_at = |row: usize| SETTINGS[row].cat;

    // 첫 줄은 첫 카테고리다 — Tab 을 카테고리 수만큼 누르면 한 바퀴 돌아 제자리.
    assert_eq!(cat_at(screens.selected()), SETTINGS_CATS[0]);
    for want in SETTINGS_CATS.iter().skip(1).chain(SETTINGS_CATS.first()) {
        assert_eq!(screens.press(Key::Tab, Mods::NONE), Some(ScreenKey::Consumed));
        assert_eq!(cat_at(screens.selected()), *want, "Tab 이 엉뚱한 카테고리로 갔다");
        assert_eq!(screens.top(), Some(Screen::Settings), "Tab 이 화면을 닫았다");
    }
    // Shift+Tab 은 되돌아간다.
    assert_eq!(screens.press(Key::BackTab, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(cat_at(screens.selected()), *SETTINGS_CATS.last().unwrap());
}

/// 카테고리 안 아무 줄에서나 `Tab` 을 눌러도 **다음 카테고리의 첫 줄**이다(지금 줄의
/// 다음 줄이 아니다 — 34줄을 Tab 으로 훑는 손은 없다).
#[test]
fn tab_from_the_middle_of_a_category_still_jumps_to_the_next_one() {
    use crate::config::{SETTINGS, SETTINGS_CATS, settings_cat_first};
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    screens.press(Key::Down, Mods::NONE);
    screens.press(Key::Down, Mods::NONE);
    assert_eq!(SETTINGS[screens.selected()].cat, SETTINGS_CATS[0], "아직 첫 카테고리여야 한다");

    screens.press(Key::Tab, Mods::NONE);
    assert_eq!(screens.selected(), settings_cat_first(SETTINGS_CATS[1]).unwrap());
}

/// ⑵ **제 것 아닌 키가 설정 판을 안 닫는다**(pytmux-374 · 정본 `SettingsScreen.on_key`).
///
/// pytmux-273 이 `press_list` 에서 쓸고 간 부류인데, 설정·플러그인은 그 **위에서**
/// 갈라져 나와 `_ => close_top()` 이 남아 있었다. 여기서 재는 것은 그 한 팔이다.
#[test]
fn a_stray_key_does_not_close_the_settings_panel() {
    for screen in [Screen::Settings, Screen::Plugins] {
        for key in [Key::Function(5), Key::Char('z'), Key::Insert, Key::Delete] {
            let mut screens = Screens::new();
            screens.open(screen);
            assert_eq!(
                screens.press(key, Mods::NONE),
                Some(ScreenKey::Consumed),
                "{screen:?} 에서 {key:?} 가 삼켜지지 않았다"
            );
            assert_eq!(screens.top(), Some(screen), "{screen:?} 를 {key:?} 가 닫았다");
        }
        // ⛔ `Esc` 는 여전히 닫는다 — 「안 닫는다」를 「못 닫는다」로 만들면 갇힌다.
        let mut screens = Screens::new();
        screens.open(screen);
        assert_eq!(screens.press(Key::Escape, Mods::NONE), Some(ScreenKey::Closed));
        assert_eq!(screens.top(), None, "{screen:?} 에서 Esc 가 안 닫았다");
    }
}

/// ⑶ `Home`·`End`·`PageUp`·`PageDown` 이 **살아 있다**(pytmux-374).
///
/// 양성 오라클이다 — 「안 닫힌다」만 재면 넷을 그냥 삼켜 버려도 통과하고, 제보가 신고한
/// 상태(`PageUp`/`PageDown` 이 안 먹는다)가 그대로 남는다. 그래서 **어디로 갔는지**를 잰다.
#[test]
fn page_keys_move_the_settings_cursor() {
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    assert_eq!(screens.selected(), 0);

    assert_eq!(screens.press(Key::PageDown, Mods::NONE), Some(ScreenKey::Consumed));
    let after = screens.selected();
    assert!(after > 0, "PageDown 이 아무 데도 안 갔다");
    assert!(after > 1, "PageDown 이 한 줄만 갔다 — 그건 ↓ 다");
    assert_eq!(screens.top(), Some(Screen::Settings), "PageDown 이 판을 닫았다");

    assert_eq!(screens.press(Key::PageUp, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.selected(), 0, "PageUp 이 제자리로 안 돌아왔다");

    // `Home` 은 맨 위. 아무 데서나 눌러도 0 이다.
    screens.press(Key::PageDown, Mods::NONE);
    assert_eq!(screens.press(Key::Home, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.selected(), 0);

    // `End` 는 **뷰가 자른다**(줄 수를 아는 것은 그리는 쪽) — core 는 상한을 두고 간다.
    assert_eq!(screens.press(Key::End, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.top(), Some(Screen::Settings), "End 가 판을 닫았다");
    screens.clamp_selection(crate::config::SETTINGS.len());
    assert_eq!(screens.selected(), crate::config::SETTINGS.len() - 1, "End 가 끝으로 안 갔다");
}

/// `End` 뒤에 `↓`·`PageDown` 을 더 눌러도 **넘치지 않는다**(뷰가 자르기 전에도).
///
/// core 가 `usize::MAX` 를 두고 가는 규약이라, 그 위에서 `+= 1` 을 하면 디버그 빌드가
/// 그 자리에서 죽는다. 자르는 것은 뷰의 일이고 **안 죽는 것은 여기 일**이다.
#[test]
fn the_settings_cursor_does_not_overflow_past_the_end() {
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    screens.press(Key::End, Mods::NONE);
    screens.press(Key::Down, Mods::NONE);
    screens.press(Key::PageDown, Mods::NONE);
    assert_eq!(screens.selected(), usize::MAX);
    assert_eq!(screens.top(), Some(Screen::Settings));
}

#[test]
fn a_prompt_can_sit_on_top_of_settings() {
    // prefix 를 바꾸는 길이다 — 물음이 설정 화면 **위에** 서고, 답하면 설정 화면이
    // 그대로 돌아온다(스택이 아니라 교체였으면 답한 뒤 화면이 통째로 사라진다).
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    screens.ask(Prompt::SetPrefix, "C-b");
    assert_eq!(screens.top(), Some(Screen::Prompt));
    assert_eq!(screens.typed(), "C-b", "지금 값이 안 채워졌다");
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Answered(Prompt::SetPrefix, "C-b".into()))
    );
    assert_eq!(screens.top(), Some(Screen::Settings));
}

// ── 원격 머지 피커(패리티 G8n) ────────────────────────────────────────────────

#[test]
fn h_and_v_change_the_split_direction() {
    // ★ 고르기 **전에** 방향을 정해야 한다 — 고르면 바로 붙는다.
    let mut screens = Screens::new();
    screens.open(Screen::MergeRemote);
    assert!(!screens.merge_horizontal(), "기본은 상하(파이썬 tb)");
    assert_eq!(
        screens.press(Key::Char('h'), Mods::NONE),
        Some(ScreenKey::Consumed)
    );
    assert!(screens.merge_horizontal());
    assert_eq!(screens.top(), Some(Screen::MergeRemote), "방향키가 화면을 닫았다");
    screens.press(Key::Char('v'), Mods::NONE);
    assert!(!screens.merge_horizontal());
}

#[test]
fn the_merge_picker_still_picks_and_closes() {
    let mut screens = Screens::new();
    screens.open(Screen::MergeRemote);
    screens.press(Key::Down, Mods::NONE);
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Chosen(1))
    );
    assert_eq!(screens.top(), None);
}

#[test]
fn escaping_the_merge_picker_merges_nothing() {
    let mut screens = Screens::new();
    screens.open(Screen::MergeRemote);
    assert_eq!(
        screens.press(Key::Escape, Mods::NONE),
        Some(ScreenKey::Closed)
    );
}

// ── 정보 팝업(패리티 `InfoTabsScreen`) ────────────────────────────────────────

/// 뷰가 프레임마다 하는 **자리 맞추기**를 그대로 흉내낸다(GUI `settle_info_tabs`).
///
/// ⛔ 이걸 안 부르고 `press` 만 하면 아무것도 안 움직인다 — 한 바퀴가 몇 칸인지도, 줄이
/// 몇인지도 core 는 모르기 때문이다(그 규약이 이 판의 요점이라 테스트도 같은 길을 간다).
fn settle(screens: &mut Screens, tabs: usize, actions: usize, rows: usize) {
    screens.wrap_info_focus(tabs);
    screens.place_info_cursor(actions, rows);
}


#[test]
fn the_info_tabs_screen_moves_tabs_with_left_right_not_up_down() {
    // 다른 읽는 화면과 갈리는 유일한 점이다. ↑↓ 가 탭을 옮기면 긴 탭을 훑을 방법이 없다.
    let mut screens = Screens::new();
    screens.open_info_tabs();
    settle(&mut screens, 3, 0, 9);
    assert_eq!(screens.info_tab(), 0);
    screens.press(Key::Right, Mods::NONE);
    settle(&mut screens, 3, 0, 9);
    assert_eq!(screens.info_tab(), 1);
    screens.press(Key::Down, Mods::NONE);
    settle(&mut screens, 3, 0, 9);
    assert_eq!(screens.info_tab(), 1, "↑↓ 가 탭을 옮겼다");
    // ★ ↑↓ 는 이제 **항목 커서**다(pytmux-373 ⑶ · 정본 `ListView` 와 같다).
    assert_eq!(screens.info_row(), 1, "↑↓ 가 항목 커서를 안 옮겼다");
}

#[test]
fn changing_tab_resets_the_scroll_and_the_cursor() {
    // 긴 탭을 훑다 옆으로 가면 짧은 그 탭이 **빈 화면**으로 보인다(스크롤이 내용보다
    // 아래에 있다) — 커서도 없는 줄을 가리킨다.
    let mut screens = Screens::new();
    screens.open_info_tabs();
    settle(&mut screens, 3, 0, 9);
    for _ in 0..5 {
        screens.press(Key::Down, Mods::NONE);
        settle(&mut screens, 3, 0, 9);
    }
    assert_eq!(screens.info_row(), 5);
    screens.press(Key::Right, Mods::NONE);
    screens.wrap_info_focus(3);
    assert_eq!(screens.scroll(), 0, "탭을 바꿨는데 스크롤이 남았다");
    // 새 탭의 커서는 **첫 내용 줄**이다(정본 `lv.index = len(acts)`).
    screens.place_info_cursor(2, 9);
    assert_eq!(screens.info_row(), 2, "탭을 바꿨는데 옛 커서가 남았다");
}

#[test]
fn the_left_right_focus_wraps_through_the_close_button() {
    // pytmux-373 ⑷ — 정본 `_sel` 은 `0..N-1 = 탭 · N = [x]` 한 바퀴다. 닫기가 그
    // 순환에 없으면 **마우스 없이는 못 누른다**(pytmux-185 의 「포커스 이동」).
    let mut screens = Screens::new();
    screens.open_info_tabs();
    settle(&mut screens, 3, 0, 9);
    // 0 에서 왼쪽 → 닫기 `[x]`
    screens.press(Key::Left, Mods::NONE);
    settle(&mut screens, 3, 0, 9);
    assert!(screens.info_close_focused(), "왼쪽으로 갔는데 [x] 에 안 왔다");
    assert_eq!(screens.info_tab(), 0, "[x] 에 왔는데 내용 탭까지 바뀌었다");
    // 닫기에서 왼쪽 → 마지막 탭
    screens.press(Key::Left, Mods::NONE);
    settle(&mut screens, 3, 0, 9);
    assert!(!screens.info_close_focused());
    assert_eq!(screens.info_tab(), 2, "[x] 에서 왼쪽이 마지막 탭이 아니다");
    // 마지막 탭에서 오른쪽 → 닫기, 거기서 오른쪽 → 첫 탭
    screens.press(Key::Right, Mods::NONE);
    settle(&mut screens, 3, 0, 9);
    assert!(screens.info_close_focused(), "오른쪽 끝에서 [x] 에 안 왔다");
    screens.press(Key::Right, Mods::NONE);
    settle(&mut screens, 3, 0, 9);
    assert!(!screens.info_close_focused());
    assert_eq!(screens.info_tab(), 0, "[x] 에서 오른쪽이 첫 탭이 아니다");
}

#[test]
fn enter_on_the_close_button_closes_but_enter_on_a_row_asks_the_view() {
    // pytmux-373 ⑵⑶ — `[x]` 면 닫고, 아니면 **뷰가 판정한다**(동작 줄이면 돌리고 판은
    // 그대로). ⛔ core 가 여기서 닫아 버리면 동작 단추를 눌러도 판이 사라져 결과를 볼
    // 곳이 없다 — 정본은 `_run_action` 뒤에 `_render_tab` 을 다시 그린다.
    let mut screens = Screens::new();
    screens.open_info_tabs();
    settle(&mut screens, 3, 0, 9);
    assert_eq!(
        screens.press(Key::Enter, Mods::NONE),
        Some(ScreenKey::Applied(0)),
        "`Enter` 가 뷰에게 안 넘어왔다"
    );
    assert!(screens.is_open(), "동작 줄일 수도 있는데 core 가 닫았다");
    screens.press(Key::Left, Mods::NONE);
    settle(&mut screens, 3, 0, 9);
    assert!(screens.info_close_focused());
    assert_eq!(screens.press(Key::Enter, Mods::NONE), Some(ScreenKey::Closed));
    assert!(!screens.is_open(), "[x] 에서 `Enter` 를 눌렀는데 안 닫혔다");
}

#[test]
fn the_info_cursor_is_placed_on_the_first_content_line_when_it_opens() {
    // 정본 `lv.index = len(acts)` — **정보가 먼저 보이게** 커서를 동작 단추 아래에 놓는다.
    // 단추 위에 놓으면 판을 열자마자 파괴적일 수도 있는 줄이 골라져 있다.
    let mut screens = Screens::new();
    screens.open_info_tabs();
    screens.place_info_cursor(2, 8);
    assert_eq!(screens.info_row(), 2, "커서가 동작 단추 위에 놓였다");
    // 두 번째부터는 **안 옮긴다**(사람이 옮긴 자리를 프레임마다 되돌리면 못 움직인다).
    screens.press(Key::Down, Mods::NONE);
    screens.place_info_cursor(2, 8);
    assert_eq!(screens.info_row(), 3);
    screens.place_info_cursor(2, 8);
    assert_eq!(screens.info_row(), 3, "프레임마다 커서를 되돌린다");
    // 줄 밖으로는 못 나간다 — 없는 줄에 `Enter` 를 치면 뷰가 빈 곳을 실행한다.
    for _ in 0..20 {
        screens.press(Key::Down, Mods::NONE);
    }
    screens.place_info_cursor(2, 8);
    assert_eq!(screens.info_row(), 7, "커서가 목록 밖으로 나갔다");
    // `Home`·`End` 도 커서다(정본 `InfoScreen` 계열과 같다 — pytmux-273 ①).
    screens.press(Key::Home, Mods::NONE);
    screens.place_info_cursor(2, 8);
    assert_eq!(screens.info_row(), 0);
    screens.press(Key::End, Mods::NONE);
    screens.place_info_cursor(2, 8);
    assert_eq!(screens.info_row(), 7);
}

#[test]
fn any_other_key_closes_the_info_tabs_screen() {
    let mut screens = Screens::new();
    screens.open_info_tabs();
    assert_eq!(screens.press(Key::Char('q'), Mods::NONE), Some(ScreenKey::Closed));
    assert!(!screens.is_open());
}

// ── 확인 화면의 동적 상세(재시작 드라이런의 실패 목록) ────────────────────────

#[test]
fn a_confirm_can_carry_detail_above_the_question() {
    // 물음은 정적이고(화면마다 같아야 한다) "무엇이 실패했나"는 매번 다르다. 그 글이
    // 없으면 사용자는 무엇을 보고 판단할지 알 수 없다.
    let mut screens = Screens::new();
    screens.confirm_with(Prompt::RestartAll, "✗ 서버 re-exec 지원".to_owned());
    assert_eq!(screens.detail(), "✗ 서버 re-exec 지원");
    assert_eq!(screens.asking(), Some(Prompt::RestartAll));
}

#[test]
fn the_detail_does_not_leak_into_the_next_confirm() {
    // ★ 안 지우면 **다음 물음 위에 지난 글**이 붙는다 — 그러면 그 확인 화면이 거짓말을
    // 한다(되돌릴 수 없는 것 앞에 서는 화면이다).
    let mut screens = Screens::new();
    screens.confirm_with(Prompt::RestartAll, "✗ 지난 실패".to_owned());
    screens.close_top();
    screens.confirm(Prompt::KillServer);
    assert_eq!(screens.detail(), "", "지난 상세가 남았다");
}

#[test]
fn the_prompt_history_narrows_picks_and_fills() {
    // 파이썬 arghist 의 손: 최근-우선 목록에서 친 글로 좁히고, ↑↓ 로 고르고,
    // Tab 이 입력칸에 채운다. Enter 는 언제나 **입력칸의 글**이다.
    let mut screens = Screens::new();
    screens.ask(Prompt::RemoteAttach, "");
    // 지연 채움 규약: 연 직후에는 미채움이고, 뷰가 이걸 보고 한 번 채운다.
    assert_eq!(screens.asking_unfilled(), Some(Prompt::RemoteAttach));
    screens.set_prompt_history(vec!["box2".into(), "box1".into(), "alpha".into()]);
    assert_eq!(screens.asking_unfilled(), None, "채웠으면 다시 채우자고 하지 않는다");
    // 좁히기: b 를 치면 box* 둘만 남는다.
    screens.press(Key::Char('b'), Mods::NONE);
    assert_eq!(screens.prompt_matches(), vec!["box2", "box1"]);
    // ↑ 는 끝(가장 오래된 쪽)부터, Tab 이 입력칸에 채운다.
    screens.press(Key::Up, Mods::NONE);
    screens.press(Key::Tab, Mods::NONE);
    assert_eq!(screens.typed(), "box1");
    // 채우고 나면 그 글로 다시 좁혀진다 — "box1" 로 시작하는 다른 후보가 없고
    // 자기 자신은 제안하지 않으므로 빈 목록이다.
    assert!(screens.prompt_matches().is_empty());
    // Enter 는 입력칸의 글 그대로.
    let out = screens.press(Key::Enter, Mods::NONE);
    assert_eq!(
        out,
        Some(ScreenKey::Answered(Prompt::RemoteAttach, "box1".into()))
    );
}

#[test]
fn a_prompt_without_history_ignores_up_instead_of_closing() {
    // 이력이 없으면(빈 목록) ↑↓·Tab 은 이력 갈래를 안 타고 기본 팔로 떨어진다.
    // ⚠ 종전에는 그 기본 팔이 "정의된 키가 아니면 닫기"라 ↑ 가 물음을 닫았다
    // (pytmux-174·273) — 정본 `Input` 은 모르는 키에 아무 일도 안 한다. 편집 중
    // 화면이 조용히 사라지는 쪽이 훨씬 나쁘다.
    let mut screens = Screens::new();
    screens.ask(Prompt::RenameTab, "이름");
    screens.set_prompt_history(Vec::new());
    let out = screens.press(Key::Up, Mods::NONE);
    assert_eq!(out, Some(ScreenKey::Consumed), "이력 없는 물음의 ↑ 가 화면을 닫혔다");
    assert!(screens.is_open());
}

// ── 곁일 셋(2026-07-31) ────────────────────────────────────────────────────────

/// `←→` 가 **값 변경**이고 방향이 실린다(정본 설정 화면의 셋째 칸).
#[test]
fn arrows_change_the_setting_value_in_both_directions() {
    let mut screens = Screens::new();
    screens.open(Screen::Settings);
    assert_eq!(
        screens.press(Key::Right, Mods::NONE),
        Some(ScreenKey::AppliedDir(0, true))
    );
    assert_eq!(
        screens.press(Key::Left, Mods::NONE),
        Some(ScreenKey::AppliedDir(0, false))
    );
    // 화면은 그대로다 — 값만 바꾸는 키다.
    assert_eq!(screens.top(), Some(Screen::Settings));
}

/// 선택지가 셋 이상인 줄에서 **뒤로도 돈다**. 앞으로만 되면 하나 지나쳤을 때 한 바퀴를
/// 더 돌아야 하고, 그건 "값을 고른다"가 아니라 "값을 감는다"다.
#[test]
fn a_three_way_setting_steps_backwards_too() {
    use crate::config::{SETTINGS, SettingPick, SettingValues, setting_pick_dir};
    let row = SETTINGS
        .iter()
        .position(|s| s.key == "window-size")
        .expect("window-size 줄이 있다");
    let values = SettingValues { window_size: "latest".into(), ..Default::default() };
    let picked = |forward| match setting_pick_dir(row, &values, forward) {
        Some(SettingPick::Act(crate::Action::SetEnum(_, value))) => value,
        other => panic!("{other:?}"),
    };
    assert_eq!(picked(true), "largest", "smallest·latest·largest 에서 다음");
    assert_eq!(picked(false), "smallest", "이전");
}

/// **설정 파일** 쪽 선택지 줄도 뒤로 돈다(`ConfigEnum` — 위 `Enum` 과 다른 팔이라
/// 하나만 재면 나머지 팔이 방향을 무시해도 안 잡힌다. 실제로 변이가 살아남았다).
#[test]
fn a_config_enum_setting_steps_backwards_too() {
    use crate::config::{SETTINGS, SettingPick, SettingValues, setting_pick_dir};
    let row = SETTINGS
        .iter()
        .position(|s| s.key == "ambiguous-width")
        .expect("ambiguous-width 줄이 있다");
    let values = SettingValues { ambiguous_width: "narrow".into(), ..Default::default() };
    let picked = |forward| match setting_pick_dir(row, &values, forward) {
        Some(SettingPick::Set(_, value)) => value,
        other => panic!("{other:?}"),
    };
    assert_eq!(picked(true), "wide", "auto·narrow·wide 에서 다음");
    assert_eq!(picked(false), "auto", "이전");
}

/// 숫자 줄은 **양끝에서 반대쪽으로 감는다** — 0.0 에서 왼쪽이 막히면 "안 먹는다"로 읽힌다.
#[test]
fn a_number_setting_wraps_at_both_ends() {
    use crate::config::{SETTINGS, SettingPick, SettingValues, setting_pick_dir};
    let row = SETTINGS
        .iter()
        .position(|s| s.key == "inactive-dim-ratio")
        .expect("줄이 있다");
    let values = SettingValues { inactive_dim_ratio: 0.0, ..Default::default() };
    match setting_pick_dir(row, &values, false) {
        Some(SettingPick::SetNumber(_, value)) => {
            assert!(value > 0.7, "0 에서 왼쪽이 위로 안 감겼다: {value}")
        }
        other => panic!("{other:?}"),
    }
}

/// 메뉴 토글은 **지금 값을 옆에 달고**, 골라도 **메뉴가 안 닫힌다**(정본과 같다).
#[test]
fn a_menu_toggle_shows_its_state_and_keeps_the_menu_open() {
    use crate::{MENU_TOGGLES, MenuToggles, menu_toggle_mark};
    let on = MenuToggles { zoom: true, sync: false, ..Default::default() };
    assert_eq!(menu_toggle_mark("zoom", &on), Some("●"));
    assert_eq!(menu_toggle_mark("sync", &on), Some("○"));
    assert_eq!(menu_toggle_mark("settings", &on), None, "토글이 아닌 줄에 표식이 붙었다");
    // 표의 다섯이 **전부** 표식을 낸다(하나라도 빠지면 그 줄만 상태를 안 보인다).
    for key in MENU_TOGGLES {
        assert!(menu_toggle_mark(key, &on).is_some(), "{key} 에 표식이 없다");
    }

    // 골라도 안 닫힌다 — 토글은 보통 여러 개를 잇달아 만진다.
    let mut screens = Screens::new();
    screens.open(Screen::Menu);
    let rows = screens.menu_rows();
    let at = rows
        .iter()
        .position(|r| matches!(r, crate::MenuRow::Item(e) if crate::menu_is_toggle(e.key)))
        .expect("최상위에 토글 줄이 있다");
    screens.panel_click(crate::PanelTarget::Row(at));
    assert_eq!(screens.press(Key::Enter, Mods::NONE), Some(ScreenKey::Chosen(at)));
    assert_eq!(screens.top(), Some(Screen::Menu), "토글인데 메뉴가 닫혔다");
}

/// 팔레트가 **설명으로도** 걸리되, 이름을 아는 사람의 길이 막히지 않는다.
#[test]
fn the_palette_matches_descriptions_but_ranks_names_first() {
    use crate::keymap::{PALETTE, palette_matches_with};
    // `nest-auto-attach` 의 설명에는 `remote-attach` 가 들어 있다(정본 문구).
    let desc = |name: &str| match name {
        "nest-auto-attach" => Some("원격에서 pytmux 실행 시 거부 대신 자동 remote-attach 승격"),
        _ => None,
    };
    let hits = palette_matches_with(None, "remote-attach", desc);
    assert!(!hits.is_empty());
    assert_eq!(
        PALETTE[hits[0]].name, "remote-attach",
        "이름을 다 쳤는데 설명으로 걸린 줄이 먼저 왔다"
    );
    assert!(
        hits.iter().any(|i| PALETTE[*i].name == "nest-auto-attach"),
        "설명 매칭이 아예 안 걸렸다"
    );
}

/// 구분자를 무엇으로 치든 같은 명령에 걸린다(정본 `norm_sep`).
#[test]
fn separators_do_not_matter_when_searching() {
    use crate::keymap::{PALETTE, palette_matches_in};
    for typed in ["rename tab", "rename_tab", "rename-tab"] {
        let hits = palette_matches_in(None, typed);
        assert_eq!(
            PALETTE.get(hits.first().copied().unwrap_or(usize::MAX)).map(|e| e.name),
            Some("rename-tab"),
            "{typed:?} 로 못 찾는다"
        );
    }
}

#[test]
fn the_kill_tab_question_knows_which_situation_it_is_in() {
    // ★ 정본 `client.py::confirm_kill_tab` 의 네 갈래. **양성 오라클**이다 — 갈래마다
    //   "무엇을 잃는가"가 실제로 다른 글로 나오는지 잰다.
    let cases: [(TabFacts, Prompt, &str); 4] = [
        // 마지막 로컬 탭 + 원격 탭도 열림 → 원격 보기까지 끊긴다는 것을 함께 적는다.
        (
            TabFacts { local: 1, has_remote: true, active_pinned: false },
            Prompt::KillTabLastRemote,
            "원격",
        ),
        // 마지막 로컬 탭 → 앱이 통째로 끝난다.
        (
            TabFacts { local: 1, has_remote: false, active_pinned: false },
            Prompt::KillTabLast,
            "pytmux",
        ),
        // 여럿 중 고정 탭.
        (
            TabFacts { local: 3, has_remote: false, active_pinned: true },
            Prompt::KillTabPinned,
            "고정",
        ),
        // 평범한 탭.
        (
            TabFacts { local: 3, has_remote: false, active_pinned: false },
            Prompt::KillTab,
            "탭의 셸",
        ),
    ];
    for (facts, want, needle) in cases {
        let got = Prompt::kill_tab(&facts);
        assert_eq!(got, want, "{facts:?}");
        assert!(
            got.question().contains(needle),
            "{facts:?} → 물음에 {needle:?} 가 없다: {:?}",
            got.question()
        );
    }

    // ★ **로컬** 수로 센다. 원격 탭이 함께 열려 있으면 전체 수는 2 이상이라, 전체로 세면
    //   마지막 로컬 탭을 닫는 경고가 통째로 빠진다(정본이 실제로 고친 결함).
    assert_eq!(
        Prompt::kill_tab(&TabFacts { local: 1, has_remote: true, active_pinned: true }),
        Prompt::KillTabLastRemote,
        "마지막 로컬 탭인데 고정 여부가 경고를 덮었다"
    );
}

#[test]
fn the_confirm_panel_titles_and_buttons_follow_the_question() {
    // 판을 열면 **가장 먼저 읽히는 글**이 제목이고, 그 다음이 버튼 낱말이다. 둘 다
    // 물음을 모르면 사용자는 본문을 다 읽어야만 무슨 일이 나는지 안다.
    let cases: [(Prompt, &str, &str); 6] = [
        (Prompt::KillTab, "탭 닫기", "닫기"),
        (Prompt::KillTabLast, "pytmux 종료", "닫기"),
        (Prompt::KillTabLastRemote, "pytmux 종료", "닫기"),
        (Prompt::KillTabPinned, "고정 탭 닫기", "닫기"),
        (Prompt::KillServer, "서버 종료", "종료"),
        (Prompt::RestartAll, "재시작 확인", "재시작"),
    ];
    for (prompt, title, yes) in cases {
        let mut screens = Screens::new();
        screens.confirm(prompt);
        assert_eq!(screens.confirm_title(), Some(title), "{prompt:?} 제목");
        assert_eq!(screens.confirm_buttons(), [yes, "취소"], "{prompt:?} 버튼");
    }
    // 확인 판이 아닌 물음(입력)은 제목을 덮지 않는다 — 뷰가 화면 기본 제목을 쓴다.
    let mut screens = Screens::new();
    screens.ask(Prompt::RenameTab, "");
    assert_eq!(screens.confirm_title(), None);
}

#[test]
fn the_pinned_question_names_the_tab_and_does_not_repeat_it() {
    // 정본 `dialog.kill_pinned_msg` 는 이름을 **문장 안에** 넣는다. 우리 `detail` 자리는
    // 원래 "물음 위 여러 줄"용이라, 슬롯 채우기로 쓴 값이 위에 또 뜨면 같은 낱말이 두 번
    // 보인다 — 그래서 `confirm_detail` 이 그 경우를 비운다.
    let mut screens = Screens::new();
    screens.confirm_with(Prompt::KillTabPinned, "빌드".to_owned());
    let q = screens.confirm_question();
    assert!(q.contains("빌드"), "고정 탭 이름이 물음에 안 들어갔다: {q:?}");
    assert!(!q.contains("{name}"), "슬롯이 안 채워졌다: {q:?}");
    assert_eq!(screens.confirm_detail(), "", "슬롯 값이 별도 줄로 또 보인다");

    // 부가 줄이 **뜻을 갖는** 판(재시작 드라이런)은 그대로 위에 보인다.
    let mut screens = Screens::new();
    screens.confirm_with(Prompt::RestartAll, "a\nb".to_owned());
    assert_eq!(screens.confirm_detail(), "a\nb");
    assert_eq!(screens.confirm_question(), Prompt::RestartAll.question());
}

#[test]
fn only_the_irreversible_confirms_are_marked_dangerous() {
    // ★ 정본 `confirm_popup(..., danger=…)` 의 경계 그대로. **양쪽을 다 잰다** —
    //   "위험한 것이 위험하다"만 재면 전부 위험으로 칠해도 통과하고, 그러면 붉은색이
    //   값을 잃는다(아무 데나 서 있는 경고는 아무도 안 읽는다).
    for prompt in [
        Prompt::KillTabLast,
        Prompt::KillTabLastRemote,
        Prompt::KillTabPinned,
        Prompt::KillServer,
        Prompt::RestartAll,
    ] {
        assert!(prompt.is_dangerous(), "{prompt:?} 가 위험 표시를 안 받는다");
    }
    for prompt in [Prompt::KillTab, Prompt::KillPane, Prompt::RenameTab, Prompt::MoveTab] {
        assert!(!prompt.is_dangerous(), "{prompt:?} 까지 붉게 칠한다");
    }

    // 판이 열려 있을 때만 뜻이 있다 — 안 열렸는데 위험하다고 하면 뷰가 헛칠한다.
    let mut screens = Screens::new();
    assert!(!screens.confirm_is_dangerous());
    screens.confirm(Prompt::KillServer);
    assert!(screens.confirm_is_dangerous());
    screens.confirm(Prompt::KillPane);
    assert!(!screens.confirm_is_dangerous(), "물음을 바꿨는데 위험 표시가 남았다");
}

// ── 다열 판의 손(설계 §4.3 `panel` · pytmux-126) ──────────────────────────────

#[test]
fn a_multi_column_plugin_panel_moves_a_whole_column_sideways() {
    // 정본 mdir 의 손 그대로다 — 채움이 세로 우선이라 ←→ 는 «열당 줄 수»만큼 뛴다.
    let mut screens = Screens::new();
    screens.open_plugin_view(true);
    screens.set_plugin_grid(10, 3);
    screens.select_row(0);

    assert_eq!(screens.press(Key::Right, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.selected(), 10, "→ 가 한 열을 안 건넜다");
    assert_eq!(screens.press(Key::Down, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.selected(), 11, "↓ 는 여전히 한 줄이다");
    assert_eq!(screens.press(Key::PageDown, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.selected(), 41, "PgDn 이 한 판(열당 줄 수 × 열 수)을 안 건넜다");
    assert_eq!(screens.press(Key::PageUp, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.selected(), 11);
    assert_eq!(screens.press(Key::Left, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.selected(), 1);
    // 맨 앞에서 더 왼쪽은 **판을 안 닫는다** — 0 에서 멈춘다(위로 넘치지 않는다).
    assert_eq!(screens.press(Key::Left, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.selected(), 0);
    assert_eq!(screens.top(), Some(Screen::PluginView), "←가 판을 닫았다");
}

#[test]
fn a_single_column_plugin_list_ignores_left_and_right() {
    // 한 열이면 ←→ 는 이 목록의 정의된 키가 아니다. ⚠ 종전에는 목록형 화면 전체가
    // `InfoScreen` 의 "아무 키나 닫기"를 기본값으로 물려받아 여기서도 판을 닫았다
    // (pytmux-181·273) — 정본의 목록 화면(Textual `ListView`/`OptionList`)은 정의 안 된
    // 키에 아무 일도 안 하고, `Esc` 만 닫는다.
    let mut screens = Screens::new();
    screens.open_plugin_view(true);
    screens.set_plugin_grid(10, 1);
    screens.select_row(3);
    assert_eq!(screens.press(Key::Right, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.top(), Some(Screen::PluginView), "→가 판을 닫았다");
    assert_eq!(screens.selected(), 3, "→가 선택을 옮겼다");
}

#[test]
fn a_panel_that_never_got_its_geometry_still_behaves_like_a_list() {
    // 뷰가 기하를 안 넣었으면(옛 뷰·글 화면) `(0, 0)` 이다 — 0 으로 나누거나 제자리에
    // 멈추는 대신 **종전 목록 그대로** 굴러야 한다.
    let mut screens = Screens::new();
    screens.open_plugin_view(true);
    screens.select_row(2);
    assert_eq!(screens.press(Key::Down, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.selected(), 3);
    // ←는 이 목록의 키가 아니다(pytmux-181) — 삼킨다, 닫지 않는다.
    assert_eq!(screens.press(Key::Left, Mods::NONE), Some(ScreenKey::Consumed));
    assert_eq!(screens.top(), Some(Screen::PluginView));
}
