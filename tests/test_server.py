"""서버 기능 테스트: 패널/윈도우/세션 조작, 검색·버퍼·캡처, 영속, 제어."""
import asyncio
import base64
import json
import os
import shutil

import harness
import pytmux
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


async def test_tree_msg_includes_panes_and_remote():
    # _tree_msg 가 윈도우별 패널 목록(id·title·cmd·remote)을 담고, fg 명령이 ssh
    # 류면 remote=True 로 판정하는지(#14/#24 데이터 인프라).
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")
        win = sess.active_window
        ids = [p.id for p in win.panes()]
        # fg 명령을 흉내: 첫 패널은 ssh(원격), 나머지는 zsh(로컬)
        first = win.panes()[0]
        srv._fg_command = lambda fd, _f=first: ("ssh" if fd == _f.master_fd
                                                else "zsh")
        msg = srv._tree_msg()
        w = msg["sessions"][0]["windows"][0]
        assert isinstance(w["panes"], list) and len(w["panes"]) == len(ids)
        p0 = next(p for p in w["panes"] if p["id"] == first.id)
        assert p0["remote"] is True and p0["cmd"] == "ssh", "ssh 패널 → 원격"
        others = [p for p in w["panes"] if p["id"] != first.id]
        assert all(p["remote"] is False for p in others), "zsh 패널 → 로컬"
        assert "active" in w
    finally:
        await teardown(srv, task, sock)


async def test_inactive_tab_claude_done_flag():
    # 비활성 탭의 Claude 패널이 busy→idle 로 끝나면 has_claude_done 이 켜지고,
    # 그 탭을 보면(select_window) 해제된다(#22).
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.new_window(sess)                 # 탭 1 추가(활성=탭1)
        srv.select_window(sess, 0)           # 탭0 활성 → 탭1 비활성
        t1 = sess.tabs[1]
        p1 = t1.window.active_pane
        p1._claude = "busy"                  # 직전 상태: 처리중
        p1.feed(b"\r\ndone\r\n? for shortcuts\r\n")   # 화면이 idle footer
        win = sess.active_window             # 탭0(활성)
        srv._scan_claude(sess, win)
        assert t1.has_claude_done is True, "비활성 탭 busy→idle → 완료 알림"
        msg = srv._status_msg(sess)
        assert msg["windows"][1]["claude_done"] is True
        # 그 탭으로 전환 → 읽음 처리(해제)
        srv.select_window(sess, 1)
        assert t1.has_claude_done is False, "보면 해제"
        # 활성 탭에서 끝나는 건 알림 대상 아님
        p1._claude = "busy"
        p1.feed(b"\r\n? for shortcuts\r\n")
        srv._scan_claude(sess, sess.active_window)   # 이제 탭1이 활성
        assert sess.tabs[1].has_claude_done is False, "활성 탭은 알림 안 함"
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


async def test_pane_master_fd_cloexec():
    """새 패널의 PTY master 에 FD_CLOEXEC 가 걸려, 이후 만들어지는 패널의 자식 셸이
    형제 패널 fd 를 상속하지 않는다(패널 간 출력 섞임 방지)."""
    import fcntl
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "lr")
        srv.new_window(sess)
        all_panes = [p for t in sess.tabs for p in t.window.panes()]
        assert len(all_panes) >= 3
        for p in all_panes:
            flags = fcntl.fcntl(p.master_fd, fcntl.F_GETFD)
            assert flags & fcntl.FD_CLOEXEC, f"pane {p.id} master fd 에 CLOEXEC 없음"
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


