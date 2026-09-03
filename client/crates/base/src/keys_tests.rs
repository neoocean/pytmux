//! 키 인코딩·모드 라우팅 회귀.
//!
//! 이 표가 틀리면 증상이 **"타이핑이 이상하다"** 로만 나타나 원인을 찾기 어렵다 —
//! 그래서 바이트를 직접 못박는다. 참조는 파이썬 클라가 보내는 것과 같은 형식이다
//! (같은 서버·같은 셸을 먹이므로 두 클라가 다른 바이트를 보내면 그 자체가 결함이다).

use super::*;
use crate::Action;

fn to_pane(mode: InputMode, key: Key, mods: Mods) -> Vec<u8> {
    match interpret(mode, key, mods) {
        KeyOutcome::ToPane(bytes) => bytes,
        other => panic!("패널로 안 갔다: {other:?}"),
    }
}

#[test]
fn plain_characters_go_to_the_pane_as_utf8() {
    assert_eq!(to_pane(InputMode::Normal, Key::Char('a'), Mods::NONE), b"a");
    // 한글은 UTF-8 바이트 그대로 — 서버가 그대로 pty 에 쓴다.
    assert_eq!(
        to_pane(InputMode::Normal, Key::Char('한'), Mods::NONE),
        "한".as_bytes()
    );
}

#[test]
fn enter_is_carriage_return_not_newline() {
    // 셸은 CR 을 줄 확정으로 본다. LF 를 보내면 프롬프트가 안 넘어간다.
    assert_eq!(to_pane(InputMode::Normal, Key::Enter, Mods::NONE), b"\r");
}

#[test]
fn backspace_is_del_not_bs() {
    // 유닉스 단말 관례는 0x7f 다. 0x08 을 보내면 셸이 다른 편집키로 읽는다.
    assert_eq!(to_pane(InputMode::Normal, Key::Backspace, Mods::NONE), &[0x7f]);
}

#[test]
fn ctrl_letters_become_control_codes() {
    assert_eq!(to_pane(InputMode::Normal, Key::Char('c'), Mods::CTRL), &[0x03]);
    assert_eq!(to_pane(InputMode::Normal, Key::Char('d'), Mods::CTRL), &[0x04]);
    // 대문자로 눌러도 같은 코드(Shift+Ctrl+C).
    assert_eq!(to_pane(InputMode::Normal, Key::Char('C'), Mods::CTRL), &[0x03]);
    // Ctrl+Space = NUL, Ctrl+[ = ESC(관례).
    assert_eq!(to_pane(InputMode::Normal, Key::Char(' '), Mods::CTRL), &[0x00]);
    assert_eq!(to_pane(InputMode::Normal, Key::Char('['), Mods::CTRL), &[0x1b]);
}

#[test]
fn unknown_ctrl_combinations_are_dropped_not_guessed() {
    // 뜻이 정해지지 않은 조합을 아무 바이트로 보내면 자식이 쓰레기를 받는다.
    assert_eq!(
        interpret(InputMode::Normal, Key::Char('한'), Mods::CTRL),
        KeyOutcome::Ignored
    );
}

#[test]
fn arrows_use_csi_form_which_works_in_both_cursor_modes() {
    assert_eq!(to_pane(InputMode::Normal, Key::Up, Mods::NONE), b"\x1b[A");
    assert_eq!(to_pane(InputMode::Normal, Key::Down, Mods::NONE), b"\x1b[B");
    assert_eq!(to_pane(InputMode::Normal, Key::Right, Mods::NONE), b"\x1b[C");
    assert_eq!(to_pane(InputMode::Normal, Key::Left, Mods::NONE), b"\x1b[D");
}

#[test]
fn alt_prefixes_esc_but_never_doubles_an_existing_escape() {
    assert_eq!(to_pane(InputMode::Normal, Key::Char('b'), Mods::ALT), b"\x1bb");
    // 이미 ESC 로 시작하는 시퀀스에 ESC 를 또 붙이면 자식이 다른 키로 읽는다.
    let alt_up = Mods {
        ctrl: false,
        alt: true,
    };
    assert_eq!(to_pane(InputMode::Normal, Key::Up, alt_up), b"\x1b[A");
}

#[test]
fn function_keys_and_editing_keys_have_their_sequences() {
    assert_eq!(encode(Key::Function(1), Mods::NONE).unwrap(), b"\x1bOP");
    assert_eq!(encode(Key::Function(5), Mods::NONE).unwrap(), b"\x1b[15~");
    assert_eq!(encode(Key::Function(12), Mods::NONE).unwrap(), b"\x1b[24~");
    assert!(encode(Key::Function(13), Mods::NONE).is_none(), "없는 키");
    assert_eq!(encode(Key::Delete, Mods::NONE).unwrap(), b"\x1b[3~");
    assert_eq!(encode(Key::BackTab, Mods::NONE).unwrap(), b"\x1b[Z");
}

// ---- 모드 ------------------------------------------------------------------

#[test]
fn normal_mode_sends_letters_that_command_mode_would_eat() {
    // 이 클라가 쓸모 있으려면 vim 에서 j 를 누를 수 있어야 한다. `j` 는 명령 모드에서
    // 탭 이동이지만 Normal 에서는 **패널로** 가야 한다.
    assert_eq!(to_pane(InputMode::Normal, Key::Char('j'), Mods::NONE), b"j");
    assert_eq!(to_pane(InputMode::Normal, Key::Char('q'), Mods::NONE), b"q");
}

#[test]
fn escape_enters_command_mode_and_is_swallowed() {
    assert_eq!(
        interpret(InputMode::Normal, Key::Escape, Mods::NONE),
        KeyOutcome::ModeChanged(InputMode::Command)
    );
}

#[test]
fn a_second_escape_only_leaves_the_mode_and_sends_nothing() {
    // ★ **뒤집힌 단언이다**(pytmux-33 ⓖ3 · 2026-09-02). 종전에는 여기서 둘째 ESC 가
    //   패널로 간다고 못박았는데, **정본은 정확히 그 반대**를 사용자 요청으로 못박아
    //   두었다: *"모드 진입/종료에 쓴 ESC 가 앱으로 새지 않게 한다"*
    //   (`clientio._handle_esc_mode` · 56632 불변).
    //
    //   자식(vim·Claude)이 ESC 를 받는 길은 그래서 **명시적인 셋**이다 — `Shift+ESC` ·
    //   `esc e` · `send-escape`. 재는 자는 `proto` 의 `mode_transition_conformance.rs`
    //   이고, 그것이 정본 소스에서 이 규칙을 직접 뽑아 온다.
    assert_eq!(
        interpret(InputMode::Command, Key::Escape, Mods::NONE),
        KeyOutcome::ModeChanged(InputMode::Normal)
    );
    // 그 대신 `Shift+ESC` 는 **모드 안에서도** 패널에 ESC 를 준다(정본 `e_sesc` 와 같다).
    assert_eq!(to_pane(InputMode::Command, Key::ShiftEscape, Mods::NONE), &[0x1b]);
}

