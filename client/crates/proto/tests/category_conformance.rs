//! 교차구현 적합성 — **화면을 나누는 기준**이 정본과 같은가.
//!
//! 설정 사이드바 · 팔레트 카테고리 탭 · 메뉴 계층은 셋 다 "무엇을 어떻게 묶나"가 화면이다.
//! 그 묶는 기준은 정본에만 있었고, 87+34+31 줄을 손으로 옮기면 조용히 어긋난다 — 그 부류의
//! 손번역은 이 저장소가 G9y 에서 이미 한 번 정본 추출로 갈아엎었다.
//!
//! 픽스처는 정본 구현에서 뽑았다: `python3 scripts/gen_categories.py`
//! (출처 = `pytmuxlib/clientutil.py` 의 분류 표 + `plugins` 레지스트리 기여).
//!
//! # 양성 오라클이다
//!
//! "정본에 없는 것을 쓰지 않는다"만 재면 **표가 통째로 비어도 통과한다**(이 저장소가 두 번
//! 밟았다). 그래서 여기서는 반대로 센다 — 우리 줄 하나하나가 정본의 **어느 분류인지**를
//! 맞히고, 못 맞히는 이름은 허용 목록에 **이름으로** 적혀 있어야 한다.

use std::collections::{BTreeMap, BTreeSet};

use base::config::{SETTINGS, SETTINGS_CATS};
use base::{MENU, MENU_GROUPS, MENU_GROUP_LABELS, MENU_TOGGLES,
                         MENU_TOPLEVEL, MenuRow, MenuTop, PALETTE, PALETTE_CATS, menu_rows};
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    command_cats: BTreeMap<String, String>,
    command_cat_order: Vec<String>,
    setting_cats: BTreeMap<String, String>,
    settings_cat_order: Vec<String>,
    menu_labels: BTreeMap<String, String>,
    menu_order: Vec<String>,
    menu_toggles: Vec<String>,
    menu_groups: BTreeMap<String, Vec<String>>,
    menu_toplevel: Vec<String>,
    menu_group_labels: BTreeMap<String, String>,
    plugin_menu_order: Vec<String>,
    plugin_menu_labels: BTreeMap<String, String>,
    plugin_menu_after: String,
    settings_order: Vec<String>,
    plugin_commands: Vec<FxCommand>,
    plugin_noarg: Vec<String>,
    plugin_settings: Vec<FxSetting>,
    plugin_setting_cats: Vec<String>,
}

#[derive(Deserialize)]
struct FxCommand {
    name: String,
    desc: String,
    cat: String,
}

#[derive(Deserialize)]
struct FxSetting {
    key: String,
    cat: String,
    #[serde(rename = "type")]
    kind: String,
    values: Vec<String>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/categories.json")).expect("픽스처를 읽을 수 없다")
}

/// 정본에서 뜬 픽스처를 **서버가 부는 표면**으로 옮긴다(설계 Tier A).
///
/// 플러그인 기여의 오라클은 전부 이것으로 잰다 — 정적 표가 아니라 **런타임 값이 화면에
/// 서는 것**이 P2 의 요점이기 때문이다. 픽스처는 `check_fixtures.py` 가 정본과 대조해
/// 신선도를 지키므로, 여기서 재는 것은 "정본이 지금 내는 그 목록"이 된다.
fn surface(fx: &Fixture) -> base::PluginSurface {
    base::PluginSurface {
        commands: fx
            .plugin_commands
            .iter()
            .map(|c| base::PluginCommand {
                name: c.name.clone(),
                desc: c.desc.clone(),
                cat: c.cat.clone(),
            })
            .collect(),
        noarg: fx.plugin_noarg.clone(),
        menu_items: fx
            .plugin_menu_order
            .iter()
            .map(|key| base::PluginMenuItem {
                key: key.clone(),
                label: fx.plugin_menu_labels.get(key).cloned().unwrap_or_default(),
            })
            .collect(),
        settings: fx
            .plugin_settings
            .iter()
            .map(|s| base::PluginSetting {
                key: s.key.clone(),
                cat: s.cat.clone(),
                kind: s.kind.clone(),
                values: s.values.clone(),
            })
            .collect(),
        setting_cats: fx.plugin_setting_cats.clone(),
    }
}

/// 정본이 **모르는** 팔레트 이름과 우리가 준 분류.
///
/// 정본에 없는 이름을 팔레트에 두는 것 자체는 결함이 아니다(우리 전용 입구·별칭이 있다 —
/// `PALETTE` 의 주석이 하나씩 이유를 든다). 다만 **조용히 늘어나면 안 된다**: 새 이름이
/// 여기 없으면 테스트가 운다. `command_conformance.rs` 의 `full` 예외 목록과 같은 규칙으로
/// **정확·정렬**이라야 한다.
static PALETTE_OURS: &[(&str, &str)] = &[
    // 정본 `SETTINGS` 에는 있고 `COMMANDS` 에는 없다(저쪽 입구는 설정 화면뿐).
    ("display-panes", "패널"),
    // ★ **GUI 만의 것**(§10-21ⓐ) — 정본의 글자 크기는 호스트 단말이 정하므로 저쪽에
    //   같은 이름이 있을 수 없다. 키(`Ctrl+=`/`Ctrl+-`/`Ctrl+0`)가 주 입구이고 팔레트는
    //   그 키를 모르는 사람의 입구다. **이 목록이 곧 그 선언이다** — 패리티 표에는
    //   실을 줄이 없다(그 표의 줄은 정본 픽스처가 정한다).
    ("font-scale-down", "설정/기타"),
    ("font-scale-reset", "설정/기타"),
    ("font-scale-up", "설정/기타"),
    // ★ **GUI 만의 것**(§10-21ⓘ3) — 정본(Textual TUI)의 풀스크린은 호스트 단말의 일이라
    //   저쪽에 같은 이름이 있을 수 없다(허용되는 갈림 ⓒ OS 창 통합). 키(`Alt`+`Enter`)가
    //   주 입구이고 팔레트는 그 키를 모르는 사람의 입구다.
    ("fullscreen", "설정/기타"),
    ("menu", "설정/기타"),
    ("notice-history", "설정/기타"),
    ("pane-border-status", "패널"),
    // 정본이 설명에서 "별칭"으로만 드는 이름들 — 팔레트는 이름을 쳐서 좁히는 화면이라
    // 별칭이 있으면 손버릇이 갈리지 않는다.
    ("plugin-manager", "설정/기타"),
    ("popup-close", "설정/기타"),
    ("resync", "설정/기타"),
    // ★ **GUI 만의 모드**(pytmux-18) — 캔버스 위에서 블록(명령 + 그 출력) 하나를 골라
    //   `↑`/`↓` 로 옮기고 `Ctrl+C` 로 복사한다. 정본에는 블록을 **보는 자리 자체가
    //   없어서**(파이썬 클라는 `blocks` 메시지를 안 그린다) 같은 이름이 있을 수 없다.
    //   키(`esc b`)가 주 입구이고 팔레트는 그 키를 모르는 사람의 입구다.
    ("select-blocks", "복사/버퍼"),
    ("status", "설정/기타"),
    // GUI 만의 판(§10-21ⓓ) — 정본에는 이 구역 자체가 없다(블록·Claude 요약은 우리가
    // 화면 아래에 갖고 있던 것이고, 제보로 판이 됐다).
    ("summary", "설정/기타"),
];

