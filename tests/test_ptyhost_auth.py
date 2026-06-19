"""pty-host 연결 인증 회귀(M1, docs/internal/SECURITY_REVIEW.md §8).

host IPC 채널은 메인 서버↔클라 채널과 동형으로 토큰(+ Unix peer-UID)으로 보호돼야
한다 — Windows 루프백 TCP 의 무인가 로컬 접속(spawn/입력주입/화면열람/shutdown)을 막는
F1 회귀 봉쇄. 여기선 POSIX unix 소켓으로 토큰 게이트의 거동을 검증한다:
  · 토큰파일이 0600 으로 게시되고 64 hex,
  · 올바른 토큰은 제어 채널 왕복 성공,
  · 틀린/누락 토큰은 거절(채널 불통),
  · 토큰 미설정 host 는 종전대로 무토큰 연결 허용(테스트 하네스 하위호환).

§9.4: 위 unix 테스트들은 Windows 를 스킵하지만(asyncio AF_UNIX 미지원), 마지막
`test_host_tcp_loopback_token_enforced` 는 **TCP 루프백**(Windows 프로덕션 경로)으로
같은 게이트를 검증해 **Windows 를 스킵하지 않는다** — windows-latest 풀 스위트가 M1
의 무인가 루프백 차단을 실측한다.
"""
import asyncio
import os
import shutil
import stat
import tempfile

from pytmuxlib import ptyhost, pty_backend
from pytmuxlib.ptyhostclient import PtyHostClient, PtyHostError


async def _serve(tokenfile=None):
    d = tempfile.mkdtemp(prefix="pytmux-hostauth-")
    endpoint = os.path.join(d, "host.sock")
    host = ptyhost.PtyHost()
    htask = asyncio.ensure_future(host.serve(endpoint, tokenfile=tokenfile))
    for _ in range(200):
        if os.path.exists(endpoint):
            break
        await asyncio.sleep(0.01)
    return host, htask, endpoint, d


async def _stop(host, htask, d, *clients):
    for c in clients:
        with __import__("contextlib").suppress(Exception):
            await c.close()
    if host._server is not None:
        host._server.close()
    htask.cancel()
    try:
        await htask
    except (asyncio.CancelledError, Exception):
        pass
    shutil.rmtree(d, ignore_errors=True)


async def test_host_token_published_0600_and_correct_token_connects():
    if pty_backend.IS_WINDOWS:
        return
    d0 = tempfile.mkdtemp(prefix="pytmux-tok-")
    tokenfile = os.path.join(d0, "host.token")
    host, htask, endpoint, d = await _serve(tokenfile=tokenfile)
    client = PtyHostClient(asyncio.get_event_loop())
    try:
        # 토큰파일이 0600 으로, 64 hex 로 게시됐다.
        assert os.path.exists(tokenfile)
        mode = stat.S_IMODE(os.lstat(tokenfile).st_mode)
        assert mode == 0o600, oct(mode)
        token = open(tokenfile, encoding="ascii").read().strip()
        assert len(token) == 64 and all(c in "0123456789abcdef" for c in token)
        # 올바른 토큰 → 인증 후 제어 채널 왕복(list)이 성립.
        await client.connect(endpoint, token=token)
        panes = await client.list_panes()
        assert panes == []
    finally:
        await _stop(host, htask, d, client)
        shutil.rmtree(d0, ignore_errors=True)


async def test_host_wrong_token_rejected():
    if pty_backend.IS_WINDOWS:
        return
    d0 = tempfile.mkdtemp(prefix="pytmux-tok-")
    tokenfile = os.path.join(d0, "host.token")
    host, htask, endpoint, d = await _serve(tokenfile=tokenfile)
    client = PtyHostClient(asyncio.get_event_loop())
    try:
        await client.connect(endpoint, token="00" * 32)   # 틀린 토큰
        # host 가 즉시 연결을 닫으므로 제어 왕복이 실패한다(예외 또는 타임아웃).
        raised = False
        try:
            await client.list_panes()
        except (PtyHostError, asyncio.TimeoutError):
            raised = True
        assert raised, "틀린 토큰인데 제어 채널이 살아있다"
        # host 는 인증 실패 연결을 현재 writer 로 채택하지 않았다.
        assert host._writer is None
    finally:
        await _stop(host, htask, d, client)
        shutil.rmtree(d0, ignore_errors=True)


