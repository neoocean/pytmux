//! 라이브 **입력박스 긁기** — 작성창이 프롬프트를 인계할 때 쓴다(패리티 G9c).
//!
//! # 무엇을 하나
//!
//! 활성 패널의 렌더된 행에서 **지금 입력칸에 들어 있는 글**을 뽑는다. 작성창
//! (`esc Insert`)이 그 값으로 시작하고, 투입할 때 **그 길이만큼 백스페이스로 비운 뒤**
//! 새 글을 넣는다. 시드와 비우기가 **같은 값**을 써야 중복도 잔여도 없다.
//!
//! # 왜 화면을 긁나 (키 추적이 아니라)
//!
//! 파이썬 클라도 처음에는 자기가 받은 키를 쌓아 추적했다. 그 값은 **줄바꿈이 CR 로
//! 도착하면 '제출'로 오인해 버퍼를 비운다** — Claude 의 멀티라인 입력이 정확히 그 꼴이라
//! 마지막 줄만 남았다(p4 64741 에서 화면 긁기를 1차로 역전). 화면은 자식이 실제로 들고
//! 있는 것 그대로라, 이 클라를 안 거친 입력(원격 제어·재접속)도 잡힌다.
//!
//! # 이 파서는 **정본을 옮긴 것**이다
//!
//! 정본은 `pytmuxlib/plugins/claude-code/claude.py::claude_input_box` 다. 주석마다 실제
//! 결함에서 온 예외가 붙어 있어(모서리 없는 현행 UI · busy 중 구획선 없음 · 멀티라인
//! 중간의 빈 줄 · 마커 뒤 비분리공백 · 연속 줄 정렬 폭 학습 · 큐 대기 플레이스홀더)
//! 기대값을 우리가 다시 해석하지 않는다 — `scripts/gen_prompt_box_fixture.py` 가 그
//! 함수를 **직접 호출해** 표를 만들고 `tests/prompt_box_conformance.rs` 가 대조한다.
//!
//! # 세 가지 반환값은 **서로 다른 뜻**이다
//!
//! - `None` — 긁을 수 없다(입력박스를 못 찾았다). 호출부는 **초안으로 떨어진다**.
//! - `Some("")` — 박스가 실제로 **비어 있다**. 빈 시드가 맞다.
//! - `Some(글)` — 그 글이 지금 입력칸에 있다.
//!
//! 앞의 둘을 하나로 뭉치면 빈 입력칸이 지난 초안을 되살린다.

/// 박스 위/아래 모서리(좌) — 구 Claude UI.
const BOX_TOP: [char; 2] = ['╭', '┌'];
const BOX_BOTTOM: [char; 2] = ['╰', '└'];
/// 박스 세로 테두리(유니코드/ASCII 폴백).
const BOX_SIDE: [char; 2] = ['│', '|'];
/// 입력 프롬프트 마커: 최신 Claude=`❯`(U+276F), 구=`>`.
const PROMPT_MARK: [char; 2] = ['❯', '>'];
/// 가로줄 구획(유니코드 실선/굵은선/겹선 + ASCII 폴백).
const RULE_CHARS: [char; 4] = ['─', '━', '═', '-'];
/// 이보다 짧으면 구획선으로 안 본다(`--` 같은 본문 배제).
const RULE_MIN: usize = 3;

/// 마커 앞 패딩으로 쓰이는 것들 — 공백과 **비분리공백**이다.
///
/// 최신 Claude 는 마커 뒤에 `\u{a0}` 를 넣는다. 보통 공백만 떼면 그 한 칸이 남아
/// 작성창 시드가 한 칸 밀린다.
fn is_pad(c: char) -> bool {
    c == ' ' || c == '\u{a0}'
}

fn lstrip_pad(line: &str) -> &str {
    line.trim_start_matches(is_pad)
}

/// 모서리·세로 테두리 없이 **가로줄만**으로 입력 구획을 그리는 현행 UI 의 구획선인가.
///
/// 대화 본문의 `---`(마크다운 구분선)도 참이 될 수 있지만, 탐색은 커서에서 **가장
/// 가까운** 줄만 취하므로(커서 = 입력 구획 안) 먼 오검출은 닿지 않는다.
fn is_box_rule(line: &str) -> bool {
    let s = line.trim();
    s.chars().count() >= RULE_MIN && s.chars().all(|c| RULE_CHARS.contains(&c))
}

