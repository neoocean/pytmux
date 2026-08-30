"""pytmux-421 — 포커스 이벤트(DECSET 1004)를 켠 앱에 `ESC[I`/`ESC[O` 를 보낸다.

배경: 포커스를 아는 앱은 그것으로 깜빡임·폴링·자동 새로고침을 멈춘다. pytmux 는 마우스
DECSET(1000/1002/1003/1006)은 추적했지만 **1004 는 코드 어디에도 없었고**, `ESC[I`/`ESC[O`
를 만들어 보내는 자리도 없었다 — 앱은 「단말이 늘 포커스」로 알고 살았다. claude 는
classic 렌더러에서도 이것을 켠다(pytmux-420 조사의 대조군 캡처).

**규칙 한 줄**: 어떤 패널이 포커스를 갖는다 = 「포커스를 가진 클라가 그 패널을 활성으로
보고 있다」. 여러 클라가 붙는 멀티플렉서라 창 하나의 포커스로는 답이 안 나온다.

되돌리면 실패해야 하는 오라클:
  · 1004 를 안 추적하면            → test_the_app_can_turn_focus_reports_on 실패
  · 안 켠 패널에도 쓰면            → test_a_pane_that_never_asked_gets_nothing 실패
    (그 두 바이트는 앱이 안 읽으면 **글자로** 화면에 박힌다)
  · 바뀔 때만 쓰는 것을 지우면      → test_it_writes_only_on_change 실패
  · 켠 순간 알리는 것을 지우면      → test_turning_it_on_reports_the_current_state 실패
  · 「가진 클라가 보고 있는 패널」을 → test_focus_follows_the_client_that_has_it 실패
    아무 패널로 넓히면
  · 원격 보기 클라를 주인으로 세면  → test_a_client_watching_a_remote_is_not_the_owner 실패
  · 클라가 다 떨어졌는데 blur 를    → test_the_last_client_leaving_blurs_the_pane 실패
    안 보내면
  · **부르는 자리를 지우면**        → test_the_call_sites_are_wired 실패
"""
import ast
import inspect

import harness  # noqa: F401  (스위트 공통 부트스트랩)

from pytmuxlib import serverio
from pytmuxlib.model import ClientConn, Pane


class _FakePty:
    """PTY 대신 쓴 바이트를 모으는 자리."""

    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)


def _pane(cols=80, rows=24):
    p = Pane(pid=0, fd=-1, cols=cols, rows=rows)
    p.pty = _FakePty()
    return p


class _Window:
    def __init__(self, pane):
        self.active_pane = pane
        self._panes = [pane]

    def panes(self):
        return self._panes


class _Tab:
    def __init__(self, window):
        self.window = window


class _Session:
    def __init__(self, panes):
        self.tabs = [_Tab(_Window(p)) for p in panes]
        self.active_index = 0

    @property
    def active_window(self):
        return self.tabs[self.active_index].window


class _Server(serverio.ServerIOMixin):
    """`sync_pane_focus` 만 빌려 쓰는 최소 서버."""

    def __init__(self, clients):
        self.clients = clients


def _client(sess, focus=True, remote_view=False):
    c = ClientConn.__new__(ClientConn)
    c.session = sess
    c.has_focus = focus
    c.remote_view = remote_view
    return c


def _arm(pane):
    """앱이 1004 를 켰다 — 실제 바이트로 켠다(플래그를 손으로 세우지 않는다)."""
    pane.update_mouse_modes(b"\x1b[?1004h")


# ---- 모델: 앱이 켜고 끈다 ----

def test_the_app_can_turn_focus_reports_on():
    p = _pane()
    assert p.focus_track is False, "아무도 안 켰으면 꺼져 있다"
    _arm(p)
    assert p.focus_track is True


def test_the_app_can_turn_it_off():
    p = _pane()
    _arm(p)
    p.update_mouse_modes(b"\x1b[?1004l")
    assert p.focus_track is False


def test_a_combined_decset_turns_it_on_too():
    """DECSET 은 파라미터를 `;` 로 묶어 한 시퀀스로 온다 — 하나만 보던 옛 정규식이
    결합형 해제를 놓쳐 마우스가 켜진 채 남던 것과 같은 부류다."""
    p = _pane()
    p.update_mouse_modes(b"\x1b[?1000;1002;1004;1006h")
    assert p.focus_track is True and p.mouse_track == 2
    p.update_mouse_modes(b"\x1b[?1000;1002;1004;1006l")
    assert p.focus_track is False and p.mouse_track == 0


