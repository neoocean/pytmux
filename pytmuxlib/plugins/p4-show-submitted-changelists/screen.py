"""p4-show-submitted-changelists 화면 — submitted 목록(풀스크린) + 상세 팝업.

ncd 의 `render_line` 기반 단일 위젯 패턴을 따른다(ListView 대신) — ↑↓ 한 칸 이동은 바뀐
두 줄만 refresh 해 ssh 원격에서도 빠르고 위젯 레이아웃 비용이 없다. 와이드 문자(한글
설명)도 `adjust_cell_length` 로 셀 기준 정확히 패딩한다.

화면 2층(코어 screen_stack):
  * ChangesScreen — submitted CL 목록 풀스크린(이게 사용자가 보는 '탭'). Esc=닫기(종료).
  * DescribeScreen — Enter 시 그 위에 올라오는 상세 팝업. Esc=팝업만 닫고 목록으로.
Esc 는 늘 스택 최상단을 닫으므로, 팝업이 떠 있으면 팝업을, 없으면 목록을 닫는다 —
사용자 시나리오(팝업 Esc 닫기 / 탭 닫아 종료)와 정확히 일치한다.

이 모듈은 클라가 화면을 실제로 열 때만 import 된다(textual 의존)."""
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

# 퍼포스 풍 팔레트: 진한 슬레이트 패널·시안 선택 막대(NCD 와 같은 결, 색만 다르게).
_BG = Style(color="#d6d6d6", bgcolor="#1c2230")
_SEL = Style(color="#000000", bgcolor="#00aaaa", bold=True)
_SEL_BLUR = Style(color="#ffffff", bgcolor="#2a6a6a")
_DIM = Style(color="#8a93a6", bgcolor="#1c2230")
_CHANGE = Style(color="#ffd866", bgcolor="#1c2230", bold=True)         # CL 번호 강조(비선택)


class _ChangesView(Widget):
    """submitted CL 목록을 한 줄 단위로 그리는 뷰(스크롤·커서 자체 관리). 행 형식:
    `@<CL>  <날짜시간>  <user>  <설명 첫 줄>`. ↑↓ 이동·Enter 상세·Esc 닫기."""
    can_focus = True

    def __init__(self, rows, empty_msg: str = ""):
        super().__init__(id="p4clview")
        self._rows = list(rows or [])
        self._empty = empty_msg
        self._sel = 0
        self._top = 0

    def on_mount(self):
        self.focus()

    def on_resize(self, event):
        self._clamp_view()
        self.refresh()

    # ---- 렌더 ----
    def _row_text(self, row: dict) -> str:
        cl = row.get("change", "")
        when = row.get("when", "")
        user = row.get("user", "")
        desc = (row.get("desc", "") or "").splitlines()
        desc = desc[0] if desc else ""
        return f"  @{cl:<7} {when:<16}  {user:<14} {desc}"

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if not self._rows:
            # 빈 상태(또는 오류) 안내를 첫 줄에 표시.
            if y == 0 and self._empty:
                return Strip([Segment("  " + self._empty, _DIM)]
                            ).adjust_cell_length(width, _BG)
            return Strip.blank(width, _BG)
        i = self._top + y
        if not (0 <= i < len(self._rows)):
            return Strip.blank(width, _BG)
        row = self._rows[i]
        if i == self._sel:
            style = _SEL if self.has_focus else _SEL_BLUR
            return Strip([Segment(self._row_text(row), style)]
                        ).adjust_cell_length(width, style)
        # 비선택 행: CL 번호만 노랑으로, 나머지는 기본색(두 세그먼트).
        cl = row.get("change", "")
        head = f"  @{cl:<7} "
        rest = self._row_text(row)[len(head):]
        return Strip([Segment(head, _CHANGE), Segment(rest, _BG)]
                    ).adjust_cell_length(width, _BG)

    # ---- 커서·스크롤(빠른 부분 갱신, ncd 와 동일) ----
    def _cur(self) -> str | None:
        if 0 <= self._sel < len(self._rows):
            return self._rows[self._sel].get("change") or None
        return None

    def _clamp_view(self):
        n = len(self._rows)
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
            self.refresh()
        else:                                       # 선택만 이동 → 두 줄만(ssh 최소)
            w = self.size.width
            self.refresh(Region(0, old - self._top, w, 1))
            self.refresh(Region(0, new - self._top, w, 1))

    # ---- 키 ----
    async def on_key(self, event: events.Key):
        k = event.key
        if k in ("escape", "q"):
            event.stop()
            self.screen.dismiss(None)               # 탭 닫기 = 플러그인 종료
            return
        if k == "enter":
            event.stop()
            cur = self._cur()
            if cur is not None:
                self.app.request_p4_describe(cur)   # 상세 팝업(스택 위)
        elif k == "up":
            event.stop(); self._move(self._sel - 1)
        elif k == "down":
            event.stop(); self._move(self._sel + 1)
        elif k in ("home", "end", "pageup", "pagedown"):
            event.stop()
            page = max(1, (self.size.height or 10) - 1)
            if k == "home":
                self._move(0)
            elif k == "end":
                self._move(len(self._rows) - 1)
            elif k == "pageup":
                self._move(self._sel - page)
            else:
                self._move(self._sel + page)

    def on_click(self, event: events.Click):
        i = self._top + event.y
        if 0 <= i < len(self._rows):
            event.stop()
            self._move(i)


