"""PTYH-2/3 재평가(2026-07-25) — 호환을 지키며 닫은 두 가지.

검수 2026-07-17 은 이 둘을 "hello 순서를 바꿔야 하니 상호인증과 함께" 로 묶어 유보했다.
위협을 경로별로 쪼개니 **프로토콜 순서를 건드리지 않고** 닫히는 부분이 있었다:

- **PTYH-2 부분(POSIX)**: 토큰을 보내기 **전에** 소켓 소유자를 검증한다. 종전엔 경로가
  누구 것인지 보지 않고 connect 한 뒤 auth 프레임을 실어 보내, 다른 사용자가 경로를
  선점하면 토큰이 수확됐다. Windows(TCP 루프백)는 등가 검사가 없어 유보 유지.
- **PTYH-3**: pre-auth 배너에서 지문(`version`·`pid`)을 뺐다. 순서는 그대로다 — 구
  client 가 hello 를 먼저 읽고 auth 를 보내므로 순서를 뒤집으면 세션유지 재시작에서
  데드락한다(그게 유보 사유이고, 여기서는 건드리지 않는다).

되돌리면 실패해야 하는 오라클:
  · 소켓 검증을 지우거나 connect 뒤로 옮기면 → test_foreign_or_loose_socket_refused 실패
  · 배너에 version/pid 를 되살리면 → test_preauth_banner_has_no_fingerprint 실패
  · hello 를 auth 뒤로 옮기면 → test_hello_still_precedes_auth 실패(호환 계약)
"""
import os
import socket
import stat
import tempfile

import harness  # noqa: F401
from pytmuxlib import ipc


def _sock_at(path, mode=0o600):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    os.chmod(path, mode)
    return s


async def test_our_private_socket_accepted():
    """정상 경로(우리 uid·0600 소켓)는 그대로 통과한다."""
    if ipc.IS_WINDOWS:
        from run import skip
        skip("POSIX 전용 검사(Windows 는 TCP 루프백 — 상호인증 몫)")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "h.sock")
    s = _sock_at(p)
    try:
        ipc.validate_local_socket(p)          # 예외 없음 = 통과
    finally:
        s.close()


async def test_foreign_or_loose_socket_refused():
    """심볼릭 링크 · 소켓 아닌 파일 · 남에게 열린 권한은 **연결 전에** 거부된다."""
    if ipc.IS_WINDOWS:
        from run import skip
        skip("POSIX 전용 검사")
    d = tempfile.mkdtemp()
    # ① 소켓이 아닌 일반 파일(공격자가 경로를 파일로 선점)
    plain = os.path.join(d, "plain.sock")
    with open(plain, "w") as f:
        f.write("")
    try:
        ipc.validate_local_socket(plain)
    except RuntimeError as e:
        assert "소켓이 아닌" in str(e)
    else:
        raise AssertionError("일반 파일이 통과했다")
    # ② 심볼릭 링크(진짜 소켓을 가리켜도 거부 — lstat 은 링크를 따라가지 않는다)
    real = os.path.join(d, "real.sock")
    s = _sock_at(real)
    link = os.path.join(d, "link.sock")
    os.symlink(real, link)
    try:
        try:
            ipc.validate_local_socket(link)
        except RuntimeError as e:
            assert "심볼릭 링크" in str(e)
        else:
            raise AssertionError("심링크가 통과했다")
        # ③ group/other 비트가 열린 소켓(다른 사용자가 붙을 수 있다)
        loose = os.path.join(d, "loose.sock")
        s2 = _sock_at(loose, mode=0o666)
        try:
            try:
                ipc.validate_local_socket(loose)
            except RuntimeError as e:
                assert "다른 사용자" in str(e)
            else:
                raise AssertionError("0666 소켓이 통과했다")
        finally:
            s2.close()
    finally:
        s.close()
    # ④ 없는 경로는 이 함수의 관심사가 아니다(connect 가 낼 오류) — 통과.
    ipc.validate_local_socket(os.path.join(d, "nope.sock"))


