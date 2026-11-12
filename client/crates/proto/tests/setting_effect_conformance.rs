//! 설정 **기본값** == 정본 — 설정 효과 자의 proto 절반(pytmux-568).
//!
//! 픽스처 `setting_effects.json` 의 `defaults` 는 빈 설정 파일로 지은 정본 클라의 `setting_current(key)`
//! 다(`scripts/gen_setting_effects.py` 가 정본을 직접 부른다). 여기서는 `base::Config::default()` 를 설정
//! 판이 보이는 낱말(`Setting::value`)로 바꿔 줄마다 맞댄다 — 종전에 `mouse-drag-threshold` 가 정본 3 ·
//! GUI 1 로 갈려 있었는데 그것을 재는 자가 없었다.
//!
//! 줄마다 «값을 바꾸면 달라지는 관측값» 은 gui 의 `session_view_setting_effect_tests.rs` 가 잰다(뷰가 있어야
//! 누를 수 있다). 서버가 주인인 줄(`null`)은 서버 회신 전 값이라 대상 밖이다 — 서버의 기본값은 두 클라가
//! 공유하는 서버의 일이다(이슈 «못 잰 것»).

use base::config::{SETTINGS, SettingValues};
use serde_json::Value;

fn fixture() -> Value {
    serde_json::from_str(include_str!("fixtures/setting_effects.json"))
        .expect("setting_effects.json 을 못 읽는다 — python3 scripts/gen_setting_effects.py")
}

/// 정본과 기본값이 갈리는 줄 — (키, 이슈, 사유). ⛔ 상한을 올리지 않는다. 맞으면 지우고 내린다.
///
/// `ambiguous-width` 는 pytmux-578 이 맞췄다(설정 판이 판정값 `narrow`/`wide` 를 보인다 — 정본 `cellwidth` 와 같다).
const DEFAULT_DIVERGENT: &[(&str, &str, &str)] = &[];
const DEFAULT_DIVERGENT_CEILING: usize = 0;

/// 환경이 정하는 줄 — 생성기는 `LANG=C` 로 뽑지만 시험 프로세스의 로케일은 그것과 무관하다.
const ENV_DEPENDENT: &[&str] = &["language"];

/// `Config::default()` → 설정 판 스냅샷. 설정 파일이 쥔 칸만(서버 칸은 기본 그대로).
///
/// ⚠ 뷰의 `setting_values` 와 같은 대응이다 — 그 함수가 칸을 잘못 옮기면 이 자가 아니라 gui 의 효과 자가 운다.
fn values_of(c: &base::Config) -> SettingValues {
    SettingValues {
        inactive_dim: c.inactive_dim,
        inactive_dim_ratio: c.inactive_dim_ratio,
        mouse: c.mouse,
        mouse_drag_copy: c.mouse_drag_copy,
        tab_bar_always: c.tab_bar_always,
        default_path: c.default_path.clone(),
        claude_command: c.claude_command.clone(),
        strip_box_drawing: c.strip_box_drawing,
        copy_unwrap: c.copy_unwrap,
        set_clipboard: c.set_clipboard,
        alt_scroll: c.alt_scroll,
        set_titles: c.set_titles,
        set_titles_string: c.set_titles_string.clone(),
        mode_keys: c.mode_keys.clone(),
        mouse_drag_threshold: c.mouse_drag_threshold,
        // 설정 판은 **판정값**을 보인다(뷰 `setting_values` · pytmux-578) — 측정 전 auto 는 narrow.
        ambiguous_width: if proto::compose::resolve_ambiguous(&c.ambiguous_width, false) { "wide" } else { "narrow" }
            .to_owned(),
        status_left: c.status_left.clone(),
        status_right: c.status_right.clone(),
        status_bg: c.status_bg.clone(),
        status_fg: c.status_fg.clone(),
        status_position: c.status_position.clone(),
        remote_title: c.remote_title.clone(),
        status_interval: c.status_interval,
        prefix: c.prefix,
        ..SettingValues::default()
    }
}

/// 정본 낱말과 우리 낱말이 같은가. 빈 색(`status-bg`·`status-fg`)은 정본이 `""`, 우리 판이 «테마» 낱말로
/// 보인다 — 둘 다 «안 정했다» 라 설정 칸이 비었는지로 잰다.
fn same(key: &str, canon: &str, ours: &str, config: &base::Config) -> bool {
    match key {
        "status-bg" => canon.is_empty() == config.status_bg.is_empty() && (canon.is_empty() || canon == ours),
        "status-fg" => canon.is_empty() == config.status_fg.is_empty() && (canon.is_empty() || canon == ours),
        _ => canon == ours,
    }
}

#[test]
fn every_canon_setting_default_matches_ours() {
    let fx = fixture();
    let config = base::Config::default();
    let values = values_of(&config);
    let mut wrong = Vec::new();
    let mut stale = Vec::new();
    let mut measured = 0;
    for row in fx["rows"].as_array().unwrap() {
        let key = row["key"].as_str().unwrap();
        let canon = &fx["defaults"][key];
        let Some(canon) = canon.as_str() else {
            continue; // 서버가 주인이거나 링크
        };
        if row["backend"] != "config" || ENV_DEPENDENT.contains(&key) {
            continue;
        }
        let Some(setting) = SETTINGS.iter().find(|s| s.key == key) else {
            wrong.push(format!("{key}: 우리 설정 판에 줄이 없다"));
            continue;
        };
        measured += 1;
        let ours = setting.value(&values);
        let declared = DEFAULT_DIVERGENT.iter().find(|(k, _, _)| *k == key);
        match (same(key, canon, &ours, &config), declared) {
            (true, Some((_, issue, _))) => stale.push(format!("{key}({issue}) — 이제 맞는다. 지우고 상한을 내릴 것")),
            (false, None) => wrong.push(format!("{key}: 정본 {canon:?} · 우리 {ours:?}")),
            _ => {}
        }
    }
    assert!(measured >= 20, "잰 줄이 {measured} 개뿐이다 — 픽스처를 못 읽었다");
    assert!(wrong.is_empty(), "설정 기본값이 정본과 갈린다:\n  {}", wrong.join("\n  "));
    assert!(stale.is_empty(), "낡은 예외:\n  {}", stale.join("\n  "));
    assert!(DEFAULT_DIVERGENT.len() <= DEFAULT_DIVERGENT_CEILING, "상한을 넘었다(올리지 않는다)");
    assert_eq!(DEFAULT_DIVERGENT.len(), DEFAULT_DIVERGENT_CEILING, "줄었다 — 상한을 내릴 것");
    for (_, issue, why) in DEFAULT_DIVERGENT {
        assert!(issue.starts_with("pytmux-") && !why.is_empty());
    }
}

#[test]
fn the_canon_population_is_every_settings_row_we_know() {
    // 정본 38줄이 우리 설정 판에 있거나(같은 키) 선언돼 있다 — 감춘 `monitor-bell`(pytmux-642).
    let fx = fixture();
    let hidden: Vec<&str> = base::keymap::HIDDEN_SURFACES.to_vec();
    let rows = fx["rows"].as_array().unwrap();
    assert!(rows.len() >= 38, "정본 SETTINGS 가 {} 줄 — 생성기를 의심할 것", rows.len());
    let missing: Vec<&str> = rows
        .iter()
        .map(|r| r["key"].as_str().unwrap())
        .filter(|k| !SETTINGS.iter().any(|s| s.key == *k) && !hidden.contains(k))
        .collect();
    assert!(missing.is_empty(), "정본 설정 줄이 우리 판에 없고 선언도 없다: {missing:?}");
}
