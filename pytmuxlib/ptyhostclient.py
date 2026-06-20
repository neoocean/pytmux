"""서버측 PTY host 클라이언트 + 원격 PtyProcess 프록시 — Windows 세션유지 재시작 옵션 C P3.

서버는 `PtyHostClient` 로 host(`ptyhost.PtyHost`)에 연결해 패널을 구동한다. 각 패널은
`_RemotePtyProcess`(= `pty_backend.PtyProcess` 표면 구현)로 표현되며, 모든 호출을 host RPC
로 포워딩한다. 서버는 패널을 **pane_id(서버 할당)**로 참조하므로, host 의 실제 자식 pid 와
무관하게 서버 재시작을 가로질러 안정적 식별이 가능하다(재연결 후 `list_panes` 로 재바인딩).

연결은 단일 멀티플렉싱 스트림이다: host→서버 출력('D' 프레임)을 `_read_loop` 가 디먹스해
패널별 on_data 콜백으로 dispatch 하고, 제어/이벤트('J' JSON)는 spawned/exit/list_reply/pong
으로 처리한다. 콜백은 **이벤트 루프 스레드**에서 호출된다(_read_loop 가 asyncio 태스크).
"""
from __future__ import annotations

import asyncio
import contextlib
import traceback

from . import ipc, pty_backend, ptyhostproto as proto


class PtyHostError(Exception):
    pass


class PtyHostClient:
    """host 와의 단일 연결을 관리한다. 패널 다수를 pane_id 로 멀티플렉싱."""

    def __init__(self, loop=None):
        self.loop = loop or asyncio.get_event_loop()
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.host_pid = -1
        self._cb: dict[int, tuple] = {}          # pane_id -> (on_data, on_eof)
        self._pid: dict[int, int] = {}
        self._exit: dict[int, object] = {}
        self._alive: set[int] = set()
        self._list_waiters: list[asyncio.Future] = []
        self._read_task: asyncio.Task | None = None
        self._on_lost = None                     # 연결 끊김 콜백(P5/P6 재연결)

    async def connect(self, endpoint: str, token: str | None = None):
        kind = ipc.parse_endpoint(endpoint)
        if kind[0] == "unix":
            r, w = await asyncio.open_unix_connection(kind[1])
        else:
            r, w = await asyncio.open_connection(kind[1], kind[2])
        self.reader, self.writer = r, w
        f = await proto.read_frame(r)
        if not f or f[0] != "json" or f[1].get("op") != "hello":
            raise PtyHostError(f"host hello 미수신: {f!r}")
        self.host_pid = f[1].get("pid", -1)
        # 인증 토큰을 **첫 프레임**으로 보낸다(M1) — host 가 채택·구동 전에 검증한다.
        if token is not None:
            ok = await proto.write_frame(
                w, proto.encode_json({"op": "auth", "token": token}))
            if not ok:
                raise PtyHostError("host 인증 프레임 전송 실패")
        self._read_task = self.loop.create_task(self._read_loop())

    async def _read_loop(self):
        try:
            while True:
                f = await proto.read_frame(self.reader)
                if f is None:
                    break
                # H2: 프레임 처리(패널 콜백/JSON 디스패치) 예외를 **프레임 단위로**
                # 격리한다. 종전엔 한 패널의 콜백/렌더 버그가 이 루프를 끊어
                # _handle_lost → host 는 멀쩡한데 전 패널 연결이 죽은 것으로 오인
                # (+ 무로깅 pass 라 추적 불가)하던 증폭을 막는다. 진짜 단절 신호는
                # read_frame→None(아래 break) 와 read 자체의 예외(바깥 except)뿐이다.
                try:
                    if f[0] == "data":
                        cb = self._cb.get(f[1])
                        if cb and cb[0]:
                            cb[0](f[2])
                    else:
                        self._dispatch_json(f[1])
                except Exception:
                    traceback.print_exc()      # 콜백 버그 가시화(무음 pass 금지)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self._handle_lost()

    def _dispatch_json(self, msg: dict):
        op = msg.get("op")
        if op == "spawned":
            pane = int(msg["pane"])
            self._pid[pane] = int(msg.get("pid", -1))
            self._alive.add(pane)
        elif op == "exit":
            pane = int(msg["pane"])
            self._exit[pane] = msg.get("status")
            self._alive.discard(pane)
            cb = self._cb.get(pane)
            if cb and cb[1]:
                cb[1]()
        elif op == "list_reply":
            for fut in self._list_waiters:
                if not fut.done():
                    fut.set_result(msg.get("panes", []))
            self._list_waiters.clear()

    def _handle_lost(self):
        for fut in self._list_waiters:
            if not fut.done():
                fut.set_exception(PtyHostError("host 연결 끊김"))
        self._list_waiters.clear()
        if self._on_lost is not None:
            with contextlib.suppress(Exception):
                self._on_lost()

    # ---- 송신(전부 fire-and-forget; spawn 회신 pid 는 비동기로 도착) ----
    def _send(self, raw: bytes):
        if self.writer is not None:
            with contextlib.suppress(Exception):
                self.writer.write(raw)

    def spawn(self, pane_id, argv, cols, rows, cwd=None, env=None):
        self._send(proto.encode_json(
            {"op": "spawn", "pane": int(pane_id), "argv": list(argv),
             "cols": int(cols), "rows": int(rows), "cwd": cwd, "env": env}))

    def send_input(self, pane_id, data: bytes):
        self._send(proto.encode_data(int(pane_id), data))

    def resize(self, pane_id, cols, rows):
        self._send(proto.encode_json(
            {"op": "resize", "pane": int(pane_id),
             "cols": int(cols), "rows": int(rows)}))

    def pause(self, pane_id):
        self._send(proto.encode_json({"op": "pause", "pane": int(pane_id)}))

    def resume(self, pane_id):
        self._send(proto.encode_json({"op": "resume", "pane": int(pane_id)}))

    def signal(self, pane_id, how: str):
        self._send(proto.encode_json(
            {"op": "signal", "pane": int(pane_id), "how": how}))

    def close_pane(self, pane_id):
        self._send(proto.encode_json({"op": "close", "pane": int(pane_id)}))

    def shutdown_host(self):
        """host 프로세스 자체를 내린다(모든 패널 종료). 서버의 '진짜' 종료에서만 —
        재시작 경로는 호출하지 않는다(연결만 끊어 host·세션 보존)."""
        self._send(proto.encode_json({"op": "shutdown"}))

    # ---- 콜백 등록·조회 ----
    def register(self, pane_id, on_data, on_eof):
        self._cb[int(pane_id)] = (on_data, on_eof)
        self._alive.add(int(pane_id))

    def unregister(self, pane_id):
        self._cb.pop(int(pane_id), None)

    def pid(self, pane_id) -> int:
        return self._pid.get(int(pane_id), -1)

    def is_alive(self, pane_id) -> bool:
        return int(pane_id) in self._alive

    def exit_status(self, pane_id):
        return self._exit.get(int(pane_id))

    async def list_panes(self):
        if self.writer is None:
            raise PtyHostError("host 미연결")
        fut = self.loop.create_future()
        self._list_waiters.append(fut)
        self._send(proto.encode_json({"op": "list"}))
        return await asyncio.wait_for(fut, 3.0)

    async def close(self):
        if self._read_task is not None:
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._read_task
        if self.writer is not None:
            with contextlib.suppress(Exception):
                self.writer.close()
        self.writer = self.reader = None

    def make_pane(self, pane_id, cols, rows) -> "_RemotePtyProcess":
        return _RemotePtyProcess(self, int(pane_id), cols, rows)


