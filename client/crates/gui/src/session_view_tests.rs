//! GUI 세션 뷰에서 **판정이 있는 부분**만 시험한다.
//!
//! 이 크레이트에는 TUI 의 `TestScreen` 에 해당하는 것이 없다 — `Scene` 이 들고 있는
//! 것은 `glyph_key`(폰트별 id)라 글자로 되돌릴 수 없고, 테스트 델리게이트에서는 그마저
//! 가짜다. 그래서 "화면에 무엇이 보이나"는 사람이 본다(설계문서 §7 GUI 따라붙이기).
//!
//! 대신 **색 판정은 순수 함수로 빼 두었으므로** 여기서 전부 잡는다(사용자 결정
//! 2026-07-28: "로직은 밀고 뷰는 얇게"). 이 자리들은 눈으로 보기 가장 어려운 종류다 —
//! 팔레트 한 줄이 복붙으로 어긋나면 그 색만 조용히 틀리고, 반전이 안 풀리면 커서가
//! 사라진다.

use super::*;

fn style(fg: Option<CellColor>, bg: Option<CellColor>, reverse: bool) -> CellStyle {
    CellStyle {
        fg,
        bg,
        reverse,
        ..CellStyle::default()
    }
}

#[test]
fn reverse_swaps_foreground_and_background() {
    // 터미널에서 반전은 장식이 아니라 **신호**다(커서·선택). 안 풀면 그 신호가 사라진다.
    let red = CellColor::Named(NamedColor::Red);
    let blue = CellColor::Named(NamedColor::Blue);
    let (fg, bg) = colors(&style(Some(red), Some(blue), true));
    assert_eq!(fg, named(NamedColor::Blue));
    assert_eq!(bg, Some(named(NamedColor::Red)));
}

#[test]
fn reverse_without_a_background_still_paints_something() {
    // 배경이 없는 셀을 반전하면 전경이 배경이 되는데, 새 전경이 없으면 **글자가
    // 배경색으로 배경 위에** 그려져 사라진다. 캔버스 배경을 전경으로 쓴다.
    let (fg, bg) = colors(&style(Some(CellColor::Named(NamedColor::Green)), None, true));
    assert_eq!(fg, palette::BG, "반전한 글자가 안 보인다");
    assert_eq!(bg, Some(named(NamedColor::Green)));
}

#[test]
fn a_plain_cell_keeps_its_colors() {
    let (fg, bg) = colors(&style(Some(CellColor::Named(NamedColor::Cyan)), None, false));
    assert_eq!(fg, named(NamedColor::Cyan));
    assert_eq!(bg, None);
}

#[test]
fn a_cell_without_a_foreground_uses_the_default_not_black() {
    // 서버는 기본 전경을 안 싣는다(`fg: None`). 0 으로 떨어뜨리면 배경 위에 검은 글자가
    // 되어 화면 절반이 안 보인다.
    let (fg, _) = colors(&style(None, None, false));
    assert_eq!(fg, palette::FG);
}

#[test]
fn rgb_from_the_server_passes_through_untouched() {
    // 24비트 색을 팔레트로 접으면 사용자가 고른 색이 사라진다.
    let (fg, _) = colors(&style(
        Some(CellColor::Rgb {
            r: 1,
            g: 2,
            b: 3,
        }),
        None,
        false,
    ));
    assert_eq!(fg, ColorU { r: 1, g: 2, b: 3, a: 0xff });
}

#[test]
fn every_palette_entry_is_opaque() {
    // 알파가 0 이면 그 색만 **투명하게** 그려진다 — 화면에서는 "그 글자만 안 보인다"로
    // 나타나고, 색이 틀린 것보다 찾기 어렵다.
    for color in ALL_NAMED {
        assert_eq!(named(color).a, 0xff, "{color:?} 가 투명하다");
    }
}

#[test]
fn bright_variants_differ_from_their_base() {
    // 팔레트는 16줄짜리 복붙 표라 한 줄이 어긋나기 쉽다. 밝은 색이 기본색과 같으면
    // 그 구분이 화면에서 통째로 사라지는데, 눈으로는 "좀 흐린가?" 정도로만 보인다.
    for (base, bright) in [
        (NamedColor::Black, NamedColor::BrightBlack),
        (NamedColor::Red, NamedColor::BrightRed),
        (NamedColor::Green, NamedColor::BrightGreen),
        (NamedColor::Yellow, NamedColor::BrightYellow),
        (NamedColor::Blue, NamedColor::BrightBlue),
        (NamedColor::Cyan, NamedColor::BrightCyan),
        (NamedColor::White, NamedColor::BrightWhite),
    ] {
        assert_ne!(named(base), named(bright), "{base:?} 와 {bright:?} 가 같다");
    }
}

// ── 블록 구역(P4) ────────────────────────────────────────────────────────────

/// 부류 전수. `block_color` 의 match 가 컴파일로 누락을 막지만, 아래 테스트가 실제로
/// 전부를 훑으려면 목록이 필요하다.
const ALL_TONES: [Tone; 5] = [
    Tone::Ok,
    Tone::Failed,
    Tone::Unknown,
    Tone::Running,
    Tone::Idle,
];

#[test]
fn an_unknown_exit_code_is_not_painted_like_success() {
    // ★ 이 뷰에서 가장 비싼 오해다. 서버가 종료코드를 못 받은 블록을 초록으로 칠하면
    // 사용자는 "끝났고 잘됐다"로 읽는다. 실패로 칠하는 것도 같은 종류의 거짓말이라
    // **둘 다와 달라야** 한다.
    let unknown = SessionView::block_color(Tone::Unknown);
    assert_ne!(unknown, SessionView::block_color(Tone::Ok));
    assert_ne!(unknown, SessionView::block_color(Tone::Failed));
}

#[test]
fn success_and_failure_never_share_a_color() {
    assert_ne!(
        SessionView::block_color(Tone::Ok),
        SessionView::block_color(Tone::Failed),
        "성공과 실패가 같은 색이면 블록 구역이 아무 말도 안 하는 것과 같다"
    );
}

#[test]
fn a_running_block_looks_different_from_a_finished_one() {
    // 진행 중을 끝난 것과 같이 칠하면 "왜 안 끝나지"를 화면에서 알 수 없다.
    for done in [Tone::Ok, Tone::Failed, Tone::Unknown] {
        assert_ne!(
            SessionView::block_color(Tone::Running),
            SessionView::block_color(done),
            "{done:?} 와 진행 중이 같은 색이다"
        );
    }
}

fn block(command: &str, cwd: Option<&str>) -> Block {
    Block {
        command: command.to_owned(),
        state: proto::blocks::BlockState::Done,
        exit: Some(0),
        cwd: cwd.map(str::to_owned),
        start_row: 0,
        end_row: None,
    }
}

#[test]
fn a_block_line_stays_inside_the_width_budget() {
    // ★ 실측 결함(2026-07-28): GUI 의 Text 는 TUI 와 달리 안 잘려서, 긴 명령 줄이
    // **창 밖으로 흘러나갔다**. 표식(4칸) + 명령 + 공백 + cwd 가 예산 안이라야 한다.
    let long = "x".repeat(300);
    let b = block(&long, Some(&"/very/long/path".repeat(20)));
    let (cmd, cwd) = SessionView::block_parts(&b, 80);
    let used = 4 + footer::width(&cmd) + cwd.as_deref().map_or(0, |c| 1 + footer::width(c));
    assert!(used <= 80, "{used} 칸을 썼다(예산 80)");
}

#[test]
fn a_cramped_line_drops_the_cwd_instead_of_halving_both() {
    // 둘을 똑같이 잘라 반쪽씩 남기면 어느 쪽도 못 읽는 줄이 된다. 명령이 주인공이다.
    let b = block("cargo build --release --workspace", Some("/home/u/proj"));
    let (cmd, cwd) = SessionView::block_parts(&b, 30);
    assert!(cwd.is_none(), "자리가 없으면 cwd 를 뺀다: {cwd:?}");
    assert!(!cmd.is_empty(), "명령까지 사라지면 그 줄은 아무 말도 안 한다");
}

#[test]
fn a_roomy_line_keeps_both() {
    let b = block("ls", Some("/tmp"));
    let (cmd, cwd) = SessionView::block_parts(&b, 80);
    assert_eq!(cmd, "ls");
    assert_eq!(cwd.as_deref(), Some("/tmp"), "자리가 넉넉하면 안 자른다");
}

#[test]
fn an_empty_command_still_says_something() {
    // 빈 문자열을 그리면 그 줄은 표식만 남아 자리만 차지한다.
    let (cmd, _) = SessionView::block_parts(&block("", None), 80);
    assert_eq!(cmd, "(명령 미상)");
}

// ── 입력(P7) ─────────────────────────────────────────────────────────────────
//
// 뷰를 통째로 만들 수는 없다(창·글꼴이 필요하다). 그래서 **판정이 있는 부분**만 순수
// 함수로 빼 두고 여기서 잡는다 — 키 이름 해석이 그것이다. 나머지(모드 전이·바이트
// 인코딩)는 core 의 오라클이 이미 덮고, GUI 는 그 core 를 부르기만 한다.

fn ks(key: &str, ctrl: bool, alt: bool, shift: bool) -> warpui::keymap::Keystroke {
    warpui::keymap::Keystroke {
        ctrl,
        alt,
        shift,
        cmd: false,
        meta: false,
        key: key.to_owned(),
    }
}

#[test]
fn the_gui_reads_key_names_with_the_same_table_as_the_tui() {
    // ★ 이름 표가 갈리면 **한쪽 클라에서만 안 먹는 키**가 생기고, 그 증상은 조용하다
    // (누르면 아무 일도 안 난다). 이름은 core 가 정하고 GUI 는 부르기만 한다.
    use base::keys::Key;
    let cases = [
        ("enter", Key::Enter),
        ("escape", Key::Escape),
        ("pageup", Key::PageUp),
        ("f5", Key::Function(5)),
        ("a", Key::Char('a')),
    ];
    for (name, want) in cases {
        let got = SessionView::key_from_keystroke(&ks(name, false, false, false));
        assert_eq!(got.map(|(k, _)| k), Some(want), "이름 '{name}'");
    }
}

#[test]
fn a_bare_tab_and_a_shifted_tab_are_different_keys() {
    // 둘이 같으면 역방향 탭 이동이 통째로 사라진다.
    use base::keys::Key;
    let plain = SessionView::key_from_keystroke(&ks("tab", false, false, false));
    let shifted = SessionView::key_from_keystroke(&ks("tab", false, false, true));
    assert_eq!(plain.map(|(k, _)| k), Some(Key::Tab));
    assert_eq!(shifted.map(|(k, _)| k), Some(Key::BackTab));
}

#[test]
fn meta_and_cmd_are_treated_as_alt() {
    // 단말은 이 셋을 구분해 주지 않는다 — 셋 다 ESC 접두로 나간다(TUI 와 같은 규칙).
    for (ctrl, alt, want_alt) in [(false, true, true), (false, false, false)] {
        let mods = SessionView::key_from_keystroke(&ks("a", ctrl, alt, false)).unwrap().1;
        assert_eq!(mods.alt, want_alt);
    }
    let mut k = ks("a", false, false, false);
    k.cmd = true;
    assert!(SessionView::key_from_keystroke(&k).unwrap().1.alt, "cmd 가 alt 로 안 갔다");
    let mut k = ks("a", false, false, false);
    k.meta = true;
    assert!(SessionView::key_from_keystroke(&k).unwrap().1.alt, "meta 가 alt 로 안 갔다");
}

#[test]
fn ctrl_survives_the_conversion() {
    // Ctrl 이 떨어지면 Ctrl+C 가 그냥 `c` 가 되어 **인터럽트가 안 간다**.
    let mods = SessionView::key_from_keystroke(&ks("c", true, false, false)).unwrap().1;
    assert!(mods.ctrl);
}

// ── 창에 맞춘 격자(슬라이스 12) ──────────────────────────────────────────────
//
// ★ 종전 GUI 는 붙을 때 80×24 를 알리고 그걸로 끝이었다 — 창을 키워도 캔버스가 안 자라고
// (나머지는 빈 배경), 줄이면 화면이 창 밖으로 넘쳤다. 아래가 그 자리를 지킨다.

#[test]
fn the_grid_fills_the_window_minus_the_chrome() {
    // 자리표: 원점 (16, 48) · 칸 8×16. 창 816×848 · 아래 구역 없음.
    // 가로 = (816 - 16 - 8)/8 = 99 · 세로 = (848 - 48 - 8)/16 = 49.5 → 49
    let got = SessionView::grid_for(probe_at(16., 48., 8., 16.), 816., 848., 0.);
    assert_eq!(got, Some((99, 49)));
}

#[test]
fn the_summary_area_takes_its_rows_out_of_the_canvas() {
    // ★ 재는 것은 **아래 구역의 높이**이지 창 바닥까지의 거리가 아니다. 후자로 재면 그
    // 아래 빈 자리까지 크롬으로 세어 캔버스가 프레임마다 줄어든다(실측: 10줄 → 3줄).
    // 세로 = (848 - 48 - 248 - 8)/16 = 34
    let got = SessionView::grid_for(probe_at(16., 48., 8., 16.), 816., 848., 248.);
    assert_eq!(got, Some((99, 34)));
}

#[test]
fn a_window_too_small_for_one_cell_is_not_reported() {
    // 알리면 서버가 최소 크기로 클램프해 되돌려 주고, 그 프레임이 매번 다시 온다.
    assert_eq!(SessionView::grid_for(probe_at(16., 48., 8., 16.), 20., 848., 0.), None);
    assert_eq!(SessionView::grid_for(probe_at(16., 48., 8., 16.), 816., 50., 0.), None);
}

#[test]
fn a_degenerate_probe_reports_nothing_instead_of_dividing_by_zero() {
    assert_eq!(SessionView::grid_for(probe_at(0., 0., 0., 16.), 800., 800., 0.), None);
    assert_eq!(SessionView::grid_for(probe_at(0., 0., 8., f32::NAN), 800., 800., 0.), None);
}

#[test]
fn the_grid_never_rounds_up() {
    // ★ 한 줄 남는 것은 빈 줄 하나지만, 한 줄 넘치면 아래 구역이 창 밖으로 밀려
    // **블록·복사 알림이 통째로 안 보인다**. 0.9칸이 남아도 안 올린다.
    let got = SessionView::grid_for(probe_at(0., 0., 10., 10.), 108., 108., 0.);
    assert_eq!(got, Some((10, 10)), "{got:?} — 남는 자리를 한 칸으로 올렸다");
}

fn probe_at(x: f32, y: f32, w: f32, h: f32) -> RectF {
    RectF::new(vec2f(x, y), vec2f(w, h))
}

// ── 입력기(IME) 확정 글자 — 슬라이스 11 ──────────────────────────────────────

#[test]
fn a_committed_string_reaches_the_pane() {
    // 한글은 자판 한 번이 글자 하나가 아니다. 조합 결과는 키가 아니라 문자열로 온다.
    assert_eq!(SessionView::typed_target(InputMode::Normal, false, "한글"), TypedTo::Pane);
}

#[test]
fn nothing_is_typed_while_the_user_is_talking_to_pytmux() {
    // 명령 모드에서 확정된 글자는 명령이 아니다. 패널로 흘리면 사용자가 pytmux 에게
    // 말하는 중에 셸에 글자가 찍힌다. **판이 없을 때** 이야기다(아래 ⓜ2 참조).
    for mode in [InputMode::Command, InputMode::Scroll] {
        assert_eq!(SessionView::typed_target(mode, false, "한글"), TypedTo::Drop, "{mode:?}");
    }
}

#[test]
fn an_empty_commit_sends_nothing() {
    // 입력기는 조합을 취소할 때 빈 확정을 보낸다. 그걸 그대로 보내면 빈 입력 프레임이
    // 매번 서버로 나간다.
    assert_eq!(SessionView::typed_target(InputMode::Normal, false, ""), TypedTo::Drop);
    assert_eq!(SessionView::typed_target(InputMode::Command, true, ""), TypedTo::Drop);
}

#[test]
fn a_committed_string_reaches_the_open_panel() {
    // ★ **제보 그대로**(§10-21ⓜ2): `esc` `:` 에서 한글을 못 쳤다. 판이 열려 있으면
    //   확정된 글자는 **그 판의 것**이다 — 종전에는 모드만 보고 버렸다.
    assert_eq!(SessionView::typed_target(InputMode::Command, true, "한글"), TypedTo::Screen);
    // 평소 모드에서 판이 열려 있는 경우(작성창 등)도 판이 먼저다.
    assert_eq!(SessionView::typed_target(InputMode::Normal, true, "한글"), TypedTo::Screen);
}

#[test]
fn korean_typed_into_the_palette_lands_in_the_filter() {
    // 순수 판정만 재면 배선이 빠져도 통과한다 — **실제로 필터에 쌓이는지**를 본다.
    let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.handle_key(Key::Escape, Mods::NONE);
    view.handle_key(Key::Char(':'), Mods::NONE);
    assert_eq!(view.screens.top(), Some(Screen::Commands), "팔레트가 안 열렸다");
    assert!(view.handle_typed("한글"), "확정 글자를 아무도 안 받았다");
    assert_eq!(view.screens.typed(), "한글", "필터에 안 쌓였다");
}

#[test]
fn korean_typed_in_command_mode_without_a_panel_still_goes_nowhere() {
    // 반대쪽도 지킨다 — 판이 없으면 종전 그대로다(셸에 글자가 찍히면 안 된다).
    let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.handle_key(Key::Escape, Mods::NONE);
    assert_eq!(view.screens.top(), None, "판이 없어야 한다");
    assert!(!view.handle_typed("한글"), "esc 모드에서 글자가 새 나갔다");
}

// ── 마우스 패스스루(슬라이스 10) ─────────────────────────────────────────────
//
// Shift+드래그만 앱에게 넘어간다. 이 판정이 **넓으면** 사용자의 복사 드래그가 앱으로 새고,
// **좁으면** 마우스 1급 앱(p4v-tui 의 스플리터 등) 안에서 드래그를 아예 못 한다.

/// 왼쪽 패널만 마우스 추적을 켠 상태. 오른쪽은 안 켰다.
fn tracking_state() -> SessionState {
    let mut state = SessionState::new();
    let msg = serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [
            {"id": 1, "x": 0, "y": 0, "w": 40, "h": 24, "active": true, "mouse": 1},
            {"id": 2, "x": 40, "y": 0, "w": 40, "h": 24},
        ]
    });
    state.apply(serde_json::from_value(msg).unwrap());
    state
}

#[test]
fn shift_drag_over_a_mouse_app_goes_to_the_app() {
    let state = tracking_state();
    assert!(SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 5), true));
}

#[test]
fn a_plain_drag_is_a_selection_even_over_a_mouse_app() {
    // ★ 평드래그를 앱에게 넘기면 **화면의 글자를 꺼낼 방법이 사라진다** — 이 클라에는
    // 마우스 캡처를 대신 풀어 줄 바깥 터미널이 없다.
    let state = tracking_state();
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 5), false));
}

#[test]
fn nothing_is_forwarded_while_the_user_is_talking_to_pytmux() {
    // 명령·스크롤 모드에서 마우스만 앱으로 새면 모드가 반쪽이 된다.
    let state = tracking_state();
    for mode in [InputMode::Command, InputMode::Scroll] {
        assert!(
            !SessionView::press_goes_to_app(&state, mode, (10, 5), true),
            "{mode:?} 에서 넘어갔다"
        );
    }
}

#[test]
fn an_app_that_never_asked_for_the_mouse_gets_nothing() {
    // 안 켠 앱에 리포트를 보내면 그 바이트가 프롬프트에 **글자로 찍힌다**.
    let state = tracking_state();
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (60, 5), true));
    // 캔버스 밖도 마찬가지다.
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 99), true));
}

/// 뒤 패널이 추적을 켠 채, 추적을 켠 앱이 든 팝업이 떠 있는 판(popup.mouse).
fn popup_tracking_state() -> SessionState {
    let mut state = SessionState::new();
    let msg = serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24, "active": true,
                   "mouse": 1, "mouse_sgr": true}],
        "popup": {"id": 99, "x": 10, "y": 5, "w": 40, "h": 10,
                  "cx": 11, "cy": 6, "cw": 38, "ch": 8,
                  "mouse": 2, "mouse_sgr": true}
    });
    state.apply(serde_json::from_value(msg).unwrap());
    state
}

#[test]
fn a_shift_press_inside_the_popup_goes_to_the_popup_app() {
    // 서버가 popup.mouse 를 광고하면 GUI 판정도 팝업 안 앱을 대상으로 잡는다.
    let state = popup_tracking_state();
    assert!(SessionView::press_goes_to_app(&state, InputMode::Normal, (12, 7), true));
    // 테두리와 팝업 밖(뒤 패널이 추적 중이어도)은 아니다 — 모달 규칙.
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 5), true));
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (60, 20), true));
}

// ── 붙여넣기 조합(슬라이스 9) ────────────────────────────────────────────────
//
// 이 판정이 틀리면 **아무 소리 없이** 어긋난다: 좁으면 붙여넣기가 안 되고, 넓으면 패널
// 안 프로그램의 키가 사라진다. 라이브로도 양쪽을 봤다(리포트 슬라이스 9) — Ctrl+Shift+V
// 는 클립보드가 패널에 들어갔고, Ctrl+V 는 아무 일도 안 났다.

#[test]
fn ctrl_shift_v_asks_for_a_paste() {
    assert!(SessionView::is_paste_chord(&ks("v", true, false, true)));
    assert!(SessionView::is_paste_chord(&ks("V", true, false, true)), "대문자로 와도 같은 키다");
}

#[test]
fn plain_ctrl_v_belongs_to_the_program_in_the_pane() {
    // ★ 0x16 은 패널 안 프로그램의 바이트다. 가로채면 그 프로그램의 기능이 조용히 사라진다.
    assert!(!SessionView::is_paste_chord(&ks("v", true, false, false)));
}

#[test]
fn a_paste_needs_both_modifiers_and_nothing_else() {
    assert!(!SessionView::is_paste_chord(&ks("v", false, false, true)), "Shift+V 는 대문자 V 다");
    assert!(!SessionView::is_paste_chord(&ks("c", true, false, true)), "Ctrl+Shift+C 는 다른 키다");
    // Alt 가 섞이면 다른 조합이다 — 넓게 잡으면 그 조합이 통째로 사라진다.
    assert!(!SessionView::is_paste_chord(&ks("v", true, true, true)));
}

// ── 하단 한 줄(§10-21ⓝ·ⓦ·ⓗ2) ────────────────────────────────────────────────
//
// 제보 셋이 **같은 줄**을 두고 있다: 복사 결과를 그 자리로 모으고(ⓝ) 닫을 수 있게 하고
// 시한을 주며(ⓦ) 그래서 비워진 머리줄을 가운데로 옮긴다(ⓗ2).

#[test]
fn the_copy_note_lands_on_the_bottom_line_not_the_head() {
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.note_flash_for_test("· 20 chars copied".into(), proto::session::Severity::Ok);
    let painted = painted_after_setup(vec![layout_one_pane()], &[], |v| {
        v.note_flash_for_test("· 20 chars copied".into(), proto::session::Severity::Ok)
    });
    assert!(
        painted_contains(&painted, "20 chars copied"),
        "복사 결과가 프레임에 없다: {painted:?}"
    );
    // 머리줄에는 **안 붙는다** — 그 줄에는 이제 이름과 주소만 남는다(ⓗ2 의 전제).
    assert!(
        painted
            .iter()
            .any(|t| t.starts_with("pytmux-gui · ") && !t.contains("copied")),
        "머리줄에 복사 결과가 아직 붙어 있다: {painted:?}"
    );
}

