"""mdir(Mdir III 풍 파일 관리자) 플러그인 테스트.

서버 측: `list_entries`(플래그·graceful)·`mdir_list_msg`(초기=패널 cwd, 탐색=명시
경로, 비디렉토리=err). 클라 측: 명령·모달·다열 리스트 구성·탐색(Enter/`.`/BS/`\\`)·
빨리찾기·F4(패널 cd)·⇧Enter(새 패널)·Esc 를 Textual headless(run_test)로 검증."""
import os
import tempfile

from harness import make_app, server_only, teardown, wait_until

import pytmuxlib.plugins.mdir.server as mds
from pytmuxlib.plugins.mdir import _cd_command


# ---- 서버: 목록 ----
async def test_mdir_list_entries_flags():
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "sub"))
        with open(os.path.join(root, "a.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(root, ".secret"), "w") as f:
            f.write("x")
        entries, err, over = mds.list_entries(root)
        assert err is None and over is False
        by = {e["n"]: e for e in entries}
        assert by["sub"]["d"] is True and by["sub"]["s"] == 0
        assert by["a.txt"]["d"] is False and by["a.txt"]["s"] == 5
        assert by["a.txt"]["m"] > 0
        assert by[".secret"]["h"] is True          # 숨김 플래그(제외 아님 — 클라 토글)
        assert by["a.txt"]["h"] is False
        if os.name != "nt":
            sh = os.path.join(root, "run.sh")
            with open(sh, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(sh, 0o755)
            ro = os.path.join(root, "ro.txt")
            with open(ro, "w") as f:
                f.write("x")
            os.chmod(ro, 0o444)
            entries, _e, _o = mds.list_entries(root)
            by = {e["n"]: e for e in entries}
            assert by["run.sh"]["x"] is True       # 실행비트 → 초록 표시용
            assert by["ro.txt"]["ro"] is True      # 읽기전용


async def test_mdir_list_entries_graceful_on_bad_path():
    entries, err, over = mds.list_entries("/no/such/dir/really")
    assert entries == [] and err and over is False


async def test_mdir_list_entries_limit():
    with tempfile.TemporaryDirectory() as root:
        for i in range(12):
            with open(os.path.join(root, f"f{i:02}"), "w") as f:
                f.write("x")
        entries, err, over = mds.list_entries(root, limit=5)
        assert len(entries) == 5 and over is True and err is None


class _FakeServer:
    """mdir_list_msg 의 초기 진입 분기(_resolve_start_cwd)만 흉내낸다."""
    def __init__(self, cwd):
        self._cwd = cwd

    def _resolve_start_cwd(self, sess, mode):
        return self._cwd


async def test_mdir_list_msg_initial_uses_pane_cwd():
    with tempfile.TemporaryDirectory() as root:
        msg = mds.mdir_list_msg(_FakeServer(root), None, None)
        assert msg["t"] == "mdir_list"
        assert msg["path"] == os.path.abspath(root)
        assert msg["nt"] == (os.name == "nt")
        assert msg["total"] > 0 and msg["free"] > 0    # 디스크 용량(하단 집계줄)
        assert msg["err"] is None


async def test_mdir_list_msg_explicit_path_and_err():
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "sub"))
        msg = mds.mdir_list_msg(None, None, os.path.join(root, "sub"))
        assert msg["path"] == os.path.abspath(os.path.join(root, "sub"))
        # 비디렉토리 → 빈 목록 + err(화면은 유지한 채 오류 표시)
        bad = mds.mdir_list_msg(None, None, os.path.join(root, "nope"))
        assert bad["entries"] == [] and bad["err"]


# ---- 서버: 파일 조작(request_mdir_op) ----
def _w(path, content="x"):
    with open(path, "w") as f:
        f.write(content)


async def test_mdir_op_copy_conflict_two_phase():
    with tempfile.TemporaryDirectory() as root:
        src, dst = os.path.join(root, "s"), os.path.join(root, "d")
        os.makedirs(src)
        os.makedirs(dst)
        _w(os.path.join(src, "a.txt"), "AAA")
        _w(os.path.join(src, "b.txt"), "B")
        _w(os.path.join(dst, "a.txt"), "BBB")
        srcs = [os.path.join(src, "a.txt"), os.path.join(src, "b.txt")]
        # 1차(ask): 충돌 회신, **아무것도 수행 안 함**(b.txt 도 안 복사).
        r1 = mds.mdir_op_msg(None, None, {"op": "copy", "src": srcs,
                                          "dst": dst, "overwrite": "ask"})
        assert r1["conflicts"] == ["a.txt"] and r1["done"] == 0
        assert open(os.path.join(dst, "a.txt")).read() == "BBB"
        assert not os.path.exists(os.path.join(dst, "b.txt"))
        # 2차(all): 덮어쓰고 전부 수행.
        r2 = mds.mdir_op_msg(None, None, {"op": "copy", "src": srcs,
                                          "dst": dst, "overwrite": "all"})
        assert r2["done"] == 2 and r2["failed"] == [], r2
        assert open(os.path.join(dst, "a.txt")).read() == "AAA"
        # 3차(skip): 충돌 항목만 건너뛰고 나머지 수행.
        dst2 = os.path.join(root, "d2")
        os.makedirs(dst2)
        _w(os.path.join(dst2, "a.txt"), "KEEP")
        r3 = mds.mdir_op_msg(None, None, {"op": "copy", "src": srcs,
                                          "dst": dst2, "overwrite": "skip"})
        assert r3["done"] == 1, r3
        assert open(os.path.join(dst2, "a.txt")).read() == "KEEP"
        assert os.path.exists(os.path.join(dst2, "b.txt"))


async def test_mdir_op_copy_dir_tree_and_into_self_guard():
    with tempfile.TemporaryDirectory() as root:
        sub = os.path.join(root, "sub")
        os.makedirs(os.path.join(sub, "inner"))
        _w(os.path.join(sub, "inner", "f.txt"), "F")
        dst = os.path.join(root, "d")
        os.makedirs(dst)
        r = mds.mdir_op_msg(None, None, {"op": "copy", "src": [sub],
                                         "dst": dst, "overwrite": "ask"})
        assert r["done"] == 1 and r["failed"] == []
        assert open(os.path.join(dst, "sub", "inner", "f.txt")).read() == "F"
        # 자기 하위로 복사 거부.
        r2 = mds.mdir_op_msg(None, None, {"op": "copy", "src": [sub],
                                          "dst": os.path.join(sub, "inner"),
                                          "overwrite": "ask"})
        assert r2["done"] == 0 and r2["failed"][0][1] == "into_self", r2


async def test_mdir_op_move_and_dir_overwrite_guard():
    with tempfile.TemporaryDirectory() as root:
        dst = os.path.join(root, "d")
        os.makedirs(dst)
        _w(os.path.join(root, "f.txt"), "F")
        r = mds.mdir_op_msg(None, None, {"op": "move",
                                         "src": [os.path.join(root, "f.txt")],
                                         "dst": dst, "overwrite": "ask"})
        assert r["done"] == 1
        assert not os.path.exists(os.path.join(root, "f.txt"))
        assert open(os.path.join(dst, "f.txt")).read() == "F"
        # 파일→파일 덮어쓰기 이동(all)은 원자 교체.
        _w(os.path.join(root, "f.txt"), "NEW")
        r2 = mds.mdir_op_msg(None, None, {"op": "move",
                                          "src": [os.path.join(root, "f.txt")],
                                          "dst": dst, "overwrite": "all"})
        assert r2["done"] == 1
        assert open(os.path.join(dst, "f.txt")).read() == "NEW"
        # 동명 디렉토리가 끼는 덮어쓰기 이동은 거부(병합 의미 모호).
        os.makedirs(os.path.join(root, "sub"))
        os.makedirs(os.path.join(dst, "sub"))
        r3 = mds.mdir_op_msg(None, None, {"op": "move",
                                          "src": [os.path.join(root, "sub")],
                                          "dst": dst, "overwrite": "all"})
        assert r3["done"] == 0 and r3["failed"][0][1] == "dir_overwrite", r3


async def test_mdir_op_delete_recursive_and_root_guard():
    with tempfile.TemporaryDirectory() as root:
        sub = os.path.join(root, "sub")
        os.makedirs(os.path.join(sub, "deep"))
        _w(os.path.join(sub, "deep", "f.txt"))
        _w(os.path.join(root, "g.txt"))
        r = mds.mdir_op_msg(None, None, {
            "op": "delete", "src": [sub, os.path.join(root, "g.txt")]})
        assert r["done"] == 2 and r["failed"] == []
        assert not os.path.exists(sub) and not os.path.exists(
            os.path.join(root, "g.txt"))
        # 루트/드라이브 루트는 서버가 거부(클라 확인과 별개의 최소 방어).
        r2 = mds.mdir_op_msg(None, None, {"op": "delete", "src": ["/"]})
        assert r2["done"] == 0 and r2["failed"][0][1] == "root"
        assert mds._is_fs_root("C:\\") and mds._is_fs_root("/") \
            and not mds._is_fs_root("/tmp")


async def test_mdir_op_rename_and_mkdir():
    with tempfile.TemporaryDirectory() as root:
        _w(os.path.join(root, "a.txt"), "A")
        r = mds.mdir_op_msg(None, None, {"op": "rename",
                                         "src": [os.path.join(root, "a.txt")],
                                         "dst": "b.txt"})
        assert r["done"] == 1
        assert open(os.path.join(root, "b.txt")).read() == "A"
        # 기존 이름과 충돌 → exists.
        _w(os.path.join(root, "c.txt"))
        r2 = mds.mdir_op_msg(None, None, {"op": "rename",
                                          "src": [os.path.join(root, "c.txt")],
                                          "dst": "b.txt"})
        assert r2["done"] == 0 and r2["failed"][0][1] == "exists"
        # 경로 구분자 든 이름 거부.
        r3 = mds.mdir_op_msg(None, None, {"op": "rename",
                                          "src": [os.path.join(root, "c.txt")],
                                          "dst": "x/y"})
        assert r3["failed"][0][1] == "bad_name"
        # mkdir 정상 + 중복 exists.
        r4 = mds.mdir_op_msg(None, None, {"op": "mkdir", "base": root,
                                          "dst": "newdir"})
        assert r4["done"] == 1 and os.path.isdir(os.path.join(root, "newdir"))
        r5 = mds.mdir_op_msg(None, None, {"op": "mkdir", "base": root,
                                          "dst": "newdir"})
        assert r5["done"] == 0 and r5["failed"][0][1] == "exists"


async def test_mdir_cd_command_dialects_and_quote_defense():
    # ncd 와 동일 규율의 사본 — Windows 임베드 따옴표·개행 무력화, POSIX shlex.
    assert _cd_command("/r/a b", nt=False) == "cd '/r/a b'\n"
    assert _cd_command(r"C:\Users\me", nt=True) == 'cd /d "C:\\Users\\me"\n'
    assert _cd_command('a" & calc &"b', nt=True) == 'cd /d "a & calc &b"\n'
    assert _cd_command("a\nrm -rf x", nt=True) == 'cd /d "arm -rf x"\n'


# ---- 클라이언트(Textual headless) ----
async def _with_app(coro, size=(120, 32)):
    srv, task, sock = await server_only()
    app = make_app(sock)
    try:
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.4)
            await coro(app, pilot, srv)
    finally:
        await teardown(srv, task, sock)


