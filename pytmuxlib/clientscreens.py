"""클라이언트 모달 스크린(팝업) — 명령목록·옵션·메뉴·트리·정보·토큰로그·
프롬프트·확인·버퍼/레이아웃 선택·권한모드.

client.py 의 거대 클로저(build_client_app)에서 분리한 자족적 ModalScreen
들(§10 LLM 친화 리팩토링). config/sock_path 를 캡처하지 않고, 데이터는
__init__ 인자로 받아 dismiss 로 결과를 돌려준다. 앱 상호작용은 self.app 으로
런타임에 한다. client.py 가 이름으로 import 해 push_screen 한다."""
from __future__ import annotations

from datetime import datetime

from textual.screen import ModalScreen
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Label, ListItem, ListView

from . import usagelog
from .clientutil import COMMANDS, MENU_ITEMS, MENU_TOGGLES


class CommandListScreen(ModalScreen):
    """명령 목록 선택기(? 입력 시). 명령이 많아 카테고리별 탭으로 나눠 한 번에 한
    카테고리만 보여준다. ←→ 로 카테고리(탭) 전환, ↑↓ 로 명령 이동, Enter 선택,
    Esc 취소 — 모두 방향키로 제어된다."""
    CSS = """
    /* §10: 터미널 배경이 팝업 배경($panel)과 같아 박스가 묻히지 않게, 백드롭을
       더 어둡게($background 80% — Textual 기본 60%보다 진하게) 깔아 팝업과
       구분되게 한다. 실제 색 블렌드라 터미널 무관하게 균일하다. */
    CommandListScreen { align: center middle; background: $background 80%; }
    #cmdbox { width: 78; height: auto; max-height: 80%;
              border: round $accent; background: $panel; }
    #cmdtabs { width: 100%; height: 1; padding: 0 1;
               background: $panel-darken-1; }
    /* §10: 높이를 "항목이 가장 많은 카테고리" 기준으로 고정(on_mount 에서
       styles.height 설정) — ←→ 카테고리 전환 시 박스가 출렁이지 않게. 항목이
       적은 카테고리는 ListView 아래쪽이 빈 채로 남아 높이를 유지한다. */
    #cmds { width: 100%;
            background: $panel;
            overflow-y: scroll;                 /* 항상 스크롤바 트랙 표시 */
            scrollbar-size-vertical: 2;
            scrollbar-color: $accent;
            scrollbar-color-hover: $accent-lighten-1;
            scrollbar-background: $panel-darken-2; }
    """

    def __init__(self, items, query=""):
        super().__init__()
        q = query.lower()
        filt = [it for it in items if it[0].startswith(q)] or list(items)
        # 카테고리 등장 순서를 유지하며 그룹화: [(카테고리, [(이름, 설명), ...]), ...]
        order, bucket = [], {}
        for it in filt:
            cat = it[2] if len(it) > 2 else "기타"
            if cat not in bucket:
                bucket[cat] = []
                order.append(cat)
            bucket[cat].append((it[0], it[1]))
        self._cats = [(c, bucket[c]) for c in order]
        self._ci = 0          # 현재 카테고리 인덱스
        self._cur = []        # 현재 카테고리의 (이름, 설명) 목록

    def compose(self) -> ComposeResult:
        with Vertical(id="cmdbox"):
            yield Label("", id="cmdtabs", markup=True)
            yield ListView(id="cmds")

    # 카테고리 전환 시 박스 높이 출렁임 방지용 화면 한도(80% 대략, 행 수).
    _CMDS_MAX_ROWS = 20

    async def on_mount(self):
        # §10: 모든 카테고리 중 최대 항목 수로 ListView 높이를 고정한다(항목 적은
        # 카테고리는 아래쪽이 빈 채로 유지 → ←→ 전환 시 높이 불변). 화면을 넘지
        # 않게 _CMDS_MAX_ROWS 로 클램프(초과 카테고리는 그때만 스크롤). 설명 줄바꿈
        # 으로 일부 항목이 2행이 될 수 있어 항목 수 기준은 근사다(드물게 스크롤).
        maxn = max((len(items) for _, items in self._cats), default=1)
        self.query_one("#cmds").styles.height = min(maxn, self._CMDS_MAX_ROWS)
        await self._render_cat()
        self.query_one(ListView).focus()

    async def _render_cat(self):
        # 상단 탭 바(현재 카테고리 강조).
        parts = []
        for i, (c, items) in enumerate(self._cats):
            if i == self._ci:
                parts.append(f"[reverse b] {c} ({len(items)}) [/]")
            else:
                parts.append(f"[dim] {c} [/]")
        self.query_one("#cmdtabs", Label).update("  ".join(parts))
        # 현재 카테고리 명령 목록으로 ListView 교체(이전 항목을 먼저 비운 뒤 채움 —
        # 비동기 clear/extend 순서를 await 로 보장해 ID 충돌/잔상을 피한다).
        self._cur = self._cats[self._ci][1] if self._cats else []
        lv = self.query_one(ListView)
        await lv.clear()
        await lv.extend([ListItem(Label(f"{n:<20} {d}"))
                         for n, d in self._cur])
        if self._cur:
            lv.index = 0
        box = self.query_one("#cmdbox", Vertical)
        box.border_title = "명령 목록"
        box.border_subtitle = "←→ 카테고리 · ↑↓ 명령 · Enter 선택 · Esc 닫기"

    def on_list_view_selected(self, event):
        idx = self.query_one(ListView).index
        if idx is not None and 0 <= idx < len(self._cur):
            self.dismiss(self._cur[idx][0])

    async def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key == "enter":
            # ListView 기본 Enter 바인딩이 포커스/타이밍 문제로 안 먹는
            # 경우가 있어 직접 현재 항목을 선택해 프롬프트에 채운다.
            event.stop()
            idx = self.query_one(ListView).index
            if idx is not None and 0 <= idx < len(self._cur):
                self.dismiss(self._cur[idx][0])
        elif event.key in ("left", "right") and len(self._cats) > 1:
            event.stop()
            step = 1 if event.key == "right" else -1
            self._ci = (self._ci + step) % len(self._cats)
            await self._render_cat()

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

