"""화면 스펙이 **내는** `do` 이름을 정본이 **다 받나** — 죽은 클릭·죽은 키의 자.

# 왜 이 자리를 재나 (pytmux-33 ⓖ3 · 축 ⑶ "플러그인 화면·클릭존" 의 남은 절반)

ⓖ3(전면 1:1 대조)이 "못 재는 축" 넷을 셌고, 그중 플러그인 축은 두 걸음이다.
첫 걸음(**화면 모양** `kind` 이 GUI 에 다 있나)은 `gen_plugin_screens.py` 가 세웠다.
그 다음 걸음 — *"그 화면 안에서 키를 눌렀을 때 무슨 일이 나나"* — 은 아직 아무도
안 쟀고, 이슈가 그 자리에 적어 둔 물음이 이것이다:

    GUI 는 `do` 이름의 **뜻을 모른 채 되돌려 보내는 것**이 계약이다(설계 §4.4).
    그러니 물을 것은 "GUI 가 그 이름을 아나"가 아니라
    **"정본이 내는 `do` 를 정본이 다 받나"** 다.

선언형 화면 스펙(`Registry.plugin_screen`)의 `keys` 표는 `{키: do 이름}` 이고, 클라는
그 표에 있는 키만 먹어 `plugin_action` 으로 **이름 그대로** 되돌려 보낸다. 받는 쪽이
그 이름을 모르면 핸들러는 `None` 을 돌려주고 — **아무 일도 안 난다.** 알림도 없다.
그것이 이 저장소가 "상습 결함" 이라 부르는 그 부류다(ⓡ `close-clock` · pytmux-20 ·
팔레트에 보이는데 안 먹던 줄 스물셋 = pytmux-35).

⛔ **글자 하나만 갈려도 조용하다.** 표는 플러그인이 짓고 핸들러도 같은 플러그인이
짓는데, 둘은 파일 안에서 수백 줄 떨어져 있고 서로를 안 부른다. `"alt-f": "mask"` 를
`"mask-set"` 으로 고치면 그 키는 그 순간 죽고 **어떤 시험도 안 운다** — 이 파일이
서기 전까지는.

# 어떻게 재나 — 왜 부르지 않고 읽나

옆 자(`gen_plugin_server_actions.py`)는 정본을 **불러서** 판정하지만, 여기서는 그
길이 `gen_plugin_screens.py` 머리말이 적은 것과 **같은 이유로** 막힌다:

 · 스펙을 내는 자리의 절반이 **비동기**다(`mdir`·`ncd`·`p4changes`·`claude-resume` 가
   코루틴/Future 를 준다). 인형에 대고 불러도 `keys` 가 든 dict 는 안 나온다.
 · `prompt`·`confirm` 판은 화면 **안**에서 물을 때만 나오므로 열기만 눌러서는
   한 번도 안 보인다 — 그런데 그 판들이야말로 `mask-apply`·`apply` 처럼
   **한 번만 쓰이는 이름**을 든다.
 · 부르면 **부작용이 난다.** `do` 를 하나씩 던져 보는 판정은 `delete`·`apply` 를
   진짜로 던지는 일이라, 재는 것이 상자를 만진다(pytmux-194 가 그 값을 치렀다).

그래서 양쪽 다 **소스를 읽는다.**

 · 내는 쪽 — `{"t": "plugin_screen", …}` dict 리터럴의 `id` 와 `keys`. 값이 글자면
   그대로, **매개변수면**(스펙을 한 곳에서 짓는 공장 — `screenspec._spec` ·
   `namesync._ask`) 그 공장의 호출부를 다시 훑어 실제로 실리는 글자를 모은다.
 · 받는 쪽 — 그 플러그인 꾸러미 안에서 `do` 를 글자와 견주는 자리 전부
   (`do == "x"` · `do in (…)` · `do != "x"` · `do.startswith("x-")` · `do in ACTIONS`).
   화면 id 로 좁히는 관문(`if sid == "x"` · `if req.get("id") != "x": return`)을
   따라가며 **그 이름이 어느 화면에서 먹히나**까지 잰다.

⛔ **못 푼 자리를 초록으로 위장하지 않는다**(저장소 규율). 글자로 못 푼 자리는
`unresolved` 에 자리와 함께 남고 아래 ③ 이 그 목록이 **비었는지** 단언한다 — 비면
"전수로 셌다"가 참이고, 안 비면 **무엇을 못 셌는지가 보인다.**

# 무엇을 아직 안 재나 (다음 사람에게)

**오버레이의 클릭존·키**(`plugin_triggers` → `plugin_overlay_action`)는 여기 없다.
같은 부류지만 살아 있는 길이 **셋**이라(이름을 `plugin_overlay_action` 이 받거나 ·
존이 여는 화면 이름 `opens` 를 싣거나 · 칠 글자 `send` 를 싣거나) 판정이 다르고,
그 셋을 한 자에 넣으면 이 파일이 두 질문을 지게 된다. `claude-code` 쪽은 이미
코드가 스스로 막고 있고(`if not (opens or send): continue`) `calendar` 쪽은
`ACTIONS` 한 표를 두 경로가 함께 본다 — 그래서 급한 순서는 이 자리가 먼저였다.

# 어디까지 좁혀 재나 (이 자의 한계 — 적어 두는 편이 낫다)

받는 쪽 항목은 화면 id 관문을 따라 좁힌다. 그래도 **관문 밖에서** 견주는 자리
(`if do == "open"` 처럼 id 를 가리기 «전»)는 그 꾸러미의 **모든 화면**에 먹히는
것으로 센다 — 실측으로 받는 쪽 53 중 38 이 좁혀졌고 나머지 15 가 그 부류다.
그래서 이 자는 *"어느 화면에서든 안 받는 이름"* 은 잡고, *"옆 화면에서는 받지만
이 화면에서는 안 받는 이름"* 은 관문이 있는 자리에서만 잡는다.
⛔ **넓히려고 꾸러미 전체의 `do` 를 세지 마라** — 그러면 남의 변수 `do` 까지 이름으로
세어 이 자가 조용히 거짓 초록이 된다(그래서 `_is_handler` 가 자리를 둘로 좁힌다).

# 실측 (2026-08-23 · 이 CL)

- 조사: 꾸러미 **7** · 화면 id **14** · 스펙 자리 **22** · 유일 `(화면, do)` **46**
  (이름으로는 **40**) · 죽은 이름 **0** · 못 푼 자리 **0**.
- **이 자가 실제로 무나** — 네 번 일부러 깨서 잰다(전부 되돌렸다):
  ⑴ 키 표의 이름 한 글자(`ncd` 의 `cd`→`cd-here`) ⑵ 받는 쪽 갈래 하나(`mdir` 의
  `hidden`) ⑶ **남의 화면 이름**(`pc-queue` 가 `toggle` 을 낸다 — id 로 안 좁히면
  통과한다) ⑷ 글자로 못 읽는 키 표(`"cl" + "ear"`). ⑴⑵⑶ 은 ①이, ⑷ 는 ②가 붉었다.

- **값**: 조사 한 번이 실측 10.4초다(부하 22~28 인 상자 · 한가하면 1초 남짓). 그중
  6초가 꾸러미 40여 편의 `ast.parse` 다. 그래서 ⑴ 조사를 한 회차 안에서 **한 번만**
  하고(`_CACHE`) ⑵ 트리를 함부로 두 번 안 내려간다(`_enclosing`·`_outermost_funcs`).
  ⛔ 그 둘을 되돌리면 22.5초 × 오라클 셋이 된다 — 게이트가 1분 반을 더 먹는다.

지금 이 자는 아무것도 안 고친다 — 값은 **다음에 갈릴 때** 나온다
(`gen_plugin_screens.py` 가 화면 모양에서 그랬던 것과 같은 자리다).
"""

