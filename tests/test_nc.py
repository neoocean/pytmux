"""ncd(Norton Change Directory 풍 디렉토리 트리) 기능 테스트.

서버 측: `_list_dirs`(직계 디렉토리만·정렬·graceful), `_ancestor_chain`,
`nc_list_msg`(초기=루트→cwd chain, 펼치기=직계 하위). 클라 측: 명령·모달·키·
speed search·콜백을 Textual headless(run_test)로 검증."""
import os
import tempfile

import harness
from harness import make_app, server_only, teardown
from textual.events import Key

# ncd 서버 측 로직은 플러그인으로 옮겼다(pytmuxlib/plugins/ncd/server.py). 예전엔
# Server 의 메서드(srv._list_dirs 등)였던 것을 이제 모듈 함수로 직접 부른다.
import pytmuxlib.plugins.ncd.server as ncds


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
            dirs = ncds._list_dirs(root)
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
            dirs = ncds._list_dirs(os.path.join(root, "alpha"))
            assert [os.path.basename(p) for p in dirs] == ["child"]
    finally:
        await teardown(srv, task, sock)


async def test_nc_list_empty_and_missing_graceful():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            assert ncds._list_dirs(root) == []                      # 빈 디렉토리
        assert ncds._list_dirs("/no/such/path/xyzzy") == []         # 없는 경로
        with tempfile.NamedTemporaryFile() as f:
            assert ncds._list_dirs(f.name) == []                    # 파일 → 빈
    finally:
        await teardown(srv, task, sock)


async def test_nc_ancestor_chain_root_to_cwd():
    srv, task, sock = await server_only()
    try:
        if os.name == "nt":
            # Windows: 드라이브 루트(C:\)까지 거슬러 올라가고 절대경로로 정규화된다.
            assert ncds._ancestor_chain("C:\\a\\b") == ["C:\\", "C:\\a", "C:\\a\\b"]
        else:
            assert ncds._ancestor_chain("/a/b/c") == ["/", "/a", "/a/b", "/a/b/c"]
            assert ncds._ancestor_chain("/") == ["/"]
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
            msg = ncds.nc_list_msg(srv, sess, None)
            assert msg["t"] == "nc_list" and msg["path"] is None
            assert msg["cwd"] == os.path.abspath(cwd)
            chain = msg["chain"]
            paths = [c[0] for c in chain]
            assert paths[-1] == os.path.abspath(cwd), paths
            if os.name == "nt":
                # Windows: 맨 앞 합성 최상위('')[드라이브 묶음] 다음부터 실제 조상 사슬.
                assert paths[0] == "" and paths[1:] == ncds._ancestor_chain(cwd), paths
            else:
                assert paths[0] == "/", paths
                assert paths == ncds._ancestor_chain(cwd)   # 순서·연속성 일치
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
            chain = ncds.nc_list_msg(srv, sess, None)["chain"]
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
            msg = ncds.nc_list_msg(srv, sess=srv.ensure_default_session(80, 24),
                                   path=sub)
            assert os.path.abspath(msg["path"]) == os.path.abspath(sub)
            assert [os.path.basename(p) for p in msg["dirs"]] == ["child"]
    finally:
        await teardown(srv, task, sock)


async def test_nc_drive_roots_empty_on_posix():
    srv, task, sock = await server_only()
    try:
        if os.name != "nt":
            assert ncds._drive_roots() == []
    finally:
        await teardown(srv, task, sock)


async def test_nc_build_chain_prepends_drives():
    """Windows: 드라이브 목록을 합성 최상위('')의 자식으로 맨 앞에 둔다(드라이브 전환)."""
    srv, task, sock = await server_only()
    orig = ncds._list_dirs
    try:
        # _build_chain 은 모듈 함수 _list_dirs 를 부른다 — 픽스처로 잠시 교체(복원).
        ncds._list_dirs = lambda p: {
            "C:\\": ["C:\\Users", "C:\\Windows"],
            "C:\\Users": ["C:\\Users\\me"]}.get(p, [])
        chain = ncds._build_chain(["C:\\", "C:\\Users"], ["C:\\", "D:\\"])
        assert chain[0] == ["", ["C:\\", "D:\\"]], chain[0]   # 드라이브 = 최상위 노드
        assert chain[1][0] == "C:\\" and "C:\\Users" in chain[1][1]
        assert chain[2][0] == "C:\\Users"
        # 현재 드라이브가 목록에 없으면 보강
        c2 = ncds._build_chain(["C:\\"], ["D:\\"])
        assert "C:\\" in c2[0][1] and "D:\\" in c2[0][1]
        # 비-Windows(드라이브 없음): 합성 최상위 없이 그대로
        c3 = ncds._build_chain(["/", "/Users"], [])
        assert c3[0][0] == "/"
    finally:
        ncds._list_dirs = orig
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
        from pytmuxlib.plugins.ncd.screen import NcdScreen
        app.send_cmd = lambda *a, **k: None
        inp = []
        app.send_input = lambda data: inp.append(data)
        app._run_command("ncd")
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        scr = app.screen
        assert isinstance(scr, NcdScreen)
        # 사슬이 펼쳐져 4행, 커서는 cwd(/r/sub)
        assert len(scr._view._rows) == 4, scr._view._rows
        assert scr._view._cur() == "/r/sub"
        await pilot.press("enter")
        await pilot.pause(0.1)
        # Windows(cmd.exe)는 `cd /d "..."`(드라이브 전환), 그 외엔 POSIX `cd ...`.
        exp = b'cd /d "/r/sub"\n' if os.name == "nt" else b"cd /r/sub\n"
        assert inp == [exp], inp
        assert not isinstance(app.screen, NcdScreen), "닫힘"
    await _with_app(body)


