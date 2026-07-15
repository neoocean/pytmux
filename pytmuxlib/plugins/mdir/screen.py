"""mdir — Mdir III(엠디르) 풍 파일 관리자 모달 화면.

Mdir III 3.10(1998, 최정한) 실물 재현(archive.org 원본 배포판·스크린샷 기준):
  - **검정 바탕 1-패널 다열 파일 리스트**(노턴류 2-pane 아님), 열 사이 `│` 구분.
  - 리스트 맨 앞 `..`(`[ Up-Dir ]`), 디렉토리(`[ SubDir ]`·붉은색), 파일, 맨 끝에
    **드라이브 항목**(`[-C-] …`, Windows) — 드라이브도 커서로 골라 Enter 전환.
  - 확장자별 색: EXE 밝은초록·COM 하늘·BAT 노랑·압축 자홍, 숨김 보라, 실행비트 초록.
  - 상단: 키 안내줄(청색 바) + `Path …` / `Volume(빈 공간)` 줄. 하단: 집계줄
    (`N File M Dir … byte free`) + 청색 정보줄(커서파일 크기│날짜│시간│속성 +
    현재 시각 + 핵심키 안내 — 원조의 `F10=MCD│F11=QCD│F12=Menu` 자리).
  - 커서 = 초록 배경 선택막대. 문자키 = 빨리찾기(speed search), `.` 상위, `\\` 루트.

렌더링은 ncd 와 같은 `render_line` 기반 단일 위젯(_MdirView) — 커서 이동 시 바뀐
행만 refresh 해 ssh 원격에서도 빠르다. 목록 데이터는 서버(request_mdir_list)가
권위(페더레이션이면 원격 머신 fs). 표시 필터(숨김/정렬)는 클라 로컬이라 왕복 없다.

스크롤 모델은 도스 원조대로 **페이지 단위**(부드러운 스크롤 없음): 항목 인덱스가
페이지(행수×열수)를 넘어가면 다음 페이지로 넘긴다. 열 채움은 세로 우선(column-major).
"""
from __future__ import annotations

import os
import time

from rich.cells import set_cell_size
from rich.segment import Segment
from rich.style import Style
from textual.screen import ModalScreen
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

from pytmuxlib import i18n

# ---- Mdir III 기본 배색(BLACK.COL 실측) ----
_TXT = Style(color="#aaaaaa", bgcolor="#000000")            # 일반 파일(회백)
_DIR = Style(color="#ff5555", bgcolor="#000000", bold=True)  # 디렉토리(붉은색)
_UP = Style(color="#ffffff", bgcolor="#000000", bold=True)   # [ Up-Dir ]
_HID = Style(color="#aa00aa", bgcolor="#000000")             # 숨은파일(보라)
_DRIVE = Style(color="#ffaa00", bgcolor="#000000", bold=True)  # [-C-] 드라이브 항목
_CUR = Style(color="#000000", bgcolor="#00aa00", bold=True)  # 선택막대(초록, 실물)
_CUR_BLUR = Style(color="#000000", bgcolor="#007700")
_BAR = Style(color="#ffffff", bgcolor="#0000aa")             # 상/하단 청색 바
_BAR_HI = Style(color="#ffff55", bgcolor="#0000aa", bold=True)
_SEP = Style(color="#555555", bgcolor="#000000")             # 구분선·열 구분 │
_PATH = Style(color="#ffff55", bgcolor="#000000", bold=True)  # Path 줄 강조
_ERR = Style(color="#ff5555", bgcolor="#000000", bold=True)

# 확장자별 색(원조: EXE=밝은초록 COM=밝은하늘 BAT/BTM=노랑 압축=밝은자홍).
_EXT_COLORS = {
    "exe": "#55ff55", "com": "#55ffff",
    "bat": "#ffff55", "btm": "#ffff55", "cmd": "#ffff55", "sh": "#ffff55",
}
_ARCHIVE_EXTS = {"zip", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz",
                 "zst", "7z", "rar", "lzh", "arj", "jar"}
_ARC_COLOR = "#ff55ff"

# 한글 원문을 i18n 키로 쓰고 en 번역을 등록(코드베이스 관례). 렌더 시점 i18n.t().
_STRIP = ("Enter 진입 · `.` 상위 · `\\` 루트 · 타이핑=빨리찾기 · "
          "F4=패널 cd · ⇧Enter/^O 새 패널 · Esc")