def test_a_new_shell_forgets_it():
    """포커스 리포트는 **앱의 것**이다 — 셸이 새로 뜨면 아무도 안 켠 상태로 돌아간다."""
    p = _pane()
    _arm(p)
    p.reset_mouse_modes()
    assert p.focus_track is False


# ---- 서버: 누가 포커스의 주인인가 ----

def test_focus_follows_the_client_that_has_it():
    a, b = _pane(), _pane()
    sess = _Session([a, b])
    _arm(a)
    _arm(b)
    srv = _Server([_client(sess, focus=True)])
    srv.sync_pane_focus(sess)
    # 활성 탭은 0 번 — a 만 포커스다. b 도 1004 를 켰지만 아무도 안 보고 있다.
    assert a.pty.written == [b"\x1b[I"]
    assert b.pty.written == [b"\x1b[O"]


def test_a_blurred_client_owns_nothing():
    a = _pane()
    sess = _Session([a])
    _arm(a)
    srv = _Server([_client(sess, focus=False)])
    srv.sync_pane_focus(sess)
    assert a.pty.written == [b"\x1b[O"]


def test_two_clients_on_different_tabs_both_have_focus():
    """두 사람이 다른 탭을 보고 있으면 **둘 다** 포커스다 — 창 하나의 포커스로
    답을 내면 이 경우가 틀린다."""
    a, b = _pane(), _pane()
    sess = _Session([a, b])
    _arm(a)
    _arm(b)
    other = _client(sess, focus=True)
    # 두 번째 클라는 1번 탭을 본다 — 세션이 하나라 활성 탭을 바꿔 흉내낸다.
    srv = _Server([_client(sess, focus=True)])
    srv.sync_pane_focus(sess)
    assert a.pty.written[-1] == b"\x1b[I" and b.pty.written[-1] == b"\x1b[O"
    sess.active_index = 1
    srv.clients.append(other)
    srv.sync_pane_focus(sess)
    # 활성 탭이 1 로 옮겨졌으니 이번엔 b 가 포커스다(a 는 blur).
    assert b.pty.written[-1] == b"\x1b[I" and a.pty.written[-1] == b"\x1b[O"


def test_a_client_watching_a_remote_is_not_the_owner():
    """§1.7 원격 보기 중인 클라가 보고 있는 것은 **상류 화면**이다 — 그동안 로컬
    패널은 아무도 안 보는 것이 맞다."""
    a = _pane()
    sess = _Session([a])
    _arm(a)
    srv = _Server([_client(sess, focus=True, remote_view=True)])
    srv.sync_pane_focus(sess)
    assert a.pty.written == [b"\x1b[O"]


def test_the_last_client_leaving_blurs_the_pane():
    a = _pane()
    sess = _Session([a])
    _arm(a)
    srv = _Server([_client(sess, focus=True)])
    srv.sync_pane_focus(sess)
    assert a.pty.written[-1] == b"\x1b[I"
    srv.clients.clear()
    srv.sync_pane_focus(sess)
    assert a.pty.written[-1] == b"\x1b[O"


# ---- 서버: 무엇을 안 쓰나 ----

def test_a_pane_that_never_asked_gets_nothing():
    """⛔ 1004 를 안 켠 앱에 쓰면 그 두 바이트가 **글자로** 화면에 박힌다
    (`ESC[I` → `[I`). 마우스 리포트가 셸 프롬프트에 박히던 것과 같은 부류다."""
    a = _pane()
    sess = _Session([a])
    srv = _Server([_client(sess, focus=True)])
    srv.sync_pane_focus(sess)
    assert a.pty.written == []


def test_it_writes_only_on_change():
    """매 프레임 쓰면 앱이 그때마다 포커스 전이를 처리해 다시 그린다 — 조용한 화면이
    30Hz 로 깜빡인다."""
    a = _pane()
    sess = _Session([a])
    _arm(a)
    srv = _Server([_client(sess, focus=True)])
    for _ in range(5):
        srv.sync_pane_focus(sess)
    assert a.pty.written == [b"\x1b[I"]


