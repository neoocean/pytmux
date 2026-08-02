//! 서버 상태 누적 오라클.

use super::*;
use crate::message::{Run, ServerMessage};

fn layout_msg(panes: &[(i64, u16)]) -> ServerMessage {
    let panes: Vec<serde_json::Value> = panes
        .iter()
        .map(|(id, w)| serde_json::json!({"id": id, "x": 0, "y": 0, "w": w, "h": 3}))
        .collect();
    serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "panes": panes, "active": panes.len()
    }))
    .unwrap()
}

fn screen_msg(pane: i64, text: &str) -> ServerMessage {
    ServerMessage::Screen(Screen {
        pane,
        rows: vec![vec![Run::plain(text)]],
        ..Default::default()
    })
}

fn status_msg(tab_names: &[&str]) -> ServerMessage {
    let windows: Vec<serde_json::Value> = tab_names
        .iter()
        .enumerate()
        .map(|(i, n)| serde_json::json!({"index": i, "name": n, "active": i == 0}))
        .collect();
    serde_json::from_value(serde_json::json!({"t": "status", "windows": windows})).unwrap()
}

#[test]
fn accumulates_layout_then_screens() {
    let mut state = SessionState::new();
    assert!(!state.is_complete(), "빈 상태는 완성이 아니다");

    assert!(state.apply(layout_msg(&[(1, 6), (2, 6)])));
    assert_eq!(state.panes().len(), 2);
    assert!(!state.is_complete(), "화면이 아직 안 왔다");

    assert!(state.apply(screen_msg(1, "hi")));
    assert!(!state.is_complete(), "패널 하나가 남았다");
    assert!(state.apply(screen_msg(2, "yo")));
    assert!(state.is_complete());

    assert_eq!(state.compose_pane(1), Some(vec!["hi    ".to_owned()]));
    assert_eq!(state.compose_pane(2), Some(vec!["yo    ".to_owned()]));
}

#[test]
fn new_layout_drops_screens_of_panes_that_are_gone() {
    // 죽은 패널의 스냅샷이 남으면 메모리와 혼란이 함께 쌓인다.
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 4), (2, 4)]));
    state.apply(screen_msg(1, "a"));
    state.apply(screen_msg(2, "b"));

    // 패널 2가 닫힌 새 배치.
    state.apply(layout_msg(&[(1, 4)]));
    assert!(state.compose_pane(1).is_some(), "남은 패널은 유지");
    assert_eq!(state.compose_pane(2), None, "사라진 패널의 화면은 버린다");
}

#[test]
fn screen_for_an_unknown_pane_is_kept_but_not_composable() {
    // 서버가 layout 보다 screen 을 먼저 보내는 순간이 있을 수 있다. 그때 버리면
    // 화면이 한 프레임 비므로 보관은 하되, 폭을 모르니 합성은 못 한다.
    let mut state = SessionState::new();
    assert!(state.apply(screen_msg(9, "early")));
    assert_eq!(state.compose_pane(9), None);
    // 배치가 오면 그때부터 합성된다.
    state.apply(layout_msg(&[(9, 8)]));
    assert_eq!(state.compose_pane(9), Some(vec!["early   ".to_owned()]));
}

#[test]
fn status_updates_tabs_and_reports_change_only_when_it_differs() {
    let mut state = SessionState::new();
    assert!(state.apply(status_msg(&["편집", "빌드"])));
    assert_eq!(state.tabs().tabs.len(), 2);

    // 같은 status 가 또 오면 다시 그릴 이유가 없다 — 서버는 30Hz 로 status 를 보낸다.
    assert!(
        !state.apply(status_msg(&["편집", "빌드"])),
        "같은 내용이면 repaint 를 요구하지 않는다"
    );
    assert!(state.apply(status_msg(&["편집", "빌드", "새 탭"])));
}

// ── 화면 델타(`screen-delta`) ────────────────────────────────────────────────
//
// ★ 이 메시지를 모르던 동안 델타는 `Unknown` 으로 떨어져 **조용히 버려졌다**(실측
// 2026-07-28). 증상이 고약하다: 낱글자 타이핑처럼 몇 줄만 바뀌는 것은 화면에 아예 안
// 나타나고, 화면을 크게 갈아엎는 출력은(서버가 70% 넘으면 full 로 보낸다) 멀쩡해 보인다.
// 그래서 "가끔 화면이 멎는다"로만 보였다.

/// 와이어 모양 그대로 만든 델타 — 필드 이름이 어긋나는 것까지 잡으려고 JSON 으로 짓는다.
fn delta_msg(pane: i64, rows: serde_json::Value, top: usize) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "screen-delta", "pane": pane, "rows": rows,
        "cursor": [1, 0], "wrap": [], "top": top
    }))
    .unwrap()
}

fn three_row_screen(pane: i64) -> ServerMessage {
    ServerMessage::Screen(Screen {
        pane,
        rows: vec![
            vec![Run::plain("첫줄")],
            vec![Run::plain("둘째줄")],
            vec![Run::plain("셋째줄")],
        ],
        ..Default::default()
    })
}

#[test]
fn a_delta_replaces_only_the_rows_it_carries() {
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 10)]));
    state.apply(three_row_screen(1));
    assert!(state.apply(delta_msg(1, serde_json::json!([[1, [["바뀐줄", {}]]]]), 0)));
    let rows = state.compose_pane(1).unwrap();
    assert_eq!(rows[0].trim_end(), "첫줄", "안 온 행이 지워졌다");
    assert_eq!(rows[1].trim_end(), "바뀐줄");
    assert_eq!(rows[2].trim_end(), "셋째줄");
}

#[test]
fn a_delta_can_append_exactly_one_row_at_the_end() {
    // 서버가 행을 하나 늘린 프레임. 그 너머 번호는 **버린다** — 사이가 빈 채로 늘리면
    // 없는 줄이 빈 줄로 그려져 화면이 조용히 어긋난다.
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 10)]));
    state.apply(three_row_screen(1));
    state.apply(delta_msg(1, serde_json::json!([[3, [["넷째줄", {}]]]]), 0));
    assert_eq!(state.compose_pane(1).unwrap().len(), 4);
    state.apply(delta_msg(1, serde_json::json!([[9, [["멀리", {}]]]]), 0));
    assert_eq!(state.compose_pane(1).unwrap().len(), 4, "구멍을 만들며 늘렸다");
}