class InfoScreen(ModalScreen):
    """간단한 읽기전용 목록 표시(show-options 등). 방향키로 항목을 내비게이션하고
    그 외 키를 누르면 닫힌다. 긴 줄(예: 프롬프트 히스토리)은 여러 줄로 줄바꿈."""
    # 항목 Label 을 컨테이너 폭(1fr)에 맞춰 줄바꿈(text-wrap 기본 wrap) → 긴
    # 프롬프트가 잘리지 않고 여러 줄로 펼쳐진다. ListItem 은 height:auto 로 늘어남.
    # 폭은 좁은 화면(모바일)에 맞춰 반응형: 96% 로 채우되 넓은 화면에선 66 칸으로
    # 캡. 제목 줄은 [제목 … [x]] 헤더로 두어 좁아도 닫기 [x] 가 (고정폭이라) 항상
    # 오른쪽에 보인다(예전엔 고정폭 박스가 화면을 넘쳐 닫기 수단이 안 보였다).
    CSS = """
    InfoScreen { align: center middle; background: $background 80%; }
    #infobox { width: 96%; max-width: 66; height: auto;
               border: round $accent; background: $panel; padding: 0 1; }
    #infohead { width: 100%; height: 1; }
    #infotitle { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #infoclose { width: 5; height: 1; content-align: center middle;
                 background: $error; color: $text; text-style: bold; }
    /* §10: 배경 탭 패널 영역 안에서 더 길어지게 — 한 번에 더 많은 항목 표시.
       ModalScreen 은 화면 전체를 덮으므로 %는 화면 기준 → 탭바/상태줄을 침범하지
       않을 만큼 여유를 두고 85% 로(이전 75%). 프롬프트 히스토리/REC/토큰 팝업이
       InfoScreen 을 공유하므로 모두 적용된다. */
    #info { width: 100%; height: auto; max-height: 85%; }
    #info ListItem { height: auto; }
    #info ListItem Label { width: 1fr; }
    """

    # 방향키(내비게이션) — 닫지 않고 ListView 선택을 옮긴다.
    _NAV_KEYS = ("up", "down", "pageup", "pagedown", "home", "end")

    def __init__(self, lines, title="info", hide_key=None, hide_cb=None):
        super().__init__()
        self._lines = lines
        self._title = title
        # 특정 키(hide_key)를 누르면 hide_cb 를 부르고 닫는다(#6 ② '이 헤더 숨기기').
        self._hide_key = hide_key
        self._hide_cb = hide_cb

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

    def on_mount(self):
        self.query_one(ListView).focus()

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
            elif event.key == "home" and n:
                lv.index = 0
            elif event.key == "end" and n:
                lv.index = n - 1
            return
        if self._hide_key and event.key == self._hide_key and self._hide_cb:
            self._hide_cb()
        self.dismiss(None)

