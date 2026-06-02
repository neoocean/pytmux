"""서버 기능 테스트: 패널/윈도우/세션 조작, 검색·버퍼·캡처, 영속, 제어."""
import asyncio
import base64

import harness
from harness import first_session, pane_text, server_only, teardown


async def test_pane_tree_ops():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "lr")
        srv.split_pane(sess, "tb")
        assert len(win.panes()) == 3
        srv.toggle_zoom(sess)
        panes, _ = win.compute_layout(0, 0, 80, 23)
        assert win.zoomed and len(panes) == 1, "줌"
        srv.toggle_zoom(sess)
        win.apply_preset("tiled")
        assert len(win.panes()) == 3, "tiled 유지"
        srv.rotate_panes(sess, True)
        srv.swap_pane(sess, True)
        assert len(win.panes()) == 3
        for p in win.panes():
            assert p.parent is None or p in (p.parent.a, p.parent.b), "트리 일관성"
    finally:
        await teardown(srv, task, sock)


async def test_break_join_pane():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")
        n = len(sess.windows)
        srv.break_pane(sess)
        assert len(sess.windows) == n + 1, "break → 새 윈도우"
        srv.join_pane(sess)
        for p in sess.active_window.panes():
            assert p.parent is None or p in (p.parent.a, p.parent.b)
    finally:
        await teardown(srv, task, sock)


async def test_last_pane_and_window():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "lr")
        p2 = win.active_pane.id
        p1 = [p.id for p in win.panes() if p.id != p2][0]
        win.active_pane = win.pane_by_id(p1)
        srv.last_pane(sess)
        assert win.active_pane.id == p2
        srv.new_window(sess)
        srv.new_window(sess)
        srv.select_window(sess, 0)
        srv.last_window(sess)
        assert sess.active_index == 2
    finally:
        await teardown(srv, task, sock)


async def test_window_move_swap_rename():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.rename_window(sess, "w0")
        srv.new_window(sess)
        srv.rename_window(sess, "w1")
        srv.new_window(sess)
        srv.rename_window(sess, "w2")
        srv.move_window(sess, 0)
        assert [w.name for w in sess.windows] == ["w2", "w0", "w1"]
        assert [w.index for w in sess.windows] == [0, 1, 2]
        srv.swap_window(sess, 2)
        assert [w.name for w in sess.windows] == ["w1", "w0", "w2"]
    finally:
        await teardown(srv, task, sock)


async def test_sessions_named_rename_kill():
    srv, task, sock = await server_only()
    try:
        a = srv.new_session(80, 24, "alpha")
        assert "alpha" in srv.sessions
        srv.rename_session(a, "alpha2")
        assert "alpha2" in srv.sessions and "alpha" not in srv.sessions
        srv.kill_session("alpha2")
        assert "alpha2" not in srv.sessions
        # get_or_create: 없는 이름이면 생성
        s = srv.get_or_create_session("brandnew", 80, 24)
        assert s.name == "brandnew" and "brandnew" in srv.sessions
    finally:
        await teardown(srv, task, sock)


async def test_sync_input_broadcast():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "lr")
        srv._layout_msg(sess)            # 패널 크기 반영
        srv.set_sync(sess, True)
        assert win.sync
        # 동기화가 켜진 윈도우의 모든 패널에 동일 입력이 전달되는지 확인
        import os
        data = b"echo SYNCED\n"
        for t in win.panes():
            os.write(t.master_fd, data)
        await asyncio.sleep(0.5)
        assert all("SYNCED" in pane_text(p) for p in win.panes())
    finally:
        await teardown(srv, task, sock)


async def test_search_buffer_capture_clear():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        for i in range(50):
            p.feed((f"L{i} NEEDLE\r\n" if i == 10 else f"L{i}\r\n").encode())
        srv.search_pane(sess, "NEEDLE", "up")
        assert p._match_abs == 10, "검색 매치"
        srv.set_buffer("BUF1")
        assert srv.buffers[0] == "BUF1"
        p.feed(b"CAPME\r\n")
        srv.capture_pane(sess)
        assert "CAPME" in srv.buffers[0], "캡처"
        srv.clear_history(sess)
        assert len(p.screen.history.top) == 0, "히스토리 비움"
    finally:
        await teardown(srv, task, sock)


async def test_layout_persistence():
    srv, task, sock = await server_only()
    srv2, task2, sock2 = await server_only()
    try:
        sess = srv.new_session(80, 24, "work")
        srv.rename_window(sess, "editor")
        srv.split_pane(sess, "lr")
        srv.split_pane(sess, "tb")
        srv.new_window(sess)
        srv.rename_window(sess, "logs")
        import tempfile
        lp = tempfile.mktemp(suffix=".json")
        assert srv.save_layout(lp)
        struct = [(s.name, [(w.name, len(w.panes())) for w in s.windows])
                  for s in srv.sessions.values()]
        assert srv2.restore_layout(lp)
        struct2 = [(s.name, [(w.name, len(w.panes())) for w in s.windows])
                   for s in srv2.sessions.values()]
        assert struct2 == struct, (struct, struct2)
    finally:
        await teardown(srv, task, sock)
        await teardown(srv2, task2, sock2)


async def test_handle_control():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        assert srv.handle_control("new-window") == "ok"
        assert len(sess.windows) == 2
        srv.handle_control("split-window -h")
        assert len(sess.active_window.panes()) == 2
        srv.handle_control("rename-window CTRL")
        assert sess.active_window.name == "CTRL"
        assert srv.handle_control("bogus").startswith("unknown")
    finally:
        await teardown(srv, task, sock)


async def test_hello_and_multiclient_minsize():
    srv, task, sock = await server_only()
    import pytmux
    try:
        rA, wA = await asyncio.open_unix_connection(path=sock)
        await pytmux.write_msg(wA, {"t": "hello", "cols": 100, "rows": 40,
                                    "session": "main"})
        sA = []
        await harness.drain(rA, sA)
        assert any(m["t"] == "layout" for m in sA)
        assert any(m["t"] == "status" and m["session"] == "main" for m in sA)
        # 둘째 클라이언트(더 작음) → 공유 최소 크기 80x24
        rB, wB = await asyncio.open_unix_connection(path=sock)
        await pytmux.write_msg(wB, {"t": "hello", "cols": 80, "rows": 24})
        sB = []
        await harness.drain(rB, sB)
        layB = [m for m in sB if m["t"] == "layout"][-1]
        assert layB["cols"] == 80 and layB["rows"] == 24, "최소 크기 공유"
        wA.close()
        wB.close()
    finally:
        await teardown(srv, task, sock)