import ast
import os

# ⛔ `harness` 를 안 읽는다 — 이 자는 **소스만** 읽으므로 정본을 import 할 이유가 없고,
#    import 하면 재는 쪽이 재는 대상을 띄우게 된다(pytmux-194 가 그 값을 치렀다).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "pytmuxlib", "plugins")

#: 화면 스펙임을 알아보는 표식 — 정본 훅의 계약이 정한 값이다(`Registry.plugin_screen`).
SPEC_MARK = "plugin_screen"


# ── 자잘한 AST 도우미 ────────────────────────────────────────────────────────
def _lit(node):
    """글자 상수면 그 값, 아니면 `None`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dget(node, key):
    """dict 리터럴에서 글자 열쇠 하나(없으면 `None`)."""
    if not isinstance(node, ast.Dict):
        return None
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    return None


def _const_value(node):
    """모듈 상수로 쓸 수 있는 값만 푼다 — 글자 · 글자 묶음 · `{글자: 글자}`.

    다른 모양(계산식·f-string·중첩)은 `None` 이다. **넓게 푸는 것이 위험**이라
    좁게 둔다: 못 푼 것은 아래에서 `unresolved` 로 남아 눈에 보이지만, 잘못 푼 것은
    조용히 초록이 된다."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        vals = [_lit(e) for e in node.elts]
        return tuple(vals) if all(v is not None for v in vals) else None
    if isinstance(node, ast.Dict):
        keys = [_lit(k) for k in node.keys]
        if any(k is None for k in keys):
            return None
        # 값이 글자가 아닌 표(달력의 `ACTIONS` = {글자: 수})도 **열쇠는** 쓸모가 있다 —
        # 그래서 값은 `None` 으로 두고, 값이 필요한 쪽이 그 `None` 을 보고 운다.
        return {k: _lit(v) for k, v in zip(keys, node.values)}
    return None


