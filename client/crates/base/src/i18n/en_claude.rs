//! `claude` 문자열의 영어 번역(규약은 `en_core.rs` 머리말).
//!
//! 그 크레이트는 core 에 의존하지 않던 최하층이었지만, i18n 때문에 core 의존이
//! 생겼다(`t()` 하나 때문이다 — 계층 방향은 여전히 core ← claude 로 문제없다).

pub static EN: &[(&str, &str)] = &[
    // ── lib.rs — Item::name / plan_item / summarize_tool(Write) ──
    ("플랜", "Plan"),
    // 플랜 제목(첫 줄+분량)과 Write 요약(경로+분량)이 같은 모양이라 키 하나로 묶인다.
    ("{text}  {n}줄", "{text}  {n} lines"),
    // ── source.rs — detail_lines(전용 화면) ──
    ("플랜 [{state}]", "Plan [{state}]"),
    ("막힌 호출", "Denied call"),
    ("사유: {reason}", "Reason: {reason}"),
];
