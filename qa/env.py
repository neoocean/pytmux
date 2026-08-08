"""qa/env.py — 격리 홈 슬롯과 빌드 스탬프.

⛔ **이 파일이 지키는 것은 안전 규율이고 그것이 이 층의 1급 제약이다**(루트 CLAUDE.md
   「프로세스 이름으로 일괄 kill 금지」). QA 는 데몬을 자주 띄우고 내리므로 격리가
   설계의 **처음**에 있어야 한다 — 그 사고 3회는 전부 "내가 띄운 것"과 "사용자가 지금
   쓰고 있는 라이브 데몬"을 구분하지 못해서 났다. 규율을 코드로 못 박는다:

   ⑴ **홈 슬롯**(`PYTMUX_HOME=<스크래치>/qa-<runId>`) — 소켓·토큰·db·captures 가 전부
      그 아래로 간다. space 의 포트 슬롯(8190~8199)과 같은 자리다.
   ⑵ **이름으로 죽이지 않는다** — 우리가 spawn 한 pid 와 우리 홈 안의 `*.ptyhost.pid`
      만 겨냥한다. `pkill`·`killall`·`Get-Process` 는 이 층에 **한 줄도 없다**
      (`tests/test_qa_layer.py` 가 기계로 지킨다).
   ⑶ **검증도 스코프 안에서** — 정리됐는지는 전역 프로세스 목록이 아니라 **내 홈의 상태
      파일**로 판정한다. 전역으로 확인하면 화면에 남는 것이 사용자의 라이브 데몬이라
      "정리 실패"로 오판하고, 그 오판이 일괄 kill 로 확대된다.
   ⑷ **라이브 부착은 만들지 않았다** — opt-in 플래그조차 두지 않는다. 사용자의 라이브
      데몬은 그 사람이 지금 일하는 세션이다(설계 결정 · `pytmux/qa-system` §2).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pytmuxlib import ipc      # noqa: E402  (경로 규약의 정본 — 베껴 적지 않는다)

IS_WINDOWS = os.name == "nt"


def _slot_base() -> str:
    """슬롯이 사는 자리. 스크래치 밖으로는 절대 안 나간다(아래 `_guard_scratch`).

    ⛔ **macOS 의 `$TMPDIR` 을 쓰면 안 된다** — `/var/folders/7h/fr7…gn/T/` 가 49자라
    그 아래 소켓 경로가 `AF_UNIX` 한계(104바이트)를 넘는다. 실측(2026-08-06, 이 층의 첫
    런): 서버가 `OSError: AF_UNIX path too long` 로 죽고 QA 는 "데몬이 12초 안에 안 떴다"
    만 보여 **제품 결함처럼 보였다**. 그래서 제품이 자기 런타임을 두는 자리와 같은
    규약(`/tmp/pytmux-<uid>`)을 쓴다 — 짧고, 이 상자에서 이미 검증된 자리다.
    """
    if IS_WINDOWS:
        return os.path.join(tempfile.gettempdir(), "pytmux-qa")
    return f"/tmp/pytmux-qa-{os.getuid()}"


SLOT_BASE = _slot_base()
#: 소켓 경로 예산. `sockaddr_un.sun_path` 는 macOS 104 · Linux 108 바이트다. 넘으면
#: **기동이 조용히 실패**하고 증상이 "서버가 안 뜬다"로만 보인다 — 그래서 슬롯을 만들 때
#: 미리 재고 거절한다(실패는 빠르고 이유가 분명해야 한다).
SUN_PATH_MAX = 104


def new_run_id(now: float | None = None) -> str:
    """런 식별자. 시각에서 짓는다 — 같은 초에 두 번 돌 일이 없고, 산출물 디렉터리
    이름과 트래커의 `run.runId` 가 같은 값이라 사람이 둘을 이어 볼 수 있다."""
    return time.strftime("qa-%Y%m%d-%H%M%S", time.localtime(now or time.time()))


class Refused(Exception):
    """슬롯을 만들 수 없다. ⛔ **조용히 성공하지 않는다** — 격리를 확신할 수 없으면
    아무것도 띄우지 않는 편이 낫다(환경 구성 실패도 결함이다)."""


def _guard_scratch(home: str) -> None:
    """홈이 스크래치 슬롯 자리인지 확인한다. 여기가 마지막 방어선이다 — 아래 어느 하나만
    어긋나도 그다음 `kill-server` 가 **남의 서버**를 내린다."""
    home = os.path.abspath(home)
    base = os.path.abspath(SLOT_BASE)
    try:
        inside = os.path.commonpath([home, base]) == base and home != base
    except ValueError:                       # 드라이브가 다르면 공통 경로가 없다(Windows)
        inside = False
    if not inside:
        raise Refused(f"QA 홈은 {base} 아래여야 한다: {home}")
    if not os.path.basename(home).startswith("qa-"):
        raise Refused(f"QA 홈 이름은 qa- 로 시작해야 한다: {home}")
    sock = os.path.join(home, "state", "default.sock")
    if not IS_WINDOWS and len(sock.encode("utf-8")) >= SUN_PATH_MAX:
        raise Refused(
            f"소켓 경로가 AF_UNIX 한계({SUN_PATH_MAX}B)를 넘는다 — 슬롯 이름을 줄여라: "
            f"{len(sock)}B {sock}")
    if os.path.exists(sock) and ipc.probe(sock):
        raise Refused(f"이 슬롯에 이미 서버가 살아 있다(남의 런과 겹친다): {sock}")


class HomeSlot:
    """`PYTMUX_HOME` 격리 슬롯. `with` 블록을 벗어나면 환경을 원래대로 되돌린다.

    ⚠ 환경변수를 세우는 것만으로는 격리가 아니다 — 그 값이 실제로 먹었는지를
    `oracles.home_isolated` 가 **매 스텝** 다시 잰다. 값이 새면 우리는 사용자의 라이브
    데몬을 운전하고 있는 것이고, 그건 결함 중 가장 비싼 것이다.
    """

    def __init__(self, run_id: str, base: str | None = None):
        self.run_id = run_id
        self.home = os.path.join(os.path.abspath(base or SLOT_BASE), run_id)
        self._saved: dict[str, str | None] = {}
        self._entered = False
        #: 우리가 직접 띄운 pid 만 담는다. 이름 매칭으로 넓히지 않는다(규율 ⑵).
        self.spawned: list[int] = []

    # ── 컨텍스트 ─────────────────────────────────────────────────────────────
    def __enter__(self) -> "HomeSlot":
        _guard_scratch(self.home)
        os.makedirs(self.home, exist_ok=True)
        # NO_COLOR 는 에이전트 셸이 심는다 — 그대로 두면 Textual 이 Monochrome 필터를
        # 물려 실 클라 캡처가 통째로 무너진다(루트 CLAUDE.md 의 실측 110건과 같은 자리).
        # 제품 결함이 아닌 것을 결함으로 신고하지 않으려면 여기서 지운다.
        for k, v in (("PYTMUX_HOME", self.home), ("NO_COLOR", None),
                     ("PYTMUX", None), ("LC_PYTMUX", None)):
            self._saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._entered = True
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._entered = False
        return False

    # ── 경로 ─────────────────────────────────────────────────────────────────
    @property
    def state_dir(self) -> str:
        return os.path.join(self.home, "state")

    @property
    def endpoint(self) -> str:
        """이 슬롯의 엔드포인트. **제품에게 물어서** 얻는다 — 경로 규약이 바뀌면 QA 가
        같이 따라가야 하고, 안 따라가면 조용히 남의 소켓을 잰다."""
        if not self._entered:
            raise Refused("슬롯 밖에서 endpoint 를 물었다(PYTMUX_HOME 이 안 서 있다)")
        return ipc.default_endpoint()

    @property
    def state_base(self) -> str:
        """`error.log`·`client.crash.log` 의 프리픽스."""
        return ipc.state_base(self.endpoint)

    # ── 정리 (규율 ⑵⑶) ───────────────────────────────────────────────────────
    def ptyhost_pids(self) -> list[int]:
        """**내 홈 안의** pid 파일만 읽는다."""
        out = []
        try:
            names = os.listdir(self.state_dir)
        except OSError:
            return out
        for n in names:
            if not n.endswith(".ptyhost.pid"):
                continue
            try:
                out.append(int(open(os.path.join(self.state_dir, n),
                                    encoding="utf-8").read().strip()))
            except (OSError, ValueError):
                pass
        return out

    def reap(self) -> None:
        """남은 것을 **pid 로만** 회수한다. 이름 매칭으로 넓히지 않는다."""
        from pytmuxlib import proc          # 지연 import — 정리 경로에서만 필요하다
        for pid in list(self.spawned) + self.ptyhost_pids():
            try:
                proc.terminate(pid, force=True)
            except Exception:
                pass                        # 이미 죽은 pid 는 정상이다

    def residue(self, timeout: float = 3.0, step: float = 0.1) -> list[str]:
        """정리 판정 — **내 홈의 상태 파일**로만 한다(규율 ⑶).

        ⚠ 소켓 파일은 종료 직후 잠깐 남는다(`kill-server` 는 0.2초 지연 shutdown).
        그래서 「살아 있는가」는 `probe` 로 묻고 파일 잔존만으로 판정하지 않는다 —
        2026-07-30 에 그 잔존을 두 번 오독했다.

        ⛔ **이 오라클은 한 번 틀렸다(2026-08-06 · 이 층의 첫 런).** 종전에는 지연을
           안 기다리고 `stop()` 직후에 한 번만 재서 **매 런 S3 을 냈다** — 위양성이다
           (원칙 ⓓ. 늑대소년이 된 QA 는 꺼진다). 실측하면 0.25초에 이미 사라진다:
           `probe` 가 0.00s True → 0.25s False. 그래서 **수렴을 기다린 뒤** 판정한다.
           고친 이유를 지우지 않는 것은 다음 사람이 "왜 폴링인가"를 다시 묻지 않게 하려는
           것이다 — 저 지연은 제품의 의도이지 결함이 아니다.
        """
        end = time.time() + timeout
        while True:
            left = []
            sock = os.path.join(self.state_dir, "default.sock")
            if os.path.exists(sock) and ipc.probe(sock):
                left.append(f"서버가 아직 응답한다: {sock}")
            for pid in self.ptyhost_pids():
                if _alive(pid):
                    left.append(f"pty-host 가 살아 있다: pid {pid}")
            if not left or time.time() >= end:
                return left
            time.sleep(step)

    def wipe(self) -> None:
        """슬롯 디렉터리를 지운다(런 산출물은 `qa/out/` 에 따로 있다)."""
        shutil.rmtree(self.home, ignore_errors=True)


def _alive(pid: int) -> bool:
    if IS_WINDOWS:
        return True                          # 신호로 물을 수 없다 — 살아 있다고 본다
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ── 빌드 스탬프 ───────────────────────────────────────────────────────────────
#
# **웹 공개에는 이 스탬프가 필수다** — 어느 개정의 QA 인지 없으면 리포트가 뜻을 잃는다.
# 형제 프로젝트가 하듯 head CL + 미제출 편집 여부를 찍는다.
#
# ⛔ p4 가 없는 상자(공개 git 클론)에서 이걸 **결함으로 신고하지 않는다.** 그러면 그
#    상자에서 도는 모든 런이 같은 이슈를 다시 열고, 위양성이 QA 를 끈다(원칙 ⓓ).
#    대신 리포트 첫 줄이 「제출본 QA 아님」이라고 말한다.

def _p4(args: list[str], timeout: float = 12.0) -> str | None:
    try:
        r = subprocess.run(["p4"] + args, cwd=ROOT, capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def stamp() -> dict:
    """`{build, head, dirty, stamped}` — 이 런이 **어느 개정**을 잰 것인가."""
    head = None
    out = _p4(["changes", "-m1", "-s", "submitted", f"{ROOT}/..."])
    if out:
        parts = out.split()
        if len(parts) >= 2 and parts[0] == "Change" and parts[1].isdigit():
            head = int(parts[1])
    opened = _p4(["opened", f"{ROOT}/..."])
    dirty = bool(opened and opened.strip())
    return {
        "build": f"p4-{head}" if head else "unstamped",
        "head": head,
        "dirty": dirty,
        "stamped": head is not None,
    }
