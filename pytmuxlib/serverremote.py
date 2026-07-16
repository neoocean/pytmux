"""원격 pytmux 어태치 페더레이션(§1.7 Stage 2) — 로컬 서버가 원격 서버의 **업스트림
클라이언트**가 되어 원격 세션의 탭/패널을 로컬 탭바에 '원격 탭'으로 흡수한다.

설계(docs/internal/REMOTE_ATTACH_SCENARIO.md §4): 와이어 프로토콜을 그대로 재사용한다 — 원격
서버는 변경 0(우리를 일반 클라로 본다). 전송은 ① 테스트/같은 머신: 엔드포인트 직결
② 실전: `ssh -T <host> pytmux stdio-proxy` 서브프로세스(8-bit clean exec 채널, 첫 줄
`TOKEN <hex>`).

핵심 단순화: 원격 패널을 로컬 모델(pyte)로 미러링하지 않는다. 클라이언트가 원격 탭을
보는 동안(`ClientConn.remote_view = host`) 그 클라에는 **업스트림 메시지(layout/screen/
screen-delta 등)를 그대로 전달**하고, 그 클라의 input/scroll/resize/일부 cmd 를
**업스트림으로 릴레이**한다. id 재작성이 없다 — 보는 동안 클라 화면 상태는 통째로
원격 것이고, 로컬 탭으로 돌아오면 로컬 _send_full 이 다시 그린다. 탭바는 로컬 status
의 windows 에 원격 탭을 병합(`⇄host:이름`)해 항상 양쪽이 보인다.

Stage 3(2026-06-12): status 를 **클라별**로 조립한다 — 보는 클라는 업스트림 status
누적본(`link.last_status`: active_pane·pane_title·Claude 헤더/토큰 등 부가필드 포함)에
병합 탭바(로컬=비활성, 원격=업스트림 active 보존 → **원격 탭 하이라이트**)를 얹어
받고, 안 보는 클라는 종전 로컬 status(원격 탭 비활성). 링크가 비명시적으로 죽으면
(_remote_reader EOF) **백오프 자동 재연결**(_RECONNECT_DELAYS, 명시 detach/재attach 가
취소), 재시작(re-exec)은 _resume_payload 의 remotes spec 으로 **링크 복원**
(remote_restore_links). 자기 자신 endpoint attach 는 거부(status 병합이 자기 ⇄ 탭을
무한 증식시키는 루프 차단).
"""
from __future__ import annotations

import asyncio
import hmac
import os
import time

from . import ipc, sshwrap
from .protocol import PROTO_VERSION, frame_msg, read_msg, write_frames, write_msg


class RemoteError(ConnectionError):
    """원격 attach 실패를 구조화해 나른다: key=i18n 키, ko=한국어 폴백, kw=포맷 인자.
    서버는 per-user 로케일을 모르므로(로케일은 클라-로컬) 실패 원인을 번역하지 않고
    키로 보존했다가 _set_err 가 detail dict 로 저장한다 — 클라가 자기 로케일로 합성.
    ConnectionError 를 상속해 기존 `except (OSError, ConnectionError, …)` 가 그대로 잡는다."""

    def __init__(self, key: str, ko: str, **kw):
        self.err_key, self.err_ko, self.err_kw = key, ko, kw
        super().__init__(ko.format(**kw) if kw else ko)


class RemoteLink:
    """업스트림(원격 서버) 연결 1개의 상태."""

    def __init__(self, host: str, reader, writer, proc=None):
        self.host = host          # 표시/식별용 이름(ssh 호스트 또는 endpoint)
        self.reader = reader
        self.writer = writer
        self.proc = proc          # ssh 서브프로세스(직결이면 None)
        self.windows: list = []   # 업스트림 status 의 windows(탭 목록) 최신본
        # §12 ①: 이 링크 원격 탭의 **다운스트림 로컬** 고정(핀) 집합(원격 로컬 index
        # ri). 핀은 보는 쪽 탭바 레이아웃 문제라 업스트림에 전파하지 않고 여기에만
        # 둔다 — _remote_tabs 가 매 status 에 pinned 비트를 실어 준다.
        self.pinned_windows: set = set()
        # 탭 하나만 이 뷰에서 분리(remote-detach 단일 탭)한 **다운스트림 로컬** 집합.
        # 링크(ssh)와 다른 탭은 그대로 두고 병합 탭바에서만 이 탭을 숨긴다. 상류가
        # 보내는 **안정 window id**(rw["wid"], 구버전 상류는 위치값 index 폴백 —
        # _win_key)로 키잉해, 상류 탭 close/reorder 로 index 가 재할당돼도 숨긴 탭이
        # 엉뚱한 탭으로 옮겨가지 않는다(코드검수 2026-07-10 M-1 — 종전 위치 index
        # 키잉의 드리프트 수정). 마지막 남은 탭까지 분리하면 호출부가 링크 전체를
        # 해제한다(remote_detach_tab).
        self.detached_windows: set = set()
        self.task: asyncio.Task | None = None
        # keepalive ping 태스크(_remote_ping_loop): 다운스트림이 업스트림에 주기적으로
        # ping 을 보내 업스트림의 死-클라 회수(_liveness_loop, ever_pinged 게이트)가
        # 이 federation 클라를 회수 대상으로 인지하게 한다. 안 그러면 다운스트림이
        # 비정상 종료(랩탑 슬립·반열림 TCP)해도 업스트림엔 좀비 클라가 남아 세션 공유
        # 크기(_session_size=min)를 그 죽은 단말의 작은 크기로 영구히 핀한다 —
        # "다른 기계에서 접속했던 작은 크기로 표시" 버그.
        self.ping_task: asyncio.Task | None = None
        self.alive = True
        # M-1: 이 링크 writer 로의 송신을 직렬화(입력/리사이즈 릴레이가 다중-await
        # 송신과 섞여 순서가 뒤집히지 않게). _link_write 가 이 락을 쓴다.
        self.write_lock = asyncio.Lock()
        # Stage 3: 재연결/재시작 복원용 원 spec({"host":…,"endpoint":…})과 소속 세션.
        self.spec: dict = {}
        self.sess = None
        # 업스트림 status 누적본(full 이 채운 옵션 키를 light 가 안 지우게 update 로
        # 합친다) — 보는 클라용 status 오버라이드(_remote_status_override)의 원천.
        self.last_status: dict = {}


# 원격 탭을 보는 동안 업스트림으로 릴레이하는 cmd action 화이트리스트.
# 입력/스크롤/리사이즈는 별도 경로(메시지 타입)로 릴레이한다. §1.7-c(섞임 금지):
# **탭 안에서 닫히는** 패널 조작(split/kill_pane/레이아웃/스왑 등)은 릴레이해
# 원격 탭엔 원격 패널만 생기게 한다 — 종전엔 비릴레이 액션이 **보이지 않는 로컬
# 트리**에 조용히 실행됐다(예: 원격 보기 중 split 이 로컬 탭을 분할). 탭 경계를
# 넘는 조작(break/join/move_pane_to_tab — 병합 전역 index 와 업스트림 로컬 index
# 공간 불일치)은 _REMOTE_BLOCK_ACTIONS 로 거부한다. 로컬 제어(restart/kill-server/
# remote_*)·kill_window(원격 탭 제거는 remote-detach 가 정문)는 여전히 제외.
_REMOTE_RELAY_ACTIONS = {
    "select_pane_id", "select_pane", "zoom", "next_window", "prev_window",
    "resize", "resize_dir",
    "split", "kill_pane", "cycle_pane", "last_pane", "rotate",
    "swap_pane", "swap_pane_to", "select_layout", "cycle_layout",
    "set_sync", "set_pane_title", "respawn_pane",
    # rename-tab/rename-pane(rename_window·set_pane_title): 원격 탭을 보는 중엔 그
    # **원격** 탭/패널 이름이 바뀌어야 한다(사용자 보고 2026-06-17). rename_window 가
    # 화이트리스트에 없어 보이지 않는 **로컬** 탭만 바꾸고 원격엔 안 먹던 버그(split 등
    # 비릴레이 액션이 로컬 트리에 조용히 실행되던 §1.7-c 와 동형). set_pane_title 은
    # 이미 위에 있다. 업스트림이 자기 active 탭/패널을 리네임하고 status/layout 으로
    # 되돌아와 병합 탭바·패널 테두리에 반영된다.
    "rename_window",
    # (Claude 토글 set_autoresume·set_prompt_clear 과 토큰 조회 request_token_log 는
    #  claude-code 플러그인 소유로 이전 — plugins.relay_actions() 로 기여한다. 코어는
    #  아래 _remote_relay_actions() 에서 이 집합과 합집합한다. delete-to-disable:
    #  플러그인 부재 시 그 액션들은 릴레이 목록에서 자동으로 빠진다.)
    # ncd(Norton Change Directory) 디렉토리 트리 요청 — 원격 탭을 보는 중엔 그 **원격**
    # 머신의 cwd/디렉토리가 나와야 한다(사용자 보고 2026-06-17). 안 그러면 로컬 서버가
    # 자기 fs 의 cwd 를 회신해 "원격 보는데 로컬 디렉토리 트리" 버그가 난다. 업스트림이
    # nc_list/nc_found 로 회신하고 그 메시지는 _remote_reader 가 보는 클라에 그대로
    # 전달한다(status·bye 외 메시지 패스스루) → ncd 모달이 원격 트리로 열린다. Enter
    # cd 는 입력 릴레이로, ⇧Enter 새 패널은 split 릴레이로 이미 원격에 적용된다.
    "request_nc_list", "request_nc_find",
    # 붙여넣기(paste=OS 클립보드/이미지 경로 텍스트·paste_buffer=페이스트 버퍼 N) —
    # 원격 탭을 보는 중엔 그 **원격** 활성 패널에 들어가야 한다(사용자 보고 2026-06-17).
    # 평문 타이핑·터미널 bracketed paste 는 input 메시지라 이미 릴레이되는데, 붙여넣기
    # 명령은 cmd 액션이라 화이트리스트에 없어 보이지 않는 로컬 패널에 주입되던 버그
    # (rename_window·split 누락과 동형 §1.7-c). 업스트림이 자기 활성 패널에 paste_text/
    # paste_buffer 로 주입한다. 대용량 텍스트는 input 릴레이와 같은 부담(프레임 한도 §5.1 내).
    "paste", "paste_buffer",
    # 화면 전체 강제 재그리기(redraw/refresh, §2.12) — 원격 탭을 보는 중엔 그 **원격**
    # 화면이 재그려져야 한다. 업스트림이 자기 패널들에 SIGWINCH 를 유발(_induce_redraw_all)
    # 하고 _send_full 로 전체 프레임을 federation 연결로 보내, _remote_reader 가 보는
    # 클라에 layout/screen 을 전달한다. 안 그러면 로컬 서버가 보이지 않는 로컬 화면만
    # 재그려 원격 잔상이 안 지워진다(§1.7-c 동형).
    "request_redraw",
}

