//! 크래시 자가 재기동 사슬(pytmux-639 G10) — **진짜 이진**을 `--crash-probe` 로 띄워 잰다.
//!
//! 정본 `run_client`: 크래시마다 `<state_base>.client.crash.log` 에 한 덩어리를 남기고, 30초 안의
//! 연쇄면 셈을 올려 같은 인자로 다시 뜬다. 셈이 5 를 넘으면 표준 오류에 포기 글을 내고 끝난다.
//! 그러니 첫 실행부터 **여섯 번 뜨고 여섯 덩어리**가 남고 포기 글은 **한 번**이다.
//!
//! 서버는 안 띄운다 — 손잡이는 붙기 전에 패닉한다. 그래도 상태 자리는 격리한다
//! (`PYTMUX_HOME`·`PYTMUX_CONFIG` — 루트 `CLAUDE.md` ⛔). 사슬의 프로세스들은 같은 표준 오류
//! 파이프를 물려받으므로 그 파이프가 닫힐 때(= 사슬 끝)까지 읽는다(상한 있음).

use std::io::Read as _;
use std::process::{Command, Stdio};
use std::time::Duration;

fn count_headers(dir: &std::path::Path) -> (usize, Vec<std::path::PathBuf>) {
    let mut total = 0;
    let mut files = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&d) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if path.to_string_lossy().ends_with(".client.crash.log") {
                let text = std::fs::read_to_string(&path).unwrap_or_default();
                total += text.matches("\n==== ").count();
                files.push(path);
            }
        }
    }
    (total, files)
}

#[test]
fn a_crashing_client_relaunches_five_times_then_gives_up_like_the_canon() {
    // AF_UNIX 경로 길이 때문에 짧은 자리를 쓴다(루트 `CLAUDE.md`).
    let home = std::path::PathBuf::from(format!("/tmp/pgc-crash-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&home);
    // 상태 디렉터리는 평소 서버가 만든다 — 정본 `_log_client_crash` 도 자리를 만들지 않으므로
    // (없으면 조용히 못 남긴다) 시험이 먼저 세운다.
    std::fs::create_dir_all(home.join("state")).unwrap();
    let config = home.join("config");
    std::fs::write(&config, "").unwrap();

    let mut child = Command::new(env!("CARGO_BIN_EXE_pytmux-gui"))
        .arg("--crash-probe")
        .env("PYTMUX_HOME", &home)
        .env("PYTMUX_CONFIG", &config)
        .env_remove(base::crash::ENV)
        .env("RUST_BACKTRACE", "0")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("이진을 못 띄웠다");
    let mut stderr = child.stderr.take().unwrap();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut text = String::new();
        let _ = stderr.read_to_string(&mut text);
        let _ = tx.send(text);
    });
    let text = rx
        .recv_timeout(Duration::from_secs(120))
        .expect("사슬이 120초 안에 안 끝났다 — 셈이 대를 못 넘어 무한 재기동 중일 수 있다");
    let _ = child.wait();

    let giveups = text.matches("자동복구를 멈춥니다").count();
    let (headers, files) = count_headers(&home);
    let _ = std::fs::remove_dir_all(&home);
    assert_eq!(giveups, 1, "포기 글이 한 번이 아니다:\n{text}");
    assert_eq!(files.len(), 1, "기록 파일이 하나가 아니다: {files:?}");
    assert_eq!(
        headers,
        1 + base::crash::MAX as usize,
        "크래시 기록 덩어리 수(= 뜬 횟수)가 정본(1 + 재기동 {})과 다르다:\n{text}",
        base::crash::MAX
    );
}
