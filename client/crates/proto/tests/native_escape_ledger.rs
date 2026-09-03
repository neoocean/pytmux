//! **Tier D 등록표 게이트** — 「내가 직접 그린다」는 오버레이가 표·`CAPS`·갈림 대장에서
//! 같은 말을 하나(pytmux-458).
//!
//! # 왜 이 자가 필요한가
//!
//! 네이티브 전환(시계·달력·IME 알약)은 전부 한 장치를 쓴다: 클라가 「이 오버레이는 내가
//! 그린다」를 광고하면 서버가 격자 런 대신 **상태**를 준다. 그 장치의 위험은 조용히
//! 늘어나는 것이다 — 오버레이 하나를 더 가져오는 데 드는 비용이 문자열 하나라, 왜
//! 가져왔는지·안 가져온 클라에서는 무엇이 보이는지가 **아무 데도 안 남는다**.
//!
//! 설계 §4.4 의 Tier D 표는 그래서 있고, 종전에 그 표는 **문서의 한 줄**이었고 게이트가
//! 없었다. 이 파일이 세 집합을 맞대 본다:
//!
//! | 집합 | 어디 |
//! |---|---|
//! | 등록표 | [`proto::message::NATIVE_OVERLAYS`] — 이름 · 왜 · 없는 클라에서 보이는 것 |
//! | 광고 | [`proto::message::CAPS`] 의 `native_overlay` |
//! | 갈림 대장 | `common/divergence.rs` 의 ⓑ(`Class::Pixels`) 줄 |
//!
//! ⚠ 대장 쪽은 **축이 다르다**(대장은 「정본에 없는 표면」을 센다). 네이티브 오버레이는
//! 정본에도 **있는** 표면이고 갈리는 것은 **그리는 방법**이라 그 축에 줄이 설 수 없다 —
//! 그래서 여기서 요구하는 것은 대장의 줄이 아니라 **등록표 자신이 ⓑ 의 근거를 적었나**다
//! (`why` 가 픽셀 그림임을 말하나). 그 사실을 이 머리말에 적어 두는 이유는, 안 적으면
//! 다음 사람이 「대장에 줄이 없다」를 빠뜨림으로 읽기 때문이다.

use std::collections::BTreeSet;

use proto::message::{CAPS, NATIVE_OVERLAYS};

/// 광고 문자열. 이 이름이 바뀌면 서버가 못 알아듣는다 — 그래서 상수로 못박는다.
const CAP: &str = "native_overlay";

#[test]
fn the_registry_and_the_advertisement_say_the_same_thing() {
    let advertised = CAPS.contains(&CAP);
    assert_eq!(
        advertised,
        !NATIVE_OVERLAYS.is_empty(),
        "등록표와 광고가 갈렸다 — 등록표가 {}인데 `CAPS` 는 {}다.\n\
         ⛔ 광고만 하고 그리는 것이 없으면 서버가 **런을 안 보내** 그 오버레이가 통째로 \
         사라진다. 반대로 그리면서 광고를 안 하면 서버는 종전대로 런을 보내고, \
         그림이 두 벌 겹친다.",
        if NATIVE_OVERLAYS.is_empty() { "비었다" } else { "차 있다" },
        if advertised { "광고한다" } else { "안 한다" },
    );
}

#[test]
fn every_registered_overlay_says_why_and_what_is_lost_without_it() {
    // ⛔ 이유 없는 등록은 그냥 가져온 것이다 — 그 줄은 다음 사람에게 아무 말도 안 한다.
    for row in NATIVE_OVERLAYS {
        assert!(!row.name.is_empty(), "이름 없는 줄이 있다");
        assert!(
            row.why.len() > 40,
            "{}: **왜** 서버가 격자로 못 그리나가 없다(한 줄로는 근거가 안 된다)",
            row.name
        );
        assert!(
            row.without.len() > 10,
            "{}: 이것을 광고 **안 한** 클라에서 무엇이 보이나가 없다 — \
             그것이 곧 정본의 그림이고, 안 적으면 「정본에서도 사라졌나」를 알 수 없다",
            row.name
        );
        // ⓑ 의 근거를 실제로 대는가(pytmux-185 의 허용 목록 중 「픽셀 단위 그림」).
        assert!(
            row.why.contains("픽셀") || row.why.contains("캔버스") || row.why.contains("벡터"),
            "{}: 허용 갈림 ⓑ(픽셀 단위 그림)의 근거가 아니다 — 다른 사유로 서버의 \
             그림을 가져오는 것은 이 장치의 쓰임이 아니다(설계 §4.1: 표현만 가져간다)",
            row.name
        );
    }
}

