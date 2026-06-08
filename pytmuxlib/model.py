"""세션 / 윈도우 / 패널(분할 트리) 모델."""
from __future__ import annotations

import asyncio  # noqa: F401  (타입 주석용)
import re
import time
from collections import deque
from functools import lru_cache

import pyte
from pyte.screens import Margins

from .protocol import HISTORY, MIN_H, MIN_W, conv_color, set_winsize


@lru_cache(maxsize=8192)
def _style_key(fg, bg, bold, italics, underscore, reverse, strike):
    """Char 스타일 필드 → 직렬화 런 키(정렬된 튜플). render 핫루프가 셀마다
    dict 생성+sort 하던 것을 메모이즈한다(B3) — 대부분 셀이 같은 스타일이라 적중률
    높음. 키에서 `dict(key)` 로 스타일 dict 를 복원한다(클라 _darken_style lru_cache 선례)."""
    d = {}
    f = conv_color(fg)
    b = conv_color(bg)
    if f:
        d["f"] = f
    if b:
        d["b"] = b
    if bold:
        d["bo"] = 1
    if italics:
        d["it"] = 1
    if underscore:
        d["un"] = 1
    if reverse:
        d["rv"] = 1
    if strike:
        d["st"] = 1
    return tuple(sorted(d.items()))


# restart-all 스냅샷(_export_screen)이 색/속성을 보존하도록 pyte 셀 속성 → SGR
# 이스케이프로 환원한다. pyte fg/bg 는 "default"·기본색명·"bright<name>"·6자리 hex.
_SGR_BASE = {"black": 30, "red": 31, "green": 32, "brown": 33, "yellow": 33,
             "blue": 34, "magenta": 35, "cyan": 36, "white": 37}


def _sgr_color(c, is_bg: bool):
    """pyte 색값 → SGR 코드 리스트(기본색이면 []). is_bg 면 배경 오프셋(+10)."""
    if not c or c == "default":
        return []
    off = 10 if is_bg else 0
    if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return [48 if is_bg else 38, 2,
                int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)]
    name, bright = c, False
    if name.startswith("bright"):
        bright, name = True, name[6:]
    base = _SGR_BASE.get(name)
    if base is None:
        return []
    return [base + off + (60 if bright else 0)]


@lru_cache(maxsize=8192)
def _cell_sgr(fg, bg, bold, italics, underscore, reverse, strike) -> str:
    """셀 속성 → SGR 이스케이프 문자열(기본 속성이면 빈 문자열). _export_screen 이
    런 경계마다 끼워 색/굵기/밑줄 등을 복원 feed 에 실어 보낸다. 캐시 적중률 높음."""
    codes = []
    if bold:
        codes.append(1)
    if italics:
        codes.append(3)
    if underscore:
        codes.append(4)
    if reverse:
        codes.append(7)
    if strike:
        codes.append(9)
    codes += _sgr_color(fg, False)
    codes += _sgr_color(bg, True)
    if not codes:
        return ""
    # 런 시작마다 reset(0) 후 속성을 다시 깔아 직전 런 잔여 속성이 안 새게 한다.
    return "\x1b[0;" + ";".join(str(c) for c in codes) + "m"


def _cell_sgr_for(ch) -> str:
    """pyte Char 셀에서 _cell_sgr 캐시 키를 뽑아 SGR 문자열을 얻는다."""
    return _cell_sgr(ch.fg, ch.bg, ch.bold, ch.italics, ch.underscore,
                     ch.reverse, ch.strikethrough)