#[test]
fn command_mode_maps_keys_through_the_shared_binding_table() {
    // 표를 여기서 다시 적지 않는다 — BINDINGS 가 정본이고 그 표를 따라간다.
    //
    // ★ **뒤집힌 단언 둘**(pytmux-466 · 449 ⑶): `j`(SelectNext)·`q`(Quit)는 **데모 판의
    //   키**라 `BLOCK_BINDINGS` 로 갔다. 세션 esc 모드에서 그 둘은 이제 모르는 키이고,
    //   모르는 키는 정본과 같이 **모드만 푼다**(그 대조는
    //   `proto/tests/mode_transition_conformance.rs` 가 정본 픽스처와 견준다).
    for key in [Key::Char('j'), Key::Char('q')] {
        assert_eq!(
            interpret(InputMode::Command, key, Mods::NONE),
            KeyOutcome::Ignored,
            "{key:?} 가 아직 esc 표에 있다 — 데모 판 키가 세션 모드로 샌다"
        );
    }
    // ★ G1c 이후 화살표는 **패널 이동**이다(파이썬 esc 모드와 같다).
    assert_eq!(
        interpret(InputMode::Command, Key::Down, Mods::NONE),
        KeyOutcome::Action(Action::SelectPane(crate::Dir::Down))
    );
    assert_eq!(
        interpret(InputMode::Command, Key::Char('n'), Mods::NONE),
        KeyOutcome::Action(Action::NewTab)
    );
    assert_eq!(
        interpret(InputMode::Command, Key::Char('p'), Mods::NONE),
        KeyOutcome::Action(Action::SplitTopBottom)
    );
    // 번호는 표가 아니라 규칙이다(prefix 와 같은 자리).
    assert_eq!(
        interpret(InputMode::Command, Key::Char('3'), Mods::NONE),
        KeyOutcome::Action(Action::SelectTab(3))
    );
    // 표에 없는 키는 조용히 무시(모드에서 아무 일도 안 일어난다).
    assert_eq!(
        interpret(InputMode::Command, Key::Char('z'), Mods::NONE),
        KeyOutcome::Ignored
    );
}

#[test]
fn command_mode_ignores_modified_keys() {
    // Ctrl+j 를 탭 이동으로 읽으면 사용자가 의도한 제어코드를 잃는다.
    assert_eq!(
        interpret(InputMode::Command, Key::Char('j'), Mods::CTRL),
        KeyOutcome::Ignored
    );
}

#[test]
fn every_binding_name_round_trips_through_the_reverse_mapping() {
    // 표를 읽는 곳이 셋이다: core 의 `binding_name`(여기) · TUI 이벤트 핸들러 ·
    // GUI 키맵 등록. 셋이 같은 문법을 안 쓰면 **그 키만 조용히 안 먹는다**.
    //
    // 실제로 그랬다: 표에 대문자 `G` 가 있었는데 core·TUI 는 원시 문자열로 맞춰 보고
    // GUI 는 `Keystroke::parse` 로 읽어 첫 프레임에 패닉했다. GUI 가 P1 에 멈춰 있어
    // 아무도 띄워 본 적이 없어서 몇 달을 그대로 지났다(2026-07-28 발견).
    for binding in crate::BINDINGS {
        // 수정키는 이름 앞에 접두로 붙는다(`ctrl-up`). 표기의 주인은
        // `binding_name_with` 라 여기서도 같은 문법으로 되돌린다.
        let (mods, name) = match binding.key.strip_prefix("ctrl-") {
            Some(rest) => (Mods::CTRL, rest),
            None => (Mods::NONE, binding.key),
        };
        let key = match name {
            "enter" => Key::Enter,
            "space" => Key::Char(' '),
            "escape" => Key::Escape,
            "up" => Key::Up,
            "down" => Key::Down,
            "left" => Key::Left,
            "right" => Key::Right,
            "tab" => Key::Tab,
            "insert" => Key::Insert,
            // Shift 를 **키 쪽에 접은** 것들(`Mods` 에 shift 가 없다는 계약).
            "shift-delete" => Key::ShiftDelete,
            s if s.starts_with("shift-") && s.chars().count() == 7 => {
                Key::Char(s.chars().next_back().unwrap())
            }
            s if s.chars().count() == 1 => Key::Char(s.chars().next().unwrap()),
            other => panic!("표의 키 '{other}' 를 이 테스트가 모른다 — 매핑을 추가할 것"),
        };
        assert_eq!(
            super::binding_name_with(key, mods).as_deref(),
            Some(binding.key),
            "표의 '{}' 와 역매핑이 어긋난다 — 그 키만 조용히 안 먹는다",
            binding.key
        );
        // 그리고 **표를 찾는 경로**로도 되돌아와야 한다. 역매핑만 맞고 조회가 어긋나면
        // (수정키를 버리던 종전 `command_action` 이 그랬다) 그 키는 여전히 안 먹는다.
        assert_eq!(
            command_action(key, mods),
            Some(binding.action),
            "표의 '{}' 가 command_action 으로는 안 찾아진다",
            binding.key
        );
    }
}

#[test]
fn a_function_key_has_a_name_even_though_our_table_has_none() {
    // ★ pytmux-125 — F-키는 [`BINDINGS`] 에 한 줄도 없지만 **플러그인 스펙이 가져간다**
    //   (mdir 의 `F10` 트리·`F5` 복사). 이름이 없으면 서버가 `"f10"` 을 광고해도 그 표에서
    //   영영 안 찾아지고, 증상은 "F-키만 안 먹는다"다 — `Home` 이 여기 있는 이유와 같다.
    assert_eq!(super::binding_name(Key::Function(10)).as_deref(), Some("f10"));
    assert_eq!(super::binding_name(Key::Function(1)).as_deref(), Some("f1"));
    // 수정키 접두 문법도 같은 자가 짓는다(두 벌이 되면 `alt-f5` 가 갈린다).
    assert_eq!(
        super::binding_name_with(Key::Function(5), Mods::ALT).as_deref(),
        Some("alt-f5")
    );
    // ⛔ 그런데 우리 표에는 여전히 한 줄도 없어야 한다 — 이름이 생겼다고 esc 모드나
    //    prefix 가 그 키를 먹기 시작하면 패널 안 프로그램의 F-키가 사라진다.
    assert_eq!(command_action(Key::Function(10), Mods::NONE), None);
    assert!(
        !crate::BINDINGS.iter().any(|b| b.key.starts_with('f') && b.key.len() > 1
            && b.key[1..].chars().all(|c| c.is_ascii_digit())),
        "표에 F-키가 생겼다 — 그러면 패널 안 프로그램의 그 키가 사라진다"
    );
}

