"""claude-name-sync 의 모달 화면 — 이름 동기화 규칙 목록 편집기(NameSyncScreen)와
규칙 추가/수정 폼(RuleFormScreen).

textual 의존이 있어 이 모듈은 **실제로 팝업을 열 때** 지연 import 된다(플러그인 __init__
은 가벼움 — 서버 프로세스도 plugins.load() 로 읽기 때문)."""
from __future__ import annotations

from textual.screen import ModalScreen
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Label, Static

from rich.text import Text

from pytmuxlib import i18n

# 한글 원문을 i18n 키로 쓰고(코드베이스 관례) en 번역을 등록한다. 렌더 시점에 t() 로
# 클라 로케일을 따른다.
_TITLE = "namesync — 이름 동기화 규칙"
_HINT = ("↑↓ 이동 · [a]추가 [e]수정 [d]삭제 · [Enter] 현재 cwd 로 추가 · "
         "[Esc] 저장·닫기")
_EMPTY = "규칙이 없습니다. [a] 또는 [Enter](현재 cwd)로 추가하세요."
_ANY = "(any)"
_FORM_ADD = "규칙 추가 — Ctrl+S 저장 · Esc 취소"
_FORM_EDIT = "규칙 수정 — Ctrl+S 저장 · Esc 취소"
_L_KEYWORD = "키워드(탭·패널·Claude 세션 이름)"
_L_PATH = "경로(정확히 이 디렉토리에서 실행 시)"
_L_HOST = "호스트(비우면 모든 머신)"
_L_OS = "OS: darwin/linux/windows(비우면 모든 OS)"
_ERR_REQ = "경로와 키워드는 필수입니다."
_SAVE = "저장"
_CANCEL = "취소"

i18n.register({
    "ko": {k: k for k in (_TITLE, _HINT, _EMPTY, _ANY, _FORM_ADD, _FORM_EDIT,
                          _L_KEYWORD, _L_PATH, _L_HOST, _L_OS, _ERR_REQ,
                          _SAVE, _CANCEL)},
    "en": {
        _TITLE: "namesync — name-sync rules",
        _HINT: ("↑↓ move · [a]add [e]edit [d]delete · [Enter] add current cwd · "
                "[Esc] save & close"),
        _EMPTY: "No rules. Add with [a] or [Enter] (current cwd).",
        _ANY: "(any)",
        _FORM_ADD: "Add rule — Ctrl+S save · Esc cancel",
        _FORM_EDIT: "Edit rule — Ctrl+S save · Esc cancel",
        _L_KEYWORD: "Keyword (tab · pane · Claude session name)",
        _L_PATH: "Path (matched when Claude runs in exactly this directory)",
        _L_HOST: "Host (blank = any machine)",
        _L_OS: "OS: darwin/linux/windows (blank = any OS)",
        _ERR_REQ: "Path and keyword are required.",
        _SAVE: "Save",
        _CANCEL: "Cancel",
        # 저장 완료 알림(플러그인 __init__ handle_message 가 t 로 조회).
        "nsmsg.saved": "namesync: {n} rule(s) saved",
    },
})
i18n.register({"ko": {"nsmsg.saved": "namesync: 규칙 {n}개 저장됨"}})


class NameSyncScreen(ModalScreen):
    """이름 동기화 규칙 목록 모달. ↑↓ 로 커서 이동, a/e/d 로 추가·수정·삭제,
    Enter 로 현재 cwd 를 미리 채운 추가, Esc 로 **저장 후 닫기**(dismiss=규칙목록).

    포커스 가능한 자식 위젯이 없어(Static 만) 화면이 직접 on_key 를 받는다."""

    CSS = """
    NameSyncScreen { align: center middle; background: $background 80%; }
    #nsbox { width: 90%; max-width: 110; height: auto; max-height: 90%;
             border: round $accent; background: $panel; padding: 0 1; }
    #nshead { width: 100%; height: 1; }
    #nstitle { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #nsclose { width: 5; height: 1; content-align: center middle;
               background: $error; color: $text; text-style: bold; }
    #nsspacer { width: 100%; height: 1; }
    #nslist { width: 100%; height: auto; min-height: 3; max-height: 70%; }
    #nshint { width: 100%; height: 1; margin-top: 1; color: $text-muted; }
    """

    def __init__(self, rules, host="", osname="", cwd=""):
        super().__init__()
        # 편집 대상은 로컬 복사본 — Esc 저장 시 dismiss 로 돌려준다.
        self._rules = [dict(r) for r in (rules or [])]
        self._host = host or ""
        self._os = osname or ""
        self._cwd = cwd or ""
        self._sel = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="nsbox"):
            with Horizontal(id="nshead"):
                yield Label(i18n.t(_TITLE), id="nstitle")
                yield Label("[x]", id="nsclose", markup=False)
            yield Label("", id="nsspacer")
            yield Static(id="nslist")
            yield Label(i18n.t(_HINT), id="nshint")

    def on_mount(self):
        self._render_list()

    # ---- 목록 렌더 ----
    def _render_list(self):
        self._sel = max(0, min(self._sel, max(0, len(self._rules) - 1)))
        body = Text()
        if not self._rules:
            body.append(i18n.t(_EMPTY), style="dim")
        else:
            for i, r in enumerate(self._rules):
                sel = (i == self._sel)
                host = r.get("host") or i18n.t(_ANY)
                osn = r.get("os") or i18n.t(_ANY)
                line = f"{'▶' if sel else ' '} {host}/{osn}  {r.get('path','')}"
                body.append(line, style="bold reverse" if sel else "")
                body.append("  → ", style="dim" if not sel else "reverse")
                body.append(r.get("keyword", ""),
                            style="bold reverse" if sel else "bold")
                if i != len(self._rules) - 1:
                    body.append("\n")
        self.query_one("#nslist", Static).update(body)

    # ---- 키/클릭 ----
    def on_key(self, event: events.Key):
        k = event.key
        if k == "escape":
            event.stop()
            self.dismiss(self._rules)          # 저장 후 닫기
        elif k in ("up", "k"):
            event.stop()
            self._sel -= 1
            self._render_list()
        elif k in ("down", "j"):
            event.stop()
            self._sel += 1
            self._render_list()
        elif k == "a":
            event.stop()
            self._open_form(None)
        elif k == "e":
            event.stop()
            if self._rules:
                self._open_form(self._sel)
        elif k == "d":
            event.stop()
            if self._rules:
                del self._rules[self._sel]
                self._render_list()
        elif k == "enter":
            event.stop()
            self._open_form(None, path=self._cwd)   # 현재 cwd 미리 채운 추가

    def on_click(self, event: events.Click):
        w = getattr(event, "widget", None)
        while w is not None:
            if getattr(w, "id", None) == "nsclose":
                event.stop()
                self.dismiss(self._rules)
                return
            w = w.parent

    # ---- 추가/수정 폼 열기 ----
    def _open_form(self, index, path=None):
        if index is None:
            rule = {"host": self._host, "os": self._os,
                    "path": path or "", "keyword": ""}
            editing = False
        else:
            rule = dict(self._rules[index])
            editing = True

        def _done(res):
            if res is None:
                return
            if editing:
                self._rules[index] = res
            else:
                self._rules.append(res)
                self._sel = len(self._rules) - 1
            self._render_list()
        self.app.push_screen(RuleFormScreen(rule, editing), _done)


