"""크로스플랫폼 IPC(클라이언트↔서버 소켓) 추상층 (docs/WINDOWS_PORT.md §6-1 ④).

서버/클라이언트가 OS 별 소켓 분기를 직접 알지 않고 이 모듈만 부르도록 갇히는 곳.

  * **Unix**: 지금처럼 `AF_UNIX`(파일시스템 경로) 소켓. `asyncio.start_unix_server`
    /`open_unix_connection`.
  * **Windows**: AF_UNIX 의 asyncio 지원이 불완전 → **TCP 루프백(127.0.0.1)** 으로
    대체. 서버가 임의(에페메럴) 포트에 바인드한 뒤 실제 포트를 **포트파일**에 적고,
    클라이언트는 그 파일을 읽어 접속한다(`start_server`/`open_connection`).

엔드포인트는 **문자열 한 개**로 표현한다(기존 `sock_path: str` 스레딩을 그대로
유지하기 위함):

  * Unix    : 파일시스템 경로            예) /run/user/1000/pytmux/default.sock
  * TCP     : "tcp:HOST:PORT"            예) tcp:127.0.0.1:54321
              PORT 0 = "에페메럴 바인드 후 포트파일에 게시" (서버 기동 시 확정)

`parse_endpoint()` 로 어느 전송인지 판별하고, 나머지 함수가 그에 맞춰 분기한다.
프레이밍(길이프리픽스+JSON)은 `protocol.read_msg`/`write_msg` 가 전송 무관하게
담당하므로 여기선 연결만 책임진다.
"""
from __future__ import annotations

import asyncio
import os
import socket
from typing import Awaitable, Callable, Optional, Tuple


IS_WINDOWS = os.name == "nt"

__all__ = [
    "IS_WINDOWS", "parse_endpoint", "is_tcp",
    "default_state_dir", "default_endpoint", "default_endpoint_candidates",
    "resolve_default_endpoint", "portfile_for", "state_base",
    "start_server", "open_connection", "probe", "control_socket",
]


# ─────────────────────────────────────────────────────────────────────────────
# 엔드포인트 표현 / 기본값
# ─────────────────────────────────────────────────────────────────────────────
def parse_endpoint(endpoint: str) -> Tuple:
    """엔드포인트 문자열 → ("unix", path) 또는 ("tcp", host, port:int)."""
    if endpoint.startswith("tcp:"):
        rest = endpoint[len("tcp:"):]
        host, _, port = rest.rpartition(":")
        if not host:
            host, port = "127.0.0.1", rest
        return ("tcp", host, int(port))
    return ("unix", endpoint)


def is_tcp(endpoint: str) -> bool:
    return endpoint.startswith("tcp:")


def default_state_dir() -> str:
    """런타임 상태(소켓/포트파일/슬롯·옵션 캐시)의 기본 디렉터리.

    Unix: $XDG_RUNTIME_DIR 또는 /tmp/pytmux-<uid>. Windows: %LOCALAPPDATA%\\pytmux.
    디렉터리를 만들고 가능하면 0o700 으로 좁힌다.
    """
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        runtime = os.path.join(base, "pytmux")
    else:
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/pytmux-{os.getuid()}"
    os.makedirs(runtime, exist_ok=True)
    if not IS_WINDOWS:
        try:
            os.chmod(runtime, 0o700)
        except OSError:
            pass
    return runtime


def default_endpoint() -> str:
    """OS 기본 엔드포인트. Unix=소켓 경로, Windows=에페메럴 TCP(포트파일 게시)."""
    if IS_WINDOWS:
        return "tcp:127.0.0.1:0"
    return os.path.join(default_state_dir(), "default.sock")


def default_endpoint_candidates() -> list:
    """이미 떠 있는 서버를 찾기 위한 기본 엔드포인트 후보(우선순위 순, 중복 제거).

    Unix 에서 `XDG_RUNTIME_DIR` 유무가 세션마다 갈리는 게 문제다(예: 데스크톱/
    systemd 로그인은 `/run/user/<uid>`, 단순 ssh 로그인은 미설정이라 `/tmp/pytmux-
    <uid>` 폴백). 서버를 띄운 세션과 새로 attach 하는 세션의 경로가 어긋나면 같은
    서버를 못 찾아 새 서버가 떠버린다. 두 위치를 모두 후보로 둬 어느 쪽에 떠 있든
    붙게 한다. Windows 는 LOCALAPPDATA 가 안정적이라 단일 후보."""
    if IS_WINDOWS:
        return [default_endpoint()]
    # POSIX 소켓 경로라 구분자는 항상 '/'(이 분기는 Unix 전용).
    cands = []
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        cands.append(f"{xdg.rstrip('/')}/pytmux/default.sock")
    cands.append(f"/tmp/pytmux-{os.getuid()}/default.sock")
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def resolve_default_endpoint() -> str:
    """기본 엔드포인트를 정한다(명시 --socket 이 없을 때).

    이미 서버가 떠 있는 후보가 있으면 그 엔드포인트(= attach 대상)를 돌려주고,
    없으면 canonical `default_endpoint()`(= 새로 기동할 위치)를 돌려준다. 서버가
    없을 때 동작은 종전과 동일하고, ssh 등으로 경로가 어긋난 채 서버가 떠 있을 때만
    그 서버를 찾아 붙는다(요청)."""
    for cand in default_endpoint_candidates():
        if probe(cand):
            return cand
    return default_endpoint()


