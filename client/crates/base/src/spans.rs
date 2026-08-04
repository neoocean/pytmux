//! 패널 글 안의 **뜻이 있는 범위** — 링크·경로(§10-21ⓥ2·ⓧ2).
//!
//! # 왜 한 배관인가
//!
//! 제보 둘이 같은 모양이다: *"패널 안의 어떤 범위에 마우스를 올리면 알려 주고, 클릭하면
//! 무언가 한다."* 하나는 `https` 링크(브라우저로 열기)이고 하나는 파일 이름(전체 경로
//! 복사)이다. 범위를 찾는 자리를 갈래마다 만들면 hover 강조·커서 모양·클릭 라우팅이
//! 갈래 수만큼 늘어난다 — **제공자만 갈아 끼우는 한 배관**으로 둔다.
//!
//! # 왜 core 인가
//!
//! "어디부터 어디까지가 링크인가"는 판정이다. 두 클라가 각자 판정하면 같은 줄에서 서로
//! 다른 자리를 짚고, 그 어긋남은 나란히 놓아야만 보인다.
//!
//! # 좌표
//!
//! 여는 것은 **글자 인덱스**(char)다. 셀 좌표로 옮기는 것은 뷰의 일이다 — 한 글자가 두
//! 칸인 글(한글·이모지)이 있어 그 산수는 폭 표를 아는 쪽이 해야 한다.

/// 범위 하나가 **무엇인가**.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SpanKind {
    /// `http`/`https` 링크. 그 밖의 스킴은 **안 잡는다**(아래 보안 항목).
    Url,
    /// 파일처럼 보이는 경로. 전체 경로로 푸는 것은 부르는 쪽의 일이다(기준 디렉터리를
    /// 아는 쪽이 서버라서 — `SessionState::pane_cwd`, **그 범위가 있던 패널의** cwd).
    Path,
}

/// 줄 안의 범위 — `[start, end)` 는 **글자 인덱스**다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Span {
    pub kind: SpanKind,
    pub start: usize,
    pub end: usize,
    /// 그 범위의 글자 그대로(경계를 다듬은 뒤의 값).
    pub text: String,
}

/// 링크로 여는 스킴은 **둘뿐**이다.
///
/// # 왜 좁히나 (보안)
///
/// 패널 출력은 **남이 만든 글**이다(원격 셸·앱). 터미널이 임의 스킴을 여는 것은 클릭 한
/// 번으로 실행이 되는 통로다 — `file:` 은 로컬 파일을 열고, 커스텀 프로토콜은 그 핸들러가
/// 무엇이든 실행한다. 그래서 `javascript:` 같은 것을 막는 목록이 아니라 **허용 목록**이다
/// (막는 목록은 늘 다음 것이 새로 생긴다).
const SCHEMES: &[&str] = &["https://", "http://"];

/// 범위 끝에서 떼어 내는 글자들 — 산문에 붙어 오는 것들.
///
/// `(https://x)` 의 닫는 괄호나 문장 끝의 마침표까지 링크로 잡으면 그 주소가 안 열린다.
const TRAILING: &[char] = &['.', ',', ';', ':', '!', '?', ')', ']', '}', '>', '"', '\'', '`'];

/// 이 주소를 **열어도 되나** — 허용 목록 한 곳(§10-21ⓥ2).
///
/// 찾을 때와 열 때가 **같은 표**를 봐야 한다. 두 자리에 각자 적으면 그 틈이 통로가 된다
/// (패널 출력은 남이 만든 글이다).
pub fn is_openable(url: &str) -> bool {
    SCHEMES.iter().any(|s| url.starts_with(s)) && url.len() > SCHEMES.iter()
        .find(|s| url.starts_with(**s))
        .map_or(usize::MAX, |s| s.len())
}

