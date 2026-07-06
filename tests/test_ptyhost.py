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


async def test_read_loop_isolates_callback_exception():
    """H2: 한 패널 데이터 콜백이 던져도 PtyHostClient._read_loop 가 끊기지 않고 이후
    프레임을 계속 처리한다. 종전엔 콜백 예외가 루프를 break → _handle_lost 로 host 는
    멀쩡한데 전 패널이 끊긴 것으로 오인(+무로깅)하던 증폭. 진짜 단절(read_frame→None)
    에서만 _handle_lost 가 1회 호출된다."""
    from pytmuxlib import ptyhostclient
    cli = ptyhostclient.PtyHostClient()
    cli.reader = object()                    # read_frame 은 아래서 가로챔
    seen = []

    def _cb(data):
        seen.append(data)
        if len(seen) == 1:
            raise RuntimeError("콜백 버그(첫 프레임)")

    cli._cb[1] = (_cb, None)
    lost = []
    cli._on_lost = lambda: lost.append(True)
    frames = [("data", 1, b"A"), ("data", 1, b"B"), None]

    async def _fake_read_frame(_r):
        return frames.pop(0)

    orig = proto.read_frame
    proto.read_frame = _fake_read_frame
    try:
        await cli._read_loop()
    finally:
        proto.read_frame = orig
    assert seen == [b"A", b"B"], ("첫 콜백 예외 후에도 둘째 프레임 처리", seen)
    assert lost == [True], ("EOF(None)에서만 _handle_lost 1회", lost)


async def test_on_eof_offloads_blocking_reap():
    """M1: _on_eof 가 reap(block=True)를 이벤트 루프에서 직접 부르지 않고 executor 로
    오프로드한다 — 자식이 EOF 후 늦게 끝나도 루프가 안 막힌다. 느린 reap 가짜 pty 로
    _on_eof 가 즉시 반환(동기 블로킹 X)되고, 이후 비동기로 exit 처리(alive=False·
    exit 프레임·패널 정리)가 완료됨을 확인."""
    import threading
    import types
    host = ptyhost.PtyHost()
    reap_started = threading.Event()
    reap_release = threading.Event()
    sink = []

    class _SlowPty:
        def reap(self, block=False):
            reap_started.set()
            reap_release.wait(2)
            return 7

        def stop_reader(self):
            pass

        def close(self):
            pass

    class _W:
        def write(self, b):
            sink.append(b)

    host._writer = _W()
    pe = types.SimpleNamespace(pty=_SlowPty(), alive=True, exit_status=None)
    host.panes[1] = pe
    host._on_eof(1)                            # 태스크만 생성 — 동기 블로킹 X
    assert pe.alive is True, "reap 전 — _on_eof 가 동기로 막지 않음"
    for _ in range(100):                      # executor reap 시작까지 양보(루프 비차단)
        if reap_started.is_set():
            break
        await asyncio.sleep(0.01)
    assert reap_started.is_set(), "reap 이 executor 에서 시작(루프 비차단)"
    reap_release.set()                        # 자식 종료
    for _ in range(200):
        if 1 not in host.panes:
            break
        await asyncio.sleep(0.01)
    assert pe.alive is False and pe.exit_status == 7, "비동기 exit 처리 완료"
    assert 1 not in host.panes, "패널 정리됨"
    assert any(b"exit" in bytes(x) for x in sink), "exit 프레임 송신"


async def test_keepalive_declares_idle_host_lost():
    """M2: ptyhostclient keepalive 가 idle(프레임 무수신)이 _IDLE_TIMEOUT 을 넘으면
    연결을 끊긴 것으로 간주해 reader 에 EOF 를 넣어 read 루프를 깨운다(half-open
    좀비/웨지의 영구 hang 방지). 건강한 host 는 pong 으로 _last_recv 를 갱신해 안 끊김."""
    import time
    from pytmuxlib import ptyhostclient as P
    cli = P.PtyHostClient()
    cli.reader = asyncio.StreamReader()
    closed = []

    class _W:
        def close(self):
            closed.append(True)

    cli.writer = _W()
    cli._last_recv = time.monotonic() - 100.0      # 오래 전 = idle
    orig = (P._PING_INTERVAL, P._IDLE_TIMEOUT)
    P._PING_INTERVAL, P._IDLE_TIMEOUT = 0.02, 0.01
    try:
        await asyncio.wait_for(cli._keepalive_loop(), 2)
    finally:
        P._PING_INTERVAL, P._IDLE_TIMEOUT = orig
    assert cli.reader.at_eof(), "idle → reader EOF(read 루프 깨움)"
    assert closed == [True], "writer 닫힘"


