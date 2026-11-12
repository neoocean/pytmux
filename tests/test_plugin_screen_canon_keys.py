"""정본 플러그인 판의 **키**를 AST 로 읽어 GUI 스펙의 키 표와 맞댄다(pytmux-611~616).

# 왜 이 자가 필요한가

GUI 의 플러그인 판은 서버가 자료로 그려 주는 선언형 판이다(`plugin_screen`). 그 판의 키 표
(`keys`)는 플러그인이 손으로 짓는데, 정본 Textual 화면(`screen.py` 의 `on_key`)과 **맞대는
자가 없었다** — 그래서 mdir 의 Space·Insert·Delete·⇧Enter, ncd 의 타이핑 찾기, p4 목록의 `q`,
namesync 의 `e`·`j`·`k`, 프롬프트 이력의 `+`/`-` 가 GUI 에서 통째로 빠진 채 아무 시험도 안
울었다(`client-reports-2026-11-12-screen-contents` §A).

`client/scripts/gen_screen_keys.py` 가 코어 화면(`clientscreens.py`)에 대해 하는 일을 이 자가
플러그인 판에 대해 한다: 정본 `on_key` 의 키 이름·글자를 **AST 로** 뽑고, 실제로 지은 스펙
dict 의 키 표에 그 이름이 있는지 — 없으면 core 가 그 키를 쥐는지, 또는 스펙이 타이핑 찾기를
선언했는지 — 를 단언한다. 반대로 **정본에 없는 키**를 스펙이 실으면 그것도 갈림으로 센다
(글자 별칭이 정본의 빨리찾기 글자를 먹던 것이 그 부류다).

키 이름은 `base::keys::binding_name_with` 문법이다 — 정본 Textual 이름의 `+` 를 `-` 로.
GUI 의 `PluginScreen::key_action` 이 그 이름을 실제로 배달하는지는 Rust 쪽 시험
(`gui/src/session_view_tests.rs` 의 `plugin_keys_*`)이 잰다.
"""
from __future__ import annotations

import ast
import importlib
import os
import tempfile
import types

import harness  # noqa: F401  (경로 설정)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUG = os.path.join(ROOT, "pytmuxlib", "plugins")

#: core(목록 판)가 쥐는 키 — 스펙이 못 가져가고(`key_action` 의 예약) 판마다 뜻이 같다.
_CORE = {"up", "down", "home", "end", "pageup", "pagedown", "escape", "tab"}
#: Textual 이 글자 키에 붙이는 이름 → 글자.
_ALIASES = {"plus": "+", "minus": "-", "equals_sign": "=", "full_stop": ".",
            "asterisk": "*", "backslash": "\\", "space": "space"}

#: (플러그인 디렉터리, 정본 파일, 정본 클래스, 그 판의 core 가 더 쥐는 키)
_CANON = {
    "mdir": ("mdir", "screen.py", "_MdirView", {"left", "right"}),
    "ncd": ("ncd", "screen.py", "_NcdView", set()),
    "p4changes": ("p4-show-submitted-changelists", "screen.py", "_ChangesView", set()),
    "claude-resume": ("claude-resume", "screen.py", "ClaudeResumeScreen", set()),
    "namesync": ("claude-name-sync", "screen.py", "NameSyncScreen", set()),
    "prompt-history": ("claude-prompt-history", "screen.py", "_HistView", set()),
}


