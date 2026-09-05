//! OS 클립보드 — **외부 도구를 부른다**(pbcopy/xclip/wl-copy/PowerShell/clip.exe).
//!
//! 파이썬 클라의 `pytmuxlib/clientclip.py` 와 **같은 도구를 같은 순서로** 부른다. 두
//! 클라가 같은 상자에서 다른 클립보드에 넣으면 안 되기 때문이다 — 사용자는 어느 클라로
//! 복사했는지 기억하고 붙여넣지 않는다.
//!
//! # 왜 크레이트 의존이 아닌가
//!
//! `arboard` 같은 크레이트를 쓰면 Linux 에서 X11/Wayland 라이브러리를 끌어온다. 이 클라의
//! 정체성은 "아무 터미널에서나 돈다"이고 거기엔 **디스플레이가 없는 ssh 세션**이 포함된다
//! (사용자 결정 2026-07-27l). 외부 도구는 없으면 없는 대로 실패할 뿐이라 그 자리에서
//! 빌드·실행이 막히지 않는다.
//!
//! # 왜 글자 `copy` 뿐인가
//!
//! **글자를 읽는 쪽**은 여기 없다. 창 계층이 이미 클립보드를 쥐고 있어서
//! (`ClipboardContent`) GUI 는 그것을 읽는다 — 여기에 또 만들면 같은 것을 읽는 길이 둘이
//! 되고, 둘은 조용히 갈라진다.
//!
//! # 그런데 **이미지**는 여기 있다([`save_image`])
//!
//! 창 계층이 주는 것은 **바이트**(`ImageData`)인데, PTY 너머로는 비트맵이 못 간다 —
//! 정본이 이미 정한 계약이 「임시 파일로 떨구고 **경로 문자열**을 붙여넣는다」이다
//! (`pytmuxlib/clientclip.py::save_image` · Claude Code CLI 등이 경로를 첨부 이미지로
//! 읽는다). 그 「떨구는」 절반만 여기 산다. 이유 둘:
//!
//! - **재는 자리가 필요하다.** 뷰 안에 두면 진짜 클립보드에 그림을 넣어야 잴 수 있는데,
//!   그건 헤드리스 러너가 못 하는 일이다. 바이트를 인자로 받으면 **어느 상자에서나**
//!   잰다(이 저장소의 상습 실패 모드 = 「그 OS 에서만 재는 규칙」).
//! - 임시 파일 자리를 정하는 일은 클립보드 도구를 부르는 일과 같은 부류다 — OS 마다
//!   다르고, 두 곳에 생기면 한쪽만 고쳐지는 날이 온다.
//!
//! # 오래 걸린다
//!
//! Windows PowerShell 은 cold start 가 0.5~2초다. 이 함수는 **막힌다** — 호출부가 별도
//! 스레드에서 부르는 것을 전제한다(파이썬 클라도 `run_worker` 로 뺀다. 이벤트 루프에서
//! 동기로 부르면 드래그를 놓을 때마다 UI 가 그만큼 멈춘다).

use std::io::Write;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// PowerShell 한 번에 주는 시간. cold start 가 길어 다른 도구보다 넉넉하다
/// (파이썬 클라와 같은 값 — `clientclip._win_copy` 의 `timeout=5`).
const PS_TIMEOUT: Duration = Duration::from_secs(5);

/// 나머지 도구의 상한(파이썬 클라와 같은 값).
const TOOL_TIMEOUT: Duration = Duration::from_secs(2);

/// 자식이 끝났는지 다시 보는 간격. 사람이 기다리는 동작이라 촘촘할 필요가 없다.
const POLL: Duration = Duration::from_millis(25);

