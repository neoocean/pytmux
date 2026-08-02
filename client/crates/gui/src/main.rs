// ★ **GUI 서브시스템으로 링크한다**(Windows). 안 걸면 이진이 콘솔 서브시스템이라
//   탐색기·바로가기로 띄울 때 Windows 가 먼저 검은 cmd 창을 붙여 주고 그 뒤에 창이
//   뜬다(사용자 실측 2026-08-02 · §10-20ⓒ). 짝은 `console::attach_parent()` 다 —
//   터미널에서 띄웠을 때는 부모 콘솔에 붙어 `--help`·실패 사유가 종전대로 보인다.
//   `not(test)` 인 이유: 이 속성은 **테스트 이진에도** 걸려, 그러면 `cargo test` 의
//   출력이 통째로 사라진다(초록/빨강을 못 보는 것이 결함보다 비싸다).
#![cfg_attr(all(windows, not(test)), windows_subsystem = "windows")]

//! pytmux 네이티브 클라이언트 — GUI 이진.
//!
//! ```sh
//! cargo run -p gui                    # 서버를 찾아 붙는다(없으면 정본으로 띄우고 붙는다)
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

mod console;
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

  pytmux-gui                  떠 있는 서버에 붙는다(없으면 서버를 띄우고 붙는다)
  pytmux-gui --socket <경로>  그 엔드포인트로만 붙는다(`tcp:host:port` 도 된다)
  pytmux-gui demo             서버 없이 블록 데모
  pytmux-gui --frame-dump=<png>  몇 초 뒤 첫 화면을 PNG 로 덤프하고 끝낸다(확인용)
  pytmux-gui --frame-keys=<키들>  덤프 전에 키를 넣는다(예: esc,:,s,p,l — 확인용)
                              `wait` 를 끼우면 거기서 서버 왕복을 기다린다
";

/// 인자 하나가 **어떤 시작**을 뜻하는가.
///
/// # 왜 갈라 두나
///
/// 이 갈림이 곧 제품의 성격이라 오라클이 붙잡고 있어야 한다. 실제로 여기가 결함이었다:
/// 인자 없이 띄우면 서버를 못 찾았을 때 **데모**로 갔고(§10-20ⓓ), 사용자는 자기 세션이
/// 사라진 화면을 봤다. 그 사유는 `eprintln!` 로만 났는데 GUI 서브시스템이 되면 그것도
/// 안 보인다 — "왜 데모인지 모른 채 데모를 본다"가 성립했다.
///
/// 이걸 `main` 안의 `match` 로 두면 되돌리는 변경이 **아무 테스트도 안 깨뜨린다**.
#[derive(Debug, PartialEq)]
enum Plan {
    /// 사용법을 찍고 정상 종료.
    Usage,
    /// 인자가 틀렸다.
    Bad(String),
    /// 서버 없이 블록 데모 — **명시 인자(`demo`)일 때만** 온다.
    Demo,
    /// 지목받은 곳에만 붙는다. 못 붙으면 실패다(폴백 없음).
    AttachTo(String),
    /// 찾아 붙고, 없으면 **띄우고** 붙는다(정본 `attach` 와 같은 모델).
    FindOrStart,
}

/// 창을 만들 때 쓰는 선택지 한 벌.
///
/// # 왜 OS 에게 맡기나 (§10-20ⓐ)
///
/// 종전에는 최소화·최대화·닫기 버튼이 **아예 없었다**. 상류가 창을 만들 때
/// `hide_title_bar` 를 `true` 로 박아 두고(`warpui_core` 의 `insert_window_internal`)
/// 그 칸을 밖에서 건드릴 길이 없었기 때문이다 — 자기 크롬을 다 그리는 앱(warp)의
/// 기본값이고, 우리는 그것을 물려받았다.
///
/// 우리가 그리는 길도 있었지만 **관습이 OS 마다 다르다**: 맥은 왼쪽 신호등 셋, Windows 는
/// 오른쪽 최소화·최대화·닫기. 자리를 한 벌로 박으면 한쪽 OS 사용자에게는 늘 어색하고,
/// 그 어색함을 고치는 값은 우리에게 없다. 그래서 `TitleBar::Native` 로 **OS 에게 맡긴다**.
///
/// 제목도 여기서 준다 — 안 주면 winit 기본값이 그대로 뜬다(실측: 제목줄이 `winit window`
/// 였다. 장식이 없던 동안에는 아무도 그 이름을 못 봤다).
fn window_options() -> warpui::AddWindowOptions {
    warpui::AddWindowOptions {
        title_bar: warpui::TitleBar::Native,
        title: Some("pytmux".to_owned()),
        ..Default::default()
    }
}

