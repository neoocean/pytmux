use super::*;

#[derive(serde::Deserialize)]
struct Fx {
    levels: Vec<String>,
    warn_at: u32,
    crit_at: u32,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("../tests/fixtures/pct_levels.json"))
        .expect("pct_levels.json 을 못 읽는다")
}

#[test]
fn the_fixture_actually_measured_something() {
    // ★ 이 오라클이 먼저다 — 픽스처가 비면 아래 단언들이 "빈 것 == 빈 것"이 되어
    //   무엇을 해도 통과한다(이 저장소가 여러 번 밟은 공허함).
    let fx = fixture();
    assert!(fx.levels.len() >= 3, "등급이 너무 적다: {:?}", fx.levels);
    assert!(fx.warn_at < fx.crit_at, "눈금이 뒤집혔다: {} {}", fx.warn_at, fx.crit_at);
}

#[test]
fn every_level_the_canon_sends_is_known_here() {
    // ⛔ 모르는 이름이 오면 그 칸은 **조용히 기본색**으로 뜬다 — 예외도 로그도 없다
    //    (pytmux-16 이 그 부류였다). 정본에 등급이 늘면 여기서 운다.
    let fx = fixture();
    let unknown: Vec<&str> =
        fx.levels.iter().map(String::as_str).filter(|n| level(n).is_none()).collect();
    assert!(
        unknown.is_empty(),
        "정본이 내는데 우리가 모르는 등급: {unknown:?}\n  \
         `celltag::level` 과 `KNOWN` 에 한 줄씩 더하고 뷰의 색도 정할 것."
    );
}

#[test]
fn we_do_not_know_names_the_canon_never_uses() {
    // 반대쪽 — 고아 이름은 죽은 무게이면서 다음 사람을 속인다(정본이 지운 이름을
    // 우리가 알고 있으면, 그 색을 쓰는 칸이 영영 안 온다).
    let fx = fixture();
    let orphan: Vec<&str> =
        KNOWN.iter().copied().filter(|n| !fx.levels.iter().any(|l| l == n)).collect();
    assert!(orphan.is_empty(), "정본이 안 쓰는 등급이 표에 남아 있다: {orphan:?}");
    let mut sorted = KNOWN.to_vec();
    sorted.sort_unstable();
    assert_eq!(KNOWN, &sorted[..], "KNOWN 은 이름순이라야 한다");
}

#[test]
fn the_levels_are_ordered_the_way_the_canon_orders_them() {
    // 픽스처의 차례는 **낮은 것부터**다. 이 열거의 `Ord` 가 그 차례를 지지 않으면
    // 「더 위험한 쪽」을 대소로 고르는 자리가 조용히 뒤집힌다.
    let fx = fixture();
    let got: Vec<Level> = fx.levels.iter().map(|n| level(n).expect("모르는 등급")).collect();
    let mut want = got.clone();
    want.sort();
    assert_eq!(got, want, "등급의 대소가 정본 차례와 다르다: {:?}", fx.levels);
}

#[test]
fn an_unknown_name_is_not_guessed_into_a_level() {
    // 모르는 이름에 아무 등급이나 주면 **틀린 뜻**이 화면에 뜬다 — 안 칠하는 편이 낫다.
    assert!(level("").is_none());
    assert!(level("warning").is_none(), "정본 Textual 테마의 어휘와 섞지 않는다");
    assert!(level("red").is_none(), "색 이름은 이 표의 어휘가 아니다");
}