#[test]
fn the_harness_token_table_can_press_a_function_key() {
    // 하네스(`--frame-keys`)가 이 키를 못 넣으면 mdir 의 F-키가 든 화면을 **영영 못
    // 찍는다** — 맥에서는 창에 키를 따로 넣을 길이 없다(`insert` 가 토큰인 이유와 같다).
    assert_eq!(
        super::parse_token("f10"),
        Some((Key::Function(10), Mods::NONE))
    );
    assert_eq!(super::parse_token("f5"), Some((Key::Function(5), Mods::NONE)));
    // ⛔ 낱글자 `f` 는 여전히 **글자**다 — 여기서 삼키면 mdir 의 빨리찾기가 죽는다.
    assert_eq!(super::parse_token("f"), Some((Key::Char('f'), Mods::NONE)));
    assert_eq!(super::parse_token("foo"), None);
}

#[test]
fn the_harness_token_table_can_press_the_page_keys() {
    // 같은 사유(pytmux-374 ⑴) — 목록을 굴린 뒤의 그림은 이 넷 없이는 못 찍는다.
    assert_eq!(super::parse_token("home"), Some((Key::Home, Mods::NONE)));
    assert_eq!(super::parse_token("end"), Some((Key::End, Mods::NONE)));
    assert_eq!(super::parse_token("pageup"), Some((Key::PageUp, Mods::NONE)));
    assert_eq!(super::parse_token("pagedown"), Some((Key::PageDown, Mods::NONE)));
}

#[test]
fn a_shifted_letter_is_written_with_the_shift_prefix() {
    // 이 규칙이 문법의 전부다. 어기면 GUI 는 패닉하고(디버그) 릴리스에서는 그 키가
    // 영영 안 먹는다 — 후자가 더 나쁘다.
    assert_eq!(super::binding_name(Key::Char('G')).as_deref(), Some("shift-G"));
    assert_eq!(super::binding_name(Key::Char('g')).as_deref(), Some("g"));
}

#[test]
fn every_help_binding_is_reachable_from_command_mode() {
    // 도움말에 적힌 키가 실제로 동작하는지 — 표와 라우팅이 갈라지는 것을 막는다.
    for binding in crate::BINDINGS.iter().filter(|b| b.show_in_help) {
        let key = match binding.key {
            "enter" => Key::Enter,
            "space" => Key::Char(' '),
            "escape" => Key::Escape,
            "up" => Key::Up,
            "down" => Key::Down,
            "left" => Key::Left,
            "right" => Key::Right,
            "tab" => Key::Tab,
            // `shift-X` 는 대문자 글자 하나다(표의 문법 — `binding_name` 참조).
            s if s.starts_with("shift-") && s.chars().count() == 7 => {
                Key::Char(s.chars().next_back().unwrap())
            }
            s if s.chars().count() == 1 => Key::Char(s.chars().next().unwrap()),
            other => panic!("도움말 키 '{other}' 를 이 테스트가 모른다 — 매핑을 추가할 것"),
        };
        if key == Key::Escape {
            continue; // ESC 는 모드 전용 규칙(위 테스트가 따로 덮는다)
        }
        // 액션 중에는 **모드 전이로 끝나는 것**도 있다(스크롤 모드 진입). 그때도 키가
        // 죽어 있으면 안 된다는 것이 이 테스트가 지키는 것이므로, 둘 다 통과로 본다.
        let outcome = interpret(InputMode::Command, key, Mods::NONE);
        let ok = match binding.action {
            crate::Action::EnterScroll => outcome == KeyOutcome::ModeChanged(InputMode::Scroll),
            action => outcome == KeyOutcome::Action(action),
        };
        assert!(
            ok,
            "도움말 키 '{}' 가 명령 모드에서 동작하지 않는다: {outcome:?}",
            binding.key
        );
    }
}

// ---- 스크롤 모드(P7 슬라이스 2) --------------------------------------------

fn scroll(key: Key) -> KeyOutcome {
    interpret(InputMode::Scroll, key, Mods::NONE)
}

#[test]
fn scroll_mode_is_entered_from_command_mode_not_from_normal() {
    // 평소 모드의 `[` 는 **셸로 가야 한다**(경로·배열 문법에 쓰는 글자다).
    assert_eq!(
        interpret(InputMode::Normal, Key::Char('['), Mods::NONE),
        KeyOutcome::ToPane(b"[".to_vec())
    );
    assert_eq!(
        interpret(InputMode::Command, Key::Char('['), Mods::NONE),
        KeyOutcome::ModeChanged(InputMode::Scroll)
    );
}

#[test]
fn page_keys_reach_the_pane_in_normal_mode() {
    // 스크롤을 별도 모드에 둔 이유가 이것이다 — less·vim 은 PgUp 을 자기가 쓴다.
    // 평소 모드에서 이 키를 뷰어 스크롤로 가로채면 그 프로그램들이 못 쓰게 된다.
    assert_eq!(to_pane(InputMode::Normal, Key::PageUp, Mods::NONE), b"\x1b[5~");
    assert_eq!(to_pane(InputMode::Normal, Key::PageDown, Mods::NONE), b"\x1b[6~");
}

#[test]
fn scroll_keys_move_the_viewport_with_the_servers_sign_convention() {
    // 과거 방향이 + 다(서버 `Pane.scroll_by`). 부호가 뒤집히면 휠·키가 반대로 움직인다.
    assert_eq!(
        scroll(Key::Up),
        KeyOutcome::Scroll {
            amount: ScrollAmount::Lines(1),
            leave: false
        }
    );
    assert_eq!(
        scroll(Key::Down),
        KeyOutcome::Scroll {
            amount: ScrollAmount::Lines(-1),
            leave: false
        }
    );
    assert_eq!(
        scroll(Key::PageUp),
        KeyOutcome::Scroll {
            amount: ScrollAmount::HalfPageUp,
            leave: false
        }
    );
    assert_eq!(
        scroll(Key::Char('g')),
        KeyOutcome::Scroll {
            amount: ScrollAmount::Top,
            leave: false
        }
    );
}

