"""Tier 3 라이브 레드팀 + 자원 모니터 — docs/internal/SECURITY_REVIEW.md §9.3.

정적(§0~§8)·런타임 Tier 1(인하네스 적대, test_security_runtime)·Tier 2(파서 퍼징)에
이어, **실행 중인 서버**를 밖에서 두드리며 자원(메모리·fd)을 표본한다. 두 모드:

  --spawn (기본): 격리된 인프로세스 서버를 띄워 적대 배터리(인증 우회·프레임 퍼징)를
      대량 **순차** 반복 → **동시 폭주**(width 연결 동시 열기) → **슬로로리스**(half-open
      잡아두고 정상 클라 응답성 표본) → **post-auth 퍼징**(유효 토큰 통과 후 명령 핸들러
      악성 입력)까지 던지며 self 메모리(tracemalloc)·열린 fd 를 표본해 ① 서버 생존
      ② 메모리 선형 상한 ③ fd 누수 없음 ④ 무인가 수용 0 ⑤ 슬로로리스 중 정상 트래픽
      안 굶김 ⑥ post-auth 생존을 단언한다. 자기완결(회귀 test_redteam 와 코어 공유).

  --attach ENDPOINT: 이미 도는 서버(office·default 데몬)에 붙어 **비파괴** 배터리만
      던진다 — 인증 우회 시도는 서버가 거절하므로 무영향, 유효 토큰으론 **read-only
      list** 만. 파괴적(unauth kill-server/control) 시도는 `--destructive` 일 때만(낡은
      무인증 서버를 우발적으로 죽이지 않도록 기본 차단). 외부 PID 의 RSS/fd 표본은
      best-effort(/proc·/dev/fd·ps·lsof).

CI 제외: 헤드리스 macOS 러너 wedge(windows.yml 주석) + 라이브 세션 의존 → 로컬/Windows
수동 도구. 코어(run_battery·표본 헬퍼)의 가벼운 회귀만 tests/test_redteam.py 가 검증한다.

사용:
    python scripts/redteam.py                      # 격리 서버 spawn → 배터리 → 자원 단언
    python scripts/redteam.py --rounds 500
    python scripts/redteam.py --attach $PYTMUX     # 라이브 서버 비파괴 점검
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import gc
import json
import os
import subprocess
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pytmuxlib import ipc, protocol  # noqa: E402
from pytmuxlib.protocol import MAX_FRAME  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# 자원 표본(메모리·fd) — self / 외부 PID
# ─────────────────────────────────────────────────────────────────────────────
def _win_handle_count(pid: int | None = None) -> int:
    """Windows 프로세스 핸들 수(fd 누수 등가 지표 — 소켓·파일이 핸들로 잡힌다).
    pid=None 이면 자기 프로세스, 아니면 외부 PID(`OpenProcess`). 불가면 -1.

    ctypes 함정(SECURITY_REVIEW §10 W3): `GetCurrentProcess`/`OpenProcess` 의 restype 과
    `GetProcessHandleCount` 의 argtypes(HANDLE/POINTER(DWORD))를 명시하지 않으면 64-bit
    핸들이 잘려 호출이 실패(-1)한다.
    """
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.GetProcessHandleCount.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(wintypes.DWORD)]
        k.GetProcessHandleCount.restype = wintypes.BOOL
        n = wintypes.DWORD(0)
        if pid is None:
            k.GetCurrentProcess.restype = wintypes.HANDLE
            handle = k.GetCurrentProcess()
            ok = k.GetProcessHandleCount(handle, ctypes.byref(n))
            return int(n.value) if ok else -1
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return -1
        try:
            ok = k.GetProcessHandleCount(handle, ctypes.byref(n))
            return int(n.value) if ok else -1
        finally:
            k.CloseHandle(handle)
    except Exception:    # noqa: BLE001 (best-effort 표본 — 못 재면 -1)
        return -1


def _win_rss_kb(pid: int) -> int:
    """Windows 외부 PID 작업셋(KB) — `tasklist` CSV 의 메모리 열 파싱. 불가면 -1."""
    try:
        import csv
        import io
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10)
        lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
        if not lines:
            return -1
        row = next(csv.reader(io.StringIO(lines[-1])))
        # 마지막 열 = "73,248 K"(콤마 천단위 + 단위) → 정수 KB.
        mem = row[-1].replace(",", "").replace("K", "").strip()
        return int(mem)
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
        return -1


def _pid_listening_on(host: str, port: int) -> int:
    """host:port 를 LISTENING 중인 PID(외부 서버 자원 표본·디스커버리용). 불가면 -1.

    Windows=`netstat -ano`(행: `TCP  127.0.0.1:PORT  0.0.0.0:0  LISTENING  PID`),
    POSIX=`lsof -ti`. best-effort — 못 찾으면 -1(자원 표본만 건너뛰고 배터리는 진행).
    """
    needle = f"{host}:{port}"
    if ipc.IS_WINDOWS:
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return -1
        for ln in out.stdout.splitlines():
            parts = ln.split()
            # TCP  <local>  <remote>  LISTENING  <pid>
            if (len(parts) >= 5 and parts[0].upper() == "TCP"
                    and parts[1] == needle and parts[3].upper() == "LISTENING"):
                try:
                    return int(parts[-1])
                except ValueError:
                    return -1
        return -1
    try:
        out = subprocess.run(["lsof", "-ti", f"@{host}:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=10)
        first = out.stdout.split()
        return int(first[0]) if first else -1
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1


def count_fds() -> int:
    """현재 프로세스의 열린 fd 수. Linux=/proc/self/fd, macOS/BSD=/dev/fd,
    Windows=프로세스 핸들 수(fd 등가 누수 지표, §10 W3). 불가면 -1."""
    for d in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(d))
        except OSError:
            continue
    if ipc.IS_WINDOWS:
        return _win_handle_count()
    return -1


def pid_fds(pid: int) -> int:
    """외부 PID 의 열린 fd 수(/proc/<pid>/fd 우선, 아니면 lsof; Windows=핸들 수).
    best-effort, 불가면 -1."""
    try:
        return len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        pass
    if ipc.IS_WINDOWS:
        return _win_handle_count(pid)
    try:
        out = subprocess.run(["lsof", "-p", str(pid)], capture_output=True,
                             text=True, timeout=10)
        return max(0, len(out.stdout.splitlines()) - 1)
    except (OSError, subprocess.SubprocessError):
        return -1


def pid_rss_kb(pid: int) -> int:
    """외부 PID 의 RSS(KB). /proc/<pid>/status 우선, 아니면 ps; Windows=tasklist 작업셋.
    best-effort, 불가면 -1."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    if ipc.IS_WINDOWS:
        return _win_rss_kb(pid)
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip() or -1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1


