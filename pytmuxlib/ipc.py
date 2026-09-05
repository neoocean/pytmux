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
import re
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
    "DEFAULT_ENDPOINT_NAME", "endpoint_name", "tcp_endpoint",
]


# ─────────────────────────────────────────────────────────────────────────────
# 엔드포인트 표현 / 기본값
# ─────────────────────────────────────────────────────────────────────────────
# TCP 엔드포인트의 **상태파일 이름**(prefix). Unix 는 소켓 경로가 곧 prefix 라 서버마다
# 저절로 갈리는데, TCP 는 포트가 재기동마다 바뀌어 경로에 못 쓴다 — 그래서 종전에는 셋
# (state_base·portfile·token)을 전부 상태 디렉터리의 **고정 이름** `default` 로 접었다.
# 발견이 안정적이어야 하니 기본값으로는 옳지만, 그 결과 **한 머신에서 서버가 둘 뜨면
# 두 서버가 같은 파일 이름을 쓴다**: 나중 것이 `default.token` 을 가져가고, 먼저 뜬
# 서버에 붙는 클라가 `read_token()` 으로 남의 토큰을 읽어 내밀어 `auth_failed` 로 끊긴다
# (실측 2026-08-08 alienware, pytmux/pytmux-152 — Windows 스위트에서 «서버 둘 + 클라
# attach» 시나리오가 통째로 못 서던 원인).
#
# 그래서 이름을 **엔드포인트 문자열이 직접 나른다**: "tcp:[NAME@]HOST:PORT".
# NAME 을 안 쓰면 `default` 라 종전 경로가 **바이트 그대로** 유지된다(발견 규약 무변경).
DEFAULT_ENDPOINT_NAME = "default"
# 이름은 그대로 **파일명**이 되므로 문자 집합을 좁게 못박는다. `remote_attach` 의
# endpoint 는 클라가 준 비신뢰 문자열이라, 여기가 느슨하면 `..%s..` 로 상태 디렉터리
# 밖에 토큰/포트파일을 읽고 쓰게 된다. 앞 글자는 영숫자, 나머지는 `[A-Za-z0-9._-]`,
# 64자 이내 — 경로 구분자·`..`·NUL 이 애초에 못 들어온다.
_ENDPOINT_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def _split_tcp(endpoint: str) -> Tuple[str, str]:
    """"tcp:[NAME@]HOST:PORT" → (name, "HOST:PORT"). 이름이 없으면 `default`.

    `@` 는 호스트 부분에 쓰이지 않으므로(userinfo 문법을 안 쓴다) 첫 `@` 하나로
    가른다. 이름이 규칙에 안 맞으면 ValueError — 조용히 `default` 로 접지 않는다
    (접으면 오타 하나가 남의 서버 토큰을 읽는 경로가 된다)."""
    rest = endpoint[len("tcp:"):]
    name, sep, addr = rest.partition("@")
    if not sep:
        return DEFAULT_ENDPOINT_NAME, rest
    if not _ENDPOINT_NAME_RE.match(name):
        raise ValueError(f"잘못된 엔드포인트 이름: {name!r} ({endpoint!r})")
    return name, addr


def tcp_endpoint(name: str = DEFAULT_ENDPOINT_NAME, host: str = "127.0.0.1",
                 port: int = 0) -> str:
    """이름 있는 TCP 엔드포인트 문자열을 짓는다(기본 이름이면 종전 형태 그대로)."""
    if name == DEFAULT_ENDPOINT_NAME:
        return f"tcp:{host}:{port}"
    if not _ENDPOINT_NAME_RE.match(name):
        raise ValueError(f"잘못된 엔드포인트 이름: {name!r}")
    return f"tcp:{name}@{host}:{port}"


def endpoint_name(endpoint: str) -> str:
    """TCP 엔드포인트의 상태파일 이름. unix 소켓이면 경로 자체가 이름이라 해당 없음."""
    if not is_tcp(endpoint):
        raise ValueError(f"tcp 엔드포인트가 아님: {endpoint!r}")
    return _split_tcp(endpoint)[0]


