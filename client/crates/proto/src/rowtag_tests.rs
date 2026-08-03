use super::*;

#[derive(serde::Deserialize)]
struct Fx {
    tags: std::collections::BTreeMap<String, String>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("../tests/fixtures/row_tags.json"))
        .expect("row_tags.json 을 못 읽는다")
}

fn hex(c: Color) -> String {
    match c {
        Color::Rgb { r, g, b } => format!("#{r:02x}{g:02x}{b:02x}"),
        other => panic!("이 표는 RGB 만 낸다: {other:?}"),
    }
}

#[test]
fn the_fixture_actually_measured_something() {
    // ★ 이 오라클이 먼저다. 픽스처가 비면 아래 단언들이 "빈 것 == 빈 것"이 되어
    //   **무엇을 해도 통과한다**(이 저장소가 여러 번 밟은 공허함).
    let fx = fixture();
    assert!(fx.tags.len() >= 10, "뽑힌 태그가 너무 적다: {}", fx.tags.len());
    assert_eq!(fx.tags.get("dir").map(String::as_str), Some("#ff5555"));
}

#[test]
fn every_tag_resolves_to_the_colour_the_canon_uses() {
    // 제보가 못박은 것이 이 자리다: *"컬러 스킴 일치가 특히 중요하다."*
    // 손으로 옮긴 값이 한 칸만 달라도 그 줄만 조용히 다른 색이 된다.
    let fx = fixture();
    let mut wrong = Vec::new();
    for (tag, want) in &fx.tags {
        match color(tag) {
            Some(got) if hex(got) == *want => {}
            Some(got) => wrong.push(format!("{tag}: 우리 {} · 정본 {want}", hex(got))),
            None => wrong.push(format!("{tag}: 우리가 모르는 이름(정본에는 있다)")),
        }
    }
    assert!(
        wrong.is_empty(),
        "정본과 다른 색:\n  {}\n  python3 scripts/gen_row_tags.py 로 다시 뽑았는지 볼 것.",
        wrong.join("\n  ")
    );
}

#[test]
fn we_do_not_know_names_the_canon_never_uses() {
    // 반대쪽도 잰다 — 고아 이름은 죽은 무게이면서 다음 사람을 속인다(정본이 그 이름을
    // 지웠는데 우리 표에 남아 있으면, 그 색을 쓰는 줄이 영영 안 온다).
    let fx = fixture();
    let orphan: Vec<&str> =
        KNOWN.iter().copied().filter(|n| !fx.tags.contains_key(*n)).collect();
    assert!(orphan.is_empty(), "정본이 안 쓰는 이름이 표에 남아 있다: {orphan:?}");
    let mut sorted = KNOWN.to_vec();
    sorted.sort_unstable();
    assert_eq!(KNOWN, &sorted[..], "KNOWN 은 이름순이라야 한다");
}

#[test]
fn an_unknown_name_is_not_painted_instead_of_guessed() {
    // 모르는 이름에 아무 색이나 주면 **틀린 뜻**이 화면에 뜬다 — 안 칠하는 편이 낫다
    // (그 줄은 기본색으로 읽히고, 전수 오라클이 그 이름을 곧 잡는다).
    assert!(color("no-such-tag").is_none());
    assert!(color("").is_none());
}
