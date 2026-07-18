"""claude-prompt-history 플러그인 테스트 — 서버 추적/status/스크롤백 점프, 미리보기
렌더, 라이브 팝업 플로, delete-to-disable.

하이픈 패키지라 importlib.import_module 로 가져온다(다른 플러그인 테스트와 동일)."""
import importlib

import harness  # noqa: F401  (sys.path 주입)
from rich.style import Style

import pytmuxlib.plugins as plugins

_PKG = "pytmuxlib.plugins.claude-prompt-history"
server = importlib.import_module(_PKG + ".server")
render = importlib.import_module(_PKG + ".render")

_CMDS = {"prompt-history", "prompts", "ph", "prompt-history-lines", "ph-lines"}


# --------------------------------------------------------------------------- #
# server.py — 추적/status/점프 (실제 Pane)
# --------------------------------------------------------------------------- #
def _claude_pane():
    from pytmuxlib.model import Pane
    p = Pane(-1, -1, 40, 6)
    p._claude = "idle"
    p._ph_history = []
    p._ph_inbuf = ""
    return p


async def test_track_input_records_and_dedups():
    p = _claude_pane()
    server.track_input(p, b"first prompt\r")
    server.track_input(p, b"first prompt\r")           # 연속 중복 → 무시
    server.track_input(p, b"multi line one\nline two\r")  # \n=줄바꿈 누적(한 항목)
    server.track_input(p, b"part")                     # 미제출(Enter 없음)
    assert p._ph_history == ["first prompt", "multi line one\nline two"]
    server.track_input(p, b"ial\r")                    # 이어서 제출 → 'partial'
    assert p._ph_history[-1] == "partial"


async def test_track_input_skips_escape_sequences():
    p = _claude_pane()
    # 화살표키(CSI) 가 섞여도 본문만 누적.
    server.track_input(p, b"ab\x1b[Dcd\r")
    assert p._ph_history == ["abcd"]


async def test_track_input_gated_on_claude():
    p = _claude_pane()
    p._claude = None                                   # Claude 아님 → 기록 안 함
    server.track_input(p, b"ignored\r")
    assert p._ph_history == []


async def test_status_fields_debounce():
    p = _claude_pane()
    p._ph_history = ["a", "b"]

    class Srv:
        _ph_max_lines = 2

    class Win:
        def panes(self):
            return [p]

    srv, win = Srv(), Win()
    msg = {}
    server.status_fields(srv, win, msg, False)          # 첫 전송: 실림
    assert msg["ph_max_lines"] == 2
    assert msg["ph_panes"] == [{"id": p.id, "h": ["a", "b"]}]
    msg2 = {}
    server.status_fields(srv, win, msg2, False)          # 변화 없음 → h 생략
    assert msg2["ph_panes"] == [{"id": p.id}]
    p._ph_history.append("c")
    msg3 = {}
    server.status_fields(srv, win, msg3, False)          # 변함 → 다시 실림
    assert msg3["ph_panes"] == [{"id": p.id, "h": ["a", "b", "c"]}]
    msg4 = {}
    server.status_fields(srv, win, msg4, True)           # full → 항상 실림
    assert msg4["ph_panes"] == [{"id": p.id, "h": ["a", "b", "c"]}]


async def test_scroll_to_prompt_finds_and_guards():
    p = _claude_pane()
    for i in range(10):
        p.feed(f"before{i:02d}\r\n".encode())
    p.feed(b"> jump target line\r\n")
    p._ph_history = ["jump target line"]
    for i in range(30):
        p.feed(f"after{i:02d}\r\n".encode())

    class Sess:
        class active_window:
            active_pane = p

    assert p.scroll == 0
    assert server.scroll_to_prompt(None, Sess(), 0) is True
    assert p.scroll > 0
    rows, _ = p.render(False)
    top = "".join(s[0] for s in rows[0])
    assert "jump target line" in top, top
    # 범위 밖 인덱스 → False
    assert server.scroll_to_prompt(None, Sess(), 9) is False
    # 스크롤백에 없는 텍스트 → False
    p._ph_history = ["not in scrollback at all"]
    assert server.scroll_to_prompt(None, Sess(), 0) is False


# --------------------------------------------------------------------------- #
# render.py — 미리보기 패널(셀 그리드 직접 검증)
# --------------------------------------------------------------------------- #
class _FakeApp:
    def __init__(self, pid, hist, max_lines, target):
        self.layout = {"panes": [{"id": pid, "x": 0, "y": 0, "w": 30, "h": 6}],
                       "active": pid}
        self.ph_panes = {pid: {"id": pid, "h": hist}}
        self.ph_max_lines = max_lines
        self._cmd_target_pane = target
        self._theme = None


def _grid(w, h):
    base = Style()
    return [[(" ", base) for _ in range(w)] for _ in range(h)]


def _text(cells):
    return "".join("".join(c[0] for c in row) for row in cells)


