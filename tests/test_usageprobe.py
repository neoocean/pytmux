"""그림자 /usage 프로브(usageprobe.query_usage) 단위 테스트.

실 `claude` 를 띄우지 않고 `_open_session` 팩토리를 가짜 세션으로 몽키패치해
부팅→`/usage` 입력→패널 스크랩→parse_usage 의 전 흐름을 결정적으로 검증한다.
플랫폼 무관(POSIX/Windows 공통 경로) — 백엔드 I/O 는 가짜로 대체한다."""
import os
import time

import harness  # noqa: F401  (경로 설정)
from pytmuxlib import usageprobe

_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "claude", "usage.txt")


def _panel_bytes() -> bytes:
    """실 /usage 캡처 fixture 를 pyte 가 행으로 렌더하도록 clear+home 후 \\r\\n 행으로."""
    with open(_FIX, encoding="utf-8") as f:
        text = f.read()
    body = text.replace("\n", "\r\n")
    return b"\x1b[2J\x1b[H" + body.encode("utf-8")


class _FakeSession:
    """캔드 세션: 처음엔 boot 화면을 내주고, `/usage` 입력을 받으면 패널을,
    `/status` 입력을 받으면 status 화면(있으면)을 내준다."""

    def __init__(self, boot: bytes, panel: bytes | None,
                 status: bytes | None = None):
        self._queue = [boot]
        self._panel = panel
        self._status = status
        self.killed = False
        self.closed = False
        self.written = b""

    def read(self, timeout: float) -> bytes:
        if self._queue:
            return self._queue.pop(0)
        time.sleep(min(timeout, 0.02))   # 빈 구간은 짧게 쉬어 busy-spin 방지
        return b""

    def write(self, data: bytes) -> None:
        self.written += data
        if b"/usage" in data and self._panel is not None:
            self._queue.append(self._panel)
        if b"/status" in data and self._status is not None:
            self._queue.append(self._status)

    def kill(self) -> None:
        self.killed = True

    def close(self) -> None:
        self.closed = True


def _patch(monkeyholder, session):
    """usageprobe._open_session 를 session 반환으로 교체하고 원복 함수를 반환."""
    orig = usageprobe._open_session
    usageprobe._open_session = lambda *a, **k: session
    monkeyholder.append(lambda: setattr(usageprobe, "_open_session", orig))


async def test_query_usage_parses_real_panel_fixture():
    boot = b"\x1b[2J\x1b[H Welcome to Claude\r\n ? for shortcuts\r\n"
    sess = _FakeSession(boot, _panel_bytes())
    undo = []
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(
            cmd="claude", boot_timeout=2.0, panel_timeout=2.0)
    finally:
        for f in undo:
            f()
    assert u is not None, "패널을 스크랩하면 dict 를 돌려야 한다"
    # fixture usage.txt: session 2% / week(all) 14% / week(sonnet) 0%
    assert u["session"]["pct"] == 2, u
    assert u["week_all"]["pct"] == 14, u
    assert u["week_sonnet"]["pct"] == 0, u
    assert "Asia/Seoul" in (u["session"]["reset"] or "")
    # /usage\r 가 정확히 한 번 주입됐는지
    assert sess.written.count(b"/usage\r") == 1, sess.written
    # 끝나면 세션을 정리(kill+close)
    assert sess.killed and sess.closed


async def test_query_usage_boot_sentinel_new_claude_footer():
    # claude v2.1.x 는 "? for shortcuts" 대신 입력박스 푸터를 띄운다 — 그 신호로도
    # 부팅 준비를 인식해야 한다(센티넬 회귀 방지).
    boot = (b"\x1b[2J\x1b[H Claude Code v2.1.172\r\n"
            b" auto mode on (shift+tab to cycle) \xe2\x86\x90 for agents\r\n")
    sess = _FakeSession(boot, _panel_bytes())
    undo = []
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(boot_timeout=2.0, panel_timeout=2.0)
    finally:
        for f in undo:
            f()
    assert u is not None, "v2.1.x 푸터로도 부팅을 인식해야 한다"
    assert u["session"]["pct"] == 2, u


