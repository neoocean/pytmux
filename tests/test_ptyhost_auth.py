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
import contextlib
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


async def _drain_stopped(host, htask):
    """질서 종료(_stop set) 후 serve() 의 정리(finally)까지 완료를 기다린다."""
    host._stop.set()
    try:
        await asyncio.wait_for(htask, 5.0)
    except (asyncio.CancelledError, Exception):
        pass


async def test_host_orderly_stop_cleans_portfile_and_tokenfile():
    """완전 재시작 첫 기동 실패 회귀(2026-07-10): host 가 질서 종료 시 자기가 게시한
    portfile/tokenfile 을 지운다 — stale 포트파일이 남으면 다음 서버 기동의
    prespawn_host 가 host 가 있는 줄 알고 건너뛰고, ensure_connected 첫 재연결이
    죽은 루프백 포트 connect 타임아웃(Windows 는 즉답 거절 없음)을 태운다."""
    d0 = tempfile.mkdtemp(prefix="pytmux-hostclean-")
    tokenfile = os.path.join(d0, "host.token")
    host, htask, ep, d = await _serve_tcp(tokenfile)
    portfile = os.path.join(d, "host.port")
    try:
        assert os.path.exists(portfile) and os.path.exists(tokenfile)
        await _drain_stopped(host, htask)
        assert not os.path.exists(portfile), "질서 종료 후 portfile 잔존"
        assert not os.path.exists(tokenfile), "질서 종료 후 tokenfile 잔존"
    finally:
        await _stop(host, htask, d)
        shutil.rmtree(d0, ignore_errors=True)


async def test_host_orderly_stop_preserves_other_hosts_files():
    """위 정리는 **내 게시물일 때만**: 이미 새 host 가 같은 경로에 다시 게시했다면
    (내용 불일치) 낡은 host 의 종료가 새 host 의 파일을 지우지 않는다."""
    d0 = tempfile.mkdtemp(prefix="pytmux-hostclean2-")
    tokenfile = os.path.join(d0, "host.token")
    host, htask, ep, d = await _serve_tcp(tokenfile)
    portfile = os.path.join(d, "host.port")
    try:
        for p, body in ((portfile, "1"), (tokenfile, "other-host-token")):
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
        await _drain_stopped(host, htask)
        assert os.path.exists(portfile), "남의 portfile 을 지웠다"
        assert os.path.exists(tokenfile), "남의 tokenfile 을 지웠다"
    finally:
        await _stop(host, htask, d)
        shutil.rmtree(d0, ignore_errors=True)


async def test_host_preauth_frame_cap_rejects_oversized_advertisement():
    """[검수 PTYH-1 회귀, 2026-07-17] 인증 전 프레임은 HANDSHAKE_MAX_FRAME(64KiB)로 캡.

    회귀 전: `_authenticate` 가 `proto.read_frame(reader)` 를 그냥 불러 MAX_FRAME
    (**16MiB**) 이 적용됐고 타임아웃도 없었다. 무인가 피어가 16MiB 를 광고한 뒤 1바이트만
    흘리면 `readexactly` 가 그만큼 버퍼를 키운 채 **영원히** 매달렸다(EOF 도 타임아웃도
    없음). Windows 는 루프백 TCP 라 같은 박스의 아무 로컬 사용자나 이걸 N개 열 수 있고,
    host 가 고갈되면 다음 서버 재시작 때 재연결이 실패해 **살아있던 셸 전부가 소멸**한다
    — host 모드의 존재 이유 그 자체가 파괴된다. 메인 서버는 같은 방어를 2026-07-03(M2)
    부터 갖췄는데 pty-host 만 남아 있었다."""
    if pty_backend.IS_WINDOWS:
        from run import skip
        skip("asyncio AF_UNIX 미지원(Windows) — 루프백 TCP 경로는 "
             "test_host_tcp_loopback_token_enforced 가 검증")
    d = tempfile.mkdtemp(prefix="pytmux-hostcap-")
    tf = os.path.join(d, "tok")
    with open(tf, "w") as f:
        f.write("s" * 64)
    host, htask, endpoint, d2 = await _serve(tokenfile=tf)
    try:
        r, w = await asyncio.open_unix_connection(endpoint)
        await r.read(64)                                  # hello 소비
        # 16MiB 를 광고하고 1바이트만 — 그리고 침묵.
        w.write((16 * 1024 * 1024).to_bytes(4, "big") + b"x")
        await w.drain()
        # 캡에 걸려 **버퍼링 없이 즉시** 끊겨야 한다(EOF=b"").
        data = await asyncio.wait_for(r.read(1), timeout=5)
        assert data == b"", f"과대 광고 프레임이 안 끊김: {data!r}"
        with contextlib.suppress(Exception):
            w.close()
        # 캡이 걸린 뒤에도 host 는 정상 피어를 받는다(생존).
        c = PtyHostClient()
        await c.connect(endpoint, token="s" * 64)
        with contextlib.suppress(Exception):
            c.close()
    finally:
        await _stop(host, htask, d2)
        shutil.rmtree(d, ignore_errors=True)


async def test_host_preauth_handshake_timeout_reaps_silent_conn():
    """[검수 PTYH-1 회귀] 인증 전 연결이 아무것도 안 보내고 매달리면 타임아웃으로 끊긴다.

    회귀 전: 타임아웃이 없어 무인가 slowloris 연결이 **무기한** 상주했다(메인 서버는
    HANDSHAKE_TIMEOUT 으로 이미 방어)."""
    if pty_backend.IS_WINDOWS:
        from run import skip
        skip("asyncio AF_UNIX 미지원(Windows) — 루프백 TCP 경로는 "
             "test_host_tcp_loopback_token_enforced 가 검증")
    d = tempfile.mkdtemp(prefix="pytmux-hosttmo-")
    tf = os.path.join(d, "tok")
    with open(tf, "w") as f:
        f.write("s" * 64)
    host, htask, endpoint, d2 = await _serve(tokenfile=tf)
    orig = ptyhost.HANDSHAKE_TIMEOUT
    try:
        # 실 타임아웃(10s)을 기다리지 않게 모듈 상수를 낮춘다(핸드셰이크 경로만 사용).
        ptyhost.HANDSHAKE_TIMEOUT = 0.3
        r, w = await asyncio.open_unix_connection(endpoint)
        await r.read(64)                                  # hello 만 받고 침묵
        data = await asyncio.wait_for(r.read(1), timeout=5)
        assert data == b"", f"침묵 연결이 안 끊김: {data!r}"
        with contextlib.suppress(Exception):
            w.close()
    finally:
        ptyhost.HANDSHAKE_TIMEOUT = orig
        await _stop(host, htask, d2)
        shutil.rmtree(d, ignore_errors=True)