async def test_missing_socket_is_not_our_error():
    """부재 경로에서 예외를 던지면 정상 기동(host 를 아직 안 띄운 경우)이 깨진다."""
    if ipc.IS_WINDOWS:
        from run import skip
        skip("POSIX 전용 검사")
    ipc.validate_local_socket(os.path.join(tempfile.mkdtemp(), "absent.sock"))


async def test_client_validates_before_sending_token():
    """`PtyHostClient.connect` 는 소켓 검증에 실패하면 **connect 도 하지 않는다** —
    검증이 auth 뒤에 있으면 이미 토큰이 나간 뒤라 의미가 없다."""
    if ipc.IS_WINDOWS:
        from run import skip
        skip("POSIX 전용 검사")
    import asyncio

    from pytmuxlib import ptyhostclient
    d = tempfile.mkdtemp()
    bad = os.path.join(d, "bad.sock")
    with open(bad, "w") as f:                 # 소켓이 아닌 파일로 선점
        f.write("")
    opened = []
    orig = asyncio.open_unix_connection

    async def spy(path):                      # 연결 시도 자체가 있으면 기록
        opened.append(path)
        return await orig(path)
    asyncio.open_unix_connection = spy
    try:
        cli = ptyhostclient.PtyHostClient()
        try:
            await cli.connect(bad, token="secret")
        except RuntimeError:
            pass
        else:
            raise AssertionError("검증 없이 연결됐다")
        assert opened == [], "검증 전에 connect 했다(토큰 노출 창)"
    finally:
        asyncio.open_unix_connection = orig


async def test_preauth_banner_has_no_fingerprint():
    """인증 **전** 배너는 `{"op": "hello"}` 뿐 — 프로토콜 버전·host PID 를 스캐너에
    주지 않는다. (소비자 0 임을 확인하고 뺐다: `host_pid` 는 대입만 되고 아무도 읽지
    않으며 hello 의 `version` 은 어디서도 참조되지 않는다.)"""
    import inspect

    from pytmuxlib import ptyhost
    src = inspect.getsource(ptyhost.PtyHost._handle_conn)
    head = src.split('"op": "hello"')[1][:200]
    assert "PROTO_VERSION" not in head, "배너에 프로토콜 버전이 되살아났다"
    assert "getpid" not in head, "배너에 host PID 가 되살아났다"


async def test_hello_still_precedes_auth():
    """**호환 계약**: hello 는 여전히 auth 보다 먼저다. 순서를 뒤집으면 신 host ×
    구 client 가 서로를 기다려 데드락하고, 그 조합이 세션유지 재시작 경로다 —
    그래서 상호인증은 버전 협상이 생길 때까지 유보한다(검수 문서 §10)."""
    import inspect

    from pytmuxlib import ptyhost, ptyhostclient
    host_src = inspect.getsource(ptyhost.PtyHost._handle_conn)
    assert host_src.index('"op": "hello"') < host_src.index("_authenticate"), \
        "host 가 인증을 배너보다 먼저 요구한다(구 client 데드락)"
    cli_src = inspect.getsource(ptyhostclient.PtyHostClient.connect)
    assert cli_src.index('"hello"') < cli_src.index('"auth"'), \
        "client 가 배너를 기다리지 않고 토큰을 보낸다(구 host 와 어긋남)"


async def test_state_dir_rule_unchanged():
    """소켓 검사는 기존 상태 디렉터리 검사(F3)를 **대체하지 않는다** — 둘 다 살아 있어야
    '디렉터리 선점'과 '소켓 선점' 두 경로가 모두 닫힌다."""
    assert hasattr(ipc, "_validate_state_dir") and hasattr(ipc, "validate_local_socket")
    if not ipc.IS_WINDOWS:
        assert stat.S_ISSOCK is not None
