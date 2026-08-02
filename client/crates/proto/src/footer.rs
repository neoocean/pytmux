//! 화면 아래 **요약 구역**의 줄 예산 — 두 뷰가 같은 규칙을 쓴다.
//!
//! 구역에 들어가는 것은 블록(§10-13)과 Claude 항목(§10-11 P5)이고, 둘 다 있으면 정해진
//! 줄 수를 나눠 쓴다.
//!
//! # 왜 크기가 고정인가 (P5 결정 ③)
//!
//! 이 구역이 커지면 그만큼 **서버 캔버스가 화면 밖으로 밀린다**. 클라는 크롬을 뺀 크기를
//! 서버에 알리므로(TUI `CHROME_ROWS`), 구역이 자라면 새 크기를 알리고 다시 받아야 하고
//! 그 왕복 동안 화면이 출렁인다. 고정으로 두면 그 왕복이 아예 필요 없다.
//!
//! # 왜 여기(뷰 밖)인가
//!
//! 예산은 **판단**이지 그리기가 아니다. 뷰마다 적으면 TUI 는 5줄, GUI 는 7줄을 쓰는 식으로
//! 조용히 갈라지고, 그러면 같은 세션이 클라마다 다르게 잘려 보인다. 그리고 여기 있으면
//! 창을 띄우지 않고 시험된다(사용자 결정 2026-07-28: "로직은 밀고 뷰는 얇게").

/// 요약 구역이 쓰는 줄 수(항목만 — 머리줄·빈 줄은 별도).
pub const ROWS: usize = 5;

/// 구역이 펼쳐져 있나 (§10-20ⓔ · 사용자 요청 2026-08-02).
///
/// # 왜 기본이 접힘인가
///
/// 이 구역은 **늘 펼쳐져** 있었고, 그만큼 서버 캔버스가 상시로 여섯 줄 좁았다. 훑는
/// 용도의 요약이 화면의 주인공(패널)을 밀어내고 있던 셈이다. 사용자 요청도 접힘이다.
///
/// # 왜 접혀도 머리줄은 남나
///
/// 통째로 사라지면 **여는 손이 사라진다** — 다시 펼 자리가 화면에 없다. 머리줄은 그
/// 자리에서 개수(`블록 N개 · Claude N개`)까지 알려 주므로, 접힌 상태가 "없다"로
/// 읽히지도 않는다.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub enum Fold {
    /// 머리줄만. **기본값** — 되돌리는 변경이 오라클을 깨우게 여기에 `#[default]` 를 둔다.
    #[default]
    Closed,
    /// 머리줄 + 항목들.
    Open,
}

impl Fold {
    /// 눌렀을 때.
    pub fn toggled(self) -> Self {
        match self {
            Fold::Closed => Fold::Open,
            Fold::Open => Fold::Closed,
        }
    }

    pub fn is_open(self) -> bool {
        matches!(self, Fold::Open)
    }
}

/// 이 구역이 **크롬에서 가져가는 줄 수**(머리줄 포함). 그릴 것이 없으면 0.
///
/// # 왜 뷰 밖인가
///
/// 이 값이 곧 서버에 알리는 캔버스 높이의 일부다 — 한 줄이 틀리면 서버가 전 세션을
/// 다시 배치하고 그 프레임이 **같은 세션의 다른 클라에게도** 간다. 창을 띄우지 않고
/// 시험돼야 하는 부류라 판단은 여기 있고 뷰는 더하기만 한다(모듈 문서 §「왜 여기인가」).
pub fn rows(fold: Fold, has_blocks: bool, has_claude: bool) -> usize {
    if !has_blocks && !has_claude {
        return 0;
    }
    // 머리줄 하나는 접혀 있어도 남는다(여는 손).
    1 + if fold.is_open() { ROWS } else { 0 }
}

/// 블록과 Claude 를 **함께** 보일 때 Claude 가 가져가는 줄 수.
pub const CLAUDE_ROWS: usize = 3;

