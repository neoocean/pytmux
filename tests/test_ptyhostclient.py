"""PtyHostClient + _RemotePtyProcess 프록시 테스트(옵션 C P3) — POSIX 전 기능 검증.

서버측 클라이언트가 host 에 연결해 패널을 PtyProcess 표면으로 구동하는 전 경로를 본다:
spawn→스트리밍 콜백, write, resize, reap(exit 푸시), 그리고 ★서버 재시작 모사(클라 끊고
재연결 후 list_panes 로 재바인딩하여 같은 셸을 계속 구동). Windows 는 스킵(ConPTY 는 스파이크)."""
import asyncio
import os
import shutil
import tempfile

from pytmuxlib import ptyhost, pty_backend
from pytmuxlib.ptyhostclient import PtyHostClient


async def _start_host():
    d = tempfile.mkdtemp(prefix="pytmux-phc-")
    endpoint = os.path.join(d, "host.sock")
    host = ptyhost.PtyHost()
    task = asyncio.ensure_future(host.serve(endpoint))
    for _ in range(100):
        if os.path.exists(endpoint):
            break
        await asyncio.sleep(0.01)
    return host, task, endpoint, d


async def _stop(host, task, endpoint, d, *clients):
    for c in clients:
        await c.close()
    if host._server is not None:
        host._server.close()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    for pid in list(host.panes):
        host._close_pane(pid)
    shutil.rmtree(d, ignore_errors=True)


class _Collector:
    def __init__(self):
        self.buf = bytearray()
        self.eof = False

    def on_data(self, d):
        self.buf += d

    def on_eof(self):
        self.eof = True

    async def wait_for(self, needle: bytes, timeout=4.0):
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if needle in self.buf:
                return True
            await asyncio.sleep(0.02)
        return needle in self.buf


async def test_client_spawn_and_stream():
    if pty_backend.IS_WINDOWS:
        return
    host, task, endpoint, d = await _start_host()
    client = PtyHostClient()
    try:
        await client.connect(endpoint)
        col = _Collector()
        proc = client.make_pane(1, 80, 24)
        proc.start_reader(None, col.on_data, col.on_eof)
        client.spawn(1, ["/bin/sh", "-c", "echo HELLO_PROXY_1; sleep 0.3"], 80, 24)
        assert await col.wait_for(b"HELLO_PROXY_1"), bytes(col.buf)
        # 'spawned' 회신으로 실제 자식 pid 가 채워진다.
        for _ in range(50):
            if proc.pid > 0:
                break
            await asyncio.sleep(0.02)
        assert proc.pid > 0, "spawned pid 미수신"
    finally:
        await _stop(host, task, endpoint, d, client)


async def test_client_write_and_resize():
    if pty_backend.IS_WINDOWS:
        return
    host, task, endpoint, d = await _start_host()
    client = PtyHostClient()
    try:
        await client.connect(endpoint)
        col = _Collector()
        proc = client.make_pane(2, 80, 24)
        proc.start_reader(None, col.on_data, col.on_eof)
        client.spawn(2, ["/bin/cat"], 80, 24)
        await asyncio.sleep(0.2)
        proc.write(b"ECHO_VIA_PROXY\n")
        assert await col.wait_for(b"ECHO_VIA_PROXY"), bytes(col.buf)
        proc.set_winsize(40, 100)          # 크래시 없이 전달되면 충분
        assert proc.cols == 100 and proc.rows == 40
    finally:
        await _stop(host, task, endpoint, d, client)


async def test_client_reap_on_exit():
    if pty_backend.IS_WINDOWS:
        return
    host, task, endpoint, d = await _start_host()
    client = PtyHostClient()
    try:
        await client.connect(endpoint)
        col = _Collector()
        proc = client.make_pane(4, 80, 24)
        proc.start_reader(None, col.on_data, col.on_eof)
        client.spawn(4, ["/bin/sh", "-c", "echo DONE; exit 7"], 80, 24)
        for _ in range(100):
            if col.eof:
                break
            await asyncio.sleep(0.02)
        assert col.eof, "on_eof 미호출"
        assert proc.reap() is not None, "exit status 미수신"
    finally:
        await _stop(host, task, endpoint, d, client)


async def test_client_reconnect_rebinds_pane():
    """★서버 재시작 모사: 클라를 끊고 새 클라로 재연결 → list_panes 로 살아있는 패널을
    찾아 재바인딩 → 같은 셸을 계속 구동(write→echo). 옵션 C 재시작 재연결의 핵심."""
    if pty_backend.IS_WINDOWS:
        return
    host, task, endpoint, d = await _start_host()
    client1 = PtyHostClient()
    client2 = PtyHostClient()
    try:
        await client1.connect(endpoint)
        proc1 = client1.make_pane(6, 80, 24)
        c1 = _Collector()
        proc1.start_reader(None, c1.on_data, c1.on_eof)
        client1.spawn(6, ["/bin/cat"], 80, 24)   # 장수명 셸
        await asyncio.sleep(0.2)
        proc1.write(b"BEFORE_RESTART\n")
        assert await c1.wait_for(b"BEFORE_RESTART"), bytes(c1.buf)

        await client1.close()                     # 서버 '재시작' — host·셸 생존
        await asyncio.sleep(0.2)

        await client2.connect(endpoint)           # 새 서버 재연결
        panes = await client2.list_panes()
        ids = {p["pane"] for p in panes}
        assert 6 in ids, ("재연결 list 에 패널 없음", panes)
        proc2 = client2.make_pane(6, 80, 24)
        c2 = _Collector()
        proc2.start_reader(None, c2.on_data, c2.on_eof)
        proc2.write(b"AFTER_RESTART\n")            # 같은 셸이 계속 산다
        assert await c2.wait_for(b"AFTER_RESTART"), bytes(c2.buf)
    finally:
        await _stop(host, task, endpoint, d, client1, client2)
