//! 어느 트랜스크립트가 이 패널의 것인가.
//!
//! Claude Code 는 세션 기록을 **작업 디렉터리별 폴더**에 넣는다:
//!
//! ```text
//! ~/.claude/projects/<인코딩된 cwd>/<세션 uuid>.jsonl
//! ```
//!
//! 인코딩 규칙은 pytmux 서버가 이미 쓰고 있는 것과 **같아야 한다**
//! (`plugins/claude-code/transcript.py::encode_project_dir`) — 한 글자만 달라도 폴더를
//! 못 찾고, 그건 "기능이 조용히 안 됨"으로 나타난다. 그래서 적합성 테스트가 파이썬에서
//! 뽑은 표와 대조한다(`tests/encode_conformance.rs`).
//!
//! # cwd 는 블록이 알려 준다
//!
//! 클라는 패널의 작업 디렉터리를 스스로 모른다 — 서버가 `blocks` 로 보내 주는 cwd(OSC 7)
//! 가 유일한 출처다. 즉 **셸 통합을 깔아야 이 뷰가 뜬다**. 안 깐 사용자에게는 아무 일도
//! 일어나지 않는다(우아한 저하).

use std::path::{Path, PathBuf};

/// 트랜스크립트 루트. `$CLAUDE_CONFIG_DIR` 우선, 없으면 `~/.claude`.
///
/// 서버(`transcript.projects_dir`)와 같은 규칙이다.
pub fn projects_dir() -> Option<PathBuf> {
    let var = |name: &str| {
        std::env::var_os(name)
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
    };
    projects_dir_in(var("CLAUDE_CONFIG_DIR"), home_dir())
}

/// 홈 디렉터리. **Windows 는 `HOME` 이 없다** — `USERPROFILE` 이 표준이고, 파이썬
/// `os.path.expanduser("~")` 도 거기를 본다. `HOME` 만 보면 Windows 에서 이 기능이
/// 통째로 꺼지는데, 증상은 "Claude 세션이 없다"와 구분되지 않아 조용하다.
pub fn home_dir() -> Option<PathBuf> {
    let var = |name: &str| {
        std::env::var_os(name)
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
    };
    if cfg!(windows) {
        return var("USERPROFILE").or_else(|| var("HOME"));
    }
    var("HOME")
}

/// 순수 규칙(테스트용): 설정 디렉터리와 홈이 주어졌을 때의 트랜스크립트 루트.
pub fn projects_dir_in(config_dir: Option<PathBuf>, home: Option<PathBuf>) -> Option<PathBuf> {
    if let Some(dir) = config_dir {
        return Some(dir.join("projects"));
    }
    Some(home?.join(".claude").join("projects"))
}

/// 절대 경로 → Claude Code 의 프로젝트 폴더 이름(`/`·`\`·`:`·`.` → `-`).
///
/// 구분자와 드라이브 콜론까지 바꾸는 이유: 폴더 **이름** 에 그것들이 남으면 이름이 아니라
/// 경로가 된다. `Path::join(root, "C:\\Users\\me")` 는 Windows 에서 **절대경로로 해석돼
/// root 를 통째로 무시하고**, POSIX 에서도 존재하지 않는 중첩 이름이 된다 — 어느 쪽이든
/// 트랜스크립트를 못 찾고, 증상은 "세션이 없다"와 구분되지 않는다(검수 2026-07-27g).
/// 서버 `transcript.encode_project_name` 과 **같은 규칙**이고, 그 일치는
/// `tests/project_dirs.rs` 가 서버에서 뽑은 픽스처로 대조한다.
pub fn encode_project_dir(cwd: &str) -> String {
    cwd.replace(['/', '\\', ':'], "-").replace('.', "-")
}

/// `root` 아래에서 이 cwd 의 **가장 최근** 트랜스크립트 파일.
///
/// 한 폴더에 세션이 여러 개 쌓이므로(`resume` 마다 새 파일) 수정 시각이 가장 최근인
/// 것을 고른다 — 지금 돌고 있는 세션이다. 폴더가 없거나 비어 있으면 `None`(정상).
pub fn newest_transcript_for(root: &Path, cwd: &str) -> Option<PathBuf> {
    let dir = root.join(encode_project_dir(cwd));
    let mut best: Option<(std::time::SystemTime, PathBuf)> = None;
    for entry in std::fs::read_dir(dir).ok()? {
        let Ok(entry) = entry else { continue };
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("jsonl") {
            continue;
        }
        let Ok(modified) = entry.metadata().and_then(|m| m.modified()) else {
            continue;
        };
        if best.as_ref().is_none_or(|(best_ts, _)| modified > *best_ts) {
            best = Some((modified, path));
        }
    }
    best.map(|(_, path)| path)
}

