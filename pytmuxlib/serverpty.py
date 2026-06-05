"""PTY/패널 생성·자식 셸 fork·출력 feed(폭주 드레인·슬라이스 ingest)·EOF 처리
서버 로직 믹스인. `server.Server` 가 상속한다(§10 LLM 친화 리팩토링). 동작 불변 —
self.* 상태와 Server 의 다른 메서드(_remove_pane_from_tree·_capture_write 등)를 참조."""
from __future__ import annotations

import asyncio
import gc
import os

from . import ipc, proc, pty_backend, sshwrap
from .model import Pane, Session, coalesce_alt_repaints
from .protocol import FEED_SLICE, MIN_H, MIN_W


class ServerPtyMixin:
    # ---- PTY/패널 생성 ----
    def _fork_shell(self, cols: int, rows: int, cwd: str | None = None,
                    cmd: str | None = None):
        """셸을 PTY 백엔드(pty_backend)에 띄우고 PtyProcess 핸들을 반환.

        OS 별 PTY/프로세스 분기는 전부 pty_backend 가 담당한다(Unix=pty.fork,
        Windows=ConPTY). CLOEXEC·winsize·논블로킹 설정도 백엔드 spawn 안에서 처리.

        cmd 가 주어지면 인터랙티브 셸 대신 `셸 -c <cmd>` 로 그 명령만 실행한다
        (display-popup 라이브 PTY 용). 명령이 끝나면 셸이 종료→PTY EOF→팝업 자동 닫힘.
        """
        cols = max(MIN_W, cols)
        rows = max(MIN_H, rows)
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env["PYTMUX"] = self.resolved_endpoint
        env.pop("LINES", None)
        env.pop("COLUMNS", None)
        # 원격(ssh) 중첩 거부: 표식(LC_PYTMUX) + ssh 래퍼를 패널 셸에 주입해 ssh 로
        # 들어간 원격에서도 pytmux 중첩이 막히게 한다(docs/HANDOFF.md §10). 로컬
        # 중첩은 위 $PYTMUX 가 담당한다.
        sshwrap.panel_env(env, ipc.default_state_dir())
        if pty_backend.IS_WINDOWS:
            shell = env.get("PYTMUX_SHELL") or env.get("COMSPEC") or "cmd.exe"
            argv = [shell, "/c", cmd] if cmd else [shell]
        else:
            shell = env.get("SHELL", "/bin/sh")
            argv = [shell, "-c", cmd] if cmd else [shell]
        return pty_backend.spawn(argv, cols=cols, rows=rows, cwd=cwd, env=env)

    def _attach_reader(self, pane: Pane) -> None:
        """패널 PTY 의 읽기를 시작한다(on_data/on_eof 는 이벤트 루프 스레드에서 호출)."""
        self._stop_pane_feed(pane)   # 재attach 시 이전 드레인 잔여 상태 초기화
        pane.pty.start_reader(
            self.loop,
            lambda d, p=pane: self._on_pane_data(p, d),
            lambda p=pane: self._pane_eof(p))

    def spawn_pane(self, cols: int, rows: int, cwd: str | None = None,
                   cmd: str | None = None) -> Pane:
        proc = self._fork_shell(cols, rows, cwd, cmd)
        fd = proc.fileno() if hasattr(proc, "fileno") else -1
        pane = Pane(proc.pid, fd, max(MIN_W, cols), max(MIN_H, rows))
        pane.pty = proc
        self._attach_reader(pane)
        return pane

    def respawn_pane(self, sess: Session):
        """활성 패널의 셸을 종료하고 같은 슬롯에서 새 셸을 띄운다."""
        win = sess.active_window
        if not win or not win.active_pane:
            return
        pane = win.active_pane
        cwd = self._pane_cwd(pane)
        self._stop_pane_feed(pane)          # 진행 중 드레인 취소(새 셸로 교체 전)
        if pane.pty is not None:
            pane.pty.stop_reader()
            pane.pty.kill()                 # SIGKILL 즉시 종료
            pane.pty.close()
            pane.pty.reap(block=True)       # SIGKILL 이므로 블로킹 회수 안전
        proc = self._fork_shell(pane.cols, pane.rows, cwd)
        fd = proc.fileno() if hasattr(proc, "fileno") else -1
        pane.reinit(proc.pid, fd, pane.cols, pane.rows)
        pane.pty = proc
        self._attach_reader(pane)

    def _on_pane_data(self, pane: Pane, data: bytes):
        # PTY 읽기/EOF/EIO 처리는 pty_backend 가 담당하고, 여기엔 수신 바이트 처리만
        # 남는다(backend 가 on_data 를 이벤트 루프 스레드에서 부른다).
        #
        # 대량 출력 비차단 처리: pyte feed 는 순수 파이썬이라 64KB 한 읽기를 통째로
        # 먹이면 ~50ms 동안 이벤트 루프가 막혀 입력·flush 가 지연된다. 그래서 버스트는
        # reader 를 잠깐 떼고(커널 PTY 백프레셔 유지 → 데이터 손실/메모리 폭증 없음)
        # FEED_SLICE 단위로 쪼개 먹이며 슬라이스마다 루프에 양보한다(_feed_drain).
        # 소량(대화형 에코 등)은 인라인 즉시 처리해 기존 경로와 동일하게 둔다.
        if pane._feed_task is not None:
            # 이미 드레인 중 → 큐에 이어 붙이면 실행 중 태스크가 소비한다(POSIX 는
            # reader 가 멈춰 여기 거의 안 옴; pause 가 no-op 인 백엔드의 버스트 대비).
            pane._feedbuf += data
            self._coalesce_feed(pane)
            return
        if len(data) <= FEED_SLICE:
            self._ingest_slice(pane, data)
            return
        pane._feedbuf += data
        self._coalesce_feed(pane)
        if pane.pty is not None:
            pane.pty.pause_reader()
        pane._feed_task = self.loop.create_task(self._feed_drain(pane))

    def _coalesce_feed(self, pane: Pane) -> None:
        """대기 중인 feedbuf 에서 무효화된 alt-screen 리페인트 프레임을 합쳐 pyte feed
        부하를 줄인다(§10 대응 ②). 옵션이 꺼져 있거나 안전 조건이 안 맞으면 no-op."""
        if not self.coalesce_repaints or not pane._feedbuf:
            return
        pane._feedbuf = coalesce_alt_repaints(pane._feedbuf, pane.alt_active)

    def _gc_drain_enter(self) -> None:
        """드레인 진입: 첫 드레인이면 순환 GC 를 끈다(중첩은 깊이만 +1)."""
        if self._gc_drain_depth == 0:
            self._gc_was_enabled = gc.isenabled()
            if self._gc_was_enabled:
                gc.disable()
        self._gc_drain_depth += 1

    def _gc_drain_exit(self) -> None:
        """드레인 종료: 마지막 드레인이면 GC 를 (원래 켜져 있었으면) 다시 켜고 1회
        collect 해 드레인 창에서 미룬 회수를 즉시 처리한다."""
        if self._gc_drain_depth > 0:
            self._gc_drain_depth -= 1
        if self._gc_drain_depth == 0 and self._gc_was_enabled:
            gc.enable()
            gc.collect()

    async def _feed_drain(self, pane: Pane):
        """버스트 바이트를 FEED_SLICE 단위로 먹이며 슬라이스마다 이벤트 루프에 양보.
        모두 비우면 reader 를 재개해 다음 배치를 읽는다(취소 시엔 재개 안 함).

        드레인이 도는 동안은 순환 GC 를 꺼 둔다(_gc_drain_enter/exit, §10) — 슬라이스
        중간 GC 일시정지로 인한 입력 끊김을 없앤다."""
        cancelled = False
        self._gc_drain_enter()
        try:
            while pane._feedbuf:
                n = min(FEED_SLICE, len(pane._feedbuf))
                chunk, pane._feedbuf = pane._feedbuf[:n], pane._feedbuf[n:]
                self._ingest_slice(pane, chunk)
                await asyncio.sleep(0)   # 양보: 입력/flush/render 가 끼어든다
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            pane._feed_task = None
            self._gc_drain_exit()
            if not cancelled and pane.pty is not None:
                try:
                    pane.pty.resume_reader()
                except Exception:
                    pass

    def _stop_pane_feed(self, pane: Pane):
        """진행 중인 드레인 태스크를 취소하고 대기 버퍼를 비운다(패널 teardown/재attach)."""
        t = pane._feed_task
        if t is not None and not t.done():
            t.cancel()
        pane._feed_task = None
        pane._feedbuf = b""

    def _ingest_slice(self, pane: Pane, data: bytes):
        """수신 바이트 한 조각을 실제로 처리한다(feed + 활동/모드 스캔). _on_pane_data
        (소량 인라인)와 _feed_drain(버스트 슬라이스) 양쪽에서 호출된다."""
        if self.capture:
            self._capture_write(pane, data)
        pane.feed(data)
        pane._activity = True
        if b"\x07" in data:
            pane._bell = True
        # bracketed paste 모드 추적(내부 앱의 DECSET 2004)
        if b"\x1b[?2004h" in data:
            pane.bracketed = True
        if b"\x1b[?2004l" in data:
            pane.bracketed = False
        # 마우스 트래킹 모드 추적(DECSET 1000/1002/1003/1006). 바뀌면 클라이언트가
        # 패스스루 여부를 알도록 레이아웃(패널별 mouse 플래그 포함)을 다시 보낸다.
        if pane.update_mouse_modes(data):
            sess = self._session_of_pane(pane)
            if sess:
                self._broadcast_session(sess)
        # pipe-pane: 패널 출력을 외부 명령으로 복제
        if pane.pipe_proc and pane.pipe_proc.stdin:
            try:
                pane.pipe_proc.stdin.write(data)
                pane.pipe_proc.stdin.flush()
            except Exception:
                pane.pipe_proc = None
        if pane.autoresume and not pane._resume_pending:
            self._maybe_schedule_resume(pane, data.decode("utf-8", "ignore"))


    def _pane_eof(self, pane: Pane):
        self._stop_pane_feed(pane)   # 진행 중 드레인 취소(정상 EOF 면 이미 빈 상태)
        if pane.pipe_proc:
            try:
                pane.pipe_proc.stdin.close()
            except Exception:
                pass
            pane.pipe_proc = None
        if pane.pty is not None:
            pane.pty.stop_reader()
            pane.pty.close()
            pane.pty.reap(block=False)
        # 라이브 PTY 팝업 패널은 트리에 없으므로(_remove_pane_from_tree 가 못 찾음)
        # 팝업으로 닫는다 — 명령이 끝나면 PTY EOF 로 여기 들어와 자동으로 사라진다.
        for sess in list(self.sessions.values()):
            if sess.popup and sess.popup.get("pane") is pane:
                sess.popup = None
                self._broadcast_session(sess)
                return
        self._remove_pane_from_tree(pane)
