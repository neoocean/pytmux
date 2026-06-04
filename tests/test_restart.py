"""작업 보존 서버 재시작(re-exec) 테스트 — docs/RESTART_SCENARIO.md §7.

직렬화 라운드트립과 fd 채택/복원을 헤드리스로 검증한다. 실제 os.execv 는 테스트
러너 프로세스를 갈아끼우므로 여기서 직접 부르지 않고, 같은 프로세스 안에서
"옛 서버가 쥔 fd 를 새 서버가 채택" 하는 경로를 그대로 재현해 검증한다(=execv 후
상속 fd 를 다시 감싸는 것과 동치). 실 박스에서의 execv 검증은 별도(수동) 몫이다.
"""
import asyncio
import os

from harness import server_only, teardown
from pytmuxlib import ipc, pty_backend


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
