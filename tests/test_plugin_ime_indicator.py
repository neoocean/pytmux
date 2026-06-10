"""ime-indicator 플러그인 회귀 — 한/영 추정 상태 전이, 배지 그리기, 명령 토글, 계약.

설계 배경(docs/IME_PREEDIT_CURSOR_SCENARIO.md): 앱은 OS IME 의 *조합 중* preedit 을
관찰할 수 없고 **확정된 글자만** 키 이벤트로 받는다. 그래서 한/영은 패널로 보낼 확정
입력 문자의 스크립트로 추정한다 — 한글→'한', ASCII 글자→'EN', 숫자/기호는 모드 중립.

`draw_ime_indicator` 는 앱 비의존 순수 함수라 앱·소켓 없이 직접 호출해 셀 출력을 단언한다.
client_key/handle_command 는 가짜 app 으로, 코어 on_key 배선은 라이브 앱으로 가드한다.
계약(delete-to-disable): 플러그인을 Registry 에서 빼면 ime 명령/훅이 전부 사라진다.
"""
import harness  # noqa: F401  (sys.path 주입)
from harness import make_app, server_only, teardown
from rich.style import Style
from textual.events import Key

import pytmuxlib.plugins as plugins


def _grid(w, h):
    base = Style()
    return [[(" ", base) for _ in range(w)] for _ in range(h)]


def _text_rows(cells):
    return ["".join(c[0] for c in row) for row in cells]


# 하이픈 디렉토리(ime-indicator)라 일반 import 불가 — importlib 로 모듈을 가져온다.
import importlib  # noqa: E402

_render = importlib.import_module("pytmuxlib.plugins.ime-indicator.render")
_pkg = importlib.import_module("pytmuxlib.plugins.ime-indicator")
draw_ime_indicator = _render.draw_ime_indicator
PLUGIN = _pkg.PLUGIN


class _FakeApp:
    """client_key/handle_command 가 닿는 최소 표면만 흉내낸 가짜 앱."""
    def __init__(self):
        self.ime_show = True
        self.ime_state = "EN"
        self.composited = 0
        self.messages = []

    def _composite(self):
        self.composited += 1

    def display_message(self, m):
        self.messages.append(m)


class _Ev:
    def __init__(self, character):
        self.character = character


# ---- 1) 순수 렌더 함수 ----
async def test_badge_drawn_top_right_and_widths():
    # '한'(와이드 2칸) → "[한]" = 4칸, 우측 reserve=4 비우고 우측정렬.
    cells = _grid(40, 5)
    st = Style(color="black", bgcolor="green", bold=True)
    draw_ime_indicator(cells, 40, 5, "한", st)
    row0 = _text_rows(cells)[0]
    assert "[한]" in row0, row0
    # 우측 4칸은 비어 있어야([x] 자리). 마지막 4칸 공백 확인.
    assert row0[-4:] == "    ", repr(row0[-4:])
    # 배지는 row 0 에만(다른 행은 공백).
    assert all(c[0] == " " for row in cells[1:] for c in row)
    # 'EN' = "[EN]" 4칸, 모두 단일폭.
    cells2 = _grid(40, 5)
    draw_ime_indicator(cells2, 40, 5, "EN", st)
    assert "[EN]" in _text_rows(cells2)[0]


async def test_badge_skipped_when_too_narrow():
    # 폭이 배지+reserve 를 못 담으면 아무것도 안 그린다.
    cells = _grid(6, 3)
    draw_ime_indicator(cells, 6, 3, "한", Style())
    assert all(c[0] == " " for row in cells for c in row)


async def test_badge_wide_continuation_cell():
    # 한글 본체 다음 칸은 빈 연속 셀("")이어야 정렬이 안 깨진다.
    cells = _grid(40, 2)
    draw_ime_indicator(cells, 40, 2, "한", Style())
    chars = [c[0] for c in cells[0]]
    i = chars.index("한")
    assert chars[i + 1] == "", chars[i:i + 3]


