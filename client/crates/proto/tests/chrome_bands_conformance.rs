//! 창의 띠 배치 적합성(pytmux-637) — 정본 `clientutil.chrome_bands` 가 쓴 답지
//! (`scripts/gen_chrome_bands.py` → `fixtures/chrome_bands.json`)에 대고 `base::chrome::bands`
//! 를 잰다.
//!
//! 재는 것: 탭바 보임/숨김 × 상태줄 위/아래 × 줄 수 0~5 의 **쌓는 차례**, 그리고 상태줄 몫 중
//! 뷰 **아래** 줄 수(GUI 의 격자 예산이 그 수를 뺀다 — 위 몫은 뷰 위 띠가 먹는다). 정본에서
//! 뷰 높이 == 보고 행 수인 것은 답지의 `report_rows` 와 `bands.view` 가 같은지로 함께 본다.
//! 그 답이 정본 실제 위젯 자리와 같은지는 `tests/test_status_position_layout.py` 가 잰다.

use base::chrome::{bands, status_rows_below_view};

#[derive(serde::Deserialize)]
struct Fixture {
    height: usize,
    rows: Vec<Row>,
}

#[derive(serde::Deserialize)]
struct Row {
    tabbar: bool,
    position: String,
    lines: u8,
    order: Vec<String>,
    bands: std::collections::BTreeMap<String, (usize, usize)>,
    report_rows: usize,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/chrome_bands.json")).expect("답지를 못 읽었다")
}

#[test]
fn the_band_order_matches_the_canon_table() {
    let fx = fixture();
    assert_eq!(fx.rows.len(), 24, "조합 수가 달라졌다 — 생성기를 봐라");
    for row in &fx.rows {
        let top = row.position == "top";
        let ours: Vec<&str> =
            bands(row.tabbar, top, row.lines).into_iter().map(|b| b.canon_name()).collect();
        assert_eq!(ours, row.order, "차례가 갈렸다: {}/{}/{}", row.tabbar, row.position, row.lines);
        // 정본에서 뷰 높이 == 보고 행 수(637 의 요점).
        assert_eq!(row.bands["view"].1, row.report_rows, "정본 답지가 스스로 어긋났다");
    }
}

#[test]
fn only_a_bottom_status_bar_takes_rows_below_the_view() {
    let fx = fixture();
    for row in &fx.rows {
        let top = row.position == "top";
        let below = row
            .bands
            .get("status")
            .filter(|(y, _)| *y > row.bands["view"].0)
            .map_or(0, |(_, h)| *h);
        assert_eq!(
            usize::from(status_rows_below_view(top, row.lines)),
            below,
            "뷰 아래 상태줄 몫이 갈렸다: {}/{}/{}",
            row.tabbar,
            row.position,
            row.lines
        );
        // 띠는 화면을 정확히 채운다(겹침 0).
        let sum: usize = row.bands.values().map(|(_, h)| h).sum();
        assert_eq!(sum, fx.height);
    }
}
