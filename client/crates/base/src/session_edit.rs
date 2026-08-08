//! 상태줄 세션 이름(`#S`)의 **제자리 편집** — 버퍼와 키(pytmux-3 제보).
//!
//! # 제보가 요구한 것
//!
//! *"세션 이름을 클릭하면 그 자리에서 바로 글자를 고쳐 리네임할 수 있어야 한다"* —
//! **인라인 편집**이지 이름을 묻는 판을 띄우라는 것이 아니다. 그래서 이 편집은
//! [`crate::screens::Screens`] 스택을 안 지나고, 그 결과 **키를 가로챌 판이 없다** —
//! 편집 중 키를 여기로 돌리는 분기가 없으면 글자가 그대로 패널(셸)로 샌다.
//!
//! # 왜 [`crate::editor::Editor`] 를 안 쓰나
//!
//! 그 편집기는 **여러 줄 작성창**의 것이고 키 뜻이 다르다: `Enter` 는 줄바꿈이 아니라
//! 전송이지만 `Esc` 는 **취소가 아니라 메뉴 모드**이고(그 문서의 「`Esc` 는 취소가
//! 아니다」 절), 선택·여러 줄·초안 개념을 함께 지고 있다. 이름 한 줄에는 그중 어느
//! 것도 안 쓰이고, 정본(파이썬 `_handle_session_edit_key`)의 표는 **`Esc` = 즉시
//! 취소**다. 뜻이 다른 표를 재사용하면 같은 자리가 두 클라에서 다르게 움직인다.
//!
//! # 왜 core 에 있나
//!
//! 뷰에 두면 "무엇이 버퍼에 들어가고 커서가 어디인가"를 뷰가 각자 정하게 된다 —
//! 이 크레이트가 존재하는 이유 그대로다. 그리는 것(강조·커서 칸)은 뷰의 몫이다.

use crate::keys::{Key, Mods};

/// 키 하나를 편집칸이 어떻게 처리했나.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionEditKey {
    /// 먹었다(고쳤거나 아무 일도 안 했거나). **어느 쪽이든 패널로는 안 간다** —
    /// 편집 중에 새는 키가 있으면 셸에 글자가 찍힌다.
    Consumed,
    /// 다 썼다 — 이 이름으로 바꾼다(`Enter`). 앞뒤 공백은 이미 털었다.
    Commit(String),
    /// 그만둔다(`Esc`). 이름은 그대로 둔다.
    Cancel,
}

/// 편집 중인 이름과 커서.
///
/// 커서는 **문자 인덱스**다(바이트도 화면 칸도 아니다) — 바이트로 세면 한글에서 커서가
/// 글자 가운데로 들어간다([`crate::editor::Editor`] 와 같은 규칙).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionEdit {
    buf: Vec<char>,
    cur: usize,
}

impl SessionEdit {
    /// 지금 이름을 싣고 시작한다. 커서는 **끝**이다(파이썬과 같다) — 이어 쓰려고 여는
    /// 것이지 앞에 끼워 넣으려고 여는 것이 아니다.
    pub fn new(name: &str) -> Self {
        let buf: Vec<char> = name.chars().collect();
        let cur = buf.len();
        Self { buf, cur }
    }

    pub fn text(&self) -> String {
        self.buf.iter().collect()
    }

    /// 커서 앞의 글자 수 — 뷰가 어디를 반전시킬지 정하는 값이다.
    pub fn cursor(&self) -> usize {
        self.cur
    }

    pub fn len(&self) -> usize {
        self.buf.len()
    }

    pub fn is_empty(&self) -> bool {
        self.buf.is_empty()
    }

    /// 커서를 `i` 번째 글자 앞으로(편집 중 그 자리를 다시 눌렀을 때).
    pub fn set_cursor(&mut self, i: usize) {
        self.cur = i.min(self.buf.len());
    }

    /// 확정된 글자를 끼워 넣는다.
    ///
    /// ⚠ **이 경로가 한글의 유일한 입구다.** 입력기는 조합이 끝난 글자를 키가 아니라
    /// 확정 문자열로 준다(정본에서는 Textual 의 Paste, GUI 에서는
    /// `on_typed_characters`) — 그래서 [`press`](Self::press) 만 배선하면 편집 중
    /// 한글이 통째로 샌다.
    ///
    /// 제어문자(탭·개행)는 이름에 안 넣는다 — 파이썬 `isprintable` 과 같은 거름이다.
    pub fn insert_str(&mut self, text: &str) {
        for ch in text.chars().filter(|c| !c.is_control()) {
            self.buf.insert(self.cur, ch);
            self.cur += 1;
        }
    }

    /// 키 하나. 표에 없는 키는 **삼킨다**(패널 유출 금지).
    ///
    /// 표는 파이썬 `_handle_session_edit_key` 그대로다 — 글자/Backspace/Delete/←→/
    /// Home·End 로 고치고 `Enter` 커밋 · `Esc` 취소.
    ///
    /// ⚠ `Ctrl`/`Alt` 조합은 **글자로 안 받는다**(`Ctrl+C` 가 이름에 `c` 를 넣으면 안
    /// 된다). 그렇다고 흘려보내지도 않는다 — 편집 중 패널로 새는 키를 안 만드는 것이
    /// 이 함수의 계약이다.
    pub fn press(&mut self, key: Key, mods: Mods) -> SessionEditKey {
        match key {
            Key::Escape | Key::ShiftEscape => return SessionEditKey::Cancel,
            Key::Enter => return SessionEditKey::Commit(self.text().trim().to_owned()),
            Key::Backspace => {
                if self.cur > 0 {
                    self.cur -= 1;
                    self.buf.remove(self.cur);
                }
            }
            Key::Delete => {
                if self.cur < self.buf.len() {
                    self.buf.remove(self.cur);
                }
            }
            Key::Left => self.cur = self.cur.saturating_sub(1),
            Key::Right => self.cur = (self.cur + 1).min(self.buf.len()),
            Key::Home => self.cur = 0,
            Key::End => self.cur = self.buf.len(),
            Key::Char(c) if mods == Mods::NONE && !c.is_control() => {
                self.buf.insert(self.cur, c);
                self.cur += 1;
            }
            _ => {}
        }
        SessionEditKey::Consumed
    }
}

#[cfg(test)]
#[path = "session_edit_tests.rs"]
mod tests;
