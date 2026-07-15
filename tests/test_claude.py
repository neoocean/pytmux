"""Claude Code 휴리스틱(pytmuxlib/claude.py) 단위 테스트 — 상태/사용량/리밋 파서.

(docs/internal/HANDOFF.md §11 분리로 test_protocol 에서 이리로 옮김.)"""
import datetime as dt
import importlib
import re
import time

import harness  # noqa: F401  (경로 설정)
import pytmuxlib.claude as claude_mod
from pytmuxlib.claude import (claude_awaiting_answer, claude_account,
                              claude_context_pct, claude_model,
                              claude_model_badge,
                              claude_perm_mode, claude_prompt, claude_state,
                              claude_usage, fmt_long_turn_badge,
                              parse_reset_delay, parse_usage,
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


async def test_parse_reset_ts():
    """/usage 리셋 표기 → epoch(현재 5h/주간 창 역산·남은 시간 표시용). 세션
    시각-만 표기, 주간 월·일 표기, 24시간, 연도 롤오버, 미파싱 None 을 못박는다."""
    from pytmuxlib.claude import parse_reset_ts
    now = dt.datetime(2026, 6, 12, 18, 0, 0)
    # 세션: 시각만(12시간제, 분 유무) — 오늘 그 시각.
    assert parse_reset_ts("6:59pm (Asia/Seoul)", now) == \
        dt.datetime(2026, 6, 12, 18, 59).timestamp()
    assert parse_reset_ts("7pm (Asia/Seoul)", now) == \
        dt.datetime(2026, 6, 12, 19, 0).timestamp()
    # 지난 시각 → 다음날(parse_reset_delay 와 동일 규약).
    assert parse_reset_ts("3am", now) == \
        dt.datetime(2026, 6, 13, 3, 0).timestamp()
    # 24시간제.
    assert parse_reset_ts("19:30", now) == \
        dt.datetime(2026, 6, 12, 19, 30).timestamp()
    # 주간: 월·일(+시각) 표기.
    assert parse_reset_ts("Jun 13 at 3am (Asia/Seoul)", now) == \
        dt.datetime(2026, 6, 13, 3, 0).timestamp()
    assert parse_reset_ts("Dec 31 at 11pm", now) == \
        dt.datetime(2026, 12, 31, 23, 0).timestamp()
    # 약간 지난 월일은 과거 그대로(호출부가 stale 판단) — 내년으로 점프하지 않음.
    assert parse_reset_ts("Jun 11 at 3am", now) == \
        dt.datetime(2026, 6, 11, 3, 0).timestamp()
    # 한참(>200일) 지난 월일 = 연도 롤오버(12월 말 실측의 'Jan 2') → 내년.
    dec = dt.datetime(2026, 12, 30, 18, 0, 0)
    assert parse_reset_ts("Jan 2 at 3am", dec) == \
        dt.datetime(2027, 1, 2, 3, 0).timestamp()
    # 미파싱/없음 → None.
    assert parse_reset_ts(None, now) is None
    assert parse_reset_ts("", now) is None
    assert parse_reset_ts("soon", now) is None
    assert parse_reset_ts("99:99", now) is None


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
    # 전송 에러 형태들 — 실측 4/4 가 `⏺ API Error: …` 배너였다(코퍼스 감사 2026-06-16).
    assert claude_api_error("⎿ API Error: Connection error.")
    assert claude_api_error("API Error (500 internal_server_error)")
    assert claude_api_error("⏺ API Error: Server is temporarily limiting "
                            "requests (not your usage limit) · Rate limited")
    assert claude_api_error("⏺ API Error: Overloaded")
    # 산문에 못 나오는 JSON 에러 타입은 배너 없이도 잡는다
    assert claude_api_error("rate_limit_error: please retry")
    assert claude_api_error("Overloaded (overloaded_error)")
    # 네트워크 무응답 배너(실측 — captures/playground.local .claude 프레임): "No response
    # from API … · Retrying in <시간> · check your network". 동반 문구(Retrying/check your
    # network)와 함께 잡고 1분 뒤 "계속" 재시도를 건다(요청 2026-06-21).
    assert claude_api_error("No response from API   · Retrying in 2m 12s · check your\nnetwork")
    assert claude_api_error("✻ No response from API · Retrying in 1s · check your network")
    assert claude_api_error("No response from API · check your network")
    # 맨 "No response from API"(동반 문구 없음)는 산문일 수 있어 안 잡는다(오탐 방지)
    assert not claude_api_error("Claude returned no response from API documentation page.")
    # 5h 사용량 배너는 전송 에러가 아니다(autoresume 가 reset 시각으로 다룸)
    assert not claude_api_error("Claude usage limit reached. resets at 5pm")
    assert not claude_api_error("You've used 93% of your session limit")
    # 사용자 입력(>)·소스/diff 줄의 'rate limit'·'api error' 는 무시(오탐 방지)
    assert not claude_api_error("> how do I handle an api error in my code?")
    assert not claude_api_error("│ > rate limit me please")
    assert not claude_api_error('57 +    assert "API Error" in resp')
    assert not claude_api_error("- # overloaded retry logic")
    # §3.7 코퍼스 감사 회귀(2026-06-16): 배너 없는 **맨단어** 'rate limit'/'overloaded'
    # 가 idle 산문/소스에 박혀 오탐하던 사례를 차단한다. 실측 FP: Claude 가 pytmux
    # i18n 카탈로그(명령 설명)를 표시한 idle 프레임의 "…rate limit) on/off…" 줄.
    assert not claude_api_error("      error·rate limit) on/off (auto-retry "
                                "on|off|toggle, default on)\",")
    assert not claude_api_error("You are rate limited. Try again shortly.")
    # tool-use 설명 산문의 'api error'(콜론/괄호 미동반)도 배너 아님 → 무시
    assert not claude_api_error("Bash: Grep captures for api error / rate limit / thinking")
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
    # 신형 idle footer(2026-06): "(shift+tab to cycle)" 접미가 빠지고 ⏵⏵ 프롬프트만
    # 남는다 — 옛 "mode on (shift" 앵커로는 None(미인식)이 돼 '포맷 미인식' 경고가
    # 멀쩡한 세션에 오발화했다(사용자 보고 2026-06-22). 접미 없는 footer 도 idle.
    assert claude_state(
        "❯\n⏵⏵ auto mode on · 2 shells · ↵ for agents · ↓ to manage") == "idle"
    assert claude_state("⏵⏵ plan mode on · 1 shell") == "idle"
    assert claude_state("⏵⏵ accept edits on · 4 shells · ↓ to manage") == "idle"
    # 후속 변형(2026-06-25): acceptEdits 가 "accept edits on"→"accept edits is on" 으로
    # 바뀌어 옛 "accept edits on" 앵커를 빗나갔다. 모드 이름(접미 무관) 앵커로 흡수한다.
    assert claude_state("⏵⏵ accept edits is on · 2 shells · ↵ for agents") == "idle"
    # ⏵⏵ 없이 모드 이름만 남은 변형(샘플이 잘려 프리픽스가 사라져도 살아남는다).
    assert claude_state("accept edits is on") == "idle"
    assert claude_state("plan mode on") == "idle"
    assert claude_state("bypass permissions") == "idle"
    # 모드 표시 없는 입력 박스(힌트만) 도 idle.
    assert claude_state("│ > │\n? for shortcuts") == "idle"
    # 일반 셸 출력은 여전히 None(모드 이름/힌트 부재) — 오인식 안 함.
    assert claude_state("total 8\ndrwxr-xr-x  2 user staff") is None
    # busy 와 idle footer 가 함께 있으면 busy 우선
    assert claude_state(
        "✽ Flowing… (8m 4s · ↑ 21.1k tokens)\n"
        "⏵⏵ auto mode on (shift+tab to cycle)") == "busy"
    # 폰트/터미널에 따라 스피너 글리프가 `*`/`·` 로 렌더되는 변형
    assert claude_state("* Baking… (10s · ↑ 419 tokens · still thinking)") == "busy"
    # 시간 표시 없이도 "still thinking" 으로 busy 판정
    assert claude_state(
        "⏵⏵ auto mode on (shift+tab to cycle)\n"
        "* Baking… still thinking") == "busy"
    # §3.4: `↑/↓ N tokens` 단독은 더는 busy 신호가 아니다 — 응답 종료 후 transcript
    # 의 토큰 델타 잔재가 idle 화면을 busy 로 오인시키던 오탐 제거(토큰 화살표는
    # 토큰 누계 파싱 전용). idle footer 가 있으니 idle.
    assert claude_state(
        "⏵⏵ auto mode on (shift+tab to cycle)\n"
        "↑ 419 tokens") == "idle"
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


async def test_claude_prompt_marks():
    """제출된 프롬프트 줄 인덱스(esc ctrl+↑/↓ 점프 목표). 열 0 의 `> `/`❯ `+내용만 —
    들여쓴 인용·라이브 입력박스(테두리 `│` 선행)·마커만 있는 줄은 제외한다."""
    from pytmuxlib.claude import claude_prompt_marks as f
    texts = ["> 첫 프롬프트", "", "⏺ 답변 1",
             "  > 들여쓴 인용(프롬프트 아님)",
             "> 둘째 프롬프트", "  이어지는 줄(마커 없음)", "⏺ 답변 2",
             "╭──────────╮", "│ > 라이브 입력 │", "╰──────────╯"]
    assert f(texts) == [0, 4], f(texts)
    # 신형 마커(❯ + 비분리공백)도 인식
    assert f(["❯\xa0신형 마커 프롬프트"]) == [0]
    # 마커만/내용 없음/구분 공백 없음 → 프롬프트 아님
    assert f([">", "> ", ">x", "❯"]) == []
    # 빈 입력/None 방어
    assert f([]) == [] and f(None) == []


async def test_claude_usage():
    # 표시는 사용량%(=100-잔량). "left/remaining N%" 는 잔량이라 100-N 으로 뒤집어 보인다
    # (2026-06-16 요청: 잔여 7% → 사용 93%). claude_context_pct 는 여전히 잔량을 낸다.
    assert claude_usage("Context left until auto-compact: 23%") == "ctx:77%"
    assert claude_usage("Context low (8% remaining)") == "ctx:92%"
    assert claude_usage("used 45.2k tokens") == "45.2k tok"
    assert claude_usage("a normal line") is None
    # 단위 없는 작은 수("1 token" 등)는 컨텍스트 지표가 아니라 노이즈 → 무시(요청:
    # 상태줄에 "1 tok" 오표시). 단위(k/M)가 붙은 값만 컨텍스트 토큰으로 채택.
    assert claude_usage("Read 1 token from cache") is None
    assert claude_usage("processed 987 tokens") is None
    assert claude_usage("ctx 1.2M tokens") == "1.2M tok"


async def test_claude_usage_context_badge():
    # 확장 컨텍스트 모델 배지(1M)를 사용량%·토큰에 덧붙인다.
    assert claude_usage("claude-opus-4-8 (1M context)") == "1M ctx"
    # M18-A: 사용%+윈도우는 슬래시 포맷 'ctx N% / 1M'(잔량 23% → 사용 77%).
    assert claude_usage(
        "Context left until auto-compact: 23%  ·  opus (1M context)") == "ctx:77%/1M"
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
    # 회귀(2026-06-22): 스크롤백/대화에 섞인 무관한 수가 버전으로 오인돼 'sonnet-255'
    # 처럼 완전히 잘못 표기되던 버그. 실 버전은 1~2자리 성분뿐이라 3자리+ 숫자나
    # 날짜 접미는 버전으로 받지 않는다(계열만, 또는 None 으로 폴백).
    assert claude_model("Sonnet 255") == "sonnet"
    assert claude_model("Sonnet-255") == "sonnet"
    assert claude_model("claude-sonnet-4-5-20250929") == "sonnet-4.5"
    # 대화 내용에 모델명이 언급돼도 하단 배지(마지막 매치)를 반환해야 한다.
    # Claude Code UI: 대화가 위, 배지가 아래.
    screen = ("I use Haiku 4.5 for quick tasks\n"
              "but Sonnet is better for this.\n"
              "\n"
              "Opus 4.8 (1M context) · /model to change\n"
              "? for shortcuts")
    assert claude_model(screen) == "opus-4.8"
    # /usage 한도 카테고리('(Sonnet only)')는 활성 모델이 아니므로 제외한다 —
    # 사용자가 /usage 패널을 열고 있을 때 'sonnet'으로 오귀속되던 것 방지(2026-06-22).
    assert claude_model("Current week (all models)\n  41% used\n"
                        "Current week (Sonnet only)\n  6% used") is None
    # 카테고리 라벨과 실제 배지가 함께 있으면 배지(마지막 비카테고리 매치)를 잡는다.
    assert claude_model("Current week (Sonnet only)\n6% used\n"
                        "Opus 4.8 (1M context) · /model to change") == "opus-4.8"


async def test_claude_model_badge_ignores_prose():
    """claude_model_badge 는 실 푸터 배지 서명('(… context)'·'/model')이 붙은 매치만
    인정한다 — 라이브 화면 스크랩이 대화/온보딩 본문의 모델명 언급을 활성 모델로
    오인해 상태줄이 엉뚱한 모델로 튀던 버그(2026-07-04: 팝업/프로브는 opus 인데
    상태줄은 'fable-5')를 막는다."""
    # 실 배지(서명 있음) → 인식.
    assert claude_model_badge(
        "Opus 4.8 (1M context) · /model to change") == "opus-4.8"
    assert claude_model_badge("Sonnet 4.6 (200K context)") == "sonnet-4.6"
    # 버그 재현: 온보딩/환경 본문의 모델 ID 설명. 서명이 없으므로 배지로 안 잡힌다
    # → None → 호출부가 /usage 프로브 실 모델로 폴백(팝업과 일치).
    assert claude_model_badge(
        "Model IDs — Fable 5: 'claude-fable-5', Opus 4.8: 'claude-opus-4-8'.\n"
        "This session uses fable-5 as an example id.") is None
    assert claude_model_badge("I switched to Haiku 4.5 earlier") is None
    assert claude_model_badge("no model here") is None
    # 'large context window' 처럼 닫는 괄호 없는 context 언급은 서명 아님 → 배제.
    assert claude_model_badge("Fable 5 has a large context window") is None
    # 본문 언급 + 실 배지 공존 → 배지(서명 있는 마지막 매치)를 잡는다. 본문의
    # 'fable-5' 는 무시.
    assert claude_model_badge(
        "earlier I used claude-fable-5 for a demo\n"
        "Opus 4.8 (1M context) · /model to change") == "opus-4.8"
    # 카테고리 서명 제외는 유지(서명이 'context)'여도 'only' 카테고리면 계열이 아님).
    assert claude_model_badge("Current week (Sonnet only)\n41% used") is None


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
    """에스컬레이션 신호 전이 → 훅 이벤트. 상승 에지에서만 1회(§8)."""
    # 비가역 자동액션 무장: None→{kind} 1회, 유지 미발화, 해제 후 재무장 1회
    prev2 = {"pending_kind": None, "limit": False}
    assert [e for e, _ in saver_hook_events(
        prev2, {"claude_pending": {"kind": "autoresume", "eta": 12}})] \
        == ["claude-auto-armed"]
    assert [e for e, _ in saver_hook_events(
        prev2, {"claude_pending": {"kind": "autoresume", "eta": 8}})] == []
    assert [e for e, _ in saver_hook_events(prev2, {"claude_pending": None})] == []
    assert [e for e, _ in saver_hook_events(
        prev2, {"claude_pending": {"kind": "ctxclear"}})] == ["claude-auto-armed"]
    # 활성 패널 limit 진입 1회(상승 에지)
    prev3 = {"pending_kind": None, "limit": False}
    m = {"active_pane": 5, "panes_claude": [{"id": 5, "claude": "limit"},
                                            {"id": 6, "claude": "idle"}]}
    assert [e for e, _ in saver_hook_events(prev3, m)] == ["claude-limit"]
    assert [e for e, _ in saver_hook_events(prev3, m)] == []  # 유지 미발화


async def test_saver_hook_events_env_payload():
    """이벤트 env 가 PYTMUX_* 컨텍스트(별칭 계정·eta)를 담는다."""
    prev = {"pending_kind": None, "limit": False}
    evs = dict(saver_hook_events(
        prev, {"claude_account": "wo…@woojinkim.org",
               "claude_pending": {"kind": "autoresume", "eta": 30}}))
    armed = evs["claude-auto-armed"]
    assert armed["PYTMUX_PENDING_KIND"] == "autoresume"
    assert armed["PYTMUX_PENDING_ETA"] == 30
    assert armed["PYTMUX_ACCOUNT"] == "wo…@woojinkim.org"


async def test_fmt_long_turn_badge_switches_to_hours():
    """장기 턴 경고 배지: 1시간 미만은 '⚠ 분:초', 1시간 이상은 '⚠ 시:분'으로 표시한다
    (사용자 요청 2026-06-17 — 1시간 넘으면 분이 60+ 로 커져 읽기 어려움)."""
    assert fmt_long_turn_badge(0) == "⚠ 0:00"
    assert fmt_long_turn_badge(75) == "⚠ 1:15"        # 1분 15초
    assert fmt_long_turn_badge(613) == "⚠ 10:13"      # 10분 13초
    assert fmt_long_turn_badge(3599) == "⚠ 59:59"     # 경계 직전 → 분:초
    assert fmt_long_turn_badge(3600) == "⚠ 1:00"      # 정확히 1시간 → 시:분
    # 사용자 예시: 화면의 ⚠75:13(=75분 13초=4513초)은 시:분으로 ⚠1:15 가 된다.
    assert fmt_long_turn_badge(4513) == "⚠ 1:15"
    assert fmt_long_turn_badge(7505) == "⚠ 2:05"      # 2시간 5분 5초


async def test_warn_info_text_classifies_warn_kinds():
    """통합 '경고' 탭 본문(TokenLogScreen._warn_info_text): **구조적 kind**(서버
    claude_warn_kind)로 종류를 판별해 제목·상황·할일을 만든다(2026-06-19 i18n 전수조사
    — 옛 한글 부분문자열 판별 대체). 첫 줄엔 현재 배지 문자열을 그대로 둔다."""
    screens = importlib.import_module("pytmuxlib.plugins.claude-code.screens")
    i18n = screens.i18n
    wit = screens.TokenLogScreen._warn_info_text
    # kind 별 제목은 i18n 키와 동치(로케일 무관 단언).
    t1, l1 = wit("long_turn", "⚠ 75:13")
    assert t1 == i18n.t("claude.warn_long_title") and l1[0] == "⚠ 75:13"
    t2, _ = wit("repeat", "⚠ 동일 결과 3회 반복 — 루프 의심")
    assert t2 == i18n.t("claude.warn_repeat_title")
    t3, _ = wit("fmt_unknown", "⚠ Claude 포맷 미인식 — 추적 중단")
    assert t3 == i18n.t("claude.warn_fmt_title")
    # 구버전 서버 호환: kind=None 이면 한글 문자열로 폴백 판별.
    assert wit(None, "Claude 포맷 미인식")[0] == i18n.t("claude.warn_fmt_title")
    assert wit(None, "여러 번 반복 — 루프 의심")[0] == i18n.t("claude.warn_repeat_title")


async def test_warn_info_text_localized_en_no_hangul():
    """en 로케일에서 [경고] 탭 본문·제목에 한글이 새지 않는다(i18n 전수조사 회귀).
    ko 로케일에선 한글 안내가 그대로 나온다."""
    import re
    screens = importlib.import_module("pytmuxlib.plugins.claude-code.screens")
    i18n = screens.i18n
    wit = screens.TokenLogScreen._warn_info_text
    badge = screens.TokenLogScreen._warn_badge
    prev = i18n.get_locale() if hasattr(i18n, "get_locale") else None
    try:
        i18n.set_locale("en")
        for kind in ("long_turn", "repeat", "fmt_unknown"):
            title, lines = wit(kind, "⚠ x")
            blob = title + "\n" + "\n".join(lines[1:])  # 0=배지(아래 별도 검증)
            assert not re.search(r"[가-힣]", blob), \
                f"en 본문에 한글 누출({kind}): {blob!r}"
        # 첫 줄 배지도 로케일화: 반복/포맷-미인식은 en 에서 한글이 없어야(장기 턴은
        # 서버 문자열이라 호출부가 넘긴 그대로 — 언어중립 '⚠ M:SS').
        assert not re.search(r"[가-힣]",
                             badge("repeat", "⚠ 동일 결과 3회 반복 — 루프 의심", 3))
        assert not re.search(r"[가-힣]", badge("fmt_unknown", "⚠ 한글 서버 문자열"))
        i18n.set_locale("ko")
        title, lines = wit("fmt_unknown", "⚠ x")
        assert re.search(r"[가-힣]", title), "ko 제목은 한글이어야"
        assert re.search(r"[가-힣]", badge("repeat", "⚠ x", 3)), \
            "ko 배지는 한글이어야"
    finally:
        i18n.set_locale(prev or "en")


async def test_warn_badge_click_routes_to_token_log_warn_tab():
    """상태줄 ⚠ 경고 배지 클릭(_open_warn_info)은 통합 토큰 팝업의 '경고' 탭을 연다
    (open_token_log('warn')). 경고가 없으면 팝업을 열지 않는다(2026-06-17 통합)."""
    cc = importlib.import_module("pytmuxlib.plugins.claude-code")

    routed = []

    class _App:
        def __init__(self, warn):
            self.status = type("S", (), {"claude_warn": warn})()

        def open_token_log(self, initial=None):
            routed.append(initial)

        def display_message(self, *a, **k):
            pass

    cc._open_warn_info(_App("⚠ 75:13"))
    assert routed == ["warn"], routed
    routed.clear()
    cc._open_warn_info(_App(None))      # 경고 없음 → 팝업 안 염
    assert routed == [], routed


async def test_statusbar_unknown_usage_badge_when_no_measurement():
    """§10-F: Claude 를 막 시작해 /usage 실측(tok5h_pct·week_sonnet_pct)이 아직
    안 왔을 때, 활성 Claude 패널이면 좌하단에 'Unknown' 사용량 배지(`?%/5h used`)를
    즉시 띄운다(요청 2026-06-18). 실측이 들어오면 그 자리에서 숫자로 갱신되고,
    비-Claude 패널이면 사용량 배지가 아예 안 뜬다."""
    from rich.segment import Segment  # noqa: F401
    from pytmuxlib import i18n
    cs = importlib.import_module("pytmuxlib.plugins.claude-code.clientstatus")

    class _S:
        pass

    def segtext(status, w=100):
        status.focus_btn = None     # 코어 StatusBar 필드(테스트는 직접 설치)
        segs = []
        cs.render_segs(status, segs, w, w0=0)
        return "".join(s.text for s in segs)

    i18n.set_locale("en")
    try:
        # 활성 Claude · 실측 미도착 → 'Unknown' 배지(`?%/5h used`)
        status = _S()
        cs.init_defaults(status)
        status.claude_active = True
        assert "?%/5h used" in segtext(status), segtext(status)
        # 실측 도착(tok5h_pct=6) → 숫자로 갱신, '?%' 사라짐
        status2 = _S()
        cs.init_defaults(status2)
        status2.claude_active = True
        status2.tok5h_pct = 6
        t2 = segtext(status2)
        assert "6%/5h used" in t2 and "?%" not in t2, t2
        # 비-Claude 패널 → 사용량 배지 자체가 없음
        status3 = _S()
        cs.init_defaults(status3)
        status3.claude_active = False
        assert "/5h" not in segtext(status3)
    finally:
        i18n.set_locale("ko")        # 모듈 기본(다른 테스트가 ko 출력에 의존)


async def test_statusbar_ctx_follows_active_pane_switch():
    """좌하단 ctx(claude_usage)는 **활성 패널 전환 시 새 패널 값으로 교체**된다 — 새
    패널의 ctx 가 아직 안 잡혀(None) 와도 이전 패널 값을 유지하지 않는다(사용자 보고
    2026-06-17: 서로 다른 Claude 세션 패널을 옮겨 다녀도 같은 ctx 가 붙어 보임). 단
    **같은 패널** 내 일시적 None 엔 종전대로 마지막 값 유지(스크롤 등 깜빡임 방지)."""
    cs = importlib.import_module("pytmuxlib.plugins.claude-code.clientstatus")

    class _S:
        pass

    status = _S()
    cs.init_defaults(status)
    # 패널 5: ctx 잡힘 → 표시
    cs.absorb(status, {"active_pane": 5, "claude_active": True,
                       "claude_usage": "ctx:49%/150K"})
    assert status.claude_usage == "ctx:49%/150K"
    # 같은 패널 5, ctx 가 일시적으로 빈 값 → 마지막 값 유지(깜빡임 방지)
    cs.absorb(status, {"active_pane": 5, "claude_active": True,
                       "claude_usage": None})
    assert status.claude_usage == "ctx:49%/150K"
    # 패널 6 으로 전환, 새 패널 ctx 아직 None → 이전 패널 값을 끊고 교체(None)
    cs.absorb(status, {"active_pane": 6, "claude_active": True,
                       "claude_usage": None})
    assert status.claude_usage is None, status.claude_usage
    # 패널 6 의 ctx 가 잡히면 그 패널 값으로
    cs.absorb(status, {"active_pane": 6, "claude_active": True,
                       "claude_usage": "ctx:12%/150K"})
    assert status.claude_usage == "ctx:12%/150K"


async def test_statusbar_model_always_shown_when_active():
    """시나리오: 모델 배지는 claude_active 이면 claude_usage 없어도 항상 표시.

    ① Claude 시작 직후 — claude_model 은 잡혔지만 claude_usage(ctx%)가 아직 None 이어도
       모델명이 상태줄에 나타난다.
    ② 패널 전환 시 — 이전 패널의 모델이 새 패널 스캔 전에 잔류하지 않는다(absorb 가
       pane_changed 에서 claude_model 을 None 으로 초기화).
    ③ 모델 변경 반영 — /model 명령으로 모델이 바뀌면 다음 status 에서 새 모델명이 표시된다.
    """
    from rich.segment import Segment  # noqa: F401
    from pytmuxlib import i18n
    cs = importlib.import_module("pytmuxlib.plugins.claude-code.clientstatus")

    class _S:
        pass

    def segtext(status, w=100):
        status.focus_btn = None
        segs = []
        cs.render_segs(status, segs, w, w0=0)
        return "".join(s.text for s in segs)

    i18n.set_locale("en")
    try:
        # ① claude_usage=None 이어도 모델명 표시
        s = _S()
        cs.init_defaults(s)
        s.claude_active = True
        s.claude_model = "opus-4.8"
        # claude_usage 는 기본값(None) 그대로
        t = segtext(s)
        assert "opus-4.8" in t, f"모델 배지 누락(usage=None): {t!r}"

        # ② 패널 전환 — 이전 모델이 잔류하지 않음
        s2 = _S()
        cs.init_defaults(s2)
        # 패널 5 에서 opus 스캔
        cs.absorb(s2, {"active_pane": 5, "claude_active": True,
                        "claude_model": "opus-4.8"})
        assert s2.claude_model == "opus-4.8"
        # 패널 6 으로 전환, 새 패널 모델 미스캔(None)
        cs.absorb(s2, {"active_pane": 6, "claude_active": True,
                        "claude_model": None})
        assert s2.claude_model is None, f"stale 모델 잔류: {s2.claude_model!r}"

        # ③ 모델 변경 반영 — 새 모델이 도착하면 즉시 갱신
        cs.absorb(s2, {"active_pane": 6, "claude_active": True,
                        "claude_model": "sonnet-4.6"})
        assert s2.claude_model == "sonnet-4.6"
        t3 = segtext(s2)
        assert "sonnet-4.6" in t3, f"변경 모델 배지 누락: {t3!r}"
    finally:
        i18n.set_locale("ko")


async def test_statusbar_badge_pink_when_viewing_remote():
    """항목6(2026-06-22): 활성 탭이 원격 병합 탭이면(viewing_remote=True) 모델·사용량
    배지 배경이 탁한 분홍(REMOTE_PINK_DIM)으로 — 로컬일 땐 청색(secondary). 원격 탭의
    분홍 외곽선과 의미를 맞춰 클릭 컨텍스트를 예측 가능하게 한다."""
    cs = importlib.import_module("pytmuxlib.plugins.claude-code.clientstatus")
    from pytmuxlib.clientutil import REMOTE_PINK_DIM

    class _S:
        pass

    def model_seg(viewing_remote):
        s = _S()
        cs.init_defaults(s)
        s.claude_active = True
        s.claude_model = "opus-4.8"
        s.tok5h_pct = 20
        s.focus_btn = None
        segs = []
        cs.render_segs(s, segs, 100, w0=0, viewing_remote=viewing_remote)
        return next(sg for sg in segs if "opus-4.8" in sg.text)

    def bg_hex(seg):
        return seg.style.bgcolor.get_truecolor().hex.lower()

    # 로컬: 분홍 아님(청색 secondary).
    assert bg_hex(model_seg(False)) != REMOTE_PINK_DIM.lower()
    # 원격: REMOTE_PINK_DIM.
    assert bg_hex(model_seg(True)) == REMOTE_PINK_DIM.lower()


# ── §5.9: 정규식 ReDoS(파국적 백트래킹) 회귀 가드 ──────────────────────────────
# claude.py 의 파서는 **신뢰할 수 없는 화면 텍스트**(Claude Code TUI 출력·붙여넣기)에
# 정규식을 돌린다. 패턴에 중첩 수량자/모호 교대가 들어가면 적대적 입력이 지수/2차
# 시간으로 폭주해(ReDoS) flush 루프를 멈출 수 있다. 아래는 ① 모듈의 모든 컴파일된
# 패턴과 ② 주요 공개 파서를 큰 적대적 입력에 돌려 **선형 시간(상한 내 완료)** 을
# 고정한다 — 향후 패턴 편집이 파국적 백트래킹을 들여오면 시간 초과로 잡힌다.

# 적대적 입력 빌더(패턴들이 노리는 토큰을 반복/혼합해 백트래킹 유발 시도). 길이는
# 선형이면 µs~ms, 파국적이면 수초+ 라 둘을 또렷이 가른다.
def _redos_payloads(n):
    return [
        " " * n,                       # 공백 런(앵커 \s* 백트래킹)
        "│ " * n,                      # 입력/턴 프리픽스 반복
        "9" * n,                       # 숫자 런(%/토큰/시각 파서)
        ("9" * (n // 2)) + "%",        # 숫자 런 뒤 % (context/usage)
        ("9" * (n // 4)) + " tokens",  # 토큰 베이트
        "a" * n,                       # 단일 문자 런(이메일/모델/단어)
        ("a@" * (n // 2)) + "b",       # 이메일류 교대(계정 파서)
        ("Opus " * (n // 5)),          # 모델 파서 베이트
        ("1:" * (n // 2)) + "30pm",    # 리셋 시각 파서 베이트
        ("x" * (n // 2) + "\n") * 2,   # 다행
    ]


async def test_regex_patterns_no_catastrophic_backtracking():
    """claude.py 의 모든 모듈 레벨 컴파일 정규식이 ~40KB 적대적 입력에서도 상한
    내 완료(파국적 백트래킹 부재)."""
    patterns = [(name, v) for name, v in vars(claude_mod).items()
                if isinstance(v, re.Pattern)]
    assert len(patterns) >= 20, f"패턴 수집 누락? {len(patterns)}"
    payloads = _redos_payloads(40000)
    BUDGET = 0.5   # 선형이면 한참 못 미침; 파국적이면 수초+ 라 여유 큰 경계
    for name, pat in patterns:
        for s in payloads:
            t0 = time.perf_counter()
            pat.search(s)
            pat.findall(s)
            dt_s = time.perf_counter() - t0
            assert dt_s < BUDGET, \
                f"{name} 가 적대적 입력에서 {dt_s:.3f}s — ReDoS 의심"


async def test_public_parsers_linear_on_hostile_screen():
    """주요 공개 파서가 거대 적대적 화면(반복 프리픽스·숫자·이메일류)에서도 빠르게
    반환(폭주 없음). 결과값이 아니라 **완료 시간**을 가드한다."""
    hostile = "\n".join([
        "│ " * 2000,
        "9" * 8000 + "% context left",
        ("a@" * 4000) + "b 's Organization",
        "Opus " * 1600,
        ("9" * 4000) + " tokens",
        "Current session  " + "9" * 4000 + "% used",
        "Resets " + "x" * 8000,
        "> " + "y" * 8000,
    ])
    fns = [claude_state, claude_usage, parse_usage, claude_context_pct,
           claude_model, claude_account, claude_prompt, claude_awaiting_answer,
           claude_perm_mode]
    t0 = time.perf_counter()
    for fn in fns:
        fn(hostile)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"공개 파서 적대적 입력 처리 {elapsed:.3f}s — 폭주 의심"
