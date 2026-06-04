"""세션 / 윈도우 / 패널(분할 트리) 모델."""
from __future__ import annotations

import asyncio  # noqa: F401  (타입 주석용)
import re
import time

import pyte

from .protocol import HISTORY, MIN_H, MIN_W, conv_color, set_winsize

class _BCEMixin:
    """배경색 소거(BCE) 동작을 하는 화면 믹스인.

    pyte 기본 erase 는 지운 빈 칸에 커서의 현재 SGR 속성을 통째로 채운다.
    그래서 프로그램이 밑줄(또는 굵게 등)을 켠 채 줄·화면을 지우면, 실제 터미널
    (xterm 류의 BCE = 배경색만 유지)과 달리 빈 칸에 밑줄이 그대로 남아
    화면 전체에 원치 않는 가로줄이 보인다(예: Claude Code 환영 화면).

    여기서는 erase 시 배경색·전경색·반전만 보존하고 밑줄/굵게/기울임/취소선/
    깜빡임 같은 글자 장식 속성은 버려 실제 터미널 동작에 맞춘다.
    """

    def _erase_char(self):
        return self.cursor.attrs._replace(
            data=" ", bold=False, italics=False, underscore=False,
            strikethrough=False, blink=False)

    def erase_characters(self, count=None):
        self.dirty.add(self.cursor.y)
        count = count or 1
        blank = self._erase_char()
        line = self.buffer[self.cursor.y]
        for x in range(self.cursor.x,
                       min(self.cursor.x + count, self.columns)):
            line[x] = blank

    def erase_in_line(self, how=0, private=False):
        self.dirty.add(self.cursor.y)
        if how == 0:
            interval = range(self.cursor.x, self.columns)
        elif how == 1:
            interval = range(self.cursor.x + 1)
        else:
            interval = range(self.columns)
        blank = self._erase_char()
        line = self.buffer[self.cursor.y]
        for x in interval:
            line[x] = blank

    def erase_in_display(self, how=0, *args, **kwargs):
        if how == 0:
            interval = range(self.cursor.y + 1, self.lines)
        elif how == 1:
            interval = range(self.cursor.y)
        elif how in (2, 3):
            interval = range(self.lines)
        else:
            interval = range(0)
        self.dirty.update(interval)
        blank = self._erase_char()
        for y in interval:
            line = self.buffer[y]
            for x in list(line):
                line[x] = blank
        if how == 0 or how == 1:
            self.erase_in_line(how)


class _BCEScreen(_BCEMixin, pyte.Screen):
    pass


class _BCEHistoryScreen(_BCEMixin, pyte.HistoryScreen):
    pass


# 대체 화면 버퍼(alternate screen) 전환 시퀀스. pyte 가 직접 지원하지 않아
# pytmux 가 직접 처리한다(vim/less/htop/Claude Code 등 풀스크린 TUI 용).
_ALT_RE = re.compile(rb"\x1b\[\?(1049|1047|47)(h|l)")
# feed 경계에서 잘린 미완성 CSI 시퀀스(예: ESC[?104, ESC[4: 등)를 다음 feed 로
# 미룬다. ESC 단독 또는 ESC[ + 종결바이트 없는 파라미터를 끝에서 잡아낸다.
# 이렇게 해야 _ALT_RE 라우팅과 아래 _sanitize_sgr 가 항상 완전한 시퀀스만 본다.
_CSI_PARTIAL_RE = re.compile(rb"\x1b(?:\[[0-9:;?<>=!]*)?$")

# 콜론(:) 서브파라미터를 쓰는 현대식 SGR(`m`)을 pyte 0.8.2 가 이해하는 세미콜론
# 형태로 정규화한다. pyte 의 CSI 파서는 콜론을 미지 문자로 보고 시퀀스를 **중단**
# 하므로, 밑줄 끄기(`CSI 4:0 m`)·곱슬밑줄(`4:3`)·24bit 색(`38:2::r:g:b`)·밑줄색
# (`58:...`) 같은 시퀀스가 들어오면 밑줄 속성이 꺼지지 않고 그대로 남아 화면 전체에
# 밑줄이 번지거나 "0m" 같은 잔해가 찍힌다(특히 capable 터미널을 감지한 Claude Code).
# 자세한 배경은 docs/HANDOFF.md §10 참조.
_SGR_RE = re.compile(rb"\x1b\[([0-9:;]*)m")

