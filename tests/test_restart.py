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
