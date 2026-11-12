//! Claude 패널의 **프롬프트 바** 규칙 한 벌(pytmux-587) — 고정 바(`draw_pinned`)와
//! `:` 작성 중 미리보기(`draw_preview`).
//!
//! 정본은 `plugins/claude-prompt-history/render.py` 다. 거기서는 플러그인이 합성 셀 위에
//! 글자를 덮어 그리고(`client_render`), 우리는 캔버스 **위**에 띠를 띄운다(블록 스티키 바
//! `proto::blocks::sticky_at` 과 같은 층 · pytmux-520 선례). 그림은 두 클라가 달라도 되지만
//! **어느 패널에 · 몇 줄 · 무슨 글 · 언제 비키나**는 같아야 한다 — 그래서 그 판정은 UI 를
//! 모르는 여기 한 자리이고, 정본 함수를 직접 부른 답지(`scripts/gen_prompt_bar.py` →
//! `tests/fixtures/prompt_bar.json`)에 대고 `tests/prompt_bar_conformance.rs` 가 잰다.
//!
//! # 왜 base 가 아니라 proto 인가
//!
//! 글자 폭(`compose::char_cells`)이 여기 있다. base 는 의존이 0 이라 폭을 못 잰다.
//!
//! # 두 바의 줄 수는 다르다
//!
//! 고정 바는 **늘 최대 2줄**([`PINNED_MAX_ROWS`])이고 긴 줄은 **접는다**. 미리보기는
//! `prompt-history-lines`(서버 `ph_max_lines`, 1~3)까지 원 줄을 **자른다**. 설정이 바꾸는
//! 것은 미리보기뿐이다(정본이 권위 — 이슈 본문의 「설정이 줄 수를 바꾼다」는 미리보기 한정).

use crate::compose::char_cells;

/// 고정 바의 최대 행수(정본 `PINNED_MAX_ROWS`). 바가 앱 글을 가리는 오버레이라 더 안 키운다.
pub const PINNED_MAX_ROWS: usize = 2;

/// 줄 첫 글자가 이것이면 「제출된 프롬프트 줄」(또는 claude 자신의 스티키 바)이다(정본 `_MARKS`).
const MARKS: [char; 3] = ['>', '❯', '›'];

/// 대화를 비우는 claude 명령 — 가장 최근 제출이 이것이면 고정 바가 안 선다(정본 `_CLEAR_CMDS`).
const CLEAR_CMDS: [&str; 3] = ["/clear", "/reset", "/new"];

/// 코어의 **패널 대상 명령**(정본 `clientutil.PANE_SCOPED_CMDS`). `:` 첫 낱말이 이것 ∪ 플러그인
/// `pane_scoped`(서버 `plugin_surface.pane_scoped`)이면 대상 패널이 서고 미리보기가 그 위에 선다.
/// 답지가 같은 표를 들고 와 대조한다.
pub const CORE_PANE_SCOPED: &[&str] = &[
    "break-pane",
    "capture-pane",
    "clear-history",
    "join-pane",
    "kill-pane",
    "paste-buffer",
    "paste-clipboard",
    "pipe-pane",
    "rename-pane",
    "resize-pane",
    "respawn-pane",
    "select-pane",
    "send-escape",
    "send-keys",
    "split-window",
    "swap-pane",
];

/// `:` 에 친 줄이 **패널 대상 명령**을 쓰고 있나(정본 `PromptScreen.on_input_changed` —
/// 첫 낱말 소문자가 코어 표 ∪ 플러그인 표).
pub fn is_pane_scoped(typed: &str, plugin: &[String]) -> bool {
    let first = typed.split_whitespace().next().unwrap_or("").to_lowercase();
    if first.is_empty() {
        return false;
    }
    CORE_PANE_SCOPED.contains(&first.as_str()) || plugin.iter().any(|p| *p == first)
}

/// 제출 글이 대화를 비우는 명령인가(인자·앞뒤 공백 무시 · 정본 `_is_clear`).
pub fn is_clear(prompt: &str) -> bool {
    prompt
        .split_whitespace()
        .next()
        .is_some_and(|w| CLEAR_CMDS.contains(&w.to_lowercase().as_str()))
}

