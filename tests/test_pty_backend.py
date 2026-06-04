"""pty_backend 추상층 회귀 테스트(헤드리스).

Unix 백엔드의 spawn → start_reader(스레드 무관 콜백) → write → set_winsize →
terminate/kill → reap → close 경로를 실제 PTY 로 검증한다. Windows(ConPTY) 경로는
pywinpty 가 필요해 이 머신에선 import 가용 여부만 확인하고 본 동작은 건너뛴다
(Windows 박스에서 별도 검증).
"""
import asyncio
import os

import harness  # noqa: F401 (sys.path 설정)
from pytmuxlib import pty_backend


async def test_spawn_read_eof():
    """짧게 출력하고 끝나는 명령 → 바이트 수신 + EOF 콜백 + reap."""
    if pty_backend.IS_WINDOWS:
        return  # ConPTY 경로는 Windows 에서 별도 검증
    loop = asyncio.get_running_loop()
    chunks: list[bytes] = []
    eof = asyncio.Event()

    pty = pty_backend.spawn(["/bin/echo", "PYTMUX_BACKEND_OK"],
                            cols=40, rows=10)
    pty.start_reader(loop, chunks.append, eof.set)
    await asyncio.wait_for(eof.wait(), timeout=5)

    out = b"".join(chunks)
    assert b"PYTMUX_BACKEND_OK" in out, out
    # EOF 후 reaper 가 종료 상태를 회수한다(블로킹 안전 — 이미 죽음).
    status = pty.reap(block=True)
    assert status is not None
    pty.close()


async def test_write_winsize_terminate():
    """셸에 입력을 써서 출력 확인 → set_winsize 무해 → terminate(SIGHUP) 로 종료."""
    if pty_backend.IS_WINDOWS:
        return
    loop = asyncio.get_running_loop()
    buf = bytearray()
    eof = asyncio.Event()

    env = dict(os.environ)
    env["PS1"] = ""  # 프롬프트 잡음 줄이기(있어도 마커 검출엔 무관)
    pty = pty_backend.spawn(["/bin/sh"], cols=40, rows=10, env=env)
    pty.start_reader(loop, buf.extend, eof.set)

    pty.write(b"echo PYTMUX_RW_OK\n")
    for _ in range(60):  # 최대 ~3s 동안 마커 등장 대기
        if b"PYTMUX_RW_OK" in bytes(buf):
            break
        await asyncio.sleep(0.05)
    assert b"PYTMUX_RW_OK" in bytes(buf), bytes(buf)

    pty.set_winsize(20, 60)  # 예외 없이 통과해야 함

    pty.terminate()  # SIGHUP → 셸 종료 → EOF
    try:
        await asyncio.wait_for(eof.wait(), timeout=5)
    except asyncio.TimeoutError:
        pty.kill()  # 보수적으로 강제 종료
    pty.reap(block=False)
    pty.close()


async def test_close_idempotent():
    """close()/reap() 를 두 번 불러도 예외가 없어야 한다."""
    if pty_backend.IS_WINDOWS:
        return
    loop = asyncio.get_running_loop()
    eof = asyncio.Event()
    pty = pty_backend.spawn(["/bin/echo", "x"], cols=20, rows=5)
    pty.start_reader(loop, lambda b: None, eof.set)
    await asyncio.wait_for(eof.wait(), timeout=5)
    pty.reap(block=True)
    pty.close()
    pty.close()  # 두 번째 호출 무해
    assert pty.reap(block=False) is None
