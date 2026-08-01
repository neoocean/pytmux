//! 마우스 패스스루 — 패널 **안에서 도는 프로그램**에게 마우스를 넘긴다.
//!
//! # 왜 필요한가
//!
//! 이 클라는 마우스 캡처를 켜고 있다(경계선 드래그·클릭 포커스·드래그 복사에 필요하다).
//! 그러면 패널 안에서 도는 마우스 1급 TUI(p4v-tui·less·htop·Claude Code)는 클릭을
//! **한 번도 못 받는다** — 증상은 "그 앱만 마우스가 안 먹는다"이고, 앱을 의심하게 된다.
//!
//! # 누가 켰는지는 서버만 안다
//!
//! 마우스 추적은 패널 안 프로그램이 DECSET(1000/1002/1003/1006)으로 켠다. 그 상태를
//! 아는 것은 PTY 출력을 파싱하는 서버뿐이고(`model.Pane.update_mouse_modes`), 서버는
//! 그것을 `layout` 의 패널마다 `mouse`/`mouse_sgr` 로 실어 보낸다. 클라가 할 일은
//! **그 값을 믿고** 바이트를 만들어 돌려주는 것뿐이다 — bracketed paste 와 같은 구조다.
//!
//! # 인코딩은 파이썬 클라와 한 글자도 다르면 안 된다
//!
//! 같은 앱이 클라에 따라 다르게 반응하면 그건 앱의 결함처럼 보인다. 아래 표는
//! `clientwidgets._encode_mouse` 를 그대로 옮긴 것이고, 적합성 테스트가 파이썬에서 뽑은
//! 픽스처와 대조한다(`tests/mouse_conformance.rs`).

/// 패널의 마우스 추적 상태. 서버가 `layout` 에 실어 보낸 값 그대로다.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct MouseMode {
    /// 0=꺼짐 · 1=클릭(1000) · 2=드래그(1002) · 3=모든 이동(1003).
    ///
    /// 서버가 **광고를 낮춰 보낼 수 있다** — Windows 에서는 3 을 2 로 캡한다(ConPTY 가
    /// 주입된 any-motion 리포트를 소비 못 하고 프롬프트에 글자로 흘린다). 그러니 클라는
    /// 이 값을 해석하지 말고 그대로 믿는다.
    pub track: u8,
    /// 1006(SGR 확장 좌표)을 켰는가. 안 켰으면 레거시 X10 인코딩이다.
    pub sgr: bool,
}

impl MouseMode {
    /// 이 패널이 마우스를 원하는가.
    pub fn wants_mouse(&self) -> bool {
        self.track >= 1
    }

    /// 버튼을 누른 채 움직이는 것까지 원하는가(1002 이상).
    ///
    /// 1000 만 켠 앱에 드래그를 보내면 **누른 적 없는 자리에서 눌린 것처럼** 읽힌다.
    pub fn wants_drag(&self) -> bool {
        self.track >= 2
    }
}

/// 넘길 이벤트 한 가지.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MouseKind {
    Press,
    Release,
    Drag,
    WheelUp,
    WheelDown,
}

/// 패널 안 좌표로 옮겨 바이트를 만든다. 패널 내용 밖이면 `None`.
///
/// `rect` 는 패널의 **내용** 사각형 `(x, y, w, h)` 다(테두리 제외). 좌표는 캔버스 기준으로
/// 받아 패널 기준 **1-based** 로 바꾼다 — 터미널 마우스 리포트의 좌표계가 그렇다.
///
/// `button` 은 1=왼쪽 · 2=가운데 · 3=오른쪽. 휠은 버튼을 안 본다.
pub fn encode(
    mode: MouseMode,
    rect: (u16, u16, u16, u16),
    x: u16,
    y: u16,
    kind: MouseKind,
    button: u8,
) -> Option<Vec<u8>> {
    let (px, py, w, h) = rect;
    // 1-based 로 옮기면서 범위를 본다. 패널 왼쪽/위쪽 밖이면 뺄셈이 음수가 되는데,
    // u16 에서는 그게 **거대한 값**이 되므로 뺄셈 전에 걸러야 한다.
    if x < px || y < py {
        return None;
    }
    let col = x - px + 1;
    let row = y - py + 1;
    if col > w || row > h {
        return None;
    }
    // 버튼 코드. 휠은 전용 값이고(64/65), 드래그는 32 를 더한다(모션 비트).
    let cb = match kind {
        MouseKind::WheelUp => 64,
        MouseKind::WheelDown => 65,
        MouseKind::Drag => base_button(button) + 32,
        MouseKind::Press | MouseKind::Release => base_button(button),
    };
    if mode.sgr {
        // SGR(1006): 좌표에 상한이 없고, 뗌을 `m` 으로 구분해 **어느 버튼을 뗐는지**
        // 알려 준다. 레거시에는 그 정보가 없다(아래).
        let final_byte = if kind == MouseKind::Release { 'm' } else { 'M' };
        return Some(format!("\x1b[<{cb};{col};{row}{final_byte}").into_bytes());
    }
    // 레거시 X10: 뗌은 **버튼 3**(어느 버튼인지 못 싣는다). 각 값에 32 를 더하고
    // 한 바이트에 담아야 하므로 223 에서 자른다 — 그 너머 좌표는 이 인코딩으로
    // 표현할 수 없다(앱이 1006 을 켜야 풀린다).
    let cb = if kind == MouseKind::Release { 3 } else { cb };
    let cap = |v: u16| 32 + v.min(223) as u8;
    Some(vec![0x1b, b'[', b'M', 32 + cb.min(223), cap(col), cap(row)])
}

fn base_button(button: u8) -> u8 {
    match button {
        2 => 1,
        3 => 2,
        _ => 0,
    }
}

#[cfg(test)]
#[path = "mouse_tests.rs"]
mod tests;
