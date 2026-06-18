"""PTY host 실프로세스 통합 테스트(옵션 C P7) — Windows 세션유지 재시작의 E2E 검증.

기존 ptyhost 테스트(test_ptyhost*.py)는 host 를 **인프로세스**(asyncio 태스크)로 띄워
프로토콜·재바인딩 로직을 검증한다. 이 파일은 그 위 한 칸 — host 를 `proc.spawn_detached`
로 **진짜 분리 OS 프로세스**(`python -m pytmuxlib.ptyhost`)로 띄워, 서버 프로세스가
죽었다 살아나는(= 연결을 끊었다 새 연결로 재접속하는) 핸드오프를 **실제 프로세스 경계와
실제 IPC(POSIX=AF_UNIX·Windows=TCP 루프백)** 위에서 검증한다.

이게 중요한 이유: 옵션 C 의 본질(host 가 PTY/ConPTY 를 영구 소유해 서버 재시작을 넘어
세션이 산다)은 "두 프로세스가 한 host 를 가로질러 같은 자식 셸을 공유"할 때만 진짜로
증명된다. 인프로세스 host 로는 그 프로세스 경계가 없다. 지금까지 이 경계 검증(P7)은
office Windows 박스 라이브로만 했는데, 이 테스트는 **백엔드 중립**(host 가 `pty_backend.spawn`
을 쓰므로 POSIX=_UnixPty·Windows=ConPTY)이라 **GitHub Actions Windows 러너(windows.yml)
에서 그대로 돈다** — 즉 라이브 수동 검증을 CI 로 끌어온다.

⚠️ 다른 ptyhost 테스트와 달리 **Windows 에서 스킵하지 않는다**(그게 이 파일의 목적).
자식은 셸이 아니라 결정적 cross-platform 파이썬 프로그램을 쓴다.
"""
import asyncio
import contextlib
import os
import re
import shutil
import sys
import tempfile

from pytmuxlib import proc, pty_backend
from pytmuxlib.ptyhostclient import PtyHostClient

# 자식이 한 칸씩 찍는 틱 — "TK<n>_PH". 출력만으로 셸 생존/출력 연속성을 결정적으로 본다.
_TICKER = (
    "import sys, time\n"
    "for i in range(100000):\n"
    "    sys.stdout.write('TK%d_PH\\n' % i)\n"
    "    sys.stdout.flush()\n"
    "    time.sleep(0.2)\n"
)
# 한 줄 입력을 받아 ECHO[...] 로 되돌리는 자식(입력 왕복 검증용).
_ECHOER = (
    "import sys\n"
    "for line in sys.stdin:\n"
    "    s = line.strip()\n"
    "    if s.endswith('QUIT_PH'):\n"
    "        break\n"
    "    sys.stdout.write('ECHO[' + s + ']\\n')\n"
    "    sys.stdout.flush()\n"
)
_TICK_RE = re.compile(r"TK(\d+)_PH")


def _host_listen(tmp: str):
    """host 가 리슨할 주소. POSIX=AF_UNIX 경로, Windows=TCP 에페메럴(+portfile)."""
    if pty_backend.IS_WINDOWS:
        return "tcp:127.0.0.1:0", os.path.join(tmp, "host.port")
    return os.path.join(tmp, "host.sock"), None


async def _spawn_real_host(tmp: str):
    """진짜 분리 프로세스로 pty-host 를 띄우고 (host_pid, connect_endpoint) 반환."""
    listen_ep, portfile = _host_listen(tmp)
    argv = [sys.executable, "-m", "pytmuxlib.ptyhost", "--endpoint", listen_ep]
    if portfile:
        argv += ["--portfile", portfile]
    host_pid = proc.spawn_detached(argv)
    assert host_pid > 0, "host 프로세스 기동 실패"
    # 리슨 준비를 폴링한다(POSIX=소켓파일·Windows=portfile 의 실제 포트).
    if pty_backend.IS_WINDOWS:
        port = None
        for _ in range(200):                 # 최대 ~10s
            try:
                with open(portfile, encoding="utf-8") as f:
                    port = int(f.read().strip())
                break
            except (OSError, ValueError):
                await asyncio.sleep(0.05)
        assert port, "host portfile(포트) 미생성"
        return host_pid, f"tcp:127.0.0.1:{port}"
    for _ in range(200):
        if os.path.exists(listen_ep):
            break
        await asyncio.sleep(0.05)
    assert os.path.exists(listen_ep), "host 소켓 미생성"
    return host_pid, listen_ep


