"""런처 단위 테스트 — 중첩 실행 거부 등(서버/클라 기동 불필요)."""
import os
import tempfile

import harness  # noqa: F401  (경로 설정)
from pytmuxlib import sshwrap
from pytmuxlib.launcher import NEST_MARKER, main, nesting_blocked


async def test_nesting_blocked_helper():
    old = os.environ.get("PYTMUX")
    old_m = os.environ.get(NEST_MARKER)
    try:
        os.environ.pop("PYTMUX", None)
        os.environ.pop(NEST_MARKER, None)
        os.environ["PYTMUX"] = "/tmp/some.sock"   # 로컬 패널 안인 상황
        assert nesting_blocked() is True, "로컬 패널 안 → 중첩 거부"
        os.environ.pop("PYTMUX", None)
        assert nesting_blocked() is False, "패널 밖 → 정상"
        # 원격(ssh)에는 PYTMUX 가 없고 표식(LC_PYTMUX)만 전파된다 → 그래도 거부.
        os.environ[NEST_MARKER] = "1"
        assert nesting_blocked() is True, "원격 표식만 있어도 거부"
    finally:
        for k, v in ((("PYTMUX", old)), ((NEST_MARKER, old_m))):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def test_sshwrap_marker_and_path():
    """sshwrap.panel_env: 표식 env + ssh 래퍼 PATH 앞단 주입. 래퍼는 SendEnv 전파."""
    if os.name == "nt":
        return  # POSIX 전용
    state = tempfile.mkdtemp(prefix="pytmux-sshwrap-")
    env = {"PATH": "/usr/bin:/bin"}
    out = sshwrap.panel_env(env, state)
    assert out[sshwrap.NEST_MARKER] == "1", "표식 env 주입"
    assert out is env
    wd = os.path.join(state, "sshwrap")
    assert out["PATH"].startswith(wd + os.pathsep), "래퍼 디렉터리가 PATH 앞단"
    # ssh/autossh 래퍼가 실행 가능하게 생성되고 SendEnv 표식을 끼운다.
    for name in ("ssh", "autossh"):
        p = os.path.join(wd, name)
        assert os.access(p, os.X_OK), f"{name} 래퍼 실행권한"
        body = open(p).read()
        assert f"SendEnv={sshwrap.NEST_MARKER}" in body, "SendEnv 표식 전파"
    # 표식 이름은 launcher 와 일치해야 한다(로컬·원격 공통 판정).
    assert sshwrap.NEST_MARKER == NEST_MARKER


async def test_sshwrap_windows_cmd_wrapper():
    """Windows(#1.4): panel_env 가 ssh.cmd/autossh.cmd 배치 래퍼를 PATH 앞단에 깐다.

    래퍼는 `%~$PATH:E`(.exe 만 검색)로 진짜 ssh.exe 를 찾고 SendEnv 표식을 끼운다 —
    PATH 에서 자기 dir 을 빼는 수고 없이 자기 .cmd 를 안 잡는다."""
    if os.name != "nt":
        return  # Windows 전용
    state = tempfile.mkdtemp(prefix="pytmux-sshwrap-")
    env = {"PATH": r"C:\Windows\System32"}
    out = sshwrap.panel_env(env, state)
    assert out[sshwrap.NEST_MARKER] == "1", "표식 env 주입"
    wd = os.path.join(state, "sshwrap")
    assert out["PATH"].startswith(wd + os.pathsep), "래퍼 디렉터리가 PATH 앞단"
    for name in ("ssh", "autossh"):
        p = os.path.join(wd, name + ".cmd")
        assert os.path.exists(p), f"{name}.cmd 래퍼 생성"
        body = open(p, encoding="utf-8").read()
        assert f"SendEnv={sshwrap.NEST_MARKER}" in body, "SendEnv 표식 전파"
        # .exe 만 검색해 자기 .cmd 를 안 잡는다(무한 재귀 방지).
        assert f"in ({name}.exe)" in body, ".exe 만 PATH 검색"
    # 멱등: 다시 호출해도 내용 동일(재작성 무해).
    sshwrap.ensure_wrapper_dir(state)
    assert sshwrap.NEST_MARKER == NEST_MARKER


