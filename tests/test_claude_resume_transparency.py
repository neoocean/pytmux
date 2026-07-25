"""F3 옵션B — 자동재개 주입의 가시화 + 쿨다운(설계 F3_SCRAPE_FORGERY_DESIGN §5).

자동재개는 **화면 텍스트** 하나로 발화하는데 그 텍스트는 Claude 가 스스로 출력할 수
있다(검수 F3: 가짜 리밋 배너 → 주입 유발, 5/6 재현). 본문 필터로는 못 막으므로
**막는 대신 관측 가능**하게 만든다 — 주입할 때마다 알림, 그리고 같은 패널 반복 주입
쿨다운. 진짜 리밋의 리셋 창은 최소 1시간이라 15분 쿨다운은 정상 자동재개를 절대
막지 않는다(막으면 그게 더 큰 손해다).

되돌리면 실패해야 하는 오라클:
  · 알림을 빼면 → test_injection_emits_notice 실패
  · 쿨다운을 빼면 → test_repeat_injection_is_throttled 실패
  · 쿨다운이 정상 리밋(1시간 뒤)까지 막으면 → test_next_reset_window_still_fires 실패
  · limit 재확인(#6)을 빼면 → test_non_limit_screen_does_not_inject 실패
"""
import importlib

import harness  # noqa: F401

sm = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")


class _PTY:
    def __init__(self):
        self.writes = []

    def write(self, b):
        self.writes.append(b)


class _Pane:
    id = 3

    def __init__(self):
        self.pty = _PTY()
        self.screen = object()
        self.resume_msg = "continue"
        self._resume_pending = True
        self._resume_handle = None
        self._scanbuf = "x"


class _Loop:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now


class _Srv:
    _fire_resume = sm.ServerClaudeMixin._fire_resume
    _notice_resume = sm.ServerClaudeMixin._notice_resume
    _RESUME_COOLDOWN = sm.ServerClaudeMixin._RESUME_COOLDOWN
    # F3 옵션A 게이트도 실물을 쓴다(기본 off — 이 파일의 오라클은 종전 그대로여야
    # 한다: 옵션을 켜지 않은 사용자에게 동작 변화 0). 옵션A 자체 검증은
    # tests/test_claude_resume_verify.py.
    _resume_verify_blocks = sm.ServerClaudeMixin._resume_verify_blocks
    claude_resume_verify = "off"

    def __init__(self):
        self.loop = _Loop()
        self.clients = []
        self.notices = []

    @staticmethod
    def _notice_msg(key, ko, *, severity=None, **kw):
        return {"t": "notice", "key": key, "sev": severity, "kw": kw}


def _setup(monkey_state="limit"):
    srv = _Srv()
    pane = _Pane()
    # 화면 판정은 순수 함수 두 개(screen_text→claude_state)라 여기만 갈아끼운다.
    sm.screen_text = lambda scr: "화면"
    sm.claude_state = lambda txt: monkey_state
    # 알림 전송은 클라 목록을 도는데, 여기선 어떤 클라도 없다 → 전송 0.
    # 대신 _notice_msg 호출을 가로채 관측한다.
    orig = _Srv._notice_msg
    srv._sent = []

    def spy(key, ko, *, severity=None, **kw):
        msg = orig(key, ko, severity=severity, **kw)
        srv._sent.append(msg)
        return msg
    srv._notice_msg = spy
    return srv, pane


async def test_injection_emits_notice():
    """주입은 반드시 흔적을 남긴다 — 이게 F3 의 핵심(사일런트 조작 제거)."""
    srv, pane = _setup()
    srv._fire_resume(pane)
    assert pane.pty.writes == [b"continue\r"]
    assert [m["key"] for m in srv._sent] == ["ccmsg.resume_injected"]
    assert srv._sent[0]["kw"]["pane"] == pane.id
    assert srv._sent[0]["kw"]["msg"] == "continue"


async def test_repeat_injection_is_throttled():
    """같은 패널에 연달아(쿨다운 안) 오는 발화는 주입하지 않고 **알린다**.

    위조 배너를 반복해 그리는 것이 그대로 반복 주입이 되던 경로."""
    srv, pane = _setup()
    srv._fire_resume(pane)
    for _ in range(5):
        pane._resume_pending = True
        srv.loop.now += 60.0                     # 1분 간격 재발화
        srv._fire_resume(pane)
    assert pane.pty.writes == [b"continue\r"]    # 주입은 1회뿐
    keys = [m["key"] for m in srv._sent]
    assert keys[0] == "ccmsg.resume_injected"
    assert keys[1:] == ["ccmsg.resume_throttled"] * 5
    assert all(m["sev"] == "warn" for m in srv._sent[1:])


async def test_next_reset_window_still_fires():
    """정상 자동재개(리셋 창 ≥1h)는 **절대** 막히지 않는다 — 막으면 이 기능이 죽는다."""
    srv, pane = _setup()
    srv._fire_resume(pane)
    srv.loop.now += 3600.0                       # 다음 리셋 창
    pane._resume_pending = True
    srv._fire_resume(pane)
    assert pane.pty.writes == [b"continue\r", b"continue\r"]


async def test_non_limit_screen_does_not_inject():
    """발화 직전 재확인(#6) — limit 이 아니면 주입도 알림도 없다."""
    srv, pane = _setup(monkey_state="idle")
    srv._fire_resume(pane)
    assert pane.pty.writes == [] and srv._sent == []


async def test_notice_failure_never_blocks_resume():
    """알림 경로가 터져도 주입은 이미 끝나 있어야 한다(가시화가 기능을 죽이면 안 됨)."""
    srv, pane = _setup()

    def boom(*a, **kw):
        raise RuntimeError("알림 실패")
    srv._notice_msg = boom
    srv._fire_resume(pane)
    assert pane.pty.writes == [b"continue\r"]


async def test_cooldown_is_shorter_than_real_reset_window():
    """쿨다운은 실제 리셋 창(최소 1시간)보다 **짧아야** 한다 — 길면 정상 재개를 먹는다."""
    assert 0 < sm.ServerClaudeMixin._RESUME_COOLDOWN < 3600.0