#[test]
fn a_delta_carries_the_whole_cursor_and_top_not_a_diff() {
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 10)]));
    state.apply(three_row_screen(1));
    state.apply(delta_msg(1, serde_json::json!([]), 42));
    assert_eq!(state.pane_cursor(1), Some((1, 0)));
    // top 이 안 따라오면 마우스 선택이 **다른 줄**을 가리킨다(절대 행 기준점이다).
    assert_eq!(state.pane_abs(1, 0, 0).map(|p| p.line), Some(42));
}

#[test]
fn a_delta_without_a_baseline_asks_for_a_redraw_once() {
    // 바탕이 없으면 바뀐 행을 얹을 데가 없다 — 그 패널은 빈 채로 굳는다. 조용히 버리는
    // 대신 다시 그려 달라고 **한 번** 청한다(파이썬 클라와 같은 처방).
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 10)]));
    assert!(!state.apply(delta_msg(1, serde_json::json!([[0, [["x", {}]]]]), 0)));
    assert!(state.take_redraw_request(), "다시 그려 달라고 안 했다");
    assert!(!state.take_redraw_request(), "가져간 요청이 안 내려갔다");
    // 같은 패널에서 또 와도 다시 청하지 않는다 — full 이 오기 전까지 매 프레임 요청하면
    // 서버가 화면을 통째로 다시 보내는 일이 초당 수십 번 일어난다.
    state.apply(delta_msg(1, serde_json::json!([[0, [["x", {}]]]]), 0));
    assert!(!state.take_redraw_request(), "디바운스가 안 걸렸다");
}

#[test]
fn a_full_screen_restores_the_baseline_so_the_next_gap_asks_again() {
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 10)]));
    state.apply(delta_msg(1, serde_json::json!([]), 0));
    assert!(state.take_redraw_request());
    state.apply(three_row_screen(1)); // full 도착 = 기준 회복
    state.apply(ServerMessage::Layout(
        match layout_msg(&[(2, 10)]) {
            ServerMessage::Layout(l) => l,
            _ => unreachable!(),
        },
    )); // 1번 패널이 사라지며 화면도 버려진다
    state.apply(delta_msg(1, serde_json::json!([]), 0));
    assert!(
        state.take_redraw_request(),
        "기준을 잃은 뒤에는 다시 청해야 한다"
    );
}

#[test]
fn quiet_messages_do_not_trigger_repaint() {
    let mut state = SessionState::new();
    assert!(!state.apply(ServerMessage::Pong { ts: None }));
    assert!(!state.apply(ServerMessage::Ok(serde_json::Value::Null)));
    assert!(!state.apply(ServerMessage::Unknown));
}

#[test]
fn bye_marks_the_session_closed() {
    let mut state = SessionState::new();
    assert!(!state.is_closed());
    assert!(state.apply(ServerMessage::Bye));
    assert!(state.is_closed());
}

#[test]
fn error_is_remembered_so_the_view_can_show_it() {
    let mut state = SessionState::new();
    assert!(state.apply(ServerMessage::Error {
        msg: "그런 탭 없음".into()
    }));
    assert_eq!(state.last_error(), Some("그런 탭 없음"));
}

#[test]
fn cursor_comes_from_the_screen_message() {
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 10)]));
    state.apply(ServerMessage::Screen(Screen {
        pane: 1,
        rows: vec![vec![Run::plain("x")]],
        cursor: Some((3, 2)),
        ..Default::default()
    }));
    assert_eq!(state.pane_cursor(1), Some((3, 2)));
    assert_eq!(state.pane_cursor(99), None);
}

#[test]
fn composed_pane_width_follows_the_layout_not_the_content() {
    // 서버가 준 폭이 권위다. 내용 길이로 폭을 추정하면 분할 화면이 어긋난다.
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 3)]));
    state.apply(screen_msg(1, "긴 내용이 들어온다"));
    let lines = state.compose_pane(1).unwrap();
    assert_eq!(
        crate::compose::display_width(&lines[0]),
        3,
        "폭은 배치가 정한다"
    );
}

// ---- 블록(§10-13) ----------------------------------------------------------

fn blocks_msg(pane: i64, entries: serde_json::Value) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "blocks", "pane": pane, "blocks": entries
    }))
    .unwrap()
}

#[test]
fn blocks_are_stored_per_pane() {
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 10), (2, 10)]));
    assert!(!state.has_blocks(), "셸 통합이 없으면 블록이 없다");

    assert!(state.apply(blocks_msg(
        1,
        serde_json::json!([{"cmd": "ls", "state": "done", "exit": 0}])
    )));
    assert_eq!(state.blocks(1).len(), 1);
    assert!(state.blocks(2).is_empty(), "다른 패널에 새면 안 된다");
    assert!(state.has_blocks());
}

#[test]
fn identical_block_list_does_not_request_a_repaint() {
    let mut state = SessionState::new();
    let msg = || blocks_msg(1, serde_json::json!([{"cmd": "ls", "state": "done"}]));
    assert!(state.apply(msg()));
    assert!(!state.apply(msg()), "안 바뀌었으면 다시 그릴 이유가 없다");
}

#[test]
fn blocks_of_a_closed_pane_are_dropped_with_its_screen() {
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 10), (2, 10)]));
    state.apply(blocks_msg(2, serde_json::json!([{"cmd": "x", "state": "done"}])));
    assert_eq!(state.blocks(2).len(), 1);

    state.apply(layout_msg(&[(1, 10)]));   // 패널 2가 닫혔다
    assert!(state.blocks(2).is_empty(), "사라진 패널의 블록은 버린다");
}

#[test]
fn active_blocks_follow_the_active_pane() {
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 10), (2, 10)]));
    state.apply(blocks_msg(2, serde_json::json!([{"cmd": "여기", "state": "done"}])));
    // layout_msg 는 active 를 패널 수로 준다(= 2).
    assert_eq!(state.active_blocks().len(), 1);
    assert_eq!(state.active_blocks()[0].command, "여기");
}

#[test]
fn blocks_without_a_layout_are_still_kept() {
    // 서버가 layout 보다 blocks 를 먼저 보내는 순간이 있을 수 있다.
    let mut state = SessionState::new();
    state.apply(blocks_msg(7, serde_json::json!([{"cmd": "a", "state": "done"}])));
    assert_eq!(state.blocks(7).len(), 1);
    assert!(state.active_blocks().is_empty(), "활성 패널을 아직 모른다");
}