def parse_endpoint(endpoint: str) -> Tuple:
    """엔드포인트 문자열 → ("unix", path) 또는 ("tcp", host, port:int).

    이름(`NAME@`)은 상태파일 경로에만 쓰이고 **전송에는 안 실린다** — 그래서 반환
    모양은 종전 그대로다(호출부 무변경)."""
    if endpoint.startswith("tcp:"):
        _name, rest = _split_tcp(endpoint)
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


def validate_local_socket(path: str) -> None:
    """유닉스 소켓 **파일**이 우리 것인지 연결 **전에** 확인한다(fail-closed).

    검수 2026-07-17 PTYH-2(rogue host 토큰 수확)의 POSIX 몫: 종전엔 경로가 누구 것인지
    보지 않고 connect 한 뒤 **인증 토큰을 실어 보냈다**. 다른 사용자가 그 경로를 선점하면
    토큰이 그대로 수확된다(같은 uid 프로세스는 토큰 파일 자체를 읽을 수 있어 애초에
    권한 상승이 아니다 — 실질 위협은 **크로스 유저**다). `_validate_state_dir` 가 상태
    디렉터리에 하는 것과 같은 규율의 **소켓 버전**이다.

    검사: ①심볼릭 링크 아님(lstat — 링크를 따라가지 않는다) ②소켓 파일임
    ③현재 uid 소유 ④group/other 권한 비트 없음. Windows(TCP 루프백)에는 등가 검사가
    없다 — 아무 로컬 프로세스나 에페메럴 포트를 선점할 수 있고 그건 **상호인증**으로만
    닫힌다(검수 문서 §10: hello 순서 호환 제약 때문에 유보 유지). 파일이 없으면
    통과시킨다 — 그건 이 함수의 관심사가 아니라 connect 가 낼 오류다."""
    if IS_WINDOWS:
        return
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if _stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"소켓 경로가 심볼릭 링크임(보안상 거부): {path}")
    if not _stat.S_ISSOCK(st.st_mode):
        raise RuntimeError(f"소켓이 아닌 파일임(보안상 거부): {path}")
    if st.st_uid != os.getuid():
        raise RuntimeError(f"소켓 소유자가 현재 사용자가 아님(보안상 거부): {path}")
    if _stat.S_IMODE(st.st_mode) & 0o077:
        raise RuntimeError(
            f"소켓이 다른 사용자에게 열려 있음(보안상 거부): {path}")


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


def _tcp_state_prefix(endpoint: str) -> str:
    """TCP 엔드포인트의 상태파일 프리픽스 — `<state_dir>/<name>`.

    포트는 재기동마다 바뀌므로 경로에 안 쓰고, **이름**(기본 `default`)만 쓴다.
    그래서 `tcp:127.0.0.1:0`(기동 전)과 `tcp:127.0.0.1:54321`(확정 후)이 같은
    자리를 가리키고, 이름이 다른 두 서버는 서로를 안 밟는다.

    ⛔ 이름을 **먼저** 검증한다 — `default_state_dir()` 은 디렉터리를 만들고 권한까지
    조이는 부작용이 있어, 거부할 문자열에 그 값을 치를 이유가 없다."""
    name = _split_tcp(endpoint)[0]
    return os.path.join(default_state_dir(), name)


def state_base(endpoint: str) -> str:
    """상태파일(slots/opts/capture/layout) 경로의 프리픽스.

    Unix 소켓이면 소켓 경로 자체(고정 소켓→안정, 임시 소켓→테스트 격리). TCP
    엔드포인트("tcp:[name@]host:port")면 콜론 등 파일명 불가 문자를 피하고 포트가
    바뀌어도 안정적이도록 상태 디렉터리(default_state_dir)의 **이름별** prefix 를
    쓴다(docs/internal/WINDOWS_PORT.md §7-c-4 · pytmux/pytmux-152)."""
    if is_tcp(endpoint):
        return _tcp_state_prefix(endpoint)
    return endpoint


