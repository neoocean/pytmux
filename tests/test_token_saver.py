"""Claude 토큰 추적/자동재개 보조 테스트.

- M8 골든 픽스처(tests/fixtures/claude/*.txt)로 claude.py 휴리스틱 회귀 고정.
- M9 claude_context_pct 잔량% 파서.
- 자동재개 예약 취소·카운트다운.
- 그림자 /usage 갱신(커밋 디바운스·한도 부근 단축).
- 설정 setter opts.json 영속.
"""
import json
import os

import harness  # noqa: F401  (경로 설정)
from harness import server_only, teardown
from pytmuxlib.claude import (claude_context_pct, claude_feedback_prompt,
                              claude_model, claude_state, claude_usage,
                              parse_inline_limit)

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
    # §3.2: 사용률 경고 화면(used 93% … limit)은 차단이 아니라 idle(footer 있음).
    assert claude_state(_fix("limit_warn.txt")) == "idle"
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
    # 실 캡처(2026-06-16 코퍼스 감사, captures/playground.local): footer 인라인 한도
    # "You've used 93% of your session limit · resets 1:40pm…". ① parse_inline_limit
    # 가 세션 %·리셋을 뽑고 ② 사용률 경고라 차단(claude_limit)·전송에러(api_error)는
    # 둘 다 False 여야 한다(#9 F1/F2 회귀 — 'limit' 단어가 차단/에러로 오판되지 않음).
    from pytmuxlib.claude import claude_api_error
    il = parse_inline_limit(_fix("inline_limit.txt"))
    assert il == {"session": {"pct": 93, "reset": "1:40pm (Asia/Seoul)"}}, il
    assert claude_state(_fix("inline_limit.txt")) == "idle"   # footer 있음 = 차단 아님
    assert claude_api_error(_fix("inline_limit.txt")) is False


async def _claude_pane(srv):
    sess = srv.ensure_default_session(80, 24)
    win = sess.active_window
    p = win.active_pane
    return sess, win, p


# ---- 자동재개 예약 취소 ----
class _FakePty:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)


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


