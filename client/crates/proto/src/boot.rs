//! 서버가 없으면 띄운다 — **기동은 정본이 한다**.
//!
//! # 왜 여기서 직접 spawn 하지 않나
//!
//! "분리된 서버를 창 없이 띄우고 인증까지 기다린다"는 레시피는 이미 정본에 있다
//! (`pytmuxlib/launcher.py::run_start_server` → `spawn_server` → `proc.spawn_detached`).
//! 그 한 곳이 아는 것이 넷이다:
//!
//! - 창 없는 인터프리터 고르기(Windows `pythonw.exe` — 없으면 콘솔 창이 뜬다),
//! - 분리 플래그(`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` /
//!   POSIX `setsid`) — 이게 없으면 **클라를 닫을 때 서버가 같이 죽는다**,
//! - 소켓 디렉터리를 0o700 으로 미리 만들기(토큰이 사는 곳),
//! - 부팅 실패 사유를 `<sock>.boot.log` 로 회수하기(없으면 실패가 통째로 사라진다 —
//!   2026-07-28 원격 실측).
//!
//! 이 넷을 Rust 로 다시 적으면 **두 벌이 되고**, 갈리는 순간 증상은 "GUI 로 띄운 서버만
//! 이상하다"가 된다. 그래서 여기서 하는 일은 정본 런처를 **찾아서 부르는 것**뿐이다.
//!
//! # 살아 있는가는 붙어 보고 판정한다
//!
//! 소켓 파일·포트파일은 서버가 죽은 뒤에도 잠깐 남는다(루트 `CLAUDE.md` ⛔ — `kill-server`
//! 는 0.2초 지연 shutdown, 실측 1.5초 안에 사라진다). 그래서 [`attach_or_start`] 는 파일
//! 존재를 묻지 않고 **먼저 붙어 본다**. 실패 사유가 "서버가 없다" 부류일 때만 띄운다.

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use base::i18n::{t, tf};

use crate::client::AttachError;
use crate::link::ServerLink;

/// 정본 런처를 부르는 argv(하위명령 앞까지).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Launcher(pub Vec<String>);

impl Launcher {
    /// `start-server` 까지 붙인 완성 argv.
    pub fn start_server_argv(&self) -> Vec<String> {
        let mut argv = self.0.clone();
        argv.push("start-server".to_owned());
        argv
    }
}

/// 정본 런처를 어디서 찾았나. 필드를 **주입받는** 이유는 `endpoint::Rules` 와 같다 —
/// 파일시스템을 안 만지고 우선순위를 재려면 탐색과 판단이 갈려 있어야 한다.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Search {
    /// `$PYTMUX_BIN` — 명시 지정. 있으면 나머지는 안 본다.
    pub bin: Option<String>,
    /// PATH 에서 찾은 `pytmux` 런처(설치 스크립트가 만드는 얇은 래퍼).
    pub on_path: Option<PathBuf>,
    /// 저장소 트리의 `pytmux.py`(설치 없이 개발 중 실행하는 경우).
    pub script: Option<PathBuf>,
    /// 그 스크립트를 돌릴 인터프리터. 없으면 스크립트 후보는 못 쓴다.
    pub python: Option<String>,
}

/// 런처를 못 찾은 이유. **갈라 두는 이유**는 사용자가 할 일이 다르기 때문이다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Missing {
    /// `pytmux.py` 는 찾았는데 **그것을 돌릴 파이썬이 없다**.
    ///
    /// 서버는 파이썬이다(정본 `pytmuxlib`). 이 클라는 네이티브 이진이라 파이썬 없는
    /// 상자에도 설치되지만, **그 상자에서는 서버를 띄울 수 없다** — 우회로가 있는
    /// 문제가 아니라 제품의 경계다. 그래서 "못 찾았다"로 뭉뚱그리지 않고 이 갈래를
    /// 따로 둔다: 사용자가 할 일이 "pytmux 를 설치"가 아니라 "파이썬을 설치"다.
    Python { script: PathBuf },
    /// 설치본도 트리도 없다.
    Nothing { tried: String },
}