class RuleFormScreen(ModalScreen):
    """규칙 추가/수정 폼. 키워드·경로·호스트·OS 입력. Ctrl+S 로 저장(dismiss=dict,
    경로·키워드가 비면 저장 거부), Esc 로 취소(dismiss=None). 호스트/OS 를 비우면
    '모든 머신/OS'(와일드카드)로 저장된다."""

    CSS = """
    RuleFormScreen { align: center middle; background: $background 80%; }
    #rfbox { width: 80%; max-width: 90; height: auto; border: round $accent;
             background: $panel; padding: 0 1; }
    #rftitle { width: 100%; height: 1; color: $accent; text-style: bold; }
    #rfbox Label.lbl { width: 100%; height: 1; margin-top: 1; color: $text-muted; }
    #rfbox Input { width: 100%; }
    #rferr { width: 100%; height: 1; color: $error; }
    #rfbtns { width: 100%; height: 1; margin-top: 1; align-horizontal: right; }
    #rfbtns Label { width: auto; height: 1; padding: 0 2; margin-left: 2;
                    text-style: bold; }
    #rfsave { background: $success; color: $text; }
    #rfcancel { background: $panel-darken-2; color: $text; }
    """

    def __init__(self, rule, editing=False):
        super().__init__()
        self._rule = dict(rule)
        self._editing = editing

    def compose(self) -> ComposeResult:
        with Vertical(id="rfbox"):
            yield Label(i18n.t(_FORM_EDIT if self._editing else _FORM_ADD),
                        id="rftitle")
            yield Label(i18n.t(_L_KEYWORD), classes="lbl")
            yield Input(value=self._rule.get("keyword", ""), id="rf_keyword")
            yield Label(i18n.t(_L_PATH), classes="lbl")
            yield Input(value=self._rule.get("path", ""), id="rf_path")
            yield Label(i18n.t(_L_HOST), classes="lbl")
            yield Input(value=self._rule.get("host", ""), id="rf_host")
            yield Label(i18n.t(_L_OS), classes="lbl")
            yield Input(value=self._rule.get("os", ""), id="rf_os")
            yield Label("", id="rferr")
            with Horizontal(id="rfbtns"):
                yield Label(i18n.t(_SAVE), id="rfsave")
                yield Label(i18n.t(_CANCEL), id="rfcancel")

    def on_mount(self):
        self.query_one("#rf_keyword", Input).focus()

    def _collect(self):
        return {
            "keyword": self.query_one("#rf_keyword", Input).value.strip(),
            "path": self.query_one("#rf_path", Input).value.strip(),
            "host": self.query_one("#rf_host", Input).value.strip(),
            "os": self.query_one("#rf_os", Input).value.strip(),
        }

    def _try_save(self):
        r = self._collect()
        if not r["path"] or not r["keyword"]:
            # 경로·키워드는 필수(둘 중 하나라도 비면 규칙이 무의미).
            self.query_one("#rferr", Label).update(i18n.t(_ERR_REQ))
            return
        self.dismiss(r)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key == "ctrl+s":
            event.stop()
            self._try_save()

    def on_click(self, event: events.Click):
        w = getattr(event, "widget", None)
        while w is not None:
            wid = getattr(w, "id", None)
            if wid == "rfcancel":
                event.stop()
                self.dismiss(None)
                return
            if wid == "rfsave":
                event.stop()
                self._try_save()
                return
            w = w.parent
