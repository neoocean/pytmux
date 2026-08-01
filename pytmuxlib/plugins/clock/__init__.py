"""clock 플러그인 — 패널 전체를 큰 시계로 덮는 오버레이(코드네임 clock-mode).

기능 전체가 이 디렉토리 안에 있다. 디렉토리를 통째로 지우면 clock-mode/open-clock/
close-clock 명령은 검색·자동완성·디스패치 어디에도 잡히지 않고, 상태줄 시각 클릭·
ESC 동선의 'clock' 버튼·prefix `t` 키는 조용히 no-op 이 된다 — 코어가 toggle_clock 을
getattr 로만 부르고, 오버레이 그리기/1초 틱/닫기는 plugins 레지스트리 훅
(client_overlay/client_tick/client_close_overlay)으로만 닿기 때문이다.

무게: 이 `__init__` 은 textual/rich 를 모듈 최상단에서 import 하지 않는다(서버
프로세스도 plugins.load() 로 같은 코드를 읽는다). 무거운 의존(Style·테마·렌더 헬퍼)은
실제 그릴 때 메서드 안에서 지연 import 한다.

상호 배타: 한 패널엔 시계/달력 중 하나만 띄운다. 시계를 켜면 같은 패널의 달력(있으면)을
닫는다 — calendar 플러그인이 설치한 `app.calendar_panes` 를 getattr 로 부드럽게
참조하므로 달력 플러그인이 없어도 안전하다."""
from __future__ import annotations

# 로케일 카탈로그(§6 ③ delete-to-disable: 플러그인이 자기 메뉴 라벨 번역을 register).
# i18n 은 os/ipc 만 의존하는 순수 모듈이라 서버 프로세스에서 import 해도 안전(textual/
# rich 와 달리 최상단 import 가능). 메뉴 렌더는 i18n.t("menu.clock-mode", default=raw)
# 라 en 만 넣어도 ko 는 raw 로 폴백하지만, 카탈로그 완결성을 위해 ko 도 함께 등록한다.
from pytmuxlib import i18n  # noqa: E402

i18n.register({
    "ko": {"menu.clock-mode": "시계 모드 토글(현재 패널 큰 시계)"},
    "en": {"menu.clock-mode": "Toggle clock mode (big clock on current pane)"},
})

# 명령 메타데이터 — 코어가 COMMANDS/COMPLETIONS/COMMAND_NOARG/PANE_SCOPED_CMDS 에 합쳐 쓴다.
COMMANDS = [
    ("clock-mode", "현재 패널을 큰 시계로 덮기(토글, 패널 클릭/Shift+ESC 로 닫기)", "설정/기타"),
    ("open-clock", "현재 패널에 큰 시계 표시(이미 떠 있으면 유지)", "설정/기타"),
    ("close-clock", "현재 패널의 큰 시계 닫기", "설정/기타"),
]
NOARG = {"clock-mode", "clock", "open-clock", "close-clock"}
PANE_SCOPED = {"clock-mode", "open-clock", "close-clock"}


def _segments(line: str):
    """`"██  ██"` → `[(0, "██"), (4, "██")]`. 이어진 **비공백** 덩어리와 그 x 오프셋.
    글리프의 공백은 "쓰지 않는다"는 뜻이라 런에 담기면 안 된다."""
    out, x, n = [], 0, len(line)
    while x < n:
        if line[x] == " ":
            x += 1
            continue
        start = x
        while x < n and line[x] != " ":
            x += 1
        out.append((start, line[start:x]))
    return out


