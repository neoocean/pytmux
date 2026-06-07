"""클라이언트 모달 스크린(팝업) — 명령목록·옵션·메뉴·트리·정보·토큰로그·
프롬프트·확인·버퍼/레이아웃 선택·권한모드.

client.py 의 거대 클로저(build_client_app)에서 분리한 자족적 ModalScreen
들(§10 LLM 친화 리팩토링). config/sock_path 를 캡처하지 않고, 데이터는
__init__ 인자로 받아 dismiss 로 결과를 돌려준다. 앱 상호작용은 self.app 으로
런타임에 한다. client.py 가 이름으로 import 해 push_screen 한다."""
from __future__ import annotations

import re
from datetime import datetime

from textual.screen import ModalScreen
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (DataTable, Input, Label, ListItem, ListView,
                             Static, TextArea)

from rich.highlighter import Highlighter
from rich.text import Text

from . import usagelog
from .clientutil import (COMMAND_FREETEXT, COMMAND_NOARG, COMMAND_OPTIONS,
                         COMMANDS, MENU_ITEMS, MENU_TOGGLES, SAVER_ROWS,
                         _char_cells, has_hangul, hangul_to_qwerty,
                         theme_color)


class _CommandWordHighlighter(Highlighter):
    """명령 프롬프트 입력에서 **첫 토큰(명령어)** 에만 옅은 배경을 입혀 인자와
    시각적으로 구분한다(사용자 요청). 명령어와 인자 사이 첫 공백까지가 명령 토큰."""

    def __init__(self, style: str):
        self._style = style          # 예: "on #2f3b52"(rich 스타일 문자열)

    def highlight(self, text):
        s = text.plain
        n = len(s)
        i = 0
        while i < n and s[i] == " ":   # 선행 공백 건너뜀
            i += 1
        j = i
        while j < n and s[j] != " ":   # 첫 토큰 끝(다음 공백)까지
            j += 1
        if j > i:
            text.stylize(self._style, i, j)


class CommandListScreen(ModalScreen):
    """명령 목록 선택기(? 입력 시). 명령이 많아 카테고리별 탭으로 나눠 한 번에 한
    카테고리만 보여준다. 탭은 별도 외곽선으로 감싸고(#32 식) ←→ 또는 마우스
    클릭으로 전환한다. ↑↓ 명령 이동, Home/End 로 목록 처음·끝, Enter 선택,
    Esc/[x] 닫기. 상단 검색창에 타이핑하면 즉시 필터링되고, 각 탭에(활성/비활성
    모두) 일치 수가 표시돼 어느 탭에 결과가 있는지 보인다.

    검색창은 표시 전용(can_focus=False)이다 — 포커스는 ListView 에 두고 화면
    on_key 에서 글자/Backspace 를 가로채 검색어를 키운다. 이렇게 해야 Input 이
    ←→·Home/End 를 커서 이동으로 가로채지 않아 탭 전환·목록 점프가 그대로
    동작한다."""
    CSS = """
    /* §10: 터미널 배경이 팝업 배경($panel)과 같아 박스가 묻히지 않게, 백드롭을
       더 어둡게($background 80% — Textual 기본 60%보다 진하게) 깔아 팝업과
       구분되게 한다. 실제 색 블렌드라 터미널 무관하게 균일하다. */
    /* 하단 프롬프트(esc :)에서 ? 로 여는 인터페이스라 팝업을 바닥(프롬프트 바로
       위)에 붙인다 — 프롬프트 입력칸(3행)을 margin-bottom 으로 비운다(#). */
    CommandListScreen { align: center bottom; background: $background 80%; }
    #cmdbox { width: 90%; max-width: 96; height: auto; max-height: 85%;
              margin-bottom: 3;
              border: round $accent; background: $panel; padding: 0 1; }
    /* 탭 그룹을 별도 외곽선으로 감싼다(#32) → head 는 3행. [x] 는 우측 끝(1fr). */
    #cmdhead { width: 100%; height: 3; }
    #cmdtabs { width: auto; height: 3; border: round $accent; }
    #cmdtabs Label { width: auto; height: 1; }   /* 탭 하나(클릭 대상) */
    #cmdgap { width: 1fr; height: 3; }
    /* 닫기 [x] 는 1행으로 우측 위 모서리에 붙인다(가로 흐름 마지막, 높이 1). */
    #cmdclose { width: 5; height: 1; content-align: center middle;
                background: $error; color: $text; text-style: bold; }
    /* 검색창(타이틀 탭줄과 목록 사이). 표시 전용이라 포커스를 받지 않는다. */
    #cmdsearch { width: 100%; height: 3; border: round $accent;
                 background: $panel-darken-1; }
    /* §10: 높이를 "항목이 가장 많은 카테고리" 기준으로 고정(on_mount 에서
       styles.height 설정) — ←→ 카테고리 전환·검색 필터 시 박스가 출렁이지
       않게. 항목이 적으면 ListView 아래쪽이 빈 채로 남아 높이를 유지한다. */
    #cmds { width: 100%;
            background: $panel;
            overflow-y: scroll;                 /* 항상 스크롤바 트랙 표시 */
            scrollbar-size-vertical: 2;
            scrollbar-color: $accent;
            scrollbar-color-hover: $accent-lighten-1;
            scrollbar-background: $panel-darken-2; }
    """

    # 카테고리 전환·검색 시 박스 높이 출렁임 방지용 화면 한도(행 수).
    _CMDS_MAX_ROWS = 18

    def __init__(self, items, query=""):
        super().__init__()
        # 전체 항목을 카테고리 등장 순서로 그룹화(검색은 런타임 필터 — 전체를 보관).
        order, bucket = [], {}
        for it in items:
            cat = it[2] if len(it) > 2 else "기타"
            if cat not in bucket:
                bucket[cat] = []
                order.append(cat)
            bucket[cat].append((it[0], it[1]))
        self._all_cats = [(c, bucket[c]) for c in order]
        self._ci = 0          # 현재 카테고리 인덱스
        self._cur = []        # 현재 카테고리의 (필터된) (이름, 설명) 목록
        self._query = query or ""   # 검색어(초기값=호출부가 넘긴 부분 입력)

    def compose(self) -> ComposeResult:
        with Vertical(id="cmdbox"):
            with Horizontal(id="cmdhead"):
                with Horizontal(id="cmdtabs"):       # 탭 그룹(외곽선, 클릭 대상)
                    for i, _c in enumerate(self._all_cats):
                        yield Label("", id=f"cmdtab_{i}", markup=True)
                yield Label("", id="cmdgap")          # [x] 를 우측 끝으로 미는 여백
                yield Label("[x]", id="cmdclose", markup=False)
            yield Input(placeholder="검색…", id="cmdsearch")
            yield ListView(id="cmds")

    async def on_mount(self):
        # §10: 모든 카테고리 중 최대 항목 수로 ListView 높이를 고정한다(전환·검색
        # 시 높이 불변). 화면을 넘지 않게 _CMDS_MAX_ROWS 로 클램프(초과 시 스크롤).
        maxn = max((len(items) for _, items in self._all_cats), default=1)
        self.query_one("#cmds").styles.height = min(maxn, self._CMDS_MAX_ROWS)
        si = self.query_one("#cmdsearch", Input)
        si.can_focus = False           # 표시 전용 — 클릭/탭 포커스가 키 모델을 깨지 않게
        si.value = self._query
        await self._rebuild()
        self.query_one(ListView).focus()

    def _matches(self, items):
        # 이름·설명 부분일치(대소문자 무시). 검색어가 비면 전체.
        q = self._query.strip().lower()
        if not q:
            return list(items)
        # 한영 오타 복원: 검색어에 한글이 섞이면 QWERTY 로 되돌려 매칭(이름에 한해).
        # 설명은 한글이므로 원문 q 로도 매칭해 양쪽 다 살린다.
        qn = hangul_to_qwerty(q).lower() if has_hangul(q) else q
        return [(n, d) for n, d in items
                if qn in n.lower() or q in n.lower() or q in d.lower()]

    async def _rebuild(self):
        searching = bool(self._query.strip())
        # 검색 중 현재 탭에 결과가 없으면 결과 있는 첫 탭으로 점프(빈 화면 방지).
        if searching and not self._matches(self._all_cats[self._ci][1]):
            for i, (_c, items) in enumerate(self._all_cats):
                if self._matches(items):
                    self._ci = i
                    break
        # 탭 바: 각 탭에 일치 수 표기. 활성=강조, 결과 있는 비활성=굵게, 없음=dim.
        for i, (c, items) in enumerate(self._all_cats):
            lbl = self.query_one(f"#cmdtab_{i}", Label)
            n = len(self._matches(items))
            if i == self._ci:
                lbl.update(f"[reverse b] {c} ({n}) [/]")
            elif searching and n:
                lbl.update(f"[b] {c} ({n}) [/]")
            else:
                lbl.update(f"[dim] {c} [/]")
        # 현재 카테고리(필터된) 명령으로 ListView 교체(clear→extend 를 await 로
        # 순서 보장해 ID 충돌/잔상을 피한다).
        self._cur = (self._matches(self._all_cats[self._ci][1])
                     if self._all_cats else [])
        lv = self.query_one(ListView)
        await lv.clear()
        if self._cur:
            await lv.extend([ListItem(Label(f"{n:<20} {d}"))
                             for n, d in self._cur])
            lv.index = 0
        else:
            await lv.extend([ListItem(Label("[dim](검색 결과 없음)[/]",
                                            markup=True))])
        box = self.query_one("#cmdbox", Vertical)
        box.border_title = "명령 목록"
        box.border_subtitle = ("타이핑 검색 · ←→/클릭 탭 · ↑↓ 명령 · "
                               "Home/End 처음·끝 · Enter 선택 · Esc 닫기")

    def _select_current(self):
        idx = self.query_one(ListView).index
        if idx is not None and 0 <= idx < len(self._cur):
            self.dismiss(self._cur[idx][0])

    def on_list_view_selected(self, event):
        self._select_current()

    async def _switch_to(self, i):
        if 0 <= i < len(self._all_cats) and i != self._ci:
            self._ci = i
            await self._rebuild()

    async def on_click(self, event: events.Click):
        # 조상 체인을 거슬러: [x]→닫기, 탭→전환, 박스 바깥(백드롭)→닫기.
        w = getattr(event, "widget", None)
        inside = False
        while w is not None:
            wid = getattr(w, "id", None)
            if wid == "cmdclose":
                event.stop(); self.dismiss(None); return
            if wid and wid.startswith("cmdtab_"):   # 탭 클릭 → 전환
                event.stop()
                await self._switch_to(int(wid.split("_")[1]))
                return
            if wid == "cmdbox":
                inside = True
            w = w.parent
        if not inside:
            event.stop(); self.dismiss(None)

    async def on_key(self, event: events.Key):
        key = event.key
        if key == "escape":
            event.stop()
            self.dismiss(None)
        elif key == "enter":
            # ListView 기본 Enter 바인딩이 포커스/타이밍 문제로 안 먹는
            # 경우가 있어 직접 현재 항목을 선택해 프롬프트에 채운다.
            event.stop()
            self._select_current()
        elif key in ("left", "right") and len(self._all_cats) > 1:
            event.stop()
            step = 1 if key == "right" else -1
            self._ci = (self._ci + step) % len(self._all_cats)
            await self._rebuild()
        elif key == "home":
            event.stop()
            lv = self.query_one(ListView)
            if self._cur:
                lv.index = 0
                try: lv.scroll_to_widget(lv.children[0])
                except Exception: pass
        elif key == "end":
            event.stop()
            lv = self.query_one(ListView)
            if self._cur:
                last = len(self._cur) - 1
                lv.index = last
                try: lv.scroll_to_widget(lv.children[last])
                except Exception: pass
        elif key == "backspace":
            # 검색창 표시 전용 — 글자 편집을 화면 on_key 에서 처리한다.
            event.stop()
            if self._query:
                self._query = self._query[:-1]
                self.query_one("#cmdsearch", Input).value = self._query
                await self._rebuild()
        elif (event.character and len(event.character) == 1
              and event.character.isprintable()):
            event.stop()
            self._query += event.character
            self.query_one("#cmdsearch", Input).value = self._query
            await self._rebuild()

