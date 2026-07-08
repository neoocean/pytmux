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
        win.invalidate_panes()  # §4.6: 리프 추가 → panes() 캐시 무효화

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
                win.invalidate_panes()  # §4.6: 리프 제거 → panes() 캐시 무효화
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

    def _pane_shell_pid(self, pane: Pane) -> int:
        """패널 셸 프로세스의 실제 pid(없으면 -1). 인프로세스 PTY 면 pane.child_pid,
        host 모드(Windows 기본·POSIX 옵션 C)면 셸이 pty-host 프로세스 안에서 돌아
        pane.child_pid 는 -1 이므로 원격 pty 프록시가 host 의 'spawned' 회신으로 아는
        실제 셸 pid(_RemotePtyProcess.pid)로 폴백한다. cwd 조회·fg 명령 추정(자동
        탭이름·ssh 감지·세션종료 토큰요약)이 host 모드에서도 올바른 pid 를 쓰게 하는
        공용 헬퍼 — 안 그러면 foreground_command(-1)=None 으로 그 기능들이 조용히 죽는다."""
        if pane is None:
            return -1
        pid = getattr(pane, "child_pid", -1)
        if (pid is None or pid < 0) and getattr(pane, "pty", None) is not None:
            pid = getattr(pane.pty, "pid", -1)
        return pid if isinstance(pid, int) and pid >= 0 else -1

    def _pane_cwd(self, pane: Pane) -> str | None:
        # 자식(셸) 프로세스의 cwd 를 추정한다. 실패 시 None. host 모드(child_pid=-1)
        # 에선 pty 프록시의 실제 셸 pid 로 폴백한다(_pane_shell_pid) — 안 그러면 ncd·
        # default-path=current 가 host 모드에서 현재 디렉토리를 못 찾는다.
        pid = self._pane_shell_pid(pane)
        if pid < 0:
            return None
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
        # 새 탭은 비고정 구역의 끝(첫 고정 탭 앞)에 삽입한다 — 고정 구역을 침범하지
        # 않게(항목7). 고정 탭이 없으면 종전대로 맨 뒤(= append 와 동일).
        prev_active = sess.active_tab
        pos = self._first_pinned_pos(sess)
        sess.tabs.insert(pos, Tab(pos, "win", Window(root)))
        self._reindex(sess)
        sess.last_index = (sess.tabs.index(prev_active)
                           if prev_active in sess.tabs else pos)
        sess.active_index = pos

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
        # 활성 탭의 구역 안으로만 이동(핀 경계 안 넘김, 항목7).
        lo, hi = self._zone_bounds(sess, sess.active_index)
        new_index = max(lo, min(new_index, hi))
        tab = sess.tabs.pop(sess.active_index)
        sess.tabs.insert(new_index, tab)
        self._reindex(sess)
        sess.active_index = sess.tabs.index(tab)

    def swap_window(self, sess: Session, target_index: int):
        n = len(sess.tabs)
        if not (0 <= target_index < n) or target_index == sess.active_index:
            return
        # 다른 구역(비고정↔고정)과는 스왑 금지 — 불변식 보존(항목7).
        lo, hi = self._zone_bounds(sess, sess.active_index)
        if not (lo <= target_index <= hi):
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
        # 같은 구역 안으로만 재배치(핀 경계 안 넘김, 항목7) — 드래그 포함.
        lo, hi = self._zone_bounds(sess, index)
        to_index = max(lo, min(to_index, hi))
        if to_index == index:
            return
        active_tab = sess.active_tab
        tab = sess.tabs.pop(index)
        sess.tabs.insert(to_index, tab)
        self._reindex(sess)
        if active_tab in sess.tabs:
            sess.active_index = sess.tabs.index(active_tab)

    def move_current_tab(self, sess: Session, where: str):
        """현재(활성) 탭을 좌/우/맨앞/맨뒤로 이동(같은 구역 안으로 클램프 — move_tab)."""
        i, n = sess.active_index, len(sess.tabs)
        to = {"left": i - 1, "right": i + 1,
              "first": 0, "last": n - 1}.get(where)
        if to is not None:
            self.move_tab(sess, i, to)

    # ---- 탭 고정(핀, 항목7) ----
    # 불변식: Session.tabs 는 항상 *[비고정…][고정…]*. _normalize_pins 가 모든 변형
    # 끝에서 안정 분할로 강제한다(상대순서 보존). 탭 번호(index)·prefix-숫자·select_window
    # 가 물리 위치를 그대로 따르므로 "보이는 순서 = 번호 순서" 가 유지된다(설계 §4-A).

    def _first_pinned_pos(self, sess: Session) -> int:
        """첫 고정 탭의 인덱스 = 비고정 구역의 끝(다음 위치). 고정 탭이 없으면 len."""
        for i, t in enumerate(sess.tabs):
            if getattr(t, "pinned", False):
                return i
        return len(sess.tabs)

    def _zone_bounds(self, sess: Session, index: int):
        """index 가 속한 구역(비고정/고정)의 [lo, hi] 인덱스 범위(포함). 드래그/이동을
        이 범위로 클램프해 구역 경계를 넘지 못하게 한다."""
        fp = self._first_pinned_pos(sess)
        if index < fp:
            return 0, fp - 1                 # 비고정 구역
        return fp, len(sess.tabs) - 1        # 고정 구역

    def _normalize_pins(self, sess: Session):
        """tabs 를 안정 분할(비고정 먼저, 고정 나중)하고 _reindex. 활성·last 탭은
        객체 신원으로 추적해 위치만 갱신한다(번호가 바뀌어도 같은 탭을 가리킴)."""
        active = sess.active_tab
        last = (sess.tabs[sess.last_index]
                if 0 <= getattr(sess, "last_index", 0) < len(sess.tabs) else None)
        # Python list.sort 는 안정 정렬 → 같은 pinned 값끼리 상대순서 보존.
        sess.tabs.sort(key=lambda t: 1 if getattr(t, "pinned", False) else 0)
        self._reindex(sess)
        if active in sess.tabs:
            sess.active_index = sess.tabs.index(active)
        if last in sess.tabs:
            sess.last_index = sess.tabs.index(last)

    def set_pinned(self, sess: Session, index: int, value):
        """index 탭의 고정 여부를 설정하고 불변식을 정규화(핀=고정 구역 맨 앞으로,
        언핀=비고정 구역 맨 뒤로 이동하는 효과). 활성 탭 신원 유지."""
        if not (0 <= index < len(sess.tabs)):
            return
        sess.tabs[index].pinned = bool(value)
        self._normalize_pins(sess)

    def toggle_pin(self, sess: Session, index: int | None = None):
        """index(기본 활성) 탭의 고정 토글."""
        if index is None:
            index = sess.active_index
        if 0 <= index < len(sess.tabs):
            self.set_pinned(sess, index,
                            not getattr(sess.tabs[index], "pinned", False))

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
        win.invalidate_panes()  # §4.6: 리프 순서 변동 → 캐시 무효화

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
        win.invalidate_panes()  # §4.6: 리프 순서 변동 → 캐시 무효화
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
        win.invalidate_panes()  # §4.6: 리프 순서 변동 → 캐시 무효화
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
        win.invalidate_panes()  # §4.6: 리프 제거(break/join/move 소스측)
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
        win.invalidate_panes()  # §4.6: 대상 창에 리프 추가(소스측은 _detach_pane)
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
        target_win.invalidate_panes()  # §4.6: 대상 창 리프 추가(소스측 _detach_pane)
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

    def _fg_command(self, pane):
        """패널의 포그라운드 명령 이름(자동 탭이름·ssh/원격 감지용).

        POSIX: PTY master fd 의 포그라운드 프로세스 그룹을 `tcgetpgrp`+`ps` 로 구한다.
        Windows(ConPTY, #7): 포그라운드 pgrp 개념이 없어 셸 자손 프로세스 트리에서 가장
        깊은 자손을 추정한다(`proc.foreground_command(pane.child_pid)`) — idle 이면 셸
        이름, ssh/python 등을 띄우면 그 이름. 실패 시 None(고정 탭이름 폴백).

        과거엔 fd 만 받아 Windows 를 무조건 None 으로 폴백했으나, child_pid 기반으로
        Windows 자동 이름·원격 감지를 지원하도록 pane 을 받는다."""
        if pane is None:
            return None
        if pty_backend.IS_WINDOWS:
            # host 모드(Windows 기본)에선 child_pid=-1 이라 pty 프록시의 실제 셸 pid 로
            # 폴백한다(_pane_shell_pid) — 안 그러면 fg 명령을 못 구해 자동 탭이름·ssh
            # 감지·세션종료 토큰요약(_claude_really_exited)이 Windows host 에서 죽는다.
            return proc.foreground_command(self._pane_shell_pid(pane))
        fd = getattr(pane, "master_fd", -1)
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
                for tab in list(sess.tabs):       # M-2: await 중 tabs 변경 대비 스냅샷
                    win = tab.window
                    ap = win.active_pane
                    if not getattr(win, "auto_rename", False) or not ap:
                        continue
                    # P8: _fg_command 는 동기 `ps`/tcgetpgrp(POSIX) 호출이라 이벤트
                    # 루프를 막는다(2초마다 auto_rename 탭마다). 읽기전용 OS 호출이라
                    # 스레드 안전 → executor 로 오프로드해 루프 응답성을 지킨다(_fg_command
                    # 자체는 동기 유지 — 다른 동기 호출부 영향 없음).
                    cmd = await asyncio.get_event_loop().run_in_executor(
                        None, self._fg_command, ap)
                    if self._autorename_apply(sess, tab, ap, cmd):
                        changed = True
                if changed:
                    self._broadcast_status(sess)

    def _autorename_apply(self, sess, tab, ap, cmd) -> bool:
        """executor 로 얻은 fg 명령으로 탭 이름을 갱신할지 결정·적용(M-2). _fg_command
        executor await 가 read(active_pane)↔write(tab.name) 를 가르는 유일 지점이라,
        그 사이 kill_window/kill_pane 가 끼어들어 tab 이 세션에서 제거되거나 active_pane
        이 바뀌었으면 stale 이름을 쓰지 않는다. 변경했으면 True."""
        if (cmd and cmd != tab.name and tab in sess.tabs
                and tab.window.active_pane is ap):
            tab.name = cmd
            return True
        return False

    def _broadcast_status(self, sess: Session):
        """세션의 모든 클라에 현재 status 를 방송한다. **반드시 per-client**
        (`_status_msg(sess, client=c)`)로 빌드해, 원격 탭을 보는 클라(remote_view)가
        `_remote_status_override`(원격 탭 active 보존·로컬 탭 비활성)를 받게 한다.
        client 없이 `_status_msg(sess)` 로 방송하면 로컬 active(=sess.active_index)가
        그대로 새어, 원격 탭을 보는 클라의 탭바가 로컬 탭으로 한 프레임 튀었다
        복귀한다(§10-F 원격 탭 활성 튐 — auto-rename 방송이 이 누락의 원인이었다)."""
        for c in self.clients:
            if c.session is sess:
                asyncio.create_task(
                    self._send_to(c, self._status_msg(sess, client=c)))

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
