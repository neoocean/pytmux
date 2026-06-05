"""Claude Code 휴리스틱(pytmuxlib/claude.py) 단위 테스트 — 상태/사용량/리밋 파서.

(docs/HANDOFF.md §11 분리로 test_protocol 에서 이리로 옮김.)"""
import datetime as dt

import harness  # noqa: F401  (경로 설정)
from pytmuxlib.claude import (claude_perm_mode, claude_prompt, claude_state,
                              claude_usage, parse_reset_delay)


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
    assert claude_usage("Context left until auto-compact: 23%") == "ctx 23%"
    assert claude_usage("Context low (8% remaining)") == "ctx 8%"
    assert claude_usage("used 45.2k tokens") == "45.2k tok"
    assert claude_usage("a normal line") is None


async def test_claude_usage_context_badge():
    # 확장 컨텍스트 모델 배지(1M)를 잔량%·토큰에 덧붙인다.
    assert claude_usage("claude-opus-4-8 (1M context)") == "1M ctx"
    assert claude_usage(
        "Context left until auto-compact: 23%  ·  opus (1M context)") == "ctx 23% 1M"
    assert claude_usage("200K context window") == "200K ctx"


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
