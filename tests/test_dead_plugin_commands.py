"""팔레트에 보이는데 안 먹던 플러그인 명령들 — 이제 전부 산다(pytmux-35).

# 무엇을 재나

제보의 부류는 하나다: 네이티브 클라는 플러그인 명령을 **전부** `plugin_open`("화면을
다오")으로 보내는데, 화면이 아니라 **상태를 바꾸는** 명령에는 그 길이 통째로 틀려
서버가 *"이 플러그인은 화면 스펙을 제공하지 않습니다"* 로 거절했다 — 사용자에게는
죽은 줄이다. 스물셋에서 시작해 이 CL 에서 0 이 됐다.

살아난 길은 둘뿐이고 **판정도 그 둘로** 한다(러스트 래칫
`the_dead_command_list_does_not_grow` 와 같은 규칙이다 — 다만 저쪽은 픽스처를 보고
여기는 정본을 직접 부른다. 정본이 먼저 울어야 픽스처가 낡은 이유를 알 수 있다):

1. `plugin_command_action` 이 이름을 **액션과 인자로 옮기고**, 그 액션을 서버가 받는다.
2. `plugin_screen` 이 **화면 스펙**을 낸다.

⚠ 이 파일은 "표가 있나"가 아니라 **"눌렀을 때 무엇이 실제로 달라지나"** 를 잰다.
값을 만드는 표만 재면 그 표를 부르는 줄을 지워도 통과한다(이 저장소가 두 번 밟은
공허 통과 — 그래서 아래 오라클들은 서버 상태·패널 바이트를 본다).
"""
import importlib
from unittest.mock import MagicMock

from harness import running_server
from pytmuxlib import plugins
from pytmuxlib.servercmd import _CMD_TABLE

#: 이 CL 이 살린 열하나(이슈 본문의 그 목록 그대로).
REVIVED = (
    "auto-launch", "capture-output", "capture-toggle", "claude-rules",
    "claude-settings", "claude-token-log", "ime-indicator", "model",
    "namesync", "prompt-clear-queue", "prompt-history-lines",
)


def _plugin(name):
    return importlib.import_module(f"pytmuxlib.plugins.{name}").PLUGIN


def _runnable(reg, name, args=()):
    """서버가 이 이름을 **명령으로 실행할 수 있나** — 생성기와 같은 판정.

    차례가 규칙이다: **코어 표를 먼저** 본다. 플러그인 훅에만 물으면 코어가 받는
    액션이 없는 것으로 보인다(`set_claude_account` 가 그렇다)."""
    got = reg.plugin_command_action(name, list(args))
    if got is None:
        return False
    action, _kw = got
    if action in _CMD_TABLE:
        return True
    try:
        disp = reg.server_command(MagicMock(), MagicMock(), MagicMock(), action, {})
    except Exception:
        return True          # 잡혔다(인형에서 넘어졌을 뿐 — 디스패치는 됐다)
    return disp is not None


def _has_screen(reg, name):
    resp = reg.plugin_screen(MagicMock(), MagicMock(),
                             {"do": "open", "name": name, "args": [], "state": {}})
    if hasattr(resp, "close"):
        resp.close()         # 안 기다릴 코루틴은 닫는다(경고를 남기지 않게)
        return True
    return isinstance(resp, dict)


# ── 전수: 열하나가 전부 둘 중 한 길로 산다 ──────────────────────────────────
async def test_the_eleven_dead_commands_are_all_alive():
    reg = plugins.load()
    dead = [n for n in REVIVED
            if not _runnable(reg, n) and not _has_screen(reg, n)]
    assert dead == [], (
        f"아직 죽은 줄: {dead} — 이름을 액션으로 옮기거나(cmdmap) 화면 스펙을 낼 것")


async def test_every_advertised_command_is_advertised_by_someone_alive():
    """오라클이 **헛돌지 않는가**: 광고 목록이 비면 위 단언은 무엇을 해도 통과한다."""
    reg = plugins.load()
    advertised = {row[0] for p in reg.plugins
                  for row in (getattr(p, "commands", None) or [])}
    assert len(advertised) >= 30, f"광고 목록이 너무 적다({len(advertised)})"
    assert set(REVIVED) <= advertised, "살렸다는 이름이 광고에 없다 — 목록이 낡았다"


# ── cmdmap: 이름 → 액션·인자 ────────────────────────────────────────────────
async def test_rec_maps_capture_names_to_the_action_the_server_takes():
    assert _plugin("rec").plugin_command_action("capture-output", []) == \
        ("set_capture", {"value": None})            # 무인자 = 서버가 토글
    assert _plugin("rec").plugin_command_action("capture-toggle", ["off"]) == \
        ("set_capture", {"value": False})
    assert _plugin("rec").plugin_command_action("clock-mode", []) is None