class CommandOptionsScreen(ModalScreen):
    """커맨드 팔레트에서 고른 명령의 옵션(선택지)을 모달 안에서 정한 뒤 프롬프트를
    거치지 않고 바로 실행한다(#3). 옵션 행을 ListView 로 두어(MenuScreen 식 포커스)
    ↑↓ 로 옵션 이동, ←→ 로 값 변경, Enter 실행, Esc 취소 — 키보드만으로 제어된다.
    완성된 명령 줄을 dismiss 로 돌려주면 호출부가 _run_command 로 바로 실행한다."""
    CSS = """
    CommandOptionsScreen { align: center middle; }
    #optmenu { width: 54; height: auto; max-height: 80%;
               border: round $accent; background: $panel; }
    """

    def __init__(self, name, desc, opts):
        super().__init__()
        self.cmd_name = name
        self.cmd_desc = desc
        self.opts = opts
        self.sel = [0 for _ in opts]   # 각 옵션의 현재 선택 index

    def compose(self) -> ComposeResult:
        # MenuScreen 과 동일한 구조(ListView 직접 + 비지 않은 Label)로 둔다 —
        # Vertical 래퍼나 빈 Label 을 섞으면 합성 단계에서 렌더 오류가 났다.
        self._labs = []
        items = []
        for i in range(len(self.opts)):
            lab = Label(self._row_text(i))
            self._labs.append(lab)
            items.append(ListItem(lab, id=f"o_{i}"))
        yield ListView(*items, id="optmenu")

    def on_mount(self):
        lv = self.query_one(ListView)
        if self.opts:
            lv.index = 0
        lv.focus()
        lv.border_title = f"{self.cmd_name} 옵션 · ←→ 값 · Enter 실행 · Esc"
        self._update_sub()

    def _update_sub(self):
        self.query_one(ListView).border_subtitle = ": " + self._build_line()

    def _build_line(self):
        toks = [self.cmd_name]
        for o, si in zip(self.opts, self.sel):
            val = o["choices"][si][1]
            if val:
                toks.append(val)
        return " ".join(toks)

    def _row_text(self, i):
        o, si = self.opts[i], self.sel[i]
        cur = o["choices"][si][0]
        arrows = "◀ ▶" if len(o["choices"]) > 1 else "    "
        return f"{o['label']}:  {arrows}  {cur}"

    def on_list_view_selected(self, event):
        # Enter/클릭 으로 행 선택 시 현재 값으로 바로 실행(on_key 와 이중 안전).
        self.dismiss(self._build_line())

    def on_key(self, event: events.Key):
        k = event.key
        if k == "escape":
            event.stop()
            self.dismiss(None)
        elif k == "enter":
            event.stop()
            self.dismiss(self._build_line())
        elif k in ("left", "right") and self.opts:
            event.stop()
            i = self.query_one(ListView).index or 0
            o = self.opts[i]
            step = 1 if k == "right" else -1
            self.sel[i] = (self.sel[i] + step) % len(o["choices"])
            self._labs[i].update(self._row_text(i))
            self._update_sub()

class MenuScreen(ModalScreen):
    CSS = """
    MenuScreen { align: center middle; }
    #menu { width: 40; height: auto; border: round $accent; background: $panel; }
    """

    def compose(self) -> ComposeResult:
        self._labels = {}     # key -> (Label 위젯, 원본 라벨)
        self._optim = {}      # 토글 낙관적 상태(status 회신 전 즉시 반영)
        items = []
        for key, label in MENU_ITEMS:
            lab = Label(self._fmt(key, label))
            self._labels[key] = (lab, label)
            items.append(ListItem(lab, id=f"m_{key}"))
        yield ListView(*items, id="menu")

    def _toggle_state(self, key):
        if key in self._optim:
            return self._optim[key]
        st = self.app.status
        return {"zoom": st.zoomed, "sync": st.sync,
                "autoresume": st.autoresume,
                "prompt_clear": getattr(st, "prompt_clear", False)}.get(
                    key, False)

    def _fmt(self, key, label):
        if key in MENU_TOGGLES:
            return f"{label}  {'●' if self._toggle_state(key) else '○'}"
        return label

    def refresh_labels(self):
        # status 회신으로 실제 상태가 왔을 때 호출 — 낙관적 값을 버리고 갱신.
        self._optim = {}
        for key, (lab, base) in getattr(self, "_labels", {}).items():
            if key in MENU_TOGGLES:
                lab.update(self._fmt(key, base))

    def on_mount(self):
        self.query_one(ListView).focus()
        self.app._menu_screen = self   # status 갱신 시 라벨 다시 그리기 위해

    def on_unmount(self):
        if getattr(self.app, "_menu_screen", None) is self:
            self.app._menu_screen = None

    def on_list_view_selected(self, event):
        key = event.item.id[2:]
        if key in MENU_TOGGLES:
            # 토글: 메뉴를 닫지 않고 명령만 보낸 뒤 라벨을 낙관적으로 갱신.
            # ESC 로만 닫는다. 실제 상태는 status 회신 때 refresh_labels 로 확정.
            self.app._run_menu_action(key)
            self._optim[key] = not self._toggle_state(key)
            lab, base = self._labels[key]
            lab.update(self._fmt(key, base))
        else:
            self.dismiss(key)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)

