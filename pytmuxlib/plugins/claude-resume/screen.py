"""claude-resume 리줌 피커 모달(textual). 클라에서 실제로 열 때 지연 import 된다.

서버가 보낸 세션 목록(최신순, 각 {id,cwd,title,mtime,project})을 리스트로 보여주고,
행을 골라 Enter(또는 클릭)하면 서버에 `claude_resume_session` 을 보내 새 탭에서 리줌한다.
[x]/바깥 클릭/Esc 로 닫는다(다른 모달 관례)."""
from __future__ import annotations

import time

from textual.screen import ModalScreen
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Label, ListItem, ListView, Static

from pytmuxlib import i18n
from pytmuxlib.clientutil import _char_cells

_PROJ_CELLS = 22       # 프로젝트 열 표시 폭(셀, CJK 2칸 고려해 정렬)


def _cellpad(s: str, cells: int) -> str:
    """문자열을 표시 셀폭 cells 로 우측 패딩(넘치면 … 로 자름) — 열 정렬용."""
    out, used = [], 0
    for ch in s:
        cw = _char_cells(ch)
        if used + cw > cells:
            # 자리 확보 후 … 붙이기
            while out and used + 1 > cells:
                used -= _char_cells(out[-1])
                out.pop()
            out.append("…")
            used += 1
            break
        out.append(ch)
        used += cw
    return "".join(out) + " " * max(0, cells - used)


class ClaudeResumeScreen(ModalScreen):
    CSS = """
    ClaudeResumeScreen { align: center middle; background: $background 80%; }
    #crbox { width: 86%; max-width: 100; height: 80%;
             border: round $accent; background: $panel; padding: 0 1; }
    #crhead { width: 100%; height: 1; }
    #crtitle { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #crclose { width: 5; height: 1; content-align: center middle;
               background: $error; color: $text; text-style: bold; }
    #crnone { width: 100%; height: 1fr; color: $text-muted; content-align: center middle; }
    #crlist { width: 100%; height: 1fr;
              scrollbar-size-vertical: 2; scrollbar-color: $accent; }
    #crhint { width: 100%; height: 1; color: $text-muted; }
    """

    def __init__(self, sessions):
        super().__init__()
        self._sessions = list(sessions or [])

    def compose(self) -> ComposeResult:
        with Vertical(id="crbox"):
            with Horizontal(id="crhead"):
                yield Static(i18n.t("cresume.title"), id="crtitle")
                yield Label("[x]", id="crclose", markup=False)
            if not self._sessions:
                yield Static(i18n.t("cresume.none"), id="crnone", markup=False)
            else:
                items = []
                for i, s in enumerate(self._sessions):
                    items.append(ListItem(Label(self._row(s), markup=False),
                                          id=f"cr_{i}"))
                yield ListView(*items, id="crlist")
            yield Static(i18n.t("cresume.hint"), id="crhint", markup=False)

    @staticmethod
    def _when(mtime) -> str:
        try:
            return time.strftime("%m-%d %H:%M", time.localtime(mtime or 0))
        except (ValueError, OSError, TypeError):
            return ""

    def _row(self, s: dict) -> str:
        """한 세션 행: '수정시각  프로젝트  제목'(프로젝트 열 정렬)."""
        when = self._when(s.get("mtime"))
        proj = _cellpad(s.get("project") or "", _PROJ_CELLS)
        title = s.get("title") or ""
        return f"{when}  {proj}  {title}"

    def on_mount(self):
        if self._sessions:
            self.query_one(ListView).focus()

    def _resume(self, idx: int):
        if 0 <= idx < len(self._sessions):
            s = self._sessions[idx]
            self.app.send_cmd("claude_resume_session",
                              session_id=s.get("id"), cwd=s.get("cwd"))
            self.app.display_message(
                i18n.t("cresume.opening", title=s.get("title", "")))
            self.dismiss(None)

    def on_list_view_selected(self, event):
        # Enter/클릭으로 행 선택 → 그 세션 리줌. item id 'cr_{i}' 에서 인덱스 추출.
        event.stop()
        wid = getattr(event.item, "id", "") or ""
        if wid.startswith("cr_"):
            try:
                self._resume(int(wid[3:]))
            except ValueError:
                pass

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)

    def on_click(self, event: events.Click):
        # [x] 클릭/바깥(백드롭) 클릭 → 닫기(다른 모달 공통 동선).
        w = getattr(event, "widget", None)
        wid = inside = None
        while w is not None:
            this = getattr(w, "id", None)
            if this:
                if wid is None:
                    wid = this
                if this == "crbox":
                    inside = True
            w = w.parent
        if wid == "crclose" or not inside:
            event.stop()
            self.dismiss(None)