/// `PALETTE` 의 모든 줄이 정본과 같은 카테고리인가.
#[test]
fn every_palette_entry_carries_the_canon_category() {
    let fx = fixture();
    assert!(!fx.command_cats.is_empty(), "픽스처가 비었다");

    let ours: BTreeMap<&str, &str> = PALETTE_OURS.iter().copied().collect();
    let mut wrong = Vec::new();
    let mut unlisted = Vec::new();
    for entry in PALETTE {
        // 팔레트 이름이 `split-window -h` 처럼 플래그를 품기도 한다 — 기본형으로 찾는다
        // (`command_help` 가 설명을 찾는 방식과 같아야 둘이 갈라지지 않는다).
        let base = entry.name.split(' ').next().unwrap_or(entry.name);
        match fx.command_cats.get(entry.name).or_else(|| fx.command_cats.get(base)) {
            Some(canon) if canon == entry.cat => {}
            Some(canon) => wrong.push(format!("{}: 우리 {} · 정본 {canon}", entry.name, entry.cat)),
            None => match ours.get(base) {
                Some(cat) if *cat == entry.cat => {}
                Some(cat) => wrong.push(format!("{}: 우리 {} · 허용 목록 {cat}", entry.name, entry.cat)),
                None => unlisted.push(entry.name),
            },
        }
    }
    assert!(
        wrong.is_empty(),
        "정본과 다른 분류의 팔레트 줄:\n  {}\n\
         python3 scripts/gen_categories.py 로 픽스처를 다시 뽑았는지 볼 것.",
        wrong.join("\n  ")
    );
    assert!(
        unlisted.is_empty(),
        "정본이 모르는 팔레트 이름인데 PALETTE_OURS 에 없다: {unlisted:?}\n\
         우리 전용 이름이라면 이 파일의 허용 목록에 **이유와 함께** 한 줄 더할 것."
    );
}

/// 허용 목록이 낡지 않았나 — 정본이 뒤늦게 그 이름을 실었거나, 우리가 팔레트에서 지웠다면
/// 목록에서도 빠져야 한다. (고아 줄은 조용히 죽은 무게이면서 다음 사람을 속인다.)
#[test]
fn the_ours_only_allowlist_is_exact_and_sorted() {
    let fx = fixture();
    let names: Vec<&str> = PALETTE_OURS.iter().map(|(n, _)| *n).collect();
    let mut sorted = names.clone();
    sorted.sort_unstable();
    assert_eq!(names, sorted, "PALETTE_OURS 는 이름순이라야 한다");

    let in_palette: BTreeSet<&str> =
        PALETTE.iter().map(|e| e.name.split(' ').next().unwrap_or(e.name)).collect();
    for name in &names {
        assert!(
            in_palette.contains(name),
            "PALETTE 에 없는 이름이 허용 목록에 남아 있다: {name}"
        );
        assert!(
            !fx.command_cats.contains_key(*name),
            "정본이 이제 {name} 을 안다 — 허용 목록에서 빼고 정본 분류를 따를 것"
        );
    }
}

/// 팔레트 **탭 차례**가 정본 차례의 부분수열인가.
#[test]
fn palette_tabs_follow_the_canon_order() {
    let fx = fixture();
    let canon: Vec<&str> = fx.command_cat_order.iter().map(String::as_str).collect();
    let want: Vec<&str> =
        canon.iter().copied().filter(|c| PALETTE_CATS.contains(c)).collect();
    assert_eq!(
        PALETTE_CATS, &want[..],
        "PALETTE_CATS 는 정본 등장 순서에서 우리가 가진 것만 남긴 것이라야 한다"
    );

    // 그리고 그 탭들이 팔레트를 **다 덮어야** 한다 — 어느 탭에도 안 잡히는 줄이 있으면
    // `전체` 탭에만 있고 카테고리 탭에서는 영영 안 보인다.
    let uncovered: Vec<&str> =
        PALETTE.iter().filter(|e| !PALETTE_CATS.contains(&e.cat)).map(|e| e.name).collect();
    assert!(uncovered.is_empty(), "어느 탭에도 안 잡히는 팔레트 줄: {uncovered:?}");
}

/// 정본이 **가질 수 없는** 설정 줄과 그 이유.
///
/// # 왜 예외가 생겼나 (2026-08-02, §10-21ⓐ)
///
/// 종전 이 자리의 규칙은 *"설정은 팔레트와 달리 우리 전용이 없다 — 정본에 없는 설정을
/// 실으면 그것을 바꿔도 정본 클라와 값이 어긋난다(같은 서버에 함께 붙는 구조다)"* 였다.
/// 그 근거는 **두 클라가 같은 값을 다르게 읽을 때** 성립한다. `font-scale` 은 그 부류가
/// 아니다 — 정본은 이 값을 **읽지도 쓰지도 않는다**(터미널 앱의 글자 크기는 호스트
/// 단말의 것이라 저쪽에 그 줄이 있을 수가 없다). 어긋날 값이 없으니 근거가 안 닿는다.
///
/// 그래도 목록으로 **박아 두는** 이유는 `PALETTE_OURS` 와 같다: 조용히 늘면 안 된다.
/// 새 줄을 여기 적을 때는 "정본이 안 가진 것"이 아니라 **"정본이 가질 수 없는 것"**임을
/// 보여야 한다 — 전자는 그냥 우리가 아직 안 단 줄이고, 그건 예외가 아니라 할 일이다.
static SETTINGS_OURS: &[(&str, &str)] = &[(
    "font-scale",
    "정본의 글자 크기는 호스트 단말이 정한다 — 저쪽에 짝이 있을 수 없다(§10-21ⓐ)",
)];

