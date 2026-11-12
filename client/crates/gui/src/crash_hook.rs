//! 크래시 자가 재기동(pytmux-639 G10) — 정본 `client.run_client` 의 `except Exception` 자리.
//!
//! 규칙(한도·창·환경 변수·글)은 [`base::crash`] 한 곳이고, 여기는 그것을 **패닉 훅**에 잇는다:
//!
//! - **메인 스레드의 패닉만** 크래시로 센다. 정본에서 크래시는 `app.run()` 밖으로 샌 예외(=
//!   이벤트 루프의 죽음)이고, 일 스레드(읽기 스레드 등)의 예외는 앱을 안 죽인다 — 우리도
//!   그런 패닉은 그 스레드만 끝내고 앱은 산다.
//! - 패닉이 `abort` 로 가는 자리(외부 콜백 안)라도 훅은 그 **전에** 돈다 — 같은 길을 탄다.
//! - 기록 자리는 정본과 같은 `<state_base>.client.crash.log`([`proto::endpoint::Endpoint::crash_log_file`]).
//!   붙기 전에 죽으면 인자·탐색으로 푼 엔드포인트의 자리, 붙은 뒤에는 붙은 곳의 자리다.
//! - 훅 안의 모든 일은 best-effort 다 — 훅이 다시 패닉하면 프로세스가 그냥 abort 한다.

use std::io::Write as _;
use std::path::PathBuf;
use std::sync::Mutex;

/// 크래시 기록 파일 — 붙는 곳이 정해질 때마다 갈아 끼운다.
static CRASH_LOG: Mutex<Option<PathBuf>> = Mutex::new(None);

/// 크래시 기록 자리를 정한다(엔드포인트가 바뀌면 다시 부른다).
pub fn set_endpoint(endpoint: &proto::endpoint::Endpoint) {
    if let Ok(mut slot) = CRASH_LOG.lock() {
        *slot = Some(endpoint.crash_log_file());
    }
}

/// 패닉 훅을 건다 — 기본 훅(메시지 출력)을 먼저 부르고 그 뒤에 정본 규칙을 탄다.
pub fn install(has_console: bool) {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        previous(info);
        if std::thread::current().name() != Some("main") {
            return;
        }
        on_main_thread_crash(info, has_console);
    }));
}

fn on_main_thread_crash(info: &std::panic::PanicHookInfo<'_>, has_console: bool) -> ! {
    let path = CRASH_LOG.lock().ok().and_then(|slot| slot.clone());
    if let Some(path) = path.as_ref() {
        let header = base::crash::log_header(
            &proto::clock::stamp_now(),
            std::process::id(),
            env!("CARGO_PKG_VERSION"),
        );
        let trace = std::backtrace::Backtrace::force_capture();
        if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
            let _ = write!(file, "{header}{info}\n{trace}\n");
        }
    }
    let elapsed = base::build::started().elapsed().as_secs_f64();
    let prev = std::env::var(base::crash::ENV).ok();
    match base::crash::decide(prev.as_deref(), elapsed) {
        base::crash::Decision::Relaunch(n) => {
            // 서버가 셸·세션을 쥐고 있다 — 새 클라가 다시 붙으면 작업이 이어진다(tmux 모델).
            if base::restart::relaunch_with_crash_count(Some(n)).is_ok() {
                std::process::exit(0);
            }
            std::process::exit(1);
        }
        base::crash::Decision::GiveUp => {
            let shown = path
                .as_ref()
                .map(|p| p.display().to_string())
                .unwrap_or_else(|| "-".to_owned());
            let text = base::crash::giveup_text(&shown);
            crate::console::say(&text, true);
            if !has_console {
                crate::console::show_fatal(&text);
            }
            std::process::exit(1);
        }
    }
}
