"""Claude Code 휴리스틱(pytmuxlib/claude.py) 단위 테스트 — 상태/사용량/리밋 파서.

(docs/HANDOFF.md §11 분리로 test_protocol 에서 이리로 옮김.)"""
import datetime as dt

import harness  # noqa: F401  (경로 설정)
from pytmuxlib.claude import (claude_model, claude_perm_mode, claude_prompt,
                              claude_state, claude_usage, parse_reset_delay,
                              saver_hook_events)


async def test_screen_text_matches_display():
    """serverclaude.screen_text(경량 추출)가 `"\\n".join(screen.display)`와 셀 단위로
    동일해야 한다(perf #11 — wcwidth 호출 없이 같은 결과). 와이드문자(CJK)·연속셀
    포함 화면으로 못박는다."""
    import pyte
    from pytmuxlib.serverclaude import screen_text

    s = pyte.Screen(20, 3)
    st = pyte.Stream(s)
    st.feed("AB한글CD\r\n↑ 1.2k tokens\r\nx")
    assert screen_text(s) == "\n".join(s.display)


async def test_parse_reset_delay():
    now = dt.datetime(2026, 6, 2, 14, 0, 0)
    assert parse_reset_delay("limit reached, resets at 3:00pm", now) == 3600
    assert parse_reset_delay("rate limit, resets at 15:30", now) == 5400
    assert parse_reset_delay("normal output, nothing", now) is None
    # 과거 시각이면 익일로
    d = parse_reset_delay("limit reached resets 9am", now)
    assert d is not None and d > 3600


async def test_claude_state():
    assert claude_state("blah blah\n? for shortcuts") == "idle"
    assert claude_state("Compacting… (esc to interrupt)") == "busy"
    assert claude_state("Claude usage limit reached. resets at 5pm") == "limit"
    assert claude_state("user@host ~ % ls") is None
    # 현행 Claude Code(2026): 작업 스피너 줄(esc to interrupt 없음)
    assert claude_state("✽ Crunching… (38s · ↓ 1.9k tokens)") == "busy"
    assert claude_state("✻ Choreographing… (3m 28s)") == "busy"
    assert claude_state("· Boondoggling… (14s · thinking)") == "busy"
    assert claude_state("✻ Symbioting…") == "busy"          # 시간 표시 전 프레임
    # 현행 idle footer: 권한 모드 줄(shift+tab 순환)
    assert claude_state("❯\n⏵⏵ auto mode on (shift+tab to cycle)") == "idle"
    assert claude_state("⏵⏵ accept edits on (shift+tab to cycle)") == "idle"
    # busy 와 idle footer 가 함께 있으면 busy 우선
    assert claude_state(
        "✽ Flowing… (8m 4s · ↑ 21.1k tokens)\n"
        "⏵⏵ auto mode on (shift+tab to cycle)") == "busy"
    # 폰트/터미널에 따라 스피너 글리프가 `*`/`·` 로 렌더되는 변형
    assert claude_state("* Baking… (10s · ↑ 419 tokens · still thinking)") == "busy"
    # 시간 표시 없이도 토큰 화살표 또는 "still thinking" 만으로 busy 판정
    assert claude_state(
        "⏵⏵ auto mode on (shift+tab to cycle)\n"
        "* Baking… still thinking") == "busy"
    assert claude_state(
        "⏵⏵ auto mode on (shift+tab to cycle)\n"
        "↑ 419 tokens") == "busy"
    # 오탐 방지: 도구 출력 말줄임표는 busy 아님
    assert claude_state("⎿  … +38 lines (ctrl+o to expand)") is None
    # 오탐 방지: 화살표 없는 토큰 언급은 busy 아님
    assert claude_state("Cost: 1.2k tokens used today") is None


async def test_claude_perm_mode():
    # auto(자동 수락): ⏵⏵ / auto mode / auto-accept edits
    assert claude_perm_mode("⏵⏵ auto mode on (shift+tab to cycle)") == "auto"
    assert claude_perm_mode("⏵⏵ accept edits on (shift+tab to cycle)") == "auto"
    assert claude_perm_mode("auto-accept edits on (shift+tab to cycle)") == "auto"
    # plan 모드
    assert claude_perm_mode("⏸ plan mode on (shift+tab to cycle)") == "plan"
    # default: 권한 글리프 없이 idle 입력 힌트만 보이는 일반 모드.
    # ★실제 Claude default footer 는 "? for shortcuts" 다(이전엔 "shift+tab to cycle"
    #  만 default 로 잡아 — 실제 footer 엔 그 문구가 없으므로 — None 을 반환했고,
    #  그래서 default→auto 자동전환이 시작 못 했다. 좁은 폭 모바일 미동작의 근본).
    assert claude_perm_mode("? for shortcuts · ← for agents") == "default"
    assert claude_perm_mode("  ? for shortcuts") == "default"
    assert claude_perm_mode("/help for help, /status for status") == "default"
    assert claude_perm_mode("normal\nshift+tab to cycle") == "default"
    # bypass(위험·명시 모드): 건드리지 않게 별도 분류
    assert claude_perm_mode("bypass permissions on") == "bypass"
    # footer 신호 없음 → 판정 불가
    assert claude_perm_mode("user@host ~ % ls") is None
    assert claude_perm_mode("✽ Crunching… (38s)") is None