class TokenLogScreen(ModalScreen):
    """토큰 사용량 영속 로그 집계 팝업(#7). 서버가 보낸 레코드를 클라이언트가
    usagelog 로 시간/일/월 × 계정 집계한다. [h]시간 [d]일 [m]월 버킷 전환,
    [a] 계정 필터 순환, 방향키 스크롤, 그 외/Esc 닫기(라운드트립 없이 전환)."""
    CSS = """
    TokenLogScreen { align: center middle; }
    #tklogbox { width: 96%; max-width: 86; height: auto;
                border: round $accent; background: $panel; padding: 0 1; }
    #tkloghead { width: 100%; height: 1; }
    #tklogtitle { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #tklogclose { width: 5; height: 1; content-align: center middle;
                  background: $error; color: $text; text-style: bold; }
    #tklog { width: 100%; height: auto; max-height: 78%; }
    #tklog ListItem { height: auto; }
    #tklog ListItem Label { width: 1fr; }
    """
    _NAV_KEYS = ("up", "down", "pageup", "pagedown", "home", "end")
    _BUCKETS = {"h": "hour", "d": "day", "m": "month"}

    def __init__(self, records):
        super().__init__()
        self._records = records or []
        self._bucket = "day"
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
                yield Label("토큰 사용량 로그", id="tklogtitle")
                # markup=False: "[x]" 가 마크업 태그로 사라지지 않게(배경색만
                # 남고 X 가 안 보이던 버그).
                yield Label("[x]", id="tklogclose", markup=False)  # 닫기 버튼
            yield ListView(id="tklog")

    async def on_mount(self):
        await self._refresh()
        self.query_one(ListView).focus()

    async def _refresh(self):
        lv = self.query_one(ListView)
        await lv.clear()
        acct = self._account if self._account is not None else "전체"
        hint = f"[h]시간 [d]일 [m]월   [a]계정: {acct}   [Esc]닫기"
        items = [ListItem(Label(hint, markup=False))]
        for ln in usagelog.summary_lines(self._records, self._bucket,
                                         self._account):
            items.append(ListItem(Label(ln, markup=False)))
        await lv.extend(items)
        self.query_one("#tklogtitle", Label).update(
            f"토큰 사용량 로그 (단위:{self._bucket})")

    def on_click(self, event: events.Click):
        # 닫기 [x] 터치/클릭 → 닫는다(좁은 화면에서도 항상 보이는 버튼).
        w = getattr(event, "widget", None)
        while w is not None:
            if getattr(w, "id", None) == "tklogclose":
                event.stop()
                self.dismiss(None)
                return
            w = w.parent

    async def on_key(self, event: events.Key):
        event.stop()
        k = event.key
        if k in self._BUCKETS:
            self._bucket = self._BUCKETS[k]
            await self._refresh()
            return
        if k == "a" and len(self._accounts) > 1:
            self._ai = (self._ai + 1) % len(self._accounts)
            await self._refresh()
            return
        if k in self._NAV_KEYS:
            lv = self.query_one(ListView)
            if k == "up":
                lv.action_cursor_up()
            elif k == "down":
                lv.action_cursor_down()
            elif k == "pageup":
                for _ in range(5):
                    lv.action_cursor_up()
            elif k == "pagedown":
                for _ in range(5):
                    lv.action_cursor_down()
            elif k == "home":
                lv.index = 0
            elif k == "end" and len(lv.children):
                lv.index = len(lv.children) - 1
            return
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

    def compose(self) -> ComposeResult:
        inp = Input(value=self._initial, placeholder=self._label,
                    suggester=self._suggester, id="pinput")
        if self._purpose == "command":
            # 바닥 고정 컨테이너에 후보(위) → 입력 박스(아래) 순으로 둬, 자동완성
            # 후보가 항상 입력 박스 위쪽에 펼쳐지게 한다(모바일 키보드에 안 가림).
            with Vertical(id="pwrap"):
                yield Label("", id="pcand", markup=True)
                # 맨 왼쪽 고정 ':' 프리픽스(별도 위젯이라 백스페이스로 안 지워짐)
                with Horizontal(id="prow"):
                    yield Label(":", id="pprefix")
                    yield inp
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
        if s and " " not in s:
            ql = s.lower()
            matches = [(n, d) for (n, d, *_) in COMMANDS if ql in n.lower()]
            # 정확히 한 개이고 그게 입력과 동일하면 더 제안할 게 없음.
            if len(matches) == 1 and matches[0][0].lower() == ql:
                matches = []
        self._cand = matches[:self.MAX_CAND]
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
        rows = []
        for i, (n, d) in enumerate(self._cand):
            if i == self._sel:
                rows.append(f"[reverse]{self._esc(n):<20} {self._esc(d)}[/reverse]")
            else:
                rows.append(f"{self._esc(n):<20} [dim]{self._esc(d)}[/dim]")
        lbl.update("\n".join(rows))

    def _accept_cand(self):
        inp = self.query_one(Input)
        name = self._cand[self._sel][0]
        inp.value = name + " "
        inp.cursor_position = len(inp.value)
        inp.focus()
        self._refresh_cands()

    def on_input_submitted(self, event):
        # 후보가 떠 있으면 Enter 는 강조된 후보를 입력에 채우고(실행하지 않음),
        # 그 다음 Enter 로 실제 실행한다.
        if self._purpose == "command" and self._cand_shown and self._cand:
            self._accept_cand()
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
    순환 주입한다. bypass(권한 우회)는 위험 모드라 목록에서 제외(실수 방지)."""
    CSS = """
    PermModeScreen { align: center middle; }
    #perm { width: 60; height: auto; max-height: 80%;
            border: round $accent; background: $panel; }
    #perm ListItem Label { width: 1fr; }
    """
    _MODES = [
        ("auto", "auto — 편집 자동 수락 (⏵⏵ auto-accept edits)"),
        ("default", "default — 매번 확인 (일반 모드)"),
        ("plan", "plan — 플랜 모드 (계획만, 실행 안 함)"),
    ]

    def __init__(self, current):
        super().__init__()
        self._current = current

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
        self.query_one(ListView).focus()

    def on_list_view_selected(self, event):
        self.dismiss(event.item.id[2:])   # "M_auto" → "auto"

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