async def test_ncd_marks_current_dir():
    # ncd 실행 시 현재 디렉토리(cwd)를 가리킨다 — cwd 행에 ◀ 마커 + 노랑 강조,
    # 커서를 다른 행으로 옮겨도 cwd 행은 계속 강조돼 어디가 현재 위치인지 보인다.
    async def body(app, pilot, srv):
        from rich.color import ColorTriplet
        from pytmuxlib.plugins.ncd.screen import _CWD
        app.send_cmd = lambda *a, **k: None
        app._run_command("ncd")
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        v = app.screen._view
        assert v._cur() == "/r/sub"                 # 커서가 처음엔 cwd 에
        i = next(idx for idx, (p, _d) in enumerate(v._rows) if p == "/r/sub")
        # cwd 행 텍스트에 ◀ 마커
        assert "◀" in v._row_text("/r/sub", v._rows[i][1]), v._row_text(
            "/r/sub", v._rows[i][1])
        # 커서를 루트로 옮겨도 cwd 행은 노랑(_CWD)으로 강조된다
        v._sel = 0
        seg = v.render_line(i - v._top)._segments[0]
        assert seg.style.color.get_truecolor() == \
            _CWD.color.get_truecolor() == ColorTriplet(255, 255, 85), seg.style
    await _with_app(body)


async def test_cd_command_windows_neutralizes_quote_break():
    # M4(SECURITY_REVIEW §8): Windows `cd /d "..."` 가 임베드 따옴표·제어문자를 제거해
    # 따옴표 탈출 후 명령 분리(`" & calc`)를 못 하게 한다. raw 보간 회귀 방지.
    from pytmuxlib.plugins.ncd import _cd_command
    out = _cd_command('a" & calc &"b', nt=True)
    assert out == 'cd /d "a & calc &b"\n', out
    assert '" ' not in out and "&\"" not in out
    # 개행 주입도 차단(한 줄만).
    assert _cd_command("a\nrm -rf x", nt=True) == 'cd /d "arm -rf x"\n'
    # 정상 경로(따옴표 없음)는 불변.
    assert _cd_command(r"C:\Users\me\proj", nt=True) == 'cd /d "C:\\Users\\me\\proj"\n'
    # POSIX 분기는 shlex.quote 그대로.
    assert _cd_command("/r/a b", nt=False) == "cd '/r/a b'\n"


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
        # Windows 는 `cd /d "..."`(공백 포함 경로는 따옴표), 그 외엔 POSIX shlex 인용.
        exp = b'cd /d "/r/a b"\n' if os.name == "nt" else b"cd '/r/a b'\n"
        assert inp == [exp], inp
    await _with_app(body)


async def test_ncd_shift_enter_and_ctrl_o_split_with_path():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.ncd.screen import NcdScreen
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
        await app.screen._view.on_key(Key("shift+enter", None))
        await pilot.pause(0.1)
        assert ("split", {"orient": "lr", "path": "/r/sub"}) in sent
    await _with_app(body)


async def test_ncd_speed_search_jumps_by_typing():
    async def body(app, pilot, srv):
        app.send_cmd = lambda *a, **k: None
        app._run_command("ncd")
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        v = app.screen._view
        assert v._cur() == "/r/sub"
        await pilot.press("o")              # 'other' 로 점프(speed search)
        await pilot.pause(0.1)
        assert v._find == "o"
        assert v._cur() == "/r/other", v._cur()
        # 방향키 누르면 검색어 리셋
        await pilot.press("up")
        await pilot.pause(0.05)
        assert v._find == ""
    await _with_app(body)


