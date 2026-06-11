"""proc 추상층 회귀 테스트(헤드리스).

분리 프로세스 기동(spawn_detached) → 살아있음/세션분리 확인 → 종료(terminate) →
사망 확인의 생애주기를 실제 프로세스로 검증한다. Windows 경로(taskkill/DETACHED)는
이 머신에서 못 돌리므로 server_argv 구성만 확인하고 본 동작은 건너뛴다.
"""
import asyncio
import os
import sys
import tempfile

import harness  # noqa: F401 (sys.path 설정)
from pytmuxlib import proc


def test_server_argv():
    argv = proc.server_argv("/tmp/x.sock")
    # 데몬은 창 없는 인터프리터를 선호한다. POSIX 는 항상 sys.executable,
    # Windows 는 같은 폴더에 pythonw.exe 가 있으면 그쪽(없으면 sys.executable).
    expected = proc._windowless_python() or sys.executable
    assert argv[0] == expected
    assert argv[-3:] == ["--socket", "/tmp/x.sock", "server"]
    assert argv[1].endswith("pytmux.py")


async def test_spawn_detached_lifecycle():
    """마커를 쓰고 잠드는 분리 자식 → 마커 등장 + setsid 분리 + terminate 로 사망."""
    if proc.IS_WINDOWS:
        return  # Windows 는 별도 박스에서 검증
    marker = tempfile.mktemp(suffix=".up")
    code = (
        "import time,sys;"
        f"open({marker!r},'w').write('up');"
        "time.sleep(30)"
    )
    pid = proc.spawn_detached([sys.executable, "-c", code])
    try:
        # 마커가 생길 때까지(자식이 실제로 실행) 대기.
        for _ in range(100):
            if os.path.exists(marker):
                break
            await asyncio.sleep(0.05)
        assert os.path.exists(marker), "분리 자식이 실행되지 않음"
        assert proc.is_alive(pid)
        # start_new_session=True → 자식이 자기 그룹의 리더(getpgid==pid).
        assert os.getpgid(pid) == pid, "setsid 분리가 안 됨"

        proc.terminate(pid, force=True)
        # spawn_detached 는 이 테스트 프로세스의 직속 자식이므로, 죽은 뒤 좀비로
        # 남지 않게 직접 reap 한다(프로덕션에선 클라이언트 종료 시 init 이 회수).
        # 프로덕션 detach 의미상 spawn_detached 가 Popen 핸들을 보관하지 않으므로
        # 여기서 os.waitpid 로 회수한다.
        for _ in range(100):
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
                if wpid == pid:
                    break
            except ChildProcessError:
                break
            await asyncio.sleep(0.05)
        assert not proc.is_alive(pid), "terminate+reap 후에도 살아 있음"
    finally:
        # 혹시 남았으면 정리.
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass
        if proc.is_alive(pid):
            proc.terminate(pid, force=True)
        if os.path.exists(marker):
            os.unlink(marker)


async def test_is_alive_false_for_bogus_pid():
    # 거의 쓰이지 않는 큰 pid → 살아있지 않음.
    assert proc.is_alive(2_000_000_000) is False
    assert proc.is_alive(-1) is False
    assert proc.is_alive(0) is False


async def test_win_is_alive_csv_exact_match():
    """Windows is_alive 가 PID 컬럼을 정확 대조 — 메모리 컬럼 부분일치 오탐 방지.

    실 Windows 가 아니어도 _win_is_alive 의 파싱을 직접 검증한다(subprocess.run 을
    가짜 CSV 로 대체). 과거 `str(pid) in out` 은 pid 892 가 메모리 "68,892 K" 에
    부분일치해 False positive 를 냈다 — 그 회귀를 고정한다.
    """
    import subprocess as _sp

    class _R:
        def __init__(self, stdout):
            self.stdout = stdout

    # 1) pid 892 가 *다른* 프로세스의 메모리 컬럼에만 등장 → 살아있지 않음.
    def fake_run_mem(cmd, **kw):
        return _R('"chrome.exe","4096","Console","1","68,892 K"\r\n')
    orig = _sp.run
    _sp.run = fake_run_mem
    try:
        assert proc._win_is_alive(892) is False, "메모리 컬럼 부분일치 오탐"
        # 2) PID 컬럼이 정확히 892 인 행 → 살아 있음.
        _sp.run = lambda cmd, **kw: _R('"cmd.exe","892","Console","1","5,000 K"\r\n')
        assert proc._win_is_alive(892) is True
        # 3) 대상 없음(INFO 줄) → 살아있지 않음.
        _sp.run = lambda cmd, **kw: _R(
            "INFO: No tasks are running which match the specified criteria.\r\n")
        assert proc._win_is_alive(892) is False
    finally:
        _sp.run = orig


async def test_win_terminate_escalates():
    """Windows terminate(force=False): graceful 후 안 죽으면 /F /T 에스컬레이트(#1.2).

    창 없는/분리 프로세스는 taskkill /T(/F 없음)로 안 죽어 고아가 되던 문제. 실제
    taskkill 대신 _win_taskkill/_win_wait_dead 를 가짜로 대체해 **호출 순서**만 검증
    (실 프로세스 종료는 _probe_term.py 로 박스에서 별도 실측). Windows 분기만 의미가
    있어 POSIX 에선 건너뛴다."""
    if not proc.IS_WINDOWS:
        return
    calls = []
    orig_kill = proc._win_taskkill
    orig_wait = proc._win_wait_dead
    proc._win_taskkill = lambda pid, *, force, timeout=10.0: calls.append(
        ("kill", force))
    try:
        # 1) force=True → 곧장 강제 1회, graceful/wait 없음.
        calls.clear()
        proc.terminate(123, force=True)
        assert calls == [("kill", True)]

        # 2) force=False, graceful 후 죽음 → 에스컬레이트 없음.
        calls.clear()
        proc._win_wait_dead = lambda pid, timeout: True
        proc.terminate(123, force=False)
        assert calls == [("kill", False)]

        # 3) force=False, 안 죽음 → graceful 후 강제 에스컬레이트.
        calls.clear()
        proc._win_wait_dead = lambda pid, timeout: False
        proc.terminate(123, force=False)
        assert calls == [("kill", False), ("kill", True)]
    finally:
        proc._win_taskkill = orig_kill
        proc._win_wait_dead = orig_wait


async def test_terminate_bogus_pid_noop():
    # 없는 pid 종료는 조용히 통과해야 한다(예외 없음).
    proc.terminate(2_000_000_000, force=True)
    proc.terminate(0)


async def test_run_sync_units():
    test_server_argv()