async def _connect(loop, endpoint: str, tries: int = 60) -> PtyHostClient:
    """host 에 접속(소켓 파일은 떴지만 accept 전인 짧은 창을 재시도로 흡수)."""
    last = None
    for _ in range(tries):
        client = PtyHostClient(loop)
        try:
            await asyncio.wait_for(client.connect(endpoint), 1.0)
            return client
        except Exception as e:                # noqa: BLE001 — 재시도 루프
            last = e
            with contextlib.suppress(Exception):
                await client.close()
            await asyncio.sleep(0.05)
    raise AssertionError(f"host 연결 실패: {last!r}")


def _ticks(buf: bytearray) -> set:
    return {int(m) for m in _TICK_RE.findall(buf.decode("utf-8", "replace"))}


async def _await(predicate, timeout: float = 8.0) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.03)
    return predicate()


def _host_dead(host_pid: int) -> bool:
    """host 프로세스가 종료됐는지. POSIX 는 좀비 주의 — 분리 host 는 이 테스트 프로세스의
    자식이라 종료 후 거둬지기 전엔 `os.kill(pid,0)`(proc.is_alive)이 '살아있음'으로 본다.
    그래서 POSIX 는 `waitpid(WNOHANG)`로 거둬(reap) 진짜 종료를 판정한다."""
    if pty_backend.IS_WINDOWS:
        return not proc.is_alive(host_pid)
    try:
        pid, _ = os.waitpid(host_pid, os.WNOHANG)
        return pid == host_pid              # 거둬짐 → 종료 확정
    except ChildProcessError:
        return True                         # 이미 없음/거둬짐
    except OSError:
        return not proc.is_alive(host_pid)


async def _kill_host(host_pid: int, *clients):
    for c in clients:
        with contextlib.suppress(Exception):
            await c.close()
    # 백스톱: 분리 프로세스가 새지 않게 강제 종료(POSIX=killpg·Windows=/F /T → 자식까지).
    if host_pid and proc.is_alive(host_pid):
        with contextlib.suppress(Exception):
            proc.terminate(host_pid, force=True, grace=2.0)
    # POSIX: 좀비를 거둬 프로세스 테이블 누수를 막는다(다음 테스트들과 한 프로세스 공유).
    if host_pid and not pty_backend.IS_WINDOWS:
        with contextlib.suppress(Exception):
            os.waitpid(host_pid, os.WNOHANG)


async def test_real_host_subprocess_survives_server_restart():
    """진짜 host 서브프로세스를 가로질러 자식 셸이 서버 재시작을 넘어 산다.

    서버 A 연결 → 자식(틱커) 기동 → A 끊김(서버 다운) → 갭 동안 자식이 계속 틱(=host 가
    pending 에 버퍼링) → 서버 B 가 같은 host 에 재접속(서버 재시작) → host 가 pending 을
    flush + 이후 라이브 틱이 B 로 흐른다. B 가 'A 가 본 마지막 틱보다 큰' 틱을 받으면
    같은 자식이 재시작을 넘어 계속 살아 출력했다는 증명(= 옵션 C 세션유지의 본질).
    """
    tmp = tempfile.mkdtemp(prefix="pytmux-ph-int-")
    loop = asyncio.get_event_loop()
    host_pid = 0
    clientA = clientB = None
    PANE = 1
    try:
        host_pid, endpoint = await _spawn_real_host(tmp)

        # ── 서버 A: 접속 후 틱커 자식 기동, 몇 틱 수신 ──
        clientA = await _connect(loop, endpoint)
        bufA = bytearray()
        clientA.register(PANE, lambda d: bufA.extend(d), None)
        clientA.spawn(PANE, [sys.executable, "-c", _TICKER], 80, 24)
        assert await _await(lambda: len(_ticks(bufA)) >= 2), \
            f"A 가 틱을 못 받음: {bufA[-120:]!r}"
        last_a = max(_ticks(bufA))

        # ── 서버 다운: A 연결만 끊는다(host·자식은 산다). 갭 동안 틱이 쌓인다 ──
        await clientA.close()
        clientA = None
        await asyncio.sleep(1.0)             # 자식이 갭 동안 계속 틱 → host pending 버퍼

        # ── 서버 B(=재시작 이미지): 같은 host 에 재접속 ──
        clientB = await _connect(loop, endpoint)
        bufB = bytearray()
        clientB.register(PANE, lambda d: bufB.extend(d), None)  # flush 전에 콜백 장착
        # B 가 last_a 보다 큰 틱을 받으면 = 같은 자식이 재시작을 넘어 계속 산다.
        assert await _await(lambda: any(t > last_a for t in _ticks(bufB))), \
            f"재시작 후 자식 출력이 끊김(last_a={last_a}, bufB={bufB[-160:]!r})"

        # host 가 여전히 같은 패널을 소유(같은 pane_id, alive).
        panes = await clientB.list_panes()
        ids = {p["pane"]: p for p in panes}
        assert PANE in ids and ids[PANE]["alive"], f"패널 미보존: {panes!r}"
        # host 프로세스도 살아 있어야 한다(재시작이 host 를 건드리지 않았다).
        assert proc.is_alive(host_pid), "재시작이 host 프로세스를 죽였다"
    finally:
        await _kill_host(host_pid, *(c for c in (clientA, clientB) if c))
        shutil.rmtree(tmp, ignore_errors=True)


