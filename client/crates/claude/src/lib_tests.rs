//! 트랜스크립트 → 블록 뷰 모델 오라클.
//!
//! 픽스처(`tests/fixtures/session.jsonl`)는 **실제 트랜스크립트와 같은 모양**으로 손수
//! 만든 것이다. 사람 데이터를 저장소에 넣지 않으려고 합성했지만, 키 구성(assistant/user
//! 이벤트 · `message.content` 의 thinking/text/tool_use/tool_result)은 실물에서 확인한
//! 그대로다.

use super::*;

const SESSION: &str = include_str!("../tests/fixtures/session.jsonl");

fn parsed() -> Transcript {
    Transcript::parse(SESSION)
}

fn titles(t: &Transcript) -> Vec<String> {
    t.items().iter().map(|i| i.title.clone()).collect()
}

#[test]
fn reads_the_conversation_as_a_list_of_blocks() {
    let t = parsed();
    let kinds: Vec<&ItemKind> = t.items().iter().map(|i| &i.kind).collect();
    assert!(matches!(kinds[0], ItemKind::Prompt), "{kinds:?}");
    assert_eq!(titles(&t)[0], "테스트를 돌리고 실패를 고쳐 줘");
    assert!(matches!(kinds[1], ItemKind::Answer));
    assert_eq!(titles(&t)[1], "먼저 스위트를 돌려 보겠습니다.", "첫 줄만 싣는다");
}

#[test]
fn thinking_is_not_a_block() {
    // 생각은 분량이 커서 목록을 통째로 먹는다.
    assert!(
        !titles(&parsed()).iter().any(|t| t.contains("길고 긴 생각")),
        "thinking 이 목록에 실렸다"
    );
}

#[test]
fn meta_user_events_are_not_prompts() {
    // 훅·시스템 알림이 사용자 턴으로 들어온다 — 사람이 친 것처럼 보이면 안 된다.
    assert!(!titles(&parsed()).iter().any(|t| t.contains("사람이 친 것이 아니다")));
}

#[test]
fn tool_calls_carry_what_they_did_not_just_their_name() {
    let t = parsed();
    let bash = t
        .items()
        .iter()
        .find(|i| matches!(&i.kind, ItemKind::Tool { name, .. } if name == "Bash"))
        .expect("Bash 호출이 없다");
    assert_eq!(bash.title, "cargo test --workspace");
}

#[test]
fn a_failed_tool_result_is_not_shown_as_success() {
    let t = parsed();
    let bash = t.items().iter().find(|i| i.title == "cargo test --workspace").unwrap();
    assert_eq!(bash.state(), Some(ToolState::Failed));
    assert_eq!(bash.state().unwrap().badge(), "err");
}

#[test]
fn a_tool_call_without_a_result_stays_running() {
    // 결과가 안 온 호출을 성공으로 칠하면 끝나지 않은 일을 끝났다고 읽게 된다.
    let t = parsed();
    let last = t.items().last().unwrap();
    assert_eq!(last.state(), Some(ToolState::Running));
    assert_eq!(last.state().unwrap().badge(), "···");
}

#[test]
fn edits_show_how_much_changed() {
    let t = parsed();
    let edit = t
        .items()
        .iter()
        .find(|i| matches!(&i.kind, ItemKind::Tool { name, .. } if name == "Edit"))
        .expect("Edit 호출이 없다");
    assert_eq!(edit.title, "/work/proj/src/lib.rs  -3/+4");
    assert_eq!(edit.state(), Some(ToolState::Ok));
}

#[test]
fn a_denied_tool_is_not_shown_as_a_failure() {
    // 막힌 것과 깨진 것은 사용자가 할 일이 정반대다. 실측 코퍼스의 거부 100건이 전부
    // `is_error: true` 였으므로, 그 플래그만 보면 둘이 한 색으로 뭉친다.
    let t = parsed();
    let denied = t
        .items()
        .iter()
        .find(|i| i.title == "rm -rf /work/proj")
        .expect("거부된 Bash 가 없다");
    assert_eq!(denied.state(), Some(ToolState::Denied));
    assert_eq!(denied.state().unwrap().badge(), "no");
    assert_eq!(
        denied.detail.as_deref(),
        Some("[Irreversible] 되돌릴 수 없는 삭제다"),
        "사유는 `Reason:` 뒤가 온다"
    );
}

#[test]
fn a_user_rejection_is_a_denial_too() {
    // 자동 분류기 거부와 사람이 고른 거부는 문구가 다르고, 결과 `content` 모양도 다르다
    // (실측: 문자열 91,009 · 배열 4,781). 배열 쪽을 못 읽으면 이 거부가 성공으로 샌다.
    let t = parsed();
    let denied = t
        .items()
        .iter()
        .find(|i| i.title == "/work/proj/notes.md  1줄")
        .expect("거부된 Write 가 없다");
    assert_eq!(denied.state(), Some(ToolState::Denied));
    assert!(denied.detail.as_deref().unwrap().starts_with("The user doesn't want"));
}

#[test]
fn quoting_the_denial_phrase_does_not_make_a_denial() {
    // 거부 문구는 **툴 출력 안에** 실려 올 수 있다(이 저장소의 grep 결과가 실제로 그렇다).
    // 포함 검사로 판정하면 그 출력이 통째로 거부로 뒤집힌다 — 접두로만 본다.
    let t = parsed();
    let grep = t
        .items()
        .iter()
        .find(|i| matches!(&i.kind, ItemKind::Tool { name, .. } if name == "Grep"))
        .expect("Grep 호출이 없다");
    assert_eq!(grep.state(), Some(ToolState::Ok), "인용은 거부가 아니다");
    assert_eq!(grep.detail, None);
}

