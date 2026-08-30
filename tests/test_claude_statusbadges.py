"""claude-code 상태줄 표식을 **한 벌로 합친 것**이 종전 출력을 재현하는가 (M4 P6 후반).

# 왜 골든이 먼저인가

이 자리는 "없는 것을 만든다"가 아니라 **"두 벌을 합친다"** 였다. 정본(`clientstatus.
render_segs`)과 네이티브(GUI 가 날 필드로 자기가 조립하던 `claude_badge`)가 같은 것을
서로 다르게 그리고 있었고, 합치면 **둘 중 하나가 조용히 바뀐다**. 시계·달력에서 배운
규율이 그래서 "합치기 전 대조 + 골든"이다(08-02b: *"합치면 오라클을 하나 잃는다 —
두 벌이 서로를 검증하던 것"*).

여기 적힌 기대값은 **합치기 전 정본이 실제로 그리던 글**이다. 규칙을 옮긴 뒤에도
글자 하나까지 같아야 한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pytmuxlib import i18n  # noqa: E402
from pytmuxlib import plugins  # noqa: E402

plugins.load()


def _mod():
    """플러그인 디렉토리 이름에 `-` 가 있어 평범한 `import` 문이 안 된다 — 파일 경로로
    직접 물린다(플러그인 모듈을 재는 다른 테스트와 같은 방식)."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pytmuxlib", "plugins", "claude-code", "statusbadges.py")
    spec = importlib.util.spec_from_file_location("_cc_statusbadges", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _texts(fields, lang="ko"):
    i18n.set_locale(lang)
    try:
        return [(b["kind"], b["text"]) for b in _mod().badges(fields)]
    finally:
        i18n.set_locale("ko")


def test_the_merged_rule_reproduces_what_the_canonical_used_to_draw():
    # 합치기 전 정본이 그리던 것 — 모델 배지 + 5시간 사용률.
    got = _texts({"claude_active": True, "claude_model": "opus-5", "tok5h_pct": 12})
    assert got == [("model", "opus-5"), ("usage", "12%/5h 사용")], got


def test_a_sonnet_pane_shows_the_weekly_number_instead():
    # 5h ↔ 주간 Sonnet 은 상호배타다(서버가 한쪽만 채운다).
    got = _texts({"claude_active": True, "week_sonnet_pct": 40})
    assert got == [("usage", "40%/주(Sonnet)")], got


def test_an_unmeasured_pane_still_takes_the_spot():
    # ★ 비워 두면 사용자는 "사용량 표시가 없는 클라"를 본다(제보 2026-06-18).
    got = _texts({"claude_active": True})
    assert got == [("usage", "?%/5h 사용")], got


def test_nothing_is_drawn_for_a_pane_that_is_not_claude():
    assert _texts({"claude_active": False, "claude_model": "opus-5"}) == []


def test_the_countdown_and_the_warning_come_after_the_usage():
    # 순서는 뜻의 일부다 — 정본의 종전 순서 그대로.
    got = _texts({
        "claude_active": True, "tok5h_pct": 5,
        "claude_pending": {"eta": 30},
        "claude_warn": "⚠ 동일 결과 3회 반복 — 루프 의심",
        "claude_warn_kind": "repeat", "claude_warn_n": 3,
    })
    assert [k for k, _ in got] == ["usage", "pending", "warn"], got
    assert got[1][1] == " ⏳ 자동재개 30s(입력=취소) ", got[1]
    # ⚠ 뒤 공백 하나가 더 붙는다(컬러 이모지가 다음 칸을 먹는다 — 제보 2026-06-17).
    assert got[2][1] == " ⚠  동일 결과 3회 반복 — 루프 의심 ", got[2]


def test_a_countdown_does_not_mix_two_languages():
    """★ 이 오라클이 이 슬라이스의 함정을 붙잡는다.

    라벨을 **인자로** 넘기면 클라가 자기 로케일 포맷에 서버 로케일 조각을 끼워
    `⏳ 자동재개 30s (input=cancel)` 같은 것을 만든다. 라벨은 포맷 안에 있어야 한다."""
    spec = _mod().badges({"claude_pending": {"eta": 30}})[0]["i18n"]["text"]
    assert spec["args"] == {"eta": "30"}, spec
    assert "자동재개" in spec["fmt"], spec
    assert "label" not in spec["args"], "번역 대상이 인자로 샜다"


def test_every_composed_badge_carries_the_ingredients_to_be_retranslated():
    """자리가 있는 글은 **전부** 재료를 싣는다 — 안 실으면 영어 클라에 한국어가 뜬다.

    GUI 는 종전에 이 줄을 자기가 조립해 그 문제가 없었으므로, 재료 없이 옮기는 것은
    회귀다(로케일 ⓑ)."""
    for fields in (
        {"claude_active": True, "tok5h_pct": 12},
        {"claude_active": True, "week_sonnet_pct": 40},
        {"claude_pending": {"eta": 9}},
        {"claude_warn": "⚠ x", "claude_warn_kind": "repeat", "claude_warn_n": 2},
    ):
        for b in _mod().badges(fields):
            if "{" in b["text"] or any(c.isdigit() for c in b["text"]):
                assert "i18n" in b, f"재료 없이 나간다: {b}"
                assert b["i18n"]["text"]["fmt"], b


def test_the_english_locale_is_actually_reachable():
    """정본 카탈로그에 en 이 있으니 서버가 en 이면 영어가 나온다 — 재료의 전제다."""
    assert _texts({"claude_active": True, "tok5h_pct": 12}, lang="en") == [
        ("usage", "12%/5h used")
    ]


# ---- 누르는 자리 (pytmux-20) ------------------------------------------------
#
# 이 칸은 오래 비어 있었고 그 이유가 적혀 있었다: *"`do` 를 실어 두면 선언은 있고 배선이
# 없는 칸이 하나 더 생긴다."* 한도 판(`usage-panel`)이 Tier C 화면을 내면서 조건이 섰다.

def test_only_the_badge_with_a_screen_is_clickable():
    """`do` 는 **그 화면이 실제로 있는 표식에만** 실린다.

    넓게 실으면 눌리는 것처럼 보이고 아무 일도 안 나는 칸이 생긴다 — 종전에 이 칸을
    통째로 비워 둔 바로 그 이유다."""
    got = _mod().badges({"claude_active": True, "claude_model": "opus-5",
                         "tok5h_pct": 12,
                         "claude_pending": {"eta": 9},
                         "claude_warn": "⚠ x"})
    opens = {b["kind"]: b.get("do") for b in got}
    assert opens["usage"] == "usage-panel", opens
    # ★ 모델도 눌린다(pytmux-379) — 규칙은 그대로고 **사실이 바뀌었다**: 그 화면이
    #   Tier C 에 생겼다(`screenspec.MODEL`). 규칙을 지키는지는 아래 대조군이 잰다.
    assert opens["model"] == "model", opens
    for kind in ("pending", "warn"):
        assert opens[kind] is None, f"{kind} 에 화면이 없는데 누를 자리를 만들었다: {opens}"


def test_the_name_it_carries_is_one_the_plugin_actually_opens():
    """실어 보내는 이름이 **그 플러그인이 여는 이름**이라야 한다.

    한 글자만 달라도 눌러도 아무 일이 안 난다(그리고 그건 조용하다) — 죽은 명령
    (pytmux-35)이 정확히 그 부류다. 그래서 표를 눈으로 옮기지 않고 **양쪽에 물어** 맞춘다.
    """
    import importlib
    plug = importlib.import_module("pytmuxlib.plugins.claude-code").PLUGIN
    got = _mod().badges({"claude_active": True, "tok5h_pct": 12})
    name = next(b["do"] for b in got if b["kind"] == "usage")
    assert name in plug._USAGE_PANEL, (name, plug._USAGE_PANEL)
    # 모델 배지도 같은 관문을 지난다 — 이름이 **그 플러그인이 여는 이름**이라야 한다.
    got_model = _mod().badges({"claude_active": True, "claude_model": "opus-5"})
    mname = next(b["do"] for b in got_model if b["kind"] == "model")
    spec = importlib.import_module("pytmuxlib.plugins.claude-code").screenspec
    assert mname in spec.MODEL, (mname, spec.MODEL)
    # 정본의 명령 갈래도 같은 이름을 받는다(팔레트에서 되는 것이 여기서도 돼야 한다).
    assert name in importlib.import_module('pytmuxlib.plugins.claude-code').NOARG, name


def test_the_usage_panel_hands_out_a_screen_spec():
    """Tier C — 서버가 한도 판을 **자료로** 낸다(정본 `_open_usage_panel` 과 같은 함수)."""
    import importlib
    plug = importlib.import_module("pytmuxlib.plugins.claude-code").PLUGIN

    class _Srv:
        _usage = {"session": {"pct": 42, "reset": "3:00 PM (Asia/Seoul)"},
                  "week_all": {"pct": 7, "reset": "Mon"}}
        _usage_ts = None

    spec = plug.plugin_screen(_Srv(), None, {"do": "open", "name": "usage-panel"})
    # ★ 2026-08-24(pytmux-371 ④): `text`(글자 막대 76칸) → **자료**(줄 + 천분율 막대).
    #   글자 막대를 실으면 받는 클라가 «텍스트 기반 인터페이스»를 그리게 된다 —
    #   사용자 지시가 금한 그것이고, 76칸은 그 창과 무관한 남의 숫자다.
    assert spec["kind"] == "table" and spec["id"] == "claude-usage-panel", spec
    assert spec["note"] == "", "값이 있는데 '데이터 없음'을 적었다"
    # 제보가 든 세 값이 그대로 있나 — 한도 이름 · 사용 비율 · 리셋 시각.
    by_label = {r["label"]: r for r in spec["rows"]}
    row = by_label.get("세션 5h") or next(iter(spec["rows"]))
    assert any("42%" in c for c in row["cols"]), row
    assert any("3:00 PM" in c for c in row["cols"]), row
    # 막대는 **천분율 정수**이고 글자가 아니다(뷰가 면으로 그린다).
    assert row["bar"] == 420, row
    joined = "".join(str(r) for r in spec["rows"]) + spec["text"]
    for glyph in ("█", "░", "▉"):
        assert glyph not in joined, f"서버가 막대를 글자로 그렸다: {glyph}"
    # 계정·신선도처럼 비율이 없는 줄은 **막대 없이** 온다(있으면 0% 막대로 오독된다).
    assert all("bar" in r or r["cols"] == [] for r in spec["rows"]), spec["rows"]
    # 내 이름이 아니면 안 받는다(첫 비-None 채택 규약).
    assert plug.plugin_screen(_Srv(), None, {"do": "open", "name": "ncd"}) is None


def test_no_limit_data_is_a_note_not_an_empty_panel():
    """**빈 목록과 실패는 다르다** — 한도를 아직 못 재 왔으면 이유를 보인다."""
    import importlib
    plug = importlib.import_module("pytmuxlib.plugins.claude-code").PLUGIN

    class _Srv:
        _usage = None
        _usage_ts = None

    spec = plug.plugin_screen(_Srv(), None, {"do": "open", "name": "limits"})
    assert spec["text"] == "", spec
    assert spec["note"], "빈 판만 띄우면 사용자는 무엇이 잘못됐는지 모른다"


# ---- 명령 표는 한 벌이다 (pytmux-35) ----------------------------------------
#
# 이름을 액션으로 옮기는 규칙이 정본 클라 안에만 있어서 네이티브 클라는 같은 명령을
# 보낼 길이 없었다 — 그것이 죽은 줄 열여덟의 뿌리다. 표를 빼 두 소비자가 같이 쓴다.

def _cmdmap():
    import importlib
    return importlib.import_module("pytmuxlib.plugins.claude-code.cmdmap")


def test_the_table_never_names_an_action_the_server_does_not_accept():
    """⛔ **이 오라클이 이 표의 존재 이유다.** 서버가 안 받는 액션을 표가 이름 대면 그
    명령은 눌러도 **조용히 아무 일도 안 한다** — 종전(거절 알림)보다 더 조용하다.

    ⚠ **받는 자리가 둘이다**: 코어 명령표(`servercmd._CMD_TABLE`)와 플러그인
    `server_command`. 한쪽만 보면 산 명령을 죽은 것으로 읽는다 — 2026-08-03 에 실제로
    그렇게 오판해 `claude-token-account`(주인은 코어 표다)를 표에서 뺄 뻔했다."""
    import json, io, os
    from pytmuxlib.servercmd import _CMD_TABLE
    fx = json.load(io.open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "client", "crates", "proto", "tests", "fixtures",
        "plugin_server_actions.json"), encoding="utf-8"))
    known = set(fx["actions"]) | set(_CMD_TABLE)
    cm = _cmdmap()
    bad = []
    # ⚠ 인자에 따라 **액션이 갈리는** 이름이 있다(`prompt-clear-queue`: 무인자면 화면,
    #    인자가 있으면 쌓기·비우기 — pytmux-35). 무인자 하나만 물으면 그 이름의 액션을
    #    통째로 안 재게 되므로 몇 가지 인자로 눌러 본다. `None` 은 *"이번엔 액션이
    #    아니다"* 이지 실패가 아니다.
    for name in cm.names():
        got = [cm.to_action(name, a) for a in ([], ["-c"], ["아무 말"])]
        assert any(g is not None for g in got), \
            f"{name}: 어떤 인자로도 액션이 안 나온다 — 표에 이름만 있고 뜻이 없다"
        for g in got:
            if g is not None and g[0] not in known:
                bad.append((name, g[0]))
    assert not bad, f"서버가 안 받는 액션을 표가 이름 댄다: {bad}"