async def test_host_missing_token_rejected_when_required():
    if pty_backend.IS_WINDOWS:
        return
    d0 = tempfile.mkdtemp(prefix="pytmux-tok-")
    tokenfile = os.path.join(d0, "host.token")
    host, htask, endpoint, d = await _serve(tokenfile=tokenfile)
    client = PtyHostClient(asyncio.get_event_loop())
    try:
        await client.connect(endpoint, token=None)        # 토큰 누락
        raised = False
        try:
            await client.list_panes()
        except (PtyHostError, asyncio.TimeoutError):
            raised = True
        assert raised, "토큰 누락인데 제어 채널이 살아있다"
        assert host._writer is None
    finally:
        await _stop(host, htask, d, client)
        shutil.rmtree(d0, ignore_errors=True)


async def test_host_without_token_allows_connect():
    # 토큰 미설정 host(테스트 하네스/수동 기동)는 종전대로 무토큰 연결 허용 — peer-UID
    # (같은 UID)만 통과 게이트. 하위호환 보존.
    if pty_backend.IS_WINDOWS:
        return
    host, htask, endpoint, d = await _serve(tokenfile=None)
    client = PtyHostClient(asyncio.get_event_loop())
    try:
        await client.connect(endpoint)
        panes = await client.list_panes()
        assert panes == []
        assert host._writer is not None
    finally:
        await _stop(host, htask, d, client)


async def _serve_tcp(tokenfile):
    """TCP 루프백(127.0.0.1:0)으로 host 를 띄운다 — Windows 프로덕션 경로. portfile 로
    실제 포트를 받아 엔드포인트를 돌려준다. 전 플랫폼 동작이라 이 경로는 Windows 도 돈다."""
    d = tempfile.mkdtemp(prefix="pytmux-hostauthtcp-")
    portfile = os.path.join(d, "host.port")
    host = ptyhost.PtyHost()
    htask = asyncio.ensure_future(
        host.serve("tcp:127.0.0.1:0", portfile=portfile, tokenfile=tokenfile))
    for _ in range(300):
        if os.path.exists(portfile):
            break
        await asyncio.sleep(0.01)
    port = open(portfile, encoding="ascii").read().strip()
    return host, htask, f"tcp:127.0.0.1:{port}", d


async def test_host_tcp_loopback_token_enforced():
    # M1 의 Windows 표면(루프백 TCP 의 무인가 로컬 접속 차단)을 TCP 로 실측한다 — Windows
    # 스킵 없음(§9.4). 토큰은 portfile 옆 tokenfile(0600)로 게시된다.
    d0 = tempfile.mkdtemp(prefix="pytmux-toktcp-")
    tokenfile = os.path.join(d0, "host.token")
    host, htask, ep, d = await _serve_tcp(tokenfile)
    good = PtyHostClient(asyncio.get_event_loop())
    bad = PtyHostClient(asyncio.get_event_loop())
    try:
        token = open(tokenfile, encoding="ascii").read().strip()
        assert len(token) == 64, token
        # 올바른 토큰 → 제어 채널 왕복 성립.
        await good.connect(ep, token=token)
        assert await good.list_panes() == []
        # 무인가(토큰 없음) → 거절. host 는 무인가 연결을 현재 writer 로 채택하지 않아
        # 인증된 good 연결이 보존된다(루프백의 다른 로컬 주체가 세션을 못 가로챈다).
        await bad.connect(ep, token=None)
        raised = False
        try:
            await bad.list_panes()
        except (PtyHostError, asyncio.TimeoutError):
            raised = True
        assert raised, "무인가 TCP 접속인데 제어 채널이 살아있다"
        assert host._writer is not None        # good 연결이 여전히 채택돼 있음
    finally:
        await _stop(host, htask, d, good, bad)
        shutil.rmtree(d0, ignore_errors=True)
