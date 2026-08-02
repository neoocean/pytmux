"""플러그인 화면 스펙(Tier C) — 서버가 **무엇을 그릴지**를 자료로 준다.

설계 = `docs/internal/PLUGIN_COMPAT_TEXTUAL_GUI_2026-08-01.md` §4.3 · §8-5 (P4).

# 왜 필요한가

`mdir`·`ncd`·`p4changes` 같은 대화형 플러그인은 화면이 **정본 클라의 Textual 위젯**이라
네이티브 클라에는 통째로 없었다 — P1 이 팔레트에 이름을 올린 뒤로는 "보이는데 눌러도
화면이 없다"가 됐다. 이 슬라이스는 그 화면을 **스펙**으로 옮긴다: 플러그인이 목록/글을
자료로 돌려주고 클라는 두 모양만 그릴 줄 알면 된다.

# 여기서 재는 것

1. 명령 표에 두 줄(`plugin_open`·`plugin_action`)이 있고 **요청한 클라에게만** 회신한다.
2. 아무 플러그인도 안 집으면 **조용히 끝나지 않는다**(알림 — 설계 §8-5).
3. 스펙의 모양이 계약대로다(목록 줄의 `key` 가 뜻을 나르고, 클라가 그것을 되돌려준다).
"""

import harness  # noqa: F401  (경로 설정)
from harness import server_only, teardown


def _server_module(plugin):
    """그 플러그인의 `server` 서브모듈. 이름에 하이픈이 있어 `import` 문으로는 못 쓴다
    (로더가 importlib 로 싣는다) — 스펙 함수가 호출 시점에 `from .server import …` 로
    읽으므로, 이 모듈의 속성을 갈아 끼우면 그대로 먹는다."""
    import importlib
    return importlib.import_module(type(plugin).__module__ + ".server")


class _Spy:
    """`_send_to` 가 이 클라에게 보낸 메시지를 모은다(회신 대상을 재는 자)."""

    def __init__(self):
        self.sent = []


async def _reply(srv, sess, action, msg):
    """명령 하나를 태우고 그 클라에게 간 메시지들을 돌려준다."""
    spy = _Spy()

    async def fake_send_to(self, client, obj):   # 클래스에 심으므로 self 를 받는다
        if client is spy:
            spy.sent.append(obj)
        return True

    from pytmuxlib.servercmd import _CMD_TABLE
    with harness.patched(type(srv), _send_to=fake_send_to):
        handler = _CMD_TABLE[action][0]
        await handler(srv, spy, sess, msg)
    return spy.sent


async def test_the_table_carries_the_two_screen_commands():
    """명령 표에 데이터로 선언돼 있나 — 클라의 적합성 게이트가 이 표를 본다.

    표에 없으면 서버는 그 action 을 플러그인 훅으로 넘기고 **조용히 끝낸다**(오류도
    로그도 없다). 클라 쪽 `command_conformance.rs` 가 정확히 그 침묵을 막는 게이트다.
    """
    srv, task, sock = await server_only()
    try:
        from pytmuxlib.servercmd import _CMD_TABLE
        for action in ("plugin_open", "plugin_action"):
            assert action in _CMD_TABLE, action
            # HANDLED = 핸들러가 응답을 완결한다(요청 클라에게만 회신).
            assert _CMD_TABLE[action][1] == "handled", action
    finally:
        await teardown(srv, task, sock)


async def test_an_unclaimed_name_gets_a_notice_not_silence():
    """아무 플러그인도 안 집으면 **알림**이 간다(설계 §8-5).

    조용한 누락이 이 저장소의 상습 결함이다 — 사용자에게는 "눌렀는데 아무 일도 안 남"
    으로 보이고, 그건 자기가 잘못 골랐다는 신호로 읽힌다.
    """
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        sent = await _reply(srv, sess, "plugin_open",
                            {"name": "그런-플러그인-없음", "args": []})
        assert len(sent) == 1, sent
        assert sent[0]["t"] == "notice", sent[0]
        # 서버발 표면은 t() 를 못 부른다 — 키와 인자를 함께 실어 클라가 번역한다.
        assert sent[0].get("key") == "msg.plugin_screen_missing", sent[0]
        assert sent[0]["kw"]["name"] == "그런-플러그인-없음", sent[0]
    finally:
        await teardown(srv, task, sock)


