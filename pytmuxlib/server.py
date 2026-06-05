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

# pytmux 프로젝트 루트(= pytmuxlib 패키지의 상위). 캡처 출력 등 "프로젝트에 영속해
# Perforce 로 공유할" 산출물의 기준 경로다. proc.server_argv 의 entry 추정과 동일 규칙.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



class Server(ServerClaudeMixin, ServerCaptureMixin, ServerPersistMixin,
             ServerPtyMixin):
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

    def set_claude_rules(self, text: str):
        self.claude_rules = text or ""
        self._save_opts()

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

    def _carry_tokens_on_close(self, pane: Pane):
        """닫히는 Claude 패널의 확정 토큰을 **같은 계정의 살아있는 패널**로 이관한다
        (#20). 그래야 계정 합계가 패널 하나 닫혔다고 줄지 않고, 같은 계정의 Claude
        Code 가 전부 닫힐 때까지 유지된다(살아남는 패널이 없으면 자연히 사라진다).
        진행 중(미확정) peak 는 응답이 끊긴 것이므로 이관하지 않는다."""
        tok = (pane._tok_state.get("total", 0)
               if getattr(pane, "_tok_state", None) else 0)
        acct = getattr(pane, "_claude_account", None)
        if not tok or not acct:
            return
        for p in self._all_panes():
            if p is not pane and getattr(p, "_claude_account", None) == acct:
                p._tok_state["total"] = p._tok_state.get("total", 0) + tok
                p._session_tokens = (p._tok_state["total"]
                                     + p._tok_state.get("peak", 0))
                return

    def _remove_pane_from_tree(self, pane: Pane):
        # 어떤 세션/탭(윈도우)에 속하는지 탐색
        for sess in list(self.sessions.values()):
            for wi, tab in enumerate(list(sess.tabs)):
                win = tab.window
                if pane not in win.panes():
                    continue
                self._carry_tokens_on_close(pane)   # #20 계정 합계 유지
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
        # getattr 폴백: Session.__new__(복원 경로)이 popup 세팅을 빠뜨려도 attach 가
        # 통째로 깨지지 않게 방어한다(§10 — 근본 원인은 복원 경로에서 popup=None 세팅).
        pu = getattr(sess, "popup", None)
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
                capture_output=True, text=True, timeout=2,
                **proc.no_window_kwargs())
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
            out = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(pgid)], capture_output=True,
                text=True, timeout=1, **proc.no_window_kwargs()).stdout.strip()
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
                "claude": pane._claude, "usage": pane._claude_usage,
                # 세션 누계 토큰(#18) — 트리 팝업이 ctx 와 함께 실제 토큰량을 보이게.
                "tokens": pane._session_tokens}

    def _tree_msg(self):
        return {"t": "tree", "current": None, "sessions": [
            {"name": s.name, "active": (s is None),
             "windows": [{"index": t.index, "name": t.name,
                          "active": (t is s.active_tab),
                          "panes": [self._pane_overview(p)
                                    for p in t.window.panes()]}
                         for t in s.tabs]}
            for s in self.sessions.values()]}

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
                              "history": p.prompt_history[-30:],
                              "perm_mode": p._perm_mode}
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
            "claude_rules": self.claude_rules,   # #27 시작 규칙(에디터 초기값용)
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
                # Claude Code 상태/사용량 갱신(+ 비활성 탭 완료 감지, #22).
                # 새 휴리스틱(프롬프트/토큰/권한모드)이 특정 화면에서 터져도 flush
                # 루프 전체(=모든 클라 렌더)가 죽지 않게 가드한다(§10 안정성).
                try:
                    if self._scan_claude(sess, win):
                        status_changed = True
                except Exception:
                    self._log_error("scan_claude")
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
        elif action == "set_claude_perm_mode":
            # footer 클릭 팝업(§10 item 2): 활성/지정 패널 권한모드 목표 설정.
            self.set_claude_perm_mode(sess, str(msg.get("target", "")),
                                      pane_id=msg.get("id"))
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
            # src(끌어온 탭 인덱스) 지정 가능(#19 탭→패널 드래그). 미지정이면 직전 탭.
            self.join_pane(sess, src_index=msg.get("src"),
                           orient=msg.get("orient", "tb"))
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
        elif action == "set_claude_rules":      # #27 시작 규칙 저장(영속)
            self.set_claude_rules(msg.get("text", ""))
            self._broadcast_session(sess)       # status 로 새 규칙 회신
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

    def _log_error(self, where: str):
        """방금 처리 중인 예외의 트레이스백을 `<sock>.error.log` 에 append 한다.

        데몬은 stderr 가 /dev/null 이라, 클라이언트 처리(attach/_send_full/dispatch)
        나 flush 루프에서 난 예외가 **조용히 삼켜지면** 진단 단서가 없다. 한 클라
        attach 가 _send_full 에서 터지면 화면이 일부만 그려진 채 연결이 끊겨(클라가
        '일부 나타났다 바로 종료') 이후 모든 attach 가 같은 상태로 브릭되는데,
        호출부가 이걸 잡아 로그를 남기고 계속 진행하게 해 자가복구한다. 로깅 자체는
        절대 실패를 전파하지 않는다(best-effort)."""
        try:
            path = ipc.state_base(self.sock_path) + ".error.log"
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n==== {stamp} [{where}] ====\n")
                f.write(traceback.format_exc())
        except Exception:
            pass

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
        # 주의: append + 초기 _send_full 을 try 안에 둔다. 예전엔 try 밖이라
        # _send_full 이 한 번 터지면 ① 클라가 self.clients 에 남아 누수되고
        # ② 화면이 일부만 그려진 채 연결이 끊겨 클라가 즉시 종료, ③ 트레이스백도
        # 없이 이후 모든 attach 가 같은 상태로 브릭됐다(사용자 보고: "화면이 일부
        # 나타났다 바로 종료"). 이제 finally 가 항상 정리하고, _send_full 은 클라별로
        # 가드해 한 클라의 실패가 다른 클라 attach 를 막지 않게 한다.
        try:
            client.session = self.get_or_create_session(
                first.get("session"), client.cols, client.rows)
            self.clients.append(client)
            # 새 클라이언트가 붙으면 공유 크기가 바뀔 수 있어 같은 세션 전체를 갱신.
            # 한 클라의 _send_full 실패가 새 attach 를 죽이지 않게 개별 가드한다.
            for c in [x for x in self.clients if x.session is client.session]:
                try:
                    await self._send_full(c)
                except Exception:
                    self._log_error("send_full(initial)")

            while self.running:
                msg = await read_msg(reader)
                if msg is None:
                    break
                try:
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
                        for c in [x for x in self.clients
                                  if x.session is client.session]:
                            await self._send_full(c)
                    elif mt == "scroll":
                        self._handle_scroll(client, msg)
                    elif mt == "cmd":
                        await self._handle_cmd(client, msg)
                except Exception:
                    # 한 메시지 처리 실패가 세션을 끊지 않게 잡아 로그만 남기고 계속.
                    self._log_error(f"dispatch({msg.get('t')})")
        except Exception:
            self._log_error("handle_client")
        finally:
            sess = client.session
            if client in self.clients:
                self.clients.remove(client)
            try:
                writer.close()
            except Exception:
                pass
            # 미러링: 남은 클라이언트는 공유 크기가 커질 수 있으니 갱신(개별 가드)
            if sess and sess in self.sessions.values():
                for c in [x for x in self.clients if x.session is sess]:
                    try:
                        await self._send_full(c)
                    except Exception:
                        self._log_error("send_full(teardown)")

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
