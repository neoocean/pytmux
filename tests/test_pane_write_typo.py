"""`pane.write` 는 없는 이름이다 — 그것을 부르는 자리도, **만들어 주는 가짜**도 막는다.

pytmux-173 의 알맹이는 오타 셋이 아니라 **그 오타가 반년을 살아남은 이유**다.
`pytmuxlib/model.py` 의 `Pane` 에는 `write` 가 없다(글자를 넣는 길은 `pane.pty.write`
하나다). 그런데 세 자리가 `pane.write(...)` 를 불렀고, 불릴 때마다 `AttributeError` 로
죽었다 — 서버는 데몬이라 stderr 이 `/dev/null` 이라 그 트레이스백은 `error.log` 에만
남았고, 사용자에게는 「눌렀는데 아무 일도 안 남」으로 보였다(라이브 로그 실측 2026-08-23).

☠ **시험은 그동안 초록이었다.** 가짜 패널들이 프로덕션에 **없는** 이름을 스스로 만들어
붙였기 때문이다(`pane.write = written.append` · `class _Pane: def write(...)`). 루트
CLAUDE.md 가 경고하는 *"값을 만드는 헬퍼만 테스트하면 공허 통과"* 의 실제 표본이라,
고치는 것으로 끝내지 않고 **같은 부류가 다시 생기는 것**을 여기서 잰다.

# 이 오라클이 «처음 돌 때» 실제로 잡은 것 (CL 73668 · 2026-08-24)

가짜는 **넷**이었다 — `tests/test_ncd_tree.py`(클래스 정의) ·
`tests/test_dead_plugin_commands.py`(대입) · `tests/test_plugin_screen.py` **둘**
(`patched(type(pane), write=…)` — 프로덕션 클래스 자체에 심는 꼴이라 제일 나쁘다).

⚠ **뒤의 둘은 좁힌 회차로는 안 보였다.** 이슈가 지목한 프로덕션 셋을 고치고 관련 모듈
넷을 돌렸을 때는 122 passed · 0 failed 였고, **전량(`python3 tests/run.py`)을 돌리고서야**
그 둘이 붉어졌다. ⇒ 이 부류를 고칠 때 좁힌 회차의 초록은 「끝났다」가 아니다.

☠ **CL 73668 의 설명 ⑶ 절은 낡았다** — 그 CL 을 제출하기 직전 설명을 갈아 끼우려던
`p4 change -i` 가 실패했는데(폼의 `Status:` 가 `new` 라 거절) 같은 줄의 `submit` 은 그대로
나갔고, 제출된 것의 설명은 고칠 권한이 없다(`p4 change -f` → permission). 파일 목록과
⑴⑵ 는 맞지만 ⑶ 의 숫자는 **위의 넷을 찾기 전** 판이다. 참값은 이 파일의 위 문단과
트래커 `pytmux/pytmux-173` 의 코멘트 #23353 이다.

세 가지를 잰다:
  ⑴ 전제 — 프로덕션 `Pane` 에 `write` 가 정말 없나.
  ⑵ 프로덕션 전수 — 받는 쪽 이름이 `pane` 으로 끝나는 `.write(` 가 있나.
  ⑶ 시험 전수 — 가짜가 `pane` 자리에 `write` 를 **만들어 붙이나**.

⛔ ⑴ 이 무너지면(=누군가 `Pane.write` 를 정말로 만들면) ⑵⑶ 은 뜻을 잃는다. 그래서 ⑴ 을
먼저 재고, 붉으면 「오타가 아니라 설계가 바뀐 것」이라고 말한다 — 그때 지울지 남길지는
사람이 정한다.
"""
import ast
import pathlib

import harness  # noqa: F401  (sys.path 주입)

from pytmuxlib.model import Pane

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# ⑶ 의 허용목록. **이름과 까닭을 함께 적는다** — 조용한 예외는 관문을 반쪽으로 만든다.
#
# `_FakePane.write` 는 바이트를 pty 로 흘리는 그 `write` 가 **아니다**: 줄을 화면에
# 그려 넣는 시험 헬퍼이고 프로덕션 코드에 넘겨지지 않는다(같은 낱말, 다른 일).
_ALLOWED_TEST_FAKES = {("tests/test_claude_prompt_blocks.py", "_FakePane")}