def _module_consts(tree):
    """모듈 맨 위의 `NAME = <상수>` 들. 화면 id·키 표가 상수로 나가는 자리가 많다."""
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        val = _const_value(node.value)
        if val is not None:
            out[tgt.id] = val
    return out


def _names_of(value):
    """상수 값 → 그것이 담은 **이름 집합**(dict 은 열쇠 · 묶음은 원소)."""
    if isinstance(value, dict):
        return set(value)
    if isinstance(value, (tuple, list, set)):
        return {v for v in value if isinstance(v, str)}
    if isinstance(value, str):
        return {value}
    return set()


def _enclosing(tree):
    """`{자식 노드: 그 노드를 품은 «가장 안쪽» 함수}` — `id`·`keys` 가 매개변수인지
    가리려면 필요하다(`gen_plugin_screens.py` 와 같은 처방).

    ⚠ 한 번만 내려간다. 「함수마다 그 아래를 다시 훑기」로 지으면 큰 파일에서 제곱이
    되고, 이 꾸러미에는 1,800줄짜리가 있다 — 실측으로 조사 한 번이 22.5초였다."""
    out = {}

    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            here = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if here is not None:
                out[child] = here
            visit(child, here)

    visit(tree, None)
    return out


def _param_slot(fn, name):
    """그 이름이 이 함수의 매개변수면 `(위치, 이름, 기본값)`. 아니면 `None`.

    `self`/`cls` 는 자리에서 뺀다 — 호출부는 `self.f(x)` 로 부르므로 위치가 하나 밀린다.
    기본값도 함께 든다: 그 자리를 **안 주는 호출부**가 무엇을 싣는지는 기본값이 답이다
    (`namesync._ask(act="apply")`)."""
    args = list(fn.args.posonlyargs) + list(fn.args.args)
    names = [a.arg for a in args]
    defaults = [None] * (len(args) - len(fn.args.defaults)) + list(fn.args.defaults)
    if name in names:
        idx = names.index(name)
        default = defaults[idx]
        if names and names[0] in ("self", "cls"):
            idx -= 1
            if idx < 0:
                return None
        return (idx, name, default)
    for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
        if a.arg == name:
            return (None, name, d)          # 키워드로만 받는다
    return None


def _call_name(call):
    """`f(...)` · `self.f(...)` · `mod.f(...)` 를 전부 `f` 로 읽는다."""
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _arg_at(call, slot, kwname):
    """호출부에서 그 자리에 실린 노드(안 실었으면 `None`)."""
    for kw in call.keywords:
        if kw.arg == kwname:
            return kw.value
    if slot is not None and slot < len(call.args):
        return call.args[slot]
    return None


