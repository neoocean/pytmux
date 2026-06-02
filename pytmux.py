#!/usr/bin/env python3
"""pytmux — Python/Textual 기반 tmux 유사 터미널 멀티플렉서.

설계 문서: docs/DESIGN.md

아키텍처: 셸 PTY 를 소유하는 백그라운드 데몬(서버) 과 화면을 그리는 Textual
클라이언트를 유닉스 도메인 소켓으로 연결한다. 클라이언트나 상위 터미널을 닫아도
서버와 셸 세션은 유지된다.

사용법:
    python3 pytmux.py                 # 서버가 없으면 데몬 기동 후 attach, 있으면 attach
    python3 pytmux.py attach -t NAME  # 이름 있는 세션에 attach(없으면 생성)
    python3 pytmux.py new -s NAME     # 이름 있는 세션 생성 후 attach
    python3 pytmux.py ls              # 세션 목록
    python3 pytmux.py kill-server     # 서버와 모든 세션 종료
    python3 pytmux.py --socket PATH   # 사용할 소켓 경로 지정

기본 키 (prefix = Ctrl-b, 설정으로 변경 가능):
    prefix %      좌우 분할        prefix "      상하 분할
    prefix x      패널 삭제(확인)  prefix z      패널 줌 토글
    prefix o      다음 패널        prefix ←↑↓→   패널 이동
    prefix H/J/K/L 패널 경계 이동  prefix c      새 윈도우
    prefix ,      윈도우 이름변경  prefix &      윈도우 삭제(확인)
    prefix $      세션 이름변경    prefix :      명령 입력
    prefix n / p  다음/이전 윈도우 prefix 0-9    윈도우 선택
    prefix d      detach           prefix [      스크롤백 모드
    prefix Enter  메뉴 열기
마우스:
    휠 위/아래    해당 패널 스크롤백        패널 클릭   포커스 이동
    경계선 드래그 패널 리사이즈            우클릭      메뉴 열기

설정 파일: ~/.config/pytmux/config (set prefix / set mouse / set status-bg /
    set status-fg / bind <key> <command>). 자세한 내용은 load_config 참고.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import fcntl
import json
import os
import pty
import signal
import socket
import struct
import sys
import termios
import time

# ---------------------------------------------------------------------------
# 공통: 상수 / 소켓 경로 / 프로토콜 프레이밍
# ---------------------------------------------------------------------------

MIN_W = 3       # 패널 최소 폭(열)
MIN_H = 2       # 패널 최소 높이(행)
FLUSH_HZ = 30   # 서버 화면 push 주기
HISTORY = 10000 # 패널당 스크롤백 보관 행 수


def default_socket_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/pytmux-{os.getuid()}"
    os.makedirs(runtime, exist_ok=True)
    try:
        os.chmod(runtime, 0o700)
    except OSError:
        pass
    return os.path.join(runtime, "default.sock")


async def read_msg(reader: asyncio.StreamReader):
    """길이-프리픽스(4바이트 빅엔디언) + JSON 한 프레임을 읽는다. EOF 면 None."""
    try:
        header = await reader.readexactly(4)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    length = int.from_bytes(header, "big")
    try:
        payload = await reader.readexactly(length)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    return json.loads(payload.decode("utf-8"))


async def write_msg(writer: asyncio.StreamWriter, obj) -> bool:
    data = json.dumps(obj).encode("utf-8")
    try:
        writer.write(len(data).to_bytes(4, "big") + data)
        await writer.drain()
        return True
    except (ConnectionError, RuntimeError):
        return False


def set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(1, rows)
    cols = max(1, cols)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def conv_color(c):
    """pyte 색 이름 → Rich 색 토큰. 알 수 없으면 None(=기본색)."""
    if not c or c == "default":
        return None
    if c == "brown":
        return "yellow"
    if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return "#" + c
    if c.startswith("bright"):
        return "bright_" + c[6:]
    return c


# ===========================================================================
#  서버 측
# ===========================================================================

import pyte  # noqa: E402  (서버에서만 필요)


class Pane:
    """잎 노드. 셸 PTY + pyte 화면 버퍼 + 스크롤백 뷰포트."""

    def __init__(self, pid: int, fd: int, cols: int, rows: int):
        self.id = pid_counter()
        self.master_fd = fd
        self.child_pid = pid
        self.cols = cols
        self.rows = rows
        self.screen = pyte.HistoryScreen(cols, rows, history=HISTORY, ratio=0.5)
        self.screen.set_mode(pyte.modes.LNM)
        self.stream = pyte.ByteStream(self.screen)
        self.scroll = 0          # 0 = live(맨 아래), 양수 = 위로 N 행
        self.dirty = True
        self.rect = (0, 0, cols, rows)
        self.parent: Split | None = None
        self.title = "shell"

    # 레이아웃 계산용
    def first_pane(self) -> "Pane":
        return self

    def feed(self, data: bytes) -> None:
        before = len(self.screen.history.top)
        self.stream.feed(data)
        after = len(self.screen.history.top)
        if self.scroll > 0 and after > before:
            # 스크롤백을 보는 중 새 출력이 위로 밀어내면 뷰포트를 고정(R6)
            self.scroll = min(self.scroll + (after - before), after)
        self.dirty = True

    def resize(self, cols: int, rows: int) -> None:
        cols = max(1, cols)
        rows = max(1, rows)
        if cols == self.cols and rows == self.rows:
            return
        self.cols, self.rows = cols, rows
        self.screen.resize(rows, cols)
        try:
            set_winsize(self.master_fd, rows, cols)
        except OSError:
            pass
        self.dirty = True

    def scroll_by(self, delta: int) -> None:
        maxoff = len(self.screen.history.top)
        self.scroll = max(0, min(self.scroll + delta, maxoff))
        self.dirty = True

    def scroll_to(self, where: str) -> None:
        if where == "top":
            self.scroll = len(self.screen.history.top)
        else:
            self.scroll = 0
        self.dirty = True

    def render(self, with_cursor: bool):
        """현재 뷰포트를 [rows, cursor] 로 직렬화. rows = 행마다 [text, style] 런 목록."""
        screen = self.screen
        cols, lines = screen.columns, screen.lines
        hist = list(screen.history.top)
        full = hist + [screen.buffer[y] for y in range(lines)]
        total = len(full)
        end = total - self.scroll
        start = end - lines
        if start < 0:
            start, end = 0, lines
        window = full[start:end]

        cursor = None
        if with_cursor and self.scroll == 0 and not screen.cursor.hidden:
            cursor = [screen.cursor.x, screen.cursor.y]

        rows = []
        for line in window:
            segs = []
            cur_text = []
            cur_key = None
            for x in range(cols):
                ch = line[x]
                style = self._char_style(ch)
                key = tuple(sorted(style.items()))
                data = ch.data or " "
                if key != cur_key:
                    if cur_text:
                        segs.append(["".join(cur_text), dict(cur_key)])
                    cur_text = [data]
                    cur_key = key
                else:
                    cur_text.append(data)
            if cur_text:
                segs.append(["".join(cur_text), dict(cur_key)])
            rows.append(segs)
        # 뷰포트가 화면보다 짧으면(스크롤 초기) 빈 줄로 채움
        while len(rows) < lines:
            rows.append([[" " * cols, {}]])
        return rows, cursor

    @staticmethod
    def _char_style(ch) -> dict:
        d = {}
        fg = conv_color(ch.fg)
        bg = conv_color(ch.bg)
        if fg:
            d["f"] = fg
        if bg:
            d["b"] = bg
        if ch.bold:
            d["bo"] = 1
        if ch.italics:
            d["it"] = 1
        if ch.underscore:
            d["un"] = 1
        if ch.reverse:
            d["rv"] = 1
        if getattr(ch, "strikethrough", False):
            d["st"] = 1
        return d


class Split:
    """내부 노드. 방향(lr/tb)과 비율로 두 자식을 분할."""

    def __init__(self, orient: str, a, b, ratio: float = 0.5):
        self.id = split_counter()
        self.orient = orient   # 'lr' = 좌우, 'tb' = 상하
        self.a = a
        self.b = b
        self.ratio = ratio
        self.rect = (0, 0, 0, 0)
        self.parent: Split | None = None

    def first_pane(self) -> Pane:
        return self.a.first_pane()


_pid_seq = [0]
_split_seq = [0]


def pid_counter() -> int:
    _pid_seq[0] += 1
    return _pid_seq[0]


def split_counter() -> int:
    _split_seq[0] += 1
    return _split_seq[0]


class Window:
    def __init__(self, index: int, name: str, root: Pane):
        self.index = index
        self.name = name
        self.root = root
        self.active_pane = root
        self.zoomed = False    # 활성 패널 전체화면(prefix z)
        self.layout_idx = 0    # 레이아웃 프리셋 순환 인덱스

    def panes(self):
        out = []
        stack = [self.root]
        while stack:
            n = stack.pop()
            if isinstance(n, Pane):
                out.append(n)
            else:
                stack.append(n.a)
                stack.append(n.b)
        return out

    def pane_by_id(self, pid: int):
        for p in self.panes():
            if p.id == pid:
                return p
        return None

    # --- 레이아웃 ---
    def compute_layout(self, x, y, w, h):
        panes, divs = [], []
        if self.zoomed and isinstance(self.active_pane, Pane):
            # 줌: 활성 패널만 전체 영역을 차지하고 분할선/타 패널은 숨김
            self.active_pane.rect = (x, y, w, h)
            panes.append(self.active_pane)
            return panes, divs
        self._layout(self.root, x, y, w, h, panes, divs)
        return panes, divs

    def _layout(self, node, x, y, w, h, panes, divs):
        node.rect = (x, y, w, h)
        if isinstance(node, Pane):
            panes.append(node)
            return
        if node.orient == "lr":
            avail = w - 1
            if avail < MIN_W * 2:
                aw = max(0, avail)  # 너무 좁으면 한쪽에 몰아줌
            else:
                aw = max(MIN_W, min(avail - MIN_W, round(avail * node.ratio)))
            dx = x + aw
            divs.append({"split_id": node.id, "orient": "lr",
                         "x": dx, "y": y, "w": 1, "h": h,
                         "rect": [x, y, w, h]})
            self._layout(node.a, x, y, aw, h, panes, divs)
            self._layout(node.b, dx + 1, y, avail - aw, h, panes, divs)
        else:
            avail = h - 1
            if avail < MIN_H * 2:
                ah = max(0, avail)
            else:
                ah = max(MIN_H, min(avail - MIN_H, round(avail * node.ratio)))
            dy = y + ah
            divs.append({"split_id": node.id, "orient": "tb",
                         "x": x, "y": dy, "w": w, "h": 1,
                         "rect": [x, y, w, h]})
            self._layout(node.a, x, y, w, ah, panes, divs)
            self._layout(node.b, x, dy + 1, w, avail - ah, panes, divs)

    def split_by_id(self, sid: int):
        stack = [self.root]
        while stack:
            n = stack.pop()
            if isinstance(n, Split):
                if n.id == sid:
                    return n
                stack += [n.a, n.b]
        return None

    # --- 레이아웃 프리셋(select-layout) ---
    @staticmethod
    def _chain(nodes, orient):
        """노드들을 동일 비율의 orient 분할 사슬로 묶는다."""
        node = nodes[-1]
        for i in range(len(nodes) - 2, -1, -1):
            count = len(nodes) - i  # 이 서브트리의 잎/노드 수
            node = Split(orient, nodes[i], node, 1.0 / count)
        return node

    def _fix_parents(self, node, parent):
        node.parent = parent
        if isinstance(node, Split):
            self._fix_parents(node.a, node)
            self._fix_parents(node.b, node)

    def apply_preset(self, preset: str):
        leaves = self.panes()
        if not leaves:
            return
        self.zoomed = False
        if preset in ("even-horizontal", "even-h"):
            self.root = self._chain(leaves, "lr")
        elif preset in ("even-vertical", "even-v"):
            self.root = self._chain(leaves, "tb")
        elif preset == "main-vertical":
            main, rest = leaves[0], leaves[1:]
            self.root = (Split("lr", main, self._chain(rest, "tb"), 0.5)
                         if rest else main)
        elif preset == "main-horizontal":
            main, rest = leaves[0], leaves[1:]
            self.root = (Split("tb", main, self._chain(rest, "lr"), 0.5)
                         if rest else main)
        elif preset == "tiled":
            n = len(leaves)
            cols = int(n ** 0.5)
            if cols * cols < n:
                cols += 1
            rows = [leaves[i:i + cols] for i in range(0, n, cols)]
            row_nodes = [self._chain(r, "lr") for r in rows]
            self.root = self._chain(row_nodes, "tb")
        else:
            return
        self._fix_parents(self.root, None)


class Session:
    def __init__(self, name: str, root: Pane):
        self.name = name
        self.created_at = time.time()
        self.windows = [Window(0, "win", root)]
        self.active_index = 0

    @property
    def active_window(self) -> Window | None:
        if not self.windows:
            return None
        self.active_index = max(0, min(self.active_index, len(self.windows) - 1))
        return self.windows[self.active_index]


class ClientConn:
    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.session: Session | None = None
        self.cols = 80
        self.rows = 24


class Server:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self.sessions: dict[str, Session] = {}
        self.clients: list[ClientConn] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.running = True
        self._session_seq = 0

    # ---- PTY/패널 생성 ----
    def spawn_pane(self, cols: int, rows: int, cwd: str | None = None) -> Pane:
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
        # 부모
        try:
            set_winsize(fd, rows, cols)
        except OSError:
            pass
        os.set_blocking(fd, False)
        pane = Pane(pid, fd, cols, rows)
        self.loop.add_reader(fd, self._on_pane_readable, pane)
        return pane

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

    def _pane_eof(self, pane: Pane):
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
        """이름이 주어지면 해당 세션에 attach(없으면 그 이름으로 생성).
        이름이 없으면 기본 세션을 보장한다."""
        if name:
            if name in self.sessions:
                return self.sessions[name]
            root = self.spawn_pane(cols, max(MIN_H, rows))
            sess = Session(name, root)
            self.sessions[name] = sess
            return sess
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
        # 어떤 세션/윈도우에 속하는지 탐색
        for sess in list(self.sessions.values()):
            for wi, win in enumerate(list(sess.windows)):
                if pane not in win.panes():
                    continue
                parent = pane.parent
                if parent is None:
                    # 윈도우의 마지막 패널 → 윈도우 제거
                    sess.windows.pop(wi)
                    self._reindex(sess)
                    if not sess.windows:
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
        for i, w in enumerate(sess.windows):
            w.index = i

    def _pane_cwd(self, pane: Pane) -> str | None:
        # 자식 프로세스의 cwd 를 추정(가능하면). 실패 시 None.
        try:
            return os.readlink(f"/proc/{pane.child_pid}/cwd")
        except OSError:
            return None

    def new_window(self, sess: Session):
        c = self.clients_of(sess)
        cols = c.cols if c else 80
        rows = c.rows if c else 24
        root = self.spawn_pane(cols, rows)
        idx = len(sess.windows)
        sess.windows.append(Window(idx, "win", root))
        sess.active_index = idx

    def select_window(self, sess: Session, index: int):
        if 0 <= index < len(sess.windows):
            sess.active_index = index

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
        win = sess.active_window
        if win and name:
            win.name = name

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
        if win in sess.windows:
            sess.windows.remove(win)
        self._reindex(sess)
        if not sess.windows:
            del self.sessions[sess.name]

    def rename_session(self, sess: Session, name: str):
        if not name or name == sess.name or name in self.sessions:
            return
        del self.sessions[sess.name]
        sess.name = name
        self.sessions[name] = sess

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

    def _layout_msg(self, sess: Session, cols: int, rows: int):
        win = sess.active_window
        if not win:
            return None
        # 모든 패널 PTY 크기를 레이아웃에 맞춰 갱신
        panes, divs = win.compute_layout(0, 0, cols, rows)
        for p in panes:
            p.resize(p.rect[2], p.rect[3])
        return {
            "t": "layout",
            "cols": cols, "rows": rows,
            "panes": [{"id": p.id, "x": p.rect[0], "y": p.rect[1],
                       "w": p.rect[2], "h": p.rect[3]} for p in panes],
            "dividers": divs,
            "active": win.active_pane.id,
        }

    def _status_msg(self, sess: Session):
        win = sess.active_window
        return {
            "t": "status",
            "session": sess.name,
            "windows": [{"index": w.index, "name": w.name,
                         "active": (i == sess.active_index)}
                        for i, w in enumerate(sess.windows)],
            "active_pane": win.active_pane.id if win else None,
            "zoomed": bool(win.zoomed) if win else False,
        }

    async def _send_full(self, client: ClientConn):
        sess = client.session
        if not sess:
            return
        lay = self._layout_msg(sess, client.cols, client.rows)
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
        elif action == "resize":
            self.resize_split(sess, msg.get("split_id"), msg.get("ratio", 0.5))
        elif action == "resize_dir":
            self.resize_dir(sess, msg.get("dir"), msg.get("cells", 3))
        elif action == "new_window":
            self.new_window(sess)
        elif action == "next_window":
            self.select_window(sess, (sess.active_index + 1) % len(sess.windows))
        elif action == "prev_window":
            self.select_window(sess, (sess.active_index - 1) % len(sess.windows))
        elif action == "select_window":
            self.select_window(sess, msg.get("index", 0))
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
        elif action == "rename_window":
            self.rename_window(sess, str(msg.get("name", "")).strip())
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
                {"name": s.name, "windows": len(s.windows),
                 "panes": sum(len(w.panes()) for w in s.windows)}
                for s in self.sessions.values()]})
            writer.close()
            return
        if t == "kill-server":
            await write_msg(writer, {"t": "ok"})
            writer.close()
            self._notify_no_sessions()
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
        await self._send_full(client)

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
                    await self._send_full(client)
                elif mt == "scroll":
                    self._handle_scroll(client, msg)
                elif mt == "cmd":
                    await self._handle_cmd(client, msg)
        finally:
            if client in self.clients:
                self.clients.remove(client)
            try:
                writer.close()
            except Exception:
                pass

    def _handle_input(self, client: ClientConn, msg: dict):
        sess = client.session
        win = sess.active_window if sess else None
        if not win:
            return
        p = win.pane_by_id(msg.get("pane")) or win.active_pane
        if p.scroll != 0:
            p.scroll = 0  # 입력 시작 시 live 로 복귀(R6)
            p.dirty = True
        data = base64.b64decode(msg.get("data", ""))
        try:
            os.write(p.master_fd, data)
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
            for win in sess.windows:
                for p in win.panes():
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
        flush = asyncio.create_task(self._flush_loop())
        async with server:
            try:
                await server.serve_forever()
            except asyncio.CancelledError:
                pass
        flush.cancel()


def run_server(sock_path: str):
    srv = Server(sock_path)
    try:
        asyncio.run(srv.serve())
    except (KeyboardInterrupt, RuntimeError):
        pass


# ===========================================================================
#  클라이언트 측 (Textual)
# ===========================================================================

def _tmux_key_to_textual(tok: str) -> str:
    """tmux 키 표기(C-a)를 Textual 키 이름(ctrl+a)으로 변환."""
    tok = tok.strip()
    if tok.lower().startswith("c-") and len(tok) == 3:
        return "ctrl+" + tok[2].lower()
    return tok


def _key_to_ctrl_bytes(prefix_key: str) -> bytes:
    """prefix 키를 셸로 그대로 보낼 때의 바이트 시퀀스."""
    if prefix_key.startswith("ctrl+") and len(prefix_key) == 6:
        c = prefix_key[5]
        if c.isalpha():
            return bytes([ord(c.lower()) - 96])
    if len(prefix_key) == 1:
        return prefix_key.encode("utf-8", "replace")
    return b"\x02"


def load_config(path: str | None = None) -> dict:
    """설정 파일을 읽어 클라이언트 설정 딕셔너리를 만든다.

    탐색 순서: 인자 경로 → $PYTMUX_CONFIG → $XDG_CONFIG_HOME/pytmux/config
    → ~/.config/pytmux/config → ~/.pytmux.conf

    지원 지시어:
        set prefix C-a            # prefix 키 변경
        set mouse on|off          # 마우스 사용 여부
        set status-bg <color>     # 상태줄 배경색
        set status-fg <color>     # 상태줄 글자색
        bind <key> <command...>   # prefix 후 <key> 에 명령 바인딩
    """
    cfg = {"prefix": "ctrl+b", "mouse": True, "bindings": {},
           "status_bg": "green", "status_fg": "black"}
    candidates = []
    if path:
        candidates.append(path)
    if os.environ.get("PYTMUX_CONFIG"):
        candidates.append(os.environ["PYTMUX_CONFIG"])
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    candidates.append(os.path.join(xdg, "pytmux", "config"))
    candidates.append(os.path.expanduser("~/.pytmux.conf"))
    cfgfile = next((c for c in candidates if c and os.path.isfile(c)), None)
    if not cfgfile:
        return cfg
    try:
        with open(cfgfile, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if parts[0] == "set" and len(parts) >= 3:
                    opt, val = parts[1], " ".join(parts[2:])
                    if opt == "prefix":
                        cfg["prefix"] = _tmux_key_to_textual(val)
                    elif opt == "mouse":
                        cfg["mouse"] = val.lower() in ("on", "true", "1", "yes")
                    elif opt == "status-bg":
                        cfg["status_bg"] = val
                    elif opt == "status-fg":
                        cfg["status_fg"] = val
                elif parts[0] == "bind" and len(parts) >= 3:
                    cfg["bindings"][parts[1]] = " ".join(parts[2:])
    except OSError:
        pass
    return cfg


def build_client_app(sock_path: str, config: dict | None = None,
                     session_name: str | None = None):
    config = config or {}
    from rich.segment import Segment
    from rich.style import Style
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.screen import ModalScreen
    from textual.strip import Strip
    from textual.widget import Widget
    from textual.widgets import Input, Label, ListItem, ListView
    from datetime import datetime

    DEFAULT_STYLE = Style()
    _style_cache: dict = {}

    def make_style(d: dict) -> Style:
        if not d:
            return DEFAULT_STYLE
        key = tuple(sorted(d.items()))
        st = _style_cache.get(key)
        if st is None:
            try:
                st = Style(color=d.get("f"), bgcolor=d.get("b"),
                           bold=bool(d.get("bo")), italic=bool(d.get("it")),
                           underline=bool(d.get("un")), reverse=bool(d.get("rv")),
                           strike=bool(d.get("st")))
            except Exception:
                st = Style(reverse=bool(d.get("rv")), bold=bool(d.get("bo")))
            _style_cache[key] = st
        return st

    SPECIAL = {
        "enter": b"\r", "tab": b"\t", "backspace": b"\x7f", "escape": b"\x1b",
        "space": b" ", "up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C",
        "left": b"\x1b[D", "home": b"\x1b[H", "end": b"\x1b[F",
        "pageup": b"\x1b[5~", "pagedown": b"\x1b[6~", "delete": b"\x1b[3~",
        "insert": b"\x1b[2~", "f1": b"\x1bOP", "f2": b"\x1bOQ", "f3": b"\x1bOR",
        "f4": b"\x1bOS", "f5": b"\x1b[15~", "f6": b"\x1b[17~", "f7": b"\x1b[18~",
        "f8": b"\x1b[19~", "f9": b"\x1b[20~", "f10": b"\x1b[21~",
        "f11": b"\x1b[23~", "f12": b"\x1b[24~",
    }

    def key_to_bytes(event: events.Key) -> bytes:
        k = event.key
        if k in SPECIAL:
            return SPECIAL[k]
        if k.startswith("ctrl+"):
            if event.character:
                return event.character.encode("utf-8", "replace")
            c = k[5:]
            if len(c) == 1 and c.isalpha():
                return bytes([ord(c.lower()) - 96])
            return b""
        if event.character is not None and event.character != "":
            return event.character.encode("utf-8", "replace")
        return b""

    MENU_ITEMS = [
        ("split_lr", "패널 분할 │ (좌우)"),
        ("split_tb", "패널 분할 ─ (상하)"),
        ("zoom", "패널 줌 토글 ⛶"),
        ("kill_pane", "패널 삭제 ✕"),
        ("new_window", "새 윈도우"),
        ("rename_window", "윈도우 이름 변경"),
        ("kill_window", "윈도우 삭제"),
        ("next_window", "다음 윈도우"),
        ("prev_window", "이전 윈도우"),
        ("new_session", "새 세션"),
        ("command", "명령 입력 (:)"),
        ("detach", "detach (앱 종료, 세션 유지)"),
        ("kill_server", "서버 종료 (모든 세션 종료)"),
    ]

    class MenuScreen(ModalScreen):
        CSS = """
        MenuScreen { align: center middle; }
        #menu { width: 40; height: auto; border: round $accent; background: $panel; }
        """

        def compose(self) -> ComposeResult:
            lv = ListView(*[ListItem(Label(label), id=f"m_{key}")
                            for key, label in MENU_ITEMS], id="menu")
            yield lv

        def on_mount(self):
            self.query_one(ListView).focus()

        def on_list_view_selected(self, event):
            key = event.item.id[2:]
            self.dismiss(key)

        def on_key(self, event: events.Key):
            if event.key == "escape":
                event.stop()
                self.dismiss(None)

    class PromptBar(Input):
        """하단 입력줄. 명령(:)·윈도우 이름변경·확인 등에 재사용."""

        def __init__(self):
            super().__init__(id="prompt")
            self.purpose = None
            self.action = None

        def on_key(self, event: events.Key):
            if event.key == "escape":
                event.stop()
                event.prevent_default()
                self.app.close_prompt()

    class MultiplexerView(Widget):
        can_focus = True

        def __init__(self):
            super().__init__(id="view")
            self._cells: list[list] = []
            self._dragging = None  # (split_id, orient, rect)

        def set_frame(self, cells):
            self._cells = cells
            self.refresh()

        def render_line(self, y: int) -> Strip:
            if y >= len(self._cells):
                return Strip.blank(self.size.width)
            row = self._cells[y]
            segs = []
            run = []
            run_st = None
            for ch, st in row:
                if st is run_st:
                    run.append(ch)
                else:
                    if run:
                        segs.append(Segment("".join(run), run_st))
                    run = [ch]
                    run_st = st
            if run:
                segs.append(Segment("".join(run), run_st))
            return Strip(segs)

        # --- 마우스 ---
        def _pane_at(self, x, y):
            for p in self.app.layout.get("panes", []):
                if p["x"] <= x < p["x"] + p["w"] and p["y"] <= y < p["y"] + p["h"]:
                    return p
            return None

        def _divider_at(self, x, y):
            for d in self.app.layout.get("dividers", []):
                if d["x"] <= x < d["x"] + d["w"] and d["y"] <= y < d["y"] + d["h"]:
                    return d
            return None

        def on_mouse_down(self, event: events.MouseDown):
            if not self.app.mouse_enabled:
                return
            if event.button == 3:
                self.app.open_menu()
                event.stop()
                return
            d = self._divider_at(event.x, event.y)
            if d:
                self._dragging = d
                self.capture_mouse()
                event.stop()
                return
            p = self._pane_at(event.x, event.y)
            if p:
                self.app.send_cmd("select_pane_id", id=p["id"])
            event.stop()

        def on_mouse_move(self, event: events.MouseMove):
            if not self._dragging:
                return
            d = self._dragging
            sx, sy, sw, sh = d["rect"]
            if d["orient"] == "lr":
                avail = sw - 1
                ratio = (event.x - sx) / avail if avail > 0 else 0.5
            else:
                avail = sh - 1
                ratio = (event.y - sy) / avail if avail > 0 else 0.5
            self.app.send_cmd("resize", split_id=d["split_id"],
                              ratio=max(0.05, min(0.95, ratio)))
            event.stop()

        def on_mouse_up(self, event: events.MouseUp):
            if self._dragging:
                self._dragging = None
                self.release_mouse()
                event.stop()

        def on_mouse_scroll_up(self, event):
            if not self.app.mouse_enabled:
                return
            p = self._pane_at(event.x, event.y) or self._active_pane()
            if p:
                self.app.send_scroll(p["id"], delta=3)
            event.stop()

        def on_mouse_scroll_down(self, event):
            if not self.app.mouse_enabled:
                return
            p = self._pane_at(event.x, event.y) or self._active_pane()
            if p:
                self.app.send_scroll(p["id"], delta=-3)
            event.stop()

        def _active_pane(self):
            aid = self.app.layout.get("active")
            for p in self.app.layout.get("panes", []):
                if p["id"] == aid:
                    return p
            return None

    class StatusBar(Widget):
        def __init__(self, bg="green", fg="black"):
            super().__init__(id="status")
            self.session = ""
            self.windows = []
            self.zoomed = False
            self.bg = bg
            self.fg = fg

        def update_status(self, msg):
            self.session = msg.get("session", "")
            self.windows = msg.get("windows", [])
            self.zoomed = msg.get("zoomed", False)
            self.refresh()

        def render_line(self, y: int) -> Strip:
            w = self.size.width
            base = Style(color=self.fg, bgcolor=self.bg)
            active = Style(color=self.bg, bgcolor="white", bold=True)
            segs = [Segment(f" [{self.session}] ", base)]
            if self.zoomed:
                segs.append(Segment("Z ", Style(color="black", bgcolor="yellow",
                                                 bold=True)))
            for win in self.windows:
                label = f"{win['index']}:{win['name']} "
                segs.append(Segment(label, active if win["active"] else base))
            now = datetime.now().strftime("%H:%M %d-%b-%y")
            host = socket.gethostname().split(".")[0]
            right = f" {host} {now} "
            used = sum(len(s.text) for s in segs)
            pad = w - used - len(right)
            if pad > 0:
                segs.append(Segment(" " * pad, base))
            segs.append(Segment(right, base))
            # 폭 맞추기(자르기)
            return Strip(segs).adjust_cell_length(w, base)

    class PytmuxApp(App):
        ENABLE_COMMAND_PALETTE = False
        BINDINGS = []
        CSS = """
        Screen { layout: vertical; }
        #view { width: 100%; height: 1fr; }
        #status { width: 100%; height: 1; dock: bottom; }
        #prompt { width: 100%; height: 1; dock: bottom; display: none;
                  background: $panel; }
        """

        def __init__(self, sock_path: str):
            super().__init__()
            self.sock_path = sock_path
            self.session_name = session_name
            self.reader = None
            self.writer = None
            self.layout = {"panes": [], "dividers": [], "active": None,
                           "cols": 80, "rows": 24}
            self.pane_content = {}   # id -> (rows, cursor)
            self.mode = "normal"     # normal | prefix | scroll | prompt
            # ---- 설정(config) 적용 ----
            self.prefix_key = config.get("prefix", "ctrl+b")
            self.prefix_bytes = _key_to_ctrl_bytes(self.prefix_key)
            self.bindings = config.get("bindings", {})
            self.mouse_enabled = config.get("mouse", True)
            self.view = MultiplexerView()
            self.status = StatusBar(bg=config.get("status_bg", "green"),
                                    fg=config.get("status_fg", "black"))
            self.prompt = PromptBar()

        def compose(self) -> ComposeResult:
            yield self.view
            yield self.status
            yield self.prompt

        async def on_mount(self):
            self.view.focus()
            try:
                self.reader, self.writer = await asyncio.open_unix_connection(
                    path=self.sock_path)
            except (ConnectionError, FileNotFoundError, OSError):
                self.exit(message="pytmux: 서버에 연결할 수 없습니다")
                return
            cols, rows = self._content_size()
            hello = {"t": "hello", "cols": cols, "rows": rows}
            if self.session_name:
                hello["session"] = self.session_name
            await write_msg(self.writer, hello)
            self.run_worker(self._reader_task(), exclusive=False)

        def _content_size(self):
            size = self.size
            return max(MIN_W, size.width), max(MIN_H, size.height - 1)

        async def _reader_task(self):
            while True:
                msg = await read_msg(self.reader)
                if msg is None:
                    self.exit()
                    return
                self._dispatch(msg)

        def _dispatch(self, msg):
            t = msg.get("t")
            if t == "layout":
                self.layout = msg
                self._composite()
            elif t == "screen":
                self.pane_content[msg["pane"]] = (msg["rows"], msg.get("cursor"))
                self._composite()
            elif t == "status":
                self.status.update_status(msg)
            elif t == "bye":
                self.exit(message="pytmux: 서버가 종료되었습니다")

        def _composite(self):
            W = self.layout.get("cols", self.size.width)
            H = self.layout.get("rows", max(1, self.size.height - 1))
            cells = [[(" ", DEFAULT_STYLE) for _ in range(W)] for _ in range(H)]
            active = self.layout.get("active")
            for p in self.layout.get("panes", []):
                content = self.pane_content.get(p["id"])
                if not content:
                    continue
                rows, cursor = content
                for ry, row in enumerate(rows):
                    if ry >= p["h"]:
                        break
                    gy = p["y"] + ry
                    if not (0 <= gy < H):
                        continue
                    cx = p["x"]
                    for text, style_d in row:
                        st = make_style(style_d)
                        for chh in text:
                            if cx - p["x"] >= p["w"]:
                                break
                            if 0 <= cx < W:
                                cells[gy][cx] = (chh, st)
                            cx += 1
                # 활성 패널 커서
                if cursor and p["id"] == active:
                    ccx, ccy = cursor
                    gx, gy = p["x"] + ccx, p["y"] + ccy
                    if 0 <= gx < W and 0 <= gy < H:
                        ch, st = cells[gy][gx]
                        cells[gy][gx] = (ch, st + Style(reverse=True))
            # 분할선
            div_style = Style(color="grey50")
            active_div = Style(color="green")
            for d in self.layout.get("dividers", []):
                ch = "│" if d["orient"] == "lr" else "─"
                for i in range(d["h"] if d["orient"] == "lr" else d["w"]):
                    if d["orient"] == "lr":
                        gx, gy = d["x"], d["y"] + i
                    else:
                        gx, gy = d["x"] + i, d["y"]
                    if 0 <= gx < W and 0 <= gy < H:
                        cells[gy][gx] = (ch, div_style)
            # display-panes 오버레이: 각 패널 중앙에 번호 표시
            if self.mode == "display":
                for i, p in enumerate(self.layout.get("panes", [])):
                    label = str(i)
                    cx0 = p["x"] + max(0, (p["w"] - len(label)) // 2)
                    cy0 = p["y"] + p["h"] // 2
                    st = Style(color="black", bold=True,
                               bgcolor="green" if p["id"] == active else "yellow")
                    for j, chh in enumerate(label):
                        gx = cx0 + j
                        if 0 <= gx < W and 0 <= cy0 < H:
                            cells[cy0][gx] = (chh, st)
            self.view.set_frame(cells)

        # ---- 송신 헬퍼 ----
        def send_cmd(self, action, **kw):
            if self.writer:
                kw["t"] = "cmd"
                kw["action"] = action
                asyncio.create_task(write_msg(self.writer, kw))

        def send_input(self, data: bytes):
            if self.writer and data:
                asyncio.create_task(write_msg(self.writer, {
                    "t": "input", "pane": self.layout.get("active"),
                    "data": base64.b64encode(data).decode("ascii")}))

        def send_scroll(self, pane_id, delta=0, bottom=False, top=False):
            if self.writer:
                m = {"t": "scroll", "pane": pane_id}
                if bottom:
                    m["bottom"] = True
                elif top:
                    m["top"] = True
                else:
                    m["delta"] = delta
                asyncio.create_task(write_msg(self.writer, m))

        # ---- 메뉴 ----
        def open_menu(self):
            def handle(result):
                if result:
                    self._run_menu_action(result)
            self.push_screen(MenuScreen(), handle)

        def _run_menu_action(self, key):
            if key == "split_lr":
                self.send_cmd("split", orient="lr")
            elif key == "split_tb":
                self.send_cmd("split", orient="tb")
            elif key == "zoom":
                self.send_cmd("zoom")
            elif key == "kill_pane":
                self.send_cmd("kill_pane")
            elif key == "new_window":
                self.send_cmd("new_window")
            elif key == "rename_window":
                self.open_prompt("rename_window", "rename-window",
                                 self._active_window_name())
            elif key == "kill_window":
                self.open_prompt("confirm", "kill-window? (y/N)",
                                 action=lambda: self.send_cmd("kill_window"))
            elif key == "next_window":
                self.send_cmd("next_window")
            elif key == "prev_window":
                self.send_cmd("prev_window")
            elif key == "new_session":
                self.open_prompt("new_session", "new-session (이름, 빈칸=자동)")
            elif key == "command":
                self.open_prompt("command", ":")
            elif key == "detach":
                self.exit(message="detached")
            elif key == "kill_server":
                self.send_cmd("kill_server")

        # ---- 프롬프트 / 명령 ----
        def _active_window_name(self):
            for w in self.status.windows:
                if w.get("active"):
                    return w.get("name", "")
            return ""

        def open_prompt(self, purpose, placeholder="", initial="", action=None):
            self.prompt.purpose = purpose
            self.prompt.action = action
            self.prompt.placeholder = placeholder
            self.prompt.value = initial
            self.status.display = False
            self.prompt.display = True
            self.prompt.focus()
            self.mode = "prompt"

        def close_prompt(self):
            self.prompt.display = False
            self.status.display = True
            self.prompt.value = ""
            self.prompt.purpose = None
            self.prompt.action = None
            self.mode = "normal"
            self.view.focus()

        def on_input_submitted(self, event):
            if event.input is not self.prompt:
                return
            val = event.value.strip()
            purpose = self.prompt.purpose
            action = self.prompt.action
            self.close_prompt()
            if purpose == "command":
                self._run_command(val)
            elif purpose == "rename_window":
                if val:
                    self.send_cmd("rename_window", name=val)
            elif purpose == "rename_session":
                if val:
                    self.send_cmd("rename_session", name=val)
            elif purpose == "new_session":
                self.send_cmd("new_session", name=val)
            elif purpose == "confirm":
                if val.lower().startswith("y") and action:
                    action()

        @staticmethod
        def _opt_value(args, flag):
            if flag in args:
                i = args.index(flag)
                if i + 1 < len(args):
                    return args[i + 1]
            return None

        @staticmethod
        def _first_int(args):
            for a in args:
                if a.lstrip("-").isdigit():
                    return int(a.lstrip("-")) if not a.startswith("-") else None
                if a.isdigit():
                    return int(a)
            return None

        def _run_command(self, line):
            """tmux 류 명령 문자열을 해석해 서버 명령으로 변환한다."""
            if not line:
                return
            parts = line.split()
            c = parts[0].lower()
            args = parts[1:]
            if c in ("split-window", "splitw"):
                orient = "lr" if "-h" in args else "tb"
                self.send_cmd("split", orient=orient)
            elif c in ("kill-pane", "killp"):
                self.send_cmd("kill_pane")
            elif c in ("new-window", "neww"):
                self.send_cmd("new_window")
            elif c in ("kill-window", "killw"):
                self.send_cmd("kill_window")
            elif c in ("next-window", "next"):
                self.send_cmd("next_window")
            elif c in ("previous-window", "prev"):
                self.send_cmd("prev_window")
            elif c in ("select-window", "selectw"):
                idx = self._opt_value(args, "-t")
                idx = int(idx) if idx and idx.isdigit() else self._first_int(args)
                if idx is not None:
                    self.send_cmd("select_window", index=idx)
            elif c in ("rename-window", "renamew"):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("rename_window", name=name)
                else:
                    self.open_prompt("rename_window", "rename-window",
                                     self._active_window_name())
            elif c in ("rename-session", "rename"):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("rename_session", name=name)
                else:
                    self.open_prompt("rename_session", "rename-session")
            elif c in ("new-session", "new"):
                name = self._opt_value(args, "-s") or " ".join(
                    a for a in args if not a.startswith("-"))
                self.send_cmd("new_session", name=name or "")
            elif c in ("switch-client", "switchc", "attach-session", "attach"):
                name = self._opt_value(args, "-t")
                if name:
                    self.send_cmd("switch_session", name=name)
            elif c in ("resize-pane", "resizep"):
                if "-Z" in args:
                    self.send_cmd("zoom")
            elif c == "zoom":
                self.send_cmd("zoom")
            elif c in ("select-layout", "selectl"):
                if args:
                    self.send_cmd("select_layout", preset=args[0])
                else:
                    self.send_cmd("cycle_layout")
            elif c in ("next-layout", "nextl"):
                self.send_cmd("cycle_layout")
            elif c in ("rotate-window", "rotatew"):
                self.send_cmd("rotate", forward=("-D" not in args))
            elif c in ("swap-pane", "swapp"):
                self.send_cmd("swap_pane", forward=("-U" not in args))
            elif c in ("detach-client", "detach"):
                self.exit(message="detached")
            elif c == "kill-server":
                self.send_cmd("kill_server")
            # 알 수 없는 명령은 조용히 무시

        # ---- 이벤트 ----
        async def on_resize(self, event):
            if self.writer:
                cols, rows = self._content_size()
                await write_msg(self.writer, {"t": "resize", "cols": cols, "rows": rows})

        def on_key(self, event: events.Key):
            # 메뉴/모달이 떠 있으면 그쪽에서 처리
            if len(self.screen_stack) > 1:
                return
            # 프롬프트 입력 중이면 Input 위젯이 처리하도록 둔다
            if self.mode == "prompt":
                return
            if self.mode == "prefix":
                self._handle_prefix(event)
                event.prevent_default()
                event.stop()
                return
            if self.mode == "scroll":
                self._handle_scroll_key(event)
                event.prevent_default()
                event.stop()
                return
            if self.mode == "display":
                self._handle_display_key(event)
                event.prevent_default()
                event.stop()
                return
            # normal
            if event.key == self.prefix_key:
                self.mode = "prefix"
                event.prevent_default()
                event.stop()
                return
            data = key_to_bytes(event)
            if data:
                self.send_input(data)
            event.prevent_default()
            event.stop()

        def _handle_prefix(self, event: events.Key):
            self.mode = "normal"
            k = event.key
            ch = event.character
            # prefix 를 한 번 더 누르면 prefix 키 자체를 셸로 전송
            if k == self.prefix_key:
                self.send_input(self.prefix_bytes)
                return
            # 사용자 정의 바인딩 우선 (config 의 bind)
            token = ch if (ch and ch.isprintable() and not k.startswith("ctrl+")) else k
            if token in self.bindings:
                self._run_command(self.bindings[token])
                return
            if k == "percent_sign" or ch == "%":
                self.send_cmd("split", orient="lr")
            elif k == "quotation_mark" or ch == '"':
                self.send_cmd("split", orient="tb")
            elif k == "x":
                self.open_prompt("confirm", "kill-pane? (y/N)",
                                 action=lambda: self.send_cmd("kill_pane"))
            elif k == "z":
                self.send_cmd("zoom")
            elif k == "o":
                self.send_cmd("cycle_pane")
            elif k == "q":
                self._enter_display()
            elif k == "space":
                self.send_cmd("cycle_layout")
            elif k == "ctrl+o":
                self.send_cmd("rotate", forward=True)
            elif ch == "{":
                self.send_cmd("swap_pane", forward=False)
            elif ch == "}":
                self.send_cmd("swap_pane", forward=True)
            elif k in ("left", "right", "up", "down"):
                self.send_cmd("select_pane", dir=k)
            elif k in ("H", "J", "K", "L"):
                self.send_cmd("resize_dir",
                              dir={"H": "left", "L": "right",
                                   "K": "up", "J": "down"}[k], cells=3)
            elif k == "c":
                self.send_cmd("new_window")
            elif k == "comma" or ch == ",":
                self.open_prompt("rename_window", "rename-window",
                                 self._active_window_name())
            elif k == "ampersand" or ch == "&":
                self.open_prompt("confirm", "kill-window? (y/N)",
                                 action=lambda: self.send_cmd("kill_window"))
            elif k == "dollar_sign" or ch == "$":
                self.open_prompt("rename_session", "rename-session")
            elif k == "colon" or ch == ":":
                self.open_prompt("command", ":")
            elif k == "n":
                self.send_cmd("next_window")
            elif k == "p":
                self.send_cmd("prev_window")
            elif k.isdigit():
                self.send_cmd("select_window", index=int(k))
            elif k == "d":
                self.exit(message="detached")
            elif k == "left_square_bracket" or ch == "[":
                self.mode = "scroll"
            elif k == "enter":
                self.open_menu()
            # 그 외 키는 무시

        # ---- display-panes (prefix q) ----
        def _enter_display(self):
            self.mode = "display"
            self._composite()
            self.set_timer(1.5, self._auto_exit_display)

        def _auto_exit_display(self):
            if self.mode == "display":
                self._exit_display()

        def _exit_display(self):
            self.mode = "normal"
            self._composite()

        def _handle_display_key(self, event: events.Key):
            panes = self.layout.get("panes", [])
            k = event.key
            if k.isdigit() and int(k) < len(panes):
                self.send_cmd("select_pane_id", id=panes[int(k)]["id"])
            self._exit_display()

        def _handle_scroll_key(self, event: events.Key):
            aid = self.layout.get("active")
            k = event.key
            if k == "up":
                self.send_scroll(aid, delta=1)
            elif k == "down":
                self.send_scroll(aid, delta=-1)
            elif k == "pageup":
                self.send_scroll(aid, delta=10)
            elif k == "pagedown":
                self.send_scroll(aid, delta=-10)
            elif k == "g":
                self.send_scroll(aid, top=True)
            elif k in ("G", "end"):
                self.send_scroll(aid, bottom=True)
            elif k in ("q", "escape", "enter"):
                self.send_scroll(aid, bottom=True)
                self.mode = "normal"

    return PytmuxApp(sock_path)


def run_client(sock_path: str, session: str | None = None):
    config = load_config()
    app = build_client_app(sock_path, config, session)
    app.run()


# ===========================================================================
#  데몬화 / 런처 / 제어 명령
# ===========================================================================

def daemonize():
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    if devnull > 2:
        os.close(devnull)


def can_connect(sock_path: str) -> bool:
    if not os.path.exists(sock_path):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(sock_path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def ensure_server(sock_path: str):
    if can_connect(sock_path):
        return
    pid = os.fork()
    if pid == 0:
        daemonize()
        run_server(sock_path)
        os._exit(0)
    # 부모: 중간 자식을 회수하고 소켓이 뜰 때까지 대기
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    for _ in range(200):
        if can_connect(sock_path):
            return
        time.sleep(0.02)
    print("pytmux: 서버 기동 실패", file=sys.stderr)
    sys.exit(1)


def control_request(sock_path: str, obj: dict):
    if not can_connect(sock_path):
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)
    data = json.dumps(obj).encode()
    s.sendall(len(data).to_bytes(4, "big") + data)
    try:
        header = _recvn(s, 4)
        if not header:
            return None
        n = int.from_bytes(header, "big")
        payload = _recvn(s, n)
        return json.loads(payload.decode())
    finally:
        s.close()


def _recvn(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pytmux", description="tmux 유사 터미널 멀티플렉서")
    parser.add_argument("--socket", default=None, help="유닉스 도메인 소켓 경로")
    sub = parser.add_subparsers(dest="command")
    p_at = sub.add_parser("attach", help="실행 중인 서버에 attach (없으면 기동)")
    p_at.add_argument("-t", "--target", default=None, help="attach 할 세션 이름")
    p_new = sub.add_parser("new", help="새(이름 있는) 세션을 만들고 attach")
    p_new.add_argument("-s", "--session-name", default=None, help="세션 이름")
    sub.add_parser("ls", help="세션 목록")
    sub.add_parser("kill-server", help="서버와 모든 세션 종료")
    p_srv = sub.add_parser("server", help="(내부) 서버를 전경 실행")
    p_srv.add_argument("--foreground", action="store_true")
    args = parser.parse_args(argv)

    sock_path = args.socket or default_socket_path()

    if args.command == "server":
        run_server(sock_path)
        return
    if args.command == "ls":
        reply = control_request(sock_path, {"t": "list"})
        if not reply:
            print("실행 중인 서버 없음")
            return
        for s in reply.get("sessions", []):
            print(f"{s['name']}: {s['windows']} windows, {s['panes']} panes")
        return
    if args.command == "kill-server":
        reply = control_request(sock_path, {"t": "kill-server"})
        print("서버 종료됨" if reply else "실행 중인 서버 없음")
        return

    # 기본 동작 = attach (필요 시 데몬 기동)
    session = None
    if args.command == "attach":
        session = args.target
    elif args.command == "new":
        session = args.session_name
    ensure_server(sock_path)
    run_client(sock_path, session)


if __name__ == "__main__":
    main()
