"""셸 PTY 를 소유하는 백그라운드 서버(데몬)."""
from __future__ import annotations

import asyncio
import base64
import gc
import json
import os
import shlex
import subprocess
import time

from . import ipc, proc, pty_backend, sshwrap, tokens, usagelog
from .model import (ClientConn, Pane, Session, Split, Tab, Window,
                    coalesce_alt_repaints)
from .claude import (claude_account, claude_perm_mode, claude_prompt,
                     claude_state, claude_usage, parse_reset_delay)
from .protocol import (FEED_SLICE, FLUSH_HZ, MIN_H, MIN_W, read_msg, write_msg)

# pytmux 프로젝트 루트(= pytmuxlib 패키지의 상위). 캡처 출력 등 "프로젝트에 영속해
# Perforce 로 공유할" 산출물의 기준 경로다. proc.server_argv 의 entry 추정과 동일 규칙.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Claude 헤더 행 예약(#1)을 풀기 전, 패널이 연속으로 non-Claude 로 보여야 하는
# 스캔(=flush) 횟수. `_claude` 는 화면 스크래핑이라 footer 가 한두 프레임 안 잡혀
# None 으로 깜빡이는데(특히 ssh/ConPTY 처럼 화면이 조각나 도착할 때), 그때마다
# 예약을 풀면 PTY 가 ±1 행으로 리사이즈를 반복해 원격 Claude 화면이 한 줄씩 위아래로
# 스크롤되는 떨림이 난다. 그래서 예약 해제(=PTY 한 행 키우기)만 디바운스한다(설정
# 은 즉시). 30Hz flush 기준 ~1초 — 진짜 Claude 종료 시 행을 되찾는 지연은 미미하다.
_HDR_CLAUDE_MISS = 30

# 권한모드 자동 오토모드 전환(§10): 한 번 idle 진입 후 auto 에 도달하지 못해도 이
# 횟수까지만 shift+tab 을 보낸다(footer 순환 순서가 고정이 아닐 수 있어 폐루프지만,
# 오검출 시 무한 순환을 막는 가드). default↔auto↔plan(↔bypass) 순환을 덮을 만큼.
_CAM_MAX = 4

# 비활성 탭 Claude 완료 알림(#22) 플리커 방지(§10 #18): busy→idle 후 idle 이 연속
# 이만큼의 스캔 프레임 동안 안정돼야 "완료"로 친다. raw busy↔idle 깜빡임(footer 가
# 한 프레임 안 잡혀 idle 로 보였다 다시 busy)에 done 이 잘못 서서 탭이 잠깐 녹색이
# 되는 것을 막는다. 30Hz flush 기준 ~0.1초 — 진짜 완료 알림 지연은 미미하다.
_DONE_IDLE_FRAMES = 3


