//! 감시자 — 진짜 파일시스템에서 트랜스크립트를 찾고 따라가는가.
//!
//! 순수 파서(`lib_tests.rs`)와 달리 여기서는 폴더·수정시각·세션 교체 같은 **파일시스템
//! 쪽 규칙**을 본다. 임시 폴더를 직접 만들어 쓴다(외부 크레이트 없이).

use std::fs;
use std::path::PathBuf;

use claude::discover::Watcher;
use claude::{ItemKind, ToolState, encode_project_dir};

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("pytmux-claude-test-{name}"));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn write(path: &PathBuf, lines: &[&str]) {
    fs::write(path, lines.join("\n")).unwrap();
}

const PROMPT: &str = r#"{"type":"user","message":{"role":"user","content":"첫 질문"}}"#;
const TOOL: &str = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ls"}}]}}"#;
const RESULT: &str = r#"{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"ok"}]}}"#;

#[test]
fn finds_the_transcript_for_a_working_directory() {
    let root = scratch("find");
    let cwd = "/work/proj";
    let dir = root.join(encode_project_dir(cwd));
    fs::create_dir_all(&dir).unwrap();
    write(&dir.join("session.jsonl"), &[PROMPT]);

    let mut watcher = Watcher::new(Some(root));
    assert!(watcher.set_cwd(cwd));
    assert!(watcher.refresh(), "첫 읽기는 변화다");
    assert_eq!(watcher.items().len(), 1);
    assert!(matches!(watcher.items()[0].kind, ItemKind::Prompt));
}

#[test]
fn an_unknown_directory_is_simply_empty() {
    // Claude 를 안 쓰는 패널에서는 아무 일도 일어나지 않아야 한다.
    let mut watcher = Watcher::new(Some(scratch("unknown")));
    watcher.set_cwd("/nowhere");
    assert!(!watcher.refresh());
    assert!(watcher.items().is_empty());
    assert!(watcher.path().is_none());
}

#[test]
fn unchanged_file_is_not_reparsed() {
    let root = scratch("unchanged");
    let cwd = "/work/proj";
    let dir = root.join(encode_project_dir(cwd));
    fs::create_dir_all(&dir).unwrap();
    write(&dir.join("s.jsonl"), &[PROMPT]);

    let mut watcher = Watcher::new(Some(root));
    watcher.set_cwd(cwd);
    assert!(watcher.refresh());
    assert!(
        !watcher.refresh(),
        "안 바뀐 파일을 매 프레임 다시 파싱하면 루프가 그것만 한다"
    );
}

#[test]
fn appended_lines_show_up_and_resolve_the_running_tool() {
    let root = scratch("append");
    let cwd = "/work/proj";
    let dir = root.join(encode_project_dir(cwd));
    fs::create_dir_all(&dir).unwrap();
    let file = dir.join("s.jsonl");
    write(&file, &[PROMPT, TOOL]);

    let mut watcher = Watcher::new(Some(root));
    watcher.set_cwd(cwd);
    watcher.refresh();
    assert_eq!(watcher.items()[1].state(), Some(ToolState::Running));

    write(&file, &[PROMPT, TOOL, RESULT]);
    assert!(watcher.refresh(), "덧쓴 줄이 보여야 한다");
    assert_eq!(watcher.items()[1].state(), Some(ToolState::Ok));
}