async def test_the_close_action_reaches_the_plugin_and_comes_back():
    """`do` 칸이 액션 이름이다 — `action` 은 명령 디스패처의 것이라 쓸 수 없다.

    (여기서 p4 를 실제로 부르지 않는 `close` 로 왕복을 잰다 — 이 상자에 P4PORT 가
    없어도 계약은 같다.)
    """
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        sent = await _reply(srv, sess, "plugin_action",
                            {"id": "p4changes", "do": "close"})
        assert sent and sent[0]["t"] == "plugin_screen_close", sent
        assert sent[0]["id"] == "p4changes", sent
    finally:
        await teardown(srv, task, sock)


async def test_the_list_spec_shape_is_the_contract():
    """목록 스펙의 모양 — 클라가 그리는 것과 되돌려주는 것이 여기 달렸다.

    p4 서브프로세스를 부르지 않으려고 데이터 생성 함수만 바꿔치기한다(스펙을 만드는
    코드가 이 테스트의 대상이지, p4 가 대상이 아니다).
    """
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        plugin = next(p for p in srv.plugins.plugins
                      if getattr(p, "name", "") == "p4-show-submitted-changelists")
        def fake_list(server, sess_, count, cwd):
            return {"t": "p4_changes", "info": "srv:1666", "err": None, "rows": [
                {"change": "68995", "desc": "플러그인 호환 P2", "user": "woojinkim",
                 "when": "2026/08/01", "client": "playground"},
            ]}
        srvmod = _server_module(plugin)
        with harness.patched(srvmod, list_changes_msg=fake_list):
            spec = plugin._changes_spec(srv, sess, 50, None)
        assert spec["t"] == "plugin_screen" and spec["kind"] == "list"
        assert spec["id"] == "p4changes"
        row = spec["rows"][0]
        # ★ `key` 는 **그 줄의 뜻**(CL 번호)이다 — 클라가 액션에 이것을 실어 보낸다.
        #   자리(번호)로 가리키면 목록이 바뀔 때 엉뚱한 CL 을 연다.
        assert row["key"] == "68995", row
        assert "플러그인 호환 P2" in row["label"], row
        assert row["cols"] == ["woojinkim", "2026/08/01"], row
        # 키 표에 있는 것만 클라가 되돌려준다.
        assert spec["keys"]["enter"] == "describe", spec
        # 빈 목록과 실패를 가르는 자리.
        assert spec["note"] == "", spec
    finally:
        await teardown(srv, task, sock)


