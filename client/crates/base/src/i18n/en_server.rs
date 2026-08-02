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
    (" …", " …"),
    (" ⏎", " ⏎"),
    (" ⏳ {label} {eta}s(입력=취소) ", " ⏳ {label} {eta}s (input=cancel) "),
    (" ⏳ 자동재개 {eta}s(입력=취소) ", " ⏳ auto-resume {eta}s (input=cancel) "),
    ("(내용 없음)", "(empty)"),
    ("(리셋 시각을 파싱할 수 없음)", "(cannot parse reset time)"),
    ("(저장된 프롬프트가 없습니다 — Claude 에 프롬프트를 입력해 보세요)", "(no saved prompts — type a prompt into Claude first)"),
    ("(제출된 체인지리스트가 없습니다)", "(no submitted changelists)"),
    ("(큐 비어 있음)", "(queue empty)"),
    ("/usage 한도 데이터 없음 — Claude 패널에서 /usage 를 먼저 실행", "No /usage limit data — run /usage in a Claude panel first"),
    ("?%/5h 사용", "?%/5h used"),
    ("Claude 사용 한도 (/usage)", "Claude usage limit (/usage)"),
    ("submitted changelists", "submitted changelists"),
    ("{pct}%/5h 사용", "{pct}%/5h used"),
    ("{pct}%/주(Sonnet)", "{pct}%/wk(Sonnet)"),
    ("↑↓ 스크롤 · PgUp/PgDn · Home/End · Esc 닫기", "↑↓ scroll · PgUp/PgDn · Home/End · Esc close"),
    ("↑↓ 이동 · Enter 상세 · Esc 닫기", "↑↓ move · Enter details · Esc close"),
    ("↻ 갱신 [u]", "↻ Refresh [u]"),
    ("▭ 패널 보기 [a]", "▭ Pane view [a]"),
    ("⚠ Claude 포맷 미인식 — 추적 중단(버전 업데이트?)", "⚠ Claude format unrecognized — tracking paused (version update?)"),
    ("⚠ 동일 결과 {n}회 반복 — 루프 의심", "⚠ Same output repeated {n}× — loop suspected"),
    ("⤢ 팝업/탭 [t]", "⤢ Popup/Tab [t]"),
    ("그 프롬프트가 스크롤백에 없습니다(회전/재시작으로 사라짐)", "That prompt is no longer in scrollback (rotated out / restarted)"),
    ("다음 리셋까지 ", "Until next reset "),
    ("다음 리셋까지 {left}", "Until next reset {left}"),
    ("불러오는 중…", "Loading…"),
    ("사용량 갱신 중… (숨은 /usage, ~수초)", "Refreshing usage… (hidden /usage, ~a few s)"),
    ("사용량 조회 중… (숨은 /usage, ~수초)", "Querying usage… (hidden /usage, ~a few sec)"),
    ("시작 규칙 비움", "Start rules cleared"),
    ("시작 규칙 저장됨", "Start rules saved"),
    ("원격 제어(Remote Control)", "Remote Control"),
    ("원격제어가 실제로 켜져 있어 정책 차단 래치를 해제합니다", "Remote control is actually on — clearing the policy-block latch"),
    ("이 패널의 Claude Code 가 데스크탑 앱 '원격 제어'로 연결돼 있습니다.
(패널 화면의 'Remote Control active' 표시)

• 원격 제어는 Claude Code CLI 의 '/rc' 명령으로 켜고 끕니다.
  → 이 화면에서 [r] 키로 바로 토글합니다(해당 패널에 /rc 주입).
• 원격 제어로 입력된 프롬프트도 상단 프롬프트 헤더에 반영됩니다.

[r] 원격 제어 토글(/rc)   ·   닫기: Esc 또는 바깥 클릭.", "This panel's Claude Code is connected to the desktop app's 'Remote Control'.
(the panel shows 'Remote Control active')

• Remote control is toggled with the Claude Code CLI '/rc' command.
  → Press [r] here to toggle it directly (injects /rc into the panel).
• Prompts entered via remote control also appear in the top prompt header.

[r] Toggle remote control (/rc)   ·   close: Esc or click outside."),
    ("자동재개", "auto-resume"),
    ("큐 비움", "Queue cleared"),
    ("표시할 Claude 경고가 없습니다(이미 해소됨).", "No Claude warning to show (already cleared)."),
    ("프롬프트 클리어 큐", "Prompt-clear queue"),
    ("프롬프트 히스토리", "Prompt history"),
    ("한도 데이터 없음 — Claude 패널에서 /usage 실행 후 [u]로 갱신", "No limit data — run /usage in a Claude pane, then [u] to refresh"),
    ("한도 데이터 없음 — Claude 패널에서 /usage 실행 후 갱신", "No limit data — run /usage in a Claude pane to refresh"),
];