def portfile_for(endpoint: str) -> str:
    """TCP 엔드포인트의 실제 포트를 게시/조회하는 파일 경로.

    Unix 소켓 경로면 `<path>.port`, "tcp:..." 면 상태 디렉터리의 `<name>.port`.
    """
    if is_tcp(endpoint):
        return _tcp_state_prefix(endpoint) + ".port"
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
    """인증 토큰 파일 경로. Unix=소켓경로+".token", TCP=`<state_dir>/<name>.token`."""
    if is_tcp(endpoint):
        return _tcp_state_prefix(endpoint) + ".token"
    return endpoint + ".token"


def server_pidfile(endpoint: str) -> str:
    """서버가 게시하는 **자기 pid** 파일(`pytmux/pytmux-435`).

    ⛔ 종전에는 이 파일이 **없었다**. 그래서 새 서버가 이 엔드포인트를 가져갈 때
    (unix=`os.replace` 로 소켓 이름을 · TCP=포트파일을 덮어써서) 앞 주인에게
    「너는 이제 아니다」를 알릴 **주소가 없었다** — 앞엣것은 경로 없는 소켓을 쥔 채
    프로브·liveness·프레임 루프를 계속 돌았다. 실측(2026-09-02 · playground)으로 한
    엔드포인트에 서버 **넷**이 36일·17일·5.8일·13분 나이로 살아 있었고, 그중 하나는
    RSS 172MB·누적 CPU 622분이었다. 그림자 `/usage` 프로브도 여러 벌 돌아 토큰 DB 에
    오프셋 다른 600초 계열이 겹쳐 찍혔다.

    ⇒ pid 를 **파일 하나로 게시**해 두면 다음 주인이 앞 주인을 겨냥할 수 있다. 이름은
    pty-host 가 같은 이유로 이미 쓰고 있는 규약(`.ptyhost.pid`)의 형제다
    (`ptyhostmgr.host_pidfile` 머리말 — 「떠 있나」를 파일 존재가 아니라 **pid 생존**
    으로 판정하려는 자리).
    """
    return state_base(endpoint) + ".server.pid"


def read_server_pid(endpoint: str) -> Optional[int]:
    """[`server_pidfile`] 이 가리키는 pid(없거나 쓰레기면 None)."""
    try:
        with open(server_pidfile(endpoint), encoding="ascii") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


_win_acl_hardened: set = set()
_win_grantee_cache: list = []   # [str] 한 번만 계산해 재사용(캐시 미스=빈 리스트)
_ACL_MARKER_SUFFIX = ".ok"