# private-marker(<,>,=)가 붙은 `m` 종결 CSI 를 제거한다. 대표적으로 XTMODKEYS
# (`CSI > 4 ; Ps m`, modifyOtherKeys) — capable 터미널을 감지한 Claude Code 가
# 켜기/끄기로 내보낸다. pyte 0.8.2 의 CSI 파서는 `>` 마커를 무시하고 이를 그냥
# `CSI 4 ; Ps m`(=SGR 밑줄 ON)으로 잘못 읽어, 이후 모든 셀에 밑줄이 번진다(화면
# 전체 밑줄 버그). pytmux 는 자체 키보드 프로토콜을 다루므로 이 시퀀스를 pyte 에
# 넘길 이유가 없어 통째로 버린다. 자세한 배경은 docs/HANDOFF.md §10 참조.
_PRIVATE_SGR_RE = re.compile(rb"\x1b\[[<>=][0-9;:]*m")

# 내부 앱의 마우스 트래킹 DECSET. 1000=press/release, 1002=+drag, 1003=any-motion,
# 1006=SGR 확장 좌표 인코딩. 클라이언트의 마우스 패스스루 판단에 쓰인다.
_MOUSE_RE = re.compile(rb"\x1b\[\?(1000|1002|1003|1006)(h|l)")


def _rewrite_sgr_token(tok: bytes) -> bytes | None:
    """콜론 서브파라미터를 가진 단일 SGR 파라미터를 세미콜론 형태로 변환.

    반환값을 그대로 세미콜론 목록에 끼워 넣는다. None 이면 그 파라미터를 버린다
    (pyte 가 표현 못하는 밑줄색 등). 콜론이 없는 토큰은 호출 전에 걸러진다.
    """
    sub = tok.split(b":")
    head = sub[0]
    if head == b"4":
        # 밑줄 스타일: 4:0 = 끄기(=24), 그 외(4:1~4:5 곱슬/이중 등) = 켜기(=4).
        val = sub[1] if len(sub) > 1 else b""
        return b"24" if val in (b"0", b"") else b"4"
    if head in (b"38", b"48"):
        kind = sub[1] if len(sub) > 1 else b""
        nums = [p for p in sub[2:] if p != b""]
        if kind == b"5" and nums:
            return head + b";5;" + nums[0]
        if kind == b"2" and len(nums) >= 3:
            # 38:2:<colorspace>:r:g:b — colorspace 가 비어도 마지막 3개가 r,g,b.
            return head + b";2;" + b";".join(nums[-3:])
        return None  # 형식 불명 → 버림(잔해 방지)
    # 58:(밑줄색) 등 pyte 미지원 → 버림.
    return None


def _sanitize_sgr(buf: bytes) -> bytes:
    if b":" not in buf:   # 콜론이 없으면 정규화할 SGR 도 없다(흔한 경로).
        return buf

    def repl(mo: "re.Match[bytes]") -> bytes:
        params = mo.group(1)
        if b":" not in params:
            return mo.group(0)   # 평범한 SGR 은 그대로.
        out = []
        for tok in params.split(b";"):
            if b":" not in tok:
                out.append(tok)
                continue
            rewritten = _rewrite_sgr_token(tok)
            if rewritten is not None:
                out.append(rewritten)
        if not out:
            # 전부 버려졌으면 시퀀스 자체를 제거한다. `CSI m`(=reset all)으로
            # 두면 앞서 설정된 굵게/색까지 의도치 않게 초기화된다.
            return b""
        return b"\x1b[" + b";".join(out) + b"m"

    return _SGR_RE.sub(repl, buf)