#[test]
fn the_line_is_not_always_red() {
    // ★ 이 줄은 빨강 고정이었다. 복사 결과까지 여기 오면서 **성공이 오류로 읽히던**
    //   자리다 — 색은 심각도가 정하고, 표는 알림 이력과 같은 것을 쓴다.
    use proto::session::Severity;
    assert_eq!(SessionView::severity_color(Severity::Ok), palette::GREEN);
    assert_eq!(SessionView::severity_color(Severity::Error), palette::RED);
    assert_eq!(SessionView::severity_color(Severity::Warn), palette::YELLOW);
    assert_ne!(
        SessionView::severity_color(Severity::Ok),
        SessionView::severity_color(Severity::Error),
        "성공과 오류가 같은 색이면 이 줄은 종전과 같다"
    );
}

#[test]
fn the_bottom_line_can_be_closed_but_the_history_stays() {
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.state.note_error("서버가 성났다");
    view.pump_headless();
    assert!(view.live_flash_for_test().is_some(), "오류가 하단 줄로 안 왔다");
    let before = view.state.notices().len();
    assert!(view.chrome_click(base::chrome::ClickTarget::DismissMessage));
    assert!(view.live_flash_for_test().is_none(), "닫아도 줄이 남는다");
    // ⚠ 이력은 남아야 한다 — 지우면 그 줄을 눌러 이력으로 가는 동선이 무의미해진다.
    assert_eq!(view.state.notices().len(), before, "닫기가 이력까지 지웠다");
}

#[test]
fn a_disconnect_line_does_not_expire() {
    // 끊김은 지나가는 사건이 아니라 **지금 상태**다 — 사라지면 화면은 멀쩡해 보이는데
    // 아무것도 안 오는 창이 된다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.ended = Some("서버가 닫았다".into());
    let flash = view.live_flash_for_test().expect("끊김 줄이 없다");
    assert!(flash.has_no_deadline(), "끊김에 시한이 붙었다");
}

// ── 판 공통 기하(§10-21 ⓗ·ⓢ·ⓥ·ⓐ2·ⓚ2) ───────────────────────────────────────
//
// 제보 다섯의 뿌리가 하나다: **판의 기하를 내용이 정한다**. 여기서 재는 것은 그 반대다 —
// 내용이 달라져도 판이 같은가.

/// 판 하나를 열고 **그려진 줄 수**를 센다.
fn panel_line_count(messages: Vec<ServerMessage>, keys: &[(Key, Mods)]) -> usize {
    painted_after(messages, keys).len()
}

#[test]
fn the_palette_keeps_its_height_when_the_filter_narrows() {
    // ⓗ⑵ — 후보 수가 달라져도 높이는 그대로다. 종전에는 목록이 짧아지면 판이 줄었다.
    //
    // ⚠ **필터는 후보가 "적지만 0 은 아닌" 자리라야 한다**(처음에 여기서 틀렸다):
    //   0 개면 "맞는 명령이 없다" 갈래로 빠져 다른 코드가 채우고, 예산을 넘게 많으면
    //   양쪽 다 예산만큼 그려 차이가 안 난다 — 둘 다 변이가 살아남는다.
    let open = |keys: &[(Key, Mods)]| {
        let mut all = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
        all.extend_from_slice(keys);
        painted_after(three_tabs(), &all)
    };
    let wide = open(&[]);
    assert!(
        wide.iter().any(|t| t.starts_with("> ")),
        "팔레트 입력줄이 없다 — 판이 안 열렸다: {wide:?}"
    );
    // `notice-history` 하나만 남는 필터.
    let typed: Vec<(Key, Mods)> =
        "notice".chars().map(|c| (Key::Char(c), Mods::NONE)).collect();
    let narrow = open(&typed);
    let hits = narrow.iter().filter(|t| t.contains("notice-history")).count();
    assert_eq!(hits, 1, "필터가 한 줄만 남기지 않았다 — 단언이 뜻을 잃는다: {narrow:?}");
    // ⚠ **조각 수가 아니라 줄 수를 센다**(§10-21ⓞ 이후). 한 줄이 세 칸으로 갈리면서
    //   "그려진 글 조각의 수"는 더 이상 높이의 대리값이 아니다 — 그 CL 에서 이 단언이
    //   먼저 울었고, 그것이 옳았다(대리값이 뜻을 잃었다는 신호다).
    let rows = |keys: &[(Key, Mods)]| -> usize {
        let mut all = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
        all.extend_from_slice(keys);
        let boxes = painted_boxes(three_tabs(), &all);
        let mut ys: Vec<i64> = boxes.iter().map(|(_, y)| y.round() as i64).collect();
        ys.sort_unstable();
        ys.dedup();
        ys.len()
    };
    assert_eq!(
        rows(&[]),
        rows(&typed),
        "필터로 좁혔더니 판 높이가 달라졌다(ⓗ)
넓을 때: {wide:?}
좁을 때: {narrow:?}"
    );
}

#[test]
fn the_palette_is_half_and_the_others_are_two_thirds() {
    // 비율의 주인은 core 다 — 제보가 팔레트만 "절반"이라고 못박았다.
    use base::screens::Screen;
    assert_eq!(Screen::Commands.height_ratio(), (1, 2));
    assert_eq!(Screen::Settings.height_ratio(), (2, 3));
    assert_eq!(Screen::Notices.height_ratio(), (2, 3));
}

#[test]
fn a_short_list_still_fills_the_panel() {
    // ⓥ — 굴려 끝에 가까워져도, 목록이 짧아도 판은 그대로다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    let budget = view.panel_budget_for_test();
    assert!(budget >= 5, "예산이 너무 작아 단언이 뜻을 잃는다: {budget}");
    // 빈 목록도 예산만큼 자리를 차지한다(빈 줄로 채운다).
    let padded = view.pad_rows_count_for_test(0, budget);
    assert_eq!(padded, budget, "모자란 줄을 안 채운다");
}

#[test]
fn a_long_line_is_cut_so_it_cannot_push_the_panel_out() {
    // ⓚ2 — `p4changes` 의 CL 설명 한 줄이 그 부류다. 폭을 못박아도 줄이 안 잘리면
    // 상한을 넘겨 밀고 나간다.
    let long = "x".repeat(400);
    let cut = proto::footer::elide(&long, 110);
    assert!(cut.chars().count() <= 110, "안 잘렸다: {}", cut.chars().count());
    assert!(cut.ends_with('…'), "잘렸으면 그 사실이 보여야 한다: {cut}");
    // 짧은 줄은 안 건드린다(자르는 것이 목적이 아니다).
    assert_eq!(proto::footer::elide("짧다", 110), "짧다");
}

// ── 모드 표식·켜짐 표시(§10-21ⓖ·ⓧ) ─────────────────────────────────────────
//
// 두 제보가 **같은 그림**을 요구한다: 배경을 채운 반전 칩. ⓖ 는 `esc` 모드를 눈에 띄게,
// ⓧ 는 토글이 켜졌다는 것을 배지가 말하게.

#[test]
fn the_mode_badge_sits_in_the_bottom_status_bar_not_the_tab_bar() {
    // 프레임은 위에서 아래로 그려진다 — 탭바에 남아 있으면 캔버스보다 **먼저**,
    // 상태줄로 갔으면 **나중에** 그려진다(감시류 배지와 같은 방법으로 잰다).
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), screen], &[(Key::Escape, Mods::NONE)]);
    let mode_at = painted.iter().position(|t| t.contains("esc"));
    let canvas_at = painted.iter().position(|t| t.contains("HELLO-ORACLE"));
    assert!(mode_at.is_some(), "esc 모드 표식이 프레임에 없다: {painted:?}");
    assert!(canvas_at.is_some(), "캔버스가 없다 — 단언이 공허해진다: {painted:?}");
    assert!(
        mode_at > canvas_at,
        "모드 표식이 캔버스보다 먼저 그려졌다(=탭바 자리 그대로다): {painted:?}"
    );
}

#[test]
fn a_toggle_badge_says_when_it_is_on() {
    // ★ 상태는 이미 클라가 안다 — 안 하던 것은 그것을 모양에 싣는 일뿐이었다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    assert!(
        !view.badge_is_on_for_test(base::Badge::TouchScroll),
        "스크롤 모드가 아닌데 켜짐이다"
    );
    view.apply_action_for_test(base::Action::ToggleScroll);
    assert!(
        view.badge_is_on_for_test(base::Badge::TouchScroll),
        "스크롤 모드에 들어왔는데 배지가 꺼짐이다(ⓧ)"
    );
}

#[test]
fn a_button_badge_is_never_on() {
    // 누르면 화면이 열리는 **버튼**은 토글이 아니다 — 켜짐을 말할 것이 없다.
    let (view, _tx, _sent) = harness();
    for badge in [base::Badge::Notices, base::Badge::Host, base::Badge::Clock, base::Badge::Calendar] {
        assert!(!view.badge_is_on_for_test(badge), "{badge:?} 가 켜짐으로 나온다");
    }
}

#[test]
fn on_and_picked_are_different_pictures() {
    // ⚠ FOCUS 는 "키보드가 이것을 고르고 있다"이고 반전은 "켜져 있다"다. 색이 같으면
    //   그 둘이 한 그림이 되어 어느 쪽인지 알 수 없다.
    assert_ne!(theme::INVERT_BG, theme::FOCUS);
    // 반전 칩은 배경 위에 글자를 빼낸다 — 둘이 같으면 글자가 안 보인다.
    assert_ne!(theme::INVERT_BG, theme::INVERT_FG);
}

// ── Alt+Tab 동선(§10-21ⓕ·ⓔ2·ⓕ2) ────────────────────────────────────────────
//
// 셋이 한 이야기다: 스위처를 열면 커서가 **다음 탭**에 있고(ⓔ2), `Ctrl` 을 쥔 채 `Tab`
// 으로 돌며(ⓕ2), 짧게 눌렀다 떼면 그것이 곧 "다음 탭으로"다(ⓕ).

#[test]
fn the_switcher_opens_on_the_next_tab_not_the_first() {
    // 정본이 일부러 그렇게 한다 — `esc Tab Enter` 가 곧 "다음 탭으로 전환"이어야 한다.
    let (mut view, tx, _sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.apply_action_for_test(base::Action::ShowTabs);
    assert_eq!(view.screens.top(), Some(base::screens::Screen::Tabs));
    assert_eq!(view.screens.selected(), 1, "첫 선택이 '다음 탭'이 아니다");
}

#[test]
fn a_single_tab_does_not_open_the_switcher() {
    // 고를 것이 없는 목록을 띄우는 것은 "아무 일도 안 일어난다"와 같다(정본과 같은 규칙).
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(
        serde_json::from_value(serde_json::json!({"t": "status", "windows": [
            {"index": 0, "name": "하나", "active": true},
        ]}))
        .unwrap(),
    )))
    .unwrap();
    view.pump_headless();
    view.apply_action_for_test(base::Action::ShowTabs);
    assert_eq!(view.screens.top(), None, "탭이 하나뿐인데 스위처가 열렸다");
}

#[test]
fn ctrl_tab_is_the_switcher_chord() {
    assert_eq!(SessionView::tab_switch_chord(&ks("tab", true, false, false)), Some(true));
    assert_eq!(
        SessionView::tab_switch_chord(&ks("tab", true, false, true)),
        Some(false),
        "Shift 가 섞이면 뒤로다"
    );
    // 평범한 Tab 은 **패널의 것**이다(셸 자동완성) — 가로채면 조용히 사라진다.
    assert_eq!(SessionView::tab_switch_chord(&ks("tab", false, false, false)), None);
    assert_eq!(SessionView::tab_switch_chord(&ks("tab", true, true, false)), None);
    assert_eq!(SessionView::tab_switch_chord(&ks("a", true, false, false)), None);
}

#[test]
fn holding_ctrl_walks_the_list_and_releasing_confirms() {
    // ★ 이것이 ⓕ2 의 전부다: 쥔 채 돌고, 떼면 확정.
    let (mut view, tx, sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.alt_tab_step_for_test(true); // Ctrl+Tab — 열리고 커서는 '다음 탭'
    assert_eq!(view.screens.top(), Some(base::screens::Screen::Tabs));
    assert_eq!(view.screens.selected(), 1);
    view.alt_tab_step_for_test(true); // 쥔 채 한 번 더 — 한 줄 아래
    assert_eq!(view.screens.selected(), 2, "쥔 채 누른 Tab 이 목록을 안 움직였다");
    view.release_ctrl(); // 뗌 = 확정
    view.pump_headless();
    assert_eq!(view.screens.top(), None, "떼도 판이 안 닫혔다");
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter().any(|o| matches!(o, Outgoing::Command(Command::SelectWindow { .. }))),
        "확정했는데 탭 전환이 안 나갔다: {out:?}"
    );
}

#[test]
fn a_quick_ctrl_tab_is_just_next_tab() {
    // ⓕ 의 동작은 ⓕ2 에서 **저절로** 나온다 — 열자마자 커서가 다음 탭이므로.
    let (mut view, tx, sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.alt_tab_step_for_test(true);
    view.release_ctrl();
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    let picked = out.iter().find_map(|o| match o {
        Outgoing::Command(Command::SelectWindow { index, .. }) => Some(*index),
        _ => None,
    });
    assert_eq!(picked, Some(1), "짧게 눌렀다 뗀 것이 '다음 탭'이 아니다: {out:?}");
}

#[test]
fn releasing_ctrl_without_holding_does_nothing() {
    // 평소에 Ctrl 을 떼는 일은 흔하다 — 그때마다 무언가 일어나면 안 된다.
    let (mut view, tx, _sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert!(!view.release_ctrl());
    assert_eq!(view.screens.top(), None);
}

// ── 패널 커서(§10-21ⓒ) ───────────────────────────────────────────────────────
//
// 배선이 **통째로 없던** 자리다: 서버는 `screen` 마다 커서를 통째로 주는데 뷰가 그 값을
// 한 번도 안 읽었다. 그래서 여기서 재는 것은 "그 값이 화면 칸으로 옮겨지나"다.
//
// ⚠ **그림 자체는 이 하네스가 못 잰다.** 시험 글꼴은 칸 폭이 0이라 오버레이의 paint 가
// `cw <= 0.5` 가드에서 돌아간다(스플리터·테두리도 같은 이유로 여기서 안 그려진다) —
// 그 층은 `client/CLAUDE.md` 가 적은 대로 **라이브 스크린샷이** 잡는다. 여기서는 그
// 앞 단계(어느 칸인가)를 못 박는다.

fn screen_with_cursor(col: u16, row: u16) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO", {}]]], "cursor": [col, row], "wrap": [], "top": 0
    }))
    .unwrap()
}

#[test]
fn the_cursor_lands_on_the_cell_the_server_named() {
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(3, 2)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    let cur = view.cursor_cell().expect("커서를 안 그린다 — 서버가 준 값을 아무도 안 읽는다");
    // 이 패널은 (0,0) 에서 시작하므로 패널 안 좌표가 곧 캔버스 좌표다.
    assert_eq!((cur.x, cur.y), (3, 2));
}

#[test]
fn the_cursor_follows_the_pane_offset() {
    // 패널이 캔버스 안쪽에 있으면 그만큼 밀려야 한다 — 안 밀면 분할했을 때 커서가
    // 왼쪽 패널에 나타난다(그 증상이 곧 ⓨ 의 모양이라 여기서 못 박는다).
    let layout: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 10, "active": 1,
        "panes": [{"id": 1, "x": 10, "y": 4, "w": 20, "h": 5, "title": "sh", "active": true}]
    }))
    .unwrap();
    let (mut view, tx, _sent) = harness();
    for msg in [layout, screen_with_cursor(2, 1)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    let cur = view.cursor_cell().unwrap();
    assert_eq!((cur.x, cur.y), (12, 5));
}

#[test]
fn no_cursor_while_a_panel_is_open() {
    // 판이 떠 있는 동안 키는 그 판의 것이다 — 그때 패널 커서를 그리면 "여기 치면
    // 들어간다"는 거짓말이 된다.
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(1, 1)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert!(view.cursor_cell().is_some(), "먼저 그려지고 있어야 뜻이 있는 단언이다");
    view.screens.open(base::screens::Screen::Keys);
    assert!(view.cursor_cell().is_none());
}

#[test]
fn a_cursor_outside_the_pane_is_not_drawn() {
    // 밀린 자리에 상자를 그리면 그것이 새 거짓말이 된다.
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(200, 0)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert!(view.cursor_cell().is_none());
}

// ── 글자 크기 배율(§10-21ⓐ) ──────────────────────────────────────────────────
//
// 제보가 "패널 캔버스만이 아니라 **앱 전체**"라고 못박았다. 그래서 재는 것 둘이다:
// ⑴ 그 조합이 실제로 잡히는가(넓지도 좁지도 않게) ⑵ **글자 크기가 실제로 바뀌는가**.
//
// ⑵ 를 부정 단언으로 두면(예: "패널로 안 샌다") 배율을 아무 데도 안 곱해도 통과한다 —
// `client/CLAUDE.md` 가 두 번 밟았다고 적은 그 자리다. 그래서 `scaled()` 가 만드는
// **값**을 직접 본다.

#[test]
fn ctrl_plus_and_minus_change_the_text_size() {
    assert_eq!(
        SessionView::font_scale_chord(&ks("=", true, false, false)),
        Some(Action::FontScale { up: true })
    );
    // Shift 를 눌러 `+` 가 오는 사람도 같은 뜻이다 — 하나만 받으면 절반에게 안 먹는다.
    assert_eq!(
        SessionView::font_scale_chord(&ks("+", true, false, true)),
        Some(Action::FontScale { up: true })
    );
    assert_eq!(
        SessionView::font_scale_chord(&ks("-", true, false, false)),
        Some(Action::FontScale { up: false })
    );
    assert_eq!(
        SessionView::font_scale_chord(&ks("0", true, false, false)),
        Some(Action::FontScaleReset)
    );
}

#[test]
fn plain_minus_belongs_to_the_program_in_the_pane() {
    // ★ 넓게 잡으면 패널에서 `-` 를 못 친다. 좁게 잡으면 확대가 안 된다 — 둘 다 조용하다.
    assert_eq!(SessionView::font_scale_chord(&ks("-", false, false, false)), None);
    assert_eq!(SessionView::font_scale_chord(&ks("=", false, false, false)), None);
    // Alt 가 섞이면 다른 조합이다.
    assert_eq!(SessionView::font_scale_chord(&ks("-", true, true, false)), None);
    // 글자 키는 배율과 상관없다(`Ctrl+a` 를 삼키면 셸의 줄 처음 가기가 사라진다).
    assert_eq!(SessionView::font_scale_chord(&ks("a", true, false, false)), None);
}

/// 배율을 **쓰기 경로를 안 타고** 옮긴다.
///
/// ⚠ `apply_action(FontScale)` 은 `set_number` 를 거쳐 **진짜 설정 파일**에 쓴다
/// (`Config::path_for_write` — 이 상자에서 돌리는 사람의 파일이다). 이 저장소는
/// 테스트에서 `PYTMUX_CONFIG` 를 세우는 것을 금한다(프로세스 전역이라 형제 테스트와
/// 경합한다 — `config_tests.rs:865` 의 그 자리). 그래서 여기서는 **값만** 옮기고,
/// "그 값이 설정 파일 형식으로 오간다"는 `base` 의 파일 왕복 오라클이 잰다.
/// 액션이 실제로 이 자리에 닿는지는 `every_action_does_something_in_this_view` 가 센다.
fn step_scale(view: &mut SessionView, up: bool) {
    view.config.font_scale = base::config::font_scale_step(view.config.font_scale, up);
}

#[test]
fn the_whole_app_grows_not_just_the_canvas() {
    let (mut view, _tx, _sent) = harness();
    let before = view.scaled(13.);
    step_scale(&mut view, true);
    let after = view.scaled(13.);
    assert!(
        after > before,
        "글자가 안 커졌다 — 배율이 어디에도 안 곱해진 것이다: {before} → {after}"
    );
    // 크롬 글자도 **같은 배율**을 탄다(제보의 "앱 전체"가 그 뜻이다). 두 자리가 서로
    // 다른 배율을 타면 탭바만 그대로 남는다.
    assert!((view.scaled(11.) / 11. - view.scaled(13.) / 13.).abs() < 1e-6);
}

#[test]
fn the_size_comes_back_to_one() {
    let (mut view, _tx, _sent) = harness();
    let base = view.scaled(13.);
    for _ in 0..3 {
        step_scale(&mut view, true);
    }
    assert!(view.scaled(13.) > base);
    view.config.font_scale = 1.0;
    assert!(
        (view.scaled(13.) - base).abs() < 1e-6,
        "되돌리기가 기본으로 안 온다 — 작게 줄여 놓으면 설정 화면조차 못 읽는다"
    );
}

// ── Claude 구역(P5) ──────────────────────────────────────────────────────────

fn tool(state: ToolState) -> ClaudeItem {
    ClaudeItem {
        kind: claude::ItemKind::Tool { name: "Bash".into(), state },
        title: "ls -la".into(),
        detail: None,
    }
}

fn said(kind: claude::ItemKind) -> ClaudeItem {
    ClaudeItem { kind, title: "안녕".into(), detail: None }
}

#[test]
fn a_denied_tool_is_not_painted_like_a_failed_one() {
    // ★ 사용자가 할 일이 **정반대**다: 막힌 것은 허용하거나 그대로 두고, 깨진 것은
    // 고친다. 빨강으로 뭉치면 "고쳐야 할 것"과 "안 시킨 것"이 한 덩어리가 된다.
    assert_ne!(
        SessionView::claude_color(&tool(ToolState::Denied)),
        SessionView::claude_color(&tool(ToolState::Failed))
    );
}

#[test]
fn a_running_tool_is_not_painted_like_a_finished_one() {
    // 결과가 안 온 툴 호출은 진행 중이지 성공이 아니다(블록의 `??` 와 같은 규칙).
    let running = SessionView::claude_color(&tool(ToolState::Running));
    assert_ne!(running, SessionView::claude_color(&tool(ToolState::Ok)));
    assert_ne!(running, SessionView::claude_color(&tool(ToolState::Failed)));
}

#[test]
fn what_the_user_typed_reads_differently_from_what_claude_said() {
    // 둘이 같은 색이면 목록에서 대화의 방향이 사라진다.
    assert_ne!(
        SessionView::claude_color(&said(claude::ItemKind::Prompt)),
        SessionView::claude_color(&said(claude::ItemKind::Answer))
    );
}

#[test]
fn every_claude_color_is_opaque() {
    // 알파 0 이면 그 부류의 줄만 통째로 안 보인다 — "대화가 안 온다"와 구분되지 않는다.
    for state in [ToolState::Ok, ToolState::Failed, ToolState::Running, ToolState::Denied] {
        assert_eq!(SessionView::claude_color(&tool(state)).a, 0xff, "{state:?}");
    }
    for kind in [
        claude::ItemKind::Prompt,
        claude::ItemKind::Answer,
    ] {
        assert_eq!(SessionView::claude_color(&said(kind)).a, 0xff);
    }
}

#[test]
fn a_plan_is_labelled_in_a_word_the_user_knows() {
    // `ExitPlanMode` 는 내부 이름이다. 정본이 정하지만 GUI 가 그걸 실제로 쓰는지도
    // 물어 둔다 — 뷰가 kind 를 다시 match 하기 시작하면 그때부터 갈린다.
    let plan = ClaudeItem {
        kind: claude::ItemKind::Plan { state: ToolState::Running },
        title: "3단계".into(),
        detail: None,
    };
    assert_eq!(plan.name(), Some("플랜"));
    assert_eq!(plan.badge(), ToolState::Running.badge());
}

