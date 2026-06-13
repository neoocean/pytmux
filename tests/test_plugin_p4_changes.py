"""p4-show-submitted-changelists 플러그인 테스트 — 서버 파싱(p4 -G 마샬), 라이브 화면
플로(목록→Enter 상세 팝업→Esc), delete-to-disable 계약.

하이픈 패키지라 `from ... import` 가 안 돼 `importlib.import_module` 로 가져온다
(usage-view/claude-code 계약 테스트와 동일 — PLUGIN_MANUAL §3.3).

서버 테스트는 실제 p4 에 의존하지 않게 `subprocess.run` 을 가짜 마샬 스트림으로
monkeypatch 한다(p4 없는 CI 에서도 결정론적). 라이브 화면 테스트도 서버 왕복 대신
합성 메시지를 클라에 직접 dispatch 해 client 경로만 탄다."""
import importlib
import io
import marshal

import harness  # noqa: F401  (sys.path 주입)

import pytmuxlib.plugins as plugins

_PKG = "pytmuxlib.plugins.p4-show-submitted-changelists"
server = importlib.import_module(_PKG + ".server")
plugin_mod = importlib.import_module(_PKG)

_P4_CMDS = {"p4changes", "submitted", "p4-changes"}


# --------------------------------------------------------------------------- #
# 가짜 p4 — subprocess.run 을 대체해 마샬 스트림/텍스트를 돌려준다
# --------------------------------------------------------------------------- #
class _FakeCompleted:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _marshal_stream(records):
    """p4 -G 출력 흉내 — dict 들을 연속 마샬링(키/값은 bytes)."""
    buf = io.BytesIO()
    for rec in records:
        marshal.dump({k.encode(): (v.encode() if isinstance(v, str) else v)
                      for k, v in rec.items()}, buf)
    return buf.getvalue()


class _FakeServer:
    def _resolve_start_cwd(self, sess, which):
        return "/tmp"


class _patch_run:
    """server.subprocess.run 을 가짜로 교체했다 복원하는 컨텍스트(커스텀 러너엔
    pytest monkeypatch fixture 가 없어 직접 저장/복원한다)."""
    def __init__(self, fn):
        self._fn = fn

    def __enter__(self):
        self._orig = server.subprocess.run
        server.subprocess.run = self._fn
        return self

    def __exit__(self, *exc):
        server.subprocess.run = self._orig
        return False


# --------------------------------------------------------------------------- #
# server.py — p4 -G 파싱(순수, subprocess 직접 패치)
# --------------------------------------------------------------------------- #
async def test_list_changes_parses_marshal():
    info_rec = [{"serverAddress": "p4d:1666", "userName": "woojinkim",
                 "clientName": "playground"}]
    change_recs = [
        {"change": "58585", "time": "1718200000", "user": "woojinkim",
         "client": "office", "status": "submitted",
         "desc": "first line\nsecond line"},
        {"change": "58584", "time": "1718100000", "user": "woojinkim",
         "client": "office", "status": "submitted", "desc": "another"},
    ]

    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        # argv = ["p4", "-G", <sub>, ...]
        if argv[2] == "info":
            return _FakeCompleted(stdout=_marshal_stream(info_rec))
        if argv[2] == "changes":
            return _FakeCompleted(stdout=_marshal_stream(change_recs))
        return _FakeCompleted()

    with _patch_run(fake_run):
        msg = server.list_changes_msg(_FakeServer(), None, 50)

    assert msg["t"] == "p4_changes"
    assert msg["err"] is None
    assert msg["info"] == {"port": "p4d:1666", "user": "woojinkim",
                           "client": "playground"}
    assert [r["change"] for r in msg["rows"]] == ["58585", "58584"]
    # 에폭 → 'YYYY/MM/DD HH:MM' 로 포맷(로컬타임이라 날짜만 느슨 검증).
    assert msg["rows"][0]["when"].startswith("20") and "/" in msg["rows"][0]["when"]
    assert msg["rows"][0]["desc"] == "first line\nsecond line"
    # -m / -s submitted 인자가 실제로 들어갔는지.
    chg = next(c for c in calls if c[2] == "changes")
    assert "-s" in chg and "submitted" in chg and "50" in chg


async def test_list_changes_surfaces_p4_error():
    err_rec = [{"code": "error", "data": "Perforce password (P4PASSWD) invalid"}]

    def fake_run(argv, **kw):
        if argv[2] == "info":
            return _FakeCompleted(stdout=b"")           # info 비어도 graceful
        return _FakeCompleted(stdout=_marshal_stream(err_rec))

    with _patch_run(fake_run):
        msg = server.list_changes_msg(_FakeServer(), None, 10)
    assert msg["rows"] == []
    assert "P4PASSWD" in msg["err"]


async def test_list_changes_p4_missing():
    def fake_run(argv, **kw):
        raise FileNotFoundError("no p4")

    with _patch_run(fake_run):
        msg = server.list_changes_msg(_FakeServer(), None, 10)
    assert msg["rows"] == [] and "no p4" in msg["err"]


async def test_describe_text_and_error():
    def fake_ok(argv, **kw):
        return _FakeCompleted(stdout="Change 58585 by woojinkim@office\n\n\tdesc"
                                     .encode())
    with _patch_run(fake_ok):
        msg = server.describe_msg(_FakeServer(), None, "58585")
    assert msg["t"] == "p4_describe" and msg["change"] == "58585"
    assert msg["err"] is None and "Change 58585" in msg["text"]

    def fake_err(argv, **kw):
        return _FakeCompleted(stderr=b"no such changelist", returncode=1)
    with _patch_run(fake_err):
        msg = server.describe_msg(_FakeServer(), None, "999999")
    assert msg["err"] == "no such changelist"


