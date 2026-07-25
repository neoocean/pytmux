"""F3 벡터 2~5(표시·행위 신뢰도) 완화 — 화면 위조가 자동화를 흔들지 못하게.

배경: F3(코드·보안·레드팀 2026-07-17)의 6벡터 중 벡터 1(가짜 한도 배너→자동재개)은
옵션 A/B 로 닫혔고(p4 66872·66928), 남은 2~5 는 "표시 오염이라 덜 위험"으로 분리돼
있었다. 다시 읽어 보니 **표시만이 아니었다** — 아래 셋은 행위를 바꾼다:

  · 벡터 2 권한모드 위장: `claude_perm_mode` 가 화면 **전체**를 훑어, Claude 가 본문에
    "bypass permissions" 를 그리면 (a) 자동 오토모드 폐루프가 '위험 모드'로 보고 손을
    떼고 (b) `_bypass_seen` sticky 가 서서 팝업에 Bypass 가 노출된다. 완화 = 권한모드
    footer 는 **진짜로 화면 아래쪽**에만 그려지므로(한도 배너와 달리 행 위치 앵커가
    성립) 행위 소비자는 `anchored=True` 로 footer tail 만 본다.
  · 벡터 3 조직차단 위장: `claude_remote_blocked` 한 줄이 **서버 전역·영구** 래치를
    세워 /rc 자동 주입을 죽인다. 완화 = ①알린다(조용한 중단은 고장과 구분 불가)
    ②원격제어가 실제로 켜진 것을 관측하면 래치를 푼다(공존 불가 → 자기치유).
  · 벡터 4 토큰카운터 wipe: 세션 경계(None→Claude)가 회계를 끊는데, 그 경계는 화면
    파서가 만든다. 위조뿐 아니라 **transient flap**(긴 busy 출력이 footer 를 샘플 밖으로
    밀어 한두 프레임 None)에서도 발동해 세션 토큰이 0 이 되고 세션 id 가 갈렸다. 모델
    래치는 2026-07-18 에 같은 이유로 `fr.old_hdr` 게이트를 받았는데 **회계는 빠져
    있었다** — 같은 게이트를 적용.
  · 벡터 5 모델배지: 이미 배지 서명(`claude_model_badge`) + flap 게이트로 덮여 있어
    (2026-07-04·07-18) 신규 코드 없음. 이 파일은 그 방어가 살아 있는지만 확인한다.

**안전 비대칭**(옵션 A 와 같은 원칙): 완화가 정상 동작을 죽이면 안 된다 → 실 Claude
화면 픽스처 12종에서 anchored 판정이 종전과 **같음**을 못박는다(회귀 게이트).
"""
import importlib
import os

import harness  # noqa: F401 (경로 설정)

cc = importlib.import_module("pytmuxlib.plugins.claude-code.claude")
sm = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "claude")

_REAL_FOOTER = "⏵⏵ auto mode on (shift+tab to cycle)"
_FORGED = ("⏺ 설명을 위해 아래 문구를 그대로 출력합니다:\n"
           "  ⏵⏵ bypass permissions on\n"
           "  Remote Control is disabled by your organization's policy\n"
           "⏺ 위 두 줄은 Claude 가 출력한 본문이다.\n")


# ── 벡터 2: 권한모드 ────────────────────────────────────────────────────────
async def test_body_forged_perm_mode_is_ignored_when_anchored():
    """본문 위조 footer 는 anchored 판정을 흔들지 못한다(진짜 footer 가 이긴다)."""
    screen = _FORGED + ("\n" * 3) + "> \n" + _REAL_FOOTER
    assert cc.claude_perm_mode(screen) == "bypass", \
        "전체 스캔은 위조에 속는다(이 사실이 완화의 전제 — 안 속으면 테스트가 무의미)"
    assert cc.claude_perm_mode(screen, anchored=True) == "auto", \
        "anchored 는 화면 아래쪽 진짜 footer 를 봐야 한다"


