"""한 화면을 넘는 마우스 드래그 선택(제보 2026-07-25).

증상: 드래그로 긁다가 휠을 굴리면 선택이 풀려 한 화면보다 긴 텍스트를 복사할 수 없었다.
근본 원인은 선택을 **화면 좌표**로 들고 있었다는 것 — 스크롤되면 같은 칸이 다른 텍스트를
가리키고, 애초에 클라는 뷰포트 셀만 갖고 있어 화면 밖 줄을 만들 수 없다.

처방 두 축을 각각 겨눈다:
  ① **좌표계**: 서버가 screen 메시지에 `top`(뷰포트 첫 행의 절대 인덱스)을 실어 주고,
     클라는 선택을 (절대행, 열)로 들고 매 프레임 화면 좌표로 환산한다 → 스크롤해도
     선택이 텍스트를 따라간다. 드래그 중 휠은 앱에 넘기지 않고 그 패널을 스크롤한다.
  ② **추출**: 화면 밖까지 걸친 범위는 스크롤백을 가진 **서버**가 뽑는다(`copy_range`
     → `selection` 회신 → 클라가 OS 클립보드).

구 서버(top 없음)에는 절대 좌표가 없어 종전 화면-내 선택으로 폴백한다 — 그 폴백도 여기서
고정한다(회귀 방지).
"""
import harness  # noqa: F401 (경로 설정)
from harness import server_only, teardown
from pytmuxlib.model import Pane


# ── ① 서버: 절대 범위 추출 ───────────────────────────────────────────────────
def _pane_with_history(lines, cols=20, rows=5):
    p = Pane(-1, -1, cols, rows)
    p.feed("".join(f"{ln}\r\n" for ln in lines).encode())
    return p


async def test_extract_range_spans_scrollback_and_screen():
    """스크롤백으로 밀려난 줄 + 현재 화면을 **하나의 절대 좌표계**로 뽑는다."""
    p = _pane_with_history([f"line{i}" for i in range(1, 21)], rows=5)
    hist = p._history_len()
    assert hist >= 10, hist            # 20줄을 5행 화면에 → 대부분 스크롤백
    # 절대 0행 = 가장 오래된 줄(line1). 전체를 한 번에 뽑는다.
    text = p.extract_range(0, 0, hist + 4, 19)
    got = [ln for ln in text.split("\n") if ln]
    assert got[0] == "line1", got[:3]
    assert "line20" in got, got[-3:]
    assert len(got) == 20, got         # 20줄 전부(화면 밖 + 화면 안)
    # 부분 범위: 절대 2..4 행만.
    part = p.extract_range(2, 0, 4, 19)
    assert [ln for ln in part.split("\n") if ln] == ["line3", "line4", "line5"], part


async def test_extract_range_column_bounds_and_order():
    """첫/끝 줄은 열 범위를 지키고 중간 줄은 폭 전체 · 뒤바뀐 좌표는 정렬한다."""
    p = _pane_with_history(["ABCDEFGHIJ", "KLMNOPQRST", "UVWXYZ0123"], rows=5)
    top = p._history_len()             # 3줄뿐이면 스크롤백 없음(top=0)
    y = top
    assert p.extract_range(y, 2, y, 5) == "CDEF"
    assert p.extract_range(y, 8, y + 2, 1) == "IJ\nKLMNOPQRST\nUV"
    # 뒤바뀐 (끝→시작) 좌표도 같은 결과(드래그를 위로 긁는 경우).
    assert p.extract_range(y + 2, 1, y, 8) == "IJ\nKLMNOPQRST\nUV"


async def test_extract_range_joins_soft_wrapped_lines():
    """자동 줄바꿈으로 접힌 줄은 개행 없이 이어야 한다(클라 화면-내 추출과 같은 규칙)."""
    p = Pane(-1, -1, 10, 4)
    p.feed(b"0123456789abcde\r\nnext\r\n")   # 첫 줄이 폭 10 을 넘어 접힘
    top = p._history_len()
    text = p.extract_range(0, 0, top + 3, 9)
    assert "0123456789abcde" in text.replace("\n", "|") or \
           "0123456789abcde" in text, repr(text)
    assert "0123456789\nabcde" not in text, "접힌 줄을 개행으로 끊었다: " + repr(text)


async def test_extract_range_clamps_out_of_range_indices():
    """스크롤백이 밀려 없어진 인덱스·음수도 조용히 클램프(예외 금지 — 드래그 중
    출력이 계속되면 정상적으로 일어난다)."""
    p = _pane_with_history(["a", "b", "c"], rows=4)
    assert p.extract_range(-50, -5, 10_000, 10_000) != ""
    assert p.extract_range(10_000, 0, 10_001, 5) == ""   # 전부 화면 밖 → 빈 줄


