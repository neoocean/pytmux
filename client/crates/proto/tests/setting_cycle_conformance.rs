//! 적합성 — 설정 판 **숫자 한 걸음**이 정본 `SettingsScreen._cycle` 과 같은가 (pytmux-622).
//!
//! 픽스처는 정본 `_cycle` 을 **직접 불러** 뽑았다(`scripts/gen_setting_cycle.py`). 여기서는
//! 같은 줄 · 같은 시작값 · 같은 방향으로 [`base::config::setting_pick_dir`] 을 눌러 같은 값이
//! 나오는지 본다. 종전 GUI 는 끝에서 반대쪽 끝으로 **감았다** — 그 갈림이면 `hi` 에서 `→`
//! 한 걸음이 `lo` 로 떨어져 이 시험이 운다.
//!
//! 시작값이 `None`(정본이 지금 값을 모름)인 칸은 정본이 `lo` 에서 뗀다. 우리 쪽은 값을
//! 언제나 알므로 그 칸은 `lo` 에서 시작한 것으로 맞댄다.

use base::config::{SETTINGS, SettingKind, SettingPick, SettingValues, setting_pick_dir};

#[derive(serde::Deserialize)]
struct Case {
    key: String,
    lo: f32,
    hi: f32,
    step: f32,
    start: Option<f32>,
    forward: bool,
    got: f32,
}

#[derive(serde::Deserialize)]
struct Fx {
    cases: Vec<Case>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/setting_cycle.json"))
        .expect("setting_cycle.json 을 못 읽는다 — scripts/gen_setting_cycle.py 로 다시 뽑을 것")
}

/// 그 키의 숫자 칸에 `v` 를 넣은 값 묶음. 모르는 키면 `None`(이 시험이 그 줄을 못 잰다).
fn values_with(key: &str, v: f32) -> Option<SettingValues> {
    let mut values = SettingValues::default();
    match key {
        "inactive-dim-ratio" => values.inactive_dim_ratio = v,
        "mouse-drag-threshold" => values.mouse_drag_threshold = v as u16,
        "status-interval" => values.status_interval = v as u16,
        _ => return None,
    }
    Some(values)
}

#[test]
fn every_canon_number_step_lands_where_canon_lands() {
    let fx = fixture();
    let mut checked = 0;
    let mut wrong = Vec::new();
    for case in &fx.cases {
        let Some(row) = SETTINGS.iter().position(|s| s.key == case.key) else {
            // 우리 표에 없는 정본 숫자 줄 — 그 갈림은 패리티 표가 센다(여기서는 못 잰다).
            continue;
        };
        let SettingKind::Number { lo, hi, step } = SETTINGS[row].kind else {
            wrong.push(format!("{}: 정본은 숫자 줄인데 우리는 아니다", case.key));
            continue;
        };
        assert!(
            (lo - case.lo).abs() < 1e-4 && (hi - case.hi).abs() < 1e-4 && (step - case.step).abs() < 1e-4,
            "{}: 범위·걸음이 정본({}..{} / {})과 다르다({lo}..{hi} / {step})",
            case.key,
            case.lo,
            case.hi,
            case.step
        );
        let start = case.start.unwrap_or(case.lo);
        let values = values_with(&case.key, start)
            .unwrap_or_else(|| panic!("{}: 이 시험의 `values_with` 에 줄을 더할 것", case.key));
        checked += 1;
        match setting_pick_dir(row, &values, case.forward) {
            Some(SettingPick::SetNumber(key, got)) if key == case.key => {
                if (got - case.got).abs() > 1e-4 {
                    wrong.push(format!(
                        "{} {start} {}: 우리 {got} · 정본 {}",
                        case.key,
                        if case.forward { "→" } else { "←" },
                        case.got
                    ));
                }
            }
            other => wrong.push(format!("{}: SetNumber 가 아니다: {other:?}", case.key)),
        }
    }
    assert!(wrong.is_empty(), "정본과 다른 숫자 걸음:\n  {}", wrong.join("\n  "));
    assert!(checked >= 24, "잰 걸음이 {checked} 개뿐이다 — 통과가 아니라 고장이다");
}

/// 픽스처가 **끝 칸**을 실제로 담았나 — 끝에서 멈추는지를 재는 시험이 끝을 안 누르면 공허하다.
#[test]
fn the_fixture_pushes_past_both_ends() {
    let fx = fixture();
    let at_hi = fx.cases.iter().any(|c| c.start == Some(c.hi) && c.forward && (c.got - c.hi).abs() < 1e-4);
    let at_lo = fx.cases.iter().any(|c| c.start == Some(c.lo) && !c.forward && (c.got - c.lo).abs() < 1e-4);
    assert!(at_hi && at_lo, "픽스처에 양끝을 넘어 누른 칸이 없다");
}