def state_base(endpoint: str) -> str:
    """상태파일(slots/opts/capture/layout) 경로의 프리픽스.

    Unix 소켓이면 소켓 경로 자체(고정 소켓→안정, 임시 소켓→테스트 격리). TCP
    엔드포인트("tcp:host:port")면 콜론 등 파일명 불가 문자를 피하고 포트가 바뀌어도
    안정적이도록 상태 디렉터리(default_state_dir)의 고정 prefix 를 쓴다
    (docs/WINDOWS_PORT.md §7-c-4)."""
    if is_tcp(endpoint):
        return os.path.join(default_state_dir(), "default")
    return endpoint


def portfile_for(endpoint: str) -> str:
    """TCP 엔드포인트의 실제 포트를 게시/조회하는 파일 경로.

    Unix 소켓 경로면 `<path>.port`, "tcp:..." 면 상태 디렉터리의 고정 파일.
    """
    if is_tcp(endpoint):
        return os.path.join(default_state_dir(), "default.port")
    return endpoint + ".port"


def _read_portfile(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _write_portfile(path: str, port: int) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(port))
    os.replace(tmp, path)


ClientCb = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


# ─────────────────────────────────────────────────────────────────────────────
# 서버: listen
# ─────────────────────────────────────────────────────────────────────────────
async def start_server(endpoint: str, on_connected: ClientCb, *,
                       portfile: Optional[str] = None) -> Tuple[asyncio.AbstractServer, str]:
    """엔드포인트에서 listen 을 시작한다. (server, resolved_endpoint) 반환.

    TCP 에페메럴(PORT 0)이면 실제 포트로 바인드한 뒤 포트파일에 게시하고, 확정된
    "tcp:HOST:PORT" 를 resolved_endpoint 로 돌려준다(서버가 PYTMUX 환경에 심을 값).
    Unix 면 stale 소켓을 지우고 start_unix_server, 0o600 으로 좁힌다.
    """
    kind = parse_endpoint(endpoint)
    if kind[0] == "tcp":
        _, host, port = kind
        server = await asyncio.start_server(on_connected, host, port)
        actual = server.sockets[0].getsockname()[1] if port == 0 else port
        resolved = f"tcp:{host}:{actual}"
        pf = portfile or portfile_for(endpoint)
        _write_portfile(pf, actual)
        return server, resolved
    # unix
    path = kind[1]
    if os.path.exists(path):
        os.unlink(path)
    server = await asyncio.start_unix_server(on_connected, path=path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return server, path


# ─────────────────────────────────────────────────────────────────────────────
# 클라이언트: connect
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_tcp_port(host: str, port: int, portfile: Optional[str],
                      endpoint: str) -> Optional[int]:
    if port != 0:
        return port
    return _read_portfile(portfile or portfile_for(endpoint))


async def open_connection(endpoint: str, *, portfile: Optional[str] = None
                          ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """엔드포인트에 비동기 연결. (reader, writer) 반환."""
    kind = parse_endpoint(endpoint)
    if kind[0] == "tcp":
        _, host, port = kind
        rport = _resolve_tcp_port(host, port, portfile, endpoint)
        if rport is None:
            raise ConnectionError(f"포트파일에서 포트를 못 읽음: {endpoint}")
        return await asyncio.open_connection(host, rport)
    return await asyncio.open_unix_connection(path=kind[1])


def control_socket(endpoint: str, *, portfile: Optional[str] = None,
                   timeout: float = 2.0) -> Optional[socket.socket]:
    """동기 제어 요청용(launcher) 연결된 소켓. 실패 시 None.

    Unix=AF_UNIX, TCP=AF_INET. 호출자가 sendall/recv 후 close 한다.
    """
    kind = parse_endpoint(endpoint)
    if kind[0] == "tcp":
        _, host, port = kind
        rport = _resolve_tcp_port(host, port, portfile, endpoint)
        if rport is None:
            return None
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target: object = (host, rport)
    else:
        if not os.path.exists(kind[1]):
            return None
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        target = kind[1]
    s.settimeout(timeout)
    try:
        s.connect(target)
        s.settimeout(None)
        return s
    except OSError:
        s.close()
        return None


def probe(endpoint: str, *, portfile: Optional[str] = None) -> bool:
    """서버가 떠 있어 접속 가능한지 동기 검사(launcher.can_connect 대체)."""
    s = control_socket(endpoint, portfile=portfile)
    if s is None:
        return False
    s.close()
    return True
