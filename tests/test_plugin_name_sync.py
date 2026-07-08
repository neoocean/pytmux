"""claude-name-sync 플러그인 테스트 — 디렉토리별 이름 동기화.

서버 측: 규칙 매칭(정확 일치·host/os 와일드카드·하위 디렉토리 제외)·정제·opts 왕복·
server_scan 전이 감지(Claude None→비None 에 탭/패널·_pending_rename 동기화)·요청 처리
(namesync_get/set). 클라 측: `:namesync` 명령→요청, config 회신→편집기, 규칙 추가/저장을
Textual headless(run_test)로 검증.

delete-to-disable: 이 플러그인은 claude-code 를 import 하지 않고, Claude 감지는 코어
Pane 안전기본값 `_claude`, Claude 리네임은 코어 안전기본값 `_pending_rename` 만 쓴다."""
import importlib

import harness  # noqa: F401  (sys.path 주입)
from harness import make_app, server_only, teardown

# 하이픈 디렉토리라 import_module 로 불러온다(패키지명에 '-' 포함).
ns = importlib.import_module("pytmuxlib.plugins.claude-name-sync")
P = ns.PLUGIN


# ---- 서버: 매칭/정제/신원 ----
async def test_match_exact_only_no_subdir():
    rules = [{"host": "hostA", "os": "darwin", "path": "/p/proj", "keyword": "proj"}]
    assert ns._match_keyword(rules, "/p/proj", "hostA", "darwin") == "proj"
    # 하위 디렉토리는 제외(사용자 결정: 정확히 그 디렉토리만).
    assert ns._match_keyword(rules, "/p/proj/src", "hostA", "darwin") is None
    # host/os 불일치는 제외.
    assert ns._match_keyword(rules, "/p/proj", "hostB", "darwin") is None
    assert ns._match_keyword(rules, "/p/proj", "hostA", "linux") is None


async def test_match_wildcard_host_os_and_tilde():
    import os
    rules = [{"host": "", "os": "", "path": "~/blog", "keyword": "blog"}]
    # host/os 빈 규칙 = 아무 머신/OS. ~ 는 확장 후 비교.
    got = ns._match_keyword(rules, os.path.expanduser("~/blog"), "anyhost", "linux")
    assert got == "blog"


async def test_match_symlink_realpath_fallback():
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        real = os.path.join(root, "real_proj")
        os.makedirs(real)
        link = os.path.join(root, "link_proj")
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            return   # 심링크 미지원(권한 없는 Windows) → 스킵
        # 규칙은 링크 경로, 실제 cwd(lsof)는 해소된 실경로 — realpath 폴백으로 매칭.
        rules = [{"host": "", "os": "", "path": link, "keyword": "proj"}]
        assert ns._match_keyword(rules, real, "h", "darwin") == "proj"
        # 반대 방향(규칙=실경로, cwd=링크)도 매칭.
        rules2 = [{"host": "", "os": "", "path": real, "keyword": "proj"}]
        assert ns._match_keyword(rules2, link, "h", "darwin") == "proj"


async def test_match_first_rule_wins_and_empty_cwd():
    rules = [{"host": "", "os": "", "path": "/x", "keyword": "first"},
             {"host": "", "os": "", "path": "/x", "keyword": "second"}]
    assert ns._match_keyword(rules, "/x", "h", "darwin") == "first"
    assert ns._match_keyword(rules, None, "h", "darwin") is None
    assert ns._match_keyword([], "/x", "h", "darwin") is None


async def test_sanitize_drops_incomplete_and_stringifies():
    out = ns._sanitize_rules([
        {"path": "/a", "keyword": "k", "host": "h"},   # 유지
        {"path": "", "keyword": "x"},                  # path 비어 버림
        {"path": "/b", "keyword": ""},                 # keyword 비어 버림
        "not a dict",                                  # 무시
    ])
    assert out == [{"host": "h", "os": "", "path": "/a", "keyword": "k"}]
    assert ns._sanitize_rules("nope") == []


async def test_identity_os_code():
    assert ns._this_os() in ("darwin", "linux", "windows")
    assert isinstance(ns._this_host(), str)