/// 설정 한 줄 한 줄이 정본과 같은 카테고리인가.
#[test]
fn every_setting_carries_the_canon_category() {
    let fx = fixture();
    let mut wrong = Vec::new();
    for setting in SETTINGS {
        match fx.setting_cats.get(setting.key) {
            Some(canon) if canon == setting.cat => {}
            Some(canon) => {
                wrong.push(format!("{}: 우리 {} · 정본 {canon}", setting.key, setting.cat))
            }
            None if SETTINGS_OURS.iter().any(|(k, _)| *k == setting.key) => {}
            None => wrong.push(format!("{}: 정본에 없는 설정", setting.key)),
        }
    }
    assert!(wrong.is_empty(), "정본과 다른 분류의 설정 줄:\n  {}", wrong.join("\n  "));
    // 목록이 낡지 않게 — 지운 줄의 예외가 남아 있으면 그것도 자국이다.
    let keys: BTreeSet<&str> = SETTINGS.iter().map(|s| s.key).collect();
    let stale: Vec<&str> =
        SETTINGS_OURS.iter().map(|(k, _)| *k).filter(|k| !keys.contains(k)).collect();
    assert!(stale.is_empty(), "설정 표에 없는 줄의 예외가 남아 있다: {stale:?}");
}

/// 설정 **사이드바 차례**가 정본 차례의 부분수열이고, 모든 설정을 덮나.
#[test]
fn settings_sidebar_follows_the_canon_order() {
    let fx = fixture();
    let want: Vec<&str> = fx
        .settings_cat_order
        .iter()
        .map(String::as_str)
        .filter(|c| SETTINGS_CATS.contains(c))
        .collect();
    assert_eq!(
        SETTINGS_CATS, &want[..],
        "SETTINGS_CATS 는 정본 SETTINGS_CATS 에서 우리가 가진 것만 남긴 것이라야 한다"
    );

    let uncovered: Vec<&str> =
        SETTINGS.iter().filter(|s| !SETTINGS_CATS.contains(&s.cat)).map(|s| s.key).collect();
    assert!(uncovered.is_empty(), "어느 탭에도 안 잡히는 설정 줄: {uncovered:?}");
}

/// 메뉴 줄의 **키와 문구**가 정본 것인가.
#[test]
fn every_menu_entry_matches_the_canon_item() {
    let fx = fixture();
    let mut wrong = Vec::new();
    for entry in MENU {
        // 플러그인이 낸 줄은 `MENU_ITEMS` 가 아니라 그 플러그인이 문구의 주인이다
        // (`i18n.register({"menu.calendar-mode": …})`) — 두 표를 다 본다.
        let canon = fx.menu_labels.get(entry.key).or_else(|| fx.plugin_menu_labels.get(entry.key));
        match canon {
            Some(label) if label == entry.label => {}
            Some(label) => wrong.push(format!("{}: 우리 {:?} · 정본 {:?}", entry.key, entry.label, label)),
            None => wrong.push(format!("{}: 정본 MENU_ITEMS 에도 플러그인 기여에도 없는 키", entry.key)),
        }
    }
    assert!(wrong.is_empty(), "정본과 다른 메뉴 줄:\n  {}", wrong.join("\n  "));

    // 키는 계층 표가 항목을 부르는 이름이다 — 겹치면 엉뚱한 줄이 그룹에 들어간다.
    let keys: BTreeSet<&str> = MENU.iter().map(|e| e.key).collect();
    assert_eq!(keys.len(), MENU.len(), "메뉴 키가 겹친다");
}

/// 메뉴 **계층**(그룹 멤버·최상위 차례·토글)이 정본 것인가.
#[test]
fn the_menu_hierarchy_is_the_canon_one() {
    let fx = fixture();
    let have: BTreeSet<&str> = MENU.iter().map(|e| e.key).collect();

    for (group, keys) in MENU_GROUPS {
        let canon = fx
            .menu_groups
            .get(*group)
            .unwrap_or_else(|| panic!("정본에 없는 메뉴 그룹: {group}"));
        // 우리가 못 하는 줄은 빼도 된다 — 다만 **차례를 바꾸거나 없는 줄을 넣을 수는 없다**.
        let want: Vec<&str> =
            canon.iter().map(String::as_str).filter(|k| have.contains(k)).collect();
        assert_eq!(*keys, &want[..], "메뉴 그룹 {group} 의 멤버·차례가 정본과 다르다");
    }

    let want_top: Vec<String> = fx
        .menu_toplevel
        .iter()
        .filter(|slot| {
            slot.strip_prefix("group:")
                .map(|g| fx.menu_groups.contains_key(g))
                .unwrap_or(*slot == "--" || have.contains(slot.as_str()))
        })
        .cloned()
        .collect();
    let ours: Vec<String> = MENU_TOPLEVEL
        .iter()
        .map(|top| match top {
            MenuTop::Group(g) => format!("group:{g}"),
            MenuTop::Separator => "--".to_string(),
            MenuTop::Item(k) => (*k).to_string(),
        })
        .collect();
    assert_eq!(ours, want_top, "메뉴 최상위 차례가 정본과 다르다");

    let want_toggles: Vec<&str> =
        fx.menu_toggles.iter().map(String::as_str).filter(|k| have.contains(k)).collect();
    assert_eq!(MENU_TOGGLES, &want_toggles[..], "토글 항목 목록이 정본과 다르다");

    for (group, label) in MENU_GROUP_LABELS {
        let canon = fx
            .menu_group_labels
            .get(*group)
            .unwrap_or_else(|| panic!("정본에 없는 메뉴 그룹 라벨: {group}"));
        assert_eq!(label, canon, "메뉴 그룹 {group} 의 문구가 정본과 다르다");
    }
}

