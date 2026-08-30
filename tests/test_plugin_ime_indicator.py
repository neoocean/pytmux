"""ime-indicator 플러그인 회귀 — 한/영 추정 상태 전이, 배지 그리기, 명령 토글, 계약.

설계 배경(docs/internal/IME_PREEDIT_CURSOR_SCENARIO.md): 앱은 OS IME 의 *조합 중* preedit 을
관찰할 수 없고 **확정된 글자만** 키 이벤트로 받는다. 그래서 한/영은 패널로 보낼 확정
입력 문자의 스크립트로 추정한다 — 한글→'한', ASCII 글자→'EN', 숫자/기호는 모드 중립.

자리 규칙(`cells.py`)은 앱 비의존 순수 함수라 앱·소켓 없이 직접 호출해 단언한다.
client_key/handle_command 는 가짜 app 으로, 코어 on_key 배선은 라이브 앱으로 가드한다.
계약(delete-to-disable): 플러그인을 Registry 에서 빼면 ime 명령/훅이 전부 사라진다.
"""
import os
import re

import harness  # noqa: F401  (sys.path 주입)
from harness import make_app, server_only, teardown, wait_until
from run import skip
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

_pkg = importlib.import_module("pytmuxlib.plugins.ime-indicator")
PLUGIN = _pkg.PLUGIN


class _FakeApp:
    """client_key/handle_command 가 닿는 최소 표면만 흉내낸 가짜 앱."""
    def __init__(self):
        self.ime_show = True
        self.ime_state = "EN"
        self.composited = 0
        self.messages = []
        self.sent = []            # (액션, 인자) — 서버로 나간 것

    def _composite(self):
        self.composited += 1

    def display_message(self, m):
        self.messages.append(m)

    def send_cmd(self, action, **kw):
        self.sent.append((action, kw))


class _Ev:
    def __init__(self, character):
        self.character = character


# ---- 1) 자리 규칙(순수) ----
#
# 2026-08-02i(P7): 그리기 함수 `draw_ime_indicator` 는 **지웠다** — 정본이 이제 런
# 생성기(`cells.py`) + 공통 소비자(`clientrender.paint_runs`)를 쓰기 때문이다. 아래는
# 그 함수가 지키던 규칙을 **자리 셈 쪽에서** 그대로 잰다. 규칙을 지운 것이 아니라
# 사는 곳이 바뀌었다.

_cells = importlib.import_module("pytmuxlib.plugins.ime-indicator.cells")


async def test_badge_is_right_aligned_and_counts_wide_chars():
    """`[한]` 은 **네 칸**이다(한글 2칸) — 폭을 글자 수로 세면 자리가 어긋난다."""
    assert _cells.text_width("[한]") == 4
    assert _cells.text_width("[EN]") == 4
    # 오른쪽 경계(exclusive) 40 에 우측정렬 → 36..40.
    assert _cells.badge_span("한", 40, 0) == (36, 40)
    assert _cells.badge_span("EN", 40, 0) == (36, 40)


async def test_badge_skipped_when_too_narrow():
    """폭이 배지를 못 담으면 아무것도 안 그린다 — 화면을 덮어 가며 알릴 것은 아니다."""
    assert _cells.badge_span("한", 3, 0) is None
    assert _cells.ime_cells("한", 0, 3) == ([], None)


async def test_badge_leaves_room_for_the_tab_close_button():
    """탭 닫기 `[x]` 와 **같은 행**이면 우측 4칸을 비운다 — [x] 가 뒤에 그려져 배지를
    덮기 때문이다. 다른 행에는 [x] 가 없어 진짜 끝까지 쓴다."""
    assert _cells.badge_span("EN", 40, _cells.RESERVE_FOR_TAB_CLOSE) == (32, 36)
    assert _cells.badge_span("EN", 40, 0) == (36, 40)


async def test_badge_at_active_pane_right_edge():
    """2026-06-16 요청: 좌우 분할에서 활성 패널이 화면 왼쪽 절반이면 배지는 화면
    오른쪽 끝이 아니라 **활성 패널의 오른쪽 끝**에 붙는다 — 비활성 패널 위에 뜨면
    어느 패널의 상태인지 헷갈린다."""
    runs, span = _cells.ime_cells("EN", 2, 40)      # 활성 패널 우측 경계 = 40
    assert span == (36, 40), span
    assert runs[0]["x"] == 36 and runs[0]["y"] == 2, runs
    # 화면이 80칸이어도 경계를 넘지 않는다(경계를 안 주면 호출부가 화면 폭을 준다).
    assert _cells.ime_cells("EN", 2, 80)[1] == (76, 80)


