//! 서버를 어디서 찾는가.
//!
//! pytmux 의 `pytmuxlib/ipc.py` 와 **같은 규칙**을 쓴다. 규칙이 어긋나면 증상이 "연결
//! 실패"가 아니라 **클라가 서버를 못 찾아 새 서버를 띄우는 것**이다 — 사용자는 자기 탭이
//! 사라진 화면을 본다. 그래서 규칙은 추측하지 않고 서버 구현에서 뽑아 픽스처로 못박는다
//! (`scripts/gen_endpoint_fixture.py` → `tests/endpoint_conformance.rs`).
//!
//! # OS 별로 규칙이 통째로 다르다
//!
//! | | Unix | Windows |
//! |---|---|---|
//! | 전송 | AF_UNIX 소켓 | **루프백 TCP**(asyncio 의 AF_UNIX 지원이 불완전) |
//! | 위치 | `$XDG_RUNTIME_DIR/pytmux` 또는 `/tmp/pytmux-<uid>` | `%LOCALAPPDATA%\pytmux` |
//! | 포트 | — | 서버가 에페메럴 포트를 잡고 `default.port` 에 게시 |
//! | 토큰 | 있으면 쓴다(0600 소켓이 1차 방어) | **필수**(같은 머신의 다른 사용자도 루프백에 붙을 수 있다) |
//!
//! Unix 에서 후보가 둘인 이유: `XDG_RUNTIME_DIR` 유무가 세션마다 갈린다(데스크톱/systemd
//! 로그인은 있고 단순 ssh 로그인은 없어 `/tmp` 폴백). 서버를 띄운 세션과 붙는 세션이
//! 어긋나면 같은 서버를 못 찾는다. Windows 는 `%LOCALAPPDATA%` 가 안정적이라 후보가 하나다.
//!
//! # 어느 OS 규칙이든 어느 호스트에서나 테스트된다
//!
//! [`Rules`] 가 환경(변수·uid·OS)을 **주입받는다**. macOS 개발 상자에서 Windows 규칙을
//! 검증할 수 있어야 하기 때문이다 — 안 그러면 Windows 박스에 가서야 틀린 걸 안다.

use std::path::{Path, PathBuf};

/// 서버에 닿는 방법 하나.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Endpoint {
    /// AF_UNIX 소켓(Unix).
    Unix {
        path: PathBuf,
        /// 인증 토큰 파일(`<소켓>.token`). 없을 수 있다 — 0600 소켓이 1차 방어다.
        token: PathBuf,
    },
    /// 루프백 TCP(Windows). 포트는 서버가 잡아 포트파일에 게시한다.
    Tcp {
        host: String,
        /// 0 이면 "포트파일을 읽어라"는 뜻이다(서버 규약).
        port: u16,
        portfile: PathBuf,
        /// 인증 토큰 파일. **루프백 TCP 에서는 필수** — 같은 머신의 다른 로컬
        /// 사용자도 접속할 수 있어 토큰이 유일한 경계다.
        token: PathBuf,
    },
}

impl Endpoint {
    /// 토큰 파일 경로.
    pub fn token_path(&self) -> &Path {
        match self {
            Endpoint::Unix { token, .. } | Endpoint::Tcp { token, .. } => token,
        }
    }

    /// 사람에게 보일 이름(오류 메시지·상태줄).
    pub fn display(&self) -> String {
        match self {
            Endpoint::Unix { path, .. } => path.display().to_string(),
            Endpoint::Tcp { host, port, .. } => format!("tcp:{host}:{port}"),
        }
    }

    /// 이 엔드포인트가 지금 살아 있는가(= 붙어 볼 가치가 있는가).
    ///
    /// Unix 는 소켓 파일의 존재, TCP 는 포트파일의 존재로 본다. **연결까지 해 보지는
    /// 않는다** — Windows 루프백에서 죽은 포트로의 connect 는 즉시 거절되지 않고
    /// 방화벽이 SYN 을 조용히 버려 타임아웃까지 매달린다(서버 쪽에서 실측된 함정).
    pub fn looks_live(&self) -> bool {
        match self {
            Endpoint::Unix { path, .. } => path.exists(),
            Endpoint::Tcp { portfile, port, .. } => *port != 0 || portfile.exists(),
        }
    }

    /// 런타임 `lang` 선택이 사는 파일 — 파이썬 `i18n._lang_file`(`state_base + ".lang"`)
    /// 과 **같은 자리**다. 어긋나면 증상이 오류가 아니라 "파이썬 클라에서 바꾼 언어를
    /// 이쪽이 못 본다"는 조용한 불일치라, 규칙을 여기 못박는다:
    /// Unix 는 소켓 경로 + `.lang`(`default.sock.lang`), TCP 는 상태 디렉터리의
    /// `default.lang`(포트파일 `default.port` 와 형제).
    pub fn lang_file(&self) -> PathBuf {
        match self {
            Endpoint::Unix { path, .. } => {
                let mut file = path.clone().into_os_string();
                file.push(".lang");
                PathBuf::from(file)
            }
            Endpoint::Tcp { portfile, .. } => portfile.with_extension("lang"),
        }
    }

    /// 실제로 붙을 포트. TCP 이고 포트가 0 이면 포트파일에서 읽는다.
    pub fn resolve_port(&self) -> Option<u16> {
        match self {
            Endpoint::Unix { .. } => None,
            Endpoint::Tcp { port, portfile, .. } => {
                if *port != 0 {
                    return Some(*port);
                }
                read_port(portfile)
            }
        }
    }
}

/// 포트파일 한 줄을 읽는다. 없거나 숫자가 아니면 `None`.
pub fn read_port(portfile: &Path) -> Option<u16> {
    std::fs::read_to_string(portfile).ok()?.trim().parse().ok()
}

