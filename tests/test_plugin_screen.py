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

import contextlib
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


class _TokenSrv:
    """토큰 판들이 읽는 서버 표면만 가진 대역(집계 DB 없이도 판은 서야 한다)."""
    _usage = {"session": {"pct": 42, "reset": "3:00 PM (KST)"},
              "week_all": {"pct": 7, "reset": "Mon"}}
    _usage_ts = None

    def _tokens_db_conn(self):
        return None

    def _read_warn_history(self, limit=50):
        import time
        return [{"ts": time.time(), "kind": "loop", "n": 5, "badge": "repeat"},
                {"ts": time.time() - 90000, "kind": "fmt", "n": 1, "badge": "format"}]


async def test_every_token_panel_reaches_every_other_one():
    """★ **전수 오라클** — 정본은 이 판들을 한 팝업의 **탭 띠**로 묶는다(기간·세션·머신·
    한도·경고). GUI 는 판이 여러 개라 같은 뜻을 «잇는 줄»로 내는데, 판마다 손으로 적으면
    새 판이 생길 때 **어떤 판에서는 안 보인다** — 그 조용한 갈림이 이 저장소가 반복해 물린
    부류다(pytmux-35 의 죽은 줄). 그래서 표(`_HUB`) 하나를 두고 여기서 전수로 잰다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    sids = [sid for _k, _l, sid in ss._HUB]
    assert len(sids) >= 4, sids
    for key, _label, sid in ss._HUB:
        spec = ss.open_spec(srv, None, sid if sid != "claude-usage-panel" else "limits")
        assert spec is not None, f"{sid} 판이 안 열린다"
        keys = [r["key"] for r in spec["rows"]]
        # 자기 자신으로 가는 줄은 없다(눌러도 아무 일 없는 줄은 거짓말이다).
        assert key not in keys, f"{sid} 가 자기 자신으로 가는 줄을 그렸다"
        # 나머지 전부로 가는 줄이 있다.
        for other_key, _l2, other_sid in ss._HUB:
            if other_sid == sid:
                continue
            assert other_key in keys, f"{sid} → {other_sid} 로 가는 줄이 없다: {keys}"
            do = "toggle" if sid == "claude-warn-history" else "apply"
            got = ss.action(srv, None, {"id": sid, "do": do, "input": other_key})
            assert got and got.get("id") == other_sid, (sid, other_key, got)


async def test_every_token_panel_reaches_the_scenario_settings_and_that_panel_stays_put():
    """⑥ 자동재개 설정 — 정본 탭 띠 **끝의 초록 배지**(`시나리오`)로 간다(pytmux-371 ⑥).

    판(`claude-settings`)은 이미 있었는데 **가는 줄이 없었다** — GUI 사용자는 팔레트에
    그 이름을 쳐야만 닿았다. 정본은 토큰 팝업의 어느 탭에서든 그 배지가 보인다.

    ★ 그리고 이 줄은 **한 방향**이다. 정본에서 ⑥ 은 탭이 아니라 경고 탭 위에 겹쳐 뜨는
    판이고 꼬리줄이 광고하는 조작이 `Enter toggle/cycle · ESC close` 뿐이다 — 탭 전환이
    없다. 그래서 `_HUB`(서로 오가는 판들의 표)가 아니라 `_HUB_ACTIONS` 에 있고,
    **대조군**으로 그 판에 잇는 줄이 안 생겼는지까지 잰다. 대칭으로 만들면 정본에 없는
    이동을 GUI 가 갖게 되고 그것이 [[pytmux-185]] 가 결함으로 세는 갈림이다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    assert [sid for _k, _l, sid in ss._HUB_ACTIONS] == ["claude-settings"], ss._HUB_ACTIONS

    # ⑴ 어느 판에서든 그 줄이 있고, 눌러야 실제로 그 판이 온다(죽은 줄 방지 · pytmux-35).
    for _key, _label, sid in ss._HUB:
        spec = ss.open_spec(srv, None, sid if sid != "claude-usage-panel" else "limits")
        keys = [r["key"] for r in spec["rows"]]
        assert ss._GOTO_SETTINGS in keys, f"{sid} → 시나리오 설정 줄이 없다: {keys}"
        do = "toggle" if sid == "claude-warn-history" else "apply"
        got = ss.action(srv, None, {"id": sid, "do": do, "input": ss._GOTO_SETTINGS})
        assert got and got.get("id") == "claude-settings", (sid, got)

    # ⑵ 대조군 — 그 판은 **어디로도 안 잇는다**(정본 꼬리줄에 탭 전환이 없다).
    panel = ss.open_spec(srv, None, "claude-settings")
    stray = [r["key"] for r in panel["rows"] if str(r["key"]).startswith("goto:")]
    assert stray == [], ("⑥ 이 정본에 없는 이동을 갖게 됐다", stray)
    # 그리고 여덟 줄이 그대로다 — 잇는 줄을 더하다 토글 줄을 밀어내지 않았다.
    assert len(panel["rows"]) == len(ss._saver_rows()) == 8, panel["rows"]


async def test_the_token_panels_answer_the_same_letter_keys_the_canonical_popup_does():
    """정본 토큰 팝업의 **글자 키**가 GUI 판에서도 같은 뜻이다(pytmux-371 · pytmux-185).

    ⛔ 여기가 «있다» 와 «같게 군다» 가 갈리는 자리다. 잇는 줄은 이미 있었다(위 시험) —
    그런데 정본을 손에 익힌 사람은 줄을 고르지 않고 `p`·`l` 을 친다
    (`screens.TokenLogScreen.on_key`). 그 글자가 GUI 에서 안 먹으면 «기능은 있는데
    안 쓰이는» 상태이고, 루트 CLAUDE.md ★★ 는 그것을 결함으로 센다.

    정본 대조표(`screens.py` 의 `on_key`):

    | 키 | 정본 | 여기 |
    |---|---|---|
    | `p` | 세션 뷰 토글 | `claude-token-sessions` |
    | `l` | 한도 상세 토글 | `claude-usage-panel` |
    | `o` | 머신 뷰 토글 | `claude-token-machines` |
    | `s` | 시나리오(자동재개) 판 | `claude-settings` |
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    want = {"p": "claude-token-sessions", "l": "claude-usage-panel",
            "o": "claude-token-machines", "s": "claude-settings"}
    for _key, _label, sid in ss._HUB:
        spec = ss.open_spec(srv, None, sid if sid != "claude-usage-panel" else "limits")
        keys = spec["keys"]
        for letter, target in want.items():
            assert letter in keys, f"{sid} 가 `{letter}` 를 안 문다: {sorted(keys)}"
            got = ss.action(srv, None, {"id": sid, "do": keys[letter], "input": ""})
            # 지금 판이 목적지면 정본처럼 **기간으로 되돌아온다**(토글).
            expect = "claude-token-period" if target == sid else target
            assert got and got.get("id") == expect, (sid, letter, expect, got)


async def test_the_letter_key_wins_over_whatever_row_the_cursor_sits_on():
    """글자를 눌렀는데 **커서가 앉은 줄**이 열리면 안 된다.

    클라는 글자 키에도 «고른 줄의 열쇠»를 `input` 으로 함께 싣는다
    (`session_view.rs` 의 `key_action` 갈래). 그래서 `action()` 이 줄을 먼저 보면,
    커서가 마침 잇는 줄 위에 있을 때 `l` 이 **엉뚱한 판**을 연다 — 눌러 보기 전에는
    안 보이고, 눌러 봐도 "가끔 이상하다"로만 보이는 부류다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    spec = ss.open_spec(srv, None, "token-period")
    got = ss.action(srv, None, {"id": "claude-token-period",
                                "do": spec["keys"]["l"],
                                "input": ss._GOTO_MACHINES})   # 커서가 «머신별 →» 위
    assert got and got["id"] == "claude-usage-panel", got


