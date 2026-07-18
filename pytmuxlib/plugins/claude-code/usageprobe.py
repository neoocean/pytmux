"""M19: 그림자 `/usage` 질의 — 사용자에게 안 보이는 숨은 대화형 `claude` 세션을
PTY 로 띄워 `/usage` 패널을 렌더·스크랩해 사용량 한도를 얻는다(방법 B, §10).

핵심: print 모드(`claude -p`)는 `/usage` 를 한 줄로 잘라 한도 숫자를 안 주므로(§10.4
B2 기각), **대화형 TUI 패널**을 pyte 로 렌더해 긁어야 세션(5시간)/주간 한도 % 와 리셋
시각이 나온다. 사용자 화면(현재 세션)엔 전혀 안 뜬다(off-screen 서브프로세스).

블로킹 함수다 — 서버는 `loop.run_in_executor` 로 호출한다. 실패/타임아웃은 전부
None(예외 안 냄).

크로스플랫폼(docs/internal/WINDOWS_PORT.md): I/O 는 `_Session` 백엔드로 갇힌다.
  * **POSIX**(`_PosixSession`): `pty.openpty`+`subprocess`(close_fds·start_new_session)
    로 fork 안전(멀티스레드 executor)하게 띄우고, `select` 로 타임아웃 read 한다.
  * **Windows**(`_WinSession`): fork·`select`·`termios` 가 없으므로 ConPTY(`pywinpty`)
    로 띄운다. pywinpty 의 `read()` 는 **블로킹**이라 동기 펌프에서 타임아웃을 못 거니
    `pty_backend._WinPty` 와 같은 모델로 **전용 리더 스레드**가 블로킹 read 후 바이트를
    버퍼에 쌓고, 메인 펌프는 그 버퍼를 시간기반으로 폴링한다(타임아웃 보장).
세션 생성은 `_open_session` 팩토리로 분리해 테스트가 실 `claude` 없이 캔드 패널을
재생할 수 있게 한다."""
from __future__ import annotations

import os
import subprocess
import threading
import time

from .claude import (claude_account, claude_account_full, claude_model,
                     parse_usage)

IS_WINDOWS = os.name == "nt"

_READ = 65536

# 조직 관리 설정(managed settings) 최초 1회 승인 게이트(2026-07-15 요청): OTEL/텔레
# 메트리 관련 관리 설정이 걸린 조직 계정은 claude 부팅 직후 이 화면으로 멈춰 서서,
# 위 "shortcuts"/"shift+tab"/"for agents" 부팅 신호가 영영 안 뜨고 boot_timeout 으로
# 조용히 실패한다(그림자 프로브는 무인 실행이라 사람이 답할 수 없음). 화면의 기본
# 선택(❯ 1. Yes, I trust these settings)을 그대로 확정하는 Enter 1회만 주입해 통과
# 시킨다 — 이후 실제 부팅 신호 대기로 이어간다.
#
# 안전(2026-07-16 검수 SEC-1): 헤더 문구만 보고 무턱대고 Enter 를 치면 향후 claude
# 빌드가 옵션 순서를 바꾸거나 기본을 "No, exit" 로 두면 **미지의 선택을 확정**한다.
# 게다가 수락은 사용자 config 에 머신 전역으로 남아, 프로브가 usage_refresh_sec 마다
# 도는 탓에 조직이 앞으로 푸시할 임의 관리설정(텔레메트리 엔드포인트·권한정책)까지
# 사용자가 검토화면을 보기도 전에 조용히 자동승인될 수 있다. 그래서 ① 긍정 기본선택
# 줄(❯/> 셀렉터가 "Yes, I trust these settings" 위에 있을 때)이 실제로 화면에 떠
# 있을 때만 Enter 를 치고 ② 부팅 대기(_open 직후 첫 wait_for)에서만 발동시킨다 —
# /usage·/status 스크랩 중엔 이 자동승인을 열지 않는다(부팅 이후엔 이 화면이 안 뜬다).
_MANAGED_SETTINGS_MARK = "Managed settings require approval"
_MANAGED_SETTINGS_YES = "Yes, I trust these settings"
_MANAGED_SETTINGS_ACCEPT = b"\r"


