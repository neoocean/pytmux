//! 적합성 — GUI 가 스스로 쓴 한국어 문구의 **말투**와 **정본 짝**(pytmux-595).
//!
//! 사람 결정(2026-11-12 · 계획 §7 ⑥): ⓐ 정본 카탈로그에 같은 뜻의 글이 있으면 **그 글 그대로**
//! (영어 짝도 정본 en) ⓑ GUI 전용 글은 **합니다체**. 종전 자들(`server_strings_conformance` ·
//! `help_i18n`)은 **열쇠**를 재고 본문 말투는 안 쟀다 — 그래서 확인 판·버퍼 판에서 정본은
//! 합니다체, GUI 는 해라체였다(4차 대조 ③).
//!
//! 픽스처: `scripts/gen_canon_phrases.py` → `fixtures/canon_phrases.json`(정본 ko/en 전수).
//!
//! 셋을 잰다:
//! 1. 정본 글 그대로인 자리(확인 물음 · 빈 판 · 재시작 점검 머리줄 …)가 두 로케일에서 정본과 같다.
//! 2. GUI 카탈로그(`base/src/i18n/en_*.rs` · 서버 글 `en_server.rs` 는 뺀다)의 한국어 열쇠 가운데
//!    해라체로 끝나는 것은 **정본 글과 같을 때만** 산다 — 예외 목록이 없다(정본 글이 곧 면제다).
//! 3. 모든 물음(`Prompt`)이 영어 로케일에서 한글 없이 나온다 — i18n 게이트(`i18n_coverage`)는
//!    `t("…")` 리터럴만 세서 `t(match …)` 로 짓는 물음을 못 본다(실제로 고정 탭 닫기 물음의
//!    영어 짝이 옛 글에 걸려 있어 영어 판에서 한국어로 떴다).

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use base::i18n;
use base::screens::Prompt;
use serde_json::Value;

fn fixture() -> Value {
    serde_json::from_str(include_str!("fixtures/canon_phrases.json"))
        .expect("canon_phrases.json 을 못 읽는다 — scripts/gen_canon_phrases.py 로 다시 뽑을 것")
}

fn canon<'a>(fx: &'a Value, loc: &str, key: &str) -> &'a str {
    fx[loc][key].as_str().unwrap_or_else(|| panic!("정본 카탈로그에 {loc}.{key} 가 없다"))
}

fn has_hangul(s: &str) -> bool {
    s.chars().any(|c| ('\u{AC00}'..='\u{D7A3}').contains(&c))
}

// ── 1. 정본 글 그대로 ──────────────────────────────────────────────────────────

/// 확인 물음 — 정본 `client.py` 의 `ConfirmScreen(i18n.t("dialog.*_msg"))` 와 같은 글.
const PROMPT_TWINS: &[(Prompt, &str)] = &[
    (Prompt::KillTab, "dialog.kill_tab_msg"),
    (Prompt::KillTabLast, "dialog.kill_pytmux_msg"),
    (Prompt::KillTabLastRemote, "dialog.kill_pytmux_remote_msg"),
    (Prompt::KillTabPinned, "dialog.kill_pinned_msg"),
    (Prompt::KillServer, "dialog.kill_server_msg"),
    (Prompt::DetachRemoteTab, "dialog.detach_remote_msg"),
];

/// 뷰·proto 가 `t("…")` 로 부르는 정본 글 — 한국어 원문이 **소스에 리터럴로** 있고 영어 짝이 정본 en 이다.
const LINE_TWINS: &[&str] = &[
    "restartcheck.safe",
    "restartcheck.unsafe",
    "msg.merge_remote_no_peers",
    "msg.merge_remote_not_remote",
    "screen.no_search_results",
    "screen.no_buffers",
    "screen.notice_history_empty",
    "msg.copied_chars",
    "msg.clipboard_suffix",
    "msg.setting_save_failed",
    "klist.p_d",
];