async def test_ensure_connected_spawn_failure_short_circuits():
    """M7: host spawn 자체가 실패하면 _spawn_host 가 False 를 돌려, ensure_connected 가
    6초 폴링 없이 즉시 None(인프로세스 폴백)으로 빠진다(실패가 묵살되지 않음)."""
    import time as _t
    from pytmuxlib import proc as _proc
    from pytmuxlib import ptyhostmgr
    loop = asyncio.get_running_loop()
    d = tempfile.mkdtemp(prefix="pytmux-mgr-")
    sock = os.path.join(d, "x.sock")

    def _boom(_argv):
        raise OSError("spawn fail (test)")

    orig = _proc.spawn_detached
    _proc.spawn_detached = _boom
    try:
        assert ptyhostmgr._spawn_host(sock) is False, "spawn 실패→False"
        t0 = _t.monotonic()
        client = await ptyhostmgr.ensure_connected(loop, sock)
        dt = _t.monotonic() - t0
    finally:
        _proc.spawn_detached = orig
    assert client is None
    assert dt < 3.0, ("6초 폴링 회피(spawn 실패 즉시 폴백)", dt)


async def test_prespawn_dedup_and_gating():
    """레버 H: prespawn_host 가 (1) host 모드 OFF 면 no-op, (2) 이미 떠 있는 host(소켓/
    portfile 존재)면 skip, (3) 처음이면 딱 한 번 spawn 하고, 뒤이은 _spawn_host 는
    **중복 spawn 하지 않는다**(host 하나만 뜨게 보장)."""
    from pytmuxlib import proc as _proc
    from pytmuxlib import ptyhostmgr
    d = tempfile.mkdtemp(prefix="pytmux-prespawn-")
    sock = os.path.join(d, "x.sock")

    calls = []
    orig_spawn = _proc.spawn_detached
    _proc.spawn_detached = lambda argv, **kw: calls.append(argv) or 4242
    orig_env = os.environ.get("PYTMUX_PTY_HOST")
    saved_spawned = set(ptyhostmgr._spawned_hosts)
    ptyhostmgr._spawned_hosts.discard(sock)
    try:
        # (1) host 모드 OFF → no-op.
        os.environ["PYTMUX_PTY_HOST"] = "0"
        ptyhostmgr.prespawn_host(sock)
        assert calls == [], "host OFF 면 prespawn no-op"

        # (2) 이미 리스닝 중인 host(소켓 존재) → skip.
        os.environ["PYTMUX_PTY_HOST"] = "1"
        endpoint = ptyhostmgr.listen_endpoint(sock)   # POSIX = <base>.ptyhost.sock
        open(endpoint, "w").close()
        ptyhostmgr.prespawn_host(sock)
        assert calls == [], "기존 host 존재 시 prespawn skip(재시작 host 보존·무중복)"
        os.unlink(endpoint)

        # (3) 처음 → 딱 한 번 spawn.
        ptyhostmgr.prespawn_host(sock)
        assert len(calls) == 1, ("첫 prespawn 은 1회 spawn", calls)
        assert sock in ptyhostmgr._spawned_hosts

        # 뒤이은 _spawn_host(ensure_connected 경로)는 중복 spawn 안 함.
        assert ptyhostmgr._spawn_host(sock) is True
        assert len(calls) == 1, ("prespawn 뒤 _spawn_host 는 no-op(무중복)", calls)
    finally:
        _proc.spawn_detached = orig_spawn
        ptyhostmgr._spawned_hosts.clear()
        ptyhostmgr._spawned_hosts.update(saved_spawned)
        if orig_env is None:
            os.environ.pop("PYTMUX_PTY_HOST", None)
        else:
            os.environ["PYTMUX_PTY_HOST"] = orig_env
        import shutil
        shutil.rmtree(d, ignore_errors=True)


