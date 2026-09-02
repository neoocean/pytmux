"""ncd 가 **트리**다 — 깊이·펼침·접기·현재 자리(pytmux-11 B).

제보: *"정본은 트리 구조를 직접 내비게이팅하는데 GUI 는 '지금 트리를 조회하고 다른
트리로 이동'만 된다 — 완전히 같게."*

종전 스펙은 **한 디렉터리씩 보이는 평면 목록**이었고, 그것은 설계 §6("스펙은 내용과
선택을 정하고 표현은 각 클라 관례를 따른다")에 기대 **의도한 선**이었다. 제보가 그 선을
옮겼으므로 스펙이 깊이와 펼침을 나른다.

여기서 재는 것은 **스펙이 실제로 무엇을 담는가**다 — 트리 모양(무엇이 펼쳐졌나)은 보는
사람마다 다르고 그 클라의 보관함(`req["state"]`)에 산다.
"""
import contextlib
import importlib
import os
import tempfile

import harness  # noqa: F401  (sys.path 주입)
from pytmuxlib import proc
from run import skip

_pkg = importlib.import_module("pytmuxlib.plugins.ncd")
PLUGIN = _pkg.PLUGIN


@contextlib.contextmanager
def _tmpdir():
    r"""임시 디렉터리를 **온디스크 이름**으로 준다(POSIX 에선 `TemporaryDirectory` 그대로).

    트리는 cwd 사슬을 `scandir` 의 이름과 맞춰 그린다. 그래서 서버가 넘기는 cwd 는
    온디스크 이름이라는 것이 이 층의 계약이고(`proc.long_path` — 8.3 단축을 편다),
    픽스처도 같은 표기여야 잰 것이 제품과 같아진다. 안 맞추면 `TMP` 가 단축 경로인
    상자(`C:\Users\WOOJIN~1\...`)에서 이 모듈이 통째로 거짓 적색이 된다 — 실제로
    그랬다(pytmux-237·-436..441). `long_path` 는 POSIX 에서 항등이다."""
    with tempfile.TemporaryDirectory() as td:
        yield proc.long_path(td)


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
    with _tmpdir() as td:
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
    with _tmpdir() as td:
        _mktree(td)
        spec, _mine = _tree(td)
        assert _row(spec, "z")["expand"] == "", _labels(spec)
        assert _row(spec, "a")["expand"] in ("open", "shut"), _labels(spec)


async def test_expanding_shows_the_children_and_collapsing_hides_them():
    """→ 로 펴고 ← 로 접는다(정본 손버릇). 지연 로드라 편 뒤에야 자식이 온다."""
    with _tmpdir() as td:
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
    with _tmpdir() as td:
        _mktree(td)
        here = os.path.join(td, "a")
        spec, mine = _tree(td, cwd=here)
        b = _row(spec, "b")["key"]
        spec = PLUGIN._collapse(mine, b)          # `b` 는 잎 → 부모로
        assert spec["rows"][spec["selected"]]["key"] == here, _labels(spec)


async def test_the_spec_declares_the_keys_that_move_the_tree():
    """`←→` 는 글자가 아니다 — 스펙이 **이름 있는 키**로 실어야 클라가 먹는다.

    안 실으면 그 키는 목록 화면의 기본 뜻(닫기)으로 떨어져 판이 닫힌다."""
    with _tmpdir() as td:
        _mktree(td)
        spec, _mine = _tree(td)
        assert spec["keys"].get("right") == "expand", spec["keys"]
        assert spec["keys"].get("left") == "collapse", spec["keys"]
        # 종전 손도 그대로다(제보는 더하라는 것이지 없애라는 것이 아니었다).
        assert spec["keys"].get("enter") == "into", spec["keys"]
        assert spec["keys"].get("c") == "cd", spec["keys"]


class _Pty:
    """패널의 pty — 받은 바이트만 들고 있는다."""

    def __init__(self):
        self.written = b""

    def write(self, data):
        self.written += data


