//! **서버가 지어 보내는 글**의 영어 번역 — 정본 카탈로그에서 뽑은 것(로케일 ⓐ).
//!
//! # 왜 이 파일만 손으로 안 적나
//!
//! 다른 `en_*.rs` 는 그 크레이트가 **자기 문자열**을 번역한 표다. 여기 있는 것은
//! 우리 문자열이 아니라 **서버가 지어 보낸 것**이고, 두 언어의 값이 이미 정본
//! 카탈로그(`pytmuxlib/i18n.py` + 플러그인 `register`)에 있다. 여기서 다시 번역하면
//! 그 순간 두 벌이 되고, 갈리는 순간 증상은 "한 화면만 한국어"다 — 그래서 값은
//! `scripts/gen_server_strings.py` 가 뽑은 픽스처와 **글자 단위로 같아야** 하고
//! `tests/server_strings_conformance.rs` 가 그것을 잰다.
//!
//! 갱신: `python3 scripts/gen_server_strings.py` 로 픽스처를 다시 뽑고, 게이트가
//! 가리키는 줄을 여기에 옮긴다.
//!
//! ⚠ 이 표는 `en_map()` 의 **마지막**에 접힌다 — 같은 한국어 원문이 다른 표에도
//! 있으면 여기가 이긴다. 서버가 보내는 글의 권위는 정본이기 때문이다(실측: `자동재개`
//! 를 우리 표는 `Auto-resume`, 정본은 `auto-resume` 로 적고 있었다).

