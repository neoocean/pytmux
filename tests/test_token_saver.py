"""토큰 절감 자동화(docs/TOKEN_SAVING_SCENARIO.md, M8~M12) 테스트.

- M8 골든 픽스처(tests/fixtures/claude/*.txt)로 claude.py 휴리스틱 회귀 고정.
- M9 claude_context_pct 잔량% 파서.
- M11 컨텍스트 잔량 기반 자동 정리(compact / doc-clear · 디바운스 · 임계 게이트).
- M10 토큰 예산 추적·경고 레벨.
- M12 자동재개 예산 게이트·예약 취소.
- 설정 setter opts.json 영속.
"""
import json
import os

import harness  # noqa: F401  (경로 설정)
from harness import server_only, teardown
from pytmuxlib.claude import (claude_context_pct, claude_feedback_prompt,
                              claude_model, claude_state, claude_usage)

FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "claude")


def _fix(name):
    with open(os.path.join(FIXDIR, name), encoding="utf-8") as f:
        return f.read()


# ---- M9: claude_context_pct ----
async def test_claude_context_pct():
    f = claude_context_pct
    assert f("context left 12%") == 12
    assert f("8% until auto-compact") == 8
    assert f("auto-compact at 5%") == 5
    assert f("context remaining 0%") == 0
    assert f("no context info here") is None
    assert f("45k tokens used") is None       # 토큰 누계는 잔량 아님
    assert f("context remaining 150%") is None  # 0~100 밖은 무시(오검출 방어)


# ---- M8: 골든 픽스처 회귀 고정 ----
async def test_golden_fixtures():
    """골든 픽스처가 기대 상태/사용량/잔량%/모델을 낸다(claude.py 회귀 고정).
    busy/idle/badge_1m/ctx_low 는 실 캡처 보강분(README), 나머지는 합성."""
    assert claude_state(_fix("limit.txt")) == "limit"
    assert claude_state(_fix("busy.txt")) == "busy"
    assert claude_state(_fix("idle.txt")) == "idle"
    assert claude_state(_fix("ctx_low.txt")) == "idle"
    assert claude_state(_fix("feedback.txt")) is None
    assert claude_feedback_prompt(_fix("feedback.txt")) is True
    assert claude_context_pct(_fix("ctx_low.txt")) == 8
    assert claude_context_pct(_fix("ctx_compact.txt")) == 12
    assert claude_context_pct(_fix("ctx_high.txt")) == 72
    assert claude_context_pct(_fix("idle.txt")) is None
    assert "1M" in (claude_usage(_fix("badge_1m.txt")) or "")
    # M14c: 실 모델 배지 'Opus 4.8 (1M context)' 에서 모델 계열·버전 추출.
    assert claude_model(_fix("badge_1m.txt")) == "opus-4.8"


# ---- M11: 컨텍스트 잔량 기반 자동 정리 ----
async def _claude_pane(srv):
    sess = srv.ensure_default_session(80, 24)
    win = sess.active_window
    p = win.active_pane
    return sess, win, p