/// footer 힌트 줄인가(정본 `_FOOTER_HINT_RE` — 대소문자 무시).
///
/// 정규식 크레이트를 들이지 않는다 — 의존을 하나 더 넣으면 라이선스 경계
/// (`PROVENANCE.md`)까지 넓어지고, 이 표는 다섯 갈래뿐이다.
fn footer_hint(s: &str) -> bool {
    let lower = s.to_lowercase();
    if lower.contains("for shortcuts") || lower.contains("/help") || lower.contains("esc to") {
        return true;
    }
    // `shift\s*\+\s*tab` · `ctrl\s*\+` — `+` 앞뒤 공백이 있을 수 있다.
    plus_after(&lower, "shift").is_some_and(|rest| rest.trim_start().starts_with("tab"))
        || plus_after(&lower, "ctrl").is_some_and(|_| true)
}

/// `word` 뒤에 (공백들 +) `+` 가 오면 그 다음 조각. 아니면 `None`.
fn plus_after<'a>(hay: &'a str, word: &str) -> Option<&'a str> {
    let mut from = 0;
    while let Some(at) = hay[from..].find(word) {
        let after = from + at + word.len();
        let rest = hay[after..].trim_start();
        if let Some(rest) = rest.strip_prefix('+') {
            return Some(rest);
        }
        from = after;
    }
    None
}

/// 박스 한 줄에서 좌우 세로 테두리와 **바깥 패딩 한 칸**을 떼고 안쪽 내용을.
///
/// 테두리가 없으면(박스 없는 입력 줄) 줄 전체를 우측 공백만 떼고 돌려준다.
fn box_inner(line: &str) -> String {
    let chars: Vec<char> = line.chars().collect();
    let Some(l) = chars.iter().position(|c| BOX_SIDE.contains(c)) else {
        return line.trim_end().to_owned();
    };
    // 오른쪽 테두리는 **왼쪽 것보다 뒤에서만** 찾는다(한 칸짜리 줄에서 같은 문자를
    // 두 번 세지 않으려는 것 — 정본의 `range(len-1, l, -1)` 과 같다).
    let r = (l + 1..chars.len())
        .rev()
        .find(|&i| BOX_SIDE.contains(&chars[i]))
        .unwrap_or(chars.len());
    let inner: String = chars[l + 1..r].iter().collect();
    let inner = inner.strip_prefix(' ').unwrap_or(&inner);
    inner.trim_end().to_owned()
}

/// 큐 대기 중 라이브 입력칸에 뜨는 플레이스홀더인가(실제 입력이 아니다).
///
/// 2026-07-18 제보: 이 안내문이 작성창 시드로 딸려 왔다.
fn is_placeholder(text: &str) -> bool {
    text.to_lowercase()
        .starts_with("press up to edit queued messages")
}

/// 구획선이 없을 때 — 현행 Claude 는 **busy·큐 대기 중** 입력 구획선을 안 그린다 —
/// 커서를 품은 **논리 프롬프트 블록**의 행들. 입력줄이 아니면 `None`.
///
/// 블록 = 마커 줄 하나 + 그 아래로 이어지는 들여쓴 연속 줄(다음 마커·빈 줄·구획선·
/// footer 힌트 전까지). 종전 정본은 여기서 커서 한 줄만 긁어, 여러 줄 프롬프트에서
/// 이웃 줄이 빠졌다.
fn prompt_block_rows(lines: &[String], anchor: usize) -> Option<Vec<usize>> {
    let n = lines.len();
    let mut start = None;
    for i in (0..=anchor).rev() {
        let s = lines[i].trim();
        if s.is_empty() {
            break; // 위쪽 빈 줄 = 블록 경계
        }
        if lstrip_pad(&lines[i])
            .chars()
            .next()
            .is_some_and(|c| PROMPT_MARK.contains(&c))
        {
            start = Some(i);
            break;
        }
        if is_box_rule(&lines[i]) || footer_hint(s) {
            break; // 구획선/footer 를 먼저 만남 = 입력 블록 밖
        }
    }
    let start = start?;
    let mut rows = vec![start];
    for i in start + 1..n {
        let s = lines[i].trim();
        if s.is_empty() || is_box_rule(&lines[i]) || footer_hint(s) {
            break;
        }
        if lstrip_pad(&lines[i])
            .chars()
            .next()
            .is_some_and(|c| PROMPT_MARK.contains(&c))
        {
            break; // 다음 프롬프트 블록 시작
        }
        rows.push(i);
    }
    Some(rows)
}

