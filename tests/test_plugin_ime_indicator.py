"""ime-indicator 플러그인 회귀 — 한/영 추정 상태 전이, 배지 그리기, 명령 토글, 계약.

설계 배경(docs/internal/IME_PREEDIT_CURSOR_SCENARIO.md): 앱은 OS IME 의 *조합 중* preedit 을
관찰할 수 없고 **확정된 글자만** 키 이벤트로 받는다. 그래서 한/영은 패널로 보낼 확정
입력 문자의 스크립트로 추정한다 — 한글→'한', ASCII 글자→'EN', 숫자/기호는 모드 중립.

`draw_ime_indicator` 는 앱 비의존 순수 함수라 앱·소켓 없이 직접 호출해 셀 출력을 단언한다.
client_key/handle_command 는 가짜 app 으로, 코어 on_key 배선은 라이브 앱으로 가드한다.
계약(delete-to-disable): 플러그인을 Registry 에서 빼면 ime 명령/훅이 전부 사라진다.
"""
import os

import harness  # noqa: F401  (sys.path 주입)
from harness import make_app, server_only, teardown
from rich.style import Style
from textual.events import Key


def _without_ssh_env():
    """SSH_CONNECTION/SSH_TTY 를 임시 제거하고 저장본을 돌려준다 — OS 실측(macOS TIS)
    경로는 비-ssh 로컬 전제다. 이 테스트 세션 자체가 ssh 일 수 있어(§9.1) env 비의존
    결정성을 위해 OS-경로 테스트는 ssh 신호를 걷어낸다. 복원=_restore_ssh_env."""
    return {k: os.environ.pop(k, None) for k in ("SSH_CONNECTION", "SSH_TTY")}


def _restore_ssh_env(saved):
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v

import pytmuxlib.plugins as plugins


def _grid(w, h):
    base = Style()
    return [[(" ", base) for _ in range(w)] for _ in range(h)]


def _text_rows(cells):
    return ["".join(c[0] for c in row) for row in cells]


# 하이픈 디렉토리(ime-indicator)라 일반 import 불가 — importlib 로 모듈을 가져온다.
import importlib  # noqa: E402

_render = importlib.import_module("pytmuxlib.plugins.ime-indicator.render")
_pkg = importlib.import_module("pytmuxlib.plugins.ime-indicator")
draw_ime_indicator = _render.draw_ime_indicator
PLUGIN = _pkg.PLUGIN


class _FakeApp:
    """client_key/handle_command 가 닿는 최소 표면만 흉내낸 가짜 앱."""
    def __init__(self):
        self.ime_show = True
        self.ime_state = "EN"
        self.composited = 0
        self.messages = []

    def _composite(self):
        self.composited += 1

    def display_message(self, m):
        self.messages.append(m)


class _Ev:
    def __init__(self, character):
        self.character = character


# ---- 1) 순수 렌더 함수 ----
async def test_badge_drawn_top_right_and_widths():
    # '한'(와이드 2칸) → "[한]" = 4칸, 우측 reserve=4 비우고 우측정렬.
    cells = _grid(40, 5)
    st = Style(color="black", bgcolor="green", bold=True)
    draw_ime_indicator(cells, 40, 5, "한", st)
    row0 = _text_rows(cells)[0]
    assert "[한]" in row0, row0
    # 우측 4칸은 비어 있어야([x] 자리). 마지막 4칸 공백 확인.
    assert row0[-4:] == "    ", repr(row0[-4:])
    # 배지는 row 0 에만(다른 행은 공백).
    assert all(c[0] == " " for row in cells[1:] for c in row)
    # 'EN' = "[EN]" 4칸, 모두 단일폭.
    cells2 = _grid(40, 5)
    draw_ime_indicator(cells2, 40, 5, "EN", st)
    assert "[EN]" in _text_rows(cells2)[0]