// ---- 패널 경계선(P3 잔여) ----------------------------------------------------

/// 테두리가 있는 배치. `box` 는 내용보다 한 칸씩 큰 바깥 사각형이다(서버 규약).
fn bordered_layout(panes: &[(i64, [u16; 4])], active: i64) -> ServerMessage {
    let panes: Vec<serde_json::Value> = panes
        .iter()
        .map(|(id, [bx, by, bw, bh])| {
            serde_json::json!({
                "id": id, "x": bx + 1, "y": by + 1, "w": bw - 2, "h": bh - 2,
                "box": [bx, by, bw, bh], "title": "shell",
                "active": *id == active,
            })
        })
        .collect();
    serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 20, "rows": 8, "panes": panes,
        "active": active, "bordered": true
    }))
    .unwrap()
}

// ── P3 — 플러그인이 얹는 글자(셀 기여) ──────────────────────────────────────────

#[test]
fn plugin_cell_runs_are_painted_where_the_server_said() {
    // 시계가 이 길로 온다. 우리는 어느 폰트를 고르고 어디에 중앙 정렬하는지 **모른다** —
    // 그건 플러그인 한 벌의 일이고, 그 사실이 이 슬라이스의 요점이다.
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 6)]));
    state.apply(screen_msg(1, "hi"));
    let cells: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay", "dim": [],
        "runs": [{"x": 2, "y": 1, "text": "██", "style": {"bo": 1},
                  "theme": {"f": "success"}}]
    }))
    .unwrap();
    assert!(state.apply(cells), "화면이 바뀌었는데 안 바뀌었다고 했다");
    let canvas = state.composite().unwrap();
    assert!(
        canvas.row_text(1).contains("██"),
        "서버가 준 글자가 화면에 없다: {:?}",
        canvas.row_text(1)
    );
}

#[test]
fn the_semantic_colour_is_resolved_by_us_not_the_server() {
    // 색의 권위는 **이 클라의 테마**다(설계 §10 위험표). 서버는 이름만 싣는다 —
    // 달력의 '오늘'처럼 **배경**에 강조색을 까는 자리도 있어서 전경만으로는 못 나른다.
    use crate::style::{Color, NamedColor};
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 6)]));
    state.apply(screen_msg(1, "hi"));
    let cells: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay", "dim": [],
        "runs": [{"x": 0, "y": 0, "text": "6", "style": {"f": "black", "bo": 1},
                  "theme": {"b": "success"}}]
    }))
    .unwrap();
    state.apply(cells);
    let canvas = state.composite().unwrap();
    let cell = canvas.cell(0, 0).expect("칸이 없다");
    assert_eq!(
        cell.style.bg,
        Some(Color::Named(NamedColor::BrightGreen)),
        "의미 이름이 우리 테마 색으로 안 풀렸다"
    );
    assert_eq!(
        cell.style.fg,
        Some(Color::Named(NamedColor::Black)),
        "런에 실린 리터럴 글자색이 사라졌다"
    );
}

#[test]
fn a_zone_hit_carries_back_the_name_we_cannot_read() {
    // 우리는 `‹` 가 지난달인지 지난해인지 **모른다**. 아는 것은 "그 자리를 눌렀다" 뿐이고
    // 뜻은 플러그인이 정한다(설계 §4.4).
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 6)]));
    let cells: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay", "dim": [], "runs": [],
        "zones": [{"x": 4, "y": 2, "w": 2, "h": 1, "pane": 1,
                   "name": "calendar", "do": "prev"}],
        "keys": [{"key": "left", "pane": 1, "name": "calendar", "do": "prev"}]
    }))
    .unwrap();
    state.apply(cells);
    assert_eq!(
        state.overlay_zone_at(5, 2),
        Some(("calendar".to_owned(), 1, "prev".to_owned()))
    );
    assert_eq!(state.overlay_zone_at(6, 2), None, "자리 밖을 눌렀는데 맞았다고 한다");
    assert_eq!(
        state.overlay_key("left"),
        Some(("calendar".to_owned(), 1, "prev".to_owned()))
    );
    assert_eq!(state.overlay_key("right"), None, "표에 없는 키를 가져갔다");
}

#[test]
fn one_pane_holds_one_overlay() {
    // 정본 규칙이다 — 시계를 켜면 그 패널의 달력이 닫힌다. 밀려난 쪽을 **서버에도**
    // 알려야 두 그림이 겹쳐 오지 않는다.
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 6)]));
    let first = state.toggle_overlay("calendar").expect("패널이 있는데 못 켰다");
    assert!(first.on && first.closed.is_none());
    let second = state.toggle_overlay("clock").expect("못 켰다");
    assert!(second.on);
    assert_eq!(second.closed.as_deref(), Some("calendar"), "밀려난 것을 안 알린다");
    // 같은 것을 다시 누르면 끈다(그때는 밀려난 것이 없다).
    let third = state.toggle_overlay("clock").expect("못 껐다");
    assert!(!third.on && third.closed.is_none());
}

#[test]
fn a_dimmed_pane_keeps_its_text_but_loses_its_brightness() {
    // 시계는 패널을 **덮되 뒤가 비쳐 보인다**. 딤은 새 글자가 아니라 있는 셀을 바꾸는
    // 일이라 런으로 못 나른다 — 서버는 "어느 패널"만 말하고 계산은 우리가 한다.
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 6)]));
    state.apply(screen_msg(1, "hi"));
    let before = state.composite().unwrap();
    let bright = before.cell(0, 0).cloned();
    let cells: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay", "dim": [1], "runs": []
    }))
    .unwrap();
    state.apply(cells);
    let after = state.composite().unwrap();
    assert!(after.text().contains("hi"), "딤이 글자를 지웠다:\n{}", after.text());
    assert_ne!(
        after.cell(0, 0).cloned(),
        bright,
        "덮으라고 했는데 뒤가 그대로 밝다"
    );
}

#[test]
fn a_bordered_pane_is_framed() {
    let mut state = SessionState::new();
    state.apply(bordered_layout(&[(1, [0, 0, 10, 4])], 1));
    state.apply(screen_msg(1, "hi"));
    let canvas = state.composite().unwrap();
    let head = |y: usize| canvas.row_text(y).chars().take(10).collect::<String>();
    assert_eq!(head(0), "┌────────┐");
    assert!(canvas.row_text(1).starts_with("│hi"), "{}", canvas.row_text(1));
    assert_eq!(head(3), "└────────┘");
}

