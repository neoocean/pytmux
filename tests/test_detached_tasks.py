"""pytmux-410 — 떼어 놓은 태스크는 **참조를 붙들어야** 한다.

무엇이 잘못돼 있었나: 서버의 브로드캐스트가 `asyncio.create_task(self._send_full(c))` 를
반환값을 버린 채 불렀다. 이벤트 루프는 그런 태스크를 **약한 참조로만** 붙든다 — 파이썬
문서가 명시하는 함정이고, GC 가 **실행 도중에** 거둬 갈 수 있다.

증상이 그 모양이었다: QA T2(다중 클라)가 2026-08-26 에 「클라 3 중 1 이 새 탭 이름을 못
받았다」를 한 번 잡았고, 한가한 상자에서 15번을 다시 돌려도 안 났다. 부하가 높을수록
(=GC 가 자주 돌수록) 자주 나는, 클라 하나만 무작위로 빠지는 결함이다.

고침: `ServerIOMixin._spawn(coro, where)` 한 자리로 모은다 — 참조를 집합에 넣어 두고,
끝나면 빼고, 예외는 `_log_error` 로 적는다(데몬은 stderr 가 /dev/null 이라 「Task
exception was never retrieved」조차 안 보인다).

되돌리면 실패해야 하는 오라클:
  · 참조를 안 붙들면            → test_a_spawned_task_is_held ·
                                   test_each_server_has_its_own_set 실패(실측)
  · 끝난 뒤 안 빼면(누수)        → test_a_finished_task_is_released 실패
  · 예외를 안 적으면            → test_a_failing_task_is_logged 실패
  · 취소를 예외로 세면          → test_a_cancelled_task_is_not_an_error 실패
  · **서버가 다시 맨 create_task 를 쓰면** → test_the_server_never_bare_creates_a_task 실패
    (이것이 진짜 재발 방지 자리다 — 위 넷은 헬퍼만 재고 부르는 쪽은 안 잰다)
"""
import ast
import asyncio
import gc
import pathlib

import harness

from pytmuxlib.serverio import ServerIOMixin

_ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Srv(ServerIOMixin):
    """`_spawn` 만 빌려 쓰는 최소 서버 — 로그는 모아 둔다."""

    def __init__(self):
        self.logged = []

    def _log_error(self, where, detail=""):
        self.logged.append(where)


async def test_a_spawned_task_is_held():
    """★ 결함의 뿌리를 재는 자리 — 다만 **무엇을 재는지 정확히 적는다.**

    실제로 무는 단언은 「참조를 들고 있다」이고, 「GC 가 거둬 가서 태스크가 죽는다」를
    직접 재지는 **못한다** — 그것은 GC 시점에 달린 일이라 결정적으로 재현되지 않는다
    (그래서 이 결함이 15/15 초록인 상자에서 안 났고 QA 가 한 번만 잡았다). 뮤테이션
    실측(2026-08-30): 참조 붙들기를 지우면 이 시험과 `..._own_set` 둘이 떨어지고,
    아래 `done == [True]` 는 그 판에서도 통과했다 — 그 줄은 「끝까지 갔다」의 바닥
    안전망이지 이 결함의 오라클이 아니다."""
    srv = _Srv()
    done = []
    gate = asyncio.Event()

    async def work():
        # ★ 게이트에서 **멈춰 있는 동안** GC 를 돌린다 — 이 정지 상태가 정확히 위험한
        #   자리다(루프가 태스크를 약한 참조로만 붙드는 구간). 고정 대기가 아니라
        #   시험이 재는 상태 그 자체다.
        await gate.wait()
        done.append(True)

    srv._spawn(work(), "unit")
    assert srv._bg_tasks, "만든 태스크를 아무도 안 들고 있다"
    gc.collect()                      # 참조가 없으면 여기서 거둬 간다
    gate.set()
    await harness.wait_for(lambda: done == [True])
    assert done == [True], "떼어 놓은 태스크가 끝까지 안 갔다"