async def test_no_badge_for_a_row_that_does_not_exist():
    """행이 음수면 생략 — 그리는 쪽이 격자 밖을 만지지 않게 여기서 막는다."""
    assert _cells.ime_cells("EN", -1, 40) == ([], None)


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
    # ★ **서버로도 나간다**(pytmux-35): 표시 여부는 서버 옵션이라 이 명령이 서버에
    #   닿지 않으면 네이티브 클라의 배지(서버가 `plugin_cells` 로 그린다)는 안 꺼지고,
    #   껐다 켠 것이 재시작에서 사라진다. 낙관적 로컬 반영은 위에서 이미 쟀다.
    assert [a for a, _kw in app.sent] == ["set_ime_indicator"] * 2


async def test_toggle_command_carries_the_explicit_value():
    """`on`/`off` 는 **그대로** 실린다 — 무인자만 서버가 뒤집는다(값 `None`)."""
    app = _FakeApp()
    PLUGIN.handle_command(app, "ime-indicator", ["off"])
    assert app.sent[-1] == ("set_ime_indicator", {"value": False})
    assert app.ime_show is False
    PLUGIN.handle_command(app, "ime-indicator", ["on"])
    assert app.sent[-1] == ("set_ime_indicator", {"value": True})
    assert app.ime_show is True
    PLUGIN.handle_command(app, "ime-indicator", [])
    assert app.sent[-1] == ("set_ime_indicator", {"value": None})


class _FakeServer:
    def __init__(self, show=True):
        self.ime_show = show
        self.saved = 0

    def _save_opts(self):
        self.saved += 1


async def test_server_owns_whether_the_badge_shows():
    """값이 서버에 있어야 **두 클라가 같은 상태**를 본다(pytmux-35).

    종전에는 이 값이 정본 클라의 인스턴스 속성뿐이라, 네이티브 클라의 배지는 서버가
    그리는데 끌 방법이 아예 없었다 — 팔레트에 보이는데 안 먹는 줄."""
    server = _FakeServer()
    # 무인자 = 뒤집기, 명시값 = 그 값. 바뀌면 영속하고 모든 클라에 알린다.
    assert PLUGIN.server_command(server, None, None, "set_ime_indicator", {}) == "broadcast"
    assert server.ime_show is False and server.saved == 1
    PLUGIN.server_command(server, None, None, "set_ime_indicator", {"value": True})
    assert server.ime_show is True
    # 내 액션이 아니면 안 집는다(코어가 다음 소비자에게 넘긴다).
    assert PLUGIN.server_command(server, None, None, "set_capture", {}) is None


async def test_status_and_opts_carry_the_flag():
    server = _FakeServer(show=False)
    assert PLUGIN.server_opts_serialize(server) == {"ime_show": False}
    # 정적 옵션이라 **full 일 때만** 싣는다(C4) — 주기 프레임에 없다고 꺼진 게 아니다.
    msg = {}
    PLUGIN.server_status(server, None, None, msg, False)
    assert "ime_show" not in msg
    PLUGIN.server_status(server, None, None, msg, True)
    assert msg["ime_show"] is False
    # opts 왕복: plugin_opts 네임스페이스, 없으면 기본 ON(종전 동작).
    fresh = _FakeServer()
    PLUGIN.server_opts_init(fresh, {"plugin_opts": {"ime_show": False}})
    assert fresh.ime_show is False
    PLUGIN.server_opts_init(fresh, {})
    assert fresh.ime_show is True


async def test_cells_are_silent_when_the_badge_is_off():
    """꺼 두면 **그림 자체가 안 나간다** — 클라가 걸러 주기를 기대하지 않는다."""
    server = _FakeServer(show=False)
    req = {"facts": {"ime": "한"}, "active": 1,
           "panes": [{"id": 1, "x": 0, "y": 0, "w": 40, "h": 10}]}
    assert PLUGIN.plugin_cells(server, None, req) == []
    server.ime_show = True
    # 켜면 실제로 런이 나온다(부정 단언만 있는 오라클은 배선이 빠져도 통과한다).
    assert PLUGIN.plugin_cells(server, _NoSession(), req)


