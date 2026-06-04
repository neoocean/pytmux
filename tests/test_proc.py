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


async def test_terminate_bogus_pid_noop():
    # 없는 pid 종료는 조용히 통과해야 한다(예외 없음).
    proc.terminate(2_000_000_000, force=True)
    proc.terminate(0)


async def test_run_sync_units():
    test_server_argv()
