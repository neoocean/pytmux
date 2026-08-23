//! 길이 프리픽스 프레이밍.
//!
//! 한 프레임 = **4바이트 빅엔디언 길이** + 그 길이만큼의 UTF-8 JSON. 서버의
//! `pytmuxlib/protocol.py::read_msg`/`write_msg` 와 같은 형식이다.
//!
//! # 상한이 왜 중요한가
//!
//! 길이 프리픽스가 4바이트라 헤더 하나가 최대 4GiB 를 요구할 수 있다. 손상된 헤더나
//! 악의적인 연결이 그 값을 광고하면 읽는 쪽이 즉시 메모리를 고갈시킨다. 그래서 서버도
//! 클라도 상한을 두고, **인증 전에는 훨씬 작은 상한**을 쓴다(핸드셰이크 프레임은 수백
//! 바이트면 충분하다). 이 값들은 심볼이 아니라 **값으로** 양쪽에 복제돼 있으므로
//! 한쪽만 바뀌면 조용히 깨진다 — 그래서 테스트가 값을 직접 못박는다.
//!
//! # 읽기 타임아웃과 «프레임 도중» (pytmux-169)
//!
//! 이 스트림에는 **읽기 타임아웃이 걸려 있다**(`client.rs` 가 250ms). 타임아웃은
//! "끊겼다"가 아니라 "아직 없다"라서 호출부가 다시 부르는데, 그 사이에 이미 읽어 둔
//! 바이트가 사라지면 **스트림 위치를 잃는다** — 다음 호출이 JSON 본문 한복판을 길이
//! 프리픽스로 읽고, 그 값이 상한을 넘어 연결이 끊긴다(실측 `577004915` = `b'"des'`).
//! 그래서 여기서는 [`std::io::Read::read_exact`] 를 쓰지 않는다. 그것은 도중에 오류가
//! 나면 **이미 소비한 바이트를 버린다** — 재시도를 전제한 이 경로에서는 못 쓴다.
//!
//! 규칙은 하나다: **한 바이트라도 읽었으면 그 프레임을 끝까지 채운다.** 아직 한
//! 바이트도 안 읽은 타임아웃만 "아직 없다"로 올라간다.

use std::io::{self, Read, Write};
use std::time::{Duration, Instant};

use crate::message::MAX_FRAME;

/// 시작된 프레임의 나머지를 기다리는 인내 시간.
///
/// 타임아웃이 연달아 나도 이만큼은 같은 프레임을 계속 채운다. 다 지나면 스트림 위치를
/// 되찾을 길이 없으므로 **어긋난 채 계속 읽지 않고** [`FrameError::Stalled`] 로 끊는다 —
/// 조용한 손상보다 정직한 오류가 싸다.
///
/// 값의 근거: 읽기 타임아웃이 250ms 이니 연속 20회다. 로컬 소켓(유닉스·루프백)에서
/// 프레임 한 장이 5초 동안 안 끝나면 그건 느린 것이 아니라 상대가 멎은 것이다.
const PARTIAL_FRAME_PATIENCE: Duration = Duration::from_secs(5);

