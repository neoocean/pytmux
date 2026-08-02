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
