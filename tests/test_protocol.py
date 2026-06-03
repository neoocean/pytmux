"""프로토콜/설정 헬퍼 단위 테스트 (서버/클라 기동 불필요)."""
import datetime as dt
import tempfile

import harness  # noqa: F401  (경로 설정)
import pytmux


async def test_key_to_ctrl_bytes():
    assert pytmux._key_to_ctrl_bytes("ctrl+a") == b"\x01"
    assert pytmux._key_to_ctrl_bytes("ctrl+b") == b"\x02"


async def test_tmux_key_to_textual():
    assert pytmux._tmux_key_to_textual("C-a") == "ctrl+a"
    assert pytmux._tmux_key_to_textual("ctrl+x") == "ctrl+x"


async def test_parse_reset_delay():
    now = dt.datetime(2026, 6, 2, 14, 0, 0)
    assert pytmux.parse_reset_delay("limit reached, resets at 3:00pm", now) == 3600
    assert pytmux.parse_reset_delay("rate limit, resets at 15:30", now) == 5400
    assert pytmux.parse_reset_delay("normal output, nothing", now) is None
    # 과거 시각이면 익일로
    d = pytmux.parse_reset_delay("limit reached resets 9am", now)
    assert d is not None and d > 3600


async def test_load_config():
    cp = tempfile.mktemp(suffix=".conf")
    with open(cp, "w") as f:
        f.write("# c\nset prefix C-a\nset mouse off\nset mode-keys emacs\n"
                "set status-bg blue\nset status-left L#S\n"
                "bind | split-window -h\nalias v split-window -h\n"
                "hook after-new-window rename-window H\n")
    cfg = pytmux.load_config(cp)
    assert cfg["prefix"] == "ctrl+a"
    assert cfg["mouse"] is False
    assert cfg["mode_keys"] == "emacs"
    assert cfg["status_bg"] == "blue" and cfg["status_left"] == "L#S"
    assert cfg["bindings"]["|"] == "split-window -h"
    assert cfg["aliases"]["v"] == "split-window -h"
    assert cfg["hooks"]["after-new-window"] == "rename-window H"


async def test_claude_state():
    from pytmuxlib.protocol import claude_state
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
    # 오탐 방지: 도구 출력 말줄임표는 busy 아님
    assert claude_state("⎿  … +38 lines (ctrl+o to expand)") is None


async def test_claude_usage():
    from pytmuxlib.protocol import claude_usage
    assert claude_usage("Context left until auto-compact: 23%") == "ctx 23%"
    assert claude_usage("Context low (8% remaining)") == "ctx 8%"
    assert claude_usage("used 45.2k tokens") == "45.2k tok"
    assert claude_usage("a normal line") is None
