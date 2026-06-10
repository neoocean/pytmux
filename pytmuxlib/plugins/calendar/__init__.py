"""calendar 플러그인 — 패널 전체를 이번 달 달력으로 덮는 오버레이(코드네임 calendar-mode).

clock 플러그인의 미러. 기능 전체가 이 디렉토리 안에 있고, 디렉토리를 통째로 지우면
calendar-mode/open-calendar/close-calendar(별칭 calendar·cal·open-cal·close-cal) 명령은
검색·자동완성·디스패치 어디에도 잡히지 않고, 상태줄 날짜 클릭·ESC 동선의 'date'
버튼은 조용히 no-op 이 된다 — 코어가 toggle_calendar 를 getattr 로만 부르고, 오버레이
그리기/1초 틱/닫기는 plugins 레지스트리 훅(client_overlay/client_tick/
client_close_overlay)으로만 닿기 때문이다.

무게: 이 `__init__` 은 textual/rich 를 모듈 최상단에서 import 하지 않는다(서버
프로세스도 plugins.load() 로 같은 코드를 읽는다). 무거운 의존은 메서드 안에서 지연
import 한다.

상호 배타: 한 패널엔 시계/달력 중 하나만. 달력을 켜면 같은 패널의 시계(있으면)를
닫는다 — clock 플러그인이 설치한 `app.clock_panes` 를 getattr 로 부드럽게 참조하므로
clock 플러그인이 없어도 안전하다."""
from __future__ import annotations

# 명령 메타데이터 — 코어가 COMMANDS/COMPLETIONS/COMMAND_NOARG/PANE_SCOPED_CMDS 에 합쳐 쓴다.
COMMANDS = [
    ("calendar-mode", "현재 패널을 이번 달 달력으로 덮기(토글, 상태줄 날짜 클릭/패널 클릭으로 닫기)", "설정/기타"),
    ("open-calendar", "현재 패널에 이번 달 달력 표시(이미 떠 있으면 유지)", "설정/기타"),
    ("close-calendar", "현재 패널의 달력 닫기", "설정/기타"),
]
NOARG = {"calendar-mode", "calendar", "cal",
         "open-calendar", "open-cal", "close-calendar", "close-cal"}
PANE_SCOPED = {"calendar-mode", "calendar", "cal",
               "open-calendar", "open-cal", "close-calendar", "close-cal"}


class _CalendarPlugin:
    name = "calendar"
    commands = COMMANDS
    noarg = NOARG
    completions = []            # 추가 옵션 템플릿 없음(명령 이름은 레지스트리가 자동 추가)
    command_options = {}
    pane_scoped = PANE_SCOPED

    # ---- 클라이언트 측 ----
    def attach_client(self, app):
        """app 인스턴스에 달력 상태/토글 글루를 설치한다(상태줄 날짜 클릭·ESC 동선·
        테스트가 app.calendar_panes / app.toggle_calendar / app.set_calendar 을
        직접 부른다 — clock 플러그인과 같은 패턴)."""
        app.calendar_panes = set()   # 달력 오버레이가 켜진 패널 id 집합

        def toggle_calendar(pane_id):
            if pane_id is None:
                return
            if pane_id in app.calendar_panes:
                app.calendar_panes.discard(pane_id)
            else:
                app.calendar_panes.add(pane_id)
                # 한 패널엔 한 오버레이만 — 시계(있으면)를 닫는다.
                cp = getattr(app, "clock_panes", None)
                if cp is not None:
                    cp.discard(pane_id)
            app._composite()

        def set_calendar(pane_id, on):
            """달력 오버레이를 명시적으로 켜거나(open-calendar) 끈다(close-calendar).
            멱등 — open 시 같은 패널의 시계는 닫는다."""
            if pane_id is None:
                return
            if on:
                app.calendar_panes.add(pane_id)
                cp = getattr(app, "clock_panes", None)
                if cp is not None:
                    cp.discard(pane_id)
            else:
                app.calendar_panes.discard(pane_id)
            app._composite()

        app.toggle_calendar = toggle_calendar
        app.set_calendar = set_calendar

    def handle_command(self, app, c, args):
        if c in ("calendar-mode", "calendar", "cal"):
            app.toggle_calendar(app.layout.get("active"))
            return True
        if c in ("open-calendar", "open-cal"):
            app.set_calendar(app.layout.get("active"), True)
            return True
        if c in ("close-calendar", "close-cal"):
            app.set_calendar(app.layout.get("active"), False)
            return True
        return False

    # ---- 클라이언트 렌더/오버레이 훅 ----
    def client_overlay(self, app, cells, W, H, active):
        """달력 모드 패널을 이번 달 달력으로 덮는다(테마 Style 해석 후 이 플러그인
        render 모듈의 순수함수에 위임). 뒤의 패널 출력은 흐리게(dim) 계속 보이고,
        오늘 날짜는 강조."""
        if not getattr(app, "calendar_panes", None):
            return
        from rich.style import Style
        from pytmuxlib.clientutil import theme_color
        from .render import draw_calendar_overlay
        styles = {
            "day": Style(color=theme_color(app, "foreground")),
            "title": Style(color=theme_color(app, "success"), bold=True),
            "today": Style(color="black",
                           bgcolor=theme_color(app, "success"), bold=True),
            "big_today": Style(color=theme_color(app, "success"), bold=True),
            "border": Style(color=theme_color(app, "accent")),
        }
        draw_calendar_overlay(cells, app.layout.get("panes", []),
                              app.calendar_panes, W, H, styles)

    def client_tick(self, app):
        """1초마다 달력이 떠 있으면 True(코어가 재합성 — 자정 넘으면 '오늘' 강조 이동)."""
        return bool(getattr(app, "calendar_panes", None))

    def client_close_overlay(self, app, pane_id):
        """해당 패널의 달력을 닫는다(Shift+ESC/패널 클릭). 닫았으면 True."""
        cp = getattr(app, "calendar_panes", None)
        if cp and pane_id in cp:
            cp.discard(pane_id)
            return True
        return False


PLUGIN = _CalendarPlugin()
