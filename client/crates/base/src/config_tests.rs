//! 설정 읽기 오라클(패리티 G5).
//!
//! 이 파일이 지키는 것은 **파이썬 클라와 같은 파일을 같은 뜻으로 읽는다**는 것이다.
//! 어긋나면 같은 상자에서 두 클라가 다르게 돌고, 사용자는 어느 쪽이 맞는지 알 수 없다.

use super::*;

#[test]
fn no_file_means_the_tmux_default() {
    // 설정이 없다고 실패하지 않는다 — 대부분의 사용자는 파일이 아예 없다.
    assert_eq!(Config::default().prefix, (Key::Char('b'), Mods::CTRL));
}

#[test]
fn the_prefix_can_be_changed_in_tmux_notation() {
    let config = Config::parse("set prefix C-a\n");
    assert_eq!(config.prefix, (Key::Char('a'), Mods::CTRL));
}

#[test]
fn the_same_key_can_be_written_several_ways() {
    // 파이썬 클라가 받는 표기를 그대로 받는다 — 한쪽만 읽히면 사용자는 "설정했는데 안
    // 먹는다"를 만난다.
    for text in ["set prefix C-a", "set prefix ctrl-a", "set prefix ctrl+a"] {
        assert_eq!(
            Config::parse(text).prefix,
            (Key::Char('a'), Mods::CTRL),
            "{text}"
        );
    }
}

#[test]
fn alt_works_too_and_case_does_not_matter() {
    assert_eq!(Config::parse("set prefix M-x").prefix, (Key::Char('x'), Mods::ALT));
    assert_eq!(Config::parse("set prefix C-A").prefix, (Key::Char('a'), Mods::CTRL));
}

#[test]
fn a_prefix_without_a_modifier_is_refused() {
    // ★ 수정키 없는 글자를 prefix 로 받으면 **그 글자를 영영 못 친다**. 파이썬 클라는
    // 그런 설정도 받지만, 그건 그쪽의 사정이고 여기서는 기본값을 지킨다.
    assert_eq!(Config::parse("set prefix a").prefix, Config::default().prefix);
}

#[test]
fn unknown_lines_are_skipped_not_fatal() {
    // 파이썬 클라가 쓰는 지시어를 우리가 모른다고 그 파일을 못 읽는 것이 되면 안 된다.
    let config = Config::parse(
        "# 주석\n\
         set mouse on\n\
         bind r source-file ~/.pytmux.conf\n\
         set inactive-dim-ratio 0.3\n\
         set prefix C-q\n\
         set status-bg blue\n",
    );
    assert_eq!(config.prefix, (Key::Char('q'), Mods::CTRL));
}

#[test]
fn a_broken_prefix_line_leaves_the_default() {
    for text in ["set prefix", "set prefix C-", "set prefix ctrl+escape", "set"] {
        assert_eq!(
            Config::parse(text).prefix,
            Config::default().prefix,
            "{text} 가 기본값을 망가뜨렸다"
        );
    }
}

#[test]
fn the_last_setting_wins() {
    // 같은 옵션이 두 번 적히면 나중 것이다(파이썬도 순서대로 덮어쓴다).
    assert_eq!(
        Config::parse("set prefix C-a\nset prefix C-t\n").prefix,
        (Key::Char('t'), Mods::CTRL)
    );
}

// ── 설정 화면의 목록(패리티 G5b) ───────────────────────────────────────────────

#[test]
fn every_setting_row_shows_a_value() {
    // 값이 빈 줄은 "못 읽는 설정"으로 보인다 — 목록에 올린 이상 지금 값을 알아야 한다.
    let values = SettingValues {
        inactive_dim: true,
        border_status: false,
        single_border: false,
        coalesce_repaints: false,
        nest_auto_attach: false,
        win_mouse_motion: false,
        mouse: true,
        mouse_drag_copy: true,
        tab_bar_always: true,
        default_path: "current".into(),
        strip_box_drawing: true,
        inactive_dim_ratio: 0.18,
        mode_keys: "vi".into(),
        mouse_drag_threshold: 1,
        ambiguous_width: "auto".into(),
        vt_parser: "pyte".into(),
        window_size: "latest".into(),
        sync: true,
        monitor_activity: false,
        monitor_bell: true,
        auto_rename: false,
        prefix: (Key::Char('a'), Mods::CTRL),
        ..Default::default()
    };
    for setting in SETTINGS {
        assert!(
            !setting.value(&values).is_empty(),
            "{} 의 값이 비어 있다",
            setting.key
        );
    }
}

#[test]
fn the_values_are_the_ones_we_were_given() {
    let values = SettingValues {
        inactive_dim: true,
        border_status: false,
        single_border: false,
        coalesce_repaints: false,
        nest_auto_attach: false,
        win_mouse_motion: false,
        mouse: true,
        mouse_drag_copy: true,
        tab_bar_always: true,
        default_path: "current".into(),
        strip_box_drawing: true,
        inactive_dim_ratio: 0.18,
        mode_keys: "vi".into(),
        mouse_drag_threshold: 1,
        ambiguous_width: "auto".into(),
        vt_parser: "pyte".into(),
        window_size: "latest".into(),
        sync: true,
        monitor_activity: false,
        monitor_bell: true,
        auto_rename: false,
        prefix: (Key::Char('a'), Mods::CTRL),
        ..Default::default()
    };
    let value_of = |key: &str| {
        SETTINGS
            .iter()
            .find(|s| s.key == key)
            .expect(key)
            .value(&values)
    };
    assert_eq!(value_of("synchronize-panes"), "on");
    assert_eq!(value_of("monitor-activity"), "off");
    // ⚠ `monitor-bell` 은 **설정 표에서 뺐다**(§10-21ⓜ — 화면에서 감춘다). 값은 여전히
    //   서버가 들고 우리도 나른다(`values.monitor_bell`) — 없어진 것은 **입구뿐**이다.
    assert_eq!(value_of("automatic-rename"), "off");
    assert_eq!(value_of("prefix"), "C-a");
}

#[test]
fn picking_a_toggle_row_gives_an_action_and_a_text_row_gives_a_question() {
    let values = SettingValues {
        inactive_dim: true,
        border_status: false,
        single_border: false,
        coalesce_repaints: false,
        nest_auto_attach: false,
        win_mouse_motion: false,
        mouse: true,
        mouse_drag_copy: true,
        tab_bar_always: true,
        default_path: "current".into(),
        strip_box_drawing: true,
        inactive_dim_ratio: 0.18,
        mode_keys: "vi".into(),
        mouse_drag_threshold: 1,
        ambiguous_width: "auto".into(),
        vt_parser: "pyte".into(),
        window_size: "latest".into(),
        sync: false,
        monitor_activity: false,
        monitor_bell: false,
        auto_rename: false,
        prefix: (Key::Char('b'), Mods::CTRL),
        ..Default::default()
    };
    // prefix 줄은 물음이고, 지금 값이 미리 채워진다. **자리를 박지 않는다** — 표가
    // 카테고리 순으로 늘어서면서 위치가 바뀐다(G8i 에서 실제로 그랬다).
    let prefix_row = SETTINGS.iter().position(|s| s.key == "prefix").expect("prefix 줄");
    match setting_pick(prefix_row, &values) {
        Some(SettingPick::Ask(Prompt::SetPrefix, seed)) => assert_eq!(seed, "C-b"),
        other => panic!("{other:?}"),
    }
    // 모든 줄은 다섯 중 하나다: 액션(서버가 주인) · 설정 파일 뒤집기 · 값으로 놓기 ·
    // 숫자 · 물음. **없는 것**만 없으면 된다 — 어떤 줄이 어떤 종류인지는 SETTINGS 가
    // 정한다.
    for row in 0..SETTINGS.len() {
        assert!(
            matches!(
                setting_pick(row, &values),
                Some(
                    SettingPick::Act(_)
                        | SettingPick::Flip(_)
                        | SettingPick::Set(..)
                        | SettingPick::SetNumber(..)
                        | SettingPick::Ask(..)
                )
            ),
            "{row} 번째 줄"
        );
    }
    assert!(setting_pick(SETTINGS.len(), &values).is_none(), "목록 밖");
}

