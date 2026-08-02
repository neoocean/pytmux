//! 입력기 배지 — **우리는 더 이상 그리지 않는다**(설계 Tier D · P7, 2026-08-02i).
//!
//! 종전에는 이 파일이 우리 손그림(`draw_ime_badge`)을 쟀다: 활성 패널 **첫 행** 오른쪽
//! 끝. 그런데 정본은 **커서가 있는 줄**에 그린다 — 두 벌이 갈려 있었고, 그 자리 주석은
//! "정본과 같은 자리"라고 적고 있었다(커서가 첫 줄일 때만 같았다).
//!
//! 이제 자리 규칙은 플러그인 한 벌(`plugins/ime-indicator/cells.py`)이 정하고, 우리는
//! **한/영이라는 사실만** `client_fact` 로 올린다. 그래서 여기서 잴 것도 바뀌었다:
//!
//! 1. 우리가 **스스로 그리지 않는다**(런이 안 오면 아무것도 안 뜬다).
//! 2. 런이 오면 **플러그인이 말한 행**에 뜬다 — 옛 코드는 무슨 값이 와도 0행이었다.
//!    이 오라클이 있었으면 위 갈림을 잡았다.

use proto::message::ServerMessage;
use proto::session::SessionState;

fn state_with_two_panes() -> SessionState {
    let mut state = SessionState::new();
    let layout: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 40, "rows": 6,
        "panes": [
            {"id": 1, "x": 0, "y": 0, "w": 18, "h": 6, "title": "left"},
            {"id": 2, "x": 20, "y": 0, "w": 18, "h": 6, "title": "right"}
        ],
        "active": 2
    }))
    .unwrap();
    state.apply(layout);
    state
}

/// 그 행의 글자만 이어 붙인다(스타일은 여기서 안 본다).
fn row_text(state: &SessionState, y: usize) -> String {
    let canvas = state.composite().expect("합성이 없다");
    let (w, _) = canvas.size();
    (0..w)
        .map(|x| canvas.cell(x, y).map(|c| c.ch).unwrap_or(' '))
        .collect()
}

#[test]
fn we_do_not_draw_the_badge_ourselves_anymore() {
    // 사실을 올리는 것과 그리는 것은 다른 일이다. 런이 안 오면 화면에 아무것도 없다 —
    // 플러그인을 지운 서버에 붙어도 우리 쪽에 배지가 남지 않는다(delete-to-disable).
    let state = state_with_two_panes();
    for y in 0..6 {
        assert!(
            !row_text(&state, y).contains("EN"),
            "런이 없는데 배지가 그려졌다(y={y}): {:?}",
            row_text(&state, y)
        );
    }
}

#[test]
fn the_badge_lands_on_the_row_the_plugin_chose() {
    // ★ 이 오라클이 P7 의 요점이다. 옛 손그림은 **무슨 일이 있어도 0행**이었다 —
    // 정본이 커서 줄에 그리는 것과 갈렸고, 아무도 그것을 재지 않았다.
    let mut state = state_with_two_panes();
    let cells: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_cells",
        "runs": [{
            "x": 34, "y": 4, "text": "[EN]",
            "style": {"f": "black", "bo": 1}, "theme": {"b": "primary"}
        }]
    }))
    .unwrap();
    state.apply(cells);
    assert_eq!(
        row_text(&state, 4).find("[EN]"),
        Some(34),
        "플러그인이 말한 자리에 안 떴다: {:?}",
        row_text(&state, 4)
    );
    assert!(!row_text(&state, 0).contains("EN"), "0행에도 그렸다(옛 손그림의 자리)");
}

#[test]
fn a_wide_character_in_a_run_does_not_shift_the_rest() {
    // ★ **와이드 문자는 두 칸이다.** `put` 이 뒷칸을 자리표로 채우므로 우리가 그 폭만큼
    // 건너뛰어야 다음 글자가 안 겹친다. 정본도 같은 자리에서 같은 실수를 하고 있었고
    // (글자마다 한 칸씩), 한글 배지가 오자 행 폭이 틀어지며 드러났다(2026-08-02i).
    let mut state = state_with_two_panes();
    let cells: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_cells",
        "runs": [{ "x": 20, "y": 2, "text": "[한]", "style": {}, "theme": {} }]
    }))
    .unwrap();
    state.apply(cells);
    let canvas = state.composite().expect("합성이 없다");
    assert_eq!(canvas.cell(20, 2).map(|c| c.ch), Some('['));
    assert_eq!(canvas.cell(21, 2).map(|c| c.ch), Some('한'));
    // 22 는 `한` 의 자리표다 — 여기에 `]` 가 오면 한 칸씩 밀린 것이다.
    assert_eq!(
        canvas.cell(23, 2).map(|c| c.ch),
        Some(']'),
        "와이드 문자를 한 칸으로 세어 뒤가 밀렸다"
    );
}
