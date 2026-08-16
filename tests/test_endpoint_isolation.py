"""TCP 엔드포인트의 **상태파일 격리** — «서버 둘 + 클라 attach» 가 서는가.

# 무엇이 잘못돼 있었나 (pytmux/pytmux-152)

Windows 는 AF_UNIX 대신 TCP 루프백으로 간다. Unix 는 **소켓 경로가 곧 상태파일
prefix** 라 서버마다 저절로 갈리는데, TCP 는 포트가 재기동마다 바뀌어 경로에 못 쓴다 —
그래서 `ipc` 는 셋(상태 prefix·포트파일·토큰)을 전부 상태 디렉터리의 **고정 이름**
`default` 로 접었다. 발견이 안정적이어야 하니 그 자체는 옳았다.

값은 «한 머신에 서버가 둘일 때» 나왔다. 두 서버가 같은 파일 이름을 쓰므로, 나중 것이
`default.token` 자리를 가져가고 **먼저 뜬 서버에 붙는 클라**가 `read_token()` 으로 남의
토큰을 읽어 내민다 → 서버가 `auth_failed` 로 끊는다. 실측 2026-08-08(alienware):
`tests/test_search_all.py` 의 원격 테스트 넷이 `connection closed waiting initial
status: ['error']` 로 상시 실패했고, 그 `error` 의 정체가 `auth_failed` 였다.

⚠ 가르는 선은 «서버가 둘»이 아니라 «둘 뜬 뒤에 **토큰으로 붙는가**» 다. 서버 API 를
직접 부르는 원격 테스트는 클라가 없어 잘 돌았고, 그래서 「원격 테스트가 다 죽는다」로
보이지 않았다.

# 왜 이 파일이 Windows 전용이 아닌가

⛔ **이 구멍을 3주간 안 보이게 한 것이 «저쪽 상자에서 초록이면 끝» 이다**(같은 날
`test_blocks` 에서도 같은 부류가 하나 났다). `ipc` 의 TCP 경로는 플랫폼 독립이라
macOS/Linux 에서 그대로 돈다 — `test_ipc` 가 이미 같은 전제로 «Windows 박스 없이
Windows 경로를 돌려 본다». 그래서 여기서는 **플랫폼과 무관하게** TCP 전송을 세워
그 시나리오를 그대로 재현한다. 이 파일이 초록이면 그 벽은 어느 상자에도 없다.

고친 방법은 이름을 **엔드포인트 문자열이 나르게** 한 것이다 — `tcp:[NAME@]HOST:PORT`.
이름을 안 쓰면 `default` 라 발견 규약과 기존 경로는 바이트 그대로다.
"""

import asyncio
import os
import time

import harness  # noqa: F401  (경로 설정)
from harness import server_only, tcp_state_dir, teardown
from pytmuxlib import ipc
from pytmuxlib.protocol import PROTO_VERSION, read_msg, write_msg


async def _attach_client(endpoint):
    """실 클라처럼 hello 로 붙는다 — **토큰을 엔드포인트에서 찾아** 실어 보낸다.

    이 한 줄(`ipc.read_token(endpoint)`)이 결함의 진원지다: 엔드포인트가 이름을 안
    나르면 두 서버가 같은 토큰 파일을 가리킨다."""
    reader, writer = await ipc.open_connection(endpoint)
    hello = {"t": "hello", "proto": PROTO_VERSION, "cols": 80, "rows": 24}
    tok = ipc.read_token(endpoint)
    if tok:
        hello["token"] = tok
    await write_msg(writer, hello)
    return reader, writer


async def _read_until(reader, pred, timeout=8.0, what="msg"):
    end = time.monotonic() + timeout
    seen = []
    while time.monotonic() < end:
        msg = await asyncio.wait_for(read_msg(reader),
                                     max(0.1, end - time.monotonic()))
        if msg is None:
            raise AssertionError(f"connection closed waiting {what}: {seen}")
        seen.append(msg.get("t"))
        if pred(msg):
            return msg
    raise AssertionError(f"timeout waiting {what}: {seen}")


