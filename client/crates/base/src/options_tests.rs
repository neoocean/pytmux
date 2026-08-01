use super::*;

fn find(command: &str) -> &'static CommandOptions {
    options_for(command).unwrap_or_else(|| panic!("{command} 이 표에 없다"))
}

#[test]
fn a_row_shows_the_arrows_only_when_there_is_somewhere_to_go() {
    let split = find("split-window");
    let row = row_text(&split.specs[0], 0);
    assert!(row.contains("◀ ▶"), "{row}");
    assert!(row.contains("좌우 분할"), "{row}");
    // 선택지가 하나뿐인 자리에는 화살표를 안 그린다 — 누를 데가 없는 화살표는 거짓말이다.
    let single = OptionSpec {
        label: "혼자",
        choices: &[Choice {
            label: "하나",
            value: "1",
        }],
    };
    assert!(!row_text(&single, 0).contains("◀"), "{}", row_text(&single, 0));
}

#[test]
fn the_line_shows_what_will_actually_run() {
    let split = find("split-window");
    assert_eq!(line(split, &[0]), "split-window -h");
    assert_eq!(line(split, &[1]), "split-window -v");
}

#[test]
fn an_empty_value_adds_no_word() {
    // `토글`·`보이는 영역` 은 값이 없다 — 정본과 같이 낱말을 안 붙인다.
    assert_eq!(line(find("synchronize-panes"), &[0]), "synchronize-panes");
    assert_eq!(line(find("capture-pane"), &[0]), "capture-pane");
    assert_eq!(line(find("capture-pane"), &[1]), "capture-pane -S");
}

#[test]
fn the_directions_map_to_the_same_actions_the_keys_use() {
    // 화면에서 고른 것과 `prefix ←` 가 다른 일을 하면 안 된다.
    assert_eq!(
        pick(find("select-pane"), &[0]),
        Some(OptionPick::Act(Action::SelectPane(Dir::Left)))
    );
    assert_eq!(
        pick(find("select-pane"), &[3]),
        Some(OptionPick::Act(Action::SelectPane(Dir::Down)))
    );
}

#[test]
fn zoom_is_not_a_direction() {
    // `resize-pane` 의 첫 선택지만 방향이 아니다 — 방향으로 읽으면 아무 일도 안 한다.
    assert_eq!(
        pick(find("resize-pane"), &[0]),
        Some(OptionPick::Act(Action::Zoom))
    );
    assert_eq!(
        pick(find("resize-pane"), &[1]),
        Some(OptionPick::Act(Action::ResizePane(Dir::Left)))
    );
}

#[test]
fn on_and_off_are_not_the_same_as_toggle() {
    // ★ 이 슬라이스의 요점이다. 토글은 지금 값을 모르는 채 뒤집고, 켜기/끄기는 정한다.
    let sync = find("synchronize-panes");
    assert_eq!(pick(sync, &[0]), Some(OptionPick::Act(Action::ToggleSync)));
    assert_eq!(pick(sync, &[1]), Some(OptionPick::Act(Action::SetSync(true))));
    assert_eq!(pick(sync, &[2]), Some(OptionPick::Act(Action::SetSync(false))));
}

#[test]
fn the_two_monitors_do_not_get_crossed() {
    assert_eq!(
        pick(find("monitor-activity"), &[1]),
        Some(OptionPick::Act(Action::SetMonitor {
            bell: false,
            on: true
        }))
    );
    assert_eq!(
        pick(find("monitor-bell"), &[1]),
        Some(OptionPick::Act(Action::SetMonitor {
            bell: true,
            on: true
        }))
    );
}

#[test]
fn a_server_option_keeps_its_identity() {
    assert_eq!(
        pick(find("exit-empty"), &[2]),
        Some(OptionPick::Act(Action::SetServerOption(
            ServerOpt::ExitEmpty,
            false
        )))
    );
    assert_eq!(
        pick(find("exit-empty"), &[0]),
        Some(OptionPick::Act(Action::ToggleServerOption(
            ServerOpt::ExitEmpty
        )))
    );
}

#[test]
fn our_own_settings_go_to_the_config_file_not_the_server() {
    assert_eq!(
        pick(find("inactive-dim"), &[1]),
        Some(OptionPick::Set("inactive-dim", "on"))
    );
    assert_eq!(
        pick(find("inactive-dim-ratio"), &[2]),
        Some(OptionPick::Set("inactive-dim-ratio", "0.30"))
    );
    // 토글은 지금 값을 여기서 모르므로 **뒤집으라고만** 말한다(설정이 값을 안다).
    assert_eq!(
        pick(find("inactive-dim"), &[0]),
        Some(OptionPick::Act(Action::ToggleInactiveDim))
    );
    assert_eq!(
        pick(find("strip-box-drawing"), &[0]),
        Some(OptionPick::Flip("strip-box-drawing"))
    );
}

#[test]
fn an_enum_value_must_be_one_the_server_knows() {
    assert_eq!(
        pick(find("vt-parser"), &[1]),
        Some(OptionPick::Act(Action::SetEnum(EnumOpt::VtParser, "native")))
    );
    assert_eq!(
        pick(find("window-size"), &[2]),
        Some(OptionPick::Act(Action::SetEnum(
            EnumOpt::WindowSize,
            "largest"
        )))
    );
}

#[test]
fn every_row_in_the_table_can_actually_do_something() {
    // 표에 이름만 있고 할 일이 없으면 고르는 순간 아무 일도 안 일어난다 — 그건
    // "명령이 있는데 안 먹는다"로 읽힌다.
    for options in COMMAND_OPTIONS {
        let choices = options.specs[0].choices.len();
        let usable = (0..choices)
            .filter(|i| pick(options, &[*i]).is_some())
            .count();
        assert!(
            usable > 0,
            "{} 은 어느 값을 골라도 아무 일도 안 한다",
            options.command
        );
    }
}

#[test]
fn every_enum_value_in_the_table_is_spelled_the_way_the_server_reads_it() {
    // 철자가 하나 틀리면 그 값만 조용히 무시된다.
    for (command, opt) in [
        ("vt-parser", EnumOpt::VtParser),
        ("window-size", EnumOpt::WindowSize),
    ] {
        let options = find(command);
        for (i, choice) in options.specs[0].choices.iter().enumerate() {
            assert!(
                opt.choices().contains(&choice.value),
                "{command} 의 {} 는 서버가 모르는 철자다",
                choice.value
            );
            assert!(pick(options, &[i]).is_some(), "{command}[{i}]");
        }
    }
}

#[test]
fn a_command_we_do_not_know_has_no_form() {
    assert!(options_for("no-such-command").is_none());
}
