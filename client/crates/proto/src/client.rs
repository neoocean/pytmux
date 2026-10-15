//! 서버에 붙어 메시지를 받는다.
//!
//! 블로킹 API 다. 비동기 런타임을 고르지 않는 이유는 이 크레이트가 GUI·TUI 양쪽에서
//! 쓰이고 각자 루프가 다르기 때문이다 — 스레드에 얹든 async 에 얹든 호출부가 정한다.

use std::io::{BufReader, BufWriter};
use std::time::Duration;

use crate::endpoint::Endpoint;
use crate::framing::{FrameError, is_timeout, read_frame, write_frame};
use crate::message::{Hello, MAX_FRAME, ServerMessage};
use crate::transport::Stream;
use crate::{compose, endpoint};

/// 붙기에 실패하는 이유들.
///
/// 문구가 `#[error("...")]` 리터럴이 아니라 아래 `err_*` 도우미인 이유: 이 오류들은
/// 화면(시동 실패·알림)에 닿는 사용자 표면이라 `i18n::tf` 를 지나야 하는데, thiserror
/// 의 형식 문자열은 컴파일 시점 리터럴이라 런타임 번역이 못 낀다. ko 출력은 종전
/// 리터럴과 바이트 단위로 같다.
#[derive(Debug, thiserror::Error)]
pub enum AttachError {
    #[error("{}", err_no_server(.0))]
    NoServer(String),
    #[error("{}", err_connect(.endpoint, .source))]
    Connect {
        endpoint: String,
        source: std::io::Error,
    },
    /// 루프백 TCP 인데 포트파일을 못 읽었다. 서버가 죽으며 남긴 잔재이거나 아직 안 떴다.
    #[error("{}", err_no_port(.0))]
    NoPort(String),
    /// **루프백 TCP 에서 토큰은 필수다.** 같은 머신의 다른 로컬 사용자도 접속할 수
    /// 있어 토큰이 유일한 경계이고, 없이 붙으면 서버가 어차피 거절한다 — 여기서
    /// 멈추는 편이 원인을 알려 준다.
    #[error("{}", err_no_token(.0))]
    NoToken(String),
    /// 루프백이 아닌 호스트에 **이 상자의 토큰**을 보내려 했다(검수 pytmux-34 #7).
    ///
    /// 토큰 파일은 호스트와 무관하게 로컬 상태 디렉터리에서 읽는다 — 그래서
    /// `--socket tcp:10.0.0.5:5000` 은 이 상자의 라이브 서버 토큰을 평문 TCP 로 그 호스트에
    /// 건넸다. 원격은 `remote-attach` 가 정식 길이다. 정말 그 호스트로 보내야 하면
    /// [`ALLOW_REMOTE_TOKEN_ENV`] 를 `1` 로 세운다.
    #[error("{}", err_non_loopback(.0))]
    NonLoopback(String),
    #[error("{}", err_handshake(.0))]
    Handshake(#[from] FrameError),
}

fn err_no_server(names: &str) -> String {
    base::i18n::tf("서버를 찾을 수 없다 (후보: {names})", &[("names", names)])
}

/// ★ 사용자 입력이 섞이는 값(엔드포인트 = 경로)을 **마지막에** 끼운다 — `tf` 는 순차
/// 치환이라 값 안의 `{...}` 가 재치환되지 않게(core `block.rs` `summary()` 의 규칙).
fn err_connect(endpoint: &str, source: &std::io::Error) -> String {
    base::i18n::tf(
        "{endpoint} 에 연결하지 못했다: {source}",
        &[("source", source.to_string().as_str()), ("endpoint", endpoint)],
    )
}

fn err_no_port(path: &str) -> String {
    base::i18n::tf("포트파일에서 포트를 못 읽었다: {path}", &[("path", path)])
}

fn err_no_token(path: &str) -> String {
    base::i18n::tf(
        "인증 토큰을 못 읽었다: {path} (서버가 띄운 사용자와 같은 계정인지 확인)",
        &[("path", path)],
    )
}

fn err_non_loopback(host: &str) -> String {
    base::i18n::tf(
        "루프백이 아닌 호스트({host})에는 이 상자의 인증 토큰을 보내지 않는다 — 원격은 remote-attach 를 쓴다 (꼭 필요하면 PYTMUX_ALLOW_REMOTE_TOKEN=1)",
        &[("host", host)],
    )
}

/// 이 값이 `1` 이면 루프백이 아닌 TCP 호스트에도 로컬 토큰을 보낸다(검수 pytmux-34 #7).
pub const ALLOW_REMOTE_TOKEN_ENV: &str = "PYTMUX_ALLOW_REMOTE_TOKEN";

/// TCP 호스트가 **이 상자**인가 — 정본 `ipc.is_local_endpoint` 와 같은 규칙
/// (`localhost`·빈 값·`::1`·`::ffff:127.0.0.1`·`127.*`).
pub fn is_loopback_host(host: &str) -> bool {
    let host = host.trim().trim_start_matches('[').trim_end_matches(']').to_ascii_lowercase();
    matches!(host.as_str(), "localhost" | "" | "::1" | "::ffff:127.0.0.1") || host.starts_with("127.")
}

/// 쓰기 상한(검수 pytmux-34 #6). 쓰기는 **UI 스레드**(`CommandSink`)가 한다 — 상한이 없으면
/// 서버 루프가 멈춘 동안의 큰 붙여넣기가 송신 버퍼를 채우고 창을 무기한 «응답 없음» 으로
/// 만든다(× 버튼도 같은 스레드라 안 눌린다). 서버 루프의 알려진 정지(수 초 · `code_version`
/// 4.5초 실측)보다 길게 잡아, 멀쩡한 서버를 상한이 끊는 일은 없게 한다.
pub const WRITE_TIMEOUT: Duration = Duration::from_secs(5);

fn err_handshake(err: &FrameError) -> String {
    base::i18n::tf("핸드셰이크 실패: {err}", &[("err", err.to_string().as_str())])
}

/// 서버와의 연결 하나.
#[derive(Debug)]
pub struct Connection {
    reader: BufReader<Stream>,
    writer: BufWriter<Stream>,
    endpoint: Endpoint,
}

impl Connection {
    /// 떠 있는 서버를 찾아 붙는다.
    pub fn attach(cols: u16, rows: u16) -> Result<Self, AttachError> {
        let endpoint = endpoint::resolve().ok_or_else(|| {
            let names: Vec<String> = endpoint::candidates()
                .iter()
                .map(Endpoint::display)
                .collect();
            AttachError::NoServer(names.join(", "))
        })?;
        Self::attach_to_endpoint(&endpoint, cols, rows)
    }

