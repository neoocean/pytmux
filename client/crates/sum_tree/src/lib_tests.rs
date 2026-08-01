//! 커서 의미론 오라클.
//!
//! 여기서 검증하는 것은 "Vec 으로 갈아끼워도 **관측 가능한 동작이 같은가**"이다.
//! 특히 [`SeekBias`] 경계 판정은 어긋나도 컴파일은 통과하고 리스트가 한 칸씩 밀리는
//! 형태로만 드러나므로, 경계값을 직접 고정한다.

use super::*;

/// 높이를 가진 항목 — `viewported_list` 가 쓰는 형태(개수 + 높이 두 축)의 축소판.
#[derive(Clone, Debug, PartialEq)]
struct Row {
    id: usize,
    height: usize,
}

#[derive(Clone, Debug, Default)]
struct RowSummary {
    count: usize,
    height: usize,
}

impl<'a> AddAssign<&'a RowSummary> for RowSummary {
    fn add_assign(&mut self, rhs: &'a RowSummary) {
        self.count += rhs.count;
        self.height += rhs.height;
    }
}

impl Item for Row {
    type Summary = RowSummary;
    fn summary(&self) -> RowSummary {
        RowSummary {
            count: 1,
            height: self.height,
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord)]
struct Count(usize);

impl<'a> Dimension<'a, RowSummary> for Count {
    fn add_summary(&mut self, summary: &'a RowSummary) {
        self.0 += summary.count;
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord)]
struct Height(usize);

impl<'a> Dimension<'a, RowSummary> for Height {
    fn add_summary(&mut self, summary: &'a RowSummary) {
        self.0 += summary.height;
    }
}

fn tree_of(heights: &[usize]) -> SumTree<Row> {
    heights
        .iter()
        .enumerate()
        .map(|(id, &height)| Row { id, height })
        .collect()
}

#[test]
fn summary_accumulates_over_all_items() {
    let tree = tree_of(&[10, 20, 30]);
    let summary = tree.summary();
    assert_eq!(summary.count, 3);
    assert_eq!(summary.height, 60);
    assert_eq!(tree.extent::<Count>(), Count(3));
    assert_eq!(tree.extent::<Height>(), Height(60));
}

#[test]
fn seek_by_count_lands_on_the_indexed_item() {
    let tree = tree_of(&[10, 20, 30, 40]);
    let mut cursor = tree.cursor::<Count, Height>();
    assert!(cursor.seek(&Count(2), SeekBias::Right));
    assert_eq!(cursor.item().map(|r| r.id), Some(2));
    // 따라다니는 축은 커서 **왼쪽**까지의 누적이다: 10 + 20.
    assert_eq!(*cursor.start(), Height(30));
    // 끝 지점은 현재 원소를 포함한다: 30 + 30.
    assert_eq!(cursor.end(), Height(60));
}

#[test]
fn seek_bias_decides_which_side_of_an_exact_boundary() {
    let tree = tree_of(&[10, 20, 30]);

    // 경계에 정확히 걸릴 때 Left 는 왼쪽 원소에 선다.
    let mut left = tree.cursor::<Count, ()>();
    left.seek(&Count(2), SeekBias::Left);
    assert_eq!(left.item().map(|r| r.id), Some(1), "Left = 왼쪽 원소");

    // Right 는 오른쪽 원소에 선다.
    let mut right = tree.cursor::<Count, ()>();
    right.seek(&Count(2), SeekBias::Right);
    assert_eq!(right.item().map(|r| r.id), Some(2), "Right = 오른쪽 원소");
}

#[test]
fn seek_by_height_finds_the_item_containing_the_pixel() {
    // 높이 누적: 10, 30, 60. 픽셀 35 는 세 번째 원소(id 2) 안이다.
    let tree = tree_of(&[10, 20, 30]);
    let mut cursor = tree.cursor::<Height, Count>();
    assert!(!cursor.seek(&Height(35), SeekBias::Right), "경계가 아니다");
    assert_eq!(cursor.item().map(|r| r.id), Some(2));
    assert_eq!(*cursor.start(), Count(2));
}

#[test]
fn slice_returns_passed_items_and_leaves_cursor_at_target() {
    let tree = tree_of(&[10, 20, 30, 40]);
    let mut cursor = tree.cursor::<Count, ()>();
    let head = cursor.slice(&Count(2), SeekBias::Right);
    assert_eq!(head.items().iter().map(|r| r.id).collect::<Vec<_>>(), [0, 1]);
    assert_eq!(cursor.item().map(|r| r.id), Some(2));

    let tail = cursor.suffix();
    assert_eq!(tail.items().iter().map(|r| r.id).collect::<Vec<_>>(), [2, 3]);
    assert_eq!(cursor.item(), None, "suffix 뒤에는 끝을 지난다");
}

#[test]
fn slice_then_push_tree_reassembles_the_original() {
    // viewported_list 가 항목을 갈아끼울 때 쓰는 패턴: 앞부분 잘라내고 → 새 항목 → 나머지.
    let tree = tree_of(&[10, 20, 30, 40]);
    let mut cursor = tree.cursor::<Count, ()>();
    let mut rebuilt = cursor.slice(&Count(2), SeekBias::Right);
    rebuilt.push_tree(cursor.suffix());
    assert_eq!(
        rebuilt.items().iter().map(|r| r.id).collect::<Vec<_>>(),
        [0, 1, 2, 3]
    );
    assert_eq!(rebuilt.summary().height, 100);
}

#[test]
fn summary_of_a_range_measures_only_what_was_passed() {
    let tree = tree_of(&[10, 20, 30, 40]);
    let mut cursor = tree.cursor::<Count, ()>();
    let measured: Height = cursor.summary(&Count(3), SeekBias::Right);
    assert_eq!(measured, Height(60), "앞의 세 항목 높이 합");
}

#[test]
fn seeking_backwards_restarts_instead_of_going_wrong() {
    // 뒤로 seek 하는 호출을 허용한다(앞으로만 훑는 구현이라 되감기가 필요하다).
    let tree = tree_of(&[10, 20, 30, 40]);
    let mut cursor = tree.cursor::<Count, Height>();
    cursor.seek(&Count(3), SeekBias::Right);
    assert_eq!(cursor.item().map(|r| r.id), Some(3));
    cursor.seek(&Count(1), SeekBias::Right);
    assert_eq!(cursor.item().map(|r| r.id), Some(1));
    assert_eq!(*cursor.start(), Height(10), "누적 좌표도 함께 되감긴다");
}

#[test]
fn next_and_prev_walk_one_item_at_a_time() {
    let tree = tree_of(&[10, 20, 30]);
    let mut cursor = tree.cursor::<Count, Height>();
    cursor.seek(&Count(0), SeekBias::Right);
    assert_eq!(cursor.item().map(|r| r.id), Some(0));
    cursor.next();
    assert_eq!(cursor.item().map(|r| r.id), Some(1));
    assert_eq!(*cursor.start(), Height(10));
    cursor.prev();
    assert_eq!(cursor.item().map(|r| r.id), Some(0));
    assert_eq!(*cursor.start(), Height(0), "prev 는 좌표를 다시 쌓는다");
}

#[test]
fn cursor_iterates_all_items() {
    let tree = tree_of(&[10, 20, 30]);
    let cursor = tree.cursor::<Count, ()>();
    let ids: Vec<_> = cursor.map(|r| r.id).collect();
    assert_eq!(ids, [0, 1, 2]);
}

#[test]
fn seek_past_the_end_stops_at_the_end() {
    let tree = tree_of(&[10, 20]);
    let mut cursor = tree.cursor::<Count, ()>();
    assert!(!cursor.seek(&Count(99), SeekBias::Right));
    assert_eq!(cursor.item(), None);
}

#[test]
fn empty_tree_is_navigable() {
    let tree: SumTree<Row> = SumTree::new();
    let mut cursor = tree.cursor::<Count, ()>();
    assert!(cursor.seek(&Count(0), SeekBias::Right), "0 은 정확한 위치다");
    assert_eq!(cursor.item(), None);
    assert!(tree.is_empty());
}

#[test]
fn update_last_recomputes_summaries() {
    let mut tree = tree_of(&[10, 20]);
    tree.update_last(|row| row.height = 100);
    assert_eq!(tree.summary().height, 110);
    assert_eq!(tree.extent::<Height>(), Height(110));
}

#[test]
fn filter_cursor_visits_only_matching_items() {
    let tree = tree_of(&[10, 25, 30, 45]);
    let cursor = tree.filter::<_, ()>(|summary: &RowSummary| summary.height >= 30);
    let ids: Vec<_> = cursor.map(|r| r.id).collect();
    assert_eq!(ids, [2, 3]);
}

#[test]
fn clone_is_cheap_and_independent() {
    // Arc 공유이지만 쓰기 시 분기해야 한다(원본이 오염되면 안 됨).
    let tree = tree_of(&[10, 20]);
    let mut copy = tree.clone();
    copy.push(Row { id: 9, height: 5 });
    assert_eq!(tree.len(), 2, "원본은 그대로");
    assert_eq!(copy.len(), 3);
    assert_eq!(tree.summary().height, 30);
    assert_eq!(copy.summary().height, 35);
}
