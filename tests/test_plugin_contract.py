"""플러그인 계약(delete-to-disable) 테스트.

핵심 계약: `pytmuxlib/plugins/claude-code/` 디렉토리를 통째로 지우면 Claude Code
기능이 **에러 없이** 사라진다 — 명령 검색·자동완성·디스패치·서버/클라 런타임 훅
어디에도 나타나지 않고, 코어(server/client)는 그대로 구동된다.

여기서는 실제로 디렉토리를 지우는 대신 `Registry` 에서 claude-code 플러그인을
**필터로 제외**해 부재를 시뮬레이션한다(discovery 와 동치). 모든 런타임 훅이 안전한
기본값(no-op/False/None)을 돌려주고, 클라 앱이 Claude 클로저 없이도 구성·렌더·ESC·
입력 경로에서 깨지지 않는지 검증한다. 이 테스트는 Phase 2 추출(2a/2b/2c)의 회귀망 —
코어가 Claude 로직을 레지스트리 훅/`getattr` 가드로만 닿는다는 계약을 못박는다.
"""
import asyncio

import harness  # noqa: F401  (sys.path 주입)
from harness import make_app, server_only, teardown
from textual.events import Key

import pytmuxlib.plugins as plugins

# claude-code 가 코어에 노출하던 명령(이 플러그인 부재 시 전부 사라져야 함).
_CLAUDE_CMDS = {
    "claude-rules", "token-saver", "auto-resume", "claude-header",
    "token-log", "claude-usage",
    "usage-panel", "token-account", "prompt-clear", "model",
    "auto-doc-clear", "auto-compact", "claude-auto-mode", "auto-launch",
}


def _registry_without_claude():
    """claude-code 플러그인을 뺀 Registry — 디렉토리 삭제(delete-to-disable)와 동치.

    `_discover()`(load 가 아니라)를 직접 써서 만든다 — load 를 monkeypatch 한
    테스트에서도 자기재귀 없이 진짜 플러그인 목록에서 claude-code 만 뺀다."""
    found = plugins._discover()
    return plugins.Registry([p for p in found
                             if getattr(p, "name", "") != "claude-code"])


def _sanity_claude_present():
    """전제 확인: 정상(플러그인 존재) 상태에선 Claude 명령이 실제로 노출된다 —
    필터 테스트가 '원래도 없던 것'을 검증하는 헛검증이 되지 않게 한다."""
    reg = plugins.load()
    names = {n for (n, *_rest) in reg.commands}
    return _CLAUDE_CMDS & names


async def test_contract_sanity_claude_present_when_loaded():
    """전제: 플러그인이 있을 때 Claude 명령이 노출돼 있어야(헛검증 방지)."""
    present = _sanity_claude_present()
    assert "model" in present and "token-saver" in present, \
        "claude-code 플러그인이 로드되지 않음 — 계약 테스트 전제 실패"


async def test_contract_no_claude_commands_without_plugin():
    """플러그인 부재 시 Claude 명령/무인자/옵션 메타데이터가 전부 사라진다."""
    reg = _registry_without_claude()
    names = {n for (n, *_rest) in reg.commands}
    leaked = _CLAUDE_CMDS & names
    assert not leaked, f"플러그인 부재인데 명령에 남음: {leaked}"
    # 무인자(noarg)·자동완성·옵션에도 Claude 흔적이 없어야.
    assert not (reg.noarg & _CLAUDE_CMDS), f"noarg 누수: {reg.noarg & _CLAUDE_CMDS}"
    assert not (set(reg.command_options) & _CLAUDE_CMDS), "command_options 누수"
    assert not (_CLAUDE_CMDS & set(reg.completions)), "completions 누수"


