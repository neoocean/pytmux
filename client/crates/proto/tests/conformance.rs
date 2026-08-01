//! 교차구현 적합성 — 이 클라이언트가 파이썬 클라이언트와 **같은 화면**을 만드는가.
//!
//! # 왜 이 오라클을 믿을 수 있는가
//!
//! 새로 만든 기준이 아니라 **pytmux 가 이미 쓰고 있는 골든**이다. pytmux 에는
//! `tests/fixtures/replay_golden.json`(p4 66957)이 있고, 60개 표본의 화면 합성 결과를
//! SHA-256 으로 동결해 파이썬 쪽 회귀를 잡고 있다.
//!
//! 그 골든이 해싱하는 `replay.render_pane_lines()` 의 입력이 `pane.render(True)` —
//! **서버가 `screen` 메시지로 보내는 `rows` 그 자체**다. 그래서 같은 `rows` 를 이쪽이
//! 합성해 같은 해시가 나오면, 격자 해석이 파이썬 클라와 같다는 뜻이 된다.
//!
//! 표본은 실제 Claude 화면 덤프 12개 + 경계를 겨눈 합성 문자열 8개(오른쪽 끝의 넓은
//! 글자, CR 로 쪼개진 한글, 스크롤, 대체 화면, 결합 문자, 이모지, 커서 이동, 긴 줄
//! 감김) × 세 가지 크기다.
//!
//! # 실패하면
//!
//! 어느 표본인지 이름으로 보고한다. 대개 폭 판정(`compose::char_cells`)이 파이썬
//! `wcwidth` 와 갈린 것이다 — 이모지·결합 문자·CJK 경계에서 잘 생긴다.
//!
//! 픽스처를 다시 만들려면: `python3 scripts/gen_wire_fixture.py`

use std::collections::BTreeMap;

use proto::compose::{compose_rows, display_width};
use proto::message::Run;
use serde::Deserialize;
use sha2::{Digest, Sha256};

#[derive(Deserialize)]
struct Sample {
    cols: usize,
    /// 와이어 그대로의 행 런: `[[[텍스트, 스타일객체], ...], ...]`
    rows: Vec<Vec<(String, serde_json::Map<String, serde_json::Value>)>>,
    /// pytmux 골든이 이 표본에 대해 동결한 값.
    sha256: String,
}

fn load() -> BTreeMap<String, Sample> {
    let raw = include_str!("fixtures/wire_rows.json");
    serde_json::from_str(raw).expect("적합성 픽스처를 읽을 수 없다")
}

fn to_rows(sample: &Sample) -> Vec<Vec<Run>> {
    sample
        .rows
        .iter()
        .map(|row| {
            row.iter()
                .map(|(text, style)| Run {
                    text: text.clone(),
                    style: style.clone(),
                })
                .collect()
        })
        .collect()
}

fn digest(lines: &[String]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(lines.join("\n").as_bytes());
    format!("{:x}", hasher.finalize())
}

#[test]
fn composed_screens_match_the_python_golden() {
    let samples = load();
    assert!(!samples.is_empty(), "픽스처가 비어 있다");

    let mut drift = Vec::new();
    for (name, sample) in &samples {
        let lines = compose_rows(&to_rows(sample), sample.cols);
        let got = digest(&lines);
        if got != sample.sha256 {
            drift.push(format!("{name}\n    골든 {}\n    계산 {got}", sample.sha256));
        }
    }

    assert!(
        drift.is_empty(),
        "파이썬 클라이언트와 화면이 갈린 표본 {}/{}건:\n  {}",
        drift.len(),
        samples.len(),
        drift.join("\n  ")
    );
}

#[test]
fn every_composed_line_is_exactly_cols_wide() {
    // 해시는 "같다"만 말한다. 이 불변식은 "무엇이 옳은가"를 말한다 — 줄의 **시각적
    // 폭**이 cols 와 같아야 한다(문자 수가 아니다). 넓은 글자가 두 칸을 먹고 연속 셀이
    // 빠지므로 둘은 다르다.
    for (name, sample) in load() {
        for (index, line) in compose_rows(&to_rows(&sample), sample.cols)
            .iter()
            .enumerate()
        {
            assert_eq!(
                display_width(line),
                sample.cols,
                "{name} 의 {index}번째 줄 폭이 어긋났다: {line:?}"
            );
        }
    }
}

#[test]
fn the_corpus_covers_the_geometries_and_hard_cases_it_claims_to() {
    // 픽스처가 조용히 쪼그라들면 위 두 테스트가 통과해도 의미가 없다.
    let samples = load();
    assert_eq!(samples.len(), 60, "표본 수가 pytmux 골든과 다르다");

    for geometry in ["@80x24", "@40x10", "@120x30"] {
        assert!(
            samples.keys().any(|k| k.ends_with(geometry)),
            "{geometry} 크기의 표본이 없다"
        );
    }
    for hard_case in [
        "synth_wide_at_right_edge",
        "synth_combining_zero_width",
        "synth_emoji_and_box",
        "synth_alt_screen",
        "synth_long_wrap",
    ] {
        assert!(
            samples.keys().any(|k| k.starts_with(hard_case)),
            "경계 표본 {hard_case} 가 없다"
        );
    }
    assert!(
        samples.keys().any(|k| k.starts_with("fixture_")),
        "실제 화면 덤프 표본이 없다 — 합성 문자열만으로는 부족하다"
    );
}
