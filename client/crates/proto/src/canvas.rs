//! 창 전체를 하나의 셀 격자로 합성한다.
//!
//! # 왜 격자인가
//!
//! 서버는 패널마다 **자기 좌표계의** 화면을 보낸다(`screen` 은 그 패널 내용만, `layout`
//! 이 `x`/`y`/`w`/`h` 를 알려 준다). 화면에 앉히려면 누군가 그 조각들을 한 판에 옮겨
//! 놓아야 한다. 파이썬 클라도 같은 일을 한다(`client.py::_composite`).
//!
//! 격자로 한 번 만들어 두면 두 가지가 공짜로 풀린다:
//!
//! - **분할 배치** — 패널을 좌표대로 blit 하면 좌우/상하 분할이 그대로 나온다.
//! - **셀별 스타일** — 셀마다 스타일을 들고 있으므로, 줄을 그릴 때 연속된 같은 스타일을
//!   런으로 묶어 넘기면 된다.
//!
//! # 넓은 글자
//!
//! 넓은 글자(한글 등)는 두 칸을 먹고 다음 칸은 **연속 셀**이 된다. 연속 셀은 줄을 만들 때
//! 건너뛴다 — 이 규칙은 `compose` 모듈과 같고, 어긋나면 줄 폭이 틀어진다.

use crate::compose::char_cells;
use crate::message::Row;
use crate::style::CellStyle;

/// 경계 문자 ↔ 변 비트(U=8, D=4, L=2, R=1).
///
/// 파이썬 클라의 `_BOX_BITS` 와 **같은 표**다(`clientutil.py`). 두 클라가 같은 배치에서
/// 다른 모양을 그리면 안 되므로 값을 그대로 옮겼다.
/// (적합성 테스트가 파이썬 표와 대조할 수 있게 공개한다 —
/// `tests/frame_conformance.rs`·`scripts/gen_box_fixture.py`.)
pub const BOX_BITS: &[(char, u8)] = &[
    ('─', 0b0011),
    ('│', 0b1100),
    ('┌', 0b0101),
    ('┐', 0b0110),
    ('└', 0b1001),
    ('┘', 0b1010),
    ('├', 0b1101),
    ('┤', 0b1110),
    ('┬', 0b0111),
    ('┴', 0b1011),
    ('┼', 0b1111),
];

/// 그 글자가 경계 문자면 뻗는 변의 비트(U=8, D=4, L=2, R=1), 아니면 `None`.
///
/// **공개하는 이유**: GUI 는 이 칸들을 글자로 그리지 않고 **실제 선**으로 그린다
/// (네이티브 앱이라 선문자를 흉내 낼 이유가 없다). 그때 "이 칸이 경계인가, 어느 쪽으로
/// 뻗는가"를 묻는 자리가 여기다 — 뷰가 문자 목록을 따로 들면 표가 둘이 된다.
pub fn box_bits(ch: char) -> Option<u8> {
    BOX_BITS.iter().find(|(c, _)| *c == ch).map(|(_, b)| *b)
}

fn box_char(bits: u8) -> Option<char> {
    BOX_BITS.iter().find(|(_, b)| *b == bits).map(|(c, _)| *c)
}

/// 격자의 한 칸.
#[derive(Debug, Clone, PartialEq)]
pub struct Cell {
    pub ch: char,
    pub style: CellStyle,
    /// 넓은 글자가 차지한 뒤쪽 칸인가. 줄을 만들 때 건너뛴다.
    pub continuation: bool,
}

impl Default for Cell {
    fn default() -> Self {
        Self {
            ch: ' ',
            style: CellStyle::default(),
            continuation: false,
        }
    }
}

/// 창 전체 크기의 셀 격자.
#[derive(Debug, Clone)]
pub struct Canvas {
    cells: Vec<Vec<Cell>>,
    cols: usize,
    rows: usize,
}

impl Canvas {
    pub fn new(cols: usize, rows: usize) -> Self {
        Self {
            cells: vec![vec![Cell::default(); cols]; rows],
            cols,
            rows,
        }
    }