async def test_render_publishes_absolute_top():
    """`top` 은 render 가 만들어 클라에 전달되는 좌표 원점이다 — 스크롤에 따라 바뀐다."""
    p = _pane_with_history([f"l{i}" for i in range(30)], rows=5)
    p.render(False)
    live_top = p._last_top
    assert live_top == p._history_len(), (live_top, p._history_len())
    p.scroll_by(4)
    p.render(False)
    assert p._last_top == live_top - 4, (p._last_top, live_top)


async def test_copy_range_command_replies_with_selection():
    """서버 명령 경로: copy_range → `selection` 회신(요청 클라에만)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        pane.feed("".join(f"row{i}\r\n" for i in range(40)).encode())
        sent = []

        class _C:
            session = sess
            remote_view = None
            id = 1

        async def _cap(client, obj):
            sent.append(obj)
        srv._send_to = _cap
        await srv._cmd_copy_range(_C(), sess, {"pane": pane.id, "y0": 0, "x0": 0,
                                              "y1": 5, "x1": 79})
        assert sent and sent[0]["t"] == "selection", sent
        lines = [ln for ln in sent[0]["text"].split("\n") if ln]
        assert lines[0] == "row0", lines[:3]
        assert len(lines) == 6, lines
    finally:
        await teardown(srv, task, sock)


# ── ② 클라: 절대 앵커 · 휠로 확장 · 서버 추출 요청 ───────────────────────────
class _FakeMouse:
    def __init__(self, x, y, button=1, ctrl=False, shift=False):
        self.x, self.y, self.button = x, y, button
        self.ctrl, self.shift = ctrl, shift
        self.stopped = False

    def stop(self):
        self.stopped = True


def _one_pane_app(app, pid=7, mouse=2):
    """패널 하나(내용 영역 x=2..11, y=1..5)만 있는 레이아웃 + 마우스 앱 가정."""
    app.layout = {"panes": [{"id": pid, "x": 2, "y": 1, "w": 10, "h": 5,
                             "box": [1, 0, 12, 7], "mouse": mouse,
                             "mouse_sgr": True, "active": True}],
                  "dividers": [], "active": pid, "cols": 100, "rows": 30}
    app.mode = "normal"
    app.mouse_drag_copy = True
    app.pane_top = {pid: 100}          # 서버가 알려준 뷰포트 첫 행 절대 인덱스
    return pid


async def test_drag_selection_survives_wheel_scroll_and_grows():
    """제보의 정면 오라클: 버튼을 누른 채 휠을 굴리면 **선택이 유지되고 확장**된다.

    · 휠은 앱으로 패스스루되지 않는다(pytmux 제스처 중이므로 — 마우스 앱 패널이어도).
    · 스크롤로 top 이 바뀌면 같은 절대 앵커가 다른 화면 행으로 매핑돼 선택이 텍스트를
      따라가고, 포인터가 가리키는 절대 행이 내려가 선택 범위가 커진다.
    """
    from test_client import _with_app

    async def body(app, pilot, srv):
        v = app.view
        pid = _one_pane_app(app)
        sent, scrolls = [], []
        app.send_mouse = lambda p, d: sent.append((p, d))
        app.send_scroll = lambda p, **kw: scrolls.append((p, kw))
        app.send_cmd = lambda action, **kw: None
        v.on_mouse_down(_FakeMouse(3, 2, 1))
        v.on_mouse_move(_FakeMouse(5, 3, 1))
        assert v._sel_abs is not None, "절대 앵커가 세워져야(top 을 아는 서버)"
        anchor = v._sel_abs[:2]
        assert anchor == (101, 1), v._sel_abs      # top 100 + (y=2 - pane_y=1)
        before = v._sel_abs
        # 드래그 중 휠 ↓ — 앱에 안 넘기고 그 패널을 스크롤한다.
        v.on_mouse_scroll_down(_FakeMouse(5, 3, 1))
        assert sent == [], "드래그 중 휠을 앱에 패스스루하면 선택 좌표계가 어긋난다"
        assert scrolls and scrolls[-1][0] == pid, scrolls
        assert v._sel_start is not None and v._sel_abs is not None, "선택이 풀렸다"
        assert v._sel_abs == before, "스크롤 프레임 전에는 범위가 그대로"
        # 서버가 스크롤된 프레임을 보냈다고 가정(top 3 증가) → 같은 포인터가 3행 아래
        # 텍스트를 가리키므로 선택이 그만큼 늘어난다.
        app.pane_top[pid] = 103
        # **호출부**로 검증한다 — 헬퍼(sync_selection)를 직접 부르면 _composite 에서
        # 그 호출을 지워도 통과한다(실측: 뮤테이션 M1 무증상).
        app._composite()
        assert v._sel_abs[:2] == anchor, "앵커(시작점)는 텍스트에 고정돼야"
        assert v._sel_abs[2] == before[2] + 3, (v._sel_abs, before)
        # 화면 좌표(하이라이트)는 여전히 패널 안이다.
        x0, y0, x1, y1 = v._sel
        assert 1 <= y0 <= 5 and 1 <= y1 <= 5, v._sel
    await _with_app(body)


async def test_release_asks_server_for_the_full_range():
    """릴리스 시 절대 범위로 `copy_range` 를 요청한다(클라가 화면 밖 텍스트를 만들 수
    없으므로). 클라측 화면-내 추출로 조용히 잘라 복사하면 안 된다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        v = app.view
        pid = _one_pane_app(app)
        cmds, copied = [], []
        app.send_mouse = lambda p, d: None
        app.send_scroll = lambda p, **kw: None
        app.send_cmd = lambda action, **kw: cmds.append((action, kw))
        app.copy_text = lambda t: copied.append(t)
        v.on_mouse_down(_FakeMouse(3, 2, 1))
        v.on_mouse_move(_FakeMouse(5, 4, 1))
        v.on_mouse_up(_FakeMouse(5, 4, 1))
        assert copied == [], "화면-내 추출로 잘라 복사하면 한 화면을 넘을 수 없다"
        assert cmds and cmds[0][0] == "copy_range", cmds
        kw = cmds[0][1]
        assert kw["pane"] == pid
        assert (kw["y0"], kw["x0"]) == (101, 1), kw     # 시작(정렬됨)
        assert (kw["y1"], kw["x1"]) == (103, 3), kw     # 끝
        assert v._sel_abs is None and v._sel_start is None, "선택 상태가 정리돼야"
    await _with_app(body)