class _Pane:
    """패널 한 짝. **글자가 들어가는 자리는 `pane.pty` 다.**

    ☠ **가짜를 `pane` 에 달지 마라**(pytmux-173). 종전 이 클래스는 `write` 를 **자기가**
    들고 있었는데, 프로덕션의 `Pane`(`pytmuxlib/model.py`)에는 그런 메서드가 **없다** —
    즉 가짜가 프로덕션에 없는 이름을 지어내 준 것이고, 그래서 아래 두 시험은 「Enter 가
    패널에 cd 를 친다」를 재고 있다고 믿으면서 **늘 초록**이었고 라이브는 **늘**
    `AttributeError` 로 터졌다(라이브 로그 실측 2026-08-23). 루트 CLAUDE.md 가 경고하는
    *공허 통과*의 표본이다.

    ⛔ 여기에 `write` 를 다시 달면 그 공허 통과가 그대로 되살아난다. 「프로덕션에 없는
    이름을 가짜가 만들지 않는가」를 저장소 전수로 재는 자리는
    `tests/test_pane_write_typo.py` 다.
    """

    def __init__(self):
        self.pty = _Pty()


class _Win:
    def __init__(self, pane):
        self.active_pane = pane


class _Sess:
    def __init__(self, pane):
        self.active_window = _Win(pane)


async def test_enter_actually_types_the_cd_into_the_active_pane():
    """★ **제보(pytmux-173)가 가리킨 절반이 여기다** — GUI 는 `Enter` 를 `into` 로 잘 보내는데
    (그쪽은 `session_view_tests.rs::enter_on_an_ncd_row_sends_the_into_action_with_that_path`
    가 잠갔다) 「패널에 `cd` 가 안 들어간다」는 제보가 남아 있었다.

    ⛔ **이 자리를 재는 시험이 없었다.** `into` 갈래는 화면을 닫는 응답을 늘 돌려주므로
    (`plugin_screen_close`) 패널에 아무것도 안 써도 **겉보기는 성공과 똑같다** — 화면은
    닫히고 아무 말도 안 남는다. 그래서 눈으로도 기계로도 안 잡히던 자리다."""
    with _tmpdir() as td:
        _mktree(td)
        target = os.path.join(td, "a")
        pane = _Pane()
        resp = PLUGIN.plugin_screen(
            object(), _Sess(pane),
            {"id": "ncd", "do": "into", "row": 0, "input": target, "state": {}},
        )
        assert resp == {"t": "plugin_screen_close", "id": "ncd", "input": target}, resp
        typed = pane.pty.written.decode("utf-8")
        assert typed.endswith("\r"), f"엔터가 안 붙었다 — 셸이 명령을 실행하지 않는다: {typed!r}"
        assert target in typed, f"고른 경로가 안 실렸다: {typed!r}"
        # 셸 방언은 **이 서버의 OS** 가 정한다(클라의 것을 쓰면 Windows 클라가 macOS
        # 패널에 `cd /d` 를 흘린다 — 그 주석이 핸들러에 적혀 있다).
        assert typed.startswith("cd /d " if os.name == "nt" else "cd "), typed


async def test_an_into_without_a_path_does_not_pretend_it_worked():
    """`input` 이 비면 패널에 아무것도 안 간다 — 그때 화면만 닫히면 사용자에게는
    **제보와 똑같은 증상**(눌렀는데 아무 일도 안 남)이 된다.

    여기서는 그 사실을 못박아 둔다: 빈 경로로는 아무것도 안 쓴다."""
    pane = _Pane()
    resp = PLUGIN.plugin_screen(
        object(), _Sess(pane),
        {"id": "ncd", "do": "into", "row": 0, "input": "", "state": {}},
    )
    assert pane.pty.written == b"", pane.pty.written
    assert resp["t"] == "plugin_screen_close", resp