class ClaudeSaverScreen(ModalScreen):
    """토큰 절감 설정 팝업(docs/TOKEN_SAVING_SCENARIO.md, `token-saver` 명령).

    각 자동 개입을 ●/○ 로 토글하고, 정리 방식·잔량 임계·일/세션 예산을 Enter 로
    프리셋 순환한다. ESC 로 닫는다. MenuScreen 과 같은 위임 구조 — 현재값/동작은
    app._saver_display/_saver_action 이 처리하고(앱 상태 의존), 서버 status 회신마다
    refresh_labels 로 권위값을 다시 그린다(client.py 의 _saver_screen 훅)."""
    CSS = """
    ClaudeSaverScreen { align: center middle; background: $background 80%; }
    #saver { width: 64; height: auto; max-height: 90%;
             border: round $accent; background: $panel; }
    #saver_hint { color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        self._labels = {}
        items = []
        for key, label, _kind in SAVER_ROWS:
            lab = Label(self._fmt(key, label))
            self._labels[key] = (lab, label)
            items.append(ListItem(lab, id=f"s_{key}"))
        items.append(ListItem(Label("Enter 토글/순환 · ESC 닫기", id="saver_hint"),
                              id="s__hint", disabled=True))
        yield ListView(*items, id="saver")

    def _fmt(self, key, label):
        return f"{label}   {self.app._saver_display(key)}"

    def refresh_labels(self):
        for key, (lab, base) in getattr(self, "_labels", {}).items():
            lab.update(self._fmt(key, base))

    def on_mount(self):
        self.query_one(ListView).focus()
        self.app._saver_screen = self

    def on_unmount(self):
        if getattr(self.app, "_saver_screen", None) is self:
            self.app._saver_screen = None

    def on_list_view_selected(self, event):
        item_id = event.item.id or ""
        if not item_id.startswith("s_") or item_id == "s__hint":
            return
        key = item_id[2:]
        # 토글/순환은 팝업을 닫지 않고 동작만 보낸 뒤 라벨을 낙관적으로 갱신한다
        # (_saver_action 이 status 를 즉시 반영). 서버 broadcast 가 권위값으로 확정.
        self.app._saver_action(key)
        self.refresh_labels()

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class ChooseTreeScreen(ModalScreen):
    CSS = """
    ChooseTreeScreen { align: center middle; }
    #tree { width: 64; height: auto; max-height: 80%;
            border: round $accent; background: $panel; }
    """

    def __init__(self, tree):
        super().__init__()
        self._treedata = tree
        self.entries = []   # 행별 dict: {kind: win|pane, index, pid?}

    def compose(self) -> ComposeResult:
        items = []
        n = 0
        for s in self._treedata.get("sessions", []):
            for w in s["windows"]:
                panes = w.get("panes", [])
                npanes = len(panes) if isinstance(panes, list) else panes
                mark = "▾" if w.get("active") else "▸"
                wlabel = f"{mark} {w['index']}:{w['name']}  ({npanes} panes)"
                self.entries.append({"kind": "win", "index": w["index"]})
                # markup=False — 라벨에 들어가는 [ssh]/제목의 대괄호가 Textual
                # 마크업으로 해석돼 사라지지 않게 한다.
                items.append(ListItem(Label(wlabel, markup=False), id=f"e{n}"))
                n += 1
                if isinstance(panes, list):
                    for p in panes:
                        app = p.get("cmd") or "shell"
                        badge = "[ssh]" if p.get("remote") else "[local]"
                        title = (p.get("title") or "").strip()
                        ttxt = f" · {title}" if title and title != "shell" else ""
                        plabel = f"    └ {badge} {app}{ttxt}"
                        self.entries.append({"kind": "pane",
                                             "index": w["index"],
                                             "pid": p["id"]})
                        items.append(ListItem(Label(plabel, markup=False),
                                              id=f"e{n}"))
                        n += 1
        yield ListView(*items, id="tree")

    def on_mount(self):
        self.query_one(ListView).focus()

    def on_list_view_selected(self, event):
        self.dismiss(("select", self.entries[int(event.item.id[1:])]))

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key in ("d", "x"):   # 선택 항목(탭/패널) 종료
            lv = self.query_one(ListView)
            idx = lv.index
            if idx is not None and 0 <= idx < len(self.entries):
                event.stop()
                self.dismiss(("kill", self.entries[idx]))

_DIVIDER_CHARS = {"─", "—", "-", "·", " "}


def _hangwrap(line, width):
    """긴 줄을 width 안으로 **하드 줄바꿈**하되, 'NN. '/'NN) ' 번호 접두사 폭만큼
    이어줄을 들여써 번호 정렬을 보존한다(§10-A #7). 가능한 공백 경계에서 끊고,
    공백이 없으면(긴 URL 등) 글자 단위로 자른다. 짧은 줄은 그대로 [line]."""
    if width < 8 or len(line) <= width:
        return [line]
    m = re.match(r"\s*\d+[.)]\s+", line)
    indent = " " * (len(m.group(0)) if m else 0)
    out, cur = [], line
    while len(cur) > width:
        cut = cur.rfind(" ", len(indent) + 1, width)   # 들여쓰기 뒤 공백 경계
        if cut <= len(indent):
            cut = width                                  # 경계 없음 → 하드 컷
        out.append(cur[:cut].rstrip())
        cur = indent + cur[cut:].lstrip()
    out.append(cur)
    return out


class InfoScreen(ModalScreen):
    """간단한 읽기전용 목록 표시(show-options 등). 방향키로 항목을 내비게이션하고
    그 외 키를 누르면 닫힌다. 긴 줄(예: 프롬프트 히스토리)은 여러 줄로 줄바꿈."""
    # 항목 Label 을 컨테이너 폭(1fr)에 맞춰 줄바꿈(text-wrap 기본 wrap) → 긴
    # 프롬프트가 잘리지 않고 여러 줄로 펼쳐진다. ListItem 은 height:auto 로 늘어남.
    # 폭은 좁은 화면(모바일)에 맞춰 반응형: 96% 로 채우되 넓은 화면에선 66 칸으로
    # 캡. 제목 줄은 [제목 … [x]] 헤더로 두어 좁아도 닫기 [x] 가 (고정폭이라) 항상
    # 오른쪽에 보인다(예전엔 고정폭 박스가 화면을 넘쳐 닫기 수단이 안 보였다).
    CSS = """
    /* 상단 패널(헤더)을 클릭해 여는 팝업이라 화면 위쪽에 붙인다 — 탭바 한 줄
       아래(margin-top)에서 시작(#). */
    InfoScreen { align: center top; background: $background 80%; }
    #infobox { width: 96%; max-width: 66; height: auto; max-height: 95%;
               margin-top: 1;
               border: round $accent; background: $panel; padding: 0 1; }
    #infohead { width: 100%; height: 1; }
    #infotitle { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #infoclose { width: 5; height: 1; content-align: center middle;
                 background: $error; color: $text; text-style: bold; }
    /* §10: 상하 공간이 넉넉하면 내용을 **한 번에** 보여준다(사용자 요청) — 리스트는
       내용 높이(height:auto)만큼 자라고, 박스(#infobox)가 화면의 95%까지만 커지도록
       막는다. 따라서 내용이 화면보다 작으면 스크롤 없이 전부 보이고, 넘칠 때만 박스
       한도에서 리스트가 스크롤된다(예전엔 85% 고정 상한이라 공간이 남아도 잘렸다).
       프롬프트 히스토리/REC/토큰 팝업이 InfoScreen 을 공유하므로 모두 적용된다. */
    #info { width: 100%; height: auto; }
    #info ListItem { height: auto; }
    #info ListItem Label { width: 1fr; }
    """

    # 방향키(내비게이션) — 닫지 않고 ListView 선택을 옮긴다.
    _NAV_KEYS = ("up", "down", "pageup", "pagedown", "home", "end")

    def __init__(self, lines, title="info", hide_key=None, hide_cb=None,
                 initial_index=None, max_width=None, wrap_hang=False):
        super().__init__()
        self._lines = lines
        self._title = title
        # 특정 키(hide_key)를 누르면 hide_cb 를 부르고 닫는다(#6 ② '이 헤더 숨기기').
        self._hide_key = hide_key
        self._hide_cb = hide_cb
        # 열 때 강조(선택)할 항목 인덱스(예: 프롬프트 히스토리의 최신 항목, #17).
        self._initial_index = initial_index
        # 박스 가로 최대폭 오버라이드(없으면 CSS 기본 66). 프롬프트 히스토리는 넓게(#17).
        self._max_width = max_width
        # §10-A #7: 긴 줄(URL 등)을 박스 폭에 맞춰 행잉-인덴트 하드 줄바꿈한다
        # (번호 정렬 보존). 폭은 마운트 후 측정하므로 call_after_refresh 로 재구성.
        self._wrap_hang = wrap_hang
        # §10-A #8: nav 에서 건너뛸(구분선/빈) 표시 줄 인덱스 — 마지막 항목서 ↓ 시
        # 구분선을 건너뛰어 [h] footer 로 바로 점프.
        self._skip = set()

    def compose(self) -> ComposeResult:
        # markup=False: 임의 텍스트(프롬프트 등)의 대괄호가 마크업으로 사라지지 않게.
        with Vertical(id="infobox"):
            with Horizontal(id="infohead"):
                yield Label(self._title, id="infotitle", markup=False)
                # markup=False: "[x]" 가 Textual 마크업 태그로 해석돼 사라지지
                # 않게(그러면 배경색만 남고 글자가 안 보인다).
                yield Label("[x]", id="infoclose", markup=False)  # 닫기 버튼
            yield ListView(*[ListItem(Label(ln, markup=False))
                             for ln in self._lines] or
                           [ListItem(Label("(없음)"))], id="info")

    @staticmethod
    def _is_skip(line):
        s = (line or "").strip()
        return (not s) or set(s) <= _DIVIDER_CHARS

    def _compute_skip(self, lines):
        return {i for i, ln in enumerate(lines) if self._is_skip(ln)}

    def _select_index(self, lv, idx):
        n = len(lv.children)
        if not n:
            return
        idx = max(0, min(idx, n - 1))
        lv.index = idx
        try:
            lv.scroll_to_widget(lv.children[idx])
        except Exception:
            pass

    def on_mount(self):
        if self._max_width is not None:        # 가로 넓히기(#17)
            self.query_one("#infobox").styles.max_width = self._max_width
        lv = self.query_one(ListView)
        lv.focus()
        if self._wrap_hang:
            # 폭이 정해진 뒤(레이아웃 후) 하드 줄바꿈으로 목록을 다시 만든다.
            self.call_after_refresh(self._rewrap)
            return
        self._skip = self._compute_skip(self._lines)
        if self._initial_index is not None:    # 최신 항목 강조 + 스크롤(#17)
            self._select_index(lv, self._initial_index)

    async def _rewrap(self):
        """박스 실제 폭을 재서 각 줄을 행잉-인덴트로 하드 줄바꿈하고 목록을 재구성
        (§10-A #7). 원래 줄→첫 표시줄 매핑으로 initial_index 도 옮긴다."""
        lv = self.query_one(ListView)
        w = self.query_one("#infobox").size.width or (self._max_width or 66)
        inner = max(20, w - 4 - 2)             # 테두리(2)+패딩(2)+스크롤바(2)
        disp, orig_to_disp = [], []
        for ln in self._lines:
            orig_to_disp.append(len(disp))
            disp.extend(_hangwrap(ln, inner))
        self._skip = self._compute_skip(disp)
        await lv.clear()
        await lv.extend([ListItem(Label(d, markup=False)) for d in disp]
                        or [ListItem(Label("(없음)"))])
        if self._initial_index is not None and orig_to_disp:
            oi = max(0, min(self._initial_index, len(orig_to_disp) - 1))
            self._select_index(lv, orig_to_disp[oi])

    def _skip_over(self, lv, step):
        """현재 선택이 건너뛸 줄(구분선/빈)이면 step 방향의 가장 가까운 실제 줄로
        옮긴다. 그 방향에 실제 줄이 없으면 그대로 둔다(반대로 넘어가지 않음)."""
        n = len(lv.children)
        j = lv.index
        if j is None:
            return
        while 0 <= j < n and j in self._skip:
            j += step
        if 0 <= j < n:
            lv.index = j

    def on_click(self, event: events.Click):
        # 조상 체인을 거슬러: 닫기 [x] → 닫음. 박스(#infobox) 안이면 유지, 바깥
        # (백드롭) 클릭이면 닫음(§10 #13 — REC/프롬프트 히스토리/토큰 팝업 공통).
        w = getattr(event, "widget", None)
        inside_box = False
        while w is not None:
            wid = getattr(w, "id", None)
            if wid == "infoclose":
                event.stop()
                self.dismiss(None)
                return
            if wid == "infobox":
                inside_box = True
            w = w.parent
        if not inside_box:        # 박스 바깥/백드롭 클릭 → 닫기
            event.stop()
            self.dismiss(None)

    def on_key(self, event: events.Key):
        event.stop()
        # 방향키는 닫지 말고 히스토리 내비게이션에 쓴다(팝업이 바로 닫히던 버그).
        if event.key in self._NAV_KEYS:
            lv = self.query_one(ListView)
            n = len(lv.children)
            # 이동 후 구분선/빈 줄은 같은 방향으로 건너뛴다(§10-A #8: ↓ → [h] 점프).
            if event.key == "up":
                lv.action_cursor_up()
                self._skip_over(lv, -1)
            elif event.key == "down":
                lv.action_cursor_down()
                self._skip_over(lv, 1)
            elif event.key == "pageup":
                for _ in range(5):
                    lv.action_cursor_up()
                self._skip_over(lv, -1)
            elif event.key == "pagedown":
                for _ in range(5):
                    lv.action_cursor_down()
                self._skip_over(lv, 1)
            elif event.key == "home" and n:
                lv.index = 0
                self._skip_over(lv, 1)
            elif event.key == "end" and n:
                lv.index = n - 1
                self._skip_over(lv, -1)
            return
        if self._hide_key and event.key == self._hide_key and self._hide_cb:
            self._hide_cb()
        self.dismiss(None)

class InfoTabsScreen(ModalScreen):
    """탭으로 나뉜 읽기전용 정보 팝업(#10). 하단 상태줄의 두 버튼(REC 출력 캡처 ·
    토큰 사용량)이 **한 팝업**을 열되 서로 다른 탭을 펴도록 통합한 것. ←→ 로 탭
    전환, ↑↓ 항목 이동, Esc/[x]/바깥클릭 닫기. 높이·닫기 동작은 InfoScreen 과 동일
    (공간이 넉넉하면 한 번에 표시, 화면의 95%까지)."""
    CSS = """
    InfoTabsScreen { align: center middle; background: $background 80%; }
    /* 더 크게(#24): 폭은 넓게, 내용이 적어도 일정 높이를 확보(min-height)해 작게
       쪼그라들지 않게 한다. 내용이 많으면 화면의 95%까지 자란다. */
    #itbox { width: 92%; max-width: 100; height: auto;
             min-height: 14; max-height: 95%;
             border: round $accent; background: $panel; padding: 0 1; }
    /* 탭 그룹을 별도 외곽선으로 감싼다(#32) → head 는 3행. [x] 는 우측 끝(1fr 스페이서). */
    #ithead { width: 100%; height: 3; }
    #ittabs { width: auto; height: 3; border: round $accent; }
    #ittabs Label { width: auto; height: 1; }   /* 탭 하나(클릭 대상) */
    #itgap { width: 1fr; height: 3; }
    /* 닫기 [x] 는 1행으로 우측 위 모서리에 붙인다(#30 — 3행 블록 아님). 가로 흐름의
       마지막(1fr 스페이서 뒤)이라 우측 끝, 높이 1이라 3행 head 의 첫 행에 놓인다. */
    #itclose { width: 5; height: 1; content-align: center middle;
               background: $error; color: $text; text-style: bold; }
    /* ←→ 로 [x] 에 포커스가 오면 강조(탭과 동일한 키보드 동선). */
    #itclose.-focus { background: $warning; color: black; text-style: bold; }
    #itbody { width: 100%; height: auto; }
    #itbody ListItem { height: auto; }
    #itbody ListItem Label { width: 1fr; }
    /* 하단 닫기 버튼(§10-A #6): 목록 아래 한 줄, 가로 가득·가운데 정렬. 클릭/터치로
       닫는다(상단 [x] 와 별개로 좁은 화면·긴 목록에서 손이 닿는 곳에 둠). */
    #itclosebtn { width: 100%; height: 1; margin-top: 1;
                  content-align: center middle; text-style: bold;
                  background: $panel-darken-2; color: $text; }
    #itclosebtn:hover { background: $error; }
    """
    _NAV = ("up", "down", "pageup", "pagedown", "home", "end")

    def __init__(self, tabs, initial=0, title="정보", actions=None):
        super().__init__()
        self._tabs = tabs              # [(탭이름, [줄, ...]), ...]
        self._ti = max(0, min(initial, len(tabs) - 1)) if tabs else 0
        # ←→ 포커스 위치: 0..N-1=탭, N=닫기[x]. 초기엔 현재 탭.
        self._sel = self._ti
        self._title = title
        # {탭인덱스: (키, 힌트, 콜백)} — 그 탭에서 키를 누르면 콜백 실행. 콜백이
        # 줄 리스트를 돌려주면 그 탭 내용을 갱신한다(예: REC 캡처 ON/OFF 토글).
        self._actions = actions or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="itbox"):
            with Horizontal(id="ithead"):
                with Horizontal(id="ittabs"):     # 탭 그룹(외곽선, #32)
                    for i, (name, _l) in enumerate(self._tabs):
                        yield Label("", id=f"ittab_{i}", markup=True)
                yield Label("", id="itgap")        # [x] 를 우측 끝으로 미는 여백
                yield Label("[x]", id="itclose", markup=False)
            yield ListView(id="itbody")
            yield Label("닫기", id="itclosebtn", markup=False)  # 하단 닫기(§10-A #6)

    async def on_mount(self):
        await self._render_tab()
        self.query_one(ListView).focus()

    def _render_tabbar(self):
        # 각 탭 라벨 갱신(현재 탭 강조). 클릭 대상이라 탭마다 별도 위젯이다(#32).
        # ←→ 포커스가 [x](=N)면 보고 있는 탭은 [b] 로, [x] 는 -focus 로 강조한다.
        n = len(self._tabs)
        for i, (name, _l) in enumerate(self._tabs):
            lbl = self.query_one(f"#ittab_{i}", Label)
            if i == self._ti:
                lbl.update(f"[reverse b] {name} [/]" if self._sel == i
                           else f"[b] {name} [/]")
            else:
                lbl.update(f"[dim] {name} [/]")
        self.query_one("#itclose", Label).set_class(self._sel == n, "-focus")

    async def _render_tab(self):
        self._render_tabbar()
        lines = self._tabs[self._ti][1] if self._tabs else []
        lv = self.query_one(ListView)
        await lv.clear()
        await lv.extend([ListItem(Label(ln, markup=False))
                         for ln in (lines or ["(없음)"])])
        if lines:
            lv.index = 0
        box = self.query_one("#itbox", Vertical)
        sub = "←→ 탭·닫기[x] · ↑↓ 항목 · Enter/Esc 닫기"
        act = self._actions.get(self._ti)
        if act:                         # 이 탭에서 가능한 동작(예: [c] 캡처 토글)
            sub = f"{act[1]} · " + sub
        box.border_subtitle = sub

    async def _switch_to(self, i):
        if 0 <= i < len(self._tabs) and i != self._ti:
            self._ti = i
            self._sel = i
            await self._render_tab()

    async def on_click(self, event: events.Click):
        w = getattr(event, "widget", None)
        inside = False
        while w is not None:
            wid = getattr(w, "id", None)
            if wid in ("itclose", "itclosebtn"):   # 상단 [x] / 하단 닫기 버튼
                event.stop(); self.dismiss(None); return
            if wid and wid.startswith("ittab_"):   # 탭 클릭 → 전환(#32)
                event.stop()
                await self._switch_to(int(wid.split("_")[1]))
                return
            if wid == "itbox":
                inside = True
            w = w.parent
        if not inside:
            event.stop(); self.dismiss(None)

    async def on_key(self, event: events.Key):
        event.stop()
        if event.key == "escape":
            self.dismiss(None)
            return
        # 현재 탭에 동작이 걸려 있고 그 키면 콜백 실행 후 내용 갱신(예: 캡처 토글).
        act = self._actions.get(self._ti)
        if act and event.key == act[0]:
            new_lines = act[2]()
            if new_lines is not None:
                self._tabs[self._ti] = (self._tabs[self._ti][0], new_lines)
            await self._render_tab()
            return
        # ←→(또는 Tab/Shift+Tab)으로 탭 + 닫기[x] 를 순환 포커스(요청). 위치는
        # 0..N-1=탭, N=닫기[x]. 탭에 오면 그 탭 내용으로 전환, [x] 에 오면 내용은
        # 그대로 두고 [x] 만 강조한다([x] 에서 Enter/그 외 키로 닫힘). ListView 가
        # 좌우키를 먹는 환경을 대비해 Tab 도 받는다.
        if (event.key in ("left", "right", "tab", "shift+tab")
                and self._tabs):
            n = len(self._tabs)
            step = -1 if event.key in ("left", "shift+tab") else 1
            self._sel = (self._sel + step) % (n + 1)
            if self._sel < n:
                self._ti = self._sel
                await self._render_tab()
            else:
                self._render_tabbar()   # [x] 포커스: 내용 유지, 탭바/[x]만 갱신
            return
        # 닫기[x] 에 포커스가 있을 때 Enter/Space → 닫기(명시적; 그 외 키도 아래에서 닫힘).
        if event.key in ("enter", "space") and self._sel == len(self._tabs):
            self.dismiss(None)
            return
        if event.key in self._NAV:
            lv = self.query_one(ListView)
            if event.key == "up":
                lv.action_cursor_up()
            elif event.key == "down":
                lv.action_cursor_down()
            elif event.key == "pageup":
                for _ in range(5):
                    lv.action_cursor_up()
            elif event.key == "pagedown":
                for _ in range(5):
                    lv.action_cursor_down()
            elif event.key == "home" and len(lv.children):
                lv.index = 0
            elif event.key == "end" and len(lv.children):
                lv.index = len(lv.children) - 1
            return
        # 그 외 키 → 닫기(InfoScreen 과 동일한 가벼운 닫힘)
        self.dismiss(None)