# ---- 무장 자동재개 카운트다운/취소 힌트 ----
async def test_pending_action_reports_kind_and_eta():
    """무장된 자동재개 타이머가 있으면 _pending_action 이 종류와 남은 초(ETA)를
    보고한다(없으면 None)."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        assert srv._pending_action(p) is None        # 무장 없음
        assert srv._pending_action(None) is None      # 패널 없음
        p._resume_handle = srv.loop.call_later(30, lambda: None)
        pa = srv._pending_action(p)
        assert pa and pa["kind"] == "resume" and 25 <= pa["eta"] <= 30, pa
        p._resume_handle.cancel()
        p._resume_handle = None
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
        assert srv.set_claude_turn_warn(long_sec=900, repeat=0) == (900, 0)
        saved = json.load(open(srv.opts_path))
        # claude_long_turn_sec·claude_repeat_alert 도 plugin_opts 로 이전됨(완전분리,
        # 2026-07-07) — 코어 top-level 이 아니라 claude-code plugin_opts 에 저장된다.
        assert saved["plugin_opts"]["claude_long_turn_sec"] == 900
        assert saved["plugin_opts"]["claude_repeat_alert"] == 0
        assert "claude_long_turn_sec" not in saved   # 코어 top-level 엔 없다
        # S5 토큰 모듈화 T3: 플러그인 소유 설정은 plugin_opts 네임스페이스에 저장된다
        # (claude-code server_opts_serialize). §7-4: deprecate 된 절대 예산
        # token_budget_* 는 더 이상 저장되지 않는다(구 키는 다음 저장에서 자연 소멸).
        po = saved["plugin_opts"]
        assert "token_budget_day" not in po
        assert "token_budget_resume_gate" not in po
        # 코어 top-level 에는 token_budget_* 가 없다.
        assert "token_budget_day" not in saved
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


# ---- 실측(/usage) 테스트 헬퍼 ----

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
    """발화 시 살아 있는 Claude 패널이 없고 최근 트랜스크립트 활동도 없으면 프로브 생략,
    둘 중 하나라도 있으면 refresh_usage 호출(_usage_probe_allowed). (실 프로브는 숨은
    claude spawn — 스텁으로 배선만 검증. recent_activity_cwd 는 실 ~/.claude 격리 위해 스텁.)"""
    import asyncio
    from pytmuxlib import transcript
    srv, task, sock = await server_only()
    _rac = transcript.recent_activity_cwd
    transcript.recent_activity_cwd = lambda *a, **k: None   # 기본: 패널밖 활동 없음
    try:
        sess, win, p = await _claude_pane(srv)
        calls = []

        async def fake_refresh():
            calls.append(1)
        srv.refresh_usage = fake_refresh
        # Claude 패널 없음 + 최근 활동 없음 → 생략
        srv._fire_usage_refresh()
        await asyncio.sleep(0)
        assert calls == [], "패널·최근활동 모두 없으면 spawn 낭비 안 함"
        # 패널 밖 사용: 패널 없어도 최근 트랜스크립트 활동 있으면 발화
        transcript.recent_activity_cwd = lambda *a, **k: "/outside/proj"
        srv._fire_usage_refresh()
        await asyncio.sleep(0)
        assert calls == [1], ("패널밖 활동 → 프로브", calls)
        transcript.recent_activity_cwd = lambda *a, **k: None
        # Claude 패널 있음 → refresh_usage 1회(활동 없어도)
        p._claude = "idle"
        srv._fire_usage_refresh()
        await asyncio.sleep(0)
        assert calls == [1, 1], calls
    finally:
        transcript.recent_activity_cwd = _rac
        await teardown(srv, task, sock)


async def test_near_limit_shortens_refresh_interval():
    """프로브 성공 직후(_after_usage_probe): 실측 사용률이 한도 부근(_USAGE_NEAR_
    LIMIT_PCT=90 이상)이면 다음 갱신을 주기/4(최소 60초)로 앞당겨 예약. 자동 갱신
    꺼짐(0)이면 존중해 생략."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        # 부근 아님(89 < 90) → 예약 없음
        _fresh_usage(srv, spct=89)
        srv._after_usage_probe()
        assert srv._usage_probe_handle is None, "부근 아님 → 주기 유지"
        # 부근(90 ≥ 90) → 앞당김 예약
        _fresh_usage(srv, spct=90)
        srv._after_usage_probe()
        assert srv._usage_probe_handle is not None, "한도 부근 → 단축 예약"
        srv._usage_probe_handle.cancel()
        srv._usage_probe_handle = None
        # 주간 축도 독립 발동
        _fresh_usage(srv, spct=10, wpct=92)
        srv._after_usage_probe()
        assert srv._usage_probe_handle is not None, "주간 92 ≥ 90 → 발동"
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
    버그의 회귀 가드(2026-06-11). Claude 패널 없으면 (최근 트랜스크립트 cwd →) 데몬
    cwd 폴백. (여기선 실 파일시스템 격리 위해 newest_transcript 를 None 으로 스텁.)"""
    import importlib
    from pytmuxlib import transcript
    srv, task, sock = await server_only()
    _nt = transcript.newest_transcript
    transcript.newest_transcript = lambda *a, **k: None   # 실 ~/.claude 격리
    try:
        sess, win, p = await _claude_pane(srv)
        # Claude 패널·최근 트랜스크립트 모두 없음 → 데몬 cwd 폴백
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
        # 패널 cwd 미상 → (트랜스크립트 없음이므로) 데몬 cwd 폴백
        srv._pane_cwd = lambda pane: None
        assert srv._probe_cwd() == "/daemon/cwd"
        # 패널 밖 사용: 살아 있는 Claude 패널 없이도 최근 트랜스크립트 cwd 로 폴백
        # (데몬 cwd 로 새지 않아 트러스트 화면에 안 막힘).
        p._claude = None
        transcript.newest_transcript = lambda *a, **k: "/x/sess.jsonl"
        _rc = transcript.read_cwd
        transcript.read_cwd = lambda path, **k: "/outside/proj"
        try:
            assert srv._probe_cwd() == "/outside/proj"
        finally:
            transcript.read_cwd = _rc
    finally:
        transcript.newest_transcript = _nt
        await teardown(srv, task, sock)


# ---- §3.5 세션/계정 귀속 정확성 ----
async def test_session_seq_seeds_from_db_after_restart():
    """§3.5②: 첫 세션 부여 직전 DB max(session) 으로 시드 → 재시작 후 새 세션 id 가
    영속된 옛 세션 id 와 충돌하지 않는다(_claude_session_seq 는 부팅마다 0)."""
    from pytmuxlib import usagedb, usagelog
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        conn = srv._tokens_db_conn()
        # 이전 부팅이 남긴 이력: 세션 id 42 까지 썼다.
        usagedb.insert(conn, usagelog.make_record(
            1_700_000_000.0, 0, p.id, 42, "a@x.org", 100))
        assert srv._claude_session_seq == 0, "부팅 직후(코어)는 0"
        assert srv._session_seq_seeded is False
        # 첫 세션 부여 → max(42) 시드 후 +1
        srv._next_claude_session_id(p)
        assert p._claude_session_id == 43, p._claude_session_id
        assert srv._session_seq_seeded is True
        # 이후 부여는 DB 재조회 없이 메모리 카운터만 단조 증가
        srv._next_claude_session_id(p)
        assert p._claude_session_id == 44
    finally:
        await teardown(srv, task, sock)


async def test_session_seq_empty_db_starts_at_one():
    """빈 DB(또는 연결 실패)면 시드=0 → 첫 세션 id 1(기존 동작 보존)."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)
        srv._next_claude_session_id(p)
        assert p._claude_session_id == 1, p._claude_session_id
    finally:
        await teardown(srv, task, sock)


