//! 서버가 **제 프로세스를 잰** 한 장 — `debug_stats` 회신(pytmux-382).
//!
//! # 왜 이 표가 따로 있나
//!
//! `debug-stats` 판의 클라 절반([`base::diag::RuntimeStats`])은 **이 프로세스**를 잰다.
//! 그런데 pytmux-382 의 조사가 실제로 필요로 한 것은 **서버**였다 — office1 의 서버가
//! 코어를 먹는 이유를 가리려고 py-spy 로 라이브 프로세스를 떠야 했고, 그 조사 코멘트가
//! *"`:debug-stats` 의 서버 절반이 있었으면 위 전부가 한 줄이었다"* 고 적었다. 이것이
//! 그 절반이다.
//!
//! # 서버는 숫자만 싣는다
//!
//! ⛔ 서버가 문장을 짓지 않는다 — 문장은 클라의 로케일로 여기서 짓는다(`base::i18n::t`).
//! 서버가 지은 글은 서버 로케일로 굳어 영어 사용자에게 한국어로 뜬다(pytmux-419 부류).
//! 그래서 와이어는 **숫자와 이름**뿐이고 라벨은 전부 이 파일이 든다.
//!
//! # 못 잰 것은 `None` 이다
//!
//! `0` 으로 적지 않는다 — 0 은 「없다」로 읽히고, 그건 우리가 모르는 사실이다(Windows 의
//! fd 수처럼 그 OS 에서 못 세는 값). `base::diag` 의 같은 규율.

use serde::Deserialize;

/// gc 세대 한 줄(정본 `gc.get_stats()` 한 원소).
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
pub struct GcGeneration {
    /// 세대 번호(와이어 이름은 `gen` — Rust 2024 의 예약어라 필드 이름만 다르다).
    #[serde(default, rename = "gen")]
    pub generation: u32,
    #[serde(default)]
    pub collections: u64,
    #[serde(default)]
    pub collected: u64,
    #[serde(default)]
    pub uncollectable: u64,
}

/// 그림자 `/usage` 프로브의 **마지막 회차**(서버 `_usage_probe_last` · CL 75059).
///
/// 이 값이 이 표에 있는 이유가 pytmux-382 의 결론이다 — 그 서버 CPU 의 지배적 소비자가
/// 이 프로브였고, 예산의 85~95% 를 쓰고 있었다. 「느려졌나」를 볼 때 제일 먼저 볼 줄이다.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
pub struct UsageProbeTiming {
    #[serde(default)]
    pub boot: Option<f64>,
    #[serde(default)]
    pub panel: Option<f64>,
    #[serde(default)]
    pub total: Option<f64>,
    #[serde(default)]
    pub ok: Option<bool>,
    /// 잰 시각(epoch 초). 「얼마나 전 값인가」를 클라가 계산한다.
    #[serde(default)]
    pub at: Option<f64>,
}

