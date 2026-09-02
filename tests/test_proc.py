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
from harness import wait_for
from pytmuxlib import proc
from run import skip


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
        await wait_for(lambda: os.path.exists(marker), timeout=5.0, step=0.05)
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


async def test_foreground_command():
    """foreground_command(#7): POSIX 는 None(servertree 가 직접), Windows 는 자손 추정.

    Windows 에선 우리 프로세스(os.getpid())의 가장 깊은 자손을 구하는데, 보통 자손이 없어
    셸/자기 자신(python) 이름을 돌려준다 — 비어있지 않은 문자열이면 OK(.exe 제거 확인).
    잘못된 pid·POSIX 는 None."""
    import os as _os
    assert proc.foreground_command(-1) is None
    assert proc.foreground_command(0) is None
    if not proc.IS_WINDOWS:
        assert proc.foreground_command(_os.getpid()) is None  # POSIX 갭 전용
        return
    name = proc.foreground_command(_os.getpid())
    assert name and isinstance(name, str), name
    assert not name.lower().endswith(".exe"), name  # 확장자 제거됨


async def test_run_sync_units():
    test_server_argv()


async def test_spawn_detached_captures_stderr_to_a_file():
    """stderr_path 를 주면 자식 stderr 가 그 파일로 간다(데몬 부팅 실패 진단의 토대).

    데몬은 stderr 가 /dev/null 이라 **부팅 중 죽으면 이유가 통째로 사라졌다** —
    원격 자동 기동 실패가 '인증 대기 시한 초과' 로만 보이던 원인(2026-07-28).
    파일 없이(=stderr_path 미지정) 기동해도 종전대로 동작해야 한다.
    """
    if proc.IS_WINDOWS:
        return  # POSIX 에서 검증(Windows 는 별도 박스)
    d = tempfile.mkdtemp(prefix="pytmux-bootlog-")
    log = os.path.join(d, "boot.log")
    code = "import sys;sys.stderr.write('BOOM: no module named zzz\\n')"
    pid = proc.spawn_detached([sys.executable, "-c", code], stderr_path=log)
    try:
        await wait_for(lambda: os.path.exists(log) and os.path.getsize(log) > 0,
                       timeout=10.0, step=0.05)
        assert "BOOM" in open(log).read(), open(log).read()
    finally:
        try:
            os.waitpid(pid, 0)
        except (ChildProcessError, OSError):
            pass


# ---- long_path — cwd 표기를 온디스크 이름으로(pytmux-237·-436..441) ----------------

def _short_name(path):
    r"""`path` 의 8.3 단축 표기를 OS 에게 물어 돌려준다(Windows 전용, 없으면 None).

    상자의 `TMP` 가 단축이든 아니든 **시험이 스스로 단축 경로를 만든다** — 그래야 이
    오라클이 어느 Windows 상자에서나 같은 것을 잰다(이 결함을 처음 드러낸 상자는
    에이전트 셸의 `TMP` 가 단축이었지만, 그건 재현 조건이지 요구사항이 아니다)."""
    import ctypes
    from ctypes import wintypes
    fn = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    fn.restype = wintypes.DWORD
    need = fn(path, None, 0)
    if not need:
        return None
    buf = ctypes.create_unicode_buffer(need)
    return buf.value if fn(path, buf, need) else None


async def test_long_path_passes_through_what_it_cannot_or_need_not_change():
    """표기를 다듬는 함수지 존재를 판정하는 함수가 아니다 — 못 펴면 **입력 그대로**.

    빈 값·없는 경로는 그대로 나오고, 이미 온디스크 이름이면 한 번 더 펴도 안 바뀐다
    (멱등). 이 단언들은 OS 를 안 가린다."""
    assert proc.long_path(None) is None
    assert proc.long_path("") == ""
    with tempfile.TemporaryDirectory() as td:
        once = proc.long_path(td)
        assert proc.long_path(once) == once, (td, once)
        ghost = os.path.join(once, "no-such~1", "deeper")
        assert proc.long_path(ghost) == ghost


async def test_long_path_opens_an_8_3_short_name():
    """`WOOJIN~1` 같은 단축 성분이 온디스크 긴 이름으로 펴져야 한다.

    이것이 안 되면 ncd 트리가 cwd 사슬을 `scandir` 의 긴 이름과 못 맞춰 **현재 자리를
    잃고 드라이브 목록으로 떨어진다**(pytmux-237). `normcase` 는 대소문자만 흡수하므로
    이 갈림을 못 덮는다."""
    if not proc.IS_WINDOWS:
        skip("8.3 단축 이름은 Windows 개념이다")
    with tempfile.TemporaryDirectory() as td:
        # 단축 성분이 생기도록 8자 넘는 이름을 만든다(짧은 이름엔 8.3 별칭이 없다).
        deep = os.path.join(td, "a directory with spaces")
        os.makedirs(deep)
        short = _short_name(deep)
        if not short:
            skip("이 볼륨에 8.3 별칭이 없다(NtfsDisable8dot3NameCreation)")
        assert "~" in short, short              # 진짜 단축 표기를 얻었다
        assert proc.long_path(short) == proc.long_path(deep), (short, deep)


async def test_long_path_never_turns_a_drive_root_into_something_else():
    """드라이브 루트는 드라이브 루트로 남아야 한다 — ⛔ `realpath` 로 대신하면 깨진다.

    실측(2026-09-02): `os.path.realpath("R:\\")` 는 매핑 네트워크 드라이브를
    `\mxfs\DATA_RX` 로 바꿨다. ncd 는 드라이브 문자를 트리 최상위 노드로 쓰므로
    그 치환이 곧 **드라이브 전환 기능의 파괴**다. 끊긴 드라이브(rc 0)도 입력 그대로."""
    if not proc.IS_WINDOWS:
        skip("드라이브 문자는 Windows 개념이다")
    roots = sorted(os.listdrives()) if hasattr(os, "listdrives") else []
    if not roots:
        skip("이 상자에 드라이브 목록이 없다")
    for d in roots:
        got = proc.long_path(d)
        assert got == d, (d, got)


async def test_process_cwd_reports_the_on_disk_name_not_the_peb_spelling():
    """PEB 는 **넣어 준 문자열 그대로**를 들고 있다 — cwd 를 파는 층이 그것을 편다.

    단축 경로에서 셸을 띄우고 그 pid 의 cwd 를 물으면 온디스크 이름이 나와야 한다.
    이 왕복이 이 결함의 실제 자리다(고침 전에는 단축 표기가 그대로 나와 ncd·mdir·
    default-path=current 가 전부 그 표기를 물려받았다)."""
    if not proc.IS_WINDOWS:
        skip("PEB cwd 읽기는 Windows 경로다")
    with tempfile.TemporaryDirectory() as td:
        deep = os.path.join(td, "a directory with spaces")
        os.makedirs(deep)
        short = _short_name(deep)
        if not short:
            skip("이 볼륨에 8.3 별칭이 없다(NtfsDisable8dot3NameCreation)")
        import subprocess
        p = subprocess.Popen(["cmd.exe", "/k"], cwd=short,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             **proc.no_window_kwargs())
        try:
            await wait_for(lambda: proc.process_cwd(p.pid) is not None)
            got = proc.process_cwd(p.pid)
            assert got is not None, "셸의 cwd 를 못 읽었다"
            assert "~" not in os.path.basename(got), got
            assert got == proc.long_path(deep), (got, deep)
        finally:
            p.kill()
            p.wait()