def _e(n, d=False, s=0, m=1000000, h=False, ro=False, x=False):
    return {"n": n, "d": d, "s": s, "m": m, "h": h, "ro": ro, "x": x}


_ENTS = [
    _e("zeta", d=True), _e("sub", d=True),
    _e("b.txt", s=10), _e("a.txt", s=5), _e(".secret", h=True),
]


def _msg(path="/r", entries=None, drives=None, nt=False):
    return {"t": "mdir_list", "path": path,
            "entries": _ENTS if entries is None else entries,
            "drives": drives or [], "free": 500, "total": 1000,
            "nt": nt, "over": False, "err": None}


def _names(view):
    return [view._item_name(it) for it in view._items]


async def test_mdir_command_requests_list():
    async def body(app, pilot, srv):
        for name in ("mdir", "m"):
            sent = []
            app.send_cmd = lambda action, **kw: sent.append((action, kw))
            app._run_command(name)
            assert app._want_mdir is True
            assert sent == [("request_mdir_list", {"path": None})], name
    await _with_app(body)


async def test_mdir_opens_with_sorted_items_hidden_filtered():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen
        app.send_cmd = lambda *a, **k: None
        app._run_command("mdir")
        app._dispatch(_msg())
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        v = app.screen._view
        # 순서: `..` → 디렉토리(이름순) → 파일(이름순). 숨김은 기본 제외.
        assert _names(v) == ["..", "sub", "zeta", "a.txt", "b.txt"], _names(v)
        assert v._idx == 0 and v._path == "/r"
    await _with_app(body)


