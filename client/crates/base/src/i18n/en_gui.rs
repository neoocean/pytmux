//! `gui` 문자열의 영어 번역(규약은 `en_core.rs` 머리말).
//!
//! GUI 뷰의 문구는 대부분 TUI 와 **같은 원문**이라 번역이 이미 `en_tui.rs` 에 있다
//! (중복 키 금지 — 첫 등장 파일에만 둔다). 여기에는 GUI 에만 있는 원문이 남는다.

pub static EN: &[(&str, &str)] = &[
    // 플러그인 표면(Tier A) — 아직 화면이 없는 기여를 눌렀을 때.
    (
        "플러그인 명령 {name} 은 이 클라에 아직 화면이 없습니다 — 터미널 클라(pytmux)에서 쓰세요",
        "Plugin command {name} has no screen in this client yet — use the terminal client (pytmux)",
    ),
    // ── root_view.rs — 블록 데모 창 제목 줄(TUI 데모와 같은 원문 — 여기가 주인) ──
    ("pytmux-gui · 블록 데모", "pytmux-gui · block demo"),
];
