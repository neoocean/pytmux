//! 살아 있는 pytmux 서버에 붙어 화면을 찍어 본다.
//!
//! P2 의 end-to-end 확인용 도구다. 적합성 테스트(`tests/conformance.rs`)는 **녹화된**
//! 와이어 페이로드로 합성을 검증하지만, 이 도구는 **실제 서버**에 붙어 핸드셰이크와
//! 메시지 순서까지 포함해 확인한다.
//!
//! ```sh
//! cargo run -p proto --example attach            # 기본 소켓을 찾는다
//! cargo run -p proto --example attach /tmp/x.sock # 소켓 지정
//! ```
use std::path::Path;
use std::time::Duration;

use proto::Connection;

fn main() {
    // 소켓을 명시하면 그것에, 아니면 서버 규칙대로 찾는다.
    let explicit = std::env::args().nth(1);
    let attached = match &explicit {
        Some(path) => Connection::attach_to(Path::new(path), 80, 24),
        None => Connection::attach(80, 24),
    };
    let mut conn = match attached {
        Ok(c) => c,
        Err(e) => {
            eprintln!("붙지 못했다: {e}");
            eprintln!("(pytmux 서버가 떠 있어야 한다: python3 pytmux.py 로 한 번 띄울 것)");
            std::process::exit(1);
        }
    };
    println!("붙었다: {}", conn.socket());

    let frame = match conn.first_frame(Duration::from_secs(5)) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("첫 화면을 받지 못했다: {e}");
            std::process::exit(1);
        }
    };

    let Some(layout) = &frame.layout else {
        eprintln!("layout 을 못 받았다 (원격 탭을 보는 중일 수 있다)");
        std::process::exit(1);
    };
    println!(
        "화면 {}x{} · 패널 {}개 · 활성 {}",
        layout.cols,
        layout.rows,
        layout.panes.len(),
        layout.active
    );

    // 블록(§10-13). 셸 통합이 켜져 있으면 여기 나온다.
    let blocks_seen: usize = layout
        .panes
        .iter()
        .map(|p| frame.blocks(p.id).len())
        .sum();
    println!("블록 {blocks_seen}개");
    for pane in &layout.panes {
        for b in frame.blocks(pane.id) {
            println!(
                "  [{}] {:?} exit={:?} cwd={:?}",
                b.badge(),
                b.command,
                b.exit,
                b.cwd
            );
        }
    }

    // 격자 전체(경계선 포함) — 두 클라가 같은 그림을 그리는지 눈으로 보는 자리다.
    if let Some(canvas) = frame.composite() {
        let (cols, rows) = canvas.size();
        println!("\n── 합성 격자 {cols}x{rows} (테두리 포함)");
        for y in 0..rows {
            println!("{}", canvas.row_text(y).trim_end());
        }
    }

    for pane in &layout.panes {
        println!(
            "\n── 패널 {} ({}x{} @ {},{}) {}{}",
            pane.id,
            pane.w,
            pane.h,
            pane.x,
            pane.y,
            pane.title,
            if pane.active { " [활성]" } else { "" }
        );
        match frame.compose_pane(pane.id) {
            Some(lines) => {
                for line in lines {
                    println!("│{}│", line.trim_end());
                }
            }
            None => println!("  (이 패널의 screen 을 아직 못 받았다)"),
        }
    }
}