# 폭 축소(shrink) 직후의 폭-불일치 전환 윈도우 길이(초). 이 동안만 autowrap 을 꺼
# cascade 대신 truncate 시킨다(_BCEMixin.draw 참조). SIGWINCH 왕복+리페인트보다 넉넉히
# 길고(분주한 박스 대비), 정상 폭에선 어차피 오버플로가 없어 무해하므로 보수적으로 잡는다.
WRAP_GUARD_SEC = 0.4


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

    # --- soft-wrap(자동 줄바꿈) 표시 ---
    # 복사 시 "자동 줄바꿈으로 이어진 줄"을 한 줄로 잇기 위한 정확 신호(휴리스틱 아님).
    # pyte 의 draw() 는 DECAWM 자동 줄바꿈일 때만 그 내부에서 self.linefeed() 를
    # 부른다(명시적 \n·VT·FF 는 Stream 이 draw 밖에서 linefeed 를 부름). 따라서
    # draw 진입 동안만 _drawing 을 켜 두면, linefeed 가 그 플래그로 "이 줄바꿈은
    # wrap 인가"를 무오류로 구분한다. wrap 이면 떠나는 줄(아직 cursor.y)의 줄 객체에
    # wrapped 를 태그한다 — 줄 객체는 스크롤백(history.top)으로 밀려가도 그대로
    # 따라가므로(같은 dict 참조), 화면 밖으로 스크롤된 wrap 도 보존된다.
    # render() 가 이 태그를 (현재 마지막 칸이 비어 있지 않을 때만) 내보내, 이후
    # 줄 끝이 지워지거나 당겨진 stale 태그는 자동으로 무효화된다.
    def draw(self, data):
        self._drawing = True
        # 폭 축소 직후 짧은 전환 윈도우(_wrap_guard_until): ConPTY/Claude 가 새 폭을
        # SIGWINCH 로 인식하기 전까지 옛(넓은) 폭으로 계속 그릴 수 있다. 그 바이트가
        # 이미 좁아진 pyte 에 들어오면 전폭 줄(예: /compact 의 ─ 구분선)이 autowrap 돼
        # 다음 줄로 흘러, Claude 의 커서 상대 리페인트 좌표까지 어긋나며 아래 줄이 전부
        # 밀린다(docs/HANDOFF.md — /compact 진행 표시 정렬 깨짐). 이 윈도우 동안만
        # DECAWM(자동 줄바꿈)을 꺼 두면, 넘치는 글자가 다음 줄로 흐르는 대신 마지막
        # 칸을 덮어쓴다(pyte draw: cursor.x==columns 또 DECAWM off → cursor.x-=width).
        # → cascade 대신 한 칸 truncate. 폭이 맞으면 오버플로 자체가 없어 동작 불변이라
        # (CR 이 마지막 글자 직후 오므로 wrap 분기에 안 들어감) 정상 출력엔 무해하다.
        guard = (pyte.modes.DECAWM in self.mode
                 and time.monotonic() < getattr(self, "_wrap_guard_until", 0.0))
        if guard:
            self.mode.discard(pyte.modes.DECAWM)
        try:
            super().draw(data)
        finally:
            if guard:
                self.mode.add(pyte.modes.DECAWM)
            self._drawing = False

    def linefeed(self):
        if getattr(self, "_drawing", False):
            # carriage_return() 직후·super().linefeed() 직전 — cursor.y 는 아직
            # '떠나는 줄'이라 그 줄을 wrap 연속원으로 태그한다.
            try:
                self.buffer[self.cursor.y].wrapped = True
            except Exception:
                pass
        super().linefeed()

    def resize(self, lines=None, columns=None):
        old_cols = self.columns
        super().resize(lines, columns)
        # 폭이 줄면 전환 윈도우 동안 autowrap 을 꺼 cascade 를 막는다(draw 참조).
        if columns is not None and columns < old_cols:
            self._wrap_guard_until = time.monotonic() + WRAP_GUARD_SEC
        # pyte 의 resize 는 탭스톱을 재계산하지 않는다. 패널은 spawn 시 MIN_W(=3)로
        # 만든 뒤 실제 폭으로 키우는데(특히 분할 새 패널), 폭 3에선 표준 탭스톱
        # range(8,3,8) 이 빈 집합이라 그대로 남는다. 탭스톱이 비면 pyte 의 TAB 은
        # 다음 8-칸 정지점이 아니라 줄 끝(마지막 칸)으로 커서를 보내, ls 처럼 탭으로
        # 컬럼을 맞추는 출력에서 다음 이름의 첫 글자가 줄 끝에 찍히고 나머지가
        # 다음 줄로 쪼개진다(사용자 보고: 좁은 우측 패널의 ls). 새 폭 기준 표준
        # 8-칸 탭스톱을 다시 세워 실제 터미널과 동일하게 만든다.
        self.tabstops = set(range(8, self.columns, 8))


class _BCEScreen(_BCEMixin, pyte.Screen):
    pass


class _History:
    """pyte History 네임드튜플의 경량 대체. pytmux 는 top/bottom 두 deque 만 쓴다
    (render·capture_pane·clear_history·_export_screen). pyte 의 ratio/size/position
    필드와 인터랙티브 페이징 상태는 쓰지 않으므로 보관하지 않는다."""

    __slots__ = ("top", "bottom")

    def __init__(self, maxlen: int):
        self.top = deque(maxlen=maxlen)
        self.bottom = deque(maxlen=maxlen)


class _ScrollbackScreen(_BCEMixin, pyte.Screen):
    """plain pyte Screen + 위로 밀려난 줄만 자체 deque 에 모으는 경량 스크롤백.

    pyte.HistoryScreen 을 대체한다. HistoryScreen 은 인터랙티브 페이징(prev/next
    _page)을 위해 `__getattribute__` 로 **모든 속성 접근**을 가로채 before/after
    _event 를 끼워 넣는데, 이 훅이 draw() 의 글자별 속성 접근마다 호출돼 feed 가
    ~3배 느려진다(1MB 피드에 __getattribute__ 1,100만+ 회 — 시간의 절반). pytmux 는
    그 페이징을 전혀 쓰지 않고 history.top 만 읽으므로, HistoryScreen 이 하던 줄
    수집(index/reverse_index)만 동일하게 재현하고 훅을 제거한다.

    수집 조건·대상 줄은 HistoryScreen.index/reverse_index 와 바이트 단위로 동일해
    스크롤백 동작에 회귀가 없다. .history.top/.bottom deque 인터페이스도 그대로라
    기존 호출부(render·capture_pane·clear_history·_export_screen)는 변경 불필요."""

    def __init__(self, columns: int, lines: int,
                 history: int = HISTORY, ratio: float = 0.5):
        # super().__init__ 가 reset() 을 호출하므로 history 를 먼저 만든다
        # (HistoryScreen 과 동일한 순서).
        self.history = _History(history)
        super().__init__(columns, lines)

    def index(self) -> None:
        """전체 화면이 위로 스크롤될 때 빠지는 맨 윗줄을 top 히스토리에 모은다
        (HistoryScreen.index 와 동일)."""
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom:
            self.history.top.append(self.buffer[top])
        super().index()

    def reverse_index(self) -> None:
        """아래로 스크롤될 때 빠지는 맨 아랫줄을 bottom 히스토리에 모은다
        (HistoryScreen.reverse_index 와 동일)."""
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == top:
            self.history.bottom.append(self.buffer[bottom])
        super().reverse_index()

    def reset(self) -> None:
        """RIS(ESC c) 등에서 화면과 함께 스크롤백도 비운다(HistoryScreen.reset 과
        동일). __init__ 의 super().__init__ → reset() 경로에서도 호출되므로
        history 가 먼저 존재해야 한다."""
        super().reset()
        self.history.top.clear()
        self.history.bottom.clear()


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

