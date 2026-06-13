"""claude-prompt-history 팝업 — 프롬프트 히스토리 리스트(점프·행수 설정).

render_line 기반 단일 위젯(ncd/p4 플러그인과 같은 패턴) — ↑↓ 한 칸 이동은 바뀐 두 줄만
refresh 해 ssh 에서도 빠르다. 시간순(오래된→최근)으로 싣고 커서는 최신에 둔다(↑=이전).
Enter 로 선택 프롬프트가 입력된 위치로 점프(서버 ph_scroll_to)하고 닫는다. +/− 로 미리보기
패널 행수를 1~3 으로 바꾼다(서버 영속). Esc 로 닫는다.

클라가 팝업을 열 때만 import(textual 의존)."""
from __future__ import annotations

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget
from textual.screen import ModalScreen

from pytmuxlib import i18n

_BG = Style(color="#d6d6d6", bgcolor="#1c2230")
_SEL = Style(color="#000000", bgcolor="#00aaaa", bold=True)
_SEL_BLUR = Style(color="#ffffff", bgcolor="#2a6a6a")
_NUM = Style(color="#8a93a6", bgcolor="#1c2230")
_DIM = Style(color="#8a93a6", bgcolor="#1c2230")
_MARK = Style(color="#ffd866", bgcolor="#1c2230")    # ⏎ 멀티라인 표시


class _HistView(Widget):
    """프롬프트 히스토리를 한 줄 단위로 그리는 뷰. 행 = `{n}. {첫 줄}` (+⏎ 멀티라인)."""
    can_focus = True

    def __init__(self, hist, empty_msg=""):
        super().__init__(id="phview")
        self._hist = list(hist)
        self._empty = empty_msg
        self._sel = max(0, len(self._hist) - 1)        # 최신 선택
        self._top = 0

    def on_mount(self):
        self._clamp_view()
        self.focus()

    def on_resize(self, event):
        self._clamp_view()
        self.refresh()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if not self._hist:
            if y == 0 and self._empty:
                return Strip([Segment("  " + self._empty, _DIM)]
                            ).adjust_cell_length(width, _BG)
            return Strip.blank(width, _BG)
        i = self._top + y
        if not (0 <= i < len(self._hist)):
            return Strip.blank(width, _BG)
        prompt = self._hist[i]
        plines = prompt.splitlines() or [""]
        first = plines[0]
        multi = len(plines) > 1
        num = f"{i + 1:>3}. "
        if i == self._sel:
            style = _SEL if self.has_focus else _SEL_BLUR
            mark = i18n.t("ph.multiline_mark") if multi else ""
            return Strip([Segment("  " + num + first + mark, style)]
                        ).adjust_cell_length(width, style)
        segs = [Segment("  " + num, _NUM), Segment(first, _BG)]
        if multi:
            segs.append(Segment(i18n.t("ph.multiline_mark"), _MARK))
        return Strip(segs).adjust_cell_length(width, _BG)

    def _cur(self):
        return self._sel if 0 <= self._sel < len(self._hist) else None

    def _clamp_view(self):
        n = len(self._hist)
        if n == 0:
            self._sel = self._top = 0
            return
        self._sel = max(0, min(n - 1, self._sel))
        h = self.size.height
        if h <= 0:
            return
        if self._sel < self._top:
            self._top = self._sel
        elif self._sel >= self._top + h:
            self._top = self._sel - h + 1
        self._top = max(0, min(self._top, max(0, n - h)))

    def _move(self, new: int):
        n = len(self._hist)
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
            self.refresh()
        else:
            w = self.size.width
            self.refresh(Region(0, old - self._top, w, 1))
            self.refresh(Region(0, new - self._top, w, 1))

    def on_key(self, event: events.Key):
        k = event.key
        if k == "escape":
            event.stop()
            self.screen.dismiss(None)
            return
        if k == "enter":
            event.stop()
            cur = self._cur()
            if cur is not None:
                self.screen.jump_to(cur)
            return
        ch = event.character
        if ch in ("+", "=", "-") or k in ("plus", "equals_sign", "minus"):
            event.stop()                        # 미리보기 행수 ±(=/+ 같은 키)
            down = ch == "-" or k == "minus"
            self.screen.bump_lines(-1 if down else 1)
            return
        if k == "up":
            event.stop(); self._move(self._sel - 1)
        elif k == "down":
            event.stop(); self._move(self._sel + 1)
        elif k in ("home", "end", "pageup", "pagedown"):
            event.stop()
            page = max(1, (self.size.height or 10) - 1)
            if k == "home":
                self._move(0)
            elif k == "end":
                self._move(len(self._hist) - 1)
            elif k == "pageup":
                self._move(self._sel - page)
            else:
                self._move(self._sel + page)

    def on_click(self, event: events.Click):
        i = self._top + event.y
        if 0 <= i < len(self._hist):
            event.stop()
            self._move(i)


class PromptHistoryScreen(ModalScreen):
    """프롬프트 히스토리 팝업. Enter=그 프롬프트 위치로 점프 후 닫기, +/−=미리보기 행수,
    Esc=닫기."""
    CSS = """
    PromptHistoryScreen { align: center middle; }
    #phbox { width: 88%; height: 80%; padding: 0 1;
             background: #1c2230; color: #d6d6d6;
             border: round #00aaaa;
             border-title-color: #ffffff; border-title-background: #1c2230;
             border-subtitle-color: #6fdcdc; border-subtitle-background: #1c2230; }
    #phview { height: 1fr; width: 1fr; }
    """

    def __init__(self, pane_id, hist, max_lines):
        super().__init__()
        self._pane_id = pane_id
        self._hist = list(hist)
        self._max_lines = int(max_lines)
        self._view = _HistView(self._hist, empty_msg=i18n.t("ph.empty"))

    def compose(self) -> ComposeResult:
        with Vertical(id="phbox"):
            yield self._view

    def on_mount(self):
        box = self.query_one("#phbox", Vertical)
        box.border_title = i18n.t("ph.popup_title")
        self._set_sub()

    def _set_sub(self):
        box = self.query_one("#phbox", Vertical)
        box.border_subtitle = i18n.t("ph.popup_sub", n=self._max_lines)

    def jump_to(self, index: int):
        """선택 프롬프트가 입력된 위치로 점프(서버)하고 팝업을 닫는다."""
        self.app.send_cmd("ph_scroll_to", index=index)
        self.dismiss(None)

    def bump_lines(self, delta: int):
        """미리보기 행수 ±(1~3). 낙관적 즉시 반영 + 서버 영속."""
        new = max(1, min(3, self._max_lines + delta))
        if new != self._max_lines:
            self._max_lines = new
            self.app.ph_max_lines = new           # 낙관적(다음 status 가 권위 확인)
            self.app.send_cmd("set_ph_max_lines", n=new)
            self._set_sub()
