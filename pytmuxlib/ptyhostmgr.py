"""서버측 PTY host 모드 관리 — 게이팅·host 기동·연결 (Windows 세션유지 재시작 옵션 C P4).

서버가 host 모드일 때, 장수명 pty-host 프로세스(`python -m pytmuxlib.ptyhost`)를 **detached**
로 띄워(서버보다 오래 산다) endpoint 를 통해 `PtyHostClient` 로 연결한다. host 가 이미 떠
있으면(서버 재시작) 그것에 재연결한다 — 이게 세션 유지의 핵심.

게이팅: Windows 기본 ON(롤백 `PYTMUX_PTY_HOST=0`). POSIX 기본 OFF지만 `PYTMUX_PTY_HOST=1`
로 강제할 수 있다(host 가 백엔드 중립이라 POSIX 에서 _UnixPty 로 전 경로 테스트 가능).

엔드포인트: POSIX=AF_UNIX 경로(결정적). Windows=TCP 루프백(포트는 host 가 동적 바인드 후
portfile 에 기록 → 서버가 읽어 연결). 연결 실패 시 서버는 host 모드를 끄고 인프로세스 백엔드
로 폴백한다(host 버그가 Windows 를 벽돌로 만들지 않게 — 기존 owned-ConPTY 폴백과 동일 철학).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import sys
import time

from . import ipc, proc, pty_backend, ptyhostclient, ptyhostproto as _proto


# 콜드 스타트 host 연결 폴 백오프 상수(WINDOWS_SLOWNESS_..._2026-07-06 §5 레버 G).
# 종전엔 고정 0.1s 폴이라 spawn 직후 무조건 100ms 를 허비하고 host 준비를 최대 100ms
# 늦게 알아챘다. 첫 시도를 곧바로 하고 촘촘히(5ms~) 지수 백오프 후 상한(0.1s)까지 폴하면
# host 가 준비되는 즉시(≈현재 폴 간격 이내) 잡아 콜드 스타트 임계경로를 줄인다. 총 예산
# (~6s)은 종전과 동일하게 유지(spawn 실패·웨지 시의 폴백 지연 상한 불변).
_CONNECT_BUDGET = 6.0        # 폴링 총 예산(초) — 종전 60×0.1s 와 동일
_CONNECT_POLL_INITIAL = 0.005
_CONNECT_POLL_BACKOFF = 1.6
_CONNECT_POLL_CAP = 0.1

# 이 프로세스에서 이미 detached 로 띄운 host 의 sock_path 집합(레버 H prespawn 과
# ensure_connected 의 중복 spawn 방지). launcher 의 prespawn_host 가 미리 띄우면 여기에
# 기록되고, 뒤이어 serve()→ensure_connected 가 (host 가 아직 리스닝 전이라) 재연결에
# 실패해도 여기 있으면 다시 spawn 하지 않고 폴링으로 넘어간다 — host 하나만 뜬다.
_spawned_hosts: set[str] = set()


def host_enabled() -> bool:
    v = (os.environ.get("PYTMUX_PTY_HOST") or "").strip().lower()
    if v in ("0", "off", "no", "false"):
        return False
    if v in ("1", "on", "yes", "true"):
        return True
    return pty_backend.IS_WINDOWS


def _state_base(sock_path: str) -> str:
    return ipc.state_base(sock_path)


def host_portfile(sock_path: str) -> str:
    return _state_base(sock_path) + ".ptyhost.port"


def host_pidfile(sock_path: str) -> str:
    """host 가 게시하는 자기 pid 파일(R3). "host 가 이미 떠 있나" 판정을 파일 존재가
    아니라 **pid 생존**으로 하기 위한 것 — 종전엔 죽은 host 의 잔재 소켓/포트파일을
    보고 '떠 있다'고 오판해 prespawn 을 건너뛰거나, 반대로 아직 bind 전인 host 를 못 보고
    **중복 spawn** 했다(고아 host 의 주요 원인 중 하나)."""
    return _state_base(sock_path) + ".ptyhost.pid"


def _spawn_lockfile(sock_path: str) -> str:
    return _state_base(sock_path) + ".ptyhost.spawnlock"


def read_host_pid(sock_path: str) -> int | None:
    try:
        with open(host_pidfile(sock_path), encoding="ascii") as f:
            pid = int(f.read().strip())
        return pid if pid > 0 else None
    except (OSError, ValueError):
        return None


def host_running(sock_path: str) -> bool:
    """살아있는 host 가 이 endpoint 를 소유하고 있나. pidfile 이 있으면 pid 생존으로,
    없으면(구버전 host·게시 실패) 종전처럼 소켓/포트파일 존재로 가늠한다."""
    pid = read_host_pid(sock_path)
    if pid is not None:
        return proc.is_alive(pid)
    try:
        if pty_backend.IS_WINDOWS:
            return _read_portfile(sock_path) is not None
        return os.path.exists(listen_endpoint(sock_path))
    except Exception:
        return False


def host_tokenfile(sock_path: str) -> str:
    """host 가 게시하는 연결 인증 토큰 파일(0600). 메인 채널의 default.token 과 충돌하지
    않도록 sock_path 파생 고유 경로를 쓴다(M1)."""
    return _state_base(sock_path) + ".ptyhost.token"


def _read_host_token(sock_path: str) -> str | None:
    try:
        with open(host_tokenfile(sock_path), encoding="ascii") as f:
            return f.read().strip() or None
    except OSError:
        return None


def listen_endpoint(sock_path: str) -> str:
    """host 에게 --endpoint 로 넘길 리슨 주소."""
    if pty_backend.IS_WINDOWS:
        return "tcp:127.0.0.1:0"            # 에페메럴 → portfile 로 실제 포트 보고
    return _state_base(sock_path) + ".ptyhost.sock"


def _read_portfile(sock_path: str) -> int | None:
    try:
        with open(host_portfile(sock_path), encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


async def _connect_endpoint(sock_path: str) -> str | None:
    """현재 연결해야 할 endpoint. POSIX=unix 경로(존재하면), Windows=portfile 의 포트."""
    if pty_backend.IS_WINDOWS:
        port = _read_portfile(sock_path)
        return f"tcp:127.0.0.1:{port}" if port else None
    path = listen_endpoint(sock_path)
    return path if os.path.exists(path) else None


# spawn 직렬화 락(R3). 서버 두 개가 같은 endpoint 로 동시에 콜드 스타트하면, 종전엔
# "소켓/포트파일이 아직 없다"는 이유로 **둘 다** host 를 띄웠다 — 후발 host 가 unix 경로를
# unlink 후 재bind(Windows 는 portfile 덮어쓰기)해 **선발 host 는 도달 불가한 고아**가 됐다.
# TTL 은 크래시로 남은 락을 스스로 풀기 위한 것(락 소유자가 죽어도 20초 뒤 재개).
_SPAWN_LOCK_TTL = 20.0


def _acquire_spawn_lock(sock_path: str) -> bool:
    """spawn 권한을 얻으면 True. 다른 프로세스가 spawn 중이면 False(호출부는 폴링으로
    그 host 를 기다린다). 락 파일을 만들 수 없는 환경이면 True(무락 = 종전 거동)."""
    path = _spawn_lockfile(sock_path)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                fresh = (time.time() - os.path.getmtime(path)) < _SPAWN_LOCK_TTL
            except OSError:
                fresh = False
            if fresh:
                return False
            with contextlib.suppress(OSError):
                os.unlink(path)          # stale(크래시 잔재) → 스틸 후 재시도
        except OSError:
            return True
    return False


def _release_spawn_lock(sock_path: str) -> None:
    """내가 건 락만 푼다(TTL 로 스틸당한 뒤 남의 락을 지우지 않게)."""
    path = _spawn_lockfile(sock_path)
    try:
        with open(path, encoding="ascii") as f:
            if f.read().strip() != str(os.getpid()):
                return
    except OSError:
        return
    with contextlib.suppress(OSError):
        os.unlink(path)


def _spawn_host(sock_path: str) -> bool:
    """pty-host 데몬을 detached 로 띄운다(서버보다 오래 산다). 성공 여부를 돌려준다.

    이 프로세스에서 같은 sock_path 로 이미 띄웠으면(예: launcher prespawn) **중복 spawn
    하지 않고** True 를 돌려준다 — host 하나만 뜨게 보장한다(레버 H). host 는 detached 라
    이 프로세스가 죽어도 살아, 한 번 띄운 사실은 프로세스 수명 동안 유효하다."""
    if sock_path in _spawned_hosts:
        return True
    argv = [sys.executable, "-m", "pytmuxlib.ptyhost",
            "--endpoint", listen_endpoint(sock_path),
            "--portfile", host_portfile(sock_path),
            "--tokenfile", host_tokenfile(sock_path),
            "--pidfile", host_pidfile(sock_path)]
    if not _acquire_spawn_lock(sock_path):
        # 다른 프로세스가 지금 host 를 띄우는 중(R3) — 중복 spawn 대신 그 host 를
        # 기다린다. True 를 돌려 ensure_connected 가 폴링 단계로 넘어가게 한다.
        _spawned_hosts.add(sock_path)
        return True
    try:
        proc.spawn_detached(argv)
        _spawned_hosts.add(sock_path)
        return True
    except Exception:
        _release_spawn_lock(sock_path)
        # M7: 종전엔 무로깅 suppress 라 spawn 실패 시 6초 폴링 낭비 후 인프로세스
        # 폴백하면서 원인이 0 로그였다. 실패를 stderr 에 남기고 False 를 돌려
        # ensure_connected 가 헛된 폴링을 건너뛰게 한다(폴백 자체는 그대로).
        import traceback
        traceback.print_exc()
        return False


def prespawn_host(sock_path: str) -> None:
    """콜드 스타트 조기 host 기동(WINDOWS_SLOWNESS_..._2026-07-06 §5 레버 H).

    launcher 가 서버 하위명령에서 **무거운 `from .server import run_server`(pyte/model
    ~140ms) 직전**에 호출한다. host 인터프리터 startup(~400ms)을 서버 자신의 import·부팅과
    **겹쳐** 콜드 스타트 임계경로에서 빼는 게 목적이다(종전엔 serve()→ensure_connected 가
    서버 import 를 다 치른 뒤에야 host 를 띄워, host 준비가 클라 attach 뒤에 직렬로 남았다).

    이미 리스닝 중인 host(서버 재시작 경로)면 **건드리지 않는다** — 세션유지 host 를 죽이거나
    중복 spawn 하지 않는다. best-effort(실패해도 serve() 가 정상 경로로 폴백). host 모드가
    꺼진 환경(POSIX 기본·`PYTMUX_PTY_HOST=0`)에선 no-op."""
    if not host_enabled():
        return
    # 이미 떠 있는 host 면 prespawn 하지 않는다(재시작 = 기존 host 재사용). 판정은
    # **pid 생존**(host_running) — 종전의 파일 존재 판정은 죽은 host 의 잔재를 '살아있음'
    # 으로 오판했다(R3). stale 이면 여기서 띄우고, 그래도 못 붙으면 ensure_connected 가
    # 폴링→폴백으로 흡수한다.
    if host_running(sock_path):
        return
    _spawn_host(sock_path)


# ─────────────────────────────────────────────────────────────────────────────
# 동기 회수 경로(R2/R4) — 이벤트 루프 없이 host 를 내린다
# ─────────────────────────────────────────────────────────────────────────────
_SYNC_TIMEOUT = 1.0


def _sync_connect(sock_path: str) -> socket.socket | None:
    """host endpoint 에 blocking 소켓으로 붙어 인증까지 마친 소켓을 돌려준다."""
    if pty_backend.IS_WINDOWS:
        port = _read_portfile(sock_path)
        if not port:
            return None
        fam, addr = socket.AF_INET, ("127.0.0.1", port)
    else:
        path = listen_endpoint(sock_path)
        if not os.path.exists(path):
            return None
        fam, addr = socket.AF_UNIX, path
    s = socket.socket(fam, socket.SOCK_STREAM)
    try:
        s.settimeout(_SYNC_TIMEOUT)
        s.connect(addr)
        token = _read_host_token(sock_path)
        if token is not None:
            s.sendall(_proto.encode_json({"op": "auth", "token": token}))
        # 단발 probe 선언: host 가 이 연결을 '새 서버'로 채택하지 않게 한다. 없으면
        # 회수 판정을 위한 조회 한 번이 살아있는 서버를 host 에서 떼어낸다(R2 주석).
        s.sendall(_proto.encode_json({"op": "probe"}))
        return s
    except OSError:
        with contextlib.suppress(OSError):
            s.close()
        return None


def _sync_read_frame(s: socket.socket):
    """blocking 소켓에서 프레임 하나를 읽는다(ptyhostproto 와 같은 프레이밍)."""
    def _recvn(n: int) -> bytes | None:
        buf = b""
        while len(buf) < n:
            try:
                chunk = s.recv(n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
        return buf

    hdr = _recvn(4)
    if hdr is None:
        return None
    (ln,) = _proto._LEN.unpack(hdr)
    if ln < 1 or ln > _proto.MAX_FRAME:
        return None
    body = _recvn(ln)
    if body is None or body[:1] != _proto.TYPE_JSON:
        return None
    try:
        import json
        return json.loads(body[1:].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def shutdown_host_sync(sock_path: str, *, idle_only: bool = False) -> bool:
    """남아 있는 host 에 `shutdown` op 를 보내 내린다. 실제로 보냈으면 True.

    이벤트 루프 없이(=서버 shutdown 마지막 단계·CLI 에서) 도는 best-effort 경로다
    (PTYHOST_ORPHAN_2026-07-24 R2/R4). 두 호출처:

    ① 서버가 **폴백 모드**(`_pty_host is None`)로 돌다가 정상 종료할 때 — 뒤늦게 뜬
       host 는 아무도 소유하지 않는데 `shutdown_host()` 를 부를 핸들이 없어 영구 잔존했다.
       이 경우 `idle_only=True` 로 **패널이 0개인 host 만** 내린다. 패널이 있으면 다른
       서버가 쓰는 host 일 수 있으므로 손대지 않는다(남의 셸 파괴 금지).
    ② `pytmux kill-server` 인데 서버가 없을 때 — 사용자의 명시 종료 명령이므로
       `idle_only=False`(패널이 있어도 내린다. 그 셸들은 어차피 고아다)."""
    s = _sync_connect(sock_path)
    if s is None:
        return False
    try:
        if idle_only:
            s.sendall(_proto.encode_json({"op": "list"}))
            for _ in range(4):        # hello/list_reply 순서 무관하게 몇 프레임 읽는다
                msg = _sync_read_frame(s)
                if msg is None:
                    return False
                if msg.get("op") == "list_reply":
                    if msg.get("panes"):
                        return False          # 사용 중 — 손대지 않는다
                    break
            else:
                return False
        s.sendall(_proto.encode_json({"op": "shutdown"}))
        with contextlib.suppress(OSError):
            s.shutdown(socket.SHUT_WR)
        with contextlib.suppress(OSError):    # host 가 닫을 때까지(=수신 확인) 짧게 대기
            s.recv(4096)
        return True
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            s.close()


async def _try_connect(loop, sock_path: str, timeout: float
                       ) -> ptyhostclient.PtyHostClient | None:
    endpoint = await _connect_endpoint(sock_path)
    if endpoint is None:
        return None
    client = ptyhostclient.PtyHostClient(loop)
    try:
        token = _read_host_token(sock_path)
        await asyncio.wait_for(client.connect(endpoint, token=token), timeout)
        return client
    except Exception:
        with contextlib.suppress(Exception):
            await client.close()
        return None


async def ensure_connected(loop, sock_path: str
                           ) -> ptyhostclient.PtyHostClient | None:
    """host 에 연결한다. 없으면 detached 로 띄우고 portfile/소켓이 뜰 때까지 기다려 연결.
    실패하면 None(서버는 인프로세스 백엔드로 폴백)."""
    # 1) 이미 떠 있는 host(서버 재시작 경로)에 우선 재연결 시도.
    client = await _try_connect(loop, sock_path, 1.5)
    if client is not None:
        # 연결됐으면 prespawn 가드는 소임을 다했다 — 지워 둬야 이후 host 크래시
        # (_on_host_lost)로 이 함수가 다시 불릴 때 죽은 host 를 새로 띄울 수 있다
        # (가드가 남아 있으면 _spawn_host 가 no-op → 죽은 host 재기동 불가·회귀).
        _spawned_hosts.discard(sock_path)
        _release_spawn_lock(sock_path)
        return client
    # 2) 없으면 새로 띄우고(이미 prespawn 됐으면 재-spawn 안 함), 리슨이 뜰 때까지
    #    폴링 후 연결. spawn 자체가 실패하면 6초 폴링은 무의미하므로 즉시 폴백(M7).
    if not _spawn_host(sock_path):
        _spawned_hosts.discard(sock_path)
        return None
    # G: 고정 0.1s 폴 대신 즉시 1회 시도 후 촘촘히(5ms~) 지수 백오프하며 상한(0.1s)까지
    #    폴한다 — host 준비를 ≈현재 폴 간격 이내로 잡는다(종전 최대 100ms 지연 제거). 총
    #    예산(~6s)은 유지해 웨지/느린 host 의 폴백 상한은 종전과 같다.
    deadline = loop.time() + _CONNECT_BUDGET
    delay = _CONNECT_POLL_INITIAL
    try:
        while True:
            client = await _try_connect(loop, sock_path, 1.0)
            if client is not None:
                return client
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(min(delay, _CONNECT_POLL_CAP))
            delay *= _CONNECT_POLL_BACKOFF
    finally:
        # 성공/타임아웃 무관하게 가드 해제 — 다음 host-loss 재연결이 죽은 host 를
        # 새로 띄울 수 있게(가드는 prespawn→직후 콜드스타트 connect 의 1회 중복만 막는 용도).
        _spawned_hosts.discard(sock_path)
        _release_spawn_lock(sock_path)      # spawn 직렬화 락도 여기서 푼다(R3)
