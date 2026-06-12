"""데몬화 · 런처 · 외부 제어 CLI.

OS 별 분기(데몬화/소켓)는 직접 알지 않고 추상층만 부른다(docs/WINDOWS_PORT.md §7-c):
  * 서버 데몬 기동/존재확인 → pytmuxlib.proc (Unix setsid 분리 / Windows DETACHED).
  * 소켓 접속/제어/probe → pytmuxlib.ipc (Unix AF_UNIX / Windows TCP 루프백+포트파일).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import ipc, proc, protocol
# NOTE: client(=textual)·server(=model→pyte→wcwidth) 는 여기서 import 하지 않는다.
# 가벼운 제어 명령(ls/cmd/kill)이 launcher 만 거쳐도 textual 전체나 pyte/wcwidth 를
# 로드해 기동이 느려졌다(Windows 사용자 보고). attach 경로의 client, `server` 명령의
# run_server 모두 main() 안에서 필요 시점에만 지연 import 한다(A4).


def can_connect(sock_path: str) -> bool:
    return ipc.probe(sock_path)


def wait_server(sock_path: str, *, polls: int = 200, interval: float = 0.02) -> bool:
    """서버가 listen 떠 접속 가능해질 때까지 폴링. 성공이면 True, 시간 초과면 False.

    A2: 초기엔 촘촘히(2ms~) 지수 백오프 후 `interval`(20ms) 상한으로 폴 — 서버가
    빨리(<20ms) 뜬 경우의 체감 지연을 줄인다(고정 20ms 면 최대 20ms 허비). 총 예산은
    기존과 동일(polls*interval ≈ 4s)으로 유지."""
    deadline = time.monotonic() + polls * interval
    delay = 0.002
    while True:
        if ipc.probe(sock_path):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(delay, interval))
        delay *= 1.6


def ensure_server(sock_path: str):
    if ipc.probe(sock_path):
        return
    # 부모 생애와 무관하게 살아남는 분리 서버 프로세스를 띄운다(Unix setsid /
    # Windows DETACHED_PROCESS). 그 뒤 listen 이 떠 접속 가능해질 때까지 대기.
    proc.spawn_detached(proc.server_argv(sock_path))
    if not wait_server(sock_path):
        print("pytmux: 서버 기동 실패", file=sys.stderr)
        sys.exit(1)


def control_request(sock_path: str, obj: dict):
    s = ipc.control_socket(sock_path)
    if s is None:
        return None
    # 제어 프레임에도 와이어 버전을 실어 서버가 비호환을 거절할 수 있게 한다(#7).
    # 연결 인증 토큰(F1)도 함께 실어 서버가 무인가 접속을 거절하게 한다(없으면 생략).
    frame = {"proto": protocol.PROTO_VERSION}
    tok = ipc.read_token(sock_path)
    if tok:
        frame["token"] = tok
    frame.update(obj)
    data = json.dumps(frame).encode()
    s.sendall(len(data).to_bytes(4, "big") + data)
    try:
        header = _recvn(s, 4)
        if not header:
            return None
        n = int.from_bytes(header, "big")
        if n > protocol.MAX_FRAME:      # 무제한 응답 길이 → OOM 방지(read_msg 와 동일 상한)
            return None
        payload = _recvn(s, n)
        try:
            return json.loads(payload)  # bytes 직접; 손상·비-JSON 응답은 None
        except (ValueError, UnicodeDecodeError):
            return None
    finally:
        s.close()


def _recvn(s, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


# 원격(ssh) 중첩 표식. 패널 셸 env 에 심기고 ssh 래퍼가 SendEnv 로 원격에 전파한다
# (pytmuxlib.sshwrap). sshwrap.NEST_MARKER 와 반드시 일치.
NEST_MARKER = "LC_PYTMUX"


def nesting_blocked() -> bool:
    """pytmux 패널 안에서 또 pytmux 를 띄우려는 중첩인지(로컬·원격 공통 판정 지점).

    - **로컬**: 패널 셸에 서버가 `$PYTMUX`(소켓 경로)를 심으므로 그게 설정돼 있으면 중첩.
    - **원격(ssh)**: `$PYTMUX` 는 ssh 로 전파 안 되지만, 패널 셸의 ssh 래퍼가 표식
      `$LC_PYTMUX` 를 SendEnv 로 원격에 전파한다(sshwrap). 원격 pytmux 는 `$PYTMUX`
      가 없어도 이 표식을 보고 중첩을 거부한다(docs/HANDOFF.md §10).

    우회 수단은 `unset PYTMUX LC_PYTMUX` 뿐(강제 옵션은 제공하지 않는다)."""
    return bool(os.environ.get("PYTMUX") or os.environ.get(NEST_MARKER))


# §1.7 in-band 중첩 감지 프로브 대기 상한. 호스트가 pytmux 면 응답은 보통 수십 ms
# (ssh RTT)에 오고, 실제 터미널은 자기 이름으로 응답해(iTerm2/kitty/xterm 등 XTVERSION
# 지원) 조기 종료된다 — 전체 대기는 XTVERSION 무응답 단말에서만 발생.
NEST_PROBE_TIMEOUT = 0.4


def host_terminal_is_pytmux(timeout: float = NEST_PROBE_TIMEOUT,
                            rfd: int | None = None,
                            wfd: int | None = None) -> bool:
    """호스팅 단말에 XTVERSION(ESC[>0q)을 질의해 응답 단말명이 pytmux 면 True(§1.7).

    env 마커(`$PYTMUX`/`$LC_PYTMUX`)는 전파 의존이라 빈틈이 있다 — ssh 래퍼 우회
    (절대경로/alias 의 ssh), sshd `AcceptEnv` 에 LC_* 부재, 비-SendEnv 클라이언트.
    그 경로로 원격 pytmux 가 패널 안에서 실제로 떠 버리면 textual-in-pyte 중첩이
    crash-relaunch·net 워치독 재접속 루프("재접속 반복")로 나타난다. 이 프로브는
    전송과 무관하게 **단말 자체에게 물어** 중첩을 확정한다: 외부 pytmux 서버는 패널
    출력의 질의를 보고 `DCS >| pytmux ST` 로 응답한다(serverpty.NEST_REPLY).

    POSIX + 양쪽 tty 일 때만 동작(아니면 False). 응답이 pytmux 가 아닌 완결 DCS 면
    실제 터미널이므로 조기 False. cbreak 로 에코를 막고 원상복구한다.
    rfd/wfd 는 테스트 주입용(기본 stdin/stdout)."""
    if os.name == "nt":
        return False
    if rfd is None or wfd is None:
        try:
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                return False
        except (ValueError, OSError):
            return False
        rfd, wfd = sys.stdin.fileno(), sys.stdout.fileno()
    import select
    import termios
    import tty
    try:
        old = termios.tcgetattr(rfd)
    except termios.error:
        return False
    buf = b""
    try:
        tty.setcbreak(rfd, termios.TCSANOW)
        os.write(wfd, b"\x1b[>0q")
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            r, _, _ = select.select([rfd], [], [], left)
            if not r:
                return False
            try:
                chunk = os.read(rfd, 256)
            except OSError:
                return False
            if not chunk:
                return False
            buf += chunk
            if b"pytmux" in buf:
                return True
            if b"\x1b\\" in buf:    # 완결 DCS 인데 pytmux 아님 → 실제 터미널
                return False
    finally:
        try:
            termios.tcsetattr(rfd, termios.TCSADRAIN, old)
        except termios.error:
            pass


def run_stdio_proxy(sock_path: str) -> int:
    """원격 어태치 페더레이션의 원격 측 전송 프리미티브(§1.7 Stage 1).

    `ssh -T <host> pytmux stdio-proxy` 로 실행되면 ① 이 머신(원격)의 서버 인증
    토큰을 `TOKEN <hex>\\n` 한 줄로 알리고 ② 이후 stdin↔서버소켓↔stdout 을 그대로
    스플라이스한다. ssh exec 채널(-T, TTY 없음)은 8-bit clean 파이프라 와이어
    프로토콜의 길이-프레임이 무손상으로 통과한다 — 로컬 pytmux 서버는 이 파이프
    위에서 원격 서버에 hello(+토큰)로 attach 해 원격 탭/패널을 흡수한다(후속 Stage).

    POSIX 전용(stdin add_reader). 서버 없으면 1. v1 단순화: stdout 쓰기는 블로킹
    os.write — 프록시 루프엔 다른 일이 없어 무해(상세 docs/REMOTE_ATTACH_SCENARIO.md)."""
    if os.name == "nt":
        print("pytmux: stdio-proxy 는 POSIX 전용입니다", file=sys.stderr)
        return 1
    if not ipc.probe(sock_path):
        print("pytmux: 실행 중인 서버 없음", file=sys.stderr)
        return 1
    tok = ipc.read_token(sock_path) or ""
    os.write(1, f"TOKEN {tok}\n".encode())

    import asyncio

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        reader, writer = await ipc.open_connection(sock_path)
        done = loop.create_future()

        def _finish():
            if not done.done():
                done.set_result(None)

        def _on_stdin():
            try:
                data = os.read(0, 65536)
            except OSError:
                data = b""
            if data:
                writer.write(data)
            else:                      # ssh 끊김/로컬 측 종료 → 정리
                loop.remove_reader(0)
                _finish()

        os.set_blocking(0, False)
        loop.add_reader(0, _on_stdin)

        async def _sock_to_stdout():
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:       # 서버 종료
                        break
                    os.write(1, data)
            finally:
                _finish()

        t = loop.create_task(_sock_to_stdout())
        try:
            await done
        finally:
            loop.remove_reader(0)
            t.cancel()
            try:
                writer.close()
            except OSError:
                pass

    asyncio.run(_run())
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pytmux", description="tmux 유사 터미널 멀티플렉서")
    parser.add_argument("--socket", default=None, help="유닉스 도메인 소켓 경로")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("attach", help="실행 중인 서버에 attach (없으면 기동)")
    sub.add_parser("ls", help="탭/패널 요약")
    sub.add_parser("kill-server", help="서버와 모든 탭/셸 종료")
    p_cmd = sub.add_parser("cmd", help="실행 중 서버에 명령 전송(외부 제어)")
    p_cmd.add_argument("words", nargs=argparse.REMAINDER,
                       help="예: cmd new-tab / cmd split-window -h")
    # §1.7 원격 어태치 페더레이션 Stage 1 전송 프리미티브: `ssh -T <host> pytmux
    # stdio-proxy` 로 원격에서 실행되면 원격 서버 소켓 ↔ stdio 를 스플라이스한다.
    sub.add_parser("stdio-proxy",
                   help="(페더레이션) 로컬 서버 소켓 ↔ stdio 스플라이스")
    p_srv = sub.add_parser("server", help="(내부) 서버를 전경 실행")
    p_srv.add_argument("--foreground", action="store_true")
    # 작업 보존 재시작(re-exec): 새 서버 이미지가 이 상태 파일로 상속된 PTY 를 채택.
    p_srv.add_argument("--resume", default=None,
                       help="(내부) 재시작 보존 상태 파일로 부트")
    p_rec = sub.add_parser("record", help="명령을 PTY 에서 실행하며 원시 출력 녹화")
    p_rec.add_argument("file", help="녹화 파일 경로")
    p_rec.add_argument("--cols", type=int, default=None)
    p_rec.add_argument("--rows", type=int, default=None)
    p_rec.add_argument("words", nargs=argparse.REMAINDER,
                       help="실행할 명령(생략 시 $SHELL). 예: record out.raw -- ls -C")
    p_rep = sub.add_parser("replay", help="녹화 파일을 재생해 텍스트 프레임 덤프")
    p_rep.add_argument("file", help="녹화 파일 경로")
    p_rep.add_argument("--cols", type=int, default=None)
    p_rep.add_argument("--rows", type=int, default=None)
    p_rep.add_argument("--ruler", action="store_true", help="열 번호 자 표시")
    args = parser.parse_args(argv)

    # 명시 --socket 이 없으면 이미 떠 있는 서버를 찾아 붙는다(ssh 로그인 등으로
    # XDG_RUNTIME_DIR 유무가 갈려 소켓 경로가 어긋나도 같은 서버에 attach 하도록).
    # 서버가 없으면 canonical 기본 경로로 떨어져 종전과 동일하게 새로 기동한다.
    sock_path = args.socket or ipc.resolve_default_endpoint()

    if args.command == "stdio-proxy":
        sys.exit(run_stdio_proxy(sock_path))
    if args.command == "server":
        from .server import run_server   # 지연 import: 서버 데몬 경로에서만 model/pyte 로드
        run_server(sock_path, resume_path=getattr(args, "resume", None))
        return
    if args.command in ("record", "replay"):
        from .replay import run_record, run_replay, term_size
        tc, tr = term_size()
        cols = args.cols or tc
        rows = args.rows or tr
        if args.command == "record":
            words = [w for w in args.words if w != "--"]
            sys.exit(run_record(args.file, cols, rows, words))
        sys.exit(run_replay(args.file, cols, rows, ruler=args.ruler))
    if args.command == "ls":
        reply = control_request(sock_path, {"t": "list"})
        if not reply:
            print("실행 중인 서버 없음")
            return
        for s in reply.get("sessions", []):
            print(f"{s['windows']} tabs, {s['panes']} panes")
        return
    if args.command == "kill-server":
        reply = control_request(sock_path, {"t": "kill-server"})
        print("서버 종료됨" if reply else "실행 중인 서버 없음")
        return
    if args.command == "cmd":
        line = " ".join(args.words)
        reply = control_request(sock_path, {"t": "control", "line": line})
        if not reply:
            print("실행 중인 서버 없음")
        else:
            print(reply.get("result", "ok"))
        return

    # 기본 동작 = attach (필요 시 데몬 기동). 단일 세션 모델: 세션 이름 없음.
    # 중첩 실행 거부: pytmux 패널 안($PYTMUX 설정)에서 다시 attach 하면 막는다
    # (재귀 렌더·입력 꼬임 방지). `unset PYTMUX LC_PYTMUX` 로만 우회(강제 옵션 없음).
    if nesting_blocked():
        print("pytmux: 이미 pytmux 안에서 실행 중입니다(로컬/원격 중첩). 우회하려면 "
              "'unset PYTMUX LC_PYTMUX'.", file=sys.stderr)
        sys.exit(1)
    # §1.7 env 마커가 전파되지 않는 원격 경로(ssh 래퍼 우회·sshd AcceptEnv 부재) 대비
    # in-band 감지: 원격 로그인(SSH_*)에서만 단말에 XTVERSION 을 질의해, 호스트가
    # pytmux 로 응답하면 중첩으로 거부한다 — 중첩 TUI 가 떠서 crash-relaunch/재접속
    # 루프로 빠지는 것을 전송과 무관하게 차단(완화). 실제 터미널은 자기 이름으로
    # 응답해 조기 통과하므로 비중첩 원격 attach 의 지연은 RTT 수준.
    if (os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY")) \
            and host_terminal_is_pytmux():
        print("pytmux: 호스트 단말이 pytmux 입니다(원격 중첩 감지 — env 마커 없이 "
              "단말 질의로 확인). 이중 실행을 막습니다.", file=sys.stderr)
        sys.exit(1)
    # 서버 기동(없으면)과 textual 로드를 **겹쳐서** 체감 기동을 줄인다: 서버를 먼저
    # 띄워두고(분리 프로세스), 그 부팅(수백 ms)이 도는 동안 무거운 client(=textual)
    # 를 import 한 뒤 readiness 를 폴링한다. 직렬(기동 완료 후 import)보다 빠르다.
    need_spawn = not ipc.probe(sock_path)
    if need_spawn:
        proc.spawn_detached(proc.server_argv(sock_path))
    from .client import run_client   # 지연 import: 서버 부팅과 병렬로 textual 로드
    if need_spawn and not wait_server(sock_path):
        print("pytmux: 서버 기동 실패", file=sys.stderr)
        sys.exit(1)
    run_client(sock_path, None)


if __name__ == "__main__":
    main()