/// 계층이 **모든 줄을 덮나** — 그룹에도 최상위에도 없는 항목은 화면에서 도달할 길이 없다.
///
/// 이 단언이 없으면 계층화가 조용히 기능을 지운다: 표에는 31줄이 남아 있고 테스트도 전부
/// 초록인데, 실제 메뉴에서는 그 줄이 안 보인다.
#[test]
fn the_hierarchy_reaches_every_menu_entry() {
    // ★ 정적 표가 아니라 **화면이 실제로 그리는 줄**(`menu_rows`)로 잰다. 플러그인 그룹은
    //   정적 표에 없고 런타임에 끼워지므로, 표만 훑으면 그 두 줄이 "도달 불가"로 잡힌다.
    let plugins = surface(&fixture()).menu_items.len();
    let mut reachable: BTreeSet<&str> = BTreeSet::new();
    for row in menu_rows(None, plugins) {
        match row {
            MenuRow::Item(entry) => {
                reachable.insert(entry.key);
            }
            MenuRow::Group(g) => {
                let members = menu_rows(Some(g), plugins);
                assert!(!members.is_empty(), "빈 서브메뉴 진입점: {g}");
                for row in members {
                    if let MenuRow::Item(entry) = row {
                        reachable.insert(entry.key);
                    }
                }
            }
            MenuRow::Plugin(_) | MenuRow::Separator => {}
        }
    }
    let orphans: Vec<&str> =
        MENU.iter().map(|e| e.key).filter(|k| !reachable.contains(k)).collect();
    assert!(orphans.is_empty(), "계층 어디에서도 못 여는 메뉴 줄: {orphans:?}");
}

/// 정본 `MENU_ITEMS` 의 **차례**가 우리 `MENU` 의 차례와 같은가(계층이 아니라 표 자체).
#[test]
fn the_menu_table_keeps_the_canon_order() {
    let fx = fixture();
    let have: BTreeSet<&str> = MENU.iter().map(|e| e.key).collect();
    let want: Vec<&str> =
        fx.menu_order.iter().map(String::as_str).filter(|k| have.contains(k)).collect();
    let ours: Vec<&str> = MENU.iter().map(|e| e.key).collect();
    assert_eq!(ours, want, "MENU 의 차례가 정본 MENU_ITEMS 와 다르다");

    // ★ 그리고 **플러그인 줄은 이 표에 없다**(P2). 손으로 옮겨 적으면 서버가 그 플러그인을
    //   안 실어도 화면에 남고(delete-to-disable 이 우리 쪽에서만 거짓), 새 플러그인이 낸
    //   줄은 영영 안 뜬다. 줄의 출처는 서버가 부는 표면 한 곳이다.
    let migrated: Vec<&str> = fx
        .plugin_menu_order
        .iter()
        .map(String::as_str)
        .filter(|k| have.contains(k))
        .collect();
    assert!(
        migrated.is_empty(),
        "플러그인이 낸 메뉴 줄이 정적 표에 손으로 옮겨져 있다: {migrated:?}\n\
         → 서버 표면(`plugin_surface.menu_items`)이 그 줄의 유일한 출처라야 한다."
    );
}

/// 플러그인 그룹이 정본이 끼우는 **그 자리**에 뜨나(정본 `_toplevel_entries`).
///
/// 자리가 중요한 이유: 최상위는 눈이 외운 목록이다. 끝에 붙이면 파괴적 동작
/// (`detach`·`kill_server`)을 격리한 구분선 **뒤**로 가서, 그 구분선의 뜻이 흐려진다.
#[test]
fn the_plugin_group_sits_where_canon_puts_it() {
    let fx = fixture();
    let plugins = surface(&fx);
    assert_eq!(fx.plugin_menu_after, "group:tab", "정본이 끼우는 자리가 바뀌었다");
    assert!(!plugins.menu_items.is_empty(), "플러그인 줄이 비었다 — 통과가 아니라 고장이다");

    let rows = menu_rows(None, plugins.menu_items.len());
    let at = rows
        .iter()
        .position(|r| matches!(r, MenuRow::Group("tab")))
        .expect("최상위에 `탭 ▸` 가 없다");
    assert!(
        matches!(rows.get(at + 1), Some(MenuRow::Group("plugin"))),
        "`탭 ▸` 바로 뒤가 플러그인 그룹이 아니다: {:?}",
        rows.get(at + 1)
    );

    // 서브메뉴의 멤버와 차례는 정본 `plugins.menu_items` 그대로다 — **서버가 준 줄**을
    // 자리로 가리키므로, 라벨까지 서버 것인지 함께 본다(정적 표에 옮겨 적으면 플러그인이
    // 문구를 고쳐도 우리는 옛 글을 보인다).
    let members: Vec<(&str, &str)> = menu_rows(Some("plugin"), plugins.menu_items.len())
        .into_iter()
        .filter_map(|r| match r {
            MenuRow::Plugin(i) => plugins
                .menu_items
                .get(i)
                .map(|item| (item.key.as_str(), item.label.as_str())),
            _ => None,
        })
        .collect();
    let want: Vec<(&str, &str)> = fx
        .plugin_menu_order
        .iter()
        .map(|k| (k.as_str(), fx.plugin_menu_labels[k].as_str()))
        .collect();
    assert_eq!(members, want, "플러그인 서브메뉴의 멤버·차례·문구가 정본과 다르다");
}

/// ★ **delete-to-disable 이 화면에서도 먹나** — 서버가 그 플러그인을 안 실으면 그룹 자체가
/// 사라져야 한다.
///
/// 이게 P2 의 핵심 단언이다. 종전에는 줄이 정적 표라, 정본에서는 시계 플러그인을 지우면
/// 메뉴 줄이 사라지는데 우리 화면에는 그대로 남았다 — "코어는 플러그인을 직접 모른다"는
/// 계약이 **우리 쪽에서만 거짓**이었고, 사용자에게는 눌러도 아무 일 없는 줄로 보였다.
#[test]
fn an_empty_surface_removes_the_plugin_group_entirely() {
    let rows = menu_rows(None, 0);
    assert!(
        !rows.iter().any(|r| matches!(r, MenuRow::Group("plugin"))),
        "플러그인 기여가 하나도 없는데 `플러그인 ▸` 진입점이 남았다"
    );
    assert!(
        menu_rows(Some("plugin"), 0).iter().all(|r| !matches!(r, MenuRow::Plugin(_))),
        "빈 표면인데 플러그인 서브메뉴에 줄이 있다"
    );
    // 그리고 최상위는 **그것 말고는 그대로**다(그룹 하나가 빠졌을 뿐).
    let with = menu_rows(None, 2).len();
    assert_eq!(with, rows.len() + 1, "플러그인 그룹 말고 다른 줄까지 달라졌다");
}

