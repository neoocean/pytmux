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

    def _remove_pane_from_tree(self, pane: Pane):
        # 어떤 세션/탭(윈도우)에 속하는지 탐색
        for sess in list(self.sessions.values()):
            for wi, tab in enumerate(list(sess.tabs)):
                win = tab.window
                if pane not in win.panes():
                    continue
                # #20 계정 합계 유지: 닫히는 Claude 패널의 확정 토큰을 같은 계정 생존
                # 패널로 이관. 토큰 누계는 플러그인 소유(S5 T4)라 코어는 pane_closing 훅으로
                # 위임한다 — 플러그인 부재 시 no-op(토큰 기능 자체가 없음, delete-to-disable).
                self.plugins.pane_closing(self, pane)
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
        # Windows: /proc·lsof 가 없으므로 PEB 를 읽어 cwd 를 구한다(proc 헬퍼).
        # 이게 None 이면 ncd 가 현재 디렉토리를 강조하지 못한다.
        if proc.IS_WINDOWS:
            return proc.process_cwd(pid)
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
    # ncd 디렉토리 나열·조상 사슬·응답 메시지 로직은 모두 플러그인으로 옮겼다
    # (pytmuxlib/plugins/ncd/server.py). 서버는 알 수 없는 action(request_nc_list)을
    # plugins.handle_server_request 로 위임하고, 플러그인은 여기 남은 범용 헬퍼
    # (_resolve_start_cwd→_pane_cwd)만 빌린다. 플러그인 디렉토리를 지우면 이 기능은
    # 조용히 사라진다(서버는 그 action 에 회신하지 않는다).

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

    def move_pane_to_tab(self, sess: Session, pane_id: int,
                         target_index: int) -> bool:
        """활성 윈도우의 pane_id 패널을 target_index 탭(윈도우)으로 옮긴다(헤더 드래그
        pick-up → 다른 탭에 드롭, #1). join_pane 의 거울상 — 거기선 다른 탭의 패널을
        현재 탭으로 당기고, 여기선 현재 탭의 패널을 지정한 탭으로 민다. 대상 윈도우의
        활성 패널과 tb 분할로 합친다. 소스 패널이 윈도우의 유일 패널이면 소스 탭은
        사라진다(break 의 반대). 옮긴 패널·대상 탭을 활성으로 만든다. 성공 시 True.

        주의: select_pane_id 가 활성 윈도우 한정이라(serverio) 픽업 소스는 항상 현재
        탭에 있다 — 그래서 소스는 sess.active_window 에서 찾는다."""
        src_win = sess.active_window
        if not src_win or not (0 <= target_index < len(sess.tabs)):
            return False
        src_tab = next((t for t in sess.tabs if t.window is src_win), None)
        if src_tab is None or src_tab.index == target_index:
            return False                      # 같은 탭 → 무동작
        pane = src_win.pane_by_id(pane_id)
        if pane is None:
            return False
        target_win = sess.tabs[target_index].window
        if target_win is src_win:
            return False
        src_single = pane.parent is None
        if not src_single:
            self._detach_pane(src_win, pane)
        # 대상 윈도우 활성 패널과 tb 분할로 삽입(join_pane 과 동일 트리 수술).
        target = target_win.active_pane
        new = Split("tb", target, pane, 0.5)
        pp = target.parent
        new.parent = pp
        target.parent = new
        pane.parent = new
        if pp is None:
            target_win.root = new
        elif pp.a is target:
            pp.a = new
        else:
            pp.b = new
        target_win.active_pane = pane
        target_win.zoomed = False
        if src_single:                        # 소스가 비었으면 그 탭 제거 + 재인덱스
            sess.tabs.remove(src_tab)
            self._reindex(sess)
        # 이동한 패널을 따라 대상 탭을 활성으로(탭 제거로 인덱스가 밀렸을 수 있어 윈도우
        # 동일성으로 다시 찾는다).
        cur_tab = next(t for t in sess.tabs if t.window is target_win)
        sess.active_index = sess.tabs.index(cur_tab)
        return True

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
            # _claude(상태)는 claude-code 플러그인 pane_init 소유(S4) — 부재 시 None
            # 으로 보고 Claude 리네임 동선을 건너뛴다(코어 리네임은 정상 동작).
            if len(panes) == 1 and getattr(panes[0], "_claude", None) is not None:
                if getattr(panes[0], "_claude", None) == "idle":
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
        except (subprocess.SubprocessError, OSError):
            # #28 좁힘: 프로세스 소멸·ps 부재·타임아웃 = 기대 가능한 실패(None
            # 폴백이 정답). 그 외 예외는 버그이므로 삼키지 않고 전파한다.
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
