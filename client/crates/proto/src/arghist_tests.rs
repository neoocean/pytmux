use super::*;

#[test]
fn record_keeps_mru_order_and_the_python_file_shape() {
    let dir = std::env::temp_dir().join(format!("pytmux-arghist-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("default.sock.arghist.json");
    let mut hist = ArgHist::load(path.clone());
    hist.record("remote-host", "box1");
    hist.record("remote-host", "box2");
    hist.record("remote-host", "box1"); // 같은 값은 맨 앞으로
    hist.record("layout-name", "dev");
    assert_eq!(hist.recent("remote-host"), vec!["box1", "box2"]);
    // 파일 모양이 파이썬과 같다 — {버킷: [최근이 앞]}. 다르면 두 클라가 서로의
    // 이력을 못 읽는다(.lang 과 같은 계약).
    let raw: std::collections::HashMap<String, Vec<String>> =
        serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    assert_eq!(raw["remote-host"], vec!["box1", "box2"]);
    // 다시 읽어도 같다(왕복).
    let again = ArgHist::load(path.clone());
    assert_eq!(again.recent("layout-name"), vec!["dev"]);
    // 빈 값·공백은 기록하지 않는다.
    let mut hist = again;
    hist.record("remote-host", "   ");
    assert_eq!(hist.recent("remote-host").len(), 2);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn the_bucket_table_matches_the_python_command_arghist() {
    use base::screens::Prompt;
    // 파이썬 COMMAND_ARGHIST 의 네 버킷이 우리 물음에 그대로 옮겨져 있다.
    assert_eq!(bucket(Prompt::RemoteAttach), Some("remote-host"));
    assert_eq!(bucket(Prompt::RemoteDetach), Some("remote-host"));
    assert_eq!(bucket(Prompt::SaveTabLayout), Some("layout-name"));
    assert_eq!(bucket(Prompt::LoadTabLayoutNew), Some("layout-name"));
    assert_eq!(bucket(Prompt::RunShell), Some("run-shell"));
    assert_eq!(bucket(Prompt::SendKeys), Some("send-keys"));
    // 이력이 뜻 없는 물음(예: 탭 이름 바꾸기 — 현재 이름이 seed 다)은 버킷이 없다.
    assert_eq!(bucket(Prompt::RenameTab), None);
}

#[test]
fn a_broken_file_starts_empty_instead_of_failing() {
    let dir = std::env::temp_dir().join(format!("pytmux-arghist-bad-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("default.sock.arghist.json");
    std::fs::write(&path, "{깨진 json").unwrap();
    let hist = ArgHist::load(path);
    assert!(hist.recent("remote-host").is_empty());
    let _ = std::fs::remove_dir_all(&dir);
}