async def test_badge_skipped_when_too_narrow():
    # 폭이 배지+reserve 를 못 담으면 아무것도 안 그린다.
    cells = _grid(6, 3)
    draw_ime_indicator(cells, 6, 3, "한", Style())
    assert all(c[0] == " " for row in cells for c in row)


async def test_badge_wide_continuation_cell():
    # 한글 본체 다음 칸은 빈 연속 셀("")이어야 정렬이 안 깨진다.
    cells = _grid(40, 2)
    draw_ime_indicator(cells, 40, 2, "한", Style())
    chars = [c[0] for c in cells[0]]
    i = chars.index("한")
    assert chars[i + 1] == "", chars[i:i + 3]


async def test_badge_on_cursor_row_right_end():
    """2026-06-11 요청: 배지는 커서가 있는 줄(y)의 오른쪽 끝 — y≠0 이면 reserve 0
    으로 진짜 끝까지, 행 범위 밖 y 는 생략(None)."""
    cells = _grid(40, 5)
    span = draw_ime_indicator(cells, 40, 5, "한", Style(), y=3, reserve_right=0)
    assert span == (36, 40), span
    row3 = _text_rows(cells)[3]
    assert row3.endswith("[한]" + ""), repr(row3[-6:])
    assert "[한]" in row3 and all(
        c[0] == " " for r, row in enumerate(cells) if r != 3 for c in row)
    # y 가 화면 밖이면 생략
    cells2 = _grid(40, 5)
    assert draw_ime_indicator(cells2, 40, 5, "EN", Style(), y=7) is None
    assert all(c[0] == " " for row in cells2 for c in row)


async def test_badge_at_active_pane_right_edge():
    """2026-06-16 요청: 좌우 분할에서 활성 패널이 화면 왼쪽 절반이면 배지는 화면
    오른쪽 끝이 아니라 **활성 패널의 오른쪽 끝**(x_right)에 그려져야 한다."""
    # 활성 패널 = 왼쪽 절반(우측 경계 x_right=40), 화면 폭 80.
    cells = _grid(80, 5)
    span = draw_ime_indicator(cells, 80, 5, "EN", Style(), y=2,
                              reserve_right=0, x_right=40)
    assert span == (36, 40), span
    row2 = _text_rows(cells)[2]
    assert row2[36:40] == "[EN]", repr(row2[34:42])
    # 화면 오른쪽 절반(비활성 패널 위)은 건드리지 않는다.
    assert row2[40:] == " " * 40, repr(row2[40:])
    # x_right 미지정이면 종전대로 화면 폭 끝.
    cells2 = _grid(80, 5)
    assert draw_ime_indicator(cells2, 80, 5, "EN", Style(), y=2,
                              reserve_right=0) == (76, 80)


# ---- 2) client_key 한/영 추정 상태 전이 ----
async def test_client_key_state_transitions():
    app = _FakeApp()
    app.ime_state = "EN"
    # 한글 확정 입력 → '한' 전환 + 재합성.
    PLUGIN.client_key(app, _Ev("가"))
    assert app.ime_state == "한"
    assert app.composited == 1
    # 같은 상태 유지 입력은 재합성 안 함(중복 합성 방지).
    PLUGIN.client_key(app, _Ev("나"))
    assert app.ime_state == "한" and app.composited == 1
    # 숫자/기호/공백은 모드 중립 — 상태 유지.
    for ch in ("5", " ", ".", "@"):
        PLUGIN.client_key(app, _Ev(ch))
    assert app.ime_state == "한" and app.composited == 1
    # ASCII 글자 → 'EN' 전환.
    PLUGIN.client_key(app, _Ev("b"))
    assert app.ime_state == "EN" and app.composited == 2
    # 호환자모(조합 낱자)도 한글로 인식.
    PLUGIN.client_key(app, _Ev("ㅁ"))
    assert app.ime_state == "한"
    # 비인쇄/문자 없음(방향키·Ctrl 등)은 무시.
    PLUGIN.client_key(app, _Ev(None))
    PLUGIN.client_key(app, _Ev("\x1b"))
    assert app.ime_state == "한"


