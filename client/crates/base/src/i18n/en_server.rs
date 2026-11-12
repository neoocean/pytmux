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
        "↑↓ 이동 · Tab/←→ 탭 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ move · Tab/←→ tabs · Esc close · p session · l limit · o machine · s scenario · u /usage",
    ),
    (
        // 띠 밖의 판(`claude-token-usage-view` 등)이 아직 쓰는 종전 꼬리줄 — 그대로 둔다.
        "↑↓ 이동 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ move · Esc close · p session · l limit · o machine · s scenario · u /usage",
    ),
    (
        "↑↓ 스크롤 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ scroll · Esc close · p session · l limit · o machine · s scenario · u /usage",
    ),
    (
        // ⚠ `[한도]` 판의 꼬리줄이 갈라졌다(pytmux-130) — 그 판의 맨 위 두 줄이
        //   **모델·컨텍스트 고르개**라 `←→`·`Enter` 가 그 안의 뜻을 갖는다. 위 짧은
        //   원문은 그것을 안 말하므로 지우지 않고 둘 다 표에 둔다(머리말의 그 규율).
        "↑↓ 이동(모델·컨텍스트) · ←→ 값 · Tab 탭 · Enter 적용 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ move (model·context) · ←→ value · Tab tabs · Enter apply · Esc close · p session · \
         l limit · o machine · s scenario · u /usage",
    ),
    (
        // ⚠ 기간 판이 **계층 트리**가 되면서 버킷 고르개가 사라졌다(pytmux-371 ①) —
        //   꼬리줄도 그 손을 적는다. 없어진 조작을 광고하면 그것도 거짓말이다.
        "↑↓ 이동 · Enter/←→ 펼침·접힘 · Tab 탭 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ move · Enter/←→ expand·collapse · Tab tabs · Esc close · p session · l limit · \
         o machine · s scenario · u /usage",
    ),
    (
        "↑↓ 이동 · Enter 날짜 펼침·접힘 · Tab/←→ 탭 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ move · Enter expand/collapse a day · Tab/←→ tabs · Esc close · p session · l limit · \
         o machine · s scenario · u /usage",
    ),
    (
        "↑↓ 이동 · Enter 적용(/model 주입) · Tab 탭 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "↑↓ move · Enter apply (injects /model) · Tab tabs · Esc close · p session · l limit · \
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
    // ── claude-code · 계층 트리가 **글자를 지을 때** 쓰는 말들(pytmux-419) ──
    //
    // ★ 이 다섯이 여기 없었던 이유가 곧 결함이었다: 정본 쪽 원문이 `screens.py`
    //   (Textual) 카탈로그에만 있어서 `gen_server_strings.py` 의 그물에 안 걸렸다.
    //   그물은 **서버가 무는 모듈**을 훑는데 서버는 그 파일을 안 읽는다. 그래서
    //   ⑴ 서버가 `i18n.t` 로 부르면 **키가 그대로** 나왔고(`pscreen.weekdays` 는
    //   `split(",")` 가 원소 하나라 기간 탭이 `IndexError` 로 아예 안 떴다)
    //   ⑵ 설령 떴어도 이 표에 없으니 **영어 사용자에게 한국어로** 보였다.
    //   원문을 `__init__.py` 로 옮겨 둘 다 고쳤고, 여기는 그 ⑵ 쪽이다.
    ("월,화,수,목,금,토,일", "Mo,Tu,We,Th,Fr,Sa,Su"),
    ("시", "h"),
    ("── 이번 달 이전 주 ──", "── earlier weeks this month ──"),
    ("── 이전 달 ──", "── earlier months ──"),
    ("이 머신", "this machine"),
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
    // ⚠ **`mdir` 의 판 넷은 지금 두 벌이 있다.** 아래 셋(F10 없는 안내 · 빈 디렉터리 ·
    //    항목 초과)이 지금 정본이 «실제로 보내는» 것이고, `F10 트리` 가 든 안내와
    //    `… · {counts}` 판은 CL 71589·71578 이 냈다가 **CL 71673 이 되돌려 버린** 세상의
    //    것이다(그 CL 이 낡은 워크스페이스에서 제출돼 둘을 덮었다). 그동안 픽스처가
    //    낡은 채라 이 게이트는 «초록인 채로» 그 사실을 가리고 있었다.
    //    ⛔ 옛 판을 지우지 마라 — pytmux-125 가 그 F-키를 되살리면 곧 다시 필요하다.
    ("(Enter 열기 · . 상위 · t 태그 · u 전체태그 · c 복사 · m 이동 · d 삭제 · r 이름 · k 새 디렉터리 · v 보기 · h 숨김 · p 패널 cd · Esc 닫기)", "(Enter open · . up · t tag · u tag all · c copy · m move · d delete · r rename · k new directory · v view · h hidden · p cd panel · Esc close)"),
    ("(Enter 열기 · . 상위 · t 태그 · u 전체태그 · c 복사 · m 이동 · d 삭제 · r 이름 · k 새 디렉터리 · v 보기 · h 숨김 · p 패널 cd · F10 트리 · Esc 닫기)", "(Enter open · . up · t tag · u tag all · c copy · m move · d delete · r rename · k new directory · v view · h hidden · p cd panel · F10 tree · Esc close)"),
    // ⚠ **옛 판을 지우지 않는다**(이 표의 규율 — 위 mdir F-키 주석). pytmux-478 ⑵ 가
    //    이 셋을 갈랐지만, 갈리기 전 문구를 보내는 서버(구버전·원격 링크 너머)가
    //    있으면 그때는 이 줄이 답이다.
    ("(↑↓ 스크롤 · Esc 닫기)", "(↑↓ scroll · Esc close)"),
    ("(규칙이 없습니다 — a 로 지금 패널의 디렉토리를 추가합니다)", "(no rules — press a to add this pane's directory)"),
    ("(내용 없음)", "(empty)"),
    ("(리셋 시각을 파싱할 수 없음)", "(cannot parse reset time)"),
    ("(저장된 프롬프트가 없습니다 — Claude 에 프롬프트를 입력해 보세요)", "(no saved prompts — type a prompt into Claude first)"),
    ("(제출된 체인지리스트가 없습니다)", "(no submitted changelists)"),
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
    ("Enter 키워드 · p 경로 · a 추가 · d 삭제 · Esc 닫기", "Enter keyword · p path · a add · d delete · Esc close"),
    ("accept — 편집·기본 FS 만 자동 수락 (⏵⏵ accept edits)", "accept — auto-accept edits·basic FS only (⏵⏵ accept edits)"),
    ("auto — 모든 동작 자동 수락, 안전검사 (⏵⏵ auto mode)", "auto — auto-accept all, safety checks (⏵⏵ auto mode)"),
    ("bypass — 권한 우회, 확인 없음 ⚠️ (Bypass Permission Mode)", "bypass — skip permissions, no confirm ⚠️ (Bypass Permission Mode)"),
    // pytmux-478 ⑵ — 꼬리줄이 「늘 붙는 것 / 스크롤될 때만」으로 갈렸다. 갈린 두 토막이
    // 각자 서버가 보내는 글이라, 각자 영어 얼굴을 가져야 한다.
    ("Esc 닫기", "Esc close"),
    ("Esc 목록으로", "Esc back to the list"),
    ("↑↓ 스크롤", "↑↓ scroll"),
    // pytmux-130 ⑴ — 토큰 팝업 탭 띠의 낱말(정본 `#tktabs` 그대로 · `screenspec._TAB_LABELS`).
    // 「세션」은 en_proto 가 이미 든다(중복 키 금지).
    ("기간", "Period"),
    ("머신", "Machine"),
    ("한도", "Limit"),
    ("경고", "Warn"),
    ("일별", "Daily"),
    ("모델", "Model"),
    // `[한도]` 판 고르개 두 줄의 이름(pytmux-130) — `모델` 은 탭 이름과 같은 낱말이라
    // 위 줄이 이미 덮고, `컨텍스트` 는 이 판에만 있다.
    ("컨텍스트", "Context"),
    ("시나리오", "Scenario"),
    ("c 비우기 · Esc 닫기", "c clear · Esc close"),
    ("default — 매번 확인 (일반 모드)", "default — confirm each time (normal)"),
    ("plan — 플랜 모드 (계획만, 실행 안 함)", "plan — plan mode (plan only, no run)"),
    ("r 원격 제어 토글(/rc) · Esc 닫기", "r toggle remote control (/rc) · Esc close"),
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
    // pytmux-544 — prompt-history 점프가 대체 화면(Claude fullscreen) 패널에서 못 뛴다.
    ("fullscreen(대체 화면) 패널에서는 그 위치로 못 뜁니다 — Claude 의 스티키 바나 transcript 모드의 이전·다음 프롬프트 키를 쓰세요", "Can't jump to it in a fullscreen (alternate screen) pane — use Claude's sticky bar or the transcript mode prompt keys"),
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
    ("아무 곳", "anywhere"),
    ("앞부분만 보입니다(뒤는 잘렸습니다)", "Only the beginning is shown (the rest is cut)"),
    ("완료마다", "each turn"),
    ("원격 제어(Remote Control)", "Remote Control"),
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
    // pytmux-477 — 재시도가 도는 중임을 말하는 표면 셋(배지 둘 + 알림 하나).
    (" ↻ 재시도 {n}회 · {eta}s ", " ↻ retry ×{n} · {eta}s "),
    (" ↻ 재시도 {n}회 ", " ↻ retry ×{n} "),
    (" ↻ Claude 재시도 중 — 대기 ", " ↻ Claude retrying — holding "),
    ("전송 에러 자동 재시도: '{msg}' 주입({n}회째 · 패널 {pane})",
     "Transmission-error auto-retry: injected '{msg}' (#{n} · pane {pane})"),
    // pytmux-468 — 플랜 전문·거부 사유 판(Tier C `claude-detail`).
    ("↑↓ 스크롤 · Esc 닫기", "↑↓ scroll · Esc close"),
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
    // ── 플러그인 판 정본 맞추기(pytmux-611~616) — mdir·ncd·p4·namesync·프롬프트 이력 ──
    ("Enter 들어가기 · Backspace/. 상위 · \\ 최상위 · Esc 나가기", "Enter open · Backspace/. up · \\ top · Esc leave"),
    ("지원하지 않는 압축 형식", "Unsupported archive format"),
    ("찾기", "Find"),
    ("F10=트리│F4=cd│⇧↵=분할│Esc", "F10=Tree│F4=cd│⇧↵=split│Esc"),
    ("선택 마스크 (예: *.txt)", "Select mask (e.g. *.txt)"),
    ("해제 마스크", "Unselect mask"),
    ("모두 덮어쓰기", "Overwrite all"),
    ("↑↓ 고르기 · Enter 확정 · Esc 취소", "↑↓ choose · Enter confirm · Esc cancel"),
    ("건너뛰기", "Skip"),
    ("대상에 같은 이름이 있습니다", "Destination has same name(s)"),
    ("압축 보기: 읽기전용", "Archive view: read-only"),
    ("규칙이 없습니다. [a] 또는 [Enter](현재 cwd)로 추가하세요.", "No rules. Add with [a] or [Enter] (current cwd)."),
    ("경로와 키워드는 필수입니다.", "Path and keyword are required."),
    ("규칙 추가 — Ctrl+S 저장 · Esc 취소", "Add rule — Ctrl+S save · Esc cancel"),
    ("규칙 수정 — Ctrl+S 저장 · Esc 취소", "Edit rule — Ctrl+S save · Esc cancel"),
    ("Enter 그 칸 고치기 · Ctrl+S 저장 · Esc 취소", "Enter edit that field · Ctrl+S save · Esc cancel"),
    ("↑↓ 이동 · [a]추가 [e]수정 [d]삭제 · [Enter] 현재 cwd 로 추가 · [Esc] 저장·닫기", "↑↓ move · [a]add [e]edit [d]delete · [Enter] add current cwd · [Esc] save & close"),
    ("호스트(비우면 모든 머신)", "Host (blank = any machine)"),
    ("키워드(탭·패널·Claude 세션 이름)", "Keyword (tab · pane · Claude session name)"),
    ("OS: darwin/linux/windows(비우면 모든 OS)", "OS: darwin/linux/windows (blank = any OS)"),
    ("경로(정확히 이 디렉토리에서 실행 시)", "Path (matched when Claude runs in exactly this directory)"),
    ("저장", "Save"),
    ("namesync — 이름 동기화 규칙", "namesync — name-sync rules"),
    ("새 탭에서 세션 리줌: {title}", "Resuming in new tab: {title}"),
    ("압축 보기 — {path}", "Archive — {path}"),
    ("{n}개를 지웁니다 — 되돌릴 수 없습니다\n디렉토리 {d}개 포함 — 하위까지 영구 삭제됩니다", "Deleting {n} — this cannot be undone\n{d} director(y/ies) included — deleted permanently"),
    ("오류: {err}", "Error: {err}"),
    ("submitted changelists — {port}", "submitted changelists — {port}"),
    ("↑↓ 이동 · Enter 그 위치로 점프 · +/− 미리보기 {n}행 · Esc 닫기", "↑↓ move · Enter jump to position · +/− preview {n} rows · Esc close"),
    // ── claude-code · 토큰 팝업 띠·경고 탭·한도 창 Σ·설정 값(pytmux-617·618) ──
    //
    // 띠 끝의 `/usage` 액션 · 활성 한도·경고 탭 재클릭의 «되돌아가기» · 정본과 같은 트리가 된
    // 경고 판의 제목·라벨·본문 · 설정 판 cycle 값의 단위(`600초 이상`). 꼬리줄의 `o머신` 은
    // 뺐다 — 정본 꼬리줄도 안 알리고, 머신이 하나면 그 키가 없다.
    (
        "[상황]\n• pytmux 가 Claude Code 화면 형식을 인식하지 못합니다.\n• 토큰/사용량 추적과 자동화(자동 재개·자동 압축·한도 게이트)가 멈춥니다.\n• Claude Code 자체 동작(입력·출력)에는 영향이 없습니다.\n• 보통 Claude Code 버전 업데이트로 화면 구조가 바뀌면 발생합니다.\n\n[할일]\n• 화면이 다시 정상 인식되면 경고는 자동으로 사라집니다(잠시 대기).\n• 계속되면 pytmux 의 Claude 파서(claude.py)를 새 포맷에 맞춰 갱신해야 합니다.\n• REC 캡처가 켜져 있으면 captures/ 로그로 새 포맷을 분석할 수 있습니다.",
        "[Situation]\n• pytmux cannot recognize the Claude Code screen format.\n• Token/usage tracking and automation (auto-retry·limit gate) stop.\n• Claude Code itself (input·output) is unaffected.\n• Usually happens when a Claude Code version update changes the layout.\n\n[To do]\n• The warning clears automatically once the screen is recognized again (wait a moment).\n• If it persists, pytmux's Claude parser (claude.py) must be updated for the new format.\n• If REC capture is on, the captures/ log can be used to analyze it.",
    ),
    (
        "Claude 포맷 미인식",
        "Claude format unrecognized",
    ),
    (
        "[상황]\n• 현재 Claude 턴이 임계 시간을 넘겨 오래 진행 중입니다(경고만, 자동 개입 없음).\n\n[할일]\n• 정상적인 긴 작업일 수 있습니다. 멈춘 듯하면 패널에서 Esc 로 중단하세요.\n• 임계는 옵션 claude_long_turn_sec 로 조정/끌 수 있습니다(0=끔).",
        "[Situation]\n• The current Claude turn exceeded the threshold and is running long (warning only, no auto-intervention).\n\n[To do]\n• It may be a normal long task. If it seems stuck, press Esc in the panel to interrupt.\n• Threshold is adjustable/disable-able via option claude_long_turn_sec (0=off).",
    ),
    (
        "Claude 장기 턴",
        "Claude long turn",
    ),
    (
        "[상황]\n• 같은 출력이 여러 번 반복됐습니다 — 루프 의심(경고만, 자동 개입 없음).\n\n[할일]\n• 진행이 없으면 다른 지시를 주거나 /clear 후 다시 시도하세요.\n• 임계는 옵션 claude_repeat_alert 로 조정/끌 수 있습니다(0=끔).",
        "[Situation]\n• The same output repeated several times — loop suspected (warning only, no auto-intervention).\n\n[To do]\n• If there's no progress, give different instructions or retry after /clear.\n• Threshold is adjustable/disable-able via option claude_repeat_alert (0=off).",
    ),
    (
        "Claude 반복 루프 의심",
        "Claude repeat-loop suspected",
    ),
    (
        "이전 탭으로 →",
        "Back to the previous tab →",
    ),
    (
        "/usage 다시 조회",
        "Refresh /usage",
    ),
    (
        "↑↓ 이동 · Tab/←→ 탭 · Esc 닫기 · p세션 · l한도 · s시나리오 · u/usage",
        "↑↓ move · Tab/←→ tabs · Esc close · p session · l limit · s scenario · u /usage",
    ),
    (
        "↑↓ 이동 · Enter 적용(/model 주입) · Tab 탭 · Esc 닫기 · p세션 · l한도 · s시나리오 · u/usage",
        "↑↓ move · Enter apply (injects /model) · Tab tabs · Esc close · p session · l limit · s scenario · u /usage",
    ),
    (
        "↑↓ 이동 · Enter/←→ 펼침·접힘 · Tab 탭 · Esc 닫기 · p세션 · l한도 · s시나리오 · u/usage",
        "↑↓ move · Enter/←→ expand·collapse · Tab tabs · Esc close · p session · l limit · s scenario · u /usage",
    ),
    (
        "/usage",
        "/usage",
    ),
    (
        "Claude 경고",
        "Claude warning",
    ),
    (
        "현재 표시할 Claude 경고가 없습니다(이미 해소됨).",
        "No active Claude warning (already cleared).",
    ),
    (
        "Enter/←→ 펼침·접힘 · 다른 탭 이동 · u/usage · Esc닫기",
        "Enter/←→ expand·collapse · switch tab · u /usage · Esc close",
    ),
    (
        "── 이전 경고 (펼쳐서 보기) ──",
        "── Past warnings (expand to view) ──",
    ),
    (
        "{v}초 이상",
        "{v}s or more",
    ),
    (
        "{v}회 이상",
        "{v}× or more",
    ),
    // 경고 본문의 **줄 하나씩**(판은 본문을 줄마다 싣고 줄마다 제 로케일로 다시 짓는다 —
    // ko/en 본문의 줄 수가 같다. 묶음 원문은 위에 있다: 그것은 정본 카탈로그의 단위다).
    ("[상황]", "[Situation]"),
    ("• pytmux 가 Claude Code 화면 형식을 인식하지 못합니다.", "• pytmux cannot recognize the Claude Code screen format."),
    ("• 토큰/사용량 추적과 자동화(자동 재개·자동 압축·한도 게이트)가 멈춥니다.", "• Token/usage tracking and automation (auto-retry·limit gate) stop."),
    ("• Claude Code 자체 동작(입력·출력)에는 영향이 없습니다.", "• Claude Code itself (input·output) is unaffected."),
    ("• 보통 Claude Code 버전 업데이트로 화면 구조가 바뀌면 발생합니다.", "• Usually happens when a Claude Code version update changes the layout."),
    ("[할일]", "[To do]"),
    ("• 화면이 다시 정상 인식되면 경고는 자동으로 사라집니다(잠시 대기).", "• The warning clears automatically once the screen is recognized again (wait a moment)."),
    ("• 계속되면 pytmux 의 Claude 파서(claude.py)를 새 포맷에 맞춰 갱신해야 합니다.", "• If it persists, pytmux's Claude parser (claude.py) must be updated for the new format."),
    ("• REC 캡처가 켜져 있으면 captures/ 로그로 새 포맷을 분석할 수 있습니다.", "• If REC capture is on, the captures/ log can be used to analyze it."),
    ("• 같은 출력이 여러 번 반복됐습니다 — 루프 의심(경고만, 자동 개입 없음).", "• The same output repeated several times — loop suspected (warning only, no auto-intervention)."),
    ("• 진행이 없으면 다른 지시를 주거나 /clear 후 다시 시도하세요.", "• If there's no progress, give different instructions or retry after /clear."),
    ("• 임계는 옵션 claude_repeat_alert 로 조정/끌 수 있습니다(0=끔).", "• Threshold is adjustable/disable-able via option claude_repeat_alert (0=off)."),
    ("• 현재 Claude 턴이 임계 시간을 넘겨 오래 진행 중입니다(경고만, 자동 개입 없음).", "• The current Claude turn exceeded the threshold and is running long (warning only, no auto-intervention)."),
    ("• 정상적인 긴 작업일 수 있습니다. 멈춘 듯하면 패널에서 Esc 로 중단하세요.", "• It may be a normal long task. If it seems stuck, press Esc in the panel to interrupt."),
    ("• 임계는 옵션 claude_long_turn_sec 로 조정/끌 수 있습니다(0=끔).", "• Threshold is adjustable/disable-able via option claude_long_turn_sec (0=off)."),
];
