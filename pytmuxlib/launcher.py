"""데몬화 · 런처 · 외부 제어 CLI."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time

from .client import run_client
from .protocol import default_socket_path
from .server import run_server


def daemonize():
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    if devnull > 2:
        os.close(devnull)


def can_connect(sock_path: str) -> bool:
    if not os.path.exists(sock_path):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(sock_path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def ensure_server(sock_path: str):
    if can_connect(sock_path):
        return
    pid = os.fork()
    if pid == 0:
        daemonize()
        run_server(sock_path)
        os._exit(0)
    # 부모: 중간 자식을 회수하고 소켓이 뜰 때까지 대기
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    for _ in range(200):
        if can_connect(sock_path):
            return
        time.sleep(0.02)
    print("pytmux: 서버 기동 실패", file=sys.stderr)
    sys.exit(1)


def control_request(sock_path: str, obj: dict):
    if not can_connect(sock_path):
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)
    data = json.dumps(obj).encode()
    s.sendall(len(data).to_bytes(4, "big") + data)
    try:
        header = _recvn(s, 4)
        if not header:
            return None
        n = int.from_bytes(header, "big")
        payload = _recvn(s, n)
        return json.loads(payload.decode())
    finally:
        s.close()


def _recvn(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


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
    p_srv = sub.add_parser("server", help="(내부) 서버를 전경 실행")
    p_srv.add_argument("--foreground", action="store_true")
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

    sock_path = args.socket or default_socket_path()

    if args.command == "server":
        run_server(sock_path)
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
    ensure_server(sock_path)
    run_client(sock_path, None)


if __name__ == "__main__":
    main()
