"""F3 옵션A — 자동재개 **대역외** 하한 게이트(설계 F3_SCRAPE_FORGERY_DESIGN §3 옵션 A).

자동재개는 화면 텍스트 하나로 발화하고 그 텍스트는 Claude 가 스스로 그릴 수 있다(검수
F3). 옵션 B 가 그걸 **가시화**했고, 여기 옵션 A 는 **대역외 사실**로 반증한다: 진짜 5h
리밋은 계정이 실제로 할당량을 소진했다는 뜻이라 그 세션의 트랜스크립트(usage_xc)에
최근 창 사용량이 무겁게 남는다. 위조 배너는 저사용 세션에서도 뜬다.

이 게이트의 **유일한 실질 위험은 오억제**(진짜 리밋에서 Claude 를 못 깨움)이므로 설계가
고른 형태가 "거의 0" 하한이고, 판단 불가는 **언제나 통과**다. 그 비대칭을 여기서 못박는다.

되돌리면 실패해야 하는 오라클:
  · 게이트를 기본 on 으로 바꾸면 → test_default_off_keeps_current_behavior 실패
  · 회계 미부착(rows=0)을 위조로 읽으면 → test_no_accounting_never_suppresses 실패
  · 창 밖(오래된) 사용량을 창 안으로 세면 → test_old_usage_outside_window_suppresses 실패
  · 억제가 쿨다운을 소모하게 만들면 → test_suppression_does_not_consume_cooldown 실패
  · 억제를 조용히 하면 → test_suppression_is_announced 실패
  · 임계 비교를 뒤집으면(>= ↔ <) → test_weak_and_strict_thresholds 실패
"""
import importlib
import time

import harness
from pytmuxlib import usagedb

sm = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
cc = importlib.import_module("pytmuxlib.plugins.claude-code")

_SID = "c4de604f-95e9-4a68-9cdd-0a18740848f3"


def _xc(xkey, ts, tokens, session=_SID):
    return {"xkey": xkey, "ts": ts, "session_uuid": session, "model": "opus-4.8",
            "input": tokens, "output": 0, "cache_create": 0, "cache_read": 0,
            "is_sidechain": 0}


class _PTY:
    def __init__(self):
        self.writes = []

    def write(self, b):
        self.writes.append(b)


class _Pane:
    id = 3

    def __init__(self, path="/tmp/%s.jsonl" % _SID):
        self.pty = _PTY()
        self.screen = object()
        self.resume_msg = "continue"
        self._resume_pending = True
        self._resume_handle = None
        self._scanbuf = "x"
        self._xc_path = path


class _Loop:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now


class _Srv:
    _fire_resume = sm.ServerClaudeMixin._fire_resume
    _notice_resume = sm.ServerClaudeMixin._notice_resume
    _resume_verify_blocks = sm.ServerClaudeMixin._resume_verify_blocks
    _xc_window_usage = sm.ServerClaudeMixin._xc_window_usage
    _RESUME_COOLDOWN = sm.ServerClaudeMixin._RESUME_COOLDOWN
    _RESUME_VERIFY_WINDOW = sm.ServerClaudeMixin._RESUME_VERIFY_WINDOW
    _RESUME_VERIFY_WEAK = sm.ServerClaudeMixin._RESUME_VERIFY_WEAK
    _RESUME_VERIFY_STRICT = sm.ServerClaudeMixin._RESUME_VERIFY_STRICT

    def __init__(self, conn, mode="weak"):
        self.loop = _Loop()
        self.clients = []
        self._conn = conn
        self.claude_resume_verify = mode
        self.sent = []

    def _tokens_db_conn(self):
        return self._conn

    def _xc_resolve_path(self, pane):
        return getattr(pane, "_xc_path", None)

    def _notice_msg(self, key, ko, *, severity=None, **kw):
        msg = {"t": "notice", "key": key, "sev": severity, "kw": kw}
        self.sent.append(msg)
        return msg


def _fire_resume_scoped(srv, state):
    """`_fire_resume` 이 도는 **동안에만** 화면 판정 두 함수를 갈아끼운 호출자.

    종전엔 `_setup` 이 `sm.screen_text`/`sm.claude_state` 를 모듈 전역에 **영구**
    치환했다. run.py 는 전 모듈을 한 프로세스에서 돌리므로 그 치환이 뒤따르는 모든
    테스트 모듈에 남아, 화면은 늘 "화면"·상태는 늘 "limit" 이 됐다 — 전체 스위트에서
    test_server(56)·test_token_saver(5)·test_transcript_wiring(5) **66건**이 이것
    하나로 깨졌다(2026-07-26). 판정 함수가 필요한 구간은 `_fire_resume` 호출뿐이라
    그 안으로 가둔다(테스트 본문은 그대로).
    """
    real = sm.ServerClaudeMixin._fire_resume

    def call(pane):
        with harness.patched(sm, screen_text=lambda scr: "화면",
                             claude_state=lambda txt: state):
            return real(srv, pane)
    return call