class _RemotePtyProcess(pty_backend.PtyProcess):
    """host 가 소유한 원격 PTY 의 서버측 프록시. PtyProcess 표면을 host RPC 로 포워딩."""

    def __init__(self, client: PtyHostClient, pane_id: int, cols: int, rows: int):
        self.client = client
        self.pane_id = pane_id
        self.cols = cols
        self.rows = rows

    @property
    def pid(self) -> int:               # host 가 'spawned' 로 보고한 실제 자식 pid(없으면 -1)
        return self.client.pid(self.pane_id)

    def start_reader(self, loop, on_data, on_eof) -> None:
        self.client.register(self.pane_id, on_data, on_eof)

    def stop_reader(self) -> None:
        self.client.unregister(self.pane_id)

    def pause_reader(self) -> None:
        self.client.pause(self.pane_id)

    def resume_reader(self) -> None:
        self.client.resume(self.pane_id)

    def write(self, data: bytes) -> int:
        self.client.send_input(self.pane_id, data)
        return len(data)

    def set_winsize(self, rows: int, cols: int) -> None:
        self.cols, self.rows = cols, rows
        self.client.resize(self.pane_id, cols, rows)

    def terminate(self) -> None:
        self.client.signal(self.pane_id, "terminate")

    def kill(self) -> None:
        self.client.signal(self.pane_id, "kill")

    def reap(self, *, block: bool = False):
        # host 가 exit 프레임으로 푸시한 종료상태. 폴링/블로킹 불필요(없으면 None).
        return self.client.exit_status(self.pane_id)

    def close(self) -> None:
        self.client.close_pane(self.pane_id)