class _NoSession:
    """`_cursor_row` 가 화면 모델을 못 읽는 경우 — 조용히 폴백해야 한다."""
    active_window = None


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


async def test_the_two_clients_poll_the_ime_at_the_same_rate():
    """정본과 Rust GUI 의 한/영 폴링 주기가 **갈리지 않는지**(pytmux-378).

    왜 여기서 재나: 이 값은 두 클라가 같은 사실을 같은 속도로 말해야 하는 자리다. 갈리면
    사용자가 두 창을 나란히 놓는 순간 그 갈림이 곧 제보가 된다 — 실제로 300ms 대 50ms
    (6배)가 그렇게 들어왔다. 어느 한쪽만 고치면 다음에 같은 제보가 반대 방향으로 온다.

    ⛔ 값을 여기 박아 두지 않는다. **두 파일에서 읽어 서로 대조**한다 — 상수를 세 곳에
    두면 그 셋이 갈릴 뿐이다.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    canon = os.path.join(root, "pytmuxlib", "plugins", "ime-indicator", "__init__.py")
    with open(canon, encoding="utf-8") as fh:
        canon_src = fh.read()
    # 정본은 `si = getattr(app, "set_interval", None)` 로 한 번 감싼 뒤 부른다 —
    # 그래서 이름이 아니라 **타이머를 담는 자리**(`_ime_os_timer`)를 앵커로 삼는다.
    m = re.search(r"_ime_os_timer\s*=\s*\(?\s*si\(\s*([0-9.]+)\s*,", canon_src)
    assert m, "정본의 폴링 타이머를 못 찾았다 — 이 가드가 공허해졌다"
    canon_ms = round(float(m.group(1)) * 1000)

    gui = os.path.join(root, "client", "crates", "gui", "src", "session_view.rs")
    if not os.path.isfile(gui):
        skip("client/ 가 없다 — Rust 클라 트리 없이 정본만 있는 판")
    with open(gui, encoding="utf-8") as fh:
        gui_src = fh.read()
    m2 = re.search(r"IME_PERIOD:\s*std::time::Duration\s*=\s*"
                   r"std::time::Duration::from_millis\((\d+)\)", gui_src)
    assert m2, "GUI 의 IME_PERIOD 를 못 찾았다 — 이름이 바뀌었으면 이 가드도 옮길 것"
    gui_ms = int(m2.group(1))

    assert gui_ms == canon_ms, (
        f"두 클라의 한/영 폴링 주기가 갈렸다 — 정본 {canon_ms}ms · GUI {gui_ms}ms"
    )


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
            await wait_until(pilot, lambda: app.ime_state == "한")
            assert app.ime_state == "한"
            app.on_key(Key("b", "b"))
            await wait_until(pilot, lambda: app.ime_state == "EN")
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


async def test_badge_stays_at_prompt_when_cursor_hidden_in_split():
    """요청 2026-06-21: 좌우 분할에서 왼쪽(활성) 패널에 타이핑 중 Claude 가 '생각 중'
    커서를 숨기면(_active_cursor_xy=None) 배지가 화면 맨 위(y=0)로 튀지 않고, 직전
    커서 행(프롬프트)에 머문다. 직전 행도 없으면 활성 패널 하단으로 떨어진다. 어느
    경우든 x 는 활성(왼쪽) 패널 우측 경계 안."""
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            app.ime_show = True
            app.ime_state = "한"
            app._ime_os = False
            W = app.layout.get("cols", 80)
            H = app.layout.get("rows", 23)
            # 좌(active, 우측 경계 39) | 우 2패널, 콘텐츠는 테두리 안쪽.
            app.layout = {"cols": W, "rows": H, "active": 1, "panes": [
                {"id": 1, "x": 1, "y": 1, "w": 37, "h": H - 2, "box": [0, 0, 39, H]},
                {"id": 2, "x": 40, "y": 1, "w": 38, "h": H - 2,
                 "box": [39, 0, 41, H]}]}
            rows_a = [[("x", {})] for _ in range(H - 2)]
            ccy = H - 6
            prompt_gy = 1 + ccy            # p["y"] + ccy
            # 1) 커서 보임 → 배지가 그 행, 활성 패널 우측 경계 안.
            app.pane_content = {1: (rows_a, (5, ccy)),
                                2: ([[("y", {})] for _ in range(H - 2)], None)}
            app._composite()
            await wait_until(pilot, lambda: app._ime_zone and app._ime_zone[2] == prompt_gy)
            assert app._ime_zone and app._ime_zone[2] == prompt_gy, app._ime_zone
            assert app._ime_zone[1] <= app._active_pane_right
            # 2) 커서 숨김(같은 활성 패널) → 직전 프롬프트 행 유지(맨 위 0 아님).
            app.pane_content[1] = (rows_a, None)
            app._composite()
            await pilot.pause(0.05)
            assert app._active_cursor_xy is None
            assert app._ime_zone[2] == prompt_gy, ("프롬프트 행 유지", app._ime_zone)
            assert app._ime_zone[2] != 0
            assert app._ime_zone[1] <= app._active_pane_right
            # 3) 직전 커서 이력이 없는 새 활성 패널 → 활성 패널 하단 행 폴백(여전히 0 아님).
            app._ime_last_cursor = None
            app.layout["active"] = 2
            app._composite()
            await pilot.pause(0.05)
            box = app._active_pane_box
            assert app._ime_zone[2] == box[1] + box[3] - 1, app._ime_zone
            assert app._ime_zone[2] != 0
    finally:
        await teardown(srv, task, sock)


# ---- 4) ssh -R IME 채널 하드닝(M2/M3, SECURITY_REVIEW §8) ----
import shutil          # noqa: E402
import socket          # noqa: E402
import tempfile        # noqa: E402

_imeagent = importlib.import_module("pytmuxlib.plugins.ime-indicator.imeagent")


async def test_imeagent_unlink_stale_guards_squat():
    # M2: 우리 소유 소켓만 stale 로 제거. 일반 파일/타 소유/심링크는 거부(선점 방지).
    if os.name == "nt":
        return
    d = tempfile.mkdtemp(prefix="pytmux-imesock-")
    try:
        assert _imeagent._unlink_stale(os.path.join(d, "nope.sock")) is True
        reg = os.path.join(d, "reg")          # 소켓 아님 → 거부, 보존
        open(reg, "w").close()
        assert _imeagent._unlink_stale(reg) is False
        assert os.path.exists(reg)
        sp = os.path.join(d, "s.sock")        # 우리 소유 소켓 → 제거
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(sp)
        try:
            assert _imeagent._unlink_stale(sp) is True
            assert not os.path.exists(sp)
        finally:
            s.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_oskbd_drain_caps_runaway_buffer():
    # M3: 개행 없는 폭주 입력은 _LINE_MAX 로 잘려 소비측 메모리가 무한 증가하지 않는다.
    # oskbd 드레인은 macOS(TIS) 워처 전용 Unix 파이프 경로 — Windows 는 IMM32 라 미사용,
    # 게다가 os.set_blocking 이 Windows<3.12 엔 없다. nt 에선 스킵.
    if os.name == "nt":
        return
    cap = _oskbd._LINE_MAX
    r, w = os.pipe()
    os.set_blocking(r, False)
    try:
        os.write(w, b"a" * 60000)             # 파이프 용량 내(개행 없음)
        sid, buf, _closed = _oskbd._drain(r, b"")
        assert sid is None and len(buf) == 60000
        os.write(w, b"b" * 60000)             # 누적 120000 → cap 으로 잘림
        sid, buf, _closed = _oskbd._drain(r, buf)
        assert sid is None
        assert len(buf) <= cap, len(buf)
    finally:
        os.close(r)
        os.close(w)


async def test_oskbd_drain_parses_line_after_newline():
    # 정상 한 줄(개행 종결)은 그대로 디코드된다(캡 도입이 정상 경로를 깨지 않음).
    if os.name == "nt":            # oskbd 드레인=macOS 전용·os.set_blocking Win<3.12 부재
        return
    r, w = os.pipe()
    os.set_blocking(r, False)
    try:
        os.write(w, b"com.apple.keylayout.ABC\n")
        sid, buf, _closed = _oskbd._drain(r, b"")
        assert sid == "com.apple.keylayout.ABC" and buf == b""
    finally:
        os.close(r)
        os.close(w)


# ── Tier D: 클라가 사실을 올리고, 그림은 플러그인이 낸다 (P7 · 2026-08-02i) ──

def _ime():
    import importlib
    return importlib.import_module("pytmuxlib.plugins.ime-indicator").PLUGIN


def _cells_mod():
    import importlib
    return importlib.import_module("pytmuxlib.plugins.ime-indicator.cells")


_PANE = {"id": 1, "x": 0, "y": 0, "w": 40, "h": 10}


async def test_the_badge_is_not_drawn_until_a_client_reports_the_fact():
    """**한/영은 서버가 모른다.** OS 가 클라 창에만 알려 주는 사실이라(설계 ⑤),
    클라가 `client_fact` 로 올리기 전에는 그릴 것이 없다 — 빈 목록이라야 서버가
    프레임을 안 만든다(끄는 것도 프레임: 빈 런이 지우개다)."""
    req = {"panes": [_PANE], "active": 1, "facts": {}}
    assert _ime().plugin_cells(None, None, req) == []
    # 올렸다 지우면(값 없음) 다시 빈 목록.
    assert _ime().plugin_cells(None, None,
                               {**req, "facts": {"ime": ""}}) == []


async def test_the_reported_fact_becomes_a_run_with_a_semantic_colour():
    """올라온 사실이 그대로 런이 된다 — 색은 **이름**이고(hex 금지) 글자는 `[한]` 꼴."""
    runs = _ime().plugin_cells(
        None, None, {"panes": [_PANE], "active": 1, "facts": {"ime": "한"}})
    assert len(runs) == 1, runs
    run = runs[0]
    assert run["text"] == "[한]", run
    assert run["theme"] == {"b": "success"}, run       # 한글 = 강조색
    for v in run["theme"].values():
        assert not v.startswith("#"), f"hex 가 실렸다: {run}"
    # 활성 패널의 **오른쪽 끝**에 붙는다(정본 규칙). '한' 은 두 칸이라 `[한]` = 4칸 —
    # 폭을 글자 수로 세면 여기서 어긋난다(와이드 문자 계산이 규칙의 일부다).
    assert run["x"] + _cells_mod().text_width(run["text"])         == _PANE["x"] + _PANE["w"], run
    # 영문은 다른 이름을 쓴다 — 두 상태가 같은 색이면 배지가 아무 말도 안 한다.
    en = _ime().plugin_cells(
        None, None, {"panes": [_PANE], "active": 1, "facts": {"ime": "EN"}})
    assert en[0]["theme"] == {"b": "primary"}, en


async def test_the_row_rule_is_one_copy_and_prefers_the_cursor():
    """자리 규칙(`badge_row`)이 정본과 **한 벌**이라야 한다 — 세 갈래를 직접 잰다.

    이 규칙이 갈려 있던 것이 P7 의 동기다: 네이티브가 늘 첫 행에 그렸고 정본은 커서
    줄에 그렸다."""
    badge_row = _cells_mod().badge_row
    box = (0, 0, 40, 10)
    assert badge_row((5, 7), None, box) == 7, "커서가 보이면 그 행"
    assert badge_row(None, 4, box) == 4, "숨겨졌으면 직전 커서 행"
    assert badge_row(None, None, box) == 9, "둘 다 없으면 패널 마지막 내용 행"
    assert badge_row(None, None, None) == 0


async def test_a_pane_too_narrow_gets_no_badge():
    """좁으면 안 그린다 — 화면을 덮어 가며 알릴 만한 것은 아니다."""
    narrow = {"id": 1, "x": 0, "y": 0, "w": 3, "h": 3}
    assert _ime().plugin_cells(
        None, None,
        {"panes": [narrow], "active": 1, "facts": {"ime": "EN"}}) == []


async def test_the_fact_rides_the_connection_not_the_session():
    """사실은 **그 연결의 것**이다 — 두 사람이 같은 세션을 봐도 서로의 한/영이 안 섞인다.

    오버레이(`plugin_overlay`)와 같은 자리(`ClientConn.plugin_state`)를 쓴다."""
    from harness import server_only, teardown
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        from pytmuxlib.servercmd import _CMD_TABLE
        handler = _CMD_TABLE["client_fact"][0]

        class _C:
            def __init__(self):
                self.plugin_state = {}
                self._cells_at = 1.0

        a, b = _C(), _C()
        await handler(srv, a, sess, {"name": "ime", "value": "한"})
        assert a.plugin_state["facts"] == {"ime": "한"}
        assert b.plugin_state == {}, "남의 연결에 새어 나갔다"
        assert a._cells_at == 0.0, "지금 다시 그리라고 안 했다"
        # 값이 비면 지운다(끄는 것도 사실이다) — 키까지 정리한다.
        await handler(srv, a, sess, {"name": "ime", "value": None})
        assert "facts" not in a.plugin_state, a.plugin_state
    finally:
        await teardown(srv, task, sock)


# ---- 8) 판이 열리면 배지는 **그 판의 입력줄**로 간다 (pytmux-14) ----------------
#
# 캔버스 배지는 활성 패널의 커서 줄에 그려지는데, 판이 열리면 커서는 판 안 입력줄로
# 가고 그 배지는 판 **뒤**에 깔려 안 보인다. 제보가 본 그림이 그것이다.
# 자리 규칙 한 문장은 그대로다 — **지금 글자를 받는 곳의 오른쪽 끝**.

async def test_the_badge_moves_into_every_screen_that_takes_typing():
    """글자를 받는 판 넷이 모두 배지를 단다 — 하나만 빠져도 그 판에서 조용히 사라진다."""
    from textual.widgets import Label as _Label
    from pytmuxlib.clientscreens import IME_BADGE_ID

    def _badge(app):
        """맨 위 판의 배지 글자(자리가 없으면 None — 아직 안 떴거나 대상이 아닌 판)."""
        try:
            return str(app.screen_stack[-1].query_one("#" + IME_BADGE_ID, _Label).content)
        except Exception:
            return None

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 30)) as pilot:
            await wait_until(pilot, lambda: getattr(app, "layout", None) is not None)
            app._ime_os = False          # OS 실측이 수동 설정을 덮지 않게(폴백 강제)
            app._ime_sock = None
            app.ime_show = True
            app.ime_state = "한"
            # 판을 여는 길은 판마다 다르다 — 각각 그 판이 실제로 열리는 길로 연다.
            from pytmuxlib.clientscreens import (CommandListScreen, PromptScreen,
                                                 _SettingInputScreen)
            opens = [
                ("팔레트", lambda: CommandListScreen([("split", "설명", "창")])),
                ("물음", lambda: PromptScreen("rename", "붙을 상자 (host)", "", None)),
                ("설정 입력", lambda: _SettingInputScreen("prefix", "C-b")),
                ("작성창", None),        # 여는 길이 따로다(블록 선택 편집기)
            ]
            for name, mk in opens:
                if mk is None:
                    app.open_compose()
                else:
                    app.push_screen(mk())
                # 배지는 0.2초 폴링으로 채워진다 — 고정 대기 대신 **값이 올 때까지** 본다.
                ok = await wait_until(pilot, lambda: "한" in (_badge(app) or ""))
                assert ok, (name, _badge(app))
                app.pop_screen()
                await wait_until(pilot, lambda: len(app.screen_stack) == 1)
    finally:
        await teardown(srv, task, sock)


async def test_the_screen_badge_dies_with_the_plugin():
    """delete-to-disable — 플러그인이 없으면 판에도 배지가 **아예 안 뜬다**.

    판이 문구를 갖고 있으면 이 단언이 죽는다(종전 작성창이 그랬다: `[{state}]` 와
    색 규칙이 `clientscreens.py` 안에 있었다)."""
    found = [p for p in plugins._discover()
             if getattr(p, "name", "") != "ime-indicator"]
    reg = plugins.Registry(found)
    assert reg.client_input_badge(None) is None, "플러그인이 없는데 배지가 나왔다"


async def test_the_screen_badge_and_the_canvas_badge_say_the_same_thing():
    """판과 캔버스가 **같은 문구·같은 의미색**을 쓴다 — 규칙이 두 벌이면 갈린다."""

    class _App:
        ime_show = True
        ime_state = "한"

    text, theme = PLUGIN.client_input_badge(_App())
    assert text == _cells.badge_text("한"), (text, _cells.badge_text("한"))
    assert theme == _cells.badge_theme("한") == "success", (theme,)
    # 캔버스 런도 같은 재료에서 나온다.
    runs, _ = _cells.ime_cells("한", 0, 40)
    assert runs[0]["text"] == text, (runs[0]["text"], text)
    assert runs[0]["theme"]["b"] == theme, (runs[0]["theme"], theme)
