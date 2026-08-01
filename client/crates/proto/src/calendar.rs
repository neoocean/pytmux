//! 달력 오버레이(패리티 G7c) — `calendar` 플러그인의 화면을 재현한다.
//!
//! # 시계와 다른 점
//!
//! 시계는 폰트만 맞추면 끝이지만, 달력은 **배치가 규칙**이다. 칸 폭·주 간격이 패널 크기에
//! 따라 늘어나고, 어느 크기에서 큰 달력 → 보통 격자 → 단순 날짜로 떨어지는지가 정해져
//! 있다. 그 규칙을 손으로 옮기면 **특정 창 크기에서만** 두 클라가 다르게 보이고, 사람이
//! 그 크기를 우연히 만나기 전까지 아무도 모른다.
//!
//! 그래서 픽스처가 글리프가 아니라 **그려진 화면**을 뜬다
//! (`scripts/gen_calendar_fixture.py` → `tests/calendar_conformance.rs`).
//!
//! # 세 단
//!
//! 1. **큰 달력** — 날짜를 시계 블록 폰트로. 큰 폰트가 안 들어가면 반칸 폰트로 한 번 더
//!    시도한다(들어가는 첫 단을 쓴다).
//! 2. **보통 격자** — `‹ YYYY-MM ›` 제목 + 요일 + 두 자리 날짜. 칸 폭 4~8, 주 간격 1~3.
//! 3. **단순 날짜** — 그것마저 안 들어가면 `YYYY-MM-DD` 한 줄.

use crate::canvas::Canvas;
use crate::clock;
use crate::style::CellStyle;

/// 날짜칸 사이 간격.
const DGAP: usize = 3;
/// 한 날짜의 두 자리 사이 간격.
const DIG: usize = 1;

const WEEKDAYS: [&str; 7] = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

/// 그릴 달 하나.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Month {
    pub year: i32,
    pub month: u32,
    /// 일요일 시작 주 단위 배열. 그 달에 없는 칸은 `0`.
    pub weeks: Vec<[u8; 7]>,
    /// 강조할 날. **이번 달이 아니면 0** — 넘긴 달에는 오늘이 없다.
    pub today: u8,
    /// 폴백 줄에 적을 오늘 날짜(이번 달일 때만 쓴다).
    pub today_of_month: u8,
}

/// 오버레이가 쓰는 색들. 뷰가 아니라 여기서 받는 이유는 두 뷰가 같은 그림을 그려야
/// 해서다(색만 다르면 같은 기능이 아니게 된다).
#[derive(Debug, Clone, Copy)]
pub struct Styles {
    pub day: CellStyle,
    pub title: CellStyle,
    /// 보통 격자에서 오늘 — 정본은 강조색을 **배경**으로 깔고 글자를 검정으로 둔다.
    pub today: CellStyle,
    /// **큰 달력**에서 오늘 — 정본 `big_today`. 블록 폰트라 칸이 크고, 거기에 배경을
    /// 깔면 화면의 절반이 색 덩어리가 된다. 그래서 저쪽은 이 단만 **글자색**으로 준다.
    pub big_today: CellStyle,
}

/// `today` 기준 `offset` 달의 달력. `offset` 이 0이 아니면 오늘 강조가 없다.
pub fn month_for(today: (i32, u32, u32), offset: i32) -> Month {
    let (cy, cm, cd) = today;
    let total = cy * 12 + (cm as i32 - 1) + offset;
    let year = total.div_euclid(12);
    let month = total.rem_euclid(12) as u32 + 1;
    Month {
        year,
        month,
        weeks: weeks_of(year, month),
        today: if offset == 0 { cd as u8 } else { 0 },
        today_of_month: cd as u8,
    }
}

/// 일요일 시작 주 배열(파이썬 `calendar.Calendar(firstweekday=6).monthdayscalendar`).
fn weeks_of(year: i32, month: u32) -> Vec<[u8; 7]> {
    let first = chrono::NaiveDate::from_ymd_opt(year, month, 1);
    let Some(first) = first else {
        return Vec::new();
    };
    // 일요일=0 으로 맞춘다(`chrono` 는 월요일=0). `Datelike` 를 여기서만 들여온다.
    let lead = {
        use chrono::Datelike;
        first.weekday().num_days_from_sunday() as usize
    };
    let days = days_in_month(year, month);
    let mut weeks = Vec::new();
    let mut week = [0u8; 7];
    let mut col = lead;
    for day in 1..=days {
        week[col] = day;
        col += 1;
        if col == 7 {
            weeks.push(week);
            week = [0u8; 7];
            col = 0;
        }
    }
    if col != 0 {
        weeks.push(week);
    }
    weeks
}