class RulesEditScreen(ModalScreen):
    """Claude 시작 규칙 편집 팝업(#27). 멀티라인 에디터에 '항상 지킬 규칙'을 적고
    Ctrl+S 로 저장(dismiss=텍스트), Esc 로 취소(dismiss=None). 저장된 규칙은 새
    Claude 세션/clear 후 프롬프트에 자동 주입된다(빈값이면 주입 안 함)."""
    CSS = """
    RulesEditScreen { align: center middle; background: $background 80%; }
    #rulesbox { width: 90%; max-width: 100; height: auto; max-height: 90%;
                border: round $accent; background: $panel; padding: 0 1; }
    /* 헤더: 타이틀(1fr) + 우측 닫기 [x]. */
    #ruleshead { width: 100%; height: 1; }
    #rulestitle { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #rulesclose { width: 5; height: 1; content-align: center middle;
                  background: $error; color: $text; text-style: bold; }
    /* 타이틀과 에디터 사이 한 줄(여백). */
    #rulesspacer { width: 100%; height: 1; }
    #rulesedit { width: 100%; height: auto; min-height: 8; max-height: 70%; }
    /* 하단 저장/취소 버튼(에디터와 한 줄 띄움, 우측 정렬). */
    #rulesbtns { width: 100%; height: 1; margin-top: 1; align-horizontal: right; }
    #rulesbtns Label { width: auto; height: 1; padding: 0 2; margin-left: 2;
                       text-style: bold; }
    #rulessave { background: $success; color: $text; }
    #rulescancel { background: $panel-darken-2; color: $text; }
    """

    def __init__(self, text=""):
        super().__init__()
        self._text = text or ""

    def compose(self) -> ComposeResult:
        with Vertical(id="rulesbox"):
            with Horizontal(id="ruleshead"):
                yield Label("Claude 시작 규칙 — Ctrl+S 저장 · Esc 취소",
                            id="rulestitle")
                # markup=False: "[x]" 가 마크업 태그로 사라지지 않게.
                yield Label("[x]", id="rulesclose", markup=False)  # 닫기 버튼
            yield Label("", id="rulesspacer")        # 타이틀↔에디터 한 줄 여백
            yield TextArea(self._text, id="rulesedit")
            with Horizontal(id="rulesbtns"):
                yield Label("저장", id="rulessave")
                yield Label("취소", id="rulescancel")

    def on_mount(self):
        ta = self.query_one(TextArea)
        ta.focus()

    def on_click(self, event: events.Click):
        # 닫기 [x]/취소 → 취소(None), 저장 → 텍스트 반환. 그 외(에디터 등) 유지.
        w = getattr(event, "widget", None)
        while w is not None:
            wid = getattr(w, "id", None)
            if wid in ("rulesclose", "rulescancel"):
                event.stop(); self.dismiss(None); return
            if wid == "rulessave":
                event.stop(); self.dismiss(self.query_one(TextArea).text); return
            w = w.parent

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key == "ctrl+s":
            event.stop()
            self.dismiss(self.query_one(TextArea).text)


