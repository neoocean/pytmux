//! 재시작의 **판정과 자기 재기동** — 패리티 `restart-all` · `restart-server`.
//!
//! # 두 가지가 있다
//!
//! - `restart-server` — 서버 코드를 갈아 끼운다(셸·PTY 는 산다).
//! - `restart-all` — 거기에 **클라 자신의 재기동**을 더한다. 서버·클라 코드를 함께
//!   갱신하면서 작업을 보존하는 동선이다.
//!
//! # 왜 드라이런이 먼저인가
//!
//! 재시작은 **되돌릴 수 없다.** 서버가 자기를 갈아 끼우는 중에 뭔가 어긋나면 셸이 통째로
//! 사라진다. 그래서 파이썬은 실행 전에 `request_restart_check` 로 안전성을 먼저 묻고
//! (부작용 없는 드라이런), 통과하면 곧장 실행하고 실패하면 **무엇이 실패했는지 적어**
//! 다시 묻는다. 우리 `restart-server` 는 그 점검을 건너뛰고 있었다 — 파이썬보다 위험했다.
//!
//! # 왜 core 인가
//!
//! 판정([`evaluate`])은 두 뷰가 같아야 한다. 한쪽만 점검을 건너뛰면 그 클라에서만 위험한
//! 재시작이 돌고, 그 사실은 사고가 난 뒤에야 보인다. 자기 재기동([`relaunch`])도 같다 —
//! **어느 이진을 어떤 인자로 다시 띄우나**는 판단이고, 뷰마다 적으면 갈린다.

/// 무엇을 재시작하나.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// 서버만. 클라는 그대로 붙어 있다가 새 서버의 첫 full 프레임을 받는다.
    Server,
    /// 서버 + **이 클라 자신**.
    All,
}

/// 드라이런 한 줄의 결과 — `(통과했나, 사람이 읽을 이름)`.
pub type CheckRow = (bool, &'static str);

/// 서버가 드라이런으로 알려 준 값들.
///
/// # 왜 JSON 을 안 받나
///
/// `core` 는 와이어를 모른다(이 크레이트에 `serde_json` 의존이 아예 없다 — 계층 게이트가
/// 지키는 경계와 같은 결이다). 회신을 이 구조체로 옮기는 것은 `proto` 의 일이고, 여기서는
/// **판정만** 한다.
///
/// 없는 칸은 `false`/`0` 으로 온다 — 그래야 서버가 이름을 바꿨을 때 **막힌다**
/// (통과로 떨어지면 점검이 있으나 마나인 상태가 되고, 그건 없는 것보다 나쁘다).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Probe {
    /// 서버가 **자기를 다시 띄울 수 있나**.
    ///
    /// 재는 방법이 OS 마다 다르다: POSIX 는 `os.execv` 로 자기를 덮어쓰고, Windows 는
    /// 그 시스템콜이 없어 **pty-host 인수인계**(옵션 C)로 같은 일을 한다. 서버가 그 둘을
    /// 이미 OR 로 재서 보내므로 여기서는 참/거짓 하나다 — 갈리는 것은 [`Probe::server_os`]
    /// 가 정하는 **라벨뿐**이다(§10-21ⓔ3).
    pub reexec: bool,
    /// 복원할 세션이 있나.
    pub sessions: bool,
    /// 상태 직렬화가 왕복하나.
    pub serialize: bool,
    pub panes: i64,
    pub panes_with_fd: i64,
    /// **서버가** 도는 OS(`windows` · `posix`). 모르면 `None` — 옛 서버다.
    ///
    /// 클라의 OS 로 대신 판단하면 안 된다: 서버는 원격일 수 있다(페더레이션·ssh).
    /// 그래서 서버가 적어 보내고, 첫 점검 줄의 라벨이 이 값을 따른다.
    pub server_os: Option<Os>,
}

/// 서버가 도는 OS — 첫 점검 줄이 **무엇을 재는가**가 여기서 갈린다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Os {
    /// `os.execv` 로 자기를 덮어쓴다.
    Posix,
    /// 그 시스템콜이 없어 **pty-host 인수인계**로 같은 일을 한다(옵션 C).
    Windows,
}

impl Os {
    /// 서버가 보낸 낱말 → 갈래. 모르는 낱말은 `None` 이다 — 억지로 한쪽으로 접으면
    /// 서버가 이름을 바꿨을 때 **틀린 라벨을 확신에 차서** 적는다.
    pub fn parse(word: &str) -> Option<Self> {
        match word {
            "windows" => Some(Self::Windows),
            "posix" => Some(Self::Posix),
            _ => None,
        }
    }
}

