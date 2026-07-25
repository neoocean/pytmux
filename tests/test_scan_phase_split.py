"""`_scan_claude` phase 분할(로드맵 #1 마무리) — **phase 간 결합**을 못박는 회귀.

세션경계·토큰회계·프롬프트승격 세 phase 는 앞서 뽑은 6개와 달리 루프 지역변수를
서로 주고받는다(`old_cl`·`committed`). 그래서 `_ScanFrame` 상태객체로 넘기는데,
**그 전달이 끊겨도 기존 골든(test_scan_claude_state_golden)은 통과한다**(실측:
`fr.committed` 를 0 으로 고정하는 뮤테이션을 골든이 못 잡았다). 골든이 덮는 것은
시나리오 8종의 프레임 지문이고, '연속 busy 중 응답 교체' 경계는 그 안에 없다.
여기서 그 공백을 정면으로 덮는다.

되돌리면 실패해야 하는 오라클:
  · 회계 phase 가 fr.committed 를 안 쓰면 → test_accounting_publishes_committed 실패
  · 승격 phase 가 fr.committed 를 안 읽으면 → test_promotion_uses_committed_boundary 실패
  · 프레임 필드를 dict/자유속성으로 바꾸면 → test_frame_fields_are_fixed 실패
"""
import importlib

import harness  # noqa: F401

sm = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")


class _Pane:
    id = 1

    def __init__(self):
        self.pending_prompts = ["첫 프롬프트", "둘째 프롬프트"]
        self.last_prompt = ""
        self._busy_exit_miss = 0
        self._claude = "busy"
        self._perm_mode = None
        self._perm_target = None
        self._bypass_seen = False
        self._tok_state = {"total": 0, "peak": 0}
        self._session_tokens = 0
        self._claude_account = None
        self._claude_account_full = None
        self._claude_account_manual = False
        self._exit_tokens = 0
        self._rules_pending = False
        self._rc_pending = False
        self._perm_auto_pending = False


class _Srv:
    _scan_prompt_promotion = sm.ServerClaudeMixin._scan_prompt_promotion
    _scan_token_accounting = sm.ServerClaudeMixin._scan_token_accounting

    def __init__(self):
        self.logged = []
        self._usage = {}          # 프로브 스냅샷(계정 폴백이 읽는다)

    def _update_claude_model(self, p, txt):
        return False

    def _log_tokens(self, sess, t, p, n):
        self.logged.append(n)

    def _token_debug_on(self):
        return False

    def _xc_tail_pane(self, sess, t, p, force=False):
        pass


def _frame(old_cl="busy", new_cl="busy", txt="화면"):
    return sm._ScanFrame(txt, old_cl, new_cl, True)


async def test_promotion_uses_committed_boundary():
    """**연속 busy 중** committed>0(= 다음 응답 시작)이면 큐의 다음 프롬프트를 승격한다.

    이 경계가 헤더의 "지금 처리 중인 프롬프트"를 갱신하는 유일한 경로다 — 끊기면
    긴 연속 작업에서 헤더가 첫 프롬프트에 얼어붙는다(골든은 이걸 못 잡는다)."""
    srv, p = _Srv(), _Pane()
    fr = _frame()
    fr.committed = 7
    srv._scan_prompt_promotion(fr, p)
    assert p.last_prompt == "첫 프롬프트"
    assert p.pending_prompts == ["둘째 프롬프트"]
    assert fr.changed is True


async def test_promotion_without_committed_does_not_advance():
    """같은 상황에서 committed=0 이면 승격하지 않는다(경계가 아니다)."""
    srv, p = _Srv(), _Pane()
    srv._scan_prompt_promotion(_frame(), p)          # committed 기본 0
    assert p.last_prompt == "" and len(p.pending_prompts) == 2


async def test_promotion_on_busy_to_idle_needs_two_frames():
    """busy→idle 첫 프레임은 깜빡임일 수 있어 승격하지 않고, 다음 idle 에 확정한다
    (§3.4 라치 — 분할하면서 이 상태가 패널에 남아야 한다)."""
    srv, p = _Srv(), _Pane()
    srv._scan_prompt_promotion(_frame(old_cl="busy", new_cl="idle"), p)
    assert p.last_prompt == "" and p._busy_exit_miss == 1
    srv._scan_prompt_promotion(_frame(old_cl="idle", new_cl="idle"), p)
    assert p.last_prompt == "첫 프롬프트" and p._busy_exit_miss == 0


async def test_accounting_publishes_committed():
    """회계 phase 가 tokens.step 의 확정값을 **프레임에 실어야** 뒤 phase 가 읽는다."""
    srv, p = _Srv(), _Pane()
    orig_step, orig_run = sm.tokens.step, sm.tokens.parse_running_tokens
    orig_acct, orig_full = sm.claude_account, sm.claude_account_full
    sm.tokens.parse_running_tokens = lambda txt: 100
    sm.tokens.step = lambda st, running, busy: 42
    sm.claude_account = lambda txt: None
    sm.claude_account_full = lambda txt: None
    try:
        fr = _frame()
        srv._scan_token_accounting(fr, None, None, p)
        assert fr.committed == 42
        assert srv.logged == [42]          # 확정분이 영속 로깅으로도 나간다
    finally:
        sm.tokens.step, sm.tokens.parse_running_tokens = orig_step, orig_run
        sm.claude_account, sm.claude_account_full = orig_acct, orig_full


async def test_frame_fields_are_fixed():
    """프레임은 __slots__ 고정 — 오타 필드가 조용히 생기면 phase 간 전달이 끊긴
    것을 아무도 모른다(이 코드의 실패 모드가 정확히 '조용한 무동작')."""
    fr = _frame()
    assert set(sm._ScanFrame.__slots__) == {
        "txt", "old_cl", "new_cl", "old_hdr", "committed", "changed"}
    try:
        fr.comitted = 1                     # 오타
        raise AssertionError("오타 필드가 생겼다")
    except AttributeError:
        pass


async def test_frame_starts_neutral():
    fr = _frame(old_cl=None, new_cl="idle", txt="t")
    assert (fr.committed, fr.changed) == (0, False)
    assert (fr.txt, fr.old_cl, fr.new_cl, fr.old_hdr) == ("t", None, "idle", True)