#[test]
fn the_registry_has_no_duplicates() {
    let names: BTreeSet<&str> = NATIVE_OVERLAYS.iter().map(|r| r.name).collect();
    assert_eq!(
        names.len(),
        NATIVE_OVERLAYS.len(),
        "같은 오버레이가 두 줄이다 — 두 줄이 갈리면 어느 쪽이 등록인지 알 수 없다"
    );
}

#[test]
fn a_frame_without_the_cap_carries_no_native_state() {
    // 대조군의 절반(나머지 절반 = 정본 스위트의 「광고 안 한 클라의 바이트가 같다」).
    // 여기서 재는 것은 **읽는 쪽**이다: `native` 칸이 없는 종전 프레임을 그대로 읽고
    // 빈 상태로 둔다(없는 칸을 `null` 이나 오류로 만들지 않는다).
    let old_frame = r#"{"t":"plugin_cells","layer":"overlay","dim":[1],
        "runs":[{"x":0,"y":0,"text":"12","style":{}}],"zones":[],"keys":[]}"#;
    let cells: proto::session::PluginCells =
        serde_json::from_str(old_frame).expect("종전 프레임을 못 읽는다 — 뒤로 안 맞는다");
    assert!(
        cells.native.is_empty(),
        "`native` 칸이 없는 프레임이 빈 상태가 아니다"
    );
    assert_eq!(cells.runs.len(), 1, "종전 칸을 읽는 방식이 바뀌었다");
    assert_eq!(cells.dim, vec![1], "종전 칸을 읽는 방식이 바뀌었다");
}

#[test]
fn a_frame_with_the_cap_carries_state_the_client_can_read() {
    // 서버가 실제로 보내는 모양(파이썬 `plugin_native` → JSON: 패널 id 가 문자열 키다).
    let frame = r#"{"t":"plugin_cells","layer":"overlay","dim":[3],"runs":[],
        "zones":[],"keys":[],"native":{"clock":{"3":{"time":"01:02:03"}}}}"#;
    let cells: proto::session::PluginCells =
        serde_json::from_str(frame).expect("네이티브 프레임을 못 읽는다");
    let clock = cells.native.get("clock").expect("`clock` 상태가 없다");
    let pane = clock.get("3").expect("패널 3 의 상태가 없다");
    assert_eq!(
        pane.get("time").and_then(|v| v.as_str()),
        Some("01:02:03"),
        "시각이 상태에서 안 읽힌다"
    );
    // ⛔ **런은 안 온다** — 오면 벡터 그림 위에 격자 글자가 겹친다(서버 쪽 계약).
    assert!(cells.runs.is_empty(), "네이티브 오버레이인데 런이 함께 왔다");
    // 딤은 종전대로 **서버가 정한다**(표현만 클라가 가져간다 · 설계 §4.1).
    assert_eq!(cells.dim, vec![3], "딤이 사라졌다 — 그것은 여전히 서버 것이다");
}

#[test]
fn print_the_native_ledger() {
    // 게이트가 아니라 **자**다.
    println!("\nTier D 네이티브 오버레이 등록표(우리가 직접 그리는 것):");
    for row in NATIVE_OVERLAYS {
        println!("  {:<10} 없는 클라에서는 — {}", row.name, row.without);
    }
    if NATIVE_OVERLAYS.is_empty() {
        println!("  (없다 — 전부 서버가 격자로 그린다)");
    }
    println!();
}
