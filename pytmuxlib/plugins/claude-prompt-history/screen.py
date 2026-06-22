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
    """프롬프트 히스토리 뷰. 멀티라인 프롬프트는 **여러 표시 행**으로 펼쳐 보인다
    (항목3 2026-06-22) — 첫 줄 `{n}. {줄}`, 이어지는 줄은 들여쓰기. 프롬프트당 최대
    max_lines 줄까지 펼치고 넘으면 마지막 표시 줄에 `…`. 선택 단위는 **프롬프트**라
    한 프롬프트의 모든 표시 행이 함께 하이라이트된다. 표시 행↔(프롬프트 idx, 줄 idx)
    매핑(_rows)으로 스크롤·클릭을 환산한다."""
    can_focus = True

    _INDENT = "      "        # 이어지는 줄 들여쓰기(= "  " + "{n:>3}. " 폭 정렬)

    def __init__(self, hist, empty_msg="", max_lines=1):
        super().__init__(id="phview")
        self._hist = list(hist)
        self._empty = empty_msg
        self._max_lines = max(1, int(max_lines))
        self._sel = max(0, len(self._hist) - 1)        # 최신 선택(프롬프트 idx)
        self._top = 0                                  # 표시 행 오프셋
        self._rebuild_rows()

    def _rebuild_rows(self):
        """표시 행 테이블 재구성: 각 프롬프트를 최대 _max_lines 줄로 펼친다. 행 =
        (프롬프트 idx, 줄 idx, ellipsis?) — ellipsis 는 상한 초과로 잘렸다는 표식."""
        rows = []
        prow = {}                                      # 프롬프트 idx → 첫 표시 행
        for pi, prompt in enumerate(self._hist):
            plines = prompt.splitlines() or [""]
            prow[pi] = len(rows)
            show = plines[:self._max_lines]
            trunc = len(plines) > self._max_lines
            for li, _ln in enumerate(show):
                ell = trunc and li == len(show) - 1     # 마지막 표시 줄에 … 표식
                rows.append((pi, li, ell))
        self._rows = rows
        self._prow = prow

    def set_max_lines(self, n):
        self._max_lines = max(1, int(n))
        self._rebuild_rows()
        self._clamp_view()
        self.refresh()

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
        r = self._top + y
        if not (0 <= r < len(self._rows)):
            return Strip.blank(width, _BG)
        pi, li, ell = self._rows[r]
        line = (self._hist[pi].splitlines() or [""])[li]
        mark = i18n.t("ph.truncated_mark") if ell else ""
        if pi == self._sel:                            # 선택 = 프롬프트 블록 전체 강조
            style = _SEL if self.has_focus else _SEL_BLUR
            if li == 0:
                body = "  " + f"{pi + 1:>3}. " + line + mark
            else:
                body = "  " + self._INDENT + line + mark
            return Strip([Segment(body, style)]).adjust_cell_length(width, style)
        if li == 0:
            segs = [Segment("  " + f"{pi + 1:>3}. ", _NUM), Segment(line, _BG)]
        else:
            segs = [Segment("  " + self._INDENT, _BG), Segment(line, _BG)]
        if mark:
            segs.append(Segment(mark, _MARK))
        return Strip(segs).adjust_cell_length(width, _BG)

    def _cur(self):
        return self._sel if 0 <= self._sel < len(self._hist) else None

    def _sel_rows(self):
        """선택 프롬프트의 표시 행 범위 [first, last] (없으면 (0,0))."""
        if self._sel not in self._prow:
            return 0, 0
        first = self._prow[self._sel]
        last = first
        while last + 1 < len(self._rows) and self._rows[last + 1][0] == self._sel:
            last += 1
        return first, last

    def _clamp_view(self):
        n = len(self._hist)
        if n == 0:
            self._sel = self._top = 0
            return
        self._sel = max(0, min(n - 1, self._sel))
        h = self.size.height
        if h <= 0:
            return
        first, last = self._sel_rows()
        # 선택 블록이 보이도록 스크롤 — 블록 끝이 아래로 넘치면 끝을 맞추되, 블록이
        # 뷰보다 크면 첫 줄을 맞춘다(첫 줄 = 번호 줄이 항상 보이게).
        if first < self._top:
            self._top = first
        elif last >= self._top + h:
            self._top = min(first, last - h + 1)
        nrows = len(self._rows)
        self._top = max(0, min(self._top, max(0, nrows - h)))

    def _move(self, new: int):
        n = len(self._hist)
        if not n:
            return
        new = max(0, min(n - 1, new))
        if new == self._sel:
            return
        self._sel = new
        self._clamp_view()
        self.refresh()      # 멀티행 블록 강조 이동은 전체 갱신(부분 갱신 행 계산 복잡)

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
        r = self._top + event.y                        # 표시 행 → 그 행의 프롬프트
        if 0 <= r < len(self._rows):
            event.stop()
            self._move(self._rows[r][0])


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
        self._view = _HistView(self._hist, empty_msg=i18n.t("ph.empty"),
                               max_lines=self._max_lines)

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
            self._view.set_max_lines(new)         # 프롬프트당 펼침 줄 수도 함께 조정
            self._set_sub()
