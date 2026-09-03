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



def _paint(cells, runs, W, H):
    """런을 정본 소비자로 얹는다(테스트가 그리기 규칙을 다시 적지 않게)."""
    from pytmuxlib.clientrender import paint_runs
    paint_runs(cells, runs, W, H, lambda name: None)


def _text(cells):
    return ["".join(c[0] or " " for c in row) for row in cells]


async def test_the_runs_draw_the_same_clock_the_canonical_overlay_draws():
    """**같은 입력, 두 경로.** 정본이 cells 에 그린 것과, 런을 얹은 것이 같아야 한다.

    2026-08-02d 에 그리는 **규칙**은 한 벌이 됐다(`clock/cells.py`) — 그래서 이 오라클이
    이제 재는 것은 "두 규칙이 같은가"가 아니라 **정본 소비자가 런을 제자리에 얹는가**다
    (`render.py` 가 좌표를 흘리면 여기서 죽는다). 규칙 자체는 그림 골든이 지킨다
    (`test_plugin_clock_render.test_the_drawn_clock_is_pinned_to_a_golden`).
    달력도 같은 이유로 짝이 되는 테스트를 남겨 뒀다(아래)."""
    from datetime import datetime
    from pytmuxlib.plugins.clock.render import draw_clock_overlay

    W, H = 60, 20
    pane = {"id": 1, "x": 0, "y": 0, "w": W, "h": H}
    now = datetime(2026, 8, 2, 3, 4, 5)

    # ① 정본 경로 — cells 에 직접 그린다(딤은 빈 격자라 아무 일도 안 한다).
    canon = _bg(W, H)
    draw_clock_overlay(canon, [pane], {1}, W, H, lambda n: "green", now=now)

    # ② 런 경로 — 서버가 준 런을 그대로 얹는다.
    mine = _bg(W, H)
    req = {"panes": [pane], "overlays": {"clock": {1: {}}}, "cols": W, "rows": H}
    # ★ 시각은 **참조를 든 모듈**에서 얼린다. `cells.py` 가 `from datetime import
    #   datetime` 을 모듈 최상단에서 하므로, `datetime` **모듈의 속성**을 갈아 봐야
    #   이미 붙잡은 이름은 안 바뀐다 — 그렇게 얼리면 벽시계가 그대로 흘러 이 오라클이
    #   초 단위로 깜빡이는 플레이크가 된다(통합 직후 실측).
    from pytmuxlib.plugins.clock import cells as clock_cells_mod
    with harness.patched(clock_cells_mod, _datetime=_FrozenDatetime(now)):
        runs = _clock().plugin_cells(None, None, req)
    # ★ 런을 얹는 것은 **진짜 소비자**(`paint_runs`)에게 시킨다. 종전에는 여기서 손으로
    #   한 줄 루프를 돌렸는데, 그건 그리기 규칙의 **네 번째 사본**이었고 실제로 갈렸다 —
    #   와이드 문자를 한 칸으로 세다가 2026-08-02i 에 드러났다.
    _paint(mine, runs, W, H)

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


