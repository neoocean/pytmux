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
from textual.widget import Widget
from textual.strip import Strip
from textual.widgets import Input, Label, ListItem, ListView, Static, TextArea

from rich.highlighter import Highlighter
from rich.segment import Segment
from rich.style import Style

from . import i18n
from .clientutil import (COMMAND_FREETEXT, COMMAND_NOARG, COMMAND_OPTIONS,
                         COMMANDS, ESC_MODE_KEYS, MENU_GROUP_LABELS, MENU_GROUPS,
                         MENU_ITEMS, MENU_TOGGLES, MENU_TOPLEVEL,
                         PANE_SCOPED_CMDS, PREFIX_KEYS, SETTINGS, SETTINGS_CATS,
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
    /* §10: 박스 높이를 "항목이 가장 많은 카테고리" 기준으로 고정(_apply_layout 에서
       #cmdbox.height 설정) — ←→ 카테고리 전환·검색 필터 시 박스가 출렁이지
       않게. ListView 는 1fr 로 박스 안을 채운다 — 고정 행수를 강제하면 낮은
       (모바일) 터미널에서 목록이 박스 밖으로 넘쳐(화면 밖) 커서가 보이지 않는
       곳까지 스크롤된다(Textual scroll_to_widget 은 보이는 영역과 무관하게
       내부 뷰포트 기준으로만 커서를 따라가기 때문). 1fr 이면 실제 가시 영역과
       뷰포트가 일치해 커서가 항상 맨 아랫줄에 머문 채 목록만 스크롤된다. */
    #cmds { width: 100%; height: 1fr;
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

    # 박스 외 세로 오버헤드(외곽선 2 + 탭머리 3 + 검색창 3)와 화면 하단 여백.
    _BOX_OVERHEAD = 8
    _MARGIN_BOTTOM = 3

    def _apply_layout(self):
        # §10: 모든 카테고리 중 최대 항목 수로 박스 높이를 고정한다(전환·검색 시
        # 높이 불변). 단, 낮은 (모바일) 터미널에서 박스가 화면 밖으로 넘치지 않게
        # 화면 높이에 맞춰 클램프한다 — 넘치면 ListView(1fr)가 화면 밖까지 늘어나
        # 커서가 보이지 않는 영역으로 스크롤된다. 목록 행 한도는 _CMDS_MAX_ROWS.
        maxn = max((len(items) for _, items in self._all_cats), default=1)
        want_rows = min(maxn, self._CMDS_MAX_ROWS)
        # 화면에 들어가는 최대 박스 높이: 하단 여백 + 위쪽 1행 안전 여백을 남긴다.
        fit = self.app.size.height - self._MARGIN_BOTTOM - 1
        box_h = max(self._BOX_OVERHEAD + 3,
                    min(want_rows + self._BOX_OVERHEAD, fit))
        self.query_one("#cmdbox", Vertical).styles.height = box_h

    async def on_mount(self):
        self._apply_layout()
        si = self.query_one("#cmdsearch", Input)
        si.can_focus = False           # 표시 전용 — 클릭/탭 포커스가 키 모델을 깨지 않게
        si.value = self._query
        await self._rebuild()
        self.query_one(ListView).focus()

    def on_resize(self, event: events.Resize):
        # 화면 회전·크기 변경(모바일) 시 박스 높이를 다시 화면에 맞춘다.
        self._apply_layout()

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

class _SettingInputScreen(ModalScreen):
    """문자열 설정(상태줄 포맷·prefix·default-path 등) 값을 :settings 화면 위에서
    한 줄로 입력받는 작은 모달. Enter=저장(값 반환), Esc=취소(None)."""
    CSS = """
    _SettingInputScreen { align: center middle; background: $background 60%; }
    #sibox { width: 70%; max-width: 80; height: auto;
             border: round $accent; background: $panel; padding: 0 1; }
    #siinput { width: 100%; border: none; height: 1; padding: 0;
               background: $panel; color: $text; }
    """

    def __init__(self, title, value, hint=""):
        super().__init__()
        self._title = title
        self._value = value or ""
        self._hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(id="sibox"):
            yield Input(value=self._value, id="siinput")

    def on_mount(self):
        box = self.query_one("#sibox", Vertical)
        box.border_title = self._title
        if self._hint:
            box.border_subtitle = self._hint
        self.query_one(Input).focus()

    def on_input_submitted(self, event):
        event.stop()
        self.dismiss(event.value)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class SettingsScreen(ModalScreen):
    """통합 설정 화면(:settings) — pytmux 설정이 config 파일·런타임 명령·서버 opts·
    플러그인 전용 화면에 흩어져 있던 것을 한 곳에 모은다. clientutil.SETTINGS 레지스트리
    하나만 순회하므로 설정 추가는 레지스트리 한 줄이면 된다.

    카테고리별 dim 헤더로 묶은 단일 리스트. 행에서: ←→(또는 클릭) = bool 토글·enum
    순환·숫자 증감을 즉시 적용+영속(app.apply_setting), 문자열은 Enter 로 입력 모달,
    링크 행(Claude·플러그인)은 Enter/클릭으로 dismiss 하며 그 명령을 돌려줘 호출부가
    전용 화면을 연다. ↑↓ 이동, Esc/[x]/바깥 클릭 닫기. CommandListScreen·
    CommandOptionsScreen 의 박스/포커스 관례를 따른다."""
    CSS = """
    SettingsScreen { align: center middle; background: $background 80%; }
    #setbox { width: 84%; max-width: 92; height: 90%;
              border: round $accent; background: $panel; padding: 0 1; }
    #sethead { width: 100%; height: 1; }
    #setgap { width: 1fr; height: 1; }
    #setclose { width: 5; height: 1; content-align: center middle;
                background: $error; color: $text; text-style: bold; }
    /* 좌: 카테고리 세로 탭 / 우: 전체 설정 목록(스크롤). 탭 클릭=해당 카테고리로 점프. */
    #setbody { width: 100%; height: 1fr; }
    #settabs { width: 18; height: 100%; padding: 0 1 0 0;
               border-right: round $panel-darken-2; }
    #settabs Label { width: 100%; height: 1; }
    #sets { width: 1fr; height: 100%;
            background: $panel; padding: 0 0 0 1;
            overflow-y: scroll;
            scrollbar-size-vertical: 2;
            scrollbar-color: $accent;
            scrollbar-background: $panel-darken-2; }
    """

    def __init__(self, prefix_key="ctrl+b", user_bindings=None,
                 root_bindings=None):
        super().__init__()
        # SETTINGS_CATS 순서로 평탄화: 각 항목 = (desc, 카테고리 첫 항목 여부).
        # _cats = 항목이 있는 카테고리(좌측 세로 탭 순서), _cat_first = 카테고리→그
        # 카테고리 첫 행의 flat 인덱스(탭 클릭 시 그 위치로 스크롤).
        self._prefix_key = prefix_key or "ctrl+b"
        self._user_bindings = dict(user_bindings or {})
        self._root_bindings = dict(root_bindings or {})
        self._flat = []
        self._cats = []
        self._cat_first = {}
        for cat in SETTINGS_CATS:
            if cat == "키":
                continue            # '키' 카테고리는 아래에서 따로(읽기 전용 레퍼런스)
            first = True
            for desc in SETTINGS:
                if desc.get("cat") == cat:
                    if first:
                        self._cats.append(cat)
                        self._cat_first[cat] = len(self._flat)
                    self._flat.append((desc, first))
                    first = False
        self._build_keys_category()
        self._labels = []     # 행별 Label 위젯(값 변경 시 그 행만 갱신)
        self._vals = {}       # key -> 현재 선택값(bool/enum=str, ratio=float, int=int)

    def _build_keys_category(self):
        """'키' 카테고리: ESC 모드·prefix 모드 내장 키 + 사용자 바인딩을 읽기 전용으로
        나열한다(요청 2026-06-18). 행 desc type='keyref' — ←→/Enter 무동작, 표시만."""
        self._cats.append("키")
        self._cat_first["키"] = len(self._flat)
        state = {"first": True}

        def add(d):
            self._flat.append((d, state["first"]))
            state["first"] = False

        def sub(text):
            add({"type": "keyref", "cat": "키", "sub": text})

        def keyrow(k, kid=None, d=None):
            add({"type": "keyref", "cat": "키", "k": k, "kid": kid, "d": d})

        # 키표기 열은 보통 언어중립(기호)이라 그대로 쓰지만, e_up/e_tb 처럼 그 자리에
        # 한글 설명문이 든 항목은 EN 로케일에서 한글이 새 보인다 — kkey.<id> 가 있으면
        # 그걸 쓰고, 없으면(대부분) 원문 표기를 그대로 둔다(default=k).
        sub(i18n.t("klist.sub_esc"))
        for kid, k, _ko, _en in ESC_MODE_KEYS:
            keyrow(i18n.t(f"kkey.{kid}", default=k), kid=kid)
        pk = (self._prefix_key.replace("ctrl+", "Ctrl-")
              .replace("shift+", "Shift-").replace("alt+", "Alt-"))
        sub(i18n.t("klist.sub_prefix", p=pk))
        for kid, k, _ko, _en in PREFIX_KEYS:
            keyrow(i18n.t(f"kkey.{kid}", default=k), kid=kid)
        sub(i18n.t("klist.sub_user"))
        if self._user_bindings:
            for key, cmd in sorted(self._user_bindings.items()):
                keyrow(key, d=str(cmd))
        else:
            keyrow("", d=i18n.t("klist.none"))
        if self._root_bindings:
            sub(i18n.t("klist.sub_user_root"))
            for key, cmd in sorted(self._root_bindings.items()):
                keyrow(key, d=str(cmd))

    # ---- 값 표시 ----
    def _vlabel(self, v):
        """저장값(on/always/ko…)을 표시 라벨로 — 로케일 번역(setval.<v>, 기본=원값).
        기술적 값(vi/emacs/pyte/native)은 미등록이라 원값 그대로."""
        return i18n.t(f"setval.{v}", default=str(v))

    def _val_display(self, desc):
        t = desc["type"]
        key = desc["key"]
        if t == "link":
            return f"[dim]→[/] {i18n.t('setting.open', default='열기')}"
        if t == "str":
            v = self._vals.get(key)
            return (v if v and v.strip()
                    else f"[dim]({i18n.t('setting.unset', default='미설정')})[/]")
        if t in ("ratio", "int"):
            v = self._vals.get(key)
            shown = (i18n.t("setting.unknown", default="미상") if v is None
                     else (f"{v:.2f}" if t == "ratio" else str(v)))
            return f"[dim]‹[/] [b]{shown}[/] [dim]›[/]"
        # bool/enum: 선택지를 한 줄에 펼치고 현재값만 강조(←→ 로 순환). 현재값을
        # 모르면(status 미수신 서버 토글) 모두 흐리게 + '미상' 표기.
        choices = ["on", "off"] if t == "bool" else list(desc["choices"])
        cur = self._vals.get(key)
        parts = []
        for c in choices:
            lbl = self._vlabel(c)
            parts.append(f"[reverse b] {lbl} [/]" if c == cur else f"[dim]{lbl}[/]")
        seg = "  ".join(parts)
        if cur is None:
            seg += f"  [dim]({i18n.t('setting.unknown', default='미상')})[/]"
        return seg

    def _row_text(self, idx):
        desc, first = self._flat[idx]
        out = ""
        if first:
            cat = i18n.t(f"setcat.{desc['cat']}", default=desc["cat"])
            # 다음 카테고리 시작 전 빈 줄 1개로 묶음을 구분(사용자 요청 2026-06-18).
            # 첫 카테고리(idx 0)는 위에 여백이 필요 없어 제외. 헤더와 같은 행 라벨에
            # 붙여 그 카테고리 첫 항목 위에 빈 줄로 보인다.
            if idx > 0:
                out += "\n"
            out += f"[dim]── {cat} ──[/]\n"
        if desc["type"] == "keyref":      # 읽기 전용 키 레퍼런스('키' 탭)
            if "sub" in desc:
                return out + f"[dim]  {desc['sub']}[/]"
            k = desc.get("k") or ""
            d = desc.get("d")
            if d is None and desc.get("kid"):
                d = i18n.t(f"klist.{desc['kid']}", default=desc["kid"])
            width = sum(_char_cells(ch) for ch in k)
            pad = max(1, 16 - width)
            return out + f"    [b]{k}[/]{' ' * pad}[dim]{d or ''}[/]"
        label = i18n.t(f"setting.{desc['key']}", default=desc["key"])
        width = sum(_char_cells(ch) for ch in label)
        pad = max(1, 26 - width)
        line = label + " " * pad + self._val_display(desc)
        if desc.get("restart"):
            line += f"  [dim]({i18n.t('setting.restart', default='재시작 시 발효')})[/]"
        return out + line

    def compose(self) -> ComposeResult:
        with Vertical(id="setbox"):
            with Horizontal(id="sethead"):
                yield Label("", id="setgap")
                yield Label("[x]", id="setclose", markup=False)
            with Horizontal(id="setbody"):
                with Vertical(id="settabs"):
                    for i in range(len(self._cats)):
                        yield Label("", id=f"settab_{i}", markup=True)
                items = []
                for i in range(len(self._flat)):
                    lab = Label("", markup=True)
                    self._labels.append(lab)
                    items.append(ListItem(lab, id=f"set_{i}"))
                yield ListView(*items, id="sets")

    def on_mount(self):
        # 현재 값을 앱 상태에서 읽어 초기화(미추적 서버 토글은 None).
        for desc, _f in self._flat:
            if desc["type"] in ("link", "keyref"):
                continue
            cur = self.app.setting_current(desc["key"])
            if desc["type"] == "ratio" and cur is not None:
                try: cur = float(cur)
                except (TypeError, ValueError): cur = None
            elif desc["type"] == "int" and cur is not None:
                try: cur = int(cur)
                except (TypeError, ValueError): cur = None
            self._vals[desc["key"]] = cur
        for i in range(len(self._flat)):
            self._labels[i].update(self._row_text(i))
        box = self.query_one("#setbox", Vertical)
        box.border_title = i18n.t("screen.settings_title", default="설정")
        box.border_subtitle = i18n.t("screen.settings_sub",
                                     default="←→ 값 · Enter 입력/열기 · Esc 닫기")
        lv = self.query_one(ListView)
        lv.index = 0
        self._refresh_tabs()
        lv.focus()

    # ---- 좌측 세로 카테고리 탭(전체 목록의 그 카테고리 위치로 스크롤) ----
    def _active_cat(self):
        idx = self.query_one(ListView).index
        if idx is not None and 0 <= idx < len(self._flat):
            return self._flat[idx][0]["cat"]
        return self._cats[0] if self._cats else None

    def _refresh_tabs(self):
        active = self._active_cat()
        for i, cat in enumerate(self._cats):
            name = i18n.t(f"setcat.{cat}", default=cat)
            lbl = self.query_one(f"#settab_{i}", Label)
            lbl.update(f"[reverse b] {name} [/]" if cat == active
                       else f"[dim] {name} [/]")

    def _jump_to_cat(self, ci):
        if not (0 <= ci < len(self._cats)):
            return
        idx = self._cat_first.get(self._cats[ci])
        if idx is None:
            return
        lv = self.query_one(ListView)
        lv.index = idx
        try:
            lv.scroll_to_widget(lv.children[idx], top=True, animate=False)
        except Exception:
            pass
        self._refresh_tabs()
        lv.focus()

    def on_list_view_highlighted(self, event):
        # ↑↓ 로 행을 옮기면 해당 행의 카테고리 탭이 활성으로 따라온다.
        self._refresh_tabs()

    def _refresh_row(self, idx):
        self._labels[idx].update(self._row_text(idx))

    def _cycle(self, idx, step):
        desc, _f = self._flat[idx]
        t, key = desc["type"], desc["key"]
        if t in ("bool", "enum"):
            choices = ["on", "off"] if t == "bool" else list(desc["choices"])
            cur = self._vals.get(key)
            try:
                i = choices.index(cur)
            except ValueError:
                i = -1 if step > 0 else 0
            val = choices[(i + step) % len(choices)]
            self._vals[key] = val
            self.app.apply_setting(desc, val)
            self._refresh_row(idx)
        elif t in ("ratio", "int"):
            cur = self._vals.get(key)
            if cur is None:
                cur = desc["lo"]
            v = max(desc["lo"], min(desc["hi"], cur + step * desc["step"]))
            if t == "int":
                v = int(round(v))
            else:
                v = round(v, 2)
            self._vals[key] = v
            self.app.apply_setting(desc, f"{v:.2f}" if t == "ratio" else str(v))
            self._refresh_row(idx)

    def _activate(self, idx):
        desc, _f = self._flat[idx]
        t = desc["type"]
        if t == "link":
            self.dismiss(desc["link"])      # 호출부가 전용 화면 명령을 실행
        elif t == "str":
            title = i18n.t(f"setting.{desc['key']}", default=desc["key"])
            cur = self._vals.get(desc["key"]) or ""

            def _cb(text, i=idx, d=desc):
                if text is not None:
                    self._vals[d["key"]] = text.strip()
                    self.app.apply_setting(d, text.strip())
                    self._refresh_row(i)
            self.app.push_screen(_SettingInputScreen(title, cur), _cb)
        else:
            self._cycle(idx, 1)             # bool/enum/숫자: Enter=한 칸 전진

    def on_list_view_selected(self, event):
        # 마우스 클릭으로 행 선택 시 활성화(Enter 는 on_key 에서 stop 해 중복 방지).
        idx = self.query_one(ListView).index
        if idx is not None and 0 <= idx < len(self._flat):
            self._activate(idx)

    async def on_click(self, event: events.Click):
        w = getattr(event, "widget", None)
        inside = False
        while w is not None:
            wid = getattr(w, "id", None)
            if wid == "setclose":
                event.stop(); self.dismiss(None); return
            if wid and wid.startswith("settab_"):    # 카테고리 탭 클릭 → 점프
                event.stop(); self._jump_to_cat(int(wid.split("_")[1])); return
            if wid == "setbox":
                inside = True
            w = w.parent
        if not inside:
            event.stop(); self.dismiss(None)

    def on_key(self, event: events.Key):
        k = event.key
        if k == "escape":
            event.stop(); self.dismiss(None)
        elif k == "enter":
            event.stop()
            idx = self.query_one(ListView).index
            if idx is not None and 0 <= idx < len(self._flat):
                self._activate(idx)
        elif k in ("left", "right"):
            event.stop()
            idx = self.query_one(ListView).index
            if idx is not None and 0 <= idx < len(self._flat):
                self._cycle(idx, 1 if k == "right" else -1)
        elif k in ("tab", "shift+tab") and self._cats:
            # Tab/Shift+Tab = 카테고리 탭 순환(다음/이전 카테고리 위치로 스크롤).
            event.stop(); event.prevent_default()
            ci = self._cats.index(self._active_cat())
            step = 1 if k == "tab" else -1
            self._jump_to_cat((ci + step) % len(self._cats))


class MenuScreen(ModalScreen):
    """우클릭 컨텍스트 메뉴(§8.1 그룹/서브메뉴화). 최상위는 그룹 진입점(`패널 ▸`
    등)+자주/세션 직접 항목+구분선만 그려 짧게 두고, 그룹을 고르면 그 자식 항목으로
    자식 MenuScreen 을 push 한다(Enter 진입·Esc 부모 복귀). leaf 항목 선택은
    dismiss(key) 가 부모로 버블해 최상위 open_menu 핸들러(_run_menu_action)가 그대로
    실행하므로 디스패치 키는 평면 시절과 동일하다 — 모든 액션 도달성 보존.

    entries=None 이면 최상위(MENU_TOPLEVEL+플러그인 그룹), 아니면 그 entries 를 그린다.
    title 은 서브메뉴 헤더(테두리 제목)."""

    CSS = """
    MenuScreen { align: center middle; }
    #menu { width: 40; height: auto; border: round $accent; background: $panel; }
    #menu > .menu-sep { color: $text-muted; }
    """

    def __init__(self, entries=None, title=None, anchor=None, group=None):
        super().__init__()
        self._entries = entries        # None=최상위
        self._title = title            # 서브메뉴 헤더
        # anchor=(부모_left, 부모_right, row_y) 면 그 부모 우측에 캐스케이드로 펼친다
        # (없으면 중앙 — 최상위 메뉴). screen 좌표.
        self._anchor = anchor
        self._group = group            # 이 서브메뉴를 연 그룹 키(호버 전환 자기식별)
        self._pending_switch = None    # 호버 전환 요청 그룹(자식 닫힌 뒤 부모가 연다)
        self._switching = False        # 전환 중 중복 dismiss 가드(자식 측)

    def _label_map(self):
        # leaf 키 → 원본 라벨. 코어 MENU_ITEMS + 플러그인 menu_items(delete-to-disable).
        m = dict(MENU_ITEMS)
        plug = getattr(self.app, "plugins", None)
        if plug:
            m.update(dict(plug.menu_items))
        return m

    def _plugin_keys(self):
        plug = getattr(self.app, "plugins", None)
        return [k for k, _ in plug.menu_items] if plug else []

    def _toplevel_entries(self):
        # 최상위 표시 토큰. 플러그인 항목이 있으면 "group:plugin" 을 group:tab 뒤에 끼운다.
        entries = list(MENU_TOPLEVEL)
        if self._plugin_keys():
            entries.insert(entries.index("group:tab") + 1, "group:plugin")
        return entries

    def _group_items(self, g):
        if g == "plugin":
            return self._plugin_keys()
        return MENU_GROUPS.get(g, [])

    def _group_label(self, g):
        return i18n.t(f"menu.group.{g}", default=MENU_GROUP_LABELS.get(g, g))

    def _leaf_label(self, key):
        return i18n.t(f"menu.{key}", default=self._label_map().get(key, key))

    def compose(self) -> ComposeResult:
        self._labels = {}     # leaf key -> (Label 위젯, 원본 라벨) — 표시된 항목만
        self._optim = {}      # 토글 낙관적 상태(status 회신 전 즉시 반영)
        entries = self._entries if self._entries is not None \
            else self._toplevel_entries()
        items = []
        for i, tok in enumerate(entries):
            if tok == "--":
                # 비선택 구분선 — 키 탐색에서 건너뛰고(disabled) 디스패치 안 됨.
                items.append(ListItem(Label("─" * 36, classes="menu-sep"),
                                      id=f"sep_{i}", disabled=True))
            elif tok.startswith("group:"):
                g = tok.split(":", 1)[1]
                items.append(ListItem(Label(f"{self._group_label(g)} ▸"),
                                      id=f"g_{g}"))
            else:
                lab = Label(self._fmt(tok, self._leaf_label(tok)))
                self._labels[tok] = (lab, self._leaf_label(tok))
                items.append(ListItem(lab, id=f"m_{tok}"))
        lv = ListView(*items, id="menu")
        if self._title:
            lv.border_title = self._title
        yield lv

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
        # 중첩(부모→자식) 메뉴에서 status 갱신은 최상위(현재) 메뉴를 가리켜야 하므로
        # 직전 _menu_screen 을 보관했다 자식이 닫힐 때 부모로 복원한다.
        self._prev_menu = getattr(self.app, "_menu_screen", None)
        self.app._menu_screen = self
        if self._anchor is not None:
            self._place_anchored()
        elif self._entries is None:
            self._bias_toplevel_left()

    def _bias_toplevel_left(self):
        # 최상위 메뉴: 그룹을 고르면 서브메뉴가 우측에 캐스케이드로 펼쳐지므로, 그 한 폭
        # (메뉴 너비)을 우측에 비워두도록 필요한 만큼만 왼쪽으로 민다. 화면이 넉넉하면
        # (≥메뉴×2) 중앙 그대로(dx=0). 세로 중앙은 CSS align 유지 — offset.x 만 보정한다.
        try:
            lv = self.query_one("#menu")
        except Exception:
            return
        size = getattr(self.app, "size", None)
        if not size:
            return
        w = 40                                   # #menu CSS width
        centered_x = max(0, (size.width - w) // 2)
        x = max(0, min(centered_x, size.width - 2 * w))
        dx = x - centered_x
        if dx:
            lv.styles.offset = (dx, 0)

    def _place_anchored(self):
        # 서브메뉴를 부모 메뉴 우측에 붙여 캐스케이드로 펼친다(우측 넘침→왼쪽 폴백).
        # 자식 모달은 배경 dim 을 끄고(부모 위 이중 dim 방지) 절대 위치로 옮긴다.
        try:
            lv = self.query_one("#menu")
        except Exception:
            return
        self.styles.background = "transparent"
        self.styles.align_horizontal = "left"
        self.styles.align_vertical = "top"
        left, right, row_y = self._anchor
        w = 40                                   # #menu CSS width
        size = getattr(self.app, "size", None)
        sw = size.width if size else (right + w)
        sh = size.height if size else (row_y + 99)
        x = right                                # 부모 오른쪽에 인접
        if x + w > sw:                           # 우측 넘침 → 부모 왼쪽으로
            x = max(0, left - w)
        h = (len(self._entries) if self._entries else 0) + 2   # 항목 + 테두리
        y = row_y
        if y + h > sh:                           # 하단 넘침 → 위로 끌어올림
            y = max(0, sh - h)
        lv.styles.offset = (x, y)

    def on_unmount(self):
        if getattr(self.app, "_menu_screen", None) is self:
            self.app._menu_screen = getattr(self, "_prev_menu", None)

    def _open_group(self, g, anchor_item=None):
        # 그룹 선택 → 자식 MenuScreen push. 자식의 leaf 선택은 dismiss(key) 로 여기 back
        # 콜백에 와, 부모도 같은 key 로 dismiss → open_menu 핸들러까지 버블한다.
        # 자식 Esc(None)는 부모를 닫지 않아 부모 메뉴로 복귀한다.
        # 부모 메뉴 영역 + 고른 행 y 를 앵커로 넘겨 자식이 우측에 캐스케이드로 펼친다.
        anchor = None
        try:
            reg = self.query_one("#menu").region
            row_y = anchor_item.region.y if anchor_item is not None else reg.y
            anchor = (reg.x, reg.right, row_y)
        except Exception:
            anchor = None                        # 영역 미정 → 자식은 중앙(폴백)
        def back(result):
            # 호버 전환(_hover_switch_from_child)으로 닫힌 거라면 result 무시하고 새
            # 그룹 서브메뉴를 연다 — 자식은 이미 unmount 됐고 _menu_screen 이 부모로
            # 복원된 상태라 새 자식의 _prev_menu 가 다시 부모로 잡힌다.
            pend = self._pending_switch
            self._pending_switch = None
            if pend is not None:
                self._open_group(pend, self._group_item_widget(pend))
                return
            if result:
                self.dismiss(result)
        self.app.push_screen(
            MenuScreen(entries=self._group_items(g),
                       title=self._group_label(g), anchor=anchor, group=g),
            back)

    def _group_item_widget(self, g):
        try:
            return self.query_one(f"#g_{g}")
        except Exception:
            return None

    def _group_item_at(self, x, y):
        """화면좌표 (x,y) 위의 그룹 항목 → (index, group, item), 없으면 None."""
        try:
            lv = self.query_one("#menu", ListView)
        except Exception:
            return None
        for idx, item in enumerate(lv.children):
            iid = getattr(item, "id", "") or ""
            if iid.startswith("g_") and item.region.contains(x, y):
                return (idx, iid[2:], item)
        return None

    def _hover_switch_from_child(self, child, x, y):
        """열린 서브메뉴(child)의 마우스가 부모의 **다른** 그룹 항목 위로 호버하면,
        현재 서브메뉴를 닫고 그 그룹 서브메뉴를 연다(요청). 같은 그룹이면 유지."""
        if getattr(child, "_switching", False):
            return                           # 이미 전환 진행 중 — 중복 dismiss 방지
        hit = self._group_item_at(x, y)
        if hit is None:
            return
        idx, g, _item = hit
        try:                                 # 부모 하이라이트도 호버 그룹으로 이동
            self.query_one("#menu", ListView).index = idx
        except Exception:
            pass
        if g == getattr(child, "_group", None):
            return                           # 이미 열린 그 서브메뉴 — 유지
        child._switching = True
        self._pending_switch = g
        child.dismiss(None)

    def on_mouse_move(self, event: events.MouseMove):
        # 서브메뉴(앵커 캐스케이드)일 때만: 부모의 다른 그룹 위로 호버 → 전환(요청).
        parent = getattr(self, "_prev_menu", None)
        if self._anchor is None or not isinstance(parent, MenuScreen):
            return
        parent._hover_switch_from_child(self, event.screen_x, event.screen_y)

    def on_list_view_selected(self, event):
        iid = event.item.id or ""
        if iid.startswith("sep_"):
            return                       # 구분선 — 안전 가드(disabled 라 보통 발화 안 됨)
        if iid.startswith("g_"):
            self._open_group(iid[2:], event.item)
            return
        key = iid[2:]                    # "m_<key>"
        if key in MENU_TOGGLES:
            # 토글: 메뉴를 닫지 않고 명령만 보낸 뒤 라벨을 낙관적으로 갱신.
            # ESC 로만 닫는다. 실제 상태는 status 회신 때 refresh_labels 로 확정.
            self.app._run_menu_action(key)
            self._optim[key] = not self._toggle_state(key)
            lab, base = self._labels[key]
            lab.update(self._fmt(key, base))
        else:
            self.dismiss(key)

    def on_click(self, event: events.Click):
        # 박스(#menu) 바깥(백드롭)을 클릭하면 메뉴를 닫는다(PluginManagerScreen·
        # InfoScreen 과 동일한 inside-box 판정). 박스 안(항목/그룹/구분선) 클릭은
        # 그대로 두어 on_list_view_selected 가 동작한다. 캐스케이드 자식 메뉴는 각자
        # 모달이라 자신의 #menu 밖 클릭 시 dismiss(None) → 부모 메뉴로 복귀(Esc 와 동일).
        w = getattr(event, "widget", None)
        inside_box = False
        while w is not None:
            if getattr(w, "id", None) == "menu":
                inside_box = True
                break
            w = w.parent
        if not inside_box:
            event.stop()
            self.dismiss(None)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class PluginManagerScreen(ModalScreen):
    """플러그인 관리 팝업(docs/internal/PLUGIN_MANAGER_SCENARIO.md) — 설치된(레지스트리 발견)
    플러그인을 이름·설명·상태([x]켜짐/[ ]꺼짐)로 나열하고, Space/Enter 로 토글한다.
    토글은 set_plugin_enabled cmd 를 서버에 보내 opts.json 에 영속하고, 서버가 전 클라에
    새 disabled 집합을 방송해 명령/훅이 즉시 빠지거나 돌아온다. 발효 안내: 좌하단 정보
    클러스터·서버 믹스인 기여 플러그인은 완전 정리에 서버 재시작이 필요(§5)."""
    CSS = """
    PluginManagerScreen { align: center middle; }
    #plgbox { width: 76; max-width: 92%; height: auto; max-height: 90%;
              border: round $accent; background: $panel; padding: 0 1; }
    #plgtitle { width: 100%; height: 1; content-align: center middle;
                text-style: bold; }
    #plglist { height: auto; max-height: 20; }
    #plghint { width: 100%; height: auto; padding: 1 0 0 0; color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        self._rows = {}   # name -> (Label, description)
        with Vertical(id="plgbox"):
            yield Label(i18n.t("plugins.title"), id="plgtitle")
            items = []
            plug = getattr(self.app, "plugins", None)
            overview = plug.plugin_overview() if plug else []
            for name, desc, _cat, enabled in overview:
                lab = Label(self._fmt(name, desc, enabled), markup=False)
                self._rows[name] = (lab, desc)
                items.append(ListItem(lab, id=f"plg_{name}"))
            yield ListView(*items, id="plglist")
            yield Label(i18n.t("plugins.hint"), id="plghint", markup=False)

    def _enabled(self, name):
        plug = getattr(self.app, "plugins", None)
        return bool(plug) and name not in plug.disabled

    def _fmt(self, name, desc, enabled=None):
        if enabled is None:
            enabled = self._enabled(name)
        box = "[x]" if enabled else "[ ]"
        return f"{box} {name:<24}{('— ' + desc) if desc else ''}"

    def on_mount(self):
        self.query_one(ListView).focus()
        self.app._plugin_screen = self   # status 회신 시 라벨 확정용

    def on_unmount(self):
        if getattr(self.app, "_plugin_screen", None) is self:
            self.app._plugin_screen = None

    def refresh_labels(self):
        """서버 status(새 disabled 집합) 도착 시 호출 — 라벨을 실제 상태로 확정."""
        for name, (lab, desc) in getattr(self, "_rows", {}).items():
            lab.update(self._fmt(name, desc))

    def _toggle(self, name):
        if name not in self._rows:
            return
        new_on = not self._enabled(name)
        self.app.send_cmd("set_plugin_enabled", name=name, on=new_on)
        # 낙관적 즉시 반영(서버 status 회신 시 refresh_labels 가 확정).
        lab, desc = self._rows[name]
        lab.update(self._fmt(name, desc, new_on))

    def on_list_view_selected(self, event):
        if event.item is not None and event.item.id:
            self._toggle(event.item.id[len("plg_"):])

    def on_click(self, event: events.Click):
        # 박스(#plgbox) 바깥(백드롭)을 클릭/터치하면 팝업을 닫는다(InfoScreen·토큰
        # 팝업과 동일한 inside-box 판정). 박스 안(목록 항목 등) 클릭은 그대로 두어
        # on_list_view_selected 토글이 동작한다.
        w = getattr(event, "widget", None)
        inside_box = False
        while w is not None:
            if getattr(w, "id", None) == "plgbox":
                inside_box = True
                break
            w = w.parent
        if not inside_box:
            event.stop()
            self.dismiss(None)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key == "space":
            lv = self.query_one(ListView)
            item = lv.highlighted_child
            if item is not None and item.id:
                event.stop()
                self._toggle(item.id[len("plg_"):])


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
    그 외 키를 누르면 닫힌다. 긴 줄은 여러 줄로 줄바꿈."""
    # 항목 Label 을 컨테이너 폭(1fr)에 맞춰 줄바꿈(text-wrap 기본 wrap) → 긴
    # 줄이 잘리지 않고 여러 줄로 펼쳐진다. ListItem 은 height:auto 로 늘어남.
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
       REC/토큰 팝업이 InfoScreen 을 공유하므로 모두 적용된다. */
    #info { width: 100%; height: auto; }
    #info ListItem { height: auto; }
    #info ListItem Label { width: 1fr; }
    """

    # 방향키(내비게이션) — 닫지 않고 ListView 선택을 옮긴다.
    _NAV_KEYS = ("up", "down", "pageup", "pagedown", "home", "end")

    def __init__(self, lines, title="info", hide_key=None, hide_cb=None,
                 max_width=None, wrap_hang=True, center=False, tick_cb=None):
        super().__init__()
        self._lines = lines
        self._title = title
        # 특정 키(hide_key)를 누르면 hide_cb 를 부르고 닫는다(원격제어 [r] 등).
        self._hide_key = hide_key
        self._hide_cb = hide_cb
        # 박스 가로 최대폭 오버라이드(없으면 CSS 기본 66).
        self._max_width = max_width
        # center=True 면 헤더에서 여는 팝업의 기본 상단 정렬 대신 **화면 중앙**에 띄운다
        # (version 팝업 등 헤더 앵커가 없는 호출). tick_cb 가 있으면 1초마다 그 콜러블이
        # 돌려주는 새 줄로 표시를 갱신한다(업타임처럼 계속 증가하는 값).
        self._center = center
        self._tick_cb = tick_cb
        # 깜빡임 방지: 표시 줄(ListItem id → 텍스트) 캐시. _tick 이 달라진 라벨만 갱신.
        self._disp_cache = {}
        # §10-A #7: 긴 줄(URL 등)을 박스 폭에 맞춰 행잉-인덴트 하드 줄바꿈한다
        # (번호 정렬 보존). 폭은 마운트 후 측정하므로 call_after_refresh 로 재구성.
        self._wrap_hang = wrap_hang
        # §10-A #8: nav 에서 건너뛸(구분선/빈) 표시 줄 인덱스 — 마지막 항목서 ↓ 시
        # 구분선을 건너뛰어 footer 로 바로 점프.
        self._skip = set()

    def compose(self) -> ComposeResult:
        # markup=False: 임의 텍스트의 대괄호가 마크업으로 사라지지 않게.
        with Vertical(id="infobox"):
            with Horizontal(id="infohead"):
                yield Label(self._title, id="infotitle", markup=False)
                # markup=False: "[x]" 가 Textual 마크업 태그로 해석돼 사라지지
                # 않게(그러면 배경색만 남고 글자가 안 보인다).
                yield Label("[x]", id="infoclose", markup=False)  # 닫기 버튼
            yield ListView(*[ListItem(Label(ln, markup=False))
                             for ln in self._lines] or
                           [ListItem(Label(i18n.t("screen.empty")))], id="info")

    @staticmethod
    def _is_skip(line):
        s = (line or "").strip()
        return (not s) or set(s) <= _DIVIDER_CHARS

    def _compute_skip(self, lines):
        return {i for i, ln in enumerate(lines) if self._is_skip(ln)}

    def on_mount(self):
        if self._max_width is not None:        # 가로 넓히기
            self.query_one("#infobox").styles.max_width = self._max_width
        if self._center:                       # 상단 정렬 대신 화면 중앙
            self.styles.align_vertical = "middle"
            self.query_one("#infobox").styles.margin = 0
        if self._tick_cb is not None:          # 업타임 등 매 초 갱신
            self.set_interval(1.0, self._tick)
        lv = self.query_one(ListView)
        lv.focus()
        if self._wrap_hang:
            # 폭이 정해진 뒤(레이아웃 후) 하드 줄바꿈으로 목록을 다시 만든다.
            self.call_after_refresh(self._rewrap)
            return
        self._skip = self._compute_skip(self._lines)

    def _wrap_lines(self, lines):
        """현재 박스 폭으로 줄들을 행잉-인덴트 하드 줄바꿈해 **표시 줄** 목록을 만든다
        (§10-A #7). wrap 비활성이면 원본 그대로."""
        if not self._wrap_hang:
            return list(lines)
        w = self.query_one("#infobox").size.width or (self._max_width or 66)
        inner = max(20, w - 4 - 2)             # 테두리(2)+패딩(2)+스크롤바(2)
        disp = []
        for ln in lines:
            disp.extend(_hangwrap(ln, inner))
        return disp

    def _tick(self):
        """tick_cb 가 돌려주는 새 줄로 표시를 갱신한다(업타임 카운트업). **깜빡임 방지**:
        새 줄을 같은 폭으로 줄바꿈해(표시 줄 기준) 줄 수가 그대로면 **달라진 라벨만**
        in-place 로 고쳐 포커스/스크롤·정적 줄을 건드리지 않는다. 줄 수 자체가 바뀔
        때만(폭 변화 등) 전체를 재구성한다 — clear()+extend() 는 한 프레임 비워 깜빡이므로
        매 초 호출하지 않는다."""
        if self._tick_cb is None:
            return
        self._lines = self._tick_cb()
        disp = self._wrap_lines(self._lines)
        lv = self.query_one(ListView)
        items = list(lv.children)
        if len(items) != len(disp):
            self.run_worker(self._rewrap())
            return
        self._skip = self._compute_skip(disp)
        for item, ln in zip(items, disp):
            if ln == self._disp_cache.get(id(item)):
                continue                       # 안 바뀐 줄은 다시 안 그린다
            self._disp_cache[id(item)] = ln
            try:
                item.query_one(Label).update(ln)
            except Exception:
                pass

    async def _rewrap(self):
        """박스 실제 폭을 재서 각 줄을 행잉-인덴트로 하드 줄바꿈하고 목록을 재구성
        (§10-A #7)."""
        lv = self.query_one(ListView)
        disp = self._wrap_lines(self._lines)
        self._skip = self._compute_skip(disp)
        await lv.clear()
        await lv.extend([ListItem(Label(d, markup=False)) for d in disp]
                        or [ListItem(Label(i18n.t("screen.empty")))])
        # 새 ListItem 기준으로 깜빡임-방지 캐시를 재설정(in-place 갱신 비교 기준).
        self._disp_cache = {id(item): d
                            for item, d in zip(lv.children, disp)}

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
        # (백드롭) 클릭이면 닫음(§10 #13 — REC/토큰 팝업 공통).
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
            # 이동 후 구분선/빈 줄은 같은 방향으로 건너뛴다(§10-A #8).
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


class _ItTabConnector(Widget):
    """InfoTabsScreen 탭 줄과 본문 사이의 '노트북 탭' 연결선(사용자 요청 2026-06-20 —
    토큰 사용량 팝업과 같은 탭 모양으로 통일). 한 줄짜리 가로 규칙(─, 박스 테두리색
    accent)을 그리되 **활성 탭 아래 구간만** ▀(상단 반블록)로 덮어, 활성 탭이 아래
    본문으로 열려 이어지는 노트북 탭처럼 보이게 한다 — 토큰 팝업 _TkTabConnector 와
    같은 기법. 활성 탭 위치는 매 렌더에 그 Label(#ittab_{_ti})의 화면 region 으로
    읽어, 폭 변화·탭 전환에 자동으로 따라온다(레이아웃 후 region 이 권위)."""

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        rule_st = Style(color=theme_color(self, "accent"))
        if w <= 0:
            return Strip([], 0)
        # 활성 탭 Label 의 가로 구간(이 위젯 기준 상대 x)을 화면 region 으로 구한다.
        bx0 = bx1 = -1
        scr = self.screen
        ti = getattr(scr, "_ti", None)
        lbl = None
        if ti is not None:
            try:
                lbl = scr.query_one(f"#ittab_{ti}", Label)
            except Exception:
                lbl = None
        if lbl is not None and getattr(lbl, "display", True):
            try:
                bx0 = lbl.region.x - self.region.x
                bx1 = bx0 + lbl.region.width
            except Exception:
                bx0 = bx1 = -1
        bx0 = max(0, bx0)
        bx1 = min(w, bx1)
        if bx1 > bx0:
            # ─(얇은 중앙선)와 ▀(상단 반블록)는 같은 accent 라도 글자가 달라 '활성
            # 탭 아래만 위로 열린' 노트북 모양으로 구분된다.
            segs = []
            if bx0 > 0:
                segs.append(Segment("─" * bx0, rule_st))
            segs.append(Segment("▀" * (bx1 - bx0), rule_st))
            if bx1 < w:
                segs.append(Segment("─" * (w - bx1), rule_st))
        else:
            segs = [Segment("─" * w, rule_st)]
        return Strip(segs).adjust_cell_length(w, rule_st)


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
    /* 플랫 탭(외곽선 없음, 창 탭바와 동일): 각 탭은 2행 라벨(빈 윗행 + 이름줄)이고
       활성 탭은 이름줄을 배경 반전으로 강조한다. head 는 2행, [x] 는 우측 끝(1fr 여백). */
    #ithead { width: 100%; height: 2; }
    #ittabs { width: auto; height: 2; }
    #ittabs Label { width: auto; height: 2; }   /* 탭 하나(2행, 클릭 대상) */
    #itgap { width: 1fr; height: 2; }
    /* 탭 줄과 본문 사이 노트북 연결선(_ItTabConnector) — 활성 탭이 본문으로 열려
       이어지게(토큰 사용량 팝업과 같은 모양, 요청 2026-06-20). 박스 테두리(round
       accent)와 같은 색 ─ 가로선이 좌우 테두리로 이어진다. */
    #itconn { width: 100%; height: 1; }
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
    /* 키 안내 줄(요청 2026-06-20): 종전엔 border_subtitle 로 바닥 테두리선 위에
       글자를 얹어 선이 끊겼다 → 토큰 사용량 팝업(#tkhint)처럼 **박스 안쪽 한 줄**
       로 내려, 그 아래 테두리선이 끊김 없이 그어지게 한다. height:1 고정이라 탭마다
       길이가 달라도 팝업 높이는 변하지 않는다. */
    #ithint { width: 100%; height: 1; color: $text-muted; }
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
        # 탭을 오가도 팝업 높이가 변하지 않게(요청), 본문 행 수를 전 탭 중 최대치로
        # 고정한다. 행 수 = 액션버튼 수 + 내용 줄 수. _render_tab 이 짧은 탭을 빈 줄로
        # 이 목표까지 채운다(긴 탭은 max-height 95% 안에서 스크롤).
        self._body_rows = max(
            (len(self._actions.get(i, [])) + max(1, len(lines))
             for i, (_n, lines) in enumerate(self._tabs)), default=1)

    def compose(self) -> ComposeResult:
        with Vertical(id="itbox"):
            with Horizontal(id="ithead"):
                with Horizontal(id="ittabs"):     # 플랫 탭 그룹(외곽선 없음)
                    for i, (name, _l) in enumerate(self._tabs):
                        yield Label("", id=f"ittab_{i}", markup=True)
                yield Label("", id="itgap")        # [x] 를 우측 끝으로 미는 여백
                yield Label("[x]", id="itclose", markup=False)
            # 노트북 연결선: 활성 탭이 아래 본문으로 열려 이어지게(요청 2026-06-20).
            yield _ItTabConnector(id="itconn")
            yield ListView(id="itbody")
            yield Label(i18n.t("screen.close"), id="itclosebtn",
                        markup=False)  # 하단 닫기(§10-A #6)
            # 키 안내 줄 — 박스 안쪽 마지막 줄(바닥 테두리선은 그 아래로 끊김 없이).
            yield Static("", id="ithint", markup=False)

    async def on_mount(self):
        await self._render_tab()
        self.query_one(ListView).focus()

    def _render_tabbar(self):
        # 각 탭 라벨 갱신(현재 탭 강조). 클릭 대상이라 탭마다 별도 위젯이다.
        # 플랫 탭(외곽선 없음): 2행 — 빈 윗행+이름줄. 활성 탭은 이름줄을 배경 반전으로
        # 강조, 비활성 탭은 흐리게 둔다. ←→ 포커스가 [x](=N)면 활성 탭은 평소 강조,
        # [x] 는 -focus 로 강조한다.
        n = len(self._tabs)
        for i, (name, _l) in enumerate(self._tabs):
            lbl = self.query_one(f"#ittab_{i}", Label)
            lbl.update(self._tab_markup(theme_color(self, "accent"), name,
                                        active=(i == self._ti),
                                        focused=(self._sel == i)))
        self.query_one("#itclose", Label).set_class(self._sel == n, "-focus")
        # 노트북 연결선 다시 그리기 — 활성 탭이 바뀌면 ▀ 다리도 새 탭 아래로 옮긴다.
        try:
            self.query_one("#itconn").refresh()
        except Exception:
            pass

    @staticmethod
    def _tab_markup(accent, name, active, focused):
        """플랫 탭 한 칸(2행) 마크업 — 외곽선 없는 납작한 모양(창 탭바와 동일, 요청).
        활성=이름줄을 accent(박스 테두리·노트북 연결선과 **동색**) 배경+흰 글자로
        강조, 포커스 시 굵게. 비활성=이름만 흐리게. 윗행은 비워 우측 [x](head 첫 행)와
        2행 높이를 맞추고 이름은 아랫행에 둔다. 종전엔 reverse(텍스트색 반전)라 아래
        연결선(accent)과 색이 어긋났다 → 토큰 팝업(_TkTabConnector)처럼 활성 탭도
        accent 로 맞춰 탭이 그 라인으로 열려 이어지게 한다(사용자 요청 2026-06-20)."""
        label = " " + name + " "
        if active:
            # 전경(white)을 배경(on accent)보다 **먼저** 써야 한다 — Textual Content
            # 마크업은 `on <색> white` 처럼 색이 on 뒤에 또 오면 그 뒷색을 배경으로
            # 잘못 묶어(white 가 배경이 돼 탭이 하얗게) 아래 연결선과 색이 어긋난다.
            style = f"b white on {accent}" if focused else f"white on {accent}"
        else:
            style = "dim"
        return f"\n[{style}]{label}[/]"

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
        body = list(lines or [i18n.t("screen.empty")])
        # 탭 전환 시 팝업 높이가 변하지 않게 짧은 탭은 빈 줄로 목표 행 수까지 채운다
        # (요청). 목표 = 전 탭 중 최대 행 수(액션 토글로 줄 수가 늘면 그만큼 갱신).
        target = max(self._body_rows, len(acts) + len(body))
        body += [""] * (target - len(acts) - len(body))
        items += [ListItem(Label(ln, markup=False)) for ln in body]
        await lv.extend(items)
        # 커서 초깃값은 첫 내용 줄(액션 버튼 위가 아니라) — 정보가 먼저 보이게.
        if items:
            lv.index = len(acts) if lines else 0
        # 키 안내를 박스 안쪽 마지막 줄(#ithint)에 둔다(요청 2026-06-20). 종전엔
        # border_subtitle 로 바닥 테두리선 위에 얹어 선이 끊겼다 → 토큰 사용량 팝업
        # (#tkhint)처럼 안쪽 한 줄로 내려 그 아래 테두리선이 끊김 없이 그어지게 한다.
        sub = i18n.t("screen.infotabs_sub")
        acts = self._actions.get(self._ti)
        if acts:                        # 이 탭의 동작들(예: [c] 캡처 토글 · [o] 폴더)
            sub = " · ".join(a[1] for a in acts) + " · " + sub
        self.query_one("#ithint", Static).update(sub)

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


# /usage 한도 막대의 **빈(잔여) 트랙** 글자 — 채움 '█' 과 대비되는 연한 음영이라
# 색이 없어도 사용/잔여를 구분한다(usage_bar_lines 비-right_align = usage-panel·
# TokenLogScreen [한도]). right_align(usage-view overlay)은 호출부가 track_char 로
# 따로 준다(표시단이 그 글자만 회색으로 칠함).
_USAGE_EMPTY_TRACK = "░"


def usage_bar_lines(usage, width=80, age_sec=None, right_align=False,
                    track_char=" ", row_gap=False):
    """Claude `/usage` 한도 dict(session·week_all·week_sonnet)를 보기 좋은 표시
    줄 목록으로 만든다. 각 줄: 라벨(10셀 패딩) + 막대 + % + 리셋(요약, 타임존 생략).
    데이터가 없으면 None. TokenLogScreen 의 한도 섹션과 자동 /usage 팝업이 공유한다.

    age_sec: 실측 경과(초, S6 T3). 2분 이상 묵었으면 마지막에 'N분 전 실측'을 붙여
    stale 임을 알린다 — 실측이 주 표시로 승격되면서 묵은 값을 현재값으로 오독하지
    않게 하는 표시측 대응(stale 스냅샷 혼동 방지).

    right_align: 켜면 막대를 트랙 폭(barw)으로 채워 행마다 리셋 시작 열을 맞추고,
    % 숫자를 막대 바로 옆이 아니라 **줄 오른쪽 끝(width)** 에 우측정렬한다(리셋은
    막대 뒤). usage-view 플러그인 팝업/오버레이가 켠다 — 기본 False 라 기존 소비자
    (usage-panel·TokenLogScreen)의 표시는 그대로다(opt-in).

    track_char: 막대의 **빈 부분**(채움 뒤 트랙)을 채우는 글자. 기본 ' '(공백 →
    배경과 동일, 종전 동작). 호출부가 회색 트랙을 그리려고 구분 글자(예 '░')를 주면
    빈 칸을 그 글자로 채워, 표시측이 그 글자만 회색으로 색칠할 수 있게 한다(요청:
    막대=흰색·빈 부분=회색으로 배경과 구분). right_align 일 때만 의미가 있다(빈 트랙이
    그 분기에서만 채워진다).

    row_gap: 켜면 막대 행들 **사이에 빈 줄 1개**를 넣어 시각적으로 분리한다(요청
    2026-06-18, [한도] 뷰). 첫 막대 앞·계정/신선도 줄엔 안 넣는다. 기본 False."""
    if not isinstance(usage, dict):
        return None
    barw = 24 if width >= 80 else (16 if width >= 60 else 8)
    # 표시할 한도(데이터 있는 것)만 먼저 모아 **라벨 폭을 통일**한다 — 라벨 길이가
    # 달라(예: 'Week Sonnet' 11셀 vs 'Week all' 8셀) 막대 시작 열이 행마다 어긋나던
    # 것을, 가장 긴 라벨 + 1칸으로 모두 패딩해 **모든 막대의 왼쪽 시작을 같은 열**에
    # 맞춘다(요청 2026-06-18 — 종전 고정 10셀은 11셀 라벨에서 막대가 한 칸 밀렸다).
    entries = []
    for key, name in (("session", i18n.t("usage.session_5h")),
                      ("week_all", i18n.t("usage.week_all")),
                      ("week_sonnet", i18n.t("usage.week_sonnet"))):
        d = usage.get(key)
        if isinstance(d, dict) and d.get("pct") is not None:
            entries.append((name, d))
    label_w = max((sum(_char_cells(c) for c in nm) for nm, _ in entries),
                  default=0)
    rows = []
    for name, d in entries:
        pct = d["pct"]
        gauge = bar(pct, 100, barw)
        # 가장 긴 라벨 + 1칸 → 모든 라벨이 같은 폭(막대 시작 열 통일), 최소 1칸 간격.
        label = name + " " * max(1, label_w + 1 - sum(_char_cells(c) for c in name))
        reset = d.get("reset")
        # 타임존 괄호는 자리 절약 위해 생략.
        reset_txt = ("↻" + reset.split(" (")[0].strip()) if reset else ""
        if right_align:
            # 막대를 트랙 폭으로 채워(공백) 리셋 시작 열을 행마다 맞추고, % 숫자는
            # 줄 오른쪽 끝(width)에 우측정렬한다 — 막대/리셋과 % 사이를 공백으로 채움.
            gauge = gauge + track_char * max(0, barw - len(gauge))
            tail = f"{pct:>3}%"
            body = f"{label}{gauge}  {reset_txt}".rstrip()
            gap = (width - sum(_char_cells(c) for c in body)
                   - sum(_char_cells(c) for c in tail))
            line = body + " " * max(1, gap) + tail
        else:
            # 전체 막대를 그려 **사용(채움)·잔여(빈칸)를 한눈에 구분**한다(요청
            # 2026-06-16, Claude /usage 표시처럼). bar() 는 채운 부분만 주므로 남는
            # 트랙을 '░'(연한 음영)로 채워 항상 전체 폭(barw)을 그린다 — 채움 '█' vs
            # 빈칸 '░' 라 색 없이도 어디까지 찼는지/전체 중 얼마 남았는지 보인다
            # (종전엔 채운 블록만 그려 전체·잔여가 안 보였다). pct≥100 이면 트랙이
            # 전부 채워져 가득 찬 막대가 된다.
            full_gauge = gauge + _USAGE_EMPTY_TRACK * max(0, barw - len(gauge))
            # % 뒤에 '사용/used' 를 명시한다(2026-06-12 사용자 보고): 방향 라벨이
            # 없으면 잔여 표기와 섞여 다른 값처럼 읽혔다 — Claude /usage 의 "N% used"
            # 와 동일 표기. footer 5h 도 같은 사용률로 통일됐다(clientstatus
            # claude.limit_used — 모든 표면이 같은 방향·같은 숫자).
            line = f"{label}{full_gauge} {pct:>3}% {i18n.t('usage.used')}"
            if reset_txt:
                line += "  " + reset_txt
        # 막대 행 사이 빈 줄 1개(row_gap) — 첫 막대 앞엔 안 넣는다.
        if row_gap and rows:
            rows.append("")
        rows.append(line)
    # 그림자 /usage 세션의 계정(일치 확인용). 키가 있을 때만 — 폰 앱과 다른 계정이면
    # 한도가 실제로 달라지므로 눈으로 대조하라고 표시한다. 신호 못 잡으면 '미확인'.
    if rows and "account" in usage:
        # 전체 이메일(account_full, 프로브가 라이브로 실어 보냄)을 우선 표시하고, 없으면
        # 별칭(account, DB 영속·재시작 직후 폴백)으로. 사용자 본인 화면이라 줄이지 않고
        # 전체를 보인다(요청·footer claude_account_full 과 동일 방침).
        acct = usage.get("account_full") or usage.get("account")
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
        # 인자 추천 모드: 완성된 arghist 명령(remote-attach 등) 뒤 인자 자리에서
        # 이전 입력 인자를 후보로 추천한다. _arg_cmd 는 그때 사용자가 친 첫 토큰(줄 재조립용).
        self._arg_mode = False
        self._arg_cmd = None
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
        raw = self.query_one(Input).value
        # 인자 추천 상태를 매번 초기화(명령 이름 치는 중이면 명령-이름 후보로 동작).
        self._arg_mode = False
        self._arg_cmd = None
        # 완성된 arghist 명령(remote-attach 등) 뒤 인자 자리면 이전 입력 인자를 추천한다.
        if self._arg_candidates(raw, lbl):
            return
        s = raw.strip()
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
            # 고르지 말고 현재 맥락에 맞는 명령을 먼저 하이라이트한다(단일 패널서 pane-
            # scoped 명령 강등).
            matches = self._context_rank(matches)
            # 관련도 정렬(마지막=우선): 단어 접두 일치를 중간 부분일치보다 위에 둔다.
            # 예: 'esc' → send-escape('escape' 단어가 esc 로 시작)가 coalesce-repaints
            # (중간 'esc')보다 위에 온다(요청). send-escape 는 pane-scoped 라 _context_rank
            # 가 강등했지만, 같은 질의에 더 적합하므로 관련도가 그 강등을 되돌린다. 같은
            # 관련도 안에서는 _context_rank 순서(맥락)가 보존된다(rename-tab 우선 유지).
            matches = self._relevance_rank(matches, qs)
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

    def _arg_candidates(self, raw, lbl):
        """완성된 arghist 명령(remote-attach 등) 뒤 **인자 자리**면 이전에 입력한 인자를
        후보(#pcand)로 추천한다(사용자 요청). 인자 자리로 판단되면(명령 토큰 + 그 뒤
        공백/부분인자) True 를 돌려 명령-이름 후보 매칭을 막는다. 추천이 비어도 True —
        그땐 후보를 숨기고 _refresh_hint 가 인자 밑줄(____)을 그린다. 부분 인자가 있으면
        prefix 로 거른다(대소문자 무시). 최근 입력이 앞에 온다(_arghist_list 순서)."""
        stripped = raw.lstrip()
        if not stripped:
            return False
        first = stripped.split(None, 1)[0]
        # 명령 토큰 뒤에 공백(또는 인자)이 있어야 '인자 자리'. 아직 명령 이름 치는 중이면 패스.
        if len(stripped) <= len(first):
            return False
        app = getattr(self, "app", None)
        canon = app._arghist_canon(first) if app else None
        if not canon:
            return False
        self._arg_mode = True
        self._arg_cmd = first
        ap = stripped[len(first):].strip()       # 지금까지 친 부분 인자(없으면 "")
        apl = ap.lower()
        hist = app._arghist_list(first)
        if ap:
            matches = [a for a in hist if a.lower().startswith(apl)]
            # 정확히 하나이고 입력과 동일하면 더 제안할 게 없음(명령-이름 가드와 동일).
            if len(matches) == 1 and matches[0].lower() == apl:
                matches = []
        else:
            matches = list(hist)
        self._cand = [(a, i18n.t("screen.arg_recent")) for a in matches]
        self._sel = 0
        if not self._cand:
            self._cand_shown = False
            lbl.display = False
        else:
            self._cand_shown = True
            lbl.display = True
            self._render_cands()
        return True

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

    def _relevance_rank(self, matches, qs):
        """후보를 질의 관련도로 안정 정렬한다(_context_rank **다음**, 즉 최종 우선
        정렬). 같은 관련도 안에서는 직전(=_context_rank 적용 후) 순서를 보존하므로 맥락
        정렬이 동률 안에서 살아 있다. qs 는 norm_sep 된 질의(구분자 제거). 관련도(작을수록 위):
          0 정확 일치 · 1 이름 전체 접두 · 2 단어 접두(이름의 한 단어가 qs 로 시작) ·
          3 중간 부분일치. 예: 'esc' → send-escape('escape' 단어가 esc 로 시작 = 2)가
          coalesce-repaints(중간 'esc' = 3)보다 위에 온다(요청)."""
        if len(matches) < 2:
            return matches

        def tier(name):
            nl = name.lower()
            nn = norm_sep(nl)
            if nn == qs:
                return 0
            if nn.startswith(qs):
                return 1
            if any(w.startswith(qs) for w in re.split(r"[-_ ]+", nl) if w):
                return 2
            return 3
        return sorted(matches, key=lambda m: tier(m[0]))

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
        # 인자 추천이면 'cmd arg' 로 채운다(뒤 공백 없음 — 완성된 인자라 Enter 로 실행).
        # 명령 이름 후보면 'name ' 으로 채워(공백) 인자 입력을 이어가게 한다.
        inp.value = f"{self._arg_cmd} {name}" if self._arg_mode else name + " "
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
        # 토글/선택지를 한 줄에 나열하고 강조된 항목만 reverse 로 표시. 양옆 ‹ › 는
        # ←→(또는 ↑↓)로 고르고 Enter 로 실행하는 토글 UI 임을 알리는 어포던스(요청).
        parts = []
        for i, (disp, _v) in enumerate(self._choices):
            d = self._esc(i18n.t(disp))   # §6 ⑤ 선택지 라벨 번역(키=원문)
            parts.append(f"[reverse] {d} [/reverse]" if i == self._choice_sel
                         else f"[dim] {d} [/dim]")
        self._set_hint("[dim]‹[/dim] " + "  ".join(parts) + " [dim]›[/dim]")

    def _run_choice(self):
        """강조된 토글/선택지로 즉시 실행(Enter). 값이 비면(예: '토글') 인자 생략."""
        name = self._hint_cmd
        val = self._choices[self._choice_sel][1]
        self.dismiss(f"{name} {val}" if val else name)

    def on_input_submitted(self, event):
        # 후보가 떠 있으면 Enter 는 강조된 후보를 입력에 채우고(실행하지 않음),
        # 그 다음 Enter 로 실제 실행한다. 단, 입력이 **이미 강조 후보와 정확히 일치**
        # 하면(다시 채워도 무변화) 바로 실행한다 — 그러지 않으면 명령 이름이 다른 명령의
        # 부분문자열일 때(예: 'help' ⊂ 'mouse-help') 후보가 영영 안 사라져(_refresh_cands
        # 의 'len==1 동일' 가드 미발동) Enter 가 실행으로 못 넘어가 영영 막힌다.
        # 인자 추천 모드: Enter 는 **실행**(채우지 않음). 인자를 아직 안 쳤으면 강조된
        # 추천으로 실행, 부분 인자를 쳤으면 친 그대로 실행(오선택 방지 — 추천은 Tab 으로만
        # 채운다). 명령-이름 후보(아래)와 달리 인자는 자유 텍스트라 Enter 를 가로채지 않는다.
        if self._purpose == "command" and self._arg_mode and self._cand_shown and self._cand:
            inp = self.query_one(Input)
            stripped = inp.value.lstrip()
            first = stripped.split(None, 1)[0] if stripped else ""
            ap = stripped[len(first):].strip()
            if not ap:
                self.dismiss(f"{self._arg_cmd} {self._cand[self._sel][0]}")
            else:
                self.dismiss(inp.value)
            return
        if self._purpose == "command" and self._cand_shown and self._cand:
            name = self._cand[self._sel][0]
            cur = self.query_one(Input).value.strip().lower()
            if norm_sep(cur) == norm_sep(name.lower()):
                self.dismiss(self.query_one(Input).value)
                return
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
        # 토글/선택지 모드: ←→·↑↓ 로 강조 이동, Enter 는 submit 에서 실행. 화면 on_key 는
        # 버블 경로상 App 의 바인딩 검사(_check_bindings)보다 먼저 도므로 여기서 event.stop()
        # 하면 Input 의 cursor_left/right 바인딩이 발동하지 않는다(좌우로 선택지 이동, 요청).
        if self._purpose == "command" and self._choices:
            if event.key in ("up", "left"):
                event.stop()
                self._choice_sel = (self._choice_sel - 1) % len(self._choices)
                self._render_hint()
            elif event.key in ("down", "right"):
                event.stop()
                self._choice_sel = (self._choice_sel + 1) % len(self._choices)
                self._render_hint()


class _ComposeTextArea(TextArea):
    """Claude Code 와 동일한 줄바꿈 규칙의 작성창 TextArea.

    Claude Code 프롬프트는 **Enter=전송, Shift+Enter=줄바꿈**(사용자 요청). Textual
    기본 TextArea 는 Enter 로 줄바꿈을 넣으므로, Enter 는 스크린의 전송(inject)으로
    돌리고 줄바꿈은 Shift+Enter / Ctrl+J 로 받는다. 단말이 Shift+Enter 를 LF(\\n)로
    보내면 Textual 은 `ctrl+j` 로 파싱하므로([[pytmux-shift-enter-newline]]) 둘 다
    줄바꿈으로 처리한다(native CSI-u 단말은 `shift+enter` 그대로 도착)."""
    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":                 # Enter = 전송(줄바꿈 아님)
            event.stop()
            event.prevent_default()
            self.screen.action_inject()
            return
        if event.key in ("shift+enter", "ctrl+j"):   # Shift+Enter / Ctrl+J = 줄바꿈
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


class ComposePromptScreen(ModalScreen):
    """블록 선택(Shift+방향키/Home/End/Shift+드래그)이 되는 멀티라인 작성창.

    Claude Code 등 자식 프롬프트 입력기는 범위 선택 후 수정/삭제 편집을 지원하지
    않는다. pytmux 는 자식의 논리 버퍼·커서 인덱스를 알 수 없어 그 위에 선택을
    투명하게 얹을 수 없으므로(타당성 검토 문서 A 안 비권장), **버퍼를 pytmux 가
    소유하는** 별도 작성창(Textual TextArea)을 띄워 네이티브 블록 선택 편집을
    제공한다. 완료(Enter) 시 텍스트를 dismiss 로 돌려주면 client 가 활성 패널에
    **bracketed paste 로 투입**한다(자동 제출 없음 — 끝에 Enter 를 붙이지 않는다).
    Esc 는 취소(투입 안 함). Enter=전송·Shift+Enter=줄바꿈(Claude Code 동일,
    _ComposeTextArea). 권고안 B — CLAUDE_PROMPT_BLOCK_SELECTION_FEASIBILITY.

    선택 의미론은 TextArea 가 정확히 처리하므로 셀↔논리인덱스 추정·키 합성이
    필요 없다(A 안이 깨지던 지점). 작성 중에는 Claude 인라인 기능(슬래시 메뉴·@
    자동완성·↑히스토리)을 못 쓰므로 '필요할 때 여는' 옵트인이다(ESC 모드 Insert).

    배치(사용자 요청): Claude 프롬프트는 **이전 출력을 보면서** 입력해야 하므로
    배경을 **딤하지 않는다**. 딤은 모달 CSS 가 아니라 클라이언트 `_composite` 의
    백드롭 딤(#25 — screen_stack>1 이면 패널 행을 어둡게+이모지 치환)에서 오므로,
    이 스크린은 `_no_backdrop_dim = True` 로 그 딤을 면제받는다(스크린 배경도 투명).
    박스 좌우는 **활성 패널 테두리 안쪽**에 맞추고(open_compose 가 패널 내부 x·폭을
    넘김), 입력 줄은 활성 패널 프롬프트 줄(커서 행)보다 **한 칸 아래**에 오도록
    하단에서 위로 띄워 배치한다(on_mount 의 margin). 여러 줄로 늘면 그 줄을 고정한
    채 **위쪽으로** 자란다(dock:bottom + height:auto). 우상단에 IME(한/영) 배지를
    띄워 작성 중에도 입력 모드를 보여준다(ime-indicator 의 app.ime_state 를 폴링)."""
    # 클라이언트 _composite 의 #25 백드롭 딤/이모지치환 면제 플래그(이 스크린이
    # 떠 있을 땐 뒤 패널을 어둡게 하지 않는다 — Claude 프롬프트를 보며 입력).
    _no_backdrop_dim = True
    CSS = """
    /* 딤 없음: 스크린 배경 투명 + _no_backdrop_dim → 박스 밖 패널(이전 출력)이
       원래 밝기로 그대로 보인다. */
    ComposePromptScreen { align: center bottom; background: transparent; }
    /* 바닥 도킹 + height:auto → 내용이 늘면 위쪽으로 자란다. on_mount 가 좌우(폭·
       margin-left)와 상하(margin-bottom)를 줘서 활성 패널 안쪽·프롬프트 줄에 맞춘다.
       박스 배경은 $panel — 안쪽 입력칸($surface)과 구분된다. */
    #cwrap { dock: bottom; width: 100%; height: auto; max-height: 80%;
             background: $panel; border: round $accent; padding: 0 1; }
    /* 박스 상단 한 줄: 왼쪽에 힌트(우측정렬), 오른쪽 끝에 IME 배지. */
    #ctop { width: 100%; height: 1; }
    #chint { width: 1fr; height: 1; color: $text-muted;
             content-align: right middle; }
    #cime { width: auto; height: 1; padding: 0 0 0 1; }
    /* 입력칸: 박스 배경($panel)과 다른 색($surface)으로 구분. TextArea 자체 테두리는
       끄고 #cwrap 의 round 테두리로 감싼다. 한 줄로 시작(min-height:1)해 프롬프트처럼
       보이고, 입력이 늘면 max-height 까지 위로 자란 뒤 그 이상은 TextArea 내부 스크롤. */
    #carea { width: 100%; height: auto; min-height: 1; max-height: 20;
             border: none; background: $surface; }
    """
    # Enter=전송은 _ComposeTextArea 가 처리. Ctrl+S 도 전송(대체), Esc 는 취소.
    # TextArea 는 ctrl+s·escape 를 자체 처리하지 않으므로 스크린 바인딩으로 버블링.
    BINDINGS = [
        ("ctrl+s", "inject", "send"),
        ("escape", "cancel", "cancel"),
    ]

    def __init__(self, initial: str = "", prompt_row: int | None = None,
                 pane_x: int | None = None, pane_w: int | None = None):
        super().__init__()
        self._initial = initial
        # 활성 패널 프롬프트(커서) 행(글로벌 화면 y). None 이면 그냥 바닥 도킹.
        self._prompt_row = prompt_row
        # 활성 패널 테두리 안쪽 좌측 x·폭(셀). None 이면 전체 폭.
        self._pane_x = pane_x
        self._pane_w = pane_w
        self._ime_last = None      # 배지 중복 갱신 방지용 직전 (state, show)

    def compose(self) -> ComposeResult:
        with Vertical(id="cwrap"):
            with Horizontal(id="ctop"):
                yield Label(i18n.t("compose.hint"), id="chint", markup=False)
                yield Label("", id="cime", markup=False)
            yield _ComposeTextArea(self._initial, id="carea", soft_wrap=True)

    def on_mount(self):
        ta = self.query_one(TextArea)
        ta.focus()
        # 초기 텍스트가 있으면(직전 프롬프트 불러오기 등) 커서를 문서 끝으로 둔다.
        if self._initial:
            lines = self._initial.split("\n")
            ta.move_cursor((len(lines) - 1, len(lines[-1])))
        wrap = self.query_one("#cwrap")
        H = self.app.size.height
        # 좌우: 활성 패널 테두리 안쪽에 맞춘다(폭 고정 + margin-left).
        ml = 0
        if self._pane_x is not None and self._pane_w is not None:
            wrap.styles.width = self._pane_w
            ml = self._pane_x
        # 상하: 입력 줄을 프롬프트 줄보다 **한 칸 아래**(사용자 요청)에 둔다. 박스
        # 하단 구조는 [입력 줄][하단 테두리 1행]이므로 입력 줄을 prompt_row+1 에
        # 두려면 하단 테두리가 prompt_row+2 → 바닥에서 margin-bottom 만큼 띄운다.
        mb = 0
        if self._prompt_row is not None:
            mb = H - self._prompt_row - 3
            mb = max(0, min(mb, max(0, H - 3)))   # 화면 밖/음수 방지
        wrap.styles.margin = (0, 0, mb, ml)       # (top, right, bottom, left)
        # IME 배지: 즉시 1회 + 0.2초 폴링으로 작성 중 한/영 전환 추종(ime-indicator
        # 가 app.ime_state 를 OS 실측/휴리스틱으로 갱신; 없으면 배지 비표시).
        self._refresh_ime()
        self.set_interval(0.2, self._refresh_ime)

    def _refresh_ime(self):
        """우상단 IME(한/영) 배지를 app.ime_state 에 맞춰 갱신한다(ime-indicator 의
        화면 배지와 같은 원천·색). 플러그인이 없거나 OFF 면 빈 배지."""
        try:
            lbl = self.query_one("#cime", Label)
        except Exception:
            return
        app = self.app
        show = getattr(app, "ime_show", False) and hasattr(app, "ime_state")
        state = getattr(app, "ime_state", "EN") if show else None
        if (state, show) == self._ime_last:
            return
        self._ime_last = (state, show)
        if not show:
            lbl.update("")
            return
        lbl.update(f"[{state}]")
        lbl.styles.background = theme_color(app, "success" if state == "한"
                                            else "primary")
        lbl.styles.color = "black"
        lbl.styles.text_style = "bold"

    def action_inject(self):
        # (text, injected) 로 돌려줘 open_compose 가 투입+초안 저장을 구분한다.
        # 끝에 개행을 붙이지 않아 자식이 자동 제출하지 않는다(사용자가 직접 Enter).
        self.dismiss((self.query_one(TextArea).text, True))

    def action_cancel(self):
        # 투입은 안 하지만 작성 중이던 내용은 돌려줘 초안으로 보존한다(다음에 시드).
        self.dismiss((self.query_one(TextArea).text, False))


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