_FIND = "찾기"
_BARKEYS = "F4=cd│⇧↵=분할│Esc"
_TRUNC = "(항목 일부만 표시)"
i18n.register({
    "ko": {_STRIP: _STRIP, _FIND: _FIND, _BARKEYS: _BARKEYS, _TRUNC: _TRUNC},
    "en": {_STRIP: ("Enter open · `.` up · `\\` root · type=speed find · "
                    "F4=pane cd · ⇧Enter/^O new pane · Esc"),
           _FIND: "Find", _BARKEYS: "F4=cd│⇧↵=split│Esc",
           _TRUNC: "(list truncated)"},
})

# 빨리찾기에 안 쓰는 예약 문자(원조 mdir 의 명령 키) — `.`=상위, `\`=루트,
# `+`/`-`/`*`/`/`=선택 계열(후속 단계), 공백=태그.
_RESERVED_CHARS = set(". \\+-*/")


def _fmt_size(s: int) -> str:
    """크기 표시 — 원조처럼 콤마 구분, 10^9 이상은 칸에 안 들어가 컴팩트(G)."""
    if s < 1_000_000_000:
        return f"{s:,}"
    return f"{s / 2**30:.1f}G"


def _fmt_space(v: int) -> str:
    if v >= 2**30:
        return f"{v / 2**30:.1f}G"
    if v >= 2**20:
        return f"{v / 2**20:.0f}M"
    return f"{v:,}"


