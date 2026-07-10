"""크로스플랫폼 IPC(클라이언트↔서버 소켓) 추상층 (docs/internal/WINDOWS_PORT.md §6-1 ④).

서버/클라이언트가 OS 별 소켓 분기를 직접 알지 않고 이 모듈만 부르도록 갇히는 곳.

  * **Unix**: 지금처럼 `AF_UNIX`(파일시스템 경로) 소켓. `asyncio.start_unix_server`
    /`open_unix_connection`.
  * **Windows**: AF_UNIX 의 asyncio 지원이 불완전 → **TCP 루프백(127.0.0.1)** 으로
    대체. 서버가 임의(에페메럴) 포트에 바인드한 뒤 실제 포트를 **포트파일**에 적고,
    클라이언트는 그 파일을 읽어 접속한다(`start_server`/`open_connection`).

엔드포인트는 **문자열 한 개**로 표현한다(기존 `sock_path: str` 스레딩을 그대로
유지하기 위함):

  * Unix    : 파일시스템 경로            예) /run/user/1000/pytmux/default.sock
  * TCP     : "tcp:HOST:PORT"            예) tcp:127.0.0.1:54321
              PORT 0 = "에페메럴 바인드 후 포트파일에 게시" (서버 기동 시 확정)

`parse_endpoint()` 로 어느 전송인지 판별하고, 나머지 함수가 그에 맞춰 분기한다.
프레이밍(길이프리픽스+JSON)은 `protocol.read_msg`/`write_msg` 가 전송 무관하게
담당하므로 여기선 연결만 책임진다.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import stat as _stat
import struct
from typing import Awaitable, Callable, Optional, Tuple


IS_WINDOWS = os.name == "nt"

__all__ = [
    "IS_WINDOWS", "parse_endpoint", "is_tcp",
    "default_state_dir", "default_endpoint", "default_endpoint_candidates",
    "resolve_default_endpoint", "portfile_for", "state_base",
    "token_path", "write_token", "read_token", "peer_uid", "open_private",
    "start_server", "open_connection", "probe", "control_socket",
]


# ─────────────────────────────────────────────────────────────────────────────
# 엔드포인트 표현 / 기본값
# ─────────────────────────────────────────────────────────────────────────────
def parse_endpoint(endpoint: str) -> Tuple:
    """엔드포인트 문자열 → ("unix", path) 또는 ("tcp", host, port:int)."""
    if endpoint.startswith("tcp:"):
        rest = endpoint[len("tcp:"):]
        host, _, port = rest.rpartition(":")
        if not host:
            host, port = "127.0.0.1", rest
        # S3: 잘못된 포트("tcp:", "tcp:host:abc")가 미처리 ValueError 로 호출부
        # (start_server/open_connection/control_socket)를 크래시시키지 않게 가드.
        try:
            return ("tcp", host, int(port))
        except (ValueError, TypeError):
            raise ValueError(f"잘못된 tcp 엔드포인트: {endpoint!r}")
    return ("unix", endpoint)


def is_tcp(endpoint: str) -> bool:
    return endpoint.startswith("tcp:")


def is_local_endpoint(endpoint: str) -> bool:
    """엔드포인트가 같은 머신(로컬)인가. AF_UNIX 소켓은 항상 로컬, TCP 는
    루프백 호스트(127.0.0.0/8·::1·localhost)면 로컬, 그 외 호스트면 원격(진짜
    네트워크)으로 본다. 클라↔서버 응답성(degraded) 표시가 로컬에선 의미 없음을
    판정하는 데 쓴다 — 로컬 RTT 스파이크는 이벤트루프/스케줄링 지터일 뿐이라
    네트워크 열화가 아니다(§10-F Windows degraded 오탐)."""
    if not is_tcp(endpoint):
        return True
    try:
        _, host, _ = parse_endpoint(endpoint)
    except ValueError:
        return False
    host = host.strip().lower()
    return (host in ("localhost", "", "::1", "::ffff:127.0.0.1")
            or host.startswith("127."))


def _validate_state_dir(path: str) -> None:
    """상태 디렉터리가 안전한지 검증한다(F3, docs/internal/SECURITY_REVIEW.md).

    `XDG_RUNTIME_DIR` 가 없는 ssh 로그인은 `/tmp/pytmux-<uid>` 로 폴백하는데, 부모가
    공유(/tmp)라 공격자가 이 경로를 **먼저 자기 소유로 생성**해 두면 피해자가 그 안에
    소켓/토큰을 만들거나 가짜 소켓에 붙어 키입력이 가로채진다. `lstat` 으로 **심볼릭
    링크가 아니고 현재 UID 소유**인지 확인해 어긋나면 거부한다(fail-closed). lstat 은
    링크를 따라가지 않으므로 공격자가 만든 심링크·디렉터리 둘 다 소유자 불일치로 잡힌다.
    """
    if IS_WINDOWS:
        return
    st = os.lstat(path)
    if _stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"상태 디렉터리가 심볼릭 링크임(보안상 거부): {path}")
    if st.st_uid != os.getuid():
        raise RuntimeError(
            f"상태 디렉터리 소유자가 현재 사용자가 아님(보안상 거부): {path}")


def pytmux_home() -> Optional[str]:
    """§10-E #1 단일 디렉토리 통합(opt-in): `PYTMUX_HOME` 이 설정돼 있으면 그 한
    디렉토리 아래에 **클라 설정(config) + 서버 상태(소켓·opts·usagedb·captures 등)**
    를 모두 둔다. 미설정이면 None → 종전 거동(흩어진 위치) 그대로(무변경·무마이그레이션).

    클라·서버가 같은 env 를 읽으므로 소켓 발견 경로가 일치한다(어긋나 새 서버가 뜨는
    일 없음). '워킹디렉토리 하위'로 두려면 `PYTMUX_HOME=./.pytmux` 처럼 상대경로를 쓰면
    되고, 여기서 abspath 로 고정해 cwd 가 달라도 같은 절대경로를 가리키게 한다."""
    h = os.environ.get("PYTMUX_HOME")
    if not h:
        return None
    return os.path.abspath(os.path.expanduser(h))


def default_state_dir() -> str:
    """런타임 상태(소켓/포트파일/슬롯·옵션 캐시)의 기본 디렉터리.

    `PYTMUX_HOME` 이 설정되면 **`<home>/state`**(§10-E #1 통합 — 런타임 일체: 소켓·opts·
    resume·slots·token·port·layout). 클라 설정(`<home>/config`)·토큰 DB(`<home>/db`)·
    captures(`<home>/captures`)는 형제 디렉터리로 분리해 역할별로 갈리게 한다(.gitignore/
    .p4ignore 를 깔끔히: 런타임/데이터/캡처는 제외, config 만 추적 가능). 아니면 —
    Unix: $XDG_RUNTIME_DIR 또는 /tmp/pytmux-<uid>. Windows: %LOCALAPPDATA%\\pytmux.
    디렉터리를 만들고, Unix 는 소유권·심링크를 검증(F3)한 뒤 0o700 으로 좁힌다.
    """
    home = pytmux_home()
    if home:
        # 런타임은 <home>/state 하위로. home 자체와 state 둘 다 만들고(POSIX) 좁힌다.
        state = os.path.join(home, "state")
        os.makedirs(state, exist_ok=True)
        if not IS_WINDOWS:
            _validate_state_dir(state)
            for d in (home, state):
                try:
                    os.chmod(d, 0o700)
                except OSError:
                    pass
        else:   # L7: PYTMUX_HOME 재배치 위치에서도 소유자 전용 ACL 강제
            for d in (home, state):
                _harden_win_acl(d, is_dir=True)
        return state
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        runtime = os.path.join(base, "pytmux")
        os.makedirs(runtime, exist_ok=True)
        _harden_win_acl(runtime, is_dir=True)   # L7: 상속 ACL 의존 제거
        return runtime
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/pytmux-{os.getuid()}"
    os.makedirs(runtime, exist_ok=True)
    _validate_state_dir(runtime)   # 공격자 선점 디렉터리 거부(검증 후 권한 좁힘)
    try:
        os.chmod(runtime, 0o700)
    except OSError:
        pass
    return runtime


def default_endpoint() -> str:
    """OS 기본 엔드포인트. Unix=소켓 경로, Windows=에페메럴 TCP(포트파일 게시)."""
    if IS_WINDOWS:
        return "tcp:127.0.0.1:0"
    return os.path.join(default_state_dir(), "default.sock")


def default_endpoint_candidates() -> list:
    """이미 떠 있는 서버를 찾기 위한 기본 엔드포인트 후보(우선순위 순, 중복 제거).

    Unix 에서 `XDG_RUNTIME_DIR` 유무가 세션마다 갈리는 게 문제다(예: 데스크톱/
    systemd 로그인은 `/run/user/<uid>`, 단순 ssh 로그인은 미설정이라 `/tmp/pytmux-
    <uid>` 폴백). 서버를 띄운 세션과 새로 attach 하는 세션의 경로가 어긋나면 같은
    서버를 못 찾아 새 서버가 떠버린다. 두 위치를 모두 후보로 둬 어느 쪽에 떠 있든
    붙게 한다. Windows 는 LOCALAPPDATA 가 안정적이라 단일 후보."""
    if IS_WINDOWS:
        return [default_endpoint()]
    # §10-E #1: PYTMUX_HOME 통합 시엔 그 소켓 하나가 canonical(XDG/tmp 이중 후보 불요).
    if pytmux_home():
        return [default_endpoint()]
    # POSIX 소켓 경로라 구분자는 항상 '/'(이 분기는 Unix 전용).
    cands = []
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        cands.append(f"{xdg.rstrip('/')}/pytmux/default.sock")
    cands.append(f"/tmp/pytmux-{os.getuid()}/default.sock")
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def resolve_default_endpoint() -> str:
    """기본 엔드포인트를 정한다(명시 --socket 이 없을 때).

    이미 서버가 떠 있는 후보가 있으면 그 엔드포인트(= attach 대상)를 돌려주고,
    없으면 canonical `default_endpoint()`(= 새로 기동할 위치)를 돌려준다. 서버가
    없을 때 동작은 종전과 동일하고, ssh 등으로 경로가 어긋난 채 서버가 떠 있을 때만
    그 서버를 찾아 붙는다(요청)."""
    for cand in default_endpoint_candidates():
        if probe(cand):
            return cand
    return default_endpoint()


def state_base(endpoint: str) -> str:
    """상태파일(slots/opts/capture/layout) 경로의 프리픽스.

    Unix 소켓이면 소켓 경로 자체(고정 소켓→안정, 임시 소켓→테스트 격리). TCP
    엔드포인트("tcp:host:port")면 콜론 등 파일명 불가 문자를 피하고 포트가 바뀌어도
    안정적이도록 상태 디렉터리(default_state_dir)의 고정 prefix 를 쓴다
    (docs/internal/WINDOWS_PORT.md §7-c-4)."""
    if is_tcp(endpoint):
        return os.path.join(default_state_dir(), "default")
    return endpoint


def portfile_for(endpoint: str) -> str:
    """TCP 엔드포인트의 실제 포트를 게시/조회하는 파일 경로.

    Unix 소켓 경로면 `<path>.port`, "tcp:..." 면 상태 디렉터리의 고정 파일.
    """
    if is_tcp(endpoint):
        return os.path.join(default_state_dir(), "default.port")
    return endpoint + ".port"


def _read_portfile(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _write_portfile(path: str, port: int) -> None:
    # tmp 는 **pid 고유 경로**여야 한다. 좀비/경쟁 상황(같은 default 엔드포인트로 두
    # 서버가 거의 동시에 기동)에서 공유 `<path>.tmp` 를 쓰면, 한 서버가 그 tmp 를 연
    # 채로 다른 서버가 같은 이름에 open/os.replace 하다 Windows 에서 WinError 5(Access
    # denied — 공유 위반)로 **기동 직후 크래시**한다(→ 빈 화면 멈춤). 같은 파일의 unix
    # 소켓 경로가 같은 이유로 이미 pid 접미사를 쓴다(start_server 참조). 실패 시 tmp 누수
    # 방지를 위해 정리한다.
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(port))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# 연결 인증 토큰(F1) — 같은 UID 만 읽을 수 있는 0600 파일로 공유 비밀을 게시한다.
# Unix 소켓은 0700 디렉터리·0600 소켓으로 이미 같은 UID 만 접근 가능하지만, Windows
# 는 127.0.0.1 TCP 루프백이라 같은 머신의 **다른 로컬 사용자도 접속 가능**하다. 토큰을
# 읽을 수 있는 건 파일을 0600 으로 둔 같은 UID 뿐이므로, hello/control 첫 메시지에 토큰을
# 실어 서버가 검증하면 무인가 로컬 주체의 접속을 차단한다(docs/internal/SECURITY_REVIEW.md F1).
# ─────────────────────────────────────────────────────────────────────────────
def token_path(endpoint: str) -> str:
    """인증 토큰 파일 경로. Unix=소켓경로+".token", TCP=상태 디렉터리 고정 파일."""
    if is_tcp(endpoint):
        return os.path.join(default_state_dir(), "default.token")
    return endpoint + ".token"


_win_acl_hardened: set = set()
_win_grantee_cache: list = []   # [str] 한 번만 계산해 재사용(캐시 미스=빈 리스트)


def _win_current_user_grantee() -> str:
    """`icacls /grant` 에 넘길 **현재 프로세스 사용자**의 모호성 없는 식별자.

    바로 `getpass.getuser()`(=`USERNAME`, 도메인 없는 짧은 이름)를 쓰면 위험하다:
    **호스트명이 사용자명과 같으면**(예: 컴퓨터명 `WOOJINKIM`, 사용자 `NATGAMES\\woojinkim`)
    icacls 가 짧은 `woojinkim` 을 도메인 사용자가 아니라 로컬 컴퓨터 권한(`WOOJINKIM\\`)으로
    해석해, `/inheritance:r /grant:r woojinkim:F` 가 **실사용자를 자기 state 디렉터리에서
    잠가버린다**(default_state_dir 의 makedirs 가 WinError 183 로 죽음). 그래서 가능하면
    **SID**(`*S-1-5-…`, 오프라인·모호성 무관)로, 안 되면 `DOMAIN\\user`(도메인 한정)로,
    그것도 안 되면 짧은 이름으로 폴백한다. 결과는 한 번만 계산해 캐시한다."""
    if _win_grantee_cache:
        return _win_grantee_cache[0]
    grantee = None
    # 1순위: 현재 토큰의 SID → icacls 는 `*<SID>` 를 그대로 받는다(항상 명확).
    try:
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        TOKEN_QUERY = 0x0008
        TokenUser = 1
        advapi32.OpenProcessToken.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.ConvertSidToStringSidW.argtypes = (
            ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        tok = wintypes.HANDLE()
        if advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(tok)):
            try:
                size = wintypes.DWORD(0)
                advapi32.GetTokenInformation(
                    tok, TokenUser, None, 0, ctypes.byref(size))
                buf = ctypes.create_string_buffer(size.value)
                if advapi32.GetTokenInformation(
                        tok, TokenUser, buf, size, ctypes.byref(size)):
                    # TOKEN_USER 의 첫 필드 = SID_AND_ATTRIBUTES.Sid (PSID)
                    psid = ctypes.cast(
                        buf, ctypes.POINTER(ctypes.c_void_p)).contents
                    sid_str = wintypes.LPWSTR()
                    if advapi32.ConvertSidToStringSidW(
                            psid, ctypes.byref(sid_str)):
                        try:
                            if sid_str.value:
                                grantee = "*" + sid_str.value
                        finally:
                            kernel32.LocalFree(sid_str)
            finally:
                kernel32.CloseHandle(tok)
    except Exception:
        grantee = None
    # 2순위: DOMAIN\user (호스트명 충돌을 도메인 한정으로 제거).
    if not grantee:
        dom = os.environ.get("USERDOMAIN")
        usr = os.environ.get("USERNAME")
        if dom and usr:
            grantee = f"{dom}\\{usr}"
    # 3순위: 짧은 이름(마지막 수단).
    if not grantee:
        try:
            import getpass
            grantee = getpass.getuser()
        except Exception:
            grantee = os.environ.get("USERNAME") or ""
    _win_grantee_cache.append(grantee)
    return grantee


def _harden_win_acl(path: str, is_dir: bool = False) -> None:
    """Windows: 경로의 상속 ACL 을 끊고 현재 사용자 전용(F)으로 조인다(보안검수 2026-07-03
    L7). POSIX 0600/0700 에 대응하는 심층방어 — 토큰파일·상태디렉토리의 기밀성이
    %LOCALAPPDATA% 기본 ACL '상속'에만 의존하지 않게 하고, PYTMUX_HOME 을 느슨한 ACL
    위치로 옮겨도 타 로컬 사용자가 토큰을 읽어 인증을 통과하지 못하게 한다. POSIX 는 no-op.
    실패는 무시(기존 상속 ACL 유지 → 무회귀). headless 검증 불가(office/CI Windows 라이브).

    **경로별 1회만** 실행한다: `default_state_dir` 이 소켓/포트/토큰/state_base 경로 해석에서
    반복 호출되고(에러 로그 경로 포함) 여기에 icacls 스폰을 걸면 연결마다 서브프로세스 →
    Windows 핸들 churn 으로 red-team 배터리 fd 증가가 임계를 넘었다(2026-07-03 os-compat).
    디렉토리를 (OI)(CI)F 로 조이면 그 안에 만들어지는 토큰 파일도 상속으로 소유자 전용이 된다."""
    if not IS_WINDOWS or path in _win_acl_hardened:
        return
    _win_acl_hardened.add(path)   # 실패해도 재시도 안 함(스팸 방지) — best-effort
    try:
        import subprocess
        user = _win_current_user_grantee()
        if not user:
            return   # 식별자를 못 구하면 상속 ACL 을 그대로 둔다(무회귀 우선).
        grant = f"{user}:(OI)(CI)F" if is_dir else f"{user}:F"
        subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", grant],
            capture_output=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def write_token(endpoint: str, token: str) -> str:
    """토큰을 0600 으로 원자적 게시(서버). 게시한 경로를 반환한다.

    O_CREAT 시점부터 0600 으로 만들어 다른 사용자가 토큰을 읽을 창을 두지 않는다.
    """
    path = token_path(endpoint)
    # _write_portfile 과 동일 이유로 pid 고유 tmp — 동시 기동 서버 간 `<path>.tmp`
    # 충돌(WinError 5)을 막는다. 실패 시 tmp 를 정리한다.
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("ascii"))
    finally:
        os.close(fd)
    try:
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    try:    # 기존 파일이 넓은 권한으로 남아 있었을 가능성 대비(best-effort).
        os.chmod(path, 0o600)
    except OSError:
        pass
    _harden_win_acl(path)   # L7: Windows 는 모드비트 무시 → 명시적 owner-only ACL
    return path


def read_token(endpoint: str) -> Optional[str]:
    """게시된 토큰을 읽는다(클라/launcher). 없거나 못 읽으면 None."""
    try:
        with open(token_path(endpoint), "r", encoding="ascii") as f:
            return f.read().strip() or None
    except OSError:
        return None


def open_private(path: str, mode: str = "w", buffering: int = -1):
    """파일을 0600 으로 연다(민감 영속·캡처 파일, F4/F5 — docs/internal/SECURITY_REVIEW.md).

    `open()` 은 umask(흔히 0644)로 만들어 잠깐 다른 사용자가 읽을 수 있는 창이 생긴다.
    `os.open(..., 0o600)` 으로 **생성 시점부터** 소유자 전용으로 만든다. mode 는
    w/wb/a/ab 만 지원(쓰기 전용). Windows 는 모드 비트가 무시되지만 per-user 영역이라
    무해하다. 기존 호출부의 `with open(...) as f: ...` 를 그대로 대체할 수 있다.
    """
    append = "a" in mode
    binary = "b" in mode
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    try:
        if binary:
            return os.fdopen(fd, "ab" if append else "wb", buffering)
        return os.fdopen(fd, "a" if append else "w", buffering, encoding="utf-8")
    except Exception:
        os.close(fd)
        raise


@contextlib.contextmanager
def private_atomic(path: str, mode: str = "w"):
    """0600 + **원자적 교체**로 파일을 쓴다(M5). temp(`<path>.tmp`)에 쓰고 정상
    종료 시 os.replace 로 한 번에 바꾼다 — 쓰는 도중 프로세스가 죽어도(특히 재시작
    execv 직전 ~0.1s 창) 절반만 쓰인 파일이 원본을 덮지 않아, 다음 부트가 손상 파일을
    읽고 복원 실패(세션 전손)하던 것을 막는다. 예외 시 temp 를 지운다. open_private 와
    동형(쓰기 전용 w/wb)."""
    tmp = path + ".tmp"
    f = open_private(tmp, mode)
    try:
        yield f
        f.close()
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(Exception):
            f.close()
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def peer_uid(sock: Optional[socket.socket]) -> Optional[int]:
    """연결된 AF_UNIX 소켓 상대 프로세스의 UID(F2). 알 수 없으면 None.

    Linux=SO_PEERCRED(ucred), macOS/BSD=LOCAL_PEERCRED(xucred). TCP 소켓·미지원 OS·
    오류는 None 을 돌려 호출부가 "검증 불가 → 통과"(파일권한+토큰이 1차 방어)로 처리한다.
    같은 UID 만 0700 디렉터리/0600 토큰에 접근 가능하므로, 이 검증은 심층 방어다.
    """
    if sock is None:
        return None
    # peer-cred 는 AF_UNIX 에서만 의미가 있다. TCP(AF_INET/6) 소켓에 SO_PEERCRED 를
    # 걸면 Linux 는 OSError 가 아니라 미설정 ucred(uid=0)를 돌려줘, 비-root 러너에서
    # 유효 연결까지 오거부된다(루프백 TCP 경로 회귀). 가족이 UNIX 가 아니면 통과.
    if getattr(sock, "family", None) != getattr(socket, "AF_UNIX", object()):
        return None
    try:
        if hasattr(socket, "SO_PEERCRED"):          # Linux: struct ucred {pid,uid,gid}
            buf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                  struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", buf)
            return uid
        if hasattr(socket, "LOCAL_PEERCRED"):       # macOS/BSD: struct xucred
            buf = sock.getsockopt(0, socket.LOCAL_PEERCRED, 1024)
            if len(buf) >= 8:                       # u_int cr_version; uid_t cr_uid; ...
                _ver, uid = struct.unpack_from("=II", buf, 0)
                return uid
    except OSError:
        return None
    return None


ClientCb = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


# ─────────────────────────────────────────────────────────────────────────────
# 서버: listen
# ─────────────────────────────────────────────────────────────────────────────
async def start_server(endpoint: str, on_connected: ClientCb, *,
                       portfile: Optional[str] = None) -> Tuple[asyncio.AbstractServer, str]:
    """엔드포인트에서 listen 을 시작한다. (server, resolved_endpoint) 반환.

    TCP 에페메럴(PORT 0)이면 실제 포트로 바인드한 뒤 포트파일에 게시하고, 확정된
    "tcp:HOST:PORT" 를 resolved_endpoint 로 돌려준다(서버가 PYTMUX 환경에 심을 값).
    Unix 면 stale 소켓을 지우고 start_unix_server, 0o600 으로 좁힌다.
    """
    kind = parse_endpoint(endpoint)
    if kind[0] == "tcp":
        _, host, port = kind
        server = await asyncio.start_server(on_connected, host, port)
        actual = server.sockets[0].getsockname()[1] if port == 0 else port
        resolved = f"tcp:{host}:{actual}"
        pf = portfile or portfile_for(endpoint)
        _write_portfile(pf, actual)
        return server, resolved
    # unix — §5.9: stale 소켓 정리를 TOCTOU 없이 한다. 종전 `exists→unlink→bind` 는
    # 검사~바인드 사이에 다른 주체가 끼어들 창이 있었다(또 unlink 가 그 새 소켓을 지움).
    # 대신 **pid 고유 임시 경로에 bind 후 `os.replace` 로 원자 교체**한다: replace 는
    # 대상이 stale 소켓이든 없든 원자적으로 갈아끼우고, 우리 바인드 소켓이 그 이름으로
    # 도달 가능해진다. 바인드 자체는 임시 경로라 항상 성공(기존 stale path 와 무관) →
    # 재시작(execv 후 stale path)·신규·stale 정리 모든 흐름에서 거동 동일, 창만 제거.
    path = kind[1]
    tmp = f"{path}.{os.getpid()}.sock.tmp"
    if os.path.exists(tmp):    # 직전 크래시 잔재(우리 pid 네임스페이스) 정리
        os.unlink(tmp)
    server = await asyncio.start_unix_server(on_connected, path=tmp)
    try:    # 공개 이름으로 노출되기 전에 0600 으로 좁힌다(replace 가 모드 보존).
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    return server, path


# ─────────────────────────────────────────────────────────────────────────────
# 클라이언트: connect
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_tcp_port(host: str, port: int, portfile: Optional[str],
                      endpoint: str) -> Optional[int]:
    if port != 0:
        return port
    return _read_portfile(portfile or portfile_for(endpoint))


async def open_connection(endpoint: str, *, portfile: Optional[str] = None
                          ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """엔드포인트에 비동기 연결. (reader, writer) 반환."""
    kind = parse_endpoint(endpoint)
    if kind[0] == "tcp":
        _, host, port = kind
        rport = _resolve_tcp_port(host, port, portfile, endpoint)
        if rport is None:
            raise ConnectionError(f"포트파일에서 포트를 못 읽음: {endpoint}")
        return await asyncio.open_connection(host, rport)
    return await asyncio.open_unix_connection(path=kind[1])


# 루프백 TCP connect 타임아웃 캡. Windows 는 리스너 없는 루프백 포트로의 connect 가
# POSIX 처럼 즉시 ECONNREFUSED 로 끝나지 않는다 — 방화벽 stealth 가 SYN 을 조용히
# 드롭해 **클라이언트 타임아웃까지 통째로 매달린다**(GHA windows-latest 실측: 정확히
# settimeout 값만큼). 그래서 죽은 서버의 stale 포트파일이 남아 있으면 probe/제어 폴이
# 폴마다 기본 2s 를 태워, kill-server 후 첫 attach 가 wait_server_authed 의 4s 예산을
# 죽은-포트 connect 두 번으로 소진하고 "서버 기동 실패"로 오판했다(완전 재시작 후
# 한 번은 실패, 2026-07-10). 루프백은 산 서버라면 앱 상태와 무관하게 커널 backlog 가
# handshake 를 즉시(<ms) 끝내므로 짧게 잡아도 오탐이 없다. 원격(비루프백) TCP 와
# 호출자가 더 짧게 준 timeout 은 그대로 둔다.
_LOOPBACK_CONNECT_TIMEOUT = 0.5


def _control_connect_timeout(endpoint: str, timeout: float) -> float:
    """control_socket 의 connect 타임아웃 결정(위 상수 주석 참조). 루프백 TCP 만
    캡하고, 원격 TCP·unix·호출자가 더 짧게 준 값은 그대로 둔다."""
    if is_tcp(endpoint) and is_local_endpoint(endpoint):
        return min(timeout, _LOOPBACK_CONNECT_TIMEOUT)
    return timeout


def control_socket(endpoint: str, *, portfile: Optional[str] = None,
                   timeout: float = 2.0) -> Optional[socket.socket]:
    """동기 제어 요청용(launcher) 연결된 소켓. 실패 시 None.

    Unix=AF_UNIX, TCP=AF_INET. 호출자가 sendall/recv 후 close 한다.
    """
    kind = parse_endpoint(endpoint)
    if kind[0] == "tcp":
        _, host, port = kind
        rport = _resolve_tcp_port(host, port, portfile, endpoint)
        if rport is None:
            return None
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target: object = (host, rport)
        timeout = _control_connect_timeout(endpoint, timeout)
    else:
        if not os.path.exists(kind[1]):
            return None
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        target = kind[1]
    s.settimeout(timeout)
    try:
        s.connect(target)
        s.settimeout(None)
        return s
    except OSError:
        s.close()
        return None


def probe(endpoint: str, *, portfile: Optional[str] = None) -> bool:
    """서버가 떠 있어 접속 가능한지 동기 검사(launcher.can_connect 대체)."""
    s = control_socket(endpoint, portfile=portfile)
    if s is None:
        return False
    s.close()
    return True