# ── ① 내는 쪽 — 화면 스펙의 `keys` 표 ───────────────────────────────────────
def _resolve_id(node, fn, consts):
    """스펙의 `id` → `("lit", 글자)` · `("param", 자리)` · `("?", 사유)`."""
    if node is None:
        return ("?", "스펙에 id 칸이 없다")
    lit = _lit(node)
    if lit is not None:
        return ("lit", lit)
    if isinstance(node, ast.Name):
        if fn is not None:
            slot = _param_slot(fn, node.id)
            if slot is not None:
                return ("param", slot)
        val = consts.get(node.id)
        if isinstance(val, str):
            return ("lit", val)
        return ("?", f"상수 {node.id} 를 못 푼다")
    return ("?", ast.dump(node)[:60])


def _resolve_keys(node, fn, consts):
    """스펙의 `keys` → `(글자로 푼 do 들, 매개변수 자리들, 못 푼 사유들)`.

    매개변수 자리는 `(위치, 이름, 기본값, 역할)` 이고 **역할이 둘**이다:
    `"table"` = 그 자리에 **표 전체**가 실린다(`screenspec._spec(keys=…)`) ·
    `"name"` = 그 자리에 **이름 하나**가 실린다(`namesync._ask(act=…)` — 표는
    `{"enter": act}` 라 값 한 칸만 매개변수다). 둘을 안 가르면 호출부에서 글자를
    표로 읽으려다 "못 푼다" 가 된다.

    칸이 아예 없으면 **선언한 키가 없다**(빈 집합)다 — 못 푼 것과 다르다."""
    if node is None or (isinstance(node, ast.Constant) and node.value is None):
        return (set(), [], [])
    if isinstance(node, ast.Call) and _call_name(node) == "dict":
        inner = node.args[0] if node.args else None
        if inner is None:
            return (set(), [], [] if not node.keywords else ["dict(키=값) 모양"])
        return _resolve_keys(inner, fn, consts)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        # `keys or {}` — 뜻은 앞엣것이 진다(뒤는 "안 주면 빈 표").
        return _resolve_keys(node.values[0], fn, consts)
    if isinstance(node, ast.Dict):
        got, params, unres = set(), [], []
        for v in node.values:
            lit = _lit(v)
            if lit is not None:
                got.add(lit)
                continue
            slot = _param_slot(fn, v.id) if (fn is not None and isinstance(v, ast.Name)) else None
            if slot is not None:
                params.append((*slot, "name"))
            else:
                unres.append(f"keys 의 값이 글자가 아니다({ast.dump(v)[:40]})")
        return (got, params, unres)
    if isinstance(node, ast.Name):
        if fn is not None:
            slot = _param_slot(fn, node.id)
            if slot is not None:
                return (set(), [(*slot, "table")], [])
        val = consts.get(node.id)
        if isinstance(val, dict):
            vals = {v for v in val.values() if v is not None}
            if any(v is None for v in val.values()):
                return (vals, [], [f"상수 {node.id} 에 글자가 아닌 값이 있다"])
            return (vals, [], [])
        if val is not None:
            return (_names_of(val), [], [])
        return (set(), [], [f"상수 {node.id} 를 못 푼다"])
    return (set(), [], [ast.dump(node)[:60]])


