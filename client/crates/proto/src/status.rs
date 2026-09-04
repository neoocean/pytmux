//! 상태줄 **형식 문자열**을 펼친다(`status-left`/`status-right`).
//!
//! # 문법은 파이썬이 정본이다
//!
//! `clientwidgets.StatusBar._expand` 와 같은 표다 — 토큰 여섯과 `strftime` 코드. 두
//! 클라가 **같은 설정 파일을 공유하므로**(로드맵 결정 3) 여기서 문법이 갈리면 한쪽에서
//! 적은 줄이 다른 쪽에서 깨진다.
//!
//! | 토큰 | 무엇 |
//! |---|---|
//! | `#S` | 세션 이름 |
//! | `#I` | 지금 탭 번호(**1부터** — 화면에 보이는 번호) |
//! | `#W` | 지금 탭 이름 |
//! | `#h` | 호스트 이름(짧게 — 첫 점 앞까지) |
//! | `#H` | 호스트 이름(전체) |
//! | `#{pane_title}` | 패널 제목 + `" · "`. 제목이 없거나 `shell` 이면 **빈 값** |
//!
//! # 순서가 규칙이다
//!
//! **`strftime` 을 먼저** 적용하고 그다음 토큰을 치환한다(파이썬과 같다). 뒤집으면 세션
//! 이름에 든 `%` 가 시각 코드로 읽혀 엉뚱한 글자가 나온다 — 사용자가 지은 이름이 형식
//! 문자열의 일부가 되는 것은 놀랍다.
//!
//! 잘못된 `strftime` 코드는 **원문 그대로 둔다**(파이썬의 `except ValueError`). 형식을
//! 잘못 적었다고 상태줄이 통째로 비면 무엇이 잘못됐는지 알 수 없다.

use chrono::Local;

/// 토큰을 채우는 데 필요한 것들. 값의 출처가 여럿이라 모아 받는다.
#[derive(Debug, Clone, Default)]
pub struct StatusCtx {
    pub session: String,
    /// 지금 탭의 **표시 번호**(1부터). 탭이 없으면 `None` — 그 자리는 빈 값이 된다.
    pub tab_number: Option<usize>,
    pub tab_name: String,
    pub pane_title: String,
}

/// 호스트 이름. 못 알아내면 빈 문자열이다(`#h` 가 빈 자리가 된다).
///
/// `std` 에 이 물음이 없어 OS 마다 다른 길을 쓴다. 빈 값을 돌려주는 편이 `unknown` 같은
/// 가짜 이름보다 낫다 — 상태줄에 `unknown` 이 적혀 있으면 그게 진짜 호스트 이름인 줄 안다.
pub fn hostname() -> String {
    #[cfg(windows)]
    {
        std::env::var("COMPUTERNAME").unwrap_or_default()
    }
    #[cfg(unix)]
    {
        // ⚠ `c_char` 의 부호는 **플랫폼이 정한다** — macOS·x86_64 리눅스는 `i8`, aarch64
        //   리눅스는 `u8` 이다. 종전의 `[0i8; 256]` 은 앞 둘에서만 맞아 리눅스 arm64 에서
        //   proto 가 아예 안 컴파일됐다(pytmux-464 · 2026-09-04 Docker aarch64 실측 —
        //   CI 는 x86_64 러너라 한 번도 못 잡았다).
        let mut buf = [0 as libc::c_char; 256];
        // SAFETY: 버퍼 길이를 그대로 넘기고, 결과는 NUL 까지만 읽는다.
        let rc = unsafe { libc::gethostname(buf.as_mut_ptr(), buf.len()) };
        if rc != 0 {
            return String::new();
        }
        let bytes: Vec<u8> = buf
            .iter()
            .take_while(|c| **c != 0)
            .map(|c| *c as u8)
            .collect();
        String::from_utf8_lossy(&bytes).into_owned()
    }
    #[cfg(not(any(windows, unix)))]
    {
        String::new()
    }
}