    /// 지정한 위치에 붙는다. 경로면 unix 소켓, `tcp:host:port` 면 루프백 TCP 다.
    pub fn attach_to(spec: &std::path::Path, cols: u16, rows: u16) -> Result<Self, AttachError> {
        Self::attach_to_endpoint(&endpoint::parse(&spec.to_string_lossy()), cols, rows)
    }

    /// 엔드포인트 하나에 붙는다.
    pub fn attach_to_endpoint(
        endpoint: &Endpoint,
        cols: u16,
        rows: u16,
    ) -> Result<Self, AttachError> {
        let name = endpoint.display();
        // ★ 토큰을 **읽기 전에** 호스트를 본다(검수 pytmux-34 #7) — 읽고 나서 거르면 그
        //   사이 어디로든 흘러갈 길이 생긴다.
        if let Endpoint::Tcp { host, .. } = endpoint
            && !is_loopback_host(host)
            && std::env::var(ALLOW_REMOTE_TOKEN_ENV).as_deref() != Ok("1")
        {
            return Err(AttachError::NonLoopback(host.clone()));
        }
        let token = endpoint::read_token(endpoint);
        let mut resolved: Option<u16> = None;
        let stream = match endpoint {
            Endpoint::Unix { path, .. } => {
                Stream::connect_unix(path).map_err(|source| AttachError::Connect {
                    endpoint: name.clone(),
                    source,
                })?
            }
            Endpoint::Tcp {
                host, portfile, ..
            } => {
                // 루프백 TCP 는 같은 머신의 다른 로컬 사용자에게도 열려 있다 —
                // 토큰이 유일한 경계라 없으면 붙지 않는다.
                if token.is_none() {
                    return Err(AttachError::NoToken(
                        endpoint.token_path().display().to_string(),
                    ));
                }
                // 못 읽은 것은 **포트파일**이다. 여기에 엔드포인트 이름을 넣으면
                // "포트파일에서 포트를 못 읽었다: tcp:127.0.0.1:0" 이 되어, 문장은
                // 파일을 가리키는데 값은 파일이 아닌 것을 준다 — 어느 파일을 봐야
                // 하는지가 사라진다(토큰 쪽은 이미 파일 경로를 준다).
                let port = endpoint
                    .resolve_port()
                    .ok_or_else(|| AttachError::NoPort(portfile.display().to_string()))?;
                // 확정된 포트를 기억해 둔다 — 진단에서 "tcp:127.0.0.1:0" 은
                // 어디에 붙었는지 알려 주지 않는다.
                resolved = Some(port);
                // **실패 진단도 확정 포트로 말한다.** 설정값(0)으로 말하면 "죽은
                // 포트파일을 물었다"와 "포트파일을 아예 못 읽었다"가 같은 문장이 된다.
                // Windows 에서 특히 아프다 — 방화벽이 SYN 을 조용히 버려 증상이
                // 거절이 아니라 타임아웃이라, 메시지가 유일한 단서다.
                let tried = format!("tcp:{host}:{port}");
                Stream::connect_tcp(host, port).map_err(|source| AttachError::Connect {
                    endpoint: tried,
                    source,
                })?
            }
        };
        // 읽기 쪽에만 타임아웃을 건다. 서버가 조용해도(사용자가 아무것도 안 하면
        // 그럴 수 있다) 죽지 않아야 하므로, 타임아웃은 "끊겼다"가 아니라 "아직
        // 없다"로 해석한다 — `next_message` 가 `Ok(None)` 을 돌려준다.
        let _ = stream.set_read_timeout(Some(Duration::from_millis(250)));
        // 쓰기에는 **끊김으로 읽는** 상한을 건다(`WRITE_TIMEOUT` 문서 · 검수 pytmux-34 #6).
        // 복제본(쓰기 절반)도 같은 소켓이라 여기서 한 번 걸면 된다.
        let _ = stream.set_write_timeout(Some(WRITE_TIMEOUT));

        // 확정 포트를 반영한 엔드포인트. 여기서부터의 진단은 전부 이것으로 말한다.
        let mut settled = endpoint.clone();
        if let (Endpoint::Tcp { port, .. }, Some(actual)) = (&mut settled, resolved) {
            *port = actual;
        }
        let write_half = stream.try_clone().map_err(|source| AttachError::Connect {
            endpoint: settled.display(),
            source,
        })?;

        let mut conn = Self {
            reader: BufReader::new(stream),
            writer: BufWriter::new(write_half),
            endpoint: settled,
        };

        let hello = Hello::new(cols, rows).with_token(token);
        write_frame(&mut conn.writer, &hello)?;
        Ok(conn)
    }