def _receiver_is_a_pane(node):
    """`X.write(...)` 의 `X` 가 패널인가 — 이름의 마지막 마디로 본다.

    `pane` · `active_pane` · `self.pane` · `win.active_pane` 을 다 잡고
    `pane.pty` 는 안 잡는다(마지막 마디가 `pty` 다). 그것이 정확히 이 시험이
    가르려는 선이다.
    """
    return ast.unparse(node).split(".")[-1].endswith("pane")


def _py_files(rel):
    for path in sorted((_ROOT / rel).rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), str(path))


async def test_the_production_pane_really_has_no_write():
    """⑴ 아래 둘의 전제. 이것이 붉으면 오타가 아니라 **설계가 바뀐 것**이다."""
    assert not hasattr(Pane, "write"), (
        "`Pane.write` 가 생겼다 — 그러면 아래 두 오라클이 재는 것이 사라진다. "
        "정말로 만든 것이면 이 파일을 지울지 좁힐지 사람이 정한다(pytmux-173)."
    )
    assert hasattr(Pane, "pty") or "pty" in Pane.__init__.__code__.co_names, (
        "패널이 pty 를 안 든다 — 글자를 넣는 길 자체가 바뀌었다"
    )


async def test_no_production_code_calls_write_on_a_pane():
    """⑵ pytmux-173 이 고친 자리 셋이 다시 생기면 여기서 운다."""
    bad = []
    for path, tree in _py_files("pytmuxlib"):
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "write"
                    and _receiver_is_a_pane(node.func.value)):
                rel = path.relative_to(_ROOT).as_posix()
                bad.append(f"{rel}:{node.lineno} {ast.unparse(node.func)}(...)")
    assert not bad, (
        "`Pane` 에 없는 `write` 를 부른다 — 불리는 순간 AttributeError 다. "
        "`pane.pty.write(...)` 로 쓰고 `pane.pty is None` 가드를 같이 둔다"
        "(정본: plugins/claude-resume/__init__.py §_resume). 걸린 것: " + " · ".join(bad)
    )


async def test_no_test_fake_invents_write_on_a_pane():
    """⑶ **고친 것보다 이쪽이 더 중요하다** — 가짜가 이름을 만들어 주면 ⑵ 를 고쳐 놔도
    다음 오타가 또 초록으로 지나간다. 가짜는 프로덕션에 **있는** 이름에만 단다."""
    bad = []
    for path, tree in _py_files("tests"):
        rel = path.relative_to(_ROOT).as_posix()
        for node in ast.walk(tree):
            # ① 대입으로 붙이는 꼴 — `pane.write = written.append`
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (isinstance(target, ast.Attribute)
                            and target.attr == "write"
                            and _receiver_is_a_pane(target.value)):
                        bad.append(f"{rel}:{node.lineno} {ast.unparse(target)} = …")
            # ② 진짜 클래스에 심는 꼴 — `patched(type(pane), write=…)`
            #    ☠ 이것이 제일 나쁘다: 가짜가 **프로덕션 클래스 자체**에 없는 이름을
            #    달아 주므로, 그 블록 안에서는 오타가 «진짜로 동작한다». 이 파일의 첫
            #    판이 이 꼴을 못 봐서 `tests/test_plugin_screen.py` 의 두 시험이
            #    남아 있었다(전량에서 붉어져서야 드러났다).
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "patched"
                    and any(kw.arg == "write" for kw in node.keywords)):
                target = ast.unparse(node.args[0]) if node.args else ""
                if "pane" in target.lower():
                    bad.append(f"{rel}:{node.lineno} patched({target}, write=…)")
            # ③ 클래스로 붙이는 꼴 — `class _Pane: def write(self, data)`
            if isinstance(node, ast.ClassDef) and node.name.lower().endswith("pane"):
                if (rel, node.name) in _ALLOWED_TEST_FAKES:
                    continue
                for item in node.body:
                    if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and item.name == "write"):
                        bad.append(f"{rel}:{item.lineno} class {node.name}: def write")
    assert not bad, (
        "가짜 패널이 프로덕션에 없는 `write` 를 만들어 붙인다 — 그러면 오타가 라이브에서만 "
        "터지고 시험은 늘 초록이다(pytmux-173 이 그렇게 살아남았다). 가짜는 `pane.pty` 쪽에 "
        "단다. 같은 낱말이지만 다른 일이면 위 `_ALLOWED_TEST_FAKES` 에 **까닭과 함께** 적는다. "
        "걸린 것: " + " · ".join(bad)
    )
