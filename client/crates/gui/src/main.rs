//! pytmux 네이티브 클라이언트 — GUI 이진.
//!
//! ```sh
//! cargo run -p gui                    # 서버를 찾아 붙고, 없으면 블록 데모
//! cargo run -p gui -- --socket <경로>  # 그 엔드포인트로만 붙는다
//! cargo run -p gui -- demo            # 항상 블록 데모
//! ```
//!
//! # 어디까지 따라왔나
//!
//! **P2(화면 동등성)까지다.** 서버에 붙어 캔버스를 그린다. 탭바는 P3, 블록은 P4, Claude
//! 구역은 P5, 입력·마우스·복사는 P7 이고 TUI 가 이미 밟은 자리를 슬라이스마다 따라간다.
//! 한 슬라이스에 다 넣으면 무엇이 깨졌는지 가릴 수 없다(설계문서 §7 「GUI 따라붙이기」).
//!
//! macOS 에서는 빌드에 Xcode + Metal 툴체인이 필요하다(PROVENANCE.md §6). Windows 는
//! wgpu 의 dx12 를 타므로 그냥 빌드된다(§9-5 정정, p4 67739).

use std::borrow::Cow;
use std::time::Duration;

use anyhow::{Result, anyhow};
use proto::ServerLink;
use warpui::{AssetProvider, platform};

mod mono_font;
mod root_view;
mod session_view;
mod ime;
mod splitter;
mod theme;

/// 서버 메시지를 퍼올리는 간격.
///
/// TUI 는 이벤트 루프가 매 프레임 채널을 훑지만(`run_until`), GUI 에는 그 자리에 해당하는
/// 루프가 없다 — 대신 주기 작업을 `ctx.spawn` 으로 띄워 뷰로 돌아온다. 30Hz 는 서버가
/// 프레임을 미는 속도와 같다(`FLUSH_HZ`) — 더 자주 깨워도 새 프레임이 없다.
const PUMP: Duration = Duration::from_millis(33);

/// 번들 에셋 없음.
///
/// 이 클라이언트는 시스템 고정폭 글꼴만 쓰고 SVG 아이콘을 아직 안 쓴다. 에셋이 필요해지면
/// (P3 의 탭 아이콘 등) `rust_embed` 로 갈아끼운다. 그때까지는 "없다"를 정직하게 말한다 —
/// 빈 바이트를 돌려주면 로더가 깨진 리소스를 조용히 그린다.
struct NoAssets;

impl AssetProvider for NoAssets {
    fn get(&self, path: &str) -> Result<Cow<'_, [u8]>> {
        Err(anyhow!("번들 에셋이 없다 (요청: {path})"))
    }
}

/// 명령줄로 요청된 것. TUI 이진과 **같은 표기**다 — 두 이진이 다른 인자를 받으면
/// 사용자가 어느 쪽을 쓰는지 기억해야 한다.
#[derive(Debug, PartialEq)]
enum Args {
    Demo,
    /// `Some` 이면 **그 엔드포인트로만** 붙는다(자동 탐색 안 함).
    Attach(Option<String>),
    Usage,
    Bad(String),
}

const USAGE: &str = "\
pytmux 네이티브 클라이언트(GUI)

  pytmux-gui                  떠 있는 서버를 찾아 붙는다
  pytmux-gui --socket <경로>  그 엔드포인트로만 붙는다(`tcp:host:port` 도 된다)
  pytmux-gui demo             서버 없이 블록 데모
  pytmux-gui --frame-dump=<png>  몇 초 뒤 첫 화면을 PNG 로 덤프하고 끝낸다(확인용)
  pytmux-gui --frame-keys=<키들>  덤프 전에 키를 넣는다(예: esc,:,s,p,l — 확인용)
";

