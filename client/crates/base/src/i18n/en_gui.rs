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
    // ── session_view.rs — 글자 배율 한 마디(§10-21ⓐ) ──
    // ⚠ 끝값 둘을 **문장 통째로** 둔다. 방향("키울 수"/"줄일 수")을 인자로 넘기면 그
    //   낱말만 한국어로 남는다(2026-08-02p 에서 배운 자리 — 사유를 줄에 이어 붙이지
    //   않는다). 줄이 둘로 늘어도 그쪽이 옳다.
    ("글자 크기: {scale}×", "Text size: {scale}×"),
    ("글자 크기: {scale}× — 더 키울 수 없다", "Text size: {scale}× — cannot go larger"),
    ("글자 크기: {scale}× — 더 줄일 수 없다", "Text size: {scale}× — cannot go smaller"),
    // 트리 판이 개요를 기다리는 동안의 한 줄(§10-21ⓖ2 — 프레임 오라클이 잡았다).
    ("개요를 기다리는 중…", "Waiting for the overview…"),
    ("첫 화면을 기다리는 중…", "Waiting for the first frame…"),
    ("맞는 명령이 없다", "No matching command"),
    ("아직 알림이 없다", "No notices yet"),
    ("버퍼가 없다", "No buffers"),
    ("(탭 없음)", "(no tabs)"),
    // §10-21ⓓ3 — 재시작 점검 판의 단추.
    ("지금 재시작 (restart-all)", "Restart now (restart-all)"),
];
