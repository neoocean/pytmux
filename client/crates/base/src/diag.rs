//! **이 클라의 런타임을 재는 자** — `debug-stats` 의 GUI 판(pytmux-457).
//!
//! # 왜 정본의 것을 옮겨 오지 않나
//!
//! 정본 `debug-stats`(`pytmuxlib/clientdiag.py`)가 내는 표는 **파이썬 클라 프로세스의
//! 것**이다 — `gc.get_objects()` 로 센 산 객체, CPython 세대별 수거 횟수, Textual 의
//! `Timer`·`Strip`·`FIFOCache`. 우리에게는 그 힙이 아예 없다. 그래서 패리티 래칫은 이
//! 줄을 **면제**했고(`NOT_IN_PALETTE`), 그 주석이 스스로 조건을 적어 뒀다:
//! *"GUI 가 제 런타임을 재는 같은 화면을 갖게 되면 그때 이 줄을 지운다."*
//!
//! 그 화면이 이것이다. ⛔ **면제와 분류는 다른 일이다** — 면제는 「팔레트에 없어도
//! 된다」이고 분류는 「GUI 에 아직 없다」다. 여기서 없애는 것은 **뒤엣것**이다.
//!
//! # 계약은 「같은 이름 · 같은 뜻」까지다
//!
//! 항목이 1:1 이 아니다(런타임이 다르다). 같아야 하는 것은 셋이다 —
//! ⑴ 팔레트에 **같은 이름**으로 있고 ⑵ 뜻이 **내 프로세스를 잰다**이고
//! ⑶ 판의 손이 정본과 같다(정본은 범용 `InfoScreen` 에 띄운다 · 아무 키나 닫고
//! `↑↓`·`PgUp/PgDn`·`Home/End` 가 굴린다). 항목의 갈림은 대장 ⓑ 로 적는다.
//!
//! # ⛔ 값 없는 판을 초록으로 접지 않는다
//!
//! 이 모듈이 지는 약속은 **값이 실제로 움직인다**는 것이다. 프레임을 두 번 돌리면
//! 프레임 수가 늘고, 그 사실을 `diag_tests.rs` 가 잰다. 채울 자리가 없어 `None` 인
//! 칸은 **모른다고 적는다** — 0 으로 적으면 「쟀는데 0」과 구별되지 않는다.
//!
//! # 왜 뷰가 아니라 여기인가
//!
//! 값을 **모으는** 것은 뷰만 할 수 있고(글리프 캐시도 씬도 뷰의 것이다), 값을 **줄로
//! 만드는** 것은 뷰가 몰라도 된다. 그 경계를 그으면 줄의 모양을 오라클이 뷰 없이 잰다
//! — `crate::hooks`·`crate::chrome` 과 같은 자리다.

use alloc_free::format_ms;

/// 프레임 시간을 몇 개나 들고 있나. 짧으면 튄 값 하나에 표가 흔들리고, 길면 방금
/// 무거워진 것을 못 본다. 정본이 `TOP_TYPES = 12` 를 고른 것과 같은 종류의 판단이다.
pub const FRAME_WINDOW: usize = 60;

mod alloc_free {
    /// `1234.5` → `"1234.5"`. 소수 한 자리 — 프레임 시간은 그 이상 볼 값이 없다.
    pub fn format_ms(v: f64) -> String {
        format!("{v:.1}")
    }
}

/// 이 클라가 자기에 대해 아는 것. **채운 자리만** 표에 뜬다.
///
/// ⛔ `Option` 을 0 으로 바꾸지 마라 — 「못 쟀다」와 「쟀는데 0」은 다른 사실이고,
/// 이 표는 그 둘을 가르려고 있다(정본 `clientdiag` 의 `rss_bytes` 가 같은 규율이다).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct RuntimeStats {
    pub pid: u32,
    /// 이 뷰가 그린 프레임 수(누적).
    pub frames: u64,
    /// 최근 프레임 시간(ms) — 새 것이 뒤에 온다. 최대 [`FRAME_WINDOW`] 개.
    pub frame_ms: Vec<f64>,
    /// 글리프(문자) 캐시가 들고 있는 항목 수.
    pub glyph_cache: Option<usize>,
    /// 마지막 씬의 그리기 원소 수.
    pub scene_nodes: Option<usize>,
    /// 마지막 프레임이 실제로 그린 **칸 수**.
    ///
    /// 씬 원소 수의 자리를 대신하는 값이 아니라 **다른 값**이다 — 저쪽은 상류가 만든
    /// 그리기 원소이고 이쪽은 우리가 채운 격자다. 상류가 전자를 안 내주는 동안 우리가
    /// 아는 「이 프레임에 한 일」의 크기가 이것이라, 두 칸을 나란히 둔다.
    pub painted_cells: Option<usize>,
    /// 아직 서버로 못 보낸 명령 수.
    pub queue_depth: usize,
    /// 링크 왕복 시간(ms).
    pub rtt_ms: Option<f64>,
    /// 상주 메모리(바이트). 못 알아내는 OS 에서는 `None` 이다.
    pub rss: Option<u64>,
    /// 지금 격자(칸).
    pub grid: (u16, u16),
    pub tabs: usize,
    pub panes: usize,
    /// 열려 있는 판의 깊이 — 판을 여닫은 것이 **거둬지나**의 가장 싼 값이다
    /// (정본 `screen_depth` 와 같은 뜻).
    pub screen_depth: usize,
}