class TokenLogScreen(ModalScreen):
    """토큰 사용량 영속 로그 집계 팝업(#7). 서버가 보낸 레코드를 클라이언트가
    usagelog 로 시간/일/주/월 × 계정(=클라이언트) 집계한다. [h]시간 [d]일 [w]주
    [m]월 버킷 전환, [a] 계정 필터 순환, 방향키 스크롤, 그 외/Esc 닫기(라운드트립
    없이 전환)."""
    CSS = """
    TokenLogScreen { align: center middle; }
    /* 높이 고정(2026-06-07 사용자 요청): 내용(레코드 수)에 따라 박스가 줄거나
       출렁이지 않게 **고정 높이**로 둔다. 표(DataTable)는 1fr 로 남는 높이를 채운다. */
    #tklogbox { width: 96%; max-width: 86; height: 76%;
                border: round $accent; background: $panel; padding: 0 1; }
    #tkloghead { width: 100%; height: 1; }
    #tklogtitle { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #tklogclose { width: 5; height: 1; content-align: center middle;
                  background: $error; color: $text; text-style: bold; }
    /* 마우스로 누르는 서브탭(버킷)+버튼 행. */
    #tktabs { width: 100%; height: 1; }
    #tktabs Label { height: 1; padding: 0 1; margin: 0 1 0 0; }
    .tkbtab { background: $surface; color: $text-muted; }
    .tkbtab-active { background: $accent; color: $text; text-style: bold; }
    .tkbbtn { background: $primary-darken-2; color: $text; }
    /* 스코프/한도(위)·키 안내(아래)는 옅게 1~몇 줄, 표가 남는 높이를 채운다. */
    #tktop { width: 100%; height: auto; color: $text-muted; }
    #tkhint { width: 100%; height: 1; color: $text-muted; }
    #tktable { width: 100%; height: 1fr; }
    """
    _NAV_KEYS = ("up", "down", "pageup", "pagedown", "home", "end")
    _BUCKETS = {"h": "hour", "d": "day", "w": "week", "m": "month"}
    # 서브탭 위젯 id → 버킷.
    _TAB_BUCKET = {"tab_hour": "hour", "tab_day": "day",
                   "tab_week": "week", "tab_month": "month"}
    # 탭 라벨(넓은 폭, 좁은 폭). 좁으면 한 글자로 줄여 모바일에서 탭 줄이 안 넘치게(P6).
    _TAB_LABELS = {
        "tab_hour": ("시간", "시"), "tab_day": ("일", "일"),
        "tab_week": ("주", "주"), "tab_month": ("월", "월"),
        "tab_acct": ("계정", "계"), "tab_panel": ("패널", "패"),
        "tab_order": ("정렬", "정"), "tab_usage": ("/usage", "U"),
        "tab_saver": ("시나리오", "S"),
    }
    # [패널]/계정 그룹이 많을 때 상위 N + '기타'로 접어 길이 폭주를 막는다(설계 §4).
    _GROUP_TOP = 8

    def __init__(self, records, usage=None):
        super().__init__()
        self._records = records or []
        self._usage = usage          # M19 그림자 /usage 한도(dict|None)
        self._bucket = "day"
        # 그룹 차원: "account"=계정별(기본), "session"=세션별([패널] 탭). 패널은
        # 닫히고 재사용되므로 안정적 세션 id 로 묶는다(설계 §8, 사용자 결정).
        self._dim = "account"
        # 버킷(시간축) 정렬: "time"=최근 위(기본), "tokens"=많이 쓴 순([o] 토글).
        self._order = "time"
        # 계정 필터 순환 목록: None(전체) + 등장 계정들(정렬)
        seen = []
        for r in self._records:
            a = r.get("account") or usagelog.UNKNOWN
            if a not in seen:
                seen.append(a)
        self._accounts = [None] + sorted(seen)
        self._ai = 0

    @property
    def _account(self):
        return self._accounts[self._ai]

    def compose(self) -> ComposeResult:
        with Vertical(id="tklogbox"):
            with Horizontal(id="tkloghead"):
                yield Label("토큰 사용량", id="tklogtitle")
                # markup=False: "[x]" 가 마크업 태그로 사라지지 않게(배경색만
                # 남고 X 가 안 보이던 버그).
                yield Label("[x]", id="tklogclose", markup=False)  # 닫기 버튼
            # 마우스로 누르는 서브탭(시간/일/주/월) + 동작 버튼.
            with Horizontal(id="tktabs"):
                yield Label("시간", id="tab_hour", classes="tkbtab", markup=False)
                yield Label("일", id="tab_day", classes="tkbtab", markup=False)
                yield Label("주", id="tab_week", classes="tkbtab", markup=False)
                yield Label("월", id="tab_month", classes="tkbtab", markup=False)
                yield Label("계정", id="tab_acct", classes="tkbbtn", markup=False)
                yield Label("패널", id="tab_panel", classes="tkbbtn", markup=False)
                yield Label("정렬", id="tab_order", classes="tkbbtn", markup=False)
                yield Label("/usage", id="tab_usage", classes="tkbbtn",
                            markup=False)
                yield Label("시나리오", id="tab_saver", classes="tkbbtn",
                            markup=False)
            yield Static("", id="tktop", markup=False)
            table = DataTable(id="tktable", zebra_stripes=True,
                              cursor_type="row")
            table.can_focus = True
            yield table
            yield Static("", id="tkhint", markup=False)

    async def on_mount(self):
        # status 훅이 새 /usage 결과를 이 화면에 밀어넣게 등록(M19). 자동 조회는 안
        # 한다(매번 열 때 숨은 claude 기동은 과함) — 마지막 결과를 보여주고, 갱신은
        # [/usage] 버튼/`claude-usage` 명령으로 명시 트리거한다.
        self.app._token_log_screen = self
        await self._refresh()
        self.query_one(DataTable).focus()

    def on_unmount(self):
        if getattr(self.app, "_token_log_screen", None) is self:
            self.app._token_log_screen = None

    def update_usage(self, usage):
        """클라 status 훅이 새 /usage 결과를 전달하면 갱신·재그린다(M19)."""
        if usage and usage != self._usage:
            self._usage = usage
            self.run_worker(self._refresh())

    def _usage_lines(self):
        """M19 그림자 /usage 한도를 표시 줄로(없으면 [u] 안내 1줄)."""
        u = self._usage
        if not isinstance(u, dict):
            return ["── Claude 한도(/usage): [/usage] 눌러 조회 ──"]
        out = ["── Claude 한도(/usage 실측) ──"]
        labels = [("session", "세션(5h)"), ("week_all", "주간(전모델)"),
                  ("week_sonnet", "주간(Sonnet)")]
        for key, name in labels:
            d = u.get(key)
            if isinstance(d, dict) and d.get("pct") is not None:
                rs = d.get("reset")
                out.append(f"  {name}: {d['pct']}% 사용"
                           + (f" · 리셋 {rs}" if rs else ""))
        return out

    def _sync_tabs(self):
        """탭 라벨(폭 반응형)·활성 버킷·활성 그룹차원([패널])·정렬([정렬]) 강조."""
        try:
            narrow = self.app.size.width < 64
        except Exception:
            narrow = False
        for tid, (full, short) in self._TAB_LABELS.items():
            try:
                self.query_one("#" + tid, Label).update(short if narrow else full)
            except Exception:
                pass
        for tid, bucket in self._TAB_BUCKET.items():
            try:
                lab = self.query_one("#" + tid, Label)
            except Exception:
                continue
            lab.set_class(bucket == self._bucket, "tkbtab-active")
        # [패널] 탭은 세션 차원일 때, [정렬] 탭은 토큰순일 때 강조.
        try:
            self.query_one("#tab_panel", Label).set_class(
                self._dim == "session", "tkbtab-active")
            self.query_one("#tab_order", Label).set_class(
                self._order == "tokens", "tkbtab-active")
        except Exception:
            pass

    def _metrics(self):
        """현재 폭 티어로 (라벨 셀폭, 막대 칸수)를 정한다(반응형). 좁으면 막대 생략."""
        try:
            w = self.app.size.width
        except Exception:
            w = 0
        if w >= 80:
            return 28, 16
        if w >= 60:
            return 22, 10
        if w >= 44:
            return 16, 6
        return 12, 0

    @staticmethod
    def _trunc(s, cells):
        """문자열을 셀폭 cells 로 자른다(한글 2셀 고려, 넘치면 … 말줄임)."""
        if cells <= 0:
            return ""
        used, out = 0, []
        for ch in s:
            cw = _char_cells(ch)
            if used + cw > cells:
                while out and used + 1 > cells:
                    used -= _char_cells(out[-1])
                    out.pop()
                out.append("…")
                return "".join(out)
            out.append(ch)
            used += cw
        return "".join(out)

    def _barcell(self, tok, vmax, pct, cells):
        """막대+% 셀 문자열. cells<=0 이면 % 만(좁은 폭)."""
        if cells <= 0:
            return f"{pct}%"
        b = usagelog.bar(tok, vmax, cells)
        return f"{b:<{cells}} {pct:>3}%"

    async def _refresh(self):
        self._sync_tabs()
        table = self.query_one(DataTable)
        table.clear(columns=True)
        label_w, bar_cells = self._metrics()
        v = usagelog.agg_view(self._records, self._bucket, self._account,
                              self._dim, self._order, top=self._GROUP_TOP)
        dimname = "세션" if self._dim == "session" else "계정"
        # 컬럼: 항목 | 토큰(우측) | 비율(막대+%)
        table.add_column("항목", key="label", width=label_w)
        table.add_column(Text("토큰", justify="right"), key="tok", width=8)
        table.add_column("비율", key="bar", width=bar_cells + 5)

        def add(label, tok, pct, vmax):
            table.add_row(self._trunc(label, label_w),
                          Text(usagelog._fmt_tokens(tok), justify="right"),
                          self._barcell(tok, vmax, pct, bar_cells))

        if not self._records or v["total"] == 0:
            table.add_row("(기록된 토큰 사용량이 없습니다)", "", "")
        else:
            # 그룹(계정/세션)이 2개 이상일 때만 그룹별 총합 + 구분선(단일이면 중복이라 생략).
            if v["multi"]:
                for label, tok, pct in v["groups"]:
                    add(label, tok, pct, v["gmax"])
                table.add_row(f"── 시간({self._bucket}) ──", "", "")
            for label, tok, pct in v["buckets"]:
                add(label, tok, pct, v["bmax"])

        # 제목: 묶음·버킷·정렬·전체 합. 스코프/한도(위)·키 안내(아래)는 분리.
        order_l = "토큰순" if self._order == "tokens" else "시간순"
        self.query_one("#tklogtitle", Label).update(
            f"토큰 사용량 · {self._bucket} · {dimname}별")
        acct = self._account if self._account is not None else "전체"
        top = self._usage_lines() + [
            f"계정 {acct} · 묶음 {dimname} · 정렬 {order_l} · 전체 "
            f"Σ{usagelog._fmt_tokens(v['total'])}"]
        self.query_one("#tktop", Static).update("\n".join(top))
        self.query_one("#tkhint", Static).update(
            "h시간 d일 w주 m월 · a계정 p패널 o정렬 · u/usage s시나리오 · Esc닫기")

    def on_click(self, event: events.Click):
        # 마우스 클릭: 닫기 [x]·서브탭(버킷)·동작 버튼을 위젯 id 로 분기한다.
        w = getattr(event, "widget", None)
        wid = None
        while w is not None:
            wid = getattr(w, "id", None)
            if wid:
                break
            w = w.parent
        if wid == "tklogclose":
            event.stop()
            self.dismiss(None)
        elif wid in self._TAB_BUCKET:
            event.stop()
            self._bucket = self._TAB_BUCKET[wid]
            self.run_worker(self._refresh())
        elif wid == "tab_acct":
            event.stop()
            if len(self._accounts) > 1:
                self._ai = (self._ai + 1) % len(self._accounts)
                self.run_worker(self._refresh())
        elif wid == "tab_panel":
            event.stop()
            # 그룹 차원 토글: 계정별 ↔ 세션별([패널]).
            self._dim = "account" if self._dim == "session" else "session"
            self.run_worker(self._refresh())
        elif wid == "tab_order":
            event.stop()
            # 버킷 정렬 토글: 시간순 ↔ 토큰순.
            self._order = "tokens" if self._order == "time" else "time"
            self.run_worker(self._refresh())
        elif wid == "tab_usage":
            event.stop()
            self.app.send_cmd("refresh_usage")
            self.query_one("#tklogtitle", Label).update("/usage 조회 중… (~수초)")
        elif wid == "tab_saver":
            event.stop()
            self.app.open_claude_saver()

    async def on_key(self, event: events.Key):
        k = event.key
        if k in self._BUCKETS:
            event.stop()
            self._bucket = self._BUCKETS[k]
            await self._refresh()
            return
        if k == "a" and len(self._accounts) > 1:
            event.stop()
            self._ai = (self._ai + 1) % len(self._accounts)
            await self._refresh()
            return
        if k == "p":
            event.stop()
            # 그룹 차원 토글: 계정별 ↔ 세션별([패널]).
            self._dim = "account" if self._dim == "session" else "session"
            await self._refresh()
            return
        if k == "o":
            event.stop()
            # 버킷 정렬 토글: 시간순 ↔ 토큰순.
            self._order = "tokens" if self._order == "time" else "time"
            await self._refresh()
            return
        if k == "s":
            event.stop()
            # M18-C: 사용량 통계에서 바로 과사용 완화 시나리오 on/off 로(§9.4).
            self.app.open_claude_saver()
            return
        if k == "u":
            event.stop()
            # M19: 그림자 /usage 갱신 요청. 결과는 status 로 와 다음 열람부터 반영.
            self.app.send_cmd("refresh_usage")
            self.query_one("#tklogtitle", Label).update("/usage 조회 중… (~수초)")
            return
        if k in self._NAV_KEYS:
            # 스크롤/커서는 포커스된 DataTable 이 자체 처리 — 가로채지 않는다.
            return
        if k == "escape":
            event.stop()
            self.dismiss(None)
            return
        # 그 외 키는 닫는다(기존 동작).
        event.stop()
        self.dismiss(None)