    pub fn size(&self) -> (usize, usize) {
        (self.cols, self.rows)
    }

    /// 칸 하나를 들여다본다. 격자 밖이면 `None`.
    pub fn cell(&self, x: usize, y: usize) -> Option<&Cell> {
        self.cells.get(y)?.get(x)
    }

    /// 칸 하나를 고친다. 격자 밖이면 `None`.
    ///
    /// 합성이 **끝난 뒤에** 덧칠하는 용도다(선택 강조가 그렇다) — 합성 중에 끼워 넣으면
    /// 나중에 blit 되는 패널이 덮어쓴다.
    pub fn cell_mut(&mut self, x: usize, y: usize) -> Option<&mut Cell> {
        self.cells.get_mut(y)?.get_mut(x)
    }

    /// 오버레이가 글자 하나를 써넣는다(파이썬 `clientrender.put_cell` 과 같은 규칙).
    ///
    /// 좌표가 **부호 있는 정수**인 이유: 중앙 정렬 계산이 음수로 떨어질 수 있고, 그때는
    /// 0 으로 접는 것이 아니라 **안 그리는** 것이 맞다(파이썬도 그렇게 버린다). `usize`
    /// 로 받아 `saturating_sub` 하면 글자가 왼쪽 끝에 뭉친다.
    ///
    /// 넓은 글자(한글 등)의 **둘째 칸을 덮으면 왼쪽 본체를 공백으로 정리한다** — 안 하면
    /// 짝이 어긋나 그 줄 전체가 밀린다(시계가 깨지던 자리다).
    pub fn put_cell(&mut self, x: isize, y: isize, ch: char, style: CellStyle) {
        if x < 0 || y < 0 {
            return;
        }
        let (x, y) = (x as usize, y as usize);
        if self.cell(x, y).is_some_and(|c| c.continuation) && x > 0 {
            if let Some(lead) = self.cell_mut(x - 1, y) {
                lead.ch = ' ';
            }
        }
        if let Some(cell) = self.cell_mut(x, y) {
            cell.ch = ch;
            cell.style = style;
            cell.continuation = false;
        }
    }

    /// 패널의 행 런들을 `(x, y)` 위치에 `w`×`h` 크기로 옮겨 놓는다.
    ///
    /// 격자 밖으로 나가는 부분은 자른다 — 서버가 준 좌표를 믿되, 창이 줄어드는 순간의
    /// 레이스로 잠깐 어긋날 수 있으므로 범위 검사는 여기서 한다.
    pub fn blit_pane(&mut self, rows: &[Row], x: usize, y: usize, w: usize, h: usize) {
        for (dy, row) in rows.iter().take(h).enumerate() {
            let ty = y + dy;
            if ty >= self.rows {
                break;
            }
            let mut cx = 0usize;
            for run in row {
                let style = CellStyle::from_map(&run.style);
                for ch in run.text.chars() {
                    if cx >= w {
                        break;
                    }
                    let tx = x + cx;
                    if tx >= self.cols {
                        break;
                    }
                    let wide = char_cells(ch) == 2;
                    self.cells[ty][tx] = Cell {
                        ch,
                        style,
                        continuation: false,
                    };
                    if wide && cx + 1 < w && tx + 1 < self.cols {
                        self.cells[ty][tx + 1] = Cell {
                            ch: ' ',
                            style,
                            continuation: true,
                        };
                        cx += 2;
                    } else {
                        cx += 1;
                    }
                }
            }
        }
    }