async def test_account_first_seen_latched_not_overwritten():
    """§3.5③: 세션에서 **처음** 검출된 계정만 래치하고, 이후 프레임에 뜬 다른(또는
    오검출) 계정 라벨로 덮지 않는다(매 프레임 last-seen → first-seen). 한 Claude
    프로세스=한 계정이므로 이미 확정된 토큰의 재귀속을 막는다."""
    srv, task, sock = await server_only()
    try:
        sess, win, p = await _claude_pane(srv)

        def scan(text):
            p.feed(b"\x1b[2J\x1b[H" + text.encode())
            srv._scan_claude(sess, win)

        # 새 세션 진입(None→idle) + 첫 계정 라벨
        scan("first@woojinkim.org's Organization\r\n? for shortcuts")
        assert p._claude_account == "fi…@woojinkim.org", p._claude_account
        # 같은 세션에서 다른 계정 라벨이 떠도 first-seen 유지
        scan("second@other.org's Organization\r\n? for shortcuts")
        assert p._claude_account == "fi…@woojinkim.org", "first-seen 유지"
        # 세션 종료 후 새 세션이 뜨면 다시 새 계정으로 래치
        scan("nonclaude shell output\r\n$ ")           # claude None → 세션 끝
        scan("third@woojinkim.org's Organization\r\n? for shortcuts")
        assert p._claude_account == "th…@woojinkim.org", "새 세션은 재래치"
    finally:
        await teardown(srv, task, sock)