def test_turning_it_on_reports_the_current_state():
    """앱이 **방금 켰으면** 값이 안 바뀌었어도 한 번 알린다 — 켠 순간의 상태를 모르면
    다음 전이까지 그림이 틀린다."""
    a = _pane()
    sess = _Session([a])
    _arm(a)
    srv = _Server([_client(sess, focus=True)])
    srv.sync_pane_focus(sess)
    assert a.pty.written == [b"\x1b[I"]
    # 앱이 껐다가 다시 켠다 — 값(포커스 있음)은 그대로지만 다시 알려야 한다.
    a.update_mouse_modes(b"\x1b[?1004l")
    srv.sync_pane_focus(sess)
    _arm(a)
    srv.sync_pane_focus(sess)
    assert a.pty.written == [b"\x1b[I", b"\x1b[I"]


def test_a_dying_pane_does_not_take_the_server_with_it():
    """종료 중인 패널의 PTY 쓰기는 실패할 수 있다 — 포커스 하나 때문에 안 죽는다."""
    class _Broken(_FakePty):
        def write(self, data):
            raise OSError("closed")

    a = _pane()
    a.pty = _Broken()
    _arm(a)
    sess = _Session([a])
    _Server([_client(sess, focus=True)]).sync_pane_focus(sess)   # 안 던지면 통과


def test_a_pane_without_a_pty_is_skipped():
    a = _pane()
    a.pty = None
    _arm(a)
    sess = _Session([a])
    _Server([_client(sess, focus=True)]).sync_pane_focus(sess)   # 안 던지면 통과


def test_a_bare_stand_in_client_does_not_break_it():
    """⛔ `self.clients` 에는 온전한 `ClientConn` 말고 **대역**(테스트 스텁·릴레이
    자리)도 들어온다. 없는 칸을 곧바로 읽으면 그 자리에서 AttributeError 가 나는데,
    이 함수는 **세션이 죽는 경로에서도** 불리므로 정리 도중에 터진다(실측: 이 오라클을
    쓰기 전에 `test_session_death_reassigns_every_client` 가 그렇게 떨어졌다).

    모르는 대역은 「모르면 종전처럼」 = 포커스 있음으로 본다."""
    class _Bare:
        def __init__(self, sess):
            self.session = sess      # 이것 말고는 아무 칸도 없다

    a = _pane()
    sess = _Session([a])
    _arm(a)
    _Server([_Bare(sess)]).sync_pane_focus(sess)
    assert a.pty.written == [b"\x1b[I"]


# ---- 배선: 부르는 자리가 실제로 있나 ----

def test_the_call_sites_are_wired():
    """**호출 제거** 뮤테이션: 위 시험은 `sync_pane_focus` 를 직접 부르므로, 제품에서
    부르는 줄을 전부 지워도 통째로 초록이다. 그래서 자리를 소스에서 직접 센다.

    셋이 필요하다 — ⓐ 클라가 포커스를 알렸을 때 ⓑ 구조가 바뀌었을 때(활성 패널·탭)
    ⓒ 클라가 떨어졌을 때. ⓓ 앱이 1004 를 켠 순간은 `serverpty` 쪽이다."""
    tree = ast.parse(inspect.getsource(serverio))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "sync_pane_focus"]
    assert len(calls) == 3, f"serverio 의 호출 자리가 셋이어야 한다(지금 {len(calls)})"

    from pytmuxlib import serverpty
    tree2 = ast.parse(inspect.getsource(serverpty))
    armed = [n for n in ast.walk(tree2)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "sync_pane_focus"]
    assert len(armed) == 1, "앱이 1004 를 켠 순간 알리는 자리가 없다"


def test_the_client_reports_its_focus():
    """정본 클라가 Textual 의 앱 포커스/블러를 서버에 나른다. 이 배선이 빠지면 서버는
    영원히 「포커스 있음」(기본값)으로 알고, 증상은 「blur 가 영영 안 온다」다."""
    from pytmuxlib import clientio
    src = inspect.getsource(clientio)
    assert "async def on_app_focus" in src and "async def on_app_blur" in src
    tree = ast.parse(src)
    sends = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_send_focus"]
    assert len(sends) == 2, "focus/blur 둘 다 보내야 한다"
