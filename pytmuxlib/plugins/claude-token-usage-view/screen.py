"""usage-view 팝업/탭 화면(textual). 모달(popup) / 풀스크린(tab) 표시 경로.

한도 막대는 코어 `usage_bar_lines` 를, 카운트다운 블록 글자는 코어 공유 `_CLOCK_FONT`
를 재사용한다. 1초 틱으로 카운트다운만 다시 그려 status 폴링(느림)과 독립적으로 매 초
센다(참조 claude-token-viewer 와 동일). 매 틱 `app.status.usage_limits` 를 다시 읽어
[u] 갱신 결과도 자동 반영한다.

실제로 열 때만 지연 import 된다(서버 프로세스는 이 모듈을 읽지 않는다). 데이터는
claude-code 가 status 로 싣는 usage_limits 를 getattr 로 부드럽게 읽고, 없으면 안내만."""
from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from pytmuxlib import i18n
from pytmuxlib.clientscreens import usage_bar_lines
from pytmuxlib.clientutil import _CLOCK_FONT, _CLOCK_FONT_ROWS

from .reset import fmt_countdown, parse_reset_to_dt, urgency

# 빈 막대 트랙을 받아올 구분 글자(usage_bar_lines track_char). 표시 단계에서 이 글자만
# 골라 회색 막대('█')로 치환·색칠한다 — 화면에 '░' 자체가 보이지는 않는다.
_TRACK = "░"
_TRACK_STYLE = "grey50"   # 빈 부분 회색(배경=검정·채움=흰색과 구분)

# 카운트다운 대상 후보(이른 순으로 고른다) — usage_bar_lines 와 같은 버킷.
_BUCKETS = [("session", "세션 5h"), ("week_all", "주 전체"),
            ("week_sonnet", "주 Sonnet")]

# urgency 토큰 → rich 스타일.
_URGENCY_STYLE = {"red": "bold bright_red", "yellow": "bold yellow",
                  "cyan": "bold bright_cyan"}


def soonest_reset(usage, now):
    """usage_limits 의 버킷 중 가장 이른(곧 도래) 리셋을 (label, dt) 로. 없으면
    (None, None). 화면·오버레이가 공유하는 선택 규칙."""
    best = (None, None)
    for key, label in _BUCKETS:
        d = usage.get(key) if isinstance(usage, dict) else None
        if not isinstance(d, dict):
            continue
        dt = parse_reset_to_dt(d.get("reset"), now)
        if dt is None:
            continue
        if best[1] is None or dt < best[1]:
            best = (label, dt)
    return best


def big_clock_text(td, style):
    """timedelta(<24h) → 3행 블록 HH:MM:SS Text(_CLOCK_FONT). 24시간 이상이거나
    음수면 None(호출부가 텍스트 카운트다운으로 폴백)."""
    total = int(td.total_seconds())
    if total < 0 or total >= 86400:
        return None
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    text = f"{h:02d}:{m:02d}:{s:02d}"
    rows = [""] * _CLOCK_FONT_ROWS
    for i, ch in enumerate(text):
        glyph = _CLOCK_FONT.get(ch, ["   "] * _CLOCK_FONT_ROWS)
        for r in range(_CLOCK_FONT_ROWS):
            if i:
                rows[r] += " "
            rows[r] += glyph[r]
    return Text("\n".join(rows), style=style)