async def test_real_host_input_roundtrips_over_real_ipc():
    """실 IPC + 실 PTY 를 가로지른 입력 왕복 — 서버가 보낸 키 입력이 자식까지 닿는다.

    인프로세스 테스트는 입력을 같은 프로세스 안에서 전달하지만, 여기선 서버→host(소켓)→
    PTY→자식→host→서버(소켓) 전 경로를 진짜 두 프로세스로 검증한다. Enter 는 실제 키처럼
    CR(`\\r`)로 보낸다(POSIX ICRNL·Windows ConPTY 양쪽에서 줄 완성)."""
    tmp = tempfile.mkdtemp(prefix="pytmux-ph-in-")
    loop = asyncio.get_event_loop()
    host_pid = 0
    client = None
    PANE = 7
    try:
        host_pid, endpoint = await _spawn_real_host(tmp)
        client = await _connect(loop, endpoint)
        buf = bytearray()
        client.register(PANE, lambda d: buf.extend(d), None)
        client.spawn(PANE, [sys.executable, "-c", _ECHOER], 80, 24)
        await asyncio.sleep(0.3)            # 자식 stdin 루프 진입 대기
        client.send_input(PANE, b"PING_PH\r")
        echoed = re.compile(r"ECHO\[[^\]]*PING_PH[^\]]*\]")
        assert await _await(
            lambda: echoed.search(buf.decode("utf-8", "replace")) is not None), \
            f"입력 왕복 실패: {buf[-160:]!r}"
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.send_input(PANE, b"QUIT_PH\r")
        await _kill_host(host_pid, *( (client,) if client else () ))
        shutil.rmtree(tmp, ignore_errors=True)


async def test_real_host_shutdown_terminates_process_and_child():
    """서버의 '진짜' 종료(shutdown op)는 host 프로세스와 자식을 함께 내린다 — 고아 방지.

    재시작 경로(연결만 끊음)와 대비되는 경로. shutdown_host() 후 host 프로세스가 실제로
    사라지는지(분리 프로세스 누수 없음)를 본다."""
    tmp = tempfile.mkdtemp(prefix="pytmux-ph-sd-")
    loop = asyncio.get_event_loop()
    host_pid = 0
    client = None
    PANE = 3
    try:
        host_pid, endpoint = await _spawn_real_host(tmp)
        client = await _connect(loop, endpoint)
        client.register(PANE, lambda d: None, None)
        client.spawn(PANE, [sys.executable, "-c", _TICKER], 80, 24)
        await asyncio.sleep(0.3)
        assert proc.is_alive(host_pid), "host 가 안 떴다"

        client.shutdown_host()              # 모든 패널 종료 + host serve 루프 정지
        # fire-and-forget 송신이라 close 전에 drain 으로 프레임이 실제로 host 에 닿게 한다
        # (안 하면 close 가 버퍼를 비우기 전에 writer 를 닫아 shutdown op 가 유실된다).
        with contextlib.suppress(Exception):
            await client.writer.drain()
        await client.close()
        client = None
        # host 프로세스가 질서있게 내려가야 한다(자식 셸도 함께 = 고아 없음).
        # Windows 는 종료가 느릴 수 있다 — ConPTY reader 스레드가 ReadFile 에서 풀리고
        # 자식 OpenConsole 이 정리될 때까지 프로세스가 안 빠진다(정정성 아닌 타이밍).
        # 6s 면 CI 에서 가끔 모자라 flaky 했다 → 넉넉히(run.py TEST_TIMEOUT=90s 안).
        assert await _await(lambda: _host_dead(host_pid), timeout=20.0), \
            "shutdown 후에도 host 프로세스가 살아 있다(고아)"
        host_pid = 0                         # 정상 종료 — 백스톱 불필요
    finally:
        await _kill_host(host_pid, *( (client,) if client else () ))
        shutil.rmtree(tmp, ignore_errors=True)
