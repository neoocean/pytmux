//! 적합성 — **두 칸이라 부르는 글자**가 정본과 같은가(폭 사전 전수).
//!
//! # 왜 표본이 아니라 전수인가
//!
//! `compose::char_cells` 는 파이썬 `cellwidth.char_cells` 와 **글자 하나까지 같아야**
//! 한다(그 모듈 머리말). 갈리면 그 줄이 통째로 밀린다. 그런데 종전에 그 계약을 재던
//! 것은 `conformance.rs` 의 표본 60개뿐이었고 — **표본에 없는 글자는 아무도 안 쟀다.**
//!
//! 실측(2026-09-01 · pytmux-407 ⓐ 작업 중 픽스처가 잡았다): 지역 지시자 `🇰` 가
//! 파이썬 `wcwidth` 로 **2**, 러스트 `unicode-width` 로 **1** 이었다. 국기가 든 줄은
//! 그래서 두 칸씩 어긋난다. 두 언어가 서로 다른 사전을 쓰므로(그리고 사전 판이 따로
//! 오르므로) 이 갈림은 **다시 생긴다** — 그래서 구간 전수로 못 박는다.
//!
//! 픽스처: `scripts/gen_char_widths.py` → `fixtures/char_widths.json`.

use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    sweep: Vec<Vec<u32>>,
    wide: Vec<Vec<u32>>,
    /// wide 모드(`ambiguous-width wide`)에서 훑은 자리와 정본이 두 칸이라 부르는 구간(pytmux-578).
    wide_sweep: Vec<Vec<u32>>,
    wide_ambig: Vec<Vec<u32>>,
    /// 정본 wide 모드가 한 칸 → 두 칸으로 **올리는** 글자(= 모호폭 갈래만 · 유니코드 전체).
    widened: Vec<Vec<u32>>,
}

