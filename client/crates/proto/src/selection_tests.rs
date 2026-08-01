//! 선택 기하 — 서버 `extract_range` 와 **같은 모양**인지.

use super::*;

fn sel(a: (usize, u16), b: (usize, u16)) -> Selection {
    let mut s = Selection::new(1, Point::new(a.0, a.1));
    s.extend_to(Point::new(b.0, b.1));
    s
}

#[test]
fn a_single_line_selection_is_the_column_range() {
    let s = sel((10, 3), (10, 6));
    assert!(!s.contains(Point::new(10, 2)));
    assert!(s.contains(Point::new(10, 3)), "시작 열은 포함이다");
    assert!(s.contains(Point::new(10, 6)), "끝 열도 포함이다");
    assert!(!s.contains(Point::new(10, 7)));
    assert!(!s.contains(Point::new(9, 4)));
    assert!(!s.contains(Point::new(11, 4)));
}

#[test]
fn the_middle_lines_are_taken_whole() {
    // 사각형 선택이 아니다. 가운데 줄은 열과 무관하게 전부 든다 — 서버의
    // `extract_range` 가 `sx=0, ex=cols-1` 로 뽑는 바로 그 줄들이다. 여기서 사각형으로
    // 그리면 사용자가 고른 모양과 복사된 텍스트가 어긋난다.
    let s = sel((10, 40), (12, 2));
    assert!(s.contains(Point::new(11, 0)));
    assert!(s.contains(Point::new(11, 79)));
    // 첫 줄은 시작 열 **뒤쪽만**.
    assert!(!s.contains(Point::new(10, 39)));
    assert!(s.contains(Point::new(10, 40)));
    // 마지막 줄은 끝 열 **앞쪽만**.
    assert!(s.contains(Point::new(12, 2)));
    assert!(!s.contains(Point::new(12, 3)));
}

#[test]
fn dragging_upward_selects_the_same_cells_as_dragging_down() {
    // 위로 끄는 사용자가 아래로 끄는 사용자와 다른 것을 얻으면 안 된다.
    let down = sel((10, 40), (12, 2));
    let up = sel((12, 2), (10, 40));
    for line in 9..14 {
        for col in [0u16, 2, 3, 39, 40, 79] {
            let p = Point::new(line, col);
            assert_eq!(down.contains(p), up.contains(p), "{p:?}");
        }
    }
    assert_eq!(down.ordered(), up.ordered());
}

#[test]
fn a_press_without_a_drag_is_not_a_selection() {
    // 클릭은 포커스 이동이고 드래그는 복사다. 이 구분이 없으면 패널을 고르려는 클릭이
    // 매번 한 글자짜리 복사를 일으켜 클립보드를 덮어쓴다.
    let s = Selection::new(1, Point::new(5, 5));
    assert!(s.is_collapsed());
    let mut moved = s;
    moved.extend_to(Point::new(5, 6));
    assert!(!moved.is_collapsed());
    // 한 칸이라도 끌었으면 그 한 칸은 선택이다.
    assert!(moved.contains(Point::new(5, 5)));
    assert!(moved.contains(Point::new(5, 6)));
}

#[test]
fn the_ordered_ends_are_what_the_server_gets() {
    // 뒤집힌 채로 보내도 서버가 정렬하지만(`extract_range`), 클라가 정렬해 보내야
    // 강조와 요청이 같은 범위가 된다.
    let s = sel((12, 2), (10, 40));
    let (a, b) = s.ordered();
    assert_eq!((a.line, a.col), (10, 40));
    assert_eq!((b.line, b.col), (12, 2));
}