# ─────────────────────────────────────────────────────────────────────────────
# raw 적대 클라 + 배터리
# ─────────────────────────────────────────────────────────────────────────────
async def _open(endpoint: str):
    kind = ipc.parse_endpoint(endpoint)
    if kind[0] == "unix":
        return await asyncio.open_unix_connection(kind[1])
    return await asyncio.open_connection(kind[1], kind[2])


async def _recv(reader, timeout: float = 2.0):
    try:
        return await asyncio.wait_for(protocol.read_msg(reader), timeout)
    except (asyncio.TimeoutError, OSError):
        return None


async def authed_list_alive(endpoint: str, token: str | None) -> bool:
    """올바른 토큰(있으면)으로 read-only list 왕복 — 서버 생존 확인. 비파괴."""
    try:
        r, w = await _open(endpoint)
    except OSError:
        return False
    try:
        msg = {"t": "list"}
        if token:
            msg["token"] = token
        w.write(protocol.frame_msg(msg))
        await w.drain()
        reply = await _recv(r)
        return isinstance(reply, dict) and reply.get("t") == "list"
    finally:
        w.close()


def _attacks(destructive: bool) -> list[tuple[str, bytes]]:
    """비파괴 적대 프레임 모음. 전부 무인가/손상이라 서버가 거절·드롭한다(라이브 무영향).
    destructive 면 무인가 kill-server/control 도 추가(낡은 무인증 서버 탐지용 — 인증
    게이트가 있으면 거절, 없으면 실제로 죽으므로 기본 차단)."""
    big = (MAX_FRAME + 1).to_bytes(4, "big") + b"x"
    _nj = b"\xff\xfenot json{"
    nonjson = len(_nj).to_bytes(4, "big") + _nj           # 길이 정합·비-JSON 본문
    nondict = protocol.frame_msg([1, 2, 3])
    trunc = (50).to_bytes(4, "big") + b"short"           # 길이 주장 > 실제(불완전)
    unauth_hello = protocol.frame_msg({"t": "hello", "cols": 80, "rows": 24})
    wrong_tok = protocol.frame_msg({"t": "hello", "token": "00" * 32,
                                    "cols": 80, "rows": 24})
    items = [("unauth_hello", unauth_hello), ("wrong_token", wrong_tok),
             ("oversized", big), ("non_json", nonjson), ("non_dict", nondict),
             ("truncated", trunc)]
    if destructive:
        items.append(("unauth_kill",
                      protocol.frame_msg({"t": "control", "line": "kill-server"})))
    return items