class Server:
    def __init__(self, sock_path: str, resume_path: str | None = None):
        self.sock_path = sock_path
        # 작업 보존 재시작(re-exec) 후 부트: 이 경로의 상태 파일로 상속된 PTY 를
        # 채택해 셸을 살린 채 복원한다(serve()). None 이면 평소 부트.
        self._resume_path = resume_path
        # 패널 셸 $PYTMUX 에 심을 엔드포인트. serve() 가 listen 을 시작하면 TCP
        # 에페메럴(포트 0)은 확정 포트로 갱신된다. 바인드 전(테스트 등)엔 입력값 그대로.
        self.resolved_endpoint = sock_path
        self.sessions: dict[str, Session] = {}
        self.clients: list[ClientConn] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.running = True
        # 대량 출력 드레인 중 순환 GC 일시정지 가드(§10 프로파일링): pyte feed 는 셀마다
        # `Char` 네임드튜플을 새로 할당해 버스트 한 번에 수백만 객체가 생긴다. 순환 GC 가
        # 이를 주기적으로 훑으면 단일 슬라이스가 30~85ms 멈춰(측정) 이벤트 루프가 끊기고
        # 입력이 뚝뚝 끊긴다. drain 이 도는 동안만 GC 를 끄고(_gc_drain_depth 0→1) 모든
        # drain 이 끝나면 다시 켜 1회 collect 한다(1→0). Char 는 불변값이라 순환이 없어
        # refcount 만으로 회수되므로 드레인 창 동안 누수 위험은 낮다.
        self._gc_drain_depth = 0
        self._gc_was_enabled = True
        self._session_seq = 0
        # Claude 세션 일련번호(#7 토큰 로깅): 패널의 claude None→비None 전이마다 +1.
        self._claude_session_seq = 0
        self.buffers: list[str] = []   # 페이스트 버퍼(최신이 앞)
        # 패널 출력 캡처(Claude 화면 문구 분석용). 기본 ON, opts.json 에 영속.
        self._capfiles: dict[int, "object"] = {}   # pane.id -> 열린 바이너리 파일
        _opts = self._load_opts()
        self.capture = bool(_opts.get("capture", True))
        # Claude 프롬프트 헤더 전역 표시(#6 ③ opts.json 영속). 클라가 status 로 받아
        # claude_header_on 에 반영하고, `claude-header on|off` 가 서버를 거쳐 갱신·영속.
        self.claude_header = bool(_opts.get("claude_header", True))
        # 패널이 하나뿐일 때 테두리(아웃라인)를 그릴지(기본 ON=항상 테두리).
        # off 면 단일 패널은 테두리 없이 화면 전체를 내용으로 쓴다. opts.json 영속.
        self.single_border = bool(_opts.get("single_border", True))
        # alt-screen 풀스크린 리페인트 코얼레싱(#§10 대응 ②). 켜면 Claude busy 스피너
        # 등 매 프레임 화면을 통째로 다시 그리는 대량 출력이 feed 보다 빠르게 쌓일 때
        # 무효화된 중간 프레임을 버려 feed 부하/지연을 줄인다(안전 조건은
        # coalesce_alt_repaints 참조 — alt-screen 한정·무손실). 기본 ON, opts.json 영속.
        self.coalesce_repaints = bool(_opts.get("coalesce_repaints", True))
        # 프롬프트 단위 클리어 모드(#9)의 ① 문서화 지시문. 패널 안 Claude 에게 보내는
        # 슬래시/지시문이며(pytmux 명령 아님), 무엇을 어디에 기록할지는 Claude 쪽
        # 프로젝트 관례(CLAUDE.md/메모리)에 맡긴다. opts.json 영속.
        self.prompt_clear_message = str(_opts.get(
            "prompt_clear_message",
            "이번 세션에서 얻은 정보·결정을 프로젝트 문서(CLAUDE.md/메모리)에 기록해줘."))
        # 자동 doc→/clear(§10): Claude 가 작업을 끝내고(busy→idle) 그 상태로
        # auto_doc_clear_delay 초 지속되면(사용자 개입 없이) prompt_clear_message →
        # /clear 를 1회 자동 주입한다. 기본 OFF(명시 토글 필요). limit 상태/사용자
        # 입력/재busy 시엔 발화하지 않는다. opts.json 영속.
        self.auto_doc_clear = bool(_opts.get("auto_doc_clear", False))
        self.auto_doc_clear_delay = float(_opts.get("auto_doc_clear_delay", 30.0))
        # 권한모드 자동 오토모드 전환(§10): Claude 패널이 idle 이고 권한모드 footer 가
        # auto(자동 수락)가 아니면 shift+tab 을 순환 주입해 auto 로 맞춘다. 기본 OFF.
        # bypass(권한 우회) 모드는 명시적·위험 설정이라 건드리지 않는다. opts.json 영속.
        self.claude_auto_mode = bool(_opts.get("claude_auto_mode", False))

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

    # ---- 토큰 리밋 자동 재개 ----
    def _maybe_schedule_resume(self, pane: Pane, newtext: str):
        pane._scanbuf = (pane._scanbuf + newtext)[-4000:]
        if pane._resume_pending:
            return
        delay = parse_reset_delay(pane._scanbuf)
        if delay is not None:
            pane._resume_pending = True
            # 해제 시각 +5초 버퍼 후 재개 메시지 입력
            self.loop.call_later(delay + 5, self._fire_resume, pane)

    def _fire_resume(self, pane: Pane):
        pane._resume_pending = False
        pane._scanbuf = ""
        if pane.pty is not None:
            try:
                pane.pty.write((pane.resume_msg + "\r").encode("utf-8"))
            except OSError:
                pass

    def set_autoresume(self, sess: Session, value=None, msg: str | None = None):
        win = sess.active_window
        if not win or not win.active_pane:
            return
        p = win.active_pane
        if msg is not None:
            p.resume_msg = msg
            return
        p.autoresume = (not p.autoresume) if value is None else bool(value)
        if p.autoresume and not p._resume_pending:
            # 켜는 순간 이미 화면에 떠 있는 리밋 안내도 즉시 검사
            rows, _ = p.render(False)
            text = "\n".join("".join(s[0] for s in r) for r in rows)
            self._maybe_schedule_resume(p, text)

    # ---- 프롬프트 단위 클리어 모드(#9) ----
    def set_prompt_clear(self, sess: Session, value=None):
        """활성 패널의 프롬프트 단위 클리어 모드 토글. value 미지정 시 반전.
        끄면 진행 중인 상태기계도 리셋한다(다음 프롬프트는 평소대로)."""
        win = sess.active_window
        if not win or not win.active_pane:
            return None
        p = win.active_pane
        p.prompt_clear_mode = (not p.prompt_clear_mode) if value is None \
            else bool(value)
        if not p.prompt_clear_mode:
            p._pc_phase = None
            p.prompt_clear_queue.clear()   # 모드 끄면 쌓인 큐도 비운다(#4)
        return p.prompt_clear_mode

    def pc_queue_add(self, sess: Session, cmd: str):
        """활성 패널의 프롬프트 단위 클리어 큐에 명령을 쌓는다(#4). 각 명령은
        doc+/clear 사이클을 마칠 때마다 하나씩 Claude 에 투입된다. 큐잉은 이
        워크플로를 함의하므로 모드가 꺼져 있으면 켠다. 패널이 한가하고 진행 중인
        시퀀스가 없으면 곧장 첫 명령을 투입해 사이클을 시작한다."""
        win = sess.active_window
        if not win or not win.active_pane:
            return None
        p = win.active_pane
        cmd = (cmd or "").strip()
        if not cmd:
            return None
        p.prompt_clear_queue.append(cmd)
        if not p.prompt_clear_mode:
            p.prompt_clear_mode = True
        if p._pc_phase is None and p._claude == "idle":
            self._pc_drain(p)
        return len(p.prompt_clear_queue)

    def pc_queue_clear(self, sess: Session):
        """활성 패널의 프롬프트 단위 클리어 큐를 비운다(#4)."""
        win = sess.active_window
        if win and win.active_pane:
            win.active_pane.prompt_clear_queue.clear()

    def set_prompt_clear_message(self, msg: str):
        """① 문서화 지시문 문구를 바꾸고 opts.json 에 영속."""
        msg = (msg or "").strip()
        if msg:
            self.prompt_clear_message = msg
            self._save_opts()
        return self.prompt_clear_message

    def _pc_inject(self, pane: Pane, text: str):
        """패널 안 Claude 에게 한 줄 입력+Enter 주입(자동재개 _fire_resume 와 동일 경로).
        프롬프트 추적/히스토리를 거치지 않아 사용자 프롬프트와 섞이지 않는다."""
        if pane.pty is None:
            return
        try:
            pane.pty.write((text + "\r").encode("utf-8"))
        except OSError:
            pass

    def _pc_drain(self, pane: Pane):
        """큐(#4)의 다음 명령을 Claude 에 투입하고 새 사이클을 시작한다. last_prompt
        를 그 명령으로 갱신해 헤더가 '지금 처리 중인 명령'을 보이게 하고, phase 는
        None 으로 둬 그 명령 완료 시 다시 doc→/clear 사이클이 돌게 한다."""
        if not pane.prompt_clear_queue:
            return
        nxt = pane.prompt_clear_queue.pop(0)
        pane.last_prompt = nxt
        pane._pc_phase = None
        self._pc_inject(pane, nxt)

    def _pc_advance(self, pane: Pane):
        """프롬프트 단위 클리어 상태기계를 busy→idle 경계에서 한 단계 전진한다.

        phase None(사용자 프롬프트 완료) → 문서화 지시 주입(phase=doc)
        phase doc(문서화 응답 완료)      → /clear 주입(phase=clear)
        phase clear(/clear 완료)         → 큐(#4)에 다음 명령이 있으면 투입하고 새
                                           사이클로, 없으면 시퀀스 종료(phase=None)
        """
        ph = pane._pc_phase
        if ph is None:
            self._pc_inject(pane, self.prompt_clear_message)
            pane._pc_phase = "doc"
        elif ph == "doc":
            self._pc_inject(pane, "/clear")
            pane._pc_phase = "clear"
        else:  # "clear"
            pane._pc_phase = None
            if pane.prompt_clear_queue:
                self._pc_drain(pane)

    # ---- 자동 doc→/clear(§10): idle 지속 N초 후 1회 문서화→/clear ----
    def set_auto_doc_clear(self, value=None):
        """Claude idle 지속 시 자동 문서화→/clear 모드 토글. value 미지정 시 반전.
        끄면 무장된 모든 패널 타이머를 해제한다. opts.json 영속."""
        self.auto_doc_clear = (not self.auto_doc_clear) if value is None \
            else bool(value)
        if not self.auto_doc_clear:
            for p in self._all_panes():
                self._adc_disarm(p)
        self._save_opts()
        return self.auto_doc_clear

    def _adc_disarm(self, pane: Pane):
        """무장된 자동 doc→/clear 타이머를 해제한다(사용자 입력·재busy·세션 종료·
        토글 off 시). 핸들이 없으면 무동작."""
        t = getattr(pane, "_adc_timer", None)
        if t is not None:
            t.cancel()
            pane._adc_timer = None

    def _adc_arm(self, pane: Pane):
        """idle 진입 시점에 무장: auto_doc_clear_delay 초 뒤 _adc_fire 를 예약한다.
        기존 타이머가 있으면 먼저 해제(재무장). 실행 중인 이벤트 루프가 없으면
        (테스트가 _scan_claude 를 동기 호출하는 등) 조용히 패스한다 — 타이머 기반
        자동 발화만 비활성일 뿐 다른 동작에는 영향이 없다."""
        self._adc_disarm(pane)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        pane._adc_timer = loop.call_later(
            self.auto_doc_clear_delay, self._adc_fire, pane)

    def _adc_fire(self, pane: Pane):
        """타이머 만료(idle 가 N초 지속됨): 발화 조건을 재확인한 뒤 문서화→/clear
        시퀀스를 시작한다. idle 이 아니거나(그새 busy/limit/종료), 이미 진행 중이거나,
        수동 prompt_clear_mode 거나, 토글이 꺼졌으면 발화하지 않는다.
        시작 = _pc_phase 를 None 으로 두고 _pc_advance 로 문서화 지시문을 주입(→doc).
        이후 busy→idle 경계마다 _scan_claude 가 doc→clear→종료로 시퀀스를 잇는다."""
        pane._adc_timer = None
        if not self.auto_doc_clear:
            return
        if pane._claude != "idle":
            return
        if pane._adc_active or pane.prompt_clear_mode:
            return
        pane._adc_active = True
        pane._pc_phase = None
        self._pc_advance(pane)

    # ---- 권한모드 자동 오토모드 전환(§10) ----
    def set_claude_auto_mode(self, value=None):
        """Claude idle 시 권한모드를 auto 로 자동 맞추는 모드 토글. value 미지정 시
        반전. 끄면 모든 패널의 시도 카운터를 리셋한다. opts.json 영속."""
        self.claude_auto_mode = (not self.claude_auto_mode) if value is None \
            else bool(value)
        if not self.claude_auto_mode:
            for p in self._all_panes():
                p._cam_tries = 0
                p._cam_last = None
        self._save_opts()
        return self.claude_auto_mode

    def _inject_keys(self, pane: Pane, data: bytes):
        """패널 PTY 로 raw 키 바이트를 보낸다(_pc_inject 와 달리 Enter 를 안 붙임).
        권한모드 순환(shift+tab=\\x1b[Z) 등 제어키 주입용."""
        if pane.pty is None:
            return
        try:
            pane.pty.write(data)
        except OSError:
            pass

    def _maybe_auto_mode(self, pane: Pane, txt: str):
        """idle 패널의 권한모드 footer 를 확인해 auto 가 아니면 shift+tab(backtab,
        \\x1b[Z)을 한 번 보내 권한모드를 순환시킨다(폐루프 — auto 가 될 때까지 다음
        프레임에 다시 시도, 최대 _CAM_MAX). footer 순서가 고정이 아닐 수 있어 폐루프로
        간다. 화면 갱신 전(직전과 같은 모드)에는 중복 주입하지 않는다.

        대상 제외: footer 미관측(None)·이미 auto·bypass(명시적 위험 모드는 안 건드림)."""
        mode = claude_perm_mode(txt)
        if mode is None:
            return                       # footer 안 보임 — 판정 불가, 상태 유지
        if mode in ("auto", "bypass"):
            pane._cam_tries = 0          # 목표 도달/대상 아님 → 시도 리셋
            pane._cam_last = mode
            return
        # default/plan → auto. 직전에 작용한 모드와 같으면(아직 화면 미갱신) 대기.
        if mode == pane._cam_last and pane._cam_tries > 0:
            return
        if pane._cam_tries >= _CAM_MAX:
            return                       # 무한 순환 가드(오검출 대비)
        pane._cam_tries += 1
        pane._cam_last = mode
        self._inject_keys(pane, b"\x1b[Z")

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

    # ---- 데몬 부트스트랩 세션 ----
    def ensure_default_session(self, cols: int, rows: int) -> Session:
        if self.sessions:
            return next(iter(self.sessions.values()))
        root = self.spawn_pane(cols, max(MIN_H, rows))
        name = str(self._session_seq)
        self._session_seq += 1
        sess = Session(name, root)
        self.sessions[name] = sess
        return sess

    def _unique_name(self, name: str | None) -> str:
        if not name:
            name = str(self._session_seq)
            self._session_seq += 1
            while name in self.sessions:
                name = str(self._session_seq)
                self._session_seq += 1
            return name
        if name not in self.sessions:
            return name
        i = 1
        while f"{name}-{i}" in self.sessions:
            i += 1
        return f"{name}-{i}"

    def new_session(self, cols: int, rows: int, name: str | None = None) -> Session:
        root = self.spawn_pane(cols, max(MIN_H, rows))
        uname = self._unique_name(name)
        sess = Session(uname, root)
        self.sessions[uname] = sess
        return sess

    def get_or_create_session(self, name: str | None, cols: int, rows: int) -> Session:
        """단일 세션 모델: 요청 이름과 무관하게 항상 하나의 기본 세션에 attach 한다.

        멀티 세션 개념을 사용자 표면에서 제거했으므로(최상위는 탭), 클라이언트의
        세션 이름 요청은 무시하고 단일 세션을 보장/반환한다."""
        return self.ensure_default_session(cols, rows)

    # ---- 트리 조작 ----
    def split_pane(self, sess: Session, orient: str, path: str | None = None):
        win = sess.active_window
        if not win:
            return
        win.zoomed = False
        target = win.active_pane
        # 시작 디렉토리: default-path 설정에 따름(기본 current=분할 대상 패널 cwd).
        cwd = self._resolve_start_cwd(sess, path)
        # 새 패널은 일단 임시 크기로 만들고, 곧 재배치된다.
        new = self.spawn_pane(MIN_W, MIN_H, cwd=cwd)
        split = Split(orient, target, new, 0.5)
        parent = target.parent
        split.parent = parent
        target.parent = split
        new.parent = split
        if parent is None:
            win.root = split
        elif parent.a is target:
            parent.a = split
        else:
            parent.b = split
        win.active_pane = new

    def kill_pane(self, sess: Session, pane: Pane):
        # 셸 자식 종료(graceful = SIGHUP). 나머지 정리는 _pane_eof.
        if pane.pty is not None:
            pane.pty.terminate()
        self._pane_eof(pane)

    def _remove_pane_from_tree(self, pane: Pane):
        # 어떤 세션/탭(윈도우)에 속하는지 탐색
        for sess in list(self.sessions.values()):
            for wi, tab in enumerate(list(sess.tabs)):
                win = tab.window
                if pane not in win.panes():
                    continue
                parent = pane.parent
                if parent is None:
                    # 윈도우의 마지막 패널 → 탭 제거
                    sess.tabs.pop(wi)
                    self._reindex(sess)
                    if not sess.tabs:
                        del self.sessions[sess.name]
                else:
                    sibling = parent.b if parent.a is pane else parent.a
                    gp = parent.parent
                    sibling.parent = gp
                    if gp is None:
                        win.root = sibling
                    elif gp.a is parent:
                        gp.a = sibling
                    else:
                        gp.b = sibling
                    if win.active_pane is pane:
                        win.active_pane = sibling.first_pane()
                self._broadcast_session(sess)
                if not self.sessions:
                    self._notify_no_sessions()
                return

    def _reindex(self, sess: Session):
        for i, t in enumerate(sess.tabs):
            t.index = i

    # ---- 라이브 PTY 팝업(display-popup) ----
    def _popup_rect(self, cols: int, rows: int, want_w=None, want_h=None):
        """팝업 박스의 화면 geometry(x, y, w, h)를 세션 크기에 맞춰 중앙 정렬로 산출.

        want_w/want_h 미지정 시 화면의 약 80%(테두리 포함 외곽 크기). 최소 외곽
        크기는 가로 12·세로 5(테두리 2 + 내용 ≥3). 항상 화면 안에 들어오도록 클램프."""
        w = want_w or max(12, cols * 8 // 10)
        h = want_h or max(5, rows * 8 // 10)
        w = max(12, min(w, cols))
        h = max(5, min(h, rows))
        x = max(0, (cols - w) // 2)
        y = max(0, (rows - h) // 2)
        return x, y, w, h

    def popup_open(self, sess: Session, cmd: str, want_w=None, want_h=None,
                   title: str | None = None, cwd: str | None = None):
        """라이브 PTY 팝업을 연다. 트리에 속하지 않는 PTY 패널 1개를 띄우고
        `셸 -c <cmd>` 를 실행한다. 이미 열려 있으면 먼저 닫는다(한 번에 하나)."""
        cmd = (cmd or "").strip()
        if not cmd:
            return False
        if sess.popup:
            self.popup_close(sess)
        cols, rows = self._session_size(sess)
        x, y, w, h = self._popup_rect(cols, rows, want_w, want_h)
        cw, ch = max(MIN_W, w - 2), max(MIN_H, h - 2)
        if cwd is None:
            win = sess.active_window
            if win and win.active_pane:
                cwd = self._pane_cwd(win.active_pane)
        pane = self.spawn_pane(cw, ch, cwd=cwd, cmd=cmd)
        pane.title = (title or cmd)[:40]
        sess.popup = {"pane": pane, "title": pane.title,
                      "want_w": want_w, "want_h": want_h, "cmd": cmd}
        self._broadcast_session(sess)
        return True

    def popup_close(self, sess: Session):
        """팝업을 닫고 그 PTY 자식을 종료한다(명령이 안 끝났어도 강제 닫기)."""
        pu = sess.popup
        if not pu:
            return False
        sess.popup = None
        pane = pu.get("pane")
        if pane is not None:
            self._stop_pane_feed(pane)  # 진행 중 드레인 취소
        if pane is not None and pane.pty is not None:
            try:
                pane.pty.stop_reader()
                pane.pty.terminate()
                pane.pty.close()
                pane.pty.reap(block=False)
            except Exception:
                pass
        self._broadcast_session(sess)
        return True

    def _popup_layout(self, sess: Session, cols: int, rows: int):
        """팝업 박스 geometry 를 산출하고 팝업 패널 PTY 를 내용 크기로 리사이즈한 뒤
        레이아웃 메시지에 넣을 dict 를 반환. 팝업 없으면 None."""
        pu = sess.popup
        if not pu:
            return None
        pane = pu.get("pane")
        if pane is None:
            return None
        x, y, w, h = self._popup_rect(cols, rows, pu.get("want_w"),
                                      pu.get("want_h"))
        cw, ch = max(MIN_W, w - 2), max(MIN_H, h - 2)
        pane.resize(cw, ch)
        return {"id": pane.id, "x": x, "y": y, "w": w, "h": h,
                "cx": x + 1, "cy": y + 1, "cw": cw, "ch": ch,
                "title": pu.get("title") or ""}

    def _pane_cwd(self, pane: Pane) -> str | None:
        # 자식(셸) 프로세스의 cwd 를 추정한다. 실패 시 None.
        pid = pane.child_pid
        # Linux 빠른 경로: /proc/<pid>/cwd 심볼릭 링크.
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            pass
        # macOS/BSD 폴백: /proc 가 없으므로 lsof 로 cwd(fd=cwd) 를 조회.
        # -Fn 은 'n<경로>' 한 줄로 출력하므로 파싱이 단순하다.
        try:
            out = subprocess.run(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                capture_output=True, text=True, timeout=2)
            for line in out.stdout.splitlines():
                if line.startswith("n"):
                    return line[1:] or None
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return None

    def _resolve_start_cwd(self, sess: Session,
                           path: str | None = None) -> str | None:
        """새 탭/패널이 시작할 디렉토리를 default-path 설정에 따라 결정한다.

        current(기본) = 현재 활성 패널의 cwd, home = $HOME, 그 외 = 해당 경로.
        결정 불가 시 None 을 반환하면 셸은 서버 프로세스의 cwd 에서 시작한다."""
        val = (path or "current").strip()
        low = val.lower()
        if low in ("", "current", "pane_current_path"):
            win = sess.active_window
            pane = win.active_pane if win else None
            return self._pane_cwd(pane) if pane else None
        if low == "home":
            return os.path.expanduser("~")
        return os.path.expanduser(val)

    def new_window(self, sess: Session, path: str | None = None):
        """새 탭(= 새 윈도우, 단일 패널)을 만들고 활성화한다."""
        c = self.clients_of(sess)
        cols = c.cols if c else 80
        rows = c.rows if c else 24
        cwd = self._resolve_start_cwd(sess, path)
        root = self.spawn_pane(cols, rows, cwd=cwd)
        idx = len(sess.tabs)
        sess.tabs.append(Tab(idx, "win", Window(root)))
        sess.last_index = sess.active_index
        sess.active_index = idx

    # 탭 용어 별칭
    new_tab = new_window

    def select_window(self, sess: Session, index: int):
        if 0 <= index < len(sess.tabs):
            if index != sess.active_index:
                sess.last_index = sess.active_index
            sess.active_index = index
            t = sess.tabs[index]
            # 보면 플래그 해제(활동·벨·Claude 완료 알림)
            t.has_activity = t.has_bell = t.has_claude_done = False

    def set_monitor(self, sess: Session, which: str, value=None):
        tab = sess.active_tab
        if not tab:
            return
        attr = "monitor_activity" if which == "activity" else "monitor_bell"
        cur = getattr(tab, attr)
        setattr(tab, attr, (not cur) if value is None else bool(value))

    def last_window(self, sess: Session):
        li = getattr(sess, "last_index", 0)
        if 0 <= li < len(sess.tabs):
            self.select_window(sess, li)

    def move_window(self, sess: Session, new_index: int):
        n = len(sess.tabs)
        if n < 2:
            return
        new_index = max(0, min(new_index, n - 1))
        tab = sess.tabs.pop(sess.active_index)
        sess.tabs.insert(new_index, tab)
        self._reindex(sess)
        sess.active_index = sess.tabs.index(tab)

    def swap_window(self, sess: Session, target_index: int):
        n = len(sess.tabs)
        if not (0 <= target_index < n) or target_index == sess.active_index:
            return
        i = sess.active_index
        sess.tabs[i], sess.tabs[target_index] = (
            sess.tabs[target_index], sess.tabs[i])
        self._reindex(sess)
        sess.active_index = target_index

    def move_tab(self, sess: Session, index: int, to_index: int):
        """index 위치의 탭을 to_index 로 재배치(활성 탭 추적 유지)."""
        n = len(sess.tabs)
        if n < 2 or not (0 <= index < n):
            return
        to_index = max(0, min(to_index, n - 1))
        if to_index == index:
            return
        active_tab = sess.active_tab
        tab = sess.tabs.pop(index)
        sess.tabs.insert(to_index, tab)
        self._reindex(sess)
        if active_tab in sess.tabs:
            sess.active_index = sess.tabs.index(active_tab)

    def move_current_tab(self, sess: Session, where: str):
        """현재(활성) 탭을 좌/우/맨앞/맨뒤로 이동."""
        i, n = sess.active_index, len(sess.tabs)
        to = {"left": i - 1, "right": i + 1,
              "first": 0, "last": n - 1}.get(where)
        if to is not None:
            self.move_tab(sess, i, to)

    LAYOUTS = ["even-horizontal", "even-vertical", "main-horizontal",
               "main-vertical", "tiled"]

    def select_layout(self, sess: Session, preset: str):
        win = sess.active_window
        if win:
            win.apply_preset(preset)

    def cycle_layout(self, sess: Session):
        win = sess.active_window
        if not win:
            return
        win.layout_idx = (win.layout_idx + 1) % len(self.LAYOUTS)
        win.apply_preset(self.LAYOUTS[win.layout_idx])

    def rotate_panes(self, sess: Session, forward: bool = True):
        win = sess.active_window
        if not win:
            return
        win.zoomed = False
        leaves = win.panes()
        n = len(leaves)
        if n < 2:
            return
        slots = [(p.parent, "a" if p.parent.a is p else "b") for p in leaves]
        shift = 1 if forward else -1
        new = [leaves[(i + shift) % n] for i in range(n)]
        for (par, attr), pane in zip(slots, new):
            setattr(par, attr, pane)
            pane.parent = par

    def swap_pane(self, sess: Session, forward: bool = True):
        win = sess.active_window
        if not win:
            return
        win.zoomed = False
        leaves = win.panes()
        n = len(leaves)
        if n < 2 or win.active_pane not in leaves:
            return
        i = leaves.index(win.active_pane)
        j = (i + 1) % n if forward else (i - 1) % n
        a, b = leaves[i], leaves[j]
        pa, aattr = a.parent, ("a" if a.parent.a is a else "b")
        pb, battr = b.parent, ("a" if b.parent.a is b else "b")
        setattr(pa, aattr, b)
        b.parent = pa
        setattr(pb, battr, a)
        a.parent = pb
        # 활성 패널은 그대로 따라간다(같은 셸)

    def swap_pane_ids(self, sess: Session, id_a: int, id_b: int) -> bool:
        """활성 윈도우에서 id_a·id_b 두 리프 패널의 트리 위치를 맞바꾼다(드래그
        swap 용). swap_pane(인접 순환)과 달리 임의의 두 패널을 지정해 교환한다.
        활성 패널은 같은 셸을 그대로 따라간다(위치만 교환)."""
        win = sess.active_window
        if not win or id_a == id_b:
            return False
        leaves = {p.id: p for p in win.panes()}
        a, b = leaves.get(id_a), leaves.get(id_b)
        if a is None or b is None or a.parent is None or b.parent is None:
            return False
        win.zoomed = False
        pa, aattr = a.parent, ("a" if a.parent.a is a else "b")
        pb, battr = b.parent, ("a" if b.parent.a is b else "b")
        setattr(pa, aattr, b)
        b.parent = pa
        setattr(pb, battr, a)
        a.parent = pb
        return True

    def _detach_pane(self, win: Window, pane: Pane):
        """패널을 윈도우 트리에서 떼어낸다(트리 수축). 활성 패널은 형제로 이동."""
        parent = pane.parent
        if parent is None:
            return False  # 윈도우의 유일 패널 → 분리 불가
        sibling = parent.b if parent.a is pane else parent.a
        gp = parent.parent
        sibling.parent = gp
        if gp is None:
            win.root = sibling
        elif gp.a is parent:
            gp.a = sibling
        else:
            gp.b = sibling
        if win.active_pane is pane:
            win.active_pane = sibling.first_pane()
        pane.parent = None
        return True

    def break_pane(self, sess: Session):
        win = sess.active_window
        if not win:
            return
        pane = win.active_pane
        if not self._detach_pane(win, pane):
            return  # 단일 패널 윈도우는 분리 불가
        win.zoomed = False
        idx = len(sess.tabs)
        sess.tabs.append(Tab(idx, "win", Window(pane)))
        sess.active_index = idx

    def join_pane(self, sess: Session, src_index: int | None = None,
                  orient: str = "tb"):
        """다른 윈도우의 활성 패널을 현재 활성 패널과 분할로 합친다."""
        win = sess.active_window
        if not win or len(sess.tabs) < 2:
            return
        if src_index is None:
            src_index = (sess.active_index - 1) % len(sess.tabs)
        if not (0 <= src_index < len(sess.tabs)) or src_index == sess.active_index:
            return
        src_tab = sess.tabs[src_index]
        src = src_tab.window
        pane = src.active_pane
        src_single = pane.parent is None
        if not src_single:
            self._detach_pane(src, pane)
        target = win.active_pane
        new = Split(orient, target, pane, 0.5)
        pp = target.parent
        new.parent = pp
        target.parent = new
        pane.parent = new
        if pp is None:
            win.root = new
        elif pp.a is target:
            pp.a = new
        else:
            pp.b = new
        win.active_pane = pane
        win.zoomed = False
        if src_single:
            sess.tabs.remove(src_tab)
            self._reindex(sess)
        cur_tab = next(t for t in sess.tabs if t.window is win)
        sess.active_index = sess.tabs.index(cur_tab)

    def toggle_zoom(self, sess: Session):
        win = sess.active_window
        if not win:
            return
        # 패널이 하나뿐이면 줌은 의미 없음
        if len(win.panes()) <= 1:
            win.zoomed = False
            return
        win.zoomed = not win.zoomed

    def rename_window(self, sess: Session, name: str):
        tab = sess.active_tab
        if tab and name:
            tab.name = name
            tab.window.auto_rename = False  # 수동 이름 지정 시 자동 갱신 끔

    rename_tab = rename_window

    def set_auto_rename(self, sess: Session, value=None):
        win = sess.active_window
        if win:
            win.auto_rename = (not win.auto_rename) if value is None else bool(value)

    def _fg_command(self, fd: int):
        """PTY 의 포그라운드 프로세스 그룹 명령 이름(자동 탭이름·ssh 감지용).

        Windows(ConPTY)에는 포그라운드 프로세스 그룹 개념과 os.tcgetpgrp/ps 가
        없어 지원하지 않는다(docs/WINDOWS_PORT.md §4 기능 열화). 자동 이름은
        고정 탭이름으로 우아하게 폴백된다. os.tcgetpgrp 는 Windows 에 아예
        없어(AttributeError) OSError 핸들러로는 못 잡으므로 먼저 분기한다."""
        if pty_backend.IS_WINDOWS:
            return None
        try:
            pgid = os.tcgetpgrp(fd)
        except OSError:
            return None
        try:
            out = subprocess.run(["ps", "-o", "comm=", "-p", str(pgid)],
                                 capture_output=True, text=True, timeout=1).stdout.strip()
        except Exception:
            return None
        if not out:
            return None
        name = os.path.basename(out.split()[0])
        return name[1:] if name.startswith("-") else name  # -zsh → zsh

    async def _autorename_loop(self):
        while self.running:
            await asyncio.sleep(2.0)
            for sess in list(self.sessions.values()):
                clients = [c for c in self.clients if c.session is sess]
                if not clients:
                    continue
                changed = False
                for tab in sess.tabs:
                    win = tab.window
                    if not getattr(win, "auto_rename", False) or not win.active_pane:
                        continue
                    cmd = self._fg_command(win.active_pane.master_fd)
                    if cmd and cmd != tab.name:
                        tab.name = cmd
                        changed = True
                if changed:
                    for c in clients:
                        await write_msg(c.writer, self._status_msg(sess))

    def kill_window(self, sess: Session):
        win = sess.active_window
        if not win:
            return
        for p in win.panes():
            self._stop_pane_feed(p)     # 진행 중 드레인 취소
            if p.pty is not None:
                p.pty.terminate()       # SIGHUP
                p.pty.stop_reader()
                p.pty.close()
                p.pty.reap(block=False)
        tab = sess.active_tab
        if tab in sess.tabs:
            sess.tabs.remove(tab)
        self._reindex(sess)
        if not sess.tabs:
            del self.sessions[sess.name]

    kill_tab = kill_window

    def rename_session(self, sess: Session, name: str):
        if not name or name == sess.name or name in self.sessions:
            return
        del self.sessions[sess.name]
        sess.name = name
        self.sessions[name] = sess

    def _destroy_pane_proc(self, pane: Pane):
        """패널의 PTY/자식 프로세스를 정리한다(트리 조작 없음)."""
        self._stop_pane_feed(pane)      # 진행 중 드레인 취소
        self._close_capfile(pane.id)
        if pane.pty is not None:
            pane.pty.terminate()        # SIGHUP
            pane.pty.stop_reader()
            pane.pty.close()
            pane.pty.reap(block=False)

    def kill_session(self, name: str):
        s = self.sessions.pop(name, None)
        if not s:
            return
        for tab in s.tabs:
            for p in tab.window.panes():
                self._destroy_pane_proc(p)
        for c in self.clients:
            if c.session is s:
                c.session = next(iter(self.sessions.values()), None)

    def switch_session(self, client: "ClientConn", name: str) -> bool:
        target = self.sessions.get(name)
        if target:
            client.session = target
            return True
        return False

    def select_pane_dir(self, sess: Session, direction: str):
        win = sess.active_window
        if not win:
            return
        cur = win.active_pane
        cx = cur.rect[0] + cur.rect[2] / 2
        cy = cur.rect[1] + cur.rect[3] / 2
        best, best_d = None, None
        for p in win.panes():
            if p is cur:
                continue
            px = p.rect[0] + p.rect[2] / 2
            py = p.rect[1] + p.rect[3] / 2
            if direction == "left" and px >= cx:
                continue
            if direction == "right" and px <= cx:
                continue
            if direction == "up" and py >= cy:
                continue
            if direction == "down" and py <= cy:
                continue
            d = abs(px - cx) + abs(py - cy)
            if best_d is None or d < best_d:
                best, best_d = p, d
        if best:
            win.active_pane = best

    def last_pane(self, sess: Session):
        win = sess.active_window
        if win:
            win.toggle_last_pane()

    def set_sync(self, sess: Session, value=None):
        win = sess.active_window
        if not win:
            return
        win.sync = (not win.sync) if value is None else bool(value)

    def set_pane_title(self, sess: Session, title: str):
        win = sess.active_window
        if win and win.active_pane:
            win.active_pane.title = title

    def set_border_status(self, sess: Session, value=None):
        win = sess.active_window
        if not win:
            return
        win.border_status = (not win.border_status) if value is None else bool(value)

    @staticmethod
    def _pane_text_lines(pane: Pane):
        screen = pane.screen
        h = getattr(screen, "history", None)
        hist = list(h.top) if h is not None else []
        full = hist + [screen.buffer[y] for y in range(screen.lines)]
        return ["".join((line[x].data or " ")
                        for x in range(screen.columns)).rstrip()
                for line in full]

    def set_buffer(self, text: str):
        if not text:
            return
        self.buffers.insert(0, text)
        del self.buffers[50:]

    @staticmethod
    def _write_paste(pane: Pane, text: str):
        """텍스트를 패널에 입력. 내부 앱이 bracketed paste 를 켰으면 마커로 감싼다
        (멀티라인 붙여넣기가 줄마다 실행되지 않고 한 번의 붙여넣기로 처리됨)."""
        data = text.encode("utf-8")
        # 붙여넣기(모바일 받아쓰기·자동완성 포함)도 프롬프트 추적에 반영한다.
        # 이게 없으면 붙여넣은 Claude 프롬프트는 last_prompt 에 안 잡혀 헤더가
        # 셸 실행 명령("claude")에 머문다. 이후 사용자가 Enter(\r) 를 누르면
        # 누적된 본문이 last_prompt 로 확정된다(개행 포함 시 즉시 확정).
        Server._track_prompt(pane, data)
        if pane.bracketed:
            data = b"\x1b[200~" + data + b"\x1b[201~"
        try:
            if pane.pty is not None:
                pane.pty.write(data)
        except OSError:
            pass

    def _reset_view(self, pane: Pane):
        if pane.scroll or pane._match_abs is not None:
            pane.scroll = 0
            pane._match_abs = None
            pane.dirty = True

    def paste_text(self, sess: Session, text: str):
        win = sess.active_window
        if not win or not text:
            return
        self._reset_view(win.active_pane)
        self._write_paste(win.active_pane, text)

    def paste_buffer(self, sess: Session, index=0):
        win = sess.active_window
        if not win or not self.buffers:
            return
        if not (0 <= index < len(self.buffers)):
            index = 0
        self._reset_view(win.active_pane)
        self._write_paste(win.active_pane, self.buffers[index])

    # ---- 레이아웃 영속(저장/복원) ----
    @property
    def layout_path(self):
        return os.path.join(os.path.dirname(ipc.state_base(self.sock_path)) or "/tmp",
                            "layout.json")

    def _serialize_node(self, node):
        if isinstance(node, Split):
            return {"type": "split", "orient": node.orient, "ratio": node.ratio,
                    "a": self._serialize_node(node.a),
                    "b": self._serialize_node(node.b)}
        return {"type": "pane", "title": node.title}

    def _build_node(self, spec, cols, rows):
        if spec.get("type") == "split":
            a = self._build_node(spec["a"], cols, rows)
            b = self._build_node(spec["b"], cols, rows)
            return Split(spec.get("orient", "lr"), a, b, spec.get("ratio", 0.5))
        p = self.spawn_pane(cols, rows)
        p.title = spec.get("title", "shell")
        return p

    def save_layout(self, path=None):
        path = path or self.layout_path
        data = {"sessions": [
            {"name": s.name, "windows": [
                {"name": t.name, "root": self._serialize_node(t.window.root)}
                for t in s.tabs]}
            for s in self.sessions.values()]}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return True
        except OSError:
            return False

    def restore_layout(self, path=None):
        path = path or self.layout_path
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        for ss in data.get("sessions", []):
            tabs = []
            for i, wspec in enumerate(ss.get("windows", [])):
                root = self._build_node(wspec["root"], 80, 24)
                w = Window(root)
                w._active = root if isinstance(root, Pane) else root.first_pane()
                w._fix_parents(root, None)
                tabs.append(Tab(i, wspec.get("name", "win"), w))
            if not tabs:
                continue
            sess = Session.__new__(Session)
            sess.name = self._unique_name(ss.get("name"))
            sess.created_at = time.time()
            sess.tabs = tabs
            sess.active_index = 0
            sess.last_index = 0
            self.sessions[sess.name] = sess
        return True

    # ---- 작업 보존 재시작(re-exec) 상태 직렬화/복원 — docs/RESTART_SCENARIO.md ----
    @property
    def resume_state_path(self) -> str:
        """재시작 보존 상태 파일. save_layout(구조만)과 달리 살아 있는 셸의 PTY
        식별자(child_pid·master_fd 번호)·패널 상태·화면 스냅샷까지 담는다."""
        return ipc.state_base(self.sock_path) + ".resume.json"

    def _all_panes(self):
        for sess in self.sessions.values():
            for tab in sess.tabs:
                for p in tab.window.panes():
                    yield p

    def _serialize_resume_node(self, node):
        if isinstance(node, Split):
            return {"type": "split", "orient": node.orient, "ratio": node.ratio,
                    "a": self._serialize_resume_node(node.a),
                    "b": self._serialize_resume_node(node.b)}
        return {"type": "pane", "pane": node.export_state()}

    def _serialize_resume_window(self, w: Window) -> dict:
        return {"root": self._serialize_resume_node(w.root),
                "active_pid": w.active_pane.child_pid if w.active_pane else None,
                "zoomed": w.zoomed, "border_status": w.border_status,
                "sync": w.sync, "auto_rename": w.auto_rename,
                "layout_idx": w.layout_idx}

    def save_resume_state(self, path: str | None = None) -> bool:
        """현재 트리·패널 상태(살아 있는 셸 PTY 포함)를 상태 파일에 직렬화한다.
        re-exec 직전에 호출되며, 새 이미지가 restore_resume_state 로 복원한다."""
        path = path or self.resume_state_path
        data = {"version": 1, "sessions": [
            {"name": s.name, "active_index": s.active_index,
             "last_index": s.last_index,
             "tabs": [{"index": t.index, "name": t.name,
                       "window": self._serialize_resume_window(t.window),
                       "monitor_activity": t.monitor_activity,
                       "monitor_bell": t.monitor_bell,
                       "monitor_claude": t.monitor_claude}
                      for t in s.tabs]}
            for s in self.sessions.values()]}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return True
        except OSError:
            return False

    def _release_master_cloexec(self):
        """re-exec 직전(ⓐ): 넘길 모든 패널 master fd 의 CLOEXEC 를 해제해 execv 가
        fd 를 닫아 셸을 죽이지 않게 한다. 새 이미지가 채택 직후 다시 건다(ⓓ/adopt).
        평상시엔 절대 풀지 않는다(§6 불변식)."""
        try:
            import fcntl
        except ImportError:
            return
        for p in self._all_panes():
            if p.master_fd is not None and p.master_fd >= 0:
                try:
                    flags = fcntl.fcntl(p.master_fd, fcntl.F_GETFD)
                    fcntl.fcntl(p.master_fd, fcntl.F_SETFD,
                                flags & ~fcntl.FD_CLOEXEC)
                except OSError:
                    pass

    def _build_resume_node(self, spec):
        if spec.get("type") == "split":
            a = self._build_resume_node(spec["a"])
            b = self._build_resume_node(spec["b"])
            return Split(spec.get("orient", "lr"), a, b, spec.get("ratio", 0.5))
        ps = spec["pane"]
        cols, rows = max(MIN_W, ps["cols"]), max(MIN_H, ps["rows"])
        # 상속된 master fd 를 fork 없이 다시 채택한다(PID 그대로 → reap/killpg 유효).
        proc = pty_backend.adopt(ps["master_fd"], ps["child_pid"],
                                 cols=cols, rows=rows)
        fd = proc.fileno() if hasattr(proc, "fileno") else ps["master_fd"]
        pane = Pane(ps["child_pid"], fd, cols, rows)
        pane.pty = proc
        pane.import_state(ps)
        self._attach_reader(pane)
        return pane

    def restore_resume_state(self, path: str | None = None) -> bool:
        """save_resume_state 가 만든 상태 파일에서 세션·탭·트리를 복원하고, 상속된
        PTY master fd 를 채택해 살아 있는 셸에 다시 연결한다. re-exec 후 새 서버
        이미지의 부트 경로에서 호출. docs/RESTART_SCENARIO.md ⓓ."""
        path = path or self.resume_state_path
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        if data.get("version") != 1:
            return False
        for ss in data.get("sessions", []):
            tabs = []
            for wt in ss.get("tabs", []):
                wspec = wt["window"]
                try:
                    root = self._build_resume_node(wspec["root"])
                except (KeyError, OSError):
                    continue
                w = Window(root)
                w._fix_parents(root, None)
                apid = wspec.get("active_pid")
                ap = next((p for p in w.panes() if p.child_pid == apid), None)
                w._active = ap or (root if isinstance(root, Pane)
                                   else root.first_pane())
                w.zoomed = wspec.get("zoomed", False)
                w.border_status = wspec.get("border_status", False)
                w.sync = wspec.get("sync", False)
                w.auto_rename = wspec.get("auto_rename", True)
                w.layout_idx = wspec.get("layout_idx", 0)
                t = Tab(wt.get("index", len(tabs)), wt.get("name", "win"), w)
                t.monitor_activity = wt.get("monitor_activity", False)
                t.monitor_bell = wt.get("monitor_bell", True)
                t.monitor_claude = wt.get("monitor_claude", True)
                tabs.append(t)
            if not tabs:
                continue
            sess = Session.__new__(Session)
            sess.name = self._unique_name(ss.get("name"))
            sess.created_at = time.time()
            sess.tabs = tabs
            sess.active_index = max(0, min(ss.get("active_index", 0),
                                           len(tabs) - 1))
            sess.last_index = ss.get("last_index", 0)
            sess.popup = None
            self.sessions[sess.name] = sess
        if self.sessions:
            self._induce_redraw_all()
        return bool(self.sessions)

    def _induce_redraw_all(self):
        """재시작 복원 후 alt-screen TUI(vim/claude/htop 등)가 다시 그리도록 각 패널
        PTY 에 SIGWINCH 를 한 번 유발한다(docs/RESTART_SCENARIO.md 주의 ① 대안 B).

        새 이미지의 pyte 는 메인 화면(직렬화한 스냅샷)에서 시작하지만, 살아 있는
        앱은 alt 화면 상태 그대로다. 재접속 크기가 이전과 같으면 resize 가 SIGWINCH
        를 안 보내 앱이 영영 다시 안 그린다. 그래서 크기를 한 칸 줄였다 되돌려
        커널이 SIGWINCH 를 보내게 강제한다(앱이 현재 화면을 전체 repaint → 스냅샷을
        덮어쓴다). winsize 만 건드리고 pyte/Pane 치수는 그대로 둔다."""
        for p in self._all_panes():
            if p.pty is None:
                continue
            try:
                p.pty.set_winsize(max(1, p.rows - 1), p.cols)
                p.pty.set_winsize(p.rows, p.cols)
            except OSError:
                pass

    def restart_server(self) -> bool:
        """작업 보존 재시작(방식① 제자리 re-exec) — 셸/PTY 를 살린 채 서버 코드만
        새 이미지로 교체한다. docs/RESTART_SCENARIO.md §3. POSIX 전용.

        ⓑ 상태 직렬화 → ⓔ 클라이언트 재접속 통지 → (call_later) ⓐ master fd
        CLOEXEC 해제 → ⓒ os.execv. PID 가 그대로라 자식 셸이 계속 자식으로 남고,
        상속된 master fd 가 PTY 를 살린다. 새 이미지는 --resume 로 채택 복원한다(ⓓ)."""
        if pty_backend.IS_WINDOWS or self.loop is None:
            return False
        if not self.sessions:
            return False
        # 폭주 드레인 중이면 아직 pyte 에 안 먹인 _feedbuf 가 남아 있을 수 있다.
        # execv 로 프로세스 이미지가 바뀌면 그 바이트(파이썬 메모리)는 사라지므로,
        # 직렬화 전에 진행 중 드레인을 멈추고 남은 바이트를 동기로 마저 먹여 화면
        # 스냅샷(_export_screen)에 반영한다.
        for p in self._all_panes():
            buf = p._feedbuf
            self._stop_pane_feed(p)   # 태스크 취소 + _feedbuf 비움
            if buf:
                self._ingest_slice(p, buf)
        if not self.save_resume_state():
            return False
        self._save_opts()
        # ⓔ 재시작 통지: 클라이언트가 끊김을 종료가 아닌 재접속으로 다루게 한다.
        for c in list(self.clients):
            asyncio.create_task(write_msg(c.writer, {"t": "restarting"}))
        # 비-PTY fd(캡처 파일 등) 정리 — 새 이미지가 다시 연다. master fd 는 보존.
        self._close_all_capfiles()
        argv = proc.server_argv(self.sock_path) + ["--resume",
                                                   self.resume_state_path]
        # write_msg 가 flush 될 짧은 틈을 준 뒤 execv(같은 이벤트 루프 틱이 아님).
        self.loop.call_later(0.1, self._do_execv, argv)
        return True

    def _do_execv(self, argv):
        # ⓐ 넘길 master fd 의 CLOEXEC 를 execv 직전에만 해제(평상시 불변식 유지).
        self._release_master_cloexec()
        self.running = False
        try:
            os.execv(argv[0], argv)
        except OSError:
            # execv 실패(드묾): 깨끗한 종료로 폴백. 셸은 SIGHUP 으로 정리된다.
            self._notify_no_sessions()

    # ---- 탭(윈도우+패널) 레이아웃 슬롯: 이름으로 저장/불러오기 ----
    @property
    def slots_path(self):
        # 상태파일 접두 기준(unix=소켓 경로 그대로, tcp=상태 디렉터리/default)
        return ipc.state_base(self.sock_path) + ".slots.json"

    def _load_slots(self) -> dict:
        try:
            with open(self.slots_path, encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_slots(self, slots: dict):
        try:
            with open(self.slots_path, "w", encoding="utf-8") as f:
                json.dump(slots, f)
        except OSError:
            pass

    # ---- 서버 옵션 영속(opts.json) ----
    @property
    def opts_path(self) -> str:
        return ipc.state_base(self.sock_path) + ".opts.json"

    def _load_opts(self) -> dict:
        try:
            with open(self.opts_path, encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_opts(self):
        try:
            with open(self.opts_path, "w", encoding="utf-8") as f:
                json.dump({"capture": self.capture,
                           "claude_header": self.claude_header,
                           "single_border": self.single_border,
                           "coalesce_repaints": self.coalesce_repaints,
                           "prompt_clear_message": self.prompt_clear_message,
                           "auto_doc_clear": self.auto_doc_clear,
                           "auto_doc_clear_delay": self.auto_doc_clear_delay,
                           "claude_auto_mode": self.claude_auto_mode}, f)
        except OSError:
            pass

    # ---- 토큰 사용량 영속 로그(#7) ----
    @property
    def tokens_log_path(self) -> str:
        """응답별 확정 토큰을 적는 JSONL 로그. capture 와 달리 휘발 영역(state_base)
        에 둔다 — 캡처(raw 화면)와 달리 민감 화면 잔재가 없는 집계 데이터라 Perforce
        공유 대상이 아니다. 조회 화면이 read+aggregate 로 시간/일/월×계정 집계."""
        return ipc.state_base(self.sock_path) + ".tokens.jsonl"

    def _tab_index_of(self, sess: Session, pane: Pane):
        for i, tab in enumerate(sess.tabs):
            if pane in tab.window.panes():
                return i
        return None

    def _log_tokens(self, sess: Session, tab: Tab, pane: Pane, amount: int):
        """응답 한 건의 확정 토큰을 JSONL 로그에 append(tokens.step committed>0 이벤트)."""
        rec = usagelog.make_record(
            ts=time.time(), tab=tab.index, pane=pane.id,
            session=pane._claude_session_id, account=pane._claude_account,
            tokens=amount)
        usagelog.append(self.tokens_log_path, rec)

    def set_claude_account(self, sess: Session, name: str):
        """활성 패널의 Claude 계정을 수동 지정(화면 휴리스틱이 못 잡을 때 보정, #7 ②).
        빈 문자열이면 수동 지정 해제(자동 감지로 복귀)."""
        win = sess.active_window
        if not win or not win.active_pane:
            return
        p = win.active_pane
        name = (name or "").strip()
        if name:
            p._claude_account = name
            p._claude_account_manual = True
        else:
            p._claude_account_manual = False
            p._claude_account = None

    # ---- 패널 출력 캡처(Claude 화면 문구 분석용) ----
    def _capture_id(self) -> str:
        """캡처 하위 폴더명(소켓별 격리). state_base 의 basename 에서 .sock 제거."""
        base = os.path.basename(ipc.state_base(self.sock_path))
        if base.endswith(".sock"):
            base = base[:-len(".sock")]
        return base or "default"

    @property
    def capture_dir(self) -> str:
        """캡처(REC) 출력 루트.

        기본 소켓(실사용)은 **프로젝트 디렉터리 하위 `captures/<sock-id>/`** 에 둔다 —
        여러 기계에서 개발 시 Perforce 로 올려 공유·관리하기 위함(docs/HANDOFF.md §10).
        **단 GitHub 미러에는 절대 올라가면 안 되므로** 이 경로는 `.gitignore`/`.p4ignore`
        의 `captures/` 로 차단한다(민감 화면 유출 방지). `PYTMUX_CAPTURE_DIR` 로 강제
        지정 가능(테스트는 임시 디렉터리를 주입해 프로젝트 오염을 막는다). 그 외(임시
        소켓 등 비기본 엔드포인트)는 휘발 영역(state_base 옆 `.capture`)을 그대로 쓴다."""
        override = os.environ.get("PYTMUX_CAPTURE_DIR")
        if override:
            return os.path.join(override, self._capture_id())
        if self.sock_path == ipc.default_endpoint():
            return os.path.join(PROJECT_DIR, "captures", self._capture_id())
        return ipc.state_base(self.sock_path) + ".capture"

    def _capture_info(self, pane):
        """활성 패널의 캡처 파일 절대경로·크기(REC 클릭 팝업용). 캡처 off 면 (None,0)."""
        if not self.capture or pane is None:
            return None, 0
        path = os.path.join(self.capture_dir, f"pane-{pane.id}.log")
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return path, size

    def _pane_location(self, pane: Pane) -> str:
        """패널이 속한 탭을 'tab<idx>:<name>' 로 반환(메타 로그용)."""
        for sess in self.sessions.values():
            for i, tab in enumerate(sess.tabs):
                if pane in tab.window.panes():
                    return f"tab{i}:{tab.name}"
        return "tab?:?"

    def _capture_write(self, pane: Pane, data: bytes):
        """패널의 raw PTY 출력을 pane-<id>.log 에 무손실 append(재생/분석용)."""
        fh = self._capfiles.get(pane.id)
        if fh is None:
            try:
                os.makedirs(self.capture_dir, exist_ok=True)
                path = os.path.join(self.capture_dir, f"pane-{pane.id}.log")
                fh = open(path, "ab", buffering=0)
            except OSError:
                return
            self._capfiles[pane.id] = fh
            # 탭/패널 매핑을 별도 텍스트 로그에 기록(raw 로그는 오염하지 않음).
            try:
                meta = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                        f"pane-{pane.id} {self._pane_location(pane)} "
                        f"title={pane.title!r}\n")
                with open(os.path.join(self.capture_dir, "sessions.log"),
                          "a", encoding="utf-8") as mf:
                    mf.write(meta)
            except OSError:
                pass
        try:
            fh.write(data)
        except OSError:
            self._close_capfile(pane.id)

    def _close_capfile(self, pane_id: int):
        fh = self._capfiles.pop(pane_id, None)
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def _close_all_capfiles(self):
        for pid in list(self._capfiles):
            self._close_capfile(pid)

    def set_capture(self, value=None):
        """출력 캡처 토글. value 미지정 시 반전. 상태를 opts.json 에 영속."""
        self.capture = (not self.capture) if value is None else bool(value)
        self._save_opts()
        if not self.capture:        # 끄면 열린 파일을 닫음(켜면 lazy 재오픈)
            self._close_all_capfiles()
        return self.capture

    def set_claude_header(self, value=None):
        """Claude 프롬프트 헤더 전역 표시 토글. 상태를 opts.json 에 영속(#6 ③)."""
        self.claude_header = (not self.claude_header) if value is None \
            else bool(value)
        self._save_opts()
        return self.claude_header

    def set_single_border(self, value=None):
        """단일 패널 테두리 표시 토글. value 미지정 시 반전. opts.json 영속."""
        self.single_border = (not self.single_border) if value is None \
            else bool(value)
        self._save_opts()
        return self.single_border

    def set_coalesce_repaints(self, value=None):
        """alt-screen 리페인트 코얼레싱 토글(§10 대응 ②). value 미지정 시 반전.
        opts.json 영속. 클라 렌더에는 영향 없는 서버 내부 동작이라 status 불필요."""
        self.coalesce_repaints = (not self.coalesce_repaints) if value is None \
            else bool(value)
        self._save_opts()
        return self.coalesce_repaints

    def list_tab_layouts(self) -> list[str]:
        return sorted(self._load_slots().keys())

    def save_tab_layout(self, sess: Session, name: str) -> bool:
        """활성 탭의 윈도우+패널 레이아웃을 이름 슬롯으로 저장."""
        tab = sess.active_tab
        if not tab or not name:
            return False
        slots = self._load_slots()
        slots[name] = self._serialize_node(tab.window.root)
        self._save_slots(slots)
        return True

    def load_tab_layout(self, sess: Session, name: str,
                        new_tab: bool = False) -> bool:
        """저장된 레이아웃을 현재 탭에 덮어쓰거나(new_tab=False) 새 탭으로 연다."""
        spec = self._load_slots().get(name)
        if not spec:
            return False
        c = self.clients_of(sess)
        cols = c.cols if c else 80
        rows = c.rows if c else 24
        root = self._build_node(spec, cols, rows)
        win = Window(root)
        win._active = root if isinstance(root, Pane) else root.first_pane()
        win._fix_parents(root, None)
        if new_tab:
            idx = len(sess.tabs)
            sess.tabs.append(Tab(idx, name, win))
            sess.last_index = sess.active_index
            sess.active_index = idx
        else:
            old = sess.active_tab
            if old is None:
                return False
            for p in old.window.panes():   # 기존 패널 정리 후 교체
                self._destroy_pane_proc(p)
            old.window = win
        return True

    def handle_control(self, line: str):
        """외부 CLI(`pytmux cmd ...`)에서 보낸 명령을 서버 측에서 처리한다."""
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        if not parts:
            return "empty"
        c, args = parts[0], parts[1:]

        def opt(flag):
            return args[args.index(flag) + 1] if flag in args and \
                args.index(flag) + 1 < len(args) else None

        def first_int():
            for a in args:
                if a.lstrip("-").isdigit():
                    return int(a.lstrip("-"))
            return None
        tname = opt("-t")
        sess = self.sessions.get(tname) if tname else next(
            iter(self.sessions.values()), None)
        if c in ("new-session", "new"):
            self.new_session(80, 24, opt("-s"))
        elif sess is None:
            return "no session"
        elif c in ("new-window", "neww", "new-tab", "newt"):
            # -c <경로> 로 시작 디렉토리 지정(tmux 호환). 없으면 default-path 기본.
            self.new_window(sess, path=opt("-c"))
        elif c in ("kill-window", "killw", "kill-tab", "killt"):
            self.kill_window(sess)
        elif c in ("split-window", "splitw"):
            # -h = 가로(상/하), -v = 세로(좌/우), -c <경로> = 시작 디렉토리
            self.split_pane(sess, "tb" if "-h" in args else
                            ("lr" if "-v" in args else "tb"), path=opt("-c"))
        elif c in ("select-window", "selectw", "select-tab", "selectt"):
            i = first_int()
            if i is not None:
                self.select_window(sess, i)
        elif c in ("rename-window", "renamew", "rename-tab", "renamet"):
            self.rename_window(sess, " ".join(a for a in args
                                              if not a.startswith("-")))
        elif c in ("move-tab-left", "move-tab-right",
                   "move-tab-first", "move-tab-last"):
            self.move_current_tab(sess, c[len("move-tab-"):])
        elif c in ("layout-save", "save-tab-layout"):
            self.save_tab_layout(sess, " ".join(a for a in args
                                                 if not a.startswith("-")))
        elif c in ("layout-load", "load-tab-layout"):
            nm = " ".join(a for a in args if not a.startswith("-"))
            self.load_tab_layout(sess, nm, new_tab=("-n" in args))
        elif c in ("kill-session", "kills"):
            self.kill_session(tname or sess.name)
        elif c == "kill-server":
            self._notify_no_sessions()
            return "ok"
        elif c in ("restart-server", "restart"):
            # 작업 보존 재시작(re-exec). 셸/PTY 보존(docs/RESTART_SCENARIO.md).
            return "restarting" if self.restart_server() else "unsupported"
        elif c in ("send-keys", "send"):
            self._control_send_keys(sess, args)
        elif c in ("capture-output", "capture-toggle"):
            val = True if "on" in args else (False if "off" in args else None)
            return "on" if self.set_capture(val) else "off"
        elif c == "claude-header":
            val = True if "on" in args else (False if "off" in args else None)
            return "on" if self.set_claude_header(val) else "off"
        elif c in ("single-border", "pane-border"):
            val = True if "on" in args else (False if "off" in args else None)
            self.set_single_border(val)
            # 레이아웃(박스 유무)이 바뀌므로 아래 broadcast 로 떨어지게 한다.
        elif c in ("coalesce-repaints", "coalesce"):
            val = True if "on" in args else (False if "off" in args else None)
            return "on" if self.set_coalesce_repaints(val) else "off"
        elif c in ("auto-doc-clear", "auto-doc"):
            val = True if "on" in args else (False if "off" in args else None)
            return "on" if self.set_auto_doc_clear(val) else "off"
        elif c in ("claude-auto-mode", "auto-mode"):
            val = True if "on" in args else (False if "off" in args else None)
            return "on" if self.set_claude_auto_mode(val) else "off"
        else:
            return f"unknown: {c}"
        for cl in list(self.clients):
            asyncio.create_task(self._send_full(cl))
        return "ok"

    @staticmethod
    def _control_send_keys(sess: Session, args):
        sp = {"Enter": b"\r", "Tab": b"\t", "Space": b" ", "Escape": b"\x1b",
              "BSpace": b"\x7f"}
        win = sess.active_window
        if not win:
            return
        out = b""
        for a in (x for x in args if not x.startswith("-")):
            if a in sp:
                out += sp[a]
            elif a.startswith("C-") and len(a) == 3 and a[2].isalpha():
                out += bytes([ord(a[2].lower()) - 96])
            else:
                out += a.encode("utf-8")
        if out:
            ap = win.active_pane
            try:
                if ap is not None and ap.pty is not None:
                    ap.pty.write(out)
            except OSError:
                pass

    def pipe_pane(self, sess: Session, cmd: str):
        win = sess.active_window
        if not win or not win.active_pane:
            return
        p = win.active_pane
        if p.pipe_proc:  # 토글/재시작: 기존 파이프 종료
            try:
                p.pipe_proc.stdin.close()
            except Exception:
                pass
            try:
                p.pipe_proc.terminate()
            except Exception:
                pass
            p.pipe_proc = None
        if cmd:
            try:
                p.pipe_proc = subprocess.Popen(proc.shell_argv(cmd),
                                               stdin=subprocess.PIPE)
            except Exception:
                p.pipe_proc = None

    def capture_pane(self, sess: Session, full=False):
        win = sess.active_window
        if not win or not win.active_pane:
            return 0
        p = win.active_pane
        texts = self._pane_text_lines(p)
        if not full:
            texts = texts[-p.screen.lines:]
        text = "\n".join(texts).rstrip("\n")
        self.set_buffer(text)
        return len(text)

    def clear_history(self, sess: Session):
        win = sess.active_window
        if not win or not win.active_pane:
            return
        p = win.active_pane
        try:  # 메인 스크롤백을 비운다(대체 화면 중이어도)
            p._main.history.top.clear()
            p._main.history.bottom.clear()
        except Exception:
            pass
        p.scroll = 0
        p._match_abs = None
        p.dirty = True

    def _buffers_msg(self):
        return {"t": "buffers", "items": [
            {"i": i, "preview": (b.splitlines()[0] if b.splitlines() else "")[:50]}
            for i, b in enumerate(self.buffers)]}

    def search_pane(self, sess: Session, query, direction="up"):
        win = sess.active_window
        if not win or not win.active_pane:
            return
        p = win.active_pane
        if query:  # 새 검색어 → 현재 뷰부터 다시
            p.search_query = query
            p._match_abs = None
        q = p.search_query
        if not q:
            return
        texts = self._pane_text_lines(p)
        total = len(texts)
        lines = p.screen.lines
        if p._match_abs is None:
            cur = (total - p.scroll) - 1  # 현재 뷰 하단
        else:
            cur = p._match_abs
        ql = q.lower()
        rng = range(cur - 1, -1, -1) if direction == "up" else range(cur + 1, total)
        found = next((i for i in rng if ql in texts[i].lower()), None)
        if found is None:
            return
        p._match_abs = found
        hist = p._history_len()
        target_start = max(0, found - lines // 2)
        p.scroll = max(0, min(hist, hist - target_start))
        p.dirty = True

    def select_pane_cycle(self, sess: Session):
        win = sess.active_window
        if not win:
            return
        ps = win.panes()
        if win.active_pane in ps:
            i = ps.index(win.active_pane)
            win.active_pane = ps[(i + 1) % len(ps)]

    def resize_split(self, sess: Session, sid: int, ratio: float):
        win = sess.active_window
        if not win:
            return
        sp = win.split_by_id(sid)
        if sp:
            sp.ratio = max(0.05, min(0.95, ratio))

    def resize_dir(self, sess: Session, direction: str, cells: int = 3):
        win = sess.active_window
        if not win:
            return
        orient = "lr" if direction in ("left", "right") else "tb"
        node = win.active_pane
        while node.parent is not None:
            p = node.parent
            if p.orient == orient:
                avail = (p.rect[2] if orient == "lr" else p.rect[3]) - 1
                if avail > 0:
                    d = cells / avail
                    if direction in ("left", "up"):
                        p.ratio = max(0.05, min(0.95, p.ratio - d))
                    else:
                        p.ratio = max(0.05, min(0.95, p.ratio + d))
                return
            node = p

    # ---- 클라이언트 통신 ----
    def clients_of(self, sess: Session):
        for c in self.clients:
            if c.session is sess:
                return c
        return None

    def _session_of_pane(self, pane: Pane) -> Session | None:
        """패널이 속한 세션을 찾는다(어느 탭/윈도우든)."""
        for sess in self.sessions.values():
            for t in sess.tabs:
                if pane in t.window.panes():
                    return sess
        return None

    def _session_size(self, sess: Session):
        """세션에 attach 한 모든 클라이언트를 수용하도록 최소 크기를 쓴다(미러링)."""
        cs = [c for c in self.clients if c.session is sess]
        if not cs:
            return 80, 24
        return (max(MIN_W, min(c.cols for c in cs)),
                max(MIN_H, min(c.rows for c in cs)))

    def _should_reserve_header(self, p) -> bool:
        """클라이언트가 이 패널에 Claude 프롬프트 헤더를 그릴지 여부(#1). 그러면
        내용 영역에서 한 행을 빼(헤더가 차지) 헤더가 1행짜리 패널 내용을 가리지
        않게 한다. 전역 옵션 claude_header + 그 패널이 Claude 이고 표시할 프롬프트가
        있을 때 참. (클라 전용 _claude_hidden_panes 팝업 숨김은 서버가 모르므로 그
        경우 예약 행은 비워둔다 — 토글 시 터미널 리플로우를 피하는 이점도 있다.)

        Claude 존재 판정은 raw `_claude` 가 아니라 **디바운스된 `_hdr_claude`**
        를 쓴다. raw 값은 footer 가 한 프레임 안 잡히면 None 으로 깜빡여(특히
        ssh/ConPTY) 예약이 매 프레임 토글→PTY ±1 행 리사이즈 반복→원격 화면이
        한 줄씩 떨리는데, 디바운스가 그 떨림을 없앤다(_scan_claude 에서 갱신)."""
        return bool(self.claude_header and p._hdr_claude and p.last_prompt)

    def _layout_msg(self, sess: Session, cols: int = None, rows: int = None):
        win = sess.active_window
        if not win:
            return None
        if cols is None or rows is None:
            cols, rows = self._session_size(sess)
        # 모든 패널 PTY 크기를 레이아웃에 맞춰 갱신
        panes, divs = win.compute_layout(0, 0, cols, rows)
        # 패널이 둘 이상이면 각 패널을 테두리 박스로 감싼다(활성=파랑, 비활성=회색).
        # 패널이 하나뿐이면 single_border 옵션이 켜져 있을 때만 아웃라인을 그린다
        # (off 면 단일 패널이 화면 전체를 내용으로 사용 — 사용자 요청).
        bordered = len(panes) >= 2 or self.single_border
        pane_msgs, titlebars = [], []
        for p in panes:
            x, y, w, h = p.rect
            box = None
            if bordered and w >= 3 and h >= 3:
                # 박스 테두리(상/하/좌/우) 안쪽이 내용 영역
                cx, cy, cw, ch = x + 1, y + 1, w - 2, h - 2
                box = [x, y, w, h]
            elif win.border_status and h > 1:
                cx, cy, cw, ch = x, y + 1, w, h - 1
                titlebars.append({"x": x, "y": y, "w": w, "title": p.title,
                                  "active": p is win.active_pane})
            else:
                cx, cy, cw, ch = x, y, w, h
            # Claude 헤더가 그려질 패널이면 내용 영역 맨 윗 한 행을 헤더에 양보한다
            # (#1). 내용은 cy+1 부터, 높이 -1. 헤더는 클라가 예약된 행(cy)에 그린다.
            hdr = self._should_reserve_header(p) and ch > 1
            if hdr:
                cy += 1
                ch -= 1
            p._hdr_reserved = hdr
            p.resize(cw, ch)
            p._mouse_sent = (p.mouse_track, p.mouse_sgr)
            pane_msgs.append({"id": p.id, "x": cx, "y": cy, "w": cw, "h": ch,
                              "title": p.title, "box": box,
                              "active": p is win.active_pane,
                              "claude_hdr": hdr,
                              "mouse": p.mouse_track, "mouse_sgr": p.mouse_sgr})
        return {
            "t": "layout",
            "cols": cols, "rows": rows,
            "panes": pane_msgs,
            "dividers": divs,
            "titlebars": titlebars,
            "bordered": bordered,
            "border_status": bool(win.border_status),
            "active": win.active_pane.id,
            "popup": self._popup_layout(sess, cols, rows),
        }

    # 패널이 원격 세션을 돌리는지 판정하는 fg 명령 이름들(소문자).
    _REMOTE_CMDS = {"ssh", "mosh", "mosh-client", "autossh", "sshpass",
                    "telnet", "et", "eternal-terminal", "kitten"}

    def _pane_overview(self, pane):
        """트리/개요용 패널 1건 정보: id·제목·fg 앱·로컬/원격·Claude 상태/사용량."""
        cmd = self._fg_command(pane.master_fd) or ""
        return {"id": pane.id, "title": (pane.title or "").strip(),
                "cmd": cmd, "remote": cmd.lower() in self._REMOTE_CMDS,
                "claude": pane._claude, "usage": pane._claude_usage}

    def _tree_msg(self):
        return {"t": "tree", "current": None, "sessions": [
            {"name": s.name, "active": (s is None),
             "windows": [{"index": t.index, "name": t.name,
                          "active": (t is s.active_tab),
                          "panes": [self._pane_overview(p)
                                    for p in t.window.panes()]}
                         for t in s.tabs]}
            for s in self.sessions.values()]}

    def _scan_claude(self, sess, win) -> bool:
        """모든 탭 패널의 Claude 상태/사용량을 화면 텍스트(screen.display)로 갱신
        하고, **비활성 탭**의 busy→idle(작업 완료) 전이를 감지해 `has_claude_done`
        를 세운다(#22). 활성 윈도우만이 아니라 전체를 훑는 이유는 백그라운드 탭의
        완료를 알리기 위해서다. 상태가 바뀌면 True 반환."""
        changed = False
        for t in sess.tabs:
            w = t.window
            for p in w.panes():
                txt = "\n".join(p.screen.display)
                old_cl = p._claude
                new_cl = claude_state(txt)
                # 사용량 표시는 Claude 세션이 살아 있는 동안 유지한다(#5): 화면에서
                # 토큰 문구가 잠시 사라져도(스크롤 등) 마지막 값을 보존하고, 세션이
                # 끝나면(claude None) 비운다.
                if new_cl:
                    u = claude_usage(txt)
                    new_use = u if u is not None else p._claude_usage
                else:
                    new_use = None
                if new_cl != p._claude or new_use != p._claude_usage:
                    p._claude = new_cl
                    p._claude_usage = new_use
                    changed = True
                # 헤더 예약(#1)용 디바운스: Claude 로 보이면 즉시 True, 아니면 연속
                # _HDR_CLAUDE_MISS 프레임 뒤에야 False. raw `_claude` 깜빡임이 헤더
                # 예약을 토글해 PTY 가 ±1 행 리사이즈를 반복(원격 화면 한 줄 떨림)
                # 하는 것을 막는다(_should_reserve_header 가 _hdr_claude 를 읽음).
                if new_cl:
                    p._hdr_claude = True
                    p._hdr_claude_miss = 0
                elif p._hdr_claude:
                    p._hdr_claude_miss += 1
                    if p._hdr_claude_miss >= _HDR_CLAUDE_MISS:
                        p._hdr_claude = False
                # 토큰 누계(#3): 새 Claude 세션 시작(None→Claude) 시 리셋, 매 프레임
                # 현재 응답 running 토큰을 step 으로 접어 응답별 peak 를 누계에 확정.
                # (확정 시점 committed>0 은 #7 의 영속 로깅 이벤트로도 쓰인다.)
                committed = 0
                if new_cl and not old_cl:
                    tokens.reset(p._tok_state)
                    # 새 Claude 세션 경계: 세션 id 부여, 계정 재감지(수동 지정은 유지).
                    self._claude_session_seq += 1
                    p._claude_session_id = self._claude_session_seq
                    if not p._claude_account_manual:
                        p._claude_account = None
                if new_cl:
                    # 계정 단서를 매 프레임 갱신(마지막 본 값 유지; 수동 지정 우선).
                    if not p._claude_account_manual:
                        acct = claude_account(txt)
                        if acct and acct != p._claude_account:
                            p._claude_account = acct
                    running = tokens.parse_running_tokens(txt)
                    committed = tokens.step(p._tok_state, running,
                                            new_cl == "busy")
                    if committed > 0:
                        self._log_tokens(sess, t, p, committed)
                    if p._tok_state["total"] != p._session_tokens:
                        p._session_tokens = p._tok_state["total"]
                        changed = True
                elif p._session_tokens:
                    p._session_tokens = 0
                    p._tok_state["peak"] = 0
                    p._tok_state["total"] = 0
                    changed = True
                # 큐된 프롬프트 승격(#4): 헤더는 "지금 처리 중인 프롬프트"를 보여야
                # 한다. busy 중 입력한 프롬프트는 _track_prompt 가 pending_prompts 에
                # 쌓아 뒀다(last_prompt 즉시 안 바꿈). 응답 경계 — ① busy→non-busy(응답
                # 종료) 또는 ② 연속 busy 중 running 토큰 급감(committed>0 = 다음 응답
                # 시작) — 에서 큐의 다음 프롬프트를 last_prompt 로 승격한다.
                if p._claude is None:
                    if p.pending_prompts:
                        p.pending_prompts.clear()
                else:
                    boundary = (old_cl == "busy" and new_cl != "busy")
                    if not boundary and committed > 0 and new_cl == "busy":
                        boundary = True
                    if boundary and p.pending_prompts:
                        p.last_prompt = p.pending_prompts.pop(0)
                        changed = True
                # 데스크탑 앱 원격제어 등 입력 경로(_track_prompt)를 안 거친 프롬프트
                # 반영(§10 #19): 화면 transcript 에서 최신 사용자 프롬프트를 best-effort
                # 추출해, 입력으로 안 잡힌(last_prompt 와 다르고 최근 히스토리에도 없는)
                # 경우에만 헤더/히스토리를 갱신한다. 로컬 입력은 _track_prompt 가 제출
                # 즉시 히스토리에 남기므로 여기 가드(히스토리 멤버십)에 걸려 중복되지
                # 않는다. 화면 파싱은 best-effort 라 보수적으로 매칭한다.
                if new_cl:
                    sp = claude_prompt(txt)
                    if (sp and sp != p.last_prompt
                            and sp not in p.prompt_history[-5:]):
                        p.last_prompt = sp
                        p.prompt_history.append(sp)
                        if len(p.prompt_history) > 200:
                            p.prompt_history = p.prompt_history[-200:]
                        changed = True
                # 비활성 탭에서 처리(busy)→대기(idle) 전이 = 작업 완료. limit 은
                # "대기"가 아니므로 대상 아님. 플리커 방지(§10 #18): raw busy→idle 즉시
                # 대신, busy 를 본 적이 있고(idle 진입) idle 이 _DONE_IDLE_FRAMES 프레임
                # 연속 안정될 때만 완료로 친다(한 프레임 깜빡임에 녹색 오검출 방지).
                if new_cl == "idle":
                    p._idle_frames += 1
                else:
                    p._idle_frames = 0
                    if new_cl == "busy":
                        p._was_busy = True   # 작업 중이었음 → 다음 안정 idle 이 '완료'
                if (w is not win and t.monitor_claude and not t.has_claude_done
                        and p._was_busy and new_cl == "idle"
                        and p._idle_frames >= _DONE_IDLE_FRAMES):
                    t.has_claude_done = True
                    p._was_busy = False
                    changed = True
                # 자동 doc→/clear(§10): idle 이탈(busy/limit/종료) 시 무장된 타이머를
                # 즉시 해제한다 — idle 이 끊기면 "N초 지속" 전제가 깨진다. 권한모드
                # 자동전환 시도 카운터도 idle 이탈 시 리셋(다음 idle 진입에 다시 시도).
                if new_cl != "idle":
                    self._adc_disarm(p)
                    p._cam_tries = 0
                    p._cam_last = None
                # 권한모드 자동 오토모드 전환(§10): idle 이고 토글이 켜졌으면 footer 의
                # 권한모드가 auto 가 아닐 때 shift+tab 을 폐루프로 순환 주입한다.
                elif self.claude_auto_mode:
                    self._maybe_auto_mode(p, txt)
                # 프롬프트 단위 클리어 모드(#9) + 자동 doc→/clear(§10): busy→idle(응답
                # 완료) 경계에서 상태기계를 전진한다. 수동 모드(prompt_clear_mode)와
                # 자동 시퀀스(_adc_active)가 같은 _pc_phase 기계를 공유한다.
                # 진행 중이 아니면서 자동 모드가 켜져 있으면 idle 진입 시점에 무장만
                # 한다(실제 발화는 N초 뒤 _adc_fire).
                if old_cl == "busy" and new_cl == "idle":
                    if p.prompt_clear_mode or p._adc_active:
                        self._pc_advance(p)
                        if p._adc_active and p._pc_phase is None:
                            p._adc_active = False   # 자동 doc→clear 시퀀스 완료
                    elif self.auto_doc_clear:
                        self._adc_arm(p)
        return changed

    @staticmethod
    def _tab_claude(tab) -> str | None:
        """탭 내 패널들의 Claude 상태를 합쳐 대표 상태 반환(limit>busy>idle)."""
        pri = {"limit": 3, "busy": 2, "idle": 1}
        best, score = None, 0
        for p in tab.window.panes():
            s = p._claude
            if s and pri[s] > score:
                best, score = s, pri[s]
        return best

    def _account_token_total(self, ap) -> int:
        """활성 패널의 Claude 계정을 키로, 그 계정에 속한 모든 패널(전체 세션 순회)
        의 세션 누적 토큰을 합산한다(§10 계정별 합계). 계정 추정 전이면 활성 패널
        단독 누계로 폴백하고, 활성 패널이 Claude 가 아니면 0 을 보낸다(이 경우
        클라이언트가 마지막 비어있지 않은 값을 유지해 표시가 사라지지 않게 한다)."""
        if not ap:
            return 0
        acct = ap._claude_account
        if acct:
            return sum(p._session_tokens for p in self._all_panes()
                       if p._claude_account == acct)
        if ap._claude:
            return ap._session_tokens
        return 0

    def _status_msg(self, sess: Session):
        win = sess.active_window
        cap_path, cap_size = self._capture_info(win.active_pane if win else None)
        return {
            "t": "status",
            "session": sess.name,
            "windows": [{"index": t.index, "name": t.name,
                         "active": (i == sess.active_index),
                         "bell": t.has_bell, "activity": t.has_activity,
                         "claude_done": t.has_claude_done,
                         "claude": self._tab_claude(t)}
                        for i, t in enumerate(sess.tabs)],
            # 활성 윈도우 패널별 Claude 상태/마지막 프롬프트(헤더용)
            "panes_claude": [{"id": p.id, "claude": p._claude,
                              "prompt": p.last_prompt,
                              "history": p.prompt_history[-30:]}
                             for p in (win.panes() if win else [])],
            "active_pane": win.active_pane.id if win else None,
            # 활성 패널이 Claude 면 토큰/컨텍스트 사용량(best-effort)
            "claude_usage": (win.active_pane._claude_usage
                             if win and win.active_pane
                             and win.active_pane._claude else None),
            # 활성 패널 계정 기준 — 그 계정에 속한 모든 세션/패널의 세션 누적 토큰을
            # 합산(§10 토큰 지속표시·계정별 합계). 계정 식별자도 함께 보내 표시에 곁들임.
            "claude_tokens": self._account_token_total(
                win.active_pane if win else None),
            "claude_account": (win.active_pane._claude_account
                               if win and win.active_pane else None),
            "zoomed": bool(win.zoomed) if win else False,
            "sync": bool(win.sync) if win else False,
            "pane_title": win.active_pane.title if win and win.active_pane else "",
            "autoresume": bool(win.active_pane.autoresume)
            if win and win.active_pane else False,
            "prompt_clear": bool(win.active_pane.prompt_clear_mode)
            if win and win.active_pane else False,
            # 프롬프트 단위 클리어 큐(#4): 활성 패널에 쌓인 명령들(표시·목록용)
            "prompt_clear_queue": (list(win.active_pane.prompt_clear_queue)
                                   if win and win.active_pane else []),
            "capture": self.capture,
            "capture_path": cap_path,
            "capture_size": cap_size,
            "claude_header": self.claude_header,
            "single_border": self.single_border,
        }

    async def _send_full(self, client: ClientConn):
        sess = client.session
        if not sess:
            return
        lay = self._layout_msg(sess)  # 세션 공유 크기(최소)로 계산
        if not lay:
            return
        await write_msg(client.writer, lay)
        win = sess.active_window
        for p in win.panes():
            rows, cursor = p.render(p is win.active_pane)
            p.dirty = False
            await write_msg(client.writer, {"t": "screen", "pane": p.id,
                                            "rows": rows, "cursor": cursor})
        # 팝업 패널 화면도 함께(트리에 없으므로 별도로 보냄). 팝업은 항상 포커스라
        # 커서를 그린다(render(True)).
        if sess.popup and sess.popup.get("pane") is not None:
            pp = sess.popup["pane"]
            rows, cursor = pp.render(True)
            pp.dirty = False
            await write_msg(client.writer, {"t": "screen", "pane": pp.id,
                                            "rows": rows, "cursor": cursor})
        await write_msg(client.writer, self._status_msg(sess))

    def _broadcast_session(self, sess: Session):
        """구조 변경 후 해당 세션의 모든 클라이언트에 전체 상태를 다시 보낸다."""
        for c in self.clients:
            if c.session is sess:
                asyncio.create_task(self._send_full(c))

    def _notify_no_sessions(self):
        for c in self.clients:
            asyncio.create_task(write_msg(c.writer, {"t": "bye"}))
        self.running = False
        if self.loop:
            self.loop.call_later(0.2, self.shutdown)

    # ---- flush 루프 ----
    async def _flush_loop(self):
        interval = 1.0 / FLUSH_HZ
        while self.running:
            await asyncio.sleep(interval)
            for sess in list(self.sessions.values()):
                win = sess.active_window
                if not win:
                    continue
                clients = [c for c in self.clients if c.session is sess]
                if not clients:
                    continue
                status_changed = False
                for p in win.panes():
                    if not p.dirty:
                        continue
                    rows, cursor = p.render(p is win.active_pane)
                    p.dirty = False
                    msg = {"t": "screen", "pane": p.id,
                           "rows": rows, "cursor": cursor}
                    for c in clients:
                        await write_msg(c.writer, msg)
                # 라이브 PTY 팝업 패널(트리 밖)도 dirty 면 스트리밍한다.
                pu = sess.popup
                if pu and pu.get("pane") is not None and pu["pane"].dirty:
                    pp = pu["pane"]
                    rows, cursor = pp.render(True)
                    pp.dirty = False
                    pmsg = {"t": "screen", "pane": pp.id,
                            "rows": rows, "cursor": cursor}
                    for c in clients:
                        await write_msg(c.writer, pmsg)
                # Claude Code 상태/사용량 갱신(+ 비활성 탭 완료 감지, #22)
                if self._scan_claude(sess, win):
                    status_changed = True
                # 활동/벨 모니터링: 비활성 윈도우의 출력/BEL 을 플래그로
                for t in sess.tabs:
                    w = t.window
                    for p in w.panes():
                        if w is win:
                            p._activity = p._bell = False  # 보고 있는 탭
                            continue
                        if p._bell:
                            p._bell = False
                            if t.monitor_bell and not t.has_bell:
                                t.has_bell = True
                                status_changed = True
                        if p._activity:
                            p._activity = False
                            if t.monitor_activity and not t.has_activity:
                                t.has_activity = True
                                status_changed = True
                # Claude 헤더 행 예약(#1) 변동: 프롬프트가 처음 떠 헤더가 생기거나
                # Claude 가 끝나 헤더가 사라지면 내용 영역을 ±1 행 해야 한다. 레이아웃을
                # 다시 보내 PTY 리사이즈 + 새 geometry 를 반영(_send_full 이 status 포함).
                if any(self._should_reserve_header(p) != p._hdr_reserved
                       for p in win.panes()):
                    for c in clients:
                        await self._send_full(c)
                    continue
                if status_changed:
                    smsg = self._status_msg(sess)
                    for c in clients:
                        await write_msg(c.writer, smsg)

    # ---- 명령 처리 ----
    async def _handle_cmd(self, client: ClientConn, msg: dict):
        sess = client.session
        if not sess:
            return
        action = msg.get("action")
        if action == "split":
            self.split_pane(sess, msg.get("orient", "lr"), path=msg.get("path"))
        elif action == "kill_pane":
            pane = sess.active_window.active_pane if sess.active_window else None
            if pane:
                self.kill_pane(sess, pane)
                return  # kill 은 트리 콜백에서 broadcast
        elif action == "select_pane":
            self.select_pane_dir(sess, msg.get("dir"))
        elif action == "select_pane_id":
            win = sess.active_window
            p = win.pane_by_id(msg.get("id")) if win else None
            if p:
                win.active_pane = p
        elif action == "cycle_pane":
            self.select_pane_cycle(sess)
        elif action == "last_pane":
            self.last_pane(sess)
        elif action == "set_sync":
            self.set_sync(sess, msg.get("value"))
        elif action == "set_pane_title":
            self.set_pane_title(sess, str(msg.get("title", "")))
        elif action == "set_border_status":
            self.set_border_status(sess, msg.get("value"))
        elif action == "respawn_pane":
            self.respawn_pane(sess)
        elif action == "search":
            self.search_pane(sess, msg.get("query"), msg.get("direction", "up"))
        elif action == "set_buffer":
            self.set_buffer(str(msg.get("text", "")))
            return
        elif action == "paste_buffer":
            self.paste_buffer(sess, int(msg.get("index", 0)))
            return
        elif action == "paste":
            self.paste_text(sess, str(msg.get("text", "")))
            return
        elif action == "request_buffers":
            await write_msg(client.writer, self._buffers_msg())
            return
        elif action == "clear_history":
            self.clear_history(sess)
            await self._send_full(client)
            return
        elif action == "capture_pane":
            n = self.capture_pane(sess, bool(msg.get("full")))
            await write_msg(client.writer, {"t": "captured", "chars": n})
            return
        elif action == "pipe_pane":
            self.pipe_pane(sess, str(msg.get("cmd", "")))
            return
        elif action == "popup_open":
            self.popup_open(sess, str(msg.get("cmd", "")),
                            want_w=msg.get("w"), want_h=msg.get("h"),
                            title=msg.get("title"))
            return  # popup_open 이 broadcast
        elif action == "popup_close":
            self.popup_close(sess)
            return  # popup_close 가 broadcast
        elif action == "save_layout":
            ok = self.save_layout()
            await write_msg(client.writer, {"t": "captured",
                                            "chars": 1 if ok else 0})
            return
        elif action == "restore_layout":
            self.restore_layout()
            await self._send_full(client)
            return
        elif action == "request_tree":
            await write_msg(client.writer, self._tree_msg())
            return
        elif action == "request_token_log":
            # 영속 토큰 로그(최근 N 줄)를 클라이언트로. 클라가 usagelog 로 시간/일/월×
            # 계정 집계해 팝업에 표시(라운드트립 없이 버킷/계정 전환).
            recs = usagelog.read(self.tokens_log_path,
                                 limit=int(msg.get("limit", 5000)))
            await write_msg(client.writer, {"t": "token_log", "records": recs})
            return
        elif action == "set_claude_account":
            self.set_claude_account(sess, str(msg.get("name", "")))
            return
        elif action == "list_layouts":
            await write_msg(client.writer, {"t": "layouts",
                                            "names": self.list_tab_layouts()})
            return
        elif action == "save_tab_layout":
            ok = self.save_tab_layout(sess, str(msg.get("name", "")).strip())
            await write_msg(client.writer, {"t": "captured",
                                            "chars": 1 if ok else 0})
            return
        elif action == "load_tab_layout":
            if self.load_tab_layout(sess, str(msg.get("name", "")).strip(),
                                    new_tab=bool(msg.get("new"))):
                for c in [x for x in self.clients if x.session is sess]:
                    await self._send_full(c)
            return
        elif action == "set_autoresume":
            self.set_autoresume(sess, value=msg.get("value"),
                                msg=msg.get("msg"))
        elif action == "resize":
            self.resize_split(sess, msg.get("split_id"), msg.get("ratio", 0.5))
        elif action == "resize_dir":
            self.resize_dir(sess, msg.get("dir"), msg.get("cells", 3))
        elif action == "new_window":
            self.new_window(sess, path=msg.get("path"))
        elif action == "next_window":
            self.select_window(sess, (sess.active_index + 1) % len(sess.tabs))
        elif action == "prev_window":
            self.select_window(sess, (sess.active_index - 1) % len(sess.tabs))
        elif action == "select_window":
            self.select_window(sess, msg.get("index", 0))
        elif action == "last_window":
            self.last_window(sess)
        elif action == "move_window":
            self.move_window(sess, int(msg.get("index", 0)))
        elif action == "swap_window":
            self.swap_window(sess, int(msg.get("index", 0)))
        elif action == "move_tab":
            self.move_tab(sess, int(msg.get("index", 0)),
                          int(msg.get("to", 0)))
        elif action == "move_current_tab":
            self.move_current_tab(sess, str(msg.get("where", "")))
        elif action == "zoom":
            self.toggle_zoom(sess)
        elif action == "select_layout":
            self.select_layout(sess, msg.get("preset", "tiled"))
        elif action == "cycle_layout":
            self.cycle_layout(sess)
        elif action == "rotate":
            self.rotate_panes(sess, bool(msg.get("forward", True)))
        elif action == "swap_pane":
            self.swap_pane(sess, bool(msg.get("forward", True)))
        elif action == "swap_pane_to":
            self.swap_pane_ids(sess, int(msg.get("id", -1)),
                               int(msg.get("to_id", -1)))
        elif action == "break_pane":
            self.break_pane(sess)
        elif action == "join_pane":
            self.join_pane(sess, orient=msg.get("orient", "tb"))
        elif action == "rename_window":
            self.rename_window(sess, str(msg.get("name", "")).strip())
        elif action == "set_auto_rename":
            self.set_auto_rename(sess, msg.get("value"))
        elif action == "set_monitor":
            self.set_monitor(sess, msg.get("which", "activity"), msg.get("value"))
        elif action == "set_capture":
            self.set_capture(msg.get("value"))
        elif action == "set_claude_header":
            self.set_claude_header(msg.get("value"))
        elif action == "set_single_border":
            self.set_single_border(msg.get("value"))
        elif action == "set_coalesce":
            self.set_coalesce_repaints(msg.get("value"))
        elif action == "set_auto_doc_clear":
            self.set_auto_doc_clear(msg.get("value"))
        elif action == "set_claude_auto_mode":
            self.set_claude_auto_mode(msg.get("value"))
        elif action == "set_prompt_clear":
            self.set_prompt_clear(sess, msg.get("value"))
        elif action == "set_prompt_clear_message":
            self.set_prompt_clear_message(str(msg.get("msg", "")))
            return
        elif action == "pc_queue_add":
            self.pc_queue_add(sess, str(msg.get("cmd", "")))
        elif action == "pc_queue_clear":
            self.pc_queue_clear(sess)
        elif action == "kill_window":
            self.kill_window(sess)
            if sess.name not in self.sessions:
                # 세션의 마지막 윈도우였음 → 다른 세션으로 옮기거나 종료
                if self.sessions:
                    client.session = next(iter(self.sessions.values()))
                    await self._send_full(client)
                else:
                    self._notify_no_sessions()
                return
            await self._send_full(client)
            return
        elif action == "rename_session":
            self.rename_session(sess, str(msg.get("name", "")).strip())
        elif action == "new_session":
            new = self.new_session(client.cols, client.rows,
                                   str(msg.get("name", "")).strip() or None)
            client.session = new
            await self._send_full(client)
            return
        elif action == "switch_session":
            self.switch_session(client, str(msg.get("name", "")).strip())
            await self._send_full(client)
            return
        elif action == "detach_others":
            for c in list(self.clients):
                if c is not client and c.session is sess:
                    await write_msg(c.writer, {"t": "bye"})
            return
        elif action == "kill_session":
            name = str(msg.get("name") or sess.name)
            self.kill_session(name)
            if not self.sessions:
                self._notify_no_sessions()
                return
            for c in self.clients:
                await self._send_full(c)
            return
        elif action == "kill_server":
            self._notify_no_sessions()
            return
        elif action == "restart_server":
            # 작업 보존 재시작(re-exec). 셸/PTY 보존(docs/RESTART_SCENARIO.md).
            self.restart_server()
            return
        else:
            return
        await self._send_full(client)

    async def handle_client(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter):
        first = await read_msg(reader)
        if first is None:
            writer.close()
            return
        t = first.get("t")
        if t == "list":
            await write_msg(writer, {"t": "list", "sessions": [
                {"name": s.name, "windows": len(s.tabs),
                 "panes": sum(len(t.window.panes()) for t in s.tabs)}
                for s in self.sessions.values()]})
            writer.close()
            return
        if t == "kill-server":
            await write_msg(writer, {"t": "ok"})
            writer.close()
            self._notify_no_sessions()
            return
        if t == "control":
            result = self.handle_control(first.get("line", ""))
            await write_msg(writer, {"t": "ok", "result": result})
            writer.close()
            return
        if t != "hello":
            writer.close()
            return

        client = ClientConn(writer)
        client.cols = max(MIN_W, int(first.get("cols", 80)))
        client.rows = max(MIN_H, int(first.get("rows", 24)))
        client.session = self.get_or_create_session(
            first.get("session"), client.cols, client.rows)
        self.clients.append(client)
        # 새 클라이언트가 붙으면 공유 크기가 바뀔 수 있어 같은 세션 전체를 갱신
        for c in [x for x in self.clients if x.session is client.session]:
            await self._send_full(c)

        try:
            while self.running:
                msg = await read_msg(reader)
                if msg is None:
                    break
                mt = msg.get("t")
                if mt == "ping":
                    # 네트워크 응답성 측정(§10): 클라가 RTT 를 재도록 즉시 echo.
                    await write_msg(client.writer, {"t": "pong",
                                                    "ts": msg.get("ts")})
                elif mt == "input":
                    self._handle_input(client, msg)
                elif mt == "resize":
                    client.cols = max(MIN_W, int(msg.get("cols", 80)))
                    client.rows = max(MIN_H, int(msg.get("rows", 24)))
                    # 미러링: 세션 공유 크기가 바뀌므로 모든 클라이언트 갱신
                    for c in [x for x in self.clients if x.session is client.session]:
                        await self._send_full(c)
                elif mt == "scroll":
                    self._handle_scroll(client, msg)
                elif mt == "cmd":
                    await self._handle_cmd(client, msg)
        finally:
            sess = client.session
            if client in self.clients:
                self.clients.remove(client)
            try:
                writer.close()
            except Exception:
                pass
            # 미러링: 남은 클라이언트는 공유 크기가 커질 수 있으니 갱신
            if sess and sess in self.sessions.values():
                for c in [x for x in self.clients if x.session is sess]:
                    await self._send_full(c)

    def _handle_input(self, client: ClientConn, msg: dict):
        sess = client.session
        win = sess.active_window if sess else None
        if not win:
            return
        # 팝업이 열려 있고 입력 대상이 팝업 패널이면 그 PTY 로만 직접 보낸다
        # (트리 밖이라 pane_by_id 로는 못 찾음; 동기화/프롬프트추적도 제외).
        pid = msg.get("pane")
        if sess.popup and sess.popup.get("pane") is not None \
                and pid == sess.popup["pane"].id:
            pp = sess.popup["pane"]
            try:
                if pp.pty is not None:
                    pp.pty.write(base64.b64decode(msg.get("data", "")))
            except OSError:
                pass
            return
        p = win.pane_by_id(pid) or win.active_pane
        data = base64.b64decode(msg.get("data", ""))
        # 마우스 패스스루: 커서 아래 패널 PTY 로만 raw 전달. 입력 동기화 대상이
        # 아니고(위치 기반), 프롬프트 추적/scroll 복귀도 건드리지 않는다.
        if msg.get("mouse"):
            try:
                if p.pty is not None:
                    p.pty.write(data)
            except OSError:
                pass
            return
        self._track_prompt(p, data)   # 마지막 입력 프롬프트 추적(Claude 헤더용)
        self._adc_disarm(p)   # 사용자 입력 = 활동 중 → 자동 doc→/clear 발화 취소(§10)
        # 입력 동기화 시 윈도우 내 모든 패널에 동일 입력 전달
        targets = win.panes() if win.sync else [p]
        for t in targets:
            if t.scroll != 0 or t._match_abs is not None:
                t.scroll = 0  # 입력 시작 시 live 로 복귀(R6)
                t._match_abs = None
                t.dirty = True
            try:
                if t.pty is not None:
                    t.pty.write(data)
            except OSError:
                pass

    @staticmethod
    def _track_prompt(pane: Pane, data: bytes):
        """입력 바이트에서 현재 줄을 누적하고 Enter 시 last_prompt 로 확정한다.
        CSI/ESC 시퀀스는 건너뛰고(화살표 등), bracketed paste 본문은 포함한다."""
        text = data.decode("utf-8", "ignore")
        buf = pane._inbuf
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "\x1b":                 # ESC: 제어 시퀀스 건너뜀
                i += 1
                if i < n and text[i] == "[":
                    i += 1
                    while i < n and not (0x40 <= ord(text[i]) <= 0x7e):
                        i += 1
                    i += 1
                else:
                    i += 1
                continue
            if ch in ("\r", "\n"):
                line = buf.strip()
                if line:
                    # 히스토리는 제출 즉시 기록(큐잉돼도 제출된 것은 맞다).
                    if not pane.prompt_history or pane.prompt_history[-1] != line:
                        pane.prompt_history.append(line)
                        if len(pane.prompt_history) > 200:
                            pane.prompt_history = pane.prompt_history[-200:]
                    # 헤더용 last_prompt(#4): 이전 프롬프트가 아직 처리중(busy)이면
                    # 즉시 덮지 말고 pending 큐에 쌓는다 — _scan_claude 가 응답 경계에
                    # 다음 프롬프트를 승격한다(헤더 = "지금 처리 중인 프롬프트").
                    # busy 가 아니면(idle/None/limit) 곧장 확정.
                    if pane._claude == "busy":
                        pane.pending_prompts.append(line)
                    else:
                        pane.last_prompt = line
                buf = ""
            elif ord(ch) in (8, 127):        # backspace
                buf = buf[:-1]
            elif ord(ch) >= 32:
                buf += ch
            i += 1
        pane._inbuf = buf[-500:]

    def _handle_scroll(self, client: ClientConn, msg: dict):
        sess = client.session
        win = sess.active_window if sess else None
        if not win:
            return
        p = win.pane_by_id(msg.get("pane")) or win.active_pane
        if msg.get("bottom"):
            p.scroll_to("bottom")
        elif msg.get("top"):
            p.scroll_to("top")
        else:
            p.scroll_by(int(msg.get("delta", 0)))

    def shutdown(self):
        self.running = False
        self._close_all_capfiles()
        for sess in self.sessions.values():
            for tab in sess.tabs:
                for p in tab.window.panes():
                    if p.pty is not None:
                        p.pty.terminate()       # SIGHUP
        try:
            # TCP 엔드포인트는 지울 파일이 없다(포트파일은 다음 기동이 덮어씀).
            if not ipc.is_tcp(self.sock_path) and os.path.exists(self.sock_path):
                os.unlink(self.sock_path)
        except OSError:
            pass
        if self.loop:
            self.loop.stop()

    async def serve(self):
        self.loop = asyncio.get_running_loop()
        # OS 별 listen 분기(Unix=AF_UNIX, Windows=TCP 루프백+포트파일)는 ipc 가 담당.
        # 확정 엔드포인트(TCP 면 실제 포트)를 패널 셸 $PYTMUX 에 게시한다.
        server, self.resolved_endpoint = await ipc.start_server(
            self.sock_path, self.handle_client)
        # 작업 보존 재시작(re-exec) 후: 상속된 PTY 를 채택해 셸을 살린 채 복원.
        # 성공 시 상태 파일을 지워(다음 평범한 재시작이 stale 채택을 시도하지 않게).
        resumed = False
        rp = self._resume_path
        if rp and os.path.exists(rp) and not self.sessions:
            resumed = self.restore_resume_state(rp)
            try:
                os.unlink(rp)
            except OSError:
                pass
        # 재부팅/재시작 후: 저장된 레이아웃이 있으면 구조 복원(셸은 새로 시작)
        if not resumed and os.path.exists(self.layout_path) and not self.sessions:
            self.restore_layout()
        flush = asyncio.create_task(self._flush_loop())
        autoname = asyncio.create_task(self._autorename_loop())
        async with server:
            try:
                await server.serve_forever()
            except asyncio.CancelledError:
                pass
        flush.cancel()
        autoname.cancel()


def run_server(sock_path: str, resume_path: str | None = None):
    srv = Server(sock_path, resume_path)
    try:
        asyncio.run(srv.serve())
    except (KeyboardInterrupt, RuntimeError):
        pass
