"""런처 단위 테스트 — 중첩 실행 거부 등(서버/클라 기동 불필요)."""
import os
import tempfile

import harness  # noqa: F401  (경로 설정)
from pytmuxlib import launcher, sshwrap
from pytmuxlib.launcher import NEST_MARKER, main, nesting_blocked


async def test_nesting_blocked_helper():
    # liveness 게이트: 마커 *존재* 만으로 거부하지 않는다. $PYTMUX 는 소켓이 실제로
    # 살아 있을 때만, $LC_PYTMUX 는 SSH 세션일 때만 권위를 가진다(잔재 마커 오탐 방지).
    old = os.environ.get("PYTMUX")
    old_m = os.environ.get(NEST_MARKER)
    old_sc = os.environ.get("SSH_CONNECTION")
    old_st = os.environ.get("SSH_TTY")
    real_probe = launcher.ipc.probe
    try:
        for k in ("PYTMUX", NEST_MARKER, "SSH_CONNECTION", "SSH_TTY"):
            os.environ.pop(k, None)

        # $PYTMUX 가 살아있는 소켓을 가리키면 → 로컬 중첩 거부.
        launcher.ipc.probe = lambda *a, **k: True
        os.environ["PYTMUX"] = "/tmp/some.sock"
        assert nesting_blocked() is True, "살아있는 로컬 패널 → 중첩 거부"

        # 같은 $PYTMUX 라도 소켓이 죽었으면 → 잔재이므로 거부 안 함(오탐 수정).
        launcher.ipc.probe = lambda *a, **k: False
        assert nesting_blocked() is False, "죽은 소켓 잔재 → 정상 실행"

        os.environ.pop("PYTMUX", None)
        assert nesting_blocked() is False, "패널 밖 → 정상"

        # $LC_PYTMUX 단독: 비-ssh 로컬 셸이면 잔재 → 무시, ssh 세션이면 원격 중첩 거부.
        os.environ[NEST_MARKER] = "1"
        assert nesting_blocked() is False, "비-ssh 로컬 잔재 표식 → 정상 실행"
        os.environ["SSH_CONNECTION"] = "1.2.3.4 5 6.7.8.9 22"
        assert nesting_blocked() is True, "ssh 세션의 원격 표식 → 중첩 거부"
    finally:
        launcher.ipc.probe = real_probe
        for k, v in (("PYTMUX", old), (NEST_MARKER, old_m),
                     ("SSH_CONNECTION", old_sc), ("SSH_TTY", old_st)):
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
        # NESTED_ATTACH §4 NEST_DEST: argv b64 를 DCS 로 발신(목적지 기록) —
        # /dev/tty 한정(파이프/리다이렉트 오염 금지), 부재 시 조용히 생략.
        assert "pytmux-ssh;" in body, "NEST_DEST DCS 발신"
        assert "/dev/tty" in body, "DEST 는 /dev/tty 로만"
        assert "base64" in body and "tr -d" in body, "argv b64(개행 제거) 인코딩"
    # 표식 이름은 launcher 와 일치해야 한다(로컬·원격 공통 판정).
    assert sshwrap.NEST_MARKER == NEST_MARKER


async def test_parse_dest_extracts_ssh_destination():
    """NESTED_ATTACH §4: parse_dest 가 ssh argv 에서 목적지(첫 비옵션 인자)를
    사용자가 친 그대로 추출 — 값 옵션 분리/결합형 스킵, `--`, autossh -M, 도메인
    계정 백슬래시 보존, 원격 명령 미포함. 실패는 ""(자동 승격만 비활성)."""
    pd = sshwrap.parse_dest
    assert pd(["ssh", "office1"]) == "office1"
    assert pd(["ssh", "-p", "2222", "-o", "X=y", "user@host", "echo", "hi"]) \
        == "user@host", "분리형 값 옵션 스킵 + 원격 명령 미포함"
    assert pd(["ssh", "-p2222", "-oStrictHostKeyChecking=no", "host"]) == "host", \
        "결합형 값 옵션 스킵"
    assert pd(["ssh", "-J", "jump1,jump2", "-l", "user", "host"]) == "host"
    assert pd(["ssh", "-4A", "host"]) == "host", "묶음 무값 플래그"
    assert pd(["ssh", "-At", "-i", "/k", "h"]) == "h"
    assert pd(["autossh", "-M", "20000", "host"]) == "host", "autossh -M 은 값 옵션"
    assert pd(["ssh", "-M", "host"]) == "host", "ssh -M 은 무값(master 모드)"
    assert pd(["ssh", "--", "-odd"]) == "-odd", "-- 뒤는 무조건 목적지"
    assert pd(["ssh", "NATGAMES\\woojinkim@office1"]) \
        == "NATGAMES\\woojinkim@office1", "도메인 백슬래시 보존"
    assert pd(["ssh"]) == "" and pd([]) == "" and pd(["ssh", "-p", "22"]) == ""


