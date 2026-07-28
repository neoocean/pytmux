"""host 모드 수명/장애 처리 테스트(옵션 C P6).

(B) 진짜 종료(shutdown op): host 가 모든 패널을 죽이고 serve 가 끝난다(고아 방지). 재시작
    경로는 이 op 를 안 보내 host 가 보존됨(P5b 에서 검증).
(C) 연결 끊김 감지: client._on_lost 훅이 host 끊김 시 호출된다(서버 재연결의 토대).
POSIX 검증 / Windows 스킵."""
import asyncio
import os
import shutil
import tempfile

from harness import wait_for   # 폴링 규약(고정 sleep 금지)
from pytmuxlib import ptyhost, pty_backend
from pytmuxlib.ptyhostclient import PtyHostClient


async def _inproc_host():
    d = tempfile.mkdtemp(prefix="pytmux-lifecycle-")
    endpoint = os.path.join(d, "host.sock")
    host = ptyhost.PtyHost()
    htask = asyncio.ensure_future(host.serve(endpoint))
    await wait_for(lambda: os.path.exists(endpoint), timeout=3.0, step=0.01)
    return host, htask, endpoint, d


async def test_shutdown_op_terminates_host():
    """(B) shutdown op 를 받으면 host 가 패널을 닫고 serve 가 깔끔히 끝난다."""
    if pty_backend.IS_WINDOWS:
        return
    host, htask, endpoint, d = await _inproc_host()
    client = PtyHostClient()
    try:
        await client.connect(endpoint)
        client.spawn(1, ["/bin/cat"], 80, 24)
        await asyncio.sleep(0.2)
        assert 1 in host.panes, "패널 생성 실패"
        client.shutdown_host()
        # serve 태스크가 끝나야 한다(_stop 이벤트로 serve_forever 취소 후 반환).
        await asyncio.wait_for(htask, 3.0)
        assert not host.panes, "shutdown 후 패널 잔존(고아)"
    finally:
        await client.close()
        if not htask.done():
            htask.cancel()
        for pid in list(host.panes):
            host._close_pane(pid)
        shutil.rmtree(d, ignore_errors=True)


async def test_on_lost_fires_on_disconnect():
    """(C) host 연결이 끊기면 client._on_lost 훅이 호출된다(서버 재연결 트리거)."""
    if pty_backend.IS_WINDOWS:
        return
    host, htask, endpoint, d = await _inproc_host()
    client = PtyHostClient()
    lost = []
    try:
        await client.connect(endpoint)
        client._on_lost = lambda: lost.append(True)
        # **채택을 기다린다** — `connect()` 반환 시점엔 host 가 아직 probe 판별
        # (`_PROBE_DECL_TIMEOUT`) 안에 있어 `_writer` 가 비어 있을 수 있고, 그 창에서
        # 내리면 serve 의 정리가 "소유 연결 close" 를 **건너뛴다**(닫을 대상이 없다).
        # 그러면 EOF 가 이 경로로 안 오고 클라 keepalive 가 좀비를 끊을 때까지(3.2s)
        # 밀려 3.0s 창을 넘긴다 — GHA ubuntu 3.11 이 4/5 회 이 창에 걸렸다(파이썬
        # 3.13 은 22ms 라 안 보였다). 여기서 보려는 계약은 "**소유 연결**이 끊기면
        # _on_lost" 이므로 소유가 성립한 뒤에 내리는 것이 맞다.
        await wait_for(lambda: host._writer is not None, timeout=3.0, step=0.01)
        # host 를 내려 연결을 끊는다.
        if host._server is not None:
            host._server.close()
        host._stop.set()
        # host 가 닫히면 client._read_loop 가 EOF → _handle_lost → _on_lost.
        await wait_for(lambda: lost, timeout=3.0, step=0.02)
        assert lost, "연결 끊김에 _on_lost 미발화"
    finally:
        await client.close()
        if not htask.done():
            htask.cancel()
            try:
                await htask
            except (asyncio.CancelledError, Exception):
                pass
        for pid in list(host.panes):
            host._close_pane(pid)
        shutil.rmtree(d, ignore_errors=True)
