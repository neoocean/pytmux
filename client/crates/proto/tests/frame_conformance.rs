//! 교차구현 적합성 — 경계 문자 합성표가 파이썬 클라와 같은가.
//!
//! 맞닿은 두 패널의 테두리는 같은 칸을 두 번 그린다. 그 칸을 `┬`·`┴`·`┼` 로 합치는
//! 규칙이 문자↔변 비트 표이고, 파이썬 클라(`clientutil._BOX_BITS`)와 이쪽
//! (`canvas::BOX_BITS`)이 **값으로 같은 표를 복제**하고 있다. 한쪽만 바뀌면 두 클라가
//! 같은 배치에서 다른 모양을 그리는데, 그건 조용히 일어난다.
//!
//! 픽스처는 파이썬 상수에서 뽑았다: `python3 scripts/gen_box_fixture.py`

use std::collections::BTreeMap;

use proto::canvas::BOX_BITS;
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    bits: BTreeMap<String, u8>,
}

#[test]
fn box_merge_table_matches_the_python_client() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/box_chars.json")).expect("픽스처를 읽을 수 없다");
    let ours: BTreeMap<String, u8> = BOX_BITS
        .iter()
        .map(|(ch, bits)| (ch.to_string(), *bits))
        .collect();
    assert_eq!(
        ours, fixture.bits,
        "경계 문자 합성표가 파이썬 클라와 갈렸다. 파이썬이 바뀐 것이면 \
         `python3 scripts/gen_box_fixture.py` 로 픽스처를 다시 만들고 이쪽 표도 맞춘다."
    );
}

#[test]
fn every_bit_pattern_maps_to_exactly_one_char() {
    // 역방향(비트 → 문자)이 유일해야 합성이 결정적이다. 표에 같은 값이 둘 있으면
    // 어떤 문자가 나올지가 순회 순서에 달린다.
    let mut seen = std::collections::BTreeSet::new();
    for (ch, bits) in BOX_BITS {
        assert!(seen.insert(*bits), "비트 {bits:#06b} 가 중복이다({ch})");
    }
}