async def _one_shot(endpoint: str, name: str, payload: bytes) -> str:
    """단발 적대 연결: 프레임 송신→짧게 응답 대기→분류. 새 소켓 1개, 끝나면 닫는다.
    반환 = 결과 버킷 키(rejected/dropped/accepted_unexpected/errors). 순차 배터리와
    동시 폭주가 분류 로직을 공유한다."""
    try:
        r, w = await _open(endpoint)
    except OSError:
        return "errors"
    try:
        w.write(payload)
        await w.drain()
        # write_eof 로 즉시 EOF 를 보낸다 — 불완전 프레임(truncated 등)에서 서버가
        # 나머지 바이트를 기다리며 클라가 응답을 기다리는 교착(틱당 수초 스톨)을 없앤다.
        # 완전 프레임은 이미 응답/종료하므로 무해.
        with contextlib.suppress(Exception):
            w.write_eof()
        reply = await _recv(r, timeout=1.0)
        if reply is None:
            return "dropped"
        if isinstance(reply, dict) and reply.get("t") == "error":
            return "rejected"
        if name in ("unauth_hello", "wrong_token"):
            return "accepted_unexpected"          # 무인가가 받아들여짐 = 결함
        return "dropped"
    except OSError:
        return "dropped"
    finally:
        with contextlib.suppress(Exception):
            w.close()


async def run_battery(endpoint: str, rounds: int, *,
                      destructive: bool = False) -> dict:
    """적대 프레임을 rounds 회 **순차** 송신하고 결과를 집계한다(연결마다 새 소켓).

    반환: {sent, rejected, dropped, accepted_unexpected, errors}.
      rejected            = auth_failed 응답(인증 게이트가 막음 — 정상)
      dropped             = 서버가 응답 없이 연결만 끊음(손상 프레임 — 정상)
      accepted_unexpected = 무인가 hello 가 비-error dict 응답을 받음(= 서버 무인증! 결함)
    """
    counts = {"sent": 0, "rejected": 0, "dropped": 0,
              "accepted_unexpected": 0, "errors": 0}
    attacks = _attacks(destructive)
    for _ in range(rounds):
        for name, payload in attacks:
            counts["sent"] += 1
            counts[await _one_shot(endpoint, name, payload)] += 1
    return counts


async def run_concurrent_flood(endpoint: str, waves: int, width: int, *,
                               destructive: bool = False) -> dict:
    """적대 프레임을 **동시에** width 개씩 waves 회 폭주시킨다 — 순차 배터리가 못 보는
    동시 연결 처리(accept 루프·연결당 태스크 스폰·동시 fd 천장)를 친다. 각 연결은 응답
    직후 닫혀 라이브에도 비파괴(연결을 잡아두지 않음). 한 웨이브의 width 연결이 진짜
    동시에 열려 서버의 동시성 경로를 압박한다.

    반환: run_battery 와 같은 버킷 + peak_inflight(한 번에 띄운 최대 동시 연결 수).
    """
    counts = {"sent": 0, "rejected": 0, "dropped": 0,
              "accepted_unexpected": 0, "errors": 0, "peak_inflight": 0}
    attacks = _attacks(destructive)
    for _ in range(waves):
        tasks = [asyncio.ensure_future(
                     _one_shot(endpoint, attacks[i % len(attacks)][0],
                               attacks[i % len(attacks)][1]))
                 for i in range(width)]
        counts["peak_inflight"] = max(counts["peak_inflight"], len(tasks))
        for res in await asyncio.gather(*tasks, return_exceptions=True):
            counts["sent"] += 1
            counts[res if isinstance(res, str) else "errors"] += 1
    return counts


async def run_slowloris(endpoint: str, token: str | None, *, n_conns: int = 50,
                        hold_sec: float = 1.0, probes: int = 8) -> dict:
    """Half-open(slowloris) 내성 — n_conns 개 연결을 열어 **불완전 프레임**(유효 4바이트
    길이프리픽스만 보내고 본문은 보류)을 보내 서버를 매단다(read_msg 의 readexactly(length)
    가 본문을 기다리며 그 연결 태스크가 잡힌다 — 서버에 읽기 타임아웃이 없다). 잡아둔 채
    유효 토큰 list 왕복(probes 회)으로 **정상 클라 응답성(RTT)** 을 표본해 head-of-line
    독립(잡아둔 연결이 정상 트래픽을 굶기지 않음)을 확인하고, 전부 닫은 뒤 서버 생존을
    단언한다. 각 연결은 hold_sec 뒤 닫혀 라이브에도 시한부.

    반환 {held, probe_ok, probe_fail, max_rtt_ms, alive_after}.
      probe_ok>0·max_rtt 유한 = 슬로로리스가 정상 트래픽을 못 굶김(내성).
      probe 전멸(probe_ok=0) = 연결 고갈로 정상 클라까지 막힘(슬로로리스 DoS 신호).
    """
    writers = []
    stall = (1024).to_bytes(4, "big")        # length=1024 약속, 본문 0바이트 → 서버 대기
    held = 0
    probe_ok = probe_fail = 0
    max_rtt_ms = 0.0
    try:
        for _ in range(n_conns):
            try:
                _r, w = await _open(endpoint)
            except OSError:
                continue
            try:
                w.write(stall)
                await w.drain()
                writers.append(w)
            except OSError:
                with contextlib.suppress(Exception):
                    w.close()
        held = len(writers)
        for _ in range(probes):
            t0 = time.monotonic()
            ok = await authed_list_alive(endpoint, token)
            dt = (time.monotonic() - t0) * 1000.0
            if ok:
                probe_ok += 1
                max_rtt_ms = max(max_rtt_ms, dt)
            else:
                probe_fail += 1
            await asyncio.sleep(hold_sec / max(1, probes))
    finally:
        for w in writers:
            with contextlib.suppress(Exception):
                w.close()
    await asyncio.sleep(0.05)                 # 서버가 매달린 readexactly 를 EOF 로 풀 시간
    alive = await authed_list_alive(endpoint, token)
    return {"held": held, "probe_ok": probe_ok, "probe_fail": probe_fail,
            "max_rtt_ms": round(max_rtt_ms, 1), "alive_after": alive}


