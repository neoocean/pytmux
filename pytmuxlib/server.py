"""셸 PTY 를 소유하는 백그라운드 서버(데몬)."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import pty
import shlex
import signal
import subprocess
import time

from .model import ClientConn, Pane, Session, Split, Tab, Window
from .protocol import (FLUSH_HZ, MIN_H, MIN_W, parse_reset_delay,
                       read_msg, set_winsize, write_msg)


class Server:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self.sessions: dict[str, Session] = {}
        self.clients: list[ClientConn] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.running = True
        self._session_seq = 0
        self.buffers: list[str] = []   # 페이스트 버퍼(최신이 앞)

    # ---- PTY/패널 생성 ----
    def _fork_shell(self, cols: int, rows: int, cwd: str | None = None):
        """셸을 PTY 에 fork/exec 하고 (pid, master_fd) 반환."""
        cols = max(MIN_W, cols)
        rows = max(MIN_H, rows)
        pid, fd = pty.fork()
        if pid == 0:  # 자식: 셸로 교체
            try:
                if cwd:
                    os.chdir(cwd)
            except OSError:
                pass
            env = dict(os.environ)
            env["TERM"] = "xterm-256color"
            env["PYTMUX"] = self.sock_path
            env.pop("LINES", None)
            env.pop("COLUMNS", None)
            shell = env.get("SHELL", "/bin/sh")
            try:
                os.execvpe(shell, [shell], env)
            except Exception:
                os._exit(127)
        try:
            set_winsize(fd, rows, cols)
        except OSError:
            pass
        os.set_blocking(fd, False)
        return pid, fd

    def spawn_pane(self, cols: int, rows: int, cwd: str | None = None) -> Pane:
        pid, fd = self._fork_shell(cols, rows, cwd)
        pane = Pane(pid, fd, max(MIN_W, cols), max(MIN_H, rows))
        self.loop.add_reader(fd, self._on_pane_readable, pane)
        return pane

    def respawn_pane(self, sess: Session):
        """활성 패널의 셸을 종료하고 같은 슬롯에서 새 셸을 띄운다."""
        win = sess.active_window
        if not win or not win.active_pane:
            return
        pane = win.active_pane
        cwd = self._pane_cwd(pane)
        try:
            self.loop.remove_reader(pane.master_fd)
        except (OSError, ValueError):
            pass
        try:
            os.killpg(os.getpgid(pane.child_pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            os.close(pane.master_fd)
        except OSError:
            pass
        try:
            os.waitpid(pane.child_pid, 0)  # SIGKILL 이므로 블로킹 회수 안전
        except ChildProcessError:
            pass
        pid, fd = self._fork_shell(pane.cols, pane.rows, cwd)
        pane.reinit(pid, fd, pane.cols, pane.rows)
        self.loop.add_reader(fd, self._on_pane_readable, pane)

    def _on_pane_readable(self, pane: Pane):
        try:
            data = os.read(pane.master_fd, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if not data:
            self._pane_eof(pane)
            return
        pane.feed(data)
        pane._activity = True
        if b"\x07" in data:
            pane._bell = True
        # bracketed paste 모드 추적(내부 앱의 DECSET 2004)
        if b"\x1b[?2004h" in data:
            pane.bracketed = True
        if b"\x1b[?2004l" in data:
            pane.bracketed = False
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
        try:
            os.write(pane.master_fd, (pane.resume_msg + "\r").encode("utf-8"))
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

    def _pane_eof(self, pane: Pane):
        if pane.pipe_proc:
            try:
                pane.pipe_proc.stdin.close()
            except Exception:
                pass
            pane.pipe_proc = None
        try:
            self.loop.remove_reader(pane.master_fd)
        except (OSError, ValueError):
            pass
        try:
            os.close(pane.master_fd)
        except OSError:
            pass
        try:
            os.waitpid(pane.child_pid, os.WNOHANG)
        except ChildProcessError:
            pass
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
    def split_pane(self, sess: Session, orient: str):
        win = sess.active_window
        if not win:
            return
        win.zoomed = False
        target = win.active_pane
        # 새 패널은 일단 임시 크기로 만들고, 곧 재배치된다.
        new = self.spawn_pane(MIN_W, MIN_H, cwd=self._pane_cwd(target))
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
        # 셸 자식 종료
        try:
            os.killpg(os.getpgid(pane.child_pid), signal.SIGHUP)
        except (OSError, ProcessLookupError):
            pass
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

    def _pane_cwd(self, pane: Pane) -> str | None:
        # 자식 프로세스의 cwd 를 추정(가능하면). 실패 시 None.
        try:
            return os.readlink(f"/proc/{pane.child_pid}/cwd")
        except OSError:
            return None

    def new_window(self, sess: Session):
        """새 탭(= 새 윈도우, 단일 패널)을 만들고 활성화한다."""
        c = self.clients_of(sess)
        cols = c.cols if c else 80
        rows = c.rows if c else 24
        root = self.spawn_pane(cols, rows)
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
            t.has_activity = t.has_bell = False  # 보면 플래그 해제

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
        """PTY 의 포그라운드 프로세스 그룹 명령 이름."""
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
            try:
                os.killpg(os.getpgid(p.child_pid), signal.SIGHUP)
            except (OSError, ProcessLookupError):
                pass
            try:
                self.loop.remove_reader(p.master_fd)
            except (OSError, ValueError):
                pass
            try:
                os.close(p.master_fd)
            except OSError:
                pass
            try:
                os.waitpid(p.child_pid, os.WNOHANG)
            except ChildProcessError:
                pass
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
        try:
            os.killpg(os.getpgid(pane.child_pid), signal.SIGHUP)
        except (OSError, ProcessLookupError):
            pass
        try:
            self.loop.remove_reader(pane.master_fd)
        except (OSError, ValueError):
            pass
        try:
            os.close(pane.master_fd)
        except OSError:
            pass
        try:
            os.waitpid(pane.child_pid, os.WNOHANG)
        except ChildProcessError:
            pass

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
        if pane.bracketed:
            data = b"\x1b[200~" + data + b"\x1b[201~"
        try:
            os.write(pane.master_fd, data)
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
        return os.path.join(os.path.dirname(self.sock_path) or "/tmp",
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

    # ---- 탭(윈도우+패널) 레이아웃 슬롯: 이름으로 저장/불러오기 ----
    @property
    def slots_path(self):
        # 소켓 경로 기준(고정 소켓이면 안정적, 테스트의 임시 소켓이면 격리됨)
        return self.sock_path + ".slots.json"

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
            self.new_window(sess)
        elif c in ("kill-window", "killw", "kill-tab", "killt"):
            self.kill_window(sess)
        elif c in ("split-window", "splitw"):
            # -h = 가로(상/하), -v = 세로(좌/우)
            self.split_pane(sess, "tb" if "-h" in args else
                            ("lr" if "-v" in args else "tb"))
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
        elif c in ("send-keys", "send"):
            self._control_send_keys(sess, args)
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
            try:
                os.write(win.active_pane.master_fd, out)
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
                p.pipe_proc = subprocess.Popen(["/bin/sh", "-c", cmd],
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

    def _session_size(self, sess: Session):
        """세션에 attach 한 모든 클라이언트를 수용하도록 최소 크기를 쓴다(미러링)."""
        cs = [c for c in self.clients if c.session is sess]
        if not cs:
            return 80, 24
        return (max(MIN_W, min(c.cols for c in cs)),
                max(MIN_H, min(c.rows for c in cs)))

    def _layout_msg(self, sess: Session, cols: int = None, rows: int = None):
        win = sess.active_window
        if not win:
            return None
        if cols is None or rows is None:
            cols, rows = self._session_size(sess)
        # 모든 패널 PTY 크기를 레이아웃에 맞춰 갱신
        panes, divs = win.compute_layout(0, 0, cols, rows)
        # 패널이 둘 이상이면 각 패널을 테두리 박스로 감싼다(활성=파랑, 비활성=회색).
        # 패널이 하나뿐이라도 활성 아웃라인을 그린다(항상 테두리).
        bordered = len(panes) >= 1
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
            p.resize(cw, ch)
            pane_msgs.append({"id": p.id, "x": cx, "y": cy, "w": cw, "h": ch,
                              "title": p.title, "box": box,
                              "active": p is win.active_pane})
        return {
            "t": "layout",
            "cols": cols, "rows": rows,
            "panes": pane_msgs,
            "dividers": divs,
            "titlebars": titlebars,
            "bordered": bordered,
            "border_status": bool(win.border_status),
            "active": win.active_pane.id,
        }

    def _tree_msg(self):
        return {"t": "tree", "current": None, "sessions": [
            {"name": s.name, "active": (s is None),
             "windows": [{"index": t.index, "name": t.name,
                          "panes": len(t.window.panes())} for t in s.tabs]}
            for s in self.sessions.values()]}

    def _status_msg(self, sess: Session):
        win = sess.active_window
        return {
            "t": "status",
            "session": sess.name,
            "windows": [{"index": t.index, "name": t.name,
                         "active": (i == sess.active_index),
                         "bell": t.has_bell, "activity": t.has_activity}
                        for i, t in enumerate(sess.tabs)],
            "active_pane": win.active_pane.id if win else None,
            "zoomed": bool(win.zoomed) if win else False,
            "sync": bool(win.sync) if win else False,
            "pane_title": win.active_pane.title if win and win.active_pane else "",
            "autoresume": bool(win.active_pane.autoresume)
            if win and win.active_pane else False,
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
                for p in win.panes():
                    if not p.dirty:
                        continue
                    rows, cursor = p.render(p is win.active_pane)
                    p.dirty = False
                    msg = {"t": "screen", "pane": p.id,
                           "rows": rows, "cursor": cursor}
                    for c in clients:
                        await write_msg(c.writer, msg)
                # 활동/벨 모니터링: 비활성 윈도우의 출력/BEL 을 플래그로
                status_changed = False
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
            self.split_pane(sess, msg.get("orient", "lr"))
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
            self.new_window(sess)
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
                if mt == "input":
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
        p = win.pane_by_id(msg.get("pane")) or win.active_pane
        data = base64.b64decode(msg.get("data", ""))
        # 입력 동기화 시 윈도우 내 모든 패널에 동일 입력 전달
        targets = win.panes() if win.sync else [p]
        for t in targets:
            if t.scroll != 0 or t._match_abs is not None:
                t.scroll = 0  # 입력 시작 시 live 로 복귀(R6)
                t._match_abs = None
                t.dirty = True
            try:
                os.write(t.master_fd, data)
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
        for sess in self.sessions.values():
            for tab in sess.tabs:
                for p in tab.window.panes():
                    try:
                        os.killpg(os.getpgid(p.child_pid), signal.SIGHUP)
                    except OSError:
                        pass
        try:
            if os.path.exists(self.sock_path):
                os.unlink(self.sock_path)
        except OSError:
            pass
        if self.loop:
            self.loop.stop()

    async def serve(self):
        self.loop = asyncio.get_running_loop()
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        server = await asyncio.start_unix_server(self.handle_client, path=self.sock_path)
        os.chmod(self.sock_path, 0o600)
        # 재부팅/재시작 후: 저장된 레이아웃이 있으면 구조 복원(셸은 새로 시작)
        if os.path.exists(self.layout_path) and not self.sessions:
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


def run_server(sock_path: str):
    srv = Server(sock_path)
    try:
        asyncio.run(srv.serve())
    except (KeyboardInterrupt, RuntimeError):
        pass