async def test_an_empty_list_says_so_and_an_error_says_something_else():
    """빈 목록과 **실패**는 화면에서 달라야 한다 — 둘 다 "줄이 없다"로 보이면 사용자는
    p4 가 죽은 것을 모른다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        plugin = next(p for p in srv.plugins.plugins
                      if getattr(p, "name", "") == "p4-show-submitted-changelists")
        srvmod = _server_module(plugin)

        def empty(server, sess_, count, cwd):
            return {"t": "p4_changes", "rows": [], "err": None, "info": ""}

        def failed(server, sess_, count, cwd):
            return {"t": "p4_changes", "rows": [], "err": "p4: command not found",
                    "info": ""}

        with harness.patched(srvmod, list_changes_msg=empty):
            spec = plugin._changes_spec(srv, sess, 50, None)
        assert "없습니다" in spec["note"], spec
        with harness.patched(srvmod, list_changes_msg=failed):
            spec = plugin._changes_spec(srv, sess, 50, None)
        assert "command not found" in spec["note"], spec
    finally:
        await teardown(srv, task, sock)


async def test_the_screen_state_belongs_to_the_client_connection():
    """화면 상태는 **그 클라의 것**이다(설계 P5).

    서버 전역에 두면 두 클라가 같은 화면을 열었을 때 서로의 자리를 옮기고, 연결이 끊겨도
    남는다. `ClientConn.plugin_state` 에 매달아 수명을 연결에 묶는다.
    """
    from pytmuxlib.model import ClientConn
    a, b = ClientConn(None), ClientConn(None)
    assert a.plugin_state == {} and b.plugin_state == {}
    a.plugin_state.setdefault("ncd", {})["path"] = "/a"
    assert b.plugin_state == {}, "한 클라의 상태가 다른 클라에 샜다"


async def test_ncd_walks_with_state_and_cd_closes_the_screen():
    """`ncd` — 상태 있는 첫 시민. 들어가면 그 클라의 자리가 바뀌고, `cd` 는 화면을 닫는다.

    정본과 **모양이 다르다**(트리 vs 평면 목록)는 것이 설계 §6 이 그은 선이다 — 스펙은
    내용과 선택을 정하고 표현은 각 클라 관례를 따른다. 결과(어디로 cd 하나)는 같다.
    """
    import os
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        plugin = next(p for p in srv.plugins.plugins if getattr(p, "name", "") == "ncd")
        state = {}
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = plugin._dir_spec({"path": here})
        assert spec["kind"] == "list" and spec["id"] == "ncd"
        # 첫 줄은 **부모로 올라가는 길**이고, 그 뜻(부모 경로)이 key 에 실린다.
        assert spec["rows"][0]["label"] == ".."
        assert spec["rows"][0]["key"] == os.path.dirname(here)
        # 스펙이 자기 글자 키를 정한다(클라는 이 표에 있는 것만 먹는다).
        assert spec["keys"] == {"enter": "into", "c": "cd"}

        # `cd` 는 패널에 명령을 넣고 화면을 닫는다 — 정본 Enter 와 같은 결과다.
        pane = sess.active_window.active_pane
        wrote = []
        with harness.patched(type(pane), write=lambda self, data: wrote.append(data)):
            resp = plugin.plugin_screen(srv, sess, {
                "id": "ncd", "do": "cd", "input": here, "state": state,
            })
        assert resp == {"t": "plugin_screen_close", "id": "ncd"}
        assert wrote and here.encode() in wrote[0], wrote
        assert wrote[0].endswith(b"\r"), "Enter 없이 보내면 셸이 실행하지 않는다"
    finally:
        await teardown(srv, task, sock)


async def test_a_path_with_spaces_survives_the_shell():
    """공백·따옴표가 든 경로가 셸에서 쪼개지지 않는다."""
    from pytmuxlib.plugins.ncd import _quote
    assert _quote("/a b/c") == '"/a b/c"'
    assert _quote("/plain") == "/plain"


# ── P6 — `mdir`: 되돌릴 수 없는 조작이 있는 첫 시민 ──────────────────────────────

def _mdir():
    from pytmuxlib.plugins.mdir import PLUGIN
    return PLUGIN


def _tree(tmp):
    """표본 디렉터리 — 하위 하나 + 파일 둘 + 숨김 하나."""
    import os
    os.makedirs(os.path.join(tmp, "sub"), exist_ok=True)
    for name, body in (("a.txt", "가나다"), ("b.txt", "bbb"), (".hidden", "x")):
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
            f.write(body)
    return tmp


async def test_mdir_lists_a_directory_as_a_table_the_client_can_draw(tmp_path=None):
    """표 스펙의 모양이 계약이다 — 줄의 `key` 가 **절대경로**(그 줄의 뜻)라야 다음
    액션이 자리가 아니라 그 항목을 가리킨다."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        mine = {"path": tmp, "tags": []}
        spec = _mdir()._spec(mine, 0, "")
        assert spec["kind"] == "table" and spec["id"] == "mdir", spec
        labels = [r["label"] for r in spec["rows"]]
        assert labels[0].strip() == "..", labels
        assert spec["rows"][0]["key"] == os.path.dirname(tmp), spec["rows"][0]
        # 디렉터리가 먼저, 그 다음 파일(정본 기본 정렬).
        assert "sub/" in labels[1], labels
        # 숨김은 기본으로 안 보인다.
        assert not any(".hidden" in ln for ln in labels), labels
        # 조작 대상 목록은 `..` 를 안 담는다 — 담기면 부모를 지울 수 있다.
        assert os.path.dirname(tmp) not in mine["items"], mine["items"]
        assert os.path.join(tmp, "a.txt") in mine["items"], mine["items"]
        # 칸 둘(크기·시각)이 실린다.
        row = next(r for r in spec["rows"] if r["label"].strip().startswith("a.txt"))
        assert len(row["cols"]) == 2 and row["cols"][0] != "<DIR>", row
        # 글자 키를 스펙이 정한다 — 여기 없는 글자는 클라에서 판을 닫는다.
        assert spec["keys"]["enter"] == "into", spec["keys"]
        assert spec["keys"]["d"] == "delete" and spec["keys"]["p"] == "cd", spec["keys"]