def _authed_fuzz_top(token: str | None) -> list[dict]:
    """유효 토큰을 실은 **악성 인증 프레임**(top-level: control/list/unknown). auth 게이트를
    통과한 뒤 명령 핸들러의 런타임 내성을 친다. kill-server 류 정상-파괴 명령은 제외."""
    tk = ({"token": token} if token else {})
    huge = "A" * (1 << 20)                    # 1 MiB 제어 라인(자원·파싱 압박)
    return [
        {"t": "control", "line": "\x00\xff ;;; $(rm -rf /) `id`", **tk},
        {"t": "control", "line": huge, **tk},
        {"t": "control", "line": 1234, **tk},                 # 비-str line
        {"t": "list", "junk": [1, {"a": huge[:2000]}], **tk}, # 과대 부속 필드
        {"t": "no_such_action", "x": [1, 2, 3], **tk},
    ]


def _authed_fuzz_loop() -> list[dict]:
    """hello 채택 후 **루프 메시지**(resize/input/scroll/cmd/unknown) 악성 변형. 디스패치
    try/except 가드를 런타임으로 실증한다(정적으로는 '잡힐 것'만 추론)."""
    huge = "A" * (1 << 20)
    return [
        {"t": "resize", "cols": "abc", "rows": None},
        {"t": "resize", "cols": -5, "rows": 10 ** 9},         # 음수·거대(clamp 확인)
        {"t": "input", "data": "!!!not-valid-base64!!!"},
        {"t": "input", "data": 12345},                        # 비-str(디코드 가드)
        {"t": "scroll", "delta": "x"},
        {"t": "cmd", "name": None, "args": huge[:2000]},
        {"t": "zzz_unknown_in_loop"},
    ]


async def run_authed_fuzz(endpoint: str, token: str | None) -> dict:
    """인증 통과 후(post-auth) 명령 핸들러 내성 — 유효 토큰을 실은 악성 프레임으로
    control/resize/input/scroll/cmd/unknown 디스패치를 친다. **격리 spawn 전용**(실 세션에
    주입되므로 라이브 attach 엔 안 씀). 각 프레임 송신 후 서버 프로세스 생존을 단언.

    반환 {sent, alive_after}. 정적 검토가 '디스패치 try/except 가 잡을 것'이라 한 가정을
    런타임 실행으로 확증한다 — 어떤 인증된 악성 입력에도 서버는 살아 있어야 한다.
    """
    sent = 0
    for frame in _authed_fuzz_top(token):
        try:
            _r, w = await _open(endpoint)
        except OSError:
            continue
        try:
            w.write(protocol.frame_msg(frame))
            await w.drain()
            await _recv(_r, timeout=1.0)
            sent += 1
        except OSError:
            pass
        finally:
            with contextlib.suppress(Exception):
                w.close()
    # hello 채택 → 루프 메시지 퍼징(같은 연결).
    hello = {"t": "hello", "cols": 80, "rows": 24}
    if token:
        hello["token"] = token
    with contextlib.suppress(OSError):
        _r, w = await _open(endpoint)
        try:
            w.write(protocol.frame_msg(hello))
            await w.drain()
            await _recv(_r, timeout=1.0)       # 초기 layout/screen
            for frame in _authed_fuzz_loop():
                w.write(protocol.frame_msg(frame))
                await w.drain()
                sent += 1
            await asyncio.sleep(0.1)           # 디스패치 처리 시간
        finally:
            with contextlib.suppress(Exception):
                w.close()
    alive = await authed_list_alive(endpoint, token)
    return {"sent": sent, "alive_after": alive}


# ─────────────────────────────────────────────────────────────────────────────
# 모드: spawn(자기완결) / attach(라이브 비파괴)
# ─────────────────────────────────────────────────────────────────────────────
def _boot_isolated_server(endpoint: str):
    """격리 환경(실 상태 미오염)으로 인프로세스 서버를 띄운다. (srv, task) 반환."""
    # PYTMUX_HOME 으로 상태(소켓·토큰·포트파일)까지 임시 격리한다. Windows 의 TCP
    # 엔드포인트는 토큰/포트파일이 `default_state_dir()`(=%LOCALAPPDATA%\\pytmux) 고정
    # 경로라, 격리 없이 띄우면 실 default 데몬의 토큰/포트를 덮어쓴다(§10 W2). unix
    # 경로는 endpoint 기반이라 무관하지만 통일해 둔다.
    os.environ["PYTMUX_HOME"] = tempfile.mkdtemp(prefix="redteam-home-")
    os.environ["PYTMUX_PTY_HOST"] = "0"          # detached host 안 띄움(결정론)
    os.environ["PYTMUX_CAPTURE_DIR"] = tempfile.mkdtemp(prefix="redteam-cap-")
    os.environ["PYTMUX_TOKENS_DB"] = tempfile.mktemp(suffix=".db",
                                                     prefix="redteam-db-")
    import pytmux
    srv = pytmux.Server(endpoint)
    task = asyncio.ensure_future(srv.serve())
    return srv, task