async def test_mdir_enter_dir_navigates_and_applies():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("mdir")
        app._dispatch(_msg())
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        await pilot.press("down")                  # `..` → sub
        sent.clear()
        await pilot.press("enter")
        assert sent == [("request_mdir_list", {"path": "/r/sub"})], sent
        # 응답 적용 → 같은 화면의 경로·목록 갱신(새 화면을 또 열지 않음)
        scr = app.screen
        app._dispatch(_msg(path="/r/sub", entries=[_e("inner.txt", s=1)]))
        assert await wait_until(pilot, lambda: scr._view._path == "/r/sub")
        assert app.screen is scr
        assert _names(scr._view) == ["..", "inner.txt"]
    await _with_app(body)


async def test_mdir_dot_parent_selects_child_dir():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("mdir")
        app._dispatch(_msg(path="/r/sub", entries=[_e("inner.txt", s=1)]))
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        sent.clear()
        await pilot.press("full_stop")             # `.` = 상위 디렉토리
        assert sent == [("request_mdir_list", {"path": "/r"})], sent
        app._dispatch(_msg())
        v = app.screen._view
        # 원조 감각: 상위로 오르면 커서는 방금 나온 디렉토리(sub) 위에.
        assert await wait_until(
            pilot, lambda: v._path == "/r" and v._item_name(v._items[v._idx]) == "sub")
    await _with_app(body)


