//! 여러 줄 작성창의 **버퍼와 키** — 패리티 `e_ins` · `ComposePromptScreen`.
//!
//! # 왜 이런 화면이 필요한가
//!
//! Claude Code 같은 자식 프롬프트 입력기는 **범위 선택 편집을 지원하지 않는다**
//! (Shift+방향키로 고르고 지우는 그것). 그 위에 선택을 얹으려면 자식의 논리 버퍼와 커서
//! 인덱스를 알아야 하는데, 우리가 받는 것은 이미 렌더된 화면 행뿐이라 알 수 없다.
//!
//! 그래서 파이썬 클라가 고른 길을 그대로 간다(타당성 검토의 권고안 B): **버퍼를 우리가
//! 소유하는** 별도 작성창을 띄우고, 다 쓰면 그 텍스트를 활성 패널에 붙여넣는다. 자식의
//! 상태를 추측할 일이 없어지는 것이 요점이다.
//!
//! # 왜 core 에 있나
//!
//! 편집 규칙은 **두 뷰가 같아야 한다**. 한쪽에서만 `Ctrl+A` 가 전체 선택이 아니거나
//! 한쪽에서만 `Enter` 가 줄바꿈이면, 사용자는 자기가 무엇을 치고 있는지 알 수 없다.
//! 그리기(박스·강조)는 뷰의 몫이고, **무엇이 버퍼에 들어가고 커서가 어디인가**는 여기다.
//!
//! # 파이썬과 같은 줄바꿈 규칙
//!
//! **`Enter` 는 전송, `Shift+Enter`(또는 `Ctrl+J`)가 줄바꿈**이다 — Claude Code 프롬프트와
//! 같은 손버릇이다(파이썬 `_ComposeTextArea` 의 존재 이유가 그것 하나다: Textual 기본
//! `TextArea` 는 `Enter` 로 줄바꿈을 넣는다). `Ctrl+J` 를 함께 받는 이유는 단말이
//! `Shift+Enter` 를 LF 로 보내면 그 조합이 `Ctrl+J` 로 도착하기 때문이다.
//!
//! # `Esc` 는 취소가 아니다
//!
//! 작성 중에 `Esc` 를 누르면 **메뉴 모드**로 들어가고, 그다음 키가 결정한다: 한 번 더
//! `Esc` 면 취소, `:` 면 명령 팔레트(작성창은 스택에 남는다), 그 외 키는 모드만 빠지고
//! 편집으로 돌아온다. 파이썬과 같은 동선이다 — `Esc` 한 번이 곧 취소면, 편집 중 습관적으로
//! 누른 `Esc` 가 쓰던 글을 날린다.

use crate::keys::{Key, Mods};

/// 키 하나를 작성창이 어떻게 처리했나.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EditorKey {
    /// 편집이 일어났거나 아무 일도 안 했다. **어느 쪽이든 패널로는 안 간다** —
    /// 작성창이 떠 있는 동안 새는 키가 있으면 셸에 글자가 찍힌다.
    Consumed,
    /// 다 썼다 — 이 텍스트를 패널에 넣는다(`Enter` · `Ctrl+S`).
    Inject,
    /// 그만둔다(`Esc` `Esc`). **쓰던 내용은 초안으로 남는다** — 그 판단은 호출부가 한다.
    Cancel,
    /// 명령 팔레트를 **이 화면 위에** 띄운다(`Esc` `:`).
    OpenPalette,
}

/// 작성창의 버퍼·커서·선택.
///
/// 열은 **문자 인덱스**다(바이트도, 화면 칸도 아니다). 바이트로 세면 한글에서 커서가 글자
/// 가운데로 들어가고, 화면 칸으로 세면 편집 위치와 그리는 위치가 어긋난다 — 폭은 그리는
/// 쪽이 계산한다(`proto::compose::display_width`).
#[derive(Debug, Clone, Default)]
pub struct Editor {
    lines: Vec<Vec<char>>,
    row: usize,
    col: usize,
    /// 선택의 반대쪽 끝. `None` 이면 선택이 없다.
    anchor: Option<(usize, usize)>,
    /// `Esc` 를 한 번 눌러 들어간 메뉴 모드(모듈 문서 참조).
    esc_mode: bool,
}