class PromptScreen(ModalScreen):
    """명령/이름변경/검색 등 한 줄 입력을 받는 바닥 고정 모달.
    Textual Input 을 별도 스크린(모달)에 담아 포커스 문제를 피한다."""
    CSS = """
    PromptScreen { align: center bottom; background: $background 80%; }
    /* §10: command 입력 줄(esc :)을 외곽선(테두리)으로 감싼다. 테두리가 위·아래
       2행을 더 쓰므로 height:3, dock:bottom 으로 테두리 포함 박스가 바닥에 붙는다.
       ':' 프리픽스·입력은 테두리 안쪽 한 행에 들어간다(아래 compose 의 #prow).
       rename/search 등 다른 용도는 #prow 를 안 쓰는 bare Input 이라 영향 없음. */
    /* 후보 영역(#pcand)을 입력 박스(#prow) **위쪽**에 확실히 두려고 둘을 바닥
       고정 Vertical(#pwrap)로 묶고 후보를 먼저 둔다 — dock:bottom 끼리의 적층
       순서가 Textual 버전에 따라 뒤집힐 수 있어(모바일서 후보가 박스 아래로 가
       키보드에 가려짐), 컨테이너 정상 흐름으로 순서를 못박는다(§10 사용자 요청). */
    #pwrap { dock: bottom; width: 100%; height: auto; }
    #prow { width: 100%; height: 3; background: $surface;
            border: round $accent; }
    #pprefix { width: 2; height: 1; color: $accent; text-style: bold;
               background: $surface; }
    #pinput { width: 1fr; border: none; height: 1; padding: 0;
              background: $surface; color: $text; }
    /* 입력 오른쪽에 붙는 힌트: 명령 설명 / 인자 밑줄(____) / 토글 선택지. 입력칸
       (1fr)이 남는 폭을 채우므로 힌트는 항상 줄 오른쪽 끝에 표시된다(명령 오른쪽). */
    #phint { width: auto; max-width: 60%; height: 1; padding: 0 1;
             background: $surface; content-align: right middle; }
    /* 입력 박스 위에 펼쳐지는 자동완성 후보 영역(부분일치 명령). */
    #pcand { width: 100%; height: auto; max-height: 12;
             background: $panel; color: $text; padding: 0 1;
             border: round $accent; }
    """

    # 후보 영역에 한 번에 보여줄 최대 명령 수.
    MAX_CAND = 12

    def __init__(self, purpose, label, initial, suggester):
        super().__init__()
        self._purpose = purpose
        self._label = label
        self._initial = initial
        self._suggester = suggester
        self._cand = []        # 현재 부분일치 후보 [(name, desc), ...]
        self._sel = 0          # 후보 영역 내 선택 인덱스
        self._cand_shown = False
        # 입력 오른쪽 힌트(완성된 명령일 때): 토글/선택지 모드 상태.
        self._hint_cmd = None      # 완성된 명령 이름(아니면 None)
        self._choices = []         # 토글/선택지 [(보임, 값), ...] (없으면 빈 리스트)
        self._choice_sel = 0       # 강조된 선택지 인덱스
        self._hint_text = ""       # 현재 힌트 원문(마크업 포함, 상태 확인용)

    def compose(self) -> ComposeResult:
        # 명령 프롬프트는 첫 토큰(명령어) 배경을 옅게 칠해 인자와 구분한다.
        hl = None
        if self._purpose == "command":
            hl = _CommandWordHighlighter(f"on {theme_color(self, 'primary-darken-3')}")
        inp = Input(value=self._initial, placeholder=self._label,
                    suggester=self._suggester, id="pinput", highlighter=hl)
        if self._purpose == "command":
            # 바닥 고정 컨테이너에 후보(위) → 입력 박스(아래) 순으로 둬, 자동완성
            # 후보가 항상 입력 박스 위쪽에 펼쳐지게 한다(모바일 키보드에 안 가림).
            with Vertical(id="pwrap"):
                yield Label("", id="pcand", markup=True)
                # 맨 왼쪽 고정 ':' 프리픽스(별도 위젯이라 백스페이스로 안 지워짐)
                with Horizontal(id="prow"):
                    yield Label(":", id="pprefix")
                    yield inp
                    # 입력 오른쪽 힌트(설명/인자 밑줄/토글 선택지). markup 으로 강조.
                    yield Label("", id="phint", markup=True)
        else:
            inp.styles.dock = "bottom"
            inp.styles.padding = (0, 1)
            yield inp

    def on_mount(self):
        inp = self.query_one(Input)
        inp.focus()
        inp.cursor_position = len(inp.value)
        if self._purpose == "command":
            self.query_one("#pcand", Label).display = False
            self._refresh_cands()
            self._refresh_hint()

    @staticmethod
    def _esc(s):
        # rich/Textual 마크업으로 해석되지 않게 '[' 를 이스케이프.
        return s.replace("[", r"\[")

    def _refresh_cands(self):
        """입력의 첫 토큰(명령 이름)으로 부분일치 후보를 재계산하고 후보 영역을
        갱신한다. 토큰에 공백이 생기면(= 옵션 입력 단계) 후보를 숨긴다."""
        if self._purpose != "command":
            return
        lbl = self.query_one("#pcand", Label)
        s = self.query_one(Input).value.strip()
        matches = []
        if not s:
            # 빈 명령 프롬프트(esc :) → 전체 명령을 위쪽에 보여준다(↑↓ 탐색, #).
            matches = [(n, d) for (n, d, *_) in COMMANDS]
        elif " " not in s:
            ql = s.lower()
            # 한영 오타 복원: IME 켠 채 친 한글(예: "ㅏㅑㅣㅣ"=kill)을 QWERTY 로
            # 되돌려 그걸로 검색한다(요청). 한글이 섞였을 때만 변환.
            if has_hangul(ql):
                ql = hangul_to_qwerty(ql).lower()
            matches = [(n, d) for (n, d, *_) in COMMANDS if ql in n.lower()]
            # 정확히 한 개이고 그게 입력과 동일하면 더 제안할 게 없음.
            if len(matches) == 1 and matches[0][0].lower() == ql:
                matches = []
        # 전체 목록을 보관(자르지 않음) — _render_cands 가 MAX_CAND 윈도우로 그린다.
        self._cand = matches
        self._sel = 0
        if not self._cand:
            self._cand_shown = False
            lbl.display = False
            return
        self._cand_shown = True
        lbl.display = True
        self._render_cands()

    def _render_cands(self):
        lbl = self.query_one("#pcand", Label)
        n = len(self._cand)
        # 전체 줄 수(후보 + 위/아래 '더 보기' 표시)를 MAX_CAND 안에 맞춘다.
        body = self.MAX_CAND if n <= self.MAX_CAND else self.MAX_CAND - 2
        if n <= body:
            start = 0
        else:                       # _sel 이 보이도록 윈도우를 민다(빈 입력=전체 명령)
            start = max(0, min(self._sel - body // 2, n - body))
        rows = []
        if start > 0:
            rows.append("[dim]  ↑ 더 …[/dim]")
        for i in range(start, min(start + body, n)):
            nm, d = self._cand[i]
            if i == self._sel:
                rows.append(f"[reverse]{self._esc(nm):<20} {self._esc(d)}[/reverse]")
            else:
                rows.append(f"{self._esc(nm):<20} [dim]{self._esc(d)}[/dim]")
        if start + body < n:
            rows.append("[dim]  ↓ 더 …[/dim]")
        lbl.update("\n".join(rows))

    def _accept_cand(self):
        inp = self.query_one(Input)
        name = self._cand[self._sel][0]
        inp.value = name + " "
        inp.cursor_position = len(inp.value)
        inp.focus()
        self._refresh_cands()
        self._refresh_hint()

    @staticmethod
    def _cmd_desc(name):
        for (n, d, *_) in COMMANDS:
            if n == name:
                return d
        return None

    def _set_hint(self, markup):
        # 힌트 텍스트를 갱신하고 원문을 보관(테스트/상태 확인용).
        self._hint_text = markup
        self.query_one("#phint", Label).update(markup)

    def _refresh_hint(self):
        """입력이 **완성된 명령**이면 입력칸 오른쪽 힌트(#phint)에 설명을 띄운다.
        - 선택지/토글 인자(COMMAND_OPTIONS): 방향키로 고르는 토글 UI(↑↓ 강조, Enter 실행).
        - 자유 텍스트 인자(COMMAND_FREETEXT): 인자 자리에 밑줄(____)+설명.
        - 그 외(무인자·인자 입력 중): 설명만. 부분 입력(후보 표시 중)이면 힌트는 비운다."""
        if self._purpose != "command":
            return
        self._hint_cmd = None
        self._choices = []
        s = self.query_one(Input).value
        first = s.split(None, 1)[0] if s.strip() else ""
        rest = s[len(first):].strip()
        name = first.lower()
        desc = self._cmd_desc(name)
        # 후보 영역이 떠 있거나(부분 입력) 완성 명령이 아니면 힌트 비움.
        if self._cand_shown or not desc:
            self._set_hint("")
            return
        self._hint_cmd = name
        opts = COMMAND_OPTIONS.get(name)
        if opts and not rest:
            # 선택지/토글: 방향키 강조 + Enter 즉시 실행(단일 spec 지원).
            self._choices = list(opts[0]["choices"])
            self._choice_sel = 0
            self._render_hint()
            return
        if name in COMMAND_FREETEXT and not rest:
            self._set_hint(f"[bold]____[/bold]  [dim]{self._esc(desc)}[/dim]")
            return
        self._set_hint(f"[dim]{self._esc(desc)}[/dim]")

    def _render_hint(self):
        # 토글/선택지를 한 줄에 나열하고 강조된 항목만 reverse 로 표시(↑↓ 로 이동).
        parts = []
        for i, (disp, _v) in enumerate(self._choices):
            d = self._esc(disp)
            parts.append(f"[reverse] {d} [/reverse]" if i == self._choice_sel
                         else f"[dim] {d} [/dim]")
        self._set_hint("  ".join(parts))

    def _run_choice(self):
        """강조된 토글/선택지로 즉시 실행(Enter). 값이 비면(예: '토글') 인자 생략."""
        name = self._hint_cmd
        val = self._choices[self._choice_sel][1]
        self.dismiss(f"{name} {val}" if val else name)

    def on_input_submitted(self, event):
        # 후보가 떠 있으면 Enter 는 강조된 후보를 입력에 채우고(실행하지 않음),
        # 그 다음 Enter 로 실제 실행한다.
        if self._purpose == "command" and self._cand_shown and self._cand:
            self._accept_cand()
            return
        # 토글/선택지 모드면 강조된 선택지로 바로 실행.
        if self._purpose == "command" and self._choices:
            self._run_choice()
            return
        self.dismiss(event.value)

    def on_input_changed(self, event):
        # 명령 프롬프트에서 '?' 입력 → 명령 목록 선택기
        if self._purpose == "command" and event.value.endswith("?"):
            inp = self.query_one(Input)
            base = event.value[:-1]
            inp.value = base

            def fill(name):
                if name:
                    inp.value = name + " "
                    inp.cursor_position = len(inp.value)
                inp.focus()
            self.app.push_screen(CommandListScreen(COMMANDS, base), fill)
            return
        self._refresh_cands()
        self._refresh_hint()

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
            return
        # 후보 영역이 떠 있을 때만 ↑↓ 로 선택 이동, Tab 으로 채우기.
        if self._purpose == "command" and self._cand_shown and self._cand:
            if event.key == "down":
                event.stop()
                self._sel = (self._sel + 1) % len(self._cand)
                self._render_cands()
            elif event.key == "up":
                event.stop()
                self._sel = (self._sel - 1) % len(self._cand)
                self._render_cands()
            elif event.key == "tab":
                event.stop()
                self._accept_cand()
            return
        # 토글/선택지 모드: ↑↓(과 가능하면 ←→)로 강조 이동, Enter 는 submit 에서 실행.
        # (←→ 는 Input 의 커서 이동 바인딩이 먼저 먹을 수 있어 ↑↓ 를 주 경로로 둔다.)
        if self._purpose == "command" and self._choices:
            if event.key in ("up", "left"):
                event.stop()
                self._choice_sel = (self._choice_sel - 1) % len(self._choices)
                self._render_hint()
            elif event.key in ("down", "right"):
                event.stop()
                self._choice_sel = (self._choice_sel + 1) % len(self._choices)
                self._render_hint()

class ConfirmScreen(ModalScreen):
    """예/아니오 확인 팝업(중앙). 두 버튼을 좌우로 배치하고, 선택된 쪽만
    유채색(강조색)·선택 안 된 쪽은 무채색(회색)으로 그려 헷갈리지 않게 한다.
    ←→ 로 선택 이동, Enter 확정, y/n 단축, Esc(=아니오) 취소, 버튼 터치로
    즉시 확정. 위험한 동작(탭 닫기 등) 확인용."""
    CSS = """
    ConfirmScreen { align: center middle; }
    #confirmbox { width: 48; height: auto; border: round $accent;
                  background: $panel; padding: 1 2; }
    #confirmmsg { width: 100%; height: auto; padding: 0 0 1 0; }
    #confirmopts { width: 100%; height: 3; align: center middle; }
    #confirmopts > Label {           /* 미선택: 무채색(회색) */
        width: 1fr; height: 3; margin: 0 1; content-align: center middle;
        text-style: bold; border: round $panel-lighten-2;
        background: $panel-lighten-1; color: $text-disabled; }
    #confirmopts > Label.sel {       /* 선택: 유채색(강조색) */
        border: round $accent; background: $accent; color: $text; }
    #confirmopts > Label.sel.danger {  /* 위험(종료) 선택: 붉은색 강조 */
        border: round $error; background: $error; color: $text; }
    """

    def __init__(self, message, yes_label="닫기", no_label="취소",
                 title="확인", default_yes=False, danger=False):
        super().__init__()
        self._message = message
        self._yes = yes_label
        self._no = no_label
        self._title = title
        self._danger = danger   # True 면 선택 강조를 $error(붉은색)로
        self._sel = 0 if default_yes else 1   # 0=예 / 1=아니오(기본 '취소')

    def compose(self) -> ComposeResult:
        with Vertical(id="confirmbox"):
            yield Label(self._message, id="confirmmsg")
            with Horizontal(id="confirmopts"):
                yield Label(self._yes, id="cy")
                yield Label(self._no, id="cn")

    def on_mount(self):
        box = self.query_one("#confirmbox", Vertical)
        box.border_title = self._title
        box.border_subtitle = "←→ 이동 · Enter 확정 · y/n · Esc 취소"
        opts = self.query_one("#confirmopts", Horizontal)
        opts.can_focus = True          # 화면이 키 입력을 받도록 포커스 대상 확보
        opts.focus()
        self._refresh()

    def _refresh(self):
        """선택 위치에 따라 강조 클래스(.sel)를 토글한다. danger 면 .danger 도."""
        cy, cn = self.query_one("#cy", Label), self.query_one("#cn", Label)
        cy.set_class(self._sel == 0, "sel")
        cn.set_class(self._sel == 1, "sel")
        cy.set_class(self._danger, "danger")
        cn.set_class(self._danger, "danger")

    def on_click(self, event: events.Click):
        # 버튼 터치/클릭 → 그 선택지로 즉시 확정.
        w = getattr(event, "widget", None)
        while w is not None:
            wid = getattr(w, "id", None)
            if wid == "cy":
                event.stop()
                self.dismiss(True)
                return
            if wid == "cn":
                event.stop()
                self.dismiss(False)
                return
            w = w.parent

    def on_key(self, event: events.Key):
        k = event.key
        if k == "escape":
            event.stop()
            self.dismiss(False)
        elif k in ("y", "Y"):
            event.stop()
            self.dismiss(True)
        elif k in ("n", "N"):
            event.stop()
            self.dismiss(False)
        elif k == "enter":
            event.stop()
            self.dismiss(self._sel == 0)
        elif k in ("left", "right", "tab"):
            event.stop()
            self._sel = 1 - self._sel
            self._refresh()

class ChooseBufferScreen(ModalScreen):
    CSS = """
    ChooseBufferScreen { align: center middle; }
    #buf { width: 64; height: auto; max-height: 80%;
           border: round $accent; background: $panel; }
    """

    def __init__(self, items):
        super().__init__()
        self._items = items

    def compose(self) -> ComposeResult:
        rows = [ListItem(Label(f"{it['i']}: {it['preview']}"), id=f"b{it['i']}")
                for it in self._items] or [ListItem(Label("(버퍼 없음)"), id="bnone")]
        yield ListView(*rows, id="buf")

    def on_mount(self):
        self.query_one(ListView).focus()

    def on_list_view_selected(self, event):
        if event.item.id == "bnone":
            self.dismiss(None)
        else:
            self.dismiss(int(event.item.id[1:]))

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)

class ChooseLayoutScreen(ModalScreen):
    """저장된 레이아웃 슬롯 선택기(방향키 이동, Enter 선택, Esc 취소)."""
    CSS = """
    ChooseLayoutScreen { align: center middle; }
    #lay { width: 56; height: auto; max-height: 80%;
           border: round $accent; background: $panel; }
    """

    def __init__(self, names, title="레이아웃 불러오기"):
        super().__init__()
        self._names = names
        self._title = title

    def compose(self) -> ComposeResult:
        rows = [ListItem(Label(nm), id=f"L{i}")
                for i, nm in enumerate(self._names)] or \
               [ListItem(Label("(저장된 레이아웃 없음)"), id="Lnone")]
        lv = ListView(*rows, id="lay")
        lv.border_title = self._title
        yield lv

    def on_mount(self):
        self.query_one(ListView).focus()

    def on_list_view_selected(self, event):
        if event.item.id == "Lnone":
            self.dismiss(None)
        else:
            self.dismiss(self._names[int(event.item.id[1:])])

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)

