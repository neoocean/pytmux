"""플러그인 관리 — Registry disabled 필터 + 서버 토글/영속/status + 클라 팝업.
docs/PLUGIN_MANAGER_SCENARIO.md."""
import json
import os

from harness import make_app, server_only, teardown
from pytmuxlib import plugins


async def _with_app(coro, size=(100, 30)):
    srv, task, sock = await server_only()
    app = make_app(sock, None, None)
    try:
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.4)
            await coro(app, pilot, srv)
    finally:
        await teardown(srv, task, sock)


# ── Registry: disabled 부분집합 + overview + default_disabled ────────────────
async def test_registry_set_disabled_filters_everything():
    reg = plugins.load()
    names_all = {getattr(p, "name", "") for p in reg._all}
    assert "rec" in names_all and "clock" in names_all
    # 비활성 적용 → plugins(활성 부분집합)에서 빠지고, 명령/자동완성에서도 빠진다.
    reg.set_disabled({"rec"})
    assert "rec" not in {getattr(p, "name", "") for p in reg.plugins}
    cmd_names = {n for (n, *_rest) in reg.commands}
    assert "capture-output" not in cmd_names, "비활성 플러그인 명령이 남음"
    assert "capture-output" not in reg.completions
    # overview 는 전체(_all)를 enabled 플래그와 함께 보여준다.
    ov = {name: enabled for (name, _d, _c, enabled) in reg.plugin_overview()}
    assert ov["rec"] is False and ov["clock"] is True
    # 재활성 → 복귀.
    reg.set_disabled(set())
    assert "capture-output" in {n for (n, *_r) in reg.commands}


async def test_registry_default_disabled_empty():
    # 현재 어떤 플러그인도 default_enabled=False 가 아니다(rec 는 capture-opt 로 OFF).
    assert plugins.load().default_disabled() == set()


# ── 서버: 토글 + opts 영속 + status 필드 + 명령 필터 ─────────────────────────
async def test_server_set_plugin_enabled_persists_and_filters():
    srv, task, sock = await server_only()
    try:
        # 끄기: disabled 에 추가 + opts.json 영속 + 명령 사라짐.
        assert srv.set_plugin_enabled("rec", False) is False
        assert "rec" in srv.plugins.disabled
        assert "capture-output" not in {n for (n, *_r) in srv.plugins.commands}
        saved = json.load(open(srv.opts_path))["disabled_plugins"]
        assert "rec" in saved, saved
        # 켜기(반전): disabled 에서 빠짐 + 명령 복귀.
        assert srv.set_plugin_enabled("rec", None) is True
        assert "rec" not in srv.plugins.disabled
        assert "capture-output" in {n for (n, *_r) in srv.plugins.commands}
        assert "rec" not in json.load(open(srv.opts_path))["disabled_plugins"]
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_server_status_carries_disabled_plugins():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.set_plugin_enabled("clock", False)
        msg = srv._status_msg(sess)
        assert "clock" in msg.get("disabled_plugins", []), msg.get("disabled_plugins")
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_server_seeds_default_disabled_when_opts_absent(tmp_path=None):
    """opts.json 에 disabled_plugins 키가 없으면 default_enabled=False 플러그인을 시드.
    현재는 그런 플러그인이 없어 빈 집합이지만, 키가 생기면 그 값이 권위임을 확인한다."""
    srv, task, sock = await server_only()
    try:
        # 새 서버(opts 없음) → 시드 = default_disabled() = 빈 집합.
        assert srv.plugins.disabled == set()
        # 토글로 키를 만든 뒤 같은 sock 새 서버 → 저장된 값을 읽는다(시드 아님).
        srv.set_plugin_enabled("ncd", False)
        import pytmux
        srv2 = pytmux.Server(sock)
        assert "ncd" in srv2.plugins.disabled, "저장된 disabled_plugins 가 권위"
        assert "ncd" not in {n for (n, *_r) in srv2.plugins.commands}
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


# ── 클라 팝업: 토글 → set_plugin_enabled cmd ────────────────────────────────
async def test_plugin_manager_popup_toggle_sends_cmd():
    from pytmuxlib.clientscreens import PluginManagerScreen

    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        app.push_screen(PluginManagerScreen())
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "PluginManagerScreen"
        # 첫 항목(활성) 위에서 Space → set_plugin_enabled(on=False) 전송.
        await pilot.press("space")
        await pilot.pause(0.05)
        assert sent and sent[0][0] == "set_plugin_enabled", sent
        assert sent[0][1].get("on") is False, sent
        # Esc 로 닫힘.
        await pilot.press("escape")
        await pilot.pause(0.05)
        assert app.screen_stack[-1] is not scr
    await _with_app(body)


async def test_plugin_manager_click_outside_closes():
    """플러그인 관리 팝업(PluginManagerScreen) 박스(#plgbox) 바깥(백드롭) 클릭/터치 시
    dismiss(None) 로 닫힌다. 박스 안(목록 항목 등) 클릭은 닫지 않는다(InfoScreen·토큰
    팝업과 동일한 inside-box 판정)."""
    from pytmuxlib.clientscreens import PluginManagerScreen

    async def body(app, pilot, srv):
        app.push_screen(PluginManagerScreen())
        await pilot.pause(0.1)
        scr = app.screen_stack[-1]
        assert scr.__class__.__name__ == "PluginManagerScreen"

        class _W:
            def __init__(self, wid, parent=None):
                self.id = wid
                self.parent = parent

        class _Ev:
            def __init__(self, widget):
                self.widget = widget
                self.stopped = False

            def stop(self):
                self.stopped = True

        # 박스 안 클릭(목록 항목 → … → #plgbox) → 닫히지 않음
        ev_in = _Ev(_W("plg_clock", parent=_W("plgbox", parent=_W("screen"))))
        scr.on_click(ev_in)
        await pilot.pause(0.05)
        assert app.screen_stack[-1] is scr, "박스 안 클릭은 닫지 않는다"
        # 박스 바깥(백드롭) 클릭 → 닫힘
        ev_out = _Ev(_W("backdrop", parent=None))
        scr.on_click(ev_out)
        await pilot.pause(0.05)
        assert app.screen_stack[-1] is not scr, "바깥 클릭은 팝업을 닫는다"
        assert ev_out.stopped
    await _with_app(body)