# ─────────────────────────────────────────────────────────────────────────────
# 경로 규약 — 이름이 실제로 셋 전부를 가르는가 / 기본값은 안 움직였는가
# ─────────────────────────────────────────────────────────────────────────────
def test_the_default_name_keeps_every_discovery_path_byte_identical():
    """이름을 안 쓰면 종전 그대로다 — 발견 규약(`default`)을 건드리지 않는다.

    이게 깨지면 이미 떠 있는 서버를 클라가 못 찾는다(포트파일/토큰 경로가 어긋난다).
    """
    with tcp_state_dir() as d:
        for ep in ("tcp:127.0.0.1:0", "tcp:127.0.0.1:54321"):
            assert ipc.state_base(ep) == os.path.join(d, "default"), ep
            assert ipc.portfile_for(ep) == os.path.join(d, "default.port"), ep
            assert ipc.token_path(ep) == os.path.join(d, "default.token"), ep
        assert ipc.tcp_endpoint() == "tcp:127.0.0.1:0"
        assert ipc.endpoint_name("tcp:127.0.0.1:0") == "default"


def test_two_named_endpoints_share_no_state_file():
    """이름이 다르면 **셋 다** 갈린다 — 하나라도 안 갈리면 그 자리가 다음 결함이다."""
    with tcp_state_dir() as d:
        a = ipc.tcp_endpoint("srvA")
        b = ipc.tcp_endpoint("srvB", port=54321)
        assert a == "tcp:srvA@127.0.0.1:0" and b == "tcp:srvB@127.0.0.1:54321"
        for f in (ipc.state_base, ipc.portfile_for, ipc.token_path):
            assert f(a) != f(b), f.__name__
            assert f(a).startswith(os.path.join(d, "srvA")), (f.__name__, f(a))
            assert f(b).startswith(os.path.join(d, "srvB")), (f.__name__, f(b))
        # 포트는 경로에 안 실린다 — 기동 전(0)과 확정 후가 같은 자리를 봐야 한다.
        assert ipc.token_path(ipc.tcp_endpoint("srvA")) == \
            ipc.token_path(ipc.tcp_endpoint("srvA", port=65000))


def test_the_name_does_not_change_what_the_transport_sees():
    """이름은 **상태파일에만** 쓰인다 — 전송(host/port)과 로컬 판정은 그대로다."""
    assert ipc.parse_endpoint("tcp:srvA@127.0.0.1:54321") == \
        ("tcp", "127.0.0.1", 54321)
    assert ipc.is_tcp("tcp:srvA@127.0.0.1:0")
    assert ipc.is_local_endpoint("tcp:srvA@127.0.0.1:54321")
    assert not ipc.is_local_endpoint("tcp:srvA@10.0.0.5:22")
    # 이름 없는 형태는 종전 그대로 파싱된다(IPv6·호스트 생략 포함).
    assert ipc.parse_endpoint("tcp:::1:1234") == ("tcp", "::1", 1234)
    assert ipc.parse_endpoint("tcp:54321") == ("tcp", "127.0.0.1", 54321)


def test_a_name_cannot_escape_the_state_directory():
    """`remote_attach` 의 endpoint 는 **클라가 준 비신뢰 문자열**이다.

    이름이 그대로 파일명이 되므로 느슨하면 상태 디렉터리 밖의 파일을 읽고 쓴다.
    거부는 ValueError 이고, **조용히 `default` 로 접지 않는다** — 접으면 오타 하나가
    남의 서버 토큰을 읽는 경로가 된다."""
    bad = ["tcp:../../etc@127.0.0.1:0", "tcp:a/b@127.0.0.1:0",
           "tcp:..@127.0.0.1:0", "tcp:@127.0.0.1:0",
           "tcp:.hidden@127.0.0.1:0", "tcp:a b@127.0.0.1:0",
           "tcp:" + "x" * 65 + "@127.0.0.1:0"]
    for ep in bad:
        for f in (ipc.parse_endpoint, ipc.state_base, ipc.token_path,
                  ipc.portfile_for, ipc.endpoint_name):
            try:
                f(ep)
                assert False, f"기대: ValueError — {f.__name__}({ep!r})"
            except ValueError:
                pass
        # 예외가 서버 루프로 새면 안 되는 자리는 삼킨다(토큰이 없는 것과 같은 결말).
        assert ipc.read_token(ep) is None, ep


