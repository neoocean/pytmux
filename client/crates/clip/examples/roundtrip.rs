//! 진짜 OS 클립보드에 넣고 다시 읽어 본다. **손으로 돌리는 확인**이다.
//!
//! ```sh
//! cargo run -p clip --example roundtrip
//! ```
//!
//! # 왜 테스트가 아닌가
//!
//! 클립보드는 **사용자가 쓰고 있는 자원**이다. `cargo test` 가 이걸 건드리면 그 상자에서
//! 일하던 사람이 방금 복사해 둔 것을 잃는다. 그래서 예제로 두고, 그마저도 원래 내용을
//! 먼저 읽어 두었다가 끝나면 되돌린다.
//!
//! # 무엇을 보는가
//!
//! 단위 테스트가 못 보는 것 하나: **콘솔 코드페이지를 건너 한글이 살아 오는가**. 이건
//! base64/UTF-16LE 왕복이 실제 PowerShell 과 맞물려야만 드러난다(파이썬 클라가 여기서
//! 깨졌었다 — 제보 2026-07-13). 다른 OS 에서는 각자의 도구로 같은 확인을 한다.

fn main() {
    // 한글 + 여러 줄 + 공백. 깨지는 방식이 저마다 달라 한 문자열에 모아 둔다.
    const SAMPLE: &str = "그림자 샤미\n  둘째 줄  \ttab";

    let before = read_clipboard();
    println!(
        "원래 클립보드: {} 글자(끝나면 되돌린다)",
        before.as_deref().map_or(0, |s| s.chars().count())
    );

    let ok = clip::copy(SAMPLE);
    println!("copy() → {ok}");

    let back = read_clipboard();
    match &back {
        Some(text) => {
            // 도구에 따라 끝에 개행이 붙는다(PowerShell `Get-Clipboard` 가 그렇다) —
            // 그건 읽는 쪽 사정이라 비교에서 뺀다. 보는 것은 **글자가 살아 왔는가**다.
            let same = text.trim_end() == SAMPLE.trim_end();
            println!("읽어온 것: {text:?}");
            println!("{}", if same { "일치 ✓" } else { "어긋남 ✗" });
        }
        None => println!("클립보드를 읽지 못했다(읽기 도구 없음) — copy 결과만 믿을 것"),
    }

    if let Some(text) = before {
        clip::copy(&text);
        println!("원래 내용을 되돌렸다");
    }
}

/// 확인용 읽기. **제품 코드에는 없다** — 이 클라의 붙여넣기는 터미널이 주는 것이라
/// 반대 방향이 필요 없다(크레이트 모듈 주석 참조).
fn read_clipboard() -> Option<String> {
    let argv: &[&str] = if cfg!(windows) {
        &["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"]
    } else if cfg!(target_os = "macos") {
        &["pbpaste"]
    } else {
        &["xclip", "-selection", "clipboard", "-o"]
    };
    let out = std::process::Command::new(argv[0])
        .args(&argv[1..])
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).into_owned())
        .filter(|s| !s.is_empty())
}