async def test_the_roster_of_letter_key_actions_matches_what_the_panels_actually_emit():
    """`_HUB_KEY_DOS` 전수 ↔ 실제로 나가는 `do` — **양방향**으로 맞댄다.

    왜 전수를 따로 적나: 죽은 `do` 를 잡는 자(`tests/test_plugin_do_wiring.py`)는 그
    파일을 **읽지 부르지 않으므로**(그 모듈 머리말) 함수가 짓는 표를 못 본다. 그래서
    「낼 수 있는 이름」을 글자로 적어 두고 그 자가 그것을 읽는다.

    ⛔ 두 벌이 되는 순간 갈릴 수 있다 — 그것을 막는 자리가 여기다:
    ⑴ 실제로 나가는 이름이 전수 밖이면 **아무도 안 재는 키**가 생기고,
    ⑵ 전수에만 있고 안 나가는 이름이 있으면 그 자는 **없는 키를 받는다고** 믿는다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    # ⚠ **글자 키만** 본다. `enter`·`right`·`left` 는 그 판 안의 조작(펼침·적용)이라
    #    탭 전환 전수와 섞으면 전수가 무엇을 말하는지 흐려진다.
    emitted = set()
    for _key, _label, sid in ss._HUB:
        spec = ss.open_spec(srv, None, sid if sid != "claude-usage-panel" else "limits")
        emitted |= {v for k, v in spec["keys"].items() if len(k) == 1 and k.isalpha()}
    assert emitted <= set(ss._HUB_KEY_DOS),         ("전수 밖의 do 가 나간다 — 그 키는 아무도 안 잰다", emitted - set(ss._HUB_KEY_DOS))
    assert set(ss._HUB_KEY_DOS) <= emitted,         ("전수에만 있고 안 나가는 do", set(ss._HUB_KEY_DOS) - emitted)


async def test_the_limit_panel_carries_the_reset_moment_not_a_countdown_string():
    """한도 판이 **시각을 자료로** 싣는다(pytmux-371 ④).

    ⛔ 글자로 실으면 초마다 프레임이 와야 한다 — 판 하나 때문에 초당 한 번 전 세션을
    다시 그리는 값이다. 그래서 서버는 «언제인지»만 싣고 남은 시간은 클라가 굴린다.

    ⚠ 못 읽는 표기면 칸을 **아예 안 만든다** — `0` 을 실으면 클라가 그것을 「지금 리셋」
    으로 그린다(그 판정은 클라 쪽 대조군이 잰다).
    """
    import importlib, time
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    spec = ss.open_spec(srv, None, "limits")
    withts = [r for r in spec["rows"] if r.get("until")]
    assert withts, f"리셋 시각을 실은 줄이 없다: {spec['rows']}"
    for r in withts:
        assert isinstance(r["until"], int), (r["key"], type(r["until"]))
        # 대역이 주는 표기("3:00 PM (KST)")는 **다가올** 시각으로 풀린다.
        assert r["until"] > time.time() - 86400, (r["key"], r["until"])
    # ⛔ 대조군 — 표기를 못 읽으면 칸이 없다(0 이 아니라 «없음»이다).
    assert ss._reset_epoch("") == 0 and ss._reset_epoch("nonsense") == 0


async def test_the_linking_rows_carry_their_words_so_the_client_can_translate_them():
    """잇는 줄의 글은 **자료가 아니라 우리가 적은 말**이다 — 재료를 실어야 번역된다.

    ⛔ 목록 줄의 `label` 을 클라는 **번역하지 않는다**(`PluginRow::say_label` — 「복사」라는
    이름의 파일이 `Copy` 로 보이면 안 되므로). 그래서 말을 그냥 실으면 **서버 로케일로
    굳는다**.

    실측(2026-08-26 · Windows 프레임): 제목·꼬리줄이 영어로 뜬 판인데 잇는 줄만
    «한도(/usage) 보기 →» 로 한국어였다. `_perm_spec` 이 이미 쓰던 처방(`i18n.phrase`)을
    같은 자리에 놓는다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    spec = ss.open_spec(srv, None, "token-period")
    links = [r for r in spec["rows"] if str(r["key"]).startswith("goto:")]
    assert links, "잇는 줄이 없다 — 재는 것이 없다"
    from pytmuxlib import i18n
    values = set(i18n._CATALOG["ko"].values())
    for r in links:
        carried = (r.get("i18n") or {}).get("label")
        assert carried, (r["key"], "글을 재료로 안 실었다 — 서버 로케일로 굳는다")
        # ⚠ 재료는 «키»가 아니라 **원문 + 인자**다(`i18n.phrase` 의 계약) — 클라는
        #   `tf(원문, 인자)` 로 다시 짓는다. 그러니 원문이 카탈로그를 거친 글이라야 한다
        #   (손으로 적은 글은 러스트 표에 짝이 없어 그대로 뜬다).
        fmt = carried.get("fmt") if isinstance(carried, dict) else None
        assert fmt in values, (r["key"], "카탈로그를 안 거친 글이다", fmt)


async def test_the_period_hint_does_not_advertise_an_operation_that_is_gone():
    """⛔ **안 먹는 키를 광고하면 그것도 거짓말이다**(pytmux-371 · 실측 2026-08-26).

    기간 판이 계층 트리가 되면서 버킷 고르개가 사라졌는데 꼬리줄은 한동안
    «Enter 로 기간 단위 고르기» 를 그대로 광고했다 — 라이브 프레임에서 눈에 띄었다.
    """
    import importlib
    from pytmuxlib import i18n
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    hint = ss.open_spec(srv, None, "token-period")["hint"]
    for gone in ("기간 단위", "period unit"):
        assert gone not in hint, f"없어진 조작을 광고한다({gone}): {hint}"
    # 그리고 **지금 있는 손**을 적는다 — Enter/←→ 펼침·접힘.
    assert "Enter" in hint and ("펼침" in hint or "expand" in hint), hint
    # 카탈로그를 거친 글이라야 클라가 자기 로케일로 다시 읽는다.
    assert hint in set(i18n._CATALOG["ko"].values()) | set(i18n._CATALOG["en"].values()), hint


async def test_the_swallowed_letters_are_left_out_on_purpose_and_that_is_written_down():
    """정본이 예약해 둔 글자(`h`·`d`·`w`·`m`·`r`)를 **스펙에 안 싣는** 것이 맞다.

    ⚠ 이 시험은 «없음»을 재는 드문 자리라 까닭을 함께 적어 둔다. 정본이 그 글자를 문
    이유는 *팝업이 오타에 닫히지 않게* 인데 GUI 에는 그 위험이 없다 — 이 클라는 스펙
    표에 **없는** 글자에 이미 아무 일도 안 한다(pytmux-181·273 · 재는 자리는
    `gui/src/session_view_tests.rs::a_letter_the_spec_does_not_declare_is_ignored_not_a_close`).
    실으면 아무 일도 안 하는 **왕복**만 한 번 더 간다.

    ⛔ 지우지 말 것: 2026-08-25 에 실제로 실었다가 되돌렸다. 이 줄이 없으면 다음 사람이
    「정본에 있으니 우리도」로 같은 길을 한 번 더 간다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    for _key, _label, sid in ss._HUB:
        spec = ss.open_spec(srv, None, sid if sid != "claude-usage-panel" else "limits")
        stray = [k for k in ss._KEY_RESERVED if k in spec["keys"]]
        assert stray == [], (sid, "예약 글자를 실었다 — 빈 왕복이 생긴다", stray)


async def test_the_tail_line_advertises_the_letter_keys_it_actually_answers():
    """꼬리줄이 광고하는 조작이 곧 최소 요건이다(pytmux-371 §옮길 때의 계약).

    ⛔ 반대 방향도 잰다 — **안 먹는 키를 광고하면** 그것도 거짓말이다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    for _key, _label, sid in ss._HUB:
        spec = ss.open_spec(srv, None, sid if sid != "claude-usage-panel" else "limits")
        hint = spec["hint"]
        for letter in sorted(ss._KEY_TABS):
            assert letter in spec["keys"], (sid, letter)
            assert f"{letter} " in hint or f"{letter}세" in hint or letter in hint,                 f"{sid} 꼬리줄이 `{letter}` 를 안 알린다: {hint}"


