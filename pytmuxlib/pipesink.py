"""pipe-pane 대상 프로세스로 패널 출력을 흘리는 **루프를 막지 않는** 싱크 (pytmux-34 서버 검수 #2).

종전엔 이벤트 루프 안에서 `proc.stdin.write(data); flush()` 를 동기로 불렀다. 대상이 stdin 을
제때 안 읽으면(멈춘 `ssh logbox 'cat >> x'` · `sleep 999`) OS 파이프 버퍼(Linux 64KiB ·
Windows 약 4KiB)가 차는 순간 그 쓰기가 **막혀 데몬 전체가 멎었다** — 서버는 단일 스레드라
모든 패널 I/O·입력·flush·ping 이 함께 선다.

이제 루프는 바이트를 **상한 있는 큐**에 넣기만 하고(즉시 반환), 실제 쓰기는 싱크마다 하나인
데몬 스레드가 한다. 대상이 `MAX_PENDING` 넘게 밀리면 그 파이프를 **끊는다**(조용히 버리지
않는다 — `overflowed` 가 서고 부르는 쪽이 사유를 로그에 남긴다). 밀린 출력을 끝없이 쥐고
있으면 이번엔 메모리가 새기 때문이다.
"""
from __future__ import annotations

import threading
from collections import deque


class PipeSink:
    MAX_PENDING = 1 << 20      # 1MiB — 이만큼 밀리면 대상이 안 읽는 것으로 본다

    def __init__(self, popen, max_pending: int | None = None):
        self.proc = popen
        self.max_pending = max_pending or self.MAX_PENDING
        self._q: deque = deque()
        self._pending = 0
        self._cv = threading.Condition()
        self.closed = False
        self.overflowed = False
        self.failed = False
        self._t = threading.Thread(target=self._run, name="pipe-pane-sink",
                                   daemon=True)
        self._t.start()

    def write(self, data: bytes) -> bool:
        """루프 스레드에서 부른다 — **절대 막지 않는다**. 받았으면 True, 닫혔으면 False."""
        if self.closed or not data:
            return not self.closed
        with self._cv:
            if self._pending + len(data) > self.max_pending:
                self.overflowed = True
                self.closed = True
                self._q.clear()
                self._pending = 0
                self._cv.notify()
                return False
            self._q.append(data)
            self._pending += len(data)
            self._cv.notify()
        return True

    def close(self, terminate: bool = False) -> None:
        with self._cv:
            self.closed = True
            self._cv.notify()
        if terminate:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def _run(self) -> None:
        stdin = getattr(self.proc, "stdin", None)
        try:
            while True:
                with self._cv:
                    while not self._q and not self.closed:
                        self._cv.wait()
                    if not self._q and self.closed:
                        break
                    chunk = self._q.popleft()
                    self._pending -= len(chunk)
                try:
                    stdin.write(chunk)
                    stdin.flush()
                except (OSError, ValueError, AttributeError):   # broken pipe / 닫힌 stdin
                    self.failed = True
                    with self._cv:
                        self.closed = True
                        self._q.clear()
                        self._pending = 0
                    break
        finally:
            try:
                if stdin is not None:
                    stdin.close()
            except (OSError, ValueError):
                pass
            if self.overflowed:
                # 안 읽는 대상은 끝까지 기다리지 않는다 — stdin 을 닫고 끝낸다.
                try:
                    self.proc.terminate()
                except OSError:
                    pass
