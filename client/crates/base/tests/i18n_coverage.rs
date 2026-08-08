//! 게이트 — `t()`/`tc()`/`tf()` 에 넘기는 **한국어 원문** 중 영어 짝이 없는 것을 세어
//! 못박는다(래칫). 대상은 이 워크스페이스의 우리 소스 전부다.
//!
//! # 왜 자가 하나 더 필요한가 (§10-24)
//!
//! 판을 영어로 열어도 조각에 한글이 남는 결함이 있었다(제보 2026-08-03 · §10-21ⓖ2).
//! 그때 세운 자는 **프레임 오라클**이다 — `gui/src/session_view_tests.rs` 가 아홉 화면을
//! 영어로 열어 그려진 조각에 한글이 남는지 본다. 그 자는 글이 **어느 경로로 왔든** 잡지만
//! **닿는 화면**만 잡는다: 오류·지나가는 말은 그 오류를 실제로 일으켜야 화면에 오른다.
//!
//! 이 파일은 반대쪽에서 잰다. 화면을 안 띄우고 **소스**를 읽으므로 오류 경로의 글까지
//! 세지만, 대신 리터럴이 아닌 자리는 못 본다 — `t(고른_낱말)` 처럼 변수를 넘기면 시야
//! 밖이다(제보의 `취소` 가 정확히 그 모양이었다). 그래서 **둘 다 필요하다**. 어느 하나가
//! 다른 하나를 대신한다고 읽으면 그 순간 절반이 안 재진다.
//!
//! ⚠ **사각지대는 작지 않다** — 실측 서른아홉 자리가 리터럴이 아닌 인자로 부른다. 큰
//! 덩어리가 `t(action.label())` 부류(`keymap.rs` 의 `Action::label` 이 원문을 돌려주고
//! 부르는 쪽이 옮긴다)라 **액션 이름은 여기서 한 줄도 안 세어진다**. 그 자리를 지키는
//! 것은 프레임 오라클이다. `print_the_untranslated` 가 이 수를 매번 같이 찍는다.
//!
//! # 래칫은 **비었다** — 그래서 이제 하나만 늘어도 운다
//!
//! 처음 세울 때는 마흔여섯이 남아 있었고 대부분 오류 경로였다. 한 슬라이스에서 다
//! 번역하면 그 CL 이 리뷰가 안 되고, 그렇다고 열어 두면 **조용히 는다** — 실제로
//! `취소` 는 2026-07-31 에 발견돼 주석까지 적힌 채 항목만 빠져 사흘을 더 살았다.
//! 그래서 먼저 **수를 못박았다**(p4 70726): 0 이 목표가 아니라 **늘지 않는 것**이
//! 먼저였다. 그 다음 CL 이 마흔여섯을 전부 채워 [`UNTRANSLATED`] 를 비웠다.
//!
//! ★ **빈 래칫은 이 게이트의 성격을 바꾼다.** 종전에는 "마흔여섯보다 늘면 운다"였고
//! 지금은 **"하나라도 영어 짝 없이 들어오면 운다"**이다. 그러니 여기가 울면 답은
//! 거의 언제나 `en_*.rs` 에 한 줄을 넣는 것이다 — 아래 [`UNTRANSLATED`] 에 줄을
//! 더하는 것은 **번역을 미룬다는 선언**이고, 미루는 이유를 CL 설명에 적을 수 있을
//! 때만 그렇게 한다. 한 번 비운 자리는 다시 채우기 쉽고, 그래서 눈에 띄어야 한다.
//!
//! ⚠ **§10-24 에 적힌 스캔 레시피(`(?<![\w:])t\(`)는 적게 셌다** — `::` 을 통째로 버려서
//! `use base::i18n;` 후 `i18n::t("…")` 로 부르는 자리를 못 봤다. 그 모양이 예순 자리
//! 넘게 있어서 수가 125/38 이 아니라 **170/46** 이었다. 여기 `call_is_ours` 가 그
//! 경로를 `i18n::` 한 마디만 골라서 통과시킨다.
//!
//! # 고치는 순서
//!
//! - **새 원문을 넣었는데 여기서 울면**: 영어 짝을 `base/src/i18n/en_*.rs` 에 넣는다.
//!   그게 정답이고, 래칫에 줄을 더하는 것은 **번역을 나중으로 미룬다는 선언**이다 —
//!   미루는 이유를 CL 설명에 적을 수 있을 때만 그렇게 한다.
//! - **번역을 채웠는데 여기서 울면**: [`UNTRANSLATED`] 에서 그 줄을 지운다(래칫 조이기).
//!   지금은 비어 있으니 이 길이 필요할 일은 위의 미루기를 한 번 지난 뒤에나 온다.
//! - **지금 목록을 통째로 다시 뽑으려면**:
//!   `cargo test -p base --test i18n_coverage -- print_the_untranslated --nocapture`
//!   가 그대로 붙여 넣을 수 있는 모양으로 찍는다.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

