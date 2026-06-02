"""테스트 공용 하니스: 서버 기동 / 클라이언트(headless) / 정리 헬퍼.

화면 없이 동작을 검증하기 위한 도구. 각 테스트는 자체 asyncio 루프(asyncio.run)
에서 실행되며, 서버를 띄우고 PTY 패널을 만든 뒤 텍스트로 결과를 확인한다.
"""
import asyncio
import os
import signal
import sys
import tempfile

# 상위 디렉토리(pytmux 패키지/진입점)를 import 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytmux  # noqa: E402


async def server_only():
    """서버를 기동하고 소켓이 뜰 때까지 대기. (srv, task, sock) 반환."""
    sock = tempfile.mktemp(suffix=".sock")
    srv = pytmux.Server(sock)
    task = asyncio.create_task(srv.serve())
    for _ in range(300):
        if os.path.exists(sock):
            break
        await asyncio.sleep(0.01)
    return srv, task, sock


def cleanup(srv, sock):
    """패널 자식 프로세스를 정리하고 소켓 파일 제거(루프는 중단하지 않음)."""
    srv.running = False
    for s in list(srv.sessions.values()):
        for t in s.tabs:
            for p in t.window.panes():
                try:
                    os.killpg(os.getpgid(p.child_pid), signal.SIGKILL)
                except Exception:
                    pass
    try:
        if os.path.exists(sock):
            os.unlink(sock)
    except OSError:
        pass


async def teardown(srv, task, sock):
    # 주의: 여기서 task 를 await 하지 않는다. Textual run_test 종료 직후엔 루프가
    # 정리 중이라 serve 태스크를 await 하면 "Event loop stopped" 가 난다.
    # cancel 만 하고 asyncio.run 의 마무리에 맡긴다.
    cleanup(srv, sock)
    task.cancel()


def pane_text(pane):
    """패널의 현재 렌더 결과를 텍스트로(스타일 제외)."""
    rows, _ = pane.render(False)
    return "\n".join("".join(seg[0] for seg in row) for row in rows)


async def drain(reader, store, timeout=0.8):
    """소켓에서 timeout 동안 들어오는 메시지를 store(list)에 모은다."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        try:
            msg = await asyncio.wait_for(pytmux.read_msg(reader),
                                         timeout=max(0.01, end - loop.time()))
        except asyncio.TimeoutError:
            break
        if msg is None:
            break
        store.append(msg)


async def first_session(srv, timeout=1.0):
    """세션이 생길 때까지 대기 후 첫 세션 반환."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if srv.sessions:
            return next(iter(srv.sessions.values()))
        await asyncio.sleep(0.02)
    return next(iter(srv.sessions.values())) if srv.sessions else None


def make_app(sock, cfg=None, session=None):
    return pytmux.build_client_app(sock, cfg or {}, session)