/// Windows 시스템 도구를 **절대경로**로 준다. 그 밖의 OS 에서는 이름 그대로다.
///
/// # 왜 필요한가 (검수 2026-08-09 B-3 · 2026-08-05 §4.3 의 미검증 항목)
///
/// Rust `std::process::Command` 는 Windows 에서 `CreateProcessW` 에 이름을 넘기지 않고
/// **자기가 찾는다**(`library/std/src/sys/process/windows.rs::search_paths`, 1.92.0 원문
/// 확인). 그 차례가 이렇다:
///
/// > 1. child paths → 2. **application path**(`current_exe()` 의 디렉터리) →
/// > 3·4. system/windows 디렉터리 → 5. parent paths(=PATH)
///
/// ★ **현재 작업 디렉터리는 안 낀다** — 2026-08-05 검수가 유보한 그 걱정은 사실이 아니었다.
/// ⛔ **그런데 ②가 있다.** 이 제품은 `client/build/` 의 **단일 실행 파일**로 배포되고
/// (`pytmux-gui-windows-x64.exe`) 사람들은 그것을 `Downloads` 같은 **자기가 쓸 수 있는
/// 폴더**에서 그대로 띄운다. 그 폴더에 `powershell.exe`·`clip.exe`·`explorer.exe` 를
/// 놓아 두면 시스템 것보다 **먼저** 잡힌다 — 복사 한 번·링크 한 번이 남의 코드를 돌린다.
/// (같은 폴더에 파일 하나를 더 놓는 일은 브라우저 다운로드 하나로 끝난다.)
///
/// # 무엇을 하나
///
/// 이름을 `%SystemRoot%` 아래의 **정해진 자리**로 바꾸고, 그 자리에 파일이 실제로 있을
/// 때만 쓴다. 판정 규칙 자체는 [`system_tool_at`] 이라는 순수 함수라 **Windows 가 아닌
/// 상자에서도 잰다**(이 저장소의 상습 실패 모드 = 「그 OS 에서만 재는 규칙」).
///
/// # ⛔ 못 찾으면 `None` 이다 — 이름으로 되돌아가지 않는다 (검수 2026-09-05 G-5)
///
/// 종전에는 `%SystemRoot%`/`windir` 이 없거나 지은 경로에 파일이 없으면 **맨 이름**을
/// 돌려줬다("보안을 위해 기능을 죽이지 않는다"). 그런데 이 함수가 막으려던 것이 바로
/// 그 맨 이름의 탐색이다 — 폴백은 구멍을 그대로 다시 연다. 환경변수가 빠진 상자는
/// 드물고, 그때 잃는 것은 «복사 한 번»이지만 폴백이 여는 것은 «남의 코드 실행»이다.
/// 그래서 fail-closed 로 간다: 부르는 쪽은 `None` 을 「그 도구는 못 쓴다」로 받아
/// 다음 후보로 가거나 **왜 안 됐는지 말한다**.
///
/// **표 밖의 이름**(`pwsh`·`xclip`·`open` …)은 종전대로 그대로 돌려준다 — 우리가 자리를
/// 못박은 적이 없는 이름까지 막으면 POSIX 도구들이 통째로 죽는다. 비-Windows 도 같다.
///
/// ⚠ `%SystemRoot%` 를 공격자가 쥐고 있다면 이 방어는 무의미하다 — 그러나 그 정도면
/// 이미 우리 프로세스의 환경을 쥔 것이라 위협 모형 밖이다(그때는 PATH 도 그의 것이다).
pub fn system_tool(name: &str) -> Option<String> {
    if !cfg!(windows) || system_tool_tail(name).is_none() {
        return Some(name.to_owned());
    }
    let root = std::env::var("SystemRoot")
        .or_else(|_| std::env::var("windir"))
        .ok();
    match system_tool_at(root.as_deref(), name) {
        Some(path) if std::path::Path::new(&path).exists() => Some(path),
        _ => None,
    }
}

/// [`system_tool`] 의 **순수한 절반** — 이름과 `%SystemRoot%` 로 절대경로를 짓는다.
///
/// 모르는 이름·루트 없음이면 `None`(= 부르는 쪽이 이름 그대로 쓴다). 표를 여기 두는
/// 이유는 그것이 **재고 싶은 것**이기 때문이다 — 파일 존재 여부와 OS 는 이 함수 밖이다.
pub fn system_tool_at(system_root: Option<&str>, name: &str) -> Option<String> {
    // 공백만 든 값도 「없다」로 본다 — 안 그러면 `  \System32\cmd.exe` 라는 상대경로가
    // 나오고, 그것은 우리가 막으려던 그 탐색으로 되돌아간다(이 시험이 잡았다).
    let root = system_root?.trim().trim_end_matches(['\\', '/']);
    if root.is_empty() {
        return None;
    }
    let tail = system_tool_tail(name)?;
    Some(format!("{root}\\{tail}"))
}

