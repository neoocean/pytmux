"""세션/윈도우/탭/패널 트리 조작 서버 로직 믹스인 — 분할·종료·이동·swap/rotate·
break/join·줌·이름변경·레이아웃 프리셋·popup·동기화·제목/border-status. `server.Server`
가 상속한다(§10 LLM 친화 리팩토링). 동작 불변 — self.* 상태와 Server 의 다른 메서드
(_send_full·_broadcast_session·save_layout·_close_capfile 등)를 그대로 참조한다."""
from __future__ import annotations

import asyncio
import os
import subprocess

from . import proc, pty_backend
from .model import ClientConn, Pane, Session, Split, Tab, Window
from .protocol import MIN_H, MIN_W, write_msg


class ServerTreeMixin:
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

    # ---- ncd (Norton Change Directory 풍 디렉토리 트리) ----
    def _list_dirs(self, path: str) -> list[str]:
        """`path` 의 직계 하위 **디렉토리**만 이름순(대소문자 무시)으로 나열해
        전체경로 리스트로 반환한다. nc 트리 표시·지연 펼치기용.

        - 파일은 제외(목적이 cd/패널 열기라 디렉토리만 의미 있다).
        - 숨김(`.` 시작) 디렉토리는 기본 제외(잡음 감소). 트리는 지연 로드이므로
          재귀하지 않고 한 단계만 읽어 대형/네트워크 디렉토리에서도 가볍다.
        - 권한 오류·경로 아님·심링크 깨짐 등은 빈 리스트로 graceful — nc 가
          중간 노드에서 죽지 않게 한다(개별 항목 오류도 건너뛴다)."""
        out: list[str] = []
        try:
            with os.scandir(path) as it:
                for e in it:
                    if e.name.startswith("."):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=True):
                            out.append(e.path)
                    except OSError:
                        continue   # stat 실패(권한·깨진 심링크) 항목은 건너뜀
        except OSError:
            return []
        out.sort(key=lambda p: os.path.basename(p).lower())
        return out

    @staticmethod
    def _ancestor_chain(cwd: str) -> list[str]:
        """`cwd` 의 조상 사슬을 루트부터 cwd 까지 순서대로 반환한다(둘 다 포함).
        예: /a/b/c → ['/', '/a', '/a/b', '/a/b/c']. ncd 초기 트리를 현재
        디렉토리까지 펼쳐 열기 위함."""
        cwd = os.path.abspath(cwd)
        parts: list[str] = []
        p = cwd
        while True:
            parts.append(p)
            parent = os.path.dirname(p)
            if parent == p:        # 루트('/'·드라이브) 도달
                break
            p = parent
        return list(reversed(parts))

    def nc_list_msg(self, sess: Session, path: str | None = None) -> dict:
        """ncd 디렉토리 목록 요청 응답.

        - `path` 가 있으면(노드 펼치기) 그 경로의 직계 하위를 `dirs` 로 회신하고
          `path` 에 절대경로를 echo 해 클라가 해당 노드를 매칭한다(지연 펼치기).
        - `path` 가 비면(초기 진입) **루트부터 현재 패널 cwd 까지의 사슬**을 만들어
          각 단계의 직계 하위와 함께 `chain` 으로 회신한다(+ `cwd`). 클라는 이 사슬을
          펼친 트리로 그리고 cwd 행에 커서를 둔다(NCD: 전체 트리·현재 위치 시작).
          cwd 추정 불가 시 루트만 1단계 회신.
        - 모든 경로는 **절대경로**(클라 경로 조합 버그 여지 제거; 표시명=basename).
        - 사슬 보존: 어떤 조상이 숨김(`.`)이라 부모의 `_list_dirs` 에서 빠져도, 그
          부모의 자식 목록에 다음 사슬 원소를 보장 포함해(없으면 추가·정렬) 펼친
          경로가 끊기지 않게 한다."""
        if path:
            root = os.path.abspath(os.path.expanduser(str(path)))
            return {"t": "nc_list", "root": root, "path": root,
                    "dirs": self._list_dirs(root)}
        cwd = self._resolve_start_cwd(sess, "current")
        cwd = os.path.abspath(cwd) if cwd else None
        chain_paths = self._ancestor_chain(cwd) if cwd else [os.path.abspath(os.sep)]
        chain: list[list] = []
        for i, p in enumerate(chain_paths):
            dirs = self._list_dirs(p)
            if i + 1 < len(chain_paths):
                nxt = chain_paths[i + 1]
                if nxt not in dirs:    # 숨김 조상도 사슬엔 보이게 보강
                    dirs = sorted(dirs + [nxt],
                                  key=lambda d: os.path.basename(d).lower())
            chain.append([p, dirs])
        return {"t": "nc_list", "root": chain_paths[0], "path": None,
                "cwd": cwd, "chain": chain}

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
            # 탭에 패널이 하나뿐이고 그게 Claude Code 패널이면, 같은 이름을 Claude
            # 세션에도 `/rename <이름>` 으로 반영한다(요청). 패널이 둘 이상이면 어느
            # 세션을 가리키는지 모호하므로 건너뛴다. _pc_inject 는 사용자 프롬프트와
            # 섞이지 않는 별도 입력 경로(자동재개·/compact 와 동일).
            # busy 일 때 즉시 주입하면 Claude Code 가 슬래시 명령으로 실행하지 않으므로
            # (compact/doc-clear 와 동일한 idle 게이트), idle 이면 지금 쏘고 아니면
            # _pending_rename 에 보류해 _scan_claude 의 busy→idle 경계에서 발동한다.
            panes = tab.window.panes()
            if len(panes) == 1 and panes[0]._claude is not None:
                if panes[0]._claude == "idle":
                    self._pc_inject(panes[0], "/rename " + name)
                else:
                    panes[0]._pending_rename = name

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
