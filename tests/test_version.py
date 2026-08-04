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
    """**실조회**(_probe_version)는 p4:/git:/unknown 셋 중 하나다.

    `code_version()` 이 아니라 프로브를 직접 부른다 — 러너가 위생을 위해
    `PYTMUX_CODE_VERSION` 을 심으므로(run.py), 래퍼를 부르면 그 값을 되읽는
    **공허 통과**가 된다."""
    v = version._probe_version(version.PROJECT_DIR, 1.5)
    assert isinstance(v, str) and v
    assert v.startswith(("p4:", "git:")) or v == "unknown"


async def test_code_version_env_override_and_process_cache():
    """override 는 그대로 돌려주고, 없으면 **프로세스 1회만** 조회한다.

    캐시가 없으면 서버·클라를 여러 번 띄우는 프로세스(테스트 러너)가 기동마다
    p4+git 서브프로세스를 새로 띄워 이벤트 루프를 정체시킨다(느린 p4 워크스테이션
    실측 4.5~5.2초/회 → 살아 있는 서버로의 루프백 connect 가 0.5s 캡에 걸려 거짓
    타임아웃). 프로브를 폭탄으로 갈아 끼워 **조회가 다시 일어나지 않음**을 단언한다."""
    import os
    from harness import patched
    old = os.environ.get("PYTMUX_CODE_VERSION")
    os.environ["PYTMUX_CODE_VERSION"] = "p4:999"
    try:
        assert version.code_version() == "p4:999"
        os.environ.pop("PYTMUX_CODE_VERSION")
        d = "/nonexistent-project-dir-for-cache-test"
        with patched(version, _probe_version=lambda *a: "p4:1"):
            assert version.code_version(d) == "p4:1"
        def _boom(*a):
            raise AssertionError("캐시 히트여야 하는데 프로브를 다시 불렀다")
        with patched(version, _probe_version=_boom):
            assert version.code_version(d) == "p4:1"
    finally:
        version._CACHE.pop("/nonexistent-project-dir-for-cache-test", None)
        if old is None:
            os.environ.pop("PYTMUX_CODE_VERSION", None)
        else:
            os.environ["PYTMUX_CODE_VERSION"] = old


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


async def test_restart_check_dry_run():
    """restart-check 드라이런: 부작용 없이 안전성 점검 결과를 회신한다(re-exec 지원·
    세션 존재·직렬화 round-trip·패널 master fd 보유·버전).

    Windows 는 작업 보존 재시작(re-exec+fd 상속)을 지원하지 않고(ConPTY 는 숫자
    master_fd 가 없어 panes_with_fd 가 0, reexec_supported 도 False) → POSIX 전용
    점검이라 건너뛴다."""
    from pytmuxlib import ipc
    if ipc.IS_WINDOWS:
        # 조용한 return 대신 명시 skip — 요약이 사유별로 리포트해야 커버리지 갭이
        # 보인다(CLAUDE.md 「명시 SKIP」). 이 상자에서 늘 PASS 로 세어지던 자리다.
        from run import skip
        skip("POSIX 전용(re-exec+fd 상속 경로 · Windows 는 pty-host 인수인계라 값이 다르다)")
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        n_before = len(list(srv._all_panes()))
        w = _CapWriter()
        client = ClientConn(w)
        client.session = sess
        client.cols, client.rows = 80, 24
        await srv._handle_cmd(client,
                              {"t": "cmd", "action": "request_restart_check"})
        rep = next((f for f in w.frames if f.get("t") == "restart_check"), None)
        assert rep is not None, w.frames
        assert rep["has_sessions"] is True
        assert rep["serialize_ok"] is True and rep["serialize_err"] == ""
        assert rep["panes"] == rep["panes_with_fd"] >= 1
        assert rep["running_version"] == srv._code_version
        # §10-21ⓔ3: 서버가 **자기 OS** 를 적어 보낸다 — 클라가 자기 OS 로 대신
        # 판단하면 원격 서버의 조건을 클라 OS 로 적게 된다.
        assert rep["server_os"] == "posix", rep["server_os"]
        # 드라이런이라 세션/패널을 안 건드린다(부작용 없음)
        assert len(list(srv._all_panes())) == n_before
    finally:
        await teardown(srv, task, sock)


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
