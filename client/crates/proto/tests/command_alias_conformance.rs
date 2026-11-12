//! **명령 별칭 적합성** — `base::COMMAND_ALIASES` 가 정본과 같은가(pytmux-470).
//!
//! # 왜 이 자가 필요한가
//!
//! 정본은 명령을 표로 안 들고 `if/elif` 로 가르므로 이름을 여럿 받는 것이 공짜였다 —
//! `kill-pane` 도 `killp` 도, `new-window` 도 `neww` 도 같은 갈래다(tmux 손버릇). 우리는
//! [`base::PALETTE`] 의 이름 그대로만 해석해 **정본이 받는 195 중 92 만** 받았다
//! (pytmux-455 가 래칫의 모집단을 넓히며 처음 셌다).
//!
//! 별칭 표는 **손으로 적는다**(제품 코드가 읽어야 하고, 이 저장소의 생성기 스물일곱은
//! 전부 `tests/fixtures/*.json` 만 낸다). 손으로 적은 미러의 위험은 *조용히 낡는 것*이니
//! **조용할 수 없게** 만든다: 여기서 정본 픽스처로 기대값을 지어 전수 대조하고, 갈리면
//! **무엇을 더하고 뺄지 글자 그대로** 찍는다.
//!
//! # ☠ 갈래는 별칭 관계가 아니다
//!
//! 이슈가 처음 적은 처방(*"그 묶음을 그대로 뽑으면 된다"*)은 그대로는 틀렸다. 정본에는
//! 이런 줄이 있다:
//!
//! ```text
//! c in ("pin-tab", "pin", "unpin-tab", "unpin", "pin-toggle")
//! ```
//!
//! 몸통이 이름을 **다시 보고** 서로 다른 일을 한다 — 접으면 `unpin` 이 pin 을 한다.
//! 그래서 픽스처가 갈래마다 `dispatches_on_name` 을 싣고(생성기
//! `scripts/gen_client_surface_fixture.py` §`_branch_groups`), 그런 갈래는 **접지 않는다**.

use std::collections::{BTreeMap, BTreeSet};

use serde::Deserialize;

#[derive(Deserialize)]
struct Group {
    names: Vec<String>,
    dispatches_on_name: bool,
}

#[derive(Deserialize)]
struct Fx {
    client_cmds: Vec<String>,
    client_cmd_groups: Vec<Group>,
    /// 정본 메뉴(`clientutil` 의 메뉴 표)의 항목 키 — 팔레트 짝이 없는 갈래의 짝을 찾는 데
    /// 쓴다(아래 [`menu_twin`]).
    menu_items: Vec<String>,
}

fn fixture() -> Fx {
    serde_json::from_str(include_str!("fixtures/client_surface.json"))
        .expect("픽스처를 읽을 수 없다 — scripts/gen_client_surface_fixture.py 로 다시 뽑을 것")
}

/// 팔레트가 그 **이름**을 든다(`split-window %` 처럼 인자 자리가 붙은 줄도 이름만 본다 —
/// `parity.rs::we_take_the_command` 와 같은 규약).
fn in_palette(name: &str) -> bool {
    base::PALETTE
        .iter()
        .any(|e| e.name == name || e.name.split(' ').next() == Some(name))
}

/// 팔레트 이름 하나가 **실제로 하는 일**(이름 그대로 든 줄의 액션).
fn palette_action(name: &str) -> Option<base::Action> {
    base::PALETTE.iter().find(|e| e.name == name).map(|e| e.action)
}

/// 팔레트 짝이 없는 갈래(`zoom`)의 짝 — **같은 이름의 정본 메뉴 항목**이 있을 때만.
///
/// 정본에서 명령 `zoom` 과 메뉴 항목 `zoom` 은 같은 일(`send_cmd("zoom")`)이다. 우리 메뉴가
/// 그 키에 거는 액션을 팔레트에서 하는 줄이 **정확히 하나**면 그 줄이 짝이다. 둘 이상이면
/// 고를 근거가 없으므로 짝이 없다(손으로 정하지 않는다).
fn menu_twin(fx: &Fx, name: &str) -> Option<&'static str> {
    if !fx.menu_items.iter().any(|m| m == name) {
        return None;
    }
    let action = base::MENU.iter().find(|m| m.key == name)?.action;
    let mut hits = base::PALETTE.iter().filter(|e| e.action == action);
    let first = hits.next()?;
    hits.next().is_none().then_some(first.name)
}

