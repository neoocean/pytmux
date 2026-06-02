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
