//! 접속 수명 — **실 정본 서버**를 띄워 끊고 다시 붙는다(pytmux-585 관문 셋).
//!
//! ```sh
//! cd client && PYTMUX_CONFIG=<빈 파일> cargo test -p proto --test live_lifecycle -- --ignored
//! ```
//!
//! 평소 스위트에는 안 든다(`#[ignore]`) — 파이썬 서버·pty-host 를 띄우는 시험이라 합본
//! 게이트의 시간을 태운다. 같은 배선의 가짜 서버판은 `lifecycle_link.rs`(늘 돈다).
//!
//! # ⛔ 격리 규율(루트 CLAUDE.md 「프로세스 이름으로 일괄 kill 금지」)
//!
//! - 서버는 **스크래치 홈**(`/tmp/pytmux-live-<pid>-<n>` — AF_UNIX 경로 길이 때문에 짧게)에
//!   뜬다. 띄우기 **전에** 같은 환경으로 `ipc.default_state_dir()` 을 물어 그 답이 스크래치
//!   밑이 아니면 패닉한다 — 「무엇을 세웠나」가 아니라 「어디에 쓸 것인가」를 묻는다
//!   (`driver._assert_isolated` 와 같은 물음 · pytmux-508).
//! - 이 시험 프로세스는 환경을 **안 바꾼다**(`set_var` 없음) — 자식에게만 물려준다. 클라
//!   쪽은 소켓 경로를 **지목**해 붙으므로 자동 발견(=사용자의 라이브 서버)을 안 탄다.
//! - 정리는 `kill-server --yes`(서버가 죽었어도 그 홈의 pty-host 까지 회수) → 남은 것은
//!   **pid 파일의 pid 만**, 그것도 그 프로세스의 명령줄에 스크래치 경로가 있을 때만 죽인다.
//!   이름 매칭은 없다. 모든 대기에는 상한이 있다.

#![cfg(unix)]

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

use base::lifecycle::Ending;
use proto::ServerLink;
use proto::supervise::{Happening, Supervisor};

struct Live {
    dir: PathBuf,
    root: PathBuf,
    python: String,
}