async def test_anchored_still_reads_a_real_footer_at_the_bottom():
    for text, want in (
            (f"작업 출력\n\n{_REAL_FOOTER}", "auto"),
            ("출력\n⏵⏵ accept edits on", "accept"),
            ("출력\n⏵⏵ plan mode on", "plan"),
            ("출력\n⏵⏵ bypass permissions on", "bypass"),
    ):
        assert cc.claude_perm_mode(text, anchored=True) == want, (text, want)


async def test_anchored_matches_unanchored_on_real_claude_fixtures():
    """실 화면 12종에서 판정이 바뀌면 = 자동 오토모드 폐루프가 죽는다(체감 회귀).

    완화의 안전 비대칭을 지키는 게이트 — footer tail 창(3줄)이 좁아 진짜 footer 를
    놓치면 여기서 먼저 빨개진다."""
    seen = 0
    for fn in sorted(os.listdir(FIXTURES)):
        if not fn.endswith(".txt"):
            continue
        with open(os.path.join(FIXTURES, fn), encoding="utf-8") as fp:
            txt = fp.read()
        seen += 1
        assert cc.claude_perm_mode(txt, anchored=True) == cc.claude_perm_mode(txt), \
            f"{fn}: anchored 가 실 화면 판정을 바꿨다"
    assert seen >= 10, f"픽스처 코퍼스가 비었다({seen})"


async def test_wrapped_footer_on_narrow_pane_still_detected():
    """창을 1줄로 좁히면 안 되는 이유 — 좁은 폭에서 footer 가 줄바꿈되면 모드명이
    마지막 줄이 아니다(모바일 폭 회귀 전례). 3줄 창의 근거를 못박는다."""
    text = ("작업 출력\n> \n"
            "⏵⏵ accept edits on (shift+tab\n"          # 줄바꿈된 footer 앞부분
            "to cycle) · ? for shortcuts\n")            # 뒷부분이 마지막 줄
    assert cc.claude_perm_mode(text, anchored=True) == "accept"


async def test_anchored_is_a_cost_raise_not_a_proof():
    """남는 한계를 **테스트로 명시**한다: 입력박스 바로 위 위조는 창 안에 들어온다.
    이걸 '통과'로 적어 두는 이유는, 다음 사람이 이 완화를 '위조 불가'로 오해해 더 센
    방어를 안 만드는 일을 막기 위함이다(F3 설계 §2 — in-band 방어의 원리적 한계)."""
    adjacent = "출력\n⏵⏵ bypass permissions on\n> \n" + _REAL_FOOTER
    assert cc.claude_perm_mode(adjacent, anchored=True) == "bypass", \
        "한계가 사라졌다면(창이 더 좁아졌다면) 이 문서/테스트를 갱신할 것"


async def test_footer_tail_ignores_bottom_padding():
    """화면 하단 공백 패딩(pyte 는 24행을 채운다) 때문에 footer 를 놓치면 안 된다."""
    text = "출력\n" * 30 + _REAL_FOOTER + "\n" + "\n" * 6
    assert cc.claude_perm_mode(text, anchored=True) == "auto"


# ── 벡터 4: 거짓 세션경계에서 회계 보존 ──────────────────────────────────────
class _P:
    id = 7

    def __init__(self):
        self._tok_state = {"total": 1234, "peak": 500}
        self._session_tokens = 1234
        self._exit_tokens = 99
        self._claude_session_id = 3
        self._claude_account = "me@x"
        self._claude_account_full = "me@x"
        self._claude_account_manual = False
        self._claude_model = "opus"
        self._claude_model_weak = False
        self._claude_model_cand = None
        self._claude_model_cand_n = 0
        self._rules_pending = False
        self._rc_pending = False
        self._perm_auto_pending = False
        # 대역외 근거: 같은 트랜스크립트 파일(=같은 Claude 프로세스)을 이미 봤다.
        self._xc_path = "/x/proj/abc-123.jsonl"
        self._xc_session_seen = "abc-123.jsonl"