async def test_ctx_autoclear_compact():
    """잔량<임계 + 응답 완료(busy→idle)면 /compact 1회 주입(기본 방식). 한 번
    발화하면 잔량이 회복할 때까지 재발화 안 함(디바운스)."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        injected = []
        srv._pc_inject = lambda pane, text: injected.append(text)
        srv.claude_ctx_autoclear = True
        srv.claude_ctx_threshold = 15
        srv.claude_ctx_action = "compact"

        def complete(text):
            p._claude = "busy"
            p.feed(b"\x1b[2J\x1b[H" + text.encode())
            srv._scan_claude(sess, win)

        complete("context left 8%\r\n? for shortcuts")     # 8 < 15 → 발화
        assert injected == ["/compact"], injected
        assert p._ctx_fired is True
        complete("context left 8%\r\n? for shortcuts")     # 여전히 낮음 → 재발화 X
        assert injected == ["/compact"], "디바운스: 회복 전 재발화 금지"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_ctx_autoclear_recovery_then_refire():
    """정리 후 잔량이 임계+여유 위로 회복하면 디바운스가 풀려 다음 저잔량에 재발화."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        injected = []
        srv._pc_inject = lambda pane, text: injected.append(text)
        srv.claude_ctx_autoclear = True
        srv.claude_ctx_threshold = 15
        srv.claude_ctx_min_interval = 0   # 시간 상한은 별도 테스트 — 여기선 끈다

        def complete(text):
            p._claude = "busy"
            p.feed(b"\x1b[2J\x1b[H" + text.encode())
            srv._scan_claude(sess, win)

        complete("context left 8%\r\n? for shortcuts")
        assert injected == ["/compact"]
        # 회복(72% ≥ 15+5) → 디바운스 해제
        complete("context left 72%\r\n? for shortcuts")
        assert p._ctx_fired is False
        complete("context left 9%\r\n? for shortcuts")     # 다시 낮음 → 재발화
        assert injected == ["/compact", "/compact"], injected
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_ctx_autoclear_threshold_gate():
    """잔량이 임계 이상이면 발화하지 않는다. 잔량% 미검출(None)도 발화하지 않는다."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        injected = []
        srv._pc_inject = lambda pane, text: injected.append(text)
        srv.claude_ctx_autoclear = True
        srv.claude_ctx_threshold = 15

        def complete(text):
            p._claude = "busy"
            p.feed(b"\x1b[2J\x1b[H" + text.encode())
            srv._scan_claude(sess, win)

        complete("context left 50%\r\n? for shortcuts")    # 50 ≥ 15 → X
        assert injected == []
        complete("? for shortcuts")                        # 잔량 None → X
        assert injected == []
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_ctx_autoclear_doc_clear_reuses_machinery():
    """action=doc-clear 면 기존 doc→/clear 상태기계를 재사용한다(문서화 지시 →
    다음 완료에 /clear). 잔량 정리가 auto-doc-clear(시간 기반)보다 우선."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        injected = []
        srv._pc_inject = lambda pane, text: injected.append(text)
        srv.claude_ctx_autoclear = True
        srv.claude_ctx_threshold = 15
        srv.claude_ctx_action = "doc-clear"

        def complete(text):
            p._claude = "busy"
            p.feed(b"\x1b[2J\x1b[H" + text.encode())
            srv._scan_claude(sess, win)

        complete("context left 8%\r\n? for shortcuts")     # → 문서화 지시 주입
        assert injected == [srv.prompt_clear_message], injected
        assert p._adc_active and p._pc_phase == "doc"
        complete("? for shortcuts")                        # 문서화 완료 → /clear
        assert injected[-1] == "/clear"
        complete("? for shortcuts")                        # /clear 완료 → 시퀀스 끝
        assert p._adc_active is False and p._pc_phase is None
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