/// 서버가 부는 명령이 **팔레트에 빠짐없이, 두 번 서지 않고** 오르나.
///
/// 두 번 서는 것이 실제로 났다(P1): `clock-mode`·`calendar-mode`·`auto-resume`·
/// `prompt-clear` 는 우리가 네이티브로 든 이름인데 서버 목록에도 있어, 같은 이름 두 줄 중
/// **하나만 동작하는** 팔레트가 됐다. 이 오라클은 그 부류를 이름 단위로 잡는다.
#[test]
fn every_plugin_command_reaches_the_palette_exactly_once() {
    let fx = fixture();
    let plugins = surface(&fx);
    assert!(!plugins.commands.is_empty(), "플러그인 명령이 비었다 — 통과가 아니라 고장이다");

    // 코어 줄의 이름. 기본형으로도 모은다 — 코어 표에는 플래그 변종이 있어
    // (`split-window -h`·`-v`) 그 둘은 같은 기본형을 갖는 **정상**이고, 우리가 잡으려는
    // 것은 코어와 플러그인 사이의 겹침이다.
    let core: BTreeSet<&str> = PALETTE
        .iter()
        .flat_map(|e| [e.name, e.name.split(' ').next().unwrap_or(e.name)])
        .collect();

    // `전체` 탭(필터 없음)에서 실제로 그려질 플러그인 줄.
    let shown: Vec<&str> = plugins
        .palette_rows(None, "")
        .into_iter()
        .map(|i| plugins.commands[i].name.as_str())
        .collect();

    let clash: Vec<&str> = shown.iter().copied().filter(|n| core.contains(n)).collect();
    assert!(
        clash.is_empty(),
        "코어 줄과 같은 이름이 플러그인 줄로 또 선다: {clash:?}\n\
         → 그 이름은 우리가 네이티브로 실행하므로 두 줄 중 하나만 동작한다."
    );

    let mut dup: Vec<&str> = Vec::new();
    let mut seen: BTreeSet<&str> = BTreeSet::new();
    for name in &shown {
        if !seen.insert(name) {
            dup.push(name);
        }
    }
    assert!(dup.is_empty(), "플러그인 목록 안에서 두 번 서는 이름: {dup:?}");

    // 빠짐없이: 서버가 부른 이름은 **코어에 있거나(네이티브) 플러그인 줄로 선다**.
    let missing: Vec<&str> = plugins
        .commands
        .iter()
        .map(|c| c.name.as_str())
        .filter(|n| !seen.contains(n) && !core.contains(n))
        .collect();
    assert!(missing.is_empty(), "서버가 불렀는데 팔레트 어디에도 안 서는 명령: {missing:?}");
}

/// 우리가 **손으로 옮겨 든** 플러그인 명령 — 곧 Tier D(클라 네이티브 어댑터) 목록이다.
///
/// 설계 §4.4·§8-4 의 래칫이 여기다: 이 목록이 늘면 같은 CL 에서 이 상수를 옮기게 한다.
/// 이유 없이 느는 목록은 "선언형이 어렵다"의 다른 이름이고, 늘어난 만큼
/// delete-to-disable 이 우리 쪽에서 거짓이 된다(서버가 그 플러그인을 안 실어도 우리는
/// 그 이름을 계속 실행한다).
static NATIVE_PLUGIN_COMMANDS: &[&str] = &[
    // 상태줄 토글 둘 — 서버 상태(`flags`)로 값이 오고 우리가 명령을 보낸다.
    "auto-resume",
    // ★ 서버가 **이미 받고 있던** 플러그인 토글 다섯(pytmux-35). 왜 서버가 못 하나 —
    //   못 하는 게 아니다. 서버는 처음부터 이 액션들을 받는다(`set_claude_auto_retry` 등,
    //   정본 훅이 치는 그 이름 그대로). 갈린 것은 **"팔레트 이름 → 서버 액션"을 아는
    //   자리**이고, 정본은 그것을 파이썬 클라 훅에 뒀다 — 그 훅은 파이썬 객체를 주고받아
    //   **소켓을 못 건넌다**(M4 §7 이 선언형으로 바꾸려는 바로 그것). 그때까지는 이 표가
    //   우리 몫이다. 종전에는 이 다섯이 `plugin_open` 으로 가서 "화면 스펙 없음"으로
    //   거절당했다 = 팔레트에는 보이는데 눌러도 안 먹는 줄.
    //   ⚠ **인자 없이 뜻이 온전한 것만** 넣었다. `claude-auto-redraw`(3-state)·
    //   `claude-token-account`(이름 인자) 등은 팔레트가 인자를 못 받는 동안(pytmux-7)
    //   반쪽이라 아직 죽은 목록에 남는다.
    "auto-retry",
    "auto-token-on-exit",
    // 패널 오버레이 셋 — 그림은 서버가 `plugin_cells` 로 준다(P3·2026-08-02e·f).
    // 우리가 네이티브로 드는 것은 **켠 사실**뿐이다: 어느 패널에 오버레이가 떴는지는
    // 그 클라만 아는 상태라 서버가 대신 정할 수 없다(설계 §4.4 `client_fact`).
    "calendar-mode",
    "claude-auto-mode",
    "claude-token-debug",
    // 토글이 아니라 **한 번 시키는** 것이다(서버가 숨은 claude 로 /usage 를 긁는다).
    "claude-usage",
    "clock-mode",
    // ★ 같은 오버레이의 **명시적 켜기/끄기**(§10-21ⓡ · 제보 "`close-clock`·
    // `close-calendar` 가 안 먹는다"). 왜 서버가 못 하나: 위 토글과 **같은 이유**다 —
    // 어느 패널에 오버레이가 떠 있는지는 그 클라만 아는 상태이고, 정본의 계약(켜기는
    // 멱등 · 대상은 활성 패널 · 시계와 달력은 상호 배타)도 그 상태 위에서만 성립한다.
    // 종전에는 이 넷이 목록에 없어 **플러그인 줄로 남았고**, 고르면 서버에 "화면을
    // 다오"로 가서 *"이 플러그인은 화면 스펙을 제공하지 않습니다"* 로 거절당했다 —
    // 팔레트에는 보이는데 눌러도 안 먹는 줄이었다.
    "close-calendar",
    "close-clock",
    "open-calendar",
    "open-clock",
    "prompt-clear",
    // ⚠ `usage-view` 는 정본에서 **세 모드**(popup·tab·pane)인데 우리 것은
    // `pane`(오버레이) 하나다 — 나머지 둘은 Textual 화면이라 짝이 없다. 위 둘과 같은
    // 이유로 네이티브(켠 사실은 클라의 것)이고, 팝업/탭이 선언형 화면으로 오면 그때
    // 갈래를 나눈다. 종전에는 이 이름이 "화면이 없다" 알림으로 끝났다.
    "usage-view",
];