// ── 낱말을 직접 찍기(§10-21ⓣ — 설정 판 마우스) ────────────────────────────────

#[test]
fn picking_a_word_lands_on_that_value_not_the_next_one() {
    // ★ 이것이 방향판과 갈리는 이유다: 선택지가 셋 이상일 때 화살표는 한 칸씩 도는데,
    //   마우스는 목표를 바로 가리킬 수 있다. 찍은 낱말이 곧 값이라야 그 뜻이 산다.
    let values = base_values();
    let row = SETTINGS.iter().position(|s| matches!(s.kind, SettingKind::ConfigEnum(_)))
        .expect("고르는 줄이 하나는 있다");
    let SettingKind::ConfigEnum(choices) = SETTINGS[row].kind else { unreachable!() };
    assert!(choices.len() >= 2, "선택지가 하나면 이 테스트가 아무것도 안 잰다");
    let now = SETTINGS[row].value(&values);
    for (i, want) in choices.iter().enumerate() {
        match setting_pick_at(row, &values, i) {
            // 지금 값을 다시 찍으면 아무 일도 안 한다(누른 것이 꺼지면 놀란다).
            None => assert_eq!(*want, now, "{want} 를 찍었는데 아무 일도 안 했다"),
            Some(SettingPick::Set(key, value)) => {
                assert_eq!(key, SETTINGS[row].key);
                assert_eq!(value, *want, "{i} 번째를 찍었는데 다른 값이 됐다");
            }
            other => panic!("{other:?}"),
        }
    }
}

#[test]
fn picking_the_value_a_toggle_already_has_does_nothing() {
    // 서버 토글은 **뒤집기**뿐이다 — 이미 그 값인데 부르면 반대로 간다(화면에 켜진
    // 것을 눌렀는데 꺼지는 그림이다).
    let mut values = base_values();
    values.inactive_dim = true;
    let row = SETTINGS.iter().position(|s| s.key == "inactive-dim").expect("줄");
    // `choices()` 는 ["on", "off"] 순이다.
    assert!(setting_pick_at(row, &values, 0).is_none(), "켜진 것을 다시 켰다");
    assert!(matches!(setting_pick_at(row, &values, 1), Some(SettingPick::Flip("inactive-dim"))));
}

#[test]
fn a_row_without_choices_cannot_be_picked_by_slot() {
    // 숫자·물음·링크에는 찍을 낱말이 없다 — 없는 자리를 만들어 내면 거짓말이다.
    let values = base_values();
    for key in ["inactive-dim-ratio", "prefix", "list-keys"] {
        let row = SETTINGS.iter().position(|s| s.key == key).expect(key);
        assert!(setting_pick_at(row, &values, 0).is_none(), "{key}");
    }
    // 목록 밖·자리 밖도 마찬가지다.
    assert!(setting_pick_at(SETTINGS.len(), &values, 0).is_none());
    let row = SETTINGS.iter().position(|s| s.key == "inactive-dim").expect("줄");
    assert!(setting_pick_at(row, &values, 99).is_none(), "없는 자리를 골랐다");
}

#[test]
fn the_notation_we_write_is_the_notation_we_read() {
    // ★ 우리가 쓴 줄을 **파이썬 클라가 읽어야 한다**. 한 바퀴 돌려 확인한다.
    for text in ["C-a", "C-b", "M-x"] {
        let parsed = parse_key(text).expect(text);
        assert_eq!(key_to_tmux(parsed), text);
        assert_eq!(Config::parse(&format!("set prefix {}", key_to_tmux(parsed))).prefix, parsed);
    }
}

#[test]
fn an_unreadable_prefix_answer_changes_nothing() {
    // ⚠ 여기에 **읽히는** 표기를 넣지 말 것 — `set_prefix` 는 성공하면 진짜 설정 파일을
    //   고친다(테스트가 개발자의 config 를 덮는다). 쓰기 규칙은 픽스처 대조가 맡는다
    //   (`proto/tests/config_write_conformance.rs`).
    // 못 읽는 표기를 파일에 적으면 다음 기동에 로더가 그 줄을 버려 기본값으로 돌아가는데,
    // 파일에는 사용자가 적은 줄이 남아 "설정했는데 안 먹는다"의 가장 나쁜 형태가 된다.
    for text in ["", "a", "C-", "ctrl+escape"] {
        assert!(set_prefix(text).is_none(), "{text:?} 가 통과했다");
    }
}

// ── 비활성 패널 딤(패리티 G6b) ────────────────────────────────────────────────

#[test]
fn inactive_dim_defaults_to_on() {
    // 파이썬과 같은 기본값이다 — 외곽선 없이도 어느 패널이 내 키를 받는지 알아야 한다.
    let config = Config::default();
    assert!(config.inactive_dim);
    assert!((config.inactive_dim_ratio - 0.18).abs() < 1e-6);
}