/// 갈래 하나의 이름 집합.
fn set_of(g: &Group) -> BTreeSet<&str> {
    g.names.iter().map(String::as_str).collect()
}

/// 정본 픽스처가 말하는 **기대 별칭 표**와, 접을 수 없어 남는 이름들.
///
/// ⚠ 「어느 것이 팔레트 이름인가」는 **여기서** 정한다(픽스처는 정본만 안다). 그 경계가
/// 생성기 머리말이 적어 둔 것이다 — 픽스처가 우리 쪽 표를 알게 되면 자가 대상을 안다.
///
/// # 접는 규칙 (pytmux-576 에서 다듬었다)
///
/// - **이름으로 다시 가르는 갈래**(`dispatches_on_name`)는 그 갈래째로는 안 접는다.
/// - 그런데 그 몸통 안의 작은 갈래가 `c in ("pin-tab", "pin")` 처럼 다시 동의어를 묶으면
///   픽스처에 **가르지 않는 작은 갈래**로 따로 실린다. 큰 갈래에 낀 이름은 그런 작은 갈래가
///   **그 큰 갈래의 부분집합으로** 있을 때만 접는다(`pin` → `pin-tab`).
///   ☠ `setw` 는 `(automatic-rename, setw)` 같은 작은 갈래가 있지만 그것은 가르는 갈래
///   `(monitor-activity, monitor-bell, setw)` 의 부분집합이 아니다 — 인자로 가르는 이름이라
///   접으면 `setw monitor-bell on` 이 엉뚱한 일을 한다(첫 회차에 잡은 바로 그것).
/// - 한 이름이 **서로 다른 짝**으로 접히려 하면 못 접는다(고를 근거가 없다).
/// - 팔레트 짝이 없는 갈래는 [`menu_twin`] 이 짝을 찾을 때만 접는다(`zoom`).
fn expected() -> (BTreeMap<String, String>, BTreeSet<String>) {
    let fx = fixture();
    // 가르는 갈래에 낀 이름 중 **덮어 주는 작은 갈래가 없는 것** — 통째로 못 접는다.
    let mut unfoldable: BTreeSet<&str> = BTreeSet::new();
    for big in fx.client_cmd_groups.iter().filter(|g| g.dispatches_on_name) {
        let big_set = set_of(big);
        for name in &big.names {
            let covered = fx.client_cmd_groups.iter().any(|small| {
                let small_set = set_of(small);
                !small.dispatches_on_name
                    && small_set.contains(name.as_str())
                    && small_set.len() < big_set.len()
                    && small_set.is_subset(&big_set)
            });
            if !covered {
                unfoldable.insert(name.as_str());
            }
        }
    }
    let mut alias: BTreeMap<String, String> = BTreeMap::new();
    let mut conflict: BTreeSet<String> = BTreeSet::new();
    let mut left = BTreeSet::new();
    for group in &fx.client_cmd_groups {
        let anchor = group.names.iter().find(|n| in_palette(n)).map(String::as_str);
        for name in &group.names {
            if in_palette(name) {
                continue;
            }
            if group.dispatches_on_name || unfoldable.contains(name.as_str()) {
                left.insert(name.clone());
                continue;
            }
            let target = anchor.or_else(|| {
                // 짝 없는 **홑 갈래**만 메뉴에서 짝을 찾는다(여럿이 묶인 갈래는 이미
                // 팔레트 이름이 있거나, 없으면 고를 근거가 없다).
                (group.names.len() == 1).then(|| menu_twin(&fx, name)).flatten()
            });
            match target {
                Some(target) => match alias.get(name) {
                    Some(prev) if prev != target => {
                        conflict.insert(name.clone());
                    }
                    _ => {
                        alias.insert(name.clone(), target.to_owned());
                    }
                },
                None => {
                    left.insert(name.clone());
                }
            }
        }
    }
    for name in &conflict {
        alias.remove(name);
        left.insert(name.clone());
    }
    // 접을 수 있는 것으로 한 번이라도 잡혔으면 「남는 것」에서 뺀다(같은 이름이 갈래
    // 둘에 걸치되 **둘 다 안전한** 경우 — 앞 갈래에 팔레트 짝이 없었을 뿐이다).
    left.retain(|name| !alias.contains_key(name));
    (alias, left)
}

