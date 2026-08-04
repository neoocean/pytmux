//! 탭 해석 오라클.
//!
//! 표본은 서버가 실제로 만드는 모양(`serverio.py::_status_msg` +
//! `serverremote.py::_remote_tabs`)을 그대로 옮긴 것이다.

use super::*;
use crate::message::{ServerMessage, Status};

/// 로컬 탭 2개 + 원격 탭 1개가 섞인 status. 서버가 만드는 순서(로컬 먼저)를 지킨다.
fn status_json() -> &'static str {
    r#"{"t":"status","session":"main","boot_id":"boot-abc",
        "active_pane":4,"zoomed":false,"sync":false,"pane_title":"zsh",
        "windows":[
          {"index":0,"name":"편집","wid":11,"active":false,"bell":false,
           "activity":true,"claude_done":false,"pinned":true},
          {"index":1,"name":"빌드","wid":12,"active":true,"bell":true,
           "activity":false,"claude_done":true,"pinned":false},
          {"index":2,"name":"⇄build-box:테스트","active":false,"remote":true,
           "pinned":false,"bell":false,"activity":false,"claude_done":false}
        ]}"#
}

fn bar() -> TabBar {
    let msg: ServerMessage = serde_json::from_str(status_json()).unwrap();
    let ServerMessage::Status(status) = msg else {
        panic!("status 로 안 갈렸다");
    };
    TabBar::from_status(&status)
}

#[test]
fn reads_local_and_remote_tabs_from_one_list() {
    // 서버가 이미 합쳐서 준다 — 클라가 합치는 일은 없다.
    let bar = bar();
    assert_eq!(bar.tabs.len(), 3);
    assert_eq!(bar.local_count(), 2, "원격 탭은 항상 뒤에 붙는다");
    assert_eq!(bar.session, "main");
    assert_eq!(bar.boot_id.as_deref(), Some("boot-abc"));
    assert_eq!(bar.active_pane, Some(4));
    assert_eq!(bar.pane_title, "zsh");
}

#[test]
fn active_tab_is_the_one_the_server_marked() {
    assert_eq!(bar().active().map(|t| t.index), Some(1));
}

#[test]
fn remote_is_decided_by_the_flag_not_by_the_name() {
    // 이름을 파싱해 판정하면 사용자가 탭 이름에 ⇄ 를 넣는 순간 깨진다.
    let bar = bar();
    assert!(!bar.tabs[0].remote);
    assert!(!bar.tabs[1].remote);
    assert!(bar.tabs[2].remote);

    // 이름이 ⇄ 로 시작해도 플래그가 없으면 로컬이다.
    let local_with_arrow = Tab {
        name: "⇄사용자가 이렇게 지었다".into(),
        remote: false,
        ..Default::default()
    };
    assert_eq!(local_with_arrow.display().host, None);
    assert_eq!(local_with_arrow.display().name, "⇄사용자가 이렇게 지었다");
}

#[test]
fn remote_label_splits_host_from_name() {
    let bar = bar();
    let label = bar.tabs[2].display();
    assert_eq!(label.host, Some("build-box"));
    assert_eq!(label.name, "테스트");
}

#[test]
fn malformed_remote_name_still_shows_something() {
    // 파싱 실패가 탭을 사라지게 하면 안 된다.
    let odd = Tab {
        name: "형식이다르다".into(),
        remote: true,
        ..Default::default()
    };
    assert_eq!(odd.display().host, None);
    assert_eq!(odd.display().name, "형식이다르다");
}

#[test]
fn tab_key_prefers_wid_over_position() {
    // 위치는 탭이 닫히거나 순서가 바뀌면 어긋난다. 서버가 wid 를 도입한 이유다.
    let bar = bar();
    let with_wid = bar.tabs[0].key(bar.boot_id.as_deref());
    assert_eq!(with_wid, "boot-abc:11");

    // 구버전 상류는 wid 를 안 보낸다 → 위치 폴백.
    let no_wid = bar.tabs[2].key(bar.boot_id.as_deref());
    assert_eq!(no_wid, "boot-abc:#2");
}

#[test]
fn keys_are_namespaced_by_boot_id() {
    // 서버가 재시작하면 wid 가 1..N 으로 재발급된다. boot_id 로 감싸지 않으면
    // 옛 키가 새 탭에 오매칭된다(서버 주석이 경고하는 바로 그것).
    let tab = Tab {
        wid: Some(3),
        ..Default::default()
    };
    assert_ne!(tab.key(Some("boot-1")), tab.key(Some("boot-2")));
}

#[test]
fn tab_flags_survive_the_trip() {
    let bar = bar();
    assert!(bar.tabs[0].pinned && bar.tabs[0].activity);
    assert!(bar.tabs[1].bell && bar.tabs[1].claude_done);
}