def _emitters(rel, tree, consts):
    """이 파일이 내는 `(화면 id, {do…}, 자리)` 들과 못 푼 자리들."""
    owner = _enclosing(tree)
    calls = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            calls.setdefault(_call_name(node), []).append(node)

    out, unres = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        mark = _dget(node, "t")
        if not (isinstance(mark, ast.Constant) and mark.value == SPEC_MARK):
            continue
        where = f"{rel}:{node.lineno}"
        fn = owner.get(node)
        kind, sid = _resolve_id(_dget(node, "id"), fn, consts)
        kset, kparams, kunres = _resolve_keys(_dget(node, "keys"), fn, consts)
        unres += [f"{where} — {u}" for u in kunres]
        if kind == "?":
            unres.append(f"{where} — id 를 못 푼다({sid})")
            continue
        if kind == "lit" and not kparams:
            out.append((sid, kset, where))
            continue

        # 공장(스펙을 한 곳에서 짓는 함수) — **정의한 파일 안에서** 호출부를 찾는다.
        # 이름만으로 넓히면 남의 동명 함수를 끌어와 거짓 실패를 만든다
        # (`gen_plugin_screens.py` 가 실측으로 물린 자리 그대로).
        if fn is None:
            unres.append(f"{where} — 매개변수인데 품은 함수가 없다")
            continue
        sites = calls.get(fn.name) or []
        if not sites:
            unres.append(f"{where} — {fn.name}() 를 부르는 자리가 없다 — 공장이 죽었나")
            continue
        for call in sites:
            at = f"{rel}:{call.lineno}"
            if kind == "lit":
                sid_here = sid
            else:
                slot, nm, dflt = sid
                arg = _arg_at(call, slot, nm)
                sid_here = _lit(arg if arg is not None else dflt)
                if sid_here is None:
                    unres.append(f"{at} — {fn.name}() 의 id 가 글자가 아니다")
                    continue
            here, bad = set(kset), False
            for slot, nm, dflt, role in kparams:
                arg = _arg_at(call, slot, nm)
                if arg is None:
                    arg = dflt
                if arg is None:
                    continue            # 그 자리를 안 줬다 = 키 표 없음
                if role == "name":
                    lit = _lit(arg)
                    if lit is None:
                        unres.append(f"{at} — {fn.name}() 의 {nm} 가 글자가 아니다")
                        bad = True
                        break
                    here.add(lit)
                    continue
                got, sub, u = _resolve_keys(arg, None, consts)
                if sub or u:
                    unres.append(f"{at} — {fn.name}() 의 keys 를 못 푼다")
                    bad = True
                    break
                here |= got
            if not bad:
                out.append((sid_here, here, at))
    return out, unres


# ── ② 받는 쪽 — `do` 를 글자와 견주는 자리 전부 ─────────────────────────────
def _get_key(node, key):
    """`<무엇>.get("key")` 꼴인가."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and node.args and _lit(node.args[0]) == key)


def _do_expr(node):
    return (isinstance(node, ast.Name) and node.id == "do") or _get_key(node, "do")


def _sid_expr(node):
    return (isinstance(node, ast.Name) and node.id == "sid") or _get_key(node, "id")


def _names_from_node(node, consts):
    """`in` 오른쪽 → 이름 집합."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return {v for v in (_lit(e) for e in node.elts) if v is not None}
    if isinstance(node, ast.Dict):
        return {v for v in (_lit(k) for k in node.keys) if v is not None}
    if isinstance(node, ast.Name):
        return _names_of(consts.get(node.id))
    return set()


