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
import sys

from . import ipc, proc, pty_backend, ptyhostclient


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
            "--tokenfile", host_tokenfile(sock_path)]
    try:
        proc.spawn_detached(argv)
        _spawned_hosts.add(sock_path)
        return True
    except Exception:
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
    # 이미 떠 있는 host 면 prespawn 하지 않는다(재시작 = 기존 host 재사용). 리스닝 여부는
    # portfile(Windows)·소켓 경로(POSIX) 존재로 가늠한다. stale 이면 여기선 skip 하지만
    # 뒤이은 ensure_connected 의 재연결이 실패→_spawn_host 가 새로 띄운다(무중복 보장).
    try:
        if pty_backend.IS_WINDOWS:
            exists = _read_portfile(sock_path) is not None
        else:
            exists = os.path.exists(listen_endpoint(sock_path))
    except Exception:
        exists = False
    if exists:
        return
    _spawn_host(sock_path)


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
