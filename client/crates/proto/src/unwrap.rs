//! 복사한 글에서 **앱이 접은 줄바꿈**을 되돌린다(설정 `copy-unwrap`).
//!
//! # 무엇을 되돌리나
//!
//! 패널 안 프로그램(Claude·less·pager…)은 자기 폭에 맞춰 문단을 접는다. 그 화면을
//! 드래그해 복사하면 **접힌 자리마다 줄바꿈과 매달림 들여쓰기**가 딸려 온다 — 붙여넣는
//! 곳의 폭은 다르므로 그 줄바꿈은 뜻이 없고, 오히려 문단을 조각낸다.
//!
//! 되돌리는 것은 **앱이 접은 것**뿐이다. 사용자가 친 줄바꿈(빈 줄·의도된 줄 끝·코드
//! 블록)은 그대로 둔다 — 그래서 판정 게이트가 셋이나 있다.
//!
//! # 왜 규칙을 정본에서 뜨나
//!
//! 게이트가 여섯이고(테두리 떼기 · 구분선 경계 · 최소 채움 · 매달림 들여쓰기 상한 ·
//! 의도된 줄 끝 · 여유 칸) 그 상수들은 실측으로 굳은 값이다. 손으로 옮기면 "우리가
//! 이해한 규칙"이 된다 — `scripts/gen_unwrap_cases.py` 가 **정본 함수를 직접 호출해**
//! 입출력 짝을 뜨고, `unwrap_conformance.rs` 가 그것과 대조한다.

use crate::compose::display_width;

/// 매달림 들여쓰기 상한 — 더 깊으면 코드 블록으로 본다(정본 `_UNWRAP_HANG_MAX`).
const HANG_MAX: usize = 12;
/// 이보다 좁은 블록은 접힐 일이 없다(정본 `_UNWRAP_MIN_FILL`).
const MIN_FILL: usize = 24;
/// 접힘 폭 추정의 여유 칸(정본 `_UNWRAP_SLACK`).
///
/// 앱은 보통 오른쪽에 한두 칸을 남기고 접는데(입력박스 패딩) 관측 최대 채움은 그만큼
/// 작게 잡힌다 — 그러면 이어지는 줄의 첫 낱말이 `|`·`&` 처럼 한 글자일 때 "들어갈
/// 자리가 있었다"로 오판해 접힘을 놓친다(정본 주석의 실측).
const SLACK: usize = 2;
/// 이 글자로 끝난 줄은 **의도된 줄 끝**이다(정본 `_UNWRAP_TAIL_STOP`).
const TAIL_STOP: &[char] = &[':', ';', '{', '}', '\\'];

const PAD: &[char] = &[' ', '\t'];

fn is_box(ch: char) -> bool {
    ('\u{2500}'..='\u{257f}').contains(&ch)
}

/// 줄 앞의 `공백* 박스런` 을 뗀다.
///
/// ⚠ 붙여넣기 쪽 규칙과 **다르다**: 안쪽 패딩 한 칸은 **남긴다** — 그 칸이 매달림
/// 들여쓰기 신호다(정본 `_cut_lead_box` 대 `_cut_lead_box_pad`).
fn cut_lead_box(s: &str) -> &str {
    let chars: Vec<(usize, char)> = s.char_indices().collect();
    let mut i = 0;
    while i < chars.len() && PAD.contains(&chars[i].1) {
        i += 1;
    }
    let mut j = i;
    while j < chars.len() && is_box(chars[j].1) {
        j += 1;
    }
    if j == i {
        return s;
    }
    match chars.get(j) {
        Some((at, _)) => &s[*at..],
        None => "",
    }
}

/// 줄 끝의 `공백* 박스런 공백* \r?` 를 뗀다(박스런이 없으면 무변경).
fn cut_trail_box(s: &str) -> &str {
    let chars: Vec<(usize, char)> = s.char_indices().collect();
    let mut i = chars.len();
    if i > 0 && chars[i - 1].1 == '\r' {
        i -= 1;
    }
    let mut j = i;
    while j > 0 && PAD.contains(&chars[j - 1].1) {
        j -= 1;
    }
    let mut k = j;
    while k > 0 && is_box(chars[k - 1].1) {
        k -= 1;
    }
    if k == j {
        // 박스런 없음 → 끝의 공백·`\r` 까지 그대로 둔다(정본과 같다).
        return s;
    }
    while k > 0 && PAD.contains(&chars[k - 1].1) {
        k -= 1;
    }
    match chars.get(k) {
        Some((at, _)) => &s[..*at],
        None => "",
    }
}