/// `debug_stats` 회신의 `stats` — 서버 `serverdiag.collect_stats` 가 만든 그대로.
///
/// 칸이 늘어도 구 클라가 안 깨지게 **전부 `default`** 다. 구 서버(이 회신을 모르는
/// 서버)는 아무것도 안 보내고, 그때 판은 「서버가 아직 답하지 않았다」로 남는다.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
pub struct ServerStats {
    #[serde(default)]
    pub pid: u32,
    #[serde(default)]
    pub python: String,
    /// 기동 뒤 흐른 초.
    #[serde(default)]
    pub uptime_s: Option<f64>,
    /// 상주 메모리(바이트).
    #[serde(default)]
    pub rss: Option<u64>,
    /// 열린 fd(Unix). Windows 는 `None`.
    #[serde(default)]
    pub fds: Option<usize>,
    #[serde(default)]
    pub threads: Option<usize>,
    /// 산 asyncio 태스크 수 — pytmux-453 이 「회차마다 한 벌씩 남는다」로 잡은 그 값.
    #[serde(default)]
    pub tasks: Option<usize>,
    #[serde(default)]
    pub clients: usize,
    /// 클라마다 **마지막 메시지 뒤 흐른 초**(`last_seen` 기준 · 아직 없으면 `None`).
    #[serde(default)]
    pub client_idle_s: Vec<Option<f64>>,
    #[serde(default)]
    pub sessions: usize,
    #[serde(default)]
    pub windows: usize,
    #[serde(default)]
    pub panes: usize,
    /// 전 패널 스크롤백 행의 합 — 메모리가 「자라는」 자리의 첫 후보.
    #[serde(default)]
    pub scrollback_rows: Option<u64>,
    #[serde(default)]
    pub remote_links: usize,
    #[serde(default)]
    pub remote_reconnecting: usize,
    #[serde(default)]
    pub objects: Option<u64>,
    #[serde(default)]
    pub gc: Vec<GcGeneration>,
    /// 산 객체 상위 — `(타입 이름, 개수)`.
    #[serde(default)]
    pub top: Vec<(String, u64)>,
    #[serde(default)]
    pub usage_probe: Option<UsageProbeTiming>,
    /// `<sock>.error.log` 크기(바이트). 회전이 없는 파일이라 이 값이 크면 그것부터다.
    #[serde(default)]
    pub error_log_bytes: Option<u64>,
}

fn mb(n: Option<u64>) -> String {
    match n {
        Some(n) => format!("{:.1} MB", n as f64 / 1_048_576.0),
        None => "?".to_owned(),
    }
}

fn count(n: Option<usize>) -> String {
    n.map_or_else(|| "?".to_owned(), |v| v.to_string())
}

fn secs(v: Option<f64>) -> String {
    match v {
        Some(s) => format!("{s:.1}s"),
        None => "?".to_owned(),
    }
}

/// 초를 사람이 읽는 길이로 — `3d 4h` · `2h 05m` · `12m 30s`.
fn uptime(s: f64) -> String {
    let s = s.max(0.0) as u64;
    let (d, h, m, sec) = (s / 86_400, (s / 3_600) % 24, (s / 60) % 60, s % 60);
    if d > 0 {
        format!("{d}d {h}h")
    } else if h > 0 {
        format!("{h}h {m:02}m")
    } else {
        format!("{m}m {sec:02}s")
    }
}