async def _await_endpoint_ready(endpoint: str, tries: int = 300) -> str:
    """서버가 listen 준비될 때까지 대기하고 **접속 가능한** 엔드포인트를 돌려준다.

    TCP(에페메럴 PORT 0)면 포트파일이 게시될 때까지 기다려 실제 포트를 박은
    "tcp:host:port" 를, unix 면 소켓 파일이 생길 때까지 기다려 그 경로를 돌려준다.
    """
    if ipc.is_tcp(endpoint):
        pf = ipc.portfile_for(endpoint)
        _, host, _ = ipc.parse_endpoint(endpoint)
        for _ in range(tries):
            port = ipc._read_portfile(pf)
            if port:
                return f"tcp:{host}:{port}"
            await asyncio.sleep(0.01)
        return endpoint
    for _ in range(tries):
        if os.path.exists(endpoint):
            return endpoint
        await asyncio.sleep(0.01)
    return endpoint


def _kill_server(srv) -> None:
    srv.running = False
    for s in list(getattr(srv, "sessions", {}).values()):
        for t in s.tabs:
            for p in t.window.panes():
                with contextlib.suppress(Exception):
                    if p.pty is not None:
                        p.pty.kill()
                        p.pty.close()


# spawn 모드 임계(self 자원 단언). 1200여 짧은 연결의 정상 변동을 넉넉히 덮되,
# 명백한 누수(연결당 fd 미반환·메모리 단조증가)는 잡는다.
_FD_SLACK = 16
_MEM_GROWTH_MAX = 12 * 1024 * 1024       # 12 MiB


async def _settled_fd_count(fd0: int) -> int:
    """배터리 직후 fd/핸들 표본이 임계를 넘으면 잠깐 이벤트 루프를 돌리고 GC 후
    재표본한다(최대 ~3s) — '지속 누수'만 판정하기 위함.

    막 닫힌 소켓/transport 의 OS 핸들 회수는 이벤트 루프 후속 틱·GC 에 걸쳐 완료되고,
    특히 Windows 의 GetProcessHandleCount 는 소켓 외 전체 핸들(스레드·이벤트 등)이라
    직후 표본이 일시적으로 부푼다 — GHA windows-3.11 이 fd Δ18(>16)로 **단발 flaky**
    하던 원인(재실행은 통과 = 지속 누수 아님, 2026-07-09/10 두 차례 관측). 진짜
    연결당 누수(1200 연결이면 수백)는 기다려도 안 줄어 그대로 잡힌다."""
    fd1 = count_fds()
    if fd0 < 0:
        return fd1
    for _ in range(6):
        if fd1 - fd0 <= _FD_SLACK:
            break
        await asyncio.sleep(0.5)
        gc.collect()
        fd1 = count_fds()
    return fd1


def _res_growth(res0: dict | None, res1: dict | None) -> tuple[bool, dict | None]:
    """attach 자원 표본의 누수 단언(전후 비교). 둘 다 유효 표본일 때만 단언한다.

    반환 (ok, detail). 표본이 없거나(-1=측정 불가) 한쪽만 있으면 ok=True(보수: 측정
    못 한 것을 결함으로 치지 않는다). fd/핸들 증가만 verdict 에 반영한다 — 비파괴
    배터리는 연결을 열고 즉시 닫으므로 서버 핸들 수가 누적되면 누수다(연결당 미반환).
    RSS 는 라이브 데몬이 타 클라·GC 로 출렁이므로 **보고만** 하고 게이트하지 않는다.
    """
    if not res0 or not res1:
        return True, None
    detail: dict = {}
    ok = True
    if res0.get("fds", -1) >= 0 and res1.get("fds", -1) >= 0:
        g = res1["fds"] - res0["fds"]
        detail["fd_growth"] = g
        ok = ok and g <= _FD_SLACK
    if res0.get("rss_kb", -1) >= 0 and res1.get("rss_kb", -1) >= 0:
        detail["rss_growth_kb"] = res1["rss_kb"] - res0["rss_kb"]   # 보고만(미게이트)
    return ok, (detail or None)