async def test_the_period_panel_is_the_same_hierarchical_tree_the_canonical_popup_draws():
    """기간 판(pytmux-371 ①) — 정본은 열자마자 **월→주→일→시각 트리**를 보인다.

    종전 GUI 판은 평면 막대 + 버킷 고르개(h/d/w/m)였다. 그 손은 정본의 **옛** 서브탭이고
    지금 정본에는 남아 있지 않다(그 글자들은 `event.stop()` 만 한다) — 즉 GUI 는 정본에
    없는 조작을 갖고, 정본에 있는 트리는 없었다. 둘 다 [[pytmux-185]] 가 세는 갈림이다.

    ⛔ 산수를 두 벌로 적지 않는 것이 이 슬라이스의 핵심이다 — 재는 것은 「트리 모양이
    나오나」이지 「트리가 맞나」가 아니다(뒤엣것은 정본 스위트가 이미 잰다).
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    ut = importlib.import_module("pytmuxlib.plugins.claude-code.usagetree")
    # 트리 산수는 **한 벌**이고 정본 화면도 그것을 부른다 — 그 사실부터 못박는다.
    scr = importlib.import_module("pytmuxlib.plugins.claude-code.screens")
    import inspect
    src = inspect.getsource(scr.TokenLogScreen._build_tree_rows)
    assert "usagetree.build" in src,         "정본 화면이 공용 트리를 안 부른다 — 산수가 두 벌이 됐다"

    # 기록을 직접 먹여 트리 줄을 만든다(대역 서버에는 DB 가 없다).
    # ⚠ **오늘 기준**으로 만든다 — 트리의 구역 가르기가 오늘을 축으로 하므로, 옛 시각을
    #   주면 전부 「이전 달」 한 줄로 접혀 일·시각 행이 아예 안 나온다(그건 트리가 맞게
    #   구는 것이지 결함이 아니다. 실측으로 한 번 헛짚었다).
    import time
    now = time.time()
    recs = [{"ts": now - i * 3600, "tokens": 1000 + i, "account": "a"}
            for i in range(30)]
    nodes, _total = ut.build(recs, None, None, ())
    assert nodes, "트리가 비었다 — 재는 것이 없다"
    kinds = {n["kind"] for n in nodes}
    assert "day" in kinds, f"일 행이 없다: {sorted(kinds)}"
    assert any(n["expandable"] for n in nodes), "펼칠 수 있는 행이 하나도 없다"
    # 오늘 행은 **기본이 펼침**이라 시각 행이 딸려 나온다(정본과 같은 기본값).
    assert "hour" in kinds, f"오늘의 시각 행이 없다: {sorted(kinds)}"
    # ⛔ 대조군 — 그 행을 **뒤집으면** 시각이 사라진다(펼침 집합이 실제로 먹는다).
    today_key = next(n["key"] for n in nodes if n["kind"] == "day" and n["expandable"])
    shut, _ = ut.build(recs, None, None, {today_key})
    assert "hour" not in {n["kind"] for n in shut}, "토글이 안 먹는다 — 집합이 죽었다"


async def test_the_period_tree_expands_and_the_client_holds_which_rows_are_open():
    """펼침 상태는 **클라가 든다** — 경고 판과 같은 처방(서버가 들면 클라 둘이 흔든다).

    정본의 `_tree_open` 은 `기본값 ^ 토글` 이라 그 집합은 「펴진 것」이 아니라
    **「뒤집은 것」**이다. 그 뜻까지 옮겼는지를 잰다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    spec = ss.open_spec(srv, None, "token-period")
    # 정본 트리의 손이 광고돼야 한다 — Enter 토글 · → 펼침 · ← 접힘.
    for key, act in (("enter", "toggle"), ("right", "expand"), ("left", "collapse")):
        assert spec["keys"].get(key) == act, (key, spec["keys"])
    # 뒤집은 집합이 왕복을 타고 돌아온다(대역 서버는 DB 가 없어 판만 다시 선다).
    out = ss.action(srv, None, {"id": "claude-token-period", "do": "toggle",
                                "row": 0, "input": "day:2026-08-25",
                                "state": {"tree_open": []}})
    assert out and out["id"] == "claude-token-period", out
    # ⛔ 대조군 — 자료 줄(키 없는 divider)을 눌러도 판이 안 닫힌다.
    out2 = ss.action(srv, None, {"id": "claude-token-period", "do": "toggle",
                                 "row": 1, "input": "", "state": {}})
    assert out2 and out2.get("t") != "plugin_screen_close", out2


async def test_the_period_tree_remembers_every_row_that_was_opened_not_just_the_last():
    """★ 펼침은 **쌓여야** 한다 — 서버가 그 집합을 되쓰지 않으면 한 번에 하나만 펴진다.

    `state` 는 클라가 프레임에 실어 보내는 것이 아니라 **서버가 연결마다 드는 dict**
    다(`ClientConn.plugin_state` · `servercmd._plugin_screen_reply` 가 그 자리를
    `req["state"]` 로 넣는다). 그래서 여기서 계산한 «뒤집은 집합»을 그 dict 에 **되쓰지
    않으면** 다음 누름이 다시 빈 집합에서 출발한다 — 방금 누른 노드 하나만 뒤집힌 채로
    다시 그려지고, 두 노드를 동시에 펼 수도 접을 수도 없다(pytmux-419 ④).

    ⛔ 이 시험이 판(`rows`)이 아니라 **state 를 보는 이유**: 되쓰기가 곧 계약이고,
    대역 서버에는 집계 DB 가 없어 트리 줄이 안 선다(`_tree_rows` 가 `None`). 트리 산수가
    맞는지는 위 시험이 이미 잰다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    # 서버가 드는 그 dict 한 벌 — 왕복마다 **같은 객체**가 온다.
    state = {}
    ss.action(srv, None, {"id": "claude-token-period", "do": "toggle",
                          "row": 0, "input": "day:2026-08-25", "state": state})
    ss.action(srv, None, {"id": "claude-token-period", "do": "toggle",
                          "row": 1, "input": "day:2026-08-26", "state": state})
    got = set(state.get("tree_open") or [])
    assert got == {"day:2026-08-25", "day:2026-08-26"}, (
        f"펼침이 안 쌓인다 — 두 번 눌렀는데 집합은 {sorted(got)} 다. "
        "action() 이 계산한 집합을 state 에 되쓰지 않는다(pytmux-419 ④)")
    # 같은 노드를 다시 누르면 **빠진다**(대칭차 — 정본 `_tree_open` 의 뜻).
    ss.action(srv, None, {"id": "claude-token-period", "do": "toggle",
                          "row": 0, "input": "day:2026-08-25", "state": state})
    assert set(state.get("tree_open") or []) == {"day:2026-08-26"}, state
    # `→`/`←` 도 같은 자리에 쌓인다(더하기·빼기라 대칭차가 아니다).
    ss.action(srv, None, {"id": "claude-token-period", "do": "expand",
                          "row": 0, "input": "day:2026-08-27", "state": state})
    assert "day:2026-08-27" in set(state.get("tree_open") or []), state
    ss.action(srv, None, {"id": "claude-token-period", "do": "collapse",
                          "row": 0, "input": "day:2026-08-27", "state": state})
    assert "day:2026-08-27" not in set(state.get("tree_open") or []), state


async def test_the_warn_history_remembers_every_day_that_was_opened():
    """경고 이력도 같은 자리다 — `warn_open` 이 안 쌓이면 날짜 둘을 동시에 못 편다.

    같은 뿌리라 같은 회차에서 잰다(pytmux-419 ④ 는 두 판을 한 줄로 적었다).
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    spec = ss.open_spec(srv, None, "warn-history")
    days = [r for r in spec["rows"] if r.get("depth") == 0 and r.get("expand")]
    assert len(days) == 2, spec["rows"]
    # 판이 처음 선 상태(최신 날짜만 펴짐)를 그대로 담고 시작한다.
    state = {"warn_open": [d["key"] for d in days if d["expand"] == "open"]}
    ss.action(srv, None, {"id": "claude-warn-history", "do": "toggle",
                          "input": days[1]["key"], "state": state})
    got = set(state.get("warn_open") or [])
    assert got == {days[0]["key"], days[1]["key"]}, (
        f"두 날짜를 동시에 못 편다 — {sorted(got)}. state 되쓰기가 없다(pytmux-419 ④)")


async def test_the_fold_survives_a_tab_hop_but_a_fresh_open_starts_folded():
    """접힘의 수명 — **탭을 옮겨도 살고 · 판을 다시 열면 기본으로 돌아간다**(정본 그대로).

    정본에서 이 다섯은 한 팝업의 탭이라 접힘을 화면 인스턴스가 든다(`_tree_toggled`).
    ⑴ [기간] 에서 트리를 펴고 [세션] 에 들렀다 돌아오면 **편 채로 있고**,
    ⑵ 팝업을 닫았다 다시 열면 새 인스턴스라 **기본 접힘**이다.
    보관함(`ClientConn.plugin_state`)은 연결 수명이라, 되돌리는 손이 없으면 ⑵ 에서
    어제 편 노드가 오늘 판에 남는다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    state = {}
    ss.open_spec(srv, None, "token-period", state=state)
    ss.action(srv, None, {"id": "claude-token-period", "do": "toggle",
                          "row": 0, "input": "day:2026-08-25", "state": state})
    assert set(state["tree_open"]) == {"day:2026-08-25"}, state

    # ⑴ 잇는 줄로 [세션] 에 갔다가 다시 [기간] 으로 — **판이** 편 채로 돌아온다.
    #    ⛔ 보관함만 보면 모자란다: 되돌아온 판을 지을 때 그 집합을 **안 건네도**
    #       보관함은 그대로라 시험이 초록이 된다(실측 — 뮤테이션이 안 물렸다).
    #       그래서 자료를 먹여 **줄까지** 잰다.
    import time
    now = time.time()
    recs = [{"ts": now - i * 3600, "tokens": 1000 + i, "account": "a"}
            for i in range(30)]
    with harness.patched(ss, _usage_records=lambda _srv, limit=4000: recs):
        st2 = {}
        first = ss.open_spec(srv, None, "token-period", state=st2)
        # 기본으로 펴져 있는 오늘 행을 골라 **접는다**(뒤집은 집합에 실린다).
        today = next(r["key"] for r in first["rows"] if r.get("expand") == "open")
        ss.action(srv, None, {"id": "claude-token-period", "do": "toggle",
                              "row": 0, "input": today, "state": st2})
        hop = ss.action(srv, None, {"id": "claude-token-period", "do": "apply",
                                    "input": ss._GOTO_SESSIONS, "state": st2})
        assert hop and hop["id"] == "claude-token-sessions", hop
        back = ss.action(srv, None, {"id": "claude-token-sessions", "do": "apply",
                                     "input": ss._GOTO_PERIOD, "state": st2})
        assert back and back["id"] == "claude-token-period", back
        got = next((r["expand"] for r in back["rows"] if r["key"] == today), None)
        assert got == "shut", (
            f"탭을 옮겼다 오니 접은 것이 도로 펴졌다({today}={got}) — 되돌아온 판에 "
            f"보관함의 집합을 안 건넸다: {st2}")
    assert set(state["tree_open"]) == {"day:2026-08-25"}, (
        f"탭을 옮겼다 오니 접힘이 날아갔다: {state}")

    # ⑵ 판을 새로 열면 되돌아간다.
    ss.open_spec(srv, None, "token-period", state=state)
    assert not state.get("tree_open"), f"새로 연 판에 옛 접힘이 남았다: {state}"


async def test_collapsing_every_warning_day_is_not_read_as_no_choice_at_all():
    """⛔ 대조군 — 경고 판에서 **다 접은 것**과 **아직 안 건드린 것**은 다르다.

    그 집합은 대칭차가 아니라 그냥 «펴진 날짜»라 빈 집합에 뜻이 있다. 없는 것을 빈
    것으로 접으면, 다 접어 둔 판이 다음 왕복에 «최신 날짜만 펴진» 기본 모양으로
    되살아난다 — 사용자가 접은 것이 저 혼자 펴진다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    spec = ss.open_spec(srv, None, "warn-history")
    days = [r for r in spec["rows"] if r.get("depth") == 0 and r.get("expand")]
    newest = next(d["key"] for d in days if d["expand"] == "open")
    # 최신 날짜를 눌러 **다 접는다** → 보관함에는 빈 목록이 남는다.
    state = {}
    ss.action(srv, None, {"id": "claude-warn-history", "do": "toggle",
                          "input": newest, "state": state})
    assert state.get("warn_open") == [], state
    # 그 상태로 탭을 옮겼다 돌아와도 **여전히 다 접혀 있다**.
    back = ss._hub_open(srv, None, ss._GOTO_WARNS, state)
    assert back and back["id"] == "claude-warn-history", back
    assert not [r for r in back["rows"] if r.get("expand") == "open"], (
        f"다 접어 뒀는데 저 혼자 펴졌다: {back['rows']}")


