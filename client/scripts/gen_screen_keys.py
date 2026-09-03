#!/usr/bin/env python3
"""정본 **화면의 키**를 AST 로 뽑는다 — 「같아 보인다」를 「눌러 봤다」로 (pytmux-454).

# 왜 필요한가

상호작용 계약(`crates/proto/tests/interaction.rs`)의 `canon` 칸은 **사람이 적는 선언**이다.
첫 회차에 「같다」로 적으려던 여덟 줄 중 **일곱이 거짓**이었다(pytmux-273) — 정본 쪽을
기계로 읽는 자가 없어서다. 모드 전이 축(`mode_transition_conformance`)은 정본
`_handle_esc_mode`·`_handle_prefix` 를 AST 로 걸어 그 자리를 메웠지만, **화면**에는 같은
자가 없었다. 이 파일이 그 자다.

# 무엇을 뽑나

`pytmuxlib/clientscreens.py` 의 `*Screen` 클래스마다:

| 칸 | 출처 | 뜻 |
|---|---|---|
| `keys` | `on_key` 의 if/elif 분기 | 그 키를 누르면 **판이 닫히나**(`close`) · 먹고 남나(`consume`) · 아무 일도 없나(`ignore`) |
| `catch_all` | 분기 밖 꼬리 문장 | **제 것이 아닌 키**의 답. 꼬리에 `self.dismiss(...)` 가 있으면 `close`, 없으면 `ignore` |
| `nav` | `self._NAV_KEYS`·`self._NAV` 멤버십 분기 | 포커스(커서)를 옮기는 키 |
| `bindings` | 클래스 `BINDINGS` + 그 판이 만드는 도우미 `ListView` 의 `BINDINGS` | 위젯 바인딩이 먼저 먹는 키 |
| `unreadable` | 못 읽은 분기 | ⛔ **조용히 버리지 않는다** — 못 읽었다고 적고, 그 화면은 「다 쟀다」로 안 센다 |

⛔ **못 읽은 것을 빈 값으로 접지 않는다.** 그러면 그 위에 선 게이트가 *아무것도 안 재면서
초록*이 된다 — `check_licenses.sh` 가 한 번 밟은 그 함정이고, 이 저장소의 생성기들이
전부 "잡힌 것이 0이면 실패"로 그것을 막는다.

# 왜 클래스 이름을 키로 쓰나

`base::Screen::canon_class()` 가 이미 「우리 판 ↔ 정본 클래스」를 안다. 같은 이름을 쓰면
대조표를 또 적을 필요가 없고, 두 표가 서로 다르게 낡는 일도 없다.

    python3 scripts/gen_screen_keys.py [--pytmux ..]
"""

import argparse
import ast
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "screen_keys.json")

SRC = ("pytmuxlib", "clientscreens.py")


def _utf8_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _is_key_expr(node, aliases):
    """이 식이 «눌린 키의 이름» 인가 — `event.key` 또는 그것을 담은 지역 이름."""
    if isinstance(node, ast.Attribute) and node.attr == "key":
        return isinstance(node.value, ast.Name) and node.value.id == "event"
    return isinstance(node, ast.Name) and node.id in aliases


def _key_aliases(fn):
    """`k = event.key` 처럼 키 이름을 담은 지역 변수들."""
    out = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Attribute) and node.value.attr == "key"):
            continue
        if not (isinstance(node.value.value, ast.Name)
                and node.value.value.id == "event"):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                out.add(tgt.id)
    return out