# ---- M14: 정리 빈도 상한(time floor) ----
async def test_ctx_min_interval_caps_refire():
    """빈도 상한이 켜져 있으면 잔량이 회복→재하락해도 직전 정리로부터 min_interval
    초가 안 지났으면 재발화하지 않는다(시간 바닥). _ctx_last_fire 를 과거로 당기면
    상한이 풀려 다시 발화한다(monotonic 시계 진행을 시뮬레이트)."""
    import time as _t
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        injected = []
        srv._pc_inject = lambda pane, text: injected.append(text)
        srv.claude_ctx_autoclear = True
        srv.claude_ctx_threshold = 15
        srv.claude_ctx_min_interval = 300   # 상한 5분

        def complete(text):
            p._claude = "busy"
            p.feed(b"\x1b[2J\x1b[H" + text.encode())
            srv._scan_claude(sess, win)

        complete("context left 8%\r\n? for shortcuts")     # 첫 발화(상한 미해당)
        assert injected == ["/compact"]
        assert p._ctx_last_fire is not None
        # 회복으로 디바운스 해제됐지만 상한(5분)은 아직 — 재하락해도 발화 금지.
        complete("context left 72%\r\n? for shortcuts")
        assert p._ctx_fired is False
        complete("context left 9%\r\n? for shortcuts")
        assert injected == ["/compact"], "빈도 상한: 시간 미경과 시 재발화 금지"
        # 시간이 지난 것으로 시뮬레이트(_ctx_last_fire 를 과거로) → 상한 해제.
        p._ctx_last_fire = _t.monotonic() - 301
        complete("context left 9%\r\n? for shortcuts")
        assert injected == ["/compact", "/compact"], "상한 경과 후 재발화"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_ctx_min_interval_setter_clamp_persist():
    """set_claude_ctx_min_interval 은 0~3600 으로 클램프하고 opts.json 에 영속.
    0=상한 없음(_ctx_cap_ok 항상 True)."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        assert srv.set_claude_ctx_min_interval(300) == 300
        assert srv.set_claude_ctx_min_interval(99999) == 3600   # 상한 클램프
        assert srv.set_claude_ctx_min_interval(-5) == 0          # 하한 클램프
        assert srv.set_claude_ctx_min_interval("bad") == 0       # 잘못된 값=현 값
        assert srv._ctx_cap_ok(p) is True                        # 0 → 항상 허용
        srv.set_claude_ctx_min_interval(300)
        with open(srv.opts_path, encoding="utf-8") as f:
            assert json.load(f)["claude_ctx_min_interval"] == 300
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


# ---- M10: 토큰 예산 추적·경고 레벨 ----
async def test_budget_tracking_and_level():
    """확정 토큰이 일 누계에 더해지고 일 예산 대비 경고 레벨(0/80/100)이 갱신된다.
    예산 0(무제한)이면 추적/경고 없음."""
    srv, task, sock = await server_only()
    try:
        srv.token_budget_day = 1000
        srv._budget_track(700)
        assert srv._today_tokens == 700 and srv._budget_level == 0   # 70%
        srv._budget_track(150)
        assert srv._today_tokens == 850 and srv._budget_level == 80  # 85%
        srv._budget_track(300)
        assert srv._today_tokens == 1150 and srv._budget_level == 100
        # 무제한이면 추적 안 함
        srv.token_budget_day = 0
        srv.token_budget_session = 0
        srv._budget_track(500)
        assert srv._budget_level == 0
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_budget_over_day_and_session():
    """_budget_over 는 일/세션 예산 중 어느 쪽이라도 초과면 True(0=그 축 무시)."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        srv.token_budget_day = 1000
        srv._today_tokens = 1200
        assert srv._budget_over(p) is True
        srv._today_tokens = 100
        assert srv._budget_over(p) is False
        srv.token_budget_day = 0
        srv.token_budget_session = 500
        p._session_tokens = 600
        assert srv._budget_over(p) is True
        p._session_tokens = 100
        assert srv._budget_over(p) is False
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