async def test_mdir_backspace_parent_and_backslash_root():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("mdir")
        app._dispatch(_msg(path="/r/sub/deep", entries=[]))
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        sent.clear()
        await pilot.press("backspace")             # 빨리찾기 비었을 때 BS=상위
        assert sent == [("request_mdir_list", {"path": "/r/sub"})], sent
        sent.clear()
        await pilot.press("backslash")             # `\` = 루트
        assert sent == [("request_mdir_list", {"path": "/"})], sent
    await _with_app(body)


async def test_mdir_f4_cds_pane_and_closes():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen
        app.send_cmd = lambda *a, **k: None
        inp = []
        app.send_input = lambda data: inp.append(data)
        app._run_command("mdir")
        app._dispatch(_msg(path="/r/a b"))         # 공백 경로 — 인용 확인 겸
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        await pilot.press("f4")
        # nt 는 서버발(fixture nt=False) → 클라 OS 와 무관하게 POSIX 방언.
        assert await wait_until(pilot, lambda: inp == [b"cd '/r/a b'\n"]), inp
        assert not isinstance(app.screen, MdirScreen), "닫힘"
    await _with_app(body)


async def test_mdir_shift_enter_splits_at_cursor_dir():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("mdir")
        app._dispatch(_msg())
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        await pilot.press("down")                  # 커서 → sub(디렉토리)
        sent.clear()
        await pilot.press("shift+enter")
        assert await wait_until(
            pilot, lambda: ("split", {"orient": "lr", "path": "/r/sub"}) in sent), sent
        assert not isinstance(app.screen, MdirScreen)
    await _with_app(body)