async def test_every_pscreen_word_the_server_speaks_is_registered_where_the_server_reads():
    """★ **전수 게이트** — 서버가 짓는 글은 서버가 읽는 카탈로그에 있어야 한다(pytmux-419).

    `screens.py` 는 Textual 이라 **서버가 안 읽는다**(플러그인 머리말의 무게 규칙 — 화면은
    실제로 열 때 지연 import 한다). 그런데 화면 **스펙**을 짓는 것은 서버다. 그래서 서버가
    쓰는 `pscreen.*` 를 `screens.py` 카탈로그에만 적어 두면 `i18n.t` 가 **키를 그대로**
    돌려주고, 그 값이 어디로 흘러가느냐에 따라 둘로 갈린다:

    - `pscreen.weekdays` — `"pscreen.weekdays".split(",")` 는 원소가 **하나**라
      `weekdays[wd]` 가 월요일 말고는 전부 `IndexError` 다. GUI 기간 탭이 **자료가 있는
      홈에서 아예 안 떴다**(다섯 중 하나가 이랬다).
    - 나머지는 안 터지고 **키 문자열이 그대로 화면에 뜬다** — 더 조용하다.

    ⛔ 정본 팝업은 `screens.py` 를 이미 물고 있어 멀쩡했다. 그래서 이 갈림은 **GUI 에서만**
    보이고 오래 안 잡혔다 — 사람이 지키는 규칙이 아니라 게이트로 센다.

    ⚠ **자식 프로세스에서 잰다**: 이 스위트는 전 모듈을 한 프로세스에서 돌아서, 앞서 도는
    시험이 `screens.py` 를 한 번이라도 import 하면 카탈로그가 채워져 **가짜 초록**이 된다.
    """
    import subprocess, sys, os, json, textwrap
    probe = textwrap.dedent('''
        import sys, os, re, io, importlib
        sys.path.insert(0, os.getcwd())
        base = os.path.join("pytmuxlib", "plugins", "claude-code")
        # 서버 프로세스가 실제로 무는 모듈들(Textual 없이 도는 것).
        server_mods = ["screenspec.py", "usagetree.py", "usagelog.py",
                       "__init__.py", "servermixin.py", "usagedb.py"]
        used = {}
        for m in server_mods:
            fp = os.path.join(base, m)
            if not os.path.exists(fp):
                continue
            src = io.open(fp, encoding="utf-8").read()
            for k in re.findall(r'i18n\.(?:t|phrase)\(\s*"(pscreen\.[a-z0-9_]+)"', src):
                used.setdefault(k, set()).add(m)
        i18n = importlib.import_module("pytmuxlib.i18n")
        importlib.import_module("pytmuxlib.plugins.claude-code")   # 서버가 무는 만큼만
        assert "pytmuxlib.plugins.claude-code.screens" not in sys.modules, \
            "탐침이 Textual 화면을 물어 버렸다 — 이 시험은 아무것도 못 잰다"
        missing = sorted(k for k in used if i18n.t(k) == k)
        print(repr((len(used), missing, {k: sorted(v) for k, v in used.items()
                                         if k in missing})))
    ''')
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, cwd=os.getcwd(), timeout=120)
    assert out.returncode == 0, f"탐침이 죽었다:\n{out.stderr}"
    total, missing, where = eval(out.stdout.strip())
    assert total >= 20, f"키를 {total}개밖에 못 찾았다 — 정규식이 낡았다"
    assert not missing, (
        f"서버가 쓰는 pscreen.* {len(missing)}개가 서버 카탈로그에 없다: "
        f"{where}. `screens.py`(Textual)에만 적으면 서버는 못 읽는다 — "
        f"`__init__.py` 의 `i18n.register` 로 옮길 것(pytmux-419)")


async def test_the_period_tree_actually_builds_on_a_server_that_never_loaded_textual():
    """⛔ 위 게이트의 **대조군** — 키가 비면 판이 «안 예쁘다» 가 아니라 **안 선다**.

    글자 하나가 빠졌을 때의 값이 얼마인지를 못박는다: 서버 모양(Textual 미로드)에서
    기간 판을 실제로 지어 본다. 이것이 GUI 사용자가 보던 그 자리다.
    """
    import subprocess, sys, os, textwrap
    probe = textwrap.dedent('''
        import sys, os, time, importlib
        sys.path.insert(0, os.getcwd())
        ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
        assert "pytmuxlib.plugins.claude-code.screens" not in sys.modules
        recs = [{"ts": time.time() - i * 3600, "tokens": 1000 + i, "account": "a"}
                for i in range(30)]
        ss._usage_records = lambda _s, limit=4000: recs
        class Srv:
            def _tokens_db_conn(self): return object()
        spec = ss._period_spec(Srv())
        rows = [r for r in spec["rows"] if not str(r["key"]).startswith("goto:")]
        assert rows, "트리가 비었다"
        assert any(r["expand"] in ("open", "shut") for r in rows), "펼칠 행이 없다"
        # 요일 이름이 실제로 붙는다 — 키가 비면 여기서 IndexError 로 죽는다.
        print("OK", len(rows))
    ''')
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, cwd=os.getcwd(), timeout=120)
    assert out.returncode == 0, (
        "Textual 을 안 문 서버에서 기간 판이 안 선다 — GUI 의 `:claude-token-period` 가 "
        f"자료 있는 홈에서 그대로 터지는 자리다(pytmux-419):\n{out.stderr}")


async def test_the_period_tree_carries_the_5h_and_1w_columns_on_hour_rows_only():
    """정본 `[기간]` 탭의 칸은 셋이다 — `Tokens` · `5h%` · `1w%`(pytmux-419 ⑤).

    토큰 칸(스크랩 Σ)은 5h 소비를 **과소반영**하므로 「그 시각에 창이 얼마나 찼나」의
    진짜 신호는 저 둘이다(권위 `/usage` 스냅샷). 조인키는 노드의 `bk`.

    ★ **시각 행에만** 붙는다 — 정본이 `show5h = (bucket == "hour" …)` 로 같은 판정을
    한다. 5h 창은 시간 단위 개념이라 일·주·월 행에 붙이면 「그 날의 5h%」라는 없는 뜻이
    생긴다.
    """
    import importlib, time
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    now = time.time()
    recs = [{"ts": now - i * 3600, "tokens": 1000 + i, "account": "a"}
            for i in range(6)]
    # 시각 키(`%Y-%m-%d %H:00`)로 두 표를 만든다 — usagedb 가 내는 그 모양이다.
    keys = sorted({time.strftime("%Y-%m-%d %H:00", time.localtime(r["ts"]))
                   for r in recs})
    p5 = {k: 9 + i * 10 for i, k in enumerate(keys)}
    p1 = {k: 63 for k in keys}

    class _Srv(_TokenSrv):
        def _tokens_db_conn(self):
            return object()

    with harness.patched(ss, _usage_records=lambda _s, limit=4000: recs,
                         _limit_pcts=lambda _s: (p5, p1)):
        spec = ss.open_spec(_Srv(), None, "token-period")
    rows = [r for r in spec["rows"] if not str(r["key"]).startswith("goto:")]
    hours = [r for r in rows if r["label"].endswith(("시", "h"))]
    assert hours, f"시각 행이 없다: {[r['label'] for r in rows]}"
    for r in hours:
        assert len(r["cols"]) == 3, (
            f"시각 행의 칸이 셋이 아니다({r['label']}): {r['cols']} — "
            "정본은 Tokens·5h%·1w% 셋이다(pytmux-419 ⑤)")
        assert r["cols"][2] == "63%", r["cols"]
    # ⛔ 대조군 — 일·월 행에는 안 붙는다(붙으면 없는 뜻이 생긴다).
    others = [r for r in rows if r not in hours and r["cols"]]
    assert others, "비교할 상위 행이 없다"
    for r in others:
        assert len(r["cols"]) == 1, (
            f"시각이 아닌 행에 5h%/1w% 가 붙었다({r['label']}): {r['cols']}")