# ── 정본 쪽: on_key 를 읽는다 ──────────────────────────────────────────────────
def _const_strs(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [e.value for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def _canon(pkg, fname, cls):
    """`(키 이름들, 글자들, 타이핑 찾기가 있나, 목록 선택(Enter)을 받나)`."""
    with open(os.path.join(PLUG, pkg, fname), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    on_key = next(n for n in klass.body
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "on_key")
    key_vars, char_vars = set(), set()
    for n in ast.walk(on_key):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Attribute) \
                and isinstance(n.value.value, ast.Name) and n.value.value.id == "event":
            for t in n.targets:
                if isinstance(t, ast.Name):
                    (key_vars if n.value.attr == "key" else
                     char_vars if n.value.attr == "character" else set()).add(t.id)

    def is_(node, attr, names):
        if isinstance(node, ast.Attribute) and node.attr == attr \
                and isinstance(node.value, ast.Name) and node.value.id == "event":
            return True
        return isinstance(node, ast.Name) and node.id in names

    keys, chars = set(), set()
    typeahead = False
    for n in ast.walk(on_key):
        if isinstance(n, ast.Compare) and len(n.ops) == 1 \
                and isinstance(n.ops[0], (ast.Eq, ast.In)):
            vals = _const_strs(n.comparators[0])
            if is_(n.left, "key", key_vars):
                keys.update(vals)
            elif is_(n.left, "character", char_vars):
                chars.update(vals)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "isprintable":
            typeahead = True
    selects = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "on_list_view_selected" for n in klass.body)
    return keys, chars, typeahead, selects


def _spec_name(textual: str) -> str:
    if textual in _ALIASES:
        return _ALIASES[textual]
    if len(textual) == 1:            # 글자 키는 글자 그대로(`+` 를 `-` 로 바꾸면 안 된다)
        return textual
    return textual.replace("+", "-")


# ── 스펙 쪽: 실제로 짓는다 ─────────────────────────────────────────────────────
def _mod(pkg, sub=None):
    name = f"pytmuxlib.plugins.{pkg}" + (f".{sub}" if sub else "")
    return importlib.import_module(name)


async def _specs():
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "sub"))
        with open(os.path.join(tmp, "a.txt"), "w") as f:
            f.write("x")
        out["mdir"] = _mod("mdir").PLUGIN._spec({"path": tmp, "tags": []}, 0, "")
        out["ncd"] = _mod("ncd").PLUGIN._open_tree({"path": tmp, "cwd": tmp})
    p4 = _mod("p4-show-submitted-changelists")
    rows = [{"change": "1", "when": "2026/11/13 00:00", "user": "u", "desc": "d\nmore"}]
    with harness.patched(_mod("p4-show-submitted-changelists", "server"),
                         list_changes_msg=lambda *a: {"rows": rows, "err": None,
                                                      "info": {"port": "p:1"}}):
        out["p4changes"] = p4.PLUGIN._changes_spec(None, None, 1, None)
    found = [{"id": "s1", "cwd": "/w", "title": "t", "project": "w", "mtime": 0}]
    with harness.patched(_mod("claude-resume", "sessions"), list_sessions=lambda **kw: found):
        out["claude-resume"] = await _mod("claude-resume").PLUGIN._open_spec({})
    srv = types.SimpleNamespace(_namesync_rules=[], _ph_max_lines=2)
    out["namesync"] = _mod("claude-name-sync", "screenspec").open_spec(
        srv, None, "namesync", {})
    # 패널 id 는 줄 key(`<패널>:<자리>`)에 실린다 — 점프가 **이 판의 패널**로 가게(pytmux-587).
    pane = types.SimpleNamespace(id=7, _ph_history=["one", "two\nlines\nthree"])
    sess = types.SimpleNamespace(active_window=types.SimpleNamespace(active_pane=pane))
    out["prompt-history"] = _mod("claude-prompt-history").PLUGIN._open_spec(srv, sess)
    return out


# ── 오라클 ────────────────────────────────────────────────────────────────────
async def test_every_canon_key_is_on_the_gui_spec():
    """정본 `on_key` 가 받는 키 전부가 스펙의 키 표에 있다(또는 core·타이핑 찾기가 쥔다)."""
    specs = await _specs()
    missing = []
    for sid, (pkg, fname, cls, core_extra) in _CANON.items():
        keys, chars, typeahead, selects = _canon(pkg, fname, cls)
        assert keys or chars or selects, f"{sid}: 정본에서 키를 하나도 못 읽었다(공허)"
        spec = specs[sid]
        table = spec.get("keys") or {}
        for k in sorted(keys | chars):
            name = _spec_name(k)
            if name in table or name in _CORE or name in core_extra:
                continue
            if name == "enter" and "enter" in table:
                continue
            # 찾는 중의 Backspace 는 타이핑 찾기의 것이다(정본 ncd 는 그것뿐이다).
            if name == "backspace" and typeahead and spec.get("typeahead"):
                continue
            # namesync 의 j/k 는 클라 로컬 이동이다(`@down`/`@up`).
            missing.append(f"{sid}: 정본 키 {k!r}(→ {name!r})가 스펙에 없다")
        if selects and "enter" not in table:
            missing.append(f"{sid}: 정본은 목록 선택(Enter)을 받는데 스펙에 enter 가 없다")
        if typeahead and not spec.get("typeahead"):
            missing.append(f"{sid}: 정본에는 타이핑 찾기가 있는데 스펙이 선언하지 않았다")
        if not typeahead and spec.get("typeahead"):
            missing.append(f"{sid}: 정본에 없는 타이핑 찾기를 스펙이 선언했다")
    assert not missing, "\n".join(missing)