async def test_claude_prompt():
    # transcript 의 "> 내용"(가장 최근) 추출, 하단 입력박스/footer 는 건너뜀
    screen = (
        "> 첫 질문입니다\n"
        "⏺ 답변 일부...\n"
        "> 두 번째 질문\n"
        "⏺ 또 답변...\n"
        "\n"
        "> \n"                       # 라이브 입력박스(빈) — 하단 skip 대상
        "⏵⏵ auto mode on (shift+tab to cycle)\n"
    )
    assert claude_prompt(screen) == "두 번째 질문", claude_prompt(screen)
    # 테두리 안 "│ > 내용" 형태도 인식
    assert claude_prompt("│ > 박스 안 프롬프트 입니다\n행2\n행3\n행4") \
        == "박스 안 프롬프트 입니다"
    # 사용자 턴이 없으면 None
    assert claude_prompt("⏺ 출력만 있음\n행2\n행3\n행4") is None
    # 하단 N줄 안의 "> 타이핑중" 은 제출 프롬프트로 오인하지 않음
    assert claude_prompt("일반\n행2\n> 타이핑 중인 줄\n행4") is None


async def test_claude_usage():
    assert claude_usage("Context left until auto-compact: 23%") == "ctx:23%"
    assert claude_usage("Context low (8% remaining)") == "ctx:8%"
    assert claude_usage("used 45.2k tokens") == "45.2k tok"
    assert claude_usage("a normal line") is None


async def test_claude_usage_context_badge():
    # 확장 컨텍스트 모델 배지(1M)를 잔량%·토큰에 덧붙인다.
    assert claude_usage("claude-opus-4-8 (1M context)") == "1M ctx"
    # M18-A: 사용%+윈도우는 슬래시 포맷 'ctx N% / 1M'.
    assert claude_usage(
        "Context left until auto-compact: 23%  ·  opus (1M context)") == "ctx:23%/1M"
    assert claude_usage("200K context window") == "200K ctx"


async def test_screen_tail_key_and_track_repeat():
    """M17 S8: 완료 꼬리 키 + 반복 카운트."""
    from pytmuxlib.claude import screen_tail_key, track_repeat
    # 빈 줄 제거 + 우측 공백 제거(좌측 들여쓰기는 보존), 마지막 n줄.
    a = screen_tail_key("x\n\n  done line  \n\n", n=12)
    assert a == "x\n  done line"
    assert screen_tail_key("only  \n", n=12) == "only"
    # 여러 줄 꼬리 n개만
    b = screen_tail_key("\n".join(str(i) for i in range(20)), n=3)
    assert b == "17\n18\n19"
    # 동일 키 연속 → +1, 다르면 0 리셋, 빈 키는 상태 유지
    tail, n = None, 0
    tail, n = track_repeat(tail, n, "A");  assert (tail, n) == ("A", 0)
    tail, n = track_repeat(tail, n, "A");  assert (tail, n) == ("A", 1)
    tail, n = track_repeat(tail, n, "A");  assert (tail, n) == ("A", 2)
    tail, n = track_repeat(tail, n, "");   assert (tail, n) == ("A", 2)   # 빈 키 무시
    tail, n = track_repeat(tail, n, "B");  assert (tail, n) == ("B", 0)   # 변경 리셋


async def test_claude_model():
    # M14c: 실 배지 'Opus 4.8 (1M context)' + 하이픈 표기 둘 다.
    assert claude_model("Opus 4.8 (1M context) · /model to change") == "opus-4.8"
    assert claude_model("claude-sonnet-4-6") == "sonnet-4.6"
    assert claude_model("Haiku 4.5") == "haiku-4.5"
    assert claude_model("Opus") == "opus"
    assert claude_model("no model here") is None


async def test_ctx_window_tokens():
    from pytmuxlib.claude import ctx_window_tokens
    assert ctx_window_tokens("1M ctx") == 1_000_000
    assert ctx_window_tokens("200K ctx") == 200_000
    assert ctx_window_tokens("ctx:23%/1M") == 1_000_000
    assert ctx_window_tokens(None) is None
    assert ctx_window_tokens("no badge here") is None
    # 서버는 배지-only 사용문자열("1M ctx")에만 호출한다(토큰 문자열엔 미사용).