#[test]
fn every_tone_is_opaque() {
    // 알파 0 이면 그 부류의 줄만 통째로 안 보인다 — "블록이 안 온다"와 구분되지 않는다.
    for tone in ALL_TONES {
        assert_eq!(SessionView::block_color(tone).a, 0xff, "{tone:?} 가 투명하다");
    }
}

/// 팔레트 전수. 와일드카드 없는 `named` 의 match 가 **컴파일로** 누락을 막지만, 위
/// 테스트들이 실제로 전부를 훑으려면 목록이 필요하다.
const ALL_NAMED: [NamedColor; 16] = [
    NamedColor::Black,
    NamedColor::Red,
    NamedColor::Green,
    NamedColor::Yellow,
    NamedColor::Blue,
    NamedColor::Magenta,
    NamedColor::Cyan,
    NamedColor::White,
    NamedColor::BrightBlack,
    NamedColor::BrightRed,
    NamedColor::BrightGreen,
    NamedColor::BrightYellow,
    NamedColor::BrightBlue,
    NamedColor::BrightMagenta,
    NamedColor::BrightCyan,
    NamedColor::BrightWhite,
];

// ── 좌표 보정(P7 마우스 · §4.2 스플리터) ─────────────────────────────────────
//
// 이 자리가 **GUI 에만 있는 문제**다: TUI 는 터미널 이벤트가 이미 셀 좌표지만 GUI 는
// 픽셀이다. 그리고 보정을 계산하면 렌더와 어긋나므로, 렌더가 남긴 사각형 하나로 푼다.
// 여기서 잡는 것은 그 산수다 — 창 없이 물을 수 있는 유일한 부분이다.

use warpui::geometry::rect::RectF;
use warpui::geometry::vector::vec2f;

/// 원점 (100, 50) · 칸 8×16 인 자리표.
fn probe() -> RectF {
    RectF::new(vec2f(100., 50.), vec2f(8., 16.))
}

#[test]
fn the_probe_origin_is_cell_zero_zero() {
    assert_eq!(SessionView::cell_at(probe(), 100., 50.), Some((0, 0)));
    // 그 칸 안 아무 데나 눌러도 같은 칸이다 — 경계에서만 넘어간다.
    assert_eq!(SessionView::cell_at(probe(), 107.9, 65.9), Some((0, 0)));
}

#[test]
fn one_cell_right_and_down_lands_on_one_one() {
    assert_eq!(SessionView::cell_at(probe(), 108., 66.), Some((1, 1)));
    assert_eq!(SessionView::cell_at(probe(), 100. + 8. * 40., 50. + 16. * 7.), Some((40, 7)));
}

#[test]
fn a_click_above_or_left_of_the_canvas_is_not_a_cell() {
    // ★ 캔버스 위는 탭바다. 음수를 u16 으로 접으면 **엉뚱한 칸**(65535 근처)이 되고,
    // 그러면 탭바를 누를 때마다 화면 끝 패널이 반응한다.
    assert_eq!(SessionView::cell_at(probe(), 99., 60.), None);
    assert_eq!(SessionView::cell_at(probe(), 110., 49.), None);
}

// ── 휠(슬라이스 8) ───────────────────────────────────────────────────────────
//
// 좌표 보정이 생기기 전까지 GUI 의 휠은 대상을 **서버 판단**(활성 패널)에 맡기고
// 있었다. 아래가 그 자리를 지킨다 — 커서 아래 패널이 활성 패널과 **다를 때** 갈린다.

/// 좌우로 나뉜 두 패널(각 40칸)을 가진 상태. `active` 는 왼쪽(1)이다.
fn split_state() -> SessionState {
    let mut state = SessionState::new();
    let msg = serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [
            {"id": 1, "x": 0, "y": 0, "w": 40, "h": 24, "active": true},
            {"id": 2, "x": 40, "y": 0, "w": 40, "h": 24},
        ]
    });
    state.apply(serde_json::from_value(msg).unwrap());
    state
}

#[test]
fn the_wheel_rolls_the_pane_under_the_cursor_not_the_active_one() {
    // ★ 이 테스트가 지키는 것. 활성 패널만 굴리면, 옆 패널을 보며 휠을 돌리는 사람은
    // **자기 눈앞이 아닌 곳**이 흘러가는 것을 본다. 라이브로도 이 장면을 찍었다
    // (리포트 슬라이스 8: 활성은 왼쪽인데 오른쪽에서 굴려 오른쪽만 움직였다).
    let state = split_state();
    assert_eq!(state.active_pane(), Some(1), "전제가 깨졌다");
    assert_eq!(SessionView::wheel_scroll(&state, true, Some((60, 5))).pane, Some(2));
    assert_eq!(SessionView::wheel_scroll(&state, true, Some((10, 5))).pane, Some(1));
}

#[test]
fn the_wheel_moves_the_same_three_lines_as_the_other_clients() {
    // 세 클라의 감각이 갈리면 같은 손짓이 화면마다 다르게 움직인다. 부호도 함께 본다 —
    // 뒤집히면 휠이 **반대로** 굴러간다(과거 방향이 +).
    let state = split_state();
    assert_eq!(SessionView::wheel_scroll(&state, true, None).delta, Some(3));
    assert_eq!(SessionView::wheel_scroll(&state, false, None).delta, Some(-3));
}

#[test]
fn a_wheel_outside_the_canvas_lets_the_server_decide() {
    // 탭바·아래 요약 구역에서 굴린 휠이다. 여기서 억지로 패널을 고르면 화면 끝 패널이
    // 반응한다 — 모르면 모른다고 하고 서버 판단(활성 패널)에 맡긴다.
    let state = split_state();
    assert_eq!(SessionView::wheel_scroll(&state, true, None).pane, None);
    assert_eq!(SessionView::wheel_scroll(&state, true, Some((10, 99))).pane, None);
    assert_eq!(SessionView::wheel_scroll(&state, true, Some((999, 5))).pane, None);
}

#[test]
fn a_wheel_before_the_first_frame_is_not_aimed_at_a_pane() {
    // 배치가 아직 없으면 좌표가 어디를 가리키는지 알 수 없다.
    let empty = SessionState::new();
    assert_eq!(SessionView::wheel_scroll(&empty, true, Some((10, 5))).pane, None);
}

#[test]
fn a_degenerate_probe_is_refused_instead_of_dividing_by_zero() {
    // 첫 프레임이나 글꼴 사고로 사각형이 0 이면, 짐작해서 처리하는 것보다 아무 일도
    // 안 하는 편이 낫다 — 엉뚱한 패널로 포커스가 가면 사용자는 왜 그런지 모른다.
    assert_eq!(SessionView::cell_at(RectF::new(vec2f(0., 0.), vec2f(0., 16.)), 5., 5.), None);
    assert_eq!(SessionView::cell_at(RectF::new(vec2f(0., 0.), vec2f(8., 0.)), 5., 5.), None);
    let nan = RectF::new(vec2f(0., 0.), vec2f(f32::NAN, 16.));
    assert_eq!(SessionView::cell_at(nan, 5., 5.), None);
}

// ── 큐 오라클 하네스 — **뷰를 통째로 세워 키를 먹인다** ────────────────────────
//
// # 왜 이제야 있나
//
// `SessionView::new` 가 `ViewContext` 를 요구하는 바람에 테스트가 이 뷰를 만들 수 없었다.
// 그래서 GUI 에는 TUI 의 `outgoing_after_*` 에 해당하는 것이 없었고, **G8p 에서 `pump()`
// 배선이 통째로 빠진 것을 워크스페이스 1287개 테스트 중 어느 것도 못 잡았다** — 잡은 것은
// 라이브 스크린샷이었다. 이 절이 그 구멍을 막는다.
//
// # 무엇을 안 지나나 (정직하게)
//
// 창이 진짜로 필요한 둘만 뺀다 — **클립보드 쓰기**와 **창 크기 보고**(`report_size`).
// 나머지(키 해석 · 모드 · 화면 스택 · 크롬 · 액션 → 명령 · 퍼올리기 · 큐 비우기)는 실제와
// **같은 순서로** 지난다. 링크도 흉내가 아니라 진짜다(`ServerLink::detached`).

use proto::link::{LinkEvent, Sent};
use proto::message::ServerMessage;

/// 소켓 없는 뷰 · 받을 것을 밀어 넣는 쪽 · 보낸 것이 쌓이는 자리.
fn harness() -> (SessionView, std::sync::mpsc::Sender<LinkEvent>, Sent) {
    // ★ 설정 쓰기를 **사물함으로 돌린다**(맨 처음 한 번만 먹는다). 액션 축 오라클이
    //   액션 전수를 먹이는데 그중 몇은 설정 파일을 고친다 — 안 돌리면 `cargo test` 가
    //   돌린 사람의 진짜 config 를 고친다(글자 배율은 다음 기동에 눈에 보인다).
    base::config::redirect_writes(std::env::temp_dir().join("pytmux-gui-test-config"));
    let (link, tx, sent) = ServerLink::detached("/tmp/test.sock");
    // 글꼴은 값 하나다 — 그리지 않는 테스트에서는 어느 id 든 상관없다.
    (
        SessionView::with_font(link, warpui::fonts::FamilyId(0)),
        tx,
        sent,
    )
}

/// 서버 메시지를 먹인 뒤 키를 먹이고, **서버로 실제 나간 것**을 돌려준다.
///
/// 키마다 `pump_headless` 를 한 번씩 돌리는 이유: 실제 GUI 도 프레임마다 한 번 돌고,
/// 스레드에서 오는 것(셸 결과)은 그 회전이 있어야 줍힌다.
fn sent_after(messages: Vec<ServerMessage>, keys: &[(Key, Mods)]) -> Vec<Outgoing> {
    let (mut view, tx, sent) = harness();
    for msg in messages {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    for (key, mods) in keys {
        view.handle_key(*key, *mods);
        view.pump_headless();
    }
    let out = sent.lock().unwrap().clone();
    out
}

fn layout_one_pane() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 4, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 4, "title": "sh", "active": true}]
    }))
    .unwrap()
}

fn three_tabs() -> Vec<ServerMessage> {
    vec![
        layout_one_pane(),
        serde_json::from_value(serde_json::json!({"t": "status", "windows": [
            {"index": 0, "name": "하나", "active": true},
            {"index": 1, "name": "둘"},
            {"index": 2, "name": "셋"},
        ]}))
        .unwrap(),
    ]
}

#[test]
fn the_harness_itself_carries_a_key_to_the_server() {
    // ★ 이 오라클이 먼저다. 하네스가 아무것도 안 나르면 **아래 전부가 공허하게 통과한다** —
    // 이 저장소가 정확히 그 방식으로 두 번 속았다.
    let out = sent_after(vec![layout_one_pane()], &[(Key::Char('a'), Mods::NONE)]);
    assert_eq!(
        out,
        vec![Outgoing::Input(b"a".to_vec())],
        "하네스가 키를 서버까지 안 날랐다"
    );
}

#[test]
fn the_prefix_table_reaches_the_server_from_this_view() {
    // prefix c → 새 탭. 표는 core 가 갖지만 **부르는 배선은 뷰마다** 있다.
    let out = sent_after(
        vec![layout_one_pane()],
        &[(Key::Char('b'), Mods::CTRL), (Key::Char('c'), Mods::NONE)],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NewWindow { .. }))),
        "{out:?}"
    );
}

#[test]
fn a_destructive_key_asks_before_it_sends() {
    // prefix x 는 **확인 화면**을 세운다. 바로 나가면 파이썬보다 위험하다.
    let out = sent_after(
        vec![layout_one_pane()],
        &[(Key::Char('b'), Mods::CTRL), (Key::Char('x'), Mods::NONE)],
    );
    assert!(out.is_empty(), "묻지 않고 보냈다: {out:?}");
}

// ── 크롬 포커스(G8r) — TUI 와 **같은 것**을 GUI 에서도 본다 ────────────────────

#[test]
fn the_top_edge_takes_the_focus_to_the_tab_bar_and_enter_switches_tab() {
    let out = sent_after(
        three_tabs(),
        &[
            (Key::Escape, Mods::NONE),
            (Key::Up, Mods::NONE),
            (Key::Right, Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::SelectWindow { index: 1, .. }))),
        "{out:?}"
    );
}

#[test]
fn shift_arrows_on_the_tab_bar_move_the_selected_tab() {
    // 활성은 0번인데 **1번을 골라** 옮긴다 — 활성 탭을 옮기는 명령을 쓰면 여기서 죽는다.
    let out = sent_after(
        three_tabs(),
        &[
            (Key::Escape, Mods::NONE),
            (Key::Up, Mods::NONE),
            (Key::Right, Mods::NONE),
            (Key::ShiftRight, Mods::NONE),
        ],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::MoveTab { index: 1, to: 2 }))),
        "{out:?}"
    );
}

#[test]
fn a_key_that_leaves_esc_mode_also_lets_go_of_the_chrome_focus() {
    let out = sent_after(
        three_tabs(),
        &[
            (Key::Escape, Mods::NONE),
            (Key::Up, Mods::NONE),
            (Key::Char('2'), Mods::NONE),
            (Key::Escape, Mods::NONE),
            (Key::Right, Mods::NONE),
        ],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::SelectPaneDir { .. }))),
        "포커스가 탭바에 남아 방향키를 먹었다: {out:?}"
    );
}

#[test]
fn the_bottom_edge_takes_the_focus_to_the_badges_and_enter_runs_one() {
    // ⚠ 종전 이 테스트는 첫 배지가 `서버` 라는 것에 기대어 `RequestVersion` 을 봤다.
    //   §10-21ⓑ 로 `서버`·`시계`·`달력` 이 목록에서 빠지면서 그 전제가 사라졌다 —
    //   재는 것("아래로 내려가면 배지에 포커스가 가고 Enter 가 그것을 실행한다")은
    //   그대로 두고 **남아 있는 배지**로 재도록 고친다. 알림은 있을 때만 실리므로
    //   하나 만들어 두고 본다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.state.note_notice(String::from("무언가 알림"));
    view.handle_key(Key::Escape, Mods::NONE);
    view.handle_key(Key::Down, Mods::NONE);
    view.handle_key(Key::Enter, Mods::NONE);
    assert_eq!(
        view.screens.top(),
        Some(base::screens::Screen::Notices),
        "아래 모서리에서 Enter 가 첫 배지(알림)를 실행하지 않았다"
    );
}

// ── 퍼올리기 자체 — G8p 가 통째로 빠졌던 자리 ────────────────────────────────

#[test]
fn a_selection_reply_reaches_the_server_buffer() {
    // 이 길은 **오라클이 하나도 없었다** — 클립보드 쓰기가 창을 요구해서 통째로 테스트
    // 밖에 있었다. 서버 버퍼로 가는 절반은 창과 무관하다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(
        serde_json::from_value(serde_json::json!({"t": "selection", "text": "복사한 것"}))
            .unwrap(),
    )))
    .unwrap();
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::SetBuffer { .. }))),
        "{out:?}"
    );
}

#[test]
fn a_delta_without_a_baseline_asks_for_a_full_frame() {
    // 서버가 기준 없는 델타를 보내면 화면이 조용히 멎는다 — 그때 다시 그려 달라고 청하는
    // 것이 뷰의 일이다(상태 누적기는 소켓을 모른다).
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(
        serde_json::from_value(
            serde_json::json!({"t": "screen-delta", "pane": 1, "rows": [], "seq": 2}),
        )
        .unwrap(),
    )))
    .unwrap();
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::RequestRedraw))),
        "{out:?}"
    );
}

#[test]
fn the_link_ending_is_noticed() {
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Ended("서버가 닫았다".into())).unwrap();
    view.pump_headless();
    assert!(view.is_ended());
}

#[test]
fn a_shell_result_reaches_the_screen_through_pump() {
    // ★ **G8p 에서 통째로 빠졌던 바로 그 배선**이다. 셸은 스레드에서 돌고 결과는
    // 퍼올리기가 줍는다 — 그 한 줄이 없으면 결과 화면이 영원히 빈다. 당시 이 크레이트에는
    // 뷰를 세울 방법이 없어 라이브 스크린샷만이 그것을 잡았다.
    let (mut view, _tx, _sent) = harness();
    view.state.apply(layout_one_pane());
    // 팔레트 → run-shell → 명령. 액션을 직접 부르지 않는 이유는 늘 같다 —
    // **그 키가 실제로 걸려 있는지**까지 봐야 한다.
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("run-shell".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend("echo pytmuxhello".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    // 스레드 결과를 기다린다. **안 기다리면 아무 일도 안 일어난 채로 통과한다**(G8p §4.1).
    for _ in 0..200 {
        view.pump_headless();
        if !view.state.shell_output().is_empty() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
    let out = view.state.shell_output().join("\n");
    assert!(out.contains("pytmuxhello"), "셸 결과가 화면에 안 담겼다: {out:?}");
}

// ── lang(i18n) — GUI 에서도 같은 배선이 돈다 ──────────────────────────────────

#[test]
fn picking_a_language_runs_the_whole_wiring_in_the_gui_too() {
    // TUI 의 같은 이름 오라클과 한 쌍이다 — 클라 안에서 끝나는 액션(SetLang)은
    // `action_to_command` 가 None 이라 **뷰마다 손 배선**이 있고, 그 배선은 한쪽만
    // 빠질 수 있다(G8p 의 pump 처럼). 전역 로케일을 안 바꾸는 설계(지금 로케일과
    // 같은 ko 를 고른다)와 그 이유는 TUI 쪽 주석 참조.
    let dir = std::env::temp_dir().join(format!("pytmux-gui-lang-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let lang_file = dir.join("default.sock.lang");
    base::i18n::set_persist_path(lang_file.clone());
    let (mut view, _tx, sent) = harness();
    view.state.apply(layout_one_pane());
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("lang".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE)); // 폼이 뜬다
    keys.push((Key::Enter, Mods::NONE)); // 한국어(ko)를 고른다
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    assert!(
        view.state.notices().any(|n| n.text.contains("언어: 한국어")),
        "언어 전환 피드백 알림이 없다"
    );
    assert_eq!(
        std::fs::read_to_string(&lang_file).ok().as_deref(),
        Some("ko"),
        "선택이 .lang 에 영속되지 않았다"
    );
    // 서버로는 아무것도 안 나간다 — 로케일은 per-user 다.
    assert!(
        sent.lock().unwrap().is_empty(),
        "클라 안에서 끝나야 하는데 서버로 나갔다"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

// ── 마우스 크롬(68332 빚) — 탭·`[+]`·`[x]`·배지 클릭이 Enter 와 같은 길로 간다 ──
//
// Hoverable(레이아웃 히트테스트) → `ViewAction::ChromeClick` → `chrome_click` 중
// 앞 구간은 창 없이 못 세운다(엘리먼트 이벤트 디스패치가 레이아웃을 요구한다) —
// 그 구간은 라이브(frame-dump 로 그림 · Windows 하네스로 클릭)가 잡고, 여기서는
// **판정·배선 구간**(core `chrome::click` → `apply_action` → 큐)을 잰다.

#[test]
fn the_summary_area_no_longer_takes_a_row_from_the_canvas() {
    // ★ 종전 이 자리의 오라클은 "머리줄을 누르면 크롬 높이가 바뀐다"였다(접기 계약).
    //   §10-21ⓓ 로 그 구역이 **화면에서 빠지면서** 접기 자체가 사라졌다 — 이제 재는
    //   것은 그 반대다: 블록이 생겨도 크롬 높이가 **안 늘어난다**(그만큼 캔버스가 늘
    //   넓다). 그것이 이 제보가 하려던 일 전체다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    let empty = view.footer_lines();
    tx.send(LinkEvent::Message(Box::new(
        serde_json::from_value(serde_json::json!({
            "t": "blocks", "pane": 1,
            "blocks": [{"command": "ls", "state": "done", "exit": 0, "start_row": 0}]
        }))
        .unwrap(),
    )))
    .unwrap();
    view.pump_headless();
    assert!(!view.state.active_blocks().is_empty(), "블록이 안 들어왔다 — 단언이 공허해진다");
    assert_eq!(
        view.footer_lines(),
        empty,
        "블록이 생겼다고 크롬이 한 줄 더 먹는다 — 요약 구역이 아직 화면에 있다(ⓓ)"
    );
}

#[test]
fn the_summary_is_reachable_as_a_panel() {
    // 화면에서 뺐으면 **여는 길**이 있어야 한다(제보: "별도 명령어나 메뉴로").
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    assert!(view.apply_action_for_test(base::Action::ShowSummary));
    assert_eq!(view.screens.top(), Some(base::screens::Screen::Summary));
}

#[test]
fn a_chrome_click_travels_the_same_road_as_enter() {
    use base::chrome::ClickTarget;
    use base::TabSpot;
    let (mut view, tx, sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    // 둘째 탭 클릭 → 전환이 나간다.
    view.chrome_click(ClickTarget::Spot(TabSpot::Tab(1)));
    view.pump_headless();
    assert!(
        sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::SelectWindow { index: 1, .. })
        )),
        "탭 클릭이 전환으로 안 나갔다: {:?}",
        sent.lock().unwrap()
    );
    // `[+]` 클릭 → 새 탭이 나간다.
    view.chrome_click(ClickTarget::Spot(TabSpot::New));
    view.pump_headless();
    assert!(
        sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::NewWindow { .. })
        )),
        "[+] 클릭이 새 탭으로 안 나갔다"
    );
    // `[x]` 클릭 → **확인 없이는 안 나간다**(Enter 와 같은 확인 화면 길).
    view.chrome_click(ClickTarget::Spot(TabSpot::Close));
    view.pump_headless();
    assert!(
        !sent.lock().unwrap().iter().any(|o| matches!(o, Outgoing::Command(Command::KillWindow))),
        "확인 없이 탭 닫기가 나갔다"
    );
    // 서버 배지 클릭 → 정보 팝업이 버전을 청한다.
    view.handle_key(Key::Escape, Mods::NONE); // 확인 화면을 닫고
    view.pump_headless();
    view.chrome_click(ClickTarget::Badge(base::Badge::Host));
    view.pump_headless();
    assert!(
        sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::RequestVersion)
        )),
        "서버 배지 클릭이 정보 요청으로 안 나갔다"
    );
}

// ── 팝업 완성(68295 빚) — w/h 와이어 · 마우스는 팝업이 먼저(GUI 도 같은 한 벌) ──