fn line_indent(s: &str) -> usize {
    s.len() - s.trim_start_matches(' ').len()
}

/// `cur` 가 `prev` 의 이어지는 줄(앱이 접은 자리)인가 — 게이트 ①②③.
fn is_app_wrap(prev: &str, cur: &str, fill: usize, prev_cells: usize) -> bool {
    if prev.trim().is_empty() || cur.trim().is_empty() {
        return false;
    }
    // ① 매달림 들여쓰기가 없거나 너무 깊다.
    let indent = line_indent(cur);
    if indent == 0 || indent > HANG_MAX {
        return false;
    }
    // ③ 의도된 줄 끝.
    if prev.chars().next_back().is_some_and(|c| TAIL_STOP.contains(&c)) {
        return false;
    }
    // ② 이어지는 줄의 첫 낱말이 **앞 줄에 못 들어갔다** = 접힌 것이다.
    let word = cur.trim().split(' ').next().unwrap_or("");
    prev_cells + 1 + display_width(word) + SLACK > fill
}

/// 복사한 글에서 앱이 접은 줄바꿈을 되돌린다(정본 `unwrap_copy_text`).
///
/// - `width` — 선택이 일어난 패널의 내용 폭(칸). **모르면 손대지 않는다**: 판정 근거가
///   없는데 이어붙이면 사용자가 고른 그대로가 아니게 된다.
/// - `first_col` — 첫 줄이 시작한 패널 내 열. 첫 줄만 그 칸에서 잘려 나와 다른 줄보다
///   짧으므로 칸 계산에 되돌려 줘야 접힘 폭 추정이 어긋나지 않는다.
///
/// 한 줄 선택은 사용자가 고른 그대로가 정답이라 손대지 않는다.
pub fn unwrap_copy_text(text: &str, width: usize, first_col: usize) -> String {
    if text.is_empty() || !text.contains('\n') || width == 0 {
        return text.to_owned();
    }
    let mut lines: Vec<String> = Vec::new();
    // 테두리·구분선만이던 줄이 있던 **자리**. 그 줄은 버리되 위아래가 붙지 않게 막는다 —
    // 안 그러면 구분선을 지운 뒤 남남인 두 줄(윗 대화 끝줄과 입력줄)이 이어붙는다.
    let mut barrier: std::collections::BTreeSet<usize> = Default::default();
    for raw in text.split('\n') {
        let s = cut_trail_box(cut_lead_box(raw)).trim_end().to_owned();
        if s.is_empty() && !raw.trim().is_empty() {
            barrier.insert(lines.len());
            continue;
        }
        lines.push(s);
    }
    if lines.len() < 2 {
        return lines.join("\n");
    }
    // 칸 폭: 첫 줄만 시작 열을 더해 다른 줄과 같은 원점(패널 열 0)으로 맞춘다.
    let cells: Vec<usize> = lines
        .iter()
        .enumerate()
        .map(|(i, l)| display_width(l) + if i == 0 { first_col } else { 0 })
        .collect();
    let fill = cells.iter().copied().max().unwrap_or(0);
    if fill < MIN_FILL.max(width / 2) {
        // 폭 근처까지 안 간 블록 = 접힘이 아니다.
        return lines.join("\n");
    }
    let mut out: Vec<String> = vec![lines[0].clone()];
    for i in 1..lines.len() {
        if !barrier.contains(&i) && is_app_wrap(&lines[i - 1], &lines[i], fill, cells[i - 1]) {
            let last = out.len() - 1;
            out[last] = format!("{} {}", out[last], lines[i].trim());
        } else {
            out.push(lines[i].clone());
        }
    }
    out.join("\n")
}