async def test_old_server_without_top_falls_back_to_screen_selection():
    """구 서버(top 미전송)에선 절대 좌표가 없어 종전 화면-내 추출로 복사한다 —
    새 기능이 구 서버에서 **아무것도 복사하지 않는** 회귀를 막는다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        v = app.view
        _one_pane_app(app)
        app.pane_top = {}                  # 구 서버: top 없음
        cmds, copied = [], []
        app.send_mouse = lambda p, d: None
        app.send_cmd = lambda action, **kw: cmds.append((action, kw))
        app.copy_text = lambda t: copied.append(t)
        v._extract_selection = lambda: "SCREENTEXT"
        v.on_mouse_down(_FakeMouse(3, 2, 1))
        v.on_mouse_move(_FakeMouse(5, 4, 1))
        assert v._sel_abs is None, "top 을 모르면 절대 앵커가 없어야"
        v.on_mouse_up(_FakeMouse(5, 4, 1))
        assert copied == ["SCREENTEXT"], copied
        assert [c for c in cmds if c[0] == "copy_range"] == [], cmds
    await _with_app(body)


async def test_wheel_without_drag_still_reaches_the_app():
    """드래그가 아닐 때 휠은 종전대로 마우스 앱에 전달돼야 한다(회귀 가드)."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        v = app.view
        pid = _one_pane_app(app)
        sent = []
        app.send_mouse = lambda p, d: sent.append((p, d))
        app.send_scroll = lambda p, **kw: sent.append(("scroll", kw))
        v.on_mouse_scroll_down(_FakeMouse(5, 3, 1))
        assert sent and sent[0][0] == pid and sent[0][1].endswith(b"M"), sent
    await _with_app(body)