async def test_the_c_key_types_a_cd_too():
    """글자 키 `c`(여기로 cd)도 같은 자리를 지난다 — `into` 만 재고 이쪽을 안 재면
    한쪽이 조용히 죽는다(둘은 서로 다른 갈래다)."""
    with _tmpdir() as td:
        _mktree(td)
        target = os.path.join(td, "a")
        pane = _Pane()
        PLUGIN.plugin_screen(
            object(), _Sess(pane),
            {"id": "ncd", "do": "cd", "row": 0, "input": target, "state": {}},
        )
        typed = pane.pty.written.decode("utf-8")
        assert target in typed and typed.endswith("\r"), typed


async def test_a_swallowed_send_says_why_instead_of_going_quiet():
    """★ 못 넣었으면 **말한다**(pytmux-417 ③ⓐ).

    가드 셋(패널 없음 · pty 없음 · `OSError`)은 전부 조용해서, 걸리면 사용자에게는
    「Enter 를 눌렀는데 아무 일도 안 남」으로 보인다 — [[pytmux-173]] 이 남긴 것과 **글자
    그대로 같은 그림**인데 트레이스백조차 없어 로그에도 단서가 없다. 실제로 제보
    (2026-08-30)를 받고도 세 갈래 중 어느 것인지 소스만으로 못 갈랐다.
    ⛔ **터지면 안 된다** — 여기서 예외를 올리면 판까지 안 닫힌다(173 이 겪은 자리).
    """
    class _Server:
        def __init__(self):
            self.logged = []

        def _log_error(self, where, detail=""):
            self.logged.append((where, detail))

    class _Boom:
        def write(self, _data):
            raise OSError("EIO")

    with _tmpdir() as td:
        _mktree(td)
        target = os.path.join(td, "a")

        for label, pane, hint in (
                ("pty 가 없다", _pane_without_pty(), "pty"),
                ("write 가 거절한다", _pane_with(_Boom()), "EIO"),
        ):
            server = _Server()
            resp = PLUGIN.plugin_screen(
                server, _Sess(pane),
                {"id": "ncd", "do": "into", "row": 0, "input": target, "state": {}},
            )
            assert resp["t"] == "plugin_screen_close", f"{label}: 판이 안 닫혔다 — {resp}"
            assert server.logged, f"{label}: 조용히 삼켰다 — 로그가 비었다"
            where, detail = server.logged[-1]
            assert where == "ncd_send_to_pane", (label, where)
            assert hint in detail, f"{label}: 이유를 안 적었다 — {detail!r}"
            assert target in detail, f"{label}: 넣으려던 글자를 안 적었다 — {detail!r}"

    # ⚠ **성공한 회차는 조용해야 한다** — 안 그러면 이 로그가 곧 잡음이 되고,
    #    잡음이 된 로그는 다음 사람이 안 읽는다(위양성 쪽도 함께 잰다).
    with _tmpdir() as td:
        _mktree(td)
        server = _Server()
        PLUGIN.plugin_screen(
            server, _Sess(_Pane()),
            {"id": "ncd", "do": "into", "row": 0,
             "input": os.path.join(td, "a"), "state": {}},
        )
        assert not server.logged, f"멀쩡히 넣고도 로그를 남겼다: {server.logged}"


def _pane_without_pty():
    p = _Pane()
    p.pty = None
    return p


def _pane_with(pty):
    p = _Pane()
    p.pty = pty
    return p


async def test_an_into_survives_a_pane_without_a_pty():
    """`pane.pty` 는 `None` 일 수 있다(`model.py` 의 `self.pty = None` · `reinit` 직후).
    거기서 터지면 `plugin_screen_close` 가 안 나가서 **화면이 안 닫힌다** — 오타 때와
    똑같은 증상이라, 고치면서 넣은 가드를 여기서 잰다(pytmux-173)."""
    with _tmpdir() as td:
        _mktree(td)
        pane = _Pane()
        pane.pty = None
        resp = PLUGIN.plugin_screen(
            object(), _Sess(pane),
            {"id": "ncd", "do": "into", "row": 0,
             "input": os.path.join(td, "a"), "state": {}},
        )
        assert resp["t"] == "plugin_screen_close", resp