def _acl_marker_dir() -> Optional[str]:
    r"""ACL 하드닝 표식 저장소(`%LOCALAPPDATA%\pytmux\acl`). 못 만들면 None.

    표식을 **대상 디렉터리 안**에 두면 안 된다: 느슨한 ACL 위치를 PYTMUX_HOME 으로
    쓰는 환경에서 타 로컬 사용자가 표식을 **선점 생성**해 하드닝을 영구히 건너뛰게
    만들 수 있다(L7 무력화). `%LOCALAPPDATA%` 는 OS 기본 ACL 이 소유자(+SYSTEM·
    Administrators)뿐이라 그 창이 없다 — Administrators 는 어차피 무엇이든 읽는다."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    d = os.path.join(base, "pytmux", "acl")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return None
    return d


def _acl_marker_path(path: str) -> Optional[str]:
    """대상 경로에 대응하는 표식 파일 경로(저장소를 못 쓰면 None).

    경로를 그대로 파일명에 쓸 수 없어(구분자·길이·대소문자) 정규화 후 해시로 키잉한다.
    Windows 파일명은 대소문자 무시라 casefold 로 같은 디렉터리가 두 키가 되지 않게 한다."""
    d = _acl_marker_dir()
    if d is None:
        return None
    import hashlib
    key = hashlib.sha256(
        os.path.abspath(path).casefold().encode("utf-8")).hexdigest()[:32]
    return os.path.join(d, key + _ACL_MARKER_SUFFIX)


def _acl_hardened_earlier(path: str, is_dir: bool) -> bool:
    """이 경로를 **이전 실행에서 이미** 조였는지(표식 존재).

    icacls 는 서브프로세스라 이 박스 실측 **1.1초/회**(EDR 이 프로세스 생성을 후킹).
    `_win_acl_hardened` 는 프로세스 내 메모이즈일 뿐이라, 매 `pytmux` 실행(클라·서버·
    pty-host 각각)이 상태 디렉터리 2개에 대해 이 비용을 다시 냈다 — 실측 **3.0초**가
    콜드 스타트 임계경로에 그대로 얹혀 런처의 서버 대기 예산(4초)을 넘겼다
    (2026-07-31 조사: 첫 `pytmux` 가 항상 '서버 기동 실패'). 디렉터리 ACL 은 한 번
    조이면 유지되므로 표식으로 실행 간 캐시한다.

    파일(토큰)은 부모 디렉터리가 `(OI)(CI)F` 로 조여져 있으면 **상속으로** 소유자
    전용이 되므로 부모의 표식을 본다(write_token 의 `_harden_win_acl` 도 생략 가능)."""
    target = path if is_dir else os.path.dirname(os.path.abspath(path))
    m = _acl_marker_path(target)
    if m is None:
        return False
    try:
        return os.path.exists(m)
    except OSError:
        return False


def _write_acl_marker(path: str) -> None:
    """하드닝 성공을 표식으로 남긴다(best-effort — 실패하면 다음 실행이 다시 조인다)."""
    m = _acl_marker_path(path)
    if m is None:
        return
    try:
        with open(m, "w", encoding="ascii") as f:
            f.write("1")
    except OSError:
        pass


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
    디렉토리를 (OI)(CI)F 로 조이면 그 안에 만들어지는 토큰 파일도 상속으로 소유자 전용이 된다.

    **실행 간**으로도 1회만 실행한다(2026-07-31): 프로세스 내 메모이즈만으로는 매
    `pytmux` 실행이 icacls 를 다시 스폰해(이 박스 실측 1.1초/회 × 상태 디렉터리 2개 =
    3.0초) 콜드 스타트가 런처 대기 예산을 넘겼다. 하드닝 성공을 표식으로 남기고
    (`_write_acl_marker`), 표식이 있으면 스폰을 생략한다(`_acl_hardened_earlier`)."""
    if not IS_WINDOWS or path in _win_acl_hardened:
        return
    _win_acl_hardened.add(path)   # 실패해도 재시도 안 함(스팸 방지) — best-effort
    if _acl_hardened_earlier(path, is_dir):
        return                    # 이전 실행이 이미 조였다 — icacls 스폰(1.1초) 생략
    try:
        import subprocess
        user = _win_current_user_grantee()
        if not user:
            return   # 식별자를 못 구하면 상속 ACL 을 그대로 둔다(무회귀 우선).
        grant = f"{user}:(OI)(CI)F" if is_dir else f"{user}:F"
        # 출력은 **파이프로 받지 않는다**(DEVNULL). 우리가 보는 건 returncode 뿐인데,
        # `capture_output=True` 는 파이프 + 리더 스레드 둘을 만들고 `run(timeout=)` 이
        # 그 스레드 join 을 거친다 — 2026-07-31 전체 스위트가 **이 자리에서** watchdog 에
        # 죽어 절단됐다(스택: run → communicate → _wait_for_tstate_lock). 파이프가 없으면
        # 리더 스레드도 없어 그 창이 원천 소멸하고, Windows 핸들 churn 도 줄어든다
        # (레드팀 fd 표본 잡음의 원천 하나 — 검수 문서 2026-07-31 §5).
        r = subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", grant],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # 표식은 **성공했을 때만**(rc 0) 남긴다 — 실패를 캐시하면 영구히 안 조인다.
        # 디렉터리에만 남긴다: 파일 표식은 매번 새로 만들어지는 토큰 파일에 무의미하고,
        # 파일의 소유자 전용성은 부모 디렉터리 ACE 상속이 보장한다(위 주석).
        if is_dir and getattr(r, "returncode", 1) == 0:
            _write_acl_marker(path)
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
    """게시된 토큰을 읽는다(클라/launcher). 없거나 못 읽으면 None.

    ValueError 도 삼킨다 — 엔드포인트 이름이 규칙에 안 맞으면 그건 "토큰이 없다"와
    같은 결말이어야 한다(비신뢰 endpoint 문자열이 `remote_attach` 로 들어오는 자리라,
    여기서 예외가 새면 서버 루프가 통째로 죽는다)."""
    try:
        with open(token_path(endpoint), "r", encoding="ascii") as f:
            return f.read().strip() or None
    except (OSError, ValueError):
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
        # 이름을 **확정 엔드포인트에도 실어 준다** — 안 실으면 클라가 받은 문자열로
        # 계산한 token/portfile 이 `default` 로 되돌아가 원래 결함이 그대로 재현된다.
        resolved = tcp_endpoint(_split_tcp(endpoint)[0], host, actual)
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
    """엔드포인트에 비동기 연결. (reader, writer) 반환.

    루프백 TCP 는 `_LOOPBACK_CONNECT_TIMEOUT` 로 캡한다 — **동기 control_socket 에만
    있던 캡을 비동기 경로에도 맞춘다**(2026-07-28). Windows 는 리스너 없는 루프백
    포트 connect 가 즉시 거절되지 않고 매달리므로(아래 상수 주석), 서버가 강제
    종료된 뒤 클라의 재접속 재시도 루프(clientconn `_connect_and_hello`)가 시도마다
    통째로 멎었다: `_RECONNECT_RETRIES_DROP`(25회)가 "~0.5초"라는 설계 의도와 달리
    **~50초**를 태우고 그제서야 종료했다(실측 3회 52·52·51초 — 사용자에겐 "한참
    멈춰 있다가 아무 말 없이 사라지는" 증상). 산 서버라면 커널 backlog 가 handshake
    를 <1ms 에 끝내므로 캡이 오탐을 만들지 않는다.
    타임아웃은 `TimeoutError`(= OSError 하위)로 나가 기존 `except OSError` 재시도
    핸들러가 그대로 흡수한다."""
    kind = parse_endpoint(endpoint)
    if kind[0] == "tcp":
        _, host, port = kind
        rport = _resolve_tcp_port(host, port, portfile, endpoint)
        if rport is None:
            raise ConnectionError(f"포트파일에서 포트를 못 읽음: {endpoint}")
        cap = _async_connect_timeout(endpoint)
        if cap is None:
            return await asyncio.open_connection(host, rport)
        return await asyncio.wait_for(
            asyncio.open_connection(host, rport), cap)
    _guard_local_socket(kind[1])
    return await asyncio.open_unix_connection(path=kind[1])