/// `--frame-dump[=<path>]` 를 골라내고 나머지 인자를 돌려준다.
///
/// # 왜 화면 캡처가 아니라 자가 덤프인가
///
/// 이 저장소의 라이브 확인은 "창 안의 그림"을 봐야 하는데, macOS 에서 에이전트 셸은
/// **Background launchd 세션**이라(2026-07-30 실측 — `launchctl managername`) 여기서
/// 띄운 창은 사용자의 Aqua 세션 화면에 컴포지트되지 않고, `screencapture` 도 그
/// 별세계를 찍는다 — 창이 멀쩡히 그려져도 "안 그려진다"로 오판하게 된다(G9i 가 정확히
/// 그 자리에 섰다). 드로어블 텍스처를 **앱이 직접 읽으면**(warpui `request_frame_capture`)
/// 세션도 화면 기록 권한도 필요 없다. Windows 하네스의 `PrintWindow` 함정(까만 사각형을
/// 성공으로 돌려준다)과 같은 부류의 답이다 — 화면이 아니라 원본에서 뜬다.
fn take_frame_dump(
    argv: impl IntoIterator<Item = String>,
) -> (Option<String>, Option<String>, Vec<String>) {
    let mut dump = None;
    let mut keys = None;
    let mut rest = Vec::new();
    let mut it = argv.into_iter();
    while let Some(arg) = it.next() {
        if arg == "--frame-dump" {
            dump = it.next();
        } else if let Some(value) = arg.strip_prefix("--frame-dump=") {
            dump = Some(value.to_owned());
        } else if arg == "--frame-keys" {
            keys = it.next();
        } else if let Some(value) = arg.strip_prefix("--frame-keys=") {
            keys = Some(value.to_owned());
        } else {
            rest.push(arg);
        }
    }
    (dump, keys, rest)
}

/// 덤프까지 기다리는 시간 — 서버 첫 프레임(layout·screen·status)이 붙고도 남는 값.
const FRAME_DUMP_DELAY: Duration = Duration::from_secs(4);

/// `--frame-keys` 하나를 읽는다 — 쉼표로 가른 토큰이고, 낱글자는 그 글자,
/// `esc`·`enter`·`tab`·`up`·`down`·`left`·`right`·`space` 는 그 키,
/// `ctrl-<글자>` 는 조합이다. 모르는 토큰은 버린다(하네스라 관대하게).
///
/// 덤프가 첫 화면만 찍을 수 있으면 팔레트·설정 같은 **키 뒤의 그림**은 영영 못
/// 찍는다 — 맥에서는 창에 키를 넣을 길이 없기 때문이다(Background 세션 —
/// `take_frame_dump` 문서). 그래서 키를 앱 안에서 넣는다(오라클과 같은 경로).
fn parse_frame_keys(spec: &str) -> Vec<(base::Key, base::Mods)> {
    // 토큰 표는 core 의 것이다(계층 규칙 — 키 정의는 한 곳). 여기는 쉼표만 가른다.
    spec.split(',')
        .filter_map(base::keys::parse_token)
        .collect()
}

/// 잡은 프레임을 PNG 로 남긴다. 실패는 stderr 로 — 하네스가 rc 로 판정한다.
fn save_frame(mut frame: warpui_core::platform::CapturedFrame, path: &str) {
    frame.ensure_rgba();
    let ok = image::save_buffer(
        path,
        &frame.data,
        frame.width,
        frame.height,
        image::ColorType::Rgba8,
    );
    match ok {
        Ok(()) => {
            println!("frame-dump: {path} ({}x{})", frame.width, frame.height);
            std::process::exit(0);
        }
        Err(e) => {
            eprintln!("frame-dump 실패: {e}");
            std::process::exit(1);
        }
    }
}

fn parse_args(argv: impl IntoIterator<Item = String>) -> Args {
    let mut socket = None;
    let mut demo = false;
    let mut it = argv.into_iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "demo" => demo = true,
            "-h" | "--help" => return Args::Usage,
            "--socket" => match it.next() {
                Some(value) => socket = Some(value),
                None => return Args::Bad("--socket 뒤에 경로가 없다".into()),
            },
            other => match other.strip_prefix("--socket=") {
                Some(value) => socket = Some(value.to_owned()),
                None => return Args::Bad(format!("모르는 인자: {other}")),
            },
        }
    }
    if demo {
        return Args::Demo;
    }
    Args::Attach(socket)
}

