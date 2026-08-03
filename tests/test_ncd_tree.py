"""ncd 가 **트리**다 — 깊이·펼침·접기·현재 자리(pytmux-11 B).

제보: *"정본은 트리 구조를 직접 내비게이팅하는데 GUI 는 '지금 트리를 조회하고 다른
트리로 이동'만 된다 — 완전히 같게."*

종전 스펙은 **한 디렉터리씩 보이는 평면 목록**이었고, 그것은 설계 §6("스펙은 내용과
선택을 정하고 표현은 각 클라 관례를 따른다")에 기대 **의도한 선**이었다. 제보가 그 선을
옮겼으므로 스펙이 깊이와 펼침을 나른다.

여기서 재는 것은 **스펙이 실제로 무엇을 담는가**다 — 트리 모양(무엇이 펼쳐졌나)은 보는
사람마다 다르고 그 클라의 보관함(`req["state"]`)에 산다.
"""
import importlib
import os
import tempfile

import harness  # noqa: F401  (sys.path 주입)

_pkg = importlib.import_module("pytmuxlib.plugins.ncd")
PLUGIN = _pkg.PLUGIN


def _tree(td, cwd=None):
    """`td` 아래에서 연 트리의 스펙(state 는 그 클라 것 — 여기선 우리가 든다)."""
    mine = {"path": cwd or td, "cwd": cwd or td}
    return PLUGIN._open_tree(mine), mine


def _labels(spec):
    return [(r["depth"], r["label"], r["expand"], r["tag"]) for r in spec["rows"]]


def _row(spec, label):
    return next(r for r in spec["rows"] if r["label"] == label)


def _mktree(td):
    """`td/a/b` 와 `td/z`(빈 디렉터리) — 깊이·잎을 한 번에 잰다."""
    os.makedirs(os.path.join(td, "a", "b"))
    os.mkdir(os.path.join(td, "z"))


async def test_the_tree_opens_expanded_down_to_where_the_shell_stands():
    """열자마자 **지금 어디에 있는지**가 보여야 한다 — 정본이 뿌리→cwd 를 펼치는 이유다.

    커서도 그 줄에 선다: 열자마자 `Enter` 한 번이 뜻을 갖는다."""
    with tempfile.TemporaryDirectory() as td:
        _mktree(td)
        here = os.path.join(td, "a")
        spec, _mine = _tree(td, cwd=here)
        rows = spec["rows"]
        # 사슬 위의 줄이 전부 있고, 깊이가 실제 계층을 따른다.
        depths = {r["label"]: r["depth"] for r in rows}
        assert depths.get("a", -1) > 0, depths
        assert depths.get("b", -1) == depths["a"] + 1, depths
        # 커서는 셸이 서 있는 줄에.
        assert rows[spec["selected"]]["key"] == here, (spec["selected"], rows)
        # 그리고 그 줄이 **현재 자리**로 표시된다(정본은 노랑 + 표식).
        assert _row(spec, "a")["tag"] == "cwd", _labels(spec)


async def test_a_leaf_is_not_a_collapsed_node():
    """접힘과 **잎**은 다르다 — 빈 디렉터리에 `▸` 를 붙이면 눌러도 안 열리는 화살표가
    생기고, 그건 화면이 거짓말하는 것이다."""
    with tempfile.TemporaryDirectory() as td:
        _mktree(td)
        spec, _mine = _tree(td)
        assert _row(spec, "z")["expand"] == "", _labels(spec)
        assert _row(spec, "a")["expand"] in ("open", "shut"), _labels(spec)