async def test_client_key_no_composite_when_hidden():
    # 배지가 꺼져 있으면 상태는 추적하되 재합성은 하지 않는다(불필요한 프레임 방지).
    app = _FakeApp()
    app.ime_show = False
    app.ime_state = "EN"
    PLUGIN.client_key(app, _Ev("가"))
    assert app.ime_state == "한" and app.composited == 0


# ---- 3) 명령 토글 ----
async def test_toggle_command():
    app = _FakeApp()
    assert PLUGIN.handle_command(app, "ime-indicator", []) is True
    assert app.ime_show is False and app.composited == 1
    assert app.messages and "OFF" in app.messages[-1]
    assert PLUGIN.handle_command(app, "ime", []) is True   # 별칭
    assert app.ime_show is True
    assert "ON" in app.messages[-1]
    # 모르는 명령은 처리 안 함.
    assert PLUGIN.handle_command(app, "clock-mode", []) is False


# ---- 3.5) §10-B OS 실측(macOS TIS) 경로 — 전부 스텁(환경 비의존) ----
_oskbd = importlib.import_module("pytmuxlib.plugins.ime-indicator.oskbd")


def _stub_source(sid):
    """oskbd.current_source_id 를 고정값 스텁으로 교체하고 원본을 돌려준다."""
    orig = _oskbd.current_source_id
    _oskbd.current_source_id = lambda: sid
    return orig


async def test_oskbd_is_korean_mapping():
    assert _oskbd.is_korean("com.apple.inputmethod.Korean.2SetKorean") is True
    assert _oskbd.is_korean("org.youknowone.inputmethod.Gureum.han2") is True
    assert _oskbd.is_korean("com.apple.keylayout.ABC") is False
    assert _oskbd.is_korean("com.apple.keylayout.US") is False
    assert _oskbd.is_korean(None) is False
    assert _oskbd.is_korean("") is False


async def test_os_probe_sets_initial_state_and_suppresses_heuristic():
    """OS 질의가 가능하면(스텁) attach_client 가 실측으로 초기 상태를 잡고,
    client_key 휴리스틱은 침묵한다(한글 모드에서 영문을 쳐도 'EN' 오판 없음).
    폴링(_poll)은 소스 변경을 즉시 반영하고, 일시 실패(None)는 직전 상태 유지."""
    orig = _stub_source("com.apple.inputmethod.Korean.2SetKorean")
    _ssh = _without_ssh_env()        # OS 실측 경로 = 비-ssh 로컬 전제(§9.1)
    try:
        app = _FakeApp()
        PLUGIN.attach_client(app)
        assert app._ime_os is True and app.ime_state == "한"
        # 휴리스틱 침묵: 영문 확정 입력이 와도 실측('한') 그대로.
        PLUGIN.client_key(app, _Ev("b"))
        assert app.ime_state == "한" and app.composited == 0
        # 폴링: 영어 소스로 바뀌면 즉시 'EN' + 재합성.
        _oskbd.current_source_id = lambda: "com.apple.keylayout.ABC"
        PLUGIN._poll(app)
        assert app.ime_state == "EN" and app.composited == 1
        # 일시 실패(None)는 상태 유지(깜빡임 방지).
        _oskbd.current_source_id = lambda: None
        PLUGIN._poll(app)
        assert app.ime_state == "EN" and app.composited == 1
    finally:
        _oskbd.current_source_id = orig
        _restore_ssh_env(_ssh)


async def test_os_unavailable_falls_back_to_heuristic():
    """OS 질의 불가(None — 비 macOS·ssh 원격 등 스텁)면 attach_client 는 폴백
    모드(EN 시작), client_tick 은 타이머 없이 False, client_key 휴리스틱 동작."""
    orig = _stub_source(None)
    try:
        app = _FakeApp()
        PLUGIN.attach_client(app)
        assert app._ime_os is False and app.ime_state == "EN"
        assert PLUGIN.client_tick(app) is False
        assert app._ime_os_timer is None, "OS 불가면 폴링 타이머도 안 깐다"
        PLUGIN.client_key(app, _Ev("가"))
        assert app.ime_state == "한"
    finally:
        _oskbd.current_source_id = orig