# §1.7-c 원격 탭을 보는 동안 거부하는 경계 횡단 조작(notice 회신). 로컬 트리에
# 조용히 실행되지도, 업스트림으로 릴레이되지도 않는다 — 원격 탭은 원격 패널만,
# 로컬 탭은 로컬 패널만(섞기 금지). 예외: join_pane 이 **같은 호스트의 두 원격
# 탭**을 가리키면 거부 대신 remote_relay_join 이 index 변환해 릴레이한다(원격
# 탭끼리 드래그 머지) — serverio 의 블록 분기 참조.
_REMOTE_BLOCK_ACTIONS = {
    "break_pane", "join_pane", "move_pane_to_tab", "kill_window",
    "move_tab", "move_window", "swap_window", "move_current_tab",
}


def _decode_remote_stderr(b: bytes) -> str:
    """원격 ssh/명령 stderr 바이트를 사람이 읽을 문자열로 디코드.

    원격이 Windows(office Windows 박스)면 콘솔이 cp949(한국어) 등 비-UTF-8
    코드페이지로 한국어 메시지("실행 중인 서버 없음")를 내보내, UTF-8 로만
    디코드하면 `����` 가 된다 → UTF-8 strict 우선, 실패 시 cp949 폴백,
    그래도 안 되면 UTF-8 replace 로 마지막 보루."""
    for enc, errs in (("utf-8", "strict"), ("cp949", "strict")):
        try:
            return b.decode(enc, errs)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


# 페더레이션 신뢰경계: 업스트림 원격 서버는 **untrusted**(악성/침해 가능)다. 로컬 IPC
# (serverio.handle_client)는 첫 프레임에 isinstance(dict)+shape 가드가 있으나, 원격 리더는
# 프레임을 그대로 다운스트림 클라에 재브로드캐스트한다 — 아래 두 헬퍼로 경계에서 검증한다.
def _relay_frame_ok(t, msg: dict) -> bool:
    """업스트림 프레임을 다운스트림 클라에 재브로드캐스트하기 전, 클라가 **무가드로**
    소비하는 필수 키를 검증한다(M1). 누락 시 클라 _dispatch 가 KeyError→reader 워커
    종료(exit_on_error)→앱 크래시였다. 알 수 없는 t 는 통과(클라는 get 기반 처리)."""
    if t in ("screen", "screen-delta"):
        return "pane" in msg and isinstance(msg.get("rows"), list)
    return True


def _strip_ctrl(s: str) -> str:
    """원격 유래 표시 문자열(핸드셰이크 실패 stderr 등)에서 C0/C1 제어문자를 제거한다
    (L3). 상태줄 스푸핑·커서 이동(\\r)·표시 손상 방지. 개행/탭은 애초에 splitlines/
    strip 으로 처리되므로 여기선 전 제어문자를 공백으로 접는다."""
    return "".join(" " if (ord(c) < 0x20 or 0x7f <= ord(c) <= 0x9f) else c
                   for c in s)


async def _kill_proc(proc) -> None:
    """ssh 서브프로세스를 확실히 종료·reap 한다(안정성 H4). 안 하면 핸드셰이크/타임아웃
    실패 경로에서 <defunct> 좀비 + stdin/stdout/stderr 3 fd 가 누수돼 재연결 스톰과
    결합한다. 이미 끝난 프로세스도 wait 로 회수한다(child watcher fd 정리)."""
    if proc is None:
        return
    try:
        if proc.returncode is None:
            proc.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), 2)
    except asyncio.TimeoutError:
        pass


def _nest_host_part(s: str) -> str:
    """`user@host` → 호스트부(마지막 @ 뒤) 소문자. 대조 전용(접속 인자 아님)."""
    return s.rsplit("@", 1)[-1].strip().lower()


def _nest_host_match(dest: str, selfreport: str) -> bool:
    """2단 ssh 오어태치 가드(NESTED_ATTACH §7 ㉣ — 보수적 시작): 래퍼가 기록한
    목적지와 원격의 self-report(user@hostname)를 호스트부 소문자 정규화 후 **접두
    일치**로 대조한다(별칭 `office1` vs 실호스트명 `OFFICE1.local` 허용). 불일치는
    "그 패널의 ssh 1단 목적지 ≠ pytmux 를 친 머신"일 수 있다는 신호 — ack 하지 않아
    자동화만 포기한다(원격은 현행 거부 폴백, 오어태치 위험 0)."""
    h1, h2 = _nest_host_part(dest), _nest_host_part(selfreport)
    return bool(h1) and bool(h2) and (h1.startswith(h2) or h2.startswith(h1))


# NEST 자동 승격에서 endpoint **직결**(ssh 미경유)을 허용할 로컬 목적지 판정.
_NEST_LOOPBACK = frozenset(("127.0.0.1", "::1", "[::1]", "localhost"))


def _nest_local_endpoint(dest: str) -> bool:
    """NEST 자동 승격 dest 가 같은 머신 직결 endpoint 인가(unix 소켓 경로, 또는
    loopback tcp). 임의 원격 `tcp:host:port` 는 False — 이런 dest 의 endpoint 직결은
    금지하고(NEW-1: 위조/조작된 _ssh_dest 의 tcp:원격호스트 직결로 임의 아웃바운드 +
    키 MITM 차단) ssh 호스트 경로(S2 가드)로만 해석한다."""
    if dest.startswith("/"):
        return True
    if dest.startswith("tcp:"):
        host = dest[4:].rsplit(":", 1)[0].strip().lower()
        return host in _NEST_LOOPBACK
    return False


