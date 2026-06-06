"""version 명령(클라/서버 버전·업타임) 테스트.

version.code_version()/fmt_uptime() 순수 함수 + 서버 request_version 회신.
"""
import json

import harness  # noqa: F401  (경로 설정)
from harness import server_only, teardown
from pytmuxlib import version
from pytmuxlib.model import ClientConn


async def test_fmt_uptime():
    assert version.fmt_uptime(0) == "00:00:00"
    assert version.fmt_uptime(59) == "00:00:59"
    assert version.fmt_uptime(3661) == "01:01:01"
    assert version.fmt_uptime(90061) == "1d 01:01:01"
    assert version.fmt_uptime(-5) == "00:00:00"   # 음수 클램프


async def test_code_version_format():
    v = version.code_version()
    assert isinstance(v, str) and v
    assert v.startswith(("p4:", "git:")) or v == "unknown"


class _CapWriter:
    """write_msg 가 보낸 프레임을 캡처하는 가짜 writer(길이프리픽스+JSON 디코드)."""
    def __init__(self):
        self.frames = []
        self._buf = b""

    def write(self, data):
        self._buf += data
        while len(self._buf) >= 4:
            n = int.from_bytes(self._buf[:4], "big")
            if len(self._buf) < 4 + n:
                break
            payload, self._buf = self._buf[4:4 + n], self._buf[4 + n:]
            self.frames.append(json.loads(payload))

    async def drain(self):
        pass

    def close(self):
        pass


async def test_server_version_reply():
    """request_version 에 서버가 자기 코드 버전·업타임·pid 를 회신한다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        w = _CapWriter()
        client = ClientConn(w)
        client.session = sess
        client.cols, client.rows = 80, 24
        await srv._handle_cmd(client, {"t": "cmd", "action": "request_version"})
        reply = next((f for f in w.frames if f.get("t") == "version"), None)
        assert reply is not None, w.frames
        assert reply["version"] == srv._code_version
        assert isinstance(reply["uptime"], (int, float)) and reply["uptime"] >= 0
        assert isinstance(reply["pid"], int)
    finally:
        await teardown(srv, task, sock)
