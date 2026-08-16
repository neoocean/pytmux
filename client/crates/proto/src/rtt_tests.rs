use std::collections::BTreeMap;

use super::*;

#[derive(serde::Deserialize)]
struct Fixture {
    now: f64,
    cases: BTreeMap<String, Case>,
}

#[derive(serde::Deserialize)]
struct Case {
    hist: Vec<(f64, f64)>,
    thr: f64,
    width: usize,
    height: usize,
    lines: Option<Vec<String>>,
    #[serde(default)]
    data: Option<GraphData>,
}

#[test]
fn the_graph_matches_the_python_canonical() {
    // ★ G9u — 답지는 파이썬 `_rtt_graph_lines` 가 쓴다(`gen_rtt_fixture.py`).
    //   자동 스케일·임계 점선·'측정 없음' 마커·은행가 반올림이 전부 이 비교에 걸린다.
    let fx: Fixture =
        serde_json::from_str(include_str!("../tests/fixtures/rtt_graph.json")).unwrap();
    assert!(fx.cases.len() >= 5, "픽스처가 얇다");
    for (name, case) in fx.cases {
        let mut hist = RttHist { threshold: case.thr, ..Default::default() };
        for (ts, rtt) in case.hist {
            hist.samples.push((ts, rtt));
        }
        let got_lines = hist.graph_lines(fx.now, case.width, case.height);
        assert_eq!(got_lines, case.lines, "파이썬과 다른 그림: {name}");
        let got_data = hist.graph_data(fx.now, case.width, case.height);
        if let Some(expected_data) = &case.data {
            let actual_data = got_data.expect("그래프 데이터가 없다");
            assert_eq!(
                actual_data.buckets, expected_data.buckets,
                "버킷이 다르다: {name}"
            );
            assert_eq!(actual_data.threshold, expected_data.threshold, "임계가 다르다: {name}");
            assert_eq!(actual_data.vmax, expected_data.vmax, "vmax 가 다르다: {name}");
            assert_eq!(actual_data.peak, expected_data.peak, "피크가 다르다: {name}");
            assert!(
                (actual_data.avg - expected_data.avg).abs() < 1e-9,
                "평균이 다르다: {name}"
            );
            assert_eq!(actual_data.count, expected_data.count, "카운트가 다르다: {name}");
            assert_eq!(
                actual_data.has_gaps, expected_data.has_gaps,
                "갭 플래그가 다르다: {name}"
            );
        }
    }
}

#[test]
fn the_hysteresis_needs_three_in_a_row() {
    // 표본 하나에 외곽선이 깜빡이면 순간 지터가 전부 빨간 화면이 된다(파이썬 3/3).
    let mut hist = RttHist::default();
    hist.sample(1.0, 0.5);
    hist.sample(1.5, 0.5);
    assert!(!hist.degraded, "2연속으로 켜졌다");
    hist.sample(2.0, 0.5);
    assert!(hist.degraded, "3연속인데 안 켜졌다");
    hist.sample(2.5, 0.01);
    hist.sample(3.0, 0.01);
    assert!(hist.degraded, "2연속으로 꺼졌다");
    hist.sample(3.5, 0.01);
    assert!(!hist.degraded, "3연속인데 안 꺼졌다");
    assert_eq!(hist.last, Some(0.01));
}

#[test]
fn old_samples_fall_out_of_the_window() {
    let mut hist = RttHist::default();
    hist.sample(0.0, 0.1);
    hist.sample(WINDOW + 10.0, 0.2); // 첫 표본은 이제 창 밖
    let lines = hist.graph_lines(WINDOW + 10.0, 48, 5).expect("표본이 있다");
    assert!(lines.iter().any(|l| l.contains("표본 1개")), "{lines:?}");
}
