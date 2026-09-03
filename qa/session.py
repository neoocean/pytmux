"""qa/session.py — 조작 어휘. QA 가 제품을 만지는 **유일한** 자리다.

★ **"실브라우저"에 해당하는 것이 우리에겐 둘이다** — 실 PTY/ConPTY 와 실 GUI 창. 이
저장소가 스스로 적어 둔 사각지대가 정확히 그 둘이다(루트 CLAUDE.md *"실 PTY·실
ConPTY·실 Claude 패널은 driver 검증 불가"* · `client/CLAUDE.md` *"GUI 배선 누락은
라이브 스크린샷만이 잡는다"*). **이 층의 존재 이유가 그 두 문장이다.**

T0 가 잡는 것은 첫째다 — 진짜 데몬 · 진짜 셸 PTY · **진짜 Textual 클라 프로세스**가
가짜 터미널 아래서 그리는 ANSI 프레임. 둘째(실 GUI 창 · Rust `pytmux-gui`)는 T3 이
잡는다 — `gui_frame` 이 그 자리다(2026-08-09 · `pytmux/pytmux-147`).

**새 하네스를 처음부터 짜지 않는다** — 재료는 이미 있다:

| 재료 | 여기서 쓰는 자리 |
| --- | --- |
| `tests/ptyshot.py` | 실 클라 화면 캡처(`capture_client`) · **다중 클라**(`clients` → `Multi`) |
| `pytmuxlib.proc` | 데몬 기동 — **pid 를 우리가 쥔다**(이름 kill 금지의 전제) |
| `pytmux.py cmd …` | 조작 — 실 CLI 표면을 그대로 지난다(제어 라인 경로) |
| `pytmux-gui --frame-dump` | **실 GUI 창의 프레임**(`gui_frame`) — 제품이 자기 드로어블에서 뜬다 |

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
from dataclasses import dataclass

from .env import ROOT, IS_WINDOWS, HomeSlot, Refused

sys.path.insert(0, os.path.join(ROOT, "tests"))
import ptyshot                                   # noqa: E402

from pytmuxlib import ipc, proc                  # noqa: E402

#: `pytmux.py ls` 의 요약줄. 트리 상태를 묻는 가장 싼 길이다.
_LS = re.compile(r"(\d+)\s+tabs?,\s*(\d+)\s+panes?")

#: `--frame-dump` 이 성공했을 때 stdout 에 내는 한 줄(`gui/src/main.rs` `save_frame`).
_DUMPED = re.compile(r"frame-dump:\s*(?P<path>.+?)\s*\((?P<w>\d+)x(?P<h>\d+)\)")

#: 실 GUI 이진을 찾는 자리. `PYTMUX_GUI` 로 지목하면 그것이 이긴다.
#: ⛔ **둘 중 «새것»을 고른다** — 이름 순으로 고르면 몇 주 묵은 `release` 를 잡고도
#:    QA 는 초록을 판다("고쳤다"와 "돈다"는 다른 사건이다 · 루트 CLAUDE.md §배포).
GUI_BINARIES = ("client/target/release/pytmux-gui", "client/target/debug/pytmux-gui")

#: 실 클라가 화면을 그릴 때까지의 **상한**(초). ⚠ 기다림이 아니라 상한이다 —
#: `capture_client` 는 `until` 이 참이 되면 그 자리에서 끝낸다(실측 0.6~1.2초).
#: T2 가 같은 것을 `DRAW_TIMEOUT` 이라는 같은 이름·같은 값으로 이미 쓰고 있었고,
#: 그쪽만 폴링이라 부하 회차에 그쪽만 살아남았다(pytmux-425·426·427).
DRAW_TIMEOUT = 25.0

#: 트레이스백 한 벌의 표시 상한(자)과, 넘칠 때 **반드시 남기는 꼬리**의 길이.
#: 꼬리가 곧 예외 타입 줄이고 그것이 지문의 재료다(`_one_traceback` 머리말).
_BLOCK_MAX = 1200
_BLOCK_TAIL = 300


class EnvBroken(Exception):
    """스택을 못 세웠다. ⛔ **`exit 0` 으로 빠지지 않는다** — 환경 구성 실패도 결함이다
    (원칙 ⓑ. 형제 프로젝트가 "서버 없으면 통과" 관행을 계승하지 않기로 한 그 자리)."""


class NotSupported(Exception):
    """이 상자에서는 잴 수 없다. ⛔ **통과가 아니라 「미검증」으로 보고한다**(원칙 ⓑ) —
    호출부가 잡아 명시 SKIP 으로 회계하고, 리포트와 종료코드가 그 사실을 말한다."""


@dataclass
class GuiShot:
    """실 GUI 창을 한 번 띄워 프레임을 뜬 결과. 판정은 시나리오가 한다."""
    binary: str
    rc: int
    stdout: str
    stderr: str
    path: str
    timed_out: bool = False
    #: 이진이 **스스로 말한** 크기(`frame-dump: <경로> (WxH)`). 실제 PNG 와 대조하면
    #: "찍었다고 말하고 안 찍은" 부류가 갈린다.
    said: tuple[int, int] | None = None

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out and os.path.exists(self.path)

    def why(self) -> str:
        """실패 사유 한 줄 — 결함 본문에 그대로 싣는다."""
        if self.timed_out:
            return f"시간 안에 안 끝나 죽였다(rc={self.rc}) · stderr: {self.stderr[:300] or '(없음)'}"
        if self.rc != 0:
            return f"rc={self.rc} · stderr: {self.stderr[:300] or '(없음)'}"
        if not os.path.exists(self.path):
            return (f"rc=0 인데 PNG 가 없다: {self.path} · "
                    f"stdout: {self.stdout[:200] or '(없음)'}")
        return ""


def _newest_source(root: str) -> tuple[float, str]:
    """GUI 이진에 **굽히는** 소스 중 가장 새것 `(mtime, 경로)`.

    ⛔ `crates/*/tests/` 는 뺀다 — 별개 테스트 타깃이라 이진에 안 들어간다. 넣으면 시험
       한 줄 고친 날 T3 이 「이진이 낡았다」로 통째로 건너뛴다(없는 문제를 만드는 관문).
    """
    newest = (0.0, "")
    for base, dirs, names in os.walk(os.path.join(root, "client", "crates")):
        dirs[:] = [d for d in dirs if d not in ("target", "tests")]
        for n in names:
            if n.endswith(".rs") or n == "Cargo.toml":
                p = os.path.join(base, n)
                try:
                    m = os.path.getmtime(p)
                except OSError:
                    continue
                if m > newest[0]:
                    newest = (m, p)
    return newest


def gui_binary() -> str:
    """이 상자에서 잴 수 있는 GUI 이진. 없으면 `NotSupported`(= 사유 붙은 SKIP).

    ⛔ **「없으면 통과」로 접지 않는다**(원칙 ⓑ) — 그리고 **낡은 이진도 안 잰다.**
       후자가 더 나쁘다: 이미 고친 결함을 다시 신고하고(늑대소년), 아직 안 고친 것에
       초록을 판다. 어느 쪽이든 그 런은 「무엇을 잰 것인가」를 말할 수 없다.
    """
    named = os.environ.get("PYTMUX_GUI")
    ext = ".exe" if IS_WINDOWS else ""
    cands = [named] if named else [os.path.join(ROOT, c) + ext for c in GUI_BINARIES]
    found = [c for c in cands if c and os.path.exists(c)]
    if not found:
        raise NotSupported(
            "실 GUI 이진이 없다 — `cd client && cargo build -p gui --bin pytmux-gui` "
            f"(또는 PYTMUX_GUI 로 지목) · 찾아본 곳: {', '.join(cands)}")
    binary = max(found, key=os.path.getmtime)
    if not named:
        src_mtime, src_path = _newest_source(ROOT)
        if src_mtime > os.path.getmtime(binary):
            raise NotSupported(
                f"GUI 이진이 소스보다 낡았다 — 지금 재면 어느 코드를 잰 것인지 말할 수 "
                f"없다. `cd client && cargo build -p gui --bin pytmux-gui` 뒤에 다시 "
                f"돌려라 (이진 {_when(os.path.getmtime(binary))} · "
                f"{os.path.relpath(src_path, ROOT)} {_when(src_mtime)})")
    if not IS_WINDOWS and sys.platform.startswith("linux") \
            and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise NotSupported("창을 만들 수 있는 디스플레이가 없다(DISPLAY·WAYLAND_DISPLAY 둘 다 비었다)")
    return binary


def _when(mtime: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))


class Session:
    """격리 슬롯 위에 선 pytmux 스택 하나."""

    def __init__(self, slot: HomeSlot, cols: int = 100, rows: int = 30,
                 python: str | None = None, ledger=None):
        self.slot = slot
        self.cols = cols
        self.rows = rows
        self.python = python or sys.executable
        self.endpoint: str | None = None
        #: 커버리지 원장(`qa/ledger.py`). ★ **여기서 적는다** — 시나리오가 따로 적으면
        #: 적기를 잊은 명령이 「안 지났다」로 세어져 원장이 조용히 거짓말한다.
        self.ledger = ledger

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
        """tmux 스타일 제어 명령(`split-window -h`·`new-window`·…). 실 CLI 를 지난다.

        ★ **지난 명령을 원장에 적는다**(`qa/ledger.py`). 적는 자리를 여기 하나로 두는
        이유는 하나다 — 시나리오가 적으면 「보냈는데 안 적은」 명령이 생기고, 원장은
        그것을 미커버로 세어 없는 구멍을 신고한다(위양성 · 원칙 ⓓ).
        ⛔ 응답도 함께 적는다: 서버가 `unknown:` 이라고 답한 것은 **지난 것이 아니다.**
        """
        out = self._run(["cmd"] + line.split()).strip()
        if self.ledger is not None:
            head = line.split()[0] if line.split() else ""
            self.ledger.record("control", head, out)
        return out

    def tree(self) -> tuple[int, int]:
        """`(탭 수, 패널 수)`. 서버가 말하는 트리를 사람이 읽는 그 요약으로 받는다."""
        out = self._run(["ls"]).strip()
        m = _LS.search(out)
        if not m:
            raise EnvBroken(f"`pytmux ls` 요약을 못 읽었다: {out!r}")
        return int(m.group(1)), int(m.group(2))

    # ── 관찰 ─────────────────────────────────────────────────────────────────
    def capture_client(self, seconds: float = DRAW_TIMEOUT, feed: bytes | None = None,
                       until=None):
        """실 Textual 클라를 가짜 터미널 아래 붙여 화면(ANSI 프레임)을 잡는다.

        반환 `(raw, alive, text)`. `alive=False` 면 캡처 시간 안에 클라가 **스스로**
        끝난 것이고 그건 곧 크래시 신호다. 캡처가 끝나면 클라는 SIGKILL 로 죽는데,
        **서버는 그래도 살아 있어야 한다** — 그것이 이 제품의 첫째 계약이라
        `T0-core-loop` 의 재부착 스텝이 그걸 곧바로 잰다.

        ⛔ **`until` 을 반드시 준다**(pytmux-425·426·427). `seconds` 는 상한이지
        기다림이 아니다 — 안 주면 이 함수는 상한을 통째로 쉬고 그때 화면에 있는 것을
        판정 재료로 삼는데, 부하가 걸린 회차에는 클라가 아직 안 그렸다. 실측
        2026-08-31 `qa-20260831-040007`(시나리오 넷 동시): 폴링하는 T2 는 통과했고
        고정 6초인 T0·T1 은 「화면 0자」 셋을 신고했다 — 셋 다 제품이 아니라 하네스가
        낸 것이다(한가할 때 첫 그리기 실측 0.6~1.2초 · 상한의 5분의 1).
        ⛔ 그리고 `until` 은 **판정과 같은 술어**여야 한다 — 약하면 덜 그린 화면을
        통과시키고(거짓 초록), 강하면 다 그린 회차까지 상한을 태운다.
        `tests/test_qa_layer.py` 의 AST 게이트가 「안 준 캡처」를 센다.
        """
        # ★ **오용 검사가 능력 검사보다 «먼저»다** — `clients()` 가 2026-08-25 에 같은
        #   순서로 고쳐진 자리인데(그 주석 참조) 이 함수만 남아 있었다. `until` 을 안 준
        #   것은 어느 OS 에서든 **호출부의 오류**지 「이 상자가 못 하는 일」이 아니고,
        #   순서가 뒤집혀 있는 동안 Windows 에서는 그 규율을 **잴 수가 없었다**
        #   (NotSupported 가 먼저 나가 test_capture_client_refuses_a_fixed_wait 이
        #   붉었다 — pytmux-444). 커버리지를 SKIP 으로 덮는 대신 잴 수 있는 쪽으로 옮긴다.
        if until is None:
            raise Refused("capture_client 에 until 이 없다 — 고정 대기는 부하 때 "
                          "안 그린 화면을 결함으로 신고한다(pytmux-425·426·427)")
        if IS_WINDOWS:
            raise NotSupported("ptyshot 은 POSIX 전용(stdlib pty)")
        raw, alive = ptyshot.capture(
            self.client_argv(),
            cols=self.cols, rows=self.rows, seconds=seconds, feed=feed,
            until=until, env={"PYTMUX_HOME": self.slot.home})
        return raw, alive, ptyshot.screen_text(raw)

    def client_argv(self) -> list[str]:
        """클라 하나를 띄우는 명령줄. `capture_client` 와 `clients` 가 **같은 것**을 쓴다."""
        return [self.python, os.path.join(ROOT, "pytmux.py")]

    def clients(self, n: int = 2):
        """실 Textual 클라 **n 개를 동시에** 같은 서버에 붙인다(`ptyshot.Multi`).

        ★ **이것이 T2 의 전부다.** `capture_client` 를 두 번 부르면 그건 동시 접속이
        아니라 **재부착**이다 — 저쪽은 상한까지 한 프로세스를 붙들고 있다 돌려주므로
        두 클라가 같은 시각에 서 있는 순간이 없다. 이 제품은 단일 서버 · 다중 클라라
        「둘이 함께 붙어 있는 동안」에만 성립하는 계약이 있고(한쪽 조작이 다른 쪽
        화면에 · 하나가 죽어도 나머지가 산다) 그것을 재는 자리가 여기다.

        ⛔ 돌려주는 것은 **호출부가 운전하는** 핸들이다. 다 쓰면 `close()` 로 회수한다
        (`with` 를 쓰면 저절로) — 우리가 쥔 pid 로만 죽는다(안전 규율 ⑵).
        """
        # ★ **오용 검사가 능력 검사보다 «먼저»다**(2026-08-25): `clients(1)` 은 어느 OS 에서든
        #   프로그래밍 오류지 「이 상자가 못 하는 일」이 아니다. 순서가 뒤집혀 있던 동안
        #   Windows 에서는 그 계약을 **잴 수가 없었고**(NotSupported 가 먼저 나가
        #   test_session_refuses_a_single_client_roster 가 붉었다) — 커버리지를 SKIP 으로
        #   덮는 대신 잴 수 있는 쪽으로 옮긴다.
        if n < 2:
            raise ValueError(f"다중 클라가 둘 미만이면 이 시나리오는 뜻이 없다: n={n}")
        if IS_WINDOWS:
            raise NotSupported("ptyshot 은 POSIX 전용(stdlib pty)")
        return ptyshot.Multi([self.client_argv()] * n, cols=self.cols, rows=self.rows,
                             env={"PYTMUX_HOME": self.slot.home})

    # ── 실 GUI 창 (T3 · pytmux/pytmux-147) ───────────────────────────────────
    def gui_frame(self, path: str, keys: str | None = None,
                  timeout: float = 90.0) -> "GuiShot":
        """실 GUI 창(Rust `pytmux-gui`)을 이 슬롯에 붙여 **프레임 한 장**을 PNG 로 뜬다.

        ★ **이것이 T3 의 전부다**(`pytmux/pytmux-147`). `capture_client` 가 실 Textual
        클라의 ANSI 화면을 잡는 것과 같은 자리이고, 여기서 잡는 것은 **GPU 드로어블에서
        읽은 진짜 창의 픽셀**이다. 그 둘이 이 저장소가 스스로 적어 둔 사각지대 둘이다.

        ⛔ **`client/scripts/*.ps1` 하네스를 부르지 않는다** — 이슈 본문이 그 여덟 개를
           재료로 들었지만(`capture_window.ps1` 외) 실측하고 나서 뒤집었다:

           ⑴ 그 하네스는 **화면을 찍는다**(`PrintWindow`) — 제품이 그 함정을 이미 알고
              있고(까만 사각형을 성공으로 돌려준다 · `gui/src/main.rs` `take_frame_dump`
              머리말) 그래서 **제품 안에 자가 덤프**(`--frame-dump`)를 두었다. 오라클이
              같은 함정을 다시 밟을 이유가 없다.
           ⑵ 그 여덟 개는 PowerShell 이라 **Windows 밖에서는 통째로 못 돈다.** 그것을
              부르는 층으로 T3 을 지으면 맥·리눅스에서 T3 은 영영 SKIP 이고, 그러면 이
              티어는 이 상자에서 **한 번도 안 도는 티어**가 된다.
           ⑶ `--frame-dump` 는 세 OS 에서 같은 코드다(`cfg` 갈림이 없다). 즉 받아들임
              기준의 「Windows 상자에서 판정한다」를 **같은 경로로** 만족한다.

           ⚠ **그래서 남는 구멍은 마우스다** — 키는 `--frame-keys` 로 넣지만 클릭·휠·
             드래그를 넣는 길은 저 여덟 개(Windows 전용)뿐이다. 조용히 덮지 않고
             시나리오가 **사유 붙은 SKIP** 으로 회계한다.

        ⛔ **`--socket` 으로 지목해서 붙인다** — 인자 없이 띄우면 서버를 못 찾았을 때
           **직접 띄우고**(`Plan::FindOrStart`) 그 순간 격리 밖의 데몬이 생긴다.
        """
        binary = gui_binary()
        endpoint = self.endpoint or self.slot.endpoint
        argv = [binary, "--socket", endpoint, f"--frame-dump={path}"]
        if keys:
            argv.append(f"--frame-keys={keys}")
        env = dict(os.environ, PYTMUX_HOME=self.slot.home)
        # ⛔ pid 를 우리가 쥔다(안전 규율 ⑵) — `run()` 은 시간이 넘쳤을 때 죽일 대상을
        #    이름으로 찾게 만든다. Popen 이라야 우리가 판 pid 하나만 겨냥할 수 있다.
        p = subprocess.Popen(argv, cwd=ROOT, env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.slot.spawned.append(p.pid)
        try:
            out, err = p.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.terminate(p.pid, force=True)
            out, err = p.communicate(timeout=10)
            timed_out = True
        m = _DUMPED.search(out or "")
        return GuiShot(
            binary=binary, rc=p.returncode, stdout=(out or "").strip(),
            stderr=(err or "").strip(), path=path, timed_out=timed_out,
            said=(int(m.group("w")), int(m.group("h"))) if m else None)

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
                out.append("Traceback (most recent call last)" + _one_traceback(block))
        return out


def _one_traceback(rest: str) -> str:
    """`Traceback (most recent call last)` 뒤에 붙은 꼬리에서 **그 트레이스백 한 벌만**
    잘라낸다.

    ⛔ 종전에는 여기서 `rest[:1200]` 로 **길이만** 잘랐다(pytmux-474 에서 그 대가를
    실측했다). 트레이스백의 마지막 줄 = 예외 타입은 `oracles._exception_line` 이
    **지문의 재료**로 쓰는 것인데, 프레임이 여섯을 넘으면 1200자 컷이 그 줄에 닿기 전에
    떨어져 **엉뚱한 줄이 지문이 된다**. 실제로 어느 결함은 3.13 의 앵커 줄
    (`~~~~~~~~~~~~~~^^^^^^`)을 제목으로 달고 트래커에 들어갔다 — 예외 타입이 어디에도
    안 남아서, 그 이슈를 읽는 사람은 **무슨 예외였는지를 알 수 없다**. 오라클이 경로·
    줄번호를 지문에서 빼려고 애쓴 그 이유가 컷 하나로 무너진 것이다.

    그래서 길이가 아니라 **경계**로 자른다: 로그의 다음 블록 머리(`\n==== …`)까지가
    한 트레이스백이다(서버 `_log_error` 와 클라 `_log_client_crash` 가 같은 머리를 쓴다).
    그 안에서만 상한을 걸되, 넘칠 때는 **머리와 꼬리를 둘 다 남긴다** — 꼬리가 곧
    예외 타입이다.
    """
    end = rest.find("\n====")
    block = rest if end < 0 else rest[:end]
    if len(block) <= _BLOCK_MAX:
        return block
    head, tail = block[:_BLOCK_MAX - _BLOCK_TAIL], block[-_BLOCK_TAIL:]
    return "%s\n… (%d자 생략) …\n%s" % (head, len(block) - _BLOCK_MAX + _BLOCK_TAIL,
                                        tail)
