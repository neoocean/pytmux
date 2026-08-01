//! 명령 인자 이력(파이썬 `clientcmd._load_arghist`/`_save_arghist` 동형).
//!
//! `remote-attach` 의 호스트, `layout-save` 의 이름처럼 **다시 칠 일이 많은 인자**를
//! 버킷별 최근-우선 목록으로 영속한다. 파일은 파이썬 클라와 **같은 자리·같은 모양**
//! (`state_base + ".arghist.json"` · `{버킷: [최근이 앞]}`)이라 두 클라가 이력을
//! 공유한다 — `.lang`(i18n)과 같은 상호 호환 결정이다.
//!
//! 저장 실패는 삼킨다(파이썬과 같은 정책 — 추천이 안 될 뿐 명령 실행을 막지 않는다).

use std::collections::HashMap;
use std::path::PathBuf;

/// 버킷당 보관할 최근 인자 수(파이썬 `_ARGHIST_MAX` 와 같다).
const MAX: usize = 30;

/// 버킷 → 최근-우선 인자 목록.
#[derive(Debug, Default)]
pub struct ArgHist {
    map: HashMap<String, Vec<String>>,
    path: Option<PathBuf>,
}

impl ArgHist {
    /// 영속 파일에서 복원한다(best-effort — 없거나 깨졌으면 빈 이력).
    pub fn load(path: PathBuf) -> Self {
        let map = std::fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str::<HashMap<String, Vec<String>>>(&text).ok())
            .unwrap_or_default();
        Self { map, path: Some(path) }
    }

    /// 소켓 경로에서 파이썬과 같은 자리를 계산해 복원한다.
    ///
    /// `.lang` 과 같은 규칙이다(`endpoint::lang_file` 문서): unix 는 소켓 경로 +
    /// `.arghist.json`, tcp 는 상태 디렉터리의 `default.arghist.json`.
    pub fn for_socket(socket: &str) -> Self {
        Self::load(arghist_file(socket))
    }

    /// 이 버킷의 최근-우선 전체 목록(뷰가 물음에 넣는다 — 좁히기는 core 가 한다).
    pub fn recent(&self, bucket: &str) -> Vec<String> {
        self.map.get(bucket).cloned().unwrap_or_default()
    }

    /// 인자 하나를 기록하고 영속한다 — 같은 값은 맨 앞으로 올라온다(MRU).
    pub fn record(&mut self, bucket: &str, value: &str) {
        let value = value.trim();
        if value.is_empty() {
            return;
        }
        let list = self.map.entry(bucket.to_owned()).or_default();
        list.retain(|v| v != value);
        list.insert(0, value.to_owned());
        list.truncate(MAX);
        self.save();
    }

    /// 원자적 기록(파이썬 `os.replace` 와 같은 모양). 임시 이름은 **프로세스마다
    /// 다르다** — 세 클라가 같은 이력 파일에 쓰므로, 이름을 나눠 쓰면 서로의 절반을
    /// rename 할 수 있다(`base::atomicfile` 모듈 문서).
    fn save(&self) {
        let Some(path) = &self.path else { return };
        let Ok(text) = serde_json::to_string(&self.map) else { return };
        let _ = base::atomicfile::write(path, &text);
    }
}

/// 파이썬 `ipc.state_base(sock) + ".arghist.json"` 과 같은 자리.
pub fn arghist_file(socket: &str) -> PathBuf {
    match crate::endpoint::parse(socket) {
        crate::endpoint::Endpoint::Unix { path, .. } => {
            let mut file = path.into_os_string();
            file.push(".arghist.json");
            PathBuf::from(file)
        }
        crate::endpoint::Endpoint::Tcp { portfile, .. } => {
            portfile.with_extension("arghist.json")
        }
    }
}

/// 이 물음이 어느 버킷의 이력을 쓰나(파이썬 `COMMAND_ARGHIST` 를 우리 물음에 옮긴 것).
///
/// 없는 물음은 `None` — 평소처럼 이력 없이 묻는다. 표가 core 가 아니라 여기 있는
/// 이유: 버킷 이름은 **영속 파일의 어휘**라 파일 규칙과 같은 크레이트가 갖는 편이
/// 어긋날 수 없다.
pub fn bucket(prompt: base::screens::Prompt) -> Option<&'static str> {
    use base::screens::Prompt;
    Some(match prompt {
        Prompt::RemoteAttach | Prompt::RemoteNewTab | Prompt::RemoteDetach => "remote-host",
        Prompt::SaveTabLayout | Prompt::LoadTabLayout | Prompt::LoadTabLayoutNew => "layout-name",
        Prompt::RunShell => "run-shell",
        Prompt::SendKeys => "send-keys",
        _ => return None,
    })
}

#[cfg(test)]
#[path = "arghist_tests.rs"]
mod tests;