#[test]
fn no_box_means_no_frame() {
    // 패널 하나 + single-border off — 내용이 화면을 꽉 쓴다. 없는 테두리를 그리면
    // 서버가 준 내용 한 줄이 가려진다.
    let mut state = SessionState::new();
    state.apply(layout_msg(&[(1, 6)]));
    state.apply(screen_msg(1, "hi"));
    let canvas = state.composite().unwrap();
    assert!(
        !canvas.text().contains('┌'),
        "box 가 없는데 테두리를 그렸다:\n{}",
        canvas.text()
    );
}

#[test]
fn touching_panes_merge_their_shared_edge() {
    // 좌우로 붙은 두 패널의 맞닿는 변은 ┬·┴ 로 이어져야 한 장의 격자처럼 보인다.
    let mut state = SessionState::new();
    state.apply(bordered_layout(&[(1, [0, 0, 10, 4]), (2, [9, 0, 10, 4])], 1));
    let canvas = state.composite().unwrap();
    let top = canvas.row_text(0);
    assert_eq!(
        top.chars().nth(9),
        Some('┬'),
        "맞닿은 상단 모서리가 안 합쳐졌다: {top}"
    );
    assert_eq!(canvas.row_text(3).chars().nth(9), Some('┴'));
}

#[test]
fn the_active_pane_owns_the_shared_edge_colour() {
    // 나중에 그린 쪽 색이 남는다 = 활성 패널의 테두리가 이웃 위로 온다.
    // **활성 패널을 목록 앞에 둔다** — 선언 순서대로 그리면 뒤의 비활성 패널이 공유
    // 변을 덮어써서 이 단언이 깨진다(순서가 실제로 계약임을 이 배치가 증명한다).
    let mut state = SessionState::new();
    state.apply(bordered_layout(&[(1, [0, 0, 10, 4]), (2, [9, 0, 10, 4])], 1));
    let canvas = state.composite().unwrap();
    // 맞닿는 변은 x=9 다 — 왼쪽 패널(비활성)도 같은 자리에 그린다.
    let shared = canvas.cell(9, 1).expect("맞닿는 변이 없다");
    assert_eq!(shared.ch, '│');
    assert!(
        shared.style.bold,
        "활성 패널이 공유 변을 가져가야 한다(파이썬 클라와 같은 순서)"
    );
    assert!(
        !canvas.cell(18, 1).unwrap().style.bold,
        "비활성 패널의 바깥 변까지 활성색이 되면 안 된다"
    );
}

#[test]
fn default_title_is_not_drawn_but_a_renamed_one_is() {
    // 서버는 모든 패널의 title 을 보낸다 — 기본값 'shell' 까지 그리면 잡음이다.
    let mut state = SessionState::new();
    state.apply(bordered_layout(&[(1, [0, 0, 12, 4])], 1));
    assert!(!state.composite().unwrap().text().contains("shell"));

    let renamed: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 20, "rows": 8, "active": 1, "bordered": true,
        "panes": [{"id": 1, "x": 1, "y": 1, "w": 10, "h": 2,
                   "box": [0, 0, 12, 4], "title": "빌드", "active": true}]
    }))
    .unwrap();
    state.apply(renamed);
    let top = state.composite().unwrap().row_text(0);
    assert!(top.contains("빌드"), "이름을 바꾸면 테두리에 보인다: {top}");
    // 모서리는 **셀 좌표**로 본다 — 이름이 넓은 글자라 글자 수와 칸 수가 다르다.
    let canvas = state.composite().unwrap();
    assert_eq!(canvas.cell(0, 0).unwrap().ch, '┌', "{top}");
    assert_eq!(canvas.cell(11, 0).unwrap().ch, '┐', "이름이 모서리를 밀어냈다: {top}");
}

#[test]
fn titlebars_are_drawn_with_a_fill_to_the_right() {
    let msg: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 20, "rows": 8, "active": 1, "border_status": true,
        "panes": [{"id": 1, "x": 0, "y": 1, "w": 20, "h": 6, "active": true}],
        "titlebars": [{"x": 0, "y": 0, "w": 20, "title": "빌드", "active": true}]
    }))
    .unwrap();
    let mut state = SessionState::new();
    state.apply(msg);
    let bar = state.composite().unwrap().row_text(0);
    assert!(bar.starts_with(" 빌드 "), "제목줄: {bar}");
    assert!(bar.ends_with('─'), "라벨 뒤는 채움선: {bar}");
}

// ── 트리·버퍼 목록(패리티 G3b) ───────────────────────────────────────────────
//
// 펴는 규칙이 proto 에 있는 이유: 목록 화면은 **줄 번호로 고른다**. 두 뷰가 각자 펴면
// GUI 에서 고른 줄과 TUI 에서 고른 줄이 다른 것을 가리킨다 — 조용한 어긋남이다.

fn tree_msg(json: serde_json::Value) -> ServerMessage {
    serde_json::from_value(json).unwrap()
}

#[test]
fn a_single_session_tree_starts_at_the_tabs() {
    // 세션이 하나뿐일 때 세션 줄은 **고를 수도 없는 줄**을 목록 맨 위에 얹을 뿐이다.
    let mut state = SessionState::new();
    state.apply(tree_msg(serde_json::json!({
        "t": "tree",
        "sessions": [{"name": "0", "windows": [
            {"index": 0, "name": "쉘", "active": true, "panes": [{"id": 1, "cmd": "bash"}]}
        ]}]
    })));
    let rows = state.tree_rows();
    assert_eq!(rows.len(), 1, "{rows:?}");
    assert_eq!(rows[0].depth, 0);
    assert_eq!(rows[0].window, Some(0));
    assert!(rows[0].active);
}