async def test_opts_roundtrip_and_legacy_fallback():
    class S:
        pass
    # plugin_opts 네임스페이스 우선 + 정제.
    s = S()
    P.server_opts_init(s, {"plugin_opts": {ns._OPT_KEY: [
        {"host": "h", "os": "darwin", "path": "/a", "keyword": "k"},
        {"path": "", "keyword": "bad"}]}})
    assert s._namesync_rules == [
        {"host": "h", "os": "darwin", "path": "/a", "keyword": "k"}]
    assert P.server_opts_serialize(s) == {ns._OPT_KEY: [
        {"host": "h", "os": "darwin", "path": "/a", "keyword": "k"}]}
    # 구 top-level 키 폴백(업그레이드 무중단).
    s2 = S()
    P.server_opts_init(s2, {ns._OPT_KEY: [{"path": "/z", "keyword": "zz"}]})
    assert s2._namesync_rules == [
        {"host": "", "os": "", "path": "/z", "keyword": "zz"}]


# ---- 서버: server_scan 전이 감지 + 적용 ----
class _Pane:
    def __init__(self):
        self.title = None
        self._claude = None
        self._pending_rename = None


class _Win:
    def __init__(self, pane):
        self._p = [pane]
        self.auto_rename = True
        self.active_pane = pane

    def panes(self):
        return self._p


class _Tab:
    def __init__(self, win):
        self.window = win
        self.name = "win"


class _Sess:
    def __init__(self, tab, win):
        self.tabs = [tab]
        self._win = win

    @property
    def active_tab(self):
        return self.tabs[0]

    @property
    def active_window(self):
        return self._win


class _Srv:
    def __init__(self, rules, cwd):
        self._namesync_rules = ns._sanitize_rules(rules)
        self._cwd = cwd
        self.bcast = 0

    def _pane_cwd(self, pane):
        return self._cwd

    def _broadcast_status(self, sess):
        self.bcast += 1


def _fixture(cwd="/tmp/projX", keyword="projX"):
    pane = _Pane()
    win = _Win(pane)
    tab = _Tab(win)
    sess = _Sess(tab, win)
    srv = _Srv([{"host": ns._this_host(), "os": ns._this_os(),
                 "path": "/tmp/projX", "keyword": keyword}], cwd)
    return srv, sess, win, tab, pane


async def test_scan_fires_on_claude_appear_and_syncs_names():
    import asyncio
    srv, sess, win, tab, pane = _fixture()
    # Claude 미실행 → 아무 것도 안 함.
    assert P.server_scan(srv, sess, win) is False
    assert getattr(pane, "_ns_synced", False) is False
    # Claude 등장 → 지연 태스크가 이름 동기화.
    pane._claude = "idle"
    P.server_scan(srv, sess, win)
    assert pane._ns_synced is True          # 즉시 1회 처리 표시(재-probe 방지)
    await asyncio.sleep(0.1)                 # executor cwd 조회 + 적용
    assert tab.name == "projX"
    assert pane.title == "projX"
    assert pane._pending_rename == "projX"   # 코어 필드 — claude-code 가 idle 에 /rename
    assert win.auto_rename is False
    assert srv.bcast == 1


async def test_scan_no_refire_while_running_then_rearm_on_exit():
    import asyncio
    srv, sess, win, tab, pane = _fixture()
    pane._claude = "idle"
    P.server_scan(srv, sess, win)
    await asyncio.sleep(0.1)
    # 이미 동기화됨 — 재스캔은 재발동 안 함.
    pane._pending_rename = None
    P.server_scan(srv, sess, win)
    await asyncio.sleep(0.05)
    assert pane._pending_rename is None
    # Claude 종료 → 재무장(다음 실행에 다시 동기화).
    pane._claude = None
    assert P.server_scan(srv, sess, win) is False
    assert pane._ns_synced is False


async def test_scan_no_match_marks_synced_no_change():
    import asyncio
    srv, sess, win, tab, pane = _fixture(cwd="/tmp/unmatched")
    pane._claude = "idle"
    P.server_scan(srv, sess, win)
    await asyncio.sleep(0.1)
    assert pane._ns_synced is True           # 처리는 됨(재-probe 방지)
    assert tab.name == "win"                 # 매칭 실패 → 이름 불변
    assert pane.title is None
    assert pane._pending_rename is None
    assert srv.bcast == 0


