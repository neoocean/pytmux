//! 적합성 — **Claude Code 마스코트가 쓰는 글자가 전부 블록 표 안인가**(pytmux-177).
//!
//! # 왜 이 오라클이 있나
//!
//! pytmux-177 은 "GUI 에서 마스코트가 알아볼 수 없는 색 블록으로 깨진다" 였고, 가설은
//! "[`BLOCK_FILLS`] 표 밖 문자라 폴백 글꼴로 그려진다" 였다. 표를 사분면까지 넓혀
//! 고쳤지만, **마스코트가 실제로 어떤 코드포인트를 쓰는지는 아무도 안 쟀다** — 즉
//! 표를 넓힌 것이 그 결함을 덮는지가 추측이었다(이슈가 "닫기 전에 그 한 번을 재라"고
//! 남긴 자리가 그것이다).
//!
//! 그래서 도는 `claude` 의 시작 화면을 pty 로 떠서 그 격자를 코드포인트로 덤프했다.
//! 나온 것이 `▐▛███▛█` / `▝▜██████▀` / `▝▝ ▝▝` — 곧 **U+2580·U+2588·U+2590** 셋은
//! 원래 표에 있었고 **U+259B·U+259C·U+259D** 셋은 없었다. 스무 칸 중 여덟 칸이 폴백
//! 글꼴로 갔다는 뜻이고, 제보의 "행마다 가로로 밀린다" 와 정확히 맞는다.
//!
//! 여기서 재는 것은 **그 대응이 지금도 성립하나** 하나다: 마스코트의 글자 중 하나라도
//! 표에서 빠지면 그 칸은 다시 폴백으로 가고 그림은 다시 깨진다.
//!
//! # 픽스처를 다시 뜨는 법 (⛔ 생성기가 없다)
//!
//! 이 픽스처만은 `gen_*.py` 가 없다 — 값이 **도는 claude 의 화면**이라 정본 모듈을
//! import 해서 뽑을 수가 없다. `claude` 가 마스코트를 바꾸면 사람이 이렇게 다시 뜬다:
//!
//! ```text
//! (sleep 10; printf '/exit\r'; sleep 3) \
//!   | TERM=xterm-256color COLUMNS=100 LINES=45 script -q ty.bin claude
//! ```
//!
//! 그 캡처에서 `CSI n G`·CR·LF 만 해석해 행으로 편 뒤 마스코트 칸만 남긴다.
//! ⚠ 오른쪽의 버전·요금제·**작업 디렉터리 경로**는 담지 않는다 — 이 크레이트는 공개
//! 미러로 나간다.
//!
//! ⛔ 이 오라클을 "표에 사분면이 있나" 로 좁혀 적지 마라. 그러면 `the_quadrants_are_
//! blocks_too` 와 같은 말을 두 번 하는 것이고, **마스코트가 다른 범위(braille·팔분면)로
//! 옮겨 가는 날** 둘 다 초록인 채로 그림만 깨진다. 재는 것은 언제나 «캡처한 그 글자» 다.

use proto::canvas::block_fills;
use serde::Deserialize;

#[derive(Deserialize)]
struct Fx {
    rows: Vec<String>,
    codepoints: Vec<String>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/claude_mascot.json"))
        .expect("픽스처를 읽을 수 없다")
}

/// 마스코트 칸에 실제로 놓이는 글자들(빈칸 제외).
fn mascot_chars(fx: &Fx) -> Vec<char> {
    let mut v: Vec<char> = fx.rows.iter().flat_map(|r| r.chars()).filter(|c| *c != ' ').collect();
    v.sort_unstable();
    v.dedup();
    v
}

#[test]
fn every_mascot_char_is_drawn_by_us_not_by_the_font() {
    // 하나라도 빠지면 그 칸만 폴백 글꼴로 가고, 폴백의 진폭이 칸너비의 정수배가 아니면
    // **그 행부터 통째로 밀린다** — 그것이 pytmux-55 이고 pytmux-177 이다.
    for ch in mascot_chars(&fixture()) {
        assert!(
            block_fills(ch).is_some(),
            "마스코트가 쓰는 U+{:04X} `{ch}` 가 BLOCK_FILLS 에 없다 — 그 칸은 폴백 글꼴로 그려져 행이 밀린다",
            ch as u32
        );
    }
}

#[test]
fn the_recorded_codepoints_match_the_recorded_rows() {
    // 두 칸이 갈리면 어느 쪽이 참인지 모른다 — 사람이 픽스처를 손으로 고칠 때의 함정이다.
    let fx = fixture();
    let from_rows: Vec<String> = mascot_chars(&fx)
        .iter()
        .map(|c| format!("U+{:04X}", *c as u32))
        .collect();
    let mut want = fx.codepoints.clone();
    want.sort();
    assert_eq!(from_rows, want, "픽스처의 rows 와 codepoints 가 서로 다르다");
}

#[test]
fn the_mascot_really_needed_the_quadrants() {
    // pytmux-177 의 답을 못박는다: 고치기 **전** 표(U+2580~U+2595)만으로는 모자랐다.
    // ⚠ 이것이 깨지면 「사분면을 넣은 것이 이 결함의 처방이었다」는 이력이 틀린 것이다.
    let fx = fixture();
    let outside: Vec<char> = mascot_chars(&fx).into_iter().filter(|c| *c > '\u{2595}').collect();
    assert!(
        !outside.is_empty(),
        "마스코트가 옛 표(U+2580~U+2595) 안에만 있다 — 그러면 pytmux-177 의 원인은 다른 자리다"
    );
    for ch in outside {
        assert!(
            ('\u{2596}'..='\u{259F}').contains(&ch),
            "마스코트가 사분면 밖(U+{:04X})까지 쓴다 — 표를 그 범위까지 넓혀야 한다",
            ch as u32
        );
    }
}