class ServerRemoteMixin:
    # 끊김(비명시) 시 자동 재연결 백오프(초). 무상한이 아니다 — §1 의 "재접속 루프"
    # 재발을 막기 위해 유한 회수 후 포기(notice)하고 수동 재시도에 맡긴다.
    _RECONNECT_DELAYS = (1, 2, 4, 8, 16, 30, 30, 30)

    # 첫 업스트림 status(탭 병합) 대기 한도 — hello 송신만으로 성공을 단정하지 않고
    # 실제 탭 도착을 기다린다(_remote_wait_first_status). 30×0.1=3초. 업스트림이
    # hello 는 받고도 _send_full 이 멈춰 status 를 안 보내는 웨지(원격 pty-host 고장
    # 등)를 조용한 실패 대신 '연결됐지만 무응답' 신호로 바꾼다. 테스트가 줄일 수 있게
    # 클래스 상수로 둔다(attach notice + 중첩 자동 승격이 공유).
    _FIRST_STATUS_TRIES = 30
    _FIRST_STATUS_DELAY = 0.1

    # keepalive ping 주기(초): 다운스트림→업스트림 federation 링크가 이 간격마다
    # ping 을 보낸다. 업스트림 死-클라 회수 임계(CLIENT_IDLE_TIMEOUT=30s)보다 훨씬
    # 짧아(6배) 정상 링크는 여유롭게 살아 있음이 갱신되고, 다운스트림이 죽으면 ping
    # 이 끊겨 업스트림이 임계 초과로 좀비 클라를 회수한다. 테스트가 줄일 수 있게 상수.
    _LINK_PING_INTERVAL = 5.0

    # Server.__init__ 가 부르지 않아도 동작하도록 지연 초기화 헬퍼.
    def _remotes_dict(self) -> dict:
        d = getattr(self, "_remotes", None)
        if d is None:
            d = self._remotes = {}
        return d

    def _remote_reconn_dict(self) -> dict:
        """진행 중 자동 재연결 태스크 {이름: Task} — 명시 detach/재attach 가 취소."""
        d = getattr(self, "_remote_reconn", None)
        if d is None:
            d = self._remote_reconn = {}
        return d

    # ---- 링크보다 오래 사는 sticky 상태(핀·단일-탭 분리) ----
    def _remote_sticky_dict(self) -> dict:
        """{호스트 이름: {"pinned": set, "detached": set}} — 그 호스트 원격 탭의
        **다운스트림 로컬** sticky 상태. RemoteLink 보다 **오래 산다**: 같은 호스트에
        다시 attach 하면(사용자 재-attach·백오프 자동 재연결) 링크 객체는 새로 만들어
        지는데, 핀/분리는 링크가 아니라 **보는 쪽 탭바 취향**이라 링크 수명에 묶이면
        안 된다(사용자 보고 2026-07-15: 원격 탭이 열린 채 같은 서버에 다시 remote-attach
        하면 핀이 전부 풀림 — 새 RemoteLink 의 빈 집합이 옛 집합을 덮어썼다).
        명시 detach(사용자 의사)는 _remote_sticky_forget 이 지운다."""
        d = getattr(self, "_remote_sticky", None)
        if d is None:
            d = self._remote_sticky = {}
        return d

    def _remote_sticky_bind(self, link: RemoteLink):
        """링크의 핀/분리 집합을 호스트별 sticky 저장소 set 에 **공유 참조**로 바인딩
        (복사 아님 — link.pinned_windows.add() 가 저장소에 그대로 반영된다). 집합이
        상류 안정 wid(_win_key)로 키잉돼 재-attach 사이 상류 탭이 바뀌어도 자동 정합
        이다: 새로 생긴 탭은 키가 집합에 없어 비고정, 사라진 탭의 키는 어느 탭과도 안
        맞아 조용히 무시된다(추가·삭제분만 갱신). 그래서 여기서 따로 가지치기하지
        않는다 — 상류가 탭을 잠깐 닫았다 되살려도(같은 wid) 핀이 살아남는다."""
        st = self._remote_sticky_dict().setdefault(
            link.host, {"pinned": set(), "detached": set()})
        link.pinned_windows = st["pinned"]
        link.detached_windows = st["detached"]

    def _remote_sticky_forget(self, name: str):
        """명시 detach 로 이 호스트의 sticky 상태를 버린다 — 나중에 다시 attach 하면
        (자동 재연결/재-attach 와 달리) 새 뷰라 전 탭이 비고정·전부 보이는 게 맞다."""
        self._remote_sticky_dict().pop(name, None)

    # ---- 전송 열기 ----
    async def _remote_transport(self, host: str | None, endpoint: str | None):
        """(reader, writer, token, proc) 를 연다. endpoint=같은 머신 직결(테스트/로컬
        페더레이션), host=`ssh -T host pytmux stdio-proxy`(첫 줄 TOKEN)."""
        if endpoint:
            reader, writer = await ipc.open_connection(endpoint)
            return reader, writer, (ipc.read_token(endpoint) or ""), None
        # S2: host 는 클라 cmd(remote_attach)에서 온 **비신뢰** 문자열이다. argv 형이라
        # 셸 인젝션은 없지만 ssh 자체가 argv 의 '-...' 를 옵션으로 해석하므로, host 가
        # '-oProxyCommand=<명령>' 이면 임의 명령이 실행된다(옵션 인젝션 → RCE). 선행 '-'·
        # 공백을 거부하고, '--' 로 ssh 옵션 파싱을 끊어 host 를 목적지로만 해석시킨다.
        # (host-key 정책은 사용자 ssh config/known_hosts 를 그대로 따른다 — 강제 변경은
        #  동작하는 설정을 깨거나 보안을 느슨하게 만들 수 있어 건드리지 않는다.)
        if not host or host.startswith("-") or any(c.isspace() for c in host):
            raise ConnectionError(f"잘못된 원격 호스트: {host!r}")
        # S2 후속: 연합 허용목록이 설정돼 있으면(opt-in, opts.json `remote_allowed_hosts`)
        # 정확히 일치하는 목적지만 허용한다 — 비신뢰 클라 입력이 데몬의 ssh egress 를
        # 임의 호스트로 조종하지 못하게. 빈 목록(기본)은 현행대로 임의 host 허용.
        allow = getattr(self, "remote_allowed_hosts", None) or []
        if allow and host not in allow:
            raise ConnectionError(
                f"허용되지 않은 원격 호스트: {host!r} "
                f"(opts.json remote_allowed_hosts 에 없음)")
        # BatchMode: 서버가 띄우는 ssh 는 TTY 가 없어 비밀번호를 못 묻는다 — 키
        # 인증 미설정이면 즉시 명확한 stderr(Permission denied)로 실패하게 한다.
        # ServerAlive*: M2 keepalive — half-open(상대가 데이터도 FIN 도 안 보내는 좀비
        # /웨지) 연결을 ssh 가 15s×3=45s 내 감지해 끊어 준다. 그러면 stdio-proxy 파이프가
        # EOF→_remote_reader 가 끊김으로 처리해 자동 재연결/회수가 진행된다(무프로토콜 변경).
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-T", "-o", "BatchMode=yes",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "--",
            host, "pytmux", "stdio-proxy",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        # 모든 실패 경로(readline 타임아웃·핸드셰이크 실패·readline 의 LimitOverrunError·
        # 취소)에서 ssh 자식과 그 파이프 3 fd 를 회수한다(H4: 좀비/fd 누수가 재연결
        # 스톰과 결합하던 것 차단). 성공 시에만 proc 를 호출자(RemoteLink)에 넘긴다.
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), 15)
            if not line.startswith(b"TOKEN "):
                # 실패 원인(ssh/원격 stderr 의 마지막 줄)을 사용자 알림에 실어 준다 —
                # 'Permission denied'(키 미설정)·'command not found'(원격 PATH/미설치)
                # 등이 그대로 보이게.
                err = b""
                try:
                    err = await asyncio.wait_for(proc.stderr.read(2048), 2)
                except asyncio.TimeoutError:
                    pass
                detail = _strip_ctrl(_decode_remote_stderr(err or line)).strip()
                detail = detail.splitlines()[-1] if detail else ""
                if not detail:
                    raise RemoteError("rerr.handshake_noresp",
                                      "stdio-proxy 핸드셰이크 실패: 응답 없음")
                if "Permission denied" in detail:
                    # 비대화 ssh(BatchMode)는 비밀번호를 못 묻는다 — 패스워드 전용
                    # 호스트는 ControlMaster 로 인증된 연결을 공유하면 된다(§5). 키에
                    # 힌트를 합쳐 둬 ko/en 모두 자연스럽게 번역된다(detail=원격 stderr).
                    raise RemoteError(
                        "rerr.handshake_perm",
                        "stdio-proxy 핸드셰이크 실패: {detail} — 키 미설정. "
                        "패스워드 호스트는 ssh config 에 ControlMaster 설정 후 "
                        "패널에서 한 번 로그인(REMOTE_ATTACH_SCENARIO §5)",
                        detail=detail)
                raise RemoteError("rerr.handshake_fail",
                                  "stdio-proxy 핸드셰이크 실패: {detail}",
                                  detail=detail)
            tok = line.split(b" ", 1)[1].strip().decode()
            if self._is_self_ssh_token(tok):
                # ssh 로 자기 자신에 되붙음(remote-attach <자기호스트/localhost>):
                # 원격(=자기 데몬)이 회신한 TOKEN 이 우리 서버 토큰과 같다. endpoint
                # 경로의 self-attach 가드(sock_path 비교)는 host 경로를 못 잡으므로
                # 여기서 토큰으로 차단한다 — 자기 ⇄ 탭 중복·ssh 낭비 방지(L2).
                raise RemoteError("rerr.self_attach",
                                  "자기 자신에는 attach 할 수 없습니다")
        except BaseException:
            await _kill_proc(proc)
            raise
        return proc.stdout, proc.stdin, tok, proc

    def _is_self_ssh_token(self, tok: str) -> bool:
        """ssh host 경로로 원격이 회신한 TOKEN 이 우리 서버 자신의 토큰과 같은지
        (=자기 자신에 ssh 로 되붙음, L2). auth_token 미설정(테스트/구경로)이면 False."""
        return bool(getattr(self, "auth_token", None)) and hmac.compare_digest(
            tok, self.auth_token)

    # ---- attach / detach ----
    async def remote_attach(self, sess, host: str | None = None,
                            endpoint: str | None = None) -> bool:
        """원격 서버에 업스트림 클라로 attach. 성공하면 탭바에 원격 탭이 병합된다.
        같은 이름의 기존 링크는 교체(detach 후 재연결)."""
        name = host or endpoint
        if not name:
            return False
        # 자기 자신 attach 가드: 자기 status 의 ⇄ 탭을 다시 흡수해 탭 목록이 status
        # 왕복마다 한 단계씩 무한 증식하는 루프를 차단한다.
        if endpoint and endpoint in (self.sock_path,
                                     getattr(self, "resolved_endpoint", None)):
            self._set_err("rerr.self_attach", "자기 자신에는 attach 할 수 없습니다")
            return False
        remotes = self._remotes_dict()
        if name in remotes:
            await self.remote_drop(remotes[name], notify=False)
        # 같은 이름의 보류 자동 재연결은 취소(이 attach 가 권위) — 단, 재연결 루프
        # 자신이 부른 경우는 자기 태스크라 건드리지 않는다.
        pend = self._remote_reconn_dict().get(name)
        if pend is not None and pend is not asyncio.current_task():
            self._remote_reconn_dict().pop(name, None)
            pend.cancel()
        self._set_err(None, "")
        try:
            reader, writer, tok, proc = await self._remote_transport(
                host, endpoint)
        except RemoteError as e:
            # 구조화된 실패 원인(핸드셰이크 등) — 키 보존(클라가 자기 로케일로 번역).
            self._set_err(e.err_key, e.err_ko, **e.err_kw)
            self._log_error(f"remote_attach({name})")
            return False
        except (OSError, ConnectionError, asyncio.TimeoutError,
                asyncio.LimitOverrunError) as e:
            # OS/네트워크 예외 메시지는 키가 없다(대개 영어) — 원문 그대로 detail 로.
            # LimitOverrunError: stdio-proxy 첫 줄이 개행 없이 64KiB 를 넘으면(고장난
            # 원격) readline 이 던진다 — _remote_transport 가 proc 정리 후 재전파(H4).
            self._set_err(None, str(e) or type(e).__name__)
            self._log_error(f"remote_attach({name})")
            return False
        link = RemoteLink(name, reader, writer, proc)
        link.spec = {"host": host, "endpoint": endpoint}
        link.sess = sess
        # 같은 호스트의 옛 링크가 있었으면(바로 위 remote_drop 이 교체) 그 핀·단일-탭
        # 분리를 새 링크가 이어받는다 — 링크 수명이 아니라 호스트 수명 상태다.
        self._remote_sticky_bind(link)
        cols, rows = self._session_size(sess)
        hello = {"t": "hello", "proto": PROTO_VERSION,
                 "cols": cols, "rows": rows}
        # 모호폭 모드(cellwidth) 전파: 이 다운스트림 단말이 East Asian Ambiguous 를
        # 2칸으로 그리면(로컬 클라 hello 의 ambig 로 이 서버 전역이 wide), 업스트림
        # pyte 격자도 같은 폭으로 맞춰야 원격 Claude TUI 가 정확히 격자에 앉는다.
        # 안 그러면 업스트림은 narrow 로 레이아웃하고 이 단말은 wide 로 그려 한 줄이
        # 1칸씩 밀려 좌우 겹침·패널 아웃라인 침범이 난다(원격 탭만의 회귀). 로컬
        # 클라→서버 hello(clientconn) 와 동형. narrow 단말이면 키를 안 실어 무영향.
        from . import cellwidth
        if cellwidth.ambiguous_wide():
            hello["ambig"] = "wide"
        if tok:
            hello["token"] = tok
        try:
            await write_msg(writer, hello)
        except (OSError, ConnectionError) as e:
            # 전송은 열렸으나 hello 송신 실패 — 연 소켓/ssh proc 를 회수한다(H4: 안
            # 그러면 transport 가 연 writer + proc 가 누수). remotes 에 아직 등록 전.
            try:
                writer.close()
            except (OSError, ConnectionError):
                pass
            await _kill_proc(proc)
            self._set_err("rerr.hello_fail", "hello 실패: {e}", e=str(e))
            self._log_error(f"remote_attach hello({name})")
            return False
        remotes[name] = link
        link.task = self.loop.create_task(self._remote_reader(link))
        # keepalive ping 가동: 업스트림 死-클라 회수가 이 링크(federation 클라)를
        # 인지하게 해, 다운스트림 비정상 종료 시 좀비 클라가 세션 크기를 작게 핀한 채
        # 남지 않게 한다(RemoteLink.ping_task 주석 참조).
        link.ping_task = self.loop.create_task(self._remote_ping_loop(link))
        return True

    async def _remote_ping_loop(self, link: RemoteLink):
        """다운스트림→업스트림 keepalive: _LINK_PING_INTERVAL 마다 ping 을 보낸다.
        업스트림 handle_client 가 이 ping 으로 client.ever_pinged=True + last_seen
        갱신 → 死-클라 회수(_liveness_loop)가 이 링크를 회수 대상으로 인지하고, 링크가
        살아 있는 한 last_seen 이 계속 갱신돼 오탐 회수도 없다. 회신 pong 은
        _remote_reader 가 소비(릴레이 안 함). 송신 실패는 링크 사망 신호 —
        _remote_reader 의 EOF 처리(remote_drop+재연결)에 맡기고 조용히 종료한다."""
        try:
            while link.alive:
                await asyncio.sleep(self._LINK_PING_INTERVAL)
                if not link.alive:
                    return
                try:
                    await self._link_write(link, {"t": "ping",
                                                  "ts": time.monotonic()})
                except (OSError, ConnectionError):
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # keepalive 는 보조 기능 — 예기치 못한 오류로 죽어도 링크 자체(reader)는
            # 살리고 조용히 로그만 남긴다(reader 의 EOF 경로가 사망을 정식 처리).
            self._log_error(f"remote_ping({link.host})")

    async def _remote_wait_first_status(self, link: RemoteLink,
                                        tries: int | None = None,
                                        delay: float | None = None) -> bool:
        """첫 업스트림 status(탭 병합)를 잠깐 기다린다. windows 가 채워지면 True,
        링크가 죽거나 시한 내 status 가 안 오면 False. remote_attach 의 '병합됨'
        알림과 중첩 자동 승격(_nest_do_attach)이 공유 — hello 송신만으로 성공을
        단정하지 않고 **실제 탭 도착**을 성공 기준으로 삼는다. 업스트림이 hello 는
        받고도 _send_full(→_layout_msg→p.resize)이 멈춰 status 를 안 보내는 웨지를
        조용한 실패 대신 명확한 신호로 만든다(원격 pty-host 고장 사례 2026-06-20)."""
        tries = self._FIRST_STATUS_TRIES if tries is None else tries
        delay = self._FIRST_STATUS_DELAY if delay is None else delay
        for _ in range(tries):
            if link.windows:
                return True
            if not link.alive:
                return False
            await asyncio.sleep(delay)
        return bool(link.windows)

    async def remote_drop(self, link: RemoteLink, notify: bool = True,
                          reconnect: bool = False):
        """링크 해제: 보던 클라는 로컬 화면으로 복귀, 탭바에서 원격 탭 제거.
        reconnect=True(비명시적 죽음 — EOF/오류)면 백오프 자동 재연결을 건다."""
        link.alive = False
        self._remotes_dict().pop(link.host, None)
        if link.task is not None and not link.task.done():
            link.task.cancel()
        if link.ping_task is not None and not link.ping_task.done():
            link.ping_task.cancel()
        for closer in (lambda: link.writer.close(),
                       lambda: link.proc and link.proc.kill()):
            try:
                closer()
            except (OSError, ProcessLookupError):
                pass
        for c in list(self.clients):
            if c.remote_view == link.host:
                c.remote_view = None
                if notify:
                    asyncio.create_task(self._send_full(c))
        if notify:
            self._remote_status_broadcast()
        if reconnect:
            self._remote_schedule_reconnect(link)

    def remote_detach(self, name: str | None = None):
        """이름 지정(없으면 전부) 링크 해제 — `remote-detach [host]`. 보류 중인
        자동 재연결도 함께 취소한다(명시 해제 = 사용자 의사)."""
        reconn = self._remote_reconn_dict()
        for n in ([name] if name else list(reconn)):
            t = reconn.pop(n, None)
            if t is not None:
                t.cancel()
            # 재연결 대기 중(링크 객체 없음)이라 아래 targets 에 안 잡혀도 명시 detach
            # 는 sticky(핀·분리)를 버린다 — 아래 루프는 살아 있는 링크만 훑는다.
            self._remote_sticky_forget(n)
        remotes = self._remotes_dict()
        targets = ([remotes[name]] if name and name in remotes
                   else list(remotes.values()) if not name else [])
        for link in targets:
            self._remote_sticky_forget(link.host)
            asyncio.create_task(self.remote_drop(link))
        if name is None:
            # detach-all: 재연결을 이미 포기한 호스트(reconn·remotes 어디에도 없어
            # 위 두 루프에 안 잡힘)의 고아 sticky 까지 버린다 — 안 그러면 나중에 그
            # 호스트에 다시 attach 할 때 옛 핀/분리가 되살아나 '명시 detach 만 버림'
            # 계약을 어긴다(검수 F-3). 공유 참조라 재대입 말고 in-place clear.
            self._remote_sticky_dict().clear()
        return bool(targets)

    def remote_detach_tab(self, sess, gi: int) -> bool:
        """원격(병합) 탭 **하나만** 이 뷰에서 분리한다 — ssh 링크와 같은 호스트의
        다른 탭은 그대로 둔다(remote_detach 는 호스트 전체를 끊는다). 상류 안정 window
        id(_win_key)를 per-link detached_windows 에 넣어 병합 탭바에서만 숨긴다.
        그 링크의 마지막 남은 탭까지 분리하면 링크 전체를 해제한다(빈 링크를 유지할
        이유가 없고, 자동 재연결도 취소해야 하므로 remote_detach 로 위임).
        gi=병합 전역 탭 index. 원격 탭이 아니면 False."""
        hit = self._remote_tab_at(sess, gi)
        if hit is None:
            return False
        link, ri = hit
        key = (self._win_key(link.windows[ri])
               if ri < len(link.windows) else None)
        if key is not None:
            link.detached_windows.add(key)
        # 남은(안 숨긴) 탭이 없으면 링크(ssh) 전체를 해제한다.
        if not self._visible_windows(link):
            self.remote_detach(link.host)
            return True
        # 닫기[x]/esc x 는 항상 **지금 보는** 원격 탭에서만 온다 → 이 호스트를 보던
        # 클라는 로컬 화면으로 복귀시킨다(full detach 와 동일 UX). 남은 원격 탭은
        # 탭바에 그대로 있어 다시 선택하면 재진입한다.
        for c in list(self.clients):
            if getattr(c, "remote_view", None) == link.host:
                c.remote_view = None
                asyncio.create_task(self._send_full(c))
        self._remote_status_broadcast()
        return True

    # ---- 끊김 백오프 자동 재연결(Stage 3) ----
    def _remote_schedule_reconnect(self, link: RemoteLink):
        # 서버 종료 중(EOF 가 teardown 에서 옴)엔 예약하지 않는다 — 종료 후 남는
        # 고아 재연결 태스크("Task was destroyed but it is pending") 방지.
        if not self.running:
            return
        reconn = self._remote_reconn_dict()
        if link.host in reconn or link.sess is None or not link.spec:
            return
        reconn[link.host] = self.loop.create_task(
            self._remote_reconnect_loop(link))

    async def _remote_reconnect_loop(self, link: RemoteLink):
        """비명시적 끊김 후 백오프 재시도. 성공/포기를 notice 로 알린다. 유한 회수
        — §1 의 무상한 '재접속 루프'를 페더레이션에 재현하지 않는다."""
        name, sess = link.host, link.sess
        try:
            for i, delay in enumerate(self._RECONNECT_DELAYS, 1):
                await asyncio.sleep(delay)
                if not self.running or sess not in self.sessions.values():
                    return
                if await self.remote_attach(sess, host=link.spec.get("host"),
                                            endpoint=link.spec.get("endpoint")):
                    # H5: remote_attach 는 hello 송신 직후 True 라 '연결됨'을 단정하지
                    # 못한다 — 업스트림이 hello 만 받고 _send_full(→resize)이 멈춘
                    # pty-host 웨지면 탭이 안 온다. 대화형 attach·중첩 승격과 동일하게
                    # **실제 첫 status(탭 병합) 도착**을 성공 기준으로 삼는다. 첫
                    # status 가 안 오면 이 시도는 실패로 간주하고 다음 백오프로 재시도
                    # (다음 remote_attach 가 같은 이름 링크를 교체하므로 누수 없음).
                    newlink = self._remotes_dict().get(name)
                    if newlink is not None and \
                            await self._remote_wait_first_status(newlink):
                        self._remote_status_broadcast()
                        self._remote_notice(
                            sess, "rnotice.reconnected",
                            "remote-attach {target}: 끊김 후 자동 재연결됨(시도 {i})",
                            target=name, i=i)
                        return
            # 마지막 시도의 실패 원인(_set_err — Permission denied/PATH 등)을 함께 실어
            # 준다(요청): 핸드셰이크가 반복 실패해 포기하는 그 순간이 원인이 가장 필요한
            # 지점인데 종전엔 "포기"만 알렸다. sticky=수동 닫기까지 유지.
            detail = self._err_detail("rerr.unknown_log", "원인 미상(서버 error.log)")
            self._remote_notice(
                sess, "rnotice.reconnect_giveup",
                "remote-attach {target}: 자동 재연결 포기({n}회) — {why} · "
                ":remote-attach 로 수동 재시도", sticky=True, detail=detail,
                target=name, n=len(self._RECONNECT_DELAYS))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log_error(f"remote_reconnect({name})")
        finally:
            self._remote_reconn_dict().pop(name, None)

    def remote_shutdown(self):
        """서버 종료 시 동기 정리: 보류 재연결 취소 + 링크 전송/ssh 종료(루프가 곧
        멈추므로 코루틴 drop 대신 즉시 닫는다)."""
        for t in list(self._remote_reconn_dict().values()):
            t.cancel()
        self._remote_reconn_dict().clear()
        for link in list(self._remotes_dict().values()):
            link.alive = False
            if link.task is not None and not link.task.done():
                link.task.cancel()
            if link.ping_task is not None and not link.ping_task.done():
                link.ping_task.cancel()
            for closer in (lambda l=link: l.writer.close(),
                           lambda l=link: l.proc and l.proc.kill()):
                try:
                    closer()
                except (OSError, ProcessLookupError):
                    pass
        self._remotes_dict().clear()

    # ─── 실패 원인(detail) 구조화 저장/조회 — 옛 _remote_last_err 문자열 대체 ───
    # 로케일은 per-user(클라-로컬)라 서버는 번역하지 않는다: 키(rerr.*)+ko 폴백+kw 를
    # detail dict 로 들고 있다가 notice 에 실어 클라가 자기 로케일로 합성하게 한다.
    def _set_err(self, key, ko: str, **kw):
        """마지막 원격 실패 원인을 구조화 저장. key=None 이면 ko(원문, 대개 OS 영어)만."""
        self._remote_err = {"key": key, "text": ko.format(**kw) if kw else ko,
                            "kw": kw}

    def _err_detail(self, default_key: str, default_ko: str) -> dict:
        """마지막 실패 원인을 notice detail dict 로. 비어 있으면 기본 키/문구로 폴백."""
        e = getattr(self, "_remote_err", None)
        if e and e.get("text"):
            return e
        return {"key": default_key, "text": default_ko, "kw": {}}

    @staticmethod
    def _notice_msg(key, ko_text: str, *, sticky: bool = False,
                    detail: dict | None = None, **kw) -> dict:
        """notice 메시지 dict 를 만든다. text=한국어 폴백(구클라/테스트), key+kw=클라
        번역용. detail 이 있으면 {why} 자리에 실패 원인(키 포함)을 넘겨 클라가 합성.
        sticky=놓치면 안 되는 알림(3초 유지 + 클릭/Enter 수동 닫기)."""
        if detail is not None:
            kw = dict(kw)
            kw["why"] = detail.get("text", "")   # ko 폴백; 클라가 detail 로 덮어씀
        text = ko_text.format(**kw) if kw else ko_text
        msg = {"t": "notice", "text": text, "key": key, "kw": kw}
        if detail is not None:
            msg["detail"] = detail
        if sticky:
            msg["secs"] = 3.0
            msg["dismissable"] = True
        return msg

    def _remote_notice(self, sess, key, ko_text: str, *, sticky: bool = False,
                       detail: dict | None = None, **kw):
        """세션의 모든 클라에 상태줄 notice(§1.7 attach 결과와 동일 표면).
        key+ko_text+kw 로 클라가 자기 로케일로 번역한다(_notice_msg 참조)."""
        msg = self._notice_msg(key, ko_text, sticky=sticky, detail=detail, **kw)
        for c in list(self.clients):
            if c.session is sess:
                asyncio.create_task(self._send_to(c, dict(msg)))

    # ---- 재시작(re-exec) 후 링크 복원(Stage 3) ----
    def remote_restore_links(self):
        """restore_resume_state 가 보관한 spec(_remote_resume)으로 부트 후 재연결.
        serve() 가 이벤트 루프 가동 직후 1회 호출한다. ssh 서브프로세스는 execv 를
        살아남지 못하므로(파이프 CLOEXEC) 링크는 항상 새로 연다."""
        specs = getattr(self, "_remote_resume", None) or []
        self._remote_resume = []
        if not specs or not self.sessions:
            return
        sess = next(iter(self.sessions.values()))

        async def _restore():
            for spec in specs:
                name = spec.get("host") or spec.get("endpoint") or "?"
                try:
                    if await self.remote_attach(sess, host=spec.get("host"),
                                                endpoint=spec.get("endpoint")):
                        # §12 ① 원격 탭 핀 복원: 재시작 전 다운스트림 로컬 핀 집합을
                        # 새 링크에 되살린다(_resume_payload 가 spec 에 실어 둠). 원격
                        # 창 index(ri) 기준이라 업스트림 창 구성이 그대로면 정합.
                        pins = spec.get("pinned_windows")
                        det = spec.get("detached_windows")
                        if pins or det:
                            link = self._remotes_dict().get(name)
                            if link is not None:
                                # update(재대입 아님): 집합은 호스트별 sticky 저장소와
                                # 공유하는 객체다(_remote_sticky_bind) — 갈아끼우면
                                # 이후 재-attach 가 핀을 잃는다. 부팅 직후라 비어 있어
                                # update==대입.
                                if pins:
                                    link.pinned_windows.update(pins)
                                # 단일-탭 분리 복원: 업스트림 window index 기준이라
                                # 우리 서버 재시작(업스트림 불변)을 그대로 살아남는다.
                                if det:
                                    link.detached_windows.update(det)
                        self._remote_status_broadcast()
                    else:
                        detail = self._err_detail("rerr.unknown", "원인 미상")
                        self._remote_notice(
                            sess, "rnotice.restore_fail",
                            "remote-attach {target}: 재시작 후 복원 실패 — {why}",
                            sticky=True, detail=detail, target=name)
                except Exception:
                    self._log_error(f"remote_restore({name})")

        self.loop.create_task(_restore())

    # ---- 업스트림 수신 ----
    async def _remote_reader(self, link: RemoteLink):
        """업스트림 메시지 루프: status 는 흡수(탭바 병합), bye/EOF 는 링크 해제,
        그 외(layout/screen/screen-delta 등)는 이 링크를 **보는** 클라에 그대로
        전달한다."""
        deliberate_bye = False    # 업스트림 발 "bye"=고의 해제 → 자동 재연결 금지
        try:
            while link.alive:
                try:
                    msg = await read_msg(link.reader)
                except (OSError, ConnectionError, asyncio.IncompleteReadError):
                    msg = None
                if msg is None:
                    break
                if not isinstance(msg, dict):
                    # 손상/악성 원격: 비-dict JSON 프레임(`[]`·`42`·`"x"`)은 무시하고
                    # 계속 — 종전엔 msg.get() 이 AttributeError→광역 except→링크 드롭→
                    # 무한 재연결 churn(L1)이었다. 링크는 유지한다.
                    continue
                t = msg.get("t")
                if t == "pong":
                    # keepalive ping(_remote_ping_loop)의 회신 — RTT 관심 없음.
                    # 소비만 하고 보는 클라에 릴레이하지 않는다(_relay_frame_ok 는
                    # 미지 t 를 통과시키므로 여기서 명시 차단; 안 그러면 다운스트림
                    # 클라가 자기가 안 보낸 pong 을 받아 RTT 표본이 오염된다).
                    continue
                if t == "status":
                    # 누적(update): 업스트림 full status 가 채운 옵션 키를 이후
                    # light status 가 지우지 않게 합친다 — 보는 클라 오버라이드의
                    # 원천(_remote_status_override).
                    link.last_status.update(msg)
                    wins = msg.get("windows", [])
                    if wins != link.windows:
                        link.windows = wins
                        self._remote_status_broadcast()
                    else:
                        # 탭 목록 불변이어도 부가필드(Claude 헤더/토큰·pane_title
                        # 등)는 변했을 수 있다 — 보는 클라만 갱신.
                        self._remote_viewer_status(link)
                    continue
                if t == "bye":
                    # 업스트림이 우리를 **고의로** 내보냄 — detach_others(다른 뷰어가
                    # `detach -a`)·kill-server·마지막 세션 소멸(_notify_no_sessions).
                    # 사고 EOF/오류와 달리 **자동 재연결하지 않는다**: 안 그러면
                    # detach_others 로 원격 클라를 떨궈도 즉시 되붙어(_session_size=min
                    # 재-핀) eviction 이 무효화되고, 원격 뷰가 계속 레터박스로 작게
                    # 남는다(kill/detach/대기 다 무효 보고 2026-07-11). "restarting"
                    # (세션유지 재시작)은 종전대로 재연결 대상이다.
                    deliberate_bye = True
                    break
                if t == "restarting":
                    break
                # §4.1: 요청 클라 식별자가 echo 돼 왔으면(request_token_log 회신) 그
                # 클라에게만 전달한다 — 같은 호스트를 보는 다른 다운스트림 클라에
                # 응답(토큰 팝업)이 새지 않게. 라우팅 전용 필드라 클라에 보내기 전 제거.
                # 없으면(layout/screen/nc_list 등) 종전대로 이 링크를 보는 전 클라에
                # 브로드캐스트. 요청 클라가 사라졌으면 매칭 0 → 조용히 드롭(정상).
                req_token = msg.pop("_req_token", None)
                if not _relay_frame_ok(t, msg):
                    # 악성/침해 원격이 relay 하는 손상 프레임(누락 pane/rows 등)을
                    # 다운스트림 클라가 무가드로 소비하면 KeyError→reader 워커 종료→
                    # 클라 앱 크래시(M1). 신뢰경계에서 필수 shape 를 검증해 드롭한다.
                    continue
                frame = frame_msg(msg)
                for c in list(self.clients):
                    if c.remote_view != link.host:
                        continue
                    if req_token is not None and id(c) != req_token:
                        continue
                    try:
                        await self._send_frames_to(c, [frame])
                    except (OSError, ConnectionError):
                        pass
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log_error(f"remote_reader({link.host})")
        finally:
            if link.alive:                 # EOF/오류/restarting=정리+복귀+자동재연결,
                # 업스트림 발 "bye"(고의 해제)만 재연결 억제.
                await self.remote_drop(link, reconnect=not deliberate_bye)

    # ---- 탭바 병합 ----
    @staticmethod
    def _win_key(rw: dict):
        """원격 window 를 가리키는 **안정 키**. 상류가 안정 wid 를 실어 보내면 그것을,
        (구버전 상류라) 없으면 위치값 index 로 폴백한다(M-1). 한 링크의 상류는 단일
        버전이라 집합 내 키가 동형이다(wid 만 or index 만 — 정수끼리 충돌 없음).
        위치 index 로 키잉하던 종전엔 상류 탭 close/reorder 로 _reindex 가 index 를
        재할당해 숨긴 탭이 엉뚱한 탭으로 옮겨갔다(코드검수 2026-07-10).

        키는 **int 만** 인정한다(검수 F-2): 상류 JSON 은 신뢰불가 입력이라 wid/index 가
        list·dict(해시불가) 또는 타입 혼재(1 과 "1")로 오면, 집합 멤버십에서 TypeError
        가 나 링크가 끊기거나, 핀 집합에 int·str 이 섞여 _resume_payload 의 sorted() 가
        TypeError 로 세션유지 재시작을 깨뜨린다. 정수 아닌 값은 None(=키 없음)으로 낮춰
        graceful 하게 무시한다(그 창은 sticky 대상에서 빠질 뿐, 링크는 산다)."""
        wid = rw.get("wid")
        if isinstance(wid, int) and not isinstance(wid, bool):
            return wid
        idx = rw.get("index")
        return idx if isinstance(idx, int) and not isinstance(idx, bool) else None

    def _visible_windows(self, link: RemoteLink) -> list:
        """이 링크에서 병합 탭바에 보일 (ri, rw) 목록 — 단일-탭 분리
        (detached_windows)로 숨긴 window 는 제외한다. ri 는 full link.windows
        위치 index 로, **라이브 relay**(select_window·_remote_tab_at 가 현재 status 의
        그 위치 창을 업스트림에 지목)용이다 — 현재 status 스냅샷 기준이라 위치가 정합.
        반면 **영속·sticky 상태**(detached_windows·pinned_windows)는 상류 재정렬을
        넘어야 하므로 안정 키(_win_key: 상류 wid, 없으면 index 폴백)로 키잉한다."""
        return [(ri, rw) for ri, rw in enumerate(link.windows)
                if self._win_key(rw) not in link.detached_windows]

    def _remote_tabs(self, base: int, client=None) -> list:
        """병합 탭 목록에 덧붙일 원격 탭 엔트리(전역 index 는 base 부터 연속).
        active 는 클라별(Stage 3) — 그 링크를 보는 클라에게만 업스트림 active 를
        보존해 원격 탭이 하이라이트된다(안 보는 클라/클라 미지정은 False).
        remote=True(§1.7-a)로 클라가 탭바/외곽선을 분홍으로 그려 로컬과 구분한다
        (이름 ⇄ 접두사 파싱 대신 명시 플래그 — 이름은 표시 전용으로 남긴다)."""
        out = []
        gi = base
        for link in self._remotes_dict().values():
            viewing = (client is not None
                       and getattr(client, "remote_view", None) == link.host)
            for ri, rw in self._visible_windows(link):
                out.append({"index": gi, "name": f"⇄{link.host}:{rw.get('name', '')}",
                            "active": bool(viewing and rw.get("active")),
                            "remote": True,
                            # §12 ① 로컬 핀 — detached 와 동일하게 안정 wid(_win_key)로
                            # 키잉(상류 탭 close/reorder 로도 핀이 안 어긋남, 로드맵 #3).
                            "pinned": self._win_key(rw) in link.pinned_windows,
                            "bell": rw.get("bell", False),
                            "activity": rw.get("activity", False),
                            "claude_done": rw.get("claude_done", False)})
                gi += 1
        return out

    def _remote_tab_at(self, sess, index: int):
        """병합 전역 index(>= len(sess.tabs)) → (link, 원격 탭 index). 없으면 None."""
        gi = len(sess.tabs)
        for link in self._remotes_dict().values():
            for ri, _ in self._visible_windows(link):
                if gi == index:
                    return link, ri
                gi += 1
        return None

    def set_remote_pinned(self, sess, gi: int, value=None):
        """§12 ①: 원격(병합) 탭 gi 의 **다운스트림 로컬** 고정(핀)을 토글/설정한다.
        핀은 보는 쪽 탭바 레이아웃 문제라 업스트림에 전파하지 않고 per-link 집합에
        저장 → _remote_tabs 가 매 status 에 pinned 비트를 실어 준다. 위치 ri 가 아니라
        상류 안정 wid(_win_key, 구상류는 index 폴백)로 키잉해 상류 탭 close/reorder 로도
        핀이 엉뚱한 탭으로 옮겨가지 않는다(로드맵 #3 — detached_windows M-1 과 동일 수정).
        value=None 이면 토글. 변동을 전 클라 탭바에 즉시 방송."""
        hit = self._remote_tab_at(sess, gi)
        if hit is None:
            return
        link, ri = hit
        if not (0 <= ri < len(link.windows)):
            return
        key = self._win_key(link.windows[ri])
        if key is None:      # 상류가 안정 키를 안 줬거나 오염(F-2) — 핀 불가, 무시
            return
        if value is None:
            value = key not in link.pinned_windows
        if value:
            link.pinned_windows.add(key)
        else:
            link.pinned_windows.discard(key)
        self._remote_status_broadcast()

    def _remote_status_broadcast(self):
        """원격 탭 목록 변동을 모든 세션 클라의 탭바에 반영. status 는 클라별
        (Stage 3 — 보는 클라=업스트림 오버라이드, 그 외=로컬)로 조립한다."""
        for sess in self.sessions.values():
            for c in [c for c in self.clients if c.session is sess]:
                frame = frame_msg(self._status_msg(sess, full=False, client=c))
                asyncio.create_task(self._send_frames_to(c, [frame]))

    def _remote_viewer_status(self, link: RemoteLink):
        """이 링크를 보는 클라에게만 status 재전송(업스트림 부가필드 갱신 반영)."""
        for c in list(self.clients):
            if c.remote_view == link.host and c.session is not None:
                frame = frame_msg(
                    self._status_msg(c.session, full=False, client=c))
                asyncio.create_task(self._send_frames_to(c, [frame]))

    def _remote_status_override(self, sess, client):
        """보는 클라용 status(Stage 3): 업스트림 status 누적본을 기반으로 —
        active_pane/zoomed/pane_title/Claude 헤더·토큰 등 부가필드가 원격 것 그대로
        전달돼 클라가 원격 패널 헤더/상태줄을 로컬과 동일하게 그린다 — windows 만
        병합 탭바(로컬=비활성, 원격=업스트림 active 보존)로 바꾼다. 업스트림 status
        를 아직 못 받았으면 None(호출부가 로컬 status 경로)."""
        link = self._remote_link_for(client)
        if link is None or not link.last_status:
            return None
        msg = dict(link.last_status)
        msg["t"] = "status"
        msg["session"] = sess.name                   # #S 등 세션명은 로컬 유지
        msg["single_border"] = self.single_border    # 보더 스타일은 로컬 취향
        msg["windows"] = [
            {"index": t.index, "name": t.name, "active": False,
             "pinned": getattr(t, "pinned", False),   # §12 ① 로컬 핀 보존
             "bell": t.has_bell, "activity": t.has_activity,
             "claude_done": t.has_claude_done}
            for t in sess.tabs] + self._remote_tabs(len(sess.tabs), client)
        return msg

    # ---- 릴레이 ----
    def _remote_link_for(self, client) -> RemoteLink | None:
        if not getattr(client, "remote_view", None):
            return None
        return self._remotes_dict().get(client.remote_view)

    async def _link_write(self, link, msg) -> None:
        """업스트림 링크로의 송신을 link.write_lock 으로 직렬화한다(M-1) — 빠른 연속
        입력/리사이즈 릴레이가 서로/다중-await 송신과 섞여 순서가 뒤집히지 않게."""
        async with link.write_lock:
            await write_msg(link.writer, msg)

    def remote_relay(self, client, msg) -> bool:
        """보는 중인 클라의 메시지를 업스트림으로 그대로 전달. 링크가 없으면(죽음
        직후 레이스) False — 호출부가 로컬 폴백."""
        link = self._remote_link_for(client)
        if link is None:
            client.remote_view = None
            return False
        # §4.1 브로드캐스트 누출 필터: 응답을 회신하는 요청(request_token_log)은 요청
        # 클라 식별자(_req_token)를 실어 보낸다. 업스트림이 회신에 그대로 echo 하고
        # (serverio handle_server_request 분기) _remote_reader 가 그 클라에만 응답을
        # 전달해, 같은 원격 호스트를 보는 **다른** 다운스트림 클라에 토큰 팝업이 새지
        # 않게 한다. id(client)=이 다운스트림 프로세스 내 안정 키(원격은 불투명 echo).
        # 미태깅 메시지(입력/리사이즈/nc_list 등)는 종전대로 뷰어 전체 브로드캐스트.
        if msg.get("action") == "request_token_log":
            msg = dict(msg, _req_token=id(client))
        try:
            asyncio.create_task(self._link_write(link, msg))
        except (OSError, ConnectionError):
            return False
        return True

    def remote_relay_join(self, client, sess, msg) -> bool:
        """§1.7-c 예외 — **같은 원격 호스트의 두 탭을 합치는** join_pane 릴레이.
        전역 src index 의 원격 탭이 지금 보는 링크와 **같은 링크**면, src 를 그 원격
        서버의 로컬 window index 로 변환해 join_pane 을 그대로 업스트림에 보낸다.
        원격 서버는 자기 active 탭(=우리가 보는 탭)에 합치고(직전 select_pane_id
        릴레이로 대상 패널이 정해져 있다) status 로 줄어든 windows 를 돌려준다 →
        병합 탭바가 갱신된다. 로컬 src·다른 호스트·링크 사망이면 False(호출부가
        거부 폴백) — 원격↔로컬·원격↔타원격은 index 공간이 안 맞아 여전히 금지."""
        link = self._remote_link_for(client)
        if link is None:
            return False
        src = msg.get("src")
        if not isinstance(src, int):
            return False
        hit = self._remote_tab_at(sess, src)
        if hit is None:
            return False                      # 로컬 탭 src — 섞기 금지
        src_link, ri = hit
        if src_link is not link:
            return False                      # 다른 원격 호스트 — 머지 불가
        out = dict(msg)
        out["src"] = ri                       # 병합 전역 index → 원격 로컬 index
        try:
            asyncio.create_task(self._link_write(link, out))
        except (OSError, ConnectionError):
            return False
        return True

    async def remote_select_window(self, client, sess, index: int) -> bool:
        """병합 전역 index 의 원격 탭으로 진입: 보기 플래그를 세우고 업스트림에
        select_window 를 릴레이한다(업스트림이 _send_full 로 전체 화면을 보내오고
        reader 가 이 클라에 전달). 로컬 index 면 False(호출부가 로컬 처리)."""
        hit = self._remote_tab_at(sess, index)
        if hit is None:
            return False
        link, ri = hit
        client.remote_view = link.host
        await self._link_write(
            link, {"t": "cmd", "action": "select_window", "index": ri})
        return True

    async def remote_new_window(self, client, sess, host: str | None = None,
                                endpoint: str | None = None) -> bool:
        """`remote-new-tab <host>`: 원격 pytmux 에 **새 터미널(window)을 만들어** 그
        새 탭을 보여준다. remote-attach 가 원격의 기존 탭들을 병합·열람만 하는 것과
        달리, 이건 원격에 새 셸을 띄운다. 아직 attach 안 된 호스트면 먼저 attach 한다
        (그 호스트의 기존 원격 탭도 함께 병합됨 — 페더레이션 모델상 불가피).

        흐름: 이 클라의 보기를 그 호스트로 돌린 뒤 업스트림에 new_window 를 릴레이한다.
        업스트림은 새 창을 만들어 active 로 바꾸고 _send_full 로 layout/screen 을
        우리(=업스트림의 한 클라) 연결로 스트리밍한다 → reader 가 보는 클라에 전달하고,
        status 의 늘어난 windows 가 병합 탭바에 새 원격 탭으로(active) 나타난다. 실패
        (호스트 미지정·attach 실패·링크 사망)면 False."""
        name = host or endpoint
        if not name:
            return False
        remotes = self._remotes_dict()
        link = remotes.get(name)
        if link is None:
            if not await self.remote_attach(sess, host=host, endpoint=endpoint):
                return False
            link = remotes.get(name)
        if link is None:
            return False
        client.remote_view = link.host
        try:
            await self._link_write(link, {"t": "cmd", "action": "new_window"})
        except (OSError, ConnectionError):
            return False
        return True

    # ---- 원격 중첩 자동 승격(docs/internal/NESTED_ATTACH_SCENARIO.md) ----
    # 패널당 승격 요청 처리 최소 간격(초) — 출력 재생(cat/replay)·루프가 만드는
    # 중복/위조 REQ 의 연타를 완화한다(§7).
    _NEST_REQ_DEBOUNCE = 5.0

    def _nest_attach_request(self, pane, selfreport: str):
        """패널 출력의 NEST_ATTACH_REQ(원격 pytmux 가 중첩을 감지하고 보낸 승격
        요청) 처리 — serverpty._nest_dcs_handle 이 부른다(이벤트 루프 스레드).

        보안 원칙(시나리오 §7): 패널 출력은 신뢰 경계 밖이다 — attach 인자는 절대
        self-report 를 쓰지 않고 **ssh 래퍼가 기록한 pane._ssh_dest 만** 쓴다(위조
        REQ 의 최대 피해 = 이미 신뢰·접속한 호스트로의 원치 않는 시점 attach).
        ack 가 가면 원격 launcher 는 위임 안내 후 exit 0, 어떤 가드에서든 무 ack 면
        원격은 타임아웃 후 현행 거부 메시지로 폴백한다(열화 없음)."""
        if not getattr(self, "nest_auto_attach", True):
            return                                   # 기능 OFF(㉢) → 무 ack
        dest = getattr(pane, "_ssh_dest", "")
        if not dest or not _nest_host_match(dest, selfreport):
            return                                   # 목적지 미기록/2단 ssh 의심(㉣)
        now = time.monotonic()
        if now - getattr(pane, "_nest_req_ts", 0.0) < self._NEST_REQ_DEBOUNCE:
            return
        pane._nest_req_ts = now
        sess = self._nest_pane_session(pane)
        if sess is None:
            return                                   # 트리 밖 패널(팝업 등)
        if pane.pty is not None:
            try:
                pane.pty.write(sshwrap.NEST_ACK)     # "접수" — 결과는 notice
            except OSError:
                pass
        self.loop.create_task(self._nest_do_attach(sess, dest))

    def _nest_pane_session(self, pane):
        for sess in self.sessions.values():
            for tab in sess.tabs:
                if pane in tab.window.panes():
                    return sess
        return None

    async def _nest_do_attach(self, sess, dest: str):
        """승격 attach 본체: 처음 보는 호스트면 remote_attach 후 첫 업스트림 status
        (탭 병합)를 잠깐 기다려 이 세션의 클라들을 그 원격 active 탭으로 **한 번** 자동
        전환한다(㉢ ON 확정, 2026-06-13). dest 가 로컬 엔드포인트 형태("/"·"tcp:"
        시작)면 직결(같은 머신/테스트).

        **이미 병합된 호스트로의 재요청은 무시한다**(사용자 보고 2026-06-17): 출력
        재생(cat/replay)·스크롤백·셸 루프가 만든 중복 NEST_ATTACH_REQ 가 디바운스
        (_NEST_REQ_DEBOUNCE)를 넘겨 들어오면, 예전엔 '멱등(전환만)' 경로가 매번 모든
        클라를 원격 탭으로 끌어가 사용자가 로컬↔원격을 오가게 만들었다. 첫 승격 때
        이미 병합·전환했으므로 재요청은 사용자의 현재 활성 탭을 yank 하지 않는다."""
        remotes = self._remotes_dict()
        if dest in remotes:
            return
        # NEW-1: dest 가 endpoint 형태(/ · tcp:)인데 로컬(unix소켓·loopback)이 아니면
        # 직결을 거부한다 — 위조/조작된 _ssh_dest 의 tcp:원격호스트 직결로 임의
        # 아웃바운드 + 키입력 MITM 하던 경로 차단. (provenance 토큰이 _ssh_dest 위조를
        # 1차로 막지만, 사용자가 `ssh tcp:evil:9999` 를 치게 유도되는 경우까지 막는
        # 심층 방어. 정상 ssh 호스트는 endpoint 형태가 아니므로 영향 없다.)
        if dest.startswith(("/", "tcp:")) and not _nest_local_endpoint(dest):
            self._remote_notice(
                sess, "rnotice.attach_blocked_nest",
                "remote-attach {target} 거부(중첩 자동 승격) — 비로컬 endpoint "
                "직결은 차단됩니다(보안)", target=dest)
            return
        endpoint = dest if _nest_local_endpoint(dest) else None
        ok = await self.remote_attach(sess, host=None if endpoint else dest,
                                      endpoint=endpoint)
        if not ok:
            detail = self._err_detail("rerr.see_log", "서버 error.log 참조")
            self._remote_notice(
                sess, "rnotice.attach_fail_nest",
                "remote-attach {target} 실패(중첩 자동 승격) — {why}",
                detail=detail, target=dest)
            return
        self._remote_status_broadcast()
        link = remotes.get(dest)
        if link is None:
            return
        # 첫 업스트림 status 가 와야 병합 전역 index 가 생긴다(전환 가능 조건).
        await self._remote_wait_first_status(link)
        self._remote_notice(
            sess, "rnotice.attach_merged_nest",
            "remote-attach {target}: 원격 탭 병합됨(중첩 자동 승격)", target=dest)
        if not link.windows:
            return
        # 병합 전역 index = 로컬 탭 수 + 앞선 링크들의 탭 수 + 업스트림 active 위치
        # (_remote_tabs/_remote_tab_at 과 같은 dict 순회 순서라 일관).
        gi = len(sess.tabs)
        for l in remotes.values():
            if l is link:
                break
            gi += len(self._visible_windows(l))
        vis = self._visible_windows(link)
        act = next((vi for vi, (_, w) in enumerate(vis) if w.get("active")), 0)
        for c in [c for c in self.clients if c.session is sess]:
            try:
                await self.remote_select_window(c, sess, gi + act)
            except (OSError, ConnectionError):
                pass
        self._remote_status_broadcast()