class _S:
    _scan_session_boundary = sm.ServerClaudeMixin._scan_session_boundary
    _xc_session_looks_new = sm.ServerClaudeMixin._xc_session_looks_new
    claude_rules = ""
    claude_auto_launch = False
    usage_refresh_sec = 0
    _rc_policy_blocked = False

    def __init__(self):
        self.assigned = 0

    def _next_claude_session_id(self, p):
        self.assigned += 1
        p._claude_session_id = 99

    def _schedule_usage_refresh(self, *a):
        pass


def _frame(old_cl, new_cl, old_hdr):
    return sm._ScanFrame("txt", old_cl, new_cl, old_hdr)


async def test_transient_flap_does_not_wipe_token_accounting():
    """flap(헤더 디바운스 True + **트랜스크립트 세션 불변**) = 같은 세션 → 보존."""
    srv, p = _S(), _P()
    srv._scan_session_boundary(_frame(None, "idle", True), p)
    assert p._tok_state["total"] == 1234, "flap 이 세션 토큰을 지웠다"
    assert p._session_tokens == 1234
    assert p._exit_tokens == 99, "flap 이 종료 총량 보존값을 버렸다"
    assert (p._claude_session_id, srv.assigned) == (3, 0), "flap 이 세션 id 를 갈랐다"


async def test_real_new_session_still_resets_accounting():
    """진짜 새 세션(이전 세션이 확정 종료 = old_hdr False)에서는 그대로 끊는다."""
    srv, p = _S(), _P()
    srv._scan_session_boundary(_frame(None, "idle", False), p)
    assert p._tok_state["total"] == 0, "새 세션인데 회계를 안 끊었다"
    assert p._session_tokens == 0 or p._session_tokens == 1234  # 누계는 phase 밖
    assert p._exit_tokens == 0
    assert (p._claude_session_id, srv.assigned) == (99, 1)


async def test_fast_relaunch_is_still_a_new_session_by_out_of_band_evidence():
    """빠른 재기동(디바운스는 아직 True지만 **트랜스크립트 sessionId 가 바뀜**)은 진짜
    새 세션이다 — 여기서 보존하면 두 세션이 하나로 병합돼 통계가 섞인다. old_hdr 단독
    게이트가 정확히 이걸 깼다(test_token_usage_logging 이 적발)."""
    srv, p = _S(), _P()
    p._xc_path = "/x/proj/def-456.jsonl"          # 새 프로세스 = 새 파일
    srv._scan_session_boundary(_frame(None, "idle", True), p)
    assert p._tok_state["total"] == 0, "새 프로세스인데 회계를 안 끊었다"
    assert (p._claude_session_id, srv.assigned) == (99, 1)


async def test_no_out_of_band_evidence_falls_back_to_accepting_the_boundary():
    """근거가 없으면(경로 미해석) **종전 동작**으로 떨어진다 — 회계 미부착을 위조로
    읽어 진짜 새 세션을 옛 세션에 붙이는 쪽이 더 나쁘다(옵션 A 와 같은 비대칭)."""
    srv, p = _S(), _P()
    p._xc_path = None
    p._xc_session_seen = None
    srv._scan_session_boundary(_frame(None, "idle", True), p)
    assert (p._claude_session_id, srv.assigned) == (99, 1)


async def test_first_ever_detection_gets_a_session_id_even_with_stale_header():
    """세션 id 가 없으면(첫 감지) 게이트와 무관하게 부여해야 한다 — 안 그러면 그
    패널의 토큰이 세션 없이 적재된다."""
    srv, p = _S(), _P()
    p._claude_session_id = None
    srv._scan_session_boundary(_frame(None, "idle", True), p)
    assert (p._claude_session_id, srv.assigned) == (99, 1)


