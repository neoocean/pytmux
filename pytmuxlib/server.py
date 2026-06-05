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
import traceback

from . import ipc, proc, pty_backend, sshwrap, tokens, usagelog
from .model import (ClientConn, Pane, Session, Split, Tab, Window,
                    coalesce_alt_repaints)
from .claude import (claude_account, claude_feedback_prompt, claude_perm_mode,
                     claude_prompt, claude_state, claude_usage, parse_reset_delay)
from .protocol import (FEED_SLICE, FLUSH_HZ, MIN_H, MIN_W, read_msg, write_msg)
from .serverclaude import (ServerClaudeMixin, _CAM_MAX, _HDR_CLAUDE_MISS,
                            _DONE_IDLE_FRAMES)
from .servercapture import ServerCaptureMixin
from .serverpersist import ServerPersistMixin
from .serverpty import ServerPtyMixin
from .serverio import ServerIOMixin
from .servertree import ServerTreeMixin

# pytmux 프로젝트 루트(= pytmuxlib 패키지의 상위). 캡처 출력 등 "프로젝트에 영속해
# Perforce 로 공유할" 산출물의 기준 경로다. proc.server_argv 의 entry 추정과 동일 규칙.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



class Server(ServerClaudeMixin, ServerCaptureMixin, ServerPersistMixin,
             ServerPtyMixin, ServerIOMixin, ServerTreeMixin):
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
        self.capture = bool(_opts.get("capture", False))   # 기본 OFF
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
        # Claude Code 시작 규칙(#27): 사용자가 에디터 팝업으로 저장해 둔 "항상 지킬
        # 규칙" 텍스트. 새 Claude 세션이 뜨면(또는 pytmux 가 /clear 한 뒤) 이 텍스트를
        # 프롬프트에 주입한다(빈 문자열이면 아무것도 안 함). opts.json 영속.
        self.claude_rules = str(_opts.get("claude_rules", ""))

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
                # no_window_kwargs: Windows 에서 pipe-pane 의 cmd /c 콘솔 창 안 뜨게(§10)
                p.pipe_proc = subprocess.Popen(proc.shell_argv(cmd),
                                               stdin=subprocess.PIPE,
                                               **proc.no_window_kwargs())
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

def run_server(sock_path: str, resume_path: str | None = None):
    srv = Server(sock_path, resume_path)
    try:
        asyncio.run(srv.serve())
    except (KeyboardInterrupt, RuntimeError):
        pass