/// 프레임을 읽다 생길 수 있는 일.
///
/// 문구가 리터럴이 아니라 `err_*` 도우미인 이유는 [`crate::client::AttachError`] 와
/// 같다 — 연결 끊김 사유로 화면에 닿는 표면이라 `i18n` 을 지나야 한다.
#[derive(Debug, thiserror::Error)]
pub enum FrameError {
    /// 상대가 연결을 닫았다. 정상 종료일 수도 있다.
    #[error("{}", base::i18n::t("연결이 닫혔다"))]
    Closed,
    /// 광고된 길이가 상한을 넘었다. 읽지 않고 끊는다.
    ///
    /// `header` 는 **길이로 읽은 그 4바이트**다(pytmux-171). 스트림이 어긋나면 이 자리에
    /// 오는 것은 길이가 아니라 **본문 글자**이고, 그때 사용자에게 보이는 것은
    /// `1684217948 바이트` 같은 뜻 모를 수다 — 제보자가 그 수를 손으로 16진수로 바꿔
    /// `dc \` 라는 아스키를 알아냈다. 그 한 걸음을 오류가 스스로 하게 한다.
    #[error("{}", err_too_large(.advertised, .limit, .header))]
    TooLarge { advertised: usize, limit: usize, header: [u8; 4] },
    /// 프레임을 시작해 놓고 나머지가 [`PARTIAL_FRAME_PATIENCE`] 안에 안 왔다.
    ///
    /// 끊긴 것도(그건 [`Closed`](FrameError::Closed)) 없는 것도 아니라 **반만 온**
    /// 것이다. 스트림 위치를 잃었으니 이 연결로는 더 읽지 않는다.
    #[error("{}", err_stalled(.got, .want))]
    Stalled { got: usize, want: usize },
    #[error("{}", err_io(.0))]
    Io(#[from] io::Error),
    /// 페이로드가 JSON 이 아니거나 기대한 모양이 아니다.
    #[error("{}", err_decode(.0))]
    Decode(#[from] serde_json::Error),
}

fn err_too_large(advertised: &usize, limit: &usize, header: &[u8; 4]) -> String {
    let mut out = base::i18n::tf(
        "프레임이 너무 크다: {advertised} 바이트 (상한 {limit})",
        &[
            ("advertised", advertised.to_string().as_str()),
            ("limit", limit.to_string().as_str()),
        ],
    );
    // ★ 길이 자리에 **글자**가 왔으면 그렇다고 말한다(pytmux-171). 그 수를 손으로
    //   16진수·아스키로 바꿔 보는 일을 오류가 대신한다 — 그 한 걸음이 「알 수 없는 수」와
    //   「스트림이 어긋났고 여기 이 글자를 길이로 읽었다」를 가른다.
    if let Some(text) = printable_ascii(header) {
        out.push(' ');
        out.push_str(&base::i18n::tf(
            "— 길이 자리에 글자가 왔다: {text} (스트림이 어긋났다)",
            &[("text", text.as_str())],
        ));
    }
    out
}

/// 네 바이트가 **전부 읽을 수 있는 아스키**면 그 글자. 아니면 `None`.
///
/// 제어문자·비아스키를 빼는 이유: 진짜 큰 길이(예: 손상된 거대 프레임)까지 글자로
/// 보여 주면 그 안내가 거짓 단서가 된다. 여기서 말하고 싶은 것은 「본문이 길이 자리에
/// 왔다」이고, 그 경우는 대개 인쇄 가능한 글자다.
fn printable_ascii(bytes: &[u8; 4]) -> Option<String> {
    bytes
        .iter()
        .all(|b| (0x20..0x7f).contains(b))
        .then(|| String::from_utf8_lossy(bytes).into_owned())
}

fn err_stalled(got: &usize, want: &usize) -> String {
    base::i18n::tf(
        "프레임이 오다 말았다: {got}/{want} 바이트",
        &[("got", got.to_string().as_str()), ("want", want.to_string().as_str())],
    )
}

fn err_io(err: &io::Error) -> String {
    base::i18n::tf("입출력 오류: {err}", &[("err", err.to_string().as_str())])
}

fn err_decode(err: &serde_json::Error) -> String {
    base::i18n::tf("프레임을 해석할 수 없다: {err}", &[("err", err.to_string().as_str())])
}

/// 읽기 타임아웃인가 — "끊겼다"가 아니라 "아직 없다".
///
/// 유닉스는 `SO_RCVTIMEO` 가 `EAGAIN`(= [`WouldBlock`](io::ErrorKind::WouldBlock))으로,
/// Windows 는 [`TimedOut`](io::ErrorKind::TimedOut) 으로 온다. 술어를 한 곳에 둔다 —
/// 프레이밍과 호출부가 각자 판정하면 한쪽만 고쳐질 때 조용히 갈린다.
pub fn is_timeout(err: &io::Error) -> bool {
    matches!(err.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut)
}

/// `buf` 를 **정확히 다 채운다**. 한 바이트라도 읽었으면 타임아웃이 나도 계속 채운다.
///
/// `committed` 는 "이 프레임을 이미 시작했다"는 뜻이다(= 헤더를 읽어 둔 페이로드 쪽).
/// 시작 전의 타임아웃만 그대로 올라가 호출부의 "아직 없다"가 된다.
///
/// ⛔ [`Read::read_exact`] 로 대신하지 마라 — 그것은 도중에 오류가 나면 **이미 소비한
/// 바이트를 버려서**, 재시도하는 호출부에게 스트림을 어긋난 채로 넘긴다(pytmux-169).
///
/// `patience` 를 인자로 받는 이유는 시험 때문이다 — 5초를 벽시계로 기다리는 오라클은
/// 아무도 안 돌린다.
fn fill<R: Read>(
    reader: &mut R,
    buf: &mut [u8],
    committed: bool,
    patience: Duration,
) -> Result<(), FrameError> {
    let mut filled = 0usize;
    // 마지막으로 무언가 읽은 뒤로 흐른 시간. 조금씩이라도 오면 인내가 다시 찬다 —
    // 재는 것은 "이 프레임이 오래 걸린다"가 아니라 "상대가 멎었다"이다.
    let mut stalled_since: Option<Instant> = None;
    while filled < buf.len() {
        match reader.read(&mut buf[filled..]) {
            // 0 = EOF. 상대가 닫았다(반만 읽었어도 더 올 것이 없다).
            Ok(0) => return Err(FrameError::Closed),
            Ok(n) => {
                filled += n;
                stalled_since = None;
            }
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) if is_timeout(&e) => {
                if !committed && filled == 0 {
                    // 이 프레임은 아직 시작도 안 했다 — 버릴 것이 없으니 그대로 올린다.
                    return Err(FrameError::Io(e));
                }
                let since = *stalled_since.get_or_insert_with(Instant::now);
                if since.elapsed() >= patience {
                    return Err(FrameError::Stalled { got: filled, want: buf.len() });
                }
            }
            Err(e) => return Err(FrameError::Io(e)),
        }
    }
    Ok(())
}

/// 프레임 하나를 읽어 JSON 값으로 돌려준다.
///
/// `limit` 는 이 프레임에 허용할 최대 페이로드 크기다. 핸드셰이크 중에는
/// [`HANDSHAKE_MAX_FRAME`](crate::message::HANDSHAKE_MAX_FRAME) 을, 그 뒤에는
/// [`MAX_FRAME`](crate::message::MAX_FRAME) 을 넘긴다.
///
/// 타임아웃이 걸린 스트림에서 이 함수를 되풀이해 부르는 것이 정상 경로다 — 한 프레임을
/// 반만 읽고 돌아가는 일은 없다(모듈 머리말 §읽기 타임아웃과 «프레임 도중»).
pub fn read_frame<R: Read>(reader: &mut R, limit: usize) -> Result<serde_json::Value, FrameError> {
    let mut header = [0u8; 4];
    fill(reader, &mut header, false, PARTIAL_FRAME_PATIENCE)?;
    let advertised = u32::from_be_bytes(header) as usize;
    if advertised > limit {
        // 읽지 않는다 — 광고된 크기를 믿고 할당하는 것이 바로 그 공격이다.
        return Err(FrameError::TooLarge {
            advertised,
            limit,
            header,
        });
    }
    let mut payload = vec![0u8; advertised];
    fill(reader, &mut payload, true, PARTIAL_FRAME_PATIENCE)?;
    Ok(serde_json::from_slice(&payload)?)
}

/// 값을 프레임으로 만들어 쓴다.
pub fn write_frame<W: Write, T: serde::Serialize>(
    writer: &mut W,
    value: &T,
) -> Result<(), FrameError> {
    let payload = serde_json::to_vec(value)?;
    if payload.len() > MAX_FRAME {
        return Err(FrameError::TooLarge {
            advertised: payload.len(),
            limit: MAX_FRAME,
            // 우리가 만든 프레임이라 길이 자리는 늘 진짜 길이다 — 글자 단서를 붙일
            // 일이 없다(`printable_ascii` 가 0 바이트를 안 읽을 수 있다고 판정한다).
            header: [0; 4],
        });
    }
    let len = u32::try_from(payload.len()).expect("MAX_FRAME 이 u32 안이다");
    writer.write_all(&len.to_be_bytes())?;
    writer.write_all(&payload)?;
    writer.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::message::HANDSHAKE_MAX_FRAME;

    fn framed(payload: &[u8]) -> Vec<u8> {
        let mut out = (payload.len() as u32).to_be_bytes().to_vec();
        out.extend_from_slice(payload);
        out
    }

    #[test]
    fn round_trips_through_the_wire_format() {
        let mut buf = Vec::new();
        write_frame(&mut buf, &serde_json::json!({"t": "hello", "proto": 1})).unwrap();
        // 헤더가 정말 4바이트 빅엔디언인지 직접 본다.
        let advertised = u32::from_be_bytes([buf[0], buf[1], buf[2], buf[3]]) as usize;
        assert_eq!(advertised, buf.len() - 4);

        let value = read_frame(&mut buf.as_slice(), MAX_FRAME).unwrap();
        assert_eq!(value["t"], "hello");
    }

    #[test]
    fn closed_connection_is_distinguishable_from_an_error() {
        // 정상 종료를 오류로 보고하면 호출부가 재접속을 시도한다.
        let err = read_frame(&mut [].as_slice(), MAX_FRAME).unwrap_err();
        assert!(matches!(err, FrameError::Closed));
    }

    #[test]
    fn truncated_payload_is_closed_not_a_hang() {
        // 길이는 10 이라고 광고하고 3바이트만 보낸 경우.
        let mut wire = 10u32.to_be_bytes().to_vec();
        wire.extend_from_slice(b"abc");
        let err = read_frame(&mut wire.as_slice(), MAX_FRAME).unwrap_err();
        assert!(matches!(err, FrameError::Closed));
    }

    #[test]
    fn oversized_frame_is_rejected_without_allocating() {
        // 4GiB 를 광고하는 헤더. 읽으려 들면 그 자리에서 죽는다.
        let wire = u32::MAX.to_be_bytes().to_vec();
        let err = read_frame(&mut wire.as_slice(), MAX_FRAME).unwrap_err();
        match err {
            FrameError::TooLarge { advertised, limit, .. } => {
                assert_eq!(advertised, u32::MAX as usize);
                assert_eq!(limit, MAX_FRAME);
            }
            other => panic!("상한 초과로 안 걸렀다: {other:?}"),
        }
    }

    #[test]
    fn a_length_that_is_really_text_names_the_characters() {
        // ★ **제보의 그 바이트다**(pytmux-171): 화면에는 `1684217948 바이트` 라고만 떴고,
        //   제보자가 손으로 `0x6463205c` = 아스키 `dc \` 임을 알아내고서야 「길이 자리에
        //   본문이 왔다」가 드러났다. 그 한 걸음을 오류가 스스로 하게 한다.
        let wire = b"dc \\";
        let err = read_frame(&mut wire.as_slice(), MAX_FRAME).unwrap_err();
        let said = err.to_string();
        assert!(
            said.contains("dc \\"),
            "길이로 읽은 글자를 안 말한다 — 그 수는 사람에게 아무 뜻이 없다: {said}"
        );
    }

    #[test]
    fn a_genuinely_huge_length_does_not_invent_a_text_clue() {
        // ⛔ 진짜로 큰 길이(제어문자가 섞인 바이트)까지 글자로 보여 주면 그 안내가
        //    **거짓 단서**가 된다 — 없는 「어긋남」을 있다고 말하는 셈이다.
        let wire = [0xff, 0xff, 0xff, 0xff];
        let err = read_frame(&mut wire.as_slice(), MAX_FRAME).unwrap_err();
        let said = err.to_string();
        assert!(
            !said.contains("길이 자리에 글자") && !said.contains("where the length"),
            "글자가 아닌 바이트에 글자 단서를 붙였다: {said}"
        );
    }

    #[test]
    fn handshake_limit_is_much_smaller_than_the_normal_one() {
        // 인증 전에는 작은 상한을 쓴다. 이 구분이 사라지면 무토큰 연결이
        // 64MiB 를 요구할 수 있게 된다.
        assert!(HANDSHAKE_MAX_FRAME < MAX_FRAME);
        let payload = vec![b'x'; HANDSHAKE_MAX_FRAME + 1];
        let wire = framed(&payload);
        let err = read_frame(&mut wire.as_slice(), HANDSHAKE_MAX_FRAME).unwrap_err();
        assert!(matches!(err, FrameError::TooLarge { .. }));
        // 같은 프레임도 인증 후 상한으로는 통과한다(JSON 은 아니라 Decode 로 떨어진다).
        let err = read_frame(&mut wire.as_slice(), MAX_FRAME).unwrap_err();
        assert!(matches!(err, FrameError::Decode(_)));
    }

    #[test]
    fn non_json_payload_reports_decode_not_close() {
        let wire = framed(b"not json at all");
        let err = read_frame(&mut wire.as_slice(), MAX_FRAME).unwrap_err();
        assert!(matches!(err, FrameError::Decode(_)));
    }

    /// 읽기 타임아웃이 걸린 소켓 흉내 — 대본대로 조각을 주거나 타임아웃을 낸다.
    ///
    /// 진짜 소켓으로도 같은 것을 재지만(`tests/partial_frame.rs`) 여기서는 **어느 조각에서
    /// 끊기는지**를 바이트 단위로 정해야 해서 대본이 필요하다.
    enum Beat {
        Bytes(Vec<u8>),
        Timeout,
    }

    struct Scripted {
        beats: std::collections::VecDeque<Beat>,
    }

    impl Scripted {
        fn new(beats: Vec<Beat>) -> Self {
            Self { beats: beats.into() }
        }
    }

    impl Read for Scripted {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            match self.beats.pop_front() {
                // 대본이 끝나면 계속 타임아웃 — 조용한 소켓과 같다.
                None | Some(Beat::Timeout) => {
                    Err(io::Error::new(io::ErrorKind::WouldBlock, "timed out"))
                }
                Some(Beat::Bytes(bytes)) => {
                    let n = bytes.len().min(buf.len());
                    buf[..n].copy_from_slice(&bytes[..n]);
                    if n < bytes.len() {
                        self.beats.push_front(Beat::Bytes(bytes[n..].to_vec()));
                    }
                    Ok(n)
                }
            }
        }
    }