/// 첫 점검 줄의 라벨 — 그 서버의 OS 가 **실제로 재는 것**을 적는다(§10-21ⓔ3).
///
/// 제보: Windows 이진에서도 `✗ 서버 re-exec 지원(POSIX·이벤트루프)` 이 떠, **못 하는
/// 것이 정상인 조건**을 실패로 보여 "재시작 불가"로 읽혔다. 재는 값은 이미 OS 별로 갈려
/// 있었고(서버가 둘을 OR 로 잰다) 갈리지 않은 것은 **라벨뿐**이었다.
///
/// 파이썬 `_restart_server_relaunch_label` 과 같다 — 두 클라가 같은 화면을 보이게.
pub fn server_relaunch_label(server_os: Option<Os>) -> &'static str {
    use crate::i18n::t;
    match server_os {
        Some(Os::Windows) => t("서버 재기동 지원(pty-host 인수인계)"),
        // 모르는 서버(옛 판)는 종전 문구 그대로 — 지어내지 않는다.
        Some(Os::Posix) | None => t("서버 re-exec 지원(POSIX·이벤트루프)"),
    }
}

/// 서버 드라이런 회신 + 클라 쪽 점검을 합쳐 `(안전한가, 줄들)` 로.
///
/// 이름과 순서는 파이썬 `_restart_check_eval` 과 같다 — 두 클라가 같은 화면을 보이게
/// 하려는 것이고, 사용자가 실패 항목을 보고 판단하는 자리라 문구가 곧 계약이다.
///
/// `Kind::Server` 는 클라를 다시 안 띄우므로 **재기동 점검을 빼고** 본다(파이썬과 같다).
pub fn evaluate(probe: Probe, relaunch_ok: bool, kind: Kind) -> (bool, Vec<CheckRow>) {
    use crate::i18n::t;
    // 패널이 하나도 없으면 통과가 아니다 — 복원할 것이 없는데 재시작하는 것은 뜻이 없고,
    // `0 == 0` 을 통과로 읽으면 그 자리가 조용히 열린다(파이썬도 `panes > 0` 을 요구한다).
    let fd_ok = probe.panes == probe.panes_with_fd && probe.panes > 0;
    let mut rows: Vec<CheckRow> = vec![
        // 값도 순서도 그대로다 — 라벨만 서버 OS 를 따른다(§10-21ⓔ3).
        (probe.reexec, server_relaunch_label(probe.server_os)),
        (probe.sessions, t("복원할 세션 존재")),
        (probe.serialize, t("상태 직렬화 round-trip")),
        (fd_ok, t("패널 master fd 보유")),
    ];
    if kind == Kind::All {
        rows.push((relaunch_ok, t("클라 재기동 가능(이진 경로 해석)")));
    }
    let safe = rows.iter().all(|(ok, _)| *ok);
    (safe, rows)
}

/// 실패한 줄만 골라 사람이 읽을 한 덩어리로. 확인 화면이 이 글을 보인다.
///
/// 실패한 것만 적는 이유: 통과한 줄까지 늘어놓으면 **무엇이 문제인지가 묻힌다.**
pub fn failure_detail(rows: &[CheckRow]) -> String {
    rows.iter()
        .filter(|(ok, _)| !ok)
        .map(|(_, label)| format!("✗ {label}"))
        .collect::<Vec<_>>()
        .join("\n")
}

/// 이 클라를 **다시 띄울 수 있나**(이진 경로를 해석할 수 있나).
///
/// 파이썬은 `sys.argv[0]` 이 `.py` 이거나 실행 가능한지 본다. 우리는 이진이라 판정이 더
/// 단순하다 — `current_exe()` 가 실재하는 파일을 가리키면 된다.
pub fn relaunch_ok() -> bool {
    std::env::current_exe().is_ok_and(|path| path.is_file())
}