impl ServerStats {
    /// 판에 실을 줄들 — `debug-stats` 판의 **서버 절반**. 클라 절반 아래에 붙는다.
    ///
    /// 값 옆에 **무엇을 보는 값인지**를 적는다(`base::diag` · 정본 `clientdiag.render`
    /// 와 같은 규율). ⛔ 긴 줄을 안 만든다 — 이 판은 읽는 판이라 줄을 안 접고, 잘린
    /// 문장은 없느니만 못하다(실기 컷 2026-09-03).
    pub fn lines(&self, now_epoch: Option<f64>) -> Vec<String> {
        let t = base::i18n::t;
        let mut out = vec![
            format!("― {} ―", t("서버 쪽")),
            format!(
                "  pid {} · python {} · {} {}",
                self.pid,
                if self.python.is_empty() { "?" } else { &self.python },
                t("기동 뒤"),
                self.uptime_s.map_or_else(|| "?".to_owned(), uptime)
            ),
            format!(
                "  {} {} · fd {} · {} {} · {} {}",
                t("상주 메모리"),
                mb(self.rss),
                count(self.fds),
                t("스레드"),
                count(self.threads),
                t("asyncio 태스크"),
                count(self.tasks)
            ),
        ];
        let idle: Vec<String> = self
            .client_idle_s
            .iter()
            .map(|v| v.map_or_else(|| "?".to_owned(), |s| format!("{s:.0}s")))
            .collect();
        out.push(format!(
            "  {} {}{} · {} {} · {} {}",
            t("클라"),
            self.clients,
            if idle.is_empty() { String::new() } else { format!(" ({} {})", t("마지막 수신"), idle.join(" · ")) },
            t("원격 링크"),
            self.remote_links,
            t("재연결 중"),
            self.remote_reconnecting
        ));
        out.push(format!(
            "  {} {} · {} {} · {} {} · {} {}",
            t("세션"),
            self.sessions,
            t("탭"),
            self.windows,
            t("패널"),
            self.panes,
            t("스크롤백 행"),
            self.scrollback_rows.map_or_else(|| "?".to_owned(), |v| v.to_string())
        ));
        if let Some(n) = self.objects {
            out.push(format!("  {} {}", t("산 객체"), n));
        }
        for g in &self.gc {
            out.push(format!(
                "    gen{}: {} {} · {} {} · {} {}",
                g.generation,
                t("수거"),
                g.collections,
                t("거둔 것"),
                g.collected,
                t("못 거둔 것"),
                g.uncollectable
            ));
        }
        for (name, n) in self.top.iter().take(6) {
            out.push(format!("    {n:>8}  {name}"));
        }
        match &self.usage_probe {
            Some(p) => {
                let ago = match (p.at, now_epoch) {
                    (Some(at), Some(now)) if now >= at => format!(" · {}", t_ago(now - at)),
                    _ => String::new(),
                };
                out.push(format!(
                    "  /usage {}: boot {} · panel {} · total {} · {}{}",
                    t("프로브 마지막 회차"),
                    secs(p.boot),
                    secs(p.panel),
                    secs(p.total),
                    match p.ok {
                        Some(true) => t("성공"),
                        Some(false) => t("실패"),
                        None => "?",
                    },
                    ago
                ));
            }
            None => out.push(format!("  /usage {}", t("프로브가 아직 한 번도 안 돌았다"))),
        }
        out.push(format!("  error.log {}", mb(self.error_log_bytes)));
        out
    }
}

fn t_ago(s: f64) -> String {
    format!("{} {}", uptime(s), base::i18n::t("전"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_reply_with_only_a_pid_still_makes_lines() {
        // 구 서버·낡은 칸 — 전부 default 라 아무 칸이 없어도 판은 뜬다.
        let stats: ServerStats = serde_json::from_str(r#"{"pid": 7}"#).unwrap();
        let lines = stats.lines(None);
        assert!(lines.iter().any(|l| l.contains("pid 7")), "{lines:?}");
        assert!(
            lines.iter().any(|l| l.contains("/usage")),
            "프로브 줄이 없다 — 382 의 첫 물음이 이 줄이다: {lines:?}"
        );
    }

    #[test]
    fn unknown_values_say_unknown_not_zero() {
        // ⛔ 못 잰 것을 0 으로 적으면 「fd 가 0 개」로 읽힌다(Windows).
        let stats = ServerStats { pid: 1, ..Default::default() };
        let joined = stats.lines(None).join("\n");
        assert!(joined.contains("fd ?"), "{joined}");
        assert!(!joined.contains("fd 0"), "{joined}");
    }

    #[test]
    fn the_probe_line_carries_its_timings_and_age() {
        let stats = ServerStats {
            pid: 1,
            usage_probe: Some(UsageProbeTiming {
                boot: Some(14.0),
                panel: Some(10.1),
                total: Some(37.0),
                ok: Some(true),
                at: Some(1_000.0),
            }),
            ..Default::default()
        };
        let joined = stats.lines(Some(1_130.0)).join("\n");
        assert!(joined.contains("boot 14.0s"), "{joined}");
        assert!(joined.contains("total 37.0s"), "{joined}");
        assert!(joined.contains("2m 10s"), "나이가 안 적혔다: {joined}");
    }

    #[test]
    fn uptime_reads_like_a_person_would_say_it() {
        assert_eq!(uptime(59.0), "0m 59s");
        assert_eq!(uptime(3_725.0), "1h 02m");
        assert_eq!(uptime(90_000.0), "1d 1h");
    }
}
