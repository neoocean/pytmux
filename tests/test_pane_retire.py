"""패널에서 떼어낸 PTY 자식은 «훅이 돌 틈»을 받고 죽는다(pytmux/pytmux-415).

`respawn_pane` 은 종전에 곧바로 SIGKILL 했다. 그것은 자식의 종료 훅을 통째로 건너뛴다 —
그리고 이 저장소의 사용자가 패널에서 돌리는 프로그램 하나가 정확히 그 훅으로 머신 상태를
되돌린다.

실측(2026-09-03 · macOS · 격리 `CLAUDE_CONFIG_DIR` · 진짜 claude 2.1.259 · 대조군 4종):

| 어떻게 죽였나 | `fullscreenBootPending` 이 고아로 남나 | 정리까지 |
| --- | --- | ---: |
| SIGHUP | 아니오 | 0.07s |
| SIGHUP + 마스터 fd 즉시 닫기 | 아니오 | 0.06s |
| SIGTERM | 아니오 | — |
| 정상 종료 | 아니오 | — |
| **SIGKILL** | **예** | — |

그 고아가 **둘**이면(임계 `yh=2` · 이진에서 읽음) claude 가 그 머신의 fullscreen 렌더러를
통째로 끈다 = 사용자가 겪은 그 증상(스티키 프롬프트 바 소실).

⛔ **고침(`_retire_pty`)은 CL 75008 로 이미 나갔는데 오라클이 없었다**(그 CL 이 스스로
"실행은 안 해 봤다"고 적었다). 이 파일이 그 자리다.

되돌리면 실패해야 하는 것:
  · `_retire_pty` 가 `terminate()` 대신 `kill()` 로 시작하면
    → test_retire_asks_before_it_forces
  · 뒷걸음(`_finish_retire`)의 확인 사살을 지우면
    → test_a_child_that_ignores_the_ask_is_forced_after_the_grace
  · 유예 안에 죽은 자식도 굳이 쏘면
    → test_a_child_that_goes_quietly_is_never_forced
  · `respawn_pane` 이 `_retire_pty` 를 안 부르고 제 손으로 죽이면
    → test_respawn_retires_through_the_two_step (호출부 오라클)
  · 뒷걸음을 동기 대기로 되돌리면
    → test_the_second_step_never_blocks_the_loop (AST 가드)
"""
import ast
import asyncio
import inspect
import textwrap

import harness  # noqa: F401  (경로 설정 + 위생 설치)

from pytmuxlib import serverpty


class _FakePty:
    """`PtyProcess` 계약 중 이 자리가 쓰는 다섯 개만 흉내낸다.

    `obeys`: `terminate()` 를 받으면 스스로 죽는다(=`reap` 이 상태를 낸다).
    `False` 면 부탁을 **무시한다** — 확인 사살을 재는 대조군.
    """

    def __init__(self, *, obeys=True):
        self.obeys = obeys
        self.log = []
        self._dead = False

    def stop_reader(self):
        self.log.append("stop_reader")

    def terminate(self):
        self.log.append("terminate")
        if self.obeys:
            self._dead = True

    def kill(self):
        self.log.append("kill")
        self._dead = True

    def close(self):
        self.log.append("close")

    def reap(self, *, block=False):
        return 0 if self._dead else None


class _Retirer(serverpty.ServerPtyMixin):
    """믹스인에서 이 두 걸음만 떼어 재는 최소 숙주."""

    def __init__(self, loop):
        self.loop = loop
        self._bg = []

    def _spawn(self, coro, where):
        """`ServerIOMixin._spawn` 의 최소 대역 — **참조를 붙들고** 태스크로 세운다.

        ⛔ 숙주가 이것을 안 주면 `_retire_pty` 가 `AttributeError` 로 죽는다. 그 자리가
        맨 `create_task` 가 아닌 이유는 production 의 규율이다(pytmux-410 — 참조가
        없으면 GC 가 실행 중인 태스크를 거둬 SIGKILL 폴백이 영영 안 나가고 고아 셸이
        남는다 · `tests/test_detached_tasks.py` 가 AST 로 그것을 지킨다).

        ⚠ 이 대역이 빠져 있어 depot head 에서 `test_retire_asks_before_it_forces` 가
        붉었다(CL 75318 은 `serverpty.py#25`(CL 75309 · 검수 2026-09-05 S-4)가 그 줄을
        `self._spawn` 으로 바꾸기 전 트리에서 쓰였다).
        """
        self._bg.append(self.loop.create_task(coro))



# ── ⑴ 부탁이 먼저다 ──────────────────────────────────────────────────────────

