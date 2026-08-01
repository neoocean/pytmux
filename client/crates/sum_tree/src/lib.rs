//! `sum_tree` — 요약(summary)을 누적하는 순서 있는 컬렉션.
//!
//! **AGPL 원본을 대체하는 자체 구현(MIT).** 원본은 증강 B-트리(arrayvec 노드, TREE_BASE 6)
//! 라 seek/slice 가 O(log n) 이다. 이 구현은 **같은 API·같은 의미론을 평평한 `Vec` 위에**
//! 올린 것으로, 복잡도만 O(n) 이다. PROVENANCE.md §2·§3.
//!
//! # 왜 이렇게 했나
//!
//! `warpui_core` 에서 이걸 쓰는 곳은 GUI 의 `viewported_list`(가상 스크롤 리스트)와
//! `table` 둘뿐이고, TUI 경로는 아예 쓰지 않는다. P0/P1 단계에서 필요한 것은 **컴파일과
//! 의미론적 정확성**이지 대규모 리스트 성능이 아니다. 진짜 B-트리는 블록 리스트가 실제로
//! 가상화를 요구하는 P3 에서 교체한다.
//!
//! # 성능 계약 (교체 시점 판단 기준)
//!
//! `seek`/`slice`/`summary` 는 **현재 커서 위치부터 앞으로만** 훑으므로, 리스트를 한 번
//! 순회하는 패턴에서는 전체가 O(n) 이다(각 항목을 한 번씩만 지난다). 비용이 문제가 되는
//! 것은 **매 프레임 무작위 위치로 seek** 하는 패턴이며, 항목 수가 수천을 넘길 때부터다.
//!
//! # 개념
//!
//! - [`Item`] 은 자기 [`Item::Summary`] 를 만든다. Summary 는 `AddAssign` 으로 합쳐지는
//!   모노이드다(예: 항목 수, 높이 합계).
//! - [`Dimension`] 은 Summary 를 누적해 얻는 "좌표"다(예: `Count`, `Height`).
//! - [`Cursor`] 는 그 좌표로 위치를 찾는다. 경계에 정확히 걸리면 [`SeekBias`] 가 좌/우를
//!   가른다.

use std::fmt;
use std::iter::FromIterator;
use std::ops::AddAssign;
use std::sync::Arc;

pub use cursor::{Cursor, FilterCursor};

mod cursor;

/// 트리에 담기는 원소. 자기 자신을 요약할 수 있어야 한다.
pub trait Item: Clone + fmt::Debug {
    type Summary: for<'a> AddAssign<&'a Self::Summary> + Default + Clone + fmt::Debug;

    fn summary(&self) -> Self::Summary;
}

/// 키로 정렬·갱신할 수 있는 원소.
pub trait KeyedItem: Item {
    type Key: for<'a> Dimension<'a, Self::Summary> + Ord;

    fn key(&self) -> Self::Key;
}

/// Summary 를 누적해 만들어지는 좌표축.
pub trait Dimension<'a, Summary: Default>: 'a + Clone + fmt::Debug + Default {
    fn add_summary(&mut self, summary: &'a Summary);
}

/// 아무것도 추적하지 않는 좌표(호출부가 한쪽 축만 필요할 때 `()` 를 쓴다).
impl<'a, T: Default> Dimension<'a, T> for () {
    fn add_summary(&mut self, _: &'a T) {}
}

/// 찾는 위치가 두 원소의 **경계에 정확히 걸릴 때** 어느 쪽에 설지.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SeekBias {
    /// 왼쪽 원소에 선다.
    Left,
    /// 오른쪽 원소에 선다.
    Right,
}

/// 항목과 그 요약을 함께 보관한다.
///
/// 요약을 저장해 두는 이유는 [`Dimension::add_summary`] 가 `&'a Summary` 를 요구하기
/// 때문이다 — 즉석에서 계산하면 빌려줄 수명이 없다.
#[derive(Clone, Debug)]
pub(crate) struct Entry<T: Item> {
    pub(crate) item: T,
    pub(crate) summary: T::Summary,
}

impl<T: Item> Entry<T> {
    fn new(item: T) -> Self {
        let summary = item.summary();
        Self { item, summary }
    }
}

/// 요약을 누적하는 순서 있는 컬렉션.
#[derive(Debug)]
pub struct SumTree<T: Item> {
    pub(crate) entries: Arc<Vec<Entry<T>>>,
    summary: T::Summary,
}

impl<T: Item> Clone for SumTree<T> {
    fn clone(&self) -> Self {
        Self {
            entries: Arc::clone(&self.entries),
            summary: self.summary.clone(),
        }
    }
}

impl<T: Item> Default for SumTree<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T: Item> SumTree<T> {
    pub fn new() -> Self {
        Self {
            entries: Arc::new(Vec::new()),
            summary: T::Summary::default(),
        }
    }

