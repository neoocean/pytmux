"""claude-reconstruct 플러그인 회귀 — **투명 VT 재구성 레이어**(설계 ③, 2026-06-29 야간).

이전 두 시도(라이브 패널 오버레이·트랜스크립트 모달 뷰)를 버리고, Claude 의 raw VT
바이트를 패널마다 둔 **모호폭=narrow 고정 shadow 에뮬레이터**에 병렬로 먹여 깨끗한 격자를
만들고, 전송 직전 `server_filter_rows` 로 그 행을 통째 교체한다. 입력은 안 건드린다.
재구성은 **wide 모드(모호폭=2 단말)에서만** 동작한다(narrow 는 깨짐이 없어 이중 feed 만
늘므로 끔) — 그래서 플러그인 훅을 타는 테스트는 `_wide()` 컨텍스트로 감싼다(전역 폭 상태는
테스트마다 복원해 다른 모듈로 새지 않게 한다).

전부 헤드리스 순수 단위(실 pty/claude 불필요). run.py 는 **async** 함수만 수집한다."""
import contextlib
import importlib

import harness  # noqa: F401  (sys.path 주입)

from pytmuxlib import cellwidth
from pytmuxlib.model import Pane

pkg = importlib.import_module("pytmuxlib.plugins.claude-reconstruct")
shadow = importlib.import_module("pytmuxlib.plugins.claude-reconstruct.shadow")
detect = importlib.import_module("pytmuxlib.plugins.claude-reconstruct.detect")
PLUGIN = pkg.PLUGIN


@contextlib.contextmanager
def _wide():
    """모호폭 wide 단말(=재구성 활성 조건)을 켠 채 본문 실행, 끝나면 narrow 로 복원해
    전역 폭 패치가 다른 테스트 모듈로 새지 않게 한다."""
    cellwidth.set_ambiguous_wide(True)
    try:
        yield
    finally:
        cellwidth.set_ambiguous_wide(False)


def _row_text(rows, y):
    return "".join(t for t, _ in rows[y]).rstrip()


# 합성 발산 시퀀스: alt 진입 → clear/home → → ×5(EAW='A') → 절대커서 col6(1-based=col5
# 0-based) → 'X'. Claude 처럼 "모호폭=1칸" 가정으로 흘린 바이트다.
#   narrow: → 가 1칸씩 col0..4, 커서가 col5 → X 가 화살표 바로 뒤 → "→→→→→X" (깨끗).
#   wide  : → 가 2칸씩 col0..9, 그러나 절대커서 col5 는 화살표 한가운데 → X 가 겹쳐 깨짐.
_ARROW = "→".encode()      # → U+2192, East Asian Ambiguous
DIVERGE = (b"\x1b[?1049h\x1b[2J\x1b[H" + _ARROW * 5 + b"\x1b[1;6HX")


# ---- 페이크 ----

class _Pane:
    """server_pty_output/filter_rows 가 읽는 최소 패널(실 Pane 아님). 게이트 속성과
    크기만 갖는다. PLUGIN.pane_init 가 _rc_* 필드를 심는다."""
    def __init__(self, cols=80, rows=24, claude=True, pid=4242, pane_id=1):
        self.cols = cols
        self.rows = rows
        self.id = pane_id
        self.child_pid = pid
        self.dirty = False
        self._last_wrap = []
        if claude is not None:
            self._hdr_claude = claude     # claude-code 가 심는 게이트
            self._claude = "idle" if claude else None


class _Tab:
    def __init__(self, win):
        self.window = win


class _Win:
    def __init__(self, panes):
        self._panes = panes

    def panes(self):
        return list(self._panes)


class _Sess:
    def __init__(self, win):
        self.active_window = win
        self.tabs = [_Tab(win)]


class _Server:
    def __init__(self, sess=None):
        self.sessions = {0: sess} if sess else {}


class _App:
    def __init__(self):
        self.sent = []

    def send_cmd(self, action, **kw):
        self.sent.append((action, kw))


# ---- shadow: 모호폭 발산을 narrow 격자가 막는다(Shadow 직접, _active 무관) ----

async def test_shadow_narrow_renders_clean_ambiguous():
    """narrow shadow 는 Claude 의 모호폭 시퀀스를 깨끗이 재구성한다(→→→→→X)."""
    sh = shadow.Shadow(40, 6)
    sh.feed(DIVERGE)
    rows, _cur = sh.render()
    assert sh.alt_active
    assert _row_text(rows, 0) == "→→→→→X"