async def test_ssh_remote_suppresses_os_probe_uses_heuristic():
    """§9.1: plain ssh 원격(SSH_CONNECTION 설정)에선 로컬 OS 질의가 **원격 박스**의
    키보드를 보므로 끄고(_ime_os=False) 확정 입력 휴리스틱으로 폴백한다 — OS 질의가
    한글 소스를 줘도(여기선 스텁) 무시하고, 실제 타이핑하는 글자 스크립트를 따른다.
    네이티브 remote-attach(클라=로컬, SSH_CONNECTION 없음)와 구분되는 경로다."""
    orig = _stub_source("com.apple.inputmethod.Korean.2SetKorean")  # 로컬이면 '한' 줬을 값
    saved = {k: os.environ.get(k) for k in ("SSH_CONNECTION", "SSH_TTY")}
    os.environ["SSH_CONNECTION"] = "1.2.3.4 5 6.7.8.9 22"
    os.environ.pop("SSH_TTY", None)
    try:
        app = _FakeApp()
        PLUGIN.attach_client(app)
        # OS 질의가 한글을 주더라도 ssh 원격이라 실측을 끄고 EN(폴백)에서 시작.
        assert app._ime_os is False, "ssh 원격은 OS 실측을 끈다"
        assert app.ime_state == "EN"
        # 휴리스틱 동작: 한글 확정 입력 → '한', ASCII → 'EN'.
        PLUGIN.client_key(app, _Ev("가"))
        assert app.ime_state == "한"
        PLUGIN.client_key(app, _Ev("z"))
        assert app.ime_state == "EN"
        # SSH_TTY 만 있어도 동일(둘 중 하나면 원격으로 본다).
        os.environ.pop("SSH_CONNECTION", None)
        os.environ["SSH_TTY"] = "/dev/ttys001"
        app2 = _FakeApp()
        PLUGIN.attach_client(app2)
        assert app2._ime_os is False
    finally:
        _oskbd.current_source_id = orig
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def test_client_tick_lazily_installs_fast_timer_once():
    """첫 client_tick 이 0.05초 전용 폴링 타이머를 1회만 지연 설치한다(attach 시점엔
    앱이 안 돌아 set_interval 불가). set_interval 이 없는 환경(테스트 더미)은 False
    마킹으로 재시도하지 않는다."""
    orig = _stub_source("com.apple.keylayout.ABC")
    _ssh = _without_ssh_env()        # OS 실측 경로 = 비-ssh 로컬 전제(§9.1)
    # 인프로세스 폴링(Windows·폴백) 경로 검증 — macOS 감시 헬퍼 spawn 은 막아
    # 결정성 보장(darwin 에서 실제 자식 프로세스가 뜨지 않게). 헬퍼 드레인 경로는
    # test_macos_watcher_drain_updates_state 가 별도로 가드한다.
    orig_spawn = _oskbd.spawn_watcher
    _oskbd.spawn_watcher = lambda: None
    try:
        calls = []

        class _TimerApp(_FakeApp):
            def set_interval(self, sec, fn):
                calls.append((sec, fn))
                return ("timer", len(calls))

        app = _TimerApp()
        PLUGIN.attach_client(app)
        assert app._ime_os is True
        assert PLUGIN.client_tick(app) is False
        assert len(calls) == 1 and calls[0][0] == 0.05
        PLUGIN.client_tick(app)
        assert len(calls) == 1, "타이머는 1회만 설치"
        # 타이머 콜백이 _poll 을 부른다 — 소스 전환 반영.
        _oskbd.current_source_id = lambda: "com.apple.inputmethod.Korean.2SetKorean"
        calls[0][1]()
        assert app.ime_state == "한"
        # set_interval 없는 앱은 False 마킹(재시도 안 함) + 틱 폴링은 그대로 동작.
        app2 = _FakeApp()
        PLUGIN.attach_client(app2)
        PLUGIN.client_tick(app2)
        assert app2._ime_os_timer is False
        assert app2.ime_state == "한"
    finally:
        _oskbd.current_source_id = orig
        _oskbd.spawn_watcher = orig_spawn
        _restore_ssh_env(_ssh)