async def test_a_half_missing_limit_pair_keeps_its_place_instead_of_shifting_left():
    """⛔ 두 칸은 **함께 움직인다** — 하나만 실으면 숫자가 조용히 거짓말을 한다.

    `1w%` 만 있는 시각에 그 값 하나만 붙이면 소비자는 그것을 **`5h%` 자리**로 읽는다
    (칸은 자리로 뜻이 정해진다). 그래서 하나만 있으면 나머지는 빈 칸으로 자리를 지키고,
    둘 다 없으면 아무것도 안 붙인다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    node = {"kind": "hour", "bk": "2026-08-31 22:00"}
    assert ss._limit_cols(node, {}, {"2026-08-31 22:00": 63}) == ["", "63%"]
    assert ss._limit_cols(node, {"2026-08-31 22:00": 9}, {}) == ["9%", ""]
    assert ss._limit_cols(node, {}, {}) == []
    # 시각이 아닌 행 · 조인키가 없는 행은 언제나 빈다.
    #
    # ⚠ 첫 줄은 **`bk` 가 있는 날짜 행**이다. 오늘 `usagetree` 는 시각 노드에만 `bk` 를
    #   실으므로 `kind` 검사 없이도 결과가 같은데, 그러면 그 줄이 «무력한 가드»라 지워도
    #   아무 시험이 안 운다(실측 — 뮤테이션이 안 물렸다). 조인키가 다른 종류로 번져도
    #   이 칸이 안 따라가는 것이 계약이므로 여기서 그 계약을 직접 문다.
    assert ss._limit_cols({"kind": "day", "bk": "2026-08-31 22:00"},
                          {"2026-08-31 22:00": 9}, {"2026-08-31 22:00": 63}) == []
    assert ss._limit_cols({"kind": "day", "bk": None}, {"x": 1}, {"x": 2}) == []
    assert ss._limit_cols({"kind": "hour", "bk": None}, {"x": 1}, {"x": 2}) == []


async def test_the_bars_are_scaled_to_the_biggest_row_not_to_the_total():
    """막대 기준은 **그 목록의 최대값**이다(정본 `bmax` 와 같다).

    전체 대비 비중을 막대로 쓰면 항목이 스물일 때 전부 5% 근처라 막대가 통째로 납작해져
    «어느 것이 큰가»를 못 읽는다. 비중은 칸에 숫자로 함께 적어 둘이 다른 값임을 말한다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    rows = ss._bar_rows([("a", 100, 50), ("b", 50, 25), ("c", 10, 5)])
    assert [r["bar"] for r in rows] == [1000, 500, 100], rows
    assert rows[0]["cols"] == ["100", "50%"], rows[0]
    # 값이 다 0 이면 막대는 0 이고 **터지지 않는다**(0 으로 나누는 자리다).
    zero = ss._bar_rows([("a", 0, 0)])
    assert zero[0]["bar"] == 0, zero


async def test_the_warn_history_opens_the_newest_day_and_folds_the_rest():
    """경고 이력은 날짜별로 접힌다 — **최신 날짜는 펴 둔다**.

    전부 접혀 있으면 판을 열자마자 「방금 무슨 경고가 있었나」를 못 본다(한 번 더 눌러야
    안다). 그리고 접힘·펼침은 **클라가 드는 상태**라(스펙의 `expand`) 서버가 그것을 들면
    클라마다 다른 판을 봐야 할 때 갈린다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    srv = _TokenSrv()
    spec = ss.open_spec(srv, None, "warn-history")
    days = [r for r in spec["rows"] if r.get("depth") == 0 and r.get("expand")]
    assert len(days) == 2, spec["rows"]
    assert days[0]["expand"] == "open" and days[1]["expand"] == "shut", days
    # 펴진 날짜 밑에는 그 날의 경고 줄이 있다(깊이 1).
    assert any(r.get("depth") == 1 for r in spec["rows"]), spec["rows"]
    # 접힌 날짜를 누르면 펴진다(클라가 실어 보낸 «지금 펴진 목록» 위에서 뒤집는다).
    opened_now = [r["key"] for r in days if r["expand"] == "open"]
    nxt = ss.action(srv, None, {"id": "claude-warn-history", "do": "toggle",
                                "input": days[1]["key"],
                                "state": {"warn_open": opened_now}})
    got = {r["key"]: r.get("expand") for r in nxt["rows"] if r.get("depth") == 0}
    assert got[days[1]["key"]] == "open", got
    # 이력이 없으면 **빈 판이 아니라 사유**다.
    class _Empty(_TokenSrv):
        def _read_warn_history(self, limit=50):
            return []
    empty = ss.open_spec(_Empty(), None, "warn-history")
    assert empty["note"], empty


async def test_the_limit_and_model_panels_link_to_each_other():
    """정본은 모델·컨텍스트와 한도를 **한 탭**에 담는다(`[한도]` 탭의 첫 두 행이 모델이다).

    GUI 는 판을 잇는 줄로 같은 곳에 닿는다(사용자 결정 ⓒ) — 정본의 판 구성을 흔들지 않고
    한 자리에서 셋에 닿는다. ⛔ 판정은 **라벨이 아니라 열쇠**로 한다: 라벨은 번역을 타므로
    영어 UI 에서 그 줄이 죽는다.
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec

    class _Srv:
        _usage = {"session": {"pct": 42, "reset": "3:00 PM (KST)"}}
        _usage_ts = None

    limits = ss.open_spec(_Srv(), None, "usage-panel")
    keys = [r["key"] for r in limits["rows"]]
    assert ss._GOTO_MODEL in keys, keys
    # 그 줄을 누르면 **닫히지 않고** 모델 판이 온다.
    nxt = ss.action(_Srv(), None, {"id": "claude-usage-panel", "do": "apply",
                                   "input": ss._GOTO_MODEL})
    assert nxt and nxt["id"] == "model", nxt

    # 반대 방향도 같다.
    model = ss.open_spec(_Srv(), None, "model")
    assert ss._GOTO_LIMITS in [r["key"] for r in model["rows"]], model["rows"][-1]
    back = ss.action(_Srv(), None, {"id": "model", "do": "apply",
                                    "input": ss._GOTO_LIMITS})
    assert back and back["id"] == "claude-usage-panel", back

    # ⛔ 대조군: 보통 줄(모델 하나)은 여전히 **적용하고 닫는다**(잇는 줄이 그것을 안 먹었다).
    first = next(r["key"] for r in model["rows"] if r["key"] != ss._GOTO_LIMITS)
    done = ss.action(_Srv(), None, {"id": "model", "do": "apply", "input": first})
    assert done and done.get("t") == "plugin_screen_close", done


async def test_the_limit_panel_carries_ratios_not_glyph_bars():
    """한도 판은 **비율**을 싣는다 — `█`·`░` 를 실으면 받는 클라가 텍스트 UI 를 그린다."""
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec

    class _Srv:
        _usage = {"session": {"pct": 42, "reset": "3:00 PM (KST)"},
                  "week_all": {"pct": 7, "reset": "Mon"}}
        _usage_ts = None

    spec = ss.open_spec(_Srv(), None, "limits")
    assert spec["kind"] == "table", spec
    bars = [r.get("bar") for r in spec["rows"] if "bar" in r]
    assert bars == [420, 70], spec["rows"]
    joined = "".join(str(r) for r in spec["rows"]) + spec.get("text", "")
    for glyph in ("█", "░", "▉"):
        assert glyph not in joined, f"서버가 막대를 글자로 그렸다: {glyph}"