/// (블록 줄, Claude 줄). 합은 결코 [`ROWS`] 를 넘지 않는다.
///
/// 규칙은 세 가지뿐이다: ⑴한쪽만 있으면 그쪽이 다 쓴다 ⑵둘 다 있으면 Claude 가
/// [`CLAUDE_ROWS`] 를 가져가고 나머지가 블록 몫이다 ⑶둘 다 없으면 구역을 안 그린다.
///
/// Claude 를 먼저 떼는 이유: 블록은 한 줄이 한 명령이라 몇 개만 봐도 흐름이 읽히지만,
/// Claude 항목은 **직전 몇 줄이 지금 무슨 일이 벌어지는지** 그 자체다.
pub fn split(has_blocks: bool, has_claude: bool) -> (usize, usize) {
    match (has_blocks, has_claude) {
        (false, false) => (0, 0),
        (true, false) => (ROWS, 0),
        (false, true) => (0, ROWS),
        (true, true) => (ROWS - CLAUDE_ROWS, CLAUDE_ROWS),
    }
}

/// 마지막 `rows` 개. **최근 것이 관심사**다 — 오래된 것은 스크롤백과 트랜스크립트에 있다.
///
/// 앞에서 자르면(`take(rows)`) 화면에는 **가장 오래된** 것이 남아, 방금 친 명령이 목록에
/// 없는 상태가 된다. 그 증상은 "블록이 안 생긴다"와 구분되지 않는다.
pub fn tail<T>(items: &[T], rows: usize) -> &[T] {
    let start = items.len().saturating_sub(rows);
    &items[start..]
}

/// `max_cols` **표시 폭**에 맞춰 자르고, 잘렸으면 `…` 를 붙인다.
///
/// # 왜 글자 수가 아니라 폭인가
///
/// 명령줄과 cwd 에는 한글·CJK 가 흔하고 그 글자들은 두 칸을 먹는다. 글자 수로 자르면
/// 한글이 섞인 줄만 화면 밖으로 밀린다 — 그리고 그건 "가끔 삐져나온다"로 보여 원인이
/// 안 잡힌다.
///
/// # 왜 자르나
///
/// TUI 는 `TuiText::truncate()` 가 터미널 폭에서 잘라 주지만 GUI 의 `Text` 는 **그냥
/// 길어진다**(실측 2026-07-28: 긴 명령 두 줄이 창 밖으로 흘러나갔다). 폭 예산을 여기서
/// 명시적으로 다룬다.
pub fn elide(text: &str, max_cols: usize) -> String {
    use unicode_width::UnicodeWidthChar;

    if max_cols == 0 {
        return String::new();
    }
    let total: usize = text.chars().map(|c| c.width().unwrap_or(0)).sum();
    if total <= max_cols {
        return text.to_owned();
    }
    // `…` 자체가 한 칸을 먹는다 — 그 자리를 남기지 않으면 자른 결과가 다시 넘친다.
    let budget = max_cols - 1;
    let mut used = 0;
    let mut out = String::new();
    for c in text.chars() {
        let w = c.width().unwrap_or(0);
        if used + w > budget {
            break;
        }
        used += w;
        out.push(c);
    }
    out.push('…');
    out
}

/// 요약 구역의 머리줄.
///
/// **줄을 늘리지 않고** 여기에만 덧붙인다 — 이 구역이 커지면 그만큼 서버 캔버스가 밀린다.
///
/// - `mode`(권한 모드)가 여기 붙는 이유: 지금 무엇이 자동으로 허용되는지가 화면 어디에도
///   없으면 사용자는 **거부 줄을 보고서야** 안다. 값은 해석하지 않고 그대로 보인다
///   (서버·Claude 가 이름을 늘려도 우리가 번역하려 들면 모르는 값이 사라진다).
/// - `remote` 이고 Claude 항목이 없으면 한 마디 붙인다. 그냥 비면 **"안 되는 것"과
///   "안 쓴 것"이 같아 보인다**.
/// `fold` 는 **접힘 표식**을 정한다(`▸` 닫힘 / `▾` 열림). 표식이 없으면 접힌 구역이
/// "블록이 있는데 안 그려진다"로 읽히고, 누를 수 있다는 것도 화면에 없다.
pub fn head(blocks: usize, claude: usize, mode: Option<&str>, remote: bool, fold: Fold) -> String {
    use base::i18n::{t, tf};

    let mut head = String::from(if fold.is_open() { "▾" } else { "▸" });
    if blocks > 0 {
        head.push_str(&tf(" 블록 {n}개", &[("n", blocks.to_string().as_str())]));
    }
    if claude > 0 {
        if blocks > 0 {
            head.push_str(" ·");
        }
        head.push_str(&tf(" Claude {n}개", &[("n", claude.to_string().as_str())]));
        if let Some(mode) = mode {
            head.push_str(&format!(" [{mode}]"));
        }
    } else if remote {
        head.push_str(t(" · 원격 상류가 Claude 대화를 안 보낸다"));
    }
    head
}