async def test_the_label_stays_data():
    """⛔ 들여쓰기를 **글자로 섞지 않는다** — 그러면 이름이 더는 자료가 아니고,
    타이핑 찾기·복사가 그 공백을 물고 간다. 깊이는 따로 나른다."""
    with _tmpdir() as td:
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
    with _tmpdir() as td:
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


def _canon_tag_colours():
    """정본에서 뽑은 **이름 → hex** 표(`client/scripts/gen_row_tags.py` 한 벌).

    여기서 정규식을 다시 쓰지 않는 이유: 그러면 「정본의 색을 읽는 법」이 두 벌이 되고,
    두 벌은 갈린다 — 이 저장소가 여러 번 밟은 자리라 뽑는 코드는 그 스크립트 하나다."""
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gen = os.path.join(root, "client", "scripts", "gen_row_tags.py")
    if not os.path.exists(gen):
        return None, root
    spec = importlib.util.spec_from_file_location("gen_row_tags", gen)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import pathlib
    return mod.collect(pathlib.Path(root))["tags"], root


async def test_ncd_never_wears_a_colour_its_own_screen_does_not_have():
    r"""☠ **회귀(제보 2026-08-08 · CL 71010 빌드)**: GUI 의 `:ncd` 가 트리 전체를 **밝은
    빨강**으로 그렸다 — "정본 배색과 다르다".

    원인은 색 코드가 아니라 **이름**이었다. 줄의 태그 어휘는 한 벌을 두 화면이 나눠 쓰고
    (`mdir/rowtag.py` 의 `TAGS`), 값은 mdir 의 `_TAG_STYLES` 에서 뽑힌다 — 그래서 `dir` 은
    곧 **Mdir 시그니처 붉은색**이다. ncd 의 스펙이 평범한 줄에 그 이름을 달고 있었고, 그
    이름을 아는 클라(Rust GUI)는 시킨 대로 붉게 칠했다. 정본 `ncd/screen.py` 에는 붉은색이
    **한 칸도 없다**(판 바탕 `_BG` · 선택 막대 `_SEL`/`_SEL_BLUR` · 현재 자리 `_CWD`).

    그래서 재는 것은 「`dir` 이 아니다」가 아니라 **「이 화면이 입는 색은 이 화면의
    팔레트에 있는 색뿐이다」**다 — 후자라야 다음에 다른 이름(`drive`·`tagged` …)이 같은
    길로 들어와도 운다. 뜻이 없는 줄은 이름을 안 달고, 그 줄은 각 클라의 기본 글자색으로
    그려진다(정본이 `_BG` 로 하는 일)."""
    import re
    tags, root = _canon_tag_colours()
    if tags is None:
        from run import skip
        skip("client/scripts/gen_row_tags.py 가 없다 — 정본 색표를 뽑을 자리가 없는 트리")
        return
    screen = os.path.join(root, "pytmuxlib", "plugins", "ncd", "screen.py")
    with open(screen, encoding="utf-8") as f:
        src = f.read()
    palette = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{6}", src)}
    assert len(palette) >= 4, f"ncd 팔레트를 못 읽었다({palette}) — 정규식이 헛돌았다"

    with _tmpdir() as td:
        _mktree(td)
        here = os.path.join(td, "a")
        spec, _mine = _tree(td, cwd=here)
        assert spec["rows"], "줄이 없으면 아래 단언이 공허하게 통과한다"
        worn = {r["tag"] for r in spec["rows"] if r["tag"]}
        assert "cwd" in worn, f"현재 자리 강조가 사라졌다: {_labels(spec)}"
        wrong = sorted(
            f"{t}({tags.get(t, '모르는 이름')})" for t in worn
            if tags.get(t, "").lower() not in palette)
        assert not wrong, (
            f"ncd 가 자기 팔레트에 없는 색을 입었다: {wrong} · "
            f"정본 팔레트 {sorted(palette)}")


