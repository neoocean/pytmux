//! 교차구현 적합성 — 터치 스크롤바의 **산수**가 정본과 한 자리도 안 갈리는가.
//!
//! 픽스처는 정본을 직접 import 해 뽑는다(`scripts/gen_scrollbar_fixture.py`). 그래서
//! 정본이 반올림이나 경계를 바꾸면 여기가 먼저 운다.
//!
//! # 왜 모양이 아니라 값인가
//!
//! 이 바의 오차는 **썸이 한 칸 어긋나는 것**으로만 보인다 — 두 클라를 나란히 놓고
//! 봐야 아는 부류이고, 라이브에서 "그런가 보다" 하고 지나가기 쉽다. 반올림이 세
//! 군데(썸 길이·썸 위치·점프 델타)라 손으로 옮기면 거의 반드시 한 군데가 갈린다.

use base::scrollbar::{self, Hit};
use serde::Deserialize;

#[derive(Deserialize)]
struct Fx {
    consts: Consts,
    bars: Vec<BarCase>,
    hits: Vec<HitCase>,
    jumps: Vec<JumpCase>,
}

#[derive(Deserialize)]
struct Consts {
    up: char,
    down: char,
    track: char,
    thumb: char,
    min_h: usize,
}

#[derive(Deserialize)]
struct BarCase {
    h: usize,
    top: usize,
    scroll: usize,
    bar: String,
}

#[derive(Deserialize)]
struct HitCase {
    h: usize,
    /// 정본은 음수도 넣어 본다(범위 밖) — 우리 쪽은 `usize` 라 그 경우를 건너뛴다.
    iy: i64,
    hit: Option<HitWire>,
}

#[derive(Deserialize)]
struct HitWire {
    kind: String,
    frac: Option<f64>,
}

#[derive(Deserialize)]
struct JumpCase {
    top: usize,
    scroll: usize,
    frac: f64,
    delta: i32,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/scrollbar.json"))
        .expect("픽스처를 못 읽었다 — scripts/gen_scrollbar_fixture.py 로 다시 뽑을 것")
}

/// 빈 픽스처는 통과가 아니라 고장이다(이 저장소가 한 번 밟았다 — 규칙이 하나도 안
/// 걸리는데 rc 0 이던 게이트).
#[test]
fn the_fixture_has_cases() {
    let fx = fixture();
    assert!(!fx.bars.is_empty() && !fx.hits.is_empty() && !fx.jumps.is_empty());
}

#[test]
fn we_draw_the_same_glyphs_as_canon() {
    let c = fixture().consts;
    assert_eq!(c.up, scrollbar::UP);
    assert_eq!(c.down, scrollbar::DOWN);
    assert_eq!(c.track, scrollbar::TRACK);
    assert_eq!(c.thumb, scrollbar::THUMB);
    assert_eq!(c.min_h, scrollbar::MIN_H);
}

#[test]
fn every_bar_matches_canon_cell_for_cell() {
    for case in fixture().bars {
        let ours: String = scrollbar::chars(case.h, case.top, case.scroll).into_iter().collect();
        assert_eq!(
            ours, case.bar,
            "h={} top={} scroll={} 에서 바가 갈렸다",
            case.h, case.top, case.scroll
        );
    }
}

#[test]
fn every_tap_lands_where_canon_says() {
    for case in fixture().hits {
        let Ok(iy) = usize::try_from(case.iy) else {
            continue; // 정본만 음수를 시험한다 — 우리 타입에는 그 값이 없다.
        };
        let ours = scrollbar::hit(case.h, iy);
        match (&case.hit, ours) {
            (None, None) => {}
            (Some(want), Some(got)) => {
                let same = match (want.kind.as_str(), got) {
                    ("up", Hit::Up) | ("down", Hit::Down) => true,
                    ("jump", Hit::Jump(frac)) => {
                        (frac - want.frac.expect("jump 은 frac 이 있다")).abs() < 1e-9
                    }
                    _ => false,
                };
                assert!(same, "h={} iy={} 에서 판정이 갈렸다: {got:?}", case.h, case.iy);
            }
            (want, got) => panic!("h={} iy={}: 정본 {:?} 우리 {got:?}", case.h, case.iy, want.is_some()),
        }
    }
}

#[test]
fn every_jump_moves_by_the_same_delta() {
    for case in fixture().jumps {
        assert_eq!(
            scrollbar::jump_delta(case.top, case.scroll, case.frac),
            case.delta,
            "top={} scroll={} frac={} 에서 델타가 갈렸다",
            case.top,
            case.scroll,
            case.frac
        );
    }
}
