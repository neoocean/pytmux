"""자작 native Screen 백엔드 — 화면 상태 의미론(로드맵 #6).

docs/internal/NATIVE_SCREEN_DESIGN_2026-07-10.md 의 명세를 따른다. 이 모듈은
``model._ScrollbackScreen``/``_BCEScreen``(= pyte.Screen + _BCEMixin) 이 하던 화면
상태 의미론을 **pyte.Screen 없이** 재현한다. vtparse.VTTokenizer 가 요구하는 것과
동일한 메서드명·시그니처·의미를 노출하므로, 파서 교체 없이 그대로 붙는다.

M1 범위(설계 §2): Char/Cursor/buffer/dirty 모델 + draw(wide/autowrap 지연)·커서 이동·
cr/lf/index/reverse_index/backspace/tab·erase_*(BCE)·select_graphic_rendition·
set_mode/reset_mode·save/restore_cursor·set/clear_tab_stop·shift_in/out·reset·bell·
set_title/set_icon_name. M2/M3 메서드(insert/delete_*·set_margins·scroll_up/down·
history/alignment_display)도 pyte 동작에 맞춰 함께 구현해 등가 오라클을 코퍼스 전체에서
green 으로 유지한다(스텁이 아니라 실동작 — 설계 §2 "스텁이라도 맞으면 포함").

**M4b(2026-07-18) pyte 완전 은퇴**: SGR 색/속성 룩업 테이블(graphics)·charset 매핑
(charsets)·모드 상수(modes)·wcwidth·CSI 디스패치 테이블을 자작 인라인 모듈
``vtconst`` 로 옮겨 pyte import 를 걷어냈다. 값은 구 pyte 상수와 **바이트 동일**하며
(vtconst 상단 참조), 골든해시(vt_render_golden.json)가 렌더 동일성을 상시 회귀한다.
"""
from __future__ import annotations

import copy
import time
import unicodedata
from collections import defaultdict, deque
from typing import NamedTuple

# 색/모드/charset 룩업 테이블·wcwidth 는 자작 인라인 모듈(vtconst)에서 가져온다
# (M4b: pyte 완전 은퇴). cs/g/mo 는 한 상수 네임스페이스(vtconst)를 세 갈래로 별칭한
# 것뿐 — charsets/graphics/modes 심볼은 vtconst 안에서 이름 충돌 없이 공존한다.
from . import vtconst
cs = g = mo = vtconst
# 모듈 전역 폭 함수 — draw()/display() 가 호출 시점에 이 이름을 조회하므로, cellwidth
# 가 모호폭(EAW='A') 인지 버전으로 `nativescreen._wcwidth` 를 교체하면 즉시 반영된다.
_wcwidth = vtconst.wcwidth

from .protocol import HISTORY

# 폭 축소 직후 autowrap 을 잠깐 끄는 전환 윈도우(model.WRAP_GUARD_SEC 와 동일값). 순환
# import 를 피하려 상수만 복제한다(값이 갈리면 안 됨 — 둘 다 0.4).
WRAP_GUARD_SEC = 0.4

_DEFAULT_MODE = frozenset((mo.DECAWM, mo.DECTCEM))


class Char(NamedTuple):
    """pyte.screens.Char 와 동형인 셀 모델(필드·기본값 동일).

    render/serverio 가 읽는 속성 접근(data/fg/bg/bold/italics/underscore/
    strikethrough/reverse/blink)이 pyte 와 완전히 일치한다. 등가 오라클은 필드
    **값** 을 비교하므로 타입 정체성은 무관하다."""
    data: str
    fg: str = "default"
    bg: str = "default"
    bold: bool = False
    italics: bool = False
    underscore: bool = False
    strikethrough: bool = False
    reverse: bool = False
    blink: bool = False


class Cursor:
    """화면 커서(pyte.screens.Cursor 와 동형: x,y,attrs,hidden)."""

    __slots__ = ("x", "y", "attrs", "hidden")

    def __init__(self, x: int, y: int, attrs: Char = Char(" ")) -> None:
        self.x = x
        self.y = y
        self.attrs = attrs
        self.hidden = False


class Margins(NamedTuple):
    """스크롤 영역 상/하 경계(0-based, 포함)."""
    top: int
    bottom: int