    pub fn from_item(item: T) -> Self {
        let mut tree = Self::new();
        tree.push(item);
        tree
    }

    pub fn items(&self) -> Vec<T> {
        self.entries.iter().map(|e| e.item.clone()).collect()
    }

    pub fn cursor<'a, S, U>(&'a self) -> Cursor<'a, T, S, U>
    where
        S: Dimension<'a, T::Summary>,
        U: Dimension<'a, T::Summary>,
    {
        Cursor::new(self)
    }

    pub fn filter<'a, F, U>(&'a self, filter_node: F) -> FilterCursor<'a, F, T, U>
    where
        F: Fn(&T::Summary) -> bool,
        U: Dimension<'a, T::Summary>,
    {
        FilterCursor::new(self, filter_node)
    }

    pub fn first(&self) -> Option<&T> {
        self.entries.first().map(|e| &e.item)
    }

    pub fn last(&self) -> Option<&T> {
        self.entries.last().map(|e| &e.item)
    }

    /// 트리 전체를 하나의 좌표로 환산한 값(= 끝 위치).
    pub fn extent<'a, D: Dimension<'a, T::Summary>>(&'a self) -> D {
        let mut dim = D::default();
        for entry in self.entries.iter() {
            dim.add_summary(&entry.summary);
        }
        dim
    }

    pub fn summary(&self) -> T::Summary {
        self.summary.clone()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn push(&mut self, item: T) {
        let entry = Entry::new(item);
        self.summary += &entry.summary;
        Arc::make_mut(&mut self.entries).push(entry);
    }

    pub fn extend<I>(&mut self, iter: I)
    where
        I: IntoIterator<Item = T>,
    {
        for item in iter {
            self.push(item);
        }
    }

    /// 다른 트리를 통째로 뒤에 붙인다.
    pub fn push_tree(&mut self, other: Self) {
        if other.is_empty() {
            return;
        }
        self.summary += &other.summary;
        let entries = Arc::make_mut(&mut self.entries);
        match Arc::try_unwrap(other.entries) {
            Ok(owned) => entries.extend(owned),
            Err(shared) => entries.extend(shared.iter().cloned()),
        }
    }

    /// 마지막 원소를 제자리에서 고친다. 요약도 다시 계산한다.
    pub fn update_last(&mut self, f: impl FnOnce(&mut T)) {
        let entries = Arc::make_mut(&mut self.entries);
        if let Some(last) = entries.last_mut() {
            f(&mut last.item);
            last.summary = last.item.summary();
        }
        self.recompute_summary();
    }

    fn recompute_summary(&mut self) {
        let mut summary = T::Summary::default();
        for entry in self.entries.iter() {
            summary += &entry.summary;
        }
        self.summary = summary;
    }
}

impl<T: KeyedItem> SumTree<T> {
    /// 키 순서를 유지하며 삽입한다. 같은 키가 있으면 교체한다.
    pub fn insert(&mut self, item: T) {
        let key = item.key();
        let entries = Arc::make_mut(&mut self.entries);
        match entries.binary_search_by(|probe| probe.item.key().cmp(&key)) {
            Ok(index) => entries[index] = Entry::new(item),
            Err(index) => entries.insert(index, Entry::new(item)),
        }
        self.recompute_summary();
    }

    /// 삽입·삭제를 한 번에 반영한다.
    pub fn edit(&mut self, edits: &mut [Edit<T>]) {
        for edit in edits {
            match edit {
                Edit::Insert(item) => self.insert(item.clone()),
                Edit::Remove(key) => {
                    let entries = Arc::make_mut(&mut self.entries);
                    if let Ok(index) = entries.binary_search_by(|probe| probe.item.key().cmp(key)) {
                        entries.remove(index);
                    }
                }
            }
        }
        self.recompute_summary();
    }
}

impl<T: Item> FromIterator<T> for SumTree<T> {
    fn from_iter<I: IntoIterator<Item = T>>(iter: I) -> Self {
        let mut tree = Self::new();
        tree.extend(iter);
        tree
    }
}

impl<T: Item> Extend<T> for SumTree<T> {
    fn extend<I: IntoIterator<Item = T>>(&mut self, iter: I) {
        SumTree::extend(self, iter);
    }
}

/// [`SumTree::edit`] 에 넘기는 편집 연산.
#[derive(Clone, Debug)]
pub enum Edit<T: KeyedItem> {
    Insert(T),
    Remove(T::Key),
}

#[cfg(test)]
#[path = "lib_tests.rs"]
mod tests;