async def test_the_canon_client_uses_the_same_table_as_the_server():
    """정본이 표를 거치지 않고 자기 규칙을 갖고 있으면 **두 표가 갈린다** — 갈리는
    순간 명령은 조용히 아무 일도 안 한다(이 결함이 생긴 경위 그대로)."""
    from pytmuxlib.plugins.rec import clientside
    app = MagicMock()
    assert clientside.handle_command(app, "capture-output", ["on"]) is True
    app.send_cmd.assert_called_once_with("set_capture", value=True)
    assert clientside.handle_command(app, "clock-mode", []) is False


async def test_prompt_history_lines_maps_and_cycles_when_bare():
    ph = _plugin("claude-prompt-history")
    assert ph.plugin_command_action("prompt-history-lines", ["2"]) == \
        ("set_ph_max_lines", {"n": 2})
    assert ph.plugin_command_action("ph-lines", []) == \
        ("set_ph_max_lines", {"n": None})           # 무인자 = 순환


async def test_auto_launch_was_dead_in_canon_too_and_now_maps():
    """`auto-launch` 는 팔레트·선택지 팝업·CLI 토글표에 다 있었는데 `handle_command`
    사슬에만 없어 **정본에서도** 아무 일이 안 났다. 전수로 재는 자가 없으면 이런
    구멍은 안 보인다."""
    cc = _plugin("claude-code")
    assert cc.plugin_command_action("auto-launch", ["on"]) == \
        ("set_claude_auto_launch", {"value": True})
    assert cc.plugin_command_action("claude-auto-launch", []) == \
        ("set_claude_auto_launch", {"value": None})


async def test_prompt_clear_queue_branches_on_its_argument():
    """한 이름이 **화면이기도 하고 액션이기도** 하다. 무인자면 `None`(→ 화면 경로)."""
    cc = _plugin("claude-code")
    assert cc.plugin_command_action("prompt-clear-queue", []) is None
    assert cc.plugin_command_action("prompt-clear-queue", ["-c"]) == \
        ("pc_queue_clear", {})
    assert cc.plugin_command_action("pc-queue", ["make", "test"]) == \
        ("pc_queue_add", {"cmd": "make test"})


# ── 서버가 그 액션을 실제로 처리하나(표만 있고 배선이 없으면 더 조용히 죽는다) ──
async def test_the_server_actually_runs_the_revived_actions():
    async with running_server() as (srv, _task, _sock):
        sess = srv.ensure_default_session(80, 24)
        client = MagicMock()

        # auto-launch — 종전에는 외부 CLI 만 이 셋터에 닿을 수 있었다.
        before = srv.claude_auto_launch
        assert srv.plugins.server_command(
            srv, client, sess, "set_claude_auto_launch", {"value": None}) == "send_full"
        assert srv.claude_auto_launch is not before

        # ime-indicator — 표시 여부가 서버 옵션이라야 두 클라가 같은 상태를 본다.
        assert srv.plugins.server_command(
            srv, client, sess, "set_ime_indicator", {"value": False}) == "broadcast"
        assert srv.ime_show is False

        # prompt-history-lines — 무인자는 순환한다(1→2→3→1).
        srv._ph_max_lines = 3
        srv.plugins.server_command(srv, client, sess, "set_ph_max_lines", {"n": None})
        assert srv._ph_max_lines == 1
        srv.plugins.server_command(srv, client, sess, "set_ph_max_lines", {"n": 2})
        assert srv._ph_max_lines == 2


# ── 화면 스펙: 무엇을 그릴지가 자료로 나오나 ────────────────────────────────
async def test_the_four_popups_become_screen_specs():
    async with running_server() as (srv, _task, _sock):
        sess = srv.ensure_default_session(80, 24)
        spec_mod = importlib.import_module(
            "pytmuxlib.plugins.claude-code.screenspec")
        for name, kind in (("claude-settings", "form"),
                           ("claude-rules", "prompt"),
                           ("model", "list"),
                           ("claude-token-log", "table"),
                           ("prompt-clear-queue", "list")):
            spec = spec_mod.open_spec(srv, sess, name)
            assert isinstance(spec, dict), f"{name}: 스펙이 안 나왔다"
            assert spec["kind"] == kind, f"{name}: {spec['kind']}"
            assert spec["title"], f"{name}: 제목이 비었다"
        # 내 이름이 아니면 안 집는다 — 다른 플러그인의 화면을 가로채면 안 된다.
        assert spec_mod.open_spec(srv, sess, "mdir") is None


