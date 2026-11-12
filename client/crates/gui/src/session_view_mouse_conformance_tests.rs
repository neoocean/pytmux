//! 세션 뷰 마우스 적합성 — 정본 `MultiplexerView` 가 손짓마다 서버로 보낸 것 ↔ 이 뷰(pytmux-569).
//!
//! 픽스처는 `python3 scripts/gen_mouse_scenarios.py` 가 정본 핸들러(`on_mouse_down/move/up` ·
//! `on_mouse_scroll_*`)에 입력열을 그대로 먹여 뽑는다. 단계마다 **와이어 프레임**(서버가 받는
//! 그 dict) · 연 판(`menu`) · 선택이 섰나 · 자동 스크롤 델타를 적는다. 여기서는 같은 입력열을
//! 뷰의 진입점(`handle_*`)에 넣고 [`Outgoing::to_frame`] 으로 바꿔 JSON 그대로 맞댄다.
//!
//! # 왜 프레젠터가 아니라 진입점인가
//!
//! 헤드리스 프레젠터는 요소가 쏜 액션을 뷰까지 안 나른다(상류 비공개 — 메모리 「클릭 주입
//! 하네스는 거짓 오라클」). 그래서 «이벤트 → 액션» 배선은 소스 오라클(`the_mouse_wiring_*`)이
//! 따로 재고, 여기는 «액션 → 서버로 나가는 것»을 잰다.
//!
//! # KNOWN — 이름 붙은 갈림만 허용한다
//!
//! 갈린 사례는 [`KNOWN`] 의 접두에 걸려야 한다. 그리고 **걸린 줄이 더는 안 갈리면 그것도
//! 실패**다(낡은 예외는 다음 결함을 숨긴다).

use super::*;
use serde_json::Value;

/// (사례 이름 접두, 왜 갈리나, 그 일을 든 이슈).
const KNOWN: &[(&str, &str, &str)] = &[
    (
        "A_b2_",
        "가운데 버튼 — 상류(warpui)가 뗌·끌기를 안 주고 누름에 ctrl 칸도 없다. GUI 는 누름에서 \
         클릭(press+release)으로 끝낸다 — 정본은 뗌 자리에서 release · 1002 앱엔 drag · Ctrl 은 삼킨다",
        "pytmux-631",
    ),
    (
        "D_b2_",
        "가운데 버튼 끌기(경계 리사이즈 · 머리줄 옮기기) — 같은 상류 사정",
        "pytmux-631",
    ),
    (
        "E_scroll_",
        "스크롤(copy) 모드 — 정본은 누르는 즉시(임계 없이) 선택을 세우고 접힌 선택도 copy_range 로 \
         복사한다(클릭이 클립보드를 덮는다 · 정본 결함 의심 · 사람 결정)",
        "pytmux-632",
    ),
];

fn fixture() -> Vec<Value> {
    let v: Value = serde_json::from_str(include_str!("../../proto/tests/fixtures/mouse_scenarios.json"))
        .expect("픽스처를 읽을 수 없다 — python3 scripts/gen_mouse_scenarios.py 로 다시 뽑을 것");
    v["cases"].as_array().expect("cases 가 없다").clone()
}

/// 패널 둘에 줄을 채운 화면(top 0) — 선택의 절대 좌표가 서려면 화면이 있어야 한다.
fn screen(pane: i64) -> ServerMessage {
    let rows: Vec<Value> = (0..9).map(|_| serde_json::json!([["0123456789abcdefghij", {}]])).collect();
    serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": pane, "rows": rows, "cursor": null, "wrap": [], "top": 0
    }))
    .unwrap()
}

fn b(v: &Value, k: &str) -> bool {
    v[k].as_bool().unwrap_or(false)
}

fn i(v: &Value, k: &str) -> i64 {
    v[k].as_i64().unwrap_or(0)
}