impl Live {
    fn new(name: &str) -> Self {
        use std::sync::atomic::{AtomicU64, Ordering};
        static SEQ: AtomicU64 = AtomicU64::new(0);
        let dir = PathBuf::from(format!(
            "/tmp/pytmux-live-{name}-{}-{}",
            std::process::id(),
            SEQ.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("empty.config"), "").unwrap();
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..").canonicalize().unwrap();
        let python = std::env::var("PYTMUX_PYTHON").unwrap_or_else(|_| "python3".to_owned());
        let live = Self { dir, root, python };
        live.assert_isolated();
        live
    }

    fn sock(&self) -> PathBuf {
        self.dir.join("state").join("default.sock")
    }

    /// 자식에게만 물려주는 환경 — 이 프로세스의 환경은 안 건드린다.
    fn py(&self) -> Command {
        let mut cmd = Command::new(&self.python);
        cmd.current_dir(&self.root)
            .env("PYTMUX_HOME", &self.dir)
            .env("PYTMUX_CONFIG", self.dir.join("empty.config"))
            .env("PYTMUX_TOKENS_DB", self.dir.join("tokens.db"))
            .env_remove("PYTMUX")
            .env_remove("LC_PYTMUX")
            .env_remove("NO_COLOR");
        cmd
    }

    fn pytmux(&self, args: &[&str]) -> std::process::Output {
        let mut cmd = self.py();
        cmd.arg(self.root.join("pytmux.py")).arg("--socket").arg(self.sock()).args(args);
        cmd.output().expect("정본 런처를 못 띄웠다")
    }

    /// 「어디에 쓸 것인가」 — 정본이 이 환경에서 고를 상태 디렉터리가 스크래치 밑인가.
    fn assert_isolated(&self) {
        let out = self
            .py()
            .args(["-c", "from pytmuxlib import ipc; print(ipc.default_state_dir())"])
            .output()
            .expect("파이썬을 못 띄웠다");
        let state = String::from_utf8_lossy(&out.stdout).trim().to_owned();
        assert!(
            Path::new(&state).starts_with(&self.dir),
            "⛔ 격리 실패 — 정본이 스크래치 밖({state})에 쓸 것이다. 띄우지 않는다."
        );
    }

    fn start(&self) -> u32 {
        let out = self.pytmux(&["start-server"]);
        assert!(
            out.status.success(),
            "start-server 실패: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        self.server_pid().expect("서버 pid 파일이 없다")
    }

    fn read_pid(path: &Path) -> Option<u32> {
        std::fs::read_to_string(path).ok()?.trim().parse().ok()
    }

    fn server_pid(&self) -> Option<u32> {
        Self::read_pid(&self.dir.join("state").join("default.sock.server.pid"))
    }

    fn host_pid(&self) -> Option<u32> {
        Self::read_pid(&self.dir.join("state").join("default.sock.ptyhost.pid"))
    }

    /// 그 pid 가 살아 있고 **명령줄에 이 스크래치 경로가 있나** — 남의 프로세스를 안 죽이는 가드.
    fn ours(&self, pid: u32) -> bool {
        let out = Command::new("ps").args(["-o", "command=", "-p", &pid.to_string()]).output();
        out.is_ok_and(|o| String::from_utf8_lossy(&o.stdout).contains(&*self.dir.to_string_lossy()))
    }

    fn sigkill(&self, pid: u32) {
        assert!(self.ours(pid), "pid {pid} 는 이 시험의 것이 아니다 — 안 죽인다");
        unsafe {
            libc::kill(pid as i32, libc::SIGKILL);
        }
    }

    fn attach(&self) -> (ServerLink, Supervisor) {
        let link = ServerLink::attach_to(&self.sock().to_string_lossy(), 80, 24).expect("못 붙었다");
        let sup = Supervisor::for_link(&link);
        (link, sup)
    }
}

impl Drop for Live {
    fn drop(&mut self) {
        let pids: Vec<u32> = self.server_pid().into_iter().chain(self.host_pid()).collect();
        let _ = self.pytmux(&["kill-server", "--yes"]);
        let deadline = Instant::now() + Duration::from_secs(5);
        while pids.iter().any(|p| self.ours(*p)) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(50));
        }
        for pid in pids {
            if self.ours(pid) {
                unsafe {
                    libc::kill(pid as i32, libc::SIGKILL);
                }
            }
        }
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn pump_until(
    sup: &mut Supervisor,
    link: &mut ServerLink,
    limit: Duration,
    mut done: impl FnMut(&Happening) -> bool,
) -> Vec<Happening> {
    let deadline = Instant::now() + limit;
    let mut all = Vec::new();
    loop {
        let got = sup.pump(link, (80, 24));
        let hit = got.iter().any(&mut done);
        all.extend(got);
        if hit {
            return all;
        }
        assert!(Instant::now() < deadline, "시한({limit:?}) 안에 안 왔다: {all:?}");
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn is_layout(h: &Happening) -> bool {
    matches!(h, Happening::Message(m) if m.kind() == "layout")
}

#[test]
#[ignore = "라이브: 실 정본 서버를 띄운다 — cargo test -p proto --test live_lifecycle -- --ignored"]
fn restart_server_is_followed_by_an_automatic_reattach() {
    let live = Live::new("restart");
    let pid = live.start();
    let (mut link, mut sup) = live.attach();
    pump_until(&mut sup, &mut link, Duration::from_secs(10), is_layout);
    let out = live.pytmux(&["cmd", "restart-server"]);
    assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "restarting", "{out:?}");
    let all = pump_until(&mut sup, &mut link, Duration::from_secs(20), |h| {
        matches!(h, Happening::Notice(t) if t == base::lifecycle::restart_done())
    });
    assert!(
        all.iter().any(|h| matches!(h, Happening::Notice(t) if t == base::lifecycle::server_restarting())),
        "restarting 통지를 못 받았다: {all:?}"
    );
    if !all.iter().any(is_layout) {
        pump_until(&mut sup, &mut link, Duration::from_secs(10), is_layout);
    }
    assert!(sup.ended().is_none());
    // POSIX execv 면 같은 pid, host 모드면 후속 서버(새 pid) — 어느 쪽이든 **이 홈의** 서버가 산다.
    let now = live.server_pid().expect("재시작 뒤 서버 pid 파일이 없다");
    assert!(live.ours(now), "재시작 뒤 서버(pid {now})가 이 홈의 것이 아니다");
    eprintln!("restart: 서버 pid {pid} → {now}");
}

#[test]
#[ignore = "라이브: 실 정본 서버를 띄운다 — cargo test -p proto --test live_lifecycle -- --ignored"]
fn kill_server_ends_with_the_terminated_line() {
    let live = Live::new("kill");
    live.start();
    let (mut link, mut sup) = live.attach();
    pump_until(&mut sup, &mut link, Duration::from_secs(10), is_layout);
    let out = live.pytmux(&["kill-server", "--yes"]);
    assert!(out.status.success(), "{out:?}");
    let want = Ending::Terminated.message();
    pump_until(&mut sup, &mut link, Duration::from_secs(10), |h| {
        matches!(h, Happening::Ended(t) if *t == want)
    });
    assert_eq!(sup.ended(), Some(&Ending::Terminated));
    assert!(!sup.attempting(), "bye 뒤에 다시 붙으려 했다");
}

#[test]
#[ignore = "라이브: 실 정본 서버를 띄운다 — cargo test -p proto --test live_lifecycle -- --ignored"]
fn a_server_killed_by_pid_ends_as_server_lost_after_trying() {
    let live = Live::new("sigkill");
    let pid = live.start();
    let (mut link, mut sup) = live.attach();
    pump_until(&mut sup, &mut link, Duration::from_secs(10), is_layout);
    live.sigkill(pid);
    let want = Ending::Lost.message();
    let all = pump_until(&mut sup, &mut link, Duration::from_secs(15), |h| {
        matches!(h, Happening::Ended(t) if *t == want)
    });
    // 다시 붙어 봤다 — 드롭 예산 끝의 마지막 사유가 남는다(Closed 둘: 읽기 끝 · 시도 실패).
    let closed = all.iter().filter(|h| matches!(h, Happening::Closed(_))).count();
    assert!(closed >= 2, "재접속 시도 없이 끝났다: {all:?}");
    assert_eq!(sup.ended(), Some(&Ending::Lost));
}
