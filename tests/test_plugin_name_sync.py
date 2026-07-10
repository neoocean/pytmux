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


async def test_sanitize_strips_control_chars_from_keyword():
    """keyword 는 최종적으로 `/rename <keyword>` 로 Claude 패널 입력에 주입될 수 있어,
    내장 CR/LF·ESC·NUL 이 다중 줄 제출(프롬프트 주입)이 되지 않게 제거한다(코드검수
    2026-07-10 Low 심층방어). 세정 후 빈 keyword 는 규칙 자체가 버려진다."""
    out = ns._sanitize_rules([
        {"path": "/a", "keyword": "proj\rmalicious"},     # CR 제거 → "projmalicious"
        {"path": "/b", "keyword": "x\ny\x1b[31mz"},       # LF/ESC 제거
        {"path": "/c", "keyword": "\r\n\x00"},            # 세정 후 빈 → 버림
        {"path": "/d", "keyword": "  ok  "},              # 공백만 다듬음
    ])
    kws = [r["keyword"] for r in out]
    assert kws == ["projmalicious", "xy[31mz", "ok"], kws
    assert all("\r" not in k and "\n" not in k and "\x1b" not in k
               and "\x00" not in k for k in kws)
    assert ns._clean_name("a\r\nb\x7f") == "ab"


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
    assert pane._ns_last_kw == "projX"       # 리네임 이력 기록
    # 이미 동기화됨 — 재스캔은 재발동 안 함.
    pane._pending_rename = None
    P.server_scan(srv, sess, win)
    await asyncio.sleep(0.05)
    assert pane._pending_rename is None
    # raw `_claude` 한 프레임 깜빡임(None)은 재무장하지 않는다(디바운스).
    pane._claude = None
    assert P.server_scan(srv, sess, win) is False
    assert pane._ns_synced is True
    pane._claude = "idle"
    P.server_scan(srv, sess, win)
    assert pane._ns_absent == 0              # 재등장에 부재 카운터 리셋
    # 세션 종료 확정 = _NS_ABSENT_FRAMES 연속 부재 → per-appearance 가드 해제.
    pane._claude = None
    for _ in range(ns._NS_ABSENT_FRAMES):
        assert P.server_scan(srv, sess, win) is False
    assert pane._ns_synced is False
    # 단, 세션 리네임 이력(_ns_last_kw)은 유지 — 거짓 종료 후 재등장에 /rename 재주입 방지.
    assert pane._ns_last_kw == "projX"


async def test_reappear_without_respawn_does_not_reinject_rename():
    import asyncio
    srv, sess, win, tab, pane = _fixture()
    # 첫 등장 → 탭/패널·세션 리네임.
    pane._claude = "idle"
    P.server_scan(srv, sess, win)
    await asyncio.sleep(0.1)
    assert pane._pending_rename == "projX"
    # 세션 종료 확정(디바운스 소진) 후 같은 셸에서 Claude 재등장(respawn 아님).
    pane._claude = None
    for _ in range(ns._NS_ABSENT_FRAMES):
        P.server_scan(srv, sess, win)
    assert pane._ns_synced is False
    pane._pending_rename = None
    srv.bcast = 0
    pane._claude = "idle"
    P.server_scan(srv, sess, win)
    await asyncio.sleep(0.1)
    # 탭·패널·세션 이름이 모두 이미 kw 라 /rename 재주입도 방송도 없다.
    assert pane._pending_rename is None
    assert srv.bcast == 0


async def test_pane_reset_rearms_full_sync():
    import asyncio
    srv, sess, win, tab, pane = _fixture()
    pane._claude = "idle"
    P.server_scan(srv, sess, win)
    await asyncio.sleep(0.1)
    assert pane._ns_last_kw == "projX"
    # 새 셸(respawn) → per-pane 상태 리셋 → 다음 등장에 세션 리네임까지 다시 무장.
    P.pane_reset(pane)
    assert pane._ns_synced is False and pane._ns_last_kw is None
    pane._pending_rename = None
    pane._claude = "idle"
    P.server_scan(srv, sess, win)
    await asyncio.sleep(0.1)
    assert pane._pending_rename == "projX"


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
    # S-3(코드검수 2026-07-10): namesync_get 은 이제 blocking _pane_cwd(macOS lsof)를
    # 동기 핸들러에서 부르지 않고 **캐시된** pane._ns_cwd 만 읽는다(이벤트 루프 프리즈
    # 방지). _pane_cwd 를 부르면 실패로 표시해 그 회귀를 못박는다.
    srv._pane_cwd = lambda pane: (_ for _ in ()).throw(
        AssertionError("namesync_get 이 blocking _pane_cwd 를 부르면 안 됨(S-3)"))
    # 스캔 이력 없음 → 캐시 없음 → cwd 는 빈 문자열(안전한 저하).
    resp0 = P.handle_server_request(srv, sess, "namesync_get", {})
    assert resp0["cwd"] == "", resp0["cwd"]
    # 스캔이 채운 캐시가 있으면 그 값을 그대로 돌려준다.
    pane._ns_cwd = "/tmp/projX"
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


async def test_nsmsg_saved_key_registered_by_init_alone():
    """§1-1(코드검수 2026-07-10): 저장 완료 알림 키 nsmsg.saved 는 소비처가 상시
    로드 모듈(__init__.handle_message)이므로 등록도 __init__ 에서 해야 한다 — 종전엔
    지연 import 모듈(screen.py)에서 등록해, 팝업을 한 번도 안 연 채 저장 회신이 오면
    미등록 키였다. **서브프로세스**에서 __init__ 만 import 하고 screen.py 는 import
    되지 않았음을 확인해, 키가 __init__ 단독으로 등록됨을 결정적으로 못박는다."""
    import os
    import subprocess
    import sys
    R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import importlib, sys;"
        "m=importlib.import_module('pytmuxlib.plugins.claude-name-sync');"
        "from pytmuxlib import i18n;"
        "scr='pytmuxlib.plugins.claude-name-sync.screen';"
        "assert scr not in sys.modules, 'screen.py 가 import 됨(격리 실패)';"
        "v=i18n.t('nsmsg.saved');"
        "assert v!='nsmsg.saved' and '{n}' in v, ('미등록/원시키: '+repr(v));"
        "print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=R,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, (r.stdout, r.stderr)