fn plan_for(args: Args) -> Plan {
    match args {
        Args::Usage => Plan::Usage,
        Args::Bad(reason) => Plan::Bad(reason),
        Args::Demo => Plan::Demo,
        Args::Attach(Some(spec)) => Plan::AttachTo(spec),
        Args::Attach(None) => Plan::FindOrStart,
    }
}

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
/// `wait` 는 키가 아니라 **한 배치의 끝**이다: 거기서 서버 왕복을 기다렸다 이어 넣는다.
/// 없으면 배치 하나다. 이것이 필요한 이유는 순서가 실제로 물리기 때문이다 — 팔레트로
/// 오버레이를 켠 **다음** 그 오버레이가 가져가는 키를 누르려면, 그 사이에 서버의 첫
/// 프레임이 와 있어야 한다(안 그러면 키가 그냥 셸로 간다 — 제품이 아니라 하네스의
/// 순서 때문에 "안 먹는다"로 보인다. 2026-08-02 달력 확인에서 실제로 그렇게 읽었다).
fn parse_frame_keys(spec: &str) -> Vec<Vec<(base::Key, base::Mods)>> {
    // 토큰 표는 core 의 것이다(계층 규칙 — 키 정의는 한 곳). 여기는 쉼표만 가른다.
    let mut batches = vec![Vec::new()];
    for token in spec.split(',') {
        if token == "wait" {
            batches.push(Vec::new());
        } else if let Some(key) = base::keys::parse_token(token) {
            batches.last_mut().expect("배치가 하나는 있다").push(key);
        }
    }
    batches
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
    // ★ **첫 줄이다.** 터미널에서 띄웠으면 부모 콘솔에 붙어, 아래의 사용법·오류·기동
    //   실패 사유가 종전처럼 보이게 한다. 러스트 std 가 표준 핸들을 첫 출력 때 캐시하므로
    //   한 줄이라도 먼저 내면 늦는다(`console` 모듈 문서).
    let has_console = console::attach_parent();
    // 로거가 없으면 `log::info!` 는 **어디로도 안 간다** — 진단을 남긴 줄 알고
    // 아무것도 안 남긴 상태가 된다. 기본은 조용하고(`RUST_LOG` 미설정 시 warn
    // 이상), 필요할 때 `RUST_LOG=info` 로 켠다.
    env_logger::init();
    // 시동 실패를 사용자에게 남긴다. 콘솔이 있으면 글로, 없으면(탐색기로 띄웠다)
    // 대화상자로 — **어느 쪽이든 사라지지는 않게**(`console::show_fatal` 문서).
    let die = |message: String, code: i32| -> ! {
        console::say(&format!("{message}\n"), true);
        if !has_console {
            console::show_fatal(&message);
        }
        std::process::exit(code)
    };
    let (frame_dump, frame_keys, argv) = take_frame_dump(std::env::args().skip(1));
    // **지정된 엔드포인트는 폴백하지 않는다** — 지목받은 곳에 못 붙었는데 데모가 뜨면
    // 사용자는 "붙었다"고 읽는다.
    let link = match plan_for(parse_args(argv)) {
        Plan::Usage => {
            console::say(USAGE, false);
            return Ok(());
        }
        Plan::Bad(reason) => die(format!("pytmux-gui: {reason}\n\n{USAGE}"), 2),
        Plan::Demo => None,
        Plan::AttachTo(spec) => match ServerLink::attach_to(&spec, 80, 24) {
            Ok(link) => Some(link),
            Err(e) => die(format!("pytmux-gui: {spec} 에 붙지 못했다: {e}"), 1),
        },
        // 정본 `attach` 와 같은 모델이다: 없으면 **띄우고** 붙는다. 실패는 실패로
        // 끝난다 — 데모로 떨어지면 사용자는 자기 세션이 사라졌다고 읽는다.
        Plan::FindOrStart => match proto::boot::attach_or_start(80, 24) {
            Ok(link) => Some(link),
            Err(e) => die(format!("pytmux-gui: {e}"), 1),
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
                ctx.add_window(window_options(), move |ctx| {
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
                                if let Some(batches) = keys {
                                    for batch in batches {
                                        let _ = spawner
                                            .spawn(move |view: &mut session_view::SessionView, ctx| {
                                                for (key, mods) in batch {
                                                    view.handle_key(key, mods);
                                                    view.pump(ctx);
                                                }
                                                ctx.notify();
                                            })
                                            .await;
                                        // 키가 만든 상태(서버 왕복 포함)가 그려질 틈을
                                        // 준다 — `wait` 로 가른 배치 사이에도 같은 틈이다.
                                        warpui::r#async::Timer::after(Duration::from_secs(2))
                                            .await;
                                    }
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
                ctx.add_window(window_options(), move |ctx| {
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
    fn no_arguments_means_start_the_server_not_show_a_demo() {
        // ★ §10-20ⓓ 가 여기였다. 인자 없이 띄웠을 때 데모로 가면 사용자는 자기 세션이
        //   사라졌다고 읽는다. 데모는 **명시 인자일 때만** 온다.
        assert_eq!(plan_for(args(&[])), Plan::FindOrStart);
        assert_eq!(plan_for(args(&["demo"])), Plan::Demo);
        // 지목받은 곳은 폴백하지 않는다 — 데모도, 새 서버도 아니다.
        assert_eq!(
            plan_for(args(&["--socket=/tmp/p.sock"])),
            Plan::AttachTo("/tmp/p.sock".into())
        );
        assert_eq!(plan_for(args(&["--help"])), Plan::Usage);
        assert!(matches!(plan_for(args(&["--nope"])), Plan::Bad(_)));
    }

    #[test]
    fn the_window_asks_the_os_to_draw_its_buttons() {
        // §10-20ⓐ: 종전에는 최소화·최대화·닫기가 없었다(상류가 `hide_title_bar` 를
        // 박아 뒀고 밖에서 건드릴 칸이 없었다). 기본값으로 되돌아가면 그 상태다.
        let options = window_options();
        assert_eq!(options.title_bar, warpui::TitleBar::Native);
        // 제목을 안 주면 winit 기본값(`winit window`)이 제목줄에 뜬다 — 장식이 없던
        // 동안에는 아무도 못 보던 이름이다.
        assert_eq!(options.title.as_deref(), Some("pytmux"));
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
            vec![vec![
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
            ]],
            "모르는 토큰은 버리고 나머지는 문서의 표 그대로다"
        );
        // `wait` 는 키가 아니라 **배치의 끝**이다 — 그 자리에서 서버 왕복을 기다린다.
        assert_eq!(
            parse_frame_keys("esc,wait,left"),
            vec![
                vec![(Key::Escape, Mods::NONE)],
                vec![(Key::Left, Mods::NONE)],
            ],
            "wait 가 배치를 가르지 않으면 순서를 못 만든다"
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
