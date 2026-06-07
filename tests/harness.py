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
from pytmuxlib import ipc  # noqa: E402

IS_WINDOWS = os.name == "nt"


async def server_only():
    """서버를 기동하고 listen 이 뜰 때까지 대기. (srv, task, endpoint) 반환.

    Unix: 임시 `.sock`(AF_UNIX). Windows: asyncio 의 AF_UNIX 지원이 불완전해
    `ipc` 가 TCP 루프백으로 분기하므로 여기서도 TCP 에페메럴(포트 0)을 쓴다.
    TCP 는 상태파일 prefix(`ipc.state_base`)·포트파일이 고정 경로
    (`default_state_dir/default`)라 테스트 간 충돌하므로, 매 테스트마다 유니크한
    상태 디렉터리를 `LOCALAPPDATA` 로 주입해 격리한다.
    반환값은 **확정 엔드포인트**(TCP 면 실제 포트)라 클라이언트가 그대로 접속한다.
    """
    if IS_WINDOWS:
        os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="pytmux-test-")
        endpoint = "tcp:127.0.0.1:0"
    else:
        endpoint = tempfile.mktemp(suffix=".sock")
    # 캡처(REC) 출력 격리: 테스트 엔드포인트 "tcp:127.0.0.1:0" 는 default_endpoint()
    # 와 같아 server.capture_dir 가 **공유 프로젝트 captures/default** 를 가리킨다.
    # 그러면 실사용 pytmux 데몬이 같은 파일을 캡처 중일 때 test_capture_output 이 그
    # 17MB 짜리 실제 세션 로그를 읽어 깨진다(테스트 격리 결함). PYTMUX_CAPTURE_DIR 를
    # 매 서버마다 유니크 임시 디렉터리로 주입해 캡처를 격리한다(capture_dir 가 이
    # override 를 우선한다). 실사용 captures/ 오염도 막는다.
    os.environ["PYTMUX_CAPTURE_DIR"] = tempfile.mkdtemp(prefix="pytmux-cap-")
    srv = pytmux.Server(endpoint)
    task = asyncio.create_task(srv.serve())
    # listen 준비 신호: Unix=소켓 파일 생성, TCP=resolved_endpoint 가 실제 포트로 확정.
    for _ in range(300):
        if ipc.is_tcp(endpoint):
            re = srv.resolved_endpoint
            if ipc.is_tcp(re) and not re.endswith(":0"):
                break
        elif os.path.exists(endpoint):
            break
        await asyncio.sleep(0.01)
    return srv, task, srv.resolved_endpoint


def cleanup(srv, endpoint):
    """패널 자식 프로세스를 정리하고 (Unix) 소켓 파일 제거(루프는 중단하지 않음)."""
    srv.running = False
    for s in list(srv.sessions.values()):
        for t in s.tabs:
            for p in t.window.panes():
                try:
                    if p.pty is not None:
                        # 크로스플랫폼: pty_backend 가 OS 별 종료를 추상화(Unix
                        # SIGKILL / Windows TerminateProcess).
                        p.pty.kill()
                        p.pty.close()
                    elif not IS_WINDOWS:
                        os.killpg(os.getpgid(p.child_pid), signal.SIGKILL)
                except Exception:
                    pass
    if not ipc.is_tcp(endpoint):
        try:
            if os.path.exists(endpoint):
                os.unlink(endpoint)
        except OSError:
            pass


async def teardown(srv, task, sock):
    # 주의: 여기서 task 를 await 하지 않는다. Textual run_test 종료 직후엔 루프가
    # 정리 중이라 serve 태스크를 await 하면 "Event loop stopped" 가 난다.
    # cancel 만 하고 asyncio.run 의 마무리에 맡긴다.
    cleanup(srv, sock)
    task.cancel()
    # server_only 가 주입한 캡처 격리 override 를 해제 — 같은 프로세스의 다른
    # 테스트(capture_dir 의 비-override 동작을 검증하는 test_capture_dir_project_and_override
    # 등)에 새지 않게 한다.
    os.environ.pop("PYTMUX_CAPTURE_DIR", None)


def pane_text(pane):
    """패널의 현재 렌더 결과를 텍스트로(스타일 제외)."""
    rows, _ = pane.render(False)
    return "\n".join("".join(seg[0] for seg in row) for row in rows)


async def wait_until(pilot, cond, timeout=4.0, step=0.05):
    """cond() 가 참이 될 때까지 pilot.pause(step) 로 폴링한다(최대 timeout). 참이 되면
    True, 시간 초과면 False. 고정 `pilot.pause(N)` + 단언 패턴의 CI 플레이크(느린
    Windows 러너에서 모달 push·키 처리·렌더가 N 초 안에 안 끝남)를 없앤다 — Unix 에선
    조건 충족 즉시 빠르고, 느린 환경에선 timeout 까지 인내한다. 호출부는 반환 후에도
    동일 조건을 단언해(실패 메시지 보존) 의미를 유지한다."""
    import asyncio as _asyncio
    loop = _asyncio.get_event_loop()
    end = loop.time() + timeout
    while True:
        try:
            if cond():
                return True
        except Exception:
            pass
        if loop.time() >= end:
            return False
        await pilot.pause(step)


async def drain(reader, store, timeout=0.8, until=None):
    """소켓에서 timeout 동안 들어오는 메시지를 store(list)에 모은다.

    until(store) 술어를 주면 만족 즉시 반환한다. Windows(TCP+ConPTY)는 메시지
    왕복이 느려 고정 창이 빠듯하므로, 호출부는 넉넉한 timeout + until 로 "조건 충족
    시 조기 반환"을 쓰면 Unix 에선 빠르고 Windows 에선 인내한다.
    """
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
        if until is not None and until(store):
            break


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