#[test]
fn leaving_scroll_mode_also_returns_to_the_live_bottom() {
    // 둘을 따로 두면 "모드는 나갔는데 화면은 과거에 멈춰 있는" 상태가 생긴다.
    for key in [Key::Char('q'), Key::Escape, Key::Enter] {
        assert_eq!(
            scroll(key),
            KeyOutcome::Scroll {
                amount: ScrollAmount::Bottom,
                leave: true
            },
            "{key:?}"
        );
    }
    // 반대로 G/end 는 맨 아래로 가되 **모드는 유지**한다(계속 훑을 수 있어야 한다).
    assert_eq!(
        scroll(Key::Char('G')),
        KeyOutcome::Scroll {
            amount: ScrollAmount::Bottom,
            leave: false
        }
    );
}

#[test]
fn typing_in_scroll_mode_does_not_leak_to_the_pane() {
    // 스크롤 중에 친 글자가 셸로 새면 스크롤백을 읽다가 명령을 실행시킨다.
    for key in [Key::Char('a'), Key::Char('1'), Key::Tab] {
        assert_eq!(scroll(key), KeyOutcome::Ignored, "{key:?}");
    }
}

#[test]
fn scroll_mode_search_keys_ask_and_repeat() {
    // ★ G9t — 파이썬 `_handle_scroll_key` 의 검색 셋: `/` 물음 · `n` 위로 반복 ·
    //   `N` 아래로 반복. 검색은 서버가 한다 — 여기는 액션만 낸다.
    assert_eq!(
        scroll(Key::Char('/')),
        KeyOutcome::Action(Action::SearchScrollback)
    );
    assert_eq!(
        scroll(Key::Char('n')),
        KeyOutcome::Action(Action::SearchAgain { down: false })
    );
    assert_eq!(
        scroll(Key::Char('N')),
        KeyOutcome::Action(Action::SearchAgain { down: true })
    );
    // emacs 의 `Ctrl+N`(한 줄 스크롤)과는 수정키가 달라 안 부딪힌다.
    assert!(matches!(
        interpret_full(
            InputMode::Scroll,
            Key::Char('n'),
            Mods::CTRL,
            (Key::Char('b'), Mods::CTRL),
            "emacs",
        ),
        KeyOutcome::Scroll { .. }
    ));
}

#[test]
fn search_keys_keep_the_scroll_mode() {
    // 맞은 줄 주변을 계속 훑는 것이 검색의 쓰임이다 — 액션인데도 평소 모드로 안
    // 돌아간다(JumpPrompt 와 같은 부류. 평소 모드면 방향키가 패널로 새 버린다).
    let mut m = ModeState::default();
    m.press(Key::Escape, Mods::NONE);
    m.press(Key::Char('['), Mods::NONE);
    for key in [Key::Char('/'), Key::Char('n'), Key::Char('N')] {
        assert!(matches!(m.press(key, Mods::NONE), KeyOutcome::Action(_)), "{key:?}");
        assert_eq!(m.mode(), InputMode::Scroll, "{key:?} 가 모드를 풀었다");
    }
}

#[test]
fn every_scroll_binding_is_reachable() {
    // 표에만 있고 라우팅이 없는 키를 막는다(명령 모드 쪽과 같은 검사).
    for binding in SCROLL_BINDINGS {
        let key = match binding.key {
            "enter" => Key::Enter,
            "escape" => Key::Escape,
            "up" => Key::Up,
            "down" => Key::Down,
            "pageup" => Key::PageUp,
            "pagedown" => Key::PageDown,
            "end" => Key::End,
            // `shift-X` 는 대문자 글자 하나다(표의 문법 — `binding_name` 참조).
            s if s.starts_with("shift-") && s.chars().count() == 7 => {
                Key::Char(s.chars().next_back().unwrap())
            }
            s if s.chars().count() == 1 => Key::Char(s.chars().next().unwrap()),
            other => panic!("스크롤 키 '{other}' 를 이 테스트가 모른다 — 매핑을 추가할 것"),
        };
        assert_eq!(
            scroll(key),
            KeyOutcome::Scroll {
                amount: binding.amount,
                leave: binding.leave
            },
            "스크롤 키 '{}' 가 동작하지 않는다",
            binding.key
        );
    }
}

// ── 모드 상태기계(`ModeState`) ───────────────────────────────────────────────
//
// `interpret` 은 전이를 **실행하지 않는다** — 그건 이 상태기계의 일이고, 뷰마다 적으면
// 조용히 갈린다. 한쪽에서만 모드가 안 풀리면 그 클라는 "가끔 키가 안 먹는" 것처럼
// 느껴지고, 화면의 단서는 배지 하나뿐이다.

#[test]
fn escape_enters_command_mode_and_a_second_one_only_leaves_it() {
    let mut m = ModeState::default();
    assert_eq!(m.mode(), InputMode::Normal);

    m.press(Key::Escape, Mods::NONE);
    assert_eq!(m.mode(), InputMode::Command, "첫 ESC 는 모드 진입이다");

    // 두 번째 ESC 는 **모드만 푼다**(정본과 같다 — 위
    // `a_second_escape_only_leaves_the_mode_and_sends_nothing` 이 근거를 든다).
    assert_eq!(
        m.press(Key::Escape, Mods::NONE),
        KeyOutcome::ModeChanged(InputMode::Normal)
    );
    assert_eq!(m.mode(), InputMode::Normal, "풀고 나면 평소 모드다");
}

#[test]
fn one_command_releases_the_mode() {
    // 모드는 **한 동작만 붙잡는다**(파이썬 클라 관습). 안 풀면 다음 타이핑이 통째로
    // 명령으로 먹혀 "키가 안 먹는다"가 된다.
    let mut m = ModeState::default();
    m.press(Key::Escape, Mods::NONE);
    // `n`(새 탭)을 쓰는 이유: 이 시험이 재는 것은 **액션을 낸 뒤 모드가 풀리나**이므로
    // esc 표에 실제로 있는 키라야 한다(`j` 는 pytmux-466 으로 데모 판 표로 갔다).
    assert!(matches!(m.press(Key::Char('n'), Mods::NONE), KeyOutcome::Action(_)));
    assert_eq!(m.mode(), InputMode::Normal);
}