/// 커서 행을 모를 때의 앵커 — 아래에서부터 박스 테두리·footer·빈 줄을 건너뛴 첫 줄.
fn find_anchor(lines: &[String]) -> Option<usize> {
    for i in (0..lines.len()).rev() {
        let s = lines[i].trim();
        if s.is_empty() {
            continue;
        }
        let first = s.chars().next()?;
        if BOX_TOP.contains(&first) || BOX_BOTTOM.contains(&first) || is_box_rule(s) {
            continue;
        }
        if footer_hint(s) {
            continue;
        }
        return Some(i);
    }
    None
}

/// 앵커를 감싸는 박스의 `(top, bottom)`. 둘 다 못 찾으면 박스 없는 입력이다.
///
/// 구획선(현행 UI)은 위아래가 **같은 모양**이라 탐색 **방향**이 top/bottom 을 정한다:
/// 커서 위의 첫 구획선 = top, 아래의 첫 구획선 = bottom.
///
/// ⚠ 첫 글자를 볼 때 **비었는지 먼저** 본다. 정본이 이 자리에서 결함을 냈다 —
/// 파이썬에서 `"" in _BOX_TOP` 은 `True` 라(빈 문자열은 모든 문자열의 부분문자열)
/// 멀티라인 프롬프트 **중간의 빈 줄**이 거짓 top 으로 잡혔고, 그러면 커서 줄만
/// 인계됐다(2026-07-16). Rust 에서는 `Option<char>` 이 그 함정을 타입으로 막지만,
/// 같은 판정을 지키려면 빈 줄에서 **아무 결정도 하지 않아야** 한다.
fn box_bounds(lines: &[String], cursor_y: usize) -> (Option<usize>, Option<usize>) {
    let mut top = None;
    for i in (0..=cursor_y).rev() {
        if i != cursor_y && is_box_rule(&lines[i]) {
            top = Some(i);
            break;
        }
        // 여기는 정본이 `lstrip()`(모든 공백)을 쓴다 — 아래 블록 탐색의
        // `lstrip("\xa0 ")` 와 다르다. 다르게 옮기면 탭이 든 줄에서 갈린다.
        let Some(c) = lines[i].trim_start().chars().next() else {
            continue;
        };
        if BOX_TOP.contains(&c) {
            top = Some(i);
            break;
        }
        if BOX_BOTTOM.contains(&c) && i != cursor_y {
            break;
        }
    }
    let mut bottom = None;
    for i in cursor_y..lines.len() {
        if i != cursor_y && is_box_rule(&lines[i]) {
            bottom = Some(i);
            break;
        }
        let Some(c) = lines[i].trim_start().chars().next() else {
            continue;
        };
        if BOX_BOTTOM.contains(&c) {
            bottom = Some(i);
            break;
        }
        if BOX_TOP.contains(&c) && i != cursor_y {
            break;
        }
    }
    (top, bottom)
}

