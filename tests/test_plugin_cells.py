"""플러그인 셀 기여(Tier B) — 서버가 **무엇을 어디에 쓸지**를 런으로 준다.

설계 = `docs/internal/PLUGIN_COMPAT_TEXTUAL_GUI_2026-08-01.md` §4.2 · §4.4 (P3).

# 왜 필요한가

시계·달력은 정본이 자기 프로세스에서 그린다(`client_overlay`). 네이티브 클라는
파이썬을 못 읽어 그 그림을 못 낸다 — 그래서 GUI 에는 **손으로 옮긴 두 번째 시계**가
있었다. 두 벌은 갈린다. 이 슬라이스는 그리는 규칙을 플러그인 한 벌로 되돌리고, 클라는
"여기 이 글자를 이 스타일로"만 받는다.

# 여기서 재는 것

1. **두 경로가 같은 그림을 낸다** — 정본 오버레이가 그린 격자와, 런을 얹은 격자가 같다.
   (이 저장소가 값을 본 「같은 입력, 두 경로」 오라클이다.)
2. 오버레이는 **그 클라의 것**이다 — 켠 클라에게만 간다.
3. **끄는 것도 프레임이다** — 빈 런이 한 번 나가야 클라가 지운다.
"""

import harness  # noqa: F401  (경로 설정)
from harness import server_only, teardown


def _clock():
    from pytmuxlib.plugins.clock import PLUGIN
    return PLUGIN


def _calendar():
    from pytmuxlib.plugins.calendar import PLUGIN
    return PLUGIN


def _bg(w, h):
    """**빈 격자로 재면 안 된다.** 시계는 패널을 덮되 숫자의 구멍으로 뒤가 비쳐
    보인다 — 배경이 공백이면 "구멍을 지웠다"와 "안 지웠다"가 똑같아 보여, 공백까지
    런에 담는 결함이 오라클을 그대로 통과한다(실측: 변이가 안 죽었다)."""
    from rich.style import Style
    st = Style()
    return [[("·", st) for _ in range(w)] for _ in range(h)]


def _text(cells):
    return ["".join(c[0] or " " for c in row) for row in cells]


async def test_the_runs_draw_the_same_clock_the_canonical_overlay_draws():
    """**같은 입력, 두 경로.** 정본이 cells 에 그린 것과, 런을 얹은 것이 같아야 한다.

    두 벌이 갈리면 같은 서버에 붙은 두 클라의 시계 자리가 달라지는데, 그건 나란히
    놓고 보기 전에는 아무도 모른다."""
    from datetime import datetime
    from pytmuxlib.plugins.clock.render import draw_clock_overlay

    W, H = 60, 20
    pane = {"id": 1, "x": 0, "y": 0, "w": W, "h": H}
    now = datetime(2026, 8, 2, 3, 4, 5)

    # ① 정본 경로 — cells 에 직접 그린다(딤은 빈 격자라 아무 일도 안 한다).
    canon = _bg(W, H)
    draw_clock_overlay(canon, [pane], {1}, W, H, None, now=now)

    # ② 런 경로 — 서버가 준 런을 그대로 얹는다.
    mine = _bg(W, H)
    req = {"panes": [pane], "overlays": {"clock": {1: {}}}, "cols": W, "rows": H}
    with harness.patched(__import__("datetime"), datetime=_FrozenDatetime(now)):
        runs = _clock().plugin_cells(None, None, req)
    for r in runs:
        for i, ch in enumerate(r["text"]):
            mine[r["y"]][r["x"] + i] = (ch, mine[r["y"]][r["x"] + i][1])

    assert _text(mine) == _text(canon), (
        "두 경로의 그림이 다르다\n런:\n" + "\n".join(_text(mine))
        + "\n정본:\n" + "\n".join(_text(canon)))


class _FrozenDatetime:
    """`datetime.now()` 만 고정한다 — 시계 테스트를 벽시계에 묶지 않으려고."""

    def __init__(self, when):
        self._when = when

    def now(self, tz=None):
        return self._when

    def __getattr__(self, name):
        import datetime as _dt
        return getattr(_dt.datetime, name)


async def test_a_small_pane_falls_back_to_plain_time_like_the_canonical_one():
    """큰 글자가 안 들어가면 단순 시각 — 판정이 두 경로에서 같아야 한다."""
    W, H = 12, 3
    pane = {"id": 1, "x": 0, "y": 0, "w": W, "h": H}
    req = {"panes": [pane], "overlays": {"clock": {1: {}}}, "cols": W, "rows": H}
    runs = _clock().plugin_cells(None, None, req)
    assert len(runs) == 1, runs
    assert len(runs[0]["text"]) == 8 and runs[0]["text"].count(":") == 2, runs