class PermModeScreen(ModalScreen):
    """Claude 권한모드 선택 팝업(하단 footer 클릭, §10 item 2). 현재 모드를 표시
    하고 목표 모드를 고르면 그 키를 dismiss → 서버가 shift+tab 폐루프로 목표까지
    순환 주입한다. bypass(권한 우회)는 위험 모드라 목록에서 제외(실수 방지).

    배치(§10-A #2): 클릭한 footer('auto mode on …') 줄 **바로 위**에, 그리고
    **좌측 정렬**(footer 가 시작하는 패널 왼쪽 x 에 맞춤)로 띄운다. 그래서 팝업이
    클릭한 그 줄에 붙어 보인다(화면 중앙이 아님). anchor 가 없으면 화면 중앙."""
    CSS = """
    PermModeScreen { align: left top; }
    #perm { width: 60; height: auto; max-height: 80%;
            border: round $accent; background: $panel; }
    #perm ListItem Label { width: 1fr; }
    """
    # 박스 폭(CSS #perm width 와 일치) — 좌측 정렬 오프셋/중앙 계산에 쓴다.
    _BOX_W = 60
    _MODES = [
        ("auto", "auto — 편집 자동 수락 (⏵⏵ auto-accept edits)"),
        ("default", "default — 매번 확인 (일반 모드)"),
        ("plan", "plan — 플랜 모드 (계획만, 실행 안 함)"),
    ]

    def __init__(self, current, anchor_y=None, anchor_x=None):
        super().__init__()
        self._current = current
        # 클릭한 footer 행(화면 y). 아래에 공간이 있으면 그 아래, 없으면 위에 띄운다.
        # None 이면 화면 세로 중앙(기존 동작).
        self._anchor_y = anchor_y
        # 클릭한 footer 가 시작하는 화면 x(패널 왼쪽). 좌측 정렬 기준(#2).
        # None 이면 화면 가로 중앙.
        self._anchor_x = anchor_x

    def compose(self) -> ComposeResult:
        items = []
        for key, label in self._MODES:
            mark = "  ◀ 현재" if key == self._current else ""
            items.append(ListItem(Label(label + mark, markup=False),
                                  id=f"M_{key}"))
        lv = ListView(*items, id="perm")
        lv.border_title = f"권한모드 선택 (현재: {self._current or '?'})"
        yield lv

    def on_mount(self):
        lv = self.query_one(ListView)
        lv.focus()
        # 클릭 위치 기준 세로 배치: 아래 공간이 충분하면 클릭 행 바로 아래, 아니면 위.
        box_h = len(self._MODES) + 2          # 테두리 포함 대략 높이
        sh = self.size.height
        if self._anchor_y is None:
            y = max(0, (sh - box_h) // 2)     # 앵커 없으면 중앙
        elif self._anchor_y - box_h >= 0:
            y = self._anchor_y - box_h        # 클릭한 줄 **바로 위**(우선, #29)
        else:
            y = self._anchor_y + 1            # 위 공간이 없을 때만 아래
        # 가로 배치(#2): footer 시작 x 에 좌측 정렬(align:left top 기준 offset).
        # 박스가 화면 오른쪽을 넘지 않게 클램프. 앵커 없으면 가로 중앙.
        sw = self.size.width
        if self._anchor_x is None:
            x = max(0, (sw - self._BOX_W) // 2)
        else:
            x = max(0, min(self._anchor_x, sw - self._BOX_W))
        lv.styles.offset = (x, y)

    def on_list_view_selected(self, event):
        self.dismiss(event.item.id[2:])   # "M_auto" → "auto"

    def on_click(self, event: events.Click):
        # 박스(#perm) 바깥(백드롭) 클릭 → 닫기(§10-A #3). InfoScreen 패턴 재사용.
        w = getattr(event, "widget", None)
        inside = False
        while w is not None:
            if getattr(w, "id", None) == "perm":
                inside = True
                break
            w = w.parent
        if not inside:
            event.stop()
            self.dismiss(None)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