    /// 붙어 있는 위치(사람에게 보일 이름).
    pub fn endpoint(&self) -> &Endpoint {
        &self.endpoint
    }

    pub fn socket(&self) -> String {
        self.endpoint.display()
    }

    /// 쓰기 절반을 떼어 낸다. 읽기는 스레드가, 쓰기는 뷰가 담당하기 위해서다.
    ///
    /// 같은 연결의 양쪽을 나눠 쥘 수 있는 것은 `UnixStream` 이 복제되기 때문이다.
    pub fn split_sink(&self) -> std::io::Result<CommandSink> {
        Ok(CommandSink {
            writer: BufWriter::new(self.writer.get_ref().try_clone()?),
        })
    }

    /// 다음 메시지. 아직 안 왔으면 `Ok(None)`, 서버가 닫았으면 `Err(Closed)`.
    ///
    /// 핸드셰이크가 끝난 뒤라 상한은 [`MAX_FRAME`] 이다.
    ///
    /// `Ok(None)` 은 **프레임을 아직 시작도 안 했다**는 뜻뿐이다. 프레임 도중의 타임아웃은
    /// 여기까지 안 온다 — `read_frame` 이 그 자리에서 마저 채운다(pytmux-169). 그 구분이
    /// 없으면 다시 부른 이 함수가 JSON 한복판을 길이 프리픽스로 읽는다.
    pub fn next_message(&mut self) -> Result<Option<ServerMessage>, FrameError> {
        match read_frame(&mut self.reader, MAX_FRAME) {
            Ok(value) => Ok(Some(serde_json::from_value(value)?)),
            Err(FrameError::Io(e)) if is_timeout(&e) => Ok(None),
            Err(e) => Err(e),
        }
    }

