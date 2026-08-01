//! 달력이 파이썬과 **같은 그림**인가 — 화면 대조(패리티 G7c).
//!
//! 글리프만 맞춰 놓고 배치 규칙(칸 폭·주 간격·단 떨어지는 경계)을 손으로 옮기면, 특정 창
//! 크기에서만 두 클라가 다르게 보인다. 사람이 그 크기를 우연히 만나기 전까지 아무도 모른다.
//! 그래서 픽스처가 **그려진 화면 전체**를 뜬다(`scripts/gen_calendar_fixture.py`).

use proto::calendar::{self, Month, Styles};
use proto::canvas::Canvas;
use proto::style::CellStyle;
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    /// 픽스처를 뽑을 때 고정한 기준 시각(`YYYY-MM-DDTHH:MM:SS`).
    now: String,
    cases: Vec<Case>,
}

#[derive(Deserialize)]
struct Case {
    w: usize,
    h: usize,
    offset: i32,
    lines: Vec<String>,
    nav_zones: Vec<Vec<i64>>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/calendar.json")).expect("픽스처를 읽을 수 없다")
}

/// 픽스처의 기준 시각에서 `(년, 월, 일)`.
fn today_of(fx: &Fixture) -> (i32, u32, u32) {
    let date = fx.now.split('T').next().expect("날짜 부분");
    let mut parts = date.split('-').map(|p| p.parse::<i64>().expect("숫자"));
    (
        parts.next().expect("년") as i32,
        parts.next().expect("월") as u32,
        parts.next().expect("일") as u32,
    )
}

fn plain_styles() -> Styles {
    // 색은 대조하지 않는다 — 픽스처가 글자만 뜬다(색은 테마가 정하고, 우리 팔레트와
    // 파이썬 rich 스타일을 일대일로 맞출 수 없다).
    let st = CellStyle::default();
    Styles { day: st, title: st, today: st, big_today: st }
}

fn render(w: usize, h: usize, month: &Month) -> Vec<String> {
    let mut canvas = Canvas::new(w, h);
    calendar::draw(&mut canvas, (0, 0, w, h), month, plain_styles());
    (0..h)
        .map(|y| {
            let line: String = (0..w)
                .map(|x| canvas.cell(x, y).map_or(' ', |c| c.ch))
                .collect();
            line.trim_end().to_owned()
        })
        .collect()
}

#[test]
fn every_size_draws_the_same_screen_as_python() {
    let fx = fixture();
    let today = today_of(&fx);
    for case in &fx.cases {
        let month = calendar::month_for(today, case.offset);
        let got = render(case.w, case.h, &month);
        assert_eq!(
            got, case.lines,
            "{}x{} (offset {}) 에서 그림이 갈렸다",
            case.w, case.h, case.offset
        );
    }
}

#[test]
fn the_arrows_sit_where_python_puts_them() {
    // 제목의 `‹`/`›` 가 클릭 영역이다 — 자리가 갈리면 화살표가 거짓말이 된다.
    let fx = fixture();
    let today = today_of(&fx);
    for case in &fx.cases {
        let month = calendar::month_for(today, case.offset);
        let got: Vec<Vec<i64>> = calendar::nav_zones((0, 0, case.w, case.h), &month)
            .into_iter()
            .map(|(x0, x1, y, d)| vec![x0 as i64, x1 as i64, y as i64, d as i64])
            .collect();
        assert_eq!(
            got, case.nav_zones,
            "{}x{} (offset {}) 의 클릭존이 갈렸다",
            case.w, case.h, case.offset
        );
    }
}

#[test]
fn the_fixture_covers_all_three_tiers() {
    // ★ 큰 달력 · 보통 격자 · 단순 날짜가 다 들어 있어야 이 표가 뜻이 있다. 한 단만
    //   덮은 표는 "전부 통과"처럼 보인다.
    let fx = fixture();
    let today = today_of(&fx);
    let mut big = false;
    let mut grid = false;
    let mut plain = false;
    for case in fx.cases.iter().filter(|c| c.offset == 0) {
        let month = calendar::month_for(today, case.offset);
        let drawn = render(case.w, case.h, &month);
        let text = drawn.join("\n");
        if text.contains('█') {
            big = true;
        } else if text.contains("Su") {
            grid = true;
        } else if text.contains("2026-07-29") {
            plain = true;
        }
    }
    assert!(big && grid && plain, "big={big} grid={grid} plain={plain}");
}

#[test]
fn a_month_we_navigated_to_has_no_today() {
    // 넘긴 달에는 오늘이 없다 — 강조가 남아 있으면 다른 달의 같은 날짜가 오늘처럼 보인다.
    let today = (2026, 7, 29);
    assert_eq!(calendar::month_for(today, 0).today, 29);
    assert_eq!(calendar::month_for(today, -1).today, 0);
    assert_eq!(calendar::month_for(today, 1).today, 0);
}

#[test]
fn navigation_crosses_the_year() {
    let today = (2026, 1, 15);
    let prev = calendar::month_for(today, -1);
    assert_eq!((prev.year, prev.month), (2025, 12));
    let next = calendar::month_for((2026, 12, 15), 1);
    assert_eq!((next.year, next.month), (2027, 1));
}

#[test]
fn february_knows_about_leap_years() {
    // 2월 마지막 줄이 틀리면 그 달만 조용히 어긋난다.
    let last = |y: i32| {
        calendar::month_for((y, 2, 1), 0)
            .weeks
            .iter()
            .flatten()
            .copied()
            .max()
            .unwrap_or(0)
    };
    assert_eq!(last(2024), 29, "윤년");
    assert_eq!(last(2026), 28);
    assert_eq!(last(2000), 29, "400 으로 나뉘는 해는 윤년");
    assert_eq!(last(1900), 28, "100 으로 나뉘면 평년");
}
