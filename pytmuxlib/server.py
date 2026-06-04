"""셸 PTY 를 소유하는 백그라운드 서버(데몬)."""
from __future__ import annotations

import asyncio
import base64
import errno
import fcntl
import json
import os
import pty
import shlex
import signal
import subprocess
import time

from . import tokens
from .model import ClientConn, Pane, Session, Split, Tab, Window
from .claude import claude_state, claude_usage, parse_reset_delay
from .protocol import (FLUSH_HZ, MIN_H, MIN_W, read_msg, set_winsize, write_msg)


class Server:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self.sessions: dict[str, Session] = {}
        self.clients: list[ClientConn] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.running = True
        self._session_seq = 0
        self.buffers: list[str] = []   # 페이스트 버퍼(최신이 앞)
        # 패널 출력 캡처(Claude 화면 문구 분석용). 기본 ON, opts.json 에 영속.
        self._capfiles: dict[int, "object"] = {}   # pane.id -> 열린 바이너리 파일
        self.capture = bool(self._load_opts().get("capture", True))

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
        # 부모가 쥔 PTY master 에 close-on-exec 를 건다. 이게 없으면 이후 새 패널을
        # 만들 때 pty.fork() 한 자식 셸이 형제 패널들의 master fd 를 그대로 상속해,
        # fd 가 여러 프로세스에 살아남아 종료·재사용 시 패널 간 출력이 섞이는(=새 탭이
        # 다른 패널을 "복사"한 듯 보이는) 원인이 된다. 각 master 생성 직후 CLOEXEC 를
        # 걸어 두면 다음 fork 의 자식은 어떤 형제 master 도 물려받지 않는다.
        try:
            flags = fcntl.fcntl(fd, fcntl.F_GETFD)
            fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
        except OSError:
            pass
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
        except OSError as e:
            # macOS/BSD 는 슬레이브(자식)가 끝나면 master 읽기에서 EIO 를 던진다 →
            # 정상 EOF 로 처리. 그 외 일시적 오류는 패널을 닫지 않고 무시한다(살아있는
            # 패널을 잘못 정리하면 fd 가 해제·재사용되며 패널 간 fd 가 꼬일 수 있다).
            if e.errno == errno.EIO:
                self._pane_eof(pane)
            return
        if not data:
            self._pane_eof(pane)
            return
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
        self._close_capfile(pane.id)
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
        # 붙여넣기(모바일 받아쓰기·자동완성 포함)도 프롬프트 추적에 반영한다.
        # 이게 없으면 붙여넣은 Claude 프롬프트는 last_prompt 에 안 잡혀 헤더가
        # 셸 실행 명령("claude")에 머문다. 이후 사용자가 Enter(\r) 를 누르면
        # 누적된 본문이 last_prompt 로 확정된다(개행 포함 시 즉시 확정).
        Server._track_prompt(pane, data)
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

    # ---- 서버 옵션 영속(opts.json) ----
    @property
    def opts_path(self) -> str:
        return self.sock_path + ".opts.json"

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
                json.dump({"capture": self.capture}, f)
        except OSError:
            pass

    # ---- 패널 출력 캡처(Claude 화면 문구 분석용) ----
    @property
    def capture_dir(self) -> str:
        return self.sock_path + ".capture"

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
        elif c in ("send-keys", "send"):
            self._control_send_keys(sess, args)
        elif c in ("capture-output", "capture-toggle"):
            val = True if "on" in args else (False if "off" in args else None)
            return "on" if self.set_capture(val) else "off"
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
            p._mouse_sent = (p.mouse_track, p.mouse_sgr)
            pane_msgs.append({"id": p.id, "x": cx, "y": cy, "w": cw, "h": ch,
                              "title": p.title, "box": box,
                              "active": p is win.active_pane,
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
                # 토큰 누계(#3): 새 Claude 세션 시작(None→Claude) 시 리셋, 매 프레임
                # 현재 응답 running 토큰을 step 으로 접어 응답별 peak 를 누계에 확정.
                # (확정 시점 committed>0 은 #7 의 영속 로깅 이벤트로도 쓰인다.)
                if new_cl and not old_cl:
                    tokens.reset(p._tok_state)
                if new_cl:
                    running = tokens.parse_running_tokens(txt)
                    tokens.step(p._tok_state, running, new_cl == "busy")
                    if p._tok_state["total"] != p._session_tokens:
                        p._session_tokens = p._tok_state["total"]
                        changed = True
                elif p._session_tokens:
                    p._session_tokens = 0
                    p._tok_state["peak"] = 0
                    p._tok_state["total"] = 0
                    changed = True
                # 비활성 탭에서 처리(busy)→대기(idle) 전이 = 작업 완료. limit 은
                # "대기"가 아니므로 대상 아님(busy→idle 만).
                if (w is not win and old_cl == "busy" and new_cl == "idle"
                        and t.monitor_claude and not t.has_claude_done):
                    t.has_claude_done = True
                    changed = True
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
            # 활성 패널 Claude 세션 누적 토큰(#3, 응답별 peak 합산)
            "claude_tokens": (win.active_pane._session_tokens
                              if win and win.active_pane
                              and win.active_pane._claude else 0),
            "zoomed": bool(win.zoomed) if win else False,
            "sync": bool(win.sync) if win else False,
            "pane_title": win.active_pane.title if win and win.active_pane else "",
            "autoresume": bool(win.active_pane.autoresume)
            if win and win.active_pane else False,
            "capture": self.capture,
            "capture_path": cap_path,
            "capture_size": cap_size,
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
        # 마우스 패스스루: 커서 아래 패널 PTY 로만 raw 전달. 입력 동기화 대상이
        # 아니고(위치 기반), 프롬프트 추적/scroll 복귀도 건드리지 않는다.
        if msg.get("mouse"):
            try:
                os.write(p.master_fd, data)
            except OSError:
                pass
            return
        self._track_prompt(p, data)   # 마지막 입력 프롬프트 추적(Claude 헤더용)
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
                    pane.last_prompt = line
                    # 히스토리 누적(연속 중복 제외, 최근 200개 캡)
                    if not pane.prompt_history or pane.prompt_history[-1] != line:
                        pane.prompt_history.append(line)
                        if len(pane.prompt_history) > 200:
                            pane.prompt_history = pane.prompt_history[-200:]
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