def _const_strs(node):
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [e.value for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def _class_tuple(cls, name):
    """클래스 몸통의 `NAME = ("a", "b", ...)` 를 읽는다."""
    for st in cls.body:
        if not isinstance(st, ast.Assign):
            continue
        for tgt in st.targets:
            if isinstance(tgt, ast.Name) and tgt.id == name:
                return _const_strs(st.value)
    return []


def _test_keys(test, aliases, nav_names):
    """분기 조건에서 «어느 키인가» 를 읽는다.

    돌려주는 것은 `(키들, nav 인가, 남은 조건 글, 못 읽었나)`.
    """
    guard = None
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        # `k in (...) and self._cats` — 첫 항이 키 비교이고 나머지는 **상태 조건**이다.
        head, rest = test.values[0], test.values[1:]
        keys, is_nav, _sub, bad = _test_keys(head, aliases, nav_names)
        if bad:
            return [], False, None, True
        return keys, is_nav, " and ".join(ast.unparse(r) for r in rest), False
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return [], False, None, True
    if not _is_key_expr(test.left, aliases):
        return [], False, None, True
    op, comp = test.ops[0], test.comparators[0]
    if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) \
            and isinstance(comp.value, str):
        return [comp.value], False, guard, False
    if isinstance(op, ast.In):
        names = _const_strs(comp)
        if names:
            return names, False, guard, False
        # `event.key in self._NAV_KEYS` — 클래스가 든 표다.
        if isinstance(comp, ast.Attribute) and comp.attr in nav_names:
            return nav_names[comp.attr], True, guard, False
    return [], False, None, True


def _is_dismiss(stmt):
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Attribute)
            and stmt.value.func.attr == "dismiss"
            and isinstance(stmt.value.func.value, ast.Name)
            and stmt.value.func.value.id == "self")


def _dismisses(body):
    """이 갈래를 타면 **반드시** 닫나 — 갈래의 **제 줄**에 `self.dismiss(...)` 가 있나.

    ⛔ 더 깊이 들어간 것은 세지 않는다. `if` 안에 든 닫기는 **조건부**이고, 그것을
    「닫는다」로 적으면 우리 쪽의 맞는 배선을 갈림으로 신고하게 된다.

    ⛔ **앞에서 빠져나가는 길이 있으면 그 닫기도 조건부다.** `InfoTabsScreen` 의
    `Enter` 는 제 줄에 `self.dismiss(None)` 을 갖지만, 그 앞의 `if` 가 「고른 줄이 동작
    단추면 그것을 돌리고 `return`」 한다 — 즉 닫기는 **거기까지 왔을 때만** 일어난다.
    """
    for st in body:
        if _is_dismiss(st):
            return True
        # 앞선 복합문이 빠져나갈 수 있으면 뒤의 닫기는 조건부다.
        if isinstance(st, (ast.If, ast.For, ast.While, ast.Try)):
            for sub in ast.walk(st):
                if isinstance(sub, ast.Return) or _is_dismiss(sub):
                    return False
    return False


def _dismisses_somewhere(body):
    """어디서든 — 조건 안이라도 — 닫을 수 있나."""
    for node in ast.walk(ast.Module(body=list(body), type_ignores=[])):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "dismiss" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "self":
            return True
    return False


def _dismisses_via(body, methods, depth=2):
    """도우미 메서드를 **한두 겹 따라가면** 닫나.

    ⛔ 이것을 「닫는다」로 접지 않는다. 정본 팔레트의 `Enter` 는
    `await self._select_current()` 를 부르고 닫는 일은 그 안에서 일어나는데, **그 안의
    닫기는 조건부**다(고른 줄이 있나 · 인자 모드인가). 설정 판의 `Enter` 도 같다 —
    `_activate` 는 `link` 형 줄에서만 닫고 나머지는 값을 돌린다.
    ⇒ 한 겹만 보고 「닫는다」로 적으면 **맞는 것을 고치게 된다**(실측 2026-09-03에 그럴
    뻔했다). 그래서 이 부류는 `close_maybe` 로 남기고 **대조에서 뺀 채 이름을 드러낸다**.
    """
    for node in ast.walk(ast.Module(body=list(body), type_ignores=[])):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "self"):
            continue
        if node.func.attr in methods and depth > 0:
            sub = methods[node.func.attr].body
            if _dismisses_somewhere(sub) or _dismisses_via(sub, methods, depth - 1):
                return True
    return False