impl Search {
    /// 우선순위대로 하나를 고른다.
    ///
    /// 순서의 이유: 명시 지정(`$PYTMUX_BIN`) > 설치본(PATH) > 개발 트리. 개발 트리를
    /// 먼저 보면 **설치된 pytmux 를 쓰는 사용자가 우리 워크스페이스 옆의 낡은 트리로**
    /// 서버를 띄우게 된다 — 서버와 클라의 프로토콜이 조용히 갈리는 가장 싼 길이다.
    pub fn launcher(&self) -> Result<Launcher, Missing> {
        if let Some(bin) = self.bin.as_ref().filter(|s| !s.is_empty()) {
            return Ok(Launcher(vec![bin.clone()]));
        }
        if let Some(path) = &self.on_path {
            return Ok(Launcher(vec![path.display().to_string()]));
        }
        match (&self.script, &self.python) {
            (Some(script), Some(python)) => Ok(Launcher(vec![
                python.clone(),
                script.display().to_string(),
            ])),
            // 스크립트만 있고 인터프리터가 없으면 부를 길이 없다. "찾았다"고 말하면
            // 실패가 spawn 단계로 미뤄져 사유가 흐려진다.
            (Some(script), None) => Err(Missing::Python {
                script: script.clone(),
            }),
            _ => Err(Missing::Nothing {
                tried: self.tried(),
            }),
        }
    }

    /// 무엇을 봤는지 — 못 찾았을 때 사용자에게 보여 줄 목록.
    pub fn tried(&self) -> String {
        let mut out = vec!["$PYTMUX_BIN".to_owned(), t("PATH 의 pytmux").to_owned()];
        out.push(match &self.script {
            Some(p) => p.display().to_string(),
            None => t("트리의 pytmux.py").to_owned(),
        });
        out.join(", ")
    }

    /// 지금 이 프로세스의 환경에서 찾는다.
    pub fn discover() -> Self {
        let env = |name: &str| {
            std::env::var(name)
                .ok()
                .filter(|v| !v.is_empty())
        };
        let script = find_script();
        Self {
            bin: env("PYTMUX_BIN"),
            on_path: which("pytmux"),
            python: env("PYTMUX_PYTHON").or_else(find_python),
            script,
        }
    }
}

/// 실행 파일이 있는 자리부터 위로 걸어 `pytmux.py` 를 찾는다.
///
/// 개발 중에는 이진이 `client/target/{debug,release}/` 에, 배포 스냅샷은
/// `client/build/` 에 있다 — 둘 다 저장소 루트에서 두세 칸 아래다. 무한정 올라가지
/// 않는 이유: 위로 갈수록 **남의 트리의 `pytmux.py`** 를 집을 확률만 올라간다.
fn find_script() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let mut dir = exe.parent()?;
    for _ in 0..6 {
        let candidate = dir.join("pytmux.py");
        if candidate.is_file() {
            return Some(candidate);
        }
        dir = dir.parent()?;
    }
    None
}

/// 스크립트를 돌릴 인터프리터.
fn find_python() -> Option<String> {
    let names: &[&str] = if cfg!(windows) {
        &["python", "python3"]
    } else {
        &["python3", "python"]
    };
    names
        .iter()
        .find_map(|n| which(n))
        .map(|p| p.display().to_string())
}