#[test]
fn the_alias_table_says_exactly_what_canon_says() {
    let (want, _) = expected();
    let got: BTreeMap<String, String> = base::COMMAND_ALIASES
        .iter()
        .map(|(a, c)| ((*a).to_owned(), (*c).to_owned()))
        .collect();
    if got == want {
        return;
    }
    let mut lines = Vec::new();
    for (alias, canonical) in &want {
        match got.get(alias) {
            None => lines.push(format!("    (\"{alias}\", \"{canonical}\"),   // ← 더할 것")),
            Some(mine) if mine != canonical => lines.push(format!(
                "    (\"{alias}\", \"{canonical}\"),   // ← 지금 \"{mine}\" 로 적혀 있다"
            )),
            _ => {}
        }
    }
    for alias in got.keys().filter(|a| !want.contains_key(*a)) {
        lines.push(format!("    (\"{alias}\", …),   // ← 뺄 것(정본에 그 갈래가 없다)"));
    }
    panic!(
        "`base::COMMAND_ALIASES` 가 정본과 다르다 — 아래 그대로 고칠 것:\n{}\n\
         (기대 {} 줄 · 지금 {} 줄. 정본이 이름을 늘렸으면 픽스처부터 다시 뽑는다:\n\
          `python3 scripts/gen_client_surface_fixture.py`)",
        lines.join("\n"),
        want.len(),
        got.len()
    );
}

#[test]
fn a_name_that_dispatches_on_itself_is_never_folded() {
    // ☠ 이 시험이 없으면 표를 「갈래를 그대로 뽑아」 다시 채우는 순간 `unpin` 이 pin 을
    //    한다. 그 위험을 **이름으로** 못박는다 — 가르는 갈래에 낀 이름이 접혀 있으면 그
    //    짝은 반드시 **그 이름과 함께 묶인 작은 동의어 갈래**의 팔레트 이름이라야 한다.
    let fx = fixture();
    let mut wrong = Vec::new();
    for group in fx.client_cmd_groups.iter().filter(|g| g.dispatches_on_name) {
        for name in &group.names {
            let Some((_, canonical)) = base::COMMAND_ALIASES.iter().find(|(a, _)| a == name)
            else {
                continue;
            };
            let ok = fx.client_cmd_groups.iter().any(|small| {
                !small.dispatches_on_name
                    && small.names.len() < group.names.len()
                    && small.names.iter().all(|n| group.names.contains(n))
                    && small.names.contains(name)
                    && small.names.iter().any(|n| n == canonical)
            });
            if !ok {
                wrong.push(format!("{name} → {canonical} (갈래: {:?})", group.names));
            }
        }
    }
    assert!(
        wrong.is_empty(),
        "이름으로 다시 가르는 갈래를 엉뚱한 짝으로 접었다 — 뜻이 바뀐다:\n  {}",
        wrong.join("\n  ")
    );
}