async def test_mdir_esc_cancels_without_side_effect():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen
        app.send_cmd = lambda *a, **k: None
        inp = []
        app.send_input = lambda data: inp.append(data)
        app._run_command("mdir")
        app._dispatch(_msg())
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        await pilot.press("escape")
        assert await wait_until(
            pilot, lambda: not isinstance(app.screen, MdirScreen))
        assert inp == []
    await _with_app(body)


async def test_mdir_speed_search_and_esc_clears_find_first():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen
        app.send_cmd = lambda *a, **k: None
        app._run_command("mdir")
        app._dispatch(_msg())
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        v = app.screen._view
        await pilot.press("z")                     # 접두 일치 → zeta(디렉토리)
        assert v._find == "z" and v._item_name(v._items[v._idx]) == "zeta"
        await pilot.press("b")                     # "zb" 접두 없음 → 부분일치도 없음
        assert v._item_name(v._items[v._idx]) == "zeta"   # 제자리(점프 안 함)
        await pilot.press("escape")                # 1차 Esc = 찾기만 해제
        assert v._find == "" and isinstance(app.screen, MdirScreen)
        await pilot.press("escape")                # 2차 Esc = 닫기
        assert await wait_until(
            pilot, lambda: not isinstance(app.screen, MdirScreen))
    await _with_app(body)


async def test_mdir_space_tags_and_all_toggle():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen
        app.send_cmd = lambda *a, **k: None
        app._run_command("mdir")
        app._dispatch(_msg())
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        v = app.screen._view
        await pilot.press("down")                  # → sub
        await pilot.press("space")                 # 태그 + 커서 아래로
        assert v._tags == {"sub"}
        assert v._item_name(v._items[v._idx]) == "zeta"
        await pilot.press("space")
        assert v._tags == {"sub", "zeta"}
        await pilot.press("asterisk")              # 반전 → 파일 2개만
        assert v._tags == {"a.txt", "b.txt"}, v._tags
        v._tag_all_toggle()                        # 일부 태그 → 전체? (비어있지 않으면 해제)
        assert v._tags == set()
        v._tag_all_toggle()
        assert v._tags == {"sub", "zeta", "a.txt", "b.txt"}
    await _with_app(body)


async def test_mdir_copy_flow_prompt_sends_op():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen, MdirPrompt
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("mdir")
        app._dispatch(_msg())
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        v = app.screen._view
        await pilot.press("down", "space")         # sub 태그
        sent.clear()
        await pilot.press("f5")                    # 복사(F5=⎇C 별칭)
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirPrompt))
        await pilot.press("enter")                 # 프리필(현재 경로) 그대로 확정
        assert await wait_until(pilot, lambda: sent != []), "op 미전송"
        assert sent == [("request_mdir_op",
                         {"op": "copy", "src": ["/r/sub"], "dst": "/r",
                          "overwrite": "ask"})], sent
        assert v._pending_op == {"op": "copy", "src": ["/r/sub"], "dst": "/r",
                                 "overwrite": "ask"}
    await _with_app(body)


async def test_mdir_delete_confirm_defaults_to_cancel():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen, MdirConfirm
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("mdir")
        app._dispatch(_msg())
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        await pilot.press("end")                   # 마지막 파일(b.txt)
        sent.clear()
        await pilot.press("f8")                    # 삭제 확인 팝업
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirConfirm))
        await pilot.press("enter")                 # 기본 선택=취소 → 아무 일 없음
        assert await wait_until(
            pilot, lambda: isinstance(app.screen, MdirScreen))
        assert sent == [], "기본이 취소가 아님(Enter 연타 위험)"
        await pilot.press("f8")
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirConfirm))
        await pilot.press("left", "enter")         # ← 로 '삭제' 선택 후 확정
        assert await wait_until(pilot, lambda: sent != [])
        assert sent == [("request_mdir_op",
                         {"op": "delete", "src": ["/r/b.txt"]})], sent
    await _with_app(body)