pub static EN: &[(&str, &str)] = &[
    // ── claude-code · 토큰 판들의 **꼬리줄**(pytmux-371 · 글자 키 광고) ──
    //
    // ★ 이 다섯은 위·아래의 짧은 꼬리줄과 **원문이 다르다**: 뒤에 이 판이 무는 글자
    //   키가 붙어 있다(`p세션 · l한도 · o머신 · s시나리오 · u/usage`). 정본 토큰
    //   팝업이 같은 자리에 같은 것을 적고, 그 줄이 곧 「이 판에서 무엇을 할 수 있나」의
    //   전부라 영어로 안 뜨면 조작이 통째로 안 읽힌다.
    // ⛔ 짧은 원문(글자 키가 없는 것)을 지우면 안 된다 — 다른 플러그인 판들이 아직
    //    그 꼬리줄을 쓴다(`mdir`·`ncd` 등). 둘 다 표에 있어야 한다.
    (
        "↑↓ 이동 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ move · Esc close · p session · l limit · o machine · s scenario · u /usage",
    ),
    (
        "↑↓ 스크롤 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ scroll · Esc close · p session · l limit · o machine · s scenario · u /usage",
    ),
    (
        // ⚠ 기간 판이 **계층 트리**가 되면서 버킷 고르개가 사라졌다(pytmux-371 ①) —
        //   꼬리줄도 그 손을 적는다. 없어진 조작을 광고하면 그것도 거짓말이다.
        "↑↓ 이동 · Enter/←→ 펼침·접힘 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ move · Enter/←→ expand·collapse · Esc close · p session · l limit · \
         o machine · s scenario · u /usage",
    ),
    (
        "↑↓ 이동 · Enter 날짜 펼침·접힘 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ move · Enter expand/collapse a day · Esc close · p session · l limit · \
         o machine · s scenario · u /usage",
    ),
    (
        "↑↓ 이동 · Enter 적용(/model 주입) · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ move · Enter apply (injects /model) · Esc close · p session · l limit · \
         o machine · s scenario · u /usage",
    ),
    // ── claude-code · 기간별·세션별 판(pytmux-371 ①②) ──
    ("토큰 사용량 · 기간별", "Token usage · by period"),
    ("토큰 사용량 · 세션별", "Token usage · by session"),
    (
        "↑↓ 이동 · Enter 로 기간 단위 고르기 · Esc 닫기",
        "↑↓ move · Enter to pick the period unit · Esc close",
    ),
    ("시간 단위로 보기", "Show by hour"),
    ("일 단위로 보기", "Show by day"),
    ("주 단위로 보기", "Show by week"),
    ("월 단위로 보기", "Show by month"),
    ("기간별 →", "By period →"),
    ("세션별 →", "By session →"),
    // ── claude-code · 경고 이력 판(pytmux-371 ⑤) + 판을 잇는 줄 ──
    ("Claude 경고 이력", "Claude warning history"),
    ("Claude 경고 이력 →", "Claude warning history →"),
    ("↑↓ 이동 · Enter 날짜 펼침·접힘 · Esc 닫기",
     "↑↓ move · Enter expand/collapse a day · Esc close"),
    ("쌓인 Claude 경고가 없습니다.", "No Claude warnings recorded."),
    (
        "이 서버는 경고 이력을 안 쌓습니다(claude-code 플러그인 필요).",
        "This server keeps no warning history (needs the claude-code plugin).",
    ),
    ("머신별 총계 →", "By machine →"),
    ("일별 집계 →", "Daily totals →"),
    // ── claude-code · 판을 잇는 줄(pytmux-371 ④) ──
    ("모델·컨텍스트 고르기 →", "Pick model/context →"),
    ("한도(/usage) 보기 →", "Show limits (/usage) →"),
    // ── claude-code · 띠 끝의 액션 배지 = ⑥ 자동재개 설정(pytmux-371 ⑥) ──
    ("시나리오 설정 →", "Scenario settings →"),
    // ── claude-code · 머신별 토큰 판(pytmux-371 ③) ──
    ("토큰 사용량 · 머신별 (Σ{tok})", "Token usage · by machine (Σ{tok})"),
    (
        "아직 다른 머신에서 온 기록이 없습니다(동기화를 켜면 채워집니다).",
        "No records from other machines yet (turn sync on to fill this).",
    ),
    ("  ◀ 현재", "  ◀ current"),
    (" …", " …"),
    (" ⏎", " ⏎"),
    (" ⏳ {label} {eta}s(입력=취소) ", " ⏳ {label} {eta}s (input=cancel) "),
    (" ⏳ 자동재개 {eta}s(입력=취소) ", " ⏳ auto-resume {eta}s (input=cancel) "),
    // ⚠ **`mdir` 의 판 넷은 지금 두 벌이 있다.** 아래 셋(F10 없는 안내 · 빈 디렉터리 ·
    //    항목 초과)이 지금 정본이 «실제로 보내는» 것이고, `F10 트리` 가 든 안내와
    //    `… · {counts}` 판은 CL 71589·71578 이 냈다가 **CL 71673 이 되돌려 버린** 세상의
    //    것이다(그 CL 이 낡은 워크스페이스에서 제출돼 둘을 덮었다). 그동안 픽스처가
    //    낡은 채라 이 게이트는 «초록인 채로» 그 사실을 가리고 있었다.
    //    ⛔ 옛 판을 지우지 마라 — pytmux-125 가 그 F-키를 되살리면 곧 다시 필요하다.
    ("(Enter 열기 · . 상위 · t 태그 · u 전체태그 · c 복사 · m 이동 · d 삭제 · r 이름 · k 새 디렉터리 · v 보기 · h 숨김 · p 패널 cd · Esc 닫기)", "(Enter open · . up · t tag · u tag all · c copy · m move · d delete · r rename · k new directory · v view · h hidden · p cd panel · Esc close)"),
    ("(Enter 열기 · . 상위 · t 태그 · u 전체태그 · c 복사 · m 이동 · d 삭제 · r 이름 · k 새 디렉터리 · v 보기 · h 숨김 · p 패널 cd · F10 트리 · Esc 닫기)", "(Enter open · . up · t tag · u tag all · c copy · m move · d delete · r rename · k new directory · v view · h hidden · p cd panel · F10 tree · Esc close)"),
    ("(↑↓ 스크롤 · Esc 닫기)", "(↑↓ scroll · Esc close)"),
    ("(규칙이 없습니다 — a 로 지금 패널의 디렉토리를 추가합니다)", "(no rules — press a to add this pane's directory)"),
    ("(내용 없음)", "(empty)"),
    ("(리셋 시각을 파싱할 수 없음)", "(cannot parse reset time)"),
    ("(저장된 프롬프트가 없습니다 — Claude 에 프롬프트를 입력해 보세요)", "(no saved prompts — type a prompt into Claude first)"),
    ("(제출된 체인지리스트가 없습니다)", "(no submitted changelists)"),
    ("(지금은 비어 있습니다. 빈 채로 저장하면 지웁니다.)", "(empty for now. Saving it empty clears the rules.)"),
    ("(큐 비어 있음)", "(queue empty)"),
    ("(큐가 비어 있습니다 — `:prompt-clear-queue <명령>` 으로 쌓습니다)", "(the queue is empty — add with `:prompt-clear-queue <command>`)"),
    ("/usage 한도 데이터 없음 — Claude 패널에서 /usage 를 먼저 실행", "No /usage limit data — run /usage in a Claude panel first"),
    ("5h 최대", "5h peak"),
    ("<드라이브>", "<DRIVE>"),
    ("<상위>", "<UP>"),
    ("?%/5h 사용", "?%/5h used"),
    ("Claude fullscreen 이 꺼져 있습니다 — 스크롤 프롬프트 바와 «클릭해서 점프»가 안 뜹니다. claude 에서 /tui fullscreen 으로 되살리세요(claude {ver} · {when} · 스트라이크 {strikes})",
     "Claude's fullscreen renderer is off — the scrolled-prompt bar and click-to-jump are gone. Run /tui fullscreen in claude to bring it back (claude {ver} · {when} · {strikes} strikes)"),
    ("Claude 모델·컨텍스트", "Claude model/context"),
    ("Claude 사용 한도 (/usage)", "Claude usage limit (/usage)"),
    ("Claude 설정", "Claude settings"),
    ("Claude 시작 규칙 — 새 세션·/clear 뒤 자동 주입", "Claude start rules — injected after a new session/clear"),
    ("Enter 키워드 · p 경로 · a 추가 · d 삭제 · Esc 닫기", "Enter keyword · p path · a add · d delete · Esc close"),
    ("accept — 편집·기본 FS 만 자동 수락 (⏵⏵ accept edits)", "accept — auto-accept edits·basic FS only (⏵⏵ accept edits)"),
    ("auto — 모든 동작 자동 수락, 안전검사 (⏵⏵ auto mode)", "auto — auto-accept all, safety checks (⏵⏵ auto mode)"),
    ("bypass — 권한 우회, 확인 없음 ⚠️ (Bypass Permission Mode)", "bypass — skip permissions, no confirm ⚠️ (Bypass Permission Mode)"),
    ("c 비우기 · Esc 닫기", "c clear · Esc close"),
    ("default — 매번 확인 (일반 모드)", "default — confirm each time (normal)"),
    ("plan — 플랜 모드 (계획만, 실행 안 함)", "plan — plan mode (plan only, no run)"),
    ("r 원격 제어 토글(/rc) · ↑↓ 스크롤 · Esc 닫기", "r toggle remote control (/rc) · ↑↓ scroll · Esc close"),
    ("remote-attach {target} 실패 — {why}", "remote-attach {target} failed — {why}"),
    ("remote-attach {target}: 연결됐지만 원격이 응답 없음 — 원격 서버 점검", "remote-attach {target}: connected but remote is unresponsive — check the remote server"),
    ("remote-attach {target}: 원격 탭 병합됨", "remote-attach {target}: remote tab merged"),
    ("remote-new-tab {target} 실패 — {why}", "remote-new-tab {target} failed — {why}"),
    ("submitted changelists", "submitted changelists"),
    // mdir 집계줄(pytmux-126) — `{counts}` 는 원조 Mdir III 의 서식이라
    // 번역 대상이 아니다(색과 같은 부류). 로케일을 타는 것은 그 앞뒤의 말뿐이다.
    ("{counts}", "{counts}"),
    ("{counts}  (항목 일부만 표시)", "{counts}  (list truncated)"),
    ("{names} 외 {n}개", "{names} and {n} more"),
    ("{n}개를 지웁니다 — 되돌릴 수 없습니다", "Deleting {n} — this cannot be undone"),
    ("{pct}%/5h 사용", "{pct}%/5h used"),
    ("{pct}%/주(Sonnet)", "{pct}%/wk(Sonnet)"),
    ("↑↓ 스크롤 · Esc 목록으로", "↑↓ scroll · Esc back to the list"),
    ("↑↓ 스크롤 · PgUp/PgDn · Home/End · Esc 닫기", "↑↓ scroll · PgUp/PgDn · Home/End · Esc close"),
    ("↑↓ 이동 · Enter 그 위치로 점프 · Esc 닫기", "↑↓ move · Enter jump to position · Esc close"),
    ("↑↓ 이동 · Enter 바꾸기 · Esc 닫기", "↑↓ move · Enter change · Esc close"),
    ("↑↓ 이동 · Enter 상세 · Esc 닫기", "↑↓ move · Enter details · Esc close"),
    ("↑↓ 이동 · Enter 적용 · Esc 닫기", "↑↓ move · Enter apply · Esc close"),
    ("↑↓ 이동 · Enter 적용(/model 주입) · Esc 닫기", "↑↓ move · Enter apply (injects /model) · Esc close"),
    ("↑↓ 이동 · Esc 닫기", "↑↓ move · Esc close"),
    ("↻ 갱신 [u]", "↻ Refresh [u]"),
    ("▭ 패널 보기 [a]", "▭ Pane view [a]"),
    ("○", "○"),
    ("●", "●"),
    ("⚠ Claude 포맷 미인식 — 추적 중단(버전 업데이트?)", "⚠ Claude format unrecognized — tracking paused (version update?)"),
    ("⚠ 동일 결과 {n}회 반복 — 루프 의심", "⚠ Same output repeated {n}× — loop suspected"),
    ("⤢ 팝업/탭 [t]", "⤢ Popup/Tab [t]"),
    ("값이 비어 있어 아무것도 안 했습니다", "Empty value — nothing was done"),
    ("같은 이름이 {n}개 있습니다 — 덮어쓸까요?", "{n} names already exist — overwrite?"),
    ("같은 자리입니다", "Same place"),
    ("권한모드 선택 (현재: {current})", "Select permission mode (current: {current})"),
    ("규칙을 걸 디렉토리 경로", "Directory the rule applies to"),
    ("규칙을 저장했습니다", "Rule saved"),
    ("그 디렉토리에서 쓸 이름 키워드", "Name keyword to use in that directory"),
    ("그 프롬프트가 스크롤백에 없습니다(회전/재시작으로 사라짐)", "That prompt is no longer in scrollback (rotated out / restarted)"),
    ("기록된 토큰 사용량이 없습니다", "No token usage recorded"),
    ("깨짐감지", "on corruption"),
    ("끔", "off"),
    ("다음 리셋까지 ", "Until next reset "),
    ("다음 리셋까지 {left}", "Until next reset {left}"),
    ("대상을 안 적었습니다", "No destination was given"),
    ("대상이 디렉터리가 아닙니다", "The destination is not a directory"),
    ("대상이 비었습니다", "The destination is empty"),
    ("대상이 없습니다", "Nothing to work on"),
    ("디렉터리 — {path}", "Directory — {path}"),
    ("디렉터리는 덮어쓰지 않습니다", "Directories are not overwritten"),
    ("루트는 대상이 아닙니다", "The root is not a target"),
    ("모르는 조작입니다", "Unknown operation"),
    ("못 읽습니다: {err}", "Cannot read: {err}"),
    ("복사 {n}건", "Copied {n}"),
    ("복사 {n}건 · 실패 {f}건", "Copied {n} · {f} failed"),
    ("복사 — 대상 디렉터리 ({n}개)", "Copy — destination directory ({n})"),
    ("불러오는 중…", "Loading…"),
    ("빈 디렉터리입니다", "Empty directory"),
    ("빈 디렉터리입니다 · {counts}", "Empty directory · {counts}"),
    ("사용량 갱신 중… (숨은 /usage, ~수초)", "Refreshing usage… (hidden /usage, ~a few s)"),
    ("사용량 조회 중… (숨은 /usage, ~수초)", "Querying usage… (hidden /usage, ~a few sec)"),
    ("삭제 {n}건", "Deleted {n}"),
    ("삭제 {n}건 · 실패 {f}건", "Deleted {n} · {f} failed"),
    ("새 디렉터리 {n}건", "Created {n}"),
    ("새 디렉터리 {n}건 · 실패 {f}건", "Created {n} · {f} failed"),
    ("새 디렉터리 이름", "New directory name"),
    ("새 이름 — {name}", "New name — {name}"),
    ("시작 규칙 비움", "Start rules cleared"),
    ("시작 규칙 저장됨", "Start rules saved"),
    ("아무 곳", "anywhere"),
    ("앞부분만 보입니다(뒤는 잘렸습니다)", "Only the beginning is shown (the rest is cut)"),
    ("약하게", "weak"),
    ("엄격", "strict"),
    ("완료마다", "each turn"),
    ("원격 제어(Remote Control)", "Remote Control"),
    ("원격제어가 실제로 켜져 있어 정책 차단 래치를 해제합니다", "Remote control is actually on — clearing the policy-block latch"),
    ("원본이 없습니다", "The source is gone"),
    ("이 규칙을 지웁니다", "Delete this rule"),
    ("이 패널의 Claude Code 가 데스크탑 앱 '원격 제어'로 연결돼 있습니다.\n(패널 화면의 'Remote Control active' 표시)\n\n• 원격 제어는 Claude Code CLI 의 '/rc' 명령으로 켜고 끕니다.\n  → 이 화면에서 [r] 키로 바로 토글합니다(해당 패널에 /rc 주입).\n• 원격 제어로 입력된 프롬프트도 상단 프롬프트 헤더에 반영됩니다.\n\n[r] 원격 제어 토글(/rc)   ·   닫기: Esc 또는 바깥 클릭.", "This panel's Claude Code is connected to the desktop app's 'Remote Control'.\n(the panel shows 'Remote Control active')\n\n• Remote control is toggled with the Claude Code CLI '/rc' command.\n  → Press [r] here to toggle it directly (injects /rc into the panel).\n• Prompts entered via remote control also appear in the top prompt header.\n\n[r] Toggle remote control (/rc)   ·   close: Esc or click outside."),
    ("이동 {n}건", "Moved {n}"),
    ("이동 {n}건 · 실패 {f}건", "Moved {n} · {f} failed"),
    ("이동 — 대상 디렉터리 ({n}개)", "Move — destination directory ({n})"),
    ("이름 동기화 규칙", "Name sync rules"),
    ("이름 변경 {n}건", "Renamed {n}"),
    ("이름 변경 {n}건 · 실패 {f}건", "Renamed {n} · {f} failed"),
    ("이름 변경은 하나씩만 됩니다", "Rename takes one at a time"),
    ("이름에 쓸 수 없는 글자가 있습니다", "The name has characters that cannot be used"),
    ("이미 있습니다", "Already there"),
    ("이진 파일이라 안 보입니다", "Binary file — not shown"),
    ("읽기 실패: {err}", "Read failed: {err}"),
    ("자기 안으로는 못 옮깁니다", "Cannot move into itself"),
    ("자동재개", "auto-resume"),
    ("자동재개 억제: 방금 주입한 뒤라 건너뜀(패널 {pane})", "Auto-resume suppressed: injected too recently (pane {pane})"),
    ("자동재개 억제: 최근 5h 실사용 {used}토큰(<{need}) — 리밋 배너가 위조로 의심됨(패널 {pane}, claude-resume-verify {mode})", "Auto-resume suppressed: only {used} tokens used in the last 5h (<{need}) — limit banner looks forged (pane {pane}, claude-resume-verify {mode})"),
    ("자동재개: '{msg}' 주입(패널 {pane})", "Auto-resume: injected '{msg}' (pane {pane})"),
    ("조직 정책 메시지 관측 — /rc 자동 주입을 중단합니다(패널 {pane})", "Org policy message seen — stopping auto /rc injection (pane {pane})"),
    ("지금", "now"),
    ("지금 자리: {path}", "Now at: {path}"),
    ("초대 코드(이 값이 곧 키입니다 — 채팅·스크린샷 금지): {code}", "Invite code (this IS the key — never paste in chat): {code}"),
    ("큐 비움", "Queue cleared"),
    ("토큰", "tokens"),
    ("토큰 DB 를 열 수 없습니다", "Cannot open the token DB"),
    ("토큰 동기화 설정: {state}", "Token sync configured: {state}"),
    ("토큰 동기화 실패 — {why}", "Token sync failed — {why}"),
    ("토큰 동기화: {state} · 마지막 성공 {last} · 받은 행 {rows} · 계정 귀속 {acct} · 적재 {grow}", "Token sync: {state} · last ok {last} · rows in {rows} · account attributed {acct} · ingest {grow}"),
    ("토큰 동기화: 올림 {sent} · 받음 {merged} · 거부 {rejected}", "Token sync: pushed {sent} · merged {merged} · rejected {rejected}"),
    ("토큰 동기화: 이 머신을 등록했습니다({label})", "Token sync: this machine is enrolled ({label})"),
    ("토큰 사용량(추정) · 일별", "Token usage (estimated) · by day"),
    ("토큰 사용량(추정) · 일별 · Σ{tok}", "Token usage (estimated) · by day · Σ{tok}"),
    ("파일 관리자 — {path}", "File manager — {path}"),
    ("파일 관리자 — {path}  [{mask}]", "File manager — {path}  [{mask}]"),
    // mdir 파일 마스크(pytmux-12 C) — 빈 값이 곧 끄기다(거는 것과 푸는 것이 한 키).
    ("파일 마스크 (예: *.txt *.md · 빈 값이면 해제)", "File mask (e.g. *.txt *.md · empty clears)"),
    ("표시할 Claude 경고가 없습니다(이미 해소됨).", "No Claude warning to show (already cleared)."),
    ("프롬프트 단위 클리어 큐", "Per-prompt clear queue"),
    ("프롬프트 클리어 큐", "Prompt-clear queue"),
    ("프롬프트 히스토리", "Prompt history"),
    ("항목이 너무 많아 일부만 보입니다", "Too many entries — showing only some"),
    ("한도 데이터 없음 — Claude 패널에서 /usage 실행 후 [u]로 갱신", "No limit data — run /usage in a Claude pane, then [u] to refresh"),
    ("한도 데이터 없음 — Claude 패널에서 /usage 실행 후 갱신", "No limit data — run /usage in a Claude pane to refresh"),
];