#[test]
fn panes_are_listed_only_when_the_tab_is_split() {
    // 패널이 하나면 그 줄은 탭 줄과 같은 말을 한다(파이썬 스위처도 2개 이상만 보인다).
    let mut state = SessionState::new();
    state.apply(tree_msg(serde_json::json!({
        "t": "tree",
        "sessions": [{"name": "0", "windows": [
            {"index": 0, "name": "쉘", "panes": [{"id": 1, "cmd": "bash"}]},
            {"index": 1, "name": "일", "panes": [
                {"id": 2, "cmd": "vim", "title": "메모"},
                {"id": 3, "cmd": "ssh", "remote": true}
            ]}
        ]}]
    })));
    let rows = state.tree_rows();
    assert_eq!(rows.len(), 4, "{rows:?}");
    assert_eq!(rows[1].window, Some(1));
    assert_eq!(rows[2].pane, Some(2));
    assert!(rows[2].label.contains("vim") && rows[2].label.contains("메모"));
    // 원격 여부는 **서버 판정**을 그대로 쓴다 — 클라가 이름으로 짐작하지 않는다.
    assert!(rows[3].label.contains("ssh"));
    assert!(rows[3].label.contains("[ssh]"), "{:?}", rows[3].label);
}

#[test]
fn several_sessions_get_a_header_row_that_cannot_be_picked() {
    let mut state = SessionState::new();
    state.apply(tree_msg(serde_json::json!({
        "t": "tree",
        "sessions": [
            {"name": "일", "windows": [{"index": 0, "name": "a", "panes": [{"id": 1}]}]},
            {"name": "놀이", "windows": [{"index": 1, "name": "b", "panes": [{"id": 2}]}]}
        ]
    })));
    let rows = state.tree_rows();
    assert_eq!(rows.len(), 4);
    assert_eq!(rows[0].window, None, "세션 줄이 고를 수 있게 됐다");
    assert_eq!(rows[1].depth, 1, "세션이 여럿이면 탭이 한 칸 들여쓴다");
}

#[test]
fn a_pinned_tab_is_marked_in_the_tree() {
    let mut state = SessionState::new();
    state.apply(tree_msg(serde_json::json!({
        "t": "tree",
        "sessions": [{"name": "0", "windows": [
            {"index": 0, "name": "고정", "pinned": true, "panes": [{"id": 1}]}
        ]}]
    })));
    assert!(state.tree_rows()[0].label.contains('*'), "핀 표식이 없다");
}

#[test]
fn no_tree_means_no_rows_not_a_panic() {
    // 화면은 **요청을 보낸 직후** 열린다 — 회신이 오기 전 한 프레임은 목록이 비어 있다.
    assert!(SessionState::new().tree_rows().is_empty());
}

#[test]
fn buffers_are_kept_as_the_server_numbered_them() {
    // 붙여넣기 명령이 그 번호를 그대로 쓴다 — 클라가 다시 매기면 엉뚱한 버퍼가 붙는다.
    let mut state = SessionState::new();
    state.apply(tree_msg(serde_json::json!({
        "t": "buffers",
        "items": [{"i": 0, "preview": "첫 줄"}, {"i": 1, "preview": "둘째"}]
    })));
    assert_eq!(state.buffers().len(), 2);
    assert_eq!(state.buffers()[1].index, 1);
    assert_eq!(state.buffers()[0].preview, "첫 줄");
}

// ── 상태줄 표식(패리티 G6) ───────────────────────────────────────────────────
//
// 서버는 이 값들을 `status` 에 매번 실어 보냈고, 클라는 **탭 목록만 꺼내 쓰고 나머지를
// 버리고 있었다**. 그래서 줌·동기화 안에 있는지 화면에서 알 수 없었다.

fn status_with(extra: serde_json::Value) -> ServerMessage {
    let mut obj = serde_json::json!({"t": "status", "windows": []});
    for (k, v) in extra.as_object().unwrap() {
        obj[k] = v.clone();
    }
    serde_json::from_value(obj).unwrap()
}

#[test]
fn the_flags_come_from_the_status_message() {
    let mut state = SessionState::new();
    state.apply(status_with(serde_json::json!({
        "zoomed": true, "sync": true, "pane_title": "빌드",
        "monitor_activity": true, "monitor_bell": false, "auto_rename": true
    })));
    let flags = state.flags();
    assert!(flags.zoomed && flags.sync && flags.monitor_activity);
    assert!(!flags.monitor_bell);
    assert_eq!(flags.pane_title, "빌드");
}

#[test]
fn exit_empty_is_unknown_until_the_server_says_it() {
    // ★ G9s — 이 칸은 2026-07-30 에야 서버 status 에 실렸다. 안 온 것을 기본
    //   `false` 로 읽으면 서버 기본(on)과 **반대인 거짓말**이 된다 — 모르면 None.
    let mut state = SessionState::new();
    state.apply(status_with(serde_json::json!({})));
    assert_eq!(state.flags().exit_empty, None, "안 왔는데 안다고 했다");
    state.apply(status_with(serde_json::json!({"exit_empty": true})));
    assert_eq!(state.flags().exit_empty, Some(true));
    state.apply(status_with(serde_json::json!({"exit_empty": false})));
    assert_eq!(state.flags().exit_empty, Some(false));
}

#[test]
fn only_the_flags_that_are_on_get_a_badge() {
    // 꺼진 것까지 적으면 줄이 길어져 정작 켜진 것이 눈에 안 띈다.
    let mut state = SessionState::new();
    state.apply(status_with(serde_json::json!({"zoomed": true})));
    assert_eq!(state.flags().badges(), vec!["[줌]"]);
}

#[test]
fn the_sync_badge_is_always_there_when_sync_is_on() {
    // ★ 동기화는 **입력이 복제되는 상태**다. 모르고 치면 모든 패널에서 같은 명령이 돈다 —
    // 표식 중 가장 위험한 것이라 빠지면 안 된다.
    let mut state = SessionState::new();
    state.apply(status_with(serde_json::json!({"sync": true})));
    assert!(state.flags().badges().contains(&"[동기화]"), "{:?}", state.flags());
}

#[test]
fn a_status_that_only_changes_a_flag_still_repaints() {
    // 탭 목록은 그대로인데 줌만 바뀌는 경우가 있다 — 그때 다시 안 그리면 표식이 안 뜬다.
    let mut state = SessionState::new();
    state.apply(status_with(serde_json::json!({})));
    assert!(
        state.apply(status_with(serde_json::json!({"zoomed": true}))),
        "표식만 바뀌었을 때 repaint 를 안 걸었다"
    );
}

#[test]
fn an_old_server_without_the_fields_is_fine() {
    // 구버전 서버는 이 키들을 안 보낸다 — 없으면 전부 꺼진 것으로 본다.
    let mut state = SessionState::new();
    state.apply(status_with(serde_json::json!({})));
    assert!(state.flags().badges().is_empty());
}