/// 아직 영어 짝이 없는 한국어 원문 — **래칫**. 지금은 **비어 있다**(모듈 머리말).
///
/// ⚠ 손으로 정렬을 지키지 않아도 된다 — 비교는 집합으로 하고, 다시 뽑는 길은
/// `print_the_untranslated` 다. 다만 **줄을 더할 때는 사유를 CL 에 적는다.**
static UNTRANSLATED: &[&str] = &[
    // 비었다 — 마흔여섯을 전부 채웠다(모듈 머리말 「래칫은 비었다」).
];

/// 스캔에서 뺄 자리.
///
/// - `warpui`·`warpui_core` — 상류 스냅샷(MIT 경계). 우리 글이 아니다.
/// - `tests/`·`*_tests.rs`·인라인 `#[cfg(test)]` — 시험이 지어낸 글은 사용자에게 안 간다.
/// - `src/i18n.rs`·`src/i18n/` — 카탈로그와 그 문서 자신(예시가 호출부처럼 보인다).
fn is_excluded(path: &Path) -> bool {
    let mut in_upstream = false;
    let mut in_tests_dir = false;
    let mut in_i18n_dir = false;
    for part in path.components() {
        let part = part.as_os_str().to_string_lossy();
        match part.as_ref() {
            "warpui" | "warpui_core" | "target" => in_upstream = true,
            "tests" => in_tests_dir = true,
            "i18n" => in_i18n_dir = true,
            _ => {}
        }
    }
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    in_upstream || in_tests_dir || in_i18n_dir || name.ends_with("_tests.rs") || name == "i18n.rs"
}

/// 스캔 결과. 수를 같이 들고 다니는 이유는 **공허 통과**를 막기 위해서다 — 스캐너가
/// 아무것도 못 찾아도 "미번역 0" 은 초록이라, 찾은 양을 따로 단언해야 한다.
#[derive(Default)]
struct Scan {
    files: usize,
    /// 리터럴을 넘긴 호출부 수(중복 포함).
    sites: usize,
    /// 한국어 원문을 **문맥과 함께** — `(tc 의 문맥, 원문)`. `t`/`tf` 는 문맥이 없다.
    ///
    /// 문맥을 버리고 원문만 들면 `tc("setting", "열기")` 를 `t("열기")` 로 재게 되어,
    /// 문맥 키(`setting\u{4}열기`)를 채워도 **영원히 미번역으로 남는다**(래칫이 안
    /// 좁혀진다). 재는 자는 호출부가 실제로 부르는 함수와 같아야 한다.
    pairs: BTreeSet<(Option<String>, String)>,
    /// 리터럴이 아닌 인자로 부른 자리 — **스캐너의 사각지대**(모듈 머리말).
    dynamic: usize,
}

impl Scan {
    /// 문맥을 접은 원문 집합(수를 세고 래칫과 대조할 때 쓴다).
    fn korean(&self) -> BTreeSet<String> {
        self.pairs.iter().map(|(_, ko)| ko.clone()).collect()
    }
}

fn scan_workspace() -> Scan {
    // `.../client/crates/base` → `.../client/crates`
    let crates = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("crates 디렉토리")
        .to_path_buf();
    let mut scan = Scan::default();
    let mut stack = vec![crates];
    while let Some(dir) = stack.pop() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(e) => e,
            Err(e) => panic!("{} 를 못 읽는다: {e}", dir.display()),
        };
        for entry in entries {
            let path = entry.expect("디렉토리 항목").path();
            if is_excluded(&path) {
                continue;
            }
            if path.is_dir() {
                stack.push(path);
            } else if path.extension().is_some_and(|e| e == "rs") {
                let src = std::fs::read_to_string(&path)
                    .unwrap_or_else(|e| panic!("{} 를 못 읽는다: {e}", path.display()));
                scan.files += 1;
                scan_text(&src, &mut scan);
            }
        }
    }
    scan
}