async def test_shadow_feed_narrow_regardless_of_global_wide():
    """전역이 wide 여도 shadow.feed 는 narrow 로 먹어 깨끗하다(전역은 그대로 복원)."""
    with _wide():
        sh = shadow.Shadow(40, 6)
        # 청크 경계로 쪼개 먹여도(드레인 슬라이스 모사) 누적 상태가 맞는지 함께 확인.
        for i in range(0, len(DIVERGE), 5):
            sh.feed(DIVERGE[i:i + 5])
        rows, _cur = sh.render()
        assert _row_text(rows, 0) == "→→→→→X"
        assert cellwidth.ambiguous_wide() is True   # feed 후 전역 복원됨


async def test_wide_real_pane_diverges_so_fix_matters():
    """대조군: 같은 바이트를 wide 실 패널에 먹이면 격자가 발산해 결과가 다르다
    (= 이 플러그인이 고치는 실제 깨짐)."""
    with _wide():
        p = Pane(-1, -1, 40, 6)
        p.feed(DIVERGE)
        rows, _ = p.render(True)
        assert _row_text(rows, 0) != "→→→→→X"


async def test_shadow_resize():
    sh = shadow.Shadow(40, 6)
    sh.feed(b"\x1b[?1049hhello")
    sh.resize(80, 10)
    assert sh.cols == 80 and sh.rows == 10


# ---- server_pty_output: Claude 패널만 shadow 에 병렬 feed(wide 모드 한정) ----

async def test_pty_output_creates_and_feeds_shadow_for_claude():
    with _wide():
        p = _Pane(claude=True)
        PLUGIN.pane_init(p)
        PLUGIN.server_pty_output(_Server(), p, DIVERGE)
        assert p._rc_shadow is not None
        assert p._rc_shadow.fed_bytes == len(DIVERGE)
        assert p._rc_shadow.alt_active


async def test_pty_output_skips_non_claude():
    with _wide():
        p = _Pane(claude=False)
        PLUGIN.pane_init(p)
        PLUGIN.server_pty_output(_Server(), p, DIVERGE)
        assert p._rc_shadow is None


async def test_pty_output_inactive_when_narrow():
    """narrow 단말이면(깨짐 없음) shadow 를 안 만든다 — 이중 feed 비용 0."""
    p = _Pane(claude=True)
    PLUGIN.pane_init(p)
    PLUGIN.server_pty_output(_Server(), p, DIVERGE)   # 전역 narrow(기본)
    assert p._rc_shadow is None


async def test_pty_output_disabled_is_noop():
    with _wide():
        p = _Pane(claude=True)
        PLUGIN.pane_init(p)
        PLUGIN.enabled = False
        try:
            PLUGIN.server_pty_output(_Server(), p, DIVERGE)
            assert p._rc_shadow is None
        finally:
            PLUGIN.enabled = True


async def test_pane_reset_and_closing_drop_shadow():
    with _wide():
        p = _Pane(claude=True)
        PLUGIN.pane_init(p)
        PLUGIN.server_pty_output(_Server(), p, DIVERGE)
        assert p._rc_shadow is not None
        PLUGIN.pane_reset(p)
        assert p._rc_shadow is None
        PLUGIN.server_pty_output(_Server(), p, DIVERGE)
        assert p._rc_shadow is not None
        PLUGIN.pane_closing(_Server(), p)
        assert p._rc_shadow is None


# ---- server_filter_rows: 전송 직전 행 교체(게이트) ----

def _orig_rows(cols):
    # 실내용이 있는 원본 행(garbled 가정) — 워밍업 폴백이 안 걸리게 충분히 채운다.
    return [[["x" * cols, {}]] for _ in range(6)]


async def test_filter_rows_swaps_clean_rows_when_claude_alt():
    with _wide():
        p = _Pane(cols=40, rows=6, claude=True)
        PLUGIN.pane_init(p)
        PLUGIN.server_pty_output(_Server(), p, DIVERGE)        # shadow alt + 내용
        out = PLUGIN.server_filter_rows(_Server(), p, _orig_rows(40))
        assert _row_text(out, 0) == "→→→→→X"


async def test_filter_rows_passthrough_when_not_claude():
    with _wide():
        p = _Pane(claude=False)
        PLUGIN.pane_init(p)
        orig = _orig_rows(40)
        assert PLUGIN.server_filter_rows(_Server(), p, orig) is orig


async def test_filter_rows_passthrough_when_no_shadow():
    with _wide():
        p = _Pane(claude=True)
        PLUGIN.pane_init(p)
        orig = _orig_rows(40)
        assert PLUGIN.server_filter_rows(_Server(), p, orig) is orig


async def test_filter_rows_passthrough_when_not_alt():
    """shadow 가 alt 가 아니면(셸 프롬프트 등) 원본 유지."""
    with _wide():
        p = _Pane(claude=True)
        PLUGIN.pane_init(p)
        PLUGIN.server_pty_output(_Server(), p, b"just a shell line\r\n")  # alt 아님
        orig = _orig_rows(80)
        assert PLUGIN.server_filter_rows(_Server(), p, orig) is orig


