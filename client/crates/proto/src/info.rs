//! 정보 팝업의 **탭 내용** — 패리티 `InfoTabsScreen`.
//!
//! # 왜 뷰가 아니라 여기인가
//!
//! 줄을 만드는 것은 **판정**이다(원격인가 · 몇 개인가 · 아직 안 왔으면 뭐라고 적나).
//! 뷰마다 적으면 같은 팝업이 GUI 와 TUI 에서 다른 말을 하기 시작한다 — 이 저장소가 이미
//! 두 번 만든 갈라짐이고, 그래서 "로직은 core/proto, 뷰는 얇게"가 §3 원칙 2 다.
//!
//! `core` 가 아니라 `proto` 인 이유는 재료가 전부 서버가 준 것이기 때문이다
//! ([`SessionState`](crate::SessionState)) — `core` 는 서버를 모른다.
//!
//! # 파이썬과 무엇이 같고 무엇이 다른가
//!
//! 같은 것(G9u 이후): **REC 탭**(rec 서버 플러그인이 status 에 싣는 값 — 플러그인이
//! 없으면 탭도 없다) · **서버 탭**(호스트 · 로컬/원격 · 소켓 · RTT 60분 그래프 · 응답성).
//! (파이썬의 토큰 사용량 탭은 2026-06-12 에 token-log 팝업으로 통합돼 **정본에도 없다**.)
//! 다른 것: 우리가 늘 아는 것을 **세션 탭**으로 하나 더 준다 — 빈 팝업보다 낫다.

use base::i18n::{t, tf};

use crate::SessionState;

/// 탭 하나 — `(제목, 줄들)`.
pub type InfoTab = (&'static str, Vec<String>);

/// 서버가 아직 대답 안 한 자리에 적는 말. 빈 줄로 두면 "정보가 없다"로 읽힌다.
const WAITING: &str = "서버에 묻는 중…";

/// 정보 팝업에 실을 탭들.
///
/// `endpoint` 는 이 클라가 붙어 있는 소켓/TCP 주소다 — 상태에는 없고 뷰가 들고 있다.
/// `now` 는 뷰의 RTT 시계([`crate::rtt::Pinger::now`]) — 그래프의 '지금'이다.
pub fn tabs(state: &SessionState, endpoint: &str, now: f64) -> Vec<InfoTab> {
    let mut tabs: Vec<InfoTab> = Vec::new();
    // REC 탭은 **rec 서버 플러그인이 있을 때만** 있다 — status 에 `capture` 칸이 오면
    // 그 플러그인이 돌고 있다는 뜻이다(파이썬 delete-to-disable 동형: 플러그인을
    // 지우면 탭이 통째로 빠진다). 자리는 파이썬처럼 맨 앞이다.
    if state.flags().capture.is_some() {
        tabs.push((t("출력 캡처(REC)"), capture_lines(state)));
    }
    tabs.push((t("서버"), server_lines(state, endpoint, now)));
    tabs.push((t("세션"), session_lines(state)));
    tabs
}

/// REC 탭 줄 — 파이썬 rec 플러그인 `capture_info_lines` 동형.
fn capture_lines(state: &SessionState) -> Vec<String> {
    let flags = state.flags();
    let on = flags.capture == Some(true);
    let head = if on { t("상태: ON (캡처 중)") } else { t("상태: OFF") };
    let mut lines = if !on {
        vec![head.to_owned(), t("(캡처 꺼짐 — REC 미표시)").to_owned()]
    } else if flags.capture_path.is_empty() {
        vec![head.to_owned(), t("(캡처 파일 준비 중…)").to_owned()]
    } else {
        let size = flags.capture_size.max(0);
        let dir = std::path::Path::new(&flags.capture_path)
            .parent()
            .map(|p| p.join("sessions.log").display().to_string())
            .unwrap_or_default();
        vec![
            head.to_owned(),
            tf("파일: {path}", &[("path", flags.capture_path.as_str())]),
            tf(
                "크기: {bytes} bytes ({kib} KiB)",
                &[
                    ("bytes", group_thousands(size).as_str()),
                    ("kib", format!("{:.1}", size as f64 / 1024.0).as_str()),
                ],
            ),
            tf("탭 매핑: {path}", &[("path", dir.as_str())]),
        ]
    };
    // 파이썬은 [c]/[o] 를 클릭 줄로 얹는다 — 우리 팝업은 읽기 전용 목록이라 키 안내를
    // 줄로 싣는다(동작 자체는 뷰가 c/o 키에서 처리한다).
    lines.push(String::new());
    lines.push(t("[c] 캡처 켜기/끄기 · [o] 기록 폴더 열기").to_owned());
    lines
}

/// 캡처 파일이 있는 폴더를 OS 파일 관리자로 연다(REC 탭 `[o]` — rec 플러그인
/// `_open_capture_dir` 동형). 경로가 없거나 못 열면 `false`.
pub fn open_capture_dir(path: &str) -> bool {
    let Some(dir) = std::path::Path::new(path).parent() else {
        return false;
    };
    #[cfg(target_os = "macos")]
    let opener = "open";
    #[cfg(target_os = "windows")]
    let opener = "explorer";
    #[cfg(all(unix, not(target_os = "macos")))]
    let opener = "xdg-open";
    std::process::Command::new(opener).arg(dir).spawn().is_ok()
}

/// 1,234,567 모양(파이썬 f"{size:,}" 동형).
fn group_thousands(n: i64) -> String {
    let digits = n.to_string();
    let mut out = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(ch);
    }
    out
}