#[test]
fn the_native_adapter_list_is_exact_and_sorted() {
    let fx = fixture();
    let plugins = surface(&fx);
    let core: BTreeSet<&str> = PALETTE
        .iter()
        .map(|e| e.name.split(' ').next().unwrap_or(e.name))
        .collect();
    let ours: Vec<&str> = plugins
        .commands
        .iter()
        .map(|c| c.name.as_str())
        .filter(|n| core.contains(n))
        .collect();
    let mut sorted = NATIVE_PLUGIN_COMMANDS.to_vec();
    sorted.sort_unstable();
    assert_eq!(NATIVE_PLUGIN_COMMANDS, &sorted[..], "목록은 이름순이라야 한다");
    let mut have = ours.clone();
    have.sort_unstable();
    have.dedup();
    assert_eq!(
        have,
        NATIVE_PLUGIN_COMMANDS.to_vec(),
        "네이티브로 든 플러그인 명령이 달라졌다 — 늘었다면 **왜 서버가 못 하는지**를 적고\n\
         이 목록을 같은 CL 에서 옮길 것(설계 §4.4). 줄었다면 여기서도 빼야 한다."
    );
}

/// 플러그인이 낸 **분류에도 탭이 있나** — 없으면 그 명령들은 `전체` 탭에서만 보인다.
#[test]
fn plugin_categories_get_their_own_tab() {
    let fx = fixture();
    let plugins = surface(&fx);
    let cats = plugins.palette_cats();

    // 코어 탭이 앞에 그대로 있고(눈이 외운 차례), 새 분류가 뒤에 붙는다.
    assert_eq!(&cats[..PALETTE_CATS.len()], PALETTE_CATS, "코어 탭 차례가 흔들렸다");

    let uncovered: Vec<&str> = plugins
        .palette_rows(None, "")
        .into_iter()
        .map(|i| plugins.commands[i].cat.as_str())
        .filter(|cat| !cats.contains(cat))
        .collect();
    assert!(uncovered.is_empty(), "어느 탭에도 안 잡히는 플러그인 분류: {uncovered:?}");

    // 정본 차례의 **부분수열**이라야 한다(탭 차례는 눈이 외우는 것이라 정본을 따른다).
    let canon: Vec<&str> = fx.command_cat_order.iter().map(String::as_str).collect();
    let want: Vec<&str> = canon.into_iter().filter(|c| cats.contains(c)).collect();
    assert_eq!(cats, want, "팔레트 탭 차례가 정본 등장 순서와 다르다");
}

/// 플러그인이 낸 **설정 줄과 분류**가 설정 화면에 서나.
///
/// 종전에는 `Claude` 카테고리가 통째로 없었고(사이드바 주석이 "우리는 아직 그 설정을 안
/// 싣는다"고 적어 두었다), 그 줄들은 화면 어디에도 없었다.
#[test]
fn plugin_settings_and_their_category_show_up() {
    let fx = fixture();
    let plugins = surface(&fx);
    assert!(!plugins.settings.is_empty(), "플러그인 설정이 비었다 — 통과가 아니라 고장이다");

    let rows = plugins.settings_rows();
    assert_eq!(rows.len(), SETTINGS.len() + plugins.settings.len());

    // 줄마다 자리를 되찾을 수 있어야 한다(화면·키·사이드바가 같은 번호를 쓴다).
    for s in &plugins.settings {
        let at = (0..plugins.settings_len())
            .find(|row| plugins.setting_at(*row).is_some_and(|r| r.key() == s.key))
            .unwrap_or_else(|| panic!("설정 화면에 안 서는 플러그인 줄: {}", s.key));
        assert_eq!(
            plugins.setting_cat_of(at),
            plugins.setting_cats().iter().position(|c| *c == s.cat),
            "{} 의 분류가 사이드바 번호와 안 맞는다",
            s.key
        );
    }

    // 사이드바: 코어 분류가 앞에 그대로, 플러그인 분류가 뒤에.
    let cats = plugins.setting_cats();
    assert_eq!(&cats[..SETTINGS_CATS.len()], SETTINGS_CATS, "코어 사이드바 차례가 흔들렸다");
    for cat in &fx.plugin_setting_cats {
        assert!(cats.contains(&cat.as_str()), "플러그인 분류가 사이드바에 없다: {cat}");
    }

    // 그리고 그 차례가 정본 차례의 부분수열인가.
    let want: Vec<&str> = fx
        .settings_cat_order
        .iter()
        .map(String::as_str)
        .filter(|c| cats.contains(c))
        .collect();
    assert_eq!(cats, want, "설정 사이드바 차례가 정본과 다르다");

    // 줄의 차례도 정본 `settings_order` 그대로다(코어 뒤에 플러그인).
    let ours: Vec<&str> = (0..plugins.settings_len())
        .filter_map(|row| plugins.setting_at(row).map(|r| r.key().to_owned()))
        // 정본이 가질 수 없는 줄은 저쪽 차례에 자리가 없다 — 뺀다(`SETTINGS_OURS`).
        .filter(|k| !SETTINGS_OURS.iter().any(|(ours, _)| ours == k))
        .map(|k| {
            fx.settings_order
                .iter()
                .find(|c| **c == k)
                .map(String::as_str)
                .unwrap_or("(정본에 없음)")
        })
        .collect();
    let have: BTreeSet<&str> = ours.iter().copied().collect();
    let want_rows: Vec<&str> = fx
        .settings_order
        .iter()
        .map(String::as_str)
        .filter(|k| have.contains(k))
        .collect();
    assert_eq!(ours, want_rows, "설정 줄 차례가 정본과 다르다");
}