async def test_the_colour_is_a_name_not_a_hex():
    """색의 권위는 **클라 테마**다(설계 §10 위험표). 서버가 hex 를 실으면 서버가 UI 를
    알게 된다 — 그러면 테마를 바꿔도 시계만 옛 색으로 남는다."""
    pane = {"id": 1, "x": 0, "y": 0, "w": 60, "h": 20}
    runs = _clock().plugin_cells(
        None, None, {"panes": [pane], "overlays": {"clock": {1: {}}}})
    assert runs, "런이 없다"
    for r in runs:
        assert r["theme"] == {"f": "success"}, r
        assert "f" not in r["style"], f"서버가 색을 정해 버렸다: {r}"


async def test_nothing_is_produced_when_no_client_turned_it_on():
    """delete-to-disable 의 반쪽 — 아무도 안 켰으면 프레임 자체가 없다."""
    pane = {"id": 1, "x": 0, "y": 0, "w": 60, "h": 20}
    assert _clock().plugin_cells(
        None, None, {"panes": [pane], "overlays": {}}) == []
    assert _clock().plugin_dim_panes(
        None, None, {"panes": [pane], "overlays": {}}) == []


async def test_the_overlay_fact_belongs_to_the_connection_not_the_session():
    """켠 사실은 **그 클라의 것**이다 — 옆 사람 화면에 내 시계가 뜨면 안 되고,
    연결이 끊기면 함께 사라져야 한다(설계 §6 이 '비용'으로 적어 둔 그 상태)."""
    from pytmuxlib.servercmd import _CMD_TABLE
    assert "plugin_overlay" in _CMD_TABLE
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)

        class _C:
            def __init__(self):
                self.plugin_state = {}
                self._cells_at = 1.0

        a, b = _C(), _C()
        handler = _CMD_TABLE["plugin_overlay"][0]
        await handler(srv, a, sess, {"name": "clock", "pane": 1, "on": True})
        assert a.plugin_state["overlays"]["clock"] == {1: {}}
        assert b.plugin_state == {}, "한 클라의 오버레이가 다른 클라에 샜다"
        # 켠 직후에는 다음 틱을 안 기다린다(껐을 때도 마찬가지 — 빈 런이 지우개다).
        assert a._cells_at == 0.0, a._cells_at
        # 끄면 이름 자체가 사라진다(빈 집합이 남으면 "켜져 있음"으로 읽힌다).
        await handler(srv, a, sess, {"name": "clock", "pane": 1, "on": False})
        assert "clock" not in a.plugin_state["overlays"], a.plugin_state
    finally:
        await teardown(srv, task, sock)


async def test_the_server_stops_resending_the_same_picture():
    """시계는 1초에 한 번만 달라진다 — 같은 그림이 30Hz 로 흐르면 안 된다.
    그리고 **끈 뒤 한 번은** 나가야 한다(그 빈 프레임이 지우개다)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window

        class _C:
            def __init__(self):
                self.plugin_state = {"overlays": {"clock": {win.active_pane.id: {}}}}
                self._cells_at = 0.0
                self._cells_last = ()

        c = _C()
        first = srv._plugin_cells_frame(c, sess, win, 100.0)
        assert first is not None, "켰는데 아무것도 안 왔다"
        # 같은 초 안에서는 다시 안 만든다.
        assert srv._plugin_cells_frame(c, sess, win, 100.5) is None
        # 껐다 — 한 번은 나가야 클라가 지운다.
        c.plugin_state["overlays"] = {}
        erase = srv._plugin_cells_frame(c, sess, win, 102.0)
        assert erase is not None, "껐는데 지우개 프레임이 없다"
        # 그 뒤로는 조용하다.
        assert srv._plugin_cells_frame(c, sess, win, 104.0) is None
    finally:
        await teardown(srv, task, sock)


async def test_a_remote_view_does_not_get_a_local_clock_on_top_of_it():
    """원격 보기(§1.7) 중에는 화면이 **업스트림 것**이다 — 그 위에 이 서버의 시계를
    얹으면 남의 화면에 없는 것이 그려진다. 들고 있던 그림이 남지 않게 지우개는
    한 번 나가야 한다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window

        class _C:
            def __init__(self):
                self.plugin_state = {"overlays": {"clock": {win.active_pane.id: {}}}}
                self._cells_at = 0.0
                self._cells_last = ()
                self.remote_view = None

        c = _C()
        assert srv._plugin_cells_frame(c, sess, win, 100.0) is not None
        c.remote_view = object()          # 원격 보기로 들어갔다
        erase = srv._plugin_cells_frame(c, sess, win, 102.0)
        assert erase is not None, "원격 보기로 들어갔는데 시계가 그대로 남는다"
        assert srv._plugin_cells_frame(c, sess, win, 104.0) is None
        # 나오면 다음 틱에 다시 그려진다.
        c.remote_view = None
        assert srv._plugin_cells_frame(c, sess, win, 106.0) is not None
    finally:
        await teardown(srv, task, sock)