fn days_in_month(year: i32, month: u32) -> u8 {
    let (ny, nm) = if month == 12 { (year + 1, 1) } else { (year, month + 1) };
    match (
        chrono::NaiveDate::from_ymd_opt(ny, nm, 1),
        chrono::NaiveDate::from_ymd_opt(year, month, 1),
    ) {
        (Some(next), Some(this)) => (next - this).num_days() as u8,
        _ => 0,
    }
}

impl Month {
    /// 제목. 화살표는 **넘길 수 있다는 안내**다 — 클릭존이 그 자리에 선다.
    pub fn title(&self) -> String {
        format!("‹ {}-{:02} ›", self.year, self.month)
    }
}

/// 제목의 `‹`/`›` 클릭 영역 — `(x0, x1, y, delta)`. 반열림 구간 `[x0, x1)` 이다.
///
/// 그리면서 기록하지 않고 **같은 입력으로 다시 계산한다**. 합성이 `&self` 라 기록할 곳이
/// 없기도 하지만, 더 큰 이유는 기록본이 낡을 수 있어서다 — 창 크기가 바뀐 뒤 옛 좌표로
/// 클릭을 받으면 엉뚱한 달로 넘어간다.
pub fn nav_zones(rect: (usize, usize, usize, usize), month: &Month) -> Vec<(isize, isize, isize, i32)> {
    let title = month.title();
    let tlen = title.chars().count() as isize;
    let Some((_, oy, tx)) = title_origin(rect, month) else {
        return Vec::new(); // 단순 날짜 폴백에는 화살표가 없다 — 존도 없다.
    };
    vec![(tx, tx + 2, oy, -1), (tx + tlen - 2, tx + tlen, oy, 1)]
}

/// 클릭이 화살표에 맞았나. 맞았으면 옮길 달 수.
pub fn hit_nav(
    rect: (usize, usize, usize, usize),
    month: &Month,
    x: usize,
    y: usize,
) -> Option<i32> {
    nav_zones(rect, month)
        .into_iter()
        .find(|(x0, x1, zy, _)| *zy == y as isize && (*x0..*x1).contains(&(x as isize)))
        .map(|(_, _, _, delta)| delta)
}

/// 어느 단으로 그릴지.
enum Tier {
    /// 블록 폰트 달력. `(폰트, 격자 폭, 줄 수, 날짜칸 폭, 주 높이)`
    Big(clock::Font, usize, usize, usize, usize),
    /// 보통 격자. `(격자 폭, 줄 수, 칸 폭, 주 간격)`
    Grid(usize, usize, usize, usize),
    /// 단순 날짜.
    Plain,
}

fn tier_for(rect: (usize, usize, usize, usize), weeks: usize) -> Tier {
    let (_, _, pw, ph) = rect;
    // 큰 쪽부터 시도해 **들어가는 첫 단**을 쓴다.
    for big in [true, false] {
        let (rows, cols) = if big {
            (clock::BIG_ROWS, clock::BIG_COLS)
        } else {
            (clock::SMALL_ROWS, clock::SMALL_COLS)
        };
        let dcw = 2 * cols + DIG + 1;
        let rhb = rows + 1;
        let gw = 7 * dcw + 6 * DGAP;
        let nl = 4 + weeks * rhb;
        if pw >= gw + 2 && ph >= nl + 2 {
            let font = clock::font_for(usize::MAX, usize::MAX, 1);
            let font = if big {
                font
            } else {
                clock::Font {
                    glyphs: clock::SMALL,
                    rows: clock::SMALL_ROWS,
                    cols: clock::SMALL_COLS,
                    width: clock::SMALL_COLS,
                    big: false,
                }
            };
            return Tier::Big(font, gw, nl, dcw, rhb);
        }
    }
    // 칸 폭·주 간격을 가용 공간에 맞춰 키운다 — 넓고 높을수록 큰 달력이다.
    let mut colw = 4;
    while colw < 8 && pw >= (6 * (colw + 1) + 2) + 2 {
        colw += 1;
    }
    let mut rowh = 1;
    while rowh < 3 && ph >= (3 + (weeks.saturating_sub(1)) * (rowh + 1) + 1) + 2 {
        rowh += 1;
    }
    let grid_w = 6 * colw + 2;
    let nlines = 4 + weeks.saturating_sub(1) * rowh + 1;
    if pw >= grid_w && ph >= nlines {
        Tier::Grid(grid_w, nlines, colw, rowh)
    } else {
        Tier::Plain
    }
}

/// 제목이 서는 자리 `(ox, oy, tx)`. 단순 날짜 폴백이면 `None`.
fn title_origin(rect: (usize, usize, usize, usize), month: &Month) -> Option<(isize, isize, isize)> {
    let (px, py, pw, ph) = rect;
    let tlen = month.title().chars().count() as isize;
    let (gw, nl) = match tier_for(rect, month.weeks.len()) {
        Tier::Big(_, gw, nl, _, _) => (gw, nl),
        Tier::Grid(gw, nl, _, _) => (gw, nl),
        Tier::Plain => return None,
    };
    let ox = px as isize + (pw as isize - gw as isize) / 2;
    let oy = py as isize + (ph as isize - nl as isize) / 2;
    Some((ox, oy, ox + (gw as isize - tlen) / 2))
}

