//! 교차구현 적합성 — 트랜스크립트 폴더 이름을 서버와 같은 규칙으로 만드는가.
//!
//! 한 글자만 달라도 폴더를 못 찾고, 그 실패는 **빈 목록으로 조용히** 나타난다("이 패널엔
//! Claude 세션이 없다"와 구분되지 않는다). 픽스처는 파이썬 구현에서 뽑았다:
//! `python3 scripts/gen_transcript_fixture.py`

use std::collections::BTreeMap;

use claude::encode_project_dir;
use serde_json::Value;

#[test]
fn project_dir_encoding_matches_the_server() {
    let raw: Value =
        serde_json::from_str(include_str!("fixtures/project_dirs.json")).expect("픽스처를 읽을 수 없다");
    let dirs: BTreeMap<String, String> =
        serde_json::from_value(raw["dirs"].clone()).expect("dirs 표를 읽을 수 없다");
    assert!(!dirs.is_empty(), "픽스처가 비었다");
    for (cwd, expected) in dirs {
        assert_eq!(
            encode_project_dir(&cwd),
            expected,
            "cwd {cwd:?} 의 폴더 이름이 서버와 다르다"
        );
    }
}