#[test]
fn the_popup_wants_and_the_modal_wheel_work_in_the_gui_too() {
    let popup_layout: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24, "title": "sh"}],
        "popup": {"id": 99, "x": 10, "y": 5, "w": 40, "h": 10,
                  "cx": 11, "cy": 6, "cw": 38, "ch": 8, "title": "top"}
    }))
    .unwrap();
    // ① 물음 대답의 `-w/-h` 가 와이어에 실린다(판정은 proto 한 벌 — TUI 와 같은 문법).
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("display-popup".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend("-w 40 -h 10 top".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    let frame = sent
        .lock()
        .unwrap()
        .iter()
        .find_map(|o| match o {
            Outgoing::Command(cmd) => Some(cmd.to_frame()),
            _ => None,
        })
        .expect("팝업 명령이 안 나갔다");
    assert_eq!(frame["action"], "popup_open");
    assert_eq!(frame["w"], 40);
    assert_eq!(frame["h"], 10);
    // ② 팝업이 떠 있으면 휠은 커서 위치와 무관하게 팝업을 굴린다(모달).
    tx.send(LinkEvent::Message(Box::new(popup_layout))).unwrap();
    view.pump_headless();
    view.handle_wheel(true, Some((75, 20)));
    view.pump_headless();
    let scrolled: Vec<_> = sent
        .lock()
        .unwrap()
        .iter()
        .filter_map(|o| match o {
            Outgoing::Scroll(scroll) => Some(scroll.pane),
            _ => None,
        })
        .collect();
    assert!(
        !scrolled.is_empty() && scrolled.iter().all(|pane| *pane == Some(99)),
        "팝업이 아니라 다른 것이 굴렀다"
    );
}

#[test]
fn the_wheel_reaches_the_popup_app_in_the_gui_too() {
    // popup.mouse 광고가 서면 GUI 도 팝업 안 앱에 휠 리포트를 넘긴다(TUI 와 한 벌).
    let popup_layout: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24, "title": "sh"}],
        "popup": {"id": 99, "x": 10, "y": 5, "w": 40, "h": 10,
                  "cx": 11, "cy": 6, "cw": 38, "ch": 8, "title": "top",
                  "mouse": 2, "mouse_sgr": true}
    }))
    .unwrap();
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(popup_layout))).unwrap();
    view.pump_headless();
    view.handle_wheel(true, Some((15, 8)));
    view.pump_headless();
    let sent = sent.lock().unwrap();
    let reports: Vec<_> = sent
        .iter()
        .filter_map(|o| match o {
            Outgoing::Mouse { pane, data } => {
                Some((*pane, String::from_utf8_lossy(data).into_owned()))
            }
            _ => None,
        })
        .collect();
    // 팝업 내용은 (11,6) 시작 → 1-based 로 열 5, 행 3. 64 = WheelUp.
    assert_eq!(reports, vec![(99, "\u{1b}[<64;5;3M".to_owned())], "휠 리포트가 안 갔다");
    assert!(
        !sent.iter().any(|o| matches!(o, Outgoing::Scroll(_))),
        "뷰 스크롤로도 샜다"
    );
}

// ── run-shell 버퍼 · if-shell else (파이썬 `_run_shell`/`_if_shell` 동형) ───────

#[test]
fn shell_output_reaches_the_server_buffer_and_else_runs_in_the_gui_too() {
    // GUI 는 이 표를 **자기 사본**으로 갖는다("두 뷰가 같은 표") — 한쪽만 고치는
    // 실수를 이 오라클이 잡는다.
    let (mut view, _tx, sent) = harness();
    view.state.apply(layout_one_pane());
    // ① run-shell 출력 → set_buffer 가 큐로.
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("run-shell".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend("echo pytmuxbuf".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    for _ in 0..200 {
        view.pump_headless();
        if !view.state.shell_output().is_empty() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
    view.pump_headless();
    assert!(
        sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::SetBuffer { text }) if text.contains("pytmuxbuf")
        )),
        "출력이 서버 버퍼로 안 갔다"
    );
    // ② if-shell 실패 갈래 — `exit 1 | clear-history | redraw` 는 redraw 를 돌린다.
    view.handle_key(Key::Escape, Mods::NONE); // 셸 결과 화면 닫기
    view.pump_headless();
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("if-shell".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend("exit 1 | clear-history | redraw".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    let mut redrew = false;
    for _ in 0..200 {
        view.pump_headless();
        if sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::RequestRedraw)
        )) {
            redrew = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
    assert!(redrew, "실패 갈래의 redraw 가 안 나갔다");
    assert!(
        !sent.lock().unwrap().iter().any(|o| matches!(o, Outgoing::Command(Command::ClearHistory))),
        "성공 갈래가 잘못 돌았다"
    );
}

// ── 이벤트 훅(G8u) — 사건이 나면 명령이 서버까지 간다 ─────────────────────────

/// 팔레트에서 `set-hook` 을 골라 한 줄을 걸고, 그 뒤 서버 메시지를 먹인다.
///
/// 액션을 직접 부르지 않는 이유는 늘 같다 — **그 이름이 팔레트에 실제로 걸려 있고,
/// 물음이 실제로 뜨고, 대답이 실제로 훅 표에 닿는지**까지 한 줄로 봐야 한다.
fn sent_after_hook(hook_line: &str, messages: Vec<ServerMessage>) -> Vec<Outgoing> {
    let (mut view, tx, sent) = harness();
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("set-hook".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend(hook_line.chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    for msg in messages {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    out
}

fn status_with(count: usize, bell: bool) -> ServerMessage {
    let windows: Vec<serde_json::Value> = (0..count)
        .map(|i| {
            serde_json::json!({
                "index": i, "name": format!("탭{i}"), "active": i == 0, "bell": bell && i == 0
            })
        })
        .collect();
    serde_json::from_value(serde_json::json!({"t": "status", "windows": windows})).unwrap()
}

#[test]
fn a_new_tab_fires_the_hook_and_the_command_reaches_the_server() {
    // ★ 양성 오라클. "안 나갔다"만 재는 시험은 배선이 통째로 빠져도 통과한다(G8p).
    let out = sent_after_hook(
        "after-new-window next-tab",
        vec![layout_one_pane(), status_with(1, false), status_with(2, false)],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NextWindow))),
        "탭이 늘었는데 훅이 안 돌았다: {out:?}"
    );
}

#[test]
fn the_first_tab_list_does_not_fire_the_hook() {
    // 붙자마자 탭 셋이 보이는 것은 "셋 생긴" 것이 아니다 — 여기서 발화하면 붙을 때마다
    // 훅이 돈다.
    let out = sent_after_hook(
        "after-new-window next-tab",
        vec![layout_one_pane(), status_with(3, false)],
    );
    assert!(
        !out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NextWindow))),
        "첫 목록에서 발화했다: {out:?}"
    );
}

#[test]
fn a_hook_argument_skips_the_question_and_goes_straight_out() {
    // 훅이 도는 자리에는 물음에 답할 사람이 없다 — 인자가 있으면 그 대답이 이미 나온
    // 것처럼 처리한다.
    let out = sent_after_hook(
        "after-new-window rename-tab 빌드",
        vec![layout_one_pane(), status_with(1, false), status_with(2, false)],
    );
    assert!(
        out.iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::RenameWindow { name }) if name == "빌드"
        )),
        "인자 있는 훅이 안 돌았다: {out:?}"
    );
}

#[test]
fn a_bell_fires_its_own_hook() {
    let out = sent_after_hook(
        "alert-bell next-tab",
        vec![layout_one_pane(), status_with(1, false), status_with(1, true)],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NextWindow))),
        "벨이 울렸는데 훅이 안 돌았다: {out:?}"
    );
}

#[test]
fn attaching_fires_its_own_hook() {
    // `client-attached` 는 **첫 배치**가 발화점이다(정본과 같다).
    let out = sent_after_hook("client-attached next-tab", vec![layout_one_pane()]);
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NextWindow))),
        "붙었는데 훅이 안 돌았다: {out:?}"
    );
}

#[test]
fn unsetting_a_hook_stops_it() {
    let (mut view, tx, sent) = harness();
    let mut keys: Vec<(Key, Mods)> = Vec::new();
    for line in ["after-new-window next-tab", "-u after-new-window"] {
        keys.push((Key::Escape, Mods::NONE));
        keys.push((Key::Char(':'), Mods::NONE));
        keys.extend("set-hook".chars().map(|c| (Key::Char(c), Mods::NONE)));
        keys.push((Key::Enter, Mods::NONE));
        keys.extend(line.chars().map(|c| (Key::Char(c), Mods::NONE)));
        keys.push((Key::Enter, Mods::NONE));
    }
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    for msg in [layout_one_pane(), status_with(1, false), status_with(2, false)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        !out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NextWindow))),
        "푼 훅이 계속 돈다: {out:?}"
    );
}

// ── 인자 폼(G8v) — TUI 와 **같은 것**을 GUI 에서도 본다 ──────────────────────

/// 팔레트에서 이름을 골라 폼을 연 뒤 그 안에서 키를 더 먹인다.
fn sent_from_option_form(name: &str, inside: &[(Key, Mods)]) -> Vec<Outgoing> {
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend(name.chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend(inside.iter().copied());
    sent_after(vec![layout_one_pane()], &keys)
}

#[test]
fn the_form_reaches_the_server_from_this_view_too() {
    // ★ 이 오라클이 먼저다 — 폼이 GUI 에서 안 열리면 아래가 공허하게 통과한다.
    let out = sent_from_option_form("split-window", &[(Key::Enter, Mods::NONE)]);
    assert!(
        out.iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::Split {
                horizontal: true,
                ..
            })
        )),
        "폼에서 고른 것이 서버까지 안 갔다: {out:?}"
    );
}

#[test]
fn the_arrow_changes_the_value_in_the_gui_form() {
    let out = sent_from_option_form(
        "split-window",
        &[(Key::Right, Mods::NONE), (Key::Enter, Mods::NONE)],
    );
    assert!(
        out.iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::Split {
                horizontal: false,
                ..
            })
        )),
        "→ 가 값을 안 바꿨다: {out:?}"
    );
}

#[test]
fn on_carries_a_value_out_of_the_gui_form() {
    let frames: Vec<serde_json::Value> = sent_from_option_form(
        "synchronize-panes",
        &[(Key::Right, Mods::NONE), (Key::Enter, Mods::NONE)],
    )
    .iter()
    .filter_map(|o| match o {
        Outgoing::Command(cmd) => Some(cmd.to_frame()),
        _ => None,
    })
    .collect();
    let frame = frames
        .iter()
        .find(|f| f["action"] == "set_sync")
        .expect("set_sync 가 안 나갔다");
    assert_eq!(frame["value"], true, "{frame}");
}

#[test]
fn escape_leaves_the_gui_form_without_doing_anything() {
    let out = sent_from_option_form(
        "synchronize-panes",
        &[(Key::Right, Mods::NONE), (Key::Escape, Mods::NONE)],
    );
    assert!(
        !out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::ToggleSync { .. }))),
        "취소했는데 나갔다: {out:?}"
    );
}

// ── 프롬프트 점프(패리티 `e_jump`) — TUI 와 **같은 것**을 GUI 에서도 본다 ─────

#[test]
fn esc_ctrl_arrows_jump_and_keep_jumping_from_this_view() {
    // 배선은 뷰마다 있다. TUI 오라클이 초록이어도 GUI 가 같은 키를 안 나르면 그 사실은
    // **라이브 스크린샷 전까지 아무도 모른다**(G8p 가 정확히 그랬다).
    let out = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Up, Mods::CTRL),
            (Key::Down, Mods::CTRL),
        ],
    );
    let jumps: Vec<&str> = out
        .iter()
        .filter_map(|o| match o {
            Outgoing::Command(Command::JumpPrompt { direction }) => Some(*direction),
            _ => None,
        })
        .collect();
    assert_eq!(jumps, vec!["up", "down"], "실제: {out:?}");
    // 그 키가 패널로도 새면 자식이 커서를 같이 움직인다.
    assert!(
        !out.iter().any(|o| matches!(o, Outgoing::Input(_))),
        "점프 키가 패널로 샜다: {out:?}"
    );
}

#[test]
fn a_plain_ctrl_up_still_reaches_the_pane_from_this_view() {
    let out = sent_after(vec![layout_one_pane()], &[(Key::Up, Mods::CTRL)]);
    assert_eq!(out, vec![Outgoing::Input(b"\x1b[A".to_vec())], "{out:?}");
}

// ── 여러 줄 작성창(패리티 `e_ins`) — TUI 와 **같은 것**을 GUI 에서도 본다 ─────

fn pasted(out: &[Outgoing]) -> Vec<String> {
    out.iter()
        .filter_map(|o| match o {
            Outgoing::Command(Command::Paste { text }) => Some(text.clone()),
            _ => None,
        })
        .collect()
}

#[test]
fn esc_insert_composes_and_enter_sends_one_paste_from_this_view() {
    // GUI 배선은 뷰마다 따로다 — TUI 오라클이 초록이어도 여기가 빠지면 **라이브
    // 스크린샷 전까지 아무도 모른다**(G8p 가 그랬다).
    let out = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('a'), Mods::NONE),
            (Key::ShiftEnter, Mods::NONE),
            (Key::Char('b'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    assert_eq!(pasted(&out), vec!["a\nb".to_owned()], "실제: {out:?}");
    // 작성 중 글자가 패널로 새면 셸에 그대로 찍힌다.
    assert!(
        !out.iter().any(|o| matches!(o, Outgoing::Input(_))),
        "작성 중 키가 패널로 샜다: {out:?}"
    );
}

#[test]
fn esc_esc_cancels_the_compose_box_without_sending() {
    let out = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('z'), Mods::NONE),
            (Key::Escape, Mods::NONE),
            (Key::Escape, Mods::NONE),
        ],
    );
    assert!(pasted(&out).is_empty(), "취소했는데 나갔다: {out:?}");
}

#[test]
fn the_draft_survives_a_cancel_in_this_view_too() {
    // `Esc` 는 "안 넣겠다"이지 "버리겠다"가 아니다.
    let out = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('z'), Mods::NONE),
            (Key::Escape, Mods::NONE),
            (Key::Escape, Mods::NONE),
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    assert_eq!(pasted(&out), vec!["z".to_owned()], "초안이 사라졌다: {out:?}");
}

#[test]
fn ctrl_a_inside_the_compose_box_selects_instead_of_closing_it() {
    // ★ 순서 함정: "수정키 조합은 화면이 알 바 아니다"를 먼저 보면 편집 중 `Ctrl+A` 가
    // **화면을 닫는다**. 고른 뒤 한 글자를 치면 통째로 바뀌는 것으로 확인한다.
    let out = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('o'), Mods::NONE),
            (Key::Char('l'), Mods::NONE),
            (Key::Char('d'), Mods::NONE),
            (Key::Char('a'), Mods::CTRL),
            (Key::Char('N'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    assert_eq!(pasted(&out), vec!["N".to_owned()], "실제: {out:?}");
}

// ── 정보 팝업(패리티 `InfoTabsScreen`) — GUI 배선 ─────────────────────────────

#[test]
fn the_server_badge_opens_the_info_tabs_and_asks_for_the_version_from_this_view() {
    // 배지 동선은 뷰마다 배선이 따로다. 버전 탭은 서버가 채우므로 **열면서 함께 청해야**
    // 한다 — 안 청하면 그 줄이 영영 "묻는 중"이다.
    //
    // ⚠ 입구가 **상태줄 오른쪽의 `#h` 구간**으로 옮겼다(§10-21ⓑ·ⓑ2) — 왼쪽 `서버`
    //   배지는 없앴다. 뜻(`Badge::Host`)은 그대로이므로 그 클릭 대상으로 잰다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.chrome_click(base::chrome::ClickTarget::Badge(base::Badge::Host));
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert_eq!(view.screens.top(), Some(base::screens::Screen::InfoTabs));
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::RequestVersion))),
        "버전을 안 청했다: {out:?}"
    );
}

#[test]
fn the_info_tab_content_is_the_same_in_both_views() {
    // ★ 줄을 만드는 것은 `proto` 한 곳이다. 뷰가 각자 지으면 **같은 팝업이 GUI 와 TUI
    // 에서 다른 말을 한다** — 이 저장소가 이미 두 번 만든 갈라짐이다. 여기서는 그 함수가
    // 두 뷰에 같은 것을 준다는 사실을 못박는다(그리는 모양은 각자다).
    let state = proto::SessionState::new();
    let tabs = proto::info::tabs(&state, "/tmp/test.sock", 0.0);
    let titles: Vec<&str> = tabs.iter().map(|(t, _)| *t).collect();
    assert_eq!(titles, vec!["서버", "세션"]);
}

// ── 프롬프트 인계·비우기(패리티 G9c) — GUI 배선 ──────────────────────────────

fn claude_pane_messages() -> Vec<ServerMessage> {
    vec![
        layout_one_pane(),
        serde_json::from_value(serde_json::json!({
            "t": "status",
            "windows": [{"index": 0, "name": "claude", "active": true}],
            // 활성 패널 id 는 status 가 든다 — 없으면 긁을 대상을 못 찾는다.
            "active_pane": 1,
            "panes_claude": [{"id": 1, "claude": true}],
        }))
        .unwrap(),
        serde_json::from_value(serde_json::json!({
            "t": "screen", "pane": 1,
            "rows": [[["────────", {}]], [["❯ 한글넷", {}]], [["────────", {}]]],
            "cursor": [0, 1], "wrap": [], "top": 0
        }))
        .unwrap(),
    ]
}

#[test]
fn the_gui_clears_the_prompt_before_pasting_and_counts_characters() {
    // 배선은 뷰마다 따로다. TUI 오라클이 초록이어도 여기가 빠지면 GUI 에서만 글이 **두
    // 번** 들어간다 — 그 사실은 라이브 전까지 아무도 모른다(G8p 가 그랬다).
    let out = sent_after(
        claude_pane_messages(),
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('x'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    // 인계된 글은 "한글넷"(3자) → 백스페이스 3개가 **먼저**, 그다음 paste.
    assert_eq!(
        out,
        vec![
            Outgoing::Input(vec![0x7f, 0x7f, 0x7f]),
            Outgoing::Command(Command::Paste { text: "한글넷x".to_owned() }),
        ],
        "실제: {out:?}"
    );
}

#[test]
fn the_gui_does_not_scrape_a_shell_pane() {
    let out = sent_after(
        vec![
            layout_one_pane(),
            serde_json::from_value(serde_json::json!({
                "t": "status",
                "windows": [{"index": 0, "name": "sh", "active": true}],
                "active_pane": 1,
            }))
            .unwrap(),
            serde_json::from_value(serde_json::json!({
                "t": "screen", "pane": 1,
                "rows": [[["────────", {}]], [["❯ ~/dir", {}]], [["────────", {}]]],
                "cursor": [0, 1], "wrap": [], "top": 0
            }))
            .unwrap(),
        ],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('z'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    assert_eq!(
        out,
        vec![Outgoing::Command(Command::Paste { text: "z".to_owned() })],
        "셸 패널을 긁었다: {out:?}"
    );
}

// ── 전체 재시작의 드라이런 게이트(패리티 `restart-all`) ───────────────────────
//
// **이 하네스에서만 잴 수 있다.** GUI 큐 오라클은 서버 메시지를 `LinkEvent` 로 밀어 넣어
// 실제 `pump_messages` 를 태우므로 게이트가 진짜로 돈다. TUI 렌더 하네스는 메시지를 상태에
// 직접 넣어(게이트를 안 지나) 이것을 못 본다 — 그 사실을 알고 여기 둔다.

fn restart_check(safe: bool) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "restart_check",
        "reexec_supported": safe,
        "has_sessions": true,
        "serialize_ok": true,
        "panes": 1,
        "panes_with_fd": 1,
    }))
    .unwrap()
}

/// ★ 게이트 오라클은 **`restart-server` 로** 돈다(`restart-all` 이 아니다).
///
/// `restart-all` 은 통과하면 `restart::relaunch()` 를 부르고, 그것은 **진짜로 프로세스를
/// 띄운다** — 테스트에서 부르면 테스트 이진의 자식이 생겨 스위트가 자기를 다시 돌린다
/// (처음에 그렇게 짜서 실제로 그랬다). 게이트 자체는 두 종류가 **같은 코드**를 지나므로
/// (`begin_restart` → `gate_restart`) 서버 종류로 재도 지키는 것이 같다. `All` 쪽의
/// 다른 점(재기동 판정)은 `base::restart` 단위 테스트가 든다.
///
/// 팔레트로 명령 하나를 실행하는 키 열(`prefix :` → 이름 → Enter).
fn palette(name: &str) -> Vec<(Key, Mods)> {
    let mut keys = vec![(Key::Char('b'), Mods::CTRL), (Key::Char(':'), Mods::NONE)];
    keys.extend(name.chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys
}

/// 키를 먹인 **뒤** 서버 메시지를 밀어 넣고 퍼올린다 — 게이트는 회신이 늦게 오는 자리다.
fn sent_after_then(messages: Vec<ServerMessage>, keys: &[(Key, Mods)], late: Vec<ServerMessage>)
    -> Vec<Outgoing>
{
    let (mut view, tx, sent) = harness();
    for msg in messages {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    for (key, mods) in keys {
        view.handle_key(*key, *mods);
        view.pump_headless();
    }
    for msg in late {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    out
}

fn has_restart(out: &[Outgoing]) -> bool {
    out.iter()
        .any(|o| matches!(o, Outgoing::Command(Command::RestartServer)))
}

#[test]
fn a_green_dry_run_lets_the_restart_through() {
    // 양성 오라클 — 점검이 통과하면 **확인 없이** 진행한다(파이썬과 같다).
    let out = sent_after_then(
        vec![layout_one_pane()],
        &palette("restart-server"),
        vec![restart_check(true)],
    );
    assert!(has_restart(&out), "통과했는데 재시작이 안 나갔다: {out:?}");
}

#[test]
fn a_failing_dry_run_blocks_the_restart_until_it_is_confirmed() {
    // ★ 이 상자가 정확히 그 경우다 — Windows 서버는 re-exec 를 못 한다
    // (`reexec_supported: false`, 2026-07-30 실측). 그때 조용히 진행하면 되돌릴 수 없다.
    let out = sent_after_then(
        vec![layout_one_pane()],
        &palette("restart-server"),
        vec![restart_check(false)],
    );
    assert!(!has_restart(&out), "실패했는데 그냥 재시작했다: {out:?}");
}

#[test]
fn confirming_after_a_failed_dry_run_does_restart() {
    // 막는 것으로 끝이 아니다 — 사용자가 실패 항목을 보고 "그래도" 라고 하면 진행한다.
    let mut keys = palette("restart-server");
    keys.push((Key::Char('y'), Mods::NONE)); // 확인 화면의 예
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    for (key, mods) in &keys[..keys.len() - 1] {
        view.handle_key(*key, *mods);
        view.pump_headless();
    }
    tx.send(LinkEvent::Message(Box::new(restart_check(false)))).unwrap();
    view.pump_headless();
    // 여기서 확인 화면이 떠 있어야 `y` 가 뜻을 갖는다.
    view.handle_key(Key::Char('y'), Mods::NONE);
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(has_restart(&out), "확인했는데 재시작이 안 나갔다: {out:?}");
}

// ── 탭 드래그(G9w) — 판정·배선 구간(hover 히트는 라이브 몫 — 크롬 클릭과 같은 경계) ──

#[test]
fn a_tab_drag_dropped_on_the_canvas_joins_into_that_pane() {
    // ★ 드롭 → core `drag_drop` → 두 명령이 **그 순서로**(select_pane_id 먼저 —
    //   서버가 그 사이의 활성 패널에 붙는 것을 막는다. TUI 와 같은 표).
    let (mut view, tx, sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    // TabPress(1) 가 하는 일 — 엘리먼트 이벤트는 레이아웃 없이 못 세워 직접 세운다.
    view.tab_drag = Some(1);
    assert!(view.handle_mouse_up(Some((2, 2))), "드롭이 처리 안 됐다");
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    let wanted: Vec<&Outgoing> = out
        .iter()
        .filter(|o| {
            matches!(
                o,
                Outgoing::Command(Command::SelectPaneId { .. } | Command::JoinPane { .. })
            )
        })
        .collect();
    assert_eq!(
        wanted,
        vec![
            &Outgoing::Command(Command::SelectPaneId { id: 1 }),
            &Outgoing::Command(Command::JoinPane { src: 1, horizontal: true }),
        ],
        "합치기 두 명령이 순서대로 안 나갔다: {out:?}"
    );
}

#[test]
fn a_tab_drag_released_nowhere_falls_back_to_select() {
    // 캔버스도 탭도 아닌 자리(상태줄·창 밖)에서 놓으면 클릭과 같은 뜻 — 전환.
    let (mut view, tx, sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.tab_drag = Some(2);
    assert!(view.handle_mouse_up(None));
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::SelectWindow { index: 2, .. })
        )),
        "전환이 안 나갔다: {out:?}"
    );
    assert!(
        !out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::MoveTab { .. } | Command::JoinPane { .. }))),
        "빈 자리 드롭이 재정렬/합치기로 샜다: {out:?}"
    );
}

