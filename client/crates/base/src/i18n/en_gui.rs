//! `gui` 문자열의 영어 번역(규약은 `en_core.rs` 머리말).
//!
//! GUI 뷰의 문구는 대부분 TUI 와 **같은 원문**이라 번역이 이미 `en_tui.rs` 에 있다
//! (중복 키 금지 — 첫 등장 파일에만 둔다). 여기에는 GUI 에만 있는 원문이 남는다.

pub static EN: &[(&str, &str)] = &[
    // 플러그인 표면(Tier A) — 아직 화면이 없는 기여를 눌렀을 때.
    (
        "플러그인 명령 {name} 은 이 클라에 아직 화면이 없습니다 — 터미널 클라(pytmux)에서 쓰세요",
        "Plugin command {name} has no screen in this client yet — use the terminal client (pytmux)",
    ),
    // ── root_view.rs — 블록 데모 창 제목 줄(TUI 데모와 같은 원문 — 여기가 주인) ──
    ("pytmux-gui · 블록 데모", "pytmux-gui · block demo"),
    // ── session_view.rs — 글자 배율 한 마디(§10-21ⓐ) ──
    // ⚠ 끝값 둘을 **문장 통째로** 둔다. 방향("키울 수"/"줄일 수")을 인자로 넘기면 그
    //   낱말만 한국어로 남는다(2026-08-02p 에서 배운 자리 — 사유를 줄에 이어 붙이지
    //   않는다). 줄이 둘로 늘어도 그쪽이 옳다.
    ("글자 크기: {scale}×", "Text size: {scale}×"),
    ("글자 크기: {scale}× — 더 키울 수 없다", "Text size: {scale}× — cannot go larger"),
    ("글자 크기: {scale}× — 더 줄일 수 없다", "Text size: {scale}× — cannot go smaller"),
    // 트리 판이 개요를 기다리는 동안의 한 줄(§10-21ⓖ2 — 프레임 오라클이 잡았다).
    ("개요를 기다리는 중…", "Waiting for the overview…"),
    ("첫 화면을 기다리는 중…", "Waiting for the first frame…"),
    ("맞는 명령이 없다", "No matching command"),
    ("아직 알림이 없다", "No notices yet"),
    ("버퍼가 없다", "No buffers"),
    ("(탭 없음)", "(no tabs)"),
    // §10-21ⓓ3 — 재시작 점검 판의 단추.
    ("지금 재시작 (restart-all)", "Restart now (restart-all)"),
    // 블록 선택 모드(pytmux-18) — 고를 것이 없을 때의 한 마디. **이유까지** 적는다:
    // "블록이 없다"만으로는 사용자가 제품 결함으로 읽는다(원인은 셸 통합이다).
    (
        "이 패널에는 블록이 없다 — 셸 통합(OSC 133)이 명령 경계를 알려 줘야 생긴다",
        "This pane has no blocks — they need shell integration (OSC 133) to mark command boundaries",
    ),
    // Claude 패널에서는 **이유가 다르다**(pytmux-21). 경계가 OSC 133 이 아니라 프롬프트
    // 마커에서 오므로, 여기서 위 문구를 쓰면 고칠 수 없는 것을 고치라는 안내가 된다.
    (
        "이 패널에는 아직 고를 턴이 없다 — 프롬프트를 한 번 보내면 턴 단위로 골라진다",
        "No turns to pick in this pane yet — send a prompt once and turns become selectable",
    ),
    // ─────────────────────────────────────────────────────────────────────────
    // §10-24(pytmux-36) — 소스 스캔이 세던 나머지. 대부분 **오류·지나가는 말**이라
    // 영어로 열어 놓고 화면을 훑는 프레임 오라클이 닿지 못하던 자리다(그 오류를
    // 실제로 일으켜야 화면에 오른다). 그래서 en 사용자는 **뭔가 잘못된 순간에만**
    // 한국어를 봤다 — 무엇이 왜 실패했는지 읽어야 하는 바로 그때.
    // ─────────────────────────────────────────────────────────────────────────
    // ── console.rs — 창도 못 띄운 실패의 대화상자 제목(Windows) ──
    ("pytmux 를 시작하지 못했습니다", "Could not start pytmux"),
    // ── session_view.rs · 설정을 만졌을 때 ──
    ("설정을 다시 읽었다", "Reloaded the config"),
    ("설정을 저장하지 못했다: {err}", "Could not save the config: {err}"),
    ("설정 형식이 아니다: {line}", "Not a config line: {line}"),
    ("바인딩을 저장하지 못했다: {err}", "Could not save the binding: {err}"),
    ("읽을 수 없는 키 표기: {answer}", "Unreadable key notation: {answer}"),
    // ── session_view.rs · 원격 탭 합치기(G8n) ──
    ("지금 탭이 원격이 아니라 합칠 것이 없다", "This tab is not remote — nothing to merge"),
    ("같은 원격에 다른 탭이 없다", "No other tab on the same remote"),
    ("분할 방향: {dir}", "Split direction: {dir}"),
    // ⚠ 끝값 둘은 `분할 방향: {dir}` 에 끼워지는 낱말이다. 낱말만 한국어로 남으면 그
    //   줄은 반만 영어다 — 명령 도움말(`패널 분할 (-h 좌우 │ · -v/기본 상하 ─)`)이
    //   이미 쓰는 그 낱말로 맞춘다.
    ("좌우 │", "side-by-side │"),
    ("상하 ─", "stacked ─"),
    // ── session_view.rs · 조각을 눌렀을 때(링크·경로) ──
    ("링크를 열었다: {url}", "Opened the link: {url}"),
    ("링크를 열지 못했다: {url}", "Could not open the link: {url}"),
    ("경로를 복사했다: {path}", "Copied the path: {path}"),
    ("경로를 복사하지 못했다: {path}", "Could not copy the path: {path}"),
    // ── session_view.rs · 재시작(§10-21ⓓ3) ──
    (
        "재시작 안전성 점검 중… (부작용 없는 드라이런)",
        "Checking restart safety… (dry run, no side effects)",
    ),
    (
        "전체 재시작 — 클라를 다시 띄웠다 (pid {pid})",
        "Full restart — relaunched the client (pid {pid})",
    ),
    ("전체 재시작 취소: {why}", "Full restart cancelled: {why}"),
    ("서버를 재시작한다 (셸은 산다)", "Restarting the server (shells survive)"),
    ("다시 붙었다: {socket}", "Reattached: {socket}"),
    ("다시 붙지 못했다: {err}", "Could not reattach: {err}"),
    // ── session_view.rs · if-shell/run-shell 이 끝났을 때 ──
    ("조건이 실패했다(코드 {code})", "The condition failed (code {code})"),
    ("끝났다(코드 {code})", "Finished (code {code})"),
    ("모르는 명령이다: {name}", "Unknown command: {name}"),
    ("조건과 명령을 `|` 로 갈라 적는다", "Write the condition and the command separated by `|`"),
    // ── session_view.rs · set-hook 한 줄 ──
    ("훅 {event} → {command}", "Hook {event} → {command}"),
    ("훅 {event} 를 풀었다", "Unset hook {event}"),
    ("그런 훅이 없다", "No such hook"),
    (
        "훅은 <이벤트> <명령> 또는 -u <이벤트> 로 적는다",
        "Write a hook as <event> <command>, or -u <event>",
    ),
    // ── session_view.rs · 쓰는 법을 되돌려 주는 한 줄들 ──
    // 명령 이름(`display-popup`)과 플래그는 그대로 두고 **설명과 자리표만** 옮긴다.
    (
        "display-popup [-w N] [-h N] <명령> · 닫기는 -C",
        "display-popup [-w N] [-h N] <command> · close with -C",
    ),
    // 팔레트에서 인자를 **이어 칠 수 있다**는 힌트. `{q}` 는 물음 판이 쓰는 그 문구다.
    ("{q} (이어서 치기)", "{q} (type it inline)"),
    // ── session_view.rs · 보일 것이 없을 때 ──
    (
        "서버가 플러그인 목록을 안 보냈다 (옛 서버이거나 아직 첫 full status 전)",
        "The server sent no plugin list (an old server, or before the first full status)",
    ),
    // `막힌 호출` = "Denied call"(en_claude.rs)에 맞춘다 — 같은 판의 두 낱말이다.
    ("보여 줄 플랜도 거부도 없다", "No plan or denied call to show"),
    ("… (잘림)", "… (truncated)"),
    // ── session_view.rs · 아직 못 그리는 화면 모양(설계 §8-5) ──
    // 이 파일 맨 위의 플러그인 한 줄과 **같은 문형**으로 둔다 — 두 막다른 길이 다른
    // 말을 하면 사용자는 둘을 다른 고장으로 읽는다.
    (
        "이 화면 모양({kind})은 이 클라에서 아직 못 그립니다 — 터미널 클라(pytmux)에서 쓰세요",
        "This screen kind ({kind}) cannot be drawn in this client yet — use the terminal client (pytmux)",
    ),
    // ── session_view.rs — render_search_results(pytmux-27) ──
    ("검색 결과가 없다", "No search results"),
];
