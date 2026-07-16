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
    무턱대고 Enter 를 쳐 미지의 선택을 확정하지 않게 한다."""
    yes_sel = (" Managed settings require approval\n"
               " ❯ 1. Yes, I trust these settings\n"
               "   2. No, exit Claude Code\n")
    no_sel = (" Managed settings require approval\n"
              "   1. Yes, I trust these settings\n"
              " ❯ 2. No, exit Claude Code\n")
    gt_sel = (" Managed settings require approval\n"
              " > Yes, I trust these settings\n")
    reworded = (" Managed settings require approval\n"
                " ❯ 1. Accept and continue\n")
    unrelated = " ? for shortcuts\n"
    assert usageprobe._managed_yes_selected(yes_sel) is True
    assert usageprobe._managed_yes_selected(gt_sel) is True
    assert usageprobe._managed_yes_selected(no_sel) is False
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