async def test_contract_server_hooks_noop_without_plugin():
    """서버 런타임 훅이 전부 안전한 기본값(no-op/False/None)을 돌려준다."""
    reg = _registry_without_claude()
    # server_status: 플러그인이 없으면 msg 에 Claude 키를 안 채운다(예외 없음).
    msg = {"windows": [{}]}
    reg.server_status(None, None, None, msg, True)
    for k in ("claude_active", "panes_claude", "claude_usage", "usage_limits"):
        assert k not in msg, f"server_status 가 {k} 를 채움(플러그인 부재인데)"
    assert "claude" not in msg["windows"][0], "windows 항목에 claude 집계 누수"
    # 나머지 훅: 루프 본문이 안 돌아 인자를 안 건드림 → None/None 전달도 안전.
    assert reg.server_scan(None, None, None) is False
    assert reg.server_pending(None, None) is None
    assert reg.server_command(None, None, None, "set_autoresume", {}) is None
    reg.server_init(None)                 # 토큰 상태 설치 안 함(no-op, server=None 무탈)
    reg.pane_closing(None, None)          # 패널 종료 토큰 이관 안 함(no-op)
    reg.server_input(None, None, b"x")    # 부수효과 없음(no-op)
    reg.server_paste(None, None, b"x")
    reg.server_pane_overview(None, None, {})
    await reg.server_usage_refresh(None)
    # 서버측 믹스인도 사라진다(Server 가 합성할 Claude 베이스 없음).
    assert reg.server_mixins() == [], "claude 서버 믹스인이 남음"


async def test_contract_client_hooks_noop_without_plugin():
    """클라 런타임 훅(오버레이/틱/명령/메시지)이 전부 안전한 기본값을 돌려준다."""
    reg = _registry_without_claude()
    reg.client_overlay(None, None, 0, 0, None)      # no-op
    assert reg.client_tick(None) is False
    assert reg.client_close_overlay(None, None) is False
    assert reg.client_overlay_key(None, None) is False
    assert reg.handle_command(None, "model", []) is False
    assert reg.handle_command(None, "token-saver", []) is False
    assert reg.handle_message(None, {"t": "token_log"}) is False


async def test_token_log_request_handled_by_plugin_hook():
    """T1(토큰 모듈화): `request_token_log` 가 코어 serverio 의 elif 분기가 아니라
    claude-code 플러그인의 `handle_server_request` 훅으로 처리된다 — serverio 가 더는
    usagedb 를 import 하지 않게(탈토큰) 옮긴 뒤의 회귀. 플러그인 부재 시엔 무응답
    (None)이라 토큰 로그 요청이 조용히 사라진다(delete-to-disable)."""
    import os
    import tempfile

    import pytmuxlib.serverio as serverio
    from pytmuxlib import usagedb, usagelog

    # 코어 serverio 가 모듈 전역에 usagedb 를 더는 두지 않는다(탈토큰).
    assert not hasattr(serverio, "usagedb"), "serverio 가 아직 usagedb 를 import 함"

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = None
    try:
        conn = usagedb.connect(path)
        usagedb.insert(conn, usagelog.make_record(
            ts=1.0, tab=0, pane=1, session=1, account="me@x", tokens=1234))

        class _FakeServer:
            def _tokens_db_conn(self):
                return conn

        reg = plugins.load()
        resp = reg.handle_server_request(
            _FakeServer(), None, "request_token_log", {"limit": 10})
        assert resp and resp["t"] == "token_log", "토큰 로그 회신이 없음"
        assert resp["total_all"] == 1234, resp
        assert resp["records"] and resp["records"][0]["tokens"] == 1234
        assert resp["accounts_total"].get("me@x") == 1234, resp["accounts_total"]
        # 플러그인 부재(디렉토리 삭제 시뮬) → 토큰 로그 요청은 무응답(None).
        reg2 = _registry_without_claude()
        assert reg2.handle_server_request(
            _FakeServer(), None, "request_token_log", {}) is None
    finally:
        # Windows: 열린 SQLite 연결이 파일을 잡고 있으면 unlink 가 WinError 32 로
        # 실패한다(POSIX 는 열린 파일도 unlink 가능). 연결을 먼저 닫는다.
        if conn is not None:
            conn.close()
        os.unlink(path)