/// 이름 → `%SystemRoot%` 아래의 **상대 자리**(표에 없으면 `None`).
///
/// [`system_tool_at`] 에서 떼어 낸 이유: [`system_tool`] 이 「표에 있는데 못 찾았다」
/// (= fail-closed)와 「우리가 자리를 못박은 적 없는 이름」(= 그대로 쓴다)을 갈라야
/// 하는데, 루트까지 엮인 함수로는 그 둘이 같은 `None` 으로 보인다(검수 2026-09-05 G-5).
pub fn system_tool_tail(name: &str) -> Option<String> {
    // ⛔ 확장자까지 적는다 — 안 적으면 그 자리에서 다시 `.exe` 를 붙이는 규칙이 하나
    //    더 생긴다(std 가 하는 그 일을 우리가 또 하게 된다).
    match name.trim_end_matches(".exe").to_ascii_lowercase().as_str() {
        // 탐색기는 System32 가 아니라 Windows 디렉터리에 산다.
        "explorer" => Some("explorer.exe".to_owned()),
        "clip" => Some("System32\\clip.exe".to_owned()),
        "cmd" => Some("System32\\cmd.exe".to_owned()),
        "rundll32" => Some("System32\\rundll32.exe".to_owned()),
        // Windows PowerShell 5.x. `pwsh`(7.x)는 시스템 자리에 없으므로 표에 안 넣는다 —
        // 이 크레이트가 부르는 것도 5.x 쪽이다(`win_copy`).
        "powershell" => {
            Some("System32\\WindowsPowerShell\\v1.0\\powershell.exe".to_owned())
        }
        _ => None,
    }
}

/// 클립보드 이미지 바이트를 **임시 파일로 떨구고 그 경로**를 준다(못 하면 `None`).
///
/// # 왜 경로인가
///
/// 정본이 정한 계약이다(`pytmuxlib/clientclip.py::save_image` · 결정 ①): 클라는 PTY 너머로
/// 비트맵을 못 옮기므로 파일로 떨구고 **경로 문자열**을 붙여넣는다. Claude Code CLI 등이
/// 그 경로를 첨부 이미지로 읽는다.
///
/// ⚠ **파일은 이 클라가 도는 상자에 생긴다.** 원격(ssh) 탭에서는 그 경로가 서버 쪽에서
/// 뜻이 없다 — 정본은 그때 scp 로 옮기거나 `Alt+V` 로 폴백한다(호출부의 일이다).
///
/// # 확장자를 MIME 에서 짓는 이유
///
/// 창 계층은 원본 형식을 **보존해서 준다**(`try_preserve_original_format` — png/jpeg/gif/
/// webp) 하고, 못 알아보면 PNG 로 바꿔서 준다(`convert_raw_bitmap_to_png`). 곧 이 집합은
/// 닫혀 있다. ⛔ **모르는 MIME 은 `None`** 이다 — 이름만 `.png` 로 붙여 두면 앱이 열다
/// 실패하고, 그 실패는 붙여넣기가 아니라 **그림이 깨졌다**로 읽힌다.
///
/// # 왜 `%TEMP%` 를 std 에 묻나
///
/// 잃어버린 앞 구현(CL 71659)이 `/tmp/pytmux_…` 를 **박아 두었다** — 그 자리는 Windows 에
/// 없고, 제보자의 상자가 바로 Windows 였다. [`std::env::temp_dir`] 이 OS 마다 맞는 자리를
/// 안다.
///
/// 지우지 않는다: 정본도 안 지운다(붙여넣은 경로를 앱이 **나중에** 읽는다 — 우리가 먼저
/// 치우면 그 앱이 빈손을 쥔다). OS 의 임시 청소가 거둔다.
pub fn save_image(bytes: &[u8], mime: &str) -> Option<String> {
    // 빈 바이트는 파일을 만들 값이 아니다 — 0바이트 그림을 붙이면 앱이 "깨진 파일"이라
    // 말하고, 사용자는 pytmux 가 망친 줄 안다.
    if bytes.is_empty() {
        return None;
    }
    let ext = image_extension(mime)?;
    let dir = std::env::temp_dir();
    // 이름이 겹치면 **남의 그림을 덮어쓴다**(연달아 붙이면 밀리초가 같을 수 있다).
    // `create_new` 는 이미 있으면 실패하므로 그때 다음 번호로 간다 — 두 번 묻지 않고
    // 만들기 한 번으로 가른다(경합에도 안전하다).
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_nanos();
    let pid = std::process::id();
    for bump in 0..MAX_NAME_TRIES {
        let path = dir.join(format!("pytmux-clip-{pid}-{stamp}-{bump}.{ext}"));
        match std::fs::File::create_new(&path) {
            Ok(mut file) => {
                // 못 쓴 파일은 **남기지 않는다** — 0바이트나 반쪽 그림이 남으면 위의
                // "빈 바이트" 규칙을 파일 쪽에서 되살리는 꼴이다.
                if file.write_all(bytes).is_err() {
                    let _ = std::fs::remove_file(&path);
                    return None;
                }
                return Some(path.to_string_lossy().into_owned());
            }
            Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(_) => return None,
        }
    }
    None
}

