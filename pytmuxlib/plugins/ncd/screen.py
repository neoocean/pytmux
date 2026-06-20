"""ncd — Norton Change Directory 풍 디렉토리 트리 모달(코드네임 nc, docs/internal/NC_SCENARIO.md).

NCD(Norton Change Directory)의 재현: 루트(`/`)부터의 **디렉토리 전용** 트리를 화면
가득 띄우되 **현재 패널 cwd 까지 펼쳐 그 위에 커서**를 둔다. ↑↓ 이동 · PgUp/PgDn·
Home/End 점프 · → 펼치기(지연 로드) · ← 접기/부모 · **디렉토리명 타이핑 = speed search
점프** · Enter = 현재 패널에서 그 디렉토리로 cd 후 닫기. (pytmux 확장) Shift+Enter /
Ctrl+O = 그 디렉토리를 연 새 패널 분할. 결과는 dismiss(("cd"|"newpane", path)).

렌더링: `ListView`(항목마다 위젯) 대신 **`render_line` 기반 단일 위젯**(`_NcdView`)으로
직접 그린다 — 옛 NCD 처럼 한 줄 단위. ↑↓ 는 바뀐 **두 줄만** refresh 해 ssh 원격에서도
빠르고, 위젯 레이아웃 비용이 없다(MultiplexerView 와 같은 방식). 색은 세그먼트 스타일로
과거 NCD 팔레트(DOS 블루 패널·시안 선택 막대)를 입힌다.

ncd 기능은 플러그인(pytmuxlib/plugins/ncd)으로 분리돼 있다 — 이 디렉토리를 지우면
ncd/nc 명령은 프롬프트에서 조용히 사라진다(레지스트리 경유 디스패치)."""
from __future__ import annotations

import os

from rich.segment import Segment
from rich.style import Style
from textual.screen import ModalScreen
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

from pytmuxlib import i18n   # 1-4: 힌트/라벨 한글 리터럴을 en 사용자에게 노출 안 하게

# 과거 NCD/Norton 팔레트: DOS 블루 패널, 시안 선택 막대(포커스), 비포커스는 청록.
_BG = Style(color="#d6d6d6", bgcolor="#0000aa")
_SEL = Style(color="#000000", bgcolor="#00aaaa", bold=True)
_SEL_BLUR = Style(color="#ffffff", bgcolor="#008b8b")
# 현재 디렉토리(cwd) 행 — 커서를 옮겨도 어디가 현재 위치인지 보이도록 밝은 노랑으로
# 강조한다(커서가 그 위에 있으면 선택 막대가 우선). 행 끝엔 ◀ 마커도 붙인다.
_CWD = Style(color="#ffff55", bgcolor="#0000aa", bold=True)
_CWD_MARK = " ◀"

# 한글 원문을 i18n 키로 쓰고(코드베이스 관례) en 번역을 등록한다. 렌더 시점에
# i18n.t(_HINT)/i18n.t(_FIND) 로 클라 로케일을 따른다(모듈 상수라 import 시점 번역
# 금지 — 그러면 로케일 고정).
_HINT = ("↑↓·PgUp/PgDn·Home/End 이동 · →펼치기 ←접기 · 타이핑 찾기 · "
         "Enter cd · ⇧Enter/^O 새 패널 · Esc")
_FIND = "찾기"
i18n.register({
    "ko": {_HINT: _HINT, _FIND: _FIND},
    "en": {_HINT: ("↑↓·PgUp/PgDn·Home/End move · → expand ← collapse · type to "
                   "find · Enter cd · ⇧Enter/^O new pane · Esc"),
           _FIND: "Find"},
})


