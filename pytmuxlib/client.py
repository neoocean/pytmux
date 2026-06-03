"""화면을 그리는 Textual 클라이언트."""
from __future__ import annotations

import asyncio
import base64
import shlex
import socket
import subprocess

from wcwidth import wcwidth

from .keymap import _key_to_ctrl_bytes, _tmux_key_to_textual, load_config
from .protocol import MIN_H, MIN_W, read_msg, write_msg


def _char_cells(ch: str) -> int:
    """터미널에서 문자가 차지하는 칸 수(와이드=2, 그 외=1)."""
    return 2 if wcwidth(ch) == 2 else 1


# clock-mode 큰 시계용 3x5 블록 폰트(시:분:초)
_CLOCK_FONT = {
    "0": ["███", "█ █", "█ █", "█ █", "███"],
    "1": ["  █", "  █", "  █", "  █", "  █"],
    "2": ["███", "  █", "███", "█  ", "███"],
    "3": ["███", "  █", "███", "  █", "███"],
    "4": ["█ █", "█ █", "███", "  █", "  █"],
    "5": ["███", "█  ", "███", "  █", "███"],
    "6": ["███", "█  ", "███", "█ █", "███"],
    "7": ["███", "  █", "  █", "  █", "  █"],
    "8": ["███", "█ █", "███", "█ █", "███"],
    "9": ["███", "█ █", "███", "  █", "███"],
    ":": ["   ", " █ ", "   ", " █ ", "   "],
}


# 한글 두벌식 자모 → QWERTY 영문 키. IME 가 켜져 있어도 단축키가 동작하도록
# 키 매칭 시 자모를 물리 키(영문)로 되돌린다.
_JAMO = {
    "ㅂ": "q", "ㅈ": "w", "ㄷ": "e", "ㄱ": "r", "ㅅ": "t", "ㅛ": "y", "ㅕ": "u",
    "ㅑ": "i", "ㅐ": "o", "ㅔ": "p", "ㅁ": "a", "ㄴ": "s", "ㅇ": "d", "ㄹ": "f",
    "ㅎ": "g", "ㅗ": "h", "ㅓ": "j", "ㅏ": "k", "ㅣ": "l", "ㅋ": "z", "ㅌ": "x",
    "ㅊ": "c", "ㅍ": "v", "ㅠ": "b", "ㅜ": "n", "ㅡ": "m",
    # 시프트(쌍자음/이중모음) → 대문자 영문
    "ㅃ": "Q", "ㅉ": "W", "ㄸ": "E", "ㄲ": "R", "ㅆ": "T", "ㅒ": "O", "ㅖ": "P",
}


def _normalize_key(k: str) -> str:
    """Textual 키 문자열에서 한글 자모를 QWERTY 영문 키로 정규화."""
    if not k:
        return k
    for pfx in ("ctrl+", "shift+", "alt+", "meta+"):
        if k.startswith(pfx):
            base = k[len(pfx):]
            return pfx + _JAMO.get(base, base)
    return _JAMO.get(k, k)