async def test_core_no_longer_imports_token_db_backend():
    """T2(토큰 모듈화): 코어 server.py·serverio.py 가 토큰 DB 백엔드(usagedb/usagelog)를
    더는 모듈 전역에 두지 않는다(탈토큰). DB 연결·기록·예산 추적은 claude-code 플러그인
    servermixin 으로 이전됐고, 코어는 server_init 훅으로 런타임 상태만 설치하게 한다.
    이 단언이 코어→토큰 결합이 되살아나는 회귀를 잡는다."""
    import pytmuxlib.server as server
    import pytmuxlib.serverio as serverio
    assert not hasattr(server, "usagedb"), "코어 server.py 가 아직 usagedb 를 import 함"
    assert not hasattr(server, "usagelog"), "코어 server.py 가 아직 usagelog 를 import 함"
    assert not hasattr(serverio, "usagedb"), "코어 serverio 가 아직 usagedb 를 import 함"
    # server_init 레지스트리 훅이 존재한다(플러그인이 토큰 런타임 상태를 설치하는 경로).
    assert hasattr(plugins.Registry, "server_init")


async def test_plugin_opts_namespace_and_migration_shim():
    """T3(토큰 모듈화)+§7-4: 플러그인 소유 설정(usage_gate_*)이 코어가 아니라
    claude-code 플러그인 소유로 opts.json 의 plugin_opts 네임스페이스에 저장/로드된다.
    **마이그레이션 shim**: 구 top-level 키는 폴백으로 읽고, plugin_opts 가 있으면
    그쪽을 우선한다. §7-4 deprecate shim: 구 opts.json 의 token_budget_* 키는 로드
    시 무시(속성 미설치)되고 serialize 에서 빠져 다음 저장에서 자연 소멸한다.
    플러그인 부재 시 init no-op·serialize {}."""
    reg = plugins.load()

    class _S:
        pass

    # ① 구 포맷(top-level only, plugin_opts 없음) → 폴백으로 읽힘(타 머신 업그레이드).
    s1 = _S()
    reg.server_opts_init(s1, {"usage_gate_session_pct": 80})
    assert s1.usage_gate_session_pct == 80
    assert s1.usage_gate_week_pct == 0           # 없는 키는 기본값
    # ② 신 포맷(plugin_opts) 우선 — 같은 키가 top-level 에도 있어도 nested 가 이긴다.
    s2 = _S()
    reg.server_opts_init(s2, {"usage_gate_session_pct": 99,
                              "plugin_opts": {"usage_gate_session_pct": 90,
                                              "usage_gate_week_pct": 7}})
    assert s2.usage_gate_session_pct == 90 and s2.usage_gate_week_pct == 7
    # ③ serialize 는 현재 server 값을 돌려준다(코어가 plugin_opts 밑에 불투명 저장).
    out = reg.server_opts_serialize(s2)
    assert out["usage_gate_session_pct"] == 90
    # ph_max_lines 는 claude-prompt-history 플러그인 소유 opt(별개) — claude-code 계약을
    # 엄격히 검증하기 위해 그 키만 빼고 비교한다.
    assert set(out) - {"ph_max_lines"} == {"usage_gate_session_pct",
                                           "usage_gate_week_pct", "claude_auto_retry"}
    assert out["claude_auto_retry"] is True   # 기본 ON(opts 부재 시)
    # §7-4 deprecate shim: 구 opts.json 에 남은 token_budget_* 는 무시(속성 미설치).
    s4 = _S()
    reg.server_opts_init(s4, {"token_budget_day": 111,
                              "plugin_opts": {"token_budget_day": 222,
                                              "token_budget_resume_gate": True}})
    assert not hasattr(s4, "token_budget_day"), "deprecate 키가 설치됨"
    assert not hasattr(s4, "token_budget_resume_gate"), "deprecate 키가 설치됨"
    # S6 T4 기본값: 세션 95(기본 ON)·주간 0(끔) — 구 opts.json(키 부재)에서도 적용.
    assert s4.usage_gate_session_pct == 95 and s4.usage_gate_week_pct == 0
    # ④ 플러그인 부재(디렉토리 삭제 시뮬) → init no-op(속성 안 생김), serialize {}.
    reg2 = _registry_without_claude()
    s3 = _S()
    reg2.server_opts_init(s3, {"usage_gate_session_pct": 5})
    assert not hasattr(s3, "usage_gate_session_pct"), "플러그인 부재인데 설정 설치됨"
    # claude-code 의 opts 는 사라진다(잔존 키는 claude-prompt-history 의 ph_max_lines 뿐).
    assert set(reg2.server_opts_serialize(s3)) - {"ph_max_lines"} == set()