/// 그릴 글 — 제어문자(C0 · DEL · C1)는 공백으로 접는다(정본 `_clean`).
fn clean(text: &str) -> String {
    text.chars()
        .map(|c| {
            let o = c as u32;
            if o < 0x20 || (0x7f..=0x9f).contains(&o) { ' ' } else { c }
        })
        .collect()
}

/// 프롬프트 표식으로 시작하는 줄이면 표식 뒤 글(앞뒤 공백 걷음), 아니면 `None`(정본 `_mark_body`).
fn mark_body(text: &str) -> Option<String> {
    let s = text.trim_start();
    let first = s.chars().next()?;
    if !MARKS.contains(&first) {
        return None;
    }
    Some(s[first.len_utf8()..].trim().to_owned())
}

/// 프롬프트를 폭 `budget` 칸에 **접어** 최대 `max_rows` 행으로(정본 `pinned_rows`).
///
/// 첫 행은 `❯ `, 뒤 행은 두 칸 들여 쓴다. 빈 줄은 건너뛴다. 넘치면 마지막 행 끝을 `…` 로.
pub fn pinned_rows(prompt: &str, budget: usize, max_rows: usize) -> Vec<String> {
    let lines: Vec<String> = prompt
        .split('\n')
        .map(|ln| clean(ln).trim().to_owned())
        .filter(|ln| !ln.is_empty())
        .collect();
    if lines.is_empty() || budget < 4 || max_rows == 0 {
        return Vec::new();
    }
    let mut rows: Vec<(String, usize)> = Vec::new();
    let mut truncated = false;
    for (li, ln) in lines.iter().enumerate() {
        let mut cur = if rows.is_empty() { "❯ ".to_owned() } else { "  ".to_owned() };
        let mut used = 2usize;
        for ch in ln.chars() {
            let w = char_cells(ch);
            if used + w > budget {
                rows.push((std::mem::replace(&mut cur, "  ".to_owned()), used));
                used = 2;
                if rows.len() > max_rows {
                    break;
                }
            }
            cur.push(ch);
            used += w;
        }
        rows.push((cur, used));
        if rows.len() >= max_rows {
            truncated = rows.len() > max_rows || li < lines.len() - 1;
            break;
        }
    }
    rows.truncate(max_rows);
    if truncated && let Some((text, used)) = rows.last_mut() {
        while !text.is_empty() && *used + 1 > budget {
            let last = text.pop().expect("비지 않았다");
            *used -= char_cells(last);
        }
        let trimmed = text.trim_end().to_owned();
        *text = trimmed + "…";
        *used += 1;
    }
    rows.into_iter().map(|(text, _)| text).collect()
}

/// 고정 바가 그 패널에 서나 — 서면 그 줄들(정본 `draw_pinned` 의 패널 하나).
///
/// - `last` — 그 패널 이력의 마지막 제출(없으면 바가 없다).
/// - `rows` — 패널 칸 범위(`x0..x1`)의 **지금 보이는 행 글**, 위에서부터(와이드 글자의 꼬리 칸은 뺀다).
/// - `scrolled` — pytmux 스크롤 중이면 블록 스티키 바가 그 자리를 쓴다(②).
/// - `is_target` — `:` 미리보기가 이 패널에 떠 있다(①).
///
/// 비키는 조건 다섯은 정본 머리말 그대로다: ① 미리보기 ② 스크롤 ③ 첫 줄이 이미 프롬프트
/// 표식 ④ 그 프롬프트가 패널 안에 보임 ⑤ 마지막 제출이 `/clear` 류.
pub fn pinned_for(
    last: Option<&str>,
    rows: &[String],
    pw: usize,
    ph: usize,
    scrolled: bool,
    is_target: bool,
) -> Option<Vec<String>> {
    let prompt = last.filter(|p| !p.is_empty())?;
    if is_target || scrolled || is_clear(prompt) {
        return None;
    }
    if pw < 8 || ph < 4 {
        return None;
    }
    let top = rows.first().map(String::as_str).unwrap_or("");
    if mark_body(top).is_some() {
        return None; // ③
    }
    let first = prompt
        .split('\n')
        .filter(|ln| !ln.trim().is_empty())
        .map(|ln| clean(ln).trim().to_owned())
        .next()
        .unwrap_or_default();
    if visible(&first, rows) {
        return None; // ④
    }
    let out = pinned_rows(prompt, pw.saturating_sub(2), PINNED_MAX_ROWS.min(ph - 2));
    (!out.is_empty()).then_some(out)
}