async def test_the_gui_spec_carries_no_key_the_canon_does_not_have():
    """스펙만 가진 키가 없다 — 정본 글자를 먹는 별칭(종전 mdir `c m d …`·ncd `c`)이 그 부류다."""
    specs = await _specs()
    extra = []
    for sid, (pkg, fname, cls, _core) in _CANON.items():
        keys, chars, _ta, selects = _canon(pkg, fname, cls)
        canon = {_spec_name(k) for k in keys | chars}
        if selects:
            canon.add("enter")
        for name in sorted((specs[sid].get("keys") or {})):
            if name not in canon:
                extra.append(f"{sid}: 스펙 키 {name!r} 는 정본 `{cls}.on_key` 에 없다")
    assert not extra, "\n".join(extra)


def test_the_reader_is_not_vacuous():
    """자가 실제로 읽는지 — 정본 mdir 의 알려진 손 몇이 읽힌 키에 들어 있다."""
    keys, chars, typeahead, _sel = _canon("mdir", "screen.py", "_MdirView")
    assert {"space", "insert", "delete", "shift+enter", "ctrl+o", "alt+c"} <= keys, keys
    assert {"+", "-", "*", ".", "\\"} <= chars, chars
    assert typeahead
    keys, _c, ta, _s = _canon("claude-name-sync", "screen.py", "NameSyncScreen")
    assert {"a", "e", "d", "j", "k", "enter"} <= keys and not ta, keys


# ── 판마다: 무엇이 실제로 나가나 ───────────────────────────────────────────────
async def test_typeahead_rows_carry_the_names_the_canon_matches():
    """찾기가 견줄 이름 — mdir 은 `..`·드라이브를 건너뛰고(빈 이름) 파일 이름 그대로."""
    specs = await _specs()
    rows = specs["mdir"]["rows"]
    assert rows[0]["find"] == "", rows[0]
    assert {r["find"] for r in rows if r["find"]} >= {"sub", "a.txt"}, rows
    assert all(r["find"] for r in specs["ncd"]["rows"]), specs["ncd"]["rows"]
    assert specs["mdir"]["typeahead"]["esc_clears"] is True
    assert specs["ncd"]["typeahead"]["esc_clears"] is False
    assert specs["ncd"]["typeahead"]["server"] == "find"


def test_the_shared_jump_rule_matches_prefix_first_then_substring():
    from pytmuxlib.typeahead import find_jump
    names = ["", "alpha", "beta", "xalphab", "Gamma"]
    assert find_jump(names, 0, "al") == 1
    assert find_jump(names, 2, "al") == 1            # 접두 일치가 포함보다 먼저
    assert find_jump(names, 0, "lph") == 1           # 접두가 없으면 포함
    assert find_jump(names, 4, "GAM") == 4           # 대소문자 무시 · 지금 줄 포함
    assert find_jump(names, 0, "zz") is None
    assert find_jump(names, 0, "") is None


async def test_p4_rows_and_title_are_the_canon_ones():
    specs = await _specs()
    row = specs["p4changes"]["rows"][0]
    assert row["label"] == "@1" and row["cols"] == ["2026/11/13 00:00", "u", "d"], row
    assert "{" not in specs["p4changes"]["title"], specs["p4changes"]["title"]
    assert specs["p4changes"]["title"] == "submitted changelists — p:1"


async def test_prompt_history_is_oldest_first_with_multi_line_preview():
    spec = (await _specs())["prompt-history"]
    assert [r["key"] for r in spec["rows"]] == ["7:0", "7:1"]
    assert spec["selected"] == 1
    # 미리보기 2줄 — 넘친 프롬프트는 마지막 표시 줄에 `…`.
    assert spec["rows"][1]["label"] == "two" and spec["rows"][1]["more"] == ["lines …"]
    assert spec["i18n"]["hint"]["args"] == {"n": "2"}, spec["i18n"]


def test_prompt_history_plus_minus_changes_and_persists_the_preview_lines():
    ph = _mod("claude-prompt-history")
    saved, broadcast = [], []
    srv = types.SimpleNamespace(_ph_max_lines=2, _save_opts=lambda: saved.append(1),
                                _broadcast_session=lambda s: broadcast.append(s))
    pane = types.SimpleNamespace(id=7, _ph_history=["a\nb\nc\nd"])
    sess = types.SimpleNamespace(active_window=types.SimpleNamespace(active_pane=pane))
    spec = ph.PLUGIN.plugin_screen(srv, sess, {"id": "prompt-history", "do": "lines+",
                                               "row": 0})
    assert srv._ph_max_lines == 3 and saved and broadcast
    assert spec["rows"][0]["more"] == ["b", "c …"], spec["rows"][0]
    ph.PLUGIN.plugin_screen(srv, sess, {"id": "prompt-history", "do": "lines+", "row": 0})
    assert srv._ph_max_lines == 3                    # 상한 3
    ph.PLUGIN.plugin_screen(srv, sess, {"id": "prompt-history", "do": "lines-", "row": 0})
    assert srv._ph_max_lines == 2