impl Editor {
    /// 미리 채울 글로 시작한다. 커서는 **문서 끝**이다(파이썬과 같다) — 이어 쓰려고 여는
    /// 것이지 앞에 끼워 넣으려고 여는 것이 아니다.
    pub fn new(seed: &str) -> Self {
        let lines: Vec<Vec<char>> = seed.split('\n').map(|l| l.chars().collect()).collect();
        let row = lines.len().saturating_sub(1);
        let col = lines.get(row).map_or(0, |l| l.len());
        Self {
            lines,
            row,
            col,
            anchor: None,
            esc_mode: false,
        }
    }

    /// 버퍼 전체. 줄 사이는 `\n` 이고 **끝에 개행을 안 붙인다** — 붙이면 자식이 그 줄을
    /// 곧바로 제출한다(파이썬도 같은 이유로 안 붙인다: 보내는 것은 사용자가 직접 `Enter`).
    pub fn text(&self) -> String {
        self.lines
            .iter()
            .map(|l| l.iter().collect::<String>())
            .collect::<Vec<_>>()
            .join("\n")
    }

    /// 그리기용 줄들.
    pub fn lines(&self) -> Vec<String> {
        self.lines.iter().map(|l| l.iter().collect()).collect()
    }

    pub fn cursor(&self) -> (usize, usize) {
        (self.row, self.col)
    }

    pub fn esc_mode(&self) -> bool {
        self.esc_mode
    }

    /// 고른 범위 `(시작, 끝)` — 늘 **시작 ≤ 끝**이다. 선택이 없거나 빈 선택이면 `None`.
    ///
    /// 정렬해서 돌려주는 이유: 뒤에서 앞으로 끌면 앵커가 커서보다 뒤에 있다. 그리는 쪽과
    /// 지우는 쪽이 각자 정렬하면 한쪽만 빠뜨린다(그러면 역방향 선택만 강조가 사라진다).
    pub fn selection(&self) -> Option<((usize, usize), (usize, usize))> {
        let anchor = self.anchor?;
        let cursor = (self.row, self.col);
        if anchor == cursor {
            return None;
        }
        Some(if anchor <= cursor {
            (anchor, cursor)
        } else {
            (cursor, anchor)
        })
    }

    /// 붙여넣기 등 **키가 아닌 입력**을 커서 자리에 넣는다.
    ///
    /// 작성창이 떠 있을 때 붙여넣은 것은 패널이 아니라 여기로 와야 한다(파이썬
    /// `paste_text` 와 같은 자리) — 안 그러면 팝업을 띄운 채 붙여넣은 글이 뒤 셸에 찍힌다.
    pub fn insert_str(&mut self, text: &str) {
        if text.is_empty() {
            return;
        }
        self.delete_selection();
        // 개행이 든 붙여넣기는 **줄이 갈린다** — 한 줄로 이어 붙이면 사용자가 복사한
        // 모양이 무너진다.
        let mut parts = text.split('\n');
        let first = parts.next().unwrap_or("");
        self.insert_chars(first.chars());
        for part in parts {
            self.split_line();
            self.insert_chars(part.chars());
        }
    }