async def test_request_nest_promotion_inband():
    """NESTED_ATTACH §4: 승격 요청이 NEST_ATTACH_REQ(user@host b64)를 단말에 쓰고
    ① NEST_ACK 수신 → True ② 무관 출력만 → 타임아웃 False ③ 무응답 → False.
    무응답 폴백 = 현행 거부 메시지(열화 없음)."""
    if os.name == "nt":
        return  # POSIX 전용(함수 자체가 nt 에서 False)
    import asyncio
    import base64
    import pty
    from pytmuxlib.launcher import request_nest_promotion

    async def attempt(reply, timeout=1.0):
        m, s = pty.openpty()
        try:
            fut = asyncio.create_task(asyncio.to_thread(
                request_nest_promotion, timeout, s, s))
            req = await asyncio.wait_for(asyncio.to_thread(os.read, m, 512), 5)
            assert sshwrap.NEST_REQ_PRE in req, ("승격 요청 DCS", req)
            payload = req.split(sshwrap.NEST_REQ_PRE, 1)[1].split(b"\x1b")[0]
            assert b"@" in base64.b64decode(payload), "self-report=user@hostname"
            if reply is not None:
                os.write(m, reply)
            return await asyncio.wait_for(fut, 5)
        finally:
            os.close(m)
            os.close(s)

    assert await attempt(sshwrap.NEST_ACK) is True, "ack → 위임 성공"
    assert await attempt(b"plain noise\r\n", timeout=0.2) is False, \
        "무관 출력 → 타임아웃 폴백"
    assert await attempt(None, timeout=0.15) is False, "무응답 → 폴백"


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


async def test_stdio_proxy_autostarts_when_no_server():
    """원격 서버 부재 시 stdio-proxy 가 분리 서버를 **자동 기동**한다(reliability):
    원격 재부팅 후·최초 접속에 attach 가 즉시 실패하던 가장 흔한 케이스를 없앤다.
    spawn_detached 호출만 확인하고 wait_server_authed 를 False 로 두어 splice 전에
    return(실서버/실소켓 불필요한 단위 테스트)."""
    import pytmuxlib.launcher as L
    calls = []
    real_probe, real_spawn = L.ipc.probe, L.proc.spawn_detached
    real_argv, real_wait = L.proc.server_argv, L.wait_server_authed
    old_env = os.environ.pop("PYTMUX_NO_REMOTE_AUTOSTART", None)
    try:
        L.ipc.probe = lambda *a, **k: False           # 원격에 서버 없음
        L.proc.server_argv = lambda sp: ["argv", sp]
        L.proc.spawn_detached = lambda argv, **kw: calls.append(argv)
        L.wait_server_authed = lambda *a, **k: False  # 인증 대기 실패 → splice 전 return 1
        assert L.run_stdio_proxy("/tmp/nope.sock") == 1
        assert calls == [["argv", "/tmp/nope.sock"]], calls   # 자동 기동을 시도함
    finally:
        L.ipc.probe, L.proc.spawn_detached = real_probe, real_spawn
        L.proc.server_argv, L.wait_server_authed = real_argv, real_wait
        if old_env is not None:
            os.environ["PYTMUX_NO_REMOTE_AUTOSTART"] = old_env


async def test_stdio_proxy_autostart_optout_env():
    """PYTMUX_NO_REMOTE_AUTOSTART=1 이면 종전대로 자동 기동 없이 1 로 실패(탈출구)."""
    import pytmuxlib.launcher as L
    calls = []
    real_probe, real_spawn = L.ipc.probe, L.proc.spawn_detached
    try:
        L.ipc.probe = lambda *a, **k: False
        L.proc.spawn_detached = lambda argv, **kw: calls.append(argv)
        os.environ["PYTMUX_NO_REMOTE_AUTOSTART"] = "1"
        assert L.run_stdio_proxy("/tmp/nope.sock") == 1
        assert calls == [], "opt-out 시 자동 기동 안 함"
    finally:
        L.ipc.probe, L.proc.spawn_detached = real_probe, real_spawn
        os.environ.pop("PYTMUX_NO_REMOTE_AUTOSTART", None)


