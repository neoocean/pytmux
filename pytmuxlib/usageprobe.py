"""M19: 그림자 `/usage` 질의 — 사용자에게 안 보이는 숨은 대화형 `claude` 세션을
PTY 로 띄워 `/usage` 패널을 렌더·스크랩해 사용량 한도를 얻는다(방법 B, §10).

핵심: print 모드(`claude -p`)는 `/usage` 를 한 줄로 잘라 한도 숫자를 안 주므로(§10.4
B2 기각), **대화형 TUI 패널**을 pyte 로 렌더해 긁어야 세션(5시간)/주간 한도 % 와 리셋
시각이 나온다. 사용자 화면(현재 세션)엔 전혀 안 뜬다(off-screen 서브프로세스).

블로킹 함수다 — 서버는 `loop.run_in_executor` 로 호출한다. `pty.fork` 대신
`pty.openpty`+`subprocess`(close_fds·start_new_session) 를 써서 멀티스레드 executor
에서 fork 안전성을 확보한다. 실패/타임아웃은 전부 None(예외 안 냄)."""
from __future__ import annotations

import os
import pty
import select
import struct
import subprocess
import time

from .claude import claude_account, parse_usage


def query_usage(cmd: str = "claude", cwd: str | None = None,
                cols: int = 95, rows: int = 45,
                boot_timeout: float = 12.0, panel_timeout: float = 8.0):
    """숨은 `claude` 를 띄워 `/usage` 패널을 스크랩·파싱한다. 결과 dict(parse_usage,
    추가로 그림자 세션 계정 `account`: 일치 확인용·없으면 None) 또는 None. 입력 주입은
    `/usage`+Enter 한 번뿐이고 끝나면 즉시 kill 한다.

    `account`: 이 숨은 세션이 로그인된 계정(claude_account 별칭). 폰/데스크탑 앱과
    **다른 계정**이면 한도 %·리셋이 실제로 달라지므로(요청), 사용자가 눈으로
    대조할 수 있게 함께 싣는다. 신뢰 신호(`<email>'s Organization`)가 안 보이면 None."""
    try:
        import pyte
    except Exception:
        return None
    master, slave = pty.openpty()
    try:
        try:
            import fcntl
            import termios
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env.pop("PYTMUX", None)
        env.pop("LC_PYTMUX", None)
        try:
            proc = subprocess.Popen(
                [cmd], stdin=slave, stdout=slave, stderr=slave,
                cwd=cwd, env=env, close_fds=True, start_new_session=True)
        except Exception:
            return None
    finally:
        try:
            os.close(slave)
        except OSError:
            pass

    sc = pyte.Screen(cols, rows)
    st = pyte.ByteStream(sc)

    def pump(sec: float) -> None:
        end = time.monotonic() + sec
        while time.monotonic() < end:
            try:
                r, _, _ = select.select([master], [], [], 0.15)
            except OSError:
                return
            if r:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    return
                if not data:
                    return
                st.feed(data)

    def disp() -> str:
        return "\n".join(sc.display)

    def wait_for(sub: str, maxs: float) -> bool:
        end = time.monotonic() + maxs
        while time.monotonic() < end:
            pump(0.3)
            if sub in disp():
                return True
        return False

    try:
        # 입력 프롬프트 준비("? for shortcuts")까지 대기. 트러스트 대화상자 등으로
        # 안 뜨면 타임아웃 → None(안전).
        if not wait_for("shortcuts", boot_timeout):
            return None
        # 부팅 화면에 계정/조직 표시가 있으면 먼저 캡처(/usage 가 화면을 덮기 전).
        acct = claude_account(disp())
        os.write(master, b"/usage\r")
        wait_for("% used", panel_timeout)
        pump(0.4)
        screen = disp()
        usage = parse_usage(screen)
        if usage is not None:
            # /usage 화면에도 계정 신호가 있으면 보강(부팅서 못 잡았을 때).
            usage["account"] = acct or claude_account(screen)
        return usage
    except Exception:
        return None
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        try:
            os.close(master)
        except OSError:
            pass
