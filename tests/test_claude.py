"""Claude Code 휴리스틱(pytmuxlib/claude.py) 단위 테스트 — 상태/사용량/리밋 파서.

(docs/HANDOFF.md §11 분리로 test_protocol 에서 이리로 옮김.)"""
import datetime as dt

import harness  # noqa: F401  (경로 설정)
from pytmuxlib.claude import (claude_awaiting_answer, claude_model,
                              claude_perm_mode, claude_prompt,
                              claude_state, claude_usage, parse_reset_delay,
                              saver_hook_events)


async def test_claude_awaiting_answer():
    """자동 /compact 억제용: 화면이 질문/선택으로 끝나면 True(요청)."""
    # ① 대화형 선택 박스(❯ + 번호 옵션)
    assert claude_awaiting_answer("질문?\n❯ 1. Yes\n  2. No\n? for shortcuts")
    assert claude_awaiting_answer("│ ❯ 1) 진행\n│   2) 취소")
    # ② 입력박스·footer 힌트를 건너뛴 마지막 본문 줄이 물음표로 끝남
    assert claude_awaiting_answer("Do you want to proceed?\n> \n? for shortcuts")
    assert claude_awaiting_answer("계속할까요?\n\n  esc to interrupt")
    assert claude_awaiting_answer("끝줄 질문입니까？")   # 전각 물음표
    # 질문이 아니면 False — footer 의 "? for shortcuts" 에 낚이지 않는다.
    assert not claude_awaiting_answer("All done. Saved 3 files.\n? for shortcuts")
    assert not claude_awaiting_answer("작업 완료.\n> \n/help for help")
    assert not claude_awaiting_answer("")


async def test_screen_text_matches_display():
    """serverclaude.screen_text(경량 추출)가 `"\\n".join(screen.display)`와 셀 단위로
    동일해야 한다(perf #11 — wcwidth 호출 없이 같은 결과). 와이드문자(CJK)·연속셀
    포함 화면으로 못박는다."""
    import importlib
    import pyte
    # serverclaude 는 claude-code 플러그인으로 이전됨(하이픈 디렉토리 → importlib).
    screen_text = importlib.import_module(
        "pytmuxlib.plugins.claude-code.servermixin").screen_text

    s = pyte.Screen(20, 3)
    st = pyte.Stream(s)
    st.feed("AB한글CD\r\n↑ 1.2k tokens\r\nx")
    assert screen_text(s) == "\n".join(s.display)


async def test_parse_reset_delay():
    now = dt.datetime(2026, 6, 2, 14, 0, 0)
    assert parse_reset_delay("limit reached, resets at 3:00pm", now) == 3600
    # §3.2: 차단 동사(reached/exceeded/will reset)가 있어야 리밋으로 본다.
    assert parse_reset_delay("rate limit reached, resets at 15:30", now) == 5400
    assert parse_reset_delay("normal output, nothing", now) is None
    # 바뀐 정밀 규약: 차단 동사 없는 'rate limit' 단독 언급은 리밋 아님(None)
    assert parse_reset_delay("rate limit, resets at 15:30", now) is None
    # 사용률 경고(used N% … limit · resets)는 차단이 아니라 None
    assert parse_reset_delay(
        "You've used 93% of your session limit · resets 1:40pm", now) is None
    # 과거 시각이면 익일로
    d = parse_reset_delay("limit reached resets 9am", now)
    assert d is not None and d > 3600


async def test_claude_limit_precision():
    """§3.2 정밀화: 차단 배너만 limit, 사용률 경고·사용자 입력·소스/diff 는 제외."""
    from pytmuxlib.claude import claude_limit
    # 차단 배너(여러 실제 문구)
    assert claude_limit("Claude usage limit reached. Your limit will reset at 5pm")
    assert claude_limit("You've reached your usage limit.")
    assert claude_limit("rate limit exceeded — try again later")
    assert claude_limit("your weekly limit will reset Jun 13")
    # 사용률 경고(차단 아님)
    assert not claude_limit(
        "You've used 93% of your session limit · resets 1:40pm")
    # 산문/단독 언급(차단 동사 없음)
    assert not claude_limit("how do I handle a rate limit in my code?")
    assert not claude_limit("the API has a limit; remember to reset it")
    # 사용자 입력 줄(>)에 친 차단 문구는 무시(오탐 방지)
    assert not claude_limit("> what does 'usage limit reached' mean?")
    assert not claude_limit("│ > my usage limit reached yesterday")
    # 소스/diff 표시(우리 테스트 코드의 리터럴)는 무시 — 실측 캡처 오탐 사례
    assert not claude_limit(
        '57 +        assert claude_state("Claude usage limit reached")')
    assert not claude_limit('- assert "usage limit reached" in msg')
    # 단, 차단 배너가 같은 화면의 코드/입력 줄과 섞여 있어도 배너는 잡는다
    assert claude_limit(
        "> show me the error\n"
        " ✗ Claude usage limit reached. Your limit will reset at 5pm")
    # #9 F1: /usage-credits 슬래시 메뉴 도움말 줄("…when you hit a limit")은 차단 아님
    # (실측 캡처에서 idle 슬래시 메뉴를 차단으로 오인하던 지배적 오탐).
    assert not claude_limit(
        "  /usage-credits   Configure usage credits to keep working "
        "when you hit a limit")
    assert not claude_limit("│ /upgrade   reached your plan limit? upgrade")
    # #9 F2: 컨텍스트 하드스톱은 usage-limit 이 아니다(claude_context_hardstop 이 다룸).
    from pytmuxlib.claude import claude_context_hardstop
    hardstop = "Context limit reached · /compact or /clear to continue"
    assert not claude_limit(hardstop)
    assert claude_context_hardstop(hardstop)   # 별도 신호로는 여전히 잡힘