def _setup(rows, mode="weak", state="limit", path=None):
    """rows = [(xkey, ts_offset_sec, tokens)] — 오프셋은 **현재 시각 기준**(음수=과거)."""
    conn = usagedb.connect(":memory:")
    now = time.time()
    if rows:
        usagedb.insert_xc_many(
            conn, [_xc(k, now + off, tok) for k, off, tok in rows])
    srv = _Srv(conn, mode=mode)
    srv._fire_resume = _fire_resume_scoped(srv, state)
    pane = _Pane(path) if path else _Pane()
    return srv, pane, conn


async def test_default_off_keeps_current_behavior():
    """기본 off = 확인 없음. 실사용이 0이어도 종전대로 주입한다 — 켜지 않은 사용자에게
    동작 변화 0(설계 §4-4 롤백 경로의 요구)."""
    assert cc.norm_resume_verify(None) == "off", "정규화 기본이 off"
    srv, pane, conn = _setup([], mode="off")
    srv._fire_resume(pane)
    assert pane.pty.writes == [b"continue\r"]
    assert [m["key"] for m in srv.sent] == ["ccmsg.resume_injected"]
    conn.close()


async def test_no_accounting_never_suppresses():
    """그 세션에 usage_xc 행이 **하나도 없으면** 판단 불가 → 통과(주입).

    이게 이 게이트의 안전 비대칭이다: '위조를 놓침'은 옵션 B 의 가시화가 받아내지만,
    '진짜 리밋을 못 깨움'은 사용자가 손으로 복구해야 하는 회귀다."""
    srv, pane, conn = _setup([], mode="strict")
    srv._fire_resume(pane)
    assert pane.pty.writes == [b"continue\r"], "회계 없음 → 억제하지 않는다"
    conn.close()


async def test_low_recent_usage_suppresses():
    """회계는 붙어 있는데(과거 행 존재) 최근 5h 사용이 거의 0 → 위조로 보고 억제."""
    srv, pane, conn = _setup([("a:1", -8 * 3600, 900_000)], mode="weak")
    srv._fire_resume(pane)
    assert pane.pty.writes == [], "억제되어 주입 없음"
    assert [m["key"] for m in srv.sent] == ["ccmsg.resume_unverified"]
    assert srv.sent[0]["sev"] == "warn"
    conn.close()


async def test_heavy_recent_usage_passes():
    """진짜 리밋의 모습 — 최근 창에 무거운 사용이 남아 있으면 그대로 주입."""
    srv, pane, conn = _setup([("a:1", -600, 900_000)], mode="strict")
    srv._fire_resume(pane)
    assert pane.pty.writes == [b"continue\r"]
    conn.close()


async def test_old_usage_outside_window_suppresses():
    """사용량이 **창 밖**(5h 이전)에만 있으면 최근 창 Σ 는 0 → 억제.

    창 경계를 무시하고 전체 Σ 를 보면 이 테스트가 통과하지 못한다(그게 뮤테이션)."""
    srv, pane, conn = _setup([("a:1", -6 * 3600, 900_000)], mode="weak")
    srv._fire_resume(pane)
    assert pane.pty.writes == []
    conn.close()


async def test_weak_and_strict_thresholds():
    """임계 경계값 — weak(5k)/strict(100k) 각각 '미만이면 억제, 이상이면 통과'."""
    W = sm.ServerClaudeMixin._RESUME_VERIFY_WEAK
    S = sm.ServerClaudeMixin._RESUME_VERIFY_STRICT
    for mode, tokens, inject in (("weak", W - 1, False), ("weak", W, True),
                                 ("strict", S - 1, False), ("strict", S, True)):
        # 창 안 행 하나로 정확히 임계를 만든다(경계를 넘는 값 — 안 그러면 비교를
        # 뒤집어도 통과하는 공허 오라클이 된다).
        srv, pane, conn = _setup([("a:1", -600, tokens)], mode=mode)
        srv._fire_resume(pane)
        got = bool(pane.pty.writes)
        assert got is inject, "%s %d토큰 → 주입 %s 여야 한다" % (mode, tokens, inject)
        conn.close()


