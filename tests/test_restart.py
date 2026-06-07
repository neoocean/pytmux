"""작업 보존 서버 재시작(re-exec) 테스트 — docs/RESTART_SCENARIO.md §7.

직렬화 라운드트립과 fd 채택/복원을 헤드리스로 검증한다. 실제 os.execv 는 테스트
러너 프로세스를 갈아끼우므로 여기서 직접 부르지 않고, 같은 프로세스 안에서
"옛 서버가 쥔 fd 를 새 서버가 채택" 하는 경로를 그대로 재현해 검증한다(=execv 후
상속 fd 를 다시 감싸는 것과 동치). 실 박스에서의 execv 검증은 별도(수동) 몫이다.
"""
import asyncio
import base64
import os
import re

import pytmux
from harness import server_only, teardown
from pytmuxlib import ipc, proc, pty_backend
from pytmuxlib.protocol import read_msg, write_msg


# ── 1. Pane 상태 직렬화 라운드트립(PTY 없이 순수) ─────────────────────────────
async def test_pane_export_import_roundtrip():
    from pytmuxlib.model import Pane
    p = Pane(1234, -1, 80, 24)
    p.title = "editor"
    p.autoresume = True
    p.resume_msg = "go on"
    p.last_prompt = "구현해줘"
    p._claude = "busy"
    p._claude_usage = "ctx 42%"
    p._session_tokens = 4321
    p._tok_state = {"peak": 100, "total": 4321}
    p._mouse_modes = {1000, 1002}
    p.mouse_sgr = True
    p.prompt_history = ["a", "b", "c"]
    p.feed(b"hello world\r\nsecond line\r\n")

    d = p.export_state()
    assert d["child_pid"] == 1234
    assert d["master_fd"] == -1
    assert d["cols"] == 80 and d["rows"] == 24

    q = Pane(1234, -1, 80, 24)
    q.import_state(d)
    assert q.title == "editor"
    assert q.autoresume is True
    assert q.resume_msg == "go on"
    assert q.last_prompt == "구현해줘"
    assert q._claude == "busy"
    assert q._claude_usage == "ctx 42%"
    assert q._session_tokens == 4321
    assert q._tok_state == {"peak": 100, "total": 4321}
    assert q._mouse_modes == {1000, 1002}
    assert q.mouse_sgr is True
    assert q.mouse_track == 2   # 1002 → drag
    assert q.prompt_history == ["a", "b", "c"]
    # 화면 스냅샷이 복원돼 내용이 비어 있지 않다.
    from harness import pane_text
    assert "hello world" in pane_text(q)
    assert "second line" in pane_text(q)


# ── 1b. 와이드(한글) 문자 스냅샷이 자간 안 벌어지고 복원된다 ──────────────────
async def test_export_import_preserves_wide_chars():
    """예전 버그: _export_screen 이 와이드 문자 연속 셀(data=="")을 공백으로 내보내,
    복원 feed 때 'AB' 인 한글이 'A B' 처럼 사이에 빈칸이 끼어 자간이 벌어졌다."""
    from pytmuxlib.model import Pane
    from harness import pane_text
    p = Pane(1234, -1, 80, 24)
    p.feed("한글 테스트 ABC\r\n".encode("utf-8"))
    q = Pane(1234, -1, 80, 24)
    q.import_state(p.export_state())
    txt = pane_text(q)
    assert "한글 테스트 ABC" in txt          # 원형 그대로 복원
    assert "한 글" not in txt and "테 스 트" not in txt   # 자간 안 벌어짐
    # export 가 멱등(복원 후 다시 export 하면 동일) — 연속 셀이 안정적으로 재구성됨
    assert q._export_screen() == p._export_screen()