fn server_lines(state: &SessionState, endpoint: &str, now: f64) -> Vec<String> {
    let host = crate::status::hostname();
    // 이 **탭 목록에 원격 탭이 하나라도 있으면** 이 클라는 원격을 보고 있다. 파이썬은
    // 상태줄 위젯의 `_is_remote` 를 보는데 그 값의 출처가 같은 사실이다.
    let remote = state.tabs().tabs.iter().any(|tab| tab.remote);
    let mut lines = vec![
        tf("호스트   {name}", &[("name", if host.is_empty() { t("(모름)") } else { &host })]),
        tf("연결     {mode}", &[("mode", if remote { t("원격 탭 있음") } else { t("로컬") })]),
        tf("엔드포인트 {endpoint}", &[("endpoint", endpoint)]),
        tf("버전     {version}", &[("version", state.version().unwrap_or(t(WAITING)))]),
        String::new(),
    ];
    // RTT — 파이썬 `_server_info_lines` 동형(G9u): 마지막 표본 · 60분 그래프 · 응답성.
    // 표본이 아직 없으면(막 붙었거나 detached 오라클) 그 줄들이 통째로 빠진다.
    let rtt = state.rtt();
    if let Some(last) = rtt.last {
        lines.push(tf(
            "RTT: {rtt} ms (임계 {thr} ms)",
            &[
                ("rtt", format!("{:.0}", last * 1000.0).as_str()),
                ("thr", format!("{:.0}", rtt.threshold * 1000.0).as_str()),
            ],
        ));
    }
    if let Some(graph) = rtt.graph_lines(now, crate::rtt::GRAPH_W, crate::rtt::GRAPH_H) {
        lines.push(String::new());
        lines.extend(graph);
    }
    lines.push(tf(
        "응답성: {state}",
        &[("state", if rtt.degraded { t("저하(degraded) — 빨간 외곽선") } else { t("정상") })],
    ));
    lines.push(String::new());
    lines.push(t("degraded 고착 시 reconnect / resync 명령으로 재접속").to_owned());
    lines
}