/// 이름이 겹칠 때 몇 번까지 다음 번호로 가나. 여기까지 겹치면 임시 디렉터리 쪽이
/// 이상한 것이라 더 세는 것이 의미가 없다.
const MAX_NAME_TRIES: u32 = 16;

/// MIME → 확장자. **순수한 절반**이라 어느 상자에서나 잰다([`save_image`] 문서).
///
/// 표를 `save_image` 안에 접지 않는 이유는 이것이 **재고 싶은 것**이기 때문이다 —
/// 파일 시스템은 이 판정 밖이다(`system_tool_at` 과 같은 규율).
pub fn image_extension(mime: &str) -> Option<&'static str> {
    // `image/jpg` 는 표준이 아니지만 도는 값이라 같이 받는다(앞 구현도 그랬다).
    match mime.trim().to_ascii_lowercase().as_str() {
        "image/png" => Some("png"),
        "image/jpeg" | "image/jpg" => Some("jpg"),
        "image/gif" => Some("gif"),
        "image/webp" => Some("webp"),
        _ => None,
    }
}

/// 텍스트를 OS 클립보드에 넣는다. 성공하면 `true`.
///
/// 도구가 하나도 없으면 `false` 다 — 그때 사용자에게 "복사됨"이라고 말하면 거짓말이 되고,
/// 붙여넣기가 안 되는 이유를 찾을 수 없게 된다. 서버 페이스트 버퍼(`set_buffer`)는 이
/// 함수와 무관하게 별도로 채워지므로, 실패해도 **pytmux 안에서의** 붙여넣기는 된다.
pub fn copy(text: &str) -> bool {
    if text.is_empty() {
        return false;
    }
    // Windows 는 코드페이지 무관 경로를 **먼저** 시도한다(아래 `win_copy` 참조).
    #[cfg(windows)]
    {
        if win_copy(text) {
            return true;
        }
    }
    // 파이썬 클라와 같은 순서·같은 도구다. 없는 도구는 실행이 `NotFound` 로 떨어져
    // 그대로 다음으로 넘어간다 — `which` 를 흉내 내지 않는 이유는, 찾기와 실행 사이에
    // 지워질 수 있는 것을 두 번 묻지 않기 위해서다.
    for argv in [
        &["pbcopy"][..],
        &["xclip", "-selection", "clipboard"][..],
        &["wl-copy"][..],
        // Windows clip.exe — ASCII 만 정확하다. 위 PowerShell 경로가 실패했을 때의 폴백.
        &["clip"][..],
    ] {
        if feed(argv, text.as_bytes(), TOOL_TIMEOUT) == Some(true) {
            return true;
        }
    }
    false
}