/// 한 파일을 훑어 `t`/`tc`/`tf` 호출부의 리터럴을 모은다.
///
/// 정규식 대신 **손으로 훑는** 이유: 주석 안의 `t("…")`(이 저장소에 실제로 많다)와
/// 문자열 안의 같은 모양을 세면 안 되고, 날 문자열·이스케이프·인라인 시험 모듈까지
/// 걸러야 한다. 그 넷을 정규식으로 하려다 셋을 놓치는 쪽이 더 비싸다.
fn scan_text(src: &str, out: &mut Scan) {
    let c: Vec<char> = src.chars().collect();
    let n = c.len();
    let mut i = 0;
    while i < n {
        // 주석
        if c[i] == '/' && i + 1 < n && c[i + 1] == '/' {
            while i < n && c[i] != '\n' {
                i += 1;
            }
            continue;
        }
        if c[i] == '/' && i + 1 < n && c[i + 1] == '*' {
            skip_block_comment(&c, &mut i);
            continue;
        }
        // `#[cfg(test)]` 가 붙은 항목은 통째로 건너뛴다(인라인 시험 모듈).
        if c[i] == '#' && starts_cfg_test(&c, i) {
            skip_attr(&c, &mut i);
            skip_item(&c, &mut i);
            continue;
        }
        // 문자 리터럴 — 라이프타임(`'a`)과 갈라야 한다.
        if c[i] == '\'' {
            if i + 1 < n && (c[i + 1] == '\\' || (i + 2 < n && c[i + 2] == '\'')) {
                skip_char_literal(&c, &mut i);
            } else {
                i += 1;
            }
            continue;
        }
        // 날 문자열 `r"…"` / `r#"…"#` — 안의 무엇도 코드가 아니다.
        if (c[i] == 'r' || c[i] == 'b') && is_raw_string_start(&c, i) {
            skip_raw_string(&c, &mut i);
            continue;
        }
        if c[i] == '"' {
            let _ = read_string(&c, &mut i);
            continue;
        }
        if is_ident_char(c[i]) {
            let start = i;
            while i < n && is_ident_char(c[i]) {
                i += 1;
            }
            let ident: String = c[start..i].iter().collect();
            if matches!(ident.as_str(), "t" | "tc" | "tf") && call_is_ours(&c, start) {
                let mut j = i;
                skip_ws(&c, &mut j);
                if j < n && c[j] == '(' {
                    j += 1;
                    read_call_args(&c, j, ident == "tc", out);
                }
            }
            continue;
        }
        i += 1;
    }
}

/// 이 `t(`/`tc(`/`tf(` 가 우리 i18n 호출인가.
///
/// 앞이 식별자 문자면 남의 이름 꼬리이고, `.` 이면 메서드다. `::` 은 **`i18n::t(` 만**
/// 통과시킨다 — 원래 적어 둔 레시피(`(?<![\w:])t\(`)는 `::` 을 통째로 버려서
/// `use base::i18n;` 후 `i18n::t("…")` 로 부르는 자리를 **못 봤다**(이 저장소에 그 모양이
/// 예순 자리 넘게 있다).
fn call_is_ours(c: &[char], start: usize) -> bool {
    if start == 0 {
        return true;
    }
    let prev = c[start - 1];
    if is_ident_char(prev) || prev == '.' {
        return false;
    }
    if prev != ':' {
        return true;
    }
    // `…::t(` — 앞의 경로 마디가 `i18n` 이라야 한다.
    if start < 2 || c[start - 2] != ':' {
        return false;
    }
    let mut k = start - 2;
    while k > 0 && is_ident_char(c[k - 1]) {
        k -= 1;
    }
    let seg: String = c[k..start - 2].iter().collect();
    seg == "i18n"
}

/// 여는 괄호 **뒤**에서 원문 인자를 읽어 담는다. `tc` 는 첫 인자가 문맥이라 둘째가 원문이다.
fn read_call_args(c: &[char], mut j: usize, is_tc: bool, out: &mut Scan) {
    skip_ws(c, &mut j);
    let mut ctx = None;
    if is_tc {
        // 문맥 인자 — 리터럴이 아니면 그 자리는 못 본다.
        if j >= c.len() || c[j] != '"' {
            out.dynamic += 1;
            return;
        }
        ctx = read_string(c, &mut j);
        skip_ws(c, &mut j);
        if ctx.is_none() || j >= c.len() || c[j] != ',' {
            out.dynamic += 1;
            return;
        }
        j += 1;
        skip_ws(c, &mut j);
    }
    if j >= c.len() || c[j] != '"' {
        out.dynamic += 1;
        return;
    }
    let Some(lit) = read_string(c, &mut j) else {
        out.dynamic += 1;
        return;
    };
    out.sites += 1;
    if lit.chars().any(is_hangul) {
        out.pairs.insert((ctx, lit));
    }
}