/// 사용자가 설정에 적은 색 이름 → 우리 색. 모르면 `None` = **테마 그대로**.
///
/// 서버의 색 표기(`bright_black`)와 사람이 칠 만한 표기(`brightblack`)를 둘 다 받는다 —
/// 밑줄을 빼먹었다고 색이 조용히 안 먹으면 무엇이 잘못됐는지 알 수 없다. `#rrggbb` 도
/// [`Color::parse`](crate::style::Color::parse) 가 이미 안다.
pub fn color(name: &str) -> Option<crate::style::Color> {
    let name = name.trim();
    if name.is_empty() {
        return None;
    }
    crate::style::Color::parse(name).or_else(|| {
        // `brightblue` → `bright_blue`. 이 한 겹만 편다(그 밖의 표기는 모르는 것이 맞다).
        let rest = name.strip_prefix("bright")?;
        crate::style::Color::parse(&format!("bright_{rest}"))
    })
}

/// 형식 문자열 하나를 펼친다.
pub fn expand(fmt: &str, ctx: &StatusCtx) -> String {
    expand_at(fmt, ctx, &Local::now().format("%Y-%m-%dT%H:%M:%S").to_string())
}

/// 시각을 **밖에서 받는** 판 — 테스트가 쓴다(같은 형식이 늘 같은 글자를 내야 잰다).
///
/// `now` 는 `%Y-%m-%dT%H:%M:%S` 꼴이다. 이 함수가 시계를 직접 읽으면 오라클이 초를
/// 넘길 때마다 흔들린다.
pub fn expand_at(fmt: &str, ctx: &StatusCtx, now: &str) -> String {
    let stamped = strftime(fmt, now);
    let host = hostname();
    // 제목이 `shell` 이면 빈 값이다 — 기본 제목을 적어 봐야 아무것도 안 알려 준다
    // (파이썬과 같은 규칙).
    let title = if ctx.pane_title.is_empty() || ctx.pane_title == "shell" {
        String::new()
    } else {
        format!("{} · ", ctx.pane_title)
    };
    stamped
        .replace("#{pane_title}", &title)
        .replace("#S", &ctx.session)
        .replace(
            "#I",
            &ctx.tab_number.map(|n| n.to_string()).unwrap_or_default(),
        )
        .replace("#W", &ctx.tab_name)
        // ★ `#H` 를 **먼저** 바꾼다. `#h` 를 먼저 하면 `#H` 는 안 걸리지만, 반대로 짧은
        // 이름이 긴 이름의 접두라 순서를 흐리면 헷갈린다 — 둘을 갈라 두는 값이다.
        .replace("#H", &host)
        .replace("#h", host.split('.').next().unwrap_or(""))
}

/// `strftime` 코드를 채운다. **모르는 코드가 하나라도 있으면 원문을 그대로** 돌려준다.
///
/// 전부-아니면-전무인 이유는 파이썬과 같다 — 저쪽은 `ValueError` 하나로 문자열 전체를
/// 포기한다. 절반만 채우면 어디까지 먹었는지 눈으로 못 가린다.
fn strftime(fmt: &str, now: &str) -> String {
    let Ok(when) = chrono::NaiveDateTime::parse_from_str(now, "%Y-%m-%dT%H:%M:%S") else {
        return fmt.to_owned();
    };
    let items = chrono::format::StrftimeItems::new(fmt);
    if items
        .clone()
        .any(|item| matches!(item, chrono::format::Item::Error))
    {
        return fmt.to_owned();
    }
    when.format_with_items(items).to_string()
}

// ── 우측 상태줄의 **구간 분해**(G9w — 파이썬 `_expand_parts` 동형) ─────────────
//
// 상태줄 오른쪽의 시각·날짜·호스트는 파이썬에서 **클릭 존**이다(시각=시계 토글 ·
// 날짜=달력 토글 · 호스트=서버 탭). 존을 만들려면 펼친 문자열 어느 구간이 어느
// 토큰에서 왔는지 알아야 하므로, 통짜 [`expand`] 와 별도로 런 목록을 낸다.

