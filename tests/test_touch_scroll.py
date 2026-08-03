"""탭(터치)으로 쓰는 스크롤 UI — `touch-scroll`(제보/진단 2026-07-31).

제보: "패널을 위로 스크롤하면 패널이 아니라 pytmux 전체가 스크롤돼 실행 이전 화면이
보인다"(iPhone Blink → ssh → MSYS). 진단(`set mouse-debug on` 로그)이 원인을 갈랐다 —
**클릭은 SGR 로 정상 도달**(`down x=28 y=10 b=1` + `\\x1b[<0;28;10M`)하는데 `wheel` 은
0건이고, `alt-scroll off`(1007 복원) 후에도 화살표 변환조차 없었다. 즉 터미널이 스와이프를
자기 스크롤백 UI로 소비한다(hterm 은 alt-screen 에서도 이전 스크롤백을 노출해 "pytmux 실행
이전"까지 올라간다). pytmux 가 휠을 받아올 방법은 원천적으로 없으므로, **도달하는 유일한
입력인 탭**으로 스크롤백을 조작하는 경로를 만든다.

세 축을 각각 겨눈다:
  ① 순수 계산: 스크롤바 글자열·히트테스트·점프 델타(`clientrender.scrollbar_*`).
  ② 배선(호출부): 서버가 `scr`(현재 스크롤 행수)을 프레임에 싣고 → 클라 `pane_scroll`
     이 채워지고 → `_composite` 이 스크롤 모드에서 스크롤바를 **그리고 클릭존을 남긴다**.
     값 생성 함수만 테스트하면 붙이는 줄을 지워도 통과하므로 호출부까지 단언한다.
  ③ 켜고 끄기: `set touch-scroll off` 면 배지·스크롤바·클릭존이 모두 사라진다.
"""
import json

import harness  # noqa: F401 (경로 설정)
from harness import server_only, teardown, wait_until
from pytmuxlib import clientrender as cr


# ── ① 순수 계산 ──────────────────────────────────────────────────────────────
async def test_scrollbar_chars_marks_position_at_both_ends():
    """썸은 라이브면 맨 아래, 맨 위로 올라가면 맨 위에 놓인다(위치 = 방향 감각)."""
    live = cr.scrollbar_chars(10, 90, 0)        # 스크롤백 90행, 라이브
    assert live[0] == cr.SCROLLBAR_UP and live[-1] == cr.SCROLLBAR_DOWN
    assert live[-2] == cr.SCROLLBAR_THUMB, live    # 트랙 맨 아래가 썸
    assert live[1] == cr.SCROLLBAR_TRACK, live
    top = cr.scrollbar_chars(10, 0, 90)         # 끝까지 올라감(top=0)
    assert top[1] == cr.SCROLLBAR_THUMB and top[-2] == cr.SCROLLBAR_TRACK, top
    mid = cr.scrollbar_chars(10, 45, 45)        # 중간
    assert cr.SCROLLBAR_THUMB in mid[3:-3], mid


async def test_scrollbar_chars_full_thumb_without_history_and_hidden_when_tiny():
    """스크롤백이 없으면 썸이 트랙 전체(움직일 곳 없음) · 3행 미만이면 미표시."""
    assert cr.scrollbar_chars(6, 0, 0)[1:-1] == [cr.SCROLLBAR_THUMB] * 4
    assert cr.scrollbar_chars(2, 0, 50) == []
    assert cr.scrollbar_chars(0, 0, 50) == []


async def test_scrollbar_hit_maps_rows_to_actions():
    """첫/끝 칸은 페이지 이동, 사이는 0.0(맨 위)~1.0(맨 아래) 점프 비율."""
    assert cr.scrollbar_hit(10, 0) == ("up", None)
    assert cr.scrollbar_hit(10, 9) == ("down", None)
    assert cr.scrollbar_hit(10, 1) == ("jump", 0.0)
    assert cr.scrollbar_hit(10, 8) == ("jump", 1.0)
    assert cr.scrollbar_hit(10, -1) is None and cr.scrollbar_hit(10, 10) is None
    assert cr.scrollbar_hit(2, 0) is None            # 미표시 높이