async def test_claude_api_error():
    """전송 에러(API error·rate limit·overloaded) 감지(요청 2026-06-12) — 1분 뒤 "계속"
    자동 재시도 트리거. 사용량 5h 배너("usage limit reached")는 안 잡고, 사용자 입력·
    소스/diff 줄은 제외(claude_limit 과 동일 _claude_body 가드)."""
    from pytmuxlib.claude import claude_api_error
    # 전송 에러 형태들
    assert claude_api_error("⎿ API Error: Connection error.")
    assert claude_api_error("API Error (500 internal_server_error)")
    assert claude_api_error("rate_limit_error: please retry")
    assert claude_api_error("You are rate limited. Try again shortly.")
    assert claude_api_error("Overloaded (overloaded_error)")
    # 5h 사용량 배너는 전송 에러가 아니다(autoresume 가 reset 시각으로 다룸)
    assert not claude_api_error("Claude usage limit reached. resets at 5pm")
    assert not claude_api_error("You've used 93% of your session limit")
    # 사용자 입력(>)·소스/diff 줄의 'rate limit'·'api error' 는 무시(오탐 방지)
    assert not claude_api_error("> how do I handle an api error in my code?")
    assert not claude_api_error("│ > rate limit me please")
    assert not claude_api_error('57 +    assert "API Error" in resp')
    assert not claude_api_error("- # overloaded retry logic")
    # 정상 화면은 False
    assert not claude_api_error("? for shortcuts\n⏵⏵ auto mode on")


async def test_claude_state():
    assert claude_state("blah blah\n? for shortcuts") == "idle"
    assert claude_state("Compacting… (esc to interrupt)") == "busy"
    assert claude_state("Claude usage limit reached. resets at 5pm") == "limit"
    assert claude_state("user@host ~ % ls") is None
    # §3.2 정밀화: 사용률 경고(used N% … limit)는 차단 아님 → limit 으로 안 잡음.
    # (idle footer 가 같이 있으면 idle, 없으면 None — 어느 쪽이든 "limit" 은 아님)
    assert claude_state(
        "You've used 93% of your session limit · resets 1:40pm\n"
        "? for shortcuts") == "idle"
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
    # #9 F1: 슬래시 메뉴(/usage-credits) 가 떠 있는 idle 화면을 limit 으로 오인하지 않음
    assert claude_state(
        "  /usage-credits   Configure usage credits to keep working "
        "when you hit a limit\n? for shortcuts") == "idle"
    # #9 F2: 컨텍스트 하드스톱은 "limit"(사용량) 이 아니다 — 별도 하드스톱 신호로 처리
    assert claude_state(
        "Context limit reached · /compact or /clear to continue") != "limit"


async def test_claude_perm_mode():
    # auto(진짜 auto 모드: 모든 동작 자동, 분류기 안전검사) — "auto mode on" 만.
    assert claude_perm_mode("⏵⏵ auto mode on (shift+tab to cycle)") == "auto"
    # accept(acceptEdits: 편집·기본 FS 만) — auto 와 **다른** 모드(둘 다 ⏵⏵ 글리프라
    # 문구로 가른다). 예전엔 둘 다 auto 로 봐 새 세션이 accept 에서 멈췄다(사용자 보고).
    assert claude_perm_mode("⏵⏵ accept edits on (shift+tab to cycle)") == "accept"
    assert claude_perm_mode("auto-accept edits on (shift+tab to cycle)") == "accept"
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
    # 단위 없는 작은 수("1 token" 등)는 컨텍스트 지표가 아니라 노이즈 → 무시(요청:
    # 상태줄에 "1 tok" 오표시). 단위(k/M)가 붙은 값만 컨텍스트 토큰으로 채택.
    assert claude_usage("Read 1 token from cache") is None
    assert claude_usage("processed 987 tokens") is None
    assert claude_usage("ctx 1.2M tokens") == "1.2M tok"


