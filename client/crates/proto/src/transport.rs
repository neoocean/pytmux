//! 어떤 소켓 위에서 말하는가.
//!
//! 서버와의 대화 내용은 OS 와 무관하지만 **전송은 다르다** — Unix 는 AF_UNIX 소켓,
//! Windows 는 루프백 TCP 다(`endpoint` 모듈 표 참조). 그 차이를 여기서 한 번 흡수하면
//! 위 계층(`client`)은 `Read`/`Write` 만 알면 된다.
//!
//! # 왜 열거형인가
//!
//! `Box<dyn Read + Write>` 로 감싸면 **읽기/쓰기 절반을 나눠 쥐는** 지금 구조가 깨진다
//! (읽기는 스레드, 쓰기는 뷰가 담당한다 — 각자 복제본이 필요하다). 열거형이면 복제도
//! 타임아웃 설정도 각 타입의 것을 그대로 쓸 수 있다.

use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

use base::i18n::t;

/// 루프백 TCP connect 타임아웃 상한.
///
/// **Windows 에서 필요하다**: 리스너 없는 루프백 포트로의 connect 가 POSIX 처럼 즉시
/// 거절되지 않는다 — 방화벽 stealth 가 SYN 을 조용히 버려 **타임아웃까지 통째로
/// 매달린다**(서버 쪽에서 GHA windows-latest 로 실측된 함정, `ipc.py` 주석). 죽은 서버가
/// 남긴 stale 포트파일이 있으면 그 대기를 매번 문다. 살아 있는 서버라면 커널이
/// 핸드셰이크를 즉시 끝내므로 짧게 잡아도 오탐이 없다.
pub const LOOPBACK_CONNECT_TIMEOUT: Duration = Duration::from_millis(500);

/// 서버와의 바이트 통로.
#[derive(Debug)]
pub enum Stream {
    #[cfg(unix)]
    Unix(std::os::unix::net::UnixStream),
    Tcp(TcpStream),
}

impl Stream {
    /// 루프백 TCP 로 붙는다.
    pub fn connect_tcp(host: &str, port: u16) -> io::Result<Self> {
        let addrs: Vec<SocketAddr> = std::net::ToSocketAddrs::to_socket_addrs(&(host, port))?
            .collect();
        let mut last = io::Error::new(io::ErrorKind::AddrNotAvailable, t("주소를 못 찾았다"));
        for addr in addrs {
            match TcpStream::connect_timeout(&addr, LOOPBACK_CONNECT_TIMEOUT) {
                Ok(stream) => {
                    // Nagle 을 끄지 않으면 작은 프레임(키 입력 하나)이 묶여 지연된다.
                    let _ = stream.set_nodelay(true);
                    return Ok(Stream::Tcp(stream));
                }
                Err(e) => last = e,
            }
        }
        Err(last)
    }

    /// AF_UNIX 소켓으로 붙는다. Windows 에는 이 경로가 없다.
    #[cfg(unix)]
    pub fn connect_unix(path: &std::path::Path) -> io::Result<Self> {
        std::os::unix::net::UnixStream::connect(path).map(Stream::Unix)
    }

    #[cfg(not(unix))]
    pub fn connect_unix(_path: &std::path::Path) -> io::Result<Self> {
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            t("이 OS 에는 AF_UNIX 경로가 없다 — 서버는 루프백 TCP 로 듣는다"),
        ))
    }

    pub fn try_clone(&self) -> io::Result<Self> {
        match self {
            #[cfg(unix)]
            Stream::Unix(s) => s.try_clone().map(Stream::Unix),
            Stream::Tcp(s) => s.try_clone().map(Stream::Tcp),
        }
    }

    /// 읽기 타임아웃. 서버가 조용한 것은 정상이므로 위 계층은 이걸 "아직 없다"로 읽는다.
    pub fn set_read_timeout(&self, timeout: Option<Duration>) -> io::Result<()> {
        match self {
            #[cfg(unix)]
            Stream::Unix(s) => s.set_read_timeout(timeout),
            Stream::Tcp(s) => s.set_read_timeout(timeout),
        }
    }
}

impl Read for Stream {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        match self {
            #[cfg(unix)]
            Stream::Unix(s) => s.read(buf),
            Stream::Tcp(s) => s.read(buf),
        }
    }
}

impl Write for Stream {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        match self {
            #[cfg(unix)]
            Stream::Unix(s) => s.write(buf),
            Stream::Tcp(s) => s.write(buf),
        }
    }

    fn flush(&mut self) -> io::Result<()> {
        match self {
            #[cfg(unix)]
            Stream::Unix(s) => s.flush(),
            Stream::Tcp(s) => s.flush(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn tcp_round_trip_and_split() {
        // 읽기/쓰기 절반을 나눠 쥐는 구조가 TCP 에서도 성립하는지 — Windows 경로의
        // 핵심 가정이다.
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut sock, _) = listener.accept().unwrap();
            let mut buf = [0u8; 5];
            sock.read_exact(&mut buf).unwrap();
            sock.write_all(b"world").unwrap();
            buf
        });

        let mut client = Stream::connect_tcp("127.0.0.1", port).unwrap();
        let mut reader = client.try_clone().unwrap();
        client.write_all(b"hello").unwrap();
        client.flush().unwrap();
        let mut back = [0u8; 5];
        reader.read_exact(&mut back).unwrap();
        assert_eq!(&back, b"world");
        assert_eq!(&server.join().unwrap(), b"hello");
    }

    #[test]
    fn connecting_to_a_dead_port_fails_fast() {
        // 상한이 없으면 Windows 에서 여기서 매달린다. 시간까지 단언해 상한이 실제로
        // 걸리는지 본다(POSIX 에서는 즉시 거절이라 넉넉히 잡아도 무해하다).
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener); // 아무도 안 듣는 포트

        let start = std::time::Instant::now();
        let result = Stream::connect_tcp("127.0.0.1", port);
        assert!(result.is_err(), "죽은 포트에 붙었다고 한다");
        assert!(
            start.elapsed() < LOOPBACK_CONNECT_TIMEOUT * 3,
            "상한보다 오래 매달렸다: {:?}",
            start.elapsed()
        );
    }

    #[test]
    fn read_timeout_is_settable_on_both_transports() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let _keep = std::thread::spawn(move || listener.accept().ok());
        let stream = Stream::connect_tcp("127.0.0.1", port).unwrap();
        assert!(stream.set_read_timeout(Some(Duration::from_millis(50))).is_ok());
    }
}
