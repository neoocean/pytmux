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

/// 그 탭에서 **할 수 있는 것** 하나 — 정본 `client_status_tabs` 가 주는
/// `(키, 라벨, 콜백)` 중 앞 둘이다.
///
/// # 왜 콜백을 안 드나
///
/// 정본은 세 번째 칸에 파이썬 함수를 담는다. 우리는 못 담는다 — 캡처 토글은 **서버로
/// 나가는 명령**이고 폴더 열기는 **OS 호출**이라, 그 둘을 쥔 것은 뷰(와 링크)뿐이고
/// `proto` 는 서버에 무엇을 보낼지까지만 안다. 그래서 여기서 드는 것은 **무엇이 있나**
/// 이고 **무엇을 하나**는 뷰가 `key` 로 갈라 든다(키 표와 클릭이 같은 자리를 지난다).
///
/// # ⛔ 이 목록을 줄 글로 그리지 마라 (pytmux-373 ⑶)
///
/// 종전에는 [`capture_lines`] 가 `[c] … · [o] …` 를 **줄 하나로** 얹었다. 그래서 그
/// 둘은 정본에서 **고를 수 있는 항목**인데 우리에게는 흐린 글자였고, 키를 직접 치는
/// 수밖에 없었다 — 꼬리줄의 `↑↓ 항목` 도 그동안 거짓이었다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InfoAction {
    /// 핫키(정본과 같은 글자).
    pub key: char,
    /// 목록에 그릴 말 — 정본과 **같은 문구**다(`[c] 캡처 켜기/끄기`).
    pub label: &'static str,
}

/// REC 탭이 **몇 번째인가**(없으면 `None`).
///
/// ⛔ 이 판정을 두 곳에 적지 마라 — [`tabs`] 가 세우는 차례와 [`tab_actions`] 가 세는
/// 차례가 갈리면 `[c]`/`[o]` 가 **엉뚱한 탭**에 붙는다. 그래서 한 함수가 쥔다.
fn rec_tab(state: &SessionState) -> Option<usize> {
    // status 에 `capture` 칸이 오면 rec 플러그인이 돌고 있다는 뜻이고, 그때 자리는
    // 파이썬처럼 맨 앞이다.
    state.flags().capture.is_some().then_some(0)
}