async def test_pane_token_accumulator_owned_by_plugin():
    """T4(토큰 모듈화): 토큰 누계(_tok_state/_session_tokens)는 코어 Pane 이 아니라
    claude-code 의 pane_init(panestate)가 설치한다. 코어 model.Pane._RESUME_FIELDS 슬롯
    에서 빠지고(직렬화는 plugin_state 가 담당), 플러그인 부재 시 설치되지 않으며 패널 종료
    토큰 이관(pane_closing)도 no-op 다(delete-to-disable)."""
    import pytmuxlib.model as model
    reg = plugins.load()
    # 코어 재시작 슬롯에서 빠졌다(plugin_state 가 담당).
    assert "_tok_state" not in model.Pane._RESUME_FIELDS
    assert "_session_tokens" not in model.Pane._RESUME_FIELDS

    class _P:
        pass

    # pane_init 훅이 토큰 누계를 설치한다(기본값 = 이전 코어 model.py 정의와 동일).
    p = _P()
    reg.pane_init(p)
    assert p._tok_state == {"peak": 0, "total": 0}
    assert p._session_tokens == 0
    # 플러그인 부재(디렉토리 삭제 시뮬) → 토큰 누계 미설치 + pane_closing no-op.
    reg2 = _registry_without_claude()
    q = _P()
    reg2.pane_init(q)
    assert not hasattr(q, "_tok_state"), "플러그인 부재인데 토큰 누계가 설치됨"
    reg2.pane_closing(None, q)   # no-op(예외 없음)


async def test_token_db_legacy_location_migration():
    """T5(토큰 모듈화): DB 파일을 claude-code 플러그인 하위(db/)로 옮긴 뒤, 이전 위치
    (프로젝트 루트 db/)에 있던 토큰 DB 를 첫 연결 시 새 위치로 1회 이전한다 — 타 머신·
    구버전 업그레이드에서 이력 무중단. WAL 사이드카도 따라 옮기고, 새 위치가 이미 있으면
    멱등(재이전 안 함). _migrate_legacy_db 를 직접 검증한다(PYTMUX_TOKENS_DB 미설정 경로)."""
    import importlib
    import os
    import tempfile

    from pytmuxlib import usagedb, usagelog
    sm = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")

    tmp = tempfile.mkdtemp()
    old = os.path.join(tmp, "old", "claude-tokens.db")
    new = os.path.join(tmp, "new", "claude-tokens.db")
    os.makedirs(os.path.dirname(old))
    conn = usagedb.connect(old)
    usagedb.insert(conn, usagelog.make_record(
        ts=1.0, tab=0, pane=1, session=1, account="me@x", tokens=777))
    conn.close()
    with open(old + "-wal", "w"):       # WAL 사이드카 존재 시뮬
        pass

    class _Fake(sm.ServerClaudeMixin):
        def __init__(self, oldp):
            self._oldp = oldp

        def _legacy_tokens_db_path(self):
            return self._oldp

    env = os.environ.pop("PYTMUX_TOKENS_DB", None)   # 마이그레이션은 강제경로 시 건너뜀
    try:
        _Fake(old)._migrate_legacy_db(new)
        assert os.path.exists(new) and not os.path.exists(old), "DB 가 새 위치로 이전 안 됨"
        assert os.path.exists(new + "-wal"), "WAL 사이드카 미이전"
        assert usagedb.total_all(usagedb.connect(new)) == 777, "이전 중 이력 유실"
        # 멱등: 새 위치가 이미 있으면 구 파일을 건드리지 않는다(재이전 안 함).
        old2 = os.path.join(tmp, "old2.db")
        with open(old2, "w"):
            pass
        _Fake(old2)._migrate_legacy_db(new)
        assert os.path.exists(old2), "이미 새 DB 가 있는데 또 이전함(멱등 위반)"
    finally:
        if env is not None:
            os.environ["PYTMUX_TOKENS_DB"] = env


