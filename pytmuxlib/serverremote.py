"""원격 pytmux 어태치 페더레이션(§1.7 Stage 2) — 로컬 서버가 원격 서버의 **업스트림
클라이언트**가 되어 원격 세션의 탭/패널을 로컬 탭바에 '원격 탭'으로 흡수한다.

설계(docs/REMOTE_ATTACH_SCENARIO.md §4): 와이어 프로토콜을 그대로 재사용한다 — 원격
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
import os
import time

from . import ipc, sshwrap
from .protocol import PROTO_VERSION, frame_msg, read_msg, write_frames, write_msg


class RemoteLink:
    """업스트림(원격 서버) 연결 1개의 상태."""

    def __init__(self, host: str, reader, writer, proc=None):
        self.host = host          # 표시/식별용 이름(ssh 호스트 또는 endpoint)
        self.reader = reader
        self.writer = writer
        self.proc = proc          # ssh 서브프로세스(직결이면 None)
        self.windows: list = []   # 업스트림 status 의 windows(탭 목록) 최신본
        self.task: asyncio.Task | None = None
        self.alive = True
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
}

# §1.7-c 원격 탭을 보는 동안 거부하는 경계 횡단 조작(notice 회신). 로컬 트리에
# 조용히 실행되지도, 업스트림으로 릴레이되지도 않는다 — 원격 탭은 원격 패널만,
# 로컬 탭은 로컬 패널만(섞기 금지).
_REMOTE_BLOCK_ACTIONS = {
    "break_pane", "join_pane", "move_pane_to_tab", "kill_window",
    "move_tab", "move_window", "swap_window", "move_current_tab",
}


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


class ServerRemoteMixin:
    # 끊김(비명시) 시 자동 재연결 백오프(초). 무상한이 아니다 — §1 의 "재접속 루프"
    # 재발을 막기 위해 유한 회수 후 포기(notice)하고 수동 재시도에 맡긴다.
    _RECONNECT_DELAYS = (1, 2, 4, 8, 16, 30, 30, 30)

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
        # BatchMode: 서버가 띄우는 ssh 는 TTY 가 없어 비밀번호를 못 묻는다 — 키
        # 인증 미설정이면 즉시 명확한 stderr(Permission denied)로 실패하게 한다.
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-T", "-o", "BatchMode=yes", "--",
            host, "pytmux", "stdio-proxy",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
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
            proc.kill()
            detail = (err or line).decode("utf-8", "replace").strip()
            detail = detail.splitlines()[-1] if detail else "응답 없음"
            if "Permission denied" in detail:
                # 비대화 ssh(BatchMode)는 비밀번호를 못 묻는다 — 패스워드 전용
                # 호스트는 ControlMaster 로 인증된 연결을 공유하면 된다(§5).
                detail += (" — 키 미설정. 패스워드 호스트는 ssh config 에 "
                           "ControlMaster 설정 후 패널에서 한 번 로그인"
                           "(REMOTE_ATTACH_SCENARIO §5)")
            raise ConnectionError(f"stdio-proxy 핸드셰이크 실패: {detail}")
        tok = line.split(b" ", 1)[1].strip().decode()
        return proc.stdout, proc.stdin, tok, proc

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
            self._remote_last_err = "자기 자신에는 attach 할 수 없습니다"
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
        self._remote_last_err = ""
        try:
            reader, writer, tok, proc = await self._remote_transport(
                host, endpoint)
        except (OSError, ConnectionError, asyncio.TimeoutError) as e:
            self._remote_last_err = str(e) or type(e).__name__
            self._log_error(f"remote_attach({name})")
            return False
        link = RemoteLink(name, reader, writer, proc)
        link.spec = {"host": host, "endpoint": endpoint}
        link.sess = sess
        cols, rows = self._session_size(sess)
        hello = {"t": "hello", "proto": PROTO_VERSION,
                 "cols": cols, "rows": rows}
        if tok:
            hello["token"] = tok
        try:
            await write_msg(writer, hello)
        except (OSError, ConnectionError) as e:
            self._remote_last_err = f"hello 실패: {e}"
            self._log_error(f"remote_attach hello({name})")
            return False
        remotes[name] = link
        link.task = self.loop.create_task(self._remote_reader(link))
        return True

    async def remote_drop(self, link: RemoteLink, notify: bool = True,
                          reconnect: bool = False):
        """링크 해제: 보던 클라는 로컬 화면으로 복귀, 탭바에서 원격 탭 제거.
        reconnect=True(비명시적 죽음 — EOF/오류)면 백오프 자동 재연결을 건다."""
        link.alive = False
        self._remotes_dict().pop(link.host, None)
        if link.task is not None and not link.task.done():
            link.task.cancel()
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
        remotes = self._remotes_dict()
        targets = ([remotes[name]] if name and name in remotes
                   else list(remotes.values()) if not name else [])
        for link in targets:
            asyncio.create_task(self.remote_drop(link))
        return bool(targets)

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
                    self._remote_status_broadcast()
                    self._remote_notice(
                        sess, f"remote-attach {name}: 끊김 후 자동 재연결됨"
                              f"(시도 {i})")
                    return
            self._remote_notice(
                sess, f"remote-attach {name}: 자동 재연결 포기"
                      f"({len(self._RECONNECT_DELAYS)}회) — "
                      f":remote-attach 로 수동 재시도")
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
            for closer in (lambda l=link: l.writer.close(),
                           lambda l=link: l.proc and l.proc.kill()):
                try:
                    closer()
                except (OSError, ProcessLookupError):
                    pass
        self._remotes_dict().clear()

    def _remote_notice(self, sess, text: str):
        """세션의 모든 클라에 상태줄 notice(§1.7 attach 결과와 동일 표면)."""
        for c in list(self.clients):
            if c.session is sess:
                asyncio.create_task(
                    write_msg(c.writer, {"t": "notice", "text": text}))

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
                        self._remote_status_broadcast()
                    else:
                        self._remote_notice(
                            sess, f"remote-attach {name}: 재시작 후 복원 실패 — "
                                  f"{getattr(self, '_remote_last_err', '')}")
                except Exception:
                    self._log_error(f"remote_restore({name})")

        self.loop.create_task(_restore())

    # ---- 업스트림 수신 ----
    async def _remote_reader(self, link: RemoteLink):
        """업스트림 메시지 루프: status 는 흡수(탭바 병합), bye/EOF 는 링크 해제,
        그 외(layout/screen/screen-delta 등)는 이 링크를 **보는** 클라에 그대로
        전달한다."""
        try:
            while link.alive:
                try:
                    msg = await read_msg(link.reader)
                except (OSError, ConnectionError, asyncio.IncompleteReadError):
                    msg = None
                if msg is None:
                    break
                t = msg.get("t")
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
                if t in ("bye", "restarting"):
                    break
                frame = frame_msg(msg)
                for c in list(self.clients):
                    if c.remote_view == link.host:
                        try:
                            await write_frames(c.writer, [frame])
                        except (OSError, ConnectionError):
                            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log_error(f"remote_reader({link.host})")
        finally:
            if link.alive:                 # EOF/오류로 끝났으면 정리+복귀+자동재연결
                await self.remote_drop(link, reconnect=True)

    # ---- 탭바 병합 ----
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
            for rw in link.windows:
                out.append({"index": gi, "name": f"⇄{link.host}:{rw.get('name', '')}",
                            "active": bool(viewing and rw.get("active")),
                            "remote": True,
                            "bell": rw.get("bell", False),
                            "activity": rw.get("activity", False),
                            "claude_done": rw.get("claude_done", False)})
                gi += 1
        return out

    def _remote_tab_at(self, sess, index: int):
        """병합 전역 index(>= len(sess.tabs)) → (link, 원격 탭 index). 없으면 None."""
        gi = len(sess.tabs)
        for link in self._remotes_dict().values():
            for ri, _ in enumerate(link.windows):
                if gi == index:
                    return link, ri
                gi += 1
        return None

    def _remote_status_broadcast(self):
        """원격 탭 목록 변동을 모든 세션 클라의 탭바에 반영. status 는 클라별
        (Stage 3 — 보는 클라=업스트림 오버라이드, 그 외=로컬)로 조립한다."""
        for sess in self.sessions.values():
            for c in [c for c in self.clients if c.session is sess]:
                frame = frame_msg(self._status_msg(sess, full=False, client=c))
                asyncio.create_task(write_frames(c.writer, [frame]))

    def _remote_viewer_status(self, link: RemoteLink):
        """이 링크를 보는 클라에게만 status 재전송(업스트림 부가필드 갱신 반영)."""
        for c in list(self.clients):
            if c.remote_view == link.host and c.session is not None:
                frame = frame_msg(
                    self._status_msg(c.session, full=False, client=c))
                asyncio.create_task(write_frames(c.writer, [frame]))

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
             "bell": t.has_bell, "activity": t.has_activity,
             "claude_done": t.has_claude_done}
            for t in sess.tabs] + self._remote_tabs(len(sess.tabs), client)
        return msg

    # ---- 릴레이 ----
    def _remote_link_for(self, client) -> RemoteLink | None:
        if not getattr(client, "remote_view", None):
            return None
        return self._remotes_dict().get(client.remote_view)

    def remote_relay(self, client, msg) -> bool:
        """보는 중인 클라의 메시지를 업스트림으로 그대로 전달. 링크가 없으면(죽음
        직후 레이스) False — 호출부가 로컬 폴백."""
        link = self._remote_link_for(client)
        if link is None:
            client.remote_view = None
            return False
        try:
            asyncio.create_task(write_msg(link.writer, msg))
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
        await write_msg(link.writer,
                        {"t": "cmd", "action": "select_window", "index": ri})
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
            await write_msg(link.writer, {"t": "cmd", "action": "new_window"})
        except (OSError, ConnectionError):
            return False
        return True

    # ---- 원격 중첩 자동 승격(docs/NESTED_ATTACH_SCENARIO.md) ----
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
        """승격 attach 본체: 같은 이름 링크가 있으면 멱등(재연결 없이 전환만),
        없으면 remote_attach 후 첫 업스트림 status(탭 병합)를 잠깐 기다려 이 세션의
        클라들을 그 원격 active 탭으로 자동 전환한다(㉢ ON 확정, 2026-06-13).
        dest 가 로컬 엔드포인트 형태("/"·"tcp:" 시작)면 직결(같은 머신/테스트)."""
        remotes = self._remotes_dict()
        fresh = dest not in remotes
        if fresh:
            endpoint = dest if dest.startswith(("/", "tcp:")) else None
            ok = await self.remote_attach(sess, host=None if endpoint else dest,
                                          endpoint=endpoint)
            if not ok:
                self._remote_notice(
                    sess, f"remote-attach {dest} 실패(중첩 자동 승격) — "
                          f"{getattr(self, '_remote_last_err', '') or '서버 error.log 참조'}")
                return
            self._remote_status_broadcast()
        link = remotes.get(dest)
        if link is None:
            return
        # 첫 업스트림 status 가 와야 병합 전역 index 가 생긴다(전환 가능 조건).
        for _ in range(30):
            if link.windows or not link.alive:
                break
            await asyncio.sleep(0.1)
        if fresh:
            self._remote_notice(
                sess, f"remote-attach {dest}: 원격 탭 병합됨(중첩 자동 승격)")
        if not link.windows:
            return
        # 병합 전역 index = 로컬 탭 수 + 앞선 링크들의 탭 수 + 업스트림 active 위치
        # (_remote_tabs/_remote_tab_at 과 같은 dict 순회 순서라 일관).
        gi = len(sess.tabs)
        for l in remotes.values():
            if l is link:
                break
            gi += len(l.windows)
        act = next((i for i, w in enumerate(link.windows) if w.get("active")), 0)
        for c in [c for c in self.clients if c.session is sess]:
            try:
                await self.remote_select_window(c, sess, gi + act)
            except (OSError, ConnectionError):
                pass
        self._remote_status_broadcast()
