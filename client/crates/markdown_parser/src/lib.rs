//! `markdown_parser` — 서식 있는 텍스트의 **표현 타입**.
//!
//! **AGPL 원본을 대체하는 자체 구현(MIT).** 원본은 마크다운/HTML 파서까지 포함한
//! 6.8천 줄 크레이트지만, `warpui`/`warpui_core` 는 **파서를 한 번도 호출하지 않고**
//! 결과를 담는 표현 타입만 쓴다(호출부 전수 확인). 그래서 여기에는 타입만 있다.
//!
//! 마크다운을 실제로 파싱해야 할 때가 오면(P5 의 Claude 블록 뷰) `pulldown-cmark` 로
//! 이 타입들을 채우는 함수를 여기 추가한다. PROVENANCE.md §2.

use std::any::Any;
use std::collections::{BTreeMap, VecDeque};
use std::fmt::Debug;
use std::ops::Range;
use std::sync::Arc;

pub mod weight;

use weight::CustomWeight;

/// 문서에 박혀 있던 구조화 블록(YAML front-matter 등)의 내용.
///
/// 원본은 `serde_yaml::Mapping` 을 썼지만, 이 크레이트에는 아직 **파서가 없어** 이 값을
/// 만들어 내는 곳도 없다. 소비자(`warpui`)도 `Embedded(_)` 로 무시만 한다. 그래서
/// 파서가 생길 때(P5) 필요한 형태로 정하기로 하고, 지금은 불투명한 맵으로 둔다.
pub type Mapping = BTreeMap<String, String>;

/// 하이퍼링크 클릭으로 보낼 수 있는 "동작".
///
/// `warpui_core::Action` 을 직접 참조하지 못하는 이유는 방향 때문이다 — `warpui` 가
/// 이 크레이트를 의존하므로 반대로 걸면 순환한다. 그래서 같은 모양의 트레이트를 두고
/// 포괄 구현으로 이어 붙인다.
pub trait Action: Any + Debug + Send + Sync {
    fn as_any(&self) -> &dyn Any;
}

impl<T> Action for T
where
    T: Any + Debug + Send + Sync,
{
    fn as_any(&self) -> &dyn Any {
        self
    }
}

/// 줄 수를 셀 수 있는 것.
pub trait LineCount {
    fn num_lines(&self) -> usize;
}

/// 서식 있는 텍스트 한 덩어리.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct FormattedText {
    pub lines: VecDeque<FormattedTextLine>,
}

impl FormattedText {
    pub fn new(lines: impl Into<VecDeque<FormattedTextLine>>) -> Self {
        Self {
            lines: lines.into(),
        }
    }

    /// 앞뒤의 빈 줄을 떼고 만든다.
    pub fn new_trimmed(lines: impl Into<VecDeque<FormattedTextLine>>) -> Self {
        let mut new = Self::new(lines);
        new.trim();
        new
    }

    /// 연속된 빈 줄은 파서가 이미 하나로 합치므로, 앞뒤 각각 최대 하나만 떼면 된다.
    fn trim(&mut self) {
        if let Some(FormattedTextLine::LineBreak) = self.lines.front() {
            self.lines.pop_front();
        }
        if let Some(FormattedTextLine::LineBreak) = self.lines.back() {
            self.lines.pop_back();
        }
    }

    pub fn is_empty(&self) -> bool {
        self.lines.is_empty()
    }

    /// 서식을 벗긴 순수 텍스트.
    pub fn raw_text(&self) -> String {
        self.lines
            .iter()
            .map(|line| line.raw_text())
            .collect::<Vec<_>>()
            .join("\n")
    }
}

impl LineCount for FormattedText {
    fn num_lines(&self) -> usize {
        self.lines.len()
    }
}

/// 인라인 조각들의 나열 = 한 줄의 내용.
pub type FormattedTextInline = Vec<FormattedTextFragment>;

/// 서식 있는 텍스트의 한 줄.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FormattedTextLine {
    Heading(FormattedTextHeader),
    Line(FormattedTextInline),
    OrderedList(OrderedFormattedIndentTextInline),
    UnorderedList(FormattedIndentTextInline),
    CodeBlock(CodeBlockText),
    TaskList(FormattedTaskList),
    LineBreak,
    HorizontalRule,
    Embedded(Mapping),
    Image(FormattedImage),
    Table(FormattedTable),
}

