"""qa/oracles.py — 상시 불변식. **매 스텝 뒤에** 다시 잰다.

시나리오가 묻는 것("분할했더니 패널이 둘인가")과 다르다 — 여기 있는 것은 *언제나* 참이어야
하는 것이고, 그래서 어느 스텝이 깼는지가 곧 귀속이 된다.

⛔ **위양성이 최대의 적이다**(원칙 ⓓ). 늑대소년이 된 QA 는 꺼진다. 그래서 오라클은 둘뿐이고
   둘 다 **다르게 해석될 여지가 없는 것**만 본다. 오라클을 고칠 때는 **왜 틀렸는지**를 여기
   주석에 남긴다(지우지 않는다 — 형제 프로젝트가 자기가 틀렸던 두 번을 남겨 둔 그 규율).

오라클이 실제로 무는지는 `tests/test_qa_layer.py` 가 **결함을 심어** 확인한다(메타 QA).
"""
from __future__ import annotations

import os

from .findings import Finding

try:                                     # 경로 규약의 정본(베껴 적지 않는다)
    from pytmuxlib import ipc
except ImportError:                      # pragma: no cover - env.py 가 sys.path 를 세운다
    ipc = None


def no_traceback(ctx) -> list[Finding]:
    """서버·클라가 예외를 로그로만 삼키지 않았는가.

    서버는 데몬이라 stderr 가 /dev/null 이다 — 이 오라클이 없으면 **"초록불 + 서버가 매
    프레임 터짐"** 이 성립한다. `tests/harness.py` 의 만능가드(2026-07-25 신설)와 같은
    판정을 런 층에서도 건다.
    """
    blocks = ctx.session.error_blocks()
    if not blocks:
        return []
    # 첫 줄의 예외 타입만 지문의 재료로 쓴다 — 경로·줄번호가 들어가면 같은 결함이 개정마다
    # 다른 지문을 얻어 재발 판정이 안 선다.
    head = _exception_line(blocks[0])
    return [Finding(
        scenario=ctx.scenario, oracle="server/no_traceback", key=head,
        title=f"서버 로그에 트레이스백이 남는다 — {head}",
        severity="S1", step=ctx.current,
        expected="서버·클라 로그에 트레이스백이 없다",
        actual=f"{len(blocks)}건:\n{blocks[0][:600]}",
    )]


def _exception_line(block: str) -> str:
    """트레이스백 블록의 마지막 줄(= 예외 타입과 메시지)."""
    for line in reversed(block.strip().splitlines()):
        s = line.strip()
        if s and not s.startswith(("File \"", "Traceback", "During handling",
                                   "The above exception")):
            return s[:160]
    return "알 수 없는 예외"


def home_isolated(ctx) -> list[Finding]:
    """⛔ **격리가 실제로 서 있는가.** 이 층에서 가장 비싼 결함이 여기 있다.

    `PYTMUX_HOME` 을 세우는 것과 그것이 **먹는 것**은 다른 사건이다. 값이 새면 우리는
    사용자가 지금 일하고 있는 라이브 데몬을 운전하게 되고, 그다음 `kill-server` 가 그
    세션을 날린다(실제로 같은 날 3번 났다 · 루트 CLAUDE.md). 그러니 매 스텝 다시 잰다:

    ⑴ 제품이 보는 홈이 우리 슬롯인가 ⑵ 제품이 고른 엔드포인트가 그 안에 있는가.
    """
    if ipc is None:                                  # pragma: no cover
        return []
    home = ctx.slot.home
    seen = ipc.pytmux_home()
    if seen != os.path.abspath(home):
        return [_leak(ctx, "PYTMUX_HOME", os.path.abspath(home), str(seen))]
    endpoint = ipc.default_endpoint()
    if not _under(endpoint, home):
        return [_leak(ctx, "엔드포인트", f"{home} 아래", endpoint)]
    return []


def _under(path: str, root: str) -> bool:
    if path.startswith("tcp:"):                      # Windows 는 포트파일이 상태 디렉터리에 있다
        path = ipc.default_state_dir()
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) \
            == os.path.abspath(root)
    except ValueError:
        return False


def _leak(ctx, what: str, expected: str, actual: str) -> Finding:
    return Finding(
        scenario=ctx.scenario, oracle="safety/home_isolated", key=what,
        title=f"QA 격리가 새고 있다 — {what} 가 슬롯 밖을 가리킨다",
        severity="S1", step=ctx.current,
        expected=f"{what} = {expected}",
        actual=f"{what} = {actual}",
    )


#: 매 스텝 뒤에 도는 것. 순서가 곧 리포트 순서다.
STANDING = (no_traceback, home_isolated)