    #[test]
    fn a_timeout_in_the_middle_of_a_frame_keeps_the_bytes_already_read() {
        // ★ 실측 결함 pytmux-169. 읽기 타임아웃(250ms)이 프레임 도중에 나면 종전에는
        // `read_exact` 가 이미 소비한 바이트를 버렸고, 다시 부른 호출부가 JSON 한복판을
        // 길이 프리픽스로 읽었다 — "프레임이 너무 크다: 577004915"(= `b'"des'`).
        let wire = framed(br#"{"t":"layout","cols":80}"#);
        let mut reader = Scripted::new(vec![
            Beat::Bytes(wire[..2].to_vec()), // 헤더도 쪼개진다
            Beat::Timeout,
            Beat::Bytes(wire[2..10].to_vec()),
            Beat::Timeout,
            Beat::Timeout,
            Beat::Bytes(wire[10..].to_vec()),
        ]);
        let value = read_frame(&mut reader, MAX_FRAME).unwrap();
        assert_eq!(value["t"], "layout");
        assert_eq!(value["cols"], 80);
    }

    #[test]
    fn a_timeout_before_the_frame_starts_stays_would_block() {
        // 조용한 서버는 정상이다. 이것이 `Connection::next_message` 의 `Ok(None)` 이 되므로
        // 다른 오류로 바꾸면 사용자가 가만히 있을 때마다 연결이 끊긴 것으로 보고된다.
        let mut reader = Scripted::new(vec![Beat::Timeout]);
        match read_frame(&mut reader, MAX_FRAME).unwrap_err() {
            FrameError::Io(e) => assert!(is_timeout(&e), "타임아웃으로 안 왔다: {e:?}"),
            other => panic!("아직 안 온 것을 오류로 바꿨다: {other:?}"),
        }
    }

    #[test]
    fn a_frame_that_stops_mid_way_is_reported_not_silently_desynced() {
        // 인내가 다하면 어긋난 채 계속 읽지 않고 끊는다. 인내를 인자로 받는 덕에
        // 이 오라클이 5초가 아니라 그 자리에서 끝난다.
        let wire = framed(br#"{"t":"status"}"#);
        let mut reader = Scripted::new(vec![Beat::Bytes(wire[..7].to_vec())]);
        let mut header = [0u8; 4];
        fill(&mut reader, &mut header, false, Duration::from_millis(10)).unwrap();
        let mut payload = vec![0u8; u32::from_be_bytes(header) as usize];
        match fill(&mut reader, &mut payload, true, Duration::from_millis(10)).unwrap_err() {
            FrameError::Stalled { got, want } => {
                assert_eq!(got, 3);
                assert_eq!(want, wire.len() - 4);
            }
            other => panic!("반만 온 프레임을 다른 것으로 말했다: {other:?}"),
        }
    }

    #[test]
    fn patience_refills_while_the_frame_keeps_arriving() {
        // 조금씩이라도 오면 계속 기다린다 — 재는 것은 "오래 걸린다"가 아니라 "멎었다"다.
        let wire = framed(br#"{"t":"screen","pane":1}"#);
        let mut beats = vec![Beat::Bytes(wire[..4].to_vec())];
        for chunk in wire[4..].chunks(3) {
            beats.push(Beat::Timeout);
            beats.push(Beat::Bytes(chunk.to_vec()));
        }
        let mut reader = Scripted::new(beats);
        let value = read_frame(&mut reader, MAX_FRAME).unwrap();
        assert_eq!(value["pane"], 1);
    }

    #[test]
    fn frame_limits_match_the_server_constants() {
        // 서버(pytmuxlib/protocol.py)와 **값으로** 공유하는 상수다. 한쪽만 바뀌면
        // 조용히 깨지므로 여기서 값을 직접 못박는다.
        assert_eq!(MAX_FRAME, 64 * 1024 * 1024);
        assert_eq!(HANDSHAKE_MAX_FRAME, 64 * 1024);
    }
}