fn is_hangul(ch: char) -> bool {
    matches!(ch, '\u{AC00}'..='\u{D7A3}' | '\u{1100}'..='\u{11FF}' | '\u{3130}'..='\u{318F}')
}

fn is_ident_char(ch: char) -> bool {
    ch.is_alphanumeric() || ch == '_'
}

fn skip_ws(c: &[char], i: &mut usize) {
    while *i < c.len() && c[*i].is_whitespace() {
        *i += 1;
    }
}

fn skip_block_comment(c: &[char], i: &mut usize) {
    let n = c.len();
    let mut depth = 1;
    *i += 2;
    while *i < n && depth > 0 {
        if c[*i] == '/' && *i + 1 < n && c[*i + 1] == '*' {
            depth += 1;
            *i += 2;
        } else if c[*i] == '*' && *i + 1 < n && c[*i + 1] == '/' {
            depth -= 1;
            *i += 2;
        } else {
            *i += 1;
        }
    }
}

fn skip_char_literal(c: &[char], i: &mut usize) {
    let n = c.len();
    *i += 1; // 여는 따옴표
    while *i < n {
        if c[*i] == '\\' {
            *i += 2;
            continue;
        }
        if c[*i] == '\'' {
            *i += 1;
            return;
        }
        *i += 1;
    }
}

fn is_raw_string_start(c: &[char], i: usize) -> bool {
    let mut k = i + 1;
    if c[i] == 'b' {
        // `br"…"` 도 날 문자열이다. `b"…"` 는 보통 문자열 경로가 처리한다.
        if k < c.len() && c[k] == 'r' {
            k += 1;
        } else {
            return false;
        }
    }
    // 앞이 식별자 문자면 남의 이름 꼬리다(`for` 의 `r` 따위).
    if i > 0 && is_ident_char(c[i - 1]) {
        return false;
    }
    let mut hashes = 0;
    while k + hashes < c.len() && c[k + hashes] == '#' {
        hashes += 1;
    }
    k + hashes < c.len() && c[k + hashes] == '"'
}

fn skip_raw_string(c: &[char], i: &mut usize) {
    let n = c.len();
    let mut k = *i + 1;
    if c[*i] == 'b' {
        k += 1;
    }
    let mut hashes = 0;
    while k + hashes < n && c[k + hashes] == '#' {
        hashes += 1;
    }
    let mut p = k + hashes + 1; // 여는 따옴표 다음
    while p < n {
        if c[p] == '"' {
            let closed = (1..=hashes).all(|h| p + h < n && c[p + h] == '#');
            if closed {
                *i = p + hashes + 1;
                return;
            }
        }
        p += 1;
    }
    *i = n;
}