#[test]
fn the_names_closed_by_576_do_what_canon_does() {
    // ★ 이름만 받고 엉뚱한 일을 하면 래칫은 초록인데 사용자는 틀린 결과를 본다. 그래서
    //   pytmux-576 이 닫은 셋은 **액션으로** 단언한다 — 명령 프롬프트(팔레트 맨 윗줄)와
    //   설정 파일 `bind` 두 길 다.
    //   정본: `pin` → `set_pinned(value=True)` · `unpin` → `set_pinned(value=False)` ·
    //   `zoom` → `send_cmd("zoom")`(clientcmd.py `_run_command`).
    for (name, want) in [
        ("pin", base::Action::SetPinned(true)),
        ("unpin", base::Action::SetPinned(false)),
        ("zoom", base::Action::Zoom),
    ] {
        // 명령 프롬프트 — 그 이름을 치면 맨 윗줄이 고른 줄이다.
        let top = base::palette_matches(name)
            .first()
            .map(|i| base::PALETTE[*i].action);
        assert_eq!(top, Some(want), "`:{name}` 의 맨 윗줄이 다른 일을 한다");
        // 설정 파일 `bind`.
        let bind = base::config::Bind::parse(&format!("bind x {name}"))
            .expect("bind 줄로 못 읽었다");
        assert_eq!(bind.action(), Some(want), "`bind x {name}` 이 다른 일을 한다");
        assert_eq!(palette_action(base::resolve_command_name(name)), Some(want));
    }
    // `zoom` 의 짝은 메뉴가 정한다 — 우리 메뉴의 `zoom` 과 팔레트의 짝이 같은 일이어야 한다.
    let menu = base::MENU.iter().find(|m| m.key == "zoom").map(|m| m.action);
    assert_eq!(menu, Some(base::Action::Zoom));
}

#[test]
fn resolving_a_name_never_leaves_the_palette() {
    // 해석의 결과는 **반드시 팔레트에 있는 이름**이라야 한다. 아니면 뷰가 그 이름을 찾다
    // 실패하고, 사용자에게는 「받는다고 해 놓고 모르는 명령이라 한다」로 보인다.
    for (alias, canonical) in base::COMMAND_ALIASES {
        assert!(
            in_palette(canonical),
            "`{alias}` 가 팔레트에 없는 이름 `{canonical}` 으로 간다"
        );
        assert!(
            !in_palette(alias),
            "`{alias}` 는 팔레트에 이미 있는 이름이다 — 별칭 줄이 필요 없다"
        );
        assert_eq!(base::resolve_command_name(alias), *canonical);
    }
    // 별칭이 아닌 이름은 **그대로** 돌아온다(해석이 팔레트 이름을 안 바꾼다).
    for entry in base::PALETTE {
        let name = entry.name.split(' ').next().unwrap_or(entry.name);
        assert_eq!(base::resolve_command_name(name), name, "팔레트 이름이 옮겨졌다");
    }
}

#[test]
fn what_we_still_cannot_take_is_named_with_a_reason() {
    // ⛔ 「몇을 덮었다」만 세면 **남은 것이 조용히 잊힌다.** 이 시험은 남는 것을 **이름으로**
    //    고정한다 — 늘거나 줄면 여기서 울고, 그때 사유를 다시 적게 된다.
    //
    // 남는 부류는 둘이고 **둘 다 별칭 문제가 아니다**:
    //   · 그 갈래에 팔레트 이름이 하나도 없다 → 팔레트에 그 줄을 내야 풀린다.
    //   · 이름으로 다시 가르는 갈래인데 그 이름이 팔레트에 없다 → 마찬가지다.
    let (_, left) = expected();
    let want: BTreeSet<String> = [
        // 이름으로 다시 가르는 갈래 — 접으면 다른 일을 한다.
        //   · `setw` — 이름이 아니라 **인자**로 가른다(`setw monitor-bell on`). 이름
        //     하나를 이름 하나로 옮기는 이 표로는 못 받는다.
        // ★ **이 표 밖에서** 받는다 — 명령 줄 실행기(`base::cmdline` · pytmux-572/576).
        //   아래 `the_two_left_by_the_table_are_taken_by_the_executor` 가 액션으로 단언한다.
        //   (`monitor-bell` 은 팔레트에 다시 서서 이 목록을 나갔다 — pytmux-642 · 사람 결정 2026-11-13.)
        "setw",
    ]
    .iter()
    .map(|s| (*s).to_owned())
    .collect();
    assert_eq!(
        left, want,
        "아직 못 받는 이름이 달라졌다 — 사유와 함께 이 목록을 고칠 것"
    );
}

