//! 셸 계열별 경로 이스케이프.

use std::borrow::Cow;

/// 이스케이프 동작이 같은 셸들의 묶음.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShellFamily {
    /// bash · zsh · fish
    Posix,
    PowerShell,
}

/// 특수문자 앞에 붙는 이스케이프 문자.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EscapeChar {
    Backslash,
    Backtick,
}

impl EscapeChar {
    pub fn as_char(self) -> char {
        match self {
            EscapeChar::Backslash => '\\',
            EscapeChar::Backtick => '`',
        }
    }
}

/// 이스케이프가 필요한 문자. 셸이 인용부호 없이 읽을 때 의미를 갖는 것들.
///
/// 두 계열 모두 같은 집합을 쓴다. PowerShell 은 `$`·백틱·공백이 핵심이고 POSIX 는
/// 글로브·리다이렉션·확장이 핵심인데, 합집합으로 이스케이프해도 **의미는 보존**된다
/// (불필요한 이스케이프가 몇 개 더 붙을 뿐). 드래그&드롭한 경로를 셸에 붙여넣는
/// 용도라 과하게 안전한 쪽이 맞다.
const NEEDS_ESCAPE: &[char] = &[
    ' ', '\t', '\n', '\'', '"', '\\', '`', '$', '&', '|', ';', '<', '>', '(', ')', '[', ']', '{',
    '}', '*', '?', '!', '#', '~', '^', '%', '=', ',',
];

impl ShellFamily {
    pub fn escape_char(&self) -> EscapeChar {
        match self {
            Self::Posix => EscapeChar::Backslash,
            Self::PowerShell => EscapeChar::Backtick,
        }
    }

    /// 인용부호 없이도 의미가 유지되도록 특수문자 앞에 이스케이프 문자를 붙인다.
    ///
    /// 빈 문자열은 `''` 로 만든다 — 그냥 두면 인자 자체가 사라지기 때문이다.
    /// 이스케이프할 것이 없으면 입력을 그대로 빌려 돌려준다(할당 없음).
    pub fn escape<'s>(&self, input: &'s str) -> Cow<'s, str> {
        if input.is_empty() {
            return "''".into();
        }
        if !input.contains(NEEDS_ESCAPE) {
            return Cow::Borrowed(input);
        }
        let esc = self.escape_char().as_char();
        let mut out = String::with_capacity(input.len() + 8);
        for ch in input.chars() {
            if NEEDS_ESCAPE.contains(&ch) {
                out.push(esc);
            }
            out.push(ch);
        }
        Cow::Owned(out)
    }

    /// [`Self::escape`] 의 역연산. 이스케이프 문자를 제거한다.
    ///
    /// 끝에 홀로 남은 이스케이프 문자는 그대로 둔다(잘린 입력을 조용히 먹지 않는다).
    pub fn unescape<'s>(&self, input: &'s str) -> Cow<'s, str> {
        let esc = self.escape_char().as_char();
        if !input.contains(esc) {
            return Cow::Borrowed(input);
        }
        let mut out = String::with_capacity(input.len());
        let mut chars = input.chars();
        while let Some(ch) = chars.next() {
            if ch == esc {
                match chars.next() {
                    Some(next) => out.push(next),
                    None => out.push(ch),
                }
            } else {
                out.push(ch);
            }
        }
        Cow::Owned(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escape_roundtrips() {
        for family in [ShellFamily::Posix, ShellFamily::PowerShell] {
            for input in ["/tmp/my file", "a$b", "plain", "괄호(있음)", "back\\slash"] {
                let escaped = family.escape(input);
                assert_eq!(family.unescape(&escaped), input, "family={family:?}");
            }
        }
    }

    #[test]
    fn empty_becomes_quoted_and_plain_is_borrowed() {
        assert_eq!(ShellFamily::Posix.escape(""), "''");
        assert!(matches!(
            ShellFamily::Posix.escape("/tmp/plain"),
            Cow::Borrowed(_)
        ));
    }

    #[test]
    fn trailing_escape_char_is_kept() {
        // 잘린 입력을 조용히 먹지 않는다.
        assert_eq!(ShellFamily::Posix.unescape("abc\\"), "abc\\");
    }
}