/// 이어 읽기 연속성을 확인하는 앵커 길이.
///
/// 이어 읽기는 "파일이 뒤로만 자란다"는 가정 위에 선다. 그 가정이 깨지면(앞부분이
/// 다시 쓰였다) 이어 붙인 내용이 조용히 어긋나므로, 이어 붙이기 직전 바이트 몇 개를
/// 기억해 두고 매번 대조한다 — 다르면 통째로 다시 읽는다. 크기 감소만 보는 것으로는
/// **같은 크기로 다시 쓰인 경우**를 못 잡는다.
///
/// **여기까지가 이 검사가 보장하는 전부다**: 이어 붙일 지점 **근처**의 재작성만 잡고,
/// 파일 한참 앞쪽이 다시 쓰인 것은 못 본다(보려면 결국 전부 다시 읽어야 하고, 그러면
/// 이어 읽기의 존재 이유가 사라진다). 트랜스크립트는 append-only 이므로 이 정도가
/// 맞는 거래다 — 더 필요해지면 그건 "이어 읽기를 쓰지 말라"는 신호다.
const ANCHOR: usize = 64;

/// 지금 보고 있는 패널의 트랜스크립트를 따라간다.
///
/// # 왜 파일을 계속 다시 읽는가
///
/// Claude 는 대화가 진행되는 동안 같은 파일에 계속 덧쓴다. 한 번 읽고 마는 뷰는 첫
/// 화면 이후로 굳는다. 그렇다고 매 프레임 MB 짜리 파일을 다시 파싱하면 30Hz 로 도는
/// 루프가 그것만 한다 — **수정 시각을 먼저 보고**, 바뀌었을 때만 읽는다.
///
/// # 바뀌었을 때 **얼마나** 읽는가
///
/// 수정 시각 가드만으로는 부족하다. 활성 세션에서는 파일이 계속 바뀌므로 가드가 거의
/// 매번 열리고, 그때마다 전체를 다시 파싱하면 비용이 **파일 크기에 비례**한다(실측:
/// 이 저장소의 23MB 트랜스크립트가 디버그 빌드 116ms · 릴리스 8.8ms — 500ms 마다
/// 116ms 는 30Hz 루프에서 세 프레임 넘게 멎는 것이다). JSONL 은 append-only 이므로
/// **덧붙은 꼬리만** 읽어 이어 붙인다. 파일이 줄었거나 앵커가 어긋나면(=앞부분이 다시
/// 쓰였다) 그때만 통째로 다시 읽는다.
///
/// 호출 주기 자체(얼마나 자주 `refresh` 를 부를지)는 호출부가 정한다. 여기서 시계를
/// 들면 테스트가 시간을 흉내 내야 한다.
#[derive(Debug, Default)]
pub struct Watcher {
    root: Option<PathBuf>,
    cwd: Option<String>,
    path: Option<PathBuf>,
    stamp: Option<(std::time::SystemTime, u64)>,
    /// 여기까지 읽어 반영했다(마지막 **완전한** 줄의 끝). 반쯤 쓰인 마지막 줄은
    /// 다음 번에 다시 읽는다 — 여기를 파일 끝으로 밀면 그 줄을 영영 잃는다.
    offset: u64,
    /// `offset` 직전 바이트들. 이어 읽기 연속성 확인용([`ANCHOR`]).
    anchor: Vec<u8>,
    /// 디스크에서 실제로 읽은 누적 바이트([`Watcher::bytes_read`]).
    bytes_read: u64,
    transcript: crate::Transcript,
}

impl Watcher {
    /// `root` 는 트랜스크립트 폴더(`~/.claude/projects`). 없으면 이 감시자는 영원히
    /// 빈 목록을 준다 — Claude 를 안 쓰는 사용자에게 아무 일도 일어나지 않는다.
    pub fn new(root: Option<PathBuf>) -> Self {
        Self {
            root,
            ..Default::default()
        }
    }

    pub fn items(&self) -> &[crate::Item] {
        self.transcript.items()
    }

    /// 지금 걸려 있는 권한 모드. 뷰가 머리줄에 붙인다.
    pub fn mode(&self) -> Option<&str> {
        self.transcript.mode()
    }

    /// 전용 화면이 읽는 두 가지 — 가장 최근 플랜과 가장 최근 거부.
    pub fn last_plan(&self) -> Option<&crate::Item> {
        self.transcript.last_plan()
    }

    pub fn last_denied(&self) -> Option<&crate::Item> {
        self.transcript.last_denied()
    }

    pub fn path(&self) -> Option<&Path> {
        self.path.as_deref()
    }

