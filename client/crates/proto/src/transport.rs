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

    /// 쓰기 타임아웃(검수 pytmux-34 #6). 읽기와 달리 이것은 **끊김**이다 — 반쯤 쓴 프레임
    /// 뒤로는 스트림이 어긋나므로 위 계층은 연결을 접는다([`Self::shutdown`]).
    pub fn set_write_timeout(&self, timeout: Option<Duration>) -> io::Result<()> {
        match self {
            #[cfg(unix)]
            Stream::Unix(s) => s.set_write_timeout(timeout),
            Stream::Tcp(s) => s.set_write_timeout(timeout),
        }
    }

    pub fn write_timeout(&self) -> io::Result<Option<Duration>> {
        match self {
            #[cfg(unix)]
            Stream::Unix(s) => s.write_timeout(),
            Stream::Tcp(s) => s.write_timeout(),
        }
    }

    /// 양쪽을 닫는다 — 같은 소켓을 쥔 **읽기 스레드도** 깨어나 끝난다(복제본도 같은 소켓).
    pub fn shutdown(&self) -> io::Result<()> {
        match self {
            #[cfg(unix)]
            Stream::Unix(s) => s.shutdown(std::net::Shutdown::Both),
            Stream::Tcp(s) => s.shutdown(std::net::Shutdown::Both),
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
    fn a_write_to_a_peer_that_never_reads_gives_up_instead_of_hanging() {
        // ☠ 검수 pytmux-34 #6: 쓰기 타임아웃이 없으면 서버 루프가 멈춘 동안의 큰 붙여넣기가
        //   송신 버퍼를 채우고 `write_all` 이 **UI 스레드를 무기한** 막는다. 읽지 않는
        //   상대에게 수 MiB 를 밀어 넣어 상한 안에 `Err` 로 돌아오는지 본다.
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let (park_tx, park_rx) = std::sync::mpsc::channel::<()>();
        let peer = std::thread::spawn(move || {
            // 받기만 하고 안 읽는다 — 시험이 끝날 때까지 소켓을 쥐고 있는다.
            let held = listener.accept().ok();
            let _ = park_rx.recv();
            drop(held);
        });
        let mut stream = Stream::connect_tcp("127.0.0.1", port).unwrap();
        stream.set_write_timeout(Some(Duration::from_millis(300))).unwrap();
        // 쓰기는 따로 돌린다 — 상한이 빠지면 시험이 **매달리지 않고 떨어지게**.
        let (done_tx, done_rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let chunk = vec![b'x'; 1 << 20];
            // 커널 버퍼(수 MiB 까지 자동 조정될 수 있다)가 차기 전에는 쓰기가 성공한다 —
            // 그래서 넉넉히 민다.
            let mut result = Ok(());
            for _ in 0..256 {
                result = stream.write_all(&chunk);
                if result.is_err() {
                    break;
                }
            }
            let _ = done_tx.send(result.is_err());
        });
        let verdict = done_rx.recv_timeout(Duration::from_secs(20));
        let _ = park_tx.send(());
        let _ = peer.join();
        match verdict {
            Ok(errored) => assert!(errored, "읽지 않는 상대에게 256 MiB 를 다 썼다고 한다"),
            Err(_) => panic!("읽지 않는 상대에게 쓰다가 20초 넘게 매달렸다 — 쓰기 상한이 없다"),
        }
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