#[test]
fn an_unknown_key_in_command_mode_leaves_the_mode() {
    // ★ **뒤집힌 단언이다**(pytmux-33 ⓖ3 · 2026-09-02). 종전 이유는 *"뜻 없는 키로
    //   모드가 풀리면 사용자는 자기가 무엇을 눌렀는지 모른 채 모드를 잃는다"* 였는데,
    //   정본은 반대를 고른다(`_handle_esc_mode` 의 마지막 `else: self._exit_esc()` —
    //   주석이 *"enter/i/그 외 → 명령 모드 종료"* 라고 적는다).
    //
    //   ⛔ 그리고 우리 쪽 `prefix` 분기가 **같은 자리에서 반대 이유를 적고 있었다**:
    //   *"안 풀면 잘못 누른 뒤의 타이핑이 통째로 표에 부딪혀 사라지고, 사용자에게는
    //   '키가 안 먹는다'로만 보인다."* 두 모드가 같은 물음에 다른 답을 들고 있던 것이고,
    //   정본이 권위다([[pytmux-185]]).
    let mut m = ModeState::default();
    m.press(Key::Escape, Mods::NONE);
    assert_eq!(m.press(Key::Function(9), Mods::NONE), KeyOutcome::Ignored);
    assert_eq!(m.mode(), InputMode::Normal);
}

#[test]
fn scroll_mode_is_left_only_by_the_keys_that_return_to_live() {
    // ★ 라이브 복귀와 모드 탈출은 **한 동작**이라야 한다. 따로 두면 "모드는 나갔는데
    // 화면은 과거에 멈춘" 상태가 생기고, 그때 사용자는 화면이 얼었다고 읽는다.
    let mut m = ModeState::default();
    m.press(Key::Escape, Mods::NONE);
    m.press(Key::Char('['), Mods::NONE);
    assert_eq!(m.mode(), InputMode::Scroll);

    // 위로 한 칸 — 아직 스크롤 모드다.
    assert!(matches!(
        m.press(Key::Up, Mods::NONE),
        KeyOutcome::Scroll { leave: false, .. }
    ));
    assert_eq!(m.mode(), InputMode::Scroll);

    assert!(matches!(
        m.press(Key::Char('q'), Mods::NONE),
        KeyOutcome::Scroll { leave: true, .. }
    ));
    assert_eq!(m.mode(), InputMode::Normal);
}

#[test]
fn typing_never_gets_stuck_in_a_mode() {
    // 평소 모드에서는 아무리 쳐도 모드가 안 바뀐다 — 여기가 흔들리면 타이핑 중간에
    // 글자가 사라진다.
    let mut m = ModeState::default();
    for c in "hello 안녕".chars() {
        assert!(matches!(m.press(Key::Char(c), Mods::NONE), KeyOutcome::ToPane(_)));
        assert_eq!(m.mode(), InputMode::Normal);
    }
}

// ── prefix 모드 (패리티 G1) ──────────────────────────────────────────────────
//
// tmux·파이썬 클라와 같은 모델: prefix 키 하나 뒤의 **키 하나**가 pytmux 의 것이다.

#[test]
fn the_prefix_key_opens_prefix_mode() {
    let mut mode = ModeState::default();
    assert_eq!(
        mode.press(Key::Char('b'), Mods::CTRL),
        KeyOutcome::ModeChanged(InputMode::Prefix)
    );
    assert_eq!(mode.mode(), InputMode::Prefix);
    assert_eq!(mode.mode().badge(), Some("[prefix]"));
}

#[test]
fn a_prefix_key_pressed_twice_reaches_the_pane() {
    // ★ 이게 없으면 패널 안 프로그램이 Ctrl+B 를 **영영 못 받는다**(emacs·less 의 뒤로
    // 이동이 그 키다). tmux 도 같은 탈출구를 둔다.
    let mut mode = ModeState::default();
    mode.press(Key::Char('b'), Mods::CTRL);
    assert_eq!(
        mode.press(Key::Char('b'), Mods::CTRL),
        KeyOutcome::ToPane(vec![0x02])
    );
    assert_eq!(mode.mode(), InputMode::Normal, "보낸 뒤에는 평소 모드다");
}

#[test]
fn prefix_letters_match_the_python_client() {
    // 손버릇이 패리티의 기준이다. 표는 core 가 갖고, 여기서는 **모드를 거친 결과**를 본다.
    for (key, action) in [
        ('%', crate::Action::SplitLeftRight),
        ('"', crate::Action::SplitTopBottom),
        ('x', crate::Action::KillPane),
        ('c', crate::Action::NewTab),
        ('&', crate::Action::KillTab),
        ('n', crate::Action::NextTab),
        ('p', crate::Action::PrevTab),
        ('l', crate::Action::LastTab),
        ('r', crate::Action::Redraw),
    ] {
        let mut mode = ModeState::default();
        mode.press(Key::Char('b'), Mods::CTRL);
        assert_eq!(
            mode.press(Key::Char(key), Mods::NONE),
            KeyOutcome::Action(action),
            "prefix {key}"
        );
        assert_eq!(mode.mode(), InputMode::Normal, "한 동작 뒤에는 모드가 풀린다");
    }
}

#[test]
fn a_number_after_the_prefix_picks_that_tab() {
    // 0~9 는 표가 아니라 규칙이다(열 줄을 적지 않는다).
    let mut mode = ModeState::default();
    mode.press(Key::Char('b'), Mods::CTRL);
    assert_eq!(
        mode.press(Key::Char('3'), Mods::NONE),
        KeyOutcome::Action(crate::Action::SelectTab(3))
    );
}

#[test]
fn an_unknown_key_after_the_prefix_cancels_it() {
    // 안 풀면 잘못 누른 prefix 뒤의 타이핑이 통째로 사라지고, 증상은 "키가 안 먹는다"다.
    let mut mode = ModeState::default();
    mode.press(Key::Char('b'), Mods::CTRL);
    assert_eq!(mode.press(Key::Function(9), Mods::NONE), KeyOutcome::Ignored);
    assert_eq!(mode.mode(), InputMode::Normal);
    // 모드가 풀렸으니 다음 글자는 패널로 간다.
    assert_eq!(
        mode.press(Key::Char('a'), Mods::NONE),
        KeyOutcome::ToPane(b"a".to_vec())
    );
}

#[test]
fn the_prefix_does_not_swallow_plain_b() {
    // Ctrl 없는 `b` 는 그냥 글자다. 이걸 삼키면 타이핑이 망가진다.
    let mut mode = ModeState::default();
    assert_eq!(
        mode.press(Key::Char('b'), Mods::NONE),
        KeyOutcome::ToPane(b"b".to_vec())
    );
}

#[test]
fn the_scroll_mode_is_reachable_from_the_prefix_too() {
    // tmux 의 `prefix [`. esc 모드에도 같은 키가 있다(둘 다 파이썬 클라와 같다).
    let mut mode = ModeState::default();
    mode.press(Key::Char('b'), Mods::CTRL);
    assert_eq!(
        mode.press(Key::Char('['), Mods::NONE),
        KeyOutcome::ModeChanged(InputMode::Scroll)
    );
    assert_eq!(mode.mode(), InputMode::Scroll);
}

