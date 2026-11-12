use super::*;

fn none() -> Aliases {
    Aliases::default()
}

#[test]
fn an_argument_command_runs_with_its_argument() {
    // pytmux-572 관문 ① — `bind x send-keys C-c` 의 줄이 그 키를 보낸다.
    assert_eq!(
        run_line("send-keys C-c", &none()),
        Some(Run::Answer(Prompt::SendKeys, "C-c".into()))
    );
    assert_eq!(
        run_line("display-message hi", &none()),
        Some(Run::Answer(Prompt::DisplayMessage, "hi".into()))
    );
}

#[test]
fn if_shell_takes_tmux_quoting() {
    // 정본 `_if_shell(args[0], args[1], args[2])` — shlex 로 쪼갠 낱말 셋이다.
    assert_eq!(
        run_line("if-shell 'test -f x' \"send-keys foo\" 'display-message no'", &none()),
        Some(Run::IfShell {
            cond: "test -f x".into(),
            then: "send-keys foo".into(),
            otherwise: Some("display-message no".into()),
        })
    );
    // 조건만 있으면 정본도 아무 일도 안 한다.
    assert_eq!(run_line("if-shell true", &none()), None);
}

#[test]
fn a_user_alias_is_expanded_before_the_name_and_keeps_trailing_args() {
    let mut a = Aliases::default();
    a.set("sp", "split-window -h");
    a.set("say", "display-message");
    assert_eq!(run_line("sp", &a), Some(Run::Act(Action::SplitLeftRight)));
    // 정본 `self.aliases[c] + " " + " ".join(args)`.
    assert_eq!(
        run_line("say hello there", &a),
        Some(Run::Answer(Prompt::DisplayMessage, "hello there".into()))
    );
    // 별칭 찾기는 친 이름을 소문자로 접어 찾는다(정본 `c = parts[0].lower()`).
    assert_eq!(run_line("SP", &a), Some(Run::Act(Action::SplitLeftRight)));
}

#[test]
fn alias_recursion_stops_at_the_canon_depth() {
    let mut a = Aliases::default();
    a.set("loop", "loop");
    assert_eq!(run_line("loop", &a), None, "자기를 부르는 별칭은 한도에서 멈춘다");
    // 한도 안의 사슬은 풀린다 — 정본은 `_depth > 8` 에서 멈추고 별칭 하나가 깊이 하나라,
    // 별칭 여덟 겹까지는 마지막 명령이 깊이 8 에서 돈다.
    let mut chain = Aliases::default();
    for i in 0..MAX_DEPTH - 1 {
        chain.set(&format!("a{i}"), &format!("a{}", i + 1));
    }
    chain.set(&format!("a{}", MAX_DEPTH - 1), "redraw");
    assert_eq!(run_line("a0", &chain), Some(Run::Act(Action::Redraw)));
    chain.set(&format!("a{}", MAX_DEPTH - 1), &format!("a{MAX_DEPTH}"));
    chain.set(&format!("a{MAX_DEPTH}"), "redraw");
    assert_eq!(run_line("a0", &chain), None, "아홉 겹째는 정본도 안 푼다");
}

#[test]
fn setw_dispatches_on_its_argument() {
    // pytmux-576 — 이름이 아니라 인자로 가른다.
    assert_eq!(
        run_line("setw monitor-bell on", &none()),
        Some(Run::Act(Action::SetMonitor { bell: true, on: true }))
    );
    assert_eq!(
        run_line("setw monitor-activity off", &none()),
        Some(Run::Act(Action::SetMonitor { bell: false, on: false }))
    );
    assert_eq!(
        run_line("setw synchronize-panes", &none()),
        Some(Run::Act(Action::ToggleSync))
    );
    assert_eq!(run_line("setw", &none()), None, "가를 인자가 없으면 정본도 아무 일도 안 한다");
}

#[test]
fn the_hidden_monitor_bell_is_taken_when_typed() {
    // 팔레트에서는 감췄지만(`HIDDEN_SURFACES`) 치면 먹는다(pytmux-576).
    assert_eq!(run_line("monitor-bell", &none()), Some(Run::Act(Action::ToggleMonitorBell)));
    assert_eq!(
        run_line("monitor-bell off", &none()),
        Some(Run::Act(Action::SetMonitor { bell: true, on: false }))
    );
    assert!(crate::keymap::is_hidden("monitor-bell"), "감춤은 그대로다");
}

#[test]
fn a_palette_name_with_a_flag_still_wins_whole() {
    assert_eq!(run_line("split-window -h", &none()), Some(Run::Act(Action::SplitLeftRight)));
    assert_eq!(run_line("split-window", &none()), Some(Run::Act(Action::SplitTopBottom)));
    assert_eq!(run_line("zoom", &none()), Some(Run::Act(Action::Zoom)));
}

#[test]
fn source_file_takes_a_path() {
    assert_eq!(
        run_line("source-file /tmp/x.conf", &none()),
        Some(Run::SourceFile(Some("/tmp/x.conf".into())))
    );
    assert_eq!(run_line("source", &none()), Some(Run::Act(Action::SourceFile)));
}

#[test]
fn shell_words_follow_shlex() {
    assert_eq!(shell_words(r#"a 'b c' "d \"e\"" f\ g"#), vec!["a", "b c", "d \"e\"", "f g"]);
    // 닫히지 않은 따옴표 — 정본처럼 공백으로 쪼갠다.
    assert_eq!(shell_words("a 'b c"), vec!["a", "'b", "c"]);
}

#[test]
fn a_hangul_typo_in_the_name_is_restored() {
    // 정본 `hangul_to_qwerty` — 이름 토큰만(인자는 안 건드린다). `ㄱㄷㅇㄱㅁㅈ` = `redraw`.
    assert_eq!(run_line("ㄱㄷㅇㄱㅁㅈ", &none()), Some(Run::Act(Action::Redraw)));
}
