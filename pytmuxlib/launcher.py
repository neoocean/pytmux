"""데몬화 · 런처 · 외부 제어 CLI.

OS 별 분기(데몬화/소켓)는 직접 알지 않고 추상층만 부른다(docs/internal/WINDOWS_PORT.md §7-c):
  * 서버 데몬 기동/존재확인 → pytmuxlib.proc (Unix setsid 분리 / Windows DETACHED).
  * 소켓 접속/제어/probe → pytmuxlib.ipc (Unix AF_UNIX / Windows TCP 루프백+포트파일).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import ipc, proc, protocol, sshwrap
# NOTE: client(=textual)·server(=model→pyte→wcwidth) 는 여기서 import 하지 않는다.
# 가벼운 제어 명령(ls/cmd/kill)이 launcher 만 거쳐도 textual 전체나 pyte/wcwidth 를
# 로드해 기동이 느려졌다(Windows 사용자 보고). attach 경로의 client, `server` 명령의
# run_server 모두 main() 안에서 필요 시점에만 지연 import 한다(A4).


def can_connect(sock_path: str) -> bool:
    return ipc.probe(sock_path)


# §5.9: wait_server 백오프 상수(종전 루프 본문 bare 리터럴 → 명명·튜닝 일원화). 초기
# 폴 간격→상한까지 지수 증가. 총 예산 polls*interval(≈4s)은 종전과 동일.
_WAIT_POLL_INITIAL = 0.002   # 첫 폴 간격(서버가 <20ms 에 뜨면 체감 지연 최소화)
_WAIT_POLL_BACKOFF = 1.6     # 폴 간격 지수 증가율(interval 상한까지)


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


def ensure_server(sock_path: str):
    if ipc.probe(sock_path):
        return
    # 부모 생애와 무관하게 살아남는 분리 서버 프로세스를 띄운다(Unix setsid /
    # Windows DETACHED_PROCESS). 그 뒤 listen 이 떠 접속 가능해질 때까지 대기.
    proc.spawn_detached(proc.server_argv(sock_path))
    if not wait_server(sock_path):
        print("pytmux: 서버 기동 실패", file=sys.stderr)
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

    **POSIX·Windows 공통**(Stage 3, 사용자 보고 — office Windows 박스): asyncio
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
            proc.spawn_detached(proc.server_argv(sock_path))
        except Exception as e:                       # 기동 자체 실패(권한·실행파일 등)
            print(f"pytmux: 서버 자동 기동 실패: {e}", file=sys.stderr)
            return 1
        # connectability 가 아니라 auth 까지 기다린다 — 좀비 소켓 교체 창에서 probe 가
        # 옛 서버를 맞히는 레이스를 피한다(메인 attach need_spawn 분기와 동일 판정).
        if not wait_server_authed(sock_path):
            print("pytmux: 서버 자동 기동 실패(인증 대기 시한 초과)", file=sys.stderr)
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pytmux", description="tmux 유사 터미널 멀티플렉서")
    parser.add_argument("--socket", default=None, help="유닉스 도메인 소켓 경로")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("attach", help="실행 중인 서버에 attach (없으면 기동)")
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
        print("서버 종료됨" if reply else "실행 중인 서버 없음")
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
        proc.spawn_detached(proc.server_argv(sock_path))
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
        print("pytmux: 서버 기동 실패", file=sys.stderr)
        sys.exit(1)
    run_client(sock_path, None)


if __name__ == "__main__":
    main()