/// 설정 줄의 **카테고리 안 차례**가 정본 것인가.
///
/// 왜 따로 재나: `setting_cats` 는 키로 정렬한 **사전**이라 차례를 안 담는다. 화면은
/// `SETTINGS` 를 위에서 아래로 그리므로 눈이 외운 자리가 차례에 달려 있는데, 3차 대조
/// (2026-08-01)까지 아무 게이트도 그것을 안 재서 `표시` 안 차례가 조용히 갈라져 있었다.
#[test]
fn the_settings_table_keeps_the_canon_order() {
    let fx = fixture();
    let have: BTreeSet<&str> = SETTINGS.iter().map(|s| s.key).collect();
    // 우리가 아직 안 다는 줄은 빼도 된다 — 다만 **차례를 바꿀 수는 없다**.
    let want: Vec<&str> =
        fx.settings_order.iter().map(String::as_str).filter(|k| have.contains(k)).collect();
    // 정본이 **가질 수 없는** 줄은 정본 차례에 자리가 없다(`SETTINGS_OURS`). 그 줄만
    // 빼고 견준다 — 나머지의 차례는 여전히 정본 그대로라야 한다.
    let ours: Vec<&str> = SETTINGS
        .iter()
        .map(|s| s.key)
        .filter(|k| !SETTINGS_OURS.iter().any(|(ours, _)| ours == k))
        .collect();
    assert_eq!(ours, want, "설정 줄의 차례가 정본과 다르다");

    // 차례가 카테고리를 넘나들면 화면이 같은 머리줄을 두 번 찍는다(2026-07-29 실측).
    let mut seen: Vec<&str> = Vec::new();
    for setting in SETTINGS {
        if seen.last() != Some(&setting.cat) {
            assert!(!seen.contains(&setting.cat), "카테고리 {} 가 두 번 나온다", setting.cat);
            seen.push(setting.cat);
        }
    }
}

// ── 팔레트에 보이는데 안 먹는 명령 (§10-21ⓡ) ────────────────────────────────
//
// 제보는 `close-clock`·`close-calendar` 둘이었지만, 재 보니 **부류**였다. 네이티브
// 클라는 플러그인 명령을 전부 "서버야, 화면을 다오"(`plugin_open`)로 보낸다 — 화면이
// 아니라 **상태를 바꾸는** 명령에는 그 경로가 통째로 틀렸고, 서버는 *"이 플러그인은
// 화면 스펙을 제공하지 않습니다"* 로 거절한다. 사용자에게는 죽은 줄로 보인다.
//
// 그래서 눈으로 세지 않고 **정본에서 뽑아** 센다(`scripts/gen_plugin_client_cmds.py`):
// 광고된 이름마다 플러그인이 화면 스펙을 내는지 실제로 물어, 안 내는 것을 모은다.

#[derive(Deserialize)]
struct CmdKinds {
    advertised: Vec<String>,
    with_screen: Vec<String>,
    /// 서버가 **명령으로 실행**할 수 있는 이름(pytmux-35 · `plugin_cmd`).
    /// 화면 스펙과 겹칠 수 있다 — 겹치면 서버가 명령 쪽을 먼저 본다.
    #[serde(default)]
    server_runnable: Vec<String>,
    stateful: Vec<String>,
}

fn cmd_kinds() -> CmdKinds {
    let raw = include_str!("fixtures/plugin_client_cmds.json");
    serde_json::from_str(raw).expect("plugin_client_cmds.json 을 못 읽는다")
}

/// **아직 죽은 줄** — 팔레트에 뜨지만 고르면 아무 일도 안 난다.
///
/// 줄이는 것이 목표다. 이 목록이 **늘면** 게이트가 운다 — 새 플러그인 명령을 광고만 하고
/// 어느 클라도 못 하게 두는 것이 이 부류가 생긴 경위다.
///
/// 어떻게 줄이나(셋 중 하나):
/// - 플러그인이 **화면 스펙**을 내면(Tier C) `plugin_open` 경로가 그대로 산다 —
///   `claude-settings`·`model`·`namesync` 처럼 정본에서 팝업인 것들이 그렇게 나갔다.
/// - 상태를 바꾸는 것은 플러그인이 **`cmdmap`** 을 내면 서버가 받는다(pytmux-35) —
///   `capture-output`·`auto-launch`·`prompt-history-lines`·`ime-indicator` 가 그 길이다.
/// - 그래도 서버가 못 하는 것만 **네이티브 어댑터**로 든다(위 `NATIVE_PLUGIN_COMMANDS`) —
///   그 목록도 래칫이라 "왜 서버가 못 하나"를 적어야 한다.
///
/// ★ **2026-08-04 에 0 이 됐다**(pytmux-35 종결 · 23 → 18 → 17 → 11 → 0). 남아 있던
///   열하나가 마지막 CL 에서 나갔다:
///   - `cmdmap` — `capture-output`·`capture-toggle`(rec) · `prompt-history-lines`
///     (claude-prompt-history) · `auto-launch`(claude-code — **정본에서도 죽어 있었다**:
///     팔레트·CLI 토글표엔 있는데 `handle_command` 에 분기가 없었다) ·
///     `ime-indicator`(표시 여부를 서버 옵션으로 옮겨 두 클라가 한 값을 본다).
///   - **화면 스펙** — `claude-settings`(form) · `claude-rules`(prompt · 지금 규칙이
///     입력칸 초기값) · `model`(list) · `claude-token-log`(table · 일별 집계. 계층
///     타임라인·`[한도]` 탭은 EXT-0008) · `namesync`(table + 물음) ·
///     `prompt-clear-queue`(무인자면 목록, 인자가 있으면 액션 — 갈래는 `cmdmap` 이 정한다).
///
/// 이 목록이 **비었다고 이 오라클이 공허해지지는 않는다**: 계산한 `dead` 가 하나라도
/// 생기면 그 순간 떨어진다(빈 목록 == 빈 목록이 아니라 `dead == []` 를 잰다). 픽스처가
/// 헛돌아 빈 것은 `the_command_kinds_fixture_actually_measured_something` 이 막는다.
static DEAD_PLUGIN_COMMANDS: &[&str] = &[];

#[test]
fn the_dead_command_list_does_not_grow() {
    let kinds = cmd_kinds();
    let core: BTreeSet<&str> = PALETTE
        .iter()
        .map(|e| e.name.split(' ').next().unwrap_or(e.name))
        .collect();
    // ★ **서버가 명령으로 실행할 수 있는 이름**도 산 것이다(pytmux-35 · `plugin_cmd`).
    //   우리는 갈래를 안 정하고 이름만 보내며, 서버가 상태형이면 거기서 처리하고 아니면
    //   화면 경로로 넘어간다. 이 칸이 없으면 살아난 명령이 여전히 죽은 것으로 세어진다.
    let runnable: BTreeSet<&str> = kinds.server_runnable.iter().map(String::as_str).collect();
    let dead: Vec<&str> = kinds
        .stateful
        .iter()
        .map(String::as_str)
        .filter(|n| !core.contains(n) && !runnable.contains(n))
        .collect();
    let mut sorted = DEAD_PLUGIN_COMMANDS.to_vec();
    sorted.sort_unstable();
    assert_eq!(DEAD_PLUGIN_COMMANDS, &sorted[..], "목록은 이름순이라야 한다");
    assert_eq!(
        dead, DEAD_PLUGIN_COMMANDS,
        "팔레트에 보이는데 안 먹는 명령이 달라졌다. **늘었다면** 그 이름을 어느 쪽으로든\n\
         살리고(화면 스펙 또는 네이티브 어댑터) 목록에서 뺄 것 — 늘려 놓고 통과시키면\n\
         이 부류가 다시 자란다. **줄었다면** 같은 CL 에서 이 목록도 줄일 것."
    );
}

