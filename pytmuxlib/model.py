"""세션 / 윈도우 / 패널(분할 트리) 모델."""
from __future__ import annotations

import asyncio  # noqa: F401  (타입 주석용)
import re
import time

import pyte

from .protocol import HISTORY, MIN_H, MIN_W, conv_color, set_winsize

# 대체 화면 버퍼(alternate screen) 전환 시퀀스. pyte 가 직접 지원하지 않아
# pytmux 가 직접 처리한다(vim/less/htop/Claude Code 등 풀스크린 TUI 용).
_ALT_RE = re.compile(rb"\x1b\[\?(1049|1047|47)(h|l)")
# feed 경계에서 잘린 미완성 CSI private 시퀀스(예: ESC[?104)를 다음 feed 로 미룸
_ALT_PARTIAL_RE = re.compile(rb"\x1b\[\?[0-9;]*$")


class Pane:
    """잎 노드. 셸 PTY + pyte 화면 버퍼 + 스크롤백 뷰포트."""

    def __init__(self, pid: int, fd: int, cols: int, rows: int):
        self.id = pid_counter()
        self.master_fd = fd
        self.child_pid = pid
        self.cols = cols
        self.rows = rows
        # 메인 화면(스크롤백 보관) + 대체 화면(풀스크린 TUI 용, 스크롤백 없음)
        self._main = pyte.HistoryScreen(cols, rows, history=HISTORY, ratio=0.5)
        self._main.set_mode(pyte.modes.LNM)
        self._main_stream = pyte.ByteStream(self._main)
        self._alt = None
        self._alt_stream = None
        self.alt_active = False
        self.screen = self._main      # 현재 활성 화면(렌더 대상)
        self._altcarry = b""          # feed 경계의 미완성 시퀀스 보관
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
        self._main = pyte.HistoryScreen(cols, rows, history=HISTORY, ratio=0.5)
        self._main.set_mode(pyte.modes.LNM)
        self._main_stream = pyte.ByteStream(self._main)
        self._alt = None
        self._alt_stream = None
        self.alt_active = False
        self.screen = self._main
        self._altcarry = b""
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
        # 대체 화면 전환 시퀀스를 가로채 메인/대체 화면으로 라우팅한다.
        buf = self._altcarry + data
        self._altcarry = b""
        m = _ALT_PARTIAL_RE.search(buf)
        if m:  # 끝에 잘린 CSI private 시퀀스는 다음 feed 로 미룸
            self._altcarry = buf[m.start():]
            buf = buf[:m.start()]
        pos = 0
        for mo in _ALT_RE.finditer(buf):
            self._feed_seg(buf[pos:mo.start()])
            if mo.group(2) == b"h":
                self._enter_alt()
            else:
                self._leave_alt()
            pos = mo.end()
        self._feed_seg(buf[pos:])
        self.dirty = True

    def _feed_seg(self, seg: bytes) -> None:
        if not seg:
            return
        if self.screen is self._main:
            before = len(self._main.history.top)
            self._main_stream.feed(seg)
            after = len(self._main.history.top)
            if self.scroll > 0 and after > before:
                # 스크롤백을 보는 중 새 출력이 위로 밀어내면 뷰포트를 고정(R6)
                self.scroll = min(self.scroll + (after - before), after)
        else:
            self._alt_stream.feed(seg)

    def _enter_alt(self) -> None:
        if self.alt_active:
            return
        self._alt = pyte.Screen(self.cols, self.rows)
        self._alt.set_mode(pyte.modes.LNM)
        self._alt_stream = pyte.ByteStream(self._alt)
        self.screen = self._alt
        self.alt_active = True
        self.scroll = 0
        self._match_abs = None

    def _leave_alt(self) -> None:
        if not self.alt_active:
            return
        self._alt = None
        self._alt_stream = None
        self.screen = self._main
        self.alt_active = False
        self.scroll = 0
        self._match_abs = None

    def resize(self, cols: int, rows: int) -> None:
        cols = max(1, cols)
        rows = max(1, rows)
        if cols == self.cols and rows == self.rows:
            return
        self.cols, self.rows = cols, rows
        self._main.resize(rows, cols)
        if self._alt is not None:
            self._alt.resize(rows, cols)
        try:
            set_winsize(self.master_fd, rows, cols)
        except OSError:
            pass
        self.dirty = True

    def _history_len(self) -> int:
        h = getattr(self.screen, "history", None)
        return len(h.top) if h is not None else 0

    def scroll_by(self, delta: int) -> None:
        self.scroll = max(0, min(self.scroll + delta, self._history_len()))
        self.dirty = True

    def scroll_to(self, where: str) -> None:
        self.scroll = self._history_len() if where == "top" else 0
        self.dirty = True

    def render(self, with_cursor: bool):
        """현재 뷰포트를 [rows, cursor] 로 직렬화. rows = 행마다 [text, style] 런 목록."""
        screen = self.screen
        cols, lines = screen.columns, screen.lines
        h = getattr(screen, "history", None)
        hist = list(h.top) if h is not None else []  # 대체 화면은 스크롤백 없음
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
                data = ch.data
                if data == "":
                    # 와이드 문자(이모지·CJK)의 연속 셀: 보내지 않는다(클라이언트가
                    # 문자 폭만큼 칸을 차지). 공백으로 바꾸면 한 칸씩 밀린다.
                    continue
                if not data:
                    data = " "
                style = self._char_style(ch)
                key = tuple(sorted(style.items()))
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

