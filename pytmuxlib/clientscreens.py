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
from textual.widgets import Input, Label, ListItem, ListView

from rich.highlighter import Highlighter

from . import i18n
from .clientutil import (COMMAND_FREETEXT, COMMAND_NOARG, COMMAND_OPTIONS,
                         COMMANDS, MENU_ITEMS, MENU_TOGGLES, PANE_SCOPED_CMDS,
                         _char_cells, bar, format_option_row, has_hangul,
                         hangul_to_qwerty, norm_sep, theme_color)


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
        all_items = []
        for it in items:
            # §6 ③ 표시 시점 번역: 카테고리·설명을 현재 로케일로(코어는 카탈로그,
            # 플러그인 명령은 등록 전이면 default 로 원본 ko 유지).
            rawcat = it[2] if len(it) > 2 else "기타"
            cat = i18n.t(f"cat.{rawcat}", default=rawcat)
            desc = i18n.t(f"cmd.{it[0]}", default=it[1])
            if cat not in bucket:
                bucket[cat] = []
                order.append(cat)
            bucket[cat].append((it[0], desc))
            all_items.append((it[0], desc))
        # 맨 앞에 '전체' 가상 카테고리 — 모든 명령을 등장 순서로 모아 ↑↓ 로 한 탭에서
        # 전부 훑어볼 수 있게 한다(요청). 길면 _CMDS_MAX_ROWS 로 클램프되어 스크롤된다.
        self._all_cats = [(i18n.t("cat.전체", default="전체"), all_items)] \
            + [(c, bucket[c]) for c in order]
        self._ci = 0          # 현재 카테고리 인덱스(기본='전체' → 바로 전부 탐색)
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
            yield Input(placeholder=i18n.t("ui.search"), id="cmdsearch")
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
        # 구분자(공백/언더바/하이픈) 무시 매칭: 검색어·이름을 모두 norm_sep 로
        # 통일해 "rename "·"rename_" 가 "rename-tab" 에 잡히게 한다.
        qs = norm_sep(qn)
        return [(n, d) for n, d in items
                if qs in norm_sep(n.lower()) or q in d.lower()]

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
            await lv.extend([ListItem(Label(
                f"[dim]{i18n.t('screen.no_search_results')}[/]", markup=True))])
        box = self.query_one("#cmdbox", Vertical)
        box.border_title = i18n.t("screen.command_list")
        box.border_subtitle = i18n.t("screen.cmdlist_sub")

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
        lv.border_title = i18n.t("screen.options_title", cmd=self.cmd_name)
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
        return format_option_row(self.opts[i], self.sel[i])

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
        for key, raw in MENU_ITEMS:
            label = i18n.t(f"menu.{key}", default=raw)   # §6 ③ 로케일 번역
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
    """긴 줄을 width(**표시 셀** 기준) 안으로 하드 줄바꿈하되, 줄 앞의 들여쓰기와
    목록 표지('NN.'/'NN)'·•·-·*·→·▷··)만큼 이어줄도 들여써 정렬을 보존한다(요청:
    좁은 폭에서 줄바꿈 시 이전 줄 들여쓰기에 맞춤 / §10-A #7 번호 정렬). 한글 등
    2셀 글자를 고려해 셀폭으로 끊어, char 수로 세던 옛 방식이 한글을 과소 줄바꿈해
    Textual 이 인덴트 없이 다시 soft-wrap 하던 문제를 없앤다. 공백 경계 우선,
    없으면(긴 URL 등) 글자 단위. 짧은 줄은 그대로 [line]."""
    cells = lambda s: sum(_char_cells(c) for c in s)
    if width < 8 or cells(line) <= width:
        return [line]
    # 선행 공백 + (있으면) 목록 표지 한 개를 들여쓰기 폭으로 잡는다.
    m = re.match(r"\s*(?:[-*•·▷→]\s+|\d+[.)]\s+)?", line)
    ind = m.group(0) if m else ""
    indent = " " * min(len(ind), max(0, width // 2))   # 너무 깊으면 절반까지만
    out, cur = [], line
    while cells(cur) > width:
        # 누적 셀폭이 width 를 막 넘는 글자 인덱스(=하드 컷 위치).
        acc, cut = 0, len(cur)
        for i, ch in enumerate(cur):
            acc += _char_cells(ch)
            if acc > width:
                cut = max(i, len(indent) + 1)
                break
        sp = cur.rfind(" ", len(indent) + 1, cut)       # 들여쓰기 뒤 공백 경계
        if sp > len(indent):
            cut = sp
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
    /* 2칼럼 행 모드(프롬프트 히스토리): 번호 칼럼(고정폭)+본문 칼럼(1fr, 자동
       줄바꿈). 본문이 여러 줄이어도 번호 칼럼 아래로 내려가지 않고 본문 칼럼
       안에 정렬돼 머문다. #info .histnum 은 id+class 라 위의 Label 규칙을 이긴다. */
    #info ListItem .histrow { width: 1fr; height: auto; }
    #info .histnum { width: 5; color: $text-muted; text-align: right; }
    #info .histbody { width: 1fr; height: auto; }
    """

    # 방향키(내비게이션) — 닫지 않고 ListView 선택을 옮긴다.
    _NAV_KEYS = ("up", "down", "pageup", "pagedown", "home", "end")

    def __init__(self, lines, title="info", hide_key=None, hide_cb=None,
                 initial_index=None, max_width=None, wrap_hang=True,
                 col_rows=None, select_cb=None):
        super().__init__()
        self._lines = lines
        # 2칼럼 행 모드(프롬프트 히스토리 #17): 각 항목을 [번호칼럼 | 본문칼럼]
        # Horizontal 로 그린다. 본문이 여러 줄(내장 \n)·자동 줄바꿈이어도 전부
        # 본문 칼럼 안에 머물러 번호와 시각적으로 분리된다. 항목은 str(전폭:
        # 구분선/footer) 또는 (번호, 본문) 튜플. 주어지면 wrap_hang/lines 표시 경로
        # 대신 이 경로를 쓴다(번호↔항목 1:1 이라 initial_index 가 곧 ListItem index).
        self._col_rows = col_rows
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
        # §3.8 Stage 2 ②: Enter/행 클릭으로 선택한 항목 인덱스를 콜백에 넘기고 닫는다
        # (프롬프트 히스토리에서 골라 그 위치로 점프). None 이면 읽기전용(아무 키나
        # 닫기) 기존 동작 유지. 콜백이 점프 불가 항목(구분선/footer)을 직접 걸러낸다.
        self._select_cb = select_cb

    @staticmethod
    def _col_item(row):
        """2칼럼 행 모드의 한 항목을 ListItem 으로 만든다. (번호, 본문) 튜플은
        [번호칼럼 | 본문칼럼] Horizontal 로, 문자열(구분선/footer)은 전폭 Label 로."""
        if isinstance(row, (tuple, list)):
            num, body = row
            return ListItem(Horizontal(
                Label(str(num), classes="histnum", markup=False),
                Label(str(body), classes="histbody", markup=False),
                classes="histrow"))
        return ListItem(Label(str(row), markup=False))

    def compose(self) -> ComposeResult:
        # markup=False: 임의 텍스트(프롬프트 등)의 대괄호가 마크업으로 사라지지 않게.
        with Vertical(id="infobox"):
            with Horizontal(id="infohead"):
                yield Label(self._title, id="infotitle", markup=False)
                # markup=False: "[x]" 가 Textual 마크업 태그로 해석돼 사라지지
                # 않게(그러면 배경색만 남고 글자가 안 보인다).
                yield Label("[x]", id="infoclose", markup=False)  # 닫기 버튼
            if self._col_rows is not None:
                yield ListView(*[self._col_item(r) for r in self._col_rows]
                               or [ListItem(Label(i18n.t("screen.empty")))], id="info")
            else:
                yield ListView(*[ListItem(Label(ln, markup=False))
                                 for ln in self._lines] or
                               [ListItem(Label(i18n.t("screen.empty")))], id="info")

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
        if self._col_rows is not None:
            # 2칼럼 모드: 번호↔ListItem 1:1 이라 행잉 줄바꿈/인덱스 재매핑이 불필요.
            # 구분선(전폭 문자열 항목)만 nav 에서 건너뛴다.
            self._skip = {i for i, r in enumerate(self._col_rows)
                          if not isinstance(r, (tuple, list))
                          and self._is_skip(r)}
            if self._initial_index is not None:
                self._select_index(lv, self._initial_index)
            return
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
                        or [ListItem(Label(i18n.t("screen.empty")))])
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
        item = None               # 클릭이 떨어진 ListItem(있으면) — §3.8 행 클릭 점프
        while w is not None:
            wid = getattr(w, "id", None)
            if wid == "infoclose":
                event.stop()
                self.dismiss(None)
                return
            if isinstance(w, ListItem):
                item = w
            if wid == "infobox":
                inside_box = True
            w = w.parent
        # §3.8 Stage 2 ②: 박스 안 행 클릭 → 그 항목으로 점프(콜백). 마우스 1급 지원.
        if inside_box and item is not None and self._select_cb is not None:
            lv = self.query_one(ListView)
            try:
                idx = lv.children.index(item)
            except ValueError:
                idx = None
            if idx is not None and idx not in self._skip:
                event.stop()
                self._select_cb(idx)
                self.dismiss(None)
                return
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
        # §3.8 Stage 2 ②: Enter → 현재 선택 항목으로 점프(콜백). 그 외 키는 닫기.
        if event.key == "enter" and self._select_cb is not None:
            lv = self.query_one(ListView)
            if lv.index is not None and lv.index not in self._skip:
                self._select_cb(lv.index)
            self.dismiss(None)
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
    /* 액션 버튼 행(▸ [c]…/[o]…): 목록 맨 위, 버튼처럼 강조. 클릭/Enter/핫키로 실행. */
    #itbody ListItem.itactbtn { background: $primary-darken-2; }
    #itbody ListItem.itactbtn Label { color: $text; text-style: bold; }
    /* 하단 닫기 버튼(§10-A #6): 목록 아래 한 줄, 가로 가득·가운데 정렬. 클릭/터치로
       닫는다(상단 [x] 와 별개로 좁은 화면·긴 목록에서 손이 닿는 곳에 둠). */
    #itclosebtn { width: 100%; height: 1; margin-top: 1;
                  content-align: center middle; text-style: bold;
                  background: $panel-darken-2; color: $text; }
    #itclosebtn:hover { background: $error; }
    """
    _NAV = ("up", "down", "pageup", "pagedown", "home", "end")

    def __init__(self, tabs, initial=0, title=None, actions=None):
        super().__init__()
        self._tabs = tabs              # [(탭이름, [줄, ...]), ...]
        self._ti = max(0, min(initial, len(tabs) - 1)) if tabs else 0
        # ←→ 포커스 위치: 0..N-1=탭, N=닫기[x]. 초기엔 현재 탭.
        self._sel = self._ti
        # title 미지정 시 로케일 기본("정보"/"Info"). 호출부가 명시하면 그대로 쓴다.
        self._title = title if title is not None else i18n.t("screen.info")
        # {탭인덱스: (키,힌트,콜백) | [(키,힌트,콜백), ...]} — 그 탭에서 키를 누르면
        # 콜백 실행. 콜백이 줄 리스트를 돌려주면 그 탭 내용을 갱신한다(예: REC 캡처
        # 토글). 한 탭에 여러 동작(예: [c] 토글 · [o] 폴더 열기)을 둘 수 있게 리스트로
        # 정규화한다(단일 튜플도 허용 — 하위호환).
        self._actions = {ti: (a if isinstance(a, list) else [a])
                         for ti, a in (actions or {}).items()}

    def compose(self) -> ComposeResult:
        with Vertical(id="itbox"):
            with Horizontal(id="ithead"):
                with Horizontal(id="ittabs"):     # 탭 그룹(외곽선, #32)
                    for i, (name, _l) in enumerate(self._tabs):
                        yield Label("", id=f"ittab_{i}", markup=True)
                yield Label("", id="itgap")        # [x] 를 우측 끝으로 미는 여백
                yield Label("[x]", id="itclose", markup=False)
            yield ListView(id="itbody")
            yield Label(i18n.t("screen.close"), id="itclosebtn",
                        markup=False)  # 하단 닫기(§10-A #6)

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

    async def _run_action(self, key):
        """현재 탭의 동작(키 일치)을 실행한다. 콜백이 줄 리스트를 돌려주면 그 탭
        내용을 교체하고 재렌더. 키보드 핫키·액션버튼 클릭·Enter 가 공유한다."""
        for a in self._actions.get(self._ti, []):
            if a[0] == key:
                new_lines = a[2]()
                if new_lines is not None:
                    self._tabs[self._ti] = (self._tabs[self._ti][0], new_lines)
                await self._render_tab()
                return True
        return False

    async def _render_tab(self):
        self._render_tabbar()
        lines = self._tabs[self._ti][1] if self._tabs else []
        lv = self.query_one(ListView)
        await lv.clear()
        # 이 탭의 동작을 클릭 가능한 버튼 행으로 목록 맨 위에 둔다(예: REC 탭의
        # [c] 캡처 토글 · [o] 기록 폴더 열기). 핫키로도, 클릭/Enter 로도 실행된다.
        acts = self._actions.get(self._ti, [])
        items = [ListItem(Label(f"▸ {a[1]}", markup=False),
                          id=f"itact_{a[0]}", classes="itactbtn") for a in acts]
        items += [ListItem(Label(ln, markup=False))
                  for ln in (lines or [i18n.t("screen.empty")])]
        await lv.extend(items)
        # 커서 초깃값은 첫 내용 줄(액션 버튼 위가 아니라) — 정보가 먼저 보이게.
        if items:
            lv.index = len(acts) if lines else 0
        box = self.query_one("#itbox", Vertical)
        sub = i18n.t("screen.infotabs_sub")
        acts = self._actions.get(self._ti)
        if acts:                        # 이 탭의 동작들(예: [c] 캡처 토글 · [o] 폴더)
            sub = " · ".join(a[1] for a in acts) + " · " + sub
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
            if wid and wid.startswith("itact_"):   # 액션 버튼 클릭 → 그 동작 실행
                event.stop()
                await self._run_action(wid[len("itact_"):])
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
        # 현재 탭의 동작 중 그 키(핫키)가 있으면 실행(예: [c] 캡처 토글 · [o] 폴더 열기).
        if any(event.key == a[0] for a in self._actions.get(self._ti, [])):
            await self._run_action(event.key)
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
        if event.key in ("enter", "space"):
            # [x] 포커스면 닫기. 목록에서 액션 버튼(▸)이 선택돼 있으면 그 동작 실행.
            # 그 외 일반 항목이면 기존대로 닫는다.
            if self._sel == len(self._tabs):
                self.dismiss(None)
                return
            lv = self.query_one(ListView)
            cur = (lv.children[lv.index]
                   if lv.index is not None and 0 <= lv.index < len(lv.children)
                   else None)
            wid = getattr(cur, "id", None)
            if wid and wid.startswith("itact_"):
                await self._run_action(wid[len("itact_"):])
                return
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

def usage_bar_lines(usage, width=80, age_sec=None, right_align=False):
    """Claude `/usage` 한도 dict(session·week_all·week_sonnet)를 보기 좋은 표시
    줄 목록으로 만든다. 각 줄: 라벨(10셀 패딩) + 막대 + % + 리셋(요약, 타임존 생략).
    데이터가 없으면 None. TokenLogScreen 의 한도 섹션과 자동 /usage 팝업이 공유한다.

    age_sec: 실측 경과(초, S6 T3). 2분 이상 묵었으면 마지막에 'N분 전 실측'을 붙여
    stale 임을 알린다 — 실측이 주 표시로 승격되면서 묵은 값을 현재값으로 오독하지
    않게 하는 표시측 대응(stale 스냅샷 혼동 방지).

    right_align: 켜면 막대를 트랙 폭(barw)으로 채워 행마다 리셋 시작 열을 맞추고,
    % 숫자를 막대 바로 옆이 아니라 **줄 오른쪽 끝(width)** 에 우측정렬한다(리셋은
    막대 뒤). usage-view 플러그인 팝업/오버레이가 켠다 — 기본 False 라 기존 소비자
    (usage-panel·TokenLogScreen)의 표시는 그대로다(opt-in)."""
    if not isinstance(usage, dict):
        return None
    barw = 24 if width >= 80 else (16 if width >= 60 else 8)
    rows = []
    for key, name in (("session", i18n.t("usage.session_5h")),
                      ("week_all", i18n.t("usage.week_all")),
                      ("week_sonnet", i18n.t("usage.week_sonnet"))):
        d = usage.get(key)
        if not (isinstance(d, dict) and d.get("pct") is not None):
            continue
        pct = d["pct"]
        gauge = bar(pct, 100, barw)
        label = name + " " * max(0, 10 - sum(_char_cells(c) for c in name))
        reset = d.get("reset")
        # 타임존 괄호는 자리 절약 위해 생략.
        reset_txt = ("↻" + reset.split(" (")[0].strip()) if reset else ""
        if right_align:
            # 막대를 트랙 폭으로 채워(공백) 리셋 시작 열을 행마다 맞추고, % 숫자는
            # 줄 오른쪽 끝(width)에 우측정렬한다 — 막대/리셋과 % 사이를 공백으로 채움.
            gauge = gauge + " " * max(0, barw - len(gauge))
            tail = f"{pct:>3}%"
            body = f"{label}{gauge}  {reset_txt}".rstrip()
            gap = (width - sum(_char_cells(c) for c in body)
                   - sum(_char_cells(c) for c in tail))
            line = body + " " * max(1, gap) + tail
        else:
            # % 뒤에 '사용/used' 를 명시한다(2026-06-12 사용자 보고): 방향 라벨이
            # 없으면 잔여 표기와 섞여 다른 값처럼 읽혔다 — Claude /usage 의 "N% used"
            # 와 동일 표기. footer 5h 도 같은 사용률로 통일됐다(clientstatus
            # claude.limit_used — 모든 표면이 같은 방향·같은 숫자).
            line = f"{label}{gauge} {pct:>3}% {i18n.t('usage.used')}"
            if reset_txt:
                line += "  " + reset_txt
        rows.append(line)
    # 그림자 /usage 세션의 계정(일치 확인용). 키가 있을 때만 — 폰 앱과 다른 계정이면
    # 한도가 실제로 달라지므로 눈으로 대조하라고 표시한다. 신호 못 잡으면 '미확인'.
    if rows and "account" in usage:
        acct = usage.get("account")
        rows.append(i18n.t("usage.account", acct=acct) if acct
                    else i18n.t("usage.account_unknown"))
    # S6 T3: 실측 신선도 — 2분 미만이면 표기 생략(잡음), 그 이상은 분/시간 단위.
    if rows and isinstance(age_sec, (int, float)) and age_sec >= 120:
        m = int(age_sec // 60)
        ago = (i18n.t("usage.ago_hm", h=m // 60, m=m % 60) if m >= 60
               else i18n.t("usage.ago_m", m=m))
        rows.append(i18n.t("usage.measured_ago", ago=ago))
    return rows or None


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

    def _commands(self):
        """후보·힌트용 명령 풀 = 코어 COMMANDS + 등록된 플러그인 명령(clock-mode·
        auto-compact 등). 플러그인 디렉토리를 지우면 그 명령이 후보·힌트에서 조용히
        빠진다('?' 목록이 `COMMANDS + reg.commands` 를 쓰는 것과 동일한 풀)."""
        reg = getattr(self.app, "plugins", None)
        return list(COMMANDS) + (list(reg.commands) if reg else [])

    def _command_options(self):
        """코어 COMMAND_OPTIONS + 플러그인 command_options(토글/선택지 인자 스키마).
        플러그인 명령(예: auto-compact on|off)도 인자 자리서 방향키 선택 UI 를 띄운다."""
        reg = getattr(self.app, "plugins", None)
        opts = dict(COMMAND_OPTIONS)
        if reg:
            opts.update(reg.command_options or {})
        return opts

    def _refresh_cands(self):
        """입력을 명령 이름과 부분일치시켜 후보 영역을 갱신한다. 구분자(공백·언더바·
        하이픈)는 norm_sep 로 통일하므로 'clock m'·'clock_m'·'clock-m' 이 모두
        'clock-mode' 에 잡힌다(공백도 언더바처럼 취급 — 멀티워드 명령 이름을 공백으로
        검색 가능). 완성된 명령 뒤에 **실제 인자**를 치면 정규화 입력이 어떤 명령
        이름의 부분문자열도 아니게 되어(예: 'rename-tab foo'→'rename-tab-foo') 후보가
        자연히 사라지고, 그때 _refresh_hint 가 인자 힌트를 대신 띄운다."""
        if self._purpose != "command":
            return
        lbl = self.query_one("#pcand", Label)
        s = self.query_one(Input).value.strip()
        pool = self._commands()
        matches = []
        if not s:
            # 빈 명령 프롬프트(esc :) → 전체 명령을 위쪽에 보여준다(↑↓ 탐색, #).
            matches = [(n, d) for (n, d, *_) in pool]
        else:
            ql = s.lower()
            # 한영 오타 복원: IME 켠 채 친 한글(예: "ㅏㅑㅣㅣ"=kill)을 QWERTY 로
            # 되돌려 그걸로 검색한다(요청). 한글이 섞였을 때만 변환.
            if has_hangul(ql):
                ql = hangul_to_qwerty(ql).lower()
            # 구분자(공백/언더바/하이픈) 무시: "rename_"·"rename " 도 "rename-tab"
            # 후보에 잡히게 검색어·이름을 norm_sep 로 통일해 부분일치한다. 공백 게이트
            # 없음 — 'clock m'(공백) 도 멀티워드 명령 'clock-mode' 에 매칭된다.
            qs = norm_sep(ql)
            matches = [(n, d) for (n, d, *_) in pool
                       if qs in norm_sep(n.lower())]
            # 정확히 한 개이고 그게 입력과 동일하면 더 제안할 게 없음.
            if len(matches) == 1 and norm_sep(matches[0][0].lower()) == qs:
                matches = []
            # 맥락 우선 정렬(요청): 접두사가 모호할 때 무조건 맨 위(선언 순서) 항목을
            # 고르지 말고 현재 맥락에 맞는 명령을 먼저 하이라이트한다. 빈 입력(전체
            # 목록)에는 적용하지 않는다 — 카탈로그 순서를 흔들지 않으려는 것.
            matches = self._context_rank(matches)
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

    def _context_rank(self, matches):
        """후보를 현재 맥락에 맞게 안정 정렬한다(기본 하이라이트=정렬 후 첫 항목).

        규칙(현재): **활성 탭에 패널이 하나뿐**이면 패널-상대 명령(rename-pane·
        resize-pane·swap-pane 등 PANE_SCOPED_CMDS + 플러그인 pane_scoped)을 탭/서버
        범위 명령 **뒤로** 내린다. 단일 패널에선 그 패널이 곧 탭 전체라, 같은 접두사를
        공유하는 모호한 경우(예: 'rename' → rename-pane vs rename-tab) 탭 범위 쪽이
        의도일 가능성이 높고 swap/resize/select/break/join 류는 사실상 무효이기 때문.
        패널이 2개 이상이면 재정렬하지 않고 기존(선언) 순서를 보존한다.

        Python sorted 는 안정적이라 같은 순위 안에서는 원래 순서가 유지된다. 유일
        매치(예: 'split' → split-window)는 순위와 무관하게 그대로 보이므로 패널 생성
        명령의 발견성은 영향을 받지 않는다."""
        if len(matches) < 2:
            return matches
        try:
            npanes = len(self.app.layout.get("panes", []))
        except Exception:
            npanes = 0
        if npanes >= 2:
            return matches
        pane_scoped = set(PANE_SCOPED_CMDS)
        reg = getattr(self.app, "plugins", None)
        if reg and getattr(reg, "pane_scoped", None):
            pane_scoped |= set(reg.pane_scoped)
        return sorted(matches, key=lambda m: 1 if m[0] in pane_scoped else 0)

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
            rows.append(f"[dim]{i18n.t('screen.more_up')}[/dim]")
        for i in range(start, min(start + body, n)):
            nm, d = self._cand[i]
            if i == self._sel:
                rows.append(f"[reverse]{self._esc(nm):<20} {self._esc(d)}[/reverse]")
            else:
                rows.append(f"{self._esc(nm):<20} [dim]{self._esc(d)}[/dim]")
        if start + body < n:
            rows.append(f"[dim]{i18n.t('screen.more_down')}[/dim]")
        lbl.update("\n".join(rows))

    def _accept_cand(self):
        inp = self.query_one(Input)
        name = self._cand[self._sel][0]
        inp.value = name + " "
        inp.cursor_position = len(inp.value)
        inp.focus()
        self._refresh_cands()
        self._refresh_hint()

    def _cmd_desc(self, name):
        # 코어 + 플러그인 명령에서 설명을 찾는다(플러그인 명령 힌트도 뜨게).
        # §6 ③ 표시 시점 번역(코어=카탈로그, 플러그인 미등록은 default 로 원본 유지).
        for (n, d, *_) in self._commands():
            if n == name:
                return i18n.t(f"cmd.{n}", default=d)
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
        opts = self._command_options().get(name)
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
            d = self._esc(i18n.t(disp))   # §6 ⑤ 선택지 라벨 번역(키=원문)
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
        # 명령 프롬프트는 이미 고정 ':' 프리픽스가 붙어 있으므로, 첫 글자로 ':' 를
        # 또 입력하면 무시한다(요청). 맨 앞의 ':' 들만 떼고(중간 ':' 는 보존) 재반영.
        if self._purpose == "command" and event.value.startswith(":"):
            inp = self.query_one(Input)
            inp.value = event.value.lstrip(":")
            inp.cursor_position = len(inp.value)
            return                       # 값 변경이 on_input_changed 를 다시 부른다
        # 명령 프롬프트: 한글 IME 가 켜진 채 명령 이름을 치면 자모(ㄴ/ㅁ/ㅔ…)나 조합
        # 음절이 들어가 명령 검색·실행이 안 된다. **명령 이름 구간(첫 공백 이전)** 의 한글을
        # QWERTY 로 되돌린다 — ESC/prefix 모드의 hangul_to_qwerty 와 동일 변환. 공백이 생기면
        # (= 인자 입력 단계) 변환하지 않아 한글 인자(rename-tab 등)는 보존된다. 모든 명령
        # 이름은 ASCII 라 이 변환은 항상 안전하다. 확정된 글자만 변환 가능(조합 중 preedit 은
        # 앱에 안 옴 — best-effort). 값을 바꾸면 변경이 on_input_changed 를 다시 부른다.
        if (self._purpose == "command" and event.value and " " not in event.value
                and has_hangul(event.value)):
            conv = hangul_to_qwerty(event.value)
            if conv != event.value:
                inp = self.query_one(Input)
                inp.value = conv
                inp.cursor_position = len(conv)
                return
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
            # 코어 명령 + 플러그인 등록 명령(ncd·claude-code 등)을 함께 보여 준다.
            reg = getattr(self.app, "plugins", None)
            cmds = COMMANDS + reg.commands if reg else COMMANDS
            self.app.push_screen(CommandListScreen(cmds, base), fill)
            return
        self._refresh_cands()
        self._refresh_hint()
        # 패널 대상 명령(rename-pane 등)을 작성 중이면 대상(활성) 패널을 밝게 표시(요청).
        if self._purpose == "command":
            first = (event.value.split(None, 1)[0].lower()
                     if event.value.strip() else "")
            try:
                self.app._set_cmd_target(
                    first in PANE_SCOPED_CMDS
                    or first in self.app.plugins.pane_scoped)
            except Exception:
                pass

    def on_unmount(self):
        # 프롬프트가 닫히면 명령 대상 패널 강조를 해제한다(요청).
        if self._purpose == "command":
            try:
                self.app._set_cmd_target(False)
            except Exception:
                pass

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
            return
        # rename 등 ghost 제안 프롬프트: Tab 으로도 제안(현재 이름)을 끝까지 채운다
        # (→ 화살표는 Textual Input 기본 accept). 채운 뒤엔 편집·덧붙이기 가능(요청).
        if (self._purpose != "command" and event.key == "tab"
                and self._suggester is not None):
            inp = self.query_one(Input)
            sug = getattr(inp, "_suggestion", "") or ""
            if sug and sug != inp.value:
                event.stop()
                inp.value = sug
                inp.cursor_position = len(sug)
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

    def __init__(self, message, yes_label=None, no_label=None,
                 title=None, default_yes=False, danger=False):
        super().__init__()
        self._message = message
        # 라벨/제목 미지정 시 로케일 기본. 호출부가 명시하면 그대로 쓴다.
        self._yes = yes_label if yes_label is not None else i18n.t("screen.close")
        self._no = no_label if no_label is not None else i18n.t("screen.cancel")
        self._title = title if title is not None else i18n.t("screen.confirm")
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
        box.border_subtitle = i18n.t("screen.confirm_sub")
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
                for it in self._items] or [ListItem(
                    Label(i18n.t("screen.no_buffers")), id="bnone")]
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

    def __init__(self, names, title=None):
        super().__init__()
        self._names = names
        self._title = title if title is not None else i18n.t("screen.layout_load")

    def compose(self) -> ComposeResult:
        rows = [ListItem(Label(nm), id=f"L{i}")
                for i, nm in enumerate(self._names)] or \
               [ListItem(Label(i18n.t("screen.no_layouts")), id="Lnone")]
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