async def redteam_spawn(rounds: int, *, conc_waves: int = 20,
                        conc_width: int = 40) -> dict:
    # Windows 는 asyncio AF_UNIX 미지원 → 프로덕션 전송인 TCP 루프백으로 띄운다(§10 W2).
    # POSIX 는 종전대로 임시 unix 소켓(파일권한 격리). resolved 는 실제 포트가 박힌
    # 접속 가능 엔드포인트(에페메럴 포트 0 은 그대로는 못 붙는다).
    endpoint = ("tcp:127.0.0.1:0" if ipc.IS_WINDOWS
                else tempfile.mktemp(suffix=".sock", prefix="redteam-"))
    srv, task = _boot_isolated_server(endpoint)
    resolved = await _await_endpoint_ready(endpoint)
    token = ipc.read_token(endpoint)
    try:
        # 워밍업(레이지 할당 안정화) 후 베이스라인.
        for _ in range(3):
            await authed_list_alive(resolved, token)
        gc.collect()
        tracemalloc.start()
        mem0, _ = tracemalloc.get_traced_memory()
        fd0 = count_fds()

        counts = await run_battery(resolved, rounds)
        # 순차 배터리 뒤 동시 폭주 — accept 루프·연결당 태스크·fd 천장을 압박한다.
        conc = await run_concurrent_flood(resolved, conc_waves, conc_width)
        # half-open 잡아두기(슬로로리스) — 정상 클라 응답성을 굶기는지 본다.
        slow = await run_slowloris(resolved, token, n_conns=50, hold_sec=0.8)
        # 인증 통과 후(post-auth) 명령 핸들러 내성 — 격리 spawn 에서만(실 세션 미주입).
        authed = await run_authed_fuzz(resolved, token)

        gc.collect()
        mem1, _ = tracemalloc.get_traced_memory()
        fd1 = await _settled_fd_count(fd0)   # 임계 초과 시 settle 후 재표본(누수만 판정)
        tracemalloc.stop()
        alive = await authed_list_alive(resolved, token)

        report = {
            "mode": "spawn", "rounds": rounds, "battery": counts,
            "concurrent": conc, "slowloris": slow, "authed_fuzz": authed,
            "server_alive": alive,
            "fd_before": fd0, "fd_after": fd1, "fd_growth": fd1 - fd0,
            "mem_growth_bytes": mem1 - mem0,
            "verdict_ok": (
                alive
                and counts["accepted_unexpected"] == 0
                and conc["accepted_unexpected"] == 0
                and counts["rejected"] + counts["dropped"] >= counts["sent"]
                and conc["rejected"] + conc["dropped"] + conc["errors"]
                    >= conc["sent"]
                and slow["alive_after"] and slow["probe_ok"] > 0   # 정상 클라 안 굶김
                and authed["alive_after"]                          # post-auth 생존
                and (fd0 < 0 or fd1 - fd0 <= _FD_SLACK)
                and (mem1 - mem0) <= _MEM_GROWTH_MAX),
        }
        return report
    finally:
        _kill_server(srv)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        if not ipc.is_tcp(endpoint):
            with contextlib.suppress(OSError):
                os.unlink(endpoint)


async def redteam_attach(endpoint: str, pid: int | None, rounds: int,
                         destructive: bool, *, conc_waves: int = 10,
                         conc_width: int = 20, slowloris: bool = False) -> dict:
    token = ipc.read_token(endpoint)
    res0 = ({"rss_kb": pid_rss_kb(pid), "fds": pid_fds(pid)} if pid else None)
    counts = await run_battery(endpoint, rounds, destructive=destructive)
    # 라이브 비파괴 동시 폭주(연결 즉시 닫힘): 외부 서버의 동시성 경로를 본다.
    conc = await run_concurrent_flood(endpoint, conc_waves, conc_width,
                                      destructive=destructive)
    # 슬로로리스는 라이브 데몬의 연결을 잠깐 잡으므로 기본 OFF(--slowloris 옵트인).
    # 켜도 hold 짧고 자동 해제 — 비파괴지만 보수적으로 둔다.
    slow = (await run_slowloris(endpoint, token, n_conns=30, hold_sec=0.6)
            if slowloris else None)
    res1 = ({"rss_kb": pid_rss_kb(pid), "fds": pid_fds(pid)} if pid else None)
    alive = await authed_list_alive(endpoint, token)
    res_ok, res_growth = _res_growth(res0, res1)
    report = {
        "mode": "attach", "endpoint": endpoint, "pid": pid, "rounds": rounds,
        "destructive": destructive, "battery": counts, "concurrent": conc,
        "slowloris": slow, "server_alive": alive,
        "resources_before": res0, "resources_after": res1,
        "resource_growth": res_growth,
        "verdict_ok": (alive and counts["accepted_unexpected"] == 0
                       and conc["accepted_unexpected"] == 0 and res_ok
                       and (slow is None
                            or (slow["alive_after"] and slow["probe_ok"] > 0))),
    }
    if counts["accepted_unexpected"] or conc["accepted_unexpected"]:
        report["warning"] = "무인가 hello 가 수용됨 — 서버가 인증 없이 동작(F1/M1 미적용?)"
    if slow is not None and slow["probe_ok"] == 0:
        report["warning"] = ("슬로로리스 중 정상 클라 list 가 전멸 — 연결 고갈/HOL "
                             "차단 가능(읽기 타임아웃·연결 캡 검토)")
    return report


