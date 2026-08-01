//! 교차구현 적합성 — 키 도움말이 정본만큼의 **마우스 제스처**를 보여 주나.
//!
//! 정본 `list-keys`(= `mouse-help`)가 이 절을 만든 이유를 저쪽 주석이 적어 둔다:
//! **구현된 제스처가 명령에도 메뉴에도 안 떠 사장돼 있었다.** 우리 GUI 는 더 심하다 —
//! 터미널과 달리 제스처를 짐작할 단서가 화면에 없다.
//!
//! # 왜 글자가 아니라 수인가
//!
//! 우리는 같은 제스처를 다르게 **묶어** 적는다(클릭을 휠 줄에, Shift+드래그를 드래그
//! 줄에) — 화면 폭이 다르니 묶음도 다른 게 맞다. 글자를 대조하면 거짓 실패만 나온다.
//! 지켜야 할 것은 "정본이 여섯 가지를 알려 주면 우리도 여섯 가지를 알려 준다"이다.
//!
//! 이 대조가 없으면 `MOUSE_GESTURES` 표와 그것을 도는 오라클이 **자기끼리만** 맞아,
//! 줄을 하나 지워도 둘 다 조용히 따라 줄어든다(실제로 변이가 안 죽었다).

use base::MOUSE_GESTURES;
use serde::Deserialize;

#[derive(Deserialize)]
struct Fx {
    mouse_gestures: Vec<String>,
}

#[test]
fn we_show_as_many_mouse_gestures_as_canon_does() {
    let fx: Fx = serde_json::from_str(include_str!("fixtures/client_surface.json"))
        .expect("픽스처를 못 읽었다");
    assert!(
        !fx.mouse_gestures.is_empty(),
        "픽스처의 제스처 목록이 비었다 — 통과가 아니라 고장이다"
    );
    assert_eq!(
        MOUSE_GESTURES.len(),
        fx.mouse_gestures.len(),
        "정본은 {}가지를 보여 주는데 우리는 {}가지다 (정본: {:?})",
        fx.mouse_gestures.len(),
        MOUSE_GESTURES.len(),
        fx.mouse_gestures
    );
    // 제스처 이름이 겹치면 화면에 같은 줄이 두 번 뜨고, 그건 표를 옮기다 붙여넣기한
    // 흔적이다.
    let mut keys: Vec<&str> = MOUSE_GESTURES.iter().map(|(k, _)| *k).collect();
    keys.sort_unstable();
    let before = keys.len();
    keys.dedup();
    assert_eq!(before, keys.len(), "제스처 이름이 겹친다");
}