// ── 그리기 오라클 — "화면에 무엇이 보이나"를 Scene 에서 기계로 잰다 (G8s 의 남은 빚) ──
//
// # 무엇이 새로 가능해졌나
//
// 큐 오라클은 "서버로 무엇이 나갔나"만 본다. 그리기 배선(상태 → render_* → 엘리먼트)이
// 빠지면 워크스페이스 전부가 초록인 채 화면만 비는데(G8p 류), 그것을 잡는 것은 지금까지
// **라이브 스크린샷뿐**이었다. Scene 의 글리프는 glyph_id 라 글자로 못 되돌린다 — 그래서
// 글자를 그리는 엘리먼트가 **원문을 Scene 에 같이 기록**하게 했고(`Scene::record_text`),
// 이 절이 그 기록을 단언한다.
//
// # 무엇을 안 지나나 (정직하게)
//
// 시험 폰트(`platform::test::FontDB`)는 **빈 Line** 을 돌려준다 — 글자 폭이 전부 0 이라
// 가로 배치·잘림·픽셀 좌표는 여기서 재지 못한다(그건 여전히 frame-dump 라이브 몫).
// 여기서 재는 것은 **존재와 순서**다: 어떤 글자가 그려지는 프레임에 실렸는가.

/// 서버 메시지(와 키)를 먹인 뷰의 한 프레임을 헤드리스로 그려, 그려진 글자들을 돌려준다.
///
/// `App::test` + `Presenter::build_scene` — `clipped_tests` 가 쓰는 그 헤드리스 GUI
/// 파이프라인이라, 레이아웃·페인트·레이어 스택을 실제와 같은 코드로 지난다.
fn painted_after(messages: Vec<ServerMessage>, keys: &[(Key, Mods)]) -> Vec<String> {
    painted_after_setup(messages, keys, |_| {})
}

/// 위와 같지만 그리기 **직전에** 뷰를 한 번 더 만진다.
///
/// 왜 필요한가: 끊김(`ended`)처럼 **서버 메시지가 아니라 이벤트 루프가** 세우는 상태가
/// 있다. 그 상태의 그림은 메시지만 먹여서는 세울 수 없다.
fn painted_after_setup(
    messages: Vec<ServerMessage>,
    keys: &[(Key, Mods)],
    setup: impl FnOnce(&mut SessionView) + 'static,
) -> Vec<String> {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    let keys = keys.to_vec();
    warpui::App::test((), |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        for msg in messages {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        for (key, mods) in keys {
            view.handle_key(key, mods);
            view.pump_headless();
        }
        setup(&mut view);
        let (window_id, _handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation {
            updated,
            ..Default::default()
        };
        app.update(move |ctx| {
            presenter.invalidate(invalidation, ctx);
            let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
            scene.painted_texts().map(|t| t.text.clone()).collect()
        })
    })
}

/// 위와 같지만 **세로 자리까지** 돌려준다 — 판이 화면 어디에 섰나를 재는 자리용.
///
/// ⚠ 시험 폰트는 글자 **폭**이 0이라 가로는 못 잰다(위 절 머리말). 세로는 줄 높이가
/// 살아 있어 잴 수 있다 — 그 사실 자체를 `the_bounds_oracle_sees_vertical_positions`
/// 가 먼저 확인한다(빈 오라클로 배치를 단언하면 아무것도 안 재고 통과한다).
fn painted_boxes(messages: Vec<ServerMessage>, keys: &[(Key, Mods)]) -> Vec<(String, f32)> {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    let keys = keys.to_vec();
    warpui::App::test((), |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        for msg in messages {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        for (key, mods) in keys {
            view.handle_key(key, mods);
            view.pump_headless();
        }
        let (window_id, _handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation {
            updated,
            ..Default::default()
        };
        app.update(move |ctx| {
            presenter.invalidate(invalidation, ctx);
            let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
            scene
                .painted_texts()
                .map(|t| (t.text.clone(), t.bounds.origin().y()))
                .collect()
        })
    })
}

/// 그 글자가 그려진 세로 자리(여럿이면 첫 것).
fn painted_y(boxes: &[(String, f32)], needle: &str) -> Option<f32> {
    boxes.iter().find(|(t, _)| t.contains(needle)).map(|(_, y)| *y)
}

fn painted_contains(painted: &[String], needle: &str) -> bool {
    painted.iter().any(|t| t.contains(needle))
}

#[test]
fn the_oracle_itself_sees_a_painted_frame() {
    // ★ 이 오라클이 먼저다 — 기록이 통째로 안 되면 아래 전부가 "없다" 단언만 남아
    // 공허하게 통과한다(부정 단언만 있는 오라클 금지 규칙과 같은 뿌리).
    let painted = painted_after(vec![], &[]);
    assert!(
        painted_contains(&painted, "첫 화면을 기다리는 중"),
        "빈 상태의 대기 문구조차 안 그려졌다 — 기록 장치가 죽었다: {painted:?}"
    );
}

#[test]
fn the_pane_screen_text_is_painted() {
    // G8p 류(상태는 쌓이는데 그리기 배선이 빠짐)를 잡는 양성 오라클.
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), screen], &[]);
    assert!(
        painted_contains(&painted, "HELLO-ORACLE"),
        "패널 화면 글자가 프레임에 없다: {painted:?}"
    );
}

/// 배율을 세운 채 한 프레임 그리고, 그 글자가 차지한 **높이**를 돌려준다.
///
/// 왜 높이인가: 시험 폰트는 글자 **폭**이 0이라 가로는 못 잰다(위 절 머리말) — 줄
/// 높이는 살아 있고 글자 크기를 따라간다. 그래서 "캔버스가 실제로 커졌나"를 잴 수
/// 있는 유일한 축이다.
fn painted_height_at_scale(scale: f32, needle: &str) -> Option<f32> {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    let needle = needle.to_owned();
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    warpui::App::test((), |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        for msg in [layout_one_pane(), screen] {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        view.config.font_scale = scale;
        let (window_id, _handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation {
            updated,
            ..Default::default()
        };
        app.update(move |ctx| {
            presenter.invalidate(invalidation, ctx);
            let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
            scene
                .painted_texts()
                .find(|t| t.text.contains(&needle))
                .map(|t| t.bounds.height())
        })
    })
}

#[test]
fn the_canvas_rows_grow_with_the_scale() {
    // ★ `scaled()` 만 재면 **캔버스가 그것을 안 쓰는** 변이가 살아남는다(`render_row`
    //   의 `13.` 을 안 곱해도 크롬은 커지므로 다른 오라클이 다 통과한다). 그래서 실제로
    //   그려진 캔버스 글자의 높이를 잰다 — 제보의 "앱 전체"에서 캔버스가 그 몫이다.
    let one = painted_height_at_scale(1.0, "HELLO-ORACLE").expect("캔버스 글자가 안 그려졌다");
    let two = painted_height_at_scale(2.0, "HELLO-ORACLE").expect("캔버스 글자가 안 그려졌다");
    assert!(
        two > one,
        "배율을 두 배로 했는데 캔버스 줄 높이가 그대로다 — render_row 가 배율을 안 탄다: {one} → {two}"
    );
}

#[test]
fn monitor_badges_sit_in_the_bottom_status_bar_not_the_tab_bar() {
    // 사용자 요청(2026-07-30): 감시류 표식([벨감시]·[활동감시])은 파이썬 정본의 시스템
    // 배지 자리인 **하단 상태줄**이다. 프레임은 위에서 아래로 그려지므로, 탭바에
    // 남아 있으면 캔버스(HELLO-ORACLE)보다 먼저, 상태줄로 갔으면 나중에 그려진다.
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    // ⚠ 표본을 **활동 감시**로 바꿨다 — 벨 감시는 화면에서 감췄다(§10-21ⓜ). 그 표식으로
    //   자리를 재면 이제 "없다"가 되어 오라클이 뜻을 잃는다.
    let flags: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status", "windows": [{"index": 0, "name": "하나", "active": true}],
        "monitor_activity": true, "monitor_bell": true
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), screen, flags], &[]);
    let bell_at = painted.iter().position(|t| t.contains("[활동감시]"));
    let canvas_at = painted.iter().position(|t| t.contains("HELLO-ORACLE"));
    assert!(bell_at.is_some(), "[활동감시] 표식이 프레임에 없다: {painted:?}");
    assert!(canvas_at.is_some(), "캔버스가 없다: {painted:?}");
    assert!(
        bell_at > canvas_at,
        "[활동감시] 가 캔버스보다 먼저(=탭바에) 그려졌다 — 하단 상태줄이 자리다: {painted:?}"
    );
    // ★ 그리고 **벨 감시는 켜져 있어도 안 그려진다**(§10-21ⓜ) — 같은 프레임에서 잰다.
    assert!(
        !painted.iter().any(|t| t.contains("[벨감시]")),
        "감춘 표식이 그려졌다: {painted:?}"
    );
}

#[test]
fn the_disconnect_message_sits_below_the_status_bar_and_opens_the_notice_history() {
    // 사용자 요청(2026-07-30): 종전 자리는 **탭바 바로 밑**이었다 — 줄이 생기는 순간
    // 캔버스를 아래로 밀고 사라질 때 되밀어, 끊겼다 붙는 동안 화면이 출썩였다.
    // 프레임은 위에서 아래로 그려지므로 자리는 **순서로** 잰다(감시 배지 오라클과 같은 식).
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after_setup(vec![layout_one_pane(), screen], &[], |view| {
        view.ended = Some("서버가 닫았다".into())
    });
    let msg_at = painted.iter().position(|t| t.contains("연결 종료"));
    let canvas_at = painted.iter().position(|t| t.contains("HELLO-ORACLE"));
    // 상태줄의 `⇕` 배지가 곧 "상태줄이 그려진 자리"다.
    // (종전에는 `시계` 배지로 짚었는데 §10-21ⓑ 로 그 배지가 사라졌다 — 표식만 바꾼다.)
    let status_at = painted.iter().position(|t| t.contains("⇕"));
    assert!(msg_at.is_some(), "끊김 메시지가 프레임에 없다: {painted:?}");
    assert!(
        painted_contains(&painted, "서버가 닫았다"),
        "사유가 프레임에 없다: {painted:?}"
    );
    assert!(canvas_at.is_some() && status_at.is_some(), "캔버스/상태줄이 없다: {painted:?}");
    assert!(
        msg_at > canvas_at,
        "메시지가 캔버스보다 먼저 그려졌다(=위쪽 자리 그대로다): {painted:?}"
    );
    assert!(
        msg_at > status_at,
        "메시지가 상태줄보다 먼저 그려졌다 — 자리는 상태줄 **아래**다: {painted:?}"
    );
}

#[test]
fn clicking_the_disconnect_message_opens_the_notice_history() {
    // 그 줄이 감싸는 클릭 대상이 실제로 알림 이력을 연다(자리는 위 오라클이 잰다).
    // 지나간 메시지는 이 한 줄에 남지 않으니, 눌러 본 사람이 이력에 닿아야 한다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.ended = Some("서버가 닫았다".into());
    view.chrome_click(base::chrome::ClickTarget::Badge(
        base::Badge::Notices,
    ));
    assert_eq!(
        view.screens.top(),
        Some(base::screens::Screen::Notices),
        "알림 이력이 안 열렸다"
    );
}

#[test]
fn the_head_line_names_the_socket_like_the_tui() {
    // 머리줄은 TUI 와 같은 배치(맨 위 한 줄) — 어느 서버에 붙었는지가 화면에 있어야
    // 하고, 복사 결과가 붙는 자리이기도 하다(아래 요약 구역은 비면 안 그려진다).
    let painted = painted_after(vec![], &[]);
    assert!(
        painted_contains(&painted, "pytmux-gui · "),
        "머리줄(소켓)이 프레임에 없다: {painted:?}"
    );
}

#[test]
fn the_tab_chrome_and_status_line_are_painted() {
    let painted = painted_after(three_tabs(), &[]);
    // 네이티브 탭바(N1): `[+]`·`[x]` 가 + 버튼과 활성 탭 안 × 로 바뀌었다.
    for needle in ["하나", "둘", "셋", "+", "×"] {
        assert!(painted_contains(&painted, needle), "{needle} 가 탭바에 없다: {painted:?}");
    }
    // ★ §10-21ⓑ — `서버`·`시계`·`달력` 배지는 **없어야 한다**(오른쪽 글자와 중복이라는
    //   제보). 부정 단언만 두면 상태줄이 통째로 안 그려져도 통과하므로, **그 동작이 옮겨
    //   간 자리**를 함께 양성으로 잰다.
    for gone in ["서버", "시계", "달력"] {
        assert!(
            !painted_contains(&painted, gone),
            "{gone} 배지가 아직 상태줄에 있다(ⓑ): {painted:?}"
        );
    }
    // 오른쪽 구간 셋이 그 동작을 갖는다(ⓑ2 로 라이브 확인) — 시각·날짜는 형식 문자열이
    // 만든다. `%H:%M` 은 시각이라 글자를 못 박으므로 `:` 를 낀 구간이 있는지로 본다.
    assert!(
        painted.iter().any(|t| t.contains("2026-")),
        "오른쪽 날짜 구간(달력 입구)이 없다: {painted:?}"
    );
    assert!(
        painted.iter().any(|t| t.len() == 5 && t.contains(':')),
        "오른쪽 시각 구간(시계 입구)이 없다: {painted:?}"
    );
}

// ── ⓑ2 — 안 먹는 것을 눌리는 것처럼 그리지 않는다 ────────────────────────────
//
// 제보는 "hover 효과만 나고 눌러도 아무 일이 없다"였고, 라이브에서 그 증상을 **정확히**
// 만드는 것은 `set mouse off` 였다(기능 자체는 멀쩡하다 — 제보자가 쓴 릴리스 이진에서도
// 시계·달력이 떴다). 즉 결함은 클릭이 아니라 **강조**다.

#[test]
fn nothing_is_highlighted_when_the_mouse_is_off() {
    // 진리표 넷 — **양성이 먼저**다(마우스가 켜져 있고 그 자리에 있으면 강조가 뜬다).
    // 그것이 없으면 아래 부정 단언은 "늘 false" 로도 통과한다.
    assert!(SessionView::hover_shown(true, true), "켜져 있으면 강조가 떠야 한다");
    assert!(
        !SessionView::hover_shown(false, true),
        "마우스를 껐는데 강조가 뜬다 — 눌러도 아무 일이 없는 것을 눌리는 것처럼 그린다(ⓑ2)"
    );
    assert!(!SessionView::hover_shown(true, false));
    assert!(!SessionView::hover_shown(false, false));
}

#[test]
fn the_click_and_the_highlight_agree_about_the_mouse_setting() {
    // 배선 확인 — 판정을 순수 함수로 빼도 **부르는 자리가 빠지면** 뜻이 없다.
    // 클릭이 거절되는 그 설정에서 강조도 함께 죽는지 본다(둘이 갈리면 그것이 ⓑ2 다).
    let (mut view, _tx, _sent) = harness();
    view.config.mouse = false;
    assert!(!view.chrome_hovered(0));
    assert!(!view.panel_hovered(0));
    assert!(
        !view.chrome_click(base::chrome::ClickTarget::Badge(base::Badge::Clock)),
        "마우스를 끈 판에서 크롬 클릭이 먹었다"
    );
}

#[test]
fn the_popup_box_is_painted_over_the_canvas() {
    let popup: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24, "title": "sh"}],
        "popup": {"id": 99, "x": 10, "y": 5, "w": 40, "h": 10,
                  "cx": 11, "cy": 6, "cw": 38, "ch": 8, "title": "ORACLE-POP"}
    }))
    .unwrap();
    let painted = painted_after(vec![popup], &[]);
    assert!(
        painted_contains(&painted, "ORACLE-POP"),
        "팝업 제목이 프레임에 없다: {painted:?}"
    );
}

#[test]
fn a_screen_floats_over_the_canvas_in_the_frame() {
    // N2: 화면(팔레트)은 캔버스를 **대체하지 않고 위에 뜬다** — 캔버스·제목·힌트·목록이
    // 한 프레임에 같이 있고, 판(제목)이 캔버스보다 **나중에**(=위에) 그려져야 한다.
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after(
        vec![layout_one_pane(), screen],
        &[(Key::Char('b'), Mods::CTRL), (Key::Char(':'), Mods::NONE)],
    );
    let canvas_at = painted.iter().position(|t| t.contains("HELLO-ORACLE"));
    let title_at = painted.iter().position(|t| t.contains("명령"));
    assert!(canvas_at.is_some(), "캔버스가 팝업 밑에서 사라졌다: {painted:?}");
    assert!(title_at.is_some(), "팔레트 제목이 없다: {painted:?}");
    assert!(
        title_at > canvas_at,
        "판이 캔버스보다 먼저 그려졌다(위아래가 뒤집혔다): {painted:?}"
    );
    // 내용도 판과 함께 떠야 한다 — 틀만 있고 목록 배선이 빠지는 결함을 잡는 양성 단언.
    assert!(
        painted_contains(&painted, "> _"),
        "팔레트 필터 줄이 없다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "split-window"),
        "팔레트 목록이 없다: {painted:?}"
    );
}

// ── 패널 테두리를 **실제 선**으로(2026-07-31 사용자 지시) ──────────────────────

/// 테두리 사각형 하나짜리 배치와, 그것을 합성한 캔버스.
fn framed_canvas() -> (proto::canvas::Canvas, proto::message::Layout) {
    use proto::canvas::Canvas;
    use proto::message::{Layout, PaneLayout};
    let mut canvas = Canvas::new(10, 5);
    canvas.draw_box(0, 0, 10, 5, CellStyle::default());
    // 패널 **안**에 앱이 그린 선문자 하나 — 이건 우리 크롬이 아니다.
    canvas.put_text(3, 2, "┌", CellStyle::default());
    let layout = Layout {
        cols: 10,
        rows: 5,
        panes: vec![PaneLayout {
            id: 1,
            x: 1,
            y: 1,
            w: 8,
            h: 3,
            boxrect: Some([0, 0, 10, 5]),
            ..Default::default()
        }],
        active: 1,
        ..Default::default()
    };
    (canvas, layout)
}

#[test]
fn the_pane_frame_becomes_real_line_segments() {
    // 양성 오라클: 네 모서리가 **뻗는 방향까지** 옳아야 한다. 비트를 잘못 옮기면 모서리가
    // 바깥으로 삐져나가거나 안쪽이 뚫려 보인다 — 눈으로는 한참 봐야 보이는 종류다.
    let (canvas, layout) = framed_canvas();
    let segs = SessionView::frame_segments(&canvas, Some(&layout));
    let at = |x: u16, y: u16| segs.iter().find(|s| s.x == x && s.y == y).map(|s| s.bits);
    assert_eq!(at(0, 0), Some(0b0101), "┌ 는 아래·오른쪽으로만 뻗는다");
    assert_eq!(at(9, 0), Some(0b0110), "┐ 는 아래·왼쪽");
    assert_eq!(at(0, 4), Some(0b1001), "└ 는 위·오른쪽");
    assert_eq!(at(9, 4), Some(0b1010), "┘ 는 위·왼쪽");
    assert_eq!(at(5, 0), Some(0b0011), "위 변은 좌우로");
    assert_eq!(at(0, 2), Some(0b1100), "왼 변은 상하로");
}

#[test]
fn a_box_character_inside_a_pane_is_left_alone() {
    // ★ 테두리를 네이티브로 그리는 것이지 **남의 화면을 고쳐 그리는 것이 아니다.**
    //   캔버스를 통째로 훑으면 패널 안 앱(`htop` 등)이 그린 선문자까지 선으로 바뀐다.
    let (canvas, layout) = framed_canvas();
    let segs = SessionView::frame_segments(&canvas, Some(&layout));
    assert!(
        !segs.iter().any(|s| s.x == 3 && s.y == 2),
        "패널 안의 선문자를 크롬으로 잡았다"
    );
}

#[test]
fn the_cells_we_draw_as_lines_are_exactly_the_cells_we_blank() {
    // 한쪽만 바뀌면 선이 두 겹으로 보이거나(글자가 남음) 테두리가 통째로 사라진다.
    let (canvas, layout) = framed_canvas();
    let segs = SessionView::frame_segments(&canvas, Some(&layout));
    let cells = SessionView::frame_cells(&canvas, Some(&layout));
    assert!(!cells.is_empty(), "빈 목록은 통과가 아니라 고장이다");
    assert_eq!(cells.len(), segs.len());
    for seg in &segs {
        assert!(cells.contains(&(seg.x, seg.y)), "({},{}) 가 비우는 목록에 없다", seg.x, seg.y);
    }
}

#[test]
fn a_divider_cell_is_left_to_the_splitter_bar() {
    // 경계 칸에 선까지 그리면 잡는 자리가 두 겹으로 보인다 — 바가 자기 그림을 그린다.
    use proto::message::Divider;
    let (canvas, mut layout) = framed_canvas();
    layout.dividers = vec![Divider {
        split_id: 7,
        orient: "lr".into(),
        x: 0,
        y: 2,
        w: 1,
        h: 1,
        ..Default::default()
    }];
    let segs = SessionView::frame_segments(&canvas, Some(&layout));
    assert!(
        !segs.iter().any(|s| s.x == 0 && s.y == 2),
        "스플리터 바가 있는 칸에 테두리 선까지 그렸다"
    );
}

#[test]
fn without_a_layout_nothing_is_converted() {
    // 첫 프레임(배치 없음)에 크롬을 지어내면 없는 테두리가 잠깐 번쩍인다.
    let (canvas, _) = framed_canvas();
    assert!(SessionView::frame_segments(&canvas, None).is_empty());
}

// ── 판이 서는 자리 — 정본 앵커(`Screen::anchor`)를 뷰가 실제로 따르나 ────────────
//
// core 가 앵커를 들고 있어도 뷰가 그것을 **안 보면** 아무 일도 안 일어난다. 적합성
// 테스트(`screen_anchor_conformance.rs`)는 core 의 표만 재므로, 그리는 자리는 여기서
// 잰다 — 이 저장소가 두 번 밟은 "값은 맞는데 붙이는 호출이 없다" 부류다.

/// ★ 이 오라클이 먼저다 — 세로 좌표가 안 살아 있으면 아래 배치 단언이 전부 공허하다.
#[test]
fn the_bounds_oracle_sees_vertical_positions() {
    let boxes = painted_boxes(vec![], &[]);
    assert!(!boxes.is_empty(), "그려진 글자가 없다 — 기록 장치가 죽었다");
    let ys: Vec<f32> = boxes.iter().map(|(_, y)| *y).collect();
    assert!(
        ys.iter().any(|y| *y > 0.0),
        "세로 좌표가 전부 0이다 — 이 오라클로는 배치를 못 잰다: {boxes:?}"
    );
}

/// `esc :` 프롬프트는 **바닥**이다(정본 `PromptScreen { align: center bottom }`).
///
/// 손과 눈이 방금 `:` 를 친 화면 아래에 있는데 판이 가운데 뜨면 시선이 한 번 튄다 —
/// 사용자가 두 달을 쓰며 굳힌 자리다(지시 2026-08-01).
#[test]
fn the_command_prompt_sits_at_the_bottom() {
    let boxes = painted_boxes(
        vec![layout_one_pane()],
        &[(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)],
    );
    let y = painted_y(&boxes, ">").unwrap_or_else(|| panic!("입력 줄이 안 그려졌다: {boxes:?}"));
    assert!(
        y > 300.0,
        "프롬프트가 화면 위쪽(y={y})에 떴다 — 정본은 바닥이다: {boxes:?}"
    );
}