/// 보통 문자열 하나를 읽어 **값**(이스케이프를 푼 것)을 돌려준다. `t()` 의 키는 값이다.
fn read_string(c: &[char], i: &mut usize) -> Option<String> {
    let n = c.len();
    let mut out = String::new();
    *i += 1; // 여는 따옴표
    while *i < n {
        let ch = c[*i];
        if ch == '"' {
            *i += 1;
            return Some(out);
        }
        if ch != '\\' {
            out.push(ch);
            *i += 1;
            continue;
        }
        *i += 1;
        if *i >= n {
            break;
        }
        let esc = c[*i];
        *i += 1;
        match esc {
            'n' => out.push('\n'),
            't' => out.push('\t'),
            'r' => out.push('\r'),
            '0' => out.push('\0'),
            '\\' => out.push('\\'),
            '"' => out.push('"'),
            '\'' => out.push('\''),
            // `\` + 줄바꿈은 그 줄의 앞 공백까지 삼킨다(러스트 규칙).
            '\n' => skip_ws(c, i),
            // ⚠ **CRLF 도 같은 줄바꿈이다.** 이 갈래가 없으면 `\r` 이 아래
            //   `other => out.push(other)` 로 떨어져 이어붙인 리터럴에 `\r\n` 이 남고,
            //   `en_*.rs` 의 짝(한 줄로 합쳐진 원문)과 안 맞아 **「영어 짝 없는 원문이
            //   늘었다」로 오탐**한다. 원문도 번역도 멀쩡한데 게이트만 빨개지는 부류다.
            //   ⛔ 작업본 줄끝은 상자마다 다르다 — 맥(LF)에서는 안 나고 Windows(CRLF)
            //   에서만 난다(실측 2026-08-08 alienware: `proto/src/boot.rs` 의 이어붙인
            //   문장 둘이 그렇게 걸렸다). 줄끝에 기대는 스캐너는 «저쪽 상자에서 초록»이
            //   판정이 못 된다.
            '\r' => {
                if *i < n && c[*i] == '\n' {
                    *i += 1;
                }
                skip_ws(c, i);
            }
            'u' => {
                if *i < n && c[*i] == '{' {
                    *i += 1;
                    let mut hex = String::new();
                    while *i < n && c[*i] != '}' {
                        hex.push(c[*i]);
                        *i += 1;
                    }
                    *i += 1;
                    if let Some(ch) = u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32) {
                        out.push(ch);
                    }
                }
            }
            'x' => {
                let mut hex = String::new();
                for _ in 0..2 {
                    if *i < n {
                        hex.push(c[*i]);
                        *i += 1;
                    }
                }
                if let Some(ch) = u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32) {
                    out.push(ch);
                }
            }
            other => out.push(other),
        }
    }
    None
}

/// `i` 가 `#[cfg(test)]`(또는 `#![cfg(test)]`)의 시작인가.
fn starts_cfg_test(c: &[char], i: usize) -> bool {
    let mut k = i + 1;
    if k < c.len() && c[k] == '!' {
        k += 1;
    }
    if k >= c.len() || c[k] != '[' {
        return false;
    }
    let mut depth = 0;
    let mut body = String::new();
    while k < c.len() {
        match c[k] {
            '[' => depth += 1,
            ']' => {
                depth -= 1;
                if depth == 0 {
                    break;
                }
            }
            ch => body.push(ch),
        }
        k += 1;
    }
    body.replace(char::is_whitespace, "") == "cfg(test)"
}

fn skip_attr(c: &[char], i: &mut usize) {
    while *i < c.len() && c[*i] != '[' {
        *i += 1;
    }
    let mut depth = 0;
    while *i < c.len() {
        match c[*i] {
            '[' => depth += 1,
            ']' => {
                depth -= 1;
                *i += 1;
                if depth == 0 {
                    return;
                }
                continue;
            }
            _ => {}
        }
        *i += 1;
    }
}

/// 속성 다음에 오는 항목 하나를 건너뛴다. `mod … { … }` 면 중괄호까지, `use …;` 면 `;` 까지.
fn skip_item(c: &[char], i: &mut usize) {
    let n = c.len();
    let mut depth: i32 = 0;
    while *i < n {
        match c[*i] {
            '/' if *i + 1 < n && c[*i + 1] == '/' => {
                while *i < n && c[*i] != '\n' {
                    *i += 1;
                }
                continue;
            }
            '/' if *i + 1 < n && c[*i + 1] == '*' => {
                skip_block_comment(c, i);
                continue;
            }
            '"' => {
                let _ = read_string(c, i);
                continue;
            }
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                *i += 1;
                if depth == 0 {
                    return;
                }
                continue;
            }
            ';' if depth == 0 => {
                *i += 1;
                return;
            }
            _ => {}
        }
        *i += 1;
    }
}

/// 지금 en 카탈로그에 짝이 없는 원문. `t()` 를 실제로 불러 재므로 `en_server` 처럼
/// 정본에서 뽑아 온 표까지 함께 센다 — 재는 것은 **사용자가 en 에서 무엇을 보는가**다.
fn missing_now(scan: &Scan) -> BTreeSet<String> {
    base::i18n::with_locale("en", || {
        scan.pairs
            .iter()
            .filter(|(ctx, ko)| match ctx {
                Some(ctx) => base::i18n::tc(ctx, ko) == ko.as_str(),
                None => base::i18n::t(ko) == ko.as_str(),
            })
            .map(|(_, ko)| ko.clone())
            .collect()
    })
}