def _outcome(body, methods, head_stops=False):
    """그 갈래를 타면 무슨 일이 나나 — `close` / `consume` / `ignore`.

    `head_stops` 는 **분기 앞에서 이미 `event.stop()` 했나**다. 정본 `InfoScreen`·
    `InfoTabsScreen` 은 함수 첫 줄에서 한 번 멈추고 그 뒤 갈래에서는 다시 안 멈춘다 —
    그 사실을 안 읽으면 커서를 옮기는 키가 전부 「아무 일도 안 함」으로 잘못 세진다.
    """
    if _dismisses(body):
        return "close"
    if _dismisses_somewhere(body) or _dismisses_via(body, methods):
        return "close_maybe"
    stops = head_stops
    for node in ast.walk(ast.Module(body=list(body), type_ignores=[])):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "stop" and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "event":
            stops = True
    return "consume" if stops else "ignore"


def _chain(node):
    """`if / elif / elif` 사슬을 `(test, body)` 목록과 마지막 `else` 로 편다."""
    out = []
    cur = node
    while True:
        out.append((cur.test, cur.body))
        if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
            cur = cur.orelse[0]
            continue
        return out, cur.orelse


def _head_stops(stmts):
    """분기 **앞**에서 `event.stop()` 을 했나 — 「이 판이 키를 다 먹는다」의 선언이다."""
    for st in stmts:
        if isinstance(st, ast.If):
            break
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) \
                and isinstance(st.value.func, ast.Attribute) \
                and st.value.func.attr == "stop" \
                and isinstance(st.value.func.value, ast.Name) \
                and st.value.func.value.id == "event":
            return True
    return False


def _tail_outcome(stmts, head_stops=False):
    """분기 **밖**의 꼬리 — 제 것 아닌 키가 여기로 떨어진다.

    ⛔ `if` 안이 아니라 함수 몸통에 바로 있는 `self.dismiss(...)` 만 센다. 그것이
    정본 `InfoScreen` 의 「아무 키나 닫기」와 `ConfirmScreen` 의 「모르는 키는 무시」를
    가르는 **유일한** 자리다(pytmux-273 ②③ 이 그 갈림이었다).
    """
    for st in stmts:
        if isinstance(st, ast.If):
            continue
        if isinstance(st, ast.Expr) and _dismisses([st]):
            return "close"
    return "consume" if head_stops else "ignore"


def _bindings(cls):
    """`BINDINGS = [Binding("home", ...), ("ctrl+s", ...)]` 의 키 이름."""
    out = []
    for st in cls.body:
        if not isinstance(st, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "BINDINGS"
                   for t in st.targets):
            continue
        for elt in getattr(st.value, "elts", []):
            first = None
            if isinstance(elt, ast.Call) and elt.args:
                first = elt.args[0]
            elif isinstance(elt, (ast.Tuple, ast.List)) and elt.elts:
                first = elt.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.append(first.value)
    return out


def _helper_widgets(tree):
    """도우미 `ListView` 들의 바인딩 — 그 판이 만들면 그 판의 키다.

    정본은 `Home`/`End`/`PgUp`/`PgDn` 을 **화면 `on_key` 가 아니라** 목록 위젯의
    바인딩으로 처리한다(부모 스크롤 바인딩이 화면보다 먼저 먹어서 — `_NoticeList`
    머리말). 그 사실을 안 읽으면 그 넷이 「정본에 없는 키」로 잘못 세진다.
    """
    return {cls.name: _bindings(cls) for cls in tree.body
            if isinstance(cls, ast.ClassDef)
            and any(isinstance(b, ast.Name) and b.id == "ListView"
                    for b in cls.bases)}


def _uses(cls, names):
    used = []
    for node in ast.walk(cls):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in names:
            used.append(node.func.id)
    return sorted(set(used))