/// Windows: PowerShell `Set-Clipboard` 로 **유니코드-안전** 복사.
///
/// `clip.exe` 는 표준입력을 **콘솔 코드페이지**로 해석한다(한국어 Windows = cp949). UTF-8
/// 바이트를 그대로 주면 한글이 깨진다 — 파이썬 클라가 실제로 겪은 결함이다(제보
/// 2026-07-13: '그림자 샤미' → '洹몃┝???ㅻ?'). 그래서 UTF-16LE → base64(순수 ASCII)로
/// 감싸 넘긴다. base64 는 <128 바이트뿐이라 어떤 코드페이지로 (역)해석돼도 무손실이다.
///
/// `Set-Clipboard` 실패(다른 앱이 클립보드를 쥐고 있는 흔한 상황)는 **비종결 오류**라
/// 그냥 두면 PowerShell 이 0 으로 끝나 성공으로 읽힌다 → `-ErrorAction Stop` + `catch`
/// 로 종료코드에 드러내야 위의 `clip.exe` 폴백이 돈다.
#[cfg(windows)]
fn win_copy(text: &str) -> bool {
    const SCRIPT: &str = "$b=[Console]::In.ReadToEnd();\
                          try{Set-Clipboard -Value ([Text.Encoding]::Unicode.GetString(\
                          [Convert]::FromBase64String($b))) -ErrorAction Stop}catch{exit 1}";
    let payload = base64(&utf16le(text));
    feed(
        &["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
        payload.as_bytes(),
        PS_TIMEOUT,
    ) == Some(true)
}

/// 자식을 띄워 표준입력으로 먹이고 끝나기를 기다린다.
///
/// - `None` — 도구가 없거나 띄우지 못했다(다음 후보로).
/// - `Some(false)` — 띄웠지만 실패했거나 제한 시간을 넘겼다.
/// - `Some(true)` — 0 으로 끝났다.
///
/// **제한 시간이 필요한 이유**: `xclip` 은 선택을 넘겨줄 때까지 살아 있는 도구라 상황에
/// 따라 안 끝난다. 무한정 기다리면 이 스레드가 영영 안 돌아오고, 그런 스레드가 드래그
/// 한 번마다 하나씩 쌓인다.
fn feed(argv: &[&str], input: &[u8], limit: Duration) -> Option<bool> {
    // ⛔ 이름을 그대로 넘기지 않는다 — Windows 에서 **이진 옆 폴더**가 시스템 디렉터리보다
    //    먼저 잡힌다([`system_tool`]). 후보 목록은 이름으로 두고(파이썬 클라와 같은
    //    순서를 읽히게) 바꾸는 자리는 **띄우는 여기 한 곳**이다.
    // 못 찾으면 **안 띄운다**(검수 2026-09-05 G-5) — `None` 은 이 함수의 「도구가
    // 없다」와 같은 뜻이라, 부르는 쪽은 그대로 다음 후보로 간다.
    let mut cmd = Command::new(system_tool(argv[0])?);
    cmd.args(&argv[1..])
        .stdin(Stdio::piped())
        // 도구가 뱉는 오류 문구가 **대체 화면에 그대로 찍히면** TUI 가 깨진다.
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        // CREATE_NO_WINDOW — 안 주면 clip.exe·PowerShell 콘솔 창이 번쩍인다
        // (파이썬 클라의 `proc.no_window_kwargs()` 와 같은 이유).
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000);
    }
    let mut child = cmd.spawn().ok()?;
    // 먹이다 실패해도(자식이 먼저 죽었다) 아래에서 종료코드로 판정한다 — 여기서
    // 돌아가 버리면 자식이 좀비로 남는다.
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(input);
        // **닫아야** 자식이 EOF 를 본다. drop 이 하는 일이지만, 여기서는 아래 대기가
        // 시작되기 전에 확실히 닫혀야 하므로 명시적으로 떨군다.
        drop(stdin);
    }
    let deadline = Instant::now() + limit;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(status.success()),
            Ok(None) => {}
            Err(_) => return Some(false),
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            // 죽인 자식도 거둔다 — 안 그러면 유닉스에서 좀비가 남는다.
            let _ = child.wait();
            return Some(false);
        }
        std::thread::sleep(POLL);
    }
}

/// UTF-16LE 바이트열. PowerShell 이 `[Text.Encoding]::Unicode` 로 되돌린다.
///
/// 짝 없는 서로게이트는 Rust `str` 에 존재할 수 없으므로 파이썬 쪽의 `errors="replace"`
/// 에 해당하는 처리가 필요 없다(타입이 이미 막고 있다).
#[cfg(windows)]
fn utf16le(text: &str) -> Vec<u8> {
    text.encode_utf16()
        .flat_map(|unit| unit.to_le_bytes())
        .collect()
}

