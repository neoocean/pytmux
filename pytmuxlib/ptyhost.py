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

종료는 둘 중 하나다: ① 서버의 `shutdown` op(진짜 종료 — kill-server/SIGTERM), ② **고아
워치독**(`_orphan_watchdog`) — 소유 서버가 죽었는데 grace 안에 아무도 안 붙으면 스스로
패널을 닫고 내려간다(PTYHOST_ORPHAN_2026-07-24 R1). ②가 없던 시절엔 ①이 못 가는 모든
경로에서 host 가 자식 셸까지 안고 영구 잔존했다.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import os
import secrets
import sys
import traceback

from . import ipc, proc, pty_backend, ptyhostproto as proto
from .protocol import HANDSHAKE_MAX_FRAME, HANDSHAKE_TIMEOUT

# 인증 전 동시 연결 상한(PTYH-1). 메인 서버 serverio._MAX_PREAUTH_CONNS 와 같은 값·
# 같은 취지 — 정상 피어는 서버 하나뿐이라 128 은 사실상 무제한이고, 무인가 폭주만 캡된다.
_MAX_PREAUTH_CONNS = 128

# 서버 재연결 갭 동안 패널별로 버퍼링하는 미전송 출력 상한(초과분은 머리부터 버림).
_PENDING_MAX = 1 * 1024 * 1024

# 고아 워치독(PTYHOST_ORPHAN_2026-07-24 R1). host 는 detached 라 서버보다 오래 살고,
# 종전엔 종료 트리거가 서버의 `shutdown` op **하나뿐**이었다 — 그 프레임이 못 가는 모든
# 경로(서버 SIGKILL·fatal·작업관리자 강제종료·폴백 모드 종료·중복 host 경합)에서 host 가
# 자식 셸까지 안은 채 영구 잔존했다. host 의 계약은 "서버 **재시작**(≈1초 갭)을 견딘다"이지
# "서버 없이 세션을 보관한다"가 아니므로, **소유자가 죽었고 grace 안에 새 서버가 안 붙으면**
# 스스로 패널을 닫고 내려간다. 소유자가 **살아 있으면** 절대 자살하지 않는다(재연결 백오프
# 중인 서버의 세션을 파괴하지 않기 위해 — 판정은 pid 생존 하나로 단순하게 유지).
_WATCH_INTERVAL = 5.0
_ORPHAN_GRACE_DEFAULT = 60.0

# 연결 직후 '단발 probe 선언'(R2)을 기다리는 시간. 정상 서버는 곧바로 `owner` 를 보내
# 이 대기가 사실상 0이다. 아무 프레임도 안 오면 probe 아님으로 보고 채택한다.
_PROBE_DECL_TIMEOUT = 0.2