async def test_token_subsystem_fully_disabled_without_plugin():
    """T6(토큰 모듈화 계약 — 종합): claude-code 를 격리하면 토큰 서브시스템 전체 —
    명령·조회·예산설정·DB 연결상태·패널 누계·종료 이관·DB 백엔드 import·서버 믹스인 —
    이 코어에서 흔적 없이 사라지고 코어는 무에러다. T1~T5 가 분산 검증한 delete-to-disable
    를 한 자리에 못박는 회귀망(토큰 결합이 코어로 되살아나면 여기서 깨진다)."""
    import pytmuxlib.server as server
    import pytmuxlib.serverio as serverio

    reg = _registry_without_claude()

    class _S:
        pass

    # ① 토큰 명령(조회/설정/팝업 진입점)이 전부 사라진다.
    names = {n for (n, *_rest) in reg.commands}
    assert not ({"token-log", "token-account", "token-saver",
                 "usage-panel"} & names), f"토큰 명령 누수: {names}"
    # ② 토큰 로그 조회(request_token_log) 무응답.
    assert reg.handle_server_request(
        _S(), None, "request_token_log", {}) is None
    # ③ 플러그인 소유 설정: server_opts_init no-op(속성 미설치) + serialize {}.
    s = _S()
    reg.server_opts_init(s, {"usage_gate_session_pct": 9,
                             "plugin_opts": {"usage_gate_session_pct": 9}})
    # claude-code 의 opts 가 사라진다(잔존 키는 claude-prompt-history 의 ph_max_lines 뿐).
    assert set(reg.server_opts_serialize(s)) - {"ph_max_lines"} == set()
    # ④ DB 연결 런타임 상태(server_init) 미설치.
    s2 = _S()
    reg.server_init(s2)
    assert not hasattr(s2, "_tokens_db"), "토큰 DB 런타임 상태가 설치됨"
    # ④-b(S6): 실측 신선도·이벤트 갱신 예약 상태(T3/T5)도 함께 사라진다.
    assert not hasattr(s2, "_usage_ts") \
        and not hasattr(s2, "_usage_probe_handle"), "S6 실측 런타임 상태가 설치됨"
    # ④-c(S6 T4): 실측 게이트 임계 설정도 미설치(plugin_opts 소유 — ③과 동일 패턴).
    assert not hasattr(s, "usage_gate_session_pct"), "실측 게이트 설정이 설치됨"
    # ⑤ 패널 토큰 누계(pane_init) 미설치 + 종료 토큰 이관(pane_closing) no-op.
    p = _S()
    reg.pane_init(p)
    assert not hasattr(p, "_tok_state") and not hasattr(p, "_session_tokens"), \
        "패널 토큰 누계가 설치됨"
    reg.pane_closing(_S(), p)   # 예외 없음(no-op)
    # ⑥ 코어 모듈은 토큰 DB 백엔드(usagedb/usagelog)를 import 하지 않는다(물리 이전 T5
    #    이후에도 유지 — 코어 탈토큰 불변식).
    assert not hasattr(server, "usagedb") and not hasattr(server, "usagelog"), \
        "코어 server 가 토큰 DB 백엔드를 import 함"
    assert not hasattr(serverio, "usagedb"), "코어 serverio 가 usagedb 를 import 함"
    # ⑦ 서버측 Claude 믹스인도 사라진다(토큰 메서드 _log_tokens/_tokens_db_conn/예산이
    #    Server 합성에서 통째로 빠진다).
    assert reg.server_mixins() == [], "claude 서버 믹스인이 남음"