# --------------------------------------------------------------------------- #
# 서버 요청 디스패치 — 레지스트리 훅 경유
# --------------------------------------------------------------------------- #
async def test_server_request_dispatch():
    def fake_run(argv, **kw):
        if argv[2] == "info":
            return _FakeCompleted(stdout=b"")
        return _FakeCompleted(stdout=_marshal_stream(
            [{"change": "1", "time": "1718200000", "user": "u",
              "client": "c", "desc": "d"}]))

    reg = plugins.load()
    with _patch_run(fake_run):
        resp = reg.handle_server_request(_FakeServer(), None,
                                         "request_p4_changes", {"count": 3})
    assert resp and resp["t"] == "p4_changes" and resp["rows"][0]["change"] == "1"
    # 미지의 action 은 None(코어가 계속 다른 경로 탐색).
    assert reg.handle_server_request(_FakeServer(), None, "nope", {}) is None


async def test_count_clamped():
    seen = {}

    def fake_run(argv, **kw):
        if argv[2] == "changes":
            seen["m"] = argv[argv.index("-m") + 1]
        return _FakeCompleted(stdout=b"")
    reg = plugins.load()
    with _patch_run(fake_run):
        reg.handle_server_request(_FakeServer(), None,
                                  "request_p4_changes", {"count": 99999})
    assert seen["m"] == "500"            # _MAX_COUNT 로 클램프


# --------------------------------------------------------------------------- #
# delete-to-disable 계약
# --------------------------------------------------------------------------- #
def _registry_without_p4():
    found = plugins._discover()
    return plugins.Registry([p for p in found
                             if getattr(p, "name", "") != _PKG.split(".")[-1]])


async def test_contract_present_when_loaded():
    names = {n for (n, *_rest) in plugins.load().commands}
    assert "p4changes" in names, "p4 플러그인이 로드되지 않음 — 전제 실패"


async def test_contract_gone_without_plugin():
    reg = _registry_without_p4()
    names = {n for (n, *_rest) in reg.commands}
    assert not (_P4_CMDS & names), f"명령 누수: {_P4_CMDS & names}"
    assert not (reg.noarg & _P4_CMDS), "noarg 누수"
    assert not (_P4_CMDS & set(reg.completions)), "completions 누수"
    # 서버 요청 훅도 부재 시 None.
    assert reg.handle_server_request(_FakeServer(), None,
                                     "request_p4_changes", {}) is None
    # 클라 명령/메시지 훅도 no-op.
    assert reg.handle_command(None, "p4changes", []) is False
    assert reg.handle_message(None, {"t": "p4_changes", "rows": []}) is False


# --------------------------------------------------------------------------- #
# 라이브 통합 — 실제 Textual 클라이언트 화면 플로
# --------------------------------------------------------------------------- #
async def test_live_screen_flow():
    """목록 메시지 → ChangesScreen 풀스크린, ↓ 이동, Enter → DescribeScreen 팝업,
    상세 메시지 채움, Esc 로 팝업 닫고 다시 Esc 로 목록(탭) 닫아 종료."""
    from harness import make_app, server_only, teardown

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.4)
            # ① 목록 메시지 합성 dispatch(서버 왕복 없이 client 경로만).
            app._want_p4_changes = True
            app._dispatch({"t": "p4_changes",
                           "info": {"port": "p4d:1666"},
                           "err": None,
                           "rows": [
                               {"change": "58585", "when": "2026/06/12 23:23",
                                "user": "woojinkim", "client": "o", "desc": "첫째"},
                               {"change": "58584", "when": "2026/06/12 23:17",
                                "user": "woojinkim", "client": "o", "desc": "둘째"}]})
            await pilot.pause(0.15)
            top = app.screen_stack[-1]
            assert top.__class__.__name__ == "ChangesScreen", top.__class__.__name__
            assert app.view._cells, "목록 화면 합성 실패"
            view = top._view
            assert view._sel == 0
            # ② ↓ 이동 — 하이라이트가 둘째 행으로.
            await pilot.press("down")
            await pilot.pause(0.05)
            assert view._sel == 1 and view._cur() == "58584"
            # ③ Enter → DescribeScreen 팝업이 스택 위로(서버에 describe 요청도 나감).
            await pilot.press("enter")
            await pilot.pause(0.15)
            pop = app.screen_stack[-1]
            assert pop.__class__.__name__ == "DescribeScreen", pop.__class__.__name__
            assert pop._change == "58584"
            # ④ 상세 메시지 채움 — 팝업 내용이 갱신된다.
            app._dispatch({"t": "p4_describe", "change": "58584",
                           "text": "Change 58584 by woojinkim\n\n\t둘째 상세",
                           "err": None})
            await pilot.pause(0.1)
            assert any("58584" in ln for ln in pop._view._lines)
            # ⑤ Esc → 팝업만 닫히고 목록으로.
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert app.screen_stack[-1].__class__.__name__ == "ChangesScreen"
            # ⑥ Esc → 목록(탭) 닫혀 플러그인 종료.
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert app.screen_stack[-1].__class__.__name__ != "ChangesScreen"
    finally:
        await teardown(srv, task, sock)