async def test_mdir_hidden_toggle_and_tagging_live_in_this_clients_state():
    """숨김 토글·태그는 **그 클라의 것**이고, 태그는 그 디렉터리를 벗어나면 사라진다.

    태그가 따라다니면 화면에 안 보이는 것이 지워진다 — 삭제가 있는 화면에서 그건
    사고다."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        p = _mdir()
        mine = {"path": tmp, "tags": []}
        p._spec(mine, 0, "")
        a = os.path.join(tmp, "a.txt")
        spec = p._tag(mine, a, 2)
        assert mine["tags"] == [a], mine
        assert any(r["label"].startswith("✓") for r in spec["rows"]), spec["rows"]
        # 커서는 한 줄 내려간다(연달아 찍는 것이 이 키의 쓰임이다).
        assert spec["selected"] == 3, spec["selected"]
        # 숨김 토글은 왕복 없이 목록을 늘린다.
        mine["hidden"] = True
        spec = p._spec(mine, 0, "")
        assert any(".hidden" in r["label"] for r in spec["rows"]), spec["rows"]
        # 하위로 들어가면 그 디렉터리의 태그만 남는다 → 여기서는 전부 사라진다.
        p._into(mine, os.path.join(tmp, "sub"), 0)
        assert mine["tags"] == [], mine


async def test_mdir_delete_asks_before_it_does_anything_and_says_what_disappears():
    """되돌릴 수 없는 것 앞의 규칙 — **묻는 단계에서는 아무것도 안 한다**, 그리고
    무엇이 사라지는지를 물음이 들고 간다(클라는 스펙의 `title`·`note` 를 그린다)."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        p = _mdir()
        mine = {"path": tmp, "tags": []}
        p._spec(mine, 0, "")
        a = os.path.join(tmp, "a.txt")
        ask = p._begin(mine, "delete", a, 2)
        assert ask["kind"] == "confirm", ask
        assert "되돌릴 수 없" in ask["title"], ask
        assert "a.txt" in ask["note"], "무엇이 지워지는지 안 보인다: %r" % (ask,)
        assert ask["keys"] == {"enter": "apply"}, ask
        assert os.path.exists(a), "묻기만 했는데 벌써 지웠다"
        # 답이 오면 그때 지운다.
        spec = p._apply(mine, "y", 2)
        assert not os.path.exists(a), "답했는데 안 지워졌다"
        assert "삭제 1건" in spec["note"], spec["note"]
        assert spec["kind"] == "table", spec