    /// 화면이 다 온 뒤에도 **이만큼 더 듣는다**. 이유는 [`first_frame`] 문서 참조.
    ///
    /// [`first_frame`]: Connection::first_frame
    const SETTLE: Duration = Duration::from_millis(250);

    /// 핸드셰이크 직후 서버가 보내는 **첫 완전한 화면**을 모은다.
    ///
    /// 서버는 붙자마자 `layout` → 패널별 `screen` → `status` → (광고했으면) `blocks` →
    /// `claude` 를 보낸다. 그래서 layout 이 알려 준 패널 수만큼 screen 을 받으면 **그림은**
    /// 완성된 것이다. `status` 를 기다리지 않는 이유는 원격 탭을 보는 클라에게는 layout
    /// 없이 status 만 가는 등 경로가 갈리기 때문이다 — 패널 수로 세는 편이 단순하고 확실하다.
    ///
    /// # 화면이 다 와도 바로 안 돌아간다 (2026-07-28)
    ///
    /// 종전에는 마지막 screen 을 받는 즉시 돌아갔다. 그런데 **서버는 그 뒤에** blocks 와
    /// claude 를 보낸다 — 즉 이 함수가 채우는 `frame.blocks` 는 **구조적으로 늘 비어**
    /// 있었고, 그걸 찍는 `examples/attach` 는 셸 통합이 멀쩡히 돌고 있어도 항상
    /// "블록 0개"라고 말했다.
    ///
    /// 오류가 아니라 **그럴듯한 0** 이라 더 나빴다: 이 값을 믿고 ConPTY 가 OSC 를
    /// 삼키는지, 셸 통합이 안 걸렸는지를 한참 팠다(둘 다 멀쩡했다). 죽은 로거(67739)와
    /// 같은 부류다 — 진단이 없는 것보다 **틀린 진단이 비싸다**.
    ///
    /// 그래서 화면이 완성되면 곧장 끝내지 않고 [`SETTLE`](Self::SETTLE) 만큼 더 듣는다.
    /// 나머지 대기와 달리 이건 **뒷정리 시간**이지 무언가를 기다리는 게 아니다 — 안 오면
    /// 안 오는 대로 그때 돌아간다(광고 안 한 클라·셸 통합 없는 패널은 아예 안 보낸다).
    ///
    /// 실클라(`ServerLink`)는 이 함수를 안 쓴다 — 리더 스레드가 오는 대로 전부 큐에
    /// 넣으므로 같은 구멍이 없다. 고친 것은 **진단 경로**다.
    ///
    /// ⚠ `deadline` 은 **프레임 사이**에서만 잰다. 프레임을 반쯤 받은 채로 상대가 멎으면
    /// `read_frame` 이 그 자리에서 최대 `PARTIAL_FRAME_PATIENCE` 만큼 더 기다린다 —
    /// 반만 읽고 물러나면 스트림이 어긋나기 때문이다(pytmux-169 · `framing.rs` 머리말).
    pub fn first_frame(&mut self, deadline: Duration) -> Result<Frame, FrameError> {
        let start = std::time::Instant::now();
        let mut frame = Frame::default();
        let mut expected: Option<usize> = None;
        // 그림이 완성된 시각. 여기부터 SETTLE 만큼 더 듣는다.
        let mut complete_at: Option<std::time::Instant> = None;

        while start.elapsed() < deadline {
            if let Some(done) = complete_at
                && done.elapsed() >= Self::SETTLE
            {
                return Ok(frame);
            }
            let msg = match self.next_message() {
                Ok(Some(msg)) => msg,
                Ok(None) => continue,
                // **그림이 다 온 뒤의 종료는 실패가 아니다.** 다 보내고 바로 닫는
                // 서버(진단용 가짜 서버가 그렇다)에서, 이미 손에 쥔 프레임을 EOF
                // 하나로 버리면 "붙었는데 아무것도 못 받았다"가 된다 — 실제로는 다
                // 받았다. 완성 전의 종료는 그대로 오류다(그때는 정말 못 받았다).
                Err(FrameError::Closed) if complete_at.is_some() => return Ok(frame),
                Err(e) => return Err(e),
            };
            match msg {
                ServerMessage::Layout(layout) => {
                    expected = Some(layout.panes.len());
                    frame.layout = Some(layout);
                }
                ServerMessage::Screen(screen) => {
                    frame.screens.push(screen);
                }
                ServerMessage::Status(status) => {
                    frame.status = Some(status);
                }
                ServerMessage::Blocks { pane, blocks } => {
                    frame.blocks.insert(pane, blocks);
                }
                ServerMessage::Bye => return Err(FrameError::Closed),
                _ => {}
            }
            if let Some(n) = expected
                && frame.screens.len() >= n
            {
                complete_at.get_or_insert_with(std::time::Instant::now);
            }
        }
        Ok(frame)
    }
}

/// 명령을 보내는 쪽. 읽기 스레드와 독립적으로 동작한다.
#[derive(Debug)]
pub struct CommandSink {
    writer: BufWriter<Stream>,
}

impl CommandSink {
    /// 명령 하나를 보낸다.
    ///
    /// 보낸 뒤 로컬 상태를 고치지 않는다 — 서버 명령 대부분이 `FULL` 이라 전체 재동기가
    /// 뒤따르고, 그게 권위다.
    pub fn send(&mut self, command: &crate::command::Command) -> Result<(), FrameError> {
        let result = write_frame(&mut self.writer, &command.to_frame());
        self.fold_on_error(result)
    }