# ---- §3.7 포맷 미인식 가시화(silent failure) ----
async def test_fmt_unknown_warning_surfaces_and_clears():
    """Claude 가 실행 중(fg 명령에 'claude')인데 화면 파서가 상태를 못 읽는 상태가
    지속되면 상태줄에 '포맷 미인식' ⚠ 경고를 세우고(추적 중단 가시화), 파서가 다시
    인식하거나 Claude 가 아니면 즉시 해제한다(§3.7). throttle/임계 상수는 0 으로 패치."""
    import sys as _sys
    srv, task, sock = await server_only()
    sm = _sys.modules[srv._update_fmt_unknown.__module__]
    orig_iv, orig_sec = sm._FMT_CHECK_INTERVAL, sm._FMT_UNKNOWN_SEC
    try:
        sess, win, p = await _claude_pane(srv)
        sm._FMT_CHECK_INTERVAL = 0.0
        sm._FMT_UNKNOWN_SEC = 0.0                 # 첫 검사에서 즉시 unknown
        srv._fg_is_claude = lambda pane: True     # ground-truth: Claude 실행 중

        def scan(text):
            p.feed(b"\x1b[2J\x1b[H" + text.encode())
            srv._scan_claude(sess, win)

        # 파서가 못 읽는 화면(claude_state None) + Claude fg → 포맷 미인식
        scan("garbled unrecognized footer xyz")
        assert p._claude is None, "파서가 상태를 못 읽음"
        assert p._fmt_unknown is True
        assert p._claude_warn and "포맷 미인식" in p._claude_warn
        # 와이어(활성 패널 경고)에도 실린다 — 클라 상태줄 ⚠ 세그먼트가 이걸 그린다.
        assert "포맷 미인식" in (srv._status_msg(sess).get("claude_warn") or "")
        # Claude 가 다시 인식되면 즉시 해제(throttle 무시)
        srv._fg_is_claude = lambda pane: True
        scan("? for shortcuts")                   # idle 로 인식
        assert p._claude == "idle"
        assert p._fmt_unknown is False
        assert p._claude_warn is None
        # 미인식 재발 후, Claude 아님(fg False)이면 해제(셸로 빠진 정상 None)
        srv._fg_is_claude = lambda pane: True
        scan("garbled again zzz")
        assert p._fmt_unknown is True
        srv._fg_is_claude = lambda pane: False
        scan("plain shell output\r\n$ ")
        assert p._fmt_unknown is False and p._claude_warn is None
    finally:
        sm._FMT_CHECK_INTERVAL, sm._FMT_UNKNOWN_SEC = orig_iv, orig_sec
        await teardown(srv, task, sock)


async def test_fmt_unknown_clears_on_static_shell_after_exit():
    """§3.7 (사용자 보고 2026-07-18 Windows): '포맷 미인식' ⚠ 가 뜬 뒤 Claude 가 종료돼
    셸이 **정적**(새 출력 없음)이면, 종전엔 dirty 게이트로 스캔이 통째로 건너뛰어져
    _update_fmt_unknown 이 다시 안 돌아 경고가 상태줄에 눌러앉았다. 이제 경고가 떠 있는
    동안 _scan_claude 의 pending 게이트가 그 패널을 계속 스캔 대상으로 잡아, **화면 재-feed
    없이** 스캔만 다시 돌려도 fg≠claude 를 관측해 해제한다."""
    import sys as _sys
    srv, task, sock = await server_only()
    sm = _sys.modules[srv._update_fmt_unknown.__module__]
    orig_iv, orig_sec = sm._FMT_CHECK_INTERVAL, sm._FMT_UNKNOWN_SEC
    try:
        sess, win, p = await _claude_pane(srv)
        sm._FMT_CHECK_INTERVAL = 0.0
        sm._FMT_UNKNOWN_SEC = 0.0
        # 미인식 화면 + Claude fg → 경고 세움(dirty feed 1회).
        srv._fg_is_claude = lambda pane: True
        p.feed(b"\x1b[2J\x1b[Hgarbled unrecognized footer")
        srv._scan_claude(sess, win)
        assert p._fmt_unknown is True and p._claude_warn
        # Claude 종료: fg 가 더는 claude 아님. **화면은 그대로 두고**(재-feed 안 함)
        # 다른 pending 항은 모두 끈다 — 스캔이 계속 도는 유일한 이유가 _fmt_unknown
        # (이번 수정)임을 격리한다. 종전 코드라면 여기서 스캔이 skip 돼 경고가 남는다.
        srv._fg_is_claude = lambda pane: False
        p._hdr_claude = False
        p._busy_exit_miss = 0
        p._exit_token_pending = 0
        p._rc_menu_active = False
        p._rc_pending = False
        p._was_busy = False
        assert p._feed_seq == p._scan_seq, "새 출력 없음(정적) 전제"
        srv._scan_claude(sess, win)                  # 재-feed 없이 스캔만
        assert p._fmt_unknown is False, "정적 셸에서도 해제돼야(pending=_fmt_unknown)"
        assert p._claude_warn is None
    finally:
        sm._FMT_CHECK_INTERVAL, sm._FMT_UNKNOWN_SEC = orig_iv, orig_sec
        await teardown(srv, task, sock)