#[test]
fn a_newer_session_file_takes_over() {
    // `claude --resume` 는 같은 폴더에 새 파일을 만든다. 뷰가 끝난 세션에 머물면 안 된다.
    let root = scratch("resume");
    let cwd = "/work/proj";
    let dir = root.join(encode_project_dir(cwd));
    fs::create_dir_all(&dir).unwrap();
    write(&dir.join("old.jsonl"), &[PROMPT]);

    let mut watcher = Watcher::new(Some(root.clone()));
    watcher.set_cwd(cwd);
    watcher.refresh();
    assert!(watcher.path().unwrap().ends_with("old.jsonl"));

    let newer = dir.join("new.jsonl");
    write(&newer, &[PROMPT, TOOL]);
    // 수정 시각이 같은 초일 수 있으므로 명시적으로 미래로 민다.
    //
    // **쓰기로 열어야 한다.** POSIX 는 읽기 핸들로도 시각을 바꿔 주지만 Windows 는
    // `FILE_WRITE_ATTRIBUTES` 가 없으면 거절한다(`os error 5, Access is denied`).
    // `File::open` 은 읽기 전용이라 이 줄이 Windows 박스에서만 터졌다.
    let future = std::time::SystemTime::now() + std::time::Duration::from_secs(5);
    fs::OpenOptions::new()
        .write(true)
        .open(&newer)
        .unwrap()
        .set_times(fs::FileTimes::new().set_modified(future))
        .unwrap();

    assert!(watcher.refresh(), "새 세션으로 갈아타야 한다");
    assert!(watcher.path().unwrap().ends_with("new.jsonl"));
    assert_eq!(watcher.items().len(), 2);
}

// ─────────────────────────────────────────────────────────────────────────────
// 이어 읽기 — 비용이 **파일 크기**가 아니라 **덧붙은 크기**에 비례하는가
// ─────────────────────────────────────────────────────────────────────────────
// 이 감시자는 30Hz 이벤트 루프 위에서 돈다. 활성 세션에서는 수정 시각 가드가 거의 매번
// 열리므로, 그때마다 전체를 다시 파싱하면 비용이 파일 크기에 비례한다(실측: 23MB
// 트랜스크립트 재파싱 = 디버그 116ms · 릴리스 8.8ms). 시간으로 재면 머신마다 흔들리므로
// **읽은 바이트**로 못박는다.

/// 항목을 만들지 않는 큰 이벤트 — 실제 트랜스크립트의 부피가 여기서 온다(툴 결과·thinking).
fn filler(n: usize) -> String {
    format!(
        r#"{{"type":"file-history-snapshot","blob":"{}"}}"#,
        "x".repeat(n)
    )
}

#[test]
fn appending_one_line_does_not_reread_the_whole_file() {
    let root = scratch("incremental");
    let cwd = "/work/proj";
    let dir = root.join(encode_project_dir(cwd));
    fs::create_dir_all(&dir).unwrap();
    let file = dir.join("s.jsonl");
    let bulk: Vec<String> = (0..40).map(|_| filler(25_000)).collect();
    let mut lines: Vec<&str> = bulk.iter().map(String::as_str).collect();
    lines.insert(0, PROMPT);
    write(&file, &lines);
    let size = fs::metadata(&file).unwrap().len();
    assert!(size > 900_000, "표본이 충분히 커야 차이가 보인다: {size}");

    let mut watcher = Watcher::new(Some(root));
    watcher.set_cwd(cwd);
    assert!(watcher.refresh());
    let after_first = watcher.bytes_read();
    assert!(after_first >= size, "첫 읽기는 통째로다: {after_first} < {size}");

    // 한 줄 덧쓴다(= 대화가 한 걸음 나아갔다).
    lines.push(TOOL);
    write(&file, &lines);
    assert!(watcher.refresh(), "덧붙은 줄이 보여야 한다");
    let delta = watcher.bytes_read() - after_first;
    assert!(
        delta < 4_096,
        "한 줄 덧붙었는데 {delta} 바이트를 읽었다 — 파일을 통째로 다시 읽고 있다 \
         (30Hz 루프에서 파일 크기에 비례하는 비용)"
    );
    assert_eq!(watcher.items().len(), 2);
}