/// 패널 하나를 달력으로 덮는다. `rect` 는 `(x, y, w, h)` 다.
pub fn draw(canvas: &mut Canvas, rect: (usize, usize, usize, usize), month: &Month, st: Styles) {
    let (px, py, pw, ph) = rect;
    let (cols, rows) = canvas.size();

    // 1) 뒤 화면 흐리게(시계와 같은 규칙).
    for y in py..(py + ph).min(rows) {
        for x in px..(px + pw).min(cols) {
            if let Some(cell) = canvas.cell_mut(x, y) {
                cell.style = clock::darken(&cell.style);
            }
        }
    }

    let title = month.title();
    let tier = tier_for(rect, month.weeks.len());
    let origin = title_origin(rect, month);

    match tier {
        Tier::Big(font, gw, _, dcw, rhb) => {
            let (ox, oy, tx) = origin.expect("큰 달력에는 제목이 있다");
            put_str(canvas, tx, oy, &title, st.title);
            for (col, wd) in WEEKDAYS.iter().enumerate() {
                let hx = ox + (col * (dcw + DGAP)) as isize
                    + (dcw as isize - wd.chars().count() as isize) / 2;
                put_str(canvas, hx, oy + 2, wd, st.day);
            }
            for (wi, week) in month.weeks.iter().enumerate() {
                let ry = oy + 4 + (wi * rhb) as isize;
                for (col, day) in week.iter().enumerate() {
                    if *day == 0 {
                        continue;
                    }
                    let style = if *day == month.today { st.big_today } else { st.day };
                    let text = day.to_string();
                    let n = text.chars().count();
                    let gwd = n * font.cols + (n - 1) * DIG;
                    let gx0 = ox + (col * (dcw + DGAP)) as isize
                        + (dcw as isize - gwd as isize) / 2;
                    for (di, ch) in text.chars().enumerate() {
                        let dx = gx0 + (di * (font.cols + DIG)) as isize;
                        put_glyph(canvas, &font, ch, dx, ry, style);
                    }
                }
            }
            let _ = gw;
        }
        Tier::Grid(_, _, colw, rowh) => {
            let (ox, oy, tx) = origin.expect("보통 격자에도 제목이 있다");
            put_str(canvas, tx, oy, &title, st.title);
            for (col, wd) in WEEKDAYS.iter().enumerate() {
                put_str(canvas, ox + (col * colw) as isize, oy + 2, wd, st.day);
            }
            for (wi, week) in month.weeks.iter().enumerate() {
                let ry = oy + 4 + (wi * rowh) as isize;
                for (col, day) in week.iter().enumerate() {
                    if *day == 0 {
                        continue;
                    }
                    let style = if *day == month.today { st.today } else { st.day };
                    // 파이썬의 `f"{day:2d}"` — 한 자리는 **앞을 공백으로** 채운다
                    // (안 채우면 칸이 왼쪽으로 밀려 열이 어긋난다).
                    put_str(canvas, ox + (col * colw) as isize, ry, &format!("{day:2}"), style);
                }
            }
        }
        Tier::Plain => {
            // 이번 달이면 오늘 날짜까지, 넘긴 달이면 연-월만(그 달엔 오늘이 없다).
            let text = if month.today == 0 {
                format!("{}-{:02}", month.year, month.month)
            } else {
                format!("{}-{:02}-{:02}", month.year, month.month, month.today_of_month)
            };
            let n = text.chars().count();
            let ox = px as isize + (pw as isize - n as isize).max(0) / 2;
            let oy = py as isize + (ph / 2) as isize;
            put_str(canvas, ox, oy, &text, st.title);
        }
    }
}

fn put_str(canvas: &mut Canvas, x: isize, y: isize, text: &str, style: CellStyle) {
    for (i, c) in text.chars().enumerate() {
        canvas.put_cell(x + i as isize, y, c, style);
    }
}

fn put_glyph(canvas: &mut Canvas, font: &clock::Font, c: char, x: isize, y: isize, style: CellStyle) {
    let lines = font.glyphs.iter().find(|(g, _)| *g == c).map(|(_, l)| *l);
    for row in 0..font.rows {
        for i in 0..font.cols {
            let ch = lines
                .and_then(|l| l.get(row))
                .and_then(|line| line.chars().nth(i))
                .unwrap_or(' ');
            if ch != ' ' {
                canvas.put_cell(x + i as isize, y + row as isize, ch, style);
            }
        }
    }
}

#[cfg(test)]
#[path = "calendar_tests.rs"]
mod tests;