# ---- 서버: 요청 처리(get/set) ----
async def test_request_get_and_set():
    srv, sess, win, tab, pane = _fixture()
    resp = P.handle_server_request(srv, sess, "namesync_get", {})
    assert resp["t"] == "namesync_config"
    assert resp["host"] == ns._this_host() and resp["os"] == ns._this_os()
    assert resp["cwd"] == "/tmp/projX"
    assert len(resp["rules"]) == 1
    saved = {"n": 0}
    srv._save_opts = lambda: saved.__setitem__("n", saved["n"] + 1)
    r2 = P.handle_server_request(
        srv, sess, "namesync_set",
        {"rules": [{"path": "/new", "keyword": "nn"}, {"path": "", "keyword": "x"}]})
    assert r2 == {"t": "namesync_saved", "count": 1}
    assert saved["n"] == 1
    assert srv._namesync_rules == [
        {"host": "", "os": "", "path": "/new", "keyword": "nn"}]
    # 알 수 없는 action 은 None(코어가 다른 경로로).
    assert P.handle_server_request(srv, sess, "nope", {}) is None


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


_CONFIG_MSG = {"t": "namesync_config", "host": "hostA", "os": "darwin",
               "cwd": "/here/now",
               "rules": [{"host": "", "os": "", "path": "/a", "keyword": "aa"}]}


async def test_namesync_command_requests_config():
    async def body(app, pilot, srv):
        for name in ("namesync", "nsync"):
            sent = []
            app.send_cmd = lambda action, **kw: sent.append((action, kw))
            app._run_command(name)
            assert app._want_namesync is True
            assert sent == [("namesync_get", {})], name
    await _with_app(body)


async def test_config_opens_editor_and_add_rule_saves():
    from textual.widgets import Input

    async def body(app, pilot, srv):
        scr_mod = importlib.import_module("pytmuxlib.plugins.claude-name-sync.screen")
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app._run_command("namesync")
        app._dispatch(dict(_CONFIG_MSG))
        await pilot.pause(0.1)
        scr = app.screen
        assert isinstance(scr, scr_mod.NameSyncScreen)
        # 'a' → 규칙 추가 폼.
        await pilot.press("a")
        await pilot.pause(0.1)
        form = app.screen
        assert isinstance(form, scr_mod.RuleFormScreen)
        form.query_one("#rf_keyword", Input).value = "web"
        form.query_one("#rf_path", Input).value = "/srv/web"
        form.query_one("#rf_host", Input).value = ""
        form.query_one("#rf_os", Input).value = ""
        await pilot.press("ctrl+s")            # 폼 저장 → 목록으로
        await pilot.pause(0.1)
        assert isinstance(app.screen, scr_mod.NameSyncScreen)
        # Esc → 목록 저장·닫기 → namesync_set 전송(원본 1 + 추가 1 = 2건).
        await pilot.press("escape")
        await pilot.pause(0.1)
        setcmds = [kw for (a, kw) in sent if a == "namesync_set"]
        assert len(setcmds) == 1
        rules = setcmds[0]["rules"]
        assert {r["keyword"] for r in rules} == {"aa", "web"}
        assert any(r["path"] == "/srv/web" for r in rules)
    await _with_app(body)


async def test_form_rejects_empty_path_or_keyword():
    from textual.widgets import Input

    async def body(app, pilot, srv):
        scr_mod = importlib.import_module("pytmuxlib.plugins.claude-name-sync.screen")
        app.send_cmd = lambda *a, **k: None
        app._run_command("namesync")
        app._dispatch(dict(_CONFIG_MSG))
        await pilot.pause(0.1)
        await pilot.press("a")
        await pilot.pause(0.1)
        form = app.screen
        # keyword 만 채우고 path 비움 → 저장 거부(폼 유지).
        form.query_one("#rf_keyword", Input).value = "x"
        form.query_one("#rf_path", Input).value = ""
        await pilot.press("ctrl+s")
        await pilot.pause(0.1)
        assert isinstance(app.screen, scr_mod.RuleFormScreen), "빈 경로인데 저장됨"
    await _with_app(body)