# ---- Windows: 뿌리가 여럿이다 (pytmux-160) ---------------------------------
#
# ☠ **드라이브 나열 코드는 있었는데 이 화면이 그것을 안 탔다.** `server._drive_roots()`
# 를 쓰는 것은 `nc_list_fs`(정본 Textual 클라가 쓰는 액션)뿐이고, 네이티브 화면을 여는
# `plugin_screen`→`_open_tree` 는 `_chain(cwd)` 의 꼭대기 — **셸이 서 있는 드라이브
# 하나** — 를 뿌리로 삼았다. 그래서 Windows 에서 `C:\` 만 보이고 `D:\` 로 옮겨갈 항목이
# 트리에 아예 없었다(제보 2026-08-08 · 우회로 없음).
#
# 여기서 Windows 를 **이 상자에서** 재는 법: 경로 판정 모듈(`server._pathmod`)과
# 드라이브 나열·디렉터리 나열을 픽스처로 바꾼다 — `test_nc.py::
# test_nc_build_chain_prepends_drives` 가 정본 쪽에서 쓰는 것과 같은 관례다.

_WIN_FS = {
    "C:\\": ["C:\\Users", "C:\\Windows"],
    "C:\\Users": ["C:\\Users\\me"],
    "C:\\Users\\me": ["C:\\Users\\me\\proj"],
    "C:\\Users\\me\\proj": [],
    "C:\\Windows": [],
    "D:\\": ["D:\\work"],
    "D:\\work": [],
}


def _windows(drives=("C:\\", "D:\\"), seen=None):
    """이 블록 동안만 Windows 인 척한다(경로 판정·드라이브·디렉터리 나열).

    `seen` 을 주면 `_list_dirs` 가 받은 경로를 거기 적는다 — **안 읽었다**를 재는 데
    쓴다(연결 안 된 드라이브를 잎 판정 하나 때문에 열면 화면이 멎는다)."""
    import ntpath
    server = importlib.import_module("pytmuxlib.plugins.ncd.server")

    def _list(p):
        # 진짜 `scandir` 처럼 굴어야 한다: Windows 파일시스템은 **대소문자를 안 가리고**,
        # `entry.path` 는 **요청한 표기** 위에 이름을 얹는다. 픽스처가 대소문자를 가리면
        # 사슬이 셸 표기(`c:\users`)로 뻗을 때 조용히 빈 목록이 돌아와, 프로덕션이 아니라
        # 픽스처가 만든 거짓 붉음이 난다.
        if seen is not None:
            seen.append(p)
        want = ntpath.normcase(ntpath.normpath(p))
        for d, kids in _WIN_FS.items():
            if ntpath.normcase(ntpath.normpath(d)) == want:
                return [ntpath.join(p, ntpath.basename(k.rstrip("\\"))) for k in kids]
        return []

    return harness.patched(server, _pathmod=ntpath,
                           _drive_roots=lambda: list(drives), _list_dirs=_list)


def _tops(spec):
    return [r["key"] for r in spec["rows"] if r["depth"] == 0]


