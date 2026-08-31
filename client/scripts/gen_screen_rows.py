#!/usr/bin/env python3
"""정본이 **화면 모양마다 줄에 싣는 칸**을 전수로 뽑아 픽스처로 남긴다.

# 왜 이 자가 필요한가 (pytmux-33 ⓖ3 · 축 ⑶ "플러그인 화면" 의 마지막 조각)

ⓖ3(전면 1:1 대조)이 "못 재는 축" 넷을 셌고, 플러그인 축은 세 걸음이다.

 ① **화면 모양**(`kind`)을 GUI 가 다 아나 — `gen_plugin_screens.py`
 ② 화면이 내는 **`do` 이름**을 정본이 다 받나 — `tests/test_plugin_do_wiring.py`
 ③ **화면 «안»의 어휘** — 이 파일. 이슈가 그 자리에 적어 둔 물음이 이것이다:

     `rows` 의 `tag`·`depth`·`expand`·`cols` … 모양은 맞는데 **안의 낱말이 갈리면**
     같은 화면이 다르게 읽힌다.

①이 초록이어도 ③은 갈릴 수 있다 — 그리고 실제로 갈려 있었다. 정본은 기간 판을
계층 트리로 내는데(줄마다 `depth`·`expand`) GUI 의 `"table"` 갈래가 그 두 칸을 **안
읽어** 판이 평면이었다(pytmux-419 ③ · CL 74520). `"list"` 갈래는 같은 두 칸을 옳게
읽고 있었으므로, 「GUI 가 그 칸을 아나」로 물었으면 초록이다. **모양마다 따로 물어야
보인다.**

⛔ **이 부류는 사람 눈으로 안 잡힌다.** 빠진 칸은 그냥 «안 그려질» 뿐이라 화면은
멀쩡해 보이고(평면 목록도 목록이다), 정본 쪽은 늘 맞으므로 나란히 굽지 않는 한
비교 대상도 없다. pytmux-33 의 2026-09-01 코멘트가 *"1:1 대조를 사람 눈으로 하면 이
부류는 안 잡힌다"* 고 적은 그 자리다.

# 어떻게 재나 — 왜 부르지 않고 읽나

`gen_plugin_screens.py` 머리말이 적은 것과 **같은 이유**로 드라이브가 막힌다: 스펙을
내는 자리의 절반이 비동기라 인형에 대고 불러도 dict 가 안 나오고, `prompt`·`confirm`
판은 화면 **안**에서 물을 때만 나온다. 그래서 여기서도 **소스를 읽는다.**

⛔ **모양 판정을 여기서 다시 적지 않는다** — `kind` 를 푸는 술어(글자면 그대로,
매개변수면 공장의 호출부를 훑는다)는 `gen_plugin_screens.py` 한 곳이 쥐고 이 파일은
그 헬퍼를 **그대로 import 한다**. 두 벌이 되면 같은 질문에 두 술어가 생기고, 갈리는
날 조용한 쪽이 믿긴다(이 저장소의 상습 결함).

여기서 새로 푸는 것은 **`rows` 가 어디서 왔나** 하나다. 이 저장소가 줄을 짓는 모양이
넷이라 넷을 다 따라간다:

 · 리터럴·컴프리헨션 — `rows=[{...} for x in xs]`
 · 이름 — `rows = []` 뒤에 `rows.append({...})` · `rows.extend(_hub_rows(...))`
 · **되돌려 받는 것** — `rows, note = _tree_rows(server, opened)`
 · ☠ **밖으로 채우는 것**(out-param) — `rows = []` 뒤에 `self._rows(mine, root, 0, rows)`
   가 그 리스트에 **직접 담는다**(ncd 의 트리). 이것을 안 따라가면 ncd 의 `list` 판이
   *"줄에 아무 칸도 안 싣는다"* 로 세지고, 그러면 이 자는 **그 화면에 대해 아무것도
   안 재면서 초록**이 된다 — 가장 나쁜 실패다.

그리고 칸은 dict 리터럴에만 있는 것이 아니다: `row["until"] = until` 처럼 **나중에
붙는 칸**이 있다(정본 한도 판의 리셋 시각 · pytmux-371 ④). 그 자리를 안 보면 `until`
이 어느 모양에도 안 실린 것으로 세진다.

⛔ **못 푼 자리를 초록으로 위장하지 않는다**(저장소 규율). 못 따라간 `rows` 는
`unresolved` 에 자리와 함께 남고, 적합성 테스트가 그 목록이 **비었는지** 단언한다.

출력: `crates/proto/tests/fixtures/screen_rows.json`

    python3 scripts/gen_screen_rows.py [--pytmux ..] [--print]
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ⛔ 모양(`kind`) 판정은 저쪽 한 곳이 쥔다 — 위 머리말 참조.
import gen_plugin_screens as screens  # noqa: E402

DEFAULT_PYTMUX = HERE.parent.parent
DEFAULT_OUT = HERE.parent / "crates" / "proto" / "tests" / "fixtures" / "screen_rows.json"

# ☠ **여기에 「줄이 가질 수 있는 칸」 목록을 두지 않는다.**
#
# 처음에는 뒀다 — `PluginRow` 의 칸들을 적어 두고 «칸이 전부 그 안이면 줄이다» 로
# 가렸다. 그러면 **정본이 새 칸을 내는 순간 그 줄이 통째로 «줄이 아니게» 세진다**:
# 뮤테이션으로 ncd 줄에 칸 하나를 더해 보니 그 화면의 줄이 픽스처에서 사라졌는데
# 게이트는 **초록**이었다(다른 파일이 같은 모양에 줄을 싣고 있어서 「모양이 비었나」
# 관문도 안 울었다). 새 칸이 왔을 때 조용히 **덜 재는 것** — 이 자가 있는 이유가
# 바로 그 부류인데 스스로 그 부류가 된 것이다.
#
# 그래서 가리는 술어는 자리(`key`·`label` 이 있나)뿐이고, **아는 칸인가**는 재는 쪽
# (`crates/proto/tests/screen_row_conformance.rs` 의 `READS`·`ELSEWHERE`)이 한 곳에서
# 묻는다. 모르는 칸은 픽스처에 그대로 실려 그 게이트가 이름을 대고 운다.

# 값을 그대로 흘리는 통과 호출 — `list(rows or [])` 류.
PASSTHROUGH = {"list", "tuple", "sorted", "reversed"}


def _literally_empty(expr) -> bool:
    """`rows=[]` · `rows=()` · `rows=None` — **정말로** 줄이 없는 자리."""
    if expr is None:
        return True
    if isinstance(expr, ast.Constant) and expr.value is None:
        return True
    return isinstance(expr, (ast.List, ast.Tuple, ast.Set)) and not expr.elts


def _keys(node: ast.Dict) -> set[str]:
    return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _is_row(node: ast.Dict) -> bool:
    """줄 dict 인가 — `key`·`label` 중 하나를 드는가.

    ⚠ **칸의 이름은 안 본다** — 위 §「목록을 두지 않는다」의 실측 그대로다. 가리는 것은
    `key`·`label` 뿐이고, 정본의 줄은 예외 없이 둘 중 하나를 든다(그 둘이 각각 «액션에
    실어 보낼 뜻»과 «화면에 적을 글»이라 뺄 수가 없다)."""
    ks = _keys(node)
    return "key" in ks or "label" in ks


def _fn_of(owner, node):
    return owner.get(node)


class Rows:
    """한 파일 안에서 `rows=` 표현이 가리키는 줄 dict 들을 따라간다."""

    def __init__(self, path: pathlib.Path, rel: str, tree: ast.AST):
        self.rel = rel
        self.tree = tree
        self.owner = screens._enclosing(tree)
        self.funcs: dict[str, ast.AST] = {
            f.name: f
            for f in ast.walk(tree)
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.unresolved: list[str] = []
        # ⚠ 「이 표현은 **호출부가 채운다**」 — 공장 안의 `rows=list(rows)` 가 그것이다.
        #   그 자리가 0건인 것은 못 푼 것이 아니라 아직 안 온 것이다(아래 `deferred` ·
        #   `resolve` 가 그 둘을 가른다).
        self.deferred = False

    # ── 칸 ────────────────────────────────────────────────────────────────
    def fields(self, node: ast.Dict) -> set[str]:
        """이 줄 dict 의 칸 — 리터럴의 열쇠 + **나중에 붙는** `row["x"] = …`.

        나중에 붙는 칸을 안 보면 `until`(한도 판의 리셋 시각)이 어느 모양에도 안 실린
        것으로 세진다 — 그리고 그 순간 이 자는 그 칸에 대해 아무것도 안 재게 된다."""
        out = _keys(node)
        fn = _fn_of(self.owner, node)
        held = self._names_bound_to(node, fn)
        if fn is not None:
            for n in ast.walk(fn):
                if not isinstance(n, ast.Assign):
                    continue
                for t in n.targets:
                    if (
                        isinstance(t, ast.Subscript)
                        and isinstance(t.value, ast.Name)
                        and t.value.id in held
                        and isinstance(t.slice, ast.Constant)
                        and isinstance(t.slice.value, str)
                    ):
                        out.add(t.slice.value)
        return out

    def _names_bound_to(self, node: ast.Dict, fn) -> set[str]:
        """그 dict 리터럴이 곧바로 대입된 이름들(`row = {...}`)."""
        if fn is None:
            return set()
        out = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and n.value is node:
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        out.add(t.id)
        return out

    # ── 자료 흐름 ─────────────────────────────────────────────────────────
    def resolve(self, expr, fn) -> set:
        """`rows=` 하나를 푼다 — ☠ **0건으로 풀리면 「못 푼 자리」로 적는다.**

        빈 결과를 통과로 두면 이 자는 그 화면에 대해 **아무것도 안 재면서 초록**이
        된다. 실측으로 그 갈래가 실제로 있었다: ncd 는 `rows = []` 를 만들어 남의
        함수에 **넘겨서** 채우는데(out-param), 그 자리를 안 따라가면 ncd 의 `list`
        판이 통째로 빠지고 — 다른 파일이 같은 모양에 줄을 실으니 「모양이 비었나」
        관문도 안 운다. 조용히 덜 재는 것이 이 자의 가장 나쁜 실패다.

        ⚠ 두 갈래는 0건이어도 옳다: **정말 빈 것**(`rows=[]` — 물음 판·글 판)과
        **호출부가 채우는 것**(공장 안의 `rows=list(rows)`)."""
        self.deferred = False
        got = self.of(expr, fn)
        if got or self.deferred or _literally_empty(expr):
            return got
        self.unresolved.append(
            f"{self.rel}:{getattr(expr, 'lineno', 0)} — rows= 를 따라갔는데 줄이 0건이다"
            f" (빈 목록이 아니다 — 못 따라간 갈래가 있다)"
        )
        return got

    def of(self, expr, fn, seen: set | None = None) -> set:
        seen = set() if seen is None else seen
        if expr is None:
            return set()
        if isinstance(expr, ast.Dict):
            return {expr} if _is_row(expr) else set()
        if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
            out = set()
            for e in expr.elts:
                out |= self.of(e, fn, seen)
            return out
        if isinstance(expr, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return self.of(expr.elt, fn, seen)
        if isinstance(expr, ast.IfExp):
            return self.of(expr.body, fn, seen) | self.of(expr.orelse, fn, seen)
        if isinstance(expr, ast.BoolOp):          # `rows or []`
            out = set()
            for e in expr.values:
                out |= self.of(e, fn, seen)
            return out
        if isinstance(expr, ast.Starred):
            return self.of(expr.value, fn, seen)
        if isinstance(expr, ast.Name):
            return self._of_name(expr, fn, seen)
        if isinstance(expr, ast.Call):
            return self._of_call(expr, fn, seen)
        if isinstance(expr, ast.Constant) and expr.value is None:
            return set()
        self.unresolved.append(
            f"{self.rel}:{getattr(expr, 'lineno', 0)} — rows= 가 낯선 모양이다"
            f" ({type(expr).__name__})"
        )
        return set()

    def _of_name(self, expr: ast.Name, fn, seen) -> set:
        if fn is None or (id(fn), expr.id) in seen:
            return set()
        seen.add((id(fn), expr.id))
        if self._is_param(fn, expr.id):
            # 공장 안의 `rows=list(rows)` — 무엇이 실리나는 **호출부가 안다**.
            self.deferred = True
        out: set = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id == expr.id:
                        out |= self.of(n.value, fn, seen)
                    elif isinstance(t, (ast.Tuple, ast.List)):
                        # `rows, note = _tree_rows(...)` — 되돌려 받는 갈래.
                        for e in t.elts:
                            if isinstance(e, ast.Name) and e.id == expr.id:
                                out |= self.of(n.value, fn, seen)
            elif isinstance(n, ast.AugAssign):
                if isinstance(n.target, ast.Name) and n.target.id == expr.id:
                    out |= self.of(n.value, fn, seen)
            elif isinstance(n, ast.Call):
                f = n.func
                # `rows.append(...)` · `rows.extend(...)`
                if (
                    isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and f.value.id == expr.id
                    and f.attr in ("append", "extend", "insert")
                ):
                    for a in n.args:
                        out |= self.of(a, fn, seen)
                # ☠ **밖으로 채우는 것** — 그 이름을 남의 함수에 넘겨 거기서 담는다.
                elif screens._call_name(n) in self.funcs:
                    callee = self.funcs[screens._call_name(n)]
                    for i, a in enumerate(n.args):
                        if not (isinstance(a, ast.Name) and a.id == expr.id):
                            continue
                        param = self._param_name(callee, i, n)
                        if param is not None and self._appends_to(callee, param):
                            out |= self._rows_in(callee, seen)
        return out

    @staticmethod
    def _is_param(fn, name: str) -> bool:
        a = fn.args
        return any(
            p.arg == name
            for p in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
            + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else [])
        )

    @staticmethod
    def _param_name(callee, idx: int, call: ast.Call) -> str | None:
        args = list(callee.args.posonlyargs) + list(callee.args.args)
        names = [a.arg for a in args]
        if names and names[0] in ("self", "cls") and isinstance(call.func, ast.Attribute):
            names = names[1:]
        return names[idx] if idx < len(names) else None

    @staticmethod
    def _appends_to(fn, name: str) -> bool:
        for n in ast.walk(fn):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == name
                and n.func.attr in ("append", "extend", "insert")
            ):
                return True
        return False

    def _of_call(self, expr: ast.Call, fn, seen) -> set:
        name = screens._call_name(expr)
        if name in PASSTHROUGH:
            out: set = set()
            for a in expr.args:
                out |= self.of(a, fn, seen)
            return out
        callee = self.funcs.get(name)
        if callee is not None:
            return self._rows_in(callee, seen)
        self.unresolved.append(
            f"{self.rel}:{expr.lineno} — rows= 를 못 푼다({name}() 이 이 파일에 없다)"
        )
        return set()

    def _rows_in(self, fn, seen) -> set:
        """줄을 짓는 함수 하나가 내는 줄 dict 전부.

        ⚠ 함수 **안**은 흐름을 안 따라간다 — 줄을 짓는 함수는 짧고 그 안의 dict 는
        전부 그 함수가 내는 줄이다(실측: 이 저장소의 여덟 자리가 다 그렇다). 여기서
        더 좁히면 판정만 무거워지고 «못 셌다»가 늘어난다."""
        if (id(fn), "#body") in seen:
            return set()
        seen.add((id(fn), "#body"))
        return {n for n in ast.walk(fn) if isinstance(n, ast.Dict) and _is_row(n)}


def collect(pytmux_root: pathlib.Path) -> dict:
    root = pytmux_root / "pytmuxlib" / "plugins"
    per: dict[str, set[str]] = {}
    sites: dict[str, set[str]] = {}
    unresolved: list[str] = []

    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(pytmux_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError as exc:                    # 못 읽은 것은 삼키지 않는다
            raise SystemExit(f"파싱 실패 {path}: {exc}")
        rows = Rows(path, rel, tree)

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Dict) and screens._is_spec_dict(node)):
                continue
            fn = _fn_of(rows.owner, node)
            kv = screens._dict_get(node, "kind")
            if kv is None:
                continue                              # ①이 이미 못 푼 자리로 적는다
            lit = screens._literal(kv)
            here = rows.resolve(screens._dict_get(node, "rows"), fn)
            if lit is not None:
                _add(per, sites, lit, here, rows, rel)
                continue
            # 공장(`kind` 가 매개변수) — 호출부마다 **그 호출이 준 `rows=`** 까지 본다.
            slot = (
                screens._param_slot(fn, kv.id)
                if isinstance(kv, ast.Name) and fn is not None
                else None
            )
            if slot is None:
                continue                              # ①이 적는다
            for call in ast.walk(tree):
                if not isinstance(call, ast.Call) or screens._call_name(call) != fn.name:
                    continue
                arg = None
                for kw in call.keywords:
                    if kw.arg == slot[1]:
                        arg = kw.value
                if arg is None and slot[0] is not None and slot[0] < len(call.args):
                    arg = call.args[slot[0]]
                kind = screens._literal(arg) if arg is not None else None
                if kind is None:
                    continue                          # ①이 적는다
                mine = set(here)
                rows_arg = None
                for kw in call.keywords:
                    if kw.arg == "rows":
                        rows_arg = kw.value
                if rows_arg is not None:
                    mine |= rows.resolve(rows_arg, _fn_of(rows.owner, call))
                _add(per, sites, kind, mine, rows, rel)

        unresolved.extend(rows.unresolved)

    return {
        "rows": {k: sorted(v) for k, v in sorted(per.items())},
        "sites": {k: sorted(v) for k, v in sorted(sites.items())},
        "unresolved": sorted(set(unresolved)),
    }


def _add(per, sites, kind: str, nodes, rows: Rows, rel: str) -> None:
    got: set[str] = set()
    for n in nodes:
        got |= rows.fields(n)
    per.setdefault(kind, set()).update(got)
    if got:
        sites.setdefault(kind, set()).add(rel)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pytmux", type=pathlib.Path, default=DEFAULT_PYTMUX)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--print", action="store_true", help="파일 대신 표준출력으로")
    args = ap.parse_args()

    data = collect(args.pytmux)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.print:
        sys.stdout.write(text)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    filled = sum(1 for v in data["rows"].values() if v)
    print(
        f"{args.out}: 줄을 싣는 모양 {filled}/{len(data['rows'])}"
        f" · 못 푼 자리 {len(data['unresolved'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
