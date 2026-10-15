"""pipe-pane 이 이벤트 루프를 막지 않는다 (pytmux-34 서버 검수 #2).

종전엔 루프 안에서 대상 stdin 에 동기로 write+flush 했다 — 대상이 안 읽으면 파이프가 차는
순간(Windows 약 4KiB) 데몬 전체가 멎었다.

되돌리면 실패해야 하는 오라클:
  · 싱크가 호출자 스레드에서 직접 쓰면 → test_a_consumer_that_never_reads_does_not_block (시한 초과)
  · 상한을 빼면 → 같은 시험의 overflowed 단언
  · serverpty 가 싱크 대신 stdin 에 직접 쓰면(호출부) → test_the_ingest_path_uses_the_sink
"""
import inspect
import os
import subprocess
import sys
import tempfile
import time

import harness  # noqa: F401

from pytmuxlib import serverpty
from pytmuxlib.pipesink import PipeSink


def test_a_consumer_that_never_reads_does_not_block():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             stdin=subprocess.PIPE)
    sink = PipeSink(child, max_pending=256 * 1024)
    try:
        t0 = time.monotonic()
        chunk = b"x" * 65536
        accepted = 0
        for _ in range(64):                          # 4MiB — 파이프 버퍼의 수백 배
            if not sink.write(chunk):
                break
            accepted += 1
        took = time.monotonic() - t0
        assert took < 1.0, "pipe-pane 쓰기가 %.2f초 막혔다 — 루프가 선다" % took
        assert sink.overflowed and sink.closed, "안 읽는 대상인데 상한에서 안 끊었다"
        assert accepted < 64
    finally:
        sink.close(terminate=True)
        try:
            child.wait(5)
        except subprocess.TimeoutExpired:
            child.kill()


def test_a_reading_consumer_gets_every_byte():
    out = os.path.join(tempfile.mkdtemp(prefix="pytmux-pipesink-"), "out.bin")
    code = ("import sys\n"
            f"open({out!r}, 'wb').write(sys.stdin.buffer.read())\n")
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE)
    sink = PipeSink(child)
    payload = [("line %05d\n" % i).encode() for i in range(5000)]
    for p in payload:
        assert sink.write(p)
    sink.close()                                     # 밀린 것을 다 쓴 뒤 stdin 을 닫는다
    child.wait(20)
    with open(out, "rb") as f:
        assert f.read() == b"".join(payload)


def test_the_ingest_path_uses_the_sink():
    """호출부 — 패널 출력 경로가 stdin 에 직접 쓰지 않고 싱크의 write 를 부른다."""
    src = inspect.getsource(serverpty)
    assert "pipe_proc.stdin.write" not in src, "루프에서 pipe-pane stdin 에 직접 쓴다"
    assert "sink.write(data)" in src
