//! 팔레트 한 줄을 **칸으로 가르는 규칙**(§10-21ⓞ·ⓗ⑶).
//!
//! # 왜 뷰가 아니라 여기인가
//!
//! 제보는 *"명령어 · 옵션 · 설명을 색으로 구분하고 칼럼을 맞춘다"* 인데, 그러려면 먼저
//! 한 줄을 **조각으로 쪼개야** 한다. 그 쪼개는 규칙의 주인이 뷰면 두 클라가 각자 자른다 —
//! 정본과 GUI 의 팔레트가 같은 이름을 다르게 보이게 되고, 그것은 계층 게이트가 막으려는
//! 바로 그 갈림이다. 핸드오프도 그렇게 적어 뒀다: *"그 규칙의 주인은 뷰가 아니라 core
//! 여야 한다."*
//!
//! # 왜 `base` 가 아니라 `proto` 인가
//!
//! 폭 계산(`unicode_width`)이 필요한데 그 도구가 여기 있다(`footer::elide` 와 같은 집).
//! `base` 는 상태·키맵·명령의 집이고 글자 폭을 모른다.

/// 팔레트 이름을 **명령어와 옵션**으로 가른다 — 첫 공백에서 자른다.
///
/// 정본의 표가 `split-window -h` · `resize-pane -Z` · `select-pane -t next` 처럼 옵션을
/// 이름 안에 품고 있어서다(`base::PALETTE`). 옵션이 없으면 두 번째는 빈 문자열이다.
///
/// ⚠ 여러 칸으로 갈라진 옵션(`-t next`)은 **한 덩이로 둔다**. 더 쪼개면 "무엇이 값인가"를
/// 이 자리가 알아야 하는데, 그것은 명령마다 다르다(그 지식은 서버의 표에 있다).
pub fn split_name(name: &str) -> (&str, &str) {
    // 자르는 규칙의 주인은 **core 한 벌**이다(pytmux-7) — 팔레트가 거를 때·훅이 돌릴
    // 때·뷰가 색을 칠할 때가 전부 같은 자리에서 잘려야 한다.
    base::screens::split_first_space(name)
}

/// 글을 `cols` 칸 폭으로 접는다. 넘치지 않으면 한 줄 그대로.
///
/// # 왜 자르지 않고 접나 (ⓗ⑶)
///
/// 제보가 *"설명이 길면 **줄바꿈**해서 보이고 판 크기는 그대로"* 라고 못박았다. 자르면
/// (`footer::elide`) 판은 지켜지지만 글이 사라진다 — 그 둘은 다른 요구다.
///
/// 낱말 경계에서 접되, 한 낱말이 폭보다 길면 그 낱말 안에서 끊는다(안 그러면 그 줄이
/// 칸을 넘겨 판을 밀고 나간다 — 접기의 목적이 무너진다).
///
/// 폭은 **칸**이다(픽셀이 아니다). 한글은 두 칸이라 글자 수로 세면 절반에서 넘친다.
pub fn wrap(text: &str, cols: usize) -> Vec<String> {
    use unicode_width::UnicodeWidthChar;

    if cols == 0 {
        return Vec::new();
    }
    let width = |s: &str| -> usize { s.chars().map(|c| c.width().unwrap_or(0)).sum() };
    if width(text) <= cols {
        return vec![text.to_owned()];
    }

    let mut out: Vec<String> = Vec::new();
    let mut line = String::new();
    let mut used = 0usize;
    let mut flush = |line: &mut String, used: &mut usize, out: &mut Vec<String>| {
        if !line.is_empty() {
            out.push(std::mem::take(line));
            *used = 0;
        }
    };
    for word in text.split(' ') {
        let w = width(word);
        if w > cols {
            // 낱말 하나가 폭보다 길다 — 글자 단위로 끊는다.
            flush(&mut line, &mut used, &mut out);
            for c in word.chars() {
                let cw = c.width().unwrap_or(0);
                if used + cw > cols {
                    flush(&mut line, &mut used, &mut out);
                }
                line.push(c);
                used += cw;
            }
            continue;
        }
        // 이어 붙이면 넘치나(앞 낱말이 있으면 사이 한 칸도 센다).
        let extra = if line.is_empty() { w } else { w + 1 };
        if used + extra > cols {
            flush(&mut line, &mut used, &mut out);
        }
        if !line.is_empty() {
            line.push(' ');
            used += 1;
        }
        line.push_str(word);
        used += w;
    }
    flush(&mut line, &mut used, &mut out);
    out
}

#[cfg(test)]
#[path = "palette_tests.rs"]
mod tests;