async def test_server_auth_ok_detects_tokenless_zombie():
    """server_auth_ok: 정상 서버(토큰 게시됨)는 True, 토큰 파일이 사라진 좀비는
    False. probe 는 둘 다 True 라 구분 못 한다 — 옛 서버가 default 소켓을 붙든 채
    /tmp 토큰만 정리돼 attach 가 auth_failed 로 화면만 깜빡이던 회귀(2026-06-16).
    control_request 는 동기 블로킹이라 in-loop 서버와 데드락하지 않게 to_thread."""
    import asyncio
    from harness import server_only, teardown
    from pytmuxlib import ipc
    from pytmuxlib.launcher import server_auth_ok
    srv, task, sock = await server_only()
    try:
        assert ipc.probe(sock) is True, "정상 서버는 connectable"
        assert await asyncio.to_thread(server_auth_ok, sock) is True, \
            "토큰 정상 → auth 통과"
        # 좀비 재현: 서버는 그대로 listen 중이나 토큰 파일만 사라짐 → 클라가 읽을
        # 토큰이 없어 서버가 auth_failed 로 거절(서버 auth_token 은 살아 있음).
        os.unlink(ipc.token_path(sock))
        assert ipc.probe(sock) is True, "토큰 없어도 여전히 connectable(좀비)"
        assert await asyncio.to_thread(server_auth_ok, sock) is False, \
            "토큰 분실 → auth_failed → 좀비로 판정"
    finally:
        await teardown(srv, task, sock)


async def test_main_refuses_nested_attach():
    # 살아있는 $PYTMUX(=로컬 패널 안)에서 attach → SystemExit(1)(ensure_server 도달
    # 전 차단). probe 를 살아있다고 고정해 마커를 권위화한다(liveness 게이트).
    old = os.environ.get("PYTMUX")
    real_probe = launcher.ipc.probe
    try:
        launcher.ipc.probe = lambda *a, **k: True
        os.environ["PYTMUX"] = "/tmp/some.sock"
        code = None
        try:
            main(["attach"])
        except SystemExit as e:
            code = e.code
        assert code == 1, f"중첩 attach 는 거부(exit 1), got {code}"
    finally:
        launcher.ipc.probe = real_probe
        if old is None:
            os.environ.pop("PYTMUX", None)
        else:
            os.environ["PYTMUX"] = old


# ── 네이티브 클라 진입점(§7 P7 슬라이스 4) ────────────────────────────────────

# ⚠ 2026-08-01: `--native`(러스트 TUI) 테스트 셋을 지웠다 — 그 플래그와 이진이 함께
# 사라졌다(사용자 결정: 클라는 정본 Textual 과 warp GUI 둘). GUI 는 `pytmux-gui` 를
# 직접 실행하므로 런처가 대신 찾아 주는 경로가 없다.


async def test_server_boot_error_names_the_missing_dependency():
    """데몬이 import 실패로 즉사하면 **어느 인터프리터에 무엇이 없는지** 를 뽑아낸다.

    실측(2026-07-28 원격 탭 실패): 원격의 homebrew `python3` 가 3.13→3.14 로 옮겨가
    requirements 없는 인터프리터를 가리켰고, 데몬은 ModuleNotFoundError 로 즉사했다.
    당시 데몬 stderr 는 /dev/null 이라 사용자가 본 것은 '인증 대기 시한 초과' 뿐 —
    원인이 인터프리터에 있다는 단서가 어디에도 없었다.
    """
    import io
    import contextlib
    from pytmuxlib import launcher as L
    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "s.sock")
        with open(L.server_boot_log(sock), "w") as f:
            f.write("Traceback (most recent call last):\n"
                    '  File "pytmuxlib/vtconst.py", line 15, in <module>\n'
                    "    from wcwidth import wcwidth as _wcwidth_base\n"
                    "ModuleNotFoundError: No module named 'wcwidth'\n")
        detail = L.server_boot_error(sock)
        assert "wcwidth" in detail, detail
        assert "pip install" in detail, f"설치 방법을 안내하지 않는다: {detail}"
        # 보고 경로까지 — 값을 만드는 헬퍼만 맞고 호출부가 빠지면 사용자는 못 본다.
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            L.report_server_start_failure(sock, "pytmux: 서버 기동 실패")
        assert "wcwidth" in err.getvalue(), err.getvalue()


