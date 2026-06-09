"""ncd — Norton Change Directory 풍 디렉토리 트리 모달(코드네임 nc, docs/NC_SCENARIO.md).

NCD(Norton Change Directory)의 재현: 루트(`/`)부터의 **디렉토리 전용** 트리를 화면
가득 띄우되 **현재 패널 cwd 까지 펼쳐 그 위에 커서**를 둔다. ↑↓ 이동 · → 펼치기(지연
로드) · ← 접기/부모 · **디렉토리명 타이핑 = speed search 점프** · Enter = 현재 패널에서
그 디렉토리로 cd 후 닫기. (pytmux 확장) Shift+Enter / Ctrl+O = 그 디렉토리를 연 새 패널
분할. 결과는 dismiss(("cd"|"newpane", path)) 로 돌려준다.

디렉토리 목록은 서버가 제공한다(app.request_nc_list → t=nc_list; 초기엔 루트→cwd
chain 으로 이 화면을 열고, 펼치기 응답은 fill_children 로 전달). clientscreens.py 의
ChooseTreeScreen/CommandListScreen 패턴을 따르되, 별 파일로 둬 다른 작업(WIP)과
체인지리스트가 섞이지 않게 한다."""
from __future__ import annotations

import os

from textual.screen import ModalScreen
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView

_HINT = ("↑↓ 이동 · → 펼치기 · ← 접기 · 타이핑 찾기 · "
         "Enter cd · ⇧Enter/^O 새 패널 · Esc 닫기")