def _managed_yes_selected(scr: str) -> bool:
    """관리설정 화면에서 긍정 기본선택(❯/> 셀렉터가 'Yes, I trust these settings'
    줄에 있음)이 실제로 떠 있는지. 셀렉터가 다른 줄(예: 'No, exit')로 옮겨갔거나
    문구가 바뀌면 False → Enter 를 치지 않고 프로브는 안전하게 실패(None)한다."""
    if _MANAGED_SETTINGS_MARK not in scr:
        return False
    for ln in scr.splitlines():
        if _MANAGED_SETTINGS_YES in ln and ("❯" in ln or ln.lstrip().startswith(">")):
            return True
    return False


class _PosixSession:
    """POSIX: pty.openpty + subprocess, select 기반 타임아웃 read."""

    def __init__(self, argv, cwd, env, cols, rows):
        import pty
        import struct

        self._master, slave = pty.openpty()
        try:
            import fcntl
            import termios
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass
        try:
            self._proc = subprocess.Popen(
                argv, stdin=slave, stdout=slave, stderr=slave,
                cwd=cwd, env=env, close_fds=True, start_new_session=True)
        finally:
            try:
                os.close(slave)
            except OSError:
                pass

    def read(self, timeout: float) -> bytes:
        import select
        try:
            r, _, _ = select.select([self._master], [], [], timeout)
        except OSError:
            return b""
        if not r:
            return b""
        try:
            return os.read(self._master, _READ)
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        try:
            os.write(self._master, data)
        except OSError:
            pass

    def kill(self) -> None:
        try:
            self._proc.kill()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=3)
        except Exception:
            pass

    def close(self) -> None:
        try:
            os.close(self._master)
        except OSError:
            pass