def _sids_from(test, consts):
    """`if <id> == "x"` · `if <id> in NAMES` → 좁혀지는 화면 id 들(아니면 `None`)."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and _sid_expr(test.left)):
        return None
    op, right = test.ops[0], test.comparators[0]
    if isinstance(op, ast.Eq):
        lit = _lit(right)
        return frozenset({lit}) if lit is not None else None
    if isinstance(op, ast.In):
        got = _names_from_node(right, consts)
        return frozenset(got) if got else None
    return None


def _guard_narrow(st, consts):
    """`if <id> != "x": return` 처럼 **그 뒤를 통째로 좁히는** 관문이면 그 id 들."""
    if not (isinstance(st, ast.If) and not st.orelse):
        return None
    if not any(isinstance(s, (ast.Return, ast.Continue, ast.Raise)) for s in st.body):
        return None
    test = st.test
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and _sid_expr(test.left)):
        return None
    op, right = test.ops[0], test.comparators[0]
    if isinstance(op, ast.NotEq):
        lit = _lit(right)
        return frozenset({lit}) if lit is not None else None
    if isinstance(op, ast.NotIn):
        got = _names_from_node(right, consts)
        return frozenset(got) if got else None
    return None


def _harvest(node, sids, out, consts):
    """이 조각에서 `do` 를 글자와 견주는 자리를 전부 적는다."""
    for n in ast.walk(node):
        if isinstance(n, ast.Compare) and len(n.ops) == 1:
            left, op, right = n.left, n.ops[0], n.comparators[0]
            if _do_expr(left):
                if isinstance(op, (ast.Eq, ast.NotEq)):
                    lit = _lit(right)
                    if lit is not None:
                        out.append((sids, lit, False))
                elif isinstance(op, (ast.In, ast.NotIn)):
                    for v in _names_from_node(right, consts):
                        out.append((sids, v, False))
            elif _do_expr(right) and isinstance(op, (ast.Eq, ast.NotEq)):
                lit = _lit(left)
                if lit is not None:
                    out.append((sids, lit, False))
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "startswith" and _do_expr(n.func.value) and n.args):
            lit = _lit(n.args[0])
            if lit is not None:
                out.append((sids, lit, True))       # 접두 — `sort-` · `cols-`


def _scan_handler(fn, consts, out):
    """핸들러 하나를 훑되 **화면 id 관문을 따라가며** 좁힌다."""

    def body(stmts, sids):
        cur = sids
        for st in stmts:
            narrowed = _guard_narrow(st, consts)
            if narrowed is not None:
                cur = narrowed if cur is None else (cur & narrowed)
                continue
            stmt(st, cur)

    def stmt(st, sids):
        if isinstance(st, ast.If):
            inner = _sids_from(st.test, consts)
            _harvest(st.test, sids, out, consts)
            here = sids if inner is None else (inner if sids is None else sids & inner)
            body(st.body, here)
            body(st.orelse, sids)
            return
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body(st.body, sids)
            return
        if isinstance(st, ast.Try):
            body(st.body, sids)
            for h in st.handlers:
                body(h.body, sids)
            body(st.orelse, sids)
            body(st.finalbody, sids)
            return
        if isinstance(st, (ast.For, ast.AsyncFor)):
            _harvest(st.iter, sids, out, consts)
            body(st.body, sids)
            body(st.orelse, sids)
            return
        if isinstance(st, ast.While):
            _harvest(st.test, sids, out, consts)
            body(st.body, sids)
            body(st.orelse, sids)
            return
        if isinstance(st, (ast.With, ast.AsyncWith)):
            for item in st.items:
                _harvest(item.context_expr, sids, out, consts)
            body(st.body, sids)
            return
        _harvest(st, sids, out, consts)

    body(fn.body, None)


def _is_handler(fn):
    """`do` 를 실제로 지는 함수인가 — 매개변수로 받거나 `req.get("do")` 를 읽는다.

    꾸러미를 통째로 훑으면 **남의 `do`** 를 받는 이름으로 세어 이 자가 거짓 초록이
    된다. 그래서 자리를 이 둘로 좁힌다."""
    if _param_slot(fn, "do") is not None:
        return True
    return any(_get_key(n, "do") for n in ast.walk(fn))


def _outermost_funcs(tree):
    """**안 겹치게** 함수를 고른다 — 바깥 함수 하나만 든다(안쪽은 그 안에서 함께 훑는다).

    한 번만 내려간다(`_enclosing` 과 같은 이유)."""
    out = []

    def visit(node, in_fn):
        for child in ast.iter_child_nodes(node):
            is_fn = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_fn and not in_fn:
                out.append(child)
            visit(child, in_fn or is_fn)

    visit(tree, False)
    return out


def _accepts(trees):
    """꾸러미 전체의 `(좁혀진 화면 id 들 | None, 이름, 접두인가)` 목록."""
    out = []
    for _rel, tree, consts in trees:
        for fn in _outermost_funcs(tree):
            if _is_handler(fn):
                _scan_handler(fn, consts, out)
    return out


def _takes(accepts, sid, do):
    """그 화면에서 그 이름이 먹히나."""
    for sids, name, prefix in accepts:
        if sids is not None and sid not in sids:
            continue
        if do.startswith(name) if prefix else do == name:
            return True
    return False


# ── 꾸러미 훑기 ──────────────────────────────────────────────────────────────
#: 한 회차 안에서 «같은 트리를 세 번 파싱하지 않게» 들고 있는다. 오라클 셋이 각자
#: 조사를 부르는데 그 값은 소스에서만 나오므로 회차 안에서 안 변한다 — 실측으로 부하가
#: 높은 상자에서 조사 한 번이 30.9초였고, 그것이 그대로 셋이면 게이트가 1분 반을 더 먹는다.
#: ⚠ 일부러 소스를 고쳐 이 자가 무는지 재는 회차는 **다른 프로세스**라 이 보관과 무관하다.
_CACHE = {}


def _packages():
    """`{꾸러미 이름: [(상대경로, 트리, 모듈상수), …]}` — 파싱 실패는 삼키지 않는다."""
    if "pkgs" in _CACHE:
        return _CACHE["pkgs"]
    out = {}
    for name in sorted(os.listdir(PLUGINS)):
        pkg = os.path.join(PLUGINS, name)
        if not os.path.isdir(pkg):
            continue
        trees = []
        for dirpath, _dirs, files in os.walk(pkg):
            for fn in sorted(files):
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                with open(path, encoding="utf-8") as fp:
                    src = fp.read()
                tree = ast.parse(src, path)          # SyntaxError 는 그대로 터진다
                rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                trees.append((rel, tree, _module_consts(tree)))
        if trees:
            out[name] = trees
    _CACHE["pkgs"] = out
    return out


def _survey():
    """`(꾸러미 → 내는 것, 꾸러미 → 받는 것, 못 푼 자리)`."""
    if "survey" in _CACHE:
        return _CACHE["survey"]
    emits, accepts, unresolved = {}, {}, []
    for name, trees in _packages().items():
        got = []
        for rel, tree, consts in trees:
            found, unres = _emitters(rel, tree, consts)
            got += found
            unresolved += unres
        if got:
            emits[name] = got
            accepts[name] = _accepts(trees)
    _CACHE["survey"] = (emits, accepts, unresolved)
    return emits, accepts, unresolved


# ── ③ 오라클 ────────────────────────────────────────────────────────────────
def test_every_key_the_spec_declares_is_a_do_the_plugin_takes():
    """**죽은 키가 없다** — 표에 실린 이름을 그 화면이 전부 받는다.

    안 받는 이름 = 눌렀는데 아무 일도 안 나는 자리다. 알림도 없다."""
    emits, accepts, _unres = _survey()
    dead = []
    for pkg, rows in sorted(emits.items()):
        for sid, dos, where in rows:
            for do in sorted(dos):
                if not _takes(accepts[pkg], sid, do):
                    dead.append(f"{where} — 화면 {sid!r} 이 키로 내는 {do!r} 를 "
                                f"{pkg} 가 안 받는다")
    assert not dead, "죽은 키:\n  " + "\n  ".join(dead)


def test_the_survey_reads_every_screen_spec_in_the_tree():
    """**못 푼 자리가 없다** — 못 읽은 것을 초록으로 위장하지 않는다.

    ⛔ 이 단언이 없으면 위 오라클은 "못 읽어서 조용한 것"과 "읽었는데 성한 것"을
    구별하지 못한다. 이 저장소가 두 번 밟은 공허 통과 그대로다."""
    _emits, _accepts, unresolved = _survey()
    assert not unresolved, "스펙을 못 읽은 자리:\n  " + "\n  ".join(sorted(unresolved))


def test_the_survey_is_not_vacuous():
    """**세는 자리가 안 사라졌다** — 스펙을 내는 꾸러미가 전부 조사에 잡힌다.

    수를 적어 두면 하루면 낡으므로, 소스에 `plugin_screen` 표식이 있는 꾸러미를
    그때그때 세어 대조한다."""
    emits, _accepts, _unres = _survey()
    declared = set()
    for name, trees in _packages().items():
        for _rel, tree, _consts in trees:
            if any(isinstance(n, ast.Dict) and isinstance(_dget(n, "t"), ast.Constant)
                   and _dget(n, "t").value == SPEC_MARK for n in ast.walk(tree)):
                declared.add(name)
    assert declared, "화면 스펙을 내는 꾸러미가 하나도 없다 — 표식이 바뀌었나"
    assert declared <= set(emits), (
        f"스펙을 내는데 조사에 안 잡힌 꾸러미: {sorted(declared - set(emits))}")
    assert any(dos for rows in emits.values() for _sid, dos, _w in rows), \
        "키 표를 하나도 못 읽었다 — 조사가 공허하다"