async def test_preview_draws_only_when_target_set():
    cells = _grid(30, 6)
    # 대상 없음(_cmd_target_pane=None) → no-op.
    render.draw_preview(_FakeApp(1, ["hi there"], 3, None), cells, 30, 6)
    assert all(c[0] == " " for row in cells for c in row)
    # 대상 설정 → 직전 프롬프트가 1행에 그려진다.
    render.draw_preview(_FakeApp(1, ["hi there"], 3, 1), cells, 30, 6)
    assert "hi there" in _text(cells)
    assert "▷" in _text(cells)


async def test_preview_multiline_expands_capped():
    cells = _grid(30, 6)
    prompt = "L1\nL2\nL3\nL4\nL5"
    render.draw_preview(_FakeApp(1, [prompt], 3, 1), cells, 30, 6)
    t = _text(cells)
    assert "L1" in t and "L2" in t and "L3" in t      # 최대 3행
    assert "L4" not in t and "L5" not in t            # 3행 초과는 안 보임
    assert "▾" in t                                   # 더 있음 표시


async def test_preview_single_line_one_row():
    cells = _grid(30, 6)
    render.draw_preview(_FakeApp(1, ["just one"], 3, 1), cells, 30, 6)
    # 1행만 칠해지고(바 스타일) 2행은 비어 있음.
    row1_painted = any(c[0] != " " for c in cells[1])
    assert not row1_painted, "단일행 프롬프트가 2행을 침범"


async def test_preview_undims_rows_and_bright_text():
    """미리보기 바는 명령 프롬프트(ModalScreen) backdrop-dim 에 흰 글자가 회색으로
    뭉개져 배경과 대비가 무너지던 것(제보: 텍스트가 배경색과 비슷해 안 읽힘)을
    막기 위해, 그린 행을 app._undim_rows 에 실어 딤에서 제외하고 순백(#FFFFFF) 볼드로
    그린다. 대상 해제 시엔 등록분을 회수해 stale 유령 밝은 줄을 남기지 않는다."""
    cells = _grid(30, 6)
    app = _FakeApp(1, ["a\nb"], 3, 1)      # 2행 미리보기(행 0·1)
    render.draw_preview(app, cells, 30, 6)
    # ① 그린 두 행이 딤 제외 집합에 실린다.
    assert app._undim_rows == {0, 1}, app._undim_rows
    # ② 글자 스타일이 순백 볼드(옅은 회색 ANSI "white" 아님).
    st = next(c[1] for c in cells[0] if c[0] not in (" ", ""))
    assert st.bold and tuple(st.color.get_truecolor()) == (255, 255, 255), st
    # ③ 대상 해제 후 재합성 → 우리 undim 행 회수(다른 팝업 딤에 유령 밝은 줄 방지).
    app._cmd_target_pane = None
    render.draw_preview(app, cells, 30, 6)
    assert not app._undim_rows, app._undim_rows


async def test_preview_undim_preserves_other_owners_rows():
    """딤 제외 회수는 **내 기여분(_ph_undim_rows)** 만 뺀다 — perm-mode 등 다른 소유자가
    실어둔 행은 그대로 둔다(공유 집합 오염 방지)."""
    cells = _grid(30, 6)
    app = _FakeApp(1, ["x"], 3, 1)
    app._undim_rows = {5}                  # 다른 소유자(예: perm-mode footer)
    render.draw_preview(app, cells, 30, 6)
    assert 5 in app._undim_rows and 0 in app._undim_rows
    app._cmd_target_pane = None
    render.draw_preview(app, cells, 30, 6)
    assert app._undim_rows == {5}, app._undim_rows   # 남의 행은 보존


async def test_popup_multiline_expands_into_rows_capped():
    """항목3(2026-06-22): 팝업 _HistView 가 멀티라인 프롬프트를 **여러 표시 행**으로
    펼친다(프롬프트당 최대 max_lines 줄, 초과 시 마지막 줄 ellipsis). 선택 단위는
    프롬프트라 _sel 은 프롬프트 idx 이고 _sel_rows 가 그 블록의 표시 행 범위를 준다.
    (size 를 만지는 _clamp_view 는 호출하지 않고 행 모델만 검증.)"""
    screen = importlib.import_module(_PKG + ".screen")
    hist = ["one", "two\nliner\nthree", "tail"]
    v = screen._HistView(hist, max_lines=2)
    # 프롬프트0=1행, 프롬프트1=2행(3줄→capped), 프롬프트2=1행 → 총 4 표시행.
    assert v._prow == {0: 0, 1: 1, 2: 3}, v._prow
    assert len(v._rows) == 4, v._rows
    assert v._rows[1] == (1, 0, False), v._rows
    assert v._rows[2] == (1, 1, True), v._rows        # 둘째 표시행에 ellipsis 표식
    # 선택은 최신 프롬프트(idx 2), 그 블록은 표시행 3 하나.
    assert v._sel == 2 and v._sel_rows() == (3, 3)
    # 상한을 늘린 새 뷰: 3줄 다 펼쳐지고 ellipsis 없음.
    v3 = screen._HistView(hist, max_lines=3)
    assert v3._prow == {0: 0, 1: 1, 2: 4}, v3._prow
    assert v3._rows[3] == (1, 2, False), v3._rows
    assert v3._sel_rows() == (4, 4)