async def test_the_machines_spec_carries_a_ratio_not_a_drawn_bar():
    """머신별 판(pytmux-371 ③) — 서버는 **비율만** 싣는다.

    ⛔ `█` 같은 글자를 서버가 실으면 그 순간 서버가 UI 를 알게 되고(설계 §10 위험표),
    격자 없는 GUI 는 그 글자를 다시 해석해야 한다. 그래서 계약은 «천분율 정수» 하나다 —
    막대를 몇 픽셀로 그릴지는 뷰가 안다(사용자 지시: 인터페이스는 GUI 기반).
    """
    import importlib
    ss = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec

    class _Srv:
        def _tokens_db_conn(self):
            return None

    # 재료가 없으면 **빈 판이 아니라 사유**다(이 저장소의 규율).
    empty = ss.open_spec(_Srv(), None, "claude-token-machines")
    assert empty["id"] == "claude-token-machines" and empty["kind"] == "table", empty
    # 재료가 없으면 **빈 판이 아니라 사유**다. ⚠ 줄이 아주 비지는 않는다 — 판을 잇는
    #   허브 줄은 늘 있다(정본의 탭 띠에 해당). 그러니 «자료 줄이 없다» 로 잰다.
    data_rows = [r for r in empty["rows"] if "bar" in r]
    assert data_rows == [] and empty["note"], empty

    # 비율은 천분율 정수이고 글자 막대가 아니다.
    rows = [("이 머신", 1200, 1.0), ("91ddca94", 300, 0.25)]
    with harness.patched(ss, _machine_rows=lambda server: (rows, "")):
        spec = ss.open_spec(_Srv(), None, "token-machines")
    bars = [r["bar"] for r in spec["rows"] if "bar" in r]
    assert bars == [1000, 250], spec["rows"]
    assert all(isinstance(b, int) for b in bars), spec["rows"]
    joined = "".join(str(r) for r in spec["rows"])
    for glyph in ("█", "▇", "▆"):
        assert glyph not in joined, f"서버가 막대를 글자로 그렸다: {glyph}"


async def test_the_machines_screen_is_reachable_as_a_command():
    """팔레트에서 열 수 있어야 한다 — 화면만 있고 여는 길이 없으면 죽은 줄이다(pytmux-35)."""
    import importlib
    mod = importlib.import_module("pytmuxlib.plugins.claude-code")
    names = {c[0] for c in mod.COMMANDS} if hasattr(mod, "COMMANDS") else set()
    if not names:
        # 표 이름이 다르면 등록 튜플에서 직접 찾는다(이 가드가 공허해지지 않게).
        names = {n for n in mod.NOARG}
    assert "claude-token-machines" in mod.NOARG, mod.NOARG
    # 그리고 그 이름이 **실제로 화면을 내는** 이름이라야 한다.
    ss = mod.screenspec
    assert "claude-token-machines" in ss.MACHINES and "claude-token-machines" in ss.IDS


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


class _FakePty:
    """패널의 pty — 받은 바이트만 들고 있는다.

    ☠ **`patched(type(pane), write=…)` 로 심지 마라**(pytmux-173). 프로덕션
    `pytmuxlib.model.Pane` 에는 `write` 가 **없다** — 시험이 그 이름을 진짜 클래스에
    만들어 붙이는 바람에, 플러그인 셋이 `pane.write` 오타로 라이브에서 늘
    `AttributeError` 로 죽는 동안 이 파일의 두 시험은 늘 초록이었다. 가짜는
    프로덕션에 **있는** 이름(`pane.pty`)에만 단다. 전수 오라클은
    `tests/test_pane_write_typo.py`.
    """

    def __init__(self, sink):
        self.sink = sink

    def write(self, data):
        self.sink.append(data)


@contextlib.contextmanager
def _pty_capture(pane):
    """`pane.pty` 를 가짜로 바꿔 쓴 바이트를 모은다(끝나면 되돌린다)."""
    wrote = []
    real = pane.pty
    pane.pty = _FakePty(wrote)
    try:
        yield wrote
    finally:
        pane.pty = real


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
        # ★ 계약이 바뀌었다(pytmux-11 B): 이 화면은 **평면 목록이 아니라 트리**다.
        #   종전에는 첫 줄이 `..`(부모로 올라가는 길)였는데, 트리에서는 부모가 **실제
        #   줄로 위에 있고** 올라가는 손은 `←` 다 — 제보가 요구한 그 변화다.
        spec = plugin._open_tree({"path": here, "cwd": here})
        assert spec["kind"] == "list" and spec["id"] == "ncd"
        # 지금 서 있는 자리가 트리 안에 있고, 커서가 거기 선다.
        assert spec["rows"][spec["selected"]]["key"] == here, spec["selected"]
        # 그리고 그 줄은 **현재 자리**로 표시된다(정본은 노랑 + 표식).
        assert spec["rows"][spec["selected"]]["tag"] == "cwd", spec["rows"][spec["selected"]]
        # 스펙이 자기 키를 정한다(클라는 이 표에 있는 것만 먹는다). 글자뿐 아니라
        # **이름 있는 키**도 실린다 — 트리는 `←→` 로 접고 편다.
        assert spec["keys"] == {"enter": "into", "c": "cd",
                                "right": "expand", "left": "collapse"}

        # `cd` 는 패널에 명령을 넣고 화면을 닫는다 — 정본 Enter 와 같은 결과다.
        pane = sess.active_window.active_pane
        with _pty_capture(pane) as wrote:
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


async def test_mdir_lists_a_directory_as_a_panel_the_client_can_draw(tmp_path=None):
    """다열 판 스펙의 모양이 계약이다 — 줄의 `key` 가 **절대경로**(그 줄의 뜻)라야 다음
    액션이 자리가 아니라 그 항목을 가리킨다."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        mine = {"path": tmp, "tags": []}
        spec = _mdir()._spec(mine, 0, "")
        assert spec["kind"] == "panel" and spec["id"] == "mdir", spec
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


async def test_mdir_panel_carries_the_column_count_and_the_head_and_foot_lines():
    """다열 판이 스펙으로 나르는 것 셋(pytmux-126 · 설계 §4.3 `panel`).

    ⛔ **셋을 한 칸에 겹치지 않는다.** `note` 는 «실패했거나 비었다»라 평상시엔 비고
    `foot` 은 평상시에 늘 있는 자료다 — 겹쳐 놓으면 실패한 순간 집계가 사라진다.

    ⚠ 열 수는 **제안**이라 `0`(자동)이 기본이고, 정본 `Alt+1~6` 이 그것을 못박는다.
    그 손이 스펙을 못 타면 같은 키가 클라마다 다른 일을 한다."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        p = _mdir()
        mine = {"path": tmp, "tags": []}
        spec = p._spec(mine, 0, "")
        # ⑴ 기본은 «자동»이다 — 여기서 수를 못박으면 좁은 창에서 이름이 안 보인다.
        assert spec["columns"] == 0, spec["columns"]
        # ⑵ 꼬리줄 = 집계. 셈은 정본 화면과 **같은 함수**라(`listing.counts`) 서식까지 같다.
        assert " File  " in spec["foot"] and " Dir  " in spec["foot"], spec["foot"]
        assert spec["foot"].endswith("N"), ("정렬 표시가 없다: %r" % spec["foot"])
        # ⑶ 평상시 `note` 는 비어 있다 — 빈 목록·실패와 섞이면 안 된다.
        assert spec["note"] == "", spec["note"]
        # 머리줄 = 볼륨. 못 재는 자리면 **빈 줄**이고, 그때 클라는 안 그린다.
        assert spec["head"] == "" or spec["head"].startswith("Free "), spec["head"]

        # 태그를 찍으면 꼬리줄이 그 사실을 말한다 — 손만 있고 눈이 없으면 안 된다.
        a = os.path.join(tmp, "a.txt")
        assert "Sel " in p._tag(mine, a, 2)["foot"], "태그가 집계줄에 안 보인다"

        # 정본 `Alt+3` 이 열을 못박고 `Alt+0` 이 자동으로 되돌린다.
        assert spec["keys"]["alt-3"] == "cols-3", spec["keys"]
        assert spec["keys"]["alt-0"] == "cols-0", spec["keys"]
        assert p._spec({**mine, "cols": 3}, 0, "")["columns"] == 3
        assert p._spec({**mine, "cols": 0}, 0, "")["columns"] == 0

        # 마스크가 걸리면 **제목이** 말한다 — 안 그러면 "파일이 절반 사라졌다"로 보인다.
        masked = p._spec({**mine, "mask": ["*.txt"]}, 0, "")
        assert "[*.txt]" in masked["title"], masked["title"]
        assert "mask" in masked["i18n"]["title"]["args"], masked["i18n"]["title"]


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
        assert spec["kind"] == "panel", spec


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
            with _pty_capture(pane) as wrote:
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