def test_ncd_newpane_splits_there_and_mdir_tree_does_not_cd_the_pane():
    ncd = _mod("ncd").PLUGIN
    splits, sent = [], []
    srv = types.SimpleNamespace(split_pane=lambda s, o, path=None: splits.append((o, path)),
                                _broadcast_session=lambda s: None)
    with harness.patched(type(ncd), _send_to_pane=lambda self, sv, ss, t: sent.append(t)):
        out = ncd.plugin_screen(srv, None, {"id": "ncd", "do": "newpane",
                                            "input": "/tmp", "state": {}})
        assert out == {"t": "plugin_screen_close", "id": "ncd"}
        assert splits == [("lr", "/tmp")], splits
        # mdir 의 F10 이 연 트리 — 패널 cd 없이 경로만 실어 닫는다(정본 콜백 = mdir 만 이동).
        state = {"ncd": {"pick_only": True}}
        out = ncd.plugin_screen(srv, None, {"id": "ncd", "do": "into",
                                            "input": "/tmp", "state": state})
        assert out["input"] == "/tmp" and sent == [], sent
        # `:ncd` 로 연 트리의 Enter 는 패널에 cd 한다(대조군).
        ncd.plugin_screen(srv, None, {"id": "ncd", "do": "into", "input": "/tmp",
                                      "state": {}})
        assert sent and "/tmp" in sent[0], sent


def test_ncd_server_find_opens_the_tree_to_the_match_or_says_nothing():
    ncd = _mod("ncd").PLUGIN
    with tempfile.TemporaryDirectory() as tmp:
        deep = os.path.join(tmp, "aa", "bb", "needle_dir")
        os.makedirs(deep)
        mine = {"path": tmp, "cwd": tmp}
        ncd._open_tree(mine)
        mine["root"] = tmp
        spec = ncd._found(mine, "needle")
        assert spec["rows"][spec["selected"]]["key"].endswith("needle_dir"), spec["selected"]
        assert spec["typeahead"]["query"] == "needle"
        # 못 찾으면 아무것도 안 보낸다(`None` 이면 서버가 「화면 없음」 알림을 띄운다).
        assert ncd._found(mine, "no-such-thing-zz") == []


async def test_mdir_root_invert_tagmask_and_newpane():
    mdir = _mod("mdir").PLUGIN
    with tempfile.TemporaryDirectory() as tmp:
        for n in ("a.txt", "b.md", "c.txt"):
            with open(os.path.join(tmp, n), "w") as f:
                f.write("x")
        mine = {"path": tmp, "tags": []}
        mdir._spec(mine, 0, "")
        # `+` → 마스크 물음 → 답이 맞는 것만 태그.
        ask = mdir.plugin_screen(None, None, {"id": "mdir", "do": "tagmask",
                                              "state": {"mdir": mine}})
        assert ask["kind"] == "prompt", ask
        mdir._apply(mine, "*.txt", 0)
        assert sorted(os.path.basename(t) for t in mine["tags"]) == ["a.txt", "c.txt"]
        # `*` → 반전.
        mine["tags"] = [os.path.join(tmp, "a.txt")]
        await _awaited(mdir.plugin_screen(None, None, {
            "id": "mdir", "do": "invert", "state": {"mdir": mine}}))
        assert sorted(os.path.basename(t) for t in mine["tags"]) == ["b.md", "c.txt"]
        # `\` → 루트.
        spec = await _awaited(mdir.plugin_screen(None, None, {
            "id": "mdir", "do": "root", "state": {"mdir": mine}}))
        assert mine["path"] == os.path.abspath(os.sep), mine["path"]
        assert spec["id"] == "mdir"
        # ⇧Enter → 그 디렉터리에서 새 패널.
        splits = []
        srv = types.SimpleNamespace(
            split_pane=lambda s, o, path=None: splits.append(path),
            _broadcast_session=lambda s: None)
        mine["path"] = tmp
        out = mdir.plugin_screen(srv, None, {"id": "mdir", "do": "newpane",
                                             "input": os.path.join(tmp, "a.txt"),
                                             "state": {"mdir": mine}})
        assert out["t"] == "plugin_screen_close" and splits == [tmp], splits


async def _awaited(x):
    import inspect
    return (await x) if inspect.isawaitable(x) else x