#[test]
fn the_command_kinds_fixture_actually_measured_something() {
    // ★ 이 오라클이 먼저다. 픽스처가 비면 위 단언은 "빈 목록 == 빈 목록"이 되어
    //   **무엇을 해도 통과한다**(이 저장소가 여러 번 밟은 공허함).
    let kinds = cmd_kinds();
    assert!(kinds.advertised.len() >= 30, "광고 목록이 너무 적다 — 생성기가 헛돌았다");
    assert!(!kinds.with_screen.is_empty(), "화면 스펙이 하나도 없다 — 생성기가 헛돌았다");
    assert_eq!(
        kinds.advertised.len(),
        kinds.with_screen.len() + kinds.stateful.len(),
        "광고 = 화면 + 상태형 이라야 한다(생성기가 이름을 흘렸다)"
    );
}

#[test]
fn the_reported_two_commands_are_alive_now() {
    // 제보 그 자체(§10-21ⓡ) — 이 넷은 이제 코어 표가 든다.
    let core: BTreeSet<&str> = PALETTE
        .iter()
        .map(|e| e.name.split(' ').next().unwrap_or(e.name))
        .collect();
    for name in ["open-clock", "close-clock", "open-calendar", "close-calendar"] {
        assert!(core.contains(name), "{name} 이 코어 표에 없다 — 다시 죽은 줄이 된다");
    }
}

// ── 우리가 치는 플러그인 액션은 서버가 받는 것이라야 한다 (pytmux-35) ──────────
//
// 죽은 명령을 살리는 길은 **서버가 이미 받는 액션 이름을 그대로 치는 것**이다. 그런데 그
// 이름은 정본 파이썬에만 있고 우리 표는 손으로 적는다 — 한 글자만 달라도 명령은 **조용히
// 아무 일도 안 한다.** 종전(거절 알림)보다 **더 조용해진다**: 팔레트에서 사라지지도 않고
// (코어 표에 있으니) 아무 반응도 없다. 그러면 `the_dead_command_list_does_not_grow` 는
// 초록인데 사용자에게는 여전히 죽은 줄이다.
//
// 그래서 이름을 눈으로 옮기지 않고 정본에게 물어 고정한다
// (`scripts/gen_plugin_server_actions.py` 가 `server_command` 를 이름마다 실제로 부른다).

#[derive(serde::Deserialize)]
struct ServerActions {
    actions: Vec<String>,
}

fn server_actions() -> ServerActions {
    serde_json::from_str(include_str!("fixtures/plugin_server_actions.json"))
        .expect("픽스처를 읽을 수 없다")
}

/// 팔레트가 서버로 직접 치는 플러그인 액션 이름들.
fn our_plugin_actions() -> Vec<&'static str> {
    use base::Action;
    base::keymap::PALETTE
        .iter()
        .filter_map(|e| match e.action {
            Action::PluginToggle { action } | Action::PluginDo { action } => Some(action),
            _ => None,
        })
        .collect()
}

#[test]
fn the_server_action_fixture_actually_measured_something() {
    // ★ 이 오라클이 먼저다. 픽스처가 비면 아래 단언은 "무엇이든 통과"가 된다.
    let fx = server_actions();
    assert!(
        fx.actions.len() >= 10,
        "서버 액션 픽스처가 너무 작다({}) — 생성기가 정본에게 못 물었을 수 있다",
        fx.actions.len()
    );
    assert!(fx.actions.iter().any(|a| a == "set_autoresume"), "{:?}", fx.actions);
}

#[test]
fn every_plugin_action_we_send_is_one_the_server_accepts() {
    let fx = server_actions();
    let known: BTreeSet<&str> = fx.actions.iter().map(String::as_str).collect();
    let ours = our_plugin_actions();
    assert!(!ours.is_empty(), "플러그인 액션을 하나도 안 친다 — 이 오라클이 공허하다");
    let unknown: Vec<&str> = ours
        .iter()
        .copied()
        .filter(|a| !known.contains(a))
        .collect();
    assert!(
        unknown.is_empty(),
        "서버가 안 받는 액션을 친다: {unknown:?}\n  \
         → 그 명령은 팔레트에 보이지만 **아무 일도 안 한다**(거절 알림조차 없다).\n  \
         정본 훅이 치는 이름을 확인하고(`plugins/*/__init__.py` 의 `send_cmd`) 표를 고칠 것."
    );
}

#[test]
fn a_command_that_needs_an_argument_is_not_wired_as_a_bare_toggle() {
    // ⚠ 인자를 파싱해야 뜻이 온전한 명령(3-state·이름 인자)은 무인자 토글로 걸면 **반쪽**이
    //    된다 — 팔레트가 인자를 못 받는 동안(pytmux-7) 죽은 목록에 남는 편이 정직하다.
    //    그것들이 슬쩍 넘어오는 것을 막는다.
    const NEEDS_ARG: &[&str] = &[
        "set_claude_auto_redraw",   // corruption/idle/off 3-state
        "set_claude_resume_verify", // 검증 모드 인자
        "set_claude_account",       // 계정 이름
        "set_prompt_clear_message", // 메시지 문자열
        "set_autoresume",           // 팔레트의 `auto-resume-message` 는 msg= 를 싣는다
        "token_sync",               // 하위 명령(sub) + 인자
    ];
    let ours = our_plugin_actions();
    let leaked: Vec<&str> = ours
        .iter()
        .copied()
        .filter(|a| NEEDS_ARG.contains(a))
        .collect();
    assert!(
        leaked.is_empty(),
        "인자가 필요한 액션을 무인자로 걸었다: {leaked:?} — 반쪽으로 사는 명령이 된다"
    );
}