/// base64(표준 알파벳, 패딩 있음. RFC 4648).
///
/// `proto::command::b64_encode` 와 **같은 표를 두 번 적은 것**이 맞다.
/// 합치려면 이 크레이트가 proto 를 의존해야 하는데, 그러면 "클립보드가 서버 프로토콜을
/// 안다"가 되어 이 크레이트의 의존 0개라는 성질(위 모듈 주석)이 깨진다. 20줄짜리 표는
/// RFC 가 고정한 것이라 갈라질 여지가 없고, 아래 테스트가 표준 벡터로 못박는다.
#[cfg(windows)]
fn base64(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(ALPHABET[((n >> (18 - 6 * i)) & 0x3f) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_text_never_reaches_a_tool() {
        // 빈 선택(공백만 끌었다)까지 프로세스를 띄우면 드래그마다 PowerShell 이 뜬다.
        assert!(!copy(""));
    }

    #[test]
    fn an_unknown_mime_never_becomes_a_file() {
        // ⛔ 이름만 `.png` 로 붙여 두면 앱이 열다 실패하고, 그 실패는 "붙여넣기가 안 된다"가
        //    아니라 **"그림이 깨졌다"** 로 읽힌다 — 원인을 짚을 수 없는 자리가 된다.
        assert_eq!(image_extension("image/bmp"), None);
        assert_eq!(image_extension("text/plain"), None);
        assert_eq!(image_extension(""), None);
        assert_eq!(save_image(b"fake-image-bytes", "image/bmp"), None);
    }

    #[test]
    fn the_extension_follows_the_mime_not_the_bytes() {
        // 창 계층은 원본 형식을 보존해서 주기도 한다 — 전부 `.png` 로 적으면 확장자가
        // 거짓말이 된다.
        assert_eq!(image_extension("image/png"), Some("png"));
        assert_eq!(image_extension("image/jpeg"), Some("jpg"));
        assert_eq!(image_extension("image/jpg"), Some("jpg"));
        assert_eq!(image_extension("image/gif"), Some("gif"));
        assert_eq!(image_extension("image/webp"), Some("webp"));
        // 대문자·여백으로 오는 값도 같은 것이다(MIME 은 대소문자를 안 가린다).
        assert_eq!(image_extension(" IMAGE/PNG "), Some("png"));
    }

    #[test]
    fn an_empty_image_never_becomes_a_file() {
        // 0바이트 그림을 붙이면 앱이 "깨진 파일"이라 말하고, 사용자는 pytmux 가 망친 줄 안다.
        assert_eq!(save_image(b"", "image/png"), None);
    }

    #[test]
    fn a_saved_image_lands_where_this_box_keeps_temp_files() {
        // ★ 잃어버린 앞 구현(CL 71659)은 `/tmp/pytmux_…` 를 박아 뒀고, 그 자리는 Windows 에
        //   없다 — 제보자의 상자가 바로 Windows 였다. 이 시험이 그 자리를 지킨다.
        let bytes = b"fake-png-bytes";
        let path = save_image(bytes, "image/png").expect("임시 파일을 못 만들었다");
        let path = std::path::PathBuf::from(path);
        assert_eq!(
            path.parent(),
            Some(std::env::temp_dir().as_path()),
            "임시 파일이 이 상자의 임시 자리에 안 생겼다: {path:?}"
        );
        assert_eq!(path.extension().and_then(|e| e.to_str()), Some("png"));
        assert_eq!(std::fs::read(&path).expect("못 읽었다"), bytes);
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn two_images_in_a_row_do_not_overwrite_each_other() {
        // 연달아 붙이면 시각이 같을 수 있다. 같은 이름이면 **앞 그림이 사라진다** —
        // 사용자는 방금 붙인 경로를 열었는데 다른 그림을 본다.
        let first = save_image(b"first-image-bytes", "image/png").expect("첫 장을 못 만들었다");
        let second = save_image(b"second-image-bytes", "image/png").expect("둘째 장을 못 만들었다");
        assert_ne!(first, second, "두 장이 같은 파일을 썼다");
        assert_eq!(std::fs::read(&first).unwrap(), b"first-image-bytes");
        assert_eq!(std::fs::read(&second).unwrap(), b"second-image-bytes");
        let _ = std::fs::remove_file(&first);
        let _ = std::fs::remove_file(&second);
    }

    #[test]
    fn a_hostile_host_never_reaches_scp() {
        // `host` 는 SSH config 별칭이라 임의 문자열이다. 앞이 `-` 면 scp 의 **플래그**가
        // 되고, 공백이 든 것은 호스트 이름이 아니다 — 둘 다 띄우기 전에 거절한다.
        assert!(!scp_to_remote("-oProxyCommand=touch /tmp/pwned", "/a", "/b"));
        assert!(!scp_to_remote("host with spaces", "/a", "/b"));
        assert!(!scp_to_remote("", "/a", "/b"));
    }

    #[test]
    fn a_missing_tool_is_skipped_not_reported_as_success() {
        // 없는 도구는 `None` 이라야 다음 후보로 넘어간다. `Some(false)` 로 읽으면
        // 첫 후보가 없는 상자(= 리눅스의 pbcopy)에서 복사가 통째로 죽는다.
        assert_eq!(
            feed(&["pytmux-no-such-clipboard-tool"], b"x", TOOL_TIMEOUT),
            None
        );
    }

    #[cfg(windows)]
    #[test]
    fn base64_matches_the_reference_vectors() {
        // 이 값이 어긋나면 PowerShell 이 되돌린 문자열이 조용히 깨진다 — 증상은
        // "복사는 됐는데 붙여넣으면 다른 글자"라 원인을 짚기 어렵다.
        assert_eq!(base64(b""), "");
        assert_eq!(base64(b"f"), "Zg==");
        assert_eq!(base64(b"fo"), "Zm8=");
        assert_eq!(base64(b"foo"), "Zm9v");
        assert_eq!(base64(b"foob"), "Zm9vYg==");
        assert_eq!(base64(b"fooba"), "Zm9vYmE=");
        assert_eq!(base64(b"foobar"), "Zm9vYmFy");
    }

    #[cfg(windows)]
    #[test]
    fn korean_survives_the_utf16_round_trip() {
        // cp949 상자에서 깨졌던 바로 그 글자다. UTF-16LE 는 한글 한 자가 2바이트다.
        assert_eq!(utf16le("가"), vec![0x00, 0xac]);
        assert_eq!(utf16le("A"), vec![0x41, 0x00]);
    }
}

/// 로컬 파일을 `scp` 로 원격 호스트에 복사한다. 성공하면 `true`.
///
/// 정본 `pytmuxlib/clientclip.py::scp_to_remote` 와 **같은 인자·같은 옵션**이다. 원격 탭에
/// 그림을 붙일 때 쓴다 — [`save_image`] 가 떨군 파일은 **이 상자**에 생기므로 원격 셸에는
/// 그 경로가 없다. 그대로 붙이면 앱이 "그런 파일 없음"이라 말하고, 사용자는 붙여넣기가
/// 깨진 줄 안다.
///
/// # 왜 argv 형인가
///
/// `host` 는 SSH config 별칭이라 **임의 문자열**일 수 있다. 셸 한 줄로 지으면 거기서
/// 인젝션이 열린다. 앞이 `-` 인 값과 공백이 든 값은 아예 거절한다 — 전자는 scp 의 플래그가
/// 되고(`--` 뒤에 두어도 `host:` 자리는 그 앞이다), 후자는 호스트 이름이 아니다.
///
/// # 왜 `BatchMode` 인가
///
/// 암호를 물으면 **영영 안 끝난다**. `remote-attach` 가 이미 키 인증을 지난 호스트라
/// 물을 일이 없고, 묻는 상황이라면 그건 실패로 접는 것이 맞다.
///
/// # 막힌다
///
/// 클립보드와 같다 — 호출부가 **별도 스레드**에서 부르는 것을 전제한다(상한 30초는
/// 정본과 같은 값이다).
pub fn scp_to_remote(host: &str, local_path: &str, remote_path: &str) -> bool {
    if host.is_empty() || host.starts_with('-') || host.chars().any(char::is_whitespace) {
        return false;
    }
    let dest = format!("{host}:{remote_path}");
    let argv = [
        "scp",
        "-B",
        "-q",
        "-o",
        "BatchMode=yes",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "--",
        local_path,
        &dest,
    ];
    // 먹일 것이 없으니 빈 표준입력이다 — `feed` 가 자식을 띄우고 상한까지 기다리는
    // 규칙을 이미 들고 있어(창 안 띄우기·죽인 자식 거두기) 여기서 다시 적지 않는다.
    feed(&argv, b"", SCP_TIMEOUT) == Some(true)
}

/// `scp` 한 번의 상한. 정본 `scp_to_remote` 의 `timeout=30` 과 같은 값이다.
const SCP_TIMEOUT: Duration = Duration::from_secs(30);

/// 셸 명령 하나를 돌리고 `(종료코드, stdout+stderr)` 를 돌려준다(`run-shell`·`if-shell`).
///
/// # 왜 여기인가
///
/// 이 크레이트가 이미 **외부 프로세스를 부르는 유일한 자리**다(클립보드 도구). 프로세스를
/// 띄우는 규칙(OS 별 셸·시간 상한·창 안 띄우기)이 두 곳에 생기면 한쪽만 고쳐지는 날이 온다.
///
/// # 셸은 파이썬과 같은 것을 쓴다
///
/// POSIX `/bin/sh -c`, Windows `%COMSPEC%`(기본 `cmd`) `/c`. 사용자가 적은 명령이 두
/// 클라에서 다르게 해석되면 그게 곧 결함이다.
///
/// # 막힌다
///
/// 클립보드와 같다 — 호출부가 **별도 스레드**에서 부르는 것을 전제한다. 상한(15초)은
/// 파이썬 `_run_shell` 과 같은 값이다.
pub fn run_shell(cmd: &str) -> (i32, String) {
    let Some(argv) = shell_argv(cmd) else {
        // 셸의 자리를 못 찾았다 — **이름으로 되돌아가지 않는다**(검수 2026-09-05 G-5).
        // 이진 옆 폴더의 `cmd.exe` 가 먼저 잡히는 그 구멍을 여기서 다시 열지 않는다.
        return (1, "셸을 찾지 못했다(COMSPEC 도 %SystemRoot% 도 안 잡힌다)".to_owned());
    };
    let mut child = match Command::new(&argv[0])
        .args(&argv[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        // 셸조차 못 띄운 것은 **사용자에게 보여야 한다** — 조용히 rc 1 만 돌려주면
        // "명령이 실패했다"와 "셸이 없다"를 못 가린다.
        Err(err) => return (1, format!("셸을 띄우지 못했다: {err}")),
    };
    let deadline = Instant::now() + SHELL_TIMEOUT;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(POLL),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return (1, format!("시간이 다 됐다({}초)", SHELL_TIMEOUT.as_secs()));
            }
            Err(err) => return (1, format!("기다리지 못했다: {err}")),
        }
    }
    match child.wait_with_output() {
        Ok(out) => {
            let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
            // stderr 도 싣는다 — 실패한 명령은 거기에만 말한다.
            let err = String::from_utf8_lossy(&out.stderr);
            if !err.trim().is_empty() {
                if !text.is_empty() && !text.ends_with('\n') {
                    text.push('\n');
                }
                text.push_str(&err);
            }
            (out.status.code().unwrap_or(1), text)
        }
        Err(err) => (1, format!("출력을 읽지 못했다: {err}")),
    }
}

/// 셸 명령의 상한. 파이썬 `_run_shell`·`_if_shell` 과 같은 값이다.
const SHELL_TIMEOUT: Duration = Duration::from_secs(15);

/// OS 별 셸 argv(파이썬 `proc.shell_argv` 와 같다).
fn shell_argv(cmd: &str) -> Option<Vec<String>> {
    if cfg!(windows) {
        // `COMSPEC` 이 있으면 그것(사용자가 고른 셸이고 보통 절대경로다). 없을 때의
        // 폴백은 **이름 `cmd` 가 아니라 절대경로**여야 한다 — 이름이면 이진 옆 폴더의
        // `cmd.exe` 가 먼저 잡힌다([`system_tool`] · 검수 2026-08-09 B-3). 그 절대경로
        // 마저 못 지으면 `None` — 이름으로 되돌아가면 그 구멍이 그대로 열린다.
        let comspec = match std::env::var("COMSPEC") {
            Ok(v) => v,
            Err(_) => system_tool("cmd")?,
        };
        Some(vec![comspec, "/c".to_owned(), cmd.to_owned()])
    } else {
        Some(vec!["/bin/sh".to_owned(), "-c".to_owned(), cmd.to_owned()])
    }
}

#[cfg(test)]
mod shell_tests {
    use super::*;

    #[test]
    fn a_command_that_prints_gives_us_its_output() {
        let (rc, text) = run_shell("echo hello");
        assert_eq!(rc, 0);
        assert!(text.contains("hello"), "{text:?}");
    }

    #[test]
    fn a_failing_command_says_so_in_the_code() {
        // ★ `if-shell` 이 이 값으로 갈린다 — 0 이 아닌데 0 을 돌려주면 조건이 뒤집힌다.
        let (rc, _) = run_shell("exit 3");
        assert_eq!(rc, 3);
    }

    #[test]
    fn stderr_is_not_thrown_away() {
        // 실패한 명령은 stderr 에만 말한다 — 버리면 왜 실패했는지 알 수 없다.
        let cmd = if cfg!(windows) { "echo oops 1>&2" } else { "echo oops >&2" };
        let (_, text) = run_shell(cmd);
        assert!(text.contains("oops"), "{text:?}");
    }
}