async def test_filter_rows_warmup_fallback():
    """shadow 가 비었는데(풀리페인트 전) 원본은 실내용이면 원본 유지(라이브 중간 시작)."""
    with _wide():
        p = _Pane(cols=40, rows=6, claude=True)
        PLUGIN.pane_init(p)
        PLUGIN.server_pty_output(_Server(), p, b"\x1b[?1049h")   # alt 진입만, 내용 없음
        orig = _orig_rows(40)
        assert PLUGIN.server_filter_rows(_Server(), p, orig) is orig


async def test_filter_rows_inactive_when_narrow():
    p = _Pane(cols=40, rows=6, claude=True)
    PLUGIN.pane_init(p)
    # wide 에서 shadow 를 만들어 두더라도, narrow 로 돌아오면 행 교체 안 함.
    with _wide():
        PLUGIN.server_pty_output(_Server(), p, DIVERGE)
    orig = _orig_rows(40)
    assert PLUGIN.server_filter_rows(_Server(), p, orig) is orig


async def test_filter_rows_disabled_passthrough():
    with _wide():
        p = _Pane(cols=40, rows=6, claude=True)
        PLUGIN.pane_init(p)
        PLUGIN.server_pty_output(_Server(), p, DIVERGE)
        PLUGIN.enabled = False
        try:
            orig = _orig_rows(40)
            assert PLUGIN.server_filter_rows(_Server(), p, orig) is orig
        finally:
            PLUGIN.enabled = True


async def test_filter_rows_sets_wrap_consistently():
    """행 교체 시 pane._last_wrap 을 shadow 격자 기준으로 맞춘다."""
    with _wide():
        p = _Pane(cols=40, rows=6, claude=True)
        PLUGIN.pane_init(p)
        PLUGIN.server_pty_output(_Server(), p, DIVERGE)
        PLUGIN.server_filter_rows(_Server(), p, _orig_rows(40))
        assert p._last_wrap == list(p._rc_shadow.pane._last_wrap)


# ---- 자기완결 detection(claude-code 부재) ----

async def test_detect_process_tree_injected():
    ps = [(100, 1, "zsh"), (200, 100, "node"), (300, 200, "claude")]
    assert detect.has_claude_descendant(100, ps_list=lambda: ps) is True
    assert detect.has_claude_descendant(999, ps_list=lambda: ps) is False


class _BarePane:
    """claude-code 가 없는 환경의 패널(_hdr_claude 속성 자체가 없음)."""
    def __init__(self, pid=100):
        self.cols = 80
        self.rows = 24
        self.id = 1
        self.child_pid = pid


async def test_self_detect_only_without_claude_code():
    with _wide():
        p = _BarePane()
        PLUGIN.pane_init(p)
        assert pkg._is_claude_pane(p) is False
        # detect 를 강제로 True 로 돌려 server_scan 이 _rc_self 를 켜는지 확인.
        orig = detect.has_claude_descendant
        detect.has_claude_descendant = lambda pid, **k: True
        try:
            p._rc_detect_ts = 0.0
            win = _Win([p])
            PLUGIN.server_scan(_Server(), _Sess(win), win)
            assert p._rc_self is True
            assert pkg._is_claude_pane(p) is True
        finally:
            detect.has_claude_descendant = orig


async def test_scan_skips_ps_when_claude_code_present():
    """claude-code 가 있으면(_hdr_claude 보유) server_scan 은 detect 를 안 부른다."""
    with _wide():
        p = _Pane(claude=True)
        PLUGIN.pane_init(p)
        called = []
        orig = detect.has_claude_descendant
        detect.has_claude_descendant = lambda pid, **k: called.append(pid) or True
        try:
            win = _Win([p])
            PLUGIN.server_scan(_Server(), _Sess(win), win)
            assert called == []     # _hdr_claude 보유 패널은 ps 불필요
        finally:
            detect.has_claude_descendant = orig


# ---- 명령 토글 배선 ----

async def test_command_sends_toggle():
    app = _App()
    assert PLUGIN.handle_command(app, "claude-rc", "") is True
    assert PLUGIN.handle_command(app, "claude-reconstruct", "") is True
    assert app.sent == [("rc_toggle", {}), ("rc_toggle", {})]
    assert PLUGIN.handle_command(app, "other-cmd", "") is False


async def test_server_command_toggles_enabled():
    p = _Pane(claude=True)
    sess = _Sess(_Win([p]))
    srv = _Server(sess)
    before = PLUGIN.enabled
    try:
        r = PLUGIN.server_command(srv, None, sess, "rc_toggle", {})
        assert r == "broadcast"
        assert PLUGIN.enabled is (not before)
        assert p.dirty is True       # 토글 시 재전송 위해 dirty
        assert PLUGIN.server_command(srv, None, sess, "other", {}) is None
    finally:
        PLUGIN.enabled = before