/// 문자열의 표시 폭. [`elide`] 와 같은 잣대를 쓰는 것이 요점이다.
pub fn width(text: &str) -> usize {
    use unicode_width::UnicodeWidthChar;
    text.chars().map(|c| c.width().unwrap_or(0)).sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_area_never_grows_past_its_budget() {
        // 넘으면 서버 캔버스가 화면 밖으로 밀리고, 그건 리사이즈를 다시 알려야 고쳐진다.
        for &blocks in &[false, true] {
            for &claude in &[false, true] {
                let (b, c) = split(blocks, claude);
                assert!(b + c <= ROWS, "{blocks}/{claude} → {b}+{c} > {ROWS}");
            }
        }
    }

    #[test]
    fn whichever_is_alone_gets_the_whole_area() {
        // 한쪽만 있는데 절반만 쓰면 남는 줄이 그냥 빈 채로 캔버스를 밀고 있게 된다.
        assert_eq!(split(true, false), (ROWS, 0));
        assert_eq!(split(false, true), (0, ROWS));
    }

    #[test]
    fn nothing_to_show_means_no_area_at_all() {
        assert_eq!(split(false, false), (0, 0));
    }

    #[test]
    fn sharing_gives_claude_the_agreed_share() {
        let (b, c) = split(true, true);
        assert_eq!(c, CLAUDE_ROWS);
        assert_eq!(b, ROWS - CLAUDE_ROWS);
        assert!(b > 0, "블록이 0줄이 되면 셸 쪽이 통째로 사라진다");
    }

    #[test]
    fn the_tail_keeps_the_newest_not_the_oldest() {
        // ★ 앞에서 자르면 방금 친 명령이 목록에 없다 — "블록이 안 생긴다"로 읽힌다.
        let items = [1, 2, 3, 4, 5, 6, 7];
        assert_eq!(tail(&items, 3), &[5, 6, 7]);
    }

    #[test]
    fn asking_for_more_than_there_is_returns_everything() {
        let items = [1, 2];
        assert_eq!(tail(&items, 5), &[1, 2]);
        assert_eq!(tail::<i32>(&[], 5), &[] as &[i32]);
        assert_eq!(tail(&items, 0), &[] as &[i32]);
    }

    #[test]
    fn text_that_fits_is_left_alone() {
        // 안 넘치는 줄에 `…` 를 붙이면 사용자는 뒤가 더 있는 줄 안다.
        assert_eq!(elide("ls -la", 10), "ls -la");
        assert_eq!(elide("ls -la", 6), "ls -la", "딱 맞는 것은 안 자른다");
    }

    #[test]
    fn the_result_never_exceeds_the_budget() {
        // ★ 자른 결과가 다시 넘치면 자른 뜻이 없다 — `…` 도 한 칸을 먹는다.
        for cols in 1..12 {
            assert!(width(&elide("abcdefghijklmnop", cols)) <= cols, "cols={cols}");
        }
    }

    #[test]
    fn wide_characters_count_as_two_columns() {
        // ★ 글자 수로 자르면 한글이 섞인 줄만 화면 밖으로 밀린다.
        assert_eq!(width("한글"), 4);
        let got = elide("한글테스트", 5);
        assert!(width(&got) <= 5, "{got:?} 가 5칸을 넘는다");
        assert!(got.ends_with('…'));
        // 반 글자를 남기지 않는다 — 두 칸짜리는 통째로 들어가거나 빠진다.
        assert_eq!(got, "한글…");
    }

    #[test]
    fn the_permission_mode_is_shown_verbatim() {
        // ★ 무엇이 자동으로 허용되는지가 화면에 없으면 사용자는 거부 줄을 보고서야 안다.
        // 값을 번역하려 들면 상류가 이름을 늘렸을 때 그 값이 조용히 사라진다.
        let line = head(0, 3, Some("acceptEdits"), false, Fold::Open);
        assert!(line.contains("Claude 3개"), "{line}");
        assert!(line.contains("[acceptEdits]"), "{line}");
    }

    #[test]
    fn a_remote_pane_without_a_conversation_says_why() {
        // 그냥 비면 "안 되는 것"과 "안 쓴 것"이 같아 보인다.
        assert!(head(2, 0, None, true, Fold::Open).contains("원격 상류"));
        // 로컬인데 대화가 없는 것은 그냥 없는 것이다 — 설명할 게 없다.
        assert!(!head(2, 0, None, false, Fold::Open).contains("원격"));
        // 원격이어도 대화가 오면 그 안내는 필요 없다.
        assert!(!head(2, 1, None, true, Fold::Open).contains("원격"));
    }

    #[test]
    fn both_kinds_are_named_when_both_are_there() {
        let line = head(2, 3, None, false, Fold::Open);
        assert!(line.contains("블록 2개") && line.contains("Claude 3개"), "{line}");
    }

    #[test]
    fn the_area_starts_folded_and_keeps_its_head_line() {
        // ★ 기본이 펼침이면 서버 캔버스가 **상시로** 여섯 줄 좁다(§10-20ⓔ 이전 상태).
        assert_eq!(Fold::default(), Fold::Closed, "기본이 접힘이 아니다");
        // 접혀도 머리줄은 남는다 — 통째로 사라지면 **다시 펼 자리가 화면에 없다**.
        assert_eq!(rows(Fold::Closed, true, false), 1);
        assert_eq!(rows(Fold::Closed, false, true), 1);
        assert_eq!(rows(Fold::Open, true, false), 1 + ROWS);
        assert_eq!(rows(Fold::Open, true, true), 1 + ROWS);
        // 그릴 것이 없으면 머리줄도 없다(종전과 같다).
        assert_eq!(rows(Fold::Closed, false, false), 0);
        assert_eq!(rows(Fold::Open, false, false), 0);
        // 펼침이 가져가는 몫이 곧 예산이다 — 어긋나면 캔버스가 그만큼 밀린다.
        assert_eq!(
            rows(Fold::Open, true, true) - rows(Fold::Closed, true, true),
            ROWS
        );
    }

    #[test]
    fn the_head_line_says_which_way_it_is_folded() {
        // 표식이 없으면 접힌 구역이 "블록이 있는데 안 그려진다"로 읽히고, 누를 수
        // 있다는 것도 화면에 없다.
        let closed = head(2, 0, None, false, Fold::Closed);
        let open = head(2, 0, None, false, Fold::Open);
        assert!(closed.starts_with('▸'), "{closed}");
        assert!(open.starts_with('▾'), "{open}");
        // 개수는 접혀도 보인다 — 접힘이 "없다"로 읽히면 안 된다.
        assert!(closed.contains("블록 2개"), "{closed}");
    }

    #[test]
    fn folding_is_a_toggle() {
        assert_eq!(Fold::Closed.toggled(), Fold::Open);
        assert_eq!(Fold::Open.toggled(), Fold::Closed);
    }

    #[test]
    fn a_zero_budget_produces_nothing_not_an_ellipsis() {
        // 자리가 없는데 `…` 하나를 그리면 그 칸이 다시 넘친다.
        assert_eq!(elide("abc", 0), "");
    }
}