class Pane:
    """잎 노드. 셸 PTY + pyte 화면 버퍼 + 스크롤백 뷰포트."""

    def __init__(self, pid: int, fd: int, cols: int, rows: int):
        self.id = pid_counter()
        self.master_fd = fd
        self.child_pid = pid
        self.cols = cols
        self.rows = rows
        # 메인 화면(스크롤백 보관) + 대체 화면(풀스크린 TUI 용, 스크롤백 없음)
        self._main = _BCEHistoryScreen(cols, rows, history=HISTORY, ratio=0.5)
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
        # Claude Code 감지: 상태(idle/busy/limit/None)와 마지막 입력 프롬프트
        self._claude = None
        self._claude_usage = None  # "ctx 42%" / "12k tok" 등(best-effort)
        # 토큰 누적(tokens.py): 현재 응답 peak + 세션 누계. _session_tokens 는
        # 표시·전송용 캐시(= _tok_state["total"]). 새 Claude 세션마다 리셋.
        self._tok_state = {"peak": 0, "total": 0}
        self._session_tokens = 0
        self._inbuf = ""         # 현재 입력 줄 누적(프롬프트 추적용)
        self.last_prompt = ""    # 마지막으로 제출한 프롬프트(한 줄)
        self.prompt_history = []  # 시간순 제출 프롬프트 목록(히스토리 팝업용)
        self.pending_prompts = []  # busy 중 입력해 큐된 프롬프트(#4, 처리 시작 시 승격)
        self.search_query = ""   # 스크롤백 검색어
        self._match_abs = None   # 현재 매치된 절대 라인 인덱스
        self.bracketed = False   # 내부 앱이 bracketed paste 모드를 켰는지
        # 내부 앱의 마우스 트래킹 모드(DECSET). 클라이언트가 이 패널로 마우스를
        # 패스스루할지/어떻게 인코딩할지 판단하는 데 쓴다(서버가 추적해 전달).
        self._mouse_modes = set()   # 켜진 {1000,1002,1003}
        self.mouse_track = 0        # 0=off 1=press/release 2=+drag 3=any-motion
        self.mouse_sgr = False      # 1006 SGR 확장 좌표 인코딩 사용 여부
        self._mouse_sent = (0, False)  # 클라이언트로 마지막 전달한 (track, sgr)
        self.pipe_proc = None    # pipe-pane 대상 프로세스
        # PTY 백엔드 핸들(pty_backend.PtyProcess). 서버가 spawn 직후 주입한다.
        # 렌더 전용(replay/진단) 패널은 None — master_fd/child_pid 만 -1 로 둔다.
        self.pty = None

    def reinit(self, pid: int, fd: int, cols: int, rows: int) -> None:
        """respawn: 새 PTY/셸로 화면 버퍼를 초기화한다."""
        self.master_fd = fd
        self.child_pid = pid
        self.pty = None          # 서버가 reinit 직후 새 PtyProcess 를 주입
        self.cols, self.rows = cols, rows
        self._main = _BCEHistoryScreen(cols, rows, history=HISTORY, ratio=0.5)
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
        self._tok_state = {"peak": 0, "total": 0}
        self._session_tokens = 0
        self.search_query = ""
        self._match_abs = None
        self.bracketed = False
        self._mouse_modes = set()
        self.mouse_track = 0
        self.mouse_sgr = False
        self._mouse_sent = (0, False)

    def update_mouse_modes(self, data: bytes) -> bool:
        """피드 데이터에서 마우스 트래킹 DECSET(1000/1002/1003/1006)을 추적한다.
        bracketed paste(2004) 추적과 같은 위치에서 호출. 상태가 바뀌면 True 를
        반환해 서버가 클라이언트에 레이아웃을 다시 보내게 한다."""
        if b"\x1b[?100" not in data:   # 1000/1002/1003/1006 모두 이 접두사
            return False
        before = (self.mouse_track, self.mouse_sgr)
        for mo in _MOUSE_RE.finditer(data):
            mode = int(mo.group(1))
            on = mo.group(2) == b"h"
            if mode == 1006:
                self.mouse_sgr = on
            elif on:
                self._mouse_modes.add(mode)
            else:
                self._mouse_modes.discard(mode)
        self.mouse_track = (3 if 1003 in self._mouse_modes
                            else 2 if 1002 in self._mouse_modes
                            else 1 if 1000 in self._mouse_modes else 0)
        return (self.mouse_track, self.mouse_sgr) != before

    # 레이아웃 계산용
    def first_pane(self) -> "Pane":
        return self

    def feed(self, data: bytes) -> None:
        # 대체 화면 전환 시퀀스를 가로채 메인/대체 화면으로 라우팅한다.
        buf = self._altcarry + data
        self._altcarry = b""
        m = _CSI_PARTIAL_RE.search(buf)
        if m:  # 끝에 잘린 CSI 시퀀스는 다음 feed 로 미룸(완전한 시퀀스만 처리)
            self._altcarry = buf[m.start():]
            buf = buf[:m.start()]
        buf = _PRIVATE_SGR_RE.sub(b"", buf)   # XTMODKEYS 등 `CSI >..m` 제거(밑줄 오인 방지)
        buf = _sanitize_sgr(buf)   # 콜론식 SGR → pyte 가 이해하는 세미콜론 형태
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
        self._alt = _BCEScreen(self.cols, self.rows)
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
        # PTY 크기 통지는 백엔드 핸들을 통해(크로스플랫폼). 렌더 전용 패널(pty=None)은
        # 옛 fd 기반 set_winsize 로 폴백(fd=-1 이면 무해하게 실패).
        if self.pty is not None:
            try:
                self.pty.set_winsize(rows, cols)
            except OSError:
                pass
        else:
            # Windows 의 렌더 전용 패널: set_winsize 가 fcntl/termios 를 지연
            # import 하므로 ModuleNotFoundError 가 날 수 있다(OSError 아님). 폭만
            # 못 알릴 뿐 무해하므로 함께 삼킨다.
            try:
                set_winsize(self.master_fd, rows, cols)
            except (OSError, ImportError):
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
    """탭에 종속된 단일 윈도우: 패널 집합(분할 트리)을 보유하는 렌더 영역.

    상위 컨테이너는 :class:`Tab` 이며(탭 1개 = 윈도우 1개), 탭이 이름/인덱스 등
    전환 단위 정보를 갖는다. 윈도우는 패널 트리와 줌/동기화/모니터 상태를 갖는다.
    """

    def __init__(self, root: Pane):
        self.root = root
        self._active = root    # 활성 패널(프로퍼티로 last-pane 추적)
        self._last = None      # 직전 활성 패널(prefix ;)
        self.zoomed = False    # 활성 패널 전체화면(prefix z)
        self.layout_idx = 0    # 레이아웃 프리셋 순환 인덱스
        self.sync = False      # 입력 동기화(synchronize-panes)
        self.border_status = False  # 패널 제목 경계선 표시(pane-border-status)
        self.auto_rename = True  # 활성 패널 명령으로 탭 이름 자동 갱신
        # 활동/벨 모니터 플래그(monitor_*/has_*)는 상위 Tab 이 보유한다.

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
        # 자식은 경계 셀을 공유(겹침)한다. 각 패널이 자기 테두리 박스를 그리므로
        # 경계 열/행을 양쪽 패널 테두리가 같은 셀로 공유한다(한 변당 최소 MIN).
        if node.orient == "lr":
            if w >= MIN_W * 2:
                bx = max(MIN_W, min(w - MIN_W, round((w - 1) * node.ratio)))
            else:
                bx = max(1, min(w - 1, (w - 1) // 2))
            divs.append({"split_id": node.id, "orient": "lr",
                         "x": x + bx, "y": y, "w": 1, "h": h,
                         "rect": [x, y, w, h]})
            self._layout(node.a, x, y, bx + 1, h, panes, divs)        # [x, x+bx]
            self._layout(node.b, x + bx, y, w - bx, h, panes, divs)   # [x+bx, x+w-1]
        else:
            if h >= MIN_H * 2:
                by = max(MIN_H, min(h - MIN_H, round((h - 1) * node.ratio)))
            else:
                by = max(1, min(h - 1, (h - 1) // 2))
            divs.append({"split_id": node.id, "orient": "tb",
                         "x": x, "y": y + by, "w": w, "h": 1,
                         "rect": [x, y, w, h]})
            self._layout(node.a, x, y, w, by + 1, panes, divs)        # [y, y+by]
            self._layout(node.b, x, y + by, w, h - by, panes, divs)   # [y+by, y+h-1]

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


class Tab:
    """최상위 전환 단위. 정확히 하나의 :class:`Window` 를 종속으로 가진다.

    이름/인덱스(상태표시줄 탭)와 출력 활동/벨 표시를 보유한다. 새 탭을 만들면
    새 윈도우(단일 패널)가 생기고 이를 패널로 분할한다.
    """

    def __init__(self, index: int, name: str, window: "Window"):
        self.index = index
        self.name = name
        self.window = window
        self.has_activity = False
        self.has_bell = False
        self.has_claude_done = False   # 비활성 탭 Claude 작업 완료(busy→idle) 알림
        self.monitor_activity = False
        self.monitor_bell = True
        self.monitor_claude = True


class Session:
    def __init__(self, name: str, root: Pane):
        self.name = name
        self.created_at = time.time()
        self.tabs = [Tab(0, "win", Window(root))]
        self.active_index = 0
        self.last_index = 0    # 직전 활성 탭(prefix l)

    @property
    def active_tab(self) -> Tab | None:
        if not self.tabs:
            return None
        self.active_index = max(0, min(self.active_index, len(self.tabs) - 1))
        return self.tabs[self.active_index]

    @property
    def active_window(self) -> Window | None:
        t = self.active_tab
        return t.window if t else None


class ClientConn:
    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.session: Session | None = None
        self.cols = 80
        self.rows = 24

