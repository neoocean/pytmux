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

MVP 한계(후속 Stage 3): 원격 탭 active 하이라이트는 클라별 차이를 status 공유 방송에
못 실어 생략, 끊김 시 자동 재연결 없음(탭 제거 + 재-attach 수동), 재시작(re-exec) 후
링크 미복원, Windows 미지원(stdio-proxy POSIX), 원격 status 의 Claude 헤더 등 부가
필드는 미전달(화면 자체는 보임).
"""
from __future__ import annotations

import asyncio
import os

from . import ipc
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


# 원격 탭을 보는 동안 업스트림으로 릴레이하는 cmd action 화이트리스트.
# 입력/스크롤/리사이즈는 별도 경로(메시지 타입)로 릴레이한다. 파괴적(kill_*)·로컬
# 제어(restart/kill-server/remote_*)는 의도적으로 제외 — 로컬에서 처리되거나 무시.
_REMOTE_RELAY_ACTIONS = {
    "select_pane_id", "select_pane", "zoom", "next_window", "prev_window",
    "resize", "resize_dir", "scroll_to_prompt", "request_prompt_segment",
}


class ServerRemoteMixin:
    # Server.__init__ 가 부르지 않아도 동작하도록 지연 초기화 헬퍼.
    def _remotes_dict(self) -> dict:
        d = getattr(self, "_remotes", None)
        if d is None:
            d = self._remotes = {}
        return d

    # ---- 전송 열기 ----
    async def _remote_transport(self, host: str | None, endpoint: str | None):
        """(reader, writer, token, proc) 를 연다. endpoint=같은 머신 직결(테스트/로컬
        페더레이션), host=`ssh -T host pytmux stdio-proxy`(첫 줄 TOKEN)."""
        if endpoint:
            reader, writer = await ipc.open_connection(endpoint)
            return reader, writer, (ipc.read_token(endpoint) or ""), None
        # BatchMode: 서버가 띄우는 ssh 는 TTY 가 없어 비밀번호를 못 묻는다 — 키
        # 인증 미설정이면 즉시 명확한 stderr(Permission denied)로 실패하게 한다.
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-T", "-o", "BatchMode=yes", host, "pytmux", "stdio-proxy",
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
        remotes = self._remotes_dict()
        if name in remotes:
            await self.remote_drop(remotes[name], notify=False)
        self._remote_last_err = ""
        try:
            reader, writer, tok, proc = await self._remote_transport(
                host, endpoint)
        except (OSError, ConnectionError, asyncio.TimeoutError) as e:
            self._remote_last_err = str(e) or type(e).__name__
            self._log_error(f"remote_attach({name})")
            return False
        link = RemoteLink(name, reader, writer, proc)
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

    async def remote_drop(self, link: RemoteLink, notify: bool = True):
        """링크 해제: 보던 클라는 로컬 화면으로 복귀, 탭바에서 원격 탭 제거."""
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

    def remote_detach(self, name: str | None = None):
        """이름 지정(없으면 전부) 링크 해제 — `remote-detach [host]`."""
        remotes = self._remotes_dict()
        targets = ([remotes[name]] if name and name in remotes
                   else list(remotes.values()) if not name else [])
        for link in targets:
            asyncio.create_task(self.remote_drop(link))
        return bool(targets)

    # ---- 업스트림 수신 ----
    async def _remote_reader(self, link: RemoteLink):
        """업스트림 메시지 루프: status 는 흡수(탭바 병합), bye/EOF 는 링크 해제,
        그 외(layout/screen/screen-delta/prompt_segment 등)는 이 링크를 **보는**
        클라에 그대로 전달한다."""
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
                    wins = msg.get("windows", [])
                    if wins != link.windows:
                        link.windows = wins
                        self._remote_status_broadcast()
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
            if link.alive:                 # EOF/오류로 끝났으면 정리+복귀
                await self.remote_drop(link)

    # ---- 탭바 병합 ----
    def _remote_tabs(self, base: int) -> list:
        """병합 탭 목록에 덧붙일 원격 탭 엔트리(전역 index 는 base 부터 연속).
        active 는 클라별 상태라 공유 status 에 못 실어 False(MVP 한계)."""
        out = []
        gi = base
        for link in self._remotes_dict().values():
            for rw in link.windows:
                out.append({"index": gi, "name": f"⇄{link.host}:{rw.get('name', '')}",
                            "active": False,
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
        """원격 탭 목록 변동을 모든 세션 클라의 탭바에 반영(가벼운 status 재전송)."""
        for sess in self.sessions.values():
            clients = [c for c in self.clients if c.session is sess]
            if not clients:
                continue
            frame = frame_msg(self._status_msg(sess, full=False))
            for c in clients:
                asyncio.create_task(write_frames(c.writer, [frame]))

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
