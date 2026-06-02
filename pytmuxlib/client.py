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


def build_client_app(sock_path: str, config: dict | None = None,
                     session_name: str | None = None):
    config = config or {}
    from rich.segment import Segment
    from rich.style import Style
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.screen import ModalScreen
    from textual.strip import Strip
    from textual.suggester import SuggestFromList
    from textual.widget import Widget
    from textual.widgets import Input, Label, ListItem, ListView
    from datetime import datetime

    DEFAULT_STYLE = Style()
    _style_cache: dict = {}

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
        ("new_window", "새 윈도우"),
        ("rename_window", "윈도우 이름 변경"),
        ("kill_window", "윈도우 삭제"),
        ("choose_tree", "윈도우 선택기(트리)"),
        ("next_window", "다음 윈도우"),
        ("prev_window", "이전 윈도우"),
        ("new_session", "새 세션"),
        ("kill_session", "세션 삭제"),
        ("command", "명령 입력"),
        ("detach", "detach (앱 종료, 세션 유지)"),
        ("kill_server", "서버 종료 (모든 세션 종료)"),
    ]

    # 명령 프롬프트(:)에서 쓸 수 있는 명령 목록 (이름, 설명) — ? 목록·자동완성용
    COMMANDS = [
        ("split-window", "패널 분할 (-h 좌우 / -v 상하)"),
        ("kill-pane", "현재 패널 삭제"),
        ("resize-pane", "패널 크기 (-Z 줌 토글)"),
        ("select-pane", "패널 이동 (-L/-R/-U/-D) 또는 제목 (-T)"),
        ("rename-pane", "패널 제목 변경"),
        ("swap-pane", "패널 위치 교환 (-U/-D)"),
        ("rotate-window", "윈도우 내 패널 회전"),
        ("break-pane", "패널을 새 윈도우로 분리"),
        ("join-pane", "다른 윈도우 패널을 현재로 합치기 (-h)"),
        ("respawn-pane", "패널 셸 재시작"),
        ("select-layout", "레이아웃 프리셋 (even-h/v, main-h/v, tiled)"),
        ("next-layout", "다음 레이아웃 프리셋"),
        ("synchronize-panes", "입력 동기화 토글 [on|off]"),
        ("capture-pane", "패널 내용을 버퍼로 캡처 (-S 전체)"),
        ("pipe-pane", "패널 출력을 외부 명령으로 파이프"),
        ("clear-history", "스크롤백 비우기"),
        ("new-window", "새 윈도우"),
        ("kill-window", "윈도우 삭제"),
        ("next-window", "다음 윈도우"),
        ("previous-window", "이전 윈도우"),
        ("last-window", "직전 윈도우"),
        ("select-window", "윈도우 선택 (-t N)"),
        ("move-window", "윈도우 이동 (-t N)"),
        ("swap-window", "윈도우 교환 (-t N)"),
        ("rename-window", "윈도우 이름 변경"),
        ("automatic-rename", "윈도우 자동 이름 [on|off]"),
        ("monitor-activity", "활동 모니터링 [on|off]"),
        ("monitor-bell", "벨 모니터링 [on|off]"),
        ("choose-tree", "세션/윈도우 선택기"),
        ("new-session", "새 세션 (-s 이름)"),
        ("kill-session", "세션 삭제 (-t 이름)"),
        ("rename-session", "세션 이름 변경"),
        ("switch-client", "세션 전환 (-t 이름)"),
        ("detach-client", "detach (-a 다른 클라이언트)"),
        ("send-keys", "패널에 키 주입 (예: Enter, C-c)"),
        ("paste-buffer", "페이스트 버퍼 붙여넣기 (N)"),
        ("choose-buffer", "페이스트 버퍼 선택기"),
        ("paste-clipboard", "OS 클립보드 붙여넣기"),
        ("auto-resume", "토큰 리밋 자동 재개 [on|off]"),
        ("auto-resume-message", "자동 재개 메시지 설정"),
        ("set", "옵션 설정 (prefix/mouse/status-*/mode-keys 등)"),
        ("show-options", "현재 옵션 보기"),
        ("set-hook", "이벤트 훅 설정 (<event> <cmd>)"),
        ("show-hooks", "훅 목록 보기"),
        ("source-file", "설정 파일 다시 불러오기"),
        ("display-message", "상태줄에 메시지 표시"),
        ("display-popup", "명령 실행 결과를 팝업으로"),
        ("clock-mode", "큰 시계 표시"),
        ("run-shell", "셸 명령 실행"),
        ("if-shell", "조건부 셸 실행"),
        ("save-layout", "레이아웃 저장"),
        ("restore-layout", "레이아웃 복원"),
        ("kill-server", "서버와 모든 세션 종료"),
    ]

    class CommandListScreen(ModalScreen):
        """명령 목록 선택기(? 입력 시). 방향키로 이동, Enter 선택, Esc 취소."""
        CSS = """
        CommandListScreen { align: center middle; }
        #cmds { width: 72; height: auto; max-height: 80%;
                border: round $accent; background: $panel; }
        """

        def __init__(self, items, query=""):
            super().__init__()
            q = query.lower()
            self._items = [it for it in items if it[0].startswith(q)] or items

        def compose(self) -> ComposeResult:
            yield ListView(*[ListItem(Label(f"{n:<20} {d}"), id=f"c{i}")
                             for i, (n, d) in enumerate(self._items)], id="cmds")

        def on_mount(self):
            self.query_one(ListView).focus()

        def on_list_view_selected(self, event):
            self.dismiss(self._items[int(event.item.id[1:])][0])

        def on_key(self, event: events.Key):
            if event.key == "escape":
                event.stop()
                self.dismiss(None)

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
                    label = (f"{s['name']}: {w['index']}:{w['name']} "
                             f"({w['panes']} panes)")
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

    class ClockScreen(ModalScreen):
        """clock-mode(prefix t): 큰 시계 오버레이. 아무 키나 누르면 닫힘."""
        CSS = """
        ClockScreen { align: center middle; }
        #clock { width: auto; height: auto; padding: 2 4;
                 border: round $accent; background: $panel;
                 color: $success; text-style: bold; }
        """

        def compose(self) -> ComposeResult:
            yield Label(datetime.now().strftime("%H:%M:%S"), id="clock")

        def on_mount(self):
            self.set_interval(1.0, self._tick)

        def _tick(self):
            self.query_one("#clock", Label).update(
                datetime.now().strftime("%H:%M:%S"))

        def on_key(self, event: events.Key):
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
        #pinput { dock: bottom; width: 100%; border: none; height: 1;
                  padding: 0 1; background: $surface; color: $text; }
        """

        def __init__(self, purpose, label, initial, suggester):
            super().__init__()
            self._purpose = purpose
            self._label = label
            self._initial = initial
            self._suggester = suggester

        def compose(self) -> ComposeResult:
            yield Input(value=self._initial, placeholder=self._label,
                        suggester=self._suggester, id="pinput")

        def on_mount(self):
            inp = self.query_one(Input)
            inp.focus()
            inp.cursor_position = len(inp.value)

        def on_input_submitted(self, event):
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

        def on_key(self, event: events.Key):
            if event.key == "escape":
                event.stop()
                self.dismiss(None)

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
                if p["x"] <= x < p["x"] + p["w"] and p["y"] <= y < p["y"] + p["h"]:
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

    class StatusBar(Widget):
        def __init__(self, bg="green", fg="black",
                     left=" [#S] ", right=" #{pane_title}#h %H:%M %d-%b-%y "):
            super().__init__(id="status")
            self.session = ""
            self.windows = []
            self.zoomed = False
            self.sync = False
            self.pane_title = ""
            self.autoresume = False
            self.prefix_off = False  # 중첩: outer prefix 해제 표시
            self.cmd_mode = False  # ESC 명령 모드 표시
            self.message = None    # display-message 임시 메시지
            self.bg = bg
            self.fg = fg
            self.left_fmt = left
            self.right_fmt = right

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
            self.refresh()

        def render_line(self, y: int) -> Strip:
            w = self.size.width
            base = Style(color=self.fg, bgcolor=self.bg)
            if self.message is not None:
                ms = Style(color="black", bgcolor="yellow", bold=True)
                return Strip([Segment(f" {self.message} ", ms)]).adjust_cell_length(
                    w, ms)
            # 활성 윈도우: 녹색 바 위에서 잘 보이도록 검정 배경 + 흰 글씨(굵게)
            active = Style(color="white", bgcolor="black", bold=True)
            segs = [Segment(self._expand(self.left_fmt), base)]
            if self.cmd_mode:
                segs.append(Segment("CMD(←↑↓→ 이동, : 명령) ",
                                    Style(color="black", bgcolor="cyan", bold=True)))
            if self.zoomed:
                segs.append(Segment("Z ", Style(color="black", bgcolor="yellow",
                                                 bold=True)))
            if self.sync:
                segs.append(Segment("SYNC ", Style(color="white", bgcolor="red",
                                                    bold=True)))
            if self.autoresume:
                segs.append(Segment("AR ", Style(color="black", bgcolor="cyan",
                                                  bold=True)))
            if self.prefix_off:
                segs.append(Segment("NEST ", Style(color="white", bgcolor="magenta",
                                                   bold=True)))
            for win in self.windows:
                flag = "!" if win.get("bell") else ("#" if win.get("activity") else "")
                label = f"{win['index']}:{win['name']}{flag} "
                if win["active"]:
                    st = active
                elif win.get("bell"):
                    st = Style(color="white", bgcolor="red", bold=True)
                elif win.get("activity"):
                    st = Style(color="black", bgcolor="yellow")
                else:
                    st = base
                segs.append(Segment(label, st))
            right = self._expand(self.right_fmt)
            used = sum(len(s.text) for s in segs)
            pad = w - used - len(right)
            if pad > 0:
                segs.append(Segment(" " * pad, base))
            segs.append(Segment(right, base))
            # 폭 맞추기(자르기)
            return Strip(segs).adjust_cell_length(w, base)

    class PytmuxApp(App):
        ENABLE_COMMAND_PALETTE = False
        BINDINGS = []
        CSS = """
        Screen { layout: vertical; }
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
            self.view = MultiplexerView()
            self.status = StatusBar(
                bg=config.get("status_bg", "green"),
                fg=config.get("status_fg", "black"),
                left=config.get("status_left", " [#S] "),
                right=config.get("status_right",
                                 " #{pane_title}#h %H:%M %d-%b-%y "))

        def compose(self) -> ComposeResult:
            yield self.view
            yield self.status

        async def on_mount(self):
            self.view.focus()
            if self.status_position == "top":
                self.status.styles.dock = "top"
            self._restart_status_timer()
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

        def _content_size(self):
            size = self.size
            return max(MIN_W, size.width), max(MIN_H, size.height - 1)

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
            elif t == "buffers":
                if self._want_buffers:
                    self._want_buffers = False
                    self._open_choose_buffer(msg.get("items", []))
            elif t == "captured":
                self.display_message(f"{msg.get('chars', 0)} chars 버퍼에 캡처됨")
            elif t == "bye":
                self.exit(message="pytmux: 서버가 종료되었습니다")

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
            # 분할선 (활성 패널에 접한 분할선은 강조색으로 표시)
            div_style = Style(color="grey50")
            active_div = Style(color="green", bold=True)
            arect = None
            for p in self.layout.get("panes", []):
                if p["id"] == active:
                    arect = (p["x"], p["y"], p["w"], p["h"])
                    break

            def _adjacent(d):
                if arect is None:
                    return False
                ax, ay, aw, ah = arect
                if d["orient"] == "lr":  # 세로 분할선(열 d["x"])
                    touch = d["x"] == ax - 1 or d["x"] == ax + aw
                    overlap = not (d["y"] + d["h"] <= ay or d["y"] >= ay + ah)
                else:                    # 가로 분할선(행 d["y"])
                    touch = d["y"] == ay - 1 or d["y"] == ay + ah
                    overlap = not (d["x"] + d["w"] <= ax or d["x"] >= ax + aw)
                return touch and overlap

            for d in self.layout.get("dividers", []):
                ch = "│" if d["orient"] == "lr" else "─"
                stl = active_div if _adjacent(d) else div_style
                for i in range(d["h"] if d["orient"] == "lr" else d["w"]):
                    if d["orient"] == "lr":
                        gx, gy = d["x"], d["y"] + i
                    else:
                        gx, gy = d["x"] + i, d["y"]
                    if 0 <= gx < W and 0 <= gy < H:
                        cells[gy][gx] = (ch, stl)
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
                        gx = cx0 + j
                        if 0 <= gx < W and 0 <= cy0 < H:
                            cells[cy0][gx] = (chh, st)
            self.view.set_frame(cells)

        # ---- 송신 헬퍼 ----
        def send_cmd(self, action, **kw):
            if self.writer:
                kw["t"] = "cmd"
                kw["action"] = action
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
                    name, idx = res
                    self.send_cmd("switch_session", name=name)
                    self.send_cmd("select_window", index=idx)
            self.push_screen(ChooseTreeScreen(tree), handle)

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
                self.open_prompt("rename_window", "rename-window",
                                 self._active_window_name())
            elif key == "kill_window":
                self.open_prompt("confirm", "kill-window? (y/N)",
                                 action=lambda: self.send_cmd("kill_window"))
            elif key == "next_window":
                self.send_cmd("next_window")
            elif key == "prev_window":
                self.send_cmd("prev_window")
            elif key == "new_session":
                self.open_prompt("new_session", "new-session (이름, 빈칸=자동)")
            elif key == "kill_session":
                self.open_prompt("confirm", "kill-session? (y/N)",
                                 action=lambda: self.send_cmd("kill_session"))
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
                suggester = SuggestFromList([n for n, _ in COMMANDS],
                                            case_sensitive=False)
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
            elif purpose == "rename_session":
                if val:
                    self.send_cmd("rename_session", name=val)
            elif purpose == "rename_pane":
                self.send_cmd("set_pane_title", title=val)
            elif purpose == "move_window":
                if val.lstrip("-").isdigit():
                    self.send_cmd("move_window", index=int(val))
            elif purpose == "search":
                if val:
                    self.send_cmd("search", query=val, direction="up")
            elif purpose == "new_session":
                self.send_cmd("new_session", name=val)
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
                # 사용 가능한 명령 목록을 읽기전용으로 표시(아무 키나 닫힘)
                self.push_screen(InfoScreen(
                    [f"{n:<22}{d}" for n, d in COMMANDS], title="commands (help)"))
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
                orient = "lr" if "-h" in args else "tb"
                self.send_cmd("split", orient=orient)
            elif c in ("kill-pane", "killp"):
                self.send_cmd("kill_pane")
            elif c in ("new-window", "neww"):
                self.send_cmd("new_window")
            elif c in ("kill-window", "killw"):
                self.send_cmd("kill_window")
            elif c in ("next-window", "next"):
                self.send_cmd("next_window")
            elif c in ("previous-window", "prev"):
                self.send_cmd("prev_window")
            elif c in ("last-window", "last"):
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
            elif c in ("move-window", "movew"):
                idx = self._opt_value(args, "-t")
                idx = int(idx) if idx and idx.isdigit() else self._first_int(args)
                if idx is not None:
                    self.send_cmd("move_window", index=idx)
            elif c in ("swap-window", "swapw"):
                idx = self._opt_value(args, "-t")
                idx = int(idx) if idx and idx.isdigit() else self._first_int(args)
                if idx is not None:
                    self.send_cmd("swap_window", index=idx)
            elif c in ("choose-tree", "choose-window", "choose-session"):
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
            elif c in ("select-window", "selectw"):
                idx = self._opt_value(args, "-t")
                idx = int(idx) if idx and idx.isdigit() else self._first_int(args)
                if idx is not None:
                    self.send_cmd("select_window", index=idx)
            elif c in ("rename-window", "renamew"):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("rename_window", name=name)
                else:
                    self.open_prompt("rename_window", "rename-window",
                                     self._active_window_name())
            elif c in ("rename-session", "rename"):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("rename_session", name=name)
                else:
                    self.open_prompt("rename_session", "rename-session")
            elif c in ("new-session", "new"):
                name = self._opt_value(args, "-s") or " ".join(
                    a for a in args if not a.startswith("-"))
                self.send_cmd("new_session", name=name or "")
            elif c in ("switch-client", "switchc", "attach-session", "attach"):
                name = self._opt_value(args, "-t")
                if name:
                    self.send_cmd("switch_session", name=name)
            elif c in ("kill-session", "kills"):
                name = self._opt_value(args, "-t")
                self.send_cmd("kill_session", name=name or "")
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
            elif c in ("choose-buffer", "list-buffers", "lsb"):
                self.choose_buffer()
            elif c in ("clear-history", "clearhist"):
                self.send_cmd("clear_history")
            elif c in ("clock-mode", "clock"):
                self.push_screen(ClockScreen())
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
            # F12: outer prefix 가로채기 토글(중첩 tmux/pytmux 용)
            if event.key == "f12":
                self.prefix_enabled = not self.prefix_enabled
                self.status.prefix_off = not self.prefix_enabled
                self.status.refresh()
                self.display_message("outer prefix " +
                                     ("ON" if self.prefix_enabled else "OFF (중첩)"))
                event.prevent_default()
                event.stop()
                return
            if self.prefix_enabled and event.key == self.prefix_key:
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
            k = event.key
            ch = event.character
            # prefix 를 한 번 더 누르면 prefix 키 자체를 셸로 전송
            if k == self.prefix_key:
                self.send_input(self.prefix_bytes)
                return
            # 사용자 정의 바인딩 우선 (config 의 bind)
            token = ch if (ch and ch.isprintable() and not k.startswith("ctrl+")) else k
            if token in self.bindings:
                self._run_command(self.bindings[token])
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
                self.open_prompt("rename_window", "rename-window",
                                 self._active_window_name())
            elif k == "ampersand" or ch == "&":
                self.open_prompt("confirm", "kill-window? (y/N)",
                                 action=lambda: self.send_cmd("kill_window"))
            elif k == "dollar_sign" or ch == "$":
                self.open_prompt("rename_session", "rename-session")
            elif k == "T":
                self.open_prompt("rename_pane", "set pane title")
            elif k == "t":
                self.push_screen(ClockScreen())
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
                self.open_prompt("move_window", "move-window to index")
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
            self.status.refresh()

        def _handle_esc_mode(self, event: events.Key):
            """ESC 로 들어온 명령 모드: 방향키=패널 이동(유지), :=명령 프롬프트,
            그 외 키는 모드 종료."""
            k = event.key
            ch = event.character
            if k in ("left", "right", "up", "down"):
                self.send_cmd("select_pane", dir=k)  # 모드 유지(연속 이동)
            elif ch == ":" or k == "colon":
                self._exit_esc()
                self.open_prompt("command", "")
            else:
                # escape/enter/i/그 외 → 명령 모드 종료(셸 입력 복귀)
                self._exit_esc()

        def _handle_scroll_key(self, event: events.Key):
            aid = self.layout.get("active")
            k = event.key
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