#[test]
fn the_two_left_by_the_table_are_taken_by_the_executor() {
    // pytmux-576 — 별칭 표가 못 접는 둘을 명령 줄 실행기가 **정본과 같은 일로** 받나. 명령
    // 프롬프트(목록에 걸린 줄이 없으면 친 줄을 실행기로 넘긴다)와 설정 파일 `bind` 가 같은
    // 함수(`base::cmdline::run_line`)를 지난다 — 두 입구를 다 단언한다.
    //   정본: `monitor-bell` → `set_monitor(which="bell", value=None)` ·
    //         `setw monitor-bell on` → `set_monitor(which="bell", value=True)`.
    use base::cmdline::{Aliases, Run};
    for (line, want) in [
        ("monitor-bell", base::Action::ToggleMonitorBell),
        ("monitor-bell off", base::Action::SetMonitor { bell: true, on: false }),
        ("setw monitor-bell on", base::Action::SetMonitor { bell: true, on: true }),
        ("setw monitor-activity off", base::Action::SetMonitor { bell: false, on: false }),
    ] {
        assert_eq!(base::cmdline::run_line(line, &Aliases::default()), Some(Run::Act(want)), "`:{line}`");
        let bind = base::config::Bind::parse(&format!("bind x {line}")).expect("bind 줄");
        assert_eq!(bind.action(), Some(want), "`bind x {line}`");
    }
    // 이제 목록에도 뜬다(pytmux-642 · 사람 결정 2026-11-13 — 정본처럼 보인다).
    assert!(base::palette_matches("monitor-bell").iter().any(|i| base::PALETTE[*i].name == "monitor-bell"));
    assert!(base::cmdline::knows("setw") && base::cmdline::knows("monitor-bell"));
}

#[test]
fn the_palette_list_itself_did_not_grow() {
    // ⛔ 별칭이 **목록에 뜨면** 두 클라의 목록이 갈린다(정본 팔레트는 89 다). 여기 있는
    //    것은 「쳤을 때 알아듣는 이름」이지 「보여 주는 이름」이 아니다.
    let shown = base::palette_matches("").len();
    assert_eq!(
        shown,
        base::PALETTE.len(),
        "빈 필터가 표 전체를 안 낸다 — 이 시험의 전제가 깨졌다"
    );
    for (alias, _) in base::COMMAND_ALIASES {
        assert!(
            !base::PALETTE.iter().any(|e| e.name == *alias),
            "별칭 `{alias}` 이 팔레트 목록에 섰다"
        );
    }
}

#[test]
fn typing_an_alias_finds_the_command_at_the_top() {
    // 사용자가 겪는 것은 이 한 가지다 — `killp` 를 쳤을 때 그 줄이 **맨 위**에 오나.
    // (별칭을 표에만 넣고 찾는 길에 안 붙이면 목록이 비고, 그 사람에게 그 명령은 없다.)
    for (alias, canonical) in [("killp", "kill-pane"), ("neww", "new-tab"), ("splitw", "split-window")] {
        let hits = base::palette_matches(alias);
        let first = hits
            .first()
            .map(|i| base::PALETTE[*i].name)
            .unwrap_or("(없다)");
        assert_eq!(
            first.split(' ').next(),
            Some(canonical),
            "`{alias}` 를 쳤는데 맨 위가 `{first}` 다"
        );
    }
}

#[test]
fn every_canon_name_is_reachable_or_listed_as_not_yet() {
    // 전수 — 정본이 받는 195 를 하나씩 물어 「받나」를 센다. 못 받는 것은 위
    // `what_we_still_cannot_take_is_named_with_a_reason` 의 목록과 **정확히 같아야** 한다.
    let fx = fixture();
    let (_, left) = expected();
    let missing: BTreeSet<String> = fx
        .client_cmds
        .iter()
        .filter(|name| !in_palette(base::resolve_command_name(name)))
        .cloned()
        .collect();
    assert_eq!(
        missing, left,
        "「받는다」의 셈이 별칭 표와 안 맞는다 — 표에 있는데 못 닿거나 그 반대다"
    );
}