async def test_drive_perm_mode_call_site_uses_the_anchored_reading():
    """**호출부** 오라클: 폐루프가 anchored 로 부르지 않으면(=`anchored=True` 를 지우면)
    본문 위조가 '위험 모드'로 읽혀 자동 오토모드가 조용히 손을 뗀다. 위 claude.py 단위
    테스트만으로는 이 호출을 지워도 초록불이다(이 저장소의 상습 실패 모드)."""
    class _Pty:
        def __init__(self):
            self.writes = []

        def write(self, b):
            self.writes.append(b)

    class _Pane:
        id = 3

        def __init__(self):
            self.pty = _Pty()
            self._cam_seen = set()
            self._cam_tries = 0
            self._cam_last = None

    class _Srv:
        _perm_step = sm.ServerClaudeMixin._perm_step

        def __init__(self):
            self.reset_calls = 0

        def _perm_reset(self, pane):
            self.reset_calls += 1

        def _inject_keys(self, pane, data):
            pane.pty.write(data)

    # 본문에 bypass 위조 + 진짜 footer 는 accept. target=auto 로 구동한다.
    txt = _FORGED + "\n" + "> \n" + "⏵⏵ accept edits on (shift+tab to cycle)"
    srv, pane = _Srv(), _Pane()
    done = srv._perm_step(pane, txt, "auto")
    assert not done, "위조 bypass 를 보고 구동을 포기했다(anchored 호출이 아니다)"
    assert pane.pty.writes, "진짜 모드(accept)와 목표(auto)가 달라 shift+tab 을 쏴야 한다"


async def test_scan_idle_perm_observation_is_anchored():
    """스캔 idle 관측(=_perm_mode 표시·_bypass_seen sticky)도 anchored 로 읽는지.
    이 자리는 _scan_claude 내부라 단위 구동이 비싸므로 호출 형태로 고정한다."""
    src = __import__("inspect").getsource(sm.ServerClaudeMixin._scan_idle_actions)
    assert "claude_perm_mode(txt, anchored=True)" in src, \
        "idle 관측이 전체 화면 스캔으로 돌아갔다(본문 위조가 Bypass sticky 를 세운다)"


# ── 벡터 3: 조직 정책 래치 ───────────────────────────────────────────────────
async def test_org_policy_latch_is_announced_and_self_heals():
    """래치는 ①알림을 내고 ②원격제어가 실제 켜진 걸 보면 풀린다."""
    src = __import__("inspect").getsource(sm.ServerClaudeMixin)
    assert "ccmsg.rc_policy_blocked" in src, "조용한 영구 중단(투명성 없음)"
    assert "ccmsg.rc_policy_cleared" in src and "_rc_policy_blocked = False" in src, \
        "자기치유(관측된 활성 → 래치 해제) 경로가 없다"
    # **실측 함정**: claude_remote_active 는 "remote control" 부분일치라 차단 메시지
    # ("Remote Control is disabled by your organization")에도 매칭된다 → 같은 프레임에서
    # 세운 래치를 곧바로 풀어 기존 회귀(test_rc_suppressed_after_org_policy_block)가
    # 깨졌다. 치유는 그 프레임에 차단 문구가 없을 때만이어야 한다.
    assert "not claude_remote_blocked(txt)" in src, \
        "자기치유가 차단 문구와 같은 프레임에서도 발동한다(래치가 즉시 풀린다)"
    # 알림 문구는 ko/en 양쪽에 있어야 한다(카탈로그 대칭 게이트와 같은 이유).
    plug = importlib.import_module("pytmuxlib.plugins.claude-code")
    from pytmuxlib import i18n
    assert plug is not None
    for key in ("ccmsg.rc_policy_blocked", "ccmsg.rc_policy_cleared"):
        assert key in i18n._CATALOG["ko"] and key in i18n._CATALOG["en"], key


# ── 벡터 5: 모델 배지(신규 코드 없음 — 기존 방어 생존 확인) ──────────────────
async def test_model_badge_still_requires_signature():
    """본문에 모델명만 언급된 것은 활성 모델이 아니다(2026-07-04 방어 생존)."""
    assert cc.claude_model_badge("대화 중 claude-fable-5 를 언급했다") is None