class _NcdView(Widget):
    """디렉토리 트리를 한 줄 단위로 직접 그리는 뷰(스크롤·커서 자체 관리, 스크롤바
    없음). 화살표 한 칸 이동은 바뀐 두 줄만 다시 그려 ssh 에서도 즉각적이다."""
    can_focus = True

    def __init__(self, root: str, chain=None, cwd: str | None = None, dirs=None):
        super().__init__(id="ncdview")
        self._root = root
        self._cwd = cwd
        self._children: dict[str, list[str]] = {}
        self._expanded: set[str] = set()
        self._rows: list[tuple[str, int]] = []
        self._pending: str | None = None
        self._find = ""
        self._find_requested = ""   # 마지막으로 서버 재귀검색을 요청한 query(중복 방지)
        self._sel = 0          # 선택(하이라이트) 행 인덱스
        self._top = 0          # 뷰포트 첫 표시 행 인덱스
        for entry in (chain or []):
            p, kids = entry[0], list(entry[1] or [])
            self._children[p] = kids
            if kids:
                self._expanded.add(p)
        if dirs is not None:
            self._children[root] = list(dirs)
        if self._children.get(root):
            self._expanded.add(root)

    def on_mount(self):
        self._rebuild_rows(keep_path=self._cwd)
        self.focus()

    def on_resize(self, event):
        self._clamp_view()
        self.refresh()

    # ---- 트리 평탄화·표시 ----
    def _flatten(self) -> list[tuple[str, int]]:
        rows: list[tuple[str, int]] = []

        def walk(parent: str, depth: int):
            for child in self._children.get(parent, []):
                rows.append((child, depth))
                if child in self._expanded:
                    walk(child, depth + 1)

        walk(self._root, 0)
        return rows

    @staticmethod
    def _disp_name(path: str) -> str:
        # 표시·검색용 이름. basename 이 비면(루트 '/' 또는 드라이브 'C:\\') 경로 자체.
        # 슬래시·백슬래시(Windows) 모두 끝에서 떼고 본다.
        return os.path.basename(path.rstrip("/\\")) or path

    def _row_text(self, path: str, depth: int) -> str:
        if path in self._expanded:
            marker = "▾"
        elif path in self._children and not self._children[path]:
            marker = " "    # 로드됨·자식 없음 → 잎
        else:
            marker = "▸"    # 접힘 또는 미로드
        name = self._disp_name(path)
        # 드라이브/루트(C:\ · /)는 구분자로 끝나므로 슬래시를 덧붙이지 않는다.
        suffix = "" if name.endswith(("/", "\\")) else "/"
        row = "  " * depth + f"{marker} {name}{suffix}"
        if self._cwd is not None and path == self._cwd:
            row += _CWD_MARK            # 현재 디렉토리 표시(가리킴)
        return row

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        i = self._top + y
        if not (0 <= i < len(self._rows)):
            return Strip.blank(width, _BG)
        path, depth = self._rows[i]
        if i == self._sel:
            style = _SEL if self.has_focus else _SEL_BLUR
        elif self._cwd is not None and path == self._cwd:
            style = _CWD            # 현재 디렉토리 강조(커서가 다른 곳에 있을 때)
        else:
            style = _BG
        text = self._row_text(path, depth)
        # 한 세그먼트로 행 내용을 만들고 폭에 맞춰 같은 스타일로 채우거나 자른다
        # (한글 등 와이드 문자도 셀 기준으로 정확히 패딩 — 선택 막대가 끝까지 채워짐).
        return Strip([Segment(text, style)]).adjust_cell_length(width, style)

    # ---- 커서·스크롤(빠른 부분 갱신) ----
    def _cur(self) -> str | None:
        if 0 <= self._sel < len(self._rows):
            return self._rows[self._sel][0]
        return None

    def _clamp_view(self):
        n = len(self._rows)
        if n == 0:
            self._sel = self._top = 0
            return
        self._sel = max(0, min(n - 1, self._sel))
        h = self.size.height
        if h <= 0:
            return                  # 레이아웃 전 — on_resize 에서 재계산
        if self._sel < self._top:
            self._top = self._sel
        elif self._sel >= self._top + h:
            self._top = self._sel - h + 1
        self._top = max(0, min(self._top, max(0, n - h)))

    def _move(self, new: int):
        n = len(self._rows)
        if not n:
            return
        new = max(0, min(n - 1, new))
        if new == self._sel:
            return
        old = self._sel
        self._sel = new
        h = self.size.height or 1
        old_top = self._top
        if new < self._top:
            self._top = new
        elif new >= self._top + h:
            self._top = new - h + 1
        if self._top != old_top:
            self.refresh()                          # 스크롤 → 뷰포트 전체
        else:                                       # 선택만 이동 → 두 줄만(ssh 최소)
            w = self.size.width
            self.refresh(Region(0, old - self._top, w, 1))
            self.refresh(Region(0, new - self._top, w, 1))

    def _rebuild_rows(self, keep_path: str | None = None):
        if keep_path is None and 0 <= self._sel < len(self._rows):
            keep_path = self._rows[self._sel][0]
        self._rows = self._flatten()
        self._sel = next((i for i, (p, _d) in enumerate(self._rows)
                          if p == keep_path), 0)
        self._clamp_view()
        self.refresh()

    # ---- speed search ----
    def _set_subtitle(self):
        try:
            box = self.screen.query_one("#ncbox", Vertical)
        except Exception:
            return
        box.border_subtitle = (
            f"{i18n.t(_HINT)}    {i18n.t(_FIND)}: {self._find}"
            if self._find else i18n.t(_HINT))

    def _reset_find(self):
        if self._find:
            self._find = ""
            self._find_requested = ""
            self._set_subtitle()

    def _jump(self):
        if not self._find or not self._rows:
            return
        q = self._find.lower()
        n = len(self._rows)
        start = self._sel
        for match_prefix in (True, False):
            for off in range(n):
                i = (start + off) % n
                name = self._disp_name(self._rows[i][0]).lower()
                if (name.startswith(q) if match_prefix else q in name):
                    self._move(i)
                    return
        # 보이는 트리에 없음 → 트리에 안 열린 디렉토리까지 서버에 재귀 검색을 요청해
        # (요청 2026-06-16) 매치가 있으면 그 경로까지 펼쳐 선택한다(apply_found).
        # 2글자 이상 + 같은 query 중복 요청 방지(키 입력마다 광역 walk 폭주 차단).
        if len(self._find) >= 2 and self._find != self._find_requested:
            self._find_requested = self._find
            fn = getattr(self.app, "request_nc_find", None)
            if fn:
                fn(self._find, self._root)

    def apply_found(self, query: str, target: str | None, chain):
        """서버 재귀 검색 결과 적용(요청). target 이 있으면 조상 사슬로 _children 를
        채우고 각 조상을 펼친 뒤 target 행을 선택한다. query 가 그새 바뀌었으면(사용자가
        더 타이핑) 무시 — 엉뚱한 점프 방지. 못 찾았으면 조용히 둔다(찾기 부제 유지)."""
        if not target or query != self._find:
            return
        for entry in (chain or []):
            p, kids = entry[0], list(entry[1] or [])
            self._children[p] = kids
            self._expanded.add(p)
        self._rebuild_rows(keep_path=target)

    # ---- 지연 펼치기 ----
    async def _expand(self, path: str):
        if path in self._expanded:
            return
        if path in self._children:
            if self._children[path]:
                self._expanded.add(path)
                self._rebuild_rows(keep_path=path)
        else:
            self._pending = path
            self.app.request_nc_list(path)

    def fill_children(self, path: str, dirs):
        self._children[path] = list(dirs or [])
        if self._pending == path:
            self._pending = None
            if self._children[path]:
                self._expanded.add(path)
        self._rebuild_rows(keep_path=path)

    # ---- 키 ----
    async def on_key(self, event: events.Key):
        k = event.key
        if k == "escape":
            event.stop()
            self.screen.dismiss(None)
            return
        cur = self._cur()
        if k == "enter":                    # 현재 패널에서 cd(NCD 핵심)
            event.stop()
            if cur is not None:
                self.screen.dismiss(("cd", cur))
        elif k in ("shift+enter", "ctrl+o"):   # 새 패널(분할) — ^O = 검색 비충돌 폴백
            event.stop()
            if cur is not None:
                self.screen.dismiss(("newpane", cur))
        elif k == "up":
            event.stop(); self._reset_find(); self._move(self._sel - 1)
        elif k == "down":
            event.stop(); self._reset_find(); self._move(self._sel + 1)
        elif k in ("home", "end", "pageup", "pagedown"):
            event.stop(); self._reset_find()
            page = max(1, (self.size.height or 10) - 1)
            if k == "home":
                self._move(0)
            elif k == "end":
                self._move(len(self._rows) - 1)
            elif k == "pageup":
                self._move(self._sel - page)
            else:
                self._move(self._sel + page)
        elif k == "right":                  # 펼치기
            event.stop(); self._reset_find()
            if cur is not None:
                await self._expand(cur)
        elif k == "left":                   # 접기 또는 부모로
            event.stop(); self._reset_find()
            if cur is None:
                return
            if cur in self._expanded:
                self._expanded.discard(cur)
                self._rebuild_rows(keep_path=cur)
            else:
                parent = os.path.dirname(cur.rstrip("/\\"))
                pi = next((i for i, (p, _d) in enumerate(self._rows)
                           if p == parent), None)
                if pi is not None:
                    self._move(pi)
        elif k == "backspace":
            event.stop()
            if self._find:
                self._find = self._find[:-1]
                self._set_subtitle()
                self._jump()
        elif event.character and event.character.isprintable() \
                and len(event.character) == 1:
            event.stop()                    # speed search 글자 입력
            self._find += event.character
            self._set_subtitle()
            self._jump()

    def on_click(self, event: events.Click):
        # 클릭한 행으로 커서 이동(없으면 무시).
        i = self._top + event.y
        if 0 <= i < len(self._rows):
            event.stop()
            self._move(i)

    # ---- 마우스 휠 스크롤(요청) ----
    def _scroll(self, delta: int):
        """뷰포트를 delta 행 만큼 굴린다. 선택(커서)은 뷰포트 안에 유지해 Enter cd
        대상이 화면 밖으로 사라지지 않게 한다(가시 행 밖이면 끝줄로 끌려온다)."""
        n = len(self._rows)
        h = self.size.height or 1
        if n <= h:
            return                      # 다 보이면 스크롤 불필요
        new_top = max(0, min(self._top + delta, n - h))
        if new_top == self._top:
            return
        self._top = new_top
        self._sel = max(self._top, min(self._sel, self._top + h - 1))
        self.refresh()

    def on_mouse_scroll_down(self, event):
        event.stop(); self._reset_find(); self._scroll(3)

    def on_mouse_scroll_up(self, event):
        event.stop(); self._reset_find(); self._scroll(-3)