#[test]
fn a_plan_is_its_own_kind_and_keeps_the_whole_text() {
    // 플랜은 승인/거부를 고르는 자리라 툴 호출 한 줄로 흘려보내면 안 된다.
    let t = parsed();
    let plan = t.last_plan().expect("플랜이 없다");
    assert!(matches!(plan.kind, ItemKind::Plan { .. }));
    assert_eq!(plan.title, "1. 실패하는 테스트를 고친다  3줄", "목록에는 첫 줄 + 분량");
    assert_eq!(
        plan.detail.as_deref().map(|d| d.lines().count()),
        Some(3),
        "전문은 detail 로 간다(요약 구역은 이걸 안 그린다)"
    );
    assert_eq!(plan.state(), Some(ToolState::Ok), "승인된 플랜");
}

#[test]
fn the_plan_body_is_bounded_and_cannot_carry_escapes() {
    let long: String = (0..MAX_BODY_LINES * 3)
        .map(|i| format!("\u{1b}]0;pwned\u{7} 줄 {i}"))
        .collect::<Vec<_>>()
        .join("\n");
    let line = serde_json::json!({
        "type": "assistant",
        "message": {"content": [{"type":"tool_use","id":"p","name":PLAN_TOOL,"input":{"plan":long}}]}
    });
    let t = Transcript::parse(&line.to_string());
    let body = t.last_plan().unwrap().detail.clone().unwrap();
    assert_eq!(body.lines().count(), MAX_BODY_LINES + 1, "잘림 표식 한 줄 포함");
    assert!(!body.contains('\u{1b}') && !body.contains('\u{7}'), "{body:?}");
}

#[test]
fn the_permission_mode_is_the_latest_one_seen() {
    // 모드는 항목이 아니라 지금 상태다. 첫 값에 머물면 화면이 옛 모드를 보여 준다.
    let t = parsed();
    assert_eq!(t.mode(), Some("plan"));
    // `type:"mode"`(값 normal)는 다른 축이다 — 이걸 권한 모드로 읽으면 안 된다.
    assert_ne!(t.mode(), Some("normal"));
}

#[test]
fn an_unknown_permission_mode_is_passed_through() {
    // 모드 이름은 Claude Code 쪽 사정으로 는다. 아는 값만 통과시키면 모르는 모드에서
    // 화면이 조용히 옛 모드를 보여 준다.
    let t = Transcript::parse(r#"{"type":"permission-mode","permissionMode":"brandNew"}"#);
    assert_eq!(t.mode(), Some("brandNew"));
}

#[test]
fn a_half_written_last_line_does_not_lose_the_rest() {
    // 트랜스크립트는 **지금 쓰이는 중인 파일**이다. 마지막 줄이 잘려 있는 것은 정상이다.
    let text = format!("{SESSION}{{\"type\":\"assist");
    let t = Transcript::parse(&text);
    assert_eq!(t.items().len(), parsed().items().len(), "온전한 앞부분은 살아야 한다");
}

#[test]
fn unknown_tools_still_say_something() {
    // 한 줄이어야 한다 — JSONL 은 **줄 단위**다(예쁘게 편 JSON 은 트랜스크립트가 아니다).
    let t = Transcript::parse(
        r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"x","name":"BrandNewTool","input":{"target":"어딘가"}}]}}"#,
    );
    assert_eq!(t.items()[0].title, "어딘가", "표에 없는 툴도 뭔가는 보여 준다");
}

#[test]
fn control_characters_cannot_ride_into_the_view() {
    // 툴 결과·프롬프트에는 실제로 ANSI 가 들어 있다. 그대로 그리면 단말 주입이 된다.
    let t = Transcript::parse(
        "{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"text\",\
         \"text\":\"\\u001b]0;pwned\\u0007 진짜 답\"}]}}",
    );
    let title = &t.items()[0].title;
    assert!(!title.contains('\u{1b}') && !title.contains('\u{7}'), "{title:?}");
    assert!(title.contains("진짜 답"));
}

#[test]
fn long_text_is_clipped() {
    let long = "가".repeat(MAX_TEXT * 3);
    let line = serde_json::json!({"type":"user","message":{"role":"user","content":long}});
    let t = Transcript::parse(&line.to_string());
    assert_eq!(t.items()[0].title.chars().count(), MAX_TEXT + 1, "잘린 표식 … 포함");
}

#[test]
fn the_item_list_is_bounded() {
    // 트랜스크립트는 계속 자란다. 상한 없는 목록은 이 저장소가 이미 물린 부류다.
    let one = r#"{"type":"user","message":{"role":"user","content":"x"}}"#;
    let many = std::iter::repeat_n(one, MAX_ITEMS * 2).collect::<Vec<_>>().join("\n");
    let t = Transcript::parse(&many);
    assert_eq!(t.items().len(), MAX_ITEMS);
}

#[test]
fn tail_gives_the_most_recent_items_in_order() {
    let t = parsed();
    let tail = t.tail(2);
    assert_eq!(tail.len(), 2);
    assert_eq!(tail[1], *t.items().last().unwrap());
}

#[test]
fn an_empty_or_junk_transcript_is_simply_empty() {
    assert!(Transcript::parse("").is_empty());
    assert!(Transcript::parse("not json\n{}\n[]").is_empty());
}