async def test_screen_messages_carry_absolute_top_on_the_wire():
    """**호출부** 오라클: `top` 이 실제로 screen·screen-delta 프레임에 실린다.

    `_last_top` 단위 테스트만 두면 serverio 에서 그 필드를 싣는 줄을 지워도 통과한다
    (이 저장소의 상습 실패 모드 — 값 생성과 붙이는 자리를 둘 다 겨눈다). 클라는 이
    필드가 없으면 화면-내 선택으로 폴백하므로, 빠지면 기능이 **조용히** 사라진다.
    """
    import json
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
        full = decode(srv._screen_frame(c, p.id, rows, cur, p._last_wrap,
                                       p._last_top))
        assert full["t"] == "screen" and full["top"] == p._last_top > 0, full["top"]
        p.feed(b"\x1b[1;1Hx")
        rows2, cur2 = p.render(True)
        delta = decode(srv._screen_frame(c, p.id, rows2, cur2, p._last_wrap,
                                        p._last_top))
        assert delta["t"] == "screen-delta" and delta["top"] == p._last_top, delta
        # 스크롤하면 top 이 따라 내려간다(클라 좌표 환산의 원점).
        p.scroll_by(5)
        rows3, cur3 = p.render(True)
        f3 = decode(srv._screen_frame(c, p.id, rows3, cur3, p._last_wrap,
                                     p._last_top))
        assert f3["top"] == full["top"] - 5, (f3["top"], full["top"])
    finally:
        await teardown(srv, task, sock)


# ── ③ 경계 밖 드래그 자동 스크롤(휠 없이 확장) ───────────────────────────────
async def test_drag_past_pane_edge_autoscrolls_and_stops_inside():
    """패널 content 밖으로 끌면 그 방향으로 **계속** 스크롤하고, 안으로 돌아오면 멈춘다.

    타이머가 필요한 이유를 함께 고정한다: move 이벤트는 포인터가 움직일 때만 오므로
    경계 밖에 멈춰 있으면 스크롤도 멈춰 버린다 → tick 이 이어서 스크롤해야 한다.
    """
    from test_client import _with_app

    async def body(app, pilot, srv):
        v = app.view
        pid = _one_pane_app(app)             # content y=1..5
        scrolls = []
        app.send_mouse = lambda p, d: None
        app.send_cmd = lambda action, **kw: None
        app.send_scroll = lambda p, **kw: scrolls.append((p, kw.get("delta")))
        v.on_mouse_down(_FakeMouse(3, 3, 1))
        v.on_mouse_move(_FakeMouse(5, 4, 1))        # 패널 안 — 자동 스크롤 없음
        assert v._autoscroll is None and v._autoscroll_delta == 0
        assert scrolls == [], scrolls
        # 위쪽 경계 밖(y=0 < py=1) → 위(과거) 방향(+)으로 스크롤 시작
        v.on_mouse_move(_FakeMouse(5, 0, 1))
        assert v._autoscroll_delta > 0, v._autoscroll_delta
        assert v._autoscroll is not None, "경계 밖이면 tick 타이머가 떠야(포인터 정지 대응)"
        # 포인터가 멈춰 있어도 tick 이 계속 스크롤한다.
        v._autoscroll_tick()
        v._autoscroll_tick()
        assert [d for p, d in scrolls if p == pid] == [v._autoscroll_delta] * 2, scrolls
        # 아래쪽 경계 밖 → 반대 방향(-)
        v.on_mouse_move(_FakeMouse(5, 9, 1))
        assert v._autoscroll_delta < 0, v._autoscroll_delta
        # 다시 패널 안 → 멈춘다
        v.on_mouse_move(_FakeMouse(5, 3, 1))
        assert v._autoscroll is None and v._autoscroll_delta == 0
        n = len(scrolls)
        v._autoscroll_tick()
        assert len(scrolls) == n, "멈춘 뒤에는 tick 이 스크롤하지 않아야"
    await _with_app(body)