class _MdirView(Widget):
    """파일 리스트+상하단 바를 한 줄 단위로 직접 그리는 뷰(커서·페이지 자체 관리)."""
    can_focus = True

    def __init__(self, msg: dict):
        super().__init__(id="mdirview")
        self._path = ""
        self._nt = False
        self._entries: list[dict] = []
        self._drives: list[str] = []
        self._free = self._total = 0
        self._err = None
        self._over = False
        self._items: list[dict] = []
        self._idx = 0
        self._find = ""
        self._show_hidden = False
        self._pending_sel: str | None = None
        self._apply(msg)

    # ---- 서버 응답 적용 ----
    def _apply(self, msg: dict):
        self._path = msg.get("path") or self._path
        self._nt = bool(msg.get("nt"))
        self._entries = list(msg.get("entries") or [])
        self._drives = list(msg.get("drives") or [])
        self._free = int(msg.get("free") or 0)
        self._total = int(msg.get("total") or 0)
        self._err = msg.get("err")
        self._over = bool(msg.get("over"))
        self._find = ""
        self._rebuild(keep_name=self._pending_sel)
        self._pending_sel = None

    def apply_list(self, msg: dict):
        self._apply(msg)
        self.refresh()

    # ---- 목록 구성(표시 모델) ----
    def _rebuild(self, keep_name: str | None = None):
        """entries → 표시 항목 리스트. 순서는 원조대로 `..` → 디렉토리 → 파일 →
        드라이브. 숨김은 토글에 따라 제외(서버는 항상 전부 보냄 — 왕복 없는 토글)."""
        ents = [e for e in self._entries if self._show_hidden or not e["h"]]
        dirs = sorted((e for e in ents if e["d"]), key=lambda e: e["n"].lower())
        files = sorted((e for e in ents if not e["d"]), key=lambda e: e["n"].lower())
        items: list[dict] = [{"k": "up"}]
        items += [{"k": "dir", "e": e} for e in dirs]
        items += [{"k": "file", "e": e} for e in files]
        items += [{"k": "drive", "p": p} for p in self._drives]
        self._items = items
        self._idx = 0
        if keep_name:
            for i, it in enumerate(items):
                if it.get("e", {}).get("n") == keep_name:
                    self._idx = i
                    break

    def _item_name(self, it: dict) -> str:
        if it["k"] == "up":
            return ".."
        if it["k"] == "drive":
            return it["p"]
        return it["e"]["n"]

    # ---- 격자 기하(페이지 단위·세로 우선) ----
    def _body_rows(self) -> int:
        return max(1, self.size.height - 6)

    def _cols(self) -> int:
        # 자동 열수: 원조 Alt-0(자동) 동작. 열 하나가 최소 ~34칸은 되게.
        return max(1, min(6, self.size.width // 34))

    def _colw(self) -> int:
        cols = self._cols()
        return max(10, (self.size.width - (cols - 1)) // cols)

    def _page_geometry(self):
        rows, cols = self._body_rows(), self._cols()
        per = rows * cols
        page = self._idx // per if per else 0
        return rows, cols, per, page

    # ---- 렌더 ----
    def render_line(self, y: int) -> Strip:
        w = self.size.width
        h = self.size.height
        if h < 7:                     # 극단 축소 방어
            return Strip.blank(w, _TXT)
        if y == 0:
            return self._line_bar(f" {i18n.t(_STRIP)}", w)
        if y == 1:
            return self._line_path(w)
        if y == 2 or y == h - 3:
            return Strip([Segment("─" * w, _SEP)])
        if y == h - 2:
            return self._line_counts(w)
        if y == h - 1:
            return self._line_info(w)
        return self._line_body(y - 3, w)

    def _line_bar(self, text: str, w: int) -> Strip:
        return Strip([Segment(set_cell_size(text, w), _BAR)])

    def _line_path(self, w: int) -> Strip:
        left = f" Path {self._path}"
        if self._find:
            right = f"{i18n.t(_FIND)}: {self._find} "
        else:
            right = (f"Free {_fmt_space(self._free)}"
                     f"/{_fmt_space(self._total)} " if self._total else "")
        pad = max(1, w - len(right) - _cells(left))
        return Strip([Segment(set_cell_size(left, _cells(left) + pad), _PATH),
                      Segment(right, _TXT)]).adjust_cell_length(w, _TXT)

    def _line_counts(self, w: int) -> Strip:
        if self._err:
            return Strip([Segment(set_cell_size(f" {self._err}", w), _ERR)])
        nf = nd = 0
        total = 0
        for it in self._items:
            if it["k"] == "file":
                nf += 1
                total += it["e"]["s"]
            elif it["k"] == "dir":
                nd += 1
        pct = round(self._free * 100 / self._total) if self._total else 0
        text = (f" {nf} File  {nd} Dir  {total:,} Byte  "
                f"{self._free:,}({pct}%)byte free")
        text += "  N"                                    # 정렬 표시(이름순)
        if self._show_hidden:
            text += " H"
        if self._over:
            text += f"  {i18n.t(_TRUNC)}"
        return Strip([Segment(set_cell_size(text, w), _TXT)])

    def _line_info(self, w: int) -> Strip:
        """최하단 청색 정보줄: 커서 항목 `크기│날짜│시간│속성` + 시계 + 핵심키."""
        it = self._items[self._idx] if 0 <= self._idx < len(self._items) else None
        left = " "
        if it and it["k"] in ("file", "dir"):
            e = it["e"]
            stamp = time.strftime("%y-%m-%d│%H:%M", time.localtime(e["m"]))
            size = "[SubDir]" if it["k"] == "dir" else _fmt_size(e["s"])
            attr = ("r" if e.get("ro") else "-") + ("h" if e.get("h") else "-")
            left = f" {size}│{stamp}│{attr}"
        elif it and it["k"] == "drive":
            left = f" {it['p']}"
        clock = time.strftime("%y-%m-%d %a %H:%M:%S")
        right = f"{i18n.t(_BARKEYS)} "
        mid_w = max(0, w - _cells(left) - _cells(right))
        mid = clock.center(mid_w)[:mid_w] if mid_w else ""
        return Strip([Segment(left, _BAR),
                      Segment(mid, _BAR_HI),
                      Segment(right, _BAR)]).adjust_cell_length(w, _BAR)

    def _line_body(self, row: int, w: int) -> Strip:
        rows, cols, per, page = self._page_geometry()
        if row >= rows:
            return Strip.blank(w, _TXT)
        colw = self._colw()
        segs: list[Segment] = []
        for c in range(cols):
            if c:
                segs.append(Segment("│", _SEP))
            i = page * per + c * rows + row
            if 0 <= i < len(self._items):
                segs.append(self._item_segment(self._items[i], colw,
                                               cursor=(i == self._idx)))
            else:
                segs.append(Segment(" " * colw, _TXT))
        return Strip(segs).adjust_cell_length(w, _TXT)

    def _item_style(self, it: dict) -> Style:
        k = it["k"]
        if k == "up":
            return _UP
        if k == "drive":
            return _DRIVE
        if k == "dir":
            return _DIR
        e = it["e"]
        if e.get("h"):
            return _HID
        ext = e["n"].rsplit(".", 1)[-1].lower() if "." in e["n"][1:] else ""
        if ext in _ARCHIVE_EXTS:
            return Style(color=_ARC_COLOR, bgcolor="#000000")
        if ext in _EXT_COLORS:
            return Style(color=_EXT_COLORS[ext], bgcolor="#000000")
        if e.get("x"):
            return Style(color="#55ff55", bgcolor="#000000")
        return _TXT

    def _item_segment(self, it: dict, colw: int, cursor: bool) -> Segment:
        k = it["k"]
        with_dt = colw >= 56
        size_w = 11
        name_w = colw - size_w - 1 - (15 if with_dt else 0)
        if k == "up":
            text = set_cell_size("..", max(1, name_w)) + " " + \
                f"{'[ Up-Dir ]':>{size_w}}"
        elif k == "drive":
            text = set_cell_size(f"[-{it['p'][:1]}-] {it['p']}", colw)
        else:
            e = it["e"]
            size = "[ SubDir ]" if k == "dir" else _fmt_size(e["s"])
            text = set_cell_size(e["n"], max(1, name_w)) + " " + \
                f"{size:>{size_w}}"
            if with_dt:
                text += time.strftime(" %y-%m-%d %H:%M", time.localtime(e["m"]))
        text = set_cell_size(text, colw)
        if cursor:
            style = _CUR if self.has_focus else _CUR_BLUR
        else:
            style = self._item_style(it)
        return Segment(text, style)

    # ---- 커서 이동(페이지 단위) ----
    def _move(self, new: int):
        n = len(self._items)
        if not n:
            return
        new = max(0, min(n - 1, new))
        if new == self._idx:
            return
        rows, cols, per, page = self._page_geometry()
        old = self._idx
        self._idx = new
        w = self.size.width
        if per and new // per != page:
            self.refresh()                       # 페이지 넘어감 → 전체
        else:                                    # 같은 페이지 → 바뀐 두 행만(ssh 최소)
            self.refresh(Region(0, 3 + (old % per) % rows, w, 1))
            self.refresh(Region(0, 3 + (new % per) % rows, w, 1))
        # 하단 정보줄(커서 파일 크기/날짜/속성)도 커서 따라 갱신.
        self.refresh(Region(0, self.size.height - 1, w, 1))

    def refresh_clock(self):
        if self.size.height > 0:
            self.refresh(Region(0, self.size.height - 1, self.size.width, 1))

    # ---- 탐색 ----
    @staticmethod
    def _parent_of(p: str) -> str | None:
        """경로 문자열만으로 부모를 구한다(클라 OS 와 무관 — 페더레이션에서 서버
        경로가 클라와 다른 방언일 수 있어 os.path 대신 구분자 직접 처리)."""
        q = p.rstrip("/\\")
        i = max(q.rfind("/"), q.rfind("\\"))
        if i < 0:
            return None                     # 루트('/')·드라이브('C:\\') — 더 위 없음
        parent = q[:i + 1]                  # 구분자 포함('/a'→'/', 'C:\\x'→'C:\\')
        if len(parent) > 1 and not (len(parent) == 3 and parent[1] == ":"):
            parent = parent.rstrip("/\\") or parent
        return parent

    def _join(self, name: str) -> str:
        p = self._path
        if p.endswith(("/", "\\")):
            return p + name
        return p + ("\\" if self._nt else "/") + name

    def _navigate(self, path: str, sel_name: str | None = None):
        self._pending_sel = sel_name
        self.app.request_mdir_list(path)

    def _go_parent(self):
        parent = self._parent_of(self._path)
        if parent:
            child = self._path.rstrip("/\\")
            child = child[max(child.rfind("/"), child.rfind("\\")) + 1:]
            self._navigate(parent, sel_name=child or None)

    def _cur_dir_target(self) -> str:
        """⇧Enter(새 패널)의 대상 — 커서가 디렉토리/드라이브면 그것, 아니면 현재."""
        it = self._items[self._idx] if 0 <= self._idx < len(self._items) else None
        if it:
            if it["k"] == "dir":
                return self._join(it["e"]["n"])
            if it["k"] == "drive":
                return it["p"]
            if it["k"] == "up":
                return self._parent_of(self._path) or self._path
        return self._path

    # ---- 빨리찾기(speed search) ----
    def _set_find(self, s: str):
        self._find = s
        self.refresh(Region(0, 1, self.size.width, 1))   # Path/Volume 줄 갱신

    def _jump(self):
        if not self._find or not self._items:
            return
        q = self._find.lower()
        n = len(self._items)
        for match_prefix in (True, False):
            for off in range(n):
                i = (self._idx + off) % n
                if self._items[i]["k"] in ("up", "drive"):
                    continue
                name = self._item_name(self._items[i]).lower()
                if (name.startswith(q) if match_prefix else q in name):
                    self._move(i)
                    return

    # ---- 키 ----
    async def on_key(self, event: events.Key):
        k = event.key
        ch = event.character
        it = self._items[self._idx] if 0 <= self._idx < len(self._items) else None
        rows, cols, per, _page = self._page_geometry()
        if k == "escape":
            event.stop()
            if self._find:
                self._set_find("")
            else:
                self.screen.dismiss(None)
        elif k == "enter":
            event.stop()
            self._set_find("")
            if it is None:
                return
            if it["k"] == "up":
                self._go_parent()
            elif it["k"] == "dir":
                self._navigate(self._join(it["e"]["n"]))
            elif it["k"] == "drive":
                self._navigate(it["p"])
            # 파일 Enter 는 후속 단계(뷰어·압축 내부 보기)에서.
        elif k in ("f4", "ctrl+enter"):        # 패널 cd 후 닫기(원조: 종료 시 잔류)
            event.stop()
            self.screen.dismiss(("cd", self._path))
        elif k in ("shift+enter", "ctrl+o"):   # 새 패널 분할(ncd 동형)
            event.stop()
            self.screen.dismiss(("newpane", self._cur_dir_target()))
        elif k == "up":
            event.stop(); self._set_find(""); self._move(self._idx - 1)
        elif k == "down":
            event.stop(); self._set_find(""); self._move(self._idx + 1)
        elif k == "left":
            event.stop(); self._set_find(""); self._move(self._idx - rows)
        elif k == "right":
            event.stop(); self._set_find(""); self._move(self._idx + rows)
        elif k == "pageup":
            event.stop(); self._set_find(""); self._move(self._idx - per)
        elif k == "pagedown":
            event.stop(); self._set_find(""); self._move(self._idx + per)
        elif k == "home":
            event.stop(); self._set_find(""); self._move(0)
        elif k == "end":
            event.stop(); self._set_find(""); self._move(len(self._items) - 1)
        elif k == "backspace":
            event.stop()
            if self._find:
                self._set_find(self._find[:-1])
                self._jump()
            else:
                self._go_parent()              # 원조 감각: BS=상위(빨리찾기 없을 때)
        elif ch == ".":
            event.stop(); self._set_find(""); self._go_parent()
        elif ch == "\\":
            event.stop()
            self._set_find("")
            root = (self._path[:3] if self._nt and self._path[1:2] == ":"
                    else "/")
            self._navigate(root)
        elif ch and ch.isprintable() and len(ch) == 1 \
                and ch not in _RESERVED_CHARS:
            event.stop()                       # 빨리찾기 글자 입력
            self._set_find(self._find + ch)
            self._jump()

    # ---- 마우스 ----
    def on_click(self, event: events.Click):
        rows, cols, per, page = self._page_geometry()
        row = event.y - 3
        if not (0 <= row < rows):
            return
        col = min(cols - 1, event.x // (self._colw() + 1))
        i = page * per + col * rows + row
        if 0 <= i < len(self._items):
            event.stop()
            self._move(i)

    def on_mouse_scroll_down(self, event):
        event.stop(); self._move(self._idx + 3)

    def on_mouse_scroll_up(self, event):
        event.stop(); self._move(self._idx - 3)

    def on_mount(self):
        self.focus()

    def on_resize(self, event):
        self._idx = max(0, min(self._idx, len(self._items) - 1))
        self.refresh()


def _cells(s: str) -> int:
    from rich.cells import cell_len
    return cell_len(s)


class MdirScreen(ModalScreen):
    """mdir 팝업 껍데기 — 검정 패널 + 이중 테두리. 실제 그리기는 _MdirView."""
    CSS = """
    MdirScreen { align: center middle; }
    #mdirbox { width: 94%; height: 92%; padding: 0;
               background: #000000; color: #aaaaaa;
               border: double #555555;
               border-title-color: #ffffff; border-title-background: #000000; }
    #mdirview { height: 1fr; width: 1fr; }
    """

    def __init__(self, msg: dict):
        super().__init__()
        self._view = _MdirView(msg)

    def compose(self) -> ComposeResult:
        with Vertical(id="mdirbox"):
            yield self._view

    def on_mount(self):
        self.query_one("#mdirbox", Vertical).border_title = "Mdir"
        self.set_interval(1.0, self._view.refresh_clock)

    def apply_list(self, msg: dict):
        self._view.apply_list(msg)