fn inline_raw_text(text: &[FormattedTextFragment]) -> String {
    text.iter().map(|f| f.text.as_str()).collect()
}

impl FormattedTextLine {
    /// 이 줄을 이루는 조각들. 조각이 없는 줄(빈 줄·구분선·임베드)은 빈 슬라이스.
    fn fragments(&self) -> &[FormattedTextFragment] {
        match self {
            Self::Heading(header) => &header.text,
            Self::Line(line) => line,
            Self::OrderedList(list) => &list.indented_text.text,
            Self::UnorderedList(list) => &list.text,
            Self::TaskList(task) => &task.text,
            Self::CodeBlock(_)
            | Self::LineBreak
            | Self::HorizontalRule
            | Self::Embedded(_)
            | Self::Image(_)
            | Self::Table(_) => &[],
        }
    }

    fn fragments_mut(&mut self) -> &mut [FormattedTextFragment] {
        match self {
            Self::Heading(header) => &mut header.text,
            Self::Line(line) => line,
            Self::OrderedList(list) => &mut list.indented_text.text,
            Self::UnorderedList(list) => &mut list.text,
            Self::TaskList(task) => &mut task.text,
            Self::CodeBlock(_)
            | Self::LineBreak
            | Self::HorizontalRule
            | Self::Embedded(_)
            | Self::Image(_)
            | Self::Table(_) => &mut [],
        }
    }

    /// 서식을 벗긴 순수 텍스트.
    pub fn raw_text(&self) -> String {
        match self {
            Self::CodeBlock(block) => block.code.clone(),
            Self::LineBreak | Self::HorizontalRule | Self::Embedded(_) => String::new(),
            Self::Image(image) => image.alt_text.clone(),
            Self::Table(table) => table.to_plain_text(),
            other => inline_raw_text(other.fragments()),
        }
    }

    /// 줄 전체의 굵기를 덮어쓴다.
    pub fn set_weight(&mut self, weight: Option<CustomWeight>) {
        for fragment in self.fragments_mut() {
            fragment.styles.weight = weight;
        }
    }

