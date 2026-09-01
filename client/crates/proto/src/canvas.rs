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

/// 블록 문자가 칸의 **어디를** 채우나. 칸을 `(0,0)`~`(1,1)` 로 본 사각형이다.
///
/// 값이 비율인 이유: 칸의 픽셀 크기는 글꼴과 배율이 정하므로 여기서는 알 수 없다.
/// 그리는 쪽이 `x0 * 칸너비` 식으로 옮긴다(테두리 선분과 같은 규율 — proto 는 무엇을,
/// 뷰는 몇 픽셀에).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BlockFill {
    pub x0: f32,
    pub y0: f32,
    pub x1: f32,
    pub y1: f32,
    /// 진하기 0~1. 음영 문자(`░`·`▒`·`▓`)는 **같은 사각형을 흐리게** 칠한 것이다.
    pub alpha: f32,
}

impl BlockFill {
    const fn rect(x0: f32, y0: f32, x1: f32, y1: f32) -> Self {
        Self { x0, y0, x1, y1, alpha: 1. }
    }

    const fn shade(alpha: f32) -> Self {
        Self { x0: 0., y0: 0., x1: 1., y1: 1., alpha }
    }
}

/// 블록 문자 ↔ 채우는 자리(U+2580 ~ U+2595).
///
/// # 왜 이 표가 필요한가 (§10-21ⓘ)
///
/// GUI 캔버스는 한 줄을 문자열로 셰이퍼에 넘긴다 — 자리는 **글리프의 전진폭**이 정한다.
/// 블록 문자는 우리가 고른 고정폭 글꼴에 거의 없어 폴백으로 가는데, 폴백이 고정폭이
/// 아니면 진폭이 칸너비의 정수배가 아니다. 그러면 그림이 **행마다 어긋난다** — Claude
/// 마스코트가 정확히 그 증상이었다.
///
/// 테두리를 실제 선으로 옮긴 것과 **같은 처방**이다: 글꼴에 맡기지 않고 우리가 그린다.
/// 그러면 진폭이 무엇이든 그림이 격자에 딱 맞는다.
///
/// # 사분면(U+2596~U+259F)도 여기 있다 (pytmux-177)
///
/// 종전에는 빠져 있었고 사유가 *"사각형 **둘**이라 이 표의 모양(하나)으로 못 적는다"* 였다.
/// 그때 남긴 말이 *"필요해지면 그때 `&[BlockFill]` 로 넓힐 것"* 이었고, **지금이 그때다** —
/// 표의 값을 사각형 **목록**으로 넓혔다.
///
/// ⚠ 없는 동안 그 글자들은 종전대로 폴백 글꼴로 그려졌고, 그것이 곧 pytmux-55 가 고친
/// 바로 그 결함(진폭이 칸너비의 정수배가 아니라 **행마다 어긋난다**)이다. 곧 표에서
/// 빠진 것은 「안 그려진다」가 아니라 **「예전 버그를 그대로 겪는다」**였다.
///
/// ★ **짐작이 아니라 쟀다**(2026-08-24). 도는 `claude`(v2.1.241)의 시작 화면을 pty 로 떠서
/// 그 격자를 코드포인트로 덤프하니 마스코트가 쓰는 글자는 여섯이었다 —
/// `▀`(U+2580) · `█`(U+2588) · `▐`(U+2590) · `▛`(U+259B) · `▜`(U+259C) · `▝`(U+259D).
/// 뒤의 셋이 사분면이고 **스무 칸 중 여덟 칸**이 그것이다 — 표가 좁던 동안 그만큼이
/// 폴백 글꼴로 갔다는 뜻이라 제보의 *"행마다 가로로 밀린다"* 와 맞는다.
/// braille(U+28xx)·팔분면(U+1FB00)은 **한 글자도 안 나왔다** — 그 범위까지 넓힐 까닭이
/// 지금은 없다.
///
/// 그 캡처는 `tests/fixtures/claude_mascot.json` 이 쥐고, 재는 자리는
/// `tests/mascot_conformance.rs` 다. ⛔ 그 픽스처에는 생성기가 없다(도는 claude 가
/// 있어야 나온다) — 다시 뜨는 법은 그 테스트 머리말에 적혀 있다.
pub const BLOCK_FILLS: &[(char, &[BlockFill])] = &[
    ('▀', &[BlockFill::rect(0., 0., 1., 0.5)]),
    ('▁', &[BlockFill::rect(0., 7. / 8., 1., 1.)]),
    ('▂', &[BlockFill::rect(0., 6. / 8., 1., 1.)]),
    ('▃', &[BlockFill::rect(0., 5. / 8., 1., 1.)]),
    ('▄', &[BlockFill::rect(0., 4. / 8., 1., 1.)]),
    ('▅', &[BlockFill::rect(0., 3. / 8., 1., 1.)]),
    ('▆', &[BlockFill::rect(0., 2. / 8., 1., 1.)]),
    ('▇', &[BlockFill::rect(0., 1. / 8., 1., 1.)]),
    ('█', &[BlockFill::rect(0., 0., 1., 1.)]),
    ('▉', &[BlockFill::rect(0., 0., 7. / 8., 1.)]),
    ('▊', &[BlockFill::rect(0., 0., 6. / 8., 1.)]),
    ('▋', &[BlockFill::rect(0., 0., 5. / 8., 1.)]),
    ('▌', &[BlockFill::rect(0., 0., 4. / 8., 1.)]),
    ('▍', &[BlockFill::rect(0., 0., 3. / 8., 1.)]),
    ('▎', &[BlockFill::rect(0., 0., 2. / 8., 1.)]),
    ('▏', &[BlockFill::rect(0., 0., 1. / 8., 1.)]),
    ('▐', &[BlockFill::rect(0.5, 0., 1., 1.)]),
    ('░', &[BlockFill::shade(0.25)]),
    ('▒', &[BlockFill::shade(0.5)]),
    ('▓', &[BlockFill::shade(0.75)]),
    ('▔', &[BlockFill::rect(0., 0., 1., 1. / 8.)]),
    ('▕', &[BlockFill::rect(7. / 8., 0., 1., 1.)]),
    // ── 사분면(U+2596~U+259F) — 하나는 사각형 하나, 셋 짜리는 «반쪽 + 사분면» 둘로 ──
    //
    // 붙은 사분면 둘은 **반쪽 하나로 합친다**(예: `▙` 의 좌상+좌하 = 왼쪽 절반).
    // 사각형을 덜 그리려는 것이 아니라, 인접한 두 사각형 사이에 반올림 틈이 생기면
    // 그림에 **머리카락 같은 선**이 보이기 때문이다.
    ('▖', &[BlockFill::rect(0., 0.5, 0.5, 1.)]),
    ('▗', &[BlockFill::rect(0.5, 0.5, 1., 1.)]),
    ('▘', &[BlockFill::rect(0., 0., 0.5, 0.5)]),
    ('▝', &[BlockFill::rect(0.5, 0., 1., 0.5)]),
    // 좌상 + 좌하 + 우하 = 왼쪽 절반 + 우하
    ('▙', &[BlockFill::rect(0., 0., 0.5, 1.), BlockFill::rect(0.5, 0.5, 1., 1.)]),
    // 좌상 + 우하(대각)
    ('▚', &[BlockFill::rect(0., 0., 0.5, 0.5), BlockFill::rect(0.5, 0.5, 1., 1.)]),
    // 좌상 + 우상 + 좌하 = 위쪽 절반 + 좌하
    ('▛', &[BlockFill::rect(0., 0., 1., 0.5), BlockFill::rect(0., 0.5, 0.5, 1.)]),
    // 좌상 + 우상 + 우하 = 위쪽 절반 + 우하
    ('▜', &[BlockFill::rect(0., 0., 1., 0.5), BlockFill::rect(0.5, 0.5, 1., 1.)]),
    // 우상 + 좌하(대각)
    ('▞', &[BlockFill::rect(0.5, 0., 1., 0.5), BlockFill::rect(0., 0.5, 0.5, 1.)]),
    // 우상 + 좌하 + 우하 = 오른쪽 절반 + 좌하
    ('▟', &[BlockFill::rect(0.5, 0., 1., 1.), BlockFill::rect(0., 0.5, 0.5, 1.)]),
];