    /// 키 입력을 활성 패널로 보낸다.
    pub fn send_input(&mut self, data: impl AsRef<[u8]>) -> Result<(), FrameError> {
        let result = write_frame(&mut self.writer, &crate::command::Input::new(data));
        self.fold_on_error(result)
    }

    /// 나가는 것 하나를 종류에 맞는 프레임으로 보낸다.
    ///
    /// 이벤트 루프는 이것만 부른다 — 종류별로 부르는 함수가 갈리면 **큐를 종류별로
    /// 비우게 되고 사용자가 한 순서가 뒤집힌다**(`Outgoing` 문서 참조).
    pub fn send_outgoing(&mut self, item: &crate::command::Outgoing) -> Result<(), FrameError> {
        let result = write_frame(&mut self.writer, &item.to_frame());
        self.fold_on_error(result)
    }

    /// 쓰기가 실패하면 **연결을 접는다**(검수 pytmux-34 #6). 상한에 걸린 쓰기는 프레임을
    /// 반쯤 보냈을 수 있어 그 뒤로 스트림이 어긋난다 — 이어 쓰면 서버가 본문 한복판을 길이
    /// 프리픽스로 읽는다. 소켓을 닫으면 같은 소켓을 쥔 읽기 스레드가 깨어나 `Ended` 를 보내고,
    /// 뷰는 평소의 재접속 경로를 탄다.
    fn fold_on_error(&mut self, result: Result<(), FrameError>) -> Result<(), FrameError> {
        if result.is_err() {
            let _ = self.writer.get_ref().shutdown();
        }
        result
    }
}

/// 한 시점의 화면 전체.
#[derive(Debug, Default)]
pub struct Frame {
    pub layout: Option<crate::message::Layout>,
    pub screens: Vec<crate::message::Screen>,
    pub status: Option<crate::message::Status>,
    /// 패널 id → 블록 목록(§10-13).
    pub blocks: std::collections::HashMap<i64, Vec<crate::blocks::Block>>,
}

impl Frame {
    /// 패널의 블록 목록.
    pub fn blocks(&self, pane_id: i64) -> &[crate::blocks::Block] {
        self.blocks.get(&pane_id).map_or(&[], Vec::as_slice)
    }