def discover_target() -> tuple[str | None, int]:
    """이미 도는 default 데몬의 (접속가능 엔드포인트, 소유 PID) 자동 탐지.

    `ipc.resolve_default_endpoint()` 로 후보를 정하고 `probe` 로 살아있는지 확인한 뒤,
    TCP 면 포트파일의 실제 포트로 정규화하고 그 포트를 LISTENING 중인 PID 를 찾는다
    (외부 PID 자원 표본용). 도는 서버가 없으면 (None, -1). 이로써 attach 가 엔드포인트·
    PID 수동 입력 없이 이 박스에서 바로 돈다(§10 W2 가 spawn 을, 본 함수가 attach 를 실용화).
    """
    ep = ipc.resolve_default_endpoint()
    if not ipc.probe(ep):
        return None, -1
    if ipc.is_tcp(ep):
        _, host, _ = ipc.parse_endpoint(ep)
        port = ipc._read_portfile(ipc.portfile_for(ep))
        if port:
            return f"tcp:{host}:{port}", _pid_listening_on(host, port)
        return ep, -1
    return ep, -1            # unix 소켓: PID 미상(best-effort), 배터리는 그대로 진행


# 자식 서버를 격리 부팅하는 부트스트랩(--attach-selftest). 별도 프로세스라 부모(redteam)
# 의 외부-PID 자원 표본(§10 W3 OpenProcess 핸들·tasklist RSS)을 *다른 프로세스*에 대해
# 실증한다(--spawn 은 self 프로세스만 표본). PYTMUX_HOME=자식 env 로 상태 격리, PTY_HOST
# 미기동(결정론). 플랫폼 기본 엔드포인트(Windows=TCP, Unix=소켓)를 discover 와 맞춘다.
_SELFTEST_BOOTSTRAP = (
    "import asyncio, pytmux, pytmuxlib.ipc as _ipc;"
    "asyncio.run(pytmux.Server(_ipc.default_endpoint()).serve())"
)


