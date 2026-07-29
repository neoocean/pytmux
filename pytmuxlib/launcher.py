"""데몬화 · 런처 · 외부 제어 CLI.

OS 별 분기(데몬화/소켓)는 직접 알지 않고 추상층만 부른다(docs/internal/WINDOWS_PORT.md §7-c):
  * 서버 데몬 기동/존재확인 → pytmuxlib.proc (Unix setsid 분리 / Windows DETACHED).
  * 소켓 접속/제어/probe → pytmuxlib.ipc (Unix AF_UNIX / Windows TCP 루프백+포트파일).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from . import ipc, proc, protocol, sshwrap
# NOTE: client(=textual)·server(=model→pyte→wcwidth) 는 여기서 import 하지 않는다.
# 가벼운 제어 명령(ls/cmd/kill)이 launcher 만 거쳐도 textual 전체나 pyte/wcwidth 를
# 로드해 기동이 느려졌다(Windows 제보). attach 경로의 client, `server` 명령의
# run_server 모두 main() 안에서 필요 시점에만 지연 import 한다(A4).


def can_connect(sock_path: str) -> bool:
    return ipc.probe(sock_path)


# §5.9: wait_server 백오프 상수(종전 루프 본문 bare 리터럴 → 명명·튜닝 일원화). 초기
# 폴 간격→상한까지 지수 증가. 총 예산 polls*interval(≈4s)은 종전과 동일.
_WAIT_POLL_INITIAL = 0.002   # 첫 폴 간격(서버가 <20ms 에 뜨면 체감 지연 최소화)
_WAIT_POLL_BACKOFF = 1.6     # 폴 간격 지수 증가율(interval 상한까지)
# 원격(stdio-proxy) 자동 기동 대기 예산: 500*0.02 = 10s. 로컬 기본(4s)보다 넉넉하되
# serverremote 의 핸드셰이크 readline 타임아웃(15s) 안에 든다.
_REMOTE_START_POLLS = 500


def wait_server(sock_path: str, *, polls: int = 200, interval: float = 0.02) -> bool:
    """서버가 listen 떠 접속 가능해질 때까지 폴링. 성공이면 True, 시간 초과면 False.

    A2: 초기엔 촘촘히(2ms~) 지수 백오프 후 `interval`(20ms) 상한으로 폴 — 서버가
    빨리(<20ms) 뜬 경우의 체감 지연을 줄인다(고정 20ms 면 최대 20ms 허비). 총 예산은
    기존과 동일(polls*interval ≈ 4s)으로 유지."""
    deadline = time.monotonic() + polls * interval
    delay = _WAIT_POLL_INITIAL
    while True:
        if ipc.probe(sock_path):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(delay, interval))
        delay *= _WAIT_POLL_BACKOFF


def server_auth_ok(sock_path: str) -> bool:
    """서버가 listen 만 하는 게 아니라 **인증까지 통과**하는지 검사(probe 보강).

    probe() 는 connect 가능 여부만 본다 — 토큰 파일이 사라졌거나 어긋난 좀비
    서버도 listen 은 계속하므로 살아있어 보이지만, 그 서버엔 어떤 클라이언트도
    다시 붙지 못한다(attach 가 화면만 깜빡이고 빠져나옴: 핸드셰이크 auth_failed).
    가벼운 `list` 제어 요청으로 토큰 인증까지 왕복해, 정상 응답이면 True. 무응답
    (None)·error(auth_failed 등)이면 False. 새 서버는 token 을 listen **전에**
    게시하므로(serverio.serve), 소켓 경로가 새 서버로 원자 교체(ipc.start_server
    의 os.replace)된 순간부터 True 가 된다 — 같은 경로를 잠깐 더 붙든 좀비는 옛
    토큰이라 여기서 True 가 되지 않는다(race-free 판정)."""
    reply = control_request(sock_path, {"t": "list"})
    return isinstance(reply, dict) and reply.get("t") != "error"


def wait_server_authed(sock_path: str, *, polls: int = 200,
                       interval: float = 0.02) -> bool:
    """server_auth_ok 가 True 가 될 때까지 폴링(wait_server 의 auth 판정판).

    좀비를 교체하려 새 서버를 띄운 직후엔 connectability(probe)만으로는 부족하다
    — 경로가 새 서버로 교체되기 전 짧은 창엔 probe 가 좀비를 맞혀 True 가 되기
    때문. auth 를 기다려야 새 서버를 정확히 본다. 백오프 예산은 wait_server 와 동일."""
    deadline = time.monotonic() + polls * interval
    delay = _WAIT_POLL_INITIAL
    while True:
        if server_auth_ok(sock_path):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(delay, interval))
        delay *= _WAIT_POLL_BACKOFF


def server_boot_log(sock_path: str) -> str:
    """서버 데몬의 **부팅 stderr** 를 받아 두는 파일 경로(<sock>.boot.log).

    서버가 여는 `<sock>.error.log` 는 서버가 살아난 **뒤**의 예외만 담는다 — 그
    앞에서 죽으면(import 실패·구문 오류·권한) 아무 흔적도 남지 않았다."""
    return f"{sock_path}.boot.log"


def spawn_server(sock_path: str) -> None:
    """서버 데몬을 분리 기동한다(부팅 stderr 는 진단용으로 boot.log 에 남긴다).

    모든 자동 기동 지점(attach·stdio-proxy·ensure_server·start-server)이 이 한
    곳을 지나 실패 사유가 항상 회수 가능하게 한다.

    소켓의 상위 디렉터리는 미리 만든다(0o700). 기본 경로는 ipc 가 만들어 주지만
    명시 `--socket` 은 아무도 만들지 않아, 없는 디렉터리를 주면 서버가 bind 에서
    죽고 **error.log 도 같은 없는 디렉터리라 못 써서** 흔적 없이 rc=0 으로 사라졌다
    (런처는 사유 0줄의 '서버 기동 실패'만 출력). 디렉터리가 있어야 boot.log 도
    쓸 수 있다 — 진단의 전제 조건이다."""
    d = os.path.dirname(os.path.abspath(sock_path))
    try:
        os.makedirs(d, exist_ok=True)
        if os.name != "nt":
            os.chmod(d, 0o700)          # 토큰/소켓이 사는 곳 — 소유자 전용
    except OSError:
        pass                            # 만들지 못해도 기동은 시도(사유는 아래서 보고)
    proc.spawn_detached(proc.server_argv(sock_path),
                        stderr_path=server_boot_log(sock_path))


def server_boot_error(sock_path: str, *, tail: int = 4096) -> str:
    """기동 실패 시 boot.log 에서 **사람이 읽을 한 줄 사유**를 뽑는다(없으면 "").

    실측 사례(2026-07-28): 원격 호스트의 `python3` 가 homebrew 업그레이드로
    3.13→3.14 로 바뀌어 requirements 가 없는 인터프리터를 가리켰다. 데몬은
    ModuleNotFoundError 로 즉사했지만 stderr 가 /dev/null 이라 사용자에게는
    '인증 대기 시한 초과' 만 보였다 — 원인이 인터프리터에 있음을 알 길이 없었다.
    의존성 누락은 특히 흔하므로 **어느 인터프리터에 무엇이 없는지 + 설치 명령**
    까지 만들어 준다."""
    text = ""
    try:
        with open(server_boot_log(sock_path), "rb") as f:
            try:
                f.seek(-tail, os.SEEK_END)
            except OSError:                    # 파일이 tail 보다 짧음
                f.seek(0)
            text = f.read().decode("utf-8", "replace")
    except OSError:
        pass
    m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", text)
    if m:
        py = proc.server_argv(sock_path)[0]
        return (f"서버 인터프리터에 필수 의존성 '{m.group(1)}' 이(가) 없습니다 "
                f"({py}) — `{py} -m pip install -r requirements.txt`")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        return lines[-1][:300]
    # 로그가 비었다 = 데몬이 stderr 한 줄 못 남기고 죽었거나, 로그 자체를 못 썼다.
    # 후자는 소켓 디렉터리 문제인데 그러면 서버도 bind 못 해 같이 죽는다 — 여기서
    # 짚어 주지 않으면 사유가 영영 0줄이다(런처가 만들 수 있는 마지막 단서).
    d = os.path.dirname(os.path.abspath(sock_path))
    if not os.path.isdir(d):
        return f"소켓 디렉터리가 없습니다: {d}"
    if not os.access(d, os.W_OK):
        return f"소켓 디렉터리에 쓸 수 없습니다(권한): {d}"
    return ""


def report_server_start_failure(sock_path: str, headline: str) -> None:
    """기동 실패를 stderr 로 보고한다 — 가능하면 boot.log 의 진짜 사유를 붙여서.
    (원격 stdio-proxy 의 stderr 는 ssh 를 타고 로컬 notice 로 그대로 표시된다.)"""
    detail = server_boot_error(sock_path)
    print(f"{headline}: {detail}" if detail else headline, file=sys.stderr)


def ensure_server(sock_path: str):
    if ipc.probe(sock_path):
        return
    # 부모 생애와 무관하게 살아남는 분리 서버 프로세스를 띄운다(Unix setsid /
    # Windows DETACHED_PROCESS). 그 뒤 listen 이 떠 접속 가능해질 때까지 대기.
    spawn_server(sock_path)
    if not wait_server(sock_path):
        report_server_start_failure(sock_path, "pytmux: 서버 기동 실패")
        sys.exit(1)


def control_request(sock_path: str, obj: dict):
    s = ipc.control_socket(sock_path)
    if s is None:
        return None
    # 제어 프레임에도 와이어 버전을 실어 서버가 비호환을 거절할 수 있게 한다(#7).
    # 연결 인증 토큰(F1)도 함께 실어 서버가 무인가 접속을 거절하게 한다(없으면 생략).
    frame = {"proto": protocol.PROTO_VERSION}
    tok = ipc.read_token(sock_path)
    if tok:
        frame["token"] = tok
    frame.update(obj)
    data = json.dumps(frame).encode()
    s.sendall(len(data).to_bytes(4, "big") + data)
    try:
        header = _recvn(s, 4)
        if not header:
            return None
        n = int.from_bytes(header, "big")
        if n > protocol.MAX_FRAME:      # 무제한 응답 길이 → OOM 방지(read_msg 와 동일 상한)
            return None
        payload = _recvn(s, n)
        try:
            return json.loads(payload)  # bytes 직접; 손상·비-JSON 응답은 None
        except (ValueError, UnicodeDecodeError):
            return None
    finally:
        s.close()


def _recvn(s, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


# 원격(ssh) 중첩 표식. 패널 셸 env 에 심기고 ssh 래퍼가 SendEnv 로 원격에 전파한다
# (pytmuxlib.sshwrap). sshwrap.NEST_MARKER 와 반드시 일치.
NEST_MARKER = "LC_PYTMUX"


def nesting_blocked() -> bool:
    """pytmux 패널 안에서 또 pytmux 를 띄우려는 중첩인지(로컬·원격 공통 판정 지점).

    - **로컬**: 패널 셸에 서버가 `$PYTMUX`(소켓 경로)를 심으므로 그게 설정돼 있으면 중첩.
    - **원격(ssh)**: `$PYTMUX` 는 ssh 로 전파 안 되지만, 패널 셸의 ssh 래퍼가 표식
      `$LC_PYTMUX` 를 SendEnv 로 원격에 전파한다(sshwrap). 원격 pytmux 는 `$PYTMUX`
      가 없어도 이 표식을 보고 중첩을 거부한다(docs/internal/HANDOFF.md §10).

    **liveness 게이트**: env 마커는 떠난 세션의 잔재로 남는다 — 패널 안에서 띄운
    터미널/claude 가 detach·서버 종료 뒤에도 `$PYTMUX`/`$LC_PYTMUX` 를 물려받은
    채로 살아 있으면, 마커 *존재* 만으로 거부하던 옛 로직은 죽은 서버에도 영구
    오탐(처음 실행인데 "이미 안에서 실행 중")을 냈다. 그래서 마커별로 권위 조건을 둔다:
    - `$PYTMUX`(소켓 경로)는 **그 소켓이 실제 접속 가능할 때만** 로컬 중첩으로 본다
      (`ipc.probe`). 죽은 소켓을 가리키면 잔재이므로 무시.
    - `$LC_PYTMUX` 는 ssh 로 전파되는 *원격* 표식이라 **SSH 세션 안에서만** 권위가
      있다. 비-ssh(로컬) 셸의 표식은 잔재이므로 무시(진짜 로컬 중첩은 위의 살아있는
      `$PYTMUX` 가 이미 잡는다 — 패널 셸엔 둘 다 심긴다).

    우회 수단은 `unset PYTMUX LC_PYTMUX` 뿐(강제 옵션은 제공하지 않는다)."""
    sock = os.environ.get("PYTMUX")
    if sock and ipc.probe(sock):
        return True
    return bool(os.environ.get(NEST_MARKER)) and bool(
        os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))


# §1.7 in-band 중첩 감지 프로브 대기 상한. 호스트가 pytmux 면 응답은 보통 수십 ms
# (ssh RTT)에 오고, 실제 터미널은 자기 이름으로 응답해(iTerm2/kitty/xterm 등 XTVERSION
# 지원) 조기 종료된다 — 전체 대기는 XTVERSION 무응답 단말에서만 발생.
NEST_PROBE_TIMEOUT = 0.4


def host_terminal_is_pytmux(timeout: float = NEST_PROBE_TIMEOUT,
                            rfd: int | None = None,
                            wfd: int | None = None) -> bool:
    """호스팅 단말에 XTVERSION(ESC[>0q)을 질의해 응답 단말명이 pytmux 면 True(§1.7).

    env 마커(`$PYTMUX`/`$LC_PYTMUX`)는 전파 의존이라 빈틈이 있다 — ssh 래퍼 우회
    (절대경로/alias 의 ssh), sshd `AcceptEnv` 에 LC_* 부재, 비-SendEnv 클라이언트.
    그 경로로 원격 pytmux 가 패널 안에서 실제로 떠 버리면 textual-in-pyte 중첩이
    crash-relaunch·net 워치독 재접속 루프("재접속 반복")로 나타난다. 이 프로브는
    전송과 무관하게 **단말 자체에게 물어** 중첩을 확정한다: 외부 pytmux 서버는 패널
    출력의 질의를 보고 `DCS >| pytmux ST` 로 응답한다(serverpty.NEST_REPLY).

    POSIX + 양쪽 tty 일 때만 동작(아니면 False). 응답이 pytmux 가 아닌 완결 DCS 면
    실제 터미널이므로 조기 False. cbreak 로 에코를 막고 원상복구한다.
    rfd/wfd 는 테스트 주입용(기본 stdin/stdout)."""
    if os.name == "nt":
        return False
    if rfd is None or wfd is None:
        try:
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                return False
        except (ValueError, OSError):
            return False
        rfd, wfd = sys.stdin.fileno(), sys.stdout.fileno()
    import select
    import termios
    import tty
    try:
        old = termios.tcgetattr(rfd)
    except termios.error:
        return False
    buf = b""
    try:
        tty.setcbreak(rfd, termios.TCSANOW)
        os.write(wfd, b"\x1b[>0q")
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            r, _, _ = select.select([rfd], [], [], left)
            if not r:
                return False
            try:
                chunk = os.read(rfd, 256)
            except OSError:
                return False
            if not chunk:
                return False
            buf += chunk
            if b"pytmux" in buf:
                return True
            if b"\x1b\\" in buf:    # 완결 DCS 인데 pytmux 아님 → 실제 터미널
                return False
    finally:
        try:
            termios.tcsetattr(rfd, termios.TCSADRAIN, old)
        except termios.error:
            pass


# 모호폭(East Asian Ambiguous) 자동감지 CPR 대기 상한. 단말 응답은 보통 즉답(<10ms)
# 이라 짧게 — 무응답(파이프·미지원 단말)이면 narrow 로 폴백한다.
AMBIG_PROBE_TIMEOUT = 0.3
# 자동감지 테스트 문자: EAW='A'(Ambiguous)·wcwidth=1 이라 좁은 단말은 1칸, CJK
# 로케일 단말은 2칸으로 그린다. 사용자의 깨진 출력에도 나타난 대표 문자(·).
_AMBIG_PROBE_CH = "·"


def _read_cpr(rfd, deadline) -> tuple[int, int] | None:
    """CPR 응답 ``ESC [ row ; col R`` 을 읽어 (row, col) 반환(타임아웃/형식오류=None)."""
    import re
    import select
    buf = b""
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        r, _, _ = select.select([rfd], [], [], left)
        if not r:
            return None
        try:
            chunk = os.read(rfd, 64)
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
        m = re.search(rb"\x1b\[(\d+);(\d+)R", buf)
        if m:
            return int(m.group(1)), int(m.group(2))


def detect_ambiguous_width(opt: str = "auto",
                           rfd: int | None = None,
                           wfd: int | None = None) -> str:
    """단말이 East Asian Ambiguous 문자를 몇 칸으로 그리는지 결정 → "wide"|"narrow".

    opt 가 "narrow"/"wide" 면 그대로(감지 생략). "auto" 면 단말에 질의: 현재 커서
    위치를 CPR 로 받고(c0), 모호폭 문자 1개를 출력한 뒤 다시 CPR(c1)로 전진 칸수를
    측정해 ≥2 면 wide. 측정 후 그 문자를 지운다(원위치로 가 EOL 삭제). POSIX+양쪽
    tty 가 아니거나 무응답/미지원이면 narrow(현행). rfd/wfd 는 테스트 주입용."""
    if opt in ("narrow", "wide"):
        return opt
    if os.name == "nt":
        return "narrow"     # Windows ConPTY 는 별도(현행 narrow 가 안전)
    if rfd is None or wfd is None:
        try:
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                return "narrow"
        except (ValueError, OSError):
            return "narrow"
        rfd, wfd = sys.stdin.fileno(), sys.stdout.fileno()
    import termios
    import tty
    try:
        old = termios.tcgetattr(rfd)
    except termios.error:
        return "narrow"
    try:
        tty.setcbreak(rfd, termios.TCSANOW)
        deadline = time.monotonic() + AMBIG_PROBE_TIMEOUT
        os.write(wfd, b"\x1b[6n")
        p0 = _read_cpr(rfd, deadline)
        if p0 is None:
            return "narrow"
        os.write(wfd, _AMBIG_PROBE_CH.encode("utf-8") + b"\x1b[6n")
        p1 = _read_cpr(rfd, deadline + AMBIG_PROBE_TIMEOUT)
        # 잔상 제거: 원래 커서 위치로 가 그 줄을 우측까지 지운다(왼쪽 프롬프트 보존).
        os.write(wfd, f"\x1b[{p0[0]};{p0[1]}H\x1b[K".encode("ascii"))
        if p1 is None or p1[0] != p0[0]:
            return "narrow"     # 무응답·줄바꿈(가장자리)면 안전하게 narrow
        return "wide" if (p1[1] - p0[1]) >= 2 else "narrow"
    finally:
        try:
            termios.tcsetattr(rfd, termios.TCSADRAIN, old)
        except termios.error:
            pass


# 승격 요청 ack 대기 상한(NESTED_ATTACH ㉤). 프로브(0.4s)보다 길게 — 바깥 서버의
# 스캔→ack 는 즉답이지만 경로에 ssh 왕복이 2회(REQ 나감·ACK 들어옴) 낀다.
NEST_ACK_TIMEOUT = 1.0


def request_nest_promotion(timeout: float = NEST_ACK_TIMEOUT,
                           rfd: int | None = None,
                           wfd: int | None = None) -> bool:
    """중첩 감지 후 거부 대신 **바깥 pytmux 에 승격을 요청**한다(NESTED_ATTACH §4).

    NEST_ATTACH_REQ(DCS, self-report=`user@hostname` b64)를 단말(=바깥 패널 스트림)
    에 쓰고 NEST_ACK 를 기다린다. ack = 바깥 서버가 접수(그 패널의 ssh 래퍼가
    기록한 **실제 ssh 목적지**로 remote_attach 시작 — self-report 는 2단 ssh 대조용
    일 뿐 attach 인자가 아니다, 시나리오 §7) → 호출부는 위임 안내 후 exit 0.
    무응답(구버전 바깥/기능 OFF/목적지 미기록/호스트 불일치/mosh DCS 미통과) =
    False → 호출부는 현행 거부 메시지로 폴백(열화 없음). 단말 처리(POSIX·tty·
    cbreak)는 host_terminal_is_pytmux 와 동일 패턴. rfd/wfd 는 테스트 주입용."""
    if os.name == "nt":
        return False
    if rfd is None or wfd is None:
        try:
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                return False
        except (ValueError, OSError):
            return False
        rfd, wfd = sys.stdin.fileno(), sys.stdout.fileno()
    import base64
    import select
    import socket
    import termios
    import tty
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    payload = base64.b64encode(f"{user}@{host}".encode("utf-8", "replace"))
    try:
        old = termios.tcgetattr(rfd)
    except termios.error:
        return False
    buf = b""
    try:
        tty.setcbreak(rfd, termios.TCSANOW)
        os.write(wfd, sshwrap.NEST_REQ_PRE + payload + sshwrap.DCS_ST)
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            r, _, _ = select.select([rfd], [], [], left)
            if not r:
                return False
            try:
                chunk = os.read(rfd, 256)
            except OSError:
                return False
            if not chunk:
                return False
            buf += chunk
            if b"pytmux-nest-ack" in buf:
                return True
    finally:
        try:
            termios.tcsetattr(rfd, termios.TCSADRAIN, old)
        except termios.error:
            pass


def _try_nest_promotion() -> bool:
    """원격 로그인의 중첩 거부 지점 공통 승격 시도 + 성공 안내 출력(NESTED_ATTACH).
    로컬 중첩($PYTMUX, 비 ssh)은 대상이 아니다 — 자기 자신 attach 는 무의미하고
    serverremote 도 자기 endpoint 를 거부한다."""
    if not (os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY")):
        return False
    if not request_nest_promotion():
        return False
    print("pytmux: 바깥 pytmux 가 이 호스트를 원격 탭(⇄)으로 어태치합니다 — "
          "결과는 바깥 상태줄 notice 로 표시됩니다.")
    return True


def run_stdio_proxy(sock_path: str) -> int:
    """원격 어태치 페더레이션의 원격 측 전송 프리미티브(§1.7 Stage 1·3).

    `ssh -T <host> pytmux stdio-proxy` 로 실행되면 ① 이 머신(원격)의 서버 인증
    토큰을 `TOKEN <hex>\\n` 한 줄로 알리고 ② 이후 stdin↔서버소켓↔stdout 을 그대로
    스플라이스한다. ssh exec 채널(-T, TTY 없음)은 8-bit clean 파이프라 와이어
    프로토콜의 길이-프레임이 무손상으로 통과한다 — 로컬 pytmux 서버는 이 파이프
    위에서 원격 서버에 hello(+토큰)로 attach 해 원격 탭/패널을 흡수한다.

    **POSIX·Windows 공통**(Stage 3, 제보 — office Windows 박스): asyncio
    add_reader(POSIX 전용) 대신 **블로킹 스레드 2개 + 동기 소켓**(ipc.control_socket
    — Unix=AF_UNIX, Windows=TCP 루프백+포트파일)으로 스플라이스한다. 새 프로세스라
    원격 서버 재시작 없이 코드 동기화만으로 동작.

    **원격 서버 자동 기동(reliability)**: 원격에 서버가 없으면(원격 재부팅 후·최초
    접속·서버 종료 뒤) 종전엔 즉시 1 로 실패했다 — 원격-attach 실패 신고의 가장 흔한
    원인이었다(사용자는 attach 전에 원격에 손수 서버를 띄워야 했다). 이제 tmux 의
    'attach 가 서버를 띄운다' 모델을 따라 **분리(detached) 서버를 자동 기동**하고
    인증까지 기다린 뒤 스플라이스한다. 비대화식 ssh exec 라도 spawn_detached 는
    setsid(Unix)/DETACHED_PROCESS(Windows)로 ssh 세션과 무관하게 살아남는다(메인
    attach 경로 need_spawn 분기와 동형). 끄려면 원격 셸 환경에
    `PYTMUX_NO_REMOTE_AUTOSTART=1`(그러면 종전대로 '실행 중인 서버 없음' 1)."""
    if not ipc.probe(sock_path):
        if os.environ.get("PYTMUX_NO_REMOTE_AUTOSTART"):
            print("pytmux: 실행 중인 서버 없음", file=sys.stderr)
            return 1
        try:
            spawn_server(sock_path)
        except Exception as e:                       # 기동 자체 실패(권한·실행파일 등)
            print(f"pytmux: 서버 자동 기동 실패: {e}", file=sys.stderr)
            return 1
        # connectability 가 아니라 auth 까지 기다린다 — 좀비 소켓 교체 창에서 probe 가
        # 옛 서버를 맞히는 레이스를 피한다(메인 attach need_spawn 분기와 동일 판정).
        # 예산은 로컬(4s)보다 넉넉한 _REMOTE_START_POLLS: 원격은 콜드 부팅(인터프리터
        # 시작+pyte/model import)이 로컬보다 느린데, 실패하면 원격 탭이 통째로 날아가고
        # 사용자는 다시 시도해야 한다. 로컬 측 핸드셰이크 타임아웃(15s) 안에 든다.
        if not wait_server_authed(sock_path, polls=_REMOTE_START_POLLS):
            # 종전엔 '인증 대기 시한 초과' 만 찍어 원인이 사라졌다 — 데몬 stderr 가
            # /dev/null 이었기 때문. 이제 boot.log 의 실제 사유를 실어 보낸다(이 stderr
            # 는 ssh 를 타고 로컬 pytmux 의 notice 로 그대로 뜬다).
            report_server_start_failure(
                sock_path, "pytmux: 서버 자동 기동 실패(인증 대기 시한 초과)")
            return 1
    import socket as _socket
    sock = ipc.control_socket(sock_path)
    if sock is None:
        print("pytmux: 서버 접속 실패", file=sys.stderr)
        return 1
    sock.settimeout(None)              # 스플라이스는 무기한 블로킹 read/recv
    out = sys.stdout.buffer            # 바이너리(Windows CRLF 변환 없음)
    # S1 신뢰 모델(docs/internal/CODE_AUDIT_2026-06-13): 여기서 내보내는 토큰은 서버 인증
    # 토큰이다. 이 stdout 은 sshd↔이 프로세스 사이의 사설 파이프이고 ssh 채널은
    # 암호화돼 있어 전송 중 노출은 없다. 같은-UID 프로세스만 stdio-proxy 를 띄우거나
    # 이 파이프를 관찰할 수 있는데, 그런 프로세스는 어차피 0600 토큰 파일을 직접
    # 읽을 수 있으므로 **추가 노출이 없다**(같은-UID 등가). 서버 측은 받은 연결에
    # F2 peer-UID(Unix)와 F1 상수시간 토큰 검증을 모두 적용한다(serverio.handle_client).
    # → 토큰을 ssh 채널로 넘기는 것은 페더레이션의 의도된 인증 방식이다.
    tok = ipc.read_token(sock_path) or ""
    out.write(f"TOKEN {tok}\n".encode())
    out.flush()

    import threading
    done = threading.Event()

    def _stdin_to_sock():
        try:
            while True:
                data = sys.stdin.buffer.read1(65536)
                if not data:           # ssh 끊김/로컬 측 종료
                    break
                sock.sendall(data)
        except (OSError, ValueError):
            pass
        finally:
            try:                       # 서버에 EOF 전달(half-close)
                sock.shutdown(_socket.SHUT_WR)
            except OSError:
                pass
            done.set()

    def _sock_to_stdout():
        try:
            while True:
                data = sock.recv(65536)
                if not data:           # 서버 종료
                    break
                out.write(data)
                out.flush()
        except (OSError, ValueError):
            pass
        finally:
            done.set()

    t_out = threading.Thread(target=_sock_to_stdout, daemon=True)
    t_in = threading.Thread(target=_stdin_to_sock, daemon=True)
    t_out.start()
    t_in.start()
    done.wait()
    # 한쪽이 끝나면 반대쪽 잔여(서버가 보내던 마지막 프레임)를 짧게 드레인한 뒤
    # 닫는다. 데몬 스레드라 남은 블로킹 read 는 프로세스 종료와 함께 정리된다.
    t_out.join(timeout=3)
    try:
        sock.close()
    except OSError:
        pass
    # 데몬 _sock_to_stdout 가 join 안에 못 끝났으면(서버가 소켓을 늦게 닫음) 아직
    # out.write/flush 중이라 stdout BufferedWriter 락을 쥔 채로 남는다. 여기서 그대로
    # return 하면 sys.exit→Py_FinalizeEx 가 데몬을 강제 종료하면서 그 락이 풀리지 않은
    # 채 stdout TextIOWrapper 를 닫으려다 `_enter_buffered_busy` fatal abort() →
    # macOS "Python quit unexpectedly" 팝업(원격-attach/stdio-proxy 자식에서 간헐).
    # 이 프로세스는 stdin↔소켓↔stdout 만 잇는 잎(leaf) 릴레이라 인터프리터 finalize 가
    # 할 일이 없다 → 우리 스트림만 flush 하고 os._exit 로 finalize 를 건너뛰어 레이스를
    # 원천 차단한다. (정상 종료(서버가 소켓 닫음)도 동일 경로라 항상 깔끔히 끝난다.)
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except (OSError, ValueError):
            pass
    os._exit(0)


def _relay_host_ok(host: str) -> bool:
    """릴레이 목적지로 쓸 수 있는 문자열인가 — `_remote_transport` 와 **같은 규칙**.

    선행 `-` 는 ssh 가 옵션으로 해석한다(`-oProxyCommand=…` → 임의 명령 실행). 공백은
    argv 한 칸을 쪼갠다. 규칙을 여기서 한 번 더 거는 이유: 이 프로세스는 **B 에서**
    도는 별개 프로세스라 A 의 검증을 믿을 수 없다 — A 가 침해됐거나 구버전일 수 있다.
    경계마다 자기 입력을 검증한다."""
    return bool(host) and not host.startswith("-") and not any(
        c.isspace() for c in host)


def _relay_allowed(sock_path: str, host: str) -> bool:
    """이 상자(B)의 `remote_allowed_hosts` 가 이 목적지를 허용하나.

    **중계자가 자기 egress 정책의 주인**이라는 것이 이 함수의 존재 이유다. 안 그러면
    A 가 B 를 임의 호스트로 가는 도약대로 쓸 수 있다 — B 에 로그인할 수 있다는 것과
    B 를 통해 아무 데나 갈 수 있다는 것은 다른 권한이다.

    서버와 **같은 파일**(`<state>.opts.json`)을 읽는다. 서버가 안 떠 있어도(릴레이는
    B 의 서버와 무관하다) 파일만 있으면 정책이 선다. 비어 있으면(기본) 종전대로 허용."""
    try:
        with open(ipc.state_base(sock_path) + ".opts.json", encoding="utf-8") as f:
            opts = json.load(f)
    except (OSError, ValueError):
        return True
    if not isinstance(opts, dict):
        return True
    allow = opts.get("remote_allowed_hosts", [])
    if isinstance(allow, str):
        allow = [allow]
    if not isinstance(allow, (list, tuple)) or not allow:
        return True
    return host in [str(h) for h in allow]


def run_relay_proxy(sock_path: str, host: str) -> int:
    """다중홉 페더레이션의 **중계 상자(B)** 전송 프리미티브(설계 §4).

    `ssh -T -- B pytmux relay-proxy C` 로 실행되면 B 에서 다시
    `ssh -T -- C pytmux stdio-proxy` 를 띄우고 자기 stdin↔그 자식↔자기 stdout 을
    스플라이스한다. A 입장에서 파이프 반대편은 여전히 "TOKEN 줄을 먼저 뱉는 무언가"라
    **와이어 프로토콜·서버 로직 변경이 0** 이다(파이프 반대편이 누구냐만 달라진다).
    TOKEN 은 **C 가** 낸 것이 그대로 통과하므로 `_is_self_ssh_token` 자기-attach
    가드도 그대로 유효하다.

    **왜 중첩 ssh 문자열(`ssh B "ssh C …"`)이 아닌가**: 그건 B 의 로그인 셸이 문자열을
    해석한다 — 지금 argv 형이라 존재하지 않던 셸 인젝션 표면이 생기고, 인용 규칙이 B 의
    셸/OS 마다 갈리며, B 가 자기 egress 정책을 강제할 자리가 사라진다. 여기서는 argv 를
    끝까지 고정한다.

    **홉 깊이는 1**이다 — relay-proxy 는 자기 목적지에 다시 relay-proxy 를 부르지
    않는다(재귀 루프와 진단 난이도 원천 차단).

    **에러 줄에는 `relay-proxy(<host>): ` 접두를 붙인다.** 세 자리(A→B ssh · 여기 ·
    B→C ssh)의 stderr 가 전부 한 파이프로 합류하는데 A 는 마지막 줄만 보여 주므로,
    접두가 없으면 "어느 홉이 실패했나"가 즉시 흐려진다."""
    tag = f"relay-proxy({host})"

    def _err(msg: str):
        print(f"{tag}: {msg}", file=sys.stderr)

    if not _relay_host_ok(host):
        _err(f"잘못된 목적지: {host!r}")
        return 1
    if not _relay_allowed(sock_path, host):
        _err("이 상자의 remote_allowed_hosts 가 허용하지 않는 목적지입니다")
        return 1
    import subprocess
    import threading
    try:
        proc = subprocess.Popen(
            ["ssh", "-T", "-o", "BatchMode=yes",
             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "--",
             host, "pytmux", "stdio-proxy"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
    except OSError as e:
        # B 에 ssh 가 없거나 실행 못 함 — 이 줄이 A 의 notice 에 그대로 뜬다.
        _err(f"ssh 를 실행하지 못했습니다: {e}")
        return 1

    out = sys.stdout.buffer
    done = threading.Event()

    def _pump(src, dst_write, close_after=None):
        try:
            while True:
                data = src.read1(65536) if hasattr(src, "read1") else src.read(65536)
                if not data:
                    break
                dst_write(data)
        except (OSError, ValueError):
            pass
        finally:
            if close_after is not None:
                try:
                    close_after()
                except OSError:
                    pass
            done.set()

    def _write_out(data):
        out.write(data)
        out.flush()

    def _write_child(data):
        proc.stdin.write(data)
        proc.stdin.flush()

    def _pump_err():
        # 안쪽 ssh 의 stderr 도 **같은 접두로 감싸** 흘린다 — 그래야 A 가 마지막 줄만
        # 봐도 "B→C 홉에서 났다"를 안다. 줄 단위라 부분 줄은 다음 것과 합쳐진다.
        try:
            for line in proc.stderr:
                text = line.decode("utf-8", "replace").rstrip("\r\n")
                if text:
                    _err(text)
        except (OSError, ValueError):
            pass

    t_out = threading.Thread(target=_pump, args=(proc.stdout, _write_out),
                             daemon=True)
    t_in = threading.Thread(
        target=_pump, args=(sys.stdin.buffer, _write_child, proc.stdin.close),
        daemon=True)
    t_err = threading.Thread(target=_pump_err, daemon=True)
    for t in (t_out, t_in, t_err):
        t.start()
    done.wait()
    t_out.join(timeout=3)
    try:
        proc.kill()
    except OSError:
        pass
    # stdio-proxy 와 같은 이유로 finalize 를 건너뛴다(데몬 스레드가 stdout 락을 쥔 채
    # 남으면 `_enter_buffered_busy` fatal abort → macOS 크래시 팝업).
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except (OSError, ValueError):
            pass
    os._exit(0)


def run_start_server(sock_path: str) -> int:
    """`pytmux start-server` — 서버 데몬만 분리 기동하고 attach 없이 돌아온다.

    tmux 의 `start-server` 대응. 쓰임새:
      * **원격 호스트 준비**: `ssh <host> pytmux start-server` — 로컬에서 원격 탭을
        열기 전에 원격 서버를 올려 두고, **실패하면 그 자리에서 이유를 본다**
        (원격 탭 실패는 stdio-proxy 자동 기동 뒤에야 드러나 진단이 늦었다).
      * 부팅 스크립트/서비스에서 서버만 미리 띄우기(클라이언트는 나중에 attach).
    이미 인증까지 정상인 서버가 있으면 아무것도 하지 않는다(멱등)."""
    if server_auth_ok(sock_path):
        print(f"pytmux: 서버가 이미 실행 중입니다: {sock_path}")
        return 0
    if ipc.probe(sock_path):
        # listen 은 하는데 인증이 안 되는 좀비(토큰 분실/불일치) — attach 경로와 같은
        # 판정으로 새 서버를 띄워 소켓 경로를 원자 교체한다.
        print("pytmux: 기존 서버가 인증을 거부합니다(좀비 서버로 추정) — "
              "새 서버로 교체합니다.", file=sys.stderr)
    try:
        spawn_server(sock_path)
    except Exception as e:                   # 실행파일/권한 등 spawn 자체 실패
        print(f"pytmux: 서버 기동 실패: {e}", file=sys.stderr)
        return 1
    if not wait_server_authed(sock_path):
        report_server_start_failure(sock_path, "pytmux: 서버 기동 실패")
        return 1
    print(f"pytmux: 서버 기동됨: {sock_path}")
    return 0


def _confirm_kill_server() -> bool:
    """CLI `kill-server` 확인 가드. 진행해도 되면 True.

    - 대화형(stdin·stderr 가 TTY): `[y/N]` 로 묻고 'y'/'yes' 만 승인(기본 거부).
    - 비대화형(파이프·리다이렉트·자동화): 물을 수 없으니 **거부**하고 `--yes`
      를 안내한다(`stdin.isatty()` False). 이게 핵심 — 패널 안 도구가 무심코
      돌린 `kill-server`(비대화형)가 호스트 세션을 죽이지 못하게 막는다.
    프롬프트는 stderr 로 내보내 stdout 파이프(`| ...`)를 오염시키지 않는다.
    """
    interactive = sys.stdin.isatty()
    if not interactive:
        print("pytmux: kill-server 는 서버와 모든 탭/셸을 종료합니다. 비대화형 "
              "호출에서는 거부합니다 — 확실하면 `--yes` 를 붙이세요.",
              file=sys.stderr)
        return False
    nested = " (이 pytmux 세션 안에서 실행 중!)" if nesting_blocked() else ""
    try:
        ans = input(f"서버와 모든 탭/셸을 종료합니다{nested}. 계속할까요? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans.strip().lower() in ("y", "yes")


#: 네이티브 클라(러스트 TUI) 이진 이름. 위치는 `find_native_client` 가 정한다.
NATIVE_CLIENT = "pytmux-client-tui"


def find_native_client(env=None) -> str | None:
    """네이티브 클라 이진의 경로. 못 찾으면 None.

    찾는 순서와 이유:
      1. `PYTMUX_CLIENT_BIN` — 직접 지목. 두 벌을 두고 견주는 경우(개발·회귀 확인)에
         PATH 를 안 흔들고 고를 수 있어야 한다.
      2. `PATH` — 설치된 상태의 정석. 이 이진은 pytmux 와 **따로 배포**된다(러스트
         워크스페이스 산출물이라 pip 패키지에 안 들어간다).
      3. 개발 트리(`../pytmux-client/target/{release,debug}/`) — 이 저장소 옆에 클라
         트리를 두고 작업하는 경우다. release 를 먼저 본다: 둘 다 있으면 사용자가
         최근에 만든 것은 대개 release 이고, debug 판은 눈에 띄게 느리다.

    찾기만 하고 **실행 가능한지는 안 따진다** — 여기서 조용히 걸러 내면 "이진이 없다"와
    "이진이 실행 권한이 없다"가 같은 메시지가 된다. 실행은 호출부가 하고 실패 사유는
    OS 가 알려 준다.
    """
    env = os.environ if env is None else env
    explicit = env.get("PYTMUX_CLIENT_BIN")
    if explicit:
        return explicit
    import shutil
    found = shutil.which(NATIVE_CLIENT, path=env.get("PATH"))
    if found:
        return found
    name = NATIVE_CLIENT + (".exe" if os.name == "nt" else "")
    tree = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "pytmux-client", "target")
    for profile in ("release", "debug"):
        cand = os.path.join(tree, profile, name)
        if os.path.exists(cand):
            return cand
    return None


def run_native_client(sock_path: str, env=None, runner=None) -> int:
    """네이티브 클라를 띄운다. 종료코드를 돌려준다.

    **엔드포인트를 넘겨준다.** 클라도 스스로 찾을 수 있지만(같은 규칙을 구현한다),
    여기서는 이미 `resolve_default_endpoint` 가 "어느 서버에 붙을지"를 정했고 필요하면
    새로 띄우기까지 했다. 클라가 다시 찾으면 그 사이에 다른 후보를 맞혀 **사용자가
    지목한 것과 다른 서버**의 탭이 뜰 수 있다.

    exec 이 아니라 자식 프로세스로 두는 이유: Windows 에서 `os.execv` 는 부모를 끝내
    셸이 곧바로 프롬프트를 그리는데, 자식은 아직 대체 화면에 그리고 있어 화면이 섞인다.
    """
    binary = find_native_client(env)
    if binary is None:
        print(f"pytmux: 네이티브 클라({NATIVE_CLIENT})를 찾지 못했습니다.\n"
              "        빌드: cargo build --release -p pytmux_client_tui\n"
              "        그다음 PATH 에 두거나 PYTMUX_CLIENT_BIN 으로 경로를 지정하세요.",
              file=sys.stderr)
        return 1
    argv = [binary, "--socket", sock_path]
    if runner is None:
        import subprocess
        runner = subprocess.call
    try:
        return runner(argv)
    except OSError as e:
        # 못 뜨는 사유는 OS 가 안다(권한 없음·아키텍처 불일치·깨진 심링크).
        print(f"pytmux: 네이티브 클라를 실행하지 못했습니다({binary}): {e}",
              file=sys.stderr)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pytmux", description="tmux 유사 터미널 멀티플렉서")
    parser.add_argument("--socket", default=None, help="유닉스 도메인 소켓 경로")
    # 네이티브 클라(러스트 TUI)로 붙는다. 최상위 옵션인 이유는 `pytmux` 와
    # `pytmux attach` 가 같은 동작이기 때문이다 — 한쪽에만 붙이면 손버릇에 따라 안 먹는다.
    parser.add_argument("--native", action="store_true",
                        help=f"네이티브 클라({NATIVE_CLIENT})로 attach "
                             "(PYTMUX_CLIENT_BIN 으로 경로 지정 가능)")
    sub = parser.add_subparsers(dest="command")
    p_attach = sub.add_parser("attach", help="실행 중인 서버에 attach (없으면 기동)")
    # SUPPRESS 가 필요하다: 기본값을 두면 `pytmux --native attach` 에서 하위 파서가
    # 자기 기본값(False)으로 **덮어써** 플래그가 조용히 안 먹는다(argparse 관례).
    p_attach.add_argument("--native", action="store_true",
                          default=argparse.SUPPRESS,
                          help=f"네이티브 클라({NATIVE_CLIENT})로 attach")
    sub.add_parser("ls", help="탭/패널 요약")
    p_kill = sub.add_parser("kill-server", help="서버와 모든 탭/셸 종료")
    p_kill.add_argument("-y", "--yes", action="store_true",
                        help="확인 없이 즉시 종료(스크립트/자동화용)")
    p_cmd = sub.add_parser("cmd", help="실행 중 서버에 명령 전송(외부 제어)")
    p_cmd.add_argument("words", nargs=argparse.REMAINDER,
                       help="예: cmd new-tab / cmd split-window -h / "
                            "cmd restart-all(서버+클라 전체 재시작)")
    # §1.7 원격 어태치 페더레이션 Stage 1 전송 프리미티브: `ssh -T <host> pytmux
    # stdio-proxy` 로 원격에서 실행되면 원격 서버 소켓 ↔ stdio 를 스플라이스한다.
    sub.add_parser("stdio-proxy",
                   help="(페더레이션) 로컬 서버 소켓 ↔ stdio 스플라이스")
    # 다중홉(A→B→C): 이 상자가 **중계자**가 된다. A 가 `ssh -T -- B pytmux
    # relay-proxy C` 로 부르고, 우리는 C 의 stdio-proxy 를 다시 띄워 스플라이스한다.
    p_relay = sub.add_parser(
        "relay-proxy",
        help="(페더레이션) 이 상자를 거쳐 <host> 의 stdio-proxy 로 중계")
    p_relay.add_argument("host", help="중계할 목적지(이 상자에서 ssh 가 되는 호스트)")
    # attach 없이 서버만 올린다(tmux start-server 대응). `server` 는 전경 데몬 본체라
    # 사람이 직접 쓰는 명령이 아니다 — 그 구분을 이름으로 드러낸다.
    sub.add_parser("start-server",
                   help="서버만 분리 기동(attach 하지 않음). 예: "
                        "ssh <host> pytmux start-server")
    p_srv = sub.add_parser("server", help="(내부) 서버를 전경 실행")
    p_srv.add_argument("--foreground", action="store_true")
    # 작업 보존 재시작(re-exec): 새 서버 이미지가 이 상태 파일로 상속된 PTY 를 채택.
    p_srv.add_argument("--resume", default=None,
                       help="(내부) 재시작 보존 상태 파일로 부트")
    p_rec = sub.add_parser("record", help="명령을 PTY 에서 실행하며 원시 출력 녹화")
    p_rec.add_argument("file", help="녹화 파일 경로")
    p_rec.add_argument("--cols", type=int, default=None)
    p_rec.add_argument("--rows", type=int, default=None)
    p_rec.add_argument("words", nargs=argparse.REMAINDER,
                       help="실행할 명령(생략 시 $SHELL). 예: record out.raw -- ls -C")
    p_rep = sub.add_parser("replay", help="녹화 파일을 재생해 텍스트 프레임 덤프")
    p_rep.add_argument("file", help="녹화 파일 경로")
    p_rep.add_argument("--cols", type=int, default=None)
    p_rep.add_argument("--rows", type=int, default=None)
    p_rep.add_argument("--ruler", action="store_true", help="열 번호 자 표시")
    args = parser.parse_args(argv)

    # 명시 --socket 이 없으면 이미 떠 있는 서버를 찾아 붙는다(ssh 로그인 등으로
    # XDG_RUNTIME_DIR 유무가 갈려 소켓 경로가 어긋나도 같은 서버에 attach 하도록).
    # 서버가 없으면 canonical 기본 경로로 떨어져 종전과 동일하게 새로 기동한다.
    sock_path = args.socket or ipc.resolve_default_endpoint()

    if args.command == "stdio-proxy":
        sys.exit(run_stdio_proxy(sock_path))
    if args.command == "relay-proxy":
        sys.exit(run_relay_proxy(sock_path, args.host))
    if args.command == "start-server":
        sys.exit(run_start_server(sock_path))
    if args.command == "server":
        # 레버 H(콜드 스타트 겹치기): host 인터프리터 startup(~400ms)을 아래 무거운
        # `from .server import run_server`(pyte/model ~140ms)+서버 부팅과 겹치도록, host 를
        # **먼저** detached 로 띄운다. host 모드 OFF·이미 떠 있는 host(재시작)면 no-op.
        # best-effort — 실패해도 serve()→ensure_connected 가 정상 경로로 띄운다.
        try:
            from . import ptyhostmgr
            ptyhostmgr.prespawn_host(sock_path)
        except Exception:
            pass
        from .server import run_server   # 지연 import: 서버 데몬 경로에서만 model/pyte 로드
        run_server(sock_path, resume_path=getattr(args, "resume", None))
        return
    if args.command in ("record", "replay"):
        from .replay import run_record, run_replay, term_size
        tc, tr = term_size()
        cols = args.cols or tc
        rows = args.rows or tr
        if args.command == "record":
            words = [w for w in args.words if w != "--"]
            sys.exit(run_record(args.file, cols, rows, words))
        sys.exit(run_replay(args.file, cols, rows, ruler=args.ruler))
    if args.command == "ls":
        reply = control_request(sock_path, {"t": "list"})
        if not reply:
            print("실행 중인 서버 없음")
            return
        for s in reply.get("sessions", []):
            print(f"{s['windows']} tabs, {s['panes']} panes")
        return
    if args.command == "kill-server":
        # kill-server 는 서버와 **모든** 탭/셸을 내린다. 패널 안에서 돌던 도구·
        # 벤치마크가 무심코 호출해 자기 호스트 세션을 통째로 죽인 사례가 있어
        # 확인 가드를 둔다(자살 방지). 대화형 TTY 면 [y/N] 로 묻고(기본 거부),
        # 비대화형(파이프·리다이렉트·자동화)이면 묻지 못하므로 **거부**하고
        # 명시 `--yes` 를 요구한다 — 사고를 낸 `kill-server 2>$null | Out-Null`
        # 같은 비대화형 호출이 바로 이 분기에서 막힌다.
        if not getattr(args, "yes", False) and not _confirm_kill_server():
            return
        reply = control_request(sock_path, {"t": "kill-server"})
        if reply:
            print("서버 종료됨")
            return
        # 서버가 없어도 **pty-host 는 남아 있을 수 있다**(서버 크래시·강제종료 등 —
        # PTYHOST_ORPHAN_2026-07-24). 그 host 는 자식 셸까지 붙들고 있는데 종전엔
        # 겨냥할 수단이 없어 사용자가 손으로 kill 해야 했다. 명시 종료 명령이므로
        # 패널이 있어도 내린다(어차피 소유 서버가 없는 고아 셸이다).
        killed = False
        try:
            from . import ptyhostmgr
            killed = ptyhostmgr.shutdown_host_sync(sock_path)
        except Exception:
            pass
        print("실행 중인 서버 없음 — 남아 있던 pty-host 를 정리했습니다"
              if killed else "실행 중인 서버 없음")
        return
    if args.command == "cmd":
        line = " ".join(args.words)
        reply = control_request(sock_path, {"t": "control", "line": line})
        if not reply:
            print("실행 중인 서버 없음")
        else:
            print(reply.get("result", "ok"))
        return

    # 기본 동작 = attach (필요 시 데몬 기동). 단일 세션 모델: 세션 이름 없음.
    # 중첩 실행 거부: pytmux 패널 안($PYTMUX 설정)에서 다시 attach 하면 막는다
    # (재귀 렌더·입력 꼬임 방지). `unset PYTMUX LC_PYTMUX` 로만 우회(강제 옵션 없음).
    if nesting_blocked():
        # NESTED_ATTACH: 원격 중첩이면 거부 대신 바깥 pytmux 에 자동 승격을 먼저
        # 요청한다(ack=위임 후 정상 종료). 무응답/로컬 중첩은 현행 거부 폴백.
        if _try_nest_promotion():
            sys.exit(0)
        print("pytmux: 이미 pytmux 안에서 실행 중입니다(로컬/원격 중첩). 원격 탭이 "
              "필요하면 로컬 pytmux 에서 ':remote-attach <이 호스트>' (§1.7 페더레이션). "
              "우회는 'unset PYTMUX LC_PYTMUX'.", file=sys.stderr)
        sys.exit(1)
    # §1.7 env 마커가 전파되지 않는 원격 경로(ssh 래퍼 우회·sshd AcceptEnv 부재) 대비
    # in-band 감지: 원격 로그인(SSH_*)에서만 단말에 XTVERSION 을 질의해, 호스트가
    # pytmux 로 응답하면 중첩으로 거부한다 — 중첩 TUI 가 떠서 crash-relaunch/재접속
    # 루프로 빠지는 것을 전송과 무관하게 차단(완화). 실제 터미널은 자기 이름으로
    # 응답해 조기 통과하므로 비중첩 원격 attach 의 지연은 RTT 수준.
    if (os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY")) \
            and host_terminal_is_pytmux():
        if _try_nest_promotion():        # NESTED_ATTACH: 프로브 확정 중첩도 승격 우선
            sys.exit(0)
        print("pytmux: 호스트 단말이 pytmux 입니다(원격 중첩 감지 — env 마커 없이 "
              "단말 질의로 확인). 이중 실행을 막습니다. 원격 탭이 필요하면 로컬 "
              "pytmux 에서 ':remote-attach <이 호스트>'.", file=sys.stderr)
        sys.exit(1)
    # 서버 기동(없으면)과 textual 로드를 **겹쳐서** 체감 기동을 줄인다: 서버를 먼저
    # 띄워두고(분리 프로세스), 그 부팅(수백 ms)이 도는 동안 무거운 client(=textual)
    # 를 import 한 뒤 readiness 를 폴링한다. 직렬(기동 완료 후 import)보다 빠르다.
    need_spawn = not ipc.probe(sock_path)
    if not need_spawn:
        # probe 는 connectability 만 본다. 토큰 파일이 사라졌거나 어긋난 좀비
        # 서버(예: 옛 서버가 default 소켓을 붙든 채 /tmp 의 토큰만 정리된 경우)도
        # listen 은 계속하므로 살아있어 보이지만, 그대로 attach 하면 클라가
        # handshake 에서 auth_failed 로 즉시 끊겨 **화면만 깜빡이고 프롬프트로**
        # 돌아온다. auth 까지 확인해 좀비면 새 서버로 교체한다(아래 spawn 의
        # os.replace 가 소켓 경로를 새 서버로 원자 교체 — 좀비는 고아 inode 에
        # 남지만 새 연결은 새 서버로 간다). auth_failed 만 좁게 본다: proto 불일치
        # 등 다른 error 는 정상 서버를 가로채지 않도록 그대로 attach 해 클라가 처리.
        reply = control_request(sock_path, {"t": "list"})
        if isinstance(reply, dict) and reply.get("error") == "auth_failed":
            print("pytmux: 기존 서버가 인증을 거부합니다(토큰 분실/불일치로 추정"
                  "되는 좀비 서버) — 새 서버로 교체합니다.", file=sys.stderr)
            need_spawn = True
    if need_spawn:
        spawn_server(sock_path)
    # 네이티브 클라는 textual 을 **아예 안 부른다**. 아래 지연 import 앞에 두는 이유가
    # 그것이다 — 러스트 이진으로 붙는 사람에게 textual 미설치가 실패 사유가 되면 안 된다.
    if getattr(args, "native", False):
        if need_spawn and not wait_server_authed(sock_path):
            report_server_start_failure(sock_path, "pytmux: 서버 기동 실패")
            sys.exit(1)
        sys.exit(run_native_client(sock_path))
    try:
        from .client import run_client   # 지연 import: 서버 부팅과 병렬로 textual 로드
    except ModuleNotFoundError as e:
        # client 는 textual/pyte/wcwidth 등 requirements.txt 의 서드파티에 의존한다.
        # 미설치면 raw traceback 대신 설치 방법을 안내한다(Windows 사용자가 자주 겪음).
        print(f"pytmux: 필수 의존성 '{e.name}' 이(가) 설치돼 있지 않습니다.\n"
              "        다음으로 의존성을 설치한 뒤 다시 실행하세요:\n"
              f"          {os.path.basename(sys.executable)} -m pip install -r requirements.txt",
              file=sys.stderr)
        sys.exit(1)
    # 새로 띄웠다면 connectability(probe)가 아니라 auth 까지 기다린다 — 좀비를
    # 교체하는 경우 경로가 새 서버로 바뀌기 전 probe 가 좀비를 맞힐 수 있어서다.
    if need_spawn and not wait_server_authed(sock_path):
        report_server_start_failure(sock_path, "pytmux: 서버 기동 실패")
        sys.exit(1)
    run_client(sock_path, None)


if __name__ == "__main__":
    main()