async def test_claude_usage_no_redos_on_wide_blank():
    """ReDoS 회귀: 와이드·대부분 공백 화면(200x50)에서 claude_usage 가 빠르게 끝나야
    한다. _CTX_BADGE_RE 의 선행 `\\s*` 가 거대 공백에서 O(n²) 백트래킹해 ~420ms 폭주
    하던 것을 선형화했다. 매 패널 스캔마다 도는 핫패스라 회귀 시 flush 가 마비된다."""
    import time
    blank = "? for shortcuts" + " " * 185 + ("\n" + " " * 200) * 49
    t0 = time.perf_counter()
    r = claude_usage(blank)
    dt = (time.perf_counter() - t0) * 1000
    assert r is None
    assert dt < 50, f"claude_usage 너무 느림(ReDoS 회귀?): {dt:.1f}ms"


async def test_claude_usage_excludes_streaming_delta():
    # busy footer 의 "↑/↓ N tokens" 스트리밍 델타는 사용량으로 보고하지 않는다.
    assert claude_usage("✽ Crunching… (12s · ↓ 1.9k tokens)") is None
    assert claude_usage("↑ 419 tokens") is None
    # 화살표 없는 누계는 그대로 채택
    assert claude_usage("total 12k tokens") == "12k tok"


async def test_protocol_reexports_claude():
    # 하위호환: protocol 에서도 여전히 import 가능해야 한다.
    from pytmuxlib.protocol import claude_state as cs, claude_usage as cu
    assert cs("Compacting… (esc to interrupt)") == "busy"
    assert cu("used 45.2k tokens") == "45.2k tok"


async def test_saver_hook_events_edges():
    """M16: 절감 신호 전이 → 훅 이벤트. 상승 에지에서만 1회(§8)."""
    prev = {"budget_level": 0, "pending_kind": None, "limit": False}

    def names(msg):
        return [e for e, _ in saver_hook_events(prev, msg)]

    # 0→80: warn 1회(over 아님)
    assert names({"budget_level": 80}) == ["claude-budget-warn"]
    # 80 유지: 미발화(같은 화면 여러 프레임)
    assert names({"budget_level": 80}) == []
    # 80→100: over 1회(warn 재발 안 함)
    assert names({"budget_level": 100}) == ["claude-budget-over"]
    # 하강(100→0)은 미발화, 이후 0→100 직행은 warn+over 둘 다
    assert names({"budget_level": 0}) == []
    assert names({"budget_level": 100}) == ["claude-budget-warn",
                                            "claude-budget-over"]
    # 비가역 자동액션 무장: None→{kind} 1회, 유지 미발화, 해제 후 재무장 1회
    prev2 = {"budget_level": 0, "pending_kind": None, "limit": False}
    assert [e for e, _ in saver_hook_events(
        prev2, {"claude_pending": {"kind": "autoresume", "eta": 12}})] \
        == ["claude-auto-armed"]
    assert [e for e, _ in saver_hook_events(
        prev2, {"claude_pending": {"kind": "autoresume", "eta": 8}})] == []
    assert [e for e, _ in saver_hook_events(prev2, {"claude_pending": None})] == []
    assert [e for e, _ in saver_hook_events(
        prev2, {"claude_pending": {"kind": "ctxclear"}})] == ["claude-auto-armed"]
    # 활성 패널 limit 진입 1회(상승 에지)
    prev3 = {"budget_level": 0, "pending_kind": None, "limit": False}
    m = {"active_pane": 5, "panes_claude": [{"id": 5, "claude": "limit"},
                                            {"id": 6, "claude": "idle"}]}
    assert [e for e, _ in saver_hook_events(prev3, m)] == ["claude-limit"]
    assert [e for e, _ in saver_hook_events(prev3, m)] == []  # 유지 미발화


async def test_saver_hook_events_env_payload():
    """이벤트 env 가 PYTMUX_* 컨텍스트(별칭 계정·레벨·eta)를 담는다."""
    prev = {"budget_level": 0, "pending_kind": None, "limit": False}
    evs = dict(saver_hook_events(
        prev, {"budget_level": 100, "claude_account": "wo…@woojinkim.org",
               "claude_pending": {"kind": "autoresume", "eta": 30}}))
    warn = evs["claude-budget-warn"]
    assert warn["PYTMUX_BUDGET_LEVEL"] == 100
    assert warn["PYTMUX_ACCOUNT"] == "wo…@woojinkim.org"
    armed = evs["claude-auto-armed"]
    assert armed["PYTMUX_PENDING_KIND"] == "autoresume"
    assert armed["PYTMUX_PENDING_ETA"] == 30