async def test_the_settings_form_shows_the_live_values_and_enter_changes_them():
    async with running_server() as (srv, _task, _sock):
        sess = srv.ensure_default_session(80, 24)
        spec_mod = importlib.import_module(
            "pytmuxlib.plugins.claude-code.screenspec")
        spec = spec_mod.open_spec(srv, sess, "claude-settings")
        rows = {r["key"]: r["cols"][0] for r in spec["rows"]}
        assert "claude_auto_mode" in rows, rows
        before = srv.claude_auto_mode
        nxt = spec_mod.action(srv, sess, {"id": "claude-settings", "do": "toggle",
                                          "row": 0, "input": "claude_auto_mode"})
        assert srv.claude_auto_mode is not before, "Enter 가 값을 안 바꿨다"
        # 그리고 **바뀐 값이 곧바로 보인다**(다음 스펙이 돌아온다).
        after = {r["key"]: r["cols"][0] for r in nxt["rows"]}
        assert after["claude_auto_mode"] != rows["claude_auto_mode"]


async def test_the_rules_prompt_carries_the_current_text_as_the_seed():
    """고치는 화면인데 지금 값이 안 실리면 '편집'이 아니라 '덮어쓰기'다."""
    async with running_server() as (srv, _task, _sock):
        sess = srv.ensure_default_session(80, 24)
        spec_mod = importlib.import_module(
            "pytmuxlib.plugins.claude-code.screenspec")
        srv.set_claude_rules("한국어로 답할 것")
        spec = spec_mod.open_spec(srv, sess, "claude-rules")
        assert spec["text"] == "한국어로 답할 것", spec
        out = spec_mod.action(srv, sess, {"id": "claude-rules", "do": "save",
                                          "row": 0, "input": "새 규칙"})
        assert srv.claude_rules == "새 규칙"
        assert out["t"] == "plugin_screen_close"


class _FakePty:
    """패널의 pty — 받은 바이트만 들고 있는다.

    ☠ **`pane.write = …` 로 달지 마라**(pytmux-173). 여기 `pane` 은 **진짜**
    `pytmuxlib.model.Pane` 이고 거기에는 `write` 가 **없다** — 그런데 이 시험이 그
    이름을 **만들어 붙여서** `/model` 적용이 라이브에서 늘 `AttributeError` 로 죽는
    동안에도 늘 초록이었다. 가짜는 프로덕션에 **있는** 이름에만 단다.
    """

    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)


async def test_choosing_a_model_types_it_into_the_active_pane():
    """정본 `_apply_model_config` 와 **같은 결과** — 그 패널을 들고 있는 것이 서버다."""
    async with running_server() as (srv, _task, _sock):
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        pane.pty = _FakePty()
        written = pane.pty.written
        spec_mod = importlib.import_module(
            "pytmuxlib.plugins.claude-code.screenspec")
        spec = spec_mod.open_spec(srv, sess, "model")
        assert any(r["key"] == "opus" for r in spec["rows"]), spec["rows"][:3]
        out = spec_mod.action(srv, sess, {"id": "model", "do": "apply",
                                          "row": 0, "input": "opus 1m"})
        assert written == [b"/model opus 1m\r"], written
        assert out["t"] == "plugin_screen_close"


async def test_applying_a_model_without_a_pty_says_no_instead_of_dying():
    """`pane.pty` 는 `None` 일 수 있다 — 그때 `_model_apply` 는 **`False`** 라야 한다.
    터지면 `plugin_screen_close` 가 안 나가고, 조용히 `True` 면 부르는 쪽이 「먹었다」로
    읽는다. 둘 다 사용자에게는 「골랐는데 아무 일도 안 남」이다(pytmux-173)."""
    async with running_server() as (srv, _task, _sock):
        sess = srv.ensure_default_session(80, 24)
        sess.active_window.active_pane.pty = None
        spec_mod = importlib.import_module(
            "pytmuxlib.plugins.claude-code.screenspec")
        assert spec_mod._model_apply(srv, sess, "opus 1m") is False


async def test_the_queue_screen_lists_what_is_queued_and_c_clears_it():
    async with running_server() as (srv, _task, _sock):
        sess = srv.ensure_default_session(80, 24)
        spec_mod = importlib.import_module(
            "pytmuxlib.plugins.claude-code.screenspec")
        pane = sess.active_window.active_pane
        pane.prompt_clear_queue.extend(["첫 명령", "둘째 명령"])
        spec = spec_mod.open_spec(srv, sess, "prompt-clear-queue")
        assert [r["label"] for r in spec["rows"]] == ["1. 첫 명령", "2. 둘째 명령"]
        assert not spec["note"], "쌓인 것이 있는데 '비었다' 안내가 붙었다"
        nxt = spec_mod.action(srv, sess, {"id": "pc-queue", "do": "clear", "row": 0})
        assert list(pane.prompt_clear_queue) == []
        assert nxt["rows"] == [] and nxt["note"], "빈 목록과 실패는 다르다"