/// 한 줄에서 링크를 찾는다.
///
/// 눈에 보이는 주소만 잡는다 — OSC 8(하이퍼링크 이스케이프)은 지금 우리가 안 받는다
/// (받으려면 서버 vtparse 까지 손대야 한다. 그건 따로 정할 일이다).
pub fn urls(line: &str) -> Vec<Span> {
    let chars: Vec<char> = line.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let Some(len) = scheme_at(&chars, i) else {
            i += 1;
            continue;
        };
        let mut end = i + len;
        while end < chars.len() && !chars[end].is_whitespace() {
            end += 1;
        }
        // 산문에 붙은 꼬리를 뗀다. 스킴만 남을 때까지 떼지는 않는다(그건 링크가 아니다).
        while end > i + len && TRAILING.contains(&chars[end - 1]) {
            end -= 1;
        }
        if end > i + len {
            out.push(Span {
                kind: SpanKind::Url,
                start: i,
                end,
                text: chars[i..end].iter().collect(),
            });
        }
        i = end.max(i + 1);
    }
    out
}

fn scheme_at(chars: &[char], at: usize) -> Option<usize> {
    SCHEMES.iter().find_map(|s| {
        let want: Vec<char> = s.chars().collect();
        (chars.len() >= at + want.len() && chars[at..at + want.len()] == want[..])
            .then_some(want.len())
    })
}

/// 한 줄에서 **경로처럼 보이는** 범위를 찾는다.
///
/// # 왜 좁게 잡나
///
/// 넓히면 아프다: 산문 속 `a/b` 나 날짜 `2026/08/02` 도 경로처럼 보인다. 그래서 두 조건을
/// **둘 다** 요구한다 — ⑴ 구분자(`/` 또는 `\`)가 있고 ⑵ 마지막 조각에 **확장자**가 있다.
/// 그러면 `2026/08/02` 는 안 걸리고(마지막 조각에 점이 없다) `server/test/x.mjs` 는 걸린다.
///
/// 존재 확인(서버가 싸게 할 수 있는 일)은 여기서 안 한다 — 이 함수는 순수해야 두 클라가
/// 같은 자리를 짚는지 창 없이 잴 수 있다. 못 푸는 경로는 부르는 쪽이 **존을 안 만든다**.
pub fn paths(line: &str) -> Vec<Span> {
    let chars: Vec<char> = line.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        if chars[i].is_whitespace() || is_wrapper(chars[i]) {
            i += 1;
            continue;
        }
        let mut end = i;
        while end < chars.len() && !chars[end].is_whitespace() && !is_wrapper(chars[end]) {
            end += 1;
        }
        let mut stop = end;
        while stop > i && TRAILING.contains(&chars[stop - 1]) {
            stop -= 1;
        }
        let word: String = chars[i..stop].iter().collect();
        if looks_like_path(&word) {
            out.push(Span { kind: SpanKind::Path, start: i, end: stop, text: word });
        }
        i = end.max(i + 1);
    }
    out
}

/// 경로를 감싸 오는 글자들 — 낱말을 자를 때 경계로 본다(`Update(a/b.mjs)` 의 괄호).
fn is_wrapper(c: char) -> bool {
    matches!(c, '(' | ')' | '[' | ']' | '{' | '}' | '"' | '\'' | '`' | '<' | '>' | ',')
}

fn looks_like_path(word: &str) -> bool {
    if word.len() < 3 || word.contains("://") {
        return false;   // 링크는 링크 제공자의 것이다
    }
    let Some(last) = word.rsplit(['/', '\\']).next() else {
        return false;
    };
    // 구분자가 있어야 하고(낱말 하나는 경로로 안 본다) 마지막 조각에 확장자가 있어야 한다.
    let has_sep = word.contains('/') || word.contains('\\');
    let dot = last.rfind('.');
    let ext_ok = dot.is_some_and(|d| d > 0 && d + 1 < last.len());
    has_sep && ext_ok
}