class ChangesScreen(ModalScreen):
    """submitted CL 목록 풀스크린. 이게 사용자가 여닫는 '탭'이다(Esc 로 종료)."""
    CSS = """
    ChangesScreen { align: center middle; }
    #p4clbox { width: 96%; height: 92%; padding: 0 1;
               background: #1c2230; color: #d6d6d6;
               border: round #00aaaa;
               border-title-color: #ffffff; border-title-background: #1c2230;
               border-subtitle-color: #6fdcdc; border-subtitle-background: #1c2230; }
    #p4clview { height: 1fr; width: 1fr; }
    """

    def __init__(self, rows, info=None, err=None):
        super().__init__()
        self._rows = list(rows or [])
        self._info = info or {}
        self._err = err
        empty = (i18n.t("p4cl.error", err=err) if err
                 else i18n.t("p4cl.empty"))
        self._view = _ChangesView(self._rows, empty_msg=empty)

    def compose(self) -> ComposeResult:
        with Vertical(id="p4clbox"):
            yield self._view

    def on_mount(self):
        box = self.query_one("#p4clbox", Vertical)
        port = self._info.get("port", "")
        box.border_title = (i18n.t("p4cl.title_port", port=port) if port
                            else i18n.t("p4cl.title"))
        sub = i18n.t("p4cl.nav")
        if self._err and self._rows:
            sub = i18n.t("p4cl.error", err=self._err) + "   " + sub
        box.border_subtitle = sub


class _TextView(Widget):
    """describe 텍스트를 줄 단위로 스크롤하는 뷰(↑↓·PgUp/PgDn·Home/End·Esc)."""
    can_focus = True

    def __init__(self, lines):
        super().__init__(id="p4descview")
        self._lines = list(lines)
        self._top = 0

    def on_mount(self):
        self.focus()

    def set_lines(self, lines):
        self._lines = list(lines) or [i18n.t("p4cl.no_detail")]
        self._top = 0
        self.refresh()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        i = self._top + y
        if not (0 <= i < len(self._lines)):
            return Strip.blank(width, _BG)
        return Strip([Segment(self._lines[i], _BG)]).adjust_cell_length(width, _BG)

    def _scroll(self, delta: int):
        h = self.size.height or 1
        maxtop = max(0, len(self._lines) - h)
        new = max(0, min(maxtop, self._top + delta))
        if new != self._top:
            self._top = new
            self.refresh()

    def on_key(self, event: events.Key):
        k = event.key
        if k == "escape":
            event.stop()
            self.screen.dismiss(None)               # 팝업만 닫고 목록으로
            return
        h = self.size.height or 10
        if k == "up":
            event.stop(); self._scroll(-1)
        elif k == "down":
            event.stop(); self._scroll(1)
        elif k == "pageup":
            event.stop(); self._scroll(-(h - 1))
        elif k == "pagedown":
            event.stop(); self._scroll(h - 1)
        elif k == "home":
            event.stop(); self._top = 0; self.refresh()
        elif k == "end":
            event.stop(); self._scroll(len(self._lines))


class DescribeScreen(ModalScreen):
    """단일 CL 의 `p4 describe` 상세 팝업(목록 위에 중앙 모달). Esc 로 닫는다."""
    CSS = """
    DescribeScreen { align: center middle; }
    #p4descbox { width: 86%; height: 84%; padding: 0 1;
                 background: #1c2230; color: #d6d6d6;
                 border: double #ffd866;
                 border-title-color: #ffd866; border-title-background: #1c2230;
                 border-subtitle-color: #6fdcdc; border-subtitle-background: #1c2230; }
    #p4descview { height: 1fr; width: 1fr; }
    """

    def __init__(self, change: str):
        super().__init__()
        self._change = str(change)
        self._view = _TextView([i18n.t("p4cl.loading")])

    def compose(self) -> ComposeResult:
        with Vertical(id="p4descbox"):
            yield self._view

    def on_mount(self):
        box = self.query_one("#p4descbox", Vertical)
        box.border_title = f"@{self._change}"
        box.border_subtitle = i18n.t("p4cl.detail_nav")

    def fill(self, text, err):
        """서버 응답을 채운다 — 오류면 오류문구, 아니면 줄 단위 텍스트."""
        if err:
            self._view.set_lines([i18n.t("p4cl.error", err=err)])
        else:
            self._view.set_lines((text or "").splitlines()
                                 or [i18n.t("p4cl.no_detail")])