async def test_fmt_unknown_throttles_fg_check():
    """fg 검사(ps)는 비싸므로 인식 실패 패널에 한해 _FMT_CHECK_INTERVAL 간격으로만
    호출한다(throttle). 인식 성공 프레임은 즉시 해제하며 fg 검사를 부르지 않는다."""
    import sys as _sys
    srv, task, sock = await server_only()
    sm = _sys.modules[srv._update_fmt_unknown.__module__]
    orig_iv = sm._FMT_CHECK_INTERVAL
    try:
        sess, win, p = await _claude_pane(srv)
        sm._FMT_CHECK_INTERVAL = 9999.0           # 사실상 1회만 통과
        calls = []
        srv._fg_is_claude = lambda pane: (calls.append(1), True)[1]

        def scan(text):
            p.feed(b"\x1b[2J\x1b[H" + text.encode())
            srv._scan_claude(sess, win)

        scan("garbled a")       # 첫 미인식 → fg 검사 1회
        scan("garbled b")       # throttle 창 → fg 검사 생략
        scan("garbled c")
        assert calls == [1], calls
        # 인식 프레임은 fg 검사 없이 즉시 해제
        scan("? for shortcuts")
        assert calls == [1], "인식 프레임은 fg 검사 안 함"
        assert p._fmt_unknown is False
    finally:
        sm._FMT_CHECK_INTERVAL = orig_iv
        await teardown(srv, task, sock)


async def test_model_families_externalized():
    """4-B: 모델 패밀리 화이트리스트를 환경변수(PYTMUX_CLAUDE_MODEL_FAMILIES)로 코드
    수정 없이 확장한다 — Anthropic 이 신규 계열을 내도 코드 변경 없이 대응. 기본
    화이트리스트는 Opus|Sonnet|Haiku|Fable(요청 2026-06-21, Fable 기본 인식)."""
    from pytmuxlib.claude import _build_model_re
    assert _build_model_re().search("Opus 4.8").group(1).lower() == "opus"
    # Fable 은 이제 기본 등록 — 'Fable 5' → 'fable' 로 파싱된다.
    fab = _build_model_re().search("running Fable 5")
    assert fab and fab.group(1).lower() == "fable", "Fable 기본 인식"
    assert _build_model_re().search("running Quill 1") is None   # 미등록 계열
    os.environ["PYTMUX_CLAUDE_MODEL_FAMILIES"] = "Quill"
    try:
        m = _build_model_re().search("running Quill 1 now")
        assert m and m.group(1).lower() == "quill", "env 로 신규 패밀리 인식"
    finally:
        del os.environ["PYTMUX_CLAUDE_MODEL_FAMILIES"]
    assert _build_model_re().search("Opus 4.8").group(1).lower() == "opus"