async def test_claude_usage_context_badge():
    # 확장 컨텍스트 모델 배지(1M)를 잔량%·토큰에 덧붙인다.
    assert claude_usage("claude-opus-4-8 (1M context)") == "1M ctx"
    # M18-A: 사용%+윈도우는 슬래시 포맷 'ctx N% / 1M'.
    assert claude_usage(
        "Context left until auto-compact: 23%  ·  opus (1M context)") == "ctx:23%/1M"
    assert claude_usage("200K context window") == "200K ctx"


async def test_parse_usage():
    """M19: 실 /usage 패널(usage.txt)에서 세션·주간 한도 %·리셋 추출."""
    import os
    from pytmuxlib.claude import parse_usage
    fix = os.path.join(os.path.dirname(__file__), "fixtures", "claude", "usage.txt")
    u = parse_usage(open(fix, encoding="utf-8").read())  # 실 raw 레이아웃(줄 분리)
    assert u["session"] == {"pct": 2, "reset": "2pm (Asia/Seoul)"}, u
    assert u["week_all"]["pct"] == 14
    assert u["week_all"]["reset"] == "Jun 13 at 3am (Asia/Seoul)"
    assert u["week_sonnet"]["pct"] == 0
    assert parse_usage("nothing here") is None
    # 좁은 레이아웃(헤더에 Resets 붙고 % 다음줄)도 처리
    u2 = parse_usage("Current session · Resets 5am (Asia/Seoul)\n  ██  10% used")
    assert u2["session"] == {"pct": 10, "reset": "5am (Asia/Seoul)"}, u2
    # 헤더만 있고 % 없으면 그 항목은 누락(짝 안 맞음)
    assert parse_usage("Current session\nResets 5am") is None


async def test_parse_inline_limit():
    """footer 인라인 한도("used 93% of your session limit · resets …")에서 % 추출."""
    from pytmuxlib.claude import parse_inline_limit
    u = parse_inline_limit(
        "You've used 93% of your session limit · resets 1:40pm (Asia/Seoul)"
        " · /usage-credits to request more")
    assert u["session"]["pct"] == 93, u
    assert "1:40pm" in u["session"]["reset"], u
    u2 = parse_inline_limit("you've used 41% of your weekly limit · resets Jun 11")
    assert u2["week_all"]["pct"] == 41, u2
    assert u2["week_all"]["reset"] == "Jun 11", u2
    assert parse_inline_limit("just normal output") is None


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


async def test_fmt_unknown_update():
    """§3.7: Claude fg + 파서 미인식이 지속되면 unknown=True(가시화). 인식 성공이나
    Claude 아님이면 즉시 해제."""
    from pytmuxlib.claude import fmt_unknown_update
    AFTER = 20.0
    # 파서가 인식(recognized=True) → 의심 없음(앵커 무관)
    assert fmt_unknown_update(None, True, True, 100.0, AFTER) == (None, False)
    # Claude 아님(fg_claude=False) → 의심 없음
    assert fmt_unknown_update(None, False, False, 100.0, AFTER) == (None, False)
    # Claude fg + 미인식: 처음 본 시각 기록, 아직 임계 미만 → unknown=False
    first, unk = fmt_unknown_update(None, False, True, 100.0, AFTER)
    assert first == 100.0 and unk is False
    # 같은 의심 지속, 임계 직전 → 여전히 False
    assert fmt_unknown_update(first, False, True, 119.9, AFTER) == (100.0, False)
    # 임계 도달 → unknown=True
    assert fmt_unknown_update(first, False, True, 120.0, AFTER) == (100.0, True)
    # 도중에 인식되면 first 도 리셋(다음 의심은 새로 카운트)
    assert fmt_unknown_update(first, True, True, 121.0, AFTER) == (None, False)


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


async def test_claude_state_usage_canonical_import():
    # Claude 휴리스틱은 pytmuxlib.claude 에서 직접 가져온다(S5a 에서 protocol.py 의
    # 죽은 하위호환 re-export 를 제거 — 코어 protocol 이 더는 claude 를 import 하지 않음).
    from pytmuxlib.claude import claude_state as cs, claude_usage as cu
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