async def test_expanding_shows_the_children_and_collapsing_hides_them():
    """→ 로 펴고 ← 로 접는다(정본 손버릇). 지연 로드라 편 뒤에야 자식이 온다."""
    with tempfile.TemporaryDirectory() as td:
        _mktree(td)
        # cwd 를 뿌리에 두면 `a` 는 접혀서 열린다.
        spec, mine = _tree(td)
        a = _row(spec, "a")["key"]
        assert _row(spec, "a")["expand"] == "shut", _labels(spec)
        assert not any(r["label"] == "b" for r in spec["rows"]), "안 폈는데 자식이 보인다"
        spec = PLUGIN._expand(mine, a)
        assert _row(spec, "a")["expand"] == "open", _labels(spec)
        assert any(r["label"] == "b" for r in spec["rows"]), _labels(spec)
        spec = PLUGIN._collapse(mine, a)
        assert _row(spec, "a")["expand"] == "shut", _labels(spec)
        assert not any(r["label"] == "b" for r in spec["rows"]), _labels(spec)


async def test_collapsing_an_already_closed_row_goes_to_its_parent():
    """★ `←` 는 **두 뜻**이다(정본과 같다): 펴져 있으면 접고, 접혀 있으면 부모로.

    한 뜻만 두면 접힌 잎에서 `←` 가 죽은 키가 되고, 사람은 그 키를 안 쓰게 된다."""
    with tempfile.TemporaryDirectory() as td:
        _mktree(td)
        here = os.path.join(td, "a")
        spec, mine = _tree(td, cwd=here)
        b = _row(spec, "b")["key"]
        spec = PLUGIN._collapse(mine, b)          # `b` 는 잎 → 부모로
        assert spec["rows"][spec["selected"]]["key"] == here, _labels(spec)


async def test_the_spec_declares_the_keys_that_move_the_tree():
    """`←→` 는 글자가 아니다 — 스펙이 **이름 있는 키**로 실어야 클라가 먹는다.

    안 실으면 그 키는 목록 화면의 기본 뜻(닫기)으로 떨어져 판이 닫힌다."""
    with tempfile.TemporaryDirectory() as td:
        _mktree(td)
        spec, _mine = _tree(td)
        assert spec["keys"].get("right") == "expand", spec["keys"]
        assert spec["keys"].get("left") == "collapse", spec["keys"]
        # 종전 손도 그대로다(제보는 더하라는 것이지 없애라는 것이 아니었다).
        assert spec["keys"].get("enter") == "into", spec["keys"]
        assert spec["keys"].get("c") == "cd", spec["keys"]


async def test_the_label_stays_data():
    """⛔ 들여쓰기를 **글자로 섞지 않는다** — 그러면 이름이 더는 자료가 아니고,
    타이핑 찾기·복사가 그 공백을 물고 간다. 깊이는 따로 나른다."""
    with tempfile.TemporaryDirectory() as td:
        _mktree(td)
        here = os.path.join(td, "a")
        spec, _mine = _tree(td, cwd=here)
        for r in spec["rows"]:
            assert r["label"] == r["label"].strip(), r


async def test_a_cut_tree_says_so_and_keeps_the_path_visible():
    """★ 형제가 수만인 디렉터리가 실제로 있다(실측: 임시 디렉터리 하나에서 89142 줄).

    ⛔ **말없이 자르지 않는다** — 잘렸으면 그렇게 말하고, 무엇보다 **내가 서 있는 자리로
    가는 길**은 남긴다. 앞쪽 형제가 상한을 다 먹어 사슬이 끊기면 그건 자른 것이 아니라
    화면을 못 쓰게 만든 것이다."""
    with tempfile.TemporaryDirectory() as td:
        deep = os.path.join(td, "zzz-mine")
        os.mkdir(deep)
        # 상한을 넘기는 형제를 만든다(이름순 정렬이라 `zzz-mine` 은 맨 뒤로 밀린다).
        for i in range(PLUGIN._MAX_KIDS + 20):
            os.mkdir(os.path.join(td, f"sib{i:05d}"))
        spec, _mine = _tree(td, cwd=deep)
        labels = [r["label"] for r in spec["rows"]]
        assert "zzz-mine" in labels, "잘리면서 내 자리로 가는 길이 끊겼다"
        assert spec["rows"][spec["selected"]]["key"] == deep, spec["selected"]
        assert spec["note"], "잘랐는데 아무 말도 안 했다"
