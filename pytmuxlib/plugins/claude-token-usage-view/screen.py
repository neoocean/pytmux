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
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from pytmuxlib.clientscreens import usage_bar_lines
from pytmuxlib.clientutil import _CLOCK_FONT

from .reset import fmt_countdown, parse_reset_to_dt, urgency

_HINT = "Esc 닫기 · [u] 갱신 · [t] 팝업/탭 전환 · [a] 패널 오버레이"

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
    """timedelta(<24h) → 5행 블록 HH:MM:SS Text(_CLOCK_FONT). 24시간 이상이거나
    음수면 None(호출부가 텍스트 카운트다운으로 폴백)."""
    total = int(td.total_seconds())
    if total < 0 or total >= 86400:
        return None
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    text = f"{h:02d}:{m:02d}:{s:02d}"
    rows = ["", "", "", "", ""]
    for i, ch in enumerate(text):
        glyph = _CLOCK_FONT.get(ch, ["   "] * 5)
        for r in range(5):
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
    #utitle { width: 100%; height: 1; color: $accent; text-style: bold; }
    #ubars  { width: 100%; height: auto; padding: 1 0; }
    #uclock { width: 100%; height: auto; content-align: center middle; }
    #uhint  { width: 100%; height: 1; color: $text-muted; }
    """

    def __init__(self, full: bool = False):
        super().__init__()
        self._full = full

    def compose(self) -> ComposeResult:
        with Vertical(id="ubox"):
            yield Static(id="utitle")
            yield Static(id="ubars")
            yield Static(id="uclock")
            yield Static(_HINT, id="uhint")

    def on_mount(self):
        if self._full:
            self.add_class("full")
        self.set_interval(1.0, self._redraw)   # 카운트다운 매 초 갱신
        self._redraw()

    def _redraw(self):
        usage = getattr(self.app.status, "usage_limits", None)
        now = datetime.now()
        self.query_one("#utitle", Static).update("Claude 사용 한도 (/usage)")
        bars = self.query_one("#ubars", Static)
        clock = self.query_one("#uclock", Static)
        w = self.app.size.width if self.app.size else 80
        age = getattr(self.app.status, "usage_age_sec", None)
        lines = usage_bar_lines(usage, min(w - 6, 76), age_sec=age,
                                right_align=True)
        if not lines:
            bars.update(Text("한도 데이터 없음 — Claude 패널에서 /usage 실행 후 [u]로 갱신",
                             style="yellow"))
            clock.update("")
            return
        bars.update("\n".join(lines))
        label, dt = soonest_reset(usage, now)
        if dt is None:
            clock.update(Text("(리셋 시각을 파싱할 수 없음)", style="dim"))
            return
        td = dt - now
        style = _URGENCY_STYLE[urgency(td)]
        out = Text()
        out.append(f"다음 리셋: {label}\n", style="bold cyan")
        big = big_clock_text(td, style)
        out.append(big if big is not None else Text(fmt_countdown(td), style=style))
        clock.update(out)

    def on_key(self, event: events.Key):
        k = event.key
        if k in ("escape", "q"):
            event.stop()
            self.dismiss(None)
        elif k == "u":
            event.stop()
            self.app.send_cmd("refresh_usage")
            self.app.display_message("사용량 갱신 중… (숨은 /usage, ~수초)", 3.0)
        elif k == "t":
            event.stop()
            self._full = not self._full
            self.set_class(self._full, "full")
        elif k == "a":
            event.stop()
            self.dismiss(None)
            fn = getattr(self.app, "open_usage_view", None)
            if fn is not None:
                fn("pane")