def test_a_core_table_action_is_reachable_through_the_new_path():
    """★ 위 오판의 회귀 가드 — **코어 표가 받는 액션**도 `plugin_cmd` 로 닿아야 한다.

    종전 설계는 플러그인 `server_command` 만 물어서, 주인이 코어 표인 액션
    (`set_claude_account`)은 새 길에서 죽었다. 지금은 서버가 **코어 표를 먼저** 본다."""
    from pytmuxlib.servercmd import _CMD_TABLE
    cm = _cmdmap()
    action, kw = cm.to_action("claude-token-account", ["나"])
    assert action == "set_claude_account" and kw == {"name": "나"}, (action, kw)
    assert action in _CMD_TABLE, "코어 표가 이 액션을 안 받는다(전제가 바뀌었다)"


def test_the_server_runs_the_same_command_the_canonical_sends():
    """정본이 `send_cmd(action, **kw)` 로 치는 것을 서버가 **같은 표로** 푼다.

    두 소비자가 같은 표를 쓴다는 것이 이 슬라이스의 전부다 — 갈리면 네이티브에서만
    죽는 명령이 다시 생긴다."""
    import importlib
    plug = importlib.import_module("pytmuxlib.plugins.claude-code").PLUGIN
    # 3-state 인자가 실제로 파싱돼 넘어가는지까지 본다(무인자 토글로 걸면 반쪽이 된다).
    cm = _cmdmap()
    assert cm.to_action("claude-auto-redraw", ["corruption"]) == (
        "set_claude_auto_redraw", {"value": "corruption"})
    assert cm.to_action("claude-resume-verify", ["strict"]) == (
        "set_claude_resume_verify", {"value": "strict"})
    assert cm.to_action("prompt-clear-message", ["작업", "끝"]) == (
        "set_prompt_clear_message", {"msg": "작업 끝"})
    # 플러그인 훅은 **옮기기만** 한다 — 실행은 서버가 한다(그래야 코어 표가 받는
    # 액션도 산다). 그 훅이 정본과 같은 표를 쓰는지 본다.
    assert plug.plugin_command_action("claude-auto-redraw", ["idle"]) == (
        "set_claude_auto_redraw", {"value": "idle"})
    # 내 것이 아니면 None — 서버가 화면 경로로 넘어간다.
    assert plug.plugin_command_action("ncd", []) is None