// ── prefix 모드 2차: 서버에 이미 있던 조작들(패리티 G1b) ─────────────────────

#[test]
fn the_second_batch_of_prefix_letters_also_matches_python() {
    use crate::{Action, Dir};
    for (key, action) in [
        ('z', Action::Zoom),
        ('o', Action::NextPane),
        (';', Action::LastPane),
        (' ', Action::CycleLayout),
        ('{', Action::SwapPane { forward: false }),
        ('}', Action::SwapPane { forward: true }),
        ('!', Action::BreakPane),
        ('d', Action::Quit),
        (']', Action::PasteBuffer),
    ] {
        let mut mode = ModeState::default();
        mode.press(Key::Char('b'), Mods::CTRL);
        assert_eq!(
            mode.press(Key::Char(key), Mods::NONE),
            KeyOutcome::Action(action),
            "prefix {key:?}"
        );
    }
    // 대문자는 shift 로 온다 — 파이썬의 `H J K L`(크기)·`P`(핀).
    for (key, action) in [
        ('H', Action::ResizePane(Dir::Left)),
        ('J', Action::ResizePane(Dir::Down)),
        ('K', Action::ResizePane(Dir::Up)),
        ('L', Action::ResizePane(Dir::Right)),
        ('P', Action::TogglePin),
    ] {
        let mut mode = ModeState::default();
        mode.press(Key::Char('b'), Mods::CTRL);
        assert_eq!(
            mode.press(Key::Char(key), Mods::NONE),
            KeyOutcome::Action(action),
            "prefix {key}"
        );
    }
}

#[test]
fn arrows_after_the_prefix_move_between_panes() {
    use crate::{Action, Dir};
    for (key, dir) in [
        (Key::Left, Dir::Left),
        (Key::Right, Dir::Right),
        (Key::Up, Dir::Up),
        (Key::Down, Dir::Down),
    ] {
        let mut mode = ModeState::default();
        mode.press(Key::Char('b'), Mods::CTRL);
        assert_eq!(
            mode.press(key, Mods::NONE),
            KeyOutcome::Action(Action::SelectPane(dir)),
            "{key:?}"
        );
    }
}

#[test]
fn a_modifier_after_the_prefix_is_part_of_the_binding() {
    // ★ `Ctrl+o`(패널 회전)가 그 경우다. 수정키를 무시하고 이름만 보면 `o`(다음 패널)로
    // 새고, 그러면 **회전이 영영 안 일어난다**.
    use crate::Action;
    let mut mode = ModeState::default();
    mode.press(Key::Char('b'), Mods::CTRL);
    assert_eq!(
        mode.press(Key::Char('o'), Mods::CTRL),
        KeyOutcome::Action(Action::RotatePanes)
    );
    // 표에 없는 수정키 조합은 조용히 버린다(모드는 풀린다).
    let mut mode = ModeState::default();
    mode.press(Key::Char('b'), Mods::CTRL);
    assert_eq!(mode.press(Key::Char('z'), Mods::ALT), KeyOutcome::Ignored);
    assert_eq!(mode.mode(), InputMode::Normal);
}

#[test]
fn a_number_is_still_a_tab_even_though_ctrl_names_exist() {
    // `ctrl-` 접두를 붙이는 길이 생긴 뒤에도 맨 숫자는 탭이어야 한다.
    use crate::Action;
    let mut mode = ModeState::default();
    mode.press(Key::Char('b'), Mods::CTRL);
    assert_eq!(
        mode.press(Key::Char('7'), Mods::NONE),
        KeyOutcome::Action(Action::SelectTab(7))
    );
}

// ── 설정된 prefix (패리티 G5) ────────────────────────────────────────────────

#[test]
fn a_configured_prefix_replaces_ctrl_b() {
    // `set prefix C-a` 를 쓴 사람에게는 Ctrl+A 가 prefix 이고 **Ctrl+B 는 그냥 바이트**다.
    let mut mode = ModeState::with_prefix((Key::Char('a'), Mods::CTRL));
    assert_eq!(
        mode.press(Key::Char('a'), Mods::CTRL),
        KeyOutcome::ModeChanged(InputMode::Prefix)
    );
    let mut mode = ModeState::with_prefix((Key::Char('a'), Mods::CTRL));
    assert_eq!(
        mode.press(Key::Char('b'), Mods::CTRL),
        KeyOutcome::ToPane(vec![0x02]),
        "옛 prefix 가 아직 삼켜진다"
    );
}

#[test]
fn the_configured_prefix_pressed_twice_still_reaches_the_pane() {
    // 탈출구는 prefix 를 바꿔도 남아야 한다.
    let mut mode = ModeState::with_prefix((Key::Char('a'), Mods::CTRL));
    mode.press(Key::Char('a'), Mods::CTRL);
    assert_eq!(
        mode.press(Key::Char('a'), Mods::CTRL),
        KeyOutcome::ToPane(vec![0x01])
    );
}

#[test]
fn the_default_is_still_ctrl_b() {
    let mut mode = ModeState::default();
    assert_eq!(mode.prefix(), (Key::Char('b'), Mods::CTRL));
    assert_eq!(
        mode.press(Key::Char('b'), Mods::CTRL),
        KeyOutcome::ModeChanged(InputMode::Prefix)
    );
}

#[test]
fn the_help_screen_lists_every_esc_binding() {
    // ★ 전에는 도움말 화면이 `show_in_help` 로 거른 뒤 빠진 것을 손으로 덧붙였다.
    // 그 목록은 조용히 낡는다 — G1c 에서 글자 아홉 개를 더했을 때 하나도 안 나왔다.
    let lines = crate::keymap::key_help_lines(&[]);
    for binding in crate::BINDINGS {
        if binding.key == "escape" {
            continue; // 모드 전용 규칙(패널로 ESC) — 표의 라벨은 화면에 거짓말이다
        }
        assert!(
            lines.iter().any(|(key, _)| key == binding.key),
            "도움말 화면에 '{}' 가 없다",
            binding.key
        );
    }
}

// ── 탭 옮기기(패리티 G8c) ─────────────────────────────────────────────────────

#[test]
fn moving_a_tab_stops_at_the_ends() {
    // ★ 이미 끝인데 명령을 보내면 서버가 같은 자리로 옮기며 화면만 한 번 출렁인다.
    use crate::TabMove;
    assert_eq!(TabMove::Left.target(0, 3), None, "맨 앞에서 더 왼쪽");
    assert_eq!(TabMove::Right.target(2, 3), None, "맨 뒤에서 더 오른쪽");
    assert_eq!(TabMove::First.target(0, 3), None, "이미 맨 앞");
    assert_eq!(TabMove::Last.target(2, 3), None, "이미 맨 뒤");
}