async def test_query_usage_dismisses_managed_settings_screen():
    """조직 관리 설정 승인 화면("Managed settings require approval")이 뜨면 기본
    선택("1. Yes, I trust these settings")을 확정하는 Enter 를 1회 보내고 정상
    부팅 신호 대기를 이어가야 한다(2026-07-15 요청) — 이전엔 boot_timeout 으로
    조용히 실패(None)했다."""
    managed = (b"\x1b[2J\x1b[H Managed settings require approval\r\n"
               b" \xe2\x9d\xaf 1. Yes, I trust these settings\r\n"
               b"   2. No, exit Claude Code\r\n"
               b" Enter to confirm \xc2\xb7 Esc to exit\r\n")
    ready = b"\x1b[2J\x1b[H Welcome to Claude\r\n ? for shortcuts\r\n"

    class _ManagedSettingsSession(_FakeSession):
        def write(self, data: bytes) -> None:
            if data == b"\r" and not self.written:
                self._queue.append(ready)   # 승인 후에야 정상 부팅 화면이 뜬다
            super().write(data)

    sess = _ManagedSettingsSession(managed, _panel_bytes())
    undo = []
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(boot_timeout=2.0, panel_timeout=2.0)
    finally:
        for f in undo:
            f()
    assert u is not None, "관리 설정 승인 화면을 자동 통과해 정상 스크랩해야 한다"
    assert u["session"]["pct"] == 2, u
    assert sess.written.startswith(b"\r"), "기본 선택 확정 Enter 를 먼저 보내야 한다"


def test_managed_yes_selected_requires_affirmative_default():
    """SEC-1: _managed_yes_selected 는 ❯/> 셀렉터가 **'Yes, I trust these settings'
    줄에 있을 때만** True. 다른 옵션(No, exit)에 있거나 문구가 바뀌면 False —
    무턱대고 Enter 를 쳐 미지의 선택을 확정하지 않게 한다.

    pytmux-151 이후 **미결 옵션('No, exit Claude Code')이 함께 보일 때만** True 다 —
    통과된 승인 화면의 잔상(옵션 줄이 다음 프레임에 덮인 상태)을 승인 대기로 세지
    않기 위해서다. 프로브도 패널 스캔과 같은 함수를 쓰므로 같은 계약이다."""
    yes_sel = (" Managed settings require approval\n"
               " ❯ 1. Yes, I trust these settings\n"
               "   2. No, exit Claude Code\n")
    no_sel = (" Managed settings require approval\n"
              "   1. Yes, I trust these settings\n"
              " ❯ 2. No, exit Claude Code\n")
    gt_sel = (" Managed settings require approval\n"
              " > Yes, I trust these settings\n"
              "   No, exit Claude Code\n")
    # 승인이 끝나 옵션 줄이 덮인 잔상(응답 대기 아님) — 여기서 True 면 프로브·패널
    # 모두 다음 인스턴스를 못 알아본다(pytmux-151).
    leftover = (" Managed settings require approval\n"
                " ❯ 1. Yes, I trust these settings\n"
                " Resume this session with:\n")
    reworded = (" Managed settings require approval\n"
                " ❯ 1. Accept and continue\n")
    unrelated = " ? for shortcuts\n"
    assert usageprobe._managed_yes_selected(yes_sel) is True
    assert usageprobe._managed_yes_selected(gt_sel) is True
    assert usageprobe._managed_yes_selected(no_sel) is False
    assert usageprobe._managed_yes_selected(leftover) is False
    assert usageprobe._managed_yes_selected(reworded) is False
    assert usageprobe._managed_yes_selected(unrelated) is False


async def test_query_usage_managed_settings_no_enter_when_not_affirmative():
    """SEC-1: 관리설정 화면이 떠도 긍정 기본선택(❯ Yes)이 아니면 Enter 를 치지
    않고 프로브는 안전하게 실패(None)해야 한다 — 향후 빌드가 기본을 'No, exit' 로
    두거나 옵션을 재배열해도 미지의 선택을 자동확정하지 않는다."""
    managed_no = (b"\x1b[2J\x1b[H Managed settings require approval\r\n"
                  b"   1. Yes, I trust these settings\r\n"
                  b" \xe2\x9d\xaf 2. No, exit Claude Code\r\n"
                  b" Enter to confirm \xc2\xb7 Esc to exit\r\n")
    ready = b"\x1b[2J\x1b[H Welcome to Claude\r\n ? for shortcuts\r\n"

    class _NoDefaultSession(_FakeSession):
        def write(self, data: bytes) -> None:
            if data == b"\r" and not self.written:
                self._queue.append(ready)   # (있으면) 승인 뒤 뜰 화면 — 오면 안 됨
            super().write(data)

    sess = _NoDefaultSession(managed_no, _panel_bytes())
    undo = []
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(boot_timeout=1.5, panel_timeout=1.5)
    finally:
        for f in undo:
            f()
    assert u is None, "긍정 기본선택이 아니면 자동 통과하지 않고 실패해야 한다"
    assert b"\r" not in sess.written, "Enter 를 치지 말아야 한다"