    /// 칸 하나를 덮어쓴다. 격자 밖이면 아무 일도 안 한다.
    ///
    /// **경계 문자끼리 만나면 변을 합친다** — 좌우로 나뉜 두 패널이 맞닿으면 그 자리에
    /// `│` 가 두 번 그려지는 게 아니라, 위아래 이웃과 만나 `┬`·`┴`·`┼` 가 되어야 한 장의
    /// 격자처럼 보인다. 파이썬 클라의 `_composite` 도 같은 비트 합성을 한다.
    pub fn put(&mut self, x: usize, y: usize, ch: char, style: CellStyle) {
        if y >= self.rows || x >= self.cols {
            return;
        }
        let merged = match (box_bits(self.cells[y][x].ch), box_bits(ch)) {
            (Some(cur), Some(new)) => box_char(cur | new).unwrap_or(ch),
            _ => ch,
        };
        self.cells[y][x] = Cell {
            ch: merged,
            style,
            continuation: false,
        };
    }

    /// 사각형 `(x, y, w, h)` 에 테두리를 그린다.
    ///
    /// 너무 작아 안이 없는 사각형(폭·높이 2 미만)은 그리지 않는다 — 서버가 그런 값을
    /// 줄 일은 없지만, 창이 줄어드는 순간의 레이스로 잠깐 어긋날 수 있다.
    pub fn draw_box(&mut self, x: usize, y: usize, w: usize, h: usize, style: CellStyle) {
        if w < 2 || h < 2 {
            return;
        }
        let (x2, y2) = (x + w - 1, y + h - 1);
        for gx in (x + 1)..x2 {
            self.put(gx, y, '─', style);
            self.put(gx, y2, '─', style);
        }
        for gy in (y + 1)..y2 {
            self.put(x, gy, '│', style);
            self.put(x2, gy, '│', style);
        }
        // 모서리는 마지막에 — 위 변들이 먼저 자리 잡아야 합성이 제대로 된다.
        self.put(x, y, '┌', style);
        self.put(x2, y, '┐', style);
        self.put(x, y2, '└', style);
        self.put(x2, y2, '┘', style);
    }

    /// 글자열을 `(x, y)` 부터 놓는다. 경계 합성은 하지 않는다(제목은 선이 아니다).
    ///
    /// 넓은 글자는 뒤 칸을 연속 셀로 채운다 — 안 그러면 그 줄의 폭이 틀어진다.
    /// 실제로 놓은 칸 수를 돌려준다.
    pub fn put_text(&mut self, x: usize, y: usize, text: &str, style: CellStyle) -> usize {
        if y >= self.rows {
            return 0;
        }
        let mut cx = x;
        for ch in text.chars() {
            if cx >= self.cols {
                break;
            }
            let wide = char_cells(ch) == 2;
            self.cells[y][cx] = Cell {
                ch,
                style,
                continuation: false,
            };
            cx += 1;
            if wide && cx < self.cols {
                self.cells[y][cx] = Cell {
                    ch: ' ',
                    style,
                    continuation: true,
                };
                cx += 1;
            }
        }
        cx - x
    }

    /// 한 줄을 **연속된 같은 스타일끼리 묶은 런**으로 돌려준다.
    ///
    /// 런으로 묶는 이유는 렌더러에 넘길 조각 수를 줄이기 위해서다. 셀 하나씩 넘기면
    /// 80×24 화면에 1,920개 조각이 생긴다.
    pub fn row_runs(&self, y: usize) -> Vec<(String, CellStyle)> {
        let Some(row) = self.cells.get(y) else {
            return Vec::new();
        };
        let mut runs: Vec<(String, CellStyle)> = Vec::new();
        for cell in row {
            if cell.continuation {
                continue;
            }
            match runs.last_mut() {
                Some((text, style)) if *style == cell.style => text.push(cell.ch),
                _ => runs.push((cell.ch.to_string(), cell.style)),
            }
        }
        runs
    }

    /// 줄의 글자만(스타일 없이). 테스트와 진단용.
    pub fn row_text(&self, y: usize) -> String {
        self.row_runs(y).into_iter().map(|(t, _)| t).collect()
    }

    /// 전체를 텍스트로.
    pub fn text(&self) -> String {
        (0..self.rows)
            .map(|y| self.row_text(y))
            .collect::<Vec<_>>()
            .join("\n")
    }
}

#[cfg(test)]
#[path = "canvas_tests.rs"]
mod tests;