def test_ncd_into_includes_the_chosen_path_for_mdir_update():
    """`ncd` 의 "into" 응답에 경로가 실린다 — mdir 갱신용(pytmux-207).

    정본과 달리 콜백이 없으므로, ncd 응답에 경로를 담아 돌려준다."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        sub = os.path.join(tmp, "sub")
        state = {}
        # ncd 의 "into"(Enter) 응답은 경로를 담아 돌려준다
        from pytmuxlib.plugins.ncd import PLUGIN as ncd_plugin
        resp = ncd_plugin.plugin_screen(None, None, {
            "id": "ncd", "do": "into", "input": sub, "state": state,
        })
        assert resp["t"] == "plugin_screen_close" and resp["id"] == "ncd"
        assert resp.get("input") == sub, f"경로가 없다: {resp}"


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


# ── 스펙의 글은 **카탈로그를 거친다**(로케일 2026-08-02o) ──────────────────────
#
# 게이트가 못 보던 자리였다: 픽스처는 정본 카탈로그에서 뽑히므로, 스펙에 **직접 적은**
# 한국어는 영어 표(`en_server.rs`)에 못 들어가고 영어 사용자에게 그대로 한국어로 뜬다.
# 정적 스캔은 생성기가 하고(`wire_literals` 래칫), 여기서는 **실제로 지어진 스펙**을
# 재서 그 스캔이 재는 것과 제품이 내보내는 것이 같은지 붙잡는다.

async def test_a_screen_spec_says_the_same_words_the_catalog_does():
    """스펙의 제목·안내·빈 줄은 카탈로그 값이어야 한다 — 손으로 적으면 영어가 안 된다.

    ⚠ 이 오라클은 "카탈로그에 있나"만 보고 **문구가 예쁜가**는 안 본다. 그래도 값이
    있다: 손으로 적은 판은 카탈로그 판과 **괄호 하나가 달랐고**, 그 한 글자 때문에
    `t()` 가 못 찾아 한국어로 떴다(실측 — p4changes·ncd·prompt-history·claude-resume).
    """
    from pytmuxlib import i18n
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        values = set(i18n._CATALOG["ko"].values())

        plugin = _plugin(srv, "claude-prompt-history")
        sess.active_window.active_pane._ph_history = []
        spec = plugin._open_spec(srv, sess)
        for field in ("title", "hint", "note"):
            assert spec[field] in values, (
                "prompt-history 스펙의 %s 가 카탈로그에 없다: %r" % (field, spec[field]))

        plugin = _plugin(srv, "claude-resume")
        spec = await plugin._open_spec({})
        # `note` 는 세션이 있으면 빈 문자열이다 — 빈 것은 글이 아니라 "할 말 없음"이라
        # 카탈로그를 안 거친다(빈 줄까지 번역 대상으로 세면 오라클이 거짓말을 한다).
        # 빈 note 의 문구는 위 prompt-history 가 덮는다(거기서는 비게 만들 수 있다).
        for field in ("title", "hint", "note"):
            assert not spec[field] or spec[field] in values, (
                "claude-resume 스펙의 %s 가 카탈로그에 없다: %r" % (field, spec[field]))
    finally:
        await teardown(srv, task, sock)


async def test_mdir_says_everything_through_the_catalog():
    """`mdir` 이 내보내는 **모든 칸**이 카탈로그를 거친다(2026-08-02p).

    다른 스펙과 달리 이 화면은 제목·안내·빈 줄만이 아니라 **줄의 칸**(`<상위>`·
    `<드라이브>`)과 **물음**(삭제·복사)과 **결과 한 줄**까지 서버가 짓는다. 그 자리
    하나가 손으로 적힌 채 남으면 영어 사용자에게 그 줄만 한국어로 뜬다.

    이름(`label`)은 일부러 안 본다 — 파일 이름은 자료라 번역 대상이 아니다."""
    import os
    import tempfile
    from pytmuxlib import i18n
    values = set(i18n._CATALOG["ko"].values())
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        p = _mdir()
        mine = {"path": tmp, "tags": []}
        spec = p._spec(mine, 0, "")
        # 제목은 자리가 있어(`{path}`) 지어진 글이 아니라 **포맷**이 카탈로그의 것이다.
        assert spec["i18n"]["title"]["fmt"] in values, spec["i18n"]
        assert spec["hint"] in values, (
            "mdir 스펙의 hint 가 카탈로그에 없다: %r" % (spec["hint"],))
        # `..` 와 드라이브 줄의 칸도 카탈로그 값이다(크기·시각은 자료라 제외).
        assert spec["rows"][0]["cols"] == [i18n.t("mdir.parent")], spec["rows"][0]
        # 빈 디렉터리·물음·결과 — 서버가 짓는 나머지 자리.
        empty = p._spec({"path": os.path.join(tmp, "sub"), "tags": []}, 0, "")
        assert empty["note"] in values, empty["note"]
        ask = p._begin(mine, "delete", os.path.join(tmp, "a.txt"), 1)
        assert ask["i18n"]["title"]["fmt"] in values, ask["i18n"]
        assert p._begin(mine, "mkdir", "", 0)["title"] in values, "mkdir 물음"
        # ⚠ **머리·꼬리줄은 일부러 카탈로그 밖이다**(pytmux-126). `Free`·`File`·`Dir`·
        #   `Byte`·`free` 는 Mdir III 원조의 서식이고 색과 같은 부류다 — 정본도 로케일과
        #   무관하게 이 글자를 쓴다. 여기서 `i18n.t` 를 부르면 **서버 로케일이 스펙에
        #   실려** 영어 클라로 샌다(그 자리가 이 시험이 지키는 것의 반대편이다).
        assert not spec["head"] or spec["head"].startswith("Free "), spec["head"]
        assert "File" in spec["foot"] and spec["foot"] not in values, spec["foot"]


async def test_mdir_tells_why_it_failed_in_words_the_client_can_translate():
    """왜 안 됐는지는 **그 줄의 칸**으로 간다 — 결과 한 줄은 수만 말한다.

    종전에는 `실패 2건 — a(이미 있습니다)` 처럼 사유를 결과 줄에 이어 붙였다. 그러면
    번역이 안 된다: 사유를 인자로 넘기면 영어 클라가 자기 포맷에 한국어 조각을 끼우고
    (`i18n.phrase` 의 그 함정), 판을 조작×사유로 만들면 쉰 개가 된다. 사유가 **그 자체로
    카탈로그의 한 줄**이면 클라가 `t()` 로 읽는다(`PluginRow::say_cols`).
    """
    import os
    import tempfile
    from pytmuxlib import i18n
    values = set(i18n._CATALOG["ko"].values())
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        p = _mdir()
        mine = {"path": tmp, "tags": []}
        p._spec(mine, 0, "")
        # 둘을 `sub` 로 옮긴다 — 파일은 되고 `sub` 자신은 **자기 안으로**라 안 된다.
        # 일괄 작업이 절반만 성공하는 것이 정상이고(서버가 개별 실패를 모아 계속한다)
        # 바로 그때 "무엇이 왜 안 됐나"가 필요하다.
        sub = os.path.join(tmp, "sub")
        mine["tags"] = [os.path.join(tmp, "a.txt"), sub]
        p._begin(mine, "move", "", 1)
        spec = p._apply(mine, sub, 1)
        row = next(r for r in spec["rows"] if r["label"].strip().startswith("sub"))
        assert "✗" in row["cols"], "안 된 줄에 표식이 없다: %r" % (row,)
        why = [c for c in row["cols"] if c in values]
        assert why, "사유가 카탈로그를 안 거쳤다(영어 클라엔 한국어로 뜬다): %r" % (row,)
        # 표식과 사유는 **다른 칸**이다 — 이어 붙이면 그 칸은 카탈로그의 글이 아니다.
        assert not any(c.startswith("✗ ") and len(c) > 2 for c in row["cols"]), row
        # 결과 한 줄은 수만 말하고, 재료를 싣는다.
        assert spec["i18n"]["note"]["fmt"] in values, spec["i18n"]
        assert "f" in spec["i18n"]["note"]["args"], spec["i18n"]["note"]
        # 사유는 한 번만 보인다 — 다음 화면에는 안 남는다.
        assert "✗" not in str(p._spec(mine, 0, "")["rows"]), "표식이 눌러앉았다"


async def test_a_composed_title_carries_the_ingredients_not_just_the_words():
    """자리가 있는 제목은 **재료**(`i18n`)도 실어야 한다 — 원문이 키가 못 되기 때문.

    `ncd` 의 제목은 `디렉터리 — {path}` 다. 글만 보내면 영어 클라가 그 문자열을 표에서
    못 찾아 한국어가 그대로 뜬다. 그래서 `fmt`+`args` 를 같이 싣고 클라가 `tf` 로
    자기 로케일에서 다시 짓는다(`i18n_say`).
    """
    import os
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        plugin = _plugin(srv, "ncd")
        path = os.path.abspath(os.sep)
        spec = plugin._open_tree({"path": path, "cwd": path})
        assert spec["i18n"]["title"]["fmt"] == "디렉터리 — {path}", spec["i18n"]
        assert spec["i18n"]["title"]["args"] == {"path": path}, spec["i18n"]
        # 글도 그대로 온다 — 재료를 모르는 클라는 종전과 똑같은 것을 본다.
        assert spec["title"].endswith(path), spec["title"]
    finally:
        await teardown(srv, task, sock)


# ---------------------------------------------------------------------------
# claude-perm-mode(pytmux-2) — 팔레트에 없는 화면이다. **패널 안 footer 를 눌러야**
# 열리고, 그래서 "어느 패널을 눌렀나"가 뜻의 일부다.
# ---------------------------------------------------------------------------

class _ScreenSpy:
    """`plugin_state` 를 가진 클라 — 화면이 판 상태를 여기 적는다(Tier C · P5)."""

    def __init__(self):
        self.sent = []
        self.plugin_state = {}


async def _screen(srv, sess, client, action, msg):
    """화면 명령 하나를 태우고 그 클라에게 간 스펙을 돌려준다."""
    async def fake_send_to(self, c, obj):
        if c is client:
            client.sent.append(obj)
        return True

    from pytmuxlib.servercmd import _CMD_TABLE
    with harness.patched(type(srv), _send_to=fake_send_to):
        await _CMD_TABLE[action][0](srv, client, sess, msg)
    return client.sent[-1] if client.sent else None


async def test_the_permission_screen_lists_what_the_canonical_popup_lists():
    """정본 `PermModeScreen` 과 **같은 표**에서 나와야 한다 — 두 벌이면 한쪽만 모드를
    하나 더 갖거나 위험 모드를 덜 숨긴다."""
    import importlib
    plugin = importlib.import_module("pytmuxlib.plugins.claude-code")

    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p._perm_mode = "plan"
        c = _ScreenSpy()
        spec = await _screen(srv, sess, c, "plugin_open",
                             {"name": "claude-perm-mode", "args": [p.id]})
        assert spec and spec["t"] == "plugin_screen", spec
        assert spec["id"] == "claude-perm-mode" and spec["kind"] == "list", spec
        keys = [r["key"] for r in spec["rows"]]
        assert keys == [k for k, _ in plugin.perm_modes("plan", False)], keys
        # 위험 모드는 가용할 때만 — 안 그러면 도달 못 하는 모드를 고르게 된다.
        assert "bypass" not in keys, keys
        # 지금 모드에 표가 붙는다(어디에 있는지 모른 채 고르면 안 된다).
        marked = [r["key"] for r in spec["rows"] if r["cols"]]
        assert marked == ["plan"], spec["rows"]

        p._bypass_seen = True
        spec2 = await _screen(srv, sess, c, "plugin_open",
                              {"name": "claude-perm-mode", "args": [p.id]})
        assert "bypass" == spec2["rows"][-1]["key"], spec2["rows"]
    finally:
        await teardown(srv, task, sock)


async def test_the_permission_screen_changes_the_pane_that_was_clicked():
    """★ 비활성 Claude 패널의 footer 를 눌렀는데 **활성 패널**의 모드가 바뀌면 안 된다.

    화면 안 동작(`plugin_action`) 프레임에는 패널 칸이 없다(계약이 id·do·row·input
    넷이다). 그래서 연 패널을 판 상태에 적어 두는데, 그 적기를 빠뜨리면 증상은 조용하다
    — 팝업은 제대로 뜨고 고르기도 되며 **엉뚱한 패널**이 바뀔 뿐이다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "h")
        panes = win.panes()
        assert len(panes) == 2, panes
        clicked = next(p for p in panes if p is not win.active_pane)

        c = _ScreenSpy()
        await _screen(srv, sess, c, "plugin_open",
                      {"name": "claude-perm-mode", "args": [clicked.id]})
        got = []
        with harness.patched(type(srv), set_claude_perm_mode=(
                lambda self, s, target, pane_id=None: got.append((target, pane_id)))):
            closed = await _screen(srv, sess, c, "plugin_action",
                                   {"id": "claude-perm-mode", "do": "apply",
                                    "row": 0, "input": "accept"})
        assert got == [("accept", clicked.id)], (got, clicked.id,
                                                 win.active_pane.id)
        # 고르면 닫는다 — 모드는 바로 안 바뀌므로(서버가 idle 을 기다려 순환 주입한다)
        # 판을 열어 둔 채 다시 그리면 "안 먹었다"로 보인다.
        assert closed["t"] == "plugin_screen_close", closed
    finally:
        await teardown(srv, task, sock)