async def test_suppression_does_not_consume_cooldown():
    """억제는 쿨다운을 **소모하지 않는다** — 소모하면 그 뒤 진짜 리밋 발화가 쿨다운에
    걸려 죽는다(억제 게이트가 정상 기능을 잡아먹는 최악의 상호작용)."""
    srv, pane, conn = _setup([("a:1", -8 * 3600, 900_000)], mode="weak")
    srv._fire_resume(pane)                     # 억제
    assert pane.pty.writes == []
    assert getattr(pane, "_resume_fired_at", None) is None
    # 이제 최근 창에 실사용이 생겼다(진짜 리밋) → 곧바로 주입돼야 한다.
    usagedb.insert_xc_many(conn, [_xc("a:2", time.time() - 60, 900_000)])
    srv.loop.now += 60.0
    pane._resume_pending = True
    srv._fire_resume(pane)
    assert pane.pty.writes == [b"continue\r"]
    conn.close()


async def test_suppression_is_announced_then_folded():
    """억제는 보인다(조용한 억제 = '자동재개 고장'과 구분 불가). 다만 위조 배너를
    계속 그리는 상대가 알림을 폭주시키지 못하게 **같은 간격으로 접는다**."""
    srv, pane, conn = _setup([("a:1", -8 * 3600, 900_000)], mode="weak")
    for i in range(4):
        pane._resume_pending = True
        srv.loop.now += 60.0
        srv._fire_resume(pane)
    assert [m["key"] for m in srv.sent] == ["ccmsg.resume_unverified"], "첫 1회만"
    srv.loop.now += sm.ServerClaudeMixin._RESUME_COOLDOWN
    pane._resume_pending = True
    srv._fire_resume(pane)
    assert len(srv.sent) == 2, "쿨다운 지나면 다시 알린다(계속 억제 중임을 알려야 한다)"
    conn.close()


async def test_unresolved_transcript_path_passes():
    """트랜스크립트 경로를 못 잡았으면 판단 불가 → 통과."""
    srv, pane, conn = _setup([("a:1", -8 * 3600, 900_000)], mode="strict")
    pane._xc_path = None
    srv._xc_resolve_path = lambda p: None
    srv._fire_resume(pane)
    assert pane.pty.writes == [b"continue\r"]
    conn.close()


async def test_db_failure_passes():
    """DB 가 터져도 자동재개를 막지 않는다(best-effort 계약)."""
    srv, pane, conn = _setup([("a:1", -8 * 3600, 900_000)], mode="weak")

    def boom():
        raise RuntimeError("DB 없음")
    srv._tokens_db_conn = boom
    srv._fire_resume(pane)
    assert pane.pty.writes == [b"continue\r"]
    conn.close()


async def test_session_scope_is_per_session():
    """다른 세션의 무거운 사용량은 이 세션의 알리바이가 되지 않는다(계정이 아니라
    **세션** 단위 신호 — 위조는 특정 패널에서 일어난다)."""
    conn = usagedb.connect(":memory:")
    now = time.time()
    usagedb.insert_xc_many(conn, [
        _xc("other:1", now - 600, 900_000, session="99999999-0000-0000-0000-0"),
        _xc("mine:1", now - 8 * 3600, 900_000)])       # 내 세션은 창 밖에만
    srv, pane = _Srv(conn, mode="weak"), _Pane()
    srv._fire_resume = _fire_resume_scoped(srv, "limit")
    srv._fire_resume(pane)
    assert pane.pty.writes == [], "남의 세션 사용량으로 통과되면 안 된다"
    conn.close()


async def test_window_query_shape():
    """DB 헬퍼 계약 — 세션 전체 행수/창 안 행수/창 안 Σ 를 함께 준다(호출부가
    'rows==0=판단 불가' 를 구분하는 근거)."""
    conn = usagedb.connect(":memory:")
    now = time.time()
    usagedb.insert_xc_many(conn, [_xc("a:1", now - 600, 10),
                                  _xc("a:2", now - 10 * 3600, 20)])
    out = usagedb.xc_session_window(conn, _SID, now - 5 * 3600)
    assert out == {"rows": 2, "win_rows": 1, "win_full": 10}
    assert usagedb.xc_session_window(conn, "없는세션", now - 1)["rows"] == 0
    conn.close()