async def test_mdir_copy_asks_where_and_the_two_step_overwrite_protocol_survives():
    """복사는 목적지를 되묻고, 겹치면 **아무것도 안 한 채** 덮어쓸지 다시 묻는다.

    절반만 수행하고 묻는 것보다 결정론적이다(정본 서버의 2단계 프로토콜 그대로)."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
        os.makedirs(src)
        os.makedirs(dst)
        for d in (src, dst):
            with open(os.path.join(d, "same.txt"), "w") as f:
                f.write("old" if d is dst else "new")
        p = _mdir()
        mine = {"path": src, "tags": []}
        p._spec(mine, 0, "")
        ask = p._begin(mine, "copy", os.path.join(src, "same.txt"), 1)
        assert ask["kind"] == "prompt" and "복사" in ask["title"], ask
        # 답 = 목적지. 겹치니 **수행 없이** 되묻는다.
        again = p._apply(mine, dst, 1)
        assert again["kind"] == "confirm" and "덮어쓸까요" in again["title"], again
        with open(os.path.join(dst, "same.txt")) as f:
            assert f.read() == "old", "되묻기 전에 이미 덮어썼다"
        # '예' 면 그때 덮어쓴다.
        spec = p._apply(mine, "y", 1)
        with open(os.path.join(dst, "same.txt")) as f:
            assert f.read() == "new", spec
        assert "복사 1건" in spec["note"], spec["note"]


async def test_mdir_cd_writes_to_the_pane_and_closes_the_screen():
    """`p` 는 정본 F4 와 **같은 결과**다 — 패널에 cd 를 치고 판을 닫는다.
    셸 방언은 **이 서버의** OS 가 정한다(원격이면 원격 셸의 방언이라야 한다)."""
    import tempfile
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = _mdir()
        with tempfile.TemporaryDirectory() as tmp:
            pane = sess.active_window.active_pane
            wrote = []
            with harness.patched(type(pane), write=lambda self, d: wrote.append(d)):
                resp = p.plugin_screen(srv, sess, {
                    "id": "mdir", "do": "cd", "row": 0, "input": "",
                    "state": {"mdir": {"path": tmp, "tags": [], "items": []}},
                })
            assert resp == {"t": "plugin_screen_close", "id": "mdir"}, resp
            assert wrote and tmp.encode() in wrote[0], wrote
            assert wrote[0].endswith(b"\n"), "Enter 없이 보내면 셸이 실행하지 않는다"
    finally:
        await teardown(srv, task, sock)


async def test_mdir_only_operates_on_what_the_current_listing_holds():
    """클라가 되돌려준 경로는 **지금 목록에 있는 것**이라야 한다.

    옛 목록의 줄(또는 지어낸 경로)이 그대로 삭제 대상이 되면, 화면에 없던 것이
    사라진다. 목록을 만들 때 담아 둔 `items` 가 그 관문이다."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        p = _mdir()
        mine = {"path": tmp, "tags": []}
        p._spec(mine, 0, "")
        outsider = os.path.join(tmp, "..", "somewhere-else")
        assert p._targets(mine, outsider) == [], "목록 밖 경로가 대상이 됐다"
        # `..` 줄도 대상이 아니다(정본 동형).
        assert p._targets(mine, os.path.dirname(tmp)) == []
        spec = p._begin(mine, "delete", outsider, 0)
        # 대상이 없으면 묻지 않는다 — 빈 확인 화면은 "누르면 뭔가 지워진다"로 읽힌다.
        assert not isinstance(spec, dict) or spec.get("kind") != "confirm", spec


# ── P7 — Claude 쪽 둘: 화면이 없던 마지막 플러그인들 ────────────────────────────

def _plugin(srv, name):
    return next(p for p in srv.plugins.plugins if getattr(p, "name", "") == name)


async def test_claude_resume_lists_sessions_and_remembers_where_each_one_lives():
    """세션 목록 스펙 — 줄의 **뜻**은 세션 id 이고, 리줌에 필요한 cwd 는 그 클라의
    화면 상태에 적어 둔다.

    왜 상태에 적나: 리줌할 때 `~/.claude/projects` 를 **다시 훑으면** 수백 개 jsonl 을
    두 번 읽는다. 그렇다고 cwd 를 줄의 key 에 붙이면 key 가 뜻 하나를 나른다는 계약이
    깨진다(그 key 는 목록이 바뀌어도 같은 세션을 가리켜야 한다).
    """
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        plugin = _plugin(srv, "claude-resume")
        found = [{"id": "abc-1", "cwd": "/work/one", "title": "첫 세션",
                  "project": "work/one", "mtime": 1_700_000_000.0},
                 {"id": "abc-2", "cwd": "/work/two", "title": "둘",
                  "project": "work/two", "mtime": 1_700_000_100.0}]
        mod = __import__("pytmuxlib.plugins.claude-resume.sessions",
                         fromlist=["sessions"])
        state = {}
        with harness.patched(mod, list_sessions=lambda **kw: found):
            spec = await plugin._open_spec(state)
        assert spec["kind"] == "list" and spec["id"] == "claude-resume"
        assert [r["key"] for r in spec["rows"]] == ["abc-1", "abc-2"]
        assert spec["rows"][0]["label"] == "첫 세션"
        assert spec["keys"] == {"enter": "resume"}
        assert state["claude-resume"]["cwds"] == {"abc-1": "/work/one",
                                                  "abc-2": "/work/two"}

        # 고른 줄 → 그 세션의 **원래 디렉토리**에서 새 탭 + 리줌 명령 주입.
        opened = []
        with harness.patched(type(srv),
                             new_window=lambda self, s, path=None: opened.append(path),
                             _broadcast_session=lambda self, s: None):
            resp = plugin.plugin_screen(srv, sess, {
                "id": "claude-resume", "do": "resume", "input": "abc-2",
                "state": state,
            })
        assert resp == {"t": "plugin_screen_close", "id": "claude-resume"}
        assert opened == ["/work/two"], "적어 둔 cwd 로 안 열었다: %r" % opened
    finally:
        await teardown(srv, task, sock)