#[test]
fn the_confirm_questions_are_the_canon_dialogs_in_both_locales() {
    let fx = fixture();
    let mut wrong = Vec::new();
    for (prompt, key) in PROMPT_TWINS {
        for loc in ["ko", "en"] {
            let ours = i18n::with_locale(loc, || prompt.question().to_owned());
            let want = canon(&fx, loc, key);
            if ours != want {
                wrong.push(format!("{prompt:?} [{loc}] 정본 {key} = {want:?} · 우리 {ours:?}"));
            }
        }
    }
    assert!(wrong.is_empty(), "확인 물음이 정본 글과 다르다:\n  {}", wrong.join("\n  "));
}

fn crates_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

/// 우리 소스(`base`·`proto`·`gui` 의 `src`, 시험·i18n 표 제외)를 이어 붙인 글.
fn our_sources() -> String {
    let mut out = String::new();
    let mut stack: Vec<PathBuf> =
        ["base/src", "proto/src", "gui/src"].iter().map(|d| crates_dir().join(d)).collect();
    while let Some(dir) = stack.pop() {
        for entry in std::fs::read_dir(&dir).expect("소스 디렉터리").flatten() {
            let path = entry.path();
            let name = path.file_name().unwrap().to_string_lossy().into_owned();
            if path.is_dir() {
                if name != "i18n" {
                    stack.push(path);
                }
            } else if name.ends_with(".rs") && !name.ends_with("_tests.rs") {
                out.push_str(&std::fs::read_to_string(&path).unwrap_or_default());
            }
        }
    }
    out
}

#[test]
fn the_gui_lines_with_a_canon_twin_use_that_twin_in_both_locales() {
    let fx = fixture();
    let src = our_sources();
    let mut wrong = Vec::new();
    for key in LINE_TWINS {
        let ko = canon(&fx, "ko", key);
        let en = canon(&fx, "en", key);
        if !src.contains(&format!("\"{ko}\"")) {
            wrong.push(format!("{key}: 정본 글 {ko:?} 이 우리 소스에 리터럴로 없다"));
        }
        let ours_en = i18n::with_locale("en", || i18n::t(ko).to_owned());
        if ours_en != en {
            wrong.push(format!("{key}: 영어 짝이 정본과 다르다 — 정본 {en:?} · 우리 {ours_en:?}"));
        }
    }
    assert!(wrong.is_empty(), "정본 짝:\n  {}", wrong.join("\n  "));
}

// ── 2. 합니다체 ─────────────────────────────────────────────────────────────────

/// Rust 문자열 리터럴 하나를 읽는다(`"` 다음부터) — 이스케이프와 줄 잇기(`\` + 개행)를 푼다.
fn read_literal(c: &[char], mut i: usize) -> (String, usize) {
    let mut out = String::new();
    while i < c.len() {
        match c[i] {
            '\\' if i + 1 < c.len() => {
                match c[i + 1] {
                    '\n' => {
                        i += 2;
                        while i < c.len() && c[i].is_whitespace() {
                            i += 1;
                        }
                        continue;
                    }
                    'n' => out.push('\n'),
                    't' => out.push('\t'),
                    other => out.push(other),
                }
                i += 2;
            }
            '"' => return (out, i + 1),
            ch => {
                out.push(ch);
                i += 1;
            }
        }
    }
    (out, i)
}

/// `en_*.rs` 표의 한국어 열쇠(튜플의 첫 글) 전수.
fn catalog_keys(file: &Path) -> Vec<String> {
    let text = std::fs::read_to_string(file).expect("i18n 표");
    let c: Vec<char> = text.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < c.len() {
        // 주석 줄은 건너뛴다.
        if c[i] == '/' && c.get(i + 1) == Some(&'/') {
            while i < c.len() && c[i] != '\n' {
                i += 1;
            }
            continue;
        }
        if c[i] == '(' {
            let mut j = i + 1;
            while j < c.len() && c[j].is_whitespace() {
                j += 1;
            }
            if c.get(j) == Some(&'"') {
                let (key, end) = read_literal(&c, j + 1);
                if has_hangul(&key) {
                    out.push(key);
                }
                i = end;
                continue;
            }
        }
        i += 1;
    }
    out
}

