---
name: run-pytmux
description: Build, run, drive, screenshot, and test pytmux (Python/Textual tmux-like terminal multiplexer). Use when asked to start pytmux, launch the server, run its tests, interact with a pane, send keys/commands, or take a screenshot of its UI.
---

pytmux is a tmux-like terminal multiplexer: a background **server (daemon)** owns shell PTYs and renders panes; a Textual **client** draws them, connected over a unix socket. The agent path does **not** need a TTY or tmux — drive the running app headlessly via `.claude/skills/run-pytmux/driver.py`, which connects to a real server like a client, sends input, and composites the server-rendered panes into a text "screenshot."

All paths below are relative to the pytmux project dir (the `<unit>`).

## Prerequisites

- Python 3.12 (POSIX: macOS or Linux — uses the stdlib `pty`; Windows uses ConPTY via `pywinpty`).
- No tmux, no display server, no TTY needed for the agent path.

## Setup

```bash
python3 -m pip install -r requirements.txt   # textual, pyte, wcwidth
```

No separate build step — it runs from source (`pytmux.py` + `pytmuxlib/`).

## Run (agent path) — driver.py

Drive a real server headlessly and capture screenshots. The driver launches its **own** server on a private temp socket and kills it on exit.

```bash
python3 .claude/skills/run-pytmux/driver.py smoke
```

This launches a server, runs a command, splits a pane, runs a command in the new pane, opens a new tab, and writes three composited screenshots to `/tmp/pytmux-shots/` (printing `02-split.txt` and `✅ PASS`). Single-shot:

```bash
python3 .claude/skills/run-pytmux/driver.py shot --cmd "echo DRIVER_OK; date" --out /tmp/pytmux-shots/shot.txt
```

Screenshots land in **`/tmp/pytmux-shots/`** — plain text with Unicode box borders, one file per state.

| subcommand | what it does |
|---|---|
| `smoke [--out DIR]` | launch → command → split → new tab; 3 screenshots; asserts pane output |
| `shot [--cmd CMD] [--out FILE]` | launch → run one command → 1 screenshot |

**Library API** (for custom flows — import the same `PytmuxDriver`):

```python
import asyncio, sys
sys.path.insert(0, ".claude/skills/run-pytmux")
from driver import PytmuxDriver

async def main():
    d = PytmuxDriver(cols=100, rows=30)
    await d.start()
    await d.control("split-window -h")     # tmux-style control line
    await d.send("echo hello\n")           # keystrokes to active pane
    await d.refresh()                      # collect server screen/layout msgs
    print(d.screen_text())                 # composited full screen
    d.screenshot("/tmp/pytmux-shots/x.txt")
    await d.stop()
asyncio.run(main())
```

`d.control(line)` accepts any pytmux control command (`split-window -h`, `new-window`, `rename-window foo`, `restart-server`, …); `d.send(text)` sends raw keystrokes; `d.refresh()` then `d.screen_text()`/`d.screenshot()` capture the screen.

## External control (no driver needed)

Against any running server (the default one if you omit `--socket`):

```bash
python3 pytmux.py ls                          # → "2 tabs, 2 panes"
python3 pytmux.py cmd new-window              # send a control command
python3 pytmux.py kill-server                 # stop server + all shells
```

## Run (human path)

```bash
python3 pytmux.py        # interactive Textual client; spawns/attaches the daemon
```

Opens the full-screen TUI in your terminal (needs a real TTY — **not usable headless**, which is why the agent path above uses `driver.py`). `prefix` is `Ctrl-b`; `prefix d` detaches (shells keep running), `prefix x` kills a pane.

## Test

```bash
python3 tests/run.py        # headless test suite → "162 passed, 0 failed"
python3 tests/run.py test_restart   # one module
```

## Gotchas

- **Nesting is refused.** A pane shell gets `$PYTMUX` set; launching `pytmux attach` inside a pytmux pane (or `pytmux.py` with `$PYTMUX`/`$LC_PYTMUX` set) is blocked to avoid recursive render. Bypass: `unset PYTMUX LC_PYTMUX`. The driver is unaffected (it speaks the socket protocol, doesn't re-launch pytmux).
- **The server is a daemon.** It survives client/driver exit. The default socket is `/tmp/pytmux-<uid>/default.sock`; stray servers linger across sessions — `pgrep -af pytmux.py` and `python3 pytmux.py kill-server` to clear. The driver isolates itself on a temp socket and cleans up its own server, so it won't touch a default-socket server you have running.
- **`split-window -h` is a stacked (top/bottom) split here** — the server returns full-width pane rects (`[0,0,100,15]` / `[0,14,100,16]`), and the driver composites them faithfully (stacked, not side-by-side). Don't mistake the vertical stack for a compositing bug.
- **The "screenshot" is server-rendered text**, composited from the `layout` message's pane rects + each pane's `screen` rows, with drawn box borders. It's the text the real client would paint — there is no pixel buffer.
- **Server-code changes need a restart.** `server.py`/`model.py`/`client.py`/`pty_backend.py` edits only take effect after `kill-server` + relaunch — or the work-preserving `restart-server` (`python3 pytmux.py cmd restart-server`), which re-execs the server while keeping live shells/PTYs.
- **cwd detection differs by OS** — Linux reads `/proc/<pid>/cwd`, macOS shells out to `lsof`; harmless if it fails.

## Troubleshooting

- **`RuntimeError: pytmux 서버 기동 실패`** (driver `start()` times out): deps missing or import error in the server. Verify `python3 -c "import textual, pyte, wcwidth"` and run `python3 tests/run.py` to surface the import failure.
- **Driver screen looks empty / only borders**: the command output hadn't flushed before capture. Increase the wait — `await d.refresh(2.0)` (or re-run `shot`); the server streams at a fixed flush rate.
- **`ls` says nothing / connection refused**: no server on that socket. Start one (`driver.py smoke`, or `python3 pytmux.py` for the TTY client) or point `--socket` at the right path.