def test_a_screen_command_still_reaches_its_screen_through_the_new_path():
    """`plugin_cmd` 는 못 알아들으면 **화면 경로로 넘어간다** — 그래야 클라가 갈래를
    안 정해도 된다(갈래를 클라가 들면 서버와 갈리고, 갈린 순간 조용히 죽는다)."""
    import importlib
    plug = importlib.import_module("pytmuxlib.plugins.claude-code").PLUGIN
    # 한도 판은 명령 표에 없다(화면이다) → 훅은 None 을 돌려준다.
    assert plug.plugin_command_action("usage-panel", []) is None
    # 그리고 화면 경로에서는 스펙이 나온다.

    class _Srv:
        _usage = {"session": {"pct": 1, "reset": None}}
        _usage_ts = None

    spec = plug.plugin_screen(_Srv(), None, {"do": "open", "name": "usage-panel"})
    # 이 판은 2026-08-24 에 `text` → `table`(자료 + 천분율 막대)로 바뀌었다(pytmux-371 ④).
    assert spec and spec["kind"] == "table", spec


async def test_the_new_command_actually_moves_server_state():
    """★ **호출부 오라클** — 표가 맞아도 서버가 안 부르면 명령은 여전히 죽어 있다.

    라이브 서버에 `plugin_cmd` 를 먹여 상태가 실제로 움직이는지 본다. 종전에는 이 이름이
    `plugin_open` 으로 가서 *"화면 스펙을 제공하지 않습니다"* 로 거절당했다."""
    from harness import server_only, teardown
    from pytmuxlib.servercmd import _CMD_TABLE
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)

        class _C:
            def __init__(self):
                self.plugin_state = {}
                self._cells_at = 1.0
                self.notices = []

            def notice(self, *a, **kw):
                self.notices.append(a)

        client = _C()
        handler, _disp = _CMD_TABLE["plugin_cmd"]
        # 3-state 인자가 그 줄에서 이어 쳐 온 모양 그대로.
        await handler(srv, client, sess,
                      {"name": "claude-auto-redraw", "args": ["corruption"]})
        assert srv.claude_auto_redraw == "corruption", srv.claude_auto_redraw
        await handler(srv, client, sess,
                      {"name": "claude-auto-redraw", "args": ["off"]})
        assert srv.claude_auto_redraw == "off", srv.claude_auto_redraw
        # 코어 표가 주인인 액션도 같은 길로 닿는다(2026-08-03 오판의 회귀 가드).
        await handler(srv, client, sess,
                      {"name": "claude-token-account", "args": ["나"]})
    finally:
        await teardown(srv, task, sock)