async def test_the_runs_follow_a_resize_without_waiting_for_the_period():
    """**창이 커지면 런도 그 프레임에 따라간다** — 주기를 기다리지 않는다(pytmux-164).

    런은 패널 사각형에서 나온다(입력기 배지의 x = 활성 패널 오른쪽 끝 - 글자 폭).
    그런데 캐시를 **시각만으로** 무르면, 리사이즈 뒤 최대 1초 동안 새 격자 위에
    **옛 폭으로 잰 배지**가 얹힌다 — 화면 나머지는 이미 새 크기라, 배지 하나만
    엉뚱한 자리에 떠 있다가 뛰는 것으로 보인다(Windows 이진 첫 화면 제보).

    시각·오버레이·사실은 이미 캐시를 무는데(`_cmd_plugin_overlay`·`_cmd_client_fact`
    가 `_cells_at = 0.0`) **기하만 빠져 있었다.** 리사이즈 자리마다 그 한 줄을 더
    적는 길도 있었지만(분할·닫기·줌·탭 전환·원격 크기…), 하나만 빠져도 같은 증상이
    조용히 돌아온다 — 그래서 재료 자체를 캐시 키에 넣는다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        # 클라의 창 크기 = 세션 공유 격자(_session_size). 리사이즈를 이 값으로 흉내낸다
        # (실제 경로도 `client.cols` 를 갈아 끼우는 것이 전부다 — serverio 의 `resize`).
        # (이 서버는 이 테스트의 것이라 인스턴스 속성으로 덮는다 — 프로덕션 전역을
        #  안 건드리므로 `harness.patched` 의 누수 가드가 볼 것이 없다.)
        size = [80, 24]
        srv._session_size = lambda s: tuple(size)

        class _C:
            def __init__(self):
                # 입력기 배지 = 오른쪽 끝에 붙는 유일한 런이라 자리 이동을 그대로 잰다.
                self.plugin_state = {"facts": {"ime": "EN"}}
                self._cells_at = 0.0
                self._cells_last = ()

        c = _C()
        assert srv._plugin_cells_frame(c, sess, win, 100.0) is not None
        narrow = [r["x"] for r in c._cells_runs]
        assert narrow, "배지가 아예 안 그려졌다 — 이 오라클이 잴 것이 없다"

        size[:] = [200, 24]                    # 창을 넓혔다(같은 초 안에)
        srv._plugin_cells_frame(c, sess, win, 100.2)
        wide = [r["x"] for r in c._cells_runs]
        assert wide != narrow, (
            f"리사이즈했는데 런이 옛 자리에 남았다: {narrow} → {wide}")
        # 새 폭의 **오른쪽 끝**에 붙었나 — "움직이긴 했다"로는 부족하다(절반만 따라가는
        # 고침도 위 단언을 통과한다). 경계는 서버가 지금 아는 패널 사각형에서 가져온다 —
        # 여기 숫자를 손으로 적으면 테두리 옵션 하나에 이 오라클이 거짓으로 운다.
        from pytmuxlib.cellwidth import char_cells
        right = {p.id: x + w
                 for p, (x, _y, w, _h) in srv._client_pane_rects(sess, win)}
        run = c._cells_runs[0]
        assert run["x"] + sum(char_cells(ch) for ch in run["text"]) \
            == right[win.active_pane.id], (run, right)
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
    # ★ 시계와 같은 이유로 **참조를 든 모듈**에서 언다(위 주석). 종전에는 `datetime`
    #   모듈의 속성을 갈아 **아무것도 안 얼고 있었고**, 그래서 이 테스트는 실제로는
    #   벽시계의 지난달을 그리고 있었다 — 아래 `"2026-07"` 단언은 **2026년 8월에만**
    #   맞는다(9월이 되면 적색). 얼려야 이 판이 날짜와 무관해진다.
    from pytmuxlib.plugins.calendar import cells as cal_cells_mod
    with harness.patched(cal_cells_mod, _datetime=_FrozenDatetime(now)):
        runs = _calendar().plugin_cells(None, None, req)
    # ★ 런을 얹는 것은 **진짜 소비자**(`paint_runs`)에게 시킨다. 종전에는 여기서 손으로
    #   한 줄 루프를 돌렸는데, 그건 그리기 규칙의 **네 번째 사본**이었고 실제로 갈렸다 —
    #   와이드 문자를 한 칸으로 세다가 2026-08-02i 에 드러났다.
    _paint(mine, runs, W, H)

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


# --------------------------------------------------------------------------- #
# claude-token-usage-view — Tier B 의 마지막 소비자(2026-08-02f)
# --------------------------------------------------------------------------- #

def _usage_view():
    import importlib
    return importlib.import_module(
        "pytmuxlib.plugins.claude-token-usage-view").PLUGIN


class _FakeServer:
    """셀 기여가 서버에서 읽는 것만 흉내낸다 — claude-code 가 긁어 둔 한도와 그 시각.

    **플러그인끼리 하드 참조는 금지**라 usage-view 는 이 둘을 `getattr` 로 부드럽게
    읽는다. 그 계약을 여기서 재려면 서버가 진짜일 필요가 없다(없을 때의 거동은 아래
    `..._without_claude_code_...` 가 따로 본다)."""

    def __init__(self, usage, ts=None):
        self._usage = usage
        if ts is not None:
            self._usage_ts = ts


_USAGE = {"session": {"pct": 41, "reset": "2pm"},
          "week_all": {"pct": 14, "reset": "Jun 13 at 3am"}}




async def test_the_usage_runs_draw_what_the_canonical_overlay_draws():
    """**같은 입력, 두 경로** — 한도 오버레이판.

    그리는 규칙은 `cells.py` 한 벌이므로, 이 오라클이 재는 것은 **정본 소비자가 런을
    제자리에 얹는가**다(시계·달력의 짝과 같은 뜻). `overlay.py` 가 좌표나 여백을
    흘리면 여기서 죽는다."""
    import importlib
    from datetime import datetime
    ov = importlib.import_module(
        "pytmuxlib.plugins.claude-token-usage-view.overlay")

    W, H = 64, 14
    pane = {"id": 1, "x": 0, "y": 0, "w": W, "h": H}
    now = datetime(2026, 6, 11, 10, 0, 0)

    canon = _bg(W, H)
    ov.draw_usage_overlay(canon, [pane], {1}, W, H, lambda n: "white",
                          _USAGE, age_sec=None, now=now)

    mine = _bg(W, H)
    req = {"panes": [pane],
           "overlays": {"claude-token-usage-view": {1: {}}},
           "cols": W, "rows": H}
    # 시각은 **참조를 든 모듈**에서 언다(시계 쪽 주석 참조 — 모듈 속성을 갈면 안 듣는다).
    cells_mod = importlib.import_module(
        "pytmuxlib.plugins.claude-token-usage-view.cells")
    with harness.patched(cells_mod, _datetime=_FrozenDatetime(now)):
        runs = _usage_view().plugin_cells(_FakeServer(_USAGE), None, req)
    # ★ 런을 얹는 것은 **진짜 소비자**(`paint_runs`)에게 시킨다. 종전에는 여기서 손으로
    #   한 줄 루프를 돌렸는데, 그건 그리기 규칙의 **네 번째 사본**이었고 실제로 갈렸다 —
    #   와이드 문자를 한 칸으로 세다가 2026-08-02i 에 드러났다.
    _paint(mine, runs, W, H)

    assert "41%" in "".join(_text(mine)), "한도 막대가 런에 안 실렸다"
    assert _text(mine) == _text(canon), (
        "두 경로의 그림이 다르다\n런:\n" + "\n".join(_text(mine))
        + "\n정본:\n" + "\n".join(_text(canon)))


async def test_the_usage_colour_is_a_name_not_a_hex():
    """서버는 색을 안 정한다 — 이름만 싣고 각 클라가 자기 테마에서 푼다.

    hex 를 실으면 서버가 UI 를 알게 되고(설계 §10 위험표), 사용자가 테마를 바꿔도
    이 오버레이만 옛 색으로 남는다."""
    pane = {"id": 1, "x": 0, "y": 0, "w": 64, "h": 14}
    runs = _usage_view().plugin_cells(
        _FakeServer(_USAGE), None,
        {"panes": [pane], "overlays": {"claude-token-usage-view": {1: {}}}})
    assert runs, "런이 하나도 없다"
    for r in runs:
        th = r.get("theme") or {}
        assert th, f"의미 색이 없다: {r}"
        for v in th.values():
            assert not v.startswith("#"), f"hex 가 실렸다: {r}"
        assert "f" not in (r.get("style") or {}), f"리터럴 색이 실렸다: {r}"


async def test_nothing_is_drawn_until_a_client_turns_the_usage_overlay_on():
    """켠 사실은 **클라만** 안다(설계 §4.4). 아무도 안 켰으면 런도 딤도 없다 —
    `plugin_cells` 가 빈 목록을 내야 서버가 프레임을 안 만든다(delete-to-disable 결)."""
    pane = {"id": 1, "x": 0, "y": 0, "w": 64, "h": 14}
    srv = _FakeServer(_USAGE)
    assert _usage_view().plugin_cells(
        srv, None, {"panes": [pane], "overlays": {}}) == []
    assert _usage_view().plugin_dim_panes(
        srv, None, {"panes": [pane], "overlays": {}}) == []
    # 켜면 그 패널이 딤 대상이 된다(뒤 화면을 흐리게 — 딤은 클라만 할 수 있다).
    on = {"panes": [pane], "overlays": {"claude-token-usage-view": {1: {}}}}
    assert _usage_view().plugin_dim_panes(srv, None, on) == [1]


async def test_the_usage_overlay_says_something_without_claude_code():
    """**빈 화면 금지.** claude-code 가 없거나 아직 안 긁었으면 서버에 한도가 없다 —
    그래도 안내 한 줄은 가야 사용자가 "고장"으로 읽지 않는다.

    그리고 **플러그인끼리 하드 참조 금지**를 여기서 잰다: 한도를 든 속성이 아예 없는
    서버(=claude-code 미설치)를 줘도 터지지 않아야 한다."""
    pane = {"id": 1, "x": 0, "y": 0, "w": 64, "h": 14}
    req = {"panes": [pane], "overlays": {"claude-token-usage-view": {1: {}}}}

    class _Bare:                     # claude-code 가 없는 서버(속성 자체가 없다)
        pass

    runs = _usage_view().plugin_cells(_Bare(), None, req)
    assert runs, "안내 한 줄도 없이 빈 화면이 됐다"
    assert any("없음" in r["text"] or "No limit" in r["text"] for r in runs), \
        [r["text"] for r in runs]


async def test_the_usage_freshness_comes_from_the_same_clock_the_status_uses():
    """묵은 값을 현재값으로 오독하지 않게 'N분 전 실측'을 붙인다(S6 T3).

    신선도의 출처는 정본 클라가 status 로 받는 것과 **같은 자리**(`_usage_ts`)라야
    한다 — 두 클라가 다른 신선도를 보면 한쪽이 거짓말을 한다."""
    import time
    pane = {"id": 1, "x": 0, "y": 0, "w": 64, "h": 14}
    req = {"panes": [pane], "overlays": {"claude-token-usage-view": {1: {}}}}
    fresh = _usage_view().plugin_cells(
        _FakeServer(_USAGE, ts=time.time()), None, req)
    stale = _usage_view().plugin_cells(
        _FakeServer(_USAGE, ts=time.time() - 3600), None, req)
    assert not any("전 실측" in r["text"] for r in fresh), "방금 잰 값에 stale 표기"
    assert any("전 실측" in r["text"] for r in stale), \
        "한 시간 묵었는데 표기가 없다: " + str([r["text"] for r in stale])


# --------------------------------------------------------------------------- #
# 런의 공통 소비자(`clientrender.paint_runs`) — 2026-08-02g 에 셋을 접은 자리
# --------------------------------------------------------------------------- #

async def test_the_client_theme_actually_colours_what_gets_drawn():
    """**의미 색이 실제로 칠해지는가.**

    이 자리는 오래 비어 있었다: `run_style` 이 `theme(...)` 을 통째로 무시하도록
    변이시켜도 오버레이 테스트 **52건이 전부 초록**이었다(2026-08-02g 실측). 골든과
    두 경로 대조가 **글자만** 보기 때문이다 — 색이 통째로 빠져도 그림은 같다.
    "값을 만드는 헬퍼만 재고 붙이는 호출은 안 재는" 이 저장소의 상습 실패 모드다.

    셋을 한 소비자로 접었으니 여기가 그 한 곳이다. 두 층을 다 잰다:
    ① 규칙(`run_style`)이 이름을 테마로 푸는가 ② 세 오버레이가 그 함수를 **실제로
    끼워 넣는가**(끼우는 줄을 지우면 ②가 죽는다)."""
    import importlib
    from pytmuxlib.clientrender import run_style
    from pytmuxlib.plugins.clock.render import draw_clock_overlay
    from pytmuxlib.plugins.calendar.render import draw_calendar_overlay
    ov = importlib.import_module(
        "pytmuxlib.plugins.claude-token-usage-view.overlay")

    SENTINEL = {"success": "#010203", "foreground": "#040506"}

    # ① 규칙: 의미 이름은 테마로 풀고, 이름이 없는 자리는 런의 리터럴을 쓴다.
    st = run_style({"text": "x", "style": {"bo": 1}, "theme": {"f": "success"}},
                   SENTINEL.get)
    assert st.color.name == "#010203", st
    assert st.bold, "축약 스타일 bo 가 떨어졌다"
    st2 = run_style({"text": "x", "style": {"f": "black"}, "theme": {"b": "success"}},
                    SENTINEL.get)
    assert st2.bgcolor.name == "#010203", st2
    assert st2.color.name == "black", "이름 없는 자리의 리터럴이 사라졌다"

    def drawn_colours(draw):
        """오버레이를 그린 뒤 **실제로 찍힌** 전경/배경 색 이름 집합."""
        W, H = 64, 16
        cells = _bg(W, H)
        draw(cells, [{"id": 1, "x": 0, "y": 0, "w": W, "h": H}], W, H)
        out = set()
        for row in cells:
            for ch, cst in row:
                if ch not in (" ", "", "·"):
                    if cst.color is not None:
                        out.add(cst.color.name)
                    if cst.bgcolor is not None:
                        out.add(cst.bgcolor.name)
        return out

    # ② 배선: 세 오버레이가 그 규칙을 실제로 통과시키는가.
    from datetime import datetime
    now = datetime(2026, 6, 11, 10, 0, 0)
    clock = drawn_colours(lambda c, p, W, H: draw_clock_overlay(
        c, p, {1}, W, H, SENTINEL.get, now=now))
    assert "#010203" in clock, f"시계 숫자가 테마 색을 안 썼다: {clock}"

    cal = drawn_colours(lambda c, p, W, H: draw_calendar_overlay(
        c, p, {1}, W, H, SENTINEL.get, now=now))
    assert "#010203" in cal, f"달력 강조가 테마 색을 안 썼다: {cal}"
    assert "#040506" in cal, f"달력 날짜가 테마 색을 안 썼다: {cal}"

    usage = drawn_colours(lambda c, p, W, H: ov.draw_usage_overlay(
        c, p, {1}, W, H, SENTINEL.get, _USAGE, now=now))
    assert "#010203" in usage, f"카운트다운이 테마 색을 안 썼다: {usage}"
    assert "#040506" in usage, f"한도 막대가 테마 색을 안 썼다: {usage}"


async def test_a_wide_character_in_a_run_keeps_the_row_aligned():
    """**와이드 문자는 두 칸이다** — 런에 한글이 들어오면 자리 셈이 달라진다.

    첫 소비자 셋(시계·달력·한도)이 전부 ASCII 라 `paint_runs` 는 글자마다 x 를 1 씩
    밀고 있었고, 아무도 안 걸렸다. 입력기 배지(`[한]`)가 오자 44칸 행이 **45** 로
    측정되며 드러났다(2026-08-02i · P7). "안 걸렸다"는 "맞았다"가 아니다.

    두 가지를 잰다: ① 글자가 제 칸에 놓이는가 ② 와이드 짝의 **연속셀**(`""`)이 생겨
    행 폭이 보존되는가."""
    from pytmuxlib.clientrender import paint_runs
    from pytmuxlib.clientutil import _char_cells

    W, H = 12, 1
    cells = _bg(W, H)
    paint_runs(cells, [{"x": 4, "y": 0, "text": "[한]", "style": {},
                        "theme": {"f": "success"}}], W, H, lambda n: "green")
    row = cells[0]
    # ① `[`=4 · `한`=5(본체)+6(연속셀) · `]`=7
    assert row[4][0] == "[" and row[5][0] == "한" and row[7][0] == "]", \
        [c[0] for c in row]
    assert row[6][0] == "", f"와이드 짝의 연속셀이 없다: {[c[0] for c in row]}"
    # ② 행 폭 보존 — 이 단언이 실측에서 45 != 44 로 울었던 그 셈이다.
    assert sum(_char_cells(c) for c, _ in row if c != "") == W, \
        [c[0] for c in row]
    # ③ 고아 연속셀 금지: `""` 의 왼쪽은 반드시 와이드 문자다.
    for x, (ch, _st) in enumerate(row):
        if ch == "":
            assert x > 0 and _char_cells(row[x - 1][0]) == 2, \
                f"고아 연속셀 @{x}: {[c[0] for c in row]}"


async def test_a_reported_fact_reaches_the_plugin_through_the_server_frame():
    """★ **배선을 잰다** — 손으로 만든 `req` 로는 안 지나는 자리다.

    P7(69218)이 정확히 여기서 물렸다: 플러그인은 `req["facts"]` 를 읽는데 서버가 그
    칸을 **안 채운 채** 나갔다. 그 슬라이스의 오라클이 전부 `plugin_cells(None, None,
    <손으로 만든 req>)` 를 불러 서버를 한 번도 안 지났고, 스위트는 초록이었다 —
    "값을 만드는 헬퍼만 재고 붙이는 호출은 안 잰다"의 교과서 사례다.

    그래서 여기서는 **서버가 만든 프레임**을 본다: 클라가 사실을 올리면 그 클라의 셀
    프레임에 배지가 실리고, 지우면 빠진다."""
    import json
    from pytmuxlib.servercmd import _CMD_TABLE

    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window

        class _C:
            def __init__(self):
                self.plugin_state = {}
                self._cells_at = 0.0
                self._cells_last = ()

        c = _C()
        # 아무도 안 올렸으면 프레임 자체가 없다(켠 적도 없다).
        assert srv._plugin_cells_frame(c, sess, win, 100.0) is None

        # 클라가 사실을 올린다 — 실제 명령 핸들러를 지난다.
        await _CMD_TABLE["client_fact"][0](srv, c, sess,
                                           {"name": "ime", "value": "한"})
        frame = srv._plugin_cells_frame(c, sess, win, 101.0)
        assert frame is not None, "사실을 올렸는데 프레임이 안 나갔다"
        body = json.loads(frame[4:])          # 길이 프리픽스 4바이트 + JSON
        texts = [r["text"] for r in body.get("runs") or []]
        assert "[한]" in texts, f"배지가 프레임에 없다: {body}"

        # 지우면 빠진다 — 빈 런이 지우개다.
        await _CMD_TABLE["client_fact"][0](srv, c, sess,
                                           {"name": "ime", "value": None})
        frame2 = srv._plugin_cells_frame(c, sess, win, 102.0)
        assert frame2 is not None, "지웠는데 지우개 프레임이 안 나갔다"
        body2 = json.loads(frame2[4:])
        assert not (body2.get("runs") or []), f"지웠는데 배지가 남았다: {body2}"
    finally:
        await teardown(srv, task, sock)


# ---------------------------------------------------------------------------
# Claude 패널 **안**의 클릭존(pytmux-2 · pytmux-23) — 오버레이가 아니라 **패널 내용**
# 에서 나오는 첫 자리다. 그래서 여기서 더 재는 것: 규칙이 정말 한 벌인가 · 오버레이를
# 하나도 안 켠 클라도 자리를 받는가 · 문구를 못 찾으면 조용히 안 만드는가.
# ---------------------------------------------------------------------------

def _footerzones():
    """claude-code 의 `footerzones` 모듈(이름에 하이픈이 있어 import 문으로는 못 쓴다)."""
    import importlib
    return importlib.import_module(
        "pytmuxlib.plugins.claude-code.footerzones")


_FOOTER = [
    "  ⏵⏵ auto mode on (shift+tab to cycle)",
    "  new task? /clear to save 386.8k tokens",
]


async def test_the_rule_that_finds_the_footer_is_one_copy():
    """정본이 부르는 함수와 서버가 부르는 함수가 **같은 것**이어야 한다.

    두 벌이 되면 그 순간 두 클라의 누르는 자리가 갈린다 — 그리고 그 갈림은 화면에
    아무 표시도 안 남기므로(둘 다 그럴듯한 자리를 보인다) 사람이 못 잡는다."""
    import importlib
    fz = _footerzones()
    render = importlib.import_module(
        "pytmuxlib.plugins.claude-code.clientrender")
    plugin = importlib.import_module("pytmuxlib.plugins.claude-code")
    assert render.scan_pane is fz.scan_pane, \
        "정본 렌더가 규칙을 따로 들고 있다"
    # 서버 쪽도 같은 이름을 지연 import 한다 — 소스로 못박는다(호출부까지 단언).
    import inspect
    src = inspect.getsource(plugin._ClaudeCodePlugin.plugin_triggers)
    assert "from .footerzones import" in src, \
        f"서버가 규칙을 다른 데서 가져온다:\n{src}"


async def test_the_scanner_covers_only_the_words_it_actually_found():
    """존은 **문구만** 덮는다 — 줄 전체를 덮으면 힌트를 눌러도 팝업이 뜬다(07-15)."""
    fz = _footerzones()
    found = fz.scan_pane(_FOOTER, 10, 5, 60, 24)
    assert set(found) == {"perm", "tokens"}, found
    px0, px1, py = found["perm"]
    assert (px0, py) == (12, 5), found              # 들여쓰기 둘 만큼 밀려 있다
    assert px1 == px0 + len("⏵⏵ auto mode on"), found
    # 힌트 "(shift+tab to cycle)" 는 존 밖이다.
    assert px1 < 10 + len(_FOOTER[0].rstrip()), found
    tx0, tx1, ty = found["tokens"]
    assert ty == 6 and tx1 - tx0 == len("386.8k tokens"), found


async def test_a_token_count_in_the_transcript_is_not_a_click_target():
    """`386.8k tokens` 는 대화 본문에도 얼마든 지나간다 — footer 서명(`/clear`)이
    같은 줄에 없으면 **존을 안 만든다**. 오탐은 아무 일도 안 나는 것보다 나쁘다."""
    fz = _footerzones()
    assert fz.scan_pane(["  we burned 386.8k tokens on that"], 0, 0, 60, 24) == {}
    assert fz.scan_pane(["  /clear the queue please"], 0, 0, 60, 24) == {}


async def test_wide_characters_before_the_footer_do_not_shift_the_zone():
    """한글이 앞에 있으면 글자 수와 칸 수가 갈린다 — 칸으로 세야 자리가 맞는다."""
    fz = _footerzones()
    found = fz.scan_pane(["한글 ⏵⏵ auto mode on"], 0, 0, 60, 24)
    x0, _x1, _y = found["perm"]
    assert x0 == 5, found          # "한글 " = 2+2+1 칸


async def test_a_client_with_no_overlay_still_gets_the_claude_zones():
    """★ **배선을 잰다**(손으로 만든 req 로는 안 지나는 자리다).

    종전 셀 프레임은 *"오버레이를 켠 적도 없으면 아무것도 안 만든다"* 로 시작했다 —
    그 전제 그대로면 시계를 안 켠 사람에게는 Claude 클릭존이 **영영 안 간다**. 그리고
    그 사람이 대다수다."""
    import json

    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p._claude = "idle"
        p.feed(("\x1b[2J\x1b[H" + "\r\n".join(_FOOTER) + "\r\n").encode("utf-8"))

        class _C:
            def __init__(self):
                self.plugin_state = {}       # 아무 오버레이도 안 켰다
                self._cells_at = 0.0
                self._cells_last = ()

        c = _C()
        frame = srv._plugin_cells_frame(c, sess, win, 100.0)
        assert frame is not None, "오버레이를 안 켰다고 클릭존까지 안 왔다"
        body = json.loads(frame[4:])
        zones = {z["do"]: z for z in body.get("zones") or []}
        assert set(zones) == {"perm", "tokens"}, body
        assert zones["perm"]["opens"] == "claude-perm-mode", zones
        assert zones["tokens"]["opens"] == "claude-token-log", zones
        assert zones["perm"]["pane"] == p.id, zones
        assert zones["perm"]["w"] == len("⏵⏵ auto mode on"), zones
        # Claude 가 아니게 되면 자리도 사라진다(그 자리는 이제 트랜스크립트다).
        p._claude = None
        erase = srv._plugin_cells_frame(c, sess, win, 101.0)
        assert erase is not None, "Claude 가 아닌데 자리가 그대로 남는다"
        assert not (json.loads(erase[4:]).get("zones") or []), erase
    finally:
        await teardown(srv, task, sock)


async def test_the_zones_do_not_wait_for_the_one_second_tick():
    """클릭존은 패널 **내용**에서 나온다 — 내용이 움직이면 자리도 움직인다. 런처럼
    1초를 기다리면 그 사이 사용자가 누른 곳이 **낡은 자리**다(시계는 초 단위라
    기다려도 되지만 이건 아니다)."""
    import json

    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p._claude = "idle"
        p.feed(("\x1b[2J\x1b[H" + "\r\n".join(_FOOTER) + "\r\n").encode("utf-8"))

        class _C:
            def __init__(self):
                self.plugin_state = {}
                self._cells_at = 0.0
                self._cells_last = ()

        c = _C()
        first = srv._plugin_cells_frame(c, sess, win, 100.0)
        assert first is not None
        was = {z["do"]: z["y"] for z in json.loads(first[4:])["zones"]}
        # 같은 초 안에서 footer 가 한 줄 밀렸다.
        p.feed(("\x1b[2J\x1b[H\r\n" + "\r\n".join(_FOOTER) + "\r\n").encode("utf-8"))
        frame = srv._plugin_cells_frame(c, sess, win, 100.1)
        assert frame is not None, "footer 가 움직였는데 다음 초까지 낡은 자리를 준다"
        now = {z["do"]: z["y"] for z in json.loads(frame[4:])["zones"]}
        assert now["perm"] == was["perm"] + 1, (was, now)
    finally:
        await teardown(srv, task, sock)


async def test_a_remote_view_does_not_get_the_local_claude_zones():
    """원격 보기 중에는 보이는 글이 **업스트림 것**이다 — 내 패널에서 잰 자리를 얹으면
    엉뚱한 데를 누른다(시계와 같은 이유)."""
    import json

    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p._claude = "idle"
        p.feed(("\x1b[2J\x1b[H" + "\r\n".join(_FOOTER) + "\r\n").encode("utf-8"))

        class _C:
            def __init__(self):
                self.plugin_state = {}
                self._cells_at = 0.0
                self._cells_last = ()
                self.remote_view = None

        c = _C()
        assert srv._plugin_cells_frame(c, sess, win, 100.0) is not None
        c.remote_view = object()
        erase = srv._plugin_cells_frame(c, sess, win, 101.0)
        assert erase is not None, "원격 보기로 들어갔는데 내 자리가 그대로 남는다"
        assert not (json.loads(erase[4:]).get("zones") or []), erase
    finally:
        await teardown(srv, task, sock)


# ---------------------------------------------------------------------------
# 넷 중 나머지 둘(`remote`·`interrupt`) — pytmux-2 잔여. 이 둘은 화면이 아니라
# 동작이라 길이 갈렸고, 그래서 오래 GUI 에 안 갔다. 여기서 재는 것: 두 길이 제대로
# 갈리는가 · 어느 것도 **조용히 사라지는 길**로 안 가는가 · 겹칠 때 차례가 맞는가.
# ---------------------------------------------------------------------------

_FOOTER4 = [
    "  ⏵⏵ auto mode on (shift+tab to cycle)",
    "  ✳ Thinking… (esc to interrupt)",
    "  Remote Control active",
    "  new task? /clear to save 386.8k tokens",
]


async def test_every_zone_the_rule_finds_has_a_way_back_to_the_server():
    """★ **넷이 다 나간다** — 그리고 넷이 다 갈 길이 있다.

    종전에는 규칙이 넷을 찾아 놓고 서버가 둘만 실었다(나머지 둘은 배선이 없어 실으면
    "누를 수는 있는데 아무 일도 안 나는 칸"이 됐다). 이제 길이 둘이라 넷이 다 나가고,
    **자리마다 정확히 한 길**이어야 한다 — 둘 다 비면 죽은 칸이고, 둘 다 차면 클라가
    어느 길로 갈지 우리가 모른다."""
    import json

    fz = _footerzones()
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p._claude = "idle"
        p.feed(("\x1b[2J\x1b[H" + "\r\n".join(_FOOTER4) + "\r\n").encode("utf-8"))

        class _C:
            def __init__(self):
                self.plugin_state = {}
                self._cells_at = 0.0
                self._cells_last = ()

        body = json.loads(srv._plugin_cells_frame(_C(), sess, win, 100.0)[4:])
        zones = {z["do"]: z for z in body.get("zones") or []}
        assert set(zones) == {"perm", "interrupt", "remote", "tokens"}, body
        # 화면을 여는 셋 — 이름은 규칙 쪽 표 그대로다.
        assert zones["perm"]["opens"] == "claude-perm-mode", zones
        assert zones["remote"]["opens"] == "claude-remote-control", zones
        assert zones["tokens"]["opens"] == "claude-token-log", zones
        # 치는 하나 — 칠 것까지 실려야 한다(안 실으면 클라가 빈 바이트를 친다).
        assert zones["interrupt"]["send"] == "\x1b", zones
        # ★ 자리마다 **정확히 한 길**이다.
        for kind, z in zones.items():
            assert bool(z["opens"]) != bool(z["send"]), (kind, z)
        # 자리는 문구만 덮는다 — 'esc to interrupt' 는 줄 전체가 아니다.
        assert zones["interrupt"]["w"] == len("esc to interrupt"), zones
    finally:
        await teardown(srv, task, sock)


async def test_the_order_the_server_ships_them_in_is_the_priority():
    """겹칠 때 무엇이 이기는가는 **차례**로 나른다.

    클라는 자리 목록에서 먼저 맞는 것을 집는다(파이썬은 `PRIORITY` 로 훑고, Rust 는
    벡터 순서로 `find` 한다). 그러니 서버가 싣는 차례가 곧 두 클라의 우선순위이고,
    그 차례가 `interrupt` 먼저가 아니면 **좁은 창에서 인터럽트를 영영 못 누른다**
    (폭이 잘리면 perm 이 줄 전체로 넓어져 그 자리를 덮는다)."""
    import json

    fz = _footerzones()
    assert fz.PRIORITY[0] == "interrupt", fz.PRIORITY
    assert set(fz.PRIORITY) == set(fz.OPENS) | set(fz.SENDS), \
        "규칙이 찾는 종류와 우선순위 표가 갈렸다"

    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p._claude = "idle"
        p.feed(("\x1b[2J\x1b[H" + "\r\n".join(_FOOTER4) + "\r\n").encode("utf-8"))

        class _C:
            def __init__(self):
                self.plugin_state = {}
                self._cells_at = 0.0
                self._cells_last = ()

        body = json.loads(srv._plugin_cells_frame(_C(), sess, win, 100.0)[4:])
        got = [z["do"] for z in body["zones"]]
        assert got == [k for k in fz.PRIORITY if k in got], (got, fz.PRIORITY)
    finally:
        await teardown(srv, task, sock)


async def test_the_bytes_the_interrupt_zone_carries_are_the_ones_canon_types():
    """정본이 치는 것과 자리에 실리는 것이 **같은 표**에서 와야 한다.

    갈리면 증상이 조용하다 — 두 클라가 같은 자리를 눌러 서로 다른 것을 친다. 그래서
    값을 두 번 적었나를 **소스로** 본다(호출부까지 단언 — 값을 만드는 표만 재면 그
    표를 안 읽는 호출부가 통과한다)."""
    import importlib

    fz = _footerzones()
    plugin = importlib.import_module("pytmuxlib.plugins.claude-code")
    # ⚠ 소스 문자열이 아니라 **상수 표**를 본다. 소스로 보면 독스트링의 `\\x1b` 가
    #   걸려 "다시 적었다"로 오진한다(실제로 그렇게 한 번 틀렸다).
    raw = plugin._interrupt_pane.__code__.co_consts
    # `from .footerzones import SENDS` 의 이름은 **튜플 상수**(fromlist)로 들어온다.
    consts = [x for c in raw for x in (c if isinstance(c, tuple) else (c,))]
    assert "SENDS" in consts, f"정본이 칠 것을 따로 들고 있다: {consts}"
    for c in consts:
        if isinstance(c, (str, bytes)) and (b"\x1b" if isinstance(c, bytes)
                                            else "\x1b") in c:
            raise AssertionError(f"정본이 ESC 를 다시 적었다: {consts}")

    sent = []

    class _App:
        def send_input_pane(self, pid, data):
            sent.append((pid, data))

    plugin._interrupt_pane(_App(), 7)
    assert sent == [(7, fz.SENDS["interrupt"].encode("utf-8"))], sent


def _body(frame):
    """프레임 바이트에서 메시지 본문을 되꺼낸다(길이 프리픽스 + JSON)."""
    import json
    assert frame is not None, "프레임이 없다"
    return json.loads(frame[4:].decode("utf-8"))


# ── Tier D 탈출구 — 「이 오버레이는 내가 그린다」(pytmux-458·459) ─────────────
#
# ⛔ 이 장치의 **대조군이 알맹이**다. 표현만 클라가 가져가는 것이라, 광고 안 한 클라
#    (= 정본)의 프레임 바이트는 종전과 **같아야** 한다. 안 그러면 이 전환은
#    「GUI 를 고쳤다」가 아니라 「프로토콜을 흔들었다」다.


class _NativeClient:
    """네이티브 오버레이를 광고한 클라."""

    caps = ("native_overlay",)

    def __init__(self, pane):
        self.plugin_state = {"overlays": {"clock": {pane: {}}}}
        self._cells_at = 0.0
        self._cells_last = ()
        self._cells_native = {}


class _PlainClient(_NativeClient):
    """광고 안 한 클라 — 정본이 이쪽이다."""

    caps = ()


async def test_the_client_that_advertises_gets_state_instead_of_runs():
    """광고한 클라에는 **런 대신 상태**가 온다(설계 §4.3 · pytmux-458)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        pane = win.active_pane.id

        frame = srv._plugin_cells_frame(_NativeClient(pane), sess, win, 100.0)
        assert frame is not None, "켰는데 아무것도 안 왔다"
        body = _body(frame)
        native = body.get("native")
        assert native, f"상태가 안 실렸다: {body}"
        assert set(native) == {"clock"}, native
        # ⚠ JSON 을 지나면 패널 id 는 **문자열 키**가 된다 — 클라가 보는 그대로다.
        assert str(pane) in native["clock"], native
        assert len(native["clock"][str(pane)]["time"]) == 8, native   # HH:MM:SS
        # ⛔ 런은 **안 온다** — 오면 벡터 시계 위에 격자 글자가 겹친다.
        assert body["runs"] == [], f"네이티브인데 런도 함께 왔다: {body['runs']}"
        # 딤은 **여전히 서버 것**이다(표현만 클라가 가져간다 · 설계 §4.1).
        assert body["dim"] == [pane], body["dim"]
    finally:
        await teardown(srv, task, sock)