/// 그 글자가 블록 문자면 채우는 자리**들**, 아니면 `None`.
///
/// 목록인 이유는 사분면 때문이다 — `▙` 처럼 셋을 채우는 글자는 사각형 하나로 못 적는다
/// ([`BLOCK_FILLS`] 문서 · pytmux-177).
pub fn block_fills(ch: char) -> Option<&'static [BlockFill]> {
    BLOCK_FILLS.iter().find(|(c, _)| *c == ch).map(|(_, f)| *f)
}

/// 격자의 한 칸.
#[derive(Debug, Clone, PartialEq)]
pub struct Cell {
    pub ch: char,
    /// 이 글자에 **얹히는** 폭 0 글자들 — 변이 선택자(U+FE0E·U+FE0F) · ZWJ(U+200D) ·
    /// 결합 표시. 보통 비어 있다.
    ///
    /// # 왜 셀이 «글자 하나»면 안 되나 (pytmux-407)
    ///
    /// 그 글자들은 앞 글자와 **한 덩어리로 셰이퍼에 들어가야** 제 모습이 된다.
    /// `⚠`+U+FE0F 를 갈라 넣으면 `⚠` 만 홀로 셰이퍼에 가고 **흑백 텍스트 표현**으로
    /// 그려진다 — 색 이모지가 아니다.
    ///
    /// 갈래를 나눈 이유(칸을 하나 더 쓰지 않는 이유): 폭 0 은 **칸을 안 먹는다**.
    /// 종전에는 이 글자가 제 칸을 차지해 뒤따르는 글자가 한 칸씩 밀렸다.
    ///
    /// ★ **폭 0 만 드는 칸이 아니다**(pytmux-407 ⓐ · 2026-09-01). 군집의 다음 조각
    /// (둘째 이모지 · 둘째 지역 지시자 · 피부톤 수정자)도 여기 든다 — 사람이 고른
    /// 규약이 「군집의 폭 = 밑글자의 폭」이라, `👨‍👩‍👧` 는 **한 칸(폭 2)**이고 그 글은
    /// 코드포인트 다섯이다. 그 조각들은 셰이퍼가 **한 글리프로** 합치므로 화면에서
    /// 사라지지 않는다(갈라 넣으면 이모지 셋이 된다 — 그것이 이 이슈의 증상이었다).
    ///
    /// ⛔ 무엇이 얹히는지는 [`crate::compose::attaches`] 한 곳이 정한다. 여기서 따로
    /// 판정하면 서버 격자와 갈리고, 갈리면 그 줄이 통째로 밀린다.
    pub marks: String,
    pub style: CellStyle,
    /// 넓은 글자가 차지한 뒤쪽 칸인가. 줄을 만들 때 건너뛴다.
    pub continuation: bool,
}

