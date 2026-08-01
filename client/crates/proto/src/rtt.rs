//! RTT 측정과 60분 그래프 — 패리티 G9u(파이썬 `clientconn` 동형).
//!
//! 클라가 0.5초마다 `{"t":"ping","ts":<monotonic>}` 를 보내면 서버가 `ts` 를 그대로
//! echo 한 `pong` 을 돌려준다(서버는 처음부터 이걸 지원했다 — 서버 변경 불요).
//! 왕복 시간이 표본이고, 여기서 이력·히스테리시스·그래프 줄을 만든다.
//!
//! # 왜 proto 인가
//!
//! 그래프 줄을 만드는 것은 판정이고(버킷·자동 스케일·임계 점선·측정 없음 마커),
//! 뷰마다 적으면 GUI 와 TUI 가 다른 그림을 그린다. 재료(pong)는 서버가 준 것이라
//! core 가 아니라 proto 다(`info.rs` 와 같은 자리).
//!
//! 알고리즘은 파이썬 `_rtt_graph_lines` 를 옮긴 것이고, 정본이 답지를 쓴다 —
//! `scripts/gen_rtt_fixture.py` → `tests/fixtures/rtt_graph.json` 적합성 테스트.

use base::i18n::{t, tf};

/// 이력 보존·그래프 창(초) — 최근 60분.
pub const WINDOW: f64 = 3600.0;
/// 그래프 가로 칸(시간 버킷 수).
pub const GRAPH_W: usize = 48;
/// 그래프 세로 행(각 행 = 1/8 정밀 세로 막대).
pub const GRAPH_H: usize = 5;
/// ping 주기(초) — 파이썬 `net_ping_interval` 기본값.
pub const PING_INTERVAL: f64 = 0.5;

