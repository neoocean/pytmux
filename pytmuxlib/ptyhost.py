"""PTY host 데몬 — ConPTY(세션) 소유를 서버 밖으로 분리하는 장수명 프로세스.

Windows 세션유지 재시작 옵션 C(`WINDOWS_RESTART_SCENARIO.md` §3). host 가 PTY(자식 셸)를
**소유**하고, 서버는 `ptyhostproto` 로 host 와 통신해 셸을 구동한다. **서버가 재시작해도
host 는 살아 있어** 새 서버가 재연결하면 세션이 그대로 이어진다 — POSIX 의 execv+fd 상속이
Windows 에서 불가한 근본 한계(HPCON 비이관)를 'HPCON 을 host 안에 상주'시켜 우회한다.

이 모듈은 **백엔드 중립**이다: `pty_backend.spawn` 을 그대로 쓰므로 Windows 에선 ConPTY
(`_OwnedConPty`), POSIX 에선 `_UnixPty` 를 소유한다. 덕분에 host↔서버 프로토콜·재연결
메커니즘 전체를 **POSIX 에서 테스트**할 수 있다(ConPTY primitive 자체는 이미 라이브 검증).

수명: serve(endpoint) → 서버 1개 연결 수락(새 연결이 옛 연결 대체) → spawn/data/제어 처리.
연결이 끊긴 동안(서버 재시작 ~1s) 자식 출력은 패널별 `pending` 링에 버퍼링했다가, 재연결
시 flush 해 **재시작 갭의 출력 손실을 막는다**(서버는 직전 스냅샷에서 이어 feed).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import os
import secrets
import sys

from . import ipc, pty_backend, ptyhostproto as proto

# 서버 재연결 갭 동안 패널별로 버퍼링하는 미전송 출력 상한(초과분은 머리부터 버림).
_PENDING_MAX = 1 * 1024 * 1024


class _PaneEntry:
    __slots__ = ("pane_id", "pty", "cols", "rows", "pid",
                 "pending", "alive", "exit_status")

    def __init__(self, pane_id: int, pty, cols: int, rows: int, pid: int):
        self.pane_id = pane_id
        self.pty = pty
        self.cols = cols
        self.rows = rows
        self.pid = pid
        self.pending = bytearray()     # 서버 미연결 중 누적된 자식 출력
        self.alive = True
        self.exit_status = None


class PtyHost:
    """패널(PTY)들을 소유하고 단일 서버 연결을 상대하는 host. 한 endpoint 에 하나."""

    def __init__(self, loop=None):
        self.loop = loop or asyncio.get_event_loop()
        self.panes: dict[int, _PaneEntry] = {}
        self._writer: asyncio.StreamWriter | None = None   # 현재 연결된 서버
        self._server = None
        self._stop: asyncio.Event | None = None            # shutdown op → serve 종료
        self._token: str | None = None                     # 연결 인증 토큰(M1)

    # ---- 연결 처리 ----
    async def _authenticate(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter) -> bool:
        """연결 피어를 인증한다(M1, docs/internal/SECURITY_REVIEW.md). 메인 채널과 동형:

        ① Unix 소켓이면 peer-UID == host UID 검증(심층 방어; TCP·미지원이면 None→통과).
        ② 토큰이 설정돼 있으면(프로덕션은 mgr 이 `--tokenfile` 로 항상 게시) 첫 프레임이
           유효 토큰을 실은 `auth` 여야 한다 — `hmac.compare_digest` 로 상수시간 비교.
        Windows 루프백 TCP 의 무인가 로컬 접속을 차단하는 핵심 게이트다(F1 회귀 봉쇄).
        """
        sock = writer.get_extra_info("socket")
        puid = ipc.peer_uid(sock)
        if puid is not None and puid != os.getuid():
            return False
        if self._token is not None:
            f = await proto.read_frame(reader)
            if not f or f[0] != "json" or f[1].get("op") != "auth":
                return False
            tok = f[1].get("token")
            if not isinstance(tok, str) or not hmac.compare_digest(tok, self._token):
                return False
        return True

    async def _handle_conn(self, reader: asyncio.StreamReader,
                           writer: asyncio.StreamWriter):
        await proto.write_frame(writer, proto.encode_json(
            {"op": "hello", "version": proto.PROTO_VERSION, "pid": os.getpid()}))
        # 인증을 **연결 채택 전에** 한다 — 무인가 접속이 현재 서버 연결을 대체하거나
        # (DoS) pending 출력을 가로채지(정보 노출) 못하게.
        if not await self._authenticate(reader, writer):
            with contextlib.suppress(Exception):
                writer.close()
            return
        # 새 서버 연결이 들어오면 이전 연결을 대체한다(서버 재시작 모델 = 1 서버).
        old = self._writer
        self._writer = writer
        if old is not None and old is not writer:
            with contextlib.suppress(Exception):
                old.close()
        # 재연결: 미전송 버퍼를 패널별로 flush 해 갭 출력을 잇는다.
        for pe in self.panes.values():
            if pe.pending:
                await proto.write_frame(
                    writer, proto.encode_data(pe.pane_id, bytes(pe.pending)))
                pe.pending.clear()
        try:
            while True:
                frame = await proto.read_frame(reader)
                if frame is None:
                    break
                if frame[0] == "data":
                    self._on_input(frame[1], frame[2])
                else:
                    await self._on_control(frame[1], writer)
        finally:
            if self._writer is writer:
                self._writer = None
            with contextlib.suppress(Exception):
                writer.close()

    def _on_input(self, pane_id: int, data: bytes):
        pe = self.panes.get(pane_id)
        if pe is not None and pe.pty is not None:
            with contextlib.suppress(OSError):
                pe.pty.write(data)

    async def _on_control(self, msg: dict, writer: asyncio.StreamWriter):
        op = msg.get("op")
        if op == "spawn":
            self._spawn(msg)
            pe = self.panes.get(int(msg["pane"]))
            await proto.write_frame(writer, proto.encode_json(
                {"op": "spawned", "pane": int(msg["pane"]),
                 "pid": pe.pid if pe else -1}))
        elif op == "resize":
            pe = self.panes.get(int(msg["pane"]))
            if pe is not None and pe.pty is not None:
                pe.cols, pe.rows = int(msg["cols"]), int(msg["rows"])
                with contextlib.suppress(OSError):
                    pe.pty.set_winsize(pe.rows, pe.cols)
        elif op in ("pause", "resume"):
            pe = self.panes.get(int(msg["pane"]))
            if pe is not None and pe.pty is not None:
                with contextlib.suppress(OSError, AttributeError):
                    (pe.pty.pause_reader if op == "pause"
                     else pe.pty.resume_reader)()
        elif op == "signal":
            self._signal(int(msg["pane"]), msg.get("how", "terminate"))
        elif op == "close":
            self._close_pane(int(msg["pane"]))
        elif op == "list":
            await proto.write_frame(writer, proto.encode_json({
                "op": "list_reply",
                "panes": [{"pane": pe.pane_id, "pid": pe.pid,
                           "cols": pe.cols, "rows": pe.rows, "alive": pe.alive}
                          for pe in self.panes.values()]}))
        elif op == "ping":
            await proto.write_frame(writer, proto.encode_json({"op": "pong"}))
        elif op == "shutdown":
            # 서버의 '진짜' 종료(kill-server·SIGTERM — 재시작 아님): 모든 자식 셸을
            # 죽이고 host 도 내려간다(고아 OpenConsole/셸 방지). 재시작 경로는 이 op 를
            # 보내지 않으므로(연결만 끊음) host 가 살아 세션을 보존한다.
            for pid in list(self.panes):
                self._close_pane(pid)
            if self._stop is not None:
                self._stop.set()

    # ---- 패널 수명 ----
    def _spawn(self, msg: dict):
        pane_id = int(msg["pane"])
        if pane_id in self.panes:
            return                       # 멱등(중복 spawn 무시)
        cols, rows = int(msg.get("cols", 80)), int(msg.get("rows", 24))
        argv = list(msg.get("argv") or [])
        if not argv:
            return
        pty = pty_backend.spawn(argv, cols=cols, rows=rows,
                                cwd=msg.get("cwd"), env=msg.get("env"))
        pe = _PaneEntry(pane_id, pty, cols, rows, getattr(pty, "pid", -1))
        self.panes[pane_id] = pe
        pty.start_reader(
            self.loop,
            lambda d, pid=pane_id: self._on_output(pid, d),
            lambda pid=pane_id: self._on_eof(pid))

    def _on_output(self, pane_id: int, data: bytes):
        """자식 출력: 서버 연결돼 있으면 'D' 프레임 즉시 전송, 아니면 pending 에 버퍼링."""
        pe = self.panes.get(pane_id)
        if pe is None:
            return
        w = self._writer
        if w is not None:
            # 핫패스: drain 안 하고 transport 버퍼에 흘린다(백프레셔는 pause/resume).
            with contextlib.suppress(Exception):
                w.write(proto.encode_data(pane_id, data))
            return
        pe.pending += data
        if len(pe.pending) > _PENDING_MAX:
            del pe.pending[:len(pe.pending) - _PENDING_MAX]

    def _on_eof(self, pane_id: int):
        pe = self.panes.get(pane_id)
        if pe is None:
            return
        status = None
        if pe.pty is not None:
            with contextlib.suppress(Exception):
                status = pe.pty.reap(block=True)
        pe.alive = False
        pe.exit_status = status
        w = self._writer
        if w is not None:
            with contextlib.suppress(Exception):
                w.write(proto.encode_json(
                    {"op": "exit", "pane": pane_id, "status": status}))
        self._close_pane(pane_id, reaped=True)

    def _signal(self, pane_id: int, how: str):
        pe = self.panes.get(pane_id)
        if pe is None or pe.pty is None:
            return
        with contextlib.suppress(OSError):
            (pe.pty.kill if how == "kill" else pe.pty.terminate)()

    def _close_pane(self, pane_id: int, *, reaped: bool = False):
        pe = self.panes.pop(pane_id, None)
        if pe is None or pe.pty is None:
            return
        with contextlib.suppress(OSError, Exception):
            pe.pty.stop_reader()
        with contextlib.suppress(OSError, Exception):
            pe.pty.close()
        if not reaped:
            with contextlib.suppress(Exception):
                pe.pty.reap(block=False)

    # ---- 데몬 본체 ----
    async def serve(self, endpoint: str, portfile: str | None = None,
                    tokenfile: str | None = None):
        self._stop = asyncio.Event()
        # 토큰을 **listen 전에** 0600 으로 게시한다(메인 서버와 동일 순서) — 서버가
        # 소켓/포트파일을 보고 연결할 즈음엔 토큰이 이미 존재한다(M1).
        if tokenfile:
            self._token = secrets.token_hex(32)
            with contextlib.suppress(Exception):
                with ipc.open_private(tokenfile) as f:
                    f.write(self._token)
        kind = ipc.parse_endpoint(endpoint)
        if kind[0] == "unix":
            with contextlib.suppress(FileNotFoundError):
                os.unlink(kind[1])
            self._server = await asyncio.start_unix_server(
                self._handle_conn, path=kind[1])
            with contextlib.suppress(OSError):   # 메인 서버와 동형: 소켓 0600 으로 좁힘
                os.chmod(kind[1], 0o600)
        else:
            self._server = await asyncio.start_server(
                self._handle_conn, host=kind[1], port=kind[2])
            # 에페메럴 포트(0)면 실제 바인드 포트를 portfile 에 적어 서버가 연결하게 한다.
            if portfile:
                with contextlib.suppress(Exception):
                    port = self._server.sockets[0].getsockname()[1]
                    with ipc.open_private(portfile) as f:
                        f.write(str(port))
        # serve_forever 대신 stop 이벤트 대기 — shutdown op 로 질서있게 내려간다.
        # `async with server` 는 쓰지 않는다(__aexit__ 의 wait_closed 가 열린 연결을
        # 기다려 hang). stop 시 서버와 현재 연결을 명시적으로 닫는다.
        serving = asyncio.ensure_future(self._server.serve_forever())
        try:
            await self._stop.wait()
        finally:
            serving.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await serving
            self._server.close()
            if self._writer is not None:    # 연결된 서버에 EOF(크래시/종료 알림)
                with contextlib.suppress(Exception):
                    self._writer.close()
        for pid in list(self.panes):        # 종료 시 남은 패널 정리(고아 방지)
            self._close_pane(pid)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="pytmux PTY host 데몬")
    ap.add_argument("--endpoint", required=True,
                    help="리슨 엔드포인트(unix 경로 또는 tcp:host:port)")
    ap.add_argument("--portfile", default=None,
                    help="tcp 에페메럴 포트일 때 실제 포트를 적을 파일")
    ap.add_argument("--tokenfile", default=None,
                    help="연결 인증 토큰을 게시할 0600 파일(M1)")
    args = ap.parse_args(argv)

    async def _run():
        host = PtyHost()
        await host.serve(args.endpoint, portfile=args.portfile,
                         tokenfile=args.tokenfile)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