/// 토큰이 있으면 읽는다.
pub fn read_token_at(path: &Path) -> Option<String> {
    let raw = std::fs::read_to_string(path).ok()?;
    let trimmed = raw.trim();
    (!trimmed.is_empty()).then(|| trimmed.to_owned())
}

/// 규칙을 계산하는 데 필요한 환경. 주입받는 이유는 위 모듈 문서 참조.
#[derive(Debug, Clone)]
pub struct Rules {
    pub windows: bool,
    pub xdg_runtime_dir: Option<PathBuf>,
    pub localappdata: Option<PathBuf>,
    pub pytmux_home: Option<PathBuf>,
    /// Unix `/tmp/pytmux-<uid>` 폴백에 쓴다.
    pub uid: u32,
    /// `%LOCALAPPDATA%` 가 없을 때의 폴백(서버는 `~` 를 쓴다).
    pub home: Option<PathBuf>,
}

impl Rules {
    /// 지금 이 프로세스의 환경에서.
    pub fn from_env() -> Self {
        let var = |name: &str| {
            std::env::var_os(name)
                .filter(|v| !v.is_empty())
                .map(PathBuf::from)
        };
        Self {
            windows: cfg!(windows),
            xdg_runtime_dir: var("XDG_RUNTIME_DIR"),
            localappdata: var("LOCALAPPDATA"),
            pytmux_home: var("PYTMUX_HOME"),
            uid: uid(),
            home: var("HOME").or_else(|| var("USERPROFILE")),
        }
    }

    /// 런타임 상태 디렉터리(소켓·포트파일·토큰이 사는 곳).
    ///
    /// `PYTMUX_HOME` 이 있으면 **`<home>/state`** 다 — `<home>` 자체가 아니다. 서버가
    /// 런타임(state)·설정(config)·DB(db)를 형제 디렉터리로 가르기 때문이고, 여기를
    /// 틀리면 소켓을 못 찾는다.
    pub fn state_dir(&self) -> PathBuf {
        if let Some(home) = &self.pytmux_home {
            return home.join("state");
        }
        if self.windows {
            let base = self
                .localappdata
                .clone()
                .or_else(|| self.home.clone())
                .unwrap_or_else(|| PathBuf::from("."));
            return base.join("pytmux");
        }
        match &self.xdg_runtime_dir {
            Some(xdg) => xdg.join("pytmux"),
            None => PathBuf::from(format!("/tmp/pytmux-{}", self.uid)),
        }
    }

    /// 이미 떠 있는 서버를 찾기 위한 후보들(우선순위 순).
    pub fn candidates(&self) -> Vec<Endpoint> {
        if self.windows {
            let dir = self.state_dir();
            return vec![Endpoint::Tcp {
                host: "127.0.0.1".to_owned(),
                port: 0,
                portfile: dir.join("default.port"),
                token: dir.join("default.token"),
            }];
        }
        // `PYTMUX_HOME` 통합이면 그 소켓 하나가 canonical 이다(이중 후보 불요).
        if self.pytmux_home.is_some() {
            return vec![unix_endpoint(self.state_dir().join("default.sock"))];
        }
        let mut out = Vec::new();
        if let Some(xdg) = &self.xdg_runtime_dir {
            out.push(unix_endpoint(xdg.join("pytmux").join("default.sock")));
        }
        let tmp = PathBuf::from(format!("/tmp/pytmux-{}/default.sock", self.uid));
        if !out.iter().any(|e| matches!(e, Endpoint::Unix { path, .. } if *path == tmp)) {
            out.push(unix_endpoint(tmp));
        }
        out
    }
}

fn unix_endpoint(path: PathBuf) -> Endpoint {
    let mut token = path.clone().into_os_string();
    token.push(".token");
    Endpoint::Unix {
        path,
        token: PathBuf::from(token),
    }
}

/// 이 환경의 후보들.
pub fn candidates() -> Vec<Endpoint> {
    Rules::from_env().candidates()
}

/// 살아 있어 보이는 첫 후보. 없으면 `None`(서버가 안 떠 있다).
pub fn resolve() -> Option<Endpoint> {
    candidates().into_iter().find(Endpoint::looks_live)
}

/// 명시된 위치 하나를 엔드포인트로. `tcp:host:port` 형식도 받는다(서버 CLI 와 같은 문법).
///
/// 포트를 명시하지 않은 `tcp:` 형식은 포트파일이 필요하므로 지금 환경의 규칙에서 가져온다.
pub fn parse(spec: &str) -> Endpoint {
    if let Some(rest) = spec.strip_prefix("tcp:") {
        let (host, port) = match rest.rsplit_once(':') {
            Some((h, p)) => (
                if h.is_empty() { "127.0.0.1" } else { h },
                p.parse().unwrap_or(0),
            ),
            None => ("127.0.0.1", rest.parse().unwrap_or(0)),
        };
        let dir = Rules::from_env().state_dir();
        return Endpoint::Tcp {
            host: host.to_owned(),
            port,
            portfile: dir.join("default.port"),
            token: dir.join("default.token"),
        };
    }
    unix_endpoint(PathBuf::from(spec))
}

/// 토큰이 있으면 읽는다(엔드포인트 규약대로).
pub fn read_token(endpoint: &Endpoint) -> Option<String> {
    read_token_at(endpoint.token_path())
}

#[cfg(unix)]
fn uid() -> u32 {
    // SAFETY: getuid 는 실패하지 않고 전역 상태를 바꾸지 않는다.
    unsafe { libc::getuid() }
}

#[cfg(not(unix))]
fn uid() -> u32 {
    0
}

#[cfg(test)]
#[path = "endpoint_tests.rs"]
mod tests;
