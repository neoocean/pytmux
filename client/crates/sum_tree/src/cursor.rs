//! [`SumTree`] 위를 좌표로 훑는 커서.
//!
//! 원본(증강 B-트리)과 **관측 가능한 의미론은 같고** 내부만 평평한 슬라이스 스캔이다.
//! 자세한 배경은 크레이트 루트 문서 참조.

use std::fmt;

use crate::{Dimension, Entry, Item, SeekBias, SumTree};

/// 좌표 기반으로 트리 위 위치를 잡는 커서.
///
/// 두 좌표축을 동시에 든다: `S` 는 **찾는 축**(seek 대상), `U` 는 **따라다니는 축**
/// (위치가 바뀔 때마다 함께 누적되어 [`Cursor::start`] 로 읽힌다).
///
/// # 경계 규칙
///
/// `seek` 는 "커서 왼쪽에 쌓인 좌표"를 기준으로 판단한다. 어떤 원소를 지났을 때의 좌표가
/// 목표와 **정확히 같아지는** 경우, [`SeekBias::Left`] 는 그 원소를 지나지 않고(왼쪽
/// 원소에 선다), [`SeekBias::Right`] 는 지난다(오른쪽 원소에 선다).
#[derive(Clone, Debug)]
pub struct Cursor<'a, T: Item, S, U> {
    entries: &'a [Entry<T>],
    index: usize,
    seek_dimension: S,
    sum_dimension: U,
    /// 원본과 같은 지연 동작: 한 번도 위치를 잡지 않은 커서는 원소를 내주지 않는다.
    did_seek: bool,
}

impl<'a, T, S, U> Cursor<'a, T, S, U>
where
    T: Item,
    S: Dimension<'a, T::Summary>,
    U: Dimension<'a, T::Summary>,
{
    pub fn new(tree: &'a SumTree<T>) -> Self {
        Self {
            entries: tree.entries.as_slice(),
            index: 0,
            seek_dimension: S::default(),
            sum_dimension: U::default(),
            did_seek: false,
        }
    }

    fn reset(&mut self) {
        self.index = 0;
        self.seek_dimension = S::default();
        self.sum_dimension = U::default();
        self.did_seek = false;
    }

    /// 커서 **시작 지점**의 찾는 축 좌표(= 커서 왼쪽 원소들의 누적).
    pub fn seek_position(&self) -> &S {
        &self.seek_dimension
    }

    /// 커서 **끝 지점**의 찾는 축 좌표(= 현재 원소까지 포함한 누적).
    pub fn end_seek_position(&self) -> S {
        let mut end = self.seek_dimension.clone();
        if let Some(entry) = self.current_entry() {
            end.add_summary(&entry.summary);
        }
        end
    }

    /// 커서 **시작 지점**의 따라다니는 축 좌표.
    pub fn start(&self) -> &U {
        &self.sum_dimension
    }

    /// 커서 **끝 지점**의 따라다니는 축 좌표.
    pub fn end(&self) -> U {
        let mut end = self.sum_dimension.clone();
        if let Some(entry) = self.current_entry() {
            end.add_summary(&entry.summary);
        }
        end
    }

    fn current_entry(&self) -> Option<&'a Entry<T>> {
        if self.did_seek {
            self.entries.get(self.index)
        } else {
            None
        }
    }

    /// 커서가 가리키는 원소. 끝을 지났거나 아직 위치를 잡지 않았으면 `None`.
    pub fn item(&self) -> Option<&'a T> {
        self.current_entry().map(|e| &e.item)
    }

    /// 커서 바로 왼쪽 원소.
    pub fn prev_item(&self) -> Option<&'a T> {
        if !self.did_seek || self.index == 0 {
            return None;
        }
        self.entries.get(self.index - 1).map(|e| &e.item)
    }

    /// 한 칸 앞으로.
    pub fn next(&mut self) {
        if !self.did_seek {
            self.did_seek = true;
            return;
        }
        if let Some(entry) = self.entries.get(self.index) {
            self.seek_dimension.add_summary(&entry.summary);
            self.sum_dimension.add_summary(&entry.summary);
            self.index += 1;
        }
    }

    /// 한 칸 뒤로. 뒤로 갈 때는 누적 좌표를 처음부터 다시 쌓는다(모노이드에 역원이
    /// 있다고 가정할 수 없다 — 예를 들어 `max` 형태의 요약은 뺄 수 없다).
    pub fn prev(&mut self) {
        if !self.did_seek || self.index == 0 {
            self.reset();
            self.did_seek = true;
            return;
        }
        let target = self.index - 1;
        self.reset();
        self.did_seek = true;
        for entry in &self.entries[..target] {
            self.seek_dimension.add_summary(&entry.summary);
            self.sum_dimension.add_summary(&entry.summary);
        }
        self.index = target;
    }
}