# ---------------------------------------------------------------------------
# 달력(2026-08-02) — 시계의 두 번째 시민이지만 **상태가 있다**(패널마다 몇 달 넘겨
# 보나). 그래서 여기서 더 재는 것: 상태가 연결에 매달리는가 · 클릭존/키가 뜻이 아니라
# **이름**으로 오가는가 · 정본과 서버가 정말 같은 규칙을 쓰는가.
# ---------------------------------------------------------------------------

def _cal_theme(name):
    return {"success": "green", "foreground": "white"}.get(name)


async def test_the_calendar_runs_draw_what_the_canonical_overlay_draws():
    """**같은 입력, 두 경로** — 달력판.

    시계는 두 벌(정본 `render` · 서버 런 생성기)을 오라클로 대조하는 데서 멈췄지만,
    달력은 정본도 런 생성기를 통해 그린다. 그래서 이 테스트가 재는 것은 "두 그림이
    같은가"가 아니라 **배선**이다 — `plugin_cells` 가 엉뚱한 오버레이 이름을 읽거나
    오프셋을 안 넘겨도 여기서 죽는다."""
    from datetime import datetime
    from pytmuxlib.plugins.calendar.render import draw_calendar_overlay

    W, H = 40, 16
    pane = {"id": 7, "x": 0, "y": 0, "w": W, "h": H}
    now = datetime(2026, 8, 2)

    canon = _bg(W, H)
    draw_calendar_overlay(canon, [pane], {7}, W, H, _cal_theme,
                          now=now, offsets={7: -1})

    mine = _bg(W, H)
    req = {"panes": [pane], "overlays": {"calendar": {7: {"offset": -1}}},
           "cols": W, "rows": H}
    with harness.patched(__import__("datetime"), datetime=_FrozenDatetime(now)):
        runs = _calendar().plugin_cells(None, None, req)
    for r in runs:
        for i, ch in enumerate(r["text"]):
            mine[r["y"]][r["x"] + i] = (ch, mine[r["y"]][r["x"] + i][1])

    assert "2026-07" in "".join(_text(mine)), "지난달을 안 그렸다"
    assert _text(mine) == _text(canon), (
        "두 경로의 그림이 다르다\n런:\n" + "\n".join(_text(mine))
        + "\n정본:\n" + "\n".join(_text(canon)))


async def test_the_calendar_colour_is_a_name_not_a_hex():
    """달력도 색을 안 정한다 — 이름만 싣고 각 클라가 자기 테마에서 푼다.

    유일한 리터럴은 '오늘'의 **글자색**(black)이다: 그 자리는 테마 강조색 바탕 위라
    이름으로 풀 것이 배경이다."""
    req = {"panes": [{"id": 1, "x": 0, "y": 0, "w": 40, "h": 16}],
           "overlays": {"calendar": {1: {}}}}
    runs = _calendar().plugin_cells(None, None, req)
    assert runs, "런이 없다"
    for r in runs:
        assert r.get("theme"), f"의미 색이 없다: {r}"
        for k, v in (r.get("style") or {}).items():
            assert k != "b", f"서버가 배경색을 정해 버렸다: {r}"
            if k == "f":
                assert v == "black" and r["theme"].get("b"), r


async def test_the_arrows_carry_a_name_the_client_cannot_read():
    """클릭존은 **뜻이 아니라 이름**을 싣는다(설계 §4.4: 행동은 서버가 정한다).

    자리는 실제로 그려진 `‹`/`›` 위여야 한다 — 화살표를 그려 놓고 클릭이 안 먹으면
    그 화살표가 거짓말이 된다."""
    pane = {"id": 3, "x": 0, "y": 0, "w": 40, "h": 16}
    req = {"panes": [pane], "overlays": {"calendar": {3: {}}}}
    runs = _calendar().plugin_cells(None, None, req)
    trig = _calendar().plugin_triggers(None, None, req)
    zones = trig["zones"]
    assert sorted(z["do"] for z in zones) == ["next", "prev"], zones
    for z in zones:
        assert z["pane"] == 3
        glyph = "‹" if z["do"] == "prev" else "›"
        hit = [r for r in runs
               if r["y"] == z["y"] and glyph in r["text"]
               and r["x"] <= z["x"] + z["w"] - 1
               and z["x"] <= r["x"] + len(r["text"]) - 1]
        assert hit, f"{glyph} 없는 자리에 클릭존: {z}"
    # 키도 같은 어휘다 — 정본이 쓰는 표 그대로 내려간다(두 경로가 갈리지 않게).
    from pytmuxlib.plugins.calendar import KEYS
    assert {(k["key"], k["do"]) for k in trig["keys"]} == set(KEYS)
    assert all(k["pane"] == 3 for k in trig["keys"])