async def test_ncd_right_expands_via_lazy_load():
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("ncd"); sent.clear()
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        v = app.screen._view
        v._sel = 3                             # /r/other (접힘·미로드)
        await pilot.press("right")
        await pilot.pause(0.1)
        assert ("request_nc_list", {"path": "/r/other"}) in sent
        app._dispatch({"t": "nc_list", "root": "/r/other", "path": "/r/other",
                       "dirs": ["/r/other/y"]})
        await pilot.pause(0.15)
        assert "/r/other" in v._expanded
        assert len(v._rows) == 5               # +/r/other/y
    await _with_app(body)


async def test_ncd_left_collapses():
    async def body(app, pilot, srv):
        app.send_cmd = lambda *a, **k: None
        app._run_command("ncd")
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        v = app.screen._view
        v._sel = 1                             # /r/sub (펼쳐져 있음)
        assert len(v._rows) == 4
        await pilot.press("left")              # 접기 → /r/sub/x 사라짐
        await pilot.pause(0.1)
        assert "/r/sub" not in v._expanded
        assert len(v._rows) == 3
    await _with_app(body)


async def test_ncd_page_home_end_fast_jumps():
    """ssh 빠른 탐색: Home/End/PageUp/PageDown 로 리페인트 1번에 멀리 점프."""
    async def body(app, pilot, srv):
        app.send_cmd = lambda *a, **k: None
        big = [f"/r/d{i:02d}" for i in range(40)]
        app._run_command("ncd")
        app._dispatch({"t": "nc_list", "root": "/", "path": None,
                       "cwd": "/r/d00",
                       "chain": [["/", ["/r"]], ["/r", big]]})
        await pilot.pause(0.1)
        v = app.screen._view
        n = len(v._rows)
        assert n == 41, n                       # /r + d00..d39
        await pilot.press("end")
        await pilot.pause(0.05)
        assert v._sel == n - 1, "End → 마지막"
        await pilot.press("home")
        await pilot.pause(0.05)
        assert v._sel == 0, "Home → 처음"
        await pilot.press("pagedown")
        await pilot.pause(0.05)
        assert v._sel > 1, ("PageDown 점프", v._sel)
        prev = v._sel
        await pilot.press("pageup")
        await pilot.pause(0.05)
        assert v._sel < prev, "PageUp 되돌림"
    await _with_app(body)


async def test_ncd_uses_norton_blue_palette():
    """과거 NCD/Norton 팔레트: 패널 DOS 블루(#0000aa), 현재 항목 시안 막대."""
    async def body(app, pilot, srv):
        from rich.color import ColorTriplet
        from pytmuxlib.plugins.ncd.screen import _BG, _SEL
        app.send_cmd = lambda *a, **k: None
        app._run_command("ncd")
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        box = app.screen.query_one("#ncbox")
        assert box.styles.background.hex.lower() == "#0000aa", \
            box.styles.background.hex
        # 팔레트 상수: 패널 #0000aa, 선택 막대 #00aaaa
        assert _BG.bgcolor.get_truecolor() == ColorTriplet(0, 0, 170)
        assert _SEL.bgcolor.get_truecolor() == ColorTriplet(0, 170, 170)
        # 선택 행이 실제로 시안 계열로 렌더된다(포커스=#00aaaa, 비포커스=#008b8b)
        v = app.screen._view
        seg = v.render_line(v._sel - v._top)._segments[0]
        assert seg.style.bgcolor.get_truecolor() in (
            ColorTriplet(0, 170, 170), ColorTriplet(0, 139, 139)), \
            seg.style.bgcolor
    await _with_app(body)


async def test_ncd_windows_drives_at_top_and_switch():
    """Windows: 드라이브 문자들이 트리 최상위 노드로 보이고, 다른 드라이브를 골라
    Enter 하면 그 드라이브로 cd(전환)된다."""
    async def body(app, pilot, srv):
        app.send_cmd = lambda *a, **k: None
        inp = []
        app.send_input = lambda d: inp.append(d)
        msg = {"t": "nc_list", "root": "", "path": None, "cwd": "C:\\Users",
               "chain": [["", ["C:\\", "D:\\"]],
                         ["C:\\", ["C:\\Users", "C:\\Windows"]],
                         ["C:\\Users", ["C:\\Users\\me"]]]}
        app._run_command("ncd")
        app._dispatch(msg)
        await pilot.pause(0.1)
        v = app.screen._view
        top = [p for (p, d) in v._rows if d == 0]
        assert "C:\\" in top and "D:\\" in top, v._rows   # 드라이브 = 최상위
        assert v._cur() == "C:\\Users"                    # cwd 선택
        await pilot.press("d")                            # speed search → D:\
        await pilot.pause(0.05)
        assert v._cur() == "D:\\", v._cur()
        await pilot.press("enter")                        # 그 드라이브로 cd
        await pilot.pause(0.05)
        assert inp and b"D:" in inp[0], inp
    await _with_app(body)