#[test]
fn moving_a_tab_lands_where_it_should() {
    use crate::TabMove;
    assert_eq!(TabMove::Left.target(2, 4), Some(1));
    assert_eq!(TabMove::Right.target(1, 4), Some(2));
    assert_eq!(TabMove::First.target(3, 4), Some(0));
    assert_eq!(TabMove::Last.target(1, 4), Some(3));
}

#[test]
fn an_empty_tab_bar_moves_nothing() {
    // 붙기 전에 눌린 키다.
    use crate::TabMove;
    for to in [TabMove::Left, TabMove::Right, TabMove::First, TabMove::Last] {
        assert_eq!(to.target(0, 0), None, "{to:?}");
    }
}

// ── send-keys 해석(패리티 G8i) ────────────────────────────────────────────────

#[test]
fn send_keys_mixes_text_and_key_names() {
    // 파이썬과 같은 표기 — 공백으로 나뉜 토큰이 키 이름이면 그 키, 아니면 글자 그대로.
    assert_eq!(super::parse_send_keys("hello Enter"), b"hello\r".to_vec());
}

#[test]
fn send_keys_understands_tmux_modifiers() {
    assert_eq!(super::parse_send_keys("C-c"), vec![0x03]);
    assert_eq!(super::parse_send_keys("C-d"), vec![0x04]);
}

#[test]
fn a_single_letter_is_that_letter_not_a_key_name() {
    // ★ `a` 를 키 이름으로 읽으면 글자를 영영 못 보낸다.
    assert_eq!(super::parse_send_keys("a"), b"a".to_vec());
}

#[test]
fn an_unknown_name_goes_through_as_text() {
    // 모르는 이름을 버리면 사용자는 그 글자가 어디로 갔는지 모른다.
    assert_eq!(super::parse_send_keys("nosuchkey"), b"nosuchkey".to_vec());
}

#[test]
fn send_keys_of_nothing_is_nothing() {
    assert!(super::parse_send_keys("   ").is_empty());
}

// ── mode-keys(패리티 G8l) ─────────────────────────────────────────────────────

#[test]
fn vi_scroll_keys_only_exist_in_vi_mode() {
    // ★ 무조건 걸면 vi 모드를 안 쓰는 사용자에게 **없던 키가 생긴다**(`j` 가 아래로
    // 흐른다) — 설정을 못 읽던 동안 이 키들을 안 걸어 둔 이유가 이것이다.
    use super::{ScrollAmount, scroll_action_in};
    assert_eq!(
        scroll_action_in("vi", Key::Char('j'), Mods::NONE),
        Some((ScrollAmount::Lines(-1), false))
    );
    assert_eq!(scroll_action_in("emacs", Key::Char('j'), Mods::NONE), None);
    assert_eq!(scroll_action_in("", Key::Char('j'), Mods::NONE), None);
}

#[test]
fn emacs_scroll_keys_are_the_control_pairs() {
    use super::{ScrollAmount, scroll_action_in};
    assert_eq!(
        scroll_action_in("emacs", Key::Char('p'), Mods::CTRL),
        Some((ScrollAmount::Lines(1), false))
    );
    assert_eq!(
        scroll_action_in("emacs", Key::Char('v'), Mods::ALT),
        Some((ScrollAmount::HalfPageUp, false))
    );
    assert_eq!(scroll_action_in("vi", Key::Char('p'), Mods::CTRL), None);
}

#[test]
fn the_shared_keys_work_in_every_mode() {
    // 화살표·PageUp·q 는 모드와 무관하다 — 그건 표의 것이다.
    use super::{ScrollAmount, scroll_action_in};
    for mode in ["", "vi", "emacs"] {
        assert_eq!(
            scroll_action_in(mode, Key::Up, Mods::NONE),
            Some((ScrollAmount::Lines(1), false)),
            "{mode}"
        );
        assert_eq!(
            scroll_action_in(mode, Key::Char('q'), Mods::NONE),
            Some((ScrollAmount::Bottom, true)),
            "{mode}"
        );
    }
}

// ── 프롬프트 점프(패리티 `e_jump`) ──────────────────────────────────────────
//
// 파이썬 clientio 의 두 자리를 그대로 옮긴 것이다: esc 모드의 `ctrl+up`/`ctrl+down` 은
// **모드를 스크롤로 바꾸고** 서버에 점프를 시키며, 스크롤 모드 안에서도 같은 키가 계속
// 먹는다. 두 자리 중 하나만 있으면 "한 번은 뛰는데 그다음부터 안 뛴다"가 된다.

#[test]
fn esc_mode_ctrl_arrows_jump_to_the_neighbouring_prompt() {
    assert_eq!(
        interpret(InputMode::Command, Key::Up, Mods::CTRL),
        KeyOutcome::Action(Action::JumpPrompt { up: true })
    );
    assert_eq!(
        interpret(InputMode::Command, Key::Down, Mods::CTRL),
        KeyOutcome::Action(Action::JumpPrompt { up: false })
    );
}

#[test]
fn jumping_leaves_you_in_scroll_mode_not_normal() {
    // ★ 이 규칙이 없으면 뛴 뒤 방향키가 **패널로 간다** — 뛴 자리 주변을 읽을 길이
    // 없어져 이 키가 반쪽이 된다(파이썬은 `_exit_esc()` 뒤 `mode = "scroll"`).
    let mut state = ModeState::default();
    assert_eq!(
        state.press(Key::Escape, Mods::NONE),
        KeyOutcome::ModeChanged(InputMode::Command)
    );
    assert_eq!(
        state.press(Key::Up, Mods::CTRL),
        KeyOutcome::Action(Action::JumpPrompt { up: true })
    );
    assert_eq!(state.mode(), InputMode::Scroll, "esc 모드가 그냥 풀렸다");
}

#[test]
fn the_jump_keys_keep_working_inside_scroll_mode() {
    // 턴 경계를 연달아 오가는 것이 이 키의 쓰임이다. 스크롤 모드에서 안 먹으면 매번
    // 나갔다 `esc` 를 다시 눌러야 한다.
    let mut state = ModeState::default();
    state.press(Key::Escape, Mods::NONE);
    state.press(Key::Up, Mods::CTRL);
    assert_eq!(
        state.press(Key::Down, Mods::CTRL),
        KeyOutcome::Action(Action::JumpPrompt { up: false })
    );
    assert_eq!(state.mode(), InputMode::Scroll, "스크롤 모드를 벗어났다");
}