async def test_a_finished_task_is_released():
    """붙들기만 하고 안 놓으면 그것은 누수다 — 오래 뜬 서버에서 무한히 자란다."""
    srv = _Srv()

    async def work():
        return None

    srv._spawn(work(), "unit")
    await harness.wait_for(lambda: srv._bg_tasks == set())
    assert srv._bg_tasks == set(), f"끝난 태스크가 남아 있다: {srv._bg_tasks}"


async def test_a_failing_task_is_logged():
    """데몬은 stderr 가 /dev/null 이라 「Task exception was never retrieved」조차 안
    보인다 — 여기서 안 받아 적으면 그 예외는 **어디에도 안 남는다**."""
    srv = _Srv()

    async def boom():
        raise RuntimeError("터졌다")

    srv._spawn(boom(), "제자리")
    await harness.wait_for(lambda: srv.logged)
    assert srv.logged == ["spawn(제자리)"], srv.logged


async def test_a_cancelled_task_is_not_an_error():
    """서버가 내려갈 때 도는 태스크는 취소된다 — 그것을 결함으로 적으면 정상 종료마다
    error.log 가 더러워지고, 진짜 예외가 그 잡음에 묻힌다."""
    srv = _Srv()

    async def slow():
        await asyncio.Event().wait()      # 취소가 유일한 끝 — 시계에 안 매단다

    t = srv._spawn(slow(), "unit")
    t.cancel()
    # 부정 단언이라 조건이 «참이 되기를» 기다릴 수 없다 — 대신 **태스크가 끝났음**을
    # 기다린 뒤 본다(고정 대기가 아니라 관측이다).
    await harness.wait_for(lambda: t.done())
    assert srv.logged == []


async def test_each_server_has_its_own_set():
    """클래스 자리에 둔 기본값(None)을 그대로 공유하면 한 서버의 태스크가 다른 서버의
    집합에 들어간다 — 한쪽이 내려갈 때 남의 것을 들고 있게 된다."""
    a, b = _Srv(), _Srv()

    async def slow():
        await asyncio.Event().wait()      # 취소가 유일한 끝

    ta = a._spawn(slow(), "unit")
    tb = b._spawn(slow(), "unit")
    assert a._bg_tasks is not b._bg_tasks
    assert ta in a._bg_tasks and ta not in b._bg_tasks
    ta.cancel()
    tb.cancel()
    await harness.wait_for(lambda: ta.done() and tb.done())


def test_the_server_never_bare_creates_a_task():
    """★★ **재발 방지의 진짜 자리.** 위 시험들은 헬퍼를 재지 부르는 쪽을 안 잰다 —
    누군가 내일 `asyncio.create_task(...)` 를 한 줄 더 쓰면 전부 초록인 채로 같은
    결함이 돌아온다.

    ⛔ **문장으로 선** 것만 문다. 변수에 담는 것(`flush = asyncio.create_task(...)`)은
    부르는 쪽이 참조를 들고 있으므로 안전하고, 실제로 서버 기동 루프가 그렇게 쓴다.
    """
    offenders = []
    for path in sorted(_ROOT.glob("pytmuxlib/server*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # 값이 안 쓰이는 표현식 = 반환된 태스크를 아무도 안 든다.
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            fn = node.value.func
            if (isinstance(fn, ast.Attribute) and fn.attr == "create_task"
                    and isinstance(fn.value, ast.Name) and fn.value.id == "asyncio"):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "맨 asyncio.create_task 가 돌아왔다 — self._spawn(코루틴, '자리') 를 쓸 것: "
        + ", ".join(offenders))


def test_the_broadcast_paths_actually_use_it():
    """양성 오라클: 위 시험은 「없다」만 재므로 배선을 통째로 지워도 통과한다.
    브로드캐스트 자리가 **실제로** `_spawn` 을 부르는지 본다."""
    seen = {}
    for name in ("server", "serverio", "servertree", "serverremote", "serverpersist"):
        src = (_ROOT / "pytmuxlib" / f"{name}.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        seen[name] = sum(
            1 for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_spawn")
    # 제어 라인 브로드캐스트(server) · 세션 브로드캐스트(serverio) 가 이 결함의 자리다.
    assert seen["server"] >= 1 and seen["serverio"] >= 2, seen
    assert sum(seen.values()) >= 10, f"자리가 너무 적다 — 이관이 덜 됐다: {seen}"