async def test_scrollbar_jump_delta_moves_to_the_tapped_position():
    """점프는 **현재 위치와의 차**다 — 절대 위치 명령 없이 기존 scroll 델타로 옮긴다."""
    # 라이브(scroll 0) + 스크롤백 90 → 맨 위 탭 = +90, 맨 아래 탭 = 0(제자리)
    assert cr.scrollbar_jump_delta(10, 90, 0, 0.0) == 90
    assert cr.scrollbar_jump_delta(10, 90, 0, 1.0) == 0
    # 45 올라가 있음 → 맨 아래 탭 = -45(라이브 복귀), 중간 탭 = 제자리
    assert cr.scrollbar_jump_delta(10, 45, 45, 1.0) == -45
    assert cr.scrollbar_jump_delta(10, 45, 45, 0.5) == 0
    # 스크롤백이 없으면 어디를 탭해도 0(무동작)
    assert cr.scrollbar_jump_delta(10, 0, 0, 0.0) == 0


# ── ② 배선: 서버 → 클라 ─────────────────────────────────────────────────────
async def test_screen_frames_carry_scroll_only_when_scrolled():
    """`scr` 는 **올라가 있을 때만** 실린다(라이브 프레임은 종전과 바이트 동일).

    호출부 오라클: `_screen_frame` 과 `_send_full` 둘 다 패널의 현재 scroll 을 넘겨야
    한다 — 한쪽만 배선하면 attach 직후(full)나 스트리밍 중(delta) 한쪽에서 썸이 죽는다.
    """
    from pytmuxlib.model import ClientConn
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(40, 6)
        p = sess.active_window.active_pane
        c = ClientConn(None)

        def decode(frame):
            return json.loads(frame[4:].decode("utf-8"))

        p.feed("".join(f"row{i}\r\n" for i in range(30)).encode())
        rows, cur = p.render(True)
        live = decode(srv._screen_frame(c, p.id, rows, cur, p._last_wrap,
                                        p._last_top, p.scroll))
        assert "scr" not in live, live          # 라이브 = 필드 없음(대역폭/골든 불변)
        p.scroll_by(5)
        rows2, cur2 = p.render(True)
        up = decode(srv._screen_frame(c, p.id, rows2, cur2, p._last_wrap,
                                      p._last_top, p.scroll))
        assert up.get("scr") == 5, up
        # full 경로(_send_full)도 같은 값을 실어야 한다 — 실제 송신을 가로채 확인.
        sent = []

        class _C:
            def __init__(self):
                import asyncio
                self.session, self.writer = sess, self
                self.write_lock = asyncio.Lock()
                self._sent_rows, self.remote_view, self.lang = {}, None, "ko"
                self.caps = ()

            def write(self, data):
                sent.append(data)

            async def drain(self):
                pass

        await srv._send_full(_C())
        screens = [m for m in (json.loads(b[4:].decode("utf-8")) for b in sent)
                   if m.get("t") == "screen" and m.get("pane") == p.id]
        assert screens and screens[0].get("scr") == 5, screens[:1]
    finally:
        await teardown(srv, task, sock)