/// 패널 화면 행에서 **라이브 입력박스의 지금 텍스트**를 뽑는다(best-effort).
///
/// `wrap` 은 **윗줄과 개행 없이 이어지는**(자동 줄바꿈) 행 인덱스다 — 서버가 `screen`
/// 프레임에 실어 보낸다. 하드 개행(`Shift+Enter`)과 갈리는 유일한 자리이므로 이 값이
/// 없으면 붙여넣은 코드 블록이 통째로 한 줄이 된다.
///
/// 반환값 세 가지의 뜻은 모듈 문서 참조.
pub fn input_text(lines: &[String], wrap: &[usize], cursor_y: Option<usize>) -> Option<String> {
    if lines.is_empty() {
        return None;
    }
    let cursor_y = match cursor_y {
        Some(c) if c < lines.len() => c,
        _ => find_anchor(lines)?,
    };
    let (top, bottom) = box_bounds(lines, cursor_y);
    let rows: Vec<usize> = match (top, bottom) {
        (Some(t), Some(b)) if t < b => (t + 1..b).collect(),
        // 박스를 못 찾음 — 마커로 논리 프롬프트 블록을 찾는다. 그것도 없으면 입력줄이
        // 아니므로 **긁지 않는다**(호출부가 초안으로 떨어진다).
        _ => prompt_block_rows(lines, cursor_y)?,
    };
    if rows.is_empty() {
        return Some(String::new());
    }
    let mut parts: Vec<String> = Vec::new();
    // 연속 줄에서 떼어낼 **정렬 들여쓰기 폭**. 첫 줄에서 학습한다 — 연속 줄은 첫 줄의
    // 텍스트 시작 열에 맞춰 들여써지므로, 첫 줄이 마커·패딩으로 소비한 폭이 곧 그 값이다.
    // 종전 정본은 2칸으로 못박아, 세로 테두리가 없어 바깥 패딩이 안쪽에 남는 현행 UI 에서
    // 한 칸이 덜 떼여 둘째 줄부터 공백이 붙어 나왔다(2026-07-16).
    let mut indent = 2usize;
    for (k, &ri) in rows.iter().enumerate() {
        let inner = box_inner(&lines[ri]);
        if k == 0 {
            let t = lstrip_pad(&inner);
            // 마커와 **그 뒤 한 칸**을 뗀다. 안 떼면 시드에 `❯` 가 딸려 온다.
            let t = match t.chars().next() {
                Some(c) if PROMPT_MARK.contains(&c) => {
                    let rest = &t[c.len_utf8()..];
                    match rest.chars().next() {
                        Some(p) if is_pad(p) => &rest[p.len_utf8()..],
                        _ => rest,
                    }
                }
                _ => t,
            };
            // 폭은 **문자 수**로 센다(바이트로 세면 한글이 섞인 줄에서 어긋난다).
            indent = inner.chars().count().saturating_sub(t.chars().count());
            parts.push(t.trim_end().to_owned());
            continue;
        }
        // 연속 줄: 정렬용 들여쓰기(indent 칸까지)만 떼고 잇는다. 그 이상은 사용자가 친
        // 것이므로 **보존한다**(붙여넣은 코드 블록 등).
        let chars: Vec<char> = inner.chars().collect();
        let mut j = 0;
        while j < indent && j < chars.len() && chars[j] == ' ' {
            j += 1;
        }
        let body: String = chars[j..].iter().collect();
        let body = body.trim_end();
        parts.push(if wrap.contains(&ri) {
            body.to_owned()
        } else {
            format!("\n{body}")
        });
    }
    let text = parts.concat();
    if is_placeholder(text.trim()) {
        return Some(String::new());
    }
    Some(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lines(raw: &[&str]) -> Vec<String> {
        raw.iter().map(|s| (*s).to_owned()).collect()
    }

    #[test]
    fn nothing_to_scrape_is_not_the_same_as_an_empty_box() {
        // ★ 이 구분이 이 모듈의 계약이다. 뭉치면 **빈 입력칸이 지난 초안을 되살린다**.
        assert_eq!(input_text(&lines(&[]), &[], None), None, "빈 화면");
        assert_eq!(
            input_text(&lines(&["────────────", "❯", "────────────"]), &[], Some(1)),
            Some(String::new()),
            "빈 입력칸"
        );
    }

    #[test]
    fn the_prompt_marker_and_its_padding_are_stripped() {
        // 최신 Claude 는 마커 뒤에 **비분리공백**을 쓴다. 보통 공백만 떼면 한 칸이 남는다.
        let nbsp = lines(&["──────", "❯\u{a0}hello", "──────"]);
        assert_eq!(input_text(&nbsp, &[], Some(1)), Some("hello".to_owned()));
    }

    #[test]
    fn a_footer_hint_is_not_part_of_the_input() {
        // 힌트 줄이 블록에 들려 오면 작성창에 안내문이 딸려 온다.
        assert!(footer_hint("? for shortcuts"));
        assert!(footer_hint("shift + tab to cycle"));
        assert!(footer_hint("ctrl+c to quit"));
        assert!(footer_hint("esc to interrupt"));
        assert!(!footer_hint("shifting tabs by hand"), "낱말만 겹치는 줄");
    }

    #[test]
    fn a_line_of_dashes_is_a_rule_but_two_dashes_are_not() {
        assert!(is_box_rule("────"));
        assert!(is_box_rule("---"));
        assert!(!is_box_rule("--"), "본문의 `--` 를 구획선으로 보면 안 된다");
        assert!(!is_box_rule("- 목록 항목"));
    }
}