    /// 키 하나. 반환값이 [`EditorKey::Consumed`] 가 아니면 **화면이 할 일이 남는다**.
    pub fn press(&mut self, key: Key, mods: Mods) -> EditorKey {
        // ── Esc 메뉴 모드 ─────────────────────────────────────────────────
        // 어떤 키가 오든 모드에서는 빠진다(파이썬과 같다). 키는 **소비된다** — 모드를
        // 빠져나오는 그 키가 편집까지 하면 사용자는 무엇이 들어갔는지 모른다.
        if self.esc_mode {
            self.esc_mode = false;
            return match key {
                Key::Escape => EditorKey::Cancel,
                Key::Char(':') => EditorKey::OpenPalette,
                _ => EditorKey::Consumed,
            };
        }
        if key == Key::Escape {
            self.esc_mode = true;
            return EditorKey::Consumed;
        }
        // ── 보내기 ────────────────────────────────────────────────────────
        // `Ctrl+S` 는 파이썬의 대체 경로다(`BINDINGS` 의 `inject`). `Enter` 를 줄바꿈으로
        // 쓰는 손버릇이 남은 사람이 쓰던 것이라 같이 둔다.
        if key == Key::Enter && mods == Mods::NONE {
            return EditorKey::Inject;
        }
        if key == Key::Char('s') && mods == Mods::CTRL {
            return EditorKey::Inject;
        }
        // ── 줄바꿈 ────────────────────────────────────────────────────────
        if key == Key::ShiftEnter || (key == Key::Char('j') && mods == Mods::CTRL) {
            self.delete_selection();
            self.split_line();
            return EditorKey::Consumed;
        }
        if key == Key::Char('a') && mods == Mods::CTRL {
            self.select_all();
            return EditorKey::Consumed;
        }
        if mods == Mods::CTRL && matches!(key, Key::Home) {
            self.anchor = None;
            self.row = 0;
            self.col = 0;
            return EditorKey::Consumed;
        }
        if mods == Mods::CTRL && matches!(key, Key::End) {
            self.anchor = None;
            self.row = self.lines.len().saturating_sub(1);
            self.col = self.line_len(self.row);
            return EditorKey::Consumed;
        }
        // ── 지우기 ────────────────────────────────────────────────────────
        if key == Key::Backspace {
            if !self.delete_selection() {
                self.delete_before();
            }
            return EditorKey::Consumed;
        }
        if key == Key::Delete {
            if !self.delete_selection() {
                self.delete_at();
            }
            return EditorKey::Consumed;
        }
        // ── 움직이기 ──────────────────────────────────────────────────────
        //
        // Shift 가 붙은 갈래는 **앵커를 세우고** 커서만 옮긴다. 안 붙은 갈래는 앵커를
        // 지운다 — 고른 뒤 방향키를 누르면 선택이 풀리는 것이 어느 편집기든 같다.
        if let Some((moved, extend)) = motion(key) {
            if extend {
                self.anchor.get_or_insert((self.row, self.col));
            } else {
                self.anchor = None;
            }
            self.apply_motion(moved);
            return EditorKey::Consumed;
        }
        // ── 글자 ──────────────────────────────────────────────────────────
        //
        // Ctrl/Alt 조합은 글자가 아니다. 표에 없는 조합은 **조용히 삼킨다**(화면이 떠
        // 있으면 모든 키가 화면의 것이라는 규칙 1) — 패널로 새면 셸에 제어코드가 간다.
        if let Key::Char(c) = key
            && !mods.ctrl
            && !mods.alt
        {
            self.delete_selection();
            self.insert_chars(std::iter::once(c));
        }
        EditorKey::Consumed
    }
}

/// 커서를 어디로 옮기나.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Motion {
    Left,
    Right,
    Up,
    Down,
    LineStart,
    LineEnd,
}

/// 이 키가 커서 이동인가 — `(어디로, 선택을 늘리는가)`.
///
/// Shift 는 [`Key`] 쪽에 접혀 있다(이 크레이트의 [`Mods`] 에는 shift 가 없다 —
/// `BackTab`·`ShiftEscape` 와 같은 자리). 그래서 표가 두 줄씩이다.
fn motion(key: Key) -> Option<(Motion, bool)> {
    Some(match key {
        Key::Left => (Motion::Left, false),
        Key::Right => (Motion::Right, false),
        Key::Up => (Motion::Up, false),
        Key::Down => (Motion::Down, false),
        Key::Home => (Motion::LineStart, false),
        Key::End => (Motion::LineEnd, false),
        Key::ShiftLeft => (Motion::Left, true),
        Key::ShiftRight => (Motion::Right, true),
        Key::ShiftUp => (Motion::Up, true),
        Key::ShiftDown => (Motion::Down, true),
        Key::ShiftHome => (Motion::LineStart, true),
        Key::ShiftEnd => (Motion::LineEnd, true),
        _ => return None,
    })
}