/// 읽는 판(버전)은 **위**다(정본 `InfoScreen { align: center top }`).
///
/// 긴 글이라 첫 줄이 늘 같은 자리라야 훑을 수 있다. 프롬프트(바닥)와 **같은 프레임 크기
/// 에서** 재야 뜻이 있다 — 그래서 두 y 를 서로 비교한다(절대 좌표가 아니라 **차이**가
/// 배치를 말한다).
#[test]
fn a_reading_screen_starts_above_a_typing_screen() {
    let version = painted_boxes(
        vec![layout_one_pane()],
        &[(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)],
    );
    let prompt_y = painted_y(&version, ">").unwrap_or_else(|| panic!("입력 줄이 없다: {version:?}"));
    // 읽는 판은 `esc ?`(키 도움말 — 정본이 InfoScreen 으로 띄우는 그 부류)로 연다.
    let keys = painted_boxes(
        vec![layout_one_pane()],
        &[(Key::Escape, Mods::NONE), (Key::Char('?'), Mods::NONE)],
    );
    let keys_y = painted_y(&keys, t("키 도움말"))
        .unwrap_or_else(|| panic!("키 도움말 제목이 안 그려졌다: {keys:?}"));
    assert!(
        keys_y < prompt_y,
        "읽는 판(y={keys_y})이 치는 판(y={prompt_y})보다 아래에 떴다"
    );
}

/// `esc :` 판의 **입력 줄이 목록 아래**다 — 곧 화면 맨 밑이다.
///
/// 사용자 지시(2026-08-01): "터미널에서 프롬프트가 보통 화면 하단에 있어 시선이 하단에
/// 가 있기 때문"에 정본은 입력 박스를 바닥에 뒀다. 우리 팔레트는 정본의 `:` 입력과
/// 명령 목록 **둘의 역할을 겸하므로** 판 안에서 같은 기하를 만들어야 한다.
#[test]
fn the_command_input_sits_below_the_list() {
    let boxes = painted_boxes(
        vec![layout_one_pane()],
        &[(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)],
    );
    let input_y = painted_y(&boxes, ">").unwrap_or_else(|| panic!("입력 줄이 없다: {boxes:?}"));
    let list_y = painted_y(&boxes, "split-window")
        .unwrap_or_else(|| panic!("명령 목록이 없다: {boxes:?}"));
    assert!(
        input_y > list_y,
        "입력(y={input_y})이 목록(y={list_y})보다 위에 있다 — 프롬프트는 아래다: {boxes:?}"
    );
}

// ── 플러그인 표면(Tier A) — 서버가 준 기여가 실제로 화면에 뜨나 ──────────────────
//
// 설계 = docs/internal/PLUGIN_COMPAT_TEXTUAL_GUI_2026-08-01.md §4.1.
// 상태에 값이 들어오는 것만 재면 **그리는 호출을 지워도 통과한다**(이 저장소가 두 번
// 밟은 공허 통과) — 그래서 그려진 글자로 잰다.

fn status_with_plugin_surface() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "하나", "active": true}],
        "plugin_surface": {
            "commands": [{"name": "mdir", "desc": "파일 관리자", "cat": "설정/기타"}],
            "noarg": ["mdir"],
            "menu_items": [{"key": "mdir", "label": "파일 관리자 ▤"}],
            "settings": [],
            "setting_cats": []
        }
    }))
    .unwrap()
}

#[test]
fn plugin_commands_show_up_in_the_palette() {
    // 이 명령은 코어 표(`base::PALETTE`)에 **없다** — 서버가 준 것이라야 뜬다.
    assert!(
        !base::PALETTE.iter().any(|e| e.name == "mdir"),
        "코어 표에 mdir 이 있으면 이 오라클은 아무것도 안 잰다"
    );
    // 이름을 쳐서 좁힌다 — 코어 명령이 87개라 안 좁히면 판 높이 예산에 밀려 안 보인다
    // (그건 이 오라클이 재려는 것이 아니다).
    let painted = painted_after(
        vec![layout_one_pane(), status_with_plugin_surface()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Char(':'), Mods::NONE),
            (Key::Char('m'), Mods::NONE),
            (Key::Char('d'), Mods::NONE),
            (Key::Char('i'), Mods::NONE),
            (Key::Char('r'), Mods::NONE),
        ],
    );
    assert!(
        painted_contains(&painted, "mdir"),
        "플러그인 명령이 팔레트에 안 떴다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "파일 관리자"),
        "설명이 서버가 준 글과 다르다: {painted:?}"
    );
}

/// 서버가 준 메뉴 줄이 **메뉴 계층 안에** 뜨나(설계 P2).
///
/// 종전에는 이 두 줄이 정적 표에 손으로 적혀 있었다 — 그러면 서버가 그 플러그인을 안
/// 실어도 화면에 남고(delete-to-disable 이 우리 쪽에서만 거짓), 새 플러그인이 낸 줄은
/// 영영 안 뜬다.
#[test]
fn plugin_menu_rows_come_from_the_server() {
    let painted = painted_after(
        vec![layout_one_pane(), status_with_plugin_surface()],
        // 메뉴는 `prefix Enter` 다(esc 모드의 Enter 는 다른 표다 — `BINDINGS`).
        &[(Key::Char('b'), Mods::CTRL), (Key::Enter, Mods::NONE)],
    );
    assert!(
        painted_contains(&painted, "플러그인  ›"),
        "최상위에 플러그인 그룹이 없다: {painted:?}"
    );

    // 들어가면 **서버가 준 문구**가 보인다(정적 표에는 없는 글이다).
    let inside = painted_after(
        vec![layout_one_pane(), status_with_plugin_surface()],
        &[
            (Key::Char('b'), Mods::CTRL),
            (Key::Enter, Mods::NONE),
            // 패널▸ 레이아웃▸ 탭▸ **플러그인▸** — 정본이 끼우는 자리가 `탭` 다음이다.
            (Key::Down, Mods::NONE),
            (Key::Down, Mods::NONE),
            (Key::Down, Mods::NONE),
            (Key::Right, Mods::NONE),
        ],
    );
    assert!(
        painted_contains(&inside, "파일 관리자 ▤"),
        "플러그인 서브메뉴에 서버가 준 줄이 없다: {inside:?}"
    );
}

/// 기여가 없으면 **그룹 자체가 없다**(delete-to-disable 이 화면에서도 먹는다).
#[test]
fn no_plugin_contributions_means_no_plugin_group() {
    let painted = painted_after(
        vec![layout_one_pane()],
        &[(Key::Char('b'), Mods::CTRL), (Key::Enter, Mods::NONE)],
    );
    assert!(
        !painted_contains(&painted, "플러그인  ›"),
        "기여가 하나도 없는데 플러그인 그룹이 떴다: {painted:?}"
    );
    // 그리고 다른 그룹은 그대로다 — 이 오라클이 "메뉴가 안 열렸다"로 헛통과하지 않게.
    assert!(painted_contains(&painted, "탭  ›"), "메뉴가 안 열렸다: {painted:?}");
}

/// 서버가 준 설정 줄과 **그 분류 탭**이 설정 화면에 서나(설계 P2).
#[test]
fn plugin_settings_show_up_with_their_own_sidebar_tab() {
    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "하나", "active": true}],
        "plugin_surface": {
            "commands": [],
            "noarg": [],
            "menu_items": [],
            "settings": [{"key": "claude-rules", "cat": "Claude", "type": "link"}],
            "setting_cats": ["Claude"]
        }
    }))
    .unwrap();
    // 설정 화면은 팔레트로 연다(전용 키가 없다). 그 뒤 **마지막 분류**로 간다 —
    // 코어 다섯 뒤가 플러그인 분류다.
    let mut keys = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("settings".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend(std::iter::repeat_n((Key::Tab, Mods::NONE), 5));
    let painted = painted_after(vec![layout_one_pane(), status], &keys);
    assert!(
        painted_contains(&painted, "Claude"),
        "플러그인 분류 탭이 사이드바에 없다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "Claude 시작 규칙…"),
        "플러그인 설정 줄이 화면에 없다: {painted:?}"
    );
}

/// 우리가 **네이티브로 든 이름**은 팔레트에 두 번 서지 않는다(P1 이 두 줄로 만들었다).
#[test]
fn a_natively_handled_plugin_command_is_not_listed_twice() {
    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "하나", "active": true}],
        "plugin_surface": {
            "commands": [{"name": "clock-mode", "desc": "서버가 준 설명", "cat": "설정/기타"}],
            "noarg": ["clock-mode"], "menu_items": [], "settings": [], "setting_cats": []
        }
    }))
    .unwrap();
    let painted = painted_after(
        vec![layout_one_pane(), status],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Char(':'), Mods::NONE),
            (Key::Char('c'), Mods::NONE),
            (Key::Char('l'), Mods::NONE),
            (Key::Char('o'), Mods::NONE),
            (Key::Char('c'), Mods::NONE),
            (Key::Char('k'), Mods::NONE),
        ],
    );
    // ⚠ 한 줄이 **세 칸**으로 갈려 그려진다(§10-21ⓞ) — 이름 칸은 그 이름 하나뿐이다.
    //   종전처럼 `"clock-mode "` 로 시작하는 **한 덩이**를 찾으면 이제 아무것도 안 걸려
    //   단언이 뜻을 잃는다(칸을 나눈 CL 에서 여기가 먼저 울었다).
    let rows = painted.iter().filter(|piece| piece.as_str() == "clock-mode").count();
    assert_eq!(rows, 1, "같은 이름이 팔레트에 두 줄 섰다: {painted:?}");
}

#[test]
fn a_frame_without_the_surface_keeps_the_previous_one() {
    // 델타에는 이 키가 없다. 그때 목록을 지우면 플러그인 기여가 **매 틱 깜빡인다**.
    let mut state = SessionState::new();
    state.apply(status_with_plugin_surface());
    assert_eq!(state.plugin_surface().commands.len(), 1);
    let delta: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status", "windows": [{"index": 0, "name": "하나", "active": true}]
    }))
    .unwrap();
    state.apply(delta);
    assert_eq!(
        state.plugin_surface().commands.len(),
        1,
        "델타 한 장에 플러그인 기여가 사라졌다"
    );
}

// ── G1 측정 — 이 **뷰**가 액션마다 실제로 무엇을 하나 ────────────────────────────
//
// 왜 이 오라클이 필요한가(`CLIENT_PRODUCT_SET_2026-08-01.md` §4-G1):
//
// 패리티 래칫의 칸은 2026-08-01 에 **GUI 하나**가 됐는데, 값은 *정본 대 Rust 쪽 아무 뷰*
// 시절 것을 물려받았다 — 즉 지워진 TUI 에만 있던 `Done` 이 섞여 있을 수 있고, 그 상태로는
// 표의 숫자가 "GUI 가 다 된다"는 **거짓말**이 된다. 표를 눈으로 다시 훑는 것은 189줄짜리
// 손번역이라 같은 부류의 부채를 하나 더 만든다.
//
// 그래서 기계로 잰다: **액션 전수**(`base::keymap::all_actions`)를 이 뷰에 먹이고, 뷰가
// 아무 일도 안 하는 것을 센다. `apply_action` 은 화면을 열거나 명령을 밀면 `true` 이고,
// 둘 다 아니면 `false` 다 — 그 `false` 가 곧 "이 클라에서는 없는 기능"이다.
//
// 예외 목록이 이 측정의 **결과**다. 목록이 이유와 함께 정확·정렬이라야 하므로, 새로 죽는
// 액션이 생기면 같은 CL 에서 여기 적히거나 고쳐진다.

/// 뷰가 **아무 일도 안 하는** 액션과 그 이유.
///
/// 셋 다 "GUI 가 못 한다"가 아니라 **다른 입구로만 뜻이 생긴다**는 뜻이다. 그래도 목록에
/// 두는 이유는 하나다: 여기 없으면 조용히 죽은 액션이 늘어난다.
static NO_OP_ACTIONS: &[(&str, &str)] = &[
    ("EnterScroll", "액션이 아니라 **모드 전이**로 처리된다(`KeyOutcome::ModeChanged`) — \
     서버 명령으로 옮기면 뷰마다 다르게 해석할 여지가 생긴다(`keys.rs` 주석)"),
    ("ToggleExpand", "블록 목록 데모 뷰의 것 — 세션 뷰에는 펼칠 목록이 없다"),
];

#[test]
fn every_action_does_something_in_this_view() {
    let mut dead: Vec<String> = Vec::new();
    for action in base::keymap::all_actions() {
        // 탭 셋짜리 세션으로 세운다 — `SelectTab(3)` 처럼 **대상이 있어야** 명령이 나는
        // 액션을 하네스 빈곤 때문에 "죽었다"로 세지 않으려는 것이다.
        let (mut view, tx, _sent) = harness();
        for msg in three_tabs() {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        if !view.apply_action_for_test(action) {
            // 이름만 견준다 — `SelectTab(3)` 처럼 값을 든 액션은 값이 뜻이 아니다.
            dead.push(format!("{action:?}").split('(').next().unwrap_or("?").to_owned());
        }
    }
    dead.sort();
    dead.dedup();
    let known: Vec<String> =
        NO_OP_ACTIONS.iter().map(|(name, _)| (*name).to_owned()).collect();
    let mut sorted = known.clone();
    sorted.sort();
    assert_eq!(known, sorted, "NO_OP_ACTIONS 는 이름순이라야 한다");
    assert_eq!(
        dead, sorted,
        "이 뷰가 아무 일도 안 하는 액션이 달라졌다.\n\
         늘었다면 그 액션은 **이 클라에서 없는 기능**이다 — 배선하거나, 왜 다른 입구로만\n\
         뜻이 생기는지를 NO_OP_ACTIONS 에 적을 것(패리티 표의 Done 도 함께 볼 것)."
    );
}

// ── 화면 축 측정(2026-08-02p) — 액션 축(G1)을 **화면**으로 넓힌다 ────────────────
//
// 패리티 표의 화면 칸은 17줄인데 그 값도 손번역이었다(`parity.rs` Item 주석의 *"설정
// 36·화면 17 축은 아직 같은 강도로 안 쟀다"*). 여기서 재는 것은 액션 축과 같은 질문의
// 화면판이다: **이 화면을 열 길이 있나.**
//
// 그리기는 안 잰다 — `render_screen_panel` 이 와일드카드 없는 `match` 라 변형이 늘면
// 컴파일러가 먼저 운다. 반면 "여는 길"은 아무도 안 지킨다: 화면 하나를 enum 에 남긴 채
// 그것을 여는 액션만 지우면 표의 수는 그대로이고 사용자는 그 화면을 영영 못 본다.

/// 액션으로는 못 여는 화면과 **무엇이 여는가**.
///
/// 전부 "GUI 에 없다"가 아니라 **서버 회신이나 다른 화면이 연다**는 뜻이다. 목록에
/// 두는 이유는 액션 축과 같다: 여기 없으면 조용히 못 여는 화면이 늘어난다.
/// ⚠ 이 셋은 **잰 값이다.** 처음에는 여덟을 적었는데(물음·확인·인자 화면 따위) 재
/// 보니 다섯은 액션이 이미 연다 — 손으로 적은 목록이 그만큼 틀렸다는 뜻이고, 이 축을
/// 기계로 재는 이유가 그것이다.
static NOT_OPENED_BY_AN_ACTION: &[(&str, &str)] = &[
    ("MergeRemote", "원격 탭 **목록이 와야** 고를 것이 있다(서버 회신이 연다)"),
    ("PluginView", "플러그인이 준 스펙(`plugin_screen`)이 연다"),
    ("ShellOutput", "`run-shell` 의 **결과가 온 뒤에** 열린다"),
];

#[test]
fn every_screen_has_a_way_to_open_it() {
    // 액션 전수를 먹여 **열린 화면의 집합**을 모은다(G1 과 같은 하네스·같은 세션).
    let mut opened: Vec<String> = Vec::new();
    for action in base::keymap::all_actions() {
        let (mut view, tx, _sent) = harness();
        for msg in three_tabs() {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        view.apply_action_for_test(action);
        if let Some(screen) = view.screens.top() {
            opened.push(format!("{screen:?}"));
        }
    }
    let mut unreachable: Vec<String> = base::screens::Screen::all()
        .iter()
        .map(|s| format!("{s:?}"))
        .filter(|name| !opened.contains(name))
        .collect();
    unreachable.sort();
    let mut known: Vec<String> =
        NOT_OPENED_BY_AN_ACTION.iter().map(|(n, _)| (*n).to_owned()).collect();
    known.sort();
    assert_eq!(
        known,
        NOT_OPENED_BY_AN_ACTION.iter().map(|(n, _)| (*n).to_owned()).collect::<Vec<_>>(),
        "NOT_OPENED_BY_AN_ACTION 은 이름순이라야 한다"
    );
    assert_eq!(
        unreachable, known,
        "액션으로 열리는 화면이 달라졌다.\n\
         늘었다면 그 화면은 **이 클라에서 열 길이 없다** — 액션을 배선하거나, 무엇이 여는지를\n\
         NOT_OPENED_BY_AN_ACTION 에 적을 것(패리티 표의 화면 칸도 함께 볼 것)."
    );
    // 빈 결과가 통과로 보이지 않게 — 아무 화면도 안 열렸다면 위 비교는 공허하다.
    assert!(opened.len() > 5, "액션이 연 화면이 너무 적다: {opened:?}");
}

// ── 플러그인 화면(Tier C · P4) — 스펙이 판이 되고, 고른 줄이 서버로 돌아간다 ────────

fn plugin_list_screen() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "p4changes", "kind": "list",
        "title": "Perforce submitted changelists",
        "hint": "(↑↓ 이동 · Enter 상세 · Esc 닫기)",
        "rows": [
            {"key": "68995", "label": "68995  플러그인 호환 P2", "cols": ["woojinkim", "08/01"]},
            {"key": "68997", "label": "68997  열린 항목 둘", "cols": ["woojinkim", "08/01"]}
        ],
        "selected": 0, "note": "", "keys": {"enter": "describe"}
    }))
    .unwrap()
}

#[test]
fn a_plugin_screen_spec_becomes_a_panel() {
    let painted = painted_after(vec![layout_one_pane(), plugin_list_screen()], &[]);
    assert!(
        painted_contains(&painted, "68995  플러그인 호환 P2"),
        "서버가 준 목록이 안 그려졌다: {painted:?}"
    );
    // 부가 칸도 그린다(정본 목록 화면과 같은 짜임).
    assert!(painted_contains(&painted, "woojinkim"), "부가 칸이 없다: {painted:?}");
}

/// 판의 **제목과 안내는 스펙의 것**이다 — 어느 판을 열었는지 화면이 말해야 한다.
///
/// P4~P6 동안 여기가 빠져 있어 `mdir`·`ncd`·`p4changes` 가 전부 `플러그인 화면` 이라는
/// 한 제목으로 떴다(`base::screens` 주석은 "뷰가 스펙으로 덮어 그린다"고 적어 뒀는데
/// 그 덮는 자리가 없었다 — 2026-08-02 라이브에서 드러났다).
#[test]
fn the_spec_names_the_panel_not_us() {
    let painted = painted_after(vec![layout_one_pane(), plugin_list_screen()], &[]);
    assert!(
        painted_contains(&painted, "Perforce submitted changelists"),
        "스펙 제목이 안 보인다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "Enter 상세"),
        "스펙 안내가 안 보인다: {painted:?}"
    );
    assert!(
        !painted_contains(&painted, "플러그인 화면"),
        "폴백 제목이 스펙을 덮었다: {painted:?}"
    );
}

#[test]
fn choosing_a_row_sends_back_its_key_not_its_position() {
    // 자리(번호)만 보내면 목록이 바뀔 때 엉뚱한 줄이 열린다 — 그 줄의 **뜻**을 보낸다.
    let sent = sent_after(
        vec![layout_one_pane(), plugin_list_screen()],
        &[(Key::Down, Mods::NONE), (Key::Enter, Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("plugin_action 이 안 나갔다: {frames:?}"));
    assert_eq!(action["id"], "p4changes");
    // 액션 이름의 칸은 `do` 다 — `action` 은 명령 디스패처의 것이다.
    assert_eq!(action["do"], "describe");
    assert_eq!(action["input"], "68997", "고른 줄의 key 가 아니다: {action:?}");
}

#[test]
fn a_plugin_command_asks_the_server_for_a_screen() {
    // P1~P2 에서는 여기서 "화면이 없다" 알림만 남았다. 이제 서버에 **화면을 묻는다**.
    let sent = sent_after(
        vec![layout_one_pane(), status_with_plugin_surface()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Char(':'), Mods::NONE),
            (Key::Char('m'), Mods::NONE),
            (Key::Char('d'), Mods::NONE),
            (Key::Char('i'), Mods::NONE),
            (Key::Char('r'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let open = frames
        .iter()
        .find(|f| f["action"] == "plugin_open")
        .unwrap_or_else(|| panic!("plugin_open 이 안 나갔다: {frames:?}"));
    assert_eq!(open["name"], "mdir");
}

#[test]
fn esc_from_the_detail_goes_back_to_the_list_without_asking_the_server() {
    // 상세에서 한 판 물러나면 **방금 보던 목록**이 그대로 있어야 한다(서버에 다시 물으면
    // p4 를 또 부르고 그 사이 목록이 달라진다).
    let detail: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "p4changes", "kind": "text",
        "title": "CL 68995", "hint": "(↑↓ 스크롤 · Esc 목록으로)",
        "text": "Change 68995 by woojinkim\n\t플러그인 호환 P2", "note": "", "keys": {}
    }))
    .unwrap();
    let painted = painted_after(
        vec![layout_one_pane(), plugin_list_screen(), detail],
        &[(Key::Escape, Mods::NONE)],
    );
    assert!(
        painted_contains(&painted, "68995  플러그인 호환 P2"),
        "Esc 뒤 목록으로 안 돌아왔다: {painted:?}"
    );
}

#[test]
fn a_kind_we_cannot_draw_says_so_instead_of_showing_an_empty_panel() {
    // 조용한 누락이 이 저장소의 상습 결함이다(설계 §8-5).
    //
    // ⚠ 2026-08-01 P5 에서 이 오라클의 표본을 바꿨다: 종전에는 `form` 을 썼는데 P5 가
    //   그 모양을 그리게 되면서 **오라클이 스스로 낡았다**(적색으로 그 사실을 알렸다).
    //   지금은 스펙에 없는 모양(`tree`)으로 잰다 — 재는 것은 특정 모양이 아니라
    //   "모르는 모양을 말하는가"다.
    let unknown: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "tree",
        "title": "트리", "hint": "", "rows": [], "text": "", "note": "", "keys": {}
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), unknown], &[]);
    assert!(
        painted.iter().any(|line| line.contains("아직 못 그립니다")),
        "못 그리는 모양인데 아무 말도 없다: {painted:?}"
    );
}

// ── P5 — 나머지 모양 넷과 **스펙이 정하는 글자 키** ────────────────────────────────

fn ncd_list_screen() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "ncd", "kind": "list",
        "title": "디렉터리 — /home/me", "hint": "(↑↓ · Enter 들어가기 · c 여기로 cd)",
        "rows": [
            {"key": "/home", "label": "..", "cols": []},
            {"key": "/home/me/src", "label": "src", "cols": []}
        ],
        "selected": 0, "note": "", "keys": {"enter": "into", "c": "cd"}
    }))
    .unwrap()
}

