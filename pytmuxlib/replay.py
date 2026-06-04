"""렌더 진단/리플레이 도구.

- record(): 프로그램을 PTY 안에서 실행하며 원시 출력을 파일로 녹화(+화면 통과).
- replay(): 녹화된 원시 바이트를 pyte + 합성 파이프라인으로 재생해 **텍스트
  프레임**으로 덤프한다. 화면 없이 vim/less/ls/claude 류 출력을 확인하고,
  골든 스냅샷 테스트를 만들거나 렌더 깨짐(열 밀림 등)을 오프라인 재현할 수 있다.

이 모듈은 살아있는 클라이언트(Textual)와 동일한 폭-인지 합성 규칙을 사용한다:
와이드 문자(이모지/CJK)는 2칸을 차지하고, pyte 연속 셀은 건너뛴다.
"""
from __future__ import annotations

import os
import sys

from wcwidth import wcwidth

from .model import Pane
from .protocol import set_winsize


def _char_cells(ch: str) -> int:
    return 2 if wcwidth(ch) == 2 else 1


def render_pane_lines(pane: Pane) -> list[str]:
    """패널의 현재 화면을 클라이언트와 동일한 방식으로 합성해 텍스트 줄 목록 반환.

    와이드 문자는 2칸을 차지하고(다음 칸은 연속 셀), 렌더 시 연속 셀은 건너뛴다.
    줄의 시각적 폭은 pane.cols 와 일치한다.
    """
    rows, _ = pane.render(True)
    W = pane.cols
    lines = []
    for row in rows:
        cells = [" "] * W
        cx = 0
        for text, _style in row:
            for ch in text:
                if cx >= W:
                    break
                cells[cx] = ch
                if _char_cells(ch) == 2 and cx + 1 < W:
                    cells[cx + 1] = ""   # 연속 셀(렌더 시 제거)
                    cx += 2
                else:
                    cx += 1
        lines.append("".join(c for c in cells if c != ""))
    return lines


def replay(data: bytes, cols: int, rows: int) -> list[str]:
    """원시 바이트를 폭 cols x rows 패널에 재생하고 합성된 텍스트 줄을 반환."""
    pane = Pane(-1, -1, cols, rows)   # PTY 불필요(피드만 함)
    pane.feed(data)
    return render_pane_lines(pane)


def _ruler(cols: int) -> str:
    tens = "".join(str((i // 10) % 10) if i % 10 == 0 else " " for i in range(cols))
    ones = "".join(str(i % 10) for i in range(cols))
    return tens + "\n" + ones


def run_replay(path: str, cols: int, rows: int, ruler: bool = False,
               rstrip: bool = True) -> int:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"replay: {e}", file=sys.stderr)
        return 1
    lines = replay(data, cols, rows)
    if rstrip:
        lines = [ln.rstrip() for ln in lines]
    print(f"# replay {path}  ({cols}x{rows}, {len(data)} bytes)")
    if ruler:
        print(_ruler(cols))
    print("\n".join(lines))
    return 0


def run_record(path: str, cols: int, rows: int, argv: list[str],
               echo: bool = True) -> int:
    """argv 명령(없으면 $SHELL)을 PTY(cols x rows)에서 실행하며 원시 출력을
    path 에 녹화한다. 호스트 터미널로 입출력을 그대로 통과시킨다(상호작용 가능).

    부모가 slave fd 를 계속 열어 두어, 자식이 종료해도 PTY 에 남은 출력을 EIO 없이
    끝까지 읽는다(macOS 의 빠른 명령 출력 유실 방지)."""
    # record() 는 POSIX pty/termios/tty/select(fd) 에 의존하는 개발 진단 도구다.
    # Windows 에는 대응물이 없어(후순위, docs/WINDOWS_PORT.md §6-3) 지원하지 않는다.
    # pywinpty 기반 ConPTY 녹화는 별도 과제이므로, 모호한 ModuleNotFoundError 대신
    # 명확한 메시지를 내고 종료한다. (replay() 재생은 순수 로직이라 Windows 에서 동작.)
    if os.name == "nt":
        sys.stderr.write(
            "record: Windows 에서는 PTY 녹화를 지원하지 않습니다"
            "(POSIX 전용 진단 도구). replay 재생은 사용 가능합니다.\n")
        return 2
    import pty
    import select
    import subprocess
    import termios
    import tty

    cmd = argv or [os.environ.get("SHELL", "/bin/sh")]
    env = dict(os.environ)
    env["TERM"] = env.get("TERM", "xterm-256color")
    master, slave = pty.openpty()
    try:
        set_winsize(slave, rows, cols)
    except OSError:
        pass
    try:
        proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave,
                                preexec_fn=os.setsid, env=env, close_fds=True)
    except FileNotFoundError:
        sys.stderr.write(f"record: 명령을 찾을 수 없음: {cmd[0]}\n")
        return 127

    out = open(path, "wb")
    stdin_fd = sys.stdin.fileno()
    isatty = os.isatty(stdin_fd)
    old = None
    if isatty:
        try:
            old = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd)
        except termios.error:
            old = None

    def _read_master():
        try:
            return os.read(master, 65536)
        except OSError:
            return b""

    try:
        while True:
            watch = [master] + ([stdin_fd] if isatty else [])
            try:
                rl, _, _ = select.select(watch, [], [], 0.1)
            except (InterruptedError, OSError):
                break
            if master in rl:
                chunk = _read_master()
                if chunk:
                    out.write(chunk)
                    out.flush()
                    if echo:
                        try:
                            os.write(1, chunk)
                        except OSError:
                            pass
            if isatty and stdin_fd in rl:
                try:
                    ind = os.read(stdin_fd, 65536)
                except OSError:
                    ind = b""
                if ind:
                    try:
                        os.write(master, ind)
                    except OSError:
                        pass
            if proc.poll() is not None:
                # 자식 종료: 남은 출력을 끝까지 비운다(slave 가 아직 열려 있음)
                while True:
                    try:
                        rl, _, _ = select.select([master], [], [], 0.05)
                    except OSError:
                        rl = []
                    if master not in rl:
                        break
                    chunk = _read_master()
                    if not chunk:
                        break
                    out.write(chunk)
                    if echo:
                        try:
                            os.write(1, chunk)
                        except OSError:
                            pass
                break
    finally:
        if old is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old)
        out.close()
        for f in (slave, master):
            try:
                os.close(f)
            except OSError:
                pass
        try:
            proc.wait(timeout=1)
        except Exception:
            pass
    sys.stderr.write(f"\nrecorded {cols}x{rows} → {path}\n"
                     f"replay: python3 pytmux.py replay {path} --cols {cols} "
                     f"--rows {rows} --ruler\n")
    return 0


def term_size():
    try:
        sz = os.get_terminal_size()
        return sz.columns, sz.lines
    except OSError:
        return 80, 24