async def redteam_attach_selftest(rounds: int) -> dict:
    """자기완결 외부-프로세스 attach 검증: 별도 자식 프로세스로 격리 서버를 띄우고
    discover→attach 로 비파괴 배터리 + **외부 PID 자원 표본**을 단언한 뒤 종료한다.

    `--spawn` 은 인프로세스(self 자원)라 §10 W3 의 외부-PID 핸들/RSS 표본 경로를 안 친다.
    이 모드는 *다른 프로세스*를 대상으로 ① discover 가 그 서버를 찾고 ② netstat 가 그
    포트의 소유 PID 를 정확히 집어내며(= Popen pid 와 일치) ③ 외부 PID 핸들/RSS 가
    실측되고 ④ 비파괴 배터리 뒤에도 서버 생존·핸들 누수 없음을 코드 실행으로 못박는다.
    """
    home = tempfile.mkdtemp(prefix="redteam-selftest-")
    env = dict(os.environ)
    env["PYTMUX_HOME"] = home
    env["PYTMUX_PTY_HOST"] = "0"
    env["PYTMUX_CAPTURE_DIR"] = tempfile.mkdtemp(prefix="redteam-selftest-cap-")
    env["PYTMUX_TOKENS_DB"] = tempfile.mktemp(suffix=".db",
                                              prefix="redteam-selftest-db-")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    child = subprocess.Popen([sys.executable, "-c", _SELFTEST_BOOTSTRAP],
                             env=env, cwd=root,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 부모(이 프로세스)도 같은 PYTMUX_HOME 을 봐야 discover 가 자식 상태(포트파일/토큰)를
    # 읽는다. 원복은 finally 에서.
    prev_home = os.environ.get("PYTMUX_HOME")
    os.environ["PYTMUX_HOME"] = home
    try:
        # 포트파일 게시(=서버 listen 준비) 대기.
        endpoint, owner_pid = None, -1
        for _ in range(500):
            if child.poll() is not None:
                raise RuntimeError(f"selftest 자식 서버 조기 종료(rc={child.returncode})")
            endpoint, owner_pid = discover_target()
            if endpoint:
                break
            await asyncio.sleep(0.02)
        if not endpoint:
            raise RuntimeError("selftest 자식 서버 디스커버리 실패(포트파일 미게시)")

        report = await redteam_attach(endpoint, owner_pid, rounds, destructive=False)
        report["mode"] = "attach-selftest"
        report["child_pid"] = child.pid
        report["discovered_pid"] = owner_pid
        # discover 가 집은 PID 가 실제 자식 프로세스와 일치하는지(디스커버리 정확성).
        # netstat 미가용 등으로 -1 이면 검증 불가로 두되(보수), 집었으면 일치 단언.
        report["pid_match"] = (owner_pid == child.pid) if owner_pid > 0 else None
        if report["pid_match"] is False:
            report["verdict_ok"] = False
            report["warning"] = (f"discover PID({owner_pid}) ≠ child PID({child.pid}) "
                                 "— 디스커버리가 엉뚱한 프로세스를 집음")
        return report
    finally:
        if prev_home is None:
            os.environ.pop("PYTMUX_HOME", None)
        else:
            os.environ["PYTMUX_HOME"] = prev_home
        with contextlib.suppress(Exception):
            child.terminate()
        with contextlib.suppress(Exception):
            child.wait(timeout=10)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="redteam",
        description="pytmux Tier 3 라이브 레드팀 + 자원 모니터(SECURITY_REVIEW §9.3)")
    ap.add_argument("--attach", metavar="ENDPOINT", default=None,
                    help="이미 도는 서버 엔드포인트(unix 경로 또는 tcp:host:port). "
                         "생략 시 격리 서버를 spawn 한다.")
    ap.add_argument("--discover", action="store_true",
                    help="도는 default 데몬을 자동 탐지해 비파괴 attach(엔드포인트·PID "
                         "수동 입력 불요). 도는 서버가 없으면 실패(verdict_ok=false).")
    ap.add_argument("--attach-selftest", action="store_true",
                    help="별도 자식 프로세스로 격리 서버를 띄워 discover→attach 로 "
                         "외부-PID 자원 표본·비파괴 배터리를 자기완결 검증(§10 W5).")
    ap.add_argument("--pid", type=int, default=None,
                    help="--attach 시 자원 표본할 서버 PID(best-effort)")
    ap.add_argument("--rounds", type=int, default=200, help="순차 배터리 반복 횟수")
    ap.add_argument("--conc-width", type=int, default=None,
                    help="동시 폭주 웨이브당 동시 연결 수(기본 spawn 40·attach 20)")
    ap.add_argument("--conc-waves", type=int, default=None,
                    help="동시 폭주 웨이브 수(기본 spawn 20·attach 10)")
    ap.add_argument("--destructive", action="store_true",
                    help="--attach 시 무인가 kill-server/control 도 시도(기본 차단)")
    ap.add_argument("--slowloris", action="store_true",
                    help="--attach/--discover 시 half-open 잡아두기로 정상 클라 응답성을 "
                         "표본(연결을 잠깐 잡으므로 라이브엔 기본 OFF; spawn 은 항상 ON)")
    ap.add_argument("--json", action="store_true", help="리포트를 JSON 으로 출력")
    args = ap.parse_args(argv)

    # 동시 폭주 오버라이드(미지정이면 spawn/attach 기본값 적용).
    conc_kw = {k: v for k, v in (("conc_waves", args.conc_waves),
                                 ("conc_width", args.conc_width)) if v}

    async def _run():
        if args.attach_selftest:
            return await redteam_attach_selftest(args.rounds)
        if args.discover:
            endpoint, pid = discover_target()
            if not endpoint:
                return {"mode": "discover", "server_alive": False,
                        "verdict_ok": False, "battery": {},
                        "warning": "도는 default 데몬을 못 찾음(서버 미기동?)"}
            rep = await redteam_attach(endpoint, pid, args.rounds,
                                       args.destructive,
                                       slowloris=args.slowloris, **conc_kw)
            rep["mode"] = "discover"
            return rep
        if args.attach:
            return await redteam_attach(args.attach, args.pid, args.rounds,
                                        args.destructive,
                                        slowloris=args.slowloris, **conc_kw)
        return await redteam_spawn(args.rounds, **conc_kw)

    report = asyncio.run(_run())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[redteam] mode={report['mode']} alive={report['server_alive']} "
              f"verdict_ok={report['verdict_ok']}")
        print(f"  battery(seq): {report['battery']}")
        if report.get("concurrent"):
            print(f"  concurrent:   {report['concurrent']}")
        if report.get("slowloris"):
            print(f"  slowloris:    {report['slowloris']}")
        if report.get("authed_fuzz"):
            print(f"  authed_fuzz:  {report['authed_fuzz']}")
        if report["mode"] == "spawn":
            print(f"  fd: {report['fd_before']}→{report['fd_after']} "
                  f"(Δ{report['fd_growth']})  mem Δ{report['mem_growth_bytes']}B")
        else:
            if report.get("endpoint"):
                print(f"  endpoint={report['endpoint']} pid={report.get('pid')}")
            if report.get("resource_growth"):
                print(f"  resources: {report['resources_before']} → "
                      f"{report['resources_after']}  Δ{report['resource_growth']}")
            if report.get("pid_match") is not None:
                print(f"  discover pid_match={report['pid_match']} "
                      f"(child={report.get('child_pid')} discovered={report.get('discovered_pid')})")
        if report.get("warning"):
            print(f"  ⚠ {report['warning']}")
    return 0 if report.get("verdict_ok") else 1


if __name__ == "__main__":
    sys.exit(main())