class NcdScreen(ModalScreen):
    CSS = """
    NcdScreen { align: center middle; }
    #ncbox { width: 90%; height: 90%; border: round $accent;
             background: $panel; padding: 0 1; }
    #nctree { height: 1fr; }
    """

    def __init__(self, root: str, chain=None, cwd: str | None = None, dirs=None):
        super().__init__()
        self._root = root
        self._cwd = cwd
        # 부모경로 → 자식 절대경로 리스트. 로드된 노드만 키로 존재(미로드와 구분).
        self._children: dict[str, list[str]] = {}
        self._expanded: set[str] = set()    # 펼쳐진 노드(자식 표시 중)
        self._rows: list[tuple[str, int]] = []   # (path, depth) 평탄화 결과
        self._pending: str | None = None    # 펼치기 응답 대기 중인 노드
        self._find = ""                     # speed search 버퍼
        # 초기 사슬(루트→cwd)을 펼친 상태로 적재. chain 항목 = [dir, [자식…]].
        for entry in (chain or []):
            p, kids = entry[0], list(entry[1] or [])
            self._children[p] = kids
            if kids:
                self._expanded.add(p)       # 사슬은 펼쳐 보인다
        if dirs is not None:                # (단순 루트 1단계 케이스)
            self._children[root] = list(dirs)
        # 루트 자체도 자식이 있으면 펼침(트리 최상단 노드들이 보이게).
        if self._children.get(root):
            self._expanded.add(root)

    def compose(self) -> ComposeResult:
        with Vertical(id="ncbox"):
            yield ListView(id="nctree")

    async def on_mount(self):
        self._set_subtitle()
        box = self.query_one("#ncbox", Vertical)
        box.border_title = f"ncd → {self._cwd or self._root}"
        # 처음엔 현재 cwd 행에 커서를 둔다(NCD: 현재 위치에서 시작).
        await self._rebuild(keep_path=self._cwd)
        self.query_one(ListView).focus()

    def _set_subtitle(self):
        box = self.query_one("#ncbox", Vertical)
        box.border_subtitle = (f"{_HINT}    찾기: {self._find}"
                               if self._find else _HINT)

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

    def _label(self, path: str, depth: int) -> str:
        if path in self._expanded:
            marker = "▾"
        elif path in self._children and not self._children[path]:
            marker = " "    # 로드됨·자식 없음 → 잎(펼칠 것 없음)
        else:
            marker = "▸"    # 접힘 또는 미로드
        name = os.path.basename(path.rstrip("/")) or path
        return "  " * depth + f"{marker} {name}/"

    async def _rebuild(self, keep_path: str | None = None):
        """현재 상태로 ListView 를 다시 그린다. keep_path 가 없으면 현재 하이라이트한
        경로를, 있으면 그 경로를 다시 선택해 펼치기/접기 후에도 커서를 유지한다."""
        lv = self.query_one(ListView)
        if keep_path is None:
            cur = lv.index
            if cur is not None and 0 <= cur < len(self._rows):
                keep_path = self._rows[cur][0]
        self._rows = self._flatten()
        # clear→extend 를 await 로 순서 보장(ID 충돌/잔상 방지 — CommandListScreen 관례).
        await lv.clear()
        if self._rows:
            await lv.extend([ListItem(Label(self._label(p, d), markup=False))
                             for p, d in self._rows])
            idx = next((i for i, (p, _d) in enumerate(self._rows)
                        if p == keep_path), 0)
            lv.index = idx

    def _cur_path(self) -> str | None:
        lv = self.query_one(ListView)
        i = lv.index
        if i is not None and 0 <= i < len(self._rows):
            return self._rows[i][0]
        return None

    # ---- speed search(이름 타이핑 즉시 점프) ----
    def _jump(self):
        """현재 위치부터 순환하며 basename 이 버퍼로 시작하는 첫 디렉토리로 이동.
        없으면 부분일치로 한 번 더. 대소문자 무시(NCD speed search)."""
        if not self._find or not self._rows:
            return
        q = self._find.lower()
        lv = self.query_one(ListView)
        start = lv.index or 0
        n = len(self._rows)
        for match_prefix in (True, False):
            for off in range(n):
                i = (start + off) % n
                name = os.path.basename(self._rows[i][0].rstrip("/")).lower()
                if (name.startswith(q) if match_prefix else q in name):
                    lv.index = i
                    return

    def _reset_find(self):
        if self._find:
            self._find = ""
            self._set_subtitle()

    # ---- 지연 펼치기 ----
    async def _expand(self, path: str):
        if path in self._expanded:
            return
        if path in self._children:          # 이미 로드됨
            if self._children[path]:        # 자식 있으면 펼침(없으면 잎 — 무시)
                self._expanded.add(path)
                await self._rebuild(keep_path=path)
        else:                               # 미로드 → 서버에 자식 요청
            self._pending = path
            self.app.request_nc_list(path)

    def fill_children(self, path: str, dirs):
        """서버 nc_list(펼치기) 응답 수신 시 app 이 호출. 자식을 채우고, 방금
        펼치기 요청한 노드면 펼친 상태로 만들어 다시 그린다."""
        self._children[path] = list(dirs or [])
        if self._pending == path:
            self._pending = None
            if self._children[path]:
                self._expanded.add(path)
        self.run_worker(self._rebuild(keep_path=path))

    # ---- 키 ----
    async def on_key(self, event: events.Key):
        k = event.key
        if k == "escape":
            event.stop()
            self.dismiss(None)
            return
        cur = self._cur_path()
        if k == "enter":                    # 현재 패널에서 cd(NCD 핵심)
            event.stop()
            if cur is not None:
                self.dismiss(("cd", cur))
        elif k in ("shift+enter", "ctrl+o"):   # 새 패널(분할) — ^O = speed search 비충돌 폴백
            event.stop()
            if cur is not None:
                self.dismiss(("newpane", cur))
        elif k == "right":                  # 펼치기
            event.stop()
            self._reset_find()
            if cur is not None:
                await self._expand(cur)
        elif k == "left":                   # 접기 또는 부모로
            event.stop()
            self._reset_find()
            if cur is None:
                return
            if cur in self._expanded:
                self._expanded.discard(cur)
                await self._rebuild(keep_path=cur)
            else:
                parent = os.path.dirname(cur.rstrip("/"))
                pidx = next((i for i, (p, _d) in enumerate(self._rows)
                             if p == parent), None)
                if pidx is not None:
                    self.query_one(ListView).index = pidx
        elif k in ("up", "down", "home", "end", "pageup", "pagedown"):
            self._reset_find()              # 이동하면 speed search 리셋(증분 검색 관례)
            # stop 안 함 → ListView 기본 이동.
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

    def on_list_view_selected(self, event):
        # 마우스/Enter 로 항목 선택 → cd(키보드 Enter 는 on_key 가 먼저 처리·stop).
        cur = self._cur_path()
        if cur is not None:
            self.dismiss(("cd", cur))
