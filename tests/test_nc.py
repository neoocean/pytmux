"""ncd(Norton Change Directory 풍 디렉토리 트리) 기능 테스트.

서버 측: `_list_dirs`(직계 디렉토리만·정렬·graceful), `_ancestor_chain`,
`nc_list_msg`(초기=루트→cwd chain, 펼치기=직계 하위). 클라 측: 명령·모달·키·
speed search·콜백을 Textual headless(run_test)로 검증."""
import os
import tempfile

import harness
from harness import make_app, server_only, teardown
from textual.events import Key


def _make_tree(root):
    """root 아래에 디렉토리/파일/숨김을 만들어 _list_dirs 검증용 픽스처를 세운다."""
    os.makedirs(os.path.join(root, "alpha"))
    os.makedirs(os.path.join(root, "Beta"))      # 대소문자 정렬 확인용
    os.makedirs(os.path.join(root, "gamma"))
    os.makedirs(os.path.join(root, ".hidden"))   # 숨김 → 제외
    os.makedirs(os.path.join(root, "alpha", "child"))
    with open(os.path.join(root, "afile.txt"), "w") as f:
        f.write("x")                              # 파일 → 제외


# ---- 서버: 디렉토리 목록·사슬 ----
async def test_nc_list_dirs_only_sorted_no_hidden():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root)
            dirs = srv._list_dirs(root)
            names = [os.path.basename(p) for p in dirs]
            assert names == ["alpha", "Beta", "gamma"], names
            assert all(os.path.isabs(p) for p in dirs), "절대경로 반환"
    finally:
        await teardown(srv, task, sock)


async def test_nc_list_subpath():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root)
            dirs = srv._list_dirs(os.path.join(root, "alpha"))
            assert [os.path.basename(p) for p in dirs] == ["child"]
    finally:
        await teardown(srv, task, sock)


async def test_nc_list_empty_and_missing_graceful():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            assert srv._list_dirs(root) == []                       # 빈 디렉토리
        assert srv._list_dirs("/no/such/path/xyzzy") == []          # 없는 경로
        with tempfile.NamedTemporaryFile() as f:
            assert srv._list_dirs(f.name) == []                     # 파일 → 빈
    finally:
        await teardown(srv, task, sock)


async def test_nc_ancestor_chain_root_to_cwd():
    srv, task, sock = await server_only()
    try:
        assert srv._ancestor_chain("/a/b/c") == ["/", "/a", "/a/b", "/a/b/c"]
        assert srv._ancestor_chain("/") == ["/"]
    finally:
        await teardown(srv, task, sock)


async def test_nc_list_msg_chain_root_to_cwd():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root)
            cwd = os.path.join(root, "alpha")       # cwd 한 단계 깊이
            sess = srv.ensure_default_session(80, 24)
            srv._pane_cwd = lambda pane, _c=cwd: _c
            msg = srv.nc_list_msg(sess, None)
            assert msg["t"] == "nc_list" and msg["path"] is None
            assert msg["cwd"] == os.path.abspath(cwd)
            chain = msg["chain"]
            paths = [c[0] for c in chain]
            assert paths[0] == "/" and paths[-1] == os.path.abspath(cwd), paths
            # 사슬이 실제 조상 사슬과 일치(순서·연속성)
            assert paths == srv._ancestor_chain(cwd)
            # 각 단계의 자식 목록에 다음 사슬 원소가 들어 있어 펼친 경로가 안 끊김
            for parent, kids in chain[:-1]:
                nxt = paths[paths.index(parent) + 1]
                assert nxt in kids, (parent, nxt)
            # cwd 의 자식 = alpha/child
            assert [os.path.basename(p) for p in chain[-1][1]] == ["child"]
    finally:
        await teardown(srv, task, sock)


async def test_nc_list_msg_chain_keeps_hidden_ancestor_visible():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            hidden = os.path.join(root, ".cfg")     # 숨김 조상
            cwd = os.path.join(hidden, "proj")
            os.makedirs(cwd)
            sess = srv.ensure_default_session(80, 24)
            srv._pane_cwd = lambda pane, _c=cwd: _c
            chain = srv.nc_list_msg(sess, None)["chain"]
            d = dict((p, kids) for p, kids in chain)
            # root 의 직계 자식엔 보통 .cfg 가 빠지지만(숨김), 사슬 보강으로 포함된다
            assert hidden in d[os.path.abspath(root)], d[os.path.abspath(root)]
    finally:
        await teardown(srv, task, sock)


async def test_nc_list_msg_subpath_echoes_path():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root)
            sub = os.path.join(root, "alpha")
            msg = srv.nc_list_msg(sess=srv.ensure_default_session(80, 24),
                                  path=sub)
            assert os.path.abspath(msg["path"]) == os.path.abspath(sub)
            assert [os.path.basename(p) for p in msg["dirs"]] == ["child"]
    finally:
        await teardown(srv, task, sock)