#[test]
fn the_label_carries_every_mark_the_server_set() {
    // 라벨을 두 뷰가 각자 조립하던 것을 여기로 모았다(2026-07-28) — 각자 조립하면
    // **같은 탭이 화면마다 달라 보인다**(한쪽에만 종 표시가 빠지는 식). 그 어긋남은
    // 조용해서 둘을 나란히 놓고 봐야 안다.
    let bar = bar();
    let labels = bar.labels(crate::tabs::FULL_TITLE);
    let pinned = &labels[0];
    assert!(pinned.starts_with("* "), "핀이 앞에 안 붙었다: {pinned}");
    assert!(pinned.ends_with('#'), "활동 표식이 없다: {pinned}");

    // 벨·Claude 완료는 로컬 탭(index 1)에 서 있다. 벨은 활동을 이긴다(정본과 같다).
    let busy = &labels[1];
    assert!(busy.ends_with('!'), "벨이 없다: {busy}");
    assert!(busy.contains("✓ "), "Claude 완료 글리프가 없다: {busy}");

    // 원격 탭은 `⇄호스트:이름` 을 **다시 조립한다**(display 가 쪼갠 것을 되붙인다).
    // 이름을 그대로 쓰지 않는 이유는 `display` 문서에 있다 — 판정은 플래그로 하고
    // 이름은 꾸미기 위해서만 쪼갠다.
    assert!(labels[2].ends_with("⇄build-box:테스트"), "{}", labels[2]);
}

#[test]
fn the_visible_number_follows_the_visible_order_not_the_index() {
    // ★ 정본 기준(2026-07-31): 라벨은 `번호:이름` 이고 **번호는 시각 순서**다 —
    // 고정 탭은 오른쪽 구역으로 밀려 그려지므로 index+1 로 매기면 화면에서 읽은 번호와
    // `prefix 숫자` 가 어긋난다(정본 `_visual_tab_numbers` 가 있는 이유).
    let bar = bar();
    // 픽스처: [0]=고정, [1]=로컬, [2]=원격. 화면에는 비고정([1],[2])이 먼저 온다.
    assert_eq!(bar.visual_numbers(), vec![3, 1, 2], "시각 순서 번호가 아니다");
    let labels = bar.labels(crate::tabs::FULL_TITLE);
    // 번호 앞에는 핀·상태 글리프가 붙는다(정본 `{핀}{글리프}{번호}:{이름}{플래그}`) —
    // 그래서 "1번이다"는 `1:이름` 조각으로 잰다.
    assert!(labels[1].contains("1:빌드"), "첫 비고정 탭이 1번이 아니다: {}", labels[1]);
    assert!(labels[2].contains("2:"), "둘째 비고정 탭이 2번이 아니다: {}", labels[2]);
    assert!(labels[0].starts_with("* 3:"), "고정 탭이 뒤 번호가 아니다: {}", labels[0]);
}

#[test]
fn a_plain_tab_gets_no_decoration() {
    // 표식이 늘 붙으면 신호가 아니라 잡음이 된다.
    let tab = Tab {
        index: 0,
        name: "shell".into(),
        ..Tab::default()
    };
    assert_eq!(tab.label(1, crate::tabs::FULL_TITLE), "1:shell");
}