async def test_claude_resume_refuses_a_session_id_that_is_not_one():
    """세션 id 는 셸로 들어간다 — 위생 실패면 **탭도 안 연다**(스펙 경로도 같은 문).

    종전에는 이 문이 `handle_server_request` 안에만 있었다. 화면 스펙이 두 번째 입구가
    되면서 같은 함수를 부르게 묶었다 — 입구가 둘인데 문이 하나여야 한다.
    """
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        plugin = _plugin(srv, "claude-resume")
        opened = []
        with harness.patched(type(srv),
                             new_window=lambda self, s, path=None: opened.append(path),
                             _broadcast_session=lambda self, s: None):
            plugin.plugin_screen(srv, sess, {
                "id": "claude-resume", "do": "resume",
                "input": "; rm -rf ~", "state": {},
            })
        assert opened == [], "위생 실패인데 탭을 열었다"
    finally:
        await teardown(srv, task, sock)


async def test_prompt_history_lists_the_same_tail_the_jump_indexes():
    """프롬프트 목록 — **최신이 위**이고, 줄의 뜻은 tail 슬라이스 안의 자리다.

    ⚠ 여기가 조용히 틀리는 자리다: 점프(`scroll_to_prompt`)의 index 는 tail 기준인데
    목록을 전체 히스토리로 만들면 오래된 패널에서 **엉뚱한 자리로** 점프한다. 그래서
    이 오라클은 tail 보다 긴 히스토리를 준다.
    """
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        plugin = _plugin(srv, "claude-prompt-history")
        pane = sess.active_window.active_pane
        pane._ph_history = ["p%d" % i for i in range(40)]
        spec = plugin._open_spec(srv, sess)
        assert spec["kind"] == "list" and spec["id"] == "prompt-history"
        assert spec["rows"][0]["label"] == "p39", "최신이 위가 아니다"
        # tail 슬라이스라 30개, 그리고 그 안의 자리가 key 다(마지막 = 29).
        assert len(spec["rows"]) == 30, len(spec["rows"])
        assert spec["rows"][0]["key"] == "29", spec["rows"][0]

        jumped = []
        srvmod = _server_module(plugin)
        with harness.patched(srvmod, scroll_to_prompt=lambda s, ss, i: jumped.append(i) is None), \
             harness.patched(type(srv), _broadcast_session=lambda self, s: None):
            resp = plugin.plugin_screen(srv, sess, {
                "id": "prompt-history", "do": "jump", "input": "29", "state": {}})
        assert resp == {"t": "plugin_screen_close", "id": "prompt-history"}
        assert jumped == [29], "고른 줄의 뜻이 그대로 안 갔다: %r" % jumped
    finally:
        await teardown(srv, task, sock)


async def test_prompt_history_says_it_is_empty_instead_of_showing_nothing():
    """빈 목록은 **빈 화면이 아니다** — 왜 비었는지 한 줄이 있어야 한다(설계 §8-5)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        plugin = _plugin(srv, "claude-prompt-history")
        sess.active_window.active_pane._ph_history = []
        spec = plugin._open_spec(srv, sess)
        assert spec["rows"] == [] and spec["note"], spec
    finally:
        await teardown(srv, task, sock)