/// 세로 막대 블록(아래→위로 차오름). 인덱스 = 1/8 칸 수.
const VBLOCKS: [char; 9] = [' ', '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];

/// 파이썬 `round`(은행가 반올림 — .5 는 짝수로). Rust `round` 는 .5 를 0 에서 먼
/// 쪽으로 보내므로 그대로 쓰면 경계 표본에서 그래프가 파이썬과 한 칸 어긋난다.
fn pyround(x: f64) -> i64 {
    let floor = x.floor();
    let diff = x - floor;
    if diff > 0.5 {
        floor as i64 + 1
    } else if diff < 0.5 {
        floor as i64
    } else {
        let f = floor as i64;
        if f % 2 == 0 { f } else { f + 1 }
    }
}

/// RTT 표본 이력 + 응답성 히스테리시스(파이썬 `_net_sample` 동형).
#[derive(Debug, Clone)]
pub struct RttHist {
    /// (monotonic 초, 왕복 초) — 오래된 것이 앞.
    samples: Vec<(f64, f64)>,
    /// 저하 판정 임계(초) — 파이썬 `net_rtt_threshold` 기본 0.4.
    pub threshold: f64,
    /// 마지막 표본(초).
    pub last: Option<f64>,
    bad: u32,
    good: u32,
    /// 응답성 저하(빨간 외곽선) — 임계 초과 3연속이면 ON, 이하 3연속이면 OFF.
    pub degraded: bool,
}

impl Default for RttHist {
    fn default() -> Self {
        Self { samples: Vec::new(), threshold: 0.4, last: None, bad: 0, good: 0, degraded: false }
    }
}

impl RttHist {
    /// 표본 하나를 넣고 창 밖을 잘라낸다.
    pub fn sample(&mut self, now: f64, rtt: f64) {
        self.last = Some(rtt);
        self.samples.push((now, rtt));
        let cutoff = now - WINDOW;
        let drop = self.samples.iter().take_while(|(ts, _)| *ts < cutoff).count();
        if drop > 0 {
            self.samples.drain(..drop);
        }
        // 히스테리시스(3/3) — 표본 하나에 외곽선이 깜빡이지 않게 한다.
        if rtt > self.threshold {
            self.bad += 1;
            self.good = 0;
        } else {
            self.good += 1;
            self.bad = 0;
        }
        if self.bad >= 3 {
            self.degraded = true;
        } else if self.good >= 3 {
            self.degraded = false;
        }
    }

    pub fn is_empty(&self) -> bool {
        self.samples.is_empty()
    }

    /// 그래프 줄들(제목·막대·축·통계·범례). 창 안 표본이 없으면 `None` — 호출부가
    /// 그래프를 통째로 생략한다(파이썬과 같은 계약).
    pub fn graph_lines(&self, now: f64, width: usize, height: usize) -> Option<Vec<String>> {
        if self.samples.is_empty() || width == 0 || height == 0 {
            return None;
        }
        let span = WINDOW;
        let mut buckets: Vec<Option<f64>> = vec![None; width];
        let mut raw: Vec<f64> = Vec::new();
        for &(ts, rtt) in &self.samples {
            let age = now - ts;
            if !(0.0..=span).contains(&age) {
                continue;
            }
            raw.push(rtt);
            // 파이썬 `int()` 는 0 쪽 절단 — age≥0 이라 floor 와 같다.
            let col_back = (age / span * width as f64) as usize;
            let col = (width - 1).saturating_sub(col_back.min(width - 1));
            buckets[col] = Some(buckets[col].map_or(rtt, |cur: f64| cur.max(rtt)));
        }
        if raw.is_empty() {
            return None;
        }
        let thr = self.threshold;
        let peak = raw.iter().copied().fold(f64::MIN, f64::max);
        let vmax = if peak > 0.0 {
            peak
        } else if thr > 0.0 {
            thr
        } else {
            1e-9
        };
        let total8 = (height * 8) as i64;
        // 칸별 1/8 채움. 측정값은 최소 1 로 띄워 '0 에 가까움'과 '측정 없음'을 가른다.
        let eighths: Vec<Option<i64>> = buckets
            .iter()
            .map(|b| b.map(|v| pyround(v / vmax * total8 as f64).clamp(1, total8)))
            .collect();
        let thr_e = pyround(thr / vmax * total8 as f64);
        let thr_row = if 0 < thr_e && thr_e <= total8 {
            Some(height as i64 - 1 - (thr_e - 1) / 8)
        } else {
            None
        };
        let mut out = vec![t("RTT 그래프 (최근 60분):").to_owned()];
        let vmax_ms = pyround(vmax * 1000.0);
        let thr_ms = pyround(thr * 1000.0);
        for r in 0..height as i64 {
            let base = (height as i64 - 1 - r) * 8;
            let on_thr = thr_row == Some(r);
            let mut cells = String::new();
            for e in &eighths {
                match e {
                    None => cells.push(if on_thr {
                        '┄'
                    } else if r == height as i64 - 1 {
                        '·'
                    } else {
                        ' '
                    }),
                    Some(e) => {
                        let blk = e - base;
                        if blk > 0 {
                            cells.push(VBLOCKS[blk.min(8) as usize]);
                        } else {
                            cells.push(if on_thr { '┄' } else { ' ' });
                        }
                    }
                }
            }
            let axis = if r == 0 {
                format!("{vmax_ms:>4} ┤")
            } else if on_thr {
                format!("{thr_ms:>4} ┄")
            } else {
                "     ┤".to_owned()
            };
            out.push(format!("{axis}{cells}"));
        }
        out.push(format!("   0 ┴{}", "─".repeat(width)));
        let left = t("-60분");
        let right = t("지금");
        let lw = crate::compose::display_width(left);
        let rw = crate::compose::display_width(right);
        let pad = (width as i64 - lw as i64 - rw as i64).max(1) as usize;
        out.push(format!("      {left}{}{right}", " ".repeat(pad)));
        let peak_ms = pyround(peak * 1000.0);
        let avg_ms = pyround(raw.iter().sum::<f64>() / raw.len() as f64 * 1000.0);
        out.push(tf(
            "peak {peak} ms · 평균 {avg} ms · 표본 {n}개",
            &[
                ("peak", peak_ms.to_string().as_str()),
                ("avg", avg_ms.to_string().as_str()),
                ("n", raw.len().to_string().as_str()),
            ],
        ));
        if eighths.iter().any(Option::is_none) {
            out.push(t("· 측정 없음(클라 미가동/끊김 구간)").to_owned());
        }
        Some(out)
    }
}

/// ping 발신기 — 뷰의 프레임 틱에서 부르면 0.5초에 한 번만 프레임을 만든다.
///
/// 두 뷰가 같은 규칙(주기·ts 시계)을 쓰라고 여기 있다. `ts` 는 이 발신기의
/// monotonic 시계라, pong 의 echo 를 [`Pinger::now`] 에서 빼면 그대로 왕복이다.
#[derive(Debug)]
pub struct Pinger {
    epoch: std::time::Instant,
    last_sent: Option<f64>,
}

impl Default for Pinger {
    fn default() -> Self {
        Self { epoch: std::time::Instant::now(), last_sent: None }
    }
}

impl Pinger {
    pub fn now(&self) -> f64 {
        self.epoch.elapsed().as_secs_f64()
    }

    /// 주기가 찼으면 보낼 ping 프레임 하나.
    ///
    /// 첫 호출은 **시계만 세우고 안 보낸다** — 파이썬 `set_interval` 도 첫 발화가
    /// 주기 뒤다. (즉시 쏘면 정확 목록을 재는 큐 오라클마다 ping 이 낀다.)
    pub fn tick(&mut self) -> Option<crate::command::Outgoing> {
        let now = self.now();
        match self.last_sent {
            None => {
                self.last_sent = Some(now);
                None
            }
            Some(last) if now - last < PING_INTERVAL => None,
            _ => {
                self.last_sent = Some(now);
                Some(crate::command::Outgoing::Ping { ts: now })
            }
        }
    }
}

#[cfg(test)]
#[path = "rtt_tests.rs"]
mod tests;