async def test_server_boot_error_is_empty_without_a_log():
    """로그가 없으면 빈 문자열 — 호출부가 종전 문구만 찍도록(없는 원인 지어내지 않음)."""
    import io
    import contextlib
    from pytmuxlib import launcher as L
    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "s.sock")
        assert L.server_boot_error(sock) == ""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            L.report_server_start_failure(sock, "pytmux: 서버 기동 실패")
        assert err.getvalue().strip() == "pytmux: 서버 기동 실패", err.getvalue()


async def test_spawn_server_routes_daemon_stderr_to_the_boot_log():
    """자동 기동은 **반드시** boot.log 로 stderr 를 돌린다(진단의 단일 지점).

    '호출 제거' 뮤테이션 방어: spawn_server 가 stderr_path 를 안 넘기면 데몬의 죽는
    이유가 다시 /dev/null 로 사라진다.
    """
    import harness
    from pytmuxlib import launcher as L
    seen = {}
    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "s.sock")
        with harness.patched(L.proc,
                             spawn_detached=lambda argv, **kw: seen.update(kw)):
            L.spawn_server(sock)
        assert seen.get("stderr_path") == L.server_boot_log(sock), seen


async def test_stdio_proxy_autostart_failure_reports_the_real_reason():
    """원격 자동 기동이 실패하면 stderr 에 **사유**가 실린다(ssh 로 로컬 notice 까지).

    종전엔 '인증 대기 시한 초과' 만 나가서 원격 탭 실패를 진단할 수 없었다.
    """
    import io
    import contextlib
    import harness
    from pytmuxlib import launcher as L
    old_env = os.environ.pop("PYTMUX_NO_REMOTE_AUTOSTART", None)
    try:
        with tempfile.TemporaryDirectory() as d:
            sock = os.path.join(d, "s.sock")
            with open(L.server_boot_log(sock), "w") as f:
                f.write("ModuleNotFoundError: No module named 'pyte'\n")
            err = io.StringIO()
            with harness.patched(L, wait_server_authed=lambda *a, **k: False,
                                 spawn_server=lambda sp: None):
                with harness.patched(L.ipc, probe=lambda *a, **k: False):
                    with contextlib.redirect_stderr(err):
                        assert L.run_stdio_proxy(sock) == 1
            out = err.getvalue()
            assert "pyte" in out, out
    finally:
        if old_env is not None:
            os.environ["PYTMUX_NO_REMOTE_AUTOSTART"] = old_env


async def test_start_server_starts_without_attaching():
    """`pytmux start-server`: 서버만 띄우고 attach 하지 않는다(0). 이미 떠 있으면 멱등."""
    import io
    import contextlib
    import harness
    from pytmuxlib import launcher as L
    spawned = []
    # ① 서버 없음 → 기동 시도 후 성공 보고.
    with harness.patched(L, spawn_server=lambda sp: spawned.append(sp),
                         server_auth_ok=lambda *a, **k: False,
                         wait_server_authed=lambda *a, **k: True):
        with harness.patched(L.ipc, probe=lambda *a, **k: False):
            with contextlib.redirect_stdout(io.StringIO()):
                assert L.run_start_server("/tmp/x.sock") == 0
    assert spawned == ["/tmp/x.sock"], spawned
    # ② 이미 인증 정상인 서버가 있으면 아무것도 띄우지 않는다(멱등).
    spawned.clear()
    with harness.patched(L, spawn_server=lambda sp: spawned.append(sp),
                         server_auth_ok=lambda *a, **k: True):
        with contextlib.redirect_stdout(io.StringIO()):
            assert L.run_start_server("/tmp/x.sock") == 0
    assert spawned == [], "이미 실행 중인데 또 띄웠다"


async def test_start_server_reports_failure_reason():
    """기동에 실패하면 rc=1 + boot.log 의 사유를 stderr 로 알린다(조용한 실패 금지)."""
    import io
    import contextlib
    import harness
    from pytmuxlib import launcher as L
    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "s.sock")
        with open(L.server_boot_log(sock), "w") as f:
            f.write("ModuleNotFoundError: No module named 'textual'\n")
        err = io.StringIO()
        with harness.patched(L, spawn_server=lambda sp: None,
                             server_auth_ok=lambda *a, **k: False,
                             wait_server_authed=lambda *a, **k: False):
            with harness.patched(L.ipc, probe=lambda *a, **k: False):
                with contextlib.redirect_stderr(err):
                    assert L.run_start_server(sock) == 1
        assert "textual" in err.getvalue(), err.getvalue()


