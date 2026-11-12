//! 입력 소스 판정 적합성(pytmux-590) — 정본 `ime-indicator/oskbd.py` 의 `is_korean`·
//! `parse_fcitx5`·`parse_ibus` 가 쓴 답지(`scripts/gen_ime_source.py` →
//! `fixtures/ime_source.json`)에 대고 `base::ime_source` 를 잰다.
//!
//! OS 에 묻는 길은 두 클라가 각자다(정본 ctypes · GUI `extern "C"`/`Command`). 같아야 하는
//! 것은 **받은 글을 무슨 뜻으로 읽나**이고, 그것이 여기서 갈리면 같은 입력기에서 한쪽만
//! `[한]` 을 띄운다.

use base::ime_source::{is_korean, parse_fcitx5, parse_ibus};

#[derive(serde::Deserialize)]
struct Fixture {
    is_korean: Vec<Korean>,
    fcitx5: Vec<Parsed>,
    ibus: Vec<Parsed>,
}

#[derive(serde::Deserialize)]
struct Korean {
    id: Option<String>,
    korean: bool,
}

#[derive(serde::Deserialize)]
struct Parsed {
    out: String,
    id: Option<String>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/ime_source.json")).expect("ime_source.json")
}

#[test]
fn source_ids_read_the_same_as_the_python_canonical() {
    let fx = fixture();
    assert!(!fx.is_korean.is_empty(), "답지가 비었다");
    for case in &fx.is_korean {
        assert_eq!(is_korean(case.id.as_deref()), case.korean, "{:?}", case.id);
    }
}

#[test]
fn linux_input_method_outputs_parse_the_same() {
    let fx = fixture();
    for case in &fx.fcitx5 {
        assert_eq!(parse_fcitx5(&case.out), case.id, "fcitx5 {:?}", case.out);
    }
    for case in &fx.ibus {
        assert_eq!(parse_ibus(&case.out), case.id, "ibus {:?}", case.out);
    }
}
