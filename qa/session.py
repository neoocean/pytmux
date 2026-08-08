"""qa/session.py — 조작 어휘. QA 가 제품을 만지는 **유일한** 자리다.

★ **"실브라우저"에 해당하는 것이 우리에겐 둘이다** — 실 PTY/ConPTY 와 실 GUI 창. 이
저장소가 스스로 적어 둔 사각지대가 정확히 그 둘이다(루트 CLAUDE.md *"실 PTY·실
ConPTY·실 Claude 패널은 driver 검증 불가"* · `client/CLAUDE.md` *"GUI 배선 누락은
라이브 스크린샷만이 잡는다"*). **이 층의 존재 이유가 그 두 문장이다.**

T0 가 잡는 것은 첫째다 — 진짜 데몬 · 진짜 셸 PTY · **진짜 Textual 클라 프로세스**가
가짜 터미널 아래서 그리는 ANSI 프레임. 실 GUI 창(Rust `pytmux-gui`)은 T3 몫이고 아직
없다(`pytmux/qa-system` §5 · 후속 이슈).

**새 하네스를 처음부터 짜지 않는다** — 재료는 이미 있다:

| 재료 | 여기서 쓰는 자리 |
| --- | --- |
| `tests/ptyshot.py` | 실 클라 화면 캡처(`capture_client`) |
| `pytmuxlib.proc` | 데몬 기동 — **pid 를 우리가 쥔다**(이름 kill 금지의 전제) |
| `pytmux.py cmd …` | 조작 — 실 CLI 표면을 그대로 지난다(제어 라인 경로) |

⛔ **`.claude/skills/run-pytmux/driver.py` 는 안 쓴다.** 그쪽이 더 편한 어휘를 갖고
   있지만 `.claude/` 는 git 미러에서 제외라 공개 클론에 **없다** — QA 층이 거기 기대면
   그 상자에서 통째로 못 돈다. 그리고 driver 가 주는 것은 서버가 합성한 **텍스트 근사**
   이고 우리가 봐야 하는 것은 클라가 실제로 그린 프레임이다. 둘은 상보적이라 driver 는
   대화형 스킬로 남는다(`tests/ptyshot.py` 머리말이 같은 구분을 적어 뒀다).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

from .env import ROOT, IS_WINDOWS, HomeSlot

sys.path.insert(0, os.path.join(ROOT, "tests"))
import ptyshot                                   # noqa: E402

from pytmuxlib import ipc, proc                  # noqa: E402

#: `pytmux.py ls` 의 요약줄. 트리 상태를 묻는 가장 싼 길이다.
_LS = re.compile(r"(\d+)\s+tabs?,\s*(\d+)\s+panes?")


class EnvBroken(Exception):
    """스택을 못 세웠다. ⛔ **`exit 0` 으로 빠지지 않는다** — 환경 구성 실패도 결함이다
    (원칙 ⓑ. 형제 프로젝트가 "서버 없으면 통과" 관행을 계승하지 않기로 한 그 자리)."""


class NotSupported(Exception):
    """이 상자에서는 잴 수 없다. ⛔ **통과가 아니라 「미검증」으로 보고한다**(원칙 ⓑ) —
    호출부가 잡아 명시 SKIP 으로 회계하고, 리포트와 종료코드가 그 사실을 말한다."""


class Session:
    """격리 슬롯 위에 선 pytmux 스택 하나."""

    def __init__(self, slot: HomeSlot, cols: int = 100, rows: int = 30,
                 python: str | None = None):
        self.slot = slot
        self.cols = cols
        self.rows = rows
        self.python = python or sys.executable
        self.endpoint: str | None = None

    # ── 수명 ─────────────────────────────────────────────────────────────────
    def start(self, timeout: float = 12.0) -> None:
        """데몬을 띄운다. **pid 를 우리가 받는다** — 그래야 정리가 이름 매칭으로 안 번진다."""
        self.endpoint = self.slot.endpoint
        pid = proc.spawn_detached(proc.server_argv(self.endpoint, python=self.python))
        self.slot.spawned.append(pid)
        end = time.time() + timeout
        while time.time() < end and not ipc.probe(self.endpoint):
            time.sleep(0.05)
        if not ipc.probe(self.endpoint):
            raise EnvBroken(f"데몬이 {timeout:.0f}초 안에 안 떴다: {self.endpoint}")

    def stop(self) -> None:
        """정상 경로(`kill-server`)로 내리고, 남은 것만 pid 로 회수한다."""
        try:
            self._run(["kill-server", "--yes"], timeout=15)
        except Exception:
            pass                                 # 이미 죽었으면 아래 회수만 남는다
        self.slot.reap()

    # ── 조작 ─────────────────────────────────────────────────────────────────
    def _run(self, args: list[str], timeout: float = 20.0) -> str:
        r = subprocess.run([self.python, os.path.join(ROOT, "pytmux.py")] + args,
                           cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise EnvBroken(f"pytmux {' '.join(args)} → rc={r.returncode}\n{r.stderr.strip()}")
        return r.stdout

    def control(self, line: str) -> str:
        """tmux 스타일 제어 명령(`split-window -h`·`new-window`·…). 실 CLI 를 지난다."""
        return self._run(["cmd"] + line.split()).strip()

    def tree(self) -> tuple[int, int]:
        """`(탭 수, 패널 수)`. 서버가 말하는 트리를 사람이 읽는 그 요약으로 받는다."""
        out = self._run(["ls"]).strip()
        m = _LS.search(out)
        if not m:
            raise EnvBroken(f"`pytmux ls` 요약을 못 읽었다: {out!r}")
        return int(m.group(1)), int(m.group(2))

    # ── 관찰 ─────────────────────────────────────────────────────────────────
    def capture_client(self, seconds: float = 6.0, feed: bytes | None = None):
        """실 Textual 클라를 가짜 터미널 아래 붙여 화면(ANSI 프레임)을 잡는다.

        반환 `(raw, alive, text)`. `alive=False` 면 캡처 시간 안에 클라가 **스스로**
        끝난 것이고 그건 곧 크래시 신호다. 캡처가 끝나면 클라는 SIGKILL 로 죽는데,
        **서버는 그래도 살아 있어야 한다** — 그것이 이 제품의 첫째 계약이라
        `T0-core-loop` 의 재부착 스텝이 그걸 곧바로 잰다.
        """
        if IS_WINDOWS:
            raise NotSupported("ptyshot 은 POSIX 전용(stdlib pty)")
        raw, alive = ptyshot.capture(
            [self.python, os.path.join(ROOT, "pytmux.py")],
            cols=self.cols, rows=self.rows, seconds=seconds, feed=feed,
            env={"PYTMUX_HOME": self.slot.home})
        return raw, alive, ptyshot.screen_text(raw)

    def error_blocks(self) -> list[str]:
        """서버·클라 로그에 남은 **트레이스백** 블록.

        서버는 데몬(stderr=/dev/null)이라 예외를 로그에만 남긴다 — 그래서 이걸 안 보면
        "초록불 + 서버가 매 프레임 터짐"이 성립한다. `tests/harness.py` 가 매 테스트
        끝에 거는 만능가드와 **같은 판정**을 QA 층에서도 건다.
        """
        base = ipc.state_base(self.endpoint or self.slot.endpoint)
        out = []
        for path in (base + ".error.log", base + ".client.crash.log"):
            try:
                txt = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for block in txt.split("Traceback (most recent call last)")[1:]:
                out.append("Traceback (most recent call last)" + block[:1200])
        return out