/// PATH 에서 실행 파일 하나를 찾는다(Windows 는 PATHEXT 도 본다).
fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts: Vec<OsString> = if cfg!(windows) {
        let raw = std::env::var_os("PATHEXT")
            .unwrap_or_else(|| OsString::from(".COM;.EXE;.BAT;.CMD"));
        std::iter::once(OsString::new())
            .chain(
                raw.to_string_lossy()
                    .split(';')
                    .filter(|s| !s.is_empty())
                    .map(OsString::from)
                    .collect::<Vec<_>>(),
            )
            .collect()
    } else {
        vec![OsString::new()]
    };
    for dir in std::env::split_paths(&path) {
        for ext in &exts {
            let mut file = OsString::from(name);
            file.push(ext);
            let candidate = dir.join(&file);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

/// 서버를 띄우려다 생긴 일.
#[derive(Debug, thiserror::Error)]
pub enum BootError {
    /// 붙기 자체가 실패했다(서버는 있는데 못 붙는 부류 포함).
    #[error("{0}")]
    Attach(#[from] AttachError),
    /// 정본 런처를 못 찾았다 — 우리가 서버를 띄울 방법이 없다.
    #[error("{}", err_no_launcher(.0))]
    NoLauncher(Missing),
    /// 런처를 부르지 못했다(권한·실행 불가 등).
    #[error("{}", err_spawn(.argv, .source))]
    Spawn {
        argv: String,
        source: std::io::Error,
    },
    /// 런처가 실패를 보고했다. `detail` 은 **정본이 낸 사유**다(boot.log 를 읽은 그 줄).
    #[error("{}", err_start_failed(.detail))]
    StartFailed { detail: String },
    /// 런처가 시한 안에 안 끝났다.
    #[error("{}", err_start_timeout())]
    Timeout,
}

/// 못 띄운 이유 + **그 상자에 아직 남아 있는 길**.
///
/// 두 번째가 없으면 사용자는 막다른 길을 본다. 파이썬이 없는 상자에서도 이 클라는
/// 여전히 쓸모가 있다 — 이미 떠 있는 서버(다른 상자·다른 계정이 띄운 것)를 지목해
/// 붙을 수 있다. 그것을 말해 주는 것이 이 문장의 절반이다.
fn err_no_launcher(missing: &Missing) -> String {
    let hint = t("이미 떠 있는 서버가 있으면 `pytmux-gui --socket <엔드포인트>` 로 지목하고, \
그리기만 보려면 `pytmux-gui demo`");
    match missing {
        Missing::Python { script } => tf(
            "서버는 파이썬으로 돕니다 — 그 파이썬을 찾지 못했습니다 ({script}). \
파이썬을 설치하거나 $PYTMUX_PYTHON 으로 지목하세요. {hint}",
            &[("hint", hint), ("script", script.display().to_string().as_str())],
        ),
        Missing::Nothing { tried } => tf(
            "서버를 띄울 pytmux 를 찾지 못했습니다 (찾아본 곳: {tried}). {hint}",
            &[("hint", hint), ("tried", tried)],
        ),
    }
}

fn err_spawn(argv: &str, source: &std::io::Error) -> String {
    tf(
        "서버 기동을 실행하지 못했다: {source} ({argv})",
        &[("source", source.to_string().as_str()), ("argv", argv)],
    )
}

fn err_start_failed(detail: &str) -> String {
    if detail.is_empty() {
        return t("서버 기동 실패").to_owned();
    }
    tf("서버 기동 실패: {detail}", &[("detail", detail)])
}

fn err_start_timeout() -> String {
    t("서버 기동이 시한 안에 끝나지 않았다").to_owned()
}

/// 이 붙기 실패가 **"서버가 없다"** 는 뜻인가.
///
/// 여기서 갈리는 것이 제품의 성격이다: 없다는 뜻이면 우리가 띄우고, 아니면 띄우지
/// 않는다. 핸드셰이크 실패는 **서버가 대답을 했다**는 뜻이라 새로 띄우면 안 된다 —
/// 프로토콜이 안 맞는 서버 옆에 한 벌을 더 세우는 꼴이고, 사용자는 자기 탭이 사라진
/// 화면을 본다.
///
/// 토큰·포트파일을 못 읽은 경우는 "없다" 쪽이다. 정본의 `start-server` 가 그 자리를
/// 좀비로 판정해 새 서버로 교체하는 것과 같은 판단이다(`run_start_server`).
pub fn means_no_server(err: &AttachError) -> bool {
    match err {
        AttachError::NoServer(_)
        | AttachError::Connect { .. }
        | AttachError::NoPort(_)
        | AttachError::NoToken(_) => true,
        AttachError::Handshake(_) => false,
    }
}

/// 런처가 끝나기를 기다리는 상한.
///
/// 정본은 자기 안에서 인증까지 기다린다(로컬 예산 ≈4초). 그보다 넉넉히 두되 **무한은
/// 아니다** — 여기서 매달리면 창이 아직 없어서 사용자에게는 "아무 일도 안 일어난다"로
/// 보인다(그 침묵이 이 슬라이스가 없애려는 바로 그 증상이다).
const START_TIMEOUT: Duration = Duration::from_secs(30);
const POLL: Duration = Duration::from_millis(50);

/// 정본 런처로 서버를 띄운다. 돌아오면 서버는 **인증까지 통과한 상태**다(정본이 기다린다).
pub fn start_server() -> Result<(), BootError> {
    start_server_with(&Search::discover())
}

/// [`start_server`] 의 탐색 결과 주입판.
pub fn start_server_with(search: &Search) -> Result<(), BootError> {
    let launcher = search.launcher().map_err(BootError::NoLauncher)?;
    let argv = launcher.start_server_argv();
    let shown = argv.join(" ");
    let mut command = Command::new(&argv[0]);
    command
        .args(&argv[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    no_window(&mut command);
    let mut child = command.spawn().map_err(|source| BootError::Spawn {
        argv: shown.clone(),
        source,
    })?;

    let deadline = Instant::now() + START_TIMEOUT;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    return Err(BootError::Timeout);
                }
                std::thread::sleep(POLL);
            }
            Err(source) => {
                return Err(BootError::Spawn {
                    argv: shown,
                    source,
                });
            }
        }
    }
    let out = child.wait_with_output().map_err(|source| BootError::Spawn {
        argv: shown,
        source,
    })?;
    if out.status.success() {
        return Ok(());
    }
    // 정본은 실패 사유를 stderr 한 줄로 낸다(boot.log 에서 뽑은 것 — `server_boot_error`).
    // 그 줄이 여기서 사라지면 사용자는 "안 뜬다"만 보게 된다.
    Err(BootError::StartFailed {
        detail: last_line(&out.stderr).or_else(|| last_line(&out.stdout)).unwrap_or_default(),
    })
}

/// 진단으로 쓸 마지막 비어 있지 않은 줄.
fn last_line(bytes: &[u8]) -> Option<String> {
    String::from_utf8_lossy(bytes)
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .next_back()
        .map(|l| l.to_owned())
}

/// Windows 에서 **콘솔 창이 번쩍이지 않게** 한다.
///
/// 설치본 런처는 `.cmd` 래퍼라 `cmd.exe` 를 지난다 — 이 플래그가 없으면 GUI 를 띄울
/// 때마다 검은 창이 한 번 뜬다. 정본의 `proc.no_window_kwargs()` 와 같은 값이다.
#[cfg(windows)]
fn no_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn no_window(_command: &mut Command) {}

/// 붙는다. 서버가 없으면 **정본으로 띄우고 다시 붙는다**.
///
/// tmux 의 `attach` 가 서버를 띄우는 것과 같은 모델이고, 정본 클라도 같은 순서다
/// (`launcher.main` 의 `need_spawn`). GUI 만 데모로 떨어지면 사용자는 자기 세션이
/// 사라졌다고 읽는다.
pub fn attach_or_start(cols: u16, rows: u16) -> Result<ServerLink, BootError> {
    match ServerLink::attach(cols, rows) {
        Ok(link) => Ok(link),
        Err(e) if means_no_server(&e) => {
            start_server()?;
            Ok(ServerLink::attach(cols, rows)?)
        }
        Err(e) => Err(e.into()),
    }
}

#[cfg(test)]
#[path = "boot_tests.rs"]
mod tests;