#[test]
fn the_jump_keys_do_not_steal_the_plain_arrows_in_scroll_mode() {
    // 수정키 없는 화살표는 여전히 **스크롤**이다. 여기가 갈리면 스크롤 모드에서
    // 위아래로 훑는 것이 통째로 사라진다.
    assert_eq!(
        interpret(InputMode::Scroll, Key::Up, Mods::NONE),
        KeyOutcome::Scroll {
            amount: ScrollAmount::Lines(1),
            leave: false
        }
    );
}

#[test]
fn normal_mode_ctrl_arrows_still_go_to_the_pane() {
    // ★ 점프는 **모드 안의 키**다. 평소 모드에서 가로채면 패널 안 프로그램(vim 의
    // 단어 이동 등)이 Ctrl+화살표를 영영 못 받는다 — 파이썬도 esc 모드에만 둔다.
    assert_eq!(
        interpret(InputMode::Normal, Key::Up, Mods::CTRL),
        KeyOutcome::ToPane(b"\x1b[A".to_vec())
    );
}

#[test]
fn opening_the_command_table_to_modifiers_did_not_bind_anything_else() {
    // `command_action` 이 수정키 조합을 통째로 버리던 것을 열었다. 표에 없는 조합은
    // 그대로 아무 일도 없어야 한다 — 안 그러면 esc 모드에서 Ctrl 조합이 엉뚱한
    // 액션으로 새기 시작한다.
    for key in [Key::Char('c'), Key::Char('a'), Key::Left, Key::Right, Key::Enter] {
        assert_eq!(command_action(key, Mods::CTRL), None, "{key:?}");
        assert_eq!(command_action(key, Mods::ALT), None, "{key:?}");
    }
}

// ── 블록 선택 모드(pytmux-18) ────────────────────────────────────────────────
//
// 제보의 요구 셋을 그대로 잰다: ⑴ 고른 상태가 있고 ⑵ `↑`/`↓` 로 한 블록씩 옮기고
// ⑶ `Ctrl`+`C` 로 그 블록을 복사한다. 여기서 재는 것은 **키의 뜻**이고, 무엇이 골라져
// 있나·무엇이 복사되나는 뷰가 잰다(`gui/src/session_view_tests.rs`).
//
// ★ 이 절에서 가장 중요한 것은 **부정 오라클**이다. 셋 다 평소 모드에서는 패널 안
//   프로그램의 키이고(인터럽트·커서 이동), 가로채는 범위가 한 칸이라도 넓어지면 그
//   프로그램의 기능이 **조용히** 사라진다.

#[test]
fn arrows_step_one_block_at_a_time_inside_the_mode() {
    let mut mode = ModeState::default();
    mode.enter_block();
    assert_eq!(mode.mode().badge(), Some("[block]"), "모드가 화면에 안 보인다");
    assert_eq!(
        mode.press(Key::Down, Mods::NONE),
        KeyOutcome::Block(BlockKey::Next)
    );
    assert_eq!(
        mode.press(Key::Up, Mods::NONE),
        KeyOutcome::Block(BlockKey::Prev)
    );
    assert_eq!(mode.mode(), InputMode::Block, "한 번 옮기고 모드에서 나갔다");
}

#[test]
fn ctrl_c_copies_the_block_inside_the_mode() {
    let mut mode = ModeState::default();
    mode.enter_block();
    assert_eq!(
        mode.press(Key::Char('c'), Mods::CTRL),
        KeyOutcome::Block(BlockKey::Copy)
    );
    // 복사하고도 머문다 — 옆 블록을 이어 고르는 것이 이 모드의 쓰임이다.
    assert_eq!(mode.mode(), InputMode::Block);
}

#[test]
fn ctrl_c_outside_the_mode_is_still_an_interrupt() {
    // ⛔ 이 줄이 무너지면 **패널 안 프로그램을 끊을 길이 사라진다.** 제보가 그 위험을
    //   먼저 적었고("Ctrl+C 는 이미 뜻이 있다"), 여기가 그 자리다.
    for mode in [InputMode::Normal, InputMode::Scroll, InputMode::Command] {
        let outcome = interpret(mode, Key::Char('c'), Mods::CTRL);
        assert_ne!(
            outcome,
            KeyOutcome::Block(BlockKey::Copy),
            "{mode:?} 에서 Ctrl+C 를 가로챘다"
        );
    }
    assert_eq!(
        interpret(InputMode::Normal, Key::Char('c'), Mods::CTRL),
        KeyOutcome::ToPane(vec![0x03]),
        "평소 모드의 Ctrl+C 가 인터럽트가 아니다"
    );
}

#[test]
fn plain_arrows_outside_the_mode_are_not_block_moves() {
    // 같은 이유의 짝 — `↑`/`↓` 는 평소 모드에서 커서 이동·히스토리다.
    assert_eq!(
        interpret(InputMode::Normal, Key::Up, Mods::NONE),
        KeyOutcome::ToPane(b"\x1b[A".to_vec())
    );
    assert_eq!(
        interpret(InputMode::Normal, Key::Down, Mods::NONE),
        KeyOutcome::ToPane(b"\x1b[B".to_vec())
    );
}

#[test]
fn three_keys_leave_the_block_mode() {
    // 스크롤 모드의 나가는 키와 같은 셋이다 — 두 모드가 다른 글자로 나가면 손이 어긋난다.
    for key in [Key::Escape, Key::Enter, Key::Char('q')] {
        let mut mode = ModeState::default();
        mode.enter_block();
        assert_eq!(
            mode.press(key, Mods::NONE),
            KeyOutcome::ModeChanged(InputMode::Normal),
            "{key:?}"
        );
        assert_eq!(mode.mode(), InputMode::Normal, "{key:?}");
    }
}

#[test]
fn typing_in_block_mode_does_not_leak_to_the_pane() {
    // 고르는 동안 친 글자가 셸에 찍히면 안 된다(esc·스크롤 모드와 같은 규율).
    let mut mode = ModeState::default();
    mode.enter_block();
    for key in [Key::Char('a'), Key::Char('Z'), Key::Left, Key::Tab] {
        assert_eq!(mode.press(key, Mods::NONE), KeyOutcome::Ignored, "{key:?}");
        assert_eq!(mode.mode(), InputMode::Block, "{key:?} 에 모드가 풀렸다");
    }
}

#[test]
fn the_esc_table_can_reach_the_block_mode() {
    // 팔레트만이 입구면 키를 아는 사람이 손을 두 번 더 움직인다. 정본에 없는 글자라
    // 안전하다(`client_surface.json` 의 `esc_keys` 에 `b` 가 없다).
    assert_eq!(
        command_action(Key::Char('b'), Mods::NONE),
        Some(crate::Action::SelectBlocks)
    );
}