# ---- M13: 예산 압박 시 plan 유도 ----
async def test_budget_plan_induction():
    """claude_budget_plan + 예산≥80% + idle + 권한모드 非plan/非bypass 면 shift+tab
    (\\x1b[Z)으로 plan 유도. bypass 는 불간섭, 예산<80% 면 무동작."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        keys = []
        srv._inject_keys = lambda pane, data: keys.append(data)
        srv.claude_budget_plan = True
        srv.token_budget_day = 1000      # 일 예산(스캔이 _session_tokens 처럼 안 덮음)

        def idle(text, today):
            srv._today_tokens = today
            srv._refresh_budget_level()
            p._claude = "idle"
            p.feed(b"\x1b[2J\x1b[H" + text.encode())
            srv._scan_claude(sess, win)

        # 예산<80%(70%) → 유도 안 함
        idle("? for shortcuts", 700)
        assert keys == []
        # 예산≥80%(90%) + default footer → plan 유도(shift+tab)
        idle("? for shortcuts", 900)
        assert keys == [b"\x1b[Z"], keys
        # bypass 는 불간섭(명시적 위험 모드)
        keys.clear()
        p._cam_tries = 0
        p._cam_last = None
        idle("bypass permissions", 950)
        assert keys == [], "bypass 는 안 건드림"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


# ---- M12: 자동재개 예산 게이트·예약 취소 ----
class _FakePty:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)


async def test_resume_budget_gate():
    """예산 게이트가 켜져 있고 예산 초과면 _fire_resume 가 continue 주입을 보류.
    게이트 꺼지거나 예산 이내면 정상 주입."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        fake = _FakePty()
        p.pty = fake
        p.feed(b"\x1b[2J\x1b[HClaude usage limit reached. resets at 5pm")
        srv.token_budget_day = 1000
        srv._today_tokens = 1500            # 초과
        srv.token_budget_resume_gate = True
        srv._fire_resume(p)
        assert fake.writes == [], "예산 초과 + 게이트 ON → 보류"
        srv.token_budget_resume_gate = False
        srv._fire_resume(p)
        assert fake.writes and fake.writes[-1] == b"continue\r", fake.writes
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_cancel_resume_clears_pending():
    """_cancel_resume 가 무장된 예약 핸들을 취소하고 pending 플래그를 내린다."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        import asyncio
        loop = asyncio.get_running_loop()
        p._resume_handle = loop.call_later(100, lambda: None)
        p._resume_pending = True
        srv._cancel_resume(p)
        assert p._resume_handle is None and p._resume_pending is False
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


# ---- M14: 무장 자동액션 카운트다운/취소 힌트 ----
async def test_pending_action_reports_kind_and_eta():
    """무장된 자동재개/auto-doc-clear 타이머가 있으면 _pending_action 이 종류와
    남은 초(ETA)를 보고한다(없으면 None). 자동재개를 우선해 본다."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        assert srv._pending_action(p) is None        # 무장 없음
        assert srv._pending_action(None) is None      # 패널 없음
        p._resume_handle = srv.loop.call_later(30, lambda: None)
        pa = srv._pending_action(p)
        assert pa and pa["kind"] == "resume" and 25 <= pa["eta"] <= 30, pa
        # resume 가 우선: 둘 다 무장돼 있어도 resume 를 보고.
        p._adc_timer = srv.loop.call_later(10, lambda: None)
        assert srv._pending_action(p)["kind"] == "resume"
        p._resume_handle.cancel()
        p._resume_handle = None
        pa = srv._pending_action(p)
        assert pa and pa["kind"] == "doc-clear" and 5 <= pa["eta"] <= 10, pa
        p._adc_timer.cancel()
        p._adc_timer = None
        assert srv._pending_action(p) is None
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_user_input_cancels_armed_resume():
    """사용자가 패널에 입력하면 무장된 자동재개 예약이 취소된다(§5.3 선점 —
    continue 중복 주입 방지). _handle_input 경로에서 _cancel_resume 가 불린다."""
    import base64
    from pytmuxlib.model import ClientConn
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        p._resume_handle = srv.loop.call_later(100, lambda: None)
        p._resume_pending = True
        client = ClientConn(None)
        client.session = sess
        srv._handle_input(client, {"pane": p.id,
                                   "data": base64.b64encode(b"x").decode()})
        assert p._resume_handle is None and p._resume_pending is False
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


# ---- 설정 setter opts.json 영속 ----
async def test_setters_persist_to_opts():
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        assert srv.set_claude_ctx_autoclear(True) is True
        assert srv.set_claude_ctx_action("doc-clear") == "doc-clear"
        assert srv.set_claude_ctx_action("bogus") == "doc-clear"   # 무효 무시
        assert srv.set_claude_ctx_threshold(200) == 99             # 클램프
        assert srv.set_token_budget(day=123000, session=45000) == (123000, 45000, 0, 0)
        assert srv.set_token_budget(h5=350000) == (123000, 45000, 350000, 0)
        assert srv.set_token_budget(acct=2_000_000) == (123000, 45000, 350000,
                                                        2_000_000)
        assert srv.set_claude_turn_warn(long_sec=900, repeat=0) == (900, 0)
        assert srv.set_token_budget_resume_gate(True) is True
        saved = json.load(open(srv.opts_path))
        assert saved["claude_long_turn_sec"] == 900
        assert saved["claude_repeat_alert"] == 0
        assert saved["claude_ctx_autoclear"] is True
        assert saved["claude_ctx_action"] == "doc-clear"
        assert saved["claude_ctx_threshold"] == 99
        # S5 토큰 모듈화 T3: token_budget_* 는 코어 top-level 이 아니라 플러그인 소유
        # plugin_opts 네임스페이스에 저장된다(claude-code server_opts_serialize).
        po = saved["plugin_opts"]
        assert po["token_budget_day"] == 123000
        assert po["token_budget_session"] == 45000
        assert po["token_budget_5h"] == 350000
        assert po["token_budget_account"] == 2_000_000
        assert po["token_budget_resume_gate"] is True
        # 코어 top-level 에는 더 이상 token_budget_* 가 없다(완전 플러그인 소유).
        assert "token_budget_day" not in saved
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