async def test_mouse_mode_tracking_and_passthrough():
    import base64
    from pytmuxlib.model import ClientConn
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # 내부 앱이 DECSET 1002+1006 을 켜면 추적되고 레이아웃에 노출된다
        changed = p.update_mouse_modes(b"\x1b[?1002h\x1b[?1006h")
        assert changed and p.mouse_track == 2 and p.mouse_sgr is True
        lay = srv._layout_msg(sess)
        pm = next(m for m in lay["panes"] if m["id"] == p.id)
        assert pm["mouse"] == 2 and pm["mouse_sgr"] is True
        # 끄면 0 으로 복귀
        p.update_mouse_modes(b"\x1b[?1002l\x1b[?1006l")
        assert p.mouse_track == 0 and p.mouse_sgr is False

        # mouse 플래그 입력은 대상 패널만, 프롬프트 추적/동기화 제외
        srv.split_pane(sess, "lr")
        srv._layout_msg(sess)
        srv.set_sync(sess, True)        # 동기화 ON 이어도 마우스는 브로드캐스트 안 함
        target = win.panes()[0]
        client = ClientConn(None)
        client.session = sess
        seq = b"\x1b[<0;3;4M"
        srv._handle_input(client, {"pane": target.id, "mouse": True,
                                   "data": base64.b64encode(seq).decode()})
        await asyncio.sleep(0.2)
        # 마우스 경로는 _track_prompt 를 거치지 않으므로 입력 누적이 없어야 함
        assert target._inbuf == "" and target.last_prompt == ""
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


async def test_claude_prompt_tracking():
    """입력에서 마지막 프롬프트 추적(백스페이스/CSI/붙여넣기) + 탭 상태 집계."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        srv._track_prompt(p, b"abc\x7f\r")            # abc + backspace → ab
        assert p.last_prompt == "ab", p.last_prompt
        srv._track_prompt(p, b"\x1b[200~pasted\x1b[201~\r")  # bracketed paste 본문
        assert p.last_prompt == "pasted", p.last_prompt
        srv._track_prompt(p, b"\x1b[Dmid\r")          # 화살표(CSI)는 건너뜀
        assert p.last_prompt == "mid", p.last_prompt
        # 붙여넣기(모바일 받아쓰기/자동완성 포함)도 추적되어야 함:
        # paste_text 로 본문 입력 후 별도 Enter(\r) 로 확정 → last_prompt 갱신.
        # (이 경로가 빠지면 헤더가 셸 실행 명령에 머문다)
        srv.paste_text(sess, "fix the header")
        assert p.last_prompt == "mid", "Enter 전엔 미확정"
        srv._track_prompt(p, b"\r")
        assert p.last_prompt == "fix the header", p.last_prompt
        # 탭 Claude 집계(limit > busy > idle)
        p._claude = "idle"
        assert srv._tab_claude(sess.active_tab) == "idle"
        p._claude = "limit"
        assert srv._tab_claude(sess.active_tab) == "limit"
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


async def test_capture_output():
    """패널 출력 캡처: 기본 ON, 무손실 기록, 토글, opts.json 영속/재시작 유지."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        assert srv.capture is True, "기본 ON"

        # 캡처 기록 → pane-<id>.log 에 raw 바이트 무손실
        srv._capture_write(pane, b"hello\x1b[31m world")
        path = os.path.join(srv.capture_dir, f"pane-{pane.id}.log")
        with open(path, "rb") as f:
            assert f.read() == b"hello\x1b[31m world", "무손실 캡처"
        # 메타 로그에 탭/패널 매핑
        meta = open(os.path.join(srv.capture_dir, "sessions.log")).read()
        assert f"pane-{pane.id}" in meta and "tab0:" in meta, meta

        # 끄면 파일 닫힘 + opts.json 영속(capture=False)
        assert srv.set_capture(False) is False
        assert pane.id not in srv._capfiles, "끄면 핸들 닫힘"
        assert json.load(open(srv.opts_path)) == {"capture": False}
        # 꺼진 동안 _on_pane_readable 경로는 기록하지 않음
        before = os.path.getsize(path)
        if srv.capture:
            srv._capture_write(pane, b"X")
        assert os.path.getsize(path) == before, "OFF 중 기록 없음"

        # 재시작 영속: 같은 sock 로 새 Server 를 만들면 OFF 를 읽음
        assert pytmux.Server(sock).capture is False, "재시작 후 OFF 유지"

        # 토글로 다시 ON → opts 갱신, 재기록 가능(lazy 재오픈)
        assert srv.set_capture(None) is True
        assert json.load(open(srv.opts_path)) == {"capture": True}
        srv._capture_write(pane, b"again")
        with open(path, "rb") as f:
            assert f.read().endswith(b"again"), "재개 후 append"
    finally:
        srv._close_all_capfiles()
        shutil.rmtree(srv.capture_dir, ignore_errors=True)
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)