async def test_start_server_is_wired_into_the_cli():
    """CLI 하위명령으로 실제 연결돼 있는지(파서만 늘고 dispatch 가 빠지는 실수 방지)."""
    import harness
    from pytmuxlib import launcher as L
    calls = []
    with harness.patched(L, run_start_server=lambda sp: calls.append(sp) or 0):
        try:
            L.main(["--socket", "/tmp/t.sock", "start-server"])
        except SystemExit as e:
            assert e.code == 0, f"exit {e.code}"
    assert calls == ["/tmp/t.sock"], calls


async def test_spawn_server_creates_the_socket_directory():
    """명시 `--socket` 이 없는 디렉터리를 가리켜도 기동한다(0o700 으로 생성).

    종전엔 서버가 bind 에서 죽고 error.log 도 같은 없는 디렉터리라 못 써서, 사유
    한 줄 없이 rc=0 으로 사라졌다 — 런처는 '서버 기동 실패' 만 찍었다.
    """
    import harness
    from pytmuxlib import launcher as L
    with tempfile.TemporaryDirectory() as d:
        sub = os.path.join(d, "made", "up")
        sock = os.path.join(sub, "s.sock")
        with harness.patched(L.proc, spawn_detached=lambda argv, **kw: 0):
            L.spawn_server(sock)
        assert os.path.isdir(sub), "소켓 상위 디렉터리를 만들지 않았다"
        if os.name != "nt":
            assert oct(os.stat(sub).st_mode & 0o777) == "0o700", "소유자 전용이 아님"


async def test_server_boot_error_falls_back_to_the_socket_directory():
    """로그가 비면(=데몬이 stderr 도 못 남김) 디렉터리 상태를 마지막 단서로 준다."""
    from pytmuxlib import launcher as L
    detail = L.server_boot_error("/nonexistent-dir-for-pytmux-test/s.sock")
    assert "디렉터리" in detail, detail


async def test_relay_proxy_splices_through_a_fake_ssh():
    """§10-15 P1: `pytmux relay-proxy <host>` 가 **실제 서브프로세스로** 안쪽
    `pytmux stdio-proxy` 를 띄워 TOKEN 줄 + 와이어 프레임을 그대로 통과시킨다.

    실 3대(A→B→C)는 CI 가 없으므로 **PATH 앞단에 가짜 `ssh`** 를 깔아 "2홉 모양"만
    그대로 태운다 — 가짜 ssh 는 인자를 무시하고 로컬 `pytmux stdio-proxy` 를 exec 한다.
    이 테스트가 지키는 계약: A 입장에서 파이프 반대편은 여전히 "TOKEN 줄을 먼저 뱉는
    무언가"이고 **와이어 프로토콜은 한 바이트도 안 바뀐다**."""
    import asyncio
    import json
    import stat
    import sys
    from harness import server_only, teardown
    from run import skip
    from pytmuxlib import protocol
    if os.name == "nt":
        skip("POSIX 전용(가짜 ssh 셸 스크립트를 PATH 앞단에 깐다)")
    srv, task, sock = await server_only()
    p = None
    tmpdir = tempfile.mkdtemp(prefix="pytmux-relay-")
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fake = os.path.join(tmpdir, "ssh")
        with open(fake, "w", encoding="utf-8") as f:
            # 인자를 통째로 무시하고 로컬 stdio-proxy 를 exec — B→C ssh 자리를 대신한다.
            f.write("#!/bin/sh\nexec %s %s --socket %s stdio-proxy\n"
                    % (sys.executable, os.path.join(root, "pytmux.py"), sock))
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC | stat.S_IXGRP
                 | stat.S_IXOTH)
        # 중계는 **B 가 허락한 목적지만** 나간다(fail-closed, 2026-08-01 §10-16ⓔ).
        # 그래서 이 상자의 정책 파일에 목적지 `C` 를 먼저 적어 둔다 — 전에는 빈 목록이
        # 곧 전부 허용이라 이 줄이 필요 없었다.
        from pytmuxlib import ipc
        with open(ipc.state_base(sock) + ".opts.json", "w", encoding="utf-8") as f:
            json.dump({"remote_allowed_hosts": ["C"]}, f)
        env = dict(os.environ, PATH=tmpdir + os.pathsep + os.environ.get("PATH", ""))
        p = await asyncio.create_subprocess_exec(
            sys.executable, os.path.join(root, "pytmux.py"),
            "--socket", sock, "relay-proxy", "C",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env)
        line = await asyncio.wait_for(p.stdout.readline(), 20)
        assert line.startswith(b"TOKEN "), line
        tok = line.split(b" ", 1)[1].strip().decode()
        frame = {"proto": protocol.PROTO_VERSION, "t": "list"}
        if tok:
            frame["token"] = tok
        payload = json.dumps(frame).encode()
        p.stdin.write(len(payload).to_bytes(4, "big") + payload)
        await p.stdin.drain()
        hdr = await asyncio.wait_for(p.stdout.readexactly(4), 20)
        body = await asyncio.wait_for(
            p.stdout.readexactly(int.from_bytes(hdr, "big")), 20)
        assert "sessions" in json.loads(body), body
    finally:
        if p is not None and p.returncode is None:
            p.kill()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        await teardown(srv, task, sock)