def build_client_app(sock_path: str, config: dict | None = None,
                     session_name: str | None = None):
    config = config or {}
    from rich.segment import Segment
    from rich.style import Style
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.strip import Strip
    from textual.suggester import SuggestFromList
    from textual.widget import Widget
    from textual.widgets import Input, Label, ListItem, ListView
    from datetime import datetime

    DEFAULT_STYLE = Style()
    _style_cache: dict = {}

    # p4v-tui 와 동일한 textual-dark 테마 색을 따른다(없으면 폴백).
    _THEME_FALLBACK = {
        "primary": "#0178D4", "secondary": "#004578", "accent": "#FEA62B",
        "background": "#121212", "surface": "#1E1E1E", "panel": "#242F38",
        "foreground": "#E0E0E0", "success": "#4EBF71", "warning": "#FEA62B",
        "error": "#B93C5B",
    }

    def theme_color(widget, name: str) -> str:
        """현재 Textual 테마에서 색을 해석(없으면 textual-dark 폴백)."""
        try:
            v = widget.app.theme_variables.get(name)
            if v:
                return v
        except Exception:
            pass
        return _THEME_FALLBACK.get(name, "white")

    def make_style(d: dict) -> Style:
        if not d:
            return DEFAULT_STYLE
        key = tuple(sorted(d.items()))
        st = _style_cache.get(key)
        if st is None:
            try:
                st = Style(color=d.get("f"), bgcolor=d.get("b"),
                           bold=bool(d.get("bo")), italic=bool(d.get("it")),
                           underline=bool(d.get("un")), reverse=bool(d.get("rv")),
                           strike=bool(d.get("st")))
            except Exception:
                st = Style(reverse=bool(d.get("rv")), bold=bool(d.get("bo")))
            _style_cache[key] = st
        return st

    SPECIAL = {
        "enter": b"\r", "tab": b"\t", "backspace": b"\x7f", "escape": b"\x1b",
        "space": b" ", "up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C",
        "left": b"\x1b[D", "home": b"\x1b[H", "end": b"\x1b[F",
        "pageup": b"\x1b[5~", "pagedown": b"\x1b[6~", "delete": b"\x1b[3~",
        "insert": b"\x1b[2~", "f1": b"\x1bOP", "f2": b"\x1bOQ", "f3": b"\x1bOR",
        "f4": b"\x1bOS", "f5": b"\x1b[15~", "f6": b"\x1b[17~", "f7": b"\x1b[18~",
        "f8": b"\x1b[19~", "f9": b"\x1b[20~", "f10": b"\x1b[21~",
        "f11": b"\x1b[23~", "f12": b"\x1b[24~",
        "shift+tab": b"\x1b[Z",   # backtab(CSI Z) — Claude 권한 모드 순환 등
        "shift+enter": b"\n",     # LF — Claude 등 입력 줄바꿈(Enter=CR 제출과 구분)
        "shift+escape": b"\x1b",  # 앱으로 ESC 전달(ESC 단독은 esc 모드 진입)
    }

    def key_to_bytes(event: events.Key) -> bytes:
        k = event.key
        if k in SPECIAL:
            return SPECIAL[k]
        if k.startswith("ctrl+"):
            c = k[5:]
            # ASCII 영문 ctrl 조합만 제어코드로(한글 등 비ASCII는 무시)
            if len(c) == 1 and "a" <= c.lower() <= "z":
                return bytes([ord(c.lower()) - 96])
            if event.character and event.character.isascii():
                return event.character.encode("utf-8", "replace")
            return b""
        if event.character is not None and event.character != "":
            return event.character.encode("utf-8", "replace")
        return b""

    MENU_ITEMS = [
        ("split_lr", "패널 분할 │ (좌우)"),
        ("split_tb", "패널 분할 ─ (상하)"),
        ("zoom", "패널 줌 토글 ⛶"),
        ("kill_pane", "패널 삭제 ✕"),
        ("sync", "입력 동기화 토글"),
        ("autoresume", "토큰리밋 자동재개 토글"),
        ("new_window", "새 탭"),
        ("rename_window", "탭 이름 변경"),
        ("kill_window", "탭 삭제"),
        ("choose_tree", "탭 선택기(트리)"),
        ("next_window", "다음 탭"),
        ("prev_window", "이전 탭"),
        ("layout_save", "레이아웃 저장(현재 탭)"),
        ("layout_load_over", "레이아웃 불러오기(현재 탭 덮어쓰기)"),
        ("layout_load_new", "레이아웃 불러오기(새 탭)"),
        ("command", "명령 입력"),
        ("detach", "detach (앱 종료, 셸 유지)"),
        ("kill_server", "서버 종료 (모든 탭/셸 종료)"),
    ]

    # 명령 프롬프트(:)에서 쓸 수 있는 명령 목록 (이름, 설명) — ? 목록·자동완성용
    # (이름, 설명, 카테고리). 카테고리는 ?/help 목록의 탭 그룹으로 쓰인다.
    # 새 명령을 추가할 땐 카테고리도 함께 지정할 것(없으면 "기타"로 묶임).
    COMMANDS = [
        ("split-window", "패널 분할 (-h 가로/상하 · -v 세로/좌우)", "패널"),
        ("kill-pane", "현재 패널 삭제", "패널"),
        ("resize-pane", "패널 크기 (-Z 줌 토글)", "패널"),
        ("select-pane", "패널 이동 (-L/-R/-U/-D) 또는 제목 (-T)", "패널"),
        ("rename-pane", "패널 제목 변경", "패널"),
        ("swap-pane", "패널 위치 교환 (-U/-D)", "패널"),
        ("rotate-window", "윈도우 내 패널 회전", "패널"),
        ("break-pane", "패널을 새 탭으로 분리", "패널"),
        ("join-pane", "다른 탭의 패널을 현재로 합치기 (-h)", "패널"),
        ("respawn-pane", "패널 셸 재시작", "패널"),
        ("select-layout", "레이아웃 프리셋 (even-h/v, main-h/v, tiled)", "패널"),
        ("next-layout", "다음 레이아웃 프리셋", "패널"),
        ("synchronize-panes", "입력 동기화 토글 [on|off]", "패널"),
        ("new-tab", "새 탭 (새 윈도우 1개 생성, = new-window)", "탭"),
        ("kill-tab", "탭 삭제 (= kill-window)", "탭"),
        ("next-tab", "다음 탭", "탭"),
        ("previous-tab", "이전 탭", "탭"),
        ("last-tab", "직전 탭", "탭"),
        ("select-tab", "탭 선택 (-t N)", "탭"),
        ("move-tab", "탭 이동 (-t N)", "탭"),
        ("move-tab-left", "현재 탭을 왼쪽으로", "탭"),
        ("move-tab-right", "현재 탭을 오른쪽으로", "탭"),
        ("move-tab-first", "현재 탭을 맨 앞으로", "탭"),
        ("move-tab-last", "현재 탭을 맨 뒤로", "탭"),
        ("swap-tab", "탭 교환 (-t N)", "탭"),
        ("rename-tab", "탭 이름 변경", "탭"),
        ("automatic-rename", "탭 자동 이름 [on|off]", "탭"),
        ("choose-tree", "탭 선택기(트리)", "탭"),
        ("capture-pane", "패널 내용을 버퍼로 캡처 (-S 전체)", "복사/버퍼"),
        ("pipe-pane", "패널 출력을 외부 명령으로 파이프", "복사/버퍼"),
        ("clear-history", "스크롤백 비우기", "복사/버퍼"),
        ("send-keys", "패널에 키 주입 (예: Enter, C-c)", "복사/버퍼"),
        ("paste-buffer", "페이스트 버퍼 붙여넣기 (N)", "복사/버퍼"),
        ("choose-buffer", "페이스트 버퍼 선택기", "복사/버퍼"),
        ("paste-clipboard", "OS 클립보드 붙여넣기", "복사/버퍼"),
        ("save-layout", "전체 레이아웃 저장(서버 영속)", "레이아웃"),
        ("restore-layout", "전체 레이아웃 복원(서버 영속)", "레이아웃"),
        ("layout-save", "현재 탭 레이아웃 저장 (이름)", "레이아웃"),
        ("layout-load", "레이아웃 불러오기 → 현재 탭 덮어쓰기 (이름)", "레이아웃"),
        ("layout-load-new", "레이아웃 불러오기 → 새 탭 (이름)", "레이아웃"),
        ("monitor-activity", "활동 모니터링 [on|off]", "모니터/Claude"),
        ("monitor-bell", "벨 모니터링 [on|off]", "모니터/Claude"),
        ("auto-resume", "토큰 리밋 자동 재개 [on|off]", "모니터/Claude"),
        ("auto-resume-message", "자동 재개 메시지 설정", "모니터/Claude"),
        ("capture-output", "패널 출력 캡처 토글 [on|off] (기본 on, 영속)", "모니터/Claude"),
        ("set", "옵션 설정 (prefix/mouse/status-*/mode-keys 등)", "설정/기타"),
        ("show-options", "현재 옵션 보기", "설정/기타"),
        ("set-hook", "이벤트 훅 설정 (<event> <cmd>)", "설정/기타"),
        ("show-hooks", "훅 목록 보기", "설정/기타"),
        ("source-file", "설정 파일 다시 불러오기", "설정/기타"),
        ("display-message", "상태줄에 메시지 표시", "설정/기타"),
        ("display-popup", "명령 실행 결과를 팝업으로", "설정/기타"),
        ("clock-mode", "현재 패널을 큰 시계로 덮기(토글, 우상단 [x]/명령으로 닫기)", "설정/기타"),
        ("run-shell", "셸 명령 실행", "설정/기타"),
        ("if-shell", "조건부 셸 실행", "설정/기타"),
        ("detach-client", "detach (앱 종료, 셸 유지)", "설정/기타"),
        ("kill-server", "서버와 모든 탭/셸 종료", "설정/기타"),
    ]

    # 명령 프롬프트 자동완성 후보. 자주 쓰는 옵션 템플릿을 앞에 두어, 명령을 다 치면
    # ghost 로 옵션(-h 등)까지 함께 제안된다(→ 로 수락). 뒤에 전체 명령 이름을 붙여
    # 옵션이 없는 명령도 보완.
    COMPLETIONS = [
        "split-window -h", "split-window -v",
        "resize-pane -Z",
        "select-pane -L", "select-pane -R", "select-pane -U", "select-pane -D",
        "swap-pane -U", "swap-pane -D",
        "join-pane -h",
        "select-layout tiled", "select-layout even-horizontal",
        "select-layout even-vertical", "select-layout main-vertical",
        "select-layout main-horizontal",
        "capture-pane -S",
        "monitor-activity on", "monitor-bell on", "automatic-rename on",
        "detach-client", "kill-server",
    ] + [c[0] for c in COMMANDS]

    class CommandListScreen(ModalScreen):
        """명령 목록 선택기(? 입력 시). 명령이 많아 카테고리별 탭으로 나눠 한 번에 한
        카테고리만 보여준다. ←→ 로 카테고리(탭) 전환, ↑↓ 로 명령 이동, Enter 선택,
        Esc 취소 — 모두 방향키로 제어된다."""
        CSS = """
        CommandListScreen { align: center middle; }
        #cmdbox { width: 78; height: auto; max-height: 80%;
                  border: round $accent; background: $panel; }
        #cmdtabs { width: 100%; height: 1; padding: 0 1;
                   background: $panel-darken-1; }
        #cmds { width: 100%; height: auto; max-height: 1fr;
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

        async def on_mount(self):
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

    class MenuScreen(ModalScreen):
        CSS = """
        MenuScreen { align: center middle; }
        #menu { width: 40; height: auto; border: round $accent; background: $panel; }
        """

        def compose(self) -> ComposeResult:
            lv = ListView(*[ListItem(Label(label), id=f"m_{key}")
                            for key, label in MENU_ITEMS], id="menu")
            yield lv

        def on_mount(self):
            self.query_one(ListView).focus()

        def on_list_view_selected(self, event):
            key = event.item.id[2:]
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
            self.entries = []

        def compose(self) -> ComposeResult:
            items = []
            n = 0
            for s in self._treedata.get("sessions", []):
                for w in s["windows"]:
                    label = f"{w['index']}:{w['name']} ({w['panes']} panes)"
                    self.entries.append((s["name"], w["index"]))
                    items.append(ListItem(Label(label), id=f"e{n}"))
                    n += 1
            yield ListView(*items, id="tree")

        def on_mount(self):
            self.query_one(ListView).focus()

        def on_list_view_selected(self, event):
            self.dismiss(self.entries[int(event.item.id[1:])])

        def on_key(self, event: events.Key):
            if event.key == "escape":
                event.stop()
                self.dismiss(None)

    class InfoScreen(ModalScreen):
        """간단한 읽기전용 목록 표시(show-options 등). 아무 키나 누르면 닫힘."""
        CSS = """
        InfoScreen { align: center middle; }
        #info { width: 64; height: auto; max-height: 80%;
                border: round $accent; background: $panel; padding: 0 1; }
        """

        def __init__(self, lines, title="info"):
            super().__init__()
            self._lines = lines
            self._title = title

        def compose(self) -> ComposeResult:
            box = ListView(*[ListItem(Label(ln)) for ln in self._lines] or
                           [ListItem(Label("(없음)"))], id="info")
            box.border_title = self._title
            yield box

        def on_mount(self):
            self.query_one(ListView).focus()

        def on_key(self, event: events.Key):
            event.stop()
            self.dismiss(None)

    class PromptScreen(ModalScreen):
        """명령/이름변경/검색 등 한 줄 입력을 받는 바닥 고정 모달.
        Textual Input 을 별도 스크린(모달)에 담아 포커스 문제를 피한다."""
        CSS = """
        PromptScreen { align: center bottom; }
        #prow { dock: bottom; width: 100%; height: 1; background: $surface; }
        #pprefix { width: 2; height: 1; color: $accent; text-style: bold;
                   background: $surface; }
        #pinput { width: 1fr; border: none; height: 1; padding: 0;
                  background: $surface; color: $text; }
        /* 입력 줄 위로 펼쳐지는 자동완성 후보 영역(부분일치 명령). */
        #pcand { dock: bottom; width: 100%; height: auto; max-height: 12;
                 background: $panel; color: $text; padding: 0 1;
                 border-top: tall $accent; }
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
                # 맨 왼쪽에 고정 ':' 프리픽스(별도 위젯이라 백스페이스로 안 지워짐)
                with Horizontal(id="prow"):
                    yield Label(":", id="pprefix")
                    yield inp
                # 입력 줄보다 먼저 docked → 입력 줄이 화면 맨 아래, 후보는 그 위로 쌓임.
                yield Label("", id="pcand", markup=True)
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
        """

        def __init__(self, message, yes_label="닫기", no_label="취소",
                     title="확인", default_yes=False):
            super().__init__()
            self._message = message
            self._yes = yes_label
            self._no = no_label
            self._title = title
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
            """선택 위치에 따라 강조 클래스(.sel)를 토글한다."""
            self.query_one("#cy", Label).set_class(self._sel == 0, "sel")
            self.query_one("#cn", Label).set_class(self._sel == 1, "sel")

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

    class MultiplexerView(Widget):
        can_focus = True

        def __init__(self):
            super().__init__(id="view")
            self._cells: list[list] = []
            self._dragging = None  # (split_id, orient, rect)
            self._sel = None       # 선택 영역 (x0,y0,x1,y1) 전역 좌표
            self._sel_start = None

        def _extract_selection(self):
            if not self._sel or not self._cells:
                return ""
            x0, y0, x1, y1 = self._sel
            if (y0, x0) > (y1, x1):
                x0, y0, x1, y1 = x1, y1, x0, y0
            out = []
            for y in range(y0, y1 + 1):
                if not (0 <= y < len(self._cells)):
                    continue
                row = self._cells[y]
                sx = x0 if y == y0 else 0
                ex = x1 if y == y1 else len(row) - 1
                text = "".join(row[x][0] for x in range(max(0, sx),
                                                         min(len(row), ex + 1)))
                out.append(text.rstrip())
            return "\n".join(out)

        def set_frame(self, cells):
            self._cells = cells
            self.refresh()

        def render_line(self, y: int) -> Strip:
            if y >= len(self._cells):
                return Strip.blank(self.size.width)
            row = self._cells[y]
            segs = []
            run = []
            run_st = None
            for ch, st in row:
                if ch == "":
                    continue  # 와이드 문자의 연속 셀 → 앞 문자가 2칸을 차지함
                if st is run_st:
                    run.append(ch)
                else:
                    if run:
                        segs.append(Segment("".join(run), run_st))
                    run = [ch]
                    run_st = st
            if run:
                segs.append(Segment("".join(run), run_st))
            return Strip(segs)

        # --- 마우스 ---
        def _pane_at(self, x, y):
            for p in self.app.layout.get("panes", []):
                bx, by, bw, bh = p.get("box") or (p["x"], p["y"], p["w"], p["h"])
                if bx <= x < bx + bw and by <= y < by + bh:
                    return p
            return None

        def _divider_at(self, x, y):
            for d in self.app.layout.get("dividers", []):
                if d["x"] <= x < d["x"] + d["w"] and d["y"] <= y < d["y"] + d["h"]:
                    return d
            return None

        def on_mouse_down(self, event: events.MouseDown):
            if not self.app.mouse_enabled:
                return
            if self.app.mode == "scroll":  # copy-mode: 드래그로 선택
                self._sel_start = (event.x, event.y)
                self._sel = (event.x, event.y, event.x, event.y)
                self.capture_mouse()
                self.app._composite()
                event.stop()
                return
            if event.button == 3:
                self.app.open_menu()
                event.stop()
                return
            # clock-mode 닫기 버튼([x]) 클릭
            for pid, (zx0, zx1, zy) in self.app._clock_close_zones.items():
                if zy == event.y and zx0 <= event.x < zx1:
                    self.app.toggle_clock(pid)
                    event.stop()
                    return
            # Claude 헤더 닫기 버튼([x]) 클릭
            for pid, (zx0, zx1, zy) in self.app._claude_close_zones.items():
                if zy == event.y and zx0 <= event.x < zx1:
                    self.app.close_claude_header(pid)
                    event.stop()
                    return
            # 현재 탭 닫기 버튼([x]) 클릭(콘텐츠 오른쪽 위)
            z = self.app._tab_close_zone
            if z and z[2] == event.y and z[0] <= event.x < z[1]:
                self.app.confirm_kill_tab()
                event.stop()
                return
            d = self._divider_at(event.x, event.y)
            if d:
                self._dragging = d
                self.capture_mouse()
                event.stop()
                return
            p = self._pane_at(event.x, event.y)
            if p:
                self.app.send_cmd("select_pane_id", id=p["id"])
            event.stop()

        def on_mouse_move(self, event: events.MouseMove):
            if self._sel_start is not None:
                self._sel = (self._sel_start[0], self._sel_start[1],
                             event.x, event.y)
                self.app._composite()
                event.stop()
                return
            if not self._dragging:
                return
            d = self._dragging
            sx, sy, sw, sh = d["rect"]
            if d["orient"] == "lr":
                avail = sw - 1
                ratio = (event.x - sx) / avail if avail > 0 else 0.5
            else:
                avail = sh - 1
                ratio = (event.y - sy) / avail if avail > 0 else 0.5
            self.app.send_cmd("resize", split_id=d["split_id"],
                              ratio=max(0.05, min(0.95, ratio)))
            event.stop()

        def on_mouse_up(self, event: events.MouseUp):
            if self._sel_start is not None:
                text = self._extract_selection()
                self._sel_start = None
                self._sel = None
                self.release_mouse()
                if text:
                    self.app.copy_text(text)
                self.app._composite()
                event.stop()
                return
            if self._dragging:
                self._dragging = None
                self.release_mouse()
                event.stop()

        def on_mouse_scroll_up(self, event):
            if not self.app.mouse_enabled:
                return
            p = self._pane_at(event.x, event.y) or self._active_pane()
            if p:
                self.app.send_scroll(p["id"], delta=3)
            event.stop()

        def on_mouse_scroll_down(self, event):
            if not self.app.mouse_enabled:
                return
            p = self._pane_at(event.x, event.y) or self._active_pane()
            if p:
                self.app.send_scroll(p["id"], delta=-3)
            event.stop()

        def _active_pane(self):
            aid = self.app.layout.get("active")
            for p in self.app.layout.get("panes", []):
                if p["id"] == aid:
                    return p
            return None

    class TabBar(Widget):
        """상단 탭 인터페이스. 각 탭과, 마지막 탭 바로 오른쪽의 [+] 새 탭 버튼을
        표시한다. (탭 닫기 [x] 는 콘텐츠 영역 오른쪽 위 모서리로 이동했다.)

        마우스 클릭과 ESC 모드 방향키(←→ 선택, Enter 전환)로 조작. 탭이 하나뿐이면
        기본 숨김이나, 설정 tab-bar always 면 항상 표시한다."""

        def __init__(self):
            super().__init__(id="tabbar")
            self.tabs = []          # [{index,name,active,bell,activity}]
            self.sel = 0            # ESC 모드 선택 인덱스(= tab.index)
            self.bar_focus = False  # ESC 모드 포커스가 탭바에 있는지
            self._scroll = 0        # 가로 스크롤(첫 표시 탭의 리스트 위치)
            self._zones = []        # [(x0, x1, kind, payload)] 클릭 히트테스트
            self._drag = None       # 드래그 중인 탭 index(재정렬)

        def set_tabs(self, tabs, active_idx):
            self.tabs = tabs
            if not self.bar_focus:
                self.sel = active_idx
            self.refresh()

        def scroll_by(self, delta):
            self._scroll = max(0, min(self._scroll + delta,
                                      max(0, len(self.tabs) - 1)))
            self.refresh()

        # Claude Code 상태 아이콘(탭): 대기 ○ / 처리중 ◐ / 리밋 멈춤 ⊘
        CLAUDE_ICON = {"idle": "○", "busy": "◐", "limit": "⊘"}

        def _labels(self):
            out = []
            for t in self.tabs:
                flag = "!" if t.get("bell") else ("#" if t.get("activity") else "")
                ic = self.CLAUDE_ICON.get(t.get("claude"))
                ic = (ic + " ") if ic else ""
                out.append(f" {ic}{t['index']}:{t['name']}{flag} ")
            return out

        def render_line(self, y: int) -> Strip:
            w = self.size.width
            fg = theme_color(self, "foreground")
            base = Style(color=fg, bgcolor=theme_color(self, "panel"))
            add_st = Style(color="black", bgcolor=theme_color(self, "success"),
                           bold=True)
            active_st = Style(color="white", bgcolor=theme_color(self, "primary"),
                              bold=True)
            sel_st = Style(color="black", bgcolor=theme_color(self, "accent"),
                           bold=True)
            arrow_st = Style(color="black", bgcolor=theme_color(self, "accent"),
                             bold=True)
            labels = self._labels()
            widths = [sum(_char_cells(c) for c in s) for s in labels]
            n = len(self.tabs)
            idxs = [t["index"] for t in self.tabs]
            selpos = idxs.index(self.sel) if self.sel in idxs else 0
            # [+] 새 탭 버튼 폭만 빼면 됨([x] 닫기는 콘텐츠 패널 위로 이동함)
            addtxt = " [+] "
            mid_w = max(1, w - len(addtxt))
            # 선택 탭이 보이도록 스크롤 보정
            self._scroll = max(0, min(self._scroll, max(0, n - 1)))
            if selpos < self._scroll:
                self._scroll = selpos
            while (self._scroll < selpos and
                   sum(widths[self._scroll:selpos + 1]) > mid_w - 2):
                self._scroll += 1

            segs, zones = [], []
            x = 0

            def add(text, st, kind=None, payload=None):
                nonlocal x
                wdt = sum(_char_cells(c) for c in text)
                zones.append((x, x + wdt, kind, payload))
                segs.append(Segment(text, st))
                x += wdt

            mid_used = 0
            if self._scroll > 0:                       # 왼쪽에 더 있음
                add("◀", arrow_st, "scroll_left")
                mid_used += 1
            i = self._scroll
            while i < n:
                tw = widths[i]
                reserve = 1 if i < n - 1 else 0        # 오른쪽 화살표 자리
                if mid_used + tw > mid_w - reserve and i > self._scroll:
                    break
                if self.bar_focus and self.tabs[i]["index"] == self.sel:
                    st = sel_st
                elif self.tabs[i].get("active"):
                    st = active_st
                else:
                    st = base
                add(labels[i], st, "tab", self.tabs[i]["index"])
                mid_used += tw
                i += 1
            if i < n:                                  # 오른쪽에 더 있음
                add("▶", arrow_st, "scroll_right")
            add(addtxt, add_st, "add")                 # 마지막 탭 바로 오른쪽
            pad = w - x
            if pad > 0:
                segs.append(Segment(" " * pad, base))
                x += pad
            self._zones = zones
            return Strip(segs).adjust_cell_length(w, base)

        def _hit(self, x):
            for x0, x1, kind, payload in self._zones:
                if x0 <= x < x1:
                    return kind, payload
            return None, None

        def on_mouse_down(self, event):
            if not self.app.mouse_enabled:
                return
            kind, payload = self._hit(event.x)
            if kind == "add":
                self.app.send_cmd("new_window")
            elif kind == "scroll_left":
                self.scroll_by(-1)
            elif kind == "scroll_right":
                self.scroll_by(1)
            elif kind == "tab":
                # 탭 클릭=드래그 시작(놓을 때 같은 탭이면 선택, 다른 탭이면 재정렬)
                self._drag = payload
                self.capture_mouse()
            event.stop()

        def on_mouse_up(self, event):
            if self._drag is None:
                return
            src = self._drag
            self._drag = None
            try:
                self.release_mouse()
            except Exception:
                pass
            kind, payload = self._hit(event.x)
            if kind == "tab" and payload != src:
                # index==위치(연속) 이므로 그대로 사용
                self.app.send_cmd("move_tab", index=src, to=payload)
            else:
                self.app.send_cmd("select_window", index=src)
            event.stop()

    class StatusBar(Widget):
        def __init__(self, bg=None, fg=None,
                     left=" ", right=" #{pane_title}#h %H:%M %Y-%m-%d "):
            super().__init__(id="status")
            self.session = ""
            self.windows = []
            self.zoomed = False
            self.sync = False
            self.pane_title = ""
            self.autoresume = False
            self.capture = True      # 패널 출력 캡처 중(서버 옵션, 기본 ON)
            self.prefix_off = False  # 중첩: outer prefix 해제 표시
            self.cmd_mode = False  # ESC 명령 모드 표시
            self.message = None    # display-message 임시 메시지
            self.hide_tabs = False  # 상단 탭바가 보이면 하단 탭 목록 생략
            self.claude_usage = None  # 활성 Claude 패널의 토큰/컨텍스트(best-effort)
            self.bg = bg
            self.fg = fg
            self.left_fmt = left
            self.right_fmt = right
            self._clock_zone = None  # (x0, x1) 시계(오른쪽) 클릭 영역

        def _expand(self, fmt):
            """#S/#h/#H/#{pane_title} 토큰과 strftime(%) 코드를 치환."""
            try:
                s = datetime.now().strftime(fmt)
            except ValueError:
                s = fmt
            host = socket.gethostname()
            tpane = (self.pane_title + " · ") if (self.pane_title
                     and self.pane_title != "shell") else ""
            aw = next((w for w in self.windows if w.get("active")), None)
            return (s.replace("#S", self.session)
                     .replace("#h", host.split(".")[0])
                     .replace("#H", host)
                     .replace("#I", str(aw["index"]) if aw else "")
                     .replace("#W", aw["name"] if aw else "")
                     .replace("#{pane_title}", tpane))

        def update_status(self, msg):
            self.session = msg.get("session", "")
            self.windows = msg.get("windows", [])
            self.zoomed = msg.get("zoomed", False)
            self.sync = msg.get("sync", False)
            self.pane_title = msg.get("pane_title", "")
            self.autoresume = msg.get("autoresume", False)
            self.capture = msg.get("capture", True)
            self.claude_usage = msg.get("claude_usage")
            self.refresh()

        def render_line(self, y: int) -> Strip:
            w = self.size.width
            # 색상은 p4v-tui 와 동일한 textual-dark 테마를 따른다(설정으로 덮어쓰기 가능).
            tc = lambda n: theme_color(self, n)  # noqa: E731
            base = Style(color=self.fg or tc("foreground"),
                         bgcolor=self.bg or tc("surface"))
            if self.message is not None:
                ms = Style(color="black", bgcolor=tc("warning"), bold=True)
                return Strip([Segment(f" {self.message} ", ms)]).adjust_cell_length(
                    w, ms)
            active = Style(color="white", bgcolor=tc("primary"), bold=True)
            segs = [Segment(self._expand(self.left_fmt), base)]
            if self.cmd_mode:
                segs.append(Segment("CMD(←↑↓→ 이동, : 명령) ",
                                    Style(color="black", bgcolor=tc("accent"),
                                          bold=True)))
            if self.zoomed:
                segs.append(Segment("Z ", Style(color="black", bgcolor=tc("warning"),
                                                 bold=True)))
            if self.sync:
                segs.append(Segment("SYNC ", Style(color="white", bgcolor=tc("error"),
                                                    bold=True)))
            if self.autoresume:
                segs.append(Segment("AR ", Style(color="black", bgcolor=tc("accent"),
                                                  bold=True)))
            if self.capture:        # 패널 출력 캡처 중
                segs.append(Segment("REC ", Style(color="white", bgcolor=tc("error"),
                                                   bold=True)))
            if self.claude_usage:   # 활성 Claude 패널 토큰/컨텍스트(best-effort)
                segs.append(Segment(f" {self.claude_usage} ",
                                    Style(color="white", bgcolor=tc("secondary"),
                                          bold=True)))
            if self.prefix_off:
                segs.append(Segment("NEST ", Style(color="white",
                                                   bgcolor=tc("secondary"), bold=True)))
            for win in ([] if self.hide_tabs else self.windows):
                flag = "!" if win.get("bell") else ("#" if win.get("activity") else "")
                label = f"{win['index']}:{win['name']}{flag} "
                if win["active"]:
                    st = active
                elif win.get("bell"):
                    st = Style(color="white", bgcolor=tc("error"), bold=True)
                elif win.get("activity"):
                    st = Style(color="black", bgcolor=tc("warning"))
                else:
                    st = base
                segs.append(Segment(label, st))
            right = self._expand(self.right_fmt)
            used = sum(len(s.text) for s in segs)
            pad = w - used - len(right)
            if pad > 0:
                segs.append(Segment(" " * pad, base))
            segs.append(Segment(right, base))
            # 오른쪽 시계 영역(클릭하면 활성 패널 clock-mode 토글)
            rw = sum(_char_cells(c) for c in right)
            self._clock_zone = (max(0, w - rw), w) if rw else None
            # 폭 맞추기(자르기)
            return Strip(segs).adjust_cell_length(w, base)

        def on_mouse_down(self, event: events.MouseDown):
            if not self.app.mouse_enabled:
                return
            z = self._clock_zone
            if z and z[0] <= event.x < z[1]:
                self.app.toggle_clock(self.app.layout.get("active"))
                event.stop()

    class PytmuxApp(App):
        ENABLE_COMMAND_PALETTE = False
        BINDINGS = []
        CSS = """
        Screen { layout: vertical; }
        #tabbar { width: 100%; height: 1; dock: top; }
        #view { width: 100%; height: 1fr; }
        #status { width: 100%; height: 1; dock: bottom; }
        """

        def __init__(self, sock_path: str):
            super().__init__()
            self.sock_path = sock_path
            self.session_name = session_name
            self.reader = None
            self.writer = None
            self.layout = {"panes": [], "dividers": [], "active": None,
                           "cols": 80, "rows": 24}
            self.pane_content = {}   # id -> (rows, cursor)
            self.mode = "normal"     # normal | prefix | scroll | prompt | display
            self._want_tree = False  # choose-tree 응답 대기
            self._want_buffers = False  # choose-buffer 응답 대기
            self._want_layouts = None  # 레이아웃 목록 응답 대기(모드: "new"/"over")
            self.clock_panes = set()   # clock-mode 가 켜진 패널 id 집합
            self._clock_close_zones = {}  # pane_id -> (x0, x1, y) 닫기 버튼 영역
            # Claude Code: 패널별 상태/마지막 프롬프트, 헤더 닫힘 추적
            self.pane_claude = {}      # id -> {"claude": state, "prompt": str}
            self._claude_hidden = {}   # id -> 닫을 때의 prompt(같으면 숨김)
            self._claude_close_zones = {}  # id -> (x0, x1, y)
            self._tab_close_zone = None  # 현재 탭 닫기 [x] 영역 (x0, x1, y)
            # ---- 설정(config) 적용 ----
            self.prefix_key = config.get("prefix", "ctrl+b")
            self.prefix_bytes = _key_to_ctrl_bytes(self.prefix_key)
            self.prefix_enabled = True  # 중첩 시 F12 로 outer prefix 일시 해제
            self.bindings = config.get("bindings", {})
            self.mouse_enabled = config.get("mouse", True)
            self.mode_keys = config.get("mode_keys", "vi")
            self.status_position = config.get("status_position", "bottom")
            self.status_interval = config.get("status_interval", 15)
            self._status_timer = None
            self.set_titles = config.get("set_titles", False)
            self.title_fmt = config.get("title_fmt", "#S:#I:#W")
            self.aliases = config.get("aliases", {})
            self.hooks = config.get("hooks", {})
            self._attached = False
            self._prev_winc = 0
            self._prev_bell = False
            self._prompt_purpose = None
            self._prompt_action = None
            self.tab_bar_always = config.get("tab_bar_always", True)
            self.view = MultiplexerView()
            self.tabbar = TabBar()
            self.status = StatusBar(
                bg=config.get("status_bg"),       # None = 테마(textual-dark) 사용
                fg=config.get("status_fg"),
                left=config.get("status_left", " "),
                right=config.get("status_right",
                                 " #{pane_title}#h %H:%M %Y-%m-%d "))

        def compose(self) -> ComposeResult:
            yield self.tabbar
            yield self.view
            yield self.status

        async def on_mount(self):
            self.tabbar.display = self.tab_bar_always
            self.view.focus()
            if self.status_position == "top":
                self.status.styles.dock = "top"
            self._restart_status_timer()
            self.set_interval(1.0, self._clock_tick)  # clock-mode 초 단위 갱신
            try:
                self.reader, self.writer = await asyncio.open_unix_connection(
                    path=self.sock_path)
            except (ConnectionError, FileNotFoundError, OSError):
                self.exit(message="pytmux: 서버에 연결할 수 없습니다")
                return
            cols, rows = self._content_size()
            hello = {"t": "hello", "cols": cols, "rows": rows}
            if self.session_name:
                hello["session"] = self.session_name
            await write_msg(self.writer, hello)
            self.run_worker(self._reader_task(), exclusive=False)

        def _tabbar_visible(self):
            return self.tab_bar_always or len(self.status.windows) >= 2

        def set_tab_bar_always(self, flag):
            """상단 탭바 항상 표시 옵션을 런타임에 바꾼다(표시/뷰 크기 동기화)."""
            flag = bool(flag)
            if flag == self.tab_bar_always:
                return
            self.tab_bar_always = flag
            self._update_tabbar()

        def _content_size(self):
            size = self.size
            extra = 1 if self._tabbar_visible() else 0   # 상단 탭바 1줄
            return max(MIN_W, size.width), max(MIN_H, size.height - 1 - extra)

        def _active_tab_index(self):
            for t in self.status.windows:
                if t.get("active"):
                    return t["index"]
            return 0

        def _update_tabbar(self):
            """상태 갱신 시 탭바 데이터/표시 여부를 동기화. 표시가 바뀌면 뷰 크기가
            달라지므로 서버에 새 크기를 통지한다."""
            visible = self._tabbar_visible()
            self.tabbar.set_tabs(self.status.windows, self._active_tab_index())
            # 상단 탭바가 보이면 하단 상태줄의 탭 목록은 생략(중복 방지)
            if self.status.hide_tabs != visible:
                self.status.hide_tabs = visible
                self.status.refresh()
            if self.tabbar.display != visible:
                self.tabbar.display = visible
                self._send_resize()

        def _send_resize(self):
            if self.writer:
                cols, rows = self._content_size()
                import asyncio as _a
                _a.create_task(write_msg(
                    self.writer, {"t": "resize", "cols": cols, "rows": rows}))

        def confirm_popup(self, message, action, title="확인",
                          yes_label="닫기"):
            """중앙 확인 팝업을 띄우고, '예'면 action 실행."""
            def done(ok):
                if ok and action:
                    action()
            self.push_screen(
                ConfirmScreen(message, yes_label=yes_label, title=title), done)

        def confirm_kill_tab(self):
            self.confirm_popup(
                "이 탭을 닫을까요? 탭의 셸이 종료됩니다.",
                action=lambda: self.send_cmd("kill_window"),
                title="탭 닫기", yes_label="닫기")

        def _pane_above(self):
            """활성 패널 위쪽(같은 열 범위)에 다른 패널이 있는지."""
            act = self.layout.get("active")
            panes = self.layout.get("panes", [])
            ap = next((p for p in panes if p["id"] == act), None)
            if not ap:
                return False
            ax, ay = ap["x"], ap["y"]
            aw = ap["w"]
            for p in panes:
                if p["id"] == act:
                    continue
                if p["y"] + p["h"] <= ay and not (
                        p["x"] + p["w"] <= ax or p["x"] >= ax + aw):
                    return True
            return False

        async def _reader_task(self):
            while True:
                msg = await read_msg(self.reader)
                if msg is None:
                    self.exit()
                    return
                self._dispatch(msg)

        def _fire_hook(self, event):
            cmd = self.hooks.get(event)
            if cmd:
                self._run_command(cmd)

        def _dispatch(self, msg):
            t = msg.get("t")
            if t == "layout":
                self.layout = msg
                self._composite()
                if not self._attached:
                    self._attached = True
                    self._fire_hook("client-attached")
            elif t == "screen":
                self.pane_content[msg["pane"]] = (msg["rows"], msg.get("cursor"))
                self._composite()
            elif t == "status":
                self.status.update_status(msg)
                self._update_claude(msg.get("panes_claude", []))
                self._update_tabbar()
                if self.set_titles:
                    self.title = self.status._expand(self.title_fmt)
                wins = msg.get("windows", [])
                n = len(wins)
                if self._prev_winc and n > self._prev_winc:
                    self._fire_hook("after-new-window")
                self._prev_winc = n
                anybell = any(w.get("bell") for w in wins)
                if anybell and not self._prev_bell:
                    self._fire_hook("alert-bell")
                self._prev_bell = anybell
            elif t == "tree":
                if self._want_tree:
                    self._want_tree = False
                    self._open_choose_tree(msg)
            elif t == "layouts":
                if self._want_layouts:
                    mode = self._want_layouts
                    self._want_layouts = None
                    self._open_choose_layout(msg.get("names", []), mode)
            elif t == "buffers":
                if self._want_buffers:
                    self._want_buffers = False
                    self._open_choose_buffer(msg.get("items", []))
            elif t == "captured":
                self.display_message(f"{msg.get('chars', 0)} chars 버퍼에 캡처됨")
            elif t == "bye":
                self.exit(message="pytmux: 서버가 종료되었습니다")

        # ---- Claude Code 마지막 프롬프트 스티키 헤더 ----
        def _update_claude(self, panes_claude):
            self.pane_claude = {e["id"]: e for e in panes_claude}
            # 프롬프트가 바뀌면(새 프롬프트 제출) 닫아둔 헤더를 다시 보이게
            for pid, e in self.pane_claude.items():
                if (pid in self._claude_hidden
                        and self._claude_hidden[pid] != e.get("prompt")):
                    self._claude_hidden.pop(pid, None)

        def close_claude_header(self, pane_id):
            info = self.pane_claude.get(pane_id, {})
            self._claude_hidden[pane_id] = info.get("prompt", "")
            self._composite()

        def _draw_claude_headers(self, cells, W, H):
            """Claude Code 패널 내부 맨 윗줄에 마지막 프롬프트를 스티키 헤더로 표시.
            스크롤과 무관(합성 시 항상 내용 최상단에 덮어 그림). 좌측 [x] 로 닫기
            (우측은 탭 닫기 [x] 와 한 행 차이로 시각적으로 겹쳐 보이므로 좌측 배치)."""
            self._claude_close_zones = {}
            if not self.pane_claude:
                return
            hdr_st = Style(color="white", bgcolor=theme_color(self, "primary"),
                           bold=True)
            close_st = Style(color="white", bgcolor=theme_color(self, "error"),
                             bold=True)
            for p in self.layout.get("panes", []):
                info = self.pane_claude.get(p["id"])
                if not info or not info.get("claude") or not info.get("prompt"):
                    continue
                if self._claude_hidden.get(p["id"]) == info["prompt"]:
                    continue
                cx, cy, cw = p["x"], p["y"], p["w"]
                if cw < 6 or not (0 <= cy < H):
                    continue
                for xx in range(cx, min(cx + cw, W)):   # 헤더 배경
                    cells[cy][xx] = (" ", hdr_st)
                # 좌측 [x] (3칸) + 공백 1칸 후 프롬프트
                bx0 = cx
                for j, chh in enumerate("[x]"):
                    if 0 <= bx0 + j < W:
                        cells[cy][bx0 + j] = (chh, close_st)
                self._claude_close_zones[p["id"]] = (bx0, bx0 + 3, cy)
                text_start = cx + 4                      # [x] + space
                budget = max(0, cw - 4)
                gx = text_start
                for chh in "▷ " + info["prompt"]:
                    wch = _char_cells(chh)
                    if gx - text_start + wch > budget:
                        break
                    if 0 <= gx < W:
                        cells[cy][gx] = (chh, hdr_st)
                        if wch == 2 and gx + 1 - text_start < budget and \
                                0 <= gx + 1 < W:
                            cells[cy][gx + 1] = ("", hdr_st)
                    gx += wch

        # ---- clock-mode(패널 전체를 덮는 큰 시계) ----
        def toggle_clock(self, pane_id):
            if pane_id is None:
                return
            if pane_id in self.clock_panes:
                self.clock_panes.discard(pane_id)
            else:
                self.clock_panes.add(pane_id)
            self._composite()

        def _clock_tick(self):
            # 1초마다: 시계 패널이 있으면 시각 갱신(뒤 화면도 함께 다시 합성)
            if self.clock_panes:
                self._composite()

        @staticmethod
        def _put_cell(cells, x, y, ch, st, W, H):
            """단일폭 글자를 cell 그리드에 정렬을 깨지 않고 써넣는다.

            배경에 한글 등 와이드 문자(2칸: 본체+빈 연속셀 "")가 있을 때 그 절반만
            덮으면 짝 셀이 어긋나 행 전체가 밀린다(예: clock-mode 시계가 깨짐).
            덮어쓰는 자리의 와이드 짝 셀을 공백으로 정리해 정렬을 보존한다.
            (오버레이가 배경 글자 일부를 지우는 것은 의도된 동작)"""
            if not (0 <= x < W and 0 <= y < H):
                return
            row = cells[y]
            if row[x][0] == "" and x > 0:
                # 이 자리가 와이드 문자의 둘째(연속) 칸 → 왼쪽 본체를 공백으로.
                row[x - 1] = (" ", row[x - 1][1])
            elif _char_cells(row[x][0]) == 2 and x + 1 < W and row[x + 1][0] == "":
                # 이 자리가 와이드 문자의 본체 → 오른쪽 연속 칸을 공백으로.
                row[x + 1] = (" ", row[x + 1][1])
            row[x] = (ch, st)

        def _draw_clock_overlay(self, cells, W, H, active):
            """clock-mode 패널을 큰 시계로 덮는다. 뒤의 패널 출력은 흐리게(dim)
            계속 보이고, 우상단 [x] 로 닫을 수 있다."""
            self._clock_close_zones = {}
            if not self.clock_panes:
                return
            dim = Style(dim=True)
            digit_st = Style(color=theme_color(self, "success"), bold=True)
            close_st = Style(color="white", bgcolor=theme_color(self, "error"),
                             bold=True)
            now = datetime.now().strftime("%H:%M:%S")
            glyphs = [_CLOCK_FONT.get(c, ["   "] * 5) for c in now]
            cw = sum(len(g[0]) for g in glyphs) + (len(glyphs) - 1)
            ch_h = 5
            for p in self.layout.get("panes", []):
                if p["id"] not in self.clock_panes:
                    continue
                px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]
                # 1) 뒤 화면 흐리게(계속 업데이트되는 내용도 dim 으로 보임)
                for yy in range(py, min(py + ph, H)):
                    for xx in range(px, min(px + pw, W)):
                        c, st = cells[yy][xx]
                        cells[yy][xx] = (c, st + dim)
                # 2) 큰 시계(공간 충분) 또는 단순 시각
                if pw >= cw and ph >= ch_h:
                    ox = px + (pw - cw) // 2
                    oy = py + (ph - ch_h) // 2
                    for row in range(ch_h):
                        gx = ox
                        for g in glyphs:
                            for c in g[row]:
                                if c != " ":
                                    self._put_cell(cells, gx, oy + row, c,
                                                   digit_st, W, H)
                                gx += 1
                            gx += 1   # 글자 사이 간격
                else:
                    ox = px + max(0, (pw - len(now)) // 2)
                    oy = py + ph // 2
                    for j, c in enumerate(now):
                        self._put_cell(cells, ox + j, oy, c, digit_st, W, H)
                # 3) 우상단 닫기 버튼 [x]
                bx0 = px + pw - 3
                if bx0 >= px and 0 <= py < H:
                    for j, c in enumerate("[x]"):
                        self._put_cell(cells, bx0 + j, py, c, close_st, W, H)
                    self._clock_close_zones[p["id"]] = (bx0, bx0 + 3, py)

        def _composite(self):
            W = self.layout.get("cols", self.size.width)
            H = self.layout.get("rows", max(1, self.size.height - 1))
            cells = [[(" ", DEFAULT_STYLE) for _ in range(W)] for _ in range(H)]
            active = self.layout.get("active")
            for p in self.layout.get("panes", []):
                content = self.pane_content.get(p["id"])
                if not content:
                    continue
                rows, cursor = content
                for ry, row in enumerate(rows):
                    if ry >= p["h"]:
                        break
                    gy = p["y"] + ry
                    if not (0 <= gy < H):
                        continue
                    cx = p["x"]
                    for text, style_d in row:
                        st = make_style(style_d)
                        for chh in text:
                            if cx - p["x"] >= p["w"]:
                                break
                            if 0 <= cx < W:
                                cells[gy][cx] = (chh, st)
                            wch = _char_cells(chh)
                            # 와이드 문자: 다음 칸은 연속 셀(렌더 시 건너뜀)
                            if wch == 2 and 0 <= cx + 1 < W and \
                                    (cx + 1 - p["x"]) < p["w"]:
                                cells[gy][cx + 1] = ("", st)
                            cx += wch
                # 활성 패널 커서
                if cursor and p["id"] == active:
                    ccx, ccy = cursor
                    gx, gy = p["x"] + ccx, p["y"] + ccy
                    if 0 <= gx < W and 0 <= gy < H:
                        ch, st = cells[gy][gx]
                        cells[gy][gx] = (ch, st + Style(reverse=True))
            # 패널 테두리 박스: 비활성=회색, 활성=파란색. 경계 셀은 인접 패널이
            # 공유하므로, 비활성 박스를 먼저 그리고 활성 박스를 마지막에 덮어
            # 활성 패널의 경계 전체가 파란색이 되도록 한다.
            inactive_box = Style(color="grey42")
            active_box = Style(color=theme_color(self, "primary"), bold=True)
            show_title = self.layout.get("border_status")
            # 박스 문자 ↔ 변 비트(U=8,D=4,L=2,R=1): 겹치는 경계를 합쳐 ┬┴├┤┼ 로 연결
            bbits = {"─": 0b0011, "│": 0b1100, "┌": 0b0101, "┐": 0b0110,
                     "└": 0b1001, "┘": 0b1010, "├": 0b1101, "┤": 0b1110,
                     "┬": 0b0111, "┴": 0b1011, "┼": 0b1111}
            brev = {v: k for k, v in bbits.items()}

            def _draw_box(p):
                box = p.get("box")
                if not box:
                    return
                bx, by, bw, bh = box
                st = active_box if p["id"] == active else inactive_box
                x2, y2 = bx + bw - 1, by + bh - 1

                def put(gx, gy, chc):
                    if not (0 <= gx < W and 0 <= gy < H):
                        return
                    cur = cells[gy][gx][0]
                    if cur in bbits and chc in bbits:   # 경계끼리 만나면 변을 합침
                        chc = brev[bbits[cur] | bbits[chc]]
                    cells[gy][gx] = (chc, st)

                for gx in range(bx + 1, x2):      # 모서리 제외(상/하)
                    put(gx, by, "─")
                    put(gx, y2, "─")
                for gy in range(by + 1, y2):      # 모서리 제외(좌/우)
                    put(bx, gy, "│")
                    put(x2, gy, "│")
                put(bx, by, "┌")                  # 모서리는 인접 박스와만 병합
                put(x2, by, "┐")
                put(bx, y2, "└")
                put(x2, y2, "┘")

            def _draw_title(p):
                """패널 이름을 위쪽 테두리 중앙에 표기(리네임됐거나 border-status).

                테두리를 모두 그린 뒤 별도 패스로 호출해, 인접 패널의 경계선이
                이름을 덮어쓰지 않게 한다. 색은 박스 색(활성=파랑/비활성=회색)."""
                box = p.get("box")
                if not box:
                    return
                bx, by, bw, _bh = box
                title = (p.get("title") or "").strip()
                renamed = title and title != "shell"
                if not ((show_title or renamed) and title and bw >= 4):
                    return
                st = active_box if p["id"] == active else inactive_box
                label = f" {title} "[: bw - 2]
                start = bx + max(1, (bw - len(label)) // 2)  # 중앙 정렬
                for i, chc in enumerate(label):
                    gx = start + i
                    if bx < gx < bx + bw - 1 and 0 <= by < H:  # 모서리 침범 방지
                        cells[by][gx] = (chc, st)

            boxes = self.layout.get("panes", [])
            for p in boxes:               # 1) 비활성 테두리 → 2) 활성 테두리(위에)
                if p["id"] != active:
                    _draw_box(p)
            for p in boxes:
                if p["id"] == active:
                    _draw_box(p)
            for p in boxes:               # 3) 이름은 테두리 위에(활성 이름 최상위)
                if p["id"] != active:
                    _draw_title(p)
            for p in boxes:
                if p["id"] == active:
                    _draw_title(p)
            # 패널 제목 경계선(pane-border-status)
            for tb in self.layout.get("titlebars", []):
                is_active = tb.get("active")
                st = Style(color="black", bgcolor="cyan" if is_active else "white")
                label = f" {tb['title']} "
                gy = tb["y"]
                if not (0 <= gy < H):
                    continue
                for i in range(tb["w"]):
                    gx = tb["x"] + i
                    chh = label[i] if i < len(label) else "─"
                    s = st if i < len(label) else Style(color="grey50")
                    if 0 <= gx < W:
                        cells[gy][gx] = (chh, s)
            # copy-mode 선택 영역 하이라이트
            sel = self.view._sel
            if sel:
                sx0, sy0, sx1, sy1 = sel
                if (sy0, sx0) > (sy1, sx1):
                    sx0, sy0, sx1, sy1 = sx1, sy1, sx0, sy0
                for yy in range(max(0, sy0), min(H, sy1 + 1)):
                    a = sx0 if yy == sy0 else 0
                    b = sx1 if yy == sy1 else W - 1
                    for xx in range(max(0, a), min(W, b + 1)):
                        c, sstl = cells[yy][xx]
                        cells[yy][xx] = (c, sstl + Style(reverse=True))
            # display-panes 오버레이: 각 패널 중앙에 번호 표시
            if self.mode == "display":
                for i, p in enumerate(self.layout.get("panes", [])):
                    label = str(i)
                    cx0 = p["x"] + max(0, (p["w"] - len(label)) // 2)
                    cy0 = p["y"] + p["h"] // 2
                    st = Style(color="black", bold=True,
                               bgcolor="green" if p["id"] == active else "yellow")
                    for j, chh in enumerate(label):
                        self._put_cell(cells, cx0 + j, cy0, chh, st, W, H)
            # Claude Code 마지막 프롬프트 스티키 헤더(내용 최상단)
            self._draw_claude_headers(cells, W, H)
            # 현재 탭 닫기 [x]: 콘텐츠 영역 오른쪽 위 모서리(상단 테두리 위)
            self._draw_tab_close(cells, W, H)
            # clock-mode 오버레이(패널 전체 덮기, 뒤 화면 dim)
            self._draw_clock_overlay(cells, W, H, active)
            self.view.set_frame(cells)

        def _draw_tab_close(self, cells, W, H):
            """현재 탭(윈도우) 닫기 [x] 버튼을 콘텐츠 영역 오른쪽 위 모서리에 그린다.
            (이전엔 상단 탭바 오른쪽 끝에 있던 것을 패널 위로 옮김.) 상단 테두리
            행(0)에 그려 Claude 헤더(내용 첫 행)·시계와 겹치지 않는다."""
            self._tab_close_zone = None
            if W < 3 or H < 1:
                return
            st = Style(color="white", bgcolor=theme_color(self, "error"), bold=True)
            bx0 = W - 3
            for j, chh in enumerate("[x]"):
                gx = bx0 + j
                if 0 <= gx < W:
                    cells[0][gx] = (chh, st)
            self._tab_close_zone = (bx0, W, 0)

        # ---- 송신 헬퍼 ----
        def send_cmd(self, action, **kw):
            if self.writer:
                kw["t"] = "cmd"
                kw["action"] = action
                # 새 탭/패널은 설정된 시작 디렉토리(default-path)를 함께 보낸다.
                # 서버가 current/home/<경로> 를 해석한다. 호출부에서 명시하면 우선.
                if action in ("split", "new_window") and "path" not in kw:
                    kw["path"] = getattr(self, "default_path", "current")
                asyncio.create_task(write_msg(self.writer, kw))

        def send_input(self, data: bytes):
            if self.writer and data:
                asyncio.create_task(write_msg(self.writer, {
                    "t": "input", "pane": self.layout.get("active"),
                    "data": base64.b64encode(data).decode("ascii")}))

        def send_scroll(self, pane_id, delta=0, bottom=False, top=False):
            if self.writer:
                m = {"t": "scroll", "pane": pane_id}
                if bottom:
                    m["bottom"] = True
                elif top:
                    m["top"] = True
                else:
                    m["delta"] = delta
                asyncio.create_task(write_msg(self.writer, m))

        # ---- 복사/버퍼 ----
        @staticmethod
        def _clipboard_copy(text):
            """OS 클립보드로 복사(pbcopy/xclip/wl-copy)."""
            import shutil
            for cmd in (["pbcopy"], ["xclip", "-selection", "clipboard"],
                        ["wl-copy"]):
                if shutil.which(cmd[0]):
                    try:
                        subprocess.run(cmd, input=text.encode("utf-8"), timeout=2)
                        return True
                    except Exception:
                        pass
            return False

        @staticmethod
        def _clipboard_paste():
            import shutil
            for cmd in (["pbpaste"], ["xclip", "-selection", "clipboard", "-o"],
                        ["wl-paste", "-n"]):
                if shutil.which(cmd[0]):
                    try:
                        return subprocess.run(cmd, capture_output=True, timeout=2
                                              ).stdout.decode("utf-8", "ignore")
                    except Exception:
                        pass
            return ""

        def copy_text(self, text):
            # 서버 페이스트 버퍼 + OS 클립보드 양쪽에 저장
            self.send_cmd("set_buffer", text=text)
            clip = self._clipboard_copy(text)
            self.display_message(
                f"{len(text)} chars 복사됨" + (" (클립보드)" if clip else ""))

        def choose_buffer(self):
            self._want_buffers = True
            self.send_cmd("request_buffers")

        def _open_choose_buffer(self, items):
            def handle(idx):
                if idx is not None:
                    self.send_cmd("paste_buffer", index=idx)
            self.push_screen(ChooseBufferScreen(items), handle)

        # ---- choose-tree ----
        def request_tree(self):
            self._want_tree = True
            self.send_cmd("request_tree")

        def _open_choose_tree(self, tree):
            def handle(res):
                if res:
                    _name, idx = res
                    self.send_cmd("select_window", index=idx)
            self.push_screen(ChooseTreeScreen(tree), handle)

        # ---- 레이아웃 저장/불러오기 ----
        def save_layout_prompt(self):
            self.open_prompt("save_layout", "레이아웃 이름으로 저장")

        def request_layouts(self, mode):
            """저장된 레이아웃 목록을 요청(mode: 'over'=현재 탭 덮어쓰기, 'new'=새 탭)."""
            self._want_layouts = mode
            self.send_cmd("list_layouts")

        def _open_choose_layout(self, names, mode):
            title = "레이아웃 → 새 탭" if mode == "new" else "레이아웃 → 현재 탭 덮어쓰기"

            def handle(name):
                if name:
                    self.send_cmd("load_tab_layout", name=name,
                                  new=(mode == "new"))
            self.push_screen(ChooseLayoutScreen(names, title), handle)

        # ---- 메뉴 ----
        def open_menu(self):
            def handle(result):
                if result:
                    self._run_menu_action(result)
            self.push_screen(MenuScreen(), handle)

        def _run_menu_action(self, key):
            if key == "split_lr":
                self.send_cmd("split", orient="lr")
            elif key == "split_tb":
                self.send_cmd("split", orient="tb")
            elif key == "zoom":
                self.send_cmd("zoom")
            elif key == "kill_pane":
                self.send_cmd("kill_pane")
            elif key == "sync":
                self.send_cmd("set_sync")
            elif key == "autoresume":
                self.send_cmd("set_autoresume")
            elif key == "choose_tree":
                self.request_tree()
            elif key == "new_window":
                self.send_cmd("new_window")
            elif key == "rename_window":
                self.open_prompt("rename_window", "rename-tab",
                                 self._active_window_name())
            elif key == "kill_window":
                self.confirm_kill_tab()
            elif key == "next_window":
                self.send_cmd("next_window")
            elif key == "prev_window":
                self.send_cmd("prev_window")
            elif key == "layout_save":
                self.save_layout_prompt()
            elif key == "layout_load_over":
                self.request_layouts("over")
            elif key == "layout_load_new":
                self.request_layouts("new")
            elif key == "command":
                self.open_prompt("command", "")
            elif key == "detach":
                self.exit(message="detached")
            elif key == "kill_server":
                self.send_cmd("kill_server")

        # ---- 프롬프트 / 명령 ----
        def display_message(self, text, secs=2.0):
            self.status.message = text
            self.status.refresh()
            self.set_timer(secs, self._clear_message)

        def _clear_message(self):
            self.status.message = None
            self.status.refresh()

        def _restart_status_timer(self):
            if self._status_timer is not None:
                self._status_timer.stop()
            self._status_timer = self.set_interval(
                self.status_interval, self.status.refresh)

        def apply_option(self, name, val):
            """클라이언트 측 옵션을 런타임에 적용."""
            if name == "prefix":
                self.prefix_key = _tmux_key_to_textual(val)
                self.prefix_bytes = _key_to_ctrl_bytes(self.prefix_key)
            elif name == "mouse":
                self.mouse_enabled = val.lower() in ("on", "true", "1", "yes")
            elif name == "status-bg":
                self.status.bg = val
                self.status.refresh()
            elif name == "status-fg":
                self.status.fg = val
                self.status.refresh()
            elif name == "mode-keys":
                self.mode_keys = "emacs" if val == "emacs" else "vi"
            elif name == "status-left":
                self.status.left_fmt = val
                self.status.refresh()
            elif name == "status-right":
                self.status.right_fmt = val
                self.status.refresh()
            elif name == "status-position":
                self.status_position = "top" if val == "top" else "bottom"
                self.status.styles.dock = self.status_position
            elif name == "status-interval":
                try:
                    self.status_interval = max(1, int(val))
                    self._restart_status_timer()
                except ValueError:
                    pass
            elif name == "set-titles":
                self.set_titles = val.lower() in ("on", "true", "1", "yes")
            elif name == "set-titles-string":
                self.title_fmt = val
            elif name in ("tab-bar", "tabbar"):
                self.set_tab_bar_always(
                    val.lower() in ("always", "on", "true", "1", "yes"))

        def show_options(self):
            lines = [
                f"prefix      {self.prefix_key}",
                f"mouse       {'on' if self.mouse_enabled else 'off'}",
                f"status-bg   {self.status.bg}",
                f"status-fg   {self.status.fg}",
                f"mode-keys   {self.mode_keys}",
            ]
            self.push_screen(InfoScreen(lines, title="options"))

        def reload_config(self, path=None):
            cfg = load_config(path)
            self.prefix_key = cfg["prefix"]
            self.prefix_bytes = _key_to_ctrl_bytes(self.prefix_key)
            self.bindings = cfg["bindings"]
            self.aliases = cfg.get("aliases", {})
            self.hooks = cfg.get("hooks", {})
            self.mouse_enabled = cfg["mouse"]
            self.mode_keys = cfg["mode_keys"]
            self.status.bg = cfg["status_bg"]
            self.status.fg = cfg["status_fg"]
            if "status_left" in cfg:
                self.status.left_fmt = cfg["status_left"]
            if "status_right" in cfg:
                self.status.right_fmt = cfg["status_right"]
            self.set_tab_bar_always(cfg.get("tab_bar_always", True))
            self.default_path = cfg.get("default_path", "current")
            self.status.refresh()

        def _active_window_name(self):
            for w in self.status.windows:
                if w.get("active"):
                    return w.get("name", "")
            return ""

        def open_prompt(self, purpose, placeholder="", initial="", action=None):
            # 한 줄 입력을 Input 을 담은 바닥 모달(PromptScreen)로 받는다.
            # 모달은 별도 스크린이라 포커스가 안정적이다(메인 뷰/AUTO_FOCUS 와 무관).
            suggester = None
            if purpose == "command":
                suggester = SuggestFromList(COMPLETIONS, case_sensitive=False)
            self.push_screen(
                PromptScreen(purpose, placeholder, initial, suggester),
                lambda val: self._prompt_done(purpose, action, val))

        def _prompt_done(self, purpose, action, val):
            if purpose == "search":
                self.mode = "scroll"  # 검색은 스크롤백 모드 유지/복귀
            if val is None:  # 취소(Esc)
                return
            val = val.strip()
            if purpose == "command":
                self._run_command(val)
            elif purpose == "rename_window":
                if val:
                    self.send_cmd("rename_window", name=val)
            elif purpose == "rename_pane":
                self.send_cmd("set_pane_title", title=val)
            elif purpose == "move_window":
                if val.lstrip("-").isdigit():
                    self.send_cmd("move_window", index=int(val))
            elif purpose == "save_layout":
                if val.strip():
                    self.send_cmd("save_tab_layout", name=val.strip())
            elif purpose == "search":
                if val:
                    self.send_cmd("search", query=val, direction="up")
            elif purpose == "confirm":
                if val.lower().startswith("y") and action:
                    action()

        @staticmethod
        def _opt_value(args, flag):
            if flag in args:
                i = args.index(flag)
                if i + 1 < len(args):
                    return args[i + 1]
            return None

        @staticmethod
        def _first_int(args):
            for a in args:
                if a.lstrip("-").isdigit():
                    return int(a.lstrip("-")) if not a.startswith("-") else None
                if a.isdigit():
                    return int(a)
            return None

        def _run_shell(self, cmd):
            try:
                res = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True,
                                     timeout=15)
                text = res.stdout.decode("utf-8", "ignore")
                rc = res.returncode
            except Exception as e:
                text, rc = str(e), 1
            if text.strip():
                self.send_cmd("set_buffer", text=text)
                self.push_screen(InfoScreen(text.splitlines()[:40], title="run-shell"))
            return rc

        def _if_shell(self, cond, then_cmd, else_cmd=None):
            try:
                rc = subprocess.run(["/bin/sh", "-c", cond], capture_output=True,
                                    timeout=15).returncode
            except Exception:
                rc = 1
            if rc == 0:
                self._run_command(then_cmd)
            elif else_cmd:
                self._run_command(else_cmd)

        _SENDKEYS = {"Enter": b"\r", "Tab": b"\t", "Space": b" ",
                     "Escape": b"\x1b", "BSpace": b"\x7f", "Up": b"\x1b[A",
                     "Down": b"\x1b[B", "Right": b"\x1b[C", "Left": b"\x1b[D"}

        def _send_keys(self, args):
            literal = "-l" in args
            toks = [a for a in args if not a.startswith("-")]
            out = b""
            for a in toks:
                if not literal and a in self._SENDKEYS:
                    out += self._SENDKEYS[a]
                elif (not literal and a.startswith("C-") and len(a) == 3
                      and a[2].isalpha()):
                    out += bytes([ord(a[2].lower()) - 96])
                else:
                    out += a.encode("utf-8")
            if out:
                self.send_input(out)

        def _run_command(self, line, _depth=0):
            """tmux 류 명령 문자열을 해석해 서버 명령으로 변환한다."""
            if not line or _depth > 8:
                return
            try:
                parts = shlex.split(line)
            except ValueError:
                parts = line.split()
            if not parts:
                return
            c = parts[0].lower()
            args = parts[1:]
            # 사용자 별칭 확장
            if c in self.aliases:
                return self._run_command(
                    self.aliases[c] + (" " + " ".join(args) if args else ""),
                    _depth + 1)
            if c in ("help", "commands", "?", "list-commands"):
                # 명령 목록 선택기: 방향키로 선택 → Enter 로 명령 프롬프트에 채움
                # → 다시 Enter 로 실행(인자 추가 가능)
                def _picked(name):
                    if name:
                        self.open_prompt("command", "", initial=name + " ")
                self.push_screen(CommandListScreen(COMMANDS), _picked)
                return
            if c in ("run-shell", "run"):
                if args:
                    self._run_shell(args[0])
                return
            if c in ("if-shell", "if"):
                if len(args) >= 2:
                    self._if_shell(args[0], args[1], args[2] if len(args) > 2 else None)
                return
            if c in ("split-window", "splitw"):
                # -h = 가로(상/하), -v = 세로(좌/우)
                orient = "tb" if "-h" in args else (
                    "lr" if "-v" in args else "tb")
                self.send_cmd("split", orient=orient)
            elif c in ("kill-pane", "killp"):
                self.send_cmd("kill_pane")
            elif c in ("new-tab", "newt", "new-window", "neww"):
                self.send_cmd("new_window")
            elif c in ("kill-tab", "killt", "kill-window", "killw"):
                self.send_cmd("kill_window")
            elif c in ("next-tab", "next-window", "next"):
                self.send_cmd("next_window")
            elif c in ("previous-tab", "prev-tab", "previous-window", "prev"):
                self.send_cmd("prev_window")
            elif c in ("last-tab", "last-window", "last"):
                self.send_cmd("last_window")
            elif c == "automatic-rename" or (
                    c == "setw" and "automatic-rename" in args):
                val = None
                if "on" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_auto_rename", value=val)
            elif (c in ("monitor-activity", "monitor-bell")) or (
                    c == "setw" and any("monitor-" in a for a in args)):
                which = "bell" if ("bell" in c or "monitor-bell" in args) else "activity"
                val = None
                if "on" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_monitor", which=which, value=val)
            elif c in ("move-tab-left", "move-tab-right",
                       "move-tab-first", "move-tab-last"):
                self.send_cmd("move_current_tab", where=c[len("move-tab-"):])
            elif c in ("move-tab", "movet", "move-window", "movew"):
                idx = self._opt_value(args, "-t")
                idx = int(idx) if idx and idx.isdigit() else self._first_int(args)
                if idx is not None:
                    self.send_cmd("move_window", index=idx)
            elif c in ("swap-tab", "swapt", "swap-window", "swapw"):
                idx = self._opt_value(args, "-t")
                idx = int(idx) if idx and idx.isdigit() else self._first_int(args)
                if idx is not None:
                    self.send_cmd("swap_window", index=idx)
            elif c in ("choose-tree", "choose-tab", "choose-window"):
                self.request_tree()
            elif c in ("select-pane", "selectp"):
                if "-T" in args:
                    title = " ".join(args[args.index("-T") + 1:])
                    self.send_cmd("set_pane_title", title=title)
                else:
                    for flag, d in (("-L", "left"), ("-R", "right"),
                                    ("-U", "up"), ("-D", "down")):
                        if flag in args:
                            self.send_cmd("select_pane", dir=d)
                            break
            elif c == "rename-pane":
                self.send_cmd("set_pane_title", title=" ".join(args))
            elif c in ("select-tab", "selectt", "select-window", "selectw"):
                idx = self._opt_value(args, "-t")
                idx = int(idx) if idx and idx.isdigit() else self._first_int(args)
                if idx is not None:
                    self.send_cmd("select_window", index=idx)
            elif c in ("rename-tab", "renamet", "rename-window", "renamew"):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("rename_window", name=name)
                else:
                    self.open_prompt("rename_window", "rename-tab",
                                     self._active_window_name())
            elif c in ("resize-pane", "resizep"):
                if "-Z" in args:
                    self.send_cmd("zoom")
            elif c == "zoom":
                self.send_cmd("zoom")
            elif c in ("select-layout", "selectl"):
                if args:
                    self.send_cmd("select_layout", preset=args[0])
                else:
                    self.send_cmd("cycle_layout")
            elif c in ("next-layout", "nextl"):
                self.send_cmd("cycle_layout")
            elif c in ("rotate-window", "rotatew"):
                self.send_cmd("rotate", forward=("-D" not in args))
            elif c in ("swap-pane", "swapp"):
                self.send_cmd("swap_pane", forward=("-U" not in args))
            elif c in ("break-pane", "breakp"):
                self.send_cmd("break_pane")
            elif c in ("join-pane", "joinp"):
                self.send_cmd("join_pane", orient=("lr" if "-h" in args else "tb"))
            elif c in ("respawn-pane", "respawnp"):
                self.send_cmd("respawn_pane")
            elif c in ("auto-resume", "autoresume"):
                val = None
                if "on" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_autoresume", value=val)
            elif c in ("auto-resume-message", "autoresume-message"):
                self.send_cmd("set_autoresume", msg=" ".join(args))
            elif c in ("capture-output", "capture-toggle"):
                val = None
                if "on" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_capture", value=val)
                self.display_message("출력 캡처 " + ("토글" if val is None else
                                     ("ON" if val else "OFF")) + " (상태줄 REC)")
            elif c in ("synchronize-panes", "syncp") or (
                    c == "setw" and "synchronize-panes" in args):
                val = None
                if "on" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_sync", value=val)
            elif c == "setw" and "pane-border-status" in args or \
                    c == "pane-border-status":
                val = None
                if "on" in args or "top" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_border_status", value=val)
            elif c in ("detach-client", "detach"):
                if "-a" in args:
                    self.send_cmd("detach_others")
                else:
                    self.exit(message="detached")
            elif c == "kill-server":
                self.send_cmd("kill_server")
            elif c in ("paste-clipboard", "pasteb-clip"):
                txt = self._clipboard_paste()
                if txt:
                    self.send_cmd("paste", text=txt)  # bracketed 패스스루
            elif c in ("send-keys", "send"):
                self._send_keys(args)
            elif c in ("paste-buffer", "pasteb"):
                idx = self._first_int(args)
                self.send_cmd("paste_buffer", index=idx or 0)
            elif c in ("capture-pane", "capturep"):
                self.send_cmd("capture_pane", full=("-S" in args or "-a" in args))
            elif c in ("pipe-pane", "pipep"):
                self.send_cmd("pipe_pane",
                              cmd=" ".join(a for a in args if not a.startswith("-")))
            elif c == "save-layout":
                self.send_cmd("save_layout")
            elif c == "restore-layout":
                self.send_cmd("restore_layout")
            elif c in ("layout-save", "save-tab-layout"):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("save_tab_layout", name=name)
                else:
                    self.save_layout_prompt()
            elif c in ("layout-load", "load-tab-layout"):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("load_tab_layout", name=name,
                                  new=("-n" in args))
                else:
                    self.request_layouts("new" if "-n" in args else "over")
            elif c in ("layout-load-new",):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("load_tab_layout", name=name, new=True)
                else:
                    self.request_layouts("new")
            elif c in ("layout-list", "list-layouts"):
                self.request_layouts("over")
            elif c in ("choose-buffer", "list-buffers", "lsb"):
                self.choose_buffer()
            elif c in ("clear-history", "clearhist"):
                self.send_cmd("clear_history")
            elif c in ("clock-mode", "clock"):
                self.toggle_clock(self.layout.get("active"))
            elif c in ("display-popup", "popup"):
                cmd = " ".join(a for a in args if not a.startswith("-"))
                if cmd:
                    try:
                        res = subprocess.run(["/bin/sh", "-c", cmd],
                                             capture_output=True, timeout=30)
                        text = (res.stdout + res.stderr).decode("utf-8", "ignore")
                    except Exception as e:
                        text = str(e)
                    self.push_screen(InfoScreen(
                        text.splitlines()[:60] or ["(출력 없음)"], title="popup"))
                else:
                    self.push_screen(InfoScreen(["display-popup <command>"],
                                                title="popup"))
            elif c in ("source-file", "source"):
                self.reload_config(args[0] if args else None)
            elif c in ("set", "set-option"):
                opts = [a for a in args if not a.startswith("-")]
                if len(opts) >= 2:
                    self.apply_option(opts[0], " ".join(opts[1:]))
            elif c in ("show-options", "show"):
                self.show_options()
            elif c == "set-hook":
                if "-u" in args and len(args) >= 2:
                    self.hooks.pop(args[args.index("-u") + 1], None)
                else:
                    opts = [a for a in args if not a.startswith("-")]
                    if len(opts) >= 2:
                        self.hooks[opts[0]] = " ".join(opts[1:])
            elif c in ("display-message", "display", "displaym"):
                self.display_message(" ".join(args) if args else "")
            elif c == "show-hooks":
                self.push_screen(InfoScreen(
                    [f"{k} → {v}" for k, v in self.hooks.items()], title="hooks"))
            # 알 수 없는 명령은 조용히 무시

        def on_paste(self, event: events.Paste):
            # 외부 터미널의 붙여넣기(멀티라인 포함)를 활성 패널로 패스스루.
            # 내부 앱이 bracketed paste 를 켰으면 서버가 마커로 감싼다.
            # (이미지 붙여넣기는 내부 Claude Code 가 공유 OS 클립보드에서 읽음)
            if len(self.screen_stack) > 1:
                return  # 프롬프트/모달 입력은 그 스크린이 처리
            if self.writer and event.text:
                self.send_cmd("paste", text=event.text)
            event.stop()

        # ---- 이벤트 ----
        async def on_resize(self, event):
            if self.writer:
                cols, rows = self._content_size()
                await write_msg(self.writer, {"t": "resize", "cols": cols, "rows": rows})
            # iOS(Blink 등)에서 소프트 키보드를 닫았다 열면 resize 가 키보드
            # 애니메이션 중간 크기로만 오고 최종 크기 이벤트가 누락될 수 있다.
            # 그 결과 마지막 행(하단 테두리)이 갱신되지 않은 채 남는다. 잠시 뒤
            # 정착된 크기로 한 번 더 통지해 새 프레임을 받아 재합성한다.
            self.set_timer(0.3, self._send_resize)

        def on_key(self, event: events.Key):
            # 메뉴/프롬프트 등 모달이 떠 있으면 그 스크린이 처리
            if len(self.screen_stack) > 1:
                return
            if self.mode == "prefix":
                self._handle_prefix(event)
                event.prevent_default()
                event.stop()
                return
            if self.mode == "scroll":
                self._handle_scroll_key(event)
                event.prevent_default()
                event.stop()
                return
            if self.mode == "display":
                self._handle_display_key(event)
                event.prevent_default()
                event.stop()
                return
            if self.mode == "esc":
                self._handle_esc_mode(event)
                event.prevent_default()
                event.stop()
                return
            # normal
            # ESC: 명령 모드 진입(키를 명령으로 받음). 셸로는 전달하지 않음.
            if event.key == "escape":
                self.mode = "esc"
                self.status.cmd_mode = True
                self.status.refresh()
                event.prevent_default()
                event.stop()
                return
            # F12: 바로 명령 프롬프트 진입(ESC 모드가 아닐 때).
            # 중첩 prefix 가로채기 토글은 prefix F12 로 이동.
            if event.key == "f12":
                self.open_prompt("command", "")
                event.prevent_default()
                event.stop()
                return
            if self.prefix_enabled and _normalize_key(event.key) == self.prefix_key:
                self.mode = "prefix"
                event.prevent_default()
                event.stop()
                return
            data = key_to_bytes(event)
            if data:
                self.send_input(data)
            event.prevent_default()
            event.stop()

        def _handle_prefix(self, event: events.Key):
            self.mode = "normal"
            # IME(한글 자모)가 켜져 있어도 동작하도록 키를 QWERTY 로 정규화
            k = _normalize_key(event.key)
            ch = event.character
            nch = _JAMO.get(ch, ch) if ch else ch  # 자모 → 영문
            # prefix 를 한 번 더 누르면 prefix 키 자체를 셸로 전송
            if k == self.prefix_key:
                self.send_input(self.prefix_bytes)
                return
            # 사용자 정의 바인딩 우선 (config 의 bind)
            token = nch if (nch and nch.isprintable() and not k.startswith("ctrl+")) else k
            if token in self.bindings:
                self._run_command(self.bindings[token])
                return
            if k == "f12":   # prefix F12: 중첩 prefix 가로채기 토글
                self.prefix_enabled = not self.prefix_enabled
                self.status.prefix_off = not self.prefix_enabled
                self.status.refresh()
                self.display_message("outer prefix " +
                                     ("ON" if self.prefix_enabled else "OFF (중첩)"))
                return
            if k == "percent_sign" or ch == "%":
                self.send_cmd("split", orient="lr")
            elif k == "quotation_mark" or ch == '"':
                self.send_cmd("split", orient="tb")
            elif k == "x":
                self.open_prompt("confirm", "kill-pane? (y/N)",
                                 action=lambda: self.send_cmd("kill_pane"))
            elif k == "z":
                self.send_cmd("zoom")
            elif k == "o":
                self.send_cmd("cycle_pane")
            elif k == "semicolon" or ch == ";":
                self.send_cmd("last_pane")
            elif k == "q":
                self._enter_display()
            elif k == "space":
                self.send_cmd("cycle_layout")
            elif k == "ctrl+o":
                self.send_cmd("rotate", forward=True)
            elif ch == "{":
                self.send_cmd("swap_pane", forward=False)
            elif ch == "}":
                self.send_cmd("swap_pane", forward=True)
            elif ch == "!":
                self.send_cmd("break_pane")
            elif k in ("left", "right", "up", "down"):
                self.send_cmd("select_pane", dir=k)
            elif k in ("H", "J", "K", "L"):
                self.send_cmd("resize_dir",
                              dir={"H": "left", "L": "right",
                                   "K": "up", "J": "down"}[k], cells=3)
            elif k == "c":
                self.send_cmd("new_window")
            elif k == "comma" or ch == ",":
                self.open_prompt("rename_window", "rename-tab",
                                 self._active_window_name())
            elif k == "ampersand" or ch == "&":
                self.confirm_kill_tab()
            elif k == "T":
                self.open_prompt("rename_pane", "set pane title")
            elif k == "t":
                self.toggle_clock(self.layout.get("active"))
            elif k == "R":
                self.send_cmd("set_autoresume")
            elif k == "colon" or ch == ":":
                self.open_prompt("command", "")
            elif k == "n":
                self.send_cmd("next_window")
            elif k == "p":
                self.send_cmd("prev_window")
            elif k == "l":
                self.send_cmd("last_window")
            elif k == "w":
                self.request_tree()
            elif k == "period" or ch == ".":
                self.open_prompt("move_window", "move-tab to index")
            elif k.isdigit():
                self.send_cmd("select_window", index=int(k))
            elif k == "d":
                self.exit(message="detached")
            elif k == "left_square_bracket" or ch == "[":
                self.mode = "scroll"
            elif k == "right_square_bracket" or ch == "]":
                self.send_cmd("paste_buffer", index=0)
            elif k == "equals_sign" or ch == "=":
                self.choose_buffer()
            elif k == "enter":
                self.open_menu()
            # 그 외 키는 무시

        # ---- display-panes (prefix q) ----
        def _enter_display(self):
            self.mode = "display"
            self._composite()
            self.set_timer(1.5, self._auto_exit_display)

        def _auto_exit_display(self):
            if self.mode == "display":
                self._exit_display()

        def _exit_display(self):
            self.mode = "normal"
            self._composite()

        def _handle_display_key(self, event: events.Key):
            panes = self.layout.get("panes", [])
            k = event.key
            if k.isdigit() and int(k) < len(panes):
                self.send_cmd("select_pane_id", id=panes[int(k)]["id"])
            self._exit_display()

        # ---- ESC(명령) 모드 ----
        def _exit_esc(self):
            self.mode = "normal"
            self.status.cmd_mode = False
            if self.tabbar.bar_focus:
                self.tabbar.bar_focus = False
                self.tabbar.refresh()
            self.status.refresh()

        def _handle_esc_mode(self, event: events.Key):
            """ESC 명령 모드: 방향키=패널 이동, 위로 더 가면 상단 탭바 포커스.
            탭바 포커스에서는 ←→ 탭 선택, Enter 전환, +/x 추가/삭제, ↓/Esc 복귀."""
            k = event.key
            ch = event.character
            tb = self.tabbar
            if tb.bar_focus:
                tabs = tb.tabs
                idxs = [t["index"] for t in tabs]
                if k == "shift+left" and tb.sel in idxs:   # 선택 탭 왼쪽으로 이동
                    pos = idxs.index(tb.sel)
                    if pos > 0:
                        self.send_cmd("move_tab", index=pos, to=pos - 1)
                        tb.sel = pos - 1
                        tb.refresh()
                elif k == "shift+right" and tb.sel in idxs:  # 오른쪽으로 이동
                    pos = idxs.index(tb.sel)
                    if pos < len(idxs) - 1:
                        self.send_cmd("move_tab", index=pos, to=pos + 1)
                        tb.sel = pos + 1
                        tb.refresh()
                elif k in ("left", "up") and idxs:
                    cur = idxs.index(tb.sel) if tb.sel in idxs else 0
                    tb.sel = idxs[(cur - 1) % len(idxs)]
                    tb.refresh()
                elif k in ("right",) and idxs:
                    cur = idxs.index(tb.sel) if tb.sel in idxs else 0
                    tb.sel = idxs[(cur + 1) % len(idxs)]
                    tb.refresh()
                elif k == "enter":
                    self.send_cmd("select_window", index=tb.sel)
                    tb.bar_focus = False
                    tb.refresh()
                elif ch in ("+", "a"):
                    self.send_cmd("new_window")
                elif ch in ("x", "d") or k == "delete":
                    self.confirm_kill_tab()
                elif k in ("down", "escape"):
                    tb.bar_focus = False
                    tb.refresh()
                    if k == "escape":
                        self._exit_esc()
                return
            if k == "up" and self._tabbar_visible() and not self._pane_above():
                tb.sel = self._active_tab_index()   # 탭바로 포커스 진입
                tb.bar_focus = True
                tb.refresh()
            elif k in ("left", "right", "up", "down"):
                self.send_cmd("select_pane", dir=k)  # 모드 유지(연속 이동)
            elif ch == ":" or k == "colon":
                self._exit_esc()
                self.open_prompt("command", "")
            else:
                # escape/enter/i/그 외 → 명령 모드 종료(셸 입력 복귀)
                self._exit_esc()

        def _handle_scroll_key(self, event: events.Key):
            aid = self.layout.get("active")
            k = _normalize_key(event.key)  # IME 무관 (j/k/g/G/n/N/q 등)
            ch = event.character
            half = max(1, self.layout.get("rows", 24) // 2)
            vi = self.mode_keys == "vi"
            emacs = self.mode_keys == "emacs"
            if k == "up" or (vi and k == "k") or (emacs and k == "ctrl+p"):
                self.send_scroll(aid, delta=1)
            elif k == "down" or (vi and k == "j") or (emacs and k == "ctrl+n"):
                self.send_scroll(aid, delta=-1)
            elif k == "pageup" or (vi and k == "ctrl+u") or (emacs and k == "alt+v"):
                self.send_scroll(aid, delta=half)
            elif k == "pagedown" or (vi and k == "ctrl+d") or (emacs and k == "ctrl+v"):
                self.send_scroll(aid, delta=-half)
            elif k == "g":
                self.send_scroll(aid, top=True)
            elif k in ("G", "end"):
                self.send_scroll(aid, bottom=True)
            elif ch == "/" or k == "slash":
                self.open_prompt("search", "search ↑ (이전 방향)")
                return  # 프롬프트 모드로 전환
            elif k == "n":
                self.send_cmd("search", direction="up")
            elif k == "N":
                self.send_cmd("search", direction="down")
            elif k in ("q", "escape", "enter"):
                self.send_scroll(aid, bottom=True)
                self.mode = "normal"

    return PytmuxApp(sock_path)


def run_client(sock_path: str, session: str | None = None):
    config = load_config()
    app = build_client_app(sock_path, config, session)
    app.run()