    /// 창 전체를 격자로 합성한다 — **패널 경계선까지** 포함한 실제 화면이다.
    ///
    /// 라이브 확인용이다: 배치가 실제로 어떻게 그려지는지는 패널을 하나씩 봐서는 알 수
    /// 없다(맞닿은 변이 합쳐지는지, 활성 테두리가 이웃 위로 오는지).
    pub fn composite(&self) -> Option<crate::canvas::Canvas> {
        let mut state = crate::SessionState::new();
        state.apply(ServerMessage::Layout(self.layout.clone()?));
        for screen in &self.screens {
            state.apply(ServerMessage::Screen(screen.clone()));
        }
        state.composite()
    }

    /// 패널 하나를 합성한 텍스트 줄들. 패널의 폭은 layout 이 알려 준다.
    pub fn compose_pane(&self, pane_id: i64) -> Option<Vec<String>> {
        let screen = self.screens.iter().find(|s| s.pane == pane_id)?;
        let cols = self
            .layout
            .as_ref()?
            .panes
            .iter()
            .find(|p| p.id == pane_id)?
            .w as usize;
        Some(compose::compose_rows(&screen.rows, cols))
    }
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::message::{HANDSHAKE_MAX_FRAME, Layout, PaneLayout, Run, Screen};

    fn frame_with(cols: u16, text: &str) -> Frame {
        Frame {
            layout: Some(Layout {
                cols,
                rows: 2,
                panes: vec![PaneLayout {
                    id: 7,
                    x: 0,
                    y: 0,
                    w: cols,
                    h: 2,
                    ..Default::default()
                }],
                active: 7,
                ..Default::default()
            }),
            screens: vec![Screen {
                pane: 7,
                rows: vec![vec![Run::plain(text)]],
                ..Default::default()
            }],
            status: None,
            blocks: Default::default(),
        }
    }

    #[test]
    fn composes_a_pane_at_the_width_the_layout_gave() {
        let frame = frame_with(6, "hi");
        assert_eq!(frame.compose_pane(7), Some(vec!["hi    ".to_owned()]));
    }

    #[test]
    fn unknown_pane_yields_nothing_rather_than_panicking() {
        // 서버가 layout 과 screen 을 다른 시점에 보내면 잠깐 어긋날 수 있다.
        assert_eq!(frame_with(4, "x").compose_pane(999), None);
    }

    #[test]
    fn attach_reports_which_sockets_it_looked_at() {
        // "서버가 없다"는 진단이 쓸모 있으려면 어디를 봤는지 말해야 한다.
        let err = Connection::attach_to(std::path::Path::new("/nonexistent/pytmux.sock"), 80, 24)
            .unwrap_err();
        let text = err.to_string();
        assert!(text.contains("/nonexistent/pytmux.sock"), "실제: {text}");
    }

    /// 시험용 TCP 엔드포인트 — 토큰 파일을 임시 자리에 만들어 `attach_to_endpoint` 가 실제
    /// 경로 그대로 붙게 한다(전역 env 를 안 건드린다).
    fn tcp_endpoint_with_token(host: &str, port: u16, tag: &str) -> (Endpoint, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "pytmux-client-test-{}-{tag}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let token = dir.join("t.token");
        std::fs::write(&token, "secret-token-for-test").unwrap();
        (
            Endpoint::Tcp {
                host: host.to_owned(),
                port,
                portfile: dir.join("t.port"),
                token,
            },
            dir,
        )
    }