/// `tab` 번째 탭에서 할 수 있는 것들 — 정본 `InfoTabsScreen._actions[ti]` 동형.
///
/// 없는 탭이면 빈 목록이다(그 탭에는 동작 줄이 안 선다).
pub fn tab_actions(state: &SessionState, tab: usize) -> Vec<InfoAction> {
    if rec_tab(state) != Some(tab) {
        return Vec::new();
    }
    vec![
        InfoAction { key: 'c', label: t("[c] 캡처 켜기/끄기") },
        InfoAction { key: 'o', label: t("[o] 기록 폴더 열기") },
    ]
}

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
    if rec_tab(state).is_some() {
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
    let lines = if !on {
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
    // ⛔ **`[c]`/`[o]` 를 줄로 얹지 않는다**(pytmux-373 ⑶). 정본
    // (`rec/clientside.py::capture_info_lines`)도 안 얹는다 — 그 둘은 줄이 아니라
    // **동작**이고([`tab_actions`]) 팝업이 고를 수 있는 항목으로 그린다.
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
    // ⛔ 이름 그대로 띄우지 않는다 — Windows 에서 **이진 옆 폴더**가 시스템 디렉터리보다
    //    먼저 잡힌다(`clip::system_tool` 의 문서에 std 원문과 함께 적었다).
    std::process::Command::new(clip::system_tool(opener))
        .arg(dir)
        .spawn()
        .is_ok()
}

/// 링크를 **OS 기본 브라우저**로 연다(§10-21ⓥ2). 열었으면 `true`.
///
/// # ⚠ 스킴을 여기서 한 번 더 본다
///
/// 범위를 찾을 때 이미 `http`/`https` 만 잡는다([`base::spans`]) — 그런데 그 판정과 이
/// 실행 사이에 자리가 있으면 그 틈이 곧 통로가 된다(패널 출력은 **남이 만든 글**이다).
/// 여는 자리에서 다시 보는 것이 싸고, 두 자리가 갈라질 수 없게 **같은 표**를 본다.
///
/// 열지 못한 것과 안 여는 것은 다르다 — 둘 다 `false` 지만 부르는 쪽이 알림으로 가른다.
pub fn open_link(url: &str) -> bool {
    if !base::spans::is_openable(url) {
        return false;
    }
    #[cfg(target_os = "macos")]
    let opener = "open";
    #[cfg(target_os = "windows")]
    let opener = "explorer";
    #[cfg(all(unix, not(target_os = "macos")))]
    let opener = "xdg-open";
    // 스킴을 좁힌 것과 **같은 이유로** 띄우는 프로그램도 좁힌다(`clip::system_tool`).
    std::process::Command::new(clip::system_tool(opener))
        .arg(url)
        .spawn()
        .is_ok()
}

/// 상대 경로를 **전체 경로**로 푼다(§10-21ⓧ2). 기준은 그 패널의 작업 디렉터리다.
///
/// 못 풀면 `None` — 그때 부르는 쪽은 **존을 안 만든다**(모르는 것을 아는 척하지 않는다).
/// 셸 통합이 없으면 cwd 를 모른다.
///
/// 기준은 [`SessionState::pane_cwd`] 로 **그 범위가 있던 패널**의 것을 준다 —
/// [`SessionState::active_cwd`] 가 아니다. 저건 활성 패널 하나를 가리키고 원격 탭에서
/// `None` 을 내는데(Claude 폴더 오판 방지), 여기서 그걸 쓰면 ⑴옆 패널 글을 남의 기준으로
/// 풀고 ⑵원격 패널에서는 아예 못 푼다. 원격의 답은 **상류 머신의 경로**이고 그게 맞다 —
/// 사용자가 그 값을 붙여 넣을 곳은 그 셸이다.
pub fn resolve_path(cwd: Option<&str>, text: &str) -> Option<String> {
    let path = std::path::Path::new(text);
    if path.is_absolute() {
        return Some(text.to_owned());
    }
    let base = cwd?;
    Some(std::path::Path::new(base).join(path).to_string_lossy().into_owned())
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

/// 버전 판(`version`)의 줄 — **서버 줄과 클라 줄이 따로 선다**(§10-21ⓐ3).
///
/// # 왜 두 줄인가
///
/// 종전에는 서버가 지은 한 줄뿐이라 **어느 이진을 보고 있는지가 화면에 없었다.** 그리고
/// 서버와 클라는 다른 OS 일 수 있다(원격 attach·페더레이션) — 한 줄에 섞으면 그 차이가
/// 사라지고, 두 줄이면 그대로 드러난다.
///
/// 모양은 정본 `_show_version_popup` 과 같다(머리줄 · 클라 · 서버 · 빈 줄 · pid). 업타임
/// 표기도 정본 `fmt_uptime` 과 같은 `1d 02:03:04` 다 — 나란히 놓고 보는 판이라 한쪽만
/// 다른 단위면 사람이 매번 환산해야 한다.
pub fn version_lines(state: &SessionState) -> Vec<String> {
    let client = format!("{} · {}", base::build::NAME, base::build::os_label());
    let mut lines = vec![
        t("pytmux 버전 / 업타임").to_owned(),
        String::new(),
        tf(
            "  클라이언트  {ver}  업타임 {up}",
            &[
                ("ver", client.as_str()),
                ("up", base::build::fmt_uptime(base::build::uptime().as_secs_f64()).as_str()),
            ],
        ),
    ];
    match state.version_reply() {
        // 서버 업타임은 회신 시점의 값이다 — 판이 떠 있는 동안 늘어야 정본과 같다.
        Some(reply) => {
            let up = base::build::fmt_uptime(reply.uptime + reply.received.elapsed().as_secs_f64());
            lines.push(tf(
                "  서버        {ver}  업타임 {up}",
                &[("ver", strip_p4(&reply.version).as_str()), ("up", up.as_str())],
            ));
            lines.push(String::new());
            lines.push(tf("  (서버 pid {pid})", &[("pid", reply.pid.to_string().as_str())]));
        }
        None => lines.push(tf("  서버        {ver}", &[("ver", t(WAITING))])),
    }
    lines.push(String::new());
    // 받아야 할 파일 이름 그대로 — 화면에 뜬 이름으로 `build/` 에서 바로 찾는다.
    lines.push(tf("  (빌드 {name})", &[("name", base::build::artifact().as_str())]));
    lines
}

/// `p4:70135` → `70135`. 정본 `_show_version_popup._cl` 과 같은 규칙이고, 그 밖의
/// 형식은 손대지 않는다(모르는 형식을 자르면 버전이 거짓이 된다).
fn strip_p4(version: &str) -> String {
    version.strip_prefix("p4:").unwrap_or(version).to_owned()
}

/// 재시작 점검 판의 줄 — **판정을 사람 말로**(§10-21ⓓ3).
///
/// # 왜 바뀌었나
///
/// 종전에는 서버가 준 칸을 `키: 값` 으로 이름순 나열했다(`serialize_ok: true`). 그런데
/// **그 값의 해석은 이미 코드에 있다** — `base::restart::evaluate` 가 같은 칸으로
/// `(안전한가, 줄들)` 을 만들고, 드라이런 게이트가 그것으로 재시작을 통과시킨다. 판만
/// 날 값을 보이고 있었으니, 사용자는 화면을 읽고도 "그래서 되나"를 알 수 없었다.
///
/// 모양은 정본 `_show_restart_check_popup` 과 같다(판정 한 줄 · PASS/FAIL 표 · 버전 · 주석).
///
/// `(안전한가, 줄들)` 을 함께 돌려주는 이유: 뷰가 단추를 **켤지 끌지**를 같은 판정으로
/// 정해야 한다. 뷰가 다시 재면 판의 글과 단추가 어긋날 수 있다.
/// 자동 재개 판의 줄들 — **정본 `open_autoresume_info` 와 같은 글**(pytmux-183).
///
/// # 왜 뷰가 아니라 여기서 짓나
///
/// 이 저장소의 규율이다(`restart_check_lines` 와 같은 자리): 줄을 짓는 것은 판정이라
/// proto 가 하고 뷰는 그리기만 한다. 뷰가 지으면 두 클라가 같은 상태를 다르게 설명하기
/// 시작한다 — 그리고 그 갈림은 **판을 열어 보기 전에는 아무도 모른다**.
///
/// 글은 정본 카탈로그의 `ar.*` 를 그대로 옮긴 것이다(`pytmuxlib/i18n.py`).
pub fn autoresume_lines(state: &SessionState) -> Vec<String> {
    use base::i18n::t;
    let on = state.flags().autoresume;
    // ⚠ **끝값 둘을 문장 통째로 둔다.** 상태말(`켜짐`)과 방향말(`끄기`)을 인자로 끼우면
    //    ⑴ 그 낱말만 한국어로 남고(이 저장소가 2026-08-02p 에 배운 자리 — `en_gui.rs`
    //    머리말이 같은 규칙을 적어 두었다) ⑵ `켜기`·`끄기` 는 이미 카탈로그에 **다른
    //    영어**(`On`/`Off`, 홀로 서는 라벨)로 들어 있어 키가 부딪힌다. 줄이 둘로 늘어도
    //    그쪽이 옳다.
    vec![
        if on {
            t("자동 재개(AR)이 현재 켜짐(ON) 입니다.").to_owned()
        } else {
            t("자동 재개(AR)이 현재 꺼짐(OFF) 입니다.").to_owned()
        },
        String::new(),
        t("• Claude 가 5시간 사용 한도로 멈추면, 리셋 시각 직후 자동으로").to_owned(),
        t("  작업을 이어갑니다('continue' 입력을 그 패널에 주입).").to_owned(),
        t("• 활성 패널 기준으로 켜고 끕니다(단축키 prefix+R 과 동일).").to_owned(),
        String::new(),
        if on {
            t("[a] AR 끄기   ·   닫기: Esc 또는 바깥 클릭.").to_owned()
        } else {
            t("[a] AR 켜기   ·   닫기: Esc 또는 바깥 클릭.").to_owned()
        },
    ]
}

pub fn restart_check_lines(state: &SessionState) -> (bool, Vec<String>) {
    use base::restart;
    if !state.has_restart_check() {
        return (false, vec![t(WAITING).to_owned()]);
    }
    let (safe, rows) = restart::evaluate(
        state.restart_probe(),
        restart::relaunch_ok(),
        restart::Kind::All,
    );
    let mut lines = vec![
        if safe { t("✅ 안전 — 지금 재시작할 수 있다") } else { t("⚠️ 주의 — 아래 FAIL 을 확인할 것") }
            .to_owned(),
        String::new(),
    ];
    for (ok, label) in &rows {
        lines.push(tf(
            "  [{res}] {label}",
            &[("res", if *ok { "PASS" } else { "FAIL" }), ("label", label)],
        ));
    }
    let field = |key: &str| {
        state
            .restart_check_field(key)
            .and_then(|v| v.as_str().map(str::to_owned))
            .unwrap_or_default()
    };
    let (run, disk) = (field("running_version"), field("disk_version"));
    if !run.is_empty() {
        lines.push(String::new());
        lines.push(tf(
            "  서버 버전: 실행={run}  디스크={disk}",
            &[("run", strip_p4(&run).as_str()), ("disk", strip_p4(&disk).as_str())],
        ));
        lines.push(
            if run == disk { t("  (동일)") } else { t("  → 재시작 시 갱신됨") }.to_owned(),
        );
    }
    let err = field("serialize_err");
    if !err.is_empty() {
        lines.push(tf("        직렬화 오류: {err}", &[("err", err.as_str())]));
    }
    lines.push(String::new());
    lines.push(t("  (버전 차이는 위험이 아니라 '재시작이 새 코드를 로드'를 뜻함)").to_owned());
    (safe, lines)
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

    /// 회신 전에도 판이 **클라 줄을 이미 안다** — 서버를 기다려 여는 판이 아니다.
    #[test]
    fn the_version_panel_names_this_binary_before_the_server_answers() {
        let lines = version_lines(&SessionState::new());
        let joined = lines.join("\n");
        assert!(joined.contains(base::build::NAME), "클라 이름이 없다: {lines:?}");
        assert!(joined.contains(base::build::os_label()), "OS 가 없다: {lines:?}");
        // ★ 받아야 할 파일 이름 그대로 — 이 줄이 빠지면 "어느 이진인가"가 다시 사라진다.
        assert!(joined.contains(&base::build::artifact()), "배포 이름이 없다: {lines:?}");
        assert!(joined.contains(WAITING), "서버 줄이 침묵한다: {lines:?}");
    }

    /// 서버 줄과 클라 줄은 **따로** 선다 — 한 줄에 섞으면 둘의 OS 가 다를 때 그 차이가
    /// 사라진다(원격 attach·페더레이션).
    #[test]
    fn the_server_line_is_separate_and_strips_the_p4_prefix() {
        let mut state = SessionState::new();
        state.apply(
            serde_json::from_value(serde_json::json!({
                "t": "version", "version": "p4:70135", "uptime": 3723.0, "pid": 4242
            }))
            .unwrap(),
        );
        let lines = version_lines(&state);
        let server = lines.iter().find(|l| l.contains("서버")).expect("서버 줄");
        assert!(server.contains("70135"), "p4 접두를 안 뗐다: {server}");
        assert!(!server.contains("p4:"), "{server}");
        // 정본과 같은 업타임 모양(회신 직후라 초 자리만 흔들린다).
        assert!(server.contains("01:02:0"), "업타임 모양이 정본과 다르다: {server}");
        assert!(lines.iter().any(|l| l.contains("4242")), "pid 가 없다: {lines:?}");
        // 그리고 클라 줄이 그대로 남아 있다(서버 회신이 그것을 밀어내지 않는다).
        assert!(lines.iter().any(|l| l.contains(base::build::NAME)), "{lines:?}");
    }

    fn restart_check(extra: serde_json::Value) -> crate::ServerMessage {
        let mut obj = serde_json::json!({
            "t": "restart_check", "reexec_supported": true, "has_sessions": true,
            "serialize_ok": true, "panes": 2, "panes_with_fd": 2,
            "running_version": "p4:70135", "disk_version": "p4:70135",
        });
        for (k, v) in extra.as_object().unwrap() {
            obj[k] = v.clone();
        }
        serde_json::from_value(obj).unwrap()
    }

    /// ★ §10-21ⓓ3 — 판이 **판정**을 말한다.
    ///
    /// 종전에는 `serialize_ok: true` 같은 날 값을 이름순으로 늘어놓았다. 그 해석은 이미
    /// 코드에 있었는데(드라이런 게이트가 그것으로 재시작을 막는다) 판만 안 쓰고 있었다.
    #[test]
    fn the_restart_panel_says_whether_it_is_safe_not_just_the_raw_fields() {
        let mut state = SessionState::new();
        state.apply(restart_check(serde_json::json!({})));
        let (safe, lines) = restart_check_lines(&state);
        assert!(safe, "{lines:?}");
        let joined = lines.join("\n");
        assert!(joined.contains("안전"), "판정 줄이 없다: {lines:?}");
        assert!(joined.contains("[PASS]"), "표가 없다: {lines:?}");
        assert!(!joined.contains("serialize_ok"), "날 값이 남았다: {lines:?}");
        // 버전 줄과 그 해설(같으면 '동일')도 정본과 같은 자리에 선다.
        assert!(joined.contains("70135"), "{lines:?}");
        assert!(joined.contains("(동일)"), "{lines:?}");
    }

    /// 실패하면 **무엇이 실패했는지** 그 자리에 있어야 한다 — 단추를 흐리게만 두면
    /// 사용자는 왜 못 하는지 알 수 없다.
    #[test]
    fn an_unsafe_check_names_the_failing_row_and_is_not_safe() {
        let mut state = SessionState::new();
        state.apply(restart_check(serde_json::json!({
            "serialize_ok": false, "serialize_err": "그림자 객체",
            "disk_version": "p4:70200",
        })));
        let (safe, lines) = restart_check_lines(&state);
        assert!(!safe, "{lines:?}");
        let joined = lines.join("\n");
        assert!(joined.contains("[FAIL]"), "{lines:?}");
        assert!(joined.contains("그림자 객체"), "직렬화 오류 글이 없다: {lines:?}");
        assert!(joined.contains("갱신됨"), "버전이 달라졌는데 안 적는다: {lines:?}");
    }

    /// 회신 전에는 **지어내지 않는다** — 빈 표를 보이면 "전부 실패"로 읽힌다.
    #[test]
    fn before_the_reply_the_restart_panel_says_it_is_waiting() {
        let (safe, lines) = restart_check_lines(&SessionState::new());
        assert!(!safe);
        assert_eq!(lines, vec![WAITING.to_owned()]);
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