# ─────────────────────────────────────────────────────────────────────────────
# 실 시나리오 — 서버 둘을 띄우고 **먼저 뜬 쪽**에 클라를 붙인다
# ─────────────────────────────────────────────────────────────────────────────
async def test_a_client_attaches_to_the_first_of_two_tcp_servers():
    """이 파일의 이유. 고치기 전에는 여기서 `auth_failed` 가 났다.

    순서가 계약이다 — A 를 먼저 띄우고, B 를 띄운 **뒤에** A 에 붙는다. B 가 상태
    파일 자리를 가져갔다면 A 의 토큰 조회가 B 것을 물어 온다."""
    with tcp_state_dir():
        srvA, taskA, sockA = await server_only(tcp=True)
        srvB, taskB, sockB = await server_only(tcp=True)
        readerA = writerA = None
        try:
            assert ipc.endpoint_name(sockA) != ipc.endpoint_name(sockB), \
                (sockA, sockB)
            srvA.ensure_default_session(80, 24)
            srvB.ensure_default_session(80, 24)
            # 두 서버의 토큰은 서로 다르고, 각자 자기 자리에서 읽힌다.
            assert srvA.auth_token and srvB.auth_token
            assert srvA.auth_token != srvB.auth_token
            assert ipc.read_token(sockA) == srvA.auth_token, "A 토큰이 B 것으로 덮였다"
            assert ipc.read_token(sockB) == srvB.auth_token
            # 그리고 실제로 붙는다 — 종전에는 이 줄이
            # `connection closed waiting initial status: ['error']` 였다.
            readerA, writerA = await _attach_client(sockA)
            await _read_until(readerA, lambda m: m.get("t") == "status",
                              what="initial status")
        finally:
            if writerA is not None:
                writerA.close()
            await teardown(srvA, taskA, sockA)
            await teardown(srvB, taskB, sockB)


async def test_the_resolved_endpoint_still_carries_the_name():
    """기동 뒤 확정 엔드포인트에 이름이 안 실리면 **결함이 그대로 재현된다** —
    클라는 그 문자열로 토큰·포트파일을 찾기 때문이다."""
    with tcp_state_dir() as d:
        srv, task, sock = await server_only(tcp=True)
        try:
            name = ipc.endpoint_name(sock)
            assert name != ipc.DEFAULT_ENDPOINT_NAME, sock
            assert ipc.parse_endpoint(sock)[2] > 0, "에페메럴 포트가 확정됐다"
            # 포트파일·토큰이 그 이름으로 실제 디스크에 있다.
            assert os.path.exists(os.path.join(d, name + ".port")), sock
            assert os.path.exists(os.path.join(d, name + ".token")), sock
            # 서버가 쥔 기동 전 문자열과 클라가 받은 확정 문자열이 같은 자리를 본다.
            assert ipc.token_path(srv.sock_path) == ipc.token_path(sock)
            assert ipc.state_base(srv.sock_path) == ipc.state_base(sock)
        finally:
            await teardown(srv, task, sock)


async def test_a_malformed_endpoint_from_a_client_fails_the_attach_not_the_server():
    """`remote_attach(endpoint=…)` 의 문자열은 **클라가 준다**(`serverio._handle_cmd`).

    모양이 틀리면 `ipc.parse_endpoint` 가 ValueError 를 던지는데, 종전에는 그것이
    `remote_attach` 의 except 목록(OSError·ConnectionError·TimeoutError·
    LimitOverrunError)에 없어 커맨드 루프로 샜다 — 사용자에게는 notice 도 없이
    「쳤는데 아무 일도 안 난다」이고 흔적은 error.log 의 트레이스백뿐이다. 이름 규칙이
    생기면서 그 입력 모양이 하나 늘었으므로 여기서 접는다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        for bad in ("tcp:127.0.0.1:abc",            # 종전부터 있던 모양
                    "tcp:../../etc@127.0.0.1:5555",  # 이름 규칙에 안 맞는 모양
                    "tcp:"):
            assert await srv.remote_attach(sess, endpoint=bad) is False, bad
        assert srv.running, "서버는 살아 있다"
    finally:
        # 실패를 **일부러** 내는 테스트라 그 라벨만 좁게 허용한다(전면 True 금지).
        await teardown(srv, task, sock, allow_errors=("remote_attach",))


async def test_run_sync_units():
    """동기 단위(경로 규약)를 async 러너에서 한 번 실행해 회계에 포함."""
    test_the_default_name_keeps_every_discovery_path_byte_identical()
    test_two_named_endpoints_share_no_state_file()
    test_the_name_does_not_change_what_the_transport_sees()
    test_a_name_cannot_escape_the_state_directory()
