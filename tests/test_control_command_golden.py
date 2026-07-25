"""제어 라인(`pytmux cmd …`) 명령 집합 전수 고정 — servercmd 의 disposition 골든과 짝.

**왜 필요한가**(조사 2026-07-25, HANDOFF §10-4 부수발견의 결론): 서버에 명령 경로가
둘이다 — `handle_control`(제어 라인·driver `control()`)과 `_handle_cmd`(cmd 액션·실
클라이언트). 조사해 보니 **로직 중복은 없다**(control 은 CLI 문법 파싱만 하고 트리
조작은 `_CMD_TABLE` 핸들러와 **같은 Server 메서드**에 위임한다). 진짜 위험은 중복이
아니라 **커버리지 드리프트**다: 한쪽에만 명령이 생기거나 사라져도 아무도 모른다.
`_CMD_TABLE` 쪽은 이미 골든이 전수 고정하는데(`test_command_table_disposition_golden`)
control 쪽은 아무 고정도 없었다 — 그 비대칭을 메운다.

되돌리면 실패해야 하는 오라클:
  · 명령 철자를 지우거나 새로 추가하면 → test_control_command_spellings_golden 실패
    (의도한 변경이면 이 목록을 같은 CL 에서 함께 고친다 — 그게 이 골든의 목적이다)
"""
import ast

import harness  # noqa: F401
from pytmuxlib import server as server_mod

# `handle_control` 이 문자열로 비교하는 명령 철자 전수(별칭 포함) + on/off 표 키.
# 별칭이 많은 것은 tmux 호환(neww/new-window/new-tab/newt …) 때문이다.
GOLDEN_CONTROL = {
    "full-restart", "kill-server", "kill-session", "kill-tab", "kill-window",
    "kills", "killt", "killw", "layout-load", "layout-save", "load-tab-layout",
    "move-tab-first", "move-tab-last", "move-tab-left", "move-tab-right",
    "new", "new-session", "new-tab", "new-window", "newt", "neww",
    "pane-border", "rename-tab", "rename-window", "renamet", "renamew",
    "restart", "restart-all", "restart-client-server", "restart-server",
    "save-tab-layout", "select-tab", "select-window", "selectt", "selectw",
    "send", "send-keys", "single-border", "split-window", "splitw",
    "vt-parser", "win-mouse-motion", "window-size", "winsize",
}
GOLDEN_ONOFF = {
    "coalesce", "coalesce-repaints", "exit-empty", "nest-attach",
    "nest-auto-attach",
}


def _control_spellings():
    """`handle_control` 소스에서 `c` 와 비교되는 문자열 리터럴을 AST 로 뽑는다.

    소스 파싱인 이유: 이 함수는 if/elif 체인이라 런타임에 열거할 표가 없다
    (`_CMD_TABLE` 과 대칭이 안 되는 지점이고, 그래서 골든이 필요하다)."""
    import inspect
    tree = ast.parse(inspect.getsource(server_mod))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "handle_control")
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) \
                and node.left.id == "c":
            for cmp_ in node.comparators:
                if isinstance(cmp_, ast.Constant) and isinstance(cmp_.value, str):
                    out.add(cmp_.value)
                elif isinstance(cmp_, (ast.Tuple, ast.List, ast.Set)):
                    for e in cmp_.elts:
                        if isinstance(e, ast.Constant) and isinstance(e.value, str):
                            out.add(e.value)
    return out


async def test_control_command_spellings_golden():
    """제어 명령 철자 전수 고정 — 조용한 증감을 막는다."""
    got = _control_spellings()
    assert got == GOLDEN_CONTROL, (
        "제어 명령 집합이 바뀌었다. 의도한 변경이면 GOLDEN_CONTROL 을 같은 CL 에서 "
        "함께 고쳐라.\n  추가: %s\n  제거: %s"
        % (sorted(got - GOLDEN_CONTROL), sorted(GOLDEN_CONTROL - got)))


async def test_onoff_control_table_golden():
    """on/off 토글 표도 함께 고정(핸들러가 setter 이름 문자열이라 오타가 조용하다)."""
    table = server_mod.Server._ONOFF_CONTROLS
    assert set(table) == GOLDEN_ONOFF
    for cmd, setter in table.items():
        assert hasattr(server_mod.Server, setter), (cmd, setter)


async def test_control_delegates_to_shared_server_methods():
    """control 은 **자기 트리 조작을 갖지 않는다** — cmd 경로와 같은 Server 메서드에
    위임한다(조사 결론의 회귀화: 여기에 트리 변형 로직이 새로 생기면 두 경로가
    갈라지기 시작한 것이다)."""
    import inspect
    tree = ast.parse(inspect.getsource(server_mod))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "handle_control")
    called = {n.func.attr for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    # 트리를 바꾸는 일은 전부 이 공유 메서드들로만 나간다.
    for shared in ("new_window", "split_pane", "kill_window", "select_window",
                   "rename_window", "move_current_tab", "kill_session"):
        assert shared in called, shared
    # 패널/윈도우 객체를 직접 만들거나 리스트를 직접 건드리는 흔적이 없어야 한다.
    src = inspect.getsource(server_mod).split("def handle_control")[1]
    src = src.split("\n    @staticmethod")[0]
    for banned in ("Pane(", "Window(", ".panes.append(", ".tabs.append("):
        assert banned not in src, banned