async def test_contract_client_app_runs_without_claude_plugin(monkeypatch=None):
    """클라 앱을 claude-code 플러그인 없이 구성·렌더·ESC·입력해도 깨지지 않는다.

    `plugins.load` 를 필터된 Registry 로 바꿔치기해 디렉토리 삭제를 시뮬레이션한
    뒤, 실제 Textual 앱을 띄운다. 코어가 Claude 팝업/오버레이/상태에 `getattr` 가드·
    레지스트리 훅으로만 닿으므로, 클로저가 설치되지 않아도 렌더·ESC·키 입력 경로가
    예외 없이 돈다(설치되지 않은 open_* 는 호출되지 않거나 no-op)."""
    orig_load = plugins.load
    plugins.load = lambda: _registry_without_claude()
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.4)
            # 1) Claude 팝업·헤더 클로저·상태가 설치되지 않았다(delete-to-disable).
            for attr in ("open_model_config", "open_perm_mode", "open_token_log",
                         "open_usage_panel",
                         "open_remote_control", "_toggle_remote_control",
                         "_update_claude", "set_claude_header",
                         "_footer_zone_at",
                         "_claude_header_panes",
                         # Phase 2c 헤더/클릭존 상태도 코어가 만들지 않는다.
                         "pane_claude", "claude_header_on",
                         "_perm_zone", "_remote_zone",
                         "_last_usage_shown_seq"):
                assert getattr(app, attr, None) is None, \
                    f"{attr} 가 설치됨(플러그인 부재인데)"
            # _hdr_panes 게이트는 코어에 남지만 플러그인 부재 시 빈 목록을 돌려준다.
            assert app._hdr_panes() == [], "헤더 포커스 게이트가 비어 있어야"
            # status 메시지에 Claude 필드가 와도 흡수기(client_status 훅)가 없어 무시.
            app._dispatch({"t": "status", "windows": [],
                           "claude_header": True, "panes_claude": [
                               {"id": 1, "claude": "idle", "prompt": "x"}],
                           "usage_shown_seq": 5})
            await pilot.pause(0.05)
            assert getattr(app, "pane_claude", None) is None, \
                "client_status 훅 부재인데 pane_claude 가 생김"
            assert app.view._cells, "Claude status 후 렌더 깨짐"
            # 하단 상태줄도 Claude 필드를 흡수하지 않는다. client_statusbar_init 훅
            # 부재라 claude_* 속성이 위젯에 아예 설치되지 않고(코어 __init__ 이 더 이상
            # 두지 않음), update_status 의 흡수 위임도 no-op → 속성이 끝내 안 생긴다.
            app.status.update_status({"claude_active": True, "claude_tokens": 9999,
                                      "claude_model": "opus", "budget_level": 100})
            await pilot.pause(0.05)
            assert not hasattr(app.status, "claude_active"), \
                "client_statusbar_init 훅 부재인데 claude_active 속성이 설치됨"
            assert app.status._usage_zone is None and \
                app.status._model_zone is None, "Claude 상태줄 클릭존이 등록됨"
            # 2) 기본 렌더가 성공했다(프레임 합성 — _draw_claude_headers 등 코어 경로 포함).
            assert app.view._cells, "프레임 합성 실패"
            # 3) ESC 모드 진입·이동·해제가 예외 없이 돈다(Claude ESC nav 가드 포함).
            await pilot.press("escape")
            await pilot.pause(0.1)
            for key in ("left", "right", "up", "down", "tab"):
                await app._on_key(Key(key, None))
            await pilot.press("escape")
            await pilot.pause(0.1)
            # 4) Claude 관련 명령을 쳐도 무해(핸들러 없음 → 코어가 조용히 무시).
            app._run_command("model")
            app._run_command("token-saver")
            await pilot.pause(0.1)
            assert app.view._cells, "Claude 명령 후 렌더 깨짐"
            # 5) 통합 상태 팝업: REC·서버 두 탭(토큰 탭은 2026-06-12 token-log 로
            # 통합·제거 — 플러그인 유무와 무관하게 동일 구성).
            app._status_cap_lines = ["파일: /tmp/x/pane-1.log"]
            app._status_tab_initial = 2          # host 클릭 = 서버 탭 의도
            app._open_status_tabs({"sessions": []})
            await pilot.pause(0.1)
            scr = app.screen_stack[-1]
            assert scr.__class__.__name__ == "InfoTabsScreen"
            names = [t[0] for t in scr._tabs]
            assert names == ["출력 캡처(REC)", "서버"], \
                f"토큰 탭이 사라지고 REC·서버만 남아야: {names}"
            assert scr._ti == 1, "initial=2 가 마지막 탭=서버(인덱스 1)로 클램프"
            await pilot.press("escape")
            await pilot.pause(0.05)
    finally:
        plugins.load = orig_load
        await teardown(srv, task, sock)