async def test_mdir_conflict_confirm_resends_with_policy():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen, MdirConfirm
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("mdir")
        app._dispatch(_msg())
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        v = app.screen._view
        v._send_op(op="copy", src=["/r/a.txt"], dst="/x", overwrite="ask")
        sent.clear()
        # 서버가 충돌 회신 → [모두 덮어쓰기/건너뛰기/취소] 확인 팝업.
        app._dispatch({"t": "mdir_result", "op": "copy", "done": 0,
                       "failed": [], "conflicts": ["a.txt"]})
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirConfirm))
        await pilot.press("left", "left", "enter")   # 기본 취소 → ←← = 모두 덮어쓰기
        assert await wait_until(pilot, lambda: sent != [])
        assert sent == [("request_mdir_op",
                         {"op": "copy", "src": ["/r/a.txt"], "dst": "/x",
                          "overwrite": "all"})], sent
    await _with_app(body)


async def test_mdir_result_notice_and_refresh():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("mdir")
        app._dispatch(_msg())
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        v = app.screen._view
        sent.clear()
        app._dispatch({"t": "mdir_result", "op": "delete", "done": 2,
                       "failed": [], "conflicts": []})
        # 완료 공지 + 목록 재조회.
        assert v._notice and v._notice[0].startswith("삭제 2"), v._notice
        assert sent == [("request_mdir_list", {"path": "/r"})], sent
        # 실패 사유 코드는 클라가 번역해 공지에 싣는다(서버발 표면 규율).
        app._dispatch({"t": "mdir_result", "op": "rename", "done": 0,
                       "failed": [["b.txt", "exists"]], "conflicts": []})
        assert v._notice[1] is True and "같은 이름이 이미 있음" in v._notice[0], \
            v._notice
    await _with_app(body)


async def test_mdir_rename_prompt_prefill_and_mkdir():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen, MdirPrompt
        from textual.widgets import Input
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("mdir")
        app._dispatch(_msg())
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        await pilot.press("end", "up")             # a.txt (파일 첫째)
        v = app.screen._view
        assert v._item_name(v._items[v._idx]) == "a.txt"
        sent.clear()
        await pilot.press("f2")                    # 이름변경 — 현재 이름 프리필
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirPrompt))
        assert app.screen.query_one(Input).value == "a.txt"
        await pilot.press("escape")                # 취소 → op 없음
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        assert sent == []
        await pilot.press("f7")                    # 새 디렉토리
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirPrompt))
        await pilot.press("n", "e", "w", "enter")
        assert await wait_until(pilot, lambda: sent != [])
        assert sent == [("request_mdir_op",
                         {"op": "mkdir", "base": "/r", "dst": "new"})], sent
        assert v._pending_sel == "new"             # 생성 후 커서를 새 디렉토리로
    await _with_app(body)


async def test_mdir_drive_entry_enter_changes_drive():
    async def body(app, pilot, srv):
        from pytmuxlib.plugins.mdir.screen import MdirScreen
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("mdir")
        app._dispatch(_msg(drives=["C:\\", "D:\\"], nt=True))
        assert await wait_until(pilot, lambda: isinstance(app.screen, MdirScreen))
        v = app.screen._view
        # 드라이브 항목은 리스트 맨 끝(원조: 커서로 골라 Enter=드라이브 전환).
        assert _names(v)[-2:] == ["C:\\", "D:\\"]
        await pilot.press("end")
        sent.clear()
        await pilot.press("enter")
        assert sent == [("request_mdir_list", {"path": "D:\\"})], sent
    await _with_app(body)