async def test_client_tracks_pane_scroll_end_to_end():
    """종단 배선: 클라가 스크롤을 보내면 **서버 프레임을 통해** pane_scroll 이 찬다.

    (스크롤 → `_handle_scroll` → flush → `_screen_frame(..., p.scroll)` → 클라 파싱)
    이 경로 어느 한 곳이 끊기면 썸이 항상 맨 아래에 붙어 조용히 쓸모없어진다.
    """
    from test_client import _with_app

    async def body(app, pilot, srv):
        sess = await harness.first_session(srv)
        p = sess.active_window.active_pane
        p.feed("".join(f"row{i}\r\n" for i in range(60)).encode())
        pid = p.id
        app.send_scroll(pid, delta=7)
        ok = await wait_until(pilot, lambda: app.pane_scroll.get(pid) == 7)
        assert ok, app.pane_scroll
        assert app.pane_top.get(pid) is not None
        app.send_scroll(pid, bottom=True)
        ok = await wait_until(pilot, lambda: app.pane_scroll.get(pid) == 0)
        assert ok, "맨 아래로 돌아오면 0 이어야(필드 부재를 0 으로 읽어야)"
    await _with_app(body)


# ── ② 배선: 그리기·클릭존 ────────────────────────────────────────────────────
def _one_pane(app, pid=7, x=2, y=1, w=10, h=6):
    app.layout = {"panes": [{"id": pid, "x": x, "y": y, "w": w, "h": h,
                             "box": [x - 1, y - 1, w + 2, h + 2],
                             "mouse": 0, "mouse_sgr": False, "active": True}],
                  "dividers": [], "active": pid, "cols": 40, "rows": 12}
    app.pane_content = {pid: ([[("." * w, {})] for _ in range(h)], None)}
    app.pane_top = {pid: 90}
    app.pane_scroll = {pid: 0}
    return pid


class _Tap:
    def __init__(self, x, y, button=1):
        self.x, self.y, self.button = x, y, button
        self.ctrl = self.shift = False
        self.stopped = False

    def stop(self):
        self.stopped = True


async def test_scrollbar_drawn_and_clickable_in_scroll_mode_only():
    """호출부 오라클: 스크롤 모드에서만 패널 오른쪽 끝 열에 스크롤바가 **그려지고**
    클릭존이 남는다. 라이브 화면(normal)은 종전 그대로(콘텐츠 침범 없음)."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        pid = _one_pane(app)
        app.mode = "normal"
        app._composite()
        assert app._touch_scroll_zone is None, "normal 모드에선 안 그린다"
        assert app.view._cells[1][11][0] == ".", app.view._cells[1][11]
        app.mode = "scroll"
        app._composite()
        assert app._touch_scroll_zone == (pid, 11, 1, 6), app._touch_scroll_zone
        col = [app.view._cells[1 + i][11][0] for i in range(6)]
        assert col[0] == cr.SCROLLBAR_UP and col[-1] == cr.SCROLLBAR_DOWN, col
        assert cr.SCROLLBAR_THUMB in col, col
    await _with_app(body)


async def test_tap_on_scrollbar_scrolls_the_pane():
    """▲/▼ 탭 = 반 화면 위/아래 · 트랙 탭 = 그 위치로 점프(현재 위치와의 차)."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        pid = _one_pane(app)
        app.mode = "scroll"
        app._composite()
        sent = []
        app.send_scroll = lambda p, **kw: sent.append((p, kw))
        v = app.view
        v.on_mouse_down(_Tap(11, 1))              # ▲
        assert sent[-1] == (pid, {"delta": 3}), sent
        v.on_mouse_down(_Tap(11, 6))              # ▼(맨 아래 칸)
        assert sent[-1] == (pid, {"delta": -3}), sent
        v.on_mouse_down(_Tap(11, 2))              # 트랙 맨 위 = 맨 위로 점프
        assert sent[-1] == (pid, {"delta": 90}), sent
        # 스크롤바 열이 아니면 종전 동작(선택 시작) — 탭이 스크롤로 새지 않는다.
        n = len(sent)
        v.on_mouse_down(_Tap(5, 3))
        assert len(sent) == n, sent
        assert v._sel_start is not None, "스크롤 모드 콘텐츠 탭은 선택 시작"
    await _with_app(body)