impl Cell {
    /// 이 칸이 그리는 **글**(글자 + 얹힌 것들). 보통은 글자 하나다.
    ///
    /// 그리는 쪽은 이것을 통째로 셰이퍼에 넘긴다 — 갈라 넘기면 위 머리말의 그 증상이다.
    pub fn text(&self) -> String {
        if self.marks.is_empty() {
            return self.ch.to_string();
        }
        let mut out = String::with_capacity(1 + self.marks.len());
        out.push(self.ch);
        out.push_str(&self.marks);
        out
    }
}

impl Default for Cell {
    fn default() -> Self {
        Self {
            ch: ' ',
            marks: String::new(),
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
            // 새 글자로 갈았으니 앞 글자에 얹혀 있던 것은 남으면 안 된다 — 남기면
            // 엉뚱한 글자에 변이 선택자가 붙는다.
            cell.marks.clear();
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
                    // ★ **얹히는 글자는 칸을 안 먹는다**(pytmux-407 · 389).
                    //   두 갈래다 — 폭 0(변이 선택자·ZWJ·결합 표시)과 **군집의 다음
                    //   조각**(둘째 이모지·둘째 지역 지시자·피부톤 수정자). 판정은
                    //   `compose::attaches` 한 벌이고 서버 격자가 같은 규칙으로 셀을
                    //   짓는다 — 여기서 다르게 세면 그 줄이 통째로 어긋난다.
                    //   ⛔ 앞 칸이 없으면(줄 첫 글자) 폭 0 은 버린다 — 얹을 자리가 없는
                    //      표시를 제 칸에 그리면 그것이 화면에 없는 글자가 된다.
                    if cx > 0 {
                        let prev = x + cx - 1;
                        // 넓은 글자의 **본체**에 얹는다(연속 칸이 아니라).
                        let at = if self.cells[ty].get(prev).is_some_and(|c| c.continuation)
                            && prev > 0
                        {
                            prev - 1
                        } else {
                            prev
                        };
                        if self.cells[ty]
                            .get(at)
                            .is_some_and(|c| crate::compose::attaches(&c.text(), ch))
                        {
                            self.cells[ty][at].marks.push(ch);
                            continue;
                        }
                    }
                    if crate::compose::char_advance(ch) == 0 {
                        continue;
                    }
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
                        marks: String::new(),
                        style,
                        continuation: false,
                    };
                    if wide && cx + 1 < w && tx + 1 < self.cols {
                        self.cells[ty][tx + 1] = Cell {
                            ch: ' ',
                            marks: String::new(),
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
    /// **넓은 글자는 뒤 칸을 연속 셀로 채운다** — [`put_text`](Self::put_text) 와 같은
    /// 규칙이다. 종전에는 이 함수만 그걸 안 해서 `[한]` 입력기 배지가 `[한 ]` 로 갈렸다
    /// (pytmux-17, 2026-08-03 실측). 플러그인 런은 글자마다 이 함수를 부르고 부르는 쪽이
    /// `x += display_width` 로 건너뛰는데, **건너뛴 칸은 손대지 않은 채 남는다.** 그 칸은
    /// 배지 스타일이 아니므로 [`row_runs`](Self::row_runs) 가 **별도 런**으로 뱉고,
    /// 화면에서는 배지 바탕이 끊긴 빈 칸 하나가 된다. 화소로 재면 이렇다:
    ///
    /// ```text
    /// 초록 1847..1878 = 2.98칸 ([한)   빈틈 11px = 1.03칸   초록 1890..1900 = 1칸 (])
    /// ```
    ///
    /// ⚠ 반대 방향도 막는다: 이미 놓인 넓은 글자의 **뒤 칸**을 좁은 글자로 덮으면 앞칸이
    /// 반쪽짜리로 남는다. `put_cell` 이 하는 것과 같이 앞칸을 공백으로 되돌린다.
    pub fn put(&mut self, x: usize, y: usize, ch: char, style: CellStyle) {
        if y >= self.rows || x >= self.cols {
            return;
        }
        // 남의 넓은 글자의 뒤 칸을 밟는 경우 — 그 글자의 앞칸을 비운다(반쪽 글자 방지).
        if self.cells[y][x].continuation && x > 0 {
            self.cells[y][x - 1].ch = ' ';
        }
        let merged = match (box_bits(self.cells[y][x].ch), box_bits(ch)) {
            (Some(cur), Some(new)) => box_char(cur | new).unwrap_or(ch),
            _ => ch,
        };
        self.cells[y][x] = Cell {
            ch: merged,
            marks: String::new(),
            style,
            continuation: false,
        };
        if char_cells(ch) == 2 && x + 1 < self.cols {
            self.cells[y][x + 1] = Cell {
                ch: ' ',
                marks: String::new(),
                style,
                continuation: true,
            };
        }
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
            // 여기도 얹히는 글자는 앞 칸에 붙는다(pytmux-407 · 위 `blit_pane` 과 같은 규칙).
            if cx > 0 {
                // 넓은 글자의 **본체**를 찾는다 — 연속 칸에 얹으면 그리는 쪽이 그 칸을
                // 건너뛰어 사라진다(저쪽과 같은 되짚기다).
                let prev = cx - 1;
                let at = if self.cells[y].get(prev).is_some_and(|c| c.continuation) && prev > 0 {
                    prev - 1
                } else {
                    prev
                };
                if self.cells[y]
                    .get(at)
                    .is_some_and(|c| crate::compose::attaches(&c.text(), ch))
                {
                    self.cells[y][at].marks.push(ch);
                    continue;
                }
            }
            if crate::compose::char_advance(ch) == 0 {
                continue;
            }
            let wide = char_cells(ch) == 2;
            self.cells[y][cx] = Cell {
                ch,
                marks: String::new(),
                style,
                continuation: false,
            };
            cx += 1;
            if wide && cx < self.cols {
                self.cells[y][cx] = Cell {
                    ch: ' ',
                    marks: String::new(),
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
            // 얹힌 것(변이 선택자 등)도 **함께** 나간다 — 빼면 이 줄을 다시 받는 쪽이
            // 색 이모지를 잃는다(pytmux-407).
            match runs.last_mut() {
                Some((text, style)) if *style == cell.style => {
                    text.push(cell.ch);
                    text.push_str(&cell.marks);
                }
                _ => runs.push((cell.text(), cell.style)),
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
