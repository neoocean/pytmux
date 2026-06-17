"""실제 화면 스크린샷 하네스 — 진짜 Textual 클라이언트를 가짜 터미널(PTY) 아래
띄워 **사용자가 실제로 보는 화면**(ANSI 프레임)을 캡처한다(§10 사용자 질문 대응).

헤드리스 단언 테스트(`run_test`/`server_only`)는 위젯 상태·합성 셀을 검증하지만,
실제 `client.py` 의 `_composite`/Textual CSS/드라이버 렌더 경로를 통과한 **터미널
출력**은 보지 못한다. 이 하네스는 그 출력을 그대로 잡아 트레이스백·테두리·프롬프트
유무 같은 "눈으로 보는" 회귀를 자동 검증한다. run-pytmux 스킬의 driver.py(서버가
합성한 스크린샷)와 상보적이다 — 이쪽은 진짜 클라 프로세스의 화면이다.

POSIX 전용(stdlib `pty`). Windows 에선 capture 가 RuntimeError 를 던지므로 호출부가
가드한다.

사용 예:
    raw, alive = ptyshot.capture([sys.executable, "pytmux.py", "--socket", sock])
    txt = ptyshot.screen_text(raw)
    assert alive and not ptyshot.has_traceback(raw)
"""
from __future__ import annotations

import os
import re
import select
import signal
import time

IS_WINDOWS = os.name == "nt"

# ANSI 제어 시퀀스(CSI/OSC/기타) 제거용 — 화면을 사람이 읽는 평문으로.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[\]P^_][^\x07\x1b]*(?:\x07|\x1b\\)?"
                   r"|\x1b[=>()][A-Za-z0-9]?")


def capture(argv, *, cols: int = 100, rows: int = 30, seconds: float = 4.0,
            env: dict | None = None, feed: bytes | None = None):
    """argv 를 PTY(가짜 터미널) 아래 실행하고 seconds 동안 출력을 모은 뒤 종료한다.

    반환: (raw_bytes, alive_at_end). alive_at_end=False 면 캡처 시간 안에 프로세스가
    스스로 종료한 것(=즉시 종료/크래시 신호). feed 가 있으면 0.6초 뒤 PTY 로 써
    키 입력을 흉내낸다(예: b':' 로 명령 프롬프트 열기)."""
    if IS_WINDOWS:
        raise RuntimeError("ptyshot.capture 는 POSIX 전용(stdlib pty)")
    import fcntl
    import pty
    import struct
    import termios

    e = dict(os.environ)
    e.pop("PYTMUX", None)
    e.pop("LC_PYTMUX", None)
    # §1.7: 개발 셸이 ssh 세션이어도 캡처 자식은 "맨 로컬 터미널" 로 흉내낸다 —
    # SSH_* 가 새면 attach 의 in-band 중첩 프로브(XTVERSION)가 발화해 캡처마다
    # 0.4초 대기 + 질의 바이트가 끼어든다.
    e.pop("SSH_CONNECTION", None)
    e.pop("SSH_TTY", None)
    e["TERM"] = "xterm-256color"
    if env:
        e.update(env)
    pid, fd = pty.fork()
    if pid == 0:                      # 자식: 클라이언트로 exec
        os.execvpe(argv[0], list(argv), e)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    buf = bytearray()
    t0 = time.time()
    alive = True
    fed = feed is None
    while time.time() - t0 < seconds:
        if not fed and time.time() - t0 >= 0.6:
            try:
                os.write(fd, feed)
            except OSError:
                pass
            fed = True
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            buf += data
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid:
            alive = False
            break
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass
    return bytes(buf), alive


def screen_text(raw: bytes) -> str:
    """캡처한 raw 바이트에서 ANSI 이스케이프를 제거해 화면 평문으로."""
    return _ANSI.sub("", raw.decode("utf-8", "replace"))


def has_traceback(raw: bytes) -> bool:
    """Textual 이 크래시 시 터미널에 토해내는 파이썬 트레이스백이 보이는지."""
    return "Traceback (most recent call last)" in raw.decode("utf-8", "replace")