async def test_status_badge_toggles_scroll_mode():
    """상태줄 `⇕` 배지 탭으로 스크롤 모드에 들고 난다(휠 없는 터미널의 진입 경로).
    나갈 땐 맨 아래(live)로 복귀 — 키보드 q/ESC 종료와 같은 의미."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        pid = _one_pane(app)
        app.status.refresh()
        app.status._render_main(app.status.rich_style)
        z = app.status._touch_zone
        assert z, "배지가 그려지고 클릭존이 남아야"
        sent = []
        app.send_scroll = lambda p, **kw: sent.append((p, kw))
        ev = _Tap(z[0], app.status.size.height - 1)
        app.status.on_mouse_down(ev)
        assert app.mode == "scroll" and ev.stopped, app.mode
        ev2 = _Tap(z[0], app.status.size.height - 1)
        app.status.on_mouse_down(ev2)
        assert app.mode == "normal", app.mode
        assert sent[-1] == (pid, {"bottom": True}), sent
    await _with_app(body)


# ── ③ 켜고 끄기 ──────────────────────────────────────────────────────────────
async def test_touch_scroll_off_removes_badge_bar_and_zones():
    """`set touch-scroll off` 면 배지·스크롤바·클릭존이 전부 사라진다(옵션 계약).
    끈 뒤에도 콘텐츠 마지막 열은 원래 글자 그대로여야 한다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        pid = _one_pane(app)
        app.mode = "scroll"
        app.apply_option("touch-scroll", "off")
        assert app.touch_scroll is False
        app._composite()
        assert app._touch_scroll_zone is None
        assert app.view._cells[1][11][0] == ".", app.view._cells[1][11]
        app.status._render_main(app.status.rich_style)
        assert app.status._touch_zone is None
        # 다시 켜면 돌아온다(토글이 단방향이 아님).
        app.apply_option("touch-scroll", "on")
        app._composite()
        assert app._touch_scroll_zone == (pid, 11, 1, 6), app._touch_scroll_zone
    await _with_app(body)


# ── ④ 검수(2026-07-31 사이클)에서 나온 항목의 회귀 ──────────────────────────
async def test_layout_prunes_pane_scroll_like_the_other_caches():
    """`pane_scroll` 도 layout 이 선언한 패널만 남긴다(F-D 계열 무한증가 방지).

    비신뢰 상류(원격 뷰)가 pane id 를 마구 흘리면 클라 캐시가 무한히 자란다 —
    2026-07-17 검수가 pane_content/wrap/top 을 정리하게 고쳤는데, 새 캐시를 그
    튜플에 안 넣으면 **같은 구멍이 조용히 다시 열린다**."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        app._dispatch({"t": "screen", "pane": 9991, "rows": [], "top": 5,
                       "scr": 3})
        assert app.pane_scroll.get(9991) == 3
        app._dispatch({"t": "layout", "panes": [{"id": 1, "x": 0, "y": 0,
                                                 "w": 5, "h": 2}],
                       "active": 1, "cols": 20, "rows": 5, "dividers": []})
        assert 9991 not in app.pane_scroll, app.pane_scroll
        assert 9991 not in app.pane_top
    await _with_app(body)


async def test_wire_ints_survive_hostile_upstream_values():
    """상류가 숫자 아닌 top/scr 을 보내도 클라가 죽지 않는다(기본값 0 으로 접음).

    페더레이션 릴레이 검증은 `pane`+`rows` 만 본다 — 종전 `int(...)` 는 여기서
    ValueError 로 클라를 통째로 죽였다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        app._dispatch({"t": "screen", "pane": 5, "rows": [], "top": "x",
                       "scr": {"nope": 1}})
        assert app.pane_top[5] == 0 and app.pane_scroll[5] == 0
        app._dispatch({"t": "screen", "pane": 5, "rows": [], "top": 7,
                       "scr": None})
        assert app.pane_top[5] == 7 and app.pane_scroll[5] == 0
    await _with_app(body)


