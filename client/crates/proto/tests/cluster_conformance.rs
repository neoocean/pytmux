//! 적합성 — **문자소 군집이 격자에 앉는 자리**가 정본과 같은가(pytmux-407 ⓐ).
//!
//! # 무엇을 재나
//!
//! 사람이 고른 규약(2026-09-01)은 **군집의 폭 = 밑글자의 폭**이다(tmux 3.4·현대 단말과
//! 같다). `👨‍👩‍👧` 는 코드포인트 다섯이지만 **한 칸(폭 2)**이고, `🇰🇷🇯🇵` 는 깃발 둘이라
//! 네 칸이다. 그 판정(`cellwidth.joins_previous` ↔ `compose::joins_previous`)을 두
//! 언어가 각각 적으므로, 갈리면 **그 줄이 통째로 밀린다** — 폭 판정이 갈릴 때와 같은
//! 증상이고 그래서 같은 방식으로 잰다(`conformance.rs` 의 형제).
//!
//! 픽스처는 **정본 서버 격자를 실제로 돌려서** 뽑는다(`scripts/gen_clusters.py` →
//! `fixtures/clusters.json`) — 규칙을 베껴 적은 표가 아니라 화면 모델이 낸 칸들이다.
//!
//! # 실패하면
//!
//! 어느 표본의 몇 번째 칸인지 이름으로 보고한다. 대개 `joins_previous` 의 세 갈래
//! (ZWJ · 피부톤 수정자 · 지역 지시자 홀짝) 중 하나가 한쪽에만 들어간 것이다.

use proto::canvas::Canvas;
use proto::style::CellStyle;
use serde::Deserialize;

#[derive(Deserialize)]
struct Sample {
    name: String,
    text: String,
    cells: Vec<String>,
}

#[derive(Deserialize)]
struct Fixture {
    cols: usize,
    samples: Vec<Sample>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/clusters.json"))
        .expect("clusters.json 을 못 읽는다")
}

/// 캔버스 한 줄을 **정본과 같은 모양**으로 읽는다 — 연속 칸은 빈 문자열, 꼬리 공백은 뗀다.
fn row_cells(canvas: &Canvas, cols: usize) -> Vec<String> {
    let mut out: Vec<String> = (0..cols)
        .map(|x| match canvas.cell(x, 0) {
            Some(c) if c.continuation => String::new(),
            Some(c) => c.text(),
            None => " ".to_owned(),
        })
        .collect();
    while out.last().is_some_and(|c| c == " ") {
        out.pop();
    }
    out
}

#[test]
fn the_fixture_actually_measured_something() {
    // ★ 이 오라클이 먼저다 — 표본이 비면 아래 단언이 "빈 것 == 빈 것"이 된다.
    let fx = fixture();
    assert!(fx.samples.len() >= 15, "표본이 너무 적다: {}", fx.samples.len());
    let family = fx
        .samples
        .iter()
        .find(|s| s.name == "zwj_family")
        .expect("가족 이모지 표본이 없다");
    assert_eq!(
        family.cells,
        vec!["|", "👨\u{200d}👩\u{200d}👧", "", "|"],
        "픽스처가 옛 규약(칸 여섯)으로 뽑혔다 — 다시 뽑을 것"
    );
}

#[test]
fn every_cluster_lands_where_the_canon_puts_it() {
    let fx = fixture();
    let mut wrong = Vec::new();
    for sample in &fx.samples {
        let mut canvas = Canvas::new(fx.cols, 1);
        canvas.put_text(0, 0, &sample.text, CellStyle::default());
        let got = row_cells(&canvas, fx.cols);
        if got != sample.cells {
            wrong.push(format!("{}: 우리 {:?} · 정본 {:?}", sample.name, got, sample.cells));
        }
    }
    assert!(
        wrong.is_empty(),
        "격자가 정본과 갈렸다:\n  {}\n  `python3 scripts/gen_clusters.py` 로 다시 뽑았는지, \
         그리고 `compose::joins_previous` 가 정본 `cellwidth.joins_previous` 와 같은지 볼 것.",
        wrong.join("\n  ")
    );
}

#[test]
fn a_zwj_between_letters_is_not_a_cluster() {
    // ⛔ 대조군 — ZWJ 는 데바나가리 등에서 **이음/끊음 제어**로도 쓰인다. 거기서 두
    //    글자를 한 칸에 접으면 그 줄이 어긋난다. 「무엇이든 ZWJ 뒤면 잇는다」는 판을
    //    이 시험이 문다(그림 글자일 것을 함께 묻는 이유다).
    let fx = fixture();
    let sample = fx
        .samples
        .iter()
        .find(|s| s.name == "zwj_devanagari")
        .expect("대조군 표본이 없다");
    assert!(
        sample.cells.len() >= 4,
        "정본이 이 둘을 한 칸으로 접었다 — 규칙이 너무 넓다: {:?}",
        sample.cells
    );
}