# --------------------------------------------------------------------------- #
# delete-to-disable 계약
# --------------------------------------------------------------------------- #
def _registry_without():
    found = plugins._discover()
    return plugins.Registry([p for p in found
                             if getattr(p, "name", "") != "claude-prompt-history"])


async def test_contract_present_when_loaded():
    names = {n for (n, *_rest) in plugins.load().commands}
    assert "prompt-history" in names


async def test_contract_gone_without_plugin():
    reg = _registry_without()
    names = {n for (n, *_rest) in reg.commands}
    assert not (_CMDS & names), f"명령 누수: {_CMDS & names}"
    assert "prompt-history" not in reg.pane_scoped
    assert reg.handle_command(None, "prompt-history", []) is False
    assert reg.server_command(None, None, None, "ph_scroll_to", {}) is None
    # 이 플러그인의 client_status 흡수가 사라져도 코어 무에러(ph 키는 무시).
    assert reg.handle_message(None, {"t": "ph_scroll_to"}) is False


# --------------------------------------------------------------------------- #
# 라이브 통합 — 실제 Textual 클라이언트
# --------------------------------------------------------------------------- #
async def test_live_popup_and_preview():
    from harness import make_app, server_only, teardown

    # 알려진 _PLUGIN_SERVER_MIXINS poison 가드: test_plugin_contract 가 plugins.load 를
    # claude 제외로 바꾼 채 server.py 를 처음 import 하면 Server 가 claude-code 믹스인
    # (_init_token_state 등) 없이 동결돼, 그 뒤 server_only 가 깨진다(기존 함정 —
    # 메모리 token-modularization-s5). 전체 스위트에선 앞 모듈이 먼저 정상 import 하므로
    # 통과한다. 부분 실행에서 poison 됐으면 이 라이브 검증만 건너뛴다(나머지 테스트가 커버).
    import pytmuxlib.server as _srv
    if not hasattr(_srv.Server, "_init_token_state"):
        return

    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.4)
            pid = app.layout.get("active")
            # ① status 흡수 — ph_panes/ph_max_lines.
            app._dispatch({"t": "status", "windows": [],
                           "ph_max_lines": 2,
                           "ph_panes": [{"id": pid,
                                         "h": ["첫 프롬프트", "둘째\n둘째줄2", "셋째"]}]})
            await pilot.pause(0.05)
            assert app.ph_panes.get(pid, {}).get("h"), "ph_panes 흡수 실패"
            assert app.ph_max_lines == 2

            # ② 미리보기 — _cmd_target_pane 설정 후 합성하면 직전 프롬프트가 그려진다.
            app._cmd_target_pane = pid
            app._composite()
            await pilot.pause(0.05)
            txt = "".join("".join(c[0] for c in row) for row in app.view._cells)
            assert "셋째" in txt, "미리보기 패널에 직전 프롬프트 없음"
            app._cmd_target_pane = None
            app._composite()

            # ③ 팝업 — prompt-history 명령 → PromptHistoryScreen.
            sent = []
            app.send_cmd = lambda action, **kw: sent.append((action, kw))
            app._run_command("prompt-history")
            await pilot.pause(0.15)
            top = app.screen_stack[-1]
            assert top.__class__.__name__ == "PromptHistoryScreen", top.__class__.__name__
            view = top._view
            assert view._sel == 2, view._sel               # 최신 선택
            # ④ ↑ 로 이전 프롬프트.
            await pilot.press("up")
            await pilot.pause(0.05)
            assert view._sel == 1
            # ⑤ +/− 로 미리보기 행수 → set_ph_max_lines 전송.
            await pilot.press("plus")
            await pilot.pause(0.05)
            assert ("set_ph_max_lines", {"n": 3}) in sent, sent
            # ⑥ Enter → ph_scroll_to(현재 인덱스) 전송 + 팝업 닫힘.
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert ("ph_scroll_to", {"index": 1}) in sent, sent
            assert app.screen_stack[-1].__class__.__name__ != "PromptHistoryScreen"

            # ⑦ 다시 열어 Esc 로 닫기.
            app._run_command("prompt-history")
            await pilot.pause(0.1)
            assert app.screen_stack[-1].__class__.__name__ == "PromptHistoryScreen"
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert app.screen_stack[-1].__class__.__name__ != "PromptHistoryScreen"
    finally:
        await teardown(srv, task, sock)