# ── 2. 서버 save_resume_state 가 트리 구조 + PTY 식별자를 담는다 ───────────────
async def test_save_resume_state_structure():
    if ipc.IS_WINDOWS:
        return
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")
        srv.new_window(sess)
        import tempfile
        path = tempfile.mktemp(suffix=".resume.json")
        assert srv.save_resume_state(path)
        import json
        with open(path) as f:
            data = json.load(f)
        assert data["version"] == 1
        s0 = data["sessions"][0]
        assert len(s0["tabs"]) == 2
        # 첫 탭은 좌우 분할 → split 노드, 자식 둘은 살아 있는 셸 pid 를 가진다.
        root = s0["tabs"][0]["window"]["root"]
        assert root["type"] == "split" and root["orient"] == "lr"
        for side in ("a", "b"):
            leaf = root[side]
            assert leaf["type"] == "pane"
            assert leaf["pane"]["child_pid"] > 0
            assert leaf["pane"]["master_fd"] >= 0
        os.unlink(path)
    finally:
        await teardown(srv, task, sock)


async def _read_until(fd, marker: bytes, timeout=3.0) -> bytes:
    """master fd 에서 marker 가 보일 때까지(또는 timeout) 비동기로 읽어 모은다."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    buf = b""
    while loop.time() < end and marker not in buf:
        try:
            chunk = os.read(fd, 65536)
            if chunk:
                buf += chunk
                continue
        except BlockingIOError:
            pass
        except OSError:
            break
        await asyncio.sleep(0.02)
    return buf


# ── 3. fd 채택: fork 없이 기존 셸 PTY 를 다시 감싸 읽기/쓰기 ───────────────────
async def test_adopt_preserves_pid_and_io():
    if ipc.IS_WINDOWS:
        return
    import fcntl
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        orig_pid, orig_fd = pane.child_pid, pane.master_fd
        # execv 후엔 옛 리더 등록이 사라진다 → 여기서도 리더를 멈추고 fd 직접 다룬다.
        pane.pty.stop_reader()
        adopted = pty_backend.adopt(orig_fd, orig_pid, cols=80, rows=24)
        assert adopted.pid == orig_pid, "채택 후 PID 보존"
        # CLOEXEC 가 다시 걸렸는지(ⓓ §6 불변식)
        flags = fcntl.fcntl(orig_fd, fcntl.F_GETFD)
        assert flags & fcntl.FD_CLOEXEC, "채택 시 CLOEXEC 재채택"
        # 살아 있는 셸과 입출력이 된다 → PTY 가 보존됐다.
        adopted.write(b"echo HELLO_ADOPT_42\r\n")
        out = await _read_until(orig_fd, b"HELLO_ADOPT_42")
        assert b"HELLO_ADOPT_42" in out, out[-200:]
        adopted.kill()
        adopted.close()
        adopted.reap(block=True)
        # 서버 cleanup 이 같은 fd 를 다시 닫지 않도록 세션을 비운다.
        srv.sessions.clear()
    finally:
        await teardown(srv, task, sock)


# ── 4. 전체 복원 라운드트립: 옛 서버가 쥔 fd 를 새 서버가 채택(= execv 후 동치) ──
async def test_restore_resume_state_roundtrip():
    if ipc.IS_WINDOWS:
        return
    import fcntl
    import tempfile
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    try:
        sess = srvA.ensure_default_session(80, 24)
        srvA.rename_window(sess, "editor")
        srvA.split_pane(sess, "lr")
        srvA.new_window(sess)
        srvA.rename_window(sess, "logs")
        # 활성 패널에 표식 상태를 심어 복원 확인
        ap = sess.active_window.active_pane
        ap.title = "MARKED"
        ap.autoresume = True
        ap._claude = "idle"
        struct = [(t.name, len(t.window.panes())) for t in sess.tabs]
        pids = sorted(p.child_pid for p in srvA._all_panes())

        path = tempfile.mktemp(suffix=".resume.json")
        assert srvA.save_resume_state(path)
        # execv 재현: 옛 서버의 리더 등록을 모두 해제(새 이미지가 fd 를 채택).
        for p in srvA._all_panes():
            p.pty.stop_reader()

        assert srvB.restore_resume_state(path)
        sessB = next(iter(srvB.sessions.values()))
        structB = [(t.name, len(t.window.panes())) for t in sessB.tabs]
        assert structB == struct, (struct, structB)
        pidsB = sorted(p.child_pid for p in srvB._all_panes())
        assert pidsB == pids, "복원 후 셸 PID 보존"
        # CLOEXEC 재채택 + 활성 패널 상태/이름 복원
        for p in srvB._all_panes():
            flags = fcntl.fcntl(p.master_fd, fcntl.F_GETFD)
            assert flags & fcntl.FD_CLOEXEC
        apB = sessB.active_window.active_pane
        assert apB.title == "MARKED"
        assert apB.autoresume is True
        assert apB._claude == "idle"
        # 살아 있는 셸과 입출력 가능(PTY 보존)
        apB.pty.stop_reader()
        apB.pty.write(b"echo RESTORED_OK_7\r\n")
        out = await _read_until(apB.master_fd, b"RESTORED_OK_7")
        assert b"RESTORED_OK_7" in out, out[-200:]
        os.unlink(path)
        srvA.sessions.clear()   # 같은 fd 이중 close 방지(B 가 정리)
    finally:
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


# ── 5. 종단간 실 재시작(os.execv): 서버 서브프로세스를 띄워 restart 후 셸 PID 보존 ──
async def _conn(endpoint):
    return await ipc.open_connection(endpoint)


async def _send_input(writer, text: str):
    await write_msg(writer, {"t": "input",
                             "data": base64.b64encode(text.encode()).decode()})


async def _await_regex(reader, pattern, timeout=8.0):
    """hello 이후 들어오는 screen 메시지의 텍스트를 모아 pattern 매치를 찾는다."""
    rx = re.compile(pattern)
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    acc = ""
    while loop.time() < end:
        try:
            msg = await asyncio.wait_for(read_msg(reader),
                                         timeout=max(0.05, end - loop.time()))
        except asyncio.TimeoutError:
            break
        if msg is None:
            break
        t = msg.get("t")
        if t == "screen":
            for row in msg.get("rows", []):
                for seg in row:
                    acc += seg[0]
        elif t == "screen-delta":   # B2: 바뀐 행만 — 그 행 텍스트도 누적
            for _y, segs in msg.get("rows", []):
                for seg in segs:
                    acc += seg[0]
        else:
            continue
        m = rx.search(acc)
        if m:
            return m
    return rx.search(acc)


async def test_execv_restart_preserves_shell_pid():
    """실제 os.execv 재시작 후에도 패널 셸이 같은 PID 로 살아 있는지 종단간 검증.

    서버를 서브프로세스로 띄우고(=실 데몬), 패널 셸에 `echo $$` 를 보내 PID 를 읽은
    뒤 restart-server 를 트리거, 재기동된 서버에 재접속해 다시 `echo $$` 로 같은
    PID 인지 확인한다. docs/RESTART_SCENARIO.md §7 test_restart_preserves_pids."""
    if ipc.IS_WINDOWS:
        return
    import tempfile
    endpoint = tempfile.mktemp(suffix=".sock")
    pid = proc.spawn_detached(proc.server_argv(endpoint))
    try:
        # listen 대기
        for _ in range(300):
            if ipc.probe(endpoint):
                break
            await asyncio.sleep(0.02)
        assert ipc.probe(endpoint), "서버 서브프로세스 기동 실패"

        reader, writer = await _conn(endpoint)
        await write_msg(writer, {"t": "hello", "cols": 80, "rows": 24,
                                 "token": ipc.read_token(endpoint)})
        await asyncio.sleep(0.3)               # 셸 프롬프트 안정화
        await _send_input(writer, "echo PX=$$\n")
        m = await _await_regex(reader, r"PX=(\d+)")
        assert m, "재시작 전 셸 PID 를 못 읽음"
        pid1 = m.group(1)

        # 별도 연결로 restart-server 제어 요청
        r2, w2 = await _conn(endpoint)
        await write_msg(w2, {"t": "control", "line": "restart-server",
                             "token": ipc.read_token(endpoint)})
        reply = await asyncio.wait_for(read_msg(r2), timeout=3.0)
        assert reply and reply.get("result") == "restarting", reply
        w2.close()

        # 옛 연결은 execv 로 끊긴다. 재기동(같은 소켓) 대기 후 재접속.
        await asyncio.sleep(0.4)
        for _ in range(400):
            if ipc.probe(endpoint):
                break
            await asyncio.sleep(0.02)
        assert ipc.probe(endpoint), "재시작 후 서버 재기동 실패"

        reader2, writer2 = await _conn(endpoint)
        # 재시작 후 토큰이 새로 발급되므로 다시 읽는다.
        await write_msg(writer2, {"t": "hello", "cols": 80, "rows": 24,
                                  "token": ipc.read_token(endpoint)})
        await asyncio.sleep(0.3)
        await _send_input(writer2, "echo PY=$$\n")
        m2 = await _await_regex(reader2, r"PY=(\d+)")
        assert m2, "재시작 후 셸 PID 를 못 읽음"
        pid2 = m2.group(1)

        assert pid1 == pid2, f"재시작 전후 셸 PID 불일치: {pid1} != {pid2}"
    finally:
        try:
            r3, w3 = await _conn(endpoint)
            await write_msg(w3, {"t": "kill-server",
                                 "token": ipc.read_token(endpoint)})
            await asyncio.sleep(0.2)
            w3.close()
        except Exception:
            pass
        proc.terminate(pid, force=True)
        try:
            if os.path.exists(endpoint):
                os.unlink(endpoint)
        except OSError:
            pass


# ── 6. 클라이언트 재접속(ⓔ): restarting 통지 → 끊김 → 새 서버에 재접속 ──────────
async def test_client_reconnects_on_restarting():
    """서버가 {"t":"restarting"} 을 보낸 뒤 연결이 끊기면, 클라이언트가 종료하지
    않고 같은 소켓 경로로 재접속한다(docs/RESTART_SCENARIO.md ⓔ). 실 execv 대신
    옛 서버가 연결을 끊고 새 서버(재접속 대상)를 띄워 동치 상황을 만든다."""
    from harness import make_app
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    app = make_app(sockA)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.5)
            assert app.layout.get("panes"), "초기 접속 레이아웃"
            # 재시작 통지 → 클라이언트가 끊김을 재접속으로 다루도록 표식
            for c in list(srvA.clients):
                await write_msg(c.writer, {"t": "restarting"})
            await pilot.pause(0.3)
            assert app._reconnecting is True, "restarting 통지로 재접속 모드"
            # 재접속 대상 = 새 서버(실제론 같은 소켓; 테스트는 별 소켓으로 대체)
            app.sock_path = sockB
            # 옛 서버가 연결을 끊는다(execv 로 리슨 소켓이 닫히는 것과 동치)
            for c in list(srvA.clients):
                c.writer.close()
            await pilot.pause(1.0)
            assert app.writer is not None, "재접속 성공"
            assert app._reconnecting is False, "재접속 후 플래그 해제"
            assert app.layout.get("panes"), "재접속 후 레이아웃 재수신"
            assert srvB.clients, "새 서버에 클라이언트 연결됨"
    finally:
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


# ── 7. 클라이언트 명령 매핑: "restart-server" → restart_server 액션 ───────────────
async def test_client_restart_command_maps_action():
    """명령 프롬프트/팔레트에서 restart-server 를 치면 restart_server 액션을 서버로
    보낸다(실제 execv 는 서버 몫이라 send_cmd 만 가로채 검증)."""
    from harness import make_app
    srv, task, sock = await server_only()
    app = make_app(sock)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.4)
            sent = []
            app.send_cmd = lambda action, **kw: sent.append((action, kw))
            app._run_command("restart-server")
            app._run_command("restart")
            assert ("restart_server", {}) in sent, sent
            assert sent.count(("restart_server", {})) == 2, sent
    finally:
        await teardown(srv, task, sock)


# ── 8. 복원 후 alt-screen 재그리기 유도(SIGWINCH) — docs §주의① 대안 B ──────────
_ALT_APP = (
    "import signal,sys,time,os\n"
    "c=[0]\n"
    "def draw(n):\n"
    " sys.stdout.write('\\x1b[2J\\x1b[H');"
    "sys.stdout.write('ALT_MARK_%d\\r\\n'%n);sys.stdout.flush()\n"
    "def h(s,f):\n c[0]+=1;draw(c[0])\n"
    "signal.signal(signal.SIGWINCH,h)\n"
    "sys.stdout.write('\\x1b[?1049h');draw(0)\n"
    "time.sleep(30)\n"
)


async def test_restore_induces_altscreen_redraw():
    """재시작 복원 후 alt-screen TUI 가 SIGWINCH 로 다시 그려지는지(재접속 크기가
    같아도) 검증한다. 살아 있는 alt 앱을 띄운 패널을 새 서버가 채택한 뒤, 같은
    크기로 복원해도 _induce_redraw_all 이 SIGWINCH 를 유발해 앱이 repaint 한다."""
    if ipc.IS_WINDOWS:
        return
    import tempfile
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    appf = tempfile.mktemp(suffix=".py")
    with open(appf, "w") as f:
        f.write(_ALT_APP)
    try:
        sess = srvA.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        pane.pty.write(f"python3 {appf}\n".encode())
        # alt 진입 + ALT_MARK_0 가 보일 때까지 대기
        from harness import pane_text
        for _ in range(150):
            await asyncio.sleep(0.02)
            if "ALT_MARK" in pane_text(pane):
                break
        assert "ALT_MARK" in pane_text(pane), "alt 앱 기동 실패: " + pane_text(pane)[:200]

        path = tempfile.mktemp(suffix=".resume.json")
        assert srvA.save_resume_state(path)
        for p in srvA._all_panes():
            p.pty.stop_reader()
        # 같은 80x24 로 복원 → 크기 변화 없음. _induce_redraw_all 이 SIGWINCH 유발.
        assert srvB.restore_resume_state(path)
        paneB = next(iter(srvB.sessions.values())).active_window.active_pane
        # SIGWINCH → 앱 repaint 출력이 새 pyte 로 들어와 ALT_MARK 가 다시 보인다.
        seen = False
        for _ in range(200):
            await asyncio.sleep(0.02)
            if "ALT_MARK" in pane_text(paneB):
                seen = True
                break
        assert seen, "복원 후 재그리기 안 됨(마커 소실): " + pane_text(paneB)[:200]
        os.unlink(path)
        srvA.sessions.clear()
    finally:
        try:
            os.unlink(appf)
        except OSError:
            pass
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


# ── 9. 복원 후 스크롤백 연속성: 화면 밖으로 밀린 줄이 스크롤백에 보존 ─────────────
async def test_restore_preserves_scrollback():
    """일반 셸 패널에서 화면 높이를 넘는 출력을 낸 뒤 재시작(채택 복원)해도, 화면
    밖으로 밀린 초기 줄이 스크롤백에 보존돼 맨 위로 스크롤하면 다시 보인다.
    docs/RESTART_SCENARIO.md: 메인 화면 평문 스냅샷 복원(순수 셸 스크롤백 연속성)."""
    if ipc.IS_WINDOWS:
        return
    import tempfile
    from harness import pane_text
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    try:
        sess = srvA.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        pane.pty.write(b"for i in $(seq 1 40); do echo SCROLL_LINE_$i; done\n")
        for _ in range(200):
            await asyncio.sleep(0.02)
            if "SCROLL_LINE_40" in pane_text(pane):
                break
        assert "SCROLL_LINE_40" in pane_text(pane), "마커 출력 실패"

        path = tempfile.mktemp(suffix=".resume.json")
        assert srvA.save_resume_state(path)
        for p in srvA._all_panes():
            p.pty.stop_reader()
        assert srvB.restore_resume_state(path)
        paneB = next(iter(srvB.sessions.values())).active_window.active_pane
        await asyncio.sleep(0.3)   # induce_redraw/프롬프트 출력 정착
        # 맨 위로 스크롤 → 화면 밖으로 밀렸던 초기 줄이 스크롤백에서 보여야 한다.
        paneB.scroll_to("top")
        top = pane_text(paneB)
        early = [i for i in range(1, 6) if f"SCROLL_LINE_{i}" in top]
        assert len(early) >= 3, f"스크롤백 초기 줄 소실: {early}\n{top[:300]}"
        os.unlink(path)
        srvA.sessions.clear()
    finally:
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)
