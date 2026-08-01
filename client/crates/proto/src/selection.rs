//! 마우스 드래그 선택 — **절대 행 좌표로** 붙잡는다.
//!
//! # 왜 화면 좌표가 아닌가
//!
//! 선택하는 동안에도 패널은 흐른다. 명령을 실행 중인 패널은 출력이 계속 올라오고,
//! 사용자가 버튼을 누른 채 휠을 굴리면 뷰포트가 통째로 움직인다. 선택을 화면 행으로
//! 들고 있으면 그때마다 **선택이 다른 텍스트로 갈아탄다** — 사용자 눈에는 잡아 둔 글이
//! 손에서 미끄러지는 것으로 보인다.
//!
//! 절대 행(스크롤백 맨 위부터 세는 인덱스)으로 들고 있으면 뷰포트가 움직여도 같은 글을
//! 가리킨다. 그 기준점은 서버가 `screen` 마다 실어 보내는
//! [`Screen::top`](crate::message::Screen::top) 이고, 서버의 추출
//! (`model.Pane.extract_range`)도 **같은 좌표계**를 쓴다. 파이썬 클라도 같은 이유로 같은
//! 좌표계를 쓴다(`clientwidgets._to_abs`).
//!
//! # 흐름 선택이지 사각형이 아니다
//!
//! 첫 줄은 시작 열부터 끝까지, 가운데 줄은 통째로, 마지막 줄은 처음부터 끝 열까지다.
//! 서버의 `extract_range` 가 그렇게 뽑으므로([`Selection::contains`] 참조) 강조도 그
//! 모양이어야 한다 — 다르면 사용자가 고른 것과 복사된 것이 어긋난다.

/// 선택의 한쪽 끝. 행은 **절대 인덱스**, 열은 **패널 안 열**이다.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Point {
    /// 스크롤백 맨 위부터 센 행 번호(`Screen::top` 과 같은 좌표계).
    pub line: usize,
    /// 패널 왼쪽 끝을 0 으로 센 열.
    pub col: u16,
}

impl Point {
    pub fn new(line: usize, col: u16) -> Self {
        Self { line, col }
    }
}

/// 드래그 한 번이 잡은 범위.
///
/// `anchor` 는 버튼을 누른 자리, `focus` 는 지금 포인터가 있는 자리다. 둘의 **순서는
/// 보장하지 않는다** — 위로 끌면 focus 가 앞선다. 순서가 필요한 곳은
/// [`ordered`](Self::ordered) 를 쓴다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Selection {
    /// 선택이 시작된 패널. 드래그가 이웃 패널로 넘어가도 **여기 묶어 둔다** — 두 패널의
    /// 텍스트를 이어 붙이면 화면에는 나란히 보여도 실제로는 남남인 줄이 섞인다.
    pub pane: i64,
    pub anchor: Point,
    pub focus: Point,
}

impl Selection {
    /// 누른 자리 하나로 시작한다. 아직 한 칸짜리다.
    pub fn new(pane: i64, at: Point) -> Self {
        Self {
            pane,
            anchor: at,
            focus: at,
        }
    }

    /// 포인터를 옮긴다. 앵커는 그대로다.
    pub fn extend_to(&mut self, at: Point) {
        self.focus = at;
    }

    /// 앞선 끝과 뒤선 끝. 서버에 보낼 `(y0,x0)..(y1,x1)` 이 이 순서다.
    pub fn ordered(&self) -> (Point, Point) {
        if self.anchor <= self.focus {
            (self.anchor, self.focus)
        } else {
            (self.focus, self.anchor)
        }
    }

    /// 아직 한 칸도 못 끌었는가.
    ///
    /// 클릭(누르고 안 움직임)과 선택을 가르는 데 쓴다 — 클릭은 포커스 이동이고 선택은
    /// 복사다. 파이썬 클라도 "이동이 있었나"로 같은 판정을 한다.
    pub fn is_collapsed(&self) -> bool {
        self.anchor == self.focus
    }

    /// 이 점이 선택 안에 드는가(**흐름 선택**).
    ///
    /// 서버 `extract_range` 의 줄별 `sx`/`ex` 계산과 같은 규칙이다: 첫 줄은 시작 열부터,
    /// 마지막 줄은 끝 열까지, 그 사이는 줄 전체. 양끝은 **포함**이다.
    pub fn contains(&self, point: Point) -> bool {
        let (a, b) = self.ordered();
        if point.line < a.line || point.line > b.line {
            return false;
        }
        let after_start = point.line > a.line || point.col >= a.col;
        let before_end = point.line < b.line || point.col <= b.col;
        after_start && before_end
    }
}

#[cfg(test)]
#[path = "selection_tests.rs"]
mod tests;