impl<'a, T, S, U> Cursor<'a, T, S, U>
where
    T: Item,
    S: Dimension<'a, T::Summary> + Ord,
    U: Dimension<'a, T::Summary>,
{
    /// 목표 좌표까지 앞으로 이동한다. 정확히 그 좌표에 섰으면 `true`.
    ///
    /// **앞으로만** 간다. 목표가 현재 위치보다 뒤면 처음부터 다시 훑는다.
    pub fn seek(&mut self, pos: &S, bias: SeekBias) -> bool {
        if self.did_seek && *pos < self.seek_dimension {
            self.reset();
        }
        self.did_seek = true;
        self.advance_to(pos, bias, |_| {});
        self.seek_dimension == *pos
    }

    /// [`Self::seek`] 와 같지만 트리 끝을 넘어가지 않는다.
    pub fn seek_clamped(&mut self, pos: &S, bias: SeekBias) {
        self.seek(pos, bias);
    }

    /// 현재 위치부터 목표 좌표까지의 원소들을 잘라 새 트리로 돌려준다. 커서는 목표로 이동.
    pub fn slice(&mut self, end: &S, bias: SeekBias) -> SumTree<T> {
        if self.did_seek && *end < self.seek_dimension {
            self.reset();
        }
        self.did_seek = true;
        let mut sliced = SumTree::new();
        self.advance_to(end, bias, |entry| sliced.push(entry.item.clone()));
        sliced
    }

    /// 현재 위치부터 끝까지를 새 트리로 돌려준다. 커서는 끝으로 이동.
    pub fn suffix(&mut self) -> SumTree<T> {
        self.did_seek = true;
        let mut rest = SumTree::new();
        while let Some(entry) = self.entries.get(self.index) {
            rest.push(entry.item.clone());
            self.seek_dimension.add_summary(&entry.summary);
            self.sum_dimension.add_summary(&entry.summary);
            self.index += 1;
        }
        rest
    }

    /// 현재 위치부터 목표 좌표까지를 임의의 좌표축 `D` 로 환산한다. 커서는 목표로 이동.
    pub fn summary<D>(&mut self, end: &S, bias: SeekBias) -> D
    where
        D: Dimension<'a, T::Summary>,
    {
        if self.did_seek && *end < self.seek_dimension {
            self.reset();
        }
        self.did_seek = true;
        let mut measured = D::default();
        self.advance_to(end, bias, |entry| measured.add_summary(&entry.summary));
        measured
    }

    /// seek 계열의 공통 알고리즘: 목표를 넘지 않는 동안만 원소를 지나며, 지나는 원소마다
    /// `visit` 를 호출한다. 경계 판정이 한 곳에만 있도록 모아 뒀다 — Left/Right 의 차이가
    /// 여기 한 줄(`<` 대 `<=`)이고, 이게 어긋나면 리스트가 한 칸씩 밀린다.
    fn advance_to(&mut self, target: &S, bias: SeekBias, mut visit: impl FnMut(&'a Entry<T>)) {
        while let Some(entry) = self.entries.get(self.index) {
            let mut candidate = self.seek_dimension.clone();
            candidate.add_summary(&entry.summary);
            let should_consume = match bias {
                SeekBias::Left => candidate < *target,
                SeekBias::Right => candidate <= *target,
            };
            if !should_consume {
                break;
            }
            visit(entry);
            self.seek_dimension = candidate;
            self.sum_dimension.add_summary(&entry.summary);
            self.index += 1;
        }
    }
}

/// 커서는 원소 이터레이터이기도 하다(`.enumerate()` 등을 쓰기 위해).
impl<'a, T, S, U> Iterator for Cursor<'a, T, S, U>
where
    T: Item,
    S: Dimension<'a, T::Summary>,
    U: Dimension<'a, T::Summary>,
{
    type Item = &'a T;

    fn next(&mut self) -> Option<Self::Item> {
        if !self.did_seek {
            self.did_seek = true;
        }
        let item = self.item()?;
        Cursor::next(self);
        Some(item)
    }
}

impl<'a, T, S, U> DoubleEndedIterator for Cursor<'a, T, S, U>
where
    T: Item,
    S: Dimension<'a, T::Summary>,
    U: Dimension<'a, T::Summary>,
{
    fn next_back(&mut self) -> Option<Self::Item> {
        if !self.did_seek {
            self.did_seek = true;
            self.index = self.entries.len();
        }
        if self.index == 0 {
            return None;
        }
        Cursor::prev(self);
        self.item()
    }
}

/// 요약 술어로 하위 구간을 건너뛰며 훑는 커서.
///
/// 원본은 노드 요약으로 **부분트리째** 건너뛰지만, 여기서는 원소별로 판정한다
/// (관측 결과는 같고 비용만 다르다).
pub struct FilterCursor<'a, F, T: Item, U> {
    entries: &'a [Entry<T>],
    index: usize,
    sum_dimension: U,
    filter_node: F,
    started: bool,
}

