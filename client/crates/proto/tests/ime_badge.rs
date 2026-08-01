//! 입력기 배지가 **활성 패널 우상단**에 그려지나 — 렌더 오라클.
//!
//! 왜 렌더로 재나: 자리를 계산하는 함수만 단위로 재면 **합성이 그것을 안 부르는 것**을
//! 못 잡는다(이 저장소가 두 번 겪은 공허 통과). 실제로 합성해 놓고 그 칸의 글자를 본다.

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
fn the_badge_sits_at_the_top_right_of_the_active_pane() {
    let mut state = state_with_two_panes();
    assert!(!row_text(&state, 0).contains("EN"), "안 넣었는데 배지가 있다");

    state.set_ime_badge(Some("EN".to_owned()));
    let line = row_text(&state, 0);
    // 활성 패널(오른쪽, x=20..38)의 **오른쪽 끝**이라야 한다 — 화면 끝도, 왼쪽 패널도 아니다.
    let at = line.find("EN").expect(&format!("배지가 없다: {line:?}"));
    assert_eq!(at, 36, "배지가 활성 패널 우상단이 아니다: {line:?}");

    // 활성 패널이 바뀌면 배지도 따라간다 — 자리를 박아 두면 분할에서 엉뚱한 칸에 남는다.
    let layout: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 40, "rows": 6,
        "panes": [
            {"id": 1, "x": 0, "y": 0, "w": 18, "h": 6, "title": "left"},
            {"id": 2, "x": 20, "y": 0, "w": 18, "h": 6, "title": "right"}
        ],
        "active": 1
    }))
    .unwrap();
    state.apply(layout);
    let line = row_text(&state, 0);
    assert_eq!(line.find("EN"), Some(16), "활성 패널을 안 따라갔다: {line:?}");
}

#[test]
fn a_pane_too_narrow_for_the_badge_keeps_its_content() {
    // 화면을 덮어 가며 알릴 만한 것은 아니다 — 좁으면 안 그린다.
    let mut state = SessionState::new();
    let layout: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 6, "rows": 3,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 3, "h": 3, "title": "tiny"}],
        "active": 1
    }))
    .unwrap();
    state.apply(layout);
    state.set_ime_badge(Some("EN".to_owned()));
    assert!(!row_text(&state, 0).contains("EN"), "좁은 패널을 배지가 덮었다");
}