    /// 지금까지 **디스크에서 실제로 읽은** 누적 바이트.
    ///
    /// 이 감시자의 핵심 계약은 "덧붙는 파일을 따라갈 때 비용이 파일 크기가 아니라
    /// **덧붙은 크기**에 비례한다"인데, 그건 시간으로 재면 머신에 따라 흔들린다.
    /// 읽은 바이트는 그 계약을 흔들리지 않게 재는 계기판이다(회귀:
    /// `tests/watcher.rs::appending_one_line_does_not_reread_the_whole_file`).
    pub fn bytes_read(&self) -> u64 {
        self.bytes_read
    }

    /// 보고 있는 패널의 작업 디렉터리를 알려 준다. 바뀌었으면 파일을 다시 찾는다.
    ///
    /// 반환값은 "찾는 대상이 바뀌었나" — 화면을 다시 그릴지는 [`Self::refresh`] 가 정한다.
    pub fn set_cwd(&mut self, cwd: &str) -> bool {
        if self.cwd.as_deref() == Some(cwd) {
            return false;
        }
        self.cwd = Some(cwd.to_owned());
        self.path = self
            .root
            .as_ref()
            .and_then(|root| newest_transcript_for(root, cwd));
        self.reset_read_state();
        true
    }

    /// 파일을 처음부터 다시 읽어야 하는 상태로 되돌린다.
    fn reset_read_state(&mut self) {
        self.stamp = None;
        self.offset = 0;
        self.anchor.clear();
        self.transcript = crate::Transcript::default();
    }

    /// 파일이 바뀌었으면 다시 읽는다. **목록이 실제로 달라졌을 때만** `true`.
    ///
    /// 새 세션이 시작되면(`resume`) 폴더에 더 최근 파일이 생기므로 매번 다시 고른다 —
    /// 안 그러면 뷰가 끝난 세션에 머문다.
    pub fn refresh(&mut self) -> bool {
        let Some(root) = self.root.clone() else {
            return false;
        };
        let Some(cwd) = self.cwd.clone() else {
            return false;
        };
        let newest = newest_transcript_for(&root, &cwd);
        let switched = newest != self.path;
        self.path = newest;
        let Some(path) = self.path.clone() else {
            let had = !self.transcript.is_empty();
            self.reset_read_state();
            return had;
        };
        if switched {
            self.reset_read_state();
        }
        // 수정 시각 + 크기: 같은 초 안의 덧쓰기를 시각만으로는 못 본다.
        let stamp = std::fs::metadata(&path)
            .ok()
            .and_then(|m| Some((m.modified().ok()?, m.len())));
        if !switched && stamp == self.stamp && !self.transcript.is_empty() {
            return false;
        }
        self.stamp = stamp;
        let len = stamp.map_or(0, |(_, len)| len);
        // 파일이 줄었으면 우리가 아는 앞부분이 이미 사라진 것이다 — 이어 붙일 수 없다.
        if self.offset > 0 && len >= self.offset {
            if let Some(tail) = self.read_tail(&path, len) {
                return self.transcript.feed(&tail);
            }
        }
        // 이어 읽기가 성립하지 않는다 → 통째로.
        let Ok(text) = std::fs::read_to_string(&path) else {
            return false;
        };
        self.bytes_read += text.len() as u64;
        let end = crate::Transcript::consumable(text.as_bytes());
        let mut next = crate::Transcript::new();
        next.feed(&text[..end]);
        let changed = next.items() != self.transcript.items();
        self.transcript = next;
        self.offset = end as u64;
        self.set_anchor(&text.as_bytes()[..end]);
        changed
    }

    /// `offset` 부터 덧붙은 **소비해도 안전한 줄들**. 앵커가 어긋나면 `None`(= 통째로
    /// 다시 읽어라).
    fn read_tail(&mut self, path: &Path, len: u64) -> Option<String> {
        use std::io::{Read, Seek, SeekFrom};
        let back = self.anchor.len() as u64;
        let mut file = std::fs::File::open(path).ok()?;
        file.seek(SeekFrom::Start(self.offset.checked_sub(back)?)).ok()?;
        let mut buf = Vec::with_capacity((len - self.offset + back) as usize);
        file.take(len - self.offset + back).read_to_end(&mut buf).ok()?;
        self.bytes_read += buf.len() as u64;
        if buf.len() < self.anchor.len() || buf[..self.anchor.len()] != self.anchor[..] {
            return None; // 앞부분이 다시 쓰였다.
        }
        let fresh = &buf[self.anchor.len()..];
        let end = crate::Transcript::consumable(fresh);
        let text = String::from_utf8_lossy(&fresh[..end]).into_owned();
        self.offset += end as u64;
        self.set_anchor(&buf[..self.anchor.len() + end]);
        Some(text)
    }

    fn set_anchor(&mut self, consumed: &[u8]) {
        let start = consumed.len().saturating_sub(ANCHOR);
        self.anchor.clear();
        self.anchor.extend_from_slice(&consumed[start..]);
    }
}