# ---- 3.6) macOS 감시 헬퍼 경로(인프로세스 TIS freeze 우회) ----
async def test_macos_watcher_drain_updates_state():
    """감시 헬퍼가 살아있으면(_ime_watch) _poll 은 인프로세스 질의 대신 헬퍼 stdout
    (read_latest)에서 최신 소스 ID 를 드레인해 배지를 갱신한다. 새 줄 없음(None)은
    직전 상태 유지, 헬퍼 종료(poll()!=None)면 드레인하지 않고 유지한다."""
    class _FakeProc:
        def __init__(self):
            self._rc = None

        def poll(self):
            return self._rc

    app = _FakeApp()
    app.ime_state = "EN"
    app._ime_os = True
    app._ime_buf = b""
    app._ime_watch = _FakeProc()
    queue = [("com.apple.inputmethod.Korean.2SetKorean", b""),
             (None, b""),
             ("com.apple.keylayout.ABC", b"")]
    orig_rl = _oskbd.read_latest
    orig_cur = _oskbd.current_source_id
    _oskbd.read_latest = lambda proc, buf: queue.pop(0)
    # 헬퍼 경로에선 인프로세스 질의를 절대 쓰면 안 된다(freeze 값 역류 방지).
    def _boom():
        raise AssertionError("watcher 경로는 current_source_id 를 쓰지 않아야 한다")
    _oskbd.current_source_id = _boom
    try:
        PLUGIN._poll(app)                       # 한글 줄 → '한'
        assert app.ime_state == "한" and app.composited == 1
        PLUGIN._poll(app)                       # None → 유지(재합성 없음)
        assert app.ime_state == "한" and app.composited == 1
        PLUGIN._poll(app)                       # ABC → 'EN'
        assert app.ime_state == "EN" and app.composited == 2
        # 헬퍼 종료 → 드레인 안 함, 직전 상태 유지.
        app._ime_watch._rc = 0
        queue.append(("com.apple.inputmethod.Korean.2SetKorean", b""))
        PLUGIN._poll(app)
        assert app.ime_state == "EN" and app.composited == 2
        assert len(queue) == 1, "헬퍼 종료 후엔 read_latest 를 부르지 않아야 한다"
    finally:
        _oskbd.read_latest = orig_rl
        _oskbd.current_source_id = orig_cur


