#!/usr/bin/env python3
"""ConPTY × asyncio × pyte 검증 슬라이스 — pytmux 네이티브 Windows 포팅 PoC.

docs/WINDOWS_PORT.md §6 "가장 빠른 검증 슬라이스" 의 구현. 포팅 작업의 ~70%
리스크가 몰린 두 서브시스템(①②)만 격리해서 찔러본다. 나머지(③ 프로세스/시그널,
④ IPC)는 이게 되면 "배관 작업" 이다.

  ① PTY 계층     : pywinpty(ConPTY) 로 cmd.exe 를 의사콘솔에 띄운다(fork 없음).
  ② 이벤트 루프  : Windows 기본 Proactor 루프는 ConPTY 파이프에 add_reader 를
                   못 건다. 그래서 **전용 리더 스레드**가 블로킹 read 후
                   loop.call_soon_threadsafe 로 asyncio 루프에 바이트를 밀어넣는다.
                   (= 실제 server.py 가 가야 할 스레드 펌프 모델 그대로)

읽힌 바이트는 **기존** pyte 파이프라인(pytmuxlib.model.Pane)에 그대로 먹이고,
**기존** 순수 렌더러(pytmuxlib.replay.render_pane_lines)로 텍스트 프레임을 덤프한다.
즉 OS 배관만 갈아끼우고 렌더 경로는 살아있는 클라이언트와 동일하다.

`echo PYTMUX_POC_OK ...` 줄이 또렷이 찍히면 ①②가 풀린 것이다.

────────────────────────────────────────────────────────────────────────────
pytmuxlib 무수정 원칙
  protocol.py 가 모듈 최상단에서 `import fcntl`/`import termios` 하므로(POSIX 전용)
  Windows 에서는 Pane 을 import 하는 순간 깨진다. 본 PoC 는 pytmuxlib 를 **건드리지
  않고**, fcntl/termios 가 없을 때만 no-op 스텁을 sys.modules 에 심어 우회한다.
  set_winsize() 는 PoC 에서 호출되지 않으므로(크기는 pywinpty 가 직접 설정) 스텁의
  ioctl 은 실제로 실행될 일이 없다. 실제 포팅에서는 protocol 의 fcntl 의존을
  pty_backend/ipc 추상층으로 걷어내야 한다(§6-1).

실행 (Windows 10 1809+ / Python 3.12):
    pip install pywinpty pyte wcwidth
    python poc\\winpty_poc.py                 # ConPTY 로 cmd.exe 띄워 검증
    python poc\\winpty_poc.py --ruler         # 열 눈금자 추가
    python poc\\winpty_poc.py --cols 120 --rows 40

어느 OS에서나(ConPTY 없이) 렌더 파이프라인 절반만 확인:
    python poc/winpty_poc.py --selftest       # 캔드 ANSI 바이트로 Pane+렌더 검증
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import types


# ── pytmuxlib import 가능하게 경로 + (필요시) POSIX 스텁 설치 ──────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))   # repo 루트(pytmux/ — scripts/poc 의 두 단계 위)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _install_posix_stubs() -> bool:
    """Windows 처럼 fcntl/termios 가 없을 때만 no-op 스텁을 심는다.

    반환값: 스텁을 심었으면 True(=네이티브 POSIX 아님).
    """
    try:
        import fcntl  # noqa: F401
        import termios  # noqa: F401
        return False
    except ModuleNotFoundError:
        fcntl_stub = types.ModuleType("fcntl")
        # set_winsize 는 PoC 에서 안 불리지만, 불려도 죽지 않게 no-op.
        fcntl_stub.ioctl = lambda *a, **k: 0  # type: ignore[attr-defined]
        termios_stub = types.ModuleType("termios")
        termios_stub.TIOCSWINSZ = 0x5414  # 값은 무의미(스텁 ioctl 이 무시)
        sys.modules.setdefault("fcntl", fcntl_stub)
        sys.modules.setdefault("termios", termios_stub)
        return True


_STUBBED = _install_posix_stubs()

# 스텁이 자리잡은 뒤에 import 해야 한다(model→protocol→fcntl 체인).
from pytmuxlib.model import Pane              # noqa: E402
from pytmuxlib.replay import render_pane_lines  # noqa: E402


def _ruler(cols: int) -> str:
    tens = "".join(str((i // 10) % 10) if i % 10 == 0 else " " for i in range(cols))
    ones = "".join(str(i % 10) for i in range(cols))
    return tens + "\n" + ones


def _dump(pane: Pane, cols: int, *, ruler: bool, header: str) -> None:
    print(header)
    if ruler:
        print(_ruler(cols))
    for ln in render_pane_lines(pane):
        print(ln.rstrip())


# ── 셀프테스트: ConPTY 없이 렌더 파이프라인 절반만 검증(아무 OS) ───────────────
def run_selftest(cols: int, rows: int, ruler: bool) -> int:
    pane = Pane(-1, -1, cols, rows)
    # 색·커서이동·와이드문자(CJK/이모지) 섞은 캔드 시퀀스.
    sample = (
        b"\x1b[2J\x1b[H"                      # clear + home
        b"PYTMUX_POC_OK self-test\r\n"
        b"\x1b[31mred\x1b[0m \x1b[1;32mbold-green\x1b[0m\r\n"
        b"wide: \xea\xb0\x80\xeb\x82\x98\xeb\x8b\xa4 CJK\r\n"  # 가나다
        b"\x1b[5;1Hrow5-col1 (after cursor move)"
    )
    pane.feed(sample)
    _dump(pane, cols, ruler=ruler,
          header=f"# selftest ({cols}x{rows}, pyte+render only, no ConPTY)")
    print("\n# OK: Pane.feed + render_pane_lines 동작(POSIX 스텁="
          f"{_STUBBED}). ConPTY 경로는 Windows 에서 --selftest 빼고 실행.",
          file=sys.stderr)
    return 0


# ── 본 PoC: pywinpty(ConPTY) + 리더 스레드 + asyncio 펌프 ─────────────────────
async def run_conpty(cols: int, rows: int, ruler: bool, timeout: float) -> int:
    try:
        from winpty import PtyProcess
    except ImportError:
        print("이 PoC 의 ConPTY 경로는 Windows + pywinpty 가 필요합니다.\n"
              "  pip install pywinpty\n"
              "다른 OS에서 렌더 파이프라인만 확인하려면: --selftest",
              file=sys.stderr)
        return 2

    loop = asyncio.get_running_loop()
    pane = Pane(-1, -1, cols, rows)       # ① PTY fd 불필요 — feed 만 한다
    done = asyncio.Event()
    bytes_seen = 0

    # ① ConPTY 로 cmd.exe spawn. dimensions=(rows, cols).
    proc = PtyProcess.spawn("cmd.exe", dimensions=(rows, cols))

    # 결정적 출력 후 종료 → EOF 로 끝을 안다.
    proc.write("echo PYTMUX_POC_OK & ver\r\n")
    proc.write("exit\r\n")

    def on_bytes(b: bytes) -> None:
        nonlocal bytes_seen
        bytes_seen += len(b)
        pane.feed(b)                      # 기존 pyte 파이프라인

    # ② 리더 스레드: 블로킹 read → call_soon_threadsafe 로 루프에 펌프.
    def reader() -> None:
        while True:
            try:
                s = proc.read(65536)      # pywinpty 는 str 을 돌려준다
            except EOFError:
                break
            except Exception:             # ConPTY 종료 시 잡다한 OSError 방어
                break
            if s:
                # NOTE: 프로덕션은 바이트 경로(low-level winpty.PTY)를 써서
                # 멀티바이트 시퀀스가 중간에 잘려 디코드 깨지는 걸 피해야 한다.
                # PoC 는 파이프라인 증명이 목적이라 utf-8 재인코딩으로 충분.
                loop.call_soon_threadsafe(on_bytes, s.encode("utf-8", "replace"))
            elif not proc.isalive():
                break
        loop.call_soon_threadsafe(done.set)

    t = threading.Thread(target=reader, name="conpty-reader", daemon=True)
    t.start()

    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        print(f"# (timeout {timeout}s — 부분 출력으로 덤프)", file=sys.stderr)

    _dump(pane, cols, ruler=ruler,
          header=f"# ConPTY replay (cmd.exe, {cols}x{rows}, {bytes_seen} bytes)")

    ok = bytes_seen > 0
    print(f"\n# {'OK' if ok else 'FAIL'}: ConPTY→리더스레드→asyncio→pyte→render "
          f"경로 {'성립' if ok else '실패'} (읽은 바이트 {bytes_seen}). "
          "①② de-risked." if ok else "# 바이트를 못 읽음 — pywinpty/ConPTY 확인.",
          file=sys.stderr)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pytmux Windows ConPTY PoC slice")
    ap.add_argument("--cols", type=int, default=100)
    ap.add_argument("--rows", type=int, default=30)
    ap.add_argument("--ruler", action="store_true", help="열 눈금자 출력")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--selftest", action="store_true",
                    help="ConPTY 없이 Pane+렌더만 검증(아무 OS)")
    args = ap.parse_args(argv)

    if args.selftest:
        return run_selftest(args.cols, args.rows, args.ruler)
    try:
        return asyncio.run(run_conpty(args.cols, args.rows, args.ruler,
                                      args.timeout))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