impl Editor {
    fn line_len(&self, row: usize) -> usize {
        self.lines.get(row).map_or(0, |l| l.len())
    }

    fn select_all(&mut self) {
        self.anchor = Some((0, 0));
        self.row = self.lines.len().saturating_sub(1);
        self.col = self.line_len(self.row);
    }

    fn insert_chars(&mut self, chars: impl Iterator<Item = char>) {
        if self.lines.is_empty() {
            self.lines.push(Vec::new());
        }
        let row = self.row.min(self.lines.len() - 1);
        self.row = row;
        for c in chars {
            let line = &mut self.lines[row];
            let at = self.col.min(line.len());
            line.insert(at, c);
            self.col = at + 1;
        }
    }

    fn split_line(&mut self) {
        if self.lines.is_empty() {
            self.lines.push(Vec::new());
        }
        let row = self.row.min(self.lines.len() - 1);
        let at = self.col.min(self.lines[row].len());
        let tail = self.lines[row].split_off(at);
        self.lines.insert(row + 1, tail);
        self.row = row + 1;
        self.col = 0;
    }

    fn delete_before(&mut self) {
        if self.col > 0 {
            let row = self.row.min(self.lines.len().saturating_sub(1));
            self.lines[row].remove(self.col - 1);
            self.col -= 1;
            return;
        }
        // 줄 맨 앞의 백스페이스는 **윗줄과 잇는다**. 아무 일도 안 하면 여러 줄로 갈린
        // 것을 되돌릴 방법이 없다.
        if self.row == 0 {
            return;
        }
        let line = self.lines.remove(self.row);
        self.row -= 1;
        self.col = self.lines[self.row].len();
        self.lines[self.row].extend(line);
    }

    fn delete_at(&mut self) {
        let row = self.row.min(self.lines.len().saturating_sub(1));
        if self.col < self.line_len(row) {
            self.lines[row].remove(self.col);
            return;
        }
        if row + 1 >= self.lines.len() {
            return;
        }
        let next = self.lines.remove(row + 1);
        self.lines[row].extend(next);
    }

    /// 고른 범위를 지운다. 지울 것이 있었으면 `true`.
    fn delete_selection(&mut self) -> bool {
        let Some(((r0, c0), (r1, c1))) = self.selection() else {
            self.anchor = None;
            return false;
        };
        let tail: Vec<char> = self.lines[r1][c1.min(self.lines[r1].len())..].to_vec();
        let head = c0.min(self.lines[r0].len());
        self.lines[r0].truncate(head);
        self.lines[r0].extend(tail);
        // 뒤에서 앞으로 지운다 — 앞에서 지우면 남은 인덱스가 밀린다.
        for row in ((r0 + 1)..=r1).rev() {
            self.lines.remove(row);
        }
        self.row = r0;
        self.col = c0.min(self.lines[r0].len());
        self.anchor = None;
        true
    }

    fn apply_motion(&mut self, motion: Motion) {
        let last = self.lines.len().saturating_sub(1);
        match motion {
            Motion::Left => {
                if self.col > 0 {
                    self.col -= 1;
                } else if self.row > 0 {
                    // 줄머리에서 왼쪽은 **윗줄 끝**이다. 안 넘어가면 여러 줄 문서에서
                    // 커서가 줄마다 갇힌다.
                    self.row -= 1;
                    self.col = self.line_len(self.row);
                }
            }
            Motion::Right => {
                if self.col < self.line_len(self.row) {
                    self.col += 1;
                } else if self.row < last {
                    self.row += 1;
                    self.col = 0;
                }
            }
            Motion::Up => {
                if self.row > 0 {
                    self.row -= 1;
                    self.col = self.col.min(self.line_len(self.row));
                }
            }
            Motion::Down => {
                if self.row < last {
                    self.row += 1;
                    self.col = self.col.min(self.line_len(self.row));
                }
            }
            Motion::LineStart => self.col = 0,
            Motion::LineEnd => self.col = self.line_len(self.row),
        }
    }
}

#[cfg(test)]
#[path = "editor_tests.rs"]
mod tests;