    /// 이 줄 안의 링크들을 **문자 인덱스 범위**와 함께 돌려준다.
    ///
    /// 범위는 줄 시작을 0 으로 하는 `char` 인덱스다(바이트가 아니다 — 호출부가 글리프
    /// 인덱스로 쓰기 때문에, 한글처럼 멀티바이트 문자에서 바이트로 세면 밑줄이 어긋난다).
    ///
    /// `include_action_links` 가 거짓이면 URL 링크만 돌려준다. 클릭 시 동작을 보내는
    /// 링크는 마우스 핸들러를 따로 다는 경로가 있어 호버 처리를 중복으로 걸면 안 된다.
    pub fn hyperlinks(&self, include_action_links: bool) -> Vec<(Range<usize>, Hyperlink)> {
        let mut out = Vec::new();
        let mut char_index = 0usize;
        for fragment in self.fragments() {
            let len = fragment.text.chars().count();
            if let Some(link) = &fragment.styles.hyperlink {
                let keep = match link {
                    Hyperlink::Url(_) => true,
                    Hyperlink::Action(_) => include_action_links,
                };
                if keep && len > 0 {
                    out.push((char_index..char_index + len, link.clone()));
                }
            }
            char_index += len;
        }
        out
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FormattedTextHeader {
    /// 1 = 가장 큰 제목.
    pub heading_size: usize,
    pub text: FormattedTextInline,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FormattedTaskList {
    pub complete: bool,
    pub indent_level: usize,
    pub text: FormattedTextInline,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FormattedIndentTextInline {
    pub indent_level: usize,
    pub text: FormattedTextInline,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CodeBlockText {
    pub lang: String,
    pub code: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OrderedFormattedIndentTextInline {
    /// 원문에 번호가 없거나 잘못됐으면 `None`.
    pub number: Option<usize>,
    pub indented_text: FormattedIndentTextInline,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FormattedImage {
    pub alt_text: String,
    pub source: String,
    /// `![alt](src "title")` 의 title. 빈 값은 `None` 으로 정규화한다.
    pub title: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum TableAlignment {
    #[default]
    Left,
    Center,
    Right,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FormattedTable {
    pub headers: Vec<FormattedTextInline>,
    pub alignments: Vec<TableAlignment>,
    pub rows: Vec<Vec<FormattedTextInline>>,
}

impl FormattedTable {
    /// 표를 서식 없는 텍스트로 편다. 셀은 탭, 행은 줄바꿈으로 가른다.
    ///
    /// 표를 그릴 수 없는 자리(예: 한 줄짜리 미리보기)에서 대신 쓰는 표현이라 정렬은
    /// 버린다 — 탭 구분은 붙여 넣었을 때 다른 도구가 다시 열 수 있는 형태이기도 하다.
    pub fn to_plain_text(&self) -> String {
        let row_text = |row: &Vec<FormattedTextInline>| {
            row.iter()
                .map(|cell| inline_raw_text(cell))
                .collect::<Vec<_>>()
                .join("\t")
        };
        let mut lines = Vec::with_capacity(self.rows.len() + 1);
        if !self.headers.is_empty() {
            lines.push(
                self.headers
                    .iter()
                    .map(|cell| inline_raw_text(cell))
                    .collect::<Vec<_>>()
                    .join("\t"),
            );
        }
        lines.extend(self.rows.iter().map(row_text));
        lines.join("\n")
    }
}

/// 한 줄 안의 서식 조각.
#[derive(Debug, Clone, Eq, PartialEq, Default)]
pub struct FormattedTextFragment {
    pub text: String,
    pub styles: FormattedTextStyles,
}

impl FormattedTextFragment {
    pub fn plain_text(text: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            styles: Default::default(),
        }
    }

    /// 클릭하면 동작을 보내는 링크 조각.
    pub fn hyperlink_action(text: impl Into<String>, action: impl Action + 'static) -> Self {
        Self {
            text: text.into(),
            styles: FormattedTextStyles {
                hyperlink: Some(Hyperlink::Action(Arc::new(action))),
                ..Default::default()
            },
        }
    }

    /// URL 로 가는 링크 조각.
    pub fn hyperlink_url(text: impl Into<String>, url: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            styles: FormattedTextStyles {
                hyperlink: Some(Hyperlink::Url(url.into())),
                ..Default::default()
            },
        }
    }

    pub fn raw_text(&self) -> String {
        self.text.clone()
    }
}

/// 내용 없이 서식만.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct FormattedTextStyles {
    pub weight: Option<CustomWeight>,
    pub italic: bool,
    pub underline: bool,
    pub strikethrough: bool,
    pub inline_code: bool,
    pub hyperlink: Option<Hyperlink>,
}

/// 링크 목적지 — URL 이거나, 클릭 시 보낼 동작.
#[derive(Debug, Clone)]
pub enum Hyperlink {
    Url(String),
    Action(Arc<dyn Action>),
}

impl Hyperlink {
    pub fn url(self) -> Option<String> {
        match self {
            Hyperlink::Url(url) => Some(url),
            Hyperlink::Action(_) => None,
        }
    }
}

/// URL 만 비교한다.
///
/// [`Action`] 은 `PartialEq` 가 없지만 서식 병합에는 `PartialEq` 가 필요하다. 동작을
/// 보내는 링크는 마크다운 파싱에서 생기지 않으므로(코드로만 만든다) URL 비교로 충분하다.
impl PartialEq for Hyperlink {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Url(left), Self::Url(right)) => left == right,
            _ => false,
        }
    }
}

impl Eq for Hyperlink {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trim_removes_one_leading_and_trailing_break() {
        let text = FormattedText::new_trimmed(vec![
            FormattedTextLine::LineBreak,
            FormattedTextLine::Line(vec![FormattedTextFragment::plain_text("가운데")]),
            FormattedTextLine::LineBreak,
        ]);
        assert_eq!(text.lines.len(), 1);
        assert_eq!(text.raw_text(), "가운데");
    }

    #[test]
    fn hyperlink_equality_ignores_actions() {
        let a = Hyperlink::Url("https://example.com".into());
        let b = Hyperlink::Url("https://example.com".into());
        assert_eq!(a, b);
        #[derive(Debug)]
        struct Dummy;
        let action = Hyperlink::Action(Arc::new(Dummy));
        assert_ne!(action, Hyperlink::Url("https://example.com".into()));
    }
}