#[test]
fn the_dim_can_be_turned_off_and_tuned() {
    let config = Config::parse("set inactive-dim off
set inactive-dim-ratio 0.4
");
    assert!(!config.inactive_dim);
    assert!((config.inactive_dim_ratio - 0.4).abs() < 1e-6);
}

#[test]
fn the_ratio_is_clamped_not_rejected() {
    // 범위를 벗어난 값 때문에 설정 파일을 통째로 못 읽는 것이 되면 안 된다(파이썬과 같다).
    assert!((Config::parse("set inactive-dim-ratio 5").inactive_dim_ratio - 0.8).abs() < 1e-6);
    assert!((Config::parse("set inactive-dim-ratio -1").inactive_dim_ratio).abs() < 1e-6);
    // 숫자가 아니면 기본값 그대로.
    assert!(
        (Config::parse("set inactive-dim-ratio 짙게").inactive_dim_ratio - 0.18).abs() < 1e-6
    );
}

// ── 글자 크기 배율(§10-21ⓐ) ──────────────────────────────────────────────────
//
// 규칙의 주인이 core 인 이유는 뷰가 둘이라서가 아니라(뷰는 하나다) **입구가 둘**이기
// 때문이다: 키(`Ctrl+=`)와 설정 화면. 두 자리가 각자 더하면 끝값·반올림이 갈리고, 그
// 어긋남은 설정 파일에 굳는다.

#[test]
fn the_scale_moves_one_step_and_stays_round() {
    assert!((font_scale_step(1.0, true) - 1.1).abs() < 1e-6);
    assert!((font_scale_step(1.0, false) - 0.9).abs() < 1e-6);
    // ★ **정확 비교**다(오차 허용 아님). 0.1 씩 f32 로 더하면 열 걸음 뒤가
    //   `2.0000002` 라 `1e-6` 허용으로는 접기를 빼도 통과한다 — 실제로 그 변이가
    //   살아남는 것을 보고 이 단언을 조였다. 배율은 `==` 로 견주는 자리가 있으므로
    //   (끝에 닿았나) 격자 위에 정확히 앉아야 한다.
    let mut scale = 1.0;
    for _ in 0..10 {
        scale = font_scale_step(scale, true);
    }
    assert_eq!(scale, 2.0, "열 걸음 뒤가 정확히 2.0 이 아니다 — 접기가 빠졌다");
}

#[test]
fn the_scale_stops_at_the_ends_instead_of_wrapping() {
    // ★ 돌면 "한 번 더 키웠는데 갑자기 작아진다"가 된다. `Number` 설정 줄은 키가
    //   하나뿐이라 한 바퀴 도는데, 이쪽은 키가 둘이라 돌 이유가 없다.
    assert!((font_scale_step(FONT_SCALE_HI, true) - FONT_SCALE_HI).abs() < 1e-6);
    assert!((font_scale_step(FONT_SCALE_LO, false) - FONT_SCALE_LO).abs() < 1e-6);
}

#[test]
fn the_scale_survives_the_config_file() {
    // 파일 왕복 — 값이 우리가 적는 그대로 다시 읽히나(설정 축 측정과 같은 방법).
    let text = edit_option("", "font-scale", &number_text("font-scale", 1.4));
    assert!((Config::parse(&text).font_scale - 1.4).abs() < 1e-6, "{text:?}");
    // 범위 밖은 **자른다** — 손으로 적은 한 줄 때문에 창이 못 쓰게 되면 안 된다.
    assert!((Config::parse("set font-scale 99").font_scale - FONT_SCALE_HI).abs() < 1e-6);
    assert!((Config::parse("set font-scale 0").font_scale - FONT_SCALE_LO).abs() < 1e-6);
    // 안 적으면 1.0 — 이 설정을 모르는 사람의 화면이 종전과 같아야 한다.
    assert!((Config::parse("set mouse on").font_scale - 1.0).abs() < 1e-6);
}

#[test]
fn the_underscore_spelling_works_too() {
    // 파이썬이 두 철자를 다 받는다 — 한쪽만 읽으면 "설정했는데 안 먹는다"가 된다.
    assert!(!Config::parse("set inactive_dim off").inactive_dim);
}

// ── 값이 셋 중 하나인 설정(패리티 G8e) ────────────────────────────────────────

#[test]
fn an_enum_row_cycles_to_the_next_value() {
    use crate::EnumOpt;
    assert_eq!(EnumOpt::VtParser.next("pyte"), "native");
    assert_eq!(EnumOpt::VtParser.next("native"), "pyte", "끝이면 처음으로");
    assert_eq!(EnumOpt::WindowSize.next("smallest"), "latest");
    assert_eq!(EnumOpt::WindowSize.next("largest"), "smallest");
}

#[test]
fn an_unknown_current_value_starts_from_the_beginning() {
    // 서버가 아직 안 알려 줬거나(빈 문자열) 우리가 모르는 값이면 첫 번째로 간다 —
    // 아무 일도 안 하는 것보다 낫다(누른 사람은 뭔가 바뀌기를 기대한다).
    use crate::EnumOpt;
    assert_eq!(EnumOpt::VtParser.next(""), "pyte");
    assert_eq!(EnumOpt::WindowSize.next("뭐지"), "smallest");
}

#[test]
fn a_value_we_have_not_heard_shows_a_question_mark() {
    // '모르는 것을 안다고 하면' 사용자가 그 값을 믿고 판단한다.
    let mut values = SettingValues {
        inactive_dim: true,
        border_status: false,
        single_border: false,
        coalesce_repaints: false,
        nest_auto_attach: false,
        win_mouse_motion: false,
        mouse: true,
        mouse_drag_copy: true,
        tab_bar_always: true,
        default_path: "current".into(),
        strip_box_drawing: true,
        inactive_dim_ratio: 0.18,
        mode_keys: "vi".into(),
        mouse_drag_threshold: 1,
        ambiguous_width: "auto".into(),
        vt_parser: String::new(),
        window_size: "latest".into(),
        sync: false,
        monitor_activity: false,
        monitor_bell: false,
        auto_rename: false,
        prefix: (Key::Char('b'), Mods::CTRL),
        ..Default::default()
    };
    let value_of = |key: &str, v: &SettingValues| {
        SETTINGS.iter().find(|s| s.key == key).expect(key).value(v)
    };
    assert_eq!(value_of("vt-parser", &values), "?");
    values.vt_parser = "native".into();
    assert_eq!(value_of("vt-parser", &values), "native");
}

// ── 마우스·탭바·기본 경로(패리티 G8g) ────────────────────────────────────────

#[test]
fn the_new_settings_have_python_defaults() {
    let c = Config::default();
    assert!(c.mouse && c.mouse_drag_copy && c.tab_bar_always);
    assert_eq!(c.default_path, "current");
}

#[test]
fn the_config_file_can_turn_the_mouse_off() {
    let c = Config::parse("set mouse off
set mouse-drag-copy off
");
    assert!(!c.mouse && !c.mouse_drag_copy);
}

#[test]
fn a_bad_tab_bar_value_keeps_the_default() {
    // ★ 오타 하나 때문에 탭바가 사라지면 무엇이 잘못됐는지 알 수 없다.
    assert!(Config::parse("set tab-bar 아무거나").tab_bar_always);
    assert!(!Config::parse("set tab-bar auto").tab_bar_always);
    assert!(Config::parse("set tab-bar always").tab_bar_always);
}

#[test]
fn the_default_path_is_taken_verbatim() {
    // 서버가 해석하는 값이라 우리가 손대지 않는다(`current`/`home`/절대경로).
    assert_eq!(Config::parse("set default-path home").default_path, "home");
}

// ── 숫자·링크 줄 · 박스드로잉(패리티 G8h) ─────────────────────────────────────

#[test]
fn a_number_row_wraps_at_the_top() {
    // 올리기만 하고 한 바퀴 돈다 — 좌우 키를 쓰면 이 화면의 다른 줄(패널 이동)과 겹친다.
    let mut values = base_values();
    let row = SETTINGS.iter().position(|s| s.key == "inactive-dim-ratio").expect("줄");
    values.inactive_dim_ratio = 0.18;
    match setting_pick(row, &values) {
        Some(SettingPick::SetNumber("inactive-dim-ratio", v)) => {
            assert!((v - 0.20).abs() < 1e-5, "{v}")
        }
        other => panic!("{other:?}"),
    }
    values.inactive_dim_ratio = 0.80;
    match setting_pick(row, &values) {
        Some(SettingPick::SetNumber(_, v)) => assert!(v.abs() < 1e-5, "끝에서 처음으로: {v}"),
        other => panic!("{other:?}"),
    }
}

#[test]
fn a_link_row_opens_a_screen() {
    let row = SETTINGS.iter().position(|s| s.key == "list-keys").expect("줄");
    assert!(matches!(
        setting_pick(row, &base_values()),
        Some(SettingPick::Act(crate::Action::ShowKeys))
    ));
}

#[test]
fn exit_empty_shows_the_server_value_or_admits_ignorance() {
    // ★ G9s — 현재값은 서버 status 의 exit_empty 에서 온다(2026-07-30 서버 CL).
    //   구버전 서버(미전송)는 `?` — 기본 on 을 off 로 지어내는 것보다 낫다.
    let row = SETTINGS.iter().find(|s| s.key == "exit-empty").expect("exit-empty");
    let mut values = base_values();
    values.exit_empty = None;
    assert_eq!(row.value(&values), "?", "모르면 모른다고 적는다");
    values.exit_empty = Some(true);
    assert_eq!(row.value(&values), "on");
    values.exit_empty = Some(false);
    assert_eq!(row.value(&values), "off");
}

#[test]
fn a_link_row_has_no_value() {
    // 값이 있는 것처럼 보이면 사용자가 그걸 바꾸려 든다.
    let values = base_values();
    for key in ["list-keys", "plugins"] {
        let row = SETTINGS.iter().find(|s| s.key == key).expect(key);
        assert_eq!(row.value(&values), "…");
    }
}

fn base_values() -> SettingValues {
    SettingValues {
        inactive_dim: true,
        border_status: false,
        single_border: false,
        coalesce_repaints: false,
        nest_auto_attach: false,
        win_mouse_motion: false,
        mouse: true,
        mouse_drag_copy: true,
        tab_bar_always: true,
        default_path: "current".into(),
        strip_box_drawing: true,
        inactive_dim_ratio: 0.18,
        mode_keys: "vi".into(),
        mouse_drag_threshold: 1,
        ambiguous_width: "auto".into(),
        vt_parser: "pyte".into(),
        window_size: "latest".into(),
        sync: false,
        monitor_activity: false,
        monitor_bell: false,
        auto_rename: false,
        prefix: (Key::Char('b'), Mods::CTRL),
        ..Default::default()
    }
}

#[test]
fn the_settings_are_grouped_by_category() {
    // ★ 화면은 `cat` 이 바뀔 때 머리줄을 찍는다 — 표가 섞여 있으면 같은 카테고리
    //   머리줄이 여러 번 나온다(2026-07-29 라이브 스크린샷이 그렇게 잡혔다).
    let mut seen: Vec<&str> = Vec::new();
    for setting in SETTINGS {
        if seen.last() != Some(&setting.cat) {
            assert!(
                !seen.contains(&setting.cat),
                "'{}' 카테고리가 흩어져 있다",
                setting.cat
            );
            seen.push(setting.cat);
        }
    }
    // 순서도 파이썬 `SETTINGS_CATS` 와 같아야 한다 — 눈이 외운 자리다.
    let order = ["표시", "입력", "동작", "상태줄", "고급", "키"];
    let mut at = 0usize;
    for cat in seen {
        let want = order[at..].iter().position(|c| *c == cat);
        at += want.unwrap_or_else(|| panic!("모르는 카테고리 '{cat}'"));
    }
}

// ── 사용자 키 바인딩(패리티 G8j) ──────────────────────────────────────────────

#[test]
fn a_bind_line_becomes_a_binding() {
    let c = Config::parse("bind r source-file
");
    assert_eq!(c.binds.len(), 1);
    assert!(c.binds[0].after_prefix, "`bind` 는 prefix 뒤다");
    assert_eq!(c.binds[0].key, "r");
    assert_eq!(c.binds[0].action(), Some(crate::Action::SourceFile));
}

#[test]
fn bind_dash_n_is_the_root_table() {
    // prefix 없이 바로 먹는 키(`bind -n`).
    let c = Config::parse("bind -n f5 redraw
");
    assert_eq!(c.binds.len(), 1);
    assert!(!c.binds[0].after_prefix);
    assert_eq!(c.binds[0].key, "f5");
}

#[test]
fn tmux_key_spelling_is_normalized() {
    // ★ raw `C-x` 로 두면 **절대 안 먹는다** — 우리가 견주는 이름은 `ctrl-x` 다.
    assert_eq!(crate::config::normalize_bind_key("C-x"), "ctrl-x");
    assert_eq!(crate::config::normalize_bind_key("M-q"), "alt-q");
    assert_eq!(crate::config::normalize_bind_key("G"), "shift-G");
    assert_eq!(crate::config::normalize_bind_key("enter"), "enter");
}

#[test]
fn a_bind_to_something_we_cannot_do_is_just_dead() {
    // 팔레트에 없는 명령이면 액션이 없다 — 그 줄을 오류로 만들지는 않는다(파이썬 클라의
    // 설정 파일에는 우리가 아직 못 하는 명령이 잔뜩 있다).
    let c = Config::parse("bind x if-shell true
");
    assert_eq!(c.binds.len(), 1);
    assert_eq!(c.binds[0].action(), None);
}

#[test]
fn the_mode_has_to_match() {
    // `bind -n q` 를 걸어 둔 사람이 prefix 뒤에 `q` 를 눌렀을 때 그게 발동하면 안 된다.
    let c = Config::parse("bind -n q redraw
");
    let hit = |after| {
        crate::config::user_action(&c.binds, after, Key::Char('q'), Mods::NONE)
    };
    assert!(hit(false).is_some());
    assert!(hit(true).is_none());
}

#[test]
fn a_modifier_binding_matches_the_pressed_key() {
    let c = Config::parse("bind -n C-g redraw
");
    assert_eq!(
        crate::config::user_action(&c.binds, false, Key::Char('g'), Mods::CTRL),
        Some(crate::Action::Redraw)
    );
    assert_eq!(
        crate::config::user_action(&c.binds, false, Key::Char('g'), Mods::NONE),
        None,
        "수정키 없이도 먹으면 그 글자를 못 친다"
    );
}

#[test]
fn a_bad_mode_keys_value_keeps_the_default() {
    // 오타 하나로 스크롤 키가 사라지면 무엇이 잘못됐는지 알 수 없다.
    assert_eq!(Config::parse("set mode-keys emacs").mode_keys, "emacs");
    assert_eq!(Config::parse("set mode-keys 아무거나").mode_keys, "vi");
    assert_eq!(Config::default().mode_keys, "vi", "파이썬 기본값과 같다");
}

#[test]
fn the_drag_threshold_is_clamped_to_a_usable_range() {
    // 0 이면 클릭이 전부 선택이 되고, 너무 크면 선택을 아예 못 한다.
    assert_eq!(Config::parse("set mouse-drag-threshold 3").mouse_drag_threshold, 3);
    assert_eq!(Config::parse("set mouse-drag-threshold 0").mouse_drag_threshold, 1);
    assert_eq!(Config::parse("set mouse-drag-threshold 99").mouse_drag_threshold, 20);
    assert_eq!(Config::default().mouse_drag_threshold, 1, "파이썬 기본값");
}

#[test]
fn an_integer_setting_is_written_as_an_integer() {
    // ★ `3.00` 을 파이썬 `int(...)` 가 못 읽는다.
    let (next, _) = set_number("mouse-drag-threshold", 3.0, &Config::default())
        .expect("숫자 설정");
    assert_eq!(next.mouse_drag_threshold, 3);
}

#[test]
fn ambiguous_width_takes_three_values() {
    assert_eq!(Config::default().ambiguous_width, "auto");
    assert_eq!(Config::parse("set ambiguous-width wide").ambiguous_width, "wide");
    assert_eq!(Config::parse("set ambiguous-width narrow").ambiguous_width, "narrow");
    // 모르는 값이면 기본값 그대로 — 오타로 화면이 밀리면 원인을 못 찾는다.
    assert_eq!(Config::parse("set ambiguous-width 넓게").ambiguous_width, "auto");
}

// ── 설정 물음 ↔ 설정 키 왕복 ─────────────────────────────────────────────────

#[test]
fn every_text_setting_round_trips_from_its_prompt() {
    // ★ 값을 받는 줄을 더해 놓고 **대답을 어디에 쓸지 안 적는** 실수를 잡는다. 그러면
    //   화면에서 고쳐도 아무 일이 안 일어나고, 그건 "설정이 있는데 안 먹는다"다.
    // 특별 취급이 필요해 **일부러 뺀** 둘. 여기 이름이 늘어난다면 그만큼 "화면에서
    // 고쳐도 안 먹는" 위험이 는 것이다 — 이유를 `prompt_key` 에 적고 여기 더한다.
    let special = ["default-path", "prefix"];
    for setting in SETTINGS {
        if let SettingKind::Text(prompt) = setting.kind {
            let want = (!special.contains(&setting.key)).then_some(setting.key);
            assert_eq!(
                crate::config::prompt_key(prompt),
                want,
                "{} 의 물음이 되돌아오지 않는다",
                setting.key
            );
        }
    }
}

#[test]
fn a_prompt_that_is_not_a_setting_has_no_key() {
    // 아무 물음이나 설정 키로 읽히면 엉뚱한 대답이 설정 파일에 적힌다.
    assert_eq!(crate::config::prompt_key(Prompt::RenameTab), None);
    assert_eq!(crate::config::prompt_key(Prompt::RunShell), None);
}

#[test]
fn the_status_bar_settings_are_all_there() {
    // 파이썬 「상태줄」 묶음 넷 + 위치 + 주기. 하나라도 빠지면 그 설정은 화면에서
    // 못 고친다(팔레트에만 있는 것은 설정 표가 세지 않는다는 G6 규칙).
    for key in [
        "status-left",
        "status-right",
        "status-bg",
        "status-fg",
        "status-position",
        "status-interval",
    ] {
        assert!(SETTINGS.iter().any(|s| s.key == key), "{key} 줄이 없다");
    }
}

#[test]
fn a_value_with_spaces_survives_the_parser() {
    // ★ 상태줄 형식은 **공백이 든 값**이다. 첫 낱말만 받으면 파이썬 기본 형식이
    //   `#{pane_title}#h` 로 잘려 시각이 통째로 사라진다 — 이 결함은 상태줄이 생기기
    //   전까지 드러나지 않았다(그전 옵션은 전부 한 낱말짜리였다).
    let config = Config::parse("set status-right #{pane_title}#h %H:%M %Y-%m-%d");
    assert_eq!(config.status_right, "#{pane_title}#h %H:%M %Y-%m-%d");
}

#[test]
fn the_status_defaults_match_the_python_client() {
    // 같은 설정 파일을 나눠 쓰므로(로드맵 결정 3) **아무것도 안 적었을 때** 두 클라가
    // 같은 줄을 그려야 한다.
    let config = Config::default();
    assert_eq!(config.status_left, " ");
    assert_eq!(config.status_right, " #{pane_title}#h %H:%M %Y-%m-%d ");
    assert_eq!(config.status_position, "bottom");
    assert_eq!(config.status_interval, 15);
}

#[test]
fn a_silly_status_interval_is_clamped_not_refused() {
    // 0 을 적으면 매 프레임 다시 그리게 된다. 파이썬도 `max(1, …)` 로 자른다.
    assert_eq!(Config::parse("set status-interval 0").status_interval, 1);
    assert_eq!(Config::parse("set status-interval 999").status_interval, 60);
    // 숫자가 아니면 기본값 그대로 — 오타 하나로 상태줄이 멎으면 안 된다.
    assert_eq!(Config::parse("set status-interval 없음").status_interval, 15);
}

#[test]
fn an_unknown_status_position_keeps_the_default() {
    assert_eq!(Config::parse("set status-position 옆").status_position, "bottom");
    assert_eq!(Config::parse("set status-position top").status_position, "top");
}

#[test]
fn a_hook_line_in_the_config_is_read_with_either_spelling() {
    // 파이썬 `keymap.py` 는 `hook` 과 `set-hook` 둘 다 받는다. 한쪽만 읽으면
    // **같은 파일을 공유하는** 다른 클라에서 적은 줄이 우리에게만 안 보인다.
    for text in [
        "hook after-new-window rename-tab 빌드",
        "set-hook after-new-window rename-tab 빌드",
    ] {
        let config = Config::parse(text);
        assert_eq!(
            config.hooks.get("after-new-window"),
            Some("rename-tab 빌드"),
            "{text}"
        );
    }
}

#[test]
fn a_hook_command_keeps_its_spaces() {
    // 훅 명령은 거의 항상 인자를 낀다 — 첫 낱말만 받으면 전부 헛돈다(`set` 이
    // 같은 결함을 갖고 있었다).
    let config = Config::parse("hook alert-bell run-shell echo 벨이 울렸다\n");
    assert_eq!(config.hooks.get("alert-bell"), Some("run-shell echo 벨이 울렸다"));
}

#[test]
fn a_hook_line_without_a_command_is_skipped_not_fatal() {
    let config = Config::parse("hook alert-bell\nset prefix C-a\n");
    assert!(config.hooks.is_empty());
    // 그 줄을 못 읽었다고 **뒤 줄까지 버리지 않는다.**
    assert_eq!(config.prefix, (Key::Char('a'), Mods::CTRL));
}

// ── alt-scroll(패리티) — 단말의 대체 스크롤 모드(DECSET 1007) ────────────────

#[test]
fn alt_scroll_is_on_by_default_like_the_python_client() {
    // 파이썬 기본값이 `disable_alt_scroll = True` 다 — 즉 **끈다**. 기본이 반대면
    // 휠이 화살표로 오는 단말에서 스크롤백이 안 열린 채 시작한다.
    assert!(Config::default().alt_scroll);
}

#[test]
fn alt_scroll_is_read_from_the_config_file() {
    let config = Config::parse("set alt-scroll off\n");
    assert!(!config.alt_scroll);
    // `_` 표기도 같은 옵션이다(정규화가 흡수한다).
    let config = Config::parse("set alt_scroll off\n");
    assert!(!config.alt_scroll, "밑줄 표기를 다른 옵션으로 읽었다");
}

#[test]
fn the_settings_screen_has_a_row_for_alt_scroll_in_the_input_category() {
    // 파이썬 `SETTINGS` 와 같은 범주다(`{"key": "alt-scroll", "cat": "입력"}`). 줄을
    // **키로** 찾는다 — 표를 재정렬해도 안 낡게(이 저장소가 세 번 밟은 자리).
    let row = SETTINGS
        .iter()
        .find(|s| s.key == "alt-scroll")
        .expect("설정 화면에 alt-scroll 줄이 없다");
    assert_eq!(row.cat, "입력");
}

#[test]
fn flipping_alt_scroll_changes_the_value_that_the_screen_shows() {
    // **양성 오라클**이다: 뒤집은 뒤 화면에 적히는 글자가 실제로 달라지는가.
    let now = Config::default();
    let (next, _) = super::flip_config("alt-scroll", &now)
        .expect("alt-scroll 을 뒤집을 수 없다");
    assert!(!next.alt_scroll);
    let row = SETTINGS.iter().find(|s| s.key == "alt-scroll").unwrap();
    let shown = |c: &Config| {
        row.value(&SettingValues {
            alt_scroll: c.alt_scroll,
            ..SettingValues::default()
        })
    };
    assert_eq!(shown(&now), "on");
    assert_eq!(shown(&next), "off");
}

#[test]
fn a_byte_order_mark_does_not_swallow_the_first_line() {
    // ★ Windows PowerShell 5.1 의 `Set-Content -Encoding utf8` 은 **기본으로 BOM 을
    // 붙인다.** 안 떼면 `\u{feff}set` 이 `set` 이 아니라 첫 줄만 조용히 사라지고,
    // 나머지 줄은 멀쩡히 먹으므로 증상이 "이 설정 하나만 안 먹는다"가 된다
    // (2026-07-30 라이브에서 실제로 밟았다).
    let with_bom = Config::parse("\u{feff}set alt-scroll off\nset mouse off\n");
    assert!(!with_bom.alt_scroll, "BOM 때문에 첫 줄이 사라졌다");
    assert!(!with_bom.mouse, "둘째 줄은 원래 먹었다 — 대조군");
}

// ── set-titles(패리티) — 창/단말 제목 ────────────────────────────────────────

#[test]
fn set_titles_is_off_by_default_and_has_the_python_format() {
    // 제목은 **바깥 것**이다. 기본으로 덮어쓰면 탭 이름을 쓰는 사람에게 놀라운 일이 된다 —
    // 파이썬도 옵트인(`config.get("set_titles", False)`)이고 형식은 `#S:#I:#W` 다.
    let config = Config::default();
    assert!(!config.set_titles);
    assert_eq!(config.set_titles_string, "#S:#I:#W");
}

#[test]
fn set_titles_and_its_format_are_read_from_the_config_file() {
    let config = Config::parse("set set-titles on\nset set-titles-string #S · #W\n");
    assert!(config.set_titles);
    assert_eq!(config.set_titles_string, "#S · #W");
}

#[test]
fn the_settings_screen_has_a_set_titles_row_in_the_behaviour_category() {
    // 파이썬 `SETTINGS` 의 `{"key": "set-titles", "cat": "동작"}` 과 같은 자리다.
    let row = SETTINGS
        .iter()
        .find(|s| s.key == "set-titles")
        .expect("설정 화면에 set-titles 줄이 없다");
    assert_eq!(row.cat, "동작");
}

/// 설정 표에서 그 키의 줄 번호(줄 번호를 박아 두면 표를 재정렬할 때 낡는다 — 이
/// 저장소가 세 번 밟은 자리라 목록 테스트는 늘 키로 찾는다).
fn row_of(key: &str) -> usize {
    SETTINGS.iter().position(|s| s.key == key).unwrap_or_else(|| panic!("{key} 줄이 없다"))
}

#[test]
fn a_choice_row_shows_every_choice_and_marks_the_current_one() {
    // ★ 종전 화면은 **지금 값만** 적었다(`auto`). 그러면 그 줄이 무엇을 받는지는
    //   눌러 봐야 안다 — `tab-bar` 가 on/off 인지 always/auto 인지 화면에 없었다.
    let values = SettingValues { tab_bar_always: false, ..Default::default() };
    let display = SETTINGS[row_of("tab-bar")].display(&values);
    let ValueDisplay::Choices { labels, cur } = display else {
        panic!("고르는 줄이 아니라고 한다: {display:?}");
    };
    assert_eq!(labels, vec!["항상".to_owned(), "자동".to_owned()], "선택지를 다 안 편다");
    assert_eq!(cur, Some(1), "지금 값이 `auto` 인데 강조가 딴 데 있다");

    // 켜기/끄기도 같은 형이다(정본 `["on", "off"] if t == "bool"`).
    let values = SettingValues { inactive_dim: true, ..Default::default() };
    let ValueDisplay::Choices { labels, cur } = SETTINGS[row_of("inactive-dim")].display(&values)
    else {
        panic!("토글 줄이 선택지를 안 편다");
    };
    assert_eq!(labels, vec!["켜짐".to_owned(), "꺼짐".to_owned()]);
    assert_eq!(cur, Some(0));
}

#[test]
fn a_value_the_server_has_not_told_us_is_not_pretended_to_be_known() {
    // 모르는 것을 아는 척하면 사용자가 그 값을 믿고 판단한다. `exit-empty` 는 구버전
    // 서버가 안 싣는 칸이라 이 자리가 실물이다.
    let values = SettingValues { exit_empty: None, ..Default::default() };
    let ValueDisplay::Choices { labels, cur } = SETTINGS[row_of("exit-empty")].display(&values)
    else {
        panic!("토글 줄이 선택지를 안 편다");
    };
    assert_eq!(labels.len(), 2, "선택지는 그대로 보여야 한다 — 무엇을 받는 줄인지는 안다");
    assert_eq!(cur, None, "모르는 값을 어느 한쪽으로 강조했다");
}

#[test]
fn the_other_row_shapes_follow_canon() {
    // 숫자는 `‹ ›` 로 "좌우로 바뀐다"를 알리고, 자유 입력은 비었을 때 (미설정)이며,
    // 링크 줄은 값이 아니라 '열기'다. 셋을 한 형으로 뭉개면 뷰가 다시 갈라 파싱한다.
    let values = SettingValues { inactive_dim_ratio: 0.2, ..Default::default() };
    assert_eq!(
        SETTINGS[row_of("inactive-dim-ratio")].display(&values),
        ValueDisplay::Stepper("0.20".to_owned())
    );

    let values = SettingValues { default_path: String::new(), ..Default::default() };
    assert_eq!(
        SETTINGS[row_of("default-path")].display(&values),
        ValueDisplay::Text { shown: "(미설정)".to_owned(), unset: true }
    );
    let values = SettingValues { default_path: "/tmp".to_owned(), ..Default::default() };
    assert_eq!(
        SETTINGS[row_of("default-path")].display(&values),
        ValueDisplay::Text { shown: "/tmp".to_owned(), unset: false }
    );

    assert_eq!(
        SETTINGS[row_of("plugins")].display(&SettingValues::default()),
        ValueDisplay::Link("열기")
    );
}

#[test]
fn settings_are_named_in_human_words_not_option_keys() {
    // 설정은 **이름을 모르는 사람**이 여는 화면이다. 옵션 키를 읽고 무엇인지 아는
    // 사람은 이미 `set-option` 을 칠 줄 안다.
    assert_eq!(setting_label("inactive-dim"), "비활성 패널 흐리게");
    assert_eq!(setting_label("mouse-drag-threshold"), "드래그 인정 최소 이동(칸)");
    // 모든 줄에 이름이 있다 — 하나라도 빠지면 그 줄만 코드 낱말로 튄다.
    for setting in SETTINGS {
        assert_ne!(setting_label(setting.key), setting.key, "{} 이름이 없다", setting.key);
    }
    // 기술적인 값은 옮기지 않는다(옮기면 설정 파일에서 못 찾는다).
    assert_eq!(setting_value_label("pyte"), "pyte");
    assert_eq!(setting_value_label("on"), "켜짐");
}

// ── 설정 축 측정(2026-08-02p) — G1 을 액션에서 **설정 줄**로 넓힌다 ─────────────
//
// 패리티 표의 설정 칸은 36줄인데, 그 값은 액션 축처럼 **재서** 얻은 것이 아니었다
// (`parity.rs` 의 Item 주석: *"설정 36·화면 17 축은 아직 같은 강도로 안 쟀다"*).
// 위 `picking_a_toggle_row_gives_an_action…` 이 재는 것은 **모양**이다 — 줄마다 다섯
// 종류 중 하나가 나온다는 것. 그 다음 질문이 안 물어져 있었다: **그 효과가 실제로
// 어딘가에 닿는가.**
//
// 닿지 않는 길이 실재한다. `flip_config`/`set_config` 는 자기가 모르는 키에 `None` 을
// 돌려주고(그 줄은 조용히 아무 일도 안 한다), 설정 파일 파서도 모르는 줄을 조용히
// 넘긴다 — 증상은 `prompt_key` 주석이 적어 둔 그대로다: *"설정 화면에서 고쳐도 안
// 바뀐다"*.
//
// 그래서 **파일을 안 건드리고** 잰다: 그 줄이 쓰겠다는 값을 `edit_option` 으로 설정
// 파일 글에 얹고 다시 파싱해, 파서가 그 키를 알아듣는지 본다. 쓰기 경로를 부르면
// `PYTMUX_CONFIG` 를 세워야 하는데 그건 프로세스 전역이라 형제 테스트와 경합한다
// (2026-08-02 로케일에서 겪은 그 모양).

/// 설정 파일이 값의 주인이 **아닌** 줄과 그 이유.
///
/// 서버가 쥔 값(`Act`)과 특별 취급이 필요한 물음 둘은 파일 왕복으로 잴 수 없다.
/// 목록이 곧 이 측정의 예외이고, 새로 생기면 같은 CL 에서 이유와 함께 여기 적힌다.
static NOT_A_CONFIG_ROW: &[(&str, &str)] = &[
    ("default-path", "빈 대답이 **되돌리기**라 `prompt_key` 가 일부러 안 돌려준다"),
    ("prefix", "대답이 값이 아니라 **키 표기**(`C-a`)라 파싱을 거친다"),
];

#[test]
fn every_setting_row_reaches_something_that_reads_it() {
    let values = base_values();
    let mut dead: Vec<String> = Vec::new();
    let mut acted: Vec<&'static str> = Vec::new();
    for (row, setting) in SETTINGS.iter().enumerate() {
        let lands = match setting_pick_dir(row, &values, true) {
            // 서버가 값의 주인이다 — 이 줄이 닿는 곳은 액션 축이 잰다(GUI 의
            // `every_action_does_something_in_this_view`). 이름만 모아 둔다.
            Some(SettingPick::Act(_)) => {
                acted.push(setting.key);
                true
            }
            Some(SettingPick::Ask(prompt, _)) => {
                prompt_key(prompt).is_some()
                    || NOT_A_CONFIG_ROW.iter().any(|(k, _)| *k == setting.key)
            }
            // 뒤집기·놓기·숫자는 **설정 파일**이 주인이다 → 파일 왕복으로 잰다.
            Some(SettingPick::Flip(key)) => {
                Config::parse(&edit_option("", key, "on"))
                    != Config::parse(&edit_option("", key, "off"))
            }
            // ⚠ **다른 값**을 표에서 고른다. 처음엔 `←`(뒤로) 가 준 값과 견줬는데,
            //    선택지가 둘뿐인 줄은 앞뒤가 같은 값이라 셋이 거짓으로 죽었다
            //    (tab-bar·status-position·mode-keys — 제품이 아니라 자가 틀렸다).
            Some(SettingPick::Set(key, value)) => match setting.kind {
                SettingKind::ConfigEnum(list) => list
                    .iter()
                    .find(|v| **v != value)
                    .is_some_and(|other| {
                        Config::parse(&edit_option("", key, value))
                            != Config::parse(&edit_option("", key, other))
                    }),
                _ => false,
            },
            // 숫자도 같은 이유로 **끝값 둘**을 견준다(한 걸음이 기본값에 떨어지면
            // 기본값과의 비교가 조용히 거짓이 된다 — 실제로 둘이 그랬다).
            Some(SettingPick::SetNumber(key, _)) => match setting.kind {
                // ★ 값을 **제품이 적는 그대로** 적는다(`number_text`). 손으로
                //   `{:.2}` 를 적었더니 정수 옵션 둘이 죽은 줄로 나왔는데, 그건 제품이
                //   아니라 이 자가 `3.00` 을 쓴 탓이었다 — 그 형식은 파서가 못 읽는다.
                SettingKind::Number { lo, hi, .. } => {
                    Config::parse(&edit_option("", key, &number_text(key, lo)))
                        != Config::parse(&edit_option("", key, &number_text(key, hi)))
                }
                _ => false,
            },
            None => false,
        };
        if !lands {
            dead.push(setting.key.to_owned());
        }
    }
    assert!(
        dead.is_empty(),
        "설정 줄이 **아무 데도 안 닿는다** — 화면에서 고쳐도 안 바뀐다는 뜻이다. \
         고치거나(파서·`flip_config`·`prompt_key` 에 그 키를 더하거나) 왜 파일이 주인이 \
         아닌지를 NOT_A_CONFIG_ROW 에 적을 것: {dead:?}"
    );
    // 빈 결과가 통과로 보이지 않게 — 표가 통째로 비면 위 루프는 조용히 초록이다.
    assert!(SETTINGS.len() >= 30, "설정 표가 줄었다: {}", SETTINGS.len());
    assert!(!acted.is_empty(), "서버가 주인인 줄이 하나도 없다 — 측정이 낡았다");
}

// ── 커서 모양·색·깜빡임(`pytmux/pytmux-161`) ─────────────────────────────────
//
// 「닿기는 하나」는 위 `every_setting_row_reaches_something_that_reads_it` 가 넷 다
// 이미 잰다(설정 화면 → 파일 → 파서 왕복). 여기서 재는 것은 그 축이 **못 보는 것**,
// 곧 «잘못 적힌 값이 어떻게 접히나»다 — 그 판정이 없으면 오타 한 글자가 커서를
// 화면에서 지우고, 그 실패는 조용하다.

#[test]
fn the_cursor_shape_is_read_from_the_config_file() {
    for want in CURSOR_STYLES {
        let config = Config::parse(&format!("set cursor-style {want}"));
        assert_eq!(&config.cursor_style, want);
    }
}

#[test]
fn an_unknown_cursor_shape_falls_back_instead_of_erasing_the_cursor() {
    // ⛔ 모르는 값을 그대로 담으면 그리는 쪽이 어느 모양도 못 골라 **아무것도 안
    //    그린다**. 오타 하나로 커서가 사라지면 무엇이 잘못됐는지 알 길이 없다
    //    (`remote-title`·`mode-keys` 와 같은 판정).
    let config = Config::parse("set cursor-style blcok");
    assert_eq!(config.cursor_style, CURSOR_STYLES[0]);
    assert_eq!(config.cursor_style, Config::default().cursor_style);
}

#[test]
fn the_cursor_defaults_keep_todays_screen() {
    // ★ 이 이슈가 부탁한 것은 「고를 수 있게」이지 「기본을 바꿔라」가 아니다.
    //   기본을 채운 네모로 옮기면 지금 쓰는 사람의 화면이 말없이 달라진다.
    let config = Config::default();
    assert_eq!(config.cursor_style, "hollow");
    assert!(!config.cursor_blink, "기본은 안 깜빡인다(종전 동작)");
    assert!(config.cursor_color.is_empty(), "빈 값 = 테마 그대로");
}

#[test]
fn the_cursor_color_takes_the_same_notation_as_the_status_bar() {
    // 자리마다 다른 표기를 받으면 한쪽에서 배운 것을 다른 쪽에서 못 쓴다. 표기를
    // 아는 것은 푸는 쪽(`proto::status::color`)이므로 **글자 그대로** 담는다.
    for text in ["blue", "brightblack", "#ff8800"] {
        assert_eq!(Config::parse(&format!("set cursor-color {text}")).cursor_color, text);
    }
}

#[test]
fn the_blink_period_is_clamped_not_dropped() {
    // 범위를 벗어난 값 때문에 설정 파일을 통째로 못 읽는 일은 없어야 한다
    // (`inactive-dim-ratio` 와 같은 규칙).
    assert_eq!(Config::parse("set cursor-blink-interval 1").cursor_blink_ms, 100);
    assert_eq!(Config::parse("set cursor-blink-interval 99999").cursor_blink_ms, 2000);
    assert_eq!(Config::parse("set cursor-blink-interval 250").cursor_blink_ms, 250);
}

// ── 커서 두께(`pytmux/pytmux-375` ⓑ) ─────────────────────────────────────────
//
// 「닿기는 하나」는 위 `every_setting_row_reaches_something_that_reads_it` 가 이미
// 잰다(설정 화면 → 파일 → 파서 왕복). 여기서 재는 것은 그 축이 **못 보는 것** 셋:
// 잘못 적힌 값이 어떻게 접히나 · 판이 모으는 다섯 줄이 실제로 있나 · 그리고 아래
// 「화면에 적는 글이 도로 읽히나」다.

#[test]
fn the_cursor_thickness_is_clamped_not_dropped() {
    // 범위 밖 때문에 설정 파일을 통째로 못 읽는 일은 없어야 한다
    // (`inactive-dim-ratio`·`cursor-blink-interval` 과 같은 규칙).
    assert_eq!(Config::parse("set cursor-thickness 0").cursor_thickness, CURSOR_THICKNESS_LO);
    assert_eq!(Config::parse("set cursor-thickness 99").cursor_thickness, CURSOR_THICKNESS_HI);
    assert_eq!(Config::parse("set cursor-thickness 3.5").cursor_thickness, 3.5);
    // 못 읽는 글자는 그 줄만 버린다 — 기본값이 남는다(커서가 사라지면 안 된다).
    assert_eq!(
        Config::parse("set cursor-thickness 굵게").cursor_thickness,
        Config::default().cursor_thickness
    );
}

#[test]
fn the_cursor_thickness_default_keeps_todays_screen() {
    // ★ 이 이슈가 부탁한 것은 「고를 수 있게」이지 「기본을 바꿔라」가 아니다.
    //   종전 상수(`splitter.rs` 의 `CURSOR_PX`)가 2px 였으므로 기본도 2px 다.
    assert_eq!(Config::default().cursor_thickness, 2.);
}

#[test]
fn the_cursor_panel_rows_all_exist() {
    // 판은 키 이름으로 줄을 모은다(색인이 아니라 — `CURSOR_SETTINGS` 머리말). 그래서
    // 이름 하나가 오타이거나 `SETTINGS` 에서 그 줄이 사라지면 **판에서 조용히 빠진다**.
    for (row, key) in CURSOR_SETTINGS.iter().enumerate() {
        let at = cursor_setting_row(row).unwrap_or_else(|| panic!("{key} 를 설정 표에서 못 찾았다"));
        assert_eq!(SETTINGS[at].key, *key, "{row}번째 줄이 다른 설정을 가리킨다");
    }
    assert_eq!(cursor_setting_row(CURSOR_SETTINGS.len()), None, "없는 줄이 무언가를 가리킨다");
    // 빈 목록은 「전부 통과」로 보인다 — 통과가 아니라 고장이다.
    assert!(CURSOR_SETTINGS.len() >= 5, "커서 판의 줄이 줄었다: {}", CURSOR_SETTINGS.len());
}

#[test]
fn the_thickness_row_goes_dim_only_on_block() {
    // ⚠ 판정은 **줄 하나**에만 걸린다 — 모양을 바꿨다고 다른 줄까지 흐려지면 그건
    //   "안 먹는다"가 아니라 "화면이 고장 났다"로 읽힌다.
    for (row, key) in CURSOR_SETTINGS.iter().enumerate() {
        let dim_on_block = !cursor_row_applies(row, "block");
        assert_eq!(dim_on_block, *key == "cursor-thickness", "{key} 의 판정이 틀렸다");
        for style in CURSOR_STYLES.iter().filter(|s| **s != "block") {
            assert!(cursor_row_applies(row, style), "{key} 가 {style} 에서 흐려졌다");
        }
    }
}

/// ☠ 숫자 줄의 `←→` 는 **지금 값의 한 걸음**이어야 한다 (pytmux-393).
///
/// 종전 오라클(`a_number_setting_shows_a_value_it_can_read_back`)은 *"화면에 적는 글이
/// 도로 읽히나"* 를 쟀다. 그때는 [`setting_pick_dir`] 이 실제로 그 글자를 되읽었기
/// 때문이고, 그래서 `font-scale` 의 `"1.0×"` 는 **예외 목록에 사유와 함께** 적혀
/// 있었다 — 「결함을 미룬다」는 선언이었다.
///
/// 이제 걸음은 [`number_value`] 가 **날 값**에서 뗀다. 그러면 재야 할 것은 표시의
/// 형식이 아니라 **행동**이다: `→` 한 번이 `지금 값 + step` 인가. 표시에 단위를 붙여도
/// 이 자는 안 운다 — 그것이 이 고침의 요지다(그 대가로 옛 예외 목록은 사라졌다).
///
/// ⛔ 이 자는 [`SETTINGS`] 의 `Number` **전수**를 돈다. `number_value` 에 줄을 안 더한
/// 새 숫자 설정은 여기서 이름과 함께 죽는다 — 그 설정의 `←→` 가 값을 아래 끝으로
/// 떨어뜨리기 전에.
#[test]
fn every_number_row_steps_from_its_own_value() {
    // 값을 전부 **아래 끝이 아닌 곳**에 놓는다. `lo` 에 두면 「글자를 되읽다 죽고 lo 로
    // 떨어지는」 옛 결함의 답과 정답이 겹쳐 오라클이 공허해진다.
    let values = SettingValues {
        inactive_dim_ratio: 0.30,
        font_scale: 1.4,
        mouse_drag_threshold: 7,
        status_interval: 20,
        cursor_blink_ms: 700,
        cursor_thickness: 2.5,
        ..base_values()
    };
    let mut checked = 0;
    for (row, setting) in SETTINGS.iter().enumerate() {
        let SettingKind::Number { lo, hi, step } = setting.kind else {
            continue;
        };
        checked += 1;
        let now = number_value(setting.key, &values).unwrap_or_else(|| {
            panic!(
                "{} 가 `number_value` 표에 없다 — `←→` 가 그 값을 아래 끝으로 떨어뜨린다",
                setting.key
            )
        });
        assert!(
            (now - lo).abs() > f32::EPSILON,
            "{}: 이 시험이 값을 아래 끝에 뒀다 — 옛 결함의 답과 겹쳐 오라클이 공허하다",
            setting.key
        );
        for forward in [true, false] {
            let step_to = if forward { now + step } else { now - step };
            let want = if step_to > hi + f32::EPSILON {
                lo
            } else if step_to < lo - f32::EPSILON {
                hi
            } else {
                step_to
            };
            match setting_pick_dir(row, &values, forward) {
                Some(SettingPick::SetNumber(key, got)) => {
                    assert_eq!(key, setting.key, "다른 줄의 키를 냈다");
                    assert!(
                        (got - want).abs() < 1e-4,
                        "{}: `{}` 한 걸음이 {got} 다 — 지금 값 {now} 에서 뗀 {want} 여야 한다",
                        setting.key,
                        if forward { "→" } else { "←" }
                    );
                }
                other => panic!("{}: 숫자 줄이 SetNumber 를 안 냈다: {other:?}", setting.key),
            }
        }
    }
    assert!(checked >= 5, "숫자 설정이 하나도 안 세어졌다 — 통과가 아니라 고장이다");
}

#[test]
fn the_blink_period_is_written_as_an_integer() {
    // ⛔ `250.00` 은 파서가 `u16` 으로 못 읽어 **그 줄이 조용히 무시된다** — 증상은
    //    "설정 화면에서 올렸는데 다음 기동에 되돌아 있다"다(`number_text` 머리말).
    assert_eq!(number_text("cursor-blink-interval", 250.0), "250");
    assert_eq!(
        Config::parse(&format!(
            "set cursor-blink-interval {}",
            number_text("cursor-blink-interval", 250.0)
        ))
        .cursor_blink_ms,
        250
    );
}