/// 사례 하나를 뷰에 먹이고 단계마다 (와이어, ui, 선택, 자동 스크롤) 을 돌려준다.
fn run_case(case: &Value) -> Vec<(Vec<Value>, Vec<String>, bool, i64)> {
    let (mut view, tx, sent) = harness();
    let layout: ServerMessage = serde_json::from_value(case["layout"].clone()).expect("배치");
    for msg in [layout, screen(1), screen(2)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    let cfg = &case["config"];
    view.config.mouse = b(cfg, "mouse");
    view.config.mouse_drag_copy = match cfg["drag_copy"].as_str() {
        Some("off") => base::config::DragCopy::Off,
        Some("shift") => base::config::DragCopy::Shift,
        _ => base::config::DragCopy::On,
    };
    view.config.mouse_drag_threshold = i(cfg, "threshold") as u16;
    match cfg["mode"].as_str() {
        Some("esc") => {
            view.handle_key(Key::Escape, Mods::NONE);
        }
        Some("prefix") => {
            let (k, m) = view.mode.prefix();
            view.handle_key(k, m);
        }
        Some("scroll") => view.mode.enter_scroll(),
        _ => {}
    }
    view.pump_headless();
    sent.lock().unwrap().clear();
    let mut out = Vec::new();
    for st in case["steps"].as_array().unwrap() {
        let (x, y) = (i(st, "x") as i32, i(st, "y") as i32);
        let at = (x >= 0 && y >= 0).then_some((x as u16, y as u16));
        let button = i(st, "button");
        let mods = MouseMods { shift: b(st, "shift"), ctrl: b(st, "ctrl"), alt: b(st, "alt") };
        let menu_before = view.screens.top() == Some(Screen::Menu);
        match (st["ev"].as_str().unwrap(), button) {
            ("down", 1) => {
                if let Some(at) = at {
                    view.handle_mouse_down_with(at, mods);
                }
            }
            ("down", 2) => {
                view.handle_middle_click(at);
            }
            // 맥 밖의 판정을 잰다 — 맥은 Ctrl+왼쪽이 곧 우클릭인 OS 관례라 Ctrl 을 안 본다(ⓒ ·
            // `handle_right_mouse_down_with` 문서). 그 갈래는 아래 `macs_ctrl_click_*` 가 잰다.
            ("down", 3) => {
                view.handle_right_mouse_down_with(at, mods.ctrl, false);
            }
            ("move", 0) => {
                view.handle_mouse_move(at);
            }
            ("move", 1) => {
                view.pointer_signed = Some((x, y));
                view.handle_mouse_drag(at);
            }
            ("up", 1) => {
                view.pointer_signed = Some((x, y));
                view.handle_mouse_up(at);
            }
            // 가운데·오른쪽의 끌기·뗌은 상류가 안 준다(KNOWN).
            ("move", _) | ("up", _) => {}
            ("wheel_up", _) => {
                view.handle_wheel(true, at);
            }
            ("wheel_down", _) => {
                view.handle_wheel(false, at);
            }
            (ev, b) => panic!("모르는 단계: {ev} {b}"),
        }
        view.pump_headless();
        let wire: Vec<Value> = sent.lock().unwrap().drain(..).map(|o| o.to_frame()).collect();
        let mut ui = Vec::new();
        if !menu_before && view.screens.top() == Some(Screen::Menu) {
            ui.push("menu".to_owned());
        }
        out.push((
            wire,
            ui,
            view.selection.is_some(),
            view.autoscroll.map_or(0, |a| i64::from(a.delta)),
        ));
    }
    out
}

/// 사례마다 갈린 첫 단계(없으면 `None`).
fn first_divergence(case: &Value) -> Option<String> {
    let got = run_case(case);
    for (want, (wire, ui, sel, auto)) in case["want"].as_array().unwrap().iter().zip(got) {
        let want_wire = want["wire"].as_array().unwrap();
        let want_ui: Vec<String> = want["ui"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|u| u.as_str().map(str::to_owned))
            .collect();
        if *want_wire != wire || want_ui != ui || b(want, "selecting") != sel || i(want, "autoscroll") != auto {
            return Some(format!(
                "단계 {}: 정본 wire={} ui={want_ui:?} 선택={} 자동={} · 우리 wire={} ui={ui:?} 선택={sel} 자동={auto}",
                i(want, "step"),
                Value::Array(want_wire.clone()),
                b(want, "selecting"),
                i(want, "autoscroll"),
                Value::Array(wire),
            ));
        }
    }
    None
}

#[test]
fn every_mouse_scenario_sends_what_the_canon_sends() {
    let cases = fixture();
    let mut wrong = Vec::new();
    let mut stale: Vec<&str> = KNOWN.iter().map(|k| k.0).collect();
    for case in &cases {
        let name = case["name"].as_str().unwrap();
        let known = KNOWN.iter().find(|k| name.starts_with(k.0));
        match (first_divergence(case), known) {
            (Some(_), Some(k)) => stale.retain(|s| *s != k.0),
            (Some(d), None) => wrong.push(format!("{name} — {d}")),
            (None, _) => {}
        }
    }
    assert!(
        wrong.is_empty(),
        "정본과 다른 것을 보낸 마우스 시나리오 {}개(픽스처 {}개 중):\n  {}",
        wrong.len(),
        cases.len(),
        wrong.join("\n  ")
    );
    assert!(stale.is_empty(), "KNOWN 줄이 더는 안 갈린다 — 표에서 지울 것: {stale:?}");
}

/// 픽스처가 **실제로 무언가를 덮는지** — 빈 픽스처도 위 시험은 통과한다.
#[test]
fn the_mouse_scenarios_cover_every_axis() {
    let cases = fixture();
    assert!(cases.len() >= 300, "시나리오가 {}개뿐이다", cases.len());
    let names: Vec<&str> = cases.iter().map(|c| c["name"].as_str().unwrap()).collect();
    for axis in ["_b1_", "_b2_", "_b3_", "_plain_", "_shift_", "_ctrl_", "_t0_", "_t1_", "_t2_", "_t3_",
                 "_dcon_", "_dcoff_", "_dcshift_", "header", "divider", "drag_out_left", "drag_out_top",
                 "B_", "C_", "E_esc", "E_prefix", "E_scroll", "A3_alt"] {
        assert!(names.iter().any(|n| n.contains(axis)), "축 {axis} 가 픽스처에 없다");
    }
    let sending = cases
        .iter()
        .flat_map(|c| c["want"].as_array().unwrap().iter())
        .filter(|w| !w["wire"].as_array().unwrap().is_empty())
        .count();
    assert!(sending >= 200, "무엇인가 보낸 단계가 {sending}개뿐이다");
}

/// 배선 — 이벤트가 진입점까지 오는가(프레젠터가 액션을 안 나르므로 소스로 잰다).
#[test]
fn the_mouse_wiring_carries_ctrl_middle_and_the_signed_pointer() {
    let src = include_str!("session_view.rs");
    for needle in [
        "MouseMods { shift: mods.shift, ctrl: mods.ctrl, alt: mods.alt }",
        ".on_middle_mouse_down(",
        "ViewAction::MiddleMouseDown(at) => self.handle_middle_click(*at)",
        "ViewAction::MouseDrag(at, Self::signed_cell_from_event(evt, position))",
        "ViewAction::MouseUp(at, Self::signed_cell_from_event(evt, position))",
    ] {
        assert!(src.contains(needle), "배선이 빠졌다: {needle}");
    }
}

/// 맥의 Ctrl+클릭 = 우클릭 — **허용 갈림 ⓒ(OS 창 통합)**. 상류가 Ctrl+왼쪽을 `RightMouseDown`
/// 으로 바꿔 주고 우리는 그 관례를 둔다(사람 결정 2026-11-13). 정본(단말 안)은 Ctrl+클릭을
/// 삼킨다. ⚠ 갈림 대장(`proto/tests/common/divergence.rs`)에는 마우스 축이 아직 없다
/// (새 축은 pytmux-598) — 그 축이 서면 이 줄을 대장으로 옮긴다.
#[test]
fn macs_ctrl_click_opens_the_menu_and_elsewhere_ctrl_right_is_swallowed() {
    let case = fixture().into_iter().find(|c| c["name"] == "A_b3_ctrl_t0_dcon_click").unwrap();
    let open_menu = |mac: bool| {
        let (mut view, tx, _sent) = harness();
        let layout: ServerMessage = serde_json::from_value(case["layout"].clone()).unwrap();
        tx.send(LinkEvent::Message(Box::new(layout))).unwrap();
        view.pump_headless();
        view.config.mouse = true;
        view.handle_right_mouse_down_with(Some((5, 4)), true, mac);
        view.screens.top() == Some(Screen::Menu)
    };
    assert!(open_menu(true), "맥의 Ctrl+클릭(=우클릭)이 메뉴를 안 열었다");
    assert!(!open_menu(false), "맥 밖의 Ctrl+우클릭이 메뉴를 열었다(정본은 삼킨다)");
}