def _guard_local_socket(path: str) -> None:
    """`validate_local_socket` 을 **연결 경로에서** 부른다(F3 후속, 검수 2026-08-05).

    종전에는 이 검사를 `ptyhostclient.connect` 하나만 불렀다. 클라↔서버 경로
    (`clientconn._connect_and_hello` → `open_connection`, `launcher` → `control_socket`)
    에는 아무 검사가 없었고, `resolve_default_endpoint` 는 `default_endpoint_candidates`
    가 **문자열로 지은** 후보를 `probe` 로 찔러 살아 있으면 그대로 돌려준다 —
    `default_state_dir` 안에 사는 `_validate_state_dir`(F3)를 한 번도 지나지 않는다.
    그래서 `/tmp/pytmux-<uid>` 를 선점한 다른 사용자의 소켓에 그대로 붙었다(실측
    2026-08-05: `default_endpoint()` 는 심링크를 거부하는데 `resolve_default_endpoint()`
    는 같은 자리를 돌려주고 hello 프레임이 그 리스너에 도착했다). 소유자가 아닌 자의
    소켓에 붙는다는 것은 **키 입력 전량이 그쪽으로 간다**는 뜻이다.

    예외는 `ConnectionError`(= OSError 하위)로 바꿔 던진다 — 재접속 루프
    (`except OSError`)와 `control_socket` 이 이미 그 모양을 다룬다. `RuntimeError`
    그대로면 클라 리더 워커 밖으로 새어 앱이 통째로 죽는다."""
    try:
        validate_local_socket(path)
    except RuntimeError as e:
        raise ConnectionError(str(e)) from e


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
# 환경변수 override 이름. 위 "오탐이 없다"는 **호출자 이벤트 루프가 안 멎는다**를 전제
# 한다 — 캡은 `asyncio.wait_for`/`settimeout` 의 벽시계이므로, 호출자가 그 사이 동기
# 작업으로 멎으면 커널이 handshake 를 이미 끝냈어도 캡이 발동한다(2026-07-31 실측:
# 서버·클라가 **같은 루프**를 쓰는 테스트에서 세션 생성(ConPTY 스폰)·상태디렉터리
# 조이기(icacls)가 0.4~0.9초 루프를 멎게 해 살아 있는 서버 connect 가 0.608s → 캡
# 0.5s 에 걸려 거짓 TimeoutError → test_server 연결 테스트가 이유 없이 hang 으로
# 보였다). 프로덕션은 클라·서버가 별 프로세스이고 재시도 루프가 흡수하므로 기본값을
# 유지하고, **한 프로세스에 둘을 넣는 러너**나 느린 박스에서만 이 변수로 넉넉히 준다.
_LOOPBACK_CAP_ENV = "PYTMUX_LOOPBACK_CONNECT_TIMEOUT"