class Savepoint(NamedTuple):
    """DECSC(save_cursor)로 저장하는 커서 스냅샷(pyte 와 동형)."""
    cursor: Cursor
    g0_charset: str
    g1_charset: str
    charset: int
    origin: bool
    wrap: bool


class StaticDefaultDict(dict):
    """조회 시 삽입하지 않는 정적 기본값 dict(pyte.screens.StaticDefaultDict 와 동일).

    dict 서브클래스라 인스턴스 속성(예: 소프트랩 표시용 ``wrapped``)을 붙일 수 있다."""

    def __init__(self, default) -> None:
        super().__init__()
        self.default = default

    def __missing__(self, key):
        return self.default


class _NativeBase:
    """pyte.Screen + model._BCEMixin 의 화면 의미론을 pyte.Screen 없이 재현한 native
    화면 기반 클래스. 스크롤백(history)은 하위 :class:`NativeScrollbackScreen` 가 더한다.

    :class:`NativeScreen` = 대체 화면(alt, 스크롤백 없음, model._BCEScreen 대응).
    """

    def __init__(self, columns: int, lines: int) -> None:
        self.savepoints: list = []
        self.columns = columns
        self.lines = lines
        self.buffer = defaultdict(self._default_line)
        self.dirty: set = set()
        # BCE draw/linefeed 소프트랩 태깅·폭축소 autowrap 가드 상태.
        self._drawing = False
        self._wrap_guard_until = 0.0
        self.reset()
        self.mode = set(_DEFAULT_MODE)
        self.margins = None

    # ── 버퍼 팩토리 ──────────────────────────────────────────────────────────
    def _default_line(self) -> StaticDefaultDict:
        return StaticDefaultDict(self.default_char)

    @property
    def default_char(self) -> Char:
        reverse = mo.DECSCNM in self.mode
        return Char(data=" ", fg="default", bg="default", reverse=reverse)

    @property
    def display(self) -> list:
        """화면 각 행을 유니코드 문자열로. pyte.Screen.display 와 **바이트 동일** —
        wide 문자(폭 2)의 stub 셀(빈 data)을 건너뛰어 pyte 와 같은 열 정렬을 낸다.
        render() 만 태우던 M1 오라클이 안 잡았던 표면(비-render 소비자: serverpersist
        재시작 매니페스트·vtparse 테스트·nest DCS 가 `.display` 를 직접 읽는다). M4
        기본 native 전환의 선결로 pyte.Screen 읽기 표면을 완성한다."""
        def render(line):
            is_wide = False
            for x in range(self.columns):
                if is_wide:               # wide 문자의 stub 셀 skip
                    is_wide = False
                    continue
                char = line[x].data
                is_wide = _wcwidth(char[0]) == 2 if char else False
                yield char
        return ["".join(render(self.buffer[y])) for y in range(self.lines)]

    def __repr__(self) -> str:
        return "{0}({1}, {2})".format(self.__class__.__name__,
                                      self.columns, self.lines)

    # ── 리셋/리사이즈 ────────────────────────────────────────────────────────
    def reset(self) -> None:
        self.dirty.update(range(self.lines))
        self.buffer.clear()
        self.margins = None
        self.mode = set(_DEFAULT_MODE)
        self.title = ""
        self.icon_name = ""
        self.charset = 0
        self.g0_charset = cs.LAT1_MAP
        self.g1_charset = cs.VT100_MAP
        self.tabstops = set(range(8, self.columns, 8))
        self.cursor = Cursor(0, 0)
        self.cursor_position()
        self.saved_columns = None

    def resize(self, lines=None, columns=None) -> None:
        old_cols = self.columns
        lines = lines or self.lines
        columns = columns or self.columns
        if lines == self.lines and columns == self.columns:
            return
        self.dirty.update(range(lines))
        if lines < self.lines:
            self.save_cursor()
            self.cursor_position(0, 0)
            self.delete_lines(self.lines - lines)   # 위에서부터 잘라냄
            self.restore_cursor()
        if columns < self.columns:
            for line in self.buffer.values():
                for x in range(columns, self.columns):
                    line.pop(x, None)
        self.lines, self.columns = lines, columns
        self.set_margins()
        # model._BCEMixin.resize: 폭 축소 시 autowrap 가드 + 탭스톱 재계산.
        if columns < old_cols:
            self._wrap_guard_until = time.monotonic() + WRAP_GUARD_SEC
        self.tabstops = set(range(8, self.columns, 8))

    def set_margins(self, top=None, bottom=None) -> None:
        if (top is None or top == 0) and bottom is None:
            self.margins = None
            return
        margins = self.margins or Margins(0, self.lines - 1)
        if top is None:
            top = margins.top
        else:
            top = max(0, min(top - 1, self.lines - 1))
        if bottom is None:
            bottom = margins.bottom
        else:
            bottom = max(0, min(bottom - 1, self.lines - 1))
        if bottom - top >= 1:
            self.margins = Margins(top, bottom)
            self.cursor_position()

    # ── 모드 ────────────────────────────────────────────────────────────────
    def set_mode(self, *modes: int, **kwargs) -> None:
        mode_list = list(modes)
        if kwargs.get("private"):
            mode_list = [m << 5 for m in modes]
            if mo.DECSCNM in mode_list:
                self.dirty.update(range(self.lines))
        self.mode.update(mode_list)
        if mo.DECCOLM in mode_list:
            self.saved_columns = self.columns
            self.resize(columns=132)
            self.erase_in_display(2)
            self.cursor_position()
        if mo.DECOM in mode_list:
            self.cursor_position()
        if mo.DECSCNM in mode_list:
            for line in self.buffer.values():
                line.default = self.default_char
                for x in line:
                    line[x] = line[x]._replace(reverse=True)
            self.select_graphic_rendition(7)
        if mo.DECTCEM in mode_list:
            self.cursor.hidden = False

    def reset_mode(self, *modes: int, **kwargs) -> None:
        mode_list = list(modes)
        if kwargs.get("private"):
            mode_list = [m << 5 for m in modes]
            if mo.DECSCNM in mode_list:
                self.dirty.update(range(self.lines))
        self.mode.difference_update(mode_list)
        if mo.DECCOLM in mode_list:
            if self.columns == 132 and self.saved_columns is not None:
                self.resize(columns=self.saved_columns)
                self.saved_columns = None
            self.erase_in_display(2)
            self.cursor_position()
        if mo.DECOM in mode_list:
            self.cursor_position()
        if mo.DECSCNM in mode_list:
            for line in self.buffer.values():
                line.default = self.default_char
                for x in line:
                    line[x] = line[x]._replace(reverse=False)
            self.select_graphic_rendition(27)
        if mo.DECTCEM in mode_list:
            self.cursor.hidden = True

    # ── charset ─────────────────────────────────────────────────────────────
    def define_charset(self, code: str, mode: str) -> None:
        if code in cs.MAPS:
            if mode == "(":
                self.g0_charset = cs.MAPS[code]
            elif mode == ")":
                self.g1_charset = cs.MAPS[code]

    def shift_in(self) -> None:
        self.charset = 0

    def shift_out(self) -> None:
        self.charset = 1

    # ── 그리기 ──────────────────────────────────────────────────────────────
    def draw(self, data: str) -> None:
        # model._BCEMixin.draw: 폭 축소 전환 윈도우 동안 autowrap 을 꺼 cascade 대신
        # truncate. _drawing 은 linefeed 가 soft-wrap 을 구분하게 하는 플래그.
        self._drawing = True
        guard = (mo.DECAWM in self.mode
                 and time.monotonic() < self._wrap_guard_until)
        if guard:
            self.mode.discard(mo.DECAWM)
        try:
            self._draw_impl(data)
        finally:
            if guard:
                self.mode.add(mo.DECAWM)
            self._drawing = False

    def _draw_impl(self, data: str) -> None:
        data = data.translate(
            self.g1_charset if self.charset else self.g0_charset)
        for char in data:
            char_width = _wcwidth(char)
            if self.cursor.x == self.columns:
                if mo.DECAWM in self.mode:
                    self.dirty.add(self.cursor.y)
                    self.carriage_return()
                    self.linefeed()
                elif char_width > 0:
                    self.cursor.x -= char_width
            if mo.IRM in self.mode and char_width > 0:
                self.insert_characters(char_width)
            line = self.buffer[self.cursor.y]
            if char_width == 1:
                line[self.cursor.x] = self.cursor.attrs._replace(data=char)
            elif char_width == 2:
                line[self.cursor.x] = self.cursor.attrs._replace(data=char)
                if self.cursor.x + 1 < self.columns:
                    line[self.cursor.x + 1] = \
                        self.cursor.attrs._replace(data="")
            elif char_width == 0 and unicodedata.combining(char):
                if self.cursor.x:
                    last = line[self.cursor.x - 1]
                    normalized = unicodedata.normalize(
                        "NFC", last.data + char)
                    line[self.cursor.x - 1] = last._replace(data=normalized)
                elif self.cursor.y:
                    last = self.buffer[self.cursor.y - 1][self.columns - 1]
                    normalized = unicodedata.normalize(
                        "NFC", last.data + char)
                    self.buffer[self.cursor.y - 1][self.columns - 1] = \
                        last._replace(data=normalized)
            else:
                break
            if char_width > 0:
                self.cursor.x = min(self.cursor.x + char_width, self.columns)
        self.dirty.add(self.cursor.y)

    # ── 기본 커서/개행 ──────────────────────────────────────────────────────
    def set_title(self, param: str) -> None:
        self.title = param

    def set_icon_name(self, param: str) -> None:
        self.icon_name = param

    def carriage_return(self) -> None:
        self.cursor.x = 0

    def index(self) -> None:
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom:
            self.dirty.update(range(self.lines))
            for y in range(top, bottom):
                self.buffer[y] = self.buffer[y + 1]
            self.buffer.pop(bottom, None)
        else:
            self.cursor_down()

    def reverse_index(self) -> None:
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == top:
            self.dirty.update(range(self.lines))
            for y in range(bottom, top, -1):
                self.buffer[y] = self.buffer[y - 1]
            self.buffer.pop(top, None)
        else:
            self.cursor_up()

    def linefeed(self) -> None:
        # model._BCEMixin.linefeed: draw 중 개행이면 떠나는 줄을 soft-wrap 연속원으로
        # 태그(줄 객체는 스크롤백으로 밀려도 같은 참조라 태그가 따라간다).
        if self._drawing:
            try:
                self.buffer[self.cursor.y].wrapped = True
            except Exception:
                pass
        self.index()
        if mo.LNM in self.mode:
            self.carriage_return()

    def tab(self) -> None:
        for stop in sorted(self.tabstops):
            if self.cursor.x < stop:
                column = stop
                break
        else:
            column = self.columns - 1
        self.cursor.x = column

    def backspace(self) -> None:
        self.cursor_back()

    # ── 저장/복원 ────────────────────────────────────────────────────────────
    def save_cursor(self) -> None:
        self.savepoints.append(Savepoint(
            copy.copy(self.cursor), self.g0_charset, self.g1_charset,
            self.charset, mo.DECOM in self.mode, mo.DECAWM in self.mode))

    def restore_cursor(self) -> None:
        if self.savepoints:
            savepoint = self.savepoints.pop()
            self.g0_charset = savepoint.g0_charset
            self.g1_charset = savepoint.g1_charset
            self.charset = savepoint.charset
            if savepoint.origin:
                self.set_mode(mo.DECOM)
            if savepoint.wrap:
                self.set_mode(mo.DECAWM)
            self.cursor = savepoint.cursor
            self.ensure_hbounds()
            self.ensure_vbounds(use_margins=True)
        else:
            self.reset_mode(mo.DECOM)
            self.cursor_position()

    # ── 삽입/삭제(행) ───────────────────────────────────────────────────────
    def insert_lines(self, count=None) -> None:
        count = count or 1
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if top <= self.cursor.y <= bottom:
            self.dirty.update(range(self.cursor.y, self.lines))
            for y in range(bottom, self.cursor.y - 1, -1):
                if y + count <= bottom and y in self.buffer:
                    self.buffer[y + count] = self.buffer[y]
                self.buffer.pop(y, None)
            self.carriage_return()

    def delete_lines(self, count=None) -> None:
        count = count or 1
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if top <= self.cursor.y <= bottom:
            self.dirty.update(range(self.cursor.y, self.lines))
            for y in range(self.cursor.y, bottom + 1):
                if y + count <= bottom:
                    if y + count in self.buffer:
                        self.buffer[y] = self.buffer.pop(y + count)
                else:
                    self.buffer.pop(y, None)
            self.carriage_return()

    # ── 삽입/삭제/소거(문자) ────────────────────────────────────────────────
    def insert_characters(self, count=None) -> None:
        self.dirty.add(self.cursor.y)
        count = count or 1
        line = self.buffer[self.cursor.y]
        for x in range(self.columns, self.cursor.x - 1, -1):
            if x + count <= self.columns:
                line[x + count] = line[x]
            line.pop(x, None)

    def delete_characters(self, count=None) -> None:
        self.dirty.add(self.cursor.y)
        count = count or 1
        line = self.buffer[self.cursor.y]
        for x in range(self.cursor.x, self.columns):
            if x + count <= self.columns:
                line[x] = line.pop(x + count, self.default_char)
            else:
                line.pop(x, None)

    # BCE: 소거 셀 속성은 배경/전경/반전만 보존(글자 장식은 버림) — model._BCEMixin.
    def _erase_char(self) -> Char:
        return self.cursor.attrs._replace(
            data=" ", bold=False, italics=False, underscore=False,
            strikethrough=False, blink=False)

    def erase_characters(self, count=None) -> None:
        self.dirty.add(self.cursor.y)
        count = count or 1
        blank = self._erase_char()
        line = self.buffer[self.cursor.y]
        for x in range(self.cursor.x,
                       min(self.cursor.x + count, self.columns)):
            line[x] = blank

    def erase_in_line(self, how=0, private=False) -> None:
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

    def erase_in_display(self, how=0, *args, **kwargs) -> None:
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

    # ── 탭스톱 ──────────────────────────────────────────────────────────────
    def set_tab_stop(self) -> None:
        self.tabstops.add(self.cursor.x)

    def clear_tab_stop(self, how=0) -> None:
        if how == 0:
            self.tabstops.discard(self.cursor.x)
        elif how == 3:
            self.tabstops = set()

    # ── 커서 경계/이동 ──────────────────────────────────────────────────────
    def ensure_hbounds(self) -> None:
        self.cursor.x = min(max(0, self.cursor.x), self.columns - 1)

    def ensure_vbounds(self, use_margins=None) -> None:
        if (use_margins or mo.DECOM in self.mode) and self.margins is not None:
            top, bottom = self.margins
        else:
            top, bottom = 0, self.lines - 1
        self.cursor.y = min(max(top, self.cursor.y), bottom)

    def cursor_up(self, count=None) -> None:
        top, _bottom = self.margins or Margins(0, self.lines - 1)
        self.cursor.y = max(self.cursor.y - (count or 1), top)

    def cursor_up1(self, count=None) -> None:
        self.cursor_up(count)
        self.carriage_return()

    def cursor_down(self, count=None) -> None:
        _top, bottom = self.margins or Margins(0, self.lines - 1)
        self.cursor.y = min(self.cursor.y + (count or 1), bottom)

    def cursor_down1(self, count=None) -> None:
        self.cursor_down(count)
        self.carriage_return()

    def cursor_back(self, count=None) -> None:
        if self.cursor.x == self.columns:
            self.cursor.x -= 1
        self.cursor.x -= count or 1
        self.ensure_hbounds()

    def cursor_forward(self, count=None) -> None:
        self.cursor.x += count or 1
        self.ensure_hbounds()

    def cursor_position(self, line=None, column=None) -> None:
        column = (column or 1) - 1
        line = (line or 1) - 1
        if self.margins is not None and mo.DECOM in self.mode:
            line += self.margins.top
            if not self.margins.top <= line <= self.margins.bottom:
                return
        self.cursor.x = column
        self.cursor.y = line
        self.ensure_hbounds()
        self.ensure_vbounds()

    def cursor_to_column(self, column=None) -> None:
        self.cursor.x = (column or 1) - 1
        self.ensure_hbounds()

    def cursor_to_line(self, line=None) -> None:
        self.cursor.y = (line or 1) - 1
        if mo.DECOM in self.mode:
            assert self.margins is not None
            self.cursor.y += self.margins.top
        self.ensure_vbounds()

    # ── SU/SD(스크롤 영역) — model._BCEMixin ─────────────────────────────────
    def scroll_up(self, count=None) -> None:
        count = count or 1
        top, bottom = self.margins or Margins(0, self.lines - 1)
        self.dirty.update(range(self.lines))
        for _ in range(min(count, bottom - top + 1)):
            self._scroll_region_up(top, bottom)

    def _scroll_region_up(self, top, bottom) -> None:
        for y in range(top, bottom):
            self.buffer[y] = self.buffer[y + 1]
        self.buffer.pop(bottom, None)

    def scroll_down(self, count=None) -> None:
        count = count or 1
        top, bottom = self.margins or Margins(0, self.lines - 1)
        self.dirty.update(range(self.lines))
        for _ in range(min(count, bottom - top + 1)):
            for y in range(bottom, top, -1):
                self.buffer[y] = self.buffer[y - 1]
            self.buffer.pop(top, None)

    # ── 기타 ────────────────────────────────────────────────────────────────
    def bell(self, *args) -> None:
        pass

    def alignment_display(self) -> None:
        self.dirty.update(range(self.lines))
        for y in range(self.lines):
            for x in range(self.columns):
                self.buffer[y][x] = self.buffer[y][x]._replace(data="E")

    def select_graphic_rendition(self, *attrs: int) -> None:
        replace: dict = {}
        if not attrs or attrs == (0,):
            self.cursor.attrs = self.default_char
            return
        attrs_list = list(reversed(attrs))
        while attrs_list:
            attr = attrs_list.pop()
            if attr == 0:
                replace.update(self.default_char._asdict())
            elif attr in g.FG_ANSI:
                replace["fg"] = g.FG_ANSI[attr]
            elif attr in g.BG:
                replace["bg"] = g.BG_ANSI[attr]
            elif attr in g.TEXT:
                attr_str = g.TEXT[attr]
                replace[attr_str[1:]] = attr_str.startswith("+")
            elif attr in g.FG_AIXTERM:
                replace.update(fg=g.FG_AIXTERM[attr])
            elif attr in g.BG_AIXTERM:
                replace.update(bg=g.BG_AIXTERM[attr])
            elif attr in (g.FG_256, g.BG_256):
                key = "fg" if attr == g.FG_256 else "bg"
                try:
                    n = attrs_list.pop()
                    if n == 5:
                        m = attrs_list.pop()
                        replace[key] = g.FG_BG_256[m]
                    elif n == 2:
                        replace[key] = "{0:02x}{1:02x}{2:02x}".format(
                            attrs_list.pop(), attrs_list.pop(),
                            attrs_list.pop())
                except IndexError:
                    pass
        self.cursor.attrs = self.cursor.attrs._replace(**replace)

    def report_device_attributes(self, mode=0, **kwargs) -> None:
        if mode == 0 and not kwargs.get("private"):
            self.write_process_input("\x1b[?6c")

    def report_device_status(self, mode) -> None:
        if mode == 5:
            self.write_process_input("\x1b[0n")
        elif mode == 6:
            x = self.cursor.x + 1
            y = self.cursor.y + 1
            if mo.DECOM in self.mode:
                assert self.margins is not None
                y -= self.margins.top
            self.write_process_input("\x1b[{0};{1}R".format(y, x))

    def write_process_input(self, data: str) -> None:
        pass

    def debug(self, *args, **kwargs) -> None:
        pass


