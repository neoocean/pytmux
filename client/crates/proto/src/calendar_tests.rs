//! 달력의 **자리**와 경계(패리티 G7c). 그림 전체는 픽스처 대조가 지킨다.

use super::*;

#[test]
fn weeks_start_on_sunday() {
    // 파이썬은 `Calendar(firstweekday=6)` 다 — 월요일 시작으로 만들면 모든 날짜가
    // 한 칸씩 밀린다(그리고 그건 달력이 아니다).
    let july = month_for((2026, 7, 29), 0);
    // 2026-07-01 은 수요일 → 첫 주는 [0,0,0,1,2,3,4]
    assert_eq!(july.weeks[0], [0, 0, 0, 1, 2, 3, 4]);
}

#[test]
fn the_last_week_is_padded_with_zeros() {
    let july = month_for((2026, 7, 29), 0);
    let last = july.weeks.last().copied().expect("마지막 주");
    assert_eq!(last, [26, 27, 28, 29, 30, 31, 0]);
}

#[test]
fn every_day_of_the_month_appears_exactly_once() {
    for (year, month) in [(2026, 7), (2024, 2), (2026, 2), (2025, 12)] {
        let m = month_for((year, month, 1), 0);
        let mut days: Vec<u8> = m.weeks.iter().flatten().copied().filter(|d| *d > 0).collect();
        days.sort_unstable();
        let want: Vec<u8> = (1..=days_in_month(year, month)).collect();
        assert_eq!(days, want, "{year}-{month}");
    }
}

#[test]
fn a_pane_too_small_still_says_the_date() {
    // 아무것도 안 그리면 사용자는 명령이 안 먹은 줄 안다.
    let mut canvas = Canvas::new(12, 3);
    let month = month_for((2026, 7, 29), 0);
    draw(&mut canvas, (0, 0, 12, 3), &month, styles());
    let text: String = (0..3)
        .flat_map(|y| (0..12).map(move |x| (x, y)))
        .map(|(x, y)| canvas.cell(x, y).map_or(' ', |c| c.ch))
        .collect();
    assert!(text.contains("2026-07-29"), "{text:?}");
}

#[test]
fn the_calendar_stays_inside_its_pane() {
    let mut canvas = Canvas::new(80, 24);
    for y in 0..24 {
        for x in 0..80 {
            if let Some(cell) = canvas.cell_mut(x, y) {
                cell.ch = 'x';
            }
        }
    }
    let month = month_for((2026, 7, 29), 0);
    draw(&mut canvas, (0, 0, 40, 24), &month, styles());
    for y in 0..24 {
        for x in 40..80 {
            assert_eq!(
                canvas.cell(x, y).map(|c| c.ch),
                Some('x'),
                "오른쪽 패널을 건드렸다 ({x},{y})"
            );
        }
    }
}

#[test]
fn clicking_an_arrow_moves_a_month() {
    let month = month_for((2026, 7, 29), 0);
    let rect = (0, 0, 30, 10);
    let zones = nav_zones(rect, &month);
    assert_eq!(zones.len(), 2, "{zones:?}");
    let (x0, _, y, delta) = zones[0];
    assert_eq!(hit_nav(rect, &month, x0 as usize, y as usize), Some(delta));
    // 가운데(날짜 숫자 자리)는 아무 일도 아니다 — 안 그러면 제목을 클릭할 때마다
    // 달이 넘어간다.
    let middle = (zones[0].1 + zones[1].0) / 2;
    assert_eq!(hit_nav(rect, &month, middle as usize, y as usize), None);
}

#[test]
fn a_pane_with_no_arrows_has_no_click_zones() {
    // 단순 날짜 폴백에는 화살표가 없다 — 존이 남아 있으면 안 보이는 것을 누르게 된다.
    let month = month_for((2026, 7, 29), 0);
    assert!(nav_zones((0, 0, 12, 3), &month).is_empty());
}

fn styles() -> Styles {
    let st = CellStyle::default();
    Styles { day: st, title: st, today: st, big_today: st }
}

/// 큰 달력의 오늘은 `big_today`, 보통 격자의 오늘은 `today` 로 그려지나.
///
/// # 왜 표 대조만으로는 모자란가
///
/// `overlay_style_conformance.rs` 는 **표**가 정본 관습대로인지를 잰다 — 그런데 그리는
/// 쪽이 둘을 바꿔 써도 그 테스트는 초록이다(변이로 확인했다). 정본이 두 단을 갈라 둔 이유가
/// 블록 폰트에 배경을 깔면 화면 절반이 색 덩어리가 되기 때문이라, 바뀌면 큰 달력이 딴판이
/// 된다. 그래서 여기서는 **그려진 셀**을 본다.
#[test]
fn each_tier_uses_its_own_today_style() {
    // 두 스타일을 서로 다른 표식으로 구분한다(색이 아니라 **어느 것이 왔나**를 잰다).
    let mark = |bold: bool, underline: bool| CellStyle { bold, underline, ..Default::default() };
    let st = Styles {
        day: CellStyle::default(),
        title: CellStyle::default(),
        today: mark(true, false),      // 격자용
        big_today: mark(false, true),  // 큰 달력용
    };
    let month = month_for((2026, 7, 29), 0);

    let cells_of = |w: usize, h: usize| {
        let mut canvas = Canvas::new(w, h);
        draw(&mut canvas, (0, 0, w, h), &month, st);
        let mut bolds = 0usize;
        let mut unders = 0usize;
        for y in 0..h {
            for x in 0..w {
                if let Some(cell) = canvas.cell(x, y) {
                    if cell.ch != ' ' && cell.style.bold {
                        bolds += 1;
                    }
                    if cell.ch != ' ' && cell.style.underline {
                        unders += 1;
                    }
                }
            }
        }
        (bolds, unders)
    };

    // 넉넉한 판 = 큰 달력 → `big_today`(밑줄 표식)만 나온다.
    let (big_bold, big_under) = cells_of(120, 40);
    assert!(big_under > 0, "큰 달력에 big_today 가 안 왔다");
    assert_eq!(big_bold, 0, "큰 달력이 격자용 today 를 썼다");

    // 좁은 판 = 보통 격자 → `today`(굵기 표식)만 나온다.
    let (grid_bold, grid_under) = cells_of(40, 12);
    assert!(grid_bold > 0, "격자에 today 가 안 왔다");
    assert_eq!(grid_under, 0, "격자가 큰 달력용 big_today 를 썼다");
}