class _ClockPlugin:
    name = "clock"
    description = "시계 오버레이 — 패널을 큰 시계로 덮음(clock-mode)"
    category = "오버레이"
    commands = COMMANDS
    noarg = NOARG
    completions = []            # 추가 옵션 템플릿 없음(명령 이름은 레지스트리가 자동 추가)
    # 우클릭 컨텍스트 메뉴 항목(§2.7) — key 는 명령 이름(코어가 _run_command 폴백).
    menu_items = [("clock-mode", "시계 모드 토글(현재 패널 큰 시계)")]
    command_options = {}
    pane_scoped = PANE_SCOPED

    # ---- 클라이언트 측 ----
    def attach_client(self, app):
        """app 인스턴스에 시계 상태/토글 글루를 설치한다(상태줄 시각 클릭·ESC 동선·
        prefix `t`·테스트가 app.clock_panes / app.toggle_clock / app.set_clock 을
        직접 부른다 — ncd 의 app.request_nc_list 설치와 같은 패턴)."""
        app.clock_panes = set()   # clock-mode 가 켜진 패널 id 집합

        def toggle_clock(pane_id):
            if pane_id is None:
                return
            if pane_id in app.clock_panes:
                app.clock_panes.discard(pane_id)
            else:
                app.clock_panes.add(pane_id)
                # 한 패널엔 한 오버레이만 — 달력(있으면)을 닫는다.
                cp = getattr(app, "calendar_panes", None)
                if cp is not None:
                    cp.discard(pane_id)
            app._composite()

        def set_clock(pane_id, on):
            """시계 오버레이를 명시적으로 켜거나(open-clock) 끈다(close-clock).
            토글이 아니라 멱등 — 이미 원하는 상태면 그대로. open 시 같은 패널의
            달력은 닫는다(한 패널엔 한 오버레이)."""
            if pane_id is None:
                return
            if on:
                app.clock_panes.add(pane_id)
                cp = getattr(app, "calendar_panes", None)
                if cp is not None:
                    cp.discard(pane_id)
            else:
                app.clock_panes.discard(pane_id)
            app._composite()

        app.toggle_clock = toggle_clock
        app.set_clock = set_clock

    def handle_command(self, app, c, args):
        if c in ("clock-mode", "clock"):
            app.toggle_clock(app.layout.get("active"))
            return True
        if c == "open-clock":
            app.set_clock(app.layout.get("active"), True)
            return True
        if c == "close-clock":
            app.set_clock(app.layout.get("active"), False)
            return True
        return False

    # ---- 클라이언트 렌더/오버레이 훅 ----
    def client_overlay(self, app, cells, W, H, active):
        """clock-mode 패널을 큰 시계로 덮는다(테마 Style 해석 후 이 플러그인 render
        모듈의 앱-비의존 순수함수에 위임). 뒤의 패널 출력은 흐리게(dim) 계속 보인다."""
        if not getattr(app, "clock_panes", None):
            return
        from rich.style import Style
        from pytmuxlib.clientutil import theme_color
        from .render import draw_clock_overlay
        digit_st = Style(color=theme_color(app, "success"), bold=True)
        draw_clock_overlay(cells, app.layout.get("panes", []),
                           app.clock_panes, W, H, digit_st)

    def client_tick(self, app):
        """1초마다 시계가 떠 있으면 True(코어가 재합성해 초 단위 갱신)."""
        return bool(getattr(app, "clock_panes", None))

    def client_close_overlay(self, app, pane_id):
        """해당 패널의 시계를 닫는다(Shift+ESC/패널 클릭). 닫았으면 True."""
        cp = getattr(app, "clock_panes", None)
        if cp and pane_id in cp:
            cp.discard(pane_id)
            return True
        return False

    # ---- 서버 측: 셀 기여(Tier B · P3) ----
    #
    # 위 `client_overlay` 와 **같은 그림을 같은 규칙으로** 만든다. 다른 것은 도착
    # 방식뿐이다: 정본은 자기 프로세스에서 cells 에 쓰고, 네이티브 클라는 서버가 뽑은
    # 런을 받는다. 그래서 로직(어느 폰트를 고르고 어디에 중앙 정렬하나)은 한 벌이라야
    # 하는데 — 지금은 **아직 두 벌**이다(정본은 P3 까지 종전 경로 유지 · 설계 미결 #1).
    # 여기가 갈리면 두 클라의 시계 자리가 달라지므로, 두 경로가 같은 자산
    # (`blockfont.clock_font_for`)을 쓰는 것으로 그 위험을 좁혀 둔다.
    def plugin_cells(self, server, sess, req):
        from datetime import datetime
        from pytmuxlib.blockfont import clock_font_for
        panes = [p for p in req.get("panes") or []
                 if p["id"] in (req.get("overlays") or {}).get("clock", ())]
        if not panes:
            return []
        text = datetime.now().strftime("%H:%M:%S")
        runs = []
        for p in panes:
            pw, ph = p["w"], p["h"]
            font, ch_h, gcols, cw = clock_font_for(pw, ph, len(text))
            if pw >= cw and ph >= ch_h:
                ox = p["x"] + (pw - cw) // 2
                oy = p["y"] + (ph - ch_h) // 2
                for row in range(ch_h):
                    line = " ".join(font.get(c, [" " * gcols] * ch_h)[row]
                                    for c in text)
                    # ★ **빈 칸은 안 싣는다**. 정본은 글리프의 공백에 아무것도 안 써서
                    #   흐려진 패널 내용이 숫자 구멍으로 비쳐 보인다 — 공백까지 런에
                    #   담으면 그 자리가 지워져 그림이 달라진다. 그래서 이어진 글자
                    #   덩어리마다 런 하나다(칸마다 쪼개는 것보다 프레임이 작다).
                    for x0, seg in _segments(line):
                        runs.append({"x": ox + x0, "y": oy + row, "text": seg,
                                     "style": {"bo": 1}, "theme": "success"})
            else:
                # 큰 글자가 안 들어가면 단순 시각(정본 폴백과 같은 판정).
                ox = p["x"] + max(0, (pw - len(text)) // 2)
                runs.append({"x": ox, "y": p["y"] + ph // 2, "text": text,
                             "style": {"bo": 1}, "theme": "success"})
        return runs

    def plugin_dim_panes(self, server, sess, req):
        """시계가 켜진 패널은 **뒤를 흐리게** 한다(정본 `dim_pane` 과 같은 뜻).
        딤은 있는 셀을 바꾸는 일이라 화면을 든 클라만 할 수 있다 — 서버는 어느
        패널인지만 말한다."""
        return sorted((req.get("overlays") or {}).get("clock", ()))


PLUGIN = _ClockPlugin()