impl<'a, F, T, U> fmt::Debug for FilterCursor<'a, F, T, U>
where
    T: Item,
    U: fmt::Debug,
{
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("FilterCursor")
            .field("index", &self.index)
            .field("sum_dimension", &self.sum_dimension)
            .finish_non_exhaustive()
    }
}

impl<'a, F, T, U> FilterCursor<'a, F, T, U>
where
    F: Fn(&T::Summary) -> bool,
    T: Item,
    U: Dimension<'a, T::Summary>,
{
    pub fn new(tree: &'a SumTree<T>, filter_node: F) -> Self {
        Self {
            entries: tree.entries.as_slice(),
            index: 0,
            sum_dimension: U::default(),
            filter_node,
            started: false,
        }
    }

    pub fn start(&self) -> &U {
        &self.sum_dimension
    }

    pub fn item(&self) -> Option<&'a T> {
        if !self.started {
            return None;
        }
        self.entries.get(self.index).map(|e| &e.item)
    }

    /// 술어를 통과하는 다음 원소로 이동한다.
    pub fn next(&mut self) {
        if self.started {
            if let Some(entry) = self.entries.get(self.index) {
                self.sum_dimension.add_summary(&entry.summary);
                self.index += 1;
            }
        } else {
            self.started = true;
        }
        while let Some(entry) = self.entries.get(self.index) {
            if (self.filter_node)(&entry.summary) {
                break;
            }
            self.sum_dimension.add_summary(&entry.summary);
            self.index += 1;
        }
    }
}

impl<'a, F, T, U> Iterator for FilterCursor<'a, F, T, U>
where
    F: Fn(&T::Summary) -> bool,
    T: Item,
    U: Dimension<'a, T::Summary>,
{
    type Item = &'a T;

    fn next(&mut self) -> Option<Self::Item> {
        if !self.started {
            FilterCursor::next(self);
        }
        let item = self.item()?;
        FilterCursor::next(self);
        Some(item)
    }
}