/// 해라체로 끝나는 마디가 있나 — `…다` 가 마디 끝에 오는데 `니다`·`습다` 가 아니다 · `…까?`/`…냐?` ·
/// `…할 것`. (낱말 안의 `다`(`다음`·`바다`)는 마디 끝이 아니라 안 걸린다.)
fn haera(s: &str) -> bool {
    let c: Vec<char> = s.chars().collect();
    let boundary = |k: usize| {
        c.get(k).is_none_or(|ch| {
            ch.is_whitespace() || ".,)—:…」»]!?'\"(·".contains(*ch)
        })
    };
    for (k, ch) in c.iter().enumerate() {
        if *ch == '다' && boundary(k + 1) {
            let prev = if k > 0 { c[k - 1] } else { ' ' };
            if prev != '니' && prev != '습' && ('\u{AC00}'..='\u{D7A3}').contains(&prev) {
                return true;
            }
        }
        if (*ch == '까' || *ch == '냐') && c.get(k + 1) == Some(&'?') {
            return true;
        }
    }
    s.contains("할 것") && (s.ends_with("할 것") || s.contains("할 것)"))
}

#[test]
fn the_gui_speaks_in_hamnida_unless_the_line_is_the_canons_own() {
    let fx = fixture();
    let canon_ko: BTreeSet<&str> =
        fx["ko"].as_object().unwrap().values().filter_map(|v| v.as_str()).collect();
    let dir = crates_dir().join("base/src/i18n");
    let mut wrong = Vec::new();
    let mut seen = 0;
    for name in ["en_core.rs", "en_gui.rs", "en_proto.rs", "en_claude.rs"] {
        for key in catalog_keys(&dir.join(name)) {
            seen += 1;
            if haera(&key) && !canon_ko.contains(key.as_str()) {
                wrong.push(format!("{name}: {key:?}"));
            }
        }
    }
    assert!(seen > 300, "읽은 열쇠가 너무 적다({seen}) — 파서가 헛돈다");
    assert!(
        wrong.is_empty(),
        "해라체 GUI 문구 {}개 — 합니다체로 고치거나(열쇠·호출부·영어 짝을 같이) 정본 글이면 그 글 그대로:\n  {}",
        wrong.len(),
        wrong.join("\n  ")
    );
}

#[test]
fn the_tone_ruler_bites() {
    // 변이 대신 — 판정이 실제로 무는지 한 줄씩.
    assert!(haera("설정을 다시 읽었다"));
    assert!(haera("이 탭을 닫을까?"));
    assert!(haera("⚠️ 주의 — 아래 FAIL 을 확인할 것"));
    assert!(haera("모르는 명령이다: {name}"));
    assert!(!haera("설정을 다시 읽었습니다"));
    assert!(!haera("이 탭을 닫을까요?"));
    assert!(!haera("다음 탭"));
    assert!(!haera("(검색 결과 없음)"));
}

// ── 3. 모든 물음이 영어로 ────────────────────────────────────────────────────────

#[test]
fn every_prompt_question_has_an_english_face() {
    // 물음 전수는 `Prompt::all()` — 변형을 더하면 컴파일이 그 목록에도 더하게 한다(pytmux-655).
    let mut wrong = Vec::new();
    assert!(Prompt::all().len() >= 40, "물음 전수가 너무 적다: {}", Prompt::all().len());
    for prompt in Prompt::all() {
        let en = i18n::with_locale("en", || prompt.question().to_owned());
        if has_hangul(&en) {
            wrong.push(format!("{prompt:?}: {en:?}"));
        }
    }
    assert!(wrong.is_empty(), "영어 판에서 한국어로 뜨는 물음:\n  {}", wrong.join("\n  "));
}