async def test_autoscroll_speed_grows_with_distance():
    """경계에서 멀수록 빨라지고 상한이 있다(먼 거리 빨리·경계 바로 밖은 한 줄씩)."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        v = app.view
        _one_pane_app(app)
        v._sel_rect = (2, 1, 10, 5)          # content y=1..5
        assert v._edge_scroll_delta(3) == 0          # 안
        assert v._edge_scroll_delta(0) == 1          # 1행 밖
        assert v._edge_scroll_delta(-2) == 2         # 3행 밖
        assert v._edge_scroll_delta(-20) == v._AUTOSCROLL_MAX
        assert v._edge_scroll_delta(6) == -1         # 아래 1행 밖
        assert v._edge_scroll_delta(30) == -v._AUTOSCROLL_MAX
    await _with_app(body)


async def test_autoscroll_stops_on_release_and_extends_selection():
    """릴리스와 함께 멈추고(유령 스크롤 금지), 스크롤된 만큼 선택이 늘어난다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        v = app.view
        pid = _one_pane_app(app)
        scrolls, cmds = [], []
        app.send_mouse = lambda p, d: None
        app.send_scroll = lambda p, **kw: scrolls.append(kw.get("delta"))
        app.send_cmd = lambda action, **kw: cmds.append((action, kw))
        app.copy_text = lambda t: None
        v.on_mouse_down(_FakeMouse(3, 2, 1))         # 절대 101행에서 시작
        v.on_mouse_move(_FakeMouse(5, 0, 1))         # 위 경계 밖 → 자동 스크롤
        v._autoscroll_tick()
        # 서버가 그만큼 스크롤된 프레임을 보냈다고 가정(top 감소 = 과거로)
        app.pane_top[pid] = 100 - 2
        app._composite()
        assert v._sel_abs[2] < v._sel_abs[0], \
            f"위로 끌면 focus 가 앵커보다 과거 행이어야: {v._sel_abs}"
        v.on_mouse_up(_FakeMouse(5, 0, 1))
        assert v._autoscroll is None and v._autoscroll_delta == 0, "릴리스 후 타이머 잔존"
        n = len(scrolls)
        v._autoscroll_tick()
        assert len(scrolls) == n, "릴리스 후 tick 이 스크롤하면 유령 스크롤"
        # 릴리스는 정렬된 절대 범위로 서버에 추출을 요청한다(위로 끌었어도 y0<y1).
        rng = [kw for a, kw in cmds if a == "copy_range"]
        assert rng and rng[0]["y0"] <= rng[0]["y1"], rng
    await _with_app(body)


async def test_autoscroll_tick_self_stops_if_the_release_was_lost():
    """릴리스 이벤트가 **유실된** 경우에도 tick 이 스스로 멈춘다(무한 스크롤 방지).

    호스트 터미널이 버튼 릴리스를 흘리는 일은 실제로 있다(마우스 리포팅 유실·포커스
    이동). 그러면 `_sel_clear` 가 안 불려 타이머만 남는데, 그때 계속 스크롤하면 패널이
    사용자 조작 없이 과거로 감긴다. 정상 경로(_sel_clear)는 타이머를 멈추므로 이 가드는
    **그 경로가 안 불린 경우**만 겨눈다 — 그래서 이 테스트가 따로 필요하다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        v = app.view
        _one_pane_app(app)
        scrolls = []
        app.send_mouse = lambda p, d: None
        app.send_cmd = lambda action, **kw: None
        app.send_scroll = lambda p, **kw: scrolls.append(kw.get("delta"))
        v.on_mouse_down(_FakeMouse(3, 3, 1))
        v.on_mouse_move(_FakeMouse(5, 0, 1))
        assert v._autoscroll is not None
        v._sel_start = None            # 릴리스 유실(선택 상태만 사라짐)
        n = len(scrolls)
        v._autoscroll_tick()
        assert len(scrolls) == n, "드래그가 끝났는데 tick 이 스크롤했다"
        assert v._autoscroll is None, "tick 이 스스로 타이머를 정리해야"
    await _with_app(body)


async def test_autoscroll_timer_actually_fires_in_a_live_app():
    """**호출부** 오라클: tick 을 손으로 부르지 않아도 실제 타이머가 스크롤을 낸다.

    `_autoscroll_tick` 단위 테스트만 두면 `set_interval` 배선을 지워도 통과한다 —
    그러면 포인터를 경계 밖에 **멈춰 둔 채** 기다리는 실제 사용에서 아무 일도 일어나지
    않는다(이 기능의 존재 이유가 사라진다)."""
    from test_client import _with_app
    from harness import wait_until

    async def body(app, pilot, srv):
        v = app.view
        _one_pane_app(app)
        scrolls = []
        app.send_mouse = lambda p, d: None
        app.send_cmd = lambda action, **kw: None
        app.send_scroll = lambda p, **kw: scrolls.append(kw.get("delta"))
        v.on_mouse_down(_FakeMouse(3, 3, 1))
        v.on_mouse_move(_FakeMouse(5, 0, 1))        # 경계 밖에서 포인터 정지
        ok = await wait_until(pilot, lambda: len(scrolls) >= 2, timeout=3.0)
        assert ok, f"타이머가 안 돌았다(스크롤 {len(scrolls)}회)"
        assert all(d > 0 for d in scrolls), scrolls
        v.on_mouse_up(_FakeMouse(5, 0, 1))
        n = len(scrolls)
        await pilot.pause(0.25)                     # 릴리스 후 정지 확인(시간이 오라클)
        assert len(scrolls) == n, f"릴리스 후에도 스크롤됨: {n}→{len(scrolls)}"
    await _with_app(body)
