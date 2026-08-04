//! 교차구현 적합성 — 패널 글의 **경로 범위**를 두 클라가 같은 자리로 짚나(§10-21ⓧ2).
//!
//! # 왜 재나
//!
//! 이 제보는 *"정본에도 같이"*를 명시했다 — 두 클라 다 이 밑줄을 긋는다. 그런데 판정은
//! 휴리스틱이다(구분자가 있나 · 마지막 조각에 확장자가 있나 · 감싼 괄호를 어디까지 떼나).
//! 그런 규칙은 옮겨 적으면 반드시 한 칸씩 어긋나고, **어긋난 것은 나란히 놓아야만 보인다**
//! (한쪽에서만 밑줄이 한 글자 길거나 짧다 — 복사한 값도 그만큼 다르다).
//!
//! 픽스처는 정본 함수를 **직접 불러** 뽑는다(`scripts/gen_spans_fixture.py`) — 정본이
//! 규칙을 바꾸면 여기가 먼저 운다.
//!
//! 링크(URL)는 안 잰다: **GUI 전용**이라 정본에 짝이 없다(§10-21ⓥ2). 없는 것을 대조하면
//! 픽스처가 거짓말을 한다.

use base::spans::{SpanKind, paths};
use serde::Deserialize;

#[derive(Deserialize)]
struct Fx {
    cases: Vec<Case>,
}

#[derive(Deserialize)]
struct Case {
    line: String,
    paths: Vec<Want>,
}

#[derive(Deserialize, Debug, PartialEq, Eq)]
struct Want {
    start: usize,
    end: usize,
    text: String,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/spans.json"))
        .expect("픽스처를 못 읽었다 — scripts/gen_spans_fixture.py 로 다시 뽑을 것")
}

#[test]
fn the_fixture_actually_measured_something() {
    // 빈 표는 통과가 아니라 고장이다(이 저장소가 한 번 밟은 자리).
    let fx = fixture();
    assert!(!fx.cases.is_empty());
    assert!(
        fx.cases.iter().map(|c| c.paths.len()).sum::<usize>() >= 5,
        "경계 사례가 너무 적다 — 아무것도 안 재는 표가 된다"
    );
}

#[test]
fn every_line_yields_the_same_paths_as_canon() {
    for case in fixture().cases {
        let got: Vec<Want> = paths(&case.line)
            .into_iter()
            .map(|s| {
                assert_eq!(s.kind, SpanKind::Path);
                Want { start: s.start, end: s.end, text: s.text }
            })
            .collect();
        assert_eq!(
            got, case.paths,
            "정본과 다른 자리를 짚는다 — 줄: {:?}",
            case.line
        );
    }
}
