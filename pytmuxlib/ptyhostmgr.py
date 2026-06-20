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
    """pty-host 데몬을 detached 로 띄운다(서버보다 오래 산다). 성공 여부를 돌려준다."""
    argv = [sys.executable, "-m", "pytmuxlib.ptyhost",
            "--endpoint", listen_endpoint(sock_path),
            "--portfile", host_portfile(sock_path),
            "--tokenfile", host_tokenfile(sock_path)]
    try:
        proc.spawn_detached(argv)
        return True
    except Exception:
        # M7: 종전엔 무로깅 suppress 라 spawn 실패 시 6초 폴링 낭비 후 인프로세스
        # 폴백하면서 원인이 0 로그였다. 실패를 stderr 에 남기고 False 를 돌려
        # ensure_connected 가 헛된 폴링을 건너뛰게 한다(폴백 자체는 그대로).
        import traceback
        traceback.print_exc()
        return False


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
        return client
    # 2) 없으면 새로 띄우고, 리슨이 뜰 때까지 폴링 후 연결. spawn 자체가 실패하면
    #    6초 폴링은 무의미하므로 즉시 폴백(M7).
    if not _spawn_host(sock_path):
        return None
    for _ in range(60):                    # 최대 ~6s
        await asyncio.sleep(0.1)
        client = await _try_connect(loop, sock_path, 1.0)
        if client is not None:
            return client
    return None