async def test_query_usage_captures_account_from_boot():
    # 부팅 화면에 계정·모델 신호가 모두 있으면 둘 다 부팅서 잡고 /status 폴백은 생략.
    boot = (b"\x1b[2J\x1b[H me@acme.com's Organization\r\n"
            b" Opus 4.8 (1M context)  /model to change\r\n"
            b" ? for shortcuts\r\n")
    sess = _FakeSession(boot, _panel_bytes())
    undo = []
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(boot_timeout=2.0, panel_timeout=2.0)
    finally:
        for f in undo:
            f()
    assert u is not None
    # 부팅 화면의 `<email>'s Organization` 신뢰 신호 → 계정이 잡혀야 한다(별칭).
    assert u.get("account"), u
    # 부팅 화면의 모델 배지도 잡힌다(model 폴백 출처).
    assert u.get("model") == "opus-4.8", u
    # 계정·모델이 모두 잡혔으면 /status 폴백은 주입하지 않는다(불필요 왕복 없음).
    assert b"/status" not in sess.written, sess.written


def _status_bytes() -> bytes:
    """실 /status(Status 탭) 화면 모사 — 계정 라벨은 여기에만 있다(2026-06-11 실관찰:
    Organization/Email 라벨, 부팅·Usage 탭엔 부재). 활성 모델도 여기에 표시되므로
    (2026-06-22) 배지가 라이브 화면에 안 떴을 때의 model 폴백 출처다."""
    body = ("   Settings  Status   Config   Usage   Stats\r\n"
            "   Version:          2.1.173\r\n"
            "   Login method:     Claude Max account\r\n"
            "   Organization:     alice@acme.com's Organization\r\n"
            "   Email:            alice@acme.com\r\n"
            "   Model:            Opus 4.8\r\n"
            "   Esc to cancel\r\n")
    return b"\x1b[2J\x1b[H" + body.encode("utf-8")


async def test_query_usage_account_fallback_via_status():
    """§5.5 잔존 후속: 부팅·/usage 화면에 계정 라벨이 없으면(실제 그렇다 — limits
    20/20 account None 의 원인) Esc+/status 로 Status 탭을 한 번 더 스크랩해
    계정을 채운다."""
    boot = b"\x1b[2J\x1b[H Welcome to Claude\r\n ? for shortcuts\r\n"
    sess = _FakeSession(boot, _panel_bytes(), status=_status_bytes())
    undo = []
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(boot_timeout=2.0, panel_timeout=2.0)
    finally:
        for f in undo:
            f()
    assert u is not None
    assert u["session"]["pct"] == 2, u            # /usage 파싱은 그대로
    assert sess.written.count(b"/status\r") == 1, sess.written
    assert u.get("account"), u                    # Status 탭에서 계정 확보
    assert "acme.com" in u["account"], u["account"]
    assert u.get("model") == "opus-4.8", u        # Status 탭에서 모델도 확보


async def test_query_usage_model_fallback_via_status_when_account_known():
    """계정은 부팅서 잡혔어도 모델 배지가 화면에 없으면(라이브 idle 푸터엔 'auto
    mode on'뿐) /status 를 한 번 스크랩해 활성 모델을 채운다(2026-06-22). 토큰이
    model NULL('?')로 적재되던 주된 원인 — 그림자 프로브로 model 폴백을 만든다."""
    boot = (b"\x1b[2J\x1b[H me@acme.com's Organization\r\n"
            b" ? for shortcuts\r\n")          # 계정 O, 모델 배지 X
    sess = _FakeSession(boot, _panel_bytes(), status=_status_bytes())
    undo = []
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(boot_timeout=2.0, panel_timeout=2.0)
    finally:
        for f in undo:
            f()
    assert u is not None
    assert u.get("account"), u                    # 계정은 부팅서 이미 확보
    assert u.get("model") == "opus-4.8", u        # 모델은 /status 폴백으로 확보
    assert sess.written.count(b"/status\r") == 1, sess.written


