"""ipc 추상층 회귀 테스트(헤드리스).

엔드포인트 파싱 + 두 전송(AF_UNIX, TCP 루프백)의 listen/connect/probe 왕복을
검증한다. macOS 는 두 전송 모두 지원하므로 Windows 박스 없이도 TCP 경로(=Windows
경로)를 여기서 실제로 돌려 본다. 프레이밍은 protocol.read_msg/write_msg 사용.
"""
import asyncio
import os
import tempfile

import harness  # noqa: F401 (sys.path 설정)
from pytmuxlib import ipc
from pytmuxlib.protocol import read_msg, write_msg


def test_parse_endpoint():  # 동기 단위(러너는 async 만 모으므로 아래서 호출)
    assert ipc.parse_endpoint("/tmp/x.sock") == ("unix", "/tmp/x.sock")
    assert ipc.parse_endpoint("tcp:127.0.0.1:54321") == ("tcp", "127.0.0.1", 54321)
    assert ipc.parse_endpoint("tcp:127.0.0.1:0") == ("tcp", "127.0.0.1", 0)
    assert ipc.is_tcp("tcp:127.0.0.1:0") and not ipc.is_tcp("/tmp/x.sock")
    # S3: 잘못된 포트는 미처리 ValueError 대신 명확한 ValueError 로 가드된다.
    for bad in ("tcp:", "tcp:127.0.0.1:abc", "tcp:host:"):
        try:
            ipc.parse_endpoint(bad)
            assert False, f"기대: ValueError for {bad!r}"
        except ValueError:
            pass


async def _echo_roundtrip(endpoint, portfile):
    """서버는 받은 메시지를 그대로 되돌려준다. 클라이언트가 왕복을 확인."""
    async def on_client(reader, writer):
        msg = await read_msg(reader)
        await write_msg(writer, {"echo": msg})  # write_msg 가 내부에서 drain
        writer.close()

    server, resolved = await ipc.start_server(endpoint, on_client, portfile=portfile)
    try:
        # 접속 전 probe = True
        assert ipc.probe(resolved, portfile=portfile) is True
        reader, writer = await ipc.open_connection(resolved, portfile=portfile)
        await write_msg(writer, {"hello": 42})
        reply = await read_msg(reader)
        writer.close()
        assert reply == {"echo": {"hello": 42}}, reply
    finally:
        server.close()
        await server.wait_closed()
    return resolved


async def test_unix_roundtrip():
    if ipc.IS_WINDOWS:
        return  # Windows 는 TCP 경로로 검증
    sock = tempfile.mktemp(suffix=".sock")
    try:
        resolved = await _echo_roundtrip(sock, None)
        assert resolved == sock
        # 서버 종료 후 probe = False
        assert ipc.probe(sock) is False
    finally:
        if os.path.exists(sock):
            os.unlink(sock)


async def test_tcp_roundtrip_ephemeral_portfile():
    """TCP 에페메럴(port 0) → 포트파일 게시 → 클라이언트가 파일로 포트 해석."""
    pf = tempfile.mktemp(suffix=".port")
    try:
        resolved = await _echo_roundtrip("tcp:127.0.0.1:0", pf)
        # 게시된 엔드포인트는 실제 포트로 확정됐다.
        kind = ipc.parse_endpoint(resolved)
        assert kind[0] == "tcp" and kind[2] > 0, resolved
        # 포트파일에 같은 포트가 적혔다.
        with open(pf) as f:
            assert int(f.read().strip()) == kind[2]
    finally:
        if os.path.exists(pf):
            os.unlink(pf)


async def test_unix_start_over_stale_socket_atomic():
    """§5.9: 기존(stale) 소켓 파일이 있어도 start_server 가 원자적으로 갈아끼우고
    정상 listen 한다 — 종전 exists→unlink→bind 의 TOCTOU 창 없이. 임시 파일 잔재도
    남기지 않는다."""
    if ipc.IS_WINDOWS:
        return  # Windows 는 TCP 경로(소켓 파일 stale 무관)
    sock = tempfile.mktemp(suffix=".sock")
    # 죽은 서버의 잔존 소켓 파일을 흉내 — bind 안 된 평범한 파일이라 probe=False(stale).
    with open(sock, "w") as f:
        f.write("")
    assert ipc.probe(sock) is False, "stale 파일은 응답 없음"
    try:
        async def on_client(reader, writer):
            await write_msg(writer, {"echo": await read_msg(reader)})
            writer.close()
        server, resolved = await ipc.start_server(sock, on_client)
        try:
            assert resolved == sock
            assert ipc.probe(sock) is True, "stale 위에 새로 listen 됨"
            reader, writer = await ipc.open_connection(sock)
            await write_msg(writer, {"hello": 7})
            assert (await read_msg(reader)) == {"echo": {"hello": 7}}
            writer.close()
            # 임시 바인드 파일(.tmp)이 남지 않는다(replace 로 소비됨).
            assert not os.path.exists(f"{sock}.{os.getpid()}.sock.tmp")
        finally:
            server.close()
            await server.wait_closed()
    finally:
        if os.path.exists(sock):
            os.unlink(sock)