async def test_no_scrollbar_zone_while_a_popup_covers_the_pane():
    """팝업(display-popup)이 떠 있으면 스크롤바를 그리지도, 클릭존을 남기지도 않는다
    — 팝업이 위에 그려지므로 존만 남으면 탭이 **보이지 않는 것**을 조작한다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        pid = _one_pane(app)
        app.mode = "scroll"
        app._composite()
        assert app._touch_scroll_zone is not None
        app.layout["popup"] = {"id": 999, "x": 0, "y": 0, "w": 20, "h": 8,
                               "cx": 1, "cy": 1, "cw": 18, "ch": 6,
                               "title": "p"}
        app._composite()
        assert app._touch_scroll_zone is None, app._touch_scroll_zone
    await _with_app(body)


async def test_no_scrollbar_zone_while_an_overlay_covers_the_pane():
    """시계·달력 오버레이가 패널을 덮으면 팝업과 **같은 이유**로 존을 안 남긴다.

    오버레이는 코어의 마지막 층 뒤에 그려져 스크롤바를 가린다 — 존만 남으면 탭이
    보이지 않는 것을 조작한다(검수 2026-07-31 §5 가 "코어에 조회 API 가 없다"로 유보한
    자리. 이제 `client_overlay_covers` 훅이 그 사실을 플러그인에게 묻는다).
    """
    from test_client import _with_app

    async def body(app, pilot, srv):
        pid = _one_pane(app)
        app.mode = "scroll"
        app._composite()
        assert app._touch_scroll_zone is not None
        # 시계를 켠다 — 플러그인이 그 사실의 주인이다.
        app.toggle_clock(pid)
        app._composite()
        assert app._touch_scroll_zone is None, app._touch_scroll_zone
        # 끄면 돌아온다(막는 것이 아니라 **덮인 동안만** 비운다).
        app.toggle_clock(pid)
        app._composite()
        assert app._touch_scroll_zone is not None
        # 달력도 같다 — 훅이 플러그인 하나에만 붙어 있으면 다른 오버레이는 샌다.
        app.toggle_calendar(pid)
        app._composite()
        assert app._touch_scroll_zone is None, app._touch_scroll_zone
    await _with_app(body)


async def test_keyboard_scroll_mode_refreshes_the_badge():
    """키보드로 들고 나도 ⇕ 배지(=모드 표시)가 즉시 따라온다 — 호출부 오라클로
    상태줄 refresh 횟수를 센다(배지 스타일은 mode 를 읽어 그린다)."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        _one_pane(app)
        app.send_scroll = lambda p, **kw: None
        n = []
        real = app.status.refresh
        app.status.refresh = lambda *a, **k: (n.append(1), real(*a, **k))[1]
        app.mode = "prefix"
        app._handle_prefix(_Key("["))
        assert app.mode == "scroll" and n, "진입 시 상태줄 갱신"
        n.clear()
        app._handle_scroll_key(_Key("q"))
        assert app.mode == "normal" and n, "이탈 시 상태줄 갱신"
    await _with_app(body)


class _Key:
    """`_handle_*_key` 가 읽는 최소 키 이벤트(key/character)."""
    def __init__(self, ch, key=None):
        self.character = ch
        self.key = key or ch

    def stop(self):
        pass

    def prevent_default(self):
        pass


async def test_config_parses_touch_scroll():
    """config `set touch-scroll off` 가 실제로 클라 기본값을 끈다(기본은 on)."""
    import os
    import tempfile
    from pytmuxlib.keymap import load_config
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config")
        with open(path, "w", encoding="utf-8") as f:
            f.write("set touch-scroll off\n")
        assert load_config(path).get("touch_scroll") is False
        with open(path, "w", encoding="utf-8") as f:
            f.write("set touch-scroll on\n")
        assert load_config(path).get("touch_scroll") is True
    assert load_config(os.path.join(d, "nope")).get("touch_scroll") is None