async def test_query_usage_model_none_when_unavailable():
    """부팅·/usage·/status 어디에도 모델 신호가 없으면 model=None — fail-open(미귀속
    'unknown', 기존 동작 보존). usage 자체는 정상 반환."""
    boot = b"\x1b[2J\x1b[H Welcome to Claude\r\n ? for shortcuts\r\n"
    sess = _FakeSession(boot, _panel_bytes(), status=None)
    undo = []
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(boot_timeout=2.0, panel_timeout=1.0)
    finally:
        for f in undo:
            f()
    assert u is not None and u["session"]["pct"] == 2
    assert u.get("model") is None


async def test_query_usage_account_none_when_status_lacks_label():
    """/status 폴백까지 갔는데도 라벨이 없으면(구버전 등) account=None — 기존
    fail-open 의미 보존(usage 자체는 정상 반환)."""
    boot = b"\x1b[2J\x1b[H Welcome to Claude\r\n ? for shortcuts\r\n"
    sess = _FakeSession(boot, _panel_bytes(), status=None)
    undo = []
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(boot_timeout=2.0, panel_timeout=1.0)
    finally:
        for f in undo:
            f()
    assert u is not None and u["session"]["pct"] == 2
    assert u.get("account") is None


async def test_query_usage_none_when_boot_times_out():
    # "shortcuts" 가 끝내 안 뜨면(트러스트 대화상자 등) None(안전) 이어야 한다.
    sess = _FakeSession(b"\x1b[2J\x1b[H loading...\r\n", _panel_bytes())
    undo = []
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(boot_timeout=0.3, panel_timeout=0.3)
    finally:
        for f in undo:
            f()
    assert u is None, "부팅 프롬프트 미도달 → None"
    assert sess.killed and sess.closed, "타임아웃도 세션을 정리해야 한다"


async def test_query_usage_none_when_session_open_fails():
    # _open_session 이 예외를 던지면(스폰 실패) query_usage 는 None 으로 흡수한다.
    orig = usageprobe._open_session

    def boom(*a, **k):
        raise OSError("spawn failed")

    usageprobe._open_session = boom
    try:
        u = usageprobe.query_usage(boot_timeout=0.3)
    finally:
        usageprobe._open_session = orig
    assert u is None


async def test_probe_never_arms_the_fullscreen_boot_canary():
    """☠ 그림자 프로브가 **사용자 패널의 렌더러**를 망가뜨리지 않는다 (pytmux-414).

    Claude Code 2.1.247 의 fullscreen boot canary 는 「fullscreen 으로 떴다가 첫 프레임
    +10초 안에 강제로 죽은」 실행을 스트라이크로 세고, 2회면 그 머신의 fullscreen 을
    통째로 끈다. 이 프로브는 정확히 그 모양으로 돈다(진짜 claude 를 띄웠다가
    `finally: sess.kill()`). 그래서 자식 env 로 canary 를 **애초에 안 무장시킨다.**

    ⛔ 「돌려 보니 되더라」로는 이 회귀를 못 잡는다 — 트립은 스트라이크 2회째에,
       그것도 **다음 실행**에서 조용히 일어난다. 그래서 재는 것은 결과가 아니라
       **자식에게 넘긴 env** 다.
    """
    captured = {}
    orig = usageprobe._open_session
    sess = _FakeSession(b"\x1b[2J\x1b[H Welcome to Claude\r\n ? for shortcuts\r\n",
                        _panel_bytes())

    def spy(argv, cwd, env, cols, rows):
        captured.update(env)
        return sess

    usageprobe._open_session = spy
    try:
        usageprobe.query_usage(cmd="claude", boot_timeout=2.0, panel_timeout=2.0)
    finally:
        usageprobe._open_session = orig

    assert captured, "자식 env 를 한 번도 못 붙잡았다 — 이 시험의 전제가 무너졌다"
    assert captured.get("CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN") == "1", (
        "그림자 프로브가 alt-screen 을 끄지 않고 claude 를 띄운다 — 그러면 fullscreen boot "
        "canary 가 무장하고, 뒤따르는 강제 kill 이 스트라이크를 쌓아 **사용자의** claude 패널이 "
        "classic 렌더러로 떨어진다(pytmux-414)")
    # ⛔ 대조군 — 같은 목적을 이 값으로 대신하면 자식이 fullscreen 으로 떠서
    #    스크래퍼의 전제(2J+draw 단일 버퍼)를 깬다. canary 를 피하는 것과 별개 문제다.
    assert "CLAUDE_CODE_NO_FLICKER" not in captured, (
        "CLAUDE_CODE_NO_FLICKER 로 대신하면 안 된다 — canary 는 피하지만 자식이 fullscreen 으로 "
        "떠서 이 스크래퍼가 가정한 단일 버퍼가 깨진다")