// ── 비활성 패널 딤(패리티 G6b) ────────────────────────────────────────────────

#[test]
fn only_the_inactive_panes_are_dimmed() {
    // ★ 활성 패널까지 흐려지면 이 기능의 뜻이 정확히 뒤집힌다.
    let mut state = SessionState::new();
    state.apply(layout_two_panes());
    state.apply(dim_screen(1, "AAAA"));
    state.apply(dim_screen(2, "BBBB"));
    state.set_inactive_dim(true, 0.5);
    let canvas = state.composite().expect("합성");
    let style_at = |x: usize| canvas.cell(x, 0).map(|c| c.style);
    // 패널 1 이 활성이다(layout_two_panes 의 active).
    assert_ne!(style_at(0), style_at(40), "두 패널의 스타일이 같다 — 딤이 안 걸렸다");
}

#[test]
fn turning_the_dim_off_leaves_both_panes_alone() {
    let mut state = SessionState::new();
    state.apply(layout_two_panes());
    state.apply(dim_screen(1, "AAAA"));
    state.apply(dim_screen(2, "AAAA"));
    state.set_inactive_dim(false, 0.5);
    let canvas = state.composite().expect("합성");
    assert_eq!(
        canvas.cell(0, 0).map(|c| c.style),
        canvas.cell(40, 0).map(|c| c.style),
        "꺼 뒀는데 흐려졌다"
    );
}

fn layout_two_panes() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 4, "active": 1,
        "panes": [
            {"id": 1, "x": 0, "y": 0, "w": 40, "h": 4, "title": "a"},
            {"id": 2, "x": 40, "y": 0, "w": 40, "h": 4, "title": "b"},
        ]
    }))
    .unwrap()
}


/// 딤 오라클이 쓰는 화면 한 장(글자 하나로 채운다).
fn dim_screen(pane: i64, text: &str) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": pane,
        "rows": [[[text, {"fg": "white"}]]],
    }))
    .unwrap()
}

// ── 알림 이력(패리티 G6c) ─────────────────────────────────────────────────────

#[test]
fn a_notice_is_not_dropped_on_the_floor() {
    // ★ 이 변형이 없던 동안 `remote-attach` 실패는 조용히 버려졌다 — 사용자에게는
    // "명령을 쳤는데 아무 일도 안 남"으로만 보였다.
    let mut state = SessionState::new();
    assert!(state.apply(notice("붙였다", Some("ok"))));
    assert_eq!(state.notices().len(), 1);
    assert_eq!(state.notices().next().map(|n| n.severity), Some(Severity::Ok));
}

#[test]
fn the_newest_notice_is_first() {
    let mut state = SessionState::new();
    state.apply(notice("하나", None));
    state.apply(notice("둘", None));
    assert_eq!(
        state.notices().map(|n| n.text.as_str()).collect::<Vec<_>>(),
        vec!["둘", "하나"]
    );
}

#[test]
fn only_errors_take_the_status_line() {
    // 모든 알림이 상태줄을 차지하면 정작 오류가 묻힌다.
    let mut state = SessionState::new();
    state.apply(notice("그냥 알림", Some("info")));
    assert_eq!(state.last_error(), None);
    state.apply(notice("실패했다", Some("error")));
    assert_eq!(state.last_error(), Some("실패했다"));
}

#[test]
fn an_unknown_severity_is_info() {
    // 서버가 등급을 늘려도 클라가 안 깨져야 한다.
    let mut state = SessionState::new();
    state.apply(notice("새 등급", Some("catastrophe")));
    assert_eq!(state.notices().next().map(|n| n.severity), Some(Severity::Info));
}

#[test]
fn the_history_does_not_grow_forever() {
    // 오래 붙어 있는 클라에서 이 목록만 끝없이 자란다.
    let mut state = SessionState::new();
    for i in 0..(NOTICE_LIMIT + 50) {
        state.apply(notice(&format!("{i}"), None));
    }
    assert_eq!(state.notices().len(), NOTICE_LIMIT);
    // 남은 것은 **새것**이다.
    assert_eq!(
        state.notices().next().map(|n| n.text.clone()),
        Some(format!("{}", NOTICE_LIMIT + 49))
    );
}

#[test]
fn client_side_errors_land_in_the_same_history() {
    // 사용자에게는 "방금 한 것이 안 됐다" 한 가지다 — 자리가 둘이면 한쪽은 아무도 안 본다.
    let mut state = SessionState::new();
    state.note_error("설정을 저장하지 못했다");
    assert_eq!(state.notices().len(), 1);
    assert_eq!(state.notices().next().map(|n| n.severity), Some(Severity::Error));
}

fn notice(text: &str, sev: Option<&str>) -> ServerMessage {
    let mut msg = serde_json::json!({"t": "notice", "text": text});
    if let Some(sev) = sev {
        msg["sev"] = serde_json::json!(sev);
    }
    serde_json::from_value(msg).unwrap()
}

#[test]
fn pasting_drops_the_pane_borders() {
    // ★ OS 네이티브 선택으로 긁으면 테두리(`│`)가 딸려 온다 — 그대로 붙이면 명령줄이
    // 망가진다. 지우는 것은 박스드로잉 블록뿐이고 나머지 글자는 손대지 않는다.
    assert_eq!(crate::strip_box_drawing("ls │ grep x"), "ls  grep x");
    assert_eq!(crate::strip_box_drawing("┌─┐└┘"), "");
    assert_eq!(crate::strip_box_drawing("한글 | pipe"), "한글 | pipe");
}

#[test]
fn the_restart_check_reply_becomes_sorted_lines() {
    // ★ 이름순으로 못박는다 — 서버가 필드를 늘려도 줄 자리가 안 흔들린다.
    let mut state = SessionState::new();
    assert!(state.restart_check().is_empty(), "묻기 전에는 비어 있다");
    let msg: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "restart_check",
        "serialize_ok": true,
        "has_sessions": true,
        "panes": 3
    }))
    .unwrap();
    state.apply(msg);
    let rows = state.restart_check();
    assert_eq!(rows.len(), 3);
    assert!(rows[0].starts_with("has_sessions"), "{rows:?}");
    assert!(rows[1].starts_with("panes"), "{rows:?}");
    assert!(rows[2].starts_with("serialize_ok"), "{rows:?}");
}