def extract(path):
    with open(path, encoding="utf-8") as fp:
        tree = ast.parse(fp.read(), filename=path)
    helpers = _helper_widgets(tree)
    out = {}
    for cls in tree.body:
        if not (isinstance(cls, ast.ClassDef) and cls.name.endswith("Screen")):
            continue
        nav_names = {}
        for attr in ("_NAV_KEYS", "_NAV"):
            got = _class_tuple(cls, attr)
            if got:
                nav_names[attr] = got
        fn = next((m for m in cls.body
                   if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and m.name == "on_key"), None)

        binds = list(_bindings(cls))
        for helper in _uses(cls, helpers):
            binds += helpers[helper]

        row = {
            "keys": {},
            "nav": [],
            "bindings": sorted(set(binds)),
            "unreadable": [],
        }
        if fn is None:
            # 키의 주인이 화면이 아닌 판(작성창은 `_ComposeTextArea` 가 든다).
            row["on_key"] = False
            row["catch_all"] = None
            out[cls.name] = row
            continue
        row["on_key"] = True

        methods = {m.name: m for m in cls.body
                   if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
        aliases = _key_aliases(fn)
        head_stops = _head_stops(fn.body)
        row["head_stops"] = head_stops
        for st in fn.body:
            if not isinstance(st, ast.If):
                continue
            branches, orelse = _chain(st)
            for test, body in branches:
                keys, is_nav, guard, bad = _test_keys(test, aliases, nav_names)
                if bad:
                    row["unreadable"].append(ast.unparse(test))
                    continue
                effect = _outcome(body, methods, head_stops)
                for name in keys:
                    entry = {"outcome": effect}
                    if guard:
                        entry["guard"] = guard
                    if is_nav:
                        entry["nav"] = True
                        row["nav"].append(name)
                    # 같은 키가 두 갈래에 나오면 **먼저 적힌 것**이 이긴다(if/elif).
                    row["keys"].setdefault(name, entry)
            if orelse:
                row["unreadable"].append("else: " + ast.unparse(orelse[0]).splitlines()[0])
        row["nav"] = sorted(set(row["nav"]))
        row["catch_all"] = _tail_outcome(fn.body, head_stops)
        out[cls.name] = row
    if not out:
        sys.exit(f"{path} 에서 화면 클래스를 하나도 못 찾았다 — 뽑는 방법이 틀렸다")
    measured = sum(1 for r in out.values() if r["keys"])
    if measured < 10:
        sys.exit(f"{path} 에서 키를 읽은 화면이 {measured} 뿐이다 — 뽑는 방법이 틀렸다")
    return out


def main():
    _utf8_stdout()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()

    root = os.path.abspath(args.pytmux)
    path = os.path.join(root, *SRC)
    if not os.path.isfile(path):
        sys.exit(f"정본 화면 모듈을 못 찾았다: {path}")

    screens = extract(path)
    payload = {
        "_comment": "python3 scripts/gen_screen_keys.py 로 생성. 출처 = "
                    "pytmuxlib/clientscreens.py 의 화면 클래스 on_key/BINDINGS/_NAV_KEYS. "
                    "crates/proto/tests/screen_key_conformance.rs 가 이 표의 키를 "
                    "base::Screens 에 실제로 눌러 대조한다(pytmux-454). "
                    "플러그인이 준 판(Tier C)은 여기 없다 — 그쪽은 스펙이 키를 정하고 "
                    "plugin_screen_conformance.rs 가 잰다.",
        "screens": dict(sorted(screens.items())),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=False)
        fp.write("\n")

    keys = sum(len(r["keys"]) for r in screens.values())
    unread = sum(len(r["unreadable"]) for r in screens.values())
    print(f"{args.out} — 화면 {len(screens)} · 키 {keys} · 못 읽은 분기 {unread}")


if __name__ == "__main__":
    main()