async def test_query_usage_reports_how_long_each_stage_took():
    """프로브는 **얼마나 걸렸고 성공했나**를 밖으로 말한다(pytmux-382).

    왜 값이 있나: 이 프로브는 진짜 `claude` 를 띄워 그 TUI 를 VT 파싱하므로 한 회차가
    수십 초짜리 CPU 다. 그런데 종전엔 소요시간도 성공 여부도 아무 데도 안 남아서,
    「아무도 아무 일을 안 하는데 서버가 코어를 먹는다」를 가리는 데 라이브 프로세스를
    py-spy 로 뜨는 수밖에 없었다.
    """
    boot = b"\x1b[2J\x1b[H Welcome to Claude\r\n ? for shortcuts\r\n"
    sess = _FakeSession(boot, _panel_bytes())
    undo, t = [], {}
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(cmd="claude", boot_timeout=2.0,
                                   panel_timeout=2.0, timings=t)
    finally:
        for f in undo:
            f()
    assert u is not None
    assert t.get("ok") is True, "성공했는데 ok 가 안 섰다: %r" % t
    for k in ("boot", "panel", "total"):
        assert isinstance(t.get(k), float), "%s 를 안 쟀다: %r" % (k, t)
    assert t["total"] >= t["boot"], t


async def test_timings_say_it_ran_even_when_the_probe_comes_back_empty():
    """⛔ **정작 알고 싶은 것은 실패한 회차다** — 그때도 「돌긴 돌았다」가 남아야 한다.

    부팅 신호가 안 뜨면 프로브는 예산을 전부 태우고 조용히 None 을 돌려준다(호출부는
    첫 실패만 로그에 남긴다). 그 회차가 계측에서 빈손이면 «비싼데 안 보이는» 그 상태가
    그대로다.
    """
    sess = _FakeSession("\x1b[2J\x1b[H (부팅 신호가 없는 화면)\r\n".encode("utf-8"), None)
    undo, t = [], {}
    _patch(undo, sess)
    try:
        u = usageprobe.query_usage(cmd="claude", boot_timeout=0.4,
                                   panel_timeout=0.4, timings=t)
    finally:
        for f in undo:
            f()
    assert u is None, "부팅 신호가 없는데 성공했다"
    assert t.get("ok") is False, "실패인데 ok 가 False 가 아니다: %r" % t
    assert t.get("boot") is None, "못 본 것을 잰 것처럼 적었다: %r" % t
    assert isinstance(t.get("total"), float) and t["total"] > 0, \
        "실패 회차가 «돌긴 돌았다»를 안 남겼다: %r" % t


async def test_boot_budget_has_real_headroom():
    """부팅 예산은 **여유가 배수**여야 한다(pytmux-382 · office1 실측).

    그 상자에서 부팅 신호까지 10.2~11.4초가 걸렸다. 종전 예산 12.0초는 여유가 10%
    뿐이었고, 그 측정은 «전용 프로세스»의 것이라 서버의 executor 스레드(같은 GIL 을
    이벤트 루프·ConPTY 리더 셋과 나눠 쓴다)에서는 더 느리다. 초과하면 값이 아니라
    **침묵**이 남으므로(예산을 다 태우고 None) 여유를 숫자로 못 박는다.
    """
    slowest_measured = 11.4
    assert usageprobe.BOOT_TIMEOUT >= slowest_measured * 2, (
        "부팅 예산 %.1fs 는 실측 최악(%.1fs)의 두 배가 안 된다 — 느린 상자에서 초과한다"
        % (usageprobe.BOOT_TIMEOUT, slowest_measured))
    assert usageprobe.PANEL_TIMEOUT >= 5.7 * 2, \
        "패널 예산도 같은 규율이어야 한다: %.1fs" % usageprobe.PANEL_TIMEOUT