    #[test]
    fn the_local_token_never_goes_to_a_non_loopback_host() {
        // ☠ 검수 pytmux-34 #7: `--socket tcp:<원격>:<포트>` 가 이 상자의 토큰을 그 호스트로
        //   보냈다. **연결 전에** 거절해야 한다 — 그래서 닿을 수 없는 주소(TEST-NET-1)를 써도
        //   connect 시간 없이 바로 돌아온다(연결을 시도했다면 상한까지 매달린다).
        if std::env::var(ALLOW_REMOTE_TOKEN_ENV).as_deref() == Ok("1") {
            return; // 이 상자가 일부러 허용해 둔 환경이면 잴 것이 없다
        }
        let (ep, dir) = tcp_endpoint_with_token("192.0.2.1", 5000, "remote");
        let err = Connection::attach_to_endpoint(&ep, 80, 24).unwrap_err();
        let _ = std::fs::remove_dir_all(dir);
        assert!(matches!(err, AttachError::NonLoopback(ref h) if h == "192.0.2.1"), "{err:?}");
    }

    #[test]
    fn loopback_hosts_follow_the_canonical_rule() {
        // 정본 `ipc.is_local_endpoint` 와 같은 표. 좁으면 로컬 서버에 못 붙고, 넓으면 토큰이 샌다.
        for host in ["127.0.0.1", "127.1.2.3", "localhost", "LOCALHOST", "::1", "[::1]", "", "::ffff:127.0.0.1"] {
            assert!(is_loopback_host(host), "{host:?} 를 원격으로 봤다");
        }
        for host in ["10.0.0.5", "192.168.0.2", "example.com", "128.0.0.1", "localhost.evil.com", "0.0.0.0"] {
            assert!(!is_loopback_host(host), "{host:?} 를 루프백으로 봤다");
        }
    }

    #[test]
    fn a_stalled_server_makes_sends_fail_instead_of_freezing_the_ui() {
        // ☠ 검수 pytmux-34 #6: 쓰기는 UI 스레드가 한다. 서버가 안 읽는 동안(루프 정지) 큰
        //   붙여넣기를 하면 상한 없는 `write_all` 이 창을 무기한 막았다. 받기만 하고 안 읽는
        //   «서버» 에 붙어 큰 입력을 밀어 넣고, 상한 안에 `Err` 로 돌아오는지 본다.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let (park_tx, park_rx) = std::sync::mpsc::channel::<()>();
        let server = std::thread::spawn(move || {
            let held = listener.accept().ok();
            let _ = park_rx.recv();
            drop(held);
        });
        let (ep, dir) = tcp_endpoint_with_token("127.0.0.1", port, "stall");
        let conn = Connection::attach_to_endpoint(&ep, 80, 24).expect("붙지 못했다");
        let mut sink = conn.split_sink().unwrap();
        let (done_tx, done_rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let big = vec![b'x'; 1 << 20];
            let mut errored = false;
            for _ in 0..256 {
                if sink.send_input(&big).is_err() {
                    errored = true;
                    break;
                }
            }
            let _ = done_tx.send(errored);
        });
        let verdict = done_rx.recv_timeout(WRITE_TIMEOUT * 4);
        let _ = park_tx.send(());
        let _ = server.join();
        let _ = std::fs::remove_dir_all(dir);
        match verdict {
            Ok(errored) => assert!(errored, "안 읽는 서버에 256 MiB 를 다 보냈다고 한다"),
            Err(_) => panic!("안 읽는 서버에 쓰다가 매달렸다 — 연결에 쓰기 상한이 없다"),
        }
    }

    #[test]
    fn handshake_limit_is_used_before_auth() {
        // 이 상수를 쓰지 않으면 인증 전 연결이 64MiB 를 요구할 수 있다.
        assert!(HANDSHAKE_MAX_FRAME < MAX_FRAME);
    }
}