# ── namesync — 목록·더하기·고치기·지우기 ──────────────────────────────────
async def test_namesync_rules_can_be_read_added_edited_and_deleted():
    async with running_server() as (srv, _task, _sock):
        sess = srv.ensure_default_session(80, 24)
        spec_mod = importlib.import_module(
            "pytmuxlib.plugins.claude-name-sync.screenspec")
        state = {}

        def act(**req):
            return spec_mod.action(srv, sess, dict(req, state=state))

        srv._namesync_rules = []
        spec = spec_mod.open_spec(srv, sess, "namesync")
        assert spec["kind"] == "table" and spec["rows"] == [] and spec["note"]

        # 더하기는 두 걸음이다(값이 둘인데 물음은 글 하나다).
        ask = act(id="namesync", do="add", row=0)
        assert ask["kind"] == "prompt"
        ask2 = act(id="namesync", do="apply", row=0, input="/tmp/work")
        assert ask2["kind"] == "prompt"
        lst = act(id="namesync", do="apply", row=0, input="일감")
        assert [r["label"] for r in lst["rows"]] == ["일감"]
        assert srv._namesync_rules[0]["path"] == "/tmp/work"

        # 키워드 고치기 — 지금 값이 초기값으로 실린다.
        ask3 = act(id="namesync", do="kw", row=0, input="0")
        assert ask3["text"] == "일감"
        act(id="namesync", do="apply", row=0, input="딴일")
        assert srv._namesync_rules[0]["keyword"] == "딴일"

        # 지우기는 되돌릴 수 없어 **묻는다** — 무엇이 사라지는지와 함께.
        ask4 = act(id="namesync", do="del", row=0, input="0")
        assert ask4["kind"] == "confirm" and "딴일" in ask4["note"]
        gone = act(id="namesync", do="apply", row=0, input="yes")
        assert srv._namesync_rules == [] and gone["rows"] == []

        # 낡은 줄을 되돌려받아도 엉뚱한 것을 안 건드린다.
        assert act(id="namesync", do="del", row=9, input="9")["rows"] == []


# ── 무게: 스펙을 짓는 것은 서버다 ──────────────────────────────────────────
async def test_building_a_screen_spec_never_reaches_for_textual():
    """화면 스펙은 **서버 프로세스에서** 지어진다 — 그 경로가 Textual 을 읽으면 서버가
    통째로 무거워진다(이 저장소의 플러그인 무게 규칙).

    파일을 읽어 판정하는 이유: `sys.modules` 로 재면 같은 프로세스의 앞선 테스트가
    이미 Textual 을 들여놓아 **무엇을 해도 통과**한다(run.py 는 전 모듈을 한 프로세스에서
    돈다). 실제로 이 규칙은 한 번 깨져 있었다 — 모델 후보를 `screens.py` 의 클래스
    속성에서 읽고 있었다(pytmux-35)."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = []
    for rel in ("claude-code/screenspec.py", "claude-code/cmdmap.py",
                "claude-name-sync/screenspec.py", "rec/cmdmap.py",
                "claude-prompt-history/cmdmap.py", "ime-indicator/cmdmap.py"):
        src = open(os.path.join(root, "pytmuxlib", "plugins", rel),
                   encoding="utf-8").read()
        for line in src.splitlines():
            s = line.strip()
            if not (s.startswith("import ") or s.startswith("from ")):
                continue
            if "textual" in s or "rich" in s or ".screens" in s or "screen import" in s:
                bad.append(f"{rel}: {s}")
    assert not bad, f"서버가 읽는 모듈이 UI 를 끌어온다: {bad}"


# ── 배선이 빠지면 잡힌다(값을 만드는 표만 재면 호출을 지워도 통과한다) ────────
async def test_the_plugin_screen_hook_actually_routes_to_the_specs():
    """`plugin_screen` 이 `screenspec` 을 안 부르면 스펙이 아무리 옳아도 죽은 줄이다."""
    reg = plugins.load()
    for name in ("claude-settings", "claude-rules", "model", "claude-token-log",
                 "prompt-clear-queue", "namesync"):
        assert _has_screen(reg, name), f"{name}: 레지스트리를 통해 화면이 안 나온다"