async def test_a_client_that_does_not_advertise_sees_exactly_what_it_saw_before():
    """대조군 — 광고 안 한 클라의 프레임에는 `native` 칸이 **아예 없다**.

    「빈 칸이라도 붙이면 되지 않나」가 아니다: 그 한 칸이 정본 스위트의 와이어 골든과
    Rust 픽스처를 함께 흔든다. 이 장치의 계약은 **바이트가 같다**이다.
    """
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        pane = win.active_pane.id

        frame = srv._plugin_cells_frame(_PlainClient(pane), sess, win, 100.0)
        assert frame is not None, "켰는데 아무것도 안 왔다"
        body = _body(frame)
        assert "native" not in body, f"광고 안 한 클라에 새 칸이 붙었다: {sorted(body)}"
        # 그리고 종전대로 **런**을 받는다 — 그것이 정본이 그리는 그림이다.
        assert body["runs"], "격자 글자 시계가 사라졌다"
        assert body["dim"] == [pane], body["dim"]
    finally:
        await teardown(srv, task, sock)


async def test_the_clock_hand_moves_for_the_native_client_too():
    """초가 넘어가면 **네이티브 클라도** 새 프레임을 받는다.

    시계는 런이 없으므로 종전 판정(런·딤·클릭존 비교)만으로는 「아무것도 안 바뀌었다」가
    되어 그 클라의 시계가 **멎는다**. 상태를 판정 열쇠에 넣은 것이 그 자리다.
    """
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        pane = win.active_pane.id
        c = _NativeClient(pane)

        from pytmuxlib.plugins.clock import cells as clock_cells_mod
        ticks = iter(["01:02:03", "01:02:04"])
        real = clock_cells_mod.clock_time
        clock_cells_mod.clock_time = lambda now=None: next(ticks)
        try:
            first = srv._plugin_cells_frame(c, sess, win, 100.0)
            assert first is not None, "첫 프레임이 없다"
            second = srv._plugin_cells_frame(c, sess, win, 102.0)
            assert second is not None, (
                "초가 넘어갔는데 프레임이 안 나갔다 — 그 클라의 시계가 멎는다")
        finally:
            clock_cells_mod.clock_time = real
        # 같은 시각이면 다시 안 보낸다(같은 그림을 30Hz 로 흘리지 않는다).
        clock_cells_mod.clock_time = lambda now=None: "01:02:04"
        try:
            assert srv._plugin_cells_frame(c, sess, win, 104.0) is None, (
                "같은 시각인데 프레임이 또 나갔다")
        finally:
            clock_cells_mod.clock_time = real
    finally:
        await teardown(srv, task, sock)