async def test_windows_lists_every_drive_as_a_sibling_at_the_top():
    r"""Windows 에는 뿌리가 여럿이다 — `C:\` 옆에 `D:\` 가 **형제로** 서야 한다.

    종전엔 셸이 선 드라이브 하나만 뿌리였고, 다른 드라이브로 갈 항목이 트리에 없었다."""
    with _windows():
        mine = {"path": "C:\\Users\\me", "cwd": "C:\\Users\\me"}
        spec = PLUGIN._open_tree(mine)
        assert _tops(spec) == ["C:\\", "D:\\"], _labels(spec)
        # 뿌리는 basename 이 비므로 경로 그대로 이름이 된다(`C:` 가 아니다 — 그건
        # Windows 에서 «그 드라이브의 현재 디렉터리»라는 전혀 다른 자리다).
        assert [r["label"] for r in spec["rows"] if r["depth"] == 0] == ["C:\\", "D:\\"]
        # 서 있는 드라이브는 펼쳐져 cwd 까지 길이 보이고, 커서가 그 줄에 선다.
        keys = [r["key"] for r in spec["rows"]]
        assert "C:\\Users" in keys and "C:\\Users\\me" in keys, keys
        assert spec["rows"][spec["selected"]]["key"] == "C:\\Users\\me", keys
        assert _row(spec, "me")["tag"] == "cwd", _labels(spec)


async def test_windows_does_not_read_a_drive_before_it_is_opened():
    r"""⛔ 잎 판정 하나 때문에 **모든 드라이브를 열지 않는다** — 연결 안 된 네트워크·
    광학 드라이브가 하나 있으면 그 한 번에 화면이 멎는다(정본도 펼칠 때 읽는다)."""
    seen = []
    with _windows(seen=seen):
        mine = {"path": "C:\\Users\\me", "cwd": "C:\\Users\\me"}
        spec = PLUGIN._open_tree(mine)
        assert "D:\\" not in seen, seen
        # 그래도 **열 수 있는 줄**로 보여야 한다(안 읽었다고 잎으로 그리면 거짓말이다).
        assert next(r for r in spec["rows"] if r["key"] == "D:\\")["expand"] == "shut"


async def test_windows_can_move_to_another_drive():
    r"""제보의 요구 그대로: `D:\` 로 **옮겨갈 수 있어야** 한다."""
    with _windows():
        mine = {"path": "C:\\Users\\me", "cwd": "C:\\Users\\me"}
        PLUGIN._open_tree(mine)
        spec = PLUGIN._expand(mine, "D:\\")
        d = next(r for r in spec["rows"] if r["key"] == "D:\\")
        assert d["expand"] == "open", _labels(spec)
        work = next(r for r in spec["rows"] if r["key"] == "D:\\work")
        assert work["depth"] == d["depth"] + 1, _labels(spec)
        # ★ **커서도 단언한다**(pytmux-417 ②). 종전엔 줄만 봐서, 편 뒤 커서가 `cwd` 로
        #   튀어도 초록이었다 — 그리고 클라는 새 스펙의 `selected` 를 그대로 적용하므로
        #   (「커서의 주인은 스펙」) 사용자에겐 **「D: 트리가 안 열린다」**로 보인다.
        assert spec["rows"][spec["selected"]]["key"] == "D:\\", (
            "편 줄에 커서가 안 남았다: "
            + spec["rows"][spec["selected"]]["key"] + " · " + _labels(spec))


async def test_collapsing_keeps_the_cursor_where_you_pressed_it():
    """접기도 같은 규약이다 — `←→` 를 번갈아 눌러도 자리가 안 흔들려야 한다."""
    with _windows():
        mine = {"path": "C:\\Users\\me", "cwd": "C:\\Users\\me"}
        PLUGIN._open_tree(mine)
        PLUGIN._expand(mine, "D:\\")
        spec = PLUGIN._collapse(mine, "D:\\")
        assert next(r for r in spec["rows"] if r["key"] == "D:\\")["expand"] == "shut"
        assert spec["rows"][spec["selected"]]["key"] == "D:\\", (
            "접은 줄에 커서가 안 남았다: "
            + spec["rows"][spec["selected"]]["key"] + " · " + _labels(spec))