# ---- 2) client_key 한/영 추정 상태 전이 ----
async def test_client_key_state_transitions():
    app = _FakeApp()
    app.ime_state = "EN"
    # 한글 확정 입력 → '한' 전환 + 재합성.
    PLUGIN.client_key(app, _Ev("가"))
    assert app.ime_state == "한"
    assert app.composited == 1
    # 같은 상태 유지 입력은 재합성 안 함(중복 합성 방지).
    PLUGIN.client_key(app, _Ev("나"))
    assert app.ime_state == "한" and app.composited == 1
    # 숫자/기호/공백은 모드 중립 — 상태 유지.
    for ch in ("5", " ", ".", "@"):
        PLUGIN.client_key(app, _Ev(ch))
    assert app.ime_state == "한" and app.composited == 1
    # ASCII 글자 → 'EN' 전환.
    PLUGIN.client_key(app, _Ev("b"))
    assert app.ime_state == "EN" and app.composited == 2
    # 호환자모(조합 낱자)도 한글로 인식.
    PLUGIN.client_key(app, _Ev("ㅁ"))
    assert app.ime_state == "한"
    # 비인쇄/문자 없음(방향키·Ctrl 등)은 무시.
    PLUGIN.client_key(app, _Ev(None))
    PLUGIN.client_key(app, _Ev("\x1b"))
    assert app.ime_state == "한"


async def test_client_key_no_composite_when_hidden():
    # 배지가 꺼져 있으면 상태는 추적하되 재합성은 하지 않는다(불필요한 프레임 방지).
    app = _FakeApp()
    app.ime_show = False
    app.ime_state = "EN"
    PLUGIN.client_key(app, _Ev("가"))
    assert app.ime_state == "한" and app.composited == 0


# ---- 3) 명령 토글 ----
async def test_toggle_command():
    app = _FakeApp()
    assert PLUGIN.handle_command(app, "ime-indicator", []) is True
    assert app.ime_show is False and app.composited == 1
    assert app.messages and "OFF" in app.messages[-1]
    assert PLUGIN.handle_command(app, "ime", []) is True   # 별칭
    assert app.ime_show is True
    assert "ON" in app.messages[-1]
    # 모르는 명령은 처리 안 함.
    assert PLUGIN.handle_command(app, "clock-mode", []) is False


# ---- 4) 계약(delete-to-disable) ----
async def test_plugin_discovered_when_loaded():
    reg = plugins.load()
    names = {n for (n, *_rest) in reg.commands}
    assert "ime-indicator" in names, "ime-indicator 플러그인이 로드되지 않음(전제 실패)"


async def test_registry_without_ime_has_no_commands_and_noop_hook():
    found = [p for p in plugins._discover()
             if getattr(p, "name", "") != "ime-indicator"]
    reg = plugins.Registry(found)
    names = {n for (n, *_rest) in reg.commands}
    assert "ime-indicator" not in names
    assert "ime" not in reg.noarg and "ime-indicator" not in reg.noarg
    # client_key 훅이 부재 시 no-op(예외 없음, app=None 도 안전).
    reg.client_key(None, _Ev("가"))


# ---- 5) 코어 on_key 배선(라이브) ----
async def test_core_on_key_updates_ime_state():
    """코어 normal-mode 입력이 plugins.client_key 를 호출해 상태가 갱신되는지."""
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.4)
            assert app.ime_show is True and app.ime_state == "EN"  # 기본
            app.mode = "normal"
            # 코어 on_key(normal) 가 plugins.client_key 를 부르는지 — 핸들러 직접 호출
            # (Textual 의 _on_key 디스패치는 프레임워크 영역이라 핸들러만 가드한다).
            app.on_key(Key("가", "가"))
            await pilot.pause(0.05)
            assert app.ime_state == "한"
            app.on_key(Key("b", "b"))
            await pilot.pause(0.05)
            assert app.ime_state == "EN"
            # 숫자는 모드 중립 — 'EN' 유지(여기선 변화 없음).
            app.on_key(Key("5", "5"))
            assert app.ime_state == "EN"
            # 배지가 콘텐츠 프레임에 그려졌는지(우상단 [EN]).
            row0 = "".join(c[0] for c in app.view._cells[0])
            assert "[EN]" in row0, row0
    finally:
        await teardown(srv, task, sock)