#[test]
fn an_unknown_restart_check_field_is_kept() {
    // 모르는 필드를 버리면 서버가 늘렸을 때 그 줄이 안 보인다.
    let mut state = SessionState::new();
    let msg: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "restart_check", "새필드": "값"
    }))
    .unwrap();
    state.apply(msg);
    assert_eq!(state.restart_check().len(), 1);
}

// ── 원격 탭 머지 후보(패리티 G8n) ─────────────────────────────────────────────

/// 병합 탭바 한 판 — 로컬 하나 + `boxA` 둘 + `boxB` 하나, 활성은 `boxA` 의 첫 탭.
fn federated_tabs() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "status",
        "active_pane": 7,
        "windows": [
            {"index": 0, "name": "local", "active": false},
            {"index": 1, "name": "⇄boxA:build", "active": true, "remote": true},
            {"index": 2, "name": "⇄boxA:logs", "active": false, "remote": true},
            {"index": 3, "name": "⇄boxB:other", "active": false, "remote": true},
        ]
    }))
    .unwrap()
}

#[test]
fn the_candidates_are_the_same_host_only() {
    // ★ 다른 호스트의 탭을 실으면 고른 순간 서버가 못 붙인다(원격끼리라야 한다).
    let mut state = SessionState::new();
    state.apply(federated_tabs());
    assert_eq!(state.active_remote_host(), Some("boxA"));
    let picks: Vec<usize> = state.merge_candidates().iter().map(|(i, _)| *i).collect();
    assert_eq!(picks, vec![2], "boxA 의 다른 탭 하나뿐이어야 한다");
}

#[test]
fn the_active_tab_is_not_a_candidate() {
    // 자기 자신에 붙일 수는 없다.
    let mut state = SessionState::new();
    state.apply(federated_tabs());
    assert!(state.merge_candidates().iter().all(|(i, _)| *i != 1));
}

#[test]
fn a_local_tab_has_no_candidates() {
    // 지금 탭이 원격이 아니면 합칠 것이 없다 — 빈 목록을 띄우는 대신 화면을 안 연다.
    let mut state = SessionState::new();
    let msg: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "local", "active": true}]
    }))
    .unwrap();
    state.apply(msg);
    assert_eq!(state.active_remote_host(), None);
    assert!(state.merge_candidates().is_empty());
}

#[test]
fn the_candidate_line_shows_the_one_based_number() {
    // 파이썬과 같은 표기(`{i+1}: {name}`) — 탭바에 보이는 번호와 맞아야 한다.
    let mut state = SessionState::new();
    state.apply(federated_tabs());
    assert_eq!(state.merge_candidates()[0].1, "3: ⇄boxA:logs");
}

// ── 팝업 안 앱 마우스 패스스루(popup.mouse) — mouse_pane_at 의 팝업 우선 판정 ─────

/// 뒤 패널(1)이 추적을 켠 채 팝업이 떠 있는 판. `popup_mouse` 로 팝업의 광고를 정한다.
fn popup_mouse_layout(popup_mouse: u8, sgr: bool) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24,
                   "mouse": 1, "mouse_sgr": true}],
        "popup": {"id": 99, "x": 10, "y": 5, "w": 40, "h": 10,
                  "cx": 11, "cy": 6, "cw": 38, "ch": 8,
                  "mouse": popup_mouse, "mouse_sgr": sgr}
    }))
    .unwrap()
}

#[test]
fn the_popup_app_that_asked_gets_the_mouse_target() {
    let mut state = SessionState::new();
    state.apply(popup_mouse_layout(2, true));
    let target = state.mouse_pane_at(12, 7).expect("팝업이 대상이어야 한다");
    assert_eq!(target.id, 99);
    // rect 는 **내용** 사각형이다 — 상자(x/y/w/h)로 인코딩하면 좌표가 한 칸씩 민다.
    assert_eq!(target.rect, (11, 6, 38, 8));
    assert!(target.mode.wants_drag(), "track=2 가 광고대로 안 왔다");
    assert!(target.mode.sgr);
}

#[test]
fn the_popup_border_and_outside_are_not_the_apps() {
    let mut state = SessionState::new();
    state.apply(popup_mouse_layout(2, true));
    // 테두리(상자 시작)와 팝업 밖 — 밖은 **뒤 패널이 추적 중이어도** 막힌다(모달).
    assert!(state.mouse_pane_at(10, 5).is_none(), "테두리가 앱의 것이 됐다");
    assert!(state.mouse_pane_at(60, 20).is_none(), "가려진 앱이 마우스를 받는다");
}

#[test]
fn a_popup_that_never_asked_blocks_passthrough_entirely() {
    // 팝업 앱이 추적을 안 켰다: 팝업 안도 None(안 켠 앱에 보내면 글자로 박힌다),
    // 뒤 패널도 None(가려져 있다) — 곧 popup.mouse 이전의 종전 동작이다.
    let mut state = SessionState::new();
    state.apply(popup_mouse_layout(0, false));
    assert!(state.mouse_pane_at(12, 7).is_none());
    assert!(state.mouse_pane_at(60, 20).is_none());
}

#[test]
fn an_old_server_without_the_popup_fields_means_no_passthrough() {
    // 구버전 서버는 popup 에 mouse 칸이 없다 — serde 기본(0/false)이 "패스스루 없음"
    // 이라 종전 동작 그대로다(기본값이 거짓말이 되는 exit-empty 류가 아니다).
    let mut state = SessionState::new();
    let msg: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24,
                   "mouse": 1, "mouse_sgr": true}],
        "popup": {"id": 99, "x": 10, "y": 5, "w": 40, "h": 10,
                  "cx": 11, "cy": 6, "cw": 38, "ch": 8}
    }))
    .unwrap();
    state.apply(msg);
    assert!(state.mouse_pane_at(12, 7).is_none());
}

#[test]
fn without_a_popup_the_pane_target_carries_rect_and_mode() {
    // 일반 패널 경로가 MouseTarget 으로 바뀌어도 값이 그대로임을 못박는다(뷰가 이
    // 값으로 인코딩한다 — rect·mode 가 어긋나면 리포트 좌표가 조용히 민다).
    let mut state = SessionState::new();
    let msg: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 7, "x": 3, "y": 2, "w": 30, "h": 10,
                   "mouse": 1, "mouse_sgr": false}]
    }))
    .unwrap();
    state.apply(msg);
    let target = state.mouse_pane_at(5, 4).expect("추적 중인 패널");
    assert_eq!(target.id, 7);
    assert_eq!(target.rect, (3, 2, 30, 10));
    assert!(target.mode.wants_mouse() && !target.mode.sgr);
}