fn mb(n: Option<u64>) -> String {
    match n {
        None => crate::i18n::t("이 OS 에서는 못 잰다").to_owned(),
        Some(v) => format!("{:.1} MB", v as f64 / 1_048_576.0),
    }
}

fn or_unknown(n: Option<usize>) -> String {
    match n {
        None => crate::i18n::t("못 쟀다").to_owned(),
        Some(v) => v.to_string(),
    }
}

impl RuntimeStats {
    /// 최근 프레임 시간의 `(중앙값, 최댓값)` — 표본이 없으면 `None`.
    ///
    /// 평균이 아니라 **중앙값**이다: 프레임 하나가 200ms 튀어도 나머지가 멀쩡하면
    /// 그것은 「느려졌다」가 아니고, 평균은 그 둘을 섞어 버린다.
    pub fn frame_median_max(&self) -> Option<(f64, f64)> {
        if self.frame_ms.is_empty() {
            return None;
        }
        let mut sorted = self.frame_ms.clone();
        sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(core::cmp::Ordering::Equal));
        let mid = sorted[sorted.len() / 2];
        let max = *sorted.last().unwrap_or(&mid);
        Some((mid, max))
    }

    /// 판에 실을 줄들. 값 옆에 **무엇을 보는 값인지**를 적는다 — 숫자 하나만 보면
    /// 「많은 건가」를 알 수 없고, 이 명령은 그것을 알려주려고 있다(정본 `render` 와
    /// 같은 규율).
    pub fn lines(&self) -> Vec<String> {
        let t = crate::i18n::t;
        let mut out = vec![
            format!("pid {} · {} {}×{}", self.pid, t("격자"), self.grid.0, self.grid.1),
            format!("{} {}", t("상주 메모리"), mb(self.rss)),
            String::new(),
            format!("― {} ―", t("그리는 쪽")),
            format!("  {} {}", t("그린 프레임"), self.frames),
        ];
        match self.frame_median_max() {
            Some((mid, max)) => out.push(format!(
                "  {} {} ms · {} {} ms  ({} {})",
                t("프레임 중앙값"),
                format_ms(mid),
                t("최댓값"),
                format_ms(max),
                t("최근 표본"),
                self.frame_ms.len()
            )),
            // ⛔ 0 으로 적지 않는다 — 아직 두 번을 안 그린 것이지 0ms 가 아니다.
            None => out.push(format!("  {}", t("프레임 시간 표본이 아직 없다"))),
        }
        out.push(format!("  {} {}", t("글리프 캐시"), or_unknown(self.glyph_cache)));
        out.push(format!("  {} {}", t("씬 원소"), or_unknown(self.scene_nodes)));
        out.push(format!("  {} {}", t("그린 칸"), or_unknown(self.painted_cells)));
        out.push(String::new());
        out.push(format!("― {} ―", t("붙어 있는 쪽")));
        out.push(format!(
            "  {} {}",
            t("링크 RTT"),
            match self.rtt_ms {
                Some(v) => format!("{} ms", format_ms(v)),
                None => t("아직 표본이 없다").to_owned(),
            }
        ));
        out.push(format!("  {} {}", t("보낼 큐 깊이"), self.queue_depth));
        out.push(format!(
            "  {} {} · {} {} · {} {}",
            t("탭"),
            self.tabs,
            t("패널"),
            self.panes,
            t("판 깊이"),
            self.screen_depth
        ));
        out.push(String::new());
        out.push(t("이 표는 **이 프로세스**를 잰다 — 정본의 같은 이름은 파이썬 클라의 \
                    힙을 재고, 항목이 1:1 이 아닌 것은 그래서다(런타임이 다르다).")
            .to_owned());
        out.push(t("값이 «자라다 눕는» 모양이면 상시 세금이지 누적이 아니다. \
                    계속 자라면 그것이 새 결함이다.")
            .to_owned());
        out
    }
}

#[cfg(test)]
#[path = "diag_tests.rs"]
mod tests;