class NativeScreen(_NativeBase):
    """대체 화면(alt-screen, 스크롤백 없음) — model._BCEScreen 의 native 대응."""


class _History:
    """model._History 와 동일한 경량 스크롤백 홀더(top/bottom deque)."""

    __slots__ = ("top", "bottom")

    def __init__(self, maxlen: int) -> None:
        self.top = deque(maxlen=maxlen)
        self.bottom = deque(maxlen=maxlen)


class NativeScrollbackScreen(_NativeBase):
    """메인 화면 + 위로 밀려난 줄만 모으는 스크롤백 — model._ScrollbackScreen 의 native
    대응. 수집 조건/대상 줄은 pyte.HistoryScreen(및 model._ScrollbackScreen)과 동일해
    render/capture_pane/clear_history 가 읽는 .history.top/.bottom 인터페이스도 같다."""

    def __init__(self, columns: int, lines: int,
                 history: int = HISTORY, ratio: float = 0.5) -> None:
        # super().__init__ 가 reset() 을 부르므로 history 를 먼저 만든다.
        self.history = _History(history)
        super().__init__(columns, lines)

    def index(self) -> None:
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom:
            self.history.top.append(self.buffer[top])
        super().index()

    def _scroll_region_up(self, top, bottom) -> None:
        if top == 0:
            self.history.top.append(self.buffer[0])
        super()._scroll_region_up(top, bottom)

    def reverse_index(self) -> None:
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == top:
            self.history.bottom.append(self.buffer[bottom])
        super().reverse_index()

    def reset(self) -> None:
        super().reset()
        self.history.top.clear()
        self.history.bottom.clear()
