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


async def test_query_usage_captures_account_from_boot():
    boot = (b"\x1b[2J\x1b[H me@acme.com's Organization\r\n"
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
    # 이미 잡혔으면 /status 폴백은 주입하지 않는다(불필요 왕복 없음).
    assert b"/status" not in sess.written, sess.written


def _status_bytes() -> bytes:
    """실 /status(Status 탭) 화면 모사 — 계정 라벨은 여기에만 있다(2026-06-11 실관찰:
    Organization/Email 라벨, 부팅·Usage 탭엔 부재)."""
    body = ("   Settings  Status   Config   Usage   Stats\r\n"
            "   Version:          2.1.173\r\n"
            "   Login method:     Claude Max account\r\n"
            "   Organization:     alice@acme.com's Organization\r\n"
            "   Email:            alice@acme.com\r\n"
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