/// 그 자리(글자 인덱스)에 걸친 범위를 찾는다. 없으면 `None`.
///
/// 링크가 경로보다 **먼저**다: `https://x/y.html` 은 둘 다에 걸릴 수 있는데, 그 자리에서
/// 사람이 기대하는 것은 "여는 것"이다.
pub fn at(line: &str, index: usize) -> Option<Span> {
    let hit = |v: Vec<Span>| v.into_iter().find(|s| index >= s.start && index < s.end);
    hit(urls(line)).or_else(|| hit(paths(line)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_bare_url_is_found_whole() {
        let spans = urls("보세요 https://example.com/a?b=1 끝");
        assert_eq!(spans.len(), 1);
        assert_eq!(spans[0].text, "https://example.com/a?b=1");
    }

    #[test]
    fn punctuation_that_follows_a_url_is_not_part_of_it() {
        // `(https://x).` 까지 잡으면 그 주소가 안 열린다.
        assert_eq!(urls("(https://x.dev/a).").remove(0).text, "https://x.dev/a");
        assert_eq!(urls("끝은 https://x.dev/a, 다음").remove(0).text, "https://x.dev/a");
    }

    /// ★ **허용 목록**이다 — 막는 목록은 늘 다음 것이 새로 생긴다.
    #[test]
    fn only_http_and_https_are_links() {
        for text in ["file:///etc/passwd", "javascript:alert(1)", "vscode://x/y", "ftp://h/f"] {
            assert!(urls(text).is_empty(), "{text} 를 링크로 잡았다");
        }
        assert_eq!(urls("http://x.dev/a").len(), 1);
    }

    #[test]
    fn a_scheme_without_a_target_is_not_a_link() {
        assert!(urls("https://").is_empty());
        assert!(!is_openable("https://"), "스킴만으로는 열 것이 없다");
    }

    /// ★ 찾을 때와 **열 때**가 같은 표를 본다 — 두 자리에 각자 적으면 그 틈이 통로다.
    #[test]
    fn the_open_guard_uses_the_same_allowlist() {
        assert!(is_openable("https://x.dev/a"));
        assert!(is_openable("http://x.dev/a"));
        for bad in ["file:///etc/passwd", "javascript:alert(1)", "vscode://x", "x.dev"] {
            assert!(!is_openable(bad), "{bad}");
        }
    }

    #[test]
    fn a_path_needs_both_a_separator_and_an_extension() {
        // ⚠ 넓히면 아프다 — 날짜와 산문 조각이 경로처럼 보인다.
        assert!(paths("2026/08/02 에 고쳤다").is_empty(), "날짜를 경로로 잡았다");
        assert!(paths("a/b 를 보라").is_empty(), "확장자가 없다");
        assert!(paths("readme.md 하나").is_empty(), "구분자가 없다");
        let spans = paths("Update(server/test/shot-guide-badges.mjs)");
        assert_eq!(spans.len(), 1);
        assert_eq!(spans[0].text, "server/test/shot-guide-badges.mjs");
    }

    #[test]
    fn a_windows_path_counts_too() {
        let spans = paths(r"열었다 client\crates\gui\src\main.rs 를");
        assert_eq!(spans.len(), 1, "{spans:?}");
        assert_eq!(spans[0].text, r"client\crates\gui\src\main.rs");
    }

    #[test]
    fn a_url_wins_over_a_path_at_the_same_spot() {
        // 그 자리에서 사람이 기대하는 것은 "여는 것"이다.
        let line = "https://x.dev/a/b.html";
        let span = at(line, 3).expect("범위");
        assert_eq!(span.kind, SpanKind::Url);
        assert_eq!(span.text, line);
    }

    #[test]
    fn a_spot_with_nothing_on_it_is_none() {
        assert!(at("그냥 글", 1).is_none());
        assert!(at("https://x.dev/a", 100).is_none(), "줄 밖");
    }

    #[test]
    fn the_span_indexes_are_characters_not_bytes() {
        // ★ 한 글자가 여러 바이트인 글이 앞에 있으면 바이트 인덱스는 자리를 밀어낸다.
        let line = "한글과 https://x.dev/a";
        let span = at(line, 4).expect("범위");
        assert_eq!(span.start, 4, "글자 인덱스가 아니다: {span:?}");
        assert_eq!(span.text, "https://x.dev/a");
    }
}
