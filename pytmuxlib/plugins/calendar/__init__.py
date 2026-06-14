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
    ("calendar-mode", "현재 패널을 이번 달 달력으로 덮기(토글, ←/→ 이전·다음 달, ↑/↓ 연 이동, Home 오늘)", "설정/기타"),
    ("open-calendar", "현재 패널에 이번 달 달력 표시(이미 떠 있으면 유지)", "설정/기타"),
    ("close-calendar", "현재 패널의 달력 닫기", "설정/기타"),
]
NOARG = {"calendar-mode", "calendar", "cal",
         "open-calendar", "open-cal", "close-calendar", "close-cal"}
PANE_SCOPED = {"calendar-mode", "calendar", "cal",
               "open-calendar", "open-cal", "close-calendar", "close-cal"}


class _CalendarPlugin:
    name = "calendar"
    description = "달력 오버레이 — 패널을 이번 달 달력으로 덮음(calendar-mode)"
    category = "오버레이"
    commands = COMMANDS
    noarg = NOARG
    completions = []            # 추가 옵션 템플릿 없음(명령 이름은 레지스트리가 자동 추가)
    # 우클릭 컨텍스트 메뉴 항목(§2.7) — key 는 명령 이름(코어가 _run_command 폴백).
    menu_items = [("calendar-mode", "달력 오버레이 토글(현재 패널 이번 달)")]
    command_options = {}
    pane_scoped = PANE_SCOPED

    # ---- 클라이언트 측 ----
    def attach_client(self, app):
        """app 인스턴스에 달력 상태/토글 글루를 설치한다(상태줄 날짜 클릭·ESC 동선·
        테스트가 app.calendar_panes / app.toggle_calendar / app.set_calendar 을
        직접 부른다 — clock 플러그인과 같은 패턴)."""
        app.calendar_panes = set()   # 달력 오버레이가 켜진 패널 id 집합
        # 패널별 표시 월 오프셋(이번 달=0; -1=지난달, +1=다음달 …). 달력을 켤 때마다
        # 이번 달(0)에서 시작하고, 닫으면 항목을 지운다.
        app.calendar_offset = {}

        def toggle_calendar(pane_id):
            if pane_id is None:
                return
            if pane_id in app.calendar_panes:
                app.calendar_panes.discard(pane_id)
                app.calendar_offset.pop(pane_id, None)
            else:
                app.calendar_panes.add(pane_id)
                app.calendar_offset[pane_id] = 0      # 항상 이번 달부터
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
                app.calendar_offset.setdefault(pane_id, 0)
                cp = getattr(app, "clock_panes", None)
                if cp is not None:
                    cp.discard(pane_id)
            else:
                app.calendar_panes.discard(pane_id)
                app.calendar_offset.pop(pane_id, None)
            app._composite()

        def calendar_nav(pane_id, delta):
            """표시 월을 delta(±1=달, ±12=해) 만큼 옮긴다 — ‹/› 클릭존이 부르는 글루.
            해당 패널에 달력이 떠 있을 때만 동작."""
            if pane_id is None or pane_id not in app.calendar_panes:
                return
            app.calendar_offset[pane_id] = \
                app.calendar_offset.get(pane_id, 0) + delta
            app._composite()

        app.toggle_calendar = toggle_calendar
        app.set_calendar = set_calendar
        app.calendar_nav = calendar_nav
        # ‹/› 클릭존: 패널 id → [(x0, x1, y, delta), …]. client_overlay 가 매 렌더마다
        # 다시 채우고, 코어 마우스 핸들러가 getattr 로 읽어 클릭을 calendar_nav 로 보낸다.
        app._calendar_nav_zones = {}

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
        # 매 렌더마다 ‹/› 클릭존을 새로 채운다(레이아웃/오프셋 변화 반영). 코어 마우스
        # 핸들러가 app._calendar_nav_zones 를 getattr 로 읽어 클릭을 디스패치한다.
        zones = app._calendar_nav_zones
        zones.clear()
        draw_calendar_overlay(cells, app.layout.get("panes", []),
                              app.calendar_panes, W, H, styles,
                              offsets=getattr(app, "calendar_offset", None),
                              nav_zones=zones)

    def client_overlay_key(self, app, event):
        """활성 패널에 달력이 떠 있을 때 네비게이션 키를 가로채(소비) 표시 월을 옮긴다.
        ←/PageUp=이전 달, →/PageDown=다음 달, ↑=이전 해, ↓=다음 해, Home/`.`=오늘(이번
        달). 소비하면 True(코어가 키를 패널로 보내지 않음). 달력이 없거나 다른 키면
        False(코어 기본 입력 경로). 패널이 달력에 덮여 있으므로 이 키들을 가져가도 셸
        입력을 가리지 않는다."""
        cp = getattr(app, "calendar_panes", None)
        if not cp:
            return False
        pid = app.layout.get("active")
        if pid is None or pid not in cp:
            return False
        off = app.calendar_offset.get(pid, 0)
        ch = event.character
        if event.key in ("left", "pageup") or ch == "[":
            app.calendar_offset[pid] = off - 1
        elif event.key in ("right", "pagedown") or ch == "]":
            app.calendar_offset[pid] = off + 1
        elif event.key == "up":
            app.calendar_offset[pid] = off - 12
        elif event.key == "down":
            app.calendar_offset[pid] = off + 12
        elif event.key == "home" or ch == ".":
            app.calendar_offset[pid] = 0
        else:
            return False
        app._composite()
        return True

    def client_tick(self, app):
        """1초마다 달력이 떠 있으면 True(코어가 재합성 — 자정 넘으면 '오늘' 강조 이동)."""
        return bool(getattr(app, "calendar_panes", None))

    def client_close_overlay(self, app, pane_id):
        """해당 패널의 달력을 닫는다(Shift+ESC/패널 클릭). 닫았으면 True."""
        cp = getattr(app, "calendar_panes", None)
        if cp and pane_id in cp:
            cp.discard(pane_id)
            getattr(app, "calendar_offset", {}).pop(pane_id, None)
            return True
        return False


PLUGIN = _CalendarPlugin()