async def test_host_terminal_probe_inband_detection():
    """§1.7 in-band 중첩 감지: 단말에 XTVERSION 을 질의해 ① pytmux 응답이면 True,
    ② 타 단말의 완결 DCS 응답이면 조기 False, ③ 무응답이면 타임아웃 False.
    env 마커가 전파되지 않는 원격 경로의 중첩(→ 재접속 루프)을 전송 무관 차단."""
    if os.name == "nt":
        return  # POSIX 전용(프로브 자체가 nt 에서 False)
    import asyncio
    import pty
    from pytmuxlib.launcher import host_terminal_is_pytmux
    from pytmuxlib.serverpty import NEST_QUERY, NEST_REPLY

    async def probe(reply, timeout=1.0):
        m, s = pty.openpty()
        try:
            fut = asyncio.create_task(asyncio.to_thread(
                host_terminal_is_pytmux, timeout, s, s))
            q = await asyncio.wait_for(asyncio.to_thread(os.read, m, 64), 5)
            assert NEST_QUERY in q, ("프로브가 XTVERSION 질의를 쓴다", q)
            if reply is not None:
                os.write(m, reply)
            return await asyncio.wait_for(fut, 5)
        finally:
            os.close(m)
            os.close(s)

    assert await probe(NEST_REPLY) is True, "pytmux 응답 → 중첩"
    assert await probe(b"\x1bP>|iTerm2 3.5.0\x1b\\") is False, \
        "타 단말 완결 응답 → 조기 비중첩"
    assert await probe(None, timeout=0.15) is False, "무응답 → 타임아웃 비중첩"


async def test_stdio_proxy_token_and_frame_roundtrip():
    """§1.7 페더레이션 Stage 1·3: `pytmux stdio-proxy` 가 ① 서버 인증 토큰을
    `TOKEN <hex>` 첫 줄로 알리고 ② stdio↔서버소켓을 스플라이스해 와이어 프레임
    (list 요청→sessions 응답)이 무손상 왕복한다 — `ssh -T` exec 채널 전송 모델.
    스레드 스플라이스라 **POSIX·Windows 공통**(Windows 원격 = office 박스, 사용자
    보고 2026-06-12). Windows 는 TCP 루프백+포트파일(ipc.control_socket) 경로."""
    import asyncio
    import json
    import sys
    from harness import server_only, teardown
    from pytmuxlib import protocol
    srv, task, sock = await server_only()
    p = None
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = await asyncio.create_subprocess_exec(
            sys.executable, os.path.join(root, "pytmux.py"),
            "--socket", sock, "stdio-proxy",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        line = await asyncio.wait_for(p.stdout.readline(), 10)
        assert line.startswith(b"TOKEN "), line
        tok = line.split(b" ", 1)[1].strip().decode()
        frame = {"proto": protocol.PROTO_VERSION, "t": "list"}
        if tok:
            frame["token"] = tok
        payload = json.dumps(frame).encode()
        p.stdin.write(len(payload).to_bytes(4, "big") + payload)
        await p.stdin.drain()
        hdr = await asyncio.wait_for(p.stdout.readexactly(4), 10)
        body = await asyncio.wait_for(
            p.stdout.readexactly(int.from_bytes(hdr, "big")), 10)
        assert "sessions" in json.loads(body), body
        p.stdin.close()                      # 로컬 측 종료 → 프록시 정리
        await asyncio.wait_for(p.wait(), 10)
    finally:
        if p is not None and p.returncode is None:
            p.kill()
        await teardown(srv, task, sock)


async def test_main_refuses_nested_attach():
    # PYTMUX 가 설정된 상태에서 attach → SystemExit(1)(ensure_server 도달 전 차단).
    old = os.environ.get("PYTMUX")
    try:
        os.environ["PYTMUX"] = "/tmp/some.sock"
        code = None
        try:
            main(["attach"])
        except SystemExit as e:
            code = e.code
        assert code == 1, f"중첩 attach 는 거부(exit 1), got {code}"
    finally:
        if old is None:
            os.environ.pop("PYTMUX", None)
        else:
            os.environ["PYTMUX"] = old