#[test]
fn missing_optional_fields_do_not_break_parsing() {
    // 플러그인이 없으면 status 에서 통째로 빠지는 키들이 있다(delete-to-disable).
    let msg: ServerMessage =
        serde_json::from_str(r#"{"t":"status","windows":[{"index":0}]}"#).unwrap();
    let ServerMessage::Status(status) = msg else {
        panic!()
    };
    let bar = TabBar::from_status(&status);
    assert_eq!(bar.tabs.len(), 1);
    assert_eq!(bar.tabs[0].name, "");
    assert_eq!(bar.boot_id, None);
    assert_eq!(bar.active_pane, None);
}

#[test]
fn status_without_windows_yields_an_empty_bar() {
    let bar = TabBar::from_status(&Status::default());
    assert!(bar.tabs.is_empty());
    assert_eq!(bar.active(), None);
    assert_eq!(bar.local_count(), 0);
}

// ── 넘침 창(G9x — 파이썬 `_entries` 스크롤 규칙) ──────────────────────────────

#[test]
fn everything_fits_when_the_budget_is_wide() {
    let w = tab_window(&[5, 5, 5], Some(0), 0, 40);
    assert_eq!(w, TabWindow { start: 0, end: 3, left: false, right: false, scroll: 0 });
}

#[test]
fn the_right_arrow_appears_when_tabs_overflow() {
    // 예산 12: 5+5=10 + ▶ 예약 1 → 두 탭까지. 셋째가 잘려 ▶ 가 선다.
    let w = tab_window(&[5, 5, 5], Some(0), 0, 12);
    assert_eq!((w.start, w.end, w.left, w.right), (0, 2, false, true));
}

#[test]
fn the_selected_tab_is_always_pulled_into_view() {
    // 선택이 창 밖(오른쪽)이면 스크롤이 따라간다 — 파이썬의 while 보정.
    let w = tab_window(&[5, 5, 5, 5], Some(3), 0, 12);
    assert!(w.scroll > 0, "스크롤이 안 따라갔다: {w:?}");
    assert!((w.start..w.end).contains(&3), "선택 탭이 안 보인다: {w:?}");
    assert!(w.left, "왼쪽에 잘린 탭이 있는데 ◀ 가 없다");
    // 선택이 창 밖(왼쪽)이면 스크롤이 되돌아온다.
    let w = tab_window(&[5, 5, 5, 5], Some(0), 2, 12);
    assert_eq!(w.scroll, 0);
    assert!((w.start..w.end).contains(&0));
}

#[test]
fn a_manual_scroll_survives_until_the_selection_moves() {
    // ★ 파이썬과 갈라선 자리(§1 — 버그까지 동형 아님): 저쪽은 보정을 매 렌더 돌려
    //   활성 탭이 맨 왼쪽이면 ▶ 가 즉시 되돌아간다. 우리는 선택이 안 움직인
    //   프레임(None)에는 보정을 안 걸어 손 스크롤이 산다.
    let w = tab_window(&[5, 5, 5, 5], None, 1, 12);
    assert_eq!(w.scroll, 1, "손 스크롤이 되돌아갔다: {w:?}");
    assert!(w.left);
    // 선택이 움직이면(Some) 그때 보정된다.
    let w = tab_window(&[5, 5, 5, 5], Some(0), 1, 12);
    assert_eq!(w.scroll, 0);
}

#[test]
fn at_least_one_tab_is_always_drawn() {
    // 예산보다 넓은 탭 하나 — 그래도 그린다(i > scroll 가드). 빈 창은 탭바가
    // 통째로 사라진 것처럼 보인다.
    let w = tab_window(&[30], None, 0, 10);
    assert_eq!((w.start, w.end), (0, 1));
    // 빈 목록은 빈 창.
    assert_eq!(tab_window(&[], None, 5, 10).end, 0);
}

// ── 원격 제목을 **그릴 때만** 접는다(§10-21ⓓ2) ─────────────────────────────────

#[test]
fn the_remote_title_folds_only_what_the_setting_asks_for() {
    // ★ 이름 자체는 못 바꾼다 — 서버가 짓고 `remote-detach` 의 인자이며 "`⇄` 와 첫
    //   `:` 사이가 호스트"라는 계약이다. 접는 것은 탭바에 찍는 글자뿐이다.
    let tab = Tab { index: 0, name: "⇄boxA:build".into(), remote: true, ..Tab::default() };
    assert_eq!(tab.label(1, "full"), "1:⇄boxA:build");
    assert_eq!(tab.label(1, "host"), "1:boxA:build", "아이콘만 뺀다(색이 원격을 말한다)");
    assert_eq!(tab.label(1, "name"), "1:build");
    // 값은 그대로다 — 접는 것이 이름을 갉아먹으면 안 된다.
    assert_eq!(tab.name, "⇄boxA:build");
}

#[test]
fn a_local_tab_is_never_folded() {
    // 판정은 **`remote` 플래그**로 한다 — 사용자가 탭 이름을 `⇄…` 로 지어도 안 속는다.
    let tab = Tab { index: 0, name: "⇄not:remote".into(), remote: false, ..Tab::default() };
    for mode in ["full", "host", "name"] {
        assert_eq!(tab.label(1, mode), "1:⇄not:remote", "{mode}");
    }
}

#[test]
fn an_unexpected_shape_is_kept_whole_instead_of_vanishing() {
    // 파싱 실패가 탭을 사라지게 하면 안 된다 — `:` 가 없으면 통째로 이름이다.
    let tab = Tab { index: 0, name: "⇄weird".into(), remote: true, ..Tab::default() };
    for mode in ["full", "host", "name"] {
        assert_eq!(tab.label(1, mode), "1:weird", "{mode}");
    }
    // 모르는 형식 낱말은 **접지 않는다**(설정 파일이 낡았거나 오타일 때).
    let ok = Tab { index: 0, name: "⇄boxA:build".into(), remote: true, ..Tab::default() };
    assert_eq!(ok.label(1, "그런건없다"), "1:⇄boxA:build");
}

#[test]
fn the_flags_survive_the_fold() {
    // 접기가 벨·활동 표식을 먹으면 그 탭이 부르는 것을 못 본다.
    let tab = Tab {
        index: 0, name: "⇄boxA:build".into(), remote: true, bell: true, pinned: true,
        ..Tab::default()
    };
    assert_eq!(tab.label(2, "name"), "* 2:build!");
}