/// 이 클라를 **같은 인자로 다시 띄우고** 자기는 끝낸다 — 부르는 쪽이 그 뒤에 종료한다.
///
/// # 왜 `exec` 가 아닌가
///
/// 파이썬은 `os.execv` 로 자기를 덮어쓴다. Windows 에는 그 시스템콜이 없으므로 **새
/// 프로세스를 띄우고 우리가 빠지는** 방식을 쓴다. 자식이 같은 콘솔·같은 창을 물려받으므로
/// 사용자에게는 같은 일로 보인다.
///
/// # 인자를 그대로 넘긴다
///
/// `--socket` 이 특히 그렇다 — 런처가 고른 엔드포인트를 잃으면 **다른 서버에 붙는다**
/// (`main.rs` 의 「지정된 엔드포인트는 폴백하지 않는다」와 같은 이유).
///
/// 성공하면 새 프로세스의 pid, 실패하면 그 까닭.
pub fn relaunch() -> Result<u32, String> {
    let exe = std::env::current_exe()
        .map_err(|e| crate::i18n::tf("이진 경로를 못 찾았다: {err}", &[("err", &e.to_string())]))?;
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::Command::new(exe)
        .args(args)
        .spawn()
        .map(|child| child.id())
        .map_err(|e| crate::i18n::tf("다시 띄우지 못했다: {err}", &[("err", &e.to_string())]))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn all_good() -> Probe {
        Probe {
            reexec: true,
            sessions: true,
            serialize: true,
            panes: 2,
            panes_with_fd: 2,
            server_os: Some(Os::Posix),
        }
    }

    /// ★ §10-21ⓔ3 — Windows 서버에 **POSIX 조건을 적지 않는다.**
    ///
    /// 제보의 증상은 "못 하는 것이 정상인 조건을 실패로 그려" 재시작이 아예 안 되는
    /// 것처럼 읽히는 것이었다. 재는 값은 그대로고 라벨만 그 OS 의 것이 된다.
    #[test]
    fn a_windows_server_is_not_told_it_lacks_a_posix_feature() {
        let probe = Probe { server_os: Some(Os::Windows), ..all_good() };
        let (_, rows) = evaluate(probe, true, Kind::All);
        assert!(rows[0].1.contains("pty-host"), "실제: {}", rows[0].1);
        assert!(!rows[0].1.contains("POSIX"), "실제: {}", rows[0].1);
        // 값·순서·개수는 안 움직인다 — 갈린 것은 라벨뿐이다.
        assert_eq!(rows.len(), 5);
        assert!(rows[0].0);
    }

    #[test]
    fn a_posix_server_keeps_the_old_wording_and_so_does_an_old_server() {
        let posix = evaluate(all_good(), true, Kind::All).1;
        assert!(posix[0].1.contains("POSIX"), "실제: {}", posix[0].1);
        // 서버가 칸을 안 보내면(옛 판) 지어내지 않고 종전 문구로 남는다.
        let old = Probe { server_os: None, ..all_good() };
        assert_eq!(evaluate(old, true, Kind::All).1[0].1, posix[0].1);
    }

    #[test]
    fn an_unknown_os_word_is_not_folded_into_one_of_the_two() {
        // 억지로 접으면 서버가 이름을 바꿨을 때 **틀린 라벨을 확신에 차서** 적는다.
        assert_eq!(Os::parse("darwin"), None);
        assert_eq!(Os::parse("windows"), Some(Os::Windows));
        assert_eq!(Os::parse("posix"), Some(Os::Posix));
    }

    #[test]
    fn everything_green_is_safe() {
        let (safe, rows) = evaluate(all_good(), true, Kind::All);
        assert!(safe, "실제: {rows:?}");
        assert_eq!(rows.len(), 5, "restart-all 은 재기동 점검까지 다섯 줄이다");
    }

    #[test]
    fn the_server_kind_does_not_check_the_client_relaunch() {
        // 클라를 다시 안 띄우므로 그 점검이 실패해도 서버 재시작은 안전하다.
        let (safe, rows) = evaluate(all_good(), false, Kind::Server);
        assert!(safe, "실제: {rows:?}");
        assert_eq!(rows.len(), 4);
        // 반대로 restart-all 은 그것 때문에 막힌다.
        let (safe_all, _) = evaluate(all_good(), false, Kind::All);
        assert!(!safe_all, "재기동이 안 되는데 restart-all 이 통과했다");
    }

    #[test]
    fn no_panes_is_not_safe_even_though_the_counts_match() {
        // ★ `0 == 0` 을 통과로 읽으면 그 자리가 조용히 열린다. 복원할 것이 없는데
        // 재시작하는 것은 뜻이 없다(파이썬도 `panes > 0` 을 요구한다).
        let probe = Probe { panes: 0, panes_with_fd: 0, ..all_good() };
        let (safe, rows) = evaluate(probe, true, Kind::All);
        assert!(!safe, "패널이 없는데 안전하다고 했다: {rows:?}");
    }

    #[test]
    fn a_missing_field_counts_as_a_failure_not_a_pass() {
        // 서버가 칸 이름을 바꾸면 **막히는** 편이 맞다. 통과로 떨어지면 점검이 있으나
        // 마나인 상태가 되고, 그건 점검이 없는 것보다 나쁘다(있다고 믿게 된다).
        // 클라 재기동 점검은 서버 회신과 무관하므로(우리 이진 경로다) 통과로 남는다 —
        // 여기서 보는 것은 **서버가 준 네 줄**이 전부 막히는가다.
        let (safe, rows) = evaluate(Probe::default(), true, Kind::All);
        assert!(!safe);
        assert!(
            rows[..4].iter().all(|(ok, _)| !ok),
            "서버 칸이 없는데 통과한 줄이 있다: {rows:?}"
        );
    }

    #[test]
    fn the_detail_lists_only_what_failed() {
        let probe = Probe { reexec: false, ..all_good() };
        let (_, rows) = evaluate(probe, true, Kind::All);
        let detail = failure_detail(&rows);
        assert!(detail.contains("re-exec"), "실제: {detail}");
        assert!(!detail.contains("세션 존재"), "통과한 줄이 섞였다: {detail}");
        assert_eq!(detail.lines().count(), 1);
    }

    #[test]
    fn this_binary_can_relaunch_itself() {
        // 판정이 늘 `false` 면 `restart-all` 이 영영 안 된다 — 그 사실을 여기서 잡는다.
        assert!(relaunch_ok(), "테스트 이진의 경로를 해석하지 못한다");
    }
}
