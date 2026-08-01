//! 교차구현 적합성 — 두 클라가 패널 안 앱에게 **같은 바이트**를 주는가.
//!
//! 패스스루는 클라가 앱에게 직접 말을 거는 유일한 자리다. 한 바이트만 어긋나도 증상은
//! "그 앱만 마우스가 이상하다"로 보여 앱을 의심하게 되고, 추적을 안 켠 앱이라면 그 바이트가
//! **글자로 찍힌다**(Windows 에서 실제로 겪은 모양 — HANDOFF §10-H).
//!
//! 픽스처는 파이썬 구현에서 뽑았다: `python3 scripts/gen_mouse_fixture.py`
//! (출처 = `pytmuxlib/clientwidgets.py` 의 `MultiplexerView._encode_mouse`).

use proto::mouse::{MouseKind, MouseMode, encode};
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    cases: Vec<Case>,
}

#[derive(Deserialize)]
struct Case {
    name: String,
    pane: [u16; 4],
    sgr: bool,
    x: u16,
    y: u16,
    kind: String,
    button: u8,
    bytes_b64: String,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/mouse.json")).expect("픽스처를 읽을 수 없다")
}

/// 표준 base64 디코더. 픽스처를 읽으려고 크레이트를 하나 더 들이지 않는다.
fn b64_decode(text: &str) -> Vec<u8> {
    const TABLE: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut bits = 0u32;
    let mut have = 0u32;
    let mut out = Vec::new();
    for ch in text.bytes() {
        if ch == b'=' {
            break;
        }
        let Some(v) = TABLE.iter().position(|c| *c == ch) else {
            continue;
        };
        bits = (bits << 6) | v as u32;
        have += 6;
        if have >= 8 {
            have -= 8;
            out.push((bits >> have) as u8);
        }
    }
    out
}

fn kind_from_name(name: &str) -> MouseKind {
    match name {
        "press" => MouseKind::Press,
        "release" => MouseKind::Release,
        "drag" => MouseKind::Drag,
        "wheelup" => MouseKind::WheelUp,
        "wheeldown" => MouseKind::WheelDown,
        other => panic!("픽스처에 모르는 종류가 있다: {other}"),
    }
}

#[test]
fn every_encoded_event_matches_the_python_client() {
    let cases = fixture().cases;
    assert!(!cases.is_empty(), "픽스처가 비었다");

    let mut wrong = Vec::new();
    for case in &cases {
        let want = b64_decode(&case.bytes_b64);
        let got = encode(
            MouseMode {
                track: 2,
                sgr: case.sgr,
            },
            (case.pane[0], case.pane[1], case.pane[2], case.pane[3]),
            case.x,
            case.y,
            kind_from_name(&case.kind),
            case.button,
        );
        // 파이썬은 "패널 밖"을 빈 바이트열로, 우리는 `None` 으로 말한다. 둘은 같은 뜻이다 —
        // 보낼 것이 없다. 타입으로 구분해 두면 호출부가 빈 프레임을 보내는 실수를 못 한다.
        let got = got.unwrap_or_default();
        if got != want {
            wrong.push(format!(
                "{}: 파이썬 {:?} · 우리 {:?}",
                case.name, want, got
            ));
        }
    }
    assert!(
        wrong.is_empty(),
        "패널 안 앱에게 파이썬 클라와 다른 바이트를 보낸다:\n  {}\n\
         픽스처가 낡은 것이라면 python3 scripts/gen_mouse_fixture.py 로 다시 뽑을 것.",
        wrong.join("\n  ")
    );
}

/// 픽스처가 **실제로 무언가를 덮는지** 본다.
///
/// 전부 빈 결과인 픽스처도 위 테스트를 통과한다(우리도 전부 `None` 을 내면 그만이다).
/// 그 상태를 "적합성 있음"으로 읽으면 안 된다.
#[test]
fn the_fixture_covers_both_encodings_and_real_bytes() {
    let cases = fixture().cases;
    let nonempty = cases.iter().filter(|c| !c.bytes_b64.is_empty()).count();
    assert!(nonempty >= 20, "실제 바이트가 있는 경우가 {nonempty}개뿐이다");
    assert!(cases.iter().any(|c| c.sgr), "SGR 경우가 없다");
    assert!(cases.iter().any(|c| !c.sgr), "레거시 X10 경우가 없다");
    assert!(
        cases.iter().any(|c| c.bytes_b64.is_empty()),
        "패널 밖 경우가 없다 — 범위 판정이 대조되지 않는다"
    );
}