class UsageScreen(ModalScreen):
    """Claude 사용 한도 막대 + 다음 리셋 카운트다운. `full=True` 면 풀스크린(탭 모드),
    아니면 중앙 모달(팝업 모드). [t] 로 두 형태를 즉석 전환한다."""

    CSS = """
    UsageScreen { align: center middle; background: $background 70%; }
    #ubox { width: 80%; max-width: 80; height: auto; max-height: 90%;
            border: round $accent; background: $panel; padding: 1 2; }
    UsageScreen.full #ubox { width: 100%; height: 100%;
                             max-width: 100%; max-height: 100%; }
    #uhead  { width: 100%; height: 1; }
    #utitle { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #uclose { width: 5; height: 1; content-align: center middle;
              background: $error; color: $text; text-style: bold; }
    #ubars  { width: 100%; height: auto; padding: 1 0; }
    #uclock { width: 100%; height: auto; content-align: center middle; }
    /* 하단 동작 버튼: 키 단축키([u]/[t]/[a])뿐 아니라 클릭/터치로도 쓸 수 있게
       각각을 탭 가능한 버튼으로(요청 — 모바일에서 키를 못 누른다). */
    #uhint  { width: 100%; height: auto; align: center middle; margin-top: 1; }
    #uhint Label { height: 1; padding: 0 1; margin: 0 1;
                   background: $boost; color: $text; }
    """

    def __init__(self, full: bool = False):
        super().__init__()
        self._full = full

    def compose(self) -> ComposeResult:
        with Vertical(id="ubox"):
            with Horizontal(id="uhead"):
                yield Static(id="utitle")
                # markup=False: "[x]" 가 Textual 마크업으로 해석돼 사라지지 않게
                # (InfoScreen 닫기 버튼과 동일 패턴).
                yield Label("[x]", id="uclose", markup=False)
            yield Static(id="ubars")
            yield Static(id="uclock")
            # 하단 동작 버튼(클릭/터치 가능). 괄호 안은 키보드 단축키. markup=False:
            # 대괄호가 마크업으로 사라지지 않게.
            with Horizontal(id="uhint"):
                yield Label(i18n.t("uview.btn_refresh"), id="uref", markup=False)
                yield Label(i18n.t("uview.btn_toggle"), id="utgl", markup=False)
                yield Label(i18n.t("uview.btn_pane"), id="upane", markup=False)

    def on_mount(self):
        if self._full:
            self.add_class("full")
        self.set_interval(1.0, self._redraw)   # 카운트다운 매 초 갱신
        self._redraw()

    def _redraw(self):
        usage = getattr(self.app.status, "usage_limits", None)
        now = datetime.now()
        self.query_one("#utitle", Static).update(i18n.t("uview.title"))
        bars = self.query_one("#ubars", Static)
        clock = self.query_one("#uclock", Static)
        w = self.app.size.width if self.app.size else 80
        age = getattr(self.app.status, "usage_age_sec", None)
        # 빈 트랙을 '░'(구분 글자)로 채워 받아, 아래에서 그 칸만 회색 막대로 칠한다 —
        # 막대(채움)=흰색 그대로, 빈 부분=회색으로 배경과 구분(요청).
        lines = usage_bar_lines(usage, min(w - 6, 76), age_sec=age,
                                right_align=True, track_char=_TRACK)
        if not lines:
            bars.update(Text(i18n.t("uview.no_data"), style="yellow"))
            clock.update("")
            return
        bars.update(self._colorize_tracks(lines))
        label, dt = soonest_reset(usage, now)
        if dt is None:
            clock.update(Text(i18n.t("uview.reset_unparsable"), style="dim"))
            return
        td = dt - now
        style = _URGENCY_STYLE[urgency(td)]
        out = Text()
        out.append(i18n.t("uview.next_reset", label=label) + "\n",
                   style="bold cyan")
        big = big_clock_text(td, style)
        out.append(big if big is not None else Text(fmt_countdown(td), style=style))
        clock.update(out)

    @staticmethod
    def _colorize_tracks(lines):
        """막대 줄 목록 → 빈 트랙 글자('░')를 회색 '█' 막대로 치환한 Text. 채움('█')
        과 그 외 글자는 기본색(흰색) 그대로 둬 '막대=흰색·빈 부분=회색'이 되게 한다."""
        out = Text()
        for i, line in enumerate(lines):
            if i:
                out.append("\n")
            for ch in line:
                if ch == _TRACK:
                    out.append("█", style=_TRACK_STYLE)
                else:
                    out.append(ch)
        return out

    # ---- 동작(키보드 단축키와 하단 버튼 탭이 공유) ----
    def _do_refresh(self):
        self.app.send_cmd("refresh_usage")
        self.app.display_message(i18n.t("uview.refreshing"), 3.0)

    def _do_toggle(self):
        self._full = not self._full
        self.set_class(self._full, "full")

    def _do_pane(self):
        self.dismiss(None)
        fn = getattr(self.app, "open_usage_view", None)
        if fn is not None:
            fn("pane")

    # 하단 버튼 id → 동작(클릭/터치). on_click 이 조상 체인에서 이 id 를 찾으면 호출.
    _BTN_ACTIONS = {"uref": _do_refresh, "utgl": _do_toggle, "upane": _do_pane}

    def on_click(self, event: events.Click):
        # 조상 체인을 거슬러: 닫기 [x](#uclose)·하단 버튼(uref/utgl/upane) → 해당 동작.
        # 박스(#ubox) 안이면 유지, 바깥(백드롭) 클릭이면 닫음(플러그인 관리·InfoScreen
        # 과 동일 판정).
        w = getattr(event, "widget", None)
        inside_box = False
        while w is not None:
            wid = getattr(w, "id", None)
            if wid == "uclose":
                event.stop()
                self.dismiss(None)
                return
            act = self._BTN_ACTIONS.get(wid)
            if act is not None:
                event.stop()
                act(self)
                return
            if wid == "ubox":
                inside_box = True
            w = w.parent
        if not inside_box:
            event.stop()
            self.dismiss(None)

    def on_key(self, event: events.Key):
        k = event.key
        if k in ("escape", "q"):
            event.stop()
            self.dismiss(None)
        elif k == "u":
            event.stop()
            self._do_refresh()
        elif k == "t":
            event.stop()
            self._do_toggle()
        elif k == "a":
            event.stop()
            self._do_pane()