#[test]
fn a_letter_key_the_spec_declares_becomes_that_plugin_action() {
    // 스펙이 자기 키를 정한다 — 목록 화면에서 글자는 원래 "닫기"라, 이 배선이 없으면
    // 우리 키가 그 판을 먼저 닫는다.
    let sent = sent_after(
        vec![layout_one_pane(), ncd_list_screen()],
        &[(Key::Down, Mods::NONE), (Key::Char('c'), Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("글자 키가 액션이 안 됐다: {frames:?}"));
    assert_eq!(action["do"], "cd");
    assert_eq!(action["input"], "/home/me/src", "고른 줄의 뜻이 안 실렸다: {action:?}");
}

#[test]
fn a_letter_the_spec_does_not_declare_still_closes_the_panel() {
    // 표에 없는 글자까지 먹으면 판을 **닫을 길이 없어진다**.
    let painted = painted_after(
        vec![layout_one_pane(), ncd_list_screen()],
        &[(Key::Char('q'), Mods::NONE)],
    );
    assert!(
        !painted_contains(&painted, "디렉터리 — /home/me"),
        "표에 없는 글자인데 판이 안 닫혔다: {painted:?}"
    );
}

#[test]
fn a_table_spec_draws_its_columns() {
    let table: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "x", "kind": "table", "title": "표", "hint": "",
        "rows": [{"key": "a", "label": "이름", "cols": ["10KB", "2026/08/01"]}],
        "text": "", "note": "", "selected": 0, "keys": {}
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), table], &[]);
    assert!(painted_contains(&painted, "이름"), "{painted:?}");
    assert!(painted_contains(&painted, "10KB"), "칸이 안 그려졌다: {painted:?}");
}

/// 줄의 **칸**은 우리 로케일로 그린다 — 이름은 그대로 둔다(2026-08-02p).
///
/// `mdir` 을 카탈로그로 옮기고 나서도 여기가 빠져 있으면 영어 사용자에게 `<상위>` 가
/// 한국어로 뜬다: 서버는 **자기** 로케일로 스펙을 짓기 때문이다(`title`·`hint`·`note`
/// 만 `say_*` 를 거치고 있었다). 배선을 되돌리는 변이(`say_cols()` → `cols`)를 이
/// 오라클이 잡는다.
#[test]
fn a_rows_columns_are_drawn_in_our_locale_but_its_name_is_not() {
    let table: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "table",
        "title": "표", "hint": "",
        // 이름이 하필 카탈로그의 말과 같아도 그대로 그린다 — 그런 이름의 파일이 있다.
        "rows": [{"key": "..", "label": "빈 디렉터리입니다", "cols": ["<상위>"]}],
        "text": "", "note": "", "selected": 0, "keys": {}
    }))
    .unwrap();
    let painted = base::i18n::with_locale("en", || {
        painted_after(vec![layout_one_pane(), table], &[])
    });
    assert!(
        painted_contains(&painted, "<UP>"),
        "칸이 서버 로케일 그대로 그려졌다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "빈 디렉터리입니다"),
        "이름을 번역했다 — 그건 자료다: {painted:?}"
    );
}

#[test]
fn a_prompt_spec_uses_the_native_ask_and_sends_the_typed_answer() {
    // 물음은 이 클라가 이미 잘하는 일이다 — 플러그인이 물었다고 화면을 한 벌 더 만들면
    // 되돌릴 수 없는 것 앞의 규칙이 두 곳에 생긴다.
    let ask: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "prompt",
        "title": "새 이름", "hint": "", "rows": [], "text": "새 이름:", "note": "",
        "selected": 0, "keys": {"enter": "rename"}
    }))
    .unwrap();
    let sent = sent_after(
        vec![layout_one_pane(), ask],
        &[
            (Key::Char('h'), Mods::NONE),
            (Key::Char('i'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("답이 안 돌아갔다: {frames:?}"));
    assert_eq!(action["do"], "rename");
    assert_eq!(action["input"], "hi");
}

// ── P3 — 오버레이는 클라만 아는 사실이고, 그림은 서버가 준다 ────────────────────

/// 달력이 켜진 한 판 — 서버가 그림 대신 **누를 자리와 키 표**까지 실어 준 프레임.
fn calendar_cells() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay", "dim": [1],
        "runs": [{"x": 10, "y": 1, "text": "‹ 2026-08 ›", "style": {"bo": 1},
                  "theme": {"f": "success"}}],
        "zones": [{"x": 10, "y": 1, "w": 2, "h": 1, "pane": 1,
                   "name": "calendar", "do": "prev"},
                  {"x": 19, "y": 1, "w": 2, "h": 1, "pane": 1,
                   "name": "calendar", "do": "next"}],
        "keys": [{"key": "left", "pane": 1, "name": "calendar", "do": "prev"},
                 {"key": "home", "pane": 1, "name": "calendar", "do": "today"}]
    }))
    .unwrap()
}

#[test]
fn clicking_the_arrow_sends_back_the_name_the_server_gave_us() {
    // 화살표를 그려 놓고 클릭이 안 먹으면 그 화살표가 **거짓말**이 된다. 우리는 뜻을
    // 모른 채 이름만 되돌려 보낸다(설계 §4.4).
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(calendar_cells()))).unwrap();
    view.pump_headless();
    view.handle_mouse_down((19, 1), false);
    view.pump_headless();
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    let act = frames
        .iter()
        .find(|f| f["action"] == "plugin_overlay_action")
        .unwrap_or_else(|| panic!("클릭이 안 올라갔다: {frames:?}"));
    assert_eq!(act["name"], "calendar");
    assert_eq!(act["do"], "next");
    assert_eq!(act["pane"], 1);
}

#[test]
fn the_overlay_takes_the_keys_the_spec_declared_and_the_pane_gets_nothing() {
    // 패널이 이미 달력에 덮여 있으니 이 키를 가져가도 셸 입력을 가리지 않는다
    // (정본 `client_overlay_key` 와 같은 규칙). **표에 있는 키만** 가져간다 —
    // 안 그러면 오버레이가 떠 있는 동안 셸이 먹통이 된다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(calendar_cells()))).unwrap();
    view.pump_headless();
    for (key, mods) in [
        (Key::Left, Mods::NONE),
        (Key::Home, Mods::NONE),
        (Key::Right, Mods::NONE),
    ] {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    let out = sent.lock().unwrap().clone();
    let frames: Vec<serde_json::Value> = out.iter().map(|o| o.to_frame()).collect();
    let acts: Vec<&str> = frames
        .iter()
        .filter(|f| f["action"] == "plugin_overlay_action")
        .map(|f| f["do"].as_str().unwrap())
        .collect();
    assert_eq!(acts, ["prev", "today"], "스펙이 정한 키를 안 가져갔다: {frames:?}");
    // 표에 없는 →(right)는 그대로 패널로 간다.
    assert!(
        out.iter().any(|o| matches!(o, Outgoing::Input(_))),
        "표에 없는 키까지 삼켰다: {out:?}"
    );
}

#[test]
fn toggling_the_calendar_tells_the_server_and_closes_the_clock_there() {
    // 한 패널엔 오버레이 하나(정본 규칙). 밀려난 시계의 **끔까지** 올려야 서버가 두
    // 그림을 겹쳐 보내지 않는다.
    // 달력에는 기본 키가 없다(팔레트·메뉴의 `calendar-mode`) — 그 두 길이 결국 부르는
    // 액션을 그대로 태운다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.apply_action(base::Action::ToggleClock);
    view.apply_action(base::Action::ToggleCalendar);
    view.pump_headless();
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    let overlays: Vec<(&str, bool)> = frames
        .iter()
        .filter(|f| f["action"] == "plugin_overlay")
        .map(|f| (f["name"].as_str().unwrap(), f["on"].as_bool().unwrap()))
        .collect();
    assert_eq!(
        overlays,
        [("clock", true), ("clock", false), ("calendar", true)],
        "달력을 켜며 시계를 안 껐다: {frames:?}"
    );
}

#[test]
fn toggling_the_clock_tells_the_server_which_pane() {
    // 시계를 서버가 그리려면 **어느 패널에 켰나**를 들어야 한다(설계 §4.4). 그 사실을
    // 안 올리면 서버는 아무것도 안 그리고, 화면에서는 "키가 안 먹었다"로 보인다.
    let sent = sent_after(
        vec![layout_one_pane()],
        &[(Key::Char('b'), Mods::CTRL), (Key::Char('t'), Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let on = frames
        .iter()
        .find(|f| f["action"] == "plugin_overlay")
        .unwrap_or_else(|| panic!("오버레이 사실이 안 올라갔다: {frames:?}"));
    assert_eq!(on["name"], "clock");
    assert_eq!(on["on"], true, "켰는데 껐다고 보냈다: {on:?}");

    // 한 번 더 누르면 **껐다고** 보낸다 — 안 보내면 서버가 영영 그린다.
    let sent = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Char('b'), Mods::CTRL),
            (Key::Char('t'), Mods::NONE),
            (Key::Char('b'), Mods::CTRL),
            (Key::Char('t'), Mods::NONE),
        ],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let offs: Vec<_> = frames
        .iter()
        .filter(|f| f["action"] == "plugin_overlay" && f["on"] == false)
        .collect();
    assert_eq!(offs.len(), 1, "끈 사실이 안 올라갔다: {frames:?}");
}

// ── P6 — 스펙이 물음 문구와 커서 자리를 정한다 ──────────────────────────────────

fn mdir_table_screen(selected: usize) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "table",
        "title": "파일 관리자 — /tmp/x", "hint": "(Enter 열기)",
        "rows": [
            {"key": "/tmp", "label": "   ..", "cols": ["<상위>"]},
            {"key": "/tmp/x/sub", "label": "  sub/", "cols": ["<DIR>", "2026/08/02 01:00"]},
            {"key": "/tmp/x/a.txt", "label": "  a.txt", "cols": ["9B", "2026/08/02 01:00"]}
        ],
        "text": "", "note": "", "selected": selected,
        "keys": {"enter": "into", "d": "delete", "t": "tag"}
    }))
    .unwrap()
}

#[test]
fn a_plugin_ask_shows_the_question_the_plugin_wrote() {
    // 되돌릴 수 없는 것 앞에서 "플러그인이 물었다:" 한 줄만 보이면, 사람은 **무엇이
    // 사라지는지 모른 채** 누른다. 문구의 주인은 스펙이다(`title` → 물음 · `note` → 상세).
    let ask: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "confirm",
        "title": "2개를 지웁니다 — 되돌릴 수 없습니다", "hint": "", "rows": [],
        "text": "", "note": "a.txt, b.txt", "selected": 0, "keys": {"enter": "apply"}
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), ask], &[]);
    assert!(
        painted_contains(&painted, "되돌릴 수 없습니다"),
        "플러그인이 쓴 물음이 안 보인다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "a.txt, b.txt"),
        "무엇이 사라지는지가 안 보인다: {painted:?}"
    );
    assert!(
        !painted_contains(&painted, "플러그인이 물었다"),
        "폴백 문구가 플러그인의 물음을 덮었다: {painted:?}"
    );
}

#[test]
fn a_plugin_screen_puts_the_cursor_where_the_spec_says() {
    // 목록을 갈아 끼우는 것은 늘 사용자의 손짓에 대한 답이다(디렉터리 이동·태그) —
    // 어디에 커서를 놓아야 하는지는 **만든 쪽**이 알고, 그 칸이 `selected` 다.
    // 이 배선이 없던 동안 그 칸은 아무도 안 읽는 죽은 칸이었다.
    let sent = sent_after(
        vec![layout_one_pane(), mdir_table_screen(2)],
        &[(Key::Char('t'), Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("액션이 안 나갔다: {frames:?}"));
    assert_eq!(
        action["input"], "/tmp/x/a.txt",
        "스펙이 고른 줄이 아니라 다른 줄이 실렸다: {action:?}"
    );
    assert_eq!(action["row"], 2, "{action:?}");
}

#[test]
fn a_detail_screen_does_not_steal_the_place_you_were_at() {
    // 글 화면(상세)은 고르는 화면이 아니다 — 거기서 커서를 건드리면 `Esc` 로 목록에
    // 돌아왔을 때 자리를 잃는다(`selected` 를 무턱대고 따르면 생기는 반대쪽 결함).
    let detail: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "text",
        "title": "a.txt", "hint": "", "rows": [], "text": "가나다",
        "note": "", "selected": 0, "keys": {}
    }))
    .unwrap();
    let sent = sent_after(
        vec![layout_one_pane(), mdir_table_screen(2), detail],
        &[(Key::Escape, Mods::NONE), (Key::Char('d'), Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action" && f["do"] == "delete")
        .unwrap_or_else(|| panic!("목록으로 못 돌아왔다: {frames:?}"));
    assert_eq!(
        action["input"], "/tmp/x/a.txt",
        "상세를 보고 왔더니 커서가 옮겨져 있었다: {action:?}"
    );
}

#[test]
fn cancelling_a_plugin_confirm_does_nothing_at_all() {
    // 기본이 '아니오'인 화면이다 — 취소가 곧 "아무 일도 안 일어남"이라야 한다.
    let ask: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "confirm",
        "title": "삭제", "hint": "", "rows": [], "text": "정말 지울까?", "note": "",
        "selected": 0, "keys": {"enter": "delete"}
    }))
    .unwrap();
    let sent = sent_after(vec![layout_one_pane(), ask], &[(Key::Escape, Mods::NONE)]);
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    assert!(
        !frames.iter().any(|f| f["action"] == "plugin_action"),
        "취소했는데 액션이 나갔다: {frames:?}"
    );
}

#[test]
fn the_input_method_state_goes_up_as_a_fact_not_a_drawing() {
    // ★ 이 배선은 오래 **라이브 스크린샷으로만** 잡혔다(GUI 에 큐 오라클이 없다던 그
    // 자리). 한/영을 묻는 일만 OS 에 남기고 올리는 일을 갈라 두니 여기서 잴 수 있다.
    //
    // 우리가 보내는 것은 **사실뿐**이다 — 어디에 무슨 색으로 그릴지는 플러그인이
    // 정한다(설계 Tier D · §4.4). 종전에는 우리가 그림까지 들고 있었고 자리가 정본과
    // 갈려 있었다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();

    assert!(view.report_ime(Some("한")), "바뀐 것을 안 알렸다");
    view.pump_headless();          // 모아 둔 것을 실제로 보낸다
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    let fact = frames
        .iter()
        .find(|f| f["action"] == "client_fact")
        .unwrap_or_else(|| panic!("사실이 안 올라갔다: {frames:?}"));
    assert_eq!(fact["name"], "ime");
    assert_eq!(fact["value"], "한");

    // 같은 값은 다시 안 올린다 — 0.3초마다 같은 말을 하면 서버가 매번 다시 그린다.
    assert!(!view.report_ime(Some("한")), "안 바뀌었는데 또 올렸다");

    // 끄는 것도 사실이다: 값이 비면 서버가 그 사실을 지운다(배지가 사라진다).
    assert!(view.report_ime(None));
    view.pump_headless();
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    let last = frames
        .iter()
        .filter(|f| f["action"] == "client_fact")
        .next_back()
        .unwrap();
    assert!(last["value"].is_null(), "끔이 안 올라갔다: {last}");
}

// ── 셀 격자(§10-21ⓙ) · 블록 문자(§10-21ⓘ) ──────────────────────────────────
//
// 제보 둘의 뿌리가 하나다: **가로 자리를 글꼴이 정하고 있었다.** 캔버스가 한 줄을 런
// 통짜로 셰이퍼에 넘기면, 폴백 글꼴에서 오는 글자(한글·블록)의 진폭이 칸너비의 정수배가
// 아닐 때 그 뒤가 전부 밀린다.
//
// 그래서 격자를 클라가 잡는다. 아래 오라클은 그 **나누기 규칙**을 글꼴 없이 잰다 —
// 시험 폰트는 폭이 0이라 픽셀은 못 재지만(이 파일 머리말), 규칙은 순수 함수다.

#[test]
fn a_pure_ascii_run_stays_one_piece() {
    // 화면의 대부분이 여기다. 한 칸에 하나씩 만들면 80x24 에 1,920 조각이 생긴다.
    let segs = SessionView::grid_segments("hello world");
    assert_eq!(segs.len(), 1, "ASCII 를 쪼갰다: {segs:?}");
    assert_eq!(segs[0], ("hello world".to_owned(), 11));
}

#[test]
fn each_hangul_char_gets_its_own_cell_box() {
    // ★ 이것이 ⓙ 의 처방이다. 한글을 이어 붙이면 그 안에서 밀린 만큼 뒤가 밀린다.
    let segs = SessionView::grid_segments("가나다");
    assert_eq!(segs.len(), 3, "한글을 한 덩이로 뒀다: {segs:?}");
    for seg in &segs {
        assert_eq!(seg.1, 2, "한글은 두 칸이다: {seg:?}");
    }
}

#[test]
fn ascii_and_hangul_alternate_without_losing_a_character() {
    // 나누기가 글자를 먹으면 화면에서 조용히 사라진다.
    let segs = SessionView::grid_segments("ab가cd나");
    let joined: String = segs.iter().map(|(s, _)| s.as_str()).collect();
    assert_eq!(joined, "ab가cd나", "나누며 글자가 바뀌었다: {segs:?}");
    assert_eq!(
        segs.iter().map(|(_, c)| *c).collect::<Vec<_>>(),
        vec![2, 2, 2, 2],
        "칸 수가 틀리면 그 뒤가 통째로 밀린다: {segs:?}"
    );
}

#[test]
fn the_cell_count_of_a_row_is_what_the_server_counted() {
    // ★ 이 합이 곧 그 줄의 폭이다. 여기가 어긋나면 우리가 잡은 격자가 서버의 격자와
    //   다른 뜻이 되어, 마우스 셀 산수·테두리·커서가 한꺼번에 어긋난다.
    for text in ["hello", "가나다", "a가b나c", "─│┌", ""] {
        let ours: usize = SessionView::grid_segments(text).iter().map(|(_, c)| c).sum();
        let server: usize = text
            .chars()
            .map(|c| proto::compose::char_cells(c).max(1))
            .sum();
        assert_eq!(ours, server, "{text:?} 의 칸 수가 서버와 다르다");
    }
}

#[test]
fn an_empty_run_makes_no_pieces() {
    assert!(SessionView::grid_segments("").is_empty());
}

/// 블록 문자가 든 캔버스 하나.
fn canvas_with(text: &str) -> proto::canvas::Canvas {
    let mut canvas = proto::canvas::Canvas::new(text.chars().count().max(4), 1);
    canvas.put_text(0, 0, text, CellStyle::default());
    canvas
}

#[test]
fn block_characters_become_rectangles_at_their_own_cells() {
    // 자리(칸 좌표)가 틀리면 그림이 통째로 어긋난다 — 마스코트 제보가 그것이다.
    let blocks = SessionView::block_cells(&canvas_with("a█b▀"));
    assert_eq!(blocks.len(), 2, "블록 둘을 못 찾았다");
    assert_eq!((blocks[0].x, blocks[0].y), (1, 0));
    assert_eq!((blocks[1].x, blocks[1].y), (3, 0));
    assert_eq!(blocks[1].fill, proto::canvas::block_fill('▀').unwrap());
}

#[test]
fn a_canvas_without_blocks_asks_for_no_rectangles() {
    // 빈 목록이라야 오버레이가 그 프레임에 아무 일도 안 한다.
    assert!(SessionView::block_cells(&canvas_with("hello")).is_empty());
}

#[test]
fn the_blanked_set_and_the_rectangles_come_from_the_same_judgement() {
    // ★ 둘이 갈리면 글리프와 사각형이 겹쳐 보이거나(칸을 안 비움) 그림이 통째로
    //   사라진다(사각형을 안 그림). 테두리 쪽에서 이미 굳은 규율이다.
    let canvas = canvas_with("░▒a▓");
    let from_rects: std::collections::BTreeSet<(u16, u16)> = SessionView::block_cells(&canvas)
        .into_iter()
        .map(|b| (b.x, b.y))
        .collect();
    assert_eq!(from_rects, SessionView::block_cell_set(&canvas));
    assert_eq!(from_rects.len(), 3);
}

#[test]
fn a_block_cell_is_not_painted_as_a_glyph() {
    // ★ **그려진 프레임**에서 잰다(순수 함수 둘이 맞아도 render_row 가 안 비우면
    //   글리프와 사각형이 겹친다 — 그 배선이 이 오라클의 대상이다).
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["AA█BB", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), screen], &[]);
    // ⚠ **프레임 전체**를 봐야 한다. 줄이 조각으로 나뉘므로("AA" · "█" · "BB") "AA" 가
    //   든 조각만 보면 거기엔 애초에 블록이 없어 **무엇을 해도 통과한다** — 변이를 심어
    //   그 사실을 알았다(판 기하 슬라이스와 같은 종류의 공허함).
    assert!(
        painted_contains(&painted, "AA"),
        "캔버스가 프레임에 없다 — 단언이 공허해진다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "BB"),
        "블록을 비우며 뒤 글자까지 지웠다: {painted:?}"
    );
    assert!(
        !painted.iter().any(|t| t.contains('█')),
        "블록이 글자로도 그려졌다(사각형과 두 겹이 된다): {painted:?}"
    );
}

