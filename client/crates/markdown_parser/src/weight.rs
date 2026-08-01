//! 글꼴 굵기.

use enum_iterator::Sequence;

/// `Normal` 이 아닌 모든 굵기. 즉 "명시적으로 지정된 굵기"만 표현한다
/// (`Normal` 은 값이 없는 상태 = `Option::None` 으로 나타낸다).
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Sequence)]
pub enum CustomWeight {
    Thin,
    ExtraLight,
    Light,
    Medium,
    Semibold,
    Bold,
    ExtraBold,
    Black,
}

impl CustomWeight {
    /// Bold 이상으로 굵은가.
    pub fn is_at_least_bold(&self) -> bool {
        matches!(self, Self::Bold | Self::ExtraBold | Self::Black)
    }

    /// 중첩된 굵기는 지원하지 않는다 — 바깥쪽 굵기가 이긴다.
    pub fn merge_weights(
        first: Option<CustomWeight>,
        second: Option<CustomWeight>,
    ) -> Option<CustomWeight> {
        first.or(second)
    }
}