# ---- S6 T4: 실측(/usage) 한도 게이트 — 자동개입 보류의 1차 기준 ----

def _fresh_usage(srv, spct=None, wpct=None, account=None):
    """테스트용 실측 주입: _usage + 신선한 _usage_ts."""
    import time
    u = {}
    if spct is not None:
        u["session"] = {"pct": spct, "reset": "2pm"}
    if wpct is not None:
        u["week_all"] = {"pct": wpct, "reset": "Jun 13"}
    if account is not None:
        u["account"] = account
    srv._usage = u
    srv._usage_ts = time.time()


async def test_usage_gate_blocks_autoresume_measured():
    """실측 세션 % ≥ 게이트(기본 95, 기본 ON)면 자동재개 보류 — 절대 예산 게이트
    (M12 토글, 기본 OFF)와 **무관하게 독립** 작동. 임계 미만이면 정상 주입."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        assert srv.usage_gate_session_pct == 95, "기본 ON(95)"
        assert srv.usage_gate_week_pct == 0, "주간 기본 끔"
        assert srv.token_budget_resume_gate is False, "M12 토글은 기본 OFF 그대로"
        fake = _FakePty()
        p.pty = fake
        p.feed(b"\x1b[2J\x1b[HClaude usage limit reached. resets at 5pm")
        _fresh_usage(srv, spct=96)
        srv._fire_resume(p)
        assert fake.writes == [], "실측 96% ≥ 95 → 보류(M12 토글 무관)"
        _fresh_usage(srv, spct=94)
        srv._fire_resume(p)
        assert fake.writes and fake.writes[-1] == b"continue\r", \
            "임계 미만 → 정상 주입"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_usage_gate_fail_open():
    """fail-open 3종: ① 실측 부재 ② stale(갱신주기×2 초과) ③ 계정 불일치(둘 다
    알려져 있고 다름) — 어느 경우도 게이트가 개입하지 않는다. 임계 0=끔."""
    import time
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        fake = _FakePty()
        p.pty = fake
        p.feed(b"\x1b[2J\x1b[HClaude usage limit reached. resets at 5pm")
        # ① 실측 부재 → 주입
        assert srv._usage is None
        srv._fire_resume(p)
        assert len(fake.writes) == 1, "실측 없음 → fail-open"
        # ② stale: 신선도 한계(usage_refresh_sec×2=1200s) 초과 → 주입
        _fresh_usage(srv, spct=99)
        srv._usage_ts = time.time() - (srv.usage_refresh_sec * 2 + 1)
        srv._fire_resume(p)
        assert len(fake.writes) == 2, "stale 실측 → fail-open"
        # ③ 계정 불일치(실측·패널 둘 다 알려짐) → 주입
        _fresh_usage(srv, spct=99, account="other@y.org")
        p._claude_account = "me@woojinkim.org"
        srv._fire_resume(p)
        assert len(fake.writes) == 3, "계정 불일치 → fail-open"
        # 같은 계정이면 차단
        _fresh_usage(srv, spct=99, account="me@woojinkim.org")
        srv._fire_resume(p)
        assert len(fake.writes) == 3, "계정 일치 + 99% → 보류"
        # 패널 계정 미상(한쪽만 알려짐)이면 같은 로그인으로 보고 적용 → 보류
        p._claude_account = None
        srv._fire_resume(p)
        assert len(fake.writes) == 3, "한쪽 미상 → 게이트 적용(보류)"
        # 임계 0 = 끔 → 99% 라도 주입
        srv.set_usage_gate(session=0)
        srv._fire_resume(p)
        assert len(fake.writes) == 4, "게이트 끔 → 주입"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_usage_gate_week_axis():
    """주간 게이트(기본 끔)를 켜면 week_all 실측도 독립 축으로 보류시킨다."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        fake = _FakePty()
        p.pty = fake
        p.feed(b"\x1b[2J\x1b[HClaude usage limit reached. resets at 5pm")
        _fresh_usage(srv, spct=10, wpct=96)        # 세션 여유·주간 압박
        srv._fire_resume(p)
        assert len(fake.writes) == 1, "주간 게이트 기본 끔 → 주입"
        srv.set_usage_gate(week=95)
        srv._fire_resume(p)
        assert len(fake.writes) == 1, "주간 96% ≥ 95 → 보류"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_usage_gate_level_in_budget_level():
    """실측 게이트 레벨이 절대 예산과 같은 눈금(0/80/100)으로 status 경고에
    합류한다: 임계 도달=100, 임계의 80%(95→76) 도달=80."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        p._claude = "idle"
        assert srv._budget_level_for(p) == 0
        _fresh_usage(srv, spct=76)                 # 95*0.8=76 → 예고(80)
        assert srv._budget_level_for(p) == 80, "임계의 80% → 80"
        _fresh_usage(srv, spct=95)
        assert srv._budget_level_for(p) == 100, "임계 도달 → 100"
        _fresh_usage(srv, spct=75)
        assert srv._budget_level_for(p) == 0, "예고 미만 → 0"
    finally:
        await teardown(srv, task, sock)


async def test_set_usage_gate_persists_and_clamps():
    """set_usage_gate: 클램프(0~100)·부분 설정·plugin_opts 영속·기본값(95/0)."""
    srv, task, sock = await server_only()
    try:
        assert (srv.usage_gate_session_pct, srv.usage_gate_week_pct) == (95, 0)
        assert srv.set_usage_gate(session=90, week=98) == (90, 98)
        assert srv.set_usage_gate(session=150) == (100, 98), "100 클램프"
        assert srv.set_usage_gate(week=-5) == (100, 0), "0 클램프"
        assert srv.set_usage_gate() == (100, 0), "무인자=변경 없음"
        saved = json.load(open(srv.opts_path))
        po = saved["plugin_opts"]
        assert po["usage_gate_session_pct"] == 100
        assert po["usage_gate_week_pct"] == 0
        assert "usage_gate_session_pct" not in saved, "top-level 비저장(플러그인 소유)"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


# ---- S6 T5: 이벤트 트리거 실측 갱신(커밋 디바운스·임계 부근 단축) ----

async def test_commit_schedules_debounced_usage_refresh():
    """응답 종료(committed) 이벤트: 실측이 묵었으면(부재 포함) 20초 디바운스 갱신을
    1회만 예약하고, 연속 커밋은 합쳐진다. 실측이 신선(<3분)하면 예약 안 함."""
    import time
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        assert srv._usage_probe_handle is None
        # 실측 부재 + 커밋 → 예약
        srv._log_tokens(sess, sess.tabs[0], p, 100)
        h1 = srv._usage_probe_handle
        assert h1 is not None, "커밋 → 디바운스 예약"
        # 연속 커밋 → 기존 예약 유지(중복 없음)
        srv._log_tokens(sess, sess.tabs[0], p, 100)
        assert srv._usage_probe_handle is h1, "디바운스 — 예약 1개 유지"
        h1.cancel()
        srv._usage_probe_handle = None
        # 신선한 실측(<3분) → 커밋이 와도 예약 생략(프로브 비용 절약)
        _fresh_usage(srv, spct=10)
        srv._log_tokens(sess, sess.tabs[0], p, 100)
        assert srv._usage_probe_handle is None, "신선 실측 → 생략"
        # 3분 넘게 묵으면 다시 예약
        srv._usage_ts = time.time() - 181
        srv._log_tokens(sess, sess.tabs[0], p, 100)
        assert srv._usage_probe_handle is not None, "묵은 실측 → 예약"
        srv._usage_probe_handle.cancel()
        srv._usage_probe_handle = None
    finally:
        await teardown(srv, task, sock)


async def test_fire_usage_refresh_gates_and_calls_probe():
    """발화 시 Claude 패널이 없으면 프로브 생략, 있으면 refresh_usage 호출.
    (실 프로브는 숨은 claude spawn — 스텁으로 배선만 검증.)"""
    import asyncio
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        calls = []

        async def fake_refresh():
            calls.append(1)
        srv.refresh_usage = fake_refresh
        # Claude 패널 없음 → 생략
        srv._fire_usage_refresh()
        await asyncio.sleep(0)
        assert calls == [], "Claude 패널 없으면 spawn 낭비 안 함"
        # Claude 패널 있음 → refresh_usage 1회
        p._claude = "idle"
        srv._fire_usage_refresh()
        await asyncio.sleep(0)
        assert calls == [1], calls
    finally:
        await teardown(srv, task, sock)


async def test_near_gate_shortens_refresh_interval():
    """프로브 성공 직후(_after_usage_probe): 실측이 게이트 임계 -10%p 이내면 다음
    갱신을 주기/4(최소 60초)로 앞당겨 예약. 자동 갱신 꺼짐(0)이면 존중해 생략."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        # 임계(95) 부근 아님(84 < 85) → 예약 없음
        _fresh_usage(srv, spct=84)
        srv._after_usage_probe()
        assert srv._usage_probe_handle is None, "부근 아님 → 주기 유지"
        # 부근(85 ≥ 95-10) → 앞당김 예약
        _fresh_usage(srv, spct=85)
        srv._after_usage_probe()
        assert srv._usage_probe_handle is not None, "임계 부근 → 단축 예약"
        srv._usage_probe_handle.cancel()
        srv._usage_probe_handle = None
        # 주간 축도 독립 발동(켜져 있을 때만)
        _fresh_usage(srv, spct=10, wpct=92)
        srv._after_usage_probe()
        assert srv._usage_probe_handle is None, "주간 게이트 꺼짐 → 미발동"
        srv.set_usage_gate(week=95)
        srv._after_usage_probe()
        assert srv._usage_probe_handle is not None, "주간 92 ≥ 95-10 → 발동"
        srv._usage_probe_handle.cancel()
        srv._usage_probe_handle = None
        # 자동 갱신 꺼짐(usage_refresh_sec=0) → 사용자 의사 존중, 앞당기지 않음
        srv.usage_refresh_sec = 0
        _fresh_usage(srv, spct=94)
        srv._after_usage_probe()
        assert srv._usage_probe_handle is None, "자동 갱신 끔 → 생략"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_usage_probe_uses_claude_pane_cwd():
    """그림자 /usage 프로브 cwd = 실행 중인 Claude 패널의 셸 cwd(신뢰된 폴더).
    데몬 cwd(홈)로 띄우면 신뢰 대화상자에 막혀 프로브가 조용히 None 이 되는 라이브
    버그의 회귀 가드(2026-06-11). Claude 패널 없으면 데몬 cwd 폴백."""
    import importlib
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        # Claude 패널 없음 → 데몬 cwd 폴백
        srv.cwd = "/daemon/cwd"
        assert srv._probe_cwd() == "/daemon/cwd"
        # Claude 패널 있음 → 그 패널 셸 cwd
        p._claude = "idle"
        srv._pane_cwd = lambda pane: "/trusted/proj"
        assert srv._probe_cwd() == "/trusted/proj"
        # refresh_usage 가 그 cwd 로 query_usage 를 부른다(스텁 — 실 spawn 없음)
        up = importlib.import_module("pytmuxlib.plugins.claude-code.usageprobe")
        seen = {}
        orig = up.query_usage

        def fake_query(cmd, cwd, **kw):
            seen["cwd"] = cwd
            return {"session": {"pct": 1, "reset": None}}
        up.query_usage = fake_query
        try:
            u = await srv.refresh_usage()
        finally:
            up.query_usage = orig
        assert u and seen["cwd"] == "/trusted/proj", seen
        # 패널 cwd 미상이면 폴백 체인 유지
        srv._pane_cwd = lambda pane: None
        assert srv._probe_cwd() == "/daemon/cwd"
    finally:
        await teardown(srv, task, sock)
