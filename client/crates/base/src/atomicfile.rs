//! 원자 교체 — **세 클라가 같은 파일에 쓴다**(L3, 트리 통합 계획 §6.3).
//!
//! # 무엇이 문제였나
//!
//! 설정·인자 이력·로케일은 클라 **하나**를 가정하고 쓰였다. 그런데 이 저장소의 전제는
//! 파이썬 Textual · Rust TUI · Rust GUI 가 **같은 서버에 동시에** 붙는 것이고, 셋 다
//! 같은 `pytmux.conf`·`*.arghist.json`·`.lang` 을 쓴다.
//!
//! 쓰기 자체는 이미 "임시 파일 + rename" 이었다. 그런데 **임시 파일 이름이 같았다** —
//! `config.tmp` 하나를 셋이 나눠 썼다. 그러면 이런 겹침이 성립한다:
//!
//! 1. A 가 `config.tmp` 를 열어 절반을 쓴다.
//! 2. B 가 같은 `config.tmp` 를 **truncate** 하고 자기 것을 쓰기 시작한다.
//! 3. A 가 rename 한다 → **반만 쓰인 파일이 `config` 가 된다.**
//!
//! 증상은 "설정이 가끔 날아간다"이고, 재현은 사실상 불가능하다(둘이 같은 순간에 저장해야
//! 한다). rename 자체는 POSIX·Windows 모두 원자적이므로, 고칠 곳은 **이름**뿐이다.
//!
//! # 규율(계획 §6.3)
//!
//! - **원자 교체**: 프로세스마다 다른 임시 이름 → rename.
//! - **관대한 읽기**: 모르는 키·줄은 보존하거나 건너뛴다(각 파서가 이미 그렇게 한다).
//! - **마지막 쓰기 승리**: 두 클라가 같은 키를 동시에 바꾸면 나중 것이 남는다. 병합은
//!   안 한다 — 설정 한 줄에 병합 의미를 만들면 그것대로 놀라운 일이 된다.

use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

/// 같은 프로세스 안에서도 임시 이름이 겹치지 않게(한 프로세스가 여러 파일을 동시에
/// 저장할 수 있다 — 설정 저장 중 인자 이력이 저장되는 식).
static SEQ: AtomicU64 = AtomicU64::new(0);

/// 이 저장에 쓸 임시 경로. **프로세스마다 다르다.**
///
/// 이름은 `<파일>.tmp.<pid>.<순번>` 이다. 남는 쓰레기가 걱정될 수 있지만, 남는 경우는
/// rename 직전에 프로세스가 죽은 때뿐이고 그때는 다음 저장이 새 이름을 쓴다(같은 이름을
/// 재사용해 **남의 것을 덮는** 쪽이 훨씬 나쁘다).
pub fn temp_path(target: &Path) -> PathBuf {
    let seq = SEQ.fetch_add(1, Ordering::Relaxed);
    let mut name = target.as_os_str().to_os_string();
    name.push(format!(".tmp.{}.{seq}", std::process::id()));
    PathBuf::from(name)
}

/// 내용을 **원자적으로** 갈아 끼운다. 읽는 쪽은 옛 것 아니면 새 것을 보고, 그 사이는 없다.
///
/// 부모 디렉토리가 없으면 만든다(첫 저장). 실패하면 임시 파일을 치우고 오류를 올린다 —
/// 남겨 두면 다음 사람이 그것을 진짜 설정으로 착각한다.
pub fn write(target: &Path, contents: &str) -> io::Result<()> {
    if let Some(parent) = target.parent().filter(|p| !p.as_os_str().is_empty()) {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = temp_path(target);
    if let Err(err) = std::fs::write(&tmp, contents) {
        let _ = std::fs::remove_file(&tmp);
        return Err(err);
    }
    // Windows 의 `rename` 은 대상이 있으면 실패하는 함수(`MoveFile`)가 아니라
    // `ReplaceFile`/`MoveFileEx` 의미로 구현돼 있다 — Rust std 가 그 차이를 흡수한다.
    if let Err(err) = std::fs::rename(&tmp, target) {
        let _ = std::fs::remove_file(&tmp);
        return Err(err);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// ★ 이 모듈이 존재하는 이유 — **임시 이름이 겹치면 안 된다.**
    ///
    /// 종전 코드는 `target.with_extension("tmp")` 였다. 그건 대상마다 하나뿐인 이름이라
    /// 두 클라가 같은 파일을 쓰면 서로의 절반을 rename 할 수 있었다.
    #[test]
    fn two_savers_never_share_a_temp_name() {
        let target = Path::new("/tmp/pytmux-atomic-test/config");
        let a = temp_path(target);
        let b = temp_path(target);
        assert_ne!(a, b, "같은 프로세스 안에서도 임시 이름이 겹쳤다");
        assert!(
            a.to_string_lossy().contains(&std::process::id().to_string()),
            "임시 이름에 pid 가 없다 — 다른 프로세스와 겹친다: {a:?}"
        );
        // 대상 이름을 덮어쓰지 않는다(`with_extension` 은 `config.json` → `config.tmp`
        // 처럼 **확장자를 갈아 치운다** — 그러면 다른 파일의 임시본과 이름이 겹칠 수 있다).
        assert!(a.to_string_lossy().starts_with(&*target.to_string_lossy()));
    }

    #[test]
    fn a_write_replaces_the_whole_file() {
        let dir = std::env::temp_dir().join(format!("pytmux-atomic-{}", std::process::id()));
        let target = dir.join("config");
        write(&target, "set mouse on\n").expect("첫 저장");
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "set mouse on\n");
        // 더 짧은 내용으로 덮어도 **앞부분만 남는** 일이 없다(truncate 가 아니라 교체).
        write(&target, "x\n").expect("두 번째 저장");
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "x\n");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 여러 스레드가 같은 파일에 동시에 써도 읽는 쪽은 **언제나 온전한 내용**을 본다.
    ///
    /// 마지막 쓰기 승리라 어느 것이 남는지는 안 정한다 — 정하는 것은 "찢긴 것은 없다"다.
    #[test]
    fn concurrent_writers_never_publish_a_torn_file() {
        let dir = std::env::temp_dir().join(format!("pytmux-atomic-cc-{}", std::process::id()));
        let target = dir.join("config");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&target, "a".repeat(64)).unwrap();
        let done = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let readers = {
            let target = target.clone();
            let done = done.clone();
            std::thread::spawn(move || {
                let mut torn = 0;
                while !done.load(Ordering::Relaxed) {
                    if let Ok(text) = std::fs::read_to_string(&target) {
                        // 어느 저장자의 것이든 길이는 64 여야 한다. 다른 길이 = 찢김.
                        if text.len() != 64 {
                            torn += 1;
                        }
                    }
                }
                torn
            })
        };
        let writers: Vec<_> = (0..4)
            .map(|n| {
                let target = target.clone();
                std::thread::spawn(move || {
                    let body = std::char::from_digit(n, 10).unwrap().to_string().repeat(64);
                    for _ in 0..50 {
                        write(&target, &body).expect("저장");
                    }
                })
            })
            .collect();
        for w in writers {
            w.join().unwrap();
        }
        done.store(true, Ordering::Relaxed);
        assert_eq!(readers.join().unwrap(), 0, "찢긴 파일이 읽혔다");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
