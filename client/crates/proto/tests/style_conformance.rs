//! 스타일 적합성 — 이 파서가 **서버가 낼 수 있는 모든 스타일 값**을 아는가.
//!
//! # 왜 별도 코퍼스가 필요했나
//!
//! P2 의 화면 적합성 오라클(`conformance.rs`)은 텍스트 배치만 고정한다. 코퍼스로 쓴
//! 클로드 화면 덤프 12개에 SGR 색 시퀀스가 **하나도 없어서** 스타일 경로는 전혀
//! 검증되지 않았다. 색을 실제로 칠하는 P3 에서 그 공백을 메운다.
//!
//! # 오라클
//!
//! `scripts/gen_style_fixture.py` 가 **실제 서버 코드**에 SGR 을 먹여 나오는 스타일
//! 객체를 그대로 뽑아 둔다. 즉 기준이 내가 상상한 값이 아니라 서버가 내놓는 값이다.
//!
//! 여기서 잡는 것은 "색이 예쁜가"가 아니라 **"모르는 값이 있는가"** 다. 모르는 색
//! 이름은 조용히 기본색으로 떨어져 아무 신호도 안 남기기 때문에, 표로 못박지 않으면
//! 사용자가 "가끔 색이 안 나온다"고 말할 때까지 아무도 모른다.

use std::collections::BTreeSet;

use proto::message::Style as StyleMap;
use proto::style::{CellStyle, Color};
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    sgr: Vec<Entry>,
    colors: Vec<String>,
    attrs: Vec<String>,
}

#[derive(Deserialize)]
struct Entry {
    seq: String,
    style: StyleMap,
}

fn load() -> Fixture {
    serde_json::from_str(include_str!("fixtures/styles.json")).expect("스타일 픽스처를 읽을 수 없다")
}

#[test]
fn every_color_the_server_can_emit_is_understood() {
    let fixture = load();
    let unknown: Vec<&String> = fixture
        .colors
        .iter()
        .filter(|name| Color::parse(name).is_none())
        .collect();
    assert!(
        unknown.is_empty(),
        "서버가 보내는데 파서가 모르는 색 {}종: {unknown:?}\n\
         → 모르는 색은 조용히 기본색이 된다. src/style.rs 의 표에 추가할 것.",
        unknown.len()
    );
}

#[test]
fn every_attribute_key_the_server_can_emit_is_understood() {
    // 파서가 읽는 키 목록. 서버가 새 속성을 추가하면 여기서 걸린다.
    let known: BTreeSet<&str> = ["bo", "it", "un", "rv", "st"].into_iter().collect();
    let fixture = load();
    let unknown: Vec<&String> = fixture
        .attrs
        .iter()
        .filter(|k| !known.contains(k.as_str()))
        .collect();
    assert!(
        unknown.is_empty(),
        "서버가 보내는데 파서가 모르는 속성 키: {unknown:?}"
    );
}

#[test]
fn no_sgr_sample_loses_its_styling_entirely() {
    // 각 표본은 무언가를 지정하는 SGR 이다. 해석 뒤에도 무언가는 남아야 한다.
    let fixture = load();
    let mut lost = Vec::new();
    for entry in &fixture.sgr {
        if entry.style.is_empty() {
            continue; // 서버가 애초에 아무 스타일도 안 낸 표본(예: 속성 없는 코드)
        }
        if CellStyle::from_map(&entry.style).is_default() {
            lost.push(&entry.seq);
        }
    }
    assert!(
        lost.is_empty(),
        "스타일이 통째로 사라진 SGR {}건: {lost:?}",
        lost.len()
    );
}

#[test]
fn bright_yellow_and_bright_magenta_survive() {
    // 이 세 코드가 파이썬 클라에서 색을 잃는 자리다(Rich 가 이름을 모른다).
    // 이쪽은 제대로 칠한다 — 의도적 차이라 명시적으로 못박는다.
    let fixture = load();
    for seq in ["93", "103", "105"] {
        let entry = fixture
            .sgr
            .iter()
            .find(|e| e.seq == seq)
            .unwrap_or_else(|| panic!("표본 SGR {seq} 가 없다"));
        let style = CellStyle::from_map(&entry.style);
        assert!(
            style.fg.is_some() || style.bg.is_some(),
            "SGR {seq} 의 색이 사라졌다: {:?}",
            entry.style
        );
    }
}

#[test]
fn the_corpus_covers_what_it_claims_to() {
    let fixture = load();
    assert!(fixture.sgr.len() >= 60, "표본이 너무 적다: {}", fixture.sgr.len());
    assert!(
        fixture.colors.iter().any(|c| c.starts_with('#')),
        "16진수 색 표본이 없다(256색·트루컬러 경로 미검증)"
    );
    assert!(
        fixture.colors.iter().any(|c| c.starts_with("bright_")),
        "밝은 색 표본이 없다"
    );
    for attr in ["bo", "it", "un", "rv", "st"] {
        assert!(
            fixture.attrs.iter().any(|a| a == attr),
            "속성 {attr} 표본이 없다"
        );
    }
}