def _loopback_cap() -> float:
    """유효 루프백 connect 캡(초). env override 가 있으면 그 값(>0), 없으면 기본."""
    try:
        v = float(os.environ.get(_LOOPBACK_CAP_ENV, ""))
    except ValueError:
        return _LOOPBACK_CONNECT_TIMEOUT
    return v if v > 0 else _LOOPBACK_CONNECT_TIMEOUT


def _control_connect_timeout(endpoint: str, timeout: float) -> float:
    """control_socket 의 connect 타임아웃 결정(위 상수 주석 참조). 루프백 TCP 만
    캡하고, 원격 TCP·unix·호출자가 더 짧게 준 값은 그대로 둔다."""
    if is_tcp(endpoint) and is_local_endpoint(endpoint):
        return min(timeout, _loopback_cap())
    return timeout


def _async_connect_timeout(endpoint: str) -> Optional[float]:
    """open_connection 의 connect 캡(초) — 루프백 TCP 만, 그 외는 None(무제한).
    원격(비루프백) TCP 는 ssh 터널/광역 지연이 정상이라 캡하지 않는다."""
    if is_tcp(endpoint) and is_local_endpoint(endpoint):
        return _loopback_cap()
    return None


def control_socket(endpoint: str, *, portfile: Optional[str] = None,
                   timeout: float = 2.0,
                   io_timeout: Optional[float] = None) -> Optional[socket.socket]:
    """동기 제어 요청용(launcher) 연결된 소켓. 실패 시 None.

    Unix=AF_UNIX, TCP=AF_INET. 호출자가 sendall/recv 후 close 한다.

    `timeout` 은 **connect** 의 시한이고, `io_timeout` 은 붙은 뒤의 send/recv 시한이다.
    기본값 `None` 은 종전 그대로 «영원히 기다린다» — 대화형 CLI 는 답이 늦어도 기다리는
    편이 맞다. 시한이 필요한 자리는 **서버가 서버를 부르는** 곳이다(검수 2026-09-05 S-1:
    `serverpersist._evict_previous_owner` 는 bind 앞에 서 있어, 앞 서버가 accept 만 하고
    답을 안 하면 새 서버가 listen 도 못 선 채 영원히 대기했다).
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
        try:                       # 남의 소켓에는 안 붙는다(_guard_local_socket 참조)
            _guard_local_socket(kind[1])
        except OSError:
            return None            # probe → False → 발견 경로가 그 후보를 버린다
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        target = kind[1]
    s.settimeout(timeout)
    try:
        s.connect(target)
        s.settimeout(io_timeout)      # None = 종전대로 블로킹
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
