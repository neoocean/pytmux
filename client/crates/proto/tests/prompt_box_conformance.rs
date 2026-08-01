//! 교차구현 적합성 — 입력박스 긁기가 **파이썬 정본과 글자 하나까지 같은가**.
//!
//! 기대값은 손으로 적은 것이 아니다. `scripts/gen_prompt_box_fixture.py` 가
//! `pytmuxlib/plugins/claude-code/claude.py::claude_input_box` 를 **직접 호출해** 만든다.
//!
//! # 왜 이 대조가 유일한 방어인가
//!
//! 이 파서는 Claude UI 의 모양에 매달린 휴리스틱이고, 주석마다 **실제 결함에서 온 예외**가
//! 붙어 있다. 틀려도 아무 소리가 안 난다 — 증상은 "작성창에 이상한 게 딸려 온다"뿐이고
//! (제보가 여섯 번 있던 자리다), 그중 절반은 **한 칸 밀림**처럼 눈으로도 잘 안 보인다.
//!
//! 정본이 UI 변화를 따라 고쳐지면 픽스처를 다시 뽑고 여기가 운다.

use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    cases: Vec<Case>,
}

#[derive(Deserialize)]
struct Case {
    name: String,
    lines: Vec<String>,
    wrap: Vec<usize>,
    cursor: Option<usize>,
    /// `null` = 긁을 수 없다 · `""` = 박스가 실제로 빔 · 그 외 = 그 글.
    expected: Option<String>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/prompt_box.json"))
        .expect("픽스처를 읽을 수 없다 — scripts/gen_prompt_box_fixture.py 로 다시 뽑을 것")
}

#[test]
fn every_case_matches_the_python_canonical() {
    let cases = fixture().cases;
    assert!(!cases.is_empty(), "픽스처가 비었다 — 빈 결과는 통과가 아니다");

    let mut wrong: Vec<String> = Vec::new();
    for case in &cases {
        let got = proto::prompt_box::input_text(
            &case.lines,
            &case.wrap,
            case.cursor,
        );
        if got != case.expected {
            wrong.push(format!(
                "{}\n     정본: {:?}\n     우리: {:?}",
                case.name, case.expected, got
            ));
        }
    }

    assert!(
        wrong.is_empty(),
        "정본과 다른 결과가 나온 사례 {}개:\n  - {}",
        wrong.len(),
        wrong.join("\n  - ")
    );
}

/// 픽스처가 **세 가지 결과를 다 담고 있는지** 본다.
///
/// 한 종류만 들어 있으면 위 대조는 그 종류만 지킨다. 특히 `null`(긁기 불가)과 `""`(빈
/// 박스)는 호출부에서 **다른 일**을 하므로, 둘 다 표본에 있어야 그 구분이 지켜진다.
#[test]
fn the_fixture_covers_all_three_kinds_of_outcome() {
    let cases = fixture().cases;
    let none = cases.iter().filter(|c| c.expected.is_none()).count();
    let empty = cases
        .iter()
        .filter(|c| c.expected.as_deref() == Some(""))
        .count();
    let text = cases
        .iter()
        .filter(|c| c.expected.as_deref().is_some_and(|t| !t.is_empty()))
        .count();
    assert!(none > 0, "'긁을 수 없다' 표본이 없다");
    assert!(empty > 0, "'빈 입력칸' 표본이 없다");
    assert!(text > 0, "'글이 있다' 표본이 없다");
}

/// 여러 줄 사례가 있는지 — 이 파서가 가장 많이 틀린 자리다.
#[test]
fn the_fixture_has_a_multiline_case() {
    let cases = fixture().cases;
    assert!(
        cases
            .iter()
            .any(|c| c.expected.as_deref().is_some_and(|t| t.contains('\n'))),
        "여러 줄 사례가 없다 — 한 줄만 맞으면 이 파서는 반쯤만 지켜진다"
    );
}
