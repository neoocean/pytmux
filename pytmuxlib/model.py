"""세션 / 윈도우 / 패널(분할 트리) 모델."""
from __future__ import annotations

import asyncio  # noqa: F401  (타입 주석용)
import time

import pyte

from .protocol import HISTORY, MIN_H, MIN_W, conv_color, set_winsize


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
        # 토큰 리밋 자동 재개
        self.autoresume = False
        self.resume_msg = "continue"
        self._scanbuf = ""
        self._resume_pending = False
        self._activity = False   # 마지막 검사 이후 출력 있었음
        self._bell = False       # 마지막 검사 이후 BEL 수신
        self.search_query = ""   # 스크롤백 검색어
        self._match_abs = None   # 현재 매치된 절대 라인 인덱스
        self.bracketed = False   # 내부 앱이 bracketed paste 모드를 켰는지
        self.pipe_proc = None    # pipe-pane 대상 프로세스

    def reinit(self, pid: int, fd: int, cols: int, rows: int) -> None:
        """respawn: 새 PTY/셸로 화면 버퍼를 초기화한다."""
        self.master_fd = fd
        self.child_pid = pid
        self.cols, self.rows = cols, rows
        self.screen = pyte.HistoryScreen(cols, rows, history=HISTORY, ratio=0.5)
        self.screen.set_mode(pyte.modes.LNM)
        self.stream = pyte.ByteStream(self.screen)
        self.scroll = 0
        self.dirty = True
        self._scanbuf = ""
        self._resume_pending = False
        self.search_query = ""
        self._match_abs = None
        self.bracketed = False

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
        for ry, line in enumerate(window):
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
            # 검색 매치 라인 전체 하이라이트
            if self._match_abs is not None and (start + ry) == self._match_abs:
                segs = [[t, {**st, "rv": 1}] for t, st in segs]
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
        self._active = root    # 활성 패널(프로퍼티로 last-pane 추적)
        self._last = None      # 직전 활성 패널(prefix ;)
        self.zoomed = False    # 활성 패널 전체화면(prefix z)
        self.layout_idx = 0    # 레이아웃 프리셋 순환 인덱스
        self.sync = False      # 입력 동기화(synchronize-panes)
        self.border_status = False  # 패널 제목 경계선 표시(pane-border-status)
        self.auto_rename = True  # 활성 패널 명령으로 이름 자동 갱신
        self.monitor_activity = False  # 비활성 윈도우 출력 감지
        self.monitor_bell = True       # 벨(BEL) 감지
        self.has_activity = False
        self.has_bell = False

    @property
    def active_pane(self):
        return self._active

    @active_pane.setter
    def active_pane(self, pane):
        if pane is not self._active:
            self._last = self._active
        self._active = pane

    def toggle_last_pane(self):
        if self._last is not None and self._last in self.panes():
            self.active_pane = self._last

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
        self.last_index = 0    # 직전 활성 윈도우(prefix l)

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