async def test_probe_false_when_down():
    if ipc.IS_WINDOWS:
        # 떠 있지 않은 TCP 포트 probe = False
        assert ipc.probe("tcp:127.0.0.1:1") is False
        return
    missing = tempfile.mktemp(suffix=".sock")
    assert ipc.probe(missing) is False


def test_is_local_endpoint():  # 동기 단위(아래 test_run_sync_units 에서 호출)
    # AF_UNIX 소켓은 항상 로컬
    assert ipc.is_local_endpoint("/tmp/x.sock")
    # 루프백 TCP = 로컬(127.0.0.0/8·::1·localhost·포트만 형태)
    assert ipc.is_local_endpoint("tcp:127.0.0.1:54321")
    assert ipc.is_local_endpoint("tcp:127.0.0.1:0")
    assert ipc.is_local_endpoint("tcp:127.5.5.5:1234")
    assert ipc.is_local_endpoint("tcp:::1:1234")
    assert ipc.is_local_endpoint("tcp:localhost:1234")
    assert ipc.is_local_endpoint("tcp:54321")          # host 생략 → 127.0.0.1
    # 진짜 원격 호스트 = 원격(degraded 유지)
    assert not ipc.is_local_endpoint("tcp:100.79.188.26:54321")
    assert not ipc.is_local_endpoint("tcp:10.0.0.5:22")
    assert not ipc.is_local_endpoint("tcp:example.com:1234")
    # 잘못된 엔드포인트는 보수적으로 원격(억제 안 함)
    assert not ipc.is_local_endpoint("tcp:127.0.0.1:abc")


def test_control_connect_timeout_loopback_capped():  # 동기 단위(아래서 호출)
    """완전 재시작 후 첫 기동 실패 회귀(2026-07-10): Windows 는 리스너 없는 루프백
    포트로의 connect 가 즉답 거절(RST) 없이 **클라이언트 타임아웃까지** 매달린다
    (방화벽 stealth SYN 드롭 — GHA windows-latest 실측 정확히 settimeout 값). stale
    포트파일(kill-server 잔재)을 가리키는 probe/제어 폴이 폴마다 기본 2s 를 태워
    wait_server_authed 4s 예산을 소진했다. 루프백 TCP 는 캡, 나머지는 그대로."""
    cap = ipc._LOOPBACK_CONNECT_TIMEOUT
    assert ipc._control_connect_timeout("tcp:127.0.0.1:54321", 2.0) == cap
    assert ipc._control_connect_timeout("tcp:127.0.0.1:0", 2.0) == cap
    assert ipc._control_connect_timeout("tcp:localhost:1234", 2.0) == cap
    # 호출자가 더 짧게 준 timeout 은 존중한다(캡은 상한일 뿐).
    assert ipc._control_connect_timeout("tcp:127.0.0.1:1", 0.1) == 0.1
    # 원격 TCP·unix 소켓은 캡하지 않는다(진짜 네트워크 RTT/기존 의미 보존).
    assert ipc._control_connect_timeout("tcp:10.0.0.5:22", 2.0) == 2.0
    assert ipc._control_connect_timeout("/tmp/x.sock", 2.0) == 2.0


async def test_probe_dead_loopback_port_returns_fast():
    """죽은 루프백 포트 probe 가 캡(+여유) 안에 False 로 끝난다 — POSIX 는 원래
    즉시 거절이고, Windows 러너에서는 위 캡이 실제로 2s→0.5s 를 보장한다."""
    import socket as _socket
    import time as _time
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                       # bind 만 하고 닫아 '방금 죽은 포트'를 만든다
    t0 = _time.monotonic()
    assert ipc.probe(f"tcp:127.0.0.1:{port}") is False
    assert _time.monotonic() - t0 < 1.5, "죽은 루프백 포트 probe 가 느리다"


async def test_run_sync_units():
    """동기 단위 테스트(parse)를 async 러너에서 한 번 실행해 커버리지에 포함."""
    test_parse_endpoint()
    test_is_local_endpoint()
    test_control_connect_timeout_loopback_capped()


async def test_private_atomic_writes_and_cleans_up():
    """M5: ipc.private_atomic 가 0600 + 원자적 교체로 쓴다 — 정상 시 temp 가 사라지고
    원본만 남으며, 예외 시 원본을 건드리지 않고 temp 를 지운다(절반 쓰기 방지)."""
    d = tempfile.mkdtemp(prefix="pytmux-atomic-")
    path = os.path.join(d, "state.json")
    with ipc.private_atomic(path) as f:
        f.write("good")
    assert open(path).read() == "good", "원자적 교체 후 내용"
    assert not os.path.exists(path + ".tmp"), "temp 제거됨"
    if os.name != "nt":
        assert (os.stat(path).st_mode & 0o777) == 0o600, "0600 권한"
    # 예외 시: 기존 원본 보존 + temp 정리(절반 쓰기가 원본 안 덮음).
    try:
        with ipc.private_atomic(path) as f:
            f.write("half")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert open(path).read() == "good", "예외 시 원본 불변(절반 쓰기 배제)"
    assert not os.path.exists(path + ".tmp"), "예외 시 temp 정리"