# ---- 클라이언트(Textual headless) ----
async def _with_app(coro, size=(100, 30)):
    srv, task, sock = await server_only()
    app = make_app(sock)
    try:
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.4)
            await coro(app, pilot, srv)
    finally:
        await teardown(srv, task, sock)


# 루트→cwd(/r/sub) 사슬 픽스처. rows: /r, /r/sub, /r/sub/x, /r/other
_CHAIN_MSG = {"t": "nc_list", "root": "/", "path": None, "cwd": "/r/sub",
              "chain": [["/", ["/r"]],
                        ["/r", ["/r/sub", "/r/other"]],
                        ["/r/sub", ["/r/sub/x"]]]}


async def test_ncd_command_requests_list():
    async def body(app, pilot, srv):
        for name in ("ncd", "nc"):
            sent = []
            app.send_cmd = lambda action, **kw: sent.append((action, kw))
            app._run_command(name)
            assert app._want_nc is True
            assert sent == [("request_nc_list", {"path": None})], name
    await _with_app(body)


async def test_ncd_opens_expanded_to_cwd_and_enter_cds():
    async def body(app, pilot, srv):
        from pytmuxlib.clientnc import NcdScreen
        app.send_cmd = lambda *a, **k: None
        inp = []
        app.send_input = lambda data: inp.append(data)
        app._run_command("ncd")
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        scr = app.screen
        assert isinstance(scr, NcdScreen)
        # 사슬이 펼쳐져 4행, 커서는 cwd(/r/sub)
        assert len(scr._rows) == 4, scr._rows
        assert scr._cur_path() == "/r/sub"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert inp == [b"cd /r/sub\n"], inp
        assert not isinstance(app.screen, NcdScreen), "닫힘"
    await _with_app(body)


async def test_ncd_cd_quotes_spaces():
    async def body(app, pilot, srv):
        app.send_cmd = lambda *a, **k: None
        inp = []
        app.send_input = lambda data: inp.append(data)
        app._run_command("ncd")
        app._dispatch({"t": "nc_list", "root": "/", "path": None,
                       "cwd": "/r/a b", "chain": [["/", ["/r"]],
                                                  ["/r", ["/r/a b"]]]})
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert inp == [b"cd '/r/a b'\n"], inp
    await _with_app(body)


async def test_ncd_shift_enter_and_ctrl_o_split_with_path():
    async def body(app, pilot, srv):
        from pytmuxlib.clientnc import NcdScreen
        # Ctrl+O (폴백)
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("ncd"); sent.clear()
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        await pilot.press("ctrl+o")
        await pilot.pause(0.1)
        assert ("split", {"orient": "lr", "path": "/r/sub"}) in sent
        # Shift+Enter 동치
        sent.clear()
        app._want_nc = True
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        await app.screen.on_key(Key("shift+enter", None))
        await pilot.pause(0.1)
        assert ("split", {"orient": "lr", "path": "/r/sub"}) in sent
    await _with_app(body)


async def test_ncd_speed_search_jumps_by_typing():
    async def body(app, pilot, srv):
        app.send_cmd = lambda *a, **k: None
        app._run_command("ncd")
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        scr = app.screen
        assert scr._cur_path() == "/r/sub"
        await pilot.press("o")              # 'other' 로 점프(speed search)
        await pilot.pause(0.1)
        assert scr._find == "o"
        assert scr._cur_path() == "/r/other", scr._cur_path()
        # 방향키 누르면 검색어 리셋
        await pilot.press("up")
        await pilot.pause(0.05)
        assert scr._find == ""
    await _with_app(body)


async def test_ncd_right_expands_via_lazy_load():
    async def body(app, pilot, srv):
        from textual.widgets import ListView
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("ncd"); sent.clear()
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        scr = app.screen
        scr.query_one(ListView).index = 3      # /r/other (접힘·미로드)
        await pilot.press("right")
        await pilot.pause(0.1)
        assert ("request_nc_list", {"path": "/r/other"}) in sent
        app._dispatch({"t": "nc_list", "root": "/r/other", "path": "/r/other",
                       "dirs": ["/r/other/y"]})
        await pilot.pause(0.15)
        assert "/r/other" in scr._expanded
        assert len(scr._rows) == 5             # +/r/other/y
    await _with_app(body)


async def test_ncd_left_collapses():
    async def body(app, pilot, srv):
        from textual.widgets import ListView
        app.send_cmd = lambda *a, **k: None
        app._run_command("ncd")
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        scr = app.screen
        scr.query_one(ListView).index = 1      # /r/sub (펼쳐져 있음)
        assert len(scr._rows) == 4
        await pilot.press("left")              # 접기 → /r/sub/x 사라짐
        await pilot.pause(0.1)
        assert "/r/sub" not in scr._expanded
        assert len(scr._rows) == 3
    await _with_app(body)


async def test_ncd_esc_closes():
    async def body(app, pilot, srv):
        from pytmuxlib.clientnc import NcdScreen
        app.send_cmd = lambda *a, **k: None
        app._run_command("ncd")
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        assert isinstance(app.screen, NcdScreen)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, NcdScreen)
    await _with_app(body)