fn main() -> Result<()> {
    // 로거가 없으면 `log::info!` 는 **어디로도 안 간다** — 진단을 남긴 줄 알고
    // 아무것도 안 남긴 상태가 된다. 기본은 조용하고(`RUST_LOG` 미설정 시 warn
    // 이상), 필요할 때 `RUST_LOG=info` 로 켠다.
    env_logger::init();
    let (frame_dump, frame_keys, argv) = take_frame_dump(std::env::args().skip(1));
    // **지정된 엔드포인트는 폴백하지 않는다** — 지목받은 곳에 못 붙었는데 데모가 뜨면
    // 사용자는 "붙었다"고 읽는다(TUI 이진과 같은 규칙).
    let link = match parse_args(argv) {
        Args::Usage => {
            print!("{USAGE}");
            return Ok(());
        }
        Args::Bad(reason) => {
            eprintln!("pytmux-gui: {reason}\n\n{USAGE}");
            std::process::exit(2);
        }
        Args::Demo => None,
        Args::Attach(Some(spec)) => match ServerLink::attach_to(&spec, 80, 24) {
            Ok(link) => Some(link),
            Err(e) => {
                eprintln!("pytmux-gui: {spec} 에 붙지 못했다: {e}");
                std::process::exit(1);
            }
        },
        Args::Attach(None) => match ServerLink::attach(80, 24) {
            Ok(link) => Some(link),
            Err(e) => {
                eprintln!("서버에 붙지 못해 블록 데모로 시작한다: {e}");
                None
            }
        },
    };

    // 로케일은 **첫 렌더 전에** 정해져 있어야 한다(영속 `.lang` > 설정 `lang` > 환경 —
    // `base::i18n` 모듈 문서). 영속 파일 자리는 붙은 엔드포인트에서 나온다: 데모(링크
    // 없음)면 영속 없이 설정·환경만 본다.
    let lang_path = link
        .as_ref()
        .map(|l| proto::endpoint::parse(l.socket()).lang_file());
    let config_lang = base::Config::load().lang;
    base::i18n::init(
        lang_path,
        (!config_lang.is_empty()).then_some(config_lang.as_str()),
    );

    let app_builder =
        platform::AppBuilder::new(platform::AppCallbacks::default(), Box::new(NoAssets), None);

    let _ = app_builder.run(move |ctx| {
        // core 의 키 바인딩 표를 GUI 키맵에 등록한다(뷰를 만들기 전에).
        root_view::init(ctx);
        match link {
            Some(link) => {
                let frame_dump = frame_dump.clone();
                ctx.add_window(warpui::AddWindowOptions::default(), move |ctx| {
                    // 서버 메시지를 주기적으로 퍼올린다. GUI 에는 TUI 의 `run_until` 에
                    // 해당하는 자리가 없어, 대신 주기 작업이 뷰로 돌아온다.
                    let spawner = ctx.spawner();
                    ctx.spawn(
                        async move {
                            loop {
                                warpui::r#async::Timer::after(PUMP).await;
                                let go = spawner
                                    .spawn(|view: &mut session_view::SessionView, ctx| {
                                        if view.pump(ctx) {
                                            ctx.notify();
                                        }
                                        // 끊긴 화면을 한 번은 보여 주고 멈춘다 — 아무
                                        // 설명 없이 얼어붙으면 사용자는 무슨 일이 났는지
                                        // 모른다.
                                        !view.is_ended()
                                    })
                                    .await;
                                if !matches!(go, Ok(true)) {
                                    break;
                                }
                            }
                        },
                        |_, _, _| {},
                    );
                    // 라이브 확인 하네스(`take_frame_dump` 문서) — 첫 프레임들이 붙은
                    // 뒤 드로어블을 뜨고 끝낸다.
                    if let Some(path) = frame_dump.clone() {
                        let spawner = ctx.spawner();
                        let keys = frame_keys.clone().map(|s| parse_frame_keys(&s));
                        ctx.spawn(
                            async move {
                                warpui::r#async::Timer::after(FRAME_DUMP_DELAY).await;
                                // 키를 먼저 넣는다(`parse_frame_keys` 문서) — 오라클과
                                // 같은 경로(handle_key)라, 찍히는 그림이 곧 사용자가
                                // 그 키로 볼 그림이다.
                                if let Some(keys) = keys {
                                    let _ = spawner
                                        .spawn(move |view: &mut session_view::SessionView, ctx| {
                                            for (key, mods) in keys {
                                                view.handle_key(key, mods);
                                                view.pump(ctx);
                                            }
                                            ctx.notify();
                                        })
                                        .await;
                                    // 키가 만든 상태(서버 왕복 포함)가 그려질 틈을 준다.
                                    warpui::r#async::Timer::after(Duration::from_secs(2)).await;
                                }
                                let _ = spawner
                                    .spawn(move |_: &mut session_view::SessionView, ctx| {
                                        let id = ctx.window_id();
                                        if let Some(window) = ctx.windows().platform_window(id) {
                                            window.as_ctx().request_frame_capture(Box::new(
                                                move |frame| save_frame(frame, &path),
                                            ));
                                        }
                                    })
                                    .await;
                            },
                            |_, _, _| {},
                        );
                    }
                    session_view::SessionView::new(link, ctx)
                });
            }
            None => {
                let frame_dump = frame_dump.clone();
                ctx.add_window(warpui::AddWindowOptions::default(), move |ctx| {
                    // 데모 창도 같은 하네스를 받는다 — 서버 없이 순수 그리기를 확인하는 판.
                    if let Some(path) = frame_dump.clone() {
                        let spawner = ctx.spawner();
                        ctx.spawn(
                            async move {
                                warpui::r#async::Timer::after(FRAME_DUMP_DELAY).await;
                                let _ = spawner
                                    .spawn(move |_: &mut root_view::RootView, ctx| {
                                        let id = ctx.window_id();
                                        if let Some(window) = ctx.windows().platform_window(id) {
                                            window.as_ctx().request_frame_capture(Box::new(
                                                move |frame| save_frame(frame, &path),
                                            ));
                                        }
                                    })
                                    .await;
                            },
                            |_, _, _| {},
                        );
                    }
                    root_view::RootView::new(ctx)
                });
            }
        }
    });

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> Args {
        parse_args(list.iter().map(|s| (*s).to_owned()))
    }

    #[test]
    fn the_two_binaries_take_the_same_arguments() {
        // 두 이진이 다른 인자를 받으면 사용자가 어느 쪽을 쓰는지 기억해야 한다.
        assert_eq!(args(&[]), Args::Attach(None));
        assert_eq!(args(&["demo"]), Args::Demo);
        let want = Args::Attach(Some("/tmp/p.sock".into()));
        assert_eq!(args(&["--socket", "/tmp/p.sock"]), want);
        assert_eq!(args(&["--socket=/tmp/p.sock"]), want);
        assert_eq!(args(&["--help"]), Args::Usage);
    }

    #[test]
    fn an_unknown_argument_is_refused_instead_of_being_ignored() {
        // 조용히 무시하면 `--socket` 오타가 **다른 서버에 붙는** 것으로 나타난다.
        assert!(matches!(args(&["--sockett=/x"]), Args::Bad(_)));
        assert!(matches!(args(&["--socket"]), Args::Bad(_)));
    }

    #[test]
    fn frame_keys_tokens_map_to_the_documented_keys() {
        use base::{Key, Mods};
        let keys = parse_frame_keys("esc,:,s,ctrl-b,%,enter,tab,up,space,insert,잘못된토큰");
        assert_eq!(
            keys,
            vec![
                (Key::Escape, Mods::NONE),
                (Key::Char(':'), Mods::NONE),
                (Key::Char('s'), Mods::NONE),
                (Key::Char('b'), Mods::CTRL),
                (Key::Char('%'), Mods::NONE),
                (Key::Enter, Mods::NONE),
                (Key::Tab, Mods::NONE),
                (Key::Up, Mods::NONE),
                (Key::Char(' '), Mods::NONE),
                (Key::Insert, Mods::NONE),
            ],
            "모르는 토큰은 버리고 나머지는 문서의 표 그대로다"
        );
    }

    #[test]
    fn frame_dump_is_peeled_off_before_normal_parsing() {
        // 하네스 플래그가 평소 인자와 섞여도 `parse_args` 는 그것을 모른 채 지나간다 —
        // 두 이진이 같은 인자를 받는다는 규칙(위 테스트)을 안 건드리기 위해서다.
        let take = |list: &[&str]| take_frame_dump(list.iter().map(|s| (*s).to_owned()));
        let (dump, keys, rest) = take(&["--frame-dump=/tmp/a.png", "--frame-keys=esc,:", "--socket=/x"]);
        assert_eq!(dump.as_deref(), Some("/tmp/a.png"));
        assert_eq!(keys.as_deref(), Some("esc,:"));
        assert_eq!(rest, vec!["--socket=/x".to_owned()]);
        let (dump, _, rest) = take(&["--frame-dump", "/tmp/b.png", "demo"]);
        assert_eq!(dump.as_deref(), Some("/tmp/b.png"));
        assert_eq!(rest, vec!["demo".to_owned()]);
        let (dump, keys, rest) = take(&["demo"]);
        assert_eq!(dump, None);
        assert_eq!(keys, None);
        assert_eq!(rest, vec!["demo".to_owned()]);
    }
}