class _WinSession:
    """Windows: ConPTY(pywinpty). 블로킹 read 를 리더 스레드가 버퍼에 쌓고, 메인
    펌프는 버퍼를 폴링한다(pty_backend._WinPty 의 스레드 펌프 모델을 동기 버전으로)."""

    def __init__(self, argv, cwd, env, cols, rows):
        from winpty import PtyProcess

        spec = argv if len(argv) > 1 else argv[0]
        self._proc = PtyProcess.spawn(
            spec, cwd=cwd, env=env, dimensions=(max(1, rows), max(1, cols)))
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader = threading.Thread(
            target=self._read_loop, name=f"usageprobe-{self._proc.pid}",
            daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        proc = self._proc
        while not self._stop.is_set():
            try:
                s = proc.read(_READ)            # 블로킹 — 데이터/EOF 까지 대기
            except EOFError:
                break
            except Exception:                   # ConPTY 종료 시 잡다한 오류 방어
                break
            if s:
                b = s.encode("utf-8", "replace")  # pywinpty 가 이미 디코드한 str → bytes
                with self._lock:
                    self._buf += b
            elif not proc.isalive():
                break

    def read(self, timeout: float) -> bytes:
        end = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._buf:
                    out = bytes(self._buf)
                    self._buf.clear()
                    return out
            if time.monotonic() >= end:
                return b""
            time.sleep(0.02)

    def write(self, data: bytes) -> None:
        try:
            self._proc.write(data.decode("utf-8", "replace"))
        except Exception:
            pass

    def kill(self) -> None:
        try:
            self._proc.terminate(force=True)
        except Exception:
            pass

    def close(self) -> None:
        self._stop.set()                        # 리더 스레드(daemon)는 read/EOF 후 종료
        try:
            self._proc.close()
        except Exception:
            pass


def _open_session(argv, cwd, env, cols, rows):
    """플랫폼별 `_Session` 을 연다(실패 시 예외 — 호출부가 None 으로 흡수).
    테스트는 이 함수를 몽키패치해 실 `claude` 없이 캔드 패널을 재생한다."""
    if IS_WINDOWS:
        return _WinSession(argv, cwd, env, cols, rows)
    return _PosixSession(argv, cwd, env, cols, rows)


def query_usage(cmd: str = "claude", cwd: str | None = None,
                cols: int = 95, rows: int = 45,
                boot_timeout: float = 12.0, panel_timeout: float = 8.0):
    """숨은 `claude` 를 띄워 `/usage` 패널을 스크랩·파싱한다. 결과 dict(parse_usage,
    추가로 그림자 세션 계정 `account`: 일치 확인용·없으면 None) 또는 None. 입력 주입은
    `/usage`+Enter (계정 미식별 시에만 Esc+`/status` 1회 추가 — 아래) 뿐이고
    끝나면 즉시 kill 한다.

    `account`: 이 숨은 세션이 로그인된 계정(claude_account 별칭). 폰/데스크탑 앱과
    **다른 계정**이면 한도 %·리셋이 실제로 달라지므로(요청), 사용자가 눈으로
    대조할 수 있게 함께 싣는다. 계정 라벨은 /status(Status 탭)에만 있어(부팅·
    Usage 탭엔 부재 — 2026-06-11 실관찰) 거기까지 못 잡으면 None.

    `model`: 이 숨은 세션의 활성 모델(claude_model 파싱값, 예 'opus-4.8'). 라이브
    Claude 패널은 모델 배지를 **상시 표시하지 않아**(idle 푸터엔 'auto mode on …'
    뿐, 배지는 /model 변경 직후 등에만 잠깐 — 2026-06-22 실관찰) 화면 스크랩만으론
    토큰이 model NULL('?')로 적재되는 일이 잦다. 그림자 프로브는 부팅 배지·/status
    의 Model 라인에서 활성 모델을 잡아 model 폴백을 제공한다(서버 _scan_claude 가
    라이브 배지 우선·없으면 이 값으로 채움). /usage 패널은 'Sonnet only' 같은 한도
    카테고리 라벨이 모델로 오인돼 출처에서 제외한다. 못 잡으면 None."""
    try:
        from pytmuxlib.nativescreen import NativeScreen
        from pytmuxlib.vtparse import VTTokenizer
    except Exception:
        return None
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env.pop("PYTMUX", None)
    env.pop("LC_PYTMUX", None)
    # Windows 는 PATH/PATHEXT 검색이 spawn 백엔드마다 다를 수 있어 미리 .exe 로 해석.
    argv = [cmd]
    if IS_WINDOWS:
        import shutil
        argv = [shutil.which(cmd) or cmd]
    try:
        sess = _open_session(argv, cwd, env, cols, rows)
    except Exception:
        return None

    # 숨은 세션 TUI 스크랩용 화면(구 pyte.Screen 대응 — M4b native 단일화). alt-screen
    # 전환은 무시(alt_hook=None)해도 /usage TUI 는 2J+draw 로 단일 버퍼에 그려 disp()
    # 가 같은 가시 콘텐츠를 낸다.
    sc = NativeScreen(cols, rows)
    st = VTTokenizer(sc)

    def pump(sec: float) -> None:
        end = time.monotonic() + sec
        while time.monotonic() < end:
            data = sess.read(0.15)
            if data:
                st.feed(data)

    def disp() -> str:
        return "\n".join(sc.display)

    managed_settings_seen = False

    def wait_for(subs, maxs: float, accept_managed: bool = False) -> bool:
        """subs(문자열 또는 후보 튜플) 중 하나라도 화면에 뜰 때까지 대기. `accept_managed`
        (부팅 대기에서만 True)이면 그 사이 조직 관리 설정 승인 화면이 뜨고 **긍정
        기본선택(❯ Yes, I trust these settings)이 실제로 선택돼 있을 때만** Enter 1회를
        확정해 넘기고 대기를 이어간다 — 그림자 프로브는 무인 실행이라 사람이 답할 수
        없다(2026-07-15). 셀렉터가 다른 옵션에 있거나 문구가 바뀌면 치지 않는다(SEC-1)."""
        nonlocal managed_settings_seen
        cands = (subs,) if isinstance(subs, str) else tuple(subs)
        end = time.monotonic() + maxs
        while time.monotonic() < end:
            pump(0.3)
            scr = disp()
            if any(c in scr for c in cands):
                return True
            if (accept_managed and not managed_settings_seen
                    and _managed_yes_selected(scr)):
                managed_settings_seen = True
                sess.write(_MANAGED_SETTINGS_ACCEPT)
                end = time.monotonic() + maxs   # 승인 후 실제 부팅 대기시간 재확보
        return False

    try:
        # 입력 프롬프트 준비까지 대기. 트러스트 대화상자 등으로 안 뜨면 타임아웃
        # → None(안전). claude 버전마다 부팅 화면 힌트가 달라 여러 신호 중 하나라도
        # 잡히면 준비로 본다: 구버전 "? for shortcuts", v2.1.x 푸터("shift+tab to
        # cycle"·"← for agents"). 어느 쪽도 입력 박스가 떴다는 신뢰 신호다.
        if not wait_for(("shortcuts", "shift+tab", "for agents"), boot_timeout,
                        accept_managed=True):
            return None
        # 부팅 화면에 계정/조직 표시가 있으면 먼저 캡처(/usage 가 화면을 덮기 전).
        # 별칭(acct, 로그·DB 영속용)과 전체 이메일(acct_full, 사용자 본인 화면 표시용)을
        # 같은 텍스트에서 함께 잡는다 — footer 와 동일한 프라이버시 분리(별칭=디스크,
        # 전체=휘발성 표시). 팝업이 전체 이메일을 보이도록(요청).
        acct = claude_account(disp())
        acct_full = claude_account_full(disp())
        model = claude_model(disp())   # 부팅/welcome 화면 배지(있으면)
        sess.write(b"/usage\r")
        wait_for("% used", panel_timeout)
        pump(0.4)
        screen = disp()
        usage = parse_usage(screen)
        if usage is not None:
            # /usage 화면에도 계정 신호가 있으면 보강(부팅서 못 잡았을 때).
            acct = acct or claude_account(screen)
            acct_full = acct_full or claude_account_full(screen)
            # 주의: /usage 패널에서는 모델을 뽑지 않는다 — 'Current week (Sonnet
            # only)' 같은 **한도 카테고리** 라벨이 활성 모델로 오인되기 때문(실측).
            # 모델은 부팅 배지·/status(Model 라인)에서만 잡는다(아래).
            if not acct or not model:
                # 계정 식별 폴백(2026-06-11 §5.5 관찰): 부팅 화면·/usage(Usage 탭)
                # 에는 계정 라벨이 **아예 없다**(실캡처 — limits 스냅샷 20/20 이
                # account None 이던 원인). 같은 설정 패널의 Status 탭(/status)에만
                # `Organization:`/`Email:` 라벨이 있으므로, 패널을 닫고 /status 를
                # 한 번 더 스크랩한다(같은 숨은 세션 재사용·토큰 비용 0·계정이
                # 이미 잡혔으면 생략). /status 탭은 활성 모델도 보여줘(2026-06-22)
                # 배지가 화면에 안 떠 model 이 None 일 때 여기서 함께 채운다.
                sess.write(b"\x1b")             # /usage 패널 닫기(Esc to cancel)
                pump(0.2)
                sess.write(b"/status\r")
                if wait_for(("Organization:", "Email:", "Login method"),
                            panel_timeout):
                    pump(0.4)
                    acct = acct or claude_account(disp())
                    acct_full = acct_full or claude_account_full(disp())
                    model = model or claude_model(disp())
            usage["account"] = acct
            usage["model"] = model
            # 전체 이메일(없으면 별칭 폴백). DB 영속 컬럼엔 없으므로 디스크엔 안 남고,
            # 라이브 status 로만 흘러 팝업/오버레이가 전체를 표시한다.
            usage["account_full"] = acct_full or acct
        return usage
    except Exception:
        return None
    finally:
        sess.kill()
        sess.close()
