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

use std::io::{self, Read, Write};

use crate::message::MAX_FRAME;

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
    #[error("{}", err_too_large(.advertised, .limit))]
    TooLarge { advertised: usize, limit: usize },
    #[error("{}", err_io(.0))]
    Io(#[from] io::Error),
    /// 페이로드가 JSON 이 아니거나 기대한 모양이 아니다.
    #[error("{}", err_decode(.0))]
    Decode(#[from] serde_json::Error),
}

fn err_too_large(advertised: &usize, limit: &usize) -> String {
    base::i18n::tf(
        "프레임이 너무 크다: {advertised} 바이트 (상한 {limit})",
        &[
            ("advertised", advertised.to_string().as_str()),
            ("limit", limit.to_string().as_str()),
        ],
    )
}

fn err_io(err: &io::Error) -> String {
    base::i18n::tf("입출력 오류: {err}", &[("err", err.to_string().as_str())])
}

fn err_decode(err: &serde_json::Error) -> String {
    base::i18n::tf("프레임을 해석할 수 없다: {err}", &[("err", err.to_string().as_str())])
}

/// 프레임 하나를 읽어 JSON 값으로 돌려준다.
///
/// `limit` 는 이 프레임에 허용할 최대 페이로드 크기다. 핸드셰이크 중에는
/// [`HANDSHAKE_MAX_FRAME`](crate::message::HANDSHAKE_MAX_FRAME) 을, 그 뒤에는
/// [`MAX_FRAME`](crate::message::MAX_FRAME) 을 넘긴다.
pub fn read_frame<R: Read>(reader: &mut R, limit: usize) -> Result<serde_json::Value, FrameError> {
    let mut header = [0u8; 4];
    match reader.read_exact(&mut header) {
        Ok(()) => {}
        Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => return Err(FrameError::Closed),
        Err(e) => return Err(FrameError::Io(e)),
    }
    let advertised = u32::from_be_bytes(header) as usize;
    if advertised > limit {
        // 읽지 않는다 — 광고된 크기를 믿고 할당하는 것이 바로 그 공격이다.
        return Err(FrameError::TooLarge {
            advertised,
            limit,
        });
    }
    let mut payload = vec![0u8; advertised];
    match reader.read_exact(&mut payload) {
        Ok(()) => {}
        Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => return Err(FrameError::Closed),
        Err(e) => return Err(FrameError::Io(e)),
    }
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
            FrameError::TooLarge { advertised, limit } => {
                assert_eq!(advertised, u32::MAX as usize);
                assert_eq!(limit, MAX_FRAME);
            }
            other => panic!("상한 초과로 안 걸렀다: {other:?}"),
        }
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

    #[test]
    fn frame_limits_match_the_server_constants() {
        // 서버(pytmuxlib/protocol.py)와 **값으로** 공유하는 상수다. 한쪽만 바뀌면
        // 조용히 깨지므로 여기서 값을 직접 못박는다.
        assert_eq!(MAX_FRAME, 64 * 1024 * 1024);
        assert_eq!(HANDSHAKE_MAX_FRAME, 64 * 1024);
    }
}