#[test]
fn a_notice_line_says_when_and_who_not_just_what() {
    // ★ 정본 알림 이력과 같은 열 구성이다(대조 문서 §8): 기호 · 시각 · 출처 · 글.
    //   기호와 글만 있으면 "언제 무엇이 있었나"를 이력에서 못 읽는다 — 그게 이력의
    //   존재 이유다.
    let mut state = SessionState::new();
    state.note_notice("설정을 다시 읽었다");
    state.apply(ServerMessage::Error { msg: "그런 탭 없음".into() });

    let lines: Vec<String> = state.notices().map(|n| n.line()).collect();
    let server = lines.iter().find(|l| l.contains("그런 탭 없음")).expect("서버 알림이 없다");
    let local = lines.iter().find(|l| l.contains("설정을 다시 읽었다")).expect("클라 알림이 없다");

    // 출처가 갈린다 — 서버가 낸 것과 클라가 낸 것을 눈으로 가를 수 있어야 한다.
    assert!(server.contains("server"), "서버 출처가 없다: {server}");
    assert!(local.contains("local"), "클라 출처가 없다: {local}");
    // 시각(HH:MM:SS)이 있다.
    let has_clock = |s: &str| {
        s.split_whitespace()
            .any(|w| w.len() == 8 && w.chars().filter(|&c| c == ':').count() == 2)
    };
    assert!(has_clock(server), "시각이 없다: {server}");
    assert!(has_clock(local), "시각이 없다: {local}");
    // 등급 기호는 그대로 맨 앞이다.
    assert!(server.starts_with('✕'), "오류 기호가 앞에 없다: {server}");
}

#[test]
fn the_claude_badge_now_comes_from_the_plugin_not_from_us() {
    // ★ 이 오라클은 **옮겨진 것**이다(M4 P6 후반). 종전에는 우리가 날 필드로
    //   `opus-5 · 12%/5h` 를 조립했고 그 문자열을 여기서 쟀다 — 그 조립이 정본과
    //   두 벌이었다는 것이 이 슬라이스가 고친 것이다. 지우기만 하면 "상태줄에 Claude
    //   표식이 뜬다"를 아무도 안 재게 되므로, 같은 축을 **새 경로**에서 잰다.
    let mut state = SessionState::default();
    assert!(state.plugin_badges().is_empty(), "재료가 없는데 배지가 떴다");
    state.apply(status_with(serde_json::json!({
        "plugin_badges": [
            {"name": "claude-code", "text": "opus-5", "theme": {"b": "secondary"}},
            {"name": "claude-code", "text": "12%/5h 사용", "theme": {"b": "secondary"},
             "i18n": {"text": {"fmt": "{pct}%/5h 사용", "args": {"pct": "12"}}}}
        ]
    })));
    let badges = state.plugin_badges();
    assert_eq!(badges.len(), 2, "{badges:?}");
    assert_eq!(badges[0].say(), "opus-5");
    // 재료가 왔으니 **우리 로케일**로 지어진다(서버가 지은 글이 아니라).
    // ⚠ 종전에는 여기서 전역을 뒤집고 **함수 안의 `Mutex`** 로 감쌌는데, 그 잠금은
    // 자기 자신만 직렬화한다 — 읽는 쪽(같은 이진의 다른 테스트 수백)이 같은 잠금을
    // 들 리가 없다. 실제로 그 창에 걸린 `the_sync_badge_is_always_there_when_sync_is_on`
    // 이 `[동기화]` 대신 영어를 보고 떨어졌다(2026-08-02). 이제 덮어쓰기는 이 스레드
    // 밖으로 안 나간다(`base::i18n::with_locale`).
    let english = base::i18n::with_locale("en", || badges[1].say());
    assert_eq!(english, "12%/5h used", "서버가 지은 한국어가 그대로 샜다");
}

#[test]
fn a_plugin_badge_arrives_as_data_and_keeps_its_semantic_colour() {
    // 종전에는 이 자리가 통째로 비어 있었다 — REC 는 파이썬 훅으로만 그려져 우리에겐
    // 없었다(`base::chrome` 의 "플러그인이 채우는 칸이라 우리에게는 없다"). 이제 서버가
    // 자료로 준다. 색은 **이름**으로 오고(hex 아님) 푸는 것은 이 클라의 표다.
    let mut state = SessionState::new();
    state.apply(status_with(serde_json::json!({
        "plugin_badges": [{
            "name": "rec", "text": " REC ",
            "style": {"bo": 1, "f": "white"}, "theme": {"b": "error"}
        }]
    })));
    let badges = state.plugin_badges();
    assert_eq!(badges.len(), 1, "{badges:?}");
    assert_eq!(badges[0].name, "rec");
    assert_eq!(badges[0].text, " REC ");
    assert_eq!(badges[0].theme.b.as_deref(), Some("error"));
    // 그 이름이 런과 **같은 표**로 풀려야 한다 — 배지만 다른 표를 두면 같은 이름이
    // 두 자리에서 다른 색이 된다.
    assert_eq!(
        crate::session::theme::color("error"),
        Some(crate::style::Color::Named(crate::style::NamedColor::BrightRed))
    );
}

#[test]
fn a_status_without_plugin_badges_means_there_are_none() {
    // ★ 이웃 필드들과 **반대 규칙**이라 못박는다. 저것들(claude_model 등)은 델타에 안
    // 실릴 수 있어 "안 왔으면 지킨다"가 맞지만, 배지는 서버가 매 status 마다 다시 만들고
    // **비면 키를 뺀다** — 여기서 지키면 캡처를 끈 뒤에도 REC 가 영영 남는다.
    let mut state = SessionState::new();
    state.apply(status_with(serde_json::json!({
        "plugin_badges": [{"name": "rec", "text": " REC "}]
    })));
    assert_eq!(state.plugin_badges().len(), 1);
    state.apply(status_with(serde_json::json!({"zoomed": false})));
    assert!(
        state.plugin_badges().is_empty(),
        "배지 키가 안 왔는데 옛 배지가 남았다 — 캡처를 꺼도 REC 가 사라지지 않는다"
    );
}
