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
        n = len(sess.tabs)
        srv.break_pane(sess)
        assert len(sess.tabs) == n + 1, "break → 새 윈도우"
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
        assert [t.name for t in sess.tabs] == ["w2", "w0", "w1"]
        assert [t.index for t in sess.tabs] == [0, 1, 2]
        srv.swap_window(sess, 2)
        assert [t.name for t in sess.tabs] == ["w1", "w0", "w2"]
    finally:
        await teardown(srv, task, sock)


async def test_single_session_enforced():
    """단일 세션 모델: 이름을 줘도 항상 같은 하나의 세션에 attach 한다."""
    srv, task, sock = await server_only()
    try:
        s1 = srv.ensure_default_session(80, 24)
        s2 = srv.get_or_create_session("brandnew", 80, 24)
        s3 = srv.get_or_create_session("other", 80, 24)
        assert s1 is s2 is s3, "세션 이름 요청은 무시되고 단일 세션"
        assert len(srv.sessions) == 1
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
        struct = [(s.name, [(t.name, len(t.window.panes())) for t in s.tabs])
                  for s in srv.sessions.values()]
        assert srv2.restore_layout(lp)
        struct2 = [(s.name, [(t.name, len(t.window.panes())) for t in s.tabs])
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
        assert len(sess.tabs) == 2
        srv.handle_control("split-window -h")
        assert len(sess.active_window.panes()) == 2
        srv.handle_control("rename-window CTRL")
        assert sess.active_tab.name == "CTRL"
        assert srv.handle_control("bogus").startswith("unknown")
    finally:
        await teardown(srv, task, sock)


async def test_tab_hierarchy_and_commands():
    """최상위 Tab → 단일 Window → 패널 집합 구조 및 탭 명령(new/kill/rename)."""
    from pytmuxlib.model import Tab, Window
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        assert isinstance(sess.tabs[0], Tab), "최상위는 Tab"
        assert isinstance(sess.active_tab.window, Window), "탭에 종속된 단일 윈도우"
        assert sess.active_tab.window is sess.active_window, "compat 프로퍼티"
        # 새 탭 = 새 윈도우(단일 패널)
        assert srv.handle_control("new-tab") == "ok"
        assert len(sess.tabs) == 2
        assert len(sess.active_tab.window.panes()) == 1
        # 탭의 윈도우를 패널로 분할
        srv.split_pane(sess, "lr")
        assert len(sess.active_tab.window.panes()) == 2
        # 탭 이름 변경
        srv.handle_control("rename-tab MYTAB")
        assert sess.active_tab.name == "MYTAB"
        # 탭 삭제
        srv.handle_control("kill-tab")
        assert len(sess.tabs) == 1
    finally:
        await teardown(srv, task, sock)


async def test_resize_rescales_panes():
    """터미널 리사이즈 시 패널이 비율대로 다시 계산된다."""
    import pytmux
    srv, task, sock = await server_only()
    try:
        r, w = await asyncio.open_unix_connection(path=sock)
        await pytmux.write_msg(w, {"t": "hello", "cols": 100, "rows": 40})
        s = []
        await harness.drain(r, s)
        sess = next(iter(srv.sessions.values()))
        srv.split_pane(sess, "lr")
        await pytmux.write_msg(w, {"t": "resize", "cols": 100, "rows": 40})
        s = []
        await harness.drain(r, s)
        big = [m for m in s if m["t"] == "layout"][-1]
        wbig = max(p["w"] for p in big["panes"])
        await pytmux.write_msg(w, {"t": "resize", "cols": 50, "rows": 40})
        s = []
        await harness.drain(r, s)
        small = [m for m in s if m["t"] == "layout"][-1]
        wsmall = max(p["w"] for p in small["panes"])
        assert small["cols"] == 50 and wsmall < wbig, (wsmall, wbig)
        w.close()
    finally:
        await teardown(srv, task, sock)


async def test_tab_reorder():
    """탭 재정렬: move_current_tab(좌/우/맨앞/맨뒤) + move_tab(임의), 활성 추적."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        for nm in ["a", "b", "c"]:
            srv.new_window(sess)
            srv.rename_window(sess, nm)
        assert [t.name for t in sess.tabs] == ["win", "a", "b", "c"]
        assert sess.active_tab.name == "c"
        srv.move_current_tab(sess, "first")
        assert [t.name for t in sess.tabs] == ["c", "win", "a", "b"]
        assert sess.active_tab.name == "c", "활성 탭 추적"
        srv.move_current_tab(sess, "last")
        assert [t.name for t in sess.tabs] == ["win", "a", "b", "c"]
        srv.move_current_tab(sess, "left")
        assert [t.name for t in sess.tabs] == ["win", "a", "c", "b"]
        # 임의 탭 이동(활성 c 는 위치 유지 추적)
        srv.move_tab(sess, 0, 3)
        assert [t.name for t in sess.tabs] == ["a", "c", "b", "win"]
        assert sess.active_tab.name == "c"
    finally:
        await teardown(srv, task, sock)


async def test_per_tab_layout_save_load():
    """활성 탭 레이아웃을 이름 슬롯에 저장 → 새 탭/현재 탭 덮어쓰기로 불러오기."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")
        srv.split_pane(sess, "tb")
        assert len(sess.active_tab.window.panes()) == 3
        assert srv.save_tab_layout(sess, "three")
        assert "three" in srv.list_tab_layouts()
        # 새 탭으로 불러오기
        assert srv.load_tab_layout(sess, "three", new_tab=True)
        assert len(sess.tabs) == 2
        assert len(sess.active_tab.window.panes()) == 3
        # 단일 패널 탭 만든 뒤 현재 탭 덮어쓰기
        srv.new_window(sess)
        assert len(sess.tabs) == 3
        assert len(sess.active_tab.window.panes()) == 1
        assert srv.load_tab_layout(sess, "three", new_tab=False)
        assert len(sess.tabs) == 3, "덮어쓰기는 탭 수 불변"
        assert len(sess.active_tab.window.panes()) == 3
        # 없는 슬롯
        assert srv.load_tab_layout(sess, "nope") is False
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
        assert any(m["t"] == "status" for m in sA)  # 단일 세션(이름 무시)
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
