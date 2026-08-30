"""스크롤 «와이어» 계약 — 서버가 싣는 `scr` 과 클라 `pane_scroll` 캐시.

⛔ **이 파일은 종전의 `test_touch_scroll.py` 에서 남은 절반이다.** 탭(터치)으로 쓰는
스크롤 UI(설정 `touch-scroll` · 상태줄 `⇕` 배지 · 스크롤 모드 세로 스크롤바)는
**사용자 결정으로 두 클라에서 걷었다**(pytmux-377) — 그 UI 를 재던 시험은 함께 지웠다.

여기 남은 것은 그 UI 의 것이 **아니다**:

- `scr`(라이브에서 위로 올라간 행수)는 **살아 있는 서버 필드**이고, Rust GUI 의
  **표시용** 스크롤바(pytmux-25 — 패널 외곽선 위에 얹히는 얇은 막대)가 그것을 읽는다.
  ⛔ 조작용(걷은 것)과 표시용(남은 것)은 다른 물건이다.
- 그래서 *"올라가 있을 때만 싣는다"*(라이브 프레임은 바이트 불변)와 *"클라가 그 값을
  종단으로 받는다"*를 여기서 계속 잰다. 이 두 줄이 없으면 GUI 의 썸이 조용히 죽는다.
- 캐시 정리(layout 이 선언한 패널만 남긴다)와 적대적 상류 값(숫자가 아닌 top/scr)도
  같은 캐시의 것이라 함께 남는다.
"""
import json

import harness  # noqa: F401 (경로 설정)
from harness import server_only, teardown, wait_until


def _pane(app, pid=7, x=2, y=1, w=10, h=6):
    """패널 하나짜리 최소 배치 — 모드 전이 오라클이 앱을 세우는 데만 쓴다."""
    app.layout = {"panes": [{"id": pid, "x": x, "y": y, "w": w, "h": h,
                             "box": [x - 1, y - 1, w + 2, h + 2],
                             "mouse": 0, "mouse_sgr": False, "active": True}],
                  "dividers": [], "active": pid, "cols": 40, "rows": 12}
    app.pane_content = {pid: ([[("." * w, {})] for _ in range(h)], None)}
    app.pane_top = {pid: 90}
    app.pane_scroll = {pid: 0}
    return pid


class _Key:
    """`_handle_*_key` 가 읽는 최소 키 이벤트(key/character)."""
    def __init__(self, ch, key=None):
        self.character = ch
        self.key = key or ch

    def stop(self):
        pass

    def prevent_default(self):
        pass


# ── 서버 → 클라 와이어 ───────────────────────────────────────────────────────
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


# ── 모드 전이가 상태줄을 다시 그리나 ─────────────────────────────────────────
async def test_keyboard_scroll_mode_refreshes_the_status_bar():
    """스크롤 모드를 들고 날 때 상태줄이 **즉시** 다시 그려진다 — 호출부 오라클로
    refresh 횟수를 센다.

    ⚠ 종전에는 이 오라클이 `⇕` 배지를 겨눴다(pytmux-377 로 그 배지를 걷었다). 재는
    것은 그대로 뜻이 있다 — 상태줄은 모드에 따라 달라지는 것을 여럿 그리고, 이 갱신이
    빠지면 다음 프레임까지 화면이 옛말을 한다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        _pane(app)
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