async def test_read_latest_parses_latest_complete_line_and_carries_partial():
    """read_latest: 가용 바이트를 비차단으로 모두 읽어 **마지막 완성 줄**의 소스 ID 와
    미완성 잔여 버퍼를 돌린다(한 틱에 변경이 여러 줄 쌓여도 최신만, 중간 깜빡임 방지).
    완성 줄이 없으면 (None, 잔여)."""
    import os
    if os.name == "nt":
        return                          # fcntl/비차단 파이프 = POSIX 전용(이 경로=macOS oskbd 감시)
    import fcntl

    r, w = os.pipe()
    fl = fcntl.fcntl(r, fcntl.F_GETFL)
    fcntl.fcntl(r, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    class _Proc:
        stdout = type("S", (), {"fileno": staticmethod(lambda: r)})()

    proc = _Proc()
    try:
        os.write(w, b"com.apple.keylayout.ABC\n"
                    b"com.apple.inputmethod.Korean.2SetKorean\npart")
        sid, buf = _oskbd.read_latest(proc, b"")
        assert sid == "com.apple.inputmethod.Korean.2SetKorean", sid
        assert buf == b"part", buf                      # 미완성 조각 carry
        # 새 데이터 없으면 None + 잔여 유지(완성 줄 없음).
        sid2, buf2 = _oskbd.read_latest(proc, buf)
        assert sid2 is None and buf2 == b"part", (sid2, buf2)
        # 잔여에 이어붙어 완성되면 그 줄을 돌린다.
        os.write(w, b"ner\ncom.apple.keylayout.ABC\n")
        sid3, buf3 = _oskbd.read_latest(proc, buf2)
        assert sid3 == "com.apple.keylayout.ABC" and buf3 == b"", (sid3, buf3)
    finally:
        os.close(r)
        os.close(w)


async def test_client_unload_terminates_watcher():
    """client_unload 가 감시 헬퍼 자식 프로세스를 종료(terminate)하고 핸들을 비운다."""
    class _FakeProc:
        def __init__(self):
            self.terminated = False

        def terminate(self):
            self.terminated = True

    app = _FakeApp()
    proc = _FakeProc()
    app._ime_watch = proc
    PLUGIN.client_unload(app)
    assert proc.terminated is True
    assert app._ime_watch is None
    # 헬퍼가 없으면 no-op(예외 없음).
    PLUGIN.client_unload(app)


# ---- 3.7) §9.1 ssh -R 에이전트 소켓 전송로 ② (원격 정확도 상향) ----
async def test_read_agent_parses_latest_carries_partial_and_eof():
    """read_agent: 소켓에서 비차단 드레인해 (최신 완성 줄, 잔여, closed) 를 돌린다 —
    한 틱에 여러 줄이면 최신만, 미완성은 carry, 피어 close 면 closed=True(폴백 신호)."""
    if os.name == "nt":
        return                          # AF_UNIX 소켓 os.read = POSIX 전용(ssh -R 경로)
    import socket
    s_read, s_write = socket.socketpair(socket.AF_UNIX)
    s_read.setblocking(False)
    try:
        s_write.sendall(b"com.apple.keylayout.ABC\n"
                        b"com.apple.inputmethod.Korean.2SetKorean\npart")
        sid, buf, closed = _oskbd.read_agent(s_read, b"")
        assert sid == "com.apple.inputmethod.Korean.2SetKorean", sid
        assert buf == b"part" and closed is False, (buf, closed)
        sid2, buf2, closed2 = _oskbd.read_agent(s_read, buf)
        assert sid2 is None and buf2 == b"part" and closed2 is False
        s_write.sendall(b"ner\ncom.apple.keylayout.ABC\n")
        sid3, buf3, closed3 = _oskbd.read_agent(s_read, buf2)
        assert sid3 == "com.apple.keylayout.ABC" and buf3 == b"" and closed3 is False
        s_write.close()                 # 피어 종료 → EOF
        _sid4, _buf4, closed4 = _oskbd.read_agent(s_read, buf3)
        assert closed4 is True, "피어 close 면 closed=True 여야(폴백 신호)"
    finally:
        s_read.close()
        try:
            s_write.close()
        except Exception:
            pass


async def test_agent_socket_poll_updates_state_then_falls_back_on_close():
    """_poll 의 소켓 경로: 에이전트 소켓이 붙어 있으면 그 줄로 배지를 갱신하고, 소켓이
    끊기면(_ime_sock=None) 휴리스틱이 재개된다. 소켓이 권위인 동안 client_key 는 무동작."""
    if os.name == "nt":
        return
    import socket
    cli, agent = socket.socketpair(socket.AF_UNIX)
    cli.setblocking(False)
    app = _FakeApp()
    app.ime_state = "EN"
    app._ime_os = False
    app._ime_sock = cli
    app._ime_sock_buf = b""
    try:
        agent.sendall(b"com.apple.inputmethod.Korean.2SetKorean\n")
        PLUGIN._poll(app)
        assert app.ime_state == "한" and app.composited == 1, app.ime_state
        # 소켓이 권위인 동안 영문 입력에도 휴리스틱이 끼어들지 않는다.
        PLUGIN.client_key(app, _Ev("a"))
        assert app.ime_state == "한"
        agent.sendall(b"com.apple.keylayout.ABC\n")
        PLUGIN._poll(app)
        assert app.ime_state == "EN" and app.composited == 2
        # 새 줄 없음 → 유지(재합성 없음).
        PLUGIN._poll(app)
        assert app.ime_state == "EN" and app.composited == 2
        # 에이전트 종료 → 소켓 비움(폴백), 이후 휴리스틱 재개.
        agent.close()
        PLUGIN._poll(app)
        assert app._ime_sock is None, "피어 close 면 _ime_sock 을 비워 폴백해야"
        PLUGIN.client_key(app, _Ev("가"))
        assert app.ime_state == "한", "소켓 폴백 후 휴리스틱이 재개되어야"
    finally:
        try:
            cli.close()
        except Exception:
            pass
        try:
            agent.close()
        except Exception:
            pass


async def test_ssh_remote_attach_connects_agent_and_makes_it_authority():
    """SSH 원격 + PYTMUX_IME_SOCK 가 살아있는 에이전트 소켓을 가리키면 attach_client 가
    연결해 권위로 삼는다(_ime_os=False). 비-ssh 면 경로를 안 잡고 OS 실측을 쓴다."""
    if os.name == "nt":
        return
    import socket
    import tempfile
    saved = {k: os.environ.get(k)
             for k in ("SSH_CONNECTION", "SSH_TTY", "PYTMUX_IME_SOCK")}
    d = tempfile.mkdtemp()
    path = os.path.join(d, "ime.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    srv.setblocking(False)
    os.environ["SSH_CONNECTION"] = "1.2.3.4 5 6.7.8.9 22"
    os.environ.pop("SSH_TTY", None)
    os.environ["PYTMUX_IME_SOCK"] = path
    app = _FakeApp()
    try:
        PLUGIN.attach_client(app)
        assert app._ime_sock is not None, "에이전트 소켓에 연결되어야"
        assert app._ime_os is False, "소켓 권위면 OS 질의 경로는 꺼져야"
        assert app._ime_agent_path == path
        conn, _ = srv.accept()
        conn.sendall(b"com.apple.inputmethod.Korean.2SetKorean\n")
        PLUGIN._poll(app)
        assert app.ime_state == "한", app.ime_state
        # 비-ssh 면 PYTMUX_IME_SOCK 가 있어도 소켓을 안 잡는다(OS 실측 경로).
        os.environ.pop("SSH_CONNECTION", None)
        os.environ.pop("SSH_TTY", None)
        orig = _stub_source("com.apple.keylayout.ABC")
        app2 = _FakeApp()
        try:
            PLUGIN.attach_client(app2)
            assert app2._ime_agent_path is None, "비-ssh 면 에이전트 경로를 안 잡아야"
            assert app2._ime_sock is None
            assert app2._ime_os is True, "비-ssh 로컬은 OS 실측 사용"
        finally:
            _oskbd.current_source_id = orig
    finally:
        try:
            srv.close()
        except Exception:
            pass
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(d, ignore_errors=True)


async def test_agent_subprocess_binds_and_accepts_connection():
    """imeagent.py 를 실제 서브프로세스로 띄워 unix 소켓에 바인드·accept 가 도는지 확인
    (전송로 ② 의 서버측 스모크 — bind/listen/accept). 흘리는 한/영 값은 OS 의존이라
    여기선 '연결 성립'만 단언한다(라이브 한/영 왕복은 실 박스 검증)."""
    if os.name == "nt":
        return
    import asyncio
    import socket
    import subprocess
    import sys as _sys
    import tempfile
    agent = importlib.import_module("pytmuxlib.plugins.ime-indicator.imeagent")
    d = tempfile.mkdtemp()
    path = os.path.join(d, "ime.sock")
    proc = subprocess.Popen(
        [_sys.executable, agent.__file__, "--sock", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cli = None
    try:
        for _ in range(150):            # 바인드까지 최대 ~3s 대기
            if os.path.exists(path):
                try:
                    cli = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    cli.settimeout(1.0)
                    cli.connect(path)
                    break
                except OSError:
                    cli = None
            await asyncio.sleep(0.02)
        assert cli is not None, "에이전트 소켓에 연결 실패(bind/accept 미동작)"
        assert proc.poll() is None, "에이전트가 즉시 죽지 않아야"
    finally:
        if cli is not None:
            try:
                cli.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
        import shutil
        shutil.rmtree(d, ignore_errors=True)


# ---- 4) 계약(delete-to-disable) ----
async def test_plugin_discovered_when_loaded():
    reg = plugins.load()
    names = {n for (n, *_rest) in reg.commands}
    assert "ime-indicator" in names, "ime-indicator 플러그인이 로드되지 않음(전제 실패)"


async def test_registry_without_ime_has_no_commands_and_noop_hook():
    found = [p for p in plugins._discover()
             if getattr(p, "name", "") != "ime-indicator"]
    reg = plugins.Registry(found)
    names = {n for (n, *_rest) in reg.commands}
    assert "ime-indicator" not in names
    assert "ime" not in reg.noarg and "ime-indicator" not in reg.noarg
    # client_key 훅이 부재 시 no-op(예외 없음, app=None 도 안전).
    reg.client_key(None, _Ev("가"))


# ---- 5) 코어 on_key 배선(라이브) ----
async def test_core_on_key_updates_ime_state():
    """코어 normal-mode 입력이 plugins.client_key 를 호출해 상태가 갱신되는지.
    §10-B: 실행 환경(macOS 로컬)에선 attach_client 가 OS 실측(_ime_os)을 켜
    휴리스틱이 침묵하므로, 여기선 **폴백 경로를 강제**(_ime_os=False)해 환경
    무관하게 client_key 배선을 검증한다(OS 경로는 6) 절에서 스텁으로 검증)."""
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.4)
            assert app.ime_show is True  # 기본 ON(상태 초깃값은 환경 따라 실측/EN)
            app._ime_os = False          # 폴백 경로 강제(환경 비의존 결정성)
            app.ime_state = "EN"
            app.mode = "normal"
            # 코어 on_key(normal) 가 plugins.client_key 를 부르는지 — 핸들러 직접 호출
            # (Textual 의 _on_key 디스패치는 프레임워크 영역이라 핸들러만 가드한다).
            app.on_key(Key("가", "가"))
            await pilot.pause(0.05)
            assert app.ime_state == "한"
            app.on_key(Key("b", "b"))
            await pilot.pause(0.05)
            assert app.ime_state == "EN"
            # 숫자는 모드 중립 — 'EN' 유지(여기선 변화 없음).
            app.on_key(Key("5", "5"))
            assert app.ime_state == "EN"
            # 배지가 콘텐츠 프레임에 그려졌는지 — **커서가 있는 줄**(2026-06-11 변경,
            # _active_cursor_xy 원천)의 오른쪽 끝. 커서 미상이면 첫 행 폴백.
            cxy = getattr(app, "_active_cursor_xy", None)
            by = cxy[1] if cxy else 0
            rowb = "".join(c[0] for c in app.view._cells[by])
            assert "[EN]" in rowb, (by, rowb)
            # _ime_zone 의 y 도 같은 행을 가리킨다(테두리 강조 예외 소비처 계약).
            assert app._ime_zone and app._ime_zone[2] == by, app._ime_zone
            # 코어 _composite 가 활성 패널 우측 경계를 채우고, 배지는 그 안에(≤경계)
            # 그려진다(2026-06-16 — 활성 패널 우측 끝 배치 배선).
            assert app._active_pane_right is not None
            assert app._ime_zone[1] <= app._active_pane_right, (
                app._ime_zone, app._active_pane_right)
    finally:
        await teardown(srv, task, sock)