async def test_relay_proxy_rejects_bad_host_and_denied_hop():
    """중계자는 **자기 입력을 자기가 검증**한다(A 를 믿지 않는다): 선행 `-`·공백
    목적지는 ssh 를 띄우기 전에 거부하고, 이 상자의 `remote_allowed_hosts` 에 없는
    목적지도 거부한다 — B 에 로그인할 수 있다는 것과 B 를 통해 아무 데나 갈 수 있다는
    것은 다른 권한이다. 에러 줄에는 홉 귀속용 `relay-proxy(<host>): ` 접두가 붙는다."""
    import json
    from pytmuxlib import ipc, launcher as L
    from run import skip
    if os.name == "nt":
        skip("POSIX 전용(허용목록 파일 경로가 상태 디렉터리 규칙을 탄다)")
    tmpdir = tempfile.mkdtemp(prefix="pytmux-relayopt-")
    sock = os.path.join(tmpdir, "s.sock")
    try:
        assert L._relay_host_ok("C") is True
        for bad in ("", "-oProxyCommand=x", "a b", " "):
            assert L._relay_host_ok(bad) is False, bad
        # ★ 허용목록이 없으면 **거부**한다(fail-closed, 2026-08-01 §10-16ⓔ).
        #   종전에는 통과였다 — "정책 미설정"을 "전부 허용"으로 읽으면 B 는 자기가
        #   도약대로 쓰이는 줄도 모른 채 아무 데나 나간다. 이 함수가 지키는 것이 B 의
        #   egress 라, 모르면 안 나가는 쪽이 맞는 기본값이다. 평범한 원격 attach 는
        #   이 경로를 안 지나므로(다중홉 릴레이 전용) 기존 동선은 그대로다.
        assert L._relay_allowed(sock, "C") is False
        # 파일이 있어도 목록이 없거나 비면 같은 판정이다(모르면 안 나간다).
        with open(ipc.state_base(sock) + ".opts.json", "w", encoding="utf-8") as f:
            json.dump({"remote_allowed_hosts": []}, f)
        assert L._relay_allowed(sock, "C") is False
        with open(ipc.state_base(sock) + ".opts.json", "w", encoding="utf-8") as f:
            json.dump({}, f)
        assert L._relay_allowed(sock, "C") is False
        with open(ipc.state_base(sock) + ".opts.json", "w", encoding="utf-8") as f:
            json.dump({"remote_allowed_hosts": ["onlythis"]}, f)
        assert L._relay_allowed(sock, "onlythis") is True
        assert L._relay_allowed(sock, "C") is False
        # 문자열 하나로 적어도 받아들인다(서버 로더와 같은 관용)
        with open(ipc.state_base(sock) + ".opts.json", "w", encoding="utf-8") as f:
            json.dump({"remote_allowed_hosts": "onlythis"}, f)
        assert L._relay_allowed(sock, "onlythis") is True
        assert L._relay_allowed(sock, "C") is False
        # 거부는 **ssh 를 띄우기 전에** 난다 — spawn 을 가로채 호출 0 을 확인한다.
        import subprocess
        spawned = []
        with harness.patched(subprocess, Popen=lambda *a, **k: spawned.append(a)):
            assert L.run_relay_proxy(sock, "C") == 1
            assert L.run_relay_proxy(sock, "-oProxyCommand=x") == 1
        assert not spawned, spawned
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
