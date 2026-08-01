//! `string-offset` — 문자열 오프셋 뉴타입.
//!
//! **AGPL 원본을 대체하는 자체 구현(MIT).** warp 저장소의 동명 크레이트가 제공하던
//! API 중 `warpui`/`warpui_core` 가 실제로 쓰는 것만 구현한다. 구현 본문은 호출부
//! 요구사항에서 새로 작성했다(PROVENANCE.md §2).
//!
//! 존재 이유: 바이트 오프셋과 문자(char) 오프셋을 타입으로 갈라 서로 섞이지 않게 한다.
//! 둘 다 `usize` 라 그냥 쓰면 조용히 뒤바뀌는데, 한글처럼 멀티바이트 문자에서 그 버그는
//! "커서가 글자 중간에 박히는" 형태로만 드러나 잡기 어렵다.

use std::fmt;
use std::ops::{Add, AddAssign, Range, Sub, SubAssign};

macro_rules! define_offset {
    ($name:ident, $doc:literal) => {
        #[doc = $doc]
        #[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Hash)]
        pub struct $name(usize);

        impl $name {
            pub const fn zero() -> Self {
                Self(0)
            }

            pub fn as_usize(self) -> usize {
                self.0
            }

            /// 이 오프셋에서 시작하고 끝나는 빈 범위(= 커서 위치를 범위로 표현할 때).
            pub fn empty_range(self) -> Range<Self> {
                self..self
            }

            /// 음수도 받는 이동. 0 아래로는 내려가지 않는다(포화).
            pub fn add_signed(self, rhs: isize) -> Self {
                if rhs >= 0 {
                    Self(self.0.saturating_add(rhs as usize))
                } else {
                    Self(self.0.saturating_sub(rhs.unsigned_abs()))
                }
            }

            /// `usize` 범위를 이 타입의 범위로 승격.
            pub fn range(range: Range<usize>) -> Range<Self> {
                Self(range.start)..Self(range.end)
            }
        }

        impl From<usize> for $name {
            fn from(value: usize) -> Self {
                Self(value)
            }
        }

        impl From<$name> for usize {
            fn from(value: $name) -> usize {
                value.0
            }
        }

        impl Add for $name {
            type Output = Self;
            fn add(self, rhs: Self) -> Self {
                Self(self.0 + rhs.0)
            }
        }

        impl Add<usize> for $name {
            type Output = Self;
            fn add(self, rhs: usize) -> Self {
                Self(self.0 + rhs)
            }
        }

        impl Sub for $name {
            type Output = Self;
            fn sub(self, rhs: Self) -> Self {
                Self(self.0 - rhs.0)
            }
        }

        impl Sub<usize> for $name {
            type Output = Self;
            fn sub(self, rhs: usize) -> Self {
                Self(self.0 - rhs)
            }
        }

        impl AddAssign for $name {
            fn add_assign(&mut self, rhs: Self) {
                self.0 += rhs.0;
            }
        }

        impl AddAssign<usize> for $name {
            fn add_assign(&mut self, rhs: usize) {
                self.0 += rhs;
            }
        }

        impl SubAssign for $name {
            fn sub_assign(&mut self, rhs: Self) {
                self.0 -= rhs.0;
            }
        }

        impl SubAssign<usize> for $name {
            fn sub_assign(&mut self, rhs: usize) {
                self.0 -= rhs;
            }
        }

        impl num_traits::SaturatingSub for $name {
            fn saturating_sub(&self, rhs: &Self) -> Self {
                Self(self.0.saturating_sub(rhs.0))
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, "{}", self.0)
            }
        }
    };
}

define_offset!(CharOffset, "문자 단위 오프셋(멀티바이트 문자 = 1).");
define_offset!(ByteOffset, "바이트 단위 오프셋(UTF-8 인코딩 기준).");

impl AddAssign<i32> for CharOffset {
    fn add_assign(&mut self, rhs: i32) {
        *self = self.add_signed(rhs as isize);
    }
}

/// 바이트 오프셋 → 문자 오프셋 변환기.
///
/// 같은 문자열에 대해 **증가하는 순서로** 여러 번 물어보는 것을 전제로, 직전 위치부터
/// 이어서 센다(호출부가 글리프를 왼→오른쪽으로 훑기 때문). 뒤로 되돌아가는 질의도
/// 정답을 주되 그때는 처음부터 다시 센다.
pub struct CharCounter<'a> {
    text: &'a str,
    last_byte: usize,
    last_char: usize,
}

impl<'a> CharCounter<'a> {
    pub fn new(str: &'a str) -> Self {
        Self {
            text: str,
            last_byte: 0,
            last_char: 0,
        }
    }

    /// 주어진 바이트 오프셋에 대응하는 문자 오프셋. 오프셋이 문자열 밖이거나
    /// 문자 경계가 아니면 `None`.
    pub fn char_offset(&mut self, byte_offset: impl Into<ByteOffset>) -> Option<CharOffset> {
        let target = byte_offset.into().as_usize();
        if target > self.text.len() || !self.text.is_char_boundary(target) {
            return None;
        }
        // 뒤로 가는 질의면 캐시를 버리고 처음부터.
        if target < self.last_byte {
            self.last_byte = 0;
            self.last_char = 0;
        }
        let advanced = self.text[self.last_byte..target].chars().count();
        self.last_byte = target;
        self.last_char += advanced;
        Some(CharOffset::from(self.last_char))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn char_counter_handles_multibyte_and_rewind() {
        // "가" 3바이트, "a" 1바이트 → 바이트 0,3,4 가 문자 0,1,2.
        let mut c = CharCounter::new("가a나");
        assert_eq!(c.char_offset(0usize), Some(CharOffset::from(0)));
        assert_eq!(c.char_offset(3usize), Some(CharOffset::from(1)));
        assert_eq!(c.char_offset(4usize), Some(CharOffset::from(2)));
        // 되감기 — 캐시를 버리고 다시 세도 답이 같아야 한다.
        assert_eq!(c.char_offset(3usize), Some(CharOffset::from(1)));
        // 문자 중간(1바이트째)은 경계가 아니다.
        assert_eq!(c.char_offset(1usize), None);
        // 문자열 밖.
        assert_eq!(c.char_offset(99usize), None);
    }

    #[test]
    fn add_signed_saturates_at_zero() {
        assert_eq!(ByteOffset::from(2).add_signed(-5), ByteOffset::zero());
        assert_eq!(ByteOffset::from(2).add_signed(3), ByteOffset::from(5));
    }
}