fn session_lines(state: &SessionState) -> Vec<String> {
    let tabs = state.tabs();
    let active = tabs.tabs.iter().find(|t| t.active);
    let flags = state.flags();
    let mut lines = vec![
        tf(
            "세션     {name}",
            &[("name", if tabs.session.is_empty() { t("(이름 없음)") } else { &tabs.session })],
        ),
        tf("탭       {n}개", &[("n", tabs.tabs.len().to_string().as_str())]),
        tf("패널     {n}개 (이 탭)", &[("n", state.panes().len().to_string().as_str())]),
        tf(
            "활성 탭  {tab}",
            &[(
                "tab",
                active
                    .map_or_else(|| t("(없음)").to_owned(), |t| format!("{} · {}", t.index, t.name))
                    .as_str(),
            )],
        ),
        tf(
            "활성 패널 {title}",
            &[("title", if flags.pane_title.is_empty() { t("(제목 없음)") } else { &flags.pane_title })],
        ),
    ];
    // 켜져 있는 것만 적는다 — 전부 적으면 꺼진 것들이 화면을 채워 켜진 것이 안 보인다.
    let on = flags.badges();
    if !on.is_empty() {
        lines.push(String::new());
        lines.push(tf("표식     {list}", &[("list", on.join(" ").as_str())]));
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn there_are_always_tabs_even_before_the_server_says_anything() {
        // ★ 팝업을 열었는데 탭이 하나도 없으면 사용자는 **키가 안 먹은 줄** 안다.
        // 회신을 기다려 여는 것이 아니라 열고 채우는 것이 이 저장소의 규약이다.
        let state = SessionState::new();
        let tabs = tabs(&state, "/tmp/x.sock", 0.0);
        assert_eq!(tabs.len(), 2);
        assert!(tabs.iter().all(|(_, lines)| !lines.is_empty()), "빈 탭이 있다");
    }

    #[test]
    fn the_endpoint_the_client_is_attached_to_is_shown() {
        // 여러 서버를 오갈 때 **어디에 붙어 있나**가 이 팝업의 첫 쓰임이다.
        let state = SessionState::new();
        let (_, lines) = &tabs(&state, "tcp:127.0.0.1:51606", 0.0)[0];
        assert!(
            lines.iter().any(|l| l.contains("tcp:127.0.0.1:51606")),
            "엔드포인트가 안 보인다: {lines:?}"
        );
    }

    fn status(extra: serde_json::Value) -> crate::ServerMessage {
        let mut obj = serde_json::json!({"t": "status", "windows": []});
        for (k, v) in extra.as_object().unwrap() {
            obj[k] = v.clone();
        }
        serde_json::from_value(obj).unwrap()
    }

    #[test]
    fn the_rec_tab_exists_only_when_the_plugin_speaks() {
        // ★ G9u — status 의 capture 칸 = rec 서버 플러그인 가동. 칸이 없으면 탭도
        //   없다(파이썬 delete-to-disable 동형). 있으면 **맨 앞**이고 상태가 보인다.
        let mut state = SessionState::new();
        assert_eq!(tabs(&state, "x", 0.0).len(), 2, "플러그인 없이 REC 탭이 섰다");
        state.apply(status(serde_json::json!({
            "capture": true, "capture_path": "/tmp/rec/pane1.log", "capture_size": 2048
        })));
        let all = tabs(&state, "x", 0.0);
        assert_eq!(all.len(), 3);
        let (title, lines) = &all[0];
        assert_eq!(*title, "출력 캡처(REC)");
        assert!(lines.iter().any(|l| l.contains("상태: ON")), "{lines:?}");
        assert!(lines.iter().any(|l| l.contains("/tmp/rec/pane1.log")), "{lines:?}");
        assert!(lines.iter().any(|l| l.contains("2,048 bytes (2.0 KiB)")), "{lines:?}");
        assert!(lines.iter().any(|l| l.contains("sessions.log")), "{lines:?}");
        // 꺼짐도 정직하게.
        state.apply(status(serde_json::json!({"capture": false})));
        let all = tabs(&state, "x", 0.0);
        assert!(all[0].1.iter().any(|l| l.contains("상태: OFF")), "{:?}", all[0].1);
    }

    #[test]
    fn the_server_tab_grows_the_rtt_block_once_samples_arrive() {
        // ★ G9u — 표본 전에는 RTT 줄이 아예 없고(없는 값을 지어내지 않는다), 표본이
        //   오면 마지막 RTT·그래프·응답성이 파이썬 순서로 선다.
        let mut state = SessionState::new();
        let before = &tabs(&state, "x", 0.0)[0].1;
        assert!(!before.iter().any(|l| l.contains("RTT")), "{before:?}");
        state.rtt_mut().sample(100.0, 0.012);
        let lines = &tabs(&state, "x", 100.0)[0].1;
        assert!(lines.iter().any(|l| l.contains("RTT: 12 ms (임계 400 ms)")), "{lines:?}");
        assert!(lines.iter().any(|l| l.contains("RTT 그래프")), "{lines:?}");
        assert!(lines.iter().any(|l| l.contains("응답성: 정상")), "{lines:?}");
    }

    #[test]
    fn an_unanswered_version_says_so_instead_of_looking_empty() {
        let state = SessionState::new();
        let (_, lines) = &tabs(&state, "x", 0.0)[0];
        assert!(lines.iter().any(|l| l.contains(WAITING)), "실제: {lines:?}");
    }
}