/// 범위 목록 이분 탐색(구간은 정렬·서로소).
fn in_ranges(ranges: &[Vec<u32>], o: u32) -> bool {
    ranges
        .binary_search_by(|r| {
            if r[1] < o {
                std::cmp::Ordering::Less
            } else if r[0] > o {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/char_widths.json"))
        .expect("char_widths.json 을 못 읽는다")
}

/// ⛔ **아직 갈려 있는 자리** — 사전 «판»이 달라서 남은 것들.
///
/// 정본은 파이썬 `wcwidth` 를 쓰고 이쪽은 `unicode-width` 를 쓴다. 둘은 서로 다른 속도로
/// 유니코드 판을 좇으므로, **새로 배정된 이모지**는 한쪽만 아는 기간이 생긴다. 아래는
/// 2026-09-01 실측으로 그 기간에 걸린 일곱이다(전부 이쪽이 1, 정본이 2).
///
/// ⚠ 이 목록은 **면제가 아니라 래칫**이다: 여기 적힌 것이 «이제 안 갈린다»가 되면 그것도
/// 실패다(아래 시험이 그것을 문다). 새 갈림이 생기면 목록에 없으므로 바로 붉어진다.
/// 근본 처방(사전 판 맞추기)은 pytmux-407 코멘트에 적어 트래커가 진다.
const KNOWN_DIVERGENCES: &[u32] =
    &[0x1F6D8, 0x1FA8A, 0x1FA8E, 0x1FAC8, 0x1FACD, 0x1FAEA, 0x1FAEF];

#[test]
fn the_fixture_actually_measured_something() {
    let fx = fixture();
    assert!(fx.wide.len() >= 50, "넓은 구간이 너무 적다: {}", fx.wide.len());
    let total: u32 = fx.sweep.iter().map(|r| r[1] - r[0] + 1).sum();
    assert!(total > 8_000, "훑은 범위가 너무 좁다: {total}");
}

#[test]
fn every_character_is_as_wide_here_as_the_canon_says() {
    let fx = fixture();
    let is_wide = |o: u32| fx.wide.iter().any(|r| (r[0]..=r[1]).contains(&o));
    let mut wrong: Vec<String> = Vec::new();
    let mut wrong_total = 0usize;
    let mut checked = 0usize;
    for range in &fx.sweep {
        for o in range[0]..=range[1] {
            let Some(ch) = char::from_u32(o) else { continue };
            checked += 1;
            // 모호폭은 **좁은 모드**로 견준다(픽스처도 그렇게 뽑았다).
            let got = proto::compose::char_cells_in(ch, false);
            let want = if is_wide(o) { 2 } else { 1 };
            if got != want && !KNOWN_DIVERGENCES.contains(&o) {
                wrong_total += 1;
                if wrong.len() < 40 {
                    wrong.push(format!("U+{o:04X}: 우리 {got} · 정본 {want}"));
                }
            }
        }
    }
    assert!(checked > 8_000, "훑은 글자가 너무 적다: {checked}");
    assert!(
        wrong.is_empty(),
        "폭이 정본과 갈렸다({wrong_total}개 · 앞 40개까지):\n  {}\n  \
         `python3 scripts/gen_char_widths.py` 로 다시 뽑았는지 보고, 그래도 갈리면 \
         `compose::char_cells_in` 이 그 구간을 정본에 맞춰야 한다(사전이 서로 다르다).",
        wrong.join("\n  ")
    );
}

#[test]
fn the_known_divergences_are_still_divergent() {
    // ⛔ 래칫의 반대쪽 — 사전이 따라잡아 갈림이 사라졌으면 **목록에서 빼야** 한다.
    //    안 그러면 이 목록이 「언젠가 맞았던 면제」로 남아 다음 갈림을 조용히 삼킨다.
    let fx = fixture();
    let is_wide = |o: u32| fx.wide.iter().any(|r| (r[0]..=r[1]).contains(&o));
    let mut healed = Vec::new();
    for o in KNOWN_DIVERGENCES {
        let Some(ch) = char::from_u32(*o) else {
            healed.push(format!("U+{o:04X}: 이제 글자가 아니다"));
            continue;
        };
        let want = if is_wide(*o) { 2 } else { 1 };
        if proto::compose::char_cells_in(ch, false) == want {
            healed.push(format!("U+{o:04X}"));
        }
    }
    assert!(
        healed.is_empty(),
        "이제 안 갈리는 자리가 목록에 남아 있다 — `KNOWN_DIVERGENCES` 에서 뺄 것: {healed:?}"
    );
}

/// wide 모드가 **무엇을 넓히나**(pytmux-578) — 유니코드 전체. 종전에는 «설정이 켜면 양쪽 다 같은
/// 규칙으로 넓힌다»는 가정만 있었고 아무도 안 쟀다 — 재 보니 선·블록(U+2500–259F)을 이쪽만 두
/// 칸으로, 라틴-1 모호폭 247자를 이쪽만 한 칸으로 셌다(`width_cjk` 를 쓴 탓 · 지금은 정본 표를 싣는다).
///
/// 「넓히는 글자」만 맞댄다 — 좁은 모드의 폭 사전 갈림(이모지 새 판 등)은 위 시험과
/// `KNOWN_DIVERGENCES` 의 몫이고, 훑기 밖 사전 갈림은 이 이슈 밖이다.
#[test]
fn wide_mode_widens_exactly_what_the_canon_widens() {
    let fx = fixture();
    let mut wrong: Vec<String> = Vec::new();
    let mut wrong_total = 0usize;
    for range in &fx.wide_sweep {
        for o in range[0]..=range[1] {
            let Some(ch) = char::from_u32(o) else { continue };
            let ours = proto::compose::char_cells_in(ch, false) == 1
                && proto::compose::char_cells_in(ch, true) == 2;
            let canon = in_ranges(&fx.widened, o);
            if ours != canon {
                wrong_total += 1;
                if wrong.len() < 40 {
                    wrong.push(format!("U+{o:04X} {ch}: 우리 넓힘={ours} · 정본 {canon}"));
                }
            }
        }
    }
    assert!(fx.widened.len() > 100, "넓히는 구간이 너무 적다: {}", fx.widened.len());
    assert!(
        wrong.is_empty(),
        "wide 모드가 넓히는 글자가 정본과 갈렸다({wrong_total}개 · 앞 40개까지):\n  {}\n  \
         `python3 scripts/gen_char_widths.py --rust > crates/proto/src/ambig_table.rs` 로 표를 다시 찍을 것.",
        wrong.join("\n  ")
    );
}

/// wide 모드의 **폭 전체**(pytmux-578) — 좁은 모드 폭이 이미 맞다고 잰 자리(위 훑기)와 모호폭이
/// 사는 라틴-1·그리스·키릴에서.
#[test]
fn every_character_is_as_wide_in_wide_mode_as_the_canon_says() {
    let fx = fixture();
    let mut ranges: Vec<Vec<u32>> = fx.sweep.clone();
    ranges.push(vec![0x00A0, 0x04FF]);
    let mut wrong: Vec<String> = Vec::new();
    let mut wrong_total = 0usize;
    let mut checked = 0usize;
    for range in &ranges {
        for o in range[0]..=range[1] {
            let Some(ch) = char::from_u32(o) else { continue };
            if KNOWN_DIVERGENCES.contains(&o) {
                continue;
            }
            checked += 1;
            let got = proto::compose::char_cells_in(ch, true);
            let want = if in_ranges(&fx.wide_ambig, o) { 2 } else { 1 };
            if got != want {
                wrong_total += 1;
                if wrong.len() < 40 {
                    wrong.push(format!("U+{o:04X} {ch}: 우리 {got} · 정본 {want}"));
                }
            }
        }
    }
    assert!(checked > 8_000, "훑은 글자가 너무 적다: {checked}");
    assert!(
        wrong.is_empty(),
        "wide 모드 폭이 정본과 갈렸다({wrong_total}개 · 앞 40개까지):\n  {}",
        wrong.join("\n  ")
    );
}

/// 픽스처의 wide 구간이 선·블록을 **안** 넓힌다는 것을 이름으로 못 박는다(위 시험이 그 자리를
/// 전수로 재지만, 무엇을 지키는지 읽는 사람에게 한 줄로 보이게).
#[test]
fn box_drawing_stays_one_cell_in_wide_mode() {
    for ch in ['─', '│', '┌', '┼', '▀', '█', '░'] {
        assert_eq!(proto::compose::char_cells_in(ch, true), 1, "{ch}");
    }
    assert_eq!(proto::compose::char_cells_in('·', true), 2);
    assert_eq!(proto::compose::char_cells_in('°', true), 2);
}