#[test]
fn the_scan_actually_reads_the_tree() {
    // 공허 방지 ① — 스캐너가 아무것도 안 찾아도 "미번역 0" 은 초록이다. 찾은 양을 잰다.
    let scan = scan_workspace();
    assert!(scan.files >= 40, "긁은 파일이 {}개뿐이다", scan.files);
    assert!(scan.sites >= 150, "리터럴 호출부가 {}개뿐이다", scan.sites);
    let korean = scan.korean();
    assert!(korean.len() >= 120, "한국어 원문이 {}개뿐이다", korean.len());
    // 공허 방지 ② — 아는 호출부가 실제로 잡혀야 한다. 앵커는 **제보의 그 낱말**이다
    // (`screens.rs` 확인 판의 `t("취소")`).
    assert!(
        korean.contains("취소"),
        "아는 원문 `취소` 를 못 찾았다 — 스캐너가 눈멀었다"
    );
    // 공허 방지 ③ — en 판정 자체가 사는지. 이게 죽으면 전부 "번역됨"으로 통과한다.
    assert_eq!(base::i18n::with_locale("en", || base::i18n::t("취소")), "Cancel");
}

#[test]
fn no_new_untranslated_korean_slips_in() {
    let scan = scan_workspace();
    let missing = missing_now(&scan);
    let ratchet: BTreeSet<String> = UNTRANSLATED.iter().map(|s| s.to_string()).collect();

    let fresh: Vec<&String> = missing.difference(&ratchet).collect();
    let stale: Vec<&String> = ratchet.difference(&missing).collect();

    assert!(
        fresh.is_empty(),
        "영어 짝 없는 한국어 원문이 {}개 늘었다 — `base/src/i18n/en_*.rs` 에 넣는다.\n\
         (미루려면 사유를 CL 에 적고 `UNTRANSLATED` 에 더한다)\n{}",
        fresh.len(),
        fresh
            .iter()
            .map(|s| format!("    {s:?},"))
            .collect::<Vec<_>>()
            .join("\n")
    );
    assert!(
        stale.is_empty(),
        "번역이 채워졌는데 래칫이 안 좁혀졌다 — `UNTRANSLATED` 에서 이 {}줄을 지운다.\n{}",
        stale.len(),
        stale
            .iter()
            .map(|s| format!("    {s:?},"))
            .collect::<Vec<_>>()
            .join("\n")
    );
}

/// 지금 목록을 그대로 붙여 넣을 모양으로 찍는다(래칫을 다시 뽑는 길).
///
/// `cargo test -p base --test i18n_coverage -- print_the_untranslated --nocapture`
#[test]
fn print_the_untranslated() {
    let scan = scan_workspace();
    let missing = missing_now(&scan);
    println!(
        "\n// 파일 {} · 리터럴 호출부 {} · 한국어 원문 {} · 리터럴 아닌 자리 {}(스캐너 사각지대)\n\
         // 영어 짝 없음: {}\nstatic UNTRANSLATED: &[&str] = &[",
        scan.files,
        scan.sites,
        scan.korean().len(),
        scan.dynamic,
        missing.len()
    );
    for ko in &missing {
        println!("    {ko:?},");
    }
    println!("];");
}

/// 래칫에 **코드에서 사라진 원문**이 남아 있지 않은지. 남으면 수가 실제보다 커 보인다.
#[test]
fn the_ratchet_has_no_ghost_rows() {
    let korean = scan_workspace().korean();
    let ghosts: Vec<&&str> = UNTRANSLATED
        .iter()
        .filter(|ko| !korean.contains(**ko))
        .collect();
    assert!(
        ghosts.is_empty(),
        "코드에 더는 없는 원문이 래칫에 {}줄 남았다 — 지운다.\n{ghosts:?}",
        ghosts.len()
    );
}

/// 파일 목록을 뽑는 규칙 자체가 도는지(제외가 통째로 실패하면 상류 글이 섞인다).
#[test]
fn exclusions_hold() {
    let root: PathBuf = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().into();
    assert!(is_excluded(&root.join("warpui/src/lib.rs")));
    assert!(is_excluded(&root.join("base/src/i18n/en_core.rs")));
    assert!(is_excluded(&root.join("base/src/i18n.rs")));
    assert!(is_excluded(&root.join("gui/src/session_view_tests.rs")));
    assert!(is_excluded(&root.join("base/tests/i18n_switch.rs")));
    assert!(!is_excluded(&root.join("base/src/keymap.rs")));
    assert!(!is_excluded(&root.join("gui/src/session_view.rs")));
}