async def test_retire_asks_before_it_forces():
    """SIGKILL 로 **시작하지 않는다** — 자식의 종료 훅이 돌 틈을 준다.

    이 한 줄이 이 이슈의 전부다: 실측에서 SIGHUP·SIGTERM·정상 종료는 자식이 자기
    기록을 0.07초에 지웠고, SIGKILL 만 그것을 고아로 남겼다.
    """
    loop = asyncio.get_running_loop()
    pty = _FakePty()
    r = _Retirer(loop)
    scheduled = []

    # 뒷걸음은 **가로채서** 붙잡는다 — 그러면 이 시험이 고정 대기 없이 끝나고
    # (저장소 대기 규약) 「태스크로 세웠나」도 함께 재진다.
    orig = loop.create_task
    loop.create_task = lambda coro: scheduled.append(coro)
    try:
        r._retire_pty(pty)
    finally:
        loop.create_task = orig
    for coro in scheduled:
        coro.close()

    assert "kill" not in pty.log, ("부탁도 없이 쐈다", pty.log)
    assert pty.log == ["stop_reader", "terminate", "close"], pty.log
    assert scheduled, "뒷걸음을 태스크로 안 세웠다 — 확인 사살이 영영 안 온다"


async def test_a_child_that_goes_quietly_is_never_forced():
    """질서 있게 물러난 자식에게는 확인 사살이 안 간다.

    ⛔ 고정 대기를 안 쓴다 — 뒷걸음 코루틴을 **직접 몰아** 끝까지 돌린다.
    """
    pty = _FakePty(obeys=True)
    pty.terminate()                              # 부탁을 받고 물러났다
    await _Retirer(asyncio.get_running_loop())._finish_retire(pty)
    assert "kill" not in pty.log, ("이미 죽은 자식을 쐈다", pty.log)


# ── ⑵ 그래도 안 죽으면 쏜다 ──────────────────────────────────────────────────

async def test_a_child_that_ignores_the_ask_is_forced_after_the_grace():
    """부탁을 무시하는 자식은 유예 뒤에 pid 로 죽는다 — 종전 동작을 잃지 않는다."""
    pty = _FakePty(obeys=False)
    pty.terminate()
    with harness.patched(serverpty, _RETIRE_GRACE=0.05):
        await _Retirer(asyncio.get_running_loop())._finish_retire(pty)
    assert "kill" in pty.log, ("유예가 지났는데 확인 사살이 없다", pty.log)
    assert pty.log.index("terminate") < pty.log.index("kill"), pty.log


async def test_the_grace_is_bounded_and_not_zero():
    """유예는 «있고» «끝난다» — 둘 중 하나라도 무너지면 이 두 걸음이 무의미하다."""
    assert 0 < serverpty._RETIRE_GRACE <= 10.0, serverpty._RETIRE_GRACE
    assert 0 < serverpty._RETIRE_POLL < serverpty._RETIRE_GRACE


# ── ⑶ 호출부 — 헬퍼만 재면 그 호출을 지워도 통과한다 ─────────────────────────

def _calls_in(func) -> set:
    tree = ast.parse(textwrap.dedent("".join(inspect.getsourcelines(func)[0])))
    return {node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}


async def test_respawn_retires_through_the_two_step():
    """`respawn_pane` 이 제 손으로 안 죽이고 두 걸음을 **부른다**(호출부 오라클)."""
    calls = _calls_in(serverpty.ServerPtyMixin.respawn_pane)
    assert "_retire_pty" in calls, "respawn 이 두 걸음을 안 부른다"
    assert "kill" not in calls, ("respawn 이 아직 제 손으로 SIGKILL 한다", sorted(calls))


async def test_the_second_step_never_blocks_the_loop():
    """뒷걸음은 **루프를 안 막는다**(AST 로 본다 — 주석 문자열에 안 걸린다).

    이 자리는 단일 스레드 asyncio 루프 안이다. 유예를 동기 대기로 걸면 그 초 동안
    모든 클라가 멎는다(pytmux-435 가 같은 함정을 같은 방식으로 갈랐다).
    """
    tree = ast.parse(textwrap.dedent(
        "".join(inspect.getsourcelines(serverpty.ServerPtyMixin._finish_retire)[0])))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert ast.unparse(node.func) != "time.sleep", "동기 대기가 루프를 막는다"
    assert any(isinstance(n, ast.Await) for n in ast.walk(tree)), (
        "뒷걸음이 await 를 하나도 안 한다 — 태스크로 도는 것이 아니다")
