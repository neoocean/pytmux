use super::*;

#[test]
fn a_hook_can_be_set_read_and_unset() {
    let mut hooks = Hooks::default();
    hooks.set("after-new-window", "rename-tab H");
    assert_eq!(hooks.get("after-new-window"), Some("rename-tab H"));
    assert!(hooks.unset("after-new-window"));
    assert_eq!(hooks.get("after-new-window"), None);
    // 없는 것을 지우면 **거짓**이다 — 호출부가 "그런 훅 없다"를 말할 수 있어야 한다.
    assert!(!hooks.unset("after-new-window"));
}

#[test]
fn setting_the_same_event_again_keeps_its_place() {
    let mut hooks = Hooks::default();
    hooks.set("a", "1");
    hooks.set("b", "2");
    hooks.set("a", "3");
    // 값 하나 고쳤다고 목록이 재배열되면 무엇이 바뀌었는지 눈으로 못 쫓는다.
    assert_eq!(hooks.lines(), vec!["a → 3", "b → 2"]);
}

#[test]
fn an_empty_hook_list_still_says_something() {
    let hooks = Hooks::default();
    assert_eq!(hooks.lines().len(), 1);
    assert!(hooks.lines()[0].contains("없다"));
}

#[test]
fn an_unknown_event_name_is_kept_not_refused() {
    // 플러그인 훅(`claude-limit` 등)이 적힌 **공유 설정 파일**이 우리 쪽에서만
    // 반쯤 사라지면 안 된다.
    let mut hooks = Hooks::default();
    hooks.set("claude-limit", "display-message 한도");
    assert_eq!(hooks.len(), 1);
    assert_eq!(hooks.get("claude-limit"), Some("display-message 한도"));
}

#[test]
fn set_hook_reads_the_event_and_the_whole_rest_as_the_command() {
    assert_eq!(
        parse_set_hook("after-new-window rename-tab 새 탭"),
        Some(SetHook::Set {
            event: String::from("after-new-window"),
            command: String::from("rename-tab 새 탭"),
        })
    );
}

#[test]
fn dash_u_means_remove() {
    assert_eq!(
        parse_set_hook("-u alert-bell"),
        Some(SetHook::Unset {
            event: String::from("alert-bell"),
        })
    );
}

#[test]
fn an_event_with_no_command_is_not_a_hook() {
    assert_eq!(parse_set_hook("alert-bell"), None);
    assert_eq!(parse_set_hook(""), None);
    // `-u` 인데 이름이 없으면 아무것도 안 한다(무엇을 지울지 모른다).
    assert_eq!(parse_set_hook("-u"), None);
}

#[test]
fn attaching_fires_once_and_only_once() {
    let mut watcher = HookWatcher::default();
    assert_eq!(watcher.saw_layout(), Some(HookEvent::ClientAttached));
    // 배치는 창 크기가 바뀔 때마다 다시 온다 — 그때마다 발화하면 훅이 아니라 폭주다.
    assert_eq!(watcher.saw_layout(), None);
    assert_eq!(watcher.saw_layout(), None);
}

#[test]
fn the_first_tab_list_is_not_a_new_tab() {
    let mut watcher = HookWatcher::default();
    // 붙자마자 탭 셋이 보이는 것은 "셋 생긴" 것이 아니다.
    assert_eq!(watcher.saw_status(3, false), Vec::new());
    assert_eq!(watcher.saw_status(4, false), vec![HookEvent::AfterNewWindow]);
}

#[test]
fn a_tab_closing_does_not_fire_and_does_not_break_the_next_one() {
    let mut watcher = HookWatcher::default();
    watcher.saw_status(2, false);
    assert_eq!(watcher.saw_status(1, false), Vec::new());
    assert_eq!(watcher.saw_status(2, false), vec![HookEvent::AfterNewWindow]);
}

#[test]
fn a_bell_fires_on_the_edge_not_while_it_stays_on() {
    let mut watcher = HookWatcher::default();
    watcher.saw_status(1, false);
    assert_eq!(watcher.saw_status(1, true), vec![HookEvent::AlertBell]);
    assert_eq!(watcher.saw_status(1, true), Vec::new());
    watcher.saw_status(1, false);
    assert_eq!(watcher.saw_status(1, true), vec![HookEvent::AlertBell]);
}

#[test]
fn both_events_can_fire_from_one_status_in_the_canon_order() {
    let mut watcher = HookWatcher::default();
    watcher.saw_status(1, false);
    assert_eq!(
        watcher.saw_status(2, true),
        vec![HookEvent::AfterNewWindow, HookEvent::AlertBell]
    );
}

#[test]
fn the_event_names_are_the_canon_spelling() {
    // 설정 파일을 두 클라가 공유한다 — 철자가 갈리면 훅이 조용히 안 먹는다.
    let names: Vec<&str> = HookEvent::ALL.iter().map(|e| e.name()).collect();
    assert_eq!(names, vec!["client-attached", "after-new-window", "alert-bell"]);
}

#[test]
fn a_bare_command_resolves_to_the_same_action_the_palette_gives() {
    assert_eq!(resolve("next-tab"), Some(HookRun::Act(Action::NextTab)));
    assert_eq!(resolve("  redraw  "), Some(HookRun::Act(Action::Redraw)));
}

#[test]
fn a_flag_that_belongs_to_the_name_is_not_torn_off_as_an_argument() {
    // 팔레트에는 `split-window -h` 처럼 플래그를 이름에 품은 항목이 있다. 첫 낱말만
    // 떼면 `split-window` + 인자 `-h` 로 잘못 갈린다.
    assert_eq!(
        resolve("split-window -h"),
        Some(HookRun::Act(Action::SplitLeftRight))
    );
    assert_eq!(
        resolve("capture-pane -S"),
        Some(HookRun::Act(Action::CapturePane(true)))
    );
}

#[test]
fn an_argument_skips_the_question_and_answers_it() {
    assert_eq!(
        resolve("rename-tab 빌드"),
        Some(HookRun::Answer(Prompt::RenameTab, String::from("빌드")))
    );
    // 인자는 **줄의 나머지 전부**다 — 공백에서 자르면 셸 명령이 반토막 난다.
    assert_eq!(
        resolve("run-shell echo hi there"),
        Some(HookRun::Answer(
            Prompt::RunShell,
            String::from("echo hi there")
        ))
    );
}

#[test]
fn the_canon_aliases_resolve_too() {
    // 파이썬 문서를 보고 적은 훅(`rename-window`)이 우리 쪽에서만 안 먹으면 안 된다.
    assert_eq!(
        resolve("rename-window H"),
        Some(HookRun::Answer(Prompt::RenameTab, String::from("H")))
    );
    assert_eq!(
        resolve("run echo hi"),
        Some(HookRun::Answer(Prompt::RunShell, String::from("echo hi")))
    );
}

#[test]
fn an_unknown_command_is_ignored_instead_of_guessed() {
    assert_eq!(resolve("no-such-command 1"), None);
    assert_eq!(resolve(""), None);
    assert_eq!(resolve("   "), None);
}

#[test]
fn every_argument_command_names_a_prompt_we_can_actually_answer() {
    // 표에 오타가 나면 그 훅만 조용히 안 먹는다. 이름은 전부 소문자여야 찾힌다.
    for (name, _) in ARG_COMMANDS {
        assert_eq!(
            *name,
            name.to_ascii_lowercase(),
            "{name} 은 소문자가 아니라 영영 안 찾힌다"
        );
        assert!(!name.contains(' '), "{name} 에 공백이 있으면 첫 낱말로 못 찾는다");
    }
}