async def test_ncd_cd_command_windows_uses_slash_d():
    """Windows(cmd.exe)에선 cd /d 로 드라이브까지 전환, 그 외엔 POSIX cd+인용."""
    from pytmuxlib.plugins.ncd import _cd_command as _ncd_cd_command
    assert _ncd_cd_command("D:\\Users", nt=True) == 'cd /d "D:\\Users"\n'
    assert _ncd_cd_command("/r/a b", nt=False) == "cd '/r/a b'\n"


async def test_ncd_esc_closes():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.ncd.screen import NcdScreen
        app.send_cmd = lambda *a, **k: None
        app._run_command("ncd")
        app._dispatch(dict(_CHAIN_MSG))
        await pilot.pause(0.1)
        assert isinstance(app.screen, NcdScreen)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, NcdScreen)
    await _with_app(body)


# ---- 서버: 재귀 검색(트리에 안 열린 디렉토리까지) ----
async def test_nc_find_msg_locates_unopened_dir():
    """nc_find_msg 가 트리에 안 열린 깊은 디렉토리를 재귀로 찾아 target+조상 사슬을
    낸다(요청 2026-06-16). 못 찾으면 target=None."""
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "alpha", "child", "needledir"))
            os.makedirs(os.path.join(root, "Beta"))
            r = ncds.nc_find_msg(srv, None, "needle", root)
            assert r["t"] == "nc_found"
            assert os.path.basename(r["target"]) == "needledir", r
            # 사슬 = root..needledir 의 부모(child)까지, 각 단계 직계하위 포함.
            cp = [os.path.normpath(c[0]) for c in r["chain"]]
            assert os.path.normpath(root) == cp[0]
            assert os.path.normpath(os.path.join(root, "alpha", "child")) in cp
            # 접두 일치 우선(needledir 가 'needle' 로 시작).
            assert os.path.basename(r["target"]).startswith("needle")
            # 못 찾으면 None.
            assert ncds.nc_find_msg(srv, None, "zzznope", root)["target"] is None
    finally:
        await teardown(srv, task, sock)


# ---- 클라: speed search 가 안 열린 디렉토리까지 찾고 펼침 ----
async def test_ncd_speed_search_finds_unopened_and_expands():
    """보이는 트리에 없는 이름을 타이핑하면 서버 재귀 검색을 요청하고(요청), 회신된
    매치 경로까지 조상을 펼친 뒤 그 행을 선택한다."""
    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("ncd"); sent.clear()
        app._dispatch(dict(_CHAIN_MSG))      # rows: /r,/r/sub,/r/sub/x,/r/other
        await pilot.pause(0.1)
        v = app.screen._view
        for ch in "deep":                    # 보이는 행에 없는 이름
            await pilot.press(ch)
        await pilot.pause(0.1)
        assert ("request_nc_find", {"query": "deep", "root": "/"}) in sent, sent
        # 서버가 깊은 매치를 회신 → 트리 펼침 + 선택.
        app._dispatch({"t": "nc_found", "query": "deep", "target": "/r/other/deep",
                       "chain": [["/r/other", ["/r/other/deep"]]]})
        await pilot.pause(0.1)
        assert "/r/other" in v._expanded
        assert v._cur() == "/r/other/deep", v._cur()
        # 그새 query 가 바뀌었으면 stale 결과는 무시(엉뚱한 점프 방지).
        v._find = "zzz"
        v.apply_found("deep", "/r/sub/x", [])
        assert v._cur() == "/r/other/deep", "stale 결과 무시"
    await _with_app(body)


# ---- 클라: 마우스 휠 스크롤 ----
async def test_ncd_mouse_wheel_scrolls_list():
    """마우스 휠 위/아래로 디렉토리 목록이 스크롤된다(요청). 선택은 뷰포트 안에 유지."""
    from types import SimpleNamespace

    async def body(app, pilot, srv):
        app.send_cmd = lambda *a, **k: None
        app._run_command("ncd")
        big = {"t": "nc_list", "root": "/", "path": None, "cwd": "/r",
               "chain": [["/", ["/r"]],
                         ["/r", [f"/r/d{i:02d}" for i in range(40)]]]}
        app._dispatch(big)
        await pilot.pause(0.1)
        v = app.screen._view
        assert len(v._rows) > v.size.height, "스크롤이 의미있으려면 행>뷰포트"
        ev = SimpleNamespace(stop=lambda: None)
        top0 = v._top
        v.on_mouse_scroll_down(ev)
        assert v._top > top0, (top0, v._top)           # 아래로
        assert v._top <= v._sel < v._top + v.size.height  # 선택은 뷰포트 안
        mid = v._top
        v.on_mouse_scroll_up(ev)
        assert v._top < mid, (mid, v._top)             # 위로
    await _with_app(body)
