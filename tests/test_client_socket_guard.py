"""F3 후속(검수 2026-08-05) — **클라 연결 경로**에도 소켓 검증이 산다.

검수 2026-07-17 PTYH-2 가 만든 `ipc.validate_local_socket` 은 그동안 `ptyhostclient`
하나만 불렀다. 정작 클라↔서버 경로에는 아무 검사가 없었다:

  `launcher.py` → `ipc.resolve_default_endpoint()` → `default_endpoint_candidates()`
  → `probe()` → 살아 있으면 그 후보를 그대로 반환 → `clientconn` 이 거기에 hello 를 보낸다

후보 문자열은 `default_state_dir()` 를 안 거치므로 **F3 의 상태 디렉터리 검사도 안 탄다**.
실측(2026-08-05): `/tmp/pytmux-<uid>` 를 심링크로 선점하면 `default_endpoint()` 는
"상태 디렉터리가 심볼릭 링크임" 으로 거부하는데 `resolve_default_endpoint()` 는 같은
자리를 돌려주고, `{'t':'hello', …, 'token': …}` 프레임이 그 리스너에 그대로 도착했다.
붙는 순간 **키 입력 전량**이 그쪽으로 간다(가짜 sudo 프롬프트도 그쪽이 그린다).

되돌리면 실패해야 하는 오라클:
  · `open_connection` 의 `_guard_local_socket` 을 빼면 → test_open_connection_refuses_*
  · `control_socket` 의 가드를 빼면 → test_probe_refuses_a_foreign_socket
  · 가드가 `RuntimeError` 를 그대로 흘리면 → test_refusal_is_an_oserror(재접속 루프가
    `except OSError` 라 클라가 통째로 죽는다)
"""
import asyncio
import os
import socket
import tempfile

import harness
from pytmuxlib import ipc


def _listener(path, mode=0o600):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    os.chmod(path, mode)
    s.listen(4)
    return s


def _skip_on_windows():
    if ipc.IS_WINDOWS:
        from run import skip
        skip("POSIX 전용(Windows 는 TCP 루프백 — 상호인증 몫)")


async def test_open_connection_refuses_a_loose_socket():
    """다른 사용자에게 열린 소켓에는 **붙지 않는다** — 붙으면 그 다음 줄이 토큰이다."""
    _skip_on_windows()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "default.sock")
    s = _listener(p, mode=0o666)
    try:
        try:
            await ipc.open_connection(p)
        except ConnectionError:
            return
        raise AssertionError("0666 소켓에 붙었다")
    finally:
        s.close()


async def test_open_connection_refuses_a_symlinked_socket():
    """심링크는 `/tmp/pytmux-<uid>` 선점의 실제 모양이다(F3 가 이름으로 막는 그것)."""
    _skip_on_windows()
    d = tempfile.mkdtemp()
    real = os.path.join(d, "real.sock")
    link = os.path.join(d, "default.sock")
    s = _listener(real)
    os.symlink(real, link)
    try:
        try:
            await ipc.open_connection(link)
        except ConnectionError:
            return
        raise AssertionError("심링크 소켓에 붙었다")
    finally:
        s.close()


async def test_refusal_is_an_oserror():
    """거부는 `OSError` 하위여야 한다 — `clientconn._connect_and_hello` 의 재시도는
    `except (ConnectionError, FileNotFoundError, OSError)` 다. `RuntimeError` 가 그대로
    새면 재시도가 아니라 **앱 종료**가 된다."""
    _skip_on_windows()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "default.sock")
    s = _listener(p, mode=0o666)
    try:
        try:
            await ipc.open_connection(p)
        except OSError:
            return
        except BaseException as e:      # noqa: BLE001 — 형(型)이 곧 계약이다
            raise AssertionError(f"OSError 가 아닌 {type(e).__name__} 로 나갔다") from e
        raise AssertionError("거부하지 않았다")
    finally:
        s.close()


async def test_probe_refuses_a_foreign_socket():
    """`probe` 가 False 여야 발견 경로(`resolve_default_endpoint`)가 그 후보를 버린다.
    True 를 돌려주면 launcher 가 그 엔드포인트를 그대로 쥐고 hello 를 보낸다."""
    _skip_on_windows()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "default.sock")
    s = _listener(p, mode=0o666)
    try:
        assert ipc.probe(p) is False, "남에게 열린 소켓을 살아 있는 서버로 봤다"
    finally:
        s.close()


async def test_our_own_socket_still_connects():
    """무회귀 — 우리 uid·0600 소켓(= `start_server` 가 만드는 모양)은 그대로 붙는다."""
    _skip_on_windows()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "default.sock")
    accepted = []

    async def on_conn(reader, writer):
        accepted.append(True)
        writer.close()

    server = await asyncio.start_unix_server(on_conn, path=p)
    os.chmod(p, 0o600)
    try:
        assert ipc.probe(p) is True, "우리 소켓을 거부했다"
        reader, writer = await ipc.open_connection(p)
        writer.close()
        # 고정 대기 금지(tests/test_wait_convention.py) — 조건이 설 때까지 폴링한다.
        await harness.wait_for(lambda: bool(accepted))
        assert accepted, "연결이 서버에 도달하지 않았다"
    finally:
        server.close()
        await server.wait_closed()
