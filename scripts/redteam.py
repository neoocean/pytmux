"""Tier 3 라이브 레드팀 + 자원 모니터 — docs/internal/SECURITY_REVIEW.md §9.3.

정적(§0~§8)·런타임 Tier 1(인하네스 적대, test_security_runtime)·Tier 2(파서 퍼징)에
이어, **실행 중인 서버**를 밖에서 두드리며 자원(메모리·fd)을 표본한다. 두 모드:

  --spawn (기본): 격리된 인프로세스 서버를 띄워 적대 배터리(인증 우회·프레임 퍼징)를
      대량 반복하며 self 메모리(tracemalloc)·열린 fd 를 표본해 ① 서버 생존 ② 메모리
      선형 상한 ③ fd 누수 없음을 단언한다. 자기완결(회귀 test_redteam 와 코어 공유).

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
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pytmuxlib import ipc, protocol  # noqa: E402
from pytmuxlib.protocol import MAX_FRAME  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# 자원 표본(메모리·fd) — self / 외부 PID
# ─────────────────────────────────────────────────────────────────────────────
def count_fds() -> int:
    """현재 프로세스의 열린 fd 수. Linux=/proc/self/fd, macOS/BSD=/dev/fd. 불가면 -1."""
    for d in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(d))
        except OSError:
            continue
    return -1


def pid_fds(pid: int) -> int:
    """외부 PID 의 열린 fd 수(/proc/<pid>/fd 우선, 아니면 lsof). best-effort, 불가면 -1."""
    try:
        return len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        pass
    try:
        out = subprocess.run(["lsof", "-p", str(pid)], capture_output=True,
                             text=True, timeout=10)
        return max(0, len(out.stdout.splitlines()) - 1)
    except (OSError, subprocess.SubprocessError):
        return -1


def pid_rss_kb(pid: int) -> int:
    """외부 PID 의 RSS(KB). /proc/<pid>/status 우선, 아니면 ps. best-effort, 불가면 -1."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
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