# 풀스크린 클리어(erase-in-display all): CSI 2J / CSI 3J. alt-screen 에서 이 시퀀스
# 이전에 그려진 내용은 전부 지워지므로(스크롤백 없음) 그 앞 바이트는 화면에 안 보인다.
_FULL_CLEAR_RE = re.compile(rb"\x1b\[[23]J")


def coalesce_alt_repaints(buf: bytes, alt_active: bool) -> bytes:
    """alt-screen 풀스크린 리페인트 버스트에서 무효화된 중간 프레임 바이트를 버려
    pyte feed 부하를 줄인다(docs/HANDOFF.md §10 대응 ②, "같은 프레임 다중 리페인트
    합치기"). Claude busy 스피너처럼 매 프레임 화면을 통째로 다시 그리는 출력이
    feed 속도보다 빠르게 쌓일 때, 마지막 한 프레임만 보이므로 그 앞을 드롭한다.

    안전 조건(하나라도 어긋나면 buf 를 **그대로 반환** — 손실 없음):
      - ``alt_active`` 여야 한다. main-screen 은 위로 밀린 줄이 스크롤백에 쌓이므로
        바이트를 버리면 스크롤백을 잃는다(절대 드롭 금지).
      - buf 안에 alt-screen 전환(``_ALT_RE``: ?1049/?1047/?47 h|l)이 없어야 한다.
        있으면 버퍼가 화면 경계를 가로질러 단순 드롭이 안전하지 않다.
      - 풀클리어(CSI 2J/3J)가 **2개 이상**이어야 한다 — 즉 이미 여러 프레임이 밀려
        '뒤처진' 상태일 때만 합친다(중간 프레임들은 각자 상태를 세팅·리셋하는 완결된
        리페인트라, 마지막 클리어 이후 프레임이 자기 상태를 다시 세운다).

    드롭 지점은 **마지막 풀클리어의 시작 위치 이전 전부**다. 클리어 자체와 그 뒤
    리페인트는 온전히 남으므로 "화면을 비우고 새 프레임을 그린다"는 결과가 보존된다.
    """
    if not alt_active:
        return buf
    if _ALT_RE.search(buf):
        return buf
    last = -1
    count = 0
    for m in _FULL_CLEAR_RE.finditer(buf):
        last = m.start()
        count += 1
    if count < 2 or last <= 0:
        return buf
    return buf[last:]


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
        self._main = _ScrollbackScreen(cols, rows, history=HISTORY, ratio=0.5)
        self._main.set_mode(pyte.modes.LNM)
        self._main_stream = pyte.ByteStream(self._main)
        self._alt = None
        self._alt_stream = None
        self.alt_active = False
        self.screen = self._main      # 현재 활성 화면(렌더 대상)
        self._altcarry = b""          # feed 경계의 미완성 시퀀스 보관
        # 대량 출력 청크 드레인(server._feed_drain): PTY 에서 읽었으나 아직 pyte 에
        # 안 먹인 바이트와, 진행 중인 비동기 드레인 태스크(서버가 생성/취소 관리).
        self._feedbuf = b""
        self._feed_task = None
        self.scroll = 0          # 0 = live(맨 아래), 양수 = 위로 N 행
        self.dirty = True
        # 행 단위 재직렬화 캐시(#8): 직전 render 의 행 직렬화 결과(라이브 뷰 한정).
        # 다음 render 는 pyte screen.dirty 가 표시한 행만 다시 만들고 나머지는 이
        # 캐시를 재사용한다. _row_cache_key=(cols,lines,id(screen))로 크기·alt 전환을
        # 감지해 무효화한다. render 가 패널당 flush당 1회라 클라 델타와 충돌 없음.
        self._row_cache = None
        self._row_cache_key = None
        self._last_wrap = []     # 직전 render 의 soft-wrap 연속원 행(프레임 상대 인덱스)
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
        # Claude 스캔 dirty 게이팅(B1): feed 마다 _feed_seq 증가. _scan_claude 가
        # 마지막 스캔 때 본 seq(_scan_seq)와 같으면 화면 텍스트가 그대로 → 스캔 생략.
        self._feed_seq = 0
        self._scan_seq = -1
        # Claude Code 감지: 상태(idle/busy/limit/None)와 마지막 입력 프롬프트
        self._claude = None
        self._claude_usage = None  # "ctx 42%" / "12k tok" 등(best-effort)
        self._claude_model = None  # M14c: 모델 배지(opus-4.8 등, best-effort)
        self._ctx_pct = None       # M15: 마지막 컨텍스트 잔량%(우선순위 정리 비교용)
        # 토큰 누적(tokens.py): 현재 응답 peak + 세션 누계. _session_tokens 는
        # 표시·전송용 캐시(= _tok_state["total"]). 새 Claude 세션마다 리셋.
        self._tok_state = {"peak": 0, "total": 0}
        self._session_tokens = 0
        # 토큰 영속 로깅(#7): 현재 Claude 세션 id(None→Claude 전이마다 새로 부여)와
        # 마지막 감지/지정한 계정(로그 계정별 구분용). manual=사용자 수동 지정 여부.
        self._claude_session_id = 0
        self._claude_account = None
        self._claude_account_manual = False
        self._inbuf = ""         # 현재 입력 줄 누적(프롬프트 추적용)
        self.last_prompt = ""    # 마지막으로 제출한 프롬프트(한 줄)
        self.prompt_history = []  # 시간순 제출 프롬프트 목록(히스토리 팝업용)
        # 직전 status(주기 flush)에 실어 보낸 history[-30:] 슬라이스(§4.5 디바운스).
        # 변할 때만 다시 싣게 비교에 쓴다(휘발성; 매 프레임 재직렬화·전송 방지).
        self._hist_sent = None
        self.pending_prompts = []  # busy 중 입력해 큐된 프롬프트(#4, 처리 시작 시 승격)
        # 프롬프트 단위 클리어 모드(#9): 켜면 사용자 프롬프트가 busy→idle 로 끝날
        # 때마다 ① 문서화 지시 ② /clear 를 순차 주입하는 소형 상태기계를 돈다.
        # _pc_phase: None(대기) | "doc"(문서화 지시 처리 대기) | "clear"(/clear 처리 대기).
        self.prompt_clear_mode = False
        self._pc_phase = None
        # 프롬프트 단위 클리어 큐(#4): 사용자가 미리 쌓아 둔 명령들. 각 명령은
        # doc+/clear 사이클을 마칠 때마다 _pc_advance 가 하나씩 투입한다.
        self.prompt_clear_queue = []
        # 자동 doc→/clear(§10): Claude 가 idle 로 N초 지속되면 1회 문서화→/clear 를
        # 자동 수행한다(서버 옵션 auto_doc_clear 가 켜졌을 때만). _adc_timer 는 무장된
        # asyncio 타이머 핸들(없으면 None), _adc_active 는 자동 시퀀스 진행 중 여부
        # (진행 중엔 _pc_phase 상태기계를 prompt_clear_mode 와 공유). 둘 다 휘발성이라
        # 재시작 직렬화(_RESUME_FIELDS) 대상이 아니다.
        self._adc_timer = None
        self._adc_active = False
        # 자동 /compact(요청): idle 가 auto_compact_delay 초 지속되면 1회 '/compact'
        # +Enter 주입. _acpt_timer 는 무장된 타이머 핸들(없으면 None, 휘발성).
        # _acpt_fired 는 이미 1회 발화했음을 표시하는 디바운스 플래그 — /compact
        # 주입이 스스로 busy→idle 경계를 또 만들어 무한 재발화하는 것을 막는다(요청).
        # 사용자가 실제로 입력하면(serverio) 해제돼 다음 idle 에 다시 발화 가능.
        self._acpt_timer = None
        self._acpt_fired = False
        # 시간기반 자동 compact·doc-clear 쿨다운 만료 시각(time.monotonic; 0=쿨다운
        # 없음). 새 세션 시작·직전 compact·직전 clear 직후엔 이 시각 전까지 _acpt_arm/
        # _adc_arm 을 무장하지 않는다(요청 — 새 세션·정리 직후 곧바로 또 압축/정리 방지).
        # 휘발성(monotonic 은 프로세스 한정이라 직렬화 안 함 — 재시작 시 0 으로 리셋).
        self._auto_cc_cooldown_until = 0.0
        # 권한모드 자동 오토모드 전환(§10): idle 일 때 footer 가 auto 가 아니면
        # shift+tab 을 순환 주입한다(서버 옵션 claude_auto_mode 가 켜졌을 때만).
        # _cam_tries 는 이번 idle 진입 후 보낸 횟수(무한 순환 가드 _CAM_MAX), _cam_last
        # 는 직전에 관측·작용한 권한모드(화면 갱신 전 중복 주입 방지). 둘 다 휘발성.
        self._cam_tries = 0
        self._cam_last = None
        # 현재 관측된 권한모드(claude_perm_mode 결과: auto/plan/default/bypass/None)와
        # 사용자가 footer 클릭 팝업으로 고른 목표 모드(§10 item 2). _perm_target 가
        # 있으면 idle 시 그 모드까지 shift+tab 을 폐루프로 순환 주입한다(도달/포기 시
        # None 으로). _perm_mode 는 status 로 클라에 보내 팝업의 '현재 모드' 표시에 씀.
        self._perm_mode = None
        self._perm_target = None
        # 비활성 탭 Claude 완료 알림(#22) 플리커 방지(§10 #18): busy↔idle 가 한 프레임
        # 흔들릴 때 done 이 잘못 서는 걸 막으려고, busy 를 본 뒤 idle 이 연속 N프레임
        # 안정될 때만 완료로 친다. _was_busy=직전에 busy 였음, _idle_frames=연속 idle 수.
        self._was_busy = False
        self._idle_frames = 0
        # M17(T7): 반복 실패 루프(S8)·비정상 장기 턴(S9) 감지용. busy 진입 시각,
        # 직전 완료 화면 꼬리 해시·연속 동일 횟수, 표시용 경고 문자열(grade0 알림만).
        self._busy_since = None
        self._done_tail = None
        self._repeat_n = 0
        self._claude_warn = None
        # 세션 피드백 프롬프트 자동 Dismiss(#26): 첫 '0'이 누락돼도 프롬프트가
        # 사라질 때까지 GAP 프레임마다 최대 MAX_TRIES 회 재주입(정적 화면에도 스캔
        # 유지). active=재시도 중, tries=쏜 횟수, wait=다음 재시도까지 남은 프레임.
        self._feedback_active = False
        self._feedback_tries = 0
        self._feedback_wait = 0
        # 수동 /clear 감지 디바운스: 환영 배너가 화면에 머무는 동안 매 프레임 토큰
        # 세션을 재리셋하지 않게, 배너가 "새로 떴을" 때만 1회 끊는다(claude_welcome).
        self._welcome_seen = False
        self._rules_pending = False  # 시작 규칙 주입 예약(다음 idle 에 1회, #27)
        # 새 Claude 세션 자동 셋업(auto-launch): 시작 시 /rc(원격제어)를 1회 주입하고
        # (_rc_pending) 그 다음 idle 에 권한모드를 auto 로 1회 유도(_perm_auto_pending).
        # 프레임을 갈라 /rc 제출과 shift+tab 이 섞이지 않게 한다. 둘 다 휘발성.
        self._rc_pending = False
        self._perm_auto_pending = False
        # _rc_done: 이 살아 있는 Claude 세션에 auto /rc 를 이미 적용했음(또는 원격제어가
        # 켜진 걸 관측했음)을 표시하는 sticky 플래그. **재시작 시 직렬화**(_RESUME_FIELDS)
        # 돼 re-exec 후에도 유지된다 — re-exec 직후 _induce_redraw_all 의 강제 repaint 가
        # 순간 빈 프레임을 만들어 _claude 가 None→Claude 로 깜빡이면 거짓 "새 세션"으로
        # 오인돼 /rc 가 재주입되던 버그(이미 켜진 원격제어 패널이 다시 뜸)를 막는다.
        # 진짜 세션 종료(_hdr_claude 디바운스 off)에서만 해제해 다음 claude 기동엔 재무장.
        self._rc_done = False
        # 토큰 절감 자동화(docs/TOKEN_SAVING_SCENARIO.md). 둘 다 휘발성(재시작 비직렬화).
        # _resume_handle: 자동재개 예약 call_later 핸들 — busy 복귀 시 cancel 하려고
        #   들고 있는다(M12; 없으면 None). _ctx_fired: 컨텍스트 잔량 자동 정리(M11)가
        #   이번 저잔량 구간에 이미 발화했는지 — 잔량이 임계+히스테리시스 위로 회복하거나
        #   새 세션이 시작될 때까지 재발화를 막는다.
        self._resume_handle = None
        self._ctx_fired = False
        # _ctx_last_fire: 마지막 자동 정리(M11 _ctx_intervene) 발화 시각(time.monotonic).
        #   M14 빈도 상한 — 정리가 컨텍스트를 못 줄이는 오검출/병적 진동에서 매 완료
        #   경계 무한 정리를 막는 시간 바닥(§5.6). None=아직 발화 안 함(휘발성).
        self._ctx_last_fire = None
        # _layout_msg 가 이 패널에 Claude 헤더 한 행을 예약했는지(#1). 예약 유무가
        # 바뀌면 flush 루프가 레이아웃(PTY 리사이즈 포함)을 다시 보낸다.
        self._hdr_reserved = False
        # 헤더 예약(#1)용 **디바운스된** Claude 존재 플래그. `_claude` 는 화면
        # 텍스트 스크래핑이라 footer(예: "? for shortcuts")가 한 프레임 안 잡히면
        # None 으로 깜빡일 수 있는데, 그 raw 값을 그대로 _should_reserve_header 에
        # 쓰면 헤더 예약이 매 프레임 토글돼 PTY 가 ±1 행으로 리사이즈를 반복한다
        # → 원격(ssh) Claude 가 SIGWINCH 마다 리플로우해 화면이 한 줄씩 위아래로
        # 스크롤되는 떨림이 생긴다(Windows/ConPTY 는 화면이 조각나 도착해 footer
        # 없는 중간 프레임을 잡을 확률이 커 증상이 두드러진다). 그래서 Claude 가
        # 사라진 것으로 보여도 _HDR_CLAUDE_MISS 프레임 연속 None 이어야 예약을 푼다.
        self._hdr_claude = False       # 디바운스된 "이 패널은 Claude" 판정
        self._hdr_claude_miss = 0      # 연속으로 non-Claude 로 본 스캔 수
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
        self._main = _ScrollbackScreen(cols, rows, history=HISTORY, ratio=0.5)
        self._main.set_mode(pyte.modes.LNM)
        self._main_stream = pyte.ByteStream(self._main)
        self._alt = None
        self._alt_stream = None
        self.alt_active = False
        self.screen = self._main
        self._altcarry = b""
        self._feedbuf = b""
        self._feed_task = None
        self.scroll = 0
        self.dirty = True
        self._row_cache = None       # 행 재직렬화 캐시 리셋(#8; 새 화면 객체)
        self._row_cache_key = None
        self._scanbuf = ""
        self._resume_pending = False
        self._resume_handle = None   # 자동재개 예약 핸들 리셋(M12; 타이머는 자가만료)
        self._ctx_fired = False      # 컨텍스트 잔량 자동 정리 디바운스 리셋(M11)
        self._ctx_last_fire = None   # 정리 빈도 상한 시각 리셋(M14)
        self._tok_state = {"peak": 0, "total": 0}
        self._session_tokens = 0
        self._claude_session_id = 0
        self._claude_account = None
        self._claude_account_manual = False
        self._pc_phase = None    # 프롬프트 단위 클리어 상태기계 리셋(모드 자체는 유지)
        self.prompt_clear_queue = []  # 새 셸이므로 쌓인 명령 큐도 버린다(#4)
        self._adc_active = False  # 자동 doc→/clear 진행상태 리셋(§10; 타이머는 만료시 자가해제)
        self._cam_tries = 0       # 권한모드 자동전환 시도 카운터 리셋(§10)
        self._cam_last = None
        self._perm_mode = None    # 새 셸 — 권한모드 관측/목표 리셋(§10 item 2)
        self._perm_target = None
        self._was_busy = False    # done 플리커 디바운스 리셋(§10 #18)
        self._idle_frames = 0
        self._hdr_reserved = False
        self._hdr_claude = False
        self._hdr_claude_miss = 0
        self.search_query = ""
        self._match_abs = None
        self.bracketed = False
        self._mouse_modes = set()
        self.mouse_track = 0
        self.mouse_sgr = False
        self._mouse_sent = (0, False)

    # 작업 보존 재시작(re-exec)용 직렬화 — docs/RESTART_SCENARIO.md ⓑ/ⓓ.
    # setattr 로 그대로 복원 가능한 JSON 가능 스칼라/딕트 필드 목록. PTY 식별자
    # (child_pid·master_fd)와 크기·화면 스냅샷은 export_state 가 별도로 다룬다.
    _RESUME_FIELDS = (
        "title", "autoresume", "resume_msg", "last_prompt",
        "_claude", "_claude_usage", "_scanbuf", "_resume_pending",
        "_claude_session_id", "_claude_account", "_claude_account_manual",
        "_tok_state", "_session_tokens", "prompt_clear_mode", "bracketed",
        "_rc_done",   # re-exec 후 거짓 새세션에 /rc 재주입 방지(원격제어 패널 재발)
    )

    def _serialize_line(self, line, columns: int) -> str:
        """한 줄(pyte 버퍼 행)을 SGR(색/속성) 포함 문자열로. 마지막 비공백 셀까지만
        내보내고(뒤 공백 절약), 속성 없는 줄은 이스케이프 0(평문 그대로 — 회귀 없음).
        와이드 문자 연속 셀(data=="")은 건너뛴다(import feed 가 다시 만든다)."""
        last = -1
        for x in range(columns):
            d = line[x].data
            if d != "" and d != " ":
                last = x
        if last < 0:
            return ""
        # cur_sgr="" = 기본(reset) 상태 기준. 색→기본 전이는 명시적 reset 으로 닫는다.
        parts, cur_sgr = [], ""
        for x in range(last + 1):
            ch = line[x]
            if ch.data == "":
                continue
            sgr = _cell_sgr_for(ch)
            if sgr != cur_sgr:
                parts.append(sgr if sgr else "\x1b[0m")
                cur_sgr = sgr
            parts.append(ch.data)
        if cur_sgr:
            parts.append("\x1b[0m")
        return "".join(parts)

    def _export_screen(self) -> list[str]:
        """메인 화면(스크롤백+현재 버퍼) 전체를 SGR 포함 줄 목록으로(뒤 빈 줄 제거).
        스크롤-업 연속성·하위호환용. 정확 복원은 _export_history/_export_viewport 사용."""
        scr = self._main
        h = getattr(scr, "history", None)
        hist = list(h.top) if h is not None else []
        lines = hist + [scr.buffer[y] for y in range(scr.lines)]
        out = [self._serialize_line(line, scr.columns) for line in lines]
        while out and not out[-1]:
            out.pop()
        return out[-HISTORY:]

    def _export_history(self) -> list[str]:
        """스크롤백(화면 밖으로 밀린 줄)만 SGR 포함으로. 뒤 빈 줄 제거·HISTORY 캡."""
        scr = self._main
        h = getattr(scr, "history", None)
        hist = list(h.top) if h is not None else []
        out = [self._serialize_line(line, scr.columns) for line in hist]
        while out and not out[-1]:
            out.pop()
        return out[-HISTORY:]

    def _export_viewport(self) -> list[str]:
        """현재 화면(보이는 scr.lines 행)을 **빈 줄 트림 없이 그대로** SGR 포함으로.
        행 수·위치가 앱의 화면 모델과 정확히 일치해야 execv 후 부분 repaint(메인 화면
        TUI 의 SIGWINCH 갱신)가 어긋나지 않는다(restart-all 커서·줄 정합, B/D)."""
        scr = self._main
        return [self._serialize_line(scr.buffer[y], scr.columns)
                for y in range(scr.lines)]

    def export_state(self) -> dict:
        """재시작 시 보존할 패널 상태를 JSON 가능 dict 로 직렬화한다.

        PTY 식별자(child_pid·master_fd 번호)·크기·마우스 모드·프롬프트 큐·화면
        스냅샷을 포함한다. 새 서버 이미지가 import_state 로 같은 Pane 상태를 복원하고,
        master_fd 번호로 상속된 PTY 를 다시 채택한다. docs/RESTART_SCENARIO.md ⓑ."""
        d = {
            "child_pid": self.child_pid,
            "master_fd": self.master_fd,
            "cols": self.cols,
            "rows": self.rows,
            "mouse_modes": sorted(self._mouse_modes),
            "mouse_sgr": self.mouse_sgr,
            "prompt_history": list(self.prompt_history)[-100:],
            "pending_prompts": list(self.pending_prompts),
            "prompt_clear_queue": list(self.prompt_clear_queue),
            # 스크롤백(연속성·하위호환) + 정확 뷰포트/커서(메인 화면 TUI 정합, B/C/D).
            "screen": self._export_screen(),          # 하위호환(구 이미지 읽기)
            "history": self._export_history(),         # 스크롤백만
            "viewport": self._export_viewport(),       # 현재 화면(트림 없음)
            "cursor": {"x": self._main.cursor.x, "y": self._main.cursor.y,
                       "hidden": bool(self._main.cursor.hidden)},
        }
        for f in self._RESUME_FIELDS:
            d[f] = getattr(self, f)
        return d

    def import_state(self, d: dict) -> None:
        """export_state 가 만든 dict 로 패널 상태를 복원한다(child_pid·master_fd 는
        생성자에서 이미 설정됐으므로 여기서 건드리지 않는다)."""
        for f in self._RESUME_FIELDS:
            if f in d:
                setattr(self, f, d[f])
        self._mouse_modes = set(d.get("mouse_modes", []))
        self.mouse_sgr = bool(d.get("mouse_sgr", False))
        self.mouse_track = (3 if 1003 in self._mouse_modes
                            else 2 if 1002 in self._mouse_modes
                            else 1 if 1000 in self._mouse_modes else 0)
        self.prompt_history = list(d.get("prompt_history", []))
        self.pending_prompts = list(d.get("pending_prompts", []))
        self.prompt_clear_queue = list(d.get("prompt_clear_queue", []))
        view = d.get("viewport")
        if view is not None:
            # 정확 복원(B/C/D): 스크롤백 + **현재 화면(트림 없음)** 을 한 번에 피드하되
            # 끝에 개행을 붙이지 않아(마지막 줄이 한 칸 스크롤돼 커서가 밀리던 D 원인)
            # 마지막 scr.lines 줄이 화면을 정확히 채우게 한다. 이어서 커서를 앱이 두고
            # 간 좌표로 절대 이동(CUP)해, execv 후 메인 화면 TUI 의 부분 repaint 가
            # 어긋나지 않게 한다. 살아 있는 앱의 다음 출력/SIGWINCH repaint 가 이어 그린다.
            hist = d.get("history") or []
            payload = "\r\n".join(list(hist) + list(view))
            self.feed(payload.encode("utf-8", "ignore"))
            cur = d.get("cursor") or {}
            try:
                cy = int(cur.get("y", 0)) + 1
                cx = int(cur.get("x", 0)) + 1
            except (TypeError, ValueError):
                cy = cx = 1
            self.feed(f"\x1b[{cy};{cx}H".encode("ascii"))
            if cur.get("hidden"):
                self.feed(b"\x1b[?25l")   # 앱이 커서를 숨긴 상태였으면 복원
        elif d.get("screen"):
            # 하위호환(구 이미지가 쓴 스냅샷 — viewport/cursor 없음): 기존 평문 경로.
            self.feed(("\r\n".join(d["screen"]) + "\r\n").encode("utf-8", "ignore"))

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
        # 빠른 경로(§4.4): ESC 가 전혀 없으면 CSI 캐리·SGR 정제·alt 전환이 있을 수
        # 없다 — 정규식 4개(_CSI_PARTIAL_RE/_PRIVATE_SGR_RE/_sanitize_sgr/_ALT_RE)를
        # 모두 건너뛰고 현재 화면에 바로 먹인다. 빌드 로그·cat 등 플레인 텍스트
        # 버스트의 흔한 핫패스로, 처리할 제어 시퀀스가 없으므로 결과는 불변이다.
        if b"\x1b" not in buf:
            self._feed_seg(buf)
            self.dirty = True
            self._feed_seq += 1
            return
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
        self._feed_seq += 1   # B1: Claude 스캔 게이팅용 — 출력 있을 때만 재스캔

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

    def _notify_winsize(self, cols: int, rows: int) -> None:
        # PTY 크기 통지는 백엔드 핸들을 통해(크로스플랫폼). 렌더 전용 패널(pty=None)은
        # 옛 fd 기반 set_winsize 로 폴백(fd=-1 이면 무해하게 실패).
        if self.pty is not None:
            try:
                self.pty.set_winsize(rows, cols)
            except OSError:
                pass
        elif isinstance(self.master_fd, int) and self.master_fd >= 0:
            # 렌더 전용 패널(pty=None)인데 유효한 master_fd 가 있으면 fd 기반 폴백.
            # fd=-1(pty 없음/죽음·테스트 스텁)이면 POSIX 에서 fcntl 이 OSError 가
            # 아니라 ValueError 를 던지므로(Python 3.13) 호출 자체를 건너뛴다.
            # Windows 는 set_winsize 가 fcntl/termios 를 지연 import 해 ImportError
            # 가능 — 폭만 못 알릴 뿐 무해하므로 삼킨다.
            try:
                set_winsize(self.master_fd, rows, cols)
            except (OSError, ImportError, ValueError):
                pass

    def resize(self, cols: int, rows: int) -> None:
        cols = max(1, cols)
        rows = max(1, rows)
        if cols == self.cols and rows == self.rows:
            return
        # 폭 축소(shrink) 시엔 ConPTY/Claude 에 좁은 폭을 **먼저** 통지해, pyte 가
        # 좁아지기 전에 SIGWINCH 로 좁은-폭 리페인트가 시작되게 한다(폭-불일치 윈도우
        # 최소화 — Windows ConPTY set_winsize 지연 대비). 넓힐 때는 pyte 를 먼저 키워도
        # wrap 이 안 생기므로 기존 순서(화면 먼저)를 유지한다. 어느 쪽이든 _BCEMixin 의
        # autowrap 가드가 남은 전환 윈도우의 cascade 를 truncate 로 흡수한다.
        shrink_w = cols < self.cols
        self.cols, self.rows = cols, rows
        if shrink_w:
            self._notify_winsize(cols, rows)
        self._main.resize(rows, cols)
        if self._alt is not None:
            self._alt.resize(rows, cols)
        if not shrink_w:
            self._notify_winsize(cols, rows)
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

    def _serialize_row(self, line, cols):
        """한 줄(line)을 [text, style] 런(run) 목록으로 직렬화한다(매치 하이라이트
        제외). render 의 빠른 경로/전체 경로가 공유한다(#8)."""
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
            key = _style_key(ch.fg, ch.bg, ch.bold, ch.italics,
                             ch.underscore, ch.reverse,
                             getattr(ch, "strikethrough", False))
            if key != cur_key:
                if cur_text:
                    segs.append(["".join(cur_text), dict(cur_key)])
                cur_text = [data]
                cur_key = key
            else:
                cur_text.append(data)
        if cur_text:
            segs.append(["".join(cur_text), dict(cur_key)])
        return segs

    def render(self, with_cursor: bool):
        """현재 뷰포트를 [rows, cursor] 로 직렬화. rows = 행마다 [text, style] 런 목록.

        #8 행 단위 재직렬화: 라이브 뷰(scroll 0)·검색 비활성·캐시 유효(같은 화면
        객체·크기)면 pyte `screen.dirty` 가 표시한 행만 다시 만들고 나머지는 직전
        캐시를 재사용한다(alt 풀리페인트에서 1줄만 바뀌어도 24행 전부 재직렬화하던
        낭비 제거). 스크롤/검색/리사이즈/alt전환/콜드캐시는 전체 경로로 폴백한다."""
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

        # 자동 줄바꿈(soft-wrap) 연속원 행을 프레임 상대 인덱스로 모은다(복사 시 한 줄
        # 잇기, serverio 가 screen 메시지 "wrap" 으로 클라에 그대로 전달). draw 가
        # 태그한 wrapped 줄 중, **현재 마지막 칸이 비어 있지 않은**(=여전히 꽉 찬) 줄만
        # 내보내 — 줄 끝이 지워지거나 당겨져 더는 wrap 이 아닌 stale 태그를 싸게
        # 무효화한다(빈 칸 default Char.data == " "; 와이드문자 stub 은 "" 라 꽉 참).
        cl = cols - 1
        self._last_wrap = [i for i, ln in enumerate(window)
                           if getattr(ln, "wrapped", False) and ln[cl].data != " "]

        cursor = None
        if with_cursor and self.scroll == 0 and not screen.cursor.hidden:
            cursor = [screen.cursor.x, screen.cursor.y]

        sdirty = getattr(screen, "dirty", None)
        live = (self.scroll == 0 and self._match_abs is None
                and not self.search_query and len(window) == lines)
        cache_key = (cols, lines, id(screen))
        # 빠른 경로: dirty 행만 재직렬화하고 나머지는 캐시 재사용.
        if (live and sdirty is not None and self._row_cache is not None
                and self._row_cache_key == cache_key):
            rows = list(self._row_cache)
            for ry in list(sdirty):
                if 0 <= ry < lines:
                    rows[ry] = self._serialize_row(window[ry], cols)
            sdirty.clear()
            self._row_cache = rows
            return rows, cursor

        # 전체 경로(스크롤/검색/alt전환/리사이즈/콜드캐시).
        rows = []
        for ry, line in enumerate(window):
            segs = self._serialize_row(line, cols)
            # 검색 매치 라인 전체 하이라이트
            if self._match_abs is not None and (start + ry) == self._match_abs:
                segs = [[t, {**st, "rv": 1}] for t, st in segs]
            rows.append(segs)
        # 뷰포트가 화면보다 짧으면(스크롤 초기) 빈 줄로 채움
        while len(rows) < lines:
            rows.append([[" " * cols, {}]])
        # 다음 빠른 경로용 캐시는 라이브 뷰일 때만 둔다(그 외엔 무효화).
        if live:
            self._row_cache = list(rows)
            self._row_cache_key = cache_key
        else:
            self._row_cache = None
            self._row_cache_key = None
        if sdirty is not None:
            sdirty.clear()
        return rows, cursor

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
        # M14 카운트다운 디바운스: 직전에 status 로 보낸 무장 자동액션 (kind, eta초).
        # flush 루프가 ETA 변동 때만 status 를 재전송하도록 비교에 쓴다(휘발성).
        self._pending_key = None
        # 라이브 PTY 팝업(display-popup): 트리에 속하지 않는 떠 있는 PTY 패널 1개.
        # None 이면 닫힌 상태. 열리면 {"pane", "title", "want_w", "want_h"} 를 담고,
        # 표시 geometry 는 매 레이아웃 계산 때 세션 크기에 맞춰 중앙 정렬로 산출한다.
        self.popup = None

    @classmethod
    def restored(cls, name: str, tabs: list, active_index: int = 0,
                 last_index: int = 0) -> "Session":
        """직렬화 복원용 생성자(__init__ 우회). 복원 경로(restore_layout·
        restore_resume_state)는 tabs 를 따로 만들어 넘기므로 __init__ 의 시그니처
        (root 1개)와 안 맞아 `__new__` 로 만든다 — 그때 __init__ 이 세팅하는 **휘발성
        속성을 빠짐없이 채워** 복원 세션이 새 세션과 동일한 속성 집합을 갖게 한다.
        과거 popup 누락이 모든 attach 를 깨뜨렸다(§10): _popup_layout 의 sess.popup
        에서 AttributeError → _send_full 실패 → 화면 일부만 그려진 채 끊김/브릭. 앞으로
        Session 에 휘발성 속성을 추가하면 **여기도 함께** 갱신할 것."""
        self = cls.__new__(cls)
        self.name = name
        self.created_at = time.time()
        self.tabs = tabs
        self.active_index = active_index
        self.last_index = last_index
        self._pending_key = None   # M14 카운트다운 디바운스(휘발성)
        self.popup = None
        return self

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
        # B2 행 단위 델타: 이 클라에 마지막으로 보낸 패널별 rows 스냅샷
        # {pane_id -> rows}. 다음 프레임에 바뀐 행만 screen-delta 로 보낸다(클라마다
        # 자기 상태 기준이라 다중 클라·신규 attach 도 정합 — seq/resync 불필요).
        self._sent_rows: dict[int, list] = {}

