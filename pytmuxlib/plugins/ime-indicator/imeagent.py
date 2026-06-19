"""ime-indicator 로컬 에이전트 — plain ssh 원격에서 IME 한/영 배지를 **로컬 머신**의
한/영 상태에 맞추기 위한 전송로(§9.1 전송로 ② = ssh -R 역포워드 unix 소켓).

배경: ime-indicator 는 완전 클라이언트측이라 Textual 클라가 도는 머신의 OS 키보드를
본다. `ssh remote` 로 들어가 원격 박스에서 pytmux 를 통째로 실행하면 클라가 **원격**
키보드를 보게 돼, 사용자가 실제 타이핑하는 **로컬** 한/영과 무관해진다(원격 macOS 면
EN 등으로 굳음). 그래서 사용자 **로컬 머신**에서 이 에이전트를 띄워 한/영 상태를
unix 소켓으로 게시하고, `ssh -R <원격소켓>:<로컬소켓>` 로 역포워드하면 원격 pytmux
클라가 `PYTMUX_IME_SOCK`(=원격 끝점)에 붙어 그 상태를 따라간다.

전송로 결정(사용자 2026-06-19): ①제어 tty 이스케이프 주입(Claude TUI 출력과 충돌·
양방향 파싱 취약)·③환경 에이전트 데몬(가장 무겁고 env-var 면 라이브 갱신 불가)을 제치고
②를 채택 — 이스케이프 충돌 없는 **구조적 채널**이라 견고하고 프레임 프로토콜이 mock
가능해 헤드리스 회귀 테스트가 된다. 상세 docs/internal/IMPROVEMENT_OPPORTUNITIES.md §9.

와이어 포맷: oskbd 감시 헬퍼와 동일하게 **개행으로 끝나는 소스 ID 한 줄**
(예 `com.apple.inputmethod.Korean.2SetKorean\n`). 클라는 `oskbd.is_korean` 으로 한/영을
판정하므로 별도 인코딩이 필요 없다. 새 클라가 붙으면 현재 상태를 **즉시 1줄** 보낸다.

사용법(로컬 머신에서):
    python <pytmux>/pytmuxlib/plugins/ime-indicator/imeagent.py --sock /tmp/pytmux-ime.sock
    ssh -R /tmp/pytmux-ime.sock:/tmp/pytmux-ime.sock remote   # StreamLocalBindUnlink yes
    # 원격에서: export PYTMUX_IME_SOCK=/tmp/pytmux-ime.sock && pytmux

플랫폼: macOS 는 oskbd 감시 헬퍼(진짜 CFRunLoop)로 이벤트 구동, 그 외(Windows 등)는
current_source_id 를 poll_interval 마다 폴링한다. 베스트에포트 — 실패는 조용히 폴백."""
from __future__ import annotations

import os
import select
import socket
import sys

# 같은 디렉토리의 oskbd 를 가져온다. __main__ 으로 직접 실행되면 sys.path[0] 이 이
# 디렉토리라 `import oskbd` 가 형제 모듈을 찾고, 패키지로 import 되면 상대 import 를 쓴다.
try:
    from . import oskbd
except ImportError:
    import oskbd  # type: ignore


DEFAULT_SOCK = "/tmp/pytmux-ime.sock"


def _emit_line(sid: str | None) -> bytes:
    return ((sid or "") + "\n").encode("utf-8", "ignore")


def serve(sock_path: str, poll_interval: float = 0.05) -> int:
    """unix 소켓에 바인드해 붙는 클라마다 현재 IME 소스 ID 를 흘린다(변경 시 + 접속 시).
    SIGINT/소켓 오류까지 영구 루프. 반환=종료 코드(베스트에포트, 거의 도달 안 함)."""
    try:
        os.unlink(sock_path)             # stale 소켓 제거(이전 에이전트 잔재)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(sock_path)
    except OSError as e:
        sys.stderr.write(f"imeagent: bind 실패 {sock_path}: {e}\n")
        return 1
    srv.listen(16)
    srv.setblocking(False)

    clients: list[socket.socket] = []
    watch = oskbd.spawn_watcher()        # macOS: 자식 헬퍼 / 그 외: None(폴링)
    wbuf = b""
    cur = oskbd.current_source_id() or ""   # 새 프로세스 첫 질의는 fresh

    def _drop(c: socket.socket) -> None:
        if c in clients:
            clients.remove(c)
        try:
            c.close()
        except Exception:
            pass

    def _broadcast(sid: str) -> None:
        data = _emit_line(sid)
        for c in list(clients):
            try:
                c.sendall(data)
            except Exception:
                _drop(c)

    try:
        while True:
            rset: list = [srv] + clients
            if watch is not None and watch.poll() is None:
                rset.append(watch.stdout)
            # macOS(이벤트 구동)는 길게, 폴링(Windows 등)은 짧게 깨어난다.
            timeout = 1.0 if watch is not None else poll_interval
            try:
                r, _, _ = select.select(rset, [], [], timeout)
            except (OSError, ValueError):
                # 닫힌 fd 가 섞였을 수 있다 — 죽은 클라 청소 후 계속.
                clients[:] = [c for c in clients if c.fileno() >= 0]
                continue

            for s in r:
                if s is srv:
                    try:
                        conn, _ = srv.accept()
                        conn.setblocking(False)
                        clients.append(conn)
                        # 접속 즉시 현재 상태 1줄(실측 부재면 빈 줄 — 클라는 무시).
                        if cur:
                            try:
                                conn.sendall(_emit_line(cur))
                            except Exception:
                                _drop(conn)
                    except Exception:
                        pass
                elif watch is not None and s is watch.stdout:
                    sid, wbuf = oskbd.read_latest(watch, wbuf)
                    if sid and sid != cur:
                        cur = sid
                        _broadcast(cur)
                else:
                    # 클라 소켓이 readable = EOF(접속 종료) 또는 미사용 입력 → 정리.
                    try:
                        if not s.recv(4096):
                            _drop(s)
                    except BlockingIOError:
                        pass
                    except Exception:
                        _drop(s)

            # 폴링 경로(Windows 등): 타임아웃마다 현재 상태를 질의해 변경 시 방송.
            if watch is None:
                sid = oskbd.current_source_id()
                if sid and sid != cur:
                    cur = sid
                    _broadcast(cur)
    except KeyboardInterrupt:
        return 0
    finally:
        for c in list(clients):
            _drop(c)
        try:
            srv.close()
        finally:
            try:
                os.unlink(sock_path)
            except OSError:
                pass


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="imeagent",
        description="ime-indicator 로컬 IME 에이전트(ssh -R 역포워드 unix 소켓, §9.1)")
    ap.add_argument("--sock", default=os.environ.get("PYTMUX_IME_SOCK")
                    or DEFAULT_SOCK,
                    help=f"게시할 unix 소켓 경로(기본 {DEFAULT_SOCK} / $PYTMUX_IME_SOCK)")
    ap.add_argument("--poll-interval", type=float, default=0.05,
                    help="폴링 경로(Windows 등) 질의 간격 초(기본 0.05)")
    args = ap.parse_args(argv)
    return serve(args.sock, poll_interval=args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