async def run_battery(endpoint: str, rounds: int, *,
                      destructive: bool = False) -> dict:
    """적대 프레임을 rounds 회 반복 송신하고 결과를 집계한다(연결마다 새 소켓).

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
            try:
                r, w = await _open(endpoint)
            except OSError:
                counts["errors"] += 1
                continue
            try:
                w.write(payload)
                await w.drain()
                # write_eof 로 즉시 EOF 를 보낸다 — 불완전 프레임(truncated 등)에서
                # 서버가 나머지 바이트를 기다리며 클라가 응답을 기다리는 교착(틱당 수초
                # 스톨)을 없앤다. 완전 프레임은 이미 응답/종료하므로 무해.
                with contextlib.suppress(Exception):
                    w.write_eof()
                reply = await _recv(r, timeout=1.0)
                if reply is None:
                    counts["dropped"] += 1
                elif isinstance(reply, dict) and reply.get("t") == "error":
                    counts["rejected"] += 1
                elif name in ("unauth_hello", "wrong_token"):
                    counts["accepted_unexpected"] += 1   # 무인가가 받아들여짐 = 결함
                else:
                    counts["dropped"] += 1
            except OSError:
                counts["dropped"] += 1
            finally:
                with contextlib.suppress(Exception):
                    w.close()
    return counts


# ─────────────────────────────────────────────────────────────────────────────
# 모드: spawn(자기완결) / attach(라이브 비파괴)
# ─────────────────────────────────────────────────────────────────────────────
def _boot_isolated_server(endpoint: str):
    """격리 환경(실 상태 미오염)으로 인프로세스 서버를 띄운다. (srv, task) 반환."""
    os.environ["PYTMUX_PTY_HOST"] = "0"          # detached host 안 띄움(결정론)
    os.environ["PYTMUX_CAPTURE_DIR"] = tempfile.mkdtemp(prefix="redteam-cap-")
    os.environ["PYTMUX_TOKENS_DB"] = tempfile.mktemp(suffix=".db",
                                                     prefix="redteam-db-")
    import pytmux
    srv = pytmux.Server(endpoint)
    task = asyncio.ensure_future(srv.serve())
    return srv, task


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


async def redteam_spawn(rounds: int) -> dict:
    endpoint = tempfile.mktemp(suffix=".sock", prefix="redteam-")
    srv, task = _boot_isolated_server(endpoint)
    for _ in range(300):
        if os.path.exists(endpoint):
            break
        await asyncio.sleep(0.01)
    token = ipc.read_token(endpoint)
    try:
        # 워밍업(레이지 할당 안정화) 후 베이스라인.
        for _ in range(3):
            await authed_list_alive(endpoint, token)
        gc.collect()
        tracemalloc.start()
        mem0, _ = tracemalloc.get_traced_memory()
        fd0 = count_fds()

        counts = await run_battery(endpoint, rounds)

        gc.collect()
        mem1, _ = tracemalloc.get_traced_memory()
        fd1 = count_fds()
        tracemalloc.stop()
        alive = await authed_list_alive(endpoint, token)

        report = {
            "mode": "spawn", "rounds": rounds, "battery": counts,
            "server_alive": alive,
            "fd_before": fd0, "fd_after": fd1, "fd_growth": fd1 - fd0,
            "mem_growth_bytes": mem1 - mem0,
            "verdict_ok": (
                alive
                and counts["accepted_unexpected"] == 0
                and counts["rejected"] + counts["dropped"] >= counts["sent"]
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
                         destructive: bool) -> dict:
    token = ipc.read_token(endpoint)
    res0 = ({"rss_kb": pid_rss_kb(pid), "fds": pid_fds(pid)} if pid else None)
    counts = await run_battery(endpoint, rounds, destructive=destructive)
    res1 = ({"rss_kb": pid_rss_kb(pid), "fds": pid_fds(pid)} if pid else None)
    alive = await authed_list_alive(endpoint, token)
    report = {
        "mode": "attach", "endpoint": endpoint, "rounds": rounds,
        "destructive": destructive, "battery": counts, "server_alive": alive,
        "resources_before": res0, "resources_after": res1,
        "verdict_ok": alive and counts["accepted_unexpected"] == 0,
    }
    if counts["accepted_unexpected"]:
        report["warning"] = "무인가 hello 가 수용됨 — 서버가 인증 없이 동작(F1/M1 미적용?)"
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="redteam",
        description="pytmux Tier 3 라이브 레드팀 + 자원 모니터(SECURITY_REVIEW §9.3)")
    ap.add_argument("--attach", metavar="ENDPOINT", default=None,
                    help="이미 도는 서버 엔드포인트(unix 경로 또는 tcp:host:port). "
                         "생략 시 격리 서버를 spawn 한다.")
    ap.add_argument("--pid", type=int, default=None,
                    help="--attach 시 자원 표본할 서버 PID(best-effort)")
    ap.add_argument("--rounds", type=int, default=200, help="배터리 반복 횟수")
    ap.add_argument("--destructive", action="store_true",
                    help="--attach 시 무인가 kill-server/control 도 시도(기본 차단)")
    ap.add_argument("--json", action="store_true", help="리포트를 JSON 으로 출력")
    args = ap.parse_args(argv)

    async def _run():
        if args.attach:
            return await redteam_attach(args.attach, args.pid, args.rounds,
                                        args.destructive)
        return await redteam_spawn(args.rounds)

    report = asyncio.run(_run())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[redteam] mode={report['mode']} alive={report['server_alive']} "
              f"verdict_ok={report['verdict_ok']}")
        print(f"  battery: {report['battery']}")
        if report["mode"] == "spawn":
            print(f"  fd: {report['fd_before']}→{report['fd_after']} "
                  f"(Δ{report['fd_growth']})  mem Δ{report['mem_growth_bytes']}B")
        if report.get("warning"):
            print(f"  ⚠ {report['warning']}")
    return 0 if report.get("verdict_ok") else 1


if __name__ == "__main__":
    sys.exit(main())