async def test_collapsing_a_shut_row_climbs_to_the_parent():
    """이미 접힌 줄에서 `←` 는 **부모로 올라간다**(정본 `←` 의 두 번째 뜻).
    커서 규약을 고치면서 이 뜻이 깨지지 않았는지 함께 잰다."""
    with _windows():
        mine = {"path": "C:\\Users\\me", "cwd": "C:\\Users\\me"}
        PLUGIN._open_tree(mine)
        # `C:\\Windows` 는 이미 접혀 있는 줄(자식이 없다) — 거기서 `←` 는 부모로 간다.
        spec = PLUGIN._collapse(mine, "C:\\Windows")
        assert spec["rows"][spec["selected"]]["key"] == "C:\\", _labels(spec)


async def test_the_hint_says_what_the_keys_actually_do():
    """힌트가 동작과 어긋나면 **화면이 스스로 틀린 기대를 만든다**(pytmux-417).

    종전 힌트는 「Enter 들어가기」였는데 실제 `Enter` 는 정본과 같이 **그 자리로 cd** 다.
    그리고 항해 키 넷은 정본 힌트에 적혀 있고 이제 GUI 에서도 먹는다."""
    from pytmuxlib import i18n
    hint = i18n.t("ncd.hint")
    assert "Enter cd" in hint, hint
    assert "들어가기" not in hint, hint
    for k in ("Home/End", "PgUp/PgDn", "펼치기", "접기"):
        assert k in hint, (k, hint)


async def test_windows_drive_row_uses_the_canonical_spelling():
    r"""셸이 준 cwd 는 사용자가 친 대소문자를 그대로 물고 온다(`c:\users\me`).

    맞추지 않으면 같은 드라이브가 **두 줄**로 뜬다(하나는 최상위 목록, 하나는 사슬 머리)."""
    with _windows():
        mine = {"path": "c:\\users\\me", "cwd": "c:\\users\\me"}
        spec = PLUGIN._open_tree(mine)
        assert _tops(spec) == ["C:\\", "D:\\"], _labels(spec)
        assert spec["rows"][spec["selected"]]["key"].lower() == "c:\\users\\me"


async def test_windows_keeps_the_drive_we_stand_on_even_if_the_listing_misses_it():
    r"""서 있는 드라이브가 목록에서 빠지면(subst·매핑 경합) **보강한다** — 지금 자리가
    트리에 없으면 사슬이 통째로 끊긴다(`server._build_chain` 이 치르는 같은 값)."""
    with _windows(drives=("D:\\",)):
        mine = {"path": "C:\\Users", "cwd": "C:\\Users"}
        spec = PLUGIN._open_tree(mine)
        assert _tops(spec) == ["C:\\", "D:\\"], _labels(spec)


async def test_posix_still_has_exactly_one_root():
    """⛔ 드라이브 층을 **없는 곳에 만들지 않는다** — POSIX 는 뿌리가 하나다.

    ⚠ 위 형제들과 달리 이 시험은 **진짜 파일시스템**을 본다(`_windows()` 같은 픽스처가
    없다) — 재려는 것이 "이 OS 에 드라이브가 없으면 층도 없다"이므로 OS 를 흉내내면
    그 자리에서 답을 심어 버린다. 그래서 Windows 에서는 **잴 것이 없다**: 이 상자에는
    드라이브가 실제로 여럿이고(C/D/R/Z) 그 층은 설계대로다. 그쪽 계약을 재는 것은
    바로 위 `test_windows_lists_every_drive_as_a_sibling_at_the_top` 이다(pytmux-396).
    """
    if os.name == "nt":
        skip("POSIX 전용(진짜 파일시스템을 본다 — Windows 는 드라이브 층이 정상이다)")
    with _tmpdir() as td:
        _mktree(td)
        spec, mine = _tree(td)
        assert mine["root"] == PLUGIN._chain(td)[0] != "", mine["root"]
        assert len([r for r in spec["rows"] if r["depth"] == 0]) == 1, _labels(spec)