async def test_the_permission_labels_travel_as_keys_not_as_korean():
    """이 줄들은 이제 소켓을 건넌다 — 한국어 원문을 키로 쓰면 로케일 그물
    (`gen_server_strings.py` 가 네임스페이스로 고른다)에 안 걸려 **영어 사용자에게
    한국어로** 뜬다. 종전 자리(`screens.py` 클래스 속성)가 딱 그 모양이었다."""
    import importlib
    plugin = importlib.import_module("pytmuxlib.plugins.claude-code")
    from pytmuxlib import i18n

    for _key, label in plugin.PERM_MODES + [plugin.PERM_BYPASS]:
        assert label.startswith("pscreen."), label
        assert i18n._CATALOG["en"].get(label), f"{label} 에 영어 짝이 없다"
        assert i18n._CATALOG["ko"].get(label), f"{label} 에 한국어 원문이 없다"


async def test_a_pane_id_that_arrived_as_text_still_names_that_pane():
    """★ 와이어의 패널 id 는 **문자열**이다 — 그걸 안 고치면 조용히 활성 패널이 된다.

    GUI 는 자리를 누를 때 `args: ["7"]` 로 보낸다(`pane.to_string()`). 그런데
    `Window.pane_by_id` 는 `p.id == pid` 로 비교하므로 `3 == "3"` 이 거짓이고, 부르는
    쪽은 죄다 `... or win.active_pane` 으로 우아하게 내려간다 — 그래서 비활성 Claude
    패널의 footer 를 눌러도 **활성 패널**이 바뀌었다. id 를 실어 보낸 이유가 통째로
    사라지는데 증상은 조용하다(팝업은 제대로 뜬다).

    위 오라클이 이걸 못 잡은 이유도 적어 둔다: 그 테스트는 `args: [p.id]` 로 **int** 를
    넘긴다. 정본이 부르는 모양이지 GUI 가 보내는 모양이 아니었다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "h")
        clicked = next(p for p in win.panes() if p is not win.active_pane)

        c = _ScreenSpy()
        await _screen(srv, sess, c, "plugin_open",
                      {"name": "claude-perm-mode", "args": [str(clicked.id)]})
        got = []
        with harness.patched(type(srv), set_claude_perm_mode=(
                lambda self, s, target, pane_id=None: got.append((target, pane_id)))):
            await _screen(srv, sess, c, "plugin_action",
                          {"id": "claude-perm-mode", "do": "apply",
                           "row": 0, "input": "accept"})
        assert got == [("accept", clicked.id)], (got, clicked.id,
                                                 win.active_pane.id)
    finally:
        await teardown(srv, task, sock)


# ---------------------------------------------------------------------------
# claude-remote-control(pytmux-2 잔여) — 이것도 팔레트에 없다. 정본에서 그 자리는
# 곧바로 토글이 아니라 **판을 먼저 열고** `[r]` 로 토글한다.
# ---------------------------------------------------------------------------

async def test_the_remote_control_screen_says_what_the_canonical_popup_says():
    """글이 두 벌이 되면 두 클라의 설명이 갈린다 — 같은 카탈로그 키에서 와야 한다."""
    from pytmuxlib import i18n

    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        c = _ScreenSpy()
        spec = await _screen(srv, sess, c, "plugin_open",
                             {"name": "claude-remote-control", "args": [str(p.id)]})
        assert spec and spec["t"] == "plugin_screen", spec
        assert spec["id"] == "claude-remote-control", spec
        assert spec["kind"] == "text", spec
        assert spec["title"] == i18n.t("ccmsg.rc_title"), spec
        assert spec["text"] == i18n.t("ccmsg.rc_body"), spec
        # ★ `[r]` 이 **실제로 실린다** — 안 실으면 정본에는 있는 손이 GUI 에만 없고,
        #   그건 "판은 뜨는데 아무것도 못 한다"가 된다(본문은 [r] 을 쓰라고 적는다).
        assert spec["keys"] == {"r": "toggle"}, spec
        assert "[r]" in spec["text"], spec["text"]
    finally:
        await teardown(srv, task, sock)


async def test_the_remote_control_toggle_types_rc_into_the_pane_that_was_clicked():
    """`[r]` → 그 패널에 `/rc` 주입 + 닫기(정본 `InfoScreen` 의 hide_key 와 같은 손).

    ★ 여기도 **누른 그 패널**이다. 권한모드가 먼저 밟은 자리라 같은 자를 댄다 —
    화면 안 동작 프레임에는 패널 칸이 없으니 여는 쪽이 판 상태에 적어야 한다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "h")
        clicked = next(p for p in win.panes() if p is not win.active_pane)

        c = _ScreenSpy()
        await _screen(srv, sess, c, "plugin_open",
                      {"name": "claude-remote-control", "args": [str(clicked.id)]})
        got = []
        with harness.patched(type(srv), _pc_inject=(
                lambda self, pane, text: got.append((pane.id, text)))):
            closed = await _screen(srv, sess, c, "plugin_action",
                                   {"id": "claude-remote-control", "do": "toggle",
                                    "row": 0, "input": None})
        assert got == [(clicked.id, "/rc")], (got, clicked.id, win.active_pane.id)
        assert closed["t"] == "plugin_screen_close", closed
        assert closed["id"] == "claude-remote-control", closed
    finally:
        await teardown(srv, task, sock)
