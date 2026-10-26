"""프로세스 표 헬퍼는 부를 때마다 ctypes 타입을 새로 남기지 않는다 (pytmux-505 · pytmux-382).

`proc._win_proc_table` 은 자동 탭이름·Claude 패널 감지가 패널마다 주기적으로 부른다.
종전엔 호출마다 `PROCESSENTRY32W` 클래스를 새로 정의하고 `ctypes.POINTER(그 클래스)` 를
argtypes 에 실었는데, `ctypes.POINTER` 는 결과를 클래스를 키로 **영구 캐시**한다 —
호출마다 클래스 1개·CField 10개·포인터 타입 1개가 영영 안 풀렸다(실측 100회 → CField
+1000). 서버가 며칠 돌면 GC 가 훑을 산 객체가 끝없이 늘어 단일 스레드 루프가 느려진다.

되돌리면 실패해야 하는 오라클:
  · `_processentry32w` 캐시를 빼고 함수 안에서 클래스를 다시 짓게 하면 → test_proc_table_does_not_leak_ctypes_types
  · `_process_basic_information` 캐시를 빼면(pytmux-565) → test_process_cwd_builds_its_struct_once
  · `_pmc_type` 캐시를 빼면(pytmux-565 · POINTER 영구 캐시) → test_win_rss_does_not_leak_ctypes_types
  · Windows 서버 rss 가 다시 None 이 되면(pytmux-565) → test_server_rss_is_measured_on_windows
"""
import gc
import os

import harness  # noqa: F401
from run import skip

from pytmuxlib import clientdiag, proc, serverdiag


def _live_ctypes_types():
    gc.collect()
    return sum(1 for o in gc.get_objects() if type(o).__name__ == "CField")


async def test_proc_table_does_not_leak_ctypes_types():
    if os.name != "nt":
        skip("Windows 전용(ToolHelp 스냅샷 경로)")
    assert proc._win_proc_table() is not None, "시험 전제: 프로세스 표를 읽어야 한다"
    before = _live_ctypes_types()
    for _ in range(30):
        proc._win_proc_table()
        proc.foreground_command(os.getpid())
    grown = _live_ctypes_types() - before
    assert grown == 0, (
        "프로세스 표를 60번 읽었더니 ctypes 필드 객체가 %d개 늘었다 — 호출마다 구조체 "
        "클래스를 새로 짓고 있다(ctypes.POINTER 가 그것을 영구 캐시한다)" % grown)


async def test_process_cwd_builds_its_struct_once():
    """`PROCESS_BASIC_INFORMATION` 은 POINTER 에 안 실려 gc 가 거두므로 CField 수로는 못
    가른다(수거 뒤 0 이 된다). 그래서 **같은 클래스 객체**를 돌려주는지로 잰다."""
    if os.name != "nt":
        skip("Windows 전용(PEB 경로)")
    proc.process_cwd(os.getpid())
    first = proc._process_basic_information()
    for _ in range(5):
        proc.process_cwd(os.getpid())
    assert proc._process_basic_information() is first, (
        "cwd 를 읽을 때마다 PROCESS_BASIC_INFORMATION 클래스를 새로 짓는다")


async def test_win_rss_does_not_leak_ctypes_types():
    if os.name != "nt":
        skip("Windows 전용(GetProcessMemoryInfo 경로)")
    assert clientdiag.rss_bytes() is not None, "시험 전제: 현재 rss 를 읽어야 한다"
    before = _live_ctypes_types()
    for _ in range(30):
        clientdiag.rss_bytes()
        serverdiag.rss_bytes()
    grown = _live_ctypes_types() - before
    assert grown == 0, (
        "rss 를 60번 쟀더니 ctypes 필드 객체가 %d개 늘었다 — 호출마다 _PMC 를 새로 짓고 "
        "ctypes.POINTER 가 그것을 영구 캐시한다" % grown)


async def test_server_rss_is_measured_on_windows():
    if os.name != "nt":
        skip("Windows 전용")
    n = serverdiag.rss_bytes()
    assert n is not None and n > 1024 * 1024, (
        "Windows 서버 rss 를 못 쟀다 — :debug-stats 가 「Resident memory ?」 로 뜬다: %r" % n)