async def test_ensure_connected_no_upfront_poll_delay():
    """레버 G: host 가 곧바로 접속 가능해지면 ensure_connected 가 **고정 100ms 선(先)sleep
    없이** 즉시 연결한다(종전 `await asyncio.sleep(0.1)` 이 첫 시도 앞에 있어 최소 100ms
    허비하던 것 제거). _try_connect 를 재연결 1회 실패→spawn 직후 성공으로 흉내내 계측."""
    import time as _t
    from pytmuxlib import proc as _proc
    from pytmuxlib import ptyhostmgr
    loop = asyncio.get_running_loop()
    d = tempfile.mkdtemp(prefix="pytmux-mgr-g-")
    sock = os.path.join(d, "x.sock")

    n = {"c": 0}
    sentinel = object()

    async def _fake_try_connect(_loop, _sock, _timeout):
        n["c"] += 1
        # 1회차 = 재연결(기존 host 없음)→None, 2회차 = spawn 직후 첫 폴→성공.
        return None if n["c"] == 1 else sentinel

    orig_try = ptyhostmgr._try_connect
    orig_spawn = _proc.spawn_detached
    ptyhostmgr._try_connect = _fake_try_connect
    _proc.spawn_detached = lambda argv, **kw: 4242
    saved_spawned = set(ptyhostmgr._spawned_hosts)
    ptyhostmgr._spawned_hosts.discard(sock)
    try:
        t0 = _t.monotonic()
        client = await ptyhostmgr.ensure_connected(loop, sock)
        dt = _t.monotonic() - t0
    finally:
        ptyhostmgr._try_connect = orig_try
        _proc.spawn_detached = orig_spawn
        ptyhostmgr._spawned_hosts.clear()
        ptyhostmgr._spawned_hosts.update(saved_spawned)
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    assert client is sentinel, "spawn 직후 첫 폴에서 연결"
    assert n["c"] == 2, ("재연결 1 + 폴 1 = 2회 시도", n["c"])
    assert dt < 0.05, ("첫 폴 앞 고정 100ms sleep 제거(즉시 시도)", dt)


async def test_prespawn_guard_cleared_allows_respawn_after_host_loss():
    """레버 H 회귀 가드: prespawn 뒤 ensure_connected 가 연결에 성공하면 `_spawned_hosts`
    가드를 **해제**해, 뒤이어 host 가 죽어 _on_host_lost→ensure_connected 가 다시 불릴 때
    죽은 host 를 **새로 띄운다**(가드가 남으면 _spawn_host 가 no-op → 죽은 host 재기동
    불가 회귀). ①콜드스타트=1회 spawn(dedup) ②host-loss=재-spawn 을 함께 검증."""
    from pytmuxlib import proc as _proc
    from pytmuxlib import ptyhostmgr
    loop = asyncio.get_running_loop()
    d = tempfile.mkdtemp(prefix="pytmux-mgr-h2-")
    sock = os.path.join(d, "x.sock")

    spawns = []
    calls = {"c": 0}
    sentinel = object()

    async def _fake_try_connect(_loop, _sock, _timeout):
        # 매 ensure_connected 는 (재연결 None → 폴 성공) 2회를 소비한다.
        calls["c"] += 1
        return None if calls["c"] % 2 == 1 else sentinel

    orig_try = ptyhostmgr._try_connect
    orig_spawn = _proc.spawn_detached
    orig_env = os.environ.get("PYTMUX_PTY_HOST")
    saved = set(ptyhostmgr._spawned_hosts)
    ptyhostmgr._spawned_hosts.discard(sock)
    ptyhostmgr._try_connect = _fake_try_connect
    _proc.spawn_detached = lambda argv, **kw: spawns.append(argv) or 1
    os.environ["PYTMUX_PTY_HOST"] = "1"
    try:
        # 콜드 스타트: prespawn(1회 spawn) → ensure_connected(재연결 실패→dedup→폴 성공).
        ptyhostmgr.prespawn_host(sock)
        assert len(spawns) == 1, ("prespawn 1회", spawns)
        c1 = await ptyhostmgr.ensure_connected(loop, sock)
        assert c1 is sentinel
        assert len(spawns) == 1, ("prespawn 뒤 중복 spawn 없음(dedup)", spawns)
        assert sock not in ptyhostmgr._spawned_hosts, "연결 성공 시 가드 해제"

        # host 죽음 → 재연결: 이번엔 가드가 없어 새 host 를 띄워야 한다.
        c2 = await ptyhostmgr.ensure_connected(loop, sock)
        assert c2 is sentinel
        assert len(spawns) == 2, ("host-loss 후 재-spawn(죽은 host 재기동)", spawns)
    finally:
        ptyhostmgr._try_connect = orig_try
        _proc.spawn_detached = orig_spawn
        ptyhostmgr._spawned_hosts.clear()
        ptyhostmgr._spawned_hosts.update(saved)
        if orig_env is None:
            os.environ.pop("PYTMUX_PTY_HOST", None)
        else:
            os.environ["PYTMUX_PTY_HOST"] = orig_env
        import shutil
        shutil.rmtree(d, ignore_errors=True)