/// 우측 런 하나의 종류. `Plain` 이외는 클릭 존이 된다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StatusRun {
    Plain,
    /// `#h`/`#H` — 클릭은 서버 탭(우리 `Badge::Host` 와 같은 액션).
    Host,
    /// 시각 계열 strftime(`%H:%M` 등) — 클릭은 시계 토글.
    Time,
    /// 날짜 계열 strftime(`%Y-%m-%d` 등) — 클릭은 달력 토글.
    Date,
    /// `#S` — 세션 이름. 클릭은 **그 자리 편집**이다(pytmux-3 제보).
    ///
    /// ⚠ **배지가 아니다.** [`run_badge`] 가 `None` 을 돌려주는 것이 `Plain` 과 같아
    /// 보이지만 뜻이 다르다 — `Plain` 은 누를 자리가 아니고, 이것은 누르면 **화면이
    /// 아니라 입력칸**이 열린다(팝업을 여는 다른 런들과 갈리는 지점이라 배지 표에
    /// 억지로 끼우지 않는다). 뷰가 이 런을 따로 알아보고 자기 편집 상태를 연다.
    ///
    /// 고유 종류인 이유는 파이썬과 같다: `_merge_runs`(여기서는 아래 ②)가 인접 동종만
    /// 합치므로, 종류를 나누는 것만으로 세션 이름 구간이 그대로 남아 **그 폭이 곧
    /// 누를 자리**가 된다.
    Session,
}

/// 시각/날짜로 분류하는 strftime 코드(파이썬 `_TIME_STRFTIME`/`_DATE_STRFTIME` 동형).
const TIME_CODES: &str = "HIMSpRTrXkl";
const DATE_CODES: &str = "YymdbBaAjeDFuwUWxgGCV";

/// 지금 시각으로 편다(뷰가 쓴다).
pub fn expand_parts(fmt: &str, ctx: &StatusCtx) -> Vec<(StatusRun, String)> {
    expand_parts_at(fmt, ctx, &Local::now().format("%Y-%m-%dT%H:%M:%S").to_string())
}

/// 시각을 밖에서 받는 판 — 오라클용([`expand_at`] 과 같은 이유).
///
/// 파이썬과 같은 두 단계 병합을 지난다: ① 시각/날짜 런 **사이의 구분자만인**
/// plain(`:-/. `)을 양옆 종류로 흡수(`%H:%M` 이 한 시계 존이 된다) ② 인접 동종 병합.
pub fn expand_parts_at(fmt: &str, ctx: &StatusCtx, now: &str) -> Vec<(StatusRun, String)> {
    let host = hostname();
    let title = if ctx.pane_title.is_empty() || ctx.pane_title == "shell" {
        String::new()
    } else {
        format!("{} · ", ctx.pane_title)
    };
    let chars: Vec<char> = fmt.chars().collect();
    let mut runs: Vec<(StatusRun, String)> = Vec::new();
    let mut push = |kind: StatusRun, text: String| runs.push((kind, text));
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '#' {
            let rest: String = chars[i..].iter().collect();
            if rest.starts_with("#{pane_title}") {
                push(StatusRun::Plain, title.clone());
                i += "#{pane_title}".chars().count();
                continue;
            }
            if let Some(&code) = chars.get(i + 1) {
                let (kind, text) = match code {
                    'h' => (StatusRun::Host, host.split('.').next().unwrap_or("").to_owned()),
                    'H' => (StatusRun::Host, host.clone()),
                    'S' => (StatusRun::Session, ctx.session.clone()),
                    'I' => (
                        StatusRun::Plain,
                        ctx.tab_number.map(|n| n.to_string()).unwrap_or_default(),
                    ),
                    'W' => (StatusRun::Plain, ctx.tab_name.clone()),
                    _ => {
                        push(StatusRun::Plain, c.to_string());
                        i += 1;
                        continue;
                    }
                };
                push(kind, text);
                i += 2;
                continue;
            }
        }
        if c == '%' && i + 1 < chars.len() {
            let code = chars[i + 1];
            if code == '%' {
                push(StatusRun::Plain, "%".to_owned());
                i += 2;
                continue;
            }
            // 한 코드만 채운다 — 모르는 코드는 원문 그대로(파이썬 ValueError 경로).
            let val = strftime(&format!("%{code}"), now);
            let kind = if TIME_CODES.contains(code) {
                StatusRun::Time
            } else if DATE_CODES.contains(code) {
                StatusRun::Date
            } else {
                StatusRun::Plain
            };
            push(kind, val);
            i += 2;
            continue;
        }
        push(StatusRun::Plain, c.to_string());
        i += 1;
    }
    // ① 같은 종류 사이의 구분자 흡수.
    let mut absorbed: Vec<(StatusRun, String)> = Vec::new();
    for (idx, (kind, text)) in runs.iter().enumerate() {
        let mut kind = *kind;
        if kind == StatusRun::Plain
            && !text.is_empty()
            && text.chars().all(|ch| ":-/. ".contains(ch))
            && let Some((prev, _)) = absorbed.last()
            && matches!(prev, StatusRun::Time | StatusRun::Date)
            && runs.get(idx + 1).is_some_and(|(next, _)| next == prev)
        {
            kind = *prev;
        }
        absorbed.push((kind, text.clone()));
    }
    // ② 인접 동종 병합 + 빈 것 제거.
    let mut merged: Vec<(StatusRun, String)> = Vec::new();
    for (kind, text) in absorbed {
        match merged.last_mut() {
            Some((last, acc)) if *last == kind => acc.push_str(&text),
            _ => merged.push((kind, text)),
        }
    }
    merged.retain(|(_, text)| !text.is_empty());
    merged
}