#[test]
fn a_measured_cell_size_is_kept_but_a_nonsense_one_is_not() {
    // 0 이나 무한대를 받으면 격자가 한 점으로 접히고, 증상은 "캔버스가 통째로 비었다"다.
    let (link, _tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    assert_eq!(view.cell_px.get(), None, "재기 전에는 없다");
    view.note_cell_size(8., 16.);
    assert_eq!(view.cell_px.get(), Some((8., 16.)));
    view.note_cell_size(0., 16.);
    view.note_cell_size(f32::INFINITY, 16.);
    assert_eq!(view.cell_px.get(), Some((8., 16.)), "말이 안 되는 값을 받았다");
}

/// 그려진 글자를 **가로 자리와 함께** 돌려준다 — 격자가 잡혔나를 재는 자리용.
///
/// # 왜 이것만은 폭을 잴 수 있나
///
/// 시험 폰트는 글자 폭이 0이라 셰이퍼가 놓는 자리는 못 잰다(위 절 머리말). 그런데
/// §10-21ⓙ 의 처방이 바로 **자리를 셰이퍼에서 뺏는 것**이다 — 못박은 조각의 가로 자리는
/// 우리가 준 칸너비가 정하므로 글꼴과 무관하다. 그래서 이 오라클은 **고친 뒤에야
/// 성립한다**: 종전 코드에서는 전부 0에 겹쳐 나온다.
fn painted_x(
    messages: Vec<ServerMessage>,
    cell: (f32, f32),
) -> Vec<(String, f32)> {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    warpui::App::test((), move |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        for msg in messages {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        // 실행 경로에서는 `report_size` 가 자리표에서 재어 남긴다. 헤드리스에는 창이
        // 없으니 같은 값을 직접 놓는다 — 재는 자리가 아니라 **쓰는 자리**를 시험한다.
        view.note_cell_size(cell.0, cell.1);
        let (window_id, _handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation {
            updated,
            ..Default::default()
        };
        app.update(move |ctx| {
            presenter.invalidate(invalidation, ctx);
            let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
            scene
                .painted_texts()
                .map(|t| (t.text.clone(), t.bounds.origin().x()))
                .collect()
        })
    })
}

fn hangul_screen(text: &str) -> Vec<ServerMessage> {
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[[text, {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    vec![layout_one_pane(), screen]
}

#[test]
fn each_wide_glyph_lands_on_its_own_cell_boundary() {
    // ★ ⓙ 의 본체다. 한글 셋을 그리면 자리가 0·2칸·4칸이어야 한다 — 셰이퍼가 놓으면
    //   (시험 폰트에서) 전부 0 에 겹치고, 실제 글꼴에서는 진폭만큼 밀린다.
    let boxes = painted_x(hangul_screen("가나다"), (10., 20.));
    let xs: Vec<f32> = ["가", "나", "다"]
        .iter()
        .map(|ch| {
            boxes
                .iter()
                .find(|(t, _)| t == ch)
                .unwrap_or_else(|| panic!("{ch} 가 프레임에 없다: {boxes:?}"))
                .1
        })
        .collect();
    // 창 왼쪽 여백만큼 통째로 밀려 있으므로 **첫 글자를 기준**으로 잰다.
    assert_eq!(xs[1] - xs[0], 20., "둘째 글자가 두 칸 뒤가 아니다: {xs:?}");
    assert_eq!(xs[2] - xs[1], 20., "셋째 글자가 두 칸 뒤가 아니다: {xs:?}");
}

#[test]
fn a_wide_glyph_after_ascii_still_lands_on_the_grid() {
    // ASCII 는 고정폭 글꼴이 놓는다(시험 폰트에서는 폭 0). 그 **뒤에** 오는 한글이
    // 서로 두 칸씩 벌어지는지를 본다 — 조각 하나라도 안 못박히면 여기가 무너진다.
    let boxes = painted_x(hangul_screen("ab가나"), (10., 20.));
    let ga = boxes.iter().find(|(t, _)| t == "가").expect("가 없다").1;
    let na = boxes.iter().find(|(t, _)| t == "나").expect("나 없다").1;
    assert_eq!(na - ga, 20., "한글 사이가 두 칸이 아니다: {boxes:?}");
}

#[test]
fn the_grid_follows_the_measured_cell_width() {
    // 칸너비가 배율로 바뀌면(§10-21ⓐ) 격자도 따라와야 한다 — 상수를 박아 두면 배율을
    // 바꾼 순간 조용히 어긋난다.
    let wide = painted_x(hangul_screen("가나"), (20., 40.));
    let ga = wide.iter().find(|(t, _)| t == "가").unwrap().1;
    let na = wide.iter().find(|(t, _)| t == "나").unwrap().1;
    assert_eq!(na - ga, 40., "칸너비 20 이면 두 칸은 40 이다: {wide:?}");
}

// ── 팔레트 세 칸 · 설명 접기(§10-21ⓞ·ⓗ⑶) ───────────────────────────────────

fn palette_pieces(filter: &str) -> Vec<String> {
    let mut keys = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend(filter.chars().map(|c| (Key::Char(c), Mods::NONE)));
    painted_after(three_tabs(), &keys)
}

#[test]
fn a_palette_row_is_drawn_as_three_separate_pieces() {
    // ★ 제보의 요구가 "색으로 구분"이라 **조각이 갈려 있어야** 한다 — 한 덩이면 색이
    //   하나뿐이다. 이름과 옵션이 따로 그려지는지가 그 증거다.
    let pieces = palette_pieces("split-window");
    assert!(
        pieces.iter().any(|p| p == "split-window"),
        "이름 칸이 따로 안 그려졌다: {pieces:?}"
    );
    assert!(
        pieces.iter().any(|p| p == "-h"),
        "옵션 칸이 따로 안 그려졌다(이름에 붙어 있다): {pieces:?}"
    );
    assert!(
        !pieces.iter().any(|p| p.starts_with("split-window -h ")),
        "이름·옵션·설명이 아직 한 덩이다: {pieces:?}"
    );
}

#[test]
fn the_split_rule_is_the_core_one() {
    // 뷰가 자기 규칙으로 자르면 두 클라가 갈린다 — core 의 것을 쓰는지 값으로 확인한다.
    assert_eq!(proto::palette::split_name("split-window -h"), ("split-window", "-h"));
    let pieces = palette_pieces("select-pane");
    assert!(
        pieces.iter().any(|p| p == "-t next"),
        "여러 낱말 옵션이 한 덩이로 안 그려졌다: {pieces:?}"
    );
}

#[test]
fn a_long_description_wraps_instead_of_pushing_the_panel() {
    // ⓗ⑶ — 접어서 **보이고**, 판은 그대로. 접힌 줄은 설명 칸 아래에 이어 붙는다.
    //
    // ⚠ 설명은 서버가 준 것으로 넣는다. 코어 표의 설명은 이 폭에서 안 접히고, 접히지
    //   않는 표본으로는 이 오라클이 아무것도 안 잰다(공허해진다).
    let cols = SessionView::PANEL_COLS
        - (SessionView::PAL_NAME_COLS + SessionView::PAL_OPTS_COLS + 2);
    let long = "가나다라마바사 ".repeat(12);
    let lines = proto::palette::wrap(long.trim(), cols);
    assert!(lines.len() > 1, "이 설명은 접혀야 한다 — 오라클이 뜻을 잃는다");

    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "하나", "active": true}],
        "plugin_surface": {
            "commands": [{"name": "verbose-thing", "desc": long.trim(), "cat": "설정/기타"}],
            "noarg": [], "menu_items": [], "settings": [], "setting_cats": []
        }
    }))
    .unwrap();
    let mut keys = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("verbose".chars().map(|c| (Key::Char(c), Mods::NONE)));
    let pieces = painted_after(vec![layout_one_pane(), status], &keys);
    for chunk in &lines {
        assert!(
            pieces.iter().any(|p| p == chunk),
            "접힌 줄이 안 그려졌다: {chunk:?} / {pieces:?}"
        );
    }
}

#[test]
fn wrapping_never_spends_more_rows_than_the_panel_has() {
    // 접히는 만큼 줄이 늘므로, 예산을 안 지키면 **접기 때문에** 판이 넘친다.
    let boxes = painted_boxes(three_tabs(), &[(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)]);
    let mut ys: Vec<i64> = boxes.iter().map(|(_, y)| y.round() as i64).collect();
    ys.sort_unstable();
    ys.dedup();
    // 창 600px · 줄 높이가 폰트에서 오므로 절대값은 못 박는다 — **넘치지 않음**만 본다.
    let bottom = ys.last().copied().unwrap_or(0);
    assert!(bottom <= 600, "판이 창 밖으로 나갔다(맨 아래 줄 y={bottom})");
}

#[test]
fn ctrl_tab_moves_the_panel_tab_while_a_panel_is_open() {
    // ⓗ⑷ — ⓕ 가 `Ctrl+Tab` 을 세션 전역으로 가져갔으므로 우선순위를 정해야 했다.
    // 판이 위에 있으면 판이 이긴다(화면이 떠 있으면 모든 키가 그 화면의 것 — core 규칙).
    let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.handle_key(Key::Escape, Mods::NONE);
    view.handle_key(Key::Char(':'), Mods::NONE);
    assert_eq!(view.screens.top(), Some(Screen::Commands), "팔레트가 안 열렸다");
    let before = view.screens.palette_tab();
    view.alt_tab_step_for_test(true);
    assert_eq!(
        view.screens.palette_tab(),
        before + 1,
        "판이 떠 있는데 Ctrl+Tab 이 분류 탭을 안 옮겼다"
    );
    // 그리고 **세션 탭 스위처를 열지 않았다** — 그것이 이 우선순위의 요점이다.
    assert_eq!(view.screens.top(), Some(Screen::Commands), "판이 스위처로 바뀌었다");
}

#[test]
fn ctrl_tab_still_switches_session_tabs_when_no_panel_is_open() {
    // 반대쪽도 지킨다 — 판이 없으면 ⓕ 의 동선 그대로다(이 단언이 없으면 위 우선순위가
    // 세션 탭 전환을 통째로 죽여도 통과한다).
    let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert_eq!(view.screens.top(), None, "판이 없어야 한다");
    view.alt_tab_step_for_test(true);
    assert_eq!(view.screens.top(), Some(Screen::Tabs), "스위처가 안 열렸다");
}

// ── 작성창 빈 줄(§10-21ⓒ2) ──────────────────────────────────────────────────

/// 작성창을 `seed` 로 열고 **그려진 줄의 세로 자리들**을 돌려준다.
fn compose_rows(seed: &str) -> Vec<f32> {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    let seed = seed.to_owned();
    warpui::App::test((), move |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
        view.pump_headless();
        view.screens.open_compose(&seed);
        let (window_id, _handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation { updated, ..Default::default() };
        app.update(move |ctx| {
            presenter.invalidate(invalidation, ctx);
            let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
            let mut ys: Vec<f32> = scene.painted_texts().map(|t| t.bounds.origin().y()).collect();
            ys.sort_by(|a, b| a.partial_cmp(b).unwrap());
            ys.dedup();
            ys
        })
    })
}

#[test]
fn consecutive_blank_lines_each_take_a_row() {
    // ★ 제보 그대로 — 빈 줄을 연달아 넣으면 그만큼 줄이 있어야 한다. 종전에는 자식이
    //   없는 행 상자의 높이가 0 이라 **커서가 놓인 빈 줄 하나만** 보였다.
    let one = compose_rows("a\nb").len();
    let three = compose_rows("a\n\n\n\nb").len();
    assert_eq!(
        three,
        one + 3,
        "빈 줄 셋이 자리를 안 차지했다(줄 자리: {one} → {three})"
    );
}

#[test]
fn a_blank_line_does_not_leak_into_the_text() {
    // ⚠ 그림으로 놓은 공백이 **내용**에 새면 보낸 글이 달라진다. 작성창의 글은
    //   `editor.lines()` 가 쥐고 있고 그림은 거기서 나오므로, 그 목록을 직접 본다.
    let (link, _tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    view.screens.open_compose("a\n\nb");
    let lines: Vec<String> = view
        .screens
        .editor()
        .expect("작성창이 안 열렸다")
        .lines()
        .to_vec();
    assert_eq!(lines, vec!["a".to_owned(), String::new(), "b".to_owned()]);
}


// ── 대문자 바인딩(§10-21ⓒ3) ─────────────────────────────────────────────────
//
// 제보는 "탭 고정이 GUI 에서 안 된다"였고, 핸드오프의 유력 가설은 **shift+글자가 대문자로
// 안 접힌다**(그래서 표의 `shift-P` 와 안 맞는다)였다. 그 가설은 **틀렸다** — 상류
// `get_input_key` 가 이미 접는다(`warpui/.../key_events.rs`: *"If the key is a character
// AND shift is pressed, we force the key to uppercase"*). 아래가 그 사실을 못박는다.

#[test]
fn esc_shift_p_sends_the_pin_command() {
    // ★ 제보의 동선 그대로 — 이 줄이 초록이면 **클라 쪽은 끝까지 간다**(라이브 컷도 같다).
    let out = sent_after(
        three_tabs(),
        &[(Key::Escape, Mods::NONE), (Key::Char('P'), Mods::NONE)],
    );
    assert!(
        out.iter().any(|o| matches!(o, Outgoing::Command(Command::TogglePin { .. }))),
        "esc Shift+P 가 고정 명령을 안 보냈다: {out:?}"
    );
    // ★ **자리를 실어야 원격 탭에도 걸린다**(§10-21ⓒ3 — 사용자 답: "원격 탭이 핀이
    //   안 됨"). 서버의 기본값(`sess.active_index`)은 로컬 탭의 자리라 원격에서 어긋난다.
    let idx = out.iter().find_map(|o| match o {
        Outgoing::Command(Command::TogglePin { index }) => Some(*index),
        _ => None,
    });
    assert_eq!(idx, Some(Some(0)), "활성 탭의 자리를 안 실었다: {out:?}");
}

#[test]
fn every_uppercase_binding_is_reachable_from_a_shifted_keystroke() {
    // 이것은 **부류**다: 표의 `shift-<대문자>` 가 여덟이고, 키 변환이 한 자리에서
    // 어긋나면 여덟이 한꺼번에 죽는다(그리고 조용하다). 상류가 주는 모양
    // (`key: "P"` + `shift: true`)을 그대로 넣어 표까지 닿는지 본다.
    for (name, ch) in [
        ("shift-G", 'G'),
        ("shift-H", 'H'),
        ("shift-J", 'J'),
        ("shift-K", 'K'),
        ("shift-L", 'L'),
        ("shift-P", 'P'),
        ("shift-R", 'R'),
        ("shift-T", 'T'),
    ] {
        let ks = ks(&ch.to_string(), false, false, true);
        let (key, mods) =
            SessionView::key_from_keystroke(&ks).unwrap_or_else(|| panic!("{name}: 키를 못 만들었다"));
        assert_eq!(key, Key::Char(ch), "{name}: 대문자로 안 접혔다");
        assert_eq!(
            base::keys::binding_name_with(key, mods).as_deref(),
            Some(name),
            "{name}: 표 이름으로 안 돌아간다"
        );
    }
}

#[test]
fn pinning_a_remote_tab_carries_that_tabs_merged_index() {
    // ★ **제보의 자리다**(§10-21ⓒ3 · 사용자 답 2026-08-03: "원격 탭이 핀이 안 됨").
    //
    // 서버는 자리를 안 실으면 `sess.active_index` 로 접는데 그것은 **로컬 탭**의 자리다.
    // 원격(병합) 탭이 활성이면 그 값은 보고 있는 탭이 아니라서 토글이 엉뚱한 로컬 탭에
    // 걸린다 — 화면에서는 "원격 탭만 핀이 안 된다"로 보인다. 정본은 그래서 활성 탭의
    // **병합 index** 를 명시해 보낸다(`clientcmd.py` 의 주석이 그 함정을 적어 뒀다).
    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [
            {"index": 0, "name": "로컬"},
            {"index": 1, "name": "⇄box:원격", "remote": true, "active": true},
        ]
    }))
    .unwrap();
    let out = sent_after(
        vec![layout_one_pane(), status],
        &[(Key::Escape, Mods::NONE), (Key::Char('P'), Mods::NONE)],
    );
    let idx = out.iter().find_map(|o| match o {
        Outgoing::Command(Command::TogglePin { index }) => Some(*index),
        _ => None,
    });
    assert_eq!(
        idx,
        Some(Some(1)),
        "원격 탭이 활성인데 그 자리를 안 실었다 — 서버가 로컬 탭을 고정한다: {out:?}"
    );
}


// ── 영어 화면에 한국어가 안 남는가(§10-21ⓖ2) ────────────────────────────────
//
// 제보: *"영어를 골랐는데 화면에 한국어가 섞인다"*(확인 판의 버튼 `취소` · 안내줄).
// 원인은 번역 누락이었고, 핸드오프의 판정은 **게이트가 없다**였다 — 로케일 래칫은
// "소켓을 건너는 한국어"를 세지 **우리가 그리는 글**을 안 센다.
//
// 여기서는 **그려진 프레임**을 잰다. 어떤 경로로 그 글이 왔든(정적 표·`t()`·서버가 준
// 글) 화면에 한글이 남으면 잡힌다 — 사용자가 보는 것과 같은 자리에서 재는 것이 요점이다.

fn hangul(text: &str) -> bool {
    text.chars().any(|c| ('\u{ac00}'..='\u{d7a3}').contains(&c))
}

/// 탭 이름이 **ASCII** 인 상태 — 사용자 자료(탭 이름·패널 글)는 번역 대상이 아니라서
/// 표본에 한글을 두면 이 오라클이 자기 자료에 걸린다.
fn ascii_tabs() -> Vec<ServerMessage> {
    vec![
        layout_one_pane(),
        serde_json::from_value(serde_json::json!({"t": "status", "windows": [
            {"index": 0, "name": "one", "active": true},
            {"index": 1, "name": "two"},
        ]}))
        .unwrap(),
    ]
}

/// 그 화면을 **영어로** 열어 그려진 조각 중 한글이 든 것을 돌려준다.
fn english_hangul(keys: &[(Key, Mods)]) -> Vec<String> {
    base::i18n::with_locale("en", || {
        painted_after(ascii_tabs(), keys)
            .into_iter()
            .filter(|t| hangul(t))
            .collect()
    })
}

fn typed(text: &str) -> Vec<(Key, Mods)> {
    text.chars().map(|c| (Key::Char(c), Mods::NONE)).collect()
}

#[test]
fn the_english_locale_actually_switches_something() {
    // ★ 공허 방지 — 로케일이 안 바뀌면 아래가 전부 "한글투성이"로 붉거나(그건 낫다)
    //   반대로 표본이 비어 조용히 통과할 수 있다. 먼저 스위치가 도는지 본다.
    let ko = base::i18n::with_locale("ko", || base::Action::Quit.label().to_owned());
    let en = base::i18n::with_locale("en", || base::Action::Quit.label().to_owned());
    assert!(hangul(&ko), "ko 라벨이 한국어가 아니다: {ko}");
    assert!(!hangul(&en), "en 으로 안 바뀌었다: {en}");
}

#[test]
fn no_korean_survives_on_the_confirm_screen() {
    // ★ **제보의 그 화면**이다(`Kill tab` 판). 되돌릴 수 없는 것 앞의 화면이라
    //   어느 쪽이 "아니오"인지 못 읽으면 그 화면의 취지가 무너진다.
    let left = english_hangul(&[(Key::Char('b'), Mods::CTRL), (Key::Char('&'), Mods::NONE)]);
    assert!(left.is_empty(), "확인 판에 한국어가 남았다: {left:?}");
}

#[test]
fn no_korean_survives_on_the_common_screens() {
    let screens: &[(&str, Vec<(Key, Mods)>)] = &[
        ("키 도움말", vec![(Key::Escape, Mods::NONE), (Key::Char('?'), Mods::NONE)]),
        ("탭 스위처", vec![(Key::Escape, Mods::NONE), (Key::Tab, Mods::NONE)]),
        ("트리", vec![(Key::Char('b'), Mods::CTRL), (Key::Char('w'), Mods::NONE)]),
        ("메뉴", vec![(Key::Char('b'), Mods::CTRL), (Key::Enter, Mods::NONE)]),
        ("팔레트", vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)]),
        ("설정", {
            let mut k = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
            k.extend(typed("settings"));
            k.push((Key::Enter, Mods::NONE));
            k
        }),
        ("알림 이력", {
            let mut k = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
            k.extend(typed("notice-history"));
            k.push((Key::Enter, Mods::NONE));
            k
        }),
        ("버퍼", {
            let mut k = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
            k.extend(typed("choose-buffer"));
            k.push((Key::Enter, Mods::NONE));
            k
        }),
        ("키 바인딩 목록", {
            let mut k = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
            k.extend(typed("list-keys"));
            k.push((Key::Enter, Mods::NONE));
            k
        }),
    ];
    let mut bad = Vec::new();
    for (name, keys) in screens {
        let left = english_hangul(keys);
        if !left.is_empty() {
            bad.push(format!("{name}: {left:?}"));
        }
    }
    assert!(
        bad.is_empty(),
        "영어 로케일인데 한국어가 남은 화면:\n  {}\n\
         카탈로그(`base/src/i18n/en_*.rs`)에 그 원문의 영어 짝을 더할 것.",
        bad.join("\n  ")
    );
}

// ── 조합 중인 글자(preedit) · §10-21ⓞ2 ⑵ ─────────────────────────────────────
//
// 확정만 받으면 사람이 `ㅎ`→`하`→`한` 을 만드는 동안 **화면이 비어 있다**. 상류는 그
// 상태를 이미 주고 있었고(winit `Ime::Preedit` → `SetMarkedText`) 받는 자리가 없었다.
// 라이브로도 확인했지만 조합은 1초 안에 확정돼 캡처 타이밍에 걸리므로, 판정은 여기서 한다.

/// 조합 중인 글자를 얹은 캔버스. 그림이 아니라 **칸의 내용**을 본다.
fn canvas_with_preedit(preedit: &str, cursor: (u16, u16)) -> proto::canvas::Canvas {
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(cursor.0, cursor.1)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.handle_preedit(preedit);
    let mut canvas = view.state.composite().expect("합성이 없다");
    view.overlay_preedit(&mut canvas);
    canvas
}

#[test]
fn the_composing_text_is_drawn_at_the_cursor() {
    let canvas = canvas_with_preedit("한", (3, 2));
    let cell = canvas.cell(3, 2).expect("칸이 없다");
    assert_eq!(cell.ch, '한', "조합 중인 글자가 커서 자리에 없다 — 사람은 자기가 무엇을 치는지 못 본다");
    // ⚠ 표시는 **이 클라가 실제로 그리는 것**이라야 한다. 밑줄이 자연스러운 선택이지만
    //    `colors()` 는 fg·bg·reverse 만 본다 — 밑줄만 세우면 조합 글자가 확정 글자와
    //    똑같이 보이고, 오라클은 초록인데 화면은 아무 말도 안 하는 상태가 된다.
    assert!(cell.style.reverse, "그려지는 표시가 없으면 확정된 글자와 구분되지 않는다");
    // 넓은 글자의 뒤 칸은 연속 셀이라야 한다(pytmux-17 과 같은 규칙 — 안 그러면 런이 끊긴다).
    assert!(canvas.cell(4, 2).is_some_and(|c| c.continuation), "뒤 칸을 안 잡았다");
}

#[test]
fn clearing_the_composition_removes_it_from_the_screen() {
    // 확정·취소되면 상류가 빈 문자열로 부른다. 안 지우면 **조합 잔상**이 화면에 남는다.
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(3, 2)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert!(view.handle_preedit("한"), "첫 조합이 상태를 안 세웠다");
    assert!(view.handle_preedit(""), "지우기가 상태를 안 바꿨다");
    let mut canvas = view.state.composite().unwrap();
    view.overlay_preedit(&mut canvas);
    assert_ne!(canvas.cell(3, 2).map(|c| c.ch), Some('한'), "조합 잔상이 남았다");
}

#[test]
fn the_composing_text_never_reaches_the_pane() {
    // ⛔ 이것이 이 기능의 안전 조건이다. 조합 중 문자열을 흘리면 셸이 **자모를** 받아
    //    `치명ㄷ` 부류가 된다(`ime.rs` 머리말의 사고). 그릴 뿐 보내지 않는다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    for step in ["ㅎ", "하", "한"] {
        view.handle_preedit(step);
        view.pump_headless();
    }
    assert!(sent.lock().unwrap().is_empty(), "조합 중인 글자가 서버로 샜다: {:?}", sent.lock().unwrap());
}

#[test]
fn the_same_composition_twice_does_not_redraw() {
    // 조합은 자판마다 **같은 문자열**로 여러 번 온다(실측: 한 음절에 3회). 매번 다시
    // 그리면 프레임이 헛돈다.
    let (mut view, _tx, _sent) = harness();
    assert!(view.handle_preedit("한"));
    assert!(!view.handle_preedit("한"), "같은 조합인데 다시 그린다고 했다");
}

#[test]
fn the_composition_is_clipped_at_the_panes_right_edge() {
    // 좌우 분할에서 넘겨 쓰면 **옆 패널을 침범한다**. 경계는 화면 폭이 아니라 그 패널의 것이다.
    let (mut view, tx, _sent) = harness();
    let split = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 4, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 4, "title": "sh", "active": true},
                  {"id": 2, "x": 10, "y": 0, "w": 70, "h": 4, "title": "sh2"}]
    })).unwrap();
    for msg in [split, screen_with_cursor(8, 0)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.handle_preedit("한글");
    let mut canvas = view.state.composite().unwrap();
    view.overlay_preedit(&mut canvas);
    assert_eq!(canvas.cell(8, 0).map(|c| c.ch), Some('한'), "첫 글자는 들어가야 한다");
    assert_ne!(canvas.cell(10, 0).map(|c| c.ch), Some('글'), "패널 경계를 넘어 그렸다");
}