async def test_a_small_calendar_advertises_no_arrows():
    """단순 날짜로 폴백한 패널엔 화살표가 없다 — 그러면 클릭존도 없어야 한다."""
    req = {"panes": [{"id": 1, "x": 0, "y": 0, "w": 12, "h": 3}],
           "overlays": {"calendar": {1: {}}}}
    assert _calendar().plugin_triggers(None, None, req)["zones"] == []
    assert _calendar().plugin_cells(None, None, req), "그림 자체는 있어야 한다"


async def test_the_action_name_moves_the_month_and_belongs_to_the_connection():
    """`do` 는 그 클라의 상태만 움직인다. 옆 사람 달력이 같이 넘어가면 안 된다."""
    from pytmuxlib.servercmd import _CMD_TABLE
    assert "plugin_overlay_action" in _CMD_TABLE
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)

        class _C:
            def __init__(self):
                self.plugin_state = {}
                self._cells_at = 1.0

        a, b = _C(), _C()
        on = _CMD_TABLE["plugin_overlay"][0]
        act = _CMD_TABLE["plugin_overlay_action"][0]
        await on(srv, a, sess, {"name": "calendar", "pane": 1, "on": True})
        await on(srv, b, sess, {"name": "calendar", "pane": 1, "on": True})
        for do, want in (("prev", -1), ("prev", -2), ("next", -1),
                         ("prev-year", -13), ("next-year", -1), ("today", 0)):
            await act(srv, a, sess, {"name": "calendar", "pane": 1, "do": do})
            assert a.plugin_state["overlays"]["calendar"][1]["offset"] == want, do
        assert b.plugin_state["overlays"]["calendar"][1] == {}, "옆 클라가 같이 넘어갔다"
        # 켤 때마다 이번 달에서 시작한다(껐다 켠 사람은 자기가 어디로 갔는지 모른다).
        await act(srv, a, sess, {"name": "calendar", "pane": 1, "do": "prev"})
        await on(srv, a, sess, {"name": "calendar", "pane": 1, "on": False})
        await on(srv, a, sess, {"name": "calendar", "pane": 1, "on": True})
        assert a.plugin_state["overlays"]["calendar"][1] == {}
        # 안 켜진 패널에 온 늦은 클릭은 조용히 버린다(터지지 않는다).
        await act(srv, a, sess, {"name": "calendar", "pane": 99, "do": "prev"})
        await act(srv, a, sess, {"name": "nosuch", "pane": 1, "do": "prev"})
    finally:
        await teardown(srv, task, sock)


async def test_moving_the_month_produces_a_new_frame_carrying_new_zones():
    """상태가 달라졌으면 프레임이 나가야 하고, 그 프레임에 **그 달의 클릭존**이 실려야
    한다.

    ⚠ 여기서 재지 **못하는** 것: "그림은 같은데 클릭존만 옮겨간" 경우. 달력에서는
    화살표가 제목 런과 같은 자리 셈에서 나와 그런 조합이 성립하지 않는다(판정 키에서
    zones 를 빼는 변이가 안 죽는 것으로 확인했다 — `serverio` 쪽 주석에 그 사실을
    적어 뒀다). 런 없이 클릭존만 내는 오버레이가 생기면 그때 이 축이 실재한다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        pid = win.active_pane.id

        class _C:
            def __init__(self):
                self.plugin_state = {"overlays": {"calendar": {pid: {}}}}
                self._cells_at = 0.0
                self._cells_last = ()

        c = _C()
        first = srv._plugin_cells_frame(c, sess, win, 100.0)
        assert first is not None
        before = c._cells_last
        c.plugin_state["overlays"]["calendar"][pid] = {"offset": -1}
        second = srv._plugin_cells_frame(c, sess, win, 102.0)
        assert second is not None, "달을 넘겼는데 프레임이 안 나갔다"
        assert c._cells_last != before
        import json
        body = json.loads(second[4:])      # 길이 프리픽스 4바이트 + JSON
        assert body["zones"], "클릭존이 프레임에 안 실렸다"
        assert {z["do"] for z in body["zones"]} == {"prev", "next"}
    finally:
        await teardown(srv, task, sock)