def orphan_grace() -> float:
    """소유자 사망 후 self-shutdown 까지의 유예(초). 0/음수면 워치독 비활성(탈출구).
    `PYTMUX_PTYHOST_GRACE` 로 조정한다(테스트는 짧게 준다)."""
    raw = (os.environ.get("PYTMUX_PTYHOST_GRACE") or "").strip()
    if not raw:
        return _ORPHAN_GRACE_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return _ORPHAN_GRACE_DEFAULT


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
        self._published_port: int | None = None            # portfile 에 게시한 포트
        self._preauth_conns = 0                            # 인증 중 연결 수(PTYH-1)
        # 고아 워치독(R1) 상태. _owner_pid = 현재/마지막 소유 서버 pid('owner' op 로 통지),
        # _prev_owner_pid = 그 직전 소유자(list_reply 로 보고 → 새 서버의 미상 패널 prune
        # 안전 게이트), _idle_since = 서버 연결이 없어진 시각(loop.time; None=연결 중).
        self._owner_pid: int | None = None
        self._prev_owner_pid: int | None = None
        self._idle_since: float | None = None
        self._watchdog: asyncio.Task | None = None
        self._published_pid: int | None = None             # pidfile 에 게시한 pid

    # ---- 연결 처리 ----
    async def _authenticate(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter) -> bool:
        """연결 피어를 인증한다(M1, docs/internal/SECURITY_REVIEW.md). 메인 채널과 동형:

        ① Unix 소켓이면 peer-UID == host UID 검증(심층 방어; TCP·미지원이면 None→통과).
        ② 토큰이 설정돼 있으면(프로덕션은 mgr 이 `--tokenfile` 로 항상 게시) 첫 프레임이
           유효 토큰을 실은 `auth` 여야 한다 — `hmac.compare_digest` 로 상수시간 비교.
        Windows 루프백 TCP 의 무인가 로컬 접속을 차단하는 핵심 게이트다(F1 회귀 봉쇄).

        ③ **인증 전 자원 상한**(PTYH-1, 보안검수 2026-07-17): 읽기에 타임아웃과 작은
           프레임 캡을 건다. 메인 서버는 이 둘 + 연결 캡을 2026-07-03(M2)·07-04(S1)에
           갖췄는데 pty-host 만 **셋 다 없이** 남아 있었다 — `read_frame` 이 16MiB 를
           하드코딩하고 타임아웃이 없어, 무인가 피어가 16MiB 를 광고한 뒤 아무것도 안
           보내면 그 연결이 **영원히** 버퍼를 문 채 산다. Windows 는 루프백 TCP 라
           같은 박스의 아무 로컬 사용자나 이걸 N개 열 수 있고, host 가 고갈되면 다음
           서버 재시작 때 재연결이 실패해 **세션 전체(살아있던 셸 전부)가 소멸**한다 —
           host 모드가 존재하는 이유 그 자체가 파괴된다.
        """
        sock = writer.get_extra_info("socket")
        puid = ipc.peer_uid(sock)
        if puid is not None and puid != os.getuid():
            return False
        if self._token is not None:
            try:
                f = await asyncio.wait_for(
                    proto.read_frame(reader, max_frame=HANDSHAKE_MAX_FRAME),
                    HANDSHAKE_TIMEOUT)
            except (asyncio.TimeoutError, OSError, ConnectionError):
                return False
            if not f or f[0] != "json" or f[1].get("op") != "auth":
                return False
            tok = f[1].get("token")
            if not isinstance(tok, str) or not hmac.compare_digest(tok, self._token):
                return False
        return True

    async def _handle_conn(self, reader: asyncio.StreamReader,
                           writer: asyncio.StreamWriter):
        # 인증 전 동시 연결 캡(PTYH-1) — 메인 서버 _MAX_PREAUTH_CONNS(S1)와 동형.
        # 각 미인증 연결이 최대 HANDSHAKE_TIMEOUT 동안 상주하므로, 캡이 없으면 무인가
        # 피어가 연결을 쌓아 accept 여력·핸들을 고갈시킨다(→ 서버 재시작 시 재연결
        # 실패 → 세션 전멸). 카운트는 **핸드셰이크 구간만** 감싼다(try/finally) —
        # 인증된 서버 연결의 수명은 세지 않는다.
        if self._preauth_conns >= _MAX_PREAUTH_CONNS:
            with contextlib.suppress(Exception):
                writer.close()
            return
        self._preauth_conns += 1
        try:
            await proto.write_frame(writer, proto.encode_json(
                {"op": "hello", "version": proto.PROTO_VERSION, "pid": os.getpid()}))
            # 인증을 **연결 채택 전에** 한다 — 무인가 접속이 현재 서버 연결을 대체하거나
            # (DoS) pending 출력을 가로채지(정보 노출) 못하게.
            ok = await self._authenticate(reader, writer)
        finally:
            self._preauth_conns -= 1
        if not ok:
            with contextlib.suppress(Exception):
                writer.close()
            return
        # 첫 제어 프레임으로 **단발 probe** 여부를 가른다(R2). probe 연결은 현재 서버
        # 연결을 대체하지 않는다 — 그러지 않으면 "이 host 를 회수해도 되나" 조회 한 번이
        # 살아있는 서버를 host 에서 떼어내 재연결 churn 을 만든다. 대기는 **짧게** 준다:
        # 정상 서버는 connect 직후 `owner` 를 보내 사실상 0이고, 아무것도 안 보내는
        # 피어(raw 테스트 클라)여도 채택·pending flush 가 이만큼만 늦는다(무기한 보류
        # 금지 — 갭 출력 flush 는 재연결의 핵심 계약이다).
        first = None
        with contextlib.suppress(asyncio.TimeoutError, OSError, ConnectionError):
            first = await asyncio.wait_for(proto.read_frame(reader),
                                           _PROBE_DECL_TIMEOUT)
        probe = bool(first and first[0] == "json"
                     and first[1].get("op") == "probe")
        if not probe:
            # 새 서버 연결이 들어오면 이전 연결을 대체한다(서버 재시작 모델 = 1 서버).
            old = self._writer
            self._writer = writer
            self._idle_since = None            # 소유자 있음 — 워치독 정지(R1)
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
            frame, first = first, None         # 이미 읽은 첫 프레임부터 처리
            while True:
                if frame is None:
                    frame = await proto.read_frame(reader)
                if frame is None:
                    break
                if frame[0] == "data":
                    if not probe:              # probe 연결은 자식 입력을 받지 않는다
                        self._on_input(frame[1], frame[2])
                else:
                    try:
                        await self._on_control(frame[1], writer)
                    except (KeyError, ValueError, TypeError):
                        # 손상 control 프레임(누락 pane/cols·비정수)이 이 연결 루프를
                        # 끊지 않게 드롭한다 — 피어는 인증된 로컬 서버라 공격 미도달,
                        # 견고성 차원(보안검수 2026-07-03 INFO).
                        pass
                frame = None                   # 다음 프레임을 읽는다
        finally:
            if self._writer is writer:
                self._writer = None
                self._idle_since = self.loop.time()   # 워치독 기산점(R1)
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
        elif op == "owner":
            # 소유 서버 pid 통지(R1). 인증 이후에만 도달하므로 무인가 위조는 불가.
            # 직전 소유자를 남겨 둔다 — 새 서버가 "내가 모르는 host 패널"을 prune 해도
            # 되는지(=직전 소유자가 죽었는지)를 list_reply 로 판정하게 한다(R5).
            pid = msg.get("pid")
            if isinstance(pid, int) and pid > 0:
                if self._owner_pid is not None and self._owner_pid != pid:
                    self._prev_owner_pid = self._owner_pid
                elif self._owner_pid is None:
                    self._prev_owner_pid = None
                self._owner_pid = pid
        elif op == "list":
            # prev_owner_alive: 직전 소유자 생존 여부(모르면 None). 새 서버는 이게
            # False 일 때만 미상 패널을 prune 한다(살아있는 다른 서버의 셸 파괴 금지).
            prev = self._prev_owner_pid
            # Windows 의 is_alive 는 tasklist 왕복(~100ms)이라 executor 로 뺀다 —
            # 재attach 임계경로에서 host 이벤트 루프(전 패널 I/O)를 막지 않게.
            prev_alive = None if prev is None else bool(
                await self.loop.run_in_executor(None, proc.is_alive, prev))
            await proto.write_frame(writer, proto.encode_json({
                "op": "list_reply",
                "prev_owner_pid": prev,
                "prev_owner_alive": prev_alive,
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
        # M1: reap(block=True)를 이벤트 루프 스레드(여기 — backend 가
        # call_soon_threadsafe 로 부른다)에서 직접 호출하면, 자식이 EOF 직후 즉시
        # 안 끝날 때(conhost 정리 지연 등) 루프가 막혀 **전 패널 I/O 가 정지**한다.
        # 블로킹 reap 을 별도 코루틴+executor 로 오프로드해 루프를 비운다.
        if self.panes.get(pane_id) is not None:
            self.loop.create_task(self._finish_eof(pane_id))

    async def _finish_eof(self, pane_id: int):
        pe = self.panes.get(pane_id)
        if pe is None:
            return
        status = None
        if pe.pty is not None:
            try:
                status = await self.loop.run_in_executor(
                    None, lambda p=pe.pty: p.reap(block=True))
            except Exception:
                status = None
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

    # ---- 고아 워치독(R1) ----
    async def _orphan_watchdog(self):
        """소유 서버가 죽었는데 grace 안에 새 서버가 안 붙으면 스스로 내려간다.

        판정은 **서버 연결이 없는 동안에만** 돈다(연결 중이면 즉시 리셋). 소유자가
        살아 있으면(재연결 백오프·일시 웨지) 절대 죽지 않는다 — 살아있는 세션을 host 가
        임의로 파괴하는 일이 없어야 한다. 소유자를 한 번도 못 받은 host(=서버가 연결
        전에 죽었거나 폴백으로 돌아선 경우)도 grace 후 회수한다(ensure_connected 의
        연결 예산이 6초라 grace 60초는 넉넉한 마진)."""
        grace = orphan_grace()
        if grace <= 0:
            return
        # 폴 간격은 grace 에 종속(짧은 grace = 테스트). 프로덕션 기본(60s)에선 5s.
        interval = min(_WATCH_INTERVAL, max(0.05, grace / 4.0))
        strikes = 0
        while True:
            await asyncio.sleep(interval)
            idle = self._idle_since
            if idle is None or self._writer is not None:
                strikes = 0
                continue                       # 서버가 붙어 있다 — 대상 아님
            if self.loop.time() - idle < grace:
                continue
            owner = self._owner_pid
            if owner is not None:
                alive = await self.loop.run_in_executor(
                    None, proc.is_alive, owner)
                if alive:
                    strikes = 0
                    continue                   # 소유자 생존 → 재연결을 기다린다
                # 사망 판정은 **연속 2회**여야 한다: Windows 의 is_alive 는 tasklist
                # 왕복이라 타임아웃/일시 실패가 곧 False 다(proc._win_is_alive). 한 번의
                # 오탐으로 **살아있는 세션의 셸을 전부 죽이는** 것은 이 워치독이 막으려는
                # 사고보다 훨씬 나쁘다 — 한 인터벌 늦더라도 두 번 확인한다.
                strikes += 1
                if strikes < 2:
                    continue
            # 고아 확정: 패널(자식 셸)까지 정리하고 serve 를 끝낸다.
            sys.stderr.write(
                f"pytmux ptyhost: 소유 서버(pid={owner}) 부재 {grace:.0f}s 초과 "
                f"— 패널 {len(self.panes)}개를 닫고 종료합니다\n")
            for pid in list(self.panes):
                self._close_pane(pid)
            if self._stop is not None:
                self._stop.set()
            return

    # ---- 데몬 본체 ----
    async def serve(self, endpoint: str, portfile: str | None = None,
                    tokenfile: str | None = None, pidfile: str | None = None):
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
                    self._published_port = port
        # 내 pid 를 게시한다(R3): 다음 서버의 "host 가 이미 떠 있나" 판정을 **파일 존재**
        # (죽은 host 의 잔재도 있다고 오판 → prespawn 스킵)에서 **pid 생존**으로 올리고,
        # `pytmux kill-server` 가 서버 부재 시 잔존 host 를 겨냥할 수 있게 한다(R4).
        if pidfile:
            with contextlib.suppress(Exception):
                with ipc.open_private(pidfile) as f:
                    f.write(str(os.getpid()))
                self._published_pid = os.getpid()
        # 소유 서버가 아직 안 붙은 상태를 워치독 기산점으로 둔다 — 서버가 연결 전에
        # 죽거나 폴백으로 돌아서면(P3) 이 host 는 아무도 안 쓰므로 grace 후 회수된다.
        self._idle_since = self.loop.time()
        self._watchdog = self.loop.create_task(self._orphan_watchdog())
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
            if self._watchdog is not None:
                self._watchdog.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._watchdog
                self._watchdog = None
            self._server.close()
            if self._writer is not None:    # 연결된 서버에 EOF(크래시/종료 알림)
                with contextlib.suppress(Exception):
                    self._writer.close()
            # 질서 종료 시 내가 게시한 portfile/tokenfile 을 지운다(내용이 내 것일
            # 때만 — 이미 새 host 가 다시 게시했으면 건드리지 않는다). stale 포트
            # 파일이 남으면 다음 서버 기동의 prespawn_host 가 host 가 있는 줄 알고
            # 건너뛰고, ensure_connected 의 첫 재연결 시도가 죽은 루프백 포트
            # connect 타임아웃(Windows 는 즉답 거절이 없다 — ipc 주석)을 태워
            # 콜드 스타트를 늦춘다. best-effort(os._exit 크래시 종료는 못 지움 —
            # 그 경우는 ensure_connected 폴링이 흡수).
            self._cleanup_published(portfile, tokenfile, pidfile)
        for pid in list(self.panes):        # 종료 시 남은 패널 정리(고아 방지)
            self._close_pane(pid)

    def _cleanup_published(self, portfile: str | None,
                           tokenfile: str | None,
                           pidfile: str | None = None) -> None:
        for path, mine in ((portfile, self._published_port),
                           (tokenfile, self._token),
                           (pidfile, self._published_pid)):
            if not path or mine is None:
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    if f.read().strip() != str(mine):
                        continue           # 새 host 의 게시물 — 보존
                os.unlink(path)
            except OSError:
                pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="pytmux PTY host 데몬")
    ap.add_argument("--endpoint", required=True,
                    help="리슨 엔드포인트(unix 경로 또는 tcp:host:port)")
    ap.add_argument("--portfile", default=None,
                    help="tcp 에페메럴 포트일 때 실제 포트를 적을 파일")
    ap.add_argument("--tokenfile", default=None,
                    help="연결 인증 토큰을 게시할 0600 파일(M1)")
    ap.add_argument("--pidfile", default=None,
                    help="내 pid 를 게시할 파일(중복 host 방지·고아 회수, R3/R4)")
    args = ap.parse_args(argv)

    # Windows(host 모드 기본)에선 패널 spawn 이 이 프로세스에서 일어난다. ConPTY 워밍
    # 풀을 host 에서도 켜, 비-UTF-8 OEM(cp949/932) 시스템의 콘솔 CP UTF-8 강제(chcp
    # helper, force_utf8_codepage)를 **풀 채움 시점(백그라운드)**에 미리 치른다 —
    # 안 켜면 _spawn 이 매 패널마다 그 동기 helper(수십 ms~1.5s)를 host 이벤트 루프에서
    # 기다려 전 패널 출력이 멈춘다(코드검수 2026-07-10 M-2: run_server 만 enable_pool 을
    # 불러 host 프로세스엔 미적용이던 갭). 비-Win/미지원/pywinpty 강제 시 enable_pool 은
    # 스스로 no-op.
    with contextlib.suppress(Exception):
        from . import conpty
        conpty.enable_pool()

    async def _run():
        host = PtyHost()
        await host.serve(args.endpoint, portfile=args.portfile,
                         tokenfile=args.tokenfile, pidfile=args.pidfile)

    # `asyncio.run` 대신 **수동 루프**를 돌린다. asyncio.run 은 코루틴 완료 후 graceful
    # cleanup(_cancel_all_tasks + shutdown_asyncgens + **shutdown_default_executor**)을
    # 하는데, 그중 shutdown_default_executor 가 기본 ThreadPoolExecutor 스레드를 **join**
    # 한다. host 는 `_on_eof` 에서 `reap(block=True)` 를 기본 executor 로 오프로드하므로
    # (§ _reap_offload), 종료 시점에 그 블로킹 reap 이 in-flight 면 Windows 에서 느리게
    # 죽는 ConPTY 자식을 기다리는 스레드 join 이 20s+ 걸려, 아래 os._exit 도달이 지연되고
    # host 가 고아로 관측됐다(CI flaky test_real_host_shutdown_…, windows-3.12 단일셀).
    # serve() 코루틴이 반환하면 질서있는 정리(패널 stop_reader/close·서버/writer close)는
    # **이미 끝났으므로**, 그 graceful cleanup(executor join)을 건너뛰고 즉시 강제 종료해
    # 결정적으로 내린다. (main 은 오직 `python -m pytmuxlib.ptyhost` 분리 프로세스로만
    # 실행돼 in-process 호출이 없으므로 os._exit 가 안전하다 — 인프로세스 host 는 serve()
    # 를 직접 await 하고 이 main 을 안 탄다.)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    code = 0
    try:
        loop.run_until_complete(_run())
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()            # serve 중 오류는 남기되 종료는 아래에서 강제
        code = 1
    # 풀에 남은 미사용 의사콘솔(OpenConsole 호스트)을 닫아 고아를 막는다(run_server 대칭).
    with contextlib.suppress(Exception):
        from . import conpty
        conpty._pool_drain()
    with contextlib.suppress(Exception):
        sys.stdout.flush()
        sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