class NcdScreen(ModalScreen):
    # 과거 NCD/Norton 외형: DOS 블루(#0000aa) 패널, 시안(#00aaaa) 이중 테두리.
    CSS = """
    NcdScreen { align: center middle; }
    #ncbox { width: 90%; height: 90%; padding: 0 1;
             background: #0000aa; color: #d6d6d6;
             border: double #00aaaa;
             border-title-color: #ffffff; border-title-background: #0000aa;
             border-subtitle-color: #6fdcdc; border-subtitle-background: #0000aa; }
    #ncdview { height: 1fr; width: 1fr; }
    """

    def __init__(self, root: str, chain=None, cwd: str | None = None, dirs=None):
        super().__init__()
        self._root = root
        self._cwd = cwd
        self._view = _NcdView(root, chain=chain, cwd=cwd, dirs=dirs)

    def compose(self) -> ComposeResult:
        with Vertical(id="ncbox"):
            yield self._view

    def on_mount(self):
        box = self.query_one("#ncbox", Vertical)
        box.border_title = f"ncd → {self._cwd or self._root}"
        box.border_subtitle = i18n.t(_HINT)

    def fill_children(self, path: str, dirs):
        self._view.fill_children(path, dirs)

    def apply_found(self, query, target, chain):
        self._view.apply_found(query, target, chain)