/// 런 종류가 여는 배지(=클릭이 하는 일). 뷰가 각자 매핑하면 시계/달력이 뒤바뀌어도
/// 아무도 안 운다 — 한 벌로 두고 단위 오라클이 지킨다.
pub fn run_badge(run: StatusRun) -> Option<base::Badge> {
    match run {
        StatusRun::Plain => None,
        StatusRun::Host => Some(base::Badge::Host),
        StatusRun::Time => Some(base::Badge::Clock),
        StatusRun::Date => Some(base::Badge::Calendar),
        // ⚠ 세션 이름은 배지를 안 연다 — 누르면 **그 자리가 입력칸**이 된다(pytmux-3).
        // 배지 하나를 억지로 붙이면 클릭이 팝업을 여는 다른 런들과 같은 뜻이 되고,
        // 그건 제보가 *"판을 띄우지 말라"* 고 한 바로 그 동작이다.
        StatusRun::Session => None,
    }
}

/// 상태줄 **메시지 한 줄**(끊김·서버 오류). 자리는 뷰가 정하고 **글은 여기가** 정한다 —
/// 종전에는 두 뷰가 각자 같은 `tf` 를 적고 있었고, 그런 짝은 한쪽만 고쳐지면 같은 사건이
/// 클라마다 다른 글로 보인다.
///
/// 끊김이 오류를 이긴다: 연결이 끝난 뒤에 남은 마지막 서버 오류는 이미 지난 이야기이고,
/// 지금 알아야 하는 것은 "붙어 있지 않다"다.
///
/// 지나간 메시지는 이 한 줄에 남지 않는다 — 이력은 알림 화면([`base::Badge::Notices`])
/// 이 갖는다. 그래서 두 뷰가 이 줄을 그 배지와 **같은 클릭 대상**으로 감싼다.
pub fn message_line(ended: Option<&str>, error: Option<&str>) -> Option<String> {
    use base::i18n::tf;

    if let Some(reason) = ended {
        return Some(tf("연결 종료: {reason}", &[("reason", reason)]));
    }
    error.map(|err| tf("서버 오류: {err}", &[("err", err)]))
}

#[cfg(test)]
#[path = "status_tests.rs"]
mod tests;
