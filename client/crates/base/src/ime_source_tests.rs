use super::*;

#[test]
fn apple_and_third_party_korean_sources_read_as_korean() {
    assert!(is_korean(Some("com.apple.inputmethod.Korean.2SetKorean")));
    assert!(is_korean(Some("org.youknowone.inputmethod.Gureum.han2")));
    assert!(!is_korean(Some("com.apple.keylayout.ABC")));
    assert!(!is_korean(None));
}

#[test]
fn linux_input_method_outputs_become_synthetic_ids() {
    assert_eq!(parse_fcitx5("hangul\n").as_deref(), Some("fcitx5:hangul"));
    assert_eq!(parse_fcitx5("\n  \n"), None, "빈 출력은 «모름»이다");
    assert_eq!(parse_ibus("xkb:us::eng\n").as_deref(), Some("ibus:xkb:us::eng"));
    assert!(is_korean(parse_fcitx5("hangul").as_deref()));
    assert!(!is_korean(parse_fcitx5("keyboard-us").as_deref()));
}

#[test]
fn unknown_is_no_badge_not_english() {
    // 모르는 것을 영문이라고 단정하지 않는다 — None 이면 낱말도 없다.
    assert_eq!(badge_word(None), None);
    assert_eq!(badge_word(Some("com.apple.keylayout.ABC")), Some("EN"));
    assert_eq!(badge_word(Some("ibus:hangul")), Some("한"));
}