#[test]
fn a_half_written_last_line_is_not_lost() {
    // 쓰는 중인 파일의 마지막 줄은 반쪽일 수 있다. 그걸 소비한 것으로 치면 **완성된 뒤
    // 다시 안 읽어** 그 이벤트를 영영 잃는다(이어 읽기가 만든 새 위험 — 통째로 다시
    // 읽던 동안에는 다음 번에 저절로 복구됐다).
    let root = scratch("partial");
    let cwd = "/work/proj";
    let dir = root.join(encode_project_dir(cwd));
    fs::create_dir_all(&dir).unwrap();
    let file = dir.join("s.jsonl");

    fs::write(&file, format!("{PROMPT}\n{}", &TOOL[..30])).unwrap();
    let mut watcher = Watcher::new(Some(root));
    watcher.set_cwd(cwd);
    watcher.refresh();
    assert_eq!(watcher.items().len(), 1, "반쪽 줄은 아직 항목이 아니다");

    fs::write(&file, format!("{PROMPT}\n{TOOL}\n")).unwrap();
    assert!(watcher.refresh(), "완성된 줄이 보여야 한다");
    assert_eq!(watcher.items().len(), 2, "반쪽이었던 줄을 잃어버렸다");
}

#[test]
fn a_rewritten_tail_forces_a_full_reread() {
    // 이어 읽기는 "파일이 뒤로만 자란다"는 가정 위에 선다. 이미 읽은 자리가 다시 쓰이면
    // 이어 붙인 내용이 **조용히** 어긋난다 — 크기가 같이 자라 버리면 크기 비교로는
    // 절대 못 잡는다. 앵커(이어 붙일 지점 직전 바이트)가 그 자리를 지킨다.
    let root = scratch("rewrite");
    let cwd = "/work/proj";
    let dir = root.join(encode_project_dir(cwd));
    fs::create_dir_all(&dir).unwrap();
    let file = dir.join("s.jsonl");
    write(&file, &[PROMPT, TOOL]);

    let mut watcher = Watcher::new(Some(root));
    watcher.set_cwd(cwd);
    watcher.refresh();
    assert_eq!(watcher.items()[1].title, "ls");

    // 마지막 레코드를 **같은 길이의 다른 내용**으로 갈아 끼우고 한 줄 덧쓴다.
    let rewritten = TOOL.replace(r#""command":"ls""#, r#""command":"lt""#);
    assert_eq!(rewritten.len(), TOOL.len(), "표본은 같은 길이여야 크기 비교가 무력해진다");
    write(&file, &[PROMPT, &rewritten, RESULT]);
    assert!(watcher.refresh());
    assert_eq!(
        watcher.items()[1].title,
        "lt",
        "이미 읽은 자리가 다시 쓰였는데 옛 내용을 들고 있다 — 앵커 검사가 없다"
    );
    assert_eq!(watcher.items().len(), 2);
    assert_eq!(watcher.items()[1].state(), Some(ToolState::Ok));
}

#[test]
fn without_a_root_nothing_happens() {
    // $HOME 도 $CLAUDE_CONFIG_DIR 도 없는 환경(서비스 계정 등)에서 죽지 않는다.
    let mut watcher = Watcher::new(None);
    assert!(watcher.set_cwd("/work/proj"));
    assert!(!watcher.refresh());
    assert!(watcher.items().is_empty());
}

#[test]
fn transcript_root_follows_the_config_dir_then_the_home() {
    use claude::discover::projects_dir_in;

    // $CLAUDE_CONFIG_DIR 이 있으면 그 아래 projects/.
    assert_eq!(
        projects_dir_in(Some(PathBuf::from("/cfg/claude")), Some(PathBuf::from("/home/me"))),
        Some(PathBuf::from("/cfg/claude/projects"))
    );
    // 없으면 ~/.claude/projects.
    assert_eq!(
        projects_dir_in(None, Some(PathBuf::from("/home/me"))),
        Some(PathBuf::from("/home/me/.claude/projects"))
    );
    // 홈도 모르면 이 기능은 그냥 꺼진다(죽지 않는다).
    assert_eq!(projects_dir_in(None, None), None);
}
