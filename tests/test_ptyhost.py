"""PTY host 데몬 테스트(옵션 C P1/P2) — 백엔드 중립이라 POSIX 에서 전 기능 검증.

host↔서버 프로토콜·spawn·스트리밍·입력 에코·list·**재연결 갭 출력 flush**·exit 프레임을
실제 `pytmuxlib.ptyhost.PtyHost` + 셸로 검증한다. Windows 의 ConPTY primitive 는 이미 라이브
검증(Phase 0 스파이크)이므로 여기선 OS 독립 기계만 본다 → Windows 에선 스킵."""
import asyncio
import os
import tempfile

from pytmuxlib import ptyhost, ptyhostproto as proto
from pytmuxlib import pty_backend


async def _start_host():
    d = tempfile.mkdtemp(prefix="pytmux-ptyhost-")
    endpoint = os.path.join(d, "host.sock")
    host = ptyhost.PtyHost()
    task = asyncio.ensure_future(host.serve(endpoint))
    for _ in range(100):                 # 소켓이 뜰 때까지 대기
        if os.path.exists(endpoint):
            break
        await asyncio.sleep(0.01)
    return host, task, endpoint, d


async def _stop_host(host, task, endpoint, d):
    if host._server is not None:
        host._server.close()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    for pid in list(host.panes):
        host._close_pane(pid)
    import shutil
    shutil.rmtree(d, ignore_errors=True)


async def _connect(endpoint):
    reader, writer = await asyncio.open_unix_connection(endpoint)
    # hello 수신 확인
    f = await asyncio.wait_for(proto.read_frame(reader), 2.0)
    assert f and f[0] == "json" and f[1]["op"] == "hello", f
    return reader, writer


async def _read_until(reader, pane_id, needle: bytes, timeout=4.0):
    """pane_id 의 'D' 데이터를 needle 이 보일 때까지 모은다(다른 op 는 수집해 반환)."""
    buf = bytearray()
    others = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        f = await asyncio.wait_for(proto.read_frame(reader),
                                   max(0.05, deadline - loop.time()))
        if f is None:
            break
        if f[0] == "data" and f[1] == pane_id:
            buf += f[2]
            if needle in buf:
                return bytes(buf), others
        elif f[0] == "json":
            others.append(f[1])
    return bytes(buf), others


async def test_ptyhost_spawn_and_stream():
    if pty_backend.IS_WINDOWS:
        return                            # ConPTY 는 Phase 0 스파이크로 검증
    host, task, endpoint, d = await _start_host()
    try:
        reader, writer = await _connect(endpoint)
        await proto.write_frame(writer, proto.encode_json({
            "op": "spawn", "pane": 1, "cols": 80, "rows": 24,
            "argv": ["/bin/sh", "-c", "echo STREAM_MARKER_42; sleep 0.2"]}))
        buf, others = await _read_until(reader, 1, b"STREAM_MARKER_42")
        assert b"STREAM_MARKER_42" in buf, buf
        assert any(o.get("op") == "spawned" and o.get("pane") == 1
                   for o in others), others
        writer.close()
    finally:
        await _stop_host(host, task, endpoint, d)


async def test_ptyhost_write_echo():
    if pty_backend.IS_WINDOWS:
        return
    host, task, endpoint, d = await _start_host()
    try:
        reader, writer = await _connect(endpoint)
        await proto.write_frame(writer, proto.encode_json({
            "op": "spawn", "pane": 7, "cols": 80, "rows": 24,
            "argv": ["/bin/cat"]}))       # cat = 입력을 그대로 에코
        await asyncio.sleep(0.2)
        await proto.write_frame(writer, proto.encode_data(7, b"PING_ECHO\n"))
        buf, _ = await _read_until(reader, 7, b"PING_ECHO")
        assert b"PING_ECHO" in buf, buf
        writer.close()
    finally:
        await _stop_host(host, task, endpoint, d)


async def test_ptyhost_list_reports_pane():
    if pty_backend.IS_WINDOWS:
        return
    host, task, endpoint, d = await _start_host()
    try:
        reader, writer = await _connect(endpoint)
        await proto.write_frame(writer, proto.encode_json({
            "op": "spawn", "pane": 3, "cols": 100, "rows": 30,
            "argv": ["/bin/cat"]}))
        await asyncio.sleep(0.2)
        await proto.write_frame(writer, proto.encode_json({"op": "list"}))
        # list_reply 가 올 때까지 프레임 수집
        reply = None
        for _ in range(50):
            f = await asyncio.wait_for(proto.read_frame(reader), 2.0)
            if f and f[0] == "json" and f[1].get("op") == "list_reply":
                reply = f[1]
                break
        assert reply is not None, "list_reply 미수신"
        panes = {p["pane"]: p for p in reply["panes"]}
        assert 3 in panes and panes[3]["alive"] and panes[3]["cols"] == 100, reply
        writer.close()
    finally:
        await _stop_host(host, task, endpoint, d)


async def test_ptyhost_reconnect_replays_pending():
    """★핵심: 서버(클라) 끊김 동안 자식이 낸 출력을 host 가 버퍼링했다가 재연결 시
    flush 한다 = 재시작 갭 출력 무손실. 옵션 C 세션유지 재시작의 토대."""
    if pty_backend.IS_WINDOWS:
        return
    host, task, endpoint, d = await _start_host()
    try:
        reader, writer = await _connect(endpoint)
        # 끊긴 뒤(0.4s 후) 출력이 나오고, 패널은 계속 살아 있도록(실제 세션 셸처럼)
        # 뒤에 sleep 을 붙인다 — 갭 중 자식이 종료하는 경우는 P6(장애처리) 범위.
        await proto.write_frame(writer, proto.encode_json({
            "op": "spawn", "pane": 5, "cols": 80, "rows": 24,
            "argv": ["/bin/sh", "-c", "sleep 0.4; echo GAP_OUTPUT_99; sleep 5"]}))
        await asyncio.sleep(0.1)
        writer.close()                    # 서버 '재시작' 모사 — host 는 생존
        await asyncio.sleep(0.5)          # 이 사이 자식이 GAP_OUTPUT_99 출력 → pending
        # 새 서버가 재연결
        reader2, writer2 = await _connect(endpoint)
        buf, _ = await _read_until(reader2, 5, b"GAP_OUTPUT_99")
        assert b"GAP_OUTPUT_99" in buf, ("재연결 후 갭 출력 flush 실패", buf)
        writer2.close()
    finally:
        await _stop_host(host, task, endpoint, d)


async def test_ptyhost_exit_frame_on_child_exit():
    if pty_backend.IS_WINDOWS:
        return
    host, task, endpoint, d = await _start_host()
    try:
        reader, writer = await _connect(endpoint)
        await proto.write_frame(writer, proto.encode_json({
            "op": "spawn", "pane": 9, "cols": 80, "rows": 24,
            "argv": ["/bin/sh", "-c", "echo BYE; exit 0"]}))
        got_exit = False
        for _ in range(80):
            f = await asyncio.wait_for(proto.read_frame(reader), 2.0)
            if f is None:
                break
            if f[0] == "json" and f[1].get("op") == "exit" and f[1].get("pane") == 9:
                got_exit = True
                break
        assert got_exit, "자식 종료 후 exit 프레임 미수신"
        assert 9 not in host.panes, "종료 패널이 host 에서 정리 안 됨"
        writer.close()
    finally:
        await _stop_host(host, task, endpoint, d)