/// 프롬프트 첫 줄이 패널 안 어느 표식 줄에 보이나(④ · 정본 `_visible`) — 앞 min(둘, 30)자를 맞댄다.
fn visible(first: &str, rows: &[String]) -> bool {
    if first.is_empty() {
        return false;
    }
    let first: Vec<char> = first.chars().collect();
    rows.iter().any(|row| {
        let Some(body) = mark_body(row) else {
            return false;
        };
        let body: Vec<char> = body.chars().collect();
        if body.is_empty() {
            return false;
        }
        let k = body.len().min(first.len()).min(30);
        body[..k] == first[..k]
    })
}

/// `:` 작성 중 미리보기의 줄들(정본 `draw_preview` 가 바 안쪽 `pw-2` 칸에 남기는 글 그대로 —
/// 끝 공백은 걷는다). 잘렸으면 마지막 줄 끝 칸이 `▾` 다. 안 서면 `None`.
pub fn preview_rows(last: Option<&str>, max_lines: usize, pw: usize, ph: usize) -> Option<Vec<String>> {
    let prompt = last.filter(|p| !p.is_empty())?;
    if pw < 6 || ph < 1 {
        return None;
    }
    let max_lines = max_lines.min(ph).max(1);
    let plines: Vec<&str> = prompt.split('\n').collect();
    let nshow = max_lines.min(plines.len()).min(ph);
    let truncated = plines.len() > nshow;
    let budget = pw.saturating_sub(2);
    let mut out = Vec::with_capacity(nshow);
    for (row, text) in plines.iter().take(nshow).enumerate() {
        // 칸 단위로 채운다 — `None` 은 와이드 글자의 꼬리 칸(정본의 `""` stub).
        let mut cells: Vec<Option<char>> = vec![Some(' '); budget];
        let prefix = if row == 0 { "▷ " } else { "  " };
        let mut drawn: String = format!("{prefix}{text}");
        if truncated && row == nshow - 1 {
            drawn.push_str("  ");
        }
        let mut used = 0usize;
        for ch in drawn.chars() {
            let w = char_cells(ch);
            if used + w > budget {
                break;
            }
            cells[used] = Some(ch);
            if w == 2 && used + 1 < budget {
                cells[used + 1] = None;
            }
            used += w;
        }
        if truncated && row == nshow - 1 && budget > 0 {
            // 정본은 `px + pw - 2` 칸 = 바 안쪽의 마지막 칸에 `▾` 를 덮는다.
            cells[budget - 1] = Some('▾');
        }
        let line: String = cells.into_iter().flatten().collect();
        out.push(line.trim_end().to_owned());
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_long_prompt_folds_into_two_rows_with_an_ellipsis() {
        let rows = pinned_rows("abcdefghijklmnopqrstuvwxyz", 10, 2);
        assert_eq!(rows.len(), 2);
        assert!(rows[0].starts_with("❯ "));
        assert!(rows[1].ends_with('…'), "{rows:?}");
    }

    #[test]
    fn the_bar_yields_when_the_prompt_is_already_on_screen() {
        let rows = vec!["hello".to_owned(), "❯ fix the bug".to_owned()];
        assert_eq!(pinned_for(Some("fix the bug"), &rows, 30, 6, false, false), None);
        assert!(pinned_for(Some("other"), &rows, 30, 6, false, false).is_some());
    }

    #[test]
    fn pane_scoped_reads_the_first_word_lowercased() {
        assert!(is_pane_scoped("Rename-Pane x", &[]));
        assert!(is_pane_scoped("ph", &["ph".to_owned()]));
        assert!(!is_pane_scoped("ph", &[]));
        assert!(!is_pane_scoped("", &[]));
    }
}
